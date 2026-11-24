"""Seite «Monitoring»: offene Fälle bearbeiten, Verlauf/Protokoll, Kauf-Thesen und Watchlist, Zuständigkeiten und E-Mails,
Schwellen, Probelauf. Die Logik steckt in monitoring.py (gleich wie im automatischen Lauf)."""
from __future__ import annotations

import html
import json
import time
import uuid

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import monitoring as M
from makro import config as C
from makro import ui
from storage import jetzt

LAGE_SEKUNDEN = 600


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def _quartalszahlen(ticker: tuple[str, ...]) -> dict:
    return M.quartalszahlen_yahoo(ticker)


def _r_aus_ctx(ctx) -> dict:
    return dict(status=ctx.status, kombis=ctx.kombis, risiken=ctx.risiken, alloc=ctx.alloc, szen=ctx.szen, szdef=ctx.szdef,
                gew=ctx.gew, ips=ctx.ips, einst=ctx.einst, bond=ctx.bond, sig=ctx.sig, sek_index=ctx.sek_index)


def _lage(ctx, lade_kurse) -> dict:
    """Aktuelle Lage (Kurse, Portfolio, Schätzungen) – 10 Minuten pro Sitzung zwischengespeichert."""
    import portfolio as P

    ss = st.session_state
    ohne = ("faelle", "monitor_log", "kontakte", "zustaendigkeit")   # Fälle und Kontakte ändern die Lage nicht
    stempel = sum(len(df) for k, df in ctx.tabellen.items() if k not in ohne)
    alt = ss.get("mon_lage")
    if alt and time.time() - alt[0] < LAGE_SEKUNDEN and alt[1] == stempel:
        return alt[2]
    with st.spinner("Prüfe Signale (Kurse, Portfolio, IPS, Taktik) …"):
        kurse_t, fehlt, _ = lade_kurse(M.ticker_benoetigt(ctx.tabellen))
        kurse = {**(ctx.kurse or {}), **kurse_t}
        tx = P.transaktionen(ctx.tabellen.get("transaktionen"))
        aktien = tuple(sorted(set(tx.loc[tx["klasse"] == "Aktie", "ticker"].str.upper()))) if len(tx) else ()
        try:
            import sektor_news_seite as SNS

            analysten = SNS.lade("unternehmen.json")[0] or {}
        except Exception:  # noqa: BLE001
            analysten = {}
        lage = M.lage_bauen(ctx.tabellen, ctx.fred, kurse, ctx.hist, _r_aus_ctx(ctx), sp500=P.sp500_liste(),
                            earnings=_quartalszahlen(aktien) if aktien else {}, perf_fn=P.performance_daten, analysten=analysten)
        if fehlt:
            lage["fehler"].append("Keine Kurse für: " + ", ".join(fehlt))
    ss["mon_lage"] = (time.time(), stempel, lage)
    return lage


def _stufe_badge(stufe: str) -> str:
    return (f"<span style='color:{M.STUFE_FARBE.get(stufe, '#556070')};font-weight:700;border:1px solid "
            f"{M.STUFE_FARBE.get(stufe, '#556070')};border-radius:6px;padding:1px 7px;font-size:12px'>{html.escape(stufe)}</span>")


def _datum(z) -> str:
    try:
        return pd.Timestamp(str(z)[:19]).tz_localize("UTC").tz_convert("Europe/Zurich").strftime("%d.%m.%Y %H:%M")
    except Exception:  # noqa: BLE001
        return str(z)[:16]


