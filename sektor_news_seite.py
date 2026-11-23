"""Seite «Sektor-News»: wichtigste Meldungen je Sektor und Kalendermonat, Monatsfazit, eigene Meldungen, Team-Bewertung.

Daten: automatisch gesammelt (news_job.py, 3× täglich, verschlüsselt im Branch «daten») + eigene Meldungen und Bewertungen
aus der Google-Tabelle. Bewertung: Team-Durchschnitt, sobald jemand bewertet hat; sonst KI; sonst Regel.
Erste Simulationsperiode: Dezember 2026 (November = Vorlauf).
"""
from __future__ import annotations

import html
import os
import uuid

import pandas as pd
import streamlit as st

import ki
import newsdaten as N
import seitenpdf as SP
from makro import config as C
from makro import ui
from storage import jetzt

QUELLE = dict(token="", repo=N.REPO_STANDARD, lokal=os.environ.get("NEWS_DATEN_LOKAL") or None)
WIRKUNG = {"+": ("▲ positiv", "#2F855A"), "-": ("▼ negativ", "#B93A32"), "0": ("● neutral", "#6B7685")}
REL_FARBE = {5: "#14325C", 4: "#1F4E8C", 3: "#5B7DB1", 2: "#9AA3AF", 1: "#C3C9D2"}
REPO_URL = f"https://github.com/{N.REPO_STANDARD}"


def konfigurieren(token: str, repo: str | None = None):
    QUELLE.update(token=token or "", repo=repo or N.REPO_STANDARD)


@st.cache_data(ttl=10 * 60, show_spinner="Lade Sektor-News …")
def _lade(name: str, _token: str, repo: str, lokal: str | None):
    return N.lade_datei(name, _token, repo=repo, lokal=lokal)


def lade(name: str):
    if not QUELLE["token"] and not QUELLE["lokal"]:
        return None, "Ohne script_token keine gesammelten Daten (Schlüssel fehlt)."
    return _lade(name, QUELLE["token"], QUELLE["repo"], QUELLE["lokal"])


def monat_aktuell() -> str:
    return N.monat_von(N.iso(None))


def monatsdaten(ctx, monat: str) -> tuple[list[dict], dict | None, str | None]:
    d, f = lade(f"news/{monat}.json")
    ms = N.monatsliste(d, N.eigene_meldungen(ctx.tabellen.get("news_eigen")), N.bewertungen(ctx.tabellen.get("news_bewertung")), monat)
    return ms, d, f


def team_fazit(tab: pd.DataFrame | None, monat: str) -> dict:
    out = {}
    if tab is not None and not tab.empty:
        for _, r in tab[tab["monat"].astype(str) == monat].sort_values("zeit").iterrows():
            out[str(r["sektor"])] = dict(text=str(r["text"] or "").strip(), person=r["person"], zeit=str(r["zeit"]))
    return {k: v for k, v in out.items() if v["text"]}


MONATSNAMEN = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]


def monat_name(m: str) -> str:
    return f"{MONATSNAMEN[int(m[5:7]) - 1]} {m[:4]}"


def _datum(z) -> str:
    t = N.ts(z)
    return t.tz_convert("Europe/Zurich").strftime("%d.%m. %H:%M") if t is not None else ""


def rel_badge(eff: dict) -> str:
    r = eff["rel"]
    farbe = REL_FARBE[max(1, min(5, int(round(r))))]
    wert = ui.de(r, 1) if eff["quelle"].startswith("Team") and abs(r - round(r)) > 0.01 else f"{r:.0f}"
    return (f"<span style='background:{farbe};color:white;border-radius:5px;padding:1px 6px;font-size:11.5px;font-weight:700'>"
            f"{wert}</span> <span style='color:#6B7685;font-size:11px'>{html.escape(eff['quelle'])}</span>")


def wirkung_html(w: str) -> str:
    t, f = WIRKUNG.get(w, WIRKUNG["0"])
    return f"<span style='color:{f};font-size:12px;font-weight:600'>{t}</span>"


