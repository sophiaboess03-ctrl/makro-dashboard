"""Sammel-Lauf Sektor-News & Unternehmen (GitHub Actions, Workflow «Sektor-News»), 3× täglich.

Ablauf (die KI nur dort, wo eine Regel nicht reicht – siehe ki.py):
 1. Google News je Sektor (11 Abfragen) und je Titel (Bestand, Watchlist, Probe-Portfolio) holen, gleiche Story von
    mehreren Quellen zusammenfassen, bekannte Meldungen nur ergänzen (weitere Quellen), Rauschen per Regel aussortieren.
 2. Regel-Bewertung (kostenlos) für alle. Nur neue Kandidaten mit Regel-Relevanz ab 3 gehen an die KI – höchstens
    4 pro Sektor, 2 pro Titel und 10 Newsletter-Absätze pro Lauf, gebündelt in einer Abfrage (je 40 Meldungen).
 3. Monatsfazit je Sektor nur neu, wenn eine neue Meldung mit Relevanz ab 4 in die Top-Liste kommt (höchstens 1× pro Tag).
 4. Unternehmensdaten (Yahoo, ohne KI) höchstens 1× pro Tag; Firmen-News-Kurzfassung nur bei neuen relevanten Meldungen
    (höchstens 1× pro Tag und Titel); deutsches Profil einmal pro Titel.
 5. Dateien verschlüsselt in den Ordner des Branches «daten» schreiben (der Workflow committet sie), Verbrauch ins Blatt
    «ki_verbrauch».

  python news_job.py --daten ../../_daten          normaler Lauf
  DRY_RUN=1 python news_job.py --daten /tmp/x      Test: nichts in die Google-Tabelle schreiben

Umgebungsvariablen: SCRIPT_URL, SCRIPT_TOKEN (Pflicht) · ANTHROPIC_API_KEY, ANTHROPIC_MODEL (optional).
Die Ausgabe enthält absichtlich nur Zahlen (die Action-Protokolle eines öffentlichen Repos sind öffentlich).
"""
from __future__ import annotations

import hashlib
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pandas as pd

import ki
import newsdaten as N
from makro import config as C

KI_PRO_SEKTOR, KI_PRO_TITEL, KI_TITEL_MAX, KI_NEWSLETTER_MAX = 4, 2, 30, 10
REGEL_EXTRA_PRO_SEKTOR = 2           # zusätzlich ohne KI gespeichert (nur Regel-Relevanz ab 4)
KI_BLOCK = 40
FAZIT_ABSTAND_H, FIRMA_ABSTAND_H, YAHOO_ABSTAND_H = 20, 20, 20
ABSCHLUSS_ABSTAND_H = 24 * 7          # Jahresabschlüsse (Bilanz, Erfolgsrechnung, Cashflow) wöchentlich
PROFILE_PRO_LAUF = 15


def _alter_h(zeit) -> float:
    t = N.ts(zeit)
    return 1e9 if t is None else (pd.Timestamp.now(tz="UTC") - t).total_seconds() / 3600


class Ablage:
    """Monatsdateien und Unternehmensdatei im Ordner des Daten-Branches."""

    def __init__(self, ordner: str, token: str):
        self.ordner, self.token = ordner, token
        self.monate: dict[str, dict] = {}
        self.geaendert: set[str] = set()
        self.fehler: list[str] = []

    def monat(self, m: str) -> dict:
        if m not in self.monate:
            d, f = N.lade_datei(f"news/{m}.json", self.token, lokal=self.ordner)
            if f:
                self.fehler.append(f)
            self.monate[m] = d or N.leerer_monat(m)
        return self.monate[m]

    def alle_meldungen(self, monate: list[str]) -> list[dict]:
        return [x for m in monate for x in self.monat(m)["meldungen"]]

    def unternehmen(self) -> dict:
        if "_u" not in self.monate:
            d, f = N.lade_datei("unternehmen.json", self.token, lokal=self.ordner)
            if f:
                self.fehler.append(f)
            self.monate["_u"] = d or {}
        return self.monate["_u"]

    def speichern(self, status: dict):
        for m in self.geaendert:
            if m == "_u":
                N.speichere_datei(self.ordner, "unternehmen.json", self.monate["_u"], self.token)
            else:
                N.speichere_datei(self.ordner, f"news/{m}.json", self.monate[m], self.token)
        N.speichere_datei(self.ordner, "status.json", status, self.token)


