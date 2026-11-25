"""Monitoring: Signale → Fälle (Exceptions) → Zuständige → protokollierte Reaktion.

Gemeinsame Logik für die App (Seite «Monitoring») und den automatischen Lauf (monitoring_job.py, GitHub Actions).

Ebenen
- Titel:       gehaltene Aktien (Verlust/Stop, Rückgang vom Hoch, relativ zum Sektor-ETF, Trend, Tagesbewegung,
               Quartalszahlen, Gewichtsdrift, Kursziel/Stop/Überprüfung aus der Kauf-These)
- Watchlist:   Kaufkandidaten mit Einstiegsmarke
- Einhaltung:  IPS-Grenzen (Bänder, Duration, Sektorabweichung, Titel-Maximum, Mindestzahl, Mandat, Ausschlüsse, Cash),
               Frühwarnung, Abweichung von der Taktik, Sektor Ist vs. Ziel, Sektor-Trigger
- IPS-Annahmen: Risiko- und Renditeschätzungen passen nicht mehr zum IPS (→ IPS überprüfen)
- Makro:       rote Ampeln, Kombi-Signale, Risiko-Warnsignale

Fall-Logik (abgleich)
- Ein Signal ohne offenen Fall → neuer Fall (Status «Neu»).
- Wird ein Signal schärfer (Info → Prüfen → Handeln), steigt die Stufe des offenen Falls (erneute Meldung).
- Erledigte Fälle bleiben zu, solange das Signal ununterbrochen besteht. Neu gemeldet wird erst, wenn das Signal
  verschwindet und wiederkommt, schärfer wird als beim Abschluss oder die beim Abschluss gesetzte neue Schwelle erreicht.
- Verschwindet das Signal eines offenen Falls, schliesst die App ihn als «Erloschen» (Ereignisse wie eine aussergewöhnliche
  Tagesbewegung bleiben offen, bis das Team reagiert hat).
- Puffer gegen Hin und Her: Ein bekanntes Signal gilt erst als verschwunden, wenn der Wert den Puffer hinter der Schwelle liegt.

Gespeichert wird nur angehängt (wie überall im Dashboard): Blätter faelle, monitor_log, kontakte, zustaendigkeit,
watchlist, thesen; Schwellen in «einstellungen» (Schlüssel monitor|…).
"""
from __future__ import annotations

import html
import json
import math
import uuid
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from makro import config as C
from makro import logic as L
from storage import zahl

# ---------------------------------------------------------------------------
# Grundbegriffe
# ---------------------------------------------------------------------------
STUFEN = ["Info", "Prüfen", "Handeln"]
RANG = {s: i for i, s in enumerate(STUFEN)}
STUFE_FARBE = {"Info": "#1F4E8C", "Prüfen": "#B7791F", "Handeln": "#B93A32"}
OFFEN = ("Neu", "In Prüfung")
STATUS = ["Neu", "In Prüfung", "Erledigt", "Erloschen"]
REAKTIONEN = ["Angepasst", "Bewusst nicht angepasst", "Fehlalarm"]
EBENEN = ["Titel", "Watchlist", "Einhaltung", "IPS-Annahmen", "Makro"]
# Bereiche ausserhalb der Sektoren gehen an alle Teammitglieder
BEREICHE_TEAM = ["Allokation", "Anleihen", "IPS", "Makro"]

FAELLE_SPALTEN = ["zeit", "person", "id", "schluessel", "ebene", "bereich", "ticker", "titel", "text", "stufe", "wert",
                  "einheit", "richtung", "art", "regel", "status", "reaktion", "begruendung", "neue_schwelle", "entscheid_id",
                  "bearbeiter", "signal_aktiv", "erstmals", "vorgaenger", "snapshot", "hinweis"]

# Schwellen: Schlüssel → (Bezeichnung, Startwert, Einheit, Gruppe)
SCHWELLEN = {
    "verlust_pruefen": ("Verlust seit Kauf: Prüfen ab", -10.0, "%", "Titel"),
    "verlust_handeln": ("Verlust seit Kauf: Handeln ab (Entscheid Pflicht)", -15.0, "%", "Titel"),
    "drawdown": ("Rückgang vom Höchststand seit Kauf: Prüfen ab", -12.0, "%", "Titel"),
    "rel_tage": ("Vergleich mit dem Sektor-ETF über", 20.0, "Handelstage", "Titel"),
    "rel_schwach": ("Schwächer als der Sektor-ETF: Prüfen ab", -8.0, "Pp.", "Titel"),
    "rel_stark": ("Stärker als der Sektor-ETF: Info ab (Gewinnmitnahme prüfen)", 10.0, "Pp.", "Titel"),
    "sigma": ("Aussergewöhnliche Tagesbewegung: Prüfen ab", 3.0, "Standardabweichungen", "Titel"),
    "sigma_tage": ("Standardabweichung aus den letzten", 60.0, "Handelstagen", "Titel"),
    "sigma_min": ("Tagesbewegung mindestens", 2.0, "%", "Titel"),
    "earnings_tage": ("Quartalszahlen ankündigen", 5.0, "Handelstage vorher", "Titel"),
    "drift": ("Position seit Kauf relativ gewachsen: Info ab", 50.0, "%", "Titel"),
    "analyst_down": ("Herabstufungen durch Analysten: Prüfen ab", 2.0, "Häuser in 30 Tagen", "Titel"),
    "kursziel_ueber": ("Kurs über dem mittleren Analysten-Kursziel: Prüfen ab", 0.0, "% über dem Kursziel", "Titel"),
    "band_nah": ("Frühwarnung: Abstand zur IPS-Grenze höchstens", 1.0, "Pp.", "Einhaltung"),
    "titel_nah": ("Frühwarnung Titel-Maximum: Abstand höchstens", 0.5, "Pp.", "Einhaltung"),
    "taktik_tol": ("Abweichung von der Taktik: Prüfen ab", 2.0, "Pp.", "Einhaltung"),
    "sektor_tol": ("Sektor |Ist − Ziel|: Prüfen ab", 1.0, "Pp. des Aktienteils", "Einhaltung"),
    "duration_tol": ("Duration: Abweichung von der Taktik, Prüfen ab", 1.0, "Jahre", "Einhaltung"),
    "verlust_ips_pruefen": ("Rückgang des Portfolios vom Höchststand: Prüfen ab", 50.0, "% des tragbaren Verlusts", "Einhaltung"),
    "verlust_ips_handeln": ("Rückgang des Portfolios vom Höchststand: Handeln ab", 80.0, "% des tragbaren Verlusts", "Einhaltung"),
    "puffer_kurs": ("Puffer gegen Hin und Her bei Kurs-Signalen", 2.0, "Pp.", "Ablauf"),
    "puffer_pp": ("Puffer gegen Hin und Her bei Gewichten", 0.5, "Pp.", "Ablauf"),
    "erinnern_pruefen": ("Offene «Prüfen»-Fälle erneut melden nach", 3.0, "Handelstagen", "Ablauf"),
}

# Was bei welchem Signal zu prüfen ist (steht in Mail und App beim Fall)
HINWEIS = {
    "verlust": "Gilt die Kauf-These noch? Halten, nachkaufen oder verkaufen – Entscheid mit Begründung festhalten.",
    "drawdown": "Rückgang einordnen: Markt, Sektor oder titelspezifisch (News, Zahlen)? These prüfen.",
    "rel_schwach": "Titelspezifische Schwäche? News und Gewinnschätzungen prüfen; allenfalls durch einen anderen Titel im Sektor ersetzen.",
    "rel_stark": "Gewinnmitnahme oder Rebalancing auf das Zielgewicht prüfen.",
    "sma200": "Trend gebrochen – zusammen mit Bewertung und These einordnen.",
    "death_cross": "Mittelfristiger Abwärtstrend (50-Tage- unter 200-Tage-Linie) – These und Gewicht überprüfen.",
    "sprung": "Grund der Bewegung klären (News, Zahlen, Herabstufung) und entscheiden, ob die These betroffen ist.",
    "earnings": "Vor den Zahlen: Erwartungen, Optionen-implizite Bewegung und Positionsgrösse prüfen.",
    "drift": "Position ist durch Kursgewinne gewachsen – Gewicht gegenüber Sektor-Ziel und Titel-Maximum prüfen.",
    "analyst_down": "Mehrere Herabstufungen – Begründungen lesen (Seite «Unternehmen»), Kauf-These und Gewicht prüfen.",
    "analyst_ziel": "Kurs liegt über dem mittleren Kursziel der Analysten – Gewinnmitnahme prüfen oder ein höheres eigenes Kursziel begründen.",
    "kursziel": "Kursziel erreicht – Gewinne mitnehmen oder neues Kursziel begründen.",
    "stop": "Stop erreicht – verkaufen oder das Halten ausdrücklich begründen.",
    "review": "Termin für die Überprüfung der These erreicht.",
    "marke": "Einstiegsmarke erreicht – Kauf prüfen (Sektor-Ziel, Cash, IPS).",
    "band": "IPS-Grenze verletzt – sofort zurückführen oder Abweichung begründen.",
    "nah": "Kurz vor einer IPS-Grenze – Spielraum prüfen, bevor sie verletzt wird.",
    "taktik": "Ist weicht von der beschlossenen Taktik ab – rebalancieren oder Taktik anpassen.",
    "rebal": "Rebalancing-Toleranz laut IPS überschritten – rebalancieren.",
    "sektor_ziel": "Sektor weicht vom Ziel ab – Titel zu- oder verkaufen oder Ziel anpassen.",
    "sektor_ips": "Maximale Sektorabweichung laut IPS überschritten – zurückführen.",
    "trigger": "Vorab festgelegter Ausstiegs-Trigger ausgelöst – Gewichtung des Sektors überprüfen.",
    "titel_max": "Maximales Gewicht je Titel laut IPS überschritten – Position reduzieren.",
    "titel_nah": "Titel nahe am IPS-Maximum – weiteres Wachstum beobachten.",
    "anzahl": "Mindestzahl Aktien laut IPS unterschritten – Titel ergänzen.",
    "mandat": "Titel nicht im S&P 500 – Mandat prüfen und verkaufen.",
    "ausschluss": "Titel bzw. Sektor ist laut IPS ausgeschlossen – verkaufen.",
    "cash": "Cash negativ – Käufe übersteigen das Kapital.",
    "duration": "Duration ausserhalb der IPS-Grenzen – Laufzeiten anpassen.",
    "duration_taktik": "Duration weicht von der Taktik ab – Laufzeiten anpassen oder Taktik ändern.",
    "laufzeit": "Laufzeit über 10 Jahre (Teamregel aus der Analyse) – prüfen.",
    "korridor": "Die beschlossene Taktik liegt ausserhalb der IPS-Bänder – Taktik korrigieren.",
    "verlust_ips": "Portfolio nähert sich dem maximal tragbaren Verlust laut IPS – Risiko reduzieren oder begründen.",
    "te_real": "Realisierter Tracking Error über dem IPS-Ziel – aktive Wetten verkleinern oder begründen.",
    "ips_annahme": "Annahme im IPS passt nicht mehr zur Lage – IPS bzw. Taktik überprüfen.",
    "makro": "Vorab festgelegte Reaktion prüfen und im Team besprechen.",
    "ki_punkt": "Beobachtungspunkt aus der KI-Analyse ausgelöst – Lage einordnen und entscheiden, ob Gewichte angepasst werden.",
    "unbekannt": "Sektor des Titels unbekannt – auf der Seite Portfolio zuordnen.",
}

