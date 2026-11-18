"""Darstellungs-Bausteine: Ampel-Badges, Kennzahl-Karten und Charts."""
from __future__ import annotations

import html
import math

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from . import config as C
from . import logic as L

FARBE = {L.ROT: "#B93A32", L.GELB: "#B7791F", L.GRUEN: "#2F855A", L.KONTEXT: "#6B7685", L.LEER: "#A0A8B4"}
HELL = {L.ROT: "#FDF1F0", L.GELB: "#FDF6E7", L.GRUEN: "#EDF7F1", L.KONTEXT: "#F1F3F6", L.LEER: "#F5F6F8"}
RAND = {L.ROT: "#F2D3CF", L.GELB: "#F0DFB8", L.GRUEN: "#CDE7D7", L.KONTEXT: "#E1E5EA", L.LEER: "#E3E6EB"}
TREND_FARBE = {"besser": "#2F855A", "schlechter": "#B93A32", "neutral": "#6B7685"}
NAVY, BLAU, STAHL, GOLD, PETROL = "#14325C", "#1F4E8C", "#8FA6C4", "#B08D57", "#2F7F7A"
PALETTE = [BLAU, GOLD, PETROL, STAHL, "#8E5572", "#9AA3AF"]
INK, TEXT, MUTED, LINIE, SOFT = "#101828", "#1D2533", "#667085", "#E3E6EB", "#F5F6F8"
SCHRIFT = "Inter, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
KOPF_ZEILE = "Portfoliosimulation FHGR · Makroanalyse USA"
PLOT_CFG = {"displayModeBar": False}


