"""Makro-Dashboard USA – Team-App für die FHGR-Portfoliosimulation.

Start lokal:  streamlit run app.py
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import partial

import pandas as pd
import streamlit as st

from makro import config as C
from makro import logic as L
from makro import ui, views
from makro.fred import FredFehler, lade_serie
from storage import GoogleTabelleStore, LocalStore, SpeicherFehler

st.set_page_config(page_title="Makro-Dashboard USA", page_icon="🇺🇸", layout="wide")
ui.css()


def secret(key, default=None):
    try:
        return st.secrets[key]
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Zugang
# ---------------------------------------------------------------------------
pw = secret("app_password")
if pw and not st.session_state.get("ok"):
    st.title("🇺🇸 Makro-Dashboard USA")
    eingabe = st.text_input("Team-Passwort", type="password")
    if eingabe:
        if eingabe == pw:
            st.session_state["ok"] = True
            st.rerun()
        else:
            st.error("Falsches Passwort.")
    st.stop()


# ---------------------------------------------------------------------------
# Speicher & Daten
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner="Verbinde mit der Google-Tabelle …")
def verbinde_google(url: str, token: str):
    return GoogleTabelleStore(url, token)


def get_store():
    url, token = secret("script_url"), secret("script_token")
    if not url or "HIER" in str(url):
        return LocalStore("daten"), ("Noch keine Google-Tabelle verbunden: in den Secrets fehlt die script_url."
                                     if secret("app_password") else None)
    try:
        return verbinde_google(str(url), str(token or "")), None
    except SpeicherFehler as e:
        return LocalStore("daten"), f"Google-Tabelle nicht verbunden: {e}"
    except Exception as e:  # noqa: BLE001
        return LocalStore("daten"), f"Google-Tabelle nicht verbunden ({type(e).__name__})."


store, store_fehler = get_store()
st.session_state["store_name"] = store.name


@st.cache_data(ttl=60, show_spinner=False)
def lese_alle(store_name: str) -> dict:
    try:
        return store.alle(), None
    except Exception as e:  # noqa: BLE001
        return LocalStore("daten").alle(), f"Lesen aus der Google-Tabelle fehlgeschlagen: {e}"


@st.cache_data(ttl=6 * 3600, show_spinner="Lade aktuelle Daten von FRED …")
def lade_fred(series: tuple[str, ...], api_key: str | None):
    start = (pd.Timestamp.today() - pd.DateOffset(years=12)).strftime("%Y-%m-%d")
    daten, fehler = {}, []

    def hole(s):
        try:
            return s, lade_serie(s, start=start, api_key=api_key)
        except (FredFehler, Exception):
            return s, None

    with ThreadPoolExecutor(max_workers=8) as ex:
        for s, reihe in ex.map(hole, series):
            if reihe is None:
                fehler.append(s)
            else:
                daten[s] = reihe
    return daten, sorted(fehler), datetime.now().strftime("%d.%m.%Y %H:%M")


def speichern(tabelle: str, zeilen: list[dict], meldung: str = "Gespeichert."):
    if not st.session_state.get("person") or st.session_state.get("person") == "Unbekannt":
        st.error("Bitte zuerst links in der Seitenleiste deinen Namen wählen bzw. eingeben.")
        return
    try:
        store.anhaengen(tabelle, zeilen)
    except Exception as e:  # noqa: BLE001
        st.error(f"Speichern fehlgeschlagen: {e}")
        return
    lese_alle.clear()
    st.session_state["meldung"] = meldung
    st.rerun()


serien = sorted({i["series"] for i in C.INDIKATOREN if i["quelle"] == "fred"} | {"GDPC1", "GDPPOT", C.REZESSION_SERIE})
fred, fred_fehler, fred_zeit = lade_fred(tuple(serien), secret("fred_api_key"))
tabellen, lese_fehler = lese_alle(store.name)
if lese_fehler:
    store_fehler = lese_fehler

daten = L.Daten(fred, tabellen["werte"])
status = L.status(daten)
kombis = L.kombi_signale(status, daten)
szen = L.szenario_wahrscheinlichkeiten(tabellen["szenario_votes"])
risiken = L.risiko_bewertung(tabellen["risiko_votes"], tabellen["risiken_neu"], status, kombis)
neutral = L.einstellungen(tabellen["einstellungen"])
alloc = L.allokation(status, szen, risiken, kombis, neutral)

# ---------------------------------------------------------------------------
# Person
# ---------------------------------------------------------------------------
with st.sidebar:
    team = list(secret("team", []) or [])
    if team:
        person = st.selectbox("Ich bin", team, key="person")
    else:
        person = st.text_input("Dein Name (für Einträge)", key="person") or "Unbekannt"
    st.caption(f"Speicher: {store.name}  \nFRED: {len(fred)} von {len(serien)} Reihen · {fred_zeit}")
    if store_fehler:
        st.error(store_fehler, icon="🔌")
    if isinstance(store, LocalStore):
        st.warning("Lokaler Speicher: Online gehen Einträge beim Neustart verloren. Für das Team die Google-Tabelle "
                   "verbinden (siehe ANLEITUNG.md).", icon="⚠️")

if msg := st.session_state.pop("meldung", None):
    st.toast(msg, icon="✅")

ctx = views.Ctx(daten=daten, status=status, kombis=kombis, szen=szen, risiken=risiken, alloc=alloc, tabellen=tabellen,
                person=person, speichern=speichern, rezession=fred.get(C.REZESSION_SERIE), fred_fehler=fred_fehler,
                fred_stand=f"FRED-Daten geladen {fred_zeit}", zyklus_team=L.zyklus_team(tabellen["zyklus_votes"]),
                zyklus_modell=L.zyklus_modell(status))

# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------
seiten = {
    "Überblick": [st.Page(partial(views.seite_gesamtbild, ctx), title="Gesamtbild", icon="🇺🇸", url_path="gesamtbild",
                          default=True)],
    "Kapitel": [st.Page(partial(views.seite_kapitel, ctx, k["id"]), title=k["name"], icon=k["icon"], url_path=k["id"])
                for k in C.KAPITEL],
    "Auswertung": [
        st.Page(partial(views.seite_szenarien_risiken, ctx), title="Szenarien & Risiken", icon="🎲", url_path="szenarien"),
        st.Page(partial(views.seite_allokation, ctx), title="Allokation", icon="🧭", url_path="allokation"),
        st.Page(partial(views.seite_termine, ctx), title="Termine", icon="📅", url_path="termine"),
    ],
    "Team": [
        st.Page(partial(views.seite_bewerten, ctx), title="Bewerten", icon="🗳️", url_path="bewerten"),
        st.Page(partial(views.seite_eintragen, ctx), title="Daten eintragen", icon="✏️", url_path="eintragen"),
        st.Page(partial(views.seite_protokoll, ctx), title="Protokoll", icon="🧾", url_path="protokoll"),
        st.Page(partial(views.seite_einstellungen, ctx), title="Einstellungen", icon="⚙️", url_path="einstellungen"),
    ],
}
st.navigation(seiten).run()
