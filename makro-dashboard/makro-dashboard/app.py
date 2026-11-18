"""Makro-Dashboard USA – Team-App für die FHGR-Portfoliosimulation.

Start lokal:  streamlit run app.py
"""
from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse
import html
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FutTimeout
from datetime import datetime
from functools import partial

import pandas as pd
import streamlit as st

from makro import config as C
from makro import logic as L
from makro import ui, views
import markt
import pipeline
import portfolio
import entscheide
import erwartungen
import bericht
import kalender
import export
import ic
import analyse
import anleihen
import dateien
import dozent
import ips as ips_mod
import szenarien_seite
import taktik
from makro import szenarien as SZ
from makro.fred import FredFehler, lade_serie
from storage import GoogleTabelleStore, LocalStore, SpeicherFehler

st.set_page_config(page_title="Makro-Dashboard USA", page_icon=":material/insights:", layout="wide")
ui.css()


def secret(key, default=None):
    try:
        return st.secrets[key]
    except Exception:
        return default


def _tabelle(key) -> dict[str, str]:
    try:
        return {str(k).strip(): str(v) for k, v in dict(secret(key, {}) or {}).items() if str(v).strip()}
    except Exception:  # noqa: BLE001
        return {}


def _gleich(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# ---------------------------------------------------------------------------
# Zugang: persönliche Logins ([benutzer] in den Secrets) oder ein Team-Passwort
# ---------------------------------------------------------------------------
BENUTZER = _tabelle("benutzer")      # Name = Passwort, darf speichern
LESER = _tabelle("lesezugriff")      # Name = Passwort, darf nur ansehen (z. B. Dozent)
pw = secret("app_password")
PERSOENLICH = bool(BENUTZER or LESER)
COOKIE, COOKIE_TAGE = "makro_login", 7


def _schluessel() -> bytes:
    """Signaturschlüssel: ändert sich, sobald ein Passwort oder das Skript-Token geändert wird."""
    roh = "|".join([str(secret("script_token", "")), str(pw or "")] + [f"{k}={v}" for k, v in sorted({**BENUTZER, **LESER}.items())])
    return hashlib.sha256(roh.encode("utf-8")).digest()


def _token(name: str) -> str:
    bis = int(time.time()) + COOKIE_TAGE * 86400
    sig = hmac.new(_schluessel(), f"{name}|{bis}".encode("utf-8"), hashlib.sha256).hexdigest()[:40]
    return urllib.parse.quote(f"{name}|{bis}|{sig}", safe="")


def _aus_cookie() -> str | None:
    try:
        roh = st.context.cookies.get(COOKIE)
    except Exception:  # noqa: BLE001
        return None
    if not roh:
        return None
    try:
        name, bis, sig = urllib.parse.unquote(roh).rsplit("|", 2)
        soll = hmac.new(_schluessel(), f"{name}|{bis}".encode("utf-8"), hashlib.sha256).hexdigest()[:40]
        if _gleich(sig, soll) and int(bis) > time.time() and name in {**BENUTZER, **LESER}:
            return name
    except Exception:  # noqa: BLE001
        pass
    return None


def _cookie_js(wert: str | None):
    """Setzt bzw. löscht das Login-Cookie im Browser (kleines unsichtbares Skript)."""
    attr = f"max-age={COOKIE_TAGE * 86400}" if wert else "max-age=0"
    ui.iframe_html(f"<script>try{{window.parent.document.cookie='{COOKIE}={wert or ''}; path=/; {attr}; SameSite=Lax; Secure';}}"
                    f"catch(e){{document.cookie='{COOKIE}={wert or ''}; path=/; {attr}; SameSite=Lax; Secure';}}</script>", 0)


def anmeldeseite():
    st.markdown("<style>section[data-testid='stSidebar'], [data-testid='stSidebarCollapsedControl'] {display:none;}</style>",
                unsafe_allow_html=True)
    if st.session_state.pop("cookie_loeschen", False):
        _cookie_js(None)
    _, mitte, _ = st.columns([1, 1.05, 1])
    with mitte:
        st.markdown('<div class="login"><div class="login-logo">M</div><div class="kopf-over">' + ui.KOPF_ZEILE + '</div>'
                    '<div class="kopf-titel">Makro-Dashboard USA</div>'
                    '<div class="kopf-sub">Bitte mit deinem persönlichen Zugang anmelden.</div></div>' if PERSOENLICH else
                    '<div class="login"><div class="login-logo">M</div><div class="kopf-over">' + ui.KOPF_ZEILE + '</div>'
                    '<div class="kopf-titel">Makro-Dashboard USA</div>'
                    '<div class="kopf-sub">Bitte mit dem Team-Passwort anmelden.</div></div>', unsafe_allow_html=True)
        with st.form("anmeldung", border=True):
            name = (st.selectbox("Name", list(BENUTZER) + [n for n in LESER if n not in BENUTZER], index=None,
                                 placeholder="Name wählen") if PERSOENLICH else None)
            eingabe = st.text_input("Passwort" if PERSOENLICH else "Team-Passwort", type="password")
            merken = st.checkbox(f"Angemeldet bleiben ({COOKIE_TAGE} Tage, nur auf eigenen Geräten)", value=True) if PERSOENLICH else False
            los = st.form_submit_button("Anmelden", type="primary", width="stretch")
        if los:
            if PERSOENLICH:
                soll = BENUTZER.get(name) if name in BENUTZER else LESER.get(name)
                if name and soll and _gleich(eingabe, soll):
                    st.session_state.update(ok=True, benutzer=name, nur_lesen=name not in BENUTZER, person=name,
                                            abgemeldet=False)
                    if merken:
                        st.session_state["cookie_setzen"] = _token(name)
                    st.rerun()
                st.error("Name oder Passwort stimmt nicht." if name else "Bitte zuerst deinen Namen wählen.")
            elif pw and _gleich(eingabe, str(pw)):
                st.session_state["ok"] = True
                st.rerun()
            else:
                st.error("Falsches Passwort.")
        st.markdown("<div class='klein' style='text-align:center; margin-top:10px'>Alle Eingaben werden automatisch "
                    "der angemeldeten Person zugeschrieben.</div>" if PERSOENLICH else "", unsafe_allow_html=True)


if PERSOENLICH:
    if not st.session_state.get("benutzer") and not st.session_state.get("abgemeldet"):
        if wer := _aus_cookie():
            st.session_state.update(ok=True, benutzer=wer, nur_lesen=wer not in BENUTZER, person=wer)
    if not st.session_state.get("benutzer") or st.session_state["benutzer"] not in {**BENUTZER, **LESER}:
        anmeldeseite()
        st.stop()
    st.session_state["person"] = st.session_state["benutzer"]
    if t := st.session_state.pop("cookie_setzen", None):
        _cookie_js(t)
elif pw and not st.session_state.get("ok"):
    anmeldeseite()
    st.stop()
NUR_LESEN = bool(st.session_state.get("nur_lesen"))


# ---------------------------------------------------------------------------
# Speicher & Daten
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner="Verbinde mit der Google-Tabelle …")
def verbinde_google(url: str, token: str):
    return GoogleTabelleStore(url, token, timeout=25)


