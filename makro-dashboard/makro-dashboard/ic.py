"""Investment Committee: gemeinsame Makrothese (Statement vom 16.11.2026) – verknüpft mit den Live-Daten der App."""
from __future__ import annotations

import html

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from makro import config as C
from makro import logic as L
from makro import ui
from storage import jetzt, zahl

STAND = "16.11.2026"

URTEIL = ("Die USA befinden sich in der **späten Expansion**, nahe einer konjunkturellen Hochphase, mit ersten Anzeichen "
          "einer Wachstumsverlangsamung und stagflationären Risiken. Ein breiter Abschwung oder eine Rezession ist für unseren "
          "kurzen Anlagehorizont noch nicht bestätigt. «Nach der Hochphase» ist ein Risiko-Szenario, keine gemessene Tatsache.")
BEGRUENDUNG = ("Das reale BIP wuchs im Q3 kräftig (+3,4 % annualisiert), die Industrieproduktion steigt und Unternehmensgewinne "
               "wachsen. Gleichzeitig sind Inflation und Zinsen hoch, der reale Konsum verliert Schwung, die Sparquote sinkt und "
               "der Wohnungsbau bleibt schwach – kein typischer früher Aufschwung mehr, aber auch noch kein eindeutiger Einbruch.")

# Bereich, Zahl im IC, Bedeutung, Signal IC, verknüpfte Indikatoren
BEWEISKETTE = [
    ("Wirtschaft", "+3,4 % Wachstum im Q3", "Die US-Wirtschaft wächst weiterhin kräftig.", "Positiv", ["bip", "outputgap"]),
    ("Arbeitsmarkt", "4,1 % arbeitslos", "Beschäftigung bleibt stabil; Löhne steigen langsamer.", "Gemischt", ["unrate", "loehne"]),
    ("Industrie", "Produktion steigt; Auslastung ≈ 77 %", "Fabriken produzieren mehr, sind aber nicht überlastet.", "Leicht positiv", ["indpro", "auslastung"]),
    ("Wohnungsbau", "schwach", "Hohe Zinsen bremsen neue Bauprojekte.", "Negativ", ["wohnbau"]),
    ("Konsum", "Kaufmenge stagniert; Sparquote 3,8 %", "Haushalte geben weiter Geld aus, haben aber weniger Puffer.", "Negativ", ["realkonsum", "sparquote"]),
    ("Inflation", "3,7 %; ohne Energie/Lebensmittel 3,1 % (PCE)", "Preise steigen weiterhin zu schnell für das Fed-Ziel von 2 %.", "Negativ", ["pce", "kernpce"]),
    ("US-Notenbank (Fed)", "weitere Zinserhöhungen möglich", "Kredite bleiben teuer; Aktienbewertungen unter Druck.", "Negativ", ["fedfunds", "dgs10"]),
    ("Unternehmensgewinne", "+31,4 % zum Vorjahr", "Viele Firmen verdienen deutlich mehr als vor einem Jahr.", "Positiv", ["eps_wachstum", "fwd_eps"]),
    ("Aktienbewertung", "KGV 17,7", "Aktien sind nicht extrem teuer, aber Anleihen bieten Konkurrenz.", "Gemischt", ["kgv", "erp"]),
    ("Geopolitik", "Öl, China, Cyberangriffe", "Unerwartete Ereignisse können Preise und Gewinne verändern.", "Risiko", ["brent", "epu"]),
]

FAKTOREN = [
    ("Öl / Iran / Ukraine", "Entspannung → Energie billiger → Inflation sinkt", "Eskalation → Energie-/Transportkosten steigen"),
    ("Zölle / Supreme Court", "Weniger Zollkosten stützen Margen und Preise", "Neue Zölle auf anderer Rechtsgrundlage / Handelskonflikte"),
    ("US–China / seltene Erden", "Entspannung verbessert Lieferketten", "Exportkontrollen, Engpässe, Chiprisiken"),
    ("KI-Rechenzentren / Regulierung", "Investitionen und Gewinne bleiben stark", "Strom-/Genehmigungsauflagen bremsen Capex"),
    ("Fed / Treasury-Renditen", "Schwächere Inflation → weniger Zinserhöhungen", "Hawkishe Überraschung → Renditen und Diskontsätze steigen"),
    ("Cyber / Tail Risks", "Keine breite Störung", "Angriff auf Infrastruktur oder Zahlungsverkehr"),
]

