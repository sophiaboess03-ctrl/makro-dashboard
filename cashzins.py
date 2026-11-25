"""Zins auf Cash in der Portfoliosimulation (Update 12).

Regel der Simulation (Dozent): Cash wird mit SOFR abzüglich 0,5 % Bankmarge verzinst; keine Steuern, keine Transaktionskosten.

Umsetzung (Näherung, bis die genaue Abrechnung der Dozenten-App bekannt ist):
- Satz je Kalendertag = max(SOFR − 0,5 %, 0). SOFR von FRED (Serie «SOFR», Satz des jeweiligen Geschäftstags; Wochenenden und
  Feiertage mit dem letzten Satz). Fehlt SOFR, Ersatz Fed Funds effektiv (DFF) − 0,5 %.
- Zinstage Act/360 (Geldmarkt-Usanz in USD): Zins pro Tag = Cash am Tagesende × Satz / 360.
- Gutschrift monatlich am Monatsende (danach verzinst sich der Zins mit); negatives Cash erhält keinen Zins.
- Prognose bis Simulationsende: SOFR folgt der Fed – Veränderung ab dem Tag nach der Fed-Sitzung (config.MONATE) gemäss den
  Szenario-Parametern «fed» (bp je Sitzung), gewichtet mit den Szenario-Wahrscheinlichkeiten.
Ohne Streamlit, damit auch monitoring_job und Tests es nutzen können.
"""
from __future__ import annotations

import pandas as pd

from makro import config as C

MARGE = 0.5            # % p.a. Abzug von SOFR
SERIE = "SOFR"
ERSATZ_SERIE = "DFF"
TAGE_BASIS = 360       # Act/360
QUELLE_URL = "https://fred.stlouisfed.org/series/SOFR"


def _reihe(fred: dict, name: str) -> pd.Series | None:
    s = (fred or {}).get(name)
    if s is None:
        return None
    s = pd.to_numeric(pd.Series(s), errors="coerce").dropna()
    if s.empty:
        return None
    s.index = pd.to_datetime(s.index).normalize()
    return s.sort_index()


def basis(fred: dict) -> tuple[pd.Series | None, str]:
    """SOFR-Reihe in % (sonst DFF als Ersatz) und Name der Quelle."""
    s = _reihe(fred, SERIE)
    if s is not None:
        return s, "SOFR"
    s = _reihe(fred, ERSATZ_SERIE)
    if s is not None:
        return s, "Fed Funds effektiv (Ersatz, SOFR fehlt)"
    return None, ""


def satz_tage(fred: dict, von: pd.Timestamp, bis: pd.Timestamp) -> pd.Series:
    """Netto-Satz in % p.a. für jeden Kalendertag von … bis (max(SOFR − Marge, 0)). Leer, wenn keine Daten."""
    s, _ = basis(fred)
    tage = pd.date_range(pd.Timestamp(von).normalize(), pd.Timestamp(bis).normalize(), freq="D")
    if s is None or not len(tage):
        return pd.Series(dtype=float)
    r = s.reindex(s.index.union(tage)).ffill().bfill().reindex(tage)
    return (r - MARGE).clip(lower=0.0)


def aktuell(fred: dict) -> dict | None:
    """Letzter SOFR, Netto-Satz, Datum, Quelle, Veränderung 30 Tage."""
    s, quelle = basis(fred)
    if s is None:
        return None
    alt = s[s.index <= s.index[-1] - pd.Timedelta(days=30)]
    return dict(sofr=float(s.iloc[-1]), netto=max(float(s.iloc[-1]) - MARGE, 0.0), datum=s.index[-1], quelle=quelle,
                d30=(float(s.iloc[-1]) - float(alt.iloc[-1])) * 100 if len(alt) else None, reihe=s)


# ---------------------------------------------------------------------------
# Realisierter Zins auf dem Cash des Portfolios (Transaktionen)
# ---------------------------------------------------------------------------
def _cash_fluss(tx: pd.DataFrame) -> pd.Series:
    """Cash-Veränderung je Kalendertag aus den Transaktionen (Kauf −, Verkauf +)."""
    if tx is None or not len(tx):
        return pd.Series(dtype=float)
    betrag = tx["menge"] * tx["kurs"] / tx["klasse"].map(lambda k: 100 if k == "Treasury" else 1)
    vz = tx["art"].map(lambda a: -1.0 if a == "Kauf" else 1.0)
    return (betrag * vz).groupby(pd.to_datetime(tx["datum"]).dt.normalize()).sum().sort_index()


def zins_reihe(tx: pd.DataFrame, kapital: float, fred: dict, bis: pd.Timestamp | None = None,
               marge: float = MARGE) -> pd.DataFrame:
    """Tageszeilen ab dem ersten Trade: cash (vor Zins), satz (% netto), zins_tag, aufgelaufen (Monat, noch nicht
    gutgeschrieben), gutgeschrieben (kumuliert), zins_total (gutgeschrieben + aufgelaufen)."""
    fluss = _cash_fluss(tx)
    if not len(fluss):
        return pd.DataFrame(columns=["cash", "satz", "zins_tag", "aufgelaufen", "gutgeschrieben", "zins_total"])
    start = fluss.index.min()
    ende = pd.Timestamp(bis or pd.Timestamp.today()).normalize()
    if ende < start:
        ende = start
    tage = pd.date_range(start, ende, freq="D")
    satz = satz_tage(fred, start, ende)
    if marge != MARGE and len(satz):
        satz = (satz + MARGE - marge).clip(lower=0.0)
    if not len(satz):
        satz = pd.Series(0.0, index=tage)
    cash_tx = kapital + fluss.reindex(tage, fill_value=0.0).cumsum()
    gut, auf, zeilen = 0.0, 0.0, []
    for d in tage:
        c = float(cash_tx[d]) + gut
        z = max(c, 0.0) * float(satz.get(d, 0.0)) / 100 / TAGE_BASIS
        auf += z
        zeilen.append((d, c, float(satz.get(d, 0.0)), z, auf, gut, gut + auf))
        if (d + pd.Timedelta(days=1)).month != d.month:      # Monatsende → Gutschrift
            gut, auf = gut + auf, 0.0
    out = pd.DataFrame(zeilen, columns=["datum", "cash", "satz", "zins_tag", "aufgelaufen", "gutgeschrieben", "zins_total"])
    return out.set_index("datum")