# US-Börsenfeiertage (NYSE) für das Zählen von Handelstagen
NYSE_FEIERTAGE = {pd.Timestamp(d) for d in (
    "2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
    "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24")}


def jetzt() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _z(x):
    v = zahl(x)
    return None if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))) else float(v)


def _txt(x) -> str:
    s = "" if x is None else str(x)
    return "" if s.lower() in ("nan", "none", "nat") else s


def de(x, d: int = 1, vz: bool = False) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    if round(x, d) == 0:
        x = 0.0
    return f"{x:{'+' if vz else ''},.{d}f}".replace(",", "'").replace(".", ",").replace("-", "−")


def handelstage(von, bis) -> int:
    """Anzahl US-Handelstage nach «von» bis und mit «bis»."""
    a, b = pd.Timestamp(von).normalize(), pd.Timestamp(bis).normalize()
    if b <= a:
        return 0
    tage = pd.bdate_range(a + pd.Timedelta(days=1), b)
    return int(sum(1 for t in tage if t not in NYSE_FEIERTAGE))


def handelstage_bis(von, n: int) -> pd.Timestamp:
    """Datum, das n US-Handelstage nach «von» liegt."""
    t = pd.Timestamp(von).normalize()
    while n > 0:
        t += pd.Timedelta(days=1)
        if t.weekday() < 5 and t not in NYSE_FEIERTAGE:
            n -= 1
    return t


# ---------------------------------------------------------------------------
# Einstellungen, Kontakte, Zuständigkeit, Watchlist, Thesen
# ---------------------------------------------------------------------------
def schwellen(einst: pd.DataFrame | None) -> dict:
    out = {k: v[1] for k, v in SCHWELLEN.items()}
    if einst is not None and not einst.empty:
        for _, r in einst.sort_values("zeit").iterrows():
            k = str(r["schluessel"])
            if k.startswith("monitor|") and k[8:] in out and _z(r["wert"]) is not None:
                out[k[8:]] = _z(r["wert"])
    return out


def schwellen_verlauf(einst: pd.DataFrame | None) -> pd.DataFrame:
    if einst is None or einst.empty:
        return pd.DataFrame(columns=["Zeit", "Person", "Schwelle", "Wert"])
    d = einst[einst["schluessel"].astype(str).str.startswith("monitor|")].sort_values("zeit", ascending=False)
    return pd.DataFrame({"Zeit": d["zeit"].str[:16].str.replace("T", " "), "Person": d["person"],
                         "Schwelle": d["schluessel"].str[8:].map(lambda k: SCHWELLEN.get(k, (k,))[0]), "Wert": d["wert"]})


def kontakte(df: pd.DataFrame | None) -> dict:
    """{Name: {email, alles}} – neuster Eintrag je Name; leere E-Mail = entfernt."""
    out = {}
    if df is not None and not df.empty:
        for _, r in df.sort_values("zeit").iterrows():
            name, mail = _txt(r.get("name")).strip(), _txt(r.get("email")).strip()
            if not name:
                continue
            if not mail:
                out.pop(name, None)
                continue
            out[name] = dict(email=mail, alles=_txt(r.get("alles")).strip().lower() in ("1", "true", "ja", "1.0"))
    return out


def zustaendigkeit(df: pd.DataFrame | None) -> dict:
    """{Sektor: (Hauptperson, Backup)}"""
    out = {}
    if df is not None and not df.empty:
        for _, r in df.sort_values("zeit").iterrows():
            if r["bereich"] in C.SEKTOREN:
                out[r["bereich"]] = (_txt(r.get("haupt")).strip(), _txt(r.get("backup")).strip())
    return out


def zustaendigkeit_pruefen(zust: dict, kont: dict) -> list[str]:
    out = []
    for s in C.SEKTOREN:
        h, b = zust.get(s, ("", ""))
        if not h or not b:
            out.append(f"{s}: Haupt- und Backup-Person fehlen" if not h and not b else f"{s}: {'Hauptperson' if not h else 'Backup-Person'} fehlt")
        elif h == b:
            out.append(f"{s}: Haupt- und Backup-Person sind gleich")
        for p in (h, b):
            if p and p not in kont:
                out.append(f"{s}: für {p} ist keine E-Mail-Adresse hinterlegt")
    return out


def watchlist(df: pd.DataFrame | None) -> list[dict]:
    if df is None or df.empty:
        return []
    d = df.sort_values("zeit").drop_duplicates("id", keep="last")
    d = d[~d["geloescht"].astype(str).str.strip().isin(["1", "1.0", "True"])]
    out = []
    for _, r in d.iterrows():
        out.append(dict(id=r["id"], ticker=_txt(r["ticker"]).upper().strip(), name=_txt(r["name"]), sektor=_txt(r["sektor"]),
                        op=_txt(r["op"]) or "<=", marke=_z(r["marke"]), these=_txt(r["these"]), person=r["person"],
                        zeit=r["zeit"]))
    return [w for w in out if w["ticker"]]


def thesen(df: pd.DataFrame | None) -> dict:
    """{Ticker: {these, kursziel, stop, ueberpruefung, quartalszahlen}} – neuster Eintrag je Ticker."""
    out = {}
    if df is not None and not df.empty:
        for _, r in df.sort_values("zeit").iterrows():
            t = _txt(r["ticker"]).upper().strip()
            if t:
                out[t] = dict(these=_txt(r["these"]), kursziel=_z(r["kursziel"]), stop=_z(r["stop"]),
                              ueberpruefung=_txt(r["ueberpruefung"])[:10], quartalszahlen=_txt(r["quartalszahlen"])[:10],
                              person=r["person"], zeit=r["zeit"])
    return out


# ---------------------------------------------------------------------------
# Signal-Hilfen
# ---------------------------------------------------------------------------
def _sig(schluessel, ebene, bereich, titel, text, stufe, regel, wert=None, einheit="", richtung="unten", art="zustand",
         ticker=""):
    return dict(schluessel=schluessel, ebene=ebene, bereich=bereich or "Allokation", titel=titel, text=text, stufe=stufe,
                regel=regel, wert=None if wert is None else round(float(wert), 4), einheit=einheit, richtung=richtung, art=art,
                ticker=ticker, hinweis=HINWEIS.get(regel, ""))


def _aktiv(wert, schwelle, richtung: str, key: str, bekannt: set, puffer: float) -> bool:
    """Schwelle erreicht? Für bereits bekannte Signale gilt der Puffer (gegen Hin und Her)."""
    if wert is None or schwelle is None or (isinstance(wert, float) and math.isnan(wert)):
        return False
    eff = schwelle
    if key in bekannt:
        eff = schwelle + puffer if richtung == "unten" else schwelle - puffer
    return wert <= eff + 1e-9 if richtung == "unten" else wert >= eff - 1e-9


def _bis(s: pd.Series | None, datum: pd.Timestamp | None) -> pd.Series | None:
    if s is None:
        return None
    s = s.dropna()
    if datum is not None:
        s = s[s.index <= datum]
    return s if len(s) else None


