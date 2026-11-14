"""Rechenlogik: Zeitreihen, Ampeln, Trends, Team-Mittelwerte, Zyklus und Allokation.

Bewusst ohne Streamlit, damit alles testbar und nachvollziehbar bleibt.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import config as C
from .storage import zahl

ROT, GELB, GRUEN, KONTEXT, LEER = "Rot", "Gelb", "Grün", "Kontext", "Keine Daten"


# ---------------------------------------------------------------------------
# Zeitreihen
# ---------------------------------------------------------------------------
def transform(s: pd.Series, art: str, scale: float = 1.0) -> pd.Series:
    s = s.dropna().sort_index()
    if s.empty:
        return s
    if art == "level":
        out = s
    elif art == "yoy":
        vorjahr = s.reindex(s.index - pd.DateOffset(years=1), method="nearest", tolerance=pd.Timedelta(days=20))
        out = (s.values / vorjahr.values - 1) * 100
        out = pd.Series(out, index=s.index)
    elif art == "mom":
        out = s.pct_change() * 100
    elif art == "diff":
        out = s.diff()
    elif art == "ann_qoq":
        out = ((s / s.shift(1)) ** 4 - 1) * 100
    elif art == "drawdown":
        out = (s / s.cummax() - 1) * 100
    elif art == "avg30":
        out = s.rolling("30D").mean()
    else:
        raise ValueError(art)
    return (out * scale).replace([np.inf, -np.inf], np.nan).dropna()


def _frame(werte: pd.Series, herkunft: str, person="", q1="", q2="", notiz="") -> pd.DataFrame:
    return pd.DataFrame({"wert": werte.astype(float), "herkunft": herkunft, "person": person,
                         "quelle1": q1, "quelle2": q2, "notiz": notiz}, index=werte.index)


def manuelle_eintraege(werte_df: pd.DataFrame, ind_id: str) -> pd.DataFrame:
    if werte_df is None or werte_df.empty:
        return pd.DataFrame(columns=["wert", "herkunft", "person", "quelle1", "quelle2", "notiz", "zeit"])
    d = werte_df[werte_df["indikator"] == ind_id].copy()
    if d.empty:
        return pd.DataFrame(columns=["wert", "herkunft", "person", "quelle1", "quelle2", "notiz", "zeit"])
    d["wert"] = d["wert"].map(zahl)
    d = d.dropna(subset=["wert"])
    d["datum"] = pd.to_datetime(d["datum"], errors="coerce")
    d = d.dropna(subset=["datum"]).sort_values("zeit")
    d = d.drop_duplicates("datum", keep="last")  # neuste Eingabe pro Datum gilt
    out = pd.DataFrame({"wert": d["wert"].values, "herkunft": "Team", "person": d["person"].values,
                        "quelle1": d["quelle1"].values, "quelle2": d["quelle2"].values,
                        "notiz": d["notiz"].values, "zeit": d["zeit"].values}, index=d["datum"].values)
    return out.sort_index()


def _kombiniere(*frames: pd.DataFrame) -> pd.DataFrame:
    """Spätere Frames haben Vorrang bei gleichem Datum (Reihenfolge: Startwert < FRED < Team)."""
    frames = [f for f in frames if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame(columns=["wert", "herkunft", "person", "quelle1", "quelle2", "notiz"])
    df = pd.concat(frames)
    df.index = pd.to_datetime(df.index)
    df = df[~df.index.duplicated(keep="last")]
    return df.sort_index()


class Daten:
    """Bündelt FRED-Rohdaten und Team-Eingaben und liefert je Indikator die kombinierte Reihe."""

    def __init__(self, fred: dict[str, pd.Series], werte_df: pd.DataFrame):
        self.fred = fred or {}
        self.werte_df = werte_df
        self._cache = {}

    def reihe(self, ind_id: str) -> pd.DataFrame:
        if ind_id in self._cache:
            return self._cache[ind_id]
        ind = C.IND_BY_ID[ind_id]
        seed = pd.Series({pd.Timestamp(d): v for d, v in ind["seed"]}, dtype=float)
        f_seed = _frame(seed, "Analyse (Startwert)", q1=ind["quelle1"], q2=ind["quelle2"]) if not seed.empty else None
        f_auto = None
        if ind["quelle"] == "fred" and ind["series"] in self.fred:
            s = transform(self.fred[ind["series"]], ind["transform"], ind["scale"])
            f_auto = _frame(s, "FRED", q1=f"FRED {ind['series']}", q2=ind["quelle1"])
        elif ind["quelle"] == "berechnet":
            s = self._berechne(ind_id)
            if s is not None and not s.empty:
                f_auto = _frame(s, "berechnet", q1=ind["quelle1"], q2=ind["quelle2"])
        f_team = manuelle_eintraege(self.werte_df, ind_id)
        f_team = f_team.drop(columns=["zeit"], errors="ignore")
        df = _kombiniere(f_seed, f_auto, f_team)
        self._cache[ind_id] = df
        return df

    def _berechne(self, ind_id):
        if ind_id == "outputgap":
            if "GDPC1" in self.fred and "GDPPOT" in self.fred:
                g, p = self.fred["GDPC1"], self.fred["GDPPOT"]
                idx = g.index.intersection(p.index)
                return ((g[idx] / p[idx] - 1) * 100).dropna()
            return None
        if ind_id == "erp":
            eps = self.reihe("fwd_eps")["wert"]
            spx = self.reihe("sp500")["wert"]
            y10 = self.reihe("dgs10")["wert"]
            if eps.empty or spx.empty or y10.empty:
                return None
            idx = spx.index[spx.index >= eps.index.min()]
            if len(idx) == 0:
                return None
            e = eps.reindex(idx, method="ffill")
            y = y10.sort_index().reindex(idx, method="ffill")
            return (e / spx[idx] * 100 - y).dropna()
        return None


# ---------------------------------------------------------------------------
# Ampeln & Status
# ---------------------------------------------------------------------------
def ampel(ind: dict, wert) -> str:
    if wert is None or (isinstance(wert, float) and math.isnan(wert)):
        return LEER
    r, g = ind.get("rot"), ind.get("gelb")
    if ind["richtung"] == "hoch":
        if r is not None and wert >= r:
            return ROT
        if g is not None and wert >= g:
            return GELB
        return GRUEN
    if ind["richtung"] == "tief":
        if r is not None and wert < r:
            return ROT
        if ind.get("id") == "realkonsum" and g is not None and wert <= g:
            return GELB
        if g is not None and wert < g:
            return GELB
        return GRUEN
    return KONTEXT


def trend(ind: dict, wert, vorwert) -> tuple[str, str]:
    """(Pfeil, Bewertung: besser/schlechter/neutral)."""
    if wert is None or vorwert is None:
        return "", "neutral"
    diff = wert - vorwert
    eps = 1e-9 + abs(vorwert) * 0.002
    if abs(diff) <= eps:
        return "→", "neutral"
    pfeil = "↑" if diff > 0 else "↓"
    if ind["richtung"] == "hoch":
        return pfeil, "schlechter" if diff > 0 else "besser"
    if ind["richtung"] == "tief":
        return pfeil, "besser" if diff > 0 else "schlechter"
    return pfeil, "neutral"


def fmt(ind: dict, wert) -> str:
    if wert is None or (isinstance(wert, float) and math.isnan(wert)):
        return "–"
    d = ind.get("dezimal", 1)
    s = f"{wert:,.{d}f}".replace(",", "'").replace(".", ",")
    if wert > 0 and ind["transform"] in ("mom", "diff", "ann_qoq") or (ind["id"] in ("erp", "kurve", "kurve3m", "bip") and wert > 0):
        s = "+" + s
    s = s.replace("-", "−")
    e = ind["einheit"]
    if e in ("%", "% y/y", "% m/m", "PP", "bp", "x"):
        return f"{s} {e}" if e != "x" else f"{s}x"
    return f"{s} {e}" if e not in ("Index",) else s


def status(daten: Daten) -> pd.DataFrame:
    zeilen = []
    for ind in C.INDIKATOREN:
        df = daten.reihe(ind["id"])
        if df.empty:
            wert = vorwert = datum = vordatum = None
            herkunft = person = q1 = q2 = ""
        else:
            last = df.iloc[-1]
            wert, datum = float(last["wert"]), df.index[-1]
            herkunft, person, q1, q2 = last["herkunft"], last["person"], last["quelle1"], last["quelle2"]
            if len(df) > 1:
                vorwert, vordatum = float(df.iloc[-2]["wert"]), df.index[-2]
            else:
                vorwert = vordatum = None
        a = ampel(ind, wert)
        if ind.get("zwei_monate") and wert is not None and len(df) >= 2 and (df["wert"].iloc[-2:] <= 0).all():
            a = ROT
        pf, bew = trend(ind, wert, vorwert)
        zeilen.append(dict(id=ind["id"], kapitel=ind["kapitel"], name=ind["name"], wert=wert, anzeige=fmt(ind, wert),
                           datum=datum, vorwert=vorwert, vor_anzeige=fmt(ind, vorwert), vordatum=vordatum,
                           ampel=a, pfeil=pf, trend=bew, herkunft=herkunft, person=person,
                           quelle1=q1, quelle2=q2, schwelle=ind["schwelle"], kern=ind["kern"]))
    return pd.DataFrame(zeilen).set_index("id", drop=False)


def wert(st: pd.DataFrame, ind_id: str):
    v = st.loc[ind_id, "wert"] if ind_id in st.index else None
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def kombi_signale(st: pd.DataFrame, daten: Daten) -> list[dict]:
    """Signale, die aus mehreren Indikatoren zusammen entstehen (aus euren Schwellen-Definitionen)."""
    out = []
    no, pr = wert(st, "ism_no"), wert(st, "ism_preise")
    out.append(dict(id="stagflation", name="Stagflationssignal (ISM)", kapitel="wachstum",
                    aktiv=no is not None and pr is not None and no < 52 and pr > 75,
                    regel="ISM-Neuaufträge < 52 UND ISM-Preise > 75",
                    aktion="Zykliker reduzieren; Konsumrückgang-Risiko höher gewichten."))
    ur, nfp = wert(st, "unrate"), wert(st, "nfp")
    out.append(dict(id="arbeitsmarkt_kippt", name="Arbeitsmarkt kippt", kapitel="arbeitsmarkt",
                    aktiv=ur is not None and nfp is not None and ur >= 4.3 and nfp < 50,
                    regel="Arbeitslosenquote ≥ 4,3 % UND Stellenaufbau < 50'000",
                    aktion="Duration auf 4–5 Jahre verlängern, Anleihen-Übergewicht halten."))
    rk = daten.reihe("realkonsum")
    aktiv = len(rk) >= 2 and bool((rk["wert"].iloc[-2:] <= 0).all())
    out.append(dict(id="realkonsum_2m", name="Realkonsum 2 Monate ≤ 0", kapitel="konsum", aktiv=aktiv,
                    regel="Realer Konsum zwei Monate in Folge ≤ 0 % zum Vormonat",
                    aktion="Warnsignal Konsumrückgang: zyklischen Konsum untergewichten."))
    y10, ur_g = wert(st, "dgs10"), st.loc["unrate", "ampel"] if "unrate" in st.index else LEER
    sahm = wert(st, "sahm")
    abkuehlung = ur_g == ROT or (sahm is not None and sahm >= 0.3)
    out.append(dict(id="duration_lang", name="10 J. > 5,3 % bei Abkühlung", kapitel="zinsen",
                    aktiv=y10 is not None and y10 > 5.3 and abkuehlung,
                    regel="10-J.-Rendite > 5,3 % UND Abkühlung (Arbeitslosenquote rot oder Sahm ≥ 0,3)",
                    aktion="Duration Richtung 5 Jahre verlängern."))
    return out


def auto_warnsignale(st: pd.DataFrame, kombis: list[dict]) -> dict[str, tuple[bool, str]]:
    k = {x["id"]: x["aktiv"] for x in kombis}
    fms = wert(st, "fms_cash")
    spar, nfib = wert(st, "sparquote"), wert(st, "nfib")
    gruende_konsum = []
    if spar is not None and spar < 3.5:
        gruende_konsum.append("Sparquote < 3,5 %")
    if k.get("realkonsum_2m"):
        gruende_konsum.append("Realkonsum 2 Monate ≤ 0")
    if k.get("stagflation"):
        gruende_konsum.append("ISM-Stagflationssignal")
    if nfib is not None and nfib < 95:
        gruende_konsum.append("NFIB < 95")
    return {
        "fms_cash": (fms is not None and fms < 4.0, "Fondsmanager-Cash < 4 %" if fms is not None and fms < 4.0 else ""),
        "konsum": (bool(gruende_konsum), ", ".join(gruende_konsum)),
    }


# ---------------------------------------------------------------------------
# Team-Bewertungen
# ---------------------------------------------------------------------------
def _neuste_pro_person(df: pd.DataFrame, schluessel: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=list(df.columns) if df is not None else [])
    return df.sort_values("zeit").drop_duplicates(["person", schluessel], keep="last")


def szenario_wahrscheinlichkeiten(votes: pd.DataFrame) -> pd.DataFrame:
    """Pro Szenario: Team-Mittel (je Person pro Thema auf 100 % normiert), Min, Max, Anzahl, Startwert."""
    v = _neuste_pro_person(votes, "szenario")
    if not v.empty:
        v = v.copy()
        v["p"] = v["p"].map(zahl)
        v = v.dropna(subset=["p"])
        v["thema"] = v["szenario"].map(lambda s: C.SZEN_BY_ID.get(s, {}).get("thema"))
        v = v.dropna(subset=["thema"])
        summe = v.groupby(["person", "thema"])["p"].transform("sum")
        v["p_norm"] = np.where(summe > 0, v["p"] / summe * 100, np.nan)
    zeilen = []
    for s in C.SZENARIEN:
        d = v[v["szenario"] == s["id"]] if not v.empty else pd.DataFrame()
        if len(d):
            zeilen.append(dict(id=s["id"], thema=s["thema"], name=s["name"], p=d["p_norm"].mean(),
                               p_min=d["p_norm"].min(), p_max=d["p_norm"].max(), n=len(d),
                               start=s["p"], basis="Team"))
        else:
            zeilen.append(dict(id=s["id"], thema=s["thema"], name=s["name"], p=float(s["p"]), p_min=np.nan,
                               p_max=np.nan, n=0, start=s["p"],
                               basis="Startwert (Vorschlag)" if s.get("p_vorschlag") else "Startwert (Analyse)"))
    df = pd.DataFrame(zeilen).set_index("id", drop=False)
    # Je Thema: Mischung aus Team-Szenarien und Startwerten wieder auf 100 % bringen
    df["p"] = df.groupby("thema")["p"].transform(lambda x: x / x.sum() * 100 if x.sum() > 0 else x)
    df["uneinig"] = (df["p_max"] - df["p_min"]).fillna(0)
    return df


def risiko_bewertung(votes: pd.DataFrame, neue: pd.DataFrame, st: pd.DataFrame, kombis) -> pd.DataFrame:
    risiken = list(C.RISIKEN)
    if neue is not None and not neue.empty:
        for _, r in neue.sort_values("zeit").drop_duplicates("id", keep="last").iterrows():
            risiken.append(dict(id=r["id"], name=r["name"], kapitel=r["kapitel"] or "geopolitik", p=None, p_text="neu",
                                impact=None, warnsignal=r["warnsignal"], reaktion=r["reaktion"], auto=None,
                                quelle=f"Team ({r['person']})"))
    v = _neuste_pro_person(votes, "risiko")
    if not v.empty:
        v = v.copy()
        v["p"] = v["p"].map(zahl)
        v["impact"] = v["impact"].map(zahl)
        v["signal"] = v["signal"].map(lambda x: str(x).strip().lower() in ("1", "true", "ja", "1.0"))
    auto = auto_warnsignale(st, kombis)
    zeilen = []
    for r in risiken:
        d = v[v["risiko"] == r["id"]] if not v.empty else pd.DataFrame()
        team_signal = bool(d["signal"].any()) if len(d) else False
        wer_signal = ", ".join(d.loc[d["signal"], "person"]) if len(d) else ""
        auto_aktiv, auto_grund = auto.get(r.get("auto"), (False, "")) if r.get("auto") else (False, "")
        p = d["p"].mean() if len(d) and d["p"].notna().any() else r["p"]
        imp = d["impact"].mean() if len(d) and d["impact"].notna().any() else r["impact"]
        zeilen.append(dict(id=r["id"], name=r["name"], kapitel=r["kapitel"], p=p, impact=imp,
                           p_min=d["p"].min() if len(d) else np.nan, p_max=d["p"].max() if len(d) else np.nan,
                           n=len(d), p_text=r["p_text"], warnsignal=r["warnsignal"], reaktion=r["reaktion"],
                           signal=team_signal or auto_aktiv, signal_team=wer_signal, signal_auto=auto_grund,
                           basis="Team" if len(d) else "Startwert", quelle=r["quelle"]))
    df = pd.DataFrame(zeilen).set_index("id", drop=False)
    df["score"] = df["p"].fillna(0) / 100 * df["impact"].fillna(0)
    return df


# ---------------------------------------------------------------------------
# Zyklus
# ---------------------------------------------------------------------------
def phase(pos: float) -> str:
    for grenze, name in C.ZYKLUS_PHASEN:
        if pos < grenze:
            return name
    return C.ZYKLUS_PHASEN[-1][1]


def zyklus_modell(st: pd.DataFrame) -> dict:
    """Regelbasierte Einordnung: Anteil Spätzyklus-Merkmale → Position; Rezessionsmerkmale schieben in den Abschwung."""
    def w(i):
        return wert(st, i)
    spaet = [
        ("Output Gap > 0", w("outputgap"), lambda x: x > 0),
        ("Kern-PCE > 2,5 %", w("kernpce"), lambda x: x > 2.5),
        ("Leitzins ≥ 3,5 % (restriktiv)", w("fedfunds"), lambda x: x >= 3.5),
        ("Zinskurve 10–2 J. < 50 bp", w("kurve"), lambda x: x < 50),
        ("Arbeitslosenquote < 4,5 %", w("unrate"), lambda x: x < 4.5),
        ("Sparquote < 4,5 %", w("sparquote"), lambda x: x < 4.5),
        ("BBB-Spread < 130 bp (sorglos)", w("bbb"), lambda x: x < 130),
        ("ISM-Preise ≥ 70", w("ism_preise"), lambda x: x >= 70),
    ]
    rezession = [
        ("Sahm-Regel ≥ 0,5", w("sahm"), lambda x: x >= 0.5),
        ("Reales BIP < 0", w("bip"), lambda x: x < 0),
        ("NFCI > 0", w("nfci"), lambda x: x > 0),
        ("BB-Spread > 300 bp", w("bb"), lambda x: x > 300),
        ("Stellenaufbau < 0", w("nfp"), lambda x: x < 0),
    ]
    sp_ok = [(n, f(v)) for n, v, f in spaet if v is not None]
    re_ok = [(n, f(v)) for n, v, f in rezession if v is not None]
    anteil = sum(x for _, x in sp_ok) / len(sp_ok) if sp_ok else 0.5
    n_rez = sum(x for _, x in re_ok)
    pos = 0.30 + 0.27 * anteil          # 0,30 (Erholung) … 0,57 (Spätzyklus)
    if n_rez:
        pos = min(0.64 + 0.06 * n_rez, 0.88)  # Abschwung … Rezession
    return dict(pos=pos, phase=phase(pos), anteil=anteil, spaet=sp_ok, rezession=re_ok, n_rez=n_rez)


def zyklus_team(votes: pd.DataFrame) -> pd.DataFrame:
    v = _neuste_pro_person(votes, "person") if votes is not None and not votes.empty else pd.DataFrame()
    if v.empty:
        return pd.DataFrame([dict(person=z["person"], von=z["von"], bis=z["bis"], kommentar=z["einordnung"],
                                  basis="Analyse") for z in C.ZYKLUS_SEED])
    v = v.copy()
    v["von"], v["bis"] = v["von"].map(zahl), v["bis"].map(zahl)
    v["basis"] = "Team"
    return v[["person", "von", "bis", "kommentar", "basis"]].dropna(subset=["von", "bis"])


# ---------------------------------------------------------------------------
# Allokation
# ---------------------------------------------------------------------------
def einstellungen(df: pd.DataFrame) -> dict:
    out = dict(C.NEUTRAL_DEFAULT)
    if df is not None and not df.empty:
        for _, r in df.sort_values("zeit").iterrows():
            v = zahl(r["wert"])
            if r["schluessel"] in out and v is not None:
                out[r["schluessel"]] = v
    return out


def allokation(st: pd.DataFrame, szen: pd.DataFrame, risiken: pd.DataFrame, kombis: list[dict], neutral: dict) -> dict:
    regeln = []
    k = {x["id"]: x["aktiv"] for x in kombis}

    def a(i):
        return st.loc[i, "ampel"] if i in st.index else LEER

    def w(i):
        return wert(st, i)

    def regel(name, aktiv, grund, aktien=0.0, duration=None, tips=None, sektoren=None, herkunft="Eure Aktion"):
        regeln.append(dict(name=name, aktiv=bool(aktiv), grund=grund, aktien=aktien, duration=duration, tips=tips,
                           sektoren=sektoren or {}, herkunft=herkunft))

    brent, erp = w("brent"), w("erp")
    kreditstress = a("bb") == ROT or a("bbb") == ROT
    regel("Kreditstress", kreditstress, "BB > 300 bp oder BBB > 150 bp", aktien=-10, duration=4.5)
    regel("Kern-CPI heiss", a("kerncpi") == ROT, "Kern-CPI ≥ 0,4 % zum Vormonat", duration=2.5, tips=25)
    regel("Öl ≥ 100 USD", brent is not None and brent >= 100, "Brent ≥ 100 USD", duration=2.5, tips=25)
    regel("Öl ≥ 110 USD", brent is not None and brent >= 110, "Brent ≥ 110 USD: Zykliker reduzieren, Cash ↑",
          aktien=-5, sektoren={"Zykl. Konsum": -0.5, "Industrie": -0.5}, herkunft="Eure Aktion · Grösse: Vorschlag")
    regel("Öl < 85 USD", brent is not None and brent < 85, "Brent < 85 USD: Energie-Übergewicht abbauen",
          sektoren={"Energie": -1})
    regel("Arbeitsmarkt kippt", k.get("arbeitsmarkt_kippt"), "Quote ≥ 4,3 % und Stellen < 50'000", duration=4.5)
    regel("Lange Zinsen hoch + Abkühlung", k.get("duration_lang"), "10 J. > 5,3 % bei Abkühlung", duration=5.0)
    regel("Aktienrisikoprämie < 1 PP", erp is not None and erp < 1.0, "Aktien kaum besser entschädigt als Treasuries",
          aktien=-5, herkunft="Eure Aktion · Grösse: Vorschlag")
    regel("Finanzstress", a("nfci") == ROT or a("stlfsi") == ROT, "NFCI oder St. Louis-Index > 0 → Risk-off höher gewichten",
          aktien=-5, herkunft="Eure Aktion · Grösse: Vorschlag")
    konsum = risiken.loc["r_konsum", "signal"] if "r_konsum" in risiken.index else False
    regel("Warnsignal Konsumrückgang", konsum, "Sparquote < 3,5 %, Realkonsum 2 Mt. ≤ 0, ISM-Stagflation oder NFIB < 95",
          sektoren={"Zykl. Konsum": -1, "Basiskonsum": 0.5, "Gesundheit": 0.5})
    ki = risiken.loc["r_ki", "signal"] if "r_ki" in risiken.index else False
    regel("Warnsignal KI-/Tech-Korrektur", ki, "Fondsmanager-Cash < 4 % oder vom Team gemeldet",
          sektoren={"IT": -1}, herkunft="Eure Aktion · Grösse: Vorschlag")
    prot = risiken.loc["r_protekt", "signal"] if "r_protekt" in risiken.index else False
    regel("Warnsignal Protektionismus", prot, "Vom Team gemeldet: Autos untergewichten",
          sektoren={"Zykl. Konsum": -0.5})
    tail = risiken.loc["r_tail", "signal"] if "r_tail" in risiken.index else False
    regel("Warnsignal Tail Risk", tail, "Vom Team gemeldet: Aktienquote senken, Cash und kurze Treasuries ↑",
          aktien=-10, duration=2.0, herkunft="Eure Aktion · Grösse: Vorschlag")
    p_neg = szen.loc["g_neg", "p"] if "g_neg" in szen.index else 0
    regel("Gewinne-Negativszenario ≥ 40 %", p_neg >= 40, f"Team-Wahrscheinlichkeit Negativ: {p_neg:.0f} %",
          aktien=-5, sektoren={"IT": -0.5}, herkunft="Eure Aktion · Schwelle: Vorschlag")

    aktive = [r for r in regeln if r["aktiv"]]

    # Duration: Szenario-gewichtet (Zinsen & Inflation), gemittelt mit den Zielen aktiver Regeln
    z = szen[szen["thema"] == "zinsen"]
    dur_szen = sum(z.loc[i, "p"] / 100 * C.SZEN_BY_ID[i]["duration"] for i in z.index)
    tips_szen = sum(z.loc[i, "p"] / 100 * C.SZEN_BY_ID[i]["tips"] for i in z.index)
    dur_ziele = [r["duration"] for r in aktive if r["duration"] is not None]
    duration = float(np.mean([dur_szen] + dur_ziele))
    tips_ziele = [r["tips"] for r in aktive if r["tips"] is not None]
    tips = max([tips_szen] + tips_ziele)
    konflikt = bool(dur_ziele) and (max(dur_ziele) - min(dur_ziele + [dur_szen]) >= 1.5)

    aktien_tilt = sum(r["aktien"] for r in aktive)
    aktien_tilt = max(-C.AKTIEN_MAX_ABWEICHUNG, min(C.AKTIEN_MAX_ABWEICHUNG, aktien_tilt))
    aktien = max(0.0, min(100.0, neutral["aktien"] + aktien_tilt))

    # Sektoren: Σ Wahrscheinlichkeit × Wirkung über alle Themen + aktive Regeln
    sek = {s: 0.0 for s in C.SEKTOREN}
    beitrag = {s: [] for s in C.SEKTOREN}
    for sid, row in szen.iterrows():
        for s_name, eff in C.SZEN_BY_ID[sid]["sektoren"].items():
            b = row["p"] / 100 * eff
            sek[s_name] += b
            beitrag[s_name].append(f"{C.THEMA_BY_ID[row['thema']]['name']}: {row['name']} ({row['p']:.0f} % × {eff:+g})")
    for r in aktive:
        for s_name, eff in r["sektoren"].items():
            sek[s_name] += eff
            beitrag[s_name].append(f"Signal: {r['name']} ({eff:+g})")
    sek_df = pd.DataFrame([dict(sektor=s, score=v, beitraege="; ".join(beitrag[s]) or "–") for s, v in sek.items()])
    sek_df["empfehlung"] = np.where(sek_df["score"] > C.SEKTOR_SCHWELLE, "Übergewichten",
                                    np.where(sek_df["score"] < -C.SEKTOR_SCHWELLE, "Untergewichten", "Neutral"))
    sek_df = sek_df.sort_values("score", ascending=False)

    return dict(neutral=neutral, aktien=aktien, aktien_tilt=aktien_tilt, anleihen=100 - aktien,
                duration=duration, duration_szen=dur_szen, duration_ziele=dur_ziele, konflikt=konflikt,
                tips=tips, tips_szen=tips_szen, regeln=regeln, sektoren=sek_df)