def seite_monitoring(ctx, team: list[str], lade_kurse):
    ui.seitenkopf("Monitoring & Exception Report",
                  "Welche Titel wir uns ansehen müssen und warum, wo wir ausserhalb von IPS, Taktik oder Sektorallokation liegen – "
                  "und wie wir darauf reagiert haben. Zuständig sind für Sektoren die Haupt- und Backup-Person, für alles andere "
                  "das ganze Team. Die Mails kommen automatisch (sofort bei «Handeln», täglich ca. 7:00, Wochenreport Samstag).")
    tab = ctx.tabellen
    s = M.schwellen(tab.get("einstellungen"))
    kont, zust = M.kontakte(tab.get("kontakte")), M.zustaendigkeit(tab.get("zustaendigkeit"))
    faelle = M.lade_faelle(tab.get("faelle"))
    lage = _lage(ctx, lade_kurse)
    signale = M.alle_signale(lage, s, M.bekannt_aus(faelle))
    zeilen, ereig = M.abgleich(signale, faelle, lage["snapshot"], wer=ctx.person)

    offen = faelle[faelle["status"].isin(M.OFFEN)] if len(faelle) else faelle
    meine = [f for f in offen.to_dict("records") if ctx.person in M.empfaenger(f, kont, zust)] if len(offen) else []
    ui.kopf_kpis([
        ("Offen · Handeln", str(int((offen["stufe"] == "Handeln").sum())) if len(offen) else "0", "sofort reagieren"),
        ("Offen · Prüfen", str(int((offen["stufe"] == "Prüfen").sum())) if len(offen) else "0", "im Team besprechen"),
        ("Offen · Info", str(int((offen["stufe"] == "Info").sum())) if len(offen) else "0", "zur Kenntnis"),
        ("Meine Bereiche", str(len(meine)), f"offene Fälle für {html.escape(ctx.person)}"),
    ])
    neu_n = sum(1 for e in ereig if e[0] in ("neu", "wieder", "eskaliert"))
    weg_n = sum(1 for e in ereig if e[0] == "erloschen")
    c1, c2, c3 = st.columns([2.6, 1, 1])
    if neu_n or weg_n:
        c1.info(f"Seit dem letzten automatischen Lauf: {neu_n} neue bzw. schärfere Signale, {weg_n} verschwunden. "
                "Der nächste Lauf übernimmt sie automatisch – oder jetzt übernehmen.", icon=":material/sync:")
        if c2.button("Jetzt übernehmen", icon=":material/sync:", width="stretch", disabled=ctx.nur_lesen):
            ctx.speichern("faelle", zeilen, f"{neu_n} Fälle angelegt bzw. aktualisiert, {weg_n} erloschen.")
    else:
        c1.caption(f"Fälle sind auf dem neusten Stand ({len(signale)} aktive Signale).")
    if c3.button("Neu berechnen", icon=":material/refresh:", width="stretch"):
        st.session_state.pop("mon_lage", None)
        st.rerun()
    if lage["fehler"]:
        st.caption("Hinweise zur Datenlage: " + " · ".join(lage["fehler"]))
    if not kont:
        st.warning("Noch keine E-Mail-Adressen hinterlegt – unter «Zuständigkeit & E-Mails» erfassen, sonst gehen keine Meldungen raus.")

    t1, t2, t3, t4, t5 = st.tabs(["Offene Fälle", "Verlauf & Protokoll", "Kauf-Thesen & Watchlist", "Zuständigkeit & E-Mails",
                                  "Schwellen & Probelauf"])
    with t1:
        _offene(ctx, faelle, kont, zust, s)
    with t2:
        _verlauf(ctx, faelle, zust)
    with t3:
        _thesen_watchlist(ctx, lage)
    with t4:
        if ctx.nur_lesen:
            st.caption("Nur für Teammitglieder.")
        else:
            _zustaendigkeit(ctx, team, kont, zust)
    with t5:
        _schwellen(ctx, s)
        st.divider()
        _probelauf(ctx, s, kont, zust, lade_kurse, lage)


