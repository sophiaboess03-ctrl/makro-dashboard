"""Sektorallokation des Aktienteils: Richtung (Über/Neutral/Unter), Abweichung zum Index, Begründung, Ausstiegs-Trigger.

Logik nach Georgs Dokument «Sektorgewichtung S&P 500 – Vergleich, Begründung, Benchmark» (21.11.2026):
- Die Gewichtung wird als ABWEICHUNG ZUM INDEX festgelegt (Prozentpunkte des Aktienteils), nicht als feste Prozentzahl.
  Ziel = Indexgewicht (aktuell) + Abweichung. Gekauft wird am 01.12. «Index vom 30.11. + Abweichung».
- Indexgewichte: eine zentrale Tabelle mit Stichtagen (Blatt «sektor_index»). Zwischen den Stichtagen schreibt die App
  die Gewichte mit den Kursen der SPDR-Sektor-ETFs fort: neues Gewicht ∝ altes Gewicht × (1 + Rendite des Sektors).
  Diese Gewichte gelten im ganzen Dashboard (Taktik, Risiko, Portfolio, Analyse).
- Prüfung: |Ist − Ziel| ≤ 0,5 Pp. grün, ≤ 1 Pp. gelb, darüber rot; Aktienquote ±2 Pp.; bewusste Abweichung höchstens
  ±50 % des Indexgewichts. Solange nur die Richtung feststeht, wird nur die Richtung geprüft.
"""
from __future__ import annotations

import html
import json

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import seitenpdf as SP
from makro import config as C
from makro import logic as L
from makro import ui
from storage import jetzt, zahl

# ---------------------------------------------------------------------------
# Regeln (Georgs Dokument, Kapitel 1 und 10)
# ---------------------------------------------------------------------------
SCORE_GEWICHTE = {"makro": 0.30, "gewinne": 0.25, "bewertung": 0.25, "momentum": 0.20}
SCORE_NAMEN = {"makro": "Makro", "gewinne": "Gewinne", "bewertung": "Bewertung", "momentum": "Momentum/Risiko"}
SCHWELLE = 0.5           # ab |Score| 0,5 weichen wir vom Index ab
PP_JE_PUNKT = 2.0        # 2 Prozentpunkte je Scorepunkt
DECKEL_PP = 3.0          # höchstens ±3 Pp.
DECKEL_REL = 0.5         # und höchstens ±50 % des Indexgewichts
TOL_GRUEN = 0.5          # |Ist − Ziel| bis 0,5 Pp. = eingehalten (Rebalancing-Schwelle laut Dokument)
TOL_GELB = 1.0
AKTIEN_TOL = 2.0         # Aktienquote ±2 Pp. um die Taktik
RICHTUNG_MIN = C.SEKTOR_SCHWELLE   # ab ±0,25 Pp. gilt ein Sektor als über-/untergewichtet

RICHTUNGEN = ["Übergewicht", "Neutral", "Untergewicht"]
RICHTUNG_KURZ = {"Übergewicht": "Über", "Neutral": "Neutral", "Untergewicht": "Unter"}

# Startwerte: Indexgewichte ChartRow (SPY) vom 16.11.2026 aus Georgs Dokument, Tabelle Kapitel 2
START_INDEX = dict(stichtag="2026-11-16", quelle="ChartRow (SPY-ETF), aus Georgs Dokument vom 21.11.",
                   gewichte={"IT": 35.5, "Finanzen": 12.8, "Gesundheit": 10.6, "Zykl. Konsum": 9.5, "Kommunikation": 8.8,
                             "Industrie": 8.3, "Basiskonsum": 5.2, "Energie": 3.6, "Versorger": 2.1, "Immobilien": 1.8,
                             "Grundstoffe": 1.7})

# Grössen für automatische Ausstiegs-Trigger: (Name, Quelle, Reihe, Einheit)
GROESSEN = {
    "brent": ("Brent (USD/Fass)", "fred", "DCOILBRENTEU", "USD"),
    "wti": ("WTI (USD/Fass)", "fred", "DCOILWTICO", "USD"),
    "us10j": ("Rendite 10-J.-Treasury (%)", "fred", "DGS10", "%"),
    "hy": ("Hochzins-Spread gesamt (%)", "fred", "BAMLH0A0HYM2", "%"),
    "dxy": ("Dollar-Index (DXY)", "kurse", "DX-Y.NYB", ""),
    "vix": ("VIX", "fred", "VIXCLS", ""),
    "datum": ("Datum erreicht", "datum", "", ""),
}
TRIGGER_SERIEN = sorted({g[2] for g in GROESSEN.values() if g[1] == "fred"})