# ---------------------------------------------------------------------------
# Titel-Signale
# ---------------------------------------------------------------------------
def signale_titel(aktien: list[dict], kurse: dict, s: dict, bekannt: set, these: dict | None = None,
                  earnings: dict | None = None, pf_faktor: float | None = None, datum: pd.Timestamp | None = None) -> list[dict]:
    """aktien: [{ticker, name, sektor, einstandskurs, kaufdatum}] · kurse: {Ticker: Series} inkl. Sektor-ETFs.
    pf_faktor = Portfoliowert / Startkapital (für die Gewichtsdrift). datum = Stichtag (Probelauf), sonst heute."""
    these, earnings = these or {}, earnings or {}
    heute = (datum or pd.Timestamp.today()).normalize()
    pk, out = s["puffer_kurs"], []
    for a in aktien:
        t, sek = a["ticker"], a.get("sektor") or ""
        bereich = sek if sek in C.SEKTOREN else "Allokation"
        name = f"{t}" + (f" ({a['name']})" if a.get("name") else "")
        px = _bis(kurse.get(t), datum)
        if px is None or len(px) < 2:
            continue
        kurs = float(px.iloc[-1])
        ek = a.get("einstandskurs")
        kd = pd.Timestamp(a["kaufdatum"]) if a.get("kaufdatum") is not None and pd.notna(a.get("kaufdatum")) else None
        # 1) Verlust seit Kauf
        if ek:
            r = (kurs / ek - 1) * 100
            k = f"titel:{t}:verlust"
            if _aktiv(r, s["verlust_pruefen"], "unten", k, bekannt, pk):
                stufe = "Handeln" if r <= s["verlust_handeln"] + 1e-9 else "Prüfen"
                out.append(_sig(k, "Titel", bereich, f"{name}: {de(r, 1, True)} % seit Kauf",
                                f"Kurs {de(kurs, 2)} USD gegenüber Einstand {de(ek, 2)} USD "
                                f"(Prüfen ab {de(s['verlust_pruefen'], 0)} %, Handeln ab {de(s['verlust_handeln'], 0)} %).",
                                stufe, "verlust", r, "%", "unten", ticker=t))
            # 9) Gewichtsdrift (Position relativ zum Portfolio gewachsen)
            if pf_faktor:
                drift = ((kurs / ek) / pf_faktor - 1) * 100
                k = f"titel:{t}:drift"
                if _aktiv(drift, s["drift"], "oben", k, bekannt, pk * 2):
                    out.append(_sig(k, "Titel", bereich, f"{name}: Gewicht seit Kauf ≈ {de(drift, 0, True)} %",
                                    f"Der Titel stieg seit dem Kauf {de((kurs / ek - 1) * 100, 1, True)} %, das Portfolio "
                                    f"{de((pf_faktor - 1) * 100, 1, True)} %. Sein Gewicht ist damit relativ um ca. "
                                    f"{de(drift, 0)} % gewachsen (Info ab {de(s['drift'], 0)} %).",
                                    "Info", "drift", drift, "%", "oben", ticker=t))
        # 2) Rückgang vom Höchststand seit Kauf
        seit = px[px.index >= kd] if kd is not None else px
        # nur wenn der Titel seit dem Kauf spürbar über dem Einstand lag – sonst sagt das Signal dasselbe wie «Verlust seit Kauf»
        if len(seit) >= 2 and (not ek or float(seit.max()) > ek * 1.03):
            hoch = float(seit.max())
            dd = (kurs / hoch - 1) * 100
            k = f"titel:{t}:drawdown"
            if _aktiv(dd, s["drawdown"], "unten", k, bekannt, pk):
                out.append(_sig(k, "Titel", bereich, f"{name}: {de(dd, 1, True)} % vom Hoch seit Kauf",
                                f"Höchster Schlusskurs seit Kauf {de(hoch, 2)} USD ({seit.idxmax():%d.%m.%Y}), heute {de(kurs, 2)} USD "
                                f"(Prüfen ab {de(s['drawdown'], 0)} %).", "Prüfen", "drawdown", dd, "%", "unten", ticker=t))
        # 3) Relativ zum Sektor-ETF
        n = int(s["rel_tage"])
        etf = C.SEKTOR_ETF.get(sek)
        ex = _bis(kurse.get(etf), datum) if etf else None
        if ex is not None and len(px) > n and len(ex) > n:
            gem = px.index.intersection(ex.index)
            if len(gem) > n:
                p2, e2 = px.loc[gem], ex.loc[gem]
                rel = ((p2.iloc[-1] / p2.iloc[-1 - n]) - (e2.iloc[-1] / e2.iloc[-1 - n])) * 100
                rt, re_ = (p2.iloc[-1] / p2.iloc[-1 - n] - 1) * 100, (e2.iloc[-1] / e2.iloc[-1 - n] - 1) * 100
                k = f"titel:{t}:rel_schwach"
                if _aktiv(rel, s["rel_schwach"], "unten", k, bekannt, pk):
                    out.append(_sig(k, "Titel", bereich, f"{name}: {de(rel, 1, True)} Pp. gegenüber {etf}",
                                    f"{n} Handelstage: {t} {de(rt, 1, True)} %, Sektor-ETF {etf} {de(re_, 1, True)} % "
                                    f"(Prüfen ab {de(s['rel_schwach'], 0)} Pp.).", "Prüfen", "rel_schwach", rel, "Pp.", "unten",
                                    ticker=t))
                k = f"titel:{t}:rel_stark"
                if _aktiv(rel, s["rel_stark"], "oben", k, bekannt, pk):
                    out.append(_sig(k, "Titel", bereich, f"{name}: {de(rel, 1, True)} Pp. gegenüber {etf}",
                                    f"{n} Handelstage: {t} {de(rt, 1, True)} %, Sektor-ETF {etf} {de(re_, 1, True)} % "
                                    f"(Info ab {de(s['rel_stark'], 0, True)} Pp.).", "Info", "rel_stark", rel, "Pp.", "oben",
                                    ticker=t))
        # 4) Trend: unter der 200-Tage-Linie, 50-Tage- unter 200-Tage-Linie
        if len(px) >= 200:
            sma200, sma50 = float(px.tail(200).mean()), float(px.tail(50).mean())
            abst = (kurs / sma200 - 1) * 100
            k = f"titel:{t}:sma200"
            if _aktiv(abst, 0.0, "unten", k, bekannt, pk / 2):
                out.append(_sig(k, "Titel", bereich, f"{name}: unter der 200-Tage-Linie",
                                f"Kurs {de(kurs, 2)} USD, 200-Tage-Linie {de(sma200, 2)} USD ({de(abst, 1, True)} %).", "Info",
                                "sma200", abst, "%", "unten", ticker=t))
            kreuz = (sma50 / sma200 - 1) * 100
            k = f"titel:{t}:death_cross"
            if _aktiv(kreuz, 0.0, "unten", k, bekannt, pk / 4):
                out.append(_sig(k, "Titel", bereich, f"{name}: 50-Tage- unter 200-Tage-Linie",
                                f"50-Tage-Linie {de(sma50, 2)} USD, 200-Tage-Linie {de(sma200, 2)} USD ({de(kreuz, 1, True)} %).",
                                "Prüfen", "death_cross", kreuz, "%", "unten", ticker=t))
        # 5) Aussergewöhnliche Tagesbewegung (Ereignis, bleibt offen bis zur Reaktion)
        m = int(s["sigma_tage"])
        rr = px.pct_change().dropna()
        if len(rr) > m:
            sd = float(rr.iloc[-1 - m:-1].std())
            r1 = float(rr.iloc[-1])
            if sd > 0 and abs(r1) >= s["sigma"] * sd and abs(r1) * 100 >= s["sigma_min"]:
                tag = rr.index[-1]
                out.append(_sig(f"titel:{t}:sprung:{tag:%Y-%m-%d}", "Titel", bereich,
                                f"{name}: {de(r1 * 100, 1, True)} % am {tag:%d.%m.}",
                                f"Tagesbewegung {de(r1 / sd, 1)} Standardabweichungen (σ der letzten {m} Tage {de(sd * 100, 2)} %; "
                                f"Prüfen ab {de(s['sigma'], 0)} σ und mindestens {de(s['sigma_min'], 0)} %).", "Prüfen", "sprung", r1 * 100, "%", "oben" if r1 > 0 else "unten",
                                art="ereignis", ticker=t))
        # 6) Quartalszahlen in den nächsten Handelstagen (manuell erfasstes Datum hat Vorrang)
        th = these.get(t, {})
        ed = th.get("quartalszahlen") or earnings.get(t)
        if ed:
            try:
                ed = pd.Timestamp(ed).normalize()
                rest = handelstage(heute, ed)
                if heute <= ed and rest <= int(s["earnings_tage"]):
                    out.append(_sig(f"titel:{t}:earnings:{ed:%Y-%m-%d}", "Titel", bereich,
                                    f"{name}: Quartalszahlen am {ed:%d.%m.%Y}",
                                    f"In {rest} Handelstagen" + (" (Datum von Hand erfasst)." if th.get("quartalszahlen") else
                                                                 " (Datum laut Yahoo Finance)."), "Info", "earnings", rest,
                                    "Handelstage", "unten", ticker=t))
            except (ValueError, TypeError):
                pass
        # 7) Kauf-These: Kursziel, Stop, Überprüfungstermin
        if th.get("kursziel"):
            k = f"titel:{t}:kursziel"
            if _aktiv(kurs, th["kursziel"], "oben", k, bekannt, th["kursziel"] * pk / 100):
                out.append(_sig(k, "Titel", bereich, f"{name}: Kursziel {de(th['kursziel'], 2)} USD erreicht",
                                f"Kurs {de(kurs, 2)} USD. These: {th.get('these') or '–'}", "Prüfen", "kursziel", kurs, "USD", "oben",
                                ticker=t))
        if th.get("stop"):
            k = f"titel:{t}:stop"
            if _aktiv(kurs, th["stop"], "unten", k, bekannt, th["stop"] * pk / 100):
                out.append(_sig(k, "Titel", bereich, f"{name}: Stop {de(th['stop'], 2)} USD erreicht",
                                f"Kurs {de(kurs, 2)} USD. These: {th.get('these') or '–'}", "Handeln", "stop", kurs, "USD", "unten",
                                ticker=t))
        if th.get("ueberpruefung"):
            try:
                d = pd.Timestamp(th["ueberpruefung"]).normalize()
                if heute >= d:
                    out.append(_sig(f"titel:{t}:review:{d:%Y-%m-%d}", "Titel", bereich,
                                    f"{name}: These überprüfen (Termin {d:%d.%m.%Y})", f"These: {th.get('these') or '–'}", "Prüfen",
                                    "review", None, "", "unten", ticker=t))
            except (ValueError, TypeError):
                pass
    return out


def signale_analysten(aktien: list[dict], analysten: dict | None, kurse: dict, s: dict, bekannt: set) -> list[dict]:
    """Bestand: Herabstufungen in 30 Tagen und Kurs über dem mittleren Kursziel (Daten: Seite «Unternehmen», Yahoo)."""
    import newsdaten as N

    out = []
    for a in aktien:
        t, sek = a["ticker"], a.get("sektor") or ""
        f = (analysten or {}).get(t)
        if not f:
            continue
        px = _bis(kurse.get(t), None)
        k = N.analysten_kennzahlen(f, kurs=float(px.iloc[-1]) if px is not None and len(px) else None)
        bereich = sek if sek in C.SEKTOREN else "Allokation"
        name = f"{t}" + (f" ({a['name']})" if a.get("name") else "")
        n = len(k["down"])
        key = f"titel:{t}:analyst_down"
        if _aktiv(float(n), s["analyst_down"], "oben", key, bekannt, 1.0):
            haeuser = ", ".join(f"{r['firma']} ({r['von'] or '?'} → {r['zu'] or '?'}, {r['datum'][8:10]}.{r['datum'][5:7]}.)"
                                for r in k["down"][:4])
            out.append(_sig(key, "Titel", bereich, f"{name}: {n} Herabstufungen in 30 Tagen",
                            f"{haeuser}. Konsens jetzt: {N.KONSENS_DE.get(str(k['konsens']), k['konsens'] or '–')} "
                            f"(Prüfen ab {de(s['analyst_down'], 0)} Häusern).", "Prüfen", "analyst_down", n, "Häuser", "oben",
                            ticker=t))
        if k["kurs"] and k["ziel"]:
            ueber = (k["kurs"] / k["ziel"] - 1) * 100
            key = f"titel:{t}:kursziel_analysten"
            if _aktiv(ueber, s["kursziel_ueber"], "oben", key, bekannt, s["puffer_kurs"]):
                out.append(_sig(key, "Titel", bereich, f"{name}: Kurs {de(ueber, 1, True)} % über dem Analysten-Kursziel",
                                f"Kurs {de(k['kurs'], 2)} USD, mittleres Kursziel {de(k['ziel'], 2)} USD ({k['n'] or '?'} Analysten).",
                                "Prüfen", "analyst_ziel", ueber, "%", "oben", ticker=t))
    return out