# ---------------------------------------------------------------------------
# 1–2: Sammeln und bewerten
# ---------------------------------------------------------------------------
def _suchen(sektor_abfragen: dict, firmen_abfragen: dict) -> tuple[dict, dict, list]:
    fehler = []

    def hole(q):
        try:
            return N.google_news(q)
        except Exception as e:  # noqa: BLE001
            return e

    ex = ThreadPoolExecutor(max_workers=6)
    sek = {s: ex.submit(hole, q) for s, q in sektor_abfragen.items()}
    fir = {t: ex.submit(hole, q) for t, q in firmen_abfragen.items()}
    out_s, out_f = {}, {}
    for d, quelle, ziel in ((sek, "Sektor", out_s), (fir, "Titel", out_f)):
        for k, f in d.items():
            try:
                r = f.result(timeout=60)
            except Exception as e:  # noqa: BLE001
                r = e
            if isinstance(r, Exception):
                fehler.append(f"{quelle}: {type(r).__name__}")
                ziel[k] = []
            else:
                ziel[k] = r
    ex.shutdown(wait=False)
    return out_s, out_f, fehler


def _bekannt(m: dict, bestand: list[dict]) -> dict | None:
    for b in bestand:
        if b["id"] == m["id"] or N.aehnlich(b["titel"], m["titel"]):
            return b
    return None


def _neue_meldung(g: dict, sektoren: list[str], ticker: list[str], art: str, regel: dict, text: str = "") -> dict:
    return dict(id=N.news_id(g["titel"]), titel=g["titel"], link=g.get("link", ""), quelle=g.get("quelle", ""),
                quellen=(g.get("quellen") or [g.get("quelle", "")])[:8], n_quellen=len(g.get("quellen") or [1]),
                zeit=g["zeit"], gefunden=N.iso(None), sektoren=sektoren, ticker=ticker, art=art, text=text[:400],
                regel_rel=regel["rel"], regel_wirkung=regel["wirkung"], regel_gruende=regel["gruende"],
                ki_rel=None, ki_wirkung=None, ki_grund="")


