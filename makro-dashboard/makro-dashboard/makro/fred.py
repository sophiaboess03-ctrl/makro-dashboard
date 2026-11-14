"""Abruf von FRED-Zeitreihen (Federal Reserve Bank of St. Louis).

Ohne API-Schlüssel wird der öffentliche CSV-Download verwendet.
Mit Schlüssel (kostenlos: https://fredaccount.stlouisfed.org) die offizielle API – stabiler.
"""
from __future__ import annotations

import io

import pandas as pd
import requests

CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
API_URL = "https://api.stlouisfed.org/fred/series/observations"
HEADERS = {"User-Agent": "Mozilla/5.0 (Makro-Dashboard FHGR Portfoliosimulation)"}


class FredFehler(Exception):
    pass


def lade_serie(series_id: str, start: str = "2000-01-01", api_key: str | None = None, timeout: int = 20) -> pd.Series:
    """Gibt eine pd.Series (DatetimeIndex → float) zurück, fehlende Werte entfernt."""
    if api_key:
        r = requests.get(API_URL, params={"series_id": series_id, "api_key": api_key, "file_type": "json",
                                          "observation_start": start}, headers=HEADERS, timeout=timeout)
        if r.status_code != 200:
            raise FredFehler(f"{series_id}: HTTP {r.status_code}")
        obs = r.json().get("observations", [])
        df = pd.DataFrame(obs)
        if df.empty:
            raise FredFehler(f"{series_id}: keine Daten")
        s = pd.Series(pd.to_numeric(df["value"], errors="coerce").values, index=pd.to_datetime(df["date"]))
    else:
        r = requests.get(CSV_URL, params={"id": series_id, "cosd": start}, headers=HEADERS, timeout=timeout)
        if r.status_code != 200 or not r.text.strip():
            raise FredFehler(f"{series_id}: HTTP {r.status_code}")
        s = parse_csv(r.text)
    s = s.dropna().sort_index()
    s.name = series_id
    if s.empty:
        raise FredFehler(f"{series_id}: keine Daten")
    return s


def parse_csv(text: str) -> pd.Series:
    df = pd.read_csv(io.StringIO(text))
    # Erste Spalte = Datum ("observation_date" oder früher "DATE"), zweite = Wert ("." = fehlend)
    datum = pd.to_datetime(df.iloc[:, 0], errors="coerce")
    wert = pd.to_numeric(df.iloc[:, 1], errors="coerce")
    return pd.Series(wert.values, index=datum).dropna()
