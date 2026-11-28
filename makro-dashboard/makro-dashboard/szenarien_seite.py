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
def seite_szenarien(ctx, team: list | None = None):
    defs, szen, lage, gew = ctx.szdef, ctx.szen, ctx.lage, ctx.gew
    R = SP.Sammler("Szenarien", "Base / Bull / Bear mit Parametern, Wahrscheinlichkeiten und Datenlage", kurz="Szenarien")
    ui.seitenkopf("Szenarien – Base, Bull, Bear",
                  "Ein gemeinsames Set für das ganze Dashboard. Jedes Szenario hat konkrete Monatswerte; der gewichtete Wert ist "
                  "Σ Wahrscheinlichkeit × Szenariowert. Die Datenlage zeigt, welches Szenario die aktuellen Zahlen stützen.")
    ph = st.empty()
    dt = ctx.tabellen.get("definitionen")
    stand = str(dt["zeit"].max()) if dt is not None and not dt.empty else ""
    ui.stand_abgleich("szenarien", stand, ("par_ed", "sek_ed", "ind_ed_", "sze_"))
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
    t1, t2, t3, t3b, t4, t5, t6, t7 = st.tabs(["Wahrscheinlichkeiten", "Parameter & gewichtete Werte", "Beschreibung", "Bearbeiten",
                                               "Datenlage (Monitor)", "CFA-Check", "Verlauf", "Änderungen"])
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
                werte_b = {s: c.number_input(defs[s]["kurz"], 0.0, 100.0, start[s], 5.0, format="%.3f") for c, s in zip(cols, defs)}
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
                            column_config={k: st.column_config.NumberColumn(k, format="%.3f", step=0.001) for k in kurz})
        st.markdown("**Sektorwirkung** (Rendite gegenüber dem S&P 500 im Monat, PP) – bestimmt Frage 12 und 14 und die "
                    "Sektor-Orientierung in der Taktik.")
        sz = pd.DataFrame([{"Sektor": sek, **{defs[s]["kurz"]: float(defs[s]["sektoren"].get(sek, 0) or 0) for s in defs},
                            "Gewichtet": f"{ui.de(gew['sektoren'][sek], 2, True)} PP",
                            "Korb": "defensiv" if sek in C.DEFENSIV else "zyklisch" if sek in C.ZYKLISCH else ""}
                           for sek in C.SEKTOREN])
        ed_s = st.data_editor(sz, hide_index=True, width="stretch", key="sek_ed", disabled=["Sektor", "Gewichtet", "Korb"],
                              column_config={k: st.column_config.NumberColumn(k, format="%.3f", step=0.001) for k in kurz})
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
                extra = [(SZm.TEXT_LABEL[f].split(" (")[0], d.get(f, "")) for f in ("signposts", "gegenargument", "horizont", "portfolio")
                         if str(d.get(f, "")).strip()]
                if extra:
                    st.markdown("<span class='klein'>" + "<br>".join(f"<b>{html.escape(l)}:</b> {html.escape(v)}" for l, v in extra)
                                + "</span>", unsafe_allow_html=True)
                verkn = ctx.risiken[ctx.risiken["richtung"] == s]
                if len(verkn):
                    st.markdown("<span class='klein'><b>Verknüpfte Risiken/Chancen:</b> " + ", ".join(
                        f"{html.escape(r['name'])} ({r['p']:.0f} %{', Warnsignal!' if r['signal'] else ''})" for _, r in verkn.iterrows()
                        if not pd.isna(r["p"])) + "</span>", unsafe_allow_html=True)
                st.caption("Ändern im Tab «Bearbeiten».")
                R.h(f"{d['name']} ({szen.loc[s, 'p']:.0f} %)")
                R.text(d["narrativ"])
                R.text(f"Annahmen: {d['annahmen']}. Folge: {d['folge']}. Aktion: {d['aktion']}.", klein=True)
                for f in ("signposts", "gegenargument", "horizont", "portfolio"):
                    if str(d.get(f, "")).strip():
                        R.text(f"{SZm.TEXT_LABEL[f].split(' (')[0]}: {d[f]}", klein=True)

    # --- Bearbeiten -------------------------------------------------------------
    with t3b:
        _szenario_bearbeiten(ctx, defs, szen)

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

    # --- Änderungen -----------------------------------------------------------
    with t7:
        rows = []
        if dt is not None and not dt.empty:
            par_name = {**{f"param:{p_['id']}": p_["name"] for p_ in C.PARAMETER},
                        **{f"alloc:{p_['id']}": "Taktik: " + p_["name"] for p_ in C.ALLOK_PARAMETER}}
            for _, r in dt[dt["bereich"] == "szenario"].sort_values("zeit", ascending=False).iterrows():
                f = str(r["feld"])
                lab = SZm.TEXT_LABEL.get(f, par_name.get(f, f.replace("sektor:", "Sektorwirkung ") if f.startswith("sektor:") else
                                                         "Indizien" if f == "indizien" else f))
                rows.append({"Zeit": str(r["zeit"])[:16].replace("T", " "), "Person": r["person"],
                             "Szenario": defs.get(r["objekt"], {}).get("kurz", r["objekt"]), "Feld": lab.split(" (")[0],
                             "Neuer Wert": str(r["wert"])[:160],
                             "Startwert": str(SZm.szenario_startwert(r["objekt"], f))[:80] if r["objekt"] in defs else ""})
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        else:
            st.caption("Noch keine Änderungen – alles Startwerte.")
    SP.knopf(ph, R, "szenarien")


