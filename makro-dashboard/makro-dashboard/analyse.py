"""Analyse einer Vorauswahl (oder des aktuellen Portfolios): Rendite-, Risiko- und Regelkennzahlen aus Kursdaten.

Methodik (auch in der App erklärt):
- Tagesschlusskurse von Yahoo Finance, dividendenbereinigt (Total Return).
- Konstante Gewichte (tägliches Rebalancing), annualisiert mit 252 Handelstagen.
- Risikofreier Zins: 13-Wochen-T-Bill (^IRX), Ersatzwert 4 %.
- Treasuries über liquide ETFs je Laufzeitband (Näherung), Cash verzinst zum risikofreien Zins.
- Noch nicht verteiltes Budget: wahlweise als Index-Platzhalter (SPY bzw. Treasury-ETF des Benchmarks) oder als Cash.
- «Aktienteil» = nur die eingegebenen Titel, auf 100 % hochgerechnet (Risiko der Titel für sich allein).
- Aktien-Eingabe auch als «Gewicht %» (Bezug Gesamtportfolio oder Aktienteil); fixe % passen die Stückzahl an,
  wenn Kapital oder Aktien-% ändern.
"""
from __future__ import annotations

import json
import math
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
KLUMPEN = 10.0          # % des Gesamtportfolios je Einzeltitel


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
    """Tagesrendite bei konstanten Gewichten; Cash verzinst zum risikofreien Zins."""
    w = pd.Series({k: v for k, v in gewichte.items() if k in R.columns}, dtype=float)
    r = R[w.index].mul(w, axis=1).sum(axis=1) if len(w) else pd.Series(0.0, index=R.index)
    return r + cash * rf.reindex(R.index).fillna(RF_ERSATZ / TAGE)


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
    k = {"Vorauswahl": kennzahlen(r_p, rf, r_s)}
    # Aktienteil allein (Titel auf 100 % hochgerechnet, ohne Treasuries, Cash und Platzhalter)
    wa = pos[pos["klasse"] == "Aktie"].groupby("ticker")["wert"].sum() if len(pos) else pd.Series(dtype=float)
    wa = wa[wa.index.isin(R.columns) & (wa > 0)]
    gew_a = (wa / wa.sum()).to_dict() if len(wa) else {}
    r_a = portfolio_reihe(R, rf, gew_a, 0.0) if gew_a else None
    if r_a is not None:
        k["Aktienteil"] = kennzahlen(r_a, rf, r_s)
    k["Neutral (IPS)"] = kennzahlen(r_n, rf, r_s)
    k["S&P 500"] = kennzahlen(r_s, rf, r_s)
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
    return dict(R=R, rf=rf, r_p=r_p, r_n=r_n, r_s=r_s, r_a=r_a, k=k, einzel=einzel, gew=gew, gew_a=gew_a, cash=c, start=start,
                fuell=fuell, beitrag=risikobeitraege(R, gew), beitrag_a=risikobeitraege(R, gew_a) if gew_a else pd.Series(dtype=float))


def vorschlag(R: pd.DataFrame, rf: pd.Series, gew: dict, cash: float, n: int = 4000, seed: int = 1,
              fix: set | None = None) -> dict | None:
    """Zufallsportfolios im Aktienteil (Treasuries, Cash und Platzhalter fix): beste Sharpe Ratio und tiefste Volatilität."""
    aktien = [t for t in gew if t in R.columns and t not in {v[0] for v in PROXY.values()} and t not in (fix or set())]
    if len(aktien) < 2:
        return None
    anteil = sum(gew[t] for t in aktien)
    fix = {t: w for t, w in gew.items() if t not in aktien}
    deckel = min(1.0, KLUMPEN / 100 / anteil) if anteil else 1.0
    ohne_deckel = deckel * len(aktien) < 1.15     # mit so wenigen Titeln nicht einhaltbar
    if ohne_deckel:
        deckel = 1.0
    rng = np.random.default_rng(seed)
    W = rng.dirichlet(np.ones(len(aktien)), size=n * 5)
    W = W[(W <= deckel + 1e-9).all(axis=1)][:n]
    if len(W) < 50:
        return None
    Ra = R[aktien].to_numpy()
    basis = portfolio_reihe(R, rf, fix, cash).to_numpy()
    rfv = rf.reindex(R.index).to_numpy()
    P = basis[:, None] + Ra @ (W.T * anteil)
    ex = P - rfv[:, None]
    sd = P.std(axis=0, ddof=1)
    sharpe = ex.mean(axis=0) / sd * math.sqrt(TAGE)
    i_s, i_v = int(np.nanargmax(sharpe)), int(np.nanargmin(sd))
    return dict(aktien=aktien, anteil=anteil, deckel=deckel, n=len(W), ohne_deckel=ohne_deckel,
                max_sharpe=dict(zip(aktien, W[i_s] * anteil)), min_vola=dict(zip(aktien, W[i_v] * anteil)),
                sharpe=(float(sharpe[i_s]), float(sharpe[i_v])), vola=(float(sd[i_s] * math.sqrt(TAGE) * 100),
                                                                        float(sd[i_v] * math.sqrt(TAGE) * 100)))


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
    df["Gewicht %"] = [_fz(x, 2) if _txt(x) else "" for x in df["Gewicht %"]]
    df = df[[bool(t.strip()) or bool(m) or bool(b) or bool(p)
             for t, m, b, p in zip(df["Ticker"], df["Menge"], df["Betrag USD"], df["Gewicht %"])]]
    leer = pd.DataFrame([{c: "" for c in AK_SPALTEN}] * mindestens_leer)
    return pd.concat([df, leer], ignore_index=True).astype("string")


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
    df.at[i, "Gewicht %"] = _fz(pct, 2) if pct is not None else ""
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
    if b["kapital"] <= 0:
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
    df.at[i, "Gewicht %"] = _fz(neu / basis * 100, 2) if neu is not None and basis > 0 else ""


