"""Portfolio-Tracker: Transaktionen → Positionen, Bewertung, Gewichte, Duration, Verlauf vs. S&P 500.

Aktien werden mit Yahoo-Finance-Kursen bewertet. Treasuries mit der aktuellen US-Zinskurve (FRED): Beim Kauf wird die
Rendite aus dem bezahlten Kurs bestimmt und ihr Abstand zur Zinskurve festgehalten; bewertet wird mit
«heutige Kurve + gleicher Abstand». Das ist eine Näherung, kein Händlerkurs.
"""
from __future__ import annotations

import html
import math
import uuid

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from makro import config as C
from makro import ui
from storage import jetzt, zahl

STARTKAPITAL = 10_000_000.0


# ---------------------------------------------------------------------------
# Anleihen-Mathematik (halbjährliche Kupons)
# ---------------------------------------------------------------------------
def _zeiten(T: float) -> list[float]:
    if T <= 0:
        return []
    n = max(1, math.ceil(T * 2 - 1e-9))
    t1 = T - (n - 1) * 0.5
    return [t1 + 0.5 * i for i in range(n)]


def preis(kupon: float, T: float, y: float) -> float:
    """Clean-Preis in % des Nominals. kupon, y in % p.a., T in Jahren."""
    if T <= 0:
        return 100.0
    c, r = kupon / 100, y / 100
    zt = _zeiten(T)
    df = [(1 + r / 2) ** (-2 * t) for t in zt]
    dirty = sum(c / 2 * 100 * d for d in df) + 100 * df[-1]
    stueckzins = c / 2 * 100 * (0.5 - zt[0]) / 0.5
    return dirty - stueckzins


def rendite(kupon: float, T: float, p: float) -> float:
    lo, hi = -5.0, 40.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if preis(kupon, T, mid) > p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def mod_duration(kupon: float, T: float, y: float) -> float:
    if T <= 0:
        return 0.0
    p0, p1, p2 = preis(kupon, T, y), preis(kupon, T, y - 0.01), preis(kupon, T, y + 0.01)
    return (p1 - p2) / (2 * p0 * 0.0001)


def kurve_am(fred: dict, datum: pd.Timestamp) -> dict[float, float]:
    out = {}
    for serie, laufzeit in C.ZINSKURVE.items():
        s = fred.get(serie)
        if s is None or s.empty:
            continue
        s = s[s.index <= datum]
        if len(s):
            out[laufzeit] = float(s.iloc[-1])
    return out


def kurve_rendite(kurve: dict[float, float], T: float):
    if not kurve:
        return None
    xs = sorted(kurve)
    return float(np.interp(T, xs, [kurve[x] for x in xs]))


# ---------------------------------------------------------------------------
# Transaktionen → Positionen
# ---------------------------------------------------------------------------
def transaktionen(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["id", "datum", "art", "klasse", "ticker", "name", "sektor", "menge", "kurs", "kupon",
                                     "faelligkeit", "notiz", "person", "zeit"])
    d = df.sort_values("zeit").drop_duplicates("id", keep="last")
    d = d[d["storniert"].astype(str).str.strip().isin(["", "0", "nan", "None"])].copy()
    d["datum"] = pd.to_datetime(d["datum"], errors="coerce")
    d["faelligkeit"] = pd.to_datetime(d["faelligkeit"], errors="coerce")
    for c in ("menge", "kurs", "kupon"):
        d[c] = d[c].map(zahl)
    d = d.dropna(subset=["datum", "menge", "kurs"])
    return d.sort_values(["datum", "zeit"])


def _betrag(r) -> float:
    return r["menge"] * r["kurs"] / (100 if r["klasse"] == "Treasury" else 1)


def positionen(tx: pd.DataFrame, bis: pd.Timestamp | None = None) -> tuple[dict, float, float]:
    """Gibt ({schlüssel: position}, cash, realisierter Gewinn) bis zum Datum zurück (gleitender Einstandskurs)."""
    pos, cash, real = {}, None, 0.0
    cash = 0.0
    for _, r in tx.iterrows():
        if bis is not None and r["datum"] > bis:
            break
        k = (r["klasse"], str(r["ticker"]).upper())
        p = pos.setdefault(k, dict(klasse=r["klasse"], ticker=str(r["ticker"]).upper(), name=r["name"], sektor=r["sektor"],
                                   menge=0.0, einstand=0.0, kupon=r["kupon"] or 0.0, faelligkeit=r["faelligkeit"],
                                   kaufdatum=r["datum"], kaufkurs=r["kurs"]))
        betrag = _betrag(r)
        if r["art"] == "Kauf":
            p["einstand"] += betrag
            p["menge"] += r["menge"]
            cash -= betrag
        else:
            menge = min(r["menge"], p["menge"])
            anteil = menge / p["menge"] if p["menge"] else 0
            kosten = p["einstand"] * anteil
            erloes = menge * r["kurs"] / (100 if r["klasse"] == "Treasury" else 1)
            real += erloes - kosten
            p["einstand"] -= kosten
            p["menge"] -= menge
            cash += erloes
    pos = {k: v for k, v in pos.items() if v["menge"] > 1e-9}
    return pos, cash, real