def _szenario_bearbeiten(ctx, defs: dict, szen: pd.DataFrame):
    """Ein Szenario vollständig bearbeiten: Texte, Monatswerte, Taktik-Orientierung, Sektorwirkung und die Zuordnung der
    Risiken/Chancen. Gespeichert werden nur geänderte Felder; alles wirkt sofort im ganzen Dashboard."""
    st.markdown(WIRKT_AUF)
    s = st.radio("Szenario", list(defs), horizontal=True, format_func=lambda x: f"{defs[x]['kurz']} ({szen.loc[x, 'p']:.0f} %)",
                 key="szw_wahl")
    d = defs[s]
    if d["geaendert"]:
        st.caption(f"{len(d['geaendert'])} Felder vom Team geändert (zuletzt "
                   f"{max(w[1] for w in d['geaendert'].values())}) – Details im Tab «Änderungen».")
    k_ = f"sze_{s}_"
    with st.form(f"sze_form_{s}"):
        c1, c2, c3 = st.columns([2.4, 1, 1.4])
        texte = {"name": c1.text_input(SZm.TEXT_LABEL["name"], d["name"], key=k_ + "name"),
                 "kurz": c2.text_input(SZm.TEXT_LABEL["kurz"], d["kurz"], max_chars=12, key=k_ + "kurz"),
                 "horizont": c3.text_input(SZm.TEXT_LABEL["horizont"], d.get("horizont", ""), placeholder="z. B. Dez. 2026 – Feb. 2027",
                                           key=k_ + "horizont")}
        c1, c2 = st.columns(2)
        for i, f in enumerate(("narrativ", "annahmen", "folge", "aktion", "signposts", "gegenargument", "portfolio")):
            texte[f] = (c1 if i % 2 == 0 else c2).text_area(SZm.TEXT_LABEL[f], d.get(f, ""), height=90, key=k_ + f)

        st.markdown("**Monatswerte und Taktik-Orientierung** (Startwert = Vorschlag aus dem IC-Statement)")
        par_rows = [{"feld": f"param:{p_['id']}", "Grösse": p_["name"], "Einheit": p_["einheit"],
                     "Startwert": float(C.SZEN_BY_ID[s]["param"].get(p_["id"], 0) or 0),
                     "Wert": float(d["param"].get(p_["id"], 0) or 0), "Erklärung": p_.get("hilfe", "")} for p_ in C.PARAMETER]
        par_rows += [{"feld": f"alloc:{p_['id']}", "Grösse": "Taktik: " + p_["name"], "Einheit": p_["einheit"],
                      "Startwert": float(C.SZEN_BY_ID[s]["alloc"].get(p_["id"], 0) or 0),
                      "Wert": float(d["alloc"].get(p_["id"], 0) or 0), "Erklärung": ""} for p_ in C.ALLOK_PARAMETER]
        par_df = pd.DataFrame(par_rows)
        ed_p = st.data_editor(par_df.drop(columns="feld"), hide_index=True, width="stretch", key=k_ + "par",
                              disabled=["Grösse", "Einheit", "Startwert", "Erklärung"],
                              column_config={"Startwert": st.column_config.NumberColumn(format="%.3f"),
                                             "Wert": st.column_config.NumberColumn(format="%.3f", step=0.001, required=True),
                                             "Erklärung": st.column_config.TextColumn(width="large")})
        st.markdown("**Sektorwirkung** (Rendite gegenüber dem S&P 500 im Monat, PP)")
        sek_df = pd.DataFrame({"Sektor": C.SEKTOREN,
                               "Startwert": [float(C.SZEN_BY_ID[s]["sektoren"].get(x, 0) or 0) for x in C.SEKTOREN],
                               "Wert": [float(d["sektoren"].get(x, 0) or 0) for x in C.SEKTOREN]})
        ed_s = st.data_editor(sek_df, hide_index=True, width="stretch", key=k_ + "sek", disabled=["Sektor", "Startwert"],
                              column_config={"Startwert": st.column_config.NumberColumn(format="%.3f"),
                                             "Wert": st.column_config.NumberColumn(format="%.3f", step=0.001, required=True)})
        st.markdown("**Risiken & Chancen → Szenario** (Richtung; ein Warnsignal zählt in der Datenlage dieses Szenarios)")
        ris = ctx.risiken
        kurz_von = {x: defs[x]["kurz"] for x in defs}
        von_kurz = {v: k for k, v in kurz_von.items()}
        ris_df = pd.DataFrame({"id": ris["id"], "Risiko/Chance": ris["name"], "Art": ris["art"],
                               "Richtung": ris["richtung"].map(lambda x: kurz_von.get(x, x)),
                               "Warnsignal": ris["signal"].map(lambda x: "● aktiv" if x else "–")})
        ed_r = st.data_editor(ris_df.drop(columns="id"), hide_index=True, width="stretch", key=k_ + "ris",
                              disabled=["Risiko/Chance", "Art", "Warnsignal"],
                              column_config={"Richtung": st.column_config.SelectboxColumn(options=list(von_kurz), required=True)})
        st.caption("Indizien der Datenlage bearbeitet ihr im Tab «Datenlage (Monitor)» → «Indizien bearbeiten»; "
                   "Texte, Status und Wirkung der Risiken auf der Seite «Risiken & Chancen».")
        b1, b2 = st.columns(2)
        los = b1.form_submit_button("Szenario speichern", type="primary", icon=":material/save:", disabled=ctx.nur_lesen)
        zurueck = b2.form_submit_button("Auf Startwerte zurücksetzen", icon=":material/restart_alt:",
                                        disabled=ctx.nur_lesen or not d["geaendert"],
                                        help="Alle Team-Änderungen dieses Szenarios (Texte, Werte, Indizien) rückgängig – Wahrscheinlichkeiten bleiben")
    if los:
        texte = {f: str(v or "").strip() for f, v in texte.items()}
        andere_kurz = {defs[x]["kurz"].strip().lower() for x in defs if x != s}
        if not texte["name"] or not texte["kurz"]:
            st.error("Name und Kurzname dürfen nicht leer sein.")
            return
        if texte["kurz"].lower() in andere_kurz:
            st.error(f"Kurzname «{texte['kurz']}» gibt es schon – er muss eindeutig sein (er erscheint als Spaltenname).")
            return
        z = jetzt()
        rows = [dict(zeit=z, person=ctx.person, bereich="szenario", objekt=s, feld=f, wert=v)
                for f, v in texte.items() if v != str(d.get(f, "")).strip()]
        for (_, alt), (_, neu) in zip(par_df.iterrows(), ed_p.iterrows()):
            if not _leer(neu["Wert"]) and abs(float(neu["Wert"]) - float(alt["Wert"])) > 1e-9:
                rows.append(dict(zeit=z, person=ctx.person, bereich="szenario", objekt=s, feld=alt["feld"], wert=float(neu["Wert"])))
        for (_, alt), (_, neu) in zip(sek_df.iterrows(), ed_s.iterrows()):
            if not _leer(neu["Wert"]) and abs(float(neu["Wert"]) - float(alt["Wert"])) > 1e-9:
                rows.append(dict(zeit=z, person=ctx.person, bereich="szenario", objekt=s, feld=f"sektor:{alt['Sektor']}",
                                 wert=float(neu["Wert"])))
        r_rows = [dict(zeit=z, person=ctx.person, bereich="risiko", objekt=alt["id"], feld="richtung", wert=von_kurz[neu["Richtung"]])
                  for (_, alt), (_, neu) in zip(ris_df.iterrows(), ed_r.iterrows())
                  if neu["Richtung"] in von_kurz and neu["Richtung"] != alt["Richtung"]]
        if not rows and not r_rows:
            st.info("Keine Änderungen.")
            return
        n = len(rows) + len(r_rows)
        if rows:
            ctx.speichern("definitionen", rows, f"Szenario {texte['kurz']} gespeichert ({n} Änderungen).",
                          weitere=[("definitionen", r_rows)] if r_rows else None)
        else:
            ctx.speichern("definitionen", r_rows, f"Zuordnung der Risiken gespeichert ({n} Änderungen).")
    if zurueck:
        z = jetzt()
        rows = [dict(zeit=z, person=ctx.person, bereich="szenario", objekt=s, feld=f, wert=SZm.szenario_startwert(s, f))
                for f in d["geaendert"]]
        ctx.speichern("definitionen", rows, f"Szenario {C.SZEN_BY_ID[s]['kurz']} auf Startwerte zurückgesetzt.")


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
EINGEBAUTE_REGEL = {"r_konsum": "Warnsignal Konsumrückgang", "r_ki": "Warnsignal KI-/Tech-Korrektur",
                    "r_protekt": "Warnsignal Protektionismus", "r_iran_esk": "Warnsignal Iran-Eskalation",
                    "r_china_esk": "Warnsignal China-Eskalation", "r_iran_deal": "Chance Iran-Deal",
                    "r_china_deal": "Chance China-Deal", "r_tail": "Warnsignal Tail Risk"}
