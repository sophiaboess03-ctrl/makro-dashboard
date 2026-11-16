"""«Was ist eingepreist?» – Markterwartungen aus Renditen, Breakevens, Spreads und Bewertung vs. Team-Sicht (IC-Prüfung 1)."""
from __future__ import annotations

import html

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from makro import ui


def _letzt(fred, serie):
    s = fred.get(serie)
    if s is None or s.dropna().empty:
        return None, None
    s = s.dropna()
    return float(s.iloc[-1]), s.index[-1]


def _perzentil(s: pd.Series | None, jahre: int = 5):
    if s is None or s.dropna().empty:
        return None
    s = s.dropna()
    s = s[s.index >= s.index[-1] - pd.DateOffset(years=jahre)]
    return float((s < s.iloc[-1]).mean() * 100)


def _bp(x):
    return "–" if x is None else f"{ui.de(x, 0, True)} bp"


def _pct(x, d=2):
    return "–" if x is None else f"{ui.de(x, d)} %"


def fed_pfad(fred: dict) -> dict:
    """Implizite Leitzinsen je Zeitabschnitt aus den Forward-Renditen der Treasury-Kurve (Näherung)."""
    effr, d_effr = _letzt(fred, "DFF")
    oben, _ = _letzt(fred, "DFEDTARU")
    basis = effr if effr is not None else (oben - 0.125 if oben is not None else None)
    out = dict(basis=basis, datum=d_effr, oben=oben, punkte=[])
    kurve = []
    for serie, t in (("DGS3MO", 0.25), ("DGS6MO", 0.5), ("DGS1", 1.0), ("DGS2", 2.0)):
        y, _ = _letzt(fred, serie)
        if y is not None:
            kurve.append((t, y))
    if basis is None or not kurve:
        return out
    t0, y0 = 0.0, None
    for t, y in kurve:
        fwd = y if y0 is None else (y * t - y0 * t0) / (t - t0)  # Forward-Rendite für den Abschnitt (t0, t]
        name = {0.25: "0–3 Mt.", 0.5: "3–6 Mt.", 1.0: "6–12 Mt.", 2.0: "1–2 J."}[t]
        diff = (fwd - basis) * 100
        out["punkte"].append(dict(laufzeit=name, monate=int(t * 12), rendite=y, forward=fwd, diff=diff, ende=diff,
                                  schritte=diff / 25))
        t0, y0 = t, y
    return out


