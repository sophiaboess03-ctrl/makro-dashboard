"""Entscheidungsprotokoll: jeder Teamentscheid mit Begründung, zwei Quellen, Gegenargument und automatischem Snapshot."""
from __future__ import annotations

import html
import json
import uuid

import pandas as pd
import streamlit as st

from makro import config as C
from makro import ui
from storage import jetzt

KATEGORIEN = ["Asset-Allokation", "Rebalancing", "Duration / Treasuries", "Sektor", "Einzeltitel", "Szenario / These", "Risiko / Absicherung", "Sonstiges"]
STATUS = ["Vorgeschlagen", "Beschlossen", "Umgesetzt", "Verworfen"]
STATUS_FARBE = {"Vorgeschlagen": "#B7791F", "Beschlossen": "#1F4E8C", "Umgesetzt": "#2F855A", "Verworfen": "#6B7685"}


def snapshot(ctx) -> dict:
    a = ctx.alloc
    rote = [r["name"] for _, r in ctx.status.iterrows() if r["ampel"] == "Rot"]
    return {
        "stand": jetzt(),
        "empfehlung": f"{ui.de(a['aktien'], 3)} % Aktien / {ui.de(a['anleihen'], 3)} % Treasuries / {ui.de(a['cash'], 3)} % Cash, "
                      f"Duration {a['duration']:.1f} J., TIPS {a['tips']:.0f} %",
        "rote_indikatoren": rote,
        "szenarien_ic": {r["name"]: round(r["p"]) for _, r in ctx.szen[ctx.szen["thema"] == "gesamt"].iterrows()},
        "zyklus": ctx.zyklus_modell["phase"],
        "kennzahlen": {i: ctx.status.loc[i, "anzeige"] for i in ("dgs10", "kernpce", "unrate", "brent", "dxy", "vix", "erp")
                       if i in ctx.status.index},
    }


def liste(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["id", "datum", "kategorie", "titel", "entscheid", "begruendung", "quelle1", "quelle2",
                                     "gegenargument", "beteiligte", "status", "snapshot", "person", "zeit"])
    return df.sort_values("zeit").drop_duplicates("id", keep="last").sort_values(["datum", "zeit"], ascending=False)


