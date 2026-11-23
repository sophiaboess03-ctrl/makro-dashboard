"""Inhalte der Team-Makroanalyse USA (Portfoliosimulation FHGR, Dez. 2026 – Feb. 2027).

Hier stehen alle Indikatoren, Schwellen, Szenarien, Risiken, Termine und Allokationsregeln.
Wer etwas ändern will (z. B. eine Schwelle), ändert es hier – die App rechnet alles andere neu.

Herkunft der Schwellen:
  "Recherche"  = aus eurer Makroanalyse
  "Vorschlag"  = von Claude ergänzt (neue Indikatoren) – bitte im Team prüfen
"""

SIMULATION_START = "2026-12-01"
SIMULATION_ENDE = "2027-02-28"
NEUTRALE_ZYKLUS_RANGE = (0.0, 1.0)

# ---------------------------------------------------------------------------
# Kapitel
# ---------------------------------------------------------------------------
KAPITEL = [
    {
        "id": "wachstum",
        "name": "Wachstum & Zyklus",
        "icon": ":material/trending_up:",
        "fazit": "Fortgeschrittene Expansion: BIP Q3 +3,4 % annualisiert, Output Gap +1,7 %, "
                 "ISM-Neuaufträge seit 10 Monaten expansiv. Gegenpol: hoher Kostendruck (ISM-Preise über 75).",
    },
    {
        "id": "arbeitsmarkt",
        "name": "Arbeitsmarkt",
        "icon": ":material/work:",
        "fazit": "Robust: Arbeitslosenquote 4,1 % und seit Q4 2025 sinkend, +165'000 Stellen im Oktober, "
                 "Lohnwachstum 3,1 %. Kein Rezessionssignal für die nächsten 3 Monate, wenig lohnseitiger Inflationsdruck.",
    },
    {
        "id": "inflation",
        "name": "Inflation & Fed",
        "icon": ":material/price_change:",
        "fazit": "Inflation hartnäckig über Ziel: Kern-PCE 3,1 %, Kern-CPI +0,3 % zum Vormonat (≈ 3,6 % annualisiert). "
                 "Die Fed erhöht; unsere Wahrscheinlichkeit bis 28.02.: Erhöhung 85 %, Halten 14 %, Senkung 1 %.",
    },
    {
        "id": "zinsen",
        "name": "Zinsen & Anleihen",
        "icon": ":material/stacked_line_chart:",
        "fazit": "Lange Zinsen hoch (10 J. 5,10 %) wegen Defizit ≈ 6 % des BIP und schwacher Auktionen. "
                 "Bear Flattening (10 J. − 2 J. +22 bp): lange Laufzeiten bringen kaum Mehrrendite → Schwerpunkt 1–7 J., nichts über 10 J.",
    },
    {
        "id": "kredit",
        "name": "Kredit & Finanzbedingungen",
        "icon": ":material/account_balance:",
        "fazit": "Finanzbedingungen locker (NFCI −0,46), Spreads sehr eng (BB 184 bp, BBB 107 bp); Stress nur im CCC-Segment. "
                 "Öl und starker Dollar belasten.",
    },
    {
        "id": "konsum",
        "name": "Konsum & Stimmung",
        "icon": ":material/shopping_cart:",
        "fazit": "Konsum aus Ersparnis: Sparquote 3,8 % (tiefster Stand seit Okt. 2022), Realkonsum stagniert, Realeinkommen −0,3 %. "
                 "Stimmung schwach – getrieben von Preisen, nicht von Jobangst.",
    },
    {
        "id": "gewinne",
        "name": "Gewinne & Bewertung",
        "icon": ":material/monitoring:",
        "fazit": "Bewertung fair (Forward-KGV 17,8 unter dem 10-J.-Schnitt von 19,1), aber Aktienrisikoprämie nur +0,51 PP. "
                 "Positionierung einseitig: Fondsmanager-Cash 3,9 %, Tech-Übergewicht netto 37 %.",
    },
    {
        "id": "geopolitik",
        "name": "Geopolitik & Extremrisiken",
        "icon": ":material/public:",
        "fazit": "Iran-Krieg/Hormus hält Brent bei 85–100 USD; Handelsfrieden USA–China läuft ≈ 10.01.2027 aus. "
                 "Weitere Risiken: Cyber, Russland–NATO, Protektionismus/USMCA und Tail Risks.",
    },
]
KAPITEL_BY_ID = {k["id"]: k for k in KAPITEL}

# ---------------------------------------------------------------------------
# Indikatoren
# quelle: "fred" (automatisch), "manuell" (Team-Eingabe), "berechnet" (aus anderen Werten)
# Jeder Indikator kann zusätzlich manuell überschrieben werden: der Wert mit dem neusten Datum gewinnt.
# richtung: "hoch" = höher ist schlechter, "tief" = tiefer ist schlechter, "kontext" = keine Ampel
# transform: level | yoy | mom | diff | ann_qoq | drawdown ; scale wird danach multipliziert
# seed: Werte aus der Makroanalyse (Fallback, falls FRED nicht erreichbar / Startwert für manuelle)
# ---------------------------------------------------------------------------
I = []

def _ind(**kw):
    kw.setdefault("gelb", None)
    kw.setdefault("rot", None)
    kw.setdefault("transform", "level")
    kw.setdefault("scale", 1.0)
    kw.setdefault("dezimal", 1)
    kw.setdefault("seed", [])
    kw.setdefault("auch_in", [])
    kw.setdefault("herkunft", "Recherche")
    kw.setdefault("kern", False)
    I.append(kw)

# --- Wachstum & Zyklus -------------------------------------------------------
_ind(id="bip", kapitel="wachstum", name="Reales BIP (zum Vorquartal, annualisiert)", einheit="%",
     quelle="fred", series="A191RL1Q225SBEA", richtung="tief", gelb=1.0, rot=0.0, herkunft="Vorschlag",
     schwelle="Gelb < 1,0 % · Rot < 0 %", kern=True,
     annahme="Q3 +3,4 % annualisiert; Lager +1,61 PP, Nettoexporte −3,32 PP. 2. Schätzung am 25.11.",
     aktion="Negatives Quartal → Risk-off-Szenario höher gewichten.",
     quelle1="BEA", quelle2="FRED A191RL1Q225SBEA", rhythmus="quartalsweise", naechster="25.11.2026",
     seed=[("2026-07-01", 3.4)])
_ind(id="outputgap", kapitel="wachstum", name="Output Gap", einheit="%",
     quelle="berechnet", series=["GDPC1", "GDPPOT"], richtung="kontext",
     schwelle="> 0 = Wirtschaft über Potenzial (Spätzyklus)",
     annahme="Schätzgrösse, kann revidiert werden.", aktion="Kontext für die Zyklus-Einordnung.",
     quelle1="FRED GDPC1/GDPPOT", quelle2="CBO", rhythmus="quartalsweise", naechster="25.11.2026",
     seed=[("2026-07-01", 1.7)])
_ind(id="indpro", kapitel="wachstum", name="Industrieproduktion (zum Vorjahr)", einheit="% y/y",
     quelle="fred", series="INDPRO", transform="yoy", richtung="tief", gelb=0.0, rot=-2.0, herkunft="Vorschlag",
     schwelle="Gelb < 0 % · Rot < −2 %",
     annahme="Industrieproduktion steigt (Wachstumsanalyse).", aktion="Kontext; stützt bzw. schwächt das Basisszenario.",
     quelle1="Federal Reserve G.17", quelle2="FRED INDPRO", rhythmus="monatlich", naechster="Mitte Monat")
_ind(id="ism_no", kapitel="wachstum", name="ISM Industrie – Neuaufträge", einheit="Index",
     quelle="manuell", richtung="tief", rot=52, kern=True,
     schwelle="Rot < 52 (zusammen mit Preisen > 75 = Stagflationssignal)",
     annahme="Unternehmen klar expansiv (10. Monat in Folge); Gegenpol zum schwächeren Konsum.",
     aktion="Konsumrückgang-Risiko höher gewichten; zyklischen Konsum und Small Caps meiden.",
     quelle1="ISM Report on Business", quelle2="Trading Economics / Action Forex", rhythmus="monatlich", naechster="01.12.2026",
     seed=[("2026-09-01", 55.3), ("2026-10-01", 55.5)])
_ind(id="ism_preise", kapitel="wachstum", name="ISM Industrie – Preise bezahlt", einheit="Index",
     quelle="manuell", richtung="hoch", gelb=70, rot=75,
     schwelle="Gelb ≥ 70 · Rot ≥ 75 (< 70 = Entlastung)",
     annahme="9. Monat über 70: hoher Kostendruck, konsumnahe Firmen können Preise kaum weitergeben.",
     aktion="Allein kein Handelssignal; mit Neuaufträgen < 52 → Stagflation, Zykliker reduzieren.",
     quelle1="ISM Report on Business", quelle2="Trading Economics / Action Forex", rhythmus="monatlich", naechster="01.12.2026",
     seed=[("2026-09-01", 77.9), ("2026-10-01", 75.2)], auch_in=["inflation"])

_ind(id="auslastung", kapitel="wachstum", name="Kapazitätsauslastung Industrie", einheit="%",
     quelle="fred", series="TCU", richtung="hoch", gelb=80.0, rot=82.0, herkunft="Vorschlag",
     schwelle="Gelb ≥ 80 % · Rot ≥ 82 % (Überhitzung)",
     annahme="≈ 76,9 % gemessen: Fabriken produzieren mehr, sind aber nicht überlastet (IC-Datenabgleich B).",
     aktion="Kontext für Zyklus und Inflationsdruck aus der Industrie.",
     quelle1="Federal Reserve G.17", quelle2="FRED TCU", rhythmus="monatlich", naechster="Mitte Monat",
     seed=[("2026-10-01", 76.9)])
