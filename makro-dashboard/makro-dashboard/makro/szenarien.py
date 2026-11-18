"""Szenarien Base/Bull/Bear: Definitionen (Startwerte + Team-Änderungen), Wahrscheinlichkeiten, Datenlage,
gewichtete Werte und die Simulation, aus der die Antworten auf die Dozentenfragen berechnet werden.

Ohne Streamlit, damit alles testbar bleibt.
"""
from __future__ import annotations

import copy
import json
import math

import numpy as np
import pandas as pd

from . import config as C
from .storage import zahl

TEXTFELDER = ("name", "kurz", "narrativ", "annahmen", "folge", "aktion")


# ---------------------------------------------------------------------------
# Definitionen: Startwerte aus config.py, überschrieben durch die neusten Team-Einträge
# ---------------------------------------------------------------------------
def _neuste(defs_tab: pd.DataFrame | None, bereich: str) -> pd.DataFrame:
    if defs_tab is None or defs_tab.empty:
        return pd.DataFrame(columns=["zeit", "person", "bereich", "objekt", "feld", "wert"])
    d = defs_tab[defs_tab["bereich"] == bereich]
    return d.sort_values("zeit").drop_duplicates(["objekt", "feld"], keep="last")


def szenario_defs(defs_tab: pd.DataFrame | None) -> dict:
    out = {s["id"]: copy.deepcopy(s) for s in C.SZENARIEN}
    for s in out.values():
        s["geaendert"] = {}
    for _, r in _neuste(defs_tab, "szenario").iterrows():
        s = out.get(r["objekt"])
        if s is None:
            continue
        feld, wert = str(r["feld"]), r["wert"]
        try:
            if feld in TEXTFELDER:
                s[feld] = str(wert)
            elif feld.startswith("param:"):
                v = zahl(wert)
                if v is not None:
                    s["param"][feld[6:]] = v
            elif feld.startswith("alloc:"):
                v = zahl(wert)
                if v is not None:
                    s["alloc"][feld[6:]] = v
            elif feld.startswith("sektor:"):
                v = zahl(wert)
                if v is not None:
                    s["sektoren"][feld[7:]] = v
            elif feld == "indizien":
                s["indizien"] = json.loads(wert)
            else:
                continue
            s["geaendert"][feld] = (r["person"], str(r["zeit"])[:10])
        except (ValueError, TypeError):
            continue
    return out


def risiko_defs(defs_tab: pd.DataFrame | None, neue: pd.DataFrame | None) -> list[dict]:
    risiken = [copy.deepcopy(r) for r in C.RISIKEN]
    if neue is not None and not neue.empty:
        for _, r in neue.sort_values("zeit").drop_duplicates("id", keep="last").iterrows():
            risiken.append(dict(id=r["id"], name=r["name"], kapitel=r["kapitel"] or "geopolitik", p=None, p_text="neu",
                                impact=None, warnsignal=r["warnsignal"], reaktion=r["reaktion"], auto=None,
                                quelle=f"Team ({r['person']})", richtung="ic_bear", art="Risiko"))
    by = {r["id"]: r for r in risiken}
    for _, r in _neuste(defs_tab, "risiko").iterrows():
        if r["objekt"] in by and r["feld"] in ("richtung", "art", "warnsignal", "reaktion", "name"):
            by[r["objekt"]][r["feld"]] = str(r["wert"])
    return risiken


