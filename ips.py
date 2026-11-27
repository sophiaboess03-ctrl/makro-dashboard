"""Investment Policy Statement (IPS) – gleiche Felder und Reihenfolge wie in der Dozenten-App.

Gespeichert wird jede Änderung als Zeile (Tabelle «ips»: Feld, Wert); es gilt der neuste Wert je Feld.
Das IPS speist den Rest des Dashboards: strategische Allokation = neutraler Anker, Bänder = Korridor der Taktik,
Duration-Grenzen = Leitplanken im Anleihen-Tool, Sektor-Abweichung = Prüfung der Taktik.
"""
from __future__ import annotations

import html
import math
from datetime import datetime

import numpy as np
import pandas as pd
import streamlit as st

import seitenpdf as SP
from makro import config as C
from makro import ui
from storage import jetzt, zahl

RISIKOBEREITSCHAFT = ["Tief", "Unterdurchschnittlich", "Ausgewogen", "Überdurchschnittlich", "Hoch"]
ESG_ANSATZ = ["Keiner", "Negativ-Screening", "Positiv-Screening", "ESG-Integration", "Thematisch", "Engagement", "Impact"]
ESG_NOTE = ["Keine Mindestnote", "A", "B", "C", "D"]
AUSSCHLUSS = ["Tabak", "Alkohol", "Glücksspiel", "Erotik (Adult Entertainment)", "Kontroverse Waffen", "Kleinwaffen",
              "Militäraufträge", "Atomenergie", "Thermische Kohle", "Palmöl", "Pestizide",
              "Gentechnisch veränderte Organismen", "Tierversuche", "Pelz und Leder", "Verstoss gegen katholische Werte"]
FAKTOREN = ["Value", "Size (klein)", "Momentum", "Quality", "Low Volatility", "Low Beta", "Growth", "Dividendenrendite",
            "ESG", "Gewinnrevisionen"]
FAKTOR_WAHL = ["bitte wählen", "besser", "neutral", "schlechter"]

