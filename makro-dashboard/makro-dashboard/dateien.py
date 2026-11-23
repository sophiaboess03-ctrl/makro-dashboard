"""Dokumente: Analysen (Word, PDF, Excel, PowerPoint …) pro Kapitel hochladen und direkt aus der App öffnen.

Online liegen die Dateien in eurem Google Drive (Ordner «Makro-Dashboard Dokumente», über das Google-Skript),
lokal im Ordner daten/dateien. In der Google-Tabelle (Blatt «dateien») steht nur die Liste mit Name, Person und Datum.
"""
from __future__ import annotations

import html
import uuid

import pandas as pd
import streamlit as st

from makro import config as C
from storage import jetzt

MAX_MB = 15

# Dokument-Bereiche je Seite (ID → Name). Die Kapitel kommen aus config.KAPITEL dazu.
SEITEN_BEREICHE = {
    "allgemein": "Allgemein / Team",
    "gesamtbild": "Gesamtbild",
    "ic": "Investment Committee",
    "portfolio": "Portfolio",
    "analyse": "Analyse",
    "maerkte": "Märkte heute",
    "news": "Tagesupdate & News",
    "szenarien": "Szenarien",
    "eingepreist": "Was ist eingepreist?",
    "risiken": "Risiken & Chancen",
    "ips": "IPS",
    "dozent": "Erwartungen (Dozent)",
    "sektoren": "Sektorallokation",
    "taktik": "Taktik",
    "risiko": "Risiko & Risikobudget",
    "anleihen": "Anleihen & Duration",
    "entscheide": "Entscheide",
    "bericht": "Berichte",
    "eintragen": "Daten eintragen",
    "termine": "Termine",
    "protokoll": "Protokoll",
}


def alle_bereiche() -> dict[str, str]:
    return {**SEITEN_BEREICHE, **{k["id"]: k["name"] for k in C.KAPITEL}}


def liste(tab: pd.DataFrame | None, bereich: str | None = None) -> pd.DataFrame:
    if tab is None or tab.empty:
        return pd.DataFrame(columns=["zeit", "person", "id", "bereich", "name", "typ", "groesse", "datei_id", "beschreibung", "geloescht"])
    d = tab.sort_values("zeit").drop_duplicates("id", keep="last")
    d = d[~d["geloescht"].astype(str).isin(["1", "1.0", "True", "true"])]
    if bereich:
        d = d[d["bereich"] == bereich]
    return d.sort_values("zeit", ascending=False)


@st.cache_data(ttl=3600, show_spinner="Lade Datei …", max_entries=30)
def _laden(_store, store_name: str, datei_id: str) -> bytes:
    return _store.datei_laden(datei_id)


def bereich(ctx, bereich_id: str, titel: str = "Dokumente & Analysen"):
    st.markdown(f"#### {titel}")
    d = liste(ctx.tabellen.get("dateien"), bereich_id)
    if d.empty:
        st.caption("Noch keine Dokumente. Ladet eure Analyse (z. B. Word oder PDF) hier hoch – das ganze Team kann sie dann direkt "
                   "aus der App öffnen.")
    for _, r in d.iterrows():
        c1, c2, c3 = st.columns([3, 1.1, 0.6])
        g = float(r["groesse"]) if str(r["groesse"]).replace(".", "").isdigit() else None
        groesse = "" if g is None else f"{g / 1e6:.1f} MB" if g >= 1e5 else f"{g / 1e3:.0f} KB"
        c1.markdown(f"**{html.escape(r['name'])}** <span class='klein'>· {html.escape(r['person'])} · {str(r['zeit'])[:10]} · "
                    f"{groesse}{(' · ' + html.escape(r['beschreibung'])) if r['beschreibung'] else ''}</span>", unsafe_allow_html=True)
        key = f"dl_{bereich_id}_{r['id']}"
        if st.session_state.get(key):
            c2.download_button("Speichern", st.session_state[key], r["name"], r["typ"] or "application/octet-stream",
                               key=f"{key}_b", icon=":material/download:", width="stretch")
        elif c2.button("Öffnen", key=f"{key}_o", icon=":material/description:", width="stretch"):
            try:
                st.session_state[key] = _laden(ctx.store, ctx.store.name, r["datei_id"])
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"Datei konnte nicht geladen werden: {e}")
        if not ctx.nur_lesen and c3.button("", key=f"{key}_x", icon=":material/delete:", help="Aus der Liste entfernen"):
            ctx.speichern("dateien", [dict(r.to_dict(), zeit=jetzt(), person=ctx.person, geloescht=1)], "Dokument entfernt.")
    if ctx.nur_lesen:
        return
    with st.popover("Dokument hochladen", icon=":material/upload_file:"):
        with st.form(f"upload_{bereich_id}", clear_on_submit=True):
            f = st.file_uploader("Datei", type=["pdf", "docx", "doc", "xlsx", "xls", "pptx", "csv", "txt", "md", "png", "jpg"],
                                 key=f"up_{bereich_id}")
            beschr = st.text_input("Kurzbeschreibung (optional)")
            if st.form_submit_button("Hochladen", type="primary") and f is not None:
                inhalt = f.getvalue()
                if len(inhalt) > MAX_MB * 1e6:
                    st.error(f"Die Datei ist grösser als {MAX_MB} MB.")
                else:
                    try:
                        with st.spinner("Lade hoch …"):
                            datei_id = ctx.store.datei_hochladen(f.name, f.type, inhalt, bereich_id)
                        ctx.speichern("dateien", [dict(zeit=jetzt(), person=ctx.person, id=uuid.uuid4().hex[:10], bereich=bereich_id,
                                                       name=f.name, typ=f.type or "", groesse=len(inhalt), datei_id=datei_id,
                                                       beschreibung=beschr.strip(), geloescht=0)], f"«{f.name}» hochgeladen.")
                    except Exception as e:  # noqa: BLE001
                        st.error(f"Hochladen fehlgeschlagen: {e}")


def seite_dokumente(ctx):
    from makro import ui

    ui.seitenkopf("Dokumente", "Alle hochgeladenen Analysen und Begründungen an einem Ort. Hochladen geht direkt unten auf "
                               "jeder Seite oder hier.")
    namen = alle_bereiche()
    alle = liste(ctx.tabellen.get("dateien"))
    st.caption(f"{len(alle)} Dokument(e) gespeichert.")
    anzahl = {bid: (int((alle["bereich"] == bid).sum()) if len(alle) else 0) for bid in namen}
    # Bereiche mit Dokumenten zuerst, danach die leeren
    for bid in sorted(namen, key=lambda b: anzahl[b] == 0):
        n = anzahl[bid]
        with st.expander(f"{namen[bid]} ({n})", expanded=n > 0 and bid == "allgemein"):
            bereich(ctx, bid, titel=namen[bid])
    # Dokumente aus Bereichen, die es nicht mehr gibt, nicht verlieren
    rest = sorted(set(alle["bereich"]) - set(namen)) if len(alle) else []
    for bid in rest:
        with st.expander(f"{bid} ({anzahl.get(bid, int((alle['bereich'] == bid).sum()))})"):
            bereich(ctx, bid, titel=bid)
