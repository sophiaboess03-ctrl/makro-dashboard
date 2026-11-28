"""«Was ist eingepreist?» – Markterwartungen aus Renditen, Breakevens, Spreads und Bewertung vs. Team-Sicht (IC-Prüfung 1)."""
from __future__ import annotations

import html

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from makro import config as C
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


QUELLEN = {
    "DFF": "https://fred.stlouisfed.org/series/DFF", "DFEDTARU": "https://fred.stlouisfed.org/series/DFEDTARU",
    "DGS3MO": "https://fred.stlouisfed.org/series/DGS3MO", "DGS2": "https://fred.stlouisfed.org/series/DGS2",
    "DGS10": "https://fred.stlouisfed.org/series/DGS10", "T5YIE": "https://fred.stlouisfed.org/series/T5YIE",
    "T10YIE": "https://fred.stlouisfed.org/series/T10YIE", "T5YIFR": "https://fred.stlouisfed.org/series/T5YIFR",
    "FedWatch": "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html",
    "BAMLH0A1HYBB": "https://fred.stlouisfed.org/series/BAMLH0A1HYBB", "VIXCLS": "https://fred.stlouisfed.org/series/VIXCLS",
}


def _q(*ids):
    return " · ".join(f"[{i}]({QUELLEN[i]})" for i in ids if i in QUELLEN)


def grinold_kroner_eingaben(ctx) -> dict:
    out = {"dp": 1.2, "i": 2.4, "g": 2.0, "ds": -1.0, "dpe": 0.0}
    be = ctx.fred.get("T10YIE")
    if be is not None and len(be.dropna()):
        out["i"] = round(float(be.dropna().iloc[-1]), 2)
    try:
        import analyse
        dy = analyse.fundamentaldaten("SPY").get("div")
        if dy:
            out["dp"] = round(float(dy) * 100, 2)
    except Exception:  # noqa: BLE001
        pass
    kgv = ctx.status.loc["kgv", "wert"] if "kgv" in ctx.status.index else None
    if kgv is not None and not pd.isna(kgv) and kgv > 0:
        rev = ((19.1 / float(kgv)) ** (1 / 5) - 1) * 100
        out["pe_hinweis"] = (f"Zum Vergleich: Kehrt das Forward-KGV ({ui.de(float(kgv), 1)}) über 5 Jahre zum 10-J.-Schnitt von 19,1 zurück, "
                             f"wären das {ui.de(rev, 1, True)} % pro Jahr Bewertungsänderung.")
    einst = ctx.tabellen.get("einstellungen")
    if einst is not None and not einst.empty:
        for _, r in einst.sort_values("zeit").iterrows():
            if str(r["schluessel"]).startswith("gk_"):
                try:
                    out[r["schluessel"][3:]] = float(str(r["wert"]).replace(",", "."))
                except ValueError:
                    pass
    for k in ("dp", "i", "g", "ds", "dpe"):
        out[k] = float(out[k])
    return out


def markt_eintraege(tab: pd.DataFrame | None) -> dict:
    if tab is None or tab.empty:
        return {}
    d = tab.sort_values("zeit").drop_duplicates("groesse", keep="last")
    return {r["groesse"]: r for _, r in d.iterrows()}


