"""Benchmark-Vergleich (Update 18, Wunsch Sophy 28.11.2026): unser Portfolio jederzeit neben dem Benchmark-Portfolio des
Dozenten (50 % S&P 500 / 50 % US-Treasuries, gleiches Startkapital).

Zwei Rechenarten:
- **Realisiert seit dem ersten Trade** (nur Bestand): Tageswerte je Anlageklasse aus den Transaktionen (wie der Tracker,
  inkl. Zins auf Cash und Kupons), Käufe/Verkäufe als Zu-/Abflüsse am Tagesanfang (gehandelt wird zum Eröffnungskurs).
- **Rückblick mit heutigen Gewichten** (Bestand oder Analyseportfolio): konstante Gewichte über 3 Monate bis 3 Jahre,
  gleiche Rechnung wie die Analyse (`analyse.rechne`).

Benchmark: SPY + Treasury-Proxy der Einstellungen (Standard GOVT), täglich 50/50 rebalanciert (`analyse.benchmark_reihe`).
Titelgewichte des Aktienteils aus der Datei der Dozenten-App (`benchmark.py`).

Attribution (Brinson-Fachler auf Anlageklassen, täglich, mit Carino verknüpft → Summe = Mehrrendite exakt):
  Allokation je Klasse  = (w_P − w_B) × (r_B,Klasse − r_B)
  Auswahl je Klasse     = w_P × (r_P,Klasse − r_B,Klasse)
  Auswahl Aktien zerlegt in Sektorwetten = w_P,Aktien × Σ_s (v_s − u_s) × (r_Sektor-ETF − r_SPY) und Titelauswahl (Rest).
"""
from __future__ import annotations

import io
import json
import math
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import analyse as A
import benchmark as BM
import cashzins as CZ
import portfolio as P
from makro import config as C
from makro import ui

BESTAND = "__bestand__"
FARBE_P, FARBE_B = ui.BLAU, A.BENCH_FARBE
INDEX_SEKTOR = "Index-Platzhalter"
MONATSNAME = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November",
              "Dezember"]
ZEITRAUM = {"start": "Seit dem ersten Trade (realisiert)", "3m": "Rückblick 3 Mt.", "6m": "Rückblick 6 Mt.",
            "1j": "Rückblick 1 J.", "3j": "Rückblick 3 J."}
MONATE_ZR = {"3m": 3, "6m": 6, "1j": 12, "3j": 36}
EFFEKTE = [  # (Schlüssel, Bezeichnung, Erklärung)
    ("alloc_aktien", "Aktienquote", "(unsere Aktienquote − 50 %) × (S&P 500 − Benchmark)"),
    ("alloc_treas", "Treasury-Quote", "(unsere Treasury-Quote − 50 %) × (Treasury-Proxy − Benchmark)"),
    ("alloc_cash", "Cash-Quote", "unsere Cash-Quote × (Zins auf Cash − Benchmark)"),
    ("sektor", "Sektorwetten im Aktienteil", "Über-/Untergewichte je Sektor × (Sektor-ETF − S&P 500)"),
    ("titel", "Titelauswahl im Aktienteil", "unsere Titel gegenüber ihrem Sektor (Rest der Aktienauswahl)"),
    ("sel_treas", "Auswahl Treasuries", "unsere Anleihen gegenüber dem Treasury-Proxy (Duration, Kurve, Kupons)"),
    ("sel_cash", "Cash-Verzinsung", "Zinsgutschrift auf unserem Cash gegenüber SOFR − 0,5 % täglich"),
]


def _usd(x, d=0) -> str:
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{ui.de(x, d)} USD"


def _sektor(s) -> str:
    s = str(s or "").strip()
    if s in C.SEKTOREN:
        return s
    return P.GICS_DE.get(s) or P.YAHOO_DE.get(s) or BM.sektor_app(s) if s else "Unbekannt"


# ---------------------------------------------------------------------------
# Rechenkern (ohne Streamlit, testbar)
# ---------------------------------------------------------------------------
def tagesdaten_bestand(tx: pd.DataFrame, kurse: dict, fred: dict, kapital: float, bis: pd.Timestamp | None = None) -> pd.DataFrame:
    """Werktage ab dem ersten Trade (plus Vortag mit dem Startkapital): Wert je Klasse am Tagesende, Zu-/Abflüsse des Tages
    je Klasse und Wert/Fluss je Sektor im Aktienteil. Spalten: aktien, treas, cash, f_aktien, f_treas, s|<Sektor>, fs|<Sektor>."""
    if tx is None or not len(tx):
        return pd.DataFrame()
    ende = pd.Timestamp(bis or pd.Timestamp.today()).normalize()
    tage = pd.bdate_range(tx["datum"].min().normalize(), max(ende, tx["datum"].min().normalize()))
    cz = CZ.zins_reihe(tx, kapital, fred, tage[-1])["zins_total"]
    tag_tx = tx["datum"].dt.normalize()
    zeilen = [dict(datum=tage[0] - pd.offsets.BDay(1), aktien=0.0, treas=0.0, cash=float(kapital), f_aktien=0.0, f_treas=0.0)]
    for d in tage:
        pos, cash_delta, _ = P.positionen(tx, d)
        b = P.bewerte(pos, kurse, fred, d)
        z = dict(datum=d, aktien=0.0, treas=0.0, cash=float(kapital + cash_delta + float(cz.get(d, 0.0))), f_aktien=0.0, f_treas=0.0)
        if len(b):
            ak = b[b["klasse"] == "Aktie"]
            tr = b[b["klasse"] == "Treasury"]
            z["aktien"] = float(ak["wert"].sum())
            z["treas"] = float(tr["wert"].sum() + tr["zins"].sum())
            for s, w in ak.groupby(ak["sektor"].map(_sektor))["wert"].sum().items():
                z[f"s|{s}"] = float(w)
        for _, r in tx[tag_tx == d].iterrows():
            betrag = P._betrag(r) * (1 if r["art"] == "Kauf" else -1)
            if r["klasse"] == "Aktie":
                z["f_aktien"] += betrag
                k = f"fs|{_sektor(r['sektor'])}"
                z[k] = z.get(k, 0.0) + betrag
            else:
                z["f_treas"] += betrag
        zeilen.append(z)
    return pd.DataFrame(zeilen).set_index("datum").fillna(0.0)


def renditen_bestand(td: pd.DataFrame) -> pd.DataFrame:
    """Tagesrenditen aus den Tageswerten. Flüsse am Tagesanfang: Basis = Vortageswert + Fluss. Beiträge je Klasse
    (Wert − Basis) / Gesamtwert Vortag summieren sich exakt zur Portfoliorendite."""
    tot = td[["aktien", "treas", "cash"]].sum(axis=1)
    tot_v = tot.shift(1)
    vor = td.shift(1)
    basis = {"aktien": vor["aktien"] + td["f_aktien"], "treas": vor["treas"] + td["f_treas"],
             "cash": vor["cash"] - td["f_aktien"] - td["f_treas"]}
    out = pd.DataFrame(index=td.index)
    out["r_p"] = tot / tot_v - 1
    for k, bs in basis.items():
        w = bs / tot_v
        beitrag = (td[k] - bs) / tot_v
        out[f"w_{k}"] = w
        out[f"r_{k}"] = np.where(w.abs() > 1e-7, beitrag / w.where(w.abs() > 1e-7, 1.0), 0.0)
    sek = [c[2:] for c in td.columns if c.startswith("s|")]
    if sek:
        bs = pd.DataFrame({s: vor.get(f"s|{s}", 0.0) + td.get(f"fs|{s}", 0.0) for s in sek})
        summe = bs.sum(axis=1)
        for s in sek:
            out[f"v|{s}"] = (bs[s] / summe.where(summe > 0)).fillna(0.0)
    out["wert"] = tot
    return out.iloc[1:].fillna(0.0)