_ind(id="wohnbau", kapitel="wachstum", name="Baubeginne Wohnungen (zum Vorjahr)", einheit="% y/y",
     quelle="fred", series="HOUST", transform="yoy", richtung="tief", gelb=0.0, rot=-10.0, herkunft="Vorschlag",
     schwelle="Gelb < 0 % · Rot < −10 %",
     annahme="Wohnungsbau schwach: hohe Zinsen bremsen neue Bauprojekte (IC-Beweiskette).",
     aktion="Weitere Schwäche stützt das Abkühlungs-/Bear-Szenario.",
     quelle1="Census Bureau", quelle2="FRED HOUST", rhythmus="monatlich", naechster="Mitte Monat")

# --- Arbeitsmarkt ------------------------------------------------------------
_ind(id="unrate", kapitel="arbeitsmarkt", name="Arbeitslosenquote", einheit="%",
     quelle="fred", series="UNRATE", richtung="hoch", rot=4.3, kern=True,
     schwelle="Rot ≥ 4,3 % (zusammen mit < 50'000 Stellen = Arbeitsmarkt kippt)",
     annahme="Seit Q4 2025 sinkend; stützt Einkommen und Konsum, spricht gegen Rezession in 3 Monaten.",
     aktion="Duration auf 4–5 Jahre verlängern, Anleihen-Übergewicht halten.",
     quelle1="BLS Employment Situation", quelle2="FRED UNRATE", rhythmus="monatlich", naechster="04.12.2026",
     seed=[("2026-10-01", 4.1)])
_ind(id="nfp", kapitel="arbeitsmarkt", name="Stellenaufbau (Nonfarm Payrolls)", einheit="Tsd.",
     quelle="fred", series="PAYEMS", transform="diff", dezimal=0, richtung="tief", rot=50, kern=True,
     schwelle="Rot < 50'000",
     annahme="Deutlich über Erwartung (≈ 60'000); Lohnwachstum 3,1 % → wenig lohnseitiger Inflationsdruck.",
     aktion="Mit Quote ≥ 4,3 %: Duration auf 4–5 Jahre verlängern.",
     quelle1="BLS Employment Situation", quelle2="FRED PAYEMS", rhythmus="monatlich", naechster="04.12.2026",
     seed=[("2026-10-01", 165)])
_ind(id="claims", kapitel="arbeitsmarkt", name="Erstanträge Arbeitslosenhilfe (wöchentlich)", einheit="Tsd.",
     quelle="fred", series="ICSA", scale=0.001, dezimal=0, richtung="hoch", gelb=260, rot=300, herkunft="Vorschlag",
     schwelle="Gelb ≥ 260'000 · Rot ≥ 300'000",
     annahme="Frühester Arbeitsmarkt-Indikator; ergänzt den monatlichen Bericht.",
     aktion="Anhaltend über 300'000 → Abkühlung, Duration Richtung 4–5 J. prüfen.",
     quelle1="U.S. Department of Labor", quelle2="FRED ICSA", rhythmus="wöchentlich (Do.)", naechster="laufend")
_ind(id="sahm", kapitel="arbeitsmarkt", name="Sahm-Regel (Echtzeit)", einheit="PP",
     quelle="fred", series="SAHMREALTIME", dezimal=2, richtung="hoch", gelb=0.3, rot=0.5, herkunft="Vorschlag",
     schwelle="Gelb ≥ 0,30 · Rot ≥ 0,50 (= Rezessionssignal)",
     annahme="Misst den Anstieg der Arbeitslosenquote gegenüber dem 12-Monats-Tief.",
     aktion="Rot → Rezessionsrisiko, Risk-off-Szenario höher gewichten.",
     quelle1="FRED SAHMREALTIME", quelle2="Claudia Sahm (2019)", rhythmus="monatlich", naechster="04.12.2026")
_ind(id="loehne", kapitel="arbeitsmarkt", name="Stundenlöhne (zum Vorjahr)", einheit="% y/y",
     quelle="fred", series="CES0500000003", transform="yoy", richtung="hoch", gelb=4.0, rot=4.5, herkunft="Vorschlag",
     schwelle="Gelb ≥ 4,0 % · Rot ≥ 4,5 %",
     annahme="Lohnwachstum 3,1 % → wenig lohnseitiger Inflationsdruck.",
     aktion="Über 4 % → Lohn-Preis-Spirale, Inflationsschock-Szenario höher gewichten.",
     quelle1="BLS", quelle2="FRED CES0500000003", rhythmus="monatlich", naechster="04.12.2026",
     seed=[("2026-10-01", 3.1)], auch_in=["inflation"])
_ind(id="jolts", kapitel="arbeitsmarkt", name="Offene Stellen (JOLTS)", einheit="Mio.",
     quelle="fred", series="JTSJOL", scale=0.001, dezimal=2, richtung="kontext",
     schwelle="Kontext: sinkend = Abkühlung",
     annahme="Nachfrage nach Arbeit; ergänzt Quote und Stellenaufbau.", aktion="Kontext.",
     quelle1="BLS JOLTS", quelle2="FRED JTSJOL", rhythmus="monatlich", naechster="laufend")

# --- Inflation & Fed ---------------------------------------------------------
_ind(id="kerncpi", kapitel="inflation", name="Kern-CPI (zum Vormonat)", einheit="% m/m",
     quelle="fred", series="CPILFESL", transform="mom", richtung="hoch", gelb=0.3, rot=0.4, kern=True,
     herkunft="Rot: Recherche · Gelb: Vorschlag", schwelle="Gelb ≥ 0,3 % · Rot ≥ 0,4 %",
     annahme="Über Erwartung (0,2 %), ≈ 3,6 % annualisiert; Wohnkosten treiben. Keine Entlastung bis 28.02.",
     aktion="Duration auf ≈ 2,5 Jahre senken, TIPS-Anteil auf 25 % erhöhen.",
     quelle1="BLS CPI", quelle2="FRED CPILFESL", rhythmus="monatlich", naechster="Mitte Dez. (BLS-Kalender)",
     seed=[("2026-10-01", 0.3)])
_ind(id="cpi", kapitel="inflation", name="CPI Gesamt (zum Vorjahr)", einheit="% y/y",
     quelle="fred", series="CPIAUCSL", transform="yoy", richtung="hoch", gelb=3.0, rot=4.0, herkunft="Vorschlag",
     schwelle="Gelb ≥ 3 % · Rot ≥ 4 %",
     annahme="Gesamtinflation inkl. Energie – reagiert direkt auf den Ölpreis.",
     aktion="Kontext zu Brent und Kern-CPI.",
     quelle1="BLS CPI", quelle2="FRED CPIAUCSL", rhythmus="monatlich", naechster="Mitte Dez.")
_ind(id="kernpce", kapitel="inflation", name="Kern-PCE (zum Vorjahr)", einheit="% y/y",
     quelle="fred", series="PCEPILFE", transform="yoy", richtung="kontext", kern=True,
     schwelle="Fed-Ziel 2 % · Basisszenario ≈ 3 %",
     annahme="Zielgrösse der Fed, +1,1 PP über Ziel; Nowcast Okt. ≈ 3,2 %. Fed erwartet 2 % erst 2029.",
     aktion="Deutlich über 3 % bestätigt das Inflationsschock-Szenario.",
     quelle1="BEA Personal Income & Outlays", quelle2="FRED PCEPILFE", rhythmus="monatlich", naechster="25.11.2026",
     seed=[("2026-08-01", 3.0), ("2026-09-01", 3.1)])
_ind(id="pce", kapitel="inflation", name="PCE-Inflation gesamt (zum Vorjahr)", einheit="% y/y",
     quelle="fred", series="PCEPI", transform="yoy", richtung="hoch", gelb=3.0, rot=3.5, herkunft="Vorschlag",
     schwelle="Gelb ≥ 3 % · Rot ≥ 3,5 % (Fed-Ziel 2 %)",
     annahme="Inflation 3,7 % gesamt, 3,1 % ohne Energie/Lebensmittel (IC-Beweiskette).",
     aktion="Mehrere schwächere Werte → Bull wahrscheinlicher; erneute Beschleunigung → Bear (IC-Monitoring).",
     quelle1="BEA Personal Income & Outlays", quelle2="FRED PCEPI", rhythmus="monatlich", naechster="25.11.2026",
     seed=[("2026-09-01", 3.7)])
_ind(id="infl_erw", kapitel="inflation", name="Inflationserwartung 5 J. in 5 J. (5y5y)", einheit="%",
     quelle="fred", series="T5YIFR", dezimal=2, richtung="hoch", gelb=2.5, rot=2.8, herkunft="Vorschlag",
     schwelle="Gelb ≥ 2,5 % · Rot ≥ 2,8 % (Erwartungen lösen sich vom Ziel)",
     annahme="Zeigt, ob der Markt der Fed langfristig glaubt.",
     aktion="Rot → Inflationsschock-Szenario höher gewichten, TIPS ↑.",
     quelle1="FRED T5YIFR", quelle2="Federal Reserve H.15", rhythmus="täglich", naechster="laufend")
_ind(id="fedfunds", kapitel="inflation", name="Fed-Funds-Zielband (Obergrenze)", einheit="%",
     quelle="fred", series="DFEDTARU", dezimal=2, richtung="kontext", kern=True,
     schwelle="Taylor-Regel ≈ 4,95–5,25 % · Markt: Dez.-Erhöhung ≈ 86–90 %",
     annahme="Unsere Wahrscheinlichkeit bis 28.02.: Erhöhung 85 % · Halten 14 % · Senkung 1 %.",
     aktion="Signalisiert die Fed am 9.12. weitere Erhöhungen → Duration Richtung 3 J.; Pause → 4–5 J.",
     quelle1="Federal Reserve", quelle2="CME FedWatch", rhythmus="Sitzungen", naechster="09.12.2026",
     seed=[("2026-11-12", 4.0)], auch_in=["zinsen"])

