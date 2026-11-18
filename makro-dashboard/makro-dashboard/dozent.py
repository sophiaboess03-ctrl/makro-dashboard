"""Erwartungen: die Monatsfragen des Dozenten, beantwortet aus euren Szenarien.

Ablauf: Szenario-Parameter × Wahrscheinlichkeiten → Simulation (20'000 Ziehungen, Streuung im Szenario aus den Daten)
→ Wahrscheinlichkeit jeder Antwort → Vorschlag für Antwort und Überzeugung. Das Team entscheidet und begründet.
Nach Monatsende prüft die App die Antworten mit denselben Quellen wie der Dozent (Yahoo, FRED).
"""
from __future__ import annotations

import html
import json
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import seitenpdf as SP
import taktik as T
from makro import config as C
from makro import szenarien as SZ
from makro import ui
from storage import jetzt, zahl


# ---------------------------------------------------------------------------
# Prognose je Monat (gecacht)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False, max_entries=20)
def _prognose(defs_json: str, p_json: str, sig_json: str, cpi_json: str, bond_json: str, monat_id: str) -> dict:
    defs = json.loads(defs_json)
    p = json.loads(p_json)
    szen = pd.DataFrame({"p": p})
    sig = {k: tuple(v) for k, v in json.loads(sig_json).items()}
    cpi = json.loads(cpi_json)
    if cpi:
        cpi["daten_monat"] = pd.Timestamp(cpi["daten_monat"])
    sim = SZ.simuliere(defs, szen, sig, cpi or None, json.loads(bond_json))
    out = {"fragen": {}, "n": len(sim)}
    for f in C.FRAGEN:
        if f["art"] == "sektoren":
            sek = SZ.sektor_wahrscheinlichkeiten(sim)
            out["sektoren"] = sek.to_dict(orient="list")
            continue
        probs = SZ.antwort_wahrscheinlichkeiten(sim, f)
        x = sim[f["groesse"]]
        i = int(np.argmax(probs))
        je = {sid: SZ.antwort_wahrscheinlichkeiten(sim[sim["szenario"] == sid], f) for sid in defs if (sim["szenario"] == sid).any()}
        out["fragen"][f["nr"]] = dict(probs=probs, erwartung=float(x.mean()), p10=float(x.quantile(0.1)), p90=float(x.quantile(0.9)),
                                      vorschlag=f["optionen"][i][0], p_vorschlag=probs[i],
                                      ueberzeugung=SZ.ueberzeugung_vorschlag(probs[i]), je_szenario=je)
    if "cpi_yoy_neu" in sim and sim["cpi_yoy_neu"].notna().any():
        out["cpi_yoy_neu"] = float(sim["cpi_yoy_neu"].mean())
    return out


def prognose(ctx, monat_id: str) -> dict:
    defs = {sid: dict(param=s["param"], sektoren=s["sektoren"]) for sid, s in ctx.szdef.items()}
    cpi = SZ.cpi_basis(ctx.fred, monat_id)
    cpi_j = json.dumps({**cpi, "daten_monat": str(cpi["daten_monat"])} if cpi else {}, default=str)
    out = _prognose(json.dumps(defs, sort_keys=True), json.dumps({s: float(ctx.szen.loc[s, "p"]) for s in defs}),
                    json.dumps(ctx.sig), cpi_j, json.dumps(ctx.bond, default=float), monat_id)
    out = dict(out)
    out["cpi"] = cpi
    if "sektoren" in out:
        out["sektoren_df"] = pd.DataFrame(out["sektoren"]).set_index("sektor", drop=False)
    return out


# ---------------------------------------------------------------------------
# Team-Antworten
# ---------------------------------------------------------------------------
def antworten(tab: pd.DataFrame | None, monat_id: str) -> dict:
    if tab is None or tab.empty:
        return {}
    d = tab[tab["monat"] == monat_id].sort_values("zeit").drop_duplicates("frage", keep="last")
    out = {}
    for _, r in d.iterrows():
        key = r["frage"] if r["frage"] == "haupt" else int(float(r["frage"]))
        out[key] = dict(antwort=r["antwort"], ueberzeugung=r["ueberzeugung"], begruendung=r["begruendung"], person=r["person"],
                        zeit=str(r["zeit"]), modell_p=zahl(r["modell_p"]), szenario_p=r.get("szenario_p", ""))
    return out


