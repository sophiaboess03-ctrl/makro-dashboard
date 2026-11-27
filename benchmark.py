"""Benchmark des Dozenten (Update 16): 50 % S&P 500 / 50 % US-Treasuries, Aktienteil mit den Titelgewichten aus der
Benchmark-Datei der Dozenten-App («benchmark-aktuell-<Datum>.csv»).

- Datei lesen (`lesen`): Block 1 = Titel (Symbol; Name; Sektor; Gewicht <Stichtag> (%); Gewicht aktuell (%); …),
  Block 2 = Sektoren (Sektor; Code; Gewicht <Stichtag>; Gewicht aktuell; Änderung; Gewicht gültig im Benchmark).
- «Gültig im Benchmark» = die Gewichte, gegen die die Dozenten-App misst (Stand Datei 25.11.: Monatsende 31.10.).
  Welche Titelspalte das ist, wird an den Sektorgewichten erkannt (gültig = Stichtag oder aktuell).
- Gespeichert in der Google-Tabelle, Blatt «einstellungen», Schlüssel «benchmark_titel» (komprimiertes JSON, eine Zelle),
  neuster Eintrag gilt. Ohne Eintrag gilt die mitgelieferte Datei `benchmark_start.csv` (Stand 25.11.2026).
- Active Share (Cremers/Petajisto) = ½ Σ |Gewicht im Portfolio − Gewicht in der Benchmark| über alle Titel. Aktienklassen
  desselben Unternehmens (GOOG/GOOGL, FOX/FOXA, NWS/NWSA) zählen zusammen, damit eine Klassenwahl keine Scheinabweichung ist.
  * Aktienteil: eure Aktien (auf 100 %) gegen den S&P 500 der Benchmark – das ist die Zahl, die das IPS begrenzt.
  * Gesamtportfolio: Aktien, Treasuries (als ein Block, der Treasury-Index des Dozenten ist nicht bekannt) und Cash gegen
    50 / 50 / 0 – untere Grenze, weil Unterschiede innerhalb der Treasuries nicht zählen.
"""
from __future__ import annotations

import base64
import csv
import functools
import io
import json
import re
import zlib
from pathlib import Path

import pandas as pd

from makro import config as C

SCHLUESSEL = "benchmark_titel"
DATEI_START = Path(__file__).with_name("benchmark_start.csv")
AKTIEN, ANLEIHEN = C.BENCHMARK["aktien"], C.BENCHMARK["anleihen"]
SEKTOR_MAP = {  # Sektorname der Dozenten-App → Sektorname im Dashboard
    "energie": "Energie", "roh-, hilfs- und betriebsstoffe": "Grundstoffe", "grundstoffe": "Grundstoffe", "materials": "Grundstoffe",
    "industrie": "Industrie", "industrials": "Industrie", "zyklischer konsum": "Zykl. Konsum", "zykl. konsum": "Zykl. Konsum",
    "nicht-basiskonsumgüter": "Zykl. Konsum", "consumer discretionary": "Zykl. Konsum", "basiskonsumgüter": "Basiskonsum",
    "basiskonsum": "Basiskonsum", "consumer staples": "Basiskonsum", "gesundheit": "Gesundheit", "gesundheitswesen": "Gesundheit",
    "health care": "Gesundheit", "finanzen": "Finanzen", "finanzwesen": "Finanzen", "financials": "Finanzen",
    "informationstechnologie": "IT", "it": "IT", "information technology": "IT", "kommunikationsdienste": "Kommunikation",
    "kommunikation": "Kommunikation", "communication services": "Kommunikation", "versorger": "Versorger",
    "versorgungsbetriebe": "Versorger", "utilities": "Versorger", "immobilien": "Immobilien", "real estate": "Immobilien",
}
BASIS_NAME = {"gueltig": "gültig im Benchmark", "aktuell": "aktuell"}


def sektor_app(name: str) -> str:
    s = str(name or "").strip()
    return SEKTOR_MAP.get(s.lower(), s if s in C.SEKTOREN else "Unbekannt")


def yahoo_ticker(sym: str) -> str:
    """BRKb → BRK-B, BF.B → BF-B, sonst gross geschrieben."""
    s = str(sym or "").strip()
    m = re.fullmatch(r"([A-Z]+)([a-z])", s)
    if m:
        return f"{m.group(1)}-{m.group(2).upper()}"
    return s.upper().replace(".", "-").replace("/", "-")