def bench_frame(index: pd.DatetimeIndex, hist: dict, vortag: pd.Timestamp | None = None) -> pd.DataFrame:
    """Tagesrenditen der Benchmark-Bausteine auf den gegebenen Tagen: SPY, Treasury-Proxy, Cash (SOFR − 0,5 %), Sektor-ETFs."""
    tick = [A.SPX, A.NEUTRAL_PROXY] + [C.SEKTOR_ETF[s] for s in C.SEKTOREN if C.SEKTOR_ETF.get(s) in hist]
    tick = [t for t in dict.fromkeys(tick) if t in hist]
    px = pd.concat({t: hist[t] for t in tick}, axis=1).sort_index()
    alle = index if vortag is None else index.union([vortag])
    px = px.reindex(px.index.union(alle)).ffill().reindex(alle)
    r = px.pct_change().reindex(index).fillna(0.0)
    rf = A.rf_reihe(hist, index)
    out = pd.DataFrame(index=index)
    out["r_spy"] = r[A.SPX]
    out["r_govt"] = r[A.NEUTRAL_PROXY]
    out["r_cashb"] = A.cash_rendite(index, rf)
    out["r_b"] = C.BENCHMARK["aktien"] / 100 * out["r_spy"] + C.BENCHMARK["anleihen"] / 100 * out["r_govt"] \
        + C.BENCHMARK["cash"] / 100 * out["r_cashb"]
    for s in C.SEKTOREN:
        e = C.SEKTOR_ETF.get(s)
        out[f"e|{s}"] = r[e] if e in r.columns else out["r_spy"]
    out["rf"] = rf
    return out


def renditen_statisch(erg: dict, pos: pd.DataFrame, sektor_von: dict) -> pd.DataFrame:
    """Rückblick mit konstanten Gewichten (Ergebnis von analyse.rechne) im gleichen Format wie renditen_bestand."""
    R, rf, gew, c = erg["R"], erg["rf"], erg["gew"], float(erg["cash"])
    ak = set(pos.loc[pos["klasse"] == "Aktie", "ticker"]) if len(pos) else set()
    tr = set(pos.loc[pos["klasse"] == "Treasury", "ticker"]) if len(pos) else set()
    fuell = erg.get("fuell") or {}
    if A.SPX in fuell:
        ak.add(A.SPX)
    if A.NEUTRAL_PROXY in fuell:
        tr.add(A.NEUTRAL_PROXY)
    wa = {t: gew[t] for t in ak if t in R.columns and gew.get(t)}
    wt = {t: gew[t] for t in tr if t in R.columns and gew.get(t)}
    out = pd.DataFrame(index=R.index)
    out["r_p"] = erg["r_p"]
    sa, st_ = sum(wa.values()), sum(wt.values())
    out["w_aktien"], out["w_treas"] = sa, st_
    out["r_aktien"] = R[list(wa)].mul(pd.Series(wa)).sum(axis=1) / sa if sa > 0 else 0.0
    out["r_treas"] = R[list(wt)].mul(pd.Series(wt)).sum(axis=1) / st_ if st_ > 0 else 0.0
    w_c = 1.0 - sa - st_                        # Cash + Titel ohne Kursdaten (Rendite 0) – damit Σ w = 1
    out["w_cash"] = w_c
    beitrag_c = c * A.cash_rendite(R.index, rf) if c else pd.Series(0.0, index=R.index)
    out["r_cash"] = beitrag_c / w_c if abs(w_c) > 1e-7 else 0.0
    if sa > 0:
        sek = {}
        for t, w in wa.items():
            s = INDEX_SEKTOR if t == A.SPX and t in fuell else _sektor(sektor_von.get(t))
            sek[s] = sek.get(s, 0.0) + w / sa
        for s, v in sek.items():
            out[f"v|{s}"] = v
    return out


def _carino(r_p: pd.Series, r_b: pd.Series) -> pd.Series:
    """Verknüpfungsfaktoren k_t / K (Carino 1999): Σ_t Effekt_t × Faktor_t = R_P − R_B über den ganzen Zeitraum."""
    Rp, Rb = float((1 + r_p).prod() - 1), float((1 + r_b).prod() - 1)
    K = (math.log1p(Rp) - math.log1p(Rb)) / (Rp - Rb) if abs(Rp - Rb) > 1e-12 else 1 / (1 + Rp)
    d = r_p - r_b
    k = pd.Series(np.where(d.abs() > 1e-12, (np.log1p(r_p) - np.log1p(r_b)) / d.where(d.abs() > 1e-12, 1.0), 1 / (1 + r_p)),
                  index=r_p.index)
    return k / K


def attribution(rp: pd.DataFrame, bf: pd.DataFrame, u: dict) -> dict:
    """Tageseffekte (Brinson-Fachler auf Klassen + Sektorzerlegung) und mit Carino verknüpfte Summen.
    rp = renditen_bestand/renditen_statisch, bf = bench_frame (gleicher Index), u = Sektorgewichte der Benchmark in %."""
    bf = bf.reindex(rp.index).fillna(0.0)
    wb_a, wb_t, wb_c = (C.BENCHMARK[k] / 100 for k in ("aktien", "anleihen", "cash"))
    r_b = bf["r_b"]
    e = pd.DataFrame(index=rp.index)
    e["alloc_aktien"] = (rp["w_aktien"] - wb_a) * (bf["r_spy"] - r_b)
    e["alloc_treas"] = (rp["w_treas"] - wb_t) * (bf["r_govt"] - r_b)
    e["alloc_cash"] = (rp["w_cash"] - wb_c) * (bf["r_cashb"] - r_b)
    sel_a = rp["w_aktien"] * (rp["r_aktien"] - bf["r_spy"])
    us = {s: float(u.get(s, 0.0)) / 100 for s in C.SEKTOREN}
    su = sum(us.values()) or 1.0
    sek = pd.Series(0.0, index=rp.index)
    for s in C.SEKTOREN:
        v = rp[f"v|{s}"] if f"v|{s}" in rp.columns else 0.0
        sek = sek + (v - us[s] / su) * (bf[f"e|{s}"] - bf["r_spy"])
    # Platzhalter (Index) und unbekannte Sektoren: wie der Index → kein Sektoreffekt; ihr Anteil fehlt in Σ v, also
    # gegen die Benchmark-Verteilung korrigieren, damit leere Sektoren nicht als Untergewicht zählen
    rest = 1.0 - sum((rp[f"v|{s}"] if f"v|{s}" in rp.columns else 0.0) for s in C.SEKTOREN)
    if isinstance(rest, pd.Series):
        sek = sek + rest * sum(us[s] / su * (bf[f"e|{s}"] - bf["r_spy"]) for s in C.SEKTOREN)
    e["sektor"] = rp["w_aktien"] * sek
    e["titel"] = sel_a - e["sektor"]
    e["sel_treas"] = rp["w_treas"] * (rp["r_treas"] - bf["r_govt"])
    e["sel_cash"] = rp["w_cash"] * (rp["r_cash"] - bf["r_cashb"])
    f = _carino(rp["r_p"], r_b)
    verkn = e.mul(f, axis=0)
    return dict(tag=e, kum=verkn.cumsum(), summe=verkn.sum(), R_p=float((1 + rp["r_p"]).prod() - 1),
                R_b=float((1 + r_b).prod() - 1), rest=float(e.sum(axis=1).sub(rp["r_p"] - r_b).abs().max()))


def perioden(rp: pd.DataFrame, bf: pd.DataFrame, realisiert: bool) -> pd.DataFrame:
    """Renditen je Kalendermonat, letzte 5 Handelstage und ganzer Zeitraum: unser Portfolio, Benchmark, Klassen."""
    bf = bf.reindex(rp.index).fillna(0.0)
    g = lambda r: float((1 + r).prod() - 1) * 100  # noqa: E731

    def zeile(name, m):
        p_, b_ = rp.loc[m], bf.loc[m]
        if not len(p_):
            return None
        held_a, held_t = p_["w_aktien"].abs() > 1e-6, p_["w_treas"].abs() > 1e-6
        return {"Zeitraum": name, "Tage": int(len(p_)), "Unser Portfolio": g(p_["r_p"]), "Benchmark": g(b_["r_b"]),
                "Differenz": g(p_["r_p"]) - g(b_["r_b"]),
                "Unsere Aktien": g(p_["r_aktien"][held_a]) if held_a.any() else None, "S&P 500": g(b_["r_spy"]),
                "Unsere Treasuries": g(p_["r_treas"][held_t]) if held_t.any() else None, "Treasury-Proxy": g(b_["r_govt"])}

    zeilen = []
    monate = sorted({(d.year, d.month) for d in rp.index})
    if not realisiert:
        monate = monate[-12:]
    for j, m in monate:
        maske = (rp.index.year == j) & (rp.index.month == m)
        name = f"{MONATSNAME[m - 1]} {j}" + (" (laufend)" if (j, m) == (pd.Timestamp.today().year, pd.Timestamp.today().month) else "")
        z = zeile(name, maske)
        if z:
            zeilen.append(z)
    if len(rp) > 5:
        z = zeile("Letzte 5 Handelstage", rp.index >= rp.index[-5])
        zeilen.append(z)
    zeilen.append(zeile("Ganzer Zeitraum", rp.index >= rp.index[0]))
    return pd.DataFrame([z for z in zeilen if z])


