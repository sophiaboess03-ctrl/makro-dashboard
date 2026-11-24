"""Seite «Unternehmen» (unter Portfolio): für jeden Titel aus Bestand, Watchlist und Probe-Portfolio automatisch ein Profil –
nach Sektoren gegliedert. Profil, Kennzahlen, unsere Position, Analysten (Konsens, Kursziele, Up-/Downgrades), News,
Kauf-These, offene Monitoring-Fälle und Team-Rating.

Daten: Yahoo Finance (ohne KI), 1× täglich vom Sammel-Lauf aktualisiert (verschlüsselt im Branch «daten»); fehlt ein Titel
dort noch (z. B. gerade erst auf die Watchlist gesetzt), lädt die Seite ihn direkt von Yahoo.
"""
from __future__ import annotations

import html
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import monitoring as M
import newsdaten as N
import seitenpdf as SP
import sektor_news_seite as SNS
from makro import config as C
from makro import ui
from storage import jetzt

RATINGS = ["Kaufen", "Halten", "Verkaufen"]
RATING_FARBE = {"Kaufen": "#2F855A", "Halten": "#B7791F", "Verkaufen": "#B93A32"}
HERKUNFT_FARBE = {"Bestand": "#14325C", "Watchlist": "#5B7DB1", "Probe-Portfolio": "#8E5572", "Analyse": "#B7791F"}


@st.cache_resource
def _yahoo_speicher() -> dict:
    """Direkt geladene Firmendaten je Titel: {ticker: (zeitpunkt, daten)}. Vollständige Daten gelten 12 Std., unvollständige
    nur 10 Min. – so bleibt ein Titel nicht einen halben Tag leer, wenn Yahoo kurz drosselt."""
    return {}


def _yahoo_direkt(ticker: tuple[str, ...], neu: bool = False) -> dict:
    import time

    sp = _yahoo_speicher()
    jetzt_s = time.time()

    def _gueltig(t):
        z, f = sp.get(t, (0, {}))
        voll = len(f.get("info") or {}) > 3 and not f.get("teilweise")
        return not neu and jetzt_s - z < (12 * 3600 if voll else 600)

    holen = [t for t in ticker if not _gueltig(t)]
    if holen:
        with st.spinner(f"Lade Unternehmensdaten von Yahoo ({len(holen)} Titel) …"):
            for t, f in N.firmen_yahoo(holen, parallel=2).items():
                sp[t] = (jetzt_s, f)
    return {t: sp[t][1] for t in ticker if t in sp}


@st.cache_resource
def _bilanz_speicher() -> dict:
    """Direkt geladene Bilanz-/Schätzungsdaten je Titel (für Titel, die der Sammel-Lauf nicht abdeckt):
    {ticker: (zeitpunkt, daten)}. Mit Daten 12 Std. gültig, ohne nur 10 Min."""
    return {}


def _bilanz_direkt(ticker: list[str], neu: bool = False) -> dict:
    import time

    sp = _bilanz_speicher()
    jetzt_s = time.time()

    def _gueltig(t):
        z, f = sp.get(t, (0, {}))
        return not neu and jetzt_s - z < (12 * 3600 if (f.get("fin") or f.get("eps_trend")) else 600)

    holen = [t for t in ticker if not _gueltig(t)]
    if holen:
        with st.spinner(f"Lade Bilanz, Cashflow und Gewinnschätzungen von Yahoo ({len(holen)} Titel) …"):
            ex = ThreadPoolExecutor(max_workers=2)
            futs = {t: ex.submit(N.firma_bilanz, t) for t in holen}
            for t, fu in futs.items():
                try:
                    sp[t] = (jetzt_s, fu.result(timeout=90))
                except Exception as e:  # noqa: BLE001
                    sp[t] = (jetzt_s, dict(bilanz_fehler=[type(e).__name__]))
            ex.shutdown(wait=False)
    return {t: sp[t][1] for t in ticker if t in sp}


def _bilanz_aus_speicher(t: str) -> dict:
    z = _bilanz_speicher().get(t)
    return z[1] if z else {}


def team_ratings(tab: pd.DataFrame | None) -> dict:
    """{Ticker: {Person: {rating, begruendung, zeit}}}"""
    out: dict = {}
    if tab is not None and not tab.empty:
        for _, r in tab.sort_values("zeit").iterrows():
            out.setdefault(str(r["ticker"]).upper(), {})[str(r["person"])] = dict(rating=str(r["rating"]),
                                                                                  begruendung=str(r["begruendung"] or ""),
                                                                                  zeit=str(r["zeit"]))
    return out


def team_konsens(r: dict) -> str:
    if not r:
        return "–"
    st_ = [x["rating"] for x in r.values() if x["rating"] in RATINGS]
    if not st_:
        return "–"
    top = max(RATINGS, key=lambda x: st_.count(x))
    return f"{top} ({st_.count(top)}/{len(st_)})"


def _pct(x, d=1, vz=False):
    return "–" if x is None else f"{ui.de(x * 100 if abs(x) < 5 else x, d, vz)} %"


def _mrd(x):
    return "–" if not x else f"{ui.de(x / 1e9, 1)} Mrd." if x < 1e12 else f"{ui.de(x / 1e12, 2)} Bio."


def _z(x, d=1):
    return "–" if x is None else ui.de(x, d)


def _pill(text: str, farbe: str) -> str:
    return (f"<span style='background:{farbe};color:white;border-radius:5px;padding:1px 7px;font-size:11.5px;"
            f"font-weight:600;margin-right:4px'>{html.escape(text)}</span>")


