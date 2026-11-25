"""KI (Claude-API) mit Kostendeckel – gezielt eingesetzt, nie «auf Vorrat».

Grundsatz: Zuerst entscheidet eine kostenlose Regel (newsdaten.regel_*). Die KI bekommt nur, was eine Regel schlecht kann:
- Relevanz und Wirkung der vorgefilterten Kandidaten (eine gebündelte Abfrage pro Lauf)
- Monatsfazit eines Sektors (nur wenn eine neue wichtige Meldung dazukommt, höchstens 1× pro Tag)
- Kurzfassung der Firmen-News (nur bei neuen relevanten Meldungen, höchstens 1× pro Tag und Titel)
- deutsches Firmenprofil (einmal pro Titel, danach gespeichert)
Nie KI für Kennzahlen, Analystendaten, Sortierung, Signale, Mails oder Buchungen.

Verbrauch: jede Abfrage liefert die Token-Zahlen zurück. Sie werden im Blatt «ki_verbrauch» protokolliert
(eine Zeile pro Lauf und Zweck). Ist der Deckel des Monats erreicht (Standard 4 USD, Einstellung «ki|deckel_usd»),
gibt anfrage() None zurück und die Aufrufer fallen auf die Regel zurück.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pandas as pd
import requests

from storage import zahl

STANDARD_MODELL = "claude-haiku-5-5"
STANDARD_DECKEL = 4.0
# USD pro Million Tokens (Input, Output), Prompts bis 100'000 Tokens – Stand Okt. 2026 (platform.claude.com, Pricing)
PREISE = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
PREIS_UNBEKANNT = (4.00, 20.00)   # unbekanntes Modell: vorsichtig teuer rechnen

SYSTEM = ("Du arbeitest für ein studentisches Team, das in einer Portfoliosimulation USD 10 Mio. in S&P-500-Aktien und "
          "US-Treasuries verwaltet und die elf S&P-500-Sektoren gegenüber dem Index über- oder untergewichtet. "
          "Schreibe auf Deutsch (Schweizer Rechtschreibung, ss statt ß), nüchtern und präzise. "
          "Verwende nur Fakten aus dem gelieferten Material und erfinde nichts.")


def monat_jetzt() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def kosten(modell: str, inp: int, out: int) -> float:
    p_in, p_out = PREISE.get(modell, PREIS_UNBEKANNT)
    return inp / 1e6 * p_in + out / 1e6 * p_out


def deckel(einst: pd.DataFrame | None) -> float:
    if einst is not None and not einst.empty:
        z = einst[einst["schluessel"] == "ki|deckel_usd"].sort_values("zeit")
        if len(z):
            v = zahl(z.iloc[-1]["wert"])
            if v is not None and v >= 0:
                return float(v)
    return STANDARD_DECKEL


def verbrauch(tab: pd.DataFrame | None, monat: str | None = None) -> dict:
    """Verbrauch eines Monats: {usd, aufrufe, input, output, nach_zweck: {zweck: usd}}."""
    monat = monat or monat_jetzt()
    out = dict(usd=0.0, aufrufe=0, input=0, output=0, nach_zweck={})
    if tab is None or tab.empty:
        return out
    t = tab[tab["monat"].astype(str) == monat]
    for _, r in t.iterrows():
        u = zahl(r["usd"]) or 0.0
        out["usd"] += u
        out["aufrufe"] += int(zahl(r["aufrufe"]) or 0)
        out["input"] += int(zahl(r["input"]) or 0)
        out["output"] += int(zahl(r["output"]) or 0)
        out["nach_zweck"][str(r["zweck"])] = out["nach_zweck"].get(str(r["zweck"]), 0.0) + u
    return out


class Budget:
    """Zählt die Kosten eines Laufs (oder einer App-Sitzung) und sperrt bei erreichtem Deckel."""

    def __init__(self, api_key: str | None, modell: str | None, deckel_usd: float, schon_verbraucht: float):
        self.api_key = (api_key or "").strip() or None
        self.modell = (modell or "").strip() or STANDARD_MODELL
        self.deckel, self.vorher = float(deckel_usd), float(schon_verbraucht)
        self.posten: dict[str, list] = {}   # zweck → [aufrufe, input, output, usd]
        self.fehler: list[str] = []

    @property
    def neu(self) -> float:
        return sum(p[3] for p in self.posten.values())

    @property
    def rest(self) -> float:
        return self.deckel - self.vorher - self.neu

    @property
    def aktiv(self) -> bool:
        return bool(self.api_key) and self.rest > 0.005

    def grund_inaktiv(self) -> str:
        if not self.api_key:
            return "kein API-Key hinterlegt"
        if self.rest <= 0.005:
            return f"Monatsdeckel von {self.deckel:.2f} USD erreicht"
        return ""

    def anfrage(self, prompt: str, zweck: str, max_tokens: int = 600, schaetzung_out: int | None = None,
                system: str | None = None, timeout: int = 120) -> str | None:
        """Text der Antwort oder None (kein Key, Deckel erreicht oder Fehler). Prüft vorher, ob die Abfrage
        (grob geschätzt) noch unter den Deckel passt."""
        if not self.aktiv:
            return None
        grob = kosten(self.modell, len(prompt) // 3 + 400, schaetzung_out or max_tokens)
        if grob > self.rest:
            self.fehler.append(f"{zweck}: übersprungen (Rest {self.rest:.3f} USD reicht nicht)")
            return None
        try:
            r = requests.post("https://api.anthropic.com/v1/messages", timeout=timeout,
                              headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                                       "content-type": "application/json"},
                              json={"model": self.modell, "max_tokens": max_tokens, "system": system or SYSTEM,
                                    "messages": [{"role": "user", "content": prompt}]})
        except Exception as e:  # noqa: BLE001
            self.fehler.append(f"{zweck}: {type(e).__name__}")
            return None
        if r.status_code != 200:
            self.fehler.append(f"{zweck}: HTTP {r.status_code}")
            return None
        j = r.json()
        u = j.get("usage") or {}
        inp = int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
        out = int(u.get("output_tokens") or 0)
        p = self.posten.setdefault(zweck, [0, 0, 0, 0.0])
        p[0] += 1
        p[1] += inp
        p[2] += out
        p[3] += kosten(self.modell, inp, out)
        return "".join(b.get("text", "") for b in j.get("content", []))

    def zeilen(self, person: str = "KI-Lauf") -> list[dict]:
        """Zeilen fürs Blatt «ki_verbrauch» (eine pro Zweck)."""
        from storage import jetzt

        return [dict(zeit=jetzt(), person=person, monat=monat_jetzt(), zweck=z, aufrufe=p[0], input=p[1], output=p[2],
                     usd=round(p[3], 6), modell=self.modell) for z, p in self.posten.items() if p[0]]


# ---------------------------------------------------------------------------
# App-Seite (Tagesupdate, Newsletter auf «Tagesupdate & News»): gleicher Deckel, Protokoll ins Blatt
# ---------------------------------------------------------------------------
APP = dict(store=None, verbraucht=0.0, deckel=STANDARD_DECKEL, person="App")


def app_konfigurieren(store, tabellen: dict, person: str = "App"):
    APP.update(store=store, verbraucht=verbrauch(tabellen.get("ki_verbrauch"))["usd"],
               deckel=deckel(tabellen.get("einstellungen")), person=person or "App")


def app_budget(api_key: str | None, modell: str | None) -> Budget:
    return Budget(api_key, modell, APP["deckel"], APP["verbraucht"])


def app_protokoll(b: Budget):
    """Verbrauch einer App-Abfrage sofort protokollieren (sonst würde der Deckel App-Abfragen nicht sehen)."""
    zeilen = b.zeilen(f"App ({APP['person']})")
    if not zeilen or APP["store"] is None:
        return
    try:
        APP["store"].anhaengen("ki_verbrauch", zeilen)
        APP["verbraucht"] += b.neu
    except Exception:  # noqa: BLE001 – Protokoll darf die Seite nicht verhindern
        pass


def json_aus(text: str | None):
    """Erstes JSON-Objekt bzw. -Array aus einer KI-Antwort (auch wenn es in ```json … ``` steht)."""
    if not text:
        return None
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    for anfang, ende in (("[", "]"), ("{", "}")):
        i, j = t.find(anfang), t.rfind(ende)
        if i >= 0 and j > i:
            try:
                return json.loads(t[i:j + 1])
            except ValueError:
                continue
    return None
