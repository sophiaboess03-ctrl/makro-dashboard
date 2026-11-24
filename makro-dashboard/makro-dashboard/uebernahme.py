"""Vorauswahl aus der Analyse als Transaktionen ins Portfolio übernehmen (Kauf per 01.12.2026).

Regeln (mit Sophy festgelegt, 23.11.2026)
- Aktien zum Eröffnungskurs des Handelstags (Yahoo «Open»), ganze Stücke (abgerundet), der Rest bleibt Cash.
- Treasuries: konkrete Anleihen aus der Analyse (Kupon, Fälligkeit, optional Kurs). Ohne Kurs: Preis aus der FRED-Zinskurve
  des Handelstags. Nominal auf 100 USD abgerundet. Zeilen nur mit Laufzeitband sind nicht übernehmbar.
- Begründung je Zeile (oder gemeinsam für alle) – Pflicht, nicht die Kauf-These.
- Erst Vorschau mit Prüfung (Cash, IPS-Bänder, Titel-Maximum, Sektorziele), dann «Bestätigen» → alles auf einmal gebucht.
  «Rückgängig» storniert die ganze Übernahme (Storno-Zeilen wie auf der Seite Portfolio, nichts wird gelöscht).
"""
from __future__ import annotations

import json
import math
import uuid

import pandas as pd
import streamlit as st

from makro import config as C
from makro import logic as L
from makro import ui
from storage import jetzt, zahl

KAUFTAG = pd.Timestamp(C.SIMULATION_START)


@st.cache_data(ttl=10 * 60, show_spinner="Lade Eröffnungskurse von Yahoo …")
def eroeffnungskurse(ticker: tuple[str, ...], datum: str) -> tuple[dict, list[str]]:
    """{Ticker: Eröffnungskurs am Datum}. Fehlt der Tag (noch nicht gehandelt, Feiertag) → Ticker in der Fehlliste."""
    if not ticker:
        return {}, []
    try:
        import yfinance as yf

        d0 = pd.Timestamp(datum)
        df = yf.download(list(ticker), start=d0.strftime("%Y-%m-%d"), end=(d0 + pd.Timedelta(days=4)).strftime("%Y-%m-%d"),
                         interval="1d", group_by="ticker", auto_adjust=False, progress=False, threads=False, timeout=20)
    except Exception:  # noqa: BLE001
        return {}, list(ticker)
    out, fehlt = {}, []
    for t in ticker:
        try:
            sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
            s = sub["Open"].dropna()
            s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
            if d0.normalize() in s.index:
                out[t] = float(s.loc[d0.normalize()])
            else:
                fehlt.append(t)
        except Exception:  # noqa: BLE001
            fehlt.append(t)
    return out, fehlt


def _txt(x) -> str:
    try:
        if x is None or pd.isna(x):
            return ""
    except (TypeError, ValueError):
        pass
    return str(x).strip()


def _datum(x) -> pd.Timestamp | None:
    try:
        t = pd.Timestamp(x)
        return None if pd.isna(t) else t.normalize()
    except Exception:  # noqa: BLE001
        return None


def treasury_name(kupon: float, faellig: pd.Timestamp) -> str:
    return f"UST {ui.de(kupon, 3)} % {faellig:%d.%m.%Y}"