def get_store():
    url, token = secret("script_url"), secret("script_token")
    if not url or "HIER" in str(url):
        return LocalStore("daten"), ("Noch keine Google-Tabelle verbunden: in den Secrets fehlt die script_url."
                                     if secret("app_password") else None)
    try:
        return verbinde_google(str(url), str(token or "")), None
    except SpeicherFehler as e:
        return LocalStore("daten"), f"Google-Tabelle nicht verbunden: {e}"
    except Exception as e:  # noqa: BLE001
        return LocalStore("daten"), f"Google-Tabelle nicht verbunden ({type(e).__name__})."


store, store_fehler = get_store()
st.session_state["store_name"] = store.name


@st.cache_data(ttl=60, show_spinner=False)
def lese_alle(store_name: str) -> dict:
    try:
        return store.alle(), None
    except Exception as e:  # noqa: BLE001
        return LocalStore("daten").alle(), f"Lesen aus der Google-Tabelle fehlgeschlagen: {e}"


@st.cache_data(ttl=6 * 3600, show_spinner="Lade aktuelle Daten von FRED …")
def lade_fred(series: tuple[str, ...], api_key: str | None):
    start = (pd.Timestamp.today() - pd.DateOffset(years=12)).strftime("%Y-%m-%d")
    daten, fehler = {}, []

    def hole(s):
        try:
            return s, lade_serie(s, start=start, api_key=api_key, timeout=12)
        except (FredFehler, Exception):
            return s, None

    ex = ThreadPoolExecutor(max_workers=10)
    futs = [ex.submit(hole, s) for s in series]
    try:
        for f in as_completed(futs, timeout=60):  # harte Obergrenze für den ersten Ladevorgang
            s, reihe = f.result()
            if reihe is None:
                fehler.append(s)
            else:
                daten[s] = reihe
    except FutTimeout:
        pass
    finally:
        ex.shutdown(wait=False)
    fehler = sorted(set(series) - set(daten))
    return daten, sorted(fehler), datetime.now().strftime("%d.%m.%Y %H:%M")


