"""Taktik pro Monat – gleiches Format wie die Dozenten-App (Aktien, Anleihen, Cash, 11 Sektor-Zielgewichte in % des
Gesamtvermögens), ergänzt um Duration, TIPS und die Begründung des Teams.

Die App macht keine Empfehlung. Neben jedem Feld steht, was Szenarien, Signale und eure Erwartungen dazu sagen.
"""
from __future__ import annotations

import html
import json
from datetime import datetime

import numpy as np
import pandas as pd
import streamlit as st

import seitenpdf as SP
from makro import config as C
from makro import ui
from storage import jetzt, zahl

KLASSEN = [("aktien", "Aktien (S&P 500)"), ("anleihen", "Anleihen (US-Staatsanleihen)"), ("cash", "Cash")]


# ---------------------------------------------------------------------------
# Monate
# ---------------------------------------------------------------------------
def frist(m: dict) -> pd.Timestamp:
    return pd.Timestamp(m["frist"])


def monat_in_arbeit(jetzt_: pd.Timestamp | None = None) -> dict:
    """Der nächste Monat, dessen Frist noch nicht abgelaufen ist (sonst der letzte)."""
    j = jetzt_ or pd.Timestamp(datetime.now())
    return next((m for m in C.MONATE if frist(m) > j), C.MONATE[-1])


def monat_gueltig(jetzt_: pd.Timestamp | None = None) -> dict | None:
    """Der Monat, dessen Taktik gerade gilt (Frist vorbei)."""
    j = jetzt_ or pd.Timestamp(datetime.now())
    vorbei = [m for m in C.MONATE if frist(m) <= j]
    return vorbei[-1] if vorbei else None


def frist_text(m: dict) -> tuple[str, bool]:
    j = pd.Timestamp(datetime.now())
    f = frist(m)
    if j >= f:
        return f"Frist {f:%d.%m.%Y %H:%M} vorbei – der Monat ist in der Dozenten-App endgültig.", False
    d = f - j
    return f"Frist: {f:%d.%m.%Y}, {f:%H:%M} Uhr Schweizer Zeit (Börseneröffnung) – noch {d.days} Tage {d.seconds // 3600} Std.", True


# ---------------------------------------------------------------------------
# Daten
# ---------------------------------------------------------------------------
def lese(tab: pd.DataFrame | None, monat: str) -> dict | None:
    if tab is None or tab.empty:
        return None
    d = tab[tab["monat"] == monat].sort_values("zeit")
    if not len(d):
        return None
    r = d.iloc[-1]
    try:
        sek = {k: float(v) for k, v in json.loads(r["sektoren"] or "{}").items()}
    except (ValueError, TypeError):
        sek = {}
    return dict(monat=monat, aktien=zahl(r["aktien"]) or 0.0, anleihen=zahl(r["anleihen"]) or 0.0, cash=zahl(r["cash"]) or 0.0,
                sektoren=sek, duration=zahl(r["duration"]), tips=zahl(r["tips"]), laufzeiten=r.get("laufzeiten", ""),
                begruendung=r.get("begruendung", ""), person=r["person"], zeit=str(r["zeit"]), n=len(d))


def sektor_standard(aktien: float) -> dict:
    """Aktiengewicht wie im S&P 500 auf die Sektoren verteilen (2 Stellen, Rundungsrest beim grössten Sektor)."""
    tot = sum(C.SP500_GEWICHT.values())
    out = {s: round(aktien * C.SP500_GEWICHT[s] / tot, 2) for s in C.SEKTOREN}
    gross = max(out, key=out.get)
    out[gross] = round(out[gross] + aktien - sum(out.values()), 2)
    return out