def sammeln(ablage: Ablage, titel: dict, store, info: list) -> list[dict]:
    """Gibt die Kandidaten für die KI zurück (bereits in den Monatsdateien eingetragen)."""
    eigene = {t: N.kurzname(d.get("name") or t) for t, d in titel.items()}
    firmen_q = {t: f'"{eigene[t]}" stock' for t in titel if eigene[t]}
    roh_s, roh_f, fehler = _suchen(N.SEKTOR_SUCHE, firmen_q)
    info += [f"Hinweis: {x}" for x in sorted(set(fehler))]
    monate = sorted({N.monat_von(N.iso(None)), N.monat_von(N.iso(N.jetzt_utc() - timedelta(days=3)))})
    bestand = ablage.alle_meldungen(monate)
    kandidaten, n_neu, n_erg, n_rausch = [], 0, 0, 0

    def verarbeiten(gruppen, sektor, tick, art, ki_max, extra_max):
        nonlocal n_neu, n_erg, n_rausch
        neu = []
        for g in gruppen:
            mid = N.news_id(g["titel"])
            alt = _bekannt(dict(id=mid, titel=g["titel"]), bestand)
            if alt is not None:   # bekannte Story: Quellen, Sektor und Titel ergänzen – keine neue KI-Abfrage
                qs = sorted(set(alt.get("quellen") or []) | set(g.get("quellen") or []))
                vorher = (len(alt.get("quellen") or []), tuple(alt["sektoren"]), tuple(alt["ticker"]))
                alt["quellen"] = qs[:8]
                alt["n_quellen"] = max(alt.get("n_quellen") or 1, len(qs))
                if sektor and sektor not in alt["sektoren"]:
                    alt["sektoren"].append(sektor)
                for t in tick:
                    if t not in alt["ticker"]:
                        alt["ticker"].append(t)
                r = N.regel_bewertung(alt["titel"], alt["quelle"], alt["quellen"], eigene)
                alt["regel_rel"] = max(alt.get("regel_rel") or 1, r["rel"])
                if vorher != (len(alt["quellen"]), tuple(alt["sektoren"]), tuple(alt["ticker"])):
                    ablage.geaendert.add(N.monat_von(alt["zeit"]))
                    n_erg += 1
                continue
            if N.ist_rauschen(g["titel"], g["quelle"]):
                n_rausch += 1
                continue
            r = N.regel_bewertung(g["titel"], g["quelle"], g.get("quellen"), eigene)
            sek = ([sektor] if sektor else []) + [s for s in N.sektoren_aus_text(g["titel"]) if s != sektor]
            neu.append(_neue_meldung(g, sek[:2] or ([titel[tick[0]]["sektor"]] if tick and titel[tick[0]]["sektor"] else []),
                                     sorted(set(tick) | set(r["ticker"])), art, r))
        neu.sort(key=lambda m: (m["regel_rel"], m["n_quellen"], m["zeit"]), reverse=True)
        ki_k = [m for m in neu if m["regel_rel"] >= 3][:ki_max]
        extra = [m for m in neu if m["regel_rel"] >= 4 and m not in ki_k][:extra_max]
        for m in ki_k + extra:
            ablage.monat(N.monat_von(m["zeit"]))["meldungen"].append(m)
            ablage.geaendert.add(N.monat_von(m["zeit"]))
            bestand.append(m)
            n_neu += 1
        return ki_k

    for s in C.SEKTOREN:
        kandidaten += verarbeiten(N.gruppieren(roh_s.get(s, [])), s, [], "sektor", KI_PRO_SEKTOR, REGEL_EXTRA_PRO_SEKTOR)
    k_titel = []
    for t, items in roh_f.items():
        k_titel += verarbeiten(N.gruppieren(items), titel[t].get("sektor") or "", [t], "titel", KI_PRO_TITEL, 1)
    kandidaten += k_titel[:KI_TITEL_MAX]
    kandidaten += newsletter(ablage, store, eigene, bestand, info)
    info.append(f"Meldungen: {n_neu} neu, {n_erg} ergänzt, {n_rausch} als Rauschen aussortiert, {len(kandidaten)} an die KI")
    return kandidaten


def newsletter(ablage: Ablage, store, eigene: dict, bestand: list, info: list) -> list[dict]:
    """Newsletter (Gmail-Label «Dashboard»): Regel zieht Absätze mit Sektorbezug heraus, nur diese gehen an die KI."""
    import markt

    try:
        mails = store.newsletter()
    except Exception as e:  # noqa: BLE001
        info.append(f"Newsletter: {type(e).__name__}")
        return []
    kand = []
    for m in mails:
        mon = N.monat_von(m.get("datum"))
        if mon < N.ERSTER_MONAT:
            continue
        d = ablage.monat(mon)
        if m["id"] in d["newsletter_ids"]:
            continue
        d["newsletter_ids"].append(m["id"])
        ablage.geaendert.add(mon)
        for p in markt.meldungen(m.get("text") or "", 12):
            text = f"{p['titel']} {p['text']}".strip()
            sek = N.sektoren_aus_text(text)
            if not sek:
                continue
            titel = p["titel"] or (p["text"][:110] + ("…" if len(p["text"]) > 110 else ""))
            g = dict(titel=titel, link="", quelle=f"Newsletter {markt._absender_name(m.get('absender', ''))}",
                     zeit=N.iso(N.ts(m.get("datum")).to_pydatetime()) if N.ts(m.get("datum")) is not None else N.iso(None))
            g["quellen"] = [g["quelle"]]
            if _bekannt(dict(id=N.news_id(titel), titel=titel), bestand):
                continue
            r = N.regel_bewertung(text, g["quelle"], g["quellen"], eigene, basis=2)
            x = _neue_meldung(g, sek, r["ticker"], "newsletter", r, p["text"])
            kand.append(x)
    kand.sort(key=lambda m: m["regel_rel"], reverse=True)
    kand = kand[:KI_NEWSLETTER_MAX]
    for x in kand:
        ablage.monat(N.monat_von(x["zeit"]))["meldungen"].append(x)
        ablage.geaendert.add(N.monat_von(x["zeit"]))
        bestand.append(x)
    return kand