def meldung_html(m: dict, sektoren: bool = True) -> str:
    e = m["eff"]
    titel = html.escape(m["titel"])
    kopf = f"<a href='{html.escape(m['link'])}' target='_blank'>{titel}</a>" if m.get("link") else f"<b>{titel}</b>"
    n = m.get("n_quellen") or len(m.get("quellen") or []) or 1
    meta = [html.escape(m.get("quelle") or ""), f"{n} Quellen" if n > 1 else "", _datum(m["zeit"])]
    if sektoren and m.get("sektoren"):
        meta.append(" / ".join(m["sektoren"]))
    if m.get("ticker"):
        meta.append(", ".join(m["ticker"]))
    if m.get("art") == "team":
        meta.append(f"erfasst von {html.escape(m.get('erfasst_von', ''))}")
    zus = ""
    if m.get("ki_grund"):
        zus += f"<div style='color:#3B4555;font-size:12px'>KI: {html.escape(m['ki_grund'])}</div>"
    if m.get("text") and m.get("art") in ("team", "newsletter"):
        zus += f"<div style='color:#3B4555;font-size:12px'>{html.escape(m['text'][:300])}</div>"
    for p, k in e["kommentare"]:
        zus += f"<div style='color:#14325C;font-size:12px'>💬 <b>{html.escape(p)}</b>: {html.escape(k)}</div>"
    return (f"<div style='margin:0 0 9px 0'>{rel_badge(e)} &nbsp;{wirkung_html(e['wirkung'])}<br>{kopf}"
            f"<div class='klein' style='color:#6B7685'>{' · '.join(x for x in meta if x)}</div>{zus}</div>")


def bewertung_formular(ctx, ms: list[dict], key: str, titel: str = "Meldung bewerten"):
    """Relevanz, Wirkung, Kommentar, Ausblenden – je Person, es zählt der Team-Durchschnitt."""
    if ctx.nur_lesen or not ms:
        return
    bew = N.bewertungen(ctx.tabellen.get("news_bewertung"))
    st.markdown(f"**{titel}**")
    wahl = st.selectbox("Meldung", [m["id"] for m in ms], key=f"bw_w_{key}", label_visibility="collapsed",
                        format_func=lambda i: next((f"[{m['eff']['rel']:.0f}] {m['titel'][:110]}" for m in ms if m["id"] == i), i))
    m = next(x for x in ms if x["id"] == wahl)
    eigen = bew.get(wahl, {}).get(ctx.person, {})
    with st.form(f"bw_f_{key}_{wahl}", border=False):
        c1, c2 = st.columns([1.2, 1])
        rel = c1.select_slider("Relevanz", [1, 2, 3, 4, 5], value=int(eigen.get("relevanz") or round(m["eff"]["rel"])),
                               help="5 = kann eine Sektorgewichtung oder Position ändern · 4 = wichtig für den Ausblick · "
                                    "3 = beachtenswert · 2 = Randnotiz · 1 = irrelevant")
        w0 = eigen.get("wirkung") or m["eff"]["wirkung"]
        w = c2.radio("Wirkung", ["+", "-", "0"], index=["+", "-", "0"].index(w0 if w0 in ("+", "-", "0") else "0"),
                     format_func=lambda x: WIRKUNG[x][0], horizontal=True)
        kom = st.text_input("Kommentar (optional)", value=eigen.get("kommentar", ""), placeholder="z. B. betrifft unsere These bei …")
        aus = st.checkbox("Für alle ausblenden (nicht relevant / doppelt)", value=bool(eigen.get("ausblenden")))
        if st.form_submit_button("Bewertung speichern", type="primary", icon=":material/star:"):
            ctx.speichern("news_bewertung", [dict(zeit=jetzt(), person=ctx.person, news_id=wahl, relevanz=rel, wirkung=w,
                                                  kommentar=kom.strip(), ausblenden="1" if aus else "")],
                          "Bewertung gespeichert.")


def sektor_block_daten(ctx, monat: str | None = None) -> dict:
    """Für die Sektorallokation: {Sektor: {fazit, quelle, top: [3 Meldungen]}}."""
    monat = monat or monat_aktuell()
    ms, d, _ = monatsdaten(ctx, monat)
    tf = team_fazit(ctx.tabellen.get("news_fazit"), monat)
    out = {}
    for s in C.SEKTOREN:
        sm = [m for m in ms if s in (m.get("sektoren") or []) and not m["eff"]["ausgeblendet"]]
        kf = ((d or {}).get("fazit") or {}).get(s)
        if s in tf:
            fz, q = tf[s]["text"], f"Team ({tf[s]['person']})"
        elif kf:
            fz, q = kf["text"], f"KI, Stand {_datum(kf['zeit'])}"
        else:
            fz, q = N.regel_fazit([m for m in sm if m["eff"]["rel"] >= 3]), "automatisch (ohne KI)"
        out[s] = dict(fazit=fz, quelle=q, top=N.top(sm, n=3, min_rel=3), n=len(sm), monat=monat)
    return out