def alloc_dict(t: dict | None, monat: dict, neutral: dict, ips: dict, regeln: list, gew: dict) -> dict:
    """Allokation fürs ganze Dashboard: die Taktik des Teams (oder neutral, solange nichts erfasst ist)."""
    if t:
        aktien, anleihen, cash = t["aktien"], t["anleihen"], t["cash"]
        sek = t["sektoren"] or sektor_standard(aktien)
        duration = t["duration"] if t["duration"] is not None else neutral["duration"]
        tips = t["tips"] if t["tips"] is not None else neutral["tips"]
        quelle, quelle_txt = "Team", f"Taktik {monat['name']} ({t['person']}, {t['zeit'][:10]})"
    else:
        aktien, anleihen, cash = neutral["aktien"], neutral["anleihen"], neutral["cash"]
        sek, duration, tips = sektor_standard(aktien), neutral["duration"], neutral["tips"]
        quelle, quelle_txt = "neutral", "noch keine Taktik – strategische Allokation (IPS)"
    zeilen = []
    for s in C.SEKTOREN:
        w = float(sek.get(s, 0))
        anteil = w / aktien * 100 if aktien else 0
        aktiv = anteil - C.SP500_GEWICHT[s]
        zeilen.append(dict(sektor=s, gewicht=w, anteil_aktien=anteil, sp500=C.SP500_GEWICHT[s], score=aktiv,
                           empfehlung="Übergewichten" if aktiv > 0.25 else "Untergewichten" if aktiv < -0.25 else "Neutral",
                           beitraege=""))
    korridor = []
    for k, name in KLASSEN:
        lo, hi = ips[f"{k}_min"], ips[f"{k}_max"]
        v = {"aktien": aktien, "anleihen": anleihen, "cash": cash}[k]
        if not lo - 1e-9 <= v <= hi + 1e-9:
            korridor.append(f"{name.split(' (')[0]} {v:.0f} % ausserhalb {lo:.0f}–{hi:.0f} %")
    return dict(neutral=dict(neutral), aktien=aktien, anleihen=anleihen, cash=cash, aktien_tilt=aktien - neutral["aktien"],
                duration=duration, tips=tips, sektoren=pd.DataFrame(zeilen).sort_values("score", ascending=False),
                korridor=korridor, regeln=regeln,
                baender={"aktien": (ips["aktien_min"], ips["aktien_max"]), "treasuries": (ips["anleihen_min"], ips["anleihen_max"]),
                         "cash": (ips["cash_min"], ips["cash_max"])}, konflikt=False, duration_szen=gew["alloc"].get("duration"),
                tips_szen=gew["alloc"].get("tips"), duration_ziele=[r["duration"] for r in regeln if r["aktiv"] and r["duration"]],
                quelle=quelle, quelle_txt=quelle_txt, monat=monat["id"], begruendung=(t or {}).get("begruendung", ""))


