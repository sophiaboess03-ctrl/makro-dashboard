"""Seiten «Szenarien» (definieren, gewichten, Datenlage) und «Risiken & Chancen»."""
from __future__ import annotations

import html
import json

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import seitenpdf as SP
from makro import config as C
from makro import logic as L
from makro import ui
from makro import szenarien as SZm
from storage import jetzt, zahl

WIRKT_AUF = ("Änderungen wirken sofort auf: **Erwartungen** (Wahrscheinlichkeit jeder Antwort), **Taktik** (Orientierung neben "
             "jedem Feld), **Analyse → Anleihen & Cash** (Zinsszenarien), **Was ist eingepreist?** (eure Sicht gegen den Markt), "
             "**IPS** (Plausibilität von Rendite und Verlust) und das **Gesamtbild**.")


def szenario_balken(szen: pd.DataFrame, defs: dict, hoehe=110, spalte="p"):
    fig = go.Figure()
    for sid in defs:
        r = szen.loc[sid]
        spanne = "" if r["n"] == 0 else f" · Einzelschätzungen {r['p_min']:.0f}–{r['p_max']:.0f} % (n={r['n']})"
        fig.add_trace(go.Bar(y=["Szenarien"], x=[r[spalte]], orientation="h", name=defs[sid]["name"],
                             marker=dict(color=C.SZEN_FARBE[sid], line=dict(color="white", width=1)),
                             text=f"{defs[sid]['kurz']} · {r[spalte]:.0f} %", textposition="inside", insidetextanchor="middle",
                             textfont=dict(color="white", size=12),
                             hovertemplate=f"{html.escape(defs[sid]['name'])}: {r[spalte]:.0f} %{spanne}<extra></extra>"))
    fig.update_layout(barmode="stack", height=hoehe, margin=dict(l=4, r=4, t=4, b=4), plot_bgcolor="white", showlegend=False)
    fig.update_xaxes(range=[0, 100], ticksuffix=" %", gridcolor="#EEF0F3")
    fig.update_yaxes(visible=False)
    st.plotly_chart(fig, width="stretch", config=ui.PLOT_CFG)