def seite_erwartungen(ctx, fred: dict):
    st.title("🎯 Was ist eingepreist?")
    st.caption("IC-Prüfung 1: Welche Fed-Schritte, welche Inflation und wie viel Risiko sind in den Marktpreisen bereits "
               "enthalten – und wo weicht eure Einschätzung davon ab? Alles automatisch aus FRED- und Marktdaten.")

    # --- Fed ---------------------------------------------------------------
    st.subheader("1. Fed-Pfad: was der Geldmarkt erwartet")
    fp = fed_pfad(fred)
    if fp["basis"] is None or not fp["punkte"]:
        st.info("Zu wenig Zinsdaten (FRED DFF / DGS3MO / DGS1). FRED-Schlüssel in den Secrets hinterlegen.")
    else:
        c1, c2 = st.columns([1.1, 1])
        with c1:
            st.dataframe(pd.DataFrame([{
                "Zeitraum": p["laufzeit"], "Impliziter Leitzins (Forward)": _pct(p["forward"]),
                "vs. heute": _bp(p["diff"]), "≈ Schritte à 25 bp": ui.de(p["schritte"], 1, True)}
                for p in fp["punkte"]]), hide_index=True, width="stretch")
            st.caption(f"Leitzins heute (Fed Funds effektiv): {_pct(fp['basis'])}"
                       f"{' · Zielband-Obergrenze ' + _pct(fp['oben']) if fp['oben'] is not None else ''}. "
                       "Näherung: Aus den Treasury-Renditen (3 Mt., 6 Mt., 1 J., 2 J.) werden Forward-Renditen je Zeitabschnitt "
                       "berechnet ≈ erwarteter Leitzins in diesem Abschnitt. Laufzeitprämie und T-Bill-Angebot verzerren, "
                       "v. a. bei 1–2 J. – für exakte Werte CME FedWatch prüfen.")
        with c2:
            fig = go.Figure(go.Bar(x=[p["laufzeit"] for p in fp["punkte"]], y=[p["ende"] for p in fp["punkte"]],
                                   marker_color=[ui.FARBE["Rot"] if p["ende"] > 0 else ui.FARBE["Grün"] for p in fp["punkte"]],
                                   text=[_bp(p["ende"]) for p in fp["punkte"]], textposition="outside"))
            fig.add_hline(y=25, line=dict(color="#94A3B8", dash="dot"), annotation_text="1 Erhöhung")
            fig.add_hline(y=-25, line=dict(color="#94A3B8", dash="dot"), annotation_text="1 Senkung")
            fig.update_layout(height=280, margin=dict(l=8, r=8, t=36, b=8), plot_bgcolor="white",
                              title=dict(text="Impliziter Leitzins vs. heute (bp)", font=dict(size=12)))
            fig.update_yaxes(gridcolor="#EEF0F3")
            st.plotly_chart(fig, width="stretch", config=ui.PLOT_CFG)
        sechs = next((p for p in fp["punkte"] if p["monate"] == 6), fp["punkte"][0])  # Abschnitt 3–6 Mt.
        zins = ctx.szen[ctx.szen["thema"] == "zinsen"]
        p_hawk = float(zins.loc["z_basis", "p"] + zins.loc["z_infl", "p"]) if "z_basis" in zins.index else None
        markt_txt = (f"≈ {ui.de(sechs['schritte'], 1)} Erhöhung(en) in 3–6 Mt." if sechs["ende"] > 12 else
                     f"≈ {ui.de(-sechs['schritte'], 1)} Senkung(en) in 3–6 Mt." if sechs["ende"] < -12 else
                     "keine Veränderung in 3–6 Mt.")
        if p_hawk is not None:
            if p_hawk >= 60 and sechs["ende"] < 12:
                fazit = "Ihr seid **hawkisher als der Markt**: Bestätigt sich eure Sicht, steigen kurze Renditen noch → kurze Duration bevorzugen."
            elif p_hawk < 40 and sechs["ende"] > 12:
                fazit = "Ihr seid **dovisher als der Markt**: Bestätigt sich eure Sicht, fallen Renditen → längere Duration lohnt."
            else:
                fazit = "Eure Sicht und der Markt liegen **ungefähr auf einer Linie** – der Zinspfad bringt wenig Überraschungspotenzial."
            st.markdown(f"**Eure Sicht:** Hawkishe Fed + Inflationsschock zusammen {p_hawk:.0f} % (Themen-Szenarien) · "
                        f"**Markt:** {markt_txt}  \n{fazit}")

    # --- Inflation ---------------------------------------------------------
    st.subheader("2. Inflation: Breakevens vs. eure Annahme")
    be5, _ = _letzt(fred, "T5YIE")
    be10, _ = _letzt(fred, "T10YIE")
    ff, _ = _letzt(fred, "T5YIFR")
    kpce = ctx.status.loc["kernpce", "wert"] if "kernpce" in ctx.status.index else None
    c = st.columns(4)
    c[0].metric("5-J.-Breakeven", _pct(be5))
    c[1].metric("10-J.-Breakeven", _pct(be10))
    c[2].metric("5y5y", _pct(ff))
    c[3].metric("Kern-PCE heute", _pct(kpce, 1) if kpce is not None and not pd.isna(kpce) else "–")
    if be5 is not None:
        st.markdown(f"Der Markt preist über 5 Jahre im Schnitt **{_pct(be5)}** CPI-Inflation ein. Euer Basisszenario rechnet mit "
                    "Kern-PCE um **3 %** bis Februar. " +
                    ("Liegt eure Erwartung **über** dem Breakeven, sind TIPS relativ günstig – das stützt euren TIPS-Anteil."
                     if be5 < 2.9 else "Der Markt erwartet ähnlich viel Inflation wie ihr – TIPS bringen dann keinen Bewertungsvorteil."))

    # --- Risiko & Bewertung ------------------------------------------------
    st.subheader("3. Risiko und Bewertung: wie viel Sorglosigkeit ist eingepreist?")
    zeilen = []
    for name, ind_id, hoch_gut in (("BB-Spread", "bb", False), ("BBB-Spread", "bbb", False), ("CCC-Spread", "ccc", False),
                                   ("VIX", "vix", False), ("Aktienrisikoprämie (Fwd)", "erp", False),
                                   ("Realzins 10 J.", "realzins", None), ("Laufzeitprämie 10 J.", "laufzeitpraemie", None)):
        df = ctx.daten.reihe(ind_id)
        if df.empty:
            continue
        p = _perzentil(df["wert"])
        if p is None:
            continue
        if ind_id in ("bb", "bbb", "ccc", "vix", "erp"):
            einordnung = ("sehr tief → Markt preist kaum Risiko ein" if p < 15 else "tief" if p < 35 else
                          "normal" if p < 65 else "hoch → Stress eingepreist" if p < 85 else "sehr hoch → viel Angst eingepreist")
        else:
            einordnung = "tief" if p < 35 else "normal" if p < 65 else "hoch"
        zeilen.append({"Grösse": name, "Aktuell": ctx.status.loc[ind_id, "anzeige"], "5-J.-Perzentil": f"{p:.0f}.",
                       "Einordnung": einordnung})
    if zeilen:
        st.dataframe(pd.DataFrame(zeilen), hide_index=True, width="stretch")
        st.caption("Perzentil = Anteil der letzten 5 Jahre mit tieferem Wert. Tiefe Spreads, tiefer VIX und eine tiefe "
                   "Aktienrisikoprämie heissen: Der Markt ist wenig für schlechte Nachrichten entschädigt (euer Bear-Case "
                   "ist kaum eingepreist).")

    # --- Gewinne -----------------------------------------------------------
    st.subheader("4. Gewinne: bewegen sich die Schätzungen?")
    eps = ctx.daten.reihe("fwd_eps")
    if len(eps) >= 2:
        e = eps["wert"]
        vier = e[e.index <= e.index[-1] - pd.Timedelta(days=28)]
        ref = float(vier.iloc[-1]) if len(vier) else float(e.iloc[0])
        ver = (float(e.iloc[-1]) / ref - 1) * 100
        st.markdown(f"Forward-EPS {ui.de(float(e.iloc[-1]), 2)} USD · Veränderung seit {ui.datum_txt(vier.index[-1] if len(vier) else e.index[0])}: "
                    f"**{ui.de(ver, 2, True)} %**. " +
                    ("Steigende Schätzungen stützen die Aktien-These." if ver > 0.3 else
                     "Sinkende Schätzungen schwächen die Aktien-These (IC-Monitoring)." if ver < -0.3 else "Schätzungen stabil."))
    else:
        st.info("Für den Trend der Gewinnschätzungen braucht es mindestens zwei Einträge «Forward-EPS» (Seite «Daten eintragen», "
                "z. B. wöchentlich aus FactSet Earnings Insight).")

    # --- Zusammenfassung ---------------------------------------------------
    st.subheader("Unsere Sicht vs. Markt")
    zus = []
    if fp["punkte"]:
        zus.append(("Fed / Zinsen", "Hawkish (Basis 55 %)", markt_txt if fp["basis"] is not None else "–"))
    if be5 is not None:
        zus.append(("Inflation", "Kern-PCE ≈ 3 % bis Feb.", f"5-J.-Breakeven {_pct(be5)}"))
    bb = next((z for z in zeilen if z["Grösse"] == "BB-Spread"), None)
    if bb:
        zus.append(("Kreditrisiko", "Risk-off 20 %, Stress nur CCC", f"BB-Spread {bb['Aktuell']} ({bb['5-J.-Perzentil']} Perzentil)"))
    erp = next((z for z in zeilen if z["Grösse"].startswith("Aktienrisiko")), None)
    if erp:
        zus.append(("Aktienbewertung", "Fair, aber kaum Prämie", f"ERP {erp['Aktuell']} ({erp['5-J.-Perzentil']} Perzentil)"))
    if zus:
        st.dataframe(pd.DataFrame(zus, columns=["Thema", "Eure Sicht (IC)", "Markt preist ein"]), hide_index=True, width="stretch")
    st.caption("Entscheidend ist nicht, ob eure Sicht stimmt, sondern ob sie vom Markt abweicht: Nur dort lohnt eine "
               "Über- oder Untergewichtung.")