def bewerte(pos: dict, kurse: dict, fred: dict, datum: pd.Timestamp) -> pd.DataFrame:
    kurve_heute = kurve_am(fred, datum)
    zeilen = []
    for p in pos.values():
        if p["klasse"] == "Aktie":
            s = kurse.get(p["ticker"])
            s = s[s.index <= datum] if s is not None else None
            kurs = float(s.iloc[-1]) if s is not None and len(s) else p["einstand"] / p["menge"]
            quelle = "Yahoo Finance" if s is not None and len(s) else "Einstandskurs (kein Kurs gefunden)"
            wert = p["menge"] * kurs
            zeilen.append(dict(p, kurs=kurs, wert=wert, zins=0.0, rendite=None, duration=None, restlaufzeit=None, quelle=quelle))
        else:
            T = max(0.0, (p["faelligkeit"] - datum).days / 365.25) if pd.notna(p["faelligkeit"]) else 0.0
            T0 = max(0.0, (p["faelligkeit"] - p["kaufdatum"]).days / 365.25) if pd.notna(p["faelligkeit"]) else 0.0
            kupon = p["kupon"] or 0.0
            y_kauf = rendite(kupon, T0, p["kaufkurs"]) if T0 > 0 else None
            k0, k1 = kurve_rendite(kurve_am(fred, p["kaufdatum"]), T0), kurve_rendite(kurve_heute, T)
            if y_kauf is not None and k0 is not None and k1 is not None:
                y = k1 + (y_kauf - k0)
                kurs, quelle = preis(kupon, T, y), "Zinskurve FRED (Näherung)"
            else:
                y, kurs, quelle = y_kauf, p["kaufkurs"], "Einstandskurs"
            tage = max(0, (datum - p["kaufdatum"]).days)
            zins = p["menge"] * kupon / 100 * tage / 365.25  # vereinfachte Kuponerträge seit Kauf
            wert = p["menge"] * kurs / 100
            zeilen.append(dict(p, kurs=kurs, wert=wert, zins=zins, rendite=y, restlaufzeit=T,
                               duration=mod_duration(kupon, T, y) if y is not None else None, quelle=quelle))
    return pd.DataFrame(zeilen)


def verlauf(tx: pd.DataFrame, kurse: dict, fred: dict, start_kapital: float) -> pd.Series:
    if tx.empty:
        return pd.Series(dtype=float)
    tage = pd.bdate_range(tx["datum"].min(), pd.Timestamp.today().normalize())
    werte = {}
    for d in tage:
        pos, cash, _ = positionen(tx, d)
        b = bewerte(pos, kurse, fred, d)
        werte[d] = start_kapital + cash + (b["wert"].sum() + b["zins"].sum() if len(b) else 0.0)
    return pd.Series(werte)


# ---------------------------------------------------------------------------
# Seite
# ---------------------------------------------------------------------------
def _usd(x, d=0):
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{ui.de(x, d)} USD"


def _einstellungen(einst: pd.DataFrame) -> tuple[float, dict]:
    kapital, bm = STARTKAPITAL, {}
    if einst is not None and not einst.empty:
        for _, r in einst.sort_values("zeit").iterrows():
            v = zahl(r["wert"])
            if r["schluessel"] == "startkapital" and v:
                kapital = v
            elif str(r["schluessel"]).startswith("bm|") and v is not None:
                bm[r["schluessel"][3:]] = v
    return kapital, bm


