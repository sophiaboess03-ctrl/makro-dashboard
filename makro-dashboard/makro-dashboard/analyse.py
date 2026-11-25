"""Analyse mehrerer Analyseportfolios (oder des aktuellen Portfolios): Rendite-, Risiko- und Regelkennzahlen aus Kursdaten.

Methodik (auch in der App erklärt):
- Tagesschlusskurse von Yahoo Finance, dividendenbereinigt (Total Return).
- Konstante Gewichte (tägliches Rebalancing), annualisiert mit 252 Handelstagen.
- Risikofreier Zins: 13-Wochen-T-Bill (^IRX), Ersatzwert 4 %.
- Treasuries über liquide ETFs je Laufzeitband (Näherung), Cash verzinst zum risikofreien Zins.
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

    titel, gesehen = [], set()
    for r in rows[kopf + 1:]:
        t = r[c_t] if c_t < len(r) else None
        if t is None or not str(t).strip():
            break
        tk = str(t).strip().upper().replace(".", "-")
        if tk in gesehen:
            continue
        gesehen.add(tk)
        titel.append(dict(ticker=tk, akt=_w(r, c_a), ges=_w(r, c_g),
                          sektor=str(r[c_s] or "") if c_s is not None else "", name=str(r[c_n] or "") if c_n is not None else ""))
    if not titel:
        return dict(fehler="Keine Titel gefunden.")
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
    return dict(titel=titel, quote=quote, summe_akt=s_a * faktor, summe_ges=s_g * faktor)


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
    ohne = [_txt(t) for t, k in zip(ss["an_aktien"]["Ticker"], ss["an_aktien"]["Kurs USD"]) if _txt(t).strip() and not _num(k)]
    n_t = sum(1 for x in d["titel"] if x["akt"])
    _meldung(f"{n_t} Titel aus dem Scoring-Modell übernommen · Bezug «Aktienteil» · Aktien {ui.de(q, 2)} % · Treasuries "
             f"{ui.de(max(0.0, 100 - q - ca), 2)} % · Cash {ui.de(ca, 2)} %."
             + (f" Ohne Kurs (Ticker prüfen): {', '.join(ohne)}." if ohne else ""))


def _scoring_bereich():
    ss = st.session_state
    with st.expander("Titel aus dem Scoring-Modell übernehmen (Excel)", icon=":material/upload_file:"):
        st.caption("Liest im Blatt «Portfolio» Ticker und «Gewicht Aktienteil» mit voller Genauigkeit und die Aktienquote "
                   "(«Soll: Aktienquote»). So rechnet die App genau wie das Modell: Gewicht Gesamtportfolio = Aktienteil × Aktienquote.")
        f = st.file_uploader("Scoring-Modell (.xlsx)", type=["xlsx"], key=f"an_sc_up{ss.get('an_ver', 0)}",
                             label_visibility="collapsed")
        if f is None:
            return
        sig = [f.name, f.size]
        if (ss.get("an_sc") or {}).get("sig") != sig:
            ss["an_sc"] = dict(sig=sig, **scoring_lesen(f))
        d = ss["an_sc"]
        if d.get("fehler"):
            st.error(d["fehler"], icon=":material/error:")
            return
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


def _sektor_bereich(ctx):
    """Sektorgewichtung des aktiven Analyseportfolios: automatisch gerechnet, im Formular anpassbar."""
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
    with st.expander("Sektorgewichtung dieses Portfolios – automatisch gerechnet, anpassbar", icon=":material/donut_small:",
                     expanded=bool(ss.get("an_sek_vorschlag"))):
        if not ist:
            st.caption("Sobald Aktien mit Betrag eingetragen (und übernommen) sind, erscheint hier die Sektorgewichtung.")
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


# ---------------------------------------------------------------------------
# Mehrere Analyseportfolios
# Gespeichert im Blatt «analysen» (je id gilt die neuste Zeile; Löschen = neue Zeile mit «geloescht» in der Eingabe).
# In der Sitzung sind mehrere Portfolios gleichzeitig offen: das aktive liegt in den an_*-Schlüsseln, die übrigen
# als Kopie in st.session_state["an_pf"][id]["state"] – Wechseln verliert also keine ungespeicherten Änderungen.
# ---------------------------------------------------------------------------
PF_KEYS = ("an_aktien", "an_treas", "an_cash", "an_kapital", "an_kap_txt", "an_p_ak", "an_p_tr", "an_p_ca", "an_bezug",
           "an_fuellen", "an_eingabe", "an_kz", "an_modell_quote")
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
                "Bezeichnung": _txt(z.get("Bezeichnung"))} for z in t_.to_dict("records")],
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
              sharpe_aktien=round(float(ka["sharpe"]), 4) if ka.get("sharpe") is not None else None)
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
        st.markdown("**Portfolios vergleichen** · alle im gleichen Zeitfenster, gegen das neutrale Portfolio (IPS) und den S&P 500")
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
    neutral_cols = ["Neutral (IPS)", "S&P 500"]

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
                             f"{ctx.alloc['neutral'].get('cash', 5):.0f} %", "S&P 500": "100 / 0 / 0 %"}),
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
        zeilen.append(_z(name, werte, erkl, {c: f(k0[c].get(key)) if key in k0[c] else "–" for c in neutral_cols}))
    for key, name in (("vola", "Volatilität Aktienteil"), ("sharpe", "Sharpe Aktienteil"), ("beta", "Beta Aktienteil")):
        fmt = _pct if key == "vola" else _zahl
        zeilen.append(_z(name, {i: fmt(erg[i]["k"].get("Aktienteil", {}).get(key)) for i in erg}, "nur eure Titel, auf 100 % hochgerechnet"))
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

    farbe = {i: PF_FARBEN[n % len(PF_FARBEN)] for n, i in enumerate(erg)}
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Wertentwicklung (Start = 100)**")
        fig = go.Figure()
        for i, e_ in erg.items():
            w = (1 + e_["r_p"]).cumprod() * 100
            fig.add_trace(go.Scatter(x=w.index, y=w, name=vg["namen"][i], line=dict(color=farbe[i], width=2)))
        for name, rr, fb in (("Neutral (IPS)", erstes["r_n"], ui.GOLD), ("S&P 500", erstes["r_s"], ui.STAHL)):
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
        for name, fb in (("Neutral (IPS)", ui.GOLD), ("S&P 500", ui.STAHL)):
            kv = k0[name]
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
        gemeinsam = [t for t, w in tz.items() if len(w) == len(spalten)]
        if len(spalten) > 1:
            st.caption(f"In allen gewählten Portfolios: {', '.join(sorted(gemeinsam)) if gemeinsam else 'kein Titel'} · "
                       f"{len(tz)} verschiedene Titel insgesamt.")


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
                  "(strategische Allokation laut IPS) und dem S&P 500.")
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

    ansicht = st.segmented_control("Ansicht", ["bearbeiten", "vergleich"], default="bearbeiten", key="an_ansicht",
                                   format_func=lambda x: "Portfolio bearbeiten & analysieren" if x == "bearbeiten"
                                   else "Portfolios vergleichen", label_visibility="collapsed") or "bearbeiten"
    if ansicht == "vergleich":
        _seite_vergleich(ctx, tab, gs, fred, lade_kurse)
        _alle_portfolios(ctx, tab)
        for k in WIDGET_KEYS:
            if k in ss:
                ss["_" + k] = ss[k]
        return

    _portfolio_leiste(ctx, tab, kapital_std, ctx.alloc, gs, fred, lade_kurse)
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

        with st.form("an_eingabe_form", border=False, enter_to_submit=False):
            a1, a2, a3, a4 = st.columns([1.3, 1, 1, 1])
            a1.text_input("Kapital (USD)", value=_txt(ss.get("an_kap_txt")), key=f"an_f_kap{sfx}",
                          help="Zeilen mit fixem Gewicht % passen ihre Stückzahl beim Übernehmen automatisch an")
            a2.number_input("Aktien %", min_value=0.0, max_value=100.0, step=0.5, format="%.1f", value=float(ss.get("an_p_ak") or 0),
                            key=f"an_f_pak{sfx}", help=f"IPS-Band {lo_a:g}–{hi_a:g} % · Taktik {ui.de(ctx.alloc['aktien'], 1)} %")
            a3.number_input("Treasuries %", min_value=0.0, max_value=100.0, step=0.5, format="%.1f",
                            value=float(ss.get("an_p_tr") or 0), key=f"an_f_ptr{sfx}",
                            help=f"IPS-Band {lo_t:g}–{hi_t:g} % · Taktik {ui.de(ctx.alloc['anleihen'], 1)} %")
            a4.number_input("Cash %", min_value=0.0, max_value=100.0, step=0.5, format="%.1f", value=float(ss.get("an_p_ca") or 0),
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
                st.markdown("**Treasuries** · Betrag je Laufzeitband – für die Übernahme ins Portfolio Kupon und Fälligkeit ergänzen")
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
                                   "Bezeichnung": st.column_config.TextColumn(help="z. B. CUSIP oder «UST 4,25 % 15.11.2029»; "
                                                                                   "leer = automatisch")})
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
        st.caption(f"{n_ak} Titel · Summe Aktien {ui.de(summe, 0)} USD · Gewicht % (Ziel) {ui.de(s_pct, 2)} % · Ist "
                   f"{ui.de(ist_pct, 2)} % von {ui.de(ziel_pct, 1)} % "
                   f"({'Aktienteil' if teil else 'Aktienquote am Gesamtportfolio'}) · "
                   "Zeile entfernen: Häkchen in der Spalte ✕ (oder Ticker leeren) · Kurse = letzter Schlusskurs; "
                   "gekauft wird am 01.12. zum Eröffnungskurs.")
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

    ss["an_kz"] = dict(hash=_hash(_kern(aktien, treas, e["kapital"], e.get("pct") or {}, e.get("bezug", GESAMT))),
                       kz=_kz_zusammenfassen(kv, ka, ak_w, tr_w, cash, total, fuellen))
    t1, t2, t3, t4, t5 = st.tabs(["Vergleich", "Einzeltitel", "Korrelation", "Regeln & Struktur", "Gewichte-Vorschlag"])
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