def antworten_kurz(tab, monat_id) -> dict:
    """Für andere Seiten: {nr: Antworttext, '12_oben': [...], '12_unten': [...]}"""
    a = antworten(tab, monat_id)
    out = {}
    for k, v in a.items():
        if k == 12:
            try:
                j = json.loads(v["antwort"])
                out["12_oben"], out["12_unten"] = j.get("oben", []), j.get("unten", [])
            except (ValueError, TypeError):
                pass
        elif k != "haupt":
            out[k] = v["antwort"]
    return out


# ---------------------------------------------------------------------------
# Auflösung (gleiche Quellen wie der Dozent)
# ---------------------------------------------------------------------------
def _ret(s: pd.Series | None, start: pd.Timestamp, ende: pd.Timestamp):
    if s is None or s.dropna().empty:
        return None
    s = s.dropna()
    a, b = s[s.index < start], s[s.index <= ende]
    if a.empty or b.empty or b.index[-1] < start:
        return None
    return (float(b.iloc[-1]) / float(a.iloc[-1]) - 1) * 100


def _diff(s, start, ende, faktor=1.0):
    if s is None or s.dropna().empty:
        return None
    s = s.dropna()
    a, b = s[s.index < start], s[s.index <= ende]
    if a.empty or b.empty or b.index[-1] < start:
        return None
    return (float(b.iloc[-1]) - float(a.iloc[-1])) * faktor


def ist_werte(monat_id: str, fred: dict, hist: dict) -> tuple[dict, str]:
    start = pd.Timestamp(monat_id + "-01")
    ende = start + pd.offsets.MonthEnd(0)
    heute = pd.Timestamp.today().normalize()
    if heute < start:
        return {}, "Monat hat noch nicht begonnen."
    stand = "endgültig" if heute > ende else f"Zwischenstand {heute:%d.%m.%Y}"
    w = {}
    w["sp500"] = _ret(hist.get("^SP500TR"), start, ende)
    anl = _ret(hist.get("GOVT"), start, ende)
    w["aktien_minus_anleihen"] = None if w["sp500"] is None or anl is None else w["sp500"] - anl
    w["vix"] = _diff(hist.get("^VIX") if hist.get("^VIX") is not None else fred.get("VIXCLS"), start, ende)
    w["fed"] = _diff(fred.get("DFEDTARU"), start, ende, 100)
    w["dgs10"] = _diff(fred.get("DGS10"), start, ende, 100)
    w["wti"] = _ret(hist.get("CL=F"), start, ende)
    g, v = _ret(hist.get("IWF"), start, ende), _ret(hist.get("IWD"), start, ende)
    w["growth_value"] = None if g is None or v is None else g - v
    sek = {s: _ret(hist.get(C.SEKTOR_ETF[s]), start, ende) for s in C.SEKTOREN}
    if all(x is not None for x in sek.values()):
        w["sektoren"] = sek
        w["def_minus_zykl"] = np.mean([sek[x] for x in C.DEFENSIV]) - np.mean([sek[x] for x in C.ZYKLISCH])
    cpi = fred.get("CPIAUCSL")
    if cpi is not None and heute > start + pd.Timedelta(days=20):
        c = cpi.dropna()
        c.index = c.index.to_period("M").to_timestamp()
        x = start - pd.DateOffset(months=1)
        try:
            y1 = (c.loc[x] / c.loc[x - pd.DateOffset(months=12)] - 1) * 100
            y0 = (c.loc[x - pd.DateOffset(months=1)] / c.loc[x - pd.DateOffset(months=13)] - 1) * 100
            w["cpi_yoy_diff"] = round(y1, 1) - round(y0, 1)
        except KeyError:
            pass
    return w, stand


def treffer(f: dict, ist: float) -> str | None:
    for text, lo, hi in f["optionen"]:
        if (lo is None or ist > lo) and (hi is None or ist <= hi):
            return text
    return None


# ---------------------------------------------------------------------------
# Texte
# ---------------------------------------------------------------------------
def _fmt(f, x):
    e = f["einheit"]
    d = 0 if e in ("bp",) else 2 if f["groesse"] == "cpi_yoy_diff" else 1
    return f"{ui.de(x, d, True)} {e}"