def _basis_aenderung():
    """Kapital oder Aktien-% geändert: Zeilen mit fixem Gewicht % → neue Stückzahl, übrige Zeilen → neues Gewicht %.
    Zeilen mit fixem % werden danach aufs Aktienbudget gedeckelt (von oben nach unten)."""
    ss = st.session_state
    if "an_aktien" not in ss:
        return
    df = ss["an_aktien"].copy().reset_index(drop=True)
    belegt = [i for i in df.index if _txt(df.at[i, "Ticker"]).strip()]
    for i in belegt:
        _ak_rechnen(df, i, set())
    prozent = [i for i in belegt if _txt(df.at[i, "Fix"]) == "%"]
    for n_, i in enumerate(prozent):
        _ak_deckeln(df, i, set(prozent[n_ + 1:]))
    ss["an_aktien"] = _aktien_df(df)
    ss["an_ak_ver"] = ss.get("an_ak_ver", 0) + 1


def _kap_aenderung():
    _feld_format("an_kap_txt")
    _basis_aenderung()


def _bezug_aenderung():
    """Bezug von «Gewicht %» umgestellt: Beträge bleiben, die Prozentzahlen werden umgerechnet."""
    ss = st.session_state
    if "an_aktien" not in ss:
        return
    p_ak = float(ss.get("an_p_ak") or 0) / 100
    df = ss["an_aktien"].copy().reset_index(drop=True)
    for i in df.index:
        pct = _num(df.at[i, "Gewicht %"])
        if _txt(df.at[i, "Fix"]) == "%" and pct is not None and p_ak > 0:
            df.at[i, "Gewicht %"] = _fz(pct / p_ak if ss.get("an_bezug") == AKTIENTEIL else pct * p_ak, 2)
    ss["an_aktien"] = df
    _basis_aenderung()


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
        df.at[i, "Gewicht %"] = _fz(pct, 2)
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
               "Kurs %": "float64", "Bezeichnung": "string"}


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
    ss["an_treas"] = _treas_df(df.reset_index(drop=True))
    ss["an_tr_ver"] = ss.get("an_tr_ver", 0) + 1


def _feld_format(key: str):
    v = _num(st.session_state.get(key))
    st.session_state[key] = ui.de(v, 0) if v is not None else ""


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
    zinsen = float(b["zins"].sum()) if len(b) else 0.0
    cash = float(kapital + cash_delta + zinsen)
    wert = float(b["wert"].sum()) + cash if len(b) else cash
    return (pd.concat([a, _leer_aktien(2)], ignore_index=True).astype({"Ticker": "string", "Menge": "float64", "Betrag USD": "float64"}),
            t.astype({"Laufzeit": "string", "Betrag USD": "float64"}),
            max(0.0, cash), wert)


