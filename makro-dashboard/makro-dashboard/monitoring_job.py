"""Automatischer Monitoring-Lauf (GitHub Actions, Workflow «Monitoring»). Ersetzt die frühere Signal-Warnung.

  python monitoring_job.py --sofort   Mo–Fr alle 2 Std. und nach US-Börsenschluss: neue Fälle «Handeln» sofort melden
  python monitoring_job.py --tag      Mo–Fr ca. 7:00: Exception Report je Person (neue Fälle, offene «Handeln»,
                                       Erinnerungen «Prüfen», erloschene Fälle)
  python monitoring_job.py --woche    Samstag ca. 7:00: Wochenreport fürs ganze Team

Jeder Lauf gleicht zuerst die Signale mit den Fällen ab (Blatt «faelle») und protokolliert jede Mail (Blatt «monitor_log»).
Der allererste Lauf schickt statt der normalen Meldungen einmalig die «Ausgangslage».

Umgebungsvariablen (GitHub → Settings → Secrets and variables → Actions):
  SCRIPT_URL, SCRIPT_TOKEN (Pflicht) · FRED_API_KEY, APP_URL (optional)
  MAIL_EMPFAENGER: nur Notlösung, solange in der App noch keine Kontakte erfasst sind
Test ohne Senden und ohne Speichern: DRY_RUN=1 python monitoring_job.py --tag  (schreibt monitoring_*.html)
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

import monitoring as M


def lage_laden(store, api_key: str | None) -> dict:
    import analyse
    import markt
    import pipeline
    import portfolio as P
    from morgenmail import lade_fred

    fred_roh = lade_fred(pipeline.fred_serien(markt.FRED_MARKT), api_key)
    yahoo, _, _ = markt.lade_yahoo(markt.YAHOO)
    fred, labels = pipeline.mische_markt(fred_roh, yahoo)
    hist, _, _ = analyse.lade_historie(pipeline.HIST_TICKER, 6)
    tabellen = store.alle()
    r = pipeline.berechne(fred, labels, tabellen, hist, yahoo)
    analyse.setze_benchmark(r["einst"]["bond_benchmark"])
    import benchmark

    benchmark.setze(tabellen)
    analyse.setze_cashzins(fred)
    kurse_t, fehlt, _ = markt.lade_yahoo(M.ticker_benoetigt(tabellen))
    kurse = {**yahoo, **kurse_t}
    tx = P.transaktionen(tabellen.get("transaktionen"))
    aktien = tuple(sorted(set(tx.loc[tx["klasse"] == "Aktie", "ticker"].str.upper()))) if len(tx) else ()
    earnings = M.quartalszahlen_yahoo(aktien) if aktien else {}
    analysten, _ = _daten("unternehmen.json")
    lage = M.lage_bauen(tabellen, fred, kurse, hist, r, sp500=P.sp500_liste(), earnings=earnings, perf_fn=P.performance_daten,
                        analysten=analysten or {})
    if fehlt:
        lage["fehler"].append("Keine Kurse für: " + ", ".join(fehlt))
    return lage


def N_seit_default() -> str:
    return (pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=26)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _daten(name: str):
    """Gesammelte Daten (Sektor-News, Unternehmen) aus dem Branch «daten» – verschlüsselt mit dem SCRIPT_TOKEN."""
    import newsdaten as N

    return N.lade_datei(name, os.environ.get("SCRIPT_TOKEN", ""), repo=os.environ.get("GITHUB_REPOSITORY") or None)


def wichtige_news(tab: dict, seit: str) -> list[dict]:
    """Sektor-News mit Relevanz 5 (Team-Ø ab 4,5, sonst KI/Regel 5), seit dem letzten Tagesreport neu gefunden."""
    import newsdaten as N

    heute = N.monat_von(N.iso(None))
    gestern = N.monat_von(N.iso(N.jetzt_utc() - pd.Timedelta(days=3)))
    bew, eigen, out = N.bewertungen(tab.get("news_bewertung")), N.eigene_meldungen(tab.get("news_eigen")), []
    for mon in sorted({heute, gestern}):
        d, _ = _daten(f"news/{mon}.json")
        for m in N.monatsliste(d, eigen, bew, mon):
            if not m["eff"]["ausgeblendet"] and m["eff"]["rel"] >= 4.5 and str(m.get("gefunden") or m["zeit"]) > seit:
                out.append(m)
    return sorted(out, key=lambda m: m["zeit"], reverse=True)


def _kontakte(tab: dict) -> dict:
    kont = M.kontakte(tab.get("kontakte"))
    if not kont:  # Notlösung: alte Empfängerliste aus den GitHub-Secrets, alle erhalten alles
        for a in [x.strip() for x in os.environ.get("MAIL_EMPFAENGER", "").split(",") if x.strip()]:
            kont[a] = dict(email=a, alles=True)
    return kont


def lauf(store, modus: str, api_key: str | None, app_url: str | None, trocken: bool = False) -> list[str]:
    from storage import jetzt

    lage = lage_laden(store, api_key)
    tab = lage["tabellen"]
    s = M.schwellen(tab.get("einstellungen"))
    faelle = M.lade_faelle(tab.get("faelle"))
    log = M.log_eintraege(tab.get("monitor_log"))
    start = not any(e["lauf"] in ("sofort", "tag", "woche", "start") for e in log)   # noch nie eine echte Meldung
    signale = M.alle_signale(lage, s, M.bekannt_aus(faelle))
    zeilen, ereig = M.abgleich(signale, faelle, lage["snapshot"])
    if zeilen and not trocken:
        store.anhaengen("faelle", zeilen)
    faelle = M.faelle_mischen(faelle, zeilen)
    alle = faelle.to_dict("records")
    offen = [f for f in alle if f["status"] in M.OFFEN]
    kont, zust = _kontakte(tab), M.zustaendigkeit(tab.get("zustaendigkeit"))
    heute = pd.Timestamp.today().normalize()
    datum = f"{heute:%d.%m.%Y}"
    meldungen = []  # (Lauf, [Personen], Betreff, HTML, [(Fall-ID, Stufe)])
    info = [f"{len(signale)} aktive Signale, {len(offen)} offene Fälle, Ereignisse: "
            + ", ".join(f"{k} {sum(1 for e in ereig if e[0] == k)}" for k in ("neu", "wieder", "eskaliert", "erloschen"))]
    info += [f"Hinweis: {x}" for x in lage["fehler"]]

    def meine(p, liste):
        return [f for f in liste if p in M.empfaenger(f, kont, zust)]

    if start:
        for p in kont:
            mf = meine(p, offen)
            if mf:
                b, h = M.mail_start(p, mf, zust, app_url)
                meldungen.append(("start", [p], b, h, [(f["id"], f["stufe"]) for f in mf]))
    elif modus == "sofort":
        schon = M.gemeldet(log, ("sofort", "start", "tag"))   # was im Tagesreport stand, kommt nicht nochmals sofort
        for p in kont:
            neu = [f for f in meine(p, offen) if f["stufe"] == "Handeln"
                   and not any(st == "Handeln" for _, st in schon.get((f["id"], p), []))]
            if neu:
                b, h = M.mail_sofort(p, neu, zust, app_url)
                meldungen.append(("sofort", [p], b, h, [(f["id"], f["stufe"]) for f in neu]))
    elif modus == "tag":
        im_report = M.gemeldet(log, ("tag", "start"))
        letzter = max((e["zeit"] for e in log if e["lauf"] in ("tag", "start")), default="")
        try:
            news = wichtige_news(tab, letzter or N_seit_default())
        except Exception as e:  # noqa: BLE001
            news = []
            info.append(f"Sektor-News nicht geladen: {type(e).__name__}")
        for p in kont:
            mo = meine(p, offen)
            teile = dict(neu=[], handeln=[], erinnerung=[], erloschen=[])
            for f in mo:
                erw = im_report.get((f["id"], p), [])
                erw_rang = max((M.RANG.get(st, 0) for _, st in erw), default=-1)
                if f["stufe"] == "Handeln":
                    teile["handeln"].append(f)
                elif erw_rang < M.RANG[f["stufe"]]:   # noch nie (bzw. noch nie auf dieser Stufe) im Report
                    teile["neu"].append(f)
                elif f["stufe"] == "Prüfen":
                    zuletzt = max(z for z, _ in erw)
                    if M.handelstage(pd.Timestamp(zuletzt[:10]), heute) >= int(s["erinnern_pruefen"]):
                        teile["erinnerung"].append(f)
            teile["erloschen"] = [f for f in meine(p, alle) if f["status"] == "Erloschen" and str(f["zeit"]) > letzter
                                  and (f["id"], p) in M.gemeldet(log, ("tag", "start", "sofort"))]
            teile["weitere"] = len(offen) - len(mo)
            teile["news"] = [m for m in news if any(p in M.empfaenger(dict(bereich=s_), kont, zust) for s_ in (m.get("sektoren") or ["Makro"]))]
            if teile["neu"] or teile["handeln"] or teile["erinnerung"] or teile["news"]:
                b, h = M.mail_tag(p, teile, zust, app_url, datum)
                ids = teile["neu"] + teile["handeln"] + teile["erinnerung"]
                meldungen.append(("tag", [p], b, h, [(f["id"], f["stufe"]) for f in ids]))
    elif modus == "woche":
        seit = (heute - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
        erledigt = [f for f in alle if f["status"] in ("Erledigt", "Erloschen") and str(f["zeit"]) >= seit]
        neu_n = sum(1 for f in alle if str(f["erstmals"]) >= seit)
        if kont and (offen or erledigt or neu_n):
            b, h = M.mail_woche("Team", offen, erledigt, neu_n, zust, app_url, datum)
            meldungen.append(("woche", sorted(kont), b, h, [(f["id"], f["stufe"]) for f in offen]))

    log_zeilen = []
    for nr, (art, personen, betreff, body, ids) in enumerate(meldungen):
        adressen = [kont[p]["email"] for p in personen if p in kont]
        if trocken:
            datei = f"monitoring_{art}_{nr}.html"
            open(datei, "w", encoding="utf-8").write(body)
            info.append(f"[Test] {betreff} → {', '.join(personen)} ({datei})")
            continue
        try:
            store.mail(adressen, betreff, body)
        except Exception as e:  # noqa: BLE001
            info.append(f"Mail an {', '.join(personen)} fehlgeschlagen: {e}")
            continue
        log_zeilen.append(dict(zeit=jetzt(), person="Monitoring", lauf=art, empfaenger=", ".join(personen),
                               faelle=json.dumps(ids), betreff=betreff))
        info.append(f"Gesendet: {betreff} → {', '.join(personen)}")
    if not meldungen:
        info.append("Keine Mail nötig.")
    if not kont:
        info.append("Keine Empfänger: in der App unter Monitoring → «Zuständigkeit & E-Mails» E-Mail-Adressen erfassen.")
    if log_zeilen:
        store.anhaengen("monitor_log", log_zeilen)
    return info


def main():
    from storage import GoogleTabelleStore

    url, token = os.environ.get("SCRIPT_URL", ""), os.environ.get("SCRIPT_TOKEN", "")
    if not url or not token:
        sys.exit("SCRIPT_URL / SCRIPT_TOKEN fehlen.")
    modus = "tag" if "--tag" in sys.argv else "woche" if "--woche" in sys.argv else "sofort"
    store = GoogleTabelleStore(url, token)
    for z in lauf(store, modus, os.environ.get("FRED_API_KEY") or None, (os.environ.get("APP_URL") or "").rstrip("/") or None,
                  bool(os.environ.get("DRY_RUN"))):
        print(z)


if __name__ == "__main__":
    main()