def seite_portfolio(ctx, fred: dict, lade_kurse, markt_tab: pd.DataFrame | None):
    ui.seitenkopf("Portfolio", "Positionen, Bewertung zu Marktpreisen, Abweichungen zur Zielallokation und alle Transaktionen mit Begründung.")
    tab = ctx.tabellen
    kapital, bm = _einstellungen(tab.get("einstellungen"))
    tx = transaktionen(tab.get("transaktionen"))
    aktien_ticker = tuple(sorted(set(tx.loc[tx["klasse"] == "Aktie", "ticker"].str.upper()))) if len(tx) else ()
    kurse, kurs_fehler = ({}, [])
    if aktien_ticker:
        kurse, kurs_fehler, _ = lade_kurse(aktien_ticker + ("^GSPC",))
    heute = pd.Timestamp.today().normalize()
    pos, cash_delta, realisiert = positionen(tx)
    b = bewerte(pos, kurse, fred, heute)
    cash = kapital + cash_delta
    aktien = float(b.loc[b["klasse"] == "Aktie", "wert"].sum()) if len(b) else 0.0
    treas = float(b.loc[b["klasse"] == "Treasury", "wert"].sum()) if len(b) else 0.0
    zinsen = float(b["zins"].sum()) if len(b) else 0.0
    total = aktien + treas + cash + zinsen
    rendite_tot = (total / kapital - 1) * 100

    spx_txt = "–"
    if len(tx):
        spx = kurse.get("^GSPC")
        if spx is not None and len(spx):
            s0 = spx[spx.index <= tx["datum"].min()]
            if len(s0):
                spx_r = (spx.iloc[-1] / s0.iloc[-1] - 1) * 100
                spx_txt = f"S&P 500 seit erstem Trade: {ui.de(spx_r, 2, True)} %"

    ui.kopf_kpis([
        ("Portfoliowert", _usd(total), f"Startkapital {_usd(kapital)}"),
        ("Rendite gesamt", f"{ui.de(rendite_tot, 2, True)} %", spx_txt),
        ("Aktien / Treasuries / Cash", f"{aktien / total * 100:.0f} / {treas / total * 100:.0f} / {cash / total * 100:.0f} %" if total else "–",
         f"Empfehlung {ctx.alloc['aktien']:.0f} / {ctx.alloc['anleihen']:.0f} / {ctx.alloc['cash']:.0f} %"),
        ("Duration Treasuries", f"{ui.de(_wdur(b), 1)} J." if treas else "–",
         f"Empfehlung {ui.de(ctx.alloc['duration'], 1)} J. · IC um 3,5 J."),
    ])
    if not len(tx):
        st.info("Noch keine Transaktionen erfasst. Unten die erste Transaktion eintragen – ab dann rechnet die App Gewichte, "
                "Rendite, Duration und den Verlauf gegenüber dem S&P 500 automatisch.")
    if kurs_fehler:
        st.warning("Keine Kurse gefunden für: " + ", ".join(kurs_fehler) + " – Ticker prüfen (z. B. BRK-B statt BRK.B).")

    if total and len(tx):
        warn = []
        for name, wert_, (lo, hi) in (("Aktien", aktien / total * 100, C.KORRIDOR["aktien"]),
                                     ("Treasuries", treas / total * 100, C.KORRIDOR["treasuries"]),
                                     ("Cash", cash / total * 100, C.KORRIDOR["cash"])):
            if not lo <= wert_ <= hi:
                warn.append(f"{name} {ui.de(wert_, 1)} % (Korridor {lo}–{hi} %)")
        if cash < -1:
            warn.append(f"Cash negativ ({_usd(cash)}) – mehr gekauft als Kapital vorhanden")
        if warn:
            st.warning("Ausserhalb des IC-Korridors bzw. der Regeln: " + "; ".join(warn))

    t1, t2, t3, t5, t4 = st.tabs(["Positionen", "Struktur & Abweichungen", "Verlauf", "Performance & Risiko", "Transaktionen"])
    with t1:
        if len(b):
            d = b.copy()
            d["Gewicht"] = d["wert"] / total * 100
            d["G/V"] = d["wert"] + d["zins"] - d["einstand"]
            out = pd.DataFrame({
                "Klasse": d["klasse"], "Titel": d["ticker"] + d["name"].map(lambda n: f" – {n}" if n else ""),
                "Sektor": d["sektor"].fillna(""), "Menge / Nominal": d["menge"].map(lambda x: ui.de(x, 0)),
                "Kurs": [f"{ui.de(k, 2)}" + (" %" if c == "Treasury" else "") for k, c in zip(d["kurs"], d["klasse"])],
                "Marktwert": d["wert"].map(lambda x: _usd(x)), "Gewicht": d["Gewicht"].map(lambda x: f"{ui.de(x, 1)} %"),
                "G/V inkl. Zinsen": d["G/V"].map(lambda x: _usd(x)),
                "Rendite / Duration": [f"{ui.de(y, 2)} % / {ui.de(du, 1)} J." if y is not None and not pd.isna(y) else "–"
                                       for y, du in zip(d["rendite"], d["duration"])],
                "Bewertung": d["quelle"]})
            st.dataframe(out, hide_index=True, width="stretch")
            st.caption(f"Cash: {_usd(cash)} · realisierte Gewinne: {_usd(realisiert)} · Kuponerträge (vereinfacht, seit Kauf): "
                       f"{_usd(zinsen)}. Treasuries: Näherung über die FRED-Zinskurve mit konstantem Abstand seit Kauf.")
            gross = d.sort_values("Gewicht", ascending=False).iloc[0]
            if gross["klasse"] == "Aktie" and gross["Gewicht"] > 10:
                st.warning(f"Klumpenrisiko: {gross['ticker']} macht {ui.de(gross['Gewicht'], 1)} % des Portfolios aus.")
        else:
            st.caption("Keine offenen Positionen.")

    with t2:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Anlageklassen: Ist vs. Empfehlung**")
            ist = [aktien / total * 100 if total else 0, treas / total * 100 if total else 0, cash / total * 100 if total else 0]
            soll = [ctx.alloc["aktien"], ctx.alloc["anleihen"], ctx.alloc["cash"]]
            fig = go.Figure([go.Bar(name="Ist", x=["Aktien", "Treasuries", "Cash"], y=ist, marker_color=ui.BLAU,
                                    text=[f"{x:.0f} %" for x in ist], textposition="outside"),
                             go.Bar(name="Empfehlung", x=["Aktien", "Treasuries", "Cash"], y=soll, marker_color="#94A3B8",
                                    text=[f"{x:.0f} %" for x in soll], textposition="outside")])
            fig.update_layout(barmode="group", height=300, margin=dict(l=8, r=8, t=10, b=8), plot_bgcolor="white",
                              legend=dict(orientation="h", y=-0.15))
            fig.update_yaxes(ticksuffix=" %", gridcolor="#EEF0F3")
            ui.zeige(fig)
        with c2:
            st.markdown("**Sektoren im Aktienteil**")
            emp = dict(zip(ctx.alloc["sektoren"]["sektor"], ctx.alloc["sektoren"]["empfehlung"]))
            ak = b[b["klasse"] == "Aktie"] if len(b) else pd.DataFrame(columns=["sektor", "wert"])
            gew = ak.groupby("sektor")["wert"].sum() / aktien * 100 if aktien else pd.Series(dtype=float)
            zeilen = []
            for s in C.SEKTOREN:
                g = float(gew.get(s, 0.0))
                ref = bm.get(s)
                diff = g - ref if ref is not None else None
                richtung = "–" if diff is None else ("Über" if diff > 1 else "Unter" if diff < -1 else "Neutral")
                e = emp.get(s, "Neutral").replace("gewichten", "")
                passt = "–" if diff is None else ("✓" if (richtung, e) in (("Über", "Über"), ("Unter", "Unter"), ("Neutral", "Neutral")) else "≠")
                zeilen.append({"Sektor": s, "Ist": f"{ui.de(g, 1)} %", "Benchmark": f"{ui.de(ref, 1)} %" if ref is not None else "–",
                               "Abweichung": f"{ui.de(diff, 1, True)} PP" if diff is not None else "–",
                               "Empfehlung": e, "Passt": passt})
            st.dataframe(pd.DataFrame(zeilen), hide_index=True, width="stretch", height=420)
            if not bm:
                st.caption("Für die Abweichung zum S&P 500 unten unter «Einstellungen Portfolio» die aktuellen Sektorgewichte "
                           "eintragen (Quelle: S&P-500-Factsheet von S&P Dow Jones Indices).")

    with t3:
        if len(tx):
            v = verlauf(tx, kurse, fred, kapital)
            spx = kurse.get("^GSPC")
            fig = go.Figure()
            if len(v):
                fig.add_trace(go.Scatter(x=v.index, y=v / kapital * 100, name="Portfolio", line=dict(color=ui.BLAU, width=2.5)))
            if spx is not None and len(v):
                s = spx[spx.index >= v.index[0]]
                if len(s):
                    fig.add_trace(go.Scatter(x=s.index, y=s / s.iloc[0] * 100, name="S&P 500", line=dict(color="#94A3B8")))
            fig.update_layout(height=360, margin=dict(l=8, r=8, t=10, b=8), plot_bgcolor="white", hovermode="x unified",
                              legend=dict(orientation="h", y=-0.15))
            fig.update_yaxes(title="Index (Start = 100)", gridcolor="#EEF0F3")
            ui.zeige(fig)
            st.caption("Portfolio inkl. Cash und vereinfachter Kuponerträge; S&P 500 als Kursindex ohne Dividenden.")
        else:
            st.caption("Der Verlauf erscheint nach der ersten Transaktion.")

    with t5:
        if len(tx):
            _performance(ctx, tx, kurse, fred, kapital, lade_kurse)
        else:
            st.caption("Performance und Risiko erscheinen nach der ersten Transaktion.")

    with t4:
        _formular(ctx, fred, lade_kurse)
        roh = tab.get("transaktionen")
        if roh is not None and not roh.empty:
            liste = transaktionsliste(roh)
            st.markdown("**Alle Transaktionen**")
            st.dataframe(liste.drop(columns=["id"]), hide_index=True, width="stretch",
                         column_config={"Begründung": st.column_config.TextColumn(width="large")})
            c1, c2, c3 = st.columns([2.2, 1, 1.3])
            offen = liste[liste["Status"] == "aktiv"]
            if len(offen):
                wahl = c1.selectbox("Transaktion stornieren", offen["id"], format_func=lambda i: (
                    lambda r: f"{r['Datum']} · {r['Art']} {r['Titel']} · {r['Menge']}")(offen.set_index("id").loc[i]),
                    key="storno_wahl")
                if c2.button("Stornieren", key="storno_btn"):
                    r = roh.sort_values("zeit").drop_duplicates("id", keep="last").set_index("id").loc[wahl]
                    zeile = {k: r[k] for k in r.index}
                    zeile.update(id=wahl, zeit=jetzt(), person=ctx.person, storniert="1")
                    ctx.speichern("transaktionen", [zeile], "Transaktion storniert.")
            if c3.button("PDF erstellen", icon=":material/picture_as_pdf:", key="tx_pdf_btn"):
                try:
                    st.session_state["tx_pdf"] = transaktionen_pdf(liste, b, cash, total, kapital, aktien, treas, ctx)
                except Exception as e:  # noqa: BLE001
                    st.error(f"PDF konnte nicht erstellt werden: {e}")
            if st.session_state.get("tx_pdf"):
                c3.download_button("Transaktionsübersicht", st.session_state["tx_pdf"],
                                   f"Transaktionen_Portfolio_{pd.Timestamp.today():%Y-%m-%d}.pdf", "application/pdf", icon=":material/download:")

    with st.expander("Einstellungen Portfolio (Startkapital, S&P-500-Sektorgewichte)"):
        with st.form("pf_einst"):
            k = st.number_input("Startkapital (USD)", min_value=0.0, value=float(kapital), step=100000.0, format="%.0f")
            st.markdown("S&P-500-Sektorgewichte in % (Benchmark für die Abweichung, mit Stand und Quelle notieren):")
            ed = st.data_editor(pd.DataFrame({"Sektor": C.SEKTOREN, "Gewicht %": [bm.get(s) for s in C.SEKTOREN]}),
                                hide_index=True, disabled=["Sektor"], key="bm_edit")
            if st.form_submit_button("Speichern"):
                zeilen = [dict(zeit=jetzt(), person=ctx.person, schluessel="startkapital", wert=k)]
                zeilen += [dict(zeit=jetzt(), person=ctx.person, schluessel=f"bm|{r['Sektor']}", wert=r["Gewicht %"])
                           for _, r in ed.iterrows() if r["Gewicht %"] is not None and not pd.isna(r["Gewicht %"])]
                ctx.speichern("einstellungen", zeilen, "Portfolio-Einstellungen gespeichert.")