RISIKO_FELD_LABEL = {"name": "Name", "art": "Art", "richtung": "Richtung (Szenario)", "kapitel": "Kapitel", "status": "Status",
                     "p": "Startwahrscheinlichkeit", "p_text": "Analyse (Text)", "impact": "Startauswirkung",
                     "warnsignal": "Warnsignal(e)", "auto": "Automatisches Warnsignal", "reaktion": "Vorab festgelegte Reaktion",
                     "quelle": "Quelle", "horizont": "Zeithorizont", "verantwortlich": "Verantwortlich",
                     "wirkung_aktien": "Wirkung Aktienquote (PP)", "wirkung_duration": "Wirkung Duration (J.)",
                     "wirkung_sektoren": "Wirkung Sektoren"}


def _leer(v) -> bool:
    return v is None or (isinstance(v, float) and np.isnan(v)) or (isinstance(v, str) and not v.strip())


def _alle_risiken(ctx) -> pd.DataFrame:
    """Aktive + archivierte Risiken (archivierte zählen im Dashboard nicht, lassen sich hier aber reaktivieren)."""
    ris = ctx.risiken
    arch = ris.attrs.get("archiv")
    return pd.concat([ris, arch]) if arch is not None and len(arch) else ris


def _wirkung_text(r) -> str:
    teile = []
    if not _leer(r.get("wirkung_aktien")) and float(r["wirkung_aktien"]):
        teile.append(f"Aktien {ui.de(float(r['wirkung_aktien']), 1, True)} PP")
    if not _leer(r.get("wirkung_duration")):
        teile.append(f"Duration {ui.de(float(r['wirkung_duration']), 1)} J.")
    for sek, w in (r.get("wirkung_sektoren") or {}).items():
        teile.append(f"{sek} {'+' if w > 0 else '−'}{ui.de(abs(w), 0)}")
    return " · ".join(teile)


