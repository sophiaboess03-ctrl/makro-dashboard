"""Analyse mehrerer Analyseportfolios (oder des aktuellen Portfolios): Rendite-, Risiko- und Regelkennzahlen aus Kursdaten.

Methodik (auch in der App erklärt):
- Tagesschlusskurse von Yahoo Finance, dividendenbereinigt (Total Return).
- Konstante Gewichte (tägliches Rebalancing), annualisiert mit 252 Handelstagen.
- Risikofreier Zins: 13-Wochen-T-Bill (^IRX), Ersatzwert 4 %.
- Treasuries über liquide ETFs je Laufzeitband (Näherung). Cash verzinst wie in der Simulation mit SOFR − 0,5 % (Update 12;
  vor dem Start der SOFR-Reihe 13-Wochen-T-Bill − 0,5 %). Sharpe Ratio weiterhin gegen den T-Bill.
- Noch nicht verteiltes Budget: wahlweise als Index-Platzhalter (SPY bzw. Treasury-ETF des Benchmarks) oder als Cash.
- «Aktienteil» = nur die eingegebenen Titel, auf 100 % hochgerechnet (Risiko der Titel für sich allein).
- Aktien-Eingabe auch als «Gewicht %» (Bezug Gesamtportfolio oder Aktienteil); fixe % passen die Stückzahl an,
  wenn Kapital oder Aktien-% ändern.
- Mehrere Analyseportfolios: gleichzeitig offen, je id überschreibbar, löschbar (Papierkorb), Vergleich im gleichen Zeitfenster.
- Eingabe als Formular (Update 9): Tippen löst nichts aus, gerechnet wird mit «Änderungen übernehmen» bzw. «Übernehmen & speichern».
- Sektorgewichtung (Update 9): Ist je Sektor, Sektoranteile anpassen → Titel im Sektor proportional skaliert.
- Update 9.2: Gewicht % mit bis zu 4 Nachkommastellen, %-Titel gemeinsam auf die nächste ganze Stückzahl gerundet (Budget als
  Obergrenze, bei zu wenig Budget alle proportional gekürzt), Spalte «Ist %», Import aus dem Scoring-Modell (Excel).
"""
from __future__ import annotations

import json
import math
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutTimeout
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from makro import config as C
from makro import logic as L
from makro import ui
from storage import jetzt, zahl

TAGE = 252
RF_TICKER = "^IRX"
SPX = "SPY"            # S&P 500 inkl. Dividenden
RF_ERSATZ = 0.04
PROXY = {  # Laufzeitband → (ETF, Duration in Jahren, Näherung)
    "1–3 J.": ("SHY", 1.9),
    "3–7 J.": ("IEI", 4.3),
    "7–10 J.": ("IEF", 7.0),
    "10–20 J.": ("TLH", 13.0),
    "20+ J.": ("TLT", 16.0),
    "TIPS": ("TIP", 6.5),
}
PROXY_NAME = {v[0]: f"Treasuries {k}" for k, v in PROXY.items()}
NEUTRAL_PROXY = "GOVT"  # Treasury-Teil von Benchmark und neutralem Portfolio (wird von der App nach «Einstellungen» gesetzt)
BENCH_ETF = {"US-Treasury-Markt gesamt (≈ GOVT)": "GOVT", "Treasuries 3–7 J. (≈ IEI)": "IEI", "Treasuries 1–3 J. (≈ SHY)": "SHY",
             "Treasuries 7–10 J. (≈ IEF)": "IEF"}


def setze_benchmark(name: str):
    global NEUTRAL_PROXY
    NEUTRAL_PROXY = BENCH_ETF.get(name, "GOVT")


CASH_SOFR: pd.Series | None = None   # SOFR in % je Tag (von FRED, gesetzt von der App), für die Verzinsung des Cash


def setze_cashzins(fred: dict):
    """Cash in den Kennzahlen wie in der Simulation verzinsen: SOFR − Marge (cashzins.py)."""
    global CASH_SOFR
    import cashzins as CZ

    CASH_SOFR = CZ.basis(fred)[0]


def cash_rendite(index: pd.DatetimeIndex, rf: pd.Series) -> pd.Series:
    """Tagesrendite des Cash: max(SOFR − Marge, 0), Act/360; ohne SOFR (oder davor) T-Bill − Marge."""
    import cashzins as CZ

    ersatz = (rf.reindex(index).fillna(RF_ERSATZ / TAGE) * TAGE * 100 - CZ.MARGE).clip(lower=0)   # % p.a.
    if CASH_SOFR is not None and len(CASH_SOFR):
        s = CASH_SOFR.reindex(CASH_SOFR.index.union(index)).ffill().reindex(index)
        s[index < CASH_SOFR.index[0]] = np.nan
        satz = (s - CZ.MARGE).clip(lower=0).fillna(ersatz)
    else:
        satz = ersatz
    return satz / 100 * 365 / CZ.TAGE_BASIS / TAGE
KLUMPEN = 10.0          # % des Gesamtportfolios je Einzeltitel
BENCH = "Benchmark (50/50)"
BENCH_FARBE = "#B93A32"       # wie auf der Seite Portfolio   # Strategie des Dozenten: 50 % S&P 500 (SPY) / 50 % Treasuries (Treasury-ETF der Einstellungen)


# ---------------------------------------------------------------------------
# Daten
# ---------------------------------------------------------------------------
@st.cache_data(ttl=6 * 3600, show_spinner="Lade Kursgeschichte von Yahoo Finance …")
def lade_historie(tickers: tuple[str, ...], jahre: int):
    """Dividendenbereinigte Tageskurse. Gibt ({ticker: Series}, fehlende Ticker, Zeitpunkt) zurück."""
    from markt import _spalte

    def _hole():
        import yfinance as yf

        return yf.download(list(tickers), period=f"{jahre}y", interval="1d", group_by="ticker", auto_adjust=True,
                           progress=False, threads=False, timeout=20)

    ex = ThreadPoolExecutor(max_workers=1)
    try:
        df = ex.submit(_hole).result(timeout=75)
    except (FutTimeout, Exception):  # noqa: BLE001
        return {}, list(tickers), datetime.now().strftime("%d.%m.%Y %H:%M")
    finally:
        ex.shutdown(wait=False)
    daten, fehler = {}, []
    for t in tickers:
        try:
            s = _spalte(df, t)
            if s is not None and len(s.dropna()) > 20:
                s = s.dropna()
                s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
                daten[t] = s[~s.index.duplicated(keep="last")].astype(float)
            else:
                fehler.append(t)
        except Exception:  # noqa: BLE001
            fehler.append(t)
    return daten, fehler, datetime.now().strftime("%d.%m.%Y %H:%M")


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fundamentaldaten(ticker: str) -> dict:
    try:
        import yfinance as yf

        i = yf.Ticker(ticker).info or {}
        dy = i.get("dividendYield")
        if dy is not None and dy > 1:      # neuere yfinance-Versionen liefern Prozent
            dy = dy / 100
        return dict(kgv_fwd=i.get("forwardPE"), kgv=i.get("trailingPE"), div=dy, mcap=i.get("marketCap"))
    except Exception:  # noqa: BLE001
        return {}


def lade_fundamentaldaten(tickers: list[str]) -> dict:
    ex = ThreadPoolExecutor(max_workers=6)
    futs = {t: ex.submit(fundamentaldaten, t) for t in tickers}
    out = {}
    for t, f in futs.items():
        try:
            out[t] = f.result(timeout=25)
        except Exception:  # noqa: BLE001
            out[t] = {}
    ex.shutdown(wait=False)
    return out


# ---------------------------------------------------------------------------
# Rechenkern (ohne Streamlit, testbar)
# ---------------------------------------------------------------------------
def rf_reihe(hist: dict, index: pd.DatetimeIndex) -> pd.Series:
    s = hist.get(RF_TICKER)
    if s is None or s.empty:
        return pd.Series(RF_ERSATZ / TAGE, index=index)
    return (s.reindex(index.union(s.index)).ffill().reindex(index).fillna(RF_ERSATZ * 100) / 100 / TAGE)


def renditen(hist: dict, tickers: list[str]) -> tuple[pd.DataFrame, pd.Timestamp | None]:
    """Tagesrenditen im gemeinsamen Zeitfenster aller Ticker."""
    vorhanden = [t for t in tickers if t in hist]
    if not vorhanden:
        return pd.DataFrame(), None
    px = pd.concat({t: hist[t] for t in vorhanden}, axis=1).sort_index().ffill()
    start = max(hist[t].index.min() for t in vorhanden)
    px = px[px.index >= start].dropna()
    return px.pct_change().dropna(how="all").fillna(0.0), start


def kennzahlen(r: pd.Series, rf: pd.Series, bm: pd.Series | None = None) -> dict:
    r = r.dropna()
    n = len(r)
    if n < 20 or r.std() == 0:
        return {}
    rf = rf.reindex(r.index).fillna(RF_ERSATZ / TAGE)
    ex = r - rf
    sd = r.std(ddof=1)
    sd_ex = ex.std(ddof=1)
    wealth = (1 + r).cumprod()
    dd = wealth / wealth.cummax() - 1
    q5, q1 = r.quantile(0.05), r.quantile(0.01)
    downside = math.sqrt(float((np.minimum(ex, 0) ** 2).mean())) * math.sqrt(TAGE)
    out = dict(
        rendite=float(wealth.iloc[-1] ** (TAGE / n) - 1) * 100,
        gesamt=float(wealth.iloc[-1] - 1) * 100,
        vola=float(sd * math.sqrt(TAGE) * 100),
        sharpe=float(ex.mean() / sd_ex * math.sqrt(TAGE)) if sd_ex > 0 else np.nan,
        sortino=float(ex.mean() * TAGE / downside) if downside > 0 else np.nan,
        mdd=float(dd.min()) * 100,
        var95=float(-q5) * 100,
        cvar95=float(-r[r <= q5].mean()) * 100,
        var99=float(-q1) * 100,
        rf=float(rf.mean() * TAGE) * 100,
        tage=n,
        von=r.index[0], bis=r.index[-1],
    )
    out["calmar"] = out["rendite"] / abs(out["mdd"]) if out["mdd"] < 0 else np.nan
    if bm is not None:
        b = bm.reindex(r.index).dropna()
        rr = r.reindex(b.index)
        if len(b) >= 20 and b.var() > 0:
            beta = float(np.cov(rr, b)[0, 1] / b.var())
            rfb = rf.reindex(b.index)
            akt = rr - b
            te = float(akt.std(ddof=1) * math.sqrt(TAGE))
            out.update(beta=beta, alpha=float(((rr - rfb).mean() - beta * (b - rfb).mean()) * TAGE) * 100,
                       te=te * 100, ir=float(akt.mean() * TAGE / te) if te > 0 else np.nan,
                       korr=float(np.corrcoef(rr, b)[0, 1]))
    return out


def portfolio_reihe(R: pd.DataFrame, rf: pd.Series, gewichte: dict[str, float], cash: float) -> pd.Series:
    """Tagesrendite bei konstanten Gewichten; Cash verzinst wie in der Simulation (SOFR − 0,5 %, siehe cash_rendite)."""
    w = pd.Series({k: v for k, v in gewichte.items() if k in R.columns}, dtype=float)
    r = R[w.index].mul(w, axis=1).sum(axis=1) if len(w) else pd.Series(0.0, index=R.index)
    return r + (cash * cash_rendite(R.index, rf) if cash else 0.0)


def risikobeitraege(R: pd.DataFrame, gewichte: dict[str, float]) -> pd.Series:
    w = pd.Series(gewichte, dtype=float)
    w = w[w.index.isin(R.columns)]
    if w.empty:
        return pd.Series(dtype=float)
    cov = R[w.index].cov() * TAGE
    var = float(w @ cov @ w)
    return (w * (cov @ w)) / var * 100 if var > 0 else w * 0


def eff_anzahl(gewichte: pd.Series) -> float:
    g = gewichte[gewichte > 0]
    return float(1 / ((g / g.sum()) ** 2).sum()) if len(g) else 0.0


def baender(restlaufzeit: float | None, name: str = "") -> str:
    if "TIPS" in str(name).upper():
        return "TIPS"
    T = restlaufzeit or 0
    return "1–3 J." if T < 3 else "3–7 J." if T < 7 else "7–10 J." if T <= 10 else "10–20 J." if T < 20 else "20+ J."


def _num(x):
    try:
        if x is None or pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    v = zahl(x)
    return None if v is None or math.isnan(v) else v


def _txt(x) -> str:
    try:
        if x is None or pd.isna(x):
            return ""
    except (TypeError, ValueError):
        pass
    return str(x)


def zusammenstellen(aktien: pd.DataFrame, treas: pd.DataFrame, cash: float, kapital: float, rest_als_cash: bool,
                    letzte: dict[str, float]) -> tuple[pd.DataFrame, float, float, list[str]]:
    """Eingaben → Positionen mit Wert. Gibt (positionen, cash, total, hinweise) zurück."""
    zeilen, hinweise = [], []
    for _, z in aktien.iterrows():
        t = _txt(z.get("Ticker")).strip().upper().replace(".", "-")
        if not t:
            continue
        menge, betrag = _num(z.get("Menge")), _num(z.get("Betrag USD"))
        kurs = letzte.get(t)
        if kurs is None:
            hinweise.append(f"{t}: keine Kurse gefunden – Ticker prüfen (z. B. BRK-B statt BRK.B).")
            continue
        if menge:
            wert = menge * kurs
        elif betrag:
            wert, menge = betrag, betrag / kurs
        else:
            hinweise.append(f"{t}: weder Menge noch Betrag angegeben – ignoriert.")
            continue
        zeilen.append(dict(ticker=t, klasse="Aktie", menge=menge, kurs=kurs, wert=wert, band=None))
    for _, z in treas.iterrows():
        band, betrag = _txt(z.get("Laufzeit")), _num(z.get("Betrag USD"))
        faellig = pd.to_datetime(z.get("Fälligkeit"), errors="coerce") if "Fälligkeit" in z else None
        if faellig is not None and pd.notna(faellig):   # konkrete Anleihe: Band aus der Restlaufzeit
            band = baender(max(0.0, (faellig - pd.Timestamp.today()).days / 365.25), _txt(z.get("Bezeichnung")))
        if band in PROXY and betrag:
            zeilen.append(dict(ticker=PROXY[band][0], klasse="Treasury", menge=None, kurs=None, wert=betrag, band=band))
    pos = pd.DataFrame(zeilen, columns=["ticker", "klasse", "menge", "kurs", "wert", "band"])
    if len(pos):
        pos = pos.groupby(["ticker", "klasse"], as_index=False, dropna=False).agg(
            menge=("menge", "sum"), kurs=("kurs", "last"), wert=("wert", "sum"), band=("band", "first"))
    investiert = float(pos["wert"].sum()) if len(pos) else 0.0
    cash = float(_num(cash) or 0)
    if rest_als_cash and kapital and kapital > investiert + cash:
        cash = kapital - investiert
    total = investiert + cash
    if kapital and total > kapital * 1.001:
        hinweise.append(f"Summe {ui.de(total, 0)} USD übersteigt das Kapital von {ui.de(kapital, 0)} USD.")
    return pos, cash, total, hinweise


FUELLEN = ("Index", "Cash")   # nicht investiertes Budget: als Index-Platzhalter oder als Cash rechnen


def offenes_budget(pos: pd.DataFrame, cash: float, kapital: float, pct: dict | None) -> dict[str, float]:
    """Noch nicht verteiltes Budget je Klasse (Kapital × Klassen-% − investiert) → Platzhalter-ETF.
    Aktien → S&P 500 (SPY), Treasuries → Treasury-ETF des Benchmarks. Höchstens so viel, wie Cash da ist."""
    if not kapital or not pct:
        return {}
    inv_a = float(pos.loc[pos["klasse"] == "Aktie", "wert"].sum()) if len(pos) else 0.0
    inv_t = float(pos.loc[pos["klasse"] == "Treasury", "wert"].sum()) if len(pos) else 0.0
    fa = max(0.0, kapital * float(pct.get("aktien", 0)) / 100 - inv_a)
    ft = max(0.0, kapital * float(pct.get("treas", 0)) / 100 - inv_t)
    summe = fa + ft
    if summe > max(cash, 0.0) and summe > 0:          # Prozentsätze > 100 %: proportional kürzen
        fa, ft = fa * max(cash, 0.0) / summe, ft * max(cash, 0.0) / summe
    out = {}
    for t, w in ((SPX, fa), (NEUTRAL_PROXY, ft)):
        if w > 0.5:
            out[t] = out.get(t, 0.0) + w
    return out


def benchmark_reihe(R: pd.DataFrame, rf: pd.Series) -> pd.Series:
    """Tagesrendite der Benchmark des Dozenten: 50 % S&P 500 (SPY) / 50 % Treasuries (Treasury-ETF), täglich rebalanciert."""
    return portfolio_reihe(R, rf, {SPX: C.BENCHMARK["aktien"] / 100, NEUTRAL_PROXY: C.BENCHMARK["anleihen"] / 100},
                           C.BENCHMARK["cash"] / 100)


def vs_benchmark(r: pd.Series, r_b: pd.Series, k_r: dict, k_b: dict) -> dict:
    """Tracking Error, Information Ratio und Mehrrendite p.a. gegenüber der Benchmark 50/50."""
    out = {}
    if k_r.get("rendite") is not None and k_b.get("rendite") is not None:
        out["mehr_bench"] = float(k_r["rendite"] - k_b["rendite"])
    akt = (r - r_b.reindex(r.index)).dropna()
    if len(akt) >= 20 and akt.std() > 1e-12:
        te = float(akt.std(ddof=1) * math.sqrt(TAGE))
        out.update(te_bench=te * 100, ir_bench=float(akt.mean() * TAGE / te))
    return out


def active_share_alle(aktien_usd: dict, treas_usd: float, fuell: dict, cash_usd: float, total: float, na: float, nt: float) -> dict:
    """Active Share (in %) für Vorauswahl, Aktienteil, Benchmark, Neutral und S&P 500 – Aktien gegen den S&P 500 der
    Benchmark-Datei, «_ges» = Gesamtportfolio gegen 50/50/0. Leeres Dict, wenn keine Benchmark-Datei geladen ist."""
    try:
        import benchmark as BM

        if BM.aktuell() is None:
            return {}
        f_a = float((fuell or {}).get(SPX, 0.0))
        f_t = float((fuell or {}).get(NEUTRAL_PROXY, 0.0)) if NEUTRAL_PROXY != SPX else 0.0
        a = BM.active_share(aktien_usd) if aktien_usd else None
        v = BM.active_share(aktien_usd, index_betrag=f_a) if (aktien_usd or f_a) else None
        ab = C.BENCHMARK["aktien"] / 100
        return dict(
            aktien=a["active_share"] if a else None, vorauswahl=v["active_share"] if v else None, detail_aktien=a, detail_vorauswahl=v,
            bench=0.0, neutral=0.0, sp=0.0, bench_ges=0.0,
            vorauswahl_ges=BM.active_share_gesamt(aktien_usd, treas_usd + f_t, cash_usd, total, index_betrag=f_a),
            neutral_ges=(abs(na - ab) + abs(1 - na - nt - (1 - ab)) + nt) / 2 * 100,
            sp_ges=(abs(1 - ab) + (1 - ab)) / 2 * 100)
    except Exception:  # noqa: BLE001 – Active Share darf die Analyse nie verhindern
        return {}


def rechne(pos: pd.DataFrame, cash: float, total: float, hist: dict, neutral: dict,
           fuell: dict[str, float] | None = None) -> dict | None:
    """Alle Kennzahlen für Vorauswahl, Aktienteil allein, neutrales IC-Portfolio und S&P 500 im gleichen Zeitfenster.
    fuell = {ETF: USD} für noch nicht verteiltes Budget (wird statt Cash gerechnet)."""
    if total <= 0:
        return None
    gew = {t: float(w) / total for t, w in pos.groupby("ticker")["wert"].sum().items()} if len(pos) else {}
    fuell = {t: float(w) for t, w in (fuell or {}).items() if w > 0}
    for t, w in fuell.items():
        gew[t] = gew.get(t, 0.0) + w / total
    alle = sorted(set(gew) | {SPX, NEUTRAL_PROXY})
    R, start = renditen(hist, alle)
    if R.empty or len(R) < 20:
        return None
    rf = rf_reihe(hist, R.index)
    c = max(0.0, cash - sum(fuell.values())) / total
    r_p = portfolio_reihe(R, rf, gew, c)
    na, nt = neutral["aktien"] / 100, neutral.get("cash", 5) / 100
    r_n = portfolio_reihe(R, rf, {SPX: na, NEUTRAL_PROXY: 1 - na - nt}, nt)
    r_s = R[SPX]
    r_b = benchmark_reihe(R, rf)
    k = {"Vorauswahl": kennzahlen(r_p, rf, r_s)}
    # Aktienteil allein (Titel auf 100 % hochgerechnet, ohne Treasuries, Cash und Platzhalter)
    wa = pos[pos["klasse"] == "Aktie"].groupby("ticker")["wert"].sum() if len(pos) else pd.Series(dtype=float)
    wa = wa[wa.index.isin(R.columns) & (wa > 0)]
    gew_a = (wa / wa.sum()).to_dict() if len(wa) else {}
    r_a = portfolio_reihe(R, rf, gew_a, 0.0) if gew_a else None
    if r_a is not None:
        k["Aktienteil"] = kennzahlen(r_a, rf, r_s)
    k[BENCH] = kennzahlen(r_b, rf, r_s)
    k["Neutral (IPS)"] = kennzahlen(r_n, rf, r_s)
    k["S&P 500"] = kennzahlen(r_s, rf, r_s)
    for name_, rr in (("Vorauswahl", r_p), ("Aktienteil", r_a), ("Neutral (IPS)", r_n), ("S&P 500", r_s)):
        if rr is not None and k.get(name_):
            k[name_].update(vs_benchmark(rr, r_b, k[name_], k[BENCH]))
    # Active Share (Update 16): Aktien gegen die Titelgewichte des S&P 500 in der Benchmark-Datei des Dozenten
    wa_all = pos[pos["klasse"] == "Aktie"].groupby("ticker")["wert"].sum() if len(pos) else pd.Series(dtype=float)
    as_ = active_share_alle(wa_all.to_dict(), float(pos.loc[pos["klasse"] == "Treasury", "wert"].sum()) if len(pos) else 0.0,
                            fuell, c * total, total, na, nt)
    for name_, key_ in (("Vorauswahl", "vorauswahl"), ("Aktienteil", "aktien"), (BENCH, "bench"), ("Neutral (IPS)", "neutral"),
                        ("S&P 500", "sp")):
        if k.get(name_) and as_.get(key_) is not None:
            k[name_]["active_share"] = as_[key_]
        if k.get(name_) and as_.get(key_ + "_ges") is not None:
            k[name_]["active_share_ges"] = as_[key_ + "_ges"]
    akt = r_p - r_n
    if akt.std() > 0:
        te = float(akt.std(ddof=1) * math.sqrt(TAGE))
        k["Vorauswahl"]["te_neutral"] = te * 100
        k["Vorauswahl"]["ir_neutral"] = float(akt.mean() * TAGE / te)
    einzel = {}
    for t in gew:
        if t in R:
            einzel[t] = kennzahlen(R[t], rf, r_s)
            andere = r_p - R[t] * gew[t]
            einzel[t]["korr_rest"] = float(np.corrcoef(R[t], andere)[0, 1]) if andere.std() > 0 else np.nan
    return dict(R=R, rf=rf, r_p=r_p, r_n=r_n, r_s=r_s, r_a=r_a, r_b=r_b, k=k, as_=as_, einzel=einzel, gew=gew, gew_a=gew_a, cash=c, start=start,
                fuell=fuell, beitrag=risikobeitraege(R, gew), beitrag_a=risikobeitraege(R, gew_a) if gew_a else pd.Series(dtype=float))


def _proj_box(v: np.ndarray, lo: np.ndarray, hi: np.ndarray, summe: float) -> np.ndarray:
    """Projektion auf {lo ≤ w ≤ hi, Σw = summe} (Bisektion über die Verschiebung)."""
    a, b = float((v - hi).min()) - 1.0, float((v - lo).max()) + 1.0
    for _ in range(80):
        m = (a + b) / 2
        if np.clip(v - m, lo, hi).sum() > summe:
            a = m
        else:
            b = m
    return np.clip(v - (a + b) / 2, lo, hi)


def optimieren(R: pd.DataFrame, rf: pd.Series, r_b: pd.Series | None, gew: dict, cash: float, fix: set | None = None,
               sektor_von: dict | None = None, deckel: float = 0.05, mindest: float = 0.0, sektor_halten: bool = True,
               daempfung: float = 0.5, iterationen: int = 500) -> dict | None:
    """Gewichte des Aktienteils optimieren (Update 16, ersetzt die Zufallssuche): Treasuries, Cash und Platzhalter bleiben fix,
    die Summe der Aktien bleibt gleich (mit «Sektorgewichte halten» auch die Summe je Sektor = Sektorallokation bleibt).
    Grenzen je Titel: mindest ≤ Gewicht ≤ deckel (Anteil am Gesamtportfolio). Ziele:
      max_sharpe – höchste Sharpe Ratio (Überschussrendite / ihre Schwankung)
      min_vola   – tiefste Volatilität des Gesamtportfolios
      max_ir     – höchste Information Ratio gegenüber der Benchmark 50/50
    daempfung (0–1): erwartete Rendite je Titel = historische Rendite, zu diesem Anteil Richtung Durchschnitt aller Titel
    gezogen (gegen Zufallstreffer der Vergangenheit). Projizierter Gradientenanstieg – Sharpe/IR sind auf dieser Menge
    quasi-konkav, das Ergebnis ist also das globale Optimum unter den Grenzen."""
    fix = fix or set()
    aktien = [t for t in gew if t in R.columns and t not in {v[0] for v in PROXY.values()} and t not in fix]
    if len(aktien) < 2:
        return None
    w0 = np.array([gew[t] for t in aktien], dtype=float)
    anteil = float(w0.sum())
    fest = {t: w for t, w in gew.items() if t not in aktien}
    X = R[aktien].to_numpy()
    y = portfolio_reihe(R, rf, fest, cash).to_numpy()
    rfv = rf.reindex(R.index).fillna(RF_ERSATZ / TAGE).to_numpy()
    rbv = r_b.reindex(R.index).fillna(0.0).to_numpy() if r_b is not None else None
    mu = X.mean(axis=0)
    mu_s = (1 - daempfung) * mu + daempfung * mu.mean()
    S = np.cov(X, rowvar=False, ddof=1)
    Xc = X - X.mean(axis=0)
    # Gruppen (Sektoren) und Grenzen
    sv = sektor_von or {}
    if sektor_halten:
        gruppen = {}
        for i, t in enumerate(aktien):
            gruppen.setdefault(sv.get(t) or "Unbekannt", []).append(i)
    else:
        gruppen = {"alle": list(range(len(aktien)))}
    lo_all, hi_all, hinweise = np.full(len(aktien), mindest), np.full(len(aktien), deckel), []
    summen = {}
    for g, idx in gruppen.items():
        s_g = float(w0[idx].sum())
        summen[g] = s_g
        n = len(idx)
        if n * deckel < s_g - 1e-12:
            hi_all[idx] = s_g / n
            hinweise.append(f"{g}: {n} Titel tragen {ui.de(s_g * 100, 2)} % nicht mit höchstens {ui.de(deckel * 100, 2)} % je Titel – "
                            f"Obergrenze dort {ui.de(s_g / n * 100, 2)} %.")
        if n * mindest > s_g + 1e-12:
            lo_all[idx] = s_g / n

    def proj(v):
        out = np.empty_like(v)
        for g, idx in gruppen.items():
            out[idx] = _proj_box(v[idx], lo_all[idx], hi_all[idx], summen[g])
        return out

    def _quoti(ziel):                      # Ziel = y − ziel (rf oder Benchmark): Mittel/Std des Überschusses
        e = y - ziel
        m0, v0 = float(e.mean()), float(e.var(ddof=1))
        c = Xc.T @ (e - e.mean()) / (len(e) - 1)

        def f(w):
            m = m0 + float(mu_s @ w)
            v = max(v0 + 2 * float(c @ w) + float(w @ S @ w), 1e-18)
            sd = math.sqrt(v)
            return m / sd, mu_s / sd - m * (c + S @ w) / sd ** 3
        return f

    def _minvar():
        v0 = float(y.var(ddof=1))
        c = Xc.T @ (y - y.mean()) / (len(y) - 1)

        def f(w):
            return -(v0 + 2 * float(c @ w) + float(w @ S @ w)), -2 * (c + S @ w)
        return f

    def _maxi(f, start):
        w = proj(start)
        val, g = f(w)
        eta = 0.02 * anteil
        for _ in range(iterationen):
            d = g / (np.abs(g).max() + 1e-30)
            while True:
                w_n = proj(w + eta * d)
                val_n, g_n = f(w_n)
                if val_n >= val - 1e-15:
                    break
                eta *= 0.5
                if eta < 1e-10:
                    return w
            schritt = float(np.abs(w_n - w).max())
            w, val, g = w_n, val_n, g_n
            eta = min(eta * 1.4, anteil)
            if schritt < 1e-9:
                break
        return w

    ziele = {"max_sharpe": _quoti(rfv), "min_vola": _minvar()}
    if rbv is not None:
        ziele["max_ir"] = _quoti(rbv)
    erg = {k: dict(zip(aktien, _maxi(f, w0))) for k, f in ziele.items()}
    return dict(aktien=aktien, anteil=anteil, fest=fest, deckel=deckel, mindest=mindest, gruppen={g: [aktien[i] for i in idx]
                for g, idx in gruppen.items()}, hinweise=hinweise, daempfung=daempfung, **erg)


def pruefungen(pos: pd.DataFrame, cash: float, total: float, info: dict, alloc: dict) -> list[tuple[str, str]]:
    """Regel-Check gegen IPS-Bänder, Mandat (S&P 500), Klumpen und Duration. (Stufe, Text).
    Die Sektoren prüft sektoren.pruefe_portfolio (Seite «Sektorallokation»)."""
    out = []
    ak = float(pos.loc[pos["klasse"] == "Aktie", "wert"].sum()) / total * 100 if len(pos) else 0
    tr = float(pos.loc[pos["klasse"] == "Treasury", "wert"].sum()) / total * 100 if len(pos) else 0
    ca = cash / total * 100
    for name, wert, (lo, hi) in (("Aktien", ak, alloc["baender"]["aktien"]), ("Treasuries", tr, alloc["baender"]["treasuries"]),
                                 ("Cash", ca, alloc["baender"]["cash"])):
        out.append((L.GRUEN if lo <= wert <= hi else L.ROT,
                    f"{name} {ui.de(wert, 1)} % – IPS-Band {lo:g}–{hi:g} %"))
    for _, z in pos[pos["klasse"] == "Aktie"].iterrows():
        g = z["wert"] / total * 100
        if g > KLUMPEN:
            out.append((L.ROT, f"Klumpenrisiko: {z['ticker']} {ui.de(g, 1)} % (> {KLUMPEN:.0f} % des Portfolios)"))
        i = info.get(z["ticker"], {})
        if i.get("im_index") is False:
            out.append((L.ROT, f"{z['ticker']} ist nicht im S&P 500 – Mandat: nur S&P-500-Aktien"))
    t = pos[pos["klasse"] == "Treasury"]
    if len(t):
        dur = float(sum(PROXY[b][1] * w for b, w in zip(t["band"], t["wert"])) / t["wert"].sum())
        ziel = alloc["duration"]
        out.append((L.GRUEN if abs(dur - ziel) <= 1 else L.GELB,
                    f"Duration Treasuries ≈ {ui.de(dur, 1)} J. (ETF-Näherung) – Taktik {ui.de(ziel, 1)} J."))
        lang = t[t["band"].isin(["10–20 J.", "20+ J."])]
        if len(lang):
            out.append((L.GELB, "Laufzeiten über 10 J. enthalten – Regel aus der Analyse: nichts > 10 J."))
    return out


# ---------------------------------------------------------------------------
# Darstellung
# ---------------------------------------------------------------------------
def _pct(x, d=1, vz=False):
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{ui.de(x, d, vz)} %"


def _pp(x, d=1):
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{ui.de(x, d, True)} Pp."


def _zahl(x, d=2, vz=False):
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else ui.de(x, d, vz)


ZEILEN = [  # (Schlüssel, Bezeichnung, Formatierer, Erklärung)
    ("rendite", "Rendite p.a.", lambda x: _pct(x, 1, True), "annualisiert, inkl. Dividenden"),
    ("vola", "Volatilität p.a.", _pct, "Schwankung der Tagesrenditen, annualisiert"),
    ("sharpe", "Sharpe Ratio", _zahl, "(Rendite − risikofreier Zins) / Volatilität"),
    ("sortino", "Sortino Ratio", _zahl, "wie Sharpe, aber nur Verlustschwankungen"),
    ("mdd", "Maximaler Drawdown", lambda x: _pct(x, 1), "grösster Verlust vom Höchststand"),
    ("calmar", "Calmar Ratio", _zahl, "Rendite p.a. / |Max. Drawdown|"),
    ("beta", "Beta zum S&P 500", _zahl, "1 = bewegt sich wie der Markt"),
    ("alpha", "Jensen-Alpha p.a.", lambda x: _pct(x, 1, True), "Überrendite nach Beta-Risiko"),
    ("korr", "Korrelation zum S&P 500", _zahl, ""),
    ("mehr_bench", "Mehrrendite vs. Benchmark p.a.", lambda x: _pp(x), "Rendite p.a. − Benchmark des Dozenten (50/50), in Prozentpunkten"),
    ("te_bench", "Tracking Error vs. Benchmark", _pct, "Schwankung der Renditedifferenz zur Benchmark 50/50 – IPS-Grenze «höchster TE»"),
    ("ir_bench", "Information Ratio vs. Benchmark", _zahl, "Mehrrendite vs. Benchmark pro Einheit Tracking Error"),
    ("active_share", "Active Share (Aktien)", lambda x: _pct(x, 1), "½ Σ |Titelgewicht − Gewicht im S&P 500 der Benchmark| – IPS-Band"),
    ("active_share_ges", "Active Share (gesamt)", lambda x: _pct(x, 1), "Aktien, Treasuries (als Block) und Cash gegen 50/50/0"),
    ("te_neutral", "Tracking Error vs. neutral", _pct, "Abweichung vom neutralen Portfolio (IPS)"),
    ("ir_neutral", "Information Ratio vs. neutral", _zahl, "Mehrrendite pro Einheit Tracking Error"),
    ("var95", "VaR 95 % (1 Tag)", _pct, "Verlust, der an 19 von 20 Tagen nicht überschritten wurde"),
    ("cvar95", "CVaR 95 % (1 Tag)", _pct, "Durchschnittsverlust an den 5 % schlechtesten Tagen"),
    ("var99", "VaR 99 % (1 Tag)", _pct, ""),
]