# Georgs Vorschlag (Kapitel 2–6) als Startwerte
GEORG = {
    "Energie": dict(richtung="Übergewicht", abweichung=1.2, makro=1, gewinne=1, bewertung=1, momentum=-1,
                    begruendung="Absicherung gegen einen Ölschock (Iran/Golf): Energie ist der einzige Sektor, der gewinnt, wenn "
                                "Inflation und Zinsen wegen Öl steigen und Aktien und Anleihen gleichzeitig fallen. Günstigster Sektor "
                                "(Forward-KGV 12,2 vs. 13,9 im 25-J.-Schnitt, 0,67× Markt). Gewinne Q3 +151 %, 95 % über den Erwartungen; "
                                "Dividende 2,51 %. Passt zum Bear-Szenario des IC.",
                    gegenargument="Ölpreis fällt (WTI 83 USD, tiefster Stand seit Ende August; IEA erwartet Überangebot). "
                                  "Momentum schwach (−6,8 % im Monat). Im Rückblick-Test kostete das Übergewicht Rendite.",
                    trigger="Ausstieg auf neutral, wenn Brent dauerhaft unter 85 USD fällt oder sich die Lage am Golf klar beruhigt. "
                            "Überprüfung monatlich.", regel_groesse="brent", regel_op="<", regel_wert="85"),
    "Finanzen": dict(richtung="Übergewicht", abweichung=1.0, makro=1, gewinne=0, bewertung=0, momentum=1,
                     begruendung="Verdient an hohen Zinsen (Fed-Erhöhung am 9.12. ≈ 98 %, wahrscheinlich eine zweite). Keine "
                                 "Kreditprobleme sichtbar (IG-Spread 86 bp). Relativ stark im fallenden Markt (+5,2 % im Monat). "
                                 "Forward-KGV 15,1 (0,83× Markt).",
                     gegenargument="Flache Zinskurve (2 J. 4,80 %, 10 J. 5,05 %) drückt die Zinsmarge. Gewinnwachstum Q3 nur +6,4 %. "
                                   "Am 18./19.11. ebenfalls gefallen.",
                     trigger="Ausstieg auf neutral, wenn der Hochzins-Spread über 4,5 % steigt (Kreditstress) oder die Fed eine "
                             "schnelle Zinswende signalisiert.", regel_groesse="hy", regel_op=">", regel_wert="4.5"),
    "Gesundheit": dict(richtung="Neutral", abweichung=0.0, makro=0, gewinne=1, bewertung=-1, momentum=1,
                       begruendung="Grösste positive Gewinnüberraschung in Q3, 2027 +20 % erwartet, defensiv, +6,7 % im Monat. "
                                   "Vorher Übergewicht; wegen des neuen Regulierungsrisikos (CMS) auf neutral gesenkt.",
                       gegenargument="CMS-Vorlage: ≈ 90 Mrd. USD Rückforderungen von Krankenversicherern (Versicherer an einem Tag "
                                     "−10 bis −13 %). Forward-KGV 33,0.",
                       trigger="Neu beurteilen nach Ende der CMS-Kommentarfrist am 19.01.2027.",
                       regel_groesse="datum", regel_op=">=", regel_wert="2027-01-19"),
    "Kommunikation": dict(richtung="Neutral", abweichung=0.0, makro=0, gewinne=0, bewertung=1, momentum=-1,
                          begruendung="Kaum Signale in eine Richtung (Score +0,05): fair bewertet (Forward-KGV 16,2), Gewinne Q3 "
                                      "+18 %; Alphabet und Meta bestimmen 78 % des Sektors.",
                          gegenargument="−10,5 % im letzten Monat, Gewinnstimmung −1,43, Werbung leidet bei schwächerem Konsum.",
                          trigger="", regel_groesse="", regel_op="", regel_wert=""),
    "IT": dict(richtung="Neutral", abweichung=0.0, makro=-1, gewinne=1, bewertung=1, momentum=-1,
               begruendung="Score 0,00: starke Gewinne (Q3 +69 %, 2027 +39 % erwartet) und vernünftige Bewertung (Forward-KGV 17,3, "
                           "14 % unter dem eigenen Schnitt) gegen hohe Realzinsen, starken Dollar, China-Risiko und schwaches Momentum. "
                           "Bei einem 35-%-Sektor ist neutral die vorsichtigste Wahl; der Anleihenteil setzt bereits auf hohe Zinsen.",
               gegenargument="Für mehr: Bull-Szenario (25 %) würde IT anführen. Für weniger: Klumpen Apple/Microsoft/Nvidia ≈ 20 % des "
                             "Index, −7,8 % im Monat, grösster Tagesverlust seit über sechs Jahren.",
               trigger="Am 30.11. KGV und Momentum nachprüfen. Übergewicht ≈ +1 bis +1,5 Pp., wenn KGV < 15,5, Fed Ende der "
                       "Erhöhungen signalisiert und Momentum stabil; Untergewicht ≈ −1 Pp., wenn Gewinnschätzungen sinken, strengere "
                       "China-Regeln kommen oder die 10-J.-Rendite über ≈ 5,3 % steigt.",
               regel_groesse="us10j", regel_op=">", regel_wert="5.3"),
    "Basiskonsum": dict(richtung="Neutral", abweichung=0.0, makro=1, gewinne=-1, bewertung=-2, momentum=1,
                        begruendung="Defensiv und preissetzungsstark, hielt sich im fallenden Markt (+6,6 % im Monat). Guter Schutz "
                                    "im Bear-Fall, aber zu teuer für ein Übergewicht (Score −0,25).",
                        gegenargument="Forward-KGV 23,7 (1,30× Markt), Gewinnstimmung −0,42, Import- und Produzentenpreise drücken "
                                      "die Marge.", trigger="", regel_groesse="", regel_op="", regel_wert=""),
    "Industrie": dict(richtung="Untergewicht", abweichung=-0.3, makro=0, gewinne=0, bewertung=-2, momentum=0,
                      begruendung="Zu teuer: Forward-KGV 22,5, 32 % über dem 25-J.-Schnitt, 1,23× Markt, ohne überdurchschnittliches "
                                  "Wachstum. Industrieproduktion zuletzt −0,4 %, Zollrisiko (Ende Zollpause 10.1.). Bewusst sehr klein.",
                      gegenargument="Rüstung und Investitionen in Rechenzentren/Netze stützen die Aufträge; Sektor breit verteilt "
                                    "(Top 3 nur 16 %).",
                      trigger="Zurück auf neutral, wenn der Auftragseingang der Industrie deutlich steigt oder die Zollpause "
                              "verlängert wird.", regel_groesse="", regel_op="", regel_wert=""),
    "Immobilien": dict(richtung="Untergewicht", abweichung=-0.3, makro=-2, gewinne=0, bewertung=0, momentum=0,
                       begruendung="Zinsempfindlichster Sektor (REITs stark fremdfinanziert). Dividende 3,20 % liegt 1,85 Pp. unter "
                                   "der 10-J.-Treasury. Teuer (Forward-KGV 33,8, 1,85× Markt).",
                       gegenargument="Hielt sich im Monat gut (+5,0 %); profitiert am stärksten, falls die Zinsen schneller fallen "
                                     "(Bull 25 %).",
                       trigger="Zurück auf neutral, wenn die 10-J.-Rendite unter 4,5 % fällt oder die Fed am 9.12. das Ende der "
                               "Erhöhungen signalisiert.", regel_groesse="us10j", regel_op="<", regel_wert="4.5"),
    "Versorger": dict(richtung="Untergewicht", abweichung=-0.4, makro=-1, gewinne=-1, bewertung=0, momentum=-1,
                      begruendung="Als «Anleihenersatz» unattraktiv: Dividende 2,90 % liegt 2,15 Pp. unter der 10-J.-Treasury. "
                                  "Hohe Zinsen verteuern die Finanzierung der stark verschuldeten Versorger. Schwächste Gewinnstimmung "
                                  "aller Sektoren (−1,17).",
                      gegenargument="Strombedarf der KI-Rechenzentren stützt die Aussichten; im Bull-Szenario profitieren Versorger.",
                      trigger="Zurück auf neutral, wenn die 10-J.-Rendite unter 4,5 % fällt.",
                      regel_groesse="us10j", regel_op="<", regel_wert="4.5"),
    "Grundstoffe": dict(richtung="Untergewicht", abweichung=-0.3, makro=-1, gewinne=-1, bewertung=0, momentum=-1,
                        begruendung="Starker Dollar (19-Monats-Hoch) drückt Rohstoffpreise und Exporte; Gold auf 14-Monats-Tief, "
                                    "Kupfer −5 %. Schwache Gewinnqualität (nur 58 % über den Erwartungen). Momentum gekippt.",
                        gegenargument="Rüstungs- und Infrastrukturausgaben stützen die Nachfrage; ein schwächerer Dollar würde "
                                      "den Sektor schnell stützen.",
                        trigger="Zurück auf neutral, wenn der Dollar-Index klar unter 100 fällt oder Gold und Kupfer wieder steigen.",
                        regel_groesse="dxy", regel_op="<", regel_wert="100"),
    "Zykl. Konsum": dict(richtung="Untergewicht", abweichung=-0.9, makro=-2, gewinne=0, bewertung=-2, momentum=-1,
                         begruendung="Konsumenten werden schwächer (Detailhandel real flach, Hausbauer-Stimmung tiefster Stand seit "
                                     "2012, 3,7 % Inflation, teure Kredite). Zollpause mit China endet am 10.1. Teuer: Forward-KGV 25,1 "
                                     "(1,37× Markt). Q3-Gewinne teils durch einmalige Zollrückerstattungen geschönt.",
                         gegenargument="Amazon wächst mit Cloud und Werbung, nicht nur im Detailhandel. Im Bull-Szenario würde der "
                                       "Sektor stark steigen (+5,1 % im Monat).",
                         trigger="Zurück auf neutral, wenn die Zollpause verlängert wird und die Detailhandelszahlen klar besser "
                                 "werden. Überprüfung nach dem 10.01.2027.",
                         regel_groesse="datum", regel_op=">=", regel_wert="2027-01-10"),
}

# Szenarien A–D für die Indexverschiebung bis zum Kauftag (Georgs Kapitel 8.3; «übrige Sektoren» als Annahme ergänzt)
SZEN_INDEX = {
    "A Seitwärts": {s: 0.0 for s in C.SEKTOREN},
    "B Tech-Erholung": {"IT": 8, "Kommunikation": 6, "Zykl. Konsum": 5, "Finanzen": 2, "Gesundheit": 1, "Industrie": 3,
                        "Basiskonsum": 0, "Energie": 0, "Versorger": 1, "Immobilien": 2, "Grundstoffe": 2},
    "C Weitere Tech-Korrektur": {"IT": -10, "Kommunikation": -7, "Zykl. Konsum": -6, "Finanzen": -1, "Gesundheit": 0,
                                 "Industrie": -2, "Basiskonsum": 1, "Energie": 1, "Versorger": 1, "Immobilien": -1,
                                 "Grundstoffe": -3},
    "D Ölschock / Bear": {"Energie": 10, "IT": -6, "Zykl. Konsum": -6, "Kommunikation": -4, "Finanzen": -2, "Gesundheit": -1,
                          "Industrie": -3, "Basiskonsum": 0, "Versorger": -1, "Immobilien": -2, "Grundstoffe": -2},
}


def _z(x):
    """Zahl oder None (auch NaN → None)."""
    v = zahl(x)
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else v


# ---------------------------------------------------------------------------
# Daten: Indexgewichte
# ---------------------------------------------------------------------------
def _norm(g: dict) -> dict:
    tot = sum(v for v in g.values() if v is not None)
    return {s: (g.get(s) or 0.0) / tot * 100 for s in C.SEKTOREN} if tot else dict(C.SP500_GEWICHT_DOZENT)


def stichtage(tab: pd.DataFrame | None) -> list[dict]:
    """Alle erfassten Stichtage (neuster zuerst): [{stichtag, quelle, person, zeit, gewichte}]."""
    out = []
    if tab is not None and not tab.empty:
        d = tab.copy()
        d["w"] = d["gewicht"].map(_z)
        d = d.dropna(subset=["w"]).sort_values("zeit")
        for stt, g in d.groupby("stichtag"):
            letzte = g.drop_duplicates("sektor", keep="last")
            gew = {r["sektor"]: float(r["w"]) for _, r in letzte.iterrows() if r["sektor"] in C.SEKTOREN}
            if len(gew) >= 8:
                z = letzte.iloc[-1]
                out.append(dict(stichtag=str(stt)[:10], quelle=z["quelle"], person=z["person"], zeit=str(z["zeit"]), gewichte=gew))
    out.sort(key=lambda x: (x["stichtag"], x["zeit"]), reverse=True)
    return out


