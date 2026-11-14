"""Speicher für Team-Eingaben.

- GoogleSheetsStore: gemeinsame Google-Tabelle (für die Online-Version auf Streamlit Cloud)
- LocalStore: CSV-Dateien im Ordner "daten/" (zum lokalen Testen)

Es wird nie etwas überschrieben: jede Eingabe ist eine neue Zeile mit Zeitstempel und Person.
So bleibt der ganze Verlauf erhalten; die App nimmt jeweils den neusten Eintrag.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd

TABELLEN = {
    "werte": ["zeit", "person", "indikator", "datum", "wert", "quelle1", "quelle2", "notiz"],
    "szenario_votes": ["zeit", "person", "szenario", "p"],
    "risiko_votes": ["zeit", "person", "risiko", "p", "impact", "signal", "kommentar"],
    "zyklus_votes": ["zeit", "person", "von", "bis", "kommentar"],
    "notizen": ["zeit", "person", "kapitel", "typ", "text"],
    "einstellungen": ["zeit", "person", "schluessel", "wert"],
    "risiken_neu": ["zeit", "person", "id", "name", "kapitel", "warnsignal", "reaktion"],
}


def jetzt() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class LocalStore:
    name = "Lokale CSV-Dateien"

    def __init__(self, ordner: str = "daten"):
        self.ordner = ordner
        os.makedirs(ordner, exist_ok=True)

    def _pfad(self, tabelle):
        return os.path.join(self.ordner, f"{tabelle}.csv")

    def lesen(self, tabelle: str) -> pd.DataFrame:
        p = self._pfad(tabelle)
        if not os.path.exists(p):
            return pd.DataFrame(columns=TABELLEN[tabelle])
        return pd.read_csv(p, dtype=str, keep_default_na=False)

    def anhaengen(self, tabelle: str, zeilen: list[dict]):
        df = pd.DataFrame([{k: z.get(k, "") for k in TABELLEN[tabelle]} for z in zeilen])
        p = self._pfad(tabelle)
        df.to_csv(p, mode="a", header=not os.path.exists(p), index=False)


class GoogleSheetsStore:
    name = "Google Sheets"

    def __init__(self, credentials: dict, sheet_url: str):
        import gspread  # nur nötig, wenn Google Sheets genutzt wird

        self.gc = gspread.service_account_from_dict(dict(credentials))
        self.sh = self.gc.open_by_url(sheet_url)
        self._ws = {}

    def _blatt(self, tabelle):
        if tabelle in self._ws:
            return self._ws[tabelle]
        import gspread

        try:
            ws = self.sh.worksheet(tabelle)
        except gspread.WorksheetNotFound:
            ws = self.sh.add_worksheet(title=tabelle, rows=1000, cols=len(TABELLEN[tabelle]))
            ws.append_row(TABELLEN[tabelle], value_input_option="RAW")
        self._ws[tabelle] = ws
        return ws

    def lesen(self, tabelle: str) -> pd.DataFrame:
        werte = self._blatt(tabelle).get_values(value_render_option="UNFORMATTED_VALUE")
        if not werte:
            return pd.DataFrame(columns=TABELLEN[tabelle])
        kopf, daten = werte[0], werte[1:]
        df = pd.DataFrame(daten, columns=kopf) if daten else pd.DataFrame(columns=kopf)
        for c in TABELLEN[tabelle]:
            if c not in df.columns:
                df[c] = ""
        return df[TABELLEN[tabelle]].astype(str)

    def anhaengen(self, tabelle: str, zeilen: list[dict]):
        ws = self._blatt(tabelle)
        rows = [[z.get(k, "") for k in TABELLEN[tabelle]] for z in zeilen]
        ws.append_rows(rows, value_input_option="RAW")


def zahl(x):
    """Robuste Umwandlung (akzeptiert auch '4,1')."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace("'", "").replace(" ", "")
    if s == "" or s.lower() in ("nan", "none"):
        return None
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None