PROMPT_BEWERTUNG = """Bewerte jede Meldung für unser Team. Wir gewichten die elf S&P-500-Sektoren gegenüber dem Index über oder unter \
(Horizont 1–3 Monate) und halten Einzeltitel daraus.

Relevanz:
5 = kann eine Sektorgewichtung oder eine unserer Positionen direkt verändern (z. B. Gewinnwarnung eines Schwergewichts, \
neue Regulierung oder Zölle mit Sektorwirkung, grosse Übernahme, Fed-Entscheid mit klarer Sektorwirkung)
4 = wichtig für den Ausblick eines Sektors oder eines unserer Titel
3 = beachtenswert, aber ohne Folgen für unsere Gewichtung
2 = Randnotiz
1 = irrelevant, Werbung, Kursziel-Liste oder blosse Wiederholung
Wirkung: + positiv, - negativ, 0 neutral oder unklar – bei einem Titel (Spalte Titel) für diesen Titel, sonst für den Sektor.
Sektoren: übernimm die Zuordnung oder korrigiere sie (höchstens 2, nur aus: {sektoren}).
Grund: höchstens 12 Wörter, Deutsch, sachlich – was ist passiert und warum zählt es.

Unsere Titel: {titel}
{stand}
Antworte nur mit einem JSON-Array, ein Objekt pro Meldung:
[{{"id":"…","r":4,"w":"-","s":["Finanzen"],"g":"…"}}]

Meldungen (id | Sektoren | Titel | Quelle, Anzahl Quellen | Schlagzeile | Auszug):
{zeilen}"""


def _stand_text(tabellen: dict) -> str:
    try:
        import sektoren as SK

        sa = SK.lade_allokation(tabellen.get("sektor_allokation"))["sektoren"]
        teile = [f"{s} {e['richtung']}" + (f" {e['abweichung']:+.1f} Pp." if e.get("abweichung") is not None else "")
                 for s, e in sa.items() if e.get("richtung") != "Neutral"]
        return "Unsere Sektorwetten: " + ("; ".join(teile) if teile else "keine") + "."
    except Exception:  # noqa: BLE001
        return ""


def ki_bewerten(kandidaten: list[dict], titel: dict, budget: ki.Budget, tabellen: dict, info: list) -> int:
    if not kandidaten or not budget.aktiv:
        if kandidaten:
            info.append(f"KI-Bewertung übersprungen ({budget.grund_inaktiv()}) – es gilt die Regel")
        return 0
    tt = ", ".join(f"{t} ({d.get('name') or t}, {d.get('sektor') or '?'})" for t, d in sorted(titel.items())) or "noch keine"
    n = 0
    for i in range(0, len(kandidaten), KI_BLOCK):
        block = kandidaten[i:i + KI_BLOCK]
        zeilen = "\n".join(f"{m['id']} | {', '.join(m['sektoren']) or '?'} | {', '.join(m['ticker']) or '-'} | {m['quelle']}, "
                           f"{m.get('n_quellen') or 1} | {m['titel']} | {(m.get('text') or '')[:220]}" for m in block)
        prompt = PROMPT_BEWERTUNG.format(sektoren=", ".join(C.SEKTOREN), titel=tt, stand=_stand_text(tabellen), zeilen=zeilen)
        antwort = ki.json_aus(budget.anfrage(prompt, "Bewertung Meldungen", max_tokens=60 * len(block) + 200,
                                             schaetzung_out=40 * len(block)))
        if not isinstance(antwort, list):
            info.append("KI-Bewertung: keine verwertbare Antwort – es gilt die Regel")
            continue
        nach_id = {m["id"]: m for m in block}
        for a in antwort:
            m = nach_id.get(str(a.get("id", "")))
            if m is None:
                continue
            try:
                m["ki_rel"] = max(1, min(5, int(round(float(a.get("r"))))))
            except (TypeError, ValueError):
                continue
            m["ki_wirkung"] = a.get("w") if a.get("w") in ("+", "-", "0") else "0"
            m["ki_grund"] = str(a.get("g") or "")[:140]
            s = [x for x in (a.get("s") or []) if x in C.SEKTOREN][:2]
            if s:
                m["sektoren"] = s
            n += 1
    return n