def anker_text(ctx, f: dict, prog: dict, monat: dict) -> str:
    st_ = ctx.status
    g = f["groesse"]

    def w(i):
        return st_.loc[i, "anzeige"] if i in st_.index else "–"
    if g == "sp500":
        return f"S&P 500 heute {w('sp500')} ({w('sp500_dd')} vom Hoch) · Aktienrisikoprämie {w('erp')} · Forward-KGV {w('kgv')}"
    if g == "aktien_minus_anleihen":
        return (f"Anleihen-Benchmark ≈ {ui.de(ctx.bond['rendite'], 2)} % Rendite, Duration {ui.de(ctx.bond['duration'], 1)} J. "
                f"→ Carry {ui.de(ctx.bond['rendite'] / 12, 2)} % im Monat · ERP {w('erp')}")
    if g == "vix":
        return f"VIX heute {w('vix')} · historisch kehrt der VIX zum Mittel (≈ 18–20) zurück"
    if g == "fed":
        return (f"Zielband-Obergrenze heute {w('fedfunds')} · Sitzung: {monat['fed_sitzung'] or 'keine'} · Markt-Erwartung "
                "siehe «Was ist eingepreist?»")
    if g == "dgs10":
        return f"10 J. heute {w('dgs10')} · 2 J. {w('dgs2')} · Laufzeitprämie {w('laufzeitpraemie')}"
    if g == "cpi_yoy_diff":
        c = prog.get("cpi")
        if not c:
            return f"CPI heute {w('cpi')} · keine Indexdaten für den Basiseffekt"
        t = (f"Im {monat['name'].split()[0]} erscheint der CPI für {c['daten_monat']:%m/%Y}. Vorherige Jahresrate "
             f"{ui.de(c['yoy_vor'], 1) + ' %' if c['yoy_vor'] is not None else 'n. v.'}; aus der Basis fällt "
             f"{(c['daten_monat'] - pd.DateOffset(months=12)):%m/%Y} mit {ui.de(c['basis_mm'], 2)} % m/m heraus → steigt die "
             f"neue Monatsrate über {ui.de(c['basis_mm'], 2)} %, steigt die Jahresrate.")
        if c.get("interpoliert"):
            t += f" Fehlende Monate interpoliert: {', '.join(c['interpoliert'])}."
        if c.get("info"):
            t += " " + c["info"]
        return t
    if g == "wti":
        return f"Brent heute {w('brent')} · WTI über Yahoo CL=F"
    if g == "growth_value":
        return f"Forward-KGV {w('kgv')} · 10 J. {w('dgs10')} (höhere Zinsen belasten eher Growth)"
    if g == "def_minus_zykl":
        return f"Zyklusphase {ctx.zyklus_modell['phase']} · VIX {w('vix')}"
    return ""


def begruendung_entwurf(ctx, f: dict, q: dict) -> str:
    defs, szen = ctx.szdef, ctx.szen
    teile = []
    for sid in defs:
        if f["groesse"] in ("aktien_minus_anleihen", "def_minus_zykl", "cpi_yoy_diff"):
            continue
        v = defs[sid]["param"].get(f["groesse"])
        if v is not None:
            teile.append(f"{defs[sid]['kurz']} ({szen.loc[sid, 'p']:.0f} %): {_fmt(f, v)}")
    base = (f"Szenarien: {'; '.join(teile)}. " if teile else "") + \
        f"Gewichtet erwartet {_fmt(f, q['erwartung'])} (80 %-Band {_fmt(f, q['p10'])} bis {_fmt(f, q['p90'])}). " + \
        f"Modell: «{q['vorschlag']}» mit {q['p_vorschlag']:.0f} %."
    return base


def hauptszenario_entwurf(ctx, monat_id: str, alte: dict) -> str:
    defs, szen = ctx.szdef, ctx.szen
    sid = szen["p"].idxmax()
    s = defs[sid]
    t = f"{s['name']} ({szen.loc[sid, 'p']:.0f} %). {s['narrativ']} "
    i = [m["id"] for m in C.MONATE].index(monat_id)
    vor = antworten(ctx.tabellen.get("erwartungen"), C.MONATE[i - 1]["id"]).get("haupt") if i > 0 else None
    if vor and vor.get("szenario_p"):
        try:
            alt = json.loads(vor["szenario_p"])
            aend = [f"{defs[k]['kurz']} {alt[k]:.0f} → {szen.loc[k, 'p']:.0f} %" for k in defs if k in alt and abs(alt[k] - szen.loc[k, "p"]) >= 1]
            t += "Gegenüber dem Vormonat: " + ("; ".join(aend) if aend else "Wahrscheinlichkeiten unverändert") + "."
        except (ValueError, TypeError, KeyError):
            pass
    else:
        t += "Erster Monat der Simulation (kein Vormonat)."
    return t