def _z(x) -> float | None:
    s = str(x or "").strip().replace("'", "").replace("%", "").replace(" ", "")
    if not s:
        return None
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _datum(kopf: str) -> str | None:
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", kopf or "")
    return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}" if m else None


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------
def lesen(roh: bytes | str, dateiname: str = "") -> dict:
    """CSV der Dozenten-App → {titel, sektoren, stichtag, stand, gueltig, quelle}. Wirft ValueError mit verständlichem Text."""
    if isinstance(roh, bytes):
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                text = roh.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    else:
        text = roh
    text = text.lstrip("﻿")
    probe = text[:2000]
    trenn = ";" if probe.count(";") >= probe.count(",") else ","
    zeilen = [z for z in csv.reader(io.StringIO(text), delimiter=trenn)]
    bloecke, akt = [], []
    for z in zeilen:
        if not any(str(c).strip() for c in z):
            if akt:
                bloecke.append(akt)
                akt = []
            continue
        akt.append([str(c).strip() for c in z])
    if akt:
        bloecke.append(akt)
    titel_b = next((b for b in bloecke if b and any(k.lower().startswith("symbol") for k in b[0])), None)
    sek_b = next((b for b in bloecke if b and b[0] and b[0][0].lower().startswith("sektor")), None)
    if titel_b is None:
        raise ValueError("Keine Titeltabelle gefunden (erwartet: Kopfzeile mit «Symbol; Name; Sektor; Gewicht …»).")
    kopf = [k.lower() for k in titel_b[0]]

    def _idx(*anf, ohne=()):
        for i, k in enumerate(kopf):
            if any(k.startswith(a) for a in anf) and not any(o in k for o in ohne):
                return i
        return None

    i_sym, i_name, i_sek = _idx("symbol", "ticker"), _idx("name"), _idx("sektor", "sector")
    i_w_alt = _idx("gewicht", ohne=("aktuell", "änderung", "anteil", "gültig"))
    i_w_akt = _idx("gewicht aktuell")
    i_k_alt, i_k_akt = _idx("kurs", ohne=("aktuell", "änderung", "kurstag")), _idx("kurs aktuell")
    i_tag = _idx("kurstag")
    if i_sym is None or (i_w_alt is None and i_w_akt is None):
        raise ValueError("Spalten «Symbol» und «Gewicht …» nicht gefunden.")
    stichtag = _datum(titel_b[0][i_w_alt]) if i_w_alt is not None else None
    daten = []
    for z in titel_b[1:]:
        if len(z) <= i_sym or not z[i_sym]:
            continue
        g = lambda i: z[i] if i is not None and i < len(z) else ""  # noqa: E731
        w_alt, w_akt = _z(g(i_w_alt)), _z(g(i_w_akt))
        if w_alt is None and w_akt is None:
            continue
        daten.append(dict(symbol=z[i_sym], ticker=yahoo_ticker(z[i_sym]), name=g(i_name), sektor=sektor_app(g(i_sek)),
                          sektor_orig=g(i_sek), w_alt=w_alt if w_alt is not None else w_akt,
                          w_akt=w_akt if w_akt is not None else w_alt, kurs_alt=_z(g(i_k_alt)), kurs_akt=_z(g(i_k_akt)),
                          kurstag=g(i_tag)))
    if len(daten) < 50:
        raise ValueError(f"Nur {len(daten)} Titel erkannt – ist das die Benchmark-Datei der Dozenten-App?")
    titel = pd.DataFrame(daten)
    stand = max((d for d in titel["kurstag"] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d))), default=None)
    sektoren = None
    if sek_b is not None:
        k2 = [k.lower() for k in sek_b[0]]
        j_alt = next((i for i, k in enumerate(k2) if k.startswith("gewicht") and not any(o in k for o in ("aktuell", "gültig", "änderung"))), None)
        j_akt = next((i for i, k in enumerate(k2) if k.startswith("gewicht aktuell")), None)
        j_g = next((i for i, k in enumerate(k2) if "gültig" in k), None)
        rows = []
        for z in sek_b[1:]:
            if not z or not z[0]:
                continue
            g2 = lambda i: _z(z[i]) if i is not None and i < len(z) else None  # noqa: E731
            rows.append(dict(sektor=sektor_app(z[0]), sektor_orig=z[0], w_alt=g2(j_alt), w_akt=g2(j_akt), w_gueltig=g2(j_g)))
        sektoren = pd.DataFrame(rows) if rows else None
    # Welche Titelspalte ist «gültig im Benchmark»? An den Sektoren erkennen (sonst Stichtag).
    gueltig = "alt"
    if sektoren is not None and sektoren["w_gueltig"].notna().any():
        d_alt = (sektoren["w_gueltig"] - sektoren["w_alt"]).abs().sum() if sektoren["w_alt"].notna().any() else 99
        d_akt = (sektoren["w_gueltig"] - sektoren["w_akt"]).abs().sum() if sektoren["w_akt"].notna().any() else 99
        gueltig = "akt" if d_akt < d_alt else "alt"
    if sektoren is None:   # aus den Titeln
        g_alt = titel.groupby("sektor")["w_alt"].sum()
        g_akt = titel.groupby("sektor")["w_akt"].sum()
        sektoren = pd.DataFrame([dict(sektor=s, sektor_orig=s, w_alt=float(g_alt.get(s, 0)), w_akt=float(g_akt.get(s, 0)),
                                      w_gueltig=float(g_alt.get(s, 0))) for s in C.SEKTOREN])
    return dict(titel=titel, sektoren=sektoren, stichtag=stichtag, stand=stand, gueltig=gueltig,
                quelle=f"Dozenten-App, Datei {dateiname}".strip().rstrip(",") if dateiname else "Dozenten-App")