PFLICHTFRAGEN = [
    ("1. Zyklische Eignung", "Wie reagiert der Sektor auf späte Expansion, verlangsamten Konsum und hohe Zinsen?"),
    ("2. Gewinntrend", "Q3-Gewinnwachstum, Revisionen Q4/2027, Margenqualität, Sondereffekte"),
    ("3. Bewertung", "Forward-KGV versus Historie und Konkurrenz; Sensitivität gegenüber Treasury-Renditen"),
    ("4. Bilanzqualität", "Net Debt/EBITDA, Zinsdeckung, Cashflow, Fälligkeiten und variable Schulden"),
    ("5. Szenarien", "Gewinner/Verlierer bei Bull, Base, Bear; Öl-, Zoll-, China-, KI- und Regulierungsrisiken"),
    ("6. Entscheidung", "Über-/Neutral-/Untergewicht mit 2–3 Datenpunkten und klarem Gegenargument"),
]
SEKTORTHEMEN = {
    "IT": "Gewinnkonzentration, KI-Capex und Rechenzentrumsauflagen", "Kommunikation": "Gewinnkonzentration, KI-Capex und Rechenzentrumsauflagen",
    "Grundstoffe": "Industriezyklus, China und Rohstoffe", "Energie": "Ölpreissensitivität",
    "Zykl. Konsum": "realer Konsum und Finanzierung", "Finanzen": "Zinskurve und Kreditqualität",
    "Versorger": "Stromnachfrage versus Regulierung und Finanzierung",
}

# Beobachtung, Was würde die These verändern, Indikatoren
MONITORING = [
    ("CPI / Kern-PCE / Löhne", "Mehrere schwächere Werte → Bull wahrscheinlicher; erneute Beschleunigung → Bear",
     ["kerncpi", "cpi", "kernpce", "pce", "loehne"]),
    ("Fed-Entscheid und Kommunikation", "Entscheidend ist die Abweichung von Markterwartungen, nicht nur der Zinsschritt",
     ["fedfunds", "dgs2"]),
    ("2J-/10J-Treasury-Renditen", "Starker Anstieg belastet Duration und Aktienbewertungen; Rückgang entlastet", ["dgs2", "dgs10", "realzins"]),
    ("Arbeitsmarkt / realer Konsum", "Breite Verschlechterung → Übergang zum Abschwung neu beurteilen",
     ["unrate", "nfp", "claims", "sahm", "realkonsum", "sparquote"]),
    ("EPS-Revisionen / Guidance", "Sinkende 2027er Schätzungen schwächen die Aktien-These", ["fwd_eps", "eps_wachstum", "kgv"]),
    ("Öl / China / Ukraine / Cyber", "Nur bei realer Veränderung von Inflation, Gewinnen oder Risikoprämien reagieren", ["brent", "epu", "vix"]),
]

ABGLEICH = [
    ("A", "Treasury-Zeitpunkte", "Gewinn-/Bewertungsanalyse: 10J 4,90 % am 06.11.; Georg: 5,10 % zum späteren Stand. Vor Allokation ein gemeinsamer Schlusskurs mit Zeitstempel."),
    ("B", "Industrieauslastung", "Qualitativ «hohe Auslastung» vs. gemessen ≈ 76,9 % ohne Überhitzung. Im Konsensbericht die Messung verwenden, historisch einordnen."),
    ("C", "Inflation", "Oktober-CPI teilweise als Schätzung gekennzeichnet: Istwerte, Schätzungen und Veröffentlichungsdaten auseinanderhalten."),
    ("D", "Bewertung", "Gewinnrendite minus Treasury-Rendite (≈ 0,75 PP) ist nicht dasselbe wie eine modellbasierte implizite ERP. Nicht gleichsetzen."),
    ("E", "Szenariogewichte", "Fed-Wahrscheinlichkeiten (Zinshandlungen) und 55/25/20 % (Gesamtmarkt) nicht addieren oder direkt vergleichen."),
    ("F", "Datenstatus", "Geopolitische Meldungen, Gesetzespläne und Extremereignis-Wahrscheinlichkeiten sind Quellenangaben bzw. Annahmen – vor externer Präsentation prüfen."),
]

