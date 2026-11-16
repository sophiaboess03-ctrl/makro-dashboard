"""Gemeinsame Berechnung für App und Morgen-Mail: Live-Kurse einmischen, Ampeln, Signale, Szenarien, Allokation."""
from __future__ import annotations

import pandas as pd

from makro import config as C
from makro import logic as L


def mische_markt(fred_roh: dict, yahoo: dict) -> tuple[dict, dict]:
    """Live-Kurse von Yahoo ergänzen die FRED-Reihen (gleiches Datum: Yahoo gewinnt) → Ampeln = Kursleiste."""
    fred = dict(fred_roh)
    labels = {}
    for ticker, reihe in C.MARKT_ERGAENZT_FRED.items():
        if ticker in yahoo:
            y = yahoo[ticker].dropna()
            if ticker == "^TNX" and len(y) and y.iloc[-1] > 20:
                y = y / 10
            f = fred_roh.get(reihe)
            fred[reihe] = y if f is None else pd.concat([f, y])[lambda x: ~x.index.duplicated(keep="last")].sort_index()
            labels[reihe] = "FRED + Yahoo"
    for i in C.INDIKATOREN:
        if i["quelle"] == "markt" and i["series"] in yahoo:
            fred[i["series"]] = yahoo[i["series"]].dropna()
            labels[i["series"]] = "Yahoo Finance"
    return fred, labels


def berechne(fred: dict, labels: dict, tabellen: dict) -> dict:
    daten = L.Daten(fred, tabellen["werte"], labels)
    status = L.status(daten)
    kombis = L.kombi_signale(status, daten)
    szen = L.szenario_wahrscheinlichkeiten(tabellen["szenario_votes"])
    risiken = L.risiko_bewertung(tabellen["risiko_votes"], tabellen["risiken_neu"], status, kombis)
    neutral = L.einstellungen(tabellen["einstellungen"])
    alloc = L.allokation(status, szen, risiken, kombis, neutral)
    return dict(daten=daten, status=status, kombis=kombis, szen=szen, risiken=risiken, alloc=alloc,
                zyklus_team=L.zyklus_team(tabellen["zyklus_votes"]), zyklus_modell=L.zyklus_modell(status))


def fred_serien(markt_fred: list[str]) -> list[str]:
    return sorted({i["series"] for i in C.INDIKATOREN if i["quelle"] == "fred"} | {"GDPC1", "GDPPOT", C.REZESSION_SERIE}
                  | set(C.ZINSKURVE) | set(C.ERWARTUNGS_SERIEN) | set(markt_fred))
