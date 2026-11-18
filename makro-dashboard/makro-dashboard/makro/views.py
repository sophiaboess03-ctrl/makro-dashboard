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
    szdef: dict = field(default_factory=dict)
    gew: dict = field(default_factory=dict)
    lage: dict = field(default_factory=dict)
    ips: dict = field(default_factory=dict)
    einst: dict = field(default_factory=dict)
    bond: dict = field(default_factory=dict)
    sig: dict = field(default_factory=dict)
    anker: dict = field(default_factory=dict)
    fred: dict = field(default_factory=dict)
    hist: dict = field(default_factory=dict)
    erw_antworten: dict = field(default_factory=dict)
    erw_sim_sektoren: dict = field(default_factory=dict)
    store: object = None
    nur_lesen: bool = False



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
        teile.append(f'<span class="pill" style="background:{ui.HELL[k]}; color:{ui.FARBE[k]}; border-color:{ui.RAND[k]}">'
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
    d["Ampel"] = d["ampel"].map(ui.ampel_txt)
    d["Trend"] = d["pfeil"] + " " + d["trend"].replace({"neutral": ""})
    d["Stand"] = d["datum"].map(ui.datum_txt)
    d["Herkunft"] = d["herkunft"] + d["person"].map(lambda p: f" ({p})" if p else "")
    out = d[["name", "anzeige", "Ampel", "Trend", "vor_anzeige", "Stand", "schwelle", "Herkunft"]]
    out.columns = ["Indikator", "Wert", "Ampel", "Trend", "Vorwert", "Stand", "Schwelle", "Herkunft"]
    st.dataframe(ui.ampel_stil(out, ["Ampel"]), hide_index=True, width="stretch")


def eingabe_formular(ctx: Ctx, ind_id: str | None = None, key: str = "f"):
    """Formular für einen neuen Wert (mit 2 Quellen, wie in eurer Vorgabe)."""
    if ind_id is None:
        optionen = [i["id"] for i in sorted(C.INDIKATOREN, key=lambda i: (i["quelle"] != "manuell",
                                                                      [k["id"] for k in C.KAPITEL].index(i["kapitel"])))
                    if i["quelle"] != "berechnet"]
        ind_id = st.selectbox("Indikator", optionen, key=f"sel_{key}", format_func=lambda i: (
            f"{C.IND_BY_ID[i]['name']} · {C.KAPITEL_BY_ID[C.IND_BY_ID[i]['kapitel']]['name']} "
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
# Kurzzusammenfassung je Kapitel: Worum geht es, was sagen die Zahlen?
# ---------------------------------------------------------------------------
def kurzfazit(ctx: Ctx, kap_id: str) -> tuple[str, list[str]]:
    eigene = [i["id"] for i in C.INDIKATOREN if i["kapitel"] == kap_id]
    z = _zaehler(ctx.status, eigene)
    saetze = []
    bewertet = z[L.ROT] + z[L.GELB] + z[L.GRUEN]
    if bewertet:
        lage = ("überwiegend unauffällig" if z[L.ROT] == 0 and z[L.GELB] <= bewertet / 3 else
                "angespannt" if z[L.ROT] >= max(2, bewertet / 3) else "gemischt")
        saetze.append(f"Lage {lage}: {z[L.ROT]} rot, {z[L.GELB]} gelb, {z[L.GRUEN]} grün von {bewertet} bewerteten Indikatoren.")
    kern = [i for i in eigene if C.IND_BY_ID[i]["kern"]] or eigene[:3]
    teile = []
    for i in kern[:4]:
        r = ctx.status.loc[i]
        if r["wert"] is None or pd.isna(r["wert"]):
            continue
        tr = {"besser": "verbessert", "schlechter": "verschlechtert"}.get(r["trend"], "")
        teile.append(f"{r['name'].split(' (')[0]} {r['anzeige']}" + (f" ({tr})" if tr else ""))
    if teile:
        saetze.append("Kern: " + "; ".join(teile) + ".")
    neu_rot, besser = [], []
    for i in eigene:
        r = ctx.status.loc[i]
        if r["vorwert"] is None or pd.isna(r["vorwert"]):
            continue
        alt = L.ampel(C.IND_BY_ID[i], r["vorwert"])
        if r["ampel"] == L.ROT and alt != L.ROT:
            neu_rot.append(r["name"].split(" (")[0])
        if alt == L.ROT and r["ampel"] in (L.GELB, L.GRUEN):
            besser.append(r["name"].split(" (")[0])
    if neu_rot:
        saetze.append("Neu rot: " + ", ".join(neu_rot) + ".")
    if besser:
        saetze.append("Nicht mehr rot: " + ", ".join(besser) + ".")
    n_sig = len(_signale(ctx, kap_id))
    if n_sig:
        saetze.append(f"{n_sig} aktive(s) Signal(e) in diesem Kapitel – siehe unten.")
    if ctx.lage:
        treffer = {}
        for sid in ctx.szdef:
            zz = [x for x in ctx.lage[sid]["zeilen"] if x["art"] == "Indikator" and x["ind"] in C.IND_BY_ID
                  and C.IND_BY_ID[x["ind"]]["kapitel"] == kap_id and x["ok"] is not None]
            if zz:
                treffer[sid] = (sum(bool(x["ok"]) for x in zz), len(zz))
        if treffer:
            best = max(treffer, key=lambda s: treffer[s][0] / treffer[s][1])
            if treffer[best][0]:
                saetze.append("Für die Szenarien: " + ", ".join(f"{ctx.szdef[s]['kurz']} {a}/{b} Indizien erfüllt"
                                                               for s, (a, b) in treffer.items())
                              + f" → die Zahlen dieses Kapitels stützen am ehesten **{ctx.szdef[best]['kurz']}**.")
    return C.KAPITEL_WORUM.get(kap_id, ""), saetze


def _kurzfazit_box(ctx: Ctx, kap_id: str, R=None):
    worum, saetze = kurzfazit(ctx, kap_id)
    st.markdown(f"<div class='karte' style='margin-bottom:14px'><div class='karte-titel'>Kurz zusammengefasst</div>"
                f"<div><b>Worum es geht:</b> {html.escape(worum)}</div>"
                f"<div style='margin-top:6px'><b>Was die Zahlen sagen:</b> "
                f"{' '.join(html.escape(s).replace('**', '') for s in saetze) or 'Noch keine Daten.'}</div></div>",
                unsafe_allow_html=True)
    if R is not None:
        R.h("Kurz zusammengefasst")
        R.text(f"Worum es geht: {worum}")
        R.text("Was die Zahlen sagen: " + " ".join(saetze))


# ---------------------------------------------------------------------------
# Gesamtbild
# ---------------------------------------------------------------------------
def seite_gesamtbild(ctx: Ctx, prog: dict | None = None, monat: dict | None = None):
    import seitenpdf as SP

    R = SP.Sammler("Makrolage USA – Gesamtbild", "Anlagehorizont Dez. 2026 – Feb. 2027 · USD 10 Mio. · S&P 500 und US-Treasuries",
                   kurz="Gesamtbild")
    ui.seitenkopf("Makrolage USA – Gesamtbild", f"Anlagehorizont Dez. 2026 – Feb. 2027 · USD 10 Mio. · S&P 500 und US-Treasuries · {ctx.fred_stand}")
    ph = st.empty()
    if ctx.fred_fehler:
        st.warning(f"{len(ctx.fred_fehler)} FRED-Reihen konnten nicht geladen werden – dort werden die Werte aus der "
                   f"Analyse bzw. Team-Einträge gezeigt. ({', '.join(ctx.fred_fehler[:6])}{' …' if len(ctx.fred_fehler) > 6 else ''})")
    alle = list(ctx.status.index)
    z = _zaehler(ctx.status, alle)
    signale = _signale(ctx)
    zt, zm = ctx.zyklus_team, ctx.zyklus_modell
    team_von, team_bis = zt["von"].mean(), zt["bis"].mean()
    tage = (pd.Timestamp(C.SIMULATION_ENDE) - pd.Timestamp.today().normalize()).days
    haupt = ctx.szen["p"].idxmax()
    lage = ctx.lage
    a = ctx.alloc
    kpis = [
        ("Hauptszenario", f"{html.escape(ctx.szdef[haupt]['kurz'])} {ctx.szen.loc[haupt, 'p']:.0f} %",
         f"Datenlage stützt: {html.escape(ctx.szdef[lage['_favorit']]['kurz'])} ({lage[lage['_favorit']]['erfuellt']}/{lage[lage['_favorit']]['n']})"),
        ("Zyklusphase (Team)", html.escape(L.phase((team_von + team_bis) / 2)), f"Modell: {html.escape(zm['phase'])}"),
        ("Ampeln", f'<span style="color:{ui.FARBE[L.ROT]}">{z[L.ROT]}</span><span style="color:#C5CAD3"> / </span>'
                   f'<span style="color:{ui.FARBE[L.GELB]}">{z[L.GELB]}</span><span style="color:#C5CAD3"> / </span>'
                   f'<span style="color:{ui.FARBE[L.GRUEN]}">{z[L.GRUEN]}</span>', f"{len(signale)} aktive Signale"),
        ("Taktik", f"{a['aktien']:.0f} / {a['anleihen']:.0f} / {a['cash']:.0f}", html.escape(a.get("quelle_txt", ""))[:60]),
        ("Tage bis Abgabe", str(tage) if tage >= 0 else "vorbei", "Abgabe 28.02.2027"),
    ]
    ui.kopf_kpis(kpis)
    R.kpis([(k, v, s) for k, v, s in kpis])

    # Fristen
    from datetime import datetime as _dt
    jetzt_ = pd.Timestamp(_dt.now())
    fristen = []
    if jetzt_ < pd.Timestamp(C.IPS_FRIST):
        n_leer = len([k for k in ("zweck", "ansatz", "begr_faehigkeit", "liquiditaet") if not str(ctx.ips.get(k, "")).strip()])
        fristen.append(f"**IPS** bis {pd.Timestamp(C.IPS_FRIST):%d.%m. %H:%M} ({(pd.Timestamp(C.IPS_FRIST) - jetzt_).days} Tage)"
                       + (f" – noch {n_leer} Kernfelder leer" if n_leer else " – Kernfelder ausgefüllt"))
    if monat:
        f = pd.Timestamp(monat["frist"])
        if jetzt_ < f:
            n_erw = len([k for k in ctx.erw_antworten.get(monat["id"], {}) if not str(k).startswith("12_")])
            taktik = ctx.tabellen.get("taktik")
            hat_t = taktik is not None and not taktik.empty and (taktik["monat"] == monat["id"]).any()
            fristen.append(f"**Taktik & Erwartungen {monat['name']}** bis {f:%d.%m. %H:%M} ({(f - jetzt_).days} Tage) – "
                           f"Taktik {'erfasst' if hat_t else 'noch offen'}, Erwartungen {n_erw} beantwortet")
    if fristen:
        st.info("  \n".join(fristen), icon=":material/schedule:")
        R.text(" | ".join(fristen))

    # Szenarien
    st.subheader("Szenarien")
    R.h("Szenarien")
    c1, c2 = st.columns([1.2, 1])
    with c1:
        ui.szenario_chart(ctx.szen)
        st.caption(ctx.szen["basis"].iloc[0])
        if lage["_hinweis"]:
            h = lage["_hinweis"]
            st.warning(f"Die Daten sprechen stärker für {ctx.szdef[h]['kurz']} (daten-angepasst {lage[h]['p_daten']:.0f} % statt "
                       f"{ctx.szen.loc[h, 'p']:.0f} %) – Seite «Szenarien» prüfen.")
    with c2:
        g = ctx.gew["param"]
        zeilen = [(par["name"], f"{ui.de(g[par['id']], par['dez'], True)} {par['einheit']}",
                   " · ".join(f"{ctx.szdef[s]['kurz']} {ui.de(ctx.szdef[s]['param'].get(par['id'], 0), par['dez'], True)}" for s in ctx.szdef))
                  for par in C.PARAMETER if par["fragen"]]
        tab = pd.DataFrame(zeilen, columns=["Grösse (Monat)", "Gewichtet", "Base · Bull · Bear"])
        st.dataframe(tab, hide_index=True, width="stretch")
    R.balken([(ctx.szdef[s]["name"], float(ctx.szen.loc[s, "p"]), tuple(int(C.SZEN_FARBE[s][i:i + 2], 16) for i in (1, 3, 5)))
              for s in ctx.szdef], " %", "Wahrscheinlichkeiten", 100)
    R.tabelle(tab, [50, 30, 100])

    # Erwartungen
    if prog and monat:
        st.subheader(f"Erwartungen {monat['name']} (Dozentenfragen)")
        R.h(f"Erwartungen {monat['name']}")
        team = ctx.erw_antworten.get(monat["id"], {})
        zeilen = []
        for f in C.FRAGEN:
            if f["art"] == "fed" and not monat["fed_sitzung"]:
                continue
            if f["art"] == "sektoren":
                sek = prog.get("sektoren_df")
                modell = (f"oben {', '.join(sek.sort_values('p_oben', ascending=False)['sektor'][:3])}" if sek is not None else "–")
                tm = f"oben {', '.join(team.get('12_oben', []))}" if team.get("12_oben") else "–"
                zeilen.append((f["nr"], f["titel"][:55] + "…", modell, "", tm))
                continue
            q = prog["fragen"][f["nr"]]
            zeilen.append((f["nr"], f["titel"][:55] + ("…" if len(f["titel"]) > 55 else ""), q["vorschlag"], f"{q['p_vorschlag']:.0f} %",
                           team.get(f["nr"], "–")))
        tab = pd.DataFrame(zeilen, columns=["Nr.", "Frage", "Modell (aus Szenarien)", "W.", "Team-Antwort"])
        st.dataframe(tab, hide_index=True, width="stretch")
        st.caption("Modell = wahrscheinlichste Antwort aus euren Szenarien; Team-Antwort = gespeichert auf der Seite «Erwartungen».")
        R.tabelle(tab, [10, 70, 50, 14, 36])

    st.divider()
    l, r = st.columns([1.05, 1])
    with l:
        st.subheader("Taktik (Team)")
        R.h("Taktik")
        c1, c2 = st.columns([1, 1.1])
        with c1:
            ui.allokation_donut(a["aktien"], a["neutral"]["aktien"], a["cash"], a["neutral"].get("cash", 0))
        with c2:
            ui.zahl_block("Aktienquote", f"{a['aktien']:.0f} %", f"{ui.de(a['aktien'] - C.BENCHMARK['aktien'], 0, True)} PP vs. Benchmark 50 %")
            ui.zahl_block("Duration Anleihen", f"{ui.de(a['duration'])} J.", f"Benchmark ≈ {ui.de(ctx.bond.get('duration'), 1)} J.")
            ui.zahl_block("TIPS-Anteil an Anleihen", f"{a['tips']:.0f} %")
        if a["korridor"]:
            st.warning("Ausserhalb des IPS-Bands: " + "; ".join(a["korridor"]) + ".")
        ow = a["sektoren"][a["sektoren"]["empfehlung"] == "Übergewichten"]["sektor"].tolist()
        uw = a["sektoren"][a["sektoren"]["empfehlung"] == "Untergewichten"]["sektor"].tolist()
        st.markdown(f"**Übergewichtet:** {', '.join(ow) or '–'}  \n**Untergewichtet:** {', '.join(uw) or '–'}")
        st.caption(f"Quelle: {a.get('quelle_txt', '')}. Eintragen auf der Seite «Taktik».")
        R.text(f"{a['aktien']:.0f} % Aktien / {a['anleihen']:.0f} % Anleihen / {a['cash']:.0f} % Cash · Duration {ui.de(a['duration'])} J. · "
               f"Übergewichtet: {', '.join(ow) or '-'} · Untergewichtet: {', '.join(uw) or '-'} ({a.get('quelle_txt', '')})")
    with r:
        st.subheader(f"Aktive Signale ({len(signale)})")
        R.h(f"Aktive Signale ({len(signale)})")
        if not signale:
            st.success("Keine roten Signale.")
        for s in signale[:8]:
            _signal_box(s)
            R.text(f"{s['titel']}: {s['text']}", klein=True)
        if len(signale) > 8:
            st.caption(f"… und {len(signale) - 8} weitere auf den Kapitelseiten.")

    st.divider()
    st.subheader("Kapitel auf einen Blick")
    R.h("Kapitel auf einen Blick")
    karten = []
    for kap in C.KAPITEL:
        ids = [i["id"] for i in C.INDIKATOREN if i["kapitel"] == kap["id"]]
        zz = _zaehler(ctx.status, ids)
        kern = [i for i in ids if C.IND_BY_ID[i]["kern"]][:3] or ids[:3]
        n_sig = len(_signale(ctx, kap["id"]))
        sig = (f'<span class="pill" style="background:#fff; color:{ui.FARBE[L.ROT]}; border-color:{ui.RAND[L.ROT]}">'
               f'{n_sig} Signal{"e" if n_sig != 1 else ""}</span>' if n_sig else "")
        zeilen = "".join(
            f'<div class="karte-zeile"><span><span class="punkt" style="background:{ui.FARBE.get(ctx.status.loc[i, "ampel"], ui.MUTED)}"></span>'
            f'{html.escape(ctx.status.loc[i, "name"].split(" (")[0])}</span>'
            f'<span>{html.escape(ctx.status.loc[i, "anzeige"])} <span style="color:{ui.TREND_FARBE[ctx.status.loc[i, "trend"]]}">'
            f'{ctx.status.loc[i, "pfeil"]}</span></span></div>' for i in kern)
        _, saetze = kurzfazit(ctx, kap["id"])
        kurz = " ".join(s.replace("**", "") for s in saetze[:2])
        karten.append(f'<div class="karte"><div class="karte-titel">{html.escape(kap["name"])}</div>'
                      f'<div style="margin-bottom:9px">{_zaehler_html(zz)}{sig}</div>{zeilen}'
                      f'<div class="karte-text">{html.escape(kurz[:190])}{"…" if len(kurz) > 190 else ""}</div></div>')
        R.h2(kap["name"])
        R.text(" ".join(s.replace("**", "") for s in saetze), klein=True)
    st.markdown(f'<div class="karten">{"".join(karten)}</div>', unsafe_allow_html=True)

    st.divider()
    l, r = st.columns(2)
    with l:
        st.subheader("Risiken & Chancen")
        ui.risiko_matrix(ctx.risiken, 330)
    with r:
        st.subheader("Konjunkturzyklus")
        ui.zyklus_chart(zt, zm)
        st.subheader("Nächste Termine")
        _termine_tabelle(n=6)
    SP.knopf(ph, R, "gesamtbild")


# ---------------------------------------------------------------------------
# Kapitel
# ---------------------------------------------------------------------------
def seite_kapitel(ctx: Ctx, kap_id: str):
    import dateien
    import seitenpdf as SP

    kap = C.KAPITEL_BY_ID[kap_id]
    ids = _ind_ids(kap_id)
    eigene = [i for i in ids if C.IND_BY_ID[i]["kapitel"] == kap_id]
    fazit, wer = _fazit(ctx, kap_id)
    nr = [k["id"] for k in C.KAPITEL].index(kap_id) + 1
    R = SP.Sammler(kap["name"], f"Kapitel {nr} von {len(C.KAPITEL)} · Makroanalyse USA · {ctx.fred_stand}", kurz=kap["name"])
    ui.seitenkopf(kap["name"], f"{_zaehler_html(_zaehler(ctx.status, eigene))}&nbsp; {len(ids)} Indikatoren · {ctx.fred_stand}",
                  over=f"Kapitel {nr} von {len(C.KAPITEL)} · Makroanalyse USA")
    ph = st.empty()
    _kurzfazit_box(ctx, kap_id, R)
    st.markdown(f"> {html.escape(fazit)}\n\n<span class='klein'>{wer}</span>", unsafe_allow_html=True)
    R.h("Fazit")
    R.text(fazit)
    R.text(wer, klein=True)

    sig = _signale(ctx, kap_id)
    if sig:
        st.markdown("#### Aktive Signale")
        R.h("Aktive Signale")
        for s in sig:
            _signal_box(s)
            R.text(f"{s['titel']}: {s['text']}", klein=True)

    st.markdown("#### Kennzahlen")
    ui.kpi_raster(ctx.status, ids, 4)
    d = ctx.status.loc[ids]
    R.h("Kennzahlen")
    R.tabelle(pd.DataFrame({"Indikator": d["name"], "Wert": d["anzeige"], "Ampel": d["ampel"],
                            "Trend": d["pfeil"] + " " + d["trend"].replace({"neutral": ""}), "Vorwert": d["vor_anzeige"],
                            "Stand": d["datum"].map(ui.datum_txt), "Schwelle": d["schwelle"]}), [52, 22, 16, 18, 20, 18, 34])
    fremd = [i for i in ids if i not in eigene]
    if fremd:
        st.caption("Auch gezeigt (gehört zu einem anderen Kapitel): " + ", ".join(C.IND_BY_ID[i]["name"] for i in fremd))
    for i in [i for i in ids if C.IND_BY_ID[i]["kern"]][:4]:
        df = ctx.daten.reihe(i)
        if len(df) > 2:
            s = df["wert"]
            s = s[s.index >= s.index[-1] - pd.DateOffset(years=3)]
            R.linien([(C.IND_BY_ID[i]["name"][:40], s, (31, 78, 140))], titel=f"{C.IND_BY_ID[i]['name']} (3 Jahre)")

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
            st.markdown("<div class='klein'>" + "<br>".join(f"<span style='color:{ui.FARBE[L.GRUEN] if ok else '#C5CAD3'}'>{'■' if ok else '□'}</span> {html.escape(n)}"
                                                           for n, ok in zm["spaet"] + zm["rezession"]) + "</div>",
                        unsafe_allow_html=True)
            st.markdown("<div class='klein' style='margin-top:8px'>" + "<br>".join(
                f"<b>{html.escape(str(r['person']))}</b>: {ui.de(r['von'], 2)}–{ui.de(r['bis'], 2)} "
                f"({L.phase((r['von'] + r['bis']) / 2)}) {html.escape(str(r['kommentar'] or ''))}"
                for _, r in ctx.zyklus_team.iterrows()) + "</div>", unsafe_allow_html=True)
        with st.expander("Eigene Zyklus-Einordnung abgeben", icon=":material/how_to_vote:"):
            with st.form("zyklus"):
                von_bis = st.slider("Deine Einordnung (0 = Tiefpunkt · 0,25 = Erholung · 0,5 = Hochphase · 0,75 = Abschwung)",
                                    0.0, 1.0, (0.48, 0.58), 0.01)
                st.caption(f"= {L.phase(von_bis[0])} bis {L.phase(von_bis[1])}")
                kom = st.text_input("Begründung (kurz)")
                if st.form_submit_button("Speichern", type="primary", disabled=ctx.nur_lesen):
                    ctx.speichern("zyklus_votes", [dict(zeit=jetzt(), person=ctx.person, von=von_bis[0], bis=von_bis[1],
                                                        kommentar=kom.strip())], "Zyklus-Einordnung gespeichert.")
        R.h("Zyklus")
        R.text(f"Modell: {ctx.zyklus_modell['phase']} · Team: " + "; ".join(
            f"{r['person']} {ui.de(r['von'], 2)}-{ui.de(r['bis'], 2)}" for _, r in ctx.zyklus_team.iterrows()))

    st.markdown("#### Details je Indikator")
    jahre = st.segmented_control("Zeitraum", [2, 5, 10], default=5, format_func=lambda j: f"{j} Jahre",
                                 key=f"jahre_{kap_id}") or 5
    for n_i, i in enumerate(ids):
        ind, r = C.IND_BY_ID[i], ctx.status.loc[i]
        farbe = {"Rot": "red", "Gelb": "orange", "Grün": "green", "Kontext": "gray"}.get(r["ampel"], "gray")
        with st.expander(f":{farbe}[●]&nbsp; {ind['name']} — {r['anzeige']}", expanded=n_i == 0):
            c1, c2 = st.columns([1.7, 1])
            with c1:
                ui.indikator_chart(ind, ctx.daten.reihe(i), ctx.rezession, jahre)
            with c2:
                art = {"fred": f"automatisch (FRED {ind.get('series')}" + (", ergänzt um Live-Kurse von Yahoo Finance)"
                                if ind.get("series") in C.MARKT_ERGAENZT_FRED.values() else ")"),
                       "markt": f"automatisch (Yahoo Finance {ind.get('series')})", "manuell": "Team-Eingabe",
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
                with st.popover("Neuen Wert eintragen", icon=":material/add:"):
                    eingabe_formular(ctx, i, key=f"{kap_id}_{i}")

    # Bezug zu Szenarien und Risiken
    st.markdown("#### Bezug zu Szenarien & Risiken")
    R.h("Bezug zu Szenarien & Risiken")
    zeilen = []
    for sid in ctx.szdef:
        for x in ctx.lage[sid]["zeilen"]:
            if x["art"] == "Indikator" and x["ind"] in ids:
                zeilen.append((ctx.szdef[sid]["kurz"], x["text"], x["anzeige"], "ja" if x["ok"] else "–" if x["ok"] is None else "nein"))
    if zeilen:
        tab = pd.DataFrame(zeilen, columns=["Szenario", "Indiz", "Heute", "Erfüllt"])
        st.dataframe(tab, hide_index=True, width="stretch")
        R.tabelle(tab, [24, 90, 40, 26])
    ris = ctx.risiken[ctx.risiken["kapitel"] == kap_id]
    if len(ris):
        _risiko_tabelle(ris)
        R.tabelle(pd.DataFrame({"Risiko": ris["name"], "Wahrsch.": ris["p"].map(lambda x: f"{x:.0f} %" if not pd.isna(x) else "–"),
                                "Warnsignal": ris["signal"].map(lambda x: "aktiv" if x else "-"), "Reaktion": ris["reaktion"]}))
    if not zeilen and not len(ris):
        st.caption("Keine Szenario-Indizien oder Risiken aus diesem Kapitel.")

    st.markdown("#### Notizen & Fazit")
    _notizen(ctx, kap_id)
    n = ctx.tabellen["notizen"]
    if n is not None and not n.empty:
        dn = n[n["kapitel"] == kap_id].sort_values("zeit", ascending=False).head(6)
        if len(dn):
            R.h("Notizen")
            for _, rr in dn.iterrows():
                R.text(f"{rr['person']} ({str(rr['zeit'])[:10]}): {rr['text']}", klein=True)
    dateien.bereich(ctx, kap_id)
    SP.knopf(ph, R, f"kap_{kap_id}")


def _risiko_tabelle(ris: pd.DataFrame):
    d = ris.copy()
    d["W."] = d["p"].map(lambda x: "–" if pd.isna(x) else f"{x:.0f} %")
    d["Ausw."] = d["impact"].map(lambda x: "–" if pd.isna(x) else ui.de(x, 1))
    d["Signal"] = d["signal"].map(lambda x: "● aktiv" if x else "–")
    out = d[["name", "W.", "Ausw.", "Signal", "p_text", "warnsignal", "reaktion", "basis"]]
    out.columns = ["Risiko", "Wahrsch.", "Auswirkung", "Warnsignal", "Analyse", "Warnsignale", "Reaktion", "Basis"]
    st.dataframe(ui.ampel_stil(out, ["Warnsignal"]), hide_index=True, width="stretch")


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


IC_DATUM = pd.Timestamp("2026-11-16")


def X_BEREICH():  # noqa: N802 – Zeitachse vom IC bis zur Abgabe
    ende = max(pd.Timestamp(C.SIMULATION_ENDE), pd.Timestamp.today().normalize()) + pd.Timedelta(days=3)
    return [IC_DATUM - pd.Timedelta(days=3), ende]


def szenario_verlauf_df(votes: pd.DataFrame, beschluss: pd.DataFrame | None = None) -> pd.DataFrame:
    """Gültige Wahrscheinlichkeit je Szenario an jedem Tag mit neuen Einschätzungen oder Beschlüssen (plus Startwerte)."""
    from . import szenarien as SZ

    defs = SZ.szenario_defs(None)
    start = pd.DataFrame([dict(tag=IC_DATUM, id=s["id"], thema="gesamt", name=s["name"], p=float(s["p"]), n=0) for s in C.SZENARIEN])
    tage = set()
    for d in (votes, beschluss):
        if d is not None and not d.empty:
            tage |= set(pd.to_datetime(d["zeit"].astype(str).str[:10], errors="coerce").dropna())
    teile = [start]
    for tag in sorted(tage):
        grenze = tag.strftime("%Y-%m-%d") + "T99"
        v = votes[votes["zeit"].astype(str) <= grenze] if votes is not None and not votes.empty else None
        b = beschluss[beschluss["zeit"].astype(str) <= grenze] if beschluss is not None and not beschluss.empty else None
        d = SZ.wahrscheinlichkeiten(v, b, defs)
        teile.append(d.assign(tag=pd.Timestamp(tag))[["tag", "id", "thema", "name", "p", "n"]].reset_index(drop=True))
    out = pd.concat(teile, ignore_index=True)
    return out.sort_values("tag").drop_duplicates(["tag", "id"], keep="last")


def zyklus_verlauf_df(votes: pd.DataFrame) -> pd.DataFrame:
    zeilen = [dict(tag=IC_DATUM, von=0.50, bis=0.58, n=0)]
    if votes is not None and not votes.empty:
        v = votes.copy()
        v["tag"] = pd.to_datetime(v["zeit"].astype(str).str[:10], errors="coerce")
        for tag in sorted(v["tag"].dropna().unique()):
            t = L.zyklus_team(v[v["tag"] <= tag].drop(columns=["tag"]))
            zeilen.append(dict(tag=pd.Timestamp(tag), von=t["von"].mean(), bis=t["bis"].mean(), n=len(t)))
    return pd.DataFrame(zeilen)


# ---------------------------------------------------------------------------
# Hilfen für Bewertungen
# ---------------------------------------------------------------------------
def _letzte(df: pd.DataFrame, person: str, key: str, item: str, feld: str):
    if df is None or df.empty:
        return None
    d = df[(df["person"] == person) & (df[key] == item)].sort_values("zeit")
    return zahl(d.iloc[-1][feld]) if len(d) else None


# ---------------------------------------------------------------------------
# Daten eintragen, Termine, Protokoll, Einstellungen
# ---------------------------------------------------------------------------
def seite_eintragen(ctx: Ctx):
    ui.seitenkopf("Daten eintragen", "Neue Werte für manuelle Indikatoren oder Überschreiben eines FRED-Werts.")
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


TERMINE_DF = None  # wird von der App mit dem automatischen Kalender gesetzt
TERMINE_INFO = None


def _termine_tabelle(n: int | None = None):
    heute = pd.Timestamp.today().normalize()
    if TERMINE_DF is not None:
        d = TERMINE_DF.copy()
    else:
        d = pd.DataFrame(C.TERMINE, columns=["sort", "Datum", "Ereignis", "Betrifft"])
        d["sort"] = pd.to_datetime(d["sort"])
    if "Quelle" not in d:
        d["Quelle"] = "Team"
    d["in"] = (d["sort"] - heute).dt.days
    if n:
        d = d[d["in"] >= -1].head(n)
    d["In"] = d["in"].map(lambda x: "heute" if x == 0 else f"in {x} T." if x > 0 else "vorbei")
    st.dataframe(d[["Datum", "In", "Ereignis", "Betrifft"] + ([] if n else ["Quelle"])], hide_index=True, width="stretch",
                 column_config={"Datum": st.column_config.TextColumn(width="medium"),
                                "In": st.column_config.TextColumn(width="small")})


def seite_termine(ctx: Ctx):
    ui.seitenkopf("Termine bis Abgabe", "Datenveröffentlichungen, Fed-Sitzungen und Fristen bis zur Abgabe am 28.02.2027.")
    _termine_tabelle()
    if TERMINE_INFO:
        st.caption(TERMINE_INFO)
    st.caption("Datenveröffentlichungen kommen automatisch aus dem FRED-Kalender (exakte Daten); Fed-Sitzungen, Gipfel, "
               "Gewinnzahlen und Fristen sind eure Einträge aus der Makroanalyse.")


def seite_protokoll(ctx: Ctx):
    ui.seitenkopf("Protokoll", "Alle Team-Eingaben, neuste zuerst. Nichts wird überschrieben – Korrekturen sind einfach neue Einträge.")
    namen = {"werte": "Werte", "szenario_votes": "Szenario-Bewertungen", "risiko_votes": "Risiko-Bewertungen",
             "zyklus_votes": "Zyklus", "notizen": "Notizen", "einstellungen": "Einstellungen", "risiken_neu": "Neue Risiken", "sektorvoten": "Sektorvoten",
             "transaktionen": "Transaktionen", "entscheide": "Entscheide", "sektordaten": "Sektordaten",
             "analysen": "Analysen", "szenario_beschluss": "Szenario-Beschlüsse", "definitionen": "Szenario-Änderungen",
             "erwartungen": "Erwartungen", "ips": "IPS", "taktik": "Taktik", "markt_erwartung": "Markt-Einträge",
             "dateien": "Dokumente"}
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
    ui.seitenkopf("Einstellungen", "Referenzwerte und Datenquellen. Die strategische Allokation (neutraler Anker) kommt aus dem IPS.")
    n = ctx.alloc["neutral"]
    st.markdown(f"**Neutraler Anker (aus dem IPS):** {n['aktien']:.0f} % Aktien / {n.get('anleihen', 100 - n['aktien'] - n.get('cash', 0)):.0f} % "
                f"Anleihen / {n.get('cash', 0):.0f} % Cash – ändern auf der Seite «IPS».")
    with st.form("neutral"):
        du = st.slider("Neutrale Duration (Jahre)", 1.0, 8.0, float(n["duration"]), 0.5,
                       help="Referenz für «vs. neutral»; die Benchmark-Duration kommt aus dem gewählten Treasury-Index unten.")
        ti = st.slider("Neutraler TIPS-Anteil (%)", 0, 50, int(round(n["tips"])), 5)
        namen = list(C.BOND_BENCHMARKS)
        akt = ctx.einst.get("bond_benchmark", C.BOND_BENCHMARK_DEFAULT)
        bb = st.selectbox("Anleihen-Benchmark (Näherung für den Treasury-Index der Dozenten-App)", namen,
                          index=namen.index(akt) if akt in namen else 0)
        if st.form_submit_button("Speichern", type="primary", disabled=ctx.nur_lesen):
            ctx.speichern("einstellungen", [dict(zeit=jetzt(), person=ctx.person, schluessel=k, wert=v)
                                            for k, v in (("duration", du), ("tips", ti), ("bond_benchmark", bb))],
                          "Einstellungen gespeichert.")
    st.divider()
    st.subheader("Datenquellen")
    st.markdown(f"- **Speicher:** {st.session_state.get('store_name', '–')}\n- **FRED:** {ctx.fred_stand}")
    if ctx.fred_fehler:
        st.markdown("Nicht geladen: " + ", ".join(ctx.fred_fehler))
    if st.button("FRED-Daten jetzt neu laden"):
        st.cache_data.clear()
        st.rerun()
