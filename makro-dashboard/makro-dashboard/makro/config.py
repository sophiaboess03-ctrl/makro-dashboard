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
        "icon": "📈",
        "fazit": "Fortgeschrittene Expansion: BIP Q3 +3,4 % annualisiert, Output Gap +1,7 %, "
                 "ISM-Neuaufträge seit 10 Monaten expansiv. Gegenpol: hoher Kostendruck (ISM-Preise über 75).",
    },
    {
        "id": "arbeitsmarkt",
        "name": "Arbeitsmarkt",
        "icon": "👷",
        "fazit": "Robust: Arbeitslosenquote 4,1 % und seit Q4 2025 sinkend, +165'000 Stellen im Oktober, "
                 "Lohnwachstum 3,1 %. Kein Rezessionssignal für die nächsten 3 Monate, wenig lohnseitiger Inflationsdruck.",
    },
    {
        "id": "inflation",
        "name": "Inflation & Fed",
        "icon": "🏛️",
        "fazit": "Inflation hartnäckig über Ziel: Kern-PCE 3,1 %, Kern-CPI +0,3 % zum Vormonat (≈ 3,6 % annualisiert). "
                 "Die Fed erhöht; unsere Wahrscheinlichkeit bis 28.02.: Erhöhung 85 %, Halten 14 %, Senkung 1 %.",
    },
    {
        "id": "zinsen",
        "name": "Zinsen & Anleihen",
        "icon": "💵",
        "fazit": "Lange Zinsen hoch (10 J. 5,10 %) wegen Defizit ≈ 6 % des BIP und schwacher Auktionen. "
                 "Bear Flattening (10 J. − 2 J. +22 bp): lange Laufzeiten bringen kaum Mehrrendite → Schwerpunkt 1–7 J., nichts über 10 J.",
    },
    {
        "id": "kredit",
        "name": "Kredit & Finanzbedingungen",
        "icon": "🏦",
        "fazit": "Finanzbedingungen locker (NFCI −0,46), Spreads sehr eng (BB 184 bp, BBB 107 bp); Stress nur im CCC-Segment. "
                 "Öl und starker Dollar belasten.",
    },
    {
        "id": "konsum",
        "name": "Konsum & Stimmung",
        "icon": "🛒",
        "fazit": "Konsum aus Ersparnis: Sparquote 3,8 % (tiefster Stand seit Okt. 2022), Realkonsum stagniert, Realeinkommen −0,3 %. "
                 "Stimmung schwach – getrieben von Preisen, nicht von Jobangst.",
    },
    {
        "id": "gewinne",
        "name": "Gewinne & Bewertung",
        "icon": "📊",
        "fazit": "Bewertung fair (Forward-KGV 17,8 unter dem 10-J.-Schnitt von 19,1), aber Aktienrisikoprämie nur +0,51 PP. "
                 "Positionierung einseitig: Fondsmanager-Cash 3,9 %, Tech-Übergewicht netto 37 %.",
    },
    {
        "id": "geopolitik",
        "name": "Geopolitik & Extremrisiken",
        "icon": "🌍",
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
     quelle="manuell", richtung="hoch", rot=103,
     schwelle="Rot anhaltend > 103",
     annahme="Fed erhöht, andere Notenbanken halten → Dollar stark seit Anfang Nov.",
     aktion="Multinationale mit hohem Auslandsumsatz untergewichten, binnenorientierte Firmen bevorzugen.",
     quelle1="Trading Economics", quelle2="ICE / Reuters", rhythmus="täglich", naechster="laufend",
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

# ---------------------------------------------------------------------------
# Szenarien (Wahrscheinlichkeit = Startwert; das Team schätzt selbst, die App mittelt)
# sektoren: Wirkung je GICS-Sektor (−2 … +2), aus der "Folge"-Spalte eurer Analyse
# ---------------------------------------------------------------------------
SEKTOREN = ["Energie", "Grundstoffe", "Industrie", "Zykl. Konsum", "Basiskonsum", "Gesundheit",
            "Finanzen", "IT", "Kommunikation", "Versorger", "Immobilien"]

THEMEN = [
    {"id": "zinsen", "name": "Zinsen & Inflation", "kapitel": ["inflation", "zinsen"],
     "quelle": "Makroanalyse Inflation, Geldpolitik, Anleihen"},
    {"id": "gewinne", "name": "Gewinne & Bewertung", "kapitel": ["gewinne"],
     "quelle": "S&P 500 Unternehmensgewinne und Bewertung"},
    {"id": "iran", "name": "Öl & Iran-Krieg", "kapitel": ["geopolitik", "kredit"],
     "quelle": "Geopolitik (Konsum-Teil: Eskalation und Deal je 25–35 %)"},
    {"id": "china", "name": "USA–China (Frist 10.01.2027)", "kapitel": ["geopolitik"],
     "quelle": "Geopolitik"},
]
THEMA_BY_ID = {t["id"]: t for t in THEMEN}

SZENARIEN = [
    # Zinsen & Inflation – Duration / TIPS-Ziel je Szenario aus euren Aktionen
    dict(id="z_basis", thema="zinsen", name="Basis: Hawkishe Fed", p=55,
         annahmen="Fed erhöht im Dez. auf 4,00–4,25 % und signalisiert mehr; Kern-PCE ≈ 3 %; Brent 85–100 USD",
         folge="2 J. 4,8–5,1 %, 10 J. 5,0–5,3 %; Bear Flattening",
         aktion="Duration ≈ 3,5 J., Schwerpunkt 1–7 J., TIPS 15 %, nichts > 10 J.",
         duration=3.5, tips=15, sektoren={}),
    dict(id="z_infl", thema="zinsen", name="Inflationsschock", p=25,
         annahmen="Erneut heisse Preisdaten, Brent > 100 USD, Fed +50 bp, schwache Auktionen",
         folge="2 J. 5,1–5,4 %, 10 J. 5,3–5,7 %; Bear Steepening; Aktien und Anleihen fallen gleichzeitig",
         aktion="Duration ≈ 2,5 J., TIPS-Anteil 25 %",
         duration=2.5, tips=25, sektoren={}),
    dict(id="z_riskoff", thema="zinsen", name="Risk-off / Abkühlung", p=20,
         annahmen="Arbeitsmarkt schwach, Konsum bricht ein, KI-Ausverkauf, CCC-Stress greift auf BB/BBB über, Fed pausiert",
         folge="2 J. 4,3–4,6 %, 10 J. 4,6–4,9 %; Bull Steepening",
         aktion="Duration 4–5 J.; bei Kreditstress Aktien −10 PP",
         duration=4.5, tips=15, tips_annahme=True, sektoren={}),
    # Gewinne & Bewertung – in der Analyse nicht beziffert: Startwerte 25/50/25 (Vorschlag)
    dict(id="g_pos", thema="gewinne", name="Positiv", p=25, p_vorschlag=True,
         annahmen="10 J. Richtung 4,5 %, Ausblick 2027 angehoben",
         folge="KGV-Ausweitung Richtung 19",
         aktion="Aktienrisikoprämie > 1 PP → Anleihen zurück auf neutral",
         sektoren={"IT": 1}),
    dict(id="g_basis", thema="gewinne", name="Basis", p=50, p_vorschlag=True,
         annahmen="Q4-Gewinnwachstum ≈ 28 %, 10 J. 4,7–5,0 %, Schätzungen 2027 halten",
         folge="KGV stabil um 17–18, Kurse folgen den Gewinnen",
         aktion="Zur Diskussion: IT übergewichten (Halbleiter + Software), Energie selektiv, zyklischen und Basiskonsum untergewichten",
         sektoren={"IT": 1, "Energie": 0.5, "Zykl. Konsum": -1, "Basiskonsum": -1}),
    dict(id="g_neg", thema="gewinne", name="Negativ", p=25, p_vorschlag=True,
         annahmen="10 J. über 5,25 %, Ausblicke 2027 enttäuschen",
         folge="KGV-Druck und sinkende Schätzungen",
         aktion="KI-Konzentration begrenzen, Aktienquote am unteren Rand",
         sektoren={"IT": -1}),
    # Öl & Iran-Krieg
    dict(id="i_basis", thema="iran", name="Basis: weder Frieden noch Krieg", p=55,
         annahmen="US-Blockade bleibt, vereinzelte Angriffe auf Schiffe, Brent ≈ 85–100 USD",
         folge="+ Energie; − Transport/Airlines, Chemie",
         aktion="Energie leicht übergewichten, Airlines meiden",
         sektoren={"Energie": 1, "Industrie": -1, "Grundstoffe": -1}),
    dict(id="i_esk", thema="iran", name="Eskalation", p=25,
         annahmen="Angriffe auf Golf-Infrastruktur oder US-Truppen, Hormus faktisch zu, Brent > 118 USD",
         folge="++ Energie, + Rüstung, Gesundheit, Basiskonsum; −− zyklischer Konsum, Transport",
         aktion="Bei Brent > 110 USD Zykliker reduzieren, Cash ↑; keine langen Treasuries",
         sektoren={"Energie": 2, "Industrie": -1, "Gesundheit": 1, "Basiskonsum": 1, "Zykl. Konsum": -2}),
    dict(id="i_deal", thema="iran", name="Entspannung (Deal)", p=20,
         annahmen="Iran öffnet Hormus und stoppt Angriffe auf Tanker",
         folge="− Energie; + Transport, zyklischer Konsum, längere Laufzeiten",
         aktion="Energie-Übergewicht bei Brent < 85 USD abbauen",
         sektoren={"Energie": -1, "Industrie": 1, "Zykl. Konsum": 1}),
    # USA–China
    dict(id="c_basis", thema="china", name="Basis: kurze Verlängerung / Teil-Deal", p=50,
         annahmen="Handelsfrieden bei APEC oder G20 verlängert; Streit um seltene Erden bleibt",
         folge="Volatilität in IT/Halbleitern, sonst neutral",
         aktion="Halbleiter mit grossem China-Geschäft nicht übergewichten, Positionsgrössen begrenzen",
         sektoren={"IT": -0.5}),
    dict(id="c_esk", thema="china", name="Eskalation", p=30,
         annahmen="Handelsfrieden läuft aus, Exportverbot seltene Erden, schärfere Zölle und Chip-Kontrollen",
         folge="−− Halbleiter, − Industrie, zyklischer Konsum; + Versorger, Gesundheit, Basiskonsum",
         aktion="Vor dem 10.01. keine neuen Wetten auf China-abhängige Titel; defensive Sektoren",
         sektoren={"IT": -2, "Industrie": -1, "Zykl. Konsum": -1, "Versorger": 1, "Gesundheit": 1, "Basiskonsum": 1}),
    dict(id="c_ent", thema="china", name="Entspannung", p=20,
         annahmen="Breiterer Handels-Deal",
         folge="++ Halbleiter/IT, + Industrie",
         aktion="Nach G20 (15.12.) aufstocken, falls das Signal klar ist",
         sektoren={"IT": 2, "Industrie": 1}),
]
SZEN_BY_ID = {s["id"]: s for s in SZENARIEN}

# ---------------------------------------------------------------------------
# Risiken (Wahrscheinlichkeit in % bis 28.02.2027, Auswirkung 1–5)
# auto: Schlüssel eines automatischen Warnsignals (siehe logic.auto_warnsignale)
# Auswirkung ist ein Startwert (Vorschlag) – das Team bewertet selbst.
# ---------------------------------------------------------------------------
RISIKEN = [
    dict(id="r_ki", name="KI-/Tech-Korrektur", kapitel="gewinne", p=50, p_text="40–60 %", impact=4,
         warnsignal="Fondsmanager-Cash < 4 %, Tech-Übergewicht > 37 %, KI-Blasen-Erwartung > 45 %, Gesetzesentwürfe zu Rechenzentren",
         reaktion="KI-/Halbleiter-Konzentration begrenzen", auto="fms_cash",
         quelle="Konsumenten, Finanzbedingungen, Stimmung, Politik"),
    dict(id="r_konsum", name="Konsumrückgang", kapitel="konsum", p=35, p_text="30–40 %", impact=3,
         warnsignal="Sparquote < 3,5 %, Realkonsum 2 Monate ≤ 0, ISM-Neuaufträge < 52 bei Preisen > 75, NFIB < 95",
         reaktion="Zyklischen Konsum und Small Caps untergewichten; Basiskonsum, Gesundheit relativ besser", auto="konsum",
         quelle="Konsumenten, Finanzbedingungen, Stimmung, Politik"),
    dict(id="r_cyber", name="Cyber-Eskalation", kapitel="geopolitik", p=30, p_text="30 % (von 25 % angehoben)", impact=3,
         warnsignal="CISA-Notfallwarnungen, Ausfall von Strom, Wasser, Zahlungsverkehr oder Börse",
         reaktion="Cybersecurity-Titel halten, Versorger breit streuen, nicht panisch verkaufen", auto=None,
         quelle="Geopolitik; Extremrisiken"),
    dict(id="r_russland", name="Russland–NATO-Eskalation", kapitel="geopolitik", p=20, p_text="20 %", impact=4,
         warnsignal="Vorfall mit Toten auf NATO-Gebiet, Debatte über Artikel 4/5",
         reaktion="Cash-Puffer nutzen, keine Panikverkäufe, Rüstung nicht überziehen", auto=None,
         quelle="Geopolitik"),
    dict(id="r_protekt", name="Protektionismus / USMCA", kapitel="geopolitik", p=25, p_text="25 %", impact=3,
         warnsignal="Neue Verfahren nach Section 232/301/338, Drohung mit USMCA-Kündigung",
         reaktion="Autos untergewichten, Firmen mit nordamerikanisch integrierter Lieferkette reduzieren", auto=None,
         quelle="Geopolitik"),
    dict(id="r_tail", name="Tail Risks (Weltkrieg ≈ 1 %, Pandemie 1–2 %, Bürgerkrieg < 0,5 %)", kapitel="geopolitik",
         p=2, p_text="je ≤ 2 %", impact=5,
         warnsignal="Artikel 5, Mobilmachung, Taiwan-Blockade; H5N1 ohne Tierkontakt oder WHO-Notstand; Gerichtsurteile nicht umgesetzt",
         reaktion="Keine Absicherung. Bei Warnsignal: Aktienquote senken, Cash und kurze Treasuries ↑ (Pandemie: Reisen verkaufen, Gesundheit und lange Treasuries ↑)",
         auto=None, quelle="Extremrisiken"),
]
RISIKO_BY_ID = {r["id"]: r for r in RISIKEN}

# ---------------------------------------------------------------------------
# Zyklus: zwei Einordnungen aus der Analyse (Position auf schematischer Kurve 0 … 1, Hochphase = 0,5)
# ---------------------------------------------------------------------------
ZYKLUS_SEED = [
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
NEUTRAL_DEFAULT = {"aktien": 60.0, "duration": 3.5, "tips": 15.0}
AKTIEN_MAX_ABWEICHUNG = 20  # PP
SEKTOR_SCHWELLE = 0.5       # Score > 0,5 = übergewichten, < −0,5 = untergewichten
