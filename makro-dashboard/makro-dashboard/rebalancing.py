"""Rebalancing des Bestands für Januar und Februar (Update 17) – mit Diagnose, Handelsplan, Vorher/Nachher, Buchung und Report.

Ablauf auf der Seite «Rebalancing»
1. Ausgangslage: Bestand zu Marktpreisen (Kupons und Zins auf Cash zählen als Cash) gegen die Ziele des gewählten Monats:
   Taktik (Aktien / Treasuries / Cash, Duration), Sektorziele (Sektorallokation: Index heute + Abweichung, wahlweise die
   Sektorgewichte der Taktik), IPS-Grenzen und Benchmark.
2. Diagnose: Welche Abweichungen verlangen ein Rebalancing? Priorität Pflicht (IPS) → Taktik → Sektoren → Titel → Anleihen →
   Cash, dazu offene Monitoring-Fälle. Daraus eine Empfehlung für die Methode.
3. Methode (CFA Level III, Rebalancing):
   - «Nur Pflicht»: IPS-Verstösse an die Grenze (+ kleiner Puffer) zurückführen – minimaler Umschlag.
   - «Ausserhalb der Toleranz auf Ziel» (Korridor-/Prozentband-Rebalancing, empfohlen zwischen den Taktik-Terminen): was
     ausserhalb der Toleranz liegt, geht ganz zurück aufs Ziel; was innerhalb liegt, bleibt.
   - «Alles auf Ziel» (Kalender-Rebalancing am Monatsbeginn, wenn die neue Taktik gilt).
   - «Ziel = Analyseportfolio»: ein in der Analyse vorbereitetes Portfolio (z. B. mit «Gewichte optimieren») ist das Ziel,
     gehandelt wird nur die Differenz zum Bestand.
   Weil die Simulation keine Transaktionskosten und keine Steuern kennt, ist «auf Ziel» statt «an den Bandrand» sinnvoll
   (bei Kosten wäre der Rand günstiger). Mindest-Handelsgrösse gegen Kleinst-Trades.
4. Innerhalb eines Sektors bleiben die Titel im gleichen Verhältnis (pro rata), das Titel-Maximum des IPS wird eingehalten,
   Titel ausserhalb des S&P 500 oder laut IPS ausgeschlossen werden verkauft. Sektoren ohne Titel: Vorschlag mit den grössten
   Indextiteln (Benchmark-Datei). Treasuries pro rata; Duration wahlweise über die kürzeste/längste Anleihe auf die Taktik.
5. Vorher/Nachher: Anlageklassen, Sektoren, Duration, Titel-Maximum, Active Share, Tracking Error und Risiko ex ante,
   IPS-Gap-Analyse.
6. Buchen zum Eröffnungskurs (Verkäufe vor Käufen), Begründung, Gegenargument und zwei Quellen Pflicht → Transaktionen
   (ids rb<batch><nr>), Entscheid «Rebalancing» im Entscheidungsprotokoll, Report-PDF automatisch unter Dokumente
   (Bereich «Rebalancing»). «Rückgängig» storniert alles.
"""
from __future__ import annotations

import json
import math
import uuid
from datetime import datetime

import numpy as np
import pandas as pd
import streamlit as st

from makro import config as C
from makro import logic as L
from makro import ui
from storage import jetzt, zahl

PRIO = ["Pflicht (IPS)", "Taktik", "Sektoren", "Titel", "Anleihen", "Cash", "Monitoring", "Hinweis"]
PRIO_STUFE = {"Pflicht (IPS)": L.ROT, "Taktik": L.GELB, "Sektoren": L.GELB, "Titel": L.GELB, "Anleihen": L.GELB, "Cash": L.GELB,
              "Monitoring": L.GELB, "Hinweis": L.GRUEN}
METHODEN = {"pflicht": "Nur Pflicht (IPS-Verstösse beheben)",
            "toleranz": "Ausserhalb der Toleranz zurück aufs Ziel",
            "voll": "Alles auf Ziel (Monatsbeginn / neue Taktik)",
            "analyse": "Ziel = gespeichertes Analyseportfolio"}
PUFFER = 0.5          # Pp. innerhalb der IPS-Grenze bei «Nur Pflicht»
KLASSEN = [("aktien", "Aktien"), ("anleihen", "Treasuries"), ("cash", "Cash")]


def _txt(x) -> str:
    try:
        if x is None or pd.isna(x):
            return ""
    except (TypeError, ValueError):
        pass
    return str(x).strip()


def _d(x, n=1, vz=False):
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else ui.de(x, n, vz)


# ---------------------------------------------------------------------------
# Bestand und Ziele
# ---------------------------------------------------------------------------
def bestand(ctx, fred: dict, lade_kurse, datum: pd.Timestamp | None = None) -> dict | None:
    """Bestand zu Marktpreisen. Cash = Kapital + Käufe/Verkäufe + Zins auf Cash + Kupons (verfügbar fürs Rebalancing)."""
    import cashzins as CZ
    import portfolio as P

    tx = P.transaktionen(ctx.tabellen.get("transaktionen"))
    if not len(tx):
        return None
    datum = (datum or pd.Timestamp.today()).normalize()
    kapital, _ = P._einstellungen(ctx.tabellen.get("einstellungen"))
    ticker = tuple(sorted(set(tx.loc[tx["klasse"] == "Aktie", "ticker"].str.upper())))
    kurse = lade_kurse(ticker)[0] if ticker else {}
    pos, cash_delta, real = P.positionen(tx)
    b = P.bewerte(pos, kurse, fred, datum)
    kupons = float(b["zins"].sum()) if len(b) else 0.0
    zins = CZ.zins_bis(tx, kapital, fred, datum)
    cash = float(kapital + cash_delta + zins + kupons)
    ak = b[b["klasse"] == "Aktie"].copy() if len(b) else pd.DataFrame(columns=["ticker", "name", "sektor", "menge", "kurs", "wert"])
    for i, r in ak.iterrows():
        if _txt(r.get("sektor")) not in C.SEKTOREN or not _txt(r.get("name")):
            info = P.ticker_info(str(r["ticker"]))
            if _txt(r.get("sektor")) not in C.SEKTOREN:
                ak.at[i, "sektor"] = info.get("sektor") or "Unbekannt"
            if not _txt(r.get("name")):
                ak.at[i, "name"] = info.get("name", "")
    tr = b[b["klasse"] == "Treasury"].copy() if len(b) else pd.DataFrame(
        columns=["ticker", "name", "kupon", "faelligkeit", "menge", "kurs", "wert", "duration", "restlaufzeit"])
    total = float(ak["wert"].sum() + tr["wert"].sum()) + cash
    return dict(ak=ak.reset_index(drop=True), tr=tr.reset_index(drop=True), cash=cash, total=total, kupons=kupons, zins=zins,
                kapital=kapital, datum=datum, kurse=kurse, tx=tx, pos=pos)


def duration(tr: pd.DataFrame) -> float | None:
    if tr is None or not len(tr) or not tr["wert"].sum():
        return None
    d = tr["duration"].astype(float).fillna(0)
    return float((d * tr["wert"]).sum() / tr["wert"].sum())


def ziele(ctx, monat_id: str, sektor_quelle: str = "sektorallokation") -> dict:
    """Ziele des Monats: Taktik (falls für den Monat gespeichert, sonst die gültige) und Sektorziele in % des Aktienteils."""
    import sektoren as SK
    import taktik as TK

    t = TK.lese(ctx.tabellen.get("taktik"), monat_id)
    a = ctx.alloc
    if t:
        z = dict(aktien=float(t["aktien"]), anleihen=float(t["anleihen"]), cash=float(t["cash"]),
                 duration=float(t["duration"]) if t["duration"] is not None else float(a["duration"]),
                 quelle=f"Taktik {C.MONAT_BY_ID[monat_id]['name']} ({t['person']}, {t['zeit'][:10]})", sek_taktik=t["sektoren"],
                 hat_taktik=True)
    else:
        z = dict(aktien=float(a["aktien"]), anleihen=float(a["anleihen"]), cash=float(a["cash"]), duration=float(a["duration"]),
                 quelle=f"für {C.MONAT_BY_ID[monat_id]['name']} noch keine Taktik gespeichert – es gilt {a.get('quelle_txt', 'die aktuelle Taktik')}",
                 sek_taktik=None, hat_taktik=False)
    idx = ctx.sek_index["gewichte"]
    if sektor_quelle == "taktik" and z["sek_taktik"] and z["aktien"]:
        sek = {s: float(z["sek_taktik"].get(s, 0.0)) / z["aktien"] * 100 for s in C.SEKTOREN}
        z["sek_quelle"] = "Sektorgewichte der Taktik"
    else:
        sa = SK.lade_allokation(ctx.tabellen.get("sektor_allokation"))["sektoren"]
        zi = SK.ziele(sa, idx)
        sek = {s: (zi[s] if zi[s] is not None else idx[s]) for s in C.SEKTOREN}
        z["sek_quelle"] = (f"Sektorallokation: Index {pd.Timestamp(ctx.sek_index['stichtag']):%d.%m.}"
                           + (" fortgeschrieben" if ctx.sek_index.get("fortgeschrieben") else "") + " + geplante Abweichung")
    tot = sum(sek.values()) or 1.0
    z["sektoren"] = {s: v / tot * 100 for s, v in sek.items()}
    z["index"] = dict(idx)
    try:
        import benchmark as BM

        z["bm_sek"] = BM.sektor_gewichte("gueltig")
    except Exception:  # noqa: BLE001
        z["bm_sek"] = dict(C.SP500_GEWICHT)
    return z


def _sektor_werte(ak: pd.DataFrame) -> dict:
    return {s: float(ak.loc[ak["sektor"] == s, "wert"].sum()) for s in C.SEKTOREN}


def letzte_rebalancings(tabellen: dict) -> list[dict]:
    e = tabellen.get("einstellungen")
    tx = tabellen.get("transaktionen")
    if e is None or e.empty:
        return []
    aktiv = set()
    if tx is not None and not tx.empty:
        d = tx.sort_values("zeit").drop_duplicates("id", keep="last")
        aktiv = set(d.loc[~d["storniert"].astype(str).str.strip().isin(["1", "1.0", "True"]), "id"].astype(str))
    out = []
    for _, r in e[e["schluessel"].astype(str).str.startswith("rebalancing|")].sort_values("zeit", ascending=False).iterrows():
        try:
            meta = json.loads(r["wert"])
        except Exception:  # noqa: BLE001
            continue
        b = str(r["schluessel"]).split("|", 1)[1]
        out.append(dict(batch=b, **meta, person=r["person"], zeit=str(r["zeit"]), aktiv=sum(1 for i in aktiv if i.startswith(f"rb{b}"))))
    return out