def aufraeumen(ablage: Ablage):
    """KI hält es für irrelevant (1) → nicht behalten (spart Platz); Rest bleibt, auch Relevanz 2 (Team kann hochstufen)."""
    for m, d in list(ablage.monate.items()):
        if m == "_u":
            continue
        vorher = len(d["meldungen"])
        d["meldungen"] = [x for x in d["meldungen"] if x.get("ki_rel") != 1]
        if len(d["meldungen"]) != vorher:
            ablage.geaendert.add(m)


# ---------------------------------------------------------------------------
# 3: Monatsfazit je Sektor
# ---------------------------------------------------------------------------
PROMPT_FAZIT = """Schreibe das Monatsfazit {monat} für den Sektor {sektor} in 3–4 Sätzen: Was waren die wichtigsten Entwicklungen, \
wie ist die Nachrichtenlage insgesamt (positiv/negativ), und was heisst das für unsere Gewichtung ({stand})? \
Stützt die Lage unsere Gewichtung, spricht sie dagegen, oder ist sie neutral? Nur die Meldungen unten verwenden.

Meldungen (Datum | Relevanz | Wirkung | Schlagzeile | Einordnung | Team-Kommentare):
{zeilen}"""


def fazits(ablage: Ablage, tabellen: dict, budget: ki.Budget, info: list) -> int:
    import sektoren as SK

    bew = N.bewertungen(tabellen.get("news_bewertung"))
    eigen = N.eigene_meldungen(tabellen.get("news_eigen"))
    sa = SK.lade_allokation(tabellen.get("sektor_allokation"))["sektoren"]
    monat = N.monat_von(N.iso(None))
    d = ablage.monat(monat)
    ms = N.monatsliste(d, eigen, bew, monat)
    n = 0
    for s in C.SEKTOREN:
        tops = N.top(ms, s, 8)
        alt = d["fazit"].get(s) or {}
        neu_wichtig = [m for m in tops if m["eff"]["rel"] >= 4 and m["id"] not in (alt.get("basis") or [])]
        if not neu_wichtig or _alter_h(alt.get("zeit")) < FAZIT_ABSTAND_H:
            continue
        if not budget.aktiv:
            break
        e = sa.get(s, {})
        stand = e.get("richtung", "Neutral") + (f" {e['abweichung']:+.1f} Pp." if e.get("abweichung") is not None else "")
        zeilen = "\n".join(f"{m['zeit'][:10]} | {m['eff']['rel']:.1f} | {m['eff']['wirkung']} | {m['titel']} | {m.get('ki_grund') or ''} | "
                           + "; ".join(f"{p}: {k}" for p, k in m["eff"]["kommentare"]) for m in tops)
        text = budget.anfrage(PROMPT_FAZIT.format(monat=monat, sektor=s, stand=stand, zeilen=zeilen), "Monatsfazit", 380)
        if text:
            d["fazit"][s] = dict(text=text.strip(), zeit=N.iso(None), basis=[m["id"] for m in tops], quelle="KI")
            ablage.geaendert.add(monat)
            n += 1
    return n


# ---------------------------------------------------------------------------
# 4: Unternehmen
# ---------------------------------------------------------------------------
PROMPT_PROFIL = """Fasse auf Deutsch in 2–3 Sätzen zusammen: Was macht {name} ({ticker}), womit verdient das Unternehmen sein Geld \
(wichtigste Segmente) und wie ist seine Marktstellung? Nur aus dieser Beschreibung:

{text}"""