# ---------------------------------------------------------------------------
# Offene Fälle
# ---------------------------------------------------------------------------
def _offene(ctx, faelle, kont, zust, s):
    if not len(faelle) or not faelle["status"].isin(M.OFFEN).any():
        st.success("Keine offenen Fälle.", icon=":material/check_circle:")
        return
    c1, c2, c3 = st.columns([1.2, 1.6, 1.6])
    nur_meine = c1.toggle("Nur meine Bereiche", value=False)
    stufen = c2.multiselect("Stufe", M.STUFEN, default=M.STUFEN)
    ebenen = c3.multiselect("Ebene", M.EBENEN, default=M.EBENEN)
    d = faelle[faelle["status"].isin(M.OFFEN) & faelle["stufe"].isin(stufen) & faelle["ebene"].isin(ebenen)].copy()
    d["_r"] = d["stufe"].map(M.RANG)
    d = d.sort_values(["_r", "erstmals"], ascending=[False, True])
    entsch = _entscheide(ctx)
    n = 0
    for f in d.to_dict("records"):
        if nur_meine and ctx.person not in M.empfaenger(f, kont, zust):
            continue
        n += 1
        with st.container(border=True):
            st.markdown(f"{_stufe_badge(f['stufe'])} &nbsp;<b>{html.escape(f['titel'])}</b> &nbsp;"
                        f"<span class='klein'>{html.escape(f['ebene'])} · {html.escape(f['bereich'])} · {html.escape(f['status'])}"
                        f"{' (' + html.escape(f['bearbeiter']) + ')' if f.get('bearbeiter') else ''} · seit {_datum(f['erstmals'])}</span>",
                        unsafe_allow_html=True)
            st.markdown(f"{html.escape(f['text'])}<br><span class='klein'><b>Was prüfen:</b> {html.escape(f.get('hinweis') or '')} · "
                        f"<b>Zuständig:</b> {html.escape(M.zustaendig_text(f, zust))}</span>", unsafe_allow_html=True)
            with st.expander("Bearbeiten und Reaktion festhalten", icon=":material/edit_note:"):
                _snapshot(f)
                if f["status"] == "Neu" and st.button("Übernehmen (in Prüfung)", key=f"ueb_{f['id']}", disabled=ctx.nur_lesen):
                    ctx.speichern("faelle", [M.reaktion_zeile(f, ctx.person, "In Prüfung")], "Fall übernommen.")
                with st.form(f"fall_{f['id']}"):
                    reaktion = st.radio("Reaktion", M.REAKTIONEN, horizontal=True, key=f"re_{f['id']}")
                    begr = st.text_area("Begründung (Pflicht bei «bewusst nicht angepasst» und «Fehlalarm»)", height=80,
                                        key=f"be_{f['id']}")
                    c1, c2 = st.columns(2)
                    eid = c1.selectbox("Verknüpfter Entscheid (bei «angepasst»)", [""] + list(entsch),
                                       format_func=lambda i: entsch.get(i, "– keiner –"), key=f"en_{f['id']}")
                    ns_txt = ("Erneut melden, wenn der Wert " + ("≤" if f.get("richtung") == "unten" else "≥") +
                              f" … ist (aktuell {f.get('wert') or '–'} {f.get('einheit') or ''})")
                    ns = c2.text_input(ns_txt, key=f"ns_{f['id']}", disabled=not str(f.get("wert") or "").strip(),
                                       help="Nur bei «bewusst nicht angepasst»: Bis zu dieser Schwelle meldet die App das Signal "
                                            "nicht erneut. Leer = erst wieder, wenn das Signal verschwindet und zurückkommt "
                                            "oder schärfer wird.")
                    if st.form_submit_button("Fall abschliessen", type="primary"):
                        if reaktion != "Angepasst" and len(begr.strip()) < 10:
                            st.error("Bitte begründen (mindestens ein Satz).")
                        elif reaktion == "Angepasst" and not eid and len(begr.strip()) < 10:
                            st.error("Bitte einen Entscheid verknüpfen oder kurz beschreiben, was angepasst wurde.")
                        elif ns.strip() and M._z(ns) is None:
                            st.error("Die neue Schwelle muss eine Zahl sein.")
                        else:
                            ctx.speichern("faelle", [M.reaktion_zeile(f, ctx.person, "Erledigt", reaktion, begr.strip(),
                                                                      M._z(ns) if reaktion == "Bewusst nicht angepasst" else None,
                                                                      eid)], f"Fall abgeschlossen ({reaktion}).")
                st.caption("Wird etwas umgesetzt, den Entscheid zuerst auf der Seite «Entscheide» erfassen (mit Begründung und "
                           "Quellen) und hier verknüpfen.")
    if not n:
        st.caption("Keine Fälle für diese Auswahl.")


def _snapshot(f: dict):
    try:
        sn = json.loads(f.get("snapshot") or "{}")
    except ValueError:
        sn = {}
    teile = []
    if sn.get("taktik"):
        teile.append(f"Taktik {sn['taktik']}")
    if sn.get("ist"):
        teile.append(f"Ist {sn['ist']} (Aktien / Treasuries / Cash)")
    if sn.get("rote_indikatoren") is not None:
        teile.append(f"{sn['rote_indikatoren']} rote Indikatoren")
    if sn.get("szenarien_ic"):
        teile.append("IC " + " / ".join(f"{k} {v} %" for k, v in sn["szenarien_ic"].items()))
    if teile:
        st.caption("Datenlage beim ersten Auftreten: " + " · ".join(teile))