QUELLEN = ("FHGR_US_Growth_Business_Cycle_Analyse; 02 Arbeitsmarkt; Makroanalyse Inflation/Geldpolitik/Anleihen (Georg, 14.11.2026); "
           "Konsumenten/Finanzbedingungen/Stimmung/Politik (11.11.2026); S&P 500 Unternehmensgewinne und Bewertung "
           "(FactSet-Stand 06.11.2026); Geopolitik; Geopolitik Tail Risks.")

# Platzhalter-Renditen für 3 Monate (in %) – nur Startwerte, bitte im Team schätzen
RENDITE_PLATZHALTER = {"ic_base": (2.0, 1.2, 1.0), "ic_bull": (7.0, 3.0, 1.0), "ic_bear": (-10.0, -1.5, 1.0)}
QUOTEN = [40, 45, 50, 55, 60]
SIGNAL_FARBE = {"Positiv": L.GRUEN, "Leicht positiv": L.GRUEN, "Gemischt": L.GELB, "Negativ": L.ROT, "Risiko": L.KONTEXT}
EMOJI = {"Rot": "🔴", "Gelb": "🟡", "Grün": "🟢", "Kontext": "⚪"}
VOTEN = ["Übergewicht", "Neutral", "Untergewicht"]
VOTUM_WERT = {"Übergewicht": 1, "Neutral": 0, "Untergewicht": -1}


def _html_tabelle(spalten: list[str], zeilen: list[list[str]], breiten: list[str] | None = None):
    """Tabelle mit Zeilenumbrüchen in den Zellen (Zellen sind bereits HTML-sicher)."""
    kopf = "".join(f"<th style='text-align:left; padding:6px 8px; border-bottom:1px solid #E3E6EB; color:#667085; "
                   f"font-weight:600; font-size:0.72rem; letter-spacing:0.06em; text-transform:uppercase; width:{(breiten or [''] * len(spalten))[i]}'>{html.escape(c)}</th>" for i, c in enumerate(spalten))
    body = "".join("<tr>" + "".join(f"<td style='vertical-align:top; padding:6px 8px; border-bottom:1px solid #F0F1F4'>{z}</td>"
                                    for z in zeile) + "</tr>" for zeile in zeilen)
    st.markdown(f"<div style='overflow-x:auto'><table style='width:100%; border-collapse:collapse; font-size:0.85rem'>"
                f"<thead><tr>{kopf}</tr></thead><tbody>{body}</tbody></table></div>", unsafe_allow_html=True)


def _live(status: pd.DataFrame, ids: list[str]) -> str:
    teile = []
    for i in ids:
        if i not in status.index:
            continue
        r = status.loc[i]
        neu = " ·neu" if r["datum"] is not None and not pd.isna(r["datum"]) and pd.Timestamp(r["datum"]) > pd.Timestamp("2026-11-16") else ""
        teile.append(f"<span style='color:{ui.FARBE.get(r['ampel'], ui.MUTED)}'>●</span> {html.escape(C.IND_BY_ID[i]['name'].split(' (')[0])}: "
                     f"<b>{html.escape(r['anzeige'])}</b> {r['pfeil']}<span style='color:#B08D57'>{neu}</span>")
    return "<br>".join(teile) or "–"


def _tendenz(status: pd.DataFrame, ids: list[str]) -> str:
    t = status.loc[[i for i in ids if i in status.index], "trend"]
    b, s = int((t == "besser").sum()), int((t == "schlechter").sum())
    if b > s:
        return f"↗ eher Richtung Bull ({b} besser, {s} schlechter)"
    if s > b:
        return f"↘ eher Richtung Bear ({s} schlechter, {b} besser)"
    return f"→ unverändert ({b} besser, {s} schlechter)"


def _team(tabellen: dict, team: list[str]) -> list[str]:
    if team:
        return list(team)
    personen = set()
    for df in tabellen.values():
        if df is not None and not df.empty and "person" in df:
            personen |= set(df["person"].dropna())
    return sorted(p for p in personen if p and p != "Unbekannt")