# --- Zinsen & Anleihen -------------------------------------------------------
_ind(id="dgs2", kapitel="zinsen", name="Rendite 2-jährige Treasury", einheit="%",
     quelle="fred", series="DGS2", dezimal=2, richtung="kontext",
     schwelle="Basis 4,8–5,1 % · Inflationsschock 5,1–5,4 % · Risk-off 4,3–4,6 %",
     annahme="Folgt der Fed-Erwartung.", aktion="Zeigt, welches Zins-Szenario der Markt spielt.",
     quelle1="U.S. Treasury", quelle2="FRED DGS2", rhythmus="täglich", naechster="laufend",
     seed=[("2026-11-12", 4.88)])
_ind(id="dgs10", kapitel="zinsen", name="Rendite 10-jährige Treasury", einheit="%",
     quelle="fred", series="DGS10", dezimal=2, richtung="hoch", gelb=5.0, rot=5.25, kern=True,
     schwelle="Gelb ≥ 5,0 % · Rot ≥ 5,25 % (> 5,5 % = Laufzeitprämie steigt)",
     annahme="Defizit ≈ 6 % des BIP und schwache Auktionen halten lange Zinsen hoch; Laufzeitprämie ≈ 0,7 % (tief).",
     aktion="Keine Laufzeiten > 10 J.; nur bei > 5,3 % UND Abkühlung Duration Richtung 5 J. verlängern.",
     quelle1="U.S. Treasury", quelle2="FRED DGS10", rhythmus="täglich", naechster="laufend",
     seed=[("2026-10-12", 4.86), ("2026-11-12", 5.10)])
_ind(id="kurve", kapitel="zinsen", name="Zinskurve 10 J. − 2 J.", einheit="bp",
     quelle="fred", series="T10Y2Y", scale=100, dezimal=0, richtung="kontext",
     schwelle="Basis: weitere leichte Verflachung",
     annahme="Bear Flattening eingetroffen (2 J. 4,88 %): lange Laufzeiten bringen kaum Mehrrendite.",
     aktion="Schwerpunkt 1–7 Jahre beibehalten.",
     quelle1="U.S. Treasury", quelle2="FRED T10Y2Y", rhythmus="täglich", naechster="laufend",
     seed=[("2026-10-12", 35), ("2026-11-12", 22)])
_ind(id="kurve3m", kapitel="zinsen", name="Zinskurve 10 J. − 3 Mt.", einheit="bp",
     quelle="fred", series="T10Y3M", scale=100, dezimal=0, richtung="tief", rot=0, herkunft="Vorschlag",
     schwelle="Rot < 0 (Inversion = klassisches Rezessionssignal der Fed)",
     annahme="Bevorzugtes Rezessionsmass der New York Fed.",
     aktion="Inversion → Risk-off-Szenario höher gewichten.",
     quelle1="FRED T10Y3M", quelle2="NY Fed Yield Curve Model", rhythmus="täglich", naechster="laufend")
_ind(id="realzins", kapitel="zinsen", name="Realzins 10 J. (TIPS)", einheit="%",
     quelle="fred", series="DFII10", dezimal=2, richtung="hoch", gelb=2.5, rot=3.0, herkunft="Vorschlag",
     schwelle="Gelb ≥ 2,5 % · Rot ≥ 3,0 %",
     annahme="Hohe Realzinsen belasten Bewertungen (v. a. Wachstumswerte).",
     aktion="Rot → KGV-Druck, Aktienrisikoprämie prüfen.",
     quelle1="U.S. Treasury", quelle2="FRED DFII10", rhythmus="täglich", naechster="laufend", auch_in=["gewinne"])
_ind(id="laufzeitpraemie", kapitel="zinsen", name="Laufzeitprämie 10 J. (Kim-Wright)", einheit="%",
     quelle="fred", series="THREEFYTP10", dezimal=2, richtung="kontext",
     schwelle="Kontext: ≈ 0,7 % (tief); steigt bei 10 J. > 5,5 %",
     annahme="Laufzeitprämie ≈ 0,7 % (tief) – Risiko, dass sie bei schwachen Auktionen steigt.",
     aktion="Steigt sie deutlich → keine Laufzeiten > 10 J. (bereits Regel).",
     quelle1="Federal Reserve Board", quelle2="FRED THREEFYTP10", rhythmus="täglich", naechster="laufend")

# --- Kredit & Finanzbedingungen ---------------------------------------------
_ind(id="bb", kapitel="kredit", name="Credit Spread BB (Hochzins)", einheit="bp",
     quelle="fred", series="BAMLH0A1HYBB", scale=100, dezimal=0, richtung="hoch", rot=300, kern=True,
     schwelle="Rot > 300 bp",
     annahme="9. Perzentil, sehr eng; Stress nur unten: CCC 1'307 bp (88. Perzentil).",
     aktion="Aktien −10 PP, Duration Richtung 4–5 Jahre.",
     quelle1="ICE BofA (via FRED BAMLH0A1HYBB)", quelle2="Tagesbriefings", rhythmus="täglich", naechster="laufend",
     seed=[("2026-11-12", 184)])
_ind(id="bbb", kapitel="kredit", name="Credit Spread BBB", einheit="bp",
     quelle="fred", series="BAMLC0A4CBBB", scale=100, dezimal=0, richtung="hoch", rot=150,
     schwelle="Rot > 150 bp",
     annahme="8. Perzentil (20 J.): kein Stress, aber wenig Entschädigung.",
     aktion="Aktien −10 PP, Duration Richtung 4–5 Jahre.",
     quelle1="ICE BofA (via FRED BAMLC0A4CBBB)", quelle2="Tagesbriefings", rhythmus="täglich", naechster="laufend",
     seed=[("2026-11-12", 107)])
_ind(id="ccc", kapitel="kredit", name="Credit Spread CCC", einheit="bp",
     quelle="fred", series="BAMLH0A3HYC", scale=100, dezimal=0, richtung="hoch", gelb=1100, rot=1400, herkunft="Vorschlag",
     schwelle="Gelb ≥ 1'100 bp · Rot ≥ 1'400 bp",
     annahme="CCC 1'307 bp (88. Perzentil): Stress im schwächsten Segment.",
     aktion="Greift der Stress auf BB/BBB über → Risk-off-Szenario.",
     quelle1="ICE BofA (via FRED BAMLH0A3HYC)", quelle2="Tagesbriefings", rhythmus="täglich", naechster="laufend",
     seed=[("2026-11-12", 1307)])
_ind(id="nfci", kapitel="kredit", name="Chicago Fed NFCI", einheit="Index",
     quelle="fred", series="NFCI", dezimal=3, richtung="hoch", rot=0, kern=True,
     schwelle="Rot > 0 (straffer als Durchschnitt); grosser Sprung in wenigen Wochen = Warnsignal",
     annahme="Bedingungen weiterhin locker, aber leicht gestrafft.",
     aktion="Über 0: Stressphase → Risk-off-Szenario höher gewichten.",
     quelle1="Chicago Fed", quelle2="FRED NFCI", rhythmus="wöchentlich (Mi.)", naechster="laufend",
     seed=[("2026-10-23", -0.498), ("2026-10-30", -0.459)])
_ind(id="stlfsi", kapitel="kredit", name="St. Louis Fed Finanzstress-Index", einheit="Index",
     quelle="fred", series="STLFSI4", dezimal=2, richtung="hoch", rot=0, herkunft="Vorschlag",
     schwelle="Rot > 0 (Stress über Durchschnitt)",
     annahme="Zweite Messung der Finanzbedingungen, bestätigt bzw. relativiert den NFCI.",
     aktion="Wie NFCI: über 0 → Risk-off höher gewichten.",
     quelle1="St. Louis Fed", quelle2="FRED STLFSI4", rhythmus="wöchentlich", naechster="laufend")
_ind(id="sloos", kapitel="kredit", name="Banken: Verschärfung Kreditstandards (SLOOS)", einheit="% netto",
     quelle="fred", series="DRTSCILM", dezimal=1, richtung="hoch", gelb=10, rot=20, herkunft="Vorschlag",
     schwelle="Gelb ≥ 10 % · Rot ≥ 20 % der Banken verschärfen",
     annahme="Fed-Bankenumfrage (Anfang Feb. 2027) zur Kreditqualität.",
     aktion="Rot → Kreditklemme, Risk-off höher gewichten.",
     quelle1="Federal Reserve SLOOS", quelle2="FRED DRTSCILM", rhythmus="quartalsweise", naechster="Anfang Feb. 2027")
_ind(id="brent", kapitel="kredit", name="Ölpreis Brent", einheit="USD/bbl",
     quelle="fred", series="DCOILBRENTEU", dezimal=2, richtung="hoch", gelb=85, rot=100, kern=True,
     schwelle="Gelb ≥ 85 · Rot ≥ 100 · ab 110 Zykliker reduzieren · < 85 Entspannung",
     annahme="Iran-Krieg/Hormus: Basis 85–100 USD; Risiko in beide Richtungen, +52 % zum Vorjahr.",
     aktion="≥ 100: Duration 2,5 J., TIPS 25 %. ≥ 110: Zykliker reduzieren, Cash ↑. < 85: Energie-Übergewicht abbauen.",
     quelle1="ICE / CNBC (FRED DCOILBRENTEU, ≈ 1 Woche Verzug)", quelle2="Trading Economics / OilPrice.com",
     rhythmus="täglich", naechster="laufend", seed=[("2026-11-12", 95.82)], auch_in=["geopolitik", "inflation"])
