"""Seite «Unternehmen» (unter Portfolio): für jeden Titel aus Bestand, Watchlist und Probe-Portfolio automatisch ein Profil –
nach Sektoren gegliedert. Profil, Kennzahlen, unsere Position, Analysten (Konsens, Kursziele, Up-/Downgrades), News,
Kauf-These, offene Monitoring-Fälle und Team-Rating.

Daten: Yahoo Finance (ohne KI), 1× täglich vom Sammel-Lauf aktualisiert (verschlüsselt im Branch «daten»); fehlt ein Titel
dort noch (z. B. gerade erst auf die Watchlist gesetzt), lädt die Seite ihn direkt von Yahoo.
"""
from __future__ import annotations

import html

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
HERKUNFT_FARBE = {"Bestand": "#14325C", "Watchlist": "#5B7DB1", "Probe-Portfolio": "#8E5572"}


@st.cache_data(ttl=12 * 3600, show_spinner="Lade Unternehmensdaten von Yahoo …")
def _yahoo_direkt(ticker: tuple[str, ...]) -> dict:
    return N.firmen_yahoo(list(ticker))


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
    ui.seitenkopf("Unternehmen", "Für jeden Titel aus Bestand, Watchlist und Probe-Portfolio: Profil, Kennzahlen, Analysten-"
                                 "Einschätzung, News und eure Einschätzung – nach Sektoren gegliedert.")
    ph = st.empty()
    tab = ctx.tabellen
    import portfolio as P

    # Probe-Portfolio wählen
    an = tab.get("analysen")
    an = an.sort_values("zeit", ascending=False).drop_duplicates("id") if an is not None and not an.empty else pd.DataFrame()
    aid = N.probe_analyse_id(tab)
    titel = N.titel_liste(tab, P.sp500_liste())
    U, fehler = SNS.lade("unternehmen.json")
    U = dict(U or {})
    fehlt = tuple(sorted(t for t in titel if t not in U or not (U[t].get("info"))))
    if fehlt:
        U.update({t: f for t, f in _yahoo_direkt(fehlt).items() if f.get("info")})
    for t, d in titel.items():
        i = (U.get(t) or {}).get("info") or {}
        d["name"] = d["name"] or i.get("shortName") or i.get("longName") or ""
        d["sektor"] = d["sektor"] or P.YAHOO_DE.get(str(i.get("sector") or ""), "")
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
    with st.expander("Welche Titel? (Bestand, Watchlist, Probe-Portfolio)", icon=":material/tune:"):
        st.caption("Bestand = Transaktionen auf der Seite «Portfolio» · Watchlist = Seite «Monitoring» → Kauf-Thesen & Watchlist · "
                   "Probe-Portfolio = eine gespeicherte Vorauswahl der Seite «Analyse» (Standard: die zuletzt gespeicherte).")
        if len(an):
            optionen = list(an["id"])
            namen = {r["id"]: f"{r['name']} ({r['person']}, {str(r['zeit'])[:10]})" for _, r in an.iterrows()}
            c1, c2 = st.columns([3, 1])
            w = c1.selectbox("Probe-Portfolio", optionen, index=optionen.index(aid) if aid in optionen else 0,
                             format_func=namen.get, key="un_probe")
            if c2.button("Übernehmen", key="un_probe_b", disabled=ctx.nur_lesen or w == aid, width="stretch"):
                ctx.speichern("einstellungen", [dict(zeit=jetzt(), person=ctx.person, schluessel="unternehmen|analyse_id", wert=w)],
                              "Probe-Portfolio für die Seite «Unternehmen» gewählt.")
        else:
            st.caption("Noch keine gespeicherte Vorauswahl in der Analyse.")
    if not titel:
        st.info("Noch keine Titel: Vorauswahl in der «Analyse» speichern, Titel auf die Watchlist setzen oder Transaktionen erfassen.")
        return

    # --- Übersicht ---------------------------------------------------------------
    zeilen = []
    for t, d in sorted(titel.items(), key=lambda x: (x[1]["sektor"] or "zz", x[0])):
        f = U.get(t) or {}
        k = N.analysten_kennzahlen(f, kurs=(pos.get(t) or {}).get("kurs"))
        zeilen.append({"Sektor": d["sektor"] or "?", "Ticker": t, "Name": d["name"], "Herkunft": ", ".join(d["herkunft"]),
                       "Kurs": k["kurs"], "Konsens": N.KONSENS_DE.get(str(k["konsens"]), "–"), "Analysten": k["n"],
                       "Kursziel Ø": k["ziel"], "Potenzial %": round(k["upside"], 1) if k["upside"] is not None else None,
                       "Herabst. 30 T.": len(k["down"]), "Team": team_konsens(tr.get(t)),
                       "News 30 T.": sum(1 for m in ms if t in (m.get("ticker") or []) and m["eff"]["rel"] >= 3),
                       "Quartalszahlen": f.get("quartalszahlen", "")})
    df = pd.DataFrame(zeilen)
    R.h("Übersicht")
    R.tabelle(df.drop(columns=["Name"]).assign(**{c: df[c].map(lambda x: "" if x is None or (isinstance(x, float) and pd.isna(x))
                                                                   else ui.de(x, 1)) for c in ("Kurs", "Kursziel Ø", "Potenzial %")}))
    sektoren = [s for s in C.SEKTOREN if any(d["sektor"] == s for d in titel.values())]
    if any(not d["sektor"] for d in titel.values()):
        sektoren.append("?")
    tabs = st.tabs(["Übersicht"] + [f"{s} ({sum(1 for d in titel.values() if (d['sektor'] or '?') == s)})" for s in sektoren])
    with tabs[0]:
        st.dataframe(df, hide_index=True, width="stretch",
                     column_config={"Kurs": st.column_config.NumberColumn(format="%.2f"),
                                    "Kursziel Ø": st.column_config.NumberColumn(format="%.2f"),
                                    "Potenzial %": st.column_config.NumberColumn(format="%+.1f")})
        st.caption("Potenzial = mittleres Analysten-Kursziel gegenüber dem Kurs. Konsens und Kursziele: Yahoo Finance (Durchschnitt der "
                   "erfassten Analysten), ohne Gewähr. Team = Mehrheit eurer Ratings.")
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
        t1, t2, t3, t4 = st.tabs(["Kennzahlen", "Analysten", "News", "Team"])
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
            st.caption(f"Nächste Quartalszahlen: {f.get('quartalszahlen') or '–'} · Stand {SNS._datum(f.get('stand'))} · Quelle Yahoo Finance"
                       + (f" · Hinweise: {', '.join(f.get('fehler') or [])}" if f.get("fehler") else ""))
            R.kpis([("Kurs", f"{_z(kurs, 2)} USD", ""), ("KGV erw.", _z(i.get("forwardPE")), ""),
                    ("Umsatzwachstum", _pct(i.get("revenueGrowth"), 1, True), ""), ("Div.-Rendite", _pct(i.get("dividendYield"), 2), "")])
        with t2:
            _analysten(t, f, k, R)
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