# ---------------------------------------------------------------------------
# Diagnose
# ---------------------------------------------------------------------------
def diagnose(ctx, B: dict, Z: dict, tol: dict) -> list[dict]:
    """Auslöser fürs Rebalancing: (prio, thema, ist, ziel, text, massnahme)."""
    ips = ctx.ips
    total = B["total"]
    out = []

    def add(prio, thema, ist, ziel, text, massnahme):
        out.append(dict(prio=prio, thema=thema, ist=ist, ziel=ziel, text=text, massnahme=massnahme))

    ist = {"aktien": float(B["ak"]["wert"].sum()) / total * 100, "anleihen": float(B["tr"]["wert"].sum()) / total * 100,
           "cash": B["cash"] / total * 100}
    for k, name in KLASSEN:
        lo, hi = ips[f"{k}_min"], ips[f"{k}_max"]
        d = ist[k] - Z[k]
        if not lo - 1e-9 <= ist[k] <= hi + 1e-9:
            add("Pflicht (IPS)", f"{name} ausserhalb des IPS-Bands", f"{_d(ist[k], 2)} %", f"{lo:g}–{hi:g} %",
                f"{name} liegen ausserhalb des IPS-Bands.", f"auf die Taktik {_d(Z[k], 1)} % zurückführen")
        elif abs(d) > float(ips.get("rebal_toleranz") or 5) + 1e-9:
            add("Pflicht (IPS)", f"{name}: Rebalancing-Toleranz überschritten", f"{_d(ist[k], 2)} %", f"{_d(Z[k], 1)} % ± {_d(ips.get('rebal_toleranz'), 1)}",
                "Die IPS-Regel verlangt ein Rebalancing.", f"auf {_d(Z[k], 1)} % zurückführen")
        elif abs(d) > tol["klasse"] + 1e-9:
            add("Taktik", f"{name} weicht von der Taktik ab", f"{_d(ist[k], 2)} %", f"{_d(Z[k], 1)} % ± {_d(tol['klasse'], 1)}",
                f"Abweichung {_d(d, 2, True)} Pp. – über der Toleranz, aber noch im IPS-Band.", "zurück auf die Taktik")
        elif abs(d) > 0.5:
            add("Hinweis", f"{name} leicht neben der Taktik", f"{_d(ist[k], 2)} %", f"{_d(Z[k], 1)} %",
                f"Abweichung {_d(d, 2, True)} Pp. – innerhalb der Toleranz.", "kein Handel nötig")
    if B["cash"] < -1:
        add("Pflicht (IPS)", "Cash negativ", f"{_d(B['cash'], 0)} USD", "≥ 0", "Mehr gekauft als Geld vorhanden.", "verkaufen")
    if B["kupons"] + B["zins"] > 0.001 * total:
        add("Cash", "Kupons und Zins auf Cash", f"{_d(B['kupons'] + B['zins'], 0)} USD", "investieren gemäss Taktik",
            "Seit dem Kauf zugeflossen – liegt als Cash und senkt die Aktien- und Treasury-Quote.", "mitverteilen")
    # Duration
    D = duration(B["tr"])
    if D is not None:
        dlo, dhi = ips["duration_min"], ips["duration_max"]
        if not dlo <= D <= dhi:
            add("Pflicht (IPS)", "Duration ausserhalb des IPS", f"{_d(D, 2)} J.", f"{_d(dlo, 1)}–{_d(dhi, 1)} J.",
                "Zinsrisiko der Treasuries ausserhalb der Grenzen.", f"auf {_d(Z['duration'], 1)} J. anpassen")
        elif abs(D - Z["duration"]) > tol["duration"] + 1e-9:
            add("Anleihen", "Duration weicht von der Taktik ab", f"{_d(D, 2)} J.", f"{_d(Z['duration'], 1)} J. ± {_d(tol['duration'], 1)}",
                "Anleihen altern (Duration sinkt jeden Monat um ≈ 0,08 J.) bzw. die Taktik hat eine neue Duration.",
                "zwischen kurzer und langer Anleihe umschichten")
    # Sektoren
    ak = B["ak"]
    aw = float(ak["wert"].sum())
    if aw > 0:
        sw = _sektor_werte(ak)
        smax = ips.get("sektor_abw_max")
        for s in C.SEKTOREN:
            ist_s = sw[s] / aw * 100
            ziel_s = Z["sektoren"][s]
            akt_bm = ist_s - Z["bm_sek"].get(s, 0.0)
            if smax and abs(akt_bm) > smax + 1e-9:
                add("Pflicht (IPS)", f"{s}: Sektorabweichung über dem IPS", f"{_d(ist_s, 2)} %",
                    f"Benchmark {_d(Z['bm_sek'].get(s), 2)} % ± {_d(smax, 1)}", f"Aktiv {_d(akt_bm, 2, True)} Pp. gegen die Benchmark.",
                    f"Richtung Ziel {_d(ziel_s, 2)} %")
            elif abs(ist_s - ziel_s) > tol["sektor"] + 1e-9:
                add("Sektoren", f"{s} neben dem Sektorziel", f"{_d(ist_s, 2)} %", f"{_d(ziel_s, 2)} % ± {_d(tol['sektor'], 1)}",
                    f"Ist − Ziel {_d(ist_s - ziel_s, 2, True)} Pp. (Kursbewegung oder neues Indexgewicht).", "Titel im Sektor pro rata anpassen")
            elif abs(ist_s - ziel_s) > 0.5:
                add("Hinweis", f"{s} knapp neben dem Ziel", f"{_d(ist_s, 2)} %", f"{_d(ziel_s, 2)} %",
                    f"Ist − Ziel {_d(ist_s - ziel_s, 2, True)} Pp. – innerhalb der Toleranz.", "beobachten")
            if sw[s] <= 0 and ziel_s > 0.5:
                add("Sektoren", f"{s}: kein Titel im Bestand", "0 %", f"{_d(ziel_s, 2)} %",
                    "Das Sektorziel ist ohne Titel nicht erreichbar.", "Titel kaufen (Vorschlag: grösste Indextitel)")
    # Titel
    tmax = ips.get("titel_max")
    aus_t = {x.strip().upper() for x in str(ips.get("aus_titel") or "").replace(";", ",").split(",") if x.strip()}
    aus_s = set(ips.get("aus_sektoren") or [])
    try:
        import benchmark as BM

        im_index = set(BM.gewichte().index)
    except Exception:  # noqa: BLE001
        im_index = set()
    for _, r in ak.iterrows():
        g = r["wert"] / total * 100
        if tmax and g > tmax + 1e-9:
            add("Pflicht (IPS)", f"{r['ticker']} über dem Titel-Maximum", f"{_d(g, 2)} %", f"≤ {_d(tmax, 1)} %",
                "Klumpenrisiko laut IPS.", f"auf {_d(tmax - 0.1, 1)} % kürzen, Rest im Sektor verteilen")
        elif tmax and g > tmax - 0.5:
            add("Hinweis", f"{r['ticker']} nahe am Titel-Maximum", f"{_d(g, 2)} %", f"≤ {_d(tmax, 1)} %", "Frühwarnung.", "beobachten")
        if im_index and r["ticker"] not in im_index:
            add("Pflicht (IPS)", f"{r['ticker']} nicht im S&P 500", f"{_d(g, 2)} %", "nur S&P-500-Titel", "Mandat der Plattform.", "verkaufen")
        if r["ticker"] in aus_t or r["sektor"] in aus_s:
            add("Pflicht (IPS)", f"{r['ticker']} laut IPS ausgeschlossen", f"{_d(g, 2)} %", "ausgeschlossen", "Ausschluss im IPS.", "verkaufen")
    nmin = ips.get("aktien_anzahl_min")
    if nmin and len(ak) < nmin:
        add("Pflicht (IPS)", "Zu wenige Aktien", str(len(ak)), f"≥ {_d(nmin, 0)}", "Mindestzahl laut IPS.", "Titel ergänzen")
    try:
        import benchmark as BM

        ad = BM.active_share(ak.groupby("ticker")["wert"].sum().to_dict()) if aw else None
        lo, hi = ips.get("active_share_min"), ips.get("active_share_max")
        if ad and lo is not None and not lo <= ad["active_share"] <= hi:
            add("Pflicht (IPS)", "Active Share ausserhalb des IPS-Bands", f"{_d(ad['active_share'], 1)} %", f"{_d(lo, 0)}–{_d(hi, 0)} %",
                "Titelauswahl zu nah bzw. zu weit vom S&P 500.", "Gewichte Richtung Index bzw. aktiver")
    except Exception:  # noqa: BLE001
        pass
    # offene Monitoring-Fälle
    try:
        import monitoring as M

        f = M.lade_faelle(ctx.tabellen.get("faelle"))
        f = f[f["status"].isin(M.OFFEN)] if len(f) else f
        for _, r in f.head(15).iterrows():
            add("Monitoring", f"Offener Fall: {r['titel']}", _txt(r.get("stufe")), "", _txt(r.get("text"))[:160],
                "im Monitoring entscheiden; ggf. hier umsetzen")
    except Exception:  # noqa: BLE001
        pass
    out.sort(key=lambda x: PRIO.index(x["prio"]))
    return out


def empfehlung(diag: list[dict], Z: dict, monat_id: str, letzte: list[dict]) -> tuple[str, str]:
    """(Methode, Begründung)"""
    n = {p: sum(1 for d in diag if d["prio"] == p) for p in PRIO}
    neue_taktik = Z.get("hat_taktik") and not any(x.get("monat") == monat_id for x in letzte if x.get("aktiv"))
    if neue_taktik and (n["Taktik"] or n["Pflicht (IPS)"] or n["Sektoren"]):
        return "voll", (f"Für {C.MONAT_BY_ID[monat_id]['name']} gilt eine neue Taktik und es gibt Abweichungen – am Monatsbeginn alles auf "
                        "das Ziel bringen (Kalender-Rebalancing). Ohne Transaktionskosten ist das die sauberste Umsetzung.")
    if n["Taktik"] or n["Sektoren"] or n["Anleihen"]:
        return "toleranz", ("Einzelne Anlageklassen, Sektoren oder die Duration liegen ausserhalb der Toleranz – nur diese zurück aufs "
                            "Ziel bringen (Korridor-Rebalancing), den Rest laufen lassen.")
    if n["Pflicht (IPS)"]:
        return "pflicht", "Nur IPS-Grenzen sind verletzt – diese gezielt beheben."
    return "keins", ("Alles innerhalb der Toleranzen – kein Rebalancing nötig. Handeln ohne Auslöser erhöht nur den Umschlag "
                     "und kostet bei Trendmärkten Rendite.")


# ---------------------------------------------------------------------------
# Handelsplan
# ---------------------------------------------------------------------------
def _verteilen_mit_deckel(werte: dict, summe: float, deckel: float) -> tuple[dict, float]:
    """Summe pro rata zu den bisherigen Werten verteilen, je Titel höchstens «deckel» (USD). Gibt (Werte, nicht verteilbar)."""
    w = {k: max(v, 0.0) for k, v in werte.items()}
    basis = sum(w.values())
    if basis <= 0:
        n = len(w)
        w = {k: 1.0 for k in w} if n else {}
        basis = float(n)
    ziel = {k: summe * v / basis for k, v in w.items()} if basis else {}
    for _ in range(50):
        ueber = {k: v - deckel for k, v in ziel.items() if v > deckel + 1e-6}
        if not ueber:
            break
        rest = sum(ueber.values())
        for k in ueber:
            ziel[k] = deckel
        frei = {k: v for k, v in ziel.items() if v < deckel - 1e-6}
        fb = sum(w[k] for k in frei)
        if fb <= 0:
            return ziel, rest
        for k in frei:
            ziel[k] += rest * w[k] / fb
    return ziel, 0.0


def _neue_titel(sektor: str, betrag: float, vorhanden: set, n: int = 3) -> dict:
    """Grösste Indextitel eines Sektors (Benchmark-Datei), Betrag nach Indexgewicht verteilt."""
    try:
        import benchmark as BM

        d = BM.aktuell()
        t = d["titel"]
        t = t[(t["sektor"] == sektor) & ~t["ticker"].isin(vorhanden)].sort_values("w_alt", ascending=False).head(n)
        if not len(t):
            return {}
        w = t["w_alt"] / t["w_alt"].sum()
        return {tk: betrag * float(x) for tk, x in zip(t["ticker"], w)}
    except Exception:  # noqa: BLE001
        return {}