def _entscheide(ctx) -> dict:
    import entscheide

    d = entscheide.liste(ctx.tabellen.get("entscheide"))
    return {r["id"]: f"{r['datum']} · {r['titel']} ({r['status']})" for _, r in d.iterrows()}


# ---------------------------------------------------------------------------
# Verlauf und Protokoll
# ---------------------------------------------------------------------------
def _verlauf(ctx, faelle, zust):
    import seitenpdf as SP

    ph = st.empty()
    if not len(faelle):
        st.caption("Noch keine Fälle protokolliert.")
        return
    c1, c2 = st.columns(2)
    status = c1.multiselect("Status", M.STATUS, default=M.STATUS, key="v_status")
    ebene = c2.multiselect("Ebene", M.EBENEN, default=M.EBENEN, key="v_ebene")
    d = faelle[faelle["status"].isin(status) & faelle["ebene"].isin(ebene)]
    entsch = _entscheide(ctx)
    tabelle = pd.DataFrame({
        "Erstmals": d["erstmals"].map(_datum), "Stufe": d["stufe"], "Ebene": d["ebene"], "Bereich": d["bereich"], "Fall": d["titel"],
        "Status": d["status"], "Reaktion": d["reaktion"], "Begründung": d["begruendung"],
        "Neue Schwelle": d["neue_schwelle"], "Entscheid": d["entscheid_id"].map(lambda i: entsch.get(i, "")),
        "Bearbeitet von": d["bearbeiter"], "Zuletzt": d["zeit"].map(_datum)})
    st.dataframe(tabelle, hide_index=True, width="stretch", column_config={"Begründung": st.column_config.TextColumn(width="large")})
    erl = faelle[faelle["status"] == "Erledigt"]
    if len(erl):
        z = erl["reaktion"].value_counts()
        st.caption("Abgeschlossen: " + " · ".join(f"{k} {v}" for k, v in z.items()) +
                   f" · von selbst erloschen {int((faelle['status'] == 'Erloschen').sum())}")
    log = M.log_eintraege(ctx.tabellen.get("monitor_log"))
    st.markdown("**Gesendete Meldungen**")
    if log:
        st.dataframe(pd.DataFrame([{"Zeit": _datum(e["zeit"]), "Art": e["lauf"], "An": e["empfaenger"], "Betreff": e["betreff"],
                                    "Fälle": len(e["faelle"])} for e in reversed(log)]), hide_index=True, width="stretch")
    else:
        st.caption("Noch keine Mails gesendet.")
    R = SP.Sammler("Monitoring-Protokoll", "Signale, Reaktionen und Begründungen (Exception Report)", kurz="Monitoring")
    R.text(f"Stand {pd.Timestamp.now():%d.%m.%Y %H:%M}. {len(faelle)} Fälle, davon {int(faelle['status'].isin(M.OFFEN).sum())} offen.")
    R.tabelle(tabelle[["Erstmals", "Stufe", "Bereich", "Fall", "Status", "Reaktion", "Begründung", "Bearbeitet von"]],
              [24, 14, 22, 52, 16, 26, 60, 20], max_zeilen=200)
    SP.knopf(ph, R, "monitoring")