# (id, Titel, Erläuterung, Typ, Einheit, Startwert)  Typ: text | zahl | wahl | mehrfach
F = dict
TABS = [
    ("Anleger und Ziele", [
        ("Zweck und Anlegerprofil", None, [
            F(id="zweck", titel="Zweck und Anlegerprofil", typ="text",
              hilfe="Für wen verwaltet das Team das Vermögen (Anlegerprofil), und was ist der Zweck des Portfolios? In der "
                    "Simulation ist das Startkapital 10 Mio. USD, angelegt in S&P 500 und US-Staatsanleihen."),
            F(id="ansatz", titel="Anlageansatz", typ="text",
              hilfe="Mit welchem Ansatz will das Team die Ziele erreichen (zum Beispiel Qualität, Wachstum, defensiv, "
                    "Makro-getrieben, Faktoren)? Passiv oder aktiv?"),
        ]),
        ("Renditeziele", "tabelle", [
            F(id="rendite_min", titel="Erforderliche Rendite (Minimum)", typ="zahl", einheit="%", start=0.0,
              hilfe="Required return: die Rendite bis Saisonende, die das Team mindestens erreichen muss. Total Return des Gesamtportfolios."),
            F(id="rendite_ziel", titel="Gewünschte Rendite", typ="zahl", einheit="%", start=3.0,
              hilfe="Desired return: die Rendite bis Saisonende, die das Team anstrebt. Mindestens so hoch wie die erforderliche Rendite."),
            F(id="rendite_rel", titel="Relatives Ziel gegenüber der Benchmark", typ="zahl", einheit="Prozentpunkte", start=0.5,
              hilfe="Relative return objective: gewünschte Mehr- oder Minderrendite gegenüber der Strategie des Dozenten. 0 = gleich wie die Benchmark."),
        ]),
        ("Risikoziele", "tabelle", [
            F(id="verlust_max", titel="Maximal tragbarer Verlust (Risikofähigkeit)", typ="zahl", einheit="%", start=10.0,
              hilfe="Ability to take risk: Verlust gegenüber dem Startkapital, den das Team finanziell höchstens verkraftet (positive Zahl, 10 = Verlust von 10 %)."),
            F(id="vola_max", titel="Höchste Volatilität (absolutes Risikoziel)", typ="zahl", einheit="% p.a.", start=12.0,
              hilfe="Absolute risk objective: höchste akzeptierte Schwankung des Gesamtportfolios. Wird nach Saisonende aus den Tagesrenditen gemessen."),
            F(id="te_max", titel="Höchster Tracking Error (relatives Risikoziel)", typ="zahl", einheit="% p.a.", start=4.0,
              hilfe="Relative risk objective: höchste akzeptierte Schwankung der Renditedifferenz zur Benchmark (Prognose des Aktienteils)."),
            F(id="beta_min", titel="Beta des Aktienteils, Minimum", typ="zahl", einheit="", start=0.8,
              hilfe="Untergrenze für die Marktempfindlichkeit der Aktien."),
            F(id="beta_max", titel="Beta des Aktienteils, Maximum", typ="zahl", einheit="", start=1.2,
              hilfe="Obergrenze für die Marktempfindlichkeit der Aktien."),
        ]),
        ("Risikobereitschaft", None, [
            F(id="bereitschaft", titel="Risikobereitschaft (Willingness)", typ="wahl", optionen=RISIKOBEREITSCHAFT, start="Ausgewogen",
              hilfe="Willingness to take risk: die Einstellung des Teams zu Verlusten. Die Fähigkeit steht oben beim maximalen Verlust."),
            F(id="begr_faehigkeit", titel="Begründung der Risikofähigkeit", typ="text",
              hilfe="Warum verträgt das Team genau diesen Verlust? Denken Sie an Vermögen, Einkommen und Verpflichtungen des Anlegers."),
            F(id="begr_bereitschaft", titel="Begründung der Risikobereitschaft", typ="text",
              hilfe="Wie reagiert der Anleger auf Verluste? Begründen Sie die gewählte Einstellung. Wo Fähigkeit und Bereitschaft "
                    "auseinanderliegen, gilt das tiefere Risiko."),
        ]),
    ]),
    ("Allokation und Beschränkungen", [
        ("Beschränkungen", None, [
            F(id="liquiditaet", titel="Liquidität", typ="text",
              hilfe="Welchen Bargeldbedarf hat der Anleger (Auszahlungen, Notfallreserve)? Begründen Sie damit den Mindest-Cash."),
            F(id="zeithorizont", titel="Zeithorizont", typ="text",
              hilfe="Wie lange legt der Anleger an, und gibt es Phasen (zum Beispiel Aufbau, Auszahlung)? In der Simulation endet die "
                    "Saison zum Saisonende, danach beginnt das Studium des Ergebnisses."),
            F(id="steuern", titel="Steuern", typ="text",
              start="In der Simulation fallen keine Steuern auf Dividenden, Coupons und Kursgewinne an. Das Team behandelt den Anleger "
                    "daher als steuerbefreit und verzichtet auf steuerlich motivierte Verkäufe oder Käufe.",
              hilfe="Steuerliche Stellung des Anlegers. Ändern Sie den Vorschlag, wenn Ihr Team von einer anderen Annahme ausgeht."),
            F(id="recht", titel="Recht und Regulierung", typ="text",
              start="Es gelten die Regeln der Plattform: nur Aktien des S&P 500 und US-Staatsanleihen, keine Leerverkäufe, Handel nur in "
                    "den Sektoren, für die die Person verantwortlich ist, Orders zum Eröffnungskurs oder zum letzten Kurs. Dazu gelten "
                    "die Grenzen dieses IPS als interne Anlagerichtlinie.",
              hilfe="Welche rechtlichen, regulatorischen und internen Regeln schränken die Anlage ein? Die Regeln der Plattform gelten immer."),
        ]),
        ("Strategische Allokation", "tabelle", [
            F(id="saa_aktien", titel="Strategisches Zielgewicht Aktien", typ="zahl", einheit="% des Vermögens", start=45.0,
              hilfe="Neutrales langfristiges Gewicht (SAA), abgeleitet aus Ziel, Risiko und Beschränkungen. Muss im Band unten liegen."),
            F(id="saa_anleihen", titel="Strategisches Zielgewicht Anleihen", typ="zahl", einheit="% des Vermögens", start=50.0,
              hilfe="Neutrales langfristiges Gewicht der Anleihen."),
            F(id="saa_cash", titel="Strategisches Zielgewicht Cash", typ="zahl", einheit="% des Vermögens", start=5.0,
              hilfe="Neutrales langfristiges Gewicht des Cash. Die drei Zielgewichte ergeben zusammen 100 %."),
            F(id="aktien_min", titel="Aktien, Minimum", typ="zahl", einheit="% des Vermögens", start=40.0,
              hilfe="Untergrenze der Aktienquote (taktischer Spielraum)."),
            F(id="aktien_max", titel="Aktien, Maximum", typ="zahl", einheit="% des Vermögens", start=50.0, hilfe="Obergrenze der Aktienquote."),
            F(id="anleihen_min", titel="Anleihen, Minimum", typ="zahl", einheit="% des Vermögens", start=45.0,
              hilfe="Untergrenze der Anleihenquote."),
            F(id="anleihen_max", titel="Anleihen, Maximum", typ="zahl", einheit="% des Vermögens", start=55.0,
              hilfe="Obergrenze der Anleihenquote."),
            F(id="cash_min", titel="Cash, Minimum", typ="zahl", einheit="% des Vermögens", start=0.0,
              hilfe="Liquiditätsreserve: Mindestanteil Cash (siehe Liquidität)."),
            F(id="cash_max", titel="Cash, Maximum", typ="zahl", einheit="% des Vermögens", start=10.0,
              hilfe="Höchste Liquidität (Cash ist kein Selbstzweck)."),
            F(id="rebal_toleranz", titel="Rebalancing-Toleranz", typ="zahl", einheit="Prozentpunkte", start=5.0,
              hilfe="Zulässige Abweichung der Ist-Quote von Aktien, Anleihen und Cash vom Zielgewicht der Taktik, bevor das Team umschichtet."),
        ]),
        ("Aktien und Anleihen", "tabelle", [
            F(id="sektor_abw_max", titel="Maximale Abweichung je Sektor", typ="zahl", einheit="Prozentpunkte", start=5.0,
              hilfe="Höchste erlaubte Abweichung des Sektorgewichts von der Benchmark (Über- oder Untergewicht)."),
            F(id="titel_max", titel="Maximales Gewicht je Titel", typ="zahl", einheit="% des Vermögens", start=5.0,
              hilfe="Grösstes erlaubtes Gewicht einer einzelnen Aktie im Gesamtvermögen."),
            F(id="aktien_anzahl_min", titel="Mindestzahl Aktien", typ="zahl", einheit="Titel", start=25.0,
              hilfe="Wie viele Aktien das Portfolio mindestens halten muss (Diversifikation)."),
            F(id="active_share_min", titel="Active Share, Minimum", typ="zahl", einheit="%", start=20.0,
              hilfe="Wie stark sich das Aktienportfolio mindestens vom S&P 500 unterscheiden soll."),
            F(id="active_share_max", titel="Active Share, Maximum", typ="zahl", einheit="%", start=80.0,
              hilfe="Obergrenze der Abweichung vom S&P 500."),
            F(id="duration_min", titel="Duration der Anleihen, Minimum", typ="zahl", einheit="Jahre", start=2.0,
              hilfe="Untergrenze der Zinsempfindlichkeit des Anleiheteils."),
            F(id="duration_max", titel="Duration der Anleihen, Maximum", typ="zahl", einheit="Jahre", start=8.0,
              hilfe="Obergrenze der Zinsempfindlichkeit des Anleiheteils."),
        ]),
    ]),
    ("Ausschlüsse", [
        ("Besondere Umstände", None, [
            F(id="esg_ansatz", titel="ESG-Ansatz", typ="wahl", optionen=ESG_ANSATZ, start="Keiner",
              hilfe="Negativ-Screening schliesst aus, Positiv-Screening bevorzugt, ESG-Integration bezieht ESG in die Analyse ein, "
                    "thematisch setzt auf Themen, Engagement übt Aktionärsrechte aus, Impact zielt auf messbare Wirkung."),
            F(id="esg_note", titel="ESG-Mindestnote der Aktien", typ="wahl", optionen=ESG_NOTE, start="Keine Mindestnote",
              hilfe="Aktien mit schlechterer LSEG-ESG-Note dürfen nicht gehalten werden."),
            F(id="aus_sektoren", titel="Ausgeschlossene Sektoren", typ="mehrfach", optionen=list(C.SEKTOREN),
              hilfe="Sektoren, die Ihr Team nicht hält (zum Beispiel aus ethischen Gründen). Das Portfolio wird dagegen geprüft."),
            F(id="aus_titel", titel="Ausgeschlossene einzelne Titel", typ="zeile", hilfe="Kürzel, durch Komma getrennt."),
            F(id="aus_kriterien", titel="Ausschlusskriterien des Kunden (Yahoo/Sustainalytics)", typ="mehrfach", optionen=AUSSCHLUSS,
              hilfe="Geschäftsfelder, mit denen der Kunde nichts zu tun haben will. Wählen Sie nur, was der Kunde wirklich ausschliesst."),
            F(id="besondere", titel="Besondere Umstände und ESG", typ="text",
              hilfe="Alles, was die Zusammensetzung des Portfolios wesentlich beeinflusst: ESG-Vorgaben, Ausschlüsse, Überzeugungen des "
                    "Anlegers. Falls keine: begründen Sie."),
        ]),
    ]),
    ("Faktoren", [
        ("Faktorpositionierung", "faktoren", [
            *[F(id=f"faktor_{i}", titel=n, typ="wahl", optionen=FAKTOR_WAHL, start="bitte wählen", hilfe="")
              for i, n in enumerate(FAKTOREN)],
            F(id="faktor_begruendung", titel="Begründung der Faktorpositionierung", typ="text",
              hilfe="Warum soll sich das Portfolio bei diesen Faktoren besser, neutral oder schlechter als die Benchmark positionieren? "
                    "Leiten Sie es aus dem Anlegerprofil, den Zielen und den Überzeugungen des Kunden ab."),
        ]),
    ]),
    ("Umsetzung", [
        ("Umsetzung", None, [
            F(id="rebalancing", titel="Rebalancing und Umsetzung", typ="text",
              hilfe="Wann und wie setzt das Team um? Nach welchen Regeln schichtet es um (Termin, Abweichung von der Toleranz)? Wie "
                    "weicht es taktisch von der strategischen Allokation ab?"),
            F(id="prozess", titel="Entscheidungsprozess und Verantwortung", typ="text",
              hilfe="Wer entscheidet im Team? Wer ist für welche Sektoren und die Anleihen verantwortlich? Wie werden "
                    "Meinungsverschiedenheiten gelöst?"),
            F(id="ueberpruefung", titel="Überprüfung und Berichterstattung", typ="text",
              hilfe="Wie oft prüft das Team die Einhaltung des IPS und die Ergebnisse (zum Beispiel monatlich bei der Taktik), und "
                    "woran misst es den Erfolg (Benchmark, Kennzahlen)?"),
        ]),
    ]),
    ("Beschreibung", [
        ("Beschreibung", None, [
            F(id="beschreibung", titel="Beschreibung", typ="text", max=4000,
              hilfe="Hier beschreibt Ihr Team frei und zusammenhängend, wer der Kunde ist, was er will und wie Sie vorgehen. Alles, was "
                    "in den Feldern nicht Platz hat, gehört hierhin (höchstens 4000 Zeichen, freiwillig)."),
        ]),
    ]),
]
FELDER = {f["id"]: f for _, abschnitte in TABS for _, _, felder in abschnitte for f in felder}
PFLICHT_TEXT = [k for k, f in FELDER.items() if f["typ"] == "text" and k != "beschreibung"]