_ind(id="dxy", kapitel="kredit", name="US-Dollar-Index (DXY)", einheit="Index",
     quelle="markt", series="DX-Y.NYB", richtung="hoch", rot=103,
     schwelle="Rot anhaltend > 103",
     annahme="Fed erhöht, andere Notenbanken halten → Dollar stark seit Anfang Nov.",
     aktion="Multinationale mit hohem Auslandsumsatz untergewichten, binnenorientierte Firmen bevorzugen.",
     quelle1="ICE (via Yahoo Finance DX-Y.NYB, automatisch)", quelle2="Trading Economics / Reuters", rhythmus="täglich", naechster="laufend",
     seed=[("2026-10-11", 101.4), ("2026-11-11", 102.5)])
_ind(id="dollar_breit", kapitel="kredit", name="Handelsgewichteter Dollar (breit, Fed)", einheit="Index",
     quelle="fred", series="DTWEXBGS", dezimal=1, richtung="kontext",
     schwelle="Kontext zum DXY (DXY selbst ist nicht auf FRED)",
     annahme="Breiter Dollar-Index der Fed, automatisch aktualisiert.", aktion="Kontext.",
     quelle1="Federal Reserve H.10", quelle2="FRED DTWEXBGS", rhythmus="täglich", naechster="laufend")

# --- Konsum & Stimmung -------------------------------------------------------
_ind(id="sparquote", kapitel="konsum", name="Sparquote", einheit="%",
     quelle="fred", series="PSAVERT", richtung="tief", gelb=4.0, rot=3.5, kern=True,
     herkunft="Rot: Recherche · Gelb: Vorschlag", schwelle="Gelb < 4,0 % · Rot < 3,5 %",
     annahme="Tiefster Stand seit Okt. 2022; Realkonsum stagniert, Realeinkommen −0,3 %: Konsum aus Ersparnis.",
     aktion="Zyklischen Konsum untergewichten; Basiskonsum und Gesundheit relativ bevorzugen.",
     quelle1="BEA Personal Income & Outlays", quelle2="FRED PSAVERT", rhythmus="monatlich", naechster="25.11.2026",
     seed=[("2026-08-01", 4.0), ("2026-09-01", 3.8)])
_ind(id="realkonsum", kapitel="konsum", name="Realer Konsum (zum Vormonat)", einheit="% m/m",
     quelle="fred", series="PCEC96", transform="mom", dezimal=2, richtung="tief", gelb=0.0, herkunft="Recherche",
     schwelle="Gelb ≤ 0 % · Warnsignal: 2 Monate in Folge ≤ 0",
     annahme="Realkonsum stagniert.", aktion="2 Monate ≤ 0 → Warnsignal Konsumrückgang.",
     quelle1="BEA", quelle2="FRED PCEC96", rhythmus="monatlich", naechster="25.11.2026", zwei_monate=True)
_ind(id="detailhandel", kapitel="konsum", name="Detailhandelsumsatz (zum Vorjahr, nominal)", einheit="% y/y",
     quelle="fred", series="RSAFS", transform="yoy", richtung="tief", gelb=2.0, rot=0.0, herkunft="Vorschlag",
     schwelle="Gelb < 2 % · Rot < 0 %",
     annahme="Detailhandel Dez. ab Mitte Jan. 2027 (Weihnachtsgeschäft).", aktion="Kontext Konsum.",
     quelle1="Census Bureau", quelle2="FRED RSAFS", rhythmus="monatlich", naechster="Mitte Monat")
_ind(id="cb_erw", kapitel="konsum", name="Conference Board – Erwartungen", einheit="Index",
     quelle="manuell", richtung="tief", rot=80, kern=True,
     schwelle="Rot < 80 (Rezessions-Warnschwelle)",
     annahme="2026 zum 7. Mal unter 80; Treiber sind Preise, nicht Jobangst (Michigan Nov. 51,0) → erholt sich bei tieferem Öl.",
     aktion="Zusammen mit Sparquote und Realkonsum lesen; allein kein Handelssignal.",
     quelle1="The Conference Board", quelle2="TheStreet / Verified Investing", rhythmus="monatlich", naechster="Ende Nov.",
     seed=[("2026-10-01", 72.6)])
_ind(id="michigan", kapitel="konsum", name="Uni Michigan Konsumentenstimmung", einheit="Index",
     quelle="fred", series="UMCSENT", richtung="kontext",
     schwelle="Kontext (Nov. 51,0)",
     annahme="Schwach wegen Preisen, nicht wegen Jobangst.", aktion="Kontext zu Conference Board.",
     quelle1="University of Michigan", quelle2="FRED UMCSENT (FRED mit Verzug)", rhythmus="monatlich", naechster="laufend",
     seed=[("2026-11-01", 51.0)])
_ind(id="nfib", kapitel="konsum", name="NFIB Optimismus (Kleinunternehmen)", einheit="Index",
     quelle="manuell", richtung="tief", gelb=98.0, rot=95,
     schwelle="Gelb < 98,0 (52-J.-Schnitt) · Rot < 95",
     annahme="Neutral, aber Unsicherheitsindex 90 (Schnitt 68); Probleme: Personal und Kosten, nicht Nachfrage.",
     aktion="Unter 95: Konsumrückgang-Risiko höher gewichten.",
     quelle1="NFIB Small Business Economic Trends", quelle2="JM Financial", rhythmus="monatlich (2. Dienstag)", naechster="Okt.-Wert prüfen",
     seed=[("2026-09-01", 97.9)])
_ind(id="kreditkarten", kapitel="konsum", name="Ausfallrate Kreditkarten (Banken)", einheit="%",
     quelle="fred", series="DRCCLACBS", dezimal=2, richtung="kontext",
     schwelle="Kontext: steigend = Konsumenten unter Druck",
     annahme="Ergänzt Sparquote: wie stark finanzieren Haushalte Konsum über Schulden.", aktion="Kontext.",
     quelle1="Federal Reserve", quelle2="FRED DRCCLACBS", rhythmus="quartalsweise", naechster="laufend")

# --- Gewinne & Bewertung -----------------------------------------------------
_ind(id="sp500_dd", kapitel="gewinne", name="S&P 500: Abstand zum Hoch", einheit="%",
     quelle="fred", series="SP500", transform="drawdown", richtung="tief", gelb=-10, rot=-20, herkunft="Vorschlag",
     schwelle="Gelb ≤ −10 % (Korrektur) · Rot ≤ −20 % (Bärenmarkt)",
     annahme="Zeigt, wie weit der Markt bereits korrigiert hat.", aktion="Kontext für Aktienquote und KI-Risiko.",
     quelle1="S&P Dow Jones Indices", quelle2="FRED SP500", rhythmus="täglich", naechster="laufend")
_ind(id="sp500", kapitel="gewinne", name="S&P 500 (Stand)", einheit="Punkte",
     quelle="fred", series="SP500", dezimal=0, richtung="kontext",
     schwelle="Für die Aktienrisikoprämie verwendet",
     annahme="Indexstand 7'592,40 (12.11.2026).", aktion="Kontext.",
     quelle1="S&P Dow Jones Indices", quelle2="FRED SP500", rhythmus="täglich", naechster="laufend",
     seed=[("2026-11-12", 7592.40)])
_ind(id="fwd_eps", kapitel="gewinne", name="Forward-EPS S&P 500 (12 Mt.)", einheit="USD",
     quelle="manuell", dezimal=2, richtung="kontext",
     schwelle="Eingabe für die Aktienrisikoprämie",
     annahme="EPS 2027 +16,1 % erwartet; Revisionen der Q4- und 2027-Schätzungen beobachten.",
     aktion="Sinkende Schätzungen → Negativ-Szenario höher gewichten.",
     quelle1="FactSet Earnings Insight", quelle2="S&P Dow Jones Indices", rhythmus="wöchentlich", naechster="laufend",
     seed=[("2026-11-12", 426.30)])
_ind(id="erp", kapitel="gewinne", name="Aktienrisikoprämie (Forward)", einheit="PP",
     quelle="berechnet", series=["fwd_eps", "sp500", "dgs10"], dezimal=2, richtung="tief", gelb=1.0, rot=0.4, kern=True,
     schwelle="Rot < 0,4 PP (KGV-Druck) · Gelb < 1,0 PP (> 1 PP = Anleihen zurück auf neutral)",
     annahme="Gewinnrendite (Forward-EPS ÷ S&P 500) − 10 J.: Aktien kaum besser entschädigt als Treasuries.",
     aktion="Anleihen leicht übergewichten; bei > 1 PP Übergewicht auf neutral zurückführen.",
     quelle1="FactSet Earnings Insight", quelle2="U.S. Treasury / FRED", rhythmus="täglich (automatisch)", naechster="laufend",
     seed=[("2026-10-12", 0.75), ("2026-11-12", 0.51)])
_ind(id="eps_wachstum", kapitel="gewinne", name="Gewinnwachstum S&P 500 (Quartal, zum Vorjahr)", einheit="% y/y",
     quelle="manuell", richtung="tief", gelb=5.0, rot=0.0, herkunft="Vorschlag",
     schwelle="Gelb < 5 % · Rot < 0 %",
     annahme="+31,4 % zum Vorjahr: viele Firmen verdienen deutlich mehr als vor einem Jahr (IC-Beweiskette).",
     aktion="Sinkende 2027er Schätzungen schwächen die Aktien-These (IC-Monitoring).",
     quelle1="FactSet Earnings Insight", quelle2="S&P Dow Jones Indices", rhythmus="wöchentlich (Berichtssaison)", naechster="Q4-Saison ab Mitte Jan.",
     seed=[("2026-11-06", 31.4)])
