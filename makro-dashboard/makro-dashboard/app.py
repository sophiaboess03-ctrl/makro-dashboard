"""Makro-Dashboard USA – Team-App für die FHGR-Portfoliosimulation.

Start lokal:  streamlit run app.py
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import partial

import pandas as pd
import streamlit as st

from makro import config as C
from makro import logic as L
from makro import ui, views
import markt
from makro.fred import FredFehler, lade_serie
from storage import GoogleTabelleStore, LocalStore, SpeicherFehler

st.set_page_config(page_title="Makro-Dashboard USA", page_icon="🇺🇸", layout="wide")
ui.css()
st.markdown("<style>.block-container {padding-top: 3.4rem;}</style>", unsafe_allow_html=True)


def secret(key, default=None):
    try:
        return st.secrets[key]
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Zugang
# ---------------------------------------------------------------------------
pw = secret("app_password")
if pw and not st.session_state.get("ok"):
    st.title("🇺🇸 Makro-Dashboard USA")
    eingabe = st.text_input("Team-Passwort", type="password")
    if eingabe:
        if eingabe == pw:
            st.session_state["ok"] = True
            st.rerun()
        else:
            st.error("Falsches Passwort.")
    st.stop()


# ---------------------------------------------------------------------------
# Speicher & Daten
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner="Verbinde mit der Google-Tabelle …")
def verbinde_google(url: str, token: str):
    return GoogleTabelleStore(url, token)


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
            return s, lade_serie(s, start=start, api_key=api_key)
        except (FredFehler, Exception):
            return s, None

    with ThreadPoolExecutor(max_workers=8) as ex:
        for s, reihe in ex.map(hole, series):
            if reihe is None:
                fehler.append(s)
            else:
                daten[s] = reihe
    return daten, sorted(fehler), datetime.now().strftime("%d.%m.%Y %H:%M")


TEAM_MODUS = bool(secret("app_password") or secret("script_url"))


def speichern(tabelle: str, zeilen: list[dict], meldung: str = "Gespeichert."):
    """Speichert, liest zur Kontrolle zurück und meldet erst dann Erfolg."""
    if not st.session_state.get("person") or st.session_state.get("person") == "Unbekannt":
        st.error("Bitte zuerst links in der Seitenleiste deinen Namen wählen bzw. eingeben.")
        return
    if TEAM_MODUS and (isinstance(store, LocalStore) or store_fehler):
        st.error("❌ **Nicht gespeichert** – die Google-Tabelle ist nicht verbunden. "
                 f"{store_fehler or ''} Details und Lösung auf der Seite «Verbindung».")
        return
    with st.spinner("Speichere und prüfe …"):
        try:
            store.anhaengen(tabelle, zeilen)
        except Exception as e:  # noqa: BLE001
            st.error(f"❌ **Nicht gespeichert:** {e}")
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
    st.session_state["meldung"] = (f"{meldung} ✔ {ort} gespeichert und bestätigt." if bestaetigt
                                   else f"{meldung} Gespeichert, Kontrolle noch ausstehend – bitte im Protokoll prüfen.")
    st.rerun()


def verbindung_testen() -> tuple[bool, str]:
    if store.name != "Google-Tabelle":
        return False, f"Keine Google-Tabelle verbunden. {store_fehler or 'In den Secrets fehlen script_url / script_token.'}"
    try:
        n = sum(len(df) for df in store.alle().values())
        return True, f"✅ Verbindung ok – {n} Einträge in der Google-Tabelle gefunden."
    except Exception as e:  # noqa: BLE001
        return False, f"❌ {e}"


serien = sorted({i["series"] for i in C.INDIKATOREN if i["quelle"] == "fred"} | {"GDPC1", "GDPPOT", C.REZESSION_SERIE}
                | set(markt.FRED_MARKT))
fred, fred_fehler, fred_zeit = lade_fred(tuple(serien), secret("fred_api_key"))
tabellen, lese_fehler = lese_alle(store.name)
if lese_fehler:
    store_fehler = lese_fehler

daten = L.Daten(fred, tabellen["werte"])
status = L.status(daten)
kombis = L.kombi_signale(status, daten)
szen = L.szenario_wahrscheinlichkeiten(tabellen["szenario_votes"])
risiken = L.risiko_bewertung(tabellen["risiko_votes"], tabellen["risiken_neu"], status, kombis)
neutral = L.einstellungen(tabellen["einstellungen"])
alloc = L.allokation(status, szen, risiken, kombis, neutral)

# ---------------------------------------------------------------------------
# Person
# ---------------------------------------------------------------------------
with st.sidebar:
    team = list(secret("team", []) or [])
    if team:
        person = st.selectbox("Ich bin", team, key="person")
    else:
        person = st.text_input("Dein Name (für Einträge)", key="person") or "Unbekannt"
    if store.name == "Google-Tabelle" and not store_fehler:
        st.success("Speichern aktiv: Google-Tabelle verbunden", icon="✅")
    elif TEAM_MODUS:
        st.error("Speichern blockiert: Google-Tabelle nicht verbunden → Seite «Verbindung»", icon="🔌")
    else:
        st.info("Lokaler Testmodus (Speicher: CSV-Dateien)")
    st.caption(f"FRED: {len(fred)} von {len(serien)} Reihen · {fred_zeit}")

if msg := st.session_state.pop("meldung", None):
    (st.success if "bestätigt" in msg else st.warning)(msg)

ctx = views.Ctx(daten=daten, status=status, kombis=kombis, szen=szen, risiken=risiken, alloc=alloc, tabellen=tabellen,
                person=person, speichern=speichern, rezession=fred.get(C.REZESSION_SERIE), fred_fehler=fred_fehler,
                fred_stand=f"FRED-Daten geladen {fred_zeit}", zyklus_team=L.zyklus_team(tabellen["zyklus_votes"]),
                zyklus_modell=L.zyklus_modell(status))

# ---------------------------------------------------------------------------
# Märkte & News
# ---------------------------------------------------------------------------
yahoo, yahoo_fehler, yahoo_zeit = markt.lade_yahoo(markt.YAHOO)
markt_tab = markt.markt_tabelle(yahoo, fred)
markt.ticker_leiste(markt_tab)
markt_info = f"Kurse geladen {yahoo_zeit} (Yahoo Finance {len(yahoo)}/{len(markt.YAHOO)}, sonst FRED)"


def _seite_news():
    news, news_fehler, news_zeit = markt.lade_news()
    markt.seite_news(markt_tab, news, news_fehler, news_zeit, alloc, views._signale(ctx), speichern, person,
                     tabellen["notizen"], secret("anthropic_api_key"), secret("anthropic_model", "claude-haiku-5-5"))


def _seite_verbindung():
    _, news_fehler, _ = markt.lade_news()
    markt.seite_verbindung(store, store_fehler, TEAM_MODUS, len(fred), len(serien), len(yahoo), len(markt.YAHOO),
                           news_fehler, verbindung_testen)


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------
seiten = {
    "Überblick": [st.Page(partial(views.seite_gesamtbild, ctx), title="Gesamtbild", icon="🇺🇸", url_path="gesamtbild",
                          default=True)],
    "Täglich": [
        st.Page(partial(markt.seite_maerkte, markt_tab, yahoo, fred, markt_info, alloc), title="Märkte heute", icon="🌐",
                url_path="maerkte"),
        st.Page(_seite_news, title="Tagesupdate & News", icon="📰", url_path="news"),
    ],
    "Kapitel": [st.Page(partial(views.seite_kapitel, ctx, k["id"]), title=k["name"], icon=k["icon"], url_path=k["id"])
                for k in C.KAPITEL],
    "Auswertung": [
        st.Page(partial(views.seite_szenarien_risiken, ctx), title="Szenarien & Risiken", icon="🎲", url_path="szenarien"),
        st.Page(partial(views.seite_allokation, ctx), title="Allokation", icon="🧭", url_path="allokation"),
        st.Page(partial(views.seite_termine, ctx), title="Termine", icon="📅", url_path="termine"),
    ],
    "Team": [
        st.Page(partial(views.seite_bewerten, ctx), title="Bewerten", icon="🗳️", url_path="bewerten"),
        st.Page(partial(views.seite_eintragen, ctx), title="Daten eintragen", icon="✏️", url_path="eintragen"),
        st.Page(partial(views.seite_protokoll, ctx), title="Protokoll", icon="🧾", url_path="protokoll"),
        st.Page(partial(views.seite_einstellungen, ctx), title="Einstellungen", icon="⚙️", url_path="einstellungen"),
        st.Page(_seite_verbindung, title="Verbindung", icon="🔌", url_path="verbindung"),
    ],
}
st.navigation(seiten).run()