def vergleichstabelle(k: dict) -> pd.DataFrame:
    spalten = list(k)
    zeilen = []
    for key, name, f, erkl in ZEILEN:
        if not any(key in k[s] for s in spalten):
            continue
        z = {"Kennzahl": name}
        for s in spalten:
            z[s] = f(k[s].get(key)) if key in k[s] else "–"
        z["Bedeutung"] = erkl
        zeilen.append(z)
    return pd.DataFrame(zeilen)


def _leer_aktien(n=6):
    return pd.DataFrame({"Ticker": [""] * n, "Menge": [None] * n, "Betrag USD": [None] * n}).astype(
        {"Ticker": "string", "Menge": "float64", "Betrag USD": "float64"})


# ---------------------------------------------------------------------------
# Eingabe: Tabelle rechnet sofort (Name, Sektor, Kurs, Stück ↔ Betrag), Zahlen mit Apostroph
# ---------------------------------------------------------------------------
AK_SPALTEN = ["Ticker", "Name", "Sektor", "Kurs USD", "Menge", "Betrag USD", "Gewicht %", "Fix"]
GESAMT, AKTIENTEIL = "Gesamtportfolio", "Aktienteil"     # Bezug der Spalte «Gewicht %»


def _basis(ss) -> float:
    """USD, auf die sich «Gewicht %» bezieht: Kapital oder Kapital × Aktien-%."""
    kap = _num(ss.get("an_kap_txt")) or 0.0
    return kap * float(ss.get("an_p_ak") or 0) / 100 if ss.get("an_bezug") == AKTIENTEIL else kap


def _fz(x, d: int = 0) -> str:
    """Zahl für die Eingabetabellen: 1'250'000 bzw. 412,35 (leer, wenn keine Zahl)."""
    v = _num(x)
    return "" if v is None else ui.de(v, d)


def _fz_pct(x) -> str:
    """Gewicht % mit 2–4 Nachkommastellen (0,5466 bleibt 0,5466, 2 wird 2,00) – eingetippte Gewichte gehen nicht verloren."""
    v = _num(x)
    if v is None:
        return ""
    ganz, _, dez = ui.de(v, 4).partition(",")
    return f"{ganz},{dez.rstrip('0').ljust(2, '0')}"


def _fz_menge(x) -> str:
    v = _num(x)
    return "" if v is None else ui.de(v, 0 if abs(v - round(v)) < 1e-9 else 2)


def _aktien_df(df: pd.DataFrame | None, mindestens_leer: int = 3) -> pd.DataFrame:
    """Eingabe-Tabelle der Aktien (alles Text, damit Beträge mit Apostroph erscheinen) + leere Zeilen am Ende."""
    df = pd.DataFrame(columns=AK_SPALTEN) if df is None else df.copy()
    for c in AK_SPALTEN:
        if c not in df.columns:
            df[c] = ""
    df = df[AK_SPALTEN].copy()
    for c in AK_SPALTEN:
        df[c] = [_txt(x) for x in df[c]]
    df["Menge"] = [_fz_menge(x) if _txt(x) else "" for x in df["Menge"]]
    df["Betrag USD"] = [_fz(x) if _txt(x) else "" for x in df["Betrag USD"]]
    df["Gewicht %"] = [_fz_pct(x) if _txt(x) else "" for x in df["Gewicht %"]]
    df = df[[bool(t.strip()) or bool(m) or bool(b) or bool(p)
             for t, m, b, p in zip(df["Ticker"], df["Menge"], df["Betrag USD"], df["Gewicht %"])]]
    leer = pd.DataFrame([{c: "" for c in AK_SPALTEN}] * mindestens_leer)
    return pd.concat([df, leer], ignore_index=True).astype("string")


def _editor_ansicht(df: pd.DataFrame) -> pd.DataFrame:
    """Aktien-Tabelle für den Editor: zusätzlich «Ist %» (tatsächliches Gewicht aus dem Betrag, gleicher Bezug wie
    «Gewicht %») und die Löschen-Spalte. «Ist %» ist nur Anzeige und wird nicht gespeichert."""
    basis = _basis(st.session_state)
    d = df.copy()
    ist = [_fz(_num(b_) / basis * 100, 2) if _num(b_) is not None and basis > 0 and _txt(t).strip() else ""
           for t, b_ in zip(d["Ticker"], d["Betrag USD"])]
    d.insert(d.columns.get_loc("Gewicht %") + 1, "Ist %", pd.Series(ist, index=d.index, dtype="string"))
    return d.assign(Löschen=False)


def _ticker_aufloesen(text: str, liste: dict) -> str:
    """Ticker oder Firmenname → Ticker (S&P-500-Liste), sonst der eingegebene Text in Grossbuchstaben."""
    roh = (text or "").strip()
    t = roh.upper().replace(".", "-")
    if not roh or t in liste or not liste:
        return t
    low = roh.lower()
    treffer = [k for k, (n, _) in liste.items() if n.lower().startswith(low)] or \
              [k for k, (n, _) in liste.items() if low in n.lower()]
    return sorted(treffer, key=len)[0] if treffer else t


def _letzte_kurse(ticker: list[str]) -> dict:
    import markt

    ticker = sorted({t for t in ticker if t})
    if not ticker:
        return {}
    k, _, _ = markt.lade_yahoo(tuple(ticker))
    return {t: float(s.dropna().iloc[-1]) for t, s in k.items() if s is not None and len(s.dropna())}


def _ak_rechnen(df: pd.DataFrame, i: int, spalten: set, kurse: dict | None = None):
    """Eine Zeile ergänzen: Ticker auflösen, Name/Sektor/Kurs holen, Stück → Betrag, Betrag → ganze Stücke,
    Gewicht % → Betrag → ganze Stücke. «Fix» merkt sich, was vorgegeben wurde (%, Stück oder USD)."""
    import portfolio as P

    roh = _txt(df.at[i, "Ticker"])
    if not roh.strip():
        df.loc[i, ["Name", "Sektor", "Kurs USD"]] = ""
        return
    if "Ticker" in spalten or not _txt(df.at[i, "Name"]):
        t = _ticker_aufloesen(roh, P.sp500_liste())
        info = P.ticker_info(t)
        kurs = (kurse or {}).get(t) if kurse is not None and t in (kurse or {}) else _letzte_kurse([t]).get(t)
        df.at[i, "Ticker"] = t
        df.at[i, "Name"] = info.get("name") or ("nicht gefunden – Ticker prüfen (z. B. BRK-B)" if kurs is None else "")
        df.at[i, "Sektor"] = info.get("sektor") or ""
        df.at[i, "Kurs USD"] = _fz(kurs, 2)
    kurs = _num(df.at[i, "Kurs USD"])
    menge, betrag, pct = _num(df.at[i, "Menge"]), _num(df.at[i, "Betrag USD"]), _num(df.at[i, "Gewicht %"])
    fix = _txt(df.at[i, "Fix"])
    if "Gewicht %" in spalten:
        fix = "%" if pct is not None else ("Stück" if menge else "")
    elif "Menge" in spalten:
        fix = "Stück"
    elif "Betrag USD" in spalten:
        fix = "USD"
    basis = _basis(st.session_state)
    if kurs:
        if fix == "%" and pct is not None and basis > 0:
            menge = math.floor(basis * pct / 100 / kurs)
            betrag = menge * kurs
        elif menge and ("Menge" in spalten or ("Ticker" in spalten and "Betrag USD" not in spalten)):
            betrag = menge * kurs
        elif betrag and ("Betrag USD" in spalten or not menge):
            menge = math.floor(betrag / kurs)
            betrag = menge * kurs
    if fix != "%":
        pct = betrag / basis * 100 if betrag is not None and basis > 0 else None
        fix = fix or ("Stück" if menge else "USD" if betrag else "")
    df.at[i, "Menge"] = _fz_menge(menge) if menge is not None else ""
    df.at[i, "Betrag USD"] = _fz(betrag) if betrag is not None else ""
    df.at[i, "Gewicht %"] = _fz_pct(pct) if pct is not None else ""
    df.at[i, "Fix"] = fix


def _aktien_neu_rechnen(df: pd.DataFrame) -> pd.DataFrame:
    """Alle Zeilen (z. B. nach «Laden»): Kurse in einem Abruf, Stück vor Betrag."""
    import portfolio as P

    df = _aktien_df(df)
    liste = P.sp500_liste()
    kurse = _letzte_kurse([_ticker_aufloesen(_txt(t), liste) for t in df["Ticker"]])
    for i in df.index:
        if _txt(df.at[i, "Ticker"]).strip():
            _ak_rechnen(df, i, {"Ticker"}, kurse)
    return df


def _treasury_kennzahlen(ctx, fred: dict, ss):
    """Unter der Eingabe (Update 14): Kupons, Marchzins, Ø Restlaufzeit, Konvexität usw. der erfassten Treasuries."""
    try:
        import anleihen as AN

        k = AN.kurve(fred)
        if len(k) < 5 or "an_treas" not in ss:
            return
        df = AN.anleihen_tabelle(_treas_df(ss["an_treas"]), k, ctx.szdef, ctx.szen)
        kz = AN.kennzahlen_kurz(df)
        if not kz:
            return
        bench = AN.benchmark_info(k, ctx.einst.get("bond_benchmark", C.BOND_BENCHMARK_DEFAULT))
        m_b = AN.mix(AN.tabelle(k, ctx.szdef, ctx.szen), bench["gewichte"], ctx.szdef)
        st.markdown(f"**Treasuries im Überblick** · {kz['n']} Anleihen, {ui.de(kz['betrag'], 0)} USD (übernommener Stand) – "
                    "Details unter «Anleihen & Cash»")
        k8 = AN.kpis_kurz(kz, bench=bench, konv_b=m_b["konv"])
        ui.kopf_kpis(k8[:4])
        ui.kopf_kpis(k8[4:])
        if (df["art"].str.startswith("nur Laufzeitband")).any():
            st.caption("Zeilen nur mit Laufzeitband zählen als Par-Anleihe in der Bandmitte (ohne Kupontermine und Marchzins) – "
                       "für genaue Werte Kupon und Fälligkeit ergänzen.")
    except Exception:  # noqa: BLE001
        pass


def _cash_zins_txt(cash: float, fred: dict, ctx) -> str:
    """Kurzer Zusatz: erwarteter Zins auf dem Cash bis Simulationsende (SOFR − 0,5 %)."""
    try:
        import cashzins as CZ

        a = CZ.aktuell(fred)
        if a is None or cash <= 0:
            return ""
        pg = CZ.prognose(cash, a["sofr"], ctx.szdef, ctx.szen)
        return f" · Zins bis 28.02. ≈ {ui.de(pg['total'], 0)} USD ({ui.de(a['netto'], 2)} %)"
    except Exception:  # noqa: BLE001
        return ""


def budget(ss) -> dict:
    """Kapital × Prozentsatz je Klasse, investiert, verfügbar (für Anzeige und Deckel)."""
    kap = _num(ss.get("an_kap_txt")) or 0.0
    p = dict(aktien=float(ss.get("an_p_ak") or 0), treas=float(ss.get("an_p_tr") or 0), cash=float(ss.get("an_p_ca") or 0))
    inv_a = sum(_num(x) or 0 for x in ss["an_aktien"]["Betrag USD"]) if "an_aktien" in ss else 0.0
    inv_t = sum(_num(x) or 0 for x in ss["an_treas"]["Betrag USD"]) if "an_treas" in ss else 0.0
    b_a, b_t = kap * p["aktien"] / 100, kap * p["treas"] / 100
    return dict(kapital=kap, pct=p, budget_a=b_a, budget_t=b_t, cash_ziel=kap * p["cash"] / 100, inv_a=inv_a, inv_t=inv_t,
                rest_a=b_a - inv_a, rest_t=b_t - inv_t, frei=kap - inv_a - inv_t, summe_pct=sum(p.values()))


def _meldung(text: str):
    st.session_state.setdefault("an_budget_meld", []).append(text)


def _ak_deckeln(df: pd.DataFrame, i: int, offen: set | None = None):
    """Zeile i so kürzen, dass die Aktien das Budget (Kapital × Aktien-%) nicht übersteigen. offen = Zeilen, die in derselben
    Änderung erst danach geprüft werden (zählen noch nicht – Reihenfolge von oben nach unten)."""
    ss = st.session_state
    b = budget(ss)
    if b["kapital"] <= 0 or _txt(df.at[i, "Fix"]) == "%":      # %-Zeilen: gemeinsam in _prozent_verteilen (Update 9.2)
        return
    andere = sum(_num(x) or 0 for j, x in zip(df.index, df["Betrag USD"]) if j != i and j not in (offen or set()))
    rest = max(0.0, b["budget_a"] - andere)
    betrag, kurs = _num(df.at[i, "Betrag USD"]) or 0.0, _num(df.at[i, "Kurs USD"])
    if betrag <= rest + 0.5:
        return
    t = _txt(df.at[i, "Ticker"]) or "Titel"
    if kurs:
        menge = math.floor(rest / kurs)
        df.at[i, "Menge"] = _fz_menge(menge)
        df.at[i, "Betrag USD"] = _fz(menge * kurs)
        _meldung(f"{t}: auf {ui.de(menge, 0)} Stück gekürzt – Aktienbudget ({ui.de(b['pct']['aktien'], 1)} % = "
                 f"{ui.de(b['budget_a'], 0)} USD) wäre sonst überschritten.")
    else:
        df.at[i, "Betrag USD"] = _fz(rest)
        _meldung(f"{t}: Betrag auf {ui.de(rest, 0)} USD gekürzt – Aktienbudget ausgeschöpft.")
    basis = _basis(ss)        # Gewicht % auf das gekürzte (machbare) Gewicht setzen
    neu = _num(df.at[i, "Betrag USD"])
    df.at[i, "Gewicht %"] = _fz_pct(neu / basis * 100) if neu is not None and basis > 0 else ""


def _prozent_verteilen(df: pd.DataFrame) -> pd.DataFrame:
    """Alle Zeilen mit fixem Gewicht % gemeinsam in ganze Stücke umrechnen (Update 9.2):
    - auf die **nächste** ganze Stückzahl gerundet (nicht mehr immer abgerundet), solange Summe der Ziele und Aktienbudget
      nicht überschritten werden – so liegen die Ist-Gewichte so nah wie möglich an den eingegebenen;
    - ergeben die %-Zeilen zusammen mehr als das freie Aktienbudget, werden alle im gleichen Verhältnis gekürzt
      (statt die untersten Zeilen auf 0 zu setzen). Das eingegebene Gewicht % bleibt stehen, «Ist %» zeigt das Erreichte."""
    ss = st.session_state
    df = df.copy()
    basis = _basis(ss)
    zeilen = [i for i in df.index if _txt(df.at[i, "Ticker"]).strip() and _txt(df.at[i, "Fix"]) == "%"
              and _num(df.at[i, "Gewicht %"]) is not None and _num(df.at[i, "Kurs USD"])]
    if not zeilen or basis <= 0:
        return df
    b = budget(ss)
    andere = sum(_num(df.at[j, "Betrag USD"]) or 0 for j in df.index if j not in zeilen and _txt(df.at[j, "Ticker"]).strip())
    frei = max(0.0, b["budget_a"] - andere) if b["kapital"] > 0 else float("inf")
    kurs = {i: _num(df.at[i, "Kurs USD"]) for i in zeilen}
    ziel = {i: basis * _num(df.at[i, "Gewicht %"]) / 100 for i in zeilen}
    total = sum(ziel.values())
    if total > frei * 1.0001 + 0.5:                 # kleine Rundungsdifferenzen (z. B. 45,00001 %) nicht melden
        f = frei / total
        ziel = {i: v * f for i, v in ziel.items()}
        _meldung(f"Die Titel mit fixem Gewicht % brauchen zusammen {ui.de(total, 0)} USD, im Aktienbudget sind noch "
                 f"{ui.de(frei, 0)} USD frei (Aktien {ui.de(b['pct']['aktien'], 2)} % = {ui.de(b['budget_a'], 0)} USD) – "
                 f"alle wurden im gleichen Verhältnis auf {ui.de(f * 100, 1)} % gekürzt. Stimmt «Aktien %» mit eurem Modell überein?")
    grenze = min(sum(ziel.values()), frei)
    menge = {i: math.floor(ziel[i] / kurs[i] + 1e-9) for i in zeilen}
    rest = grenze - sum(menge[i] * kurs[i] for i in zeilen)
    bruch = sorted(zeilen, key=lambda i: -(ziel[i] / kurs[i] - menge[i]))
    for i in bruch:                      # aufrunden, wo ≥ ½ Stück fehlt und es noch Platz hat (grösster Rest zuerst)
        if ziel[i] / kurs[i] - menge[i] >= 0.5 and kurs[i] <= rest + 1e-6:
            menge[i] += 1
            rest -= kurs[i]
    # Update 11: übrig gebliebenes Budget weiter auffüllen (grösster fehlender Bruchteil zuerst, je Titel höchstens 1 Stück
    # über dem abgerundeten Wert) – so kommt die Summe so nah wie möglich an 100 % bzw. die Aktienquote heran.
    for i in bruch:
        if menge[i] * kurs[i] < ziel[i] - 1e-6 and kurs[i] <= rest + 1e-6:
            menge[i] += 1
            rest -= kurs[i]
    for i in zeilen:
        df.at[i, "Menge"] = _fz_menge(menge[i])
        df.at[i, "Betrag USD"] = _fz(menge[i] * kurs[i])
    return df


def _basis_aenderung():
    """Kapital oder Aktien-% geändert: Zeilen mit fixem Gewicht % → neue Stückzahl, übrige Zeilen → neues Gewicht %.
    Zeilen mit fixem % werden danach gemeinsam verteilt (_prozent_verteilen)."""
    ss = st.session_state
    if "an_aktien" not in ss:
        return
    df = ss["an_aktien"].copy().reset_index(drop=True)
    belegt = [i for i in df.index if _txt(df.at[i, "Ticker"]).strip()]
    for i in belegt:
        _ak_rechnen(df, i, set())
    ss["an_aktien"] = _aktien_df(_prozent_verteilen(df))
    ss["an_ak_ver"] = ss.get("an_ak_ver", 0) + 1


SORTIERUNG = {"sektor": "Sektor, dann A–Z", "ticker": "Ticker A–Z", "name": "Name A–Z", "keine": "Reihenfolge lassen"}


def _sortieren(df: pd.DataFrame, art: str) -> pd.DataFrame:
    """Aktien-Tabelle sortieren (Update 9.1): nach Sektor (alphabetisch, ohne Sektor am Schluss) und Ticker, nach Ticker
    oder nach Name. Leere Zeilen bleiben unten."""
    df = _aktien_df(df, 0).reset_index(drop=True)
    if art not in ("sektor", "ticker", "name") or not len(df):
        return _aktien_df(df)
    voll = [i for i in df.index if _txt(df.at[i, "Ticker"]).strip()]
    rest = [i for i in df.index if i not in voll]

    def _k(i):
        t = _txt(df.at[i, "Ticker"]).strip().upper()
        if art == "sektor":
            sek = _txt(df.at[i, "Sektor"]).strip()
            return (sek == "", sek.lower(), t)
        if art == "name":
            n = _txt(df.at[i, "Name"]).strip()
            return (n == "" or n.startswith("nicht gefunden"), n.lower(), t)
        return (t,)
    return _aktien_df(df.loc[sorted(voll, key=_k) + rest])


def _form_anwenden(sfx: str, aktion: str, sortierung: str | None = None):
    """Formular «Eingabe» abgeschickt (Update 9): Erst jetzt werden Aufteilung, Bezug, Aktien- und Treasury-Tabelle und
    die Suche übernommen und gerechnet – während des Tippens passiert nichts, die Tabelle springt also nicht.
    aktion: «uebernehmen», «speichern» (übernehmen + Portfolio speichern) oder «zurueck» (Eingaben verwerfen).
    sortierung: neue Sortierung der Aktien-Tabelle (bleibt für weitere Übernahmen gesetzt, neue Titel landen am richtigen Ort)."""
    ss = st.session_state
    if aktion == "zurueck":
        ss["an_form_ver"] = ss.get("an_form_ver", 0) + 1
        return
    alt_kap = _num(ss.get("an_kap_txt")) or 0.0
    alt_pak = float(ss.get("an_p_ak") or 0)
    alt_bez = ss.get("an_bezug", GESAMT)
    neu_kap = _num(ss.get(f"an_f_kap{sfx}"))
    if neu_kap is None or neu_kap < 0:
        if _txt(ss.get(f"an_f_kap{sfx}")).strip():
            _meldung("Kapital nicht erkannt – der bisherige Wert bleibt.")
        neu_kap = alt_kap
    neu_pak = float(ss.get(f"an_f_pak{sfx}", alt_pak) or 0)
    neu_bez = ss.get(f"an_f_bez{sfx}") or alt_bez
    # 1. Bezug umgestellt: fixe % auf den neuen Bezug umrechnen (Beträge bleiben), Zeilen bleiben an ihrem Platz,
    #    damit die Änderungen der Tabelle (Zeilennummern) danach richtig zugeordnet werden.
    if neu_bez != alt_bez and alt_pak > 0 and "an_aktien" in ss:
        df = ss["an_aktien"].copy().reset_index(drop=True)
        for i in df.index:
            pct = _num(df.at[i, "Gewicht %"])
            if _txt(df.at[i, "Fix"]) == "%" and pct is not None:
                df.at[i, "Gewicht %"] = _fz_pct(pct / (alt_pak / 100) if neu_bez == AKTIENTEIL else pct * alt_pak / 100)
        ss["an_aktien"] = df
    # 2. Aufteilung setzen
    ss.update(an_kap_txt=ui.de(neu_kap, 0), an_kapital=float(neu_kap), an_p_ak=neu_pak,
              an_p_tr=float(ss.get(f"an_f_ptr{sfx}", ss.get("an_p_tr")) or 0),
              an_p_ca=float(ss.get(f"an_f_pca{sfx}", ss.get("an_p_ca")) or 0), an_bezug=neu_bez)
    # 3. Tabellen (nur, was geändert wurde, wird gerechnet; Deckel aufs Budget wie bisher)
    if "an_aktien" in ss:
        _ak_aenderung(f"an_ed_a{sfx}")
    if "an_treas" in ss:
        _tr_aenderung(f"an_ed_t{sfx}")
    # 4. Kapital, Aktien-% oder Bezug geändert → Zeilen mit fixem Gewicht % bekommen die neue Stückzahl
    if abs(neu_kap - alt_kap) > 0.5 or abs(neu_pak - alt_pak) > 1e-9 or neu_bez != alt_bez:
        _basis_aenderung()
    # 5. Suchfeld: Titel anfügen
    if such := ss.get(f"an_f_such{sfx}"):
        ss.update(an_such=such, an_such_stk=ss.get(f"an_f_stk{sfx}") or 0, an_such_pct=ss.get(f"an_f_pct{sfx}") or 0.0)
        _such_hinzu()
    if "an_aktien" in ss:
        ss["an_aktien"] = _aktien_df(_prozent_verteilen(ss["an_aktien"]))
    if sortierung:
        ss["an_sort"] = sortierung
    if ss.get("an_sort") in ("sektor", "ticker", "name") and "an_aktien" in ss:
        ss["an_aktien"] = _sortieren(ss["an_aktien"], ss["an_sort"])
    ss["an_form_ver"] = ss.get("an_form_ver", 0) + 1
    if aktion == "speichern":
        ss["an_pf_save_req"] = True


# ---------------------------------------------------------------------------
# Sektorgewichtung des Analyseportfolios (Update 9): Ist je Sektor rechnen, Sektoranteile anpassen,
# Titel im Sektor proportional mitziehen (Verhältnis der Titel untereinander bleibt gleich).
# ---------------------------------------------------------------------------
UNBEKANNT = "Unbekannt"


def _sektor_von(x) -> str:
    s = _txt(x).strip()
    return s if s in C.SEKTOREN else UNBEKANNT


def sektor_ist(df: pd.DataFrame) -> tuple[dict, float]:
    """Ist-Anteil je Sektor in % der investierten Aktien (wie die Sektorallokation) und investierte Summe in USD.
    Reihenfolge wie C.SEKTOREN, «Unbekannt» am Schluss."""
    w: dict[str, float] = {}
    for t, s, b in zip(df["Ticker"], df["Sektor"], df["Betrag USD"]):
        v = _num(b) or 0.0
        if _txt(t).strip() and v > 0:
            k = _sektor_von(s)
            w[k] = w.get(k, 0.0) + v
    summe = sum(w.values())
    reihe = [s for s in C.SEKTOREN + [UNBEKANNT] if s in w]
    return ({s: w[s] / summe * 100 for s in reihe} if summe else {}), summe


def sektor_anteile(ist: dict, neu: dict, ausgleich: bool = True) -> dict:
    """Neue Sektoranteile (% des Aktienteils). Geänderte Sektoren gelten wie eingegeben. Mit ausgleich werden die übrigen
    proportional angepasst, damit die Summe 100 % bleibt (geht nicht, wenn die geänderten schon > 100 % ergeben)."""
    out = {s: max(0.0, float(neu[s])) if neu.get(s) is not None else ist[s] for s in ist}
    geaendert = {s for s in ist if abs(out[s] - ist[s]) > 0.006}        # 0,006: gerundete Anzeige gilt als unverändert
    uebrig = [s for s in ist if s not in geaendert]
    rest = 100.0 - sum(out[s] for s in geaendert)
    u = sum(ist[s] for s in uebrig)
    if ausgleich and geaendert and uebrig and u > 0 and rest >= 0:
        for s in uebrig:
            out[s] = ist[s] * rest / u
    return out


def sektor_skalieren(df: pd.DataFrame, anteile: dict, ziel_summe: float, basis: float) -> pd.DataFrame:
    """Titel je Sektor proportional auf den neuen Sektoranteil bringen: Betrag × (Ziel des Sektors / Ist des Sektors),
    dann ganze Stücke (abgerundet). Das Verhältnis der Titel innerhalb des Sektors bleibt gleich.
    ziel_summe = Aktien gesamt in USD nach dem Anpassen (anteile in % davon); basis = USD-Basis von «Gewicht %»."""
    df = df.copy().reset_index(drop=True)
    ist, summe = sektor_ist(df)
    wert_s = {s: ist[s] * summe / 100 for s in ist}
    for i in df.index:
        b = _num(df.at[i, "Betrag USD"]) or 0.0
        s = _sektor_von(df.at[i, "Sektor"])
        if not _txt(df.at[i, "Ticker"]).strip() or b <= 0 or s not in anteile or wert_s.get(s, 0) <= 0:
            continue
        ziel = b * (ziel_summe * anteile[s] / 100) / wert_s[s]
        kurs = _num(df.at[i, "Kurs USD"])
        if kurs:
            menge = math.floor(ziel / kurs + 1e-9)
            betrag = menge * kurs
            df.at[i, "Menge"] = _fz_menge(menge)
        else:
            betrag = ziel
        df.at[i, "Betrag USD"] = _fz(betrag)
        df.at[i, "Gewicht %"] = _fz_pct(betrag / basis * 100) if basis > 0 else ""
        if _txt(df.at[i, "Fix"]) != "%":           # %-Zeilen bleiben %-fix (mit neuem Gewicht), sonst gilt die neue Stückzahl
            df.at[i, "Fix"] = "Stück" if kurs else "USD"
    return df


def _sek_form(sfx: str, aktion: str, ziele: dict):
    """Formular «Sektorgewichtung» abgeschickt: anwenden, Ziele der Sektorallokation einsetzen oder auf Ist zurücksetzen."""
    ss = st.session_state
    ss["an_sek_ver"] = ss.get("an_sek_ver", 0) + 1
    if aktion == "ist":
        ss.pop("an_sek_vorschlag", None)
        return
    df = ss["an_aktien"]
    ist, summe = sektor_ist(df)
    if aktion == "ziel":
        z = {s: ziele.get(s) for s in ist if s in ziele and ziele.get(s) is not None}
        tot = sum(z.values())
        if tot > 0:
            ss["an_sek_vorschlag"] = dict(ver=ss.get("an_ak_ver", 0), werte={s: round(v / tot * 100, 2) for s, v in z.items()},
                                          roh=z)
        return
    zeilen = ss.get("an_sek_zeilen") or []
    neu = {s: v for s, v in zeilen}
    for i, w in ((ss.get(f"an_sek_ed{sfx}") or {}).get("edited_rows") or {}).items():
        if int(i) < len(zeilen) and "Neu %" in w:
            neu[zeilen[int(i)][0]] = _num(w["Neu %"])
    neu = {s: v for s, v in neu.items() if s in ist}
    if not ist:
        return
    b = budget(ss)
    ziel_summe = b["budget_a"] if ss.get(f"an_sek_summe{sfx}") == "budget" and b["budget_a"] > 0 else summe
    ausgleich = bool(ss.get(f"an_sek_ausgl{sfx}", True))
    anteile = sektor_anteile(ist, neu, ausgleich)
    if ausgleich and abs(sum(anteile.values()) - 100) > 0.05:
        _meldung(f"Die Sektoranteile ergeben {ui.de(sum(anteile.values()), 1)} % – die geänderten Sektoren allein sind schon über "
                 "100 % (oder alle wurden geändert), darum konnte nicht ausgeglichen werden. Die Aktiensumme ändert sich entsprechend.")
    total = ziel_summe * sum(anteile.values()) / 100
    if b["kapital"] > 0 and total > b["budget_a"] + 0.5:     # nie übers Aktienbudget: alles im gleichen Verhältnis kürzen
        f = b["budget_a"] / total
        anteile = {s: v * f for s, v in anteile.items()}
        _meldung(f"Die Sektoranteile ergeben zusammen mehr als das Aktienbudget ({ui.de(b['budget_a'], 0)} USD) – alle Sektoren "
                 f"wurden im gleichen Verhältnis auf {ui.de(f * 100, 1)} % gekürzt.")
    ss["an_aktien"] = _aktien_df(sektor_skalieren(df, anteile, ziel_summe, _basis(ss)))
    ss["an_ak_ver"] = ss.get("an_ak_ver", 0) + 1
    ss.pop("an_sek_vorschlag", None)
    neu_ist, neu_summe = sektor_ist(ss["an_aktien"])
    geaendert = [s for s in ist if abs(neu_ist.get(s, 0) - ist[s]) >= 0.05]
    _meldung("Sektorgewichte angewendet" + (f" ({', '.join(geaendert)})" if geaendert else " – nichts geändert")
             + f": Aktien gesamt {ui.de(summe, 0)} → {ui.de(neu_summe, 0)} USD. Titel innerhalb eines Sektors behalten ihr Verhältnis "
               "(ganze Stücke, abgerundet).")


# ---------------------------------------------------------------------------
# Import aus dem Scoring-Modell (Excel, Blatt «Portfolio») – Update 9.2
# ---------------------------------------------------------------------------
def _kopf(x) -> str:
    return "".join(str(x or "").lower().split()).replace("-", "").replace("\u00ad", "")


def scoring_lesen(datei) -> dict:
    """Blatt «Portfolio» des Scoring-Modells lesen: Titel mit Sektor, Name, Gewicht Aktienteil und Gesamtportfolio (in %),
    Aktienquote (Zelle «Soll: Aktienquote», sonst Summe Gesamt / Summe Aktienteil). Liest die in Excel gespeicherten Werte."""
    import openpyxl

    try:
        wb = openpyxl.load_workbook(datei, data_only=True, read_only=True)
    except Exception as e:  # noqa: BLE001
        return dict(fehler=f"Datei nicht lesbar: {e}")
    if "Portfolio" not in wb.sheetnames:
        return dict(fehler="Blatt «Portfolio» nicht gefunden – ist das das Scoring-Modell?")
    rows = [list(r) for r in wb["Portfolio"].iter_rows(min_row=1, max_row=600, values_only=True)]
    wb.close()
    kopf = None
    for r_i, r in enumerate(rows[:30]):
        n = [_kopf(c) for c in r]
        if "ticker" in n and any(x.startswith("gewichtaktienteil") for x in n):
            kopf = r_i
            c_t = n.index("ticker")
            c_a = next(i for i, x in enumerate(n) if x.startswith("gewichtaktienteil"))
            c_g = next((i for i, x in enumerate(n) if x.startswith("gewichtgesamt")), None)
            c_s = n.index("sektor") if "sektor" in n else None
            c_n = n.index("bezeichnung") if "bezeichnung" in n else None
            break
    if kopf is None:
        return dict(fehler="Im Blatt «Portfolio» keine Kopfzeile mit «Ticker» und «Gewicht Aktienteil» gefunden.")

    def _w(r, c):
        v = r[c] if c is not None and c < len(r) else None
        return float(v) if isinstance(v, (int, float)) else _num(v)

    # Update 11: Leerzeilen (z. B. zwischen Sektoren) beenden die Liste nicht mehr; Zwischentitel, Summenzeilen und doppelte
    # Ticker werden gemeldet statt still übergangen. Schluss erst bei «Summe/Total» oder nach 25 leeren Zeilen.
    titel, pos, uebersprungen, leer = [], {}, [], 0
    for z_nr, r in enumerate(rows[kopf + 1:], start=kopf + 2):
        t = r[c_t] if c_t < len(r) else None
        roh = str(t).strip() if t is not None else ""
        if not roh:
            leer += 1
            if leer >= 25:
                break
            continue
        leer = 0
        if _kopf(roh).startswith(("summe", "total", "gesamt", "sum")):
            break
        tk = roh.upper().replace(".", "-").replace(" ", "-").replace("/", "-")
        akt = _w(r, c_a)
        if not re.fullmatch(r"[A-Z][A-Z0-9]{0,5}(-[A-Z])?", tk):
            if akt:
                uebersprungen.append(f"Zeile {z_nr}: «{roh}» ist kein Ticker (Gewicht {akt:g})")
            continue
        if tk in pos:                          # doppelter Ticker: Gewichte zusammenzählen
            x = pos[tk]
            x["akt"] = (x["akt"] or 0) + (akt or 0) if (x["akt"] is not None or akt is not None) else None
            g_ = _w(r, c_g)
            x["ges"] = (x["ges"] or 0) + (g_ or 0) if (x["ges"] is not None or g_ is not None) else None
            uebersprungen.append(f"Zeile {z_nr}: {tk} kommt doppelt vor – Gewichte zusammengezählt")
            continue
        pos[tk] = dict(ticker=tk, akt=akt, ges=_w(r, c_g), zeile=z_nr,
                       sektor=str(r[c_s] or "") if c_s is not None else "", name=str(r[c_n] or "") if c_n is not None else "")
        titel.append(pos[tk])
    if not titel:
        return dict(fehler="Keine Titel gefunden.")
    for x in titel:
        if not x["akt"]:
            uebersprungen.append(f"Zeile {x['zeile']}: {x['ticker']} hat kein Gewicht im Aktienteil – wird nicht übernommen")
    if all(x["akt"] is None for x in titel):
        return dict(fehler="Die Gewichte sind leer – die Datei einmal in Excel öffnen und speichern (dann sind die Formelwerte drin).")
    s_a = sum(x["akt"] or 0 for x in titel)
    s_g = sum(x["ges"] or 0 for x in titel)
    faktor = 100.0 if s_a <= 1.5 else 1.0                 # Excel speichert 1,21 % als 0,0121
    for x in titel:
        x["akt"] = x["akt"] * faktor if x["akt"] is not None else None
        x["ges"] = x["ges"] * faktor if x["ges"] is not None else None
    quote = None
    for r in rows[:60]:
        for j, c in enumerate(r):
            if _kopf(c).startswith("soll:aktienquote"):
                quote = next((v for v in r[j + 1:] if isinstance(v, (int, float))), None)
                break
        if quote is not None:
            break
    if quote is not None:
        quote = quote * 100 if quote <= 1.5 else quote
    elif s_a:
        quote = s_g / s_a * 100
    return dict(titel=titel, quote=quote, summe_akt=s_a * faktor, summe_ges=s_g * faktor, uebersprungen=uebersprungen)