# ---------------------------------------------------------------------------
# Kauf-Thesen und Watchlist
# ---------------------------------------------------------------------------
def _thesen_watchlist(ctx, lage):
    import portfolio as P

    th = M.thesen(ctx.tabellen.get("thesen"))
    gehalten = sorted(a["ticker"] for a in lage.get("aktien_liste") or [])
    st.markdown("**Kauf-These je Titel (freiwillig)** · Kursziel, Stop und Überprüfungstermin werden überwacht; ein von Hand "
                "erfasstes Datum der Quartalszahlen hat Vorrang vor Yahoo Finance.")
    if th:
        st.dataframe(pd.DataFrame([{"Ticker": t, "These": v["these"], "Kursziel": v["kursziel"], "Stop": v["stop"],
                                    "Überprüfen am": v["ueberpruefung"], "Quartalszahlen": v["quartalszahlen"], "Von": v["person"]}
                                   for t, v in sorted(th.items())]), hide_index=True, width="stretch",
                     column_config={"These": st.column_config.TextColumn(width="large")})
    opt = sorted(set(gehalten) | set(th))
    c0, _ = st.columns([1, 2])
    wahl = c0.selectbox("Titel", opt + ["anderer Ticker …"], key="th_wahl") if opt else "anderer Ticker …"
    alt = th.get(wahl, {})
    with st.form("these"):
        ticker = st.text_input("Ticker", value="" if wahl == "anderer Ticker …" else wahl).strip().upper()
        these = st.text_area("These (warum gekauft, was muss eintreten)", value=alt.get("these", ""), height=70)
        c1, c2, c3, c4 = st.columns(4)
        kz = c1.text_input("Kursziel USD", value=M.de(alt["kursziel"], 2) if alt.get("kursziel") else "")
        sp = c2.text_input("Stop USD", value=M.de(alt["stop"], 2) if alt.get("stop") else "")
        ue = c3.text_input("Überprüfen am (TT.MM.JJJJ)", value=_dm(alt.get("ueberpruefung")))
        qz = c4.text_input("Quartalszahlen am (TT.MM.JJJJ)", value=_dm(alt.get("quartalszahlen")))
        if st.form_submit_button("These speichern", type="primary"):
            fehler = [n for n, v in (("Kursziel", kz), ("Stop", sp)) if v.strip() and M._z(v.replace("'", "")) is None]
            fehler += [n for n, v in (("Überprüfen am", ue), ("Quartalszahlen am", qz)) if v.strip() and _iso(v) is None]
            if not ticker:
                st.error("Bitte einen Ticker angeben.")
            elif fehler:
                st.error("Bitte prüfen: " + ", ".join(fehler))
            else:
                ctx.speichern("thesen", [dict(zeit=jetzt(), person=ctx.person, ticker=ticker, these=these.strip(),
                                              kursziel=M._z(kz.replace("'", "")) or "", stop=M._z(sp.replace("'", "")) or "",
                                              ueberpruefung=_iso(ue) or "", quartalszahlen=_iso(qz) or "")],
                              f"These für {ticker} gespeichert.")
    st.divider()
    st.markdown("**Watchlist (Kaufkandidaten)** · Signal «Prüfen», sobald der Kurs die Einstiegsmarke erreicht; zuständig sind "
                "Haupt- und Backup-Person des Sektors.")
    wl = M.watchlist(ctx.tabellen.get("watchlist"))
    if wl:
        kurse = lage.get("kurse") or {}
        st.dataframe(pd.DataFrame([{"Ticker": w["ticker"], "Name": w["name"], "Sektor": w["sektor"],
                                    "Marke": f"{w['op'].replace('<=', '≤').replace('>=', '≥')} {M.de(w['marke'], 2)}",
                                    "Kurs": M.de(float(kurse[w["ticker"]].dropna().iloc[-1]), 2) if w["ticker"] in kurse else "–",
                                    "These": w["these"], "Von": w["person"]} for w in wl]), hide_index=True, width="stretch")
        c1, c2, _ = st.columns([1.5, 1, 2])
        weg = c1.selectbox("Entfernen", [w["id"] for w in wl], format_func=lambda i: next(w["ticker"] for w in wl if w["id"] == i),
                           key="wl_weg")
        if c2.button("Von der Watchlist nehmen", key="wl_weg_btn"):
            w = next(x for x in wl if x["id"] == weg)
            ctx.speichern("watchlist", [dict(w, zeit=jetzt(), person=ctx.person, geloescht="1")], f"{w['ticker']} entfernt.")
    c0, _ = st.columns([1, 2])
    tk = c0.text_input("Ticker hinzufügen", placeholder="z. B. CAT – Enter drücken", key="wl_tk").strip().upper()
    info = P.ticker_info(tk) if tk else {}
    if info:
        st.markdown(f"{html.escape(info.get('name') or tk)} · **{html.escape(info.get('sektor') or 'Sektor unbekannt')}**")
    with st.form("wl", clear_on_submit=True):
        c1, c2, c3 = st.columns([1.2, 1, 1])
        sek_idx = C.SEKTOREN.index(info["sektor"]) if info.get("sektor") in C.SEKTOREN else None
        sektor = c1.selectbox("Sektor", C.SEKTOREN, index=sek_idx, placeholder="Sektor wählen")
        op = c2.selectbox("Melden, wenn Kurs", ["<=", ">="], format_func=lambda o: "≤ Marke (Einstieg bei Rückgang)" if o == "<="
                          else "≥ Marke (Ausbruch)")
        marke = c3.text_input("Marke USD")
        these = st.text_input("These / Grund")
        if st.form_submit_button("Auf die Watchlist", type="primary"):
            if not tk or not sektor or M._z(marke.replace("'", "")) is None:
                st.error("Bitte Ticker (oben), Sektor und eine Marke als Zahl angeben.")
            else:
                ctx.speichern("watchlist", [dict(zeit=jetzt(), person=ctx.person, id=uuid.uuid4().hex[:8], ticker=tk,
                                                 name=info.get("name", ""), sektor=sektor, op=op, marke=M._z(marke.replace("'", "")),
                                                 these=these.strip(), geloescht="")], f"{tk} auf die Watchlist gesetzt.")


