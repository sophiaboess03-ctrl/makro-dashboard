"""PDF für jede Seite: Die Seite sammelt beim Anzeigen ihre Inhalte (Text, Kennzahlen, Tabellen, einfache Charts)
in einem «Sammler». Der Knopf oben auf der Seite macht daraus ein PDF im Dashboard-Design.
"""
from __future__ import annotations

import html
import re
from datetime import datetime

import pandas as pd
import streamlit as st

import bericht as B


def klar(x) -> str:
    """Markdown/HTML → reiner Text fürs PDF."""
    s = str(x if x is not None else "")
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = s.replace("**", "").replace("__", "").replace("`", "")
    s = re.sub(r":material/[a-z_]+:", "", s)
    s = re.sub(r":(red|green|orange|gray|blue)\[(.*?)\]", r"\2", s)
    return s.strip()


class Sammler:
    def __init__(self, titel: str, unter: str = "", kurz: str | None = None, quer: bool = False):
        self.titel, self.unter, self.kurz, self.quer = titel, unter, kurz or titel, quer
        self.bloecke: list[tuple] = []

    def neue_seite(self):
        self.bloecke.append(("seite",))

    # --- Inhalte -----------------------------------------------------------
    def h(self, text: str):
        self.bloecke.append(("titel", klar(text)))

    def h2(self, text: str):
        self.bloecke.append(("untertitel", klar(text)))

    def text(self, text: str, fett: bool = False, klein: bool = False):
        t = klar(text)
        if t:
            self.bloecke.append(("text", t, fett, klein))

    def kpis(self, items):
        items = [(klar(a), klar(b), klar(c)) for a, b, c in items]
        for i in range(0, len(items), 4):
            teil = items[i:i + 4]
            n = max(len(teil), 1)
            m = {1: 120, 2: 70, 3: 44, 4: 32}[n] * (3 if self.quer else 2) // 2
            self.bloecke.append(("kpis", [(a[:m], b, c if len(c) <= m else c[:m - 1] + "…") for a, b, c in teil]))

    def tabelle(self, df: pd.DataFrame, breiten=None, max_zeilen: int = 60, farben=None, gross: float | None = None):
        """farben (optional): je Zeile eine Liste RGB-Tupel oder None je Spalte (Zellhintergrund)."""
        if df is None or len(df) == 0:
            return
        d = df.head(max_zeilen)
        kopf = [klar(c) for c in d.columns]
        zeilen = [[klar(v) if not (isinstance(v, float) and pd.isna(v)) else "–" for v in r] for r in d.itertuples(index=False)]
        self.bloecke.append(("tabelle", kopf, zeilen, breiten, farben[:max_zeilen] if farben else None, gross))

    def linien(self, serien, einheit="", titel="", h: float | None = None):
        self.bloecke.append(("linien", serien, einheit, titel, h))

    def balken(self, items: list[tuple[str, float, tuple]], einheit="", titel="", max_wert=None):
        self.bloecke.append(("balken", items, einheit, titel, max_wert))

    # --- PDF ---------------------------------------------------------------
    def _hoehe(self, b, pdf) -> float:
        """Grobe Höhe eines Blocks in mm (für Seitenumbrüche)."""
        art = b[0]
        breite = pdf.w - pdf.l_margin - pdf.r_margin
        if art == "titel":
            return 14.0
        if art == "untertitel":
            return 6.5
        if art == "text":
            gross = 7.8 if b[3] else 9
            zeichen = breite / (gross * 0.19)
            return (len(b[1]) // max(zeichen, 1) + 1) * gross * 0.52 + 1
        if art == "kpis":
            return 20.0
        if art == "tabelle":
            return (len(b[2]) + 1) * (_gross_tabelle(b, self.quer) * 0.55 + 2.6) + 2
        if art == "linien":
            return (b[4] if len(b) > 4 and b[4] else 46 if self.quer else 58) + 16
        if art == "balken":
            return 5.2 * len(b[1]) + 12
        return 0.0

    def pdf(self) -> bytes:
        pdf = B._neu(self.kurz, self.quer)
        pdf.deckblatt = True
        pdf.add_page()
        B._haupttitel(pdf, self.titel, klar(self.unter) + f"  -  erstellt {datetime.now():%d.%m.%Y %H:%M}")
        nutzbar = pdf.h - 18 - 24          # Seite ohne Kopf/Fuss
        for n_b, b in enumerate(self.bloecke):
            art = b[0]
            try:
                if art in ("titel", "untertitel"):
                    # Überschrift nicht allein unten auf der Seite: mit dem folgenden Inhalt bis zur ersten Tabelle/Grafik zusammenhalten
                    gruppe = 0.0
                    for b2 in self.bloecke[n_b:]:
                        gruppe += self._hoehe(b2, pdf)
                        if b2[0] in ("tabelle", "linien", "balken", "kpis", "seite"):
                            break
                    if gruppe > pdf.h - 18 - pdf.get_y() and gruppe < nutzbar:
                        pdf.add_page()
                if art == "titel":
                    B._titel(pdf, b[1])
                elif art == "untertitel":
                    B._untertitel(pdf, b[1])
                elif art == "text":
                    B._text(pdf, b[1], gross=7.8 if b[3] else 9, fett=b[2], farbe=B.MUTED if b[3] else None)
                    pdf.ln(0.8)
                elif art == "kpis":
                    B._kpis(pdf, b[1])
                elif art == "tabelle":
                    gross = _gross_tabelle(b, self.quer)
                    # kurze Tabellen nicht über einen Seitenumbruch zerreissen
                    zeilen_h = gross * 0.55 + 2.6
                    hoehe = (len(b[2]) + 1) * zeilen_h
                    rest = pdf.h - 18 - pdf.get_y()
                    if hoehe > rest and hoehe < nutzbar and rest < nutzbar - 10:
                        pdf.add_page()
                    B._tabelle(pdf, b[1], b[2], b[3], gross=gross, farben=b[4] if len(b) > 4 else None)
                elif art == "seite":
                    pdf.add_page()
                elif art == "linien":
                    B._linien(pdf, b[1], h=(b[4] if len(b) > 4 and b[4] else 46 if self.quer else 58), einheit=b[2], titel=b[3])
                elif art == "balken":
                    _balken(pdf, b[1], b[2], b[3], b[4])
            except Exception as e:  # noqa: BLE001 – ein kaputter Block soll das PDF nicht verhindern
                B._text(pdf, f"(Abschnitt konnte nicht dargestellt werden: {type(e).__name__})", gross=7, farbe=B.MUTED)
        return bytes(pdf.output())


def _gross_tabelle(b, quer: bool) -> float:
    n = len(b[1])
    return (b[5] if len(b) > 5 and b[5] else
            (7.6 if n <= 8 else 7.0 if n <= 11 else 6.4 if n <= 15 else 5.8) if quer else
            7.4 if n <= 6 else 6.6 if n <= 9 else 5.9)


def rgb(farbe: str) -> tuple[int, int, int]:
    """'#1F4E8C' → (31, 78, 140)."""
    f = farbe.lstrip("#")
    return tuple(int(f[i:i + 2], 16) for i in (0, 2, 4))


def _balken(pdf, items, einheit, titel, max_wert):
    if not items:
        return
    h = 5.2 * len(items) + 4
    if pdf.get_y() + h + 8 > pdf.h - 18:
        pdf.add_page()
    if titel:
        B._untertitel(pdf, titel)
    x0 = pdf.l_margin + 52
    w = pdf.w - pdf.r_margin - x0 - 22
    m = max_wert or max(abs(v) for _, v, _ in items) or 1
    neg = any(v < 0 for _, v, _ in items)
    null = x0 + (w / 2 if neg else 0)
    skala = (w / 2 if neg else w) / m
    pdf.set_font("Helvetica", "", 7.5)
    for name, v, farbe in items:
        y = pdf.get_y()
        pdf.set_xy(pdf.l_margin, y)
        pdf.set_text_color(*B.INK)
        pdf.cell(50, 4.2, B._t(name[:38]))
        pdf.set_fill_color(*(farbe or B.BLAU))
        breite = abs(v) * skala
        pdf.rect(null - (breite if v < 0 else 0), y + 0.6, max(breite, 0.3), 3.2, style="F")
        pdf.set_xy(null + (breite if v >= 0 else 0) + 1.5 if v >= 0 else null + 1.5, y)
        pdf.set_text_color(*B.MUTED)
        pdf.cell(20, 4.2, B._t(f"{v:+.1f}{einheit}" if neg else f"{v:.0f}{einheit}"))
        pdf.set_xy(pdf.l_margin, y + 5.2)
    if neg:
        pdf.set_draw_color(*B.STAHL)
        pdf.line(null, pdf.get_y() - 5.2 * len(items), null, pdf.get_y())
    pdf.set_text_color(*B.INK)
    pdf.ln(2)


def knopf(ph, sammler: Sammler | None, key: str, dateiname: str | None = None):
    """Oben auf der Seite: «PDF erstellen», danach «PDF herunterladen»."""
    if sammler is None:
        return
    with ph.container():
        c1, c2, _ = st.columns([1, 1, 3])
        if c1.button("PDF erstellen", icon=":material/picture_as_pdf:", key=f"pdf_{key}", width="content"):
            try:
                st.session_state[f"pdfdata_{key}"] = sammler.pdf()
            except Exception as e:  # noqa: BLE001
                st.error(f"PDF konnte nicht erstellt werden: {e}")
        if st.session_state.get(f"pdfdata_{key}"):
            c2.download_button("PDF herunterladen", st.session_state[f"pdfdata_{key}"],
                               dateiname or f"{re.sub(r'[^A-Za-z0-9]+', '_', sammler.titel)}_{datetime.now():%Y-%m-%d}.pdf",
                               "application/pdf", icon=":material/download:", key=f"dl_{key}", width="content")
