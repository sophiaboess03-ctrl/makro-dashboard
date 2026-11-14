"""Speicher für Team-Eingaben.

- GoogleTabelleStore: gemeinsame Google-Tabelle über ein kleines Apps-Script
- LocalStore: CSV-Dateien im Ordner "daten/" (zum lokalen Testen)

Es wird nie etwas überschrieben: jede Eingabe ist eine neue Zeile mit Zeitstempel und Person.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pandas as pd
import requests

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


def _leer(tabelle):
    return pd.DataFrame(columns=TABELLEN[tabelle])


class LocalStore:
    name = "Lokale CSV-Dateien"

    def __init__(self, ordner: str = "daten"):
        self.ordner = ordner
        os.makedirs(ordner, exist_ok=True)

    def _pfad(self, tabelle):
        return os.path.join(self.ordner, f"{tabelle}.csv")

    def alle(self) -> dict[str, pd.DataFrame]:
        out = {}
        for t in TABELLEN:
            p = self._pfad(t)
            out[t] = pd.read_csv(p, dtype=str, keep_default_na=False) if os.path.exists(p) else _leer(t)
        return out

    def anhaengen(self, tabelle: str, zeilen: list[dict]):
        df = pd.DataFrame([{k: z.get(k, "") for k in TABELLEN[tabelle]} for z in zeilen])
        p = self._pfad(tabelle)
        df.to_csv(p, mode="a", header=not os.path.exists(p), index=False)


class SpeicherFehler(Exception):
    pass


class GoogleTabelleStore:
    """Spricht mit dem Apps-Script der Google-Tabelle (eine URL, ein geheimer Schlüssel)."""

    name = "Google-Tabelle"

    def __init__(self, url: str, token: str, timeout: int = 45):
        self.url, self.token, self.timeout = url.strip(), token.strip(), timeout
        if not self.url.startswith("https://script.google.com/"):
            raise SpeicherFehler("Die script_url muss mit https://script.google.com/ beginnen und auf /exec enden.")
        self._rufe({"aktion": "test"})

    def _rufe(self, nutzlast: dict) -> dict:
        nutzlast = {**nutzlast, "token": self.token}
        try:
            r = requests.post(self.url, data=json.dumps(nutzlast), headers={"Content-Type": "text/plain"},
                              timeout=self.timeout, allow_redirects=True)
        except requests.RequestException as e:
            raise SpeicherFehler(f"Google-Tabelle nicht erreichbar ({type(e).__name__}).") from e
        try:
            antwort = r.json()
        except ValueError as e:
            raise SpeicherFehler("Das Skript antwortet nicht richtig. Ist es als Web-App mit Zugriff «Jeder» "
                                 "bereitgestellt und endet die URL auf /exec?") from e
        if not antwort.get("ok"):
            raise SpeicherFehler(f"Skript meldet: {antwort.get('fehler', 'unbekannter Fehler')}")
        return antwort

    def alle(self) -> dict[str, pd.DataFrame]:
        daten = self._rufe({"aktion": "lesen"}).get("daten", {})
        out = {}
        for t, kopf in TABELLEN.items():
            werte = daten.get(t) or []
            if len(werte) < 2:
                out[t] = _leer(t)
                continue
            df = pd.DataFrame(werte[1:], columns=werte[0])
            for c in kopf:
                if c not in df.columns:
                    df[c] = ""
            out[t] = df[kopf].astype(str)
        return out

    def anhaengen(self, tabelle: str, zeilen: list[dict]):
        kopf = TABELLEN[tabelle]
        self._rufe({"aktion": "anhaengen", "tabelle": tabelle, "kopf": kopf,
                    "zeilen": [[str(z.get(k, "")) for k in kopf] for z in zeilen]})


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
