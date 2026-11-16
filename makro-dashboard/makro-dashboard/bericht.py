"""Wochenbericht als PDF: Schnappschuss von Ampeln, Signalen, Szenarien, Allokation, Märkten, Portfolio und Entscheiden."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st
from fpdf import FPDF

from makro import config as C
from makro import logic as L

ERSATZ = {"−": "-", "–": "-", "—": "-", "≥": ">=", "≤": "<=", "→": "->", "↑": "(+)", "↓": "(-)", "↗": "(+)", "↘": "(-)",
          "≈": "~", "·": "-", "«": '"', "»": '"', "‘": "'", "’": "'", "“": '"', "”": '"', "…": "...", "×": "x", "Σ": "Summe",
          "✓": "ok", "✔": "ok", "⚠": "!", "₿": "BTC", " ": " ", " ": " "}


def _t(x) -> str:
    s = str(x)
    for a, b in ERSATZ.items():
        s = s.replace(a, b)
    return s.encode("latin-1", "replace").decode("latin-1")


class PDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(120, 130, 150)
        self.cell(0, 6, _t("Makro-Dashboard USA - Portfoliosimulation FHGR"), align="L")
        self.cell(0, 6, _t(datetime.now().strftime("%d.%m.%Y %H:%M")), align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(120, 130, 150)
        self.cell(0, 6, f"Seite {self.page_no()}", align="C")


def _titel(pdf, text):
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(47, 93, 168)
    pdf.cell(0, 7, _t(text), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 9)


def _text(pdf, text, gross=9, fett=False):
    pdf.set_font("Helvetica", "B" if fett else "", gross)
    pdf.multi_cell(0, 4.6, _t(text), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)


def _tabelle(pdf, kopf, zeilen, breiten=None):
    pdf.set_font("Helvetica", "", 8)
    from fpdf.fonts import FontFace

    with pdf.table(col_widths=breiten, text_align="LEFT", line_height=4.4, first_row_as_headings=True,
                   headings_style=FontFace(emphasis="BOLD", fill_color=(238, 240, 243))) as t:
        r = t.row()
        for k in kopf:
            r.cell(_t(k))
        for z in zeilen:
            r = t.row()
            for v in z:
                r.cell(_t(v))
    pdf.set_font("Helvetica", "", 9)


def erstelle(ctx, markt_tab, signale, termine_df, portfolio_info: dict | None) -> bytes:
    pdf = PDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, _t("Wochenbericht Makrolage USA"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    zm, zt = ctx.zyklus_modell, ctx.zyklus_team
    z = ctx.status["ampel"].value_counts()
    _text(pdf, f"Stand {datetime.now():%d.%m.%Y} - erstellt von {ctx.person}. Zyklus (Team): "
               f"{L.phase((zt['von'].mean() + zt['bis'].mean()) / 2)}; Modell: {zm['phase']}. "
               f"Ampeln: {z.get('Rot', 0)} rot, {z.get('Gelb', 0)} gelb, {z.get('Grün', 0)} grün.")

    a = ctx.alloc
    _titel(pdf, "1. Gesamtempfehlung")
    ow = ", ".join(a["sektoren"][a["sektoren"]["empfehlung"] == "Übergewichten"]["sektor"]) or "-"
    uw = ", ".join(a["sektoren"][a["sektoren"]["empfehlung"] == "Untergewichten"]["sektor"]) or "-"
    _text(pdf, f"Aktien {a['aktien']:.0f} % / Treasuries {a['anleihen']:.0f} % / Cash {a['cash']:.0f} % "
               f"(neutral {a['neutral']['aktien']:.0f} / {100 - a['neutral']['aktien'] - a['neutral'].get('cash', 0):.0f} / "
               f"{a['neutral'].get('cash', 0):.0f}). Duration {a['duration']:.1f} J., TIPS {a['tips']:.0f} % der Anleihen.")
    _text(pdf, f"Übergewichten: {ow}. Untergewichten: {uw}.")
    if a["korridor"]:
        _text(pdf, "Ausserhalb IC-Korridor: " + "; ".join(a["korridor"]), fett=True)
    aktiv = [r for r in a["regeln"] if r["aktiv"]]
    if aktiv:
        _tabelle(pdf, ["Aktives Signal", "Auslöser", "Wirkung"],
                 [[r["name"], r["grund"], ", ".join(x for x in (f"Aktien {r['aktien']:+.0f} PP" if r["aktien"] else "",
                                                               f"Duration {r['duration']:g} J." if r["duration"] else "",
                                                               f"TIPS {r['tips']:g} %" if r["tips"] else "",
                                                               ", ".join(f"{k} {v:+g}" for k, v in r["sektoren"].items())) if x)]
                  for r in aktiv], (45, 75, 70))

    _titel(pdf, f"2. Aktive Signale ({len(signale)})")
    for s in signale[:15]:
        _text(pdf, f"- {s['titel']}: {s['text']}")

    _titel(pdf, "3. Szenarien (Team-Wahrscheinlichkeiten)")
    rows = []
    for t in C.THEMEN:
        d = ctx.szen[ctx.szen["thema"] == t["id"]]
        rows.append([t["name"], " | ".join(f"{r['name']}: {r['p']:.0f} %" for _, r in d.iterrows()),
                     "Team" if d["n"].max() else "Startwert"])
    _tabelle(pdf, ["Thema", "Szenarien", "Basis"], rows, (45, 125, 20))

    _titel(pdf, "4. Risiken")
    _tabelle(pdf, ["Risiko", "W.", "Ausw.", "Warnsignal"],
             [[r["name"], f"{r['p']:.0f} %" if pd.notna(r["p"]) else "-", f"{r['impact']:.1f}" if pd.notna(r["impact"]) else "-",
               "AKTIV" if r["signal"] else "-"] for _, r in ctx.risiken.sort_values("score", ascending=False).iterrows()],
             (100, 20, 20, 50))

    _titel(pdf, "5. Kernindikatoren")
    kern = ctx.status[ctx.status["kern"]]
    _tabelle(pdf, ["Indikator", "Wert", "Ampel", "Trend", "Stand"],
             [[r["name"], r["anzeige"], r["ampel"], f"{r['pfeil']} {r['trend']}".strip(),
               "" if r["datum"] is None or pd.isna(r["datum"]) else pd.Timestamp(r["datum"]).strftime("%d.%m.%Y")]
              for _, r in kern.iterrows()], (75, 35, 20, 30, 30))

    if markt_tab is not None and len(markt_tab):
        _titel(pdf, "6. Märkte (1 Woche)")
        m = markt_tab[markt_tab["gruppe"].isin(["Indizes", "Sektoren", "Währungen", "Rohstoffe", "Krypto", "Zinsen"])]
        m = m[m["kurs"].notna()]

        def ver(x, art):
            if x is None or pd.isna(x):
                return "-"
            return f"{x:+.0f} bp" if art == "bp" else f"{x:+.2f}" if art == "pkt" else f"{x:+.2f} %"
        _tabelle(pdf, ["Instrument", "Kurs", "1 Tag", "1 Woche", "YTD"],
                 [[r["name"], f"{r['kurs']:,.2f}".replace(",", "'"), ver(r["d1"], r["art"]), ver(r["w1"], r["art"]),
                   ver(r["ytd"], r["art"])] for _, r in m.iterrows()], (70, 30, 30, 30, 30))

    if portfolio_info:
        _titel(pdf, "7. Portfolio")
        _text(pdf, portfolio_info["text"])

    e = ctx.tabellen.get("entscheide")
    if e is not None and not e.empty:
        e = e.sort_values("zeit").drop_duplicates("id", keep="last")
        e = e[pd.to_datetime(e["datum"], errors="coerce") >= pd.Timestamp.today().normalize() - pd.Timedelta(days=7)]
        if len(e):
            _titel(pdf, "8. Entscheide der letzten 7 Tage")
            for _, r in e.iterrows():
                _text(pdf, f"{r['datum']} - {r['titel']} ({r['status']}): {r['entscheid']} Begründung: {r['begruendung']} "
                           f"Quellen: {r['quelle1']}; {r['quelle2']}.")

    if termine_df is not None and len(termine_df):
        heute = pd.Timestamp.today().normalize()
        t = termine_df[(termine_df["sort"] >= heute) & (termine_df["sort"] <= heute + pd.Timedelta(days=14))]
        if len(t):
            _titel(pdf, "9. Termine der nächsten 14 Tage")
            _tabelle(pdf, ["Datum", "Ereignis", "Betrifft"], [[r["Datum"], r["Ereignis"], r["Betrifft"]] for _, r in t.iterrows()],
                     (30, 80, 80))
    pdf.ln(4)
    _text(pdf, "Automatisch erstellt aus dem Team-Dashboard. Werte mit Quelle «Analyse (Startwert)» stammen aus der "
               "Makroanalyse; automatische Daten: FRED, Yahoo Finance. Regelbasierte Empfehlung - kein Teamentscheid.", gross=7)
    return bytes(pdf.output())


def seite_bericht(ctx, markt_tab, signale, termine_df, portfolio_info):
    st.title("📄 Wochenbericht")
    st.markdown("Ein Klick erzeugt einen PDF-Schnappschuss mit Gesamtempfehlung, aktiven Signalen, Szenarien, Risiken, "
                "Kernindikatoren, Märkten, Portfolio, Entscheiden der Woche und den nächsten Terminen. Am besten jeden "
                "Freitag herunterladen und im Team-Ordner ablegen – so habt ihr für die Abgabe einen lückenlosen Verlauf.")
    if st.button("PDF erstellen", type="primary"):
        try:
            st.session_state["bericht_pdf"] = erstelle(ctx, markt_tab, signale, termine_df, portfolio_info)
        except Exception as e:  # noqa: BLE001
            st.error(f"PDF konnte nicht erstellt werden: {e}")
    if st.session_state.get("bericht_pdf"):
        st.download_button("📥 Wochenbericht herunterladen", st.session_state["bericht_pdf"],
                           f"Wochenbericht_Makro_USA_{datetime.now():%Y-%m-%d}.pdf", "application/pdf")