# ---------------------------------------------------------------------------
# Speichern / Laden
# ---------------------------------------------------------------------------
_T_SPALTEN = ["symbol", "name", "sektor", "w_alt", "w_akt", "kurs_akt"]


def kodieren(d: dict) -> str:
    t = d["titel"]
    js = dict(v=1, stichtag=d.get("stichtag"), stand=d.get("stand"), gueltig=d.get("gueltig", "alt"), quelle=d.get("quelle", ""),
              titel=[[r["symbol"], r["name"], r["sektor"], r["w_alt"], r["w_akt"], r.get("kurs_akt")] for _, r in t.iterrows()],
              sektoren=[[r["sektor"], r["w_alt"], r["w_akt"], r["w_gueltig"]] for _, r in d["sektoren"].iterrows()])
    roh = json.dumps(js, ensure_ascii=False, separators=(",", ":"), default=float).encode("utf-8")
    return "z1:" + base64.b64encode(zlib.compress(roh, 9)).decode("ascii")


@functools.lru_cache(maxsize=8)
def dekodieren(s: str) -> dict | None:
    try:
        s = str(s)
        js = json.loads(zlib.decompress(base64.b64decode(s[3:])).decode("utf-8") if s.startswith("z1:") else s)
        titel = pd.DataFrame(js["titel"], columns=_T_SPALTEN)
        titel["ticker"] = titel["symbol"].map(yahoo_ticker)
        sek = pd.DataFrame(js["sektoren"], columns=["sektor", "w_alt", "w_akt", "w_gueltig"])
        return dict(titel=titel, sektoren=sek, stichtag=js.get("stichtag"), stand=js.get("stand"), gueltig=js.get("gueltig", "alt"),
                    quelle=js.get("quelle", ""))
    except Exception:  # noqa: BLE001
        return None


@functools.lru_cache(maxsize=1)
def _start() -> dict | None:
    try:
        d = lesen(DATEI_START.read_bytes(), DATEI_START.name)
        d.update(quelle="mitgelieferte Datei (Dozenten-App, Stand 25.11.2026)", person="Startwert", zeit="")
        return d
    except Exception:  # noqa: BLE001
        return None


_AKTIV: dict | None = None


def aus_tabellen(tabellen: dict | None) -> dict | None:
    """Neuster gespeicherter Benchmark (Blatt «einstellungen») oder die mitgelieferte Datei."""
    e = (tabellen or {}).get("einstellungen")
    if e is not None and len(e) and "schluessel" in e:
        d = e[e["schluessel"] == SCHLUESSEL].sort_values("zeit")
        for _, z in d.iloc[::-1].iterrows():
            x = dekodieren(str(z["wert"]))
            if x is not None:
                return dict(x, person=str(z.get("person", "")), zeit=str(z.get("zeit", "")))
    return _start()