TEAM_MODUS = bool(secret("app_password") or secret("script_url"))


def speichern(tabelle: str, zeilen: list[dict], meldung: str = "Gespeichert."):
    """Speichert, liest zur Kontrolle zurück und meldet erst dann Erfolg."""
    if NUR_LESEN:
        st.error("Nur Lesezugriff – mit diesem Zugang können keine Änderungen gespeichert werden.")
        return
    if not st.session_state.get("person") or st.session_state.get("person") == "Unbekannt":
        st.error("Bitte zuerst links in der Seitenleiste deinen Namen wählen bzw. eingeben.")
        return
    if TEAM_MODUS and (isinstance(store, LocalStore) or store_fehler):
        st.error("**Nicht gespeichert** – die Google-Tabelle ist nicht verbunden. "
                 f"{store_fehler or ''} Details und Lösung auf der Seite «Verbindung».")
        return
    with st.spinner("Speichere und prüfe …"):
        try:
            store.anhaengen(tabelle, zeilen)
        except Exception as e:  # noqa: BLE001
            st.error(f"**Nicht gespeichert:** {e}")
            return
        bestaetigt = False
        try:
            df = store.alle()[tabelle]
            z0, p0 = str(zeilen[0].get("zeit")), str(zeilen[0].get("person"))
            bestaetigt = int(((df["zeit"] == z0) & (df["person"] == p0)).sum()) >= len(zeilen)
        except Exception:  # noqa: BLE001
            pass
    lese_alle.clear()
    ort = "in der Google-Tabelle" if store.name == "Google-Tabelle" else "lokal"
    st.session_state["meldung"] = (f"{meldung} {ort[0].upper() + ort[1:]} gespeichert und bestätigt." if bestaetigt
                                   else f"{meldung} Gespeichert, Kontrolle noch ausstehend – bitte im Protokoll prüfen.")
    st.rerun()


def verbindung_testen() -> tuple[bool, str]:
    if store.name != "Google-Tabelle":
        return False, f"Keine Google-Tabelle verbunden. {store_fehler or 'In den Secrets fehlen script_url / script_token.'}"
    try:
        n = sum(len(df) for df in store.alle().values())
        return True, f"Verbindung ok – {n} Einträge in der Google-Tabelle gefunden."
    except Exception as e:  # noqa: BLE001
        return False, f"Fehler: {e}"


serien = pipeline.fred_serien(markt.FRED_MARKT)
fred, fred_fehler, fred_zeit = lade_fred(tuple(serien), secret("fred_api_key"))
tabellen, lese_fehler = lese_alle(store.name)
if lese_fehler:
    store_fehler = lese_fehler

yahoo, yahoo_fehler, yahoo_zeit = markt.lade_yahoo(markt.YAHOO)
fred_roh = fred
fred, labels = pipeline.mische_markt(fred_roh, yahoo)
hist, hist_fehler, _ = analyse.lade_historie(pipeline.HIST_TICKER, 6)
_r = pipeline.berechne(fred, labels, tabellen, hist)
daten, status, kombis, szen, risiken, alloc = _r["daten"], _r["status"], _r["kombis"], _r["szen"], _r["risiken"], _r["alloc"]
try:
    _fp = erwartungen.fed_pfad(fred)
    _fed_markt = _fp["punkte"][0]["diff"] if _fp["punkte"] else None
except Exception:  # noqa: BLE001
    _fed_markt = None
anker = SZ.daten_anker(fred, hist, status, _fed_markt)