def css():
    st.markdown(f"""
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Source+Serif+4:opsz,wght@8..60,500;8..60,600&display=swap');
      .block-container {{padding-top: 2.4rem; padding-bottom: 4rem; max-width: 1360px;}}
      [data-testid="stDecoration"], .stAppDeployButton {{display: none;}}
      @media print {{
        section[data-testid="stSidebar"], header[data-testid="stHeader"], [data-testid="stSidebarCollapsedControl"],
        .stButton, .stDownloadButton, iframe[height="40"], [data-testid="stToolbar"] {{display: none !important;}}
        .block-container {{max-width: 100% !important; padding-top: 0 !important;}}
        details {{display: block;}}
      }}
      .kopf, .kpi, .karte, .badge, .signal, .ticker, .klein, .pill, .nutzer, .login {{font-family: {SCHRIFT};}}

      /* Seitenkopf */
      .kopf {{margin: 0.2rem 0 1.5rem; padding-bottom: 1.05rem; border-bottom: 1px solid {LINIE};}}
      .kopf-over {{font-size: 0.7rem; font-weight: 600; letter-spacing: 0.13em; text-transform: uppercase; color: {BLAU};
                  margin-bottom: 0.5rem;}}
      .kopf-titel {{font-family: 'Source Serif 4', Georgia, 'Times New Roman', serif; font-weight: 600; font-size: 2.1rem;
                   line-height: 1.15; color: {INK}; letter-spacing: -0.01em;}}
      .kopf-sub {{font-size: 0.86rem; color: {MUTED}; margin-top: 0.5rem; line-height: 1.5;}}

      /* Überschriften innerhalb der Seiten */
      [data-testid="stHeading"] h3, .stMarkdown h3 {{font-size: 1.12rem; font-weight: 600; color: {INK}; letter-spacing: -0.005em;}}
      [data-testid="stHeading"] h4, .stMarkdown h4 {{font-size: 0.74rem; font-weight: 600; color: {MUTED}; letter-spacing: 0.11em;
                                   text-transform: uppercase; padding-top: 1.1rem; margin-bottom: 0.2rem;}}
      .stMarkdown blockquote {{border-left: 3px solid {BLAU}; background: {SOFT}; padding: 0.7rem 1rem; border-radius: 0 8px 8px 0;
                              color: {TEXT}; margin: 0.4rem 0 0.6rem;}}
      hr {{margin: 1.6rem 0 !important; border-color: {LINIE} !important;}}

      /* Badges und Pills */
      .badge {{display: inline-flex; align-items: center; gap: 5px; padding: 0 8px 0 7px; border-radius: 999px; font-size: 0.72rem;
              font-weight: 600; line-height: 1.6; white-space: nowrap; border: 1px solid transparent; vertical-align: 1px;}}
      .badge::before {{content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor;}}
      .pill {{display: inline-block; padding: 0 8px; border-radius: 999px; font-size: 0.72rem; font-weight: 600; margin-right: 4px;
             line-height: 1.6; border: 1px solid transparent;}}

      /* Kennzahlen */
      .kpi-grid {{display: grid; grid-template-columns: repeat(var(--n), minmax(0, 1fr)); gap: 14px; margin: 0 0 1.3rem;}}
      .kpi {{background: #fff; border: 1px solid {LINIE}; border-radius: 10px; padding: 14px 16px 13px; min-width: 0;}}
      .kpi-name {{font-size: 0.68rem; font-weight: 600; letter-spacing: 0.09em; text-transform: uppercase; color: {MUTED};
                 line-height: 1.3;}}
      .kpi-wert {{font-size: 1.55rem; font-weight: 600; line-height: 1.25; margin: 6px 0 3px; color: {INK};
                 font-variant-numeric: tabular-nums; letter-spacing: -0.015em;}}
      .kpi-sub {{font-size: 0.76rem; color: {MUTED}; line-height: 1.4;}}
      .kpi .badge {{margin-right: 6px;}}

      /* Karten (Kapitel) */
      .karten {{display: grid; grid-template-columns: repeat(var(--n, 4), minmax(0, 1fr)); gap: 14px; margin-bottom: 0.6rem;}}
      .karte {{border: 1px solid {LINIE}; border-radius: 10px; padding: 14px 16px 12px; background: #fff; display: flex;
              flex-direction: column; min-width: 0;}}
      .karte-titel {{font-weight: 600; font-size: 0.93rem; color: {INK}; margin-bottom: 7px;}}
      .karte-zeile {{display: flex; justify-content: space-between; gap: 8px; font-size: 0.8rem; padding: 6px 0;
                    border-top: 1px solid #EFF1F4; color: {TEXT};}}
      .karte-zeile > span:last-child {{font-weight: 600; white-space: nowrap; font-variant-numeric: tabular-nums;}}
      .punkt {{display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 7px; vertical-align: 1px;}}
      .karte-text {{font-size: 0.78rem; color: {MUTED}; padding-top: 8px; margin-top: auto; line-height: 1.5;
                   border-top: 1px solid #EFF1F4;}}

      /* Signale */
      .signal {{border: 1px solid {RAND[L.ROT]}; border-left: 3px solid {FARBE[L.ROT]}; background: #FFFBFA; padding: 9px 12px;
               border-radius: 8px; margin-bottom: 8px; font-size: 0.88rem; color: {INK};}}
      .signal.gelb {{border-color: {RAND[L.GELB]}; border-left-color: {FARBE[L.GELB]}; background: #FFFDF7;}}
      .signal b {{font-weight: 600;}}
      .kap-titel {{font-weight: 600; font-size: 1rem; color: {INK};}}
      .klein {{font-size: 0.8rem; color: {MUTED}; line-height: 1.5;}}

      /* Marktleiste */
      .ticker {{display: flex; flex-wrap: wrap; gap: 3px 20px; font-size: 0.77rem; color: {TEXT}; background: {SOFT};
               border: 1px solid {LINIE}; border-radius: 8px; padding: 7px 14px; margin-bottom: 1.1rem;
               font-variant-numeric: tabular-nums;}}
      .ticker span.t {{white-space: nowrap;}}
      .ticker b {{font-weight: 600; color: {MUTED}; margin-right: 3px;}}

      /* Seitenleiste */
      section[data-testid="stSidebar"] {{border-right: 1px solid {LINIE};}}
      [data-testid="stSidebarNavSeparator"] {{display: none;}}
      [data-testid="stNavSectionHeader"] {{font-size: 0.68rem; font-weight: 600; letter-spacing: 0.11em; text-transform: uppercase;
                                         color: {MUTED};}}
      .nutzer {{border: 1px solid {LINIE}; background: #fff; border-radius: 10px; padding: 10px 12px; margin: 2px 0 6px;}}
      .nutzer-label {{font-size: 0.66rem; letter-spacing: 0.1em; text-transform: uppercase; color: {MUTED}; font-weight: 600;}}
      .nutzer-name {{font-size: 0.98rem; font-weight: 600; color: {INK}; margin-top: 2px;}}
      .status {{display: flex; align-items: center; gap: 7px; font-size: 0.78rem; color: {TEXT}; margin: 4px 0;}}
      .status::before {{content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--c);}}

      /* Tabs, Expander, Tabellen */
      .stTabs [data-baseweb="tab-list"] {{gap: 6px; border-bottom: 1px solid {LINIE};}}
      .stTabs [data-baseweb="tab"] {{font-weight: 500;}}
      [data-testid="stExpander"] details {{border-color: {LINIE}; border-radius: 10px;}}
      [data-testid="stExpander"] summary p {{font-weight: 500;}}
      [data-testid="stCaptionContainer"] {{color: {MUTED};}}

      /* Anmeldung */
      .login {{text-align: center; margin: 7vh 0 1.6rem;}}
      .login-logo {{display: inline-flex; width: 46px; height: 46px; border-radius: 11px; background: {NAVY}; color: #fff;
                   align-items: center; justify-content: center; font-family: 'Source Serif 4', Georgia, serif; font-size: 1.45rem;
                   font-weight: 600; margin-bottom: 14px;}}
      .login .kopf-over {{margin-bottom: 0.45rem;}}
      .login .kopf-titel {{font-size: 1.85rem;}}
    </style>""", unsafe_allow_html=True)