# ---------------------------------------------------------------------------
# Seite
# ---------------------------------------------------------------------------
def _balken(probs, optionen, team=None):
    fig = go.Figure(go.Bar(x=probs, y=[o[0] for o in optionen], orientation="h",
                           marker_color=[ui.BLAU if (team and o[0] == team) else "#A9B8CF" for o in optionen],
                           text=[f"{p:.0f} %" for p in probs], textposition="outside", cliponaxis=False))
    fig.update_layout(height=40 + 32 * len(optionen), margin=dict(l=4, r=40, t=4, b=4), plot_bgcolor="white", showlegend=False)
    fig.update_xaxes(range=[0, 100], visible=False)
    fig.update_yaxes(autorange="reversed", tickfont=dict(size=11))
    st.plotly_chart(fig, width="stretch", config=ui.PLOT_CFG)


def seite_erwartungen(ctx):
    R = SP.Sammler("Erwartungen (Dozentenfragen)", "Monatsfragen des Dozenten, abgeleitet aus den Szenarien", kurz="Erwartungen")
    ui.seitenkopf("Erwartungen – Fragen des Dozenten",
                  "Aus euren Szenarien berechnet die App, wie wahrscheinlich jede Antwort ist. Ihr entscheidet, wählt die Überzeugung "
                  "und begründet. Ändert ihr Szenarien oder Wahrscheinlichkeiten, ändern sich die Modellwerte hier sofort.")
    ph = st.empty()
    ids = [m["id"] for m in C.MONATE]
    m_id = st.segmented_control("Monat", ids, default=T.monat_in_arbeit()["id"], format_func=lambda i: C.MONAT_BY_ID[i]["name"],
                                key="erw_monat") or T.monat_in_arbeit()["id"]
    m = C.MONAT_BY_ID[m_id]
    txt, offen = T.frist_text(m)
    (st.info if offen else st.warning)(txt, icon=":material/schedule:")
    prog = prognose(ctx, m_id)
    team = antworten(ctx.tabellen.get("erwartungen"), m_id)
    fragen = [f for f in C.FRAGEN if not (f["art"] == "fed" and not m["fed_sitzung"])]
    n_ok = sum(1 for f in fragen if f["nr"] in team) + (1 if "haupt" in team else 0)
    p_txt = " · ".join(f"{ctx.szdef[s]['kurz']} {ctx.szen.loc[s, 'p']:.0f} %" for s in ctx.szdef)
    kpis = [("Beantwortet", f"{n_ok} / {len(fragen) + 1}", "inkl. Hauptszenario"),
            ("Szenarien", p_txt, ctx.szen["basis"].iloc[0][:60]),
            ("Simulation", f"{prog['n']:,}".replace(",", "'") + " Ziehungen", f"Streuung im Szenario {C.STREUUNG_IM_SZENARIO * 100:.0f} % der historischen"),
            ("Überzeugung", " / ".join(f"{n} {w} %" for n, w in C.UEBERZEUGUNG), "Stufen bitte mit Dozenten-App abgleichen")]
    ui.kopf_kpis(kpis)
    R.text(f"{m['name']} · {txt}")
    R.kpis(kpis)
    if not m["fed_sitzung"]:
        st.caption(f"Im {m['name']} gibt es keine Fed-Sitzung – Frage 4 entfällt.")

    gruppe = None
    eingaben = {}
    zusammenfassung = []
    for f in fragen:
        if f["gruppe"] != gruppe:
            gruppe = f["gruppe"]
            st.markdown(f"### {gruppe}")
            R.h(gruppe)
        key = f"erw_{m_id}_{f['nr']}"
        alt = team.get(f["nr"])
        with st.container(border=True):
            st.markdown(f"**Frage {f['nr']}: {f['titel']}**")
            c1, c2 = st.columns([1.05, 1])
            if f["art"] == "sektoren":
                sek = prog.get("sektoren_df")
                with c1:
                    if sek is not None:
                        d = sek.copy()
                        st.dataframe(pd.DataFrame({
                            "Sektor": d["sektor"].map(C.SEKTOR_DOZENT),
                            "Erw. vs. S&P": d["erwartet"].map(lambda x: f"{ui.de(x, 1, True)} PP"),
                            "P(oben 3)": d["p_oben"].map(lambda x: f"{x:.0f} %"), "P(unten 3)": d["p_unten"].map(lambda x: f"{x:.0f} %")}),
                            hide_index=True, width="stretch", height=420)
                        vor_oben = list(d.sort_values("p_oben", ascending=False)["sektor"][:3])
                        vor_unten = list(d.sort_values("p_unten", ascending=False)["sektor"][:3])
                        st.caption(f"Vorschlag oben: {', '.join(vor_oben)} · unten: {', '.join(vor_unten)}")
                with c2:
                    try:
                        j = json.loads(alt["antwort"]) if alt else {}
                    except (ValueError, TypeError):
                        j = {}
                    oben = st.multiselect("Oben: schlagen den S&P 500 am stärksten", C.SEKTOREN, default=j.get("oben", []),
                                          max_selections=3, key=f"{key}_oben", format_func=lambda s: C.SEKTOR_DOZENT[s])
                    unten = st.multiselect("Unten: schneiden am schwächsten ab", [s for s in C.SEKTOREN if s not in oben],
                                           default=[s for s in j.get("unten", []) if s not in oben], max_selections=3,
                                           key=f"{key}_unten", format_func=lambda s: C.SEKTOR_DOZENT[s])
                    p_m = float(np.mean([sek.loc[s, "p_oben"] for s in oben] + [sek.loc[s, "p_unten"] for s in unten])) if sek is not None and (oben or unten) else 0
                    vor_u = SZ.ueberzeugung_vorschlag(p_m) if p_m else "mittel"
                    stufen = [n for n, _ in C.UEBERZEUGUNG]
                    ub = st.selectbox("Überzeugung", stufen, index=stufen.index(alt["ueberzeugung"]) if alt and alt["ueberzeugung"] in stufen
                                      else stufen.index(vor_u), format_func=SZ.stufe_text, key=f"{key}_u",
                                      help=f"Modell: mittlere Wahrscheinlichkeit eurer Wahl {p_m:.0f} %")
                    entwurf = (f"Szenario-gewichtete Sektorwirkung: oben {', '.join(vor_oben)}, unten {', '.join(vor_unten)}."
                               if sek is not None else "")
                    begr = st.text_area("Begründung", value=alt["begruendung"] if alt else entwurf, key=f"{key}_b", height=90)
                antwort = json.dumps({"oben": oben, "unten": unten}, ensure_ascii=False) if len(oben) == 3 and len(unten) == 3 else None
                modell_p, vorschlag = p_m, f"oben {', '.join(vor_oben)} / unten {', '.join(vor_unten)}" if sek is not None else ""
                anzeige = f"oben: {', '.join(oben) or '–'} · unten: {', '.join(unten) or '–'}"
                if not (len(oben) == 3 and len(unten) == 3):
                    st.caption("Je genau 3 Sektoren wählen, sonst wird die Frage nicht gespeichert.")
            else:
                q = prog["fragen"][f["nr"]]
                with c1:
                    _balken(q["probs"], f["optionen"], alt["antwort"] if alt else None)
                    st.markdown(f"<span class='klein'>Gewichtet erwartet <b>{_fmt(f, q['erwartung'])}</b> · 80 %-Band "
                                f"{_fmt(f, q['p10'])} bis {_fmt(f, q['p90'])}<br>Je Szenario: " +
                                " · ".join(f"{ctx.szdef[s]['kurz']}: {f['optionen'][int(np.argmax(v))][0]} ({max(v):.0f} %)"
                                           for s, v in q["je_szenario"].items()) +
                                f"<br>Daten: {html.escape(anker_text(ctx, f, prog, m))}</span>", unsafe_allow_html=True)
                with c2:
                    opts = [o[0] for o in f["optionen"]]
                    idx = opts.index(alt["antwort"]) if alt and alt["antwort"] in opts else opts.index(q["vorschlag"])
                    wahl = st.radio("Antwort", opts, index=idx, key=f"{key}_a", horizontal=False)
                    p_w = q["probs"][opts.index(wahl)]
                    stufen = [n for n, _ in C.UEBERZEUGUNG]
                    vor_u = SZ.ueberzeugung_vorschlag(p_w)
                    ub = st.selectbox("Überzeugung", stufen, index=stufen.index(alt["ueberzeugung"]) if alt and alt["ueberzeugung"] in stufen
                                      else stufen.index(vor_u), format_func=SZ.stufe_text, key=f"{key}_u",
                                      help=f"Modell-Wahrscheinlichkeit eurer Antwort: {p_w:.0f} % → Vorschlag «{vor_u}»")
                    if wahl != q["vorschlag"]:
                        st.caption(f"Abweichung vom Modell («{q['vorschlag']}», {q['p_vorschlag']:.0f} %) – in der Begründung erklären.")
                    begr = st.text_area("Begründung (mind. 30 Zeichen)", value=alt["begruendung"] if alt else begruendung_entwurf(ctx, f, q),
                                        key=f"{key}_b", height=90)
                antwort, modell_p, vorschlag, anzeige = wahl, p_w, q["vorschlag"], wahl
            st.markdown(f"<span class='klein'>Auflösung: {html.escape(f['aufloesung'])} Bezug zum Portfolio: {html.escape(f['portfolio'])}"
                        + (f" · Zuletzt gespeichert: {html.escape(alt['person'])}, {alt['zeit'][:16].replace('T', ' ')}" if alt else
                           " · noch nicht gespeichert") + "</span>", unsafe_allow_html=True)
        eingaben[f["nr"]] = dict(antwort=antwort, ueberzeugung=ub, begruendung=begr, modell_p=modell_p, modell_antwort=vorschlag)
        zusammenfassung.append((f"{f['nr']}", f["titel"][:70], anzeige, SZ.stufe_text(ub), f"{modell_p:.0f} %", begr))
        R.h2(f"Frage {f['nr']}: {f['titel']}")
        R.text(f"Antwort: {anzeige} · Überzeugung {SZ.stufe_text(ub)} · Modell {modell_p:.0f} %")
        R.text(begr, klein=True)

    st.markdown("### Hauptszenario")
    alt = team.get("haupt")
    haupt = st.text_area("Welches Szenario erwartet ihr für den Monat, und was hat sich gegenüber dem Vormonat geändert? (mind. 40 Zeichen)",
                         value=alt["begruendung"] if alt else hauptszenario_entwurf(ctx, m_id, team), height=130, key=f"erw_{m_id}_haupt")
    R.h("Hauptszenario")
    R.text(haupt)

    c1, c2 = st.columns([1, 3])
    if c1.button("Alle Antworten speichern", type="primary", disabled=ctx.nur_lesen, icon=":material/save:"):
        z = jetzt()
        snap = json.dumps({s: round(float(ctx.szen.loc[s, "p"]), 1) for s in ctx.szdef})
        zeilen, fehlt = [], []
        for nr, e in eingaben.items():
            if e["antwort"] is None:
                fehlt.append(str(nr))
                continue
            if len(str(e["begruendung"]).strip()) < 30:
                fehlt.append(f"{nr} (Begründung < 30 Zeichen)")
            zeilen.append(dict(zeit=z, person=ctx.person, monat=m_id, frage=nr, antwort=e["antwort"], ueberzeugung=e["ueberzeugung"],
                               begruendung=e["begruendung"].strip(), modell_p=round(e["modell_p"], 1), modell_antwort=e["modell_antwort"],
                               szenario_p=snap))
        zeilen.append(dict(zeit=z, person=ctx.person, monat=m_id, frage="haupt", antwort="", ueberzeugung="", begruendung=haupt.strip(),
                           modell_p="", modell_antwort="", szenario_p=snap))
        if fehlt:
            st.session_state["meldung_erw"] = "Noch offen: Frage " + ", ".join(fehlt)
        ctx.speichern("erwartungen", zeilen, f"Erwartungen {m['name']} gespeichert.")
    c2.caption("Gespeichert wird der Stand aller Fragen samt Momentaufnahme der Szenario-Wahrscheinlichkeiten. Es gilt jeweils der "
               "neuste Eintrag; ältere bleiben in der Google-Tabelle (Blatt «erwartungen»).")
    if msg := st.session_state.pop("meldung_erw", None):
        st.warning(msg)

    zus = pd.DataFrame(zusammenfassung, columns=["Nr.", "Frage", "Antwort", "Überzeugung", "Modell", "Begründung"])
    with st.expander("Übersicht zum Übertragen in die Dozenten-App", icon=":material/content_copy:"):
        st.dataframe(zus, hide_index=True, width="stretch")
    R.h("Übersicht")
    R.tabelle(zus.drop(columns=["Begründung"]), [10, 80, 50, 22, 18])

    # Auflösung
    ist, stand = ist_werte(m_id, ctx.fred, ctx.hist)
    if ist:
        st.markdown(f"### Auflösung ({stand})")
        zeilen = []
        for f in fragen:
            if f["art"] == "sektoren":
                if ist.get("sektoren"):
                    r = sorted(ist["sektoren"], key=ist["sektoren"].get, reverse=True)
                    a = team.get(12)
                    j = json.loads(a["antwort"]) if a else {}
                    n_t = len(set(j.get("oben", [])) & set(r[:3])) + len(set(j.get("unten", [])) & set(r[-3:]))
                    zeilen.append((12, f"oben {', '.join(r[:3])} / unten {', '.join(r[-3:])}", f"{n_t} von 6 richtig" if a else "–", ""))
                continue
            v = ist.get(f["groesse"])
            if v is None:
                zeilen.append((f["nr"], "noch keine Daten", "–", ""))
                continue
            richtig = treffer(f, v)
            a = team.get(f["nr"])
            zeilen.append((f["nr"], f"{_fmt(f, v)} → {richtig}", ("✓" if a and a["antwort"] == richtig else "✗") if a else "–",
                           f"{a['modell_p']:.0f} %" if a and a["modell_p"] is not None else ""))
        aufl = pd.DataFrame(zeilen, columns=["Frage", "Ergebnis", "Team", "Modell-W. eurer Antwort"])
        st.dataframe(aufl, hide_index=True, width="stretch")
        st.caption("Quellen wie beim Dozenten: Yahoo Finance (^SP500TR, ^VIX, CL=F, IWF, IWD, Sektor-ETFs als Näherung für die "
                   "Sektorindizes), FRED (DFEDTARU, DGS10, CPIAUCSL). Anleihen-Rendite über GOVT als Näherung des Treasury-Index.")
        R.h(f"Auflösung ({stand})")
        R.tabelle(aufl)
    with st.expander("So rechnet die App", icon=":material/functions:"):
        st.markdown(
            "1. **Szenarien:** Jedes Szenario hat für jede Grösse einen Monatswert (Seite «Szenarien»).\n"
            "2. **Simulation:** 20'000-mal wird ein Szenario nach seiner Wahrscheinlichkeit gezogen, dann jede Grösse mit der "
            f"historischen Streuung × {C.STREUUNG_IM_SZENARIO:g} gestreut (VIX und Sektoren hängen mit dem S&P 500 zusammen, "
            "2 J. mit 10 J.).\n"
            "3. **Abgeleitete Grössen:** Aktien gegen Anleihen = S&P 500 − (Carry − Duration × Renditeänderung der Benchmark); "
            "CPI-Jahresrate aus der Monatsrate und dem Basiseffekt (gerundet auf eine Stelle wie veröffentlicht); defensiv gegen "
            "zyklisch aus den Sektorwerten.\n"
            "4. **Antwort-Wahrscheinlichkeit** = Anteil der Ziehungen in jeder Antwort. **Vorschlag Überzeugung** = Stufe, deren "
            "Prozentwert am nächsten bei der Wahrscheinlichkeit eurer Antwort liegt.\n\n"
            "Streuung je Grösse: " + ", ".join(f"{C.PARAM_BY_ID[k]['name']} {ui.de(v[0], 2)} {C.PARAM_BY_ID[k]['einheit']} ({v[1]})"
                                                for k, v in ctx.sig.items() if k in C.PARAM_BY_ID))
    SP.knopf(ph, R, f"erw_{m_id}", f"Erwartungen_{m_id}.pdf")