# ---------------------------------------------------------------------------
# Werte lesen
# ---------------------------------------------------------------------------
def werte(tab: pd.DataFrame | None) -> dict:
    """Aktuelle IPS-Werte (neuster Eintrag je Feld, sonst Startwert). Zahlen als float, Mehrfachauswahl als Liste."""
    roh = {}
    meta = {}
    if tab is not None and not tab.empty:
        for _, r in tab.sort_values("zeit").iterrows():
            roh[r["feld"]] = r["wert"]
            meta[r["feld"]] = (r["person"], str(r["zeit"]))
    out = {}
    for k, f in FELDER.items():
        v = roh.get(k, f.get("start", "" if f["typ"] != "mehrfach" else []))
        if f["typ"] == "zahl":
            z = zahl(v)
            v = z if z is not None else f.get("start")
        elif f["typ"] == "mehrfach":
            v = [x for x in str(v).split("|") if x] if isinstance(v, str) else list(v or [])
        else:
            v = "" if v is None else str(v)
        out[k] = v
    out["_meta"] = meta
    out["_gespeichert"] = bool(roh)
    out["_stand"] = max((m[1] for m in meta.values()), default="")
    return out


def neutral(ips: dict, einst: dict) -> dict:
    return dict(aktien=ips["saa_aktien"], anleihen=ips["saa_anleihen"], cash=ips["saa_cash"],
                duration=einst.get("duration", C.NEUTRAL_DEFAULT["duration"]), tips=einst.get("tips", C.NEUTRAL_DEFAULT["tips"]))


