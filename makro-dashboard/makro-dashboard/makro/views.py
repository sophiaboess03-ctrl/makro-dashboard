"""Die einzelnen Seiten der App."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
import streamlit as st

from . import config as C
from . import logic as L
from . import ui
from .storage import jetzt, zahl


@dataclass
class Ctx:
    daten: L.Daten
    status: pd.DataFrame
    kombis: list
    szen: pd.DataFrame
    risiken: pd.DataFrame
    alloc: dict
    tabellen: dict
    person: str
    speichern: callable
    rezession: pd.Series | None
    fred_fehler: list = field(default_factory=list)
    fred_stand: str = ""
    zyklus_team: pd.DataFrame | None = None
    zyklus_modell: dict | None = None


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------
def _ind_ids(kap_id: str) -> list[str]:
    eigene = [i["id"] for i in C.INDIKATOREN if i["kapitel"] == kap_id]
    fremde = [i["id"] for i in C.INDIKATOREN if kap_id in i["auch_in"]]
    return eigene + fremde


def _fazit(ctx: Ctx, kap_id: str) -> tuple[str, str]:
    n = ctx.tabellen["notizen"]
    if n is not None and not n.empty:
        f = n[(n["kapitel"] == kap_id) & (n["typ"] == "fazit")].sort_values("zeit")
        if not f.empty:
            r = f.iloc[-1]
            return r["text"], f"Team-Fazit von {r['person']}, {r['zeit'][:10]}"
    return C.KAPITEL_BY_ID[kap_id]["fazit"], "Fazit aus der Makroanalyse (Nov. 2026)"


def _zaehler(st_df: pd.DataFrame, ids: list[str]) -> dict:
    a = st_df.loc[ids, "ampel"].value_counts()
    return {k: int(a.get(k, 0)) for k in (L.ROT, L.GELB, L.GRUEN, L.KONTEXT, L.LEER)}


def _zaehler_html(z: dict) -> str:
    teile = []
    for k in (L.ROT, L.GELB, L.GRUEN):
        teile.append(f'<span class="pill" style="background:{ui.HELL[k]}; color:{ui.FARBE[k]}; font-weight:700">'
                     f'{z[k]} {k}</span>')
    return "".join(teile)


def _signale(ctx: Ctx, kap_id: str | None = None) -> list[dict]:
    """Alle aktiven Signale: rote Indikatoren, Kombi-Signale, Risiko-Warnsignale."""
    out = []
    for _, r in ctx.status.iterrows():
        if r["ampel"] == L.ROT and (kap_id is None or r["kapitel"] == kap_id or kap_id in C.IND_BY_ID[r["id"]]["auch_in"]):
            out.append(dict(stufe="rot", titel=f"{r['name']}: {r['anzeige']}", text=C.IND_BY_ID[r["id"]]["aktion"],
                            kapitel=r["kapitel"]))
    for k in ctx.kombis:
        if k["aktiv"] and (kap_id is None or k["kapitel"] == kap_id):
            out.append(dict(stufe="rot", titel=f"Kombi-Signal: {k['name']}", text=f"{k['regel']} → {k['aktion']}",
                            kapitel=k["kapitel"]))
    for _, r in ctx.risiken.iterrows():
        if r["signal"] and (kap_id is None or r["kapitel"] == kap_id):
            wer = " · ".join(x for x in (f"automatisch: {r['signal_auto']}" if r["signal_auto"] else "",
                                         f"gemeldet von {r['signal_team']}" if r["signal_team"] else "") if x)
            out.append(dict(stufe="rot", titel=f"Warnsignal Risiko: {r['name']}", text=f"{wer} → {r['reaktion']}",
                            kapitel=r["kapitel"]))
    return out


def _signal_box(s: dict):
    st.markdown(f'<div class="signal {"gelb" if s["stufe"] == "gelb" else ""}"><b>{html.escape(s["titel"])}</b>'
                f'<br><span class="klein">{html.escape(s["text"])}</span></div>', unsafe_allow_html=True)


def _ind_tabelle(ctx: Ctx, ids: list[str]):
    d = ctx.status.loc[ids].copy()
    d["Ampel"] = d["ampel"].map(lambda a: {"Rot": "🔴 Rot", "Gelb": "🟡 Gelb", "Grün": "🟢 Grün",
                                             "Kontext": "⚪ Kontext"}.get(a, "– keine Daten"))
    d["Trend"] = d["pfeil"] + " " + d["trend"].replace({"neutral": ""})
    d["Stand"] = d["datum"].map(ui.datum_txt)
    d["Herkunft"] = d["herkunft"] + d["person"].map(lambda p: f" ({p})" if p else "")
    out = d[["name", "anzeige", "Ampel", "Trend", "vor_anzeige", "Stand", "schwelle", "Herkunft"]]
    out.columns = ["Indikator", "Wert", "Ampel", "Trend", "Vorwert", "Stand", "Schwelle", "Herkunft"]
    st.dataframe(out, hide_index=True, width="stretch")


def eingabe_formular(ctx: Ctx, ind_id: str | None = None, key: str = "f"):
    """Formular für einen neuen Wert (mit 2 Quellen, wie in eurer Vorgabe)."""
    if ind_id is None:
        optionen = [i["id"] for i in sorted(C.INDIKATOREN, key=lambda i: (i["quelle"] != "manuell",
                                                                      [k["id"] for k in C.KAPITEL].index(i["kapitel"])))
                    if i["quelle"] != "berechnet"]
        ind_id = st.selectbox("Indikator", optionen, key=f"sel_{key}", format_func=lambda i: (
            f"{C.KAPITEL_BY_ID[C.IND_BY_ID[i]['kapitel']]['icon']} {C.IND_BY_ID[i]['name']} "
            f"({'manuell' if C.IND_BY_ID[i]['quelle'] == 'manuell' else 'FRED – Überschreiben möglich'})"))
        r = ctx.status.loc[ind_id]
        st.caption(f"Aktuell: {r['anzeige']} (Stand {ui.datum_txt(r['datum'])}, {r['herkunft']}) · "
                   f"Schwelle: {C.IND_BY_ID[ind_id]['schwelle']}")
    with st.form(key=f"form_{key}", clear_on_submit=True):
        ind = C.IND_BY_ID[ind_id]
        c1, c2 = st.columns(2)
        wert = c1.text_input(f"Wert in {ind['einheit']}", placeholder="z. B. 54,2")
        datum = c2.date_input("Datum / Periode (bei Monatswerten: 1. des Monats)", value=date.today(), format="DD.MM.YYYY")
        q1 = c1.text_input("Quelle 1", placeholder=ind["quelle1"])
        q2 = c2.text_input("Quelle 2", placeholder=ind["quelle2"])
        notiz = st.text_input("Notiz (optional)", placeholder="z. B. über Erwartung, Revision des Vormonats …")
        ok = st.form_submit_button("Wert speichern", type="primary")
    if ok:
        w = zahl(wert)
        if w is None:
            st.error("Bitte eine Zahl eingeben (Komma oder Punkt geht beides).")
        elif not q1.strip() or not q2.strip():
            st.error("Bitte zwei Quellen angeben – eure Vorgabe: jede Information mit mind. 2 Quellen.")
        else:
            ctx.speichern("werte", [dict(zeit=jetzt(), person=ctx.person, indikator=ind_id, datum=datum.isoformat(),
                                         wert=w, quelle1=q1.strip(), quelle2=q2.strip(), notiz=notiz.strip())],
                          f"{ind['name']}: {wert} gespeichert.")


# ---------------------------------------------------------------------------
# Gesamtbild
# ---------------------------------------------------------------------------
def seite_gesamtbild(ctx: Ctx):
    st.title("🇺🇸 Makrolage USA – Gesamtbild")
    st.caption(f"Portfoliosimulation FHGR · Dez. 2026 – Feb. 2027 · {ctx.fred_stand}")
    if ctx.fred_fehler:
        st.warning(f"{len(ctx.fred_fehler)} FRED-Reihen konnten nicht geladen werden – dort werden die Werte aus der "
                   f"Analyse bzw. Team-Einträge gezeigt. ({', '.join(ctx.fred_fehler[:6])}{' …' if len(ctx.fred_fehler) > 6 else ''})")

    alle = list(ctx.status.index)
    z = _zaehler(ctx.status, alle)
    signale = _signale(ctx)
    zt, zm = ctx.zyklus_team, ctx.zyklus_modell
    team_von, team_bis = zt["von"].mean(), zt["bis"].mean()
    tage = (pd.Timestamp(C.SIMULATION_ENDE) - pd.Timestamp.today().normalize()).days

    ui.kopf_kpis([
        ("Zyklusphase (Team)", html.escape(L.phase((team_von + team_bis) / 2)),
         f"Team {ui.de(team_von, 2)}–{ui.de(team_bis, 2)} · Modell: {html.escape(zm['phase'])}"),
        ("Ampeln", f'<span style="color:{ui.FARBE[L.ROT]}">{z[L.ROT]}</span> · <span style="color:{ui.FARBE[L.GELB]}">{z[L.GELB]}</span>'
                   f' · <span style="color:{ui.FARBE[L.GRUEN]}">{z[L.GRUEN]}</span>',
         f"Rot · Gelb · Grün von {len(alle)} Indikatoren ({z[L.KONTEXT]} Kontext)"),
        ("Aktive Signale", str(len(signale)), "rote Indikatoren, Kombi-Signale, Risiko-Warnsignale"),
        ("Tage bis Abgabe", str(tage) if tage >= 0 else "vorbei", "Abgabe Portfoliosimulation 28.02.2027"),
    ])

    st.subheader("Kapitel auf einen Blick")
    for zeile in range(0, len(C.KAPITEL), 4):
        cols = st.columns(4)
        for col, kap in zip(cols, C.KAPITEL[zeile:zeile + 4]):
            ids = [i["id"] for i in C.INDIKATOREN if i["kapitel"] == kap["id"]]
            zz = _zaehler(ctx.status, ids)
            kern = [i for i in ids if C.IND_BY_ID[i]["kern"]][:3] or ids[:3]
            n_sig = len(_signale(ctx, kap["id"]))
            with col.container(border=True):
                st.markdown(f'<div class="kap-titel">{kap["icon"]} {html.escape(kap["name"])}</div>'
                            f'<div style="margin:4px 0 6px">{_zaehler_html(zz)}'
                            f'{"<span class=pill style=background:#FBE9E9;color:#D64545>⚠ " + str(n_sig) + " Signal(e)</span>" if n_sig else ""}</div>',
                            unsafe_allow_html=True)
                for i in kern:
                    r = ctx.status.loc[i]
                    st.markdown(f'<div class="klein">{ui.badge(r["ampel"])} {html.escape(r["name"])}: '
                                f'<b>{html.escape(r["anzeige"])}</b> <span style="color:{ui.TREND_FARBE[r["trend"]]}">{r["pfeil"]}</span></div>',
                                unsafe_allow_html=True)
                fazit, _ = _fazit(ctx, kap["id"])
                st.markdown(f'<div class="klein" style="margin-top:6px">{html.escape(fazit[:170])}{"…" if len(fazit) > 170 else ""}</div>',
                            unsafe_allow_html=True)

    st.divider()
    l, r = st.columns([1.05, 1])
    with l:
        st.subheader("Gesamtempfehlung Portfolio")
        a = ctx.alloc
        c1, c2 = st.columns([1, 1.1])
        with c1:
            ui.allokation_donut(a["aktien"], a["neutral"]["aktien"], a["cash"], a["neutral"].get("cash", 0))
        with c2:
            ui.zahl_block("Aktienquote", f"{a['aktien']:.0f} %", f"{ui.de(a['aktien_tilt'], 0, True)} PP vs. neutral")
            ui.zahl_block("Duration Anleihen", f"{ui.de(a['duration'])} J.",
                          f"{ui.de(a['duration'] - a['neutral']['duration'], 1, True)} J. vs. neutral")
            ui.zahl_block("TIPS-Anteil an Anleihen", f"{a['tips']:.0f} %")
        if a["korridor"]:
            st.warning("Ausserhalb des IC-Arbeitskorridors: " + "; ".join(a["korridor"]) + ".")
        ow = a["sektoren"][a["sektoren"]["empfehlung"] == "Übergewichten"]["sektor"].tolist()
        uw = a["sektoren"][a["sektoren"]["empfehlung"] == "Untergewichten"]["sektor"].tolist()
        st.markdown(f"**Übergewichten:** {', '.join(ow) or '–'}  \n**Untergewichten:** {', '.join(uw) or '–'}  \n"
                    f"**Laufzeiten:** Schwerpunkt 1–7 J., nichts > 10 J.")
        if a["konflikt"]:
            st.warning("Die Signale ziehen die Duration in verschiedene Richtungen – siehe Seite Allokation.")
        st.caption("Regelbasiert aus euren Aktionen, Ampeln und Team-Wahrscheinlichkeiten. Details auf der Seite «Allokation».")
    with r:
        st.subheader(f"Aktive Signale ({len(signale)})")
        if not signale:
            st.success("Keine roten Signale.")
        for s in signale[:9]:
            _signal_box(s)
        if len(signale) > 9:
            st.caption(f"… und {len(signale) - 9} weitere auf den Kapitelseiten.")

    st.divider()
    l, r = st.columns(2)
    with l:
        st.subheader("Szenarien (Team-Wahrscheinlichkeiten)")
        ui.szenario_chart(ctx.szen)
        n_team = int(ctx.szen["n"].max()) if len(ctx.szen) else 0
        st.caption("Startwerte aus eurer Analyse, bis das Team selbst bewertet." if n_team == 0
                   else f"Durchschnitt der neusten Schätzung jeder Person (bis {n_team} Personen); Hover zeigt die Spanne.")
    with r:
        st.subheader("Risiko-Matrix")
        ui.risiko_matrix(ctx.risiken, 330)

    st.divider()
    l, r = st.columns([1.3, 1])
    with l:
        st.subheader("Konjunkturzyklus")
        ui.zyklus_chart(zt, zm)
    with r:
        st.subheader("Nächste Termine")
        _termine_tabelle(n=6)


# ---------------------------------------------------------------------------
# Kapitel
# ---------------------------------------------------------------------------
def seite_kapitel(ctx: Ctx, kap_id: str):
    kap = C.KAPITEL_BY_ID[kap_id]
    st.title(f"{kap['icon']} {kap['name']}")
    ids = _ind_ids(kap_id)
    eigene = [i for i in ids if C.IND_BY_ID[i]["kapitel"] == kap_id]
    fazit, wer = _fazit(ctx, kap_id)
    st.markdown(f"> {html.escape(fazit)}\n\n<span class='klein'>{wer}</span>", unsafe_allow_html=True)
    st.markdown(_zaehler_html(_zaehler(ctx.status, eigene)), unsafe_allow_html=True)

    sig = _signale(ctx, kap_id)
    if sig:
        st.markdown("#### Aktive Signale")
        for s in sig:
            _signal_box(s)

    st.markdown("#### Kennzahlen")
    ui.kpi_raster(ctx.status, ids, 4)
    fremd = [i for i in ids if i not in eigene]
    if fremd:
        st.caption("Auch gezeigt (gehört zu einem anderen Kapitel): " + ", ".join(C.IND_BY_ID[i]["name"] for i in fremd))

    if kap_id == "wachstum":
        st.markdown("#### Zyklus-Einordnung")
        c1, c2 = st.columns([1.4, 1])
        with c1:
            ui.zyklus_chart(ctx.zyklus_team, ctx.zyklus_modell, 280)
        with c2:
            zm = ctx.zyklus_modell
            st.markdown(f"**Modell:** {zm['phase']} (Position {ui.de(zm['pos'], 2)})")
            st.caption(f"{sum(x for _, x in zm['spaet'])} von {len(zm['spaet'])} Spätzyklus-Merkmalen erfüllt, "
                       f"{zm['n_rez']} Rezessionsmerkmal(e). Ohne Rezessionsmerkmal: Position 0,30 + 0,27 × Anteil Spätzyklus.")
            st.markdown("<div class='klein'>" + "<br>".join(f"{'✅' if ok else '▫️'} {html.escape(n)}"
                                                           for n, ok in zm["spaet"] + zm["rezession"]) + "</div>",
                        unsafe_allow_html=True)
            st.markdown("<div class='klein' style='margin-top:8px'>" + "<br>".join(
                f"<b>{html.escape(str(r['person']))}</b>: {ui.de(r['von'], 2)}–{ui.de(r['bis'], 2)} "
                f"({L.phase((r['von'] + r['bis']) / 2)}) {html.escape(str(r['kommentar'] or ''))}"
                for _, r in ctx.zyklus_team.iterrows()) + "</div>", unsafe_allow_html=True)

    st.markdown("#### Details je Indikator")
    jahre = st.segmented_control("Zeitraum", [2, 5, 10], default=5, format_func=lambda j: f"{j} Jahre",
                                 key=f"jahre_{kap_id}") or 5
    for n_i, i in enumerate(ids):
        ind, r = C.IND_BY_ID[i], ctx.status.loc[i]
        icon = {"Rot": "🔴", "Gelb": "🟡", "Grün": "🟢", "Kontext": "⚪"}.get(r["ampel"], "▫️")
        with st.expander(f"{icon} {ind['name']} — {r['anzeige']}", expanded=n_i == 0):
            c1, c2 = st.columns([1.7, 1])
            with c1:
                ui.indikator_chart(ind, ctx.daten.reihe(i), ctx.rezession, jahre)
            with c2:
                art = {"fred": f"automatisch (FRED {ind.get('series')})", "manuell": "Team-Eingabe",
                       "berechnet": "automatisch berechnet"}[ind["quelle"]]
                st.markdown(f"**Schwelle:** {ind['schwelle']}  \n<span class='klein'>Herkunft Schwelle: {ind['herkunft']}</span>",
                            unsafe_allow_html=True)
                st.markdown(f"**Annahme:** {ind['annahme']}")
                st.markdown(f"**Aktion:** {ind['aktion']}")
                st.markdown(f"<span class='klein'>Daten: {art} · Quellen: {html.escape(ind['quelle1'])}; "
                            f"{html.escape(ind['quelle2'])} · Rhythmus: {ind['rhythmus']} · Nächster Wert: {ind['naechster']}</span>",
                            unsafe_allow_html=True)
                if r["herkunft"] == "Team":
                    st.markdown(f"<span class='klein'>Neuster Wert von {html.escape(r['person'])}: "
                                f"{html.escape(str(r['quelle1']))}; {html.escape(str(r['quelle2']))}</span>",
                                unsafe_allow_html=True)
            if ind["quelle"] != "berechnet":
                with st.popover("➕ Neuen Wert eintragen"):
                    eingabe_formular(ctx, i, key=f"{kap_id}_{i}")

    themen = [t["id"] for t in C.THEMEN if kap_id in t["kapitel"]]
    if themen:
        st.markdown("#### Szenarien")
        ui.szenario_chart(ctx.szen, themen)
        _szenario_tabelle(ctx, themen)
    ris = ctx.risiken[ctx.risiken["kapitel"] == kap_id]
    if len(ris):
        st.markdown("#### Risiken")
        _risiko_tabelle(ris)

    st.markdown("#### Notizen & Fazit")
    _notizen(ctx, kap_id)


def _szenario_tabelle(ctx: Ctx, themen):
    d = ctx.szen[ctx.szen["thema"].isin(themen)].copy()
    d["Thema"] = d["thema"].map(lambda t: C.THEMA_BY_ID[t]["name"])
    d["Team-W."] = d["p"].map(lambda x: f"{x:.0f} %")
    d["Spanne"] = d.apply(lambda r: "–" if r["n"] == 0 else f"{r['p_min']:.0f}–{r['p_max']:.0f} % (n={r['n']})", axis=1)
    d["Start"] = d["start"].map(lambda x: f"{x:.0f} %")
    for k in ("annahmen", "folge", "aktion"):
        d[k] = d["id"].map(lambda i: C.SZEN_BY_ID[i][k])
    out = d[["Thema", "name", "Team-W.", "Spanne", "Start", "annahmen", "folge", "aktion"]]
    out.columns = ["Thema", "Szenario", "Team-W.", "Spanne Team", "Startwert", "Annahmen", "Folge", "Aktion"]
    st.dataframe(out, hide_index=True, width="stretch")


def _risiko_tabelle(ris: pd.DataFrame):
    d = ris.copy()
    d["W."] = d["p"].map(lambda x: "–" if pd.isna(x) else f"{x:.0f} %")
    d["Ausw."] = d["impact"].map(lambda x: "–" if pd.isna(x) else f"{x:.1f}")
    d["Signal"] = d["signal"].map(lambda x: "🔴 aktiv" if x else "–")
    out = d[["name", "W.", "Ausw.", "Signal", "p_text", "warnsignal", "reaktion", "basis"]]
    out.columns = ["Risiko", "Wahrsch.", "Auswirkung", "Warnsignal", "Analyse", "Warnsignale", "Reaktion", "Basis"]
    st.dataframe(out, hide_index=True, width="stretch")


def _notizen(ctx: Ctx, kap_id: str):
    n = ctx.tabellen["notizen"]
    d = n[(n["kapitel"] == kap_id)].sort_values("zeit", ascending=False) if n is not None and not n.empty else pd.DataFrame()
    for _, r in d.head(8).iterrows():
        st.markdown(f"<span class='klein'><b>{html.escape(r['person'])}</b> · {r['zeit'][:10]} · "
                    f"{'Fazit' if r['typ'] == 'fazit' else 'Notiz'}</span><br>{html.escape(r['text'])}", unsafe_allow_html=True)
    with st.form(f"notiz_{kap_id}", clear_on_submit=True):
        text = st.text_area("Neue Notiz (z. B. Fed-Entscheid, neues Ereignis)", height=80)
        als_fazit = st.checkbox("Als neues Kapitel-Fazit verwenden (ersetzt das angezeigte Fazit)")
        if st.form_submit_button("Speichern") and text.strip():
            ctx.speichern("notizen", [dict(zeit=jetzt(), person=ctx.person, kapitel=kap_id,
                                           typ="fazit" if als_fazit else "notiz", text=text.strip())], "Notiz gespeichert.")


# ---------------------------------------------------------------------------
# Szenarien & Risiken
# ---------------------------------------------------------------------------
def seite_szenarien_risiken(ctx: Ctx):
    st.title("🎲 Szenarien & Risiken")
    st.caption("Wahrscheinlichkeiten = Durchschnitt der neusten Schätzung jeder Person (pro Thema auf 100 % normiert). "
               "Bewerten könnt ihr auf der Seite «Bewerten».")
    ui.szenario_chart(ctx.szen)
    _szenario_tabelle(ctx, [t["id"] for t in C.THEMEN])
    uneinig = ctx.szen[ctx.szen["uneinig"] >= 20]
    if len(uneinig):
        st.info("Grosse Uneinigkeit im Team (≥ 20 PP Spanne): " +
                ", ".join(f"{r['name']} ({C.THEMA_BY_ID[r['thema']]['name']}: {r['p_min']:.0f}–{r['p_max']:.0f} %)"
                          for _, r in uneinig.iterrows()) + " – lohnt sich zu besprechen.")
    st.divider()
    st.subheader("Risiko-Matrix")
    ui.risiko_matrix(ctx.risiken)
    _risiko_tabelle(ctx.risiken.sort_values("score", ascending=False))


# ---------------------------------------------------------------------------
# Allokation
# ---------------------------------------------------------------------------
def seite_allokation(ctx: Ctx):
    a = ctx.alloc
    st.title("🧭 Allokation – Gesamtempfehlung")
    st.caption("Regelbasiert aus euren Aktionen je Indikator, den Team-Wahrscheinlichkeiten und den Warnsignalen. "
               "Sie ersetzt nicht eure Entscheidung, sondern zeigt, wohin die Signale zusammen zeigen.")
    ui.kopf_kpis([
        ("Aktien (S&P 500)", f"{a['aktien']:.0f} %", f"{ui.de(a['aktien_tilt'], 0, True)} PP vs. neutral {a['neutral']['aktien']:.0f} %"),
        ("Treasuries / Cash", f"{a['anleihen']:.0f} % / {a['cash']:.0f} %", "Treasuries: Schwerpunkt 1–7 J., nichts > 10 J."),
        ("Duration", f"{ui.de(a['duration'])} J.", f"szenario-gewichtet {ui.de(a['duration_szen'])} J."),
        ("TIPS-Anteil an Anleihen", f"{a['tips']:.0f} %", f"szenario-gewichtet {a['tips_szen']:.0f} %"),
    ])

    st.markdown("#### So kommen die Zahlen zustande")
    st.markdown(
        f"- **Aktienquote:** neutraler Anker {a['neutral']['aktien']:.0f} % Aktien / {100 - a['neutral']['aktien'] - a['neutral'].get('cash', 0):.0f} % "
        f"Treasuries / {a['neutral'].get('cash', 0):.0f} % Cash (Investment Committee, Regler auf «Einstellungen») plus die Summe "
        f"der aktiven Signale, begrenzt auf ±{C.AKTIEN_MAX_ABWEICHUNG} PP. IC-Arbeitskorridor: Aktien 40–50 %, Treasuries 45–55 %, Cash 0–10 %.\n"
        f"- **Duration:** Durchschnitt aus der szenario-gewichteten Duration (Hawkishe Fed 3,5 J., Inflationsschock 2,5 J., "
        f"Risk-off 4,5 J., gewichtet mit euren Wahrscheinlichkeiten = {ui.de(a['duration_szen'], 2)} J.) und den Zielen aller aktiven "
        f"Signale ({', '.join(ui.de(x) + ' J.' for x in a['duration_ziele']) or 'keine'}).\n"
        f"- **TIPS:** szenario-gewichtet (Basis 15 %, Inflationsschock 25 %, Risk-off ohne Angabe → 15 %); ein aktives "
        f"Inflationssignal setzt mindestens 25 %.\n"
        f"- **Sektoren:** Σ (Wahrscheinlichkeit × Wirkung) über alle Szenario-Themen plus aktive Signale. "
        f"Wirkung je Szenario aus eurer Spalte «Folge» (+ = profitiert, − = leidet).")
    if a["korridor"]:
        st.warning("Ausserhalb des IC-Arbeitskorridors: " + "; ".join(a["korridor"]) + " – im Team begründen oder Signale prüfen.")
    if a["konflikt"]:
        st.warning("Konflikt: Die aktiven Signale verlangen sehr unterschiedliche Durationen (z. B. Inflation kurz, "
                   "Kreditstress lang). Der Mittelwert ist dann nur ein Kompromiss – im Team entscheiden.")

    st.markdown("#### Signale und ihre Wirkung")
    reg = pd.DataFrame(a["regeln"])
    reg["Status"] = reg["aktiv"].map(lambda x: "🔴 aktiv" if x else "–")
    reg["Aktien"] = reg["aktien"].map(lambda x: f"{ui.de(x, 0, True)} PP" if x else "")
    reg["Duration"] = reg["duration"].map(lambda x: f"{ui.de(x)} J." if x is not None and not pd.isna(x) else "")
    reg["TIPS"] = reg["tips"].map(lambda x: f"{x:g} %" if x is not None and not pd.isna(x) else "")
    reg["Sektoren"] = reg["sektoren"].map(lambda d: ", ".join(f"{k} {ui.de(v, 1, True)}" for k, v in d.items()))
    reg = reg.sort_values("aktiv", ascending=False)
    out = reg[["Status", "name", "Aktien", "Duration", "TIPS", "Sektoren", "grund", "herkunft"]]
    out.columns = ["Status", "Signal", "Aktien", "Duration-Ziel", "TIPS-Ziel", "Sektoren", "Auslöser", "Herkunft"]
    st.dataframe(out, hide_index=True, width="stretch")
    st.caption("«Grösse: Vorschlag» = die Richtung kommt aus eurer Analyse, die Höhe (z. B. −5 PP) hat Claude ergänzt – "
               "im Team prüfen und in makro/logic.py anpassen.")

    st.markdown("#### Sektoren")
    c1, c2 = st.columns([1.2, 1])
    with c1:
        ui.sektor_chart(a["sektoren"])
    with c2:
        d = a["sektoren"][["sektor", "score", "empfehlung", "beitraege"]].copy()
        d["score"] = d["score"].map(lambda x: ui.de(x, 2, True))
        d.columns = ["Sektor", "Score", "Empfehlung", "Beiträge"]
        st.dataframe(d, hide_index=True, width="stretch", height=420)


# ---------------------------------------------------------------------------
# Bewerten
# ---------------------------------------------------------------------------
def _letzte(df: pd.DataFrame, person: str, key: str, item: str, feld: str):
    if df is None or df.empty:
        return None
    d = df[(df["person"] == person) & (df[key] == item)].sort_values("zeit")
    return zahl(d.iloc[-1][feld]) if len(d) else None


def seite_bewerten(ctx: Ctx):
    st.title("🗳️ Bewerten")
    st.caption(f"Du bewertest als **{ctx.person}**. Gespeichert wird jeweils deine neuste Einschätzung; "
               "die App mittelt über alle Personen.")
    t1, t2, t3 = st.tabs(["Szenarien", "Risiken", "Zyklus"])
    with t1:
        for th in C.THEMEN:
            sz = [s for s in C.SZENARIEN if s["thema"] == th["id"]]
            with st.form(f"sz_{th['id']}"):
                st.markdown(f"**{th['name']}** <span class='klein'>({th['quelle']})</span>", unsafe_allow_html=True)
                werte = {}
                cols = st.columns(len(sz))
                for col, s in zip(cols, sz):
                    vor = _letzte(ctx.tabellen["szenario_votes"], ctx.person, "szenario", s["id"], "p")
                    start = int(round(vor if vor is not None else ctx.szen.loc[s["id"], "p"]))
                    werte[s["id"]] = col.slider(s["name"], 0, 100, start, 5, help=f"{s['annahmen']} → {s['aktion']}")
                summe = sum(werte.values())
                st.caption(f"Summe: {summe} % – wird beim Speichern auf 100 % normiert.")
                if st.form_submit_button("Speichern", type="primary"):
                    if summe == 0:
                        st.error("Mindestens ein Szenario braucht eine Wahrscheinlichkeit > 0.")
                    else:
                        ctx.speichern("szenario_votes", [dict(zeit=jetzt(), person=ctx.person, szenario=k,
                                                              p=round(v / summe * 100, 1)) for k, v in werte.items()],
                                      f"Einschätzung «{th['name']}» gespeichert.")
    with t2:
        ris = ctx.risiken
        with st.form("risiken"):
            zeilen = []
            for rid, r in ris.iterrows():
                st.markdown(f"**{r['name']}** <span class='klein'>Analyse: {r['p_text']} · Warnsignale: "
                            f"{html.escape(r['warnsignal'])}</span>", unsafe_allow_html=True)
                c1, c2, c3, c4 = st.columns([1.3, 1, 0.9, 1.6])
                vp = _letzte(ctx.tabellen["risiko_votes"], ctx.person, "risiko", rid, "p")
                vi = _letzte(ctx.tabellen["risiko_votes"], ctx.person, "risiko", rid, "impact")
                vs = _letzte(ctx.tabellen["risiko_votes"], ctx.person, "risiko", rid, "signal")
                p = c1.slider("Wahrscheinlichkeit (%)", 0, 100, int(round(vp if vp is not None else (r["p"] if not pd.isna(r["p"]) else 20))),
                              5, key=f"rp_{rid}")
                imp = c2.select_slider("Auswirkung", [1, 2, 3, 4, 5],
                                       int(round(vi if vi is not None else (r["impact"] if not pd.isna(r["impact"]) else 3))),
                                       key=f"ri_{rid}", help="1 = kaum spürbar · 5 = schwerer Einbruch im Portfolio")
                sig = c3.checkbox("Warnsignal ausgelöst", bool(vs) if vs is not None else False, key=f"rs_{rid}")
                kom = c4.text_input("Kommentar", key=f"rk_{rid}", label_visibility="visible")
                zeilen.append(dict(zeit=jetzt(), person=ctx.person, risiko=rid, p=p, impact=imp, signal=int(sig), kommentar=kom))
            if st.form_submit_button("Alle Risiken speichern", type="primary"):
                ctx.speichern("risiko_votes", zeilen, "Risikobewertung gespeichert.")
        with st.expander("➕ Neues Risiko hinzufügen"):
            with st.form("neues_risiko", clear_on_submit=True):
                name = st.text_input("Name")
                kap = st.selectbox("Kapitel", [k["id"] for k in C.KAPITEL], index=7,
                                   format_func=lambda k: C.KAPITEL_BY_ID[k]["name"])
                ws = st.text_input("Warnsignal(e)")
                re_ = st.text_input("Vorab festgelegte Reaktion")
                if st.form_submit_button("Risiko anlegen") and name.strip():
                    rid = "r_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:30]
                    ctx.speichern("risiken_neu", [dict(zeit=jetzt(), person=ctx.person, id=rid, name=name.strip(),
                                                       kapitel=kap, warnsignal=ws.strip(), reaktion=re_.strip())],
                                  "Risiko angelegt – jetzt oben bewerten.")
    with t3:
        st.markdown("Wo steht die US-Wirtschaft auf der Zykluskurve? 0 = Tiefpunkt · 0,25 = Erholung · 0,5 = Hochphase · "
                    "0,75 = Abschwung · 1 = Tiefpunkt.")
        ui.zyklus_chart(ctx.zyklus_team, ctx.zyklus_modell, 230)
        with st.form("zyklus"):
            von_bis = st.slider("Deine Einordnung (Spanne)", 0.0, 1.0, (0.48, 0.58), 0.01)
            st.caption(f"= {L.phase(von_bis[0])} bis {L.phase(von_bis[1])}")
            kom = st.text_input("Begründung (kurz)")
            if st.form_submit_button("Speichern", type="primary"):
                ctx.speichern("zyklus_votes", [dict(zeit=jetzt(), person=ctx.person, von=von_bis[0], bis=von_bis[1],
                                                    kommentar=kom.strip())], "Zyklus-Einordnung gespeichert.")


# ---------------------------------------------------------------------------
# Daten eintragen, Termine, Protokoll, Einstellungen
# ---------------------------------------------------------------------------
def seite_eintragen(ctx: Ctx):
    st.title("✏️ Daten eintragen")
    st.markdown("Für Indikatoren, die nicht auf FRED sind (ISM, Conference Board, NFIB, BofA-Umfrage, Forward-KGV, "
                "Forward-EPS, DXY) – oder um einen FRED-Wert mit einer neueren Zahl zu überschreiben. "
                "**Der Wert mit dem neusten Datum gilt.** Ampel, Trend, Allokation und Gesamtbild passen sich sofort an.")
    eingabe_formular(ctx, None, key="haupt")
    st.divider()
    st.subheader("Fällige Updates")
    man = [i for i in C.INDIKATOREN if i["quelle"] == "manuell"]
    zeilen = []
    for i in man:
        r = ctx.status.loc[i["id"]]
        alter = (pd.Timestamp.today() - pd.Timestamp(r["datum"])).days if r["datum"] is not None and not pd.isna(r["datum"]) else None
        zeilen.append(dict(Indikator=i["name"], Wert=r["anzeige"], Stand=ui.datum_txt(r["datum"]),
                           Alter=f"{alter} Tage" if alter is not None else "–", Rhythmus=i["rhythmus"],
                           Nächster=i["naechster"], Herkunft=r["herkunft"]))
    st.dataframe(pd.DataFrame(zeilen), hide_index=True, width="stretch")


def _termine_tabelle(n: int | None = None):
    heute = pd.Timestamp.today().normalize()
    d = pd.DataFrame(C.TERMINE, columns=["sort", "Datum", "Ereignis", "Betrifft"])
    d["sort"] = pd.to_datetime(d["sort"])
    d["in"] = (d["sort"] - heute).dt.days
    if n:
        d = d[d["in"] >= -1].head(n)
    d["In"] = d["in"].map(lambda x: "heute" if x == 0 else f"in {x} T." if x > 0 else "vorbei")
    st.dataframe(d[["Datum", "In", "Ereignis", "Betrifft"]], hide_index=True, width="stretch",
                 column_config={"Datum": st.column_config.TextColumn(width="medium"),
                                "In": st.column_config.TextColumn(width="small")})


def seite_termine(ctx: Ctx):
    st.title("📅 Termine bis Abgabe")
    _termine_tabelle()
    st.caption("Datum «Mitte Dez.» u. Ä. ist für den Countdown angenähert – genaue Daten im BLS-/BEA-Kalender prüfen.")


def seite_protokoll(ctx: Ctx):
    st.title("🧾 Protokoll")
    st.caption("Alle Team-Eingaben, neuste zuerst. Nichts wird überschrieben – Korrekturen sind einfach neue Einträge.")
    namen = {"werte": "Werte", "szenario_votes": "Szenario-Bewertungen", "risiko_votes": "Risiko-Bewertungen",
             "zyklus_votes": "Zyklus", "notizen": "Notizen", "einstellungen": "Einstellungen", "risiken_neu": "Neue Risiken", "sektorvoten": "Sektorvoten"}
    tabs = st.tabs(list(namen.values()))
    for t, key in zip(tabs, namen):
        with t:
            d = ctx.tabellen.get(key)
            if d is None or d.empty:
                st.info("Noch keine Einträge.")
            else:
                d = d.sort_values("zeit", ascending=False).copy()
                if key == "werte":
                    d["indikator"] = d["indikator"].map(lambda i: C.IND_BY_ID.get(i, {}).get("name", i))
                st.dataframe(d, hide_index=True, width="stretch")
                st.download_button("Als CSV herunterladen", d.to_csv(index=False).encode("utf-8"), f"{key}.csv",
                                   "text/csv", key=f"dl_{key}")


def seite_einstellungen(ctx: Ctx):
    st.title("⚙️ Einstellungen")
    n = ctx.alloc["neutral"]
    st.markdown("Neutrale Ausgangsallokation – gilt für das ganze Team. Die Signale verschieben die Empfehlung davon weg.")
    with st.form("neutral"):
        ak = st.slider("Neutrale Aktienquote (%)", 0, 100, int(round(n["aktien"])), 5,
                       help="Investment Committee: Diskussionsanker 45 %, Arbeitskorridor 40–50 %")
        ca = st.slider("Cash (%)", 0, 20, int(round(n.get("cash", 5))), 1, help="Investment Committee: Korridor 0–10 %, Anker 5 %")
        du = st.slider("Neutrale Duration (Jahre)", 1.0, 7.0, float(n["duration"]), 0.5,
                       help="Nur Referenz für die Anzeige «vs. neutral»; die Empfehlung selbst kommt aus Szenarien und Signalen.")
        ti = st.slider("Neutraler TIPS-Anteil (%)", 0, 50, int(round(n["tips"])), 5)
        if st.form_submit_button("Speichern", type="primary"):
            ctx.speichern("einstellungen", [dict(zeit=jetzt(), person=ctx.person, schluessel=k, wert=v)
                                            for k, v in (("aktien", ak), ("cash", ca), ("duration", du), ("tips", ti))],
                          "Einstellungen gespeichert.")
    st.divider()
    st.subheader("Datenquellen")
    st.markdown(f"- **Speicher:** {st.session_state.get('store_name', '–')}\n- **FRED:** {ctx.fred_stand}")
    if ctx.fred_fehler:
        st.markdown("Nicht geladen: " + ", ".join(ctx.fred_fehler))
    if st.button("FRED-Daten jetzt neu laden"):
        st.cache_data.clear()
        st.rerun()