# ---------------------------------------------------------------------------
# Wahrscheinlichkeiten: Einzelschätzungen + Team-Beschluss
# ---------------------------------------------------------------------------
def wahrscheinlichkeiten(votes: pd.DataFrame | None, beschluss: pd.DataFrame | None, defs: dict) -> pd.DataFrame:
    v = pd.DataFrame()
    if votes is not None and not votes.empty:
        v = votes[votes["szenario"].isin(C.SZEN_IDS)].sort_values("zeit").drop_duplicates(["person", "szenario"], keep="last").copy()
        v["p"] = v["p"].map(zahl)
        v = v.dropna(subset=["p"])
        if len(v):
            summe = v.groupby("person")["p"].transform("sum")
            v["p"] = np.where(summe > 0, v["p"] / summe * 100, np.nan)
    b = None
    if beschluss is not None and not beschluss.empty:
        bb = beschluss.sort_values("zeit").iloc[-1]
        werte = {sid: zahl(bb.get(sid)) for sid in C.SZEN_IDS}
        if all(x is not None for x in werte.values()) and sum(werte.values()) > 0:
            tot = sum(werte.values())
            b = dict(werte={k: x / tot * 100 for k, x in werte.items()}, person=bb["person"], zeit=str(bb["zeit"])[:10],
                     begruendung=bb.get("begruendung", ""))
    zeilen = []
    for sid in C.SZEN_IDS:
        s = defs[sid]
        d = v[v["szenario"] == sid] if len(v) else pd.DataFrame()
        mittel = float(d["p"].mean()) if len(d) else np.nan
        zeilen.append(dict(id=sid, thema="gesamt", name=s["name"], kurz=s["kurz"], start=float(C.SZEN_BY_ID[sid]["p"]),
                           p_mittel=mittel, p_min=float(d["p"].min()) if len(d) else np.nan,
                           p_max=float(d["p"].max()) if len(d) else np.nan, n=len(d),
                           p_beschluss=b["werte"][sid] if b else np.nan))
    df = pd.DataFrame(zeilen).set_index("id", drop=False)
    if b:
        df["p"], basis = df["p_beschluss"], f"Team-Beschluss vom {b['zeit']} ({b['person']})"
    elif df["n"].sum() > 0:
        df["p"] = df["p_mittel"].fillna(df["start"])
        df["p"] = df["p"] / df["p"].sum() * 100
        basis = f"Durchschnitt der Einzelschätzungen (n = {int(df['n'].max())}), noch kein Team-Beschluss"
    else:
        df["p"], basis = df["start"], "Startwerte aus dem IC-Statement (noch keine Team-Schätzung)"
    df["basis"] = basis
    df["uneinig"] = (df["p_max"] - df["p_min"]).fillna(0)
    df.attrs["beschluss"] = b
    return df


# ---------------------------------------------------------------------------
# Datenlage: Welche Szenario-Indizien sind heute erfüllt?
# ---------------------------------------------------------------------------
def _pruefe(op: str, soll, ist) -> bool | None:
    if ist is None or (isinstance(ist, float) and math.isnan(ist)):
        return None
    try:
        if op == ">":
            return ist > float(soll)
        if op == "<":
            return ist < float(soll)
        if op == "zwischen":
            lo, hi = (float(x) for x in str(soll).replace(",", ".").split(";"))
            return lo <= ist <= hi
    except (ValueError, TypeError):
        return None
    return None


def datenlage(defs: dict, status: pd.DataFrame, risiken: pd.DataFrame, szen: pd.DataFrame) -> dict:
    out = {}
    for sid, s in defs.items():
        zeilen = []
        for z in s.get("indizien", []):
            ist = status.loc[z["ind"], "wert"] if z.get("ind") in status.index else None
            ist = None if ist is None or pd.isna(ist) else float(ist)
            zeilen.append(dict(text=z.get("text") or z.get("ind"), ind=z.get("ind"), op=z.get("op"), soll=z.get("wert"),
                               ist=ist, anzeige=status.loc[z["ind"], "anzeige"] if z.get("ind") in status.index else "–",
                               ok=_pruefe(z.get("op"), z.get("wert"), ist), art="Indikator"))
        if risiken is not None and len(risiken):
            for _, r in risiken[(risiken["richtung"] == sid) & (risiken["signal"])].iterrows():
                zeilen.append(dict(text=f"Warnsignal: {r['name']}", ind=r["id"], op="", soll="", ist=None,
                                   anzeige=r["signal_auto"] or r["signal_team"] or "gemeldet", ok=True, art="Risiko"))
        bewertet = [z for z in zeilen if z["ok"] is not None]
        anteil = sum(z["ok"] for z in bewertet) / len(bewertet) if bewertet else 0.5
        out[sid] = dict(zeilen=zeilen, anteil=anteil, erfuellt=sum(bool(z["ok"]) for z in bewertet), n=len(bewertet))
    # Daten-angepasste Wahrscheinlichkeit: Team-Wahrscheinlichkeit × (0,75 + 0,5 × Anteil erfüllter Indizien), normiert
    roh = {sid: szen.loc[sid, "p"] * (0.75 + 0.5 * out[sid]["anteil"]) for sid in defs}
    tot = sum(roh.values()) or 1
    for sid in defs:
        out[sid]["p_daten"] = roh[sid] / tot * 100
        out[sid]["diff"] = out[sid]["p_daten"] - szen.loc[sid, "p"]
    fav = max(defs, key=lambda k: out[k]["anteil"])
    out["_favorit"] = fav
    out["_hinweis"] = next((sid for sid in defs if out[sid]["diff"] >= 10), None)
    return out


