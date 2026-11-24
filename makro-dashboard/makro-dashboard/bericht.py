"""Wochenbericht als PDF: Schnappschuss von Ampeln, Signalen, Szenarien, Allokation, Märkten, Portfolio und Entscheiden."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st
from fpdf import FPDF

from makro import config as C
from makro import logic as L
from makro import ui

ERSATZ = {"−": "-", "–": "-", "—": "-", "≥": ">=", "≤": "<=", "→": "->", "↑": "(+)", "↓": "(-)", "↗": "(+)", "↘": "(-)",
          "≈": "~", "·": "-", "«": '"', "»": '"', "‘": "'", "’": "'", "“": '"', "”": '"', "…": "...", "×": "x", "Σ": "Summe",
          "✓": "ok", "✔": "ok", "✗": "x", "■": "x", "□": "-", "●": "*", "Δ": "Delta ", "π": "pi", "σ": "Sigma", "⚠": "!", "₿": "BTC", " ": " ", " ": " "}


def _t(x) -> str:
    s = str(x)
    for a, b in ERSATZ.items():
        s = s.replace(a, b)
    return s.encode("latin-1", "replace").decode("latin-1")


NAVY, BLAU, INK, MUTED, LINIE, SOFT = (20, 50, 92), (31, 78, 140), (16, 24, 40), (102, 112, 133), (227, 230, 235), (245, 246, 248)
GOLD, STAHL = (176, 141, 87), (143, 166, 196)
AMPEL_RGB = {"Rot": (185, 58, 50), "Gelb": (183, 121, 31), "Grün": (47, 133, 90), "Kontext": (107, 118, 133)}
KOPF = "PORTFOLIOSIMULATION FHGR  -  MAKROANALYSE USA"


class PDF(FPDF):
    titel_kurz = "Makro-Dashboard USA"

    def header(self):
        if self.page_no() == 1 and getattr(self, "deckblatt", False):
            return
        self.set_font("Helvetica", "B", 7)
        self.set_text_color(*BLAU)
        self.cell(95, 5, _t(KOPF), align="L")
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*MUTED)
        self.cell(0, 5, _t(f"{self.titel_kurz}  -  Stand {datetime.now():%d.%m.%Y}"), align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*LINIE)
        self.set_line_width(0.2)
        self.line(self.l_margin, self.get_y() + 1, self.w - self.r_margin, self.get_y() + 1)
        self.ln(5)
        self.set_text_color(*INK)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*MUTED)
        self.cell(0, 6, f"Seite {self.page_no()}", align="R")


def _neu(titel_kurz: str) -> PDF:
    pdf = PDF(orientation="P", unit="mm", format="A4")
    pdf.titel_kurz = titel_kurz
    pdf.set_margins(16, 14, 16)
    pdf.set_auto_page_break(True, margin=16)
    return pdf


def _haupttitel(pdf, titel, unter=""):
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.set_text_color(*BLAU)
    pdf.cell(0, 5, _t(KOPF), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Times", "B", 21)
    pdf.set_text_color(*INK)
    pdf.cell(0, 11, _t(titel), new_x="LMARGIN", new_y="NEXT")
    if unter:
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*MUTED)
        pdf.multi_cell(0, 4.6, _t(unter), new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*LINIE)
    pdf.line(pdf.l_margin, pdf.get_y() + 2, pdf.w - pdf.r_margin, pdf.get_y() + 2)
    pdf.ln(5)
    pdf.set_text_color(*INK)


def _titel(pdf, text):
    if pdf.get_y() > pdf.h - 45:
        pdf.add_page()
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 11.5)
    pdf.set_text_color(*NAVY)
    pdf.cell(0, 7, _t(text), new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*BLAU)
    pdf.set_line_width(0.5)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + 14, pdf.get_y())
    pdf.set_line_width(0.2)
    pdf.ln(2.5)
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "", 9)


def _untertitel(pdf, text):
    pdf.ln(1.5)
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.set_text_color(*MUTED)
    pdf.cell(0, 5, _t(text.upper()), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "", 9)


def _text(pdf, text, gross=9, fett=False, farbe=None):
    pdf.set_font("Helvetica", "B" if fett else "", gross)
    pdf.set_text_color(*(farbe or INK))
    pdf.multi_cell(0, gross * 0.52, _t(text), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*INK)


def _tabelle(pdf, kopf, zeilen, breiten=None, gross=7.8):
    from fpdf.fonts import FontFace

    pdf.set_font("Helvetica", "", gross)
    pdf.set_draw_color(*LINIE)
    pdf.set_fill_color(255, 255, 255)
    with pdf.table(col_widths=breiten, text_align="LEFT", line_height=gross * 0.55, first_row_as_headings=True,
                   borders_layout="HORIZONTAL_LINES", padding=1.2,
                   headings_style=FontFace(emphasis="BOLD", color=MUTED, fill_color=SOFT)) as t:
        r = t.row()
        for k in kopf:
            r.cell(_t(k))
        for z in zeilen:
            r = t.row()
            for v in z:
                r.cell(_t(v))
    pdf.ln(1)
    pdf.set_font("Helvetica", "", 9)


def _kpis(pdf, items, h=17):
    """Kennzahl-Kacheln: [(Titel, Wert, Untertitel)]."""
    if pdf.get_y() + h > pdf.h - 20:
        pdf.add_page()
    n = len(items)
    gap = 3
    w = (pdf.w - pdf.l_margin - pdf.r_margin - gap * (n - 1)) / n
    y = pdf.get_y()
    for i, (t, v, u) in enumerate(items):
        x = pdf.l_margin + i * (w + gap)
        pdf.set_draw_color(*LINIE)
        pdf.set_fill_color(255, 255, 255)
        pdf.rect(x, y, w, h, style="D", round_corners=True, corner_radius=1.5)
        pdf.set_xy(x + 3, y + 2.2)
        pdf.set_font("Helvetica", "B", 6.3)
        pdf.set_text_color(*MUTED)
        pdf.cell(w - 6, 3.5, _t(t.upper()))
        pdf.set_xy(x + 3, y + 6)
        gr = 12.5
        pdf.set_font("Helvetica", "B", gr)
        while gr > 7.5 and pdf.get_string_width(_t(v)) > w - 6:
            gr -= 0.5
            pdf.set_font("Helvetica", "B", gr)
        pdf.set_text_color(*INK)
        pdf.cell(w - 6, 6, _t(v))
        pdf.set_xy(x + 3, y + 12.3)
        pdf.set_font("Helvetica", "", 6.5)
        pdf.set_text_color(*MUTED)
        pdf.cell(w - 6, 3.5, _t(u))
    pdf.set_text_color(*INK)
    pdf.set_xy(pdf.l_margin, y + h + 3)


def _linien(pdf, serien: list[tuple[str, pd.Series, tuple]], h=58, einheit="", titel=""):
    """Einfaches Liniendiagramm direkt im PDF (ohne Bildexport)."""
    serien = [(n, s.dropna(), f) for n, s, f in serien if s is not None and len(s.dropna()) > 1]
    if not serien:
        return
    if pdf.get_y() + h + 16 > pdf.h - 18:
        pdf.add_page()
    if titel:
        _untertitel(pdf, titel)
    x0, y0 = pdf.l_margin + 12, pdf.get_y() + 2
    w = pdf.w - pdf.r_margin - x0
    t0 = min(s.index.min() for _, s, _ in serien)
    t1 = max(s.index.max() for _, s, _ in serien)
    lo = min(float(s.min()) for _, s, _ in serien)
    hi = max(float(s.max()) for _, s, _ in serien)
    pad = (hi - lo) * 0.08 or 1
    lo, hi = lo - pad, hi + pad
    span = max((t1 - t0).total_seconds(), 1)
    px = lambda t: x0 + (t - t0).total_seconds() / span * w  # noqa: E731
    py = lambda v: y0 + h - (v - lo) / (hi - lo) * h  # noqa: E731
    pdf.set_font("Helvetica", "", 6.5)
    pdf.set_text_color(*MUTED)
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        pdf.set_draw_color(*LINIE)
        pdf.set_line_width(0.15)
        pdf.line(x0, py(v), x0 + w, py(v))
        pdf.set_xy(pdf.l_margin, py(v) - 1.5)
        pdf.cell(11, 3, _t(f"{v:,.0f}{einheit}".replace(",", "'")), align="R")
    for k in range(5):
        t = t0 + (t1 - t0) * k / 4
        pdf.set_xy(px(t) - 10, y0 + h + 1)
        pdf.cell(20, 3, t.strftime("%d.%m.%y"), align="C")
    for name, s, farbe in serien:
        pdf.set_draw_color(*farbe)
        pdf.set_line_width(0.55 if name == serien[0][0] else 0.35)
        pts = [(px(t), py(float(v))) for t, v in s.items()]
        for (xa, ya), (xb, yb) in zip(pts[:-1], pts[1:]):
            pdf.line(xa, ya, xb, yb)
    pdf.set_line_width(0.2)
    y = y0 + h + 5.5
    x = x0
    for name, _, farbe in serien:
        pdf.set_fill_color(*farbe)
        pdf.rect(x, y + 0.8, 4, 1.4, style="F")
        pdf.set_xy(x + 5, y)
        pdf.set_text_color(*MUTED)
        pdf.cell(32, 3, _t(name))
        x += 38
    pdf.set_fill_color(255, 255, 255)
    pdf.set_draw_color(*LINIE)
    pdf.set_text_color(*INK)
    pdf.set_xy(pdf.l_margin, y + 6)


def erstelle(ctx, markt_tab, signale, termine_df, portfolio_info: dict | None) -> bytes:
    pdf = _neu("Wochenbericht")
    pdf.deckblatt = True
    pdf.add_page()
    zm, zt = ctx.zyklus_modell, ctx.zyklus_team
    z = ctx.status["ampel"].value_counts()
    _haupttitel(pdf, "Wochenbericht Makrolage USA",
                f"Stand {datetime.now():%d.%m.%Y}  -  erstellt von {ctx.person}  -  Anlagehorizont Dez. 2026 - Feb. 2027")
    a = ctx.alloc
    _kpis(pdf, [("Zyklusphase (Team)", L.phase((zt["von"].mean() + zt["bis"].mean()) / 2)[:24], f"Modell: {zm['phase']}"),
                ("Ampeln Rot / Gelb / Grün", f"{z.get('Rot', 0)} / {z.get('Gelb', 0)} / {z.get('Grün', 0)}",
                 f"von {len(ctx.status)} Indikatoren"),
                ("Aktive Signale", str(len(signale)), "Indikatoren, Kombis, Risiken"),
                ("Taktik A / A / C", f"{a['aktien']:.0f} / {a['anleihen']:.0f} / {a['cash']:.0f} %",
                 f"Duration {a['duration']:.1f} J.  -  TIPS {a['tips']:.0f} %")])

    _titel(pdf, "1. Taktik des Teams")
    ow = ", ".join(a["sektoren"][a["sektoren"]["empfehlung"] == "Übergewichten"]["sektor"]) or "-"
    uw = ", ".join(a["sektoren"][a["sektoren"]["empfehlung"] == "Untergewichten"]["sektor"]) or "-"
    _text(pdf, f"Aktien {a['aktien']:.0f} % / Treasuries {a['anleihen']:.0f} % / Cash {a['cash']:.0f} % "
               f"(neutral {a['neutral']['aktien']:.0f} / {100 - a['neutral']['aktien'] - a['neutral'].get('cash', 0):.0f} / "
               f"{a['neutral'].get('cash', 0):.0f}). Duration {a['duration']:.1f} J., TIPS {a['tips']:.0f} % der Anleihen.")
    _text(pdf, f"Übergewichten: {ow}. Untergewichten: {uw}.")
    if a["korridor"]:
        _text(pdf, "Ausserhalb IPS-Band: " + "; ".join(a["korridor"]), fett=True)
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

    _titel(pdf, "3. Szenarien")
    rows = [[ctx.szdef[s]["name"], f"{ctx.szen.loc[s, 'p']:.0f} %", ctx.szdef[s]["narrativ"][:150]] for s in ctx.szdef]
    _tabelle(pdf, ["Szenario", "W.", "Kurzbeschreibung"], rows, (55, 15, 120))
    _text(pdf, ctx.szen["basis"].iloc[0], gross=7.5, farbe=MUTED)

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
               "Makroanalyse; automatische Daten: FRED, Yahoo Finance. Taktik = Teameingabe (Seite Taktik).", gross=7,
          farbe=MUTED)
    return bytes(pdf.output())


def dossier(ctx, team: list[str], fred: dict, lade_kurse) -> bytes:
    """Abgabe-Dossier: These, Performance vs. Benchmark, Positionen, Transaktionen, Entscheide, Einschätzungen, Analysen."""
    import json

    import analyse as A
    import ic
    import portfolio as P
    from makro import views

    tab = ctx.tabellen
    pdf = _neu("Abgabe-Dossier")
    pdf.deckblatt = True
    pdf.add_page()
    pdf.ln(38)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*BLAU)
    pdf.cell(0, 5, _t(KOPF), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Times", "B", 30)
    pdf.set_text_color(*INK)
    pdf.multi_cell(0, 13, _t("Abgabe-Dossier\nPortfoliosimulation"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(0, 6, _t("Makroanalyse USA  -  Anlagehorizont Dezember 2026 bis Februar 2027\n"
                            "USD 10 Mio.  -  Aktien aus dem S&P 500 und US-Treasuries"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*BLAU)
    pdf.set_line_width(0.8)
    pdf.line(pdf.l_margin, pdf.get_y() + 5, pdf.l_margin + 25, pdf.get_y() + 5)
    pdf.set_line_width(0.2)
    pdf.ln(12)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*INK)
    pdf.cell(0, 5, _t("Team: " + ", ".join(team or [])), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*MUTED)
    pdf.cell(0, 5, _t(f"Stand {datetime.now():%d.%m.%Y}  -  erstellt von {ctx.person}  -  FHGR Banking & Finance"),
             new_x="LMARGIN", new_y="NEXT")

    # Portfolio-Daten
    tx = P.transaktionen(tab.get("transaktionen"))
    kapital, _ = P._einstellungen(tab.get("einstellungen"))
    kurse = {}
    if len(tx):
        tick = tuple(sorted(set(tx.loc[tx["klasse"] == "Aktie", "ticker"].str.upper())))
        kurse = lade_kurse(tick + ("^GSPC",))[0] if tick else {}
    perf = P.performance_daten(tx, kurse, fred, kapital, ctx.alloc["neutral"]) if len(tx) else None
    pos, cash_delta, _ = P.positionen(tx) if len(tx) else ({}, 0.0, 0.0)
    b = P.bewerte(pos, kurse, fred, pd.Timestamp.today().normalize()) if pos else pd.DataFrame()
    cash = kapital + cash_delta
    total = (float(b["wert"].sum() + b["zins"].sum()) if len(b) else 0.0) + cash
    if perf:
        pdf.ln(18)
        _kpis(pdf, [("Portfoliowert", f"{ui.de(total, 0)} USD", f"Startkapital {ui.de(kapital, 0)} USD"),
                    ("Rendite seit Start", f"{ui.de(perf['g_p'], 2, True)} %", f"Benchmark 50/50 {ui.de(perf['g_b'], 2, True)} %"),
                    ("Mehrrendite vs. Benchmark", f"{ui.de(perf['g_p'] - perf['g_b'], 2, True)} PP",
                     f"vs. neutral (IPS) {ui.de(perf['g_p'] - perf['g_n'], 2, True)} PP"),
                    ("Sharpe Ratio", A._zahl(perf["kp"].get("sharpe")), f"neutral {A._zahl(perf['kn'].get('sharpe'))}")])

    # 1 These
    pdf.add_page()
    _titel(pdf, "1. Makrothese und Taktik")
    _text(pdf, ic.URTEIL.replace("**", ""))
    pdf.ln(1)
    _text(pdf, ic.BEGRUENDUNG, farbe=MUTED)
    a = ctx.alloc
    _kpis(pdf, [("Taktik Aktien / Anl. / Cash", f"{a['aktien']:.0f} / {a['anleihen']:.0f} / {a['cash']:.0f} %",
                 f"neutral {a['neutral']['aktien']:.0f} / {100 - a['neutral']['aktien'] - a['neutral'].get('cash', 0):.0f} / "
                 f"{a['neutral'].get('cash', 0):.0f} %"),
                ("Duration", f"{ui.de(a['duration'], 1)} J.", f"TIPS {a['tips']:.0f} % der Treasuries"),
                ("Zyklus (Team)", L.phase((ctx.zyklus_team['von'].mean() + ctx.zyklus_team['bis'].mean()) / 2)[:24],
                 f"Modell: {ctx.zyklus_modell['phase']}")])
    ow = ", ".join(a["sektoren"][a["sektoren"]["empfehlung"] == "Übergewichten"]["sektor"]) or "-"
    uw = ", ".join(a["sektoren"][a["sektoren"]["empfehlung"] == "Untergewichten"]["sektor"]) or "-"
    _text(pdf, f"Sektoren übergewichten: {ow}. Untergewichten: {uw}.")

    # 2 Performance
    _titel(pdf, "2. Performance gegenüber Benchmark")
    if perf:
        kp, kn = perf["kp"], perf["kn"]
        ks = A.kennzahlen(perf["r_s"], A.rf_reihe({}, perf["r_s"].index), perf["r_s"]) if perf["tage"] >= 20 else {}
        zeilen = [["Rendite seit Start", f"{ui.de(perf['g_p'], 2, True)} %", f"{ui.de(perf['g_n'], 2, True)} %",
                   f"{ui.de(perf['g_s'], 2, True)} %"],
                  ["Benchmark Dozent (50/50)", f"{ui.de(perf['g_b'], 2, True)} %", "", ""],
                  ["Tracking Error vs. Benchmark", f"{ui.de(kp.get('te_bench'), 2)} %" if kp.get("te_bench") else "-", "", ""]]
        for key, name, f, _ in A.ZEILEN:
            if key in kp or key in kn:
                zeilen.append([name, f(kp.get(key)) if key in kp else "-", f(kn.get(key)) if key in kn else "-",
                               f(ks.get(key)) if key in ks else "-"])
        _tabelle(pdf, ["Kennzahl", "Portfolio", "Neutral (IPS)", "S&P 500"], zeilen, (70, 36, 36, 36))
        if perf["tage"] < 20:
            _text(pdf, f"Erst {perf['tage']} Handelstage - Risikokennzahlen ab 20 Handelstagen.", gross=7.5, farbe=MUTED)
        idx = lambda r: pd.concat([pd.Series([100.0], index=[perf["v"].index[0]]), (1 + r).cumprod() * 100])  # noqa: E731
        _linien(pdf, [("Portfolio", idx(perf["r_p"]), BLAU), ("Benchmark 50/50", idx(perf["r_b"]), (185, 58, 50)),
                      ("Neutral (IPS)", idx(perf["r_n"]), GOLD),
                      ("S&P 500", idx(perf["r_s"]), STAHL)], titel="Wertentwicklung (Start = 100)")
        _text(pdf, "Neutral = strategische Allokation (IPS) mit S&P 500 (SPY) / Treasuries (Benchmark-ETF) / Cash zum T-Bill-Satz, konstante Gewichte. "
                   "S&P 500 inkl. Dividenden. Treasuries im Portfolio über die FRED-Zinskurve bewertet (Näherung).",
              gross=7, farbe=MUTED)
    else:
        _text(pdf, "Noch keine Transaktionen erfasst.")

    # 3 Positionen
    if len(b):
        _titel(pdf, "3. Positionen per " + datetime.now().strftime("%d.%m.%Y"))
        d = b.copy()
        _tabelle(pdf, ["Klasse", "Titel", "Sektor", "Menge / Nominal", "Kurs", "Marktwert USD", "Gewicht"],
                 [[r["klasse"], str(r["ticker"]) + (f" - {r['name']}" if r["name"] and str(r["name"]) != "nan" else ""),
                   r["sektor"] if r["sektor"] and str(r["sektor"]) != "nan" else "", ui.de(r["menge"], 0),
                   ui.de(r["kurs"], 2) + (" %" if r["klasse"] == "Treasury" else ""), ui.de(r["wert"], 0),
                   f"{ui.de(r['wert'] / total * 100, 1)} %"] for _, r in d.iterrows()] +
                 [["Cash", "", "", "", "", ui.de(cash, 0), f"{ui.de(cash / total * 100, 1)} %"]],
                 (18, 52, 24, 24, 20, 25, 15))

    # 4 Transaktionen
    roh = tab.get("transaktionen")
    if roh is not None and not roh.empty:
        _titel(pdf, "4. Transaktionen mit Begründung")
        liste = P.transaktionsliste(roh)
        liste = liste[liste["Status"] == "aktiv"].iloc[::-1]
        _tabelle(pdf, ["Datum", "Art", "Titel", "Menge", "Kurs", "Betrag", "Von", "Begründung"],
                 [[r["Datum"], r["Art"], r["Titel"], r["Menge"], r["Kurs"], r["Betrag"], r["Erfasst von"], r["Begründung"]]
                  for _, r in liste.iterrows()], (16, 11, 30, 15, 18, 22, 14, 52), gross=7)

    # 5 Entscheide
    e = tab.get("entscheide")
    if e is not None and not e.empty:
        _titel(pdf, "5. Entscheidungsprotokoll")
        e = e.sort_values("zeit").drop_duplicates("id", keep="last").sort_values("datum")
        for _, r in e.iterrows():
            _text(pdf, f"{r['datum']}  -  {r['titel']}  ({r['status']})", fett=True)
            _text(pdf, f"Entscheid: {r['entscheid']}")
            _text(pdf, f"Begründung: {r['begruendung']}", farbe=MUTED)
            _text(pdf, f"Quellen: {r['quelle1']}; {r['quelle2']}  -  Gegenargument: {r['gegenargument'] or '-'}  -  "
                       f"Beteiligte: {r['beteiligte'] or '-'}", gross=7.5, farbe=MUTED)
            pdf.ln(1.5)

    # 6 Einschätzungen
    _titel(pdf, "6. Verlauf der Einschätzungen")
    v = views.szenario_verlauf_df(tab.get("szenario_votes"), tab.get("szenario_beschluss"))
    zeilen = []
    for sid, g in v.groupby("id", sort=False):
        g = g.sort_values("tag")
        zeilen.append([ctx.szdef.get(sid, {}).get("name", g["name"].iloc[0]), f"{g['p'].iloc[0]:.0f} %", f"{g['p'].iloc[-1]:.0f} %",
                       f"{g['p'].iloc[-1] - g['p'].iloc[0]:+.0f} PP", str(len(g) - 1)])
    _tabelle(pdf, ["Szenario", "Start (IC)", "Heute", "Veränderung", "Updates"], zeilen, (90, 22, 22, 30, 18), gross=7.2)
    z = views.zyklus_verlauf_df(tab.get("zyklus_votes"))
    _text(pdf, f"Zyklusposition Team: Start {ui.de((z['von'].iloc[0] + z['bis'].iloc[0]) / 2, 2)} "
               f"({L.phase((z['von'].iloc[0] + z['bis'].iloc[0]) / 2)}), heute {ui.de((z['von'].iloc[-1] + z['bis'].iloc[-1]) / 2, 2)} "
               f"({L.phase((z['von'].iloc[-1] + z['bis'].iloc[-1]) / 2)}).", gross=8)

    # 7 Analysen
    an = tab.get("analysen")
    if an is not None and not an.empty:
        _titel(pdf, "7. Analyseportfolios (Seite Analyse)")
        zeilen = []
        for _, r in A.portfolios(an).sort_values("zeit").iterrows():
            try:
                kz = json.loads(r["kennzahlen"])
            except Exception:  # noqa: BLE001
                kz = {}
            zeilen.append([str(r["zeit"])[:10], r["name"], r["person"], A._zahl(kz.get("sharpe")),
                           A._pct(kz.get("rendite"), 1, True), A._pct(kz.get("vola")), A._zahl(kz.get("beta")),
                           f"{kz.get('aktien', '-')} / {kz.get('treasuries', '-')} / {kz.get('cash', '-')}", r["notiz"]])
        _tabelle(pdf, ["Datum", "Name", "Von", "Sharpe", "Rendite p.a.", "Vola", "Beta", "A / T / C %", "Notiz"], zeilen,
                 (16, 34, 14, 13, 17, 12, 11, 20, 41), gross=7)

    # 8 Risiken
    _titel(pdf, "8. Risiken und Warnsignale (aktuell)")
    _tabelle(pdf, ["Risiko", "Wahrsch.", "Auswirkung", "Warnsignal", "Vorab festgelegte Reaktion"],
             [[r["name"], f"{r['p']:.0f} %" if pd.notna(r["p"]) else "-", ui.de(r["impact"], 1) if pd.notna(r["impact"]) else "-",
               "aktiv" if r["signal"] else "-", r["reaktion"]] for _, r in ctx.risiken.sort_values("score", ascending=False).iterrows()],
             (40, 16, 18, 18, 86), gross=7.2)

    _titel(pdf, "Methodik und Quellen")
    _text(pdf, "Makrodaten: FRED (Federal Reserve Bank of St. Louis); Kurse: Yahoo Finance (dividendenbereinigt); Sektoren: GICS. "
               "Kennzahlen annualisiert mit 252 Handelstagen; Sharpe Ratio mit dem 13-Wochen-T-Bill-Satz als risikofreiem Zins; "
               "VaR historisch (95 %, 1 Tag). Analysen von Vorauswahlen beruhen auf vergangenen Kursen und sind keine Prognose. "
               "Makroanalyse des Teams: " + ic.QUELLEN, gross=7.5, farbe=MUTED)
    return bytes(pdf.output())


def seite_bericht(ctx, markt_tab, signale, termine_df, portfolio_info, team=None, fred=None, lade_kurse=None):
    ui.seitenkopf("Berichte", "PDF-Berichte für das Team-Archiv und für die Abgabe – im gleichen Design wie das Dashboard.")
    c1, c2 = st.columns(2)
    with c1.container(border=True):
        st.markdown("**Wochenbericht**")
        st.caption("Schnappschuss mit Taktik, Signalen, Szenarien, Risiken, Kernindikatoren, Märkten, Portfolio, "
                   "Entscheiden der Woche und Terminen. Am besten jeden Freitag herunterladen und im Team-Ordner ablegen.")
        if st.button("Wochenbericht erstellen", type="primary", icon=":material/description:"):
            try:
                st.session_state["bericht_pdf"] = erstelle(ctx, markt_tab, signale, termine_df, portfolio_info)
            except Exception as e:  # noqa: BLE001
                st.error(f"PDF konnte nicht erstellt werden: {e}")
        if st.session_state.get("bericht_pdf"):
            st.download_button("Wochenbericht herunterladen", st.session_state["bericht_pdf"],
                               f"Wochenbericht_Makro_USA_{datetime.now():%Y-%m-%d}.pdf", "application/pdf", icon=":material/download:")
    with c2.container(border=True):
        st.markdown("**Abgabe-Dossier**")
        st.caption("Anhang für die Abgabe: Makrothese, Performance gegenüber Benchmark (50/50), neutralem Portfolio (IPS) und S&P 500, "
                   "Positionen, alle Transaktionen mit Begründung, Entscheidungsprotokoll, Verlauf eurer Einschätzungen, "
                   "geprüfte Vorauswahlen, Risiken und Methodik.")
        if st.button("Abgabe-Dossier erstellen", type="primary", icon=":material/folder_zip:", disabled=fred is None):
            try:
                with st.spinner("Erstelle Dossier …"):
                    st.session_state["dossier_pdf"] = dossier(ctx, team or [], fred, lade_kurse)
            except Exception as e:  # noqa: BLE001
                st.error(f"Dossier konnte nicht erstellt werden: {e}")
        if st.session_state.get("dossier_pdf"):
            st.download_button("Abgabe-Dossier herunterladen", st.session_state["dossier_pdf"],
                               f"Abgabe-Dossier_Portfoliosimulation_{datetime.now():%Y-%m-%d}.pdf", "application/pdf",
                               icon=":material/download:")