def _fazit_box(s: str, d: dict | None, sm: list[dict], tf: dict):
    kf = ((d or {}).get("fazit") or {}).get(s)
    if kf:
        st.markdown(f"<div style='background:#F3F6FB;border-left:3px solid #1F4E8C;padding:8px 12px;border-radius:4px;"
                    f"font-size:13.5px'>{html.escape(kf['text'])}<div class='klein' style='color:#6B7685;margin-top:4px'>"
                    f"Monatsfazit KI · Stand {_datum(kf['zeit'])} · wird neu geschrieben, sobald eine neue wichtige Meldung "
                    f"(Relevanz ab 4) dazukommt</div></div>", unsafe_allow_html=True)
    else:
        st.markdown(f"<div style='background:#F5F6F8;border-left:3px solid #9AA3AF;padding:8px 12px;border-radius:4px;"
                    f"font-size:13px'>{html.escape(N.regel_fazit([m for m in sm if m['eff']['rel'] >= 3]))}"
                    f"<div class='klein' style='color:#6B7685;margin-top:4px'>Automatische Übersicht (ohne KI) – ein KI-Fazit "
                    f"entsteht, sobald eine Meldung mit Relevanz ab 4 vorliegt</div></div>", unsafe_allow_html=True)
    if s in tf:
        st.markdown(f"<div style='background:#FDF6E7;border-left:3px solid #B7791F;padding:8px 12px;border-radius:4px;"
                    f"font-size:13.5px;margin-top:6px'>{html.escape(tf[s]['text'])}<div class='klein' style='color:#6B7685;"
                    f"margin-top:4px'>Team-Fazit · {html.escape(str(tf[s]['person']))}, {_datum(tf[s]['zeit'])}</div></div>",
                    unsafe_allow_html=True)