def plan(ctx, B: dict, Z: dict, methode: str, tol: dict, opt: dict, analyse_eingabe: dict | None = None) -> dict:
    """Ziel je Position und Handelsliste. Preise = Vorschaukurse (letzter Schlusskurs bzw. Modellkurs der Anleihe)."""
    import portfolio as P

    ips = ctx.ips
    total = B["total"]
    ak, tr = B["ak"], B["tr"]
    aw, tw = float(ak["wert"].sum()), float(tr["wert"].sum())
    ist = {"aktien": aw / total * 100, "anleihen": tw / total * 100, "cash": B["cash"] / total * 100}
    gruende: dict[str, list[str]] = {}

    def grund(k, txt):
        gruende.setdefault(k, [])
        if txt not in gruende[k]:
            gruende[k].append(txt)

    kurs_ak = {r["ticker"]: float(r["kurs"]) for _, r in ak.iterrows()}
    ziel_ak, ziel_tr = {}, {}
    neu_info = {}
    if methode == "analyse" and analyse_eingabe:
        # Ziel = Analyseportfolio: Stück bzw. Betrag je Titel, Treasuries über Kupon + Fälligkeit
        for a in analyse_eingabe.get("aktien", []):
            t = _txt(a.get("Ticker")).upper().replace(".", "-")
            if not t:
                continue
            menge, betrag = zahl(a.get("Menge")), zahl(a.get("Betrag USD"))
            k = kurs_ak.get(t)
            if k is None:
                s = B["kurse"].get(t)
                if s is None:
                    try:
                        import analyse as A

                        k = A._letzte_kurse([t]).get(t)
                    except Exception:  # noqa: BLE001
                        k = None
                else:
                    k = float(s.iloc[-1])
                if k:
                    kurs_ak[t] = k
                    info = P.ticker_info(t)
                    neu_info[t] = dict(name=info.get("name", ""), sektor=info.get("sektor") or "Unbekannt")
            if not k:
                continue
            ziel_ak[t] = (menge * k) if menge else (betrag or 0.0)
            grund(t, "Ziel aus dem Analyseportfolio")
        for t in kurs_ak:
            if t not in ziel_ak and t in set(ak["ticker"]):
                ziel_ak[t] = 0.0
                grund(t, "nicht im Analyseportfolio → verkaufen")
        for _, r in tr.iterrows():
            ziel_tr[r["ticker"]] = 0.0
        for z in analyse_eingabe.get("treas", []):
            kup, fae, bet = zahl(z.get("Kupon %")), pd.to_datetime(z.get("Fälligkeit"), errors="coerce"), zahl(z.get("Betrag USD"))
            if not bet:
                continue
            treffer = [r["ticker"] for _, r in tr.iterrows() if kup is not None and pd.notna(fae) and abs((r["kupon"] or 0) - kup) < 1e-6
                       and pd.notna(r["faelligkeit"]) and abs((r["faelligkeit"] - fae).days) <= 3]
            if treffer:
                ziel_tr[treffer[0]] = bet
                grund(treffer[0], "Ziel aus dem Analyseportfolio")
        for t, v in ziel_tr.items():
            if v == 0.0:
                grund(t, "nicht im Analyseportfolio")
        if any(zahl(z.get("Betrag USD")) for z in analyse_eingabe.get("treas", [])) and not any(v > 0 for v in ziel_tr.values()):
            grund("_info", "Treasuries des Analyseportfolios passen zu keiner gehaltenen Anleihe (Kupon + Fälligkeit) – neue Anleihen "
                           "über «Analyseportfolio ins Portfolio übernehmen» bzw. die Seite Portfolio buchen.")
    else:
        # 1) Anlageklassen
        z_kl = {}
        for k, name in KLASSEN:
            lo, hi = ips[f"{k}_min"], ips[f"{k}_max"]
            if methode == "voll":
                z_kl[k] = Z[k]
            elif methode == "toleranz":
                aus = abs(ist[k] - Z[k]) > tol["klasse"] + 1e-9 or not lo <= ist[k] <= hi
                z_kl[k] = Z[k] if aus else ist[k]
            else:   # pflicht
                if ist[k] < lo:
                    z_kl[k] = min(lo + PUFFER, Z[k]) if Z[k] > lo else lo + PUFFER
                elif ist[k] > hi:
                    z_kl[k] = max(hi - PUFFER, Z[k]) if Z[k] < hi else hi - PUFFER
                elif abs(ist[k] - Z[k]) > float(ips.get("rebal_toleranz") or 5):
                    z_kl[k] = Z[k]
                else:
                    z_kl[k] = ist[k]
        # Cash = Rest; liegt er ausserhalb von Band oder Toleranz, Aktien und Treasuries aufs Ziel
        c_rest = 100 - z_kl["aktien"] - z_kl["anleihen"]
        lo_c, hi_c = ips["cash_min"], ips["cash_max"]
        if methode != "pflicht" and (abs(c_rest - Z["cash"]) > tol["klasse"] + 1e-9 or not lo_c <= c_rest <= hi_c):
            frei = c_rest - Z["cash"]          # zu viel Cash → in die Klassen mit Abstand zum Ziel bzw. pro rata Taktik
            z_kl["aktien"] += frei * Z["aktien"] / (Z["aktien"] + Z["anleihen"])
            z_kl["anleihen"] += frei * Z["anleihen"] / (Z["aktien"] + Z["anleihen"])
            grund("_info", f"Cash {_d(c_rest, 2)} % lag ausserhalb der Toleranz – auf die Taktik ({_d(Z['cash'], 1)} %) gebracht.")
        elif methode == "pflicht" and not lo_c <= c_rest <= hi_c:
            frei = c_rest - min(max(Z["cash"], lo_c + PUFFER), hi_c - PUFFER)
            z_kl["aktien"] += frei * Z["aktien"] / (Z["aktien"] + Z["anleihen"])
            z_kl["anleihen"] += frei * Z["anleihen"] / (Z["aktien"] + Z["anleihen"])
        E_neu, T_neu = z_kl["aktien"] / 100 * total, z_kl["anleihen"] / 100 * total
        kl_grund = {"aktien": f"Aktienquote {_d(ist['aktien'], 2)} → {_d(z_kl['aktien'], 2)} %",
                    "anleihen": f"Treasury-Quote {_d(ist['anleihen'], 2)} → {_d(z_kl['anleihen'], 2)} %"}
        # 2) Sektoren
        sw = _sektor_werte(ak)
        ist_s = {s: (sw[s] / aw * 100 if aw else 0.0) for s in C.SEKTOREN}
        smax = ips.get("sektor_abw_max") or 99
        anteil = {}
        fest = set()
        for s in C.SEKTOREN:
            zs = Z["sektoren"][s]
            if methode == "voll":
                anteil[s] = zs
                fest.add(s)
            elif methode == "toleranz":
                if abs(ist_s[s] - zs) > tol["sektor"] + 1e-9 or abs(ist_s[s] - Z["bm_sek"].get(s, 0)) > smax:
                    anteil[s] = zs
                    fest.add(s)
                else:
                    anteil[s] = ist_s[s]
            else:
                bm = Z["bm_sek"].get(s, 0.0)
                if abs(ist_s[s] - bm) > smax:
                    anteil[s] = zs if abs(zs - bm) <= smax else bm + math.copysign(smax - PUFFER / 2, ist_s[s] - bm)
                    fest.add(s)
                else:
                    anteil[s] = ist_s[s]
        rest_soll = 100 - sum(anteil[s] for s in fest)
        lose = [s for s in C.SEKTOREN if s not in fest]
        lose_sum = sum(anteil[s] for s in lose)
        if lose and lose_sum > 0:
            for s in lose:
                anteil[s] = anteil[s] * rest_soll / lose_sum
        elif fest:
            f_sum = sum(anteil[s] for s in fest)
            for s in fest:
                anteil[s] = anteil[s] * 100 / f_sum if f_sum else anteil[s]
        # 3) Titel je Sektor
        tmax_usd = (float(ips.get("titel_max") or 100) - 0.1) / 100 * total
        aus_t = {x.strip().upper() for x in str(ips.get("aus_titel") or "").replace(";", ",").split(",") if x.strip()}
        aus_s = set(ips.get("aus_sektoren") or [])
        try:
            import benchmark as BM

            im_index = set(BM.gewichte().index)
        except Exception:  # noqa: BLE001
            im_index = set()
        raus = {r["ticker"] for _, r in ak.iterrows() if r["ticker"] in aus_t or r["sektor"] in aus_s
                or (im_index and r["ticker"] not in im_index)}
        for t in raus:
            ziel_ak[t] = 0.0
            grund(t, "laut IPS nicht erlaubt (Mandat/Ausschluss) → verkaufen")
        rest_unverteilt = 0.0
        for s in C.SEKTOREN + ["Unbekannt"]:
            teil = ak[(ak["sektor"] == s) & ~ak["ticker"].isin(raus)]
            soll = E_neu * anteil.get(s, 0.0) / 100 if s in C.SEKTOREN else float(teil["wert"].sum())
            werte = dict(zip(teil["ticker"], teil["wert"].astype(float)))
            ist_sum = sum(werte.values())
            if not werte:
                if soll > 0.002 * total and methode in ("voll", "toleranz") and opt.get("neue_titel", True):
                    neu = _neue_titel(s, soll, set(ak["ticker"]))
                    for t, v in neu.items():
                        ziel_ak[t] = v
                        k = B["kurse"].get(t)
                        try:
                            import analyse as A

                            kurs_ak[t] = float(k.iloc[-1]) if k is not None and len(k) else A._letzte_kurse([t]).get(t)
                        except Exception:  # noqa: BLE001
                            kurs_ak[t] = None
                        info = P.ticker_info(t)
                        neu_info[t] = dict(name=info.get("name", ""), sektor=s)
                        grund(t, f"{s}: Sektor ohne Titel → grösste Indextitel")
                else:
                    rest_unverteilt += soll
                continue
            neu, rest = _verteilen_mit_deckel(werte, soll, tmax_usd)
            rest_unverteilt += rest
            for t, v in neu.items():
                ziel_ak[t] = v
                if abs(v - werte[t]) > 1:
                    if werte[t] > tmax_usd + 1 and v <= tmax_usd + 1:
                        grund(t, "über dem Titel-Maximum → gekürzt")
                    if s in fest and abs(soll - ist_sum) > 1:
                        grund(t, f"{s}: Sektor {_d(ist_s.get(s), 2)} → {_d(anteil[s], 2)} % des Aktienteils")
                    if abs(z_kl["aktien"] - ist["aktien"]) > 1e-9:
                        grund(t, kl_grund["aktien"])
                    if not gruende.get(t):
                        grund(t, "Sektor gleicht aus (Summe der Sektoren = 100 %)")
        if rest_unverteilt > 1:
            grund("_info", f"{_d(rest_unverteilt, 0)} USD liessen sich wegen Titel-Maximum bzw. fehlender Titel nicht verteilen – bleiben Cash.")
        # 4) Treasuries pro rata, Duration optional
        if len(tr):
            werte_t = dict(zip(tr["ticker"], tr["wert"].astype(float)))
            neu_t = {t: T_neu * v / tw for t, v in werte_t.items()} if tw else {}
            D = duration(tr)
            if D is not None and (methode == "voll" or (opt.get("duration", True) and abs(D - Z["duration"]) > tol["duration"])
                                  or not ips["duration_min"] <= D <= ips["duration_max"]) and len(tr) >= 2:
                dur = dict(zip(tr["ticker"], tr["duration"].astype(float)))
                kurz, lang = min(dur, key=dur.get), max(dur, key=dur.get)
                if dur[lang] - dur[kurz] > 0.1 and T_neu > 0:
                    x = (Z["duration"] - D) * T_neu / (dur[lang] - dur[kurz])      # USD von kurz nach lang
                    x = max(-neu_t[lang], min(neu_t[kurz], x))
                    neu_t[kurz] -= x
                    neu_t[lang] += x
                    for t in (kurz, lang):
                        grund(t, f"Duration {_d(D, 2)} → {_d(Z['duration'], 1)} J. (Umschichtung kurz ↔ lang)")
            for t, v in neu_t.items():
                ziel_tr[t] = v
                if abs(v - werte_t[t]) > 1 and abs(z_kl["anleihen"] - ist["anleihen"]) > 1e-9:
                    grund(t, kl_grund["anleihen"])
        elif T_neu > 0.005 * total:
            grund("_info", "Keine Treasuries im Bestand – neue Anleihen über die Seite Portfolio bzw. die Analyse (Übernahme) kaufen.")

    # --- Handelsliste in ganzen Stücken / Nominal auf 100 USD --------------------------------------------
    zeilen = []
    for _, r in ak.iterrows():
        t = r["ticker"]
        zeilen.append(dict(klasse="Aktie", ticker=t, name=_txt(r.get("name")), sektor=r["sektor"], kupon=None, faellig=None,
                           kurs=float(r["kurs"]), ist_menge=float(r["menge"]), ist_wert=float(r["wert"]),
                           ziel_wert=float(ziel_ak.get(t, r["wert"]))))
    for t, v in ziel_ak.items():
        if t not in set(ak["ticker"]) and kurs_ak.get(t):
            zeilen.append(dict(klasse="Aktie", ticker=t, name=neu_info.get(t, {}).get("name", ""),
                               sektor=neu_info.get(t, {}).get("sektor", "Unbekannt"), kupon=None, faellig=None, kurs=float(kurs_ak[t]),
                               ist_menge=0.0, ist_wert=0.0, ziel_wert=float(v)))
    for _, r in tr.iterrows():
        t = r["ticker"]
        zeilen.append(dict(klasse="Treasury", ticker=t, name=_txt(r.get("name")), sektor="Treasury", kupon=r["kupon"],
                           faellig=r["faelligkeit"], kurs=float(r["kurs"]), ist_menge=float(r["menge"]), ist_wert=float(r["wert"]),
                           ziel_wert=float(ziel_tr.get(t, r["wert"])), duration=float(r["duration"] or 0)))
    df = pd.DataFrame(zeilen)
    if not len(df):
        return dict(trades=df, info=gruende.get("_info", []), total=total)
    faktor = np.where(df["klasse"] == "Treasury", 0.01, 1.0)
    roh = df["ziel_wert"] / (df["kurs"] * faktor)
    ziel_m = np.where(df["klasse"] == "Treasury", np.floor(roh / 100) * 100, np.floor(roh + 1e-9))
    # Verkäufe auf ganze Stück aufrunden nicht nötig; Kaufe abgerundet → Cash bleibt ≥ 0
    df["ziel_menge"] = np.where(df["ziel_wert"] <= 0.5, 0.0, ziel_m)
    df["delta_menge"] = df["ziel_menge"] - df["ist_menge"]
    df["delta_wert"] = df["delta_menge"] * df["kurs"] * faktor
    df["grund"] = [" · ".join(gruende.get(t, [])) for t in df["ticker"]]
    # Mindest-Handelsgrösse
    mini = float(opt.get("min_trade", 0.0)) / 100 * total
    pflicht = df["ziel_menge"].eq(0) & df["ist_menge"].gt(0)
    klein = df["delta_wert"].abs() < mini
    df.loc[klein & ~pflicht, ["ziel_menge", "delta_menge", "delta_wert"]] = [np.nan, 0.0, 0.0]
    df["ziel_menge"] = df["ziel_menge"].fillna(df["ist_menge"])
    df["art"] = np.where(df["delta_menge"] > 0, "Kauf", np.where(df["delta_menge"] < 0, "Verkauf", "–"))
    cash_nach = B["cash"] - float(df["delta_wert"].sum())
    # Cash darf nicht negativ werden: grösste Käufe kürzen
    if cash_nach < 0:
        for i in df[df["delta_wert"] > 0].sort_values("delta_wert", ascending=False).index:
            if cash_nach >= 0:
                break
            f = 0.01 if df.at[i, "klasse"] == "Treasury" else 1.0
            schritt = 100 if df.at[i, "klasse"] == "Treasury" else 1
            n = min(df.at[i, "delta_menge"], math.ceil(-cash_nach / (df.at[i, "kurs"] * f * schritt)) * schritt)
            df.at[i, "delta_menge"] -= n
            df.at[i, "ziel_menge"] -= n
            df.at[i, "delta_wert"] = df.at[i, "delta_menge"] * df.at[i, "kurs"] * f
            cash_nach = B["cash"] - float(df["delta_wert"].sum())
        df["art"] = np.where(df["delta_menge"] > 0, "Kauf", np.where(df["delta_menge"] < 0, "Verkauf", "–"))
    df["ziel_wert_ist"] = df["ziel_menge"] * df["kurs"] * faktor
    return dict(trades=df.reset_index(drop=True), info=gruende.get("_info", []), total=total, cash_nach=cash_nach,
                methode=methode, tol=tol, opt=opt)