def seitenkopf(titel: str, sub: str | None = None, over: str | None = None):
    """Einheitlicher Seitenkopf: Kopfzeile, Titel (Serifenschrift), Untertitel."""
    sub_html = f'<div class="kopf-sub">{sub}</div>' if sub else ""
    st.markdown(f'<div class="kopf"><div class="kopf-over">{html.escape(over or KOPF_ZEILE)}</div>'
                f'<div class="kopf-titel">{html.escape(titel)}</div>{sub_html}</div>', unsafe_allow_html=True)


LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="232" height="40" viewBox="0 0 232 40">'
    '<rect x="0" y="2" width="36" height="36" rx="8" fill="#14325C"/>'
    '<text x="18" y="27.5" text-anchor="middle" font-family="Georgia, \'Times New Roman\', serif" font-size="21" '
    'font-weight="700" fill="#FFFFFF">M</text>'
    '<text x="48" y="19" font-family="Helvetica, Arial, sans-serif" font-size="15" font-weight="700" fill="#101828">'
    'Makro-Dashboard USA</text>'
    '<text x="48" y="34" font-family="Helvetica, Arial, sans-serif" font-size="9.5" font-weight="600" letter-spacing="1.3" '
    'fill="#667085">PORTFOLIOSIMULATION FHGR</text></svg>')
ICON_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="36" height="36" viewBox="0 0 36 36">'
            '<rect width="36" height="36" rx="8" fill="#14325C"/><text x="18" y="25.5" text-anchor="middle" '
            'font-family="Georgia, serif" font-size="21" font-weight="700" fill="#FFFFFF">M</text></svg>')


def logo():
    try:
        st.logo(LOGO_SVG, size="large", icon_image=ICON_SVG)
    except Exception:  # noqa: BLE001 – Logo ist nur Dekoration
        pass


def status_zeile(text: str, farbe: str):
    st.markdown(f'<div class="status" style="--c:{farbe}">{html.escape(text)}</div>', unsafe_allow_html=True)


def ampel_txt(a) -> str:
    return f"● {a}" if a in (L.ROT, L.GELB, L.GRUEN, L.KONTEXT) else "– keine Daten"


def ampel_stil(df: pd.DataFrame, spalten: list[str]):
    """Styler: färbt Zellen, die mit «● Rot/Gelb/Grün/Kontext» oder «● aktiv» beginnen."""
    def f(v):
        v = str(v)
        for k in (L.ROT, L.GELB, L.GRUEN, L.KONTEXT):
            if v.startswith(f"● {k}"):
                return f"color: {FARBE[k]}; font-weight: 600"
        if v.startswith("● aktiv"):
            return f"color: {FARBE[L.ROT]}; font-weight: 600"
        return ""
    return df.style.map(f, subset=[c for c in spalten if c in df.columns])