# ---------------------------------------------------------------------------
# Gewichtete Werte
# ---------------------------------------------------------------------------
def gewichtet(defs: dict, szen: pd.DataFrame) -> dict:
    p = {sid: szen.loc[sid, "p"] / 100 for sid in defs}
    out = {"param": {}, "alloc": {}, "sektoren": {}}
    for par in C.PARAMETER:
        out["param"][par["id"]] = sum(p[s] * float(defs[s]["param"].get(par["id"], 0) or 0) for s in defs)
    for par in C.ALLOK_PARAMETER:
        out["alloc"][par["id"]] = sum(p[s] * float(defs[s]["alloc"].get(par["id"], 0) or 0) for s in defs)
    for sek in C.SEKTOREN:
        out["sektoren"][sek] = sum(p[s] * float(defs[s]["sektoren"].get(sek, 0) or 0) for s in defs)
    out["param"]["def_minus_zykl"] = (np.mean([out["sektoren"][x] for x in C.DEFENSIV])
                                      - np.mean([out["sektoren"][x] for x in C.ZYKLISCH]))
    return out


def def_minus_zykl(s: dict) -> float:
    return float(np.mean([s["sektoren"].get(x, 0) for x in C.DEFENSIV]) - np.mean([s["sektoren"].get(x, 0) for x in C.ZYKLISCH]))


# ---------------------------------------------------------------------------
# Historische Streuung (Monat) aus den Daten
# ---------------------------------------------------------------------------
def _monatlich(s: pd.Series | None) -> pd.Series | None:
    if s is None or len(s.dropna()) < 40:
        return None
    s = s.dropna()
    s = s[s.index >= s.index[-1] - pd.DateOffset(years=5)]
    return s.resample("ME").last().dropna()


def sigmas(fred: dict, hist: dict) -> dict:
    """Standardabweichung der Monatsveränderung der letzten 5 Jahre; Fallback config. Gibt {id: (sigma, quelle)}."""
    out = {}

    def setze(pid, wert, quelle):
        if wert is not None and np.isfinite(wert) and wert > 0:
            out[pid] = (float(wert), quelle)

    m = _monatlich(hist.get("^SP500TR"))
    if m is not None:
        setze("sp500", m.pct_change().std() * 100, "Yahoo ^SP500TR, 5 J.")
    for pid, serie in (("dgs10", "DGS10"), ("dgs2", "DGS2")):
        m = _monatlich(fred.get(serie))
        if m is not None:
            setze(pid, m.diff().std() * 100, f"FRED {serie}, 5 J.")
    m = _monatlich(hist.get("CL=F")) if hist.get("CL=F") is not None else _monatlich(fred.get("DCOILWTICO"))
    if m is not None:
        setze("wti", m.pct_change().clip(-0.6, 0.6).std() * 100, "WTI, 5 J.")
    m = _monatlich(hist.get("^VIX")) if hist.get("^VIX") is not None else _monatlich(fred.get("VIXCLS"))
    if m is not None:
        setze("vix", m.diff().std(), "VIX, 5 J.")
    a, b = _monatlich(hist.get("IWF")), _monatlich(hist.get("IWD"))
    if a is not None and b is not None:
        d = (a.pct_change() - b.pct_change()).dropna() * 100
        setze("growth_value", d.std(), "Yahoo IWF − IWD, 5 J.")
    cpi = fred.get("CPIAUCSL")
    if cpi is not None and len(cpi.dropna()) > 30:
        mm = cpi.dropna().pct_change().dropna() * 100
        mm = mm[mm.index >= mm.index[-1] - pd.DateOffset(years=5)]
        setze("cpi_mm", mm.std(), "FRED CPIAUCSL, 5 J.")
    m = _monatlich(fred.get("UNRATE"))
    if m is not None:
        setze("arbeitslos", m.diff().clip(-0.5, 0.5).std(), "FRED UNRATE, 5 J.")
    m = _monatlich(hist.get("DX-Y.NYB"))
    if m is not None:
        setze("dxy", m.pct_change().std() * 100, "Yahoo DX-Y.NYB, 5 J.")
    for par in C.PARAMETER:  # Ausreisser begrenzen: höchstens Faktor 2 weg von der Annahme
        if par["id"] in out and par["sigma"]:
            v, q = out[par["id"]]
            out[par["id"]] = (float(min(max(v, 0.5 * par["sigma"]), 2 * par["sigma"])), q)
    for par in C.PARAMETER:
        if par["id"] not in out and par["sigma"]:
            out[par["id"]] = (float(par["sigma"]), "Annahme (keine Daten)")
    return out