# ---------------------------------------------------------------------------
# Vorher / Nachher
# ---------------------------------------------------------------------------
def _pos_analyse(df: pd.DataFrame, spalte_menge: str) -> pd.DataFrame:
    """Handelsliste → Positionen im Format von analyse.rechne (Treasuries als ETF-Näherung je Laufzeitband)."""
    import analyse as A

    zeilen = []
    for _, r in df.iterrows():
        m = float(r[spalte_menge] or 0)
        if m <= 0:
            continue
        if r["klasse"] == "Aktie":
            zeilen.append(dict(ticker=r["ticker"], klasse="Aktie", menge=m, kurs=r["kurs"], wert=m * r["kurs"], band=None))
        else:
            T = max(0.0, (pd.Timestamp(r["faellig"]) - pd.Timestamp.today()).days / 365.25) if pd.notna(r["faellig"]) else 5.0
            band = A.baender(T, r["ticker"])
            zeilen.append(dict(ticker=A.PROXY[band][0], klasse="Treasury", menge=None, kurs=None, wert=m * r["kurs"] / 100, band=band))
    pos = pd.DataFrame(zeilen, columns=["ticker", "klasse", "menge", "kurs", "wert", "band"])
    if len(pos):
        pos = pos.groupby(["ticker", "klasse"], as_index=False, dropna=False).agg(
            menge=("menge", "sum"), kurs=("kurs", "last"), wert=("wert", "sum"), band=("band", "first"))
    return pos


def vergleich(ctx, B: dict, pl: dict, Z: dict) -> dict:
    """Kennzahlen und IPS-Gap vorher/nachher (gleiche Kursgeschichte 1 Jahr)."""
    import analyse as A
    import ips_abgleich as IA
    import portfolio as P
    import sektoren as SK

    df = pl["trades"]
    total = pl["total"]
    out = {}
    tick = sorted(set(df.loc[df["klasse"] == "Aktie", "ticker"]))
    proxies = sorted({v[0] for v in A.PROXY.values()})
    hist, _, _ = A.lade_historie(tuple(tick + proxies + [A.SPX, A.NEUTRAL_PROXY, A.RF_TICKER]), 1)
    info = {t: {**P.ticker_info(t), "sektor": s} for t, s in zip(df["ticker"], df["sektor"]) if s != "Treasury"}
    sa = SK.lade_allokation(ctx.tabellen.get("sektor_allokation"))["sektoren"]
    for name, sp, cash in (("vorher", "ist_menge", B["cash"]), ("nachher", "ziel_menge", pl["cash_nach"])):
        pos = _pos_analyse(df, sp)
        d_ = df[df["klasse"] == "Treasury"].copy()
        d_["w"] = d_[sp] * d_["kurs"] / 100
        dur = float((d_["w"] * d_["duration"]).sum() / d_["w"].sum()) if len(d_) and d_["w"].sum() else None
        erg = A.rechne(pos, cash, total, hist, ctx.alloc["neutral"]) if len(pos) else None
        m = IA.messwerte_portfolio(ctx, erg, pos, total, cash, info, duration=dur) if erg else {}
        gap = IA.gap_analyse(ctx.ips, {**ctx.alloc, "aktien": Z["aktien"], "anleihen": Z["anleihen"], "cash": Z["cash"]}, m) if m else []
        a_ = df[(df["klasse"] == "Aktie") & (df[sp] > 0)]
        ak_df = pd.DataFrame({"ticker": a_["ticker"], "name": a_["name"], "sektor": a_["sektor"], "wert": a_[sp] * a_["kurs"]})
        sek = SK.pruefe_portfolio(ak_df, total, sa, ctx.sek_index["gewichte"], float(Z["aktien"]))
        grosster = (ak_df.sort_values("wert", ascending=False).iloc[0] if len(ak_df) else None)
        out[name] = dict(erg=erg, m=m, gap=gap, n_gap=IA.kurz(gap) if gap else {}, sek=sek, dur=dur, cash=cash,
                         aktien=float(ak_df["wert"].sum()) / total * 100, treas=float(d_["w"].sum()) / total * 100,
                         cash_pct=cash / total * 100, n=len(ak_df),
                         gross=(grosster["ticker"], float(grosster["wert"]) / total * 100) if grosster is not None else None)
    return out


