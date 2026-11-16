"""Morgen-Mail ans Team (läuft Mo–Fr um 7:30 über GitHub Actions, Versand über das Google-Skript).

Benötigte Umgebungsvariablen (GitHub → Settings → Secrets and variables → Actions):
  SCRIPT_URL, SCRIPT_TOKEN, MAIL_EMPFAENGER (Adressen mit Komma getrennt)
  optional: FRED_API_KEY, APP_URL
Test lokal: DRY_RUN=1 python morgenmail.py  (schreibt morgenmail.html statt zu senden)
"""
from __future__ import annotations

import html
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pandas as pd

import kalender
import markt
import pipeline
from makro import config as C
from makro import logic as L
from makro import ui
from makro.fred import lade_serie
from storage import GoogleTabelleStore


def _fett(s: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(s).replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>"))


def lade_fred(serien, api_key):
    start = (pd.Timestamp.today() - pd.DateOffset(years=12)).strftime("%Y-%m-%d")

    def hole(s):
        try:
            return s, lade_serie(s, start=start, api_key=api_key, timeout=20)
        except Exception:  # noqa: BLE001
            return s, None
    with ThreadPoolExecutor(8) as ex:
        return {s: r for s, r in ex.map(hole, serien) if r is not None}


def baue_mail(store, api_key: str | None, app_url: str | None) -> tuple[str, str]:
    fred_roh = lade_fred(pipeline.fred_serien(markt.FRED_MARKT), api_key)
    yahoo, _, _ = markt.lade_yahoo(markt.YAHOO)
    fred, labels = pipeline.mische_markt(fred_roh, yahoo)
    tabellen = store.alle()
    r = pipeline.berechne(fred, labels, tabellen)
    st_, a = r["status"], r["alloc"]
    tab = markt.markt_tabelle(yahoo, fred_roh)
    teile = []

    saetze = markt.tagesupdate_saetze(tab, a, None) if len(tab) else []
    teile.append("<h3 style='color:#2F5DA8'>Märkte</h3><ul>" + "".join(f"<li>{_fett(x)}</li>" for x in saetze) + "</ul>")

    # Neue rote Ampeln (Vorwert war nicht rot)
    neu_rot, rot = [], []
    for _, z in st_.iterrows():
        if z["ampel"] != L.ROT:
            continue
        ind = C.IND_BY_ID[z["id"]]
        rot.append(f"{z['name']}: {z['anzeige']}")
        if z["vorwert"] is not None and not pd.isna(z["vorwert"]) and L.ampel(ind, z["vorwert"]) != L.ROT:
            neu_rot.append(f"<li><b>{html.escape(z['name'])}: {html.escape(z['anzeige'])}</b> → {html.escape(ind['aktion'])}</li>")
    kombi = [f"<li><b>{html.escape(k['name'])}</b>: {html.escape(k['aktion'])}</li>" for k in r["kombis"] if k["aktiv"]]
    warn = [f"<li><b>{html.escape(x['name'])}</b>: {html.escape(x['reaktion'])}</li>" for _, x in r["risiken"].iterrows() if x["signal"]]
    teile.append("<h3 style='color:#D64545'>Signale</h3>" +
                 (f"<p><b>Neu rot:</b></p><ul>{''.join(neu_rot)}</ul>" if neu_rot else "<p>Keine neuen roten Ampeln.</p>") +
                 (f"<p>Kombi-Signale:</p><ul>{''.join(kombi)}</ul>" if kombi else "") +
                 (f"<p>Risiko-Warnsignale:</p><ul>{''.join(warn)}</ul>" if warn else "") +
                 f"<p style='color:#5A6475;font-size:12px'>Alle roten Indikatoren ({len(rot)}): {html.escape('; '.join(rot)) or 'keine'}</p>")

    ow = ", ".join(a["sektoren"][a["sektoren"]["empfehlung"] == "Übergewichten"]["sektor"]) or "–"
    uw = ", ".join(a["sektoren"][a["sektoren"]["empfehlung"] == "Untergewichten"]["sektor"]) or "–"
    teile.append(f"<h3 style='color:#2F5DA8'>Empfehlung (regelbasiert)</h3><p>Aktien {a['aktien']:.0f} % · Treasuries "
                 f"{a['anleihen']:.0f} % · Cash {a['cash']:.0f} % · Duration {ui.de(a['duration'], 1)} J. · TIPS {a['tips']:.0f} %<br>"
                 f"Übergewichten: {html.escape(ow)} · Untergewichten: {html.escape(uw)}</p>")

    tdf, _ = kalender.termine_df(api_key)
    heute = pd.Timestamp.today().normalize()
    t = tdf[(tdf["sort"] >= heute) & (tdf["sort"] <= heute + pd.Timedelta(days=3))]
    if len(t):
        teile.append("<h3 style='color:#2F5DA8'>Termine (nächste 3 Tage)</h3><ul>" +
                     "".join(f"<li>{html.escape(z['Datum'])}: {html.escape(z['Ereignis'])} – {html.escape(z['Betrifft'])}</li>"
                             for _, z in t.iterrows()) + "</ul>")

    news, _, _ = markt.lade_news()
    zeilen = []
    for tid, name, _, _ in markt.NEWS_THEMEN[:8]:
        for n in news.get(tid, [])[:2]:
            zeilen.append(f"<li>{html.escape(name)}: <a href='{html.escape(n['link'])}'>{html.escape(n['titel'])}</a> "
                          f"<span style='color:#7A8699'>({html.escape(n['quelle'])})</span></li>")
    if zeilen:
        teile.append("<h3 style='color:#2F5DA8'>Schlagzeilen</h3><ul>" + "".join(zeilen) + "</ul>")

    try:
        mails = [m for m in store.newsletter() if pd.Timestamp(m["datum"]) >= pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=26)]
        if mails:
            teile.append("<h3 style='color:#2F5DA8'>Neue Newsletter</h3><ul>" +
                         "".join(f"<li>{html.escape(markt._absender_name(m['absender']))}: {html.escape(m['betreff'])}</li>"
                                 for m in mails) + "</ul>")
    except Exception:  # noqa: BLE001
        pass

    link = f"<p><a href='{html.escape(app_url)}'>→ Zum Dashboard</a></p>" if app_url else ""
    datum = datetime.now().strftime("%d.%m.%Y")
    body = (f"<div style='font-family:Arial,sans-serif;font-size:14px;max-width:720px'>"
            f"<h2>Makro-Update USA – {datum}</h2>{''.join(teile)}{link}"
            f"<p style='color:#7A8699;font-size:11px'>Automatisch erstellt aus dem Team-Dashboard (FRED, Yahoo Finance, "
            f"Google News). Regelbasierte Empfehlung – kein Teamentscheid.</p></div>")
    return f"Makro-Update USA {datum}: {len(neu_rot)} neue rote Signale", body


def main():
    url, token = os.environ.get("SCRIPT_URL", ""), os.environ.get("SCRIPT_TOKEN", "")
    empf = [x.strip() for x in os.environ.get("MAIL_EMPFAENGER", "").split(",") if x.strip()]
    if not url or not token:
        sys.exit("SCRIPT_URL / SCRIPT_TOKEN fehlen.")
    store = GoogleTabelleStore(url, token)
    betreff, body = baue_mail(store, os.environ.get("FRED_API_KEY") or None, os.environ.get("APP_URL") or None)
    if os.environ.get("DRY_RUN"):
        open("morgenmail.html", "w", encoding="utf-8").write(body)
        print(betreff, "→ morgenmail.html")
        return
    if not empf:
        sys.exit("MAIL_EMPFAENGER fehlt.")
    store.mail(empf, betreff, body)
    print("Gesendet:", betreff, "an", len(empf), "Empfänger")


if __name__ == "__main__":
    main()
