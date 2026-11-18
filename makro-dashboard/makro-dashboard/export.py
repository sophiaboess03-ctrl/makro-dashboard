"""Excel-Export: alle Team-Eingaben (jede Tabelle ein Blatt) plus Übersicht und Indikatoren – zum Sichern mit einem Klick."""
from __future__ import annotations

import io
from datetime import datetime

import pandas as pd

from makro import config as C
from makro import ui

BLATT = {"werte": "Werte", "szenario_votes": "Szenario-Bewertungen", "risiko_votes": "Risiko-Bewertungen",
         "zyklus_votes": "Zyklus", "notizen": "Notizen & Briefings", "einstellungen": "Einstellungen",
         "risiken_neu": "Neue Risiken", "sektorvoten": "Sektorvoten", "transaktionen": "Transaktionen",
         "entscheide": "Entscheide", "sektordaten": "Sektordaten"}


def excel(tabellen: dict, status: pd.DataFrame, alloc: dict, szen: pd.DataFrame, person: str) -> bytes:
    puffer = io.BytesIO()
    with pd.ExcelWriter(puffer, engine="openpyxl") as xw:
        a = alloc
        uebersicht = pd.DataFrame([
            ("Stand", datetime.now().strftime("%d.%m.%Y %H:%M")),
            ("Exportiert von", person),
            ("Taktik Aktien / Anleihen / Cash", f"{a['aktien']:.0f} / {a['anleihen']:.0f} / {a['cash']:.0f} %"),
            ("Taktik Duration", f"{ui.de(a['duration'], 1)} J."),
            ("Taktik TIPS-Anteil", f"{a['tips']:.0f} %"),
            ("Übergewichtet", ", ".join(a["sektoren"][a["sektoren"]["empfehlung"] == "Übergewichten"]["sektor"]) or "–"),
            ("Untergewichtet", ", ".join(a["sektoren"][a["sektoren"]["empfehlung"] == "Untergewichten"]["sektor"]) or "–"),
        ] + [(f"Szenario {r['name']}", f"{r['p']:.0f} %") for _, r in szen.iterrows()],
            columns=["Feld", "Wert"])
        uebersicht.to_excel(xw, sheet_name="Übersicht", index=False)
        ind = status[["name", "anzeige", "ampel", "trend", "datum", "herkunft", "person", "quelle1", "quelle2", "schwelle"]].copy()
        ind["datum"] = ind["datum"].map(lambda d: "" if d is None or pd.isna(d) else pd.Timestamp(d).strftime("%d.%m.%Y"))
        ind.columns = ["Indikator", "Wert", "Ampel", "Trend", "Stand", "Herkunft", "Person", "Quelle 1", "Quelle 2", "Schwelle"]
        ind.to_excel(xw, sheet_name="Indikatoren heute", index=False)
        for key, name in BLATT.items():
            df = tabellen.get(key)
            if df is None:
                continue
            df = df.sort_values("zeit") if "zeit" in df and len(df) else df
            if key == "werte" and len(df):
                df = df.copy()
                df["indikator"] = df["indikator"].map(lambda i: C.IND_BY_ID.get(i, {}).get("name", i))
            df.to_excel(xw, sheet_name=name[:31], index=False)
        for ws in xw.book.worksheets:
            ws.freeze_panes = "A2"
            for spalte in ws.columns:
                breite = max(len(str(c.value or "")) for c in spalte[:200])
                ws.column_dimensions[spalte[0].column_letter].width = min(60, max(10, breite + 2))
    return puffer.getvalue()