def vorschlag(eingabe: dict, datum: pd.Timestamp, fred: dict, info_fn) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Aus der gespeicherten Vorauswahl die Buchungsvorschläge (Aktien, Treasuries) mit Kursen und Mengen."""
    import portfolio as P

    hinweise = []
    akt = [a for a in eingabe.get("aktien", []) if _txt(a.get("Ticker"))]
    ticker = tuple(sorted({_txt(a["Ticker"]).upper().replace(".", "-") for a in akt}))
    kurse, fehlt = eroeffnungskurse(ticker, datum.strftime("%Y-%m-%d"))
    if fehlt:
        hinweise.append(f"Kein Eröffnungskurs vom {datum:%d.%m.%Y} für: {', '.join(fehlt)}. Die US-Börse öffnet um 15:30 Schweizer "
                        "Zeit – danach neu berechnen oder den Kurs unten von Hand eintragen.")
    za = []
    for a in akt:
        t = _txt(a["Ticker"]).upper().replace(".", "-")
        i = info_fn(t) or {}
        k = kurse.get(t)
        menge, betrag = zahl(a.get("Menge")), zahl(a.get("Betrag USD"))
        if menge:
            stueck = math.floor(menge + 1e-9)
        elif betrag and k:
            stueck = math.floor(betrag / k)
        else:
            stueck = None
        za.append({"Ticker": t, "Name": i.get("name", ""), "Sektor": i.get("sektor") or "", "Stück": stueck,
                   "Kurs USD": k, "Betrag aus Vorauswahl": ui.de(betrag, 0) if betrag else "", "Begründung": ""})
    zt = []
    for r in eingabe.get("treas", []):
        betrag, kupon, faellig = zahl(r.get("Betrag USD")), zahl(r.get("Kupon %")), _datum(r.get("Fälligkeit"))
        if not betrag:
            continue
        if kupon is None or faellig is None:
            hinweise.append(f"Treasury {r.get('Laufzeit') or ''} ({ui.de(betrag, 0)} USD) hat nur ein Laufzeitband – in der Analyse "
                            "Kupon und Fälligkeit ergänzen, sonst wird sie nicht übernommen.")
            continue
        kurs = zahl(r.get("Kurs %"))
        quelle = "aus der Analyse"
        if kurs is None:
            T = max(0.0, (faellig - datum).days / 365.25)
            y = P.kurve_rendite(P.kurve_am(fred, datum), T)
            kurs = round(P.preis(kupon, T, y), 4) if y is not None else None
            quelle = "Zinskurve FRED" if kurs is not None else "fehlt"
        nominal = math.floor(betrag / (kurs / 100) / 100) * 100 if kurs else None
        zt.append({"Bezeichnung": _txt(r.get("Bezeichnung")) or treasury_name(kupon, faellig), "Kupon %": kupon,
                   "Fälligkeit": faellig.date(), "Nominal USD": ui.de(nominal, 0) if nominal else "", "Kurs %": kurs,
                   "Kurs-Quelle": quelle, "Betrag aus Vorauswahl": ui.de(betrag, 0), "Begründung": ""})
    return pd.DataFrame(za), pd.DataFrame(zt), hinweise


def buchungen(aktien: pd.DataFrame, treas: pd.DataFrame, datum: pd.Timestamp, begr_alle: str, batch: str, person: str) -> list[dict]:
    z = jetzt()
    out, n = [], 0
    for _, r in aktien.iterrows():
        if not zahl(r["Stück"]) or not zahl(r["Kurs USD"]):
            continue
        n += 1
        out.append(dict(zeit=z, person=person, id=f"ue{batch}{n:02d}", datum=datum.date().isoformat(), art="Kauf", klasse="Aktie",
                        ticker=r["Ticker"], name=_txt(r["Name"]), sektor=_txt(r["Sektor"]), menge=float(zahl(r["Stück"])),
                        kurs=round(float(zahl(r["Kurs USD"])), 4), kupon="", faelligkeit="",
                        notiz=_txt(r["Begründung"]) or begr_alle.strip(), storniert=""))
    for _, r in treas.iterrows():
        if not zahl(r["Nominal USD"]) or not zahl(r["Kurs %"]):
            continue
        n += 1
        out.append(dict(zeit=z, person=person, id=f"ue{batch}{n:02d}", datum=datum.date().isoformat(), art="Kauf", klasse="Treasury",
                        ticker=_txt(r["Bezeichnung"]), name="", sektor="", menge=float(zahl(r["Nominal USD"])),
                        kurs=round(float(zahl(r["Kurs %"])), 4), kupon=float(zahl(r["Kupon %"]) or 0),
                        faelligkeit=pd.Timestamp(r["Fälligkeit"]).date().isoformat(), notiz=_txt(r["Begründung"]) or begr_alle.strip(),
                        storniert=""))
    return out


def pruefen(ctx, aktien: pd.DataFrame, treas: pd.DataFrame, begr_alle: str, kapital: float, cash_frei: float,
            bestand_wert: float) -> tuple[list[tuple[str, str]], bool]:
    """(Meldungen [(Stufe, Text)], harte Fehler?)"""
    import sektoren as SK

    m, hart = [], False
    for _, r in aktien.iterrows():
        if not zahl(r["Kurs USD"]) or not zahl(r["Stück"]):
            m.append((L.ROT, f"{r['Ticker']}: Kurs oder Stückzahl fehlt – wird nicht gebucht."))
            hart = True
        if len(_txt(r["Begründung"]) or begr_alle.strip()) < 10:
            m.append((L.ROT, f"{r['Ticker']}: Begründung fehlt (eigene oder gemeinsame, mindestens ein Satz)."))
            hart = True
        if _txt(r["Sektor"]) not in C.SEKTOREN:
            m.append((L.ROT, f"{r['Ticker']}: Sektor unbekannt – in der Tabelle wählen."))
            hart = True
    for _, r in treas.iterrows():
        if not zahl(r["Kurs %"]) or not zahl(r["Nominal USD"]):
            m.append((L.ROT, f"{r['Bezeichnung']}: Kurs oder Nominal fehlt."))
            hart = True
        if len(_txt(r["Begründung"]) or begr_alle.strip()) < 10:
            m.append((L.ROT, f"{r['Bezeichnung']}: Begründung fehlt."))
            hart = True
    w_a = float(sum((zahl(r["Stück"]) or 0) * (zahl(r["Kurs USD"]) or 0) for _, r in aktien.iterrows()))
    w_t = float(sum((zahl(r["Nominal USD"]) or 0) * (zahl(r["Kurs %"]) or 0) / 100 for _, r in treas.iterrows()))
    rest = cash_frei - w_a - w_t
    total = bestand_wert + cash_frei
    m.append((L.ROT if rest < -1 else L.GRUEN, f"Investiert {ui.de(w_a + w_t, 0)} USD · Cash danach {ui.de(rest, 0)} USD"
              + (" – mehr als verfügbar!" if rest < -1 else "")))
    if total > 0:
        alloc = ctx.alloc
        if not bestand_wert:
            for name, wert, (lo, hi), soll in (("Aktien", w_a / total * 100, alloc["baender"]["aktien"], alloc["aktien"]),
                                               ("Treasuries", w_t / total * 100, alloc["baender"]["treasuries"], alloc["anleihen"]),
                                               ("Cash", rest / total * 100, alloc["baender"]["cash"], alloc["cash"])):
                m.append((L.GRUEN if lo <= wert <= hi else L.ROT,
                          f"{name} {ui.de(wert, 1)} % – IPS-Band {lo:g}–{hi:g} %, Taktik {ui.de(soll, 1)} %"))
        else:
            m.append((L.GELB, "Im Portfolio sind schon Transaktionen erfasst – die Übernahme kommt dazu. Bänder danach auf der Seite "
                              "«Portfolio» prüfen."))
        tmax = ctx.ips.get("titel_max")
        for _, r in aktien.iterrows():
            g = (zahl(r["Stück"]) or 0) * (zahl(r["Kurs USD"]) or 0) / total * 100
            if tmax and g > tmax + 1e-9:
                m.append((L.ROT, f"{r['Ticker']}: {ui.de(g, 2)} % – über dem Titel-Maximum laut IPS ({ui.de(tmax, 1)} %)."))
        ak_df = pd.DataFrame({"ticker": aktien["Ticker"], "name": aktien["Name"], "sektor": aktien["Sektor"],
                              "wert": [(zahl(a) or 0) * (zahl(b) or 0) for a, b in zip(aktien["Stück"], aktien["Kurs USD"])]}) \
            if len(aktien) else pd.DataFrame(columns=["ticker", "name", "sektor", "wert"])
        try:
            sa = SK.lade_allokation(ctx.tabellen.get("sektor_allokation"))["sektoren"]
            sp = SK.pruefe_portfolio(ak_df, total, sa, ctx.sek_index["gewichte"], float(ctx.alloc["aktien"]))
            n_aus = sp["n"][L.ROT] + sp["n"][L.GELB]
            m.append((L.ROT if sp["n"][L.ROT] else L.GELB if sp["n"][L.GELB] else L.GRUEN,
                      f"Sektorallokation: {sp['n'][L.GRUEN]} von 11 Sektoren im Ziel" + (f", {n_aus} ausserhalb" if n_aus else "")))
        except Exception as e:  # noqa: BLE001
            m.append((L.GELB, f"Sektorprüfung nicht möglich ({type(e).__name__})."))
    return m, hart


def uebernahmen(tabellen: dict) -> list[dict]:
    e = tabellen.get("einstellungen")
    tx = tabellen.get("transaktionen")
    if e is None or e.empty:
        return []
    out = []
    aktiv_ids = set()
    if tx is not None and not tx.empty:
        d = tx.sort_values("zeit").drop_duplicates("id", keep="last")
        aktiv_ids = set(d.loc[~d["storniert"].astype(str).str.strip().isin(["1", "1.0", "True"]), "id"].astype(str))
    for _, r in e[e["schluessel"].astype(str).str.startswith("uebernahme|")].sort_values("zeit", ascending=False).iterrows():
        try:
            meta = json.loads(r["wert"])
        except Exception:  # noqa: BLE001
            continue
        b = str(r["schluessel"]).split("|", 1)[1]
        n_aktiv = sum(1 for i in aktiv_ids if i.startswith(f"ue{b}"))
        out.append(dict(batch=b, **meta, person=r["person"], zeit=str(r["zeit"]), aktiv=n_aktiv))
    return out


def bereich(ctx, fred: dict, gespeichert: pd.DataFrame):
    """Expander auf der Seite «Analyse»."""
    import portfolio as P

    ss = st.session_state
    st.caption("Gekauft wird zum **Eröffnungskurs** des Handelstags (Yahoo), in **ganzen Stücken** (abgerundet, Rest bleibt Cash). "
               "Treasuries mit Kupon und Fälligkeit aus der Analyse; ohne Kurs rechnet die App den Preis aus der Zinskurve. "
               "Erst Vorschau und Prüfung, dann «Bestätigen» – «Rückgängig» storniert die ganze Übernahme.")
    if not len(gespeichert):
        st.info("Zuerst oben ein Analyseportfolio speichern – übernommen wird ein gespeichertes Analyseportfolio.")
        return
    namen = {r["id"]: f"{r['name']} ({r['person']}, {str(r['zeit'])[:10]})" for _, r in gespeichert.iterrows()}
    if ss.get("ue_aid") not in namen and ss.get("an_aktiv") in namen:
        ss["ue_aid"] = ss["an_aktiv"]          # Standard: das gerade aktive Analyseportfolio
    c1, c2, c3 = st.columns([2.4, 1, 1])
    aid = c1.selectbox("Analyseportfolio (gespeicherter Stand)", list(gespeichert["id"]), format_func=namen.get, key="ue_aid",
                       help="Übernommen wird die zuletzt gespeicherte Version – ungespeicherte Änderungen zuerst speichern.")
    datum = pd.Timestamp(c2.date_input("Handelsdatum", value=max(KAUFTAG, pd.Timestamp.today().normalize()).date(),
                                       format="DD.MM.YYYY", key="ue_datum"))
    if c3.button("Vorschau berechnen", type="primary", icon=":material/preview:", width="stretch", key="ue_los"):
        z = gespeichert.set_index("id").loc[aid]
        e = json.loads(z["eingabe"])
        a, t, h = vorschlag(e, datum, fred, P.ticker_info)
        ss["ue_vor"] = dict(aid=aid, name=z["name"], datum=datum, aktien=a, treas=t, hinweise=h, ver=ss.get("ue_vor", {}).get("ver", 0) + 1)
    vor = ss.get("ue_vor")
    if vor and (vor["aid"] != aid or vor["datum"] != datum):
        st.caption("Auswahl geändert – «Vorschau berechnen» erneut klicken.")
        vor = None
    if vor:
        for h in vor["hinweise"]:
            st.warning(h, icon=":material/info:")
        v = vor["ver"]
        st.markdown(f"**Aktien** · {len(vor['aktien'])} Titel · Stück, Kurs, Sektor und Begründung änderbar")
        aktien = st.data_editor(vor["aktien"], hide_index=True, width="stretch", key=f"ue_a{v}", disabled=["Ticker", "Name", "Betrag aus Vorauswahl"],
                                column_config={"Stück": st.column_config.NumberColumn(min_value=0, step=1, format="%d"),
                                               "Kurs USD": st.column_config.NumberColumn(min_value=0.0, format="%.2f"),
                                               "Sektor": st.column_config.SelectboxColumn(options=C.SEKTOREN),
                                               "Begründung": st.column_config.TextColumn(width="large")}) if len(vor["aktien"]) else vor["aktien"]
        if len(vor["treas"]):
            st.markdown(f"**Treasuries** · {len(vor['treas'])} Anleihen · Nominal, Kurs und Begründung änderbar")
            treas = st.data_editor(vor["treas"], hide_index=True, width="stretch", key=f"ue_t{v}",
                                   disabled=["Kupon %", "Fälligkeit", "Kurs-Quelle", "Betrag aus Vorauswahl"],
                                   column_config={"Nominal USD": st.column_config.TextColumn(help="z. B. 3'000'000"),
                                                  "Kurs %": st.column_config.NumberColumn(min_value=0.0, format="%.4f"),
                                                  "Kupon %": st.column_config.NumberColumn(format="%.3f"),
                                                  "Begründung": st.column_config.TextColumn(width="large")})
        else:
            treas = pd.DataFrame(columns=["Bezeichnung", "Kupon %", "Fälligkeit", "Nominal USD", "Kurs %", "Begründung"])
        begr = st.text_area("Gemeinsame Begründung (gilt für alle Zeilen ohne eigene Begründung)", key=f"ue_b{v}", height=80,
                            placeholder="z. B. Umsetzung IC-Beschluss vom …: Sektorallokation gemäss Seite «Sektorallokation», "
                                        "Taktik Dezember …")
        tx = P.transaktionen(ctx.tabellen.get("transaktionen"))
        kapital, _ = P._einstellungen(ctx.tabellen.get("einstellungen"))
        bestand = 0.0
        cash_frei = kapital
        if len(tx):
            pos, cash_delta, _ = P.positionen(tx)
            b = P.bewerte(pos, {}, fred, pd.Timestamp.today().normalize())
            bestand = float(b["wert"].sum()) if len(b) else 0.0
            cash_frei = kapital + cash_delta
        meld, hart = pruefen(ctx, aktien, treas, begr, kapital, cash_frei, bestand)
        st.markdown("**Prüfung vor dem Buchen**")
        st.markdown("".join(f'<div class="karte-zeile"><span>{ui.badge(s_, {L.ROT: "Verstoss", L.GELB: "Hinweis", L.GRUEN: "ok"}[s_])}'
                            f'&nbsp; {t_}</span><span></span></div>' for s_, t_ in meld), unsafe_allow_html=True)
        weich = any(s_ == L.ROT for s_, _ in meld) and not hart
        trotz = st.checkbox("Trotz Verstössen gegen IPS/Sektorziele buchen (Begründung oben festhalten)", key=f"ue_trotz{v}") if weich else True
        rows = buchungen(aktien, treas, vor["datum"], begr, "x", ctx.person)
        schon = [u for u in uebernahmen(ctx.tabellen) if u.get("analyse") == vor["aid"] and u["aktiv"]]
        if schon:
            st.warning(f"Diese Vorauswahl wurde schon übernommen ({schon[0]['zeit'][:10]}, {schon[0]['aktiv']} Buchungen aktiv) – "
                       "zuerst rückgängig machen oder bewusst ein zweites Mal buchen.", icon=":material/content_copy:")
        if st.button(f"Bestätigen: {len(rows)} Käufe per {vor['datum']:%d.%m.%Y} buchen", type="primary", icon=":material/done_all:",
                     disabled=ctx.nur_lesen or hart or not trotz or not rows, key=f"ue_ok{v}"):
            batch = uuid.uuid4().hex[:6]
            rows = buchungen(aktien, treas, vor["datum"], begr, batch, ctx.person)
            meta = dict(analyse=vor["aid"], name=vor["name"], datum=vor["datum"].date().isoformat(), n=len(rows))
            try:
                ctx.store.anhaengen("einstellungen", [dict(zeit=jetzt(), person=ctx.person, schluessel=f"uebernahme|{batch}",
                                                           wert=json.dumps(meta, ensure_ascii=False))])
            except Exception as e:  # noqa: BLE001
                st.error(f"Nicht gebucht: {e}")
                return
            ss.pop("ue_vor", None)
            ctx.speichern("transaktionen", rows, f"Übernahme «{vor['name']}»: {len(rows)} Käufe per {vor['datum']:%d.%m.%Y} gebucht.")
    # bisherige Übernahmen
    alle = uebernahmen(ctx.tabellen)
    if alle:
        st.markdown("**Bisherige Übernahmen**")
        for u in alle:
            c1, c2 = st.columns([4, 1])
            c1.markdown(f"<div class='klein'><b>{u.get('name', '')}</b> · Handelstag {u.get('datum', '')} · {u.get('n', 0)} Buchungen, "
                        f"davon {u['aktiv']} aktiv · gebucht von {u['person']} am {u['zeit'][:10]}</div>", unsafe_allow_html=True)
            if u["aktiv"] and c2.button("Rückgängig", key=f"ue_undo_{u['batch']}", icon=":material/undo:", disabled=ctx.nur_lesen,
                                        width="stretch"):
                roh = ctx.tabellen["transaktionen"].sort_values("zeit").drop_duplicates("id", keep="last")
                roh = roh[roh["id"].astype(str).str.startswith(f"ue{u['batch']}")
                          & ~roh["storniert"].astype(str).str.strip().isin(["1", "1.0", "True"])]
                z = jetzt()
                storno = [{**r, "zeit": z, "person": ctx.person, "storniert": "1"} for r in roh.to_dict("records")]
                ctx.speichern("transaktionen", storno, f"Übernahme «{u.get('name', '')}» rückgängig gemacht ({len(storno)} Stornos).")