def vergleich_tabelle(ctx, V: dict, Z: dict) -> pd.DataFrame:
    ips = ctx.ips

    def kv(x, k):
        e = V[x]["erg"]
        return (e["k"]["Vorauswahl"].get(k) if e else None)

    def ka(x, k):
        e = V[x]["erg"]
        return (e["k"].get("Aktienteil", {}).get(k) if e else None)

    def m(x, k):
        return V[x]["m"].get(k)
    z = [
        ("Aktien", f"{_d(V['vorher']['aktien'], 2)} %", f"{_d(V['nachher']['aktien'], 2)} %", f"Taktik {_d(Z['aktien'], 1)} % · IPS {ips['aktien_min']:g}–{ips['aktien_max']:g}"),
        ("Treasuries", f"{_d(V['vorher']['treas'], 2)} %", f"{_d(V['nachher']['treas'], 2)} %", f"Taktik {_d(Z['anleihen'], 1)} % · IPS {ips['anleihen_min']:g}–{ips['anleihen_max']:g}"),
        ("Cash", f"{_d(V['vorher']['cash_pct'], 2)} %", f"{_d(V['nachher']['cash_pct'], 2)} %", f"Taktik {_d(Z['cash'], 1)} % · IPS {ips['cash_min']:g}–{ips['cash_max']:g}"),
        ("Duration Treasuries", f"{_d(V['vorher']['dur'], 2)} J.", f"{_d(V['nachher']['dur'], 2)} J.", f"Taktik {_d(Z['duration'], 1)} J. · IPS {_d(ips['duration_min'], 0)}–{_d(ips['duration_max'], 0)}"),
        ("Sektoren im Ziel", f"{V['vorher']['sek']['n'][L.GRUEN]} / 11", f"{V['nachher']['sek']['n'][L.GRUEN]} / 11", "±0,5 Pp. grün"),
        ("Σ |Ist − Ziel| Sektoren", f"{_d(V['vorher']['sek']['summe_abw'], 2)} Pp.", f"{_d(V['nachher']['sek']['summe_abw'], 2)} Pp.", "kleiner = näher am Plan"),
        ("Grösster Titel", f"{V['vorher']['gross'][0]} {_d(V['vorher']['gross'][1], 2)} %" if V["vorher"]["gross"] else "–",
         f"{V['nachher']['gross'][0]} {_d(V['nachher']['gross'][1], 2)} %" if V["nachher"]["gross"] else "–", f"IPS ≤ {_d(ips.get('titel_max'), 1)} %"),
        ("Anzahl Aktien", str(V["vorher"]["n"]), str(V["nachher"]["n"]), f"IPS ≥ {_d(ips.get('aktien_anzahl_min'), 0)}"),
        ("Active Share Aktien", f"{_d(ka('vorher', 'active_share'), 1)} %", f"{_d(ka('nachher', 'active_share'), 1)} %",
         f"IPS {_d(ips.get('active_share_min'), 0)}–{_d(ips.get('active_share_max'), 0)} %"),
        ("Tracking Error ex ante", f"{_d(m('vorher', 'te_ea'), 2)} %", f"{_d(m('nachher', 'te_ea'), 2)} %", f"IPS ≤ {_d(ips.get('te_max'), 1)} %"),
        ("Tracking Error historisch (12 Mt.)", f"{_d(kv('vorher', 'te_bench'), 2)} %", f"{_d(kv('nachher', 'te_bench'), 2)} %", "heutige Gewichte, vergangene Kurse"),
        ("Volatilität ex ante", f"{_d(m('vorher', 'vola_ea'), 1)} %", f"{_d(m('nachher', 'vola_ea'), 1)} %", f"IPS ≤ {_d(ips.get('vola_max'), 0)} %"),
        ("ES 95 % bis Saisonende", f"{_d(-m('vorher', 'es95_3') if m('vorher', 'es95_3') is not None else None, 1)} %",
         f"{_d(-m('nachher', 'es95_3') if m('nachher', 'es95_3') is not None else None, 1)} %", f"tragbar −{_d(ips.get('verlust_max'), 0)} %"),
        ("Erwartete Rendite bis Saisonende", f"{_d(m('vorher', 'e3'), 2, True)} %", f"{_d(m('nachher', 'e3'), 2, True)} %",
         f"erforderlich {_d(ips.get('rendite_min'), 1)} % · Mehrrendite vs. Benchmark {_d(m('nachher', 'akt3'), 2, True)} Pp."),
        ("Beta Aktienteil", _d(ka("vorher", "beta"), 2), _d(ka("nachher", "beta"), 2), f"IPS {_d(ips.get('beta_min'), 2)}–{_d(ips.get('beta_max'), 2)}"),
        ("IPS-Prüfungen verletzt", str(V["vorher"]["n_gap"].get(L.ROT, "–")), str(V["nachher"]["n_gap"].get(L.ROT, "–")),
         "Gap-Analyse (rot)"),
    ]
    return pd.DataFrame(z, columns=["Kennzahl", "Vorher", "Nachher", "Ziel / Grenze"])


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
REGELN_TEXT = (
    "Rebalancing-Regeln des Teams (nach CFA Level III): (1) Pflicht – IPS-Grenzen (Bänder, Rebalancing-Toleranz, Titel-Maximum, "
    "Duration, Sektorabweichung, Active Share, Mandat) werden sofort behoben. (2) Kalender – am Monatsbeginn, wenn die neue Taktik "
    "gilt, wird das Portfolio auf die Taktik gebracht. (3) Korridor – zwischen den Terminen wird nur gehandelt, was ausserhalb der "
    "Toleranz liegt (Anlageklassen, Sektoren, Duration), und dann zurück aufs Ziel. Weil die Simulation weder Transaktionskosten "
    "noch Steuern kennt, wird aufs Ziel statt an den Bandrand rebalanciert; eine Mindest-Handelsgrösse verhindert Kleinst-Trades. "
    "Innerhalb der Sektoren bleiben die Titel im Verhältnis (die Titelauswahl ist ein eigener Entscheid), Gewinner werden nur "
    "gekürzt, wenn ein Limit oder ein Ziel es verlangt (Momentum nicht ohne Grund brechen). Im letzten Monat werden "
    "unbeabsichtigte Wetten abgebaut, weil sie bis zum Saisonende kaum noch Ertrag bringen können.")


def report(ctx, B: dict, Z: dict, monat_id: str, diag: list[dict], pl: dict, V: dict | None, doku: dict, entwurf: bool,
           kurse_info: str = "", datum: pd.Timestamp | None = None):
    import seitenpdf as SP

    m = C.MONAT_BY_ID[monat_id]["name"]
    df = pl["trades"]
    t = df[df["delta_menge"] != 0]
    R = SP.Sammler(f"Rebalancing-Report {m}" + (" (Entwurf)" if entwurf else ""),
                   f"Bestand vom {B['datum']:%d.%m.%Y} · Methode: {METHODEN.get(pl.get('methode'), pl.get('methode'))} · "
                   + (f"Handelstag {datum:%d.%m.%Y}" if datum is not None else "noch nicht gebucht"),
                   kurz=f"Rebalancing {m}", quer=True)
    R.h("1. Ausgangslage")
    R.kpis([
        ("Portfoliowert", f"{ui.de(B['total'], 0)} USD", f"Start {ui.de(B['kapital'], 0)} USD · {ui.de((B['total'] / B['kapital'] - 1) * 100, 2, True)} %"),
        ("Aktien / Treasuries / Cash", f"{_d(V['vorher']['aktien'], 1)} / {_d(V['vorher']['treas'], 1)} / {_d(V['vorher']['cash_pct'], 1)} %" if V else "–",
         f"Ziel {_d(Z['aktien'], 1)} / {_d(Z['anleihen'], 1)} / {_d(Z['cash'], 1)} %"),
        ("Duration", f"{_d(duration(B['tr']), 2)} J.", f"Ziel {_d(Z['duration'], 1)} J."),
        ("Kupons + Zins auf Cash", f"{ui.de(B['kupons'] + B['zins'], 0)} USD", "seit Kauf, liegen als Cash"),
    ])
    R.text(f"Ziele: {Z['quelle']}. Sektorziele: {Z['sek_quelle']}.", klein=True)
    emp, emp_txt = empfehlung(diag, Z, monat_id, [])
    R.h2("Auf einen Blick")
    R.kpis([
        ("Auslöser", f"{sum(1 for d in diag if d['prio'] == 'Pflicht (IPS)')} Pflicht",
         f"{sum(1 for d in diag if d['prio'] in ('Taktik', 'Sektoren', 'Titel', 'Anleihen', 'Cash'))} über der Toleranz · "
         f"{sum(1 for d in diag if d['prio'] == 'Monitoring')} offene Monitoring-Fälle"),
        ("Methode", METHODEN.get(pl.get("methode"), "").split(" (")[0], "Empfehlung der App: "
         + ({"keins": "kein Rebalancing"}.get(emp) or METHODEN.get(emp, emp).split(" (")[0])),
        ("Trades", str(int((df["delta_menge"] != 0).sum())),
         f"Umschlag {ui.de(df['delta_wert'].abs().sum() / 2 / pl['total'] * 100, 1)} % · Cash danach {ui.de(pl['cash_nach'] / pl['total'] * 100, 2)} %"),
        ("IPS-Prüfungen verletzt", f"{V['vorher']['n_gap'].get(L.ROT, '–')} → {V['nachher']['n_gap'].get(L.ROT, '–')}" if V else "–",
         f"Sektoren im Ziel {V['vorher']['sek']['n'][L.GRUEN]} → {V['nachher']['sek']['n'][L.GRUEN]} von 11" if V else ""),
    ])
    R.text(f"Empfehlung: {emp_txt}", klein=True)
    R.text(f"Begründung des Teams: {doku.get('begruendung') or '(noch leer)'}", klein=True)
    R.h("2. Auslöser (Diagnose)")
    if diag:
        R.tabelle(pd.DataFrame([{"Priorität": d["prio"], "Thema": d["thema"], "Ist": d["ist"], "Ziel / Grenze": d["ziel"],
                                 "Befund": d["text"], "Massnahme": d["massnahme"]} for d in diag]),
                  breiten=[28, 55, 26, 34, 75, 45], max_zeilen=60,
                  farben=[[{L.ROT: (253, 241, 240), L.GELB: (253, 246, 231), L.GRUEN: (237, 247, 241)}.get(PRIO_STUFE[d["prio"]])]
                          + [None] * 5 for d in diag[:60]])
    else:
        R.text("Keine Auslöser – alles innerhalb der Toleranzen.")
    R.h("3. Methode und Regeln")
    tol = pl.get("tol") or {}
    opt = pl.get("opt") or {}
    R.text(f"Gewählt: {METHODEN.get(pl.get('methode'), pl.get('methode'))}. Toleranzen: Anlageklassen ±{_d(tol.get('klasse'), 1)} Pp., "
           f"Sektoren ±{_d(tol.get('sektor'), 1)} Pp. des Aktienteils, Duration ±{_d(tol.get('duration'), 1)} J. · Mindest-Handelsgrösse "
           f"{_d(opt.get('min_trade'), 2)} % des Portfolios · Sektoren ohne Titel: {'grösste Indextitel kaufen' if opt.get('neue_titel', True) else 'nicht auffüllen'}.")
    R.text(REGELN_TEXT, klein=True)
    for i_ in pl.get("info", []):
        R.text(f"Hinweis: {i_}", klein=True)
    if V:
        R.neue_seite()
        R.h("4. Vorher / Nachher")
        R.tabelle(vergleich_tabelle(ctx, V, Z), breiten=[60, 45, 45, 110])
        R.h2("Sektoren vorher → nachher (Anteil am Aktienteil)")
        sv, sn = V["vorher"]["sek"]["sektoren"].set_index("Sektor"), V["nachher"]["sek"]["sektoren"].set_index("Sektor")
        R.tabelle(pd.DataFrame([{"Sektor": s, "Ziel": sv.loc[s, "Ziel"], "Vorher": sv.loc[s, "Ist"], "Nachher": sn.loc[s, "Ist"],
                                 "Ist − Ziel vorher": sv.loc[s, "Ist − Ziel"], "Ist − Ziel nachher": sn.loc[s, "Ist − Ziel"],
                                 "Prüfung nachher": str(sn.loc[s, "Prüfung"]).replace("● ", "")} for s in C.SEKTOREN]),
                  farben=[[None] * 6 + [{L.ROT: (253, 241, 240), L.GELB: (253, 246, 231), L.GRUEN: (237, 247, 241)}.get(sn.loc[s, "_stufe"])]
                          for s in C.SEKTOREN])
    R.neue_seite()
    R.h(f"5. Trades ({len(t)}) · Verkäufe {ui.de(-t.loc[t['delta_wert'] < 0, 'delta_wert'].sum(), 0)} USD · Käufe "
        f"{ui.de(t.loc[t['delta_wert'] > 0, 'delta_wert'].sum(), 0)} USD")
    if kurse_info:
        R.text(kurse_info, klein=True)
    if len(t):
        t = t.assign(_o=np.where(t["delta_menge"] < 0, 0, 1)).sort_values(["_o", "klasse", "sektor", "ticker"])
        mit_b = "begruendung" in t and t["begruendung"].astype(str).str.strip().ne("").any()
        R.tabelle(pd.DataFrame({
            "Art": t["art"], "Titel": t["ticker"], "Sektor": t["sektor"], "Ist": t["ist_menge"].map(lambda x: ui.de(x, 0)),
            "Ziel": t["ziel_menge"].map(lambda x: ui.de(x, 0)), "Δ Stück/Nominal": t["delta_menge"].map(lambda x: ui.de(x, 0, True)),
            "Kurs": t["kurs"].map(lambda x: ui.de(x, 2)), "Betrag USD": t["delta_wert"].map(lambda x: ui.de(x, 0, True)),
            "Grund": t["grund"], **({"Begründung": t["begruendung"]} if mit_b else {})}),
            breiten=[16, 22, 26, 20, 20, 22, 18, 24, 120] + ([50] if mit_b else []), max_zeilen=300)
    R.text(f"Cash danach {ui.de(pl['cash_nach'], 0)} USD ({_d(pl['cash_nach'] / pl['total'] * 100, 2)} %). Stück ganz, Nominal auf 100 USD "
           "abgerundet; Verkäufe werden vor den Käufen gebucht.", klein=True)
    if V and V["nachher"]["gap"]:
        R.neue_seite()
        _gap_pdf(R, V)
    R.h("7. Entscheid und Dokumentation")
    R.tabelle(pd.DataFrame([("Titel", doku.get("titel", "")), ("Begründung", doku.get("begruendung", "")),
                            ("Gegenargument / Risiko", doku.get("gegen", "")), ("Quellen", f"{doku.get('q1', '')}; {doku.get('q2', '')}"),
                            ("Beteiligte", ", ".join(doku.get("beteiligte") or [])), ("Erstellt von", f"{ctx.person}, {datetime.now():%d.%m.%Y %H:%M}"),
                            ("Status", "Entwurf – noch nicht gebucht" if entwurf else "gebucht")], columns=["", "Inhalt"]), breiten=[45, 215])
    return R