def signale_watchlist(wl: list[dict], kurse: dict, s: dict, bekannt: set, datum=None) -> list[dict]:
    out = []
    for w in wl:
        px = _bis(kurse.get(w["ticker"]), datum)
        if px is None or w.get("marke") is None:
            continue
        kurs = float(px.iloc[-1])
        richtung = "unten" if w["op"] in ("<=", "<") else "oben"
        k = f"watch:{w['id']}:marke"
        if _aktiv(kurs, w["marke"], richtung, k, bekannt, w["marke"] * s["puffer_kurs"] / 100):
            bereich = w["sektor"] if w["sektor"] in C.SEKTOREN else "Allokation"
            out.append(_sig(k, "Watchlist", bereich, f"Watchlist {w['ticker']}: Marke {de(w['marke'], 2)} USD erreicht",
                            f"Kurs {de(kurs, 2)} USD ({'≤' if richtung == 'unten' else '≥'} {de(w['marke'], 2)}). "
                            f"These: {w.get('these') or '–'}", "Prüfen", "marke", kurs, "USD", richtung, ticker=w["ticker"]))
    return out


# ---------------------------------------------------------------------------
# Einhaltung (IPS, Taktik, Sektoren)
# ---------------------------------------------------------------------------
def signale_einhaltung(lage: dict, s: dict, bekannt: set) -> list[dict]:
    import sektoren as SK

    out = []
    ips, alloc = lage["ips"], lage["alloc"]
    pp = s["puffer_pp"]
    # Taktik selbst ausserhalb der Bänder (gilt auch vor der ersten Transaktion)
    for txt in alloc.get("korridor") or []:
        out.append(_sig(f"taktik:korridor:{txt.split(' ')[0]}", "Einhaltung", "Allokation", f"Taktik: {txt}",
                        "Die gespeicherte Taktik verletzt das IPS.", "Handeln", "korridor"))
    b, total = lage.get("b"), lage.get("total") or 0.0
    if b is None or not lage.get("hat_tx") or total <= 0:
        return out
    cash = lage["cash"]
    ak = float(b.loc[b["klasse"] == "Aktie", "wert"].sum()) if len(b) else 0.0
    tr = float(b.loc[b["klasse"] == "Treasury", "wert"].sum()) if len(b) else 0.0
    ist = {"aktien": ak / total * 100, "anleihen": tr / total * 100, "cash": cash / total * 100}
    namen = {"aktien": "Aktien", "anleihen": "Treasuries", "cash": "Cash"}
    # IPS-Bänder und Frühwarnung
    for k_, name in namen.items():
        lo, hi = ips[f"{k_}_min"], ips[f"{k_}_max"]
        w = ist[k_]
        if _aktiv(w, hi + 1e-6, "oben", f"ips:band:{k_}:oben", bekannt, pp):
            out.append(_sig(f"ips:band:{k_}:oben", "Einhaltung", "Allokation", f"{name} {de(w, 1)} % über dem IPS-Maximum {de(hi, 0)} %",
                            f"IPS-Band {de(lo, 0)}–{de(hi, 0)} %.", "Handeln", "band", w, "%", "oben"))
        elif _aktiv(w, lo - 1e-6, "unten", f"ips:band:{k_}:unten", bekannt, pp):
            out.append(_sig(f"ips:band:{k_}:unten", "Einhaltung", "Allokation", f"{name} {de(w, 1)} % unter dem IPS-Minimum {de(lo, 0)} %",
                            f"IPS-Band {de(lo, 0)}–{de(hi, 0)} %.", "Handeln", "band", w, "%", "unten"))
        elif hi - lo > 2 * s["band_nah"]:
            if _aktiv(w, hi - s["band_nah"], "oben", f"ips:nah:{k_}:oben", bekannt, pp):
                out.append(_sig(f"ips:nah:{k_}:oben", "Einhaltung", "Allokation", f"{name} {de(w, 1)} % – nahe am IPS-Maximum {de(hi, 0)} %",
                                f"Abstand {de(hi - w, 1)} Pp. (Frühwarnung ab {de(s['band_nah'], 1)} Pp.).", "Prüfen", "nah", w, "%", "oben"))
            elif _aktiv(w, lo + s["band_nah"], "unten", f"ips:nah:{k_}:unten", bekannt, pp) and not (k_ == "cash" and lo <= 0):
                out.append(_sig(f"ips:nah:{k_}:unten", "Einhaltung", "Allokation", f"{name} {de(w, 1)} % – nahe am IPS-Minimum {de(lo, 0)} %",
                                f"Abstand {de(w - lo, 1)} Pp. (Frühwarnung ab {de(s['band_nah'], 1)} Pp.).", "Prüfen", "nah", w, "%", "unten"))
    if cash < -1:
        out.append(_sig("ips:cash_negativ", "Einhaltung", "Allokation", f"Cash negativ ({de(cash, 0)} USD)",
                        "Es wurde mehr gekauft als Kapital vorhanden ist.", "Handeln", "cash", cash, "USD", "unten"))
    # Abweichung von der Taktik (nur mit beschlossener Taktik)
    if alloc.get("quelle") == "Team":
        tol_ips = ips.get("rebal_toleranz", 5.0)
        for k_, name in namen.items():
            soll = float(alloc[k_])
            d = ist[k_] - soll
            k = f"taktik:abw:{k_}"
            if _aktiv(abs(d), s["taktik_tol"], "oben", k, bekannt, pp):
                stufe = "Handeln" if abs(d) >= tol_ips - 1e-9 else "Prüfen"
                out.append(_sig(k, "Einhaltung", "Allokation", f"{name}: Ist {de(ist[k_], 1)} % vs. Taktik {de(soll, 1)} %",
                                f"Abweichung {de(d, 1, True)} Pp. (Prüfen ab ±{de(s['taktik_tol'], 1)} Pp., Rebalancing laut IPS ab "
                                f"±{de(tol_ips, 1)} Pp.).", stufe, "rebal" if stufe == "Handeln" else "taktik", abs(d), "Pp.", "oben"))
    # Duration und Laufzeiten
    t = b[b["klasse"] == "Treasury"]
    t = t[t["duration"].notna()] if len(t) else t
    if len(t) and t["wert"].sum():
        dur = float((t["duration"] * t["wert"]).sum() / t["wert"].sum())
        lo, hi = ips["duration_min"], ips["duration_max"]
        if not lo - 1e-9 <= dur <= hi + 1e-9:
            out.append(_sig("ips:duration", "Einhaltung", "Anleihen", f"Duration {de(dur, 1)} J. ausserhalb {de(lo, 0)}–{de(hi, 0)} J.",
                            "IPS-Grenze für die Duration der Anleihen.", "Handeln", "duration", dur, "J.", "oben" if dur > hi else "unten"))
        ziel = alloc.get("duration")
        if ziel is not None and alloc.get("quelle") == "Team":
            k = "taktik:duration"
            if _aktiv(abs(dur - ziel), s["duration_tol"], "oben", k, bekannt, 0.2):
                out.append(_sig(k, "Einhaltung", "Anleihen", f"Duration {de(dur, 1)} J. vs. Taktik {de(ziel, 1)} J.",
                                f"Abweichung {de(dur - ziel, 1, True)} J. (Prüfen ab ±{de(s['duration_tol'], 1)} J.).", "Prüfen",
                                "duration_taktik", abs(dur - ziel), "J.", "oben"))
        lang = b[(b["klasse"] == "Treasury") & (b["restlaufzeit"].fillna(0) > 10)]
        for _, z in lang.iterrows():
            out.append(_sig(f"anleihe:{z['ticker']}:laufzeit", "Einhaltung", "Anleihen", f"{z['ticker']}: Restlaufzeit {de(z['restlaufzeit'], 1)} J.",
                            "Teamregel: keine Laufzeiten über 10 Jahre.", "Prüfen", "laufzeit", z["restlaufzeit"], "J.", "oben"))
    # Aktien: Titel-Maximum, Mindestzahl, Mandat, Ausschlüsse
    a = b[b["klasse"] == "Aktie"]
    if len(a):
        tmax = ips.get("titel_max")
        aus_t = {x.strip().upper() for x in str(ips.get("aus_titel") or "").replace(";", ",").split(",") if x.strip()}
        aus_s = set(ips.get("aus_sektoren") or [])
        sp = lage.get("sp500") or {}
        for _, z in a.iterrows():
            g = z["wert"] / total * 100
            bereich = z["sektor"] if z["sektor"] in C.SEKTOREN else "Allokation"
            if tmax:
                k = f"ips:titel_max:{z['ticker']}"
                if _aktiv(g, tmax + 1e-6, "oben", k, bekannt, pp / 2):
                    out.append(_sig(k, "Einhaltung", bereich, f"{z['ticker']}: {de(g, 2)} % über dem IPS-Maximum je Titel",
                                    f"Maximales Gewicht je Titel {de(tmax, 1)} % des Vermögens.", "Handeln", "titel_max", g, "%", "oben",
                                    ticker=z["ticker"]))
                elif _aktiv(g, tmax - s["titel_nah"], "oben", f"ips:titel_nah:{z['ticker']}", bekannt, pp / 2):
                    out.append(_sig(f"ips:titel_nah:{z['ticker']}", "Einhaltung", bereich,
                                    f"{z['ticker']}: {de(g, 2)} % – nahe am Titel-Maximum {de(tmax, 1)} %",
                                    f"Abstand {de(tmax - g, 2)} Pp.", "Prüfen", "titel_nah", g, "%", "oben", ticker=z["ticker"]))
            if sp and z["ticker"] not in sp:
                out.append(_sig(f"ips:mandat:{z['ticker']}", "Einhaltung", bereich, f"{z['ticker']}: nicht im S&P 500",
                                "Mandat: nur S&P-500-Aktien (Quelle: öffentliche S&P-500-Liste).", "Handeln", "mandat", ticker=z["ticker"]))
            if z["ticker"] in aus_t or z["sektor"] in aus_s:
                out.append(_sig(f"ips:ausschluss:{z['ticker']}", "Einhaltung", bereich, f"{z['ticker']}: laut IPS ausgeschlossen",
                                "Ausgeschlossener " + ("Titel." if z["ticker"] in aus_t else f"Sektor ({z['sektor']})."), "Handeln",
                                "ausschluss", ticker=z["ticker"]))
            if z["sektor"] not in C.SEKTOREN:
                out.append(_sig(f"titel:{z['ticker']}:sektor_unbekannt", "Einhaltung", "Allokation", f"{z['ticker']}: Sektor unbekannt",
                                "Ohne Sektor kann die Sektorallokation nicht geprüft werden.", "Prüfen", "unbekannt", ticker=z["ticker"]))
        nmin = ips.get("aktien_anzahl_min")
        if nmin and len(a) < nmin:
            out.append(_sig("ips:anzahl", "Einhaltung", "Allokation", f"Nur {len(a)} Aktien (IPS: mindestens {de(nmin, 0)})",
                            "Mindestzahl Aktien laut IPS unterschritten.", "Handeln", "anzahl", len(a), "Titel", "unten"))
        # Sektoren: Ist vs. Ziel und IPS-Grenze
        sa = SK.lade_allokation(lage["tabellen"].get("sektor_allokation"))["sektoren"]
        idx = lage["sek_index"]["gewichte"]
        p = SK.pruefe_portfolio(a[["ticker", "name", "sektor", "wert"]], total, sa, idx, float(alloc["aktien"]))
        smax = ips.get("sektor_abw_max")
        for _, z in p["sektoren"].iterrows():
            sek = z["Sektor"]
            aktiv = z["_ist"] - z["_index"]
            k = f"sektor:{sek}:abw"
            ips_verletzt = bool(smax) and _aktiv(abs(aktiv), smax + 1e-6, "oben", k, bekannt, pp)
            ziel_txt = (f"Ist {de(z['_ist'], 2)} %, Ziel {de(z['_ziel'], 2)} %, Index {de(z['_index'], 2)} % des Aktienteils"
                        if z["_ziel"] is not None and not pd.isna(z["_ziel"]) else
                        f"Ist {de(z['_ist'], 2)} %, Index {de(z['_index'], 2)} % des Aktienteils (Richtung «{z['Richtung']}», noch ohne Zahl)")
            ips_txt = f" IPS: höchstens ±{de(smax, 1)} Pp. gegenüber Index (aktuell {de(aktiv, 1, True)} Pp.)." if smax else ""
            hat_ziel = z["_diff"] is not None and not pd.isna(z["_diff"])
            if hat_ziel:
                wert = abs(z["_diff"])
                abw = _aktiv(wert, s["sektor_tol"], "oben", k, bekannt, pp / 2)
                kurz = f"Ist − Ziel {de(z['_diff'], 2, True)} Pp."
                txt = (f"{ziel_txt}; Prüfen ab ±{de(s['sektor_tol'], 1)} Pp. Betrag bis zum Ziel: {z['Fehlt (+) / zu viel (−)']}."
                       + ips_txt)
            else:
                wert, abw = abs(aktiv), z["_stufe"] == L.ROT
                kurz = f"Richtung «{z['Richtung']}» nicht umgesetzt ({de(aktiv, 2, True)} Pp.)"
                txt = ziel_txt + "." + ips_txt
            if ips_verletzt:
                out.append(_sig(k, "Einhaltung", sek, f"{sek}: {de(aktiv, 1, True)} Pp. gegenüber Index – über der IPS-Grenze",
                                txt, "Handeln", "sektor_ips", wert, "Pp.", "oben"))
            elif abw:
                out.append(_sig(k, "Einhaltung", sek, f"{sek}: {kurz}", txt, "Prüfen", "sektor_ziel", wert, "Pp.", "oben"))
    return out