def performance_daten(tx, kurse, fred, kapital, neutral: dict) -> dict | None:
    """Realisierte Tagesrenditen des Portfolios vs. neutrales IC-Portfolio und S&P 500 (gleiche Tage)."""
    import analyse as A

    v = verlauf(tx, kurse, fred, kapital)
    if len(v) < 2:
        return None
    hist, _, _ = A.lade_historie((A.SPX, A.NEUTRAL_PROXY, A.RF_TICKER), 2)
    if A.SPX not in hist or A.NEUTRAL_PROXY not in hist:
        return None
    # Startpunkt = Kapital am Vortag des ersten Trades, damit auch der erste Tag (Kauf vs. Schlusskurs) zählt
    v = pd.concat([pd.Series([kapital], index=[v.index[0] - pd.offsets.BDay(1)]), v])
    r_p = v.pct_change().dropna()
    px = pd.concat({t: hist[t] for t in (A.SPX, A.NEUTRAL_PROXY)}, axis=1).sort_index().ffill()
    px = px.reindex(px.index.union(v.index)).ffill().reindex(v.index)
    R = px.pct_change().reindex(r_p.index).fillna(0.0)
    rf = A.rf_reihe(hist, r_p.index)
    na, nc = neutral["aktien"] / 100, neutral.get("cash", 5) / 100
    r_n = A.portfolio_reihe(R, rf, {A.SPX: na, A.NEUTRAL_PROXY: 1 - na - nc}, nc)
    r_s = R[A.SPX]
    kp, kn = A.kennzahlen(r_p, rf, r_s), A.kennzahlen(r_n, rf, r_s)
    akt = r_p - r_n
    if len(akt) >= 20 and akt.std() > 0:
        te = float(akt.std(ddof=1) * np.sqrt(A.TAGE))
        kp.update(te_neutral=te * 100, ir_neutral=float(akt.mean() * A.TAGE / te))
    gesamt = lambda r: float((1 + r).prod() - 1) * 100  # noqa: E731
    return dict(v=v, r_p=r_p, r_n=r_n, r_s=r_s, kp=kp, kn=kn, g_p=gesamt(r_p), g_n=gesamt(r_n), g_s=gesamt(r_s),
                tage=len(r_p))