def seite_erwartungen(ctx, fred: dict, prog: dict | None = None):
    import seitenpdf as SP
    import anleihen as AN
    from storage import jetzt, zahl

    R = SP.Sammler("Was ist eingepreist?", "Markterwartungen aus Marktpreisen gegen eure Szenarien, mit Quellen", kurz="Eingepreist")
    ui.seitenkopf("Was ist eingepreist?",
                  "Welche Fed-Schritte, Zinsen, Inflation und wie viel Risiko stecken bereits in den Marktpreisen – und wo weicht "
                  "eure gewichtete Szenario-Sicht davon ab? Nur dort lohnt eine Über- oder Untergewichtung. Jede Zahl mit Quelle.")
    ph = st.empty()
    gew = ctx.gew["param"]
    manuell = markt_eintraege(ctx.tabellen.get("markt_erwartung"))
    zus = []

    # --- Fed ---------------------------------------------------------------
    st.subheader("1. Fed-Pfad: was der Geldmarkt erwartet")
    R.h("1. Fed-Pfad")
    fp = fed_pfad(fred)
    markt_txt = "–"
    if fp["basis"] is None or not fp["punkte"]:
        st.info("Zu wenig Zinsdaten (FRED DFF / DGS3MO / DGS1). FRED-Schlüssel in den Secrets hinterlegen.")
    else:
        c1, c2 = st.columns([1.1, 1])
        with c1:
            tab = pd.DataFrame([{
                "Zeitraum": p["laufzeit"], "Impliziter Leitzins (Forward)": _pct(p["forward"]),
                "vs. heute": _bp(p["diff"]), "≈ Schritte à 25 bp": ui.de(p["schritte"], 1, True)} for p in fp["punkte"]])
            st.dataframe(tab, hide_index=True, width="stretch")
            st.caption(f"Leitzins heute (Fed Funds effektiv): {_pct(fp['basis'])}"
                       f"{' · Zielband-Obergrenze ' + _pct(fp['oben']) if fp['oben'] is not None else ''}. "
                       "Näherung aus den Forward-Renditen der Treasury-Kurve (3 Mt., 6 Mt., 1 J., 2 J.). Laufzeitprämie und T-Bill-Angebot "
                       "verzerren – deshalb unten mit CME FedWatch abgleichen.")
            st.markdown("Quellen: " + _q("DFF", "DFEDTARU", "DGS3MO", "DGS2", "FedWatch"))
            R.tabelle(tab)
        with c2:
            fig = go.Figure(go.Bar(x=[p["laufzeit"] for p in fp["punkte"]], y=[p["ende"] for p in fp["punkte"]],
                                   marker_color=[ui.FARBE["Rot"] if p["ende"] > 0 else ui.FARBE["Grün"] for p in fp["punkte"]],
                                   text=[_bp(p["ende"]) for p in fp["punkte"]], textposition="outside"))
            fig.add_hline(y=25, line=dict(color="#94A3B8", dash="dot"), annotation_text="1 Erhöhung")
            fig.add_hline(y=-25, line=dict(color="#94A3B8", dash="dot"), annotation_text="1 Senkung")
            fig.update_layout(height=280, margin=dict(l=8, r=8, t=36, b=8), plot_bgcolor="white",
                              title=dict(text="Impliziter Leitzins vs. heute (bp)", font=dict(size=12)))
            fig.update_yaxes(gridcolor="#EEF0F3")
            ui.zeige(fig)
        erste = fp["punkte"][0]
        markt_txt = f"{_bp(erste['ende'])} in 0–3 Mt. (T-Bills)"
    fw = manuell.get("fedwatch")
    if fw is not None:
        st.markdown(f"**CME FedWatch (Team-Eintrag {html.escape(str(fw['datum']))}, {html.escape(fw['person'])}):** "
                    f"{html.escape(str(fw['wert']))} · Quelle: {html.escape(str(fw['quelle']))}")
        markt_txt = f"FedWatch: {fw['wert']} ({fw['datum']})"
        R.text(f"CME FedWatch (Team-Eintrag {fw['datum']}): {fw['wert']} - Quelle: {fw['quelle']}")
    with st.popover("FedWatch-Wahrscheinlichkeit eintragen", icon=":material/edit:", disabled=ctx.nur_lesen):
        with st.form("fedwatch", clear_on_submit=True):
            wert = st.text_input("Wahrscheinlichkeiten nächste Sitzung", placeholder="z. B. Erhöhung 25 bp: 68 %, unverändert: 32 %")
            datum = st.date_input("Stand")
            quelle = st.text_input("Quelle", value="CME FedWatch Tool")
            if st.form_submit_button("Speichern") and wert.strip():
                ctx.speichern("markt_erwartung", [dict(zeit=jetzt(), person=ctx.person, groesse="fedwatch", wert=wert.strip(),
                                                       quelle=quelle.strip(), datum=datum.strftime("%d.%m.%Y"))], "FedWatch gespeichert.")
    tr = None
    try:
        from makro import szenarien as SZ
        tr = SZ.taylor(ctx.status)
    except Exception:  # noqa: BLE001
        pass
    if tr:
        t = (f"**Taylor-Regel** (CFA Level III, CME): r* {ui.de(C.TAYLOR_R_NEUTRAL, 1)} % + Inflation {ui.de(tr['pi'], 1)} % (Kern-PCE) + "
             f"0,5 × Inflationslücke + 0,5 × Output Gap ({ui.de(tr['gap'], 1)} %) = **{ui.de(tr['i'], 2)} %** gegenüber Fed-Obergrenze "
             f"{ui.de(tr['fed'], 2)} % → " + ("die Regel verlangt eine **straffere** Fed (Druck auf Erhöhungen)." if tr["diff"] > 0.25 else
                                              "die Regel verlangt eine **lockerere** Fed (Raum für Senkungen)." if tr["diff"] < -0.25 else
                                              "Fed ungefähr dort, wo die Regel sie sieht."))
        st.markdown(t)
        st.caption(f"Neutraler Realzins r* = {ui.de(C.TAYLOR_R_NEUTRAL, 1)} % ist eine Annahme (Laubach-Williams-Schätzung der NY Fed prüfen); "
                   "Daten: FRED PCEPILFE, GDPC1/GDPPOT, DFEDTARU.")
        R.text(t)
    if prog and 4 in prog["fragen"]:
        q = prog["fragen"][4]
        opts = [o[0] for o in C.FRAGE_BY_NR[4]["optionen"]]
        st.markdown("**Eure Sicht (Szenarien):** " + " · ".join(f"{o} {p:.0f} %" for o, p in zip(opts, q["probs"])) +
                    f" · gewichtet {ui.de(gew['fed'], 0, True)} bp")
        zus.append(("Fed (nächste Sitzung)", " · ".join(f"{o} {p:.0f} %" for o, p in zip(opts, q["probs"])), markt_txt,
                    "FRED DFF/DGS3MO, CME FedWatch"))
    else:
        zus.append(("Fed", f"gewichtet {ui.de(gew['fed'], 0, True)} bp", markt_txt, "FRED DFF/DGS3MO, CME FedWatch"))

    # --- Zinsen ------------------------------------------------------------
    st.subheader("2. Renditen: Forward gegen eure Erwartung")
    R.h("2. Renditen")
    k = AN.kurve(fred)
    if len(k) >= 5:
        zeilen = []
        for T, pid in ((2.0, "dgs2"), (10.0, "dgs10")):
            kz = AN.laufzeit_kz(k, T)
            zeilen.append({"Laufzeit": f"{T:g} J.", "Rendite heute": _pct(kz["y"]), "Markt preist ein (1 Mt.)": _bp(kz["fwd_dy"]),
                           "Eure Erwartung (gewichtet)": _bp(gew[pid]), "Differenz": _bp(gew[pid] - kz["fwd_dy"])})
            zus.append((f"Rendite {T:g} J. (1 Mt.)", _bp(gew[pid]), _bp(kz["fwd_dy"]), f"FRED DGS{T:g}, Forward aus Kurve"))
        tab = pd.DataFrame(zeilen)
        st.dataframe(tab, hide_index=True, width="stretch")
        st.caption("Markt = Forward-Rendite in einem Monat aus der heutigen Kurve minus heutige Rendite. Liegt eure Erwartung tiefer, "
                   "seid ihr bullisher als der Markt (längere Duration lohnt). Details: «Analyse → Anleihen & Cash». Quellen: " + _q("DGS2", "DGS10"))
        R.tabelle(tab)

    # --- Inflation ---------------------------------------------------------
    st.subheader("3. Inflation: Breakevens gegen eure Annahme")
    R.h("3. Inflation")
    be5, _ = _letzt(fred, "T5YIE")
    be10, _ = _letzt(fred, "T10YIE")
    ff, _ = _letzt(fred, "T5YIFR")
    kpce = ctx.status.loc["kernpce", "wert"] if "kernpce" in ctx.status.index else None
    cpi_j = gew["cpi_mm"] * 12
    kp = [("5-J.-Breakeven", _pct(be5), "FRED T5YIE"), ("10-J.-Breakeven", _pct(be10), "FRED T10YIE"),
          ("5y5y", _pct(ff), "FRED T5YIFR"), ("Eure CPI-Erwartung", f"{ui.de(cpi_j, 1)} % ann.", f"gewichtet {ui.de(gew['cpi_mm'], 2)} % m/m")]
    ui.kopf_kpis(kp)
    R.kpis(kp)
    if be5 is not None:
        t = (f"Der Markt preist über 5 Jahre im Schnitt **{_pct(be5)}** CPI-Inflation ein. Eure gewichtete Monatsrate entspricht "
             f"≈ **{ui.de(cpi_j, 1)} %** annualisiert (Kern-PCE heute {_pct(kpce, 1) if kpce is not None and not pd.isna(kpce) else '–'}). " +
             ("Ihr erwartet mehr Inflation als eingepreist → TIPS relativ günstig." if cpi_j > be5 + 0.3 else
              "Ihr erwartet weniger Inflation als eingepreist → nominale Treasuries relativ attraktiv." if cpi_j < be5 - 0.3 else
              "Eure Sicht liegt nahe am Markt → TIPS ohne klaren Bewertungsvorteil."))
        st.markdown(t + " Quellen: " + _q("T5YIE", "T10YIE", "T5YIFR"))
        R.text(t)
        zus.append(("Inflation", f"≈ {ui.de(cpi_j, 1)} % annualisiert", f"5-J.-Breakeven {_pct(be5)}", "FRED T5YIE"))

    # --- Risiko & Bewertung ------------------------------------------------
    st.subheader("4. Risiko und Bewertung: wie viel Sorglosigkeit ist eingepreist?")
    R.h("4. Risiko und Bewertung")
    zeilen = []
    for name, ind_id in (("BB-Spread", "bb"), ("BBB-Spread", "bbb"), ("CCC-Spread", "ccc"), ("VIX", "vix"),
                         ("Aktienrisikoprämie (Fwd)", "erp"), ("Realzins 10 J.", "realzins"), ("Laufzeitprämie 10 J.", "laufzeitpraemie")):
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
        ind = C.IND_BY_ID[ind_id]
        zeilen.append({"Grösse": name, "Aktuell": ctx.status.loc[ind_id, "anzeige"], "5-J.-Perzentil": f"{p:.0f}.",
                       "Einordnung": einordnung, "Quelle": ind.get("quelle1", "")})
    if zeilen:
        tab = pd.DataFrame(zeilen)
        st.dataframe(tab, hide_index=True, width="stretch")
        st.caption("Perzentil = Anteil der letzten 5 Jahre mit tieferem Wert. Tiefe Spreads, tiefer VIX und eine tiefe Risikoprämie "
                   "heissen: Der Markt ist wenig für schlechte Nachrichten entschädigt.")
        R.tabelle(tab)
        p_bear = ctx.szen.loc["ic_bear", "p"] if "ic_bear" in ctx.szen.index else None
        vix_z = next((z for z in zeilen if z["Grösse"] == "VIX"), None)
        if vix_z is not None and p_bear is not None:
            zus.append(("Risiko (Bear-Szenario)", f"{p_bear:.0f} % Wahrscheinlichkeit", f"VIX {vix_z['Aktuell']} ({vix_z['5-J.-Perzentil']} Perzentil)",
                        "FRED VIXCLS"))
        erp = next((z for z in zeilen if z["Grösse"].startswith("Aktienrisiko")), None)
        if erp:
            zus.append(("Aktien (1 Mt.)", f"S&P 500 gewichtet {ui.de(gew['sp500'], 1, True)} %",
                        f"ERP {erp['Aktuell']} ({erp['5-J.-Perzentil']} Perzentil)", "Forward-EPS (Team), FRED SP500/DGS10"))

    # --- Gewinne -----------------------------------------------------------
    st.subheader("5. Gewinne: bewegen sich die Schätzungen?")
    eps = ctx.daten.reihe("fwd_eps")
    if len(eps) >= 2:
        e = eps["wert"]
        vier = e[e.index <= e.index[-1] - pd.Timedelta(days=28)]
        ref = float(vier.iloc[-1]) if len(vier) else float(e.iloc[0])
        ver = (float(e.iloc[-1]) / ref - 1) * 100
        t = (f"Forward-EPS {ui.de(float(e.iloc[-1]), 2)} USD · Veränderung seit {ui.datum_txt(vier.index[-1] if len(vier) else e.index[0])}: "
             f"**{ui.de(ver, 2, True)} %**. " + ("Steigende Schätzungen stützen die Aktien-These." if ver > 0.3 else
                                                 "Sinkende Schätzungen schwächen die Aktien-These." if ver < -0.3 else "Schätzungen stabil.") +
             " Quelle: Team-Einträge (FactSet Earnings Insight).")
        st.markdown(t)
        R.h("5. Gewinne")
        R.text(t)
    else:
        st.info("Für den Trend der Gewinnschätzungen braucht es mindestens zwei Einträge «Forward-EPS» (Seite «Daten eintragen», "
                "z. B. wöchentlich aus FactSet Earnings Insight).")

    # --- Grinold-Kroner ---------------------------------------------------------
    st.subheader("6. Aktien langfristig: Grinold-Kroner-Modell")
    R.h("6. Grinold-Kroner")
    gk = grinold_kroner_eingaben(ctx)
    with st.form("gk"):
        cc = st.columns(5)
        dp = cc[0].number_input("Dividendenrendite D/P (%)", 0.0, 5.0, gk["dp"], 0.1, format="%.3f")
        i_ = cc[1].number_input("Inflation i (%)", 0.0, 6.0, gk["i"], 0.1, format="%.3f", help="Standard: 10-J.-Breakeven (FRED T10YIE)")
        g = cc[2].number_input("Reales Gewinnwachstum g (%)", -2.0, 6.0, gk["g"], 0.1, format="%.3f")
        ds = cc[3].number_input("Veränderung Aktienzahl ΔS (%)", -4.0, 4.0, gk["ds"], 0.1, format="%.3f", help="negativ = Netto-Aktienrückkäufe")
        dpe = cc[4].number_input("Bewertungsänderung ΔP/E p.a. (%)", -5.0, 5.0, gk["dpe"], 0.1, format="%.3f")
        if st.form_submit_button("Annahmen fürs Team speichern", disabled=ctx.nur_lesen):
            ctx.speichern("einstellungen", [dict(zeit=jetzt(), person=ctx.person, schluessel=f"gk_{k}", wert=v)
                                            for k, v in (("dp", dp), ("i", i_), ("g", g), ("ds", ds), ("dpe", dpe))],
                          "Grinold-Kroner-Annahmen gespeichert.")
    er = dp + i_ + g - ds + dpe
    y10 = ctx.status.loc["dgs10", "wert"] if "dgs10" in ctx.status.index else None
    t = (f"E(R) = D/P + (i + g) − ΔS + ΔP/E = {ui.de(dp, 1)} + ({ui.de(i_, 1)} + {ui.de(g, 1)}) − ({ui.de(ds, 1)}) + {ui.de(dpe, 1)} = "
         f"**{ui.de(er, 1)} % p.a.** ≈ {ui.de(er / 12, 2)} % pro Monat. " +
         (f"Gegenüber der 10-J.-Rendite ({ui.de(y10, 2)} %) ergibt das eine erwartete Aktienrisikoprämie von **{ui.de(er - y10, 1, True)} PP**. "
          if y10 is not None and not pd.isna(y10) else "") +
         f"Euer gewichteter Szenario-Wert für den S&P 500: {ui.de(gew['sp500'], 2, True)} % im Monat.")
    st.markdown(t)
    if gk.get("pe_hinweis"):
        st.caption(gk["pe_hinweis"])
    st.caption("Grinold-Kroner (CFA Level III, CME Part 2) ist ein langfristiges Modell – es kalibriert die Grössenordnung, nicht den "
               "einzelnen Monat. Quellen: Dividendenrendite Yahoo (SPY) oder Annahme, Inflation FRED T10YIE, Rest Team-Annahmen.")
    R.text(t)
    zus.append(("Aktien langfristig (Grinold-Kroner)", f"{ui.de(er, 1)} % p.a.", f"10 J. {ui.de(y10, 2)} %" if y10 is not None else "–",
                "Yahoo SPY, FRED T10YIE, Team"))

    # --- Zusammenfassung ---------------------------------------------------
    st.subheader("Eure Sicht gegen den Markt")
    if zus:
        tab = pd.DataFrame(zus, columns=["Thema", "Eure Sicht (Szenarien, gewichtet)", "Markt preist ein", "Quelle"])
        st.dataframe(tab, hide_index=True, width="stretch")
        R.h("Eure Sicht gegen den Markt")
        R.tabelle(tab, [36, 52, 52, 40])
    st.caption("Eure Sicht kommt live aus der Seite «Szenarien» – ändert ihr dort Werte oder Wahrscheinlichkeiten, ändert sich diese "
               "Tabelle. Entscheidend ist nicht, ob eure Sicht stimmt, sondern ob sie vom Markt abweicht.")
    SP.knopf(ph, R, "eingepreist")