def _wert(par, v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "–"
    return f"{ui.de(v, par['dez'], True)} {par['einheit']}"


# ---------------------------------------------------------------------------
# Szenarien
# ---------------------------------------------------------------------------
def seite_szenarien(ctx):
    defs, szen, lage, gew = ctx.szdef, ctx.szen, ctx.lage, ctx.gew
    R = SP.Sammler("Szenarien", "Base / Bull / Bear mit Parametern, Wahrscheinlichkeiten und Datenlage", kurz="Szenarien")
    ui.seitenkopf("Szenarien – Base, Bull, Bear",
                  "Ein gemeinsames Set für das ganze Dashboard. Jedes Szenario hat konkrete Monatswerte; der gewichtete Wert ist "
                  "Σ Wahrscheinlichkeit × Szenariowert. Die Datenlage zeigt, welches Szenario die aktuellen Zahlen stützen.")
    ph = st.empty()
    dt = ctx.tabellen.get("definitionen")
    stand = str(dt["zeit"].max()) if dt is not None and not dt.empty else ""
    ui.stand_abgleich("szenarien", stand, ("par_ed", "sek_ed", "ind_ed_"))
    st.info(WIRKT_AUF, icon=":material/account_tree:")

    fav = lage["_favorit"]
    kpis = [(defs[s]["name"].split(":")[0], f"{szen.loc[s, 'p']:.0f} %",
             f"Daten: {lage[s]['erfuellt']}/{lage[s]['n']} Indizien · daten-angepasst {lage[s]['p_daten']:.0f} %") for s in defs]
    kpis.append(("Datenlage stützt am stärksten", defs[fav]["kurz"], f"{lage[fav]['anteil'] * 100:.0f} % der Indizien erfüllt"))
    ui.kopf_kpis(kpis)
    R.kpis(kpis)
    st.caption(f"Gültige Wahrscheinlichkeiten: {szen['basis'].iloc[0]}.")
    if lage["_hinweis"]:
        h = lage["_hinweis"]
        st.warning(f"Die Daten sprechen stärker für **{defs[h]['name']}** als eure Wahrscheinlichkeit: daten-angepasst "
                   f"{lage[h]['p_daten']:.0f} % statt {szen.loc[h, 'p']:.0f} %. Prüfen, ob ihr umgewichten oder das Szenario anpassen müsst.")
    szenario_balken(szen, defs)

    kons = SZm.konsistenz(defs, szen)
    if any(k[0] == "fehler" for k in kons):
        st.error(f"Konsistenz-Check: {sum(k[0] == 'fehler' for k in kons)} Widerspruch/Widersprüche in den Szenarien – siehe Tab «Parameter».")
    t1, t2, t3, t4, t5, t6 = st.tabs(["Wahrscheinlichkeiten", "Parameter & gewichtete Werte", "Beschreibung", "Datenlage (Monitor)",
                                      "CFA-Check", "Verlauf"])
    # --- Wahrscheinlichkeiten -----------------------------------------------
    with t1:
        b = szen.attrs.get("beschluss")
        tab = pd.DataFrame({
            "Szenario": [defs[s]["name"] for s in defs],
            "Gültig": [f"{szen.loc[s, 'p']:.0f} %" for s in defs],
            "Team-Beschluss": [f"{szen.loc[s, 'p_beschluss']:.0f} %" if not pd.isna(szen.loc[s, "p_beschluss"]) else "–" for s in defs],
            "Mittel Einzelschätzungen": [f"{szen.loc[s, 'p_mittel']:.0f} %" if szen.loc[s, "n"] else "–" for s in defs],
            "Spanne": [f"{szen.loc[s, 'p_min']:.0f}–{szen.loc[s, 'p_max']:.0f} % (n={szen.loc[s, 'n']})" if szen.loc[s, "n"] else "–" for s in defs],
            "Daten-angepasst": [f"{lage[s]['p_daten']:.0f} %" for s in defs],
            "Startwert IC": [f"{szen.loc[s, 'start']:.0f} %" for s in defs]})
        st.dataframe(tab, hide_index=True, width="stretch")
        R.h("Wahrscheinlichkeiten")
        R.tabelle(tab)
        if b:
            st.caption(f"Team-Beschluss vom {b['zeit']} ({b['person']}): {b['begruendung']}")
            R.text(f"Begründung Team-Beschluss ({b['zeit']}, {b['person']}): {b['begruendung']}", klein=True)
        uneinig = szen[szen["uneinig"] >= 20]
        if len(uneinig):
            st.info("Grosse Uneinigkeit (≥ 20 PP): " + ", ".join(f"{r['kurz']} {r['p_min']:.0f}–{r['p_max']:.0f} %" for _, r in uneinig.iterrows()))
        c1, c2 = st.columns(2)
        with c1.container(border=True):
            st.markdown(f"**Deine Einzelschätzung** ({html.escape(ctx.person)})")
            v = ctx.tabellen.get("szenario_votes")
            eigen = {}
            if v is not None and not v.empty:
                d = v[(v["person"] == ctx.person) & v["szenario"].isin(C.SZEN_IDS)].sort_values("zeit").drop_duplicates("szenario", keep="last")
                eigen = {r["szenario"]: zahl(r["p"]) for _, r in d.iterrows()}
            with st.form("einzel"):
                werte = {s: st.slider(defs[s]["name"], 0, 100, int(round(eigen.get(s) or szen.loc[s, "p"])), 5) for s in defs}
                st.caption(f"Summe {sum(werte.values())} % – wird beim Speichern auf 100 % normiert.")
                if st.form_submit_button("Einschätzung speichern", disabled=ctx.nur_lesen):
                    tot = sum(werte.values()) or 1
                    ctx.speichern("szenario_votes", [dict(zeit=jetzt(), person=ctx.person, szenario=k, p=round(x / tot * 100, 1))
                                                     for k, x in werte.items()], "Einschätzung gespeichert.")
        with c2.container(border=True):
            st.markdown("**Team-Beschluss** (gilt fürs ganze Dashboard und die Dozentenfragen)")
            with st.form("beschluss"):
                start = {s: float(round(szen.loc[s, "p_mittel"] if szen.loc[s, "n"] else szen.loc[s, "p"])) for s in defs}
                cols = st.columns(3)
                werte_b = {s: c.number_input(defs[s]["kurz"], 0.0, 100.0, start[s], 5.0) for c, s in zip(cols, defs)}
                begr = st.text_area("Begründung (was hat sich geändert?)", height=80)
                gegen = st.text_area("Stärkstes Gegenargument (Advocatus Diaboli – gegen Bestätigungsfehler)", height=60)
                st.caption(f"Summe {ui.de(sum(werte_b.values()), 0)} % (muss 100 % sein). Startwerte = Mittel der Einzelschätzungen.")
                if st.form_submit_button("Beschluss speichern", type="primary", disabled=ctx.nur_lesen):
                    if abs(sum(werte_b.values()) - 100) > 0.5:
                        st.error("Die Summe muss 100 % sein.")
                    elif len(begr.strip()) < 15:
                        st.error("Bitte kurz begründen.")
                    elif len(gegen.strip()) < 10:
                        st.error("Bitte das stärkste Gegenargument festhalten (CFA: Bestätigungsfehler vermeiden).")
                    else:
                        ctx.speichern("szenario_beschluss", [dict(zeit=jetzt(), person=ctx.person,
                                                                  begruendung=f"{begr.strip()} | Gegenargument: {gegen.strip()}",
                                                                  **{s: x for s, x in werte_b.items()})], "Team-Beschluss gespeichert.")

    # --- Parameter ------------------------------------------------------------
    with t2:
        st.markdown("Monatswerte je Szenario. **Gewichtet** = Σ Wahrscheinlichkeit × Wert. **Daten-Anker** zeigt, was die "
                    "aktuellen Daten nahelegen (zur Kalibrierung). Werte ändern: Tabelle bearbeiten und speichern.")
        anker = ctx.anker
        zeilen = []
        for par in C.PARAMETER:
            z = {"id": par["id"], "Grösse": f"{par['name']} ({par['einheit']})"}
            for s in defs:
                z[defs[s]["kurz"]] = float(defs[s]["param"].get(par["id"], 0) or 0)
            z["Gewichtet"] = _wert(par, gew["param"][par["id"]])
            z["Daten-Anker"] = anker.get(par["id"], "–")
            z["Dozentenfrage"] = ", ".join(str(x) for x in par["fragen"]) or "–"
            zeilen.append(z)
        for par in C.ALLOK_PARAMETER:
            z = {"id": "alloc:" + par["id"], "Grösse": f"Taktik: {par['name']} ({par['einheit']})"}
            for s in defs:
                z[defs[s]["kurz"]] = float(defs[s]["alloc"].get(par["id"], 0) or 0)
            z["Gewichtet"] = f"{ui.de(gew['alloc'][par['id']], par['dez'], par['id'] == 'aktien_tilt')} {par['einheit']}"
            z["Daten-Anker"] = "–"
            z["Dozentenfrage"] = "Taktik"
            zeilen.append(z)
        df = pd.DataFrame(zeilen)
        kurz = [defs[s]["kurz"] for s in defs]
        ed = st.data_editor(df.drop(columns="id"), hide_index=True, width="stretch", key="par_ed",
                            disabled=["Grösse", "Gewichtet", "Daten-Anker", "Dozentenfrage"],
                            column_config={k: st.column_config.NumberColumn(k, format="%.2f") for k in kurz})
        st.markdown("**Sektorwirkung** (Rendite gegenüber dem S&P 500 im Monat, PP) – bestimmt Frage 12 und 14 und die "
                    "Sektor-Orientierung in der Taktik.")
        sz = pd.DataFrame([{"Sektor": sek, **{defs[s]["kurz"]: float(defs[s]["sektoren"].get(sek, 0) or 0) for s in defs},
                            "Gewichtet": f"{ui.de(gew['sektoren'][sek], 2, True)} PP",
                            "Korb": "defensiv" if sek in C.DEFENSIV else "zyklisch" if sek in C.ZYKLISCH else ""}
                           for sek in C.SEKTOREN])
        ed_s = st.data_editor(sz, hide_index=True, width="stretch", key="sek_ed", disabled=["Sektor", "Gewichtet", "Korb"],
                              column_config={k: st.column_config.NumberColumn(k, format="%.1f", step=0.5) for k in kurz})
        st.caption(f"Defensiv minus zyklisch je Szenario: " +
                   " · ".join(f"{defs[s]['kurz']} {ui.de(SZ_def(defs[s]), 2, True)} PP" for s in defs) +
                   f" · gewichtet {ui.de(gew['param']['def_minus_zykl'], 2, True)} PP")
        aend = []
        for (_, alt), (_, neu) in zip(df.iterrows(), ed.iterrows()):
            for s in defs:
                k = defs[s]["kurz"]
                if neu[k] is not None and not pd.isna(neu[k]) and abs(float(neu[k]) - float(alt[k])) > 1e-9:
                    feld = alt["id"] if alt["id"].startswith("alloc:") else f"param:{alt['id']}"
                    aend.append(dict(objekt=s, feld=feld, wert=float(neu[k])))
        for (_, alt), (_, neu) in zip(sz.iterrows(), ed_s.iterrows()):
            for s in defs:
                k = defs[s]["kurz"]
                if neu[k] is not None and not pd.isna(neu[k]) and abs(float(neu[k]) - float(alt[k])) > 1e-9:
                    aend.append(dict(objekt=s, feld=f"sektor:{alt['Sektor']}", wert=float(neu[k])))
        st.markdown("**Konsistenz-Check** – Szenarien müssen in sich stimmig sein (CFA: interne Konsistenz von Szenarien)")
        if not kons:
            st.success("Keine Widersprüche gefunden.")
        for a_, t_ in kons:
            (st.error if a_ == "fehler" else st.warning)(t_)
        R.h("Konsistenz-Check")
        R.text("; ".join(t_ for _, t_ in kons) or "Keine Widersprüche gefunden.")
        c1, c2 = st.columns([1, 3])
        if c1.button(f"Änderungen speichern ({len(aend)})", type="primary", disabled=not aend or ctx.nur_lesen, icon=":material/save:"):
            z = jetzt()
            ctx.speichern("definitionen", [dict(zeit=z, person=ctx.person, bereich="szenario", **a) for a in aend],
                          "Szenario-Parameter gespeichert.")
        geaendert = [(defs[s]["kurz"], f, wer, wann) for s in defs for f, (wer, wann) in defs[s]["geaendert"].items()]
        c2.caption(f"{len(geaendert)} Werte vom Team geändert, der Rest sind Startwerte (Richtung aus IC-Statement und "
                   "Themen-Szenarien, Höhe Vorschlag Claude)." if geaendert else
                   "Alles noch Startwerte: Richtung aus dem IC-Statement und den Themen-Szenarien, Höhe = Vorschlag Claude – im Team prüfen.")
        R.h("Parameter je Szenario (Monatswerte)")
        R.tabelle(df.drop(columns=["id"]).assign(**{k: df[k].map(lambda x: ui.de(x, 2, True)) for k in kurz}), [52, 18, 18, 18, 22, 40, 14])
        R.h("Sektorwirkung (PP gegenüber S&P 500)")
        R.tabelle(sz.assign(**{k: sz[k].map(lambda x: ui.de(x, 1, True)) for k in kurz}))

    # --- Beschreibung -----------------------------------------------------------
    with t3:
        cols = st.columns(3)
        for c, s in zip(cols, defs):
            d = defs[s]
            with c.container(border=True):
                st.markdown(f"<div style='border-top:4px solid {C.SZEN_FARBE[s]}; padding-top:6px'><b>{html.escape(d['name'])}</b> · "
                            f"{szen.loc[s, 'p']:.0f} %</div>", unsafe_allow_html=True)
                st.markdown(d["narrativ"])
                st.markdown(f"<span class='klein'><b>Annahmen:</b> {html.escape(d['annahmen'])}<br><b>Folge:</b> {html.escape(d['folge'])}"
                            f"<br><b>Aktion:</b> {html.escape(d['aktion'])}</span>", unsafe_allow_html=True)
                verkn = ctx.risiken[ctx.risiken["richtung"] == s]
                if len(verkn):
                    st.markdown("<span class='klein'><b>Verknüpfte Risiken/Chancen:</b> " + ", ".join(
                        f"{html.escape(r['name'])} ({r['p']:.0f} %{', Warnsignal!' if r['signal'] else ''})" for _, r in verkn.iterrows())
                        + "</span>", unsafe_allow_html=True)
                with st.popover("Bearbeiten", icon=":material/edit:", disabled=ctx.nur_lesen):
                    with st.form(f"txt_{s}"):
                        neu = {f: st.text_area(f.capitalize(), d[f], height=70 if f != "name" else 40) for f in
                               ("name", "kurz", "narrativ", "annahmen", "folge", "aktion")}
                        if st.form_submit_button("Speichern"):
                            z = jetzt()
                            rows = [dict(zeit=z, person=ctx.person, bereich="szenario", objekt=s, feld=f, wert=v.strip())
                                    for f, v in neu.items() if v.strip() != d[f]]
                            if rows:
                                ctx.speichern("definitionen", rows, "Beschreibung gespeichert.")
                R.h(f"{d['name']} ({szen.loc[s, 'p']:.0f} %)")
                R.text(d["narrativ"])
                R.text(f"Annahmen: {d['annahmen']}. Folge: {d['folge']}. Aktion: {d['aktion']}.", klein=True)

    # --- Datenlage --------------------------------------------------------------
    with t4:
        st.markdown("Für jedes Szenario: Welche Indizien sprechen heute dafür? Ein ausgelöstes Warnsignal eines verknüpften Risikos "
                    "zählt ebenfalls. **Daten-angepasst** = eure Wahrscheinlichkeit × (0,75 + 0,5 × Anteil erfüllter Indizien), normiert. "
                    "Das ist ein Hinweis zum Nachdenken, keine automatische Änderung.")
        R.h("Datenlage")
        cols = st.columns(3)
        for c, s in zip(cols, defs):
            with c.container(border=True):
                st.markdown(f"**{defs[s]['kurz']}** – {lage[s]['erfuellt']} von {lage[s]['n']} erfüllt · daten-angepasst "
                            f"{lage[s]['p_daten']:.0f} % ({ui.de(lage[s]['diff'], 0, True)} PP)")
                for z in lage[s]["zeilen"]:
                    farbe = ui.FARBE[L.GRUEN] if z["ok"] else ("#C5CAD3" if z["ok"] is None else ui.FARBE[L.ROT])
                    sym = "■" if z["ok"] else ("–" if z["ok"] is None else "□")
                    st.markdown(f"<span class='klein'><span style='color:{farbe}'>{sym}</span> {html.escape(z['text'])} "
                                f"<span style='color:#98A2B3'>(heute {html.escape(str(z['anzeige']))})</span></span>",
                                unsafe_allow_html=True)
                R.h2(f"{defs[s]['kurz']}: {lage[s]['erfuellt']}/{lage[s]['n']} Indizien, daten-angepasst {lage[s]['p_daten']:.0f} %")
                R.tabelle(pd.DataFrame([("ja" if z["ok"] else "–" if z["ok"] is None else "nein", z["text"], z["anzeige"])
                                        for z in lage[s]["zeilen"]], columns=["Erfüllt", "Indiz", "Heute"]), [16, 110, 54])
        with st.expander("Indizien bearbeiten", icon=":material/tune:"):
            s = st.selectbox("Szenario", list(defs), format_func=lambda x: defs[x]["name"], key="ind_sz")
            ind_ids = [i["id"] for i in C.INDIKATOREN]
            df = pd.DataFrame(defs[s]["indizien"] or [dict(ind="unrate", op="<", wert=4.3, text="")])
            df["wert"] = df["wert"].astype(str)
            ed = st.data_editor(df[["ind", "op", "wert", "text"]], num_rows="dynamic", hide_index=True, width="stretch", key=f"ind_ed_{s}",
                                column_config={"ind": st.column_config.SelectboxColumn("Indikator", options=ind_ids, required=True),
                                               "op": st.column_config.SelectboxColumn("Bedingung", options=C.INDIZ_OPS, required=True),
                                               "wert": st.column_config.TextColumn("Schwelle (bei «zwischen»: 0.2;0.4)"),
                                               "text": st.column_config.TextColumn("Beschreibung")})
            st.caption("Indikator-IDs: " + ", ".join(f"{i['id']} = {i['name']}" for i in C.INDIKATOREN[:60]))
            if st.button("Indizien speichern", disabled=ctx.nur_lesen):
                rows = [dict(ind=r["ind"], op=r["op"], wert=str(r["wert"]).replace(",", "."), text=r["text"] or r["ind"])
                        for _, r in ed.dropna(subset=["ind", "op"]).iterrows()]
                ctx.speichern("definitionen", [dict(zeit=jetzt(), person=ctx.person, bereich="szenario", objekt=s, feld="indizien",
                                                    wert=json.dumps(rows, ensure_ascii=False))], "Indizien gespeichert.")

    # --- CFA-Check -----------------------------------------------------------
    with t5:
        cfa_check(ctx, R)

    # --- Verlauf -----------------------------------------------------------
    with t6:
        b = ctx.tabellen.get("szenario_beschluss")
        if b is not None and not b.empty:
            d = b.sort_values("zeit").copy()
            d["zeit"] = pd.to_datetime(d["zeit"].str[:10])
            fig = go.Figure()
            for s in defs:
                fig.add_trace(go.Scatter(x=d["zeit"], y=d[s].map(zahl), name=defs[s]["kurz"], mode="lines+markers",
                                         line=dict(color=C.SZEN_FARBE[s], shape="hv")))
            fig.update_layout(height=280, margin=dict(l=8, r=8, t=10, b=8), plot_bgcolor="white")
            fig.update_yaxes(ticksuffix=" %", range=[0, 100], gridcolor="#EEF0F3")
            ui.zeige(fig)
            st.dataframe(pd.DataFrame({"Datum": d["zeit"].dt.strftime("%d.%m.%Y"), "Person": d["person"],
                                       **{defs[s]["kurz"]: d[s].map(lambda x: f"{zahl(x):.0f} %") for s in defs},
                                       "Begründung": d["begruendung"]}).iloc[::-1], hide_index=True, width="stretch")
        else:
            st.caption("Noch kein Team-Beschluss gespeichert.")
    SP.knopf(ph, R, "szenarien")


def cfa_check(ctx, R=None):
    """CME-Prozess (CFA Level III, Capital Market Expectations Part 1) und Verhaltensfehler beim Prognostizieren."""
    import dozent
    defs, szen, lage = ctx.szdef, ctx.szen, ctx.lage
    b = szen.attrs.get("beschluss")
    heute = pd.Timestamp.today().normalize()
    alter = (heute - pd.Timestamp(b["zeit"])).days if b else None
    erw = ctx.tabellen.get("erwartungen")
    n_erw = 0 if erw is None or erw.empty else erw["monat"].nunique()
    aufgeloest = []
    for m in C.MONATE:
        ist, stand = dozent.ist_werte(m["id"], ctx.fred, ctx.hist)
        if ist and stand == "endgültig":
            aufgeloest.append(m["name"])
    aus_daten = sum(1 for k, v in ctx.sig.items() if not v[1].startswith("Annahme"))
    abgedeckt = {n for p in C.PARAMETER for n in p["fragen"]} | {12, 14}
    schritte = [
        ("1. Erwartungen festlegen", f"{len(abgedeckt & set(C.FRAGE_BY_NR))} von {len(C.FRAGEN)} Dozentenfragen durch Szenario-Parameter abgedeckt",
         len(abgedeckt & set(C.FRAGE_BY_NR)) == len(C.FRAGEN)),
        ("2. Historische Daten auswerten", f"Streuung für {aus_daten} von {len(ctx.sig)} Grössen aus 5 Jahren Daten (statt Annahme)",
         aus_daten >= len(ctx.sig) - 2),
        ("3. Methoden / Modelle", "Szenario-Simulation, Taylor-Regel, Grinold-Kroner, Forward-Kurve, Breakevens, Datenlage-Indizien", True),
        ("4. Informationsquellen", f"FRED {len(ctx.fred)} Reihen, Yahoo {len(ctx.hist)} Kursreihen; Team-Werte mit 2 Quellen", len(ctx.fred) > 30),
        ("5. Aktuelle Lage interpretieren", f"Datenlage stützt {defs[lage['_favorit']]['kurz']}; Zyklus Modell {ctx.zyklus_modell['phase']}", True),
        ("6. Erwartungen dokumentieren", (f"Team-Beschluss vom {b['zeit']} mit Begründung" if b else "Noch kein Team-Beschluss") +
         f"; Erwartungen für {n_erw} Monat(e) gespeichert", bool(b) and n_erw > 0),
        ("7. Ergebnisse prüfen, Feedback", f"Aufgelöste Monate: {', '.join(aufgeloest) or 'noch keine'} (Seite «Erwartungen», Abschnitt Feedback)",
         bool(aufgeloest)),
    ]
    st.markdown("**CME-Prozess in 7 Schritten** (CFA Level III, Capital Market Expectations, Part 1)")
    tab = pd.DataFrame([("✓" if ok else "offen", n, t) for n, t, ok in schritte], columns=["", "Schritt", "Stand im Dashboard"])
    st.dataframe(tab, hide_index=True, width="stretch")

    st.markdown("**Verhaltensfehler beim Prognostizieren** – automatische Hinweise")
    flags = []
    if b and all(abs(b["werte"][s] - C.SZEN_BY_ID[s]["p"]) < 1 for s in defs):
        flags.append(("Anker (Anchoring)", "Der Team-Beschluss entspricht genau den IC-Startwerten. Bewusst neu schätzen, nicht nur übernehmen."))
    if alter is not None and alter > 21 and lage["_hinweis"]:
        flags.append(("Status quo", f"Letzter Beschluss vor {alter} Tagen, die Datenlage hat sich seither verschoben."))
    haupt = szen["p"].idxmax()
    if lage["_favorit"] != haupt and lage[lage["_favorit"]]["anteil"] - lage[haupt]["anteil"] >= 0.3:
        flags.append(("Bestätigungsfehler (Confirmation)", f"Hauptszenario ist {defs[haupt]['kurz']}, die Daten stützen aber deutlich stärker "
                                                           f"{defs[lage['_favorit']]['kurz']}. Gegenargumente ernst nehmen."))
    if szen["p"].max() >= 75:
        flags.append(("Überzuversicht (Overconfidence)", f"Ein Szenario hat {szen['p'].max():.0f} %. Prognosen sind unsicherer, als sie sich anfühlen."))
    if erw is not None and not erw.empty:
        d = erw[erw["frage"] != "haupt"].sort_values("zeit").drop_duplicates(["monat", "frage"], keep="last")
        hoch = d[(d["ueberzeugung"] == C.UEBERZEUGUNG[-1][0]) & (d["modell_p"].map(zahl).fillna(100) < C.UEBERZEUGUNG[-1][1] - 15)]
        if len(hoch):
            flags.append(("Überzuversicht (Overconfidence)", f"{len(hoch)} Antwort(en) mit Überzeugung «hoch», obwohl das Modell deutlich "
                                                             "weniger Wahrscheinlichkeit zeigt."))
        mitte = 0
        for _, r in d.iterrows():
            try:
                f = C.FRAGE_BY_NR[int(float(r["frage"]))]
            except (ValueError, KeyError):
                continue
            if f["art"] == "bereich" and len(f["optionen"]) == 3 and r["antwort"] == f["optionen"][1][0]:
                mitte += 1
        if len(d) >= 6 and mitte / len(d) >= 0.7:
            flags.append(("Vorsicht / Konservatismus (Prudence)", f"{mitte} von {len(d)} Antworten in der mittleren Kategorie – "
                                                                  "Prognosen nicht aus Vorsicht systematisch zur Mitte ziehen."))
    flags.append(("Verfügbarkeit / Recency (Hinweis)", "Die Streuung stammt aus den letzten 5 Jahren (inkl. 2022). Ein anderes Zeitfenster "
                                                      "gäbe andere Werte – Ergebnisse nicht überinterpretieren (time-period bias)."))
    for n, t in flags:
        st.warning(f"**{n}:** {t}")
    st.caption("Weitere Herausforderungen laut CFA: Datenrevisionen und -fehler, Grenzen historischer Schätzungen (Regimewechsel), "
               "Modellunsicherheit, falsch interpretierte Korrelationen. Deshalb: Streuung, Szenarien und Stresstests gemeinsam lesen.")
    if R:
        R.h("CFA-Check: CME-Prozess")
        R.tabelle(tab, [14, 52, 114])
        R.h("Verhaltensfehler")
        for n, t in flags:
            R.text(f"{n}: {t}", klein=True)


def SZ_def(s):
    from makro import szenarien as SZ
    return SZ.def_minus_zykl(s)


# ---------------------------------------------------------------------------
# Risiken & Chancen
# ---------------------------------------------------------------------------
def seite_risiken(ctx):
    ris, defs = ctx.risiken, ctx.szdef
    R = SP.Sammler("Risiken & Chancen", "Geopolitische und interne Risiken, verknüpft mit den Szenarien", kurz="Risiken")
    ui.seitenkopf("Risiken & Chancen", "Geopolitische und interne Risiken, jedes mit Richtung (auf welches Szenario es hinarbeitet). "
                  "Ein ausgelöstes Warnsignal zählt in der Datenlage des Szenarios.")
    ph = st.empty()
    aktiv = ris[ris["signal"]]
    kpis = [("Risiken", str(int((ris["art"] == "Risiko").sum())), "Richtung Bear"),
            ("Chancen", str(int((ris["art"] == "Chance").sum())), "Richtung Bull"),
            ("Warnsignale aktiv", str(len(aktiv)), ", ".join(aktiv["name"])[:70] or "keine"),
            ("Höchster Score", ris.sort_values("score").iloc[-1]["name"][:28] if len(ris) else "–", "Wahrsch. × Auswirkung")]
    ui.kopf_kpis(kpis)
    R.kpis(kpis)
    c1, c2 = st.columns([1.2, 1])
    with c1:
        ui.risiko_matrix(ris)
    with c2:
        for s in defs:
            d = ris[ris["richtung"] == s]
            if len(d):
                st.markdown(f"**→ {html.escape(defs[s]['name'])}**")
                st.markdown("<span class='klein'>" + "<br>".join(
                    f"{'🔴 ' if r['signal'] else ''}{html.escape(r['name'])} · {r['p']:.0f} % · Auswirkung {ui.de(r['impact'], 1)}"
                    for _, r in d.iterrows()) + "</span>", unsafe_allow_html=True)
    d = ris.sort_values("score", ascending=False)
    out = pd.DataFrame({"Risiko/Chance": d["name"], "Art": d["art"], "Richtung": d["richtung"].map(lambda x: defs.get(x, {}).get("kurz", x)),
                        "Wahrsch.": d["p"].map(lambda x: "–" if pd.isna(x) else f"{x:.0f} %"),
                        "Auswirkung": d["impact"].map(lambda x: "–" if pd.isna(x) else ui.de(x, 1)),
                        "Warnsignal": d["signal"].map(lambda x: "● aktiv" if x else "–"), "Warnsignale": d["warnsignal"],
                        "Reaktion": d["reaktion"], "Basis": d["basis"]})
    st.dataframe(ui.ampel_stil(out, ["Warnsignal"]), hide_index=True, width="stretch")
    R.h("Übersicht")
    R.tabelle(out.drop(columns=["Basis"]), [34, 13, 12, 13, 13, 13, 45, 37])

    st.markdown("#### Bewerten")
    st.caption(f"Du bewertest als {ctx.person}. Es zählt deine neuste Einschätzung; die App mittelt über alle Personen.")
    from makro.views import _letzte
    with st.form("risiken"):
        zeilen = []
        for rid, r in ris.iterrows():
            st.markdown(f"**{r['name']}** <span class='klein'>({r['art']} → {defs.get(r['richtung'], {}).get('kurz', '')}) · Analyse: "
                        f"{r['p_text']} · Warnsignale: {html.escape(r['warnsignal'])}</span>", unsafe_allow_html=True)
            c1, c2, c3, c4, c5 = st.columns([1.3, 1, 0.9, 0.9, 1.6])
            vp = _letzte(ctx.tabellen["risiko_votes"], ctx.person, "risiko", rid, "p")
            vi = _letzte(ctx.tabellen["risiko_votes"], ctx.person, "risiko", rid, "impact")
            vs = _letzte(ctx.tabellen["risiko_votes"], ctx.person, "risiko", rid, "signal")
            p = c1.slider("Wahrscheinlichkeit (%)", 0, 100, int(round(vp if vp is not None else (r["p"] if not pd.isna(r["p"]) else 20))),
                          5, key=f"rp_{rid}")
            imp = c2.select_slider("Auswirkung", [1, 2, 3, 4, 5], int(round(vi if vi is not None else (r["impact"] if not pd.isna(r["impact"]) else 3))),
                                   key=f"ri_{rid}")
            ri = c3.selectbox("Richtung", list(defs), index=list(defs).index(r["richtung"]) if r["richtung"] in defs else 2,
                              format_func=lambda x: defs[x]["kurz"], key=f"rr_{rid}")
            sig = c4.checkbox("Warnsignal ausgelöst", bool(vs) if vs is not None else False, key=f"rs_{rid}")
            kom = c5.text_input("Kommentar", key=f"rk_{rid}")
            zeilen.append((dict(zeit=jetzt(), person=ctx.person, risiko=rid, p=p, impact=imp, signal=int(sig), kommentar=kom),
                           ri if ri != r["richtung"] else None, rid))
        if st.form_submit_button("Alle speichern", type="primary", disabled=ctx.nur_lesen):
            neu_r = [dict(zeit=jetzt(), person=ctx.person, bereich="risiko", objekt=rid, feld="richtung", wert=ri)
                     for _, ri, rid in zeilen if ri]
            if neu_r:
                try:
                    ctx.store.anhaengen("definitionen", neu_r)
                except Exception:  # noqa: BLE001
                    pass
            ctx.speichern("risiko_votes", [z for z, _, _ in zeilen], "Risikobewertung gespeichert.")
    with st.expander("Neues Risiko hinzufügen", icon=":material/add:"):
        with st.form("neues_risiko", clear_on_submit=True):
            import re
            name = st.text_input("Name")
            kap = st.selectbox("Kapitel", [k["id"] for k in C.KAPITEL], index=7, format_func=lambda k: C.KAPITEL_BY_ID[k]["name"])
            ws = st.text_input("Warnsignal(e)")
            re_ = st.text_input("Vorab festgelegte Reaktion")
            if st.form_submit_button("Risiko anlegen", disabled=ctx.nur_lesen) and name.strip():
                rid = "r_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:30]
                ctx.speichern("risiken_neu", [dict(zeit=jetzt(), person=ctx.person, id=rid, name=name.strip(), kapitel=kap,
                                                   warnsignal=ws.strip(), reaktion=re_.strip())], "Risiko angelegt – jetzt oben bewerten.")
    SP.knopf(ph, R, "risiken")