def pruefe(werte: dict, ips: dict, erw: dict | None = None, bench_dur: float | None = None) -> list[tuple[str, str]]:
    out = []
    s = werte["aktien"] + werte["anleihen"] + werte["cash"]
    out.append(("ok" if abs(s - 100) < 0.01 else "fehler", f"Summe Anlageklassen {ui.de(s, 2)} % (muss 100 % sein)"))
    for k, name in KLASSEN:
        v = werte[k]
        lo, hi = ips[f"{k}_min"], ips[f"{k}_max"]
        dlo, dhi = C.DOZENT_BAND[k]
        if not dlo <= v <= dhi:
            out.append(("fehler", f"{name}: {ui.de(v, 2)} % ausserhalb der Dozenten-Bandbreite {dlo}–{dhi} %"))
        elif not lo <= v <= hi:
            out.append(("fehler", f"{name}: {ui.de(v, 2)} % ausserhalb eures IPS-Bands {ui.de(lo, 0)}–{ui.de(hi, 0)} %"))
    ssum = sum(werte["sektoren"].values())
    out.append(("ok" if abs(ssum - werte["aktien"]) < 0.015 else "fehler",
                f"Summe Sektoren {ui.de(ssum, 2)} % (muss dem Aktiengewicht {ui.de(werte['aktien'], 2)} % entsprechen)"))
    if werte["aktien"] > 0:
        for sek, w in werte["sektoren"].items():
            abw = w / werte["aktien"] * 100 - C.SP500_GEWICHT[sek]
            if abs(abw) > ips["sektor_abw_max"] + 1e-9:
                out.append(("fehler", f"{sek}: {ui.de(abw, 2, True)} PP gegenüber dem S&P 500 (IPS erlaubt ±{ui.de(ips['sektor_abw_max'], 0)} PP)"))
    for sek in ips.get("aus_sektoren", []):
        if werte["sektoren"].get(sek, 0) > 0:
            out.append(("fehler", f"{sek} ist im IPS ausgeschlossen, hat aber {ui.de(werte['sektoren'][sek], 2)} %"))
    if werte.get("duration") is not None and not ips["duration_min"] <= werte["duration"] <= ips["duration_max"]:
        out.append(("fehler", f"Duration {ui.de(werte['duration'], 1)} J. ausserhalb des IPS-Bands "
                              f"{ui.de(ips['duration_min'], 0)}–{ui.de(ips['duration_max'], 0)} J."))
    if len(str(werte.get("begruendung", "")).strip()) < 30:
        out.append(("warnung", "Begründung fehlt oder ist kürzer als 30 Zeichen"))
    # Abgleich mit den eigenen Erwartungen
    if erw:
        a2 = erw.get(2)
        if a2 and a2.startswith("Aktien besser") and werte["aktien"] < C.BENCHMARK["aktien"]:
            out.append(("warnung", "Frage 2 erwartet «Aktien besser», die Taktik hat aber weniger Aktien als die Benchmark (50 %)"))
        if a2 and a2.startswith("Anleihen besser") and werte["aktien"] > C.BENCHMARK["aktien"]:
            out.append(("warnung", "Frage 2 erwartet «Anleihen besser», die Taktik hat aber mehr Aktien als die Benchmark (50 %)"))
        a10 = erw.get(10)
        e_abw = werte["sektoren"].get("Energie", 0) / werte["aktien"] * 100 - C.SP500_GEWICHT["Energie"] if werte["aktien"] else 0
        if a10 and a10.startswith("steigt") and e_abw < -0.25:
            out.append(("warnung", "Frage 10 erwartet steigenden Ölpreis, Energie ist aber untergewichtet"))
        if a10 and a10.startswith("fällt") and e_abw > 0.25:
            out.append(("warnung", "Frage 10 erwartet fallenden Ölpreis, Energie ist aber übergewichtet"))
        oben, unten = erw.get("12_oben") or [], erw.get("12_unten") or []
        for sek in oben:
            if werte["aktien"] and werte["sektoren"].get(sek, 0) / werte["aktien"] * 100 < C.SP500_GEWICHT[sek] - 0.25:
                out.append(("warnung", f"Frage 12 sieht {sek} oben, in der Taktik ist er untergewichtet"))
        for sek in unten:
            if werte["aktien"] and werte["sektoren"].get(sek, 0) / werte["aktien"] * 100 > C.SP500_GEWICHT[sek] + 0.25:
                out.append(("warnung", f"Frage 12 sieht {sek} unten, in der Taktik ist er übergewichtet"))
        a5 = erw.get(5)
        if a5 and bench_dur and werte.get("duration"):
            if a5.startswith("steigt") and werte["duration"] > bench_dur:
                out.append(("warnung", "Frage 5 erwartet steigende 10-J.-Renditen, die Duration ist aber länger als die Benchmark"))
            if a5.startswith("fällt") and werte["duration"] < bench_dur:
                out.append(("warnung", "Frage 5 erwartet fallende 10-J.-Renditen, die Duration ist aber kürzer als die Benchmark"))
    return out


# ---------------------------------------------------------------------------
# Seite
# ---------------------------------------------------------------------------
def _setze(prefix: str, werte: dict):
    for k, _ in KLASSEN:
        st.session_state[f"{prefix}{k}"] = float(werte[k])
    for s in C.SEKTOREN:
        st.session_state[f"{prefix}s_{s}"] = float(werte["sektoren"].get(s, 0))


def _sektoren_sp(prefix: str):
    akt = st.session_state.get(f"{prefix}aktien", 50.0)
    for s, v in sektor_standard(akt).items():
        st.session_state[f"{prefix}s_{s}"] = v