def seite_risiken(ctx, team: list | None = None):
    ris, defs = ctx.risiken, ctx.szdef
    alle = _alle_risiken(ctx)
    team = [t for t in (team or []) if t]
    R = SP.Sammler("Risiken & Chancen", "Geopolitische und interne Risiken, verknüpft mit den Szenarien", kurz="Risiken")
    ui.seitenkopf("Risiken & Chancen", "Geopolitische und interne Risiken, jedes mit Richtung (auf welches Szenario es hinarbeitet). "
                  "Ein ausgelöstes Warnsignal zählt in der Datenlage des Szenarios, im Gesamtbild, im Monitoring und – wenn ihr "
                  "eine Wirkung festlegt – in den Signalen der Taktik.")
    ph = st.empty()
    tab_ = ctx.tabellen
    stand = max([str(t["zeit"].max()) for t in (tab_.get("definitionen"), tab_.get("risiken_neu"), tab_.get("risiko_votes"))
                 if t is not None and not t.empty] or [""])
    ui.stand_abgleich("risiken", stand, ("rp_", "ri_", "rr_", "rs_", "rk_", "rbe_", "rneu_"))
    aktiv = ris[ris["signal"]]
    arch = ris.attrs.get("archiv")
    kpis = [("Risiken", str(int((ris["art"] == "Risiko").sum())), "meist Richtung Bear"),
            ("Chancen", str(int((ris["art"] == "Chance").sum())), "meist Richtung Bull"),
            ("Warnsignale aktiv", str(len(aktiv)), ", ".join(aktiv["name"])[:70] or "keine"),
            ("Höchster Score", ris.sort_values("score").iloc[-1]["name"][:28] if len(ris) else "–", "Wahrsch. × Auswirkung"),
            ("Archiviert", str(0 if arch is None else len(arch)), "zählen nirgends, reaktivierbar")]
    ui.kopf_kpis(kpis)
    R.kpis(kpis)
    t1, t2, t3, t4, t5 = st.tabs(["Übersicht", "Bewerten", "Bearbeiten", "Neu anlegen", "Änderungen & Kommentare"])

    # --- Übersicht -------------------------------------------------------------
    with t1:
        c1, c2 = st.columns([1.2, 1])
        with c1:
            if len(ris):
                ui.risiko_matrix(ris)
        with c2:
            for s in defs:
                d = ris[ris["richtung"] == s]
                if len(d):
                    st.markdown(f"**→ {html.escape(defs[s]['name'])}**")
                    st.markdown("<span class='klein'>" + "<br>".join(
                        f"{'🔴 ' if r['signal'] else ''}{html.escape(r['name'])} · {r['p']:.0f} % · Auswirkung {ui.de(r['impact'], 1)}"
                        + (f" · {html.escape(r['verantwortlich'])}" if r["verantwortlich"] else "")
                        for _, r in d.iterrows() if not pd.isna(r["p"]) and not pd.isna(r["impact"])) + "</span>",
                        unsafe_allow_html=True)
        f1, f2, f3 = st.columns(3)
        f_art = f1.multiselect("Art", ["Risiko", "Chance"], default=["Risiko", "Chance"], key="rf_art")
        f_ri = f2.multiselect("Richtung", list(defs), default=list(defs), format_func=lambda x: defs[x]["kurz"], key="rf_ri")
        f_ar = f3.toggle("Archivierte zeigen", False, key="rf_arch")
        d = (alle if f_ar else ris)
        d = d[d["art"].isin(f_art) & d["richtung"].isin(f_ri)].sort_values("score", ascending=False)
        out = pd.DataFrame({"Risiko/Chance": d["name"], "Art": d["art"],
                            "Richtung": d["richtung"].map(lambda x: defs.get(x, {}).get("kurz", x)),
                            "Status": d["status"],
                            "Wahrsch.": d["p"].map(lambda x: "–" if pd.isna(x) else f"{x:.0f} %"),
                            "Team": [f"{r['p_min']:.0f}–{r['p_max']:.0f} % (n={r['n']})" if r["n"] else "–" for _, r in d.iterrows()],
                            "Auswirkung": d["impact"].map(lambda x: "–" if pd.isna(x) else ui.de(x, 1)),
                            "Warnsignal": d["signal"].map(lambda x: "● aktiv" if x else "–"), "Warnsignale": d["warnsignal"],
                            "Reaktion": d["reaktion"], "Wirkung Taktik": [_wirkung_text(r) or "–" for _, r in d.iterrows()],
                            "Horizont": d["horizont"].replace("", "–"), "Verantwortlich": d["verantwortlich"].replace("", "–"),
                            "Basis": d["basis"]})
        st.dataframe(ui.ampel_stil(out, ["Warnsignal"]), hide_index=True, width="stretch")
        R.h("Übersicht")
        R.tabelle(out[["Risiko/Chance", "Art", "Richtung", "Wahrsch.", "Auswirkung", "Warnsignal", "Warnsignale", "Reaktion"]],
                  [34, 13, 12, 13, 13, 13, 45, 37])

    # --- Bewerten --------------------------------------------------------------
    with t2:
        st.caption(f"Du bewertest als {ctx.person}. Es zählt deine neuste Einschätzung; die App mittelt über alle Personen. "
                   "Richtung, Texte, Status und Wirkung im Tab «Bearbeiten».")
        from makro.views import _letzte
        with st.form("risiken"):
            zeilen = []
            for rid, r in ris.iterrows():
                kom = r["kommentare"][-1] if r["kommentare"] else None
                st.markdown(f"**{html.escape(r['name'])}** <span class='klein'>({r['art']} → {defs.get(r['richtung'], {}).get('kurz', '')}) · "
                            f"Analyse: {html.escape(str(r['p_text']))} · Warnsignale: {html.escape(str(r['warnsignal']))}"
                            + (f"<br>Letzter Kommentar ({html.escape(kom[0])}, {kom[1]}): {html.escape(kom[2])}" if kom else "")
                            + "</span>", unsafe_allow_html=True)
                c1, c2, c3, c4 = st.columns([1.4, 1, 0.9, 1.7])
                vp = _letzte(ctx.tabellen["risiko_votes"], ctx.person, "risiko", rid, "p")
                vi = _letzte(ctx.tabellen["risiko_votes"], ctx.person, "risiko", rid, "impact")
                vs = _letzte(ctx.tabellen["risiko_votes"], ctx.person, "risiko", rid, "signal")
                p = c1.slider("Wahrscheinlichkeit (%)", 0, 100, int(round(vp if vp is not None else (r["p"] if not pd.isna(r["p"]) else 20))),
                              1, key=f"rp_{rid}")
                imp = c2.select_slider("Auswirkung", [1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5],
                                       min([1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5], key=lambda x: abs(x - (vi if vi is not None else (
                                           r["impact"] if not pd.isna(r["impact"]) else 3)))), key=f"ri_{rid}")
                sig = c3.checkbox("Warnsignal ausgelöst", bool(vs) if vs is not None else False, key=f"rs_{rid}")
                kom_neu = c4.text_input("Kommentar (optional)", key=f"rk_{rid}")
                zeilen.append(dict(zeit=jetzt(), person=ctx.person, risiko=rid, p=p, impact=imp, signal=int(sig), kommentar=kom_neu.strip()))
            if st.form_submit_button("Alle speichern", type="primary", disabled=ctx.nur_lesen or not len(ris)):
                ctx.speichern("risiko_votes", zeilen, "Risikobewertung gespeichert.")

    # --- Bearbeiten ------------------------------------------------------------
    with t3:
        _risiko_bearbeiten(ctx, alle, defs, team)

    # --- Neu anlegen -------------------------------------------------------------
    with t4:
        _risiko_neu(ctx, alle, defs, team)

    # --- Änderungen & Kommentare ---------------------------------------------------
    with t5:
        namen = dict(zip(alle["id"], alle["name"]))
        dt = ctx.tabellen.get("definitionen")
        rows = []
        if dt is not None and not dt.empty:
            for _, r in dt[dt["bereich"] == "risiko"].sort_values("zeit", ascending=False).iterrows():
                w = str(r["wert"])
                if r["feld"] == "richtung":
                    w = defs.get(w, {}).get("kurz", w)
                rows.append({"Zeit": str(r["zeit"])[:16].replace("T", " "), "Person": r["person"],
                             "Risiko/Chance": namen.get(r["objekt"], r["objekt"]),
                             "Feld": RISIKO_FELD_LABEL.get(r["feld"], r["feld"]), "Neuer Wert": w[:160]})
        neu = ctx.tabellen.get("risiken_neu")
        if neu is not None and not neu.empty:
            for _, r in neu.iterrows():
                rows.append({"Zeit": str(r["zeit"])[:16].replace("T", " "), "Person": r["person"], "Risiko/Chance": r["name"],
                             "Feld": "neu angelegt", "Neuer Wert": r["warnsignal"][:160]})
        st.markdown("**Änderungen an Risiken & Chancen**")
        if rows:
            st.dataframe(pd.DataFrame(rows).sort_values("Zeit", ascending=False), hide_index=True, width="stretch")
        else:
            st.caption("Noch keine Änderungen – alles Startwerte aus dem IC-Statement.")
        st.markdown("**Kommentare aus den Bewertungen**")
        kom = [{"Zeit": z, "Person": p_, "Risiko/Chance": r["name"], "Kommentar": k}
               for _, r in alle.iterrows() for p_, z, k in r["kommentare"]]
        if kom:
            st.dataframe(pd.DataFrame(kom).sort_values("Zeit", ascending=False), hide_index=True, width="stretch")
        else:
            st.caption("Noch keine Kommentare.")
    SP.knopf(ph, R, "risiken")