def seite_analyse(ctx, fred: dict, lade_kurse):
    import portfolio as P

    ui.seitenkopf("Analyse – Vorauswahl prüfen",
                  "Titel und Mengen eingeben – die App berechnet Rendite, Risiko, Sharpe Ratio, Beta, Value at Risk, "
                  "Korrelationen und prüft eure Regeln. Vergleich immer mit dem neutralen Portfolio (strategische Allokation laut IPS) und dem S&P 500.")
    tab = ctx.tabellen
    kapital_std, bm = P._einstellungen(tab.get("einstellungen"))
    ss = st.session_state
    ss.setdefault("an_ver", 0)
    if "an_aktien" not in ss:
        ss.update(an_aktien=_aktien_df(None, 6), an_treas=_leer_treas(), an_cash=0.0, an_kapital=float(kapital_std))
    if list(ss["an_aktien"].columns) != AK_SPALTEN:      # Sitzung aus der Zeit vor dem Update
        ss["an_aktien"] = _aktien_neu_rechnen(ss["an_aktien"])
    if "an_kap_txt" not in ss:
        ss.update(an_kap_txt=ui.de(float(ss["an_kapital"]), 0))
    if "an_p_ak" not in ss:
        ss.update(an_p_ak=float(ctx.alloc["aktien"]), an_p_tr=float(ctx.alloc["anleihen"]), an_p_ca=float(ctx.alloc["cash"]))
    ss.setdefault("an_bezug", GESAMT)

    # --- Vorlage laden --------------------------------------------------------
    gespeichert = tab.get("analysen")
    gespeichert = (gespeichert.sort_values("zeit", ascending=False).drop_duplicates("id")
                   if gespeichert is not None and not gespeichert.empty else pd.DataFrame())
    optionen = ["leer", "portfolio"] + list(gespeichert["id"]) if len(gespeichert) else ["leer", "portfolio"]
    namen = {"leer": "Leere Vorauswahl", "portfolio": "Aktuelles Portfolio übernehmen"}
    for _, z in gespeichert.iterrows():
        namen[z["id"]] = f"Gespeichert: {z['name']} ({z['person']}, {str(z['zeit'])[:10]})"
    c1, c2, _ = st.columns([2.2, 0.8, 1.6])
    wahl = c1.selectbox("Ausgangslage", optionen, format_func=lambda i: namen.get(i, i), key="an_vorlage")
    if c2.button("Laden", icon=":material/upload:", width="stretch"):
        if wahl == "leer":
            ss.update(an_aktien=_aktien_df(None, 6), an_treas=_leer_treas(), an_cash=0.0)
        elif wahl == "portfolio":
            v = portfolio_vorlage(tab, fred, lade_kurse)
            if v is None:
                ss["an_meldung"] = "Im Portfolio sind noch keine Transaktionen erfasst."
            else:
                ss.update(an_kapital=v[3], an_kap_txt=ui.de(float(v[3]), 0))    # zuerst: Basis für «Gewicht %»
                ss.update(an_aktien=_aktien_neu_rechnen(v[0]), an_treas=_treas_df(v[1]), an_cash=max(0.0, v[2]))
        else:
            z = gespeichert.set_index("id").loc[wahl]
            e = json.loads(z["eingabe"])
            a_ = pd.DataFrame(e["aktien"])
            for c_ in ("Ticker", "Menge", "Betrag USD"):
                if c_ not in a_.columns:
                    a_[c_] = None
            t_ = _treas_df(pd.DataFrame(e["treas"]))
            if isinstance(e.get("pct"), dict):
                ss.update(an_p_ak=float(e["pct"].get("aktien", ss["an_p_ak"])), an_p_tr=float(e["pct"].get("treas", ss["an_p_tr"])),
                          an_p_ca=float(e["pct"].get("cash", ss["an_p_ca"])))
            kap_ = float(e.get("kapital", kapital_std))
            ss.update(an_kapital=kap_, an_kap_txt=ui.de(kap_, 0), an_bezug=e.get("bezug") if e.get("bezug") in (GESAMT, AKTIENTEIL) else GESAMT)
            ss.update(an_aktien=_aktien_neu_rechnen(a_),
                      an_treas=t_ if len(t_) else _leer_treas(),
                      an_cash=float(e.get("cash", 0)))
        ss["an_ver"] += 1
        ss["an_ak_ver"] = ss.get("an_ak_ver", 0) + 1
        ss["an_tr_ver"] = ss.get("an_tr_ver", 0) + 1
        ss.update(an_kap_txt=ui.de(float(ss["an_kapital"]), 0))
        ss.pop("an_eingabe", None)
        st.rerun()

    if m := ss.pop("an_meldung", None):
        st.warning(m)

    with st.expander(f"Vorauswahl ins Portfolio übernehmen (Kauf per {pd.Timestamp(C.SIMULATION_START):%d.%m.%Y})",
                     icon=":material/shopping_cart_checkout:", expanded=bool(ss.get("ue_vor"))):
        import uebernahme

        uebernahme.bereich(ctx, fred, gespeichert)

    # --- Eingabe (rechnet sofort, berechnet wird mit «Kennzahlen berechnen») ---------------
    import portfolio as P2

    v = ss["an_ver"]
    with st.container(border=True):
        st.markdown("**Aufteilung & Budget** · jeder Kauf verringert das verfügbare Budget seiner Anlageklasse")
        a1, a2, a3, a4 = st.columns([1.3, 1, 1, 1])
        a1.text_input("Kapital (USD)", key="an_kap_txt", on_change=_kap_aenderung,
                      help="Zeilen mit fixem Gewicht % passen ihre Stückzahl automatisch an")
        lo_a, hi_a = ctx.alloc["baender"]["aktien"]
        lo_t, hi_t = ctx.alloc["baender"]["treasuries"]
        lo_c, hi_c = ctx.alloc["baender"]["cash"]
        a2.number_input("Aktien %", min_value=0.0, max_value=100.0, step=0.5, format="%.1f", key="an_p_ak",
                        on_change=_basis_aenderung,
                        help=f"IPS-Band {lo_a:g}–{hi_a:g} % · Taktik {ui.de(ctx.alloc['aktien'], 1)} %")
        a3.number_input("Treasuries %", min_value=0.0, max_value=100.0, step=0.5, format="%.1f", key="an_p_tr",
                        help=f"IPS-Band {lo_t:g}–{hi_t:g} % · Taktik {ui.de(ctx.alloc['anleihen'], 1)} %")
        a4.number_input("Cash %", min_value=0.0, max_value=100.0, step=0.5, format="%.1f", key="an_p_ca",
                        help=f"IPS-Band {lo_c:g}–{hi_c:g} % · Taktik {ui.de(ctx.alloc['cash'], 1)} %")
        b = budget(ss)

        def _rest(x):
            return f"<span style='color:{'#B93A32' if x < -0.5 else '#2F855A'}'>{ui.de(x, 0)} USD</span>"
        ui.kopf_kpis([
            ("Aktien verfügbar", _rest(b["rest_a"]), f"Budget {ui.de(b['budget_a'], 0)} · investiert {ui.de(b['inv_a'], 0)} USD"),
            ("Treasuries verfügbar", _rest(b["rest_t"]), f"Budget {ui.de(b['budget_t'], 0)} · investiert {ui.de(b['inv_t'], 0)} USD"),
            ("Cash (Rest)", f"{ui.de(b['frei'], 0)} USD",
             f"{ui.de(b['frei'] / b['kapital'] * 100 if b['kapital'] else 0, 1)} % · Ziel mind. {ui.de(b['cash_ziel'], 0)} USD"),
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
        l, r = st.columns([1.35, 1])
        with l:
            st.markdown("**Aktien** · Ticker oder Firmenname eintippen, dann Stück, Betrag *oder* Gewicht % – der Rest rechnet sich")
            st.radio("«Gewicht %» bezogen auf", [GESAMT, AKTIENTEIL], key="an_bezug", horizontal=True, on_change=_bezug_aenderung,
                     format_func=lambda x: "Gesamtportfolio (Kapital)" if x == GESAMT else "Aktienteil (Kapital × Aktien-%)",
                     help="Gesamtportfolio: 2 % = 2 % des Kapitals (wie Titel-Maximum im IPS). Aktienteil: 2 % = 2 % des "
                          "Aktienbudgets (wie die Sektorgewichte). Beim Umstellen bleiben die Beträge gleich.")
            liste = P2.sp500_liste()
            if liste:
                c1, c2, c2b, c3 = st.columns([2.1, 0.7, 0.7, 1.1])
                such = c1.selectbox("Titel suchen", sorted(liste), index=None, key="an_such", placeholder="z. B. MSFT oder Microsoft",
                                    format_func=lambda t: f"{t} – {liste[t][0]} · {liste[t][1]}", label_visibility="collapsed")
                stk = c2.number_input("Stück", min_value=0, step=10, key="an_such_stk", label_visibility="collapsed",
                                      help="Stückzahl")
                pz = c2b.number_input("%", min_value=0.0, max_value=100.0, step=0.5, format="%.2f", key="an_such_pct",
                                      label_visibility="collapsed", help="oder Gewicht % (hat Vorrang vor Stück)")
                if such:
                    k_ = _letzte_kurse([such]).get(such)
                    bas_ = _basis(ss)
                    stk_ = math.floor(bas_ * pz / 100 / k_) if pz and k_ and bas_ > 0 else stk
                    c3.button(f"+ {such}" + (f" · {ui.de(stk_ * k_, 0)} USD" if k_ and stk_ else ""), on_click=_such_hinzu,
                              width="stretch", type="primary", key="an_such_b")
                    st.caption(f"{liste[such][0]} · {liste[such][1]} · letzter Kurs {_fz(k_, 2) or '–'} USD"
                               + (f" → {ui.de(pz, 2)} % = {ui.de(stk_, 0)} Stück = **{ui.de(stk_ * k_, 0)} USD**" if k_ and pz and stk_
                                  else f" → {ui.de(stk, 0)} Stück = **{ui.de(stk * k_, 0)} USD**" if k_ and stk else ""))
                else:
                    c3.caption("Stück oder % eingeben, dann hinzufügen")
            ak_key = f"an_ed_a{ss.get('an_ak_ver', 0)}"
            st.data_editor(ss["an_aktien"].assign(Löschen=False), num_rows="dynamic", hide_index=True, width="stretch", key=ak_key,
                           on_change=_ak_aenderung, args=(ak_key,), disabled=["Name", "Sektor", "Kurs USD", "Fix"],
                           column_config={
                               "Löschen": st.column_config.CheckboxColumn("✕", help="Häkchen setzen = Zeile entfernen", width="small"),
                               "Ticker": st.column_config.TextColumn(help="Ticker (z. B. MSFT, BRK-B) oder Firmenname (z. B. Microsoft)"),
                               "Name": st.column_config.TextColumn(width="medium"),
                               "Kurs USD": st.column_config.TextColumn(help="letzter Schlusskurs (Yahoo Finance)"),
                               "Menge": st.column_config.TextColumn("Stück", help="Stückzahl → Betrag wird berechnet"),
                               "Betrag USD": st.column_config.TextColumn(help="Betrag → ganze Stücke (abgerundet), Betrag danach exakt"),
                               "Gewicht %": st.column_config.TextColumn(
                                   help="Anteil am Gesamtportfolio bzw. Aktienteil (Umschalter oben). Eingetippt = fix: ändern sich "
                                        "Kapital oder Aktien-%, werden die Stück neu berechnet. Sonst zeigt die Spalte das Ist-Gewicht."),
                               "Fix": st.column_config.TextColumn(width="small",
                                                                  help="Was vorgegeben ist: % (Gewicht bleibt, Stück passen sich an), "
                                                                       "Stück oder USD (Betrag bleibt)")})
            n_ak = sum(1 for t in ss["an_aktien"]["Ticker"] if _txt(t).strip())
            summe = sum(_num(b_) or 0 for b_ in ss["an_aktien"]["Betrag USD"])
            s_pct = sum(_num(p_) or 0 for p_ in ss["an_aktien"]["Gewicht %"])
            ziel_pct = 100.0 if ss.get("an_bezug") == AKTIENTEIL else float(ss.get("an_p_ak") or 0)
            st.caption(f"{n_ak} Titel · Summe Aktien {ui.de(summe, 0)} USD = {ui.de(s_pct, 1)} % von {ui.de(ziel_pct, 1)} % "
                       f"({'Aktienteil' if ss.get('an_bezug') == AKTIENTEIL else 'Aktienquote am Gesamtportfolio'}) · "
                       "Zeile entfernen: Häkchen in der Spalte ✕ (oder Ticker leeren) · Kurse = letzter Schlusskurs; "
                       "gekauft wird am 01.12. zum Eröffnungskurs.")
        with r:
            st.markdown("**Treasuries** · Betrag je Laufzeitband – für die Übernahme ins Portfolio Kupon und Fälligkeit ergänzen")
            tr_key = f"an_ed_t{ss.get('an_tr_ver', 0)}"
            st.data_editor(_treas_df(ss["an_treas"]).assign(Löschen=False), num_rows="dynamic", hide_index=True, width="stretch",
                           key=tr_key, on_change=_tr_aenderung, args=(tr_key,),
                           column_config={
                               "Löschen": st.column_config.CheckboxColumn("✕", help="Häkchen setzen = Zeile entfernen", width="small"),
                               "Laufzeit": st.column_config.SelectboxColumn(options=list(PROXY),
                                                                            help="Mit Fälligkeit: automatisch aus der Restlaufzeit"),
                               "Betrag USD": st.column_config.TextColumn(help="z. B. 3000000 – wird als 3'000'000 angezeigt"),
                               "Kupon %": st.column_config.NumberColumn(min_value=0.0, max_value=15.0, step=0.125, format="%.3f"),
                               "Fälligkeit": st.column_config.DateColumn(format="DD.MM.YYYY"),
                               "Kurs %": st.column_config.NumberColumn(min_value=0.0, format="%.3f",
                                                                       help="leer = Preis aus der Zinskurve am Handelstag"),
                               "Bezeichnung": st.column_config.TextColumn(help="z. B. CUSIP oder «UST 4,25 % 15.11.2029»; "
                                                                               "leer = automatisch")})
            st.caption("Treasuries höchstens Kapital × Treasury-%. Zeile entfernen: Häkchen in der Spalte ✕.")
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
    if los:
        kap = _num(ss.get("an_kap_txt"))
        ss.update(an_kapital=float(kap or 0), an_cash=0.0)
        ss["an_eingabe"] = dict(jahre=jahre, fund=fund, rest=True, aktien=ss["an_aktien"].copy(), treas=_treas_df(ss["an_treas"]),
                                cash=0.0, kapital=float(kap or 0), pct=dict(b["pct"]), bezug=ss.get("an_bezug", GESAMT))
    if "an_eingabe" not in ss:
        st.info("Titel eintragen und auf «Kennzahlen berechnen» klicken. Tipp: Mit «Aktuelles Portfolio übernehmen» siehst du "
                "sofort, wie sich ein geplanter Kauf auf Sharpe Ratio, Risiko und Regeln auswirkt.")
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
    ui.kopf_kpis([
        ("Sharpe Ratio", _zahl(kv.get("sharpe")), f"Aktienteil {_zahl(ka.get('sharpe'))} · neutral {_zahl(kn.get('sharpe'))} · "
                                                   f"S&P 500 {_zahl(ks.get('sharpe'))}"),
        ("Rendite p.a.", _pct(kv.get("rendite"), 1, True), f"Aktienteil {_pct(ka.get('rendite'), 1, True)} · "
                                                            f"neutral {_pct(kn.get('rendite'), 1, True)}"),
        ("Volatilität p.a.", _pct(kv.get("vola")), f"Aktienteil {_pct(ka.get('vola'))} · neutral {_pct(kn.get('vola'))} · "
                                                    f"S&P 500 {_pct(ks.get('vola'))}"),
        ("Max. Drawdown", _pct(kv.get("mdd")), f"Aktienteil {_pct(ka.get('mdd'))} · neutral {_pct(kn.get('mdd'))}"),
    ])
    ui.kopf_kpis([
        ("Beta zum S&P 500", _zahl(kv.get("beta")), f"Aktienteil {_zahl(ka.get('beta'))} · neutral {_zahl(kn.get('beta'))}"),
        ("Tracking Error vs. neutral", _pct(kv.get("te_neutral")), f"Information Ratio {_zahl(kv.get('ir_neutral'))}"),
        ("VaR 95 % · 1 Tag", f"{ui.de(kv.get('var95', 0) / 100 * total, 0)} USD",
         f"{_pct(kv.get('var95'), 2)} · 10 Tage ≈ {ui.de(kv.get('var95', 0) / 100 * total * math.sqrt(10), 0)} USD"),
        ("Aufteilung", f"{(ak_w + f_a) / total * 100:.0f} / {(tr_w + f_t) / total * 100:.0f} / {cash_eff / total * 100:.0f} %",
         f"Aktien / Treasuries / Cash" + (f" (davon investiert {ak_w / total * 100:.0f} / {tr_w / total * 100:.0f} %)" if erg["fuell"] else "")
         + f" · {int((pos['klasse'] == 'Aktie').sum())} Titel, effektiv {ui.de(eff_anzahl(pos.loc[pos['klasse'] == 'Aktie', 'wert']), 1)}"),
    ])

    t1, t2, t3, t4, t5, t6 = st.tabs(["Vergleich", "Einzeltitel", "Korrelation", "Regeln & Struktur", "Gewichte-Vorschlag",
                                      "Speichern"])
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
                    {n_: k[n_] for n_ in ("Neutral (IPS)", "S&P 500")}
        st.dataframe(vergleichstabelle(k), hide_index=True, width="stretch",
                     column_config={"Bedeutung": st.column_config.TextColumn(width="large")})
        st.caption("Vorauswahl = ganzes Portfolio (Aktien, Treasuries, Cash" + (", offenes Budget als Index" if erg["fuell"] else "")
                   + ") · Aktienteil = nur eure Titel, auf 100 % hochgerechnet (so schwankt der Aktienteil für sich allein).")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Wertentwicklung (Start = 100)**")
            fig = go.Figure()
            for name, rr, farbe, breite in (("Vorauswahl", erg["r_p"], ui.BLAU, 2.4), ("Aktienteil", erg["r_a"], ui.BLAU, 1.2),
                                            ("Neutral (IPS)", erg["r_n"], ui.GOLD, 1.8), ("S&P 500", erg["r_s"], ui.STAHL, 1.6)):
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
            for name, rr, farbe in (("Vorauswahl", erg["r_p"], ui.BLAU), ("Neutral (IPS)", erg["r_n"], ui.GOLD)):
                w = (1 + rr).cumprod()
                dd = (w / w.cummax() - 1) * 100
                fig.add_trace(go.Scatter(x=dd.index, y=dd, name=name, line=dict(color=farbe, width=1.6),
                                         fill="tozeroy" if name == "Vorauswahl" else None,
                                         fillcolor="rgba(31,78,140,0.10)"))
            fig.update_layout(height=320, margin=dict(l=8, r=8, t=10, b=8), hovermode="x unified")
            fig.update_yaxes(ticksuffix=" %")
            ui.zeige(fig)
        st.caption(f"Zeitraum {kv['von']:%d.%m.%Y} – {kv['bis']:%d.%m.%Y} ({kv['tage']} Handelstage) · risikofreier Zins "
                   f"Ø {ui.de(kv['rf'], 2)} % (13-Wochen-T-Bill) · dividendenbereinigte Kurse, konstante Gewichte · "
                   f"Neutral = {ctx.alloc['neutral']['aktien']:.0f} % S&P 500 / "
                   f"{100 - ctx.alloc['neutral']['aktien'] - ctx.alloc['neutral'].get('cash', 5):.0f} % Treasuries ({NEUTRAL_PROXY}) / "
                   f"{ctx.alloc['neutral'].get('cash', 5):.0f} % Cash. Vergangene Werte sind keine Prognose. Kurse {zeit}.")

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
            oben = kor.where(np.triu(np.ones(kor.shape, dtype=bool), 1)).stack().sort_values(ascending=False)
            oben = oben[[a != SPX and b_ != SPX for a, b_ in oben.index]]
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
        n_rot = sum(1 for s_, _ in checks if s_ == L.ROT)
        st.markdown(f"**Prüfung gegen eure Regeln** · {ui.badge(L.ROT if n_rot else L.GRUEN, f'{n_rot} Verstösse' if n_rot else 'alles im Rahmen')}",
                    unsafe_allow_html=True)
        st.markdown("".join(f'<div class="karte-zeile"><span>{ui.badge(s_, {L.ROT: "Verstoss", L.GELB: "Hinweis", L.GRUEN: "ok"}[s_])}'
                            f'&nbsp; {txt}</span><span></span></div>' for s_, txt in checks), unsafe_allow_html=True)
        st.markdown("")
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
        vs = vorschlag(erg["R"], erg["rf"], erg["gew"], erg["cash"], fix=set(erg["fuell"]) - set(pos["ticker"]))
        if vs is None:
            st.caption("Für einen Vorschlag mindestens zwei Aktien eingeben.")
        else:
            regel = ("ohne Klumpen-Grenze, weil sie mit so wenigen Titeln nicht einhaltbar ist" if vs["ohne_deckel"]
                     else f"kein Titel über {KLUMPEN:.0f} % des Portfolios")
            st.markdown(f"Aus {ui.de(vs['n'], 0)} zufälligen Gewichtungen des Aktienteils (Treasuries, Cash"
                        f"{' und offenes Budget' if erg['fuell'] else ''} bleiben fix, {regel}): "
                        "Welche hätte im gewählten Zeitraum die beste Sharpe Ratio bzw. die tiefste Volatilität gehabt?")
            zeilen = []
            for t in vs["aktien"]:
                kurs = letzte.get(t)
                zeilen.append({"Titel": t, "Heute": _pct(erg["gew"][t] * 100),
                               "Max. Sharpe": _pct(vs["max_sharpe"][t] * 100),
                               "Stück (Max. Sharpe)": ui.de(vs["max_sharpe"][t] * total / kurs, 0) if kurs else "–",
                               "Min. Volatilität": _pct(vs["min_vola"][t] * 100),
                               "Stück (Min. Vola)": ui.de(vs["min_vola"][t] * total / kurs, 0) if kurs else "–"})
            st.dataframe(pd.DataFrame(zeilen), hide_index=True, width="stretch")
            ui.kopf_kpis([("Sharpe heute", _zahl(kv.get("sharpe")), f"Vola {_pct(kv.get('vola'))}"),
                          ("Sharpe mit Max.-Sharpe-Gewichten", _zahl(vs["sharpe"][0]), f"Vola {_pct(vs['vola'][0])}"),
                          ("Sharpe mit Min.-Vola-Gewichten", _zahl(vs["sharpe"][1]), f"Vola {_pct(vs['vola'][1])}")])
            st.caption("Achtung: Die Gewichte sind auf die Vergangenheit optimiert und reagieren stark auf den Zeitraum. "
                       "Als Denkanstoss nutzen, nicht als Handelsanweisung – Entscheid im Team mit Begründung erfassen.")

    with t6:
        if ss.get("nur_lesen"):
            st.caption("Mit Lesezugriff kann nichts gespeichert werden.")
        with st.form("an_speichern", clear_on_submit=True):
            name = st.text_input("Name der Vorauswahl", placeholder="z. B. Energie + Gesundheit übergewichtet, Variante A")
            notiz = st.text_area("Notiz / Begründung", height=80)
            if st.form_submit_button("Vorauswahl speichern", type="primary", icon=":material/save:"):
                if not name.strip():
                    st.error("Bitte einen Namen angeben.")
                else:
                    a_ = aktien[[bool(_txt(t).strip()) for t in aktien["Ticker"]]]
                    t_ = treas[[_num(x) is not None for x in treas["Betrag USD"]]]
                    eingabe = dict(aktien=[{"Ticker": _txt(z["Ticker"]), "Menge": _num(z.get("Menge")),
                                            "Betrag USD": _num(z.get("Betrag USD")), "Gewicht %": _num(z.get("Gewicht %")),
                                            "Fix": _txt(z.get("Fix"))} for z in a_.to_dict("records")],
                                   treas=[{"Laufzeit": _txt(z["Laufzeit"]), "Betrag USD": _num(z["Betrag USD"]),
                                           "Kupon %": _num(z.get("Kupon %")), "Kurs %": _num(z.get("Kurs %")),
                                           "Fälligkeit": pd.Timestamp(z["Fälligkeit"]).date().isoformat()
                                           if z.get("Fälligkeit") is not None and pd.notna(z.get("Fälligkeit")) else "",
                                           "Bezeichnung": _txt(z.get("Bezeichnung"))}
                                          for z in t_.to_dict("records")],
                                   cash=float(e["cash"]), kapital=float(e["kapital"]), jahre=int(e["jahre"]),
                                   pct=e.get("pct") or dict(budget(ss)["pct"]), bezug=e.get("bezug", GESAMT), fuellen=fuellen)
                    kz = {k_: (round(v_, 4) if isinstance(v_, float) else v_) for k_, v_ in kv.items()
                          if k_ not in ("von", "bis")}
                    kz.update(aktien=round(ak_w / total * 100, 1), treasuries=round(tr_w / total * 100, 1),
                              cash=round(cash / total * 100, 1), total=round(total, 0),
                              von=f"{kv['von']:%Y-%m-%d}", bis=f"{kv['bis']:%Y-%m-%d}", fuellen=fuellen,
                              vola_aktien=round(float(ka["vola"]), 4) if ka.get("vola") is not None else None,
                              sharpe_aktien=round(float(ka["sharpe"]), 4) if ka.get("sharpe") is not None else None)
                    ctx.speichern("analysen", [dict(zeit=jetzt(), person=ctx.person, id=uuid.uuid4().hex[:8], name=name.strip(),
                                                    eingabe=json.dumps(eingabe, ensure_ascii=False),
                                                    kennzahlen=json.dumps(kz, ensure_ascii=False), notiz=notiz.strip())],
                                  f"Vorauswahl «{name.strip()}» gespeichert.")
        if len(gespeichert):
            zeilen = []
            for _, z in gespeichert.iterrows():
                try:
                    kz = json.loads(z["kennzahlen"])
                except Exception:  # noqa: BLE001
                    kz = {}
                zeilen.append({"Datum": str(z["zeit"])[:10], "Name": z["name"], "Von": z["person"],
                               "Sharpe": _zahl(kz.get("sharpe")), "Rendite p.a.": _pct(kz.get("rendite"), 1, True),
                               "Vola": _pct(kz.get("vola")), "Vola Aktienteil": _pct(kz.get("vola_aktien")),
                               "Beta": _zahl(kz.get("beta")),
                               "Aktien/Treas./Cash": f"{kz.get('aktien', '–')} / {kz.get('treasuries', '–')} / {kz.get('cash', '–')}",
                               "Notiz": z["notiz"]})
            st.markdown("**Gespeicherte Vorauswahlen**")
            st.dataframe(pd.DataFrame(zeilen), hide_index=True, width="stretch")