def signale_sektor_trigger(lage: dict, bekannt: set) -> list[dict]:
    import sektoren as SK

    out = []
    sa = SK.lade_allokation(lage["tabellen"].get("sektor_allokation"))["sektoren"]
    titel = lage.get("aktien_liste") or []
    for t in SK.trigger_liste(sa, lage["fred"], lage["kurse"]):
        if not t["ausgeloest"]:
            continue
        betr = [a["ticker"] for a in titel if a.get("sektor") == t["sektor"]]
        out.append(_sig(f"sektor:{t['sektor']}:trigger:{t['regel']}", "Einhaltung", t["sektor"],
                        f"{t['sektor']}: Trigger ausgelöst ({t['regel']})",
                        f"Aktuell {t['wert']}. Vorab festgelegt: {t['trigger'] or '–'}"
                        + (f" Betroffene Titel: {', '.join(betr)}." if betr else ""), "Prüfen", "trigger"))
    return out


def signale_ips_annahmen(lage: dict, s: dict, bekannt: set) -> list[dict]:
    """Ex-ante-Schätzungen (IPS und Taktik) und realisierte Kennzahlen gegen die IPS-Ziele."""
    out = []
    ips = lage["ips"]
    for status, thema, text in lage.get("ips_pruef") or []:
        if status in ("warnung", "fehler"):
            out.append(_sig(f"ips_annahme:{thema}", "IPS-Annahmen", "IPS", f"IPS überprüfen: {thema}", text, "Prüfen", "ips_annahme"))
    # realisiert: Rückgang vom Höchststand gegen den tragbaren Verlust
    v = lage.get("verlauf")
    vm = ips.get("verlust_max")
    if v is not None and len(v) > 1 and vm:
        dd = float(v.iloc[-1] / v.max() - 1) * 100
        anteil = -dd / vm * 100
        k = "ips:verlust"
        if _aktiv(anteil, s["verlust_ips_pruefen"], "oben", k, bekannt, 5):
            stufe = "Handeln" if anteil >= s["verlust_ips_handeln"] else "Prüfen"
            out.append(_sig(k, "Einhaltung", "IPS", f"Portfolio {de(dd, 1, True)} % vom Höchststand",
                            f"Das sind {de(anteil, 0)} % des maximal tragbaren Verlusts von −{de(vm, 0)} % "
                            f"(Prüfen ab {de(s['verlust_ips_pruefen'], 0)} %, Handeln ab {de(s['verlust_ips_handeln'], 0)} %).",
                            stufe, "verlust_ips", anteil, "%", "oben"))
    te = lage.get("te_real")
    if te is not None and ips.get("te_max") and te > ips["te_max"]:
        out.append(_sig("ips:te_real", "Einhaltung", "IPS", f"Realisierter Tracking Error {de(te, 2)} % p.a.",
                        f"IPS höchstens {de(ips['te_max'], 1)} % (gegenüber der Benchmark 50/50, seit dem ersten Trade).", "Prüfen",
                        "te_real", te, "%", "oben"))
    return out


def signale_makro(r: dict) -> list[dict]:
    out = []
    st_ = r.get("status")
    if st_ is not None:
        for _, z in st_.iterrows():
            if z["ampel"] == L.ROT:
                ind = C.IND_BY_ID.get(z["id"], {})
                out.append(_sig(f"makro:ind:{z['id']}", "Makro", "Makro", f"Rote Ampel: {z['name']} {z['anzeige']}",
                                f"Vorab festgelegte Reaktion: {ind.get('aktion', '–')}", "Prüfen", "makro"))
    for k in r.get("kombis") or []:
        if k["aktiv"]:
            out.append(_sig(f"makro:kombi:{k['name']}", "Makro", "Makro", f"Kombi-Signal: {k['name']}",
                            f"Vorab festgelegte Reaktion: {k.get('aktion', '–')}", "Prüfen", "makro"))
    ri = r.get("risiken")
    if ri is not None:
        for rid, x in ri.iterrows():
            if x["signal"]:
                out.append(_sig(f"makro:risiko:{rid}", "Makro", "Makro", f"Warnsignal Risiko: {x['name']}",
                                f"Vorab festgelegte Reaktion: {x.get('reaktion', '–')}", "Prüfen", "makro"))
    return out


# ---------------------------------------------------------------------------
# Lage zusammenstellen (gleich für App und automatischen Lauf)
# ---------------------------------------------------------------------------
def ticker_benoetigt(tabellen: dict) -> tuple[str, ...]:
    """Aktien im Bestand + Watchlist + Sektor-ETFs (für die Kursabfrage)."""
    import portfolio as P

    tx = P.transaktionen(tabellen.get("transaktionen"))
    akt = set(tx.loc[tx["klasse"] == "Aktie", "ticker"].str.upper()) if len(tx) else set()
    akt |= {w["ticker"] for w in watchlist(tabellen.get("watchlist"))}
    try:                                  # KI-Beobachtungspunkte (Update 10): Titel, Sektor-ETFs, SPY
        import ki_portfolio as KP

        akt |= KP.ticker_benoetigt(tabellen)
    except Exception:  # noqa: BLE001
        pass
    return tuple(sorted(akt | set(C.SEKTOR_ETF.values())))