def _performance(ctx, tx, kurse, fred, kapital, lade_kurse):
    import analyse as A

    d = performance_daten(tx, kurse, fred, kapital, ctx.alloc["neutral"])
    if d is None:
        st.info("Für den Vergleich fehlen noch Kursdaten (Yahoo Finance) – bitte später nochmals öffnen.")
        return
    kp, kn = d["kp"], d["kn"]
    st.markdown("**Realisiert seit dem ersten Trade** · Vergleich mit dem neutralen IC-Portfolio "
                f"({ctx.alloc['neutral']['aktien']:.0f} / {100 - ctx.alloc['neutral']['aktien'] - ctx.alloc['neutral'].get('cash', 5):.0f} / "
                f"{ctx.alloc['neutral'].get('cash', 5):.0f}) und dem S&P 500 inkl. Dividenden")
    ui.kopf_kpis([
        ("Rendite seit Start", A._pct(d["g_p"], 2, True), f"neutral {A._pct(d['g_n'], 2, True)} · S&P 500 {A._pct(d['g_s'], 2, True)}"),
        ("Mehrrendite vs. neutral", f"{ui.de(d['g_p'] - d['g_n'], 2, True)} PP",
         f"vs. S&P 500 {ui.de(d['g_p'] - d['g_s'], 2, True)} PP"),
        ("Sharpe Ratio", A._zahl(kp.get("sharpe")), f"neutral {A._zahl(kn.get('sharpe'))}"),
        ("Max. Drawdown", A._pct(kp.get("mdd")), f"neutral {A._pct(kn.get('mdd'))}"),
    ])
    if d["tage"] < 20:
        st.caption(f"Erst {d['tage']} Handelstage – Sharpe Ratio, Volatilität, Beta und Tracking Error werden ab 20 Handelstagen "
                   "berechnet, aussagekräftig sind sie ab etwa 2 Monaten.")
    else:
        ui.kopf_kpis([
            ("Volatilität p.a.", A._pct(kp.get("vola")), f"neutral {A._pct(kn.get('vola'))}"),
            ("Beta zum S&P 500", A._zahl(kp.get("beta")), f"Korrelation {A._zahl(kp.get('korr'))}"),
            ("Tracking Error vs. neutral", A._pct(kp.get("te_neutral")), f"Information Ratio {A._zahl(kp.get('ir_neutral'))}"),
            ("Jensen-Alpha p.a.", A._pct(kp.get("alpha"), 1, True), f"Sortino {A._zahl(kp.get('sortino'))}"),
        ])
    fig = go.Figure()
    for name, rr, farbe, breite in (("Portfolio", d["r_p"], ui.BLAU, 2.6), ("Neutral (IC)", d["r_n"], ui.GOLD, 1.8),
                                    ("S&P 500", d["r_s"], ui.STAHL, 1.6)):
        w = pd.concat([pd.Series([100.0], index=[d["v"].index[0]]), (1 + rr).cumprod() * 100])
        fig.add_trace(go.Scatter(x=w.index, y=w, name=name, line=dict(color=farbe, width=breite)))
    fig.update_layout(height=340, margin=dict(l=8, r=8, t=10, b=8), hovermode="x unified")
    fig.update_yaxes(title="Index (Start = 100)")
    ui.zeige(fig)

    st.markdown("**Risiko der heutigen Positionen** · auf Basis der Kursschwankungen der letzten 12 Monate")
    vor = A.portfolio_vorlage(ctx.tabellen, fred, lade_kurse)
    if vor is None:
        return
    tick = sorted({A._txt(t).strip().upper() for t in vor[0]["Ticker"] if A._txt(t).strip()})
    prox = sorted({A.PROXY[A._txt(b)][0] for b in vor[1]["Laufzeit"] if A._txt(b) in A.PROXY})
    hist, _, _ = A.lade_historie(tuple(tick + prox + [A.SPX, A.NEUTRAL_PROXY, A.RF_TICKER]), 1)
    letzte = {t: float(s.iloc[-1]) for t, s in hist.items()}
    pos, cash, total, _ = A.zusammenstellen(vor[0], vor[1], vor[2], vor[3], False, letzte)
    erg = A.rechne(pos, cash, total, hist, ctx.alloc["neutral"]) if total > 0 and len(pos) else None
    if erg is None:
        st.caption("Für die Risikoanalyse fehlen Kursdaten.")
        return
    kv = erg["k"]["Vorauswahl"]
    top = erg["beitrag"].sort_values(ascending=False).head(3)
    ui.kopf_kpis([
        ("VaR 95 % · 1 Tag", f"{ui.de(kv.get('var95', 0) / 100 * total, 0)} USD", f"CVaR {ui.de(kv.get('cvar95', 0) / 100 * total, 0)} USD"),
        ("Volatilität p.a. (ex ante)", A._pct(kv.get("vola")), f"neutral {A._pct(erg['k']['Neutral (IC)'].get('vola'))}"),
        ("Beta zum S&P 500", A._zahl(kv.get("beta")), f"Sharpe 12 Mt. {A._zahl(kv.get('sharpe'))}"),
        ("Grösste Risikotreiber", ", ".join(A.PROXY_NAME.get(t, t) for t in top.index[:2]),
         " · ".join(f"{A.PROXY_NAME.get(t, t)} {ui.de(v, 0)} %" for t, v in top.items())),
    ])
    info = {t: ticker_info(t) for t in pos.loc[pos["klasse"] == "Aktie", "ticker"]}
    checks = A.pruefungen(pos, cash, total, info, ctx.alloc)
    st.markdown("".join(f'<div class="karte-zeile"><span>{ui.badge(s_, {"Rot": "Verstoss", "Gelb": "Hinweis", "Grün": "ok"}[s_])}'
                        f'&nbsp; {txt}</span><span></span></div>' for s_, txt in checks), unsafe_allow_html=True)
    st.caption("Mehr Details (Einzeltitel, Korrelationen, Was-wäre-wenn) auf der Seite «Analyse» mit «Aktuelles Portfolio übernehmen».")