def _scoring_uebernehmen():
    """Titel aus dem Scoring-Modell ins aktive Analyseportfolio: Bezug «Aktienteil», Aktien % = Aktienquote des Modells,
    Treasuries = 100 − Aktien − Cash, alle Titel mit fixem Gewicht % (volle Genauigkeit), Kurse in einem Abruf."""
    ss = st.session_state
    d = ss.get("an_sc") or {}
    if not d.get("titel"):
        return
    q = float(d.get("quote") or ss.get("an_p_ak") or 0)
    ca = float(ss.get("an_p_ca") or 0)
    ss.update(an_p_ak=round(q, 4), an_p_tr=round(max(0.0, 100 - q - ca), 4), an_bezug=AKTIENTEIL, an_modell_quote=round(q, 4))
    df = pd.DataFrame([{"Ticker": x["ticker"], "Gewicht %": _fz_pct(x["akt"]), "Fix": "%"} for x in d["titel"] if x["akt"]])
    df = _prozent_verteilen(_aktien_neu_rechnen(df))
    if ss.get("an_sort") in ("sektor", "ticker", "name"):
        df = _sortieren(df, ss["an_sort"])
    ss["an_aktien"] = _aktien_df(df)
    for k in ("an_ak_ver", "an_form_ver"):
        ss[k] = ss.get(k, 0) + 1
    ss["an_sc_abgleich"] = dict(datei=d.get("datei", ""), zeit=datetime.now().strftime("%d.%m.%Y %H:%M"), quote=q,
                                soll={x["ticker"]: float(x["akt"]) for x in d["titel"] if x["akt"]},
                                summe=float(d.get("summe_akt") or 0), uebersprungen=list(d.get("uebersprungen") or []))
    ohne = [_txt(t) for t, k in zip(ss["an_aktien"]["Ticker"], ss["an_aktien"]["Kurs USD"]) if _txt(t).strip() and not _num(k)]
    n_t = sum(1 for x in d["titel"] if x["akt"])
    _meldung(f"{n_t} Titel aus dem Scoring-Modell übernommen · Bezug «Aktienteil» · Aktien {ui.de(q, 2)} % · Treasuries "
             f"{ui.de(max(0.0, 100 - q - ca), 2)} % · Cash {ui.de(ca, 2)} %."
             + (f" Ohne Kurs (Ticker prüfen): {', '.join(ohne)}." if ohne else ""))


def scoring_abgleich(ss) -> dict | None:
    """Update 11: Vergleich des aktiven Analyseportfolios mit dem zuletzt importierten Scoring-Modell. Ist-Gewichte im Aktienteil
    des Modells (Betrag / (Kapital × Aktienquote des Modells)), Differenz nach Ursachen: kein Kurs, nicht mehr in der Tabelle,
    zusätzliche Titel, Rundung auf ganze Stücke."""
    a = ss.get("an_sc_abgleich")
    if not a or "an_aktien" not in ss:
        return None
    kap = _num(ss.get("an_kap_txt")) or 0.0
    basis = kap * float(a.get("quote") or 0) / 100
    if basis <= 0:
        return None
    df = ss["an_aktien"]
    zeile = {}
    for t, k, b_, m in zip(df["Ticker"], df["Kurs USD"], df["Betrag USD"], df["Menge"]):
        t = _txt(t).strip().upper()
        if t:
            zeile[t] = dict(kurs=_num(k), betrag=_num(b_) or 0.0, menge=_num(m))
    out, ursache = [], dict(kein_kurs=0.0, fehlt=0.0, extra=0.0)
    for t, soll in a["soll"].items():
        z = zeile.get(t)
        ist = (z["betrag"] / basis * 100) if z else 0.0
        if z is None:
            grund = "nicht mehr in der Tabelle"
            ursache["fehlt"] += soll
        elif not z["kurs"]:
            grund = "kein Kurs von Yahoo"
            ursache["kein_kurs"] += soll
        else:
            grund = "ganze Stücke" if abs(ist - soll) >= 0.0005 else ""
        out.append(dict(Ticker=t, **{"Excel %": soll, "App Ist %": ist, "Diff. Pp.": ist - soll},
                        Stück=(z or {}).get("menge"), **{"Kurs USD": (z or {}).get("kurs")}, Grund=grund))
    for t, z in zeile.items():
        if t not in a["soll"] and z["betrag"]:
            ist = z["betrag"] / basis * 100
            ursache["extra"] += ist
            out.append(dict(Ticker=t, **{"Excel %": 0.0, "App Ist %": ist, "Diff. Pp.": ist}, Stück=z["menge"],
                            **{"Kurs USD": z["kurs"]}, Grund="nicht im Modell (zusätzlich)"))
    tab = pd.DataFrame(out)
    s_soll, s_ist = float(sum(a["soll"].values())), float(tab["App Ist %"].sum()) if len(tab) else 0.0
    rundung = (s_ist - s_soll) + ursache["kein_kurs"] + ursache["fehlt"] - ursache["extra"]
    ohne_kurs = [r["Ticker"] for r in out if r["Grund"] == "kein Kurs von Yahoo"]
    return dict(tab=tab, s_soll=s_soll, s_ist=s_ist, diff=s_ist - s_soll, rundung=rundung, ohne_kurs=ohne_kurs,
                abw=float(tab["Diff. Pp."].abs().sum()) if len(tab) else 0.0, **ursache,
                quote_app=float(ss.get("an_p_ak") or 0), **{k: a.get(k) for k in ("quote", "datei", "zeit", "uebersprungen")})


def _kurse_nachladen():
    """Update 11: Kurse für Titel ohne Kurs erneut bei Yahoo holen (der leere Abruf ist sonst 15 Min. zwischengespeichert)."""
    import markt

    ss = st.session_state
    df = ss["an_aktien"].copy().reset_index(drop=True)
    idx = [i for i in df.index if _txt(df.at[i, "Ticker"]).strip() and not _num(df.at[i, "Kurs USD"])]
    if not idx:
        return
    tick = sorted({_txt(df.at[i, "Ticker"]).strip().upper().replace(".", "-") for i in idx})
    try:
        markt.lade_yahoo.clear(tuple(tick))
    except Exception:  # noqa: BLE001  (ältere Streamlit-Versionen: nur ganzer Speicher)
        markt.lade_yahoo.clear()
    kurse = _letzte_kurse(tick)
    for i in idx:
        _ak_rechnen(df, i, {"Ticker"}, kurse)
    ss["an_aktien"] = _aktien_df(_prozent_verteilen(df))
    for k in ("an_ak_ver", "an_form_ver"):
        ss[k] = ss.get(k, 0) + 1
    noch = [t for t in tick if t not in kurse]
    _meldung(f"Kurse nachgeladen: {len(tick) - len(noch)} von {len(tick)} gefunden."
             + (f" Weiterhin ohne Kurs: {', '.join(noch)} – Ticker prüfen (z. B. BRK-B) oder in einer Minute nochmals." if noch else ""))


def _abgleich_anzeigen(ab: dict):
    ok = abs(ab["diff"]) < 0.05 and not ab["ohne_kurs"]
    st.markdown(f"**Abgleich mit dem Scoring-Modell** · Import {ab['zeit']}"
                + (f" · {ab['datei']}" if ab.get("datei") else ""))
    ui.kopf_kpis([("Excel: Summe Aktienteil", f"{ui.de(ab['s_soll'], 2)} %", f"{len(ab['tab'])} Titel"),
                  ("App: Summe Ist", f"{ui.de(ab['s_ist'], 2)} %", "Beträge / (Kapital × Aktienquote Modell)"),
                  ("Differenz", f"{ui.de(ab['diff'], 2, True)} Pp.", "ok" if ok else "siehe Ursachen"),
                  ("Σ |Ist − Excel|", f"{ui.de(ab['abw'], 2)} Pp.", "über alle Titel")])
    teile = []
    if ab["kein_kurs"] > 0.0001:
        teile.append(f"**{ui.de(ab['kein_kurs'], 2)} Pp.** fehlen, weil Yahoo für {len(ab['ohne_kurs'])} Titel keinen Kurs geliefert "
                     f"hat ({', '.join(ab['ohne_kurs'][:12])}{' …' if len(ab['ohne_kurs']) > 12 else ''}) – ohne Kurs kann die App "
                     "keine Stücke rechnen.")
    if ab["fehlt"] > 0.0001:
        teile.append(f"**{ui.de(ab['fehlt'], 2)} Pp.** gehören zu Titeln, die nach dem Import aus der Tabelle entfernt wurden.")
    if ab["extra"] > 0.0001:
        teile.append(f"**{ui.de(ab['extra'], 2)} Pp.** kommen von zusätzlichen Titeln, die nicht im Modell sind.")
    if abs(ab["rundung"]) >= 0.005:
        teile.append(f"**{ui.de(ab['rundung'], 2, True)} Pp.** Rundung auf ganze Stücke (je Titel höchstens 1 Stück).")
    if abs(ab["quote_app"] - float(ab["quote"] or 0)) > 0.01:
        teile.append(f"«Aktien %» steht auf {ui.de(ab['quote_app'], 2)} %, das Modell rechnet mit {ui.de(float(ab['quote']), 2)} % – "
                     "dadurch stimmen die Beträge nicht mit Excel überein. «Aktien %» oben auf den Wert des Modells setzen.")
    for t in teile:
        st.markdown(f"- {t}")
    if ab["ohne_kurs"]:
        st.button(f"Fehlende Kurse neu laden ({len(ab['ohne_kurs'])})", icon=":material/refresh:", key="an_sc_kurse",
                  on_click=_kurse_nachladen, help="Holt die Kurse der Titel ohne Kurs nochmals bei Yahoo und rechnet die Stücke")
    if ab.get("uebersprungen"):
        with st.popover(f"Beim Lesen der Excel-Datei aufgefallen ({len(ab['uebersprungen'])})", icon=":material/rule:"):
            for u in ab["uebersprungen"]:
                st.markdown(f"- {u}")
    t = ab["tab"]
    t = t.reindex(t["Diff. Pp."].abs().sort_values(ascending=False).index)
    nur = st.toggle("Nur Titel mit Abweichung ≥ 0,01 Pp.", value=True, key="an_sc_nur")
    if nur:
        t = t[t["Diff. Pp."].abs() >= 0.01]
    st.dataframe(t, hide_index=True, width="stretch", height=min(320, 38 + 35 * max(len(t), 1)),
                 column_config={"Excel %": st.column_config.NumberColumn(format="%.4f"),
                                "App Ist %": st.column_config.NumberColumn(format="%.4f"),
                                "Diff. Pp.": st.column_config.NumberColumn(format="%+.4f"),
                                "Stück": st.column_config.NumberColumn(format="%d"),
                                "Kurs USD": st.column_config.NumberColumn(format="%.2f")})


def _scoring_bereich():
    ss = st.session_state
    ab = scoring_abgleich(ss)
    with st.expander("Titel aus dem Scoring-Modell übernehmen (Excel)", icon=":material/upload_file:",
                     expanded=bool(ab and (abs(ab["diff"]) >= 0.05 or ab["ohne_kurs"]))):
        if ab:
            _abgleich_anzeigen(ab)
            st.divider()
        st.caption("Liest im Blatt «Portfolio» Ticker und «Gewicht Aktienteil» mit voller Genauigkeit und die Aktienquote "
                   "(«Soll: Aktienquote»). So rechnet die App genau wie das Modell: Gewicht Gesamtportfolio = Aktienteil × Aktienquote.")
        f = st.file_uploader("Scoring-Modell (.xlsx)", type=["xlsx"], key=f"an_sc_up{ss.get('an_ver', 0)}",
                             label_visibility="collapsed")
        if f is None:
            return
        sig = [f.name, f.size]
        if (ss.get("an_sc") or {}).get("sig") != sig:
            ss["an_sc"] = dict(sig=sig, datei=f.name, **scoring_lesen(f))
        d = ss["an_sc"]
        if d.get("fehler"):
            st.error(d["fehler"], icon=":material/error:")
            return
        if d.get("uebersprungen"):
            st.warning("Beim Lesen aufgefallen:\n" + "\n".join(f"- {u}" for u in d["uebersprungen"]), icon=":material/rule:")
        ui.kopf_kpis([("Titel", f"{len(d['titel'])}", "aus dem Blatt «Portfolio»"),
                      ("Summe Aktienteil", f"{ui.de(d['summe_akt'], 2)} %", "soll 100 %"),
                      ("Summe Gesamtportfolio", f"{ui.de(d['summe_ges'], 2)} %", "= Aktienquote"),
                      ("Aktienquote", f"{ui.de(d['quote'], 2)} %" if d.get("quote") is not None else "–", "wird «Aktien %»")])
        st.dataframe(pd.DataFrame([{"Sektor": x["sektor"], "Ticker": x["ticker"], "Name": x["name"],
                                    "Gewicht Aktienteil %": round(x["akt"], 4) if x["akt"] is not None else None,
                                    "Gewicht Gesamt %": round(x["ges"], 4) if x["ges"] is not None else None} for x in d["titel"]]),
                     hide_index=True, width="stretch", height=240)
        st.button("In dieses Analyseportfolio übernehmen – ersetzt die Aktien", type="primary", icon=":material/download:",
                  on_click=_scoring_uebernehmen, key="an_sc_los",
                  help="Treasuries-Zeilen bleiben. Danach prüfen und mit «Speichern» fürs Team ablegen.")


# ---------------------------------------------------------------------------
# Import der Treasuries aus einer Excel-Liste (Update 11, Wunsch Sophy 25.11.2026)
# Reiter wählbar (Standard «Portfolio»), Spalten werden an der Überschrift erkannt (deutsch/englisch).
# ---------------------------------------------------------------------------
TR_SPALTEN = [  # (Schlüssel, Anfänge der Überschrift) – Reihenfolge zählt («Nominalzins» ist ein Kupon, kein Nominal)
    ("isin", ("isin",)),
    ("cusip", ("cusip",)),
    ("kupon", ("kupon", "coupon", "cpn", "zinssatz", "nominalzins", "zins%", "zins(")),
    ("faellig", ("fälligkeit", "faelligkeit", "fällig", "faellig", "maturity", "laufzeitende", "verfall", "rückzahlung",
                 "rueckzahlung", "endfälligkeit", "enddatum")),
    ("rendite", ("rendite", "ytm", "yield", "verfallrendite")),
    ("auswahl", ("auswahl", "ausgewählt", "ausgewaehlt", "select", "kaufen", "importfolio", "inportfolio")),
    ("gewicht", ("gewicht", "weight", "anteil", "allokation", "quote")),
    ("nominal", ("nominal", "nennwert", "facevalue", "face", "par")),
    ("betrag", ("betrag", "marktwert", "investiert", "investition", "amount", "marketvalue", "wert")),
    ("kurs", ("kurs", "preis", "price", "cleanprice")),
    ("bez", ("bezeichnung", "name", "titel", "anleihe", "security", "beschreibung", "description", "bond", "wertpapier")),
]
TR_MARKER = {"x", "ja", "yes", "1", "true", "wahr", "✓", "✔", "✅", "j", "y", "ok"}