def lage_bauen(tabellen: dict, fred: dict, kurse: dict, hist: dict, r: dict, sp500: dict | None = None,
               earnings: dict | None = None, mit_risiko: bool = True, perf_fn=None, analysten: dict | None = None) -> dict:
    """r: Ergebnis von pipeline.berechne (oder gleichwertig aus dem App-Kontext). kurse: Titel, ETFs und Marktkurse."""
    import ips as IPS
    import portfolio as P

    lage = dict(tabellen=tabellen, fred=fred, kurse=kurse, hist=hist or {}, r=r, ips=r["ips"], alloc=r["alloc"],
                sek_index=r["sek_index"], sp500=sp500 or {}, earnings=earnings or {}, hat_tx=False, fehler=[],
                analysten=analysten or {})
    tx = P.transaktionen(tabellen.get("transaktionen"))
    kapital, _ = P._einstellungen(tabellen.get("einstellungen"))
    heute = pd.Timestamp.today().normalize()
    if len(tx):
        pos, cash_delta, _ = P.positionen(tx)
        b = P.bewerte(pos, kurse, fred, heute)
        zinsen = float(b["zins"].sum()) if len(b) else 0.0
        cash = kapital + cash_delta
        total = (float(b["wert"].sum()) if len(b) else 0.0) + cash + zinsen
        lage.update(hat_tx=True, b=b, cash=cash, total=total, kapital=kapital, tx=tx, pf_faktor=total / kapital if kapital else None)
        akt = b[b["klasse"] == "Aktie"] if len(b) else b
        lage["aktien_liste"] = [dict(ticker=z["ticker"], name=_txt(z["name"]), sektor=_txt(z["sektor"]),
                                     einstandskurs=z["einstand"] / z["menge"] if z["menge"] else None, kaufdatum=z["kaufdatum"])
                                for _, z in akt.iterrows()]
        try:
            lage["verlauf"] = P.verlauf(tx, kurse, fred, kapital)
        except Exception as e:  # noqa: BLE001
            lage["fehler"].append(f"Verlauf: {type(e).__name__}")
        if perf_fn is not None:
            try:
                d = perf_fn(tx, kurse, fred, kapital, lage["alloc"]["neutral"])
                lage["te_real"] = d["kp"].get("te_bench") if d else None
            except Exception as e:  # noqa: BLE001
                lage["fehler"].append(f"Tracking Error: {type(e).__name__}")
    # Annahmen des IPS und Risiko der Taktik
    pruef = []
    try:
        sch = IPS.risiko_schaetzung(r["ips"], hist or {}, r["gew"], r["szdef"], r["bond"])
        pruef += [p for p in IPS.pruefungen(r["ips"], sch)
                  if p[1] in ("Volatilität (Schätzung)", "Verlust im Bear-Szenario", "Erwartete Rendite (Szenarien)")]
    except Exception as e:  # noqa: BLE001
        lage["fehler"].append(f"IPS-Schätzung: {type(e).__name__}")
    if mit_risiko:
        try:
            from types import SimpleNamespace

            import risiko as RI

            ns = SimpleNamespace(alloc=r["alloc"], szdef=r["szdef"], fred=fred, szen=r["szen"], sig=r["sig"], bond=r["bond"])
            rr = RI.berechne(ns)
            if rr:
                kz = RI.kennzahlen_aktienteil(RI.taktik_werte(ns), RI.sektor_betas(hist or {}))
                pruef += [(st_, f"Taktik – {th}", tx_) for st_, th, tx_ in RI.pruefungen(rr, r["ips"], kz)]
        except Exception as e:  # noqa: BLE001
            lage["fehler"].append(f"Risiko der Taktik: {type(e).__name__}")
    lage["ips_pruef"] = pruef
    lage["snapshot"] = snapshot(lage)
    return lage


def snapshot(lage: dict) -> dict:
    a, r = lage["alloc"], lage["r"]
    out = {"stand": jetzt(), "taktik": f"{a['aktien']:.0f} / {a['anleihen']:.0f} / {a['cash']:.0f} (Duration {a['duration']:.1f} J.)"}
    if lage.get("hat_tx") and lage.get("total"):
        b, t = lage["b"], lage["total"]
        out["ist"] = (f"{b.loc[b['klasse'] == 'Aktie', 'wert'].sum() / t * 100:.1f} / "
                      f"{b.loc[b['klasse'] == 'Treasury', 'wert'].sum() / t * 100:.1f} / {lage['cash'] / t * 100:.1f}")
        out["portfoliowert"] = round(t, 0)
    try:
        out["rote_indikatoren"] = int((r["status"]["ampel"] == L.ROT).sum())
        sz = r["szen"]
        out["szenarien_ic"] = {z["name"]: round(z["p"]) for _, z in sz[sz["thema"] == "gesamt"].iterrows()}
    except Exception:  # noqa: BLE001
        pass
    return out


def alle_signale(lage: dict, s: dict, bekannt: set) -> list[dict]:
    out = []
    out += signale_titel(lage.get("aktien_liste") or [], lage["kurse"], s, bekannt, thesen(lage["tabellen"].get("thesen")),
                         lage.get("earnings"), lage.get("pf_faktor"))
    out += signale_analysten(lage.get("aktien_liste") or [], lage.get("analysten"), lage["kurse"], s, bekannt)
    out += signale_watchlist(watchlist(lage["tabellen"].get("watchlist")), lage["kurse"], s, bekannt)
    out += signale_einhaltung(lage, s, bekannt)
    out += signale_sektor_trigger(lage, bekannt)
    out += signale_ips_annahmen(lage, s, bekannt)
    out += signale_makro(lage["r"])
    try:                                  # KI-Beobachtungspunkte (Update 10) – ein Fehler darf das übrige Monitoring nicht stoppen
        import ki_portfolio as KP

        out += KP.signale(lage, bekannt)
    except Exception as e:  # noqa: BLE001
        lage.setdefault("fehler", []).append(f"KI-Beobachtungspunkte: {type(e).__name__}")
    # gleiche Schlüssel zusammenfassen (höchste Stufe gewinnt)
    eind = {}
    for x in out:
        if x["schluessel"] not in eind or RANG[x["stufe"]] > RANG[eind[x["schluessel"]]["stufe"]]:
            eind[x["schluessel"]] = x
    return list(eind.values())


# ---------------------------------------------------------------------------
# Fälle
# ---------------------------------------------------------------------------
def lade_faelle(df: pd.DataFrame | None) -> pd.DataFrame:
    """Neuster Stand je Fall. Doppelte offene Fälle zum selben Signal (gleichzeitig angelegt) → nur der älteste zählt."""
    if df is None or df.empty:
        return pd.DataFrame(columns=FAELLE_SPALTEN)
    d = df.sort_values("zeit").drop_duplicates("id", keep="last").copy()
    d["_offen"] = d["status"].isin(OFFEN)
    doppelt = d[d["_offen"]].sort_values("erstmals").duplicated("schluessel", keep="first")
    d = d.drop(index=doppelt[doppelt].index)
    return d.drop(columns="_offen").sort_values("erstmals", ascending=False).reset_index(drop=True)


def bekannt_aus(f: pd.DataFrame) -> set:
    """Schlüssel, deren Signal zuletzt bestand (für den Puffer)."""
    if f is None or f.empty:
        return set()
    return set(f.loc[f["status"].isin(OFFEN) | (f["signal_aktiv"].astype(str) == "1"), "schluessel"])


def _zeile(fall: dict, **neu) -> dict:
    z = {k: fall.get(k, "") for k in FAELLE_SPALTEN}
    z.update(neu)
    z["zeit"] = neu.get("zeit") or jetzt()
    return {k: ("" if v is None else v) for k, v in z.items()}


def _schwelle_erreicht(sig: dict, ns) -> bool:
    ns = _z(ns)
    if ns is None or sig.get("wert") is None:
        return False
    return sig["wert"] <= ns if sig.get("richtung") == "unten" else sig["wert"] >= ns


def abgleich(signale: list[dict], faelle: pd.DataFrame, snap: dict | None = None, wer: str = "Monitoring") -> tuple[list[dict], list[tuple]]:
    """Gleicht die aktuellen Signale mit den Fällen ab. Gibt (neue Zeilen fürs Blatt faelle, Ereignisse) zurück.
    Ereignisse: ("neu"|"eskaliert"|"erloschen"|"wieder", Fall-Dict)."""
    zeilen, ereig = [], []
    zeit = jetzt()
    snap_txt = json.dumps(snap or {}, ensure_ascii=False, default=str)
    nach = {}
    if faelle is not None and len(faelle):
        for _, f in faelle.sort_values("erstmals").iterrows():
            nach.setdefault(f["schluessel"], []).append(f.to_dict())
    aktuell = {x["schluessel"]: x for x in signale}

    def neuer_fall(sig, vorgaenger="", grund="neu"):
        fall = dict(sig, id=uuid.uuid4().hex[:10], status="Neu", reaktion="", begruendung="", neue_schwelle="",
                    entscheid_id="", bearbeiter="", signal_aktiv="1", erstmals=zeit, vorgaenger=vorgaenger,
                    snapshot=json.dumps({**(snap or {}), "wert": sig.get("wert")}, ensure_ascii=False, default=str))
        z = _zeile(fall, zeit=zeit, person=wer)
        zeilen.append(z)
        ereig.append((grund, z))

    for key, sig in aktuell.items():
        liste = nach.get(key, [])
        offen = [f for f in liste if f["status"] in OFFEN]
        if offen:
            f = offen[0]
            if RANG[sig["stufe"]] > RANG.get(f["stufe"], 0):
                z = _zeile(f, zeit=zeit, person=wer, stufe=sig["stufe"], titel=sig["titel"], text=sig["text"], wert=sig["wert"],
                           signal_aktiv="1", hinweis=sig["hinweis"])
                zeilen.append(z)
                ereig.append(("eskaliert", z))
            elif str(f.get("signal_aktiv")) != "1":
                zeilen.append(_zeile(f, zeit=zeit, person=wer, signal_aktiv="1"))
            continue
        if not liste:
            neuer_fall(sig)
            continue
        f = liste[-1]  # jüngster abgeschlossener Fall
        if str(f.get("signal_aktiv")) != "1":
            neuer_fall(sig, f["id"], "wieder")      # Signal war weg und ist zurück
        elif f["status"] == "Erledigt" and (RANG[sig["stufe"]] > RANG.get(f["stufe"], 0) or _schwelle_erreicht(sig, f.get("neue_schwelle"))):
            neuer_fall(sig, f["id"], "eskaliert")   # schärfer als beim Abschluss oder neue Schwelle erreicht
            zeilen.append(_zeile(f, zeit=zeit, person=wer, signal_aktiv="0"))
    # verschwundene Signale
    for key, liste in nach.items():
        if key in aktuell:
            continue
        for f in liste:
            if f["status"] in OFFEN:
                if f.get("art") == "ereignis":
                    if str(f.get("signal_aktiv")) == "1":
                        zeilen.append(_zeile(f, zeit=zeit, person=wer, signal_aktiv="0"))
                    continue
                z = _zeile(f, zeit=zeit, person=wer, status="Erloschen", signal_aktiv="0",
                           begruendung="Signal von selbst verschwunden (automatisch geschlossen).")
                zeilen.append(z)
                ereig.append(("erloschen", z))
            elif str(f.get("signal_aktiv")) == "1":
                zeilen.append(_zeile(f, zeit=zeit, person=wer, signal_aktiv="0"))
    return zeilen, ereig