def _wdur(b: pd.DataFrame) -> float:
    t = b[(b["klasse"] == "Treasury") & b["duration"].notna()] if len(b) else b
    return float((t["duration"] * t["wert"]).sum() / t["wert"].sum()) if len(t) and t["wert"].sum() else 0.0


GICS_DE = {"Information Technology": "IT", "Health Care": "Gesundheit", "Financials": "Finanzen",
           "Consumer Discretionary": "Zykl. Konsum", "Communication Services": "Kommunikation", "Industrials": "Industrie",
           "Consumer Staples": "Basiskonsum", "Energy": "Energie", "Utilities": "Versorger", "Real Estate": "Immobilien",
           "Materials": "Grundstoffe"}
YAHOO_DE = {"Technology": "IT", "Healthcare": "Gesundheit", "Financial Services": "Finanzen", "Consumer Cyclical": "Zykl. Konsum",
            "Communication Services": "Kommunikation", "Industrials": "Industrie", "Consumer Defensive": "Basiskonsum",
            "Energy": "Energie", "Utilities": "Versorger", "Real Estate": "Immobilien", "Basic Materials": "Grundstoffe"}
SP500_LISTE = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def sp500_liste() -> dict:
    """Ticker → (Name, Sektor) aus der öffentlichen S&P-500-Liste (GICS). Leer, falls nicht erreichbar."""
    try:
        import io

        import requests

        r = requests.get(SP500_LISTE, timeout=15)
        df = pd.read_csv(io.StringIO(r.text))
        sym = next(c for c in df.columns if c.lower() in ("symbol", "ticker"))
        name = next((c for c in df.columns if c.lower() in ("security", "name")), sym)
        sek = next(c for c in df.columns if "sector" in c.lower())
        return {str(z[sym]).upper().replace(".", "-"): (str(z[name]), GICS_DE.get(str(z[sek]), ""))
                for _, z in df.iterrows()}
    except Exception:  # noqa: BLE001
        return {}


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def yahoo_info(ticker: str) -> tuple[str, str]:
    try:
        import yfinance as yf

        i = yf.Ticker(ticker).info or {}
        return str(i.get("shortName") or i.get("longName") or ""), YAHOO_DE.get(str(i.get("sector") or ""), "")
    except Exception:  # noqa: BLE001
        return "", ""


def ticker_info(ticker: str) -> dict:
    """Name, Sektor und ob im S&P 500 – zuerst aus der GICS-Liste, sonst von Yahoo Finance."""
    t = ticker.strip().upper().replace(".", "-")
    if not t:
        return {}
    liste = sp500_liste()
    if t in liste:
        name, sektor = liste[t]
        return dict(ticker=t, name=name, sektor=sektor, im_index=True, quelle="S&P-500-Liste (GICS)")
    name, sektor = yahoo_info(t)
    return dict(ticker=t, name=name, sektor=sektor, im_index=False if liste else None, quelle="Yahoo Finance" if sektor else "")


def transaktionsliste(roh: pd.DataFrame) -> pd.DataFrame:
    d = roh.sort_values("zeit").drop_duplicates("id", keep="last").copy()
    d["_datum"] = pd.to_datetime(d["datum"], errors="coerce")
    d = d.sort_values(["_datum", "zeit"], ascending=False)
    zeilen = []
    for _, r in d.iterrows():
        menge, kurs = zahl(r["menge"]), zahl(r["kurs"])
        treas = r["klasse"] == "Treasury"
        betrag = menge * kurs / (100 if treas else 1) if menge is not None and kurs is not None else None
        storno = str(r["storniert"]).strip() in ("1", "1.0", "True")
        zeilen.append({"id": r["id"], "Datum": pd.Timestamp(r["_datum"]).strftime("%d.%m.%Y") if pd.notna(r["_datum"]) else r["datum"],
                       "Art": r["art"], "Klasse": r["klasse"],
                       "Titel": str(r["ticker"]) + (f" – {r['name']}" if r["name"] and str(r["name"]) != "nan" else ""),
                       "Sektor": r["sektor"] if r["sektor"] and str(r["sektor"]) != "nan" else "",
                       "Menge": ui.de(menge, 0) if menge is not None else "",
                       "Kurs": (ui.de(kurs, 2) + (" %" if treas else " USD")) if kurs is not None else "",
                       "Betrag": _usd(betrag) if betrag is not None else "",
                       "Begründung": r["notiz"] if r["notiz"] and str(r["notiz"]) != "nan" else "– (keine Begründung erfasst)",
                       "Erfasst von": r["person"], "Status": "storniert" if storno else "aktiv"})
    return pd.DataFrame(zeilen)