def _risiko_bearbeiten(ctx, alle: pd.DataFrame, defs: dict, team: list):
    """Alle Felder eines Risikos/einer Chance ändern; gespeichert werden nur geänderte Felder (Tabelle «definitionen»)."""
    if not len(alle):
        st.caption("Keine Risiken vorhanden – im Tab «Neu anlegen» erfassen.")
        return
    rid = st.selectbox("Risiko / Chance", list(alle.index), key="rbw_wahl",
                       format_func=lambda x: f"{alle.loc[x, 'name']} ({alle.loc[x, 'art']}"
                                             + (", archiviert" if alle.loc[x, "status"] == "archiviert" else "") + ")")
    r = alle.loc[rid]
    eingebaut = EINGEBAUTE_REGEL.get(rid)
    if r["geaendert"]:
        st.caption("Vom Team geändert: " + ", ".join(f"{RISIKO_FELD_LABEL.get(f, f)} ({w}, {d})" for f, (w, d) in r["geaendert"].items()))
    kap_ids = [k["id"] for k in C.KAPITEL]
    k_ = f"rbe_{rid}_"
    with st.form(f"rbe_form_{rid}"):
        c1, c2, c3, c4 = st.columns([2, 1, 1, 1.3])
        name = c1.text_input("Name", r["name"], key=k_ + "name")
        art = c2.selectbox("Art", ["Risiko", "Chance"], index=0 if r["art"] != "Chance" else 1, key=k_ + "art")
        richtung = c3.selectbox("Richtung (Szenario)", list(defs), index=list(defs).index(r["richtung"]) if r["richtung"] in defs else 2,
                                format_func=lambda x: defs[x]["kurz"], key=k_ + "richtung",
                                help="Auf welches Szenario arbeitet es hin? Ein Warnsignal zählt dort in der Datenlage.")
        status = c4.selectbox("Status", list(SZm.RISIKO_STATUS), index=list(SZm.RISIKO_STATUS).index(r["status"]),
                              format_func=lambda x: SZm.RISIKO_STATUS_TEXT[x], key=k_ + "status")
        c1, c2, c3, c4 = st.columns([1, 1.6, 1, 1.4])
        p_start = c1.number_input("Startwahrscheinlichkeit (%)", 0.0, 100.0, float(_start_or(r, "p", 20.0, ctx)), 1.0, format="%.0f",
                                  key=k_ + "p", help="Gilt, solange niemand bewertet hat (danach zählt das Mittel der Team-Bewertungen).")
        p_text = c2.text_input("Analyse (Text, z. B. «30–40 %»)", str(r["p_text"] or ""), key=k_ + "p_text")
        i_start = c3.number_input("Startauswirkung (1–5)", 1.0, 5.0, float(_start_or(r, "impact", 3.0, ctx)), 0.5, format="%.1f",
                                  key=k_ + "impact")
        kap = c4.selectbox("Kapitel", kap_ids, index=kap_ids.index(r["kapitel"]) if r["kapitel"] in kap_ids else 0,
                           format_func=lambda k: C.KAPITEL_BY_ID[k]["name"], key=k_ + "kapitel")
        c1, c2 = st.columns(2)
        warn = c1.text_area("Warnsignal(e) – woran erkennen wir es?", str(r["warnsignal"] or ""), height=90, key=k_ + "warnsignal")
        reak = c2.text_area("Vorab festgelegte Reaktion", str(r["reaktion"] or ""), height=90, key=k_ + "reaktion")
        c1, c2, c3, c4 = st.columns([1.6, 1, 1, 1])
        auto_keys = list(SZm.AUTO_SIGNALE)
        auto = c1.selectbox("Automatisches Warnsignal", auto_keys, index=auto_keys.index(r["auto"] or "") if (r["auto"] or "") in auto_keys else 0,
                            format_func=lambda x: SZm.AUTO_SIGNALE[x], key=k_ + "auto")
        horizont = c2.text_input("Zeithorizont", str(r["horizont"] or ""), placeholder="z. B. bis 28.02.2027", key=k_ + "horizont")
        opt = [""] + team + ([r["verantwortlich"]] if r["verantwortlich"] and r["verantwortlich"] not in team else [])
        if team:
            verantw = c3.selectbox("Verantwortlich", opt, index=opt.index(r["verantwortlich"] or ""), key=k_ + "verantwortlich",
                                   format_func=lambda x: x or "– niemand –")
        else:
            verantw = c3.text_input("Verantwortlich", str(r["verantwortlich"] or ""), key=k_ + "verantwortlich")
        quelle = c4.text_input("Quelle", str(r["quelle"] or ""), key=k_ + "quelle")

        st.markdown("**Wirkung, wenn das Warnsignal aktiv ist** – erscheint in der Taktik unter «Signale» (Orientierung, keine Pflicht)"
                    + (f". Ersetzt die eingebaute Regel «{eingebaut}»." if eingebaut else "."))
        hat = not _leer(r["wirkung_aktien"]) or not _leer(r["wirkung_duration"]) or bool(r["wirkung_sektoren"])
        c1, c2, c3 = st.columns([1.2, 1, 1])
        eigen = c1.checkbox("Eigene Wirkung festlegen", hat, key=k_ + "eigen",
                            help="Aus = keine eigene Wirkung" + (" (es gilt die eingebaute Regel)" if eingebaut else ""))
        w_ak = c2.number_input("Aktienquote (PP)", -20.0, 20.0, 0.0 if _leer(r["wirkung_aktien"]) else float(r["wirkung_aktien"]), 1.0,
                               format="%.1f", key=k_ + "w_ak")
        w_du = c3.number_input("Duration-Ziel (J., 0 = keine Vorgabe)", 0.0, 15.0,
                               0.0 if _leer(r["wirkung_duration"]) else float(r["wirkung_duration"]), 0.5, format="%.1f", key=k_ + "w_du")
        ws = r["wirkung_sektoren"] or {}
        ed_w = st.data_editor(pd.DataFrame({"Sektor": C.SEKTOREN, "Wirkung": [float(ws.get(x, 0)) for x in C.SEKTOREN]}),
                              hide_index=True, width="stretch", disabled=["Sektor"], key=k_ + "w_sek",
                              column_config={"Wirkung": st.column_config.NumberColumn(
                                  "Wirkung (−2 = stark untergewichten … +2 = stark übergewichten)", min_value=-2.0, max_value=2.0,
                                  step=1.0, format="%+.0f")})
        b1, b2 = st.columns([1, 1])
        speichern = b1.form_submit_button("Änderungen speichern", type="primary", icon=":material/save:", disabled=ctx.nur_lesen)
        zurueck = b2.form_submit_button("Auf Startwerte zurücksetzen", icon=":material/restart_alt:",
                                        disabled=ctx.nur_lesen or r["herkunft"] != "Start" or not r["geaendert"],
                                        help="Nur für Risiken aus dem IC-Statement: alle Team-Änderungen dieses Risikos rückgängig")
    if speichern:
        sek_w = {x["Sektor"]: float(x["Wirkung"]) for _, x in ed_w.iterrows() if not _leer(x["Wirkung"]) and float(x["Wirkung"])}
        neu = dict(name=name.strip(), art=art, richtung=richtung, status=status, p=float(p_start), p_text=p_text.strip(),
                   impact=float(i_start), kapitel=kap, warnsignal=warn.strip(), reaktion=reak.strip(), auto=auto,
                   horizont=horizont.strip(), verantwortlich=(verantw or "").strip(), quelle=quelle.strip(),
                   wirkung_aktien=float(w_ak) if eigen else None, wirkung_duration=(float(w_du) or None) if eigen else None,
                   wirkung_sektoren=sek_w if eigen else {})
        if not neu["name"]:
            st.error("Der Name darf nicht leer sein.")
            return
        rows = []
        for f, v in neu.items():
            alt = r[f] if f != "auto" else (r["auto"] or "")
            if f in ("p", "impact"):
                alt_v = _start_or(r, f, None, ctx)
                gleich = alt_v is not None and abs(float(alt_v) - v) < 1e-9
            elif f in ("wirkung_aktien", "wirkung_duration"):
                gleich = (_leer(alt) and v is None) or (not _leer(alt) and v is not None and abs(float(alt) - v) < 1e-9)
            elif f == "wirkung_sektoren":
                gleich = dict(alt or {}) == v
            else:
                gleich = str(alt or "").strip() == str(v)
            if not gleich:
                w = json.dumps(v, ensure_ascii=False) if f == "wirkung_sektoren" else ("" if v is None else v)
                rows.append(dict(zeit=jetzt(), person=ctx.person, bereich="risiko", objekt=rid, feld=f, wert=w))
        if not rows:
            st.info("Keine Änderungen.")
        else:
            ctx.speichern("definitionen", rows, f"«{neu['name']}» gespeichert ({len(rows)} Feld{'er' if len(rows) > 1 else ''}).")
    if zurueck:
        rows = []
        for f in r["geaendert"]:
            w = SZm.risiko_startwert(rid, f)
            rows.append(dict(zeit=jetzt(), person=ctx.person, bereich="risiko", objekt=rid, feld=f,
                             wert="" if w is None else (json.dumps(w) if isinstance(w, dict) else w)))
        if rows:
            ctx.speichern("definitionen", rows, f"«{r['name']}» auf Startwerte zurückgesetzt.")