def setze(tabellen: dict | None) -> dict | None:
    """Von der App (und vom Monitoring-Lauf) einmal pro Lauf aufgerufen."""
    global _AKTIV
    _AKTIV = aus_tabellen(tabellen)
    return _AKTIV


def aktuell() -> dict | None:
    return _AKTIV if _AKTIV is not None else _start()


def beschreibung(d: dict | None = None) -> str:
    d = d or aktuell()
    if not d:
        return "Keine Benchmark-Datei geladen."
    st_ = pd.Timestamp(d["stichtag"]).strftime("%d.%m.%Y") if d.get("stichtag") else "?"
    sd = pd.Timestamp(d["stand"]).strftime("%d.%m.%Y") if d.get("stand") else "?"
    g = st_ if d.get("gueltig", "alt") == "alt" else sd
    return f"{len(d['titel'])} Titel · gültig im Benchmark: Gewichte vom {g} · aktuell: Stand {sd} · {d.get('quelle', '')}"


# ---------------------------------------------------------------------------
# Gewichte
# ---------------------------------------------------------------------------
def _spalte(d: dict, basis: str) -> str:
    if basis == "aktuell":
        return "w_akt"
    return "w_alt" if d.get("gueltig", "alt") == "alt" else "w_akt"


def gewichte(basis: str = "gueltig", d: dict | None = None) -> pd.Series:
    """Titelgewichte des S&P 500 in der Benchmark (Yahoo-Ticker → Anteil, Summe 1)."""
    d = d or aktuell()
    if not d:
        return pd.Series(dtype=float)
    t = d["titel"]
    w = pd.Series(t[_spalte(d, basis)].astype(float).values, index=t["ticker"]).groupby(level=0).sum()
    w = w[w > 0]
    return w / w.sum() if w.sum() > 0 else w


def sektor_gewichte(basis: str = "gueltig", d: dict | None = None) -> dict:
    """Sektorgewichte der Benchmark in % (Dashboard-Sektornamen)."""
    d = d or aktuell()
    if not d:
        return dict(C.SP500_GEWICHT)
    s = d["sektoren"]
    spalte = "w_gueltig" if basis == "gueltig" and s["w_gueltig"].notna().any() else ("w_akt" if basis == "aktuell" else "w_alt")
    out = {r["sektor"]: float(r[spalte] or 0) for _, r in s.iterrows() if r["sektor"] in C.SEKTOREN}
    return {k: out.get(k, 0.0) for k in C.SEKTOREN}


def infos(d: dict | None = None) -> tuple[dict, dict, dict]:
    """(Name, Sektor, Emittent) je Ticker. Emittent: gleicher Name = gleiche Firma (Alphabet A/C, Fox A/B, News Corp A/B)."""
    d = d or aktuell()
    if not d:
        return {}, {}, {}
    t = d["titel"]
    name = dict(zip(t["ticker"], t["name"].astype(str)))
    sektor = dict(zip(t["ticker"], t["sektor"]))
    emit = {tk: (n.strip().lower() or tk) for tk, n in name.items()}
    return name, sektor, emit


def norm_ticker(t) -> str:
    return str(t or "").strip().upper().replace(".", "-")