def _position(tabellen: dict, fred: dict, lade_kurse) -> dict:
    """Bestand: Gewicht, Einstand, Performance seit Kauf vs. Sektor-ETF."""
    import portfolio as P

    tx = P.transaktionen(tabellen.get("transaktionen"))
    if not len(tx):
        return {}
    pos, cash_delta, _ = P.positionen(tx)
    aktien = sorted({p["ticker"] for p in pos.values() if p["klasse"] == "Aktie"})
    kurse = lade_kurse(tuple(aktien) + tuple(C.SEKTOR_ETF.values()))[0] if aktien else {}
    heute = pd.Timestamp.today().normalize()
    b = P.bewerte(pos, kurse, fred, heute)
    kapital, _ = P._einstellungen(tabellen.get("einstellungen"))
    total = (float(b["wert"].sum()) + float(b["zins"].sum()) if len(b) else 0.0) + kapital + cash_delta
    out = {}
    for _, z in b[b["klasse"] == "Aktie"].iterrows() if len(b) else []:
        ek = z["einstand"] / z["menge"] if z["menge"] else None
        r = (z["kurs"] / ek - 1) * 100 if ek else None
        etf = C.SEKTOR_ETF.get(str(z["sektor"]))
        r_etf = None
        s = kurse.get(etf) if etf else None
        if s is not None and len(s) and pd.notna(z["kaufdatum"]):
            s0 = s[s.index <= pd.Timestamp(z["kaufdatum"])]
            if len(s0):
                r_etf = (float(s.iloc[-1]) / float(s0.iloc[-1]) - 1) * 100
        out[z["ticker"]] = dict(menge=z["menge"], kurs=z["kurs"], wert=z["wert"], gewicht=z["wert"] / total * 100 if total else None,
                                einstand=ek, rendite=r, etf=etf, r_etf=r_etf, kaufdatum=z["kaufdatum"])
    return out