def duration_analyse(treas: pd.DataFrame, fred: dict, fuell_t: float, d_bench: float) -> tuple[float | None, dict]:
    """Betragsgewichtete Mod. Duration der Treasuries eines Analyseportfolios + Beträge je Laufzeitband."""
    heute = pd.Timestamp.today().normalize()
    k = P.kurve_am(fred, heute)
    summe, dsum, baender = 0.0, 0.0, {}
    for _, z in treas.iterrows():
        betrag = A._num(z.get("Betrag USD"))
        if not betrag:
            continue
        faellig = pd.to_datetime(z.get("Fälligkeit"), errors="coerce")
        kupon = A._num(z.get("Kupon %"))
        if pd.notna(faellig):
            T = max(0.0, (faellig - heute).days / 365.25)
            band = A.baender(T, A._txt(z.get("Bezeichnung")))
            kurs = A._num(z.get("Kurs %"))
            y = P.rendite(kupon or 0.0, T, kurs) if kurs and T > 0 else P.kurve_rendite(k, T)
            D = P.mod_duration(kupon if kupon is not None else (y or 0.0), T, y) if y is not None and T > 0 else None
        else:
            band = A._txt(z.get("Laufzeit"))
            D = A.PROXY[band][1] if band in A.PROXY else None
        if band:
            baender[band] = baender.get(band, 0.0) + betrag
        if D is not None:
            summe += betrag
            dsum += betrag * D
    if fuell_t > 0:
        baender["Treasury-Proxy (offenes Budget)"] = fuell_t
        summe += fuell_t
        dsum += fuell_t * d_bench
    return (dsum / summe if summe else None), baender