_ind(id="kgv", kapitel="gewinne", name="Forward-KGV S&P 500", einheit="x",
     quelle="manuell", richtung="kontext",
     schwelle="Basis 17–18 · positiv Richtung 19",
     annahme="Unter 10-J.-Schnitt (19,1); PEG ≈ 1,1, fair sofern EPS 2027 (+16,1 %) halten.",
     aktion="Revisionen der Q4- und 2027-Schätzungen beobachten (Q4-Saison ab Mitte Jan.).",
     quelle1="FactSet Earnings Insight", quelle2="S&P Dow Jones Indices", rhythmus="wöchentlich", naechster="laufend",
     seed=[("2026-11-05", 17.7), ("2026-11-12", 17.8)])
_ind(id="vix", kapitel="gewinne", name="Volatilität VIX", einheit="Index",
     quelle="fred", series="VIXCLS", richtung="hoch", gelb=20, rot=30, herkunft="Vorschlag",
     schwelle="Gelb ≥ 20 · Rot ≥ 30",
     annahme="Angstbarometer; tiefer VIX bei einseitiger Positionierung = Sorglosigkeit.",
     aktion="Rot → Risk-off läuft, keine Panikverkäufe, Cash-Puffer nutzen.",
     quelle1="Cboe", quelle2="FRED VIXCLS", rhythmus="täglich", naechster="laufend")
_ind(id="fms_cash", kapitel="gewinne", name="BofA Fondsmanager – Cash-Quote", einheit="%",
     quelle="manuell", richtung="tief", rot=4.0,
     schwelle="Rot < 4 % (Verkaufssignal)",
     annahme="Profis voll investiert, Tech-Übergewicht netto 37 %, 45 % erwarten KI-Blase: einseitige Positionierung.",
     aktion="KI-/Halbleiter-Konzentration begrenzen; Tech-Übergewicht > 37 % und KI-Blase > 45 % zusätzlich prüfen.",
     quelle1="BofA Global Fund Manager Survey", quelle2="CryptoBriefing", rhythmus="monatlich (Mitte Monat)", naechster="Mitte Nov.",
     seed=[("2026-10-01", 3.9)])

# --- Geopolitik & Extremrisiken ---------------------------------------------
_ind(id="epu", kapitel="geopolitik", name="Wirtschaftspolitische Unsicherheit (EPU, 30-Tage-Schnitt)", einheit="Index",
     quelle="fred", series="USEPUINDXD", transform="avg30", dezimal=0, richtung="kontext",
     schwelle="Kontext: Langfrist-Schnitt ≈ 100",
     annahme="Misst Unsicherheit durch Politik (Zölle, Fristen, Sanktionen) in US-Zeitungen.",
     aktion="Starker Anstieg vor dem 10.01. → USA–China-Eskalation höher gewichten.",
     quelle1="Baker, Bloom & Davis", quelle2="FRED USEPUINDXD", rhythmus="täglich", naechster="laufend")

INDIKATOREN = I
IND_BY_ID = {i["id"]: i for i in I}

# Rezessionsbalken in Charts
REZESSION_SERIE = "USREC"

# Live-Kurse (Yahoo Finance) ergänzen FRED-Reihen, die dort mit Verzug erscheinen: Yahoo-Ticker → FRED-Reihe
MARKT_ERGAENZT_FRED = {"BZ=F": "DCOILBRENTEU", "^VIX": "VIXCLS", "^GSPC": "SP500", "^TNX": "DGS10"}

# Zinskurve für die Bewertung von Treasuries und die Markterwartungen
ZINSKURVE = {"DGS1MO": 1 / 12, "DGS3MO": 0.25, "DGS6MO": 0.5, "DGS1": 1, "DGS2": 2, "DGS3": 3, "DGS5": 5, "DGS7": 7,
             "DGS10": 10, "DGS20": 20, "DGS30": 30}
ERWARTUNGS_SERIEN = ["DFF", "T5YIE", "T10YIE"]

# ---------------------------------------------------------------------------
# Sektoren (S&P-500-Sektoren, gemessen über die SPDR-Sektor-ETFs)
# faktor: +1 = zyklisch (Risk-on), −1 = defensiv (Risk-off) – für die Simulation der Sektor-Fragen
# ---------------------------------------------------------------------------
SEKTOREN = ["Energie", "Grundstoffe", "Industrie", "Zykl. Konsum", "Basiskonsum", "Gesundheit",
            "Finanzen", "IT", "Kommunikation", "Versorger", "Immobilien"]
SEKTOR_DOZENT = {  # Bezeichnung in der Dozenten-App
    "Energie": "Energie", "Grundstoffe": "Roh-, Hilfs- und Betriebsstoffe", "Industrie": "Industrie",
    "Zykl. Konsum": "Zyklischer Konsum", "Basiskonsum": "Basiskonsumgüter", "Gesundheit": "Gesundheit",
    "Finanzen": "Finanzen", "IT": "Informationstechnologie", "Kommunikation": "Kommunikationsdienste",
    "Versorger": "Versorger", "Immobilien": "Immobilien",
}
SEKTOR_ETF = {"Energie": "XLE", "Grundstoffe": "XLB", "Industrie": "XLI", "Zykl. Konsum": "XLY", "Basiskonsum": "XLP",
              "Gesundheit": "XLV", "Finanzen": "XLF", "IT": "XLK", "Kommunikation": "XLC", "Versorger": "XLU",
              "Immobilien": "XLRE"}
SEKTOR_FAKTOR = {"Energie": 0.5, "Grundstoffe": 0.8, "Industrie": 1.0, "Zykl. Konsum": 1.0, "Basiskonsum": -1.0,
                 "Gesundheit": -0.8, "Finanzen": 0.9, "IT": 1.0, "Kommunikation": 0.6, "Versorger": -1.0,
                 "Immobilien": -0.2}
DEFENSIV = ["Basiskonsum", "Gesundheit", "Versorger"]                 # Dozenten-Frage 14
ZYKLISCH = ["Zykl. Konsum", "Industrie", "Finanzen", "IT"]

# ---------------------------------------------------------------------------
# Szenarien: Base / Bull / Bear (gemäss Investment Committee 16.11.2026)
#
# Jedes Szenario hat konkrete Parameter für den laufenden Monat. Daraus rechnet die App:
#   gewichteter Wert = Σ Wahrscheinlichkeit × Szenariowert
#   Wahrscheinlichkeit je Antwort der Dozentenfragen (Simulation mit der historischen Streuung)
#   Orientierung für Allokation und Anleihen
# Alles hier sind STARTWERTE. Das Team ändert sie in der App (Seite «Szenarien»), gespeichert wird in der Google-Tabelle.
# Herkunft der Zahlen: Richtung aus IC-Statement und Themen-Szenarien eurer Analyse, Höhe = Vorschlag Claude.
# ---------------------------------------------------------------------------
# Sektorwirkung = Rendite gegenüber dem S&P 500 (PP). Mit S&P-Gewichten gewichtet sollte sie sich ungefähr aufheben
# (IT ist ≈ 38 % des Index – eine IT-Wette verschiebt sonst den ganzen Index).
# Parameter je Szenario. sigma = typische Streuung in einem Monat (Fallback, die App rechnet sie aus den Daten nach).
# fragen = Nummern der Dozentenfragen, die davon abhängen.
PARAMETER = [
    dict(id="sp500", name="S&P 500 Total Return", einheit="%", dez=1, sigma=4.3, fragen=[1, 2],
         hilfe="Rendite des S&P 500 inkl. Dividenden im Monat"),
    dict(id="fed", name="Fed: Obergrenze Zielband", einheit="bp", dez=0, sigma=None, fragen=[4],
         hilfe="Veränderung an der Fed-Sitzung im Monat (−25 = Senkung, 0 = unverändert, +25 = Erhöhung)"),
    dict(id="dgs2", name="Rendite 2 J.", einheit="bp", dez=0, sigma=22, fragen=[],
         hilfe="Veränderung der 2-jährigen Treasury-Rendite im Monat (für die Zinskurve im Anleihen-Tool)"),
    dict(id="dgs10", name="Rendite 10 J.", einheit="bp", dez=0, sigma=24, fragen=[2, 5],
         hilfe="Veränderung der 10-jährigen Treasury-Rendite im Monat"),
    dict(id="cpi_mm", name="CPI gesamt im Berichtsmonat", einheit="% m/m", dez=2, sigma=0.12, fragen=[7],
         hilfe="Monatsveränderung des CPI, der im Monat veröffentlicht wird. Die App rechnet daraus mit dem Basiseffekt die neue Jahresrate."),
    dict(id="wti", name="Ölpreis WTI", einheit="%", dez=1, sigma=8.5, fragen=[10],
         hilfe="Veränderung des WTI-Preises (CL=F) im Monat"),
    dict(id="vix", name="VIX", einheit="Pkt.", dez=1, sigma=4.0, fragen=[3],
         hilfe="Veränderung des VIX vom Monatsanfang bis Monatsende"),
    dict(id="growth_value", name="Growth minus Value", einheit="PP", dez=1, sigma=2.6, fragen=[13],
         hilfe="Rendite IWF minus Rendite IWD im Monat"),
    dict(id="arbeitslos", name="Arbeitslosenquote", einheit="PP", dez=1, sigma=0.12, fragen=[],
         hilfe="Veränderung der Arbeitslosenquote im Arbeitsmarktbericht des Monats"),
    dict(id="dxy", name="Dollar-Index (DXY)", einheit="%", dez=1, sigma=1.8, fragen=[],
         hilfe="Veränderung des Dollar-Index im Monat"),
]
PARAM_BY_ID = {p["id"]: p for p in PARAMETER}
SEKTOR_SIGMA = 2.6       # Streuung eines Sektors gegenüber dem S&P 500 in einem Monat (PP), ohne Risk-on/off-Faktor
FAKTOR_SIGMA = 1.6       # Streuung des gemeinsamen Risk-on/off-Faktors (PP)
STREUUNG_IM_SZENARIO = 0.75  # Anteil der historischen Streuung, der innerhalb eines Szenarios bleibt