def seite_unternehmen(ctx, fred: dict, lade_kurse):
    R = SP.Sammler("Unternehmen", "Profile, Kennzahlen, Analysten und News je Titel – nach Sektoren", kurz="Unternehmen")
    ui.seitenkopf("Unternehmen", "Für jeden Titel aus Bestand und Watchlist (auf Wunsch auch aus der Analyse): Profil, Kennzahlen, "
                                 "Analysten-Einschätzung, News und eure Einschätzung – nach Sektoren gegliedert.")
    ph = st.empty()
    tab = ctx.tabellen
    import portfolio as P

    # Titel: Bestand + Watchlist (+ mitverfolgte Vorauswahl); Analyse-Titel nur auf Wunsch
    import analyse as A

    an = A.portfolios(tab.get("analysen"))
    aid = N.probe_analyse_id(tab)
    titel = N.titel_liste(tab, P.sp500_liste())
    ss = st.session_state
    with st.container(border=True):
        c1, c2 = st.columns([1.2, 2.6])
        zeigen = c1.toggle("Titel aus der Analyse anzeigen", key="un_an",
                           help="Zum kurzen Ansehen: die Titel der aktuellen Eingabe auf der Seite «Analyse» oder einer gespeicherten "
                                "Vorauswahl. Standardmässig nur Bestand und Watchlist.")
        quelle = None
        if zeigen:
            opt = (["eingabe"] if "an_aktien" in ss else []) + list(an["id"]) if len(an) else (["eingabe"] if "an_aktien" in ss else [])
            aktiv_name = ss.get("an_pf_name") or ss.get("_an_pf_name") or ""
            namen = {"eingabe": f"Aktives Analyseportfolio{' «' + aktiv_name + '»' if aktiv_name else ''} – aktueller Stand, auch ungespeichert",
                     **({r["id"]: f"Analyseportfolio «{r['name']}» ({r['person']}, {str(r['zeit'])[:10]})" for _, r in an.iterrows()} if len(an) else {})}
            if opt:
                quelle = c2.selectbox("Welche Analyse?", opt, format_func=namen.get, key="un_an_quelle", label_visibility="collapsed")
            else:
                c2.caption("Noch keine Analyse: auf der Seite «Analyse» Titel eintragen oder ein Analyseportfolio speichern.")
        if quelle == "eingabe":
            neu_t = [str(t).strip().upper() for t in ss["an_aktien"]["Ticker"] if str(t).strip() not in ("", "<NA>", "nan")]
        elif quelle:
            neu_t = N.probe_ticker(tab, quelle)
        else:
            neu_t = []
        sp = P.sp500_liste()
        for t in neu_t:
            d = titel.setdefault(t, dict(name=sp.get(t, ("", ""))[0], sektor=sp.get(t, ("", ""))[1], herkunft=[]))
            if "Analyse" not in d["herkunft"]:
                d["herkunft"].append("Analyse")
        if quelle and quelle != "eingabe" and not ctx.nur_lesen:
            if aid == quelle:
                st.caption("Dieses Analyseportfolio wird automatisch mitverfolgt (News, Analysten, KI-Profil im Sammel-Lauf).")
                if st.button("Nicht mehr mitverfolgen", key="un_folgen_aus"):
                    ctx.speichern("einstellungen", [dict(zeit=jetzt(), person=ctx.person, schluessel="unternehmen|analyse_id", wert="")],
                                  "Analyseportfolio wird nicht mehr mitverfolgt.")
            elif st.button("Dieses Analyseportfolio automatisch mitverfolgen", key="un_folgen_an", icon=":material/track_changes:",
                           help="Der Sammel-Lauf holt dann auch für diese Titel News, Analystendaten und ein KI-Profil."):
                ctx.speichern("einstellungen", [dict(zeit=jetzt(), person=ctx.person, schluessel="unternehmen|analyse_id", wert=quelle)],
                              "Analyseportfolio wird ab dem nächsten Sammel-Lauf mitverfolgt.")
        elif neu_t:
            st.caption("Daten direkt von Yahoo (ohne KI-Profil und Firmen-Kurzfassung) – zum Mitverfolgen das Analyseportfolio speichern "
                       "und hier wählen.")
    U, fehler = SNS.lade("unternehmen.json")
    U = dict(U or {})
    fehlt = tuple(sorted(t for t in titel if t not in U or not (U[t].get("info"))))
    if fehlt:
        neu = bool(ss.pop("un_yahoo_neu", False))
        for t, f in _yahoo_direkt(fehlt, neu=neu).items():
            if f.get("info") or t not in U:
                U[t] = {**(U.get(t) or {}), **f}
        # Kurs und 52-Wochen-Spanne notfalls aus den Tageskursen (gleicher Abruf wie im Rest der App)
        ohne = tuple(t for t in fehlt if not ((U.get(t) or {}).get("info") or {}).get("currentPrice"))
        if ohne:
            try:
                kurse = lade_kurse(ohne)[0] or {}
            except Exception:  # noqa: BLE001
                kurse = {}
            for t in ohne:
                s = kurse.get(t)
                if s is None or not len(s.dropna()):
                    continue
                s = s.dropna()
                f = dict(U.get(t) or {"ticker": t, "stand": N.iso(None), "fehler": []})
                i = dict(f.get("info") or {})
                j = s[s.index >= s.index[-1] - pd.Timedelta(days=365)]
                i.setdefault("currentPrice", float(s.iloc[-1]))
                i.setdefault("fiftyTwoWeekLow", float(j.min()))
                i.setdefault("fiftyTwoWeekHigh", float(j.max()))
                f["info"], f["teilweise"] = i, True
                U[t] = f
        leer = [t for t in fehlt if len(((U.get(t) or {}).get("info") or {})) <= 3 or (U.get(t) or {}).get("teilweise")]
        if leer:
            c1, c2 = st.columns([4, 1], vertical_alignment="center")
            c1.warning(f"Yahoo Finance hat für {len(leer)} Titel keine vollständigen Kennzahlen geliefert "
                       f"({', '.join(leer[:12])}{' …' if len(leer) > 12 else ''}). Meist drosselt Yahoo kurz – in ein paar Minuten "
                       "nochmals laden. Kurs und 52-Wochen-Spanne kommen notfalls aus den Tageskursen.", icon=":material/cloud_off:")
            if c2.button("Neu laden", icon=":material/refresh:", key="un_yahoo_btn", width="stretch"):
                ss["un_yahoo_neu"] = True
                st.rerun()
    for t, d in titel.items():
        i = (U.get(t) or {}).get("info") or {}
        d["name"] = d["name"] or i.get("shortName") or i.get("longName") or ""
        d["sektor"] = d["sektor"] or P.YAHOO_DE.get(str(i.get("sector") or ""), "")
    # Bilanz & Gewinnschätzungen: Sammel-Lauf liefert sie für mitverfolgte Titel, sonst auf Knopfdruck direkt
    if ss.pop("un_bil_alle", False):
        _bilanz_direkt([t for t in titel if not (U.get(t) or {}).get("fin")], neu=True)
    if (t_einzel := ss.pop("un_bil_einzel", None)):
        _bilanz_direkt([t_einzel], neu=True)
    for t in titel:
        b = _bilanz_aus_speicher(t)
        if b and not (U.get(t) or {}).get("fin"):
            U[t] = {**(U.get(t) or {}), **{k: v for k, v in b.items() if v}}
    pos = _position(tab, fred, lade_kurse)
    tr = team_ratings(tab.get("team_rating"))
    th = M.thesen(tab.get("thesen"))
    faelle = M.lade_faelle(tab.get("faelle"))
    offen = faelle[faelle["status"].isin(M.OFFEN)] if len(faelle) else faelle
    monat = SNS.monat_aktuell()
    grenze = N.iso(N.jetzt_utc() - pd.Timedelta(days=30))
    ms = []
    for mon in sorted({monat, N.monat_von(grenze)}):
        ms += [m for m in SNS.monatsdaten(ctx, mon)[0] if m["zeit"] >= grenze]

    n_her = {h: sum(1 for d in titel.values() if h in d["herkunft"]) for h in HERKUNFT_FARBE}
    ui.kopf_kpis([
        ("Titel", str(len(titel)), " · ".join(f"{h} {n}" for h, n in n_her.items())),
        ("Sektoren abgedeckt", f"{len({d['sektor'] for d in titel.values() if d['sektor']})} / 11", ""),
        ("Analysten-Konsens «Kaufen»", str(sum(1 for t in titel if str(((U.get(t) or {}).get("info") or {}).get("recommendationKey"))
                                               in ("buy", "strong_buy"))), "Titel mit Konsens Kaufen / Stark kaufen"),
        ("Daten", SNS._datum(min((U[t].get("stand") for t in titel if t in U), default=None)) or "–",
         "Yahoo Finance, 1× täglich · ältester Stand"),
    ])
    if fehler:
        st.warning(fehler, icon=":material/cloud_off:")
    if not titel:
        st.info("Noch keine Titel im Bestand oder auf der Watchlist. Oben «Titel aus der Analyse anzeigen» einschalten, um die "
                "Unternehmen einer Analyse anzusehen.")
        return

    # --- Übersicht ---------------------------------------------------------------
    zeilen = []
    for t, d in sorted(titel.items(), key=lambda x: (x[1]["sektor"] or "zz", x[0])):
        f = U.get(t) or {}
        k = N.analysten_kennzahlen(f, kurs=(pos.get(t) or {}).get("kurs"))
        bk = N.bilanz_kennzahlen(f, d["sektor"])
        fs = bk["f_score"]
        zeilen.append({"Sektor": d["sektor"] or "?", "Ticker": t, "Name": d["name"], "Herkunft": ", ".join(d["herkunft"]),
                       "Nettoversch./EBITDA": None if bk["nd_ebitda"] is None else round(bk["nd_ebitda"], 1),
                       "FCF-Rendite %": None if bk["fcf_rendite"] is None else round(bk["fcf_rendite"] * 100, 1),
                       "F-Score": f"{fs['score']}/{fs['n']}" if fs and fs["n"] else "",
                       "EPS-Rev. 30 T. %": None if bk["rev30_jahr"] is None else round(bk["rev30_jahr"], 1),
                       "Kurs": k["kurs"], "Konsens": N.KONSENS_DE.get(str(k["konsens"]), "–"), "Analysten": k["n"],
                       "Kursziel Ø": k["ziel"], "Potenzial %": round(k["upside"], 1) if k["upside"] is not None else None,
                       "Herabst. 30 T.": len(k["down"]), "Team": team_konsens(tr.get(t)),
                       "News 30 T.": sum(1 for m in ms if t in (m.get("ticker") or []) and m["eff"]["rel"] >= 3),
                       "Quartalszahlen": f.get("quartalszahlen", "")})
    df = pd.DataFrame(zeilen)
    BIL_SP = ["Nettoversch./EBITDA", "FCF-Rendite %", "F-Score", "EPS-Rev. 30 T. %"]
    df = df[[c for c in df.columns if c not in BIL_SP and c != "Quartalszahlen"] + BIL_SP + ["Quartalszahlen"]]
    R.h("Übersicht")
    R.tabelle(df.drop(columns=["Name"] + BIL_SP).assign(**{c: df[c].map(lambda x: "" if x is None or (isinstance(x, float) and pd.isna(x))
                                                                   else ui.de(x, 1)) for c in ("Kurs", "Kursziel Ø", "Potenzial %")}))
    sektoren = [s for s in C.SEKTOREN if any(d["sektor"] == s for d in titel.values())]
    if any(not d["sektor"] for d in titel.values()):
        sektoren.append("?")
    tabs = st.tabs(["Übersicht"] + [f"{s} ({sum(1 for d in titel.values() if (d['sektor'] or '?') == s)})" for s in sektoren])
    with tabs[0]:
        st.dataframe(df, hide_index=True, width="stretch",
                     column_config={"Kurs": st.column_config.NumberColumn(format="%.2f"),
                                    "Kursziel Ø": st.column_config.NumberColumn(format="%.2f"),
                                    "Potenzial %": st.column_config.NumberColumn(format="%+.1f"),
                                    "Nettoversch./EBITDA": st.column_config.NumberColumn(format="%.1f"),
                                    "FCF-Rendite %": st.column_config.NumberColumn(format="%.1f"),
                                    "EPS-Rev. 30 T. %": st.column_config.NumberColumn(format="%+.1f")})
        st.caption("Potenzial = mittleres Analysten-Kursziel gegenüber dem Kurs. Konsens und Kursziele: Yahoo Finance (Durchschnitt der "
                   "erfassten Analysten), ohne Gewähr. Team = Mehrheit eurer Ratings.")
        st.caption("Nettoverschuldung/EBITDA: unter 2 solide, über 4 hoch (bei Finanzen nicht aussagekräftig, leer) · "
                   "FCF-Rendite = Free Cashflow / Marktkapitalisierung · F-Score = Piotroski (Punkte / prüfbare Kriterien, "
                   "7–9 stark, 0–3 schwach) · EPS-Rev. 30 T. = Änderung der Gewinnschätzung fürs laufende Jahr in 30 Tagen.")
        ohne = [t for t in titel if not (U.get(t) or {}).get("fin")]
        if ohne:
            c1, c2 = st.columns([3.2, 1.3], vertical_alignment="center")
            c1.caption(f"Ohne Bilanzdaten: {', '.join(sorted(ohne)[:15])}{' …' if len(ohne) > 15 else ''} – Bestand, Watchlist und "
                       "mitverfolgte Titel kommen mit dem nächsten Sammel-Lauf, die übrigen auf Knopfdruck (ca. 2–3 Sek. pro Titel).")
            if c2.button(f"Bilanzdaten laden ({len(ohne)})", icon=":material/account_balance:", key="un_bil_alle_btn", width="stretch"):
                ss["un_bil_alle"] = True
                st.rerun()
    df_b = df[["Ticker"] + BIL_SP]
    df_b = df_b[df_b[BIL_SP].apply(lambda r: any(x not in (None, "") and not (isinstance(x, float) and pd.isna(x)) for x in r), axis=1)]
    if len(df_b):
        R.h2("Bilanz & Qualität")
        R.tabelle(df_b.assign(**{c: df_b[c].map(lambda x: "" if x is None or (isinstance(x, float) and pd.isna(x)) else ui.de(x, 1))
                                 for c in ("Nettoversch./EBITDA", "FCF-Rendite %", "EPS-Rev. 30 T. %")}))
    for s, tb in zip(sektoren, tabs[1:]):
        with tb:
            for t, d in sorted(((t, d) for t, d in titel.items() if (d["sektor"] or "?") == s), key=lambda x: x[0]):
                _titel_karte(ctx, t, d, U.get(t) or {}, pos.get(t), tr.get(t, {}), th.get(t), offen, ms, R)
    SP.knopf(ph, R, "unternehmen")