def active_share(werte: dict, basis: str = "gueltig", sektor_von: dict | None = None, d: dict | None = None,
                 index_betrag: float = 0.0) -> dict | None:
    """Active Share des Aktienteils gegen den S&P 500 der Benchmark.
    werte = {Ticker: USD oder Gewicht}; index_betrag = zusätzlicher Betrag, der wie der Index liegt (z. B. offenes
    Aktienbudget als SPY) – zählt mit den Benchmark-Gewichten."""
    d = d or aktuell()
    if not d:
        return None
    bm = gewichte(basis, d)
    name, sek_bm, emit = infos(d)
    p = pd.Series({norm_ticker(k): float(v) for k, v in werte.items() if v and float(v) > 0}, dtype=float)
    p = p.groupby(level=0).sum()
    summe = float(p.sum()) + max(float(index_betrag or 0), 0.0)
    if summe <= 0:
        return None
    p = p / summe
    if index_betrag and index_betrag > 0:
        p = p.add(bm * (index_betrag / summe), fill_value=0.0)
    key = lambda t: emit.get(t, t)  # noqa: E731
    pg = p.groupby(key).sum()
    bg = bm.groupby(key).sum()
    alle = pg.index.union(bg.index)
    diff = pg.reindex(alle, fill_value=0.0) - bg.reindex(alle, fill_value=0.0)
    as_ = float(diff.abs().sum() / 2 * 100)
    gehalten = [k for k in pg.index if pg[k] > 0]
    sv = sektor_von or {}
    # Tabelle je Emittent
    tick_von = {}
    for t in list(bm.index) + list(p.index):
        tick_von.setdefault(key(t), [])
        if t not in tick_von[key(t)]:
            tick_von[key(t)].append(t)
    zeilen = []
    for k in alle:
        tks = tick_von.get(k, [k])
        t0 = tks[0]
        zeilen.append(dict(ticker=" / ".join(sorted(tks)), name=name.get(t0, ""), sektor=sek_bm.get(t0) or sv.get(t0) or "Unbekannt",
                           portfolio=float(pg.get(k, 0.0)) * 100, benchmark=float(bg.get(k, 0.0)) * 100,
                           aktiv=float(diff[k]) * 100, im_index=k in bg.index))
    tab = pd.DataFrame(zeilen)
    tab["_abs"] = tab["aktiv"].abs()
    tab = tab.sort_values("_abs", ascending=False).drop(columns="_abs").reset_index(drop=True)
    nicht_im_index = sorted(t for t in p.index if t not in bm.index and p[t] > 0)
    # Sektoren
    ps = tab.groupby("sektor")["portfolio"].sum()
    bs = tab.groupby("sektor")["benchmark"].sum()
    beitrag = tab.assign(a=tab["aktiv"].abs() / 2).groupby("sektor")["a"].sum()
    sek = pd.DataFrame([dict(sektor=s, portfolio=float(ps.get(s, 0.0)), benchmark=float(bs.get(s, 0.0)),
                             aktiv=float(ps.get(s, 0.0) - bs.get(s, 0.0)), beitrag_as=float(beitrag.get(s, 0.0)),
                             titel_p=int(((tab["sektor"] == s) & (tab["portfolio"] > 0)).sum()),
                             titel_b=int(((tab["sektor"] == s) & (tab["benchmark"] > 0)).sum()))
                        for s in C.SEKTOREN + (["Unbekannt"] if "Unbekannt" in ps.index else [])])
    as_sektor = float(sek["aktiv"].abs().sum() / 2)
    return dict(active_share=as_, as_sektor=as_sektor, as_titel_in_sektor=as_ - as_sektor, n_portfolio=len(gehalten),
                n_benchmark=len(bg), n_gemeinsam=int(sum(1 for k in gehalten if k in bg.index)),
                abdeckung=float(bg.reindex(gehalten).fillna(0).sum() * 100), nicht_im_index=nicht_im_index,
                nicht_gehalten=float(bg.drop(index=[k for k in gehalten if k in bg.index]).sum() * 100),
                tabelle=tab, sektoren=sek, basis=basis, index_anteil=float(index_betrag / summe * 100) if index_betrag else 0.0)


def active_share_gesamt(aktien: dict, treas: float, cash: float, total: float, basis: str = "gueltig",
                        index_betrag: float = 0.0, d: dict | None = None) -> float | None:
    """Active Share des Gesamtportfolios gegen 50 % S&P 500 / 50 % Treasuries / 0 % Cash (Treasuries als ein Block).
    aktien = {Ticker: USD}, treas/cash/index_betrag in USD, total = Gesamtwert."""
    d = d or aktuell()
    if not d or total <= 0:
        return None
    bm = gewichte(basis, d)
    _, _, emit = infos(d)
    key = lambda t: emit.get(t, t)  # noqa: E731
    p = pd.Series({norm_ticker(k): float(v) / total for k, v in aktien.items() if v and float(v) > 0}, dtype=float)
    p = p.groupby(level=0).sum()
    if index_betrag:
        p = p.add(bm * (index_betrag / total), fill_value=0.0)
    pg, bg = p.groupby(key).sum(), (bm * AKTIEN / 100).groupby(key).sum()
    alle = pg.index.union(bg.index)
    s = float((pg.reindex(alle, fill_value=0) - bg.reindex(alle, fill_value=0)).abs().sum())
    s += abs(treas / total - ANLEIHEN / 100) + abs(cash / total - C.BENCHMARK["cash"] / 100)
    return s / 2 * 100