def zins_bis(tx: pd.DataFrame, kapital: float, fred: dict, bis: pd.Timestamp | None = None) -> float:
    """Zins auf Cash seit dem ersten Trade bis zum Datum (gutgeschrieben + aufgelaufen)."""
    z = zins_reihe(tx, kapital, fred, bis)
    return float(z["zins_total"].iloc[-1]) if len(z) else 0.0


def monate(z: pd.DataFrame) -> pd.DataFrame:
    """Zins je Kalendermonat (Ø Cash, Ø Satz, Zins)."""
    if not len(z):
        return pd.DataFrame(columns=["Monat", "Tage", "Ø Cash", "Ø Satz", "Zins"])
    g = z.groupby(z.index.to_period("M"))
    return pd.DataFrame({"Monat": [str(p) for p in g.groups], "Tage": g.size().values, "Ø Cash": g["cash"].mean().values,
                         "Ø Satz": g["satz"].mean().values, "Zins": g["zins_tag"].sum().values})


# ---------------------------------------------------------------------------
# Prognose bis Simulationsende
# ---------------------------------------------------------------------------
def _fed_termine() -> list[pd.Timestamp]:
    out = []
    for m in C.MONATE:
        if m.get("fed_sitzung"):
            out.append(pd.to_datetime(m["fed_sitzung"], dayfirst=True))
    return out


def satz_pfad(sofr_heute: float, fed_bp: float, von: pd.Timestamp, bis: pd.Timestamp, marge: float = MARGE) -> pd.Series:
    """Netto-Satz je Kalendertag, wenn SOFR an jeder Fed-Sitzung um fed_bp bp springt (ab dem Tag danach)."""
    tage = pd.date_range(pd.Timestamp(von).normalize(), pd.Timestamp(bis).normalize(), freq="D")
    termine = [t for t in _fed_termine() if t >= pd.Timestamp(von).normalize()]
    werte = []
    for d in tage:
        n = sum(1 for t in termine if t < d)
        werte.append(max(sofr_heute + n * fed_bp / 100 - marge, 0.0))
    return pd.Series(werte, index=tage)


def prognose(cash: float, sofr_heute: float, defs: dict | None = None, szen: pd.DataFrame | None = None,
             von: pd.Timestamp | str | None = None, bis: pd.Timestamp | str | None = None, marge: float = MARGE) -> dict:
    """Erwarteter Zins auf einem konstanten Cash-Betrag von … bis (Standard: Simulationsstart bzw. heute … Simulationsende).
    Gibt {'monate': DataFrame, 'total': USD, 'szen': {sid: USD}, 'satz_mittel': %} zurück."""
    heute = pd.Timestamp.today().normalize()
    von = pd.Timestamp(von) if von is not None else max(pd.Timestamp(C.SIMULATION_START), heute)
    bis = pd.Timestamp(bis) if bis is not None else pd.Timestamp(C.SIMULATION_ENDE)
    if bis < von:
        return dict(monate=pd.DataFrame(), total=0.0, szen={}, satz_mittel=None, von=von, bis=bis)
    pfade = {}
    if defs and szen is not None:
        for sid in defs:
            pfade[sid] = satz_pfad(sofr_heute, float(defs[sid]["param"].get("fed", 0) or 0), von, bis, marge)
        p = {sid: float(szen.loc[sid, "p"]) / 100 for sid in defs}
        gew = sum(p[s] * pfade[s] for s in pfade) / (sum(p.values()) or 1)
    else:
        gew = satz_pfad(sofr_heute, 0.0, von, bis, marge)
    unv = satz_pfad(sofr_heute, 0.0, von, bis, marge)
    zt = max(cash, 0.0) * gew / 100 / TAGE_BASIS
    g = zt.groupby(zt.index.to_period("M"))
    mon = pd.DataFrame({"Monat": [str(p_) for p_ in g.groups], "Tage": g.size().values,
                        "Ø Satz (gewichtet)": gew.groupby(gew.index.to_period("M")).mean().values,
                        "Zins (gewichtet)": g.sum().values,
                        "Zins (SOFR unverändert)": (max(cash, 0.0) * unv / 100 / TAGE_BASIS).groupby(unv.index.to_period("M")).sum().values})
    return dict(monate=mon, total=float(zt.sum()), szen={s: float((max(cash, 0.0) * v / 100 / TAGE_BASIS).sum()) for s, v in pfade.items()},
                unveraendert=float((max(cash, 0.0) * unv / 100 / TAGE_BASIS).sum()), satz_mittel=float(gew.mean()),
                von=von, bis=bis, tage=len(gew))