def faelle_mischen(faelle: pd.DataFrame, zeilen: list[dict]) -> pd.DataFrame:
    if not zeilen:
        return faelle
    roh = pd.concat([faelle, pd.DataFrame(zeilen)], ignore_index=True) if len(faelle) else pd.DataFrame(zeilen)
    return lade_faelle(roh.astype(str))


def reaktion_zeile(fall: dict, person: str, status: str, reaktion: str = "", begruendung: str = "", neue_schwelle=None,
                   entscheid_id: str = "") -> dict:
    return _zeile(fall, person=person, status=status, reaktion=reaktion, begruendung=begruendung,
                  neue_schwelle="" if neue_schwelle is None else neue_schwelle, entscheid_id=entscheid_id, bearbeiter=person)


# ---------------------------------------------------------------------------
# Wer bekommt was
# ---------------------------------------------------------------------------
def empfaenger(fall: dict, kont: dict, zust: dict) -> list[str]:
    """Sektoren → Haupt- und Backup-Person (+ wer «alles erhalten» gewählt hat); alles andere → alle.
    Fehlt die Zuständigkeit eines Sektors, geht der Fall an alle."""
    alle = sorted(kont)
    if fall.get("bereich") in C.SEKTOREN:
        h, b = zust.get(fall["bereich"], ("", ""))
        wer = [p for p in (h, b) if p in kont]
        if not wer:
            return alle
        return sorted(set(wer) | {p for p, k in kont.items() if k["alles"]})
    return alle


def zustaendig_text(fall: dict, zust: dict) -> str:
    if fall.get("bereich") in C.SEKTOREN:
        h, b = zust.get(fall["bereich"], ("", ""))
        return f"{h or '?'} (Backup {b or '?'})" if h or b else "alle (Zuständigkeit fehlt)"
    return "alle"


# ---------------------------------------------------------------------------
# Meldeprotokoll
# ---------------------------------------------------------------------------
def log_eintraege(df: pd.DataFrame | None) -> list[dict]:
    out = []
    if df is not None and not df.empty:
        for _, r in df.sort_values("zeit").iterrows():
            try:
                fl = json.loads(r["faelle"]) if _txt(r["faelle"]) else []
            except ValueError:
                fl = []
            out.append(dict(zeit=r["zeit"], lauf=r["lauf"], empfaenger=_txt(r["empfaenger"]), faelle=fl, betreff=_txt(r["betreff"])))
    return out


def gemeldet(log: list[dict], laeufe: tuple[str, ...]) -> dict:
    """{(Fall-ID, Person): [(Zeit, Stufe), …]} für die angegebenen Läufe."""
    out = {}
    for e in log:
        if e["lauf"] not in laeufe:
            continue
        for p in [x.strip() for x in e["empfaenger"].split(",") if x.strip()]:
            for x in e["faelle"]:
                fid, stufe = (x[0], x[1]) if isinstance(x, (list, tuple)) else (x, "")
                out.setdefault((fid, p), []).append((e["zeit"], stufe))
    return out


# ---------------------------------------------------------------------------
# Mails
# ---------------------------------------------------------------------------
def _e(x) -> str:
    return html.escape(_txt(x))


def _fall_html(f: dict, zust: dict, mit_status: bool = False, seit: str = "") -> str:
    farbe = STUFE_FARBE.get(f["stufe"], "#556070")
    zusatz = ""
    if mit_status:
        try:
            zusatz = f" · {_e(f['status'])} seit {pd.Timestamp(str(f['erstmals'])[:10]):%d.%m.}"
        except (ValueError, TypeError):
            zusatz = f" · {_e(f['status'])}"
    return (f"<li style='margin-bottom:9px'><span style='color:{farbe};font-weight:700'>{_e(f['stufe'])}</span> "
            f"<b>{_e(f['titel'])}</b><br><span style='color:#334155'>{_e(f['text'])}</span><br>"
            f"<span style='color:#556070;font-size:12px'>Was prüfen: {_e(f.get('hinweis'))} · Zuständig: "
            f"{_e(zustaendig_text(f, zust))}{zusatz}{(' · ' + _e(seit)) if seit else ''}</span></li>")


def _rahmen(titel: str, farbe: str, inhalt: str, app_url: str | None, fuss: str) -> str:
    link = f"<p><a href='{html.escape(app_url)}/monitoring'>→ Fälle im Dashboard bearbeiten (Seite «Monitoring»)</a></p>" if app_url else ""
    return (f"<div style='font-family:Arial,sans-serif;font-size:14px;max-width:700px;color:#101828'>"
            f"<h2 style='color:{farbe};margin-bottom:6px'>{html.escape(titel)}</h2>{inhalt}{link}"
            f"<p style='color:#6B7685;font-size:11px'>{html.escape(fuss)}</p></div>")


def _abschnitt(titel: str, faelle: list[dict], zust: dict, **kw) -> str:
    if not faelle:
        return ""
    return f"<h3 style='margin:16px 0 6px'>{html.escape(titel)} ({len(faelle)})</h3><ul style='padding-left:18px'>" + \
        "".join(_fall_html(f, zust, **kw) for f in faelle) + "</ul>"


def _sortiert(faelle: list[dict]) -> list[dict]:
    return sorted(faelle, key=lambda f: (-RANG.get(f["stufe"], 0), f.get("bereich", ""), f.get("titel", "")))


def mail_sofort(person: str, faelle: list[dict], zust: dict, app_url: str | None) -> tuple[str, str]:
    f = _sortiert(faelle)
    betreff = f"Handeln: {f[0]['titel']}" + (f" (+{len(f) - 1} weitere)" if len(f) > 1 else "")
    inhalt = (f"<p>Hallo {html.escape(person)}, es gibt {'einen neuen Fall' if len(f) == 1 else f'{len(f)} neue Fälle'} der Stufe "
              f"<b>Handeln</b> in deinem Bereich.</p>" + _abschnitt("Handeln", f, zust))
    return betreff, _rahmen("Exception: sofort reagieren", STUFE_FARBE["Handeln"], inhalt, app_url,
                            "Sofort-Meldung aus dem Monitoring (Prüfung Mo–Fr alle 2 Stunden und nach US-Börsenschluss). "
                            "Bitte im Dashboard übernehmen und die Reaktion festhalten (angepasst / bewusst nicht angepasst / Fehlalarm).")


def mail_tag(person: str, teile: dict, zust: dict, app_url: str | None, datum: str) -> tuple[str, str]:
    n_neu = len(teile["neu"])
    inhalt = (f"<p>Hallo {html.escape(person)}, hier der Exception Report für deine Bereiche (Sektoren als Haupt- oder "
              f"Backup-Person, dazu Allokation, Anleihen, IPS und Makro).</p>"
              + _abschnitt("Neu seit dem letzten Report", _sortiert(teile["neu"]), zust)
              + _abschnitt("Offen – Stufe Handeln (täglich, bis erledigt)", _sortiert(teile["handeln"]), zust, mit_status=True)
              + _abschnitt("Offen – Stufe Prüfen (Erinnerung)", _sortiert(teile["erinnerung"]), zust, mit_status=True)
              + _abschnitt("Von selbst erloschen", _sortiert(teile["erloschen"]), zust)
              + _news_abschnitt(teile.get("news") or []))
    if teile.get("weitere"):
        inhalt += f"<p style='color:#556070'>Weitere offene Fälle im Team (andere Sektoren): {teile['weitere']}.</p>"
    betreff = f"Exception Report {datum}: {n_neu} neu, {len(teile['handeln'])} offen Handeln" + (
        f" · {len(teile['news'])} wichtige Sektor-News" if teile.get("news") else "")
    return betreff, _rahmen(f"Exception Report – {datum}", "#14325C", inhalt, app_url,
                            "Täglicher Exception Report aus dem Monitoring (Mo–Fr ca. 7:00). Info-Fälle werden nur beim ersten "
                            "Auftreten und im Wochenreport gemeldet.")


def _news_abschnitt(news: list[dict]) -> str:
    """Info im Tagesreport: neue Sektor-News mit Relevanz 5 (Team-Durchschnitt, sonst KI bzw. Regel) – kein Fall."""
    if not news:
        return ""
    zeilen = "".join(
        f"<li style='margin-bottom:6px'><b>{_e(', '.join(m.get('sektoren') or []))}</b> · "
        + (f"<a href='{_e(m['link'])}'>{_e(m['titel'])}</a>" if m.get("link") else _e(m["titel"]))
        + f" <span style='color:#556070'>({_e(m.get('quelle'))}, {_e(m['zeit'][:10])}, Bewertung {_e(m['eff']['quelle'])}"
        + (f", Wirkung {'positiv' if m['eff']['wirkung'] == '+' else 'negativ' if m['eff']['wirkung'] == '-' else 'neutral'}")
        + ")</span>" + (f"<br><span style='color:#3B4555'>{_e(m['ki_grund'])}</span>" if m.get("ki_grund") else "") + "</li>"
        for m in news[:10])
    return (f"<h3 style='color:{STUFE_FARBE['Info']}'>Info: wichtige Sektor-News (Relevanz 5)</h3><ul style='padding-left:18px'>"
            f"{zeilen}</ul><p style='color:#556070;font-size:12px'>Kein Fall – zur Einordnung. Bewerten und Fazit: Seite «Sektor-News».</p>")