def _kurs_reihe(kurse: dict, hist: dict | None, ticker: str) -> pd.Series | None:
    for quelle in (kurse or {}, hist or {}):
        s = quelle.get(ticker)
        if s is not None and len(s.dropna()):
            return s.dropna()
    return None


def index_info(tab: pd.DataFrame | None, kurse: dict | None = None, hist: dict | None = None) -> dict:
    """Massgebliche Indexgewichte: neuster Stichtag, fortgeschrieben mit den Sektor-ETF-Kursen bis heute."""
    st_liste = stichtage(tab)
    basis = st_liste[0] if st_liste else dict(stichtag=START_INDEX["stichtag"], quelle=START_INDEX["quelle"], person="Startwert",
                                              zeit="", gewichte=START_INDEX["gewichte"])
    w0 = _norm(basis["gewichte"])
    t0 = pd.Timestamp(basis["stichtag"])
    rend, bis, fehlt = {}, None, []
    for s in C.SEKTOREN:
        r = _kurs_reihe(kurse, hist, C.SEKTOR_ETF[s])
        if r is None:
            fehlt.append(s)
            continue
        vor = r[r.index <= t0]
        if not len(vor):
            fehlt.append(s)
            continue
        rend[s] = float(r.iloc[-1] / vor.iloc[-1] - 1)
        bis = r.index[-1] if bis is None else min(bis, r.index[-1])
    if fehlt or bis is None or bis <= t0:
        gew, fort = w0, False
    else:
        roh = {s: w0[s] * (1 + rend[s]) for s in C.SEKTOREN}
        gew, fort = _norm(roh), True
    idx_r = sum(w0[s] / 100 * rend.get(s, 0) for s in C.SEKTOREN) if rend else None
    return dict(gewichte=gew, basis=w0, stichtag=basis["stichtag"], quelle=basis["quelle"], person=basis["person"],
                fortgeschrieben=fort, bis=bis if fort else None, rendite=rend, index_rendite=idx_r, fehlt=fehlt,
                anzahl_stichtage=len(st_liste), start=not st_liste)


# ---------------------------------------------------------------------------
# Daten: Allokation (Richtung, Abweichung, Begründung, Trigger)
# ---------------------------------------------------------------------------
FELDER = ["richtung", "abweichung", "makro", "gewinne", "bewertung", "momentum", "begruendung", "gegenargument", "trigger",
          "regel_groesse", "regel_op", "regel_wert"]


def lade_allokation(tab: pd.DataFrame | None) -> dict:
    """{sektoren: {sektor: dict}, quelle: 'Team'|'Georg', person, zeit}. Ohne Einträge: Georgs Startwerte."""
    if tab is None or tab.empty:
        return dict(sektoren={s: dict(GEORG[s]) for s in C.SEKTOREN}, quelle="Georg", person="", zeit="")
    d = tab.sort_values("zeit").drop_duplicates("sektor", keep="last").set_index("sektor")
    out = {}
    for s in C.SEKTOREN:
        if s not in d.index:
            out[s] = dict(GEORG[s])
            continue
        r = d.loc[s]
        out[s] = dict(richtung=r["richtung"] if r["richtung"] in RICHTUNGEN else "Neutral", abweichung=_z(r["abweichung"]),
                      **{k: _z(r[k]) for k in SCORE_GEWICHTE},
                      **{k: str(r[k]) if str(r[k]) != "nan" else "" for k in ("begruendung", "gegenargument", "trigger",
                                                                               "regel_groesse", "regel_op", "regel_wert")})
    z = d.sort_values("zeit").iloc[-1]
    return dict(sektoren=out, quelle="Team", person=z["person"], zeit=str(z["zeit"]))


def score(e: dict) -> float | None:
    p = {k: _z(e.get(k)) for k in SCORE_GEWICHTE}
    if all(v is None for v in p.values()):
        return None
    return sum(SCORE_GEWICHTE[k] * (p[k] or 0.0) for k in SCORE_GEWICHTE)


def regel_abweichungen(sek: dict, index: dict) -> dict:
    """Georgs Regel: ±0,5-Schwelle, 2 Pp./Punkt, Deckel ±3 bzw. ±50 % Indexgewicht, danach Nullsumme (grössere Seite skalieren)."""
    roh = {}
    for s in C.SEKTOREN:
        sc = score(sek[s])
        if sc is None or abs(sc) < SCHWELLE - 1e-9:
            roh[s] = 0.0
            continue
        deckel = min(DECKEL_PP, DECKEL_REL * index[s])
        roh[s] = float(np.clip(PP_JE_PUNKT * sc, -deckel, deckel))
    ueber, unter = sum(v for v in roh.values() if v > 0), -sum(v for v in roh.values() if v < 0)
    if ueber > 0 and unter > 0:
        if unter > ueber:
            roh = {s: v * ueber / unter if v < 0 else v for s, v in roh.items()}
        else:
            roh = {s: v * unter / ueber if v > 0 else v for s, v in roh.items()}
    else:
        roh = {s: 0.0 for s in roh}
    return {s: round(v, 1) for s, v in roh.items()}


def ziele(sek: dict, index: dict) -> dict:
    """Zielgewicht in % des Aktienteils je Sektor (None, solange bei Über/Unter keine Zahl feststeht)."""
    out = {}
    for s in C.SEKTOREN:
        e, a = sek[s], _z(sek[s].get("abweichung"))
        if e["richtung"] == "Neutral":
            out[s] = index[s] + (a or 0.0)
        else:
            out[s] = max(0.0, index[s] + a) if a is not None else None
    return out


def plan_meldungen(sek: dict, index: dict, ips: dict | None = None) -> list[tuple[str, str]]:
    """Konsistenz der Allokation selbst: Richtung vs. Vorzeichen, Deckel, Nullsumme, IPS."""
    out = []
    zahlen = {s: _z(e.get("abweichung")) for s, e in sek.items()}
    for s, e in sek.items():
        a = zahlen[s]
        if a is None:
            if e["richtung"] != "Neutral":
                out.append((L.GELB, f"{s}: {e['richtung']} ohne Zahl – Prüfung vorerst nur nach Richtung"))
            continue
        if e["richtung"] == "Übergewicht" and a <= 0 or e["richtung"] == "Untergewicht" and a >= 0:
            out.append((L.ROT, f"{s}: Richtung «{e['richtung']}», aber Abweichung {ui.de(a, 1, True)} Pp."))
        if e["richtung"] == "Neutral" and abs(a) > 1e-9:
            out.append((L.GELB, f"{s}: neutral, aber Abweichung {ui.de(a, 1, True)} Pp."))
        if abs(a) > DECKEL_REL * index[s] + 1e-9:
            out.append((L.ROT, f"{s}: {ui.de(a, 1, True)} Pp. ist mehr als ±50 % des Indexgewichts ({ui.de(DECKEL_REL * index[s], 2)} Pp.)"))
        if abs(a) > DECKEL_PP + 1e-9:
            out.append((L.ROT, f"{s}: {ui.de(a, 1, True)} Pp. über dem Deckel von ±{ui.de(DECKEL_PP, 0)} Pp."))
        if ips and abs(a) > ips.get("sektor_abw_max", 99) + 1e-9:
            out.append((L.ROT, f"{s}: {ui.de(a, 1, True)} Pp. über der IPS-Grenze ±{ui.de(ips['sektor_abw_max'], 1)} Pp."))
    bekannt = [a for a in zahlen.values() if a is not None]
    if len(bekannt) == len(zahlen):
        summe = sum(bekannt)
        out.append((L.GRUEN if abs(summe) < 0.05 else L.ROT,
                    f"Summe der Abweichungen {ui.de(summe, 1, True)} Pp. (muss 0 sein) · Summe der Wetten "
                    f"{ui.de(sum(a for a in bekannt if a > 0), 1)} Pp. über / {ui.de(-sum(a for a in bekannt if a < 0), 1)} Pp. unter"))
    return out


# ---------------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------------
def _letzter(fred: dict, kurse: dict, quelle: str, reihe: str):
    s = (fred if quelle == "fred" else kurse or {}).get(reihe)
    if s is None or not len(s.dropna()):
        return None, None
    s = s.dropna()
    v = float(s.iloc[-1])
    if reihe == "DGS10" and v > 20:
        v /= 10
    return v, s.index[-1]