def seite_taktik(ctx, bond: dict):
    ips, gew, defs, szen = ctx.ips, ctx.gew, ctx.szdef, ctx.szen
    R = SP.Sammler("Taktik", "Monatliche Taktik: Aktien, Anleihen, Cash und Sektor-Zielgewichte (Format Dozenten-App)", kurz="Taktik")
    ui.seitenkopf("Taktik (Allokation pro Monat)",
                  "Gleiches Format wie die Dozenten-App. Ihr tragt die Zielgewichte selbst ein; die App prüft gegen IPS und Dozenten-"
                  "Bandbreiten und zeigt daneben, was Szenarien, Signale und eure Erwartungen sagen.")
    ph = st.empty()
    ids = [m["id"] for m in C.MONATE]
    m_id = st.segmented_control("Monat", ids, default=monat_in_arbeit()["id"], format_func=lambda i: C.MONAT_BY_ID[i]["name"],
                                key="taktik_monat") or monat_in_arbeit()["id"]
    m = C.MONAT_BY_ID[m_id]
    t = lese(ctx.tabellen.get("taktik"), m_id)
    txt, offen = frist_text(m)
    (st.info if offen else st.warning)(txt, icon=":material/schedule:")
    if t:
        st.caption(f"Zuletzt gespeichert von {t['person']} am {t['zeit'][:16].replace('T', ' ')} ({t['n']} Version(en)).")
    else:
        st.caption("Noch nichts erfasst. In der Dozenten-App gilt dann die Strategie des Dozenten (50 % Aktien, 50 % Anleihen, "
                   "Sektoren wie im S&P 500).")
    R.text(f"{m['name']} · {txt}")

    prefix = f"tk_{m_id}_"
    ui.stand_abgleich(f"taktik_{m_id}", t["zeit"] if t else "", (prefix,), t["person"] if t else "")
    if f"{prefix}aktien" not in st.session_state:
        start = t or dict(aktien=ips["saa_aktien"], anleihen=ips["saa_anleihen"], cash=ips["saa_cash"],
                          sektoren=sektor_standard(ips["saa_aktien"]))
        _setze(prefix, start)

    c = st.columns(4)
    c[0].button("Strategie 50/50 einsetzen", on_click=_setze, args=(prefix, dict(aktien=50.0, anleihen=50.0, cash=0.0,
                                                                                    sektoren=sektor_standard(50.0))),
                width="stretch")
    c[1].button("IPS-Strategie einsetzen", on_click=_setze, args=(prefix, dict(aktien=ips["saa_aktien"], anleihen=ips["saa_anleihen"],
                                                                                 cash=ips["saa_cash"], sektoren=sektor_standard(ips["saa_aktien"]))),
                width="stretch")
    c[2].button("Sektoren wie im S&P 500 verteilen", on_click=_sektoren_sp, args=(prefix,), width="stretch")
    if t:
        c[3].button("Gespeicherte Taktik laden", on_click=_setze, args=(prefix, t), width="stretch")

    # Orientierung
    p_txt = " · ".join(f"{defs[s]['kurz']} {szen.loc[s, 'p']:.0f} %" for s in defs)
    o_aktien = ctx.alloc["neutral"]["aktien"] + gew["alloc"]["aktien_tilt"]
    aktive = [r for r in ctx.alloc["regeln"] if r["aktiv"]]
    sig_aktien = sum(r["aktien"] for r in aktive)
    erw = ctx.erw_antworten.get(m_id, {})

    st.markdown("#### Anlageklassen")
    kopf = st.columns([1.6, 1.1, 1.1, 0.8, 2.6])
    for col, txt_ in zip(kopf, ["Anlageklasse", "Band (IPS · Dozent)", "Zielgewicht (% des Vermögens)", "Prüfung", "Orientierung"]):
        col.markdown(f"<span class='klein'><b>{txt_}</b></span>", unsafe_allow_html=True)
    orient = {
        "aktien": f"Szenarien ({p_txt}): {ui.de(o_aktien, 0)} % · Signale: {ui.de(sig_aktien, 0, True)} PP"
                  + (f" · Frage 1: {erw.get(1)}" if erw.get(1) else "") + (f" · Frage 2: {erw.get(2)}" if erw.get(2) else ""),
        "anleihen": f"Benchmark 50 % · Szenarien: {ui.de(100 - o_aktien - ips['saa_cash'], 0)} %"
                    + (f" · Frage 5: {erw.get(5)}" if erw.get(5) else ""),
        "cash": f"IPS {ui.de(ips['cash_min'], 0)}–{ui.de(ips['cash_max'], 0)} % · Cash-Zins ≈ {ui.de(bond.get('cash', 0), 2)} % p.a.",
    }
    werte = {}
    pdf_kl = []
    for k, name in KLASSEN:
        cols = st.columns([1.6, 1.1, 1.1, 0.8, 2.6])
        lo, hi = ips[f"{k}_min"], ips[f"{k}_max"]
        dlo, dhi = C.DOZENT_BAND[k]
        cols[0].markdown(f"**{name}**")
        cols[1].markdown(f"{ui.de(lo, 0)}–{ui.de(hi, 0)} % · {dlo}–{dhi} %")
        werte[k] = cols[2].number_input(name, 0.0, 100.0, step=0.5, format="%.2f", key=f"{prefix}{k}", label_visibility="collapsed")
        ok = lo <= werte[k] <= hi and dlo <= werte[k] <= dhi
        cols[3].markdown(f"<span style='color:{ui.FARBE['Grün'] if ok else ui.FARBE['Rot']}; font-weight:600'>"
                         f"{'im Band' if ok else 'ausserhalb'}</span>", unsafe_allow_html=True)
        cols[4].markdown(f"<span class='klein'>{html.escape(orient[k])}</span>", unsafe_allow_html=True)
        pdf_kl.append((name, f"{ui.de(lo, 0)}–{ui.de(hi, 0)} %", f"{ui.de(werte[k], 2)} %", "im Band" if ok else "AUSSERHALB",
                       orient[k]))
    s = sum(werte.values())
    st.markdown(f"Summe: <span style='color:{ui.FARBE['Grün'] if abs(s - 100) < 0.01 else ui.FARBE['Rot']}; font-weight:700'>"
                f"{ui.de(s, 2)} %</span> (muss 100 % sein)", unsafe_allow_html=True)

    st.markdown("#### Sektoren")
    rang = gew["sektoren"]
    sim_sek = ctx.erw_sim_sektoren.get(m_id)
    kopf = st.columns([1.7, 0.8, 1.1, 0.9, 0.9, 2.4])
    for col, txt_ in zip(kopf, ["Sektor", "Gewicht S&P 500 (% der Aktien, Seite Sektorallokation)", "Zielgewicht (% des Vermögens)", "% der Aktien",
                                "aktiv vs. S&P", "Orientierung"]):
        col.markdown(f"<span class='klein'><b>{txt_}</b></span>", unsafe_allow_html=True)
    sektoren = {}
    pdf_sek = []
    regel_sek = {sek: sum(r["sektoren"].get(sek, 0) for r in aktive) for sek in C.SEKTOREN}
    import sektoren as SK
    sa = SK.lade_allokation(ctx.tabellen.get("sektor_allokation"))["sektoren"]
    sa_ziel = SK.ziele(sa, C.SP500_GEWICHT)
    for sek in C.SEKTOREN:
        cols = st.columns([1.7, 0.8, 1.1, 0.9, 0.9, 2.4])
        cols[0].markdown(f"{C.SEKTOR_DOZENT[sek]} ({C.GICS_CODE[sek]})")
        cols[1].markdown(ui.de(C.SP500_GEWICHT[sek], 2))
        sektoren[sek] = cols[2].number_input(sek, 0.0, 100.0, step=0.05, format="%.2f", key=f"{prefix}s_{sek}",
                                             label_visibility="collapsed")
        anteil = sektoren[sek] / werte["aktien"] * 100 if werte["aktien"] else 0
        aktiv = anteil - C.SP500_GEWICHT[sek]
        zu_viel = abs(aktiv) > ips["sektor_abw_max"]
        cols[3].markdown(ui.de(anteil, 2))
        cols[4].markdown(f"<span style='color:{ui.FARBE['Rot'] if zu_viel else ui.INK if abs(aktiv) < 0.25 else ui.BLAU}; "
                         f"font-weight:600'>{ui.de(aktiv, 2, True)} PP</span>", unsafe_allow_html=True)
        e_sa = sa[sek]
        teile = [f"Sektorallokation: {SK.RICHTUNG_KURZ[e_sa['richtung']]}"
                 + (f" {ui.de(e_sa['abweichung'], 1, True)} PP → {ui.de(sa_ziel[sek] * werte['aktien'] / 100, 2)} %"
                    if sa_ziel[sek] is not None and SK._z(e_sa.get('abweichung')) is not None else ""),
                 f"Szenarien {ui.de(rang[sek], 1, True)} PP/Mt."]
        if regel_sek[sek]:
            teile.append(f"Signale {ui.de(regel_sek[sek], 0, True)}")
        if sim_sek is not None and sek in sim_sek.index:
            teile.append(f"P(oben) {sim_sek.loc[sek, 'p_oben']:.0f} % · P(unten) {sim_sek.loc[sek, 'p_unten']:.0f} %")
        if sek in (erw.get("12_oben") or []):
            teile.append("Frage 12: oben")
        if sek in (erw.get("12_unten") or []):
            teile.append("Frage 12: unten")
        o = " · ".join(teile)
        cols[5].markdown(f"<span class='klein'>{html.escape(o)}</span>", unsafe_allow_html=True)
        pdf_sek.append((f"{C.SEKTOR_DOZENT[sek]} ({C.GICS_CODE[sek]})", ui.de(C.SP500_GEWICHT[sek], 2), ui.de(sektoren[sek], 2),
                        ui.de(anteil, 2), f"{ui.de(aktiv, 2, True)} PP", o))
    ssum = sum(sektoren.values())
    st.markdown(f"Summe: <span style='color:{ui.FARBE['Grün'] if abs(ssum - werte['aktien']) < 0.015 else ui.FARBE['Rot']}; "
                f"font-weight:700'>{ui.de(ssum, 2)} %</span> (muss dem Aktiengewicht {ui.de(werte['aktien'], 2)} % entsprechen)",
                unsafe_allow_html=True)

    st.markdown("#### Anleihen-Steuerung und Begründung")
    c1, c2, c3 = st.columns(3)
    dur = c1.number_input("Duration der Anleihen (J.)", 0.0, 20.0, value=float(t["duration"]) if t and t["duration"] is not None
                          else float(round(gew["alloc"]["duration"], 1)), step=0.1, key=f"{prefix}dur",
                          help=f"IPS {ui.de(ips['duration_min'], 0)}–{ui.de(ips['duration_max'], 0)} J. · Benchmark ≈ "
                               f"{ui.de(bond['duration'], 1)} J. · Szenarien gewichtet {ui.de(gew['alloc']['duration'], 1)} J.")
    tips = c2.number_input("TIPS-Anteil an den Anleihen (%)", 0.0, 100.0, value=float(t["tips"]) if t and t["tips"] is not None
                           else float(round(gew["alloc"]["tips"])), step=5.0, key=f"{prefix}tips",
                           help=f"Szenarien gewichtet {ui.de(gew['alloc']['tips'], 0)} %")
    lz = c3.text_input("Laufzeiten-Schwerpunkt", value=(t or {}).get("laufzeiten", "") or "1–7 J., nichts > 10 J.", key=f"{prefix}lz")
    st.caption(f"Orientierung: Benchmark-Duration ≈ {ui.de(bond['duration'], 1)} J. · Szenarien {ui.de(gew['alloc']['duration'], 1)} J. · "
               f"aktive Signale: {', '.join(ui.de(x, 1) + ' J.' for x in ctx.alloc['duration_ziele']) or 'keine'} · Details auf «Anleihen & Duration».")
    begr = st.text_area("Begründung der Taktik (für Entscheidungsprotokoll und Dozent, mindestens 30 Zeichen)",
                        value=(t or {}).get("begruendung", ""), height=120, key=f"{prefix}begr")

    werte_alle = dict(werte, sektoren=sektoren, duration=dur, tips=tips, begruendung=begr)
    pr = pruefe(werte_alle, ips, erw, bond.get("duration"))

    import risiko
    st.markdown("#### Risiko dieser Taktik gegenüber der Benchmark")
    r_risk = risiko.berechne(ctx, dict(aktien=werte["aktien"], anleihen=werte["anleihen"], cash=werte["cash"],
                                       sektoren=sektoren, duration=dur)) if werte["aktien"] > 0 else None
    if r_risk:
        risiko.abschnitt(ctx, r_risk, dict(werte, sektoren=sektoren, duration=dur), R, kurz=True)
        kz = risiko.kennzahlen_aktienteil(dict(werte, sektoren=sektoren), risiko.sektor_betas(ctx.hist))
        for a_, b_, c_ in risiko.pruefungen(r_risk, ips, kz):
            if a_ != "ok":
                pr.append((a_, f"{b_}: {c_}"))
        st.caption("Rechnet live mit den Werten oben. Stresstests, Reverse-Stresstest und Verteilung auf der Seite «Risiko & Risikobudget».")
    fehler = [x for x in pr if x[0] == "fehler"]
    hinweise = [x for x in pr if x[0] == "warnung"]
    st.markdown("#### Prüfung")
    if not fehler:
        st.success("Alle Pflichtprüfungen erfüllt (Summen, Bänder, Sektorabweichung, Duration).")
    for _, x in fehler:
        st.error(x)
    for _, x in hinweise:
        st.warning(x)

    c1, c2 = st.columns([1, 3])
    if c1.button("Taktik speichern", type="primary", disabled=ctx.nur_lesen, icon=":material/save:"):
        ctx.speichern("taktik", [dict(zeit=jetzt(), person=ctx.person, monat=m_id, aktien=werte["aktien"],
                                      anleihen=werte["anleihen"], cash=werte["cash"],
                                      sektoren=json.dumps({k: round(v, 4) for k, v in sektoren.items()}),
                                      duration=dur, tips=tips, laufzeiten=lz, begruendung=begr.strip())],
                      f"Taktik {m['name']} gespeichert.")
    c2.caption("Speichern ist auch mit offenen Prüfungen möglich (Entwurf). In die Dozenten-App nur übertragen, wenn keine roten "
               "Meldungen mehr da sind. Jede Version bleibt in der Google-Tabelle (Blatt «taktik»).")

    with st.expander("Werte zum Übertragen in die Dozenten-App", icon=":material/content_copy:"):
        st.code("\n".join([f"{n}: {ui.de(werte[k], 2)}" for k, n in KLASSEN] +
                          [f"{C.SEKTOR_DOZENT[s]} ({C.GICS_CODE[s]}): {ui.de(sektoren[s], 2)}" for s in C.SEKTOREN]), language=None)

    with st.expander("Was die Signale sagen (Orientierung)", icon=":material/info:"):
        if aktive:
            st.dataframe(pd.DataFrame([dict(Signal=r["name"], Auslöser=r["grund"],
                                            Aktien=f"{ui.de(r['aktien'], 0, True)} PP" if r["aktien"] else "",
                                            Duration=f"{ui.de(r['duration'], 1)} J." if r["duration"] else "",
                                            Sektoren=", ".join(f"{k} {v:+g}" for k, v in r["sektoren"].items())) for r in aktive]),
                         hide_index=True, width="stretch")
        else:
            st.caption("Keine aktiven Signale.")

    # Verlauf
    tab = ctx.tabellen.get("taktik")
    if tab is not None and not tab.empty:
        v = tab.sort_values("zeit", ascending=False)
        st.markdown("#### Verlauf")
        st.dataframe(pd.DataFrame({"Monat": v["monat"].map(lambda i: C.MONAT_BY_ID.get(i, {}).get("name", i)),
                                   "Gespeichert": v["zeit"].str[:16].str.replace("T", " "), "Person": v["person"],
                                   "Aktien / Anl. / Cash": [f"{ui.de(zahl(a), 1)} / {ui.de(zahl(b), 1)} / {ui.de(zahl(c), 1)}"
                                                           for a, b, c in zip(v["aktien"], v["anleihen"], v["cash"])],
                                   "Duration": v["duration"].map(lambda x: ui.de(zahl(x), 1)),
                                   "Begründung": v["begruendung"].str[:120]}), hide_index=True, width="stretch")

    R.h("Anlageklassen")
    R.tabelle(pd.DataFrame(pdf_kl, columns=["Anlageklasse", "Band IPS", "Zielgewicht", "Prüfung", "Orientierung"]), [36, 20, 20, 18, 86])
    R.h("Sektoren (Zielgewicht in % des Gesamtvermögens)")
    R.tabelle(pd.DataFrame(pdf_sek, columns=["Sektor", "S&P 500", "Zielgewicht", "% der Aktien", "aktiv", "Orientierung"]),
              [42, 16, 18, 18, 18, 68])
    R.h("Anleihen und Begründung")
    R.text(f"Duration {ui.de(dur, 1)} J. · TIPS {ui.de(tips, 0)} % · Laufzeiten: {lz}")
    R.text(begr or "(keine Begründung)")
    R.h("Prüfung")
    R.tabelle(pd.DataFrame([("✗" if a == "fehler" else "!" if a == "warnung" else "✓", b) for a, b in pr], columns=["", "Ergebnis"]),
              [8, 172])
    SP.knopf(ph, R, f"taktik_{m_id}", f"Taktik_{m_id}.pdf")