def _renditen(einst: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    werte = {k: list(v) for k, v in RENDITE_PLATZHALTER.items()}
    wer = "Platzhalter – bitte im Team schätzen"
    if einst is not None and not einst.empty:
        d = einst[einst["schluessel"].str.startswith("ret|")].sort_values("zeit")
        for _, r in d.iterrows():
            _, sid, klasse = r["schluessel"].split("|") if "|" in r["schluessel"] else (None, None, None)
            v = zahl(r["wert"])
            if sid in werte and v is not None:
                werte[sid][["aktien", "treasuries", "cash"].index(klasse)] = v
        if len(d):
            wer = f"zuletzt geändert von {d.iloc[-1]['person']} am {d.iloc[-1]['zeit'][:10]}"
    df = pd.DataFrame([dict(id=k, Szenario=C.SZEN_BY_ID[k]["name"], **{"Aktien %": v[0], "Treasuries %": v[1], "Cash %": v[2]})
                       for k, v in werte.items()])
    return df, wer


def seite_ic(ctx, markt_tab: pd.DataFrame | None, team: list[str]):
    tab = ctx.tabellen
    notizen = tab.get("notizen")
    ic_n = notizen[notizen["kapitel"] == "ic"] if notizen is not None and not notizen.empty else pd.DataFrame(columns=["typ", "text", "person", "zeit"])
    mitglieder = _team(tab, team)
    freigaben = ic_n[ic_n["typ"] == "freigabe"].drop_duplicates("person", keep="last") if len(ic_n) else ic_n
    alle_frei = bool(mitglieder) and set(mitglieder) <= set(freigaben["person"])

    status_txt = ui.badge(L.GRUEN, "Version 1.0 freigegeben") if alle_frei else ui.badge(L.GELB, "Entwurf zur Teamabstimmung")
    ui.seitenkopf("Investment Committee – Makrothese",
                  f"{status_txt}&nbsp; Statement vom {STAND} · Anlagehorizont Dez. 2026 – Feb. 2027 · "
                  f"verbindliche Ausgangsbasis für die Sektoranalysen; definitive Asset-Allokation folgt.")

    t1, t2, t3, t4, t5 = st.tabs(["These & Beweise", "Szenarien & Renditen", "Sektorvoten", "Monitoring", "Datenabgleich & Freigabe"])

    # --- These -------------------------------------------------------------
    with t1:
        c1, c2 = st.columns([1.15, 1])
        with c1:
            st.subheader("Gemeinsames Urteil")
            st.markdown(URTEIL)
            st.markdown(f"<span class='klein'>{BEGRUENDUNG}</span>", unsafe_allow_html=True)
        with c2:
            ui.zyklus_chart(ctx.zyklus_team, ctx.zyklus_modell, 240)
            st.caption(f"Modell heute: {ctx.zyklus_modell['phase']} · Team-Einordnungen auf der Seite «Bewerten» anpassen.")
        st.subheader("Beweiskette – IC-Stand vs. heute")
        _html_tabelle(["Bereich", f"IC {STAND}", "Bedeutung", "Signal IC", "Heute (live)"],
                      [[f"<b>{html.escape(b)}</b>", html.escape(z), html.escape(m),
                        f"{ui.badge(SIGNAL_FARBE[s], s)}",
                        _live(ctx.status, ids)] for b, z, m, s, ids in BEWEISKETTE],
                      ["12%", "18%", "25%", "10%", "35%"])
        st.caption("«·neu» = Wert mit Datum nach dem IC-Statement. Einordnung IC: Hohe Inflation und langsameres Wachstum sind "
                   "Warnsignale – eine Rezession ist damit noch nicht bestätigt.")
        st.subheader("Was kann die These verändern?")
        st.dataframe(pd.DataFrame(FAKTOREN, columns=["Thema", "Positiver Kanal (Bull)", "Negativer Kanal (Bear)"]),
                     hide_index=True, width="stretch")
        st.caption("Supreme-Court-Urteil gegen IEEPA-Zölle (Feb. 2026) ist bekannt, kein neuer Katalysator; andere Zollgrundlagen bleiben "
                   "möglich. KI-Regulierung ist ein Risiko, kein beschlossenes Gesetz.")

    # --- Szenarien & Renditen ---------------------------------------------
    with t2:
        szen = ctx.szen[ctx.szen["thema"] == "gesamt"]
        st.subheader("Gemeinsame Szenarien Dez. 2026 – Feb. 2027")
        ui.szenario_chart(ctx.szen, ["gesamt"], 120)
        cols = st.columns(3)
        for c, sid in zip(cols, ["ic_base", "ic_bull", "ic_bear"]):
            s, r = C.SZEN_BY_ID[sid], szen.loc[sid]
            spanne = f"Team {r['p_min']:.0f}–{r['p_max']:.0f} % (n={r['n']})" if r["n"] else "Startwert IC"
            with c.container(border=True):
                st.markdown(f"**{html.escape(s['name'])}**  \n<span style='font-size:1.5rem; font-weight:700'>{r['p']:.0f} %</span> "
                            f"<span class='klein'>{spanne} · IC: {s['p']} %</span>", unsafe_allow_html=True)
                st.markdown(f"<span class='klein'>{html.escape(s['annahmen'])}. {html.escape(s['folge'])}.</span>", unsafe_allow_html=True)
        st.caption("Subjektive Team-Arbeitshypothesen, an Marktpreisen zu spiegeln. Nicht mit den Fed-Wahrscheinlichkeiten oder den "
                   "Themen-Szenarien addieren (Datenabgleich E). Wahrscheinlichkeiten ändern: Seite «Bewerten».")

        st.subheader("Szenariorenditen und Portfolioquoten")
        st.markdown("Schritte (3) und (4) der nächsten Sitzung: erwartete Renditen je Szenario für die **nächsten 3 Monate** schätzen, "
                    "dann 40/45/50/55/60 % Aktien vergleichen.")
        ren, wer = _renditen(tab.get("einstellungen"))
        st.caption(f"Renditeannahmen: {wer}")
        ed = st.data_editor(ren.drop(columns="id"), hide_index=True, width="stretch", disabled=["Szenario"], key="ren_edit",
                            column_config={k: st.column_config.NumberColumn(k, format="%.1f", step=0.5)
                                           for k in ("Aktien %", "Treasuries %", "Cash %")})
        c1, c2 = st.columns([1, 2])
        cash = c1.slider("Cash-Anteil (%)", 0, 10, int(round(ctx.alloc["cash"])), 1, help="IC-Korridor 0–10 %")
        if c2.button("Renditeannahmen fürs Team speichern"):
            zeilen = []
            for sid, (_, r) in zip(ren["id"], ed.iterrows()):
                for klasse, spalte in (("aktien", "Aktien %"), ("treasuries", "Treasuries %"), ("cash", "Cash %")):
                    zeilen.append(dict(zeit=jetzt(), person=ctx.person, schluessel=f"ret|{sid}|{klasse}", wert=float(r[spalte])))
            ctx.speichern("einstellungen", zeilen, "Renditeannahmen gespeichert.")
        p = {sid: szen.loc[sid, "p"] / 100 for sid in ["ic_base", "ic_bull", "ic_bear"]}
        R = {sid: ed.iloc[i] for i, sid in enumerate(ren["id"])}
        zeilen = []
        for a in QUOTEN:
            t = 100 - a - cash
            if t < 0:
                continue
            r_s = {sid: (a * R[sid]["Aktien %"] + t * R[sid]["Treasuries %"] + cash * R[sid]["Cash %"]) / 100 for sid in R}
            er = sum(p[sid] * r_s[sid] for sid in r_s)
            sd = float(np.sqrt(sum(p[sid] * (r_s[sid] - er) ** 2 for sid in r_s)))
            lo, hi = C.KORRIDOR["aktien"]
            zeilen.append(dict(aktien=a, treasuries=t, cash=cash, er=er, base=r_s["ic_base"], bull=r_s["ic_bull"],
                               bear=r_s["ic_bear"], sd=sd, korridor=lo <= a <= hi))
        res = pd.DataFrame(zeilen)
        c1, c2 = st.columns([1.2, 1])
        with c1:
            out = pd.DataFrame({
                "Aktien / Treas. / Cash": [f"{r.aktien} / {r.treasuries} / {r.cash}" for r in res.itertuples()],
                "Erwartete Rendite": res["er"].map(lambda x: f"{ui.de(x, 2, True)} %"),
                "Base": res["base"].map(lambda x: f"{ui.de(x, 2, True)} %"),
                "Bull": res["bull"].map(lambda x: f"{ui.de(x, 2, True)} %"),
                "Bear (Verlustrisiko)": res["bear"].map(lambda x: f"{ui.de(x, 2, True)} %"),
                "Streuung": res["sd"].map(lambda x: f"{ui.de(x, 2)} PP"),
                "Korridor": res["korridor"].map(lambda x: "✓" if x else "–"),
            })
            st.dataframe(out, hide_index=True, width="stretch")
            st.caption("Erwartete Rendite = Σ Wahrscheinlichkeit × Szenariorendite (3 Monate). Streuung = Standardabweichung über "
                       "die drei Szenarien. Modell-Empfehlung heute: "
                       f"{ctx.alloc['aktien']:.0f} % Aktien (Seite «Allokation»).")
        with c2:
            fig = go.Figure(go.Scatter(x=res["bear"], y=res["er"], mode="markers+text", text=[f"{a} %" for a in res["aktien"]],
                                       textposition="top center", marker=dict(size=14, color=[ui.BLAU if k else "#94A3B8" for k in res["korridor"]]),
                                       hovertemplate="Aktien %{text}<br>Bear %{x:.2f} %<br>Erwartet %{y:.2f} %<extra></extra>"))
            fig.update_layout(height=300, margin=dict(l=8, r=8, t=20, b=8), plot_bgcolor="white")
            fig.update_xaxes(title="Rendite im Bear Case (%)", gridcolor="#EEF0F3")
            fig.update_yaxes(title="Erwartete Rendite (%)", gridcolor="#EEF0F3")
            ui.zeige(fig)
            st.caption("Blau = innerhalb des IC-Korridors (40–50 % Aktien).")

        st.subheader("Vorbereitung der Asset-Allokation")
        st.markdown("""
- **Arbeitskorridor:** Aktien 40–50 %, US-Treasuries 45–55 %, Cash 0–10 % · **Anker:** 45 / 50 / 5 (kein Teamentscheid) · **Duration** um 3,5 J. (Georg); handelbare Anleihenliste vor Umsetzung prüfen.
- **Nächste Sitzung:** (1) Sektorvoten konsolidieren → Tab «Sektorvoten» · (2) Renditen je Szenario schätzen → Tabelle oben · (3) wahrscheinlichkeitsgewichtete Rendite und Bear-Verlust → oben · (4) 40–60 % Aktien vergleichen → oben · (5) Quote und Duration mit dokumentiertem Entscheid festlegen.
- **Drei Prüfungen:** Was ist in Aktien und Anleihen bereits eingepreist (Fed-Pfad, Gewinne)? Passen Gewinne, Bewertung und Risiken jedes Sektors zur späten Phase? Erst Szenariorenditen, dann Quoten vergleichen.
""")

    # --- Sektorvoten -------------------------------------------------------
    with t3:
        st.subheader("Sektorvoten")
        st.caption("Jede Person beurteilt ihren Sektor relativ zum S&P 500 mit derselben Base/Bull/Bear-Definition. "
                   "Abweichungen von der Makrothese ausdrücklich begründen.")
        sv = tab.get("sektorvoten")
        sv = sv.sort_values("zeit").drop_duplicates(["person", "sektor"], keep="last") if sv is not None and not sv.empty else pd.DataFrame(
            columns=["zeit", "person", "sektor", "votum", "datenpunkte", "gegenargument"])
        modell = dict(zip(ctx.alloc["sektoren"]["sektor"], ctx.alloc["sektoren"]["empfehlung"]))
        markt1m = {}
        if markt_tab is not None and not markt_tab.empty and "m1" in markt_tab:
            spx = markt_tab[markt_tab["name"] == "S&P 500"]
            spx_m = float(spx["m1"].iloc[0]) if len(spx) and not pd.isna(spx["m1"].iloc[0]) else None
            for _, r in markt_tab[markt_tab["gruppe"] == "Sektoren"].iterrows():
                if spx_m is not None and not pd.isna(r.get("m1")):
                    markt1m[r["name"]] = r["m1"] - spx_m
        zeilen = []
        for s in C.SEKTOREN:
            d = sv[sv["sektor"] == s]
            werte = [VOTUM_WERT.get(v, 0) for v in d["votum"]]
            konsens = "–" if not werte else ("Übergewicht" if np.mean(werte) > 0.33 else "Untergewicht" if np.mean(werte) < -0.33 else "Neutral")
            zeilen.append({"Sektor": s, "Voten Team": ", ".join(f"{p}: {v}" for p, v in zip(d["person"], d["votum"])) or "–",
                           "Team-Konsens": konsens, "Modell (Allokation)": modell.get(s, "–").replace("gewichten", "gewicht"),
                           "Markt 1 Mt. vs. S&P": f"{ui.de(markt1m[s], 2, True)} PP" if s in markt1m else "–",
                           "IC-Sektorthema": SEKTORTHEMEN.get(s, "")})
        st.dataframe(pd.DataFrame(zeilen), hide_index=True, width="stretch")
        abw = [z["Sektor"] for z in zeilen if z["Team-Konsens"] not in ("–", "Neutral") and z["Modell (Allokation)"] not in ("–",)
               and z["Team-Konsens"] != z["Modell (Allokation)"]]
        if abw:
            st.info("Team-Konsens weicht vom Modell ab bei: " + ", ".join(abw) + " – Begründung im Votum dokumentieren.")

        _sektordaten(ctx, tab)

        c1, c2 = st.columns([1, 1.2])
        with c1:
            with st.form("sektorvotum", clear_on_submit=True):
                st.markdown("**Votum abgeben / aktualisieren**")
                sektor = st.selectbox("Sektor", C.SEKTOREN)
                votum = st.radio("Votum relativ zum S&P 500", VOTEN, index=1, horizontal=True)
                dp = st.text_area("2–3 Datenpunkte (mit Quelle)", height=90)
                ga = st.text_area("Klares Gegenargument", height=70)
                if st.form_submit_button("Votum speichern", type="primary"):
                    if not dp.strip() or not ga.strip():
                        st.error("Bitte Datenpunkte und Gegenargument angeben (Pflichtfrage 6).")
                    else:
                        ctx.speichern("sektorvoten", [dict(zeit=jetzt(), person=ctx.person, sektor=sektor, votum=votum,
                                                           datenpunkte=dp.strip(), gegenargument=ga.strip())],
                                      f"Votum {sektor}: {votum} gespeichert.")
        with c2:
            st.markdown("**Pflichtfragen je Sektor**")
            st.dataframe(pd.DataFrame(PFLICHTFRAGEN, columns=["Pflichtfrage", "Was dokumentiert werden muss"]),
                         hide_index=True, width="stretch")
        if len(sv):
            st.markdown("**Begründungen**")
            for _, r in sv.sort_values(["sektor", "zeit"]).iterrows():
                with st.expander(f"{r['sektor']} – {r['votum']} ({r['person']}, {r['zeit'][:10]})"):
                    st.markdown(f"**Datenpunkte:** {html.escape(r['datenpunkte'])}  \n**Gegenargument:** {html.escape(r['gegenargument'])}")

    # --- Monitoring --------------------------------------------------------
    with t4:
        st.subheader("Gemeinsames Monitoring und Änderungsregeln")
        _html_tabelle(["Beobachtung", "Was würde die These verändern?", "Heute (live)", "Tendenz seit Vorwert"],
                      [[f"<b>{html.escape(b)}</b>", html.escape(w), _live(ctx.status, ids), html.escape(_tendenz(ctx.status, ids))]
                       for b, w, ids in MONITORING], ["16%", "26%", "38%", "20%"])
        st.caption("Tendenz = Zahl der Indikatoren, die sich seit dem letzten Wert verbessert bzw. verschlechtert haben (gemäss Ampel-Richtung). "
                   "Nächste Termine: Seite «Termine».")

    # --- Datenabgleich & Freigabe -----------------------------------------
    with t5:
        st.subheader("Datenabgleich vor endgültiger Freigabe")
        erledigt = ic_n[ic_n["typ"] == "abgleich"] if len(ic_n) else ic_n
        for code, titel, text in ABGLEICH:
            e = erledigt[erledigt["text"] == code] if len(erledigt) else erledigt
            c1, c2 = st.columns([5, 1.2])
            haken = (ui.badge(L.GRUEN, f"erledigt · {', '.join(e['person'].unique())}") if len(e) else ui.badge(L.LEER, "offen"))
            c1.markdown(f"**{code}. {titel}** · <span class='klein'>{haken}</span><br><span class='klein'>{html.escape(text)}</span>",
                        unsafe_allow_html=True)
            if not len(e) and c2.button("Erledigt", key=f"abgl_{code}"):
                ctx.speichern("notizen", [dict(zeit=jetzt(), person=ctx.person, kapitel="ic", typ="abgleich", text=code)],
                              f"Punkt {code} als erledigt markiert.")
        st.subheader("Freigabe Version 1.0")
        st.markdown("Bitte Szenariogewichte, Zyklusklassifikation und sektorübergreifende Annahmen gemeinsam bestätigen. "
                    "Sind alle bestätigt, gilt Version 1.0 als verbindliche Grundlage.")
        if mitglieder:
            st.markdown("  \n".join(f"{':green[■]' if m in set(freigaben['person']) else ':gray[□]'} {html.escape(m)}" for m in mitglieder))
        if ctx.person not in set(freigaben["person"]):
            if st.button("Ich bestätige die Makrothese (Version 1.0)", type="primary"):
                ctx.speichern("notizen", [dict(zeit=jetzt(), person=ctx.person, kapitel="ic", typ="freigabe",
                                               text="Bestätigt: Szenariogewichte, Zyklusklassifikation, Annahmen")],
                              "Bestätigung gespeichert.")
        else:
            st.success("Du hast die Makrothese bestätigt.")
        st.subheader("Quellenbasis")
        st.markdown(f"<span class='klein'>{QUELLEN} Für vollständige Quellenbelege und Berechnungsmethoden gelten die "
                    "Originaldokumente.</span>", unsafe_allow_html=True)


SEKTOR_FELDER = [("fwd_kgv", "Fwd-KGV"), ("kgv_hist", "KGV 10-J.-Schnitt"), ("eps_q3", "EPS-Wachstum Q3 %"),
                 ("eps_rev", "Revision EPS 2027 (4 Wo.) %"), ("nd_ebitda", "Net Debt/EBITDA"), ("quelle", "Quelle / Stand")]


def _sektordaten(ctx, tab):
    st.markdown("**Kennzahlen je Sektor** (Pflichtfragen 2–4) – direkt in der Tabelle eintragen und speichern")
    sd = tab.get("sektordaten")
    werte, quellen = {}, {}
    if sd is not None and not sd.empty:
        for _, r in sd.sort_values("zeit").iterrows():
            werte[(r["sektor"], r["feld"])] = r["wert"]
    zeilen = []
    for s in C.SEKTOREN:
        z = {"Sektor": s}
        for feld, label in SEKTOR_FELDER:
            v = werte.get((s, feld))
            z[label] = (v if feld == "quelle" else zahl(v)) if v not in (None, "") else (None if feld != "quelle" else "")
        zeilen.append(z)
    df = pd.DataFrame(zeilen)
    cfg = {label: st.column_config.NumberColumn(label, format="%.1f") for feld, label in SEKTOR_FELDER if feld != "quelle"}
    cfg["Quelle / Stand"] = st.column_config.TextColumn("Quelle / Stand", width="medium")
    ed = st.data_editor(df, hide_index=True, width="stretch", disabled=["Sektor"], column_config=cfg, key="sektordaten_edit")
    c1, c2 = st.columns([1, 3])
    if c1.button("Kennzahlen speichern"):
        neu = []
        def norm(v, feld):
            if feld == "quelle":
                return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()
            return None if v is None or pd.isna(v) else round(float(v), 4)

        for (_, alt), (_, jetzt_z) in zip(df.iterrows(), ed.iterrows()):
            for feld, label in SEKTOR_FELDER:
                a, b = norm(alt[label], feld), norm(jetzt_z[label], feld)
                if a != b:
                    neu.append(dict(zeit=jetzt(), person=ctx.person, sektor=jetzt_z["Sektor"], feld=feld,
                                    wert="" if b is None else b, quelle=norm(jetzt_z["Quelle / Stand"], "quelle")))
        if neu:
            ctx.speichern("sektordaten", neu, f"{len(neu)} Sektorkennzahl(en) gespeichert.")
        else:
            st.info("Keine Änderungen.")
    pr = ed.dropna(subset=["Fwd-KGV", "KGV 10-J.-Schnitt"])
    if len(pr):
        txt = " · ".join(f"{r['Sektor']}: {ui.de((r['Fwd-KGV'] / r['KGV 10-J.-Schnitt'] - 1) * 100, 0, True)} %"
                         for _, r in pr.iterrows() if r["KGV 10-J.-Schnitt"])
        c2.caption(f"Bewertungsprämie vs. 10-J.-Schnitt: {txt}")
