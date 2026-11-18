"""Anleihen & Duration: Risikoanalyse des Treasury-Teils und Ideen für Mehrrendite gegenüber der Benchmark,
abgeleitet aus euren Zinserwartungen (Szenario-Parameter Fed, 2 J., 10 J.).

Methodik (Näherung, 1 Monat Horizont):
  Rendite = Carry (Rendite/12) + Roll-down (Duration × Kurvensteigung über 1 Monat)
            − Duration × Δ Rendite + ½ × Konvexität × Δ Rendite²
  Δ Rendite je Laufzeit: Fed-Veränderung am kurzen Ende (3 Mt.), linear zu 2 J. und 10 J., darüber wie 10 J.
  Markt-Erwartung: implizite Forward-Rendite in 1 Monat aus der heutigen Kurve.
"""
from __future__ import annotations

import itertools
import json

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import portfolio as P
import seitenpdf as SP
from makro import config as C
from makro import ui
from storage import jetzt, zahl

H = 1 / 12  # Horizont 1 Monat


# ---------------------------------------------------------------------------
# Rechenkern
# ---------------------------------------------------------------------------
def kurve(fred: dict, vor_tagen: int = 0) -> dict[float, float]:
    ende = None
    for s in C.ZINSKURVE:
        r = fred.get(s)
        if r is not None and len(r.dropna()):
            ende = max(ende, r.dropna().index[-1]) if ende is not None else r.dropna().index[-1]
    if ende is None:
        return {}
    return P.kurve_am(fred, ende - pd.Timedelta(days=vor_tagen))


def y_bei(k: dict, T: float) -> float | None:
    return P.kurve_rendite(k, T)


def laufzeit_kz(k: dict, T: float) -> dict:
    y = y_bei(k, T)
    D = P.mod_duration(y, T, y)
    p0, pm, pp = P.preis(y, T, y), P.preis(y, T, y - 0.1), P.preis(y, T, y + 0.1)
    konv = (pm + pp - 2 * p0) / (p0 * 0.001 ** 2)
    y_roll = y_bei(k, max(T - H, 1 / 12))
    roll = D * (y - y_roll) if T > H else 0.0
    # Forward-Rendite der Laufzeit T in 1 Monat (Markt-Erwartung)
    y_h, y_th = y_bei(k, H), y_bei(k, T + H)
    fwd = ((y_th * (T + H)) - y_h * H) / T
    return dict(T=T, y=y, D=D, konv=konv, carry=y / 12, roll=roll, fwd_dy=(fwd - y) * 100)


def dy_szenario(T: float, par: dict) -> float:
    """Renditeänderung in bp für Laufzeit T aus Fed, 2 J. und 10 J."""
    pts = [(0.25, float(par.get("fed", 0) or 0)), (2.0, float(par.get("dgs2", 0) or 0)), (10.0, float(par.get("dgs10", 0) or 0)),
           (30.0, float(par.get("dgs10", 0) or 0))]
    return float(np.interp(T, [p[0] for p in pts], [p[1] for p in pts]))


def rendite_1m(kz: dict, dy_bp: float) -> float:
    d = dy_bp / 100 / 100
    return kz["carry"] + kz["roll"] - kz["D"] * dy_bp / 100 + 0.5 * kz["konv"] * d ** 2 * 100


def tabelle(k: dict, defs: dict, szen: pd.DataFrame) -> pd.DataFrame:
    p = {sid: szen.loc[sid, "p"] / 100 for sid in defs}
    zeilen = []
    for T in C.LAUFZEITEN:
        kz = laufzeit_kz(k, T)
        r = {sid: rendite_1m(kz, dy_szenario(T, defs[sid]["param"])) for sid in defs}
        dy_erw = sum(p[s] * dy_szenario(T, defs[s]["param"]) for s in defs)
        zeilen.append(dict(kz, dy_erw=dy_erw, erw=sum(p[s] * r[s] for s in defs), worst=min(r.values()),
                           breakeven=(kz["carry"] + kz["roll"]) / kz["D"] * 100 if kz["D"] > 0 else np.nan,
                           **{f"r_{s}": v for s, v in r.items()}))
    return pd.DataFrame(zeilen).set_index("T", drop=False)


