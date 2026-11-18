"""Analyse einer Vorauswahl (oder des aktuellen Portfolios): Rendite-, Risiko- und Regelkennzahlen aus Kursdaten.

Methodik (auch in der App erklärt):
- Tagesschlusskurse von Yahoo Finance, dividendenbereinigt (Total Return).
- Konstante Gewichte (tägliches Rebalancing), annualisiert mit 252 Handelstagen.
- Risikofreier Zins: 13-Wochen-T-Bill (^IRX), Ersatzwert 4 %.
- Treasuries über liquide ETFs je Laufzeitband (Näherung), Cash verzinst zum risikofreien Zins.
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
    wealth = (1 + r).cumprod()
    dd = wealth / wealth.cummax() - 1
    q5, q1 = r.quantile(0.05), r.quantile(0.01)
    downside = math.sqrt(float((np.minimum(ex, 0) ** 2).mean())) * math.sqrt(TAGE)
    out = dict(
        rendite=float(wealth.iloc[-1] ** (TAGE / n) - 1) * 100,
        gesamt=float(wealth.iloc[-1] - 1) * 100,
        vola=sd * math.sqrt(TAGE) * 100,
        sharpe=float(ex.mean() / sd * math.sqrt(TAGE)),
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


def rechne(pos: pd.DataFrame, cash: float, total: float, hist: dict, neutral: dict) -> dict | None:
    """Alle Kennzahlen für Vorauswahl, neutrales IC-Portfolio und S&P 500 im gleichen Zeitfenster."""
    if total <= 0:
        return None
    gew = {t: float(w) / total for t, w in pos.groupby("ticker")["wert"].sum().items()} if len(pos) else {}
    alle = sorted(set(gew) | {SPX, NEUTRAL_PROXY})
    R, start = renditen(hist, alle)
    if R.empty or len(R) < 20:
        return None
    rf = rf_reihe(hist, R.index)
    c = cash / total
    r_p = portfolio_reihe(R, rf, gew, c)
    na, nt = neutral["aktien"] / 100, neutral.get("cash", 5) / 100
    r_n = portfolio_reihe(R, rf, {SPX: na, NEUTRAL_PROXY: 1 - na - nt}, nt)
    r_s = R[SPX]
    k = {"Vorauswahl": kennzahlen(r_p, rf, r_s), "Neutral (IPS)": kennzahlen(r_n, rf, r_s),
         "S&P 500": kennzahlen(r_s, rf, r_s)}
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
    return dict(R=R, rf=rf, r_p=r_p, r_n=r_n, r_s=r_s, k=k, einzel=einzel, gew=gew, cash=c, start=start,
                beitrag=risikobeitraege(R, gew))


def vorschlag(R: pd.DataFrame, rf: pd.Series, gew: dict, cash: float, n: int = 4000, seed: int = 1) -> dict | None:
    """Zufallsportfolios im Aktienteil (Treasuries und Cash fix): beste Sharpe Ratio und tiefste Volatilität."""
    aktien = [t for t in gew if t in R.columns and t not in {v[0] for v in PROXY.values()}]
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
    """Regel-Check gegen IPS-Bänder, Mandat (S&P 500), Klumpen, Duration und Sektorempfehlung. (Stufe, Text)."""
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
    emp = dict(zip(alloc["sektoren"]["sektor"], alloc["sektoren"]["empfehlung"]))
    sek = {}
    for _, z in pos[pos["klasse"] == "Aktie"].iterrows():
        s = info.get(z["ticker"], {}).get("sektor") or "Unbekannt"
        sek[s] = sek.get(s, 0) + z["wert"]
    aw = sum(sek.values())
    for s, w in sorted(sek.items(), key=lambda x: -x[1]):
        e = emp.get(s)
        if e == "Untergewichten" and aw and w / aw > 0.15:
            out.append((L.GELB, f"{s}: {ui.de(w / aw * 100, 0)} % des Aktienteils, Taktik «Untergewichtet»"))
    fehlt = [s for s, e in emp.items() if e == "Übergewichten" and s not in sek]
    if fehlt and aw and "Unbekannt" not in sek:
        out.append((L.GELB, "Empfohlene Übergewichte ohne Titel: " + ", ".join(fehlt)))
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


def _leer_treas():
    return pd.DataFrame({"Laufzeit": ["1–3 J.", "3–7 J.", "7–10 J."], "Betrag USD": [None, None, None]}).astype(
        {"Laufzeit": "string", "Betrag USD": "float64"})


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
        ss.update(an_aktien=_leer_aktien(), an_treas=_leer_treas(), an_cash=0.0, an_kapital=float(kapital_std))

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
            ss.update(an_aktien=_leer_aktien(), an_treas=_leer_treas(), an_cash=0.0)
        elif wahl == "portfolio":
            v = portfolio_vorlage(tab, fred, lade_kurse)
            if v is None:
                ss["an_meldung"] = "Im Portfolio sind noch keine Transaktionen erfasst."
            else:
                ss.update(an_aktien=v[0], an_treas=v[1], an_cash=max(0.0, v[2]), an_kapital=v[3])
        else:
            z = gespeichert.set_index("id").loc[wahl]
            e = json.loads(z["eingabe"])
            a_ = pd.DataFrame(e["aktien"], columns=["Ticker", "Menge", "Betrag USD"])
            t_ = pd.DataFrame(e["treas"], columns=["Laufzeit", "Betrag USD"])
            ss.update(an_aktien=pd.concat([a_, _leer_aktien(2)], ignore_index=True).astype(
                          {"Ticker": "string", "Menge": "float64", "Betrag USD": "float64"}),
                      an_treas=(t_ if len(t_) else _leer_treas()).astype({"Laufzeit": "string", "Betrag USD": "float64"}),
                      an_cash=float(e.get("cash", 0)), an_kapital=float(e.get("kapital", kapital_std)))
        ss["an_ver"] += 1
        ss.pop("an_eingabe", None)
        st.rerun()

    if m := ss.pop("an_meldung", None):
        st.warning(m)

    # --- Eingabe -------------------------------------------------------------
    v = ss["an_ver"]
    with st.form("an_form", border=True):
        l, r = st.columns([1.35, 1])
        with l:
            st.markdown("**Aktien** · Menge in Stück *oder* Betrag in USD")
            aktien = st.data_editor(ss["an_aktien"], num_rows="dynamic", hide_index=True, width="stretch", key=f"an_ed_a{v}",
                                    column_config={
                                        "Ticker": st.column_config.TextColumn(help="z. B. MSFT, XOM, BRK-B"),
                                        "Menge": st.column_config.NumberColumn("Menge (Stück)", min_value=0, step=1, format="%.0f"),
                                        "Betrag USD": st.column_config.NumberColumn(min_value=0, step=1000, format="%.0f")})
        with r:
            st.markdown("**Treasuries** · Betrag je Laufzeitband")
            treas = st.data_editor(ss["an_treas"], num_rows="dynamic", hide_index=True, width="stretch", key=f"an_ed_t{v}",
                                   column_config={
                                       "Laufzeit": st.column_config.SelectboxColumn(options=list(PROXY), required=True),
                                       "Betrag USD": st.column_config.NumberColumn(min_value=0, step=100000, format="%.0f")})
            k1, k2 = st.columns(2)
            kapital = k1.number_input("Kapital (USD)", min_value=0.0, value=float(ss["an_kapital"]), step=100000.0,
                                      format="%.0f", key=f"an_kap{v}")
            cash = k2.number_input("Cash (USD)", min_value=0.0, value=float(ss["an_cash"]), step=100000.0, format="%.0f",
                                   key=f"an_cash{v}")
            rest = st.checkbox("Nicht investierten Rest als Cash rechnen", value=True, key=f"an_rest{v}")
        o1, o2, o3 = st.columns([1.3, 1.2, 1])
        jahre = o1.segmented_control("Zeitraum der Kursdaten", [1, 2, 3, 5], default=1, format_func=lambda j: f"{j} J.",
                                     key=f"an_j{v}") or 1
        fund = o2.toggle("Bewertung laden (KGV, Dividende)", value=False, key=f"an_f{v}",
                         help="Holt pro Titel Kennzahlen von Yahoo Finance – dauert einige Sekunden.")
        los = o3.form_submit_button("Kennzahlen berechnen", type="primary", icon=":material/calculate:", width="stretch")
    if los:
        ss.update(an_aktien=aktien, an_treas=treas, an_cash=cash, an_kapital=kapital)
        ss["an_eingabe"] = dict(jahre=jahre, fund=fund, rest=rest)
    if "an_eingabe" not in ss:
        st.info("Titel eintragen und auf «Kennzahlen berechnen» klicken. Tipp: Mit «Aktuelles Portfolio übernehmen» siehst du "
                "sofort, wie sich ein geplanter Kauf auf Sharpe Ratio, Risiko und Regeln auswirkt.")
        return
    e = ss["an_eingabe"]
    aktien, treas = ss["an_aktien"], ss["an_treas"]

    # --- Daten & Rechnung -------------------------------------------------------
    ticker = sorted({_txt(t).strip().upper().replace(".", "-") for t in aktien["Ticker"] if _txt(t).strip()})
    proxies = sorted({PROXY[_txt(b)][0] for b in treas["Laufzeit"] if _txt(b) in PROXY})
    hist, fehlt, zeit = lade_historie(tuple(ticker + proxies + [SPX, NEUTRAL_PROXY, RF_TICKER]), int(e["jahre"]))
    if SPX not in hist:
        st.error("Kursdaten von Yahoo Finance sind gerade nicht erreichbar. Bitte in einer Minute nochmals versuchen.")
        return
    letzte = {t: float(s.iloc[-1]) for t, s in hist.items()}
    pos, cash, total, hinweise = zusammenstellen(aktien, treas, ss["an_cash"], ss["an_kapital"], e["rest"], letzte)
    for h in hinweise:
        st.warning(h)
    if total <= 0 or not len(pos):
        st.warning("Bitte mindestens eine Aktie oder einen Treasury-Betrag eingeben.")
        return
    erg = rechne(pos, cash, total, hist, ctx.alloc["neutral"])
    if erg is None:
        st.error("Zu wenige gemeinsame Kursdaten – Zeitraum vergrössern oder Ticker prüfen.")
        return
    info = {t: P.ticker_info(t) for t in pos.loc[pos["klasse"] == "Aktie", "ticker"]}
    kv, kn, ks = erg["k"]["Vorauswahl"], erg["k"]["Neutral (IPS)"], erg["k"]["S&P 500"]
    if erg["start"] is not None and erg["start"] > hist[SPX].index.min() + pd.Timedelta(days=10):
        st.caption(f"Gemeinsames Zeitfenster ab {erg['start']:%d.%m.%Y} (ein Titel hat eine kürzere Kursgeschichte).")

    ak_w = float(pos.loc[pos["klasse"] == "Aktie", "wert"].sum())
    tr_w = float(pos.loc[pos["klasse"] == "Treasury", "wert"].sum())
    ui.kopf_kpis([
        ("Sharpe Ratio", _zahl(kv.get("sharpe")), f"neutral {_zahl(kn.get('sharpe'))} · S&P 500 {_zahl(ks.get('sharpe'))}"),
        ("Rendite p.a.", _pct(kv.get("rendite"), 1, True), f"neutral {_pct(kn.get('rendite'), 1, True)}"),
        ("Volatilität p.a.", _pct(kv.get("vola")), f"neutral {_pct(kn.get('vola'))}"),
        ("Max. Drawdown", _pct(kv.get("mdd")), f"neutral {_pct(kn.get('mdd'))}"),
    ])
    ui.kopf_kpis([
        ("Beta zum S&P 500", _zahl(kv.get("beta")), f"neutral {_zahl(kn.get('beta'))}"),
        ("Tracking Error vs. neutral", _pct(kv.get("te_neutral")), f"Information Ratio {_zahl(kv.get('ir_neutral'))}"),
        ("VaR 95 % · 1 Tag", f"{ui.de(kv.get('var95', 0) / 100 * total, 0)} USD",
         f"{_pct(kv.get('var95'), 2)} · 10 Tage ≈ {ui.de(kv.get('var95', 0) / 100 * total * math.sqrt(10), 0)} USD"),
        ("Aufteilung", f"{ak_w / total * 100:.0f} / {tr_w / total * 100:.0f} / {cash / total * 100:.0f} %",
         f"Aktien / Treasuries / Cash · {int((pos['klasse'] == 'Aktie').sum())} Titel, effektiv "
         f"{ui.de(eff_anzahl(pos.loc[pos['klasse'] == 'Aktie', 'wert']), 1)}"),
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
                k = {"Vorauswahl": k["Vorauswahl"], "Aktuelles Portfolio": e2["k"]["Vorauswahl"],
                     "Neutral (IPS)": k["Neutral (IPS)"], "S&P 500": k["S&P 500"]}
        st.dataframe(vergleichstabelle(k), hide_index=True, width="stretch",
                     column_config={"Bedeutung": st.column_config.TextColumn(width="large")})
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Wertentwicklung (Start = 100)**")
            fig = go.Figure()
            for name, rr, farbe, breite in (("Vorauswahl", erg["r_p"], ui.BLAU, 2.4), ("Neutral (IPS)", erg["r_n"], ui.GOLD, 1.8),
                                            ("S&P 500", erg["r_s"], ui.STAHL, 1.6)):
                w = (1 + rr).cumprod() * 100
                fig.add_trace(go.Scatter(x=w.index, y=w, name=name, line=dict(color=farbe, width=breite)))
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
        zeilen = []
        for _, z in pos.iterrows():
            t = z["ticker"]
            ke = erg["einzel"].get(t, {})
            i = info.get(t, {})
            f = fd.get(t, {})
            zeilen.append({
                "Titel": t if z["klasse"] == "Aktie" else f"Treasuries {z['band']} ({t})",
                "Name": i.get("name", "") if z["klasse"] == "Aktie" else "ETF-Näherung",
                "Sektor": i.get("sektor", "") if z["klasse"] == "Aktie" else "Treasury",
                "Menge": ui.de(z["menge"], 0) if z["klasse"] == "Aktie" else "–",
                "Kurs": ui.de(z["kurs"], 2) if z["klasse"] == "Aktie" else "–",
                "Wert USD": ui.de(z["wert"], 0), "Gewicht": _pct(z["wert"] / total * 100),
                "Risikobeitrag": _pct(erg["beitrag"].get(t)),
                "Rendite p.a.": _pct(ke.get("rendite"), 1, True), "Vola p.a.": _pct(ke.get("vola")),
                "Sharpe": _zahl(ke.get("sharpe")), "Beta": _zahl(ke.get("beta")), "Max. DD": _pct(ke.get("mdd")),
                "Korr. zum Rest": _zahl(ke.get("korr_rest")),
                **({"KGV fwd": _zahl(f.get("kgv_fwd"), 1), "Div.-Rendite": _pct((f.get("div") or 0) * 100 if f.get("div") is not None else None, 2)}
                   if e["fund"] else {})})
        st.dataframe(pd.DataFrame(zeilen), hide_index=True, width="stretch")
        st.caption("Risikobeitrag = Anteil am Gesamtrisiko (Varianz) des Portfolios. Liegt er deutlich über dem Gewicht, "
                   "treibt der Titel das Risiko überproportional. Korr. zum Rest = Korrelation mit dem übrigen Portfolio "
                   "(tief = guter Diversifikationsbeitrag).")
        b = erg["beitrag"]
        if len(b):
            g = pd.Series(erg["gew"]) * 100
            ordnung = b.sort_values(ascending=True).index
            lab = [PROXY_NAME.get(t, t) for t in ordnung]
            fig = go.Figure([go.Bar(y=lab, x=g.reindex(ordnung), name="Gewicht", orientation="h", marker_color=ui.STAHL),
                             go.Bar(y=lab, x=b.reindex(ordnung), name="Risikobeitrag", orientation="h",
                                    marker_color=ui.BLAU)])
            fig.update_layout(barmode="group", height=max(260, 34 * len(b) + 80), margin=dict(l=8, r=8, t=10, b=8),
                              hovermode="y unified")
            fig.update_xaxes(ticksuffix=" %")
            ui.zeige(fig)

    with t3:
        spalten = [t for t in erg["gew"] if t in erg["R"].columns] + [SPX]
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
        checks = pruefungen(pos, cash, total, info, ctx.alloc)
        n_rot = sum(1 for s_, _ in checks if s_ == L.ROT)
        st.markdown(f"**Prüfung gegen eure Regeln** · {ui.badge(L.ROT if n_rot else L.GRUEN, f'{n_rot} Verstösse' if n_rot else 'alles im Rahmen')}",
                    unsafe_allow_html=True)
        st.markdown("".join(f'<div class="karte-zeile"><span>{ui.badge(s_, {L.ROT: "Verstoss", L.GELB: "Hinweis", L.GRUEN: "ok"}[s_])}'
                            f'&nbsp; {txt}</span><span></span></div>' for s_, txt in checks), unsafe_allow_html=True)
        st.markdown("")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Sektoren im Aktienteil**")
            emp = dict(zip(ctx.alloc["sektoren"]["sektor"], ctx.alloc["sektoren"]["empfehlung"]))
            sek = pd.Series({t: info.get(t, {}).get("sektor") or "Unbekannt"
                             for t in pos.loc[pos["klasse"] == "Aktie", "ticker"]})
            werte = pos[pos["klasse"] == "Aktie"].set_index("ticker")["wert"]
            gw = werte.groupby(sek.reindex(werte.index)).sum() / ak_w * 100 if ak_w else pd.Series(dtype=float)
            zeilen = [{"Sektor": s, "Gewicht": _pct(gw.get(s, 0.0)),
                       "S&P 500": _pct(bm.get(s)) if bm.get(s) is not None else "–",
                       "Abweichung": f"{ui.de(gw.get(s, 0.0) - bm[s], 1, True)} PP" if bm.get(s) is not None else "–",
                       "Taktik": emp.get(s, "–")} for s in C.SEKTOREN + (["Unbekannt"] if "Unbekannt" in gw.index else [])]
            st.dataframe(pd.DataFrame(zeilen), hide_index=True, width="stretch", height=430)
            if not bm:
                st.caption("S&P-500-Sektorgewichte auf der Seite «Portfolio» unter «Einstellungen Portfolio» eintragen, "
                           "dann erscheint die Abweichung zum Index.")
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
        vs = vorschlag(erg["R"], erg["rf"], erg["gew"], erg["cash"])
        if vs is None:
            st.caption("Für einen Vorschlag mindestens zwei Aktien eingeben.")
        else:
            regel = ("ohne Klumpen-Grenze, weil sie mit so wenigen Titeln nicht einhaltbar ist" if vs["ohne_deckel"]
                     else f"kein Titel über {KLUMPEN:.0f} % des Portfolios")
            st.markdown(f"Aus {ui.de(vs['n'], 0)} zufälligen Gewichtungen des Aktienteils (Treasuries und Cash bleiben fix, {regel}): "
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
                    eingabe = dict(aktien=[{k_: (_num(v_) if k_ != "Ticker" else _txt(v_)) for k_, v_ in z.items()}
                                           for z in a_.to_dict("records")],
                                   treas=[{"Laufzeit": _txt(z["Laufzeit"]), "Betrag USD": _num(z["Betrag USD"])}
                                          for z in t_.to_dict("records")],
                                   cash=float(ss["an_cash"]), kapital=float(ss["an_kapital"]), jahre=int(e["jahre"]))
                    kz = {k_: (round(v_, 4) if isinstance(v_, float) else v_) for k_, v_ in kv.items()
                          if k_ not in ("von", "bis")}
                    kz.update(aktien=round(ak_w / total * 100, 1), treasuries=round(tr_w / total * 100, 1),
                              cash=round(cash / total * 100, 1), total=round(total, 0),
                              von=f"{kv['von']:%Y-%m-%d}", bis=f"{kv['bis']:%Y-%m-%d}")
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
                               "Vola": _pct(kz.get("vola")), "Beta": _zahl(kz.get("beta")),
                               "Aktien/Treas./Cash": f"{kz.get('aktien', '–')} / {kz.get('treasuries', '–')} / {kz.get('cash', '–')}",
                               "Notiz": z["notiz"]})
            st.markdown("**Gespeicherte Vorauswahlen**")
            st.dataframe(pd.DataFrame(zeilen), hide_index=True, width="stretch")