def _gap_pdf(R, V):
    import ips_abgleich as IA

    gap = V["nachher"]["gap"]
    n = IA.kurz(gap)
    hell = {L.ROT: (253, 241, 240), L.GELB: (253, 246, 231), L.GRUEN: (237, 247, 241)}
    R.h(f"6. IPS gegen Portfolio nach dem Rebalancing · {n[L.GRUEN]} eingehalten, {n[L.GELB]} knapp, {n[L.ROT]} verletzt "
        f"(vorher {V['vorher']['n_gap'].get(L.ROT, '–')} verletzt)")
    df = pd.DataFrame(gap)
    R.tabelle(df.assign(Status=[IA.STATUS_TEXT[z["Status"]] for z in gap]).drop(columns=["Hinweis", "Was es fürs Risiko heisst"]),
              breiten=[45, 60, 60, 75, 25], farben=[[None] * 4 + [hell.get(z["Status"])] for z in gap])


# ---------------------------------------------------------------------------
# Buchen
# ---------------------------------------------------------------------------
def buchungen(trades: pd.DataFrame, datum: pd.Timestamp, batch: str, person: str, monat_name: str, begr_alle: str) -> list[dict]:
    z = jetzt()
    t = trades[trades["delta_menge"] != 0].copy()
    t["_o"] = np.where(t["delta_menge"] < 0, 0, 1)           # Verkäufe zuerst
    out = []
    for n, (_, r) in enumerate(t.sort_values(["_o", "klasse", "ticker"]).iterrows(), start=1):
        notiz = f"Rebalancing {monat_name}: " + (_txt(r.get("begruendung")) or _txt(r.get("grund")) or "gemäss Report")
        if begr_alle.strip() and not _txt(r.get("begruendung")):
            notiz += f" – {begr_alle.strip()[:300]}"
        tr = r["klasse"] == "Treasury"
        out.append(dict(zeit=z, person=person, id=f"rb{batch}{n:03d}", datum=datum.date().isoformat(),
                        art="Kauf" if r["delta_menge"] > 0 else "Verkauf", klasse=r["klasse"], ticker=r["ticker"],
                        name=_txt(r.get("name")), sektor="" if tr else _txt(r.get("sektor")), menge=float(abs(r["delta_menge"])),
                        kurs=round(float(r["kurs_buchung"] if pd.notna(r.get("kurs_buchung")) else r["kurs"]), 4),
                        kupon=float(r["kupon"]) if tr and r["kupon"] is not None else "",
                        faelligkeit=pd.Timestamp(r["faellig"]).date().isoformat() if tr and pd.notna(r["faellig"]) else "",
                        notiz=notiz[:900], storniert=""))
    return out


def buchungskurse(ctx, fred: dict, B: dict, trades: pd.DataFrame, datum: pd.Timestamp) -> tuple[pd.DataFrame, list[str], str]:
    """Aktien: Eröffnungskurs am Handelstag (Yahoo); Treasuries: Modellkurs am Handelstag (Zinskurve, Spread vom Kauf)."""
    import portfolio as P
    import uebernahme as UE

    df = trades.copy()
    ak = tuple(sorted(df.loc[(df["klasse"] == "Aktie") & (df["delta_menge"] != 0), "ticker"]))
    opens, fehlt = UE.eroeffnungskurse(ak, datum.strftime("%Y-%m-%d")) if ak else ({}, [])
    b_t = P.bewerte({k: v for k, v in B["pos"].items() if v["klasse"] == "Treasury"}, {}, fred, datum)
    kt = dict(zip(b_t["ticker"], b_t["kurs"])) if len(b_t) else {}
    df["kurs_buchung"] = [opens.get(t) if k == "Aktie" else kt.get(t, ku) for t, k, ku in zip(df["ticker"], df["klasse"], df["kurs"])]
    df["kurs_quelle"] = ["Eröffnung" if (k == "Aktie" and t in opens) else ("Zinskurve" if k == "Treasury" else "fehlt")
                         for t, k in zip(df["ticker"], df["klasse"])]
    info = (f"Aktien zum Eröffnungskurs vom {datum:%d.%m.%Y} (Yahoo), Treasuries zum Modellkurs (FRED-Zinskurve, Spread vom Kauf).")
    return df, [t for t in fehlt if t in set(df.loc[df["delta_menge"] != 0, "ticker"])], info