# ---------------------------------------------------------------------------
# Darstellung: Benchmark-Datei laden (Einstellungen bzw. Analyse)
# ---------------------------------------------------------------------------
def bereich_import(ctx, key: str = "bm"):
    import streamlit as st

    from makro import ui
    from storage import jetzt

    d = aktuell()
    st.markdown(f"**Benchmark des Dozenten** · 50 % S&P 500 / 50 % US-Treasuries · {beschreibung(d)}"
                + (f" · gespeichert von {d.get('person')} am {str(d.get('zeit'))[:10]}" if d and d.get("zeit") else ""))
    st.caption("Die Titelgewichte der Benchmark kommen aus der Datei der Dozenten-App (Export «benchmark-aktuell-<Datum>.csv»). "
               "Sie bestimmen Active Share, Über-/Untergewichte je Titel und die Sektorgewichte «gültig im Benchmark». "
               "Neue Datei einfach hier hochladen – sie gilt dann im ganzen Dashboard (Analyse, Vergleich, Portfolio, Monitoring).")
    datei = st.file_uploader("Neue Benchmark-Datei (CSV der Dozenten-App)", type=["csv", "txt"], key=f"{key}_datei")
    if datei is None:
        return
    try:
        neu = lesen(datei.getvalue(), datei.name)
    except ValueError as e:
        st.error(f"Datei nicht lesbar: {e}")
        return
    sek = neu["sektoren"].copy()
    ui.kopf_kpis([
        ("Titel", str(len(neu["titel"])), f"Summe Gewichte {ui.de(float(neu['titel']['w_alt'].sum()), 2)} %"),
        ("Gewichte gültig im Benchmark", pd.Timestamp(neu["stichtag"] if neu["gueltig"] == "alt" else neu["stand"]).strftime("%d.%m.%Y")
         if (neu["stichtag"] or neu["stand"]) else "?", "an den Sektorgewichten erkannt"),
        ("Kursstand", pd.Timestamp(neu["stand"]).strftime("%d.%m.%Y") if neu["stand"] else "?", "letzter Kurstag in der Datei"),
        ("Sektoren erkannt", f"{int(sek['sektor'].isin(C.SEKTOREN).sum())} / 11",
         ", ".join(sek.loc[~sek["sektor"].isin(C.SEKTOREN), "sektor_orig"].astype(str)) if "sektor_orig" in sek else ""),
    ])
    st.dataframe(pd.DataFrame({"Sektor": sek["sektor"], "Stichtag %": sek["w_alt"].map(lambda x: ui.de(x, 2)),
                               "aktuell %": sek["w_akt"].map(lambda x: ui.de(x, 2)),
                               "gültig im Benchmark %": sek["w_gueltig"].map(lambda x: ui.de(x, 2))}),
                 hide_index=True, width="stretch")
    c1, c2 = st.columns(2)
    if c1.button("Benchmark-Datei speichern (gilt fürs ganze Team)", type="primary", icon=":material/save:",
                 disabled=ctx.nur_lesen, key=f"{key}_speichern"):
        ctx.speichern("einstellungen", [dict(zeit=jetzt(), person=ctx.person, schluessel=SCHLUESSEL, wert=kodieren(neu))],
                      f"Benchmark-Datei ({len(neu['titel'])} Titel, Stand {neu['stand']}) gespeichert.")
    if neu.get("stand") and c2.button(f"Sektorgewichte «aktuell» ({pd.Timestamp(neu['stand']):%d.%m.}) als Stichtag der "
                                      "Sektorallokation eintragen", icon=":material/event:", disabled=ctx.nur_lesen,
                                      key=f"{key}_stichtag",
                                      help="Die Seite «Sektorallokation» rechnet Ziele = Indexgewicht + Abweichung. Mit diesem "
                                           "Stichtag startet die Fortschreibung von den echten Gewichten der Dozenten-App."):
        z = jetzt()
        ctx.speichern("sektor_index", [dict(zeit=z, person=ctx.person, stichtag=neu["stand"], sektor=r["sektor"], gewicht=r["w_akt"],
                                            quelle=f"{neu['quelle']} (Gewicht aktuell)") for _, r in sek.iterrows()
                                       if r["sektor"] in C.SEKTOREN and r["w_akt"] is not None],
                      f"Sektorgewichte Stichtag {pd.Timestamp(neu['stand']):%d.%m.%Y} gespeichert.")
