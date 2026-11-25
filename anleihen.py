"""Anleihen & Cash: Risikoanalyse des Treasury-Teils und Ideen für Mehrrendite gegenüber der Benchmark,
abgeleitet aus euren Zinserwartungen (Szenario-Parameter Fed, 2 J., 10 J.), plus Zins auf Cash (SOFR − 0,5 %).

Ab Update 12 keine eigene Seite mehr: Ansicht «Anleihen & Cash» auf der Seite «Analyse». Gerechnet wird mit den echten
Anleihen des aktiven Analyseportfolios (Kupon, Fälligkeit, Kurs, Betrag); Zeilen nur mit Laufzeitband gelten als Par-Anleihe
in der Mitte des Bands. Der Laufzeiten-Plan für andere Seiten (Textbaustein Erwartungen) kommt aus dem Bestand, sonst aus dem
gespeicherten Plan (Knopf in der Ansicht), sonst aus der Taktik-Duration.

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
    import cashzins as CZ

    k = kurve(fred)
    if len(k) < 4:
        out = dict(name=name, gewichte=C.BOND_BENCHMARKS.get(name, {}), rendite=4.5, duration=5.5, laufzeit=6.5, cash=4.0)
    else:
        out = benchmark_info(k, name)
    cz = CZ.aktuell(fred)          # Cash-Zins der Simulation: SOFR − 0,5 % (Update 12)
    if cz is not None:
        out["cash"] = cz["netto"]
    elif "cash" in out:
        out["cash"] = max(out["cash"] - CZ.MARGE, 0.0)
    return out


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
    """Positionierung der Anleihen: Bestand (Transaktionen), sonst gespeicherter Laufzeiten-Plan, sonst Bullet mit der
    Duration aus der Taktik."""
    w_best = plan_aus_bestand(ctx.tabellen)
    if w_best:
        return w_best, "Bestand"
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


def relativ(tab: pd.DataFrame, w: dict, bench: dict, defs: dict, szen: pd.DataFrame, sig_10y: float = 25.0,
            m_p: dict | None = None) -> dict:
    """Wirkung der Anleihen-Positionierung relativ zur Benchmark je Szenario (Monat, in bp):
    Carry/Roll-Differenz + Durationseffekt (paralleler Teil) + Kurveneffekt (Rest, inkl. Konvexität).
    m_p = Kennzahlen der echten Anleihen (mix_anleihen); sonst aus den Laufzeit-Gewichten w."""
    m_p, m_b = m_p or mix(tab, w, defs), mix(tab, bench["gewichte"], defs)
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
# Echte Anleihen (Update 12): je Anleihe Rendite, Duration, Carry, Szenarien – Grundlage der Ansicht «Anleihen & Cash»
# ---------------------------------------------------------------------------
BAND_T = {"1–3 J.": 2.0, "3–7 J.": 5.0, "7–10 J.": 8.5, "10–20 J.": 15.0, "20+ J.": 25.0, "TIPS": 6.5}


def auf_raster(T: float) -> dict[float, float]:
    """Verteilt eine Laufzeit linear auf die zwei benachbarten Stützstellen von C.LAUFZEITEN (für Plan und KRD)."""
    xs = [float(x) for x in C.LAUFZEITEN]
    if T <= xs[0]:
        return {xs[0]: 1.0}
    if T >= xs[-1]:
        return {xs[-1]: 1.0}
    for lo, hi in zip(xs, xs[1:]):
        if lo <= T <= hi:
            a = (hi - T) / (hi - lo)
            return {k: v for k, v in ((lo, a), (hi, 1 - a)) if v > 1e-9}
    return {xs[-1]: 1.0}


def _plan_aus(gewichte: list[tuple[float, float]]) -> dict[float, float]:
    tot = sum(w for _, w in gewichte if w > 0)
    out: dict[float, float] = {}
    for T, w in gewichte:
        if w <= 0 or not tot:
            continue
        for k_, a in auf_raster(T).items():
            out[k_] = out.get(k_, 0.0) + a * w / tot
    return out


def plan_aus_bestand(tabellen: dict) -> dict[float, float]:
    """Laufzeit-Gewichte der Treasuries im Bestand (nach Einstand, Restlaufzeit heute)."""
    try:
        tx = P.transaktionen(tabellen.get("transaktionen"))
        if not len(tx):
            return {}
        pos = P.positionen(tx)[0]
        heute = pd.Timestamp.today().normalize()
        g = [(max((p["faelligkeit"] - heute).days / 365.25, 1 / 12), float(p["einstand"])) for p in pos.values()
             if p["klasse"] == "Treasury" and pd.notna(p.get("faelligkeit"))]
        return _plan_aus(g)
    except Exception:  # noqa: BLE001
        return {}


def plan_aus_anleihen(df: pd.DataFrame) -> dict[float, float]:
    return _plan_aus(list(zip(df["T"], df["betrag"]))) if len(df) else {}


def kupon_termine(faellig: pd.Timestamp, von: pd.Timestamp, bis: pd.Timestamp) -> list[pd.Timestamp]:
    """Kupontermine (halbjährlich, rückwärts ab Fälligkeit) im Intervall (von, bis]."""
    out, d, n = [], pd.Timestamp(faellig), 0
    while d > von and n < 80:
        if d <= bis:
            out.append(d)
        n += 1
        d = pd.Timestamp(faellig) - pd.DateOffset(months=6 * n)
    return sorted(out)


def marchzins(nominal: float, kupon: float, faellig: pd.Timestamp, kauf: pd.Timestamp) -> float:
    """Aufgelaufener Zins (Act/Act) seit dem letzten Kupon bis zum Kauftag."""
    if not kupon or pd.isna(faellig) or kauf >= faellig:
        return 0.0
    n = 0
    naechster = pd.Timestamp(faellig)
    while naechster - pd.DateOffset(months=6) >= kauf and n < 80:
        naechster = naechster - pd.DateOffset(months=6)
        n += 1
    letzter = naechster - pd.DateOffset(months=6)
    tage = (naechster - letzter).days or 182
    return nominal * kupon / 100 / 2 * (kauf - letzter).days / tage


def anleihe_kz(k: dict, kupon: float, T: float, y: float, defs: dict, pw: dict) -> dict:
    """Kennzahlen einer Anleihe für 1 Monat: Duration, Konvexität, Carry, Roll-down (Kurve, gleicher Spread), Szenarien."""
    T = max(T, 1 / 365)
    D = P.mod_duration(kupon, T, y)
    p0, pm, pp = P.preis(kupon, T, y), P.preis(kupon, T, y - 0.1), P.preis(kupon, T, y + 0.1)
    konv = (pm + pp - 2 * p0) / (p0 * 0.001 ** 2) if p0 else 0.0
    roll = D * ((y_bei(k, T) or y) - (y_bei(k, max(T - H, 1 / 12)) or y)) if T > H else 0.0
    kz = dict(D=D, konv=konv, carry=y / 12 if T > H else y * T, roll=roll)
    r = {sid: rendite_1m(kz, dy_szenario(T, defs[sid]["param"])) for sid in defs}
    return dict(kz, erw=sum(pw[s] * r[s] for s in defs), worst=min(r.values()) if r else 0.0,
                breakeven=(kz["carry"] + roll) / D * 100 if D > 0 else np.nan, **{f"r_{s}": v for s, v in r.items()})


def anleihen_tabelle(treas: pd.DataFrame, k: dict, defs: dict, szen: pd.DataFrame, heute: pd.Timestamp | None = None,
                     kauf: pd.Timestamp | None = None, ende: pd.Timestamp | None = None) -> pd.DataFrame:
    """Treasury-Zeilen des Analyseportfolios → eine Zeile je Anleihe mit allen Kennzahlen.
    Kurs % gegeben → Rendite aus dem Kurs (Spread zur Kurve bleibt fest); sonst Rendite = Kurve (Spread 0).
    Nur Laufzeitband → Par-Anleihe in der Mitte des Bands (Näherung)."""
    import analyse as A

    heute = (heute or pd.Timestamp.today()).normalize()
    kauf = kauf or max(pd.Timestamp(C.SIMULATION_START), heute)
    ende = ende or pd.Timestamp(C.SIMULATION_ENDE)
    pw = {sid: float(szen.loc[sid, "p"]) / 100 for sid in defs}
    zeilen = []
    for _, z in treas.iterrows():
        betrag = A._num(z.get("Betrag USD")) or 0.0
        if betrag <= 0:
            continue
        faellig = pd.to_datetime(z.get("Fälligkeit"), errors="coerce")
        kupon, kurs_l = A._num(z.get("Kupon %")), A._num(z.get("Kurs %"))
        name = A._txt(z.get("Bezeichnung")).strip()
        isin = A._txt(z.get("ISIN")).strip()
        band = A._txt(z.get("Laufzeit")).strip()
        if pd.notna(faellig) and faellig > heute:
            T = (faellig - heute).days / 365.25
            band = A.baender(T, name)
            y_k = y_bei(k, T)
            if kupon is None:
                kupon, art = (y_k or 4.0), "ohne Kupon: Kupon = Kurvenrendite"
            else:
                art = "Anleihe"
            if kurs_l:
                y = P.rendite(kupon, T, kurs_l)
                kurs, art = kurs_l, art + " · Kurs aus der Liste"
            else:
                y = y_k if y_k is not None else kupon
                kurs = P.preis(kupon, T, y)
                art += " · Kurs aus der Zinskurve"
            name = name or f"T {ui.de(kupon, 3)} % {faellig:%m/%Y}"
        elif band in BAND_T:
            T = BAND_T[band]
            y = y_bei(k, T) or 4.0
            kupon, kurs, faellig = y, 100.0, pd.NaT
            art = "nur Laufzeitband: Par-Anleihe in der Bandmitte (Näherung)"
            name = name or f"Band {band}"
        else:
            continue
        kz = anleihe_kz(k, kupon, T, y, defs, pw)
        nominal = betrag / (kurs / 100) if kurs else betrag
        kup = kupon_termine(faellig, kauf, ende) if pd.notna(faellig) else []
        zeilen.append(dict(name=name, isin=isin, band=band, kupon=kupon, faellig=faellig, T=T, betrag=betrag, nominal=nominal,
                           kurs=kurs, y=y, spread=(y - (y_bei(k, T) or y)) * 100, dv01=betrag * kz["D"] / 1e4,
                           kupons_sim=nominal * kupon / 100 / 2 * len(kup), kupon_daten=", ".join(f"{d:%d.%m.%Y}" for d in kup),
                           marchzins=marchzins(nominal, kupon, faellig, kauf) if pd.notna(faellig) else 0.0, art=art, **kz))
    df = pd.DataFrame(zeilen)
    if len(df):
        df["gewicht"] = df["betrag"] / df["betrag"].sum() * 100
    return df


def mix_anleihen(df: pd.DataFrame, defs: dict) -> dict:
    """Betragsgewichtete Kennzahlen der Anleihen (gleiche Schlüssel wie mix())."""
    w = df["betrag"] / df["betrag"].sum()

    def s(col):
        return float((df[col] * w).sum())
    return dict(duration=s("D"), rendite=s("y"), konv=s("konv"), erw=s("erw"), carry=s("carry") + s("roll"),
                laufzeit=s("T"), **{f"r_{sid}": s(f"r_{sid}") for sid in defs})


def krd_anleihen(df: pd.DataFrame) -> dict[float, float]:
    """Key-Rate-Durations: Duration × Gewicht jeder Anleihe auf die benachbarten Stützstellen verteilt."""
    out = {float(T): 0.0 for T in C.LAUFZEITEN}
    if not len(df):
        return out
    tot = df["betrag"].sum()
    for _, z in df.iterrows():
        for T, a in auf_raster(float(z["T"])).items():
            out[T] += a * z["betrag"] / tot * z["D"]
    return out


def stress_anleihen(df: pd.DataFrame, par: dict) -> float:
    """Sofortige Wertänderung (% des Treasury-Teils) bei einer Kurvenbewegung (bp für fed/dgs2/dgs10)."""
    tot = df["betrag"].sum() or 1
    return float(sum(z["betrag"] / tot * (-z["D"] * dy_szenario(z["T"], par) / 100
                                          + 0.5 * z["konv"] * (dy_szenario(z["T"], par) / 1e4) ** 2 * 100) for _, z in df.iterrows()))


def konvexitaet(kupon: float, T: float, y: float) -> float:
    """Konvexität (Jahre²) numerisch aus dem Preis bei ±10 bp."""
    if T <= 0:
        return 0.0
    p0, pm, pp = P.preis(kupon, T, y), P.preis(kupon, T, y - 0.1), P.preis(kupon, T, y + 0.1)
    return (pm + pp - 2 * p0) / (p0 * 0.001 ** 2) if p0 else 0.0


def kennzahlen_kurz(df: pd.DataFrame) -> dict | None:
    """Betragsgewichtete Kurz-Kennzahlen des Treasury-Teils (aus anleihen_tabelle)."""
    if df is None or not len(df) or not df["betrag"].sum():
        return None
    w = df["betrag"] / df["betrag"].sum()
    return dict(n=len(df), betrag=float(df["betrag"].sum()), nominal=float(df["nominal"].sum()),
                kupon=float((df["kupon"] * w).sum()), kupons=float(df["kupons_sim"].sum()), marchzins=float(df["marchzins"].sum()),
                kupon_jahr=float((df["nominal"] * df["kupon"] / 100).sum()),
                restlaufzeit=float((df["T"] * w).sum()), duration=float((df["D"] * w).sum()), konv=float((df["konv"] * w).sum()),
                rendite=float((df["y"] * w).sum()), dv01=float(df["dv01"].sum()),
                kuerzeste=float(df["T"].min()), laengste=float(df["T"].max()))


def kpis_kurz(kz: dict, kauf: pd.Timestamp | None = None, ende: pd.Timestamp | None = None, bench: dict | None = None,
              bestand: bool = False, konv_b: float | None = None) -> list[tuple[str, str, str]]:
    """8 Kacheln: Kupons, Marchzins, Ø Restlaufzeit, Konvexität (+ Ø Kupon, Rendite, Duration, DV01)."""
    kauf = kauf or max(pd.Timestamp(C.SIMULATION_START), pd.Timestamp.today().normalize())
    ende = ende or pd.Timestamp(C.SIMULATION_ENDE)
    return [
        ("Kupons bis " + f"{ende:%d.%m.}", _usd(kz["kupons"]),
         ("seit Kauf" if bestand else f"Zahlungen {kauf:%d.%m.} – {ende:%d.%m.%Y}") + f" · {_usd(kz['kupon_jahr'])} pro Jahr"),
        ("Marchzins " + ("bezahlt" if bestand else f"beim Kauf {kauf:%d.%m.}"), _usd(kz["marchzins"]),
         "aufgelaufener Zins seit dem letzten Kupon (Dirty − Clean)"),
        ("Ø Restlaufzeit", f"{ui.de(kz['restlaufzeit'], 2)} J.",
         f"{ui.de(kz['kuerzeste'], 1)} – {ui.de(kz['laengste'], 1)} J." + (f" · Benchmark {ui.de(bench['laufzeit'], 1)} J." if bench else "")),
        ("Konvexität", ui.de(kz["konv"], 1), (f"Benchmark {ui.de(konv_b, 1)} · " if konv_b is not None else "")
         + "betragsgewichtet, ½ × K × Δy²"),
        ("Ø Kupon", f"{ui.de(kz['kupon'], 3)} %", f"{kz['n']} Anleihen · Nominal {_usd(kz['nominal'])}"),
        ("Ø Rendite (YTM)", f"{ui.de(kz['rendite'], 2)} %", "betragsgewichtet"),
        ("Mod. Duration", f"{ui.de(kz['duration'], 2)} J." , f"Benchmark {ui.de(bench['duration'], 2)} J." if bench else ""),
        ("DV01", _usd(kz["dv01"]), "Wertänderung je +1 bp (negativ)"),
    ]


def bestand_tabelle(b: pd.DataFrame, heute: pd.Timestamp | None = None, ende: pd.Timestamp | None = None) -> pd.DataFrame:
    """Treasuries im Bestand (portfolio.bewerte) → gleiche Spalten wie anleihen_tabelle für kennzahlen_kurz.
    Kupons = Zahlungen seit Kauf bis Simulationsende, Marchzins = beim Kauf bezahlt."""
    heute = (heute or pd.Timestamp.today()).normalize()
    ende = ende or pd.Timestamp(C.SIMULATION_ENDE)
    t = b[b["klasse"] == "Treasury"] if b is not None and len(b) else pd.DataFrame()
    zeilen = []
    for _, z in t.iterrows():
        fa, kupon = z.get("faelligkeit"), float(z.get("kupon") or 0)
        T = float(z.get("restlaufzeit") or 0)
        y = z.get("rendite")
        y = float(y) if y is not None and pd.notna(y) else kupon
        D = z.get("duration")
        kauf = pd.Timestamp(z["kaufdatum"])
        kup = kupon_termine(fa, kauf, ende) if pd.notna(fa) else []
        zeilen.append(dict(betrag=float(z["wert"]), nominal=float(z["menge"]), kupon=kupon, T=T, y=y,
                           D=float(D) if D is not None and pd.notna(D) else 0.0, konv=konvexitaet(kupon, T, y),
                           dv01=float(z["wert"]) * (float(D) if D is not None and pd.notna(D) else 0.0) / 1e4,
                           kupons_sim=float(z["menge"]) * kupon / 100 / 2 * len(kup),
                           marchzins=marchzins(float(z["menge"]), kupon, fa, kauf) if pd.notna(fa) else 0.0))
    return pd.DataFrame(zeilen)


def _usd(x, d=0) -> str:
    return "–" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{ui.de(x, d)} USD"


# ---------------------------------------------------------------------------
# Ansicht «Anleihen & Cash» auf der Seite «Analyse»
# ---------------------------------------------------------------------------
def bereich_analyse(ctx, fred: dict):
    import analyse as A
    import cashzins as CZ

    ss = st.session_state
    pf_name = (ss.get("an_pf", {}).get(ss.get("an_aktiv"), {}) or {}).get("name", "Analyseportfolio")
    R = SP.Sammler("Anleihen & Cash", f"Treasury-Teil und Cash von «{pf_name}»: Risiko, Zinserwartungen, Mehrrendite vs. Benchmark",
                   kurz="Anleihen")
    ph = st.empty()
    k, k_alt = kurve(fred), kurve(fred, 30)
    if len(k) < 5:
        st.warning("Zu wenige Zinsdaten von FRED. Bitte FRED-Schlüssel in den Secrets prüfen.")
        return
    defs, szen, ips, a = ctx.szdef, ctx.szen, ctx.ips, ctx.alloc
    bench = benchmark_info(k, ctx.einst.get("bond_benchmark", C.BOND_BENCHMARK_DEFAULT))
    tab = tabelle(k, defs, szen)
    b = A.budget(ss)
    heute = pd.Timestamp.today().normalize()
    kauf = max(pd.Timestamp(C.SIMULATION_START), heute)
    ende = pd.Timestamp(C.SIMULATION_ENDE)
    df = anleihen_tabelle(A._treas_df(ss["an_treas"]), k, defs, szen, heute, kauf, ende) if "an_treas" in ss else pd.DataFrame()
    m_b = mix(tab, bench["gewichte"], defs)

    if len(df):
        m_p = mix_anleihen(df, defs)
        w_plan = plan_aus_anleihen(df)
        betrag = float(df["betrag"].sum())
        quelle = f"{len(df)} Anleihen aus «{pf_name}»"
    else:
        w_plan, info = gewichte_aktiv(ctx, tab, bench)
        m_p = mix(tab, w_plan, defs)
        betrag = b["budget_t"] or C.STARTKAPITAL * a["anleihen"] / 100
        quelle = f"noch keine Anleihen in «{pf_name}» – Platzhalter: {info}"
        st.info(f"In «{pf_name}» sind noch keine Treasuries erfasst. Die Auswertung zeigt solange einen Platzhalter ({info}). "
                "Anleihen eintragen: Ansicht «Portfolio bearbeiten & analysieren» oder unten «Treasuries aus Excel übernehmen».",
                icon=":material/info:")
    dv01 = betrag * m_p["duration"] / 1e4
    kpis = [
        ("Treasury-Teil", _usd(betrag), f"{ui.de(betrag / b['kapital'] * 100 if b['kapital'] else 0, 1)} % des Kapitals · "
                                       f"Budget {_usd(b['budget_t'])} · {quelle}"),
        ("Duration", f"{ui.de(m_p['duration'], 2)} J.",
         f"Benchmark {ui.de(bench['duration'], 2)} J. · Taktik {ui.de(a.get('duration') or 0, 1)} J. · IPS "
         f"{ui.de(ips['duration_min'], 0)}–{ui.de(ips['duration_max'], 0)} J."),
        ("Rendite (Yield)", f"{ui.de(m_p['rendite'], 2)} %", f"Benchmark {ui.de(m_b['rendite'], 2)} % · DV01 {_usd(dv01)} je bp"),
        ("Erwartete Mehrrendite 1 Mt.", f"{ui.de((m_p['erw'] - m_b['erw']) * 100, 0, True)} bp",
         f"Anleihen {ui.de(m_p['erw'], 2, True)} % vs. Benchmark {ui.de(m_b['erw'], 2, True)} % · ≈ "
         f"{_usd((m_p['erw'] - m_b['erw']) / 100 * betrag)}"),
    ]
    ui.kopf_kpis(kpis)
    R.kpis(kpis)
    kz_ = kennzahlen_kurz(df) if len(df) else None
    if kz_:
        k8 = kpis_kurz(kz_, kauf, ende, bench, konv_b=m_b["konv"])
        ui.kopf_kpis(k8[:4])
        R.kpis(k8[:4])
    if not ips["duration_min"] <= m_p["duration"] <= ips["duration_max"]:
        st.error(f"Die Duration liegt ausserhalb des IPS-Bands ({ui.de(ips['duration_min'], 0)}–{ui.de(ips['duration_max'], 0)} J.).")
    if a.get("duration") and abs(a["duration"] - m_p["duration"]) > 0.5:
        st.info(f"Die Taktik nennt eine Duration von {ui.de(a['duration'], 1)} J., die Anleihen ergeben {ui.de(m_p['duration'], 2)} J. "
                "Eines von beiden anpassen.")
    if len(df) and b["budget_t"] and abs(betrag - b["budget_t"]) > max(1000, b["budget_t"] * 0.01):
        st.caption(f"Hinweis: Anleihen {_usd(betrag)} vs. Treasury-Budget {_usd(b['budget_t'])} "
                   f"({ui.de((betrag - b['budget_t']) / b['budget_t'] * 100, 1, True)} %). Gewichte und Duration beziehen sich auf die "
                   "erfassten Anleihen.")
    st.caption(f"Benchmark: {bench['name']} (wählbar unter «Einstellungen»; den genauen Treasury-Index der Dozenten-App kennen wir "
               "nicht). Daten: FRED-Zinskurve DGS1MO … DGS30. Horizont 1 Monat, Zinserwartungen aus den Szenario-Parametern.")
    A._treasury_bereich()

    t1, t2, t3, t4, t5 = st.tabs(["Unsere Anleihen", "Positionierung vs. Benchmark", "Stresstest", "Zins auf Cash",
                                  "Zinskurve & Ideen"])

    # --- 1. Anleihen einzeln --------------------------------------------------
    with t1:
        if len(df):
            pw = {sid: float(szen.loc[sid, "p"]) / 100 for sid in defs}
            out = pd.DataFrame({
                "Anleihe": df["name"], "ISIN": df["isin"], "Band": df["band"],
                "Kupon": df["kupon"].map(lambda x: f"{ui.de(x, 3)} %"),
                "Fälligkeit": df["faellig"].map(lambda d: f"{d:%d.%m.%Y}" if pd.notna(d) else "–"),
                "Restlaufzeit": df["T"].map(lambda x: f"{ui.de(x, 1)} J."),
                "Betrag": df["betrag"].map(_usd), "Gewicht": df["gewicht"].map(lambda x: f"{ui.de(x, 1)} %"),
                "Kurs": df["kurs"].map(lambda x: f"{ui.de(x, 3)} %"),
                "Rendite": df["y"].map(lambda x: f"{ui.de(x, 2)} %"),
                "Spread zur Kurve": df["spread"].map(lambda x: f"{ui.de(x, 0, True)} bp"),
                "Mod. Duration": df["D"].map(lambda x: ui.de(x, 2)),
                "Konvexität": df["konv"].map(lambda x: ui.de(x, 1)),
                "DV01": df["dv01"].map(_usd),
                "Carry + Roll / Mt.": [f"{ui.de((c + r_) * 100, 0)} bp · {_usd((c + r_) / 100 * w)}"
                                       for c, r_, w in zip(df["carry"], df["roll"], df["betrag"])],
                "Puffer": df["breakeven"].map(lambda x: f"{ui.de(x, 0)} bp"),
                "Erw. 1 Mt.": df["erw"].map(lambda x: f"{ui.de(x, 2, True)} %"),
                **{defs[s]["kurz"]: df[f"r_{s}"].map(lambda x: f"{ui.de(x, 2, True)} %") for s in defs},
                "Kupons bis 28.02.": df["kupons_sim"].map(_usd),
                "Kupontermine": df["kupon_daten"].replace("", "–"),
                f"Marchzins {kauf:%d.%m.}": df["marchzins"].map(_usd),
                "Berechnung": df["art"]})
            st.dataframe(out, hide_index=True, width="stretch",
                         column_config={"Berechnung": st.column_config.TextColumn(width="medium")})
            kup_tot, mz_tot = float(df["kupons_sim"].sum()), float(df["marchzins"].sum())
            p_txt = " · ".join(f"{defs[s_]['kurz']} {pw[s_] * 100:.0f} %" for s_ in defs)
            st.caption(f"Marchzins = aufgelaufener Zins seit dem letzten Kupon, den ihr beim Kauf am {kauf:%d.%m.} zusätzlich zum "
                       "Clean-Kurs bezahlt (Dirty = Clean + Marchzins). Rechnet die Dozenten-App mit Dirty-Preisen, fehlt dieser "
                       "Betrag im Cash. Konvexität: Wertänderung ≈ −Duration × Δy + ½ × Konvexität × Δy².")
            st.caption("Rendite = Yield to Maturity (halbjährlich). Mit «Kurs %» aus der Liste rechnet die App die Rendite aus dem Kurs "
                       "und hält den Abstand (Spread) zur FRED-Kurve fest; ohne Kurs liegt die Anleihe auf der Kurve. Carry + Roll = "
                       "Ertrag in einem Monat bei unveränderter Kurve. Puffer = so viele bp darf die Rendite steigen, bevor die Anleihe "
                       "im Monat verliert. Szenario-Spalten = Monatsrendite je Szenario "
                       f"({p_txt}).")
            R.h("Unsere Anleihen")
            R.tabelle(out.drop(columns=["ISIN", "Berechnung", "Puffer", "Kupontermine"]))
            # Fälligkeitsprofil
            prof = df.assign(b_=df["band"]).groupby("b_")["betrag"].sum()
            fig = go.Figure(go.Bar(x=list(prof.index), y=list(prof.values / betrag * 100), marker_color=ui.BLAU,
                                   text=[f"{ui.de(v / betrag * 100, 0)} %" for v in prof.values], textposition="outside"))
            fig.update_layout(height=240, margin=dict(l=8, r=8, t=24, b=8), plot_bgcolor="white",
                              title=dict(text="Laufzeitprofil (Anteil am Treasury-Teil)", font=dict(size=12)))
            fig.update_yaxes(ticksuffix=" %", gridcolor="#EEF0F3")
            ui.zeige(fig)
        else:
            st.caption("Noch keine Anleihen erfasst.")

    # --- 2. Positionierung ----------------------------------------------------
    with t2:
        st.caption(f"Grundlage: {quelle}.")
        zeilen = [("Duration", f"{ui.de(m_p['duration'], 2)} J.", f"{ui.de(m_b['duration'], 2)} J.",
                   f"{ui.de(m_p['duration'] - m_b['duration'], 2, True)} J."),
                  ("Rendite (Yield)", f"{ui.de(m_p['rendite'], 2)} %", f"{ui.de(m_b['rendite'], 2)} %",
                   f"{ui.de((m_p['rendite'] - m_b['rendite']) * 100, 0, True)} bp"),
                  ("Carry + Roll / Monat", f"{ui.de(m_p['carry'] * 100, 0)} bp", f"{ui.de(m_b['carry'] * 100, 0)} bp",
                   f"{ui.de((m_p['carry'] - m_b['carry']) * 100, 0, True)} bp"),
                  ("DV01 auf den Treasury-Teil", _usd(dv01), _usd(betrag * m_b["duration"] / 1e4), ""),
                  ("Erwartete Rendite 1 Mt.", f"{ui.de(m_p['erw'], 2, True)} %", f"{ui.de(m_b['erw'], 2, True)} %",
                   f"{ui.de((m_p['erw'] - m_b['erw']) * 100, 0, True)} bp")]
        zeilen += [(f"Szenario {defs[s]['kurz']} ({szen.loc[s, 'p']:.0f} %)", f"{ui.de(m_p[f'r_{s}'], 2, True)} %",
                    f"{ui.de(m_b[f'r_{s}'], 2, True)} %", f"{ui.de((m_p[f'r_{s}'] - m_b[f'r_{s}']) * 100, 0, True)} bp") for s in defs]
        vgl = pd.DataFrame(zeilen, columns=["Kennzahl", "Unsere Anleihen", "Benchmark", "Differenz"])
        krd_p = krd_anleihen(df) if len(df) else {float(T): w_plan.get(T, 0) * tab.loc[T, "D"] for T in C.LAUFZEITEN}
        krd_b = {float(T): bench["gewichte"].get(T, 0) * tab.loc[T, "D"] for T in C.LAUFZEITEN}
        c1, c2 = st.columns([1, 1.1])
        with c1:
            st.markdown("**Unsere Anleihen gegen die Benchmark**")
            st.dataframe(vgl, hide_index=True, width="stretch")
        with c2:
            st.markdown("**Key-Rate-Durations** (aktiv = unsere − Benchmark)")
            xs = [_lz(T) for T in C.LAUFZEITEN]
            fig = go.Figure([go.Bar(name="Unsere", x=xs, y=[krd_p[float(T)] for T in C.LAUFZEITEN], marker_color=ui.BLAU),
                             go.Bar(name="Benchmark", x=xs, y=[krd_b[float(T)] for T in C.LAUFZEITEN], marker_color="#94A3B8"),
                             go.Scatter(name="Aktiv", x=xs, y=[krd_p[float(T)] - krd_b[float(T)] for T in C.LAUFZEITEN],
                                        mode="markers+lines", line=dict(color=ui.GOLD, width=2))])
            fig.update_layout(barmode="group", height=280, margin=dict(l=8, r=8, t=10, b=8), plot_bgcolor="white",
                              legend=dict(orientation="h", y=-0.2))
            fig.update_yaxes(title="J.", gridcolor="#EEF0F3")
            ui.zeige(fig)
        krd = pd.DataFrame({"Laufzeit": [_lz(T) for T in C.LAUFZEITEN],
                            "Unsere": [ui.de(krd_p[float(T)], 2) for T in C.LAUFZEITEN],
                            "Benchmark": [ui.de(krd_b[float(T)], 2) for T in C.LAUFZEITEN],
                            "Aktiv": [ui.de(krd_p[float(T)] - krd_b[float(T)], 2, True) for T in C.LAUFZEITEN]})
        st.caption("Key-Rate-Duration: Jede Anleihe wird mit ihrer Duration auf die zwei benachbarten Stützstellen der Kurve verteilt. "
                   "Positiv aktiv = an dieser Laufzeit zinsempfindlicher als die Benchmark (gewinnt, wenn diese Rendite fällt). "
                   "Gleiches Vorzeichen überall = Durationswette, wechselndes Vorzeichen = Kurvenwette.")
        R.h("Unsere Anleihen gegen die Benchmark")
        R.tabelle(vgl)
        R.h("Key-Rate-Durations")
        R.tabelle(krd)

        st.markdown("**Wirkung relativ zur Benchmark – Zerlegung je Szenario (1 Monat)**")
        rel = relativ(tab, w_plan, bench, defs, szen, ctx.sig.get("dgs10", (25, ""))[0], m_p=m_p)
        zr = [{"Szenario": f"{defs[s]['kurz']} ({szen.loc[s, 'p']:.0f} %)",
               "Δ Rendite (Benchmark-Laufzeit)": f"{ui.de(v['dy'], 0, True)} bp",
               "Durationseffekt": f"{ui.de(v['duration'], 0, True)} bp", "Kurveneffekt": f"{ui.de(v['kurve'], 0, True)} bp",
               "Carry/Roll": f"{ui.de(v['carry'], 0, True)} bp", "Total vs. Benchmark": f"{ui.de(v['gesamt'], 0, True)} bp",
               "in USD": _usd(v["gesamt"] / 1e4 * betrag)} for s, v in rel["szen"].items()]
        e = rel["erw"]
        zr.append({"Szenario": "Gewichtet", "Δ Rendite (Benchmark-Laufzeit)": "", "Durationseffekt": f"{ui.de(e['duration'], 0, True)} bp",
                   "Kurveneffekt": f"{ui.de(e['kurve'], 0, True)} bp", "Carry/Roll": f"{ui.de(e['carry'], 0, True)} bp",
                   "Total vs. Benchmark": f"{ui.de(e['gesamt'], 0, True)} bp", "in USD": _usd(e["gesamt"] / 1e4 * betrag)})
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
        if len(df):
            gespeichert, info_plan = plan_lesen(ctx.tabellen.get("einstellungen"), bench)
            bestand = plan_aus_bestand(ctx.tabellen)
            st.markdown("**Für andere Seiten verwenden**")
            st.caption("Der Textbaustein auf «Erwartungen (Dozent)» rechnet mit dem Bestand, sobald Treasuries gekauft sind"
                       + (" (aktuell: Bestand)." if bestand else f". Bis dahin mit dem gespeicherten Laufzeiten-Plan – {info_plan}."))
            if st.button(f"Laufzeiten von «{pf_name}» als Anleihen-Plan fürs Team speichern", icon=":material/save:",
                         disabled=ctx.nur_lesen, key="anl_plan_save"):
                ctx.speichern("einstellungen", [dict(zeit=jetzt(), person=ctx.person, schluessel="bond_plan",
                                                     wert=json.dumps({str(T): v for T, v in w_plan.items()}))],
                              "Anleihen-Plan gespeichert.")

    # --- 3. Stresstest --------------------------------------------------------
    with t3:
        schocks = [("Parallel −100 bp", -100, -100), ("Parallel −50 bp", -50, -50), ("Parallel +50 bp", 50, 50),
                   ("Parallel +100 bp", 100, 100), ("Steepener (2 J. −25, 10 J. +25)", -25, 25), ("Flattener (2 J. +25, 10 J. −25)", 25, -25),
                   ("Bear Steepener (2 J. +10, 10 J. +50)", 10, 50), ("Bull Flattener (2 J. −10, 10 J. −50)", -10, -50)]
        zeilen = []
        for name, d2, d10 in schocks:
            par = dict(fed=d2, dgs2=d2, dgs10=d10)
            if len(df):
                wp = stress_anleihen(df, par)
            else:
                tot = sum(w_plan.values()) or 1
                wp = sum(v / tot * (-laufzeit_kz(k, T)["D"] * dy_szenario(T, par) / 100
                                    + 0.5 * laufzeit_kz(k, T)["konv"] * (dy_szenario(T, par) / 1e4) ** 2 * 100) for T, v in w_plan.items())
            wb = sum(v * (-laufzeit_kz(k, T)["D"] * dy_szenario(T, par) / 100
                          + 0.5 * laufzeit_kz(k, T)["konv"] * (dy_szenario(T, par) / 1e4) ** 2 * 100) for T, v in bench["gewichte"].items())
            zeilen.append((name, f"{ui.de(wp, 2, True)} %", _usd(wp / 100 * betrag), f"{ui.de(wb, 2, True)} %",
                           f"{ui.de((wp - wb) * 100, 0, True)} bp", _usd((wp - wb) / 100 * betrag)))
        stress = pd.DataFrame(zeilen, columns=["Schock (sofort)", "Unsere Anleihen", "in USD", "Benchmark", "Differenz", "Differenz USD"])
        st.dataframe(stress, hide_index=True, width="stretch")
        if len(df):
            einzeln = pd.DataFrame({"Anleihe": df["name"],
                                    **{n: [_usd(z["betrag"] * (-z["D"] * dy_szenario(z["T"], dict(fed=d2, dgs2=d2, dgs10=d10)) / 1e4))
                                           for _, z in df.iterrows()] for n, d2, d10 in schocks[2:4] + schocks[4:6]}})
            with st.expander("Wirkung je Anleihe (USD, ohne Konvexität)"):
                st.dataframe(einzeln, hide_index=True, width="stretch")
        st.caption("Sofortige Kurvenbewegung, Spreads fest. 2 J. und 10 J. wie angegeben, kurzes Ende wie 2 J., dazwischen linear, "
                   "über 10 J. wie 10 J. Inkl. Konvexität.")
        R.h("Stresstest")
        R.tabelle(stress)

    # --- 4. Zins auf Cash -----------------------------------------------------
    with t4:
        _cash_bereich(ctx, fred, k, b, df, kauf, ende, R)

    # --- 5. Kurve & Ideen -----------------------------------------------------
    with t5:
        st.markdown("**Zinskurve heute, vor einem Monat und eure Erwartung für das Monatsende** · Punkte = unsere Anleihen")
        xs = sorted(k)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=xs, y=[k_alt.get(x) for x in xs], name="vor 1 Monat", line=dict(color="#A0A8B4", dash="dot")))
        fig.add_trace(go.Scatter(x=xs, y=[k[x] for x in xs], name="heute", line=dict(color=ui.BLAU, width=3)))
        pw_ = {sid: szen.loc[sid, "p"] / 100 for sid in defs}
        fig.add_trace(go.Scatter(x=xs, y=[k[x] + sum(pw_[s] * dy_szenario(x, defs[s]["param"]) for s in defs) / 100 for x in xs],
                                 name="Erwartung Monatsende (gewichtet)", line=dict(color=ui.GOLD, width=2)))
        for sid in defs:
            fig.add_trace(go.Scatter(x=xs, y=[k[x] + dy_szenario(x, defs[sid]["param"]) / 100 for x in xs], name=defs[sid]["kurz"],
                                     line=dict(color=C.SZEN_FARBE.get(sid, "#888"), width=1), opacity=0.55, visible="legendonly"))
        if len(df):
            fig.add_trace(go.Scatter(x=df["T"].clip(lower=1 / 12), y=df["y"], mode="markers", name="Unsere Anleihen",
                                     text=df["name"], hovertemplate="%{text}<br>%{x:.1f} J. · %{y:.2f} %<extra></extra>",
                                     marker=dict(size=(df["gewicht"].clip(lower=2) ** 0.5 * 4).tolist(), color=ui.PETROL,
                                                 line=dict(color="white", width=1))))
        fig.update_layout(height=340, margin=dict(l=8, r=8, t=10, b=8), plot_bgcolor="white", legend=dict(orientation="h", y=-0.2))
        fig.update_xaxes(type="log", tickvals=xs, ticktext=[_lz(x) for x in xs], gridcolor="#EEF0F3")
        fig.update_yaxes(ticksuffix=" %", gridcolor="#EEF0F3")
        ui.zeige(fig)

        st.markdown("**Laufzeiten im Vergleich (1 Monat)**")
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
        st.caption("Markt (Forward) = Renditeänderung, die der Markt über die Kurve einpreist. Mehrrendite entsteht dort, wo eure "
                   "Erwartung vom Markt abweicht.")
        R.h("Laufzeiten im Vergleich (1 Monat)")
        R.tabelle(out.drop(columns=["Δ 1 Mt."]))

        idee = ideen(tab, bench, defs, ips["duration_min"], ips["duration_max"])
        if len(idee):
            st.markdown("**Wo ist Mehrrendite möglich?** Laufzeiten-Mischungen (1–2 Laufzeiten, im IPS-Durationsband, höchstens −1 % "
                        "Minderrendite im schlechtesten Szenario), sortiert nach erwarteter Mehrrendite plus ¼ × schlechtestes Szenario. "
                        "Letzte Zeile: eure Anleihen zum Vergleich.")
            iout = pd.DataFrame({"Strategie": idee["Strategie"], "Duration": idee["duration"].map(lambda x: f"{ui.de(x, 2)} J."),
                                 "Erw. Mehrrendite": idee["erw_aktiv"].map(lambda x: f"{ui.de(x * 100, 0, True)} bp"),
                                 **{defs[s]["kurz"]: idee[f"a_{s}"].map(lambda x: f"{ui.de(x * 100, 0, True)} bp") for s in defs},
                                 "Schlechtestes Szenario": idee["worst_aktiv"].map(lambda x: f"{ui.de(x * 100, 0, True)} bp")})
            akt_ = {s: m_p[f"r_{s}"] - m_b[f"r_{s}"] for s in defs}
            iout = pd.concat([iout, pd.DataFrame([{"Strategie": "→ Unsere Anleihen", "Duration": f"{ui.de(m_p['duration'], 2)} J.",
                                                   "Erw. Mehrrendite": f"{ui.de((m_p['erw'] - m_b['erw']) * 100, 0, True)} bp",
                                                   **{defs[s]["kurz"]: f"{ui.de(v * 100, 0, True)} bp" for s, v in akt_.items()},
                                                   "Schlechtestes Szenario": f"{ui.de(min(akt_.values()) * 100, 0, True)} bp"}])],
                             ignore_index=True)
            st.dataframe(iout, hide_index=True, width="stretch")
            R.h("Ideen: Laufzeiten-Mischungen")
            R.tabelle(iout)
        gew = ctx.gew["param"]
        kurve_erw = gew.get("dgs10", 0) - gew.get("dgs2", 0)
        fwd_kurve = float(tab.loc[10.0, "fwd_dy"] - tab.loc[2.0, "fwd_dy"])
        txt = [f"**Duration:** Ihr erwartet für 10 J. {ui.de(gew.get('dgs10', 0), 0, True)} bp, der Markt preist "
               f"{ui.de(tab.loc[10.0, 'fwd_dy'], 0, True)} bp ein. " +
               ("Ihr seid bullisher als der Markt → Duration über der Benchmark lohnt sich, wenn ihr recht habt."
                if gew.get("dgs10", 0) < tab.loc[10.0, "fwd_dy"] - 5 else
                "Ihr seid bearisher als der Markt → Duration unter der Benchmark schützt."
                if gew.get("dgs10", 0) > tab.loc[10.0, "fwd_dy"] + 5 else
                "Kaum Abweichung zum Markt → Duration nahe Benchmark, Mehrrendite eher über Carry und Kurve.")
               + f" Eure Anleihen: {ui.de(m_p['duration'] - m_b['duration'], 1, True)} J. aktive Duration.",
               f"**Kurve (10 J. − 2 J.):** eure Erwartung {ui.de(kurve_erw, 0, True)} bp, Markt {ui.de(fwd_kurve, 0, True)} bp. " +
               ("→ **Steepener** (kurze Laufzeiten über-, lange untergewichten, gleiche Duration)." if kurve_erw > fwd_kurve + 5 else
                "→ **Flattener** (lange Laufzeiten über-, kurze untergewichten, gleiche Duration)." if kurve_erw < fwd_kurve - 5 else
                "→ keine klare Kurvenwette.")]
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
        with st.expander("Methodik und Quellen"):
            st.markdown(__doc__.split("\n", 2)[2])
            st.markdown("Quellen: FRED (Board of Governors / U.S. Treasury) DGS1MO, DGS3MO, DGS6MO, DGS1, DGS2, DGS3, DGS5, DGS7, DGS10, "
                        "DGS20, DGS30, T5YIE, SOFR · [fred.stlouisfed.org](https://fred.stlouisfed.org/categories/115)")
    SP.knopf(ph, R, "anleihen")


def _cash_bereich(ctx, fred: dict, k: dict, b: dict, df: pd.DataFrame, kauf: pd.Timestamp, ende: pd.Timestamp, R):
    """Zins auf dem geplanten Cash (SOFR − 0,5 %) bis Simulationsende, nach Szenario, und Vergleich mit T-Bills."""
    import cashzins as CZ

    a = CZ.aktuell(fred)
    if a is None:
        st.warning("Kein SOFR von FRED – Zins auf Cash kann nicht gerechnet werden (FRED-Schlüssel prüfen).")
        return
    defs, szen = ctx.szdef, ctx.szen
    cash = max(b["frei"], 0.0)
    kup = float(df["kupons_sim"].sum()) if len(df) else 0.0
    pg = CZ.prognose(cash, a["sofr"], defs, szen, von=kauf, bis=ende)
    tb3 = y_bei(k, 0.25)
    tb1 = y_bei(k, 1 / 12)
    kpis = [
        ("Satz heute", f"{ui.de(a['netto'], 2)} %", f"{a['quelle']} {ui.de(a['sofr'], 2)} % ({a['datum']:%d.%m.%Y}) − "
                                                    f"{ui.de(CZ.MARGE, 1)} % Bankmarge"),
        ("Geplantes Cash", _usd(cash), f"{ui.de(cash / b['kapital'] * 100 if b['kapital'] else 0, 2)} % · Kapital − Aktien − Treasuries"),
        (f"Zins {kauf:%d.%m.} – {ende:%d.%m.%Y}", _usd(pg["total"]),
         f"szenariogewichtet · SOFR unverändert {_usd(pg.get('unveraendert'))} · {pg.get('tage', 0)} Tage"),
        ("Cash vs. 3-Mt.-T-Bill", f"{ui.de(((tb3 or 0) - a['netto']) * 100, 0, True)} bp" if tb3 else "–",
         f"T-Bill {ui.de(tb3, 2)} % vs. Cash netto {ui.de(a['netto'], 2)} %" if tb3 else ""),
    ]
    ui.kopf_kpis(kpis)
    R.h("Zins auf Cash")
    R.kpis(kpis)
    c1, c2 = st.columns([1.1, 1])
    with c1:
        m = pg["monate"]
        if len(m):
            mt = pd.DataFrame({"Monat": m["Monat"], "Tage": m["Tage"], "Ø Satz netto": m["Ø Satz (gewichtet)"].map(lambda x: f"{ui.de(x, 2)} %"),
                               "Zins (gewichtet)": m["Zins (gewichtet)"].map(_usd),
                               "SOFR unverändert": m["Zins (SOFR unverändert)"].map(_usd)})
            st.markdown("**Erwarteter Zins je Monat**")
            st.dataframe(mt, hide_index=True, width="stretch")
            R.tabelle(mt)
        sz = pd.DataFrame([{"Szenario": f"{defs[s]['kurz']} ({szen.loc[s, 'p']:.0f} %)",
                            "Fed je Sitzung": f"{ui.de(float(defs[s]['param'].get('fed', 0) or 0), 0, True)} bp",
                            "Zins bis 28.02.": _usd(v)} for s, v in pg["szen"].items()])
        if len(sz):
            st.markdown("**Nach Szenario**")
            st.dataframe(sz, hide_index=True, width="stretch")
            R.tabelle(sz)
    with c2:
        s = a["reihe"]
        s = s[s.index >= s.index[-1] - pd.Timedelta(days=365)]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=s.index, y=s, name="SOFR", line=dict(color=ui.STAHL, width=1.6)))
        fig.add_trace(go.Scatter(x=s.index, y=(s - CZ.MARGE).clip(lower=0), name="Cash netto (SOFR − 0,5 %)",
                                 line=dict(color=ui.BLAU, width=2.4)))
        dgs3 = fred.get("DGS3MO")
        if dgs3 is not None and len(dgs3.dropna()):
            t3 = dgs3.dropna()
            t3 = t3[t3.index >= s.index[0]]
            fig.add_trace(go.Scatter(x=t3.index, y=t3, name="3-Mt.-T-Bill", line=dict(color=ui.GOLD, width=1.4, dash="dot")))
        fig.update_layout(height=300, margin=dict(l=8, r=8, t=10, b=8), plot_bgcolor="white", hovermode="x unified",
                          legend=dict(orientation="h", y=-0.2))
        fig.update_yaxes(ticksuffix=" %", gridcolor="#EEF0F3")
        ui.zeige(fig)
    txt = []
    if tb3:
        diff = tb3 - a["netto"]
        tage = max((ende - kauf).days, 0)
        txt.append(f"**Cash oder T-Bills?** Wegen der Bankmarge bringt Cash {ui.de(a['netto'], 2)} %, eine 3-Mt.-T-Bill "
                   f"{ui.de(tb3, 2)} %" + (f" (1 Mt. {ui.de(tb1, 2)} %)" if tb1 else "") + ". Jede Million, die statt als Cash in "
                   f"kurzen T-Bills liegt, bringt bis {ende:%d.%m.} ≈ {_usd(diff / 100 * 1e6 * tage / 360)} "
                   f"{'mehr' if diff > 0 else 'weniger'}. T-Bills zählen aber zur Treasury-Quote (IPS-Band und Benchmark), nicht zum Cash – "
                   "Cash nur so hoch halten, wie es IPS und Liquiditätsbedarf verlangen.")
    if kup:
        txt.append(f"**Kupons:** Bis {ende:%d.%m.%Y} zahlen eure Anleihen ≈ {_usd(kup)} Kupons aus. Sie landen im Cash und werden ab "
                   "dann ebenfalls mit SOFR − 0,5 % verzinst (im Portfolio-Tracker noch als «Kuponerträge» gezeigt).")
    fed_ = [f"{m_['fed_sitzung']}" for m_ in C.MONATE if m_.get("fed_sitzung")]
    txt.append(f"**Annahmen:** Act/360 auf dem Cash am Tagesende, monatliche Gutschrift, keine Steuern und Transaktionskosten. "
               f"SOFR folgt der Fed: Veränderung gemäss Szenario-Parameter «Fed» ab dem Tag nach der Sitzung ({', '.join(fed_)}). "
               f"Cash bleibt bis {ende:%d.%m.%Y} gleich hoch. Quelle: [FRED SOFR]({CZ.QUELLE_URL}).")
    for t in txt:
        st.markdown(t)
        R.text(t)