def _titel_karte(ctx, t: str, d: dict, f: dict, p: dict | None, tr: dict, these: dict | None, offen: pd.DataFrame,
                 ms: list[dict], R):
    i = f.get("info") or {}
    k = N.analysten_kennzahlen(f, kurs=(p or {}).get("kurs"))
    kopf = (f"{t} · {d['name'] or i.get('shortName', '')} · " + " / ".join(d["herkunft"]) + f" · Konsens "
            f"{N.KONSENS_DE.get(str(k['konsens']), '–')}" + (f" · Potenzial {ui.de(k['upside'], 1, True)} %" if k["upside"] is not None else "")
            + (f" · Team {team_konsens(tr)}" if tr else ""))
    R.h2(kopf)
    with st.expander(kopf, expanded=False):
        st.markdown(" ".join(_pill(h, HERKUNFT_FARBE.get(h, "#6B7685")) for h in d["herkunft"])
                    + (f"<span class='klein' style='color:#6B7685'>{html.escape(str(i.get('industry') or ''))}"
                       f"{' · ' + ui.de(i['fullTimeEmployees'], 0) + ' Mitarbeitende' if i.get('fullTimeEmployees') else ''}</span>"),
                    unsafe_allow_html=True)
        prof = f.get("profil_de") or (i.get("longBusinessSummary") or "")[:600]
        if prof:
            st.markdown(f"<div style='font-size:13.5px;margin:6px 0 10px'>{html.escape(prof)}"
                        + ("" if f.get("profil_de") else " …") + "</div>", unsafe_allow_html=True)
            st.caption("Profil: KI-Zusammenfassung der Yahoo-Beschreibung (einmalig erstellt)" if f.get("profil_de")
                       else "Profil: Yahoo-Beschreibung (englisch, gekürzt)")
            R.text(prof, klein=True)
        bk = N.bilanz_kennzahlen(f, d["sektor"])
        t1, t1b, t2, t3, t4 = st.tabs(["Kennzahlen", "Bilanz & Qualität", "Analysten", "News", "Team"])
        with t1b:
            _bilanz(t, d, f, bk, R)
        with t1:
            kurs = k["kurs"]
            ui.kopf_kpis([
                ("Kurs", f"{_z(kurs, 2)} USD", f"52 W.: {_z(i.get('fiftyTwoWeekLow'), 2)} – {_z(i.get('fiftyTwoWeekHigh'), 2)}"),
                ("Marktkapitalisierung", _mrd(i.get("marketCap")), f"Beta {_z(i.get('beta'), 2)}"),
                ("KGV aktuell / erwartet", f"{_z(i.get('trailingPE'))} / {_z(i.get('forwardPE'))}",
                 f"PEG {_z(i.get('pegRatio'), 2)} · KBV {_z(i.get('priceToBook'))}"),
                ("Dividendenrendite", _pct(i.get("dividendYield"), 2), f"Ausschüttung {_pct(i.get('payoutRatio'), 0)}"),
            ])
            ui.kopf_kpis([
                ("Umsatzwachstum", _pct(i.get("revenueGrowth"), 1, True), f"Umsatz {_mrd(i.get('totalRevenue'))} USD"),
                ("Gewinnwachstum", _pct(i.get("earningsGrowth"), 1, True), f"Quartal {_pct(i.get('earningsQuarterlyGrowth'), 1, True)}"),
                ("Marge operativ / netto", f"{_pct(i.get('operatingMargins'))} / {_pct(i.get('profitMargins'))}",
                 f"Bruttomarge {_pct(i.get('grossMargins'))}"),
                ("Eigenkapitalrendite", _pct(i.get("returnOnEquity")), f"Verschuldung D/E {_z(i.get('debtToEquity'), 0)}"),
            ])
            if p:
                ui.kopf_kpis([
                    ("Unser Gewicht", f"{ui.de(p['gewicht'], 2)} %" if p.get("gewicht") is not None else "–",
                     f"{ui.de(p['menge'], 0)} Stück · {ui.de(p['wert'], 0)} USD"),
                    ("Seit Kauf", f"{ui.de(p['rendite'], 1, True)} %" if p.get("rendite") is not None else "–",
                     f"Einstand {_z(p.get('einstand'), 2)} USD · {pd.Timestamp(p['kaufdatum']):%d.%m.%Y}" if pd.notna(p.get("kaufdatum")) else ""),
                    (f"Sektor-ETF {p.get('etf') or ''}", f"{ui.de(p['r_etf'], 1, True)} %" if p.get("r_etf") is not None else "–",
                     "im gleichen Zeitraum"),
                    ("Relativ zum Sektor", f"{ui.de(p['rendite'] - p['r_etf'], 1, True)} Pp."
                     if p.get("rendite") is not None and p.get("r_etf") is not None else "–", ""),
                ])
            st.caption(f"Nächste Quartalszahlen: {f.get('quartalszahlen') or '–'} · Stand {SNS._datum(f.get('stand')) or '–'} · "
                       "Quelle Yahoo Finance"
                       + (" · nur Kursdaten (Yahoo hat die übrigen Kennzahlen gerade nicht geliefert – oben «Neu laden»)"
                          if f.get("teilweise") else "")
                       + (f" · Hinweise: {', '.join(f.get('fehler') or [])}" if f.get("fehler") else ""))
            R.kpis([("Kurs", f"{_z(kurs, 2)} USD", ""), ("KGV erw.", _z(i.get("forwardPE")), ""),
                    ("Umsatzwachstum", _pct(i.get("revenueGrowth"), 1, True), ""), ("Div.-Rendite", _pct(i.get("dividendYield"), 2), "")])
        with t2:
            _analysten(t, f, k, R)
            _schaetzungen(t, f, bk, R)
        with t3:
            relevant = N.top(ms, ticker=t, n=15, min_rel=2)
            if (f.get("news") or {}).get("text"):
                st.markdown(f"<div style='background:#F3F6FB;border-left:3px solid #1F4E8C;padding:8px 12px;border-radius:4px;"
                            f"font-size:13.5px'>{html.escape(f['news']['text'])}<div class='klein' style='color:#6B7685;margin-top:4px'>"
                            f"Kurzfassung KI · Stand {SNS._datum(f['news']['zeit'])} · nur neu, wenn relevante Meldungen dazukommen"
                            f"</div></div>", unsafe_allow_html=True)
                R.text("News: " + f["news"]["text"], klein=True)
            if not relevant:
                st.caption("Keine Meldungen in den letzten 30 Tagen.")
            st.markdown("".join(SNS.meldung_html(m) for m in relevant), unsafe_allow_html=True)
            SNS.bewertung_formular(ctx, relevant, f"u_{t}")
        with t4:
            _team(ctx, t, tr, these, offen)


