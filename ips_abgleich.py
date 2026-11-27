"""IPS ↔ Portfolio aus Risikosicht (Update 16, nach der Folie «Connecting the IPS and the portfolio from a risk perspective»).

Das IPS sagt, welches Risiko eingegangen werden DARF; das Portfolio zeigt, welches Risiko TATSÄCHLICH eingegangen wird.
Die Verbindung: jede IPS-Aussage wird in eine messbare Portfolio-Kennzahl mit Grenze übersetzt und regelmässig geprüft
(Gap-Analyse).

  IPS-Element                    Bedeutung fürs Risiko                              Kennzahl im Portfolio
  Risikobereitschaft (willing.)  Schwankung/Verlust, die der Kunde emotional trägt  Volatilität, Drawdown
  Risikofähigkeit (ability)      Verlust, den der Kunde tragen kann                 Stress-Verlust, Liquidität vs. Cash-Bedarf
  Gesamttoleranz                 Die tiefere von Bereitschaft und Fähigkeit gilt    die engere der beiden Grenzen
  Renditeziel                    Rendite muss mit der Risikotoleranz erreichbar sein erwartete vs. erforderliche Rendite
  Beschränkungen                 Horizont, Liquidität, Recht, Besonderes            Anlageklassen-Bänder, Duration, Titel, Ausschlüsse
  SAA und Benchmark              langfristiger Risikoanker, Bänder, Rebalancing     Gewichte vs. Bänder, TE und Active Share vs. Benchmark

Gemessen wird doppelt, wo möglich:
- historisch (ex post): heutige Gewichte mit den Tagesrenditen der gewählten Periode (analyse.rechne),
- ex ante: Szenario-Simulation des Dashboards (risiko.berechne, 3 Monate bis Saisonende) mit Aufteilung, Sektoren und Duration
  des Portfolios.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import streamlit as st

from makro import config as C
from makro import logic as L
from makro import ui

OK, WARN, VERSTOSS, OFFEN = L.GRUEN, L.GELB, L.ROT, "offen"
STATUS_TEXT = {OK: "eingehalten", WARN: "knapp / prüfen", VERSTOSS: "verletzt", OFFEN: "nicht messbar"}
Z95 = 1.645


def _d(x, n=1, vz=False):
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else ui.de(x, n, vz)


def _schlechter(*st_):
    rang = {VERSTOSS: 3, WARN: 2, OK: 1, OFFEN: 0}
    st_ = [s for s in st_ if s is not None]
    return max(st_, key=lambda s: rang[s]) if st_ else OFFEN


def _grenze_oben(wert, grenze, knapp: float = 0.9):
    if wert is None or grenze is None:
        return OFFEN
    return OK if wert <= grenze * knapp else WARN if wert <= grenze else VERSTOSS


def _band(wert, lo, hi, knapp: float = 0.0):
    if wert is None:
        return OFFEN
    if lo - 1e-9 <= wert <= hi + 1e-9:
        return WARN if knapp and (wert - lo < knapp or hi - wert < knapp) else OK
    return VERSTOSS


# ---------------------------------------------------------------------------
# Messwerte
# ---------------------------------------------------------------------------
def ex_ante(ctx, aktien: float, anleihen: float, cash: float, sektoren_ges: dict, duration: float | None) -> dict | None:
    """Szenario-Simulation (risiko.berechne) mit der Aufteilung eines Portfolios. sektoren_ges = % des Gesamtportfolios."""
    try:
        import risiko

        werte = dict(aktien=float(aktien), anleihen=float(anleihen), cash=float(cash),
                     sektoren={s: float(sektoren_ges.get(s, 0.0)) for s in C.SEKTOREN},
                     duration=float(duration if duration else ctx.alloc.get("duration") or 4.0))
        return risiko.berechne(ctx, werte)
    except Exception:  # noqa: BLE001
        return None


def schlechteste_3m(r: pd.Series | None) -> float | None:
    """Schlechteste rollende 63-Handelstage-Rendite in % (historischer Stress)."""
    if r is None or len(r.dropna()) < 70:
        return None
    w = (1 + r.dropna()).cumprod()
    return float((w / w.shift(63) - 1).min() * 100)


def messwerte_portfolio(ctx, erg: dict, pos: pd.DataFrame, total: float, cash: float, info: dict, f_a: float = 0.0,
                        f_t: float = 0.0, duration: float | None = None, mit_ex_ante: bool = True) -> dict:
    """Alle Grössen für die Gap-Analyse eines Analyseportfolios bzw. des Bestands (Ergebnis von analyse.rechne)."""
    import analyse as A

    k = erg["k"]
    kv, ka = k.get("Vorauswahl", {}), k.get("Aktienteil", {})
    ak = pos[pos["klasse"] == "Aktie"]
    tr = pos[pos["klasse"] == "Treasury"]
    ak_w, tr_w = float(ak["wert"].sum()), float(tr["wert"].sum())
    cash_eff = max(0.0, cash - f_a - f_t)
    a_pct, t_pct, c_pct = (ak_w + f_a) / total * 100, (tr_w + f_t) / total * 100, cash_eff / total * 100
    if duration is None:
        teile = [(A.PROXY[b][1], w) for b, w in zip(tr["band"], tr["wert"]) if b in A.PROXY]
        if f_t:
            teile.append((float(ctx.bond.get("duration") or 6.0), f_t))
        duration = sum(d * w for d, w in teile) / sum(w for _, w in teile) if teile else None
    # Sektoren (Anteil am Aktienteil inkl. offenes Budget als Index)
    sek_ak = {s: 0.0 for s in C.SEKTOREN}
    for t, w in ak.groupby("ticker")["wert"].sum().items():
        s = (info.get(t) or {}).get("sektor")
        if s in sek_ak:
            sek_ak[s] += float(w)
    try:
        import benchmark as BM

        bm_sek = BM.sektor_gewichte("gueltig")
    except Exception:  # noqa: BLE001
        bm_sek = dict(C.SP500_GEWICHT)
    if f_a:
        for s in C.SEKTOREN:
            sek_ak[s] += f_a * bm_sek.get(s, 0.0) / 100
    summe = sum(sek_ak.values())
    sek_pct = {s: v / summe * 100 for s, v in sek_ak.items()} if summe else {}
    sek_aktiv = {s: sek_pct.get(s, 0.0) - bm_sek.get(s, 0.0) for s in C.SEKTOREN} if summe else {}
    gross = ak.groupby("ticker")["wert"].sum().sort_values(ascending=False) if len(ak) else pd.Series(dtype=float)
    m = dict(
        quelle_hist=f"historisch {kv.get('von'):%d.%m.%Y} – {kv.get('bis'):%d.%m.%Y}" if kv.get("von") is not None else "historisch",
        vola_hist=kv.get("vola"), mdd_hist=kv.get("mdd"), stress_hist=schlechteste_3m(erg.get("r_p")),
        te_hist=kv.get("te_bench"), beta_aktien=ka.get("beta"), active_share=ka.get("active_share"),
        active_share_inkl=kv.get("active_share") if f_a else None,
        aktien_pct=a_pct, treas_pct=t_pct, cash_pct=c_pct, duration=duration,
        titel_max=(gross.index[0], float(gross.iloc[0]) / total * 100) if len(gross) else None,
        n_aktien=int(len(gross)), sektor_aktiv=sek_aktiv,
        nicht_im_index=[t for t in gross.index if (info.get(t) or {}).get("im_index") is False],
    )
    try:
        import benchmark as BM

        bm = BM.gewichte()
        if len(bm):
            m["nicht_im_index"] = sorted(set(m["nicht_im_index"]) | {t for t in gross.index if BM.norm_ticker(t) not in bm.index})
    except Exception:  # noqa: BLE001
        pass
    ips = ctx.ips
    aus_t = {x.strip().upper() for x in str(ips.get("aus_titel") or "").replace(";", ",").split(",") if x.strip()}
    aus_s = set(ips.get("aus_sektoren") or [])
    m["ausgeschlossen"] = [t for t in gross.index if t in aus_t or (info.get(t) or {}).get("sektor") in aus_s]
    if mit_ex_ante and total > 0:
        r = ex_ante(ctx, a_pct, t_pct, c_pct, {s: sek_pct.get(s, 0.0) * a_pct / 100 for s in C.SEKTOREN}, duration)
        if r:
            m.update(vola_ea=r["vola"], es95_3=r["es95_3"], var95_3=r["var95_3"], e3=r["e3"], e3_b=r["e3_b"], akt3=r["akt3"],
                     te_ea=r["te"], bear_3m=(r.get("szen") or {}).get("ic_bear", {}).get("r_p"), s3=r["s3"])
    return m


def messwerte_taktik(ctx) -> dict:
    """Ex ante mit der Taktik des Monats (für die Seite IPS): Aufteilung, Sektoren, Duration aus der Taktik."""
    a = ctx.alloc
    try:
        sek = {r["sektor"]: float(r["gewicht"]) for _, r in a["sektoren"].iterrows()}
    except Exception:  # noqa: BLE001
        sek = {}
    ak = float(a["aktien"])
    sek_pct = {s: (sek.get(s, 0.0) / ak * 100 if ak else 0.0) for s in C.SEKTOREN}
    try:
        import benchmark as BM

        bm_sek = BM.sektor_gewichte("gueltig")
    except Exception:  # noqa: BLE001
        bm_sek = dict(C.SP500_GEWICHT)
    m = dict(aktien_pct=ak, treas_pct=float(a["anleihen"]), cash_pct=float(a["cash"]), duration=float(a.get("duration") or 0) or None,
             sektor_aktiv={s: sek_pct[s] - bm_sek.get(s, 0.0) for s in C.SEKTOREN} if ak else {},
             active_share_sektor=sum(abs(sek_pct[s] - bm_sek.get(s, 0.0)) for s in C.SEKTOREN) / 2 if ak else None)
    r = ex_ante(ctx, ak, float(a["anleihen"]), float(a["cash"]), sek, m["duration"])
    if r:
        m.update(vola_ea=r["vola"], es95_3=r["es95_3"], var95_3=r["var95_3"], e3=r["e3"], e3_b=r["e3_b"], akt3=r["akt3"],
                 te_ea=r["te"], bear_3m=(r.get("szen") or {}).get("ic_bear", {}).get("r_p"), s3=r["s3"])
        try:
            import risiko

            kz = risiko.kennzahlen_aktienteil(risiko.taktik_werte(ctx), risiko.sektor_betas(ctx.hist))
            m["beta_aktien"] = kz.get("beta")
        except Exception:  # noqa: BLE001
            pass
    return m


# ---------------------------------------------------------------------------
# Gap-Analyse
# ---------------------------------------------------------------------------
def gap_analyse(ips: dict, alloc: dict, m: dict) -> list[dict]:
    """Zeilen: IPS-Element, Bedeutung, Kennzahl, Grenze (IPS), Ist, Status, Hinweis."""
    z = []

    def add(element, bedeutung, kennzahl, grenze, ist, status, hinweis=""):
        z.append({"IPS-Element": element, "Was es fürs Risiko heisst": bedeutung, "Kennzahl im Portfolio": kennzahl,
                  "Grenze (IPS)": grenze, "Ist": ist, "Status": status, "Hinweis": hinweis})

    vmax, lmax, temax = ips.get("vola_max"), ips.get("verlust_max"), ips.get("te_max")
    hist = m.get("quelle_hist", "historisch")
    # 1 Risikobereitschaft
    st_v = _schlechter(_grenze_oben(m.get("vola_hist"), vmax), _grenze_oben(m.get("vola_ea"), vmax))
    add("Risikobereitschaft (Willingness)", f"Wie viel Schwankung der Kunde emotional akzeptiert – Einstellung «{ips.get('bereitschaft', '?')}»",
        "Volatilität p.a. (absolutes Risikoziel)", f"≤ {_d(vmax, 1)} %",
        " · ".join(x for x in (f"hist. {_d(m.get('vola_hist'), 1)} %" if m.get("vola_hist") is not None else "",
                               f"ex ante {_d(m.get('vola_ea'), 1)} %" if m.get("vola_ea") is not None else "") if x) or "–",
        st_v, f"{hist}; ex ante = Szenario-Simulation")
    st_dd = OFFEN if m.get("mdd_hist") is None or lmax is None else (
        OK if -m["mdd_hist"] <= lmax * 0.9 else WARN if -m["mdd_hist"] <= lmax else VERSTOSS)
    if "mdd_hist" in m:
        add("Risikobereitschaft (Willingness)", "Wie viel Verlust vom Höchststand der Kunde aushält, ohne auszusteigen",
            "Maximaler Drawdown", f"≥ −{_d(lmax, 0)} % (tragbarer Verlust)", f"{_d(m.get('mdd_hist'), 1)} %" if m.get("mdd_hist") is not None else "–",
            st_dd, "Das IPS hat kein eigenes Drawdown-Limit – Vergleich mit dem maximal tragbaren Verlust. Historisch über die ganze "
                   "Periode, also strenger als die 3 Monate der Saison.")
    # 2 Risikofähigkeit
    stress = [(n, v) for n, v in (("Bear-Szenario 3 Mt.", m.get("bear_3m")), ("ES 95 % 3 Mt.", -m["es95_3"] if m.get("es95_3") is not None else None),
                                  ("schlechteste 3 Mt. hist.", m.get("stress_hist"))) if v is not None]
    worst = min((v for _, v in stress), default=None)
    st_s = OFFEN if worst is None or lmax is None else (OK if -worst <= lmax * 0.8 else WARN if -worst <= lmax else VERSTOSS)
    add("Risikofähigkeit (Ability)", "Wie viel Verlust der Kunde tragen kann (Vermögen, Einkommen, Verpflichtungen, Horizont)",
        "Stress-Verlust bis Saisonende", f"≥ −{_d(lmax, 0)} %", " · ".join(f"{n} {_d(v, 1, True)} %" for n, v in stress) or "–", st_s,
        "Bear = Szenario «Bear» der Seite Szenarien; ES = Durchschnitt der schlechtesten 5 % der Simulation; historisch = rollend 63 Tage")
    c_lo, c_hi = alloc["baender"]["cash"]
    add("Risikofähigkeit (Ability)", "Genug flüssige Mittel für den Cash-Bedarf (Liquidität im IPS)",
        "Cash-Anteil vs. Mindest-Cash", f"Cash ≥ {_d(c_lo, 1)} %",
        f"Cash {_d(m.get('cash_pct'), 1)} % · Treasuries {_d(m.get('treas_pct'), 1)} % (täglich handelbar)",
        OFFEN if m.get("cash_pct") is None else (OK if m["cash_pct"] >= c_lo - 1e-9 else VERSTOSS),
        "Alle Titel der Simulation sind täglich liquide – massgeblich ist der Mindest-Cash.")
    # 3 Gesamttoleranz
    if vmax is not None and lmax is not None:
        l_vola = Z95 * vmax * math.sqrt(3 / 12)
        massg, quelle = (lmax, "Fähigkeit") if lmax <= l_vola else (l_vola, "Bereitschaft")
        ist = m.get("var95_3")
        quelle_ist = "VaR 95 % 3 Mt. ex ante"
        if ist is None and m.get("vola_hist") is not None:
            ist, quelle_ist = Z95 * m["vola_hist"] * 0.5, "1,645 × Vola hist. × 0,5"
        add("Gesamte Risikotoleranz", "Die TIEFERE von Bereitschaft und Fähigkeit gilt",
            "die engere der beiden Grenzen (95 %-Verlust über 3 Mt.)",
            f"≥ −{_d(massg, 1)} % (aus {quelle}: Fähigkeit −{_d(lmax, 0)} %, Bereitschaft ≈ −{_d(l_vola, 1)} %)",
            f"{_d(-ist, 1)} % ({quelle_ist})" if ist is not None else "–",
            OFFEN if ist is None else (OK if ist <= massg * 0.9 else WARN if ist <= massg else VERSTOSS),
            f"Bereitschaft umgerechnet: 1,645 × Vola-Ziel {_d(vmax, 0)} % × Wurzel(3/12).")
    # 4 Renditeziel
    r_min, r_ziel, r_rel = ips.get("rendite_min"), ips.get("rendite_ziel"), ips.get("rendite_rel")
    e3 = m.get("e3")
    add("Renditeziel (absolut)", "Die erforderliche Rendite muss mit der Risikotoleranz erreichbar sein",
        "erwartete Rendite bis Saisonende vs. erforderliche", f"≥ {_d(r_min, 1)} % (gewünscht {_d(r_ziel, 1)} %)",
        f"{_d(e3, 2, True)} % (Szenarien, gewichtet)" + (f" · Streuung {_d(m.get('s3'), 2)} %" if m.get("s3") is not None else "") if e3 is not None else "–",
        OFFEN if e3 is None else (OK if e3 >= (r_ziel or 0) else WARN if e3 >= (r_min or 0) else VERSTOSS),
        "Roy's Safety-First: (erwartet − erforderlich) / Streuung" + (f" = {_d((e3 - r_min) / m['s3'], 2)}" if e3 is not None and m.get("s3") else ""))
    akt3 = m.get("akt3")
    add("Renditeziel (relativ)", "Mehrrendite gegenüber der Benchmark des Dozenten muss das eingegangene aktive Risiko rechtfertigen",
        "erwartete Mehrrendite vs. Benchmark 50/50", f"≥ {_d(r_rel, 1, True)} Pp.",
        f"{_d(akt3, 2, True)} Pp. (Benchmark erwartet {_d(m.get('e3_b'), 2, True)} %)" if akt3 is not None else "–",
        OFFEN if akt3 is None else (OK if akt3 >= (r_rel or 0) else WARN), "ex ante, 3 Monate")
    # 5 Beschränkungen
    lo_a, hi_a = alloc["baender"]["aktien"]
    lo_t, hi_t = alloc["baender"]["treasuries"]
    st_b = _schlechter(_band(m.get("aktien_pct"), lo_a, hi_a, 1), _band(m.get("treas_pct"), lo_t, hi_t, 1), _band(m.get("cash_pct"), c_lo, c_hi))
    add("Beschränkungen", "Anlageklassen innerhalb der IPS-Bänder (Mandat, Liquidität)", "Gewichte je Anlageklasse",
        f"Aktien {lo_a:g}–{hi_a:g} · Treasuries {lo_t:g}–{hi_t:g} · Cash {c_lo:g}–{c_hi:g} %",
        f"{_d(m.get('aktien_pct'), 1)} / {_d(m.get('treas_pct'), 1)} / {_d(m.get('cash_pct'), 1)} %", st_b, "gelb = weniger als 1 Pp. vom Rand")
    d_lo, d_hi = ips.get("duration_min"), ips.get("duration_max")
    add("Beschränkungen", "Zinsrisiko des Anleiheteils (Zeithorizont)", "Modified Duration Treasuries",
        f"{_d(d_lo, 1)}–{_d(d_hi, 1)} J.", f"{_d(m.get('duration'), 1)} J." if m.get("duration") else "–",
        OFFEN if not m.get("duration") or d_lo is None else _band(m["duration"], d_lo, d_hi, 0.25))
    if "titel_max" in m:
        tm = m.get("titel_max")
        add("Beschränkungen", "Kein Klumpenrisiko in einem Titel", "grösstes Titelgewicht", f"≤ {_d(ips.get('titel_max'), 1)} % des Vermögens",
            f"{tm[0]} {_d(tm[1], 2)} %" if tm else "–", OFFEN if not tm else _grenze_oben(tm[1], ips.get("titel_max"), 0.95))
    if "n_aktien" in m:
        add("Beschränkungen", "Diversifikation", "Anzahl Aktien", f"≥ {_d(ips.get('aktien_anzahl_min'), 0)}", str(m["n_aktien"]),
            OK if m["n_aktien"] >= (ips.get("aktien_anzahl_min") or 0) else VERSTOSS)
    if "nicht_im_index" in m:
        fehler = list(m.get("nicht_im_index") or []) + list(m.get("ausgeschlossen") or [])
        add("Beschränkungen", "Recht/Regeln der Plattform und Ausschlüsse des Kunden", "Titel ausserhalb S&P 500 oder ausgeschlossen",
            "keine", ", ".join(fehler) if fehler else "keine", VERSTOSS if fehler else OK,
            "S&P 500 = Benchmark-Datei der Dozenten-App; Ausschlüsse = IPS «Ausgeschlossene Titel/Sektoren»")
    # 6 SAA & Benchmark
    if m.get("aktien_pct") is not None:
        abw = max(abs(m["aktien_pct"] - alloc["aktien"]), abs(m["treas_pct"] - alloc["anleihen"]), abs(m["cash_pct"] - alloc["cash"]))
        tol = ips.get("rebal_toleranz") or 5
        add("SAA und Benchmark", "Langfristiger Risikoanker – Rebalancing-Regel", "grösste Abweichung Ist − Taktik (A/T/C)",
            f"≤ {_d(tol, 1)} Pp. (Rebalancing-Toleranz)", f"{_d(abw, 2)} Pp. · SAA {_d(ips.get('saa_aktien'), 0)}/{_d(ips.get('saa_anleihen'), 0)}/"
                                                          f"{_d(ips.get('saa_cash'), 0)} · Taktik {_d(alloc['aktien'], 1)}/{_d(alloc['anleihen'], 1)}/{_d(alloc['cash'], 1)}",
            _grenze_oben(abw, tol, 0.6))
    st_te = _schlechter(_grenze_oben(m.get("te_hist"), temax), _grenze_oben(m.get("te_ea"), temax))
    add("SAA und Benchmark", "Wie weit das Portfolio von der Benchmark des Dozenten abweichen darf", "Tracking Error vs. Benchmark 50/50",
        f"≤ {_d(temax, 1)} % p.a.", " · ".join(x for x in (f"hist. {_d(m.get('te_hist'), 2)} %" if m.get("te_hist") is not None else "",
                                                          f"ex ante {_d(m.get('te_ea'), 2)} %" if m.get("te_ea") is not None else "") if x) or "–",
        st_te, "relatives Risikoziel")
    as_lo, as_hi = ips.get("active_share_min"), ips.get("active_share_max")
    if m.get("active_share") is not None:
        add("SAA und Benchmark", "Wie aktiv die Titelauswahl gegenüber dem S&P 500 ist", "Active Share des Aktienteils",
            f"{_d(as_lo, 0)}–{_d(as_hi, 0)} %", f"{_d(m['active_share'], 1)} %"
            + (f" (inkl. offenes Budget als Index {_d(m['active_share_inkl'], 1)} %)" if m.get("active_share_inkl") is not None else ""),
            _band(m["active_share"], as_lo, as_hi, 2), "Titelgewichte aus der Benchmark-Datei (gültig im Benchmark)")
    elif m.get("active_share_sektor") is not None:
        add("SAA und Benchmark", "Wie aktiv die Titelauswahl gegenüber dem S&P 500 ist", "Active Share – nur Sektor-Ebene (Untergrenze)",
            f"{_d(as_lo, 0)}–{_d(as_hi, 0)} %", f"≥ {_d(m['active_share_sektor'], 1)} %",
            WARN if m["active_share_sektor"] > (as_hi or 100) else OK if m["active_share_sektor"] >= (as_lo or 0) else OFFEN,
            "Nur eine Untergrenze – die echte Active Share kommt aus den Einzeltiteln (Analyse → «Benchmark & Active Share», Portfolio).")
    b_lo, b_hi = ips.get("beta_min"), ips.get("beta_max")
    if m.get("beta_aktien") is not None:
        add("SAA und Benchmark", "Marktempfindlichkeit der Aktien gegenüber dem S&P 500", "Beta des Aktienteils",
            f"{_d(b_lo, 2)}–{_d(b_hi, 2)}", _d(m["beta_aktien"], 2), _band(m["beta_aktien"], b_lo, b_hi, 0.03))
    if m.get("sektor_aktiv"):
        s_max, v_max = max(m["sektor_aktiv"].items(), key=lambda x: abs(x[1]))
        g = ips.get("sektor_abw_max")
        add("SAA und Benchmark", "Sektorwetten im Rahmen", "grösste Sektorabweichung vs. Benchmark (gültig)", f"≤ ±{_d(g, 1)} Pp.",
            f"{s_max} {_d(v_max, 2, True)} Pp.", _grenze_oben(abs(v_max), g, 0.9), "Anteil am Aktienteil")
    return z


def kurz(zeilen: list[dict]) -> dict:
    return {s: sum(1 for z in zeilen if z["Status"] == s) for s in (OK, WARN, VERSTOSS, OFFEN)}


def anzeigen(zeilen: list[dict], titel: str = "IPS gegen Portfolio: Risiko-Abgleich (Gap-Analyse)", R=None, erklaerung: str = ""):
    n = kurz(zeilen)
    st.markdown(f"#### {titel}")
    st.markdown("Das IPS sagt, **welches Risiko eingegangen werden darf**; das Portfolio zeigt, **welches Risiko tatsächlich "
                "eingegangen wird**. Jede IPS-Aussage ist hier in eine messbare Kennzahl mit Grenze übersetzt und geprüft."
                + (f" {erklaerung}" if erklaerung else ""))
    ui.kopf_kpis([
        ("Eingehalten", f"<span style='color:{ui.FARBE[L.GRUEN]}'>{n[OK]}</span>", f"von {len(zeilen)} Prüfungen"),
        ("Knapp / prüfen", f"<span style='color:{ui.FARBE[L.GELB]}'>{n[WARN]}</span>", "nahe an der Grenze"),
        ("Verletzt", f"<span style='color:{ui.FARBE[L.ROT] if n[VERSTOSS] else ui.FARBE[L.GRUEN]}'>{n[VERSTOSS]}</span>",
         "Lücke zwischen IPS und Portfolio – handeln oder IPS begründet anpassen"),
        ("Nicht messbar", str(n[OFFEN]), "fehlende Daten (z. B. Kurse, Benchmark-Datei)"),
    ])
    df = pd.DataFrame(zeilen)
    df["Status"] = [f"● {s} · {STATUS_TEXT[s]}" if s != OFFEN else "– nicht messbar" for s in df["Status"]]
    st.dataframe(ui.ampel_stil(df, ["Status"]), hide_index=True, width="stretch",
                 column_config={"Was es fürs Risiko heisst": st.column_config.TextColumn(width="medium"),
                                "Hinweis": st.column_config.TextColumn(width="medium")})
    st.caption("Grün = eingehalten mit Reserve · Gelb = knapp (über 90 % der Grenze bzw. nahe am Bandrand) · Rot = verletzt. "
               "Ex ante = Szenario-Simulation des Dashboards bis Saisonende (Seite Szenarien), historisch = heutige Gewichte mit "
               "vergangenen Tagesrenditen.")
    if R is not None:
        _hell = {L.ROT: (253, 241, 240), L.GELB: (253, 246, 231), L.GRUEN: (237, 247, 241)}
        R.h(f"{titel} · {n[OK]} eingehalten, {n[WARN]} knapp, {n[VERSTOSS]} verletzt")
        R.text("Das IPS sagt, welches Risiko eingegangen werden darf; das Portfolio zeigt, welches tatsächlich eingegangen wird. "
               "Jede IPS-Aussage ist in eine messbare Kennzahl mit Grenze übersetzt (Gap-Analyse).", klein=True)
        R.tabelle(df.assign(Status=[STATUS_TEXT[z["Status"]] for z in zeilen]).drop(columns=["Hinweis"]),
                  breiten=[34, 52, 40, 38, 52, 22], farben=[[None] * 5 + [_hell.get(z["Status"])] for z in zeilen], max_zeilen=40)
    return n