def seite_entscheide(ctx, team: list[str]):
    ui.seitenkopf("Entscheidungsprotokoll", "Jeder Teamentscheid mit Begründung, zwei Quellen und Gegenargument. Beim Speichern hält die App automatisch "
               "fest, was das Dashboard in diesem Moment zeigte (Taktik, rote Indikatoren, Szenarien) – so bleibt "
               "nachvollziehbar, auf welcher Datenlage entschieden wurde.")
    import seitenpdf as SP
    ph = st.empty()
    d = liste(ctx.tabellen.get("entscheide"))
    R = SP.Sammler("Entscheidungsprotokoll", "Teamentscheide mit Begründung, Quellen, Gegenargument und Datenlage", kurz="Entscheide")
    for _, r in d.iterrows():
        R.h2(f"{r['datum']} - {r['titel']} ({r['status']}, {r['kategorie']})")
        R.text(f"Entscheid: {r['entscheid']}")
        R.text(f"Begründung: {r['begruendung']}")
        R.text(f"Gegenargument: {r['gegenargument'] or '-'} - Quellen: {r['quelle1']}; {r['quelle2']} - Beteiligte: {r['beteiligte'] or '-'}",
               klein=True)
    SP.knopf(ph, R if len(d) else None, "entscheide")

    c = st.columns(4)
    for col, s in zip(c, STATUS):
        col.metric(s, int((d["status"] == s).sum()) if len(d) else 0)

    with st.expander("Neuen Entscheid erfassen", icon=":material/add:", expanded=not len(d)):
        with st.form("entscheid", clear_on_submit=True):
            c1, c2, c3 = st.columns([1, 1.3, 1])
            datum = c1.date_input("Datum", value=pd.Timestamp.today().date(), format="DD.MM.YYYY")
            kat = c2.selectbox("Kategorie", KATEGORIEN)
            status = c3.selectbox("Status", STATUS, index=1)
            titel = st.text_input("Titel", placeholder="z. B. Aktienquote 45 %, Duration 3,5 J.")
            entscheid = st.text_area("Entscheid (was genau?)", height=70)
            begr = st.text_area("Begründung (2–3 Datenpunkte)", height=90)
            c1, c2 = st.columns(2)
            q1 = c1.text_input("Quelle 1")
            q2 = c2.text_input("Quelle 2")
            gegen = st.text_area("Gegenargument / Risiko", height=60)
            beteiligte = st.multiselect("Beteiligte", team or [ctx.person], default=[ctx.person] if ctx.person in (team or [ctx.person]) else [])
            if st.form_submit_button("Entscheid speichern", type="primary"):
                fehlt = [n for n, v in (("Titel", titel), ("Entscheid", entscheid), ("Begründung", begr), ("Quelle 1", q1),
                                        ("Quelle 2", q2)) if not v.strip()]
                if fehlt:
                    st.error("Bitte ausfüllen: " + ", ".join(fehlt) + " (eure Vorgabe: jede Information mit 2 Quellen).")
                else:
                    ctx.speichern("entscheide", [dict(zeit=jetzt(), person=ctx.person, id=uuid.uuid4().hex[:10],
                                                      datum=pd.Timestamp(datum).date().isoformat(), kategorie=kat,
                                                      titel=titel.strip(), entscheid=entscheid.strip(), begruendung=begr.strip(),
                                                      quelle1=q1.strip(), quelle2=q2.strip(), gegenargument=gegen.strip(),
                                                      beteiligte=", ".join(beteiligte), status=status,
                                                      snapshot=json.dumps(snapshot(ctx), ensure_ascii=False))],
                                  "Entscheid gespeichert.")

    if not len(d):
        st.info("Noch keine Entscheide erfasst.")
        return
    c1, c2 = st.columns(2)
    f_kat = c1.multiselect("Kategorie filtern", KATEGORIEN)
    f_st = c2.multiselect("Status filtern", STATUS)
    if f_kat:
        d = d[d["kategorie"].isin(f_kat)]
    if f_st:
        d = d[d["status"].isin(f_st)]
    for _, r in d.iterrows():
        farbe = STATUS_FARBE.get(r["status"], "#6B7685")
        with st.expander(f"{r['datum']} · {r['titel']} · {r['status']}"):
            st.markdown(f"<span class='badge' style='color:{farbe}; background:#fff; border-color:#E3E6EB'>{html.escape(r['status'])}</span> "
                        f"<span class='klein'>{html.escape(r['kategorie'])} · erfasst von {html.escape(r['person'])} · "
                        f"Beteiligte: {html.escape(r['beteiligte'] or '–')}</span>", unsafe_allow_html=True)
            st.markdown(f"**Entscheid:** {html.escape(r['entscheid'])}  \n**Begründung:** {html.escape(r['begruendung'])}  \n"
                        f"**Gegenargument / Risiko:** {html.escape(r['gegenargument'] or '–')}  \n"
                        f"**Quellen:** {html.escape(r['quelle1'])}; {html.escape(r['quelle2'])}")
            try:
                sn = json.loads(r["snapshot"])
                kz = " · ".join(C.IND_BY_ID[k]["name"].split(" (")[0] + " " + str(v) for k, v in sn["kennzahlen"].items())
                sz = ", ".join(f"{k}: {v} %" for k, v in sn["szenarien_ic"].items())
                st.markdown(f"<div class='klein' style='background:#F5F6F8; padding:8px; border-radius:6px'>"
                            f"<b>Datenlage beim Entscheid ({sn['stand'][:10]}):</b> Taktik {html.escape(sn['empfehlung'])} · "
                            f"Zyklus {html.escape(sn['zyklus'])} · Szenarien {html.escape(sz)}<br>"
                            f"Kennzahlen: {html.escape(kz)}<br>"
                            f"Rote Indikatoren: {html.escape(', '.join(sn['rote_indikatoren']) or 'keine')}</div>",
                            unsafe_allow_html=True)
            except Exception:  # noqa: BLE001
                pass
            neu = st.selectbox("Status ändern", STATUS, index=STATUS.index(r["status"]) if r["status"] in STATUS else 0,
                               key=f"stat_{r['id']}")
            if neu != r["status"] and st.button("Status speichern", key=f"sb_{r['id']}"):
                zeile = {k: r[k] for k in r.index}
                zeile.update(zeit=jetzt(), person=ctx.person, status=neu)
                ctx.speichern("entscheide", [zeile], f"Status: {neu}.")
    st.download_button("Protokoll als CSV herunterladen", d.drop(columns=["snapshot"]).to_csv(index=False).encode("utf-8"),
                       "entscheidungsprotokoll.csv", "text/csv")