def _analysten(t: str, f: dict, k: dict, R):
    v = k["verteilung"]
    c1, c2 = st.columns([1.1, 1])
    with c1:
        st.markdown(f"**Konsens: {N.KONSENS_DE.get(str(k['konsens']), '–')}**" + (f" · Ø {ui.de(k['mean'], 2)} (1 = Stark kaufen, 5 = Verkaufen)"
                                                                               if k.get("mean") else "")
                    + (f" · {k['n']} Analysten" if k.get("n") else ""))
        if v and sum(v.values()):
            namen = ["Stark kaufen", "Kaufen", "Halten", "Verkaufen", "Stark verkaufen"]
            farben = ["#1E6B45", "#2F855A", "#B7791F", "#C05621", "#B93A32"]
            fig = go.Figure()
            for n, key, fa in zip(namen, ("strongBuy", "buy", "hold", "sell", "strongSell"), farben):
                fig.add_trace(go.Bar(y=["heute"], x=[v.get(key, 0)], name=n, orientation="h", marker_color=fa, text=[v.get(key, 0) or ""],
                                     textposition="inside"))
            fig.update_layout(barmode="stack", height=120, margin=dict(l=8, r=8, t=4, b=4), showlegend=True,
                              legend=dict(orientation="h", y=-0.5), plot_bgcolor="white")
            fig.update_xaxes(visible=False)
            ui.zeige(fig, key=f"un_konsens_{t}")
        emp = f.get("empfehlungen") or []
        if len(emp) > 1:
            st.dataframe(pd.DataFrame([{"Monat": {"0m": "aktuell", "-1m": "vor 1 Mt.", "-2m": "vor 2 Mt.", "-3m": "vor 3 Mt."}.get(
                str(e.get("period")), str(e.get("period"))), "Stark kaufen": e.get("strongBuy"), "Kaufen": e.get("buy"),
                "Halten": e.get("hold"), "Verkaufen": e.get("sell"), "Stark verkaufen": e.get("strongSell")} for e in emp]),
                hide_index=True, width="stretch")
    with c2:
        i = f.get("info") or {}
        st.markdown("**Kursziele**")
        if k["ziel"]:
            punkte = [("Tief", i.get("targetLowPrice")), ("Ø", i.get("targetMeanPrice")), ("Median", i.get("targetMedianPrice")),
                      ("Hoch", i.get("targetHighPrice"))]
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=[x for _, x in punkte if x], y=[0] * sum(1 for _, x in punkte if x), mode="markers+text",
                                     text=[n for n, x in punkte if x], textposition="top center",
                                     marker=dict(size=11, color="#1F4E8C"), name="Kursziele"))
            if k["kurs"]:
                fig.add_trace(go.Scatter(x=[k["kurs"]], y=[0], mode="markers+text", text=["Kurs"], textposition="bottom center",
                                         marker=dict(size=13, color="#B7791F", symbol="diamond"), name="Kurs"))
            fig.update_layout(height=140, margin=dict(l=8, r=8, t=24, b=24), showlegend=False, plot_bgcolor="white")
            fig.update_yaxes(visible=False, range=[-1, 1])
            ui.zeige(fig, key=f"un_kz_{t}")
            st.caption(f"Ø {ui.de(k['ziel'], 2)} USD · Potenzial {ui.de(k['upside'], 1, True)} %" if k["upside"] is not None else "")
            R.text(f"Analysten: {N.KONSENS_DE.get(str(k['konsens']), '–')}, {k['n'] or '?'} Analysten, Kursziel Ø {ui.de(k['ziel'], 2)} USD"
                   + (f", Potenzial {ui.de(k['upside'], 1, True)} %" if k["upside"] is not None else "") +
                   f", {len(k['down'])} Herab-/{len(k['up'])} Heraufstufungen in 30 Tagen", klein=True)
        else:
            st.caption("Keine Kursziele vorhanden.")
    ra = f.get("ratings") or []
    if ra:
        st.markdown("**Letzte Up- und Downgrades** (120 Tage)")
        akt = {"up": "▲ Heraufstufung", "down": "▼ Herabstufung", "main": "bestätigt", "reit": "bestätigt", "init": "neu abgedeckt"}
        st.dataframe(pd.DataFrame([{"Datum": f"{r['datum'][8:10]}.{r['datum'][5:7]}.{r['datum'][:4]}", "Haus": r["firma"],
                                    "Aktion": akt.get(r["aktion"].lower(), r["aktion"]), "Von": r["von"], "Zu": r["zu"],
                                    "Kursziel": (f"{ui.de(r['kz_alt'], 0)} → {ui.de(r['kz_neu'], 0)}" if r.get("kz_alt") and r.get("kz_neu")
                                                 else ui.de(r["kz_neu"], 0) if r.get("kz_neu") else "")} for r in ra[:12]]),
                     hide_index=True, width="stretch")
    if len(k["down"]) >= 2:
        st.warning(f"{len(k['down'])} Herabstufungen in den letzten 30 Tagen – im Monitoring als «Prüfen» gemeldet (für Titel im Bestand).",
                   icon=":material/trending_down:")