# ---------------------------------------------------------------------------
# Seite
# ---------------------------------------------------------------------------
def seite_rebalancing(ctx, fred: dict, lade_kurse, team: list[str] | None = None):
    import monitoring as M
    import seitenpdf as SP
    import taktik as TK

    ss = st.session_state
    ui.seitenkopf("Rebalancing – Bestand anpassen (Januar / Februar)",
                  "Prüft den Bestand gegen Taktik, Sektorziele und IPS, schlägt die nötigen Trades vor, zeigt Vorher/Nachher und bucht mit "
                  "Begründung. Jede Umsetzung erzeugt einen Report (PDF) unter Dokumente und einen Eintrag im Entscheidungsprotokoll.")
    letzte = letzte_rebalancings(ctx.tabellen)
    mo = [m["id"] for m in C.MONATE]
    m_std = ctx.alloc.get("monat") if ctx.alloc.get("monat") in mo else TK.monat_in_arbeit()["id"]
    c1, c2, c3 = st.columns([1.2, 1.6, 1])
    monat = c1.selectbox("Für welchen Monat?", mo, index=mo.index(m_std), format_func=lambda i: C.MONAT_BY_ID[i]["name"], key="rb_monat",
                         help="Ziele = Taktik dieses Monats. Frist Taktik: Januar 04.01. 15:30, Februar 01.02. 15:30 – danach am "
                              "ersten Handelstag zum Eröffnungskurs umsetzen.")
    sq = c2.radio("Sektorziele aus", ["sektorallokation", "taktik"], horizontal=True, key="rb_sq",
                  format_func=lambda x: "Sektorallokation (Index heute + Abweichung)" if x == "sektorallokation" else "Sektorgewichten der Taktik",
                  help="Standard wie die Prüfung auf der Seite Portfolio: Index mit den Kursen fortgeschrieben + eure Abweichung. "
                       "«Taktik» = die beim Speichern der Taktik festgehaltenen Sektorgewichte.")
    c3.caption(f"Frist Taktik {C.MONAT_BY_ID[monat]['name']}: {pd.Timestamp(C.MONAT_BY_ID[monat]['frist']):%d.%m.%Y %H:%M}")
    B = bestand(ctx, fred, lade_kurse)
    if B is None:
        st.info("Noch kein Bestand – zuerst auf der Seite Analyse ein Portfolio übernehmen (Kauf am 01.12.).")
        return
    Z = ziele(ctx, monat, sq)
    (st.success if Z["hat_taktik"] else st.warning)(f"Ziele: {Z['quelle']} · Sektorziele: {Z['sek_quelle']}.",
                                                    icon=":material/flag:")
    s = M.schwellen(ctx.tabellen.get("einstellungen"))
    with st.expander("Welches Rebalancing ist sinnvoll? Regeln und Toleranzen", icon=":material/rule:"):
        st.markdown(
            "- **Pflicht (sofort):** IPS-Grenzen – Bänder, Rebalancing-Toleranz (±" + _d(ctx.ips.get("rebal_toleranz"), 1) + " Pp.), "
            "Titel-Maximum, Duration, Sektorabweichung gegen die Benchmark, Active Share, Mandat und Ausschlüsse.\n"
            "- **Kalender (Monatsbeginn):** Die Taktik für Januar bzw. Februar ist bis 04.01. bzw. 01.02., 15:30 in der Dozenten-App "
            "zu erfassen – danach Bestand auf die neue Taktik bringen («Alles auf Ziel»), damit Taktik und Portfolio übereinstimmen.\n"
            "- **Korridor (zwischendurch):** Nur was ausserhalb der Toleranz liegt, zurück aufs Ziel («Ausserhalb der Toleranz»). "
            "Kleine Abweichungen laufen lassen – Handeln ohne Auslöser bringt nur Umschlag und bricht Trends.\n"
            "- **Keine Kosten, keine Steuern:** In der Simulation lohnt sich «aufs Ziel» statt «an den Bandrand».\n"
            "- **Cash:** Kupons und Zins auf Cash fliessen zu und senken die Quoten – beim Rebalancing mitverteilen.\n"
            "- **Duration:** Anleihen altern (≈ −0,08 J. pro Monat); Abweichung von der Taktik über eine Umschichtung kurz ↔ lang.\n"
            "- **Februar (letzter Monat):** unbeabsichtigte Wetten abbauen, Tracking Error bewusst steuern.")
        k1, k2, k3, k4 = st.columns(4)
        tol = dict(klasse=k1.number_input("Toleranz Anlageklassen (Pp.)", 0.25, 10.0, float(s.get("taktik_tol", 2.0)), 0.25, key="rb_tk",
                                          help="Standard = Monitoring «Abweichung von der Taktik: Prüfen ab»; IPS-Pflicht ab Rebalancing-Toleranz"),
                   sektor=k2.number_input("Toleranz Sektoren (Pp. Aktienteil)", 0.1, 5.0, float(s.get("sektor_tol", 1.0)), 0.1, key="rb_ts"),
                   duration=k3.number_input("Toleranz Duration (J.)", 0.1, 3.0, float(s.get("duration_tol", 1.0)), 0.1, key="rb_td"))
        min_trade = k4.number_input("Mindest-Handelsgrösse (% Portfolio)", 0.0, 1.0, 0.02, 0.01, format="%.2f", key="rb_min",
                                    help="Trades darunter werden weggelassen (ausser Verkäufe auf 0). 0,02 % = 2'000 USD bei 10 Mio.")
    tol = dict(klasse=ss.get("rb_tk", float(s.get("taktik_tol", 2.0))), sektor=ss.get("rb_ts", float(s.get("sektor_tol", 1.0))),
               duration=ss.get("rb_td", float(s.get("duration_tol", 1.0))))
    min_trade = ss.get("rb_min", 0.02)

    # --- 1 Ausgangslage + Diagnose ---------------------------------------------------------------
    diag = diagnose(ctx, B, Z, tol)
    emp, emp_txt = empfehlung(diag, Z, monat, letzte)
    total = B["total"]
    ist = (float(B["ak"]["wert"].sum()) / total * 100, float(B["tr"]["wert"].sum()) / total * 100, B["cash"] / total * 100)
    n_p = sum(1 for d in diag if d["prio"] == "Pflicht (IPS)")
    ui.kopf_kpis([
        ("Portfoliowert", f"{ui.de(total, 0)} USD", f"{ui.de((total / B['kapital'] - 1) * 100, 2, True)} % seit Start · Bewertung {B['datum']:%d.%m.%Y}"),
        ("Aktien / Treasuries / Cash", f"{ist[0]:.1f} / {ist[1]:.1f} / {ist[2]:.1f} %".replace(".", ","),
         f"Ziel {_d(Z['aktien'], 1)} / {_d(Z['anleihen'], 1)} / {_d(Z['cash'], 1)} %"),
        ("Pflicht-Auslöser (IPS)", f"<span style='color:{ui.FARBE[L.ROT] if n_p else ui.FARBE[L.GRUEN]}'>{n_p}</span>",
         f"{sum(1 for d in diag if d['prio'] in ('Taktik', 'Sektoren', 'Titel', 'Anleihen', 'Cash'))} weitere über der Toleranz"),
        ("Empfehlung", {"keins": "kein Rebalancing"}.get(emp, METHODEN.get(emp, emp)).split(" (")[0], emp_txt[:110]),
    ])
    st.markdown("#### 1. Diagnose: Was verlangt ein Rebalancing?")
    if diag:
        dd = pd.DataFrame([{"Priorität": f"● {PRIO_STUFE[d['prio']]} · {d['prio']}", "Thema": d["thema"], "Ist": d["ist"],
                            "Ziel / Grenze": d["ziel"], "Befund": d["text"], "Massnahme": d["massnahme"]} for d in diag])
        st.dataframe(ui.ampel_stil(dd, ["Priorität"]), hide_index=True, width="stretch",
                     column_config={"Befund": st.column_config.TextColumn(width="large")})
    else:
        st.success("Keine Auslöser – alles innerhalb der Toleranzen.", icon=":material/check_circle:")
    st.info(f"**Empfehlung:** {emp_txt}", icon=":material/lightbulb:")

    # --- 2 Methode + Plan ----------------------------------------------------------------------
    st.markdown("#### 2. Handelsplan")
    import analyse as A

    gs = A.portfolios(ctx.tabellen.get("analysen"))
    opts = list(METHODEN)
    m1, m2, m3 = st.columns([1.8, 1.2, 1.2])
    methode = m1.radio("Methode", opts, index=opts.index(emp) if emp in opts else 1, key="rb_methode",
                       format_func=lambda x: METHODEN[x] + (" ← empfohlen" if x == emp else ""))
    neue = m2.toggle("Sektoren ohne Titel auffüllen (grösste Indextitel)", value=True, key="rb_neu")
    dur_opt = m3.toggle("Duration auf die Taktik (kurz ↔ lang)", value=True, key="rb_dur")
    ein = None
    if methode == "analyse":
        if not len(gs):
            st.warning("Noch kein gespeichertes Analyseportfolio. Tipp: Analyse → «Neu → Aus dem aktuellen Bestand», anpassen oder "
                       "«Gewichte optimieren», speichern – dann hier als Ziel wählen.")
            return
        aid = st.selectbox("Ziel-Analyseportfolio (gespeicherter Stand)", list(gs["id"]), key="rb_aid",
                           format_func=lambda i: f"{gs.set_index('id').loc[i, 'name']} ({gs.set_index('id').loc[i, 'person']}, "
                                                 f"{str(gs.set_index('id').loc[i, 'zeit'])[:10]})")
        ein = json.loads(gs.set_index("id").loc[aid, "eingabe"])
    opt = dict(min_trade=min_trade, neue_titel=neue, duration=dur_opt)
    pl = plan(ctx, B, Z, methode, tol, opt, ein)
    df = pl["trades"]
    for i_ in pl["info"]:
        st.caption(i_)
    if not len(df):
        return
    df["Handeln"] = df["delta_menge"] != 0
    df["begruendung"] = ""
    ansicht = pd.DataFrame({
        "Handeln": df["Handeln"], "Art": df["art"], "Klasse": df["klasse"], "Titel": df["ticker"], "Sektor": df["sektor"],
        "Ist": df["ist_menge"], "Ist %": df["ist_wert"] / total * 100, "Ziel %": df["ziel_wert_ist"] / total * 100,
        "Ziel Stück/Nominal": df["ziel_menge"], "Δ": df["delta_menge"], "Kurs (Vorschau)": df["kurs"], "Betrag USD": df["delta_wert"],
        "Grund": df["grund"], "Begründung (optional)": df["begruendung"]})
    ansicht = ansicht.sort_values(["Handeln", "Klasse", "Sektor", "Titel"], ascending=[False, True, True, True])
    ver = f"{monat}|{methode}|{sq}|{tol}|{opt}|{ss.get('rb_aid', '')}"
    st.caption("Häkchen «Handeln» entfernen = Trade weglassen. «Ziel Stück/Nominal» ist änderbar (z. B. runde Zahlen). Alles "
               "andere rechnet die App. Kurse in der Vorschau = letzter Schlusskurs bzw. Modellkurs; gebucht wird zum Eröffnungskurs.")
    ed = st.data_editor(ansicht, hide_index=True, width="stretch", key=f"rb_ed_{abs(hash(ver)) % 10**8}",
                        disabled=["Art", "Klasse", "Titel", "Sektor", "Ist", "Ist %", "Ziel %", "Δ", "Kurs (Vorschau)", "Betrag USD", "Grund"],
                        column_config={"Ist %": st.column_config.NumberColumn(format="%.2f"), "Ziel %": st.column_config.NumberColumn(format="%.2f"),
                                       "Ist": st.column_config.NumberColumn(format="%.0f"),
                                       "Ziel Stück/Nominal": st.column_config.NumberColumn(min_value=0.0, format="%.0f"),
                                       "Δ": st.column_config.NumberColumn(format="%+.0f"),
                                       "Kurs (Vorschau)": st.column_config.NumberColumn(format="%.2f"),
                                       "Betrag USD": st.column_config.NumberColumn(format="%+.0f"),
                                       "Grund": st.column_config.TextColumn(width="large"),
                                       "Begründung (optional)": st.column_config.TextColumn(width="medium")},
                        height=min(38 + 35 * len(ansicht), 600))
    # Änderungen übernehmen
    ed = ed.set_index("Titel")
    for i, r in df.iterrows():
        e_ = ed.loc[r["ticker"]] if r["ticker"] in ed.index else None
        if e_ is None:
            continue
        zm = float(e_["Ziel Stück/Nominal"]) if pd.notna(e_["Ziel Stück/Nominal"]) else r["ist_menge"]
        if not bool(e_["Handeln"]):
            zm = r["ist_menge"]
        df.at[i, "ziel_menge"] = zm
        df.at[i, "begruendung"] = _txt(e_["Begründung (optional)"])
    f = np.where(df["klasse"] == "Treasury", 0.01, 1.0)
    df["delta_menge"] = df["ziel_menge"] - df["ist_menge"]
    df["delta_wert"] = df["delta_menge"] * df["kurs"] * f
    df["ziel_wert_ist"] = df["ziel_menge"] * df["kurs"] * f
    df["art"] = np.where(df["delta_menge"] > 0, "Kauf", np.where(df["delta_menge"] < 0, "Verkauf", "–"))
    pl["trades"] = df
    pl["cash_nach"] = B["cash"] - float(df["delta_wert"].sum())
    t_ = df[df["delta_menge"] != 0]
    ui.kopf_kpis([
        ("Trades", str(len(t_)), f"{int((t_['delta_menge'] < 0).sum())} Verkäufe · {int((t_['delta_menge'] > 0).sum())} Käufe"),
        ("Verkäufe", f"{ui.de(-t_.loc[t_['delta_wert'] < 0, 'delta_wert'].sum(), 0)} USD", ""),
        ("Käufe", f"{ui.de(t_.loc[t_['delta_wert'] > 0, 'delta_wert'].sum(), 0)} USD",
         f"Umschlag {ui.de(t_['delta_wert'].abs().sum() / 2 / total * 100, 1)} % des Portfolios"),
        ("Cash danach", f"<span style='color:{ui.FARBE[L.ROT] if pl['cash_nach'] < 0 else 'inherit'}'>{ui.de(pl['cash_nach'], 0)} USD</span>",
         f"{_d(pl['cash_nach'] / total * 100, 2)} % · Ziel {_d(Z['cash'], 1)} %"),
    ])

    # --- 3 Vorher / Nachher --------------------------------------------------------------------
    st.markdown("#### 3. Vorher / Nachher")
    try:
        V = vergleich(ctx, B, pl, Z)
    except Exception as e:  # noqa: BLE001
        V = None
        st.warning(f"Vorher/Nachher nicht berechenbar: {type(e).__name__}: {e}")
    if V:
        st.dataframe(vergleich_tabelle(ctx, V, Z), hide_index=True, width="stretch")
        with st.expander(f"IPS-Gap-Analyse nach dem Rebalancing ({V['nachher']['n_gap'].get(L.ROT, 0)} verletzt, vorher "
                         f"{V['vorher']['n_gap'].get(L.ROT, 0)})", icon=":material/policy:"):
            import ips_abgleich as IA

            IA.anzeigen(V["nachher"]["gap"], titel="IPS gegen Portfolio nach dem Rebalancing")

    # --- 4 Dokumentation + Buchen ----------------------------------------------------------------
    st.markdown("#### 4. Begründen, Report, Buchen")
    mn = C.MONAT_BY_ID[monat]["name"]
    with st.container(border=True):
        d1, d2 = st.columns([1, 1])
        titel = d1.text_input("Titel", value=f"Rebalancing {mn}: {METHODEN[methode].split(' (')[0]}", key=f"rb_titel_{monat}_{methode}")
        tag0 = max(pd.Timestamp(C.SIMULATION_START), pd.Timestamp.today().normalize(),
                   pd.Timestamp(C.MONAT_BY_ID[monat]["frist"]).normalize())
        datum = pd.Timestamp(d2.date_input("Handelstag", value=tag0.date(), format="DD.MM.YYYY", key=f"rb_datum_{monat}",
                                           help="Aktien zum Eröffnungskurs dieses Tages (ab 15:30 Schweizer Zeit verfügbar)"))
        begr = st.text_area("Begründung (Pflicht) – warum jetzt, warum diese Methode, welche Auslöser", key="rb_begr", height=90,
                            placeholder="z. B. Neue Taktik Januar 45/50/5 (IC-Beschluss vom …). Aktienquote nach dem Dezember-Rally 47,2 % – "
                                        "über der Toleranz; Finanzen −1,3 Pp. unter Ziel; Duration 3,1 J. statt 3,5 J. …")
        gegen = st.text_area("Gegenargument / Risiko (Pflicht)", key="rb_gegen", height=60,
                             placeholder="z. B. Momentum in IT spricht gegen das Kürzen; Fed-Sitzung 27.01. kann die Duration-Wette drehen")
        q1c, q2c, bc = st.columns([1, 1, 1.2])
        q1 = q1c.text_input("Quelle 1 (Pflicht)", key="rb_q1", placeholder="z. B. Seite Portfolio, Stand …")
        q2 = q2c.text_input("Quelle 2 (Pflicht)", key="rb_q2", placeholder="z. B. Taktik Januar / IC-Protokoll")
        bet = bc.multiselect("Beteiligte", team or [ctx.person], default=[ctx.person] if ctx.person in (team or [ctx.person]) else [],
                             key="rb_bet")
    doku = dict(titel=titel, begruendung=begr, gegen=gegen, q1=q1, q2=q2, beteiligte=bet)
    fehlt = [n for n, v in (("Begründung", begr), ("Gegenargument", gegen), ("Quelle 1", q1), ("Quelle 2", q2)) if len(v.strip()) < 3]
    r1, r2, r3 = st.columns([1, 1, 1.6])
    if r1.button("Report-Entwurf (PDF) erstellen", icon=":material/picture_as_pdf:", key="rb_entwurf", width="stretch",
                 help="Zum Besprechen im Team, bevor gebucht wird"):
        try:
            ss["rb_pdf_entwurf"] = report(ctx, B, Z, monat, diag, pl, V, doku, entwurf=True).pdf()
        except Exception as e:  # noqa: BLE001
            st.error(f"Report nicht erstellt: {e}")
    if ss.get("rb_pdf_entwurf"):
        r2.download_button("Entwurf herunterladen", ss["rb_pdf_entwurf"], f"Rebalancing_{monat}_Entwurf_{datetime.now():%Y-%m-%d}.pdf",
                           "application/pdf", icon=":material/download:", key="rb_dl_entwurf", width="stretch")
    if r3.button("Eröffnungskurse laden und Buchung vorbereiten", icon=":material/price_check:", key="rb_kurse", width="stretch",
                 type="primary", disabled=not len(t_)):
        bk, fehl, kinfo = buchungskurse(ctx, fred, B, df, datum)
        ss["rb_buchung"] = dict(df=bk, fehlt=fehl, info=kinfo, datum=datum, sig=ver + str(df["ziel_menge"].tolist()))
    bu = ss.get("rb_buchung")
    if bu and bu["sig"] != ver + str(df["ziel_menge"].tolist()):
        st.caption("Plan oder Handelstag geändert – Eröffnungskurse neu laden.")
        bu = None
    if bu and bu["datum"] != datum:
        bu = None
    if bu:
        bk = bu["df"]
        bk["begruendung"] = df["begruendung"].values
        tb = bk[bk["delta_menge"] != 0].copy()
        if bu["fehlt"]:
            st.warning(f"Kein Eröffnungskurs vom {datum:%d.%m.%Y} für {', '.join(bu['fehlt'])} (Börse öffnet 15:30 Schweizer Zeit). "
                       "Kurs unten von Hand eintragen oder später neu laden.", icon=":material/schedule:")
        st.markdown("**Buchung** · Kurse prüfen (änderbar)")
        tb_ed = st.data_editor(pd.DataFrame({"Art": np.where(tb["delta_menge"] > 0, "Kauf", "Verkauf"), "Titel": tb["ticker"],
                                             "Menge": tb["delta_menge"].abs(), "Kurs": tb["kurs_buchung"].astype(float),
                                             "Quelle": tb["kurs_quelle"]}),
                               hide_index=True, width="stretch", key="rb_kurs_ed", disabled=["Art", "Titel", "Menge", "Quelle"],
                               column_config={"Kurs": st.column_config.NumberColumn(min_value=0.0, format="%.4f",
                                                                                    help="Aktie USD, Treasury % vom Nominal")})
        bk.loc[tb.index, "kurs_buchung"] = tb_ed["Kurs"].values
        f2 = np.where(bk["klasse"] == "Treasury", 0.01, 1.0)
        cash_b = B["cash"] - float((bk["delta_menge"] * bk["kurs_buchung"].fillna(bk["kurs"]) * f2).sum())
        ohne_kurs = int(bk.loc[bk["delta_menge"] != 0, "kurs_buchung"].isna().sum())
        st.caption(f"{bu['info']} Cash nach der Buchung ≈ {ui.de(cash_b, 0)} USD.")
        if cash_b < -1:
            st.error("Cash würde negativ – Käufe im Plan reduzieren.", icon=":material/block:")
        if fehlt:
            st.error("Für die Dokumentation fehlt: " + ", ".join(fehlt) + " (Teamregel: Begründung, Gegenargument, zwei Quellen).")
        schon = [x for x in letzte if x.get("monat") == monat and x["aktiv"]]
        if schon:
            st.warning(f"Für {mn} ist schon ein Rebalancing gebucht ({schon[0]['zeit'][:10]}, {schon[0]['aktiv']} Buchungen aktiv). "
                       "Ein zweites ist möglich (z. B. Korridor-Rebalancing), bitte bewusst.", icon=":material/content_copy:")
        ok = not fehlt and cash_b >= -1 and not ohne_kurs and not ctx.nur_lesen
        if st.button(f"Bestätigen: {len(tb)} Trades per {datum:%d.%m.%Y} buchen, Report ablegen", type="primary",
                     icon=":material/done_all:", disabled=not ok, key="rb_ok"):
            _buchen(ctx, B, Z, monat, diag, pl, V, doku, bk, datum, bu["info"])
    _historie(ctx, letzte)
    try:
        import dateien

        dateien.bereich(ctx, "rebalancing", "Rebalancing-Reports")
    except Exception:  # noqa: BLE001
        pass