def _iso(txt: str) -> str | None:
    txt = (txt or "").strip()
    if not txt:
        return None
    for f in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            return pd.to_datetime(txt, format=f).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue
    return None


def _dm(iso: str | None) -> str:
    try:
        return pd.Timestamp(iso).strftime("%d.%m.%Y") if iso else ""
    except (ValueError, TypeError):
        return ""


# ---------------------------------------------------------------------------
# Zuständigkeit und E-Mails
# ---------------------------------------------------------------------------
def _zustaendigkeit(ctx, team, kont, zust):
    namen = sorted(set(team) | set(kont)) or [ctx.person]
    st.markdown("**E-Mail-Adressen** · nur in der Google-Tabelle gespeichert (nicht im öffentlichen Code). «Alle Fälle» = erhält "
                "zusätzlich die Fälle aller Sektoren (z. B. für die Person, die das Monitoring verantwortet).")
    k_df = pd.DataFrame([{"Name": n, "E-Mail": kont.get(n, {}).get("email", ""), "Alle Fälle": kont.get(n, {}).get("alles", False)}
                         for n in namen])
    k_neu = st.data_editor(k_df, hide_index=True, width="stretch", key="kontakte_ed", disabled=["Name"],
                           column_config={"E-Mail": st.column_config.TextColumn(width="large"),
                                          "Alle Fälle": st.column_config.CheckboxColumn()})
    if st.button("E-Mail-Adressen speichern", type="primary", key="k_save"):
        zeilen = []
        for _, z in k_neu.iterrows():
            alt = kont.get(z["Name"], {})
            mail = str(z["E-Mail"] or "").strip()
            if mail and ("@" not in mail or "." not in mail.split("@")[-1]):
                st.error(f"E-Mail für {z['Name']} sieht ungültig aus.")
                return
            if mail != alt.get("email", "") or bool(z["Alle Fälle"]) != alt.get("alles", False):
                zeilen.append(dict(zeit=jetzt(), person=ctx.person, name=z["Name"], email=mail, alles="1" if z["Alle Fälle"] else ""))
        if zeilen:
            ctx.speichern("kontakte", zeilen, f"{len(zeilen)} Kontakte gespeichert.")
        else:
            st.info("Keine Änderung.")
    st.divider()
    st.markdown("**Zuständigkeit je Sektor** · Haupt- und Backup-Person erhalten beide alle Meldungen ihres Sektors (Titel, "
                "Watchlist, Sektor-Ziel, Trigger). Allokation, Anleihen, IPS und Makro gehen an alle.")
    z_df = pd.DataFrame([{"Sektor": s_, "Hauptperson": zust.get(s_, ("", ""))[0], "Backup": zust.get(s_, ("", ""))[1]}
                         for s_ in C.SEKTOREN])
    z_neu = st.data_editor(z_df, hide_index=True, width="stretch", key="zust_ed", disabled=["Sektor"], height=40 + 35 * 11,
                           column_config={"Hauptperson": st.column_config.SelectboxColumn(options=[""] + namen),
                                          "Backup": st.column_config.SelectboxColumn(options=[""] + namen)})
    neu = {z["Sektor"]: (z["Hauptperson"] or "", z["Backup"] or "") for _, z in z_neu.iterrows()}
    for p in M.zustaendigkeit_pruefen(neu, kont):
        st.warning(p, icon=":material/warning:")
    if st.button("Zuständigkeit speichern", type="primary", key="z_save"):
        gleich = [s_ for s_, (h, b) in neu.items() if h and h == b]
        if gleich:
            st.error("Haupt- und Backup-Person müssen verschieden sein: " + ", ".join(gleich))
            return
        zeilen = [dict(zeit=jetzt(), person=ctx.person, bereich=s_, haupt=h, backup=b) for s_, (h, b) in neu.items()
                  if (h, b) != zust.get(s_, ("", ""))]
        if zeilen:
            ctx.speichern("zustaendigkeit", zeilen, f"Zuständigkeit für {len(zeilen)} Sektoren gespeichert.")
        else:
            st.info("Keine Änderung.")
    st.divider()
    if st.button("Test-Mail an mich senden", icon=":material/mail:", key="testmail"):
        _an_mich(ctx, kont, "Test: Monitoring-Mail",
                 f"<p>Hallo {html.escape(ctx.person)}, diese Test-Mail zeigt, dass der Versand aus dem Monitoring funktioniert.</p>",
                 "test")