def _x(x, d=1, suffix="", vz=False):
    return "–" if x is None or (isinstance(x, float) and pd.isna(x)) else f"{ui.de(x, d, vz)}{suffix}"


def _div(a, b):
    return a / b if a is not None and b not in (None, 0) else None


def _pz(x, d=1, vz=False):
    """Anteil (0,05) → «5,0 %»."""
    return _x(x * 100 if x is not None else None, d, " %", vz)


def _laden_knopf(t: str, text: str, key: str):
    if st.button(text, key=key, icon=":material/account_balance:"):
        st.session_state["un_bil_einzel"] = t
        st.rerun()


def _bilanz(t: str, d: dict, f: dict, bk: dict, R):
    """Reiter «Bilanz & Qualität»: Verschuldung, Cashflow, F-Score, 4-Jahres-Verlauf."""
    if bk["hinweis"]:
        st.info(bk["hinweis"], icon=":material/info:")
    if not bk["hat_abschluss"]:
        st.caption("Noch keine Jahresabschlüsse geladen – die Werte unten stammen, soweit vorhanden, aus den Yahoo-Kennzahlen.")
        _laden_knopf(t, "Bilanzdaten laden", f"un_bil_{t}")
    if bk["finanz"]:
        ui.kopf_kpis([
            ("Kurs-Buchwert (KBV)", _x(bk["kbv"], 2), "unter 1 = Börse bewertet unter Buchwert"),
            ("Eigenkapitalrendite", _pz(bk["roe"]), "Gewinn / Eigenkapital"),
            ("Eigenkapitalquote", _pz(bk["ek_quote"]), "Eigenkapital / Bilanzsumme (Banken typisch 8–12 %)"),
            ("Cash & kurzfr. Anlagen", f"{_mrd(bk['cash'])} USD", ""),
        ])
    else:
        nd, netto = bk["nd_ebitda"], bk["netto"]
        ui.kopf_kpis([
            ("Nettoverschuldung / EBITDA", "Nettocash" if netto is not None and netto < 0 else _x(nd, 1, "×"),
             f"Nettoverschuldung {_mrd(netto)} USD · unter 2× solide, über 4× hoch"),
            ("Zinsdeckung", _x(bk["zinsdeckung"], 1, "×"), "EBIT / Zinsaufwand · über 5× komfortabel, unter 2× knapp"),
            ("Current Ratio", _x(bk["current_ratio"], 2), "Umlaufvermögen / kurzfr. Schulden · über 1 = gedeckt"),
            ("Eigenkapitalquote", _pz(bk["ek_quote"]), f"Cash & kurzfr. Anlagen {_mrd(bk['cash'])} USD"),
        ])
    fs = bk["f_score"]
    ui.kopf_kpis([
        ("Free Cashflow", f"{_mrd(bk['fcf'])} USD", "operativer Cashflow − Investitionen, letztes Geschäftsjahr"),
        ("FCF-Rendite", _pz(bk["fcf_rendite"]), "Free Cashflow / Marktkapitalisierung · Vergleich: Gewinnrendite = 1 / KGV"),
        ("Cash Conversion", _pz(bk["cash_conversion"], 0), "operativer Cashflow / Gewinn · über 100 % = Gewinn kommt als Cash an"),
        ("Piotroski F-Score", f"{fs['score']} / {fs['n']}" if fs and fs["n"] else "–",
         ("7–9 stark · 4–6 mittel · 0–3 schwach" + (f" · nur {fs['n']} von 9 prüfbar" if fs["n"] < 9 else ""))
         if fs and fs["n"] else "braucht zwei Jahresabschlüsse"),
    ])
    R.kpis([("Nettoversch./EBITDA", "n. a." if bk["finanz"] else _x(bk["nd_ebitda"], 1, "×"), ""),
            ("FCF-Rendite", _pz(bk["fcf_rendite"]), ""),
            ("F-Score", f"{fs['score']}/{fs['n']}" if fs and fs["n"] else "–", ""),
            ("EPS-Rev. 30 T.", _x(bk["rev30_jahr"], 1, " %", True), "")])
    if bk["hinweis"]:
        R.text("Hinweis: " + bk["hinweis"], klein=True)
    if fs and fs["n"]:
        with st.popover("F-Score im Detail", icon=":material/checklist:"):
            j = fs["jahre"]
            if len(j) > 1:
                st.caption(f"Geschäftsjahr {j[0][:4]} gegenüber {j[1][:4]}")
            st.dataframe(pd.DataFrame([{"Bereich": b, "Kriterium": n, "Erfüllt": "✓" if x else ("✗" if x is False else "– nicht prüfbar")}
                                       for b, n, x in fs["kriterien"]]), hide_index=True, width="stretch")
            st.caption("Piotroski (2000): 9 einfache Kriterien zu Rentabilität, Finanzierung und Effizienz, je 1 Punkt. ROA hier "
                       "mit der Bilanzsumme am Jahresende gerechnet (vereinfacht).")
    j, v = bk["jahre"], bk["verlauf"]
    if len(j) >= 2 and (v.get("umsatz") or v.get("eps")):
        jl = [x[:4] for x in j][::-1]
        leer = [None] * len(j)
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Umsatz und operative Marge**")
            fig = go.Figure()
            ums = (v.get("umsatz") or leer)[::-1]
            fig.add_trace(go.Bar(x=jl, y=[x / 1e9 if x is not None else None for x in ums], name="Umsatz (Mrd. USD)",
                                 marker_color=ui.STAHL))
            marge = [_div(e, u) * 100 if _div(e, u) is not None else None for e, u in zip((v.get("ebit") or leer)[::-1], ums)]
            if any(m is not None for m in marge) and not bk["finanz"]:
                fig.add_trace(go.Scatter(x=jl, y=marge, name="Operative Marge %", yaxis="y2", mode="lines+markers",
                                         line=dict(color=ui.BLAU, width=2)))
            fig.update_layout(height=240, margin=dict(l=8, r=8, t=10, b=8), legend=dict(orientation="h", y=-0.25), plot_bgcolor="white",
                              yaxis2=dict(overlaying="y", side="right", ticksuffix=" %", showgrid=False))
            ui.zeige(fig, key=f"un_ums_{t}")
        with c2:
            st.markdown("**Gewinn je Aktie und Free Cashflow**")
            fig = go.Figure()
            if v.get("fcf"):
                fig.add_trace(go.Bar(x=jl, y=[x / 1e9 if x is not None else None for x in v["fcf"][::-1]], name="Free Cashflow (Mrd. USD)",
                                     marker_color=ui.PETROL, opacity=0.55, yaxis="y2"))
            if v.get("eps"):
                fig.add_trace(go.Scatter(x=jl, y=v["eps"][::-1], name="Gewinn je Aktie (USD)", mode="lines+markers",
                                         line=dict(color=ui.BLAU, width=2)))
            fig.update_layout(height=240, margin=dict(l=8, r=8, t=10, b=8), legend=dict(orientation="h", y=-0.25), plot_bgcolor="white",
                              yaxis2=dict(overlaying="y", side="right", showgrid=False))
            ui.zeige(fig, key=f"un_eps_{t}")
    stand = f.get("abschluss_stand") or f.get("bilanz_stand")
    st.caption("Quelle: Yahoo Finance" + (f", Geschäftsjahre {', '.join(x[:4] for x in j)}" if j else "")
               + (f" · Stand {SNS._datum(stand)}" if stand else "")
               + (f" · Hinweise: {', '.join(f.get('bilanz_fehler') or [])}" if f.get("bilanz_fehler") else ""))


