"""Sektor-News und Unternehmensdaten: Sammeln, Regel-Bewertung, Speicher (ohne Streamlit, für App und GitHub-Lauf).

Speicher
- Gesammelte Meldungen, KI-Einschätzungen, Monatsfazits und Unternehmensdaten liegen als verschlüsselte JSON-Dateien im
  Branch «daten» dieses Repos (news/AAAA-MM.json.enc, unternehmen.json.enc). So wächst die Google-Tabelle nicht, und
  Branch-Commits lösen keinen Neustart der App aus. Schlüssel = aus dem script_token abgeleitet (App und GitHub haben ihn
  schon) → niemand ausser euch kann lesen, welche Titel ihr anschaut. Wird das Token geändert, beginnt die Sammlung neu.
- Eure Eingaben (eigene Meldungen, Bewertungen, eigenes Fazit, Team-Rating) liegen wie alles andere in der Google-Tabelle.

Bewertung: effektiv() – Team-Durchschnitt, sobald jemand bewertet hat; sonst KI; sonst Regel.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from urllib.parse import quote_plus

import pandas as pd
import requests

from makro import config as C
from storage import zahl

REPO_STANDARD = "sophiaboess03-ctrl/makro-dashboard"
BRANCH = "daten"
ERSTER_MONAT = "2026-11"         # Vorlauf; erste Simulationsperiode = Dezember 2026
UA = {"User-Agent": "Mozilla/5.0 (Makro-Dashboard FHGR)"}

# ---------------------------------------------------------------------------
# Suchbegriffe je Sektor (Google News, englisch) und Wörter für die Zuordnung
# ---------------------------------------------------------------------------
SEKTOR_SUCHE = {
    "Energie": "oil stocks OR energy stocks OR Exxon OR Chevron OR OPEC OR natural gas prices OR refiners",
    "Grundstoffe": "materials stocks OR chemical makers OR copper prices OR steel prices OR Linde OR Freeport-McMoRan OR mining stocks",
    "Industrie": "industrial stocks OR Boeing OR Caterpillar OR defense contractors OR airlines OR railroads OR GE Aerospace",
    "Zykl. Konsum": "Amazon OR Tesla OR Home Depot OR retailers OR consumer discretionary stocks OR automakers OR holiday sales",
    "Basiskonsum": "consumer staples stocks OR Walmart OR Procter & Gamble OR Coca-Cola OR Costco OR PepsiCo OR grocery prices",
    "Gesundheit": "healthcare stocks OR pharma stocks OR FDA approval OR UnitedHealth OR Eli Lilly OR Medicare OR drug pricing",
    "Finanzen": "bank stocks OR JPMorgan OR Goldman Sachs OR bank earnings OR insurers OR Berkshire Hathaway OR credit card",
    "IT": "tech stocks OR Nvidia OR Microsoft OR Apple OR semiconductor stocks OR AI chips OR software stocks",
    "Kommunikation": "Alphabet OR Meta Platforms OR Netflix OR telecom stocks OR Verizon OR AT&T OR digital advertising",
    "Versorger": "utility stocks OR power demand OR electricity prices OR NextEra OR data center power OR nuclear power utilities",
    "Immobilien": "REIT OR REITs OR real estate stocks OR commercial real estate OR Prologis OR mortgage rates housing",
}
SEKTOR_WORTE = {
    "Energie": [r"\boil\b", r"crude", r"brent", r"\bwti\b", r"opec", r"natural gas", r"\blng\b", r"exxon", r"chevron", r"refin",
                r"conocophillips", r"drill", r"\böl\b", r"erdöl", r"erdgas"],
    "Grundstoffe": [r"chemical", r"copper", r"steel", r"alumin", r"mining", r"\bminer", r"linde", r"freeport", r"fertili", r"lithium",
                    r"gold price", r"kupfer", r"stahl", r"rohstoff"],
    "Industrie": [r"boeing", r"caterpillar", r"defen[cs]e", r"aerospace", r"airline", r"railroad", r"\bups\b", r"fedex", r"lockheed",
                  r"industrial", r"machinery", r"rüstung", r"luftfahrt"],
    "Zykl. Konsum": [r"amazon", r"tesla", r"home depot", r"retail", r"automaker", r"\bcar sales", r"holiday sales", r"black friday",
                     r"mcdonald", r"nike", r"starbucks", r"discretionary", r"detailhandel"],
    "Basiskonsum": [r"walmart", r"procter", r"coca-cola", r"costco", r"pepsico", r"grocer", r"staples", r"food prices", r"tobacco",
                    r"basiskonsum", r"lebensmittel"],
    "Gesundheit": [r"\bfda\b", r"pharma", r"drugmaker", r"biotech", r"unitedhealth", r"eli lilly", r"medicare", r"medicaid", r"health insur",
                   r"vaccine", r"glp-1", r"pfizer", r"merck", r"gesundheit"],
    "Finanzen": [r"\bbanks?\b", r"jpmorgan", r"goldman", r"morgan stanley", r"insurer", r"berkshire", r"credit card", r"lender",
                 r"\bvisa\b", r"mastercard", r"wells fargo", r"bank of america", r"citigroup", r"private credit", r"banken"],
    "IT": [r"nvidia", r"microsoft", r"apple", r"semiconductor", r"chipmaker", r"\bchips?\b", r"software", r"\bai\b", r"artificial intelligence",
           r"broadcom", r"\bamd\b", r"intel", r"oracle", r"cloud", r"halbleiter"],
    "Kommunikation": [r"alphabet", r"google", r"\bmeta\b", r"facebook", r"netflix", r"telecom", r"verizon", r"at&t", r"t-mobile",
                      r"advertis", r"streaming", r"disney", r"werbung"],
    "Versorger": [r"utilit", r"power demand", r"electricity", r"power grid", r"nextera", r"duke energy", r"nuclear", r"power plant",
                  r"versorger", r"strompreis"],
    "Immobilien": [r"\breits?\b", r"real estate", r"commercial property", r"office vacanc", r"prologis", r"mortgage", r"housing",
                   r"home sales", r"immobilien"],
}

# Rauschen: wird ohne KI aussortiert
RAUSCHEN = [
    r"\b(\d+|top|best|these|two|three|four|five)\s+(\w+\s+){0,3}stocks?\s+to\s+(buy|watch|own|sell|hold)",
    r"\bis\s+(it\s+)?(time|too late)\s+to\s+(buy|sell)", r"\bshould\s+you\s+(buy|sell)", r"\b(buy|sell)\s+now\?",
    r"\bstock\s+(forecast|prediction)", r"price\s+prediction", r"\bmillionaire", r"\bretire\b.*\bstock",
    r"\bsponsored\b", r"\bpaid\s+(post|content)\b", r"\bshares?\s+(sold|bought|acquired)\s+by\b", r"\bposition\s+(in|raised|lowered)\b",
    r"\b(stake|holdings?)\s+(in|trimmed|boosted|raised|lifted|cut)\b.*\bby\b", r"\bunusual\s+options\b", r"\bdividend\s+stocks?\s+to\b",
    r"\bwhy\s+.{0,40}\s+(stock|shares)\s+(is|are)\s+(soaring|sinking|up|down|falling|rising)\s+today\b",
]
RAUSCHEN_QUELLEN = {"marketbeat", "defense world", "etf daily news", "the motley fool", "investorsobserver", "ainvest", "stocktitan",
                    "tickerreport", "zacks investment research", "simply wall st", "gurufocus", "insidermonkey", "insider monkey",
                    "benzinga pro", "dailypolitical", "thelincolnianonline"}
TIER1 = {"reuters", "bloomberg", "the wall street journal", "wall street journal", "wsj", "financial times", "cnbc",
         "associated press", "ap news", "barron's", "marketwatch", "the new york times", "new york times", "axios", "the economist",
         "fortune", "yahoo finance", "politico", "s&p global", "fierce pharma", "stat", "the information", "nikkei asia"}
STARK = [r"earnings", r"quarterly (results|profit|revenue)", r"\bguidance\b", r"\boutlook\b", r"\bforecast", r"\bbeats?\b", r"\bmiss(es|ed)?\b",
         r"downgrad", r"upgrad", r"\bfda\b", r"approv", r"merger", r"acqui", r"takeover", r"\bdeal\b", r"tariff", r"sanction", r"\bopec\b",
         r"rate (cut|hike)", r"\bfed\b", r"federal reserve", r"layoff", r"job cuts", r"bankrupt", r"lawsuit", r"antitrust", r"\bprobe\b",
         r"investigation", r"recall", r"\bstrike\b", r"dividend (cut|hike|increase)", r"buyback", r"\bsec\b", r"record", r"plunge",
         r"soar", r"surge", r"tumble", r"warns?\b", r"regulat", r"ban\b", r"export control", r"ceo (resign|steps down|ousted)"]
POSITIV = [r"\bbeats?\b", r"\braises?\b", r"\braised\b", r"surge", r"soar", r"jump", r"rall(y|ies)", r"record high", r"upgrad", r"approv",
           r"\bwins?\b", r"\bgains?\b", r"\brises?\b", r"strong(er)? demand", r"boost", r"rebound", r"tops estimates", r"buyback",
           r"dividend (hike|increase)", r"rate cut"]
NEGATIV = [r"\bmiss(es|ed)?\b", r"\bcuts?\b", r"plunge", r"\bfall(s|ing)?\b", r"slump", r"downgrad", r"\bprobe\b", r"lawsuit", r"recall",
           r"\bwarns?\b", r"layoff", r"job cuts", r"tumble", r"\bdrops?\b", r"\bsinks?\b", r"bankrupt", r"weak(er)? demand", r"slowdown",
           r"tariff", r"sanction", r"\bban\b", r"investigation", r"rate hike", r"losses", r"\bslides?\b"]
STOPP = {"the", "and", "for", "with", "from", "that", "this", "after", "over", "into", "amid", "says", "said", "will", "its", "are",
         "was", "has", "have", "new", "stock", "stocks", "shares", "share", "inc", "corp", "company", "how", "why", "what", "who",
         "more", "than", "but", "not", "you", "your", "about", "today", "week", "year", "report", "reports"}


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------
def jetzt_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(d: datetime | None) -> str:
    return (d or jetzt_utc()).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ts(x) -> pd.Timestamp | None:
    try:
        t = pd.Timestamp(str(x))
        return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    except Exception:  # noqa: BLE001
        return None


def monat_von(x) -> str:
    t = ts(x)
    return t.strftime("%Y-%m") if t is not None else jetzt_utc().strftime("%Y-%m")


def monate_bis_heute(start: str = ERSTER_MONAT) -> list[str]:
    out, m = [], pd.Period(start, "M")
    ende = pd.Period(jetzt_utc().strftime("%Y-%m"), "M")
    while m <= ende:
        out.append(str(m))
        m += 1
    return out


@lru_cache(maxsize=50000)
def woerter(t: str) -> frozenset[str]:
    return frozenset(w for w in re.findall(r"[a-z0-9äöü&]+", (t or "").lower()) if len(w) >= 3 and w not in STOPP)


def aehnlich(a: str, b: str, schwelle: float = 0.55) -> bool:
    wa, wb = woerter(a), woerter(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= schwelle


def news_id(titel: str, quelle: str = "") -> str:
    return hashlib.sha1(re.sub(r"\W+", " ", (titel or "").lower()).strip()[:120].encode("utf-8")).hexdigest()[:12]


def _treffer(muster: list[str], text: str) -> list[str]:
    low = (text or "").lower()
    return [m for m in muster if re.search(m, low)]


def kurzname(name: str) -> str:
    """«Exxon Mobil Corporation» → «Exxon Mobil» (für die Suche und das Erkennen in Schlagzeilen)."""
    n = re.sub(r"\(.*?\)", "", name or "")
    n = re.sub(r",?\s+(inc\.?|incorporated|corporation|corp\.?|company|co\.?|holdings?|group|plc|ltd\.?|n\.v\.|s\.a\.|class [a-c])\b\.?",
               "", n, flags=re.I)
    n = re.sub(r"^the\s+", "", n.strip(), flags=re.I).strip(" ,.")
    teile = n.split()
    return " ".join(teile[:3]) if teile else (name or "")


# ---------------------------------------------------------------------------
# Verschlüsselte Ablage im Branch «daten»
# ---------------------------------------------------------------------------
def _fernet(token: str):
    from cryptography.fernet import Fernet

    key = base64.urlsafe_b64encode(hashlib.sha256(("makro-dashboard-daten|" + (token or "")).encode("utf-8")).digest())
    return Fernet(key)


def verschluesseln(obj, token: str) -> bytes:
    import zlib

    roh = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _fernet(token).encrypt(zlib.compress(roh, 9))


def entschluesseln(roh: bytes, token: str):
    import zlib

    return json.loads(zlib.decompress(_fernet(token).decrypt(roh)).decode("utf-8"))


def lade_datei(name: str, token: str, repo: str | None = None, lokal: str | None = None, timeout: int = 20):
    """name z. B. «news/2026-12.json». Gibt (objekt|None, fehler|None) zurück. lokal = Ordner (GitHub-Lauf, Tests)."""
    datei = name + ".enc"
    try:
        if lokal:
            p = os.path.join(lokal, datei)
            if not os.path.exists(p):
                return None, None
            roh = open(p, "rb").read()
        else:
            url = f"https://raw.githubusercontent.com/{repo or REPO_STANDARD}/{BRANCH}/{datei}"
            r = requests.get(url, timeout=timeout, headers=UA)
            if r.status_code == 404:
                return None, None
            if r.status_code != 200:
                return None, f"Daten-Branch nicht erreichbar (HTTP {r.status_code})"
            roh = r.content
    except Exception as e:  # noqa: BLE001
        return None, f"Daten nicht lesbar ({type(e).__name__})"
    try:
        return entschluesseln(roh, token), None
    except Exception:  # noqa: BLE001
        return None, "Daten nicht entschlüsselbar (script_token geändert?) – die Sammlung beginnt beim nächsten Lauf neu."


def speichere_datei(ordner: str, name: str, obj, token: str):
    p = os.path.join(ordner, name + ".enc")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(verschluesseln(obj, token))


def leerer_monat(monat: str) -> dict:
    return dict(monat=monat, meldungen=[], fazit={}, newsletter_ids=[], stand=None, laeufe=[])


# ---------------------------------------------------------------------------
# Google News
# ---------------------------------------------------------------------------
def google_news(query: str, stunden: int = 30, timeout: int = 12) -> list[dict]:
    url = f"https://news.google.com/rss/search?q={quote_plus(query + ' when:2d')}&hl=en-US&gl=US&ceid=US:en"
    r = requests.get(url, timeout=timeout, headers=UA)
    root = ET.fromstring(r.content)
    grenze = jetzt_utc() - timedelta(hours=stunden)
    out = []
    for it in root.iter("item"):
        titel = (it.findtext("title") or "").strip()
        quelle = (it.findtext("source") or "").strip()
        if quelle and titel.endswith(" - " + quelle):
            titel = titel[: -len(quelle) - 3].strip()
        try:
            zeit = parsedate_to_datetime(it.findtext("pubDate"))
        except Exception:  # noqa: BLE001
            zeit = None
        if zeit is None or zeit < grenze or not titel:
            continue
        out.append(dict(titel=titel, link=it.findtext("link") or "", quelle=quelle, zeit=iso(zeit)))
    return out


def gruppieren(items: list[dict]) -> list[dict]:
    """Gleiche Story von mehreren Quellen → eine Meldung (älteste Fassung einer Top-Quelle bevorzugt), quellen = alle."""
    gruppen: list[list[dict]] = []
    for i in sorted(items, key=lambda x: x["zeit"]):
        for g in gruppen:
            if aehnlich(g[0]["titel"], i["titel"]):
                g.append(i)
                break
        else:
            gruppen.append([i])
    out = []
    for g in gruppen:
        haupt = next((x for x in g if x["quelle"].lower() in TIER1), g[0])
        quellen = sorted({x["quelle"] for x in g if x["quelle"]})
        out.append({**haupt, "quellen": quellen})
    return out


# ---------------------------------------------------------------------------
# Regel-Bewertung (kostenlos)
# ---------------------------------------------------------------------------
def ist_rauschen(titel: str, quelle: str) -> bool:
    return bool(_treffer(RAUSCHEN, titel)) or (quelle or "").strip().lower() in RAUSCHEN_QUELLEN


def sektoren_aus_text(text: str, max_n: int = 2) -> list[str]:
    treffer = [(s, len(_treffer(m, text))) for s, m in SEKTOR_WORTE.items()]
    treffer = [x for x in treffer if x[1]]
    return [s for s, _ in sorted(treffer, key=lambda x: -x[1])[:max_n]]


def regel_bewertung(titel: str, quelle: str, quellen: list[str] | None, eigene: dict | None = None,
                    basis: int = 1) -> dict:
    """Relevanz 1–5 und Wirkung (+/−/0) nach Regeln. eigene = {Ticker: Kurzname} der Titel im Bestand/Watchlist/Probe."""
    eigene = eigene or {}
    gr = []
    r = basis
    q = {x.lower() for x in (quellen or [quelle])}
    if q & TIER1:
        r += 1
        gr.append("Top-Quelle")
    n = len(quellen or [])
    if n >= 3:
        r += 1
        gr.append(f"{n} Quellen")
    if n >= 6:
        r += 1
    if _treffer(STARK, titel):
        r += 1
        gr.append("starkes Stichwort")
    getroffen = [t for t, nm in eigene.items() if re.search(rf"\b{re.escape(t)}\b", titel) or
                 (nm and re.search(rf"\b{re.escape(nm.split()[0])}\b", titel, re.I) and len(nm.split()[0]) >= 4)]
    if getroffen:
        r += 1
        gr.append("eigener Titel")
    pos, neg = len(_treffer(POSITIV, titel)), len(_treffer(NEGATIV, titel))
    w = "+" if pos > neg else "-" if neg > pos else "0"
    return dict(rel=max(1, min(5, r)), wirkung=w, gruende=gr, ticker=getroffen)


# ---------------------------------------------------------------------------
# Effektive Bewertung: Team > KI > Regel
# ---------------------------------------------------------------------------
def bewertungen(tab: pd.DataFrame | None) -> dict:
    """{news_id: {person: neuste Bewertung}}"""
    out: dict = {}
    if tab is None or tab.empty:
        return out
    for _, r in tab.sort_values("zeit").iterrows():
        out.setdefault(str(r["news_id"]), {})[str(r["person"])] = dict(
            relevanz=zahl(r["relevanz"]), wirkung=str(r["wirkung"] or ""), kommentar=str(r["kommentar"] or "").strip(),
            ausblenden=str(r.get("ausblenden", "")).strip().lower() in ("1", "true", "ja", "1.0"), zeit=str(r["zeit"]))
    return out


def effektiv(m: dict, bew: dict) -> dict:
    team = [b for b in bew.get(m["id"], {}).values() if b["relevanz"] is not None]
    alle = bew.get(m["id"], {})
    kommentare = [(p, b["kommentar"]) for p, b in alle.items() if b["kommentar"]]
    ausgeblendet = any(b["ausblenden"] for b in alle.values())
    if team:
        rel = sum(b["relevanz"] for b in team) / len(team)
        stimmen = [b["wirkung"] for b in team if b["wirkung"] in ("+", "-", "0")]
        if stimmen:
            w = max(("+", "-", "0"), key=lambda x: (stimmen.count(x), x == (m.get("ki_wirkung") or m.get("regel_wirkung"))))
        else:
            w = m.get("ki_wirkung") or m.get("regel_wirkung") or "0"
        return dict(rel=rel, wirkung=w, quelle=f"Team ({len(team)})", kommentare=kommentare, ausgeblendet=ausgeblendet)
    if m.get("ki_rel") is not None:
        return dict(rel=float(m["ki_rel"]), wirkung=m.get("ki_wirkung") or "0", quelle="KI", kommentare=kommentare,
                    ausgeblendet=ausgeblendet)
    return dict(rel=float(m.get("regel_rel") or 1), wirkung=m.get("regel_wirkung") or "0", quelle="Regel", kommentare=kommentare,
                ausgeblendet=ausgeblendet)


def eigene_meldungen(tab: pd.DataFrame | None) -> list[dict]:
    """Vom Team erfasste Meldungen (Blatt news_eigen) im gleichen Format wie die gesammelten."""
    if tab is None or tab.empty:
        return []
    d = tab.sort_values("zeit").drop_duplicates("id", keep="last")
    d = d[~d["geloescht"].astype(str).str.strip().isin(["1", "1.0", "True", "true"])]
    out = []
    for _, r in d.iterrows():
        sek = [s.strip() for s in str(r["sektoren"]).split(",") if s.strip() in C.SEKTOREN]
        datum = str(r["datum"] or r["zeit"])[:10]
        out.append(dict(id=str(r["id"]), titel=str(r["titel"]), link=str(r["link"] or ""), quelle=str(r["quelle"] or "Team"),
                        quellen=[str(r["quelle"] or "Team")], zeit=f"{datum}T12:00:00Z", sektoren=sek or [],
                        ticker=[t.strip().upper() for t in str(r["ticker"] or "").split(",") if t.strip()], art="team",
                        text=str(r["text"] or ""), erfasst_von=str(r["person"]), regel_rel=None, regel_wirkung="0",
                        ki_rel=None, ki_wirkung=None, ki_grund=""))
    return out


def monatsliste(daten: dict | None, eigen: list[dict], bew: dict, monat: str) -> list[dict]:
    """Alle Meldungen eines Monats (gesammelt + eigene), mit effektiver Bewertung, neuste zuerst."""
    ms = list((daten or {}).get("meldungen") or []) + [m for m in eigen if monat_von(m["zeit"]) == monat]
    out = []
    for m in ms:
        e = effektiv(m, bew)
        out.append({**m, "eff": e})
    return sorted(out, key=lambda x: x["zeit"], reverse=True)


def top(meldungen: list[dict], sektor: str | None = None, n: int = 10, min_rel: float = 0, ticker: str | None = None) -> list[dict]:
    ms = [m for m in meldungen if not m["eff"]["ausgeblendet"] and m["eff"]["rel"] >= min_rel
          and (sektor is None or sektor in (m.get("sektoren") or []))
          and (ticker is None or ticker in (m.get("ticker") or []))]
    return sorted(ms, key=lambda m: (round(m["eff"]["rel"], 1), m["zeit"]), reverse=True)[:n]


def regel_fazit(ms: list[dict]) -> str:
    """Kostenlose Zusammenfassung: Anzahl, Saldo der Wirkung, wichtigste Schlagzeilen."""
    if not ms:
        return "Noch keine relevanten Meldungen in diesem Monat."
    pos = sum(1 for m in ms if m["eff"]["wirkung"] == "+")
    neg = sum(1 for m in ms if m["eff"]["wirkung"] == "-")
    wichtig = [m for m in ms if m["eff"]["rel"] >= 4]
    saldo = "eher positiv" if pos > neg + 1 else "eher negativ" if neg > pos + 1 else "gemischt"
    t = (f"{len(ms)} Meldungen, davon {len(wichtig)} wichtig (Relevanz ab 4) · Wirkung {saldo} ({pos} positiv, {neg} negativ). "
         "Wichtigste: " + "; ".join(m["titel"] for m in top(ms, n=3)) + ".")
    return t


# ---------------------------------------------------------------------------
# Welche Titel? Bestand, Watchlist, Probe-Portfolio
# ---------------------------------------------------------------------------
def probe_analyse_id(tabellen: dict) -> str | None:
    e = tabellen.get("einstellungen")
    if e is not None and not e.empty:
        z = e[e["schluessel"] == "unternehmen|analyse_id"].sort_values("zeit")
        if len(z) and str(z.iloc[-1]["wert"]).strip():
            return str(z.iloc[-1]["wert"]).strip()
    a = tabellen.get("analysen")
    if a is not None and not a.empty:
        return str(a.sort_values("zeit").iloc[-1]["id"])
    return None


def probe_ticker(tabellen: dict, analyse_id: str | None = None) -> list[str]:
    a = tabellen.get("analysen")
    aid = analyse_id or probe_analyse_id(tabellen)
    if a is None or a.empty or not aid:
        return []
    z = a[a["id"].astype(str) == aid].sort_values("zeit")
    if not len(z):
        return []
    try:
        e = json.loads(z.iloc[-1]["eingabe"])
    except Exception:  # noqa: BLE001
        return []
    return sorted({str(x.get("Ticker") or "").strip().upper().replace(".", "-") for x in e.get("aktien", [])} - {""})


def titel_liste(tabellen: dict, sp500: dict | None = None) -> dict:
    """{Ticker: {name, sektor, herkunft: [Bestand|Watchlist|Probe-Portfolio]}}"""
    import monitoring as M
    import portfolio as P

    out: dict = {}

    def add(t, herkunft, name="", sektor=""):
        t = str(t or "").strip().upper().replace(".", "-")
        if not t:
            return
        d = out.setdefault(t, dict(name="", sektor="", herkunft=[]))
        if herkunft not in d["herkunft"]:
            d["herkunft"].append(herkunft)
        d["name"] = d["name"] or (name if name and name != "nan" else "")
        d["sektor"] = d["sektor"] or (sektor if sektor in C.SEKTOREN else "")

    tx = P.transaktionen(tabellen.get("transaktionen"))
    if len(tx):
        pos, _, _ = P.positionen(tx)
        for p in pos.values():
            if p["klasse"] == "Aktie":
                add(p["ticker"], "Bestand", str(p["name"] or ""), str(p["sektor"] or ""))
    for w in M.watchlist(tabellen.get("watchlist")):
        add(w["ticker"], "Watchlist", w.get("name", ""), w.get("sektor", ""))
    for t in probe_ticker(tabellen):
        add(t, "Probe-Portfolio")
    sp500 = sp500 or {}
    for t, d in out.items():
        if t in sp500:
            d["name"] = d["name"] or sp500[t][0]
            d["sektor"] = d["sektor"] or sp500[t][1]
    return out


# ---------------------------------------------------------------------------
# Unternehmensdaten von Yahoo (ohne KI)
# ---------------------------------------------------------------------------
INFO_FELDER = ["shortName", "longName", "sector", "industry", "longBusinessSummary", "website", "fullTimeEmployees", "marketCap",
               "currentPrice", "regularMarketPrice", "previousClose", "trailingPE", "forwardPE", "pegRatio", "priceToBook",
               "enterpriseToEbitda", "trailingEps", "forwardEps", "revenueGrowth", "earningsGrowth", "earningsQuarterlyGrowth",
               "grossMargins", "operatingMargins", "profitMargins", "returnOnEquity", "debtToEquity", "freeCashflow", "totalRevenue",
               "dividendYield", "payoutRatio", "beta", "fiftyTwoWeekLow", "fiftyTwoWeekHigh", "fiftyDayAverage", "twoHundredDayAverage",
               "targetMeanPrice", "targetMedianPrice", "targetHighPrice", "targetLowPrice", "numberOfAnalystOpinions",
               "recommendationMean", "recommendationKey", "currency"]


def _sauber(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:  # noqa: BLE001
            return str(v)
    return v


def firma_yahoo(ticker: str) -> dict:
    """Profil, Kennzahlen, Analysten (Konsens, Verlauf, Kursziele, Up-/Downgrades), nächster Quartalstermin."""
    import yfinance as yf

    tk = yf.Ticker(ticker)
    d: dict = dict(ticker=ticker, stand=iso(None), fehler=[])
    try:
        info = tk.info or {}
        d["info"] = {k: _sauber(info.get(k)) for k in INFO_FELDER if info.get(k) is not None}
        dy = d["info"].get("dividendYield")
        if dy is not None and dy > 1:           # neuere yfinance-Versionen liefern Prozent
            d["info"]["dividendYield"] = dy / 100
    except Exception as e:  # noqa: BLE001
        d["info"] = {}
        d["fehler"].append(f"info: {type(e).__name__}")
    try:
        rec = tk.recommendations
        if rec is not None and len(rec):
            d["empfehlungen"] = [{k: _sauber(v) for k, v in z.items()} for z in rec.head(4).to_dict("records")]
    except Exception as e:  # noqa: BLE001
        d["fehler"].append(f"Empfehlungen: {type(e).__name__}")
    try:
        ud = tk.upgrades_downgrades
        if ud is not None and len(ud):
            ud = ud.reset_index()
            dc = next((c for c in ud.columns if "date" in c.lower()), ud.columns[0])
            ud[dc] = pd.to_datetime(ud[dc], errors="coerce")
            grenze = pd.Timestamp.now(tz=ud[dc].dt.tz) - pd.Timedelta(days=120) if ud[dc].dt.tz is not None else \
                pd.Timestamp.now() - pd.Timedelta(days=120)
            ud = ud[ud[dc] >= grenze].sort_values(dc, ascending=False).head(25)
            d["ratings"] = [dict(datum=pd.Timestamp(z[dc]).strftime("%Y-%m-%d"), firma=str(z.get("Firm", "")),
                                 von=str(z.get("FromGrade", "") or ""), zu=str(z.get("ToGrade", "") or ""),
                                 aktion=str(z.get("Action", "") or ""),
                                 kz_alt=_sauber(z.get("priorPriceTarget")), kz_neu=_sauber(z.get("currentPriceTarget")))
                            for _, z in ud.iterrows()]
    except Exception as e:  # noqa: BLE001
        d["fehler"].append(f"Up-/Downgrades: {type(e).__name__}")
    try:
        cal = tk.calendar
        e = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if e:
            e = e[0] if isinstance(e, (list, tuple)) else e
            d["quartalszahlen"] = pd.Timestamp(e).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        pass
    return d


def firmen_yahoo(ticker: list[str], timeout: int = 90) -> dict:
    out = {}
    ex = ThreadPoolExecutor(max_workers=4)
    futs = {t: ex.submit(firma_yahoo, t) for t in ticker}
    for t, f in futs.items():
        try:
            out[t] = f.result(timeout=timeout)
        except Exception as e:  # noqa: BLE001
            out[t] = dict(ticker=t, stand=iso(None), info={}, fehler=[type(e).__name__])
    ex.shutdown(wait=False)
    return out


def analysten_kennzahlen(f: dict | None, kurs: float | None = None, tage: int = 30) -> dict:
    """Konsens, Kursziel-Abstand und Herabstufungen der letzten Tage (für Seite und Monitoring)."""
    f = f or {}
    i = f.get("info") or {}
    kurs = kurs or i.get("currentPrice") or i.get("regularMarketPrice")
    ziel = i.get("targetMeanPrice")
    grenze = (pd.Timestamp.today() - pd.Timedelta(days=tage)).strftime("%Y-%m-%d")
    neu = [r for r in f.get("ratings") or [] if r["datum"] >= grenze]
    down = [r for r in neu if r["aktion"].lower() == "down"]
    up = [r for r in neu if r["aktion"].lower() == "up"]
    e0 = (f.get("empfehlungen") or [{}])[0]
    n = sum(int(e0.get(k) or 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell"))
    return dict(kurs=kurs, ziel=ziel, upside=(ziel / kurs - 1) * 100 if ziel and kurs else None, down=down, up=up,
                konsens=i.get("recommendationKey"), mean=i.get("recommendationMean"), n=i.get("numberOfAnalystOpinions") or n or None,
                verteilung={k: int(e0.get(k) or 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell")} if e0 else {})


KONSENS_DE = {"strong_buy": "Stark kaufen", "buy": "Kaufen", "hold": "Halten", "underperform": "Untergewichten", "sell": "Verkaufen",
              "strong_sell": "Stark verkaufen", "none": "–"}