def isin_pruefziffer(basis11: str) -> int:
    """Prüfziffer einer ISIN (Luhn über die Ziffernfolge, Buchstaben A=10 … Z=35)."""
    ziffern = "".join(str(int(c, 36)) for c in basis11.upper())
    summe = 0
    for i, ch in enumerate(reversed(ziffern)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            d = d - 9 if d > 9 else d
        summe += d
    return (10 - summe % 10) % 10


def isin_ok(isin: str) -> bool:
    s = (isin or "").strip().upper()
    return bool(re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", s)) and isin_pruefziffer(s[:11]) == int(s[11])


def isin_aus_cusip(cusip: str) -> str:
    s = (cusip or "").strip().upper()
    return f"US{s}{isin_pruefziffer('US' + s)}" if re.fullmatch(r"[A-Z0-9]{9}", s) else ""


def _tr_zahl(zelle) -> tuple[float | None, bool]:
    """(Zahl, war Prozent?) aus einer Excel-Zelle – auch Texte wie «4,25 %» oder «1'000'000». Prozent-Zellen kommen
    gleich in Prozent zurück (Excel speichert 4,25 % als 0,0425 → 4,25)."""
    v = zelle.value if hasattr(zelle, "value") else zelle
    fmt = str(getattr(zelle, "number_format", "") or "")
    if isinstance(v, bool):
        return None, False
    if isinstance(v, (int, float)):
        return (float(v) * 100, True) if "%" in fmt else (float(v), False)
    s = str(v or "").strip()
    if not s:
        return None, False
    pct = s.endswith("%")
    s = s.rstrip("%").replace("’", "'").strip()
    if "," in s and "." in s:                 # 1,000,000.50 bzw. 1.000.000,50
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif s.count(",") > 1:                    # 2,000,000
        s = s.replace(",", "")
    elif s.count(".") > 1:                    # 2.000.000
        s = s.replace(".", "")
    return zahl(s), pct


def _tr_datum(v) -> pd.Timestamp | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return pd.Timestamp(v).normalize()
    if isinstance(v, (int, float)) and 20000 < v < 80000:          # Excel-Seriennummer
        return (pd.Timestamp("1899-12-30") + pd.Timedelta(days=int(v))).normalize()
    s = str(v).strip()
    t = pd.to_datetime(s, dayfirst="." in s, errors="coerce")
    if pd.isna(t):
        t = pd.to_datetime(s, format="%m/%d/%Y", errors="coerce")
    return None if pd.isna(t) else pd.Timestamp(t).normalize()


def treasury_blaetter(datei) -> list[str]:
    import openpyxl

    wb = openpyxl.load_workbook(datei, read_only=True)
    namen = list(wb.sheetnames)
    wb.close()
    return namen


def treasury_lesen(datei, blatt: str | None = None) -> dict:
    """Treasury-Liste lesen. Erkennt die Kopfzeile an «Kupon» + «Fälligkeit» oder «ISIN»/«CUSIP» (erste 40 Zeilen).
    Gibt dict(blatt, zeilen=[{isin, bez, kupon, faellig, kurs, nominal, betrag, gewicht, rendite}], spalten, hinweise,
    gewicht_bezug=«teil»/«gesamt»/None) zurück. Prozentwerte (Excel-Format %) werden in % umgerechnet."""
    import openpyxl

    try:
        wb = openpyxl.load_workbook(datei, data_only=True, read_only=True)
    except Exception as e:  # noqa: BLE001
        return dict(fehler=f"Datei nicht lesbar: {e}")
    namen = list(wb.sheetnames)
    if blatt not in namen:
        blatt = "Portfolio" if "Portfolio" in namen else namen[0]
    rows = [list(r) for r in wb[blatt].iter_rows(min_row=1, max_row=800)]
    wb.close()
    kopf, sp, titel = None, {}, {}
    for r_i, r in enumerate(rows[:40]):
        n = [_kopf(c.value) for c in r]
        m = {}
        for j, x in enumerate(n):
            if not x:
                continue
            for key, anf in TR_SPALTEN:
                if key not in m and x.startswith(anf):
                    m[key] = j
                    break
        if ("kupon" in m and "faellig" in m) or "isin" in m or "cusip" in m:
            kopf, sp = r_i, m
            titel = {k: str(r[j].value).strip() for k, j in m.items()}
            break
    if kopf is None:
        return dict(fehler=f"Im Reiter «{blatt}» keine Kopfzeile mit «Kupon» und «Fälligkeit» (oder «ISIN»/«CUSIP») gefunden.",
                    blaetter=namen, blatt=blatt)

    def zelle(r, key):
        j = sp.get(key)
        return r[j] if j is not None and j < len(r) else None

    roh, hinweise, leer = [], [], 0
    for z_nr, r in enumerate(rows[kopf + 1:], start=kopf + 2):
        werte = [c.value for c in r if c is not None]
        if all(v is None or str(v).strip() == "" for v in werte):
            leer += 1
            if leer >= 25:
                break
            continue
        leer = 0
        erste = next((str(v) for v in werte if v is not None and str(v).strip()), "")
        if _kopf(erste).startswith(("summe", "total", "gesamt")):
            break
        isin = str(getattr(zelle(r, "isin"), "value", "") or "").strip().upper()
        cusip = str(getattr(zelle(r, "cusip"), "value", "") or "").strip().upper()
        kupon, kupon_pct = _tr_zahl(zelle(r, "kupon")) if zelle(r, "kupon") is not None else (None, False)
        faellig = _tr_datum(getattr(zelle(r, "faellig"), "value", None))
        if not (isin or cusip or kupon is not None or faellig is not None):
            continue                                            # Zwischentitel o. ä.
        x = dict(zeile=z_nr, isin=isin, cusip=cusip, bez=str(getattr(zelle(r, "bez"), "value", "") or "").strip(),
                 kupon=kupon, kupon_pct=kupon_pct, faellig=faellig,
                 auswahl=str(getattr(zelle(r, "auswahl"), "value", "") or "").strip().lower())
        for key in ("kurs", "nominal", "betrag", "gewicht", "rendite"):
            v, p = _tr_zahl(zelle(r, key)) if zelle(r, key) is not None else (None, False)
            x[key], x[key + "_pct"] = v, p
        roh.append(x)
    if not roh:
        return dict(fehler=f"Im Reiter «{blatt}» unter der Kopfzeile keine Anleihen gefunden.", blaetter=namen, blatt=blatt)
    # Auswahl-Spalte: sind Zeilen markiert, gelten nur diese
    if "auswahl" in sp and any(x["auswahl"] in TR_MARKER for x in roh):
        n0 = len(roh)
        roh = [x for x in roh if x["auswahl"] in TR_MARKER]
        hinweise.append(f"Spalte «{titel['auswahl']}»: {len(roh)} von {n0} Anleihen markiert – nur diese werden übernommen.")
    # Prozent-Zellen sind schon in %. Übrige Zahlen als Bruch erkennen: alle Kupons/Renditen ≤ 0,2, Gewichte Σ ≤ 1,5,
    # Kurse ≤ 2 (0,9875 des Nominals) → × 100
    for key, als_bruch in (("kupon", lambda w: 0 < max(w) <= 0.2), ("rendite", lambda w: 0 < max(w) <= 0.2),
                           ("gewicht", lambda w: 0 < sum(w) <= 1.5), ("kurs", lambda w: 0 < max(w) <= 2)):
        w = [x[key] for x in roh if x[key] is not None and not x[key + "_pct"]]
        if w and als_bruch(w):
            for x in roh:
                if x[key] is not None and not x[key + "_pct"]:
                    x[key] *= 100
    zeilen = []
    for x in roh:
        isin = x["isin"]
        if not isin and x["cusip"]:
            isin = isin_aus_cusip(x["cusip"])
        if isin and not isin_ok(isin):
            if re.fullmatch(r"[A-Z0-9]{9}", isin):                 # CUSIP in der ISIN-Spalte
                isin = isin_aus_cusip(isin)
            else:
                hinweise.append(f"Zeile {x['zeile']}: ISIN «{isin}» hat eine falsche Prüfziffer oder Länge – bitte prüfen.")
        if x["faellig"] is None:
            hinweise.append(f"Zeile {x['zeile']}{' (' + isin + ')' if isin else ''}: keine Fälligkeit – nicht übernommen.")
            continue
        if x["kupon"] is None:
            hinweise.append(f"Zeile {x['zeile']}{' (' + isin + ')' if isin else ''}: kein Kupon – für die Übernahme ins Portfolio "
                            "in der Analyse ergänzen (0 bei T-Bills).")
        zeilen.append(dict(isin=isin, bez=x["bez"], kupon=x["kupon"], faellig=x["faellig"], kurs=x["kurs"], nominal=x["nominal"],
                           betrag=x["betrag"], gewicht=x["gewicht"], rendite=x["rendite"], zeile=x["zeile"]))
    if not zeilen:
        return dict(fehler="Keine Anleihe mit Fälligkeit gefunden.", blaetter=namen, blatt=blatt, hinweise=hinweise)
    gb = None                                                       # worauf bezieht sich die Spalte Gewicht?
    if "gewicht" in sp:
        k_ = _kopf(titel["gewicht"])
        gb = "gesamt" if any(w in k_ for w in ("gesamt", "total", "portfolio")) else \
            "teil" if any(w in k_ for w in ("treasur", "anleihe", "bond", "teil", "obligation")) else None
    return dict(blatt=blatt, blaetter=namen, zeilen=zeilen, spalten={k: titel[k] for k in sp}, hinweise=hinweise,
                gewicht_bezug=gb)


def treasury_betraege(zeilen: list[dict], quelle: str, budget_t: float, kapital: float, gewicht_bezug: str) -> tuple[list, list]:
    """Betrag USD je Anleihe. quelle: betrag | nominal | gewicht | gleich. Übersteigt die Summe das Treasury-Budget,
    werden alle im gleichen Verhältnis gekürzt (wie der Deckel in der Eingabe). Gibt (beträge, meldungen) zurück."""
    meld, b = [], []
    for x in zeilen:
        if quelle == "betrag":
            b.append(x["betrag"] or 0.0)
        elif quelle == "nominal":
            b.append((x["nominal"] or 0.0) * ((x["kurs"] or 100.0) / 100))
        elif quelle == "gewicht":
            b.append((x["gewicht"] or 0.0) / 100 * (kapital if gewicht_bezug == "gesamt" else budget_t))
        else:
            b.append(budget_t / len(zeilen) if zeilen else 0.0)
    if quelle == "nominal" and any(x["nominal"] and not x["kurs"] for x in zeilen):
        meld.append("Ohne Kurs in der Liste ist der Betrag ≈ Nominal (Kurs 100 %) – die Übernahme rechnet am Handelstag mit dem "
                    "Kurs aus der Zinskurve.")
    s = sum(b)
    if budget_t > 0 and s > budget_t + 0.5:
        f = budget_t / s
        b = [v * f for v in b]
        meld.append(f"Die Anleihen ergeben zusammen {ui.de(s, 0)} USD, das Treasury-Budget ist {ui.de(budget_t, 0)} USD – alle im "
                    f"gleichen Verhältnis auf {ui.de(f * 100, 1)} % gekürzt. Stimmt «Treasuries %»?")
    return [math.floor(v) for v in b], meld


def _treasury_uebernehmen():
    ss = st.session_state
    p = ss.get("an_tri_vorschau") or {}
    if not p.get("zeilen"):
        return
    neu = pd.DataFrame([{"Laufzeit": baender(max(0.0, (x["faellig"] - pd.Timestamp.today()).days / 365.25), x["bez"]),
                         "Betrag USD": betrag, "Kupon %": x["kupon"], "Fälligkeit": x["faellig"],
                         "Kurs %": x["kurs"] if p.get("kurs_uebernehmen") else None, "Bezeichnung": x["bez"], "ISIN": x["isin"]}
                        for x, betrag in zip(p["zeilen"], p["betraege"]) if betrag > 0])
    if not p.get("ersetzen") and "an_treas" in ss:
        alt = _treas_df(ss["an_treas"])
        alt = alt[[_num(x) is not None for x in alt["Betrag USD"]]]
        neu = pd.concat([alt, neu], ignore_index=True)
    ss["an_treas"] = _treas_df(neu)
    for k in ("an_tr_ver", "an_form_ver"):
        ss[k] = ss.get(k, 0) + 1
    s = sum(p["betraege"])
    _meldung(f"{sum(1 for b_ in p['betraege'] if b_ > 0)} Treasuries aus «{p['datei']}» (Reiter «{p['blatt']}») übernommen · "
             f"{ui.de(s, 0)} USD von {ui.de(p['budget_t'], 0)} USD Treasury-Budget"
             + (f" · {ui.de(p['budget_t'] - s, 0)} USD noch offen." if p["budget_t"] - s > 100 else "."))


def _treasury_bereich():
    ss = st.session_state
    with st.expander("Treasuries aus Excel übernehmen", icon=":material/account_balance:"):
        st.caption("Liest eure Anleihen-Liste: Reiter wählbar (Standard «Portfolio»). Erkannt werden die Spalten an der Überschrift – "
                   "ISIN (oder CUSIP), Bezeichnung, Kupon, Fälligkeit und, falls vorhanden, Betrag, Nominal, Kurs, Gewicht und eine "
                   "Spalte «Auswahl» (x/ja = übernehmen). Das Laufzeitband rechnet die App aus der Fälligkeit.")
        f = st.file_uploader("Treasury-Liste (.xlsx)", type=["xlsx"], key=f"an_tri_up{ss.get('an_ver', 0)}",
                             label_visibility="collapsed")
        if f is None:
            return
        try:
            blaetter = treasury_blaetter(f)
        except Exception as e:  # noqa: BLE001
            st.error(f"Datei nicht lesbar: {e}", icon=":material/error:")
            return
        std = "Portfolio" if "Portfolio" in blaetter else blaetter[0]
        c1, c2 = st.columns([1.3, 2])
        blatt = c1.selectbox("Reiter", blaetter, index=blaetter.index(std), key=f"an_tri_bl{f.name}{f.size}")
        sig = [f.name, f.size, blatt]
        if (ss.get("an_tri") or {}).get("sig") != sig:
            f.seek(0)
            ss["an_tri"] = dict(sig=sig, **treasury_lesen(f, blatt))
        d = ss["an_tri"]
        if d.get("fehler"):
            st.error(d["fehler"], icon=":material/error:")
            return
        c2.caption("Erkannte Spalten: " + " · ".join(f"{k} = «{v}»" for k, v in d["spalten"].items()))
        for h in d["hinweise"]:
            st.warning(h, icon=":material/info:")
        z = d["zeilen"]
        quellen = [q for q, ok in (("betrag", any(x["betrag"] for x in z)), ("nominal", any(x["nominal"] for x in z)),
                                   ("gewicht", any(x["gewicht"] for x in z)), ("gleich", True)) if ok]
        namen_q = {"betrag": "Betrag aus der Liste", "nominal": "Nominal × Kurs", "gewicht": "Gewicht aus der Liste",
                   "gleich": "Treasury-Budget gleich verteilen"}
        b = budget(ss)
        o1, o2 = st.columns([1.6, 1.4])
        quelle = o1.radio("Beträge", quellen, format_func=namen_q.get, horizontal=True, key=f"an_tri_q{sig}")
        gb = d.get("gewicht_bezug")
        if quelle == "gewicht":
            s_w = sum(x["gewicht"] or 0 for x in z)
            vorschlag = gb or ("teil" if abs(s_w - 100) < 1.5 else "gesamt" if abs(s_w - b["pct"]["treas"]) < 1.5 else "teil")
            gb = o2.radio("Gewicht bezogen auf", ["teil", "gesamt"], index=0 if vorschlag == "teil" else 1, horizontal=True,
                          key=f"an_tri_gb{sig}", format_func=lambda x: "Treasury-Teil" if x == "teil" else "Gesamtportfolio",
                          help=f"Summe der Gewichte in der Liste: {ui.de(s_w, 2)} %")
        o3, o4 = st.columns(2)
        ersetzen = o3.checkbox("Bisherige Treasury-Zeilen ersetzen", value=True, key=f"an_tri_ers{sig}")
        kurs_ueb = o4.checkbox("Kurs aus der Liste auch für die Übernahme ins Portfolio verwenden", value=False,
                               key=f"an_tri_kurs{sig}", disabled=not any(x["kurs"] for x in z),
                               help="Aus: Die Übernahme am Handelstag rechnet den Preis aus der Zinskurve (aktueller). Ein: Kurs aus der "
                                    "Liste wird fest eingetragen.")
        betraege, meld = treasury_betraege(z, quelle, b["budget_t"], b["kapital"], gb or "teil")
        for m_ in meld:
            st.info(m_, icon=":material/content_cut:")
        s = sum(betraege)
        heute = pd.Timestamp.today()
        vor = pd.DataFrame([{"ISIN": x["isin"], "Bezeichnung": x["bez"] or "(automatisch)", "Kupon %": x["kupon"],
                             "Fälligkeit": x["faellig"].date(), "Restlaufzeit J.": round((x["faellig"] - heute).days / 365.25, 2),
                             "Band": baender(max(0.0, (x["faellig"] - heute).days / 365.25), x["bez"]),
                             "Kurs %": x["kurs"], "Rendite %": x["rendite"], "Betrag USD": ui.de(bt, 0),
                             "Anteil Treasuries %": round(bt / b["budget_t"] * 100, 2) if b["budget_t"] else None}
                            for x, bt in zip(z, betraege)])
        ui.kopf_kpis([("Anleihen", f"{len(z)}", f"Reiter «{d['blatt']}»"),
                      ("Summe", f"{ui.de(s, 0)} USD", f"Treasury-Budget {ui.de(b['budget_t'], 0)} USD"),
                      ("Anteil am Budget", f"{ui.de(s / b['budget_t'] * 100 if b['budget_t'] else 0, 2)} %",
                       f"Treasuries {ui.de(b['pct']['treas'], 2)} % des Kapitals"),
                      ("Ø Restlaufzeit", f"{ui.de(sum(bt * (x['faellig'] - heute).days / 365.25 for x, bt in zip(z, betraege)) / s, 1) if s else '–'} J.",
                       "betragsgewichtet")])
        st.dataframe(vor.drop(columns=[c for c in ("Kurs %", "Rendite %") if vor[c].isna().all()]), hide_index=True,
                     width="stretch", height=min(320, 38 + 35 * len(vor)),
                     column_config={"Kupon %": st.column_config.NumberColumn(format="%.3f"),
                                    "Fälligkeit": st.column_config.DateColumn(format="DD.MM.YYYY")})
        ss["an_tri_vorschau"] = dict(zeilen=z, betraege=betraege, ersetzen=ersetzen, kurs_uebernehmen=kurs_ueb, datei=f.name,
                                     blatt=d["blatt"], budget_t=b["budget_t"])
        st.button("In dieses Analyseportfolio übernehmen" + (" – ersetzt die Treasuries" if ersetzen else " – anfügen"),
                  type="primary", icon=":material/download:", on_click=_treasury_uebernehmen, key="an_tri_los",
                  disabled=s <= 0, help="Danach prüfen und mit «Speichern» fürs Team ablegen. Ändert sich später Kapital oder "
                                        "Treasuries %, die Liste neu übernehmen.")


def _sektor_bereich(ctx, eigene_ansicht: bool = False):
    """Sektorgewichtung des aktiven Analyseportfolios: automatisch gerechnet, im Formular anpassbar.
    eigene_ansicht (Update 18): als Ansicht «Sektorgewichte anpassen» oben in der Analyse – mit Grafik und Titelliste."""
    import sektoren as SK

    ss = st.session_state
    df = ss["an_aktien"]
    ist, summe = sektor_ist(df)
    index = ctx.sek_index["gewichte"]
    try:
        ziele_sa = SK.ziele(SK.lade_allokation(ctx.tabellen.get("sektor_allokation"))["sektoren"], index)
    except Exception:  # noqa: BLE001
        ziele_sa = {}
    ziele = {s: (ziele_sa.get(s) if ziele_sa.get(s) is not None else index.get(s)) for s in C.SEKTOREN}
    if eigene_ansicht:
        rahmen = st.container(border=True)
    else:
        rahmen = st.expander("Sektorgewichte anpassen – Aktien werden automatisch umgerechnet (auch als eigene Ansicht oben)",
                             icon=":material/donut_small:", expanded=bool(ss.get("an_sek_vorschlag")))
    with rahmen:
        if eigene_ansicht:
            st.markdown("**Sektorgewichte anpassen** · für das oben gewählte Analyseportfolio. «Neu %» eintippen und "
                        "«Sektorgewichte anwenden» – die Aktien des Sektors werden im gleichen Verhältnis grösser bzw. kleiner "
                        "(ganze Stücke). Danach oben «Speichern».")
        if not ist:
            st.caption("Sobald Aktien mit Betrag eingetragen (und übernommen) sind, erscheint hier die Sektorgewichtung"
                       + (" – Titel in der Ansicht «Portfolio bearbeiten & analysieren» eintragen." if eigene_ansicht else "."))
            return
        vs = ss.get("an_sek_vorschlag")
        if vs and vs.get("ver") != ss.get("an_ak_ver", 0):
            vs = None
            ss.pop("an_sek_vorschlag", None)
        titel: dict[str, list] = {}
        for t, s, b_ in zip(df["Ticker"], df["Sektor"], df["Betrag USD"]):
            if _txt(t).strip() and (_num(b_) or 0) > 0:
                titel.setdefault(_sektor_von(s), []).append((_txt(t).strip(), _num(b_)))
        zeilen, start = [], []
        for s, p in ist.items():
            tt = sorted(titel.get(s, []), key=lambda x: -x[1])
            ws = sum(w for _, w in tt)
            neu = (vs["werte"].get(s, 0.0) if vs else round(p, 2))
            start.append((s, neu))
            zeilen.append({"Sektor": s, "Titel (Anteil im Sektor)": " · ".join(f"{t} {ui.de(w / ws * 100, 0)} %" for t, w in tt),
                           "Ist %": round(p, 2), "Index %": round(index.get(s), 2) if index.get(s) is not None else None,
                           "Ziel %": round(ziele_sa[s], 2) if ziele_sa.get(s) is not None else None,
                           "Ist − Ziel (Pp.)": round(p - ziele_sa[s], 2) if ziele_sa.get(s) is not None else None, "Neu %": neu})
        ss["an_sek_zeilen"] = start
        fehlen = [s for s in C.SEKTOREN if s not in ist and (ziele.get(s) or 0) > 0]
        b = budget(ss)
        st.caption(f"Anteile in % der investierten Aktien ({ui.de(summe, 0)} USD) – wie die Ziele der Sektorallokation "
                   "(Index + geplante Abweichung). **Neu %** ändern und «Sektorgewichte anwenden»: Die Titel des Sektors werden "
                   "im gleichen Verhältnis grösser bzw. kleiner, ganze Stücke abgerundet.")
        if vs:
            st.info("Ziele der Sektorallokation in «Neu %» eingesetzt – prüfen und mit «Sektorgewichte anwenden» übernehmen."
                    + (" Sektoren ohne Titel fehlen, darum wurden die Ziele der übrigen auf 100 % hochgerechnet." if fehlen else ""),
                   icon=":material/info:")
        skey = f"{ss.get('an_ver', 0)}.{ss.get('an_ak_ver', 0)}.{ss.get('an_sek_ver', 0)}"
        with st.form("an_sek_form", border=False, enter_to_submit=False):
            st.data_editor(pd.DataFrame(zeilen), hide_index=True, width="stretch", key=f"an_sek_ed{skey}", num_rows="fixed",
                           disabled=["Sektor", "Titel (Anteil im Sektor)", "Ist %", "Index %", "Ziel %", "Ist − Ziel (Pp.)"],
                           column_config={
                               "Titel (Anteil im Sektor)": st.column_config.TextColumn(width="large"),
                               "Ist %": st.column_config.NumberColumn(format="%.2f %%"),
                               "Index %": st.column_config.NumberColumn(format="%.2f %%", help="S&P-500-Gewicht (Sektorallokation)"),
                               "Ziel %": st.column_config.NumberColumn(format="%.2f %%",
                                                                       help="Index + geplante Abweichung (Seite Sektorallokation); "
                                                                            "leer = nur Richtung festgelegt"),
                               "Ist − Ziel (Pp.)": st.column_config.NumberColumn(format="%.2f"),
                               "Neu %": st.column_config.NumberColumn(min_value=0.0, max_value=100.0, step=0.1, format="%.2f",
                                                                      help="Gewünschter Anteil am Aktienteil")})
            o1, o2 = st.columns([1.3, 1])
            o1.checkbox("Übrige Sektoren gleichen aus (Summe bleibt 100 %)", value=True, key=f"an_sek_ausgl{skey}",
                        help="Ein: Nur die geänderten Sektoren gelten wie eingegeben, die anderen werden im gleichen Verhältnis "
                             "kleiner bzw. grösser. Aus: Jeder Sektor gilt genau wie eingegeben – die Aktiensumme ändert sich.")
            o2.radio("Aktien gesamt danach", ["gleich", "budget"], horizontal=True, key=f"an_sek_summe{skey}",
                     format_func=lambda x: f"wie jetzt ({ui.de(summe, 0)} USD)" if x == "gleich"
                     else f"ganzes Aktienbudget ({ui.de(b['budget_a'], 0)} USD)",
                     help="«Ganzes Aktienbudget» verteilt Kapital × Aktien-% nach den Sektoranteilen – so lässt sich das "
                          "Budget voll investieren, ohne die Struktur zu ändern.")
            k1, k2, k3, _ = st.columns([1.2, 1.5, 1, 0.8])
            k1.form_submit_button("Sektorgewichte anwenden", type="primary", icon=":material/tune:", width="stretch",
                                  on_click=_sek_form, args=(skey, "anwenden", ziele))
            k2.form_submit_button("Ziele der Sektorallokation einsetzen", icon=":material/target:", width="stretch",
                                  on_click=_sek_form, args=(skey, "ziel", ziele),
                                  help="Füllt «Neu %» mit den Zielen (ohne Zielzahl: Indexgewicht) – angewendet wird erst danach")
            k3.form_submit_button("Auf Ist zurücksetzen", icon=":material/restart_alt:", width="stretch",
                                  on_click=_sek_form, args=(skey, "ist", ziele))
        st.caption("Wirkt auf die übernommene Eingabe oben – offene Änderungen in der Aktien-Tabelle zuerst übernehmen. "
                   + (f"Ohne Titel: {', '.join(f'{s} (Ziel {ui.de(ziele[s], 1)} %)' for s in fehlen)} – dafür oben Titel "
                      "hinzufügen." if fehlen else "Alle Sektoren mit Zielgewicht sind vertreten."))
    if eigene_ansicht:
        _sektor_ansicht_extra(ctx, df, ist, summe, index, ziele_sa)


def _sektor_ansicht_extra(ctx, df: pd.DataFrame, ist: dict, summe: float, index: dict, ziele_sa: dict):
    """Ansicht «Sektorgewichte anpassen»: Grafik Ist / Ziel / Benchmark und die Titel je Sektor (zeigt die Wirkung sofort)."""
    try:
        import benchmark as BM

        bm = BM.sektor_gewichte("gueltig")
    except Exception:  # noqa: BLE001
        bm = {}
    b = budget(st.session_state)
    sek = [s for s in C.SEKTOREN if s in ist or (ziele_sa.get(s) or 0) > 0 or (bm.get(s) or 0) > 0]
    fig = go.Figure([
        go.Bar(name="Ist (dieses Portfolio)", x=sek, y=[ist.get(s, 0.0) for s in sek], marker_color=ui.BLAU,
               text=[ui.de(ist.get(s, 0.0), 1) for s in sek], textposition="outside"),
        go.Bar(name="Ziel (Sektorallokation)", x=sek, y=[ziele_sa.get(s) for s in sek], marker_color="#94A3B8"),
        go.Bar(name="Benchmark (Dozent)", x=sek, y=[bm.get(s) if bm else index.get(s) for s in sek], marker_color=BENCH_FARBE)])
    fig.update_layout(barmode="group", height=330, margin=dict(l=8, r=8, t=10, b=8), legend=dict(orientation="h", y=-0.2))
    fig.update_yaxes(ticksuffix=" %")
    ui.zeige(fig)
    ui.kopf_kpis([
        ("Aktien investiert", f"{ui.de(summe, 0)} USD", f"Aktienbudget {ui.de(b['budget_a'], 0)} USD · Rest {ui.de(b['rest_a'], 0)} USD"),
        ("Sektoren vertreten", f"{len([s for s in ist if s in C.SEKTOREN])} / {len(C.SEKTOREN)}",
         "fehlt: " + (", ".join(s for s in C.SEKTOREN if s not in ist) or "keiner")),
        ("Σ |Ist − Ziel|", f"{ui.de(sum(abs(ist.get(s, 0.0) - ziele_sa[s]) for s in C.SEKTOREN if ziele_sa.get(s) is not None), 1)} Pp.",
         "über alle Sektoren mit Zielzahl"),
        ("Sektor-Active-Share", f"{ui.de(sum(abs(ist.get(s, 0.0) - (bm.get(s) or 0)) for s in C.SEKTOREN) / 2, 1)} %" if bm else "–",
         "½ Σ |Ist − Benchmark| – Untergrenze des Active Share"),
    ])
    st.markdown("**Titel je Sektor** · so sieht der Aktienteil aus (ändert sich mit «Sektorgewichte anwenden»)")
    zeilen = []
    for t, s, k, m, b_ in zip(df["Ticker"], df["Sektor"], df["Kurs USD"], df["Menge"], df["Betrag USD"]):
        v = _num(b_) or 0.0
        if _txt(t).strip() and v > 0:
            zeilen.append({"Sektor": _sektor_von(s), "Ticker": _txt(t).strip(), "Stück": _num(m), "Kurs USD": _num(k), "Betrag USD": v,
                           "% Aktienteil": v / summe * 100 if summe else None,
                           "% Gesamt": v / b["kapital"] * 100 if b["kapital"] else None})
    if zeilen:
        t_df = pd.DataFrame(zeilen)
        reihe = {s: i for i, s in enumerate(C.SEKTOREN + [UNBEKANNT])}
        t_df = t_df.assign(_o=t_df["Sektor"].map(lambda s: reihe.get(s, 99))).sort_values(["_o", "Betrag USD"], ascending=[True, False])
        st.dataframe(t_df.drop(columns="_o"), hide_index=True, width="stretch", height=420, column_config={
            "Stück": st.column_config.NumberColumn(format="%,.0f"), "Kurs USD": st.column_config.NumberColumn(format="%.2f"),
            "Betrag USD": st.column_config.NumberColumn(format="%,.0f"),
            "% Aktienteil": st.column_config.NumberColumn(format="%.2f %%"), "% Gesamt": st.column_config.NumberColumn(format="%.2f %%")})
    st.caption("Titel hinzufügen oder entfernen in «Portfolio bearbeiten & analysieren». Änderungen bleiben beim Wechsel der Ansicht "
               "erhalten; gespeichert wird mit «Speichern» oben (neue Version des Analyseportfolios). Für eine Variante zuerst "
               "«Neu → Kopie des aktiven Portfolios».")


def _ak_aenderung(key: str):
    """Callback der Aktien-Tabelle: Änderungen übernehmen und die betroffenen Zeilen sofort rechnen."""
    ss = st.session_state
    ch = ss.get(key) or {}
    df = ss["an_aktien"].copy().reset_index(drop=True)     # genau die angezeigte Tabelle (Zeilennummern!)
    betroffen = {}
    weg = {int(i) for i in (ch.get("deleted_rows") or []) if int(i) < len(df)}
    for i, werte in (ch.get("edited_rows") or {}).items():
        i = int(i)
        if i >= len(df):
            continue
        if werte.get("Löschen"):
            weg.add(i)
            continue
        for c, v in werte.items():
            if c in AK_SPALTEN:
                df.at[i, c] = "" if v is None else str(v)
        if "Ticker" in werte and not _txt(df.at[i, "Ticker"]).strip():   # Ticker geleert → ganze Zeile weg
            weg.add(i)
            continue
        betroffen[i] = set(werte)
    for i, sp in betroffen.items():
        if i not in weg:
            _ak_rechnen(df, i, sp)
    if weg:
        df = df.drop(index=list(weg))
    reihe = sorted(j for j in betroffen if j not in weg)
    for n_, i in enumerate(reihe):
        _ak_deckeln(df, i, set(reihe[n_ + 1:]))
    df = df.reset_index(drop=True)
    for werte in ch.get("added_rows") or []:
        if werte.get("Löschen"):
            continue
        df = pd.concat([df, pd.DataFrame([{c: ("" if werte.get(c) is None else str(werte.get(c))) for c in AK_SPALTEN}])],
                       ignore_index=True)
        _ak_rechnen(df, len(df) - 1, set(werte))
        _ak_deckeln(df, len(df) - 1)
    ss["an_aktien"] = _aktien_df(df)
    ss["an_ak_ver"] = ss.get("an_ak_ver", 0) + 1


def _such_hinzu():
    """Suchfeld: gewählten Titel mit Stückzahl als neue Zeile anfügen."""
    ss = st.session_state
    t = ss.get("an_such")
    if not t:
        return
    df = _aktien_df(ss["an_aktien"], 0).reset_index(drop=True)
    if t in set(df["Ticker"]):
        i = int(df.index[df["Ticker"] == t][0])
    else:
        df = pd.concat([df, pd.DataFrame([{c: "" for c in AK_SPALTEN}])], ignore_index=True)
        i = len(df) - 1
        df.at[i, "Ticker"] = t
    stueck = ss.get("an_such_stk") or 0
    pct = ss.get("an_such_pct") or 0.0
    if pct:                        # Prozent hat Vorrang vor Stück
        df.at[i, "Gewicht %"] = _fz_pct(pct)
        _ak_rechnen(df, i, {"Ticker", "Gewicht %"})
    else:
        df.at[i, "Menge"] = _fz_menge(stueck) if stueck else ""
        _ak_rechnen(df, i, {"Ticker", "Menge"} if stueck else {"Ticker"})
    _ak_deckeln(df, i)
    ss["an_aktien"] = _aktien_df(df)
    ss["an_ak_ver"] = ss.get("an_ak_ver", 0) + 1
    ss["an_such"] = None
    ss["an_such_stk"] = 0
    ss["an_such_pct"] = 0.0


TREAS_TYPEN = {"Laufzeit": "string", "Betrag USD": "string", "Kupon %": "float64", "Fälligkeit": "datetime64[ns]",
               "Kurs %": "float64", "Bezeichnung": "string", "ISIN": "string"}


def _treas_df(df: pd.DataFrame) -> pd.DataFrame:
    """Treasury-Eingabe mit allen Spalten (ältere gespeicherte Vorauswahlen haben nur Laufzeit und Betrag).
    Betrag als Text mit Apostroph (1'000'000)."""
    df = df.copy()
    for c in TREAS_TYPEN:
        if c not in df.columns:
            df[c] = None
    df["Fälligkeit"] = pd.to_datetime(df["Fälligkeit"], errors="coerce")
    df["Betrag USD"] = [_fz(x) for x in df["Betrag USD"]]
    for c in ("Kupon %", "Kurs %"):
        df[c] = [_num(x) for x in df[c]]
    return df[list(TREAS_TYPEN)].astype(TREAS_TYPEN)


def _tr_aenderung(key: str):
    """Callback der Treasury-Tabelle: Änderungen übernehmen, Betrag mit Apostroph formatieren."""
    ss = st.session_state
    ch = ss.get(key) or {}
    df = _treas_df(ss["an_treas"]).reset_index(drop=True).astype(object)
    weg = {int(i) for i in (ch.get("deleted_rows") or []) if int(i) < len(df)}
    geaendert = []
    for i, werte in (ch.get("edited_rows") or {}).items():
        i = int(i)
        if i < len(df):
            if werte.get("Löschen"):
                weg.add(i)
                continue
            for c, v in werte.items():
                if c in TREAS_TYPEN:
                    df.at[i, c] = v
            if "Betrag USD" in werte:
                geaendert.append(i)
    if weg:
        df = df.drop(index=list(weg))
    for werte in ch.get("added_rows") or []:
        if not werte.get("Löschen"):
            df = pd.concat([df, pd.DataFrame([{c: werte.get(c) for c in TREAS_TYPEN}], index=[max(df.index, default=-1) + 1])])
            geaendert.append(df.index[-1])
    kap = _num(ss.get("an_kap_txt")) or 0.0
    b_t = kap * float(ss.get("an_p_tr") or 0) / 100
    for n_, i in enumerate(geaendert):          # Deckel: Treasuries höchstens Kapital × Treasury-% (von oben nach unten)
        if i not in df.index or kap <= 0:
            continue
        spaeter = set(geaendert[n_ + 1:])
        andere = sum(_num(x) or 0 for j, x in zip(df.index, df["Betrag USD"]) if j != i and j not in spaeter)
        rest = max(0.0, b_t - andere)
        if (_num(df.at[i, "Betrag USD"]) or 0) > rest + 0.5:
            df.at[i, "Betrag USD"] = rest
            _meldung(f"Treasury {_txt(df.at[i, 'Laufzeit']) or _txt(df.at[i, 'Bezeichnung'])}: Betrag auf {ui.de(rest, 0)} USD gekürzt "
                     f"– Treasury-Budget ({ui.de(float(ss.get('an_p_tr') or 0), 1)} % = {ui.de(b_t, 0)} USD) ausgeschöpft.")
    if "ISIN" in df.columns:          # Update 11: Grossbuchstaben, CUSIP (9 Zeichen) → ISIN, falsche Prüfziffer melden
        for i in df.index:
            s_ = _txt(df.at[i, "ISIN"]).strip().upper().replace(" ", "")
            if re.fullmatch(r"[A-Z0-9]{9}", s_):
                s_ = isin_aus_cusip(s_)
            elif s_ and not isin_ok(s_):
                _meldung(f"ISIN «{s_}» hat eine falsche Prüfziffer oder Länge – bitte prüfen.")
            df.at[i, "ISIN"] = s_
    ss["an_treas"] = _treas_df(df.reset_index(drop=True))
    ss["an_tr_ver"] = ss.get("an_tr_ver", 0) + 1


def _leer_treas():
    return _treas_df(pd.DataFrame({"Laufzeit": ["1–3 J.", "3–7 J.", "7–10 J."], "Betrag USD": [None, None, None]}))


def portfolio_vorlage(tabellen: dict, fred: dict, lade_kurse) -> tuple[pd.DataFrame, pd.DataFrame, float, float] | None:
    """Aktuelles Portfolio als Vorlage (Aktien mit Stückzahl, Treasuries je Laufzeitband, Cash)."""
    import portfolio as P

    tx = P.transaktionen(tabellen.get("transaktionen"))
    if not len(tx):
        return None
    kapital, _ = P._einstellungen(tabellen.get("einstellungen"))
    ticker = tuple(sorted(set(tx.loc[tx["klasse"] == "Aktie", "ticker"].str.upper())))
    kurse = lade_kurse(ticker)[0] if ticker else {}
    pos, cash_delta, _ = P.positionen(tx)
    b = P.bewerte(pos, kurse, fred, pd.Timestamp.today().normalize())
    ak = b[b["klasse"] == "Aktie"] if len(b) else b
    a = pd.DataFrame({"Ticker": ak["ticker"].astype(str).tolist() if len(ak) else [],
                      "Menge": ak["menge"].astype(float).tolist() if len(ak) else [],
                      "Betrag USD": [None] * len(ak)})
    tr = b[b["klasse"] == "Treasury"] if len(b) else b
    if len(tr):
        tr = tr.assign(band=[baender(T, n) for T, n in zip(tr["restlaufzeit"], tr["ticker"])])
        t = tr.groupby("band")["wert"].sum().reset_index().rename(columns={"band": "Laufzeit", "wert": "Betrag USD"})
    else:
        t = _leer_treas()
    import cashzins as CZ

    zinsen = float(b["zins"].sum()) if len(b) else 0.0
    cash = float(kapital + cash_delta + zinsen + CZ.zins_bis(tx, kapital, fred))
    wert = float(b["wert"].sum()) + cash if len(b) else cash
    return (pd.concat([a, _leer_aktien(2)], ignore_index=True).astype({"Ticker": "string", "Menge": "float64", "Betrag USD": "float64"}),
            t.astype({"Laufzeit": "string", "Betrag USD": "float64"}),
            max(0.0, cash), wert)


# ---------------------------------------------------------------------------
# Mehrere Analyseportfolios
# Gespeichert im Blatt «analysen» (je id gilt die neuste Zeile; Löschen = neue Zeile mit «geloescht» in der Eingabe).
# In der Sitzung sind mehrere Portfolios gleichzeitig offen: das aktive liegt in den an_*-Schlüsseln, die übrigen
# als Kopie in st.session_state["an_pf"][id]["state"] – Wechseln verliert also keine ungespeicherten Änderungen.
# ---------------------------------------------------------------------------
PF_KEYS = ("an_aktien", "an_treas", "an_cash", "an_kapital", "an_kap_txt", "an_p_ak", "an_p_tr", "an_p_ca", "an_bezug",
           "an_fuellen", "an_eingabe", "an_kz", "an_modell_quote", "an_sc_abgleich")
# Widget-Schlüssel löscht Streamlit, sobald die Seite verlassen wird → Spiegel «_<key>» hält den Wert
WIDGET_KEYS = ("an_kap_txt", "an_p_ak", "an_p_tr", "an_p_ca", "an_bezug", "an_fuellen", "an_pf_name", "an_pf_notiz")
BESTAND = "__bestand__"        # im Vergleich: aktuelles Portfolio (Transaktionen)
PF_FARBEN = [ui.BLAU, ui.PETROL, "#8E5572", "#C05621", ui.NAVY, "#6B7A2F", "#5A6ACF", "#9C4221"]


def _ist_geloescht(eingabe) -> bool:
    try:
        return bool(json.loads(eingabe).get("geloescht"))
    except Exception:  # noqa: BLE001
        return False


def portfolios(df: pd.DataFrame | None, mit_geloeschten: bool = False) -> pd.DataFrame:
    """Gespeicherte Analyseportfolios: neuste Version je id (ohne gelöschte), mit Ersteller, Erstelldatum und Anzahl Versionen.
    Sortiert nach letzter Änderung (neuste zuerst)."""
    spalten = ["zeit", "person", "id", "name", "eingabe", "kennzahlen", "notiz", "erstellt_von", "erstellt", "versionen", "geloescht"]
    if df is None or df.empty or "id" not in df.columns:
        return pd.DataFrame(columns=spalten)
    d = df[df["id"].astype(str).str.strip() != ""].copy()
    if d.empty:
        return pd.DataFrame(columns=spalten)
    d["zeit"] = d["zeit"].astype(str)
    d = d.sort_values("zeit", kind="stable")
    erst = d.groupby("id").agg(erstellt_von=("person", "first"), erstellt=("zeit", "first"), versionen=("zeit", "size"))
    neu = d.drop_duplicates("id", keep="last").merge(erst, left_on="id", right_index=True)
    neu["geloescht"] = [_ist_geloescht(e) for e in neu["eingabe"]]
    if not mit_geloeschten:
        neu = neu[~neu["geloescht"]]
    return neu.sort_values("zeit", ascending=False).reset_index(drop=True)[spalten]


def _datum(z) -> str:
    s = str(z or "")
    return f"{s[8:10]}.{s[5:7]}.{s[:4]}" if len(s) >= 10 else s


def _kern(aktien: pd.DataFrame, treas: pd.DataFrame, kapital: float, pct: dict, bezug: str) -> dict:
    """Eingabe eines Portfolios in der Speicherform (Aktien, Treasuries, Kapital, Aufteilung, Bezug)."""
    a_ = aktien[[bool(_txt(t).strip()) for t in aktien["Ticker"]]] if len(aktien) else aktien
    t_ = _treas_df(treas)
    t_ = t_[[_num(x) is not None for x in t_["Betrag USD"]]]
    return dict(
        aktien=[{"Ticker": _txt(z["Ticker"]).strip(), "Menge": _num(z.get("Menge")), "Betrag USD": _num(z.get("Betrag USD")),
                 "Gewicht %": _num(z.get("Gewicht %")), "Fix": _txt(z.get("Fix"))} for z in a_.to_dict("records")],
        treas=[{"Laufzeit": _txt(z["Laufzeit"]), "Betrag USD": _num(z["Betrag USD"]), "Kupon %": _num(z.get("Kupon %")),
                "Kurs %": _num(z.get("Kurs %")),
                "Fälligkeit": pd.Timestamp(z["Fälligkeit"]).date().isoformat()
                if z.get("Fälligkeit") is not None and pd.notna(z.get("Fälligkeit")) else "",
                "Bezeichnung": _txt(z.get("Bezeichnung")), "ISIN": _txt(z.get("ISIN")).strip().upper()}
               for z in t_.to_dict("records")],
        cash=0.0, kapital=float(kapital or 0), pct={k: float(v) for k, v in (pct or {}).items()}, bezug=bezug or GESAMT)


def _eingabe_aus_state(s) -> dict:
    """Speicherform aus dem aktiven Portfolio (st.session_state) oder einer gespeicherten Sitzungskopie (dict)."""
    s = {k: s[k] for k in PF_KEYS + WIDGET_KEYS if k in s}
    b = budget(s)
    e = _kern(s.get("an_aktien", _aktien_df(None, 0)), s.get("an_treas", _leer_treas()), b["kapital"], b["pct"],
              s.get("an_bezug", GESAMT))
    e["fuellen"] = s.get("an_fuellen") if s.get("an_fuellen") in FUELLEN else "Index"
    if s.get("an_modell_quote") is not None:
        e["modell_quote"] = float(s["an_modell_quote"])       # Aktienquote des Scoring-Modells (nur für den Hinweis)
    return e


def _hash(e: dict, name: str = "", notiz: str = "") -> str:
    kern = {k: e.get(k) for k in ("aktien", "treas", "kapital", "pct", "bezug")}
    return json.dumps(dict(e=kern, name=(name or "").strip(), notiz=(notiz or "").strip()), sort_keys=True, ensure_ascii=False,
                      default=str)


def _standard_state(kapital: float, alloc: dict) -> dict:
    return dict(an_aktien=_aktien_df(None, 6), an_treas=_leer_treas(), an_cash=0.0, an_kapital=float(kapital),
                an_kap_txt=ui.de(float(kapital), 0), an_p_ak=float(alloc["aktien"]), an_p_tr=float(alloc["anleihen"]),
                an_p_ca=float(alloc["cash"]), an_bezug=GESAMT, an_fuellen="Index")


def _versionen_hoch():
    ss = st.session_state
    ss["an_ver"] = ss.get("an_ver", 0) + 1
    ss["an_ak_ver"] = ss.get("an_ak_ver", 0) + 1
    ss["an_tr_ver"] = ss.get("an_tr_ver", 0) + 1


def _aus_eingabe_laden(e: dict, kapital_std: float, alloc: dict):
    """Gespeicherte Eingabe ins aktive Portfolio laden (zuerst Kapital, Prozent und Bezug, dann die Zeilen rechnen)."""
    ss = st.session_state
    pct = e.get("pct") if isinstance(e.get("pct"), dict) else {}
    kap = float(e.get("kapital") or kapital_std)
    ss.update(_standard_state(kap, alloc))
    ss.update(an_p_ak=float(pct.get("aktien", alloc["aktien"])), an_p_tr=float(pct.get("treas", alloc["anleihen"])),
              an_p_ca=float(pct.get("cash", alloc["cash"])),
              an_bezug=e.get("bezug") if e.get("bezug") in (GESAMT, AKTIENTEIL) else GESAMT,
              an_fuellen=e.get("fuellen") if e.get("fuellen") in FUELLEN else "Index")
    a_ = pd.DataFrame(e.get("aktien") or [])
    for c_ in ("Ticker", "Menge", "Betrag USD"):
        if c_ not in a_.columns:
            a_[c_] = None
    t_ = _treas_df(pd.DataFrame(e.get("treas") or []))
    if e.get("modell_quote") is not None:
        ss["an_modell_quote"] = float(e["modell_quote"])
    ss.update(an_aktien=_aktien_df(_prozent_verteilen(_aktien_neu_rechnen(a_))), an_treas=t_ if len(t_) else _leer_treas())


def _wert(key: str, default=None):
    ss = st.session_state
    return ss[key] if key in ss else ss.get("_" + key, default)


def _stash():
    """Aktives Portfolio als Kopie in an_pf ablegen (vor dem Wechsel)."""
    ss = st.session_state
    pf, alt = ss.setdefault("an_pf", {}), ss.get("an_aktiv")
    if alt in pf:
        pf[alt]["state"] = {k: _wert(k) for k in PF_KEYS if _wert(k) is not None}
        pf[alt]["name"] = _wert("an_pf_name", pf[alt]["name"])
        pf[alt]["notiz"] = _wert("an_pf_notiz", pf[alt].get("notiz", ""))


def _aktiv_setzen(pid: str):
    ss = st.session_state
    ent = ss["an_pf"][pid]
    ss["an_aktiv"] = pid
    ss["an_pf_wahl"] = pid
    for k, v in (("an_pf_name", ent["name"]), ("an_pf_notiz", ent.get("notiz", ""))):
        ss[k] = v
        ss["_" + k] = v
    for k in WIDGET_KEYS:
        if k in ss:
            ss["_" + k] = ss[k]
    _versionen_hoch()


def _pf_oeffnen(pid: str, tab: dict, kapital_std: float, alloc: dict, verwerfen: bool = False):
    """Portfolio aktiv machen: aus der Sitzungskopie oder (sonst bzw. bei verwerfen=True) aus dem Speicher laden."""
    ss = st.session_state
    pf = ss.setdefault("an_pf", {})
    if not verwerfen and ss.get("an_aktiv") != pid:
        _stash()
    for k in PF_KEYS:
        ss.pop(k, None)
    ent = pf.get(pid)
    if ent and ent.get("state") and not verwerfen:
        ss.update(ent["state"])
        ent["state"] = None
    else:
        gs = portfolios(tab.get("analysen"))
        if pid not in set(gs["id"]):            # nicht (mehr) gespeichert
            ss.update(_standard_state(kapital_std, alloc))
            pf[pid] = dict(name=(ent or {}).get("name") or "Neues Portfolio", notiz="", gespeichert=False, zeit=None,
                           basis=None, state=None)
        else:
            z = gs.set_index("id").loc[pid]
            try:
                e = json.loads(z["eingabe"])
            except Exception:  # noqa: BLE001
                e = {}
            _aus_eingabe_laden(e, kapital_std, alloc)
            pf[pid] = dict(name=str(z["name"]), notiz=str(z["notiz"] or ""), gespeichert=True, zeit=str(z["zeit"]),
                           versionen=int(z["versionen"]), basis=None, state=None)
            pf[pid]["basis"] = _hash(_eingabe_aus_state(ss), pf[pid]["name"], pf[pid]["notiz"])
    _aktiv_setzen(pid)


def _pf_neu(art: str, tab: dict, kapital_std: float, alloc: dict, fred: dict | None = None, lade_kurse=None):
    """Neues Analyseportfolio in der Sitzung: leer, Kopie des aktiven oder aus dem aktuellen Bestand."""
    ss = st.session_state
    pf = ss.setdefault("an_pf", {})
    alt = ss.get("an_aktiv")
    kopie = {k: (v.copy() if hasattr(v, "copy") else v) for k in PF_KEYS if (v := _wert(k)) is not None} if art == "kopie" else None
    alt_name = _wert("an_pf_name", "")
    _stash()
    pid = uuid.uuid4().hex[:8]
    nummer = len(pf) + 1
    name = {"kopie": f"Kopie von {alt_name}".strip(), "bestand": f"Bestand {datetime.now():%d.%m.%Y}"}.get(art, f"Neues Portfolio {nummer}")
    pf[pid] = dict(name=name, notiz="", gespeichert=False, zeit=None, basis=None, state=None)
    for k in PF_KEYS:
        ss.pop(k, None)
    if art == "kopie" and kopie:
        ss.update(kopie)
        ss.pop("an_eingabe", None)
        ss.pop("an_kz", None)
    else:
        ss.update(_standard_state(kapital_std, alloc))
    if art == "bestand":
        v = portfolio_vorlage(tab, fred or {}, lade_kurse)
        if v is None:
            ss["an_meldung"] = "Im Portfolio sind noch keine Transaktionen erfasst – leeres Portfolio angelegt."
        else:
            ss.update(an_kapital=float(v[3]), an_kap_txt=ui.de(float(v[3]), 0))      # zuerst: Basis für «Gewicht %»
            ss.update(an_aktien=_aktien_neu_rechnen(v[0]), an_treas=_treas_df(v[1]))
            ss["an_meldung_info"] = ("Bestand übernommen. Hinweis: Aktien-, Treasury- und Cash-% stehen auf der Taktik – "
                                     "anpassen, falls der Bestand anders aufgeteilt ist.")
    _aktiv_setzen(pid)


def _pf_wechsel(tab: dict, kapital_std: float, alloc: dict):
    ss = st.session_state
    neu = ss.get("an_pf_wahl")
    if neu and neu != ss.get("an_aktiv"):
        _pf_oeffnen(neu, tab, kapital_std, alloc)


def _pf_verwerfen(tab: dict, kapital_std: float, alloc: dict):
    """Gespeichertes Portfolio: letzte gespeicherte Version neu laden. Ungespeichertes: schliessen."""
    ss = st.session_state
    pid, pf = ss.get("an_aktiv"), ss.get("an_pf", {})
    if pid not in pf:
        return
    if pf[pid].get("gespeichert"):
        _pf_oeffnen(pid, tab, kapital_std, alloc, verwerfen=True)
        return
    pf.pop(pid)
    ss["an_aktiv"] = None
    gs = portfolios(tab.get("analysen"))
    rest = list(pf) + [i for i in gs["id"] if i not in pf]
    if rest:
        _pf_oeffnen(rest[0], tab, kapital_std, alloc, verwerfen=False)
    else:
        _pf_neu("leer", tab, kapital_std, alloc)


def _kz_zusammenfassen(kv: dict, ka: dict, ak_w: float, tr_w: float, cash: float, total: float, fuellen: str) -> dict:
    kz = {k_: (round(v_, 4) if isinstance(v_, float) else v_) for k_, v_ in kv.items() if k_ not in ("von", "bis")}
    kz.update(aktien=round(ak_w / total * 100, 1), treasuries=round(tr_w / total * 100, 1),
              cash=round(cash / total * 100, 1), total=round(total, 0),
              von=f"{kv['von']:%Y-%m-%d}", bis=f"{kv['bis']:%Y-%m-%d}", fuellen=fuellen,
              vola_aktien=round(float(ka["vola"]), 4) if ka.get("vola") is not None else None,
              sharpe_aktien=round(float(ka["sharpe"]), 4) if ka.get("sharpe") is not None else None,
              active_share_aktien=round(float(ka["active_share"]), 2) if ka.get("active_share") is not None else None)
    return kz


def _portfolio_leiste(ctx, tab: dict, kapital_std: float, alloc: dict, gs: pd.DataFrame, fred: dict, lade_kurse):
    """Auswahl, Neu/Kopie, Name, Notiz, Speichern, Verwerfen, Löschen – oben auf der Seite."""
    ss = st.session_state
    pf = ss["an_pf"]
    pid = ss["an_aktiv"]
    ent = pf[pid]
    gs_ids = list(gs["id"])
    gz = gs.set_index("id") if len(gs) else None
    reihe = [i for i in pf if i not in gs_ids] + gs_ids          # ungespeicherte neue zuerst

    def _dirty(i) -> bool:
        e_ = pf.get(i)
        if not e_:
            return False
        if not e_.get("gespeichert"):
            return True
        if i == pid:
            return _hash(_eingabe_aus_state(ss), _wert("an_pf_name", ""), _wert("an_pf_notiz", "")) != e_.get("basis")
        if e_.get("state"):
            return _hash(_eingabe_aus_state(e_["state"]), e_["name"], e_.get("notiz", "")) != e_.get("basis")
        return False

    # Beschriftung bleibt stabil, solange man im Portfolio arbeitet (Streamlit merkt sich die Auswahl über den Text):
    # Name = Stand beim Öffnen/Speichern, ungespeicherte Änderungen stehen in der Zeile darunter.
    labels = {}
    for i in reihe:
        if i in pf and not pf[i].get("gespeichert"):
            labels[i] = f"{pf[i]['name']} · neu, nicht gespeichert"
        else:
            z = gz.loc[i]
            labels[i] = f"{pf[i]['name'] if i in pf else z['name']} · {z['erstellt_von']}" + \
                        (" · ● geändert" if i != pid and _dirty(i) else "")
    zaehler = {}
    for i in reihe:                      # gleiche Beschriftung unterscheidbar machen
        zaehler[labels[i]] = zaehler.get(labels[i], 0) + 1
        if zaehler[labels[i]] > 1:
            labels[i] += f" ({zaehler[labels[i]]})"

    args = (tab, kapital_std, alloc)
    with st.container(border=True):
        c1, c2, c3, c4, c5 = st.columns([2.6, 0.8, 0.9, 0.9, 0.8], vertical_alignment="bottom")
        ss["an_pf_wahl"] = pid
        c1.selectbox(f"Analyseportfolio ({len(reihe)})", reihe, format_func=labels.get, key="an_pf_wahl", on_change=_pf_wechsel, args=args,
                     help="Mehrere Portfolios parallel: Wechseln behält ungespeicherte Änderungen der anderen. Gespeicherte "
                          "Portfolios sehen und bearbeiten alle im Team.")
        with c2.popover("Neu", icon=":material/add:", width="stretch"):
            st.button("Leeres Portfolio", key="an_neu_leer", on_click=_pf_neu, args=("leer",) + args, width="stretch",
                      icon=":material/note_add:")
            st.button("Kopie des aktiven Portfolios", key="an_neu_kopie", on_click=_pf_neu, args=("kopie",) + args,
                      width="stretch", icon=":material/content_copy:", help="z. B. um eine Variante B zu rechnen")
            st.button("Aus dem aktuellen Bestand", key="an_neu_bestand", on_click=_pf_neu, args=("bestand",) + args + (fred, lade_kurse),
                      width="stretch", icon=":material/account_balance_wallet:",
                      help="Übernimmt die gebuchten Positionen (Seite Portfolio) – um geplante Käufe darauf aufzubauen")
        dirty = _dirty(pid)
        speichern = c3.button("Speichern", type="primary" if dirty else "secondary", icon=":material/save:", width="stretch",
                              disabled=ctx.nur_lesen or not dirty, key="an_pf_save",
                              help=("Überschreibt dieses Portfolio (frühere Versionen bleiben in der Google-Tabelle)" if ent.get("gespeichert")
                                    else "Legt das Portfolio im Team an")
                              + ". Noch nicht übernommene Eingaben unten zuerst übernehmen – oder gleich «Übernehmen & speichern».")
        if ss.pop("an_pf_save_req", False) and not ctx.nur_lesen:     # «Übernehmen & speichern» im Eingabe-Formular
            if dirty:
                speichern = True
            else:
                st.toast("Übernommen – es gab keine Änderungen zum Speichern.", icon=":material/check:")
        c4.button("Verwerfen" if ent.get("gespeichert") else "Schliessen", icon=":material/undo:" if ent.get("gespeichert") else ":material/close:",
                  width="stretch", key="an_pf_verwerfen", on_click=_pf_verwerfen, args=args,
                  disabled=ent.get("gespeichert") and not dirty,
                  help="Ungespeicherte Änderungen verwerfen und die gespeicherte Version laden" if ent.get("gespeichert")
                  else "Dieses ungespeicherte Portfolio schliessen")
        loeschen = False
        with c5.popover("Löschen", icon=":material/delete:", width="stretch", disabled=ctx.nur_lesen or not ent.get("gespeichert")):
            st.markdown(f"**«{_wert('an_pf_name', ent['name'])}»** für das ganze Team löschen?")
            st.caption("Es verschwindet aus der Auswahl, dem Vergleich und der Übernahme. Wiederherstellen ist unter «Alle Analyseportfolios» "
                       "möglich.")
            try:
                import newsdaten as N
                if N.probe_analyse_id(tab) == pid:
                    st.warning("Dieses Portfolio wird auf der Seite «Unternehmen» mitverfolgt – das Mitverfolgen endet damit.",
                               icon=":material/track_changes:")
            except Exception:  # noqa: BLE001
                pass
            loeschen = st.button("Endgültig löschen", type="primary", key="an_pf_del", icon=":material/delete_forever:")
        n1, n2 = st.columns([1.2, 2])
        ss.setdefault("an_pf_name", _wert("an_pf_name", ent["name"]))
        ss.setdefault("an_pf_notiz", _wert("an_pf_notiz", ent.get("notiz", "")))
        n1.text_input("Name", key="an_pf_name", placeholder="z. B. Variante A – Energie + Finanzen übergewichtet")
        n2.text_input("Notiz / Idee dahinter", key="an_pf_notiz", placeholder="Was soll diese Variante zeigen?")
        if ent.get("gespeichert") and gz is not None and pid in gz.index:
            z = gz.loc[pid]
            info = (f"Angelegt von {z['erstellt_von']} am {_datum(z['erstellt'])} · zuletzt gespeichert von {z['person']} am "
                    f"{_datum(z['zeit'])} · Version {int(z['versionen'])}")
        else:
            info = "Noch nicht gespeichert – nur in deiner Sitzung sichtbar."
        if dirty and ent.get("gespeichert"):
            info += " · **● ungespeicherte Änderungen**"
        andere = [n for i, n in zip(gs["id"], gs["name"]) if i != pid and str(n).strip().lower() == str(_wert("an_pf_name", "")).strip().lower()]
        st.caption(info + (" · Achtung: Ein anderes Portfolio heisst gleich." if andere else ""))

    if speichern:
        name = str(_wert("an_pf_name", "")).strip()
        if not name:
            st.error("Bitte einen Namen angeben.")
        else:
            e = _eingabe_aus_state(ss)
            e["jahre"] = int((ss.get("an_eingabe") or {}).get("jahre", 1))
            kz = ss.get("an_kz") or {}
            kz = kz.get("kz", {}) if kz.get("hash") == _hash(e) else {}
            zeit = jetzt()
            alt = dict(ent)
            vers = int(gz.loc[pid, "versionen"]) if gz is not None and pid in gz.index else 0
            ent.update(gespeichert=True, zeit=zeit, versionen=vers + 1, name=name, notiz=str(_wert("an_pf_notiz", "")).strip(),
                       basis=_hash(e, name, str(_wert("an_pf_notiz", ""))))
            ctx.speichern("analysen", [dict(zeit=zeit, person=ctx.person, id=pid, name=name,
                                            eingabe=json.dumps(e, ensure_ascii=False, default=str),
                                            kennzahlen=json.dumps(kz, ensure_ascii=False, default=str),
                                            notiz=str(_wert("an_pf_notiz", "")).strip())],
                          f"Analyseportfolio «{name}» gespeichert.")
            ent.clear()          # nur erreicht, wenn das Speichern nicht geklappt hat
            ent.update(alt)
    if loeschen and pid in gs_ids:
        z = gz.loc[pid]
        try:
            e = json.loads(z["eingabe"])
        except Exception:  # noqa: BLE001
            e = {}
        e["geloescht"] = True
        backup = (dict(pf), pid)
        pf.pop(pid, None)
        ss["an_aktiv"] = None
        rest = [i for i in reihe if i != pid]
        ss["an_pf_ziel"] = rest[0] if rest else "__neu__"
        ctx.speichern("analysen", [dict(zeit=jetzt(), person=ctx.person, id=pid, name=z["name"],
                                        eingabe=json.dumps(e, ensure_ascii=False), kennzahlen=z["kennzahlen"], notiz=z["notiz"])],
                      f"Analyseportfolio «{z['name']}» gelöscht.")
        pf.clear()               # Löschen hat nicht geklappt → zurück
        pf.update(backup[0])
        ss["an_aktiv"] = backup[1]
        ss.pop("an_pf_ziel", None)


def _alle_portfolios(ctx, tab: dict):
    """Übersicht aller gespeicherten Analyseportfolios + Papierkorb."""
    gs_alle = portfolios(tab.get("analysen"), mit_geloeschten=True)
    gs = gs_alle[~gs_alle["geloescht"]] if len(gs_alle) else gs_alle
    weg = gs_alle[gs_alle["geloescht"]] if len(gs_alle) else gs_alle
    with st.expander(f"Alle Analyseportfolios ({len(gs)})" + (f" · Papierkorb ({len(weg)})" if len(weg) else ""),
                     icon=":material/folder_open:"):
        if not len(gs):
            st.caption("Noch keine gespeicherten Analyseportfolios.")
        else:
            zeilen = []
            for _, z in gs.iterrows():
                try:
                    kz = json.loads(z["kennzahlen"])
                except Exception:  # noqa: BLE001
                    kz = {}
                try:
                    n_t = len(json.loads(z["eingabe"]).get("aktien") or [])
                except Exception:  # noqa: BLE001
                    n_t = None
                zeilen.append({"Name": z["name"], "Angelegt": f"{z['erstellt_von']}, {_datum(z['erstellt'])}",
                               "Zuletzt": f"{z['person']}, {_datum(z['zeit'])}", "Vers.": int(z["versionen"]),
                               "Titel": n_t, "Sharpe": _zahl(kz.get("sharpe")), "Rendite p.a.": _pct(kz.get("rendite"), 1, True),
                               "Vola": _pct(kz.get("vola")), "Vola Aktienteil": _pct(kz.get("vola_aktien")),
                               "A/T/C %": f"{kz.get('aktien', '–')} / {kz.get('treasuries', '–')} / {kz.get('cash', '–')}",
                               "Notiz": z["notiz"]})
            st.dataframe(pd.DataFrame(zeilen), hide_index=True, width="stretch")
            st.caption("Kennzahlen = Stand der letzten Berechnung vor dem Speichern («–», wenn danach noch geändert wurde). "
                       "Aktuelle Werte nebeneinander: Ansicht «Portfolios vergleichen».")
        if len(weg):
            st.markdown("**Papierkorb**")
            for _, z in weg.iterrows():
                a, b = st.columns([3, 1])
                a.caption(f"{z['name']} · gelöscht von {z['person']} am {_datum(z['zeit'])}")
                if b.button("Wiederherstellen", key=f"an_wh_{z['id']}", disabled=ctx.nur_lesen, icon=":material/restore_from_trash:"):
                    try:
                        e = json.loads(z["eingabe"])
                    except Exception:  # noqa: BLE001
                        e = {}
                    e.pop("geloescht", None)
                    ctx.speichern("analysen", [dict(zeit=jetzt(), person=ctx.person, id=z["id"], name=z["name"],
                                                    eingabe=json.dumps(e, ensure_ascii=False), kennzahlen=z["kennzahlen"],
                                                    notiz=z["notiz"])], f"Analyseportfolio «{z['name']}» wiederhergestellt.")


def _seite_vergleich(ctx, tab: dict, gs: pd.DataFrame, fred: dict, lade_kurse):
    """Mehrere Analyseportfolios (und auf Wunsch den Bestand) im gleichen Zeitfenster nebeneinander."""
    import portfolio as P
    import sektoren as SK

    ss = st.session_state
    pf = ss.get("an_pf", {})
    pid = ss.get("an_aktiv")
    gs_ids = list(gs["id"])
    reihe = [i for i in pf if i not in gs_ids] + gs_ids
    namen = {}
    for i in reihe:
        if i == pid:
            namen[i] = str(_wert("an_pf_name", "")).strip() or pf[i]["name"]
        elif i in pf:
            namen[i] = pf[i]["name"]
        else:
            namen[i] = str(gs.set_index("id").loc[i, "name"])
    # gleiche Namen unterscheidbar machen
    zaehler = {}
    for i in reihe:
        n = namen[i]
        zaehler[n] = zaehler.get(n, 0) + 1
        if zaehler[n] > 1:
            namen[i] = f"{n} ({zaehler[n]})"
    optionen = reihe + [BESTAND]
    namen[BESTAND] = "Aktueller Bestand (Seite Portfolio)"

    labs = {i: namen[i] + (" · ungespeichert" if i in pf and not pf[i].get("gespeichert") else "") for i in optionen}

    with st.container(border=True):
        st.markdown("**Portfolios vergleichen** · alle im gleichen Zeitfenster, gegen die Benchmark des Dozenten (50/50), das "
                    "neutrale Portfolio (IPS) und den S&P 500")
        default = [i for i in st.session_state.get("an_vgl_wahl", []) if i in optionen] or reihe[:4]
        ss["an_vgl_wahl"] = default
        wahl = st.multiselect("Portfolios", optionen, format_func=labs.get, key="an_vgl_wahl", max_selections=8,
                              help="Offene Portfolios gehen mit ihrem aktuellen Stand in den Vergleich, auch ungespeichert.")
        o1, o2, o3 = st.columns([1.2, 1.6, 1])
        jahre = o1.segmented_control("Zeitraum der Kursdaten", [1, 2, 3, 5], default=1, format_func=lambda j: f"{j} J.",
                                     key="an_vgl_j") or 1
        fuellen = o2.segmented_control("Noch nicht verteiltes Budget rechnen als", list(FUELLEN), default="Index", key="an_vgl_f",
                                       format_func=lambda x: "Index (S&P 500 / Treasuries)" if x == "Index" else "Cash",
                                       help="Gilt für alle Portfolios gleich, damit der Vergleich fair ist.") or "Index"
        los = o3.button("Vergleich berechnen", type="primary", icon=":material/compare_arrows:", width="stretch",
                        disabled=len(wahl) < 1)

    # Eingaben einsammeln (aktueller Stand, auch ungespeichert)
    def _eingabe(i):
        if i == pid:
            return _eingabe_aus_state(ss)
        if i in pf and pf[i].get("state"):
            return _eingabe_aus_state(pf[i]["state"])
        try:
            return json.loads(gs.set_index("id").loc[i, "eingabe"])
        except Exception:  # noqa: BLE001
            return {}

    if los:
        stand = {}
        for i in wahl:
            if i == BESTAND:
                v = portfolio_vorlage(tab, fred, lade_kurse)
                if v is None:
                    st.warning("Im Portfolio sind noch keine Transaktionen erfasst – Bestand weggelassen.")
                    continue
                stand[i] = dict(bestand=True, aktien=v[0], treas=v[1], cash=v[2], kapital=v[3], pct=None)
            else:
                e = _eingabe(i)
                a_ = pd.DataFrame(e.get("aktien") or [])
                for c_ in ("Ticker", "Menge", "Betrag USD"):
                    if c_ not in a_.columns:
                        a_[c_] = None
                stand[i] = dict(bestand=False, aktien=a_, treas=_treas_df(pd.DataFrame(e.get("treas") or [])), cash=0.0,
                                kapital=float(e.get("kapital") or 0), pct=e.get("pct"))
        ss["an_vgl"] = dict(stand=stand, jahre=int(jahre), fuellen=fuellen, namen={i: namen[i] for i in stand},
                            sig=_vgl_signatur(wahl, jahre, fuellen, _eingabe))
    vg = ss.get("an_vgl")
    if not vg or not vg["stand"]:
        st.info("Portfolios wählen und auf «Vergleich berechnen» klicken. Tipp: Mit «Neu → Kopie des aktiven Portfolios» "
                "legst du schnell eine Variante an, die du hier gegen das Original stellst.")
        return
    if vg.get("sig") != _vgl_signatur(wahl, jahre, fuellen, _eingabe):
        st.caption("Auswahl, Einstellungen oder ein Portfolio wurden seit der Berechnung geändert – «Vergleich berechnen» klicken, "
                   "um zu aktualisieren.")

    # --- Kurse: ein Abruf für alle ------------------------------------------------
    ticker = set()
    for d in vg["stand"].values():
        ticker |= {_txt(t).strip().upper().replace(".", "-") for t in d["aktien"]["Ticker"] if _txt(t).strip()}
        bands = [baender(max(0.0, (pd.to_datetime(f, errors="coerce") - pd.Timestamp.today()).days / 365.25), _txt(n))
                 if pd.notna(pd.to_datetime(f, errors="coerce")) else _txt(b)
                 for b, f, n in zip(d["treas"]["Laufzeit"], d["treas"].get("Fälligkeit", [None] * len(d["treas"])),
                                    d["treas"].get("Bezeichnung", [""] * len(d["treas"])))]
        ticker |= {PROXY[b][0] for b in bands if b in PROXY}
    hist, fehlt, zeit = lade_historie(tuple(sorted(ticker | {SPX, NEUTRAL_PROXY, RF_TICKER})), int(vg["jahre"]))
    if SPX not in hist:
        st.error("Kursdaten von Yahoo Finance sind gerade nicht erreichbar. Bitte in einer Minute nochmals versuchen.")
        return
    letzte = {t: float(s.iloc[-1]) for t, s in hist.items()}

    # --- je Portfolio zusammenstellen ----------------------------------------------
    roh, hinweise = {}, []
    for i, d in vg["stand"].items():
        if d["bestand"]:
            pos, cash, total, h = zusammenstellen(d["aktien"], d["treas"], d["cash"], d["kapital"], False, letzte)
            fuell = {}
        else:
            pos, cash, total, h = zusammenstellen(d["aktien"], d["treas"], 0, d["kapital"], True, letzte)
            fuell = offenes_budget(pos, cash, d["kapital"], d["pct"]) if vg["fuellen"] == "Index" else {}
        hinweise += [f"{vg['namen'][i]}: {x}" for x in h]
        if total <= 0 or not len(pos):
            hinweise.append(f"{vg['namen'][i]}: keine Positionen – weggelassen.")
            continue
        roh[i] = dict(pos=pos, cash=cash, total=total, fuell={t: w for t, w in fuell.items() if t in hist})
    for h in hinweise:
        st.warning(h)
    if not roh:
        return
    # gemeinsames Zeitfenster: spätester Start aller verwendeten Titel
    benutzt = {SPX, NEUTRAL_PROXY} | {t for r in roh.values() for t in list(r["pos"]["ticker"]) + list(r["fuell"])}
    start = max(hist[t].index.min() for t in benutzt if t in hist)
    hist_c = {t: (s[s.index >= start] if t != RF_TICKER else s) for t, s in hist.items()}
    erg = {}
    for i, r in roh.items():
        e_ = rechne(r["pos"], r["cash"], r["total"], hist_c, ctx.alloc["neutral"], r["fuell"])
        if e_ is not None:
            erg[i] = e_
    if not erg:
        st.error("Zu wenige gemeinsame Kursdaten – Zeitraum vergrössern oder Ticker prüfen.")
        return
    erstes = next(iter(erg.values()))
    k0 = erstes["k"]
    # --- PDF des Vergleichs (Update 13) ------------------------------------------------
    import seitenpdf as SP

    ph_pdf = st.empty()
    R = SP.Sammler("Vergleich der Analyseportfolios",
                   f"{len(erg)} Portfolios nebeneinander · gemeinsames Zeitfenster {k0['S&P 500']['von']:%d.%m.%Y} – "
                   f"{k0['S&P 500']['bis']:%d.%m.%Y} ({k0['S&P 500']['tage']} Handelstage) · offenes Budget als "
                   f"{'Index' if vg['fuellen'] == 'Index' else 'Cash'} gerechnet", kurz="Vergleich Analyseportfolios", quer=True)
    gz_ = gs.set_index("id") if len(gs) else None
    R.tabelle(pd.DataFrame([{
        "Portfolio": vg["namen"][i],
        "Art": "Bestand (Transaktionen)" if i == BESTAND else ("offen, ungespeichert" if i in pf and not pf[i].get("gespeichert")
                                                               else "gespeichert"),
        "Angelegt von": (str(gz_.loc[i, "erstellt_von"]) if gz_ is not None and i in gz_.index else "–"),
        "Zuletzt": (f"{gz_.loc[i, 'person']}, {_datum(gz_.loc[i, 'zeit'])}" if gz_ is not None and i in gz_.index else "–"),
        "Notiz": (str(gz_.loc[i, "notiz"])[:120] if gz_ is not None and i in gz_.index and _txt(gz_.loc[i, "notiz"]) else "")}
        for i in erg]), breiten=[55, 40, 35, 50, 85])
    st.caption(f"Gemeinsames Zeitfenster {k0['S&P 500']['von']:%d.%m.%Y} – {k0['S&P 500']['bis']:%d.%m.%Y} "
               f"({k0['S&P 500']['tage']} Handelstage) · Kurse {zeit}"
               + (f" · offenes Budget als Index gerechnet" if vg["fuellen"] == "Index" else " · offenes Budget als Cash"))

    # --- Regeln & Sektoren je Portfolio ---------------------------------------------
    sa = SK.lade_allokation(tab.get("sektor_allokation"))["sektoren"]
    info_all = {t: P.ticker_info(t) for r in roh.values() for t in r["pos"].loc[r["pos"]["klasse"] == "Aktie", "ticker"]}
    zusatz = {}
    for i in erg:
        r = roh[i]
        pos, cash, total = r["pos"], r["cash"], r["total"]
        ak_pos = pos[pos["klasse"] == "Aktie"]
        ak_df = pd.DataFrame({"ticker": ak_pos["ticker"].tolist(),
                              "name": [info_all.get(t, {}).get("name", "") for t in ak_pos["ticker"]],
                              "sektor": [info_all.get(t, {}).get("sektor") or "Unbekannt" for t in ak_pos["ticker"]],
                              "wert": ak_pos["wert"].astype(float).tolist()})
        sek_p = SK.pruefe_portfolio(ak_df, total, sa, ctx.sek_index["gewichte"], float(ctx.alloc["aktien"]))
        checks = pruefungen(pos, cash, total, {t: info_all.get(t, {}) for t in ak_pos["ticker"]}, ctx.alloc)
        ak_w = float(ak_pos["wert"].sum())
        tr = pos[pos["klasse"] == "Treasury"]
        tr_w = float(tr["wert"].sum())
        f_a = erg[i]["fuell"].get(SPX, 0.0)
        f_t = erg[i]["fuell"].get(NEUTRAL_PROXY, 0.0) if NEUTRAL_PROXY != SPX else 0.0
        dur = float(sum(PROXY[b][1] * w for b, w in zip(tr["band"], tr["wert"])) / tr_w) if tr_w else None
        zusatz[i] = dict(sek=sek_p, checks=checks, ak_w=ak_w, tr_w=tr_w, f_a=f_a, f_t=f_t, dur=dur, ak_df=ak_df,
                         cash_eff=max(0.0, cash - f_a - f_t), total=total, n=len(ak_pos),
                         eff=eff_anzahl(ak_pos["wert"].astype(float)) if len(ak_pos) else 0.0)

    spalten = [vg["namen"][i] for i in erg]
    neutral_cols = [BENCH, "Neutral (IPS)", "S&P 500"]

    def _z(name, werte: dict, bed="", neutral=None):
        z = {"Kennzahl": name}
        for i in erg:
            z[vg["namen"][i]] = werte.get(i, "–")
        for c in neutral_cols:
            z[c] = (neutral or {}).get(c, "–")
        z["Bedeutung"] = bed
        return z

    zeilen = [
        _z("Aufteilung A / T / C", {i: f"{(zusatz[i]['ak_w'] + zusatz[i]['f_a']) / zusatz[i]['total'] * 100:.0f} / "
                                       f"{(zusatz[i]['tr_w'] + zusatz[i]['f_t']) / zusatz[i]['total'] * 100:.0f} / "
                                       f"{zusatz[i]['cash_eff'] / zusatz[i]['total'] * 100:.0f} %" for i in erg},
           "Aktien / Treasuries / Cash in % (inkl. offenes Budget)",
           {"Neutral (IPS)": f"{ctx.alloc['neutral']['aktien']:.0f} / "
                             f"{100 - ctx.alloc['neutral']['aktien'] - ctx.alloc['neutral'].get('cash', 5):.0f} / "
                             f"{ctx.alloc['neutral'].get('cash', 5):.0f} %", "S&P 500": "100 / 0 / 0 %",
            BENCH: f"{C.BENCHMARK['aktien']:.0f} / {C.BENCHMARK['anleihen']:.0f} / {C.BENCHMARK['cash']:.0f} %"}),
        _z("Investiert", {i: f"{zusatz[i]['ak_w'] / zusatz[i]['total'] * 100:.0f} / {zusatz[i]['tr_w'] / zusatz[i]['total'] * 100:.0f} %"
                          for i in erg}, "davon schon in Titeln bzw. Treasuries (Rest = offenes Budget / Cash)"),
        _z("Titel (effektiv)", {i: f"{zusatz[i]['n']} ({ui.de(zusatz[i]['eff'], 1)})" for i in erg},
           "Anzahl Aktien, in Klammern die effektive Anzahl (Klumpen zählen weniger)"),
        _z("Kapital", {i: f"{ui.de(zusatz[i]['total'], 0)} USD" for i in erg}),
    ]
    for key, name, f, erkl in ZEILEN:
        werte = {i: f(erg[i]["k"]["Vorauswahl"].get(key)) for i in erg if key in erg[i]["k"]["Vorauswahl"]}
        if not werte:
            continue
        zeilen.append(_z(name, werte, erkl, {c: f(k0[c].get(key)) if key in k0.get(c, {}) else "–" for c in neutral_cols}))
    for key, name in (("vola", "Volatilität Aktienteil"), ("sharpe", "Sharpe Aktienteil"), ("beta", "Beta Aktienteil"),
                      ("active_share", "Active Share Aktienteil")):
        fmt = _pct if key in ("vola", "active_share") else _zahl
        zeilen.append(_z(name, {i: fmt(erg[i]["k"].get("Aktienteil", {}).get(key)) for i in erg},
                         "nur eure Titel, auf 100 % hochgerechnet" + (f" · IPS {ui.de(ctx.ips.get('active_share_min'), 0)}–"
                                                                      f"{ui.de(ctx.ips.get('active_share_max'), 0)} %"
                                                                      if key == "active_share" else "")))
    zeilen += [
        _z("Duration Treasuries", {i: f"{ui.de(zusatz[i]['dur'], 1)} J." if zusatz[i]["dur"] else "–" for i in erg},
           f"ETF-Näherung · Taktik {ui.de(ctx.alloc['duration'], 1)} J."),
        _z("Regeln", {i: (f"{sum(1 for s_, _ in zusatz[i]['checks'] if s_ == L.ROT)} Verstösse"
                          if any(s_ == L.ROT for s_, _ in zusatz[i]["checks"]) else "ok")
                      + (f", {sum(1 for s_, _ in zusatz[i]['checks'] if s_ == L.GELB)} Hinweise"
                         if any(s_ == L.GELB for s_, _ in zusatz[i]["checks"]) else "") for i in erg},
           "IPS-Bänder, Klumpen > 10 %, Mandat S&P 500, Duration (wie Reiter «Regeln & Struktur»)"),
        _z("Sektoren eingehalten", {i: f"{zusatz[i]['sek']['n'][L.GRUEN]} von 11" for i in erg},
           "gegen die Seite «Sektorallokation» (Ziel bzw. Richtung)"),
        _z("Σ |Ist − Ziel| Sektoren", {i: f"{ui.de(zusatz[i]['sek']['summe_abw'], 1)} Pp." for i in erg},
           "Summe der Abweichungen der Sektoren mit Zielzahl (kleiner = näher am Plan)"),
    ]
    st.dataframe(pd.DataFrame(zeilen), hide_index=True, width="stretch",
                 column_config={"Bedeutung": st.column_config.TextColumn(width="large")})
    vgl_df = pd.DataFrame(zeilen)
    R.h("Kennzahlen nebeneinander")
    R.tabelle(vgl_df, breiten=[34] + [24] * (len(vgl_df.columns) - 2) + [70])

    farbe = {i: PF_FARBEN[n % len(PF_FARBEN)] for n, i in enumerate(erg)}
    R.linien([(vg["namen"][i], (1 + e_["r_p"]).cumprod() * 100, SP.rgb(farbe[i])) for i, e_ in erg.items()]
             + [(n_, (1 + rr).cumprod() * 100, SP.rgb(fb)) for n_, rr, fb in
                ((BENCH, erstes["r_b"], BENCH_FARBE), ("Neutral (IPS)", erstes["r_n"], ui.GOLD), ("S&P 500", erstes["r_s"], ui.STAHL))],
             titel="Wertentwicklung (Start = 100)")
    R.h2("Rendite und Risiko")
    R.tabelle(pd.DataFrame([{"Portfolio": vg["namen"][i], "Rendite p.a.": _pct(e_["k"]["Vorauswahl"].get("rendite"), 1, True),
                             "Volatilität p.a.": _pct(e_["k"]["Vorauswahl"].get("vola")),
                             "Sharpe": _zahl(e_["k"]["Vorauswahl"].get("sharpe")),
                             "Max. Drawdown": _pct(e_["k"]["Vorauswahl"].get("mdd")),
                             "Rendite pro Einheit Vola": _zahl((e_["k"]["Vorauswahl"].get("rendite") or 0)
                                                               / (e_["k"]["Vorauswahl"].get("vola") or float("nan")))}
                            for i, e_ in erg.items()]
                           + [{"Portfolio": n_, "Rendite p.a.": _pct(k0[n_].get("rendite"), 1, True), "Volatilität p.a.": _pct(k0[n_].get("vola")),
                               "Sharpe": _zahl(k0[n_].get("sharpe")), "Max. Drawdown": _pct(k0[n_].get("mdd")),
                               "Rendite pro Einheit Vola": _zahl((k0[n_].get("rendite") or 0) / (k0[n_].get("vola") or float("nan")))}
                              for n_ in (BENCH, "Neutral (IPS)", "S&P 500") if n_ in k0]), breiten=[60, 35, 35, 30, 35, 40])
    R.neue_seite()
    R.h("Regeln je Portfolio")
    regel_z, regel_f = [], []
    for i in erg:
        for s_, t_ in zusatz[i]["checks"]:
            regel_z.append({"Portfolio": vg["namen"][i], "Status": {L.ROT: "Verstoss", L.GELB: "Hinweis", L.GRUEN: "ok"}[s_],
                            "Prüfung": t_})
            regel_f.append([None, _HELL_RGB.get(s_), None])
    R.tabelle(pd.DataFrame(regel_z), breiten=[50, 22, 190], farben=regel_f, max_zeilen=200)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Wertentwicklung (Start = 100)**")
        fig = go.Figure()
        for i, e_ in erg.items():
            w = (1 + e_["r_p"]).cumprod() * 100
            fig.add_trace(go.Scatter(x=w.index, y=w, name=vg["namen"][i], line=dict(color=farbe[i], width=2)))
        for name, rr, fb in ((BENCH, erstes["r_b"], BENCH_FARBE), ("Neutral (IPS)", erstes["r_n"], ui.GOLD),
                             ("S&P 500", erstes["r_s"], ui.STAHL)):
            w = (1 + rr).cumprod() * 100
            fig.add_trace(go.Scatter(x=w.index, y=w, name=name, line=dict(color=fb, width=1.6, dash="dot")))
        fig.update_layout(height=340, margin=dict(l=8, r=8, t=10, b=8), hovermode="x unified")
        ui.zeige(fig)
    with c2:
        st.markdown("**Rendite und Risiko**")
        fig = go.Figure()
        for i, e_ in erg.items():
            kv = e_["k"]["Vorauswahl"]
            fig.add_trace(go.Scatter(x=[kv.get("vola")], y=[kv.get("rendite")], mode="markers+text", name=vg["namen"][i],
                                     text=[vg["namen"][i]], textposition="top center", marker=dict(size=13, color=farbe[i]),
                                     hovertemplate="%{text}<br>Vola %{x:.1f} %<br>Rendite %{y:.1f} %<extra></extra>"))
        for name, fb in ((BENCH, BENCH_FARBE), ("Neutral (IPS)", ui.GOLD), ("S&P 500", ui.STAHL)):
            kv = k0.get(name, {})
            fig.add_trace(go.Scatter(x=[kv.get("vola")], y=[kv.get("rendite")], mode="markers+text", name=name, text=[name],
                                     textposition="bottom center", marker=dict(size=11, color=fb, symbol="diamond"),
                                     hovertemplate="%{text}<br>Vola %{x:.1f} %<br>Rendite %{y:.1f} %<extra></extra>"))
        fig.update_layout(height=340, margin=dict(l=8, r=8, t=10, b=8), showlegend=False)
        fig.update_xaxes(title="Volatilität p.a.", ticksuffix=" %")
        fig.update_yaxes(title="Rendite p.a.", ticksuffix=" %")
        ui.zeige(fig)
    st.caption("Vergangene Werte im gemeinsamen Zeitfenster, konstante Gewichte, dividendenbereinigt – keine Prognose. "
               "Oben links = mehr Rendite bei weniger Schwankung.")

    # --- Sektoren nebeneinander ---------------------------------------------------
    st.markdown("**Sektoren** · Anteil am Aktienteil und Abweichung vom Ziel der Sektorallokation")
    erste_sek = zusatz[next(iter(erg))]["sek"]["sektoren"].set_index("Sektor")
    sek_zeilen, stufen = [], []
    for s in C.SEKTOREN:
        z = {"Sektor": s, "Richtung": erste_sek.loc[s, "Richtung"], "Index": f"{ui.de(erste_sek.loc[s, '_index'], 2)} %",
             "Ziel": erste_sek.loc[s, "Ziel"]}
        st_z = {}
        for i in erg:
            r_ = zusatz[i]["sek"]["sektoren"].set_index("Sektor").loc[s]
            diff = r_["_diff"]
            z[vg["namen"][i]] = (f"{ui.de(r_['_ist'], 1)} % ({ui.de(diff, 1, True)})" if diff is not None and pd.notna(diff)
                                 else f"{ui.de(r_['_ist'], 1)} % (aktiv {ui.de(r_['_ist'] - r_['_index'], 1, True)})")
            st_z[vg["namen"][i]] = r_["_stufe"]
        sek_zeilen.append(z)
        stufen.append(st_z)
    sek_df = pd.DataFrame(sek_zeilen)
    st_df = pd.DataFrame(stufen)

    def _farben(_):
        out = pd.DataFrame("", index=sek_df.index, columns=sek_df.columns)
        for c in st_df.columns:
            out[c] = [f"background-color: {ui.HELL.get(s_, '')}; color: {ui.FARBE.get(s_, '')}" for s_ in st_df[c]]
        return out

    st.dataframe(sek_df.style.apply(_farben, axis=None), hide_index=True, width="stretch")
    R.h("Sektoren nebeneinander")
    R.text("Je Portfolio: Ist-Anteil am Aktienteil, in Klammern Ist − Ziel in Pp. (ohne Zielzahl: aktiv vs. Index). "
           "Grün ≤ 0,5 Pp., gelb ≤ 1 Pp., sonst rot.", klein=True)
    R.tabelle(sek_df, farben=[[None] * 4 + [_HELL_RGB.get(st_z.get(c_)) for c_ in sek_df.columns[4:]] for st_z in stufen])
    st.caption("Je Portfolio: Ist-Anteil am Aktienteil, in Klammern Ist − Ziel in Pp. (ohne Zielzahl: aktiv vs. Index). "
               "Grün ≤ 0,5 Pp., gelb ≤ 1 Pp., sonst rot – wie im Reiter «Regeln & Struktur».")

    # --- Titel nebeneinander --------------------------------------------------------
    st.markdown("**Titel** · Gewicht am Gesamtportfolio")
    tz = {}
    for i in erg:
        for t, w in zusatz[i]["ak_df"].groupby("ticker")["wert"].sum().items():
            tz.setdefault(t, {})[vg["namen"][i]] = w / zusatz[i]["total"] * 100
    if tz:
        tdf = pd.DataFrame([{"Titel": t, "Name": info_all.get(t, {}).get("name", ""), "Sektor": info_all.get(t, {}).get("sektor", ""),
                             **{c: (f"{ui.de(w[c], 2)} %" if c in w else "") for c in spalten},
                             "In Portfolios": f"{len(w)} von {len(spalten)}", "_n": len(w), "_w": sum(w.values())}
                            for t, w in tz.items()])
        tdf = tdf.sort_values(["_n", "_w"], ascending=False).drop(columns=["_n", "_w"])
        st.dataframe(tdf, hide_index=True, width="stretch")
        R.neue_seite()
        R.h(f"Titel nebeneinander · Gewicht am Gesamtportfolio ({len(tz)} verschiedene Titel)")
        R.tabelle(tdf, max_zeilen=600)
        gemeinsam = [t for t, w in tz.items() if len(w) == len(spalten)]
        if len(spalten) > 1:
            st.caption(f"In allen gewählten Portfolios: {', '.join(sorted(gemeinsam)) if gemeinsam else 'kein Titel'} · "
                       f"{len(tz)} verschiedene Titel insgesamt.")
            R.text(f"In allen gewählten Portfolios: {', '.join(sorted(gemeinsam)) if gemeinsam else 'kein Titel'}.", klein=True)
    R.text("Vergangene Werte im gemeinsamen Zeitfenster, konstante Gewichte, dividendenbereinigt – keine Prognose. Cash verzinst "
           "mit SOFR − 0,5 % wie in der Simulation. Duration der Treasuries im Vergleich als ETF-Näherung.", klein=True)
    SP.knopf(ph_pdf, R, "vergleich_pdf", dateiname=f"Vergleich_Analyseportfolios_{datetime.now():%Y-%m-%d}.pdf")


def _vgl_signatur(wahl, jahre, fuellen, eingabe_fn) -> str:
    teile = []
    for i in wahl:
        if i == BESTAND:
            teile.append(i)
        else:
            teile.append(_hash(eingabe_fn(i)))
    return json.dumps([teile, jahre, fuellen], default=str)


def seite_analyse(ctx, fred: dict, lade_kurse):
    import portfolio as P

    ui.seitenkopf("Analyse – Analyseportfolios prüfen und vergleichen",
                  "Mehrere Analyseportfolios anlegen, Titel und Mengen eingeben – die App berechnet Rendite, Risiko, Sharpe Ratio, "
                  "Beta, Value at Risk, Korrelationen und prüft eure Regeln. Vergleich immer mit dem neutralen Portfolio "
                  "(strategische Allokation laut IPS) und dem S&P 500. Ansicht «Anleihen & Cash»: Treasuries einzeln gegen die "
                  "Benchmark, Szenarien, Stresstest und Zins auf Cash.")
    tab = ctx.tabellen
    kapital_std, bm = P._einstellungen(tab.get("einstellungen"))
    ss = st.session_state
    ss.setdefault("an_ver", 0)
    gs = portfolios(tab.get("analysen"))
    args = (tab, kapital_std, ctx.alloc)

    # --- Widget-Werte nach einem Seitenwechsel wiederherstellen ----------------------
    for k in WIDGET_KEYS:
        if k not in ss and "_" + k in ss:
            ss[k] = ss["_" + k]
    # --- Portfolios in der Sitzung -----------------------------------------------------
    pf = ss.setdefault("an_pf", {})
    if ziel := ss.pop("an_pf_ziel", None):                     # nach dem Löschen
        if ziel == "__neu__":
            _pf_neu("leer", *args)
        else:
            _pf_oeffnen(ziel, *args)
    if ss.get("an_aktiv") not in pf:
        if "an_aktien" in ss and not pf:                       # Sitzung von vor dem Update: Eingabe behalten
            pid = uuid.uuid4().hex[:8]
            pf[pid] = dict(name="Neues Portfolio 1", notiz="", gespeichert=False, zeit=None, basis=None, state=None)
            for k, v in _standard_state(kapital_std, ctx.alloc).items():
                ss.setdefault(k, v)
            _aktiv_setzen(pid)
        elif pf:
            _pf_oeffnen(next(iter(pf)), *args)
        elif len(gs):
            _pf_oeffnen(gs["id"].iloc[0], *args)
        else:
            _pf_neu("leer", *args)
    pid = ss["an_aktiv"]
    ent = pf[pid]
    # gespeicherte Portfolios: von anderen geändert oder gelöscht?
    gz = gs.set_index("id") if len(gs) else None
    for i, e_ in list(pf.items()):
        if not e_.get("gespeichert"):
            continue
        if gz is None or i not in gz.index:
            e_["gespeichert"] = False                         # inzwischen gelöscht → bleibt als ungespeichertes offen
            if i == pid:
                st.info("Dieses Portfolio wurde inzwischen gelöscht – «Speichern» legt es wieder an.", icon=":material/info:")
            continue
        neuer = (str(gz.loc[i, "zeit"]) != str(e_.get("zeit") or "")
                 or int(gz.loc[i, "versionen"]) != int(e_.get("versionen") or 0))
        if not neuer:
            continue
        if i == pid:
            if _hash(_eingabe_aus_state(ss), _wert("an_pf_name", ""), _wert("an_pf_notiz", "")) == e_.get("basis"):
                _pf_oeffnen(i, *args, verwerfen=True)        # unverändert → neue Version still übernehmen
                ent = pf[pid]
            else:
                st.warning(f"{gz.loc[i, 'person']} hat dieses Portfolio am {_datum(gz.loc[i, 'zeit'])} gespeichert, während du es "
                           "geändert hast. «Speichern» überschreibt die Version, «Verwerfen» lädt sie.", icon=":material/sync_problem:")
        elif not e_.get("state") or _hash(_eingabe_aus_state(e_["state"]), e_["name"], e_.get("notiz", "")) == e_.get("basis"):
            e_["state"] = None                                 # beim nächsten Öffnen neu laden
            e_["zeit"], e_["versionen"] = str(gz.loc[i, "zeit"]), int(gz.loc[i, "versionen"])
    if list(ss["an_aktien"].columns) != AK_SPALTEN:          # Sitzung aus der Zeit vor dem Update
        ss["an_aktien"] = _aktien_neu_rechnen(ss["an_aktien"])
    ss.setdefault("an_kap_txt", ui.de(float(ss.get("an_kapital") or kapital_std), 0))
    if "an_p_ak" not in ss:
        ss.update(an_p_ak=float(ctx.alloc["aktien"]), an_p_tr=float(ctx.alloc["anleihen"]), an_p_ca=float(ctx.alloc["cash"]))
    ss.setdefault("an_bezug", GESAMT)

    ansicht = st.segmented_control("Ansicht", ["bearbeiten", "sektoren", "anleihen", "vergleich"], default="bearbeiten",
                                   key="an_ansicht",
                                   format_func=lambda x: {"bearbeiten": "Portfolio bearbeiten & analysieren",
                                                          "sektoren": "Sektorgewichte anpassen",
                                                          "anleihen": "Anleihen & Cash",
                                                          "vergleich": "Portfolios vergleichen"}[x],
                                   label_visibility="collapsed") or "bearbeiten"
    if ansicht == "sektoren":       # Update 18: Sektorgewichte eines Analyseportfolios als eigene Ansicht (Wunsch Sophy 28.11.)
        _portfolio_leiste(ctx, tab, kapital_std, ctx.alloc, gs, fred, lade_kurse)
        for k_ in ("an_meldung", "an_meldung_info"):
            if m := ss.pop(k_, None):
                (st.warning if k_ == "an_meldung" else st.info)(m)
        for m_ in ss.pop("an_budget_meld", []):
            st.info(m_, icon=":material/tune:")
        _sektor_bereich(ctx, eigene_ansicht=True)
        for k in WIDGET_KEYS:
            if k in ss:
                ss["_" + k] = ss[k]
        return
    if ansicht == "anleihen":       # Update 12: frühere Seite «Anleihen & Duration» + Zins auf Cash, für das aktive Portfolio
        import anleihen as AN

        _portfolio_leiste(ctx, tab, kapital_std, ctx.alloc, gs, fred, lade_kurse)
        AN.bereich_analyse(ctx, fred)
        for k in WIDGET_KEYS:
            if k in ss:
                ss["_" + k] = ss[k]
        return
    if ansicht == "vergleich":
        _seite_vergleich(ctx, tab, gs, fred, lade_kurse)
        _alle_portfolios(ctx, tab)
        for k in WIDGET_KEYS:
            if k in ss:
                ss["_" + k] = ss[k]
        return

    _portfolio_leiste(ctx, tab, kapital_std, ctx.alloc, gs, fred, lade_kurse)
    ph_dl = _download_leiste(ctx, fred)
    gespeichert = gs

    if m := ss.pop("an_meldung", None):
        st.warning(m)
    if m := ss.pop("an_meldung_info", None):
        st.info(m, icon=":material/info:")

    with st.expander(f"Analyseportfolio ins Portfolio übernehmen (Kauf per {pd.Timestamp(C.SIMULATION_START):%d.%m.%Y})",
                     icon=":material/shopping_cart_checkout:", expanded=bool(ss.get("ue_vor"))):
        import uebernahme

        uebernahme.bereich(ctx, fred, gespeichert)

    _scoring_bereich()
    _treasury_bereich()

    # --- Eingabe (Update 9): alles in einem Formular – getippt wird ohne Unterbruch, gerechnet erst mit «Übernehmen» ----------
    import portfolio as P2

    v = ss["an_ver"]
    sfx = f"{v}.{ss.get('an_ak_ver', 0)}.{ss.get('an_tr_ver', 0)}.{ss.get('an_form_ver', 0)}"
    lo_a, hi_a = ctx.alloc["baender"]["aktien"]
    lo_t, hi_t = ctx.alloc["baender"]["treasuries"]
    lo_c, hi_c = ctx.alloc["baender"]["cash"]
    b = budget(ss)
    with st.container(border=True):
        st.markdown("**Aufteilung & Budget** · jeder Kauf verringert das verfügbare Budget seiner Anlageklasse")

        def _rest(x):
            return f"<span style='color:{'#B93A32' if x < -0.5 else '#2F855A'}'>{ui.de(x, 0)} USD</span>"
        ui.kopf_kpis([
            ("Aktien verfügbar", _rest(b["rest_a"]), f"Budget {ui.de(b['budget_a'], 0)} · investiert {ui.de(b['inv_a'], 0)} USD"),
            ("Treasuries verfügbar", _rest(b["rest_t"]), f"Budget {ui.de(b['budget_t'], 0)} · investiert {ui.de(b['inv_t'], 0)} USD"),
            ("Cash (Rest)", f"{ui.de(b['frei'], 0)} USD",
             f"{ui.de(b['frei'] / b['kapital'] * 100 if b['kapital'] else 0, 1)} % · Ziel mind. {ui.de(b['cash_ziel'], 0)} USD"
             + _cash_zins_txt(b["frei"], fred, ctx)),
            ("Verfügbares Kapital", f"{ui.de(max(b['frei'] - b['cash_ziel'], 0), 0)} USD",
             f"von {ui.de(b['kapital'], 0)} USD · noch investierbar ohne Cash-Ziel zu unterschreiten"),
        ])
        if abs(b["summe_pct"] - 100) > 0.01:
            st.warning(f"Die Prozentsätze ergeben {ui.de(b['summe_pct'], 1)} % statt 100 %.", icon=":material/percent:")
        aus = [n for n, w, (lo, hi) in (("Aktien", b["pct"]["aktien"], (lo_a, hi_a)), ("Treasuries", b["pct"]["treas"], (lo_t, hi_t)),
                                        ("Cash", b["pct"]["cash"], (lo_c, hi_c))) if not lo <= w <= hi]
        if aus:
            st.warning("Ausserhalb der IPS-Bänder: " + ", ".join(aus), icon=":material/policy:")
        if b["rest_a"] < -0.5 or b["rest_t"] < -0.5:
            st.error("Budget überschritten (z. B. nach Senken eines Prozentsatzes) – Stück bzw. Beträge reduzieren.", icon=":material/block:")
        for m_ in ss.pop("an_budget_meld", []):
            st.info(m_, icon=":material/content_cut:")

        with st.form("an_eingabe_form", border=False, enter_to_submit=False):
            a1, a2, a3, a4 = st.columns([1.3, 1, 1, 1])
            a1.text_input("Kapital (USD)", value=_txt(ss.get("an_kap_txt")), key=f"an_f_kap{sfx}",
                          help="Zeilen mit fixem Gewicht % passen ihre Stückzahl beim Übernehmen automatisch an")
            a2.number_input("Aktien %", min_value=0.0, max_value=100.0, step=0.5, format="%.2f", value=float(ss.get("an_p_ak") or 0),
                            key=f"an_f_pak{sfx}", help=f"IPS-Band {lo_a:g}–{hi_a:g} % · Taktik {ui.de(ctx.alloc['aktien'], 1)} %")
            a3.number_input("Treasuries %", min_value=0.0, max_value=100.0, step=0.5, format="%.2f",
                            value=float(ss.get("an_p_tr") or 0), key=f"an_f_ptr{sfx}",
                            help=f"IPS-Band {lo_t:g}–{hi_t:g} % · Taktik {ui.de(ctx.alloc['anleihen'], 1)} %")
            a4.number_input("Cash %", min_value=0.0, max_value=100.0, step=0.5, format="%.2f", value=float(ss.get("an_p_ca") or 0),
                            key=f"an_f_pca{sfx}", help=f"IPS-Band {lo_c:g}–{hi_c:g} % · Taktik {ui.de(ctx.alloc['cash'], 1)} %")
            l, r = st.columns([1.35, 1])
            with l:
                st.markdown("**Aktien** · Ticker oder Firmenname eintippen, dann Stück, Betrag *oder* Gewicht % – der Rest rechnet "
                            "sich beim Übernehmen")
                st.radio("«Gewicht %» bezogen auf", [GESAMT, AKTIENTEIL], horizontal=True, key=f"an_f_bez{sfx}",
                         index=1 if ss.get("an_bezug") == AKTIENTEIL else 0,
                         format_func=lambda x: "Gesamtportfolio (Kapital)" if x == GESAMT else "Aktienteil (Kapital × Aktien-%)",
                         help="Gesamtportfolio: 2 % = 2 % des Kapitals (wie Titel-Maximum im IPS). Aktienteil: 2 % = 2 % des "
                              "Aktienbudgets (wie die Sektorgewichte). Beim Umstellen bleiben die Beträge gleich; % in der Tabelle "
                              "gelten im neuen Bezug.")
                liste = P2.sp500_liste()
                if liste:
                    c1, c2, c2b, c3 = st.columns([2.1, 0.7, 0.7, 1.1])
                    c1.selectbox("Titel hinzufügen", sorted(liste), index=None, key=f"an_f_such{sfx}",
                                 placeholder="Titel hinzufügen: z. B. MSFT oder Microsoft",
                                 format_func=lambda t: f"{t} – {liste[t][0]} · {liste[t][1]}", label_visibility="collapsed")
                    c2.number_input("Stück", min_value=0, step=10, value=0, key=f"an_f_stk{sfx}", label_visibility="collapsed",
                                    help="Stückzahl für den gewählten Titel")
                    c2b.number_input("%", min_value=0.0, max_value=100.0, step=0.5, format="%.2f", value=0.0, key=f"an_f_pct{sfx}",
                                     label_visibility="collapsed", help="oder Gewicht % (hat Vorrang vor Stück)")
                    c3.form_submit_button("Hinzufügen", icon=":material/add:", type="primary", width="stretch",
                                          on_click=_form_anwenden, args=(sfx, "uebernehmen"),
                                          help="Fügt den gewählten Titel in die Tabelle ein (übernimmt dabei auch die übrigen Eingaben)")
                akt_sort = ss.get("an_sort", "keine")
                s0, *s_kn = st.columns([0.7, 1.2, 1, 1, 1.2])
                s0.caption(f"Sortieren · aktiv: {SORTIERUNG.get(akt_sort, SORTIERUNG['keine'])}")
                for col, art in zip(s_kn, SORTIERUNG):
                    col.form_submit_button(SORTIERUNG[art], width="stretch", type="secondary",
                                           icon=":material/check:" if art == akt_sort else None,
                                           on_click=_form_anwenden, args=(sfx, "uebernehmen", art),
                                           help="Sortiert die Tabelle (übernimmt dabei die übrigen Eingaben). Die Sortierung bleibt, "
                                                "neu hinzugefügte Titel landen am richtigen Ort." if art != "keine"
                                           else "Ab jetzt nicht mehr automatisch sortieren (neue Titel unten anfügen)")
                st.data_editor(_editor_ansicht(ss["an_aktien"]), num_rows="dynamic", hide_index=True, width="stretch",
                               key=f"an_ed_a{sfx}", disabled=["Name", "Sektor", "Kurs USD", "Ist %", "Fix"],
                               column_config={
                                   "Löschen": st.column_config.CheckboxColumn("✕", help="Häkchen setzen = Zeile entfernen", width="small"),
                                   "Ticker": st.column_config.TextColumn(help="Ticker (z. B. MSFT, BRK-B) oder Firmenname (z. B. Microsoft)"),
                                   "Name": st.column_config.TextColumn(width="medium"),
                                   "Kurs USD": st.column_config.TextColumn(help="letzter Schlusskurs (Yahoo Finance)"),
                                   "Menge": st.column_config.TextColumn("Stück", help="Stückzahl → Betrag wird berechnet"),
                                   "Betrag USD": st.column_config.TextColumn(help="Betrag → ganze Stücke (abgerundet), Betrag danach exakt"),
                                   "Gewicht %": st.column_config.TextColumn(
                                       help="Anteil am Gesamtportfolio bzw. Aktienteil (Umschalter oben), bis 4 Nachkommastellen. "
                                            "Eingetippt = fix (Ziel): ändern sich Kapital oder Aktien-%, werden die Stück neu berechnet. "
                                            "Sonst zeigt die Spalte das Ist-Gewicht."),
                                   "Ist %": st.column_config.TextColumn(
                                       help="Tatsächliches Gewicht mit ganzen Stücken (gleicher Bezug wie «Gewicht %»). Weicht vom "
                                            "Ziel ab, weil nur ganze Aktien gekauft werden – bei teuren Titeln (z. B. BKNG) sichtbar."),
                                   "Fix": st.column_config.TextColumn(width="small",
                                                                      help="Was vorgegeben ist: % (Gewicht bleibt, Stück passen sich an), "
                                                                           "Stück oder USD (Betrag bleibt)")})
            with r:
                st.markdown("**Treasuries** · Betrag je Laufzeitband – für die Übernahme ins Portfolio Kupon und Fälligkeit ergänzen "
                            "(oder oben «Treasuries aus Excel übernehmen»)")
                st.data_editor(_treas_df(ss["an_treas"]).assign(Löschen=False), num_rows="dynamic", hide_index=True, width="stretch",
                               key=f"an_ed_t{sfx}",
                               column_config={
                                   "Löschen": st.column_config.CheckboxColumn("✕", help="Häkchen setzen = Zeile entfernen", width="small"),
                                   "Laufzeit": st.column_config.SelectboxColumn(options=list(PROXY),
                                                                                help="Mit Fälligkeit: automatisch aus der Restlaufzeit"),
                                   "Betrag USD": st.column_config.TextColumn(help="z. B. 3000000 – wird als 3'000'000 angezeigt"),
                                   "Kupon %": st.column_config.NumberColumn(min_value=0.0, max_value=15.0, step=0.125, format="%.3f"),
                                   "Fälligkeit": st.column_config.DateColumn(format="DD.MM.YYYY"),
                                   "Kurs %": st.column_config.NumberColumn(min_value=0.0, format="%.3f",
                                                                           help="leer = Preis aus der Zinskurve am Handelstag"),
                                   "Bezeichnung": st.column_config.TextColumn(help="z. B. «UST 4,25 % 15.11.2029»; "
                                                                                   "leer = automatisch"),
                                   "ISIN": st.column_config.TextColumn(help="z. B. US91282CLF67 – wird bei der Übernahme ins "
                                                                            "Portfolio mitgebucht. Eine CUSIP wird beim Übernehmen "
                                                                            "in die ISIN umgerechnet.")})
                st.caption("Treasuries höchstens Kapital × Treasury-%. Zeile entfernen: Häkchen in der Spalte ✕.")
            k1, k2, k3, k4 = st.columns([1.2, 1.3, 1.1, 2])
            k1.form_submit_button("Änderungen übernehmen", type="primary", icon=":material/check:", width="stretch",
                                  on_click=_form_anwenden, args=(sfx, "uebernehmen"),
                                  help="Rechnet alle Eingaben (Aufteilung, Tabellen, Suche) auf einmal")
            k2.form_submit_button("Übernehmen & speichern", icon=":material/save:", width="stretch", disabled=ctx.nur_lesen,
                                  on_click=_form_anwenden, args=(sfx, "speichern"),
                                  help="Übernimmt die Eingaben und speichert das Analyseportfolio fürs Team")
            k3.form_submit_button("Eingaben verwerfen", icon=":material/undo:", width="stretch",
                                  on_click=_form_anwenden, args=(sfx, "zurueck"),
                                  help="Noch nicht übernommene Eingaben verwerfen (der übernommene Stand bleibt)")
            k4.caption("Tippen und mehrere Werte ändern, so viel du willst – gerechnet wird erst beim Übernehmen. "
                       "Der Knopf «Speichern» oben speichert nur Übernommenes.")
        n_ak = sum(1 for t in ss["an_aktien"]["Ticker"] if _txt(t).strip())
        summe = sum(_num(b_) or 0 for b_ in ss["an_aktien"]["Betrag USD"])
        s_pct = sum(_num(p_) or 0 for p_ in ss["an_aktien"]["Gewicht %"])
        basis_ = _basis(ss)
        ist_pct = summe / basis_ * 100 if basis_ > 0 else 0.0
        teil = ss.get("an_bezug") == AKTIENTEIL
        ziel_pct = 100.0 if teil else float(ss.get("an_p_ak") or 0)
        if s_pct > ziel_pct + 0.01:
            st.warning(f"Die eingegebenen Gewichte ergeben {ui.de(s_pct, 2)} % "
                       + (f"des Aktienteils – mehr als 100 %." if teil else
                          f"des Gesamtportfolios, «Aktien %» steht aber auf {ui.de(ziel_pct, 2)} %. Für euer Modell «Aktien %» auf "
                          f"{ui.de(s_pct, 2)} setzen – sonst werden die Titel gekürzt."), icon=":material/warning:")
        elif teil and n_ak and abs(float(ss.get("an_p_ak") or 0) - float(ss.get("an_modell_quote") or ss.get("an_p_ak") or 0)) > 0.01:
            st.warning(f"Bezug «Aktienteil»: Die Gewichte gelten für Aktien {ui.de(float(ss.get('an_p_ak') or 0), 2)} % des Kapitals. "
                       f"Das Scoring-Modell rechnet mit {ui.de(float(ss['an_modell_quote']), 2)} % – «Aktien %» anpassen, damit die "
                       "Beträge übereinstimmen.", icon=":material/warning:")
        ab_ = scoring_abgleich(ss)
        if ab_ and (abs(ab_["diff"]) >= 0.05 or ab_["ohne_kurs"]):
            st.warning(f"Abweichung zum Scoring-Modell: App {ui.de(ab_['s_ist'], 2)} % statt {ui.de(ab_['s_soll'], 2)} % des "
                       f"Aktienteils ({ui.de(ab_['diff'], 2, True)} Pp.)"
                       + (f", {len(ab_['ohne_kurs'])} Titel ohne Kurs" if ab_["ohne_kurs"] else "")
                       + " – Ursachen und Liste oben unter «Titel aus dem Scoring-Modell übernehmen».", icon=":material/difference:")
        st.caption(f"{n_ak} Titel · Summe Aktien {ui.de(summe, 0)} USD · Gewicht % (Ziel) {ui.de(s_pct, 2)} % · Ist "
                   f"{ui.de(ist_pct, 2)} % von {ui.de(ziel_pct, 1)} % "
                   f"({'Aktienteil' if teil else 'Aktienquote am Gesamtportfolio'}) · "
                   "Zeile entfernen: Häkchen in der Spalte ✕ (oder Ticker leeren) · Kurse = letzter Schlusskurs; "
                   "gekauft wird am 01.12. zum Eröffnungskurs.")
        _treasury_kennzahlen(ctx, fred, ss)
        _sektor_bereich(ctx)
        o1, o1b, o2, o3 = st.columns([1.2, 1.5, 1.1, 1])
        jahre = o1.segmented_control("Zeitraum der Kursdaten", [1, 2, 3, 5], default=1, format_func=lambda j: f"{j} J.",
                                     key=f"an_j{v}") or 1
        ss.setdefault("an_fuellen", "Index")
        fuellen = o1b.segmented_control(
            "Noch nicht verteiltes Budget rechnen als", list(FUELLEN), key="an_fuellen",
            format_func=lambda x: "Index (S&P 500 / Treasuries)" if x == "Index" else "Cash",
            help="Index: Was vom Aktien- bzw. Treasury-Budget noch offen ist, zählt als S&P 500 bzw. Treasury-ETF des Benchmarks – "
                 "so zeigen Volatilität, Beta und Drawdown das Portfolio, wie es nach dem Kauf aussähe. Cash: Offenes Budget "
                 "liegt verzinst als Cash (macht das Portfolio rechnerisch sehr ruhig, solange wenig investiert ist).") or "Index"
        fund = o2.toggle("Bewertung laden (KGV, Dividende)", value=False, key=f"an_f{v}",
                         help="Holt pro Titel Kennzahlen von Yahoo Finance – dauert einige Sekunden.")
        los = o3.button("Kennzahlen berechnen", type="primary", icon=":material/calculate:", width="stretch")
        auto_pdf = ss.pop("an_pdf_auto", None) == ss.get("an_aktiv")     # «Ganzes Portfolio als PDF» (Update 15)
        if auto_pdf:
            los = True
        if ss.pop("an_auto_rechnen", False):        # nach «Gewichte übernehmen» gleich neu rechnen (gleiche Einstellungen)
            los = True
            jahre = int((ss.get("an_auto_e") or {}).get("jahre") or jahre)
            fund = bool((ss.get("an_auto_e") or {}).get("fund", fund))
    for k in WIDGET_KEYS:          # Spiegel für den nächsten Seitenbesuch
        if k in ss:
            ss["_" + k] = ss[k]
    if los:
        kap = _num(ss.get("an_kap_txt"))
        ss.update(an_kapital=float(kap or 0), an_cash=0.0)
        ss["an_eingabe"] = dict(jahre=jahre, fund=fund, rest=True, aktien=ss["an_aktien"].copy(), treas=_treas_df(ss["an_treas"]),
                                cash=0.0, kapital=float(kap or 0), pct=dict(b["pct"]), bezug=ss.get("an_bezug", GESAMT))
    if "an_eingabe" not in ss:
        st.info("Titel eintragen und auf «Kennzahlen berechnen» klicken. Tipp: Mit «Neu → Aus dem aktuellen Bestand» siehst du "
                "sofort, wie sich ein geplanter Kauf auf Sharpe Ratio, Risiko und Regeln auswirkt; mit «Neu → Kopie» rechnest du "
                "eine Variante und stellst sie unter «Portfolios vergleichen» neben das Original.")
        return
    e = ss["an_eingabe"]
    if "aktien" not in e:     # ältere Sitzung
        e.update(aktien=ss["an_aktien"], treas=_treas_df(ss["an_treas"]), cash=float(ss["an_cash"]), kapital=float(ss["an_kapital"]))
    aktien, treas = e["aktien"], e["treas"]
    if not aktien.equals(ss["an_aktien"]) or not treas.equals(_treas_df(ss["an_treas"])):
        st.caption("Die Eingabe wurde seit der letzten Berechnung geändert – «Kennzahlen berechnen» klicken, um zu aktualisieren.")

    # --- Daten & Rechnung -------------------------------------------------------
    ticker = sorted({_txt(t).strip().upper().replace(".", "-") for t in aktien["Ticker"] if _txt(t).strip()})
    pos0 = zusammenstellen(aktien.iloc[0:0], treas, 0, 0, False, {})[0]
    proxies = sorted({PROXY[_txt(b)][0] for b in pos0["band"] if _txt(b) in PROXY})
    hist, fehlt, zeit = lade_historie(tuple(ticker + proxies + [SPX, NEUTRAL_PROXY, RF_TICKER]), int(e["jahre"]))
    if SPX not in hist:
        st.error("Kursdaten von Yahoo Finance sind gerade nicht erreichbar. Bitte in einer Minute nochmals versuchen.")
        return
    letzte = {t: float(s.iloc[-1]) for t, s in hist.items()}
    pos, cash, total, hinweise = zusammenstellen(aktien, treas, e["cash"], e["kapital"], e["rest"], letzte)
    for h in hinweise:
        st.warning(h)
    if total <= 0 or not len(pos):
        st.warning("Bitte mindestens eine Aktie oder einen Treasury-Betrag eingeben.")
        return
    fuell = offenes_budget(pos, cash, e["kapital"], e.get("pct")) if fuellen == "Index" else {}
    fehl_f = [t for t in fuell if t not in hist]
    if fehl_f:          # Platzhalter-ETF nicht geladen (z. B. Treasury-ETF des Benchmarks nicht im Abruf)
        hist = {**hist, **lade_historie(tuple(fehl_f), int(e["jahre"]))[0]}
        fuell = {t: w for t, w in fuell.items() if t in hist}
    erg = rechne(pos, cash, total, hist, ctx.alloc["neutral"], fuell)
    if erg is None:
        st.error("Zu wenige gemeinsame Kursdaten – Zeitraum vergrössern oder Ticker prüfen.")
        return
    info = {t: P.ticker_info(t) for t in pos.loc[pos["klasse"] == "Aktie", "ticker"]}
    kv, kn, ks = erg["k"]["Vorauswahl"], erg["k"]["Neutral (IPS)"], erg["k"]["S&P 500"]
    ka = erg["k"].get("Aktienteil", {})
    if erg["start"] is not None and erg["start"] > hist[SPX].index.min() + pd.Timedelta(days=10):
        st.caption(f"Gemeinsames Zeitfenster ab {erg['start']:%d.%m.%Y} (ein Titel hat eine kürzere Kursgeschichte).")

    ak_w = float(pos.loc[pos["klasse"] == "Aktie", "wert"].sum())
    tr_w = float(pos.loc[pos["klasse"] == "Treasury", "wert"].sum())
    f_a, f_t = erg["fuell"].get(SPX, 0.0), erg["fuell"].get(NEUTRAL_PROXY, 0.0) if NEUTRAL_PROXY != SPX else 0.0
    cash_eff = max(0.0, cash - f_a - f_t)
    if erg["fuell"]:
        st.info(f"Noch nicht verteilt und als Index gerechnet: "
                + " · ".join(x for x in (f"{ui.de(f_a, 0)} USD Aktienbudget → S&P 500 ({SPX})" if f_a else "",
                                         f"{ui.de(f_t, 0)} USD Treasury-Budget → {NEUTRAL_PROXY}" if f_t else "") if x)
                + ". Die Kennzahlen zeigen das Portfolio, wie es nach dem Kauf aussähe. Die Titel allein: Spalte «Aktienteil».",
                icon=":material/pie_chart:")
    elif cash / total > 0.15 + float((e.get("pct") or {}).get("cash", 0)) / 100:
        st.info(f"{ui.de(cash / total * 100, 0)} % des Kapitals sind noch nicht investiert und zählen als Cash – darum sind "
                "Volatilität, Beta und Drawdown der Vorauswahl so tief. Für die Titel allein siehe «Aktienteil», oder oben "
                "«Noch nicht verteiltes Budget rechnen als: Index» wählen.", icon=":material/info:")
    # --- PDF der Analyse (Update 13): sammelt unten alle Zahlen, Knopf oben ----------------------------------
    import seitenpdf as SP

    ph_pdf = st.empty()
    R = _analyse_sammler(ctx, e, kv, total, cash, ak_w, tr_w, f_a, f_t, cash_eff, zeit, erg)
    if erg["fuell"]:
        R.text(f"Noch nicht verteilt und als Index gerechnet: {ui.de(f_a, 0)} USD Aktienbudget → S&P 500 ({SPX}), "
               f"{ui.de(f_t, 0)} USD Treasury-Budget → {NEUTRAL_PROXY}.", klein=True)
    kb = erg["k"].get(BENCH, {})
    ips_ = ctx.ips
    as_lo, as_hi = float(ips_.get("active_share_min") or 0), float(ips_.get("active_share_max") or 100)

    def _ampel(wert, ok: bool, txt=None):
        if wert is None:
            return "–"
        return f"<span style='color:{ui.FARBE['Grün'] if ok else ui.FARBE['Rot']}'>{txt or wert}</span>"
    as_a = ka.get("active_share")
    kpi1 = [
        ("Sharpe Ratio", _zahl(kv.get("sharpe")), f"Benchmark {_zahl(kb.get('sharpe'))} · Aktienteil {_zahl(ka.get('sharpe'))} · "
                                                   f"neutral {_zahl(kn.get('sharpe'))} · S&P 500 {_zahl(ks.get('sharpe'))}"),
        ("Rendite p.a.", _pct(kv.get("rendite"), 1, True), f"Benchmark {_pct(kb.get('rendite'), 1, True)} → "
                                                            f"{_pp(kv.get('mehr_bench'))} · "
                                                            f"Aktienteil {_pct(ka.get('rendite'), 1, True)}"),
        ("Volatilität p.a.", _ampel(_pct(kv.get("vola")), (kv.get("vola") or 0) <= float(ips_.get("vola_max") or 99)),
         f"IPS ≤ {ui.de(ips_.get('vola_max'), 0)} % · Benchmark {_pct(kb.get('vola'))} · Aktienteil {_pct(ka.get('vola'))}"),
        ("Max. Drawdown", _pct(kv.get("mdd")), f"Benchmark {_pct(kb.get('mdd'))} · neutral {_pct(kn.get('mdd'))} · "
                                               f"IPS tragbar −{ui.de(ips_.get('verlust_max'), 0)} %"),
    ]
    ui.kopf_kpis(kpi1)
    kpi2 = [
        ("Beta zum S&P 500", _zahl(kv.get("beta")), f"Aktienteil {_zahl(ka.get('beta'))} (IPS {ui.de(ips_.get('beta_min'), 2)}–"
                                                     f"{ui.de(ips_.get('beta_max'), 2)}) · Benchmark {_zahl(kb.get('beta'))}"),
        ("Tracking Error vs. Benchmark", _ampel(_pct(kv.get("te_bench")), (kv.get("te_bench") or 0) <= float(ips_.get("te_max") or 99)),
         f"IPS ≤ {ui.de(ips_.get('te_max'), 1)} % · IR {_zahl(kv.get('ir_bench'))} · vs. neutral {_pct(kv.get('te_neutral'))}"),
        ("Active Share (Aktien)", _ampel(_pct(as_a), as_a is not None and as_lo <= as_a <= as_hi),
         f"IPS {ui.de(as_lo, 0)}–{ui.de(as_hi, 0)} % · gesamt vs. 50/50 {_pct(kv.get('active_share_ges'))}"
         + (f" · inkl. offenes Budget {_pct(kv.get('active_share'))}" if erg["fuell"].get(SPX) else "")),
        ("VaR 95 % · 1 Tag", f"{ui.de(kv.get('var95', 0) / 100 * total, 0)} USD",
         f"{_pct(kv.get('var95'), 2)} · 10 Tage ≈ {ui.de(kv.get('var95', 0) / 100 * total * math.sqrt(10), 0)} USD"),
        ("Aufteilung", f"{(ak_w + f_a) / total * 100:.0f} / {(tr_w + f_t) / total * 100:.0f} / {cash_eff / total * 100:.0f} %",
         f"Aktien / Treasuries / Cash · Benchmark 50 / 50 / 0" + (f" (investiert {ak_w / total * 100:.0f} / {tr_w / total * 100:.0f} %)" if erg["fuell"] else "")
         + f" · {int((pos['klasse'] == 'Aktie').sum())} Titel, effektiv {ui.de(eff_anzahl(pos.loc[pos['klasse'] == 'Aktie', 'wert']), 1)}"),
    ]
    ui.kopf_kpis(kpi2)
    R.h("Kennzahlen auf einen Blick")
    R.kpis(kpi1 + kpi2)

    ss["an_kz"] = dict(hash=_hash(_kern(aktien, treas, e["kapital"], e.get("pct") or {}, e.get("bezug", GESAMT))),
                       kz=_kz_zusammenfassen(kv, ka, ak_w, tr_w, cash, total, fuellen))
    t1, t_bm, t_ips, t2, t3, t4, t5 = st.tabs(["Vergleich", "Benchmark & Active Share", "IPS-Abgleich", "Einzeltitel",
                                               "Korrelation", "Regeln & Struktur", "Gewichte optimieren"])
    with t1:
        k = dict(erg["k"])
        akt = portfolio_vorlage(tab, fred, lade_kurse)
        if akt is not None:
            letzte2 = dict(letzte)
            hist2 = hist
            tick2 = sorted({_txt(t).strip().upper() for t in akt[0]["Ticker"] if _txt(t).strip()} - set(hist))
            prox2 = sorted({PROXY[_txt(b)][0] for b in akt[1]["Laufzeit"] if _txt(b) in PROXY} - set(hist))
            if tick2 or prox2:
                hist2 = {**hist, **lade_historie(tuple(tick2 + prox2), int(e["jahre"]))[0]}
                letzte2 = {t: float(s.iloc[-1]) for t, s in hist2.items()}
            p2, c2_, tot2, _ = zusammenstellen(akt[0], akt[1], akt[2], akt[3], False, letzte2)
            e2 = rechne(p2, c2_, tot2, hist2, ctx.alloc["neutral"]) if tot2 > 0 else None
            if e2:
                k = {n_: k[n_] for n_ in ("Vorauswahl", "Aktienteil") if n_ in k} | {"Aktuelles Portfolio": e2["k"]["Vorauswahl"]} | \
                    {n_: k[n_] for n_ in (BENCH, "Neutral (IPS)", "S&P 500") if n_ in k}
        vgl_df = vergleichstabelle(k)
        st.dataframe(vgl_df, hide_index=True, width="stretch",
                     column_config={"Bedeutung": st.column_config.TextColumn(width="large")})
        R.h("Kennzahlen im Vergleich")
        R.text("Vorauswahl = ganzes Portfolio (Aktien, Treasuries, Cash" + (", offenes Budget als Index" if erg["fuell"] else "")
               + ") · Aktienteil = nur eure Titel, auf 100 % hochgerechnet · Benchmark = Strategie des Dozenten 50 % S&P 500 / 50 % "
               f"Treasuries ({NEUTRAL_PROXY}) · Neutral = strategische Allokation laut IPS.", klein=True)
        R.tabelle(vgl_df, breiten=[34] + [22] * (len(vgl_df.columns) - 2) + [80])
        R.linien([(n_, (1 + rr).cumprod() * 100, SP.rgb(fb)) for n_, rr, fb in
                  (("Vorauswahl", erg["r_p"], ui.BLAU), ("Aktienteil", erg["r_a"], ui.PETROL), (BENCH, erg["r_b"], BENCH_FARBE),
                   ("Neutral (IPS)", erg["r_n"], ui.GOLD), ("S&P 500", erg["r_s"], ui.STAHL)) if rr is not None],
                 titel="Wertentwicklung (Start = 100)")
        R.linien([(n_, ((1 + rr).cumprod() / (1 + rr).cumprod().cummax() - 1) * 100, SP.rgb(fb)) for n_, rr, fb in
                  (("Vorauswahl", erg["r_p"], ui.BLAU), (BENCH, erg["r_b"], BENCH_FARBE), ("Neutral (IPS)", erg["r_n"], ui.GOLD))],
                 einheit=" %", titel="Drawdown (Verlust vom Höchststand)")
        st.caption("Vorauswahl = ganzes Portfolio (Aktien, Treasuries, Cash" + (", offenes Budget als Index" if erg["fuell"] else "")
                   + ") · Aktienteil = nur eure Titel, auf 100 % hochgerechnet (so schwankt der Aktienteil für sich allein).")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Wertentwicklung (Start = 100)**")
            fig = go.Figure()
            for name, rr, farbe, breite in (("Vorauswahl", erg["r_p"], ui.BLAU, 2.4), ("Aktienteil", erg["r_a"], ui.BLAU, 1.2),
                                            (BENCH, erg["r_b"], BENCH_FARBE, 1.8),
                                            ("Neutral (IPS)", erg["r_n"], ui.GOLD, 1.6), ("S&P 500", erg["r_s"], ui.STAHL, 1.4)):
                if rr is None:
                    continue
                w = (1 + rr).cumprod() * 100
                fig.add_trace(go.Scatter(x=w.index, y=w, name=name,
                                         line=dict(color=farbe, width=breite, dash="dot" if name == "Aktienteil" else None)))
            fig.update_layout(height=320, margin=dict(l=8, r=8, t=10, b=8), hovermode="x unified")
            ui.zeige(fig)
        with c2:
            st.markdown("**Drawdown (Verlust vom Höchststand)**")
            fig = go.Figure()
            for name, rr, farbe in (("Vorauswahl", erg["r_p"], ui.BLAU), (BENCH, erg["r_b"], BENCH_FARBE),
                                    ("Neutral (IPS)", erg["r_n"], ui.GOLD)):
                w = (1 + rr).cumprod()
                dd = (w / w.cummax() - 1) * 100
                fig.add_trace(go.Scatter(x=dd.index, y=dd, name=name, line=dict(color=farbe, width=1.6),
                                         fill="tozeroy" if name == "Vorauswahl" else None,
                                         fillcolor="rgba(31,78,140,0.10)"))
            fig.update_layout(height=320, margin=dict(l=8, r=8, t=10, b=8), hovermode="x unified")
            fig.update_yaxes(ticksuffix=" %")
            ui.zeige(fig)
        st.caption(f"Zeitraum {kv['von']:%d.%m.%Y} – {kv['bis']:%d.%m.%Y} ({kv['tage']} Handelstage) · risikofreier Zins "
                   f"Ø {ui.de(kv['rf'], 2)} % (13-Wochen-T-Bill) · Cash verzinst mit SOFR − 0,5 % wie in der Simulation · dividendenbereinigte Kurse, konstante Gewichte · "
                   f"Benchmark = 50 % S&P 500 (SPY) / 50 % Treasuries ({NEUTRAL_PROXY}) · "
                   f"Neutral = {ctx.alloc['neutral']['aktien']:.0f} % S&P 500 / "
                   f"{100 - ctx.alloc['neutral']['aktien'] - ctx.alloc['neutral'].get('cash', 5):.0f} % Treasuries ({NEUTRAL_PROXY}) / "
                   f"{ctx.alloc['neutral'].get('cash', 5):.0f} % Cash. Vergangene Werte sind keine Prognose. Kurse {zeit}.")

    with t_bm:
        _bereich_benchmark(ctx, erg, pos, info, total, cash, ak_w, tr_w, f_a, f_t, cash_eff, R)

    with t_ips:
        import ips_abgleich as IA

        dur_t = None
        try:                  # Duration aus den einzelnen Anleihen (genauer als die ETF-Näherung), sonst ETF-Näherung
            import anleihen as AN

            k_ = AN.kurve(fred)
            df_a = AN.anleihen_tabelle(_treas_df(e["treas"]), k_, ctx.szdef, ctx.szen) if len(k_) >= 5 else None
            if df_a is not None and len(df_a) and f_t <= 0.5:
                dur_t = float(AN.mix_anleihen(df_a, ctx.szdef)["duration"])
        except Exception:  # noqa: BLE001
            dur_t = None
        m_ips = IA.messwerte_portfolio(ctx, erg, pos, total, cash, info, f_a, f_t, duration=dur_t)
        R.neue_seite()
        IA.anzeigen(IA.gap_analyse(ctx.ips, ctx.alloc, m_ips), R=R,
                    erklaerung="Gemessen am Analyseportfolio" + (" (offenes Budget als Index gerechnet)" if erg["fuell"] else "") + ".")

    with t2:
        fd = lade_fundamentaldaten(list(info)) if e["fund"] else {}
        in_pos = set(pos["ticker"])
        fuell_name = {SPX: "Offenes Aktienbudget (S&P 500)", NEUTRAL_PROXY: f"Offenes Treasury-Budget ({NEUTRAL_PROXY})"}

        def _label(t):
            if t in erg["fuell"]:
                return (PROXY_NAME.get(t, t) + " inkl. offenes Budget") if t in in_pos else fuell_name.get(t, t)
            return PROXY_NAME.get(t, t)

        zeilen = []
        for _, z in pos.iterrows():
            t = z["ticker"]
            ke = erg["einzel"].get(t, {})
            i = info.get(t, {})
            f = fd.get(t, {})
            ist_ak = z["klasse"] == "Aktie"
            zeilen.append({
                "Titel": t if ist_ak else f"Treasuries {z['band']} ({t})",
                "Name": i.get("name", "") if ist_ak else ("ETF-Näherung" + (" inkl. offenes Budget" if t in erg["fuell"] else "")),
                "Sektor": i.get("sektor", "") if ist_ak else "Treasury",
                "Menge": ui.de(z["menge"], 0) if ist_ak else "–",
                "Kurs": ui.de(z["kurs"], 2) if ist_ak else "–",
                "Wert USD": ui.de(erg["gew"].get(t, 0) * total, 0), "Gewicht": _pct(erg["gew"].get(t, 0) * 100),
                "Gewicht im Aktienteil": _pct(erg["gew_a"][t] * 100) if t in erg["gew_a"] else "–",
                "Risikobeitrag": _pct(erg["beitrag"].get(t)),
                "Risikobeitrag im Aktienteil": _pct(erg["beitrag_a"].get(t)) if t in erg["gew_a"] else "–",
                "Rendite p.a.": _pct(ke.get("rendite"), 1, True), "Vola p.a.": _pct(ke.get("vola")),
                "Sharpe": _zahl(ke.get("sharpe")), "Beta": _zahl(ke.get("beta")), "Max. DD": _pct(ke.get("mdd")),
                "Korr. zum Rest": _zahl(ke.get("korr_rest")),
                **({"KGV fwd": _zahl(f.get("kgv_fwd"), 1), "Div.-Rendite": _pct((f.get("div") or 0) * 100 if f.get("div") is not None else None, 2)}
                   if e["fund"] else {})})
        for t, w in erg["fuell"].items():          # Platzhalter für offenes Budget
            if t in in_pos:
                continue
            ke = erg["einzel"].get(t, {})
            zeilen.append({"Titel": fuell_name.get(t, t), "Name": "Platzhalter, noch nicht verteilt", "Sektor": "–", "Menge": "–",
                           "Kurs": "–", "Wert USD": ui.de(w, 0), "Gewicht": _pct(w / total * 100), "Gewicht im Aktienteil": "–",
                           "Risikobeitrag": _pct(erg["beitrag"].get(t)), "Risikobeitrag im Aktienteil": "–",
                           "Rendite p.a.": _pct(ke.get("rendite"), 1, True), "Vola p.a.": _pct(ke.get("vola")),
                           "Sharpe": _zahl(ke.get("sharpe")), "Beta": _zahl(ke.get("beta")), "Max. DD": _pct(ke.get("mdd")),
                           "Korr. zum Rest": _zahl(ke.get("korr_rest")),
                           **({"KGV fwd": "–", "Div.-Rendite": "–"} if e["fund"] else {})})
        st.dataframe(pd.DataFrame(zeilen), hide_index=True, width="stretch")
        R.neue_seite()
        R.h(f"Einzeltitel ({int((pos['klasse'] == 'Aktie').sum())} Aktien, Treasuries als ETF-Näherung)")
        ez = pd.DataFrame(zeilen).drop(columns=["Risikobeitrag im Aktienteil"], errors="ignore")
        R.tabelle(ez, max_zeilen=400, breiten=([20, 40, 20] + [13] * (len(ez.columns) - 3)) if len(ez.columns) > 3 else None)
        R.text("Gewicht/Risikobeitrag = Anteil am ganzen Portfolio; «im Aktienteil» = nur unter euren Titeln. Liegt der Risikobeitrag "
               "deutlich über dem Gewicht, treibt der Titel das Risiko überproportional. Korr. zum Rest = Korrelation mit dem übrigen "
               "Portfolio.", klein=True)
        if len(erg["beitrag"]):
            top_ = erg["beitrag"].sort_values(ascending=False).head(15)
            R.balken([(_label(t), float(v), SP.rgb(ui.BLAU)) for t, v in top_.items()], einheit=" %",
                     titel="Grösste Risikobeiträge (Anteil am Portfoliorisiko)")
        st.caption("Vola p.a. = Schwankung des einzelnen Titels. Gewicht/Risikobeitrag = Anteil am ganzen Portfolio, «im Aktienteil» = "
                   "nur unter euren Titeln. Liegt der Risikobeitrag deutlich über dem Gewicht, treibt der Titel das Risiko "
                   "überproportional. Korr. zum Rest = Korrelation mit dem übrigen Portfolio (tief = guter Diversifikationsbeitrag).")
        b = erg["beitrag"]
        if len(b):
            g = pd.Series(erg["gew"]) * 100
            ordnung = b.sort_values(ascending=True).index
            lab = [_label(t) for t in ordnung]
            fig = go.Figure([go.Bar(y=lab, x=g.reindex(ordnung), name="Gewicht", orientation="h", marker_color=ui.STAHL),
                             go.Bar(y=lab, x=b.reindex(ordnung), name="Risikobeitrag", orientation="h",
                                    marker_color=ui.BLAU)])
            fig.update_layout(barmode="group", height=max(260, 34 * len(b) + 80), margin=dict(l=8, r=8, t=10, b=8),
                              hovermode="y unified")
            fig.update_xaxes(ticksuffix=" %")
            ui.zeige(fig)

    with t3:
        spalten = list(dict.fromkeys([t for t in pos["ticker"] if t in erg["R"].columns] + [SPX]))
        if len(spalten) >= 3:
            kor = erg["R"][spalten].corr()
            beschr = ["S&P 500" if s == SPX else PROXY_NAME.get(s, s) for s in spalten]
            fig = go.Figure(go.Heatmap(z=kor.values, x=beschr, y=beschr, zmin=-1, zmax=1,
                                       colorscale=[[0, "#B93A32"], [0.5, "#FFFFFF"], [1, ui.BLAU]],
                                       text=[[ui.de(v, 2) for v in zeile] for zeile in kor.values], texttemplate="%{text}",
                                       hovertemplate="%{y} / %{x}: %{text}<extra></extra>", colorbar=dict(thickness=10)))
            fig.update_layout(height=max(320, 30 * len(spalten) + 120), margin=dict(l=8, r=8, t=10, b=8))
            fig.update_yaxes(autorange="reversed")
            ui.zeige(fig)
            oben = kor.where(np.triu(np.ones(kor.shape, dtype=bool), 1)).stack().dropna().sort_values(ascending=False)
            oben = oben[[a != SPX and b_ != SPX for a, b_ in oben.index]]
            R.h("Korrelation")
            if len(oben):
                R.text(f"Durchschnittliche Korrelation zwischen den Positionen: {ui.de(float(oben.mean()), 2)} "
                       f"({len(oben)} Paare, ohne S&P 500).")
                paare = pd.concat([oben.head(10), oben.tail(5)])
                R.tabelle(pd.DataFrame({"Paar": [f"{a} / {b_}" for a, b_ in paare.index],
                                        "Korrelation": [ui.de(v, 2) for v in paare.values],
                                        "": ["stärkste"] * min(10, len(oben)) + ["schwächste"] * (len(paare) - min(10, len(oben)))}),
                          breiten=[60, 25, 25])
            if len(spalten) <= 16:
                R.h2("Korrelationsmatrix (Tagesrenditen)")
                R.tabelle(pd.DataFrame([[b_] + [ui.de(v, 2) for v in zeile] for b_, zeile in zip(beschr, kor.values)],
                                       columns=["", *[c[:10] for c in beschr]]))
            if len(oben):
                st.caption("Am stärksten miteinander verbunden: " + ", ".join(
                    f"{a}/{b_} {ui.de(v, 2)}" for (a, b_), v in oben.head(3).items()) +
                           " · am wenigsten: " + ", ".join(f"{a}/{b_} {ui.de(v, 2)}" for (a, b_), v in oben.tail(3).items()))
        else:
            st.caption("Für eine Korrelationsmatrix mindestens zwei Positionen eingeben.")

    with t4:
        import sektoren as SK

        ak_pos = pos[pos["klasse"] == "Aktie"]
        ak_df = pd.DataFrame({"ticker": ak_pos["ticker"].tolist(),
                              "name": [info.get(t, {}).get("name", "") for t in ak_pos["ticker"]],
                              "sektor": [info.get(t, {}).get("sektor") or "Unbekannt" for t in ak_pos["ticker"]],
                              "wert": ak_pos["wert"].astype(float).tolist()})
        sa = SK.lade_allokation(ctx.tabellen.get("sektor_allokation"))["sektoren"]
        sek_p = SK.pruefe_portfolio(ak_df, total, sa, ctx.sek_index["gewichte"], float(ctx.alloc["aktien"]))
        n_aus = sek_p["n"][L.ROT] + sek_p["n"][L.GELB]
        checks = pruefungen(pos, cash, total, info, ctx.alloc) + [
            (sek_p["quote_stufe"], f"Aktienquote {ui.de(sek_p['quote'], 1)} % – Soll {ui.de(float(ctx.alloc['aktien']), 1)} % ±2 Pp."),
            (L.ROT if sek_p["n"][L.ROT] else L.GELB if sek_p["n"][L.GELB] else L.GRUEN,
             f"Sektorallokation: {sek_p['n'][L.GRUEN]} von 11 Sektoren eingehalten" + (f", {n_aus} ausserhalb – Details unten" if n_aus else ""))]
        if ka.get("active_share") is not None:
            lo_, hi_ = float(ctx.ips.get("active_share_min") or 0), float(ctx.ips.get("active_share_max") or 100)
            checks.append((L.GRUEN if lo_ <= ka["active_share"] <= hi_ else L.ROT,
                           f"Active Share Aktien {ui.de(ka['active_share'], 1)} % – IPS {ui.de(lo_, 0)}–{ui.de(hi_, 0)} % "
                           "(Details: Reiter «Benchmark & Active Share»)"))
        if kv.get("te_bench") is not None:
            checks.append((L.GRUEN if kv["te_bench"] <= float(ctx.ips.get("te_max") or 99) else L.GELB,
                           f"Tracking Error vs. Benchmark {ui.de(kv['te_bench'], 2)} % (historisch) – IPS höchstens "
                           f"{ui.de(ctx.ips.get('te_max'), 1)} %"))
        n_rot = sum(1 for s_, _ in checks if s_ == L.ROT)
        st.markdown(f"**Prüfung gegen eure Regeln** · {ui.badge(L.ROT if n_rot else L.GRUEN, f'{n_rot} Verstösse' if n_rot else 'alles im Rahmen')}",
                    unsafe_allow_html=True)
        st.markdown("".join(f'<div class="karte-zeile"><span>{ui.badge(s_, {L.ROT: "Verstoss", L.GELB: "Hinweis", L.GRUEN: "ok"}[s_])}'
                            f'&nbsp; {txt}</span><span></span></div>' for s_, txt in checks), unsafe_allow_html=True)
        st.markdown("")
        R.neue_seite()
        R.h(f"Regeln & Struktur · {n_rot} Verstösse" if n_rot else "Regeln & Struktur · alles im Rahmen")
        R.tabelle(pd.DataFrame({"Status": [{L.ROT: "Verstoss", L.GELB: "Hinweis", L.GRUEN: "ok"}[s_] for s_, _ in checks],
                                "Prüfung": [t_ for _, t_ in checks]}), breiten=[25, 240],
                  farben=[[_HELL_RGB.get(s_), None] for s_, _ in checks])
        R.h2("Anlageklassen vs. Taktik")
        R.tabelle(pd.DataFrame({"Klasse": ["Aktien", "Treasuries", "Cash"],
                                "Vorauswahl": [_pct(ak_w / total * 100), _pct(tr_w / total * 100), _pct(cash / total * 100)],
                                "inkl. offenes Budget": [_pct((ak_w + f_a) / total * 100), _pct((tr_w + f_t) / total * 100),
                                                         _pct(cash_eff / total * 100)],
                                "Taktik": [_pct(ctx.alloc["aktien"]), _pct(ctx.alloc["anleihen"]), _pct(ctx.alloc["cash"])],
                                "IPS-Band": [f"{lo:g}–{hi:g} %" for lo, hi in (ctx.alloc["baender"]["aktien"],
                                                                              ctx.alloc["baender"]["treasuries"],
                                                                              ctx.alloc["baender"]["cash"])]}),
                  breiten=[40, 40, 45, 40, 40])
        sek_tab = sek_p["sektoren"]
        R.h2(f"Sektorallokation · {sek_p['n'][L.GRUEN]} von 11 eingehalten · Aktienquote {ui.de(sek_p['quote'], 1)} %")
        spalten_s = ["Sektor", "Richtung", "Abw. geplant", "Index", "Ziel", "Ist", "Aktiv vs. Index", "Ist − Ziel",
                     "Fehlt (+) / zu viel (−)", "Anzahl Titel"]
        R.tabelle(sek_tab[[c_ for c_ in spalten_s if c_ in sek_tab.columns]],
                  farben=[[_HELL_RGB.get(s_)] + [None] * (len(spalten_s) - 1) for s_ in sek_tab["_stufe"]])
        SK.zeige_pruefung(ctx, ak_df, total, "Sektorallokation im Probe-Portfolio", key="an")
        c2, _ = st.columns(2)
        with c2:
            st.markdown("**Anlageklassen vs. Taktik**")
            ist = [ak_w / total * 100, tr_w / total * 100, cash / total * 100]
            soll = [ctx.alloc["aktien"], ctx.alloc["anleihen"], ctx.alloc["cash"]]
            fig = go.Figure([go.Bar(name="Vorauswahl", x=["Aktien", "Treasuries", "Cash"], y=ist, marker_color=ui.BLAU,
                                    text=[f"{x:.0f} %" for x in ist], textposition="outside"),
                             go.Bar(name="Taktik", x=["Aktien", "Treasuries", "Cash"], y=soll, marker_color=ui.STAHL,
                                    text=[f"{x:.0f} %" for x in soll], textposition="outside")])
            fig.update_layout(barmode="group", height=300, margin=dict(l=8, r=8, t=10, b=8))
            fig.update_yaxes(ticksuffix=" %")
            ui.zeige(fig)

    with t5:
        _gewichte_bereich(ctx, erg, pos, info, total, letzte, R, (tab, kapital_std, ctx.alloc))

    _anleihen_pdf(R, ctx, fred, e, cash_eff)
    R.h("Methodik")
    R.text(f"Zeitraum {kv['von']:%d.%m.%Y} – {kv['bis']:%d.%m.%Y} ({kv['tage']} Handelstage), Kurse {zeit}. Dividendenbereinigte "
           "Tageskurse (Yahoo Finance), konstante Gewichte, annualisiert mit 252 Handelstagen. Risikofreier Zins: 13-Wochen-T-Bill "
           f"(Ø {ui.de(kv['rf'], 2)} %). Cash verzinst wie in der Simulation mit SOFR − 0,5 %. Treasuries in den Kennzahlen über ETFs je "
           "Laufzeitband (Näherung); Anleihen-Teil mit den einzelnen Anleihen auf der FRED-Zinskurve. Vergangene Werte sind keine "
           "Prognose.", klein=True)
    pf_name = _wert("an_pf_name", "") or (ss.get("an_pf", {}).get(ss.get("an_aktiv"), {}) or {}).get("name", "Analyse")
    dname = f"Analyse_{re.sub(r'[^A-Za-z0-9]+', '_', str(pf_name)).strip('_') or 'Portfolio'}_{datetime.now():%Y-%m-%d}.pdf"
    if auto_pdf:
        try:
            ss["pdfdata_analyse_pdf"] = R.pdf()
            ss.update(an_pdf_pid=ss.get("an_aktiv"), an_pdf_name=dname)
            ph_dl.download_button("PDF herunterladen", ss["pdfdata_analyse_pdf"], dname, "application/pdf", icon=":material/download:",
                                  key="an_dl_pdf_neu", width="stretch", type="primary")
        except Exception as ex:  # noqa: BLE001
            ph_dl.error(f"PDF konnte nicht erstellt werden: {ex}")
    SP.knopf(ph_pdf, R, "analyse_pdf", dateiname=dname)


# ---------------------------------------------------------------------------
# Download-Leiste oben in «Portfolio bearbeiten & analysieren» (Update 15, in Update 16 enthalten)
# ---------------------------------------------------------------------------
def _excel_export(ctx, fred: dict) -> bytes:
    """Positionen des aktiven Analyseportfolios als Excel (übernommener Stand, ohne Yahoo-Abruf): Übersicht, Aktien
    (inkl. Benchmark-Gewicht und Aktiv), Treasuries (alle Kennzahlen je Anleihe)."""
    import io

    ss = st.session_state
    b = budget(ss)
    kap = b["kapital"] or 0.0
    ak = _aktien_df(ss.get("an_aktien"), 0)
    ak = ak[[bool(_txt(t).strip()) for t in ak["Ticker"]]]
    betr = [_num(x) or 0.0 for x in ak["Betrag USD"]]
    summe_a = sum(betr)
    try:
        import benchmark as BM

        bm = BM.gewichte() * 100
        werte = {BM.norm_ticker(t): v for t, v in zip(ak["Ticker"], betr)}
        as_ = BM.active_share(werte) if werte else None
    except Exception:  # noqa: BLE001
        bm, as_ = pd.Series(dtype=float), None
    aktien = pd.DataFrame({
        "Ticker": ak["Ticker"].values, "Name": ak["Name"].values, "Sektor": ak["Sektor"].values,
        "Kurs USD": [_num(x) for x in ak["Kurs USD"]], "Stück": [_num(x) for x in ak["Menge"]], "Betrag USD": betr,
        "Gewicht-Vorgabe %": [_num(x) for x in ak["Gewicht %"]], "Fix": ak["Fix"].values,
        "% Gesamt": [v / kap * 100 if kap else None for v in betr],
        "% Aktienteil": [v / summe_a * 100 if summe_a else None for v in betr],
        "% Benchmark (S&P 500, gültig)": [float(bm.get(str(t).upper().replace(".", "-"), 0.0)) for t in ak["Ticker"]]})
    aktien["Aktiv vs. Benchmark Pp."] = aktien["% Aktienteil"].fillna(0) - aktien["% Benchmark (S&P 500, gültig)"]
    tr_df = pd.DataFrame()
    kz = None
    try:
        import anleihen as AN

        k = AN.kurve(fred)
        if len(k) >= 5:
            tr_df = AN.anleihen_tabelle(_treas_df(ss.get("an_treas", _leer_treas())), k, ctx.szdef, ctx.szen)
            kz = AN.kennzahlen_kurz(tr_df)
    except Exception:  # noqa: BLE001
        pass
    if len(tr_df):
        tr_out = pd.DataFrame({
            "Bezeichnung": tr_df["name"], "ISIN": tr_df["isin"], "Laufzeitband": tr_df["band"], "Kupon %": tr_df["kupon"],
            "Fälligkeit": tr_df["faellig"].map(lambda d: d.date() if pd.notna(d) else None), "Restlaufzeit J.": tr_df["T"],
            "Betrag USD": tr_df["betrag"], "Nominal USD": tr_df["nominal"], "Gewicht %": tr_df["gewicht"], "Kurs %": tr_df["kurs"],
            "Rendite %": tr_df["y"], "Spread bp": tr_df["spread"], "Mod. Duration": tr_df["D"], "Konvexität": tr_df["konv"],
            "DV01 USD": tr_df["dv01"], "Kupons bis 28.02. USD": tr_df["kupons_sim"], "Kupontermine": tr_df["kupon_daten"],
            "Marchzins USD": tr_df["marchzins"]})
    else:
        t0 = _treas_df(ss.get("an_treas", _leer_treas()))
        tr_out = t0[[bool(_txt(x).strip()) or bool(_num(y)) for x, y in zip(t0["Laufzeit"], t0["Betrag USD"])]]
    inv_t = b["inv_t"]
    ueb = [("Analyseportfolio", str(_wert("an_pf_name", "") or "")), ("Stand", datetime.now().strftime("%d.%m.%Y %H:%M")),
           ("Kapital USD", kap), ("Aktien % (geplant)", b["pct"]["aktien"]), ("Treasuries % (geplant)", b["pct"]["treas"]),
           ("Cash % (geplant)", b["pct"]["cash"]), ("Aktien investiert USD", summe_a), ("Treasuries investiert USD", inv_t),
           ("Cash (Rest) USD", kap - summe_a - inv_t), ("Anzahl Aktien", len(aktien)),
           ("Benchmark", "50 % S&P 500 / 50 % US-Treasuries (Dozent)"),
           ("Active Share Aktien %", round(as_["active_share"], 2) if as_ else None)]
    if kz:
        ueb += [("Treasuries: Ø Kupon %", kz["kupon"]), ("Treasuries: Ø Rendite %", kz["rendite"]),
                ("Treasuries: Mod. Duration", kz["duration"]), ("Treasuries: Konvexität", kz["konv"]),
                ("Treasuries: Ø Restlaufzeit J.", kz["restlaufzeit"]), ("Treasuries: Kupons bis 28.02. USD", kz["kupons"]),
                ("Treasuries: Marchzins USD", kz["marchzins"]), ("Treasuries: DV01 USD", kz["dv01"])]
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as xw:
        pd.DataFrame(ueb, columns=["Grösse", "Wert"]).to_excel(xw, sheet_name="Übersicht", index=False)
        aktien.to_excel(xw, sheet_name="Aktien", index=False)
        tr_out.to_excel(xw, sheet_name="Treasuries", index=False)
        for ws in xw.book.worksheets:
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(45, max(10, max(len(str(c.value or "")) for c in col) + 2))
    return bio.getvalue()


def _download_leiste(ctx, fred: dict):
    """Ganz oben: «Ganzes Portfolio als PDF» (rechnet und baut das Analyse-PDF in einem Lauf) und «Positionen als Excel».
    Gibt den Platzhalter für «PDF herunterladen» zurück (wird am Ende des Laufs gefüllt)."""
    ss = st.session_state
    pid = ss.get("an_aktiv")
    with st.container(border=True):
        c0, c1, c2, c3 = st.columns([1.5, 1.3, 1.2, 1.3])
        c0.markdown("**Herunterladen** · ganzes Analyseportfolio")
        c1.button("Ganzes Portfolio als PDF", icon=":material/picture_as_pdf:", key="an_dl_pdf", width="stretch",
                  on_click=lambda: ss.__setitem__("an_pdf_auto", pid),
                  help="Rechnet die Kennzahlen (Zeitraum und Optionen wie unten eingestellt, übernommener Stand) und erstellt das "
                       "PDF mit allen Reitern – auch ohne vorher «Kennzahlen berechnen» zu klicken.")
        ph = c2.empty()
        if ss.get("pdfdata_analyse_pdf") and ss.get("an_pdf_pid") == pid and ss.get("an_pdf_auto") != pid:
            ph.download_button("PDF herunterladen", ss["pdfdata_analyse_pdf"], ss.get("an_pdf_name") or "Analyse.pdf", "application/pdf",
                               icon=":material/download:", key="an_dl_pdf_fertig", width="stretch")
        try:
            name = re.sub(r"[^A-Za-z0-9]+", "_", str(_wert("an_pf_name", "") or "Portfolio")).strip("_") or "Portfolio"
            c3.download_button("Positionen als Excel", _excel_export(ctx, fred), f"Positionen_{name}_{datetime.now():%Y-%m-%d}.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", icon=":material/table:",
                               key="an_dl_xlsx", width="stretch",
                               help="Aktien (mit Benchmark-Gewicht) und Treasuries mit allen Kennzahlen, sofort, ohne Kursabruf")
        except Exception as ex:  # noqa: BLE001
            c3.caption(f"Excel nicht möglich: {type(ex).__name__}")
    return ph


# ---------------------------------------------------------------------------
# Benchmark & Active Share (Update 16)
# ---------------------------------------------------------------------------
def _bereich_benchmark(ctx, erg: dict, pos: pd.DataFrame, info: dict, total: float, cash: float, ak_w: float, tr_w: float,
                       f_a: float, f_t: float, cash_eff: float, R):
    """Reiter «Benchmark & Active Share»: Anlageklassen, Sektoren und Titel gegen die Benchmark des Dozenten."""
    import benchmark as BM

    ss = st.session_state
    ips_ = ctx.ips
    d = BM.aktuell()
    if d is None:
        st.warning("Keine Benchmark-Datei geladen – unten die CSV der Dozenten-App hochladen.")
        with st.container(border=True):
            BM.bereich_import(ctx, key="an_bm")
        return
    basis = st.segmented_control("Titelgewichte der Benchmark", ["gueltig", "aktuell"], default="gueltig", key="an_bm_basis",
                                 format_func=lambda x: {"gueltig": "gültig im Benchmark", "aktuell": "aktuell (mit Kursen fortgeschrieben)"}[x],
                                 help="«Gültig im Benchmark» = die Gewichte, gegen die die Dozenten-App misst (Monatsende). «Aktuell» = "
                                      "mit den Kursen seither fortgeschrieben.") or "gueltig"
    ak = pos[pos["klasse"] == "Aktie"]
    werte = ak.groupby("ticker")["wert"].sum().to_dict()
    sektor_von = {t: (info.get(t) or {}).get("sektor") for t in werte}
    a = BM.active_share(werte, basis, sektor_von) if werte else None
    a_inkl = BM.active_share(werte, basis, sektor_von, index_betrag=f_a) if f_a else None
    a_anders = BM.active_share(werte, "aktuell" if basis == "gueltig" else "gueltig", sektor_von) if werte else None
    as_ges = BM.active_share_gesamt(werte, tr_w + f_t, cash_eff, total, basis, index_betrag=f_a)
    lo, hi = float(ips_.get("active_share_min") or 0), float(ips_.get("active_share_max") or 100)
    kv, kb = erg["k"]["Vorauswahl"], erg["k"].get(BENCH, {})
    st.caption(f"Benchmark des Dozenten = 50 % S&P 500 / 50 % US-Treasuries. Aktienteil: {BM.beschreibung(d)}. Rendite und Risiko "
               f"der Benchmark über SPY und {NEUTRAL_PROXY} (Treasury-Proxy aus den Einstellungen), täglich auf 50/50 rebalanciert.")

    def _f(x, ok):
        return f"<span style='color:{ui.FARBE['Grün'] if ok else ui.FARBE['Rot']}'>{_pct(x, 1)}</span>" if x is not None else "–"
    kp = [
        ("Active Share Aktienteil", _f(a and a["active_share"], bool(a) and lo <= a["active_share"] <= hi),
         f"IPS {ui.de(lo, 0)}–{ui.de(hi, 0)} % · {'aktuell' if basis == 'gueltig' else 'gültig'}: {_pct(a_anders and a_anders['active_share'], 1)}"
         + (f" · inkl. offenes Budget als Index {_pct(a_inkl['active_share'], 1)}" if a_inkl else "")),
        ("davon Sektorwetten", _pct(a and a["as_sektor"], 1), f"Titelauswahl innerhalb der Sektoren {_pct(a and a['as_titel_in_sektor'], 1)}"),
        ("Titel im Index", f"{a['n_gemeinsam']} / {a['n_portfolio']}" if a else "–",
         (f"decken {ui.de(a['abdeckung'], 1)} % des S&P 500 ab · {a['n_benchmark']} Indextitel"
          + (f" · <b style='color:{ui.FARBE['Rot']}'>nicht im Index: {', '.join(a['nicht_im_index'])}</b>" if a["nicht_im_index"] else ""))
         if a else ""),
        ("Active Share gesamt", _pct(as_ges, 1), "Aktien, Treasuries (als Block) und Cash gegen 50 / 50 / 0 – Untergrenze"),
    ]
    ui.kopf_kpis(kp)
    ui.kopf_kpis([
        ("Mehrrendite vs. Benchmark p.a.", _pp(kv.get("mehr_bench")), f"Portfolio {_pct(kv.get('rendite'), 1, True)} · "
                                                                               f"Benchmark {_pct(kb.get('rendite'), 1, True)}"),
        ("Tracking Error vs. Benchmark", _pct(kv.get("te_bench")), f"IPS ≤ {ui.de(ips_.get('te_max'), 1)} % · Information Ratio "
                                                                     f"{_zahl(kv.get('ir_bench'))}"),
        ("Volatilität", _pct(kv.get("vola")), f"Benchmark {_pct(kb.get('vola'))}"),
        ("Sharpe Ratio", _zahl(kv.get("sharpe")), f"Benchmark {_zahl(kb.get('sharpe'))}"),
    ])
    R.neue_seite()
    R.h("Benchmark & Active Share")
    R.text(f"Benchmark des Dozenten: 50 % S&P 500 / 50 % Treasuries. Titelgewichte: {BM.beschreibung(d)} (Basis: "
           f"{BM.BASIS_NAME[basis]}).", klein=True)
    R.kpis(kp)

    # Anlageklassen
    st.markdown("**Anlageklassen** · Portfolio gegen Benchmark, neutrales Portfolio (IPS) und Taktik")
    nt = ctx.alloc["neutral"]
    ist = [(ak_w + f_a) / total * 100, (tr_w + f_t) / total * 100, cash_eff / total * 100]
    bm_ = [C.BENCHMARK["aktien"], C.BENCHMARK["anleihen"], C.BENCHMARK["cash"]]
    neu_ = [nt["aktien"], 100 - nt["aktien"] - nt.get("cash", 5), nt.get("cash", 5)]
    tak = [ctx.alloc["aktien"], ctx.alloc["anleihen"], ctx.alloc["cash"]]
    baend = [ctx.alloc["baender"]["aktien"], ctx.alloc["baender"]["treasuries"], ctx.alloc["baender"]["cash"]]
    kl = pd.DataFrame({"Klasse": ["Aktien", "Treasuries", "Cash"], "Portfolio": [_pct(x) for x in ist],
                       "Benchmark": [_pct(x) for x in bm_], "Aktiv vs. Benchmark": [f"{ui.de(i - b, 1, True)} Pp." for i, b in zip(ist, bm_)],
                       "Neutral (IPS)": [_pct(x) for x in neu_], "Taktik": [_pct(x) for x in tak],
                       "IPS-Band": [f"{lo_:g}–{hi_:g} %" for lo_, hi_ in baend]})
    st.dataframe(kl, hide_index=True, width="stretch")
    R.h2("Anlageklassen")
    R.tabelle(kl, breiten=[30, 30, 30, 40, 35, 30, 35])
    if not a:
        st.caption("Noch keine Aktien im Analyseportfolio.")
        return

    # Sektoren
    g_max = float(ips_.get("sektor_abw_max") or 5)
    sk = a["sektoren"].copy()
    sk["_st"] = [L.GRUEN if abs(x) <= g_max * 0.8 else L.GELB if abs(x) <= g_max else L.ROT for x in sk["aktiv"]]
    sek_df = pd.DataFrame({"Sektor": sk["sektor"], f"Benchmark ({BM.BASIS_NAME[basis]})": sk["benchmark"].map(lambda x: f"{ui.de(x, 2)} %"),
                           "Portfolio (Aktienteil)": sk["portfolio"].map(lambda x: f"{ui.de(x, 2)} %"),
                           "Aktiv": sk["aktiv"].map(lambda x: f"{ui.de(x, 2, True)} Pp."),
                           "IPS ±": f"{ui.de(g_max, 1)} Pp.", "Prüfung": [f"● {x}" for x in sk["_st"]],
                           "Beitrag Active Share": sk["beitrag_as"].map(lambda x: f"{ui.de(x, 1)} Pp."),
                           "Titel Portfolio / Index": [f"{p} / {b}" for p, b in zip(sk["titel_p"], sk["titel_b"])]})
    st.markdown("**Sektoren** · Anteil am Aktienteil gegen die Benchmark, Grenze = IPS «Maximale Abweichung je Sektor»")
    st.dataframe(ui.ampel_stil(sek_df, ["Prüfung"]), hide_index=True, width="stretch")
    st.caption("Beitrag Active Share = halbe Summe der Titelabweichungen im Sektor (Sektorwette + Titelauswahl). Die Sektorziele "
               "eurer Sektorallokation (Index + geplante Abweichung) prüft der Reiter «Regeln & Struktur».")
    R.h2("Sektoren gegen die Benchmark")
    R.tabelle(sek_df.assign(Prüfung=[x.replace("● ", "") for x in sek_df["Prüfung"]]),
              farben=[[None] * 5 + [_HELL_RGB.get(x)] + [None] * 2 for x in sk["_st"]])

    # Über- und Untergewichte
    tabl = a["tabelle"]
    ueber = tabl[tabl["aktiv"] > 0].head(15)
    unter = tabl[tabl["aktiv"] < 0].head(15)
    fmt = lambda df_: pd.DataFrame({"Titel": df_["ticker"], "Name": df_["name"], "Sektor": df_["sektor"],  # noqa: E731
                                    "Portfolio": df_["portfolio"].map(lambda x: f"{ui.de(x, 2)} %"),
                                    "Benchmark": df_["benchmark"].map(lambda x: f"{ui.de(x, 2)} %"),
                                    "Aktiv": df_["aktiv"].map(lambda x: f"{ui.de(x, 2, True)} Pp.")})
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Grösste Übergewichte** (Anteil am Aktienteil)")
        st.dataframe(fmt(ueber), hide_index=True, width="stretch")
    with c2:
        st.markdown("**Grösste Untergewichte** (auch nicht gehaltene Indextitel)")
        st.dataframe(fmt(unter), hide_index=True, width="stretch")
    zz = pd.concat([ueber.head(12), unter.head(12)]).sort_values("aktiv")
    fig = go.Figure(go.Bar(y=zz["ticker"], x=zz["aktiv"], orientation="h",
                           marker_color=[ui.BLAU if x > 0 else BENCH_FARBE for x in zz["aktiv"]],
                           hovertemplate="%{y}: %{x:.2f} Pp.<extra></extra>"))
    fig.update_layout(height=max(300, 22 * len(zz) + 60), margin=dict(l=8, r=8, t=10, b=8))
    fig.update_xaxes(ticksuffix=" Pp.", title="Aktiv gegenüber der Benchmark (Anteil am Aktienteil)")
    ui.zeige(fig)
    R.h2("Grösste Über- und Untergewichte")
    R.tabelle(fmt(pd.concat([ueber, unter])), max_zeilen=30, breiten=[25, 60, 30, 25, 25, 25])
    with st.expander(f"Alle Titel des Portfolios mit Benchmark-Gewicht ({a['n_portfolio']})", icon=":material/table_view:"):
        alle = tabl[tabl["portfolio"] > 0].sort_values("portfolio", ascending=False)
        st.dataframe(fmt(alle), hide_index=True, width="stretch")
        st.download_button("Als CSV herunterladen", fmt(tabl).to_csv(index=False, sep=";").encode("utf-8-sig"),
                           "active_share_titel.csv", "text/csv", icon=":material/download:", key="an_bm_csv")
    with st.expander("Benchmark-Datei der Dozenten-App aktualisieren", icon=":material/upload_file:"):
        BM.bereich_import(ctx, key="an_bm")


# ---------------------------------------------------------------------------
# Gewichte optimieren und ins Analyseportfolio übernehmen (Update 16)
# ---------------------------------------------------------------------------
VARIANTEN = {"heute": "Heute", "max_sharpe": "Max. Sharpe", "min_vola": "Min. Volatilität", "max_ir": "Max. IR vs. Benchmark"}


def _gewichte_uebernehmen(neu: dict, total: float, als_kopie: bool, speichern: bool, label: str, grad: int, args: tuple):
    """Callback: Gewichte (Anteil am Gesamtportfolio je Ticker) als fixes «Gewicht %» ins aktive Analyseportfolio bzw. in eine
    Kopie schreiben, in ganze Stücke umrechnen und gleich neu rechnen lassen."""
    ss = st.session_state
    e_alt = ss.get("an_eingabe") or {}
    if als_kopie:
        alt_name = str(_wert("an_pf_name", "") or "Portfolio")
        _pf_neu("kopie", *args)
        name = f"{alt_name} – {label}"
        ss["an_pf"][ss["an_aktiv"]]["name"] = name
        ss["an_pf_name"] = ss["_an_pf_name"] = name
    kap = _num(ss.get("an_kap_txt")) or 0.0
    if kap <= 0 or "an_aktien" not in ss:
        _meldung("Kapital fehlt – Gewichte nicht übernommen.")
        return
    p_ak = float(ss.get("an_p_ak") or 0)
    df = _aktien_df(ss["an_aktien"], 0).reset_index(drop=True)
    n = 0
    for i in df.index:
        t = _txt(df.at[i, "Ticker"]).strip().upper().replace(".", "-")
        if t in neu:
            pct_g = float(neu[t]) * total / kap * 100
            pct = pct_g if ss.get("an_bezug") != AKTIENTEIL else (pct_g / (p_ak / 100) if p_ak > 0 else pct_g)
            df.at[i, "Gewicht %"] = _fz_pct(round(pct, 4))
            df.at[i, "Fix"] = "%"
            n += 1
    df = _prozent_verteilen(df)
    if ss.get("an_sort") in ("sektor", "ticker", "name"):
        df = _sortieren(df, ss["an_sort"])
    ss["an_aktien"] = _aktien_df(df)
    ss["an_ak_ver"] = ss.get("an_ak_ver", 0) + 1
    ss["an_form_ver"] = ss.get("an_form_ver", 0) + 1
    ss["an_auto_rechnen"] = True
    ss["an_auto_e"] = dict(jahre=e_alt.get("jahre"), fund=e_alt.get("fund", False))
    if speichern:
        ss["an_pf_save_req"] = True
    ss["an_meldung_info"] = (f"«{label}» übernommen ({grad} % umgesetzt): {n} Titel stehen jetzt mit fixem Gewicht % in der Tabelle "
                             "und sind in ganze Stücke umgerechnet, die Kennzahlen unten sind neu gerechnet. "
                             + ("Gespeichert wird mit der Leiste oben. " if speichern else
                                "Noch nicht gespeichert – oben «Speichern» (oder «Verwerfen», um zurückzugehen). ")
                             + "Ins echte Portfolio kommt es wie immer über «Analyseportfolio ins Portfolio übernehmen».")


def _gewichte_bereich(ctx, erg: dict, pos: pd.DataFrame, info: dict, total: float, letzte: dict, R, args: tuple):
    import benchmark as BM

    ips_ = ctx.ips
    st.markdown("Optimiert die Gewichte **eurer Aktien** auf die gewählte Kennzahl – Treasuries, Cash und offenes Budget bleiben "
                "fix, die Aktienquote auch. Mit «Sektorgewichte halten» bleibt zusätzlich die Summe je Sektor gleich, eure "
                "Sektorallokation wird also nicht verändert – optimiert wird nur die Titelauswahl innerhalb der Sektoren.")
    with st.container(border=True):
        c1, c2, c3, c4 = st.columns([1, 1, 1, 1.3])
        deckel = c1.number_input("Höchstgewicht je Titel (% Gesamt)", min_value=0.1, max_value=100.0, step=0.25, format="%.2f",
                                 value=float(ips_.get("titel_max") or KLUMPEN), key="an_opt_deckel",
                                 help="Standard = IPS «Maximales Gewicht je Titel»")
        mindest = c2.number_input("Mindestgewicht je Titel (% Gesamt)", min_value=0.0, max_value=5.0, step=0.05, format="%.2f",
                                  value=0.05, key="an_opt_min",
                                  help="> 0 = jeder Titel bleibt im Portfolio (IPS-Mindestzahl Aktien, Sektorplan). 0 = Titel dürfen "
                                       "wegfallen.")
        halten = c3.toggle("Sektorgewichte halten", value=True, key="an_opt_sek",
                           help="Summe je Sektor bleibt wie heute – die Sektorallokation bleibt eingehalten.")
        daempf = c4.slider("Renditen dämpfen", 0, 100, 50, step=10, format="%d %%", key="an_opt_d",
                           help="Historische Renditen sind ein schwacher Schätzer. 50 % = je Titel halb eigene Rendite, halb "
                                "Durchschnitt aller Titel. 100 % = nur Risiko zählt (Max. Sharpe ≈ Risiko-Optimierung).")
    fix_ = set(erg["fuell"]) - set(pos["ticker"])
    sektor_von = {t: (info.get(t) or {}).get("sektor") or "Unbekannt" for t in pos["ticker"]}
    o = optimieren(erg["R"], erg["rf"], erg.get("r_b"), erg["gew"], erg["cash"], fix=fix_, sektor_von=sektor_von,
                   deckel=deckel / 100, mindest=mindest / 100, sektor_halten=halten, daempfung=daempf / 100)
    if o is None:
        st.caption("Für eine Optimierung mindestens zwei Aktien mit Kursgeschichte eingeben.")
        return
    for h in o["hinweise"]:
        st.caption(h)
    ohne = [t for t in pos.loc[pos["klasse"] == "Aktie", "ticker"] if t not in o["aktien"]]
    if ohne:
        st.caption("Ohne Kursgeschichte nicht optimiert (Gewicht bleibt): " + ", ".join(ohne))
    gew_v = {"heute": {t: erg["gew"][t] for t in o["aktien"]}}
    gew_v.update({k_: o[k_] for k_ in ("max_sharpe", "min_vola", "max_ir") if k_ in o})
    stats = {}
    for k_, w_ in gew_v.items():
        r_ = portfolio_reihe(erg["R"], erg["rf"], {**o["fest"], **w_}, erg["cash"])
        kk = kennzahlen(r_, erg["rf"], erg["r_s"])
        if kk:
            kk.update(vs_benchmark(r_, erg["r_b"], kk, erg["k"].get(BENCH, {})))
            ad = BM.active_share({t: w for t, w in w_.items()}) if BM.aktuell() is not None else None
            kk["active_share"] = ad["active_share"] if ad else None
            kk["max_w"] = max(w_.values()) * 100
            kk["n"] = sum(1 for v in w_.values() if v > 1e-5)
            kk["umschlag"] = sum(abs(w_[t] - gew_v["heute"][t]) for t in w_) / 2 * 100
        stats[k_] = kk
    zeilen = []
    for key, name, f in (("sharpe", "Sharpe Ratio", _zahl), ("rendite", "Rendite p.a.", lambda x: _pct(x, 1, True)),
                         ("vola", "Volatilität p.a.", _pct), ("mdd", "Max. Drawdown", _pct), ("beta", "Beta zum S&P 500", _zahl),
                         ("te_bench", "Tracking Error vs. Benchmark", _pct), ("ir_bench", "Information Ratio vs. Benchmark", _zahl),
                         ("active_share", "Active Share Aktien", _pct), ("max_w", "Grösstes Titelgewicht", lambda x: _pct(x, 2)),
                         ("n", "Titel mit Gewicht", lambda x: str(int(x)) if x is not None else "–"),
                         ("umschlag", "Umschichtung (einfach)", lambda x: _pct(x, 1))):
        zeilen.append({"Kennzahl": name, **{VARIANTEN[k_]: f(stats[k_].get(key)) if stats.get(k_) else "–" for k_ in gew_v}})
    vgl = pd.DataFrame(zeilen)
    st.dataframe(vgl, hide_index=True, width="stretch")
    st.caption(f"Historisch im gewählten Zeitraum mit konstanten Gewichten (inkl. Treasuries und Cash). IPS: Vola ≤ "
               f"{ui.de(ips_.get('vola_max'), 0)} %, TE ≤ {ui.de(ips_.get('te_max'), 1)} %, Active Share "
               f"{ui.de(ips_.get('active_share_min'), 0)}–{ui.de(ips_.get('active_share_max'), 0)} %. Optimierte Gewichte passen "
               "perfekt zur Vergangenheit – die Dämpfung und die Grenzen halten sie vernünftig. Umschichtung = halbe Summe der "
               "Gewichtsänderungen.")
    tz = []
    for t in o["aktien"]:
        z = {"Titel": t, "Name": (info.get(t) or {}).get("name", ""), "Sektor": sektor_von.get(t, "")}
        for k_ in gew_v:
            z[VARIANTEN[k_]] = _pct(gew_v[k_][t] * 100, 2)
        tz.append(z)
    tdf = pd.DataFrame(tz)
    if halten:
        tdf = tdf.sort_values(["Sektor", "Titel"])
    with st.expander(f"Gewichte je Titel ({len(tdf)}) · Anteil am Gesamtportfolio", icon=":material/table_view:"):
        st.dataframe(tdf, hide_index=True, width="stretch")

    # --- Übernehmen ----------------------------------------------------------------
    st.markdown("**In das Analyseportfolio übernehmen**")
    vor = [k_ for k_ in gew_v if k_ != "heute"]
    a1, a2, a3 = st.columns([1.6, 1.2, 1.4])
    wahl = a1.radio("Vorschlag", vor, format_func=VARIANTEN.get, horizontal=True, key="an_opt_wahl")
    grad = a2.slider("Umsetzung", 0, 100, 100, step=10, format="%d %%", key="an_opt_grad",
                     help="100 % = Vorschlag ganz. 50 % = Mitte zwischen heute und Vorschlag (schrittweise umschichten). "
                          "Grenzen und Sektorsummen bleiben auch bei der Mischung eingehalten.")
    ziel = a3.radio("Wohin", ["aktiv", "kopie"], horizontal=True, key="an_opt_ziel",
                    format_func=lambda x: "dieses Portfolio" if x == "aktiv" else "neue Kopie (Original bleibt)")
    g = grad / 100
    neu = {t: (1 - g) * gew_v["heute"][t] + g * gew_v[wahl][t] for t in o["aktien"]}
    diff = sorted(((t, (neu[t] - gew_v["heute"][t]) * 100) for t in o["aktien"]), key=lambda x: -abs(x[1]))
    st.caption("Grösste Änderungen (Pp. des Gesamtportfolios): " + " · ".join(f"{t} {ui.de(v, 2, True)}" for t, v in diff[:8] if abs(v) >= 0.005))
    b1, b2, b3 = st.columns([1.2, 1.4, 2.4])
    label = VARIANTEN[wahl] + ("" if grad == 100 else f" {grad} %")
    b1.button(f"«{VARIANTEN[wahl]}» übernehmen", type="primary", icon=":material/move_down:", width="stretch",
              on_click=_gewichte_uebernehmen, args=(neu, total, ziel == "kopie", False, label, grad, args), key="an_opt_los",
              help="Schreibt die Gewichte als fixes «Gewicht %» in die Aktien-Tabelle (ganze Stücke) und rechnet neu")
    b2.button("Übernehmen & speichern", icon=":material/save:", width="stretch", disabled=ctx.nur_lesen,
              on_click=_gewichte_uebernehmen, args=(neu, total, ziel == "kopie", True, label, grad, args), key="an_opt_los_s")
    b3.caption("Danach steht das Portfolio mit den neuen Gewichten oben in der Eingabe (Fix = %). Ins echte Portfolio (Transaktionen) "
               "geht es wie gewohnt über «Analyseportfolio ins Portfolio übernehmen» – übernommen wird der gespeicherte Stand.")
    R.neue_seite()
    R.h("Gewichte optimieren (historisch, nur Denkanstoss)")
    R.text(f"Höchstgewicht je Titel {ui.de(deckel, 2)} %, Mindestgewicht {ui.de(mindest, 2)} %, Sektorgewichte "
           f"{'gehalten' if halten else 'frei'}, Renditen zu {daempf} % gedämpft. Treasuries, Cash und offenes Budget fix.", klein=True)
    R.tabelle(vgl)
    R.tabelle(tdf, max_zeilen=400)


_HELL_RGB = {L.ROT: (253, 241, 240), L.GELB: (253, 246, 231), L.GRUEN: (237, 247, 241)}


def _analyse_sammler(ctx, e: dict, kv: dict, total: float, cash: float, ak_w: float, tr_w: float, f_a: float, f_t: float,
                     cash_eff: float, zeit: str, erg: dict):
    """PDF der Analyse (Update 13): Kopf mit Portfolio, Ersteller, Version, Zeitraum, Aufteilung."""
    import seitenpdf as SP

    ss = st.session_state
    ent = (ss.get("an_pf", {}).get(ss.get("an_aktiv"), {}) or {})
    name = _wert("an_pf_name", "") or ent.get("name", "Analyseportfolio")
    notiz = _wert("an_pf_notiz", "") or ent.get("notiz", "")
    status = "gespeichert" if ent.get("gespeichert") else "nicht gespeichert"
    if ent.get("versionen"):
        status += f", Version {ent.get('versionen')}"
    R = SP.Sammler(f"Analyse: {name}",
                   f"Analyseportfolio ({status}) · Kapital {ui.de(e.get('kapital') or total, 0)} USD · "
                   f"Zeitraum {kv['von']:%d.%m.%Y} – {kv['bis']:%d.%m.%Y}",
                   kurz=f"Analyse {name}", quer=True)
    if notiz:
        R.text(f"Notiz: {notiz}", klein=True)
    pct = e.get("pct") or {}
    R.text(f"Aufteilung geplant (Aktien / Treasuries / Cash): {ui.de(pct.get('aktien', 0), 2)} / {ui.de(pct.get('treas', 0), 2)} / "
           f"{ui.de(pct.get('cash', 0), 2)} % · investiert {ui.de(ak_w / total * 100, 1)} / {ui.de(tr_w / total * 100, 1)} % · "
           f"Cash {ui.de(cash / total * 100, 1)} % · Taktik {ui.de(ctx.alloc['aktien'], 1)} / {ui.de(ctx.alloc['anleihen'], 1)} / "
           f"{ui.de(ctx.alloc['cash'], 1)} % · Bezug «Gewicht %»: {e.get('bezug', GESAMT)}.", klein=True)
    return R


def _anleihen_pdf(R, ctx, fred: dict, e: dict, cash: float):
    """Anleihen & Cash im Analyse-PDF: Kennzahlen je Anleihe vs. Benchmark und Zins auf Cash bis Simulationsende."""
    try:
        import anleihen as AN
        import cashzins as CZ

        k = AN.kurve(fred)
        R.neue_seite()
        R.h("Anleihen & Cash")
        if len(k) < 5:
            R.text("Keine Zinskurve von FRED – Anleihen-Teil nicht gerechnet.", klein=True)
            return
        defs, szen = ctx.szdef, ctx.szen
        df = AN.anleihen_tabelle(_treas_df(e["treas"]), k, defs, szen)
        bench = AN.benchmark_info(k, ctx.einst.get("bond_benchmark", C.BOND_BENCHMARK_DEFAULT))
        tab = AN.tabelle(k, defs, szen)
        m_b = AN.mix(tab, bench["gewichte"], defs)
        a = CZ.aktuell(fred)
        pg = CZ.prognose(cash, a["sofr"], defs, szen) if a else None
        if len(df):
            m_p = AN.mix_anleihen(df, defs)
            betrag = float(df["betrag"].sum())
            R.kpis([
                ("Treasury-Teil", f"{ui.de(betrag, 0)} USD", f"{len(df)} Anleihen"),
                ("Duration", f"{ui.de(m_p['duration'], 2)} J.", f"Benchmark {ui.de(bench['duration'], 2)} J. · Taktik "
                                                              f"{ui.de(ctx.alloc.get('duration') or 0, 1)} J."),
                ("Rendite (Yield)", f"{ui.de(m_p['rendite'], 2)} %", f"Benchmark {ui.de(m_b['rendite'], 2)} % · DV01 "
                                                                   f"{ui.de(betrag * m_p['duration'] / 1e4, 0)} USD"),
                ("Erw. Mehrrendite 1 Mt.", f"{ui.de((m_p['erw'] - m_b['erw']) * 100, 0, True)} bp",
                 f"Anleihen {ui.de(m_p['erw'], 2, True)} % vs. Benchmark {ui.de(m_b['erw'], 2, True)} %"),
            ])
            R.kpis(AN.kpis_kurz(AN.kennzahlen_kurz(df), bench=bench, konv_b=m_b["konv"])[:4])
            R.tabelle(pd.DataFrame({
                "Anleihe": df["name"], "ISIN": df["isin"], "Kupon": df["kupon"].map(lambda x: f"{ui.de(x, 3)} %"),
                "Fälligkeit": df["faellig"].map(lambda d: f"{d:%d.%m.%Y}" if pd.notna(d) else "–"),
                "Betrag USD": df["betrag"].map(lambda x: ui.de(x, 0)), "Gewicht": df["gewicht"].map(lambda x: f"{ui.de(x, 1)} %"),
                "Kurs": df["kurs"].map(lambda x: f"{ui.de(x, 3)} %"), "Rendite": df["y"].map(lambda x: f"{ui.de(x, 2)} %"),
                "Spread": df["spread"].map(lambda x: f"{ui.de(x, 0, True)} bp"), "Mod. Dur.": df["D"].map(lambda x: ui.de(x, 2)),
                "Konvexität": df["konv"].map(lambda x: ui.de(x, 1)), "Restlaufz.": df["T"].map(lambda x: f"{ui.de(x, 1)} J."),
                "DV01 USD": df["dv01"].map(lambda x: ui.de(x, 0)),
                "Carry+Roll/Mt.": [f"{ui.de((c + r_) * 100, 0)} bp" for c, r_ in zip(df["carry"], df["roll"])],
                "Erw. 1 Mt.": df["erw"].map(lambda x: f"{ui.de(x, 2, True)} %"),
                **{defs[s]["kurz"]: df[f"r_{s}"].map(lambda x: f"{ui.de(x, 2, True)} %") for s in defs},
                "Kupons bis 28.02.": df["kupons_sim"].map(lambda x: ui.de(x, 0)),
                "Marchzins": df["marchzins"].map(lambda x: ui.de(x, 0))}), max_zeilen=200)
            R.text(f"Kupons bis 28.02.: {ui.de(float(df['kupons_sim'].sum()), 0)} USD · Marchzins beim Kauf: "
                   f"{ui.de(float(df['marchzins'].sum()), 0)} USD · Benchmark: {bench['name']}. Rendite aus dem Kurs der Liste "
                   "(sonst FRED-Kurve), Szenario-Spalten = Monatsrendite je Szenario.", klein=True)
        else:
            R.text("Keine Treasuries im Analyseportfolio erfasst.", klein=True)
        if a and pg:
            R.h2("Zins auf Cash (SOFR − 0,5 %)")
            R.kpis([("Satz heute", f"{ui.de(a['netto'], 2)} %", f"SOFR {ui.de(a['sofr'], 2)} % ({a['datum']:%d.%m.%Y}) − 0,5 %"),
                    ("Cash", f"{ui.de(cash, 0)} USD", "Kapital − Aktien − Treasuries"),
                    ("Zins bis 28.02.", f"{ui.de(pg['total'], 0)} USD", f"szenariogewichtet · unverändert {ui.de(pg['unveraendert'], 0)} USD"),
                    ("Je Szenario", " / ".join(ui.de(v, 0) for v in pg["szen"].values()),
                     " / ".join(defs[s]["kurz"] for s in pg["szen"]))])
    except Exception as ex:  # noqa: BLE001 – Anleihen-Teil soll das PDF nicht verhindern
        R.text(f"(Anleihen & Cash konnte nicht gerechnet werden: {type(ex).__name__})", klein=True)