def bench_titel_tabelle(basis: str, total: float, aktien_usd: dict, index_betrag: float = 0.0) -> pd.DataFrame | None:
    """Das Benchmark-Portfolio Titel für Titel mit unserem Kapital, daneben unser Gewicht."""
    d = BM.aktuell()
    if not d:
        return None
    t = d["titel"].copy()
    sp = BM._spalte(d, basis)
    w = t[sp].astype(float).fillna(0.0)
    w = w / w.sum() if w.sum() > 0 else w
    quote = C.BENCHMARK["aktien"] / 100
    bm_w = BM.gewichte(basis, d)
    unser = pd.Series({BM.norm_ticker(k): float(v) for k, v in aktien_usd.items() if v}, dtype=float).groupby(level=0).sum()
    out = pd.DataFrame({
        "Ticker": t["ticker"], "Name": t["name"].astype(str), "Sektor": t["sektor"],
        "Gewicht Aktienteil %": w * 100, "Gewicht gesamt %": w * quote * 100, "Betrag USD": w * quote * total,
        "Kurs Datei USD": t["kurs_akt"].astype(float) if "kurs_akt" in t else np.nan})
    out["Stück (≈)"] = np.floor(out["Betrag USD"] / out["Kurs Datei USD"].where(out["Kurs Datei USD"] > 0))
    out["Unser Gewicht gesamt %"] = [float(unser.get(tk, 0.0)) / total * 100 + (index_betrag / total * 100 * float(bm_w.get(tk, 0.0))
                                     if index_betrag else 0.0) for tk in out["Ticker"]]
    out["Unser Betrag USD"] = out["Unser Gewicht gesamt %"] / 100 * total
    out["Aktiv Pp."] = out["Unser Gewicht gesamt %"] - out["Gewicht gesamt %"]
    return out.sort_values("Gewicht Aktienteil %", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Daten zusammentragen
# ---------------------------------------------------------------------------
@st.cache_data(ttl=900, show_spinner="Rechne den Verlauf seit dem ersten Trade …", max_entries=4)
def _tages_cache(sig: str, _tx, _kurse, _fred, kapital: float) -> pd.DataFrame:
    return tagesdaten_bestand(_tx, _kurse, _fred, kapital)


def _quellen(ctx, tx: pd.DataFrame) -> tuple[list, dict, callable]:
    ss = st.session_state
    gs = A.portfolios(ctx.tabellen.get("analysen"))
    pf, pid = ss.get("an_pf", {}) or {}, ss.get("an_aktiv")
    opt, namen = [], {}
    if len(tx):
        opt.append(BESTAND)
        namen[BESTAND] = "Aktueller Bestand (Seite Portfolio)"
    gs_ids = list(gs["id"])
    for i in pf:
        if i not in gs_ids:
            opt.append(i)
            namen[i] = f"{pf[i].get('name') or 'Neues Portfolio'} · Analyse, nicht gespeichert"
    for _, z in gs.iterrows():
        opt.append(z["id"])
        namen[z["id"]] = f"{z['name']} · Analyse, {A._datum(z['zeit'])} ({z['person']})" + (" · offen" if z["id"] in pf else "")

    def eingabe(i) -> dict:
        if i == pid:
            return A._eingabe_aus_state(ss)
        if i in pf and pf[i].get("state"):
            return A._eingabe_aus_state(pf[i]["state"])
        try:
            return json.loads(gs.set_index("id").loc[i, "eingabe"])
        except Exception:  # noqa: BLE001
            return {}
    return opt, namen, eingabe


def _statisch(ctx, quelle: str, e: dict, monate: int, fred: dict, lade_kurse) -> dict | None:
    """Rückblick mit heutigen Gewichten: Positionen, analyse.rechne, Tagesrenditen je Klasse, Zusammensetzung."""
    if quelle == BESTAND:
        v = A.portfolio_vorlage(ctx.tabellen, fred, lade_kurse)
        if v is None:
            return None
        aktien, treas, cash, kapital = v
        pct, rest_cash, fuellen = None, False, "Cash"
    else:
        aktien = pd.DataFrame(e.get("aktien") or [])
        for c_ in ("Ticker", "Menge", "Betrag USD"):
            if c_ not in aktien.columns:
                aktien[c_] = None
        treas = A._treas_df(pd.DataFrame(e.get("treas") or []))
        cash, kapital, pct, rest_cash = 0.0, float(e.get("kapital") or 0), e.get("pct"), True
        fuellen = e.get("fuellen") if e.get("fuellen") in A.FUELLEN else "Index"
    tick = {A._txt(t).strip().upper().replace(".", "-") for t in aktien["Ticker"] if A._txt(t).strip()}
    for _, z in treas.iterrows():
        f = pd.to_datetime(z.get("Fälligkeit"), errors="coerce")
        b = A.baender(max(0.0, (f - pd.Timestamp.today()).days / 365.25), A._txt(z.get("Bezeichnung"))) if pd.notna(f) \
            else A._txt(z.get("Laufzeit"))
        if b in A.PROXY:
            tick.add(A.PROXY[b][0])
    etfs = {C.SEKTOR_ETF[s] for s in C.SEKTOREN if C.SEKTOR_ETF.get(s)}
    jahre = max(1, math.ceil(monate / 12))
    hist, fehlt, _ = A.lade_historie(tuple(sorted(tick | etfs | {A.SPX, A.NEUTRAL_PROXY, A.RF_TICKER})), jahre)
    if A.SPX not in hist or A.NEUTRAL_PROXY not in hist:
        return dict(fehler="Kursdaten von Yahoo Finance sind gerade nicht erreichbar – bitte in einer Minute nochmals versuchen.")
    letzte = {t: float(s.iloc[-1]) for t, s in hist.items()}
    pos, cash_, total, hinweise = A.zusammenstellen(aktien, treas, cash, kapital, rest_cash, letzte)
    fuell = A.offenes_budget(pos, cash_, kapital, pct) if fuellen == "Index" and pct else {}
    fuell = {t: w for t, w in fuell.items() if t in hist}
    if total <= 0:
        return dict(fehler="Das Portfolio hat keine Positionen.")
    ab = pd.Timestamp.today().normalize() - pd.DateOffset(months=monate)
    hist_c = {t: (s[s.index >= ab] if t != A.RF_TICKER else s) for t, s in hist.items()}
    erg = A.rechne(pos, cash_, total, hist_c, ctx.alloc["neutral"], fuell)
    if erg is None:
        return dict(fehler="Zu wenige gemeinsame Kursdaten für diesen Zeitraum.")
    name_bm, sek_bm, _ = BM.infos()
    ak_t = list(pos.loc[pos["klasse"] == "Aktie", "ticker"]) if len(pos) else []
    sektor_von = {t: sek_bm.get(t) or P.ticker_info(t).get("sektor") for t in ak_t}
    rp = renditen_statisch(erg, pos, sektor_von)
    i_spy = hist[A.SPX].index
    vortag = i_spy[i_spy < rp.index[0]][-1] if (i_spy < rp.index[0]).any() else rp.index[0] - pd.offsets.BDay(1)
    bf = bench_frame(rp.index, hist, vortag)
    aktien_usd = pos[pos["klasse"] == "Aktie"].groupby("ticker")["wert"].sum().to_dict() if len(pos) else {}
    treas_usd = float(pos.loc[pos["klasse"] == "Treasury", "wert"].sum()) if len(pos) else 0.0
    f_a, f_t = float(fuell.get(A.SPX, 0.0)), float(fuell.get(A.NEUTRAL_PROXY, 0.0)) if A.NEUTRAL_PROXY != A.SPX else 0.0
    if quelle == BESTAND:
        b = _bestand_heute(ctx, fred, lade_kurse)
        dur, baender = (P._wdur(b["b"]) if b and b["treas"] else None), (b["baender"] if b else {})
    else:
        dur, baender = duration_analyse(treas, fred, f_t, float(ctx.bond.get("duration") or 6.0))
    zus = dict(aktien_usd=aktien_usd, sektor_von=sektor_von, index_betrag=f_a, aktien=sum(aktien_usd.values()) + f_a,
               treas=treas_usd + f_t, cash=max(0.0, cash_ - f_a - f_t), total=total, duration=dur, baender=baender)
    return dict(erg=erg, rp=rp, bf=bf, hist=hist, zus=zus, start_wert=total, hinweise=hinweise, fehlt=fehlt,
                wert_p=total * (1 + rp["r_p"]).cumprod(), realisiert=False)


def _bestand_heute(ctx, fred: dict, lade_kurse) -> dict | None:
    tx = P.transaktionen(ctx.tabellen.get("transaktionen"))
    if not len(tx):
        return None
    kapital, _ = P._einstellungen(ctx.tabellen.get("einstellungen"))
    tick = tuple(sorted(set(tx.loc[tx["klasse"] == "Aktie", "ticker"].str.upper())))
    kurse = lade_kurse(tick)[0] if tick else {}
    heute = pd.Timestamp.today().normalize()
    pos, cash_delta, _ = P.positionen(tx)
    b = P.bewerte(pos, kurse, fred, heute)
    cash = kapital + cash_delta + float(CZ.zins_bis(tx, kapital, fred))
    ak = b[b["klasse"] == "Aktie"] if len(b) else pd.DataFrame(columns=["ticker", "wert", "sektor"])
    tr = b[b["klasse"] == "Treasury"] if len(b) else pd.DataFrame(columns=["wert", "zins", "restlaufzeit", "ticker"])
    treas = float(tr["wert"].sum() + tr["zins"].sum()) if len(tr) else 0.0
    baender = {}
    for _, z in tr.iterrows():
        bd = A.baender(z["restlaufzeit"], z["ticker"])
        baender[bd] = baender.get(bd, 0.0) + float(z["wert"] + z["zins"])
    aktien_usd = ak.groupby(ak["ticker"].str.upper())["wert"].sum().to_dict() if len(ak) else {}
    sektor_von = {str(t).upper(): _sektor(s) for t, s in zip(ak["ticker"], ak["sektor"])} if len(ak) else {}
    total = sum(aktien_usd.values()) + treas + cash
    return dict(b=b, tx=tx, kurse=kurse, kapital=kapital, aktien_usd=aktien_usd, sektor_von=sektor_von, index_betrag=0.0,
                aktien=sum(aktien_usd.values()), treas=treas, cash=cash, total=total, duration=P._wdur(b) if treas else None,
                baender=baender)


def _realisiert(ctx, fred: dict, lade_kurse) -> dict | None:
    bh = _bestand_heute(ctx, fred, lade_kurse)
    if bh is None:
        return None
    tx, kurse, kapital = bh["tx"], bh["kurse"], bh["kapital"]
    sig = f"{len(tx)}|{tx['id'].astype(str).str.cat(sep=',')}|{pd.Timestamp.today():%Y-%m-%d %H}|{kapital}|{len(kurse)}"
    td = _tages_cache(sig, tx, kurse, fred, kapital)
    if len(td) < 2:
        return dict(fehler="Noch kein ganzer Handelstag seit dem ersten Trade.")
    rp = renditen_bestand(td)
    etfs = {C.SEKTOR_ETF[s] for s in C.SEKTOREN if C.SEKTOR_ETF.get(s)}
    hist, _, _ = A.lade_historie(tuple(sorted(etfs | {A.SPX, A.NEUTRAL_PROXY, A.RF_TICKER})), 2)
    if A.SPX not in hist or A.NEUTRAL_PROXY not in hist:
        return dict(fehler="Kursdaten der Benchmark (Yahoo Finance) sind gerade nicht erreichbar – bitte später nochmals öffnen.")
    bf = bench_frame(rp.index, hist, td.index[0])
    zus = {k: bh[k] for k in ("aktien_usd", "sektor_von", "index_betrag", "aktien", "treas", "cash", "total", "duration", "baender")}
    return dict(rp=rp, bf=bf, hist=hist, zus=zus, start_wert=kapital, wert_p=rp["wert"], realisiert=True, td=td, hinweise=[])


# ---------------------------------------------------------------------------
# Seite
# ---------------------------------------------------------------------------
def _fmt_tab(df: pd.DataFrame, pct_spalten: list[str], pp_spalten: list[str] = (), usd_spalten: list[str] = ()) -> pd.DataFrame:
    d = df.copy()
    for c in pct_spalten:
        if c in d:
            d[c] = d[c].map(lambda x: A._pct(x, 2, True) if x is not None and not pd.isna(x) else "–")
    for c in pp_spalten:
        if c in d:
            d[c] = d[c].map(lambda x: A._pp(x, 2) if x is not None and not pd.isna(x) else "–")
    for c in usd_spalten:
        if c in d:
            d[c] = d[c].map(lambda x: _usd(x))
    return d


def _farbe(x: float | None) -> str:
    if x is None or pd.isna(x):
        return ui.MUTED if hasattr(ui, "MUTED") else "#667085"
    return ui.FARBE["Grün"] if x >= 0 else ui.FARBE["Rot"]


def seite_benchmark(ctx, fred: dict, lade_kurse):
    import seitenpdf as SP

    ui.seitenkopf("Benchmark-Vergleich",
                  "Unser Portfolio neben dem Benchmark-Portfolio des Dozenten (50 % S&P 500 / 50 % US-Treasuries, gleiches Kapital): "
                  "Wertentwicklung, Mehrrendite und woher sie kommt, Zusammensetzung Titel für Titel und Risiko.")
    ph_pdf = st.empty()
    tx = P.transaktionen(ctx.tabellen.get("transaktionen"))
    opt, namen, eingabe = _quellen(ctx, tx)
    if not opt:
        st.info("Noch kein Bestand und kein gespeichertes Analyseportfolio. Lege in der Analyse ein Portfolio an (oder erfasse die "
                "ersten Transaktionen) – dann steht es hier neben der Benchmark.")
        _bereich_benchmark_portfolio(ctx, fred, None, "gueltig", None)
        return
    ss = st.session_state
    if ss.get("bv_quelle") not in opt:
        ss["bv_quelle"] = BESTAND if BESTAND in opt else (ss.get("an_aktiv") if ss.get("an_aktiv") in opt else opt[0])
    with st.container(border=True):
        c1, c2, c3 = st.columns([1.6, 1.9, 1])
        quelle = c1.selectbox("Unser Portfolio", opt, format_func=namen.get, key="bv_quelle",
                              help="Bestand = echte Transaktionen. Analyseportfolios zeigen, wie ein Entwurf gegen die Benchmark "
                                   "gelaufen wäre (heutige Gewichte, Rückblick).")
        zr_opt = list(ZEITRAUM) if quelle == BESTAND else [k for k in ZEITRAUM if k != "start"]
        if ss.get("bv_zr") not in zr_opt:
            ss["bv_zr"] = "start" if quelle == BESTAND else "1j"
        zr = c2.segmented_control("Zeitraum", zr_opt, format_func=lambda k: ZEITRAUM[k], key="bv_zr") or zr_opt[0]
        basis = c3.segmented_control("Benchmark-Gewichte", ["gueltig", "aktuell"], default="gueltig", key="bv_basis",
                                     format_func=lambda b: "gültig" if b == "gueltig" else "aktuell",
                                     help="«gültig» = Gewichte, gegen die die Dozenten-App misst (Monatsende); «aktuell» = mit "
                                          "den Kursen fortgeschrieben.") or "gueltig"
        st.caption("Benchmark: " + BM.beschreibung() + f" · Treasuries über {A.NEUTRAL_PROXY} (Einstellungen), täglich 50/50 "
                   "rebalanciert · Cash-Zins SOFR − 0,5 %.")

    with st.spinner("Rechne den Vergleich …"):
        try:
            if zr == "start":
                dat = _realisiert(ctx, fred, lade_kurse)
            else:
                dat = _statisch(ctx, quelle, eingabe(quelle) if quelle != BESTAND else {}, MONATE_ZR[zr], fred, lade_kurse)
        except Exception as ex:  # noqa: BLE001
            dat = dict(fehler=f"Vergleich nicht berechenbar ({type(ex).__name__}: {ex})")
    if not dat or dat.get("fehler"):
        st.warning((dat or {}).get("fehler") or "Keine Daten.")
        _bereich_benchmark_portfolio(ctx, fred, None, basis, None)
        return
    for h in dat.get("hinweise", []):
        st.caption("Hinweis: " + h)

    rp, bf, zus = dat["rp"], dat["bf"].reindex(dat["rp"].index).fillna(0.0), dat["zus"]
    start = float(dat["start_wert"])
    wert_b = start * (1 + bf["r_b"]).cumprod()
    wert_p = dat["wert_p"]
    g_p, g_b = float((1 + rp["r_p"]).prod() - 1) * 100, float((1 + bf["r_b"]).prod() - 1) * 100
    as_ = BM.active_share(zus["aktien_usd"], basis, zus["sektor_von"], index_betrag=zus["index_betrag"]) \
        if (zus["aktien_usd"] or zus["index_betrag"]) else None
    rf = bf["rf"]
    k_p = A.kennzahlen(rp["r_p"], rf, bf["r_spy"])
    k_b = A.kennzahlen(bf["r_b"], rf, bf["r_spy"])
    k_p.update(A.vs_benchmark(rp["r_p"], bf["r_b"], k_p, k_b))
    titel_zr = ZEITRAUM[zr] + (f" · {rp.index[0]:%d.%m.%Y} – {rp.index[-1]:%d.%m.%Y}" if len(rp) else "")
    R = SP.Sammler("Benchmark-Vergleich", f"{namen[quelle]} gegen die Benchmark des Dozenten (50/50) · {titel_zr}",
                   kurz="Benchmark-Vergleich", quer=True)

    lo_as, hi_as = float(ctx.ips.get("active_share_min") or 0), float(ctx.ips.get("active_share_max") or 100)
    as_wert = as_["active_share"] if as_ else None
    kpis = [
        ("Unser Portfolio", _usd(float(wert_p.iloc[-1])), f"Start {_usd(start)} · {A._pct(g_p, 2, True)}"),
        ("Benchmark-Portfolio", _usd(float(wert_b.iloc[-1])), f"gleicher Start · {A._pct(g_b, 2, True)}"),
        ("Mehrrendite", f"<span style='color:{_farbe(g_p - g_b)}'>{ui.de(g_p - g_b, 2, True)} Pp.</span>",
         f"{_usd(float(wert_p.iloc[-1] - wert_b.iloc[-1]))} · IPS-Ziel {ui.de(ctx.ips.get('rendite_rel'), 1, True)} Pp. p.a."),
        ("Active Share (Aktien)", f"<span style='color:{ui.FARBE['Grün'] if as_wert is not None and lo_as <= as_wert <= hi_as else ui.FARBE['Rot']}'>"
         f"{A._pct(as_wert)}</span>" if as_wert is not None else "–",
         f"IPS {ui.de(lo_as, 0)}–{ui.de(hi_as, 0)} % · Basis {BM.BASIS_NAME[basis]}"),
    ]
    ui.kopf_kpis(kpis)
    R.kpis([(a, b, c) for a, b, c in kpis])
    if len(rp) < 20:
        st.caption(f"Erst {len(rp)} Handelstage – Tracking Error, Volatilität und Beta werden ab 20 Handelstagen gerechnet "
                   "(Reiter «Risiko» zeigt dafür die heutigen Gewichte über 12 Monate).")
    else:
        te_max = float(ctx.ips.get("te_max") or 0)
        k2 = [("Tracking Error vs. Benchmark", f"<span style='color:{ui.FARBE['Grün'] if not te_max or (k_p.get('te_bench') or 0) <= te_max else ui.FARBE['Rot']}'>"
               f"{A._pct(k_p.get('te_bench'))}</span>", f"IPS höchstens {ui.de(te_max, 1)} %"),
              ("Information Ratio", A._zahl(k_p.get("ir_bench")), "Mehrrendite p.a. / Tracking Error"),
              ("Volatilität p.a.", A._pct(k_p.get("vola")), f"Benchmark {A._pct(k_b.get('vola'))}"),
              ("Max. Drawdown", A._pct(k_p.get("mdd")), f"Benchmark {A._pct(k_b.get('mdd'))}")]
        ui.kopf_kpis(k2)
        R.kpis(k2)
    if not dat["realisiert"]:
        st.caption("Rückblick: heutige Gewichte über den ganzen Zeitraum gehalten (täglich auf die Gewichte zurückgesetzt) – so wäre "
                   "das Portfolio gegen die Benchmark gelaufen, keine echte Performance.")

    t1, t2, t3, t4, t5 = st.tabs(["Wertentwicklung", "Woher kommt der Unterschied?", "Zusammensetzung", "Benchmark-Portfolio", "Risiko"])
    with t1:
        _tab_verlauf(rp, bf, wert_p, wert_b, start, dat["realisiert"], R)
    with t2:
        _tab_attribution(rp, bf, start, basis, R)
    with t3:
        _tab_zusammensetzung(ctx, zus, as_, basis, R)
    with t4:
        _bereich_benchmark_portfolio(ctx, fred, zus, basis, R)
    with t5:
        _tab_risiko(ctx, quelle, eingabe, rp, bf, k_p, k_b, dat, fred, lade_kurse, R)
    R.text("Quellen: Kurse Yahoo Finance (SPY, Treasury-ETF, Sektor-ETFs dividendenbereinigt), Zinsen FRED (SOFR, Zinskurve), "
           "Titelgewichte der Benchmark aus der Datei der Dozenten-App. Unsere Aktien im Bestand werden wie im Tracker mit "
           "Schlusskursen ohne Dividenden bewertet.", klein=True)
    SP.knopf(ph_pdf, R, "benchmark_vgl", dateiname=f"Benchmark-Vergleich_{datetime.now():%Y-%m-%d}.pdf")


def _tab_verlauf(rp, bf, wert_p, wert_b, start, realisiert, R):
    zeige_extra = st.toggle("S&P 500 und Treasury-Proxy einzeln zeigen", value=False, key="bv_extra")
    erster = rp.index[0] - pd.offsets.BDay(1)
    linie = lambda s: pd.concat([pd.Series([start], index=[erster]), s])  # noqa: E731
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=linie(wert_p).index, y=linie(wert_p), name="Unser Portfolio", line=dict(color=FARBE_P, width=2.6)))
    fig.add_trace(go.Scatter(x=linie(wert_b).index, y=linie(wert_b), name="Benchmark-Portfolio 50/50", line=dict(color=FARBE_B, width=2)))
    if zeige_extra:
        for n, r, f in (("S&P 500 (SPY)", bf["r_spy"], ui.STAHL), (f"Treasuries ({A.NEUTRAL_PROXY})", bf["r_govt"], ui.GOLD)):
            fig.add_trace(go.Scatter(x=linie(start * (1 + r).cumprod()).index, y=linie(start * (1 + r).cumprod()), name=n,
                                     line=dict(color=f, width=1.4, dash="dot")))
    fig.update_layout(height=360, margin=dict(l=8, r=8, t=10, b=8), hovermode="x unified")
    fig.update_yaxes(title="Wert in USD", tickformat=",.0f")
    ui.zeige(fig)
    vorsprung = pd.concat([pd.Series([0.0], index=[erster]), wert_p - wert_b])
    fig2 = go.Figure(go.Scatter(x=vorsprung.index, y=vorsprung, fill="tozeroy", name="Vorsprung",
                                line=dict(color=FARBE_P if vorsprung.iloc[-1] >= 0 else FARBE_B, width=1.6)))
    fig2.update_layout(height=200, margin=dict(l=8, r=8, t=26, b=8), title=dict(text="Vorsprung gegenüber der Benchmark (USD)",
                                                                                 font=dict(size=12)), showlegend=False)
    fig2.update_yaxes(tickformat=",.0f", zeroline=True, zerolinecolor="#98A2B3")
    ui.zeige(fig2)
    st.markdown("**Renditen je Zeitraum**")
    tab = perioden(rp, bf, realisiert)
    anz = _fmt_tab(tab, ["Unser Portfolio", "Benchmark", "Unsere Aktien", "S&P 500", "Unsere Treasuries", "Treasury-Proxy"], ["Differenz"])
    st.dataframe(anz, hide_index=True, width="stretch")
    st.caption("Monatsrenditen wie in der Simulation (Kalendermonat). «Unsere Aktien/Treasuries» = Rendite der Klasse für sich "
               "(nur Tage mit Bestand), daneben ihr Gegenstück in der Benchmark.")
    R.h("Wertentwicklung")
    import seitenpdf as SP

    R.linien([("Unser Portfolio", linie(wert_p) / start * 100, SP.rgb(FARBE_P)),
              ("Benchmark 50/50", linie(wert_b) / start * 100, SP.rgb(FARBE_B))], titel="Wert (Start = 100)")
    R.h2("Renditen je Zeitraum")
    R.tabelle(anz)