# Orientierung für die Allokation je Szenario
ALLOK_PARAMETER = [
    dict(id="aktien_tilt", name="Aktienquote vs. neutral", einheit="PP", dez=0),
    dict(id="duration", name="Duration Anleihen", einheit="J.", dez=1),
    dict(id="tips", name="TIPS-Anteil an Anleihen", einheit="%", dez=0),
]

SZENARIEN = [
    dict(id="ic_base", kurz="Base", name="Base: Späte Expansion, erhöhte Inflation", p=55,
         narrativ="Das Wachstum ist schwächer als im Q3, der Arbeitsmarkt bleibt stabil. Realer Konsum und Wohnbau bremsen. "
                  "Die Inflation sinkt höchstens langsam, die Fed bleibt restriktiv.",
         annahmen="Wachstum schwächer als Q3, Arbeitsmarkt stabil, realer Konsum und Wohnbau bremsen; Inflation sinkt höchstens langsam; "
                  "Iran: weder Frieden noch Krieg (Brent 85–100 USD); USA–China: kurze Verlängerung",
         folge="Fed bleibt restriktiv, Renditen hoch, leichtes Bear Flattening; Gewinne stützen Aktien, Aufwärtspotenzial durch "
               "Finanzierungskosten und enge Risikoprämie begrenzt",
         aktion="Aktien im Korridor 40–50 %, Treasuries 45–55 %, Duration um 3,5 J., Schwerpunkt 1–7 J.",
         param=dict(sp500=0.8, fed=25, dgs2=10, dgs10=5, cpi_mm=0.30, wti=0.0, vix=0.5, growth_value=0.5, arbeitslos=0.0,
                    dxy=0.3),
         alloc=dict(aktien_tilt=0, duration=3.5, tips=15),
         sektoren={"IT": 0.5, "Energie": 0.5, "Zykl. Konsum": -0.5, "Basiskonsum": -0.5, "Industrie": -0.3,
                   "Grundstoffe": -0.3},
         indizien=[
             dict(ind="unrate", op="<", wert=4.3, text="Arbeitsmarkt stabil"),
             dict(ind="kerncpi", op="zwischen", wert="0.2;0.4", text="Kern-CPI 0,2–0,4 % m/m"),
             dict(ind="dgs10", op="zwischen", wert="4.7;5.3", text="10 J. 4,7–5,3 %"),
             dict(ind="brent", op="zwischen", wert="80;100", text="Brent 80–100 USD"),
             dict(ind="bb", op="<", wert=300, text="Keine Kreditkrise (BB < 300 bp)"),
             dict(ind="ism_no", op=">", wert=50, text="Industrie expansiv (ISM-Neuaufträge > 50)"),
         ]),
    dict(id="ic_bull", kurz="Bull", name="Bull: Disinflation ohne Rezession", p=25,
         narrativ="Energie und Handel entspannen sich, Löhne und Inflation kühlen ab. Beschäftigung und Gewinne bleiben robust. "
                  "Die Fed strafft weniger, die Renditen sinken.",
         annahmen="Entspannung bei Energie und Handel (Iran-Deal oder China-Deal), Löhne und Inflation kühlen ab, "
                  "Beschäftigung und Gewinne robust",
         folge="Fed strafft weniger, Renditen sinken; Aktien profitieren von Gewinnen und Bewertung, Treasuries mit Kursgewinnen",
         aktion="Aktienquote Richtung oberer Korridor, Duration eher länger, Zykliker und IT übergewichten",
         param=dict(sp500=3.0, fed=0, dgs2=-20, dgs10=-25, cpi_mm=0.15, wti=-8.0, vix=-2.5, growth_value=1.5,
                    arbeitslos=0.0, dxy=-1.0),
         alloc=dict(aktien_tilt=5, duration=4.5, tips=10),
         sektoren={"IT": 0.5, "Zykl. Konsum": 1.0, "Industrie": 0.8, "Finanzen": 0.5, "Kommunikation": 0.5,
                   "Immobilien": 0.5, "Energie": -1.5, "Basiskonsum": -1.0, "Versorger": -0.8, "Gesundheit": -1.0},
         indizien=[
             dict(ind="kerncpi", op="<", wert=0.25, text="Kern-CPI < 0,25 % m/m"),
             dict(ind="brent", op="<", wert=85, text="Brent < 85 USD"),
             dict(ind="dgs10", op="<", wert=4.8, text="10 J. < 4,8 %"),
             dict(ind="nfp", op=">", wert=100, text="Stellenaufbau > 100'000"),
             dict(ind="vix", op="<", wert=16, text="VIX < 16"),
             dict(ind="ism_preise", op="<", wert=70, text="ISM-Preise < 70 (Kostendruck lässt nach)"),
         ]),
    dict(id="ic_bear", kurz="Bear", name="Bear: Inflations-/Zinsschock, Gewinnenttäuschung", p=20,
         narrativ="Energie-, Zoll- oder Lieferkettenschocks halten die Inflation hoch, die Fed wird restriktiver. "
                  "Höhere Renditen belasten Bewertungen und lange Treasuries, schwacher Konsum trifft die Umsätze.",
         annahmen="Energie-, Zoll- oder Lieferkettenschocks (Iran-Eskalation, China-Eskalation) halten Inflation hoch, "
                  "Fed restriktiver, Gewinnschätzungen sinken",
         folge="Höhere Renditen belasten Bewertungen und lange Treasuries; schwacher Konsum trifft Umsätze; Aktienverluste "
               "überproportional; Aktien und Anleihen fallen gleichzeitig",
         aktion="Aktienquote am unteren Korridor, kurze Duration, TIPS-Anteil hoch, Energie und Defensive übergewichten",
         param=dict(sp500=-5.0, fed=50, dgs2=40, dgs10=35, cpi_mm=0.50, wti=15.0, vix=7.0, growth_value=-2.5,
                    arbeitslos=0.1, dxy=1.5),
         alloc=dict(aktien_tilt=-10, duration=2.5, tips=25),
         sektoren={"Energie": 3.0, "Basiskonsum": 1.5, "Gesundheit": 1.5, "Versorger": 1.0, "Grundstoffe": 0.5,
                   "IT": -0.7, "Zykl. Konsum": -1.5, "Industrie": -0.5, "Finanzen": -0.5, "Kommunikation": -0.5,
                   "Immobilien": -0.5},
         indizien=[
             dict(ind="kerncpi", op=">", wert=0.39, text="Kern-CPI ≥ 0,4 % m/m"),
             dict(ind="brent", op=">", wert=100, text="Brent > 100 USD"),
             dict(ind="dgs10", op=">", wert=5.25, text="10 J. > 5,25 %"),
             dict(ind="bb", op=">", wert=300, text="Kreditstress (BB > 300 bp)"),
             dict(ind="vix", op=">", wert=25, text="VIX > 25"),
             dict(ind="sahm", op=">", wert=0.49, text="Sahm-Regel ≥ 0,5"),
         ]),
]
SZEN_IDS = [s["id"] for s in SZENARIEN]
SZEN_BY_ID = {s["id"]: s for s in SZENARIEN}
SZEN_FARBE = {"ic_base": "#1F4E8C", "ic_bull": "#2F855A", "ic_bear": "#B93A32"}
INDIZ_OPS = [">", "<", "zwischen"]