def benchmark_info(k: dict, name: str) -> dict:
    w = C.BOND_BENCHMARKS.get(name) or C.BOND_BENCHMARKS[C.BOND_BENCHMARK_DEFAULT]
    kz = {T: laufzeit_kz(k, T) for T in w}
    return dict(name=name, gewichte=w, rendite=sum(w[T] * kz[T]["y"] for T in w), duration=sum(w[T] * kz[T]["D"] for T in w),
                laufzeit=sum(w[T] * T for T in w), cash=y_bei(k, 0.25) or 4.0)


def bond_basis(fred: dict, name: str) -> dict:
    k = kurve(fred)
    if len(k) < 4:
        return dict(name=name, gewichte=C.BOND_BENCHMARKS.get(name, {}), rendite=4.5, duration=5.5, laufzeit=6.5, cash=4.0)
    return benchmark_info(k, name)


def mix(tab: pd.DataFrame, w: dict, defs: dict) -> dict:
    w = {float(T): v for T, v in w.items() if v}
    tot = sum(w.values()) or 1
    w = {T: v / tot for T, v in w.items()}

    def s(col):
        return sum(v * float(np.interp(T, tab.index, tab[col])) for T, v in w.items())
    return dict(duration=s("D"), rendite=s("y"), konv=s("konv"), erw=s("erw"), carry=s("carry") + s("roll"),
                **{f"r_{sid}": s(f"r_{sid}") for sid in defs})


def ideen(tab: pd.DataFrame, bench: dict, defs: dict, dur_min: float, dur_max: float, max_verlust: float = 1.0) -> pd.DataFrame:
    """Alle Mischungen aus 1–2 Laufzeiten (Schritte 10 %), die im IPS-Durationsband liegen; sortiert nach erwarteter Mehrrendite."""
    b = mix(tab, bench["gewichte"], defs)
    zeilen = []
    T = list(tab.index)
    for t1, t2 in itertools.combinations_with_replacement(T, 2):
        for a in np.arange(0.1, 1.01, 0.1) if t1 != t2 else [1.0]:
            w = {t1: a, t2: 1 - a} if t1 != t2 else {t1: 1.0}
            m = mix(tab, w, defs)
            if not dur_min <= m["duration"] <= dur_max:
                continue
            aktiv = {sid: m[f"r_{sid}"] - b[f"r_{sid}"] for sid in defs}
            if min(aktiv.values()) < -max_verlust:
                continue
            name = (f"{_lz(t1)}" if t1 == t2 else f"{a * 100:.0f} % {_lz(t1)} + {(1 - a) * 100:.0f} % {_lz(t2)}")
            zeilen.append(dict(Strategie=name, w=w, duration=m["duration"], erw_aktiv=m["erw"] - b["erw"],
                               worst_aktiv=min(aktiv.values()), **{f"a_{s}": v for s, v in aktiv.items()}))
    d = pd.DataFrame(zeilen)
    if d.empty:
        return d
    d["score"] = d["erw_aktiv"] + 0.25 * d["worst_aktiv"]
    return d.sort_values("score", ascending=False).drop_duplicates("Strategie").head(8)


def gewichte_aktiv(ctx, tab: pd.DataFrame, bench: dict) -> tuple[dict, str]:
    """Positionierung der Anleihen: gespeicherter Laufzeiten-Plan, sonst Bullet mit der Duration aus der Taktik."""
    plan, info = plan_lesen(ctx.tabellen.get("einstellungen"), bench)
    if not info.startswith("Noch kein Plan"):
        return plan, "Laufzeiten-Plan"
    dur = ctx.alloc.get("duration") or bench["duration"]
    T = float(np.interp(dur, tab["D"].values, tab.index.values))
    lo = max([t for t in tab.index if t <= T], default=tab.index[0])
    hi = min([t for t in tab.index if t >= T], default=tab.index[-1])
    if hi == lo:
        return {lo: 1.0}, f"Bullet {ui.de(T, 1)} J. (Duration aus der Taktik)"
    a = (tab.loc[hi, "D"] - dur) / (tab.loc[hi, "D"] - tab.loc[lo, "D"])
    return {lo: float(a), hi: float(1 - a)}, f"Duration {ui.de(dur, 1)} J. aus der Taktik ({_lz(lo)}/{_lz(hi)})"


