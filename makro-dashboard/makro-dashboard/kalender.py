"""Termin-Kalender: Veröffentlichungstermine der US-Daten automatisch von FRED (braucht fred_api_key) + eure Ereignisse."""
from __future__ import annotations

import pandas as pd
import requests
import streamlit as st

from makro import config as C

# FRED-Veröffentlichung (Teil des Namens) → (Anzeige, betrifft, max. Anzahl künftiger Termine)
RELEASES = [
    ("Consumer Price Index", "CPI (Inflation)", "Kern-CPI-Regel, Inflation", 3),
    ("Employment Situation", "Arbeitsmarktbericht", "Arbeitslosenquote, Stellenaufbau, Löhne", 3),
    ("Gross Domestic Product", "BIP", "Zyklus, Output Gap", 3),
    ("Personal Income and Outlays", "Einkommen & Ausgaben (PCE, Sparquote)", "Kern-PCE, Sparquote, Realkonsum", 3),
    ("Advance Monthly Sales for Retail", "Detailhandelsumsätze", "Konsum", 3),
    ("Job Openings and Labor Turnover", "JOLTS (offene Stellen)", "Arbeitsmarkt", 2),
    ("Industrial Production", "Industrieproduktion & Auslastung", "Wachstum", 3),
    ("New Residential Construction", "Baubeginne", "Wohnungsbau", 2),
    ("Producer Price Index", "Produzentenpreise (PPI)", "Inflation", 2),
    ("Surveys of Consumers", "Uni Michigan Stimmung", "Konsum & Stimmung", 3),
    ("Unemployment Insurance Weekly Claims", "Erstanträge (wöchentlich)", "Arbeitsmarkt", 2),
]
DATEN_STICHWORTE = ("CPI", "Arbeitsmarkt", "BIP", "Einkommen")  # manuelle Termine, die der Kalender ersetzt


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def lade_releases(api_key: str | None):
    if not api_key:
        return [], "Ohne FRED-Schlüssel nur eure Termine (fred_api_key in den Secrets ergänzen)."
    heute = pd.Timestamp.today().normalize()
    try:
        r = requests.get("https://api.stlouisfed.org/fred/releases/dates", timeout=20, params={
            "api_key": api_key, "file_type": "json", "realtime_start": heute.strftime("%Y-%m-%d"),
            "realtime_end": (heute + pd.Timedelta(days=110)).strftime("%Y-%m-%d"),
            "include_release_dates_with_no_data": "true", "order_by": "release_date", "sort_order": "asc", "limit": 1000})
        daten = r.json().get("release_dates", [])
    except Exception as e:  # noqa: BLE001
        return [], f"FRED-Kalender nicht erreichbar ({type(e).__name__})."
    out, zaehler = [], {}
    for d in daten:
        name = d.get("release_name", "")
        for teil, anzeige, betrifft, maximal in RELEASES:
            if teil.lower() in name.lower():
                if zaehler.get(anzeige, 0) < maximal:
                    zaehler[anzeige] = zaehler.get(anzeige, 0) + 1
                    out.append((d["date"], anzeige, betrifft))
                break
    return out, None


def termine_df(api_key: str | None) -> tuple[pd.DataFrame, str | None]:
    auto, info = lade_releases(api_key)
    manuell = pd.DataFrame(C.TERMINE, columns=["sort", "Datum", "Ereignis", "Betrifft"])
    manuell["Quelle"] = "Team"
    if auto:
        manuell = manuell[~manuell["Ereignis"].str.contains("|".join(DATEN_STICHWORTE))]
        a = pd.DataFrame([dict(sort=d, Datum=pd.Timestamp(d).strftime("%d.%m.%Y"), Ereignis=e, Betrifft=b, Quelle="FRED-Kalender")
                          for d, e, b in auto])
        df = pd.concat([manuell, a], ignore_index=True)
    else:
        df = manuell
    df["sort"] = pd.to_datetime(df["sort"])
    return df.sort_values("sort").reset_index(drop=True), info
