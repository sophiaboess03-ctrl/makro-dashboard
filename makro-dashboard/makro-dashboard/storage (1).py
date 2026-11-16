"""Speicher für Team-Eingaben.

- GoogleTabelleStore: gemeinsame Google-Tabelle über ein kleines Apps-Script (siehe google-script.txt)
- LocalStore: CSV-Dateien im Ordner "daten/" (zum lokalen Testen)

Es wird nie etwas überschrieben: jede Eingabe ist eine neue Zeile mit Zeitstempel und Person.
So bleibt der ganze Verlauf erhalten; die App nimmt jeweils den neusten Eintrag.
"""
from __future__ import annotations

import json
import os
import re
import time
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


LocalStore.newsletter = lambda self: []


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

    def _rufe(self, nutzlast: dict, versuche: int = 3) -> dict:
        nutzlast = {**nutzlast, "token": self.token}
        letzter = None
        for versuch in range(versuche):
            try:
                r = requests.post(self.url, data=json.dumps(nutzlast), headers={"Content-Type": "text/plain"},
                                  timeout=self.timeout, allow_redirects=True)
            except requests.RequestException as e:
                letzter = SpeicherFehler(f"Google-Tabelle nicht erreichbar ({type(e).__name__}).")
                time.sleep(1.5 * (versuch + 1))
                continue
            try:
                antwort = r.json()
            except ValueError:
                raise SpeicherFehler(diagnose(r)) from None
            if not antwort.get("ok"):
                raise SpeicherFehler(f"Skript meldet: {antwort.get('fehler', 'unbekannter Fehler')}")
            return antwort
        raise letzter or SpeicherFehler("Google-Tabelle nicht erreichbar.")

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

    def newsletter(self) -> list[dict]:
        """E-Mails mit dem Gmail-Label «Dashboard» (letzte 10 Tage), neuste zuerst."""
        try:
            return self._rufe({"aktion": "newsletter"}).get("mails", [])
        except SpeicherFehler as e:
            if "Unbekannte Aktion" in str(e):
                raise SpeicherFehler("Das Google-Skript ist noch die alte Version ohne Newsletter-Funktion. "
                                     "Neuen Code einfügen und als «Neue Version» bereitstellen.") from None
            raise

    def anhaengen(self, tabelle: str, zeilen: list[dict]):
        kopf = TABELLEN[tabelle]
        self._rufe({"aktion": "anhaengen", "tabelle": tabelle, "kopf": kopf,
                    "zeilen": [[str(z.get(k, "")) for k in kopf] for z in zeilen]})


def diagnose(r) -> str:
    """Übersetzt eine Nicht-JSON-Antwort von Google in eine verständliche Ursache."""
    text = r.text[:20000]
    titel = re.search(r"<title>(.*?)</title>", text, re.S | re.I)
    titel = titel.group(1).strip() if titel else ""
    url = str(getattr(r, "url", ""))
    if "accounts.google.com" in url or "ServiceLogin" in text or "accounts.google.com" in text[:5000]:
        return ("Google verlangt eine Anmeldung. Lösung: In Apps Script → Bereitstellen → Bereitstellungen verwalten → "
                "Stift → Zugriff auf «Jeder» stellen (nicht «Jeder mit Google-Konto») → Version «Neue Version» → Bereitstellen.")
    if "doPost" in text or "Skriptfunktion" in text or "Script function not found" in text:
        return ("Im bereitgestellten Skript fehlt doPost. Lösung: Code in Apps Script vollständig einfügen, speichern, "
                "dann Bereitstellen → Bereitstellungen verwalten → Stift → «Neue Version» → Bereitstellen.")
    if r.status_code == 404 or "nicht gefunden" in titel.lower() or "not found" in titel.lower():
        return "Die script_url stimmt nicht (Seite nicht gefunden). Sie muss mit https://script.google.com/macros/s/ beginnen und auf /exec enden."
    if r.status_code in (401, 403):
        return f"Kein Zugriff (HTTP {r.status_code}). Zugriff der Web-App auf «Jeder» stellen und neue Version bereitstellen."
    return f"Unerwartete Antwort von Google (HTTP {r.status_code}{', ' + titel if titel else ''})."


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