def _tab_attribution(rp, bf, start, basis, R):
    u = BM.sektor_gewichte(basis)
    at = attribution(rp, bf, u)
    s = at["summe"] * 100
    st.markdown("**Woher kommt die Mehrrendite?** · Zerlegung der Differenz zur Benchmark in Entscheide")
    gesamt = (at["R_p"] - at["R_b"]) * 100
    fig = go.Figure(go.Waterfall(
        orientation="v", measure=["relative"] * len(EFFEKTE) + ["total"],
        x=[n for _, n, _ in EFFEKTE] + ["Mehrrendite"], y=[float(s[k]) for k, _, _ in EFFEKTE] + [gesamt],
        text=[f"{ui.de(float(s[k]), 2, True)}" for k, _, _ in EFFEKTE] + [f"{ui.de(gesamt, 2, True)}"], textposition="outside",
        increasing=dict(marker=dict(color=ui.FARBE["Grün"])), decreasing=dict(marker=dict(color=ui.FARBE["Rot"])),
        totals=dict(marker=dict(color=FARBE_P)), connector=dict(line=dict(color="#D0D5DD"))))
    fig.update_layout(height=340, margin=dict(l=8, r=8, t=20, b=8), showlegend=False)
    fig.update_yaxes(ticksuffix=" Pp.")
    ui.zeige(fig)
    tab = pd.DataFrame([{"Beitrag": n, "Pp.": float(s[k]), "USD": float(s[k]) / 100 * start, "Rechnung": erkl}
                        for k, n, erkl in EFFEKTE] + [{"Beitrag": "Summe = Mehrrendite", "Pp.": gesamt, "USD": gesamt / 100 * start,
                                                      "Rechnung": f"unser Portfolio {A._pct(at['R_p'] * 100, 2, True)} − Benchmark "
                                                                  f"{A._pct(at['R_b'] * 100, 2, True)}"}])
    anz = _fmt_tab(tab, [], ["Pp."], ["USD"])
    st.dataframe(anz, hide_index=True, width="stretch", column_config={"Rechnung": st.column_config.TextColumn(width="large")})
    kum = at["kum"] * 100
    fig2 = go.Figure()
    gruppen = {"Allokation (Quoten)": ["alloc_aktien", "alloc_treas", "alloc_cash"], "Sektorwetten": ["sektor"],
               "Titelauswahl": ["titel"], "Treasuries & Cash": ["sel_treas", "sel_cash"]}
    for (n, ks), f in zip(gruppen.items(), [ui.NAVY, ui.PETROL, ui.GOLD, ui.STAHL]):
        fig2.add_trace(go.Scatter(x=kum.index, y=kum[ks].sum(axis=1), name=n, line=dict(color=f, width=1.8)))
    fig2.add_trace(go.Scatter(x=kum.index, y=kum.sum(axis=1), name="Mehrrendite", line=dict(color=FARBE_P, width=2.6)))
    fig2.update_layout(height=300, margin=dict(l=8, r=8, t=26, b=8), hovermode="x unified",
                       title=dict(text="Beiträge im Zeitverlauf (kumuliert, Pp.)", font=dict(size=12)))
    fig2.update_yaxes(ticksuffix=" Pp.", zeroline=True, zerolinecolor="#98A2B3")
    ui.zeige(fig2)
    st.caption("Methode: täglich Brinson-Fachler auf Anlageklassen (Allokation = Über-/Untergewicht der Klasse × Rendite der Klasse "
               "in der Benchmark gegenüber der ganzen Benchmark; Auswahl = unser Gewicht × (unsere Klassenrendite − Klasse in der "
               "Benchmark)), die Aktienauswahl zusätzlich in Sektorwetten (mit den SPDR-Sektor-ETFs) und Titelauswahl zerlegt. "
               "Die Tage sind mit Carino verknüpft, darum ergibt die Summe genau die Mehrrendite über den Zeitraum. Unsere Aktien "
               "zählen ohne Dividenden (wie im Tracker), SPY mit – das drückt die Titelauswahl leicht.")
    R.h("Woher kommt die Mehrrendite?")
    R.tabelle(anz)
    import seitenpdf as SP

    R.balken([(n, float(s[k]), SP.rgb(ui.FARBE["Grün"] if s[k] >= 0 else ui.FARBE["Rot"])) for k, n, _ in EFFEKTE] +
             [("Mehrrendite", gesamt, SP.rgb(FARBE_P))], einheit=" Pp.", titel="Beiträge in Prozentpunkten")