def baender(ips: dict) -> dict:
    return {"aktien": (ips["aktien_min"], ips["aktien_max"]), "anleihen": (ips["anleihen_min"], ips["anleihen_max"]),
            "cash": (ips["cash_min"], ips["cash_max"])}


# ---------------------------------------------------------------------------
# Prüfungen
# ---------------------------------------------------------------------------
def pruefungen(ips: dict, risiko: dict | None = None) -> list[tuple[str, str, str]]:
    """(Status ok/warnung/fehler, Thema, Text)."""
    out = []
    s = ips["saa_aktien"] + ips["saa_anleihen"] + ips["saa_cash"]
    out.append(("ok" if abs(s - 100) < 0.01 else "fehler", "Strategische Allokation",
                f"Summe der Zielgewichte {ui.de(s, 2)} % (muss 100 % sein)."))
    for k, name in (("aktien", "Aktien"), ("anleihen", "Anleihen"), ("cash", "Cash")):
        lo, hi = ips[f"{k}_min"], ips[f"{k}_max"]
        dlo, dhi = C.DOZENT_BAND[k]
        if lo > hi:
            out.append(("fehler", f"Band {name}", f"Minimum {ui.de(lo, 0)} % ist grösser als Maximum {ui.de(hi, 0)} %."))
        elif lo < dlo or hi > dhi:
            out.append(("fehler", f"Band {name}", f"{ui.de(lo, 0)}–{ui.de(hi, 0)} % liegt nicht innerhalb der Bandbreite des "
                                                  f"Dozenten ({dlo}–{dhi} %)."))
        else:
            out.append(("ok", f"Band {name}", f"{ui.de(lo, 0)}–{ui.de(hi, 0)} % innerhalb der Dozenten-Bandbreite {dlo}–{dhi} %."))
        saa = ips[f"saa_{k}"]
        if not lo <= saa <= hi:
            out.append(("fehler", f"Zielgewicht {name}", f"{ui.de(saa, 0)} % liegt nicht im eigenen Band {ui.de(lo, 0)}–{ui.de(hi, 0)} %."))
    if ips["rendite_ziel"] < ips["rendite_min"]:
        out.append(("fehler", "Renditeziele", "Die gewünschte Rendite muss mindestens so hoch sein wie die erforderliche."))
    for a, b, name in (("beta_min", "beta_max", "Beta"), ("active_share_min", "active_share_max", "Active Share"),
                       ("duration_min", "duration_max", "Duration")):
        if ips[a] > ips[b]:
            out.append(("fehler", name, "Minimum ist grösser als Maximum."))
    if ips["verlust_max"] <= 0:
        out.append(("warnung", "Risikofähigkeit", "Der maximal tragbare Verlust ist als positive Zahl einzutragen (10 = Verlust von 10 %)."))
    leer = [FELDER[k]["titel"] for k in PFLICHT_TEXT if not str(ips.get(k, "")).strip()]
    if leer:
        out.append(("warnung", "Noch leer", ", ".join(leer)))
    offen = [FELDER[f"faktor_{i}"]["titel"] for i in range(len(FAKTOREN)) if ips.get(f"faktor_{i}") in ("", "bitte wählen")]
    if offen:
        out.append(("warnung", "Faktoren", "Jeder Faktor braucht eine Wahl. Offen: " + ", ".join(offen)))
    if len(ips.get("beschreibung", "")) > 4000:
        out.append(("fehler", "Beschreibung", f"{len(ips['beschreibung'])} Zeichen, erlaubt sind höchstens 4000."))
    if risiko:
        if risiko.get("vola") is not None:
            st_ = "ok" if risiko["vola"] <= ips["vola_max"] else "warnung"
            out.append((st_, "Volatilität (Schätzung)", f"Strategische Allokation ≈ {ui.de(risiko['vola'], 1)} % p.a. "
                                                         f"(Ziel höchstens {ui.de(ips['vola_max'], 0)} %). {risiko['quelle']}"))
        if risiko.get("verlust_bear") is not None:
            st_ = "ok" if -risiko["verlust_bear"] <= ips["verlust_max"] else "warnung"
            out.append((st_, "Verlust im Bear-Szenario", f"Strategische Allokation über 3 Monate ≈ {ui.de(risiko['verlust_bear'], 1, True)} % "
                                                         f"(tragbar höchstens −{ui.de(ips['verlust_max'], 0)} %)."))
        if risiko.get("rendite_erw") is not None:
            st_ = "ok" if risiko["rendite_erw"] >= ips["rendite_min"] else "warnung"
            out.append((st_, "Erwartete Rendite (Szenarien)", f"Strategische Allokation über 3 Monate ≈ {ui.de(risiko['rendite_erw'], 1, True)} % "
                                                              f"(erforderlich {ui.de(ips['rendite_min'], 1)} %, gewünscht {ui.de(ips['rendite_ziel'], 1)} %). "
                                                              "Grobe Schätzung: gewichtete Monatswerte × 3."))
    return out