def trigger_status(e: dict, fred: dict, kurse: dict) -> dict:
    g = e.get("regel_groesse") or ""
    if g not in GROESSEN or not e.get("regel_op"):
        return dict(stufe=L.KONTEXT, text="nur Text (von Hand prüfen)", wert="–", regel="–", ausgeloest=False)
    name, quelle, reihe, einheit = GROESSEN[g]
    op, ziel_txt = e["regel_op"], str(e.get("regel_wert") or "").strip()
    if quelle == "datum":
        try:
            ziel = pd.Timestamp(ziel_txt)
        except ValueError:
            return dict(stufe=L.LEER, text="Datum ungültig", wert="–", regel=f"Datum ≥ {ziel_txt}", ausgeloest=False)
        heute = pd.Timestamp.today().normalize()
        aus = heute >= ziel
        rest = (ziel - heute).days
        return dict(stufe=L.ROT if aus else L.GRUEN, ausgeloest=aus, regel=f"Datum ≥ {ziel:%d.%m.%Y}", wert=f"{heute:%d.%m.%Y}",
                    text="Termin erreicht – Gewichtung überprüfen" if aus else f"noch {rest} Tage")
    ziel = _z(ziel_txt)
    v, d = _letzter(fred, kurse, quelle, reihe)
    regel = f"{name} {op} {ui.de(ziel, 2) if ziel is not None else '?'}"
    if v is None or ziel is None:
        return dict(stufe=L.LEER, text="keine Daten" if v is None else "Schwelle fehlt", wert="–", regel=regel, ausgeloest=False)
    aus = v < ziel if op == "<" else v > ziel if op == ">" else v <= ziel if op == "<=" else v >= ziel
    abstand = v - ziel
    nah = not aus and abs(abstand) <= max(abs(ziel) * 0.03, 0.15)
    wert = f"{ui.de(v, 2)}{' ' + einheit if einheit else ''} ({d:%d.%m.})"
    return dict(stufe=L.ROT if aus else L.GELB if nah else L.GRUEN, ausgeloest=aus, regel=regel, wert=wert,
                text="ausgelöst – Gewichtung überprüfen" if aus else ("knapp davor" if nah else "nicht ausgelöst"))


def trigger_liste(sek: dict, fred: dict, kurse: dict) -> list[dict]:
    return [dict(sektor=s, richtung=sek[s]["richtung"], trigger=sek[s].get("trigger", ""), **trigger_status(sek[s], fred, kurse))
            for s in C.SEKTOREN]


# ---------------------------------------------------------------------------
# Prüfung eines Portfolios (echt oder Probe)
# ---------------------------------------------------------------------------
def pruefe_portfolio(aktien: pd.DataFrame, total: float, sek: dict, index: dict, aktien_soll: float) -> dict:
    """aktien: Spalten ticker, name, sektor, wert. Gibt Sektortabelle, Titeltabelle, Meldungen und Kennzahlen zurück."""
    aktien = aktien.copy()
    if not len(aktien):
        aktien = pd.DataFrame(columns=["ticker", "name", "sektor", "wert"])
    aktien["sektor"] = aktien["sektor"].fillna("").map(lambda x: x if x in C.SEKTOREN else "Unbekannt")
    ak_w = float(aktien["wert"].sum())
    ziel = ziele(sek, index)
    zeilen, meld = [], []
    for s in C.SEKTOREN:
        e = sek[s]
        teil = aktien[aktien["sektor"] == s].sort_values("wert", ascending=False)
        ist = float(teil["wert"].sum()) / ak_w * 100 if ak_w else 0.0
        aktiv = ist - index[s]
        z = ziel[s]
        if z is not None:
            diff = ist - z
            stufe = L.GRUEN if abs(diff) <= TOL_GRUEN + 1e-9 else L.GELB if abs(diff) <= TOL_GELB + 1e-9 else L.ROT
            pruef = f"Ist − Ziel {ui.de(diff, 2, True)} Pp."
        else:
            diff = None
            ok = aktiv >= RICHTUNG_MIN if e["richtung"] == "Übergewicht" else aktiv <= -RICHTUNG_MIN
            schwach = aktiv > 0 if e["richtung"] == "Übergewicht" else aktiv < 0
            stufe = L.GRUEN if ok else L.GELB if schwach else L.ROT
            pruef = f"nur Richtung: aktiv {ui.de(aktiv, 2, True)} Pp."
        usd_diff = (z - ist) / 100 * ak_w if z is not None and ak_w else None
        titel = " · ".join(f"{t} {ui.de(w / ak_w * 100, 1)} %" for t, w in zip(teil["ticker"], teil["wert"])) if ak_w else ""
        zeilen.append({"Sektor": s, "Richtung": RICHTUNG_KURZ[e["richtung"]],
                       "Abw. geplant": "–" if e.get("abweichung") is None else f"{ui.de(e['abweichung'], 1, True)} Pp.",
                       "Index": f"{ui.de(index[s], 2)} %", "Ziel": "–" if z is None else f"{ui.de(z, 2)} %",
                       "Ist": f"{ui.de(ist, 2)} %", "Aktiv vs. Index": f"{ui.de(aktiv, 2, True)} Pp.",
                       "Ist − Ziel": "–" if diff is None else f"{ui.de(diff, 2, True)} Pp.",
                       "Fehlt (+) / zu viel (−)": "–" if usd_diff is None else f"{ui.de(usd_diff, 0, True)} USD",
                       "Prüfung": f"● {stufe}", "Anzahl Titel": len(teil), "Titel (Anteil am Aktienteil)": titel or "–",
                       "_stufe": stufe, "_ist": ist, "_ziel": z, "_index": index[s], "_diff": diff})
        if stufe != L.GRUEN:
            meld.append((stufe, f"{s}: {pruef} ({RICHTUNG_KURZ[e['richtung']]}"
                                + (f", Ziel {ui.de(z, 2)} %, Ist {ui.de(ist, 2)} %)" if z is not None else ")")))
    unbek = aktien[aktien["sektor"] == "Unbekannt"]
    if len(unbek):
        meld.append((L.ROT, "Sektor unbekannt: " + ", ".join(unbek["ticker"].astype(str)) + " – Sektor von Hand zuordnen"))
    quote = ak_w / total * 100 if total else 0.0
    q_stufe = L.GRUEN if abs(quote - aktien_soll) <= AKTIEN_TOL + 1e-9 else L.ROT
    if q_stufe != L.GRUEN:
        meld.insert(0, (q_stufe, f"Aktienquote {ui.de(quote, 1)} % – Soll {ui.de(aktien_soll, 1)} % ±{ui.de(AKTIEN_TOL, 0)} Pp."))
    titel_df = pd.DataFrame({
        "Sektor": aktien["sektor"], "Ticker": aktien["ticker"], "Name": aktien.get("name", pd.Series([""] * len(aktien))).fillna(""),
        "Marktwert USD": aktien["wert"].map(lambda x: ui.de(x, 0)),
        "% Aktienteil": (aktien["wert"] / ak_w * 100).map(lambda x: ui.de(x, 2)) if ak_w else "–",
        "% Gesamt": (aktien["wert"] / total * 100).map(lambda x: ui.de(x, 2)) if total else "–",
        "_w": aktien["wert"]})
    reihen = {s: i for i, s in enumerate(C.SEKTOREN + ["Unbekannt"])}
    titel_df = titel_df.assign(_r=titel_df["Sektor"].map(reihen)).sort_values(["_r", "_w"], ascending=[True, False])
    df = pd.DataFrame(zeilen)
    n = {k: int((df["_stufe"] == k).sum()) for k in (L.GRUEN, L.GELB, L.ROT)}
    return dict(sektoren=df, titel=titel_df.drop(columns=["_r", "_w"]), meldungen=meld, quote=quote, quote_stufe=q_stufe,
                n=n, aktien_wert=ak_w, summe_abw=float(sum(abs(x) for x in df["_diff"] if x is not None)))