def _buchen(ctx, B, Z, monat, diag, pl, V, doku, bk, datum, kinfo):
    import entscheide as EN

    batch = uuid.uuid4().hex[:6]
    mn = C.MONAT_BY_ID[monat]["name"]
    rows = buchungen(bk, datum, batch, ctx.person, mn, doku["begruendung"])
    pl2 = dict(pl, trades=bk.assign(kurs=bk["kurs_buchung"].fillna(bk["kurs"])))
    f = np.where(bk["klasse"] == "Treasury", 0.01, 1.0)
    pl2["cash_nach"] = B["cash"] - float((bk["delta_menge"] * pl2["trades"]["kurs"] * f).sum())
    pl2["trades"]["delta_wert"] = pl2["trades"]["delta_menge"] * pl2["trades"]["kurs"] * f
    try:
        pdf = report(ctx, B, Z, monat, diag, pl2, V, doku, entwurf=False, kurse_info=kinfo, datum=datum).pdf()
    except Exception as e:  # noqa: BLE001
        pdf = None
        st.warning(f"Report nicht erstellt: {e}")
    name = f"Rebalancing_{monat}_{datum:%Y-%m-%d}_{batch}.pdf"
    datei_id = ""
    z = jetzt()
    if pdf:
        st.session_state["rb_pdf_final"] = (name, pdf)
        try:
            datei_id = ctx.store.datei_hochladen(name, "application/pdf", pdf, "rebalancing")
            ctx.store.anhaengen("dateien", [dict(zeit=z, person=ctx.person, id=uuid.uuid4().hex[:10], bereich="rebalancing", name=name,
                                                 typ="application/pdf", groesse=len(pdf), datei_id=datei_id,
                                                 beschreibung=doku["titel"], geloescht=0)])
        except Exception as e:  # noqa: BLE001
            st.warning(f"Report nicht in Dokumente abgelegt ({e}) – bitte unten herunterladen und von Hand hochladen.")
    t = bk[bk["delta_menge"] != 0]
    zus = dict(monat=monat, datum=datum.date().isoformat(), methode=pl.get("methode"), n=len(rows), titel=doku["titel"],
               verkauf=round(float(-(t["delta_menge"].clip(upper=0) * t["kurs_buchung"].fillna(t["kurs"])
                                     * np.where(t["klasse"] == "Treasury", 0.01, 1)).sum()), 0),
               kauf=round(float((t["delta_menge"].clip(lower=0) * t["kurs_buchung"].fillna(t["kurs"])
                                 * np.where(t["klasse"] == "Treasury", 0.01, 1)).sum()), 0),
               vorher=f"{_d(V['vorher']['aktien'], 1)}/{_d(V['vorher']['treas'], 1)}/{_d(V['vorher']['cash_pct'], 1)}" if V else "",
               nachher=f"{_d(V['nachher']['aktien'], 1)}/{_d(V['nachher']['treas'], 1)}/{_d(V['nachher']['cash_pct'], 1)}" if V else "",
               ips_rot_vorher=V["vorher"]["n_gap"].get(L.ROT) if V else None, ips_rot_nachher=V["nachher"]["n_gap"].get(L.ROT) if V else None,
               report=name, datei_id=datei_id, entscheid=f"rb{batch}")
    try:
        ctx.store.anhaengen("einstellungen", [dict(zeit=z, person=ctx.person, schluessel=f"rebalancing|{batch}",
                                                   wert=json.dumps(zus, ensure_ascii=False, default=str))])
        snap = EN.snapshot(ctx)
        snap["rebalancing"] = zus
        ctx.store.anhaengen("entscheide", [dict(zeit=z, person=ctx.person, id=f"rb{batch}", datum=datum.date().isoformat(),
                                                kategorie="Rebalancing", titel=doku["titel"],
                                                entscheid=f"{len(rows)} Trades per {datum:%d.%m.%Y} ({METHODEN.get(pl.get('methode'))}): "
                                                          f"Verkäufe {ui.de(zus['verkauf'], 0)} USD, Käufe {ui.de(zus['kauf'], 0)} USD. "
                                                          f"Aufteilung vorher {zus['vorher']} → nachher {zus['nachher']} %. Report: {name}",
                                                begruendung=doku["begruendung"], quelle1=doku["q1"], quelle2=doku["q2"],
                                                gegenargument=doku["gegen"], beteiligte=", ".join(doku.get("beteiligte") or []),
                                                status="Umgesetzt", snapshot=json.dumps(snap, ensure_ascii=False, default=str))])
    except Exception as e:  # noqa: BLE001
        st.error(f"Nicht gebucht: {e}")
        return
    st.session_state.pop("rb_buchung", None)
    ctx.speichern("transaktionen", rows, f"Rebalancing {mn}: {len(rows)} Trades per {datum:%d.%m.%Y} gebucht, Report «{name}» abgelegt.")


def _historie(ctx, letzte: list[dict]):
    if fin := st.session_state.get("rb_pdf_final"):
        st.download_button(f"Report herunterladen ({fin[0]})", fin[1], fin[0], "application/pdf", icon=":material/download:",
                           key="rb_dl_final", type="primary")
    if not letzte:
        return
    st.markdown("#### Bisherige Rebalancings")
    for u in letzte:
        c1, c2 = st.columns([4, 1])
        c1.markdown(f"<div class='klein'><b>{u.get('titel', '')}</b> · {C.MONAT_BY_ID.get(u.get('monat'), {}).get('name', u.get('monat'))} · "
                    f"Handelstag {u.get('datum', '')} · {u.get('n', 0)} Trades, davon {u['aktiv']} aktiv · Aufteilung {u.get('vorher', '')} → "
                    f"{u.get('nachher', '')} % · IPS verletzt {u.get('ips_rot_vorher', '–')} → {u.get('ips_rot_nachher', '–')} · "
                    f"{u['person']}, {u['zeit'][:10]} · Report {u.get('report', '')}</div>", unsafe_allow_html=True)
        if u["aktiv"] and c2.button("Rückgängig", key=f"rb_undo_{u['batch']}", icon=":material/undo:", disabled=ctx.nur_lesen, width="stretch"):
            roh = ctx.tabellen["transaktionen"].sort_values("zeit").drop_duplicates("id", keep="last")
            roh = roh[roh["id"].astype(str).str.startswith(f"rb{u['batch']}") & ~roh["storniert"].astype(str).str.strip().isin(["1", "1.0", "True"])]
            z = jetzt()
            try:
                e = ctx.tabellen.get("entscheide")
                alt = e[e["id"] == u.get("entscheid")].sort_values("zeit").iloc[-1].to_dict() if e is not None and len(e) else None
                if alt:
                    ctx.store.anhaengen("entscheide", [dict(alt, zeit=z, person=ctx.person, status="Verworfen",
                                                            entscheid=f"RÜCKGÄNGIG ({datetime.now():%d.%m.%Y}): " + str(alt.get("entscheid", "")))])
            except Exception:  # noqa: BLE001
                pass
            ctx.speichern("transaktionen", [{**r, "zeit": z, "person": ctx.person, "storniert": "1"} for r in roh.to_dict("records")],
                          f"Rebalancing «{u.get('titel', '')}» rückgängig gemacht.")