def risiko_schaetzung(ips: dict, hist: dict, gew: dict, defs: dict, bond: dict) -> dict:
    """Volatilität der strategischen Allokation aus 3 Jahren Tagesrenditen (S&P 500 TR und Treasury-ETF) und grobe Szenario-Renditen."""
    out = {"quelle": ""}
    a, b = hist.get("^SP500TR") if hist.get("^SP500TR") is not None else hist.get("SPY"), hist.get("GOVT")
    wa, wb = ips["saa_aktien"] / 100, ips["saa_anleihen"] / 100
    if a is not None and b is not None and len(a) > 200 and len(b) > 200:
        r = pd.concat([a.pct_change(), b.pct_change()], axis=1).dropna().tail(756)
        cov = r.cov().values * 252
        out["vola"] = float(math.sqrt(wa ** 2 * cov[0, 0] + wb ** 2 * cov[1, 1] + 2 * wa * wb * cov[0, 1]) * 100)
        out["quelle"] = "Aus 3 Jahren Tagesrenditen (S&P 500 TR, GOVT)."
    else:
        out["vola"] = math.sqrt((wa * 16) ** 2 + (wb * 6) ** 2)
        out["quelle"] = "Annahme: S&P 500 16 %, Treasuries 6 % p.a., Korrelation 0."

    def rendite(par):
        dy = par.get("dgs2", 0) + (par.get("dgs10", 0) - par.get("dgs2", 0)) * min(max((bond["laufzeit"] - 2) / 8, 0), 1.2)
        anl = bond["rendite"] / 12 - bond["duration"] * dy / 100
        cash = bond.get("cash", 4.0) / 12
        return 3 * (wa * par.get("sp500", 0) + wb * anl + ips["saa_cash"] / 100 * cash)

    out["rendite_erw"] = rendite(gew["param"])
    out["verlust_bear"] = rendite(defs["ic_bear"]["param"]) if "ic_bear" in defs else None
    return out