def _start_or(r, feld: str, standard, ctx=None):
    """Startwert (p / impact) des Risikos: Team-Änderung > config.py > standard. r['p'] ist das Team-Mittel, darum separat."""
    g = r["geaendert"] or {}
    dt = ctx.tabellen.get("definitionen") if ctx is not None else None
    if feld in g and dt is not None and not dt.empty:
        d = dt[(dt["bereich"] == "risiko") & (dt["objekt"] == r["id"]) & (dt["feld"] == feld)].sort_values("zeit")
        if len(d):
            v = zahl(d.iloc[-1]["wert"])
            if v is not None:
                return v
    v = SZm.risiko_startwert(r["id"], feld)
    v = zahl(v) if v not in (None, "") else None
    if v is None and r["n"] == 0 and not _leer(r[feld]):
        v = float(r[feld])
    return standard if v is None else v


def _risiko_neu(ctx, alle: pd.DataFrame, defs: dict, team: list):
    st.caption("Neues Risiko oder neue Chance mit allen Angaben. Danach im Tab «Bewerten» einschätzen; alles lässt sich unter "
               "«Bearbeiten» ändern oder archivieren.")
    kap_ids = [k["id"] for k in C.KAPITEL]
    with st.form("neues_risiko", clear_on_submit=True):
        c1, c2, c3 = st.columns([2, 1, 1])
        name = c1.text_input("Name", key="rneu_name")
        art = c2.selectbox("Art", ["Risiko", "Chance"], key="rneu_art")
        richtung = c3.selectbox("Richtung (Szenario)", list(defs), index=2, format_func=lambda x: defs[x]["kurz"], key="rneu_ri",
                                help="Chancen meist Bull, Risiken meist Bear")
        c1, c2, c3, c4 = st.columns(4)
        p = c1.number_input("Wahrscheinlichkeit (%)", 0.0, 100.0, 20.0, 1.0, format="%.0f", key="rneu_p")
        imp = c2.number_input("Auswirkung (1–5)", 1.0, 5.0, 3.0, 0.5, format="%.1f", key="rneu_i")
        kap = c3.selectbox("Kapitel", kap_ids, index=min(7, len(kap_ids) - 1), format_func=lambda k: C.KAPITEL_BY_ID[k]["name"], key="rneu_kap")
        horizont = c4.text_input("Zeithorizont", placeholder="z. B. bis 28.02.2027", key="rneu_h")
        c1, c2 = st.columns(2)
        ws = c1.text_area("Warnsignal(e)", height=80, key="rneu_ws")
        re_ = c2.text_area("Vorab festgelegte Reaktion", height=80, key="rneu_re")
        c1, c2 = st.columns(2)
        if team:
            verantw = c1.selectbox("Verantwortlich", [""] + team, format_func=lambda x: x or "– niemand –", key="rneu_v")
        else:
            verantw = c1.text_input("Verantwortlich", key="rneu_v")
        quelle = c2.text_input("Quelle", key="rneu_q")
        los = st.form_submit_button("Anlegen", type="primary", icon=":material/add:", disabled=ctx.nur_lesen)
    if los:
        if not name.strip():
            st.error("Bitte einen Namen eingeben.")
            return
        vorhanden = set(alle["id"]) | {x["id"] for x in C.RISIKEN}
        neu_t = ctx.tabellen.get("risiken_neu")
        if neu_t is not None and not neu_t.empty:
            vorhanden |= set(neu_t["id"])
        rid = SZm._neue_risiko_id(name, vorhanden)
        z = jetzt()
        felder = dict(art=art, richtung=richtung, p=float(p), impact=float(imp), horizont=horizont.strip(),
                      verantwortlich=(verantw or "").strip(), quelle=quelle.strip(), p_text="Team")
        rows = [dict(zeit=z, person=ctx.person, bereich="risiko", objekt=rid, feld=f, wert=v) for f, v in felder.items() if v != ""]
        ctx.speichern("risiken_neu", [dict(zeit=z, person=ctx.person, id=rid, name=name.strip(), kapitel=kap,
                                           warnsignal=ws.strip(), reaktion=re_.strip())],
                      f"«{name.strip()}» angelegt – jetzt im Tab «Bewerten» einschätzen.", weitere=[("definitionen", rows)])