# ---------------------------------------------------------------------------
# Basiseffekt CPI: aus der Monatsveränderung die neue Jahresrate
# ---------------------------------------------------------------------------
def cpi_basis(fred: dict, monat_id: str) -> dict | None:
    s = fred.get("CPIAUCSL")
    if s is None or len(s.dropna()) < 26:
        return None
    s = s.dropna().copy()
    s.index = s.index.to_period("M").to_timestamp()
    idx = pd.date_range(s.index.min(), s.index.max(), freq="MS")
    fehlend = [d for d in idx if d not in s.index]
    s = np.exp(np.log(s.reindex(idx)).interpolate())  # fehlende Monate (z. B. Okt. 2025) geometrisch interpoliert
    q = pd.Timestamp(monat_id + "-01")
    daten_monat = q - pd.DateOffset(months=1)          # im Dezember erscheint der November-CPI
    letzter = s.index[-1]
    vor = daten_monat - pd.DateOffset(months=1)
    if letzter < vor:
        luecke = (vor.year - letzter.year) * 12 + vor.month - letzter.month
        info_luecke = f"Der letzte verfügbare CPI ist {letzter:%m/%Y}; {luecke} Monat(e) dazwischen werden mit dem Szenario-Wert fortgeschrieben."
    else:
        luecke, info_luecke = 0, ""
    def wert(d):
        return float(s.loc[d]) if d in s.index else None
    i_vor = wert(min(vor, letzter))
    i_basis_neu = wert(daten_monat - pd.DateOffset(months=12))
    i_basis_vor = wert(vor - pd.DateOffset(months=12))
    if None in (i_vor, i_basis_neu, i_basis_vor):
        return None
    yoy_vor = (wert(min(vor, letzter)) / i_basis_vor - 1) * 100 if luecke == 0 else None
    basis_mm = (i_basis_neu / i_basis_vor - 1) * 100
    return dict(i_vor=i_vor, luecke=luecke, i_basis_neu=i_basis_neu, yoy_vor=yoy_vor, daten_monat=daten_monat,
                basis_mm=basis_mm, info=info_luecke,
                interpoliert=[d.strftime("%m/%Y") for d in fehlend if d >= daten_monat - pd.DateOffset(months=14)])