def seite_sektor_news(ctx, team: list[str]):
    R = SP.Sammler("Sektor-News", "Wichtigste Meldungen je Sektor und Monat, mit Bewertung und Monatsfazit", kurz="Sektor-News")
    ui.seitenkopf("Sektor-News", "Wichtigste Meldungen je S&P-500-Sektor und Kalendermonat. Sammelt automatisch 3× täglich "
                                 "(Google News, eure Newsletter), ihr könnt eigene Meldungen erfassen und die Relevanz bewerten.")
    ph = st.empty()
    monate = list(reversed(N.monate_bis_heute()))
    c1, c2, c3 = st.columns([1.1, 1.3, 2])
    monat = c1.selectbox("Monat", monate, format_func=lambda m: monat_name(m) + (" (Vorlauf)" if m < C.SIMULATION_START[:7] else ""),
                         key="sn_monat")
    min_rel = c2.select_slider("Mindest-Relevanz in der Übersicht", [1, 2, 3, 4, 5], value=3, key="sn_min")
    ms, d, fehler = monatsdaten(ctx, monat)
    status, _ = lade("status.json")
    v = ki.verbrauch(ctx.tabellen.get("ki_verbrauch"))
    dk = ki.deckel(ctx.tabellen.get("einstellungen"))
    sichtbar = [m for m in ms if not m["eff"]["ausgeblendet"]]
    wichtig = [m for m in sichtbar if m["eff"]["rel"] >= 4]
    ui.kopf_kpis([
        ("Meldungen im Monat", str(sum(1 for m in sichtbar if m["eff"]["rel"] >= 3)),
         f"Relevanz ab 3 · {sum(1 for m in sichtbar if m['art'] == 'team')} vom Team erfasst"),
        ("Wichtig (ab 4)", str(len(wichtig)), f"davon {sum(1 for m in wichtig if m['eff']['rel'] >= 4.5)} mit Relevanz 5"),
        ("Letzte Sammlung", _datum(status["stand"]) if status else "–",
         "automatisch 6:05 · 14:05 · 22:35" if status else "noch kein Lauf – siehe Tab «KI & Einstellungen»"),
        ("KI-Verbrauch " + pd.Timestamp.now().strftime("%m.%Y"), f"{v['usd']:.2f} USD", f"Deckel {dk:.2f} USD · {v['aufrufe']} Abfragen"),
    ])
    if fehler:
        st.warning(fehler, icon=":material/cloud_off:")
    if monat < C.SIMULATION_START[:7]:
        st.caption("Vorlauf: Die erste Simulationsperiode beginnt am 01.12.2026. Die Sammlung läuft schon, damit ihr ab dem "
                   "ersten Tag einen Überblick habt.")
    tf = team_fazit(ctx.tabellen.get("news_fazit"), monat)
    try:
        import sektoren as SK

        sa = SK.lade_allokation(ctx.tabellen.get("sektor_allokation"))["sektoren"]
    except Exception:  # noqa: BLE001
        sa = {}

    t1, t2, t3, t4 = st.tabs(["Übersicht je Sektor", "Alle Meldungen", "Meldung erfassen", "KI & Einstellungen"])
    with t1:
        R.h(f"Übersicht {monat_name(monat)}")
        reihen = sorted(C.SEKTOREN, key=lambda s: -sum(1 for m in sichtbar if s in m["sektoren"] and m["eff"]["rel"] >= 4))
        for s in reihen:
            sm = [m for m in sichtbar if s in (m.get("sektoren") or [])]
            liste = N.top(sm, n=25, min_rel=min_rel)
            pos = sum(1 for m in liste if m["eff"]["wirkung"] == "+")
            neg = sum(1 for m in liste if m["eff"]["wirkung"] == "-")
            e = sa.get(s, {})
            stand = e.get("richtung", "") + (f" {ui.de(e['abweichung'], 1, True)} Pp." if e.get("abweichung") is not None else "")
            n4 = sum(1 for m in liste if m["eff"]["rel"] >= 4)
            with st.expander(f"{s} · {stand or 'Neutral'} · {len(liste)} Meldungen, {n4} wichtig · ▲ {pos} ▼ {neg}", expanded=n4 > 0):
                _fazit_box(s, d, sm, tf)
                st.markdown("")
                if not liste:
                    st.caption("Keine Meldungen über der Mindest-Relevanz.")
                st.markdown("".join(meldung_html(m, sektoren=False) for m in liste[:6]), unsafe_allow_html=True)
                if len(liste) > 6:
                    with st.popover(f"Weitere {len(liste) - 6} Meldungen", icon=":material/expand_more:"):
                        st.markdown("".join(meldung_html(m, sektoren=False) for m in liste[6:]), unsafe_allow_html=True)
                st.divider()
                bewertung_formular(ctx, liste or sm[:25], f"s_{s}")
                if not ctx.nur_lesen:
                    with st.form(f"sn_tf_{s}_{monat}", border=False):
                        st.markdown("**Team-Fazit** (wird statt des automatischen Fazits gezeigt, auch auf der Seite «Sektorallokation»)")
                        txt = st.text_area("Team-Fazit", value=tf.get(s, {}).get("text", ""), height=90, label_visibility="collapsed",
                                           placeholder="Was heisst die Nachrichtenlage für unsere Gewichtung?")
                        if st.form_submit_button("Fazit speichern", icon=":material/save:"):
                            ctx.speichern("news_fazit", [dict(zeit=jetzt(), person=ctx.person, monat=monat, sektor=s, text=txt.strip())],
                                          f"Fazit {s} gespeichert.")
            kf = ((d or {}).get("fazit") or {}).get(s)
            R.h2(f"{s} ({stand or 'Neutral'})")
            R.text(tf[s]["text"] if s in tf else kf["text"] if kf else N.regel_fazit([m for m in sm if m["eff"]["rel"] >= 3]))
            for m in liste[:5]:
                R.text(f"[{m['eff']['rel']:.0f} {WIRKUNG.get(m['eff']['wirkung'], WIRKUNG['0'])[0]}] {m['titel']} "
                       f"({m.get('quelle', '')}, {m['zeit'][:10]})", klein=True)

    with t2:
        f1, f2, f3, f4 = st.columns([1.6, 0.8, 1, 1.4])
        fs = f1.multiselect("Sektoren", C.SEKTOREN, key="sn_f_s", placeholder="alle")
        fr = f2.selectbox("ab Relevanz", [1, 2, 3, 4, 5], index=2, key="sn_f_r")
        fa = f3.multiselect("Herkunft", ["sektor", "titel", "newsletter", "team"], key="sn_f_a", placeholder="alle",
                            format_func={"sektor": "Sektor-Suche", "titel": "Titel-Suche", "newsletter": "Newsletter",
                                         "team": "vom Team"}.get)
        fq = f4.text_input("Suche", key="sn_f_q", placeholder="Stichwort oder Ticker")
        aus = st.toggle("Ausgeblendete zeigen", key="sn_f_aus")
        gef = [m for m in ms if (aus or not m["eff"]["ausgeblendet"]) and m["eff"]["rel"] >= fr
               and (not fs or set(fs) & set(m.get("sektoren") or [])) and (not fa or m.get("art") in fa)
               and (not fq or fq.lower() in (m["titel"] + " " + " ".join(m.get("ticker") or [])).lower())]
        if gef:
            st.dataframe(pd.DataFrame([{
                "Datum": _datum(m["zeit"]), "Relevanz": round(m["eff"]["rel"], 1), "Bewertung": m["eff"]["quelle"],
                "Wirkung": WIRKUNG.get(m["eff"]["wirkung"], WIRKUNG["0"])[0], "Sektoren": ", ".join(m.get("sektoren") or []),
                "Titel": ", ".join(m.get("ticker") or []), "Schlagzeile": m["titel"], "Quelle": m.get("quelle", ""),
                "Quellen": m.get("n_quellen") or 1, "Einordnung KI": m.get("ki_grund", ""),
                "Kommentare": " · ".join(f"{p}: {k}" for p, k in m["eff"]["kommentare"]), "Link": m.get("link") or None}
                for m in gef]), hide_index=True, width="stretch",
                column_config={"Link": st.column_config.LinkColumn(display_text="öffnen"),
                               "Relevanz": st.column_config.NumberColumn(format="%.1f"),
                               "Schlagzeile": st.column_config.TextColumn(width="large")})
            st.caption(f"{len(gef)} Meldungen. Bewertung: «Team (n)» = Durchschnitt eurer Bewertungen, «KI» = Einschätzung der KI, "
                       "«Regel» = automatische Regel (Quelle, Anzahl Quellen, Stichwörter, eure Titel).")
            bewertung_formular(ctx, gef[:200], "alle")
        else:
            st.caption("Keine Meldungen für diese Auswahl.")

    with t3:
        if ctx.nur_lesen:
            st.caption("Mit Lesezugriff kann nichts erfasst werden.")
        else:
            st.caption("Für Meldungen, die die automatische Suche nicht findet (z. B. aus Bloomberg, Dozenten-Briefing, Analystenberichten). "
                       "Eure Relevanz gilt sofort als Team-Bewertung.")
            with st.form("sn_neu", clear_on_submit=True):
                titel = st.text_input("Schlagzeile / Titel *")
                c1, c2, c3 = st.columns([1.4, 1, 1])
                link = c1.text_input("Link (optional)")
                quelle = c2.text_input("Quelle", placeholder="z. B. Bloomberg")
                datum = c3.date_input("Datum", value=pd.Timestamp.today().date(), format="DD.MM.YYYY")
                c4, c5 = st.columns([1.6, 1])
                sek = c4.multiselect("Sektor(en) *", C.SEKTOREN)
                tick = c5.text_input("Ticker (optional, mit Komma)", placeholder="z. B. XOM, CVX")
                text = st.text_area("Kurz: Was ist passiert, warum zählt es?", height=80)
                c6, c7 = st.columns([1.2, 1])
                rel = c6.select_slider("Relevanz", [1, 2, 3, 4, 5], value=4)
                w = c7.radio("Wirkung", ["+", "-", "0"], format_func=lambda x: WIRKUNG[x][0], horizontal=True)
                if st.form_submit_button("Meldung speichern", type="primary", icon=":material/add:"):
                    if not titel.strip() or not sek:
                        st.error("Bitte Schlagzeile und mindestens einen Sektor angeben.")
                    else:
                        nid = "t" + uuid.uuid4().hex[:11]
                        z = jetzt()
                        ctx.store.anhaengen("news_bewertung", [dict(zeit=z, person=ctx.person, news_id=nid, relevanz=rel, wirkung=w,
                                                                    kommentar="", ausblenden="")])
                        ctx.speichern("news_eigen", [dict(zeit=z, person=ctx.person, id=nid, datum=pd.Timestamp(datum).date().isoformat(),
                                                          titel=titel.strip(), link=link.strip(), quelle=quelle.strip() or "Team",
                                                          sektoren=",".join(sek), ticker=tick.upper().replace(" ", ""), text=text.strip(),
                                                          geloescht="")], "Meldung gespeichert.")
            eig = [m for m in ms if m.get("art") == "team"]
            if eig:
                st.markdown("**Eure Meldungen in diesem Monat**")
                st.markdown("".join(meldung_html(m) for m in eig), unsafe_allow_html=True)
                wahl = st.selectbox("Meldung entfernen", [m["id"] for m in eig], index=None, placeholder="– wählen –",
                                    format_func=lambda i: next(m["titel"][:100] for m in eig if m["id"] == i), key="sn_del")
                if wahl and st.button("Entfernen", key="sn_del_b"):
                    r = ctx.tabellen["news_eigen"]
                    r = r[r["id"] == wahl].sort_values("zeit").iloc[-1].to_dict()
                    r.update(zeit=jetzt(), person=ctx.person, geloescht="1")
                    ctx.speichern("news_eigen", [r], "Meldung entfernt.")

    with t4:
        _ki_einstellungen(ctx, v, dk, status)
    SP.knopf(ph, R, "sektor_news")


