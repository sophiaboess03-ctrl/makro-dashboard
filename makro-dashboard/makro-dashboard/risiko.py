"""Risiko & Risikobudget der Taktik – nach dem CFA-Curriculum (Level III Asset Allocation / Level II–III Market Risk).

Grundlage ist dieselbe Szenario-Simulation wie bei den Dozentenfragen (Monatswerte je Szenario + historische Streuung).
Für jede Ziehung wird die Rendite der Taktik und der Benchmark des Dozenten (50 % S&P 500 / 50 % Treasuries) berechnet:

  Aktive Rendite = Asset Allocation  (a − 50 %) × S&P + (b − 50 %) × Anleihen-Benchmark + c × Cash
                 + Sektor-Wetten      a × Σ (Sektorgewicht in den Aktien − S&P-Gewicht) × Sektor-Mehrrendite
                 + Duration & Kurve   b × (Rendite eurer Anleihen − Rendite der Anleihen-Benchmark)

Tracking Error (ex ante) = Streuung der aktiven Rendite. Risikobudget = Anteil jedes Entscheids am Tracking Error
(Euler-Zerlegung: Kovarianz des Teils mit der aktiven Rendite / Varianz der aktiven Rendite).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import anleihen as AN
import seitenpdf as SP
from makro import config as C
from makro import szenarien as SZ
from makro import ui
from storage import jetzt, zahl

TEILE = ["Asset Allocation", "Sektor-Wetten", "Duration & Kurve"]
BUDGET_START = {"Asset Allocation": 50.0, "Sektor-Wetten": 30.0, "Duration & Kurve": 20.0}


def _anleihe(kurve: dict, dauer: float) -> tuple[float, float, float]:
    """Laufzeit T mit Modified Duration ≈ dauer auf der heutigen Kurve; gibt (T, Rendite, Duration)."""
    Ts = [t for t in C.LAUFZEITEN if t >= 0.25]
    Ds = [AN.laufzeit_kz(kurve, t)["D"] for t in Ts]
    T = float(np.interp(dauer, Ds, Ts))
    kz = AN.laufzeit_kz(kurve, T)
    return T, kz["y"], kz["D"]


@st.cache_data(ttl=3600, show_spinner=False, max_entries=30)
def _berechne(defs_json, p_json, sig_json, cpi_json, bond_json, taktik_json, kurve_json) -> dict:
    defs = json.loads(defs_json)
    szen = pd.DataFrame({"p": json.loads(p_json)})
    sig = {k: tuple(v) for k, v in json.loads(sig_json).items()}
    cpi = json.loads(cpi_json) or None
    if cpi:
        cpi["daten_monat"] = pd.Timestamp(cpi["daten_monat"])
    bond = json.loads(bond_json)
    t = json.loads(taktik_json)
    kurve = {float(k): v for k, v in json.loads(kurve_json).items()}
    sim = SZ.simuliere(defs, szen, sig, cpi, bond, n=20000, seed=11)
    a, b, c = t["aktien"] / 100, t["anleihen"] / 100, t["cash"] / 100
    cash_m = bond.get("cash", 4.0) / 12
    # Anleihen der Taktik: Bullet mit der gewählten Duration
    if kurve:
        T_p, y_p, D_p = _anleihe(kurve, t["duration"])
    else:
        T_p, y_p, D_p = bond["laufzeit"], bond["rendite"], t["duration"]
    w = min(max((T_p - 2) / 8, 0), 1.2)
    dy_p = sim["dgs2"] + (sim["dgs10"] - sim["dgs2"]) * w
    r_anl_p = y_p / 12 - D_p * dy_p / 100
    r_anl_b = sim["anleihen"]
    # Sektor-Wetten innerhalb der Aktien
    sek_akt = {s: (t["sektoren"].get(s, 0) / t["aktien"] * 100 if t["aktien"] else 0) - C.SP500_GEWICHT[s] for s in C.SEKTOREN}
    S = sum(sek_akt[s] / 100 * sim[f"sek:{s}"] for s in C.SEKTOREN)
    teile = pd.DataFrame({
        "Asset Allocation": (a - 0.5) * sim["sp500"] + (b - 0.5) * r_anl_b + c * cash_m,
        "Sektor-Wetten": a * S,
        "Duration & Kurve": b * (r_anl_p - r_anl_b),
    })
    aktiv = teile.sum(axis=1)
    r_b = 0.5 * sim["sp500"] + 0.5 * r_anl_b
    r_p = r_b + aktiv
    d = pd.DataFrame({"szenario": sim["szenario"], "r_p": r_p, "r_b": r_b, "aktiv": aktiv, "sp500": sim["sp500"],
                      "anl_p": r_anl_p, "anl_b": r_anl_b, "dgs10": sim["dgs10"]})
    d = pd.concat([d, teile], axis=1)
    # 3 Monate (Saison): Szenario bleibt, Streuung innerhalb wächst mit √3
    for col in ["r_p", "r_b", "aktiv"] + TEILE:
        m = d.groupby("szenario")[col].transform("mean")
        d[f"{col}_3m"] = 3 * m + np.sqrt(3) * (d[col] - m)
    var_akt = float(d["aktiv"].var())
    beitrag = {k: float(np.cov(d[k], d["aktiv"])[0, 1] / var_akt) if var_akt > 0 else 0 for k in TEILE}
    te = float(d["aktiv"].std() * np.sqrt(12))
    out = dict(
        e_p=float(d["r_p"].mean()), e_b=float(d["r_b"].mean()), e_akt=float(d["aktiv"].mean()),
        vola=float(d["r_p"].std() * np.sqrt(12)), vola_b=float(d["r_b"].std() * np.sqrt(12)), te=te,
        ir=float(d["aktiv"].mean() * 12 / te) if te > 0 else np.nan,
        var95=float(-d["r_p"].quantile(0.05)), es95=float(-d.loc[d["r_p"] <= d["r_p"].quantile(0.05), "r_p"].mean()),
        p_verlust=float((d["r_p"] < 0).mean() * 100),
        e3=float(d["r_p_3m"].mean()), s3=float(d["r_p_3m"].std()), e3_b=float(d["r_b_3m"].mean()),
        var95_3=float(-d["r_p_3m"].quantile(0.05)), es95_3=float(-d.loc[d["r_p_3m"] <= d["r_p_3m"].quantile(0.05), "r_p_3m"].mean()),
        akt3_q05=float(d["aktiv_3m"].quantile(0.05)), akt3=float(d["aktiv_3m"].mean()),
        beitrag=beitrag, te_teil={k: float(d[k].std() * np.sqrt(12)) for k in TEILE},
        e_teil={k: float(d[k].mean()) for k in TEILE},
        szen={s: dict(r_p=float(g["r_p_3m"].mean()), r_b=float(g["r_b_3m"].mean()), aktiv=float(g["aktiv_3m"].mean()),
                      **{k: float(g[f"{k}_3m"].mean()) for k in TEILE}) for s, g in d.groupby("szenario")},
        anl=dict(T=T_p, y=y_p, D=D_p), sek_akt=sek_akt,
        bear_anl_3m=float(d.loc[d["szenario"] == "ic_bear", "anl_p"].mean() * 3) if (d["szenario"] == "ic_bear").any() else None,
        cash_m=cash_m,
        verteilung=np.histogram(d["r_p_3m"], bins=40)[0].tolist(), kanten=np.histogram(d["r_p_3m"], bins=40)[1].tolist(),
    )
    return out


def taktik_werte(ctx, werte: dict | None = None) -> dict:
    a = ctx.alloc
    if werte is None:
        sek = {r["sektor"]: r["gewicht"] for _, r in a["sektoren"].iterrows()}
        werte = dict(aktien=a["aktien"], anleihen=a["anleihen"], cash=a["cash"], sektoren=sek, duration=a["duration"])
    return werte


def berechne(ctx, werte: dict | None = None) -> dict | None:
    import dozent

    t = taktik_werte(ctx, werte)
    defs = {sid: dict(param=s["param"], sektoren=s["sektoren"]) for sid, s in ctx.szdef.items()}
    mid = ctx.alloc.get("monat") or C.MONATE[0]["id"]
    cpi = SZ.cpi_basis(ctx.fred, mid)
    cpi_j = json.dumps({**cpi, "daten_monat": str(cpi["daten_monat"])} if cpi else {}, default=str)
    try:
        k = AN.kurve(ctx.fred)
    except Exception:  # noqa: BLE001
        k = {}
    try:
        return _berechne(json.dumps(defs, sort_keys=True), json.dumps({s: float(ctx.szen.loc[s, "p"]) for s in defs}),
                         json.dumps(ctx.sig), cpi_j, json.dumps(ctx.bond, default=float),
                         json.dumps(t, sort_keys=True, default=float), json.dumps({str(x): y for x, y in k.items()}))
    except Exception as e:  # noqa: BLE001
        st.warning(f"Risikoberechnung nicht möglich: {e}")
        return None


# ---------------------------------------------------------------------------
# Historische Stressmonate und Sensitivitäten (aus den Kursdaten)
# ---------------------------------------------------------------------------
def _mon(s):
    return s.dropna().resample("ME").last().pct_change().dropna() * 100 if s is not None and len(s.dropna()) > 40 else None


def historische_stresstests(hist: dict, t: dict, d_bench: float, n: int = 3) -> pd.DataFrame:
    sp, govt = _mon(hist.get("^SP500TR")), _mon(hist.get("GOVT"))
    if sp is None or govt is None:
        return pd.DataFrame()
    sek = {s: _mon(hist.get(C.SEKTOR_ETF[s])) for s in C.SEKTOREN}
    spy = _mon(hist.get("SPY"))
    df = pd.concat({"sp": sp, "govt": govt}, axis=1).dropna()
    wahl = list(df["sp"].nsmallest(n).index) + [i for i in df["govt"].nsmallest(n).index]
    zeilen = []
    for m in dict.fromkeys(wahl):
        r_sp, r_g = float(df.loc[m, "sp"]), float(df.loc[m, "govt"])
        r_anl_p = r_g * (t["duration"] / d_bench) if d_bench else r_g  # gleiche Renditebewegung, andere Duration
        S = 0.0
        if spy is not None and all(sek[s] is not None and m in sek[s].index for s in C.SEKTOREN) and m in spy.index:
            akt = {s: (t["sektoren"].get(s, 0) / t["aktien"] * 100 if t["aktien"] else 0) - C.SP500_GEWICHT[s] for s in C.SEKTOREN}
            S = sum(akt[s] / 100 * (float(sek[s].loc[m]) - float(spy.loc[m])) for s in C.SEKTOREN)
        a, b = t["aktien"] / 100, t["anleihen"] / 100
        r_p = a * (r_sp + S) + b * r_anl_p + t["cash"] / 100 * 0.35
        r_b = 0.5 * r_sp + 0.5 * r_g
        zeilen.append(dict(Monat=m.strftime("%m/%Y"), Auslöser="schwacher Aktienmonat" if m in df["sp"].nsmallest(n).index else "schwacher Anleihenmonat",
                           sp=r_sp, govt=r_g, r_p=r_p, r_b=r_b, aktiv=r_p - r_b))
    return pd.DataFrame(zeilen)


def sektor_betas(hist: dict) -> dict:
    spy = hist.get("SPY")
    if spy is None:
        return {}
    out = {}
    for s, etf in C.SEKTOR_ETF.items():
        x = hist.get(etf)
        if x is None:
            continue
        r = pd.concat([x.pct_change(), spy.pct_change()], axis=1).dropna().tail(756)
        if len(r) > 100 and r.iloc[:, 1].var() > 0:
            out[s] = float(np.clip(r.cov().iloc[0, 1] / r.iloc[:, 1].var(), 0.4, 1.8))  # Ausreisser begrenzen
    return out


def kennzahlen_aktienteil(t: dict, betas: dict) -> dict:
    if not t["aktien"]:
        return {}
    w = {s: t["sektoren"].get(s, 0) / t["aktien"] for s in C.SEKTOREN}
    beta = sum(w[s] * betas.get(s, 1.0) for s in C.SEKTOREN) if betas else None
    as_sektor = 0.5 * sum(abs(w[s] * 100 - C.SP500_GEWICHT[s]) for s in C.SEKTOREN)
    return dict(beta=beta, active_share_sektor=as_sektor)


def budget_lesen(einst: pd.DataFrame | None) -> dict:
    if einst is not None and not einst.empty:
        d = einst[einst["schluessel"] == "risikobudget"].sort_values("zeit")
        if len(d):
            try:
                return {k: float(v) for k, v in json.loads(d.iloc[-1]["wert"]).items()}
            except (ValueError, TypeError):
                pass
    return dict(BUDGET_START)


# ---------------------------------------------------------------------------
# Darstellung (Seite und Abschnitt in der Taktik)
# ---------------------------------------------------------------------------
def pruefungen(r: dict, ips: dict, kz: dict) -> list[tuple[str, str, str]]:
    out = []
    out.append(("ok" if r["te"] <= ips["te_max"] else "fehler", "Tracking Error",
                f"{ui.de(r['te'], 2)} % p.a. (IPS höchstens {ui.de(ips['te_max'], 1)} %)"))
    out.append(("ok" if r["vola"] <= ips["vola_max"] else "fehler", "Volatilität",
                f"{ui.de(r['vola'], 1)} % p.a. (IPS höchstens {ui.de(ips['vola_max'], 1)} %)"))
    out.append(("ok" if r["es95_3"] <= ips["verlust_max"] else "warnung", "Expected Shortfall 95 % (3 Mt.)",
                f"{ui.de(-r['es95_3'], 1)} % (tragbarer Verlust −{ui.de(ips['verlust_max'], 0)} %)"))
    if kz.get("beta") is not None:
        out.append(("ok" if ips["beta_min"] <= kz["beta"] <= ips["beta_max"] else "fehler", "Beta Aktienteil",
                    f"{ui.de(kz['beta'], 2)} (IPS {ui.de(ips['beta_min'], 2)}–{ui.de(ips['beta_max'], 2)}, Sektor-Betas 3 J.)"))
    if kz.get("active_share_sektor") is not None:
        st_ = "ok" if kz["active_share_sektor"] <= ips["active_share_max"] else "warnung"
        out.append((st_, "Active Share (nur Sektor-Ebene)",
                    f"≥ {ui.de(kz['active_share_sektor'], 1)} % – Untergrenze, die echte Active Share kommt aus den Einzeltiteln "
                    f"(IPS {ui.de(ips['active_share_min'], 0)}–{ui.de(ips['active_share_max'], 0)} %)"))
    sfr = (r["e3"] - ips["rendite_min"]) / r["s3"] if r["s3"] > 0 else np.nan
    out.append(("ok" if sfr > 0 else "warnung", "Roy's Safety-First (3 Mt.)",
                f"SFR = ({ui.de(r['e3'], 2)} % − {ui.de(ips['rendite_min'], 1)} %) / {ui.de(r['s3'], 2)} % = {ui.de(sfr, 2)}"))
    ziel_rel = ips["rendite_rel"]
    out.append(("ok" if r["akt3"] >= ziel_rel - 1e-9 else "warnung", "Relatives Renditeziel",
                f"Erwartete Mehrrendite 3 Mt. {ui.de(r['akt3'], 2, True)} PP (IPS-Ziel bis Saisonende {ui.de(ziel_rel, 1, True)} PP)"))
    return out


def abschnitt(ctx, r: dict, werte: dict, R=None, kurz: bool = False):
    ips = ctx.ips
    betas = sektor_betas(ctx.hist)
    kz = kennzahlen_aktienteil(werte, betas)
    budget = budget_lesen(ctx.tabellen.get("einstellungen"))
    kp = [("Tracking Error ex ante", f"{ui.de(r['te'], 2)} %", f"Budget (IPS) {ui.de(ips['te_max'], 1)} % · genutzt {r['te'] / ips['te_max'] * 100:.0f} %"),
          ("Erw. Mehrrendite (3 Mt.)", f"{ui.de(r['akt3'], 2, True)} PP", f"Information Ratio {ui.de(r['ir'], 2)}"),
          ("VaR / ES 95 % (1 Mt.)", f"−{ui.de(r['var95'], 1)} / −{ui.de(r['es95'], 1)} %",
           f"≈ {ui.de(r['var95'] / 100 * C.STARTKAPITAL / 1e3, 0)} / {ui.de(r['es95'] / 100 * C.STARTKAPITAL / 1e3, 0)} Tsd. USD"),
          ("Volatilität", f"{ui.de(r['vola'], 1)} %", f"Benchmark {ui.de(r['vola_b'], 1)} % · IPS ≤ {ui.de(ips['vola_max'], 0)} %")]
    ui.kopf_kpis(kp)
    if R:
        R.kpis(kp)

    # Risikobudget
    st.markdown("##### Risikobudget: Wofür braucht ihr euren Tracking Error?")
    zeilen = []
    for k in TEILE:
        anteil = r["beitrag"][k] * 100
        zeilen.append({"Entscheid": k, "Erw. Mehrrendite (3 Mt.)": f"{ui.de(r['e_teil'][k] * 3, 2, True)} PP",
                       "TE allein": f"{ui.de(r['te_teil'][k], 2)} %", "Anteil am TE": f"{anteil:.0f} %",
                       "Geplant": f"{budget.get(k, 0):.0f} %",
                       "Rendite je TE-Einheit": ui.de(r['e_teil'][k] * 12 / r['te_teil'][k], 2) if r['te_teil'][k] > 0 else "–",
                       "Status": "über Plan" if anteil > budget.get(k, 0) + 15 else "unter Plan" if anteil < budget.get(k, 0) - 15 else "im Plan"})
    tab = pd.DataFrame(zeilen)
    c1, c2 = st.columns([1.5, 1])
    with c1:
        st.dataframe(tab, hide_index=True, width="stretch")
        st.caption("Anteil am TE = Euler-Zerlegung (Beitrag zur Varianz der aktiven Rendite). Kann negativ sein, wenn ein Entscheid andere "
                   "absichert. «Rendite je TE-Einheit» zeigt, welcher Entscheid sein Risiko am besten bezahlt – dort lohnt mehr Budget.")
    with c2:
        fig = go.Figure(go.Bar(x=[r["beitrag"][k] * r["te"] for k in TEILE], y=TEILE, orientation="h",
                               marker_color=[ui.BLAU, ui.GOLD, ui.PETROL],
                               text=[f"{r['beitrag'][k] * 100:.0f} %" for k in TEILE], textposition="outside"))
        fig.add_vline(x=ips["te_max"], line=dict(color=ui.FARBE["Rot"], dash="dot"), annotation_text="Budget IPS")
        fig.update_layout(height=200, margin=dict(l=4, r=30, t=24, b=4), plot_bgcolor="white", showlegend=False,
                          title=dict(text="Beitrag zum Tracking Error (% p.a.)", font=dict(size=12)))
        fig.update_xaxes(range=[0, max(ips["te_max"], r["te"]) * 1.25], gridcolor="#EEF0F3")
        ui.zeige(fig)
    if R:
        R.h("Risikobudget (Tracking Error)")
        R.tabelle(tab)
    if kurz:
        return
    with st.popover("Geplante Aufteilung des Risikobudgets ändern", icon=":material/tune:", disabled=ctx.nur_lesen):
        with st.form("budget"):
            neu = {k: st.number_input(k, 0.0, 100.0, float(budget.get(k, 0)), 5.0, format="%.3f") for k in TEILE}
            if st.form_submit_button("Speichern"):
                tot = sum(neu.values()) or 1
                ctx.speichern("einstellungen", [dict(zeit=jetzt(), person=ctx.person, schluessel="risikobudget",
                                                     wert=json.dumps({k: v / tot * 100 for k, v in neu.items()}))],
                              "Risikobudget gespeichert.")

    # Prüfung gegen IPS
    st.markdown("##### Prüfung gegen die Risikoziele im IPS")
    pr = pruefungen(r, ips, kz)
    for a_, b_, c_ in pr:
        (st.error if a_ == "fehler" else st.warning if a_ == "warnung" else st.success)(f"**{b_}:** {c_}")
    if R:
        R.h("Prüfung gegen das IPS")
        R.tabelle(pd.DataFrame([("✗" if a_ == "fehler" else "!" if a_ == "warnung" else "✓", b_, c_) for a_, b_, c_ in pr],
                               columns=["", "Kennzahl", "Ergebnis"]), [8, 50, 122])

    # Szenarien und Stresstests
    st.markdown("##### Szenario-Analyse und Stresstests (3 Monate, bis Saisonende)")
    defs = ctx.szdef
    z = [{"Szenario": f"{defs[s]['kurz']} ({ctx.szen.loc[s, 'p']:.0f} %)", "Taktik": f"{ui.de(v['r_p'], 2, True)} %",
          "Benchmark": f"{ui.de(v['r_b'], 2, True)} %", "Aktiv": f"{ui.de(v['aktiv'], 2, True)} PP",
          **{k: f"{ui.de(v[k], 2, True)}" for k in TEILE}} for s, v in r["szen"].items()]
    hs = historische_stresstests(ctx.hist, werte, ctx.bond.get("duration", 5.5))
    for _, h in hs.iterrows():
        z.append({"Szenario": f"Historisch {h['Monat']} ({h['Auslöser']}; S&P {ui.de(h['sp'], 1, True)} %, Treasuries {ui.de(h['govt'], 1, True)} %)",
                  "Taktik": f"{ui.de(h['r_p'], 2, True)} %", "Benchmark": f"{ui.de(h['r_b'], 2, True)} %",
                  "Aktiv": f"{ui.de(h['aktiv'], 2, True)} PP", **{k: "" for k in TEILE}})
    stab = pd.DataFrame(z)
    st.dataframe(stab, hide_index=True, width="stretch")
    st.caption("Hypothetische Szenarien = eure Szenarien (3 Monate). Historische Szenarien = die schwächsten Aktien- und Anleihenmonate "
               "der letzten 5–6 Jahre (Yahoo ^SP500TR, GOVT, Sektor-ETFs), angewendet auf die heutige Taktik (1 Monat). Die Spalten "
               "Asset Allocation, Sektor-Wetten und Duration & Kurve zeigen, woher die aktive Rendite kommt (PP).")
    if R:
        R.h("Szenario-Analyse und Stresstests")
        R.tabelle(stab)

    # Reverse-Stresstest
    a, b = werte["aktien"] / 100, werte["anleihen"] / 100
    L = ips["verlust_max"]
    anl3 = r["bear_anl_3m"] if r["bear_anl_3m"] is not None else 0.0
    x = (-L - b * anl3 - werte["cash"] / 100 * r["cash_m"] * 3) / a if a else None
    carry3 = r["anl"]["y"] / 12 * 3
    dy_be = carry3 / r["anl"]["D"] * 100 if r["anl"]["D"] else None
    txt = [f"**Reverse-Stresstest:** Fallen die Aktien über die Saison um **{ui.de(-x, 1)} %** (bei Zinsen wie im Bear-Szenario), "
           f"erreicht die Taktik den maximal tragbaren Verlust von −{ui.de(L, 0)} % aus dem IPS." if x is not None else "",
           f"**Anleihen:** Eure Treasuries (≈ {ui.de(r['anl']['T'], 1)} J. Laufzeit, Duration {ui.de(r['anl']['D'], 1)} J.) verdienen "
           f"über 3 Monate ≈ {ui.de(carry3, 2)} % Zins. Erst wenn ihre Rendite um mehr als **{ui.de(dy_be, 0)} bp** steigt, wird der "
           "Anleiheteil negativ." if dy_be else "",
           f"**Schlechtestes 5 % gegenüber der Benchmark (3 Mt.):** {ui.de(r['akt3_q05'], 2, True)} PP – so viel kann die Taktik in "
           "einem ungünstigen Fall hinter der Strategie des Dozenten liegen."]
    for t_ in txt:
        if t_:
            st.markdown(t_)
            if R:
                R.text(t_)


def seite_risiko(ctx):
    R = SP.Sammler("Risiko & Risikobudget", "Ex-ante-Risiko der Taktik gegenüber der Benchmark des Dozenten (50/50)", kurz="Risiko")
    ui.seitenkopf("Risiko & Risikobudget",
                  "Wie viel Risiko nimmt die Taktik gegenüber der Strategie des Dozenten, wofür wird das Risikobudget eingesetzt und "
                  "hält alles die Risikoziele im IPS ein? Grundlage: eure Szenarien, historische Streuung, aktuelle Kurve.")
    ph = st.empty()
    a = ctx.alloc
    st.caption(f"Berechnet für: {a.get('quelle_txt', '')} · {a['aktien']:.0f} / {a['anleihen']:.0f} / {a['cash']:.0f} % · "
               f"Duration {ui.de(a['duration'], 1)} J. Andere Taktik testen: Werte auf der Seite «Taktik» eingeben – dort steht der "
               "gleiche Abschnitt live.")
    r = berechne(ctx)
    if r is None:
        SP.knopf(ph, None, "risiko")
        return
    abschnitt(ctx, r, taktik_werte(ctx), R)
    st.markdown("##### Verteilung der Taktik-Rendite über die Saison (3 Monate)")
    k = r["kanten"]
    fig = go.Figure(go.Bar(x=[(k[i] + k[i + 1]) / 2 for i in range(len(k) - 1)], y=r["verteilung"],
                           marker_color=[ui.FARBE["Rot"] if (k[i] + k[i + 1]) / 2 < -r["var95_3"] else ui.STAHL for i in range(len(k) - 1)]))
    fig.add_vline(x=-ctx.ips["verlust_max"], line=dict(color=ui.FARBE["Rot"], dash="dot"), annotation_text="tragbarer Verlust")
    fig.add_vline(x=ctx.ips["rendite_min"], line=dict(color=ui.GOLD, dash="dot"), annotation_text="erforderlich")
    fig.add_vline(x=ctx.ips["rendite_ziel"], line=dict(color=ui.FARBE["Grün"], dash="dot"), annotation_text="gewünscht")
    fig.update_layout(height=280, margin=dict(l=8, r=8, t=20, b=8), plot_bgcolor="white", bargap=0.05)
    fig.update_xaxes(ticksuffix=" %", title="Rendite Dez.–Feb.")
    fig.update_yaxes(visible=False)
    ui.zeige(fig)
    st.caption(f"Erwartet {ui.de(r['e3'], 2, True)} % (Benchmark {ui.de(r['e3_b'], 2, True)} %), Streuung {ui.de(r['s3'], 2)} %, "
               f"VaR 95 % {ui.de(-r['var95_3'], 1)} %, Expected Shortfall 95 % {ui.de(-r['es95_3'], 1)} %. Rot = schlechteste 5 %.")
    with st.expander("Methodik (CFA)", icon=":material/school:"):
        st.markdown(__doc__.split("\n", 2)[2] + """
- **VaR 95 %** = Verlust, der in 95 % der Fälle nicht überschritten wird. **Expected Shortfall (CVaR)** = durchschnittlicher Verlust in den
  schlechtesten 5 % – erfasst den Tail besser als der VaR.
- **Roy's Safety-First-Ratio** = (erwartete Rendite − erforderliche Rendite) / Streuung; höher ist besser.
- **Stresstests:** hypothetisch (eure Szenarien) und historisch (schwächste Monate). **Reverse-Stresstest:** Welcher Schock
  bringt die Taktik an die Verlustgrenze des IPS?
- **Grenzen:** Normalverteilte Streuung innerhalb der Szenarien unterschätzt Extremereignisse; die Szenarien selbst können falsch sein
  (Modellrisiko). Deshalb VaR immer zusammen mit Stresstests lesen.""")
    SP.knopf(ph, R, "risiko")