PERIODEN = {"0q": "Laufendes Quartal", "+1q": "Nächstes Quartal", "0y": "Laufendes Jahr", "+1y": "Nächstes Jahr"}


def _schaetzungen(t: str, f: dict, bk: dict, R):
    """Im Reiter «Analysten»: Gewinnschätzungen (Revisionen) und Überraschungen der letzten Quartale."""
    st.markdown("**Gewinnschätzungen** · wie haben die Analysten ihre Schätzung für den Gewinn je Aktie angepasst?")
    sc = bk["schaetzungen"]
    if not sc:
        st.caption("Keine Schätzungsdaten geladen.")
        _laden_knopf(t, "Schätzungen und Bilanzdaten laden", f"un_sch_{t}")
    else:
        zeilen = []
        for p in ("0q", "+1q", "0y", "+1y"):
            z = sc.get(p)
            if not z:
                continue
            zeilen.append({"Periode": PERIODEN[p], "EPS-Schätzung heute": _x(z.get("heute"), 2),
                           "vor 30 T.": _x(z.get("d30"), 2), "vor 90 T.": _x(z.get("d90"), 2),
                           "Änderung 30 T.": _x(z.get("p30"), 1, " %", True), "Änderung 90 T.": _x(z.get("p90"), 1, " %", True),
                           "Analysten ↑ / ↓ (30 T.)": f"{_x(z.get('hoch30'), 0)} / {_x(z.get('runter30'), 0)}"})
        st.dataframe(pd.DataFrame(zeilen), hide_index=True, width="stretch")
        r = bk["rev30_jahr"]
        if r is not None and abs(r) >= 2:
            (st.success if r > 0 else st.warning)(
                f"Die Gewinnschätzung fürs laufende Jahr wurde in 30 Tagen um {ui.de(r, 1, True)} % "
                f"{'angehoben' if r > 0 else 'gesenkt'} – {'positives' if r > 0 else 'negatives'} Momentum-Signal.",
                icon=":material/trending_up:" if r > 0 else ":material/trending_down:")
    ue = bk["ueberraschungen"]
    if ue:
        st.markdown(f"**Gewinnüberraschungen** · {bk['beats']} von {len(ue)} Quartalen auf oder über der Erwartung")
        st.dataframe(pd.DataFrame([{"Quartal": f"{u['quartal'][5:7]}.{u['quartal'][:4]}", "Erwartet (EPS)": _x(u["schaetzung"], 2),
                                    "Ist (EPS)": _x(u["ist"], 2),
                                    "Überraschung": _x(_ueb_prozent(u), 1, " %", True)} for u in ue]),
                     hide_index=True, width="stretch")
    if sc or ue:
        st.caption("Quelle: Yahoo Finance (Konsens der erfassten Analysten). Steigende Schätzungen und wiederholte positive "
                   "Überraschungen gehen oft mit überdurchschnittlicher Kursentwicklung in den Folgemonaten einher – kein Automatismus.")
        R.text(f"Gewinnschätzungen: laufendes Jahr {_x(bk['rev30_jahr'], 1, ' %', True)} in 30 Tagen"
               + (f", {bk['beats']} von {len(ue)} Quartalen auf/über Erwartung" if ue else ""), klein=True)