def _an_mich(ctx, kont, betreff, body, art) -> bool:
    k = kont.get(ctx.person)
    if not k:
        st.error("Für dich ist noch keine E-Mail-Adresse gespeichert (oben erfassen und speichern).")
        return False
    if not hasattr(ctx.store, "mail"):
        st.error("Mails gehen nur mit verbundener Google-Tabelle.")
        return False
    try:
        ctx.store.mail([k["email"]], betreff, body)
        ctx.store.anhaengen("monitor_log", [dict(zeit=jetzt(), person=ctx.person, lauf=art, empfaenger=ctx.person, faelle="[]",
                                                 betreff=betreff)])
    except Exception as e:  # noqa: BLE001
        st.error(f"Mail nicht gesendet: {e}")
        return False
    st.success(f"Gesendet an {k['email']} (nur an dich).")
    return True


# ---------------------------------------------------------------------------
# Schwellen und Probelauf
# ---------------------------------------------------------------------------
def _schwellen(ctx, s):
    st.markdown("**Schwellen** · Startwerte, änderbar; jede Änderung wird mit Person und Zeit protokolliert. Die Grenzen aus dem IPS "
                "(Bänder, Duration, Sektorabweichung, Titel-Maximum, Rebalancing-Toleranz, tragbarer Verlust, TE) kommen direkt von "
                "der Seite «IPS».")
    with st.form("schwellen"):
        neu = {}
        for gruppe in ("Titel", "Einhaltung", "Ablauf"):
            st.markdown(f"*{gruppe}*")
            keys = [k for k, v in M.SCHWELLEN.items() if v[3] == gruppe]
            cols = st.columns(3)
            for i, k in enumerate(keys):
                name, start, einheit, _ = M.SCHWELLEN[k]
                neu[k] = cols[i % 3].number_input(f"{name} ({einheit})", value=float(s[k]), step=0.5 if abs(start) < 20 else 5.0,
                                                  key=f"sw_{k}", help=f"Startwert {M.de(start, 1)}")
        if st.form_submit_button("Schwellen speichern", type="primary"):
            if neu["verlust_handeln"] > neu["verlust_pruefen"]:
                st.error("«Handeln» muss beim Verlust tiefer liegen als «Prüfen».")
            else:
                zeilen = [dict(zeit=jetzt(), person=ctx.person, schluessel=f"monitor|{k}", wert=v) for k, v in neu.items()
                          if abs(v - s[k]) > 1e-9]
                if zeilen:
                    ctx.speichern("einstellungen", zeilen, f"{len(zeilen)} Schwellen geändert.")
                else:
                    st.info("Keine Änderung.")
    with st.expander("Änderungen der Schwellen"):
        st.dataframe(M.schwellen_verlauf(ctx.tabellen.get("einstellungen")), hide_index=True, width="stretch")