def zeige_pruefung(ctx, aktien: pd.DataFrame, total: float, titel: str = "Sektorallokation einhalten", key: str = "pf",
                   sammler=None) -> dict:
    """Sektorübersicht mit Titeln und Ampel – für Portfolio und Probe-Portfolio."""
    sa = lade_allokation(ctx.tabellen.get("sektor_allokation"))
    idx = ctx.sek_index["gewichte"]
    soll = float(ctx.alloc["aktien"])
    p = pruefe_portfolio(aktien, total, sa["sektoren"], idx, soll)
    st.markdown(f"#### {titel}")
    ui.kopf_kpis([
        ("Aktienquote", f"{ui.de(p['quote'], 1)} %", f"Soll {ui.de(soll, 1)} % ±{ui.de(AKTIEN_TOL, 0)} Pp. · "
                                                       f"{ui.badge(p['quote_stufe'], 'ok' if p['quote_stufe'] == L.GRUEN else 'ausserhalb')}"),
        ("Sektoren eingehalten", f"{p['n'][L.GRUEN]} / 11", f"{p['n'][L.GELB]} knapp · {p['n'][L.ROT]} ausserhalb"),
        ("Summe |Ist − Ziel|", f"{ui.de(p['summe_abw'], 1)} Pp.", f"Toleranz je Sektor ±{ui.de(TOL_GRUEN, 1)} Pp. (gelb bis ±{ui.de(TOL_GELB, 0)})"),
        ("Benchmark", f"Index {pd.Timestamp(ctx.sek_index['stichtag']):%d.%m.}",
         "fortgeschrieben bis " + f"{ctx.sek_index['bis']:%d.%m.}" if ctx.sek_index["fortgeschrieben"] else "Stand Stichtag"),
    ])
    if sa["quelle"] == "Georg":
        st.caption("Sektorallokation: noch nicht gespeichert – geprüft wird gegen Georgs Startwerte (Seite «Sektorallokation»).")
    spalten = ["Sektor", "Richtung", "Abw. geplant", "Index", "Ziel", "Ist", "Ist − Ziel", "Fehlt (+) / zu viel (−)", "Prüfung",
               "Titel (Anteil am Aktienteil)"]
    st.dataframe(ui.ampel_stil(p["sektoren"][spalten], ["Prüfung"]), hide_index=True, width="stretch", height=40 + 35 * 11,
                 column_config={"Titel (Anteil am Aktienteil)": st.column_config.TextColumn(width="large")})
    rot = [t for s_, t in p["meldungen"] if s_ == L.ROT]
    if rot:
        st.error("**Nicht eingehalten:**\n\n" + "\n".join(f"- {t}" for t in rot), icon=":material/error:")
    gelb = [t for s_, t in p["meldungen"] if s_ == L.GELB]
    if gelb:
        st.warning("**Knapp ausserhalb der Toleranz (0,5–1 Pp.):**\n\n" + "\n".join(f"- {t}" for t in gelb),
                   icon=":material/warning:")
    if not p["meldungen"] and p["aktien_wert"]:
        st.success("Alle Sektoren und die Aktienquote liegen innerhalb der Toleranz.", icon=":material/check_circle:")
    with st.expander(f"Welche Aktie in welchem Sektor ({len(p['titel'])} Titel)", icon=":material/category:",
                     expanded=bool(len(p["titel"])) and len(p["titel"]) <= 40):
        if len(p["titel"]):
            st.dataframe(p["titel"], hide_index=True, width="stretch", height=min(38 + 35 * len(p["titel"]), 640))
        else:
            st.caption("Noch keine Aktien.")
    st.caption("Ziel = Indexgewicht (letzter Stichtag, mit Sektor-ETF-Kursen fortgeschrieben) + geplante Abweichung, in % des "
               "Aktienteils. «Fehlt / zu viel» = Betrag bis zum Ziel. Bei Über-/Untergewicht ohne Zahl wird nur die Richtung "
               "geprüft (mind. ±0,25 Pp.). Neutrale Sektoren müssen auf ±0,5 Pp. am Index liegen.")
    if sammler is not None:
        sammler.h(titel)
        sammler.text(f"Aktienquote {ui.de(p['quote'], 1)} % (Soll {ui.de(soll, 1)} %) · eingehalten {p['n'][L.GRUEN]}/11 · "
                     f"Summe |Ist − Ziel| {ui.de(p['summe_abw'], 1)} Pp.")
        sammler.tabelle(p["sektoren"][["Sektor", "Richtung", "Index", "Ziel", "Ist", "Ist − Ziel", "Prüfung"]].assign(
            Prüfung=lambda d: d["Prüfung"].str.replace("● ", "")), [34, 20, 22, 22, 22, 28, 32])
    return p


# ---------------------------------------------------------------------------
# Szenarien bis zum Kauftag
# ---------------------------------------------------------------------------
def szenario_gewichte(basis: dict, renditen: dict) -> tuple[dict, float]:
    roh = {s: basis[s] * (1 + renditen.get(s, 0) / 100) for s in C.SEKTOREN}
    idx = sum(basis[s] / 100 * renditen.get(s, 0) for s in C.SEKTOREN)
    return _norm(roh), idx


# ---------------------------------------------------------------------------
# Seite
# ---------------------------------------------------------------------------
def _editor_df(sek: dict) -> pd.DataFrame:
    df = pd.DataFrame([{"Sektor": s, "Richtung": sek[s]["richtung"], "Abweichung (Pp.)": _z(sek[s].get("abweichung")),
                        **{SCORE_NAMEN[k]: _z(sek[s].get(k)) for k in SCORE_GEWICHTE}} for s in C.SEKTOREN])
    return df.astype({"Abweichung (Pp.)": "float64", **{SCORE_NAMEN[k]: "float64" for k in SCORE_GEWICHTE}})


def _lade_in_editor(sek: dict):
    ss = st.session_state
    ss["sa_df"] = _editor_df(sek)
    for s in C.SEKTOREN:
        for k in ("begruendung", "gegenargument", "trigger", "regel_groesse", "regel_op", "regel_wert"):
            ss[f"sa_{k}_{s}"] = sek[s].get(k) or ""
    ss["sa_ver"] = ss.get("sa_ver", 0) + 1


def _regel_einsetzen(index: dict):
    ss = st.session_state
    df = ss["sa_df"].copy()
    ed = ss.get(f"sa_ed_{ss.get('sa_ver', 0)}")
    if isinstance(ed, dict):  # noch nicht übernommene Änderungen im Editor berücksichtigen
        for i, ch in (ed.get("edited_rows") or {}).items():
            for col, v in ch.items():
                df.loc[int(i), col] = v
    sek = {r["Sektor"]: {k: _z(r[SCORE_NAMEN[k]]) for k in SCORE_GEWICHTE} for _, r in df.iterrows()}
    reg = regel_abweichungen(sek, index)
    df["Abweichung (Pp.)"] = df["Sektor"].map(reg)
    df["Richtung"] = df["Sektor"].map(lambda s: "Übergewicht" if reg[s] > 0 else "Untergewicht" if reg[s] < 0 else "Neutral")
    ss["sa_df"] = df
    ss["sa_ver"] = ss.get("sa_ver", 0) + 1


