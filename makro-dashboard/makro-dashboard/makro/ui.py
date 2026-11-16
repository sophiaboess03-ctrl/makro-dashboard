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

FARBE = {L.ROT: "#D64545", L.GELB: "#E0A100", L.GRUEN: "#2E9E5B", L.KONTEXT: "#7A8699", L.LEER: "#B8BEC8"}
HELL = {L.ROT: "#FBE9E9", L.GELB: "#FDF4DC", L.GRUEN: "#E5F4EA", L.KONTEXT: "#EEF0F3", L.LEER: "#F3F4F6"}
TREND_FARBE = {"besser": "#2E9E5B", "schlechter": "#D64545", "neutral": "#7A8699"}
BLAU = "#2F5DA8"
PLOT_CFG = {"displayModeBar": False}


def css():
    st.markdown("""
    <style>
      .block-container {padding-top: 1.6rem; max-width: 1400px;}
      .badge {display:inline-block; padding:1px 9px; border-radius:999px; font-size:0.78rem; font-weight:600;
              color:white; white-space:nowrap;}
      .kpi-name {font-size:0.82rem; color:#5A6475; line-height:1.2; min-height:2.1em;}
      .kpi-wert {font-size:1.55rem; font-weight:700; line-height:1.25; margin:2px 0;}
      .kpi-sub {font-size:0.76rem; color:#7A8699;}
      .pill {display:inline-block; padding:0 7px; border-radius:6px; font-size:0.75rem; margin-right:4px;}
      .signal {border-left:4px solid #D64545; background:#FBE9E9; padding:6px 10px; border-radius:6px; margin-bottom:6px;}
      .signal.gelb {border-left-color:#E0A100; background:#FDF4DC;}
      .kap-titel {font-weight:700; font-size:1.02rem;}
      .klein {font-size:0.8rem; color:#5A6475;}
    </style>""", unsafe_allow_html=True)