PROMPT_FIRMA = """Fasse die Nachrichtenlage zu {name} ({ticker}, Sektor {sektor}) der letzten 30 Tage in 2–3 Sätzen zusammen: \
Was ist passiert, und betrifft es {these}? Nur die Meldungen unten verwenden.

Meldungen (Datum | Relevanz | Wirkung | Schlagzeile | Einordnung):
{zeilen}"""


def unternehmen(ablage: Ablage, titel: dict, tabellen: dict, budget: ki.Budget, info: list) -> dict:
    import monitoring as M

    U = ablage.unternehmen()
    alt = [t for t in titel if _alter_h((U.get(t) or {}).get("stand")) >= YAHOO_ABSTAND_H or (U.get(t) or {}).get("teilweise")]
    if alt:
        # Jahresabschlüsse höchstens wöchentlich neu (ändern sich quartalsweise), Schätzungen täglich
        abschluss = {t for t in alt if not (U.get(t) or {}).get("fin")
                     or _alter_h((U.get(t) or {}).get("abschluss_stand")) >= ABSCHLUSS_ABSTAND_H}
        frisch = N.firmen_yahoo(alt, parallel=2, bilanz=True, abschluss=abschluss)
        for t, f in frisch.items():
            vorher = U.get(t) or {}
            if (not f.get("info") or f.get("teilweise")) and vorher.get("info") and not vorher.get("teilweise"):
                vorher["fehler"] = f.get("fehler", [])     # Yahoo-Ausfall: alte (vollständige) Daten behalten
                continue
            for k in ("profil_de", "profil_hash", "news"):
                if k in vorher:
                    f[k] = vorher[k]
            for k in ("fin", "abschluss_stand", "eps_trend", "eps_revisionen", "ueberraschungen"):
                if k not in f and k in vorher:           # nicht neu geholt oder Abruf gescheitert → alten Stand behalten
                    f[k] = vorher[k]
            U[t] = f
        ablage.geaendert.add("_u")
    for t, f in U.items():                     # Name/Sektor aus Yahoo ergänzen
        if t in titel:
            titel[t]["name"] = titel[t]["name"] or (f.get("info") or {}).get("shortName", "")
    # Profil (einmal pro Titel)
    n_p = 0
    for t in sorted(titel):
        f = U.get(t) or {}
        en = (f.get("info") or {}).get("longBusinessSummary") or ""
        h = hashlib.sha1(en.encode("utf-8")).hexdigest()[:10] if en else ""
        if not en or f.get("profil_hash") == h or n_p >= PROFILE_PRO_LAUF or not budget.aktiv:
            continue
        text = budget.anfrage(PROMPT_PROFIL.format(name=titel[t].get("name") or t, ticker=t, text=en[:2500]), "Firmenprofil", 220)
        if text:
            f.update(profil_de=text.strip(), profil_hash=h)
            ablage.geaendert.add("_u")
            n_p += 1
    # News-Kurzfassung (nur bei neuen relevanten Meldungen)
    bew = N.bewertungen(tabellen.get("news_bewertung"))
    eigen = N.eigene_meldungen(tabellen.get("news_eigen"))
    th = M.thesen(tabellen.get("thesen"))
    grenze = N.iso(N.jetzt_utc() - timedelta(days=30))
    monate = sorted({N.monat_von(N.iso(None)), N.monat_von(grenze)})
    ms = [m for mon in monate for m in N.monatsliste(ablage.monat(mon), eigen, bew, mon) if m["zeit"] >= grenze]
    n_f = 0
    for t in sorted(titel):
        f = U.setdefault(t, dict(ticker=t, info={}))
        relevant = [m for m in N.top(ms, ticker=t, n=10) if m["eff"]["rel"] >= 3]
        alt_n = f.get("news") or {}
        if not relevant or not [m for m in relevant if m["id"] not in (alt_n.get("basis") or [])]:
            continue
        if _alter_h(alt_n.get("zeit")) < FIRMA_ABSTAND_H or not budget.aktiv:
            continue
        these = th.get(t, {}).get("these") if isinstance(th.get(t), dict) else ""
        zeilen = "\n".join(f"{m['zeit'][:10]} | {m['eff']['rel']:.1f} | {m['eff']['wirkung']} | {m['titel']} | {m.get('ki_grund') or ''}"
                           for m in relevant)
        text = budget.anfrage(PROMPT_FIRMA.format(name=titel[t].get("name") or t, ticker=t, sektor=titel[t].get("sektor") or "?",
                                                  these=f"unsere Kauf-These «{these}»" if these else "die Investment-These (Wachstum, Margen, Risiken)",
                                                  zeilen=zeilen), "Firmen-News", 260)
        if text:
            f["news"] = dict(text=text.strip(), zeit=N.iso(None), basis=[m["id"] for m in relevant])
            ablage.geaendert.add("_u")
            n_f += 1
    # alte Titel nach 45 Tagen ohne Bezug entfernen
    for t in [t for t in U if t not in titel and _alter_h(U[t].get("stand")) > 45 * 24]:
        U.pop(t)
        ablage.geaendert.add("_u")
    return dict(yahoo=len(alt), profile=n_p, firmen_news=n_f)


