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
    "sektorvoten": ["zeit", "person", "sektor", "votum", "datenpunkte", "gegenargument"],
    "transaktionen": ["zeit", "person", "id", "datum", "art", "klasse", "ticker", "name", "sektor", "menge", "kurs",
                      "kupon", "faelligkeit", "notiz", "storniert"],
    "entscheide": ["zeit", "person", "id", "datum", "kategorie", "titel", "entscheid", "begruendung", "quelle1", "quelle2",
                   "gegenargument", "beteiligte", "status", "snapshot"],
    "sektordaten": ["zeit", "person", "sektor", "feld", "wert", "quelle"],
    "analysen": ["zeit", "person", "id", "name", "eingabe", "kennzahlen", "notiz"],
    "signal_log": ["zeit", "person", "signale"],
    # neu: Szenarien, Erwartungen, IPS, Taktik, Dokumente
    "definitionen": ["zeit", "person", "bereich", "objekt", "feld", "wert"],
    "szenario_beschluss": ["zeit", "person", "ic_base", "ic_bull", "ic_bear", "begruendung"],
    "erwartungen": ["zeit", "person", "monat", "frage", "antwort", "ueberzeugung", "begruendung", "modell_p", "modell_antwort",
                    "szenario_p"],
    "ips": ["zeit", "person", "feld", "wert"],
    "taktik": ["zeit", "person", "monat", "aktien", "anleihen", "cash", "sektoren", "duration", "tips", "laufzeiten",
               "begruendung"],
    "markt_erwartung": ["zeit", "person", "groesse", "wert", "quelle", "datum"],
    "dateien": ["zeit", "person", "id", "bereich", "name", "typ", "groesse", "datei_id", "beschreibung", "geloescht"],
    # Sektorallokation: Indexgewichte mit Stichtag und Abweichung/Begründung je Sektor
    "sektor_index": ["zeit", "person", "stichtag", "sektor", "gewicht", "quelle"],
    "sektor_allokation": ["zeit", "person", "sektor", "richtung", "abweichung", "makro", "gewinne", "bewertung", "momentum",
                          "begruendung", "gegenargument", "trigger", "regel_groesse", "regel_op", "regel_wert"],
    # Monitoring: Fälle (Exceptions), Meldeprotokoll, Kontakte, Zuständigkeit, Watchlist, Kauf-Thesen
    "faelle": ["zeit", "person", "id", "schluessel", "ebene", "bereich", "ticker", "titel", "text", "stufe", "wert", "einheit",
               "richtung", "art", "regel", "status", "reaktion", "begruendung", "neue_schwelle", "entscheid_id", "bearbeiter",
               "signal_aktiv", "erstmals", "vorgaenger", "snapshot", "hinweis"],
    "monitor_log": ["zeit", "person", "lauf", "empfaenger", "faelle", "betreff"],
    "kontakte": ["zeit", "person", "name", "email", "alles"],
    "zustaendigkeit": ["zeit", "person", "bereich", "haupt", "backup"],
    "watchlist": ["zeit", "person", "id", "ticker", "name", "sektor", "op", "marke", "these", "geloescht"],
    "thesen": ["zeit", "person", "ticker", "these", "kursziel", "stop", "ueberpruefung", "quartalszahlen"],
    # Sektor-News & Unternehmen (die gesammelten Meldungen selbst liegen verschlüsselt im Branch «daten», siehe newsdaten.py)
    "news_eigen": ["zeit", "person", "id", "datum", "titel", "link", "quelle", "sektoren", "ticker", "text", "geloescht"],
    "news_bewertung": ["zeit", "person", "news_id", "relevanz", "wirkung", "kommentar", "ausblenden"],
    "news_fazit": ["zeit", "person", "monat", "sektor", "text"],
    "team_rating": ["zeit", "person", "ticker", "rating", "begruendung"],
    "ki_verbrauch": ["zeit", "person", "monat", "zweck", "aufrufe", "input", "output", "usd", "modell"],
    # KI-Analyse des Portfolios (Update 10) und daraus übernommene Beobachtungspunkte fürs Monitoring
    "ki_analyse": ["zeit", "person", "id", "quelle", "quelle_id", "quelle_name", "modell", "ergebnis", "kennzahlen", "hash"],
    "ki_beobachtung": ["zeit", "person", "id", "analyse_id", "bereich", "sektor", "ticker", "titel", "grund", "reaktion", "groesse",
                       "op", "wert", "stufe", "geloescht"],
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


def _lokal_datei_pfad(self, datei_id: str) -> str:
    os.makedirs(os.path.join(self.ordner, "dateien"), exist_ok=True)
    return os.path.join(self.ordner, "dateien", re.sub(r"[^A-Za-z0-9_.-]", "_", datei_id))


def _lokal_hochladen(self, name: str, typ: str, inhalt: bytes, bereich: str) -> str:
    datei_id = f"{datetime.now():%Y%m%d%H%M%S}_{re.sub(r'[^A-Za-z0-9_.-]', '_', name)}"
    with open(_lokal_datei_pfad(self, datei_id), "wb") as f:
        f.write(inhalt)
    return datei_id


def _lokal_laden(self, datei_id: str) -> bytes:
    with open(_lokal_datei_pfad(self, datei_id), "rb") as f:
        return f.read()


LocalStore.datei_hochladen = _lokal_hochladen
LocalStore.datei_laden = _lokal_laden


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

    def _rufe(self, nutzlast: dict, versuche: int = 2) -> dict:
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

    def mail(self, empfaenger: list[str], betreff: str, html: str):
        self._rufe({"aktion": "mail", "empfaenger": empfaenger, "betreff": betreff, "html": html})

    def datei_hochladen(self, name: str, typ: str, inhalt: bytes, bereich: str) -> str:
        """Speichert die Datei im Google-Drive-Ordner «Makro-Dashboard Dokumente» (über das Skript). Gibt die Datei-ID zurück."""
        import base64

        try:
            a = self._rufe({"aktion": "datei_hochladen", "name": name, "typ": typ or "application/octet-stream", "bereich": bereich,
                            "inhalt": base64.b64encode(inhalt).decode("ascii")}, versuche=1)
        except SpeicherFehler as e:
            if "Unbekannte Aktion" in str(e):
                raise SpeicherFehler("Das Google-Skript kennt das Hochladen noch nicht. Neuen Code aus google-script.txt einfügen, "
                                     "Drive-Berechtigung erlauben und als «Neue Version» bereitstellen.") from None
            raise
        return a["datei_id"]

    def datei_laden(self, datei_id: str) -> bytes:
        import base64

        return base64.b64decode(self._rufe({"aktion": "datei_laden", "datei_id": datei_id})["inhalt"])

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