def seite_sektoren(ctx, kurse: dict):
    R = SP.Sammler("Sektorallokation", "Über- und Untergewichte im Aktienteil gegenüber dem S&P 500, mit Begründung", kurz="Sektoren")
    ui.seitenkopf("Sektorallokation (Aktienteil)",
                  "Richtung und Abweichung je Sektor gegenüber dem S&P 500, mit Begründung und Ausstiegs-Trigger. Gekauft wird "
                  "«Indexgewicht am Stichtag + Abweichung»; Portfolio und Analyse prüfen dagegen.")
    ph = st.empty()
    ss = st.session_state
    info = ctx.sek_index
    idx = info["gewichte"]
    sa = lade_allokation(ctx.tabellen.get("sektor_allokation"))
    sek = sa["sektoren"]
    trig = trigger_liste(sek, ctx.fred, kurse)
    n_aus = sum(t["ausgeloest"] for t in trig)
    ziel = ziele(sek, idx)
    aktien_soll = float(ctx.alloc["aktien"])
    bekannt = [e.get("abweichung") for e in sek.values() if e.get("abweichung") is not None]

    ui.kopf_kpis([
        ("Benchmark (Indexgewichte)", f"Stichtag {pd.Timestamp(info['stichtag']):%d.%m.%Y}",
         html.escape(info["quelle"][:60]) + (f" · fortgeschrieben bis {info['bis']:%d.%m.}" if info["fortgeschrieben"] else "")),
        ("Aktienquote (Taktik)", f"{ui.de(aktien_soll, 1)} %", f"Toleranz ±{ui.de(AKTIEN_TOL, 0)} Pp. · {html.escape(ctx.alloc['quelle_txt'][:50])}"),
        ("Aktive Wetten", f"{ui.de(sum(abs(a) for a in bekannt), 1)} Pp.",
         f"Summe {ui.de(sum(bekannt), 1, True)} Pp. (muss 0 sein) · {len(bekannt)}/11 mit Zahl"),
        ("Ausstiegs-Trigger", f"{n_aus} ausgelöst", f"{sum(1 for t in trig if t['stufe'] == L.GELB)} knapp davor · "
                                                  f"{sum(1 for t in trig if t['stufe'] != L.KONTEXT)} automatisch überwacht"),
    ])
    if sa["quelle"] == "Georg":
        st.info("Noch nichts gespeichert. Angezeigt sind die Startwerte aus Georgs Dokument vom 21.11.2026 – unten direkt "
                "prüfen, anpassen und speichern.", icon=":material/info:")
    else:
        st.caption(f"Zuletzt gespeichert von {sa['person']} am {sa['zeit'][:16].replace('T', ' ')}.")
    for t in trig:
        if t["ausgeloest"]:
            st.warning(f"**Trigger {t['sektor']}** ({t['regel']}, aktuell {t['wert']}): {t['trigger']}", icon=":material/notifications_active:")
    if info["start"]:
        st.caption("Indexgewichte: noch kein Stichtag gespeichert – Startwerte ChartRow 16.11. aus Georgs Dokument. "
                   "Am 30.11. die echten Gewichte im Tab «Indexgewichte» eintragen.")
    elif info["fehlt"]:
        st.caption("Fortschreibung nicht möglich (Kurse fehlen für " + ", ".join(info["fehlt"]) + ") – es gilt der Stand am Stichtag.")

    t1, t3, t4, t5, t6 = st.tabs(["Allokation & Begründung", "Indexgewichte", "Trigger", "Szenarien bis Kauftag", "Regeln"])

    # --- Allokation & Begründung (direkt bearbeiten) -------------------------
    with t1:
        if "sa_df" not in ss or ss.get("sa_stand") != sa["zeit"]:
            _lade_in_editor(sek)
            ss["sa_stand"] = sa["zeit"]
        c = st.columns(3)
        c[0].button("Scorecard-Regel als Abweichung einsetzen", on_click=_regel_einsetzen, args=(idx,), width="stretch",
                    help="Rechnet aus den Punkten: ab Score ±0,5 je Punkt 2 Pp., Deckel ±3 Pp. bzw. ±50 % des Indexgewichts, "
                         "danach Nullsumme (grössere Seite wird verkleinert).")
        c[1].button("Georgs Startwerte laden", on_click=_lade_in_editor, args=({s: dict(GEORG[s]) for s in C.SEKTOREN},),
                    width="stretch")
        if sa["quelle"] == "Team":
            c[2].button("Gespeicherte Werte laden (Änderungen verwerfen)", on_click=_lade_in_editor, args=(sek,), width="stretch")
        st.caption("Direkt in der Tabelle bearbeiten. Richtung ist Pflicht; die Abweichung in Pp. des Aktienteils kann leer bleiben, "
                   "bis die Zahlen kurz vor der Abgabe feststehen – dann prüft die App nur die Richtung. Punkte −2 bis +2 sind "
                   "optional. Ziel, Prüfung und Grafik rechnen sofort mit; gespeichert wird erst mit «Speichern».")
        ed = st.data_editor(
            ss["sa_df"], hide_index=True, width="stretch", key=f"sa_ed_{ss.get('sa_ver', 0)}", disabled=["Sektor"],
            height=40 + 35 * 11,
            column_config={"Richtung": st.column_config.SelectboxColumn(options=RICHTUNGEN, required=True),
                           "Abweichung (Pp.)": st.column_config.NumberColumn(min_value=-10.0, max_value=10.0, step=0.1, format="%.1f"),
                           **{SCORE_NAMEN[k]: st.column_config.NumberColumn(min_value=-2.0, max_value=2.0, step=1.0, format="%.0f")
                              for k in SCORE_GEWICHTE}})

        # Entwurf = aktuelle Eingaben (Tabelle + Texte)
        entwurf = {}
        for _, r in ed.iterrows():
            s = r["Sektor"]
            entwurf[s] = dict(richtung=r["Richtung"] or "Neutral", abweichung=_z(r["Abweichung (Pp.)"]),
                              **{k: _z(r[SCORE_NAMEN[k]]) for k in SCORE_GEWICHTE},
                              **{k: (ss.get(f"sa_{k}_{s}") or "").strip() for k in ("begruendung", "gegenargument", "trigger",
                                                                                     "regel_groesse", "regel_op", "regel_wert")})
        z_e, reg_e = ziele(entwurf, idx), regel_abweichungen(entwurf, idx)
        trig_e = {t["sektor"]: t for t in trigger_liste(entwurf, ctx.fred, kurse)}
        zeilen = []
        for s in sorted(C.SEKTOREN, key=lambda x: -(entwurf[x].get("abweichung") or 0)):
            e, a, sc = entwurf[s], entwurf[s].get("abweichung"), score(entwurf[s])
            tr = trig_e[s]
            zeilen.append({"Sektor": s, "Richtung": e["richtung"], "Abweichung": "–" if a is None else f"{ui.de(a, 1, True)} Pp.",
                           "Index heute": f"{ui.de(idx[s], 2)} %", "Ziel % Aktien": "–" if z_e[s] is None else f"{ui.de(z_e[s], 2)} %",
                           "Ziel % Gesamt": "–" if z_e[s] is None else f"{ui.de(z_e[s] * aktien_soll / 100, 2)} %",
                           "Relativ": "–" if a is None or not idx[s] else f"{ui.de(a / idx[s] * 100, 0, True)} %",
                           "Score": "–" if sc is None else ui.de(sc, 2, True), "Regel (Scorecard)": f"{ui.de(reg_e[s], 1, True)} Pp.",
                           "Trigger": f"● {tr['stufe']}" if tr["stufe"] != L.KONTEXT else "Text"})
        df = pd.DataFrame(zeilen)
        st.markdown("**Ergebnis: Zielgewichte** (Ziel % Gesamt = Ziel % Aktien × Aktienquote der Taktik; geht automatisch in die Taktik)")
        st.dataframe(ui.ampel_stil(df.drop(columns=["Richtung", "Abweichung"]), ["Trigger"]), hide_index=True, width="stretch",
                     height=40 + 35 * 11)
        c1, c2 = st.columns([1.3, 1])
        with c1:
            werte = sorted([(s, entwurf[s]["abweichung"] if entwurf[s]["abweichung"] is not None else
                             (0.25 if entwurf[s]["richtung"] == "Übergewicht" else -0.25 if entwurf[s]["richtung"] == "Untergewicht" else 0))
                            for s in C.SEKTOREN], key=lambda x: x[1])
            fig = go.Figure(go.Bar(x=[a for _, a in werte], y=[s for s, _ in werte], orientation="h",
                                   marker_color=["#2F855A" if a > 0 else "#DD6B20" if a < 0 else "#A0A8B4" for _, a in werte],
                                   text=[ui.de(a, 1, True) if entwurf[s]["abweichung"] is not None else "Richtung" for s, a in werte],
                                   textposition="outside"))
            lo, hi = min(a for _, a in werte), max(a for _, a in werte)
            fig.update_layout(height=360, margin=dict(l=8, r=30, t=10, b=8), plot_bgcolor="white", showlegend=False)
            fig.update_xaxes(ticksuffix=" Pp.", gridcolor="#EEF0F3", zeroline=True, zerolinecolor="#667085",
                             range=[min(lo, 0) - 0.45, max(hi, 0) + 0.45])
            ui.zeige(fig)
            st.caption("Abweichung zum Index in Pp. des Aktienteils (grün = Übergewicht, orange = Untergewicht).")
        with c2:
            st.markdown("**Prüfung der Allokation**")
            pm = plan_meldungen(entwurf, idx, ctx.ips)
            if not pm:
                st.caption("Keine Hinweise.")
            st.markdown("".join(f'<div class="karte-zeile"><span>{ui.badge(s_, {L.ROT: "Verstoss", L.GELB: "Hinweis", L.GRUEN: "ok"}[s_])}'
                                f'&nbsp; {html.escape(txt)}</span><span></span></div>' for s_, txt in pm), unsafe_allow_html=True)

        st.markdown("#### Begründungen")
        optionen = [""] + list(GROESSEN)
        for s in sorted(C.SEKTOREN, key=lambda x: -(entwurf[x].get("abweichung") or 0)):
            e = entwurf[s]
            a, sc = e.get("abweichung"), score(e)
            kopf = (f"{s} · {e['richtung']}" + ("" if a is None else f" {ui.de(a, 1, True)} Pp.")
                    + ("" if sc is None else f" · Score {ui.de(sc, 2, True)}")
                    + (" · Begründung fehlt" if e["richtung"] != "Neutral" and len(e["begruendung"]) < 20 else ""))
            with st.expander(kopf):
                st.text_area("Warum? (Begründung mit Belegen)", key=f"sa_begruendung_{s}", height=100)
                st.text_area("Was spricht dagegen?", key=f"sa_gegenargument_{s}", height=80)
                st.text_area("Wann ändern wir die Gewichtung? (Trigger als Text)", key=f"sa_trigger_{s}", height=68)
                k1, k2, k3 = st.columns([1.6, 0.6, 1])
                if ss.get(f"sa_regel_groesse_{s}") not in optionen:
                    ss[f"sa_regel_groesse_{s}"] = ""
                k1.selectbox("Automatisch überwachen", optionen, key=f"sa_regel_groesse_{s}",
                             format_func=lambda g: "– nicht automatisch –" if not g else GROESSEN[g][0])
                if ss.get(f"sa_regel_op_{s}") not in ("", "<", ">", "<=", ">="):
                    ss[f"sa_regel_op_{s}"] = ""
                k2.selectbox("Bedingung", ["", "<", ">", "<=", ">="], key=f"sa_regel_op_{s}")
                k3.text_input("Schwelle (Zahl oder Datum JJJJ-MM-TT)", key=f"sa_regel_wert_{s}")
                tr = trig_e[s]
                if tr["stufe"] != L.KONTEXT:
                    st.caption(f"Aktuell: {tr['regel']} · Wert {tr['wert']} · {tr['text']}")

        def _gleich(x, y):
            return (x is None and y is None) or (x is not None and y is not None and abs(float(x) - float(y)) < 1e-9) \
                if not isinstance(x, str) and not isinstance(y, str) else (x or "") == (y or "")
        geaendert = [s for s in C.SEKTOREN if any(not _gleich(entwurf[s].get(k), sek[s].get(k) if k not in SCORE_GEWICHTE and k != "abweichung"
                                                                   else _z(sek[s].get(k))) for k in FELDER)]
        if geaendert or sa["quelle"] == "Georg":
            st.warning("Ungespeicherte Änderungen" + (f": {', '.join(geaendert)}" if geaendert else " (Startwerte)") +
                       " – erst nach «Speichern» gelten sie für Taktik, Portfolio und Analyse.", icon=":material/edit_note:")
        if st.button("Sektorallokation speichern", type="primary", icon=":material/save:", disabled=ctx.nur_lesen):
            fehler = [f"{s}: Begründung fehlt (mind. 20 Zeichen bei Über-/Untergewicht)" for s in C.SEKTOREN
                      if entwurf[s]["richtung"] != "Neutral" and len(entwurf[s]["begruendung"]) < 20]
            if fehler:
                for f in fehler:
                    st.error(f)
            else:
                z = jetzt()
                ctx.speichern("sektor_allokation", [dict(zeit=z, person=ctx.person, sektor=s,
                                                         **{k: ("" if v is None else v) for k, v in entwurf[s].items()})
                                                    for s in C.SEKTOREN], "Sektorallokation gespeichert.")
        # Verlauf
        tab = ctx.tabellen.get("sektor_allokation")
        if tab is not None and not tab.empty:
            with st.expander("Verlauf (alle gespeicherten Versionen)", icon=":material/history:"):
                v = tab.sort_values("zeit", ascending=False)
                st.dataframe(pd.DataFrame({"Gespeichert": v["zeit"].str[:16].str.replace("T", " "), "Person": v["person"],
                                           "Sektor": v["sektor"], "Richtung": v["richtung"], "Abweichung": v["abweichung"],
                                           "Begründung": v["begruendung"].str[:100]}), hide_index=True, width="stretch")

        # PDF: gespeicherter Stand
        reg = regel_abweichungen(sek, idx)
        zp = []
        for s in sorted(C.SEKTOREN, key=lambda x: -(sek[x].get("abweichung") or 0)):
            e, a, sc = sek[s], sek[s].get("abweichung"), score(sek[s])
            tr = next(t for t in trig if t["sektor"] == s)
            zp.append({"Sektor": s, "Richtung": e["richtung"], "Abweichung": "–" if a is None else f"{ui.de(a, 1, True)} Pp.",
                       "Index heute": f"{ui.de(idx[s], 2)} %", "Ziel % Aktien": "–" if ziel[s] is None else f"{ui.de(ziel[s], 2)} %",
                       "Ziel % Gesamt": "–" if ziel[s] is None else f"{ui.de(ziel[s] * aktien_soll / 100, 2)} %",
                       "Relativ": "–" if a is None or not idx[s] else f"{ui.de(a / idx[s] * 100, 0, True)} %",
                       "Score": "–" if sc is None else ui.de(sc, 2, True), "Regel": f"{ui.de(reg[s], 1, True)} Pp.",
                       "Trigger": tr["stufe"] if tr["stufe"] != L.KONTEXT else "Text"})
        R.h("Sektorallokation" + (" (Startwerte, noch nicht gespeichert)" if sa["quelle"] == "Georg" else ""))
        R.text(f"Index: Stichtag {info['stichtag']} ({info['quelle']})" + (f", fortgeschrieben bis {info['bis']:%d.%m.%Y}" if info["fortgeschrieben"] else "")
               + f" · Aktienquote {ui.de(aktien_soll, 1)} %")
        R.tabelle(pd.DataFrame(zp), [26, 20, 18, 18, 18, 18, 14, 12, 20, 16])
        for s in C.SEKTOREN:
            e = sek[s]
            R.text(f"{s} ({e['richtung']}): {e.get('begruendung') or '-'} | Dagegen: {e.get('gegenargument') or '-'} | "
                   f"Trigger: {e.get('trigger') or '-'}", klein=True)

    # --- Indexgewichte -------------------------------------------------------
    with t3:
        st.markdown("Eine Tabelle für das ganze Dashboard: Taktik, Risiko, Portfolio und Analyse rechnen mit diesen Gewichten. "
                    "Zwischen zwei Stichtagen schreibt die App die Gewichte mit den Kursen der SPDR-Sektor-ETFs fort "
                    "(neues Gewicht ∝ altes Gewicht × (1 + Sektorrendite)).")
        zeilen = [{"Sektor": s, "ETF": C.SEKTOR_ETF[s], f"Stichtag {pd.Timestamp(info['stichtag']):%d.%m.}": f"{ui.de(info['basis'][s], 2)} %",
                   "Rendite seit Stichtag": "–" if s not in info["rendite"] else f"{ui.de(info['rendite'][s] * 100, 1, True)} %",
                   "Heute (fortgeschrieben)": f"{ui.de(idx[s], 2)} %", "Veränderung": f"{ui.de(idx[s] - info['basis'][s], 2, True)} Pp.",
                   "Dozenten-App (fest)": f"{ui.de(C.SP500_GEWICHT_DOZENT[s], 2)} %"} for s in C.SEKTOREN]
        st.dataframe(pd.DataFrame(zeilen), hide_index=True, width="stretch")
        if info["index_rendite"] is not None:
            st.caption(f"S&P 500 (aus Sektoren) seit Stichtag ≈ {ui.de(info['index_rendite'] * 100, 1, True)} %. "
                       "Die Spalte «Dozenten-App» zeigt die fest hinterlegten Werte zum Vergleich; sie werden nicht mehr verwendet.")
        st.markdown("#### Neuen Stichtag eintragen")
        st.caption("Am 30.11. die echten Gewichte eintragen (Quelle: LSEG-Datei der Gruppe, SPDR-Sektor-ETFs, ChartRow oder "
                   "S&P-Factsheet). Vorausgefüllt mit den fortgeschriebenen Werten von heute.")
        with st.form("si_form"):
            k1, k2 = st.columns([1, 2])
            datum = k1.date_input("Stichtag", value=pd.Timestamp.today().date(), format="DD.MM.YYYY")
            quelle = k2.text_input("Quelle (Pflicht)", placeholder="z. B. LSEG-Datei der Gruppe, Stand 30.11.2026 Schluss")
            ed_i = st.data_editor(pd.DataFrame({"Sektor": C.SEKTOREN, "Gewicht %": [round(idx[s], 2) for s in C.SEKTOREN]}),
                                  hide_index=True, disabled=["Sektor"], key="si_ed",
                                  column_config={"Gewicht %": st.column_config.NumberColumn(min_value=0.0, max_value=100.0,
                                                                                             step=0.01, format="%.2f")})
            los_i = st.form_submit_button("Stichtag speichern", type="primary", icon=":material/save:", disabled=ctx.nur_lesen)
        if los_i:
            summe = float(ed_i["Gewicht %"].fillna(0).sum())
            if not quelle.strip():
                st.error("Bitte die Quelle angeben.")
            elif not 98.5 <= summe <= 101.5:
                st.error(f"Summe {ui.de(summe, 2)} % – sie muss ungefähr 100 % sein.")
            else:
                z = jetzt()
                ctx.speichern("sektor_index", [dict(zeit=z, person=ctx.person, stichtag=f"{datum:%Y-%m-%d}", sektor=r["Sektor"],
                                                    gewicht=r["Gewicht %"], quelle=quelle.strip()) for _, r in ed_i.iterrows()],
                              f"Indexgewichte Stichtag {datum:%d.%m.%Y} gespeichert.")
        liste = stichtage(ctx.tabellen.get("sektor_index"))
        if liste:
            st.markdown("#### Gespeicherte Stichtage")
            st.dataframe(pd.DataFrame([{"Stichtag": x["stichtag"], "Quelle": x["quelle"], "Von": x["person"],
                                        **{s: ui.de(x["gewichte"].get(s), 2) for s in C.SEKTOREN}} for x in liste]),
                         hide_index=True, width="stretch")
        R.h("Indexgewichte")
        R.tabelle(pd.DataFrame(zeilen), [28, 12, 22, 26, 28, 24, 26])

    # --- Trigger -------------------------------------------------------------
    with t4:
        df_t = pd.DataFrame([{"Sektor": t["sektor"], "Richtung": t["richtung"], "Automatische Regel": t["regel"],
                              "Aktuell": t["wert"], "Status": f"● {t['stufe']}" if t["stufe"] in (L.ROT, L.GELB, L.GRUEN) else t["text"],
                              "Hinweis": t["text"], "Trigger (Text)": t["trigger"] or "–"} for t in trig])
        st.dataframe(ui.ampel_stil(df_t, ["Status"]), hide_index=True, width="stretch",
                     column_config={"Trigger (Text)": st.column_config.TextColumn(width="large")})
        st.caption("Rot = Bedingung erfüllt → Gewichtung im Team überprüfen (nicht automatisch ändern). Gelb = weniger als 3 % "
                   "von der Schwelle entfernt. Daten: FRED (Brent, WTI, 10-J.-Rendite, Hochzins-Spread, VIX; mit Yahoo-Tageskursen "
                   "ergänzt) und Yahoo Finance (Dollar-Index). Regeln im Tab «Allokation & Begründung» je Sektor einstellen.")
        R.h("Ausstiegs-Trigger")
        R.tabelle(df_t.drop(columns=["Hinweis"]).assign(Status=lambda d: d["Status"].str.replace("● ", "")), [22, 20, 34, 22, 14, 68])

    # --- Szenarien -----------------------------------------------------------
    with t5:
        st.markdown("Wie könnte der Index bis zum Kauftag aussehen – und was hiesse das, wenn wir **feste Prozentzahlen** statt "
                    "«Index + Abweichung» kaufen? Ausgangspunkt sind die heutigen Indexgewichte.")
        if "sa_szen" not in ss:
            ss["sa_szen"] = pd.DataFrame({"Sektor": C.SEKTOREN, **{n: [float(r.get(s, 0)) for s in C.SEKTOREN]
                                                                    for n, r in SZEN_INDEX.items()}})
        ed_s = st.data_editor(ss["sa_szen"], hide_index=True, width="stretch", disabled=["Sektor"], key="sa_szen_ed",
                              column_config={n: st.column_config.NumberColumn(f"{n} (Kurs %)", step=0.5, format="%.1f")
                                             for n in SZEN_INDEX})
        st.caption("Kursannahmen bis zum Kauftag nach Georgs Kapitel 8.3 (B passt zum Bull-, D zum Bear-Szenario des IC); "
                   "Werte für die «übrigen Sektoren» sind ergänzte Annahmen und hier änderbar. Keine Prognose.")
        fest = {s: (ziel[s] if ziel[s] is not None else idx[s]) for s in C.SEKTOREN}
        gew_s, idx_r, abw_fest = {}, {}, {}
        for n in SZEN_INDEX:
            g, r = szenario_gewichte(idx, dict(zip(ed_s["Sektor"], ed_s[n].fillna(0))))
            gew_s[n], idx_r[n] = g, r
            abw_fest[n] = {s: fest[s] - g[s] for s in C.SEKTOREN}
        st.markdown("**Indexgewichte am Kauftag je Szenario**")
        st.dataframe(pd.DataFrame([{"Sektor": s, "Heute": f"{ui.de(idx[s], 2)} %",
                                    **{n.split(" ")[0]: f"{ui.de(gew_s[n][s], 2)} %" for n in SZEN_INDEX},
                                    "Bandbreite": f"{ui.de(min(gew_s[n][s] for n in SZEN_INDEX), 1)}–{ui.de(max(gew_s[n][s] for n in SZEN_INDEX), 1)} %"}
                                   for s in C.SEKTOREN] +
                                  [{"Sektor": "Index (Kurs)", "Heute": "", **{n.split(" ")[0]: f"{ui.de(idx_r[n], 1, True)} %" for n in SZEN_INDEX},
                                    "Bandbreite": ""}]), hide_index=True, width="stretch")
        st.markdown("**Abweichung zum Index am Kauftag, wenn wir die heutigen Zielzahlen fest kaufen**")
        plan = {s: sek[s].get("abweichung") or 0.0 for s in C.SEKTOREN}
        zeilen_a = [{"Sektor": s, "Geplant": f"{ui.de(plan[s], 1, True)}",
                     **{n.split(" ")[0]: f"{ui.de(abw_fest[n][s], 2, True)}" for n in SZEN_INDEX}} for s in C.SEKTOREN]
        zeilen_a.append({"Sektor": "Summe |Abweichungen|", "Geplant": ui.de(sum(abs(v) for v in plan.values()), 1),
                         **{n.split(" ")[0]: ui.de(sum(abs(v) for v in abw_fest[n].values()), 1) for n in SZEN_INDEX}})
        st.dataframe(pd.DataFrame(zeilen_a), hide_index=True, width="stretch")
        it = {n: abw_fest[n]["IT"] for n in SZEN_INDEX}
        st.caption("In Pp. des Aktienteils. Mit «Index am Stichtag + Abweichung» wäre die Abweichung in jedem Szenario gleich "
                   f"der Spalte «Geplant». Grösste ungewollte Wette bei festen Zahlen: IT von {ui.de(min(it.values()), 1, True)} "
                   f"bis {ui.de(max(it.values()), 1, True)} Pp.")
        R.h("Szenarien bis Kauftag (feste Zahlen)")
        R.tabelle(pd.DataFrame(zeilen_a), [44, 22, 22, 22, 22, 22])

    # --- Regeln --------------------------------------------------------------
    with t6:
        st.markdown(f"""
**So entsteht die Gewichtung (Georgs Scorecard)**
- Je Sektor Punkte von −2 bis +2 in vier Bereichen: Makro 30 %, Gewinne 25 %, Bewertung 25 %, Momentum/Risiko 20 %.
- Ab Gesamtscore ±{ui.de(SCHWELLE, 1)} weichen wir ab: {ui.de(PP_JE_PUNKT, 0)} Pp. je Scorepunkt, höchstens ±{ui.de(DECKEL_PP, 0)} Pp. und ±50 % des Indexgewichts.
- Danach werden Über- und Untergewichte so ausgeglichen, dass die Summe null ist (die grössere Seite wird verkleinert).

**Umsetzung**
- Festgelegt wird die **Abweichung zum Index**, nicht eine feste Zahl. Kauf am 01.12.: Indexgewicht vom 30.11. + Abweichung.
- Solange nur die Richtung feststeht, prüft die App: Übergewicht ≥ +{ui.de(RICHTUNG_MIN, 2)} Pp., Untergewicht ≤ −{ui.de(RICHTUNG_MIN, 2)} Pp., neutral ±{ui.de(TOL_GRUEN, 1)} Pp.

**Prüfung im Portfolio und im Probe-Portfolio**
- |Ist − Ziel| ≤ {ui.de(TOL_GRUEN, 1)} Pp. grün, ≤ {ui.de(TOL_GELB, 0)} Pp. gelb, darüber rot (Pp. des Aktienteils).
- Umschichten erst, wenn ein Sektor mehr als {ui.de(TOL_GRUEN, 1)} Pp. vom Ziel abweicht oder die Aktienquote mehr als ±{ui.de(AKTIEN_TOL, 0)} Pp. von der Taktik.
- Ungewollte Abweichung je neutralem Sektor höchstens ±{ui.de(TOL_GRUEN, 1)} Pp.; bewusste Abweichung immer am aktuellen Indexgewicht messen (±50 %).
- Überprüfung monatlich sowie nach dem Fed-Entscheid (9.12.), dem Ende der Zollpause (≈ 10.1.) und der CMS-Frist (19.1.).
""")
    SP.knopf(ph, R, "sektoren", "Sektorallokation.pdf")