def transaktionen_pdf(liste: pd.DataFrame, b: pd.DataFrame, cash, total, kapital, aktien, treas, ctx) -> bytes:
    from fpdf import FPDF
    from fpdf.fonts import FontFace

    from bericht import _t

    class P(FPDF):
        def header(self):
            self.set_font("Helvetica", "B", 9)
            self.set_text_color(120, 130, 150)
            self.cell(0, 6, _t("Portfoliosimulation FHGR - Transaktionsübersicht"), align="L")
            self.cell(0, 6, _t(pd.Timestamp.now().strftime("%d.%m.%Y %H:%M")), align="R", new_x="LMARGIN", new_y="NEXT")
            self.set_text_color(0, 0, 0)

        def footer(self):
            self.set_y(-12)
            self.set_font("Helvetica", "", 8)
            self.cell(0, 6, f"Seite {self.page_no()}", align="C")

    def tabelle(pdf, kopf, zeilen, breiten, gross=8):
        pdf.set_font("Helvetica", "", gross)
        with pdf.table(col_widths=breiten, text_align="LEFT", line_height=4.2, first_row_as_headings=True,
                       headings_style=FontFace(emphasis="BOLD", fill_color=(238, 240, 243))) as t:
            r = t.row()
            for k in kopf:
                r.cell(_t(k))
            for z in zeilen:
                r = t.row()
                for v in z:
                    r.cell(_t(v))

    def titel(pdf, text):
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(47, 93, 168)
        pdf.cell(0, 7, _t(text), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)

    pdf = P(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(True, margin=14)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 17)
    pdf.cell(0, 9, _t("Transaktionsübersicht Portfolio"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    r = (total / kapital - 1) * 100 if kapital else 0
    pdf.multi_cell(0, 4.8, _t(
        f"Stand {pd.Timestamp.today():%d.%m.%Y}, erstellt von {ctx.person}. Portfoliowert {ui.de(total, 0)} USD "
        f"({ui.de(r, 2, True)} % seit Start, Startkapital {ui.de(kapital, 0)} USD). Aktien {ui.de(aktien / total * 100 if total else 0, 1)} % · "
        f"Treasuries {ui.de(treas / total * 100 if total else 0, 1)} % · Cash {ui.de(cash / total * 100 if total else 0, 1)} %. "
        f"Empfehlung Dashboard: {ctx.alloc['aktien']:.0f} / {ctx.alloc['anleihen']:.0f} / {ctx.alloc['cash']:.0f} %."),
        new_x="LMARGIN", new_y="NEXT")

    if len(b):
        titel(pdf, "Positionen")
        tabelle(pdf, ["Klasse", "Titel", "Sektor", "Menge/Nominal", "Kurs", "Marktwert USD", "Gewicht", "G/V inkl. Zinsen USD"],
                [[z["klasse"], z["ticker"] + (f" - {z['name']}" if z["name"] else ""), z["sektor"] or "",
                  ui.de(z["menge"], 0), ui.de(z["kurs"], 2) + (" %" if z["klasse"] == "Treasury" else ""),
                  ui.de(z["wert"], 0), ui.de(z["wert"] / total * 100 if total else 0, 1) + " %",
                  ui.de(z["wert"] + z["zins"] - z["einstand"], 0)] for _, z in b.iterrows()],
                (20, 70, 28, 30, 25, 35, 20, 40))

    titel(pdf, "Transaktionen mit Begründung")
    tabelle(pdf, ["Datum", "Art", "Titel", "Sektor", "Menge", "Kurs", "Betrag", "Begründung", "Von", "Status"],
            [[z["Datum"], z["Art"], z["Titel"], z["Sektor"], z["Menge"], z["Kurs"], z["Betrag"], z["Begründung"],
              z["Erfasst von"], z["Status"]] for _, z in liste.iterrows()],
            (19, 14, 42, 22, 20, 22, 28, 75, 18, 17), gross=7.5)
    pdf.ln(3)
    pdf.set_font("Helvetica", "", 7)
    pdf.multi_cell(0, 4, _t("Treasuries bewertet über die US-Zinskurve (FRED) mit konstantem Abstand seit Kauf (Näherung); Aktien "
                            "mit Schlusskursen von Yahoo Finance. Kuponerträge vereinfacht. Stornierte Transaktionen sind "
                            "aufgeführt, fliessen aber nicht in die Bewertung ein."))
    return bytes(pdf.output())


def _formular(ctx, fred, lade_kurse):
    st.markdown("**Transaktion erfassen**")
    c0, c00 = st.columns([1, 2])
    klasse = c0.radio("Anlageklasse", ["Aktie", "Treasury"], horizontal=True, key="tx_klasse")
    info = {}
    if klasse == "Aktie":
        tk = c00.text_input("Ticker (S&P 500)", placeholder="z. B. MSFT – Enter drücken", key="tx_ticker")
        info = ticker_info(tk) if tk.strip() else {}
        if info:
            if info.get("sektor"):
                txt = f"{info['name']} · **{info['sektor']}** (Quelle: {info['quelle']})"
                st.markdown(txt)
            else:
                st.warning("Sektor nicht gefunden – bitte unten von Hand wählen.")
            if info.get("im_index") is False:
                st.warning(f"{info['ticker']} steht nicht in der aktuellen S&P-500-Liste – Simulationsregel prüfen.")
    with st.form("tx", clear_on_submit=True):
        c1, c2 = st.columns(2)
        art = c1.radio("Art", ["Kauf", "Verkauf"], horizontal=True)
        datum = c2.date_input("Handelsdatum", value=pd.Timestamp.today().date(), format="DD.MM.YYYY")
        if klasse == "Aktie":
            ticker = info.get("ticker", "")
            c4, c5, c6 = st.columns(3)
            sek_idx = C.SEKTOREN.index(info["sektor"]) if info.get("sektor") in C.SEKTOREN else None
            sektor = c4.selectbox("Sektor (automatisch, änderbar)", C.SEKTOREN, index=sek_idx,
                                  placeholder="Sektor wählen")
            menge = c5.number_input("Stück", min_value=0.0, step=10.0, format="%.0f")
            kurs = c6.text_input("Kurs USD (leer = Schlusskurs automatisch)")
            name, kupon, faell = info.get("name", ""), None, None
        else:
            c3, c4, c5, c6, c7 = st.columns([2, 1, 1, 1.2, 1.2])
            ticker = c3.text_input("Bezeichnung / CUSIP", placeholder="z. B. UST 4,25 % 15.11.2029")
            kupon = c4.number_input("Kupon % p.a.", min_value=0.0, max_value=15.0, step=0.125, format="%.3f")
            faell = c5.date_input("Fälligkeit", value=(pd.Timestamp.today() + pd.DateOffset(years=3)).date(), format="DD.MM.YYYY")
            menge = c6.number_input("Nominal USD", min_value=0.0, step=100000.0, format="%.0f")
            kurs = c7.text_input("Kurs in % (leer = aus Zinskurve)")
            sektor, name = "", ""
        begruendung = st.text_area("Begründung (Pflicht) – z. B. Bezug zu Szenario, Signal oder Sektorvotum, mit Quelle",
                                   height=80)
        if st.form_submit_button("Transaktion speichern", type="primary"):
            if not str(ticker).strip() or not menge:
                st.error("Bitte Ticker/Bezeichnung (oben) und Menge angeben.")
                return
            if len(begruendung.strip()) < 10:
                st.error("Bitte eine Begründung angeben (mindestens ein Satz) – sie ist Pflicht.")
                return
            if klasse == "Aktie" and not sektor:
                st.error("Bitte den Sektor wählen.")
                return
            k = zahl(kurs)
            d = pd.Timestamp(datum)
            if k is None and klasse == "Aktie":
                kk, _, _ = lade_kurse((str(ticker).upper(),))
                s = kk.get(str(ticker).upper())
                s = s[s.index <= d] if s is not None else None
                k = float(s.iloc[-1]) if s is not None and len(s) else None
            if k is None and klasse == "Treasury":
                T = max(0.0, (pd.Timestamp(faell) - d).days / 365.25)
                y = kurve_rendite(kurve_am(fred, d), T)
                k = preis(kupon, T, y) if y is not None else None
            if k is None:
                st.error("Kein Kurs gefunden – bitte Kurs von Hand eingeben.")
                return
            ctx.speichern("transaktionen", [dict(zeit=jetzt(), person=ctx.person, id=uuid.uuid4().hex[:10],
                                                 datum=d.date().isoformat(), art=art, klasse=klasse,
                                                 ticker=str(ticker).strip().upper() if klasse == "Aktie" else str(ticker).strip(),
                                                 name=name.strip(), sektor=sektor or "", menge=menge, kurs=round(k, 4),
                                                 kupon=kupon if kupon is not None else "",
                                                 faelligkeit=pd.Timestamp(faell).date().isoformat() if faell else "",
                                                 notiz=begruendung.strip(), storniert="")],
                          f"{art} {str(ticker).strip()} gespeichert (Kurs {ui.de(k, 2)}).")


def zusammenfassung(tabellen: dict, fred: dict, lade_kurse, alloc: dict) -> dict | None:
    """Kurzer Text für Wochenbericht und Morgen-Mail."""
    tx = transaktionen(tabellen.get("transaktionen"))
    if not len(tx):
        return None
    kapital, _ = _einstellungen(tabellen.get("einstellungen"))
    ticker = tuple(sorted(set(tx.loc[tx["klasse"] == "Aktie", "ticker"].str.upper())))
    kurse = lade_kurse(ticker + ("^GSPC",))[0] if ticker else {}
    pos, cash_delta, _ = positionen(tx)
    b = bewerte(pos, kurse, fred, pd.Timestamp.today().normalize())
    cash = kapital + cash_delta
    ak = float(b.loc[b["klasse"] == "Aktie", "wert"].sum()) if len(b) else 0.0
    tr = float(b.loc[b["klasse"] == "Treasury", "wert"].sum()) if len(b) else 0.0
    total = ak + tr + cash + (float(b["zins"].sum()) if len(b) else 0.0)
    r = (total / kapital - 1) * 100
    text = (f"Portfoliowert {ui.de(total, 0)} USD ({ui.de(r, 2, True)} % seit Start). Aktien {ak / total * 100:.1f} %, "
            f"Treasuries {tr / total * 100:.1f} %, Cash {cash / total * 100:.1f} % (Empfehlung {alloc['aktien']:.0f} / "
            f"{alloc['anleihen']:.0f} / {alloc['cash']:.0f}). Duration Treasuries {ui.de(_wdur(b), 1)} J. "
            f"{len(b)} Positionen.")
    return dict(text=text, total=total, rendite=r)