def mail_woche(person: str, offen: list[dict], erledigt: list[dict], neu_n: int, zust: dict, app_url: str | None,
               datum: str) -> tuple[str, str]:
    zeilen = "".join(
        f"<tr><td style='padding:3px 8px'>{_e(f['stufe'])}</td><td style='padding:3px 8px'>{_e(f['titel'])}</td>"
        f"<td style='padding:3px 8px'>{_e(f.get('reaktion') or f['status'])}</td><td style='padding:3px 8px'>"
        f"{_e(f.get('begruendung'))}</td><td style='padding:3px 8px'>{_e(f.get('bearbeiter'))}</td></tr>" for f in erledigt)
    tab = ("<h3>Diese Woche abgeschlossen</h3><table style='border-collapse:collapse;font-size:13px'><tr style='background:#EEF0F3'>"
           "<th align='left' style='padding:3px 8px'>Stufe</th><th align='left' style='padding:3px 8px'>Fall</th>"
           "<th align='left' style='padding:3px 8px'>Reaktion</th><th align='left' style='padding:3px 8px'>Begründung</th>"
           "<th align='left' style='padding:3px 8px'>Von</th></tr>" + zeilen + "</table>") if erledigt else ""
    inhalt = (f"<p>Hallo {html.escape(person)}, die Woche im Monitoring: {neu_n} neue Fälle, {len(erledigt)} abgeschlossen, "
              f"{len(offen)} noch offen.</p>" + _abschnitt("Noch offen", _sortiert(offen), zust, mit_status=True) + tab)
    return (f"Monitoring Wochenreport {datum}: {len(offen)} offen, {len(erledigt)} abgeschlossen",
            _rahmen(f"Monitoring – Wochenreport {datum}", "#14325C", inhalt, app_url,
                    "Wochenübersicht für das ganze Team (Samstag ca. 7:00, nach dem US-Börsenschluss vom Freitag)."))


def mail_start(person: str, faelle: list[dict], zust: dict, app_url: str | None) -> tuple[str, str]:
    inhalt = (f"<p>Hallo {html.escape(person)}, das Monitoring ist ab heute aktiv. Diese Fälle bestehen schon zum Start "
              f"(Ausgangslage) und betreffen deine Bereiche. Ab jetzt kommen nur noch Veränderungen.</p>"
              + _abschnitt("Ausgangslage", _sortiert(faelle), zust))
    return f"Monitoring gestartet: Ausgangslage mit {len(faelle)} Fällen", _rahmen(
        "Monitoring gestartet – Ausgangslage", "#14325C", inhalt, app_url, "Einmalige Startmeldung des Monitorings.")


# ---------------------------------------------------------------------------
# Probelauf: welche Fälle wären in den letzten Monaten entstanden?
# ---------------------------------------------------------------------------
def probelauf(ticker: list[str], kurse: dict, sektoren: dict, s: dict, fred: dict, sek_alloc: dict, zust: dict,
              tage: int = 63) -> dict:
    """Simuliert die Titel-Signale und Sektor-Trigger Tag für Tag über die letzten «tage» Handelstage.
    Annahme: alle Titel am ersten Tag gleich gewichtet gekauft, keine Reaktion des Teams (Fälle schliessen nur von selbst)."""
    import sektoren as SK

    spx = next((kurse[t] for t in ticker if t in kurse), None)
    if spx is None:
        return dict(fehler="Keine Kurse für die gewählten Titel.")
    alle_tage = sorted(set().union(*[set(kurse[t].dropna().index) for t in ticker if t in kurse]))
    alle_tage = [d for d in alle_tage if d <= pd.Timestamp.today().normalize()]
    if len(alle_tage) < tage + 5:
        return dict(fehler="Zu wenig Kursgeschichte für den Probelauf.")
    tage_liste = alle_tage[-tage:]
    start = tage_liste[0]
    aktien = []
    for t in ticker:
        px = kurse.get(t)
        if px is None:
            continue
        p0 = px[px.index <= start]
        if len(p0):
            aktien.append(dict(ticker=t, name="", sektor=sektoren.get(t, ""), einstandskurs=float(p0.iloc[-1]), kaufdatum=start))
    offen, faelle = {}, []
    for d in tage_liste:
        werte = []
        for a in aktien:
            px = kurse[a["ticker"]]
            w0 = px[px.index <= start]
            wd = px[px.index <= d]
            if len(w0) and len(wd):
                werte.append(float(wd.iloc[-1] / w0.iloc[-1]))
        pf = float(np.mean(werte)) * 0.5 + 0.5 if werte else None   # grob: halber Aktienteil, Rest stabil
        sig = signale_titel(aktien, kurse, s, set(offen), pf_faktor=pf, datum=d)
        fred_d = {k: v[v.index <= d] for k, v in fred.items()} if fred else {}
        kurse_d = {k: v[v.index <= d] for k, v in kurse.items()}
        for t in SK.trigger_liste(sek_alloc, fred_d, kurse_d):
            if t["ausgeloest"] and "Datum" not in t["regel"]:
                sig.append(_sig(f"sektor:{t['sektor']}:trigger:{t['regel']}", "Einhaltung", t["sektor"],
                                f"{t['sektor']}: Trigger ausgelöst ({t['regel']})", "", "Prüfen", "trigger"))
        jetzt_ = {x["schluessel"]: x for x in sig}
        for k, x in jetzt_.items():
            if k not in offen:
                f = dict(x, datum=d)
                offen[k] = f
                faelle.append(f)
            elif RANG[x["stufe"]] > RANG[offen[k]["stufe"]]:
                offen[k]["stufe"] = x["stufe"]
        for k in [k for k in offen if k not in jetzt_ and offen[k]["art"] != "ereignis"]:
            offen.pop(k)
    df = pd.DataFrame(faelle)
    if df.empty:
        return dict(df=df, wochen=pd.DataFrame(), regeln=pd.DataFrame(), personen=pd.DataFrame(), start=start, ende=tage_liste[-1],
                    n_titel=len(aktien), n_wochen=max(1.0, tage / 5))
    df["woche"] = df["datum"].dt.to_period("W-SUN").dt.start_time
    alle_wochen = pd.Index(sorted({pd.Timestamp(d).to_period("W-SUN").start_time for d in tage_liste}), name="woche")
    wochen = df.pivot_table(index="woche", columns="stufe", values="schluessel", aggfunc="count", fill_value=0)
    wochen = wochen.reindex(index=alle_wochen, columns=STUFEN, fill_value=0).reset_index()
    regeln = df.groupby(["regel", "stufe"]).size().rename("Fälle").reset_index().sort_values("Fälle", ascending=False)
    pers = {}
    for _, f in df.iterrows():
        if f["bereich"] in C.SEKTOREN and zust.get(f["bereich"]):
            for p in {x for x in zust[f["bereich"]] if x}:
                pers[p] = pers.get(p, 0) + 1
    personen = pd.DataFrame([{"Person": p, "Fälle (Sektoren)": n, "pro Woche": round(n / max(1, tage / 5), 1)}
                             for p, n in sorted(pers.items())])
    return dict(df=df, wochen=wochen, regeln=regeln, personen=personen, start=start, ende=tage_liste[-1], n_titel=len(aktien),
                n_wochen=max(1.0, tage / 5))


def probelauf_html(p: dict, person: str, s: dict) -> tuple[str, str]:
    wo = p["wochen"]
    n = len(p["df"])
    k = p["n_wochen"]
    pruef = int(p["df"]["stufe"].eq("Prüfen").sum()) if n else 0
    zeilen = "".join("<tr>" + f"<td style='padding:3px 8px'>{pd.Timestamp(z['woche']):%d.%m.%Y}</td>" +
                     "".join(f"<td style='padding:3px 8px' align='right'>{int(z.get(st_, 0))}</td>" for st_ in STUFEN) + "</tr>"
                     for _, z in wo.iterrows())
    reg = "".join(f"<li>{html.escape(str(z['regel']))} ({html.escape(z['stufe'])}): {z['Fälle']}</li>" for _, z in p["regeln"].iterrows())
    body = (f"<div style='font-family:Arial,sans-serif;font-size:14px;max-width:700px'><h2 style='color:#14325C'>Probelauf Monitoring</h2>"
            f"<p>Hallo {html.escape(person)}, so viele Fälle wären vom {p['start']:%d.%m.%Y} bis {p['ende']:%d.%m.%Y} entstanden "
            f"({p['n_titel']} Titel, gleich gewichtet; ohne Reaktion des Teams; nur Titel-Signale und Sektor-Trigger):</p>"
            f"<p><b>{n} Fälle</b>, davon {pruef} «Prüfen» – im Schnitt {de(pruef / k, 1)} «Prüfen»-Fälle pro Woche "
            f"(Ziel etwa 5–10).</p><table style='border-collapse:collapse;font-size:13px'><tr style='background:#EEF0F3'>"
            f"<th style='padding:3px 8px'>Woche ab</th>" + "".join(f"<th style='padding:3px 8px'>{x}</th>" for x in STUFEN) +
            f"</tr>{zeilen}</table><h3>Nach Regel</h3><ul>{reg}</ul><p style='color:#6B7685;font-size:11px'>Nur an dich gesendet "
            f"(Probelauf). Schwellen: Verlust {de(s['verlust_pruefen'], 0)} / {de(s['verlust_handeln'], 0)} %, Rückgang vom Hoch "
            f"{de(s['drawdown'], 0)} %, relativ {de(s['rel_schwach'], 0)} / {de(s['rel_stark'], 0, True)} Pp., "
            f"Tagesbewegung {de(s['sigma'], 0)} σ.</p></div>")
    return f"Probelauf Monitoring: {n} Fälle in {de(k, 0)} Wochen", body


# ---------------------------------------------------------------------------
# Quartalszahlen (Yahoo Finance, ohne Gewähr)
# ---------------------------------------------------------------------------
def quartalszahlen_yahoo(ticker: tuple[str, ...], timeout: int = 40) -> dict:
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FT

    def hole(t):
        try:
            import yfinance as yf

            cal = yf.Ticker(t).calendar
            d = cal.get("Earnings Date") if isinstance(cal, dict) else None
            if d:
                d = d[0] if isinstance(d, (list, tuple)) else d
                return t, pd.Timestamp(d).normalize()
        except Exception:  # noqa: BLE001
            pass
        return t, None

    out = {}
    ex = ThreadPoolExecutor(max_workers=4)
    try:
        for t, d in ex.map(hole, ticker, timeout=timeout):
            if d is not None:
                out[t] = d
    except FT:
        pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        ex.shutdown(wait=False)
    return out