def de(x, d: int = 1, vorzeichen: bool = False) -> str:
    """Zahl im Schweizer/deutschen Format (Komma, Apostroph, echtes Minus)."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    if round(x, d) == 0:
        x = 0.0
    s = f"{x:{'+' if vorzeichen else ''},.{d}f}".replace(",", "'").replace(".", ",")
    return s.replace("-", "−")


def kopf_kpis(items: list[tuple[str, str, str]]):
    """Kopfzeile mit grossen Kennzahlen: (Titel, Wert als HTML, Untertitel als HTML)."""
    karten = "".join(f'<div class="kpi"><div class="kpi-name">{html.escape(t)}</div><div class="kpi-wert">{w}</div>'
                     f'<div class="kpi-sub">{u}</div></div>' for t, w, u in items)
    st.markdown(f'<div class="kpi-grid" style="--n:{len(items)}">{karten}</div>', unsafe_allow_html=True)


def zahl_block(titel: str, wert: str, sub: str = ""):
    st.markdown(f'<div style="margin-bottom:14px"><div class="kpi-name">{html.escape(titel)}</div>'
                f'<div class="kpi-wert" style="margin:3px 0 1px">{html.escape(wert)}</div><div class="kpi-sub">{html.escape(sub)}</div></div>',
                unsafe_allow_html=True)


def badge(a: str, text: str | None = None) -> str:
    return (f'<span class="badge" style="color:{FARBE.get(a, MUTED)}; background:{HELL.get(a, SOFT)}; '
            f'border-color:{RAND.get(a, LINIE)}">{html.escape(text or a)}</span>')


def datum_txt(d) -> str:
    if d is None or (isinstance(d, float) and math.isnan(d)) or pd.isna(d):
        return "–"
    return pd.Timestamp(d).strftime("%d.%m.%Y")


def kpi_karte_html(row: pd.Series, ind: dict) -> str:
    a = row["ampel"]
    trend = ""
    if row["pfeil"]:
        trend = (f'<span style="color:{TREND_FARBE[row["trend"]]}; font-weight:600">{row["pfeil"]}</span> '
                 f'<span class="kpi-sub">vorher {html.escape(row["vor_anzeige"])}</span>')
    herkunft = row["herkunft"] + (f" · {row['person']}" if row["person"] else "")
    farbe = FARBE[a] if a in (L.ROT, L.GELB) else INK
    return (f'<div class="kpi"><div class="kpi-name" style="min-height:2.6em">{html.escape(ind["name"])}</div>'
            f'<div class="kpi-wert" style="color:{farbe}">{html.escape(row["anzeige"])}</div>'
            f'<div>{badge(a)}{trend}</div>'
            f'<div class="kpi-sub" style="margin-top:7px">Stand {datum_txt(row["datum"])} · {html.escape(herkunft)}</div></div>')


def kpi_karte(row: pd.Series, ind: dict):
    st.markdown(kpi_karte_html(row, ind), unsafe_allow_html=True)


def kpi_raster(st_df: pd.DataFrame, ids: list[str], spalten: int = 4):
    karten = "".join(kpi_karte_html(st_df.loc[i], C.IND_BY_ID[i]) for i in ids)
    st.markdown(f'<div class="kpi-grid" style="--n:{spalten}">{karten}</div>', unsafe_allow_html=True)


def stil(fig, legende: bool | None = None):
    """Einheitliche Diagramm-Optik (Schrift, Farben, Raster, Tooltip) ohne Höhe/Ränder zu ändern."""
    fig.update_layout(font=dict(family=SCHRIFT, size=12, color=TEXT), colorway=PALETTE, plot_bgcolor="white",
                      paper_bgcolor="white", hoverlabel=dict(bgcolor="white", bordercolor=LINIE,
                                                             font=dict(family=SCHRIFT, size=12, color=INK)),
                      legend=dict(orientation="h", y=-0.15, font=dict(size=11, color=MUTED)))
    if legende is not None:
        fig.update_layout(showlegend=legende)
    fig.update_xaxes(showgrid=False, linecolor="#D0D5DD", tickfont=dict(color=MUTED), title_font=dict(color=MUTED, size=11))
    fig.update_yaxes(gridcolor="#EEF0F3", zeroline=False, linecolor="#D0D5DD", tickfont=dict(color=MUTED),
                     title_font=dict(color=MUTED, size=11))
    return fig


def zeige(fig, **kw):
    stil(fig)
    st.plotly_chart(fig, width="stretch", config=PLOT_CFG, **kw)


def _layout(fig, hoehe=300, legende=False):
    fig.update_layout(height=hoehe, margin=dict(l=8, r=8, t=28, b=8), showlegend=legende, hovermode="x unified")
    return stil(fig)


def indikator_chart(ind: dict, df: pd.DataFrame, rezession: pd.Series | None, jahre: int = 5):
    if df.empty:
        st.info("Noch keine Daten – FRED nicht erreichbar oder noch kein Team-Eintrag.")
        return
    start = pd.Timestamp.today() - pd.DateOffset(years=jahre)
    d = df[df.index >= start]
    if d.empty:
        d = df.tail(12)
    fig = go.Figure()
    if rezession is not None and not rezession.empty:
        r = rezession[rezession.index >= d.index.min()]
        _rezessionsbalken(fig, r)
    fig.add_trace(go.Scatter(x=d.index, y=d["wert"], mode="lines" if len(d) > 30 else "lines+markers",
                             line=dict(color=BLAU, width=2.2), name=ind["name"],
                             hovertemplate="%{y:.2f} " + ind["einheit"] + "<extra></extra>"))
    team = d[d["herkunft"] == "Team"]
    if not team.empty:
        fig.add_trace(go.Scatter(x=team.index, y=team["wert"], mode="markers", name="Team-Eintrag",
                                 marker=dict(color="#B08D57", size=9, symbol="diamond"),
                                 customdata=team["person"], hovertemplate="Team (%{customdata}): %{y:.2f}<extra></extra>"))
    seed = d[d["herkunft"].str.startswith("Analyse")]
    if not seed.empty:
        fig.add_trace(go.Scatter(x=seed.index, y=seed["wert"], mode="markers", name="Wert aus Analyse",
                                 marker=dict(color="#94A3B8", size=7), hovertemplate="Analyse: %{y:.2f}<extra></extra>"))
    for lvl, farbe, txt in ((ind.get("gelb"), FARBE[L.GELB], "Gelb"), (ind.get("rot"), FARBE[L.ROT], "Rot")):
        if lvl is not None:
            fig.add_hline(y=lvl, line=dict(color=farbe, dash="dash", width=1.5),
                          annotation_text=f"{txt} {lvl:g}", annotation_position="top left",
                          annotation_font=dict(color=farbe, size=11))
    fig.add_vline(x=pd.Timestamp(C.SIMULATION_START).timestamp() * 1000, line=dict(color="#94A3B8", dash="dot", width=1))
    _layout(fig, 290, legende=not team.empty or not seed.empty)
    fig.update_layout(title=dict(text=f"{ind['einheit']}", font=dict(size=11, color=MUTED), x=0))
    st.plotly_chart(fig, width="stretch", config=PLOT_CFG)


def _rezessionsbalken(fig, rez: pd.Series):
    if rez is None or rez.empty:
        return
    an = False
    beginn = None
    for t, v in rez.items():
        if v >= 1 and not an:
            an, beginn = True, t
        elif v < 1 and an:
            fig.add_vrect(x0=beginn, x1=t, fillcolor="#D5DBE4", opacity=0.45, line_width=0)
            an = False
    if an:
        fig.add_vrect(x0=beginn, x1=rez.index[-1], fillcolor="#D5DBE4", opacity=0.45, line_width=0)


def zyklus_chart(team: pd.DataFrame, modell: dict, hoehe=260):
    x = np.linspace(0, 1, 300)
    y = np.sin(2 * np.pi * (x - 0.25))
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines", line=dict(color="#CBD5E1", width=3), hoverinfo="skip", showlegend=False))
    farben = [BLAU, GOLD, PETROL, "#8E5572", STAHL, "#4A7FA8"]
    for i, (_, r) in enumerate(team.iterrows()):
        xs = np.linspace(r["von"], r["bis"], 40)
        fig.add_trace(go.Scatter(x=xs, y=np.sin(2 * np.pi * (xs - 0.25)) + 0.06 * (i - (len(team) - 1) / 2),
                                 mode="lines", line=dict(color=farben[i % len(farben)], width=9),
                                 name=r["person"], opacity=0.75,
                                 hovertemplate=f"{html.escape(str(r['person']))}: {r['von']:.2f}–{r['bis']:.2f}<extra></extra>"))
    p = modell["pos"]
    fig.add_trace(go.Scatter(x=[p], y=[np.sin(2 * np.pi * (p - 0.25))], mode="markers+text", text=["Modell"],
                             textposition="top center", marker=dict(size=14, color="#101828", symbol="x"),
                             name="Regelbasiertes Modell", hovertemplate=f"Modell: {p:.2f}<extra></extra>"))
    for pos, txt in ((0.0, "Tief"), (0.25, "Erholung"), (0.5, "Hochphase"), (0.75, "Abschwung"), (1.0, "Tief")):
        fig.add_annotation(x=pos, y=-1.35, text=txt, showarrow=False, font=dict(size=11, color="#556070"))
    _layout(fig, hoehe, legende=True)
    fig.update_xaxes(visible=False, range=[-0.03, 1.03])
    fig.update_yaxes(visible=False, range=[-1.55, 1.35])
    fig.update_layout(hovermode="closest")
    st.plotly_chart(fig, width="stretch", config=PLOT_CFG)


def szenario_chart(szen: pd.DataFrame, themen=None, hoehe=None):
    """Gestapelter Balken Base/Bull/Bear (gültige Wahrscheinlichkeiten)."""
    fig = go.Figure()
    for sid in szen.index:
        r = szen.loc[sid]
        spanne = "" if r["n"] == 0 else f" · Einzelschätzungen {r['p_min']:.0f}–{r['p_max']:.0f} % (n={r['n']})"
        fig.add_trace(go.Bar(y=["Szenarien"], x=[r["p"]], orientation="h", name=r["name"],
                             marker=dict(color=C.SZEN_FARBE.get(sid, BLAU), line=dict(color="white", width=1)),
                             text=f"{r['kurz']} · {r['p']:.0f} %", textposition="inside", insidetextanchor="middle",
                             textfont=dict(size=12, color="white"),
                             hovertemplate=f"{html.escape(r['name'])}: {r['p']:.0f} %{spanne} · {html.escape(r['basis'])}<extra></extra>"))
    _layout(fig, hoehe or 100)
    fig.update_layout(barmode="stack", hovermode="closest")
    fig.update_xaxes(range=[0, 100], ticksuffix=" %")
    fig.update_yaxes(visible=False)
    st.plotly_chart(fig, width="stretch", config=PLOT_CFG)


def risiko_matrix(ris: pd.DataFrame, hoehe=360):
    fig = go.Figure()
    for x0, x1, y0, y1, c in ((0, 25, 0.5, 3, "#F2F8F4"), (25, 100, 0.5, 3, "#FDF9EF"),
                              (0, 25, 3, 5.5, "#FDF9EF"), (25, 100, 3, 5.5, "#FCF3F2")):
        fig.add_shape(type="rect", x0=x0, x1=x1, y0=y0, y1=y1, fillcolor=c, line_width=0, layer="below")
    d = ris.dropna(subset=["p", "impact"]).copy()
    # Punkte mit gleicher Position leicht auseinanderschieben
    d["px"] = d["p"].astype(float)
    for _, grp in d.groupby([d["p"].round(0), d["impact"].round(1)]):
        if len(grp) > 1:
            for k, idx in enumerate(grp.index):
                d.loc[idx, "px"] = grp.loc[idx, "p"] + (k - (len(grp) - 1) / 2) * 3.5
    farbe = np.where(d["signal"], FARBE[L.ROT], np.where(d.get("art", pd.Series("Risiko", index=d.index)) == "Chance", FARBE[L.GRUEN], BLAU))
    spanne = [("" if r["n"] == 0 else f"<br>Team: {r['p_min']:.0f}–{r['p_max']:.0f} % (n={r['n']})") for _, r in d.iterrows()]
    fig.add_trace(go.Scatter(
        x=d["px"], y=d["impact"], mode="markers+text", text=[str(i + 1) for i in range(len(d))],
        textposition="middle center", textfont=dict(size=11, color="white"),
        marker=dict(size=20 + d["score"] * 16, color=farbe, line=dict(color="white", width=1.5)),
        customdata=np.stack([d["name"], d["warnsignal"], spanne, d["p"].round(0).astype(int).astype(str)], axis=-1),
        hovertemplate="<b>%{customdata[0]}</b><br>Wahrscheinlichkeit %{customdata[3]} % · Auswirkung %{y:.1f}%{customdata[2]}"
                      "<br>Warnsignal: %{customdata[1]}<extra></extra>"))
    _layout(fig, hoehe)
    fig.update_layout(hovermode="closest")
    fig.update_xaxes(range=[0, 70], title="Wahrscheinlichkeit bis 28.02.2027 (%)", ticksuffix=" %")
    fig.update_yaxes(range=[0.5, 5.5], title="Auswirkung (1–5)", dtick=1)
    st.plotly_chart(fig, width="stretch", config=PLOT_CFG)
    legende = " · ".join(f"**{i + 1}** {n}" for i, n in enumerate(d["name"]))
    st.markdown(f"<span class='klein'>{legende}</span>".replace("**", ""), unsafe_allow_html=True)
    st.caption("Rot = Warnsignal aktiv (automatisch oder vom Team gemeldet), grün = Chance (Richtung Bull). Punktgrösse = Wahrscheinlichkeit × Auswirkung.")


def sektor_chart(sek: pd.DataFrame, hoehe=360):
    d = sek.sort_values("score")
    farben = [FARBE[L.GRUEN] if s > C.SEKTOR_SCHWELLE else FARBE[L.ROT] if s < -C.SEKTOR_SCHWELLE else "#9CA3AF"
              for s in d["score"]]
    fig = go.Figure(go.Bar(x=d["score"], y=d["sektor"], orientation="h", marker_color=farben,
                           text=[de(s, 2, True) for s in d["score"]], textposition="outside",
                           customdata=d["beitraege"], hovertemplate="%{y}: %{x:+.2f}<br>%{customdata}<extra></extra>"))
    for v in (C.SEKTOR_SCHWELLE, -C.SEKTOR_SCHWELLE):
        fig.add_vline(x=v, line=dict(color="#CBD5E1", dash="dot"))
    m = max(1.5, float(d["score"].abs().max()) + 0.5)
    _layout(fig, hoehe)
    fig.update_layout(hovermode="closest")
    fig.update_xaxes(range=[-m, m], title="Aktives Gewicht gegenüber S&P 500 (PP der Aktien)", zeroline=True,
                     zerolinecolor="#94A3B8")
    st.plotly_chart(fig, width="stretch", config=PLOT_CFG)


def allokation_donut(aktien: float, neutral: float, cash: float = 0.0, neutral_cash: float = 0.0):
    tr = 100 - aktien - cash
    fig = go.Figure(go.Pie(values=[aktien, tr, cash], labels=["Aktien (S&P 500)", "Treasuries", "Cash"], hole=0.62,
                           marker=dict(colors=[BLAU, STAHL, "#E3E6EB"]), sort=False, textinfo="none",
                           hovertemplate="%{label}: %{value:.0f} %<extra></extra>"))
    fig.add_annotation(text=f"<b>{aktien:.0f} / {tr:.0f} / {cash:.0f}</b><br><span style='font-size:11px'>Aktien / Treas. / Cash<br>"
                            f"neutral {neutral:.0f} / {100 - neutral - neutral_cash:.0f} / {neutral_cash:.0f}</span>",
                       showarrow=False, font=dict(size=16, color=INK))
    fig.update_layout(height=220, margin=dict(l=0, r=0, t=0, b=0), showlegend=False)
    zeige(fig)


def iframe_html(code: str, hoehe: int):
    """HTML mit Skript einbetten (neue Streamlit-Versionen: st.iframe, ältere: components.html)."""
    if hasattr(st, "iframe"):
        st.iframe(code, height=max(1, int(hoehe)))
    else:
        import streamlit.components.v1 as components

        components.html(code, height=hoehe)


def stand_abgleich(name: str, stand: str, prefixe: tuple[str, ...], wer: str = "") -> None:
    """Hat jemand anderes inzwischen gespeichert, werden die Eingabefelder neu geladen – sonst würde man mit
    veralteten Werten dessen Änderungen überschreiben."""
    k = f"_stand_{name}"
    alt = st.session_state.get(k)
    if alt is not None and alt != stand:
        for key in [x for x in list(st.session_state.keys()) if str(x).startswith(prefixe)]:
            del st.session_state[key]
        if not st.session_state.get("_selbst_dieser_lauf", False):
            st.info(f"Inzwischen neu gespeichert{(' von ' + wer) if wer else ''} – die Felder zeigen jetzt den neusten Stand.",
                    icon=":material/sync:")
    st.session_state[k] = stand