def _ki_einstellungen(ctx, v: dict, dk: float, status: dict | None):
    st.markdown("**Wo die KI eingesetzt wird – und wo nicht**")
    st.markdown(
        "- **Zuerst die Regel (kostenlos):** gleiche Story von mehreren Quellen wird zusammengefasst, Rauschen («3 stocks to buy», "
        "Kursziel-Listen, Fonds-Positionen) aussortiert, alles nach Quelle, Anzahl Quellen, Stichwörtern und euren Titeln bewertet.\n"
        "- **KI nur für neue Kandidaten** mit Regel-Relevanz ab 3 (höchstens 4 je Sektor, 2 je Titel und 10 Newsletter-Absätze pro "
        "Lauf), gebündelt in einer Abfrage: Relevanz, Wirkung und eine kurze Einordnung.\n"
        "- **Monatsfazit** je Sektor nur neu, wenn eine neue Meldung mit Relevanz ab 4 dazukommt (höchstens 1× pro Tag); "
        "**Firmen-News-Kurzfassung** nur bei neuen relevanten Meldungen; **Firmenprofil** einmal pro Titel.\n"
        "- **Nie KI** für Kennzahlen, Analystendaten, Sortierung, eure Bewertungen, Signale, Mails und Buchungen.")
    c1, c2 = st.columns([1, 1.4])
    with c1:
        st.markdown(f"**Verbrauch {pd.Timestamp.now():%m.%Y}:** {v['usd']:.3f} USD von {dk:.2f} USD")
        st.progress(min(1.0, v["usd"] / dk) if dk else 1.0)
        if v["nach_zweck"]:
            st.dataframe(pd.DataFrame([{"Zweck": z, "USD": round(u, 4)} for z, u in sorted(v["nach_zweck"].items(), key=lambda x: -x[1])]),
                         hide_index=True, width="stretch")
        if not ctx.nur_lesen:
            with st.form("ki_deckel"):
                neu = st.number_input("Monatsdeckel (USD)", min_value=0.0, max_value=50.0, value=float(dk), step=0.5, format="%.2f",
                                      help="Ist er erreicht, läuft bis Monatsende nur noch die kostenlose Regel. 0 = KI aus.")
                if st.form_submit_button("Deckel speichern"):
                    ctx.speichern("einstellungen", [dict(zeit=jetzt(), person=ctx.person, schluessel="ki|deckel_usd", wert=neu)],
                                  "KI-Deckel gespeichert.")
    with c2:
        st.markdown("**Letzter Sammel-Lauf**")
        if status:
            st.caption(f"{_datum(status['stand'])} · KI {'aktiv' if status.get('ki_aktiv') else 'aus (' + (status.get('ki_grund') or '') + ')'}"
                       f" · Kosten des Laufs {status.get('kosten_lauf', 0):.4f} USD")
            st.code("\n".join(status.get("info") or []), language=None)
        else:
            st.info("Noch kein Lauf gefunden. Einrichtung (einmalig):\n\n"
                    f"1. Secrets prüfen bzw. ergänzen: [{REPO_URL}/settings/secrets/actions]({REPO_URL}/settings/secrets/actions) – "
                    "`SCRIPT_URL` und `SCRIPT_TOKEN` (habt ihr fürs Monitoring schon), neu `ANTHROPIC_API_KEY` (gleicher Key wie "
                    "`anthropic_api_key` in den Streamlit-Secrets).\n"
                    f"2. Lauf starten: [{REPO_URL}/actions/workflows/sektor-news.yml]({REPO_URL}/actions/workflows/sektor-news.yml) → "
                    "«Run workflow».\n3. Nach 3–5 Minuten diese Seite neu laden.", icon=":material/info:")
    st.caption("Speicher: gesammelte Meldungen und Unternehmensdaten verschlüsselt im Branch «daten» des Repos (Schlüssel aus dem "
               "script_token), eure Eingaben in der Google-Tabelle (news_eigen, news_bewertung, news_fazit, team_rating, ki_verbrauch).")