def relativ(tab: pd.DataFrame, w: dict, bench: dict, defs: dict, szen: pd.DataFrame, sig_10y: float = 25.0) -> dict:
    """Wirkung der Anleihen-Positionierung relativ zur Benchmark je Szenario (Monat, in bp):
    Carry/Roll-Differenz + Durationseffekt (paralleler Teil) + Kurveneffekt (Rest, inkl. Konvexität)."""
    m_p, m_b = mix(tab, w, defs), mix(tab, bench["gewichte"], defs)
    t_b = bench["laufzeit"]
    out = {"D_p": m_p["duration"], "D_b": m_b["duration"], "aktiv_D": m_p["duration"] - m_b["duration"], "szen": {}}
    for sid in defs:
        dy_par = dy_szenario(t_b, defs[sid]["param"])
        gesamt = (m_p[f"r_{sid}"] - m_b[f"r_{sid}"]) * 100
        carry = (m_p["carry"] - m_b["carry"]) * 100
        dur = -(m_p["duration"] - m_b["duration"]) * dy_par
        out["szen"][sid] = dict(gesamt=gesamt, carry=carry, duration=dur, kurve=gesamt - carry - dur, dy=dy_par)
    p = {sid: szen.loc[sid, "p"] / 100 for sid in defs}
    out["erw"] = {k: sum(p[s] * out["szen"][s][k] for s in defs) for k in ("gesamt", "carry", "duration", "kurve")}
    out["te"] = abs(out["aktiv_D"]) * sig_10y * np.sqrt(12) / 100   # % p.a., nur Durationsrisiko
    out["ir"] = out["erw"]["gesamt"] / 100 * 12 / out["te"] if out["te"] > 0 else np.nan
    return out


def begruendung(rel: dict, defs: dict, szen: pd.DataFrame, gew: dict, fwd10: float, bench_name: str) -> str:
    aD = rel["aktiv_D"]
    lage = (f"{ui.de(abs(aD), 1)} J. länger als" if aD > 0.15 else f"{ui.de(abs(aD), 1)} J. kürzer als" if aD < -0.15 else "gleich lang wie")
    t = (f"Unsere Anleihen haben eine Duration von {ui.de(rel['D_p'], 1)} J. und sind damit {lage} die Benchmark ({bench_name}, "
         f"{ui.de(rel['D_b'], 1)} J.). Wir erwarten für die 10-J.-Rendite {ui.de(gew['dgs10'], 0, True)} bp im Monat, der Markt preist "
         f"{ui.de(fwd10, 0, True)} bp ein. ")
    t += "Wirkung gegenüber der Benchmark je Szenario: " + "; ".join(
        f"{defs[s]['kurz']} {ui.de(v['gesamt'], 0, True)} bp" for s, v in rel["szen"].items()) + ". "
    e = rel["erw"]
    t += (f"Gewichtet {ui.de(e['gesamt'], 0, True)} bp pro Monat (Duration {ui.de(e['duration'], 0, True)}, Kurve {ui.de(e['kurve'], 0, True)}, "
          f"Carry/Roll {ui.de(e['carry'], 0, True)} bp) bei einem aktiven Zinsrisiko von ≈ {ui.de(rel['te'], 2)} % p.a. Tracking Error.")
    return t


def relativ_kurz(ctx) -> str:
    """Kurzer Begründungstext für andere Seiten (Erwartungen, Taktik)."""
    try:
        k = kurve(ctx.fred)
        if len(k) < 5:
            return ""
        bench = benchmark_info(k, ctx.einst.get("bond_benchmark", C.BOND_BENCHMARK_DEFAULT))
        tab = tabelle(k, ctx.szdef, ctx.szen)
        w, _ = gewichte_aktiv(ctx, tab, bench)
        rel = relativ(tab, w, bench, ctx.szdef, ctx.szen, ctx.sig.get("dgs10", (25, ""))[0])
        return begruendung(rel, ctx.szdef, ctx.szen, ctx.gew["param"], float(tab.loc[10.0, "fwd_dy"]), bench["name"])
    except Exception:  # noqa: BLE001
        return ""


def _lz(T: float) -> str:
    return "3 Mt." if T == 0.25 else f"{T:g} J."


def plan_lesen(einst: pd.DataFrame | None, bench: dict) -> tuple[dict, str]:
    if einst is not None and not einst.empty:
        d = einst[einst["schluessel"] == "bond_plan"].sort_values("zeit")
        if len(d):
            try:
                w = {float(k): float(v) for k, v in json.loads(d.iloc[-1]["wert"]).items()}
                return w, f"Plan von {d.iloc[-1]['person']} ({str(d.iloc[-1]['zeit'])[:10]})"
            except (ValueError, TypeError):
                pass
    return dict(bench["gewichte"]), "Noch kein Plan gespeichert – Startwert = Benchmark"