def _tab_zusammensetzung(ctx, zus, as_, basis, R):
    import sektoren as SK

    total = zus["total"] or 1.0
    wb = C.BENCHMARK
    st.markdown("**Anlageklassen**")
    kl = pd.DataFrame([
        {"Klasse": "Aktien", "Unser Portfolio %": zus["aktien"] / total * 100, "Benchmark %": wb["aktien"],
         "Unser Betrag": zus["aktien"], "Benchmark-Betrag": wb["aktien"] / 100 * total},
        {"Klasse": "Treasuries", "Unser Portfolio %": zus["treas"] / total * 100, "Benchmark %": wb["anleihen"],
         "Unser Betrag": zus["treas"], "Benchmark-Betrag": wb["anleihen"] / 100 * total},
        {"Klasse": "Cash", "Unser Portfolio %": zus["cash"] / total * 100, "Benchmark %": wb["cash"],
         "Unser Betrag": zus["cash"], "Benchmark-Betrag": wb["cash"] / 100 * total}])
    kl["Differenz"] = kl["Unser Portfolio %"] - kl["Benchmark %"]
    c1, c2 = st.columns([1.15, 1])
    with c1:
        anz_kl = pd.DataFrame({"Klasse": kl["Klasse"], "Unser Portfolio": kl["Unser Portfolio %"].map(lambda x: f"{ui.de(x, 1)} %"),
                               "Benchmark": kl["Benchmark %"].map(lambda x: f"{ui.de(x, 0)} %"),
                               "Differenz": kl["Differenz"].map(lambda x: A._pp(x, 1)),
                               "Unser Betrag": kl["Unser Betrag"].map(_usd), "Benchmark-Betrag": kl["Benchmark-Betrag"].map(_usd)})
        st.dataframe(anz_kl, hide_index=True, width="stretch")
        d_b = float(ctx.bond.get("duration") or 0)
        d_p = zus.get("duration")
        ui.kopf_kpis([
            ("Duration Treasuries", f"{ui.de(d_p, 1)} J." if d_p is not None else "–", f"Benchmark {ui.de(d_b, 1)} J. ({A.NEUTRAL_PROXY}-Mix)"),
            ("Duration gesamt", f"{ui.de((d_p or 0) * zus['treas'] / total, 2)} J.",
             f"Benchmark {ui.de(d_b * wb['anleihen'] / 100, 2)} J. · Zinsrisiko des ganzen Portfolios"),
        ])
    with c2:
        fig = go.Figure([go.Bar(name="Unser Portfolio", x=kl["Klasse"], y=kl["Unser Portfolio %"], marker_color=FARBE_P,
                                text=[f"{x:.0f} %" for x in kl["Unser Portfolio %"]], textposition="outside"),
                         go.Bar(name="Benchmark", x=kl["Klasse"], y=kl["Benchmark %"], marker_color=FARBE_B,
                                text=[f"{x:.0f} %" for x in kl["Benchmark %"]], textposition="outside")])
        fig.update_layout(barmode="group", height=290, margin=dict(l=8, r=8, t=10, b=8))
        fig.update_yaxes(ticksuffix=" %")
        ui.zeige(fig)
    R.h("Zusammensetzung")
    R.tabelle(anz_kl)

    if zus.get("baender") or zus["treas"]:
        st.markdown("**Treasuries nach Laufzeit**")
        mix = C.BOND_BENCHMARKS.get(ctx.einst.get("bond_benchmark")) or C.BOND_BENCHMARKS[C.BOND_BENCHMARK_DEFAULT]
        bb = {}
        for T, w in mix.items():
            band = A.baender(float(T))
            bb[band] = bb.get(band, 0.0) + w
        baender = list(dict.fromkeys(list(A.PROXY) + list(zus.get("baender", {}))))
        zeilen = []
        for band in baender:
            u_ = zus.get("baender", {}).get(band, 0.0)
            b_ = bb.get(band, 0.0) * wb["anleihen"] / 100 * total
            if u_ or b_:
                zeilen.append({"Laufzeit": band, "Unser Betrag": _usd(u_), "Unser Anteil gesamt": f"{ui.de(u_ / total * 100, 1)} %",
                               "Benchmark-Betrag": _usd(b_), "Benchmark-Anteil gesamt": f"{ui.de(b_ / total * 100, 1)} %",
                               "Differenz": A._pp((u_ - b_) / total * 100, 1)})
        lz = pd.DataFrame(zeilen)
        st.dataframe(lz, hide_index=True, width="stretch")
        st.caption(f"Benchmark-Treasuries: Laufzeitmix «{ctx.einst.get('bond_benchmark') or C.BOND_BENCHMARK_DEFAULT}» (Einstellungen) – "
                   "der genaue Treasury-Index des Dozenten ist nicht bekannt.")
        R.h2("Treasuries nach Laufzeit")
        R.tabelle(lz)

    if as_ is None:
        st.info("Keine Aktien im Portfolio bzw. keine Benchmark-Datei – Sektoren und Titel lassen sich nicht vergleichen.")
        return
    st.markdown(f"**Sektoren im Aktienteil** · gegen die Benchmark ({BM.BASIS_NAME[basis]})")
    sa = SK.lade_allokation(ctx.tabellen.get("sektor_allokation"))["sektoren"]
    zl = SK.ziele(sa, C.SP500_GEWICHT)
    sek = as_["sektoren"].copy()
    q = zus["aktien"] / total
    sek_anz = pd.DataFrame({
        "Sektor": sek["sektor"], "Unser Aktienteil": sek["portfolio"].map(lambda x: f"{ui.de(x, 1)} %"),
        "Benchmark": sek["benchmark"].map(lambda x: f"{ui.de(x, 1)} %"), "Aktiv": sek["aktiv"].map(lambda x: A._pp(x, 1)),
        "Unser Ziel": [f"{ui.de(zl[s], 1)} %" if zl.get(s) is not None else "–" for s in sek["sektor"]],
        "Unser Anteil gesamt": (sek["portfolio"] * q).map(lambda x: f"{ui.de(x, 1)} %"),
        "Benchmark gesamt": (sek["benchmark"] * wb["aktien"] / 100).map(lambda x: f"{ui.de(x, 1)} %"),
        "Titel (wir / Benchmark)": [f"{a} / {b}" for a, b in zip(sek["titel_p"], sek["titel_b"])],
        "Beitrag Active Share": sek["beitrag_as"].map(lambda x: f"{ui.de(x, 1)} Pp.")})
    c1, c2 = st.columns([1.25, 1])
    with c1:
        st.dataframe(sek_anz, hide_index=True, width="stretch")
    with c2:
        s2 = sek.sort_values("aktiv")
        fig = go.Figure(go.Bar(x=s2["aktiv"], y=s2["sektor"], orientation="h",
                               marker_color=[ui.FARBE["Grün"] if v >= 0 else ui.FARBE["Rot"] for v in s2["aktiv"]],
                               text=[ui.de(v, 1, True) for v in s2["aktiv"]], textposition="outside"))
        fig.update_layout(height=380, margin=dict(l=8, r=8, t=26, b=8), title=dict(text="Über-/Untergewicht (Pp. Aktienteil)",
                                                                                    font=dict(size=12)), showlegend=False)
        ui.zeige(fig)
    R.h2("Sektoren im Aktienteil")
    R.tabelle(sek_anz)

    st.markdown("**Titel: grösste Abweichungen zur Benchmark** (in % des Aktienteils)")
    ui.kopf_kpis([
        ("Active Share", A._pct(as_["active_share"]), f"davon Sektorwetten {ui.de(as_['as_sektor'], 1)} Pp., "
                                                     f"Titelauswahl {ui.de(as_['as_titel_in_sektor'], 1)} Pp."),
        ("Titel", f"{as_['n_portfolio']} / {as_['n_benchmark']}", f"wir / Benchmark · {as_['n_gemeinsam']} gemeinsam"),
        ("Abdeckung", A._pct(as_["abdeckung"]), "Benchmark-Gewicht der Titel, die wir halten"),
        ("Nicht im Index", str(len(as_["nicht_im_index"])), ", ".join(as_["nicht_im_index"][:6]) or "alle Titel im S&P 500"),
    ])
    tb = as_["tabelle"]
    fm = lambda d: pd.DataFrame({"Ticker": d["ticker"], "Name": d["name"], "Sektor": d["sektor"],  # noqa: E731
                                 "Wir": d["portfolio"].map(lambda x: f"{ui.de(x, 2)} %"),
                                 "Benchmark": d["benchmark"].map(lambda x: f"{ui.de(x, 2)} %"),
                                 "Aktiv": d["aktiv"].map(lambda x: A._pp(x, 2))})
    ueber, unter = tb[tb["aktiv"] > 0].head(12), tb[tb["aktiv"] < 0].head(12)
    c1, c2 = st.columns(2)
    c1.markdown("Übergewichte")
    c1.dataframe(fm(ueber), hide_index=True, width="stretch")
    c2.markdown("Untergewichte (inkl. nicht gehaltener Indextitel)")
    c2.dataframe(fm(unter), hide_index=True, width="stretch")
    R.h2("Grösste Übergewichte")
    R.tabelle(fm(ueber))
    R.h2("Grösste Untergewichte")
    R.tabelle(fm(unter))