def _probelauf(ctx, s, kont, zust, lade_kurse, lage):
    import portfolio as P
    import sektoren as SK

    st.markdown("**Probelauf** · Wie viele Fälle wären in den letzten drei Monaten entstanden? Gerechnet werden die Titel-Signale "
                "und die Sektor-Trigger, als wären die Titel vor drei Monaten gleich gewichtet gekauft worden und das Team hätte nie "
                "reagiert. Ziel: etwa 5–10 «Prüfen»-Fälle pro Woche. Das Ergebnis geht nur an dich.")
    gehalten = [a["ticker"] for a in lage.get("aktien_liste") or []]
    vorschlag = gehalten
    if not vorschlag:
        import analyse as A

        an = A.portfolios(ctx.tabellen.get("analysen"))
        if len(an):
            try:
                e = json.loads(an.iloc[0]["eingabe"])
                vorschlag = [str(z.get("Ticker") or "").upper() for z in e.get("aktien", []) if str(z.get("Ticker") or "").strip()]
            except (ValueError, KeyError, TypeError):
                vorschlag = []
    quelle = "aktuelles Portfolio" if gehalten else "zuletzt gespeichertes Analyseportfolio (Seite Analyse)" if vorschlag else "Eingabe"
    txt = st.text_input(f"Titel (Vorschlag aus: {quelle})", value=", ".join(vorschlag), key="probe_titel",
                        placeholder="z. B. MSFT, JPM, XOM, UNH")
    ticker = [t.strip().upper() for t in txt.replace(";", ",").split(",") if t.strip()]
    if st.button("Probelauf rechnen", icon=":material/science:", disabled=not ticker):
        with st.spinner("Rechne die letzten drei Monate Tag für Tag …"):
            kurse, fehlt, _ = lade_kurse(tuple(sorted(set(ticker) | set(C.SEKTOR_ETF.values()) | {"DX-Y.NYB"})))
            sek = {t: P.ticker_info(t).get("sektor", "") for t in ticker}
            sa = SK.lade_allokation(ctx.tabellen.get("sektor_allokation"))["sektoren"]
            st.session_state["probe"] = (M.probelauf([t for t in ticker if t in kurse], kurse, sek, s, ctx.fred, sa, zust), fehlt)
    if "probe" not in st.session_state:
        return
    p, fehlt = st.session_state["probe"]
    if fehlt:
        st.caption("Keine Kurse für: " + ", ".join(fehlt))
    if p.get("fehler"):
        st.error(p["fehler"])
        return
    df, wo = p["df"], p["wochen"]
    k = p["n_wochen"]
    n_p = int(df["stufe"].eq("Prüfen").sum()) if len(df) else 0
    ui.kopf_kpis([
        ("Fälle gesamt", str(len(df)), f"{p['start']:%d.%m.} – {p['ende']:%d.%m.%Y} · {p['n_titel']} Titel"),
        ("«Prüfen» pro Woche", M.de(n_p / k, 1), "Ziel etwa 5–10"),
        ("«Handeln» gesamt", str(int(df["stufe"].eq("Handeln").sum())) if len(df) else "0", "sofort-Mails"),
        ("«Info» gesamt", str(int(df["stufe"].eq("Info").sum())) if len(df) else "0", "nur Tages- und Wochenreport"),
    ])
    if len(wo):
        fig = go.Figure([go.Bar(name=x, x=wo["woche"], y=wo[x], marker_color=M.STUFE_FARBE[x]) for x in M.STUFEN if x in wo])
        fig.update_layout(barmode="stack", height=280, margin=dict(l=8, r=8, t=10, b=8), plot_bgcolor="white",
                          legend=dict(orientation="h", y=-0.2))
        fig.update_yaxes(title="neue Fälle", gridcolor="#EEF0F3")
        ui.zeige(fig)
        c1, c2 = st.columns(2)
        c1.markdown("**Nach Regel**")
        c1.dataframe(p["regeln"], hide_index=True, width="stretch")
        c2.markdown("**Pro Person (Sektor-Fälle laut Zuständigkeit)**")
        if len(p["personen"]):
            c2.dataframe(p["personen"], hide_index=True, width="stretch")
        else:
            c2.caption("Noch keine Zuständigkeit erfasst.")
        with st.expander(f"Alle {len(df)} Fälle"):
            st.dataframe(pd.DataFrame({"Datum": df["datum"].dt.strftime("%d.%m.%Y"), "Stufe": df["stufe"], "Fall": df["titel"],
                                       "Regel": df["regel"]}), hide_index=True, width="stretch")
        if st.button("Ergebnis per Mail an mich senden", icon=":material/mail:", key="probe_mail"):
            b, h = M.probelauf_html(p, ctx.person, s)
            _an_mich(ctx, kont, b, h, "probe")
    else:
        st.success("In diesem Zeitraum wäre kein Fall entstanden.")