def _ueb_prozent(u: dict):
    """Überraschung in %: Yahoo liefert surprisePercent je nach Version als Anteil (0,05) oder in Prozent (5,0)."""
    if u.get("schaetzung") not in (None, 0) and u.get("ist") is not None:
        return (u["ist"] - u["schaetzung"]) / abs(u["schaetzung"]) * 100
    p = u.get("prozent")
    return None if p is None else (p * 100 if abs(p) < 2 else p)


def _team(ctx, t: str, tr: dict, these: dict | None, offen: pd.DataFrame):
    c1, c2 = st.columns([1.1, 1])
    with c1:
        st.markdown("**Team-Rating**")
        if tr:
            for p, r in sorted(tr.items()):
                st.markdown(f"{_pill(r['rating'], RATING_FARBE.get(r['rating'], '#6B7685'))} <b>{html.escape(p)}</b> "
                            f"<span class='klein' style='color:#6B7685'>{SNS._datum(r['zeit'])}</span><br>"
                            f"<span class='klein'>{html.escape(r['begruendung'])}</span>", unsafe_allow_html=True)
            st.caption(f"Mehrheit: {team_konsens(tr)}")
        else:
            st.caption("Noch keine Ratings.")
        if not ctx.nur_lesen:
            eigen = tr.get(ctx.person, {})
            with st.form(f"un_tr_{t}", border=True):
                r = st.radio("Mein Rating", RATINGS, horizontal=True,
                             index=RATINGS.index(eigen["rating"]) if eigen.get("rating") in RATINGS else 1)
                b = st.text_area("Begründung", value=eigen.get("begruendung", ""), height=80,
                                 placeholder="z. B. Bewertung, Gewinnrevisionen, Rolle im Sektor, Abweichung vom Analysten-Konsens")
                if st.form_submit_button("Rating speichern", type="primary", icon=":material/how_to_vote:"):
                    if len(b.strip()) < 10:
                        st.error("Bitte kurz begründen (mindestens ein Satz).")
                    else:
                        ctx.speichern("team_rating", [dict(zeit=jetzt(), person=ctx.person, ticker=t, rating=r, begruendung=b.strip())],
                                      f"Rating {t} gespeichert.")
    with c2:
        st.markdown("**Kauf-These**")
        if these and (these.get("these") or these.get("kursziel") or these.get("stop")):
            st.markdown(f"<div class='klein'>{html.escape(these.get('these') or '–')}<br>Kursziel {_z(these.get('kursziel'), 2)} · "
                        f"Stop {_z(these.get('stop'), 2)} · überprüfen {these.get('ueberpruefung') or '–'} "
                        f"<span style='color:#6B7685'>({html.escape(str(these.get('person', '')))})</span></div>", unsafe_allow_html=True)
        else:
            st.caption("Keine Kauf-These erfasst (Seite «Monitoring» → Kauf-Thesen & Watchlist).")
        st.markdown("**Offene Monitoring-Fälle**")
        of = offen[offen["ticker"].astype(str).str.upper() == t] if len(offen) else offen
        if len(of):
            for _, z in of.iterrows():
                st.markdown(f"<div class='klein'><b style='color:{M.STUFE_FARBE.get(z['stufe'], '#556070')}'>{html.escape(str(z['stufe']))}</b> "
                            f"{html.escape(str(z['titel']))}</div>", unsafe_allow_html=True)
        else:
            st.caption("Keine.")