def _bereich_benchmark_portfolio(ctx, fred, zus, basis, R):
    d = BM.aktuell()
    if not d:
        st.info("Keine Benchmark-Datei geladen (Seite Einstellungen).")
        return
    total = float(zus["total"]) if zus else float(P._einstellungen(ctx.tabellen.get("einstellungen"))[0])
    tab = bench_titel_tabelle(basis, total, zus["aktien_usd"] if zus else {}, zus["index_betrag"] if zus else 0.0)
    w = tab["Gewicht Aktienteil %"] / 100
    eff = 1 / float((w ** 2).sum()) if (w ** 2).sum() > 0 else 0
    st.markdown(f"**Das Benchmark-Portfolio mit {_usd(total)}** · so sähe es aus, wenn wir genau die Benchmark hielten")
    ui.kopf_kpis([
        ("Aufteilung", "50 / 50 / 0 %", f"Aktien {_usd(total * C.BENCHMARK['aktien'] / 100)} · Treasuries "
                                         f"{_usd(total * C.BENCHMARK['anleihen'] / 100)}"),
        ("Titel im Aktienteil", str(len(tab)), f"effektiv {ui.de(eff, 0)} · Gewichte {BM.BASIS_NAME[basis]}"),
        ("Top 10", f"{ui.de(float(tab['Gewicht Aktienteil %'].head(10).sum()), 1)} %",
         ", ".join(tab["Ticker"].head(5)) + " …"),
        ("Treasuries", f"{ui.de(ctx.bond.get('duration'), 1)} J. Duration",
         f"Rendite {ui.de(ctx.bond.get('rendite'), 2)} % · Proxy {A.NEUTRAL_PROXY}"),
    ])
    c1, c2, c3 = st.columns([1.6, 1.2, 1.2])
    such = c1.text_input("Suchen (Ticker oder Name)", key="bv_bm_such", placeholder="z. B. NVDA oder Bank")
    sektoren = c2.multiselect("Sektoren", C.SEKTOREN, key="bv_bm_sek")
    filt = c3.segmented_control("Titel", ["alle", "gehalten", "nicht"], default="alle", key="bv_bm_filt",
                                format_func={"alle": "Alle", "gehalten": "Von uns gehalten", "nicht": "Nicht gehalten"}.get) or "alle"
    t = tab
    if such:
        m = t["Ticker"].str.contains(such.strip(), case=False, regex=False) | t["Name"].str.contains(such.strip(), case=False, regex=False)
        t = t[m]
    if sektoren:
        t = t[t["Sektor"].isin(sektoren)]
    if filt == "gehalten":
        t = t[t["Unser Gewicht gesamt %"] > 1e-6]
    elif filt == "nicht":
        t = t[t["Unser Gewicht gesamt %"] <= 1e-6]
    st.dataframe(t, hide_index=True, width="stretch", height=420, column_config={
        "Gewicht Aktienteil %": st.column_config.NumberColumn(format="%.3f"),
        "Gewicht gesamt %": st.column_config.NumberColumn(format="%.3f"),
        "Betrag USD": st.column_config.NumberColumn(format="%,.0f"),
        "Kurs Datei USD": st.column_config.NumberColumn(format="%.2f"),
        "Stück (≈)": st.column_config.NumberColumn(format="%,.0f"),
        "Unser Gewicht gesamt %": st.column_config.NumberColumn(format="%.3f"),
        "Unser Betrag USD": st.column_config.NumberColumn(format="%,.0f"),
        "Aktiv Pp.": st.column_config.NumberColumn(format="%+.3f")})
    sd = pd.Timestamp(d["stand"]).strftime("%d.%m.%Y") if d.get("stand") else "?"
    st.caption(f"{len(t)} von {len(tab)} Titeln. Betrag = Gewicht × 50 % × {_usd(total)}; Stück zum Kurs der Datei (Stand {sd}), "
               "nur zur Orientierung. «Unser Gewicht» enthält beim Analyseportfolio auch das offene Aktienbudget als Index.")
    mix = C.BOND_BENCHMARKS.get(ctx.einst.get("bond_benchmark")) or C.BOND_BENCHMARKS[C.BOND_BENCHMARK_DEFAULT]
    try:
        import anleihen as AN

        k = AN.kurve(fred)
    except Exception:  # noqa: BLE001
        k = {}
    tr = pd.DataFrame([{"Laufzeit": f"{T:g} J.", "Anteil Treasury-Teil": f"{ui.de(w_ * 100, 0)} %",
                        "Anteil gesamt": f"{ui.de(w_ * C.BENCHMARK['anleihen'], 1)} %",
                        "Betrag": _usd(w_ * C.BENCHMARK["anleihen"] / 100 * total),
                        "Rendite heute": f"{ui.de(P.kurve_rendite(k, float(T)), 2)} %" if k else "–"} for T, w_ in mix.items()])
    with st.expander(f"Treasury-Teil der Benchmark ({ctx.einst.get('bond_benchmark') or C.BOND_BENCHMARK_DEFAULT})"):
        st.dataframe(tr, hide_index=True, width="stretch")
        st.caption("Laufzeitmix als Näherung an den Treasury-Index (Rendite und Risiko der Benchmark kommen vom ETF in den Einstellungen).")
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        tab.to_excel(xw, sheet_name="Benchmark Aktien", index=False)
        tr.to_excel(xw, sheet_name="Benchmark Treasuries", index=False)
    st.download_button("Benchmark-Portfolio als Excel", buf.getvalue(), f"Benchmark-Portfolio_{datetime.now():%Y-%m-%d}.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", icon=":material/download:",
                       key="bv_bm_xlsx")
    if R is not None:
        R.neue_seite()
        R.h(f"Benchmark-Portfolio mit {_usd(total)}")
        R.tabelle(pd.DataFrame({"Ticker": tab["Ticker"], "Name": tab["Name"].str[:34], "Sektor": tab["Sektor"],
                                "Gewicht Aktien": tab["Gewicht Aktienteil %"].map(lambda x: f"{ui.de(x, 2)} %"),
                                "Betrag": tab["Betrag USD"].map(_usd),
                                "Unser Gewicht ges.": tab["Unser Gewicht gesamt %"].map(lambda x: f"{ui.de(x, 2)} %"),
                                "Aktiv": tab["Aktiv Pp."].map(lambda x: A._pp(x, 2))}), max_zeilen=40)
        R.text(f"Die 40 grössten von {len(tab)} Titeln. Vollständige Liste: Knopf «Benchmark-Portfolio als Excel».", klein=True)
        R.h2("Treasury-Teil")
        R.tabelle(tr)