# ---------------------------------------------------------------------------
# Seite
# ---------------------------------------------------------------------------
def _frist_text() -> tuple[str, bool]:
    frist = pd.Timestamp(C.IPS_FRIST)
    jetzt_ = pd.Timestamp(datetime.now())
    if jetzt_ >= frist:
        return f"Frist {frist:%d.%m.%Y %H:%M} abgelaufen – das IPS in der Dozenten-App ist endgültig.", False
    d = frist - jetzt_
    return (f"Pflicht: Das IPS muss bis {frist:%d.%m.%Y, %H:%M} Uhr in der Dozenten-App erfasst sein – noch {d.days} Tage "
            f"{d.seconds // 3600} Std."), True


def seite_ips(ctx, hist: dict, gew: dict, defs: dict, bond: dict):
    ips = ctx.ips
    R = SP.Sammler("Investment Policy Statement", "Team-Entwurf für die Dozenten-App · Benchmark 50 % S&P 500 / 50 % US-Staatsanleihen",
                   kurz="IPS")
    ui.seitenkopf("Investment Policy Statement (IPS)",
                  "Gleiche Felder wie in der Dozenten-App. Hier im Team erarbeiten, prüfen und dann in die Dozenten-App übertragen. "
                  f"Zuletzt gespeichert: {ips['_stand'][:16].replace('T', ' ') or 'noch nie'}.")
    ph = st.empty()
    letzte = max(ips["_meta"].values(), key=lambda m: m[1], default=("", ""))
    ui.stand_abgleich("ips", ips["_stand"], ("ips_",), letzte[0])
    txt, offen = _frist_text()
    (st.error if offen else st.info)(txt, icon=":material/schedule:")
    st.caption("Wirkt im ganzen Dashboard: strategische Allokation = neutraler Anker, Bänder = Korridor der Taktik, Duration-Grenzen "
               "→ Anleihen-Tool, maximale Sektorabweichung → Prüfung der Taktik.")

    st.markdown("#### Rahmenbedingungen der Simulation (vorgegeben)")
    rahmen = pd.DataFrame([
        ("Benchmark", "Strategie des Dozenten: 50 % Aktien (S&P 500) und 50 % US-Staatsanleihen"),
        ("Zeitraum", "01.12.2026 bis 28.02.2027"), ("Startkapital", "10'000'000 USD"),
        ("Bandbreiten des Dozenten", "Aktien 40 bis 60 %, Anleihen 40 bis 60 %, Cash 0 bis 10 %. Eure Bänder müssen darin liegen."),
    ], columns=["Punkt", "Vorgabe"])
    st.dataframe(rahmen, hide_index=True, width="stretch")
    R.h("Rahmenbedingungen")
    R.tabelle(rahmen, [45, 135])

    risiko = risiko_schaetzung(ips, hist, gew, defs, bond)
    pr = pruefungen(ips, risiko)
    n_f, n_w = sum(p[0] == "fehler" for p in pr), sum(p[0] == "warnung" for p in pr)
    st.markdown("#### Prüfung")
    ui.kopf_kpis([
        ("Fehler", f'<span style="color:{ui.FARBE["Rot"] if n_f else ui.FARBE["Grün"]}">{n_f}</span>', "muss vor der Abgabe weg"),
        ("Hinweise", str(n_w), "leere Felder, Plausibilität"),
        ("SAA", f"{ui.de(ips['saa_aktien'], 0)} / {ui.de(ips['saa_anleihen'], 0)} / {ui.de(ips['saa_cash'], 0)}",
         "Aktien / Anleihen / Cash"),
        ("Volatilität SAA (Schätzung)", f"{ui.de(risiko['vola'], 1)} %", f"Ziel ≤ {ui.de(ips['vola_max'], 0)} % p.a."),
    ])
    sym = {"ok": "✓", "warnung": "!", "fehler": "✗"}
    pdf_pr = pd.DataFrame([(sym[a], b, c) for a, b, c in pr], columns=["", "Prüfung", "Ergebnis"])
    with st.expander(f"Alle Prüfungen ({len(pr)})", expanded=bool(n_f)):
        for a, b, c in pr:
            (st.error if a == "fehler" else st.warning if a == "warnung" else st.success)(f"**{b}:** {c}")
    R.h("Prüfung")
    R.tabelle(pdf_pr, [8, 45, 127])

    # Update 16: IPS ↔ Portfolio aus Risikosicht (Gap-Analyse) – mit der Taktik des Monats, ex ante
    with st.expander("IPS ↔ Portfolio aus Risikosicht: Gap-Analyse mit der Taktik (ex ante)", icon=":material/compare_arrows:"):
        try:
            import ips_abgleich as IA

            st.caption("Jede IPS-Aussage als messbare Kennzahl mit Grenze, hier gemessen an der Taktik des Monats (Aufteilung, "
                       "Sektoren, Duration) mit der Szenario-Simulation bis Saisonende. Dieselbe Prüfung mit den echten Titeln: "
                       "Analyse → Reiter «IPS-Abgleich» (Analyseportfolio) und Portfolio → «Performance & Risiko» (Bestand).")
            IA.anzeigen(IA.gap_analyse(ips, ctx.alloc, IA.messwerte_taktik(ctx)), titel="IPS gegen Taktik: Risiko-Abgleich", R=R)
        except Exception as e:  # noqa: BLE001
            st.caption(f"Gap-Analyse nicht möglich: {type(e).__name__}: {e}")

    tabs = st.tabs([t for t, _ in TABS])
    aenderungen = {}
    for (tab_name, abschnitte), tab in zip(TABS, tabs):
        R.h(tab_name)
        with tab:
            for titel, art, felder in abschnitte:
                with st.container(border=True):
                    st.markdown(f"**{titel}**")
                    pdf_zeilen = []
                    for f in felder:
                        alt = ips[f["id"]]
                        key = f"ips_{f['id']}"
                        if art in ("tabelle", "faktoren") and f["typ"] in ("zahl", "wahl"):
                            c1, c2, c3 = st.columns([1.4, 0.8, 2.6])
                            c1.markdown(f"**{f['titel']}**")
                            if f["typ"] == "zahl":
                                neu = c2.number_input(f["titel"], value=float(alt), step=0.5 if f.get("einheit") != "" else 0.05,
                                                      key=key, label_visibility="collapsed", disabled=ctx.nur_lesen)
                            else:
                                neu = c2.selectbox(f["titel"], f["optionen"], index=f["optionen"].index(alt) if alt in f["optionen"] else 0,
                                                   key=key, label_visibility="collapsed", disabled=ctx.nur_lesen)
                            c3.markdown(f"<span class='klein'>{html.escape(f.get('einheit', ''))} · {html.escape(f['hilfe'])}</span>",
                                        unsafe_allow_html=True)
                            pdf_zeilen.append((f["titel"], f"{ui.de(neu, 2) if f['typ'] == 'zahl' else neu} {f.get('einheit', '')}".strip(),
                                               f["hilfe"]))
                        else:
                            st.markdown(f"<div style='font-weight:600; margin-top:6px'>{html.escape(f['titel'])}</div>"
                                        f"<span class='klein'>{html.escape(f['hilfe'])}</span>", unsafe_allow_html=True)
                            if f["typ"] == "text":
                                neu = st.text_area(f["titel"], value=alt, height=110, key=key, label_visibility="collapsed",
                                                   max_chars=f.get("max"), disabled=ctx.nur_lesen)
                            elif f["typ"] == "zeile":
                                neu = st.text_input(f["titel"], value=alt, key=key, label_visibility="collapsed", disabled=ctx.nur_lesen)
                            elif f["typ"] == "wahl":
                                neu = st.selectbox(f["titel"], f["optionen"], index=f["optionen"].index(alt) if alt in f["optionen"] else 0,
                                                   key=key, label_visibility="collapsed", disabled=ctx.nur_lesen)
                            else:
                                neu = st.pills(f["titel"], f["optionen"], selection_mode="multi", default=[x for x in alt if x in f["optionen"]],
                                               key=key, label_visibility="collapsed", disabled=ctx.nur_lesen) or []
                            wert_txt = ", ".join(neu) if isinstance(neu, list) else str(neu)
                            R.h2(f["titel"])
                            R.text(wert_txt or "(leer)", klein=not wert_txt)
                        neu_s = "|".join(neu) if isinstance(neu, list) else (repr(float(neu)) if f["typ"] == "zahl" else str(neu))
                        alt_s = "|".join(alt) if isinstance(alt, list) else (repr(float(alt)) if f["typ"] == "zahl" else str(alt))
                        if neu_s != alt_s:
                            aenderungen[f["id"]] = neu_s
                    if pdf_zeilen:
                        R.tabelle(pd.DataFrame(pdf_zeilen, columns=["Vorgabe", "Wert", "Erläuterung"]), [55, 30, 95])
                    if titel == "Strategische Allokation":
                        s = sum(st.session_state.get(f"ips_saa_{k}", ips[f"saa_{k}"]) for k in ("aktien", "anleihen", "cash"))
                        farbe = ui.FARBE["Grün"] if abs(s - 100) < 0.01 else ui.FARBE["Rot"]
                        st.markdown(f"<span style='color:{farbe}; font-weight:600'>Summe Zielgewichte: {ui.de(s, 2)} %</span>",
                                    unsafe_allow_html=True)

    st.divider()
    c1, c2 = st.columns([1, 3])
    if c1.button(f"IPS speichern ({len(aenderungen)} Änderung{'en' if len(aenderungen) != 1 else ''})", type="primary",
                 disabled=not aenderungen or ctx.nur_lesen, icon=":material/save:"):
        z = jetzt()
        ctx.speichern("ips", [dict(zeit=z, person=ctx.person, feld=k, wert=v) for k, v in aenderungen.items()], "IPS gespeichert.")
    c2.caption("Gespeichert werden nur geänderte Felder; der Verlauf bleibt in der Google-Tabelle (Blatt «ips») erhalten. "
               "Es zählt der zuletzt gespeicherte Stand – wie in der Dozenten-App.")

    with st.expander("Text zum Übertragen in die Dozenten-App", icon=":material/content_copy:"):
        teile = []
        for tab_name, abschnitte in TABS:
            for titel, _, felder in abschnitte:
                for f in felder:
                    v = ips[f["id"]]
                    v = ", ".join(v) if isinstance(v, list) else (ui.de(v, 2) if f["typ"] == "zahl" else v)
                    teile.append(f"{f['titel']}: {v}")
        st.code("\n".join(teile), language=None)
    SP.knopf(ph, R, "ips", f"IPS_Team_{datetime.now():%Y-%m-%d}.pdf")