# ---------------------------------------------------------------------------
# Risiken und Chancen (Wahrscheinlichkeit in % bis 28.02.2027, Auswirkung 1–5)
# auto: Schlüssel eines automatischen Warnsignals (siehe logic.auto_warnsignale)
# richtung: auf welches Szenario das Risiko hinarbeitet (ein ausgelöstes Warnsignal zählt dort als Indiz)
# Auswirkung ist ein Startwert (Vorschlag) – das Team bewertet selbst.
# ---------------------------------------------------------------------------
RISIKEN = [
    dict(id="r_ki", name="KI-/Tech-Korrektur", kapitel="gewinne", p=50, p_text="40–60 %", impact=4,
         warnsignal="Fondsmanager-Cash < 4 %, Tech-Übergewicht > 37 %, KI-Blasen-Erwartung > 45 %, Gesetzesentwürfe zu Rechenzentren",
         reaktion="KI-/Halbleiter-Konzentration begrenzen", auto="fms_cash", richtung="ic_bear", art="Risiko",
         quelle="Konsumenten, Finanzbedingungen, Stimmung, Politik"),
    dict(id="r_konsum", name="Konsumrückgang", kapitel="konsum", p=35, p_text="30–40 %", impact=3,
         warnsignal="Sparquote < 3,5 %, Realkonsum 2 Monate ≤ 0, ISM-Neuaufträge < 52 bei Preisen > 75, NFIB < 95",
         reaktion="Zyklischen Konsum und Small Caps untergewichten; Basiskonsum, Gesundheit relativ besser", auto="konsum", richtung="ic_bear", art="Risiko",
         quelle="Konsumenten, Finanzbedingungen, Stimmung, Politik"),
    dict(id="r_cyber", name="Cyber-Eskalation", kapitel="geopolitik", p=30, p_text="30 % (von 25 % angehoben)", impact=3,
         warnsignal="CISA-Notfallwarnungen, Ausfall von Strom, Wasser, Zahlungsverkehr oder Börse",
         reaktion="Cybersecurity-Titel halten, Versorger breit streuen, nicht panisch verkaufen", auto=None, richtung="ic_bear", art="Risiko",
         quelle="Geopolitik; Extremrisiken"),
    dict(id="r_russland", name="Russland–NATO-Eskalation", kapitel="geopolitik", p=20, p_text="20 %", impact=4,
         warnsignal="Vorfall mit Toten auf NATO-Gebiet, Debatte über Artikel 4/5",
         reaktion="Cash-Puffer nutzen, keine Panikverkäufe, Rüstung nicht überziehen", auto=None, richtung="ic_bear", art="Risiko",
         quelle="Geopolitik"),
    dict(id="r_protekt", name="Protektionismus / USMCA", kapitel="geopolitik", p=25, p_text="25 %", impact=3,
         warnsignal="Neue Verfahren nach Section 232/301/338, Drohung mit USMCA-Kündigung",
         reaktion="Autos untergewichten, Firmen mit nordamerikanisch integrierter Lieferkette reduzieren", auto=None, richtung="ic_bear", art="Risiko",
         quelle="Geopolitik"),
    dict(id="r_tail", name="Tail Risks (Weltkrieg ≈ 1 %, Pandemie 1–2 %, Bürgerkrieg < 0,5 %)", kapitel="geopolitik",
         p=2, p_text="je ≤ 2 %", impact=5,
         warnsignal="Artikel 5, Mobilmachung, Taiwan-Blockade; H5N1 ohne Tierkontakt oder WHO-Notstand; Gerichtsurteile nicht umgesetzt",
         reaktion="Keine Absicherung. Bei Warnsignal: Aktienquote senken, Cash und kurze Treasuries ↑ (Pandemie: Reisen verkaufen, Gesundheit und lange Treasuries ↑)",
         auto=None, quelle="Extremrisiken", richtung="ic_bear", art="Risiko"),
    # Geopolitische Themen (bisher eigene Themen-Szenarien) – jetzt als Risiken und Chancen mit Richtung Bear/Bull
    dict(id="r_iran_esk", name="Iran: Eskalation (Hormus zu)", kapitel="geopolitik", p=25, p_text="25 %", impact=4,
         warnsignal="Angriffe auf Golf-Infrastruktur oder US-Truppen, Hormus faktisch zu, Brent > 110 USD",
         reaktion="Zykliker reduzieren, Cash ↑, keine langen Treasuries; Energie, Gesundheit, Basiskonsum relativ besser",
         auto="oel_hoch", richtung="ic_bear", art="Risiko", quelle="Geopolitik (Öl & Iran-Krieg)"),
    dict(id="r_iran_deal", name="Iran: Entspannung (Deal)", kapitel="geopolitik", p=20, p_text="20 %", impact=3,
         warnsignal="Iran öffnet Hormus und stoppt Angriffe auf Tanker, Brent < 85 USD",
         reaktion="Energie-Übergewicht abbauen; Transport, zyklischer Konsum und längere Laufzeiten profitieren",
         auto="oel_tief", richtung="ic_bull", art="Chance", quelle="Geopolitik (Öl & Iran-Krieg)"),
    dict(id="r_china_esk", name="USA–China: Eskalation (Frist 10.01.)", kapitel="geopolitik", p=30, p_text="30 %", impact=3,
         warnsignal="Handelsfrieden läuft aus, Exportverbot seltene Erden, schärfere Zölle und Chip-Kontrollen",
         reaktion="Vor dem 10.01. keine neuen Wetten auf China-abhängige Titel; Halbleiter untergewichten, defensive Sektoren",
         auto=None, richtung="ic_bear", art="Risiko", quelle="Geopolitik (USA–China)"),
    dict(id="r_china_deal", name="USA–China: breiter Handels-Deal", kapitel="geopolitik", p=20, p_text="20 %", impact=3,
         warnsignal="Breiterer Handels-Deal an G20 (15.12.) oder vor dem 10.01.",
         reaktion="Halbleiter/IT und Industrie aufstocken, falls das Signal klar ist",
         auto=None, richtung="ic_bull", art="Chance", quelle="Geopolitik (USA–China)"),
]
RISIKO_BY_ID = {r["id"]: r for r in RISIKEN}

# ---------------------------------------------------------------------------
# Zyklus: zwei Einordnungen aus der Analyse (Position auf schematischer Kurve 0 … 1, Hochphase = 0,5)
# ---------------------------------------------------------------------------
ZYKLUS_SEED = [
    dict(person="Investment Committee (16.11.)", von=0.50, bis=0.58,
         einordnung="Späte Expansion, nahe Hochphase; Abschwung noch nicht bestätigt",
         argumente="BIP Q3 +3,4 %, Gewinne +31,4 %; aber Inflation und Zinsen hoch, Realkonsum schwach, Wohnungsbau schwach."),
    dict(person="Wachstumsanalyse", von=0.46, bis=0.56, einordnung="Fortgeschrittene Expansion, nahe Hochphase",
         argumente="BIP Q3 +3,4 % annualisiert, Output Gap +1,7 %, Industrieproduktion steigt, Arbeitsmarkt stabil."),
    dict(person="Konsum-/Politikanalyse", von=0.52, bis=0.60, einordnung="Spätzyklus mit stagflationären Zügen",
         argumente="Inflation über Ziel, Fed erhöht, Realkonsum stagniert, Sparquote tief, Stimmung schlecht."),
]
ZYKLUS_PHASEN = [  # (bis Position, Name)
    (0.12, "Tiefpunkt"), (0.32, "Erholung"), (0.45, "Expansion"), (0.61, "Hochphase / Spätzyklus"),
    (0.78, "Abschwung"), (0.90, "Rezession"), (1.01, "Tiefpunkt"),
]

# ---------------------------------------------------------------------------
# Termine (sort = Datum zum Sortieren/Countdown)
# ---------------------------------------------------------------------------
TERMINE = [
    ("2026-11-17", "17.11.2026", "Quartalszahlen Nvidia", "KI-Konzentration, Risk-off-Szenario"),
    ("2026-11-18", "18.–19.11.2026", "APEC Shenzhen (Trump–Xi)", "USA–China"),
    ("2026-11-19", "19.11.2026", "Auktion 10-jährige TIPS", "Laufzeitprämie"),
    ("2026-11-25", "25.11.2026", "BIP Q3 (2. Schätzung); Einkommen und Ausgaben Okt.", "Zyklus, Sparquote, Kern-PCE"),
    ("2026-12-01", "01.12.2026", "ISM Industrie Nov.", "Stagflationssignal"),
    ("2026-12-04", "04.12.2026", "Arbeitsmarktbericht Nov.", "Arbeitsmarkt-Regel, Fed"),
    ("2026-12-08", "08./09.12.2026", "Fed-Sitzung mit Projektionen", "Leitzins, Duration"),
    ("2026-12-14", "14.–15.12.2026", "G20 Miami", "USA–China"),
    ("2026-12-15", "Mitte Dez. 2026", "CPI Nov. (Datum im BLS-Kalender prüfen)", "Kern-CPI-Regel"),
    ("2027-01-10", "≈ 10.01.2027", "Ende Handelsfrieden USA–China", "Halbleiter, Industrie"),
    ("2027-01-15", "ab Mitte Jan. 2027", "Q4-Berichtssaison, Detailhandel Dez.", "Gewinne 2027, Konsum"),
    ("2027-01-26", "26./27.01.2027", "Fed-Sitzung", "Leitzins, Duration"),
    ("2027-02-03", "Anfang Feb. 2027", "Arbeitsmarkt Jan.; Fed-Bankenumfrage", "Arbeitsmarkt, Kreditqualität"),
    ("2027-02-15", "Mitte Feb. 2027", "CPI Jan.", "Inflation"),
    ("2027-02-28", "28.02.2027", "Abgabe Portfoliosimulation", "–"),
]

# ---------------------------------------------------------------------------
# Allokation: neutrale Werte (Startwerte – im Team per Regler einstellbar)
# ---------------------------------------------------------------------------
# Neutraler Diskussionsanker gemäss Investment-Committee-Statement (16.11.2026): 45 % Aktien / 50 % Treasuries / 5 % Cash
NEUTRAL_DEFAULT = {"aktien": 45.0, "cash": 5.0, "duration": 3.5, "tips": 15.0}
KORRIDOR = {"aktien": (40, 50), "treasuries": (45, 55), "cash": (0, 10)}  # Arbeitskorridor IC, zur Diskussion
AKTIEN_MAX_ABWEICHUNG = 15  # PP
SEKTOR_SCHWELLE = 0.25      # aktives Gewicht > 0,25 PP = übergewichtet, < −0,25 PP = untergewichtet

# ---------------------------------------------------------------------------
# Vorgaben der Simulation (Dozenten-App)
# ---------------------------------------------------------------------------
STARTKAPITAL = 10_000_000
BENCHMARK = {"aktien": 50.0, "anleihen": 50.0, "cash": 0.0}            # Strategie des Dozenten
DOZENT_BAND = {"aktien": (40, 60), "anleihen": (40, 60), "cash": (0, 10)}
# Gewicht der Sektoren im S&P 500 (% der Aktien), Stand Dozenten-App Nov. 2026 – bei Änderung hier nachführen
SP500_GEWICHT = {"Energie": 3.70, "Grundstoffe": 1.69, "Industrie": 8.11, "Zykl. Konsum": 9.24, "Basiskonsum": 4.76,
                 "Gesundheit": 10.01, "Finanzen": 11.79, "IT": 37.88, "Kommunikation": 9.05, "Versorger": 2.03,
                 "Immobilien": 1.75}
# Feste Kopie der Dozenten-App-Werte. SP500_GEWICHT selbst wird beim Start mit den Indexgewichten der Seite
# «Sektorallokation» überschrieben (letzter Stichtag, mit den Sektor-ETF-Kursen fortgeschrieben), siehe sektoren.index_info.
SP500_GEWICHT_DOZENT = dict(SP500_GEWICHT)
GICS_CODE = {"Energie": 10, "Grundstoffe": 15, "Industrie": 20, "Zykl. Konsum": 25, "Basiskonsum": 30, "Gesundheit": 35,
             "Finanzen": 40, "IT": 45, "Kommunikation": 50, "Versorger": 55, "Immobilien": 60}