# ---------------------------------------------------------------------------
# Seite
# ---------------------------------------------------------------------------
def seite_anleihen(ctx, fred: dict):
    R = SP.Sammler("Anleihen & Duration", "Treasury-Teil: Risiko, Zinserwartungen und Mehrrendite gegenüber der Benchmark",
                   kurz="Anleihen")
    ui.seitenkopf("Anleihen & Duration",
                  "Risikoanalyse des Treasury-Teils und wo ihr mit euren Zinserwartungen mehr Rendite als der Markt holen könnt. "
                  "Horizont 1 Monat, Zinserwartungen aus den Szenario-Parametern (Fed, 2 J., 10 J.).")
    ph = st.empty()
    k, k_alt = kurve(fred), kurve(fred, 30)
    if len(k) < 5:
        st.warning("Zu wenige Zinsdaten von FRED. Bitte FRED-Schlüssel in den Secrets prüfen.")
        SP.knopf(ph, None, "anleihen")
        return
    defs, szen, ips, a = ctx.szdef, ctx.szen, ctx.ips, ctx.alloc
    bench = benchmark_info(k, ctx.einst.get("bond_benchmark", C.BOND_BENCHMARK_DEFAULT))
    tab = tabelle(k, defs, szen)
    betrag = C.STARTKAPITAL * a["anleihen"] / 100
    plan, plan_info = gewichte_aktiv(ctx, tab, bench)
    if plan_info == "Laufzeiten-Plan":
        plan_info = plan_lesen(ctx.tabellen.get("einstellungen"), bench)[1]
    else:
        plan_info = f"Noch kein Laufzeiten-Plan gespeichert – Startwert: {plan_info}"
    ui.stand_abgleich("bond_plan", plan_info, ("bond_plan_ed",))
    m_plan, m_b = mix(tab, plan, defs), mix(tab, bench["gewichte"], defs)
    dv01 = betrag * m_plan["duration"] / 10000
    kpis = [
        ("Anleihen (Taktik)", f"{a['anleihen']:.0f} %", f"≈ {ui.de(betrag / 1e6, 2)} Mio. USD · {a.get('quelle_txt', '')}"),
        ("Duration Plan", f"{ui.de(m_plan['duration'], 2)} J.",
         f"Benchmark {ui.de(bench['duration'], 2)} J. · IPS {ui.de(ips['duration_min'], 0)}–{ui.de(ips['duration_max'], 0)} J."),
        ("DV01", f"{ui.de(dv01, 0)} USD", "Wertänderung je +1 bp Rendite (negativ)"),
        ("Erwartete Mehrrendite 1 Mt.", f"{ui.de((m_plan['erw'] - m_b['erw']) * 100, 0, True)} bp",
         f"Plan {ui.de(m_plan['erw'], 2, True)} % vs. Benchmark {ui.de(m_b['erw'], 2, True)} %"),
    ]
    ui.kopf_kpis(kpis)
    R.kpis(kpis)
    if not ips["duration_min"] <= m_plan["duration"] <= ips["duration_max"]:
        st.error(f"Die Duration des Plans liegt ausserhalb des IPS-Bands ({ui.de(ips['duration_min'], 0)}–{ui.de(ips['duration_max'], 0)} J.).")
    if a.get("duration") and abs(a["duration"] - m_plan["duration"]) > 0.5:
        st.info(f"Die Taktik nennt eine Duration von {ui.de(a['duration'], 1)} J., der Laufzeiten-Plan unten ergibt "
                f"{ui.de(m_plan['duration'], 2)} J. Eines von beiden anpassen.")
    st.caption(f"Benchmark: {bench['name']} (wählbar unter «Einstellungen»; den genauen Treasury-Index der Dozenten-App kennen wir "
               "nicht). Daten: FRED-Zinskurve DGS1MO … DGS30.")

    # --- Zinskurve -----------------------------------------------------------
    st.markdown("#### 1. Zinskurve heute, vor einem Monat und eure Erwartung für das Monatsende")
    xs = sorted(k)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=xs, y=[k_alt.get(x) for x in xs], name="vor 1 Monat", line=dict(color="#A0A8B4", dash="dot")))
    fig.add_trace(go.Scatter(x=xs, y=[k[x] for x in xs], name="heute", line=dict(color=ui.BLAU, width=3)))
    pw = {sid: szen.loc[sid, "p"] / 100 for sid in defs}
    fig.add_trace(go.Scatter(x=xs, y=[k[x] + sum(pw[s] * dy_szenario(x, defs[s]["param"]) for s in defs) / 100 for x in xs],
                             name="Erwartung Monatsende (gewichtet)", line=dict(color=ui.GOLD, width=2)))
    for sid in defs:
        fig.add_trace(go.Scatter(x=xs, y=[k[x] + dy_szenario(x, defs[sid]["param"]) / 100 for x in xs], name=defs[sid]["kurz"],
                                 line=dict(color=C.SZEN_FARBE[sid], width=1), opacity=0.55, visible="legendonly"))
    fig.update_layout(height=320, margin=dict(l=8, r=8, t=10, b=8), plot_bgcolor="white", legend=dict(orientation="h", y=-0.2))
    fig.update_xaxes(type="log", tickvals=xs, ticktext=[_lz(x) for x in xs], gridcolor="#EEF0F3")
    fig.update_yaxes(ticksuffix=" %", gridcolor="#EEF0F3")
    ui.zeige(fig)

    st.markdown("#### 2. Laufzeiten im Vergleich (1 Monat)")
    out = pd.DataFrame({
        "Laufzeit": [_lz(T) for T in tab.index],
        "Rendite": tab["y"].map(lambda x: f"{ui.de(x, 2)} %"),
        "Δ 1 Mt.": [f"{ui.de((k[T] - k_alt.get(T, k[T])) * 100, 0, True)} bp" if T in k else "–" for T in tab.index],
        "Mod. Duration": tab["D"].map(lambda x: ui.de(x, 2)),
        "Carry + Roll": (tab["carry"] + tab["roll"]).map(lambda x: f"{ui.de(x * 100, 0)} bp"),
        "Puffer (Breakeven)": tab["breakeven"].map(lambda x: f"{ui.de(x, 0)} bp"),
        "Markt (Forward)": tab["fwd_dy"].map(lambda x: f"{ui.de(x, 0, True)} bp"),
        "Eure Erwartung": tab["dy_erw"].map(lambda x: f"{ui.de(x, 0, True)} bp"),
        "Erw. Rendite": tab["erw"].map(lambda x: f"{ui.de(x, 2, True)} %"),
        **{defs[s]["kurz"]: tab[f"r_{s}"].map(lambda x: f"{ui.de(x, 2, True)} %") for s in defs},
        "Sicht vs. Markt": [("bullisher → länger" if r["dy_erw"] < r["fwd_dy"] - 5 else
                             "bearisher → kürzer" if r["dy_erw"] > r["fwd_dy"] + 5 else "wie Markt") for _, r in tab.iterrows()],
    })
    st.dataframe(out, hide_index=True, width="stretch")
    st.caption("Carry + Roll = Ertrag in einem Monat, wenn sich die Kurve nicht bewegt. Puffer = so viele bp darf die Rendite steigen, "
               "bevor die Laufzeit im Monat verliert. Markt (Forward) = Renditeänderung, die der Markt über die Kurve einpreist. "
               "Mehrrendite entsteht dort, wo eure Erwartung vom Markt abweicht.")
    R.h("Laufzeiten im Vergleich (1 Monat)")
    R.tabelle(out.drop(columns=["Δ 1 Mt."]))

    # --- Plan vs. Benchmark ------------------------------------------------
    st.markdown("#### 3. Euer Laufzeiten-Plan gegen die Benchmark")
    st.caption(plan_info)
    ed_df = pd.DataFrame({"Laufzeit": [_lz(T) for T in C.LAUFZEITEN], "T": C.LAUFZEITEN,
                          "Plan %": [round(plan.get(T, 0) * 100, 1) for T in C.LAUFZEITEN],
                          "Benchmark %": [round(bench["gewichte"].get(T, 0) * 100, 1) for T in C.LAUFZEITEN]})
    c1, c2 = st.columns([1, 1.25])
    with c1:
        ed = st.data_editor(ed_df.drop(columns="T"), hide_index=True, width="stretch", disabled=["Laufzeit", "Benchmark %"],
                            key="bond_plan_ed", column_config={"Plan %": st.column_config.NumberColumn(min_value=0, max_value=100, step=5)})
        summe = float(ed["Plan %"].sum())
        st.caption(f"Summe Plan: {ui.de(summe, 1)} % (wird auf 100 % normiert).")
        neu = {T: float(v) / 100 for T, v in zip(C.LAUFZEITEN, ed["Plan %"]) if v}
        if st.button("Plan fürs Team speichern", disabled=ctx.nur_lesen or summe <= 0, icon=":material/save:"):
            ctx.speichern("einstellungen", [dict(zeit=jetzt(), person=ctx.person, schluessel="bond_plan",
                                                 wert=json.dumps({str(T): v / (summe / 100) for T, v in neu.items()}))],
                          "Laufzeiten-Plan gespeichert.")
    m_plan = mix(tab, neu, defs) if neu else m_plan
    with c2:
        zeilen = [("Duration", f"{ui.de(m_plan['duration'], 2)} J.", f"{ui.de(m_b['duration'], 2)} J.",
                   f"{ui.de(m_plan['duration'] - m_b['duration'], 2, True)} J."),
                  ("Rendite (Yield)", f"{ui.de(m_plan['rendite'], 2)} %", f"{ui.de(m_b['rendite'], 2)} %",
                   f"{ui.de((m_plan['rendite'] - m_b['rendite']) * 100, 0, True)} bp"),
                  ("Carry + Roll / Monat", f"{ui.de(m_plan['carry'] * 100, 0)} bp", f"{ui.de(m_b['carry'] * 100, 0)} bp",
                   f"{ui.de((m_plan['carry'] - m_b['carry']) * 100, 0, True)} bp"),
                  ("DV01 auf Anleihenbetrag", f"{ui.de(betrag * m_plan['duration'] / 1e4, 0)} USD",
                   f"{ui.de(betrag * m_b['duration'] / 1e4, 0)} USD", ""),
                  ("Erwartete Rendite 1 Mt.", f"{ui.de(m_plan['erw'], 2, True)} %", f"{ui.de(m_b['erw'], 2, True)} %",
                   f"{ui.de((m_plan['erw'] - m_b['erw']) * 100, 0, True)} bp")]
        zeilen += [(f"Szenario {defs[s]['kurz']} ({szen.loc[s, 'p']:.0f} %)", f"{ui.de(m_plan[f'r_{s}'], 2, True)} %",
                    f"{ui.de(m_b[f'r_{s}'], 2, True)} %", f"{ui.de((m_plan[f'r_{s}'] - m_b[f'r_{s}']) * 100, 0, True)} bp") for s in defs]
        vgl = pd.DataFrame(zeilen, columns=["Kennzahl", "Plan", "Benchmark", "Differenz"])
        st.dataframe(vgl, hide_index=True, width="stretch")
    R.h("Plan gegen Benchmark")
    R.tabelle(ed_df.drop(columns="T"))
    R.tabelle(vgl)

    st.markdown("**Key-Rate-Durations** (Beitrag jeder Laufzeit zur Duration; aktiv = Plan − Benchmark)")
    w_p = {T: v / (sum((neu or plan).values()) or 1) for T, v in (neu or plan).items()}
    krd = pd.DataFrame({"Laufzeit": [_lz(T) for T in C.LAUFZEITEN],
                        "Plan": [ui.de(w_p.get(T, 0) * tab.loc[T, "D"], 2) for T in C.LAUFZEITEN],
                        "Benchmark": [ui.de(bench["gewichte"].get(T, 0) * tab.loc[T, "D"], 2) for T in C.LAUFZEITEN],
                        "Aktiv": [ui.de((w_p.get(T, 0) - bench["gewichte"].get(T, 0)) * tab.loc[T, "D"], 2, True) for T in C.LAUFZEITEN]})
    st.dataframe(krd, hide_index=True, width="stretch")
    st.caption("Positive aktive Key-Rate-Duration = ihr seid an dieser Laufzeit zinsempfindlicher als die Benchmark (gewinnt, wenn diese "
               "Rendite fällt). So seht ihr, ob eine Wette auf die Duration (überall gleich) oder auf die Kurve (Vorzeichen wechselt) läuft.")
    R.h("Key-Rate-Durations")
    R.tabelle(krd)

    st.markdown("#### 3b. Wirkung relativ zur Benchmark – Begründung")
    rel = relativ(tab, neu or plan, bench, defs, szen, ctx.sig.get("dgs10", (25, ""))[0])
    zr = [{"Szenario": f"{defs[s]['kurz']} ({szen.loc[s, 'p']:.0f} %)", "Δ Rendite (Benchmark-Laufzeit)": f"{ui.de(v['dy'], 0, True)} bp",
           "Durationseffekt": f"{ui.de(v['duration'], 0, True)} bp", "Kurveneffekt": f"{ui.de(v['kurve'], 0, True)} bp",
           "Carry/Roll": f"{ui.de(v['carry'], 0, True)} bp", "Total vs. Benchmark": f"{ui.de(v['gesamt'], 0, True)} bp"}
          for s, v in rel["szen"].items()]
    e = rel["erw"]
    zr.append({"Szenario": "Gewichtet", "Δ Rendite (Benchmark-Laufzeit)": "", "Durationseffekt": f"{ui.de(e['duration'], 0, True)} bp",
               "Kurveneffekt": f"{ui.de(e['kurve'], 0, True)} bp", "Carry/Roll": f"{ui.de(e['carry'], 0, True)} bp",
               "Total vs. Benchmark": f"{ui.de(e['gesamt'], 0, True)} bp"})
    rtab = pd.DataFrame(zr)
    st.dataframe(rtab, hide_index=True, width="stretch")
    c1, c2, c3 = st.columns(3)
    c1.metric("Aktive Duration", f"{ui.de(rel['aktiv_D'], 2, True)} J.")
    c2.metric("Tracking Error Anleihen", f"{ui.de(rel['te'], 2)} % p.a.", help="Nur Durationsrisiko: |aktive Duration| × Streuung 10 J.")
    c3.metric("Information Ratio (ex ante)", ui.de(rel["ir"], 2))
    text = begruendung(rel, defs, szen, ctx.gew["param"], float(tab.loc[10.0, "fwd_dy"]), bench["name"])
    st.markdown("**Textbaustein für Begründung (Frage 4/5, Taktik, Entscheid):**")
    st.code(text, language=None, wrap_lines=True)
    st.caption("Zerlegung wie im CFA-Curriculum (Fixed Income, Yield Curve Strategies): Rendite = Rolling Yield (Carry + Roll-down) "
               "− Duration × Δ Rendite + Konvexität. Durationseffekt = paralleler Teil, Kurveneffekt = Rest. Das Zinsrisiko gehört ins "
               "Risikobudget (Seite «Risiko & Risikobudget», Teil «Duration & Kurve»).")
    R.h("Wirkung relativ zur Benchmark")
    R.tabelle(rtab)
    R.text(text)

    st.markdown("#### 4. Stresstest (sofortige Kurvenbewegung)")
    schocks = [("Parallel −100 bp", -100, -100), ("Parallel −50 bp", -50, -50), ("Parallel +50 bp", 50, 50),
               ("Parallel +100 bp", 100, 100), ("Steepener (2 J. −25, 10 J. +25)", -25, 25), ("Flattener (2 J. +25, 10 J. −25)", 25, -25)]
    zeilen = []
    for name, d2, d10 in schocks:
        par = dict(fed=d2, dgs2=d2, dgs10=d10)

        def wirkung(w):
            tot = sum(w.values()) or 1
            return sum(v / tot * (-laufzeit_kz(k, T)["D"] * dy_szenario(T, par) / 100
                                  + 0.5 * laufzeit_kz(k, T)["konv"] * (dy_szenario(T, par) / 1e4) ** 2 * 100) for T, v in w.items())
        wp, wb = wirkung(neu or plan), wirkung(bench["gewichte"])
        zeilen.append((name, f"{ui.de(wp, 2, True)} %", f"{ui.de(wp / 100 * betrag / 1e3, 0, True)} Tsd. USD",
                       f"{ui.de(wb, 2, True)} %", f"{ui.de((wp - wb) * 100, 0, True)} bp"))
    stress = pd.DataFrame(zeilen, columns=["Schock", "Plan", "Plan (USD)", "Benchmark", "Differenz"])
    st.dataframe(stress, hide_index=True, width="stretch")
    R.h("Stresstest")
    R.tabelle(stress)

    # --- Ideen ---------------------------------------------------------------
    st.markdown("#### 5. Wo ist Mehrrendite gegenüber der Benchmark möglich?")
    idee = ideen(tab, bench, defs, ips["duration_min"], ips["duration_max"])
    if len(idee):
        st.markdown("**Laufzeiten-Mischungen** (1–2 Laufzeiten, innerhalb des IPS-Durationsbands, höchstens −1 % Minderrendite im "
                    "schlechtesten Szenario). Sortiert nach erwarteter Mehrrendite plus ¼ × schlechtestes Szenario:")
        iout = pd.DataFrame({"Strategie": idee["Strategie"], "Duration": idee["duration"].map(lambda x: f"{ui.de(x, 2)} J."),
                             "Erw. Mehrrendite": idee["erw_aktiv"].map(lambda x: f"{ui.de(x * 100, 0, True)} bp"),
                             **{defs[s]["kurz"]: idee[f"a_{s}"].map(lambda x: f"{ui.de(x * 100, 0, True)} bp") for s in defs},
                             "Schlechtestes Szenario": idee["worst_aktiv"].map(lambda x: f"{ui.de(x * 100, 0, True)} bp")})
        st.dataframe(iout, hide_index=True, width="stretch")
        R.h("Ideen: Laufzeiten-Mischungen")
        R.tabelle(iout)
    gew = ctx.gew["param"]
    kurve_erw = gew.get("dgs10", 0) - gew.get("dgs2", 0)
    fwd_kurve = float(tab.loc[10.0, "fwd_dy"] - tab.loc[2.0, "fwd_dy"])
    txt = []
    txt.append(f"**Duration:** Ihr erwartet für 10 J. {ui.de(gew.get('dgs10', 0), 0, True)} bp, der Markt preist "
               f"{ui.de(tab.loc[10.0, 'fwd_dy'], 0, True)} bp ein. " +
               ("Ihr seid bullisher als der Markt → Duration über der Benchmark lohnt sich, wenn ihr recht habt."
                if gew.get("dgs10", 0) < tab.loc[10.0, "fwd_dy"] - 5 else
                "Ihr seid bearisher als der Markt → Duration unter der Benchmark schützt."
                if gew.get("dgs10", 0) > tab.loc[10.0, "fwd_dy"] + 5 else
                "Kaum Abweichung zum Markt → Duration nahe Benchmark, Mehrrendite eher über Carry und Kurve."))
    txt.append(f"**Kurve (10 J. − 2 J.):** eure Erwartung {ui.de(kurve_erw, 0, True)} bp, Markt {ui.de(fwd_kurve, 0, True)} bp. " +
               ("→ **Steepener** (kurze Laufzeiten über-, lange untergewichten, gleiche Duration)." if kurve_erw > fwd_kurve + 5 else
                "→ **Flattener** (lange Laufzeiten über-, kurze untergewichten, gleiche Duration)." if kurve_erw < fwd_kurve - 5 else
                "→ keine klare Kurvenwette."))
    beste = tab.assign(q=(tab["carry"] + tab["roll"]) / tab["D"].clip(lower=0.1)).sort_values("q", ascending=False)
    txt.append(f"**Carry/Roll:** Am meisten Ertrag pro Einheit Duration bei {_lz(beste.index[0])} und {_lz(beste.index[1])} "
               "(bei unveränderter Kurve).")
    be5 = fred.get("T5YIE")
    if be5 is not None and len(be5.dropna()):
        be = float(be5.dropna().iloc[-1])
        cpi_jahr = gew.get("cpi_mm", 0) * 12
        txt.append(f"**TIPS:** Der Markt preist {ui.de(be, 2)} % Inflation (5-J.-Breakeven) ein, eure gewichtete CPI-Erwartung "
                   f"entspricht ≈ {ui.de(cpi_jahr, 1)} % annualisiert. " +
                   ("TIPS sind relativ attraktiv (ihr erwartet mehr Inflation als eingepreist)." if cpi_jahr > be + 0.3 else
                    "Nominale Treasuries sind relativ attraktiv (ihr erwartet weniger Inflation als eingepreist)." if cpi_jahr < be - 0.3 else
                    "Kein klarer Vorteil für TIPS."))
    for t in txt:
        st.markdown(t)
        R.text(t)
    st.caption("Alle Werte sind Näherungen (Par-Anleihen auf der FRED-Kurve, 1 Monat, ohne Transaktionskosten). Die Ideen sind "
               "Orientierung – entschieden wird in der Taktik und im Entscheidungsprotokoll.")
    with st.expander("Methodik und Quellen"):
        st.markdown(__doc__.split("\n", 2)[2])
        st.markdown("Quellen: FRED (Board of Governors / U.S. Treasury) DGS1MO, DGS3MO, DGS6MO, DGS1, DGS2, DGS3, DGS5, DGS7, DGS10, "
                    "DGS20, DGS30, T5YIE · [fred.stlouisfed.org](https://fred.stlouisfed.org/categories/115)")
    SP.knopf(ph, R, "anleihen")