def de(x, d: int = 1, vorzeichen: bool = False) -> str:
    """Zahl im Schweizer/deutschen Format (Komma, Apostroph, echtes Minus)."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    s = f"{x:{'+' if vorzeichen else ''},.{d}f}".replace(",", "'").replace(".", ",")
    return s.replace("-", "−")


def kopf_kpis(items: list[tuple[str, str, str]]):
    """Kopfzeile mit grossen Kennzahlen: (Titel, Wert, Untertitel)."""
    cols = st.columns(len(items))
    for c, (t, w, u) in zip(cols, items):
        with c.container(border=True):
            st.markdown(f'<div class="kpi-name">{html.escape(t)}</div><div class="kpi-wert">{w}</div>'
                        f'<div class="kpi-sub">{u}</div>', unsafe_allow_html=True)


def zahl_block(titel: str, wert: str, sub: str = ""):
    st.markdown(f'<div style="margin-bottom:10px"><div class="kpi-name" style="min-height:0">{html.escape(titel)}</div>'
                f'<div class="kpi-wert">{html.escape(wert)}</div><div class="kpi-sub">{html.escape(sub)}</div></div>',
                unsafe_allow_html=True)


def badge(a: str) -> str:
    return f'<span class="badge" style="background:{FARBE.get(a, "#999")}">{html.escape(a)}</span>'


def datum_txt(d) -> str:
    if d is None or (isinstance(d, float) and math.isnan(d)) or pd.isna(d):
        return "–"
    return pd.Timestamp(d).strftime("%d.%m.%Y")


def kpi_karte(row: pd.Series, ind: dict):
    a = row["ampel"]
    with st.container(border=True):
        trend = ""
        if row["pfeil"]:
            trend = (f'<span style="color:{TREND_FARBE[row["trend"]]}; font-weight:700">{row["pfeil"]}</span> '
                     f'<span class="kpi-sub">vorher {html.escape(row["vor_anzeige"])}</span>')
        herkunft = row["herkunft"] + (f" · {row['person']}" if row["person"] else "")
        st.markdown(
            f'<div class="kpi-name">{html.escape(ind["name"])}</div>'
            f'<div class="kpi-wert" style="color:{FARBE[a] if a in (L.ROT, L.GELB) else "#1F2937"}">{html.escape(row["anzeige"])}</div>'
            f'<div>{badge(a)} {trend}</div>'
            f'<div class="kpi-sub" style="margin-top:4px">Stand {datum_txt(row["datum"])} · {html.escape(herkunft)}</div>',
            unsafe_allow_html=True)


def kpi_raster(st_df: pd.DataFrame, ids: list[str], spalten: int = 4):
    for i in range(0, len(ids), spalten):
        cols = st.columns(spalten)
        for c, ind_id in zip(cols, ids[i:i + spalten]):
            with c:
                kpi_karte(st_df.loc[ind_id], C.IND_BY_ID[ind_id])


def _layout(fig, hoehe=300, legende=False):
    fig.update_layout(height=hoehe, margin=dict(l=8, r=8, t=28, b=8), plot_bgcolor="white", paper_bgcolor="white",
                      showlegend=legende, hovermode="x unified", font=dict(size=12),
                      legend=dict(orientation="h", y=-0.15))
    fig.update_xaxes(showgrid=False, linecolor="#D0D5DD")
    fig.update_yaxes(gridcolor="#EEF0F3", zeroline=False)
    return fig


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
                             line=dict(color=BLAU, width=2), name=ind["name"],
                             hovertemplate="%{y:.2f} " + ind["einheit"] + "<extra></extra>"))
    team = d[d["herkunft"] == "Team"]
    if not team.empty:
        fig.add_trace(go.Scatter(x=team.index, y=team["wert"], mode="markers", name="Team-Eintrag",
                                 marker=dict(color="#7C3AED", size=9, symbol="diamond"),
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
    fig.update_layout(title=dict(text=f"{ind['einheit']}", font=dict(size=11, color="#7A8699"), x=0))
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
            fig.add_vrect(x0=beginn, x1=t, fillcolor="#CBD5E1", opacity=0.35, line_width=0)
            an = False
    if an:
        fig.add_vrect(x0=beginn, x1=rez.index[-1], fillcolor="#CBD5E1", opacity=0.35, line_width=0)


def zyklus_chart(team: pd.DataFrame, modell: dict, hoehe=260):
    x = np.linspace(0, 1, 300)
    y = np.sin(2 * np.pi * (x - 0.25))
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines", line=dict(color="#CBD5E1", width=3), hoverinfo="skip", showlegend=False))
    farben = ["#2F5DA8", "#7C3AED", "#0E9F6E", "#E07B00", "#BE185D", "#0891B2"]
    for i, (_, r) in enumerate(team.iterrows()):
        xs = np.linspace(r["von"], r["bis"], 40)
        fig.add_trace(go.Scatter(x=xs, y=np.sin(2 * np.pi * (xs - 0.25)) + 0.06 * (i - (len(team) - 1) / 2),
                                 mode="lines", line=dict(color=farben[i % len(farben)], width=9),
                                 name=r["person"], opacity=0.75,
                                 hovertemplate=f"{html.escape(str(r['person']))}: {r['von']:.2f}–{r['bis']:.2f}<extra></extra>"))
    p = modell["pos"]
    fig.add_trace(go.Scatter(x=[p], y=[np.sin(2 * np.pi * (p - 0.25))], mode="markers+text", text=["Modell"],
                             textposition="top center", marker=dict(size=14, color="#111827", symbol="x"),
                             name="Regelbasiertes Modell", hovertemplate=f"Modell: {p:.2f}<extra></extra>"))
    for pos, txt in ((0.0, "Tief"), (0.25, "Erholung"), (0.5, "Hochphase"), (0.75, "Abschwung"), (1.0, "Tief")):
        fig.add_annotation(x=pos, y=-1.35, text=txt, showarrow=False, font=dict(size=11, color="#5A6475"))
    _layout(fig, hoehe, legende=True)
    fig.update_xaxes(visible=False, range=[-0.03, 1.03])
    fig.update_yaxes(visible=False, range=[-1.55, 1.35])
    fig.update_layout(hovermode="closest")
    st.plotly_chart(fig, width="stretch", config=PLOT_CFG)


def szenario_chart(szen: pd.DataFrame, themen: list[str] | None = None, hoehe=None):
    themen = themen or [t["id"] for t in C.THEMEN]
    fig = go.Figure()
    palette = ["#2F5DA8", "#E07B00", "#0E9F6E", "#9CA3AF"]
    for t in themen:
        d = szen[szen["thema"] == t]
        for j, (_, r) in enumerate(d.iterrows()):
            spanne = "" if r["n"] == 0 else f" · Team {r['p_min']:.0f}–{r['p_max']:.0f} % (n={r['n']})"
            fig.add_trace(go.Bar(y=[C.THEMA_BY_ID[t]["name"]], x=[r["p"]], orientation="h", name=r["name"],
                                 marker=dict(color=palette[j % len(palette)], line=dict(color="white", width=1)),
                                 text=f"{r['name'].replace('Basis: ', 'Basis: ')}<br>{r['p']:.0f} %", textposition="inside",
                                 insidetextanchor="middle", textfont=dict(size=11),
                                 hovertemplate=f"{html.escape(r['name'])}: {r['p']:.0f} %{spanne} · {r['basis']}<extra></extra>"))
    _layout(fig, hoehe or 70 + 62 * len(themen))
    fig.update_layout(barmode="stack", hovermode="closest")
    fig.update_xaxes(range=[0, 100], ticksuffix=" %")
    fig.update_yaxes(autorange="reversed")
    st.plotly_chart(fig, width="stretch", config=PLOT_CFG)


def risiko_matrix(ris: pd.DataFrame, hoehe=360):
    fig = go.Figure()
    for x0, x1, y0, y1, c in ((0, 25, 0.5, 3, "#E5F4EA"), (25, 100, 0.5, 3, "#FDF4DC"),
                              (0, 25, 3, 5.5, "#FDF4DC"), (25, 100, 3, 5.5, "#FBE9E9")):
        fig.add_shape(type="rect", x0=x0, x1=x1, y0=y0, y1=y1, fillcolor=c, line_width=0, layer="below")
    d = ris.dropna(subset=["p", "impact"]).copy()
    # Punkte mit gleicher Position leicht auseinanderschieben
    d["px"] = d["p"].astype(float)
    for _, grp in d.groupby([d["p"].round(0), d["impact"].round(1)]):
        if len(grp) > 1:
            for k, idx in enumerate(grp.index):
                d.loc[idx, "px"] = grp.loc[idx, "p"] + (k - (len(grp) - 1) / 2) * 3.5
    farbe = np.where(d["signal"], FARBE[L.ROT], BLAU)
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
    st.caption("Rot = Warnsignal aktiv (automatisch oder vom Team gemeldet). Punktgrösse = Wahrscheinlichkeit × Auswirkung.")


def sektor_chart(sek: pd.DataFrame, hoehe=360):
    d = sek.sort_values("score")
    farben = [FARBE[L.GRUEN] if s > C.SEKTOR_SCHWELLE else FARBE[L.ROT] if s < -C.SEKTOR_SCHWELLE else "#9CA3AF"
              for s in d["score"]]
    fig = go.Figure(go.Bar(x=d["score"], y=d["sektor"], orientation="h", marker_color=farben,
                           text=[f"{s:+.2f}" for s in d["score"]], textposition="outside",
                           customdata=d["beitraege"], hovertemplate="%{y}: %{x:+.2f}<br>%{customdata}<extra></extra>"))
    for v in (C.SEKTOR_SCHWELLE, -C.SEKTOR_SCHWELLE):
        fig.add_vline(x=v, line=dict(color="#CBD5E1", dash="dot"))
    m = max(1.5, float(d["score"].abs().max()) + 0.5)
    _layout(fig, hoehe)
    fig.update_layout(hovermode="closest")
    fig.update_xaxes(range=[-m, m], title="Score (> +0,5 übergewichten · < −0,5 untergewichten)", zeroline=True,
                     zerolinecolor="#94A3B8")
    st.plotly_chart(fig, width="stretch", config=PLOT_CFG)


def allokation_donut(aktien: float, neutral: float, cash: float = 0.0, neutral_cash: float = 0.0):
    tr = 100 - aktien - cash
    fig = go.Figure(go.Pie(values=[aktien, tr, cash], labels=["Aktien (S&P 500)", "Treasuries", "Cash"], hole=0.62,
                           marker=dict(colors=[BLAU, "#94A3B8", "#E5E7EB"]), sort=False, textinfo="none",
                           hovertemplate="%{label}: %{value:.0f} %<extra></extra>"))
    fig.add_annotation(text=f"<b>{aktien:.0f} / {tr:.0f} / {cash:.0f}</b><br><span style='font-size:11px'>Aktien / Treas. / Cash<br>"
                            f"neutral {neutral:.0f} / {100 - neutral - neutral_cash:.0f} / {neutral_cash:.0f}</span>",
                       showarrow=False, font=dict(size=16))
    fig.update_layout(height=220, margin=dict(l=0, r=0, t=0, b=0), showlegend=False)
    st.plotly_chart(fig, width="stretch", config=PLOT_CFG)