def _tab_risiko(ctx, quelle, eingabe, rp, bf, k_p, k_b, dat, fred, lade_kurse, R):
    k_s = A.kennzahlen(bf["r_spy"], bf["rf"], bf["r_spy"])
    held = rp["w_aktien"].abs() > 1e-6
    k_a = A.kennzahlen(rp["r_aktien"][held], bf["rf"], bf["r_spy"]) if held.sum() >= 20 else {}
    if k_a:
        k_a.update(A.vs_benchmark(rp["r_aktien"][held], bf["r_spy"], k_a, k_s))
    k_b = dict(k_b)
    if dat.get("erg") is not None:
        for n_, k_ in (("Vorauswahl", k_p), ("Aktienteil", k_a)):
            for key in ("active_share", "active_share_ges"):
                v = dat["erg"]["k"].get(n_, {}).get(key)
                if v is not None:
                    k_[key] = v
    tabellen = []
    if k_p:
        st.markdown(f"**Kennzahlen im Zeitraum** ({ZEITRAUM.get(st.session_state.get('bv_zr'), '')})")
        vt = A.vergleichstabelle({"Unser Portfolio": k_p, "Unsere Aktien": k_a, A.BENCH: k_b, "S&P 500": k_s})
        st.dataframe(vt, hide_index=True, width="stretch", column_config={"Bedeutung": st.column_config.TextColumn(width="large")})
        tabellen.append(("Kennzahlen im Zeitraum", vt))
        dd = lambda r: ((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1) * 100  # noqa: E731
        fig = go.Figure([go.Scatter(x=rp.index, y=dd(rp["r_p"]), name="Unser Portfolio", line=dict(color=FARBE_P, width=2)),
                         go.Scatter(x=rp.index, y=dd(bf["r_b"]), name="Benchmark 50/50", line=dict(color=FARBE_B, width=1.6))])
        fig.update_layout(height=260, margin=dict(l=8, r=8, t=26, b=8), hovermode="x unified",
                          title=dict(text="Drawdown (Rückgang vom Höchststand)", font=dict(size=12)))
        fig.update_yaxes(ticksuffix=" %")
        ui.zeige(fig)
        if len(rp) >= 60:
            akt = (rp["r_p"] - bf["r_b"]).rolling(60).std() * math.sqrt(A.TAGE) * 100
            fig2 = go.Figure(go.Scatter(x=akt.index, y=akt, name="TE 60 Tage", line=dict(color=ui.NAVY, width=1.8)))
            if ctx.ips.get("te_max"):
                fig2.add_hline(y=float(ctx.ips["te_max"]), line=dict(color=ui.FARBE["Rot"], dash="dash"),
                               annotation_text=f"IPS-Grenze {ui.de(ctx.ips['te_max'], 1)} %")
            fig2.update_layout(height=230, margin=dict(l=8, r=8, t=26, b=8), showlegend=False,
                               title=dict(text="Tracking Error rollierend (60 Handelstage, p.a.)", font=dict(size=12)))
            fig2.update_yaxes(ticksuffix=" %")
            ui.zeige(fig2)
    else:
        st.info(f"Erst {len(rp)} Handelstage – für Volatilität, Tracking Error und Beta braucht es mindestens 20.")
    if dat.get("realisiert"):
        st.markdown("**Mit den heutigen Gewichten über die letzten 12 Monate** · zeigt das Risiko, das wir heute gegenüber der Benchmark tragen")
        st_ = _statisch(ctx, BESTAND, {}, 12, fred, lade_kurse)
        if st_ and not st_.get("fehler"):
            kk = st_["erg"]["k"]
            vt2 = A.vergleichstabelle({"Unser Portfolio (heute)": kk.get("Vorauswahl", {}), "Unsere Aktien": kk.get("Aktienteil", {}),
                                       A.BENCH: kk.get(A.BENCH, {}), "S&P 500": kk.get("S&P 500", {})})
            st.dataframe(vt2, hide_index=True, width="stretch", column_config={"Bedeutung": st.column_config.TextColumn(width="large")})
            tabellen.append(("Mit heutigen Gewichten (12 Monate)", vt2))
        else:
            st.caption((st_ or {}).get("fehler") or "Für die heutigen Gewichte fehlen Kursdaten.")
    st.caption("TE, IR und Active Share mit IPS-Grenzen und Ampeln zusätzlich im IPS-Abgleich (Seite Portfolio bzw. Analyse).")
    R.neue_seite()
    R.h("Risiko gegenüber der Benchmark")
    for t_, df_ in tabellen:
        R.h2(t_)
        R.tabelle(df_.drop(columns=["Bedeutung"]))