# ---------------------------------------------------------------------------
def lauf(store, ordner: str, token: str, api_key: str | None, modell: str | None, trocken: bool = False) -> list[str]:
    import portfolio as P

    info: list[str] = []
    tabellen = store.alle()
    v = ki.verbrauch(tabellen.get("ki_verbrauch"))
    budget = ki.Budget(api_key, modell, ki.deckel(tabellen.get("einstellungen")), v["usd"])
    info.append(f"KI: {'aktiv' if budget.aktiv else 'aus – ' + budget.grund_inaktiv()} · Monat bisher {v['usd']:.3f} von "
                f"{budget.deckel:.2f} USD")
    ablage = Ablage(ordner, token)
    titel = N.titel_liste(tabellen, P.sp500_liste())
    u = unternehmen(ablage, titel, tabellen, ki.Budget(None, None, 0, 0), info)   # zuerst nur Yahoo (Namen für die Suche)
    kand = sammeln(ablage, titel, store, info)
    n_ki = ki_bewerten(kand, titel, budget, tabellen, info)
    aufraeumen(ablage)
    n_fz = fazits(ablage, tabellen, budget, info)
    u2 = unternehmen(ablage, titel, tabellen, budget, info)
    info.append(f"KI: {n_ki} Meldungen bewertet, {n_fz} Monatsfazits, {u2['profile']} Profile, {u2['firmen_news']} Firmen-Kurzfassungen · "
                f"Yahoo: {u['yahoo']} Titel aktualisiert · Kosten dieses Laufs {budget.neu:.4f} USD")
    info += [f"Hinweis KI: {x}" for x in budget.fehler[:5]]
    info += [f"Hinweis Daten: {x}" for x in sorted(set(ablage.fehler))]
    status = dict(stand=N.iso(None), titel=len(titel), kandidaten=len(kand), ki_bewertet=n_ki, fazits=n_fz,
                  ki_aktiv=budget.aktiv, ki_grund=budget.grund_inaktiv(), kosten_lauf=round(budget.neu, 5), info=info)
    ablage.speichern(status)
    if budget.zeilen() and not trocken:
        try:
            store.anhaengen("ki_verbrauch", budget.zeilen())
        except Exception as e:  # noqa: BLE001
            info.append(f"Verbrauch nicht protokolliert: {type(e).__name__}")
    return info


def main():
    from storage import GoogleTabelleStore

    url, token = os.environ.get("SCRIPT_URL", ""), os.environ.get("SCRIPT_TOKEN", "")
    if not url or not token:
        sys.exit("SCRIPT_URL / SCRIPT_TOKEN fehlen.")
    ordner = sys.argv[sys.argv.index("--daten") + 1] if "--daten" in sys.argv else "_daten"
    os.makedirs(ordner, exist_ok=True)
    store = GoogleTabelleStore(url, token)
    for z in lauf(store, ordner, token, os.environ.get("ANTHROPIC_API_KEY") or None, os.environ.get("ANTHROPIC_MODEL") or None,
                  bool(os.environ.get("DRY_RUN"))):
        print(z)


if __name__ == "__main__":
    main()