# Monate der Simulation: Frist = Börseneröffnung am ersten Handelstag (15:30 Schweizer Zeit)
MONATE = [
    dict(id="2026-12", name="Dezember 2026", frist="2026-12-01 15:30", fed_sitzung="09.12.2026"),
    dict(id="2027-01", name="Januar 2027", frist="2027-01-04 15:30", fed_sitzung="27.01.2027"),
    dict(id="2027-02", name="Februar 2027", frist="2027-02-01 15:30", fed_sitzung=None),
]
MONAT_BY_ID = {m["id"]: m for m in MONATE}
IPS_FRIST = "2026-12-01 15:30"

# Überzeugung in der Dozenten-App. Sichtbar ist «mittel (70 %)»; die anderen Stufen bitte mit der App abgleichen.
UEBERZEUGUNG = [("tief", 55), ("mittel", 70), ("hoch", 85)]

# Dozentenfragen. art: bereich (Zahl → 3 Antworten), fed (diskret), sektoren (3 oben / 3 unten)
# groesse = Simulationsgrösse; optionen = (Text, untere Grenze, obere Grenze), Grenzen in der Einheit der Grösse
FRAGEN = [
    dict(nr=1, gruppe="Zwischen den Anlageklassen", titel="Rendite des S&P 500 (Total Return) im Monat", art="bereich",
         groesse="sp500", einheit="%",
         optionen=[("unter −2 %", None, -2), ("−2 % bis +2 %", -2, 2), ("über +2 %", 2, None)],
         aufloesung="Yahoo: ^SP500TR, Schlusskurs des letzten Handelstags vor dem Monat bis Monatsende.",
         portfolio="Aktienquote der Taktik gegen die Strategie."),
    dict(nr=2, gruppe="Zwischen den Anlageklassen", titel="Aktien gegen Anleihen: Welche Klasse schneidet besser ab?",
         art="bereich", groesse="aktien_minus_anleihen", einheit="PP",
         optionen=[("Anleihen besser um mehr als 2 Punkte", None, -2), ("innerhalb von ±2 Punkten", -2, 2),
                   ("Aktien besser um mehr als 2 Punkte", 2, None)],
         aufloesung="Yahoo: S&P 500 Total Return gegen den Treasury-Index der App.",
         portfolio="Aktien- gegen Anleihenquote der Taktik."),
    dict(nr=3, gruppe="Zwischen den Anlageklassen", titel="Risikoappetit: VIX am Monatsende gegen Monatsanfang",
         art="bereich", groesse="vix", einheit="Pkt.",
         optionen=[("sinkt um mehr als 2 Punkte", None, -2), ("−2 bis +2 Punkte", -2, 2), ("steigt um mehr als 2 Punkte", 2, None)],
         aufloesung="Yahoo: ^VIX, Monatsende gegen Monatsanfang.", portfolio="Aktienquote und defensive Sektoren."),
    dict(nr=4, gruppe="Zinsen", titel="Entscheid der Fed: Obergrenze des Zielbands am Monatsende gegen Monatsanfang",
         art="fed", groesse="fed", einheit="bp",
         optionen=[("Senkung", None, -1), ("unverändert", -1, 1), ("Erhöhung", 1, None)],
         aufloesung="FRED: DFEDTARU (nur Monate mit Sitzung).", portfolio="Duration der Anleihen gegen die Benchmark."),
    dict(nr=5, gruppe="Zinsen", titel="Rendite 10-jähriger US-Staatsanleihen: Veränderung im Monat", art="bereich",
         groesse="dgs10", einheit="bp",
         optionen=[("fällt um mehr als 15 Basispunkte", None, -15), ("−15 bis +15 Basispunkte", -15, 15),
                   ("steigt um mehr als 15 Basispunkte", 15, None)],
         aufloesung="FRED: DGS10.", portfolio="Duration der Anleihen gegen die Benchmark."),
    dict(nr=7, gruppe="Inflation und Wachstum", titel="US-Inflation (CPI gesamt, Jahresrate): Veröffentlichung im Monat gegen die vorherige",
         art="bereich", groesse="cpi_yoy_diff", einheit="PP",
         optionen=[("tiefer (−0.2 Punkte oder mehr)", None, -0.15), ("−0.1 bis +0.1 Punkte", -0.15, 0.15),
                   ("höher (+0.2 Punkte oder mehr)", 0.15, None)],
         aufloesung="Veröffentlichte Jahresrate (eine Nachkommastelle) gegen die vorherige Veröffentlichung.",
         portfolio="TIPS-Anteil und Duration."),
    dict(nr=10, gruppe="Rohstoffe und Währung", titel="Ölpreis (WTI): Veränderung im Monat", art="bereich", groesse="wti",
         einheit="%", optionen=[("fällt um mehr als 5 %", None, -5), ("−5 % bis +5 %", -5, 5), ("steigt um mehr als 5 %", 5, None)],
         aufloesung="Yahoo: CL=F.", portfolio="Gewicht des Sektors Energie gegen die Benchmark."),
    dict(nr=12, gruppe="Innerhalb der Aktien", titel="Sektoren: Welche 3 der 11 Sektoren schlagen den S&P 500 am stärksten (oben), welche 3 am schwächsten (unten)?",
         art="sektoren", groesse="sektoren", einheit="PP", optionen=[],
         aufloesung="Yahoo: Sektorindizes (wie im Marktüberblick), Rang der Monatsrendite.",
         portfolio="Aktive Sektorgewichte der Taktik."),
    dict(nr=13, gruppe="Innerhalb der Aktien", titel="Stil: Wachstum gegen Substanz (Growth gegen Value)", art="bereich",
         groesse="growth_value", einheit="PP",
         optionen=[("Value besser um mehr als 1.5 Punkte", None, -1.5), ("innerhalb von ±1.5 Punkten", -1.5, 1.5),
                   ("Growth besser um mehr als 1.5 Punkte", 1.5, None)],
         aufloesung="Yahoo: IWF gegen IWD.", portfolio="Faktorprofil Growth und Value."),
    dict(nr=14, gruppe="Innerhalb der Aktien", titel="Defensiv gegen zyklisch: Korb aus Basiskonsum, Gesundheit, Versorger gegen Korb aus Zyklischer Konsum, Industrie, Finanzen, Technologie",
         art="bereich", groesse="def_minus_zykl", einheit="PP",
         optionen=[("Defensive besser um mehr als 1.5 Punkte", 1.5, None), ("innerhalb von ±1.5 Punkten", -1.5, 1.5),
                   ("Zyklische besser um mehr als 1.5 Punkte", None, -1.5)],
         aufloesung="Yahoo: Sektorindizes.", portfolio="Sektorgewichte der Taktik."),
]
FRAGE_BY_NR = {f["nr"]: f for f in FRAGEN}
ERWARTUNG_TICKER = ("^SP500TR", "IWF", "IWD", "GOVT", "IEI", "^IRX")  # zusätzlich zu den Markt-Tickern

# Anleihen-Benchmark (Treasury-Index der Dozenten-App unbekannt → Näherung wählbar in «Einstellungen»)
# Gewichte je Laufzeit-Stützstelle (Jahre)
BOND_BENCHMARKS = {
    "US-Treasury-Markt gesamt (≈ GOVT)": {1: 0.20, 2: 0.15, 3: 0.12, 5: 0.15, 7: 0.10, 10: 0.10, 20: 0.06, 30: 0.12},
    "Treasuries 3–7 J. (≈ IEI)": {3: 0.35, 5: 0.40, 7: 0.25},
    "Treasuries 1–3 J. (≈ SHY)": {1: 0.45, 2: 0.55},
    "Treasuries 7–10 J. (≈ IEF)": {7: 0.40, 10: 0.60},
}
BOND_BENCHMARK_DEFAULT = "US-Treasury-Markt gesamt (≈ GOVT)"
LAUFZEITEN = [0.25, 1, 2, 3, 5, 7, 10, 20, 30]

# Worum es im Kapitel geht (Kurzzusammenfassung oben auf jeder Kapitelseite)
KAPITEL_WORUM = {
    "wachstum": "Wie stark wächst die US-Wirtschaft und wo stehen wir im Zyklus? Bestimmt, ob Aktien und zyklische Sektoren Rückenwind haben.",
    "arbeitsmarkt": "Hält der Arbeitsmarkt? Ein kippender Arbeitsmarkt ist das wichtigste Rezessionssignal und verändert den Fed-Pfad.",
    "inflation": "Wie hartnäckig ist die Inflation und was macht die Fed? Bestimmt Zinsen, Duration und TIPS-Anteil (Fragen 4 und 7).",
    "zinsen": "Wo stehen die Renditen und die Zinskurve? Bestimmt Duration, Laufzeiten und die Konkurrenz für Aktien (Fragen 2 und 5).",
    "kredit": "Wie locker sind die Finanzbedingungen, wie teuer ist Öl? Steigende Spreads warnen früh vor Risk-off (Frage 10).",
    "konsum": "Kann der Konsum (≈ 70 % des BIP) mithalten? Schwacher Konsum trifft zyklischen Konsum und Gewinne.",
    "gewinne": "Rechtfertigen Gewinne und Bewertung die Aktienkurse? Bestimmt Aktienquote, Stil und Sektoren (Fragen 1, 3, 12–14).",
    "geopolitik": "Welche politischen Schocks können das Szenario kippen? Iran/Öl, USA–China, Cyber, Russland, Zölle und Tail Risks.",
}

# Taylor-Regel (CFA Level III, Capital Market Expectations): i* = r_neutral + π + 0,5 × (π − π_Ziel) + 0,5 × Output Gap
TAYLOR_R_NEUTRAL = 1.0   # neutraler Realzins in % (Annahme, z. B. Laubach-Williams-Schätzung der NY Fed prüfen)
TAYLOR_PI_ZIEL = 2.0
