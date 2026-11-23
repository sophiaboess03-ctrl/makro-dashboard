"""Gemeinsame Berechnung für App und Morgen-Mail: Live-Kurse einmischen, Ampeln, Signale, Szenarien, IPS, Taktik."""
from __future__ import annotations

import pandas as pd

from makro import config as C
from makro import logic as L
from makro import szenarien as SZ

# Lange Kursgeschichte (6 J.) für Streuung, Auflösung der Dozentenfragen und Risiko-Schätzungen
HIST_TICKER = tuple(sorted({"^SP500TR", "IWF", "IWD", "GOVT", "IEI", "SPY", "CL=F", "^VIX", "DX-Y.NYB", "^IRX",
                            *C.SEKTOR_ETF.values()}))


def mische_markt(fred_roh: dict, yahoo: dict) -> tuple[dict, dict]:
    """Live-Kurse von Yahoo ergänzen die FRED-Reihen (gleiches Datum: Yahoo gewinnt) → Ampeln = Kursleiste."""
    fred = dict(fred_roh)
    labels = {}
    for ticker, reihe in C.MARKT_ERGAENZT_FRED.items():
        if ticker in yahoo:
            y = yahoo[ticker].dropna()
            if ticker == "^TNX" and len(y) and y.iloc[-1] > 20:
                y = y / 10
            f = fred_roh.get(reihe)
            fred[reihe] = y if f is None else pd.concat([f, y])[lambda x: ~x.index.duplicated(keep="last")].sort_index()
            labels[reihe] = "FRED + Yahoo"
    for i in C.INDIKATOREN:
        if i["quelle"] == "markt" and i["series"] in yahoo:
            fred[i["series"]] = yahoo[i["series"]].dropna()
            labels[i["series"]] = "Yahoo Finance"
    return fred, labels


def text_einstellungen(df: pd.DataFrame | None) -> dict:
    out = {"bond_benchmark": C.BOND_BENCHMARK_DEFAULT}
    if df is not None and not df.empty:
        for _, r in df.sort_values("zeit").iterrows():
            if r["schluessel"] == "bond_benchmark" and r["wert"] in C.BOND_BENCHMARKS:
                out["bond_benchmark"] = r["wert"]
    return out


def berechne(fred: dict, labels: dict, tabellen: dict, hist: dict | None = None, kurse: dict | None = None) -> dict:
    import anleihen as AN
    import ips as IPS
    import sektoren as SK
    import taktik as T

    hist = hist or {}
    # Eine Quelle für die S&P-500-Sektorgewichte im ganzen Dashboard (Seite «Sektorallokation»)
    sek_index = SK.index_info(tabellen.get("sektor_index"), kurse, hist)
    C.SP500_GEWICHT.update(sek_index["gewichte"])
    daten = L.Daten(fred, tabellen["werte"], labels)
    status = L.status(daten)
    kombis = L.kombi_signale(status, daten)
    sdefs = SZ.szenario_defs(tabellen.get("definitionen"))
    rdefs = SZ.risiko_defs(tabellen.get("definitionen"), tabellen.get("risiken_neu"))
    szen = SZ.wahrscheinlichkeiten(tabellen.get("szenario_votes"), tabellen.get("szenario_beschluss"), sdefs)
    risiken = L.risiko_bewertung(tabellen["risiko_votes"], rdefs, status, kombis)
    einst = {**L.einstellungen(tabellen["einstellungen"]), **text_einstellungen(tabellen["einstellungen"])}
    ips = IPS.werte(tabellen.get("ips"))
    neutral = IPS.neutral(ips, einst)
    gew = SZ.gewichtet(sdefs, szen)
    lage = SZ.datenlage(sdefs, status, risiken, szen)
    regeln = L.signal_regeln(status, risiken, kombis)
    m_gueltig, m_arbeit = T.monat_gueltig(), T.monat_in_arbeit()
    t, monat = None, m_arbeit
    if m_gueltig:
        t, monat = T.lese(tabellen.get("taktik"), m_gueltig["id"]), m_gueltig
    if t is None:
        t, monat = T.lese(tabellen.get("taktik"), m_arbeit["id"]), m_arbeit
    alloc = T.alloc_dict(t, monat, neutral, ips, regeln, gew, tabellen)
    bond = AN.bond_basis(fred, einst["bond_benchmark"])
    sig = SZ.sigmas(fred, hist)
    return dict(daten=daten, status=status, kombis=kombis, szen=szen, risiken=risiken, alloc=alloc, szdef=sdefs, gew=gew,
                lage=lage, ips=ips, einst=einst, bond=bond, sig=sig, monat_arbeit=m_arbeit, sek_index=sek_index,
                zyklus_team=L.zyklus_team(tabellen["zyklus_votes"]), zyklus_modell=L.zyklus_modell(status))


def fred_serien(markt_fred: list[str]) -> list[str]:
    return sorted({i["series"] for i in C.INDIKATOREN if i["quelle"] == "fred"} | {"GDPC1", "GDPPOT", C.REZESSION_SERIE}
                  | set(C.ZINSKURVE) | set(C.ERWARTUNGS_SERIEN) | set(markt_fred) | {"CPIAUCSL", "DFEDTARU", "T5YIFR"} | set(_trigger_serien()))


def _trigger_serien() -> list[str]:
    """FRED-Reihen für die Ausstiegs-Trigger der Sektorallokation (z. B. Hochzins-Spread)."""
    import sektoren as SK

    return SK.TRIGGER_SERIEN