# ---------------------------------------------------------------------------
# Simulation (Monte Carlo): Szenario ziehen, dann jede Grösse mit der Streuung im Szenario
# ---------------------------------------------------------------------------
def simuliere(defs: dict, szen: pd.DataFrame, sig: dict, cpi: dict | None, bond: dict, n: int = 20000,
              seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ids = list(defs)
    p = np.array([max(szen.loc[s, "p"], 0) for s in ids], dtype=float)
    p = p / p.sum()
    wahl = rng.choice(len(ids), size=n, p=p)
    f = C.STREUUNG_IM_SZENARIO

    def mu(pid):
        return np.array([float(defs[s]["param"].get(pid, 0) or 0) for s in ids])[wahl]

    def sd(pid):
        return sig.get(pid, (C.PARAM_BY_ID[pid]["sigma"] or 0, ""))[0] * f

    z_sp = rng.standard_normal(n)
    z_zins = rng.standard_normal(n)
    d = pd.DataFrame({"szenario": np.array(ids)[wahl]})
    d["sp500"] = mu("sp500") + sd("sp500") * z_sp
    d["fed"] = mu("fed")
    d["dgs10"] = mu("dgs10") + sd("dgs10") * z_zins
    d["dgs2"] = mu("dgs2") + sd("dgs2") * (0.8 * z_zins + 0.6 * rng.standard_normal(n))
    d["vix"] = mu("vix") + sd("vix") * (-0.7 * z_sp + 0.71 * rng.standard_normal(n))
    d["wti"] = mu("wti") + sd("wti") * rng.standard_normal(n)
    d["growth_value"] = mu("growth_value") + sd("growth_value") * (0.3 * z_sp + 0.95 * rng.standard_normal(n))
    d["cpi_mm"] = mu("cpi_mm") + sd("cpi_mm") * rng.standard_normal(n)
    d["arbeitslos"] = mu("arbeitslos") + sd("arbeitslos") * rng.standard_normal(n)
    d["dxy"] = mu("dxy") + sd("dxy") * rng.standard_normal(n)
    # Anleihen-Benchmark: Carry − Duration × Renditeänderung bei der mittleren Laufzeit
    t = bond.get("laufzeit", 6.0)
    w = min(max((t - 2) / 8, 0), 1.2)
    dy = d["dgs2"] + (d["dgs10"] - d["dgs2"]) * w
    d["anleihen"] = bond.get("rendite", 4.5) / 12 - bond.get("duration", 5.5) * dy / 100
    d["aktien_minus_anleihen"] = d["sp500"] - d["anleihen"]
    # Sektoren relativ zum S&P 500: Szenario-Wirkung + Risk-on/off-Faktor + eigene Streuung
    faktor = C.FAKTOR_SIGMA * (0.6 * z_sp + 0.8 * rng.standard_normal(n))
    for sek in C.SEKTOREN:
        m = np.array([float(defs[s]["sektoren"].get(sek, 0) or 0) for s in ids])[wahl]
        d[f"sek:{sek}"] = m + C.SEKTOR_FAKTOR[sek] * faktor + C.SEKTOR_SIGMA * rng.standard_normal(n)
    d["def_minus_zykl"] = d[[f"sek:{x}" for x in C.DEFENSIV]].mean(axis=1) - d[[f"sek:{x}" for x in C.ZYKLISCH]].mean(axis=1)
    # CPI: neue Jahresrate (gerundet wie veröffentlicht) gegen die vorherige
    if cpi:
        i_neu = cpi["i_vor"] * (1 + d["cpi_mm"] / 100) ** (1 + cpi["luecke"])
        yoy_neu = (i_neu / cpi["i_basis_neu"] - 1) * 100
        if cpi["yoy_vor"] is not None:
            d["cpi_yoy_diff"] = yoy_neu.round(1) - round(cpi["yoy_vor"], 1)
        else:
            i_vor = cpi["i_vor"] * (1 + d["cpi_mm"] / 100) ** cpi["luecke"]
            yoy_vor = (i_vor / (cpi["i_basis_neu"] / (1 + cpi["basis_mm"] / 100)) - 1) * 100
            d["cpi_yoy_diff"] = yoy_neu.round(1) - yoy_vor.round(1)
        d["cpi_yoy_neu"] = yoy_neu
    else:  # ohne CPI-Daten: Näherung über Monatsrate minus 0,25 % Basismonat
        d["cpi_yoy_diff"] = (d["cpi_mm"] - 0.25).round(1)
        d["cpi_yoy_neu"] = np.nan
    return d


def antwort_wahrscheinlichkeiten(sim: pd.DataFrame, frage: dict) -> list[float]:
    if frage["art"] == "sektoren":
        return []
    x = sim[frage["groesse"]]
    out = []
    for _, lo, hi in frage["optionen"]:
        m = pd.Series(True, index=x.index)
        if lo is not None:
            m &= x > lo
        if hi is not None:
            m &= x <= hi if frage["art"] != "fed" else x < hi
        out.append(float(m.mean() * 100))
    tot = sum(out) or 1
    return [v / tot * 100 for v in out]


def sektor_wahrscheinlichkeiten(sim: pd.DataFrame) -> pd.DataFrame:
    cols = [f"sek:{s}" for s in C.SEKTOREN]
    rang = sim[cols].rank(axis=1, ascending=False)
    return pd.DataFrame({"sektor": C.SEKTOREN,
                         "erwartet": [float(sim[c].mean()) for c in cols],
                         "p_oben": [float((rang[c] <= 3).mean() * 100) for c in cols],
                         "p_unten": [float((rang[c] >= len(cols) - 2).mean() * 100) for c in cols],
                         "rang": [float(rang[c].mean()) for c in cols]}).sort_values("erwartet", ascending=False)


def ueberzeugung_vorschlag(p: float) -> str:
    """Stufe, deren Prozentwert der Modell-Wahrscheinlichkeit am nächsten liegt."""
    return min(C.UEBERZEUGUNG, key=lambda s: abs(s[1] - p))[0]


def stufe_text(name: str) -> str:
    return next((f"{n} ({w} %)" for n, w in C.UEBERZEUGUNG if n == name), name)


# ---------------------------------------------------------------------------
# Daten-Anker: was die aktuellen Daten für jeden Parameter nahelegen (zur Kalibrierung der Szenario-Werte)
# ---------------------------------------------------------------------------
def _de(x, d=1, vz=True):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "–"
    s = f"{x:{'+' if vz else ''},.{d}f}".replace(",", "'").replace(".", ",")
    return s.replace("-", "−")


def daten_anker(fred: dict, hist: dict, status: pd.DataFrame, fed_markt_bp: float | None = None) -> dict:
    out = {}

    def w(i):
        v = status.loc[i, "wert"] if i in status.index else None
        return None if v is None or pd.isna(v) else float(v)

    erp, y10 = w("erp"), w("dgs10")
    m = _monatlich(hist.get("^SP500TR"))
    mom = float(m.pct_change().iloc[-1] * 100) if m is not None and len(m) > 2 else None
    if erp is not None and y10 is not None:
        out["sp500"] = f"(ERP + 10 J.)/12 ≈ {_de((erp + y10) / 12, 2)} % · letzter Monat {_de(mom)} %"
    if fed_markt_bp is not None:
        out["fed"] = f"Markt (T-Bills, 0–3 Mt.): {_de(fed_markt_bp, 0)} bp"
    for pid, serie in (("dgs2", "DGS2"), ("dgs10", "DGS10")):
        m = _monatlich(fred.get(serie))
        if m is not None and len(m) > 2:
            out[pid] = f"letzter Monat {_de(float(m.diff().iloc[-1]) * 100, 0)} bp · 3 Mt. Ø {_de(float(m.diff().tail(3).mean()) * 100, 0)} bp"
    cpi = fred.get("CPIAUCSL")
    if cpi is not None and len(cpi.dropna()) > 15:
        mm = cpi.dropna().pct_change().dropna() * 100
        out["cpi_mm"] = f"3 Mt. Ø {_de(float(mm.tail(3).mean()), 2, False)} % m/m · Vorjahresmonat {_de(float(mm.iloc[-12]), 2, False)} %"
    m = _monatlich(hist.get("CL=F")) if hist.get("CL=F") is not None else _monatlich(fred.get("DCOILWTICO"))
    if m is not None and len(m) > 2:
        out["wti"] = f"letzter Monat {_de(float(m.pct_change().iloc[-1] * 100))} % · Niveau {_de(float(m.iloc[-1]), 1, False)} USD"
    v = fred.get("VIXCLS") if fred.get("VIXCLS") is not None else hist.get("^VIX")
    if v is not None and len(v.dropna()) > 300:
        v = v.dropna()
        mittel = float(v[v.index >= v.index[-1] - pd.DateOffset(years=5)].median())
        out["vix"] = (f"heute {_de(float(v.iloc[-1]), 1, False)}, 5-J.-Median {_de(mittel, 1, False)} → Rückkehr zum Mittel ≈ "
                      f"{_de(0.3 * (mittel - float(v.iloc[-1])))} Pkt./Mt.")
    a, b = _monatlich(hist.get("IWF")), _monatlich(hist.get("IWD"))
    if a is not None and b is not None and len(a) > 4:
        d = (a.pct_change() - b.pct_change()).dropna() * 100
        out["growth_value"] = f"letzter Monat {_de(float(d.iloc[-1]))} PP · 3 Mt. Ø {_de(float(d.tail(3).mean()))} PP"
    m = _monatlich(fred.get("UNRATE"))
    if m is not None and len(m) > 4:
        out["arbeitslos"] = f"3 Mt. Ø {_de(float(m.diff().tail(3).mean()), 2)} PP/Mt. · heute {_de(float(m.iloc[-1]), 1, False)} %"
    m = _monatlich(hist.get("DX-Y.NYB"))
    if m is not None and len(m) > 2:
        out["dxy"] = f"letzter Monat {_de(float(m.pct_change().iloc[-1] * 100))} %"
    return out


def taylor(status: pd.DataFrame) -> dict | None:
    """Taylor-Regel mit Kern-PCE und Output Gap; Vergleich mit der Obergrenze des Fed-Zielbands."""
    def w(i):
        v = status.loc[i, "wert"] if i in status.index else None
        return None if v is None or pd.isna(v) else float(v)
    pi, gap, fed = w("kernpce"), w("outputgap"), w("fedfunds")
    if pi is None or gap is None:
        return None
    i = C.TAYLOR_R_NEUTRAL + pi + 0.5 * (pi - C.TAYLOR_PI_ZIEL) + 0.5 * gap
    return dict(i=i, pi=pi, gap=gap, fed=fed, diff=(i - fed) if fed is not None else None)


# ---------------------------------------------------------------------------
# Konsistenz-Check: Sind die Szenarien in sich stimmig? (CFA: Szenarien müssen intern konsistent sein)
# ---------------------------------------------------------------------------
def konsistenz(defs: dict, szen: pd.DataFrame) -> list[tuple[str, str]]:
    out = []
    for sid, s in defs.items():
        p, k = s["param"], s["kurz"]
        sp, vix, fed, d2, d10 = (float(p.get(x, 0) or 0) for x in ("sp500", "vix", "fed", "dgs2", "dgs10"))
        if sp <= -2 and vix < 0:
            out.append(("fehler", f"{k}: Aktien fallen ({_de(sp)} %), aber der VIX sinkt – in Stressphasen steigt der VIX."))
        if sp >= 2 and vix > 2:
            out.append(("warnung", f"{k}: Starker Aktienmonat ({_de(sp)} %) mit steigendem VIX ({_de(vix)}) ist selten."))
        if fed > 0 and d2 < -10:
            out.append(("warnung", f"{k}: Die Fed erhöht, aber die 2-J.-Rendite fällt um {_de(-d2, 0, False)} bp – das passt nur, wenn der "
                                   "Markt danach rasche Senkungen erwartet."))
        if fed < 0 and d2 > 10:
            out.append(("warnung", f"{k}: Die Fed senkt, aber die 2-J.-Rendite steigt."))
        dz = def_minus_zykl(s)
        if sp >= 2 and dz > 0.5:
            out.append(("warnung", f"{k}: Defensive schlagen Zykliker ({_de(dz)} PP) in einem starken Aktienmonat – eher untypisch."))
        if sp <= -2 and dz < -0.5:
            out.append(("warnung", f"{k}: Zykliker schlagen Defensive in einem schwachen Aktienmonat – eher untypisch."))
        wti, en = float(p.get("wti", 0) or 0), float(s["sektoren"].get("Energie", 0) or 0)
        if wti >= 5 and en < 0:
            out.append(("fehler", f"{k}: Öl steigt ({_de(wti)} %), Energie liegt aber unter dem S&P 500."))
        if wti <= -5 and en > 0:
            out.append(("fehler", f"{k}: Öl fällt ({_de(wti)} %), Energie liegt aber über dem S&P 500."))
        gv = float(p.get("growth_value", 0) or 0)
        if d10 >= 25 and gv >= 1.5:
            out.append(("warnung", f"{k}: Stark steigende lange Zinsen (+{_de(d10, 0, False)} bp) und gleichzeitig Growth deutlich vorne – "
                                   "höhere Zinsen belasten normalerweise lange Duration-Aktien (Growth)."))
        sek_mittel = sum(float(s["sektoren"].get(x, 0) or 0) * C.SP500_GEWICHT[x] for x in C.SEKTOREN) / 100
        if abs(sek_mittel) > 0.3:
            out.append(("warnung", f"{k}: Die Sektor-Mehrrenditen ergeben gewichtet {_de(sek_mittel, 2)} PP – gegenüber dem S&P 500 "
                                   "sollten sie sich (mit S&P-Gewichten) ungefähr aufheben."))
    if {"ic_base", "ic_bull", "ic_bear"} <= set(defs):
        sp = {s: float(defs[s]["param"].get("sp500", 0) or 0) for s in defs}
        if not sp["ic_bull"] > sp["ic_base"] > sp["ic_bear"]:
            out.append(("fehler", "Reihenfolge: Die Aktienrendite sollte Bull > Base > Bear sein."))
    for sid in defs:
        if szen.loc[sid, "p"] < 5:
            out.append(("warnung", f"{defs[sid]['kurz']}: Wahrscheinlichkeit unter 5 % – dann eher als Risiko führen."))
    if szen["p"].max() >= 75:
        out.append(("warnung", f"Ein Szenario hat {szen['p'].max():.0f} % – Vorsicht vor Überzuversicht (overconfidence)."))
    return out