# ---------------------------------------------------------------------------
# Seitenleiste: angemeldete Person, Status, Sicherung
# ---------------------------------------------------------------------------
TEAM = list(BENUTZER) if BENUTZER else list(secret("team", []) or [])
ui.logo()
with st.sidebar:
    if PERSOENLICH:
        person = st.session_state["benutzer"]
        rolle = "Lesezugriff" if NUR_LESEN else "Team"
        st.markdown(f'<div class="nutzer"><div class="nutzer-label">Angemeldet · {rolle}</div>'
                    f'<div class="nutzer-name">{html.escape(person)}</div></div>', unsafe_allow_html=True)
        if st.button("Abmelden", icon=":material/logout:", width="stretch"):
            for k in ("ok", "benutzer", "nur_lesen", "person", "excel_export"):
                st.session_state.pop(k, None)
            st.session_state.update(abgemeldet=True, cookie_loeschen=True)
            st.rerun()
    elif TEAM:
        person = st.selectbox("Ich bin", TEAM, key="person")
    else:
        person = st.text_input("Dein Name (für Einträge)", key="person") or "Unbekannt"
    if NUR_LESEN:
        ui.status_zeile("Nur ansehen – Speichern gesperrt", ui.FARBE[L.KONTEXT])
    elif store.name == "Google-Tabelle" and not store_fehler:
        ui.status_zeile("Speichern aktiv · Google-Tabelle verbunden", ui.FARBE[L.GRUEN])
    elif TEAM_MODUS:
        ui.status_zeile("Speichern blockiert · siehe «Verbindung»", ui.FARBE[L.ROT])
    else:
        ui.status_zeile("Lokaler Testmodus (CSV-Dateien)", ui.FARBE[L.GELB])
    ui.status_zeile(f"FRED {len(fred)}/{len(serien)} Reihen · {fred_zeit}", ui.STAHL)
    if not NUR_LESEN:
        if st.button("Alles als Excel sichern", icon=":material/backup:", width="stretch"):
            try:
                st.session_state["excel_export"] = export.excel(tabellen, status, alloc, szen, person)
            except Exception as e:  # noqa: BLE001
                st.error(f"Export fehlgeschlagen: {e}")
        if st.session_state.get("excel_export"):
            st.download_button("Excel herunterladen", st.session_state["excel_export"],
                               f"Makro-Dashboard_Sicherung_{datetime.now():%Y-%m-%d_%H%M}.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch",
                               icon=":material/download:")

if msg := st.session_state.pop("meldung", None):
    if "bestätigt" in msg:
        st.success(msg, icon=":material/check_circle:")
    else:
        st.warning(msg, icon=":material/info:")

ctx = views.Ctx(daten=daten, status=status, kombis=kombis, szen=szen, risiken=risiken, alloc=alloc, tabellen=tabellen,
                person=person, speichern=speichern, rezession=fred.get(C.REZESSION_SERIE), fred_fehler=fred_fehler,
                fred_stand=f"FRED-Daten geladen {fred_zeit}", zyklus_team=L.zyklus_team(tabellen["zyklus_votes"]),
                zyklus_modell=L.zyklus_modell(status), szdef=_r["szdef"], gew=_r["gew"], lage=_r["lage"], ips=_r["ips"],
                einst=_r["einst"], bond=_r["bond"], sig=_r["sig"], anker=anker, fred=fred, hist=hist,
                erw_antworten={m["id"]: dozent.antworten_kurz(tabellen.get("erwartungen"), m["id"]) for m in C.MONATE},
                store=store, nur_lesen=NUR_LESEN)
MONAT_ARBEIT = _r["monat_arbeit"]


def _prog(monat_id: str | None = None):
    mid = monat_id or MONAT_ARBEIT["id"]
    try:
        p = dozent.prognose(ctx, mid)
    except Exception as e:  # noqa: BLE001
        st.warning(f"Prognose für die Dozentenfragen nicht berechenbar: {e}")
        return None
    if p.get("sektoren_df") is not None:
        ctx.erw_sim_sektoren[mid] = p["sektoren_df"]
    return p


def _seite_gesamtbild():
    views.seite_gesamtbild(ctx, _prog(), MONAT_ARBEIT)


def _seite_taktik():
    for m in C.MONATE:
        if m["id"] not in ctx.erw_sim_sektoren:
            _prog(m["id"])
    taktik.seite_taktik(ctx, ctx.bond)


def _seite_eingepreist():
    erwartungen.seite_erwartungen(ctx, fred, _prog())

# ---------------------------------------------------------------------------
# Märkte & News
# ---------------------------------------------------------------------------
markt_tab = markt.markt_tabelle(yahoo, fred_roh)
markt.ticker_leiste(markt_tab)
markt_info = f"Kurse geladen {yahoo_zeit} (Yahoo Finance {len(yahoo)}/{len(markt.YAHOO)}, sonst FRED)"


def _seite_news():
    news, news_fehler, news_zeit = markt.lade_news()
    mails, nl_fehler = markt.lade_newsletter(store, store.name if not store_fehler else "-")
    markt.seite_news(markt_tab, news, news_fehler, news_zeit, alloc, views._signale(ctx), speichern, person,
                     tabellen["notizen"], secret("anthropic_api_key"), secret("anthropic_model", "claude-haiku-5-5"),
                     mails, nl_fehler)


views.TERMINE_DF, views.TERMINE_INFO = kalender.termine_df(secret("fred_api_key"))


def _seite_portfolio():
    portfolio.seite_portfolio(ctx, fred, markt.lade_yahoo, markt_tab)


def _seite_bericht():
    info = portfolio.zusammenfassung(tabellen, fred, markt.lade_yahoo, alloc)
    bericht.seite_bericht(ctx, markt_tab, views._signale(ctx), views.TERMINE_DF, info, team=TEAM, fred=fred,
                          lade_kurse=markt.lade_yahoo)


def _seite_verbindung():
    _, news_fehler, _ = markt.lade_news()
    mails, nl_fehler = markt.lade_newsletter(store, store.name if not store_fehler else "-")
    markt.seite_verbindung(store, store_fehler, TEAM_MODUS, len(fred), len(serien), len(yahoo), len(markt.YAHOO),
                           news_fehler, verbindung_testen, len(mails), nl_fehler)


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------
M = ":material/{}:".format
seiten = {
    "Überblick": [st.Page(_seite_gesamtbild, title="Gesamtbild", icon=M("dashboard"), url_path="gesamtbild", default=True),
                  st.Page(lambda: ic.seite_ic(ctx, markt_tab, TEAM), title="Investment Committee", icon=M("gavel"), url_path="ic"),
                  st.Page(_seite_portfolio, title="Portfolio", icon=M("account_balance_wallet"), url_path="portfolio"),
                  st.Page(lambda: analyse.seite_analyse(ctx, fred, markt.lade_yahoo), title="Analyse",
                          icon=M("analytics"), url_path="analyse")],
    "Täglich": [
        st.Page(partial(markt.seite_maerkte, markt_tab, yahoo, fred, markt_info, alloc), title="Märkte heute",
                icon=M("candlestick_chart"), url_path="maerkte"),
        st.Page(_seite_news, title="Tagesupdate & News", icon=M("newspaper"), url_path="news"),
    ],
    "Kapitel": [st.Page(partial(views.seite_kapitel, ctx, k["id"]), title=k["name"], icon=k["icon"], url_path=k["id"])
                for k in C.KAPITEL],
    "Szenarien": [
        st.Page(lambda: szenarien_seite.seite_szenarien(ctx), title="Szenarien", icon=M("alt_route"), url_path="szenarien"),
        st.Page(_seite_eingepreist, title="Was ist eingepreist?", icon=M("query_stats"), url_path="erwartungen"),
        st.Page(lambda: szenarien_seite.seite_risiken(ctx), title="Risiken & Chancen", icon=M("warning"), url_path="risiken"),
    ],
    "Entscheiden & Abgabe": [
        st.Page(lambda: ips_mod.seite_ips(ctx, hist, ctx.gew, ctx.szdef, ctx.bond), title="IPS", icon=M("policy"), url_path="ips"),
        st.Page(lambda: dozent.seite_erwartungen(ctx), title="Erwartungen (Dozent)", icon=M("quiz"), url_path="dozent"),
        st.Page(_seite_taktik, title="Taktik", icon=M("donut_large"), url_path="taktik"),
        st.Page(lambda: anleihen.seite_anleihen(ctx, fred), title="Anleihen & Duration", icon=M("stacked_line_chart"),
                url_path="anleihen"),
        st.Page(lambda: entscheide.seite_entscheide(ctx, TEAM), title="Entscheide", icon=M("fact_check"), url_path="entscheide"),
        st.Page(_seite_bericht, title="Berichte", icon=M("description"), url_path="bericht"),
    ],
    "Team": [
        st.Page(partial(views.seite_eintragen, ctx), title="Daten eintragen", icon=M("edit_note"), url_path="eintragen"),
        st.Page(lambda: dateien.seite_dokumente(ctx), title="Dokumente", icon=M("folder_open"), url_path="dokumente"),
        st.Page(partial(views.seite_termine, ctx), title="Termine", icon=M("event"), url_path="termine"),
        st.Page(partial(views.seite_protokoll, ctx), title="Protokoll", icon=M("history"), url_path="protokoll"),
        st.Page(partial(views.seite_einstellungen, ctx), title="Einstellungen", icon=M("tune"), url_path="einstellungen"),
        st.Page(_seite_verbindung, title="Verbindung", icon=M("cable"), url_path="verbindung"),
    ],
}
if NUR_LESEN:  # Lesezugriff: keine Eingabeseiten
    seiten["Team"] = [p for p in seiten["Team"] if p.url_path in ("dokumente", "protokoll", "termine")]
st.navigation(seiten, expanded=True).run()
