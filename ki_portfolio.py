"""KI-Portfolioanalyse (Update 10): Wo ist das Portfolio schwach, und worauf müssen wir achten, wenn sich etwas verschiebt?
Verknüpft mit dem Monitoring.

Ablauf
1. Material – ohne KI, alles aus der App gerechnet: Zusammensetzung (aktueller Bestand oder ein gespeichertes Analyseportfolio),
   Kennzahlen aus der Kursgeschichte (analyse.rechne: Rendite, Vola, Beta, Drawdown, Tracking Error vs. neutral, Risikobeiträge,
   Korrelationen), Sektoren Ist / Index / Ziel, Regeln (IPS), Bewertung und Analysten (unternehmen.json, falls gesammelt),
   Makro-Lage (Szenarien, rote Ampeln, Sektor-Trigger), aktuelle Werte der messbaren Grössen und was das Monitoring schon überwacht.
2. Eine KI-Abfrage (Claude Sonnet, gleicher Monatsdeckel wie alle KI-Abfragen) → JSON: Fazit, Schwachpunkte, Beobachtungspunkte
   (je mit einer messbaren Regel aus GROESSEN), Stärken, Fragen ans Team. Die App prüft jede Regel (Grösse, Ticker, Sektor, Zahl).
3. Gespeichert im Blatt «ki_analyse». Das Team wählt Beobachtungspunkte aus (Schwelle und Stufe änderbar) → Blatt «ki_beobachtung».
4. Das Monitoring (App und automatischer Lauf) prüft die aktiven Punkte wie alle anderen Signale → Fall «KI-Beobachtung: …»
   mit der vorgeschlagenen Reaktion als Hinweis, Zuständigkeit nach Sektor wie gewohnt.

Die KI rechnet keine Kennzahlen selbst und legt keine Fälle direkt an – sie schlägt vor, das Team entscheidet.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid

import pandas as pd

from makro import config as C
from storage import jetzt, zahl

MODELL = "claude-sonnet-5-5"
MAX_OUT = 6000
BESTAND = "__bestand__"
STUFEN = ["Info", "Prüfen", "Handeln"]
SCHWERE = ["hoch", "mittel", "tief"]
OPS = ("<", ">", "<=", ">=")

# Messbare Grössen für Beobachtungspunkte: Schlüssel → (Bezeichnung, Einheit, braucht "" | "ticker" | "sektor")
GROESSEN = {
    "brent": ("Brent", "USD/Fass", ""),
    "wti": ("WTI", "USD/Fass", ""),
    "us10j": ("Rendite 10-J.-Treasury", "%", ""),
    "hy": ("Hochzins-Spread", "%", ""),
    "dxy": ("Dollar-Index (DXY)", "", ""),
    "vix": ("VIX", "", ""),
    "spx_dd": ("S&P 500: Abstand zum 52-Wochen-Hoch", "%", ""),
    "datum": ("Termin erreicht", "", ""),
    "titel_kurs": ("Kurs", "USD", "ticker"),
    "titel_20t": ("Rendite über 20 Handelstage", "%", "ticker"),
    "titel_rel20": ("Rendite minus Sektor-ETF über 20 Handelstage", "Pp.", "ticker"),
    "sektor_20t": ("Sektor-ETF-Rendite über 20 Handelstage", "%", "sektor"),
    "sektor_rel20": ("Sektor-ETF minus S&P 500 über 20 Handelstage", "Pp.", "sektor"),
    "sektor_gewicht": ("Ist-Gewicht im Aktienteil (Bestand)", "%", "sektor"),
    "titel_gewicht": ("Gewicht am Gesamtportfolio (Bestand)", "%", "ticker"),
    "aktienquote": ("Aktienquote (Bestand)", "%", ""),
}
MAKRO = ("brent", "wti", "us10j", "hy", "dxy", "vix", "spx_dd", "datum")
SPY = "SPY"

T_ANALYSE = "ki_analyse"
T_PUNKTE = "ki_beobachtung"
PUNKT_SPALTEN = ["zeit", "person", "id", "analyse_id", "bereich", "sektor", "ticker", "titel", "grund", "reaktion", "groesse",
                 "op", "wert", "stufe", "geloescht"]
ANALYSE_SPALTEN = ["zeit", "person", "id", "quelle", "quelle_id", "quelle_name", "modell", "ergebnis", "kennzahlen", "hash"]


def _txt(x) -> str:
    if x is None:
        return ""
    try:
        if pd.isna(x):
            return ""
    except (TypeError, ValueError):
        pass
    return str(x)


def _de(x, d: int = 1, vz: bool = False) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    s = f"{x:{'+' if vz else ''},.{d}f}".replace(",", "'").replace(".", ",")
    return s.replace("-", "−")


# ---------------------------------------------------------------------------
# Beobachtungspunkte: lesen, Werte holen, prüfen (ohne Streamlit – auch im automatischen Lauf)
# ---------------------------------------------------------------------------
def punkte(df: pd.DataFrame | None) -> list[dict]:
    """Aktive Beobachtungspunkte (neuster Stand je id, ohne gelöschte), älteste zuerst."""
    if df is None or df.empty or "id" not in df.columns:
        return []
    d = df.sort_values("zeit").drop_duplicates("id", keep="last")
    d = d[~d["geloescht"].astype(str).str.strip().isin(["1", "1.0", "True", "true"])]
    out = []
    for _, r in d.iterrows():
        p = {k: _txt(r.get(k)) for k in PUNKT_SPALTEN}
        p["ticker"] = p["ticker"].upper().strip()
        out.append(p)
    return sorted(out, key=lambda p: p["zeit"])


def ticker_benoetigt(tabellen: dict) -> set:
    """Kurse, die das Monitoring für die KI-Punkte braucht (Titel, deren Sektor-ETF, S&P 500)."""
    out = set()
    for p in punkte(tabellen.get(T_PUNKTE)):
        if p["groesse"] not in GROESSEN:
            continue
        if p["ticker"]:
            out.add(p["ticker"])
        if p["sektor"] in C.SEKTOR_ETF:
            out.add(C.SEKTOR_ETF[p["sektor"]])
        if p["groesse"] in ("spx_dd", "sektor_rel20"):
            out.add(SPY)
    return out


def _reihe(kurse: dict, t: str) -> pd.Series | None:
    s = (kurse or {}).get(t)
    if s is None:
        return None
    s = s.dropna()
    return s if len(s) else None


def _spx(kurse: dict) -> pd.Series | None:
    s = _reihe(kurse, SPY)
    return s if s is not None else _reihe(kurse, "^GSPC")


def _r20(s: pd.Series | None) -> float | None:
    if s is None or len(s) < 21:
        return None
    return float(s.iloc[-1] / s.iloc[-21] - 1) * 100


def wert(groesse: str, ticker: str, sektor: str, fred: dict, kurse: dict, bestand: tuple | None = None):
    """Aktueller Wert einer Grösse (oder None). bestand = (Bewertung b, Total) aus portfolio.bewerte."""
    import sektoren as SK

    if groesse in ("brent", "wti", "us10j", "hy", "dxy", "vix"):
        _, quelle, reihe, _ = SK.GROESSEN[groesse]
        v, _ = SK._letzter(fred, kurse, quelle, reihe)
        return v
    if groesse == "spx_dd":
        s = _spx(kurse)
        if s is None:
            return None
        j = s[s.index >= s.index[-1] - pd.Timedelta(days=365)]
        return float(s.iloc[-1] / j.max() - 1) * 100
    if groesse == "titel_kurs":
        s = _reihe(kurse, ticker)
        return float(s.iloc[-1]) if s is not None else None
    if groesse == "titel_20t":
        return _r20(_reihe(kurse, ticker))
    if groesse == "titel_rel20":
        a, b = _r20(_reihe(kurse, ticker)), _r20(_reihe(kurse, C.SEKTOR_ETF.get(sektor, "")))
        return a - b if a is not None and b is not None else None
    if groesse == "sektor_20t":
        return _r20(_reihe(kurse, C.SEKTOR_ETF.get(sektor, "")))
    if groesse == "sektor_rel20":
        a, b = _r20(_reihe(kurse, C.SEKTOR_ETF.get(sektor, ""))), _r20(_spx(kurse))
        return a - b if a is not None and b is not None else None
    if groesse in ("sektor_gewicht", "titel_gewicht", "aktienquote"):
        if not bestand or bestand[0] is None or not len(bestand[0]) or not bestand[1]:
            return None
        b, total = bestand
        ak = b[b["klasse"] == "Aktie"]
        ak_w = float(ak["wert"].sum())
        if groesse == "aktienquote":
            return ak_w / total * 100
        if groesse == "titel_gewicht":
            return float(ak.loc[ak["ticker"].astype(str).str.upper() == ticker, "wert"].sum()) / total * 100
        return float(ak.loc[ak["sektor"] == sektor, "wert"].sum()) / ak_w * 100 if ak_w else None
    return None


def groesse_text(p: dict) -> str:
    g = p.get("groesse") or ""
    if g not in GROESSEN:
        return "von Hand prüfen"
    name, einheit, braucht = GROESSEN[g]
    bezug = p.get("ticker") if braucht == "ticker" else p.get("sektor") if braucht == "sektor" else ""
    return f"{bezug + ': ' if bezug else ''}{name}{' (' + einheit + ')' if einheit else ''}"


def regel_text(p: dict) -> str:
    g = p.get("groesse") or ""
    if g not in GROESSEN:
        return "von Hand prüfen"
    name, einheit, _ = GROESSEN[g]
    bezug = p.get("ticker") or (p.get("sektor") if GROESSEN[g][2] == "sektor" else "")
    if g == "datum":
        d = _txt(p.get("wert"))[:10]
        return f"{p.get('ticker') + ': ' if p.get('ticker') else ''}Termin {d[8:10]}.{d[5:7]}.{d[:4]}" if len(d) == 10 else "Termin ?"
    v = zahl(p.get("wert"))
    return f"{bezug + ': ' if bezug else ''}{name} {p.get('op') or '?'} {_de(v, 2) if v is not None else '?'}{' ' + einheit if einheit else ''}"


def status(p: dict, fred: dict, kurse: dict, bestand: tuple | None = None) -> dict:
    """{wert, anzeige, ausgeloest, text} für einen Punkt."""
    g = p.get("groesse") or ""
    if g not in GROESSEN:
        return dict(wert=None, anzeige="–", ausgeloest=False, text="ohne Regel – von Hand prüfen")
    if g == "datum":
        try:
            ziel = pd.Timestamp(_txt(p.get("wert"))[:10])
        except (ValueError, TypeError):
            return dict(wert=None, anzeige="–", ausgeloest=False, text="Datum ungültig")
        heute = pd.Timestamp.today().normalize()
        tage = (ziel - heute).days
        return dict(wert=float(tage), anzeige=f"{heute:%d.%m.%Y}", ausgeloest=tage <= 0,
                    text="Termin erreicht" if tage <= 0 else f"noch {tage} Tage")
    v = wert(g, p.get("ticker", ""), p.get("sektor", ""), fred, kurse, bestand)
    s = zahl(p.get("wert"))
    einheit = GROESSEN[g][1]
    if v is None:
        nur_bestand = g in ("sektor_gewicht", "titel_gewicht", "aktienquote")
        return dict(wert=None, anzeige="–", ausgeloest=False,
                    text="erst mit Bestand (nach dem Kauf)" if nur_bestand else "keine Daten")
    anzeige = f"{_de(v, 2)}{' ' + einheit if einheit else ''}"
    op = p.get("op") or ""
    if s is None or op not in OPS:
        return dict(wert=v, anzeige=anzeige, ausgeloest=False, text="Regel unvollständig")
    aus = v < s if op == "<" else v > s if op == ">" else v <= s if op == "<=" else v >= s
    abstand = abs(v - s)
    nah = not aus and abstand <= max(abs(s) * 0.05, 0.5 if einheit in ("%", "Pp.") else 0)
    return dict(wert=v, anzeige=anzeige, ausgeloest=aus, text="ausgelöst" if aus else ("knapp davor" if nah else "nicht ausgelöst"))


def signale(lage: dict, bekannt: set) -> list[dict]:
    """Monitoring-Signale aus den aktiven Beobachtungspunkten (für monitoring.alle_signale)."""
    import monitoring as M

    out = []
    bestand = (lage.get("b"), lage.get("total")) if lage.get("hat_tx") else None
    for p in punkte(lage["tabellen"].get(T_PUNKTE)):
        g = p["groesse"]
        if g not in GROESSEN:
            continue
        st_ = status(p, lage["fred"], lage["kurse"], bestand)
        if st_["wert"] is None:
            continue
        key = f"ki:{p['id']}"
        if g != "datum":                                     # Puffer gegen Hin und Her (wie die übrigen Signale)
            s = zahl(p["wert"])
            richtung = "unten" if p["op"] in ("<", "<=") else "oben"
            puffer = max(abs(s or 0) * 0.01, 0.25 if GROESSEN[g][1] in ("%", "Pp.") else 0.0)
            if not M._aktiv(st_["wert"], s, richtung, key, bekannt, puffer):
                continue
        elif not st_["ausgeloest"]:
            continue
        else:
            richtung = "oben"
        sek = p["sektor"] if p["sektor"] in C.SEKTOREN else ""
        ebene = "Titel" if p["ticker"] else ("Makro" if g in MAKRO else "Einhaltung")
        bereich = sek or ("Makro" if g in MAKRO else "Allokation")
        stufe = p["stufe"] if p["stufe"] in STUFEN else "Prüfen"
        sig = M._sig(key, ebene, bereich, f"KI-Beobachtung: {p['titel']}",
                     f"{regel_text(p)} – aktuell {st_['anzeige']}. Warum beobachten: {p['grund'] or '–'}", stufe, "ki_punkt",
                     st_["wert"] if g != "datum" else None, GROESSEN[g][1], richtung, ticker=p["ticker"])
        if p["reaktion"]:
            sig["hinweis"] = p["reaktion"]
        out.append(sig)
    return out


# ---------------------------------------------------------------------------
# Material für die KI (in der App)
# ---------------------------------------------------------------------------
def quellen(tabellen: dict) -> tuple[list[str], dict]:
    """Wählbare Portfolios: aktueller Bestand (falls Transaktionen) und gespeicherte Analyseportfolios."""
    import analyse as A
    import portfolio as P

    ids, namen = [], {}
    tx = P.transaktionen(tabellen.get("transaktionen"))
    if len(tx):
        ids.append(BESTAND)
        namen[BESTAND] = "Aktueller Bestand (Seite Portfolio)"
    gs = A.portfolios(tabellen.get("analysen"))
    for _, z in gs.iterrows():
        ids.append(z["id"])
        namen[z["id"]] = f"Analyseportfolio «{z['name']}» · {z['person']}, {A._datum(z['zeit'])}"
    return ids, namen


def quelle_hash(tabellen: dict, qid: str) -> str:
    """Fingerabdruck der Zusammensetzung (ohne Kurse) – zeigt, ob das Portfolio seit der Analyse geändert hat."""
    import analyse as A
    import portfolio as P

    if qid == BESTAND:
        tx = P.transaktionen(tabellen.get("transaktionen"))
        if not len(tx):
            return ""
        pos = P.positionen(tx)[0]
        roh = json.dumps(sorted((str(k), round(float(v), 4)) for k, v in zip(pos["ticker"], pos["menge"])) if len(pos) else [],
                         default=str)
    else:
        gs = A.portfolios(tabellen.get("analysen"))
        if qid not in set(gs["id"]):
            return ""
        try:
            roh = A._hash(json.loads(gs.set_index("id").loc[qid, "eingabe"]))
        except Exception:  # noqa: BLE001
            roh = ""
    return hashlib.sha1(roh.encode()).hexdigest()[:12]


def _portfolio_laden(ctx, qid: str, fred: dict, lade_kurse) -> dict | None:
    import analyse as A

    tab = ctx.tabellen
    if qid == BESTAND:
        v = A.portfolio_vorlage(tab, fred, lade_kurse)
        if v is None:
            return None
        return dict(name="Aktueller Bestand", aktien=v[0], treas=v[1], cash=v[2], kapital=v[3], pct=None, rest=False)
    gs = A.portfolios(tab.get("analysen"))
    if qid not in set(gs["id"]):
        return None
    z = gs.set_index("id").loc[qid]
    try:
        e = json.loads(z["eingabe"])
    except Exception:  # noqa: BLE001
        return None
    a_ = pd.DataFrame(e.get("aktien") or [])
    for c in ("Ticker", "Menge", "Betrag USD"):
        if c not in a_.columns:
            a_[c] = None
    return dict(name=str(z["name"]), aktien=a_, treas=A._treas_df(pd.DataFrame(e.get("treas") or [])), cash=0.0,
                kapital=float(e.get("kapital") or 0), pct=e.get("pct"), rest=True)


def _f(x, d=1, vz=False, einheit=""):
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{_de(x, d, vz)}{einheit}"


def material(ctx, qid: str, fred: dict, lade_kurse) -> dict | None:
    """Alles, was die KI sehen soll, als Text – plus Kontext für die Prüfung der Antwort. None, wenn nichts zu analysieren ist."""
    import analyse as A
    import monitoring as M
    import portfolio as P
    import sektoren as SK
    from makro import logic as L

    q = _portfolio_laden(ctx, qid, fred, lade_kurse)
    if q is None:
        return None
    ticker = sorted({_txt(t).strip().upper().replace(".", "-") for t in q["aktien"]["Ticker"] if _txt(t).strip()})
    pos0 = A.zusammenstellen(q["aktien"].iloc[0:0], q["treas"], 0, 0, False, {})[0]
    proxies = sorted({A.PROXY[_txt(b)][0] for b in pos0["band"] if _txt(b) in A.PROXY})
    etfs = sorted(set(C.SEKTOR_ETF.values()))
    hist, fehlt, zeit = A.lade_historie(tuple(sorted(set(ticker + proxies + etfs + [A.SPX, A.NEUTRAL_PROXY, A.RF_TICKER]))), 1)
    if A.SPX not in hist:
        raise RuntimeError("Kursdaten von Yahoo Finance sind gerade nicht erreichbar – bitte später nochmals versuchen.")
    letzte = {t: float(s.iloc[-1]) for t, s in hist.items()}
    pos, cash, total, hinw = A.zusammenstellen(q["aktien"], q["treas"], q["cash"], q["kapital"], q["rest"], letzte)
    if total <= 0 or not len(pos):
        return None
    fuell = A.offenes_budget(pos, cash, q["kapital"], q["pct"]) if q["pct"] else {}
    fuell = {t: w for t, w in fuell.items() if t in hist}
    erg = A.rechne(pos, cash, total, hist, ctx.alloc["neutral"], fuell)
    info = {t: P.ticker_info(t) for t in pos.loc[pos["klasse"] == "Aktie", "ticker"]}
    sektor_von = {t: (i.get("sektor") or "Unbekannt") for t, i in info.items()}
    try:
        import sektor_news_seite as SNS

        U = SNS.lade("unternehmen.json")[0] or {}
    except Exception:  # noqa: BLE001
        U = {}

    ak = pos[pos["klasse"] == "Aktie"].copy()
    ak_w = float(ak["wert"].sum())
    tr = pos[pos["klasse"] == "Treasury"]
    tr_w = float(tr["wert"].sum())
    f_a, f_t = (erg or {}).get("fuell", {}).get(A.SPX, 0.0), (erg or {}).get("fuell", {}).get(A.NEUTRAL_PROXY, 0.0)
    ips = ctx.ips
    teile = []

    # A) Überblick
    gew = ak.groupby("ticker")["wert"].sum().sort_values(ascending=False) if len(ak) else pd.Series(dtype=float)
    top10 = float(gew.head(10).sum()) / total * 100 if len(gew) else 0.0
    start = pd.Timestamp(C.SIMULATION_START)
    teile.append(
        f"Heute: {pd.Timestamp.today():%d.%m.%Y} · Simulationsstart (Kauf): {start:%d.%m.%Y}\n"
        f"## Portfolio: {q['name']} – "
        + ("gehaltener Bestand" if qid == BESTAND else "geplantes Analyseportfolio (noch nicht gekauft)") + "\n"
        f"Kapital {_de(q['kapital'], 0)} USD · bewertet {_de(total, 0)} USD · Kurse vom {zeit}\n"
        f"Aufteilung investiert: Aktien {_de(ak_w / total * 100, 1)} % · Treasuries {_de(tr_w / total * 100, 1)} % · "
        f"Cash {_de(cash / total * 100, 1)} %"
        + (f" (davon noch nicht verteilt und als Index gerechnet: Aktien {_de(f_a / total * 100, 1)} %, Treasuries "
           f"{_de(f_t / total * 100, 1)} %)" if fuell else "") + "\n"
        f"Taktik: Aktien {_de(ctx.alloc['aktien'], 1)} % / Treasuries {_de(ctx.alloc['anleihen'], 1)} % / Cash "
        f"{_de(ctx.alloc['cash'], 1)} %, Duration {_de(ctx.alloc['duration'], 1)} J. · Benchmark 50 % S&P 500 / 50 % Treasuries\n"
        f"Aktien: {len(gew)} Titel, effektive Anzahl {_de(A.eff_anzahl(ak['wert'].astype(float)), 1) if len(ak) else '–'}, "
        f"grösster Titel {gew.index[0] if len(gew) else '–'} {_de(float(gew.iloc[0]) / total * 100 if len(gew) else None, 2)} % des Portfolios, "
        f"Top 10 zusammen {_de(top10, 1)} %")
    if hinw:
        teile.append("Hinweise beim Zusammenstellen: " + " · ".join(hinw))

    # B) IPS
    try:
        import ips as IPS

        z_ = [f"{f['titel']}: {_de(ips[k], 1)} {f.get('einheit', '')}".strip() for k, f in IPS.FELDER.items()
              if f["typ"] == "zahl" and isinstance(ips.get(k), (int, float))]
        teile.append("## IPS (Grenzen und Ziele)\n" + "\n".join(f"- {x}" for x in z_))
    except Exception:  # noqa: BLE001
        pass

    # C) Kennzahlen
    if erg:
        k = erg["k"]
        sp = [("Portfolio", k.get("Vorauswahl", {})), ("Nur Aktienteil", k.get("Aktienteil", {})),
              ("Neutral (IPS)", k.get("Neutral (IPS)", {})), ("S&P 500", k.get("S&P 500", {}))]
        zeilen = ["| Kennzahl (1 Jahr, Tagesdaten) | " + " | ".join(n for n, _ in sp) + " |", "|---|" + "---|" * len(sp)]
        for key, name, d in (("rendite", "Rendite p.a. %", 1), ("vola", "Volatilität p.a. %", 1), ("sharpe", "Sharpe", 2),
                             ("mdd", "Max. Drawdown %", 1), ("beta", "Beta zum S&P 500", 2), ("var95", "VaR 95 % 1 Tag %", 2),
                             ("cvar95", "CVaR 95 % 1 Tag %", 2)):
            zeilen.append(f"| {name} | " + " | ".join(_f(x.get(key), d) for _, x in sp) + " |")
        kv = k.get("Vorauswahl", {})
        zeilen.append(f"Tracking Error vs. neutral {_f(kv.get('te_neutral'), 2)} % p.a., Information Ratio {_f(kv.get('ir_neutral'), 2)} · "
                      f"Zeitfenster {kv.get('von'):%d.%m.%Y} – {kv.get('bis'):%d.%m.%Y}" if kv.get("von") is not None else "")
        teile.append("## Kennzahlen aus der Kursgeschichte\n" + "\n".join(zeilen))

    # D) Sektoren
    sa = SK.lade_allokation(ctx.tabellen.get("sektor_allokation"))["sektoren"]
    ak_df = pd.DataFrame({"ticker": ak["ticker"].tolist(), "name": [info.get(t, {}).get("name", "") for t in ak["ticker"]],
                          "sektor": [sektor_von.get(t) for t in ak["ticker"]], "wert": ak["wert"].astype(float).tolist()})
    sek_p = SK.pruefe_portfolio(ak_df, total, sa, ctx.sek_index["gewichte"], float(ctx.alloc["aktien"]))
    beitr_sek = {}
    if erg is not None and len(erg["beitrag"]):
        for t, v in erg["beitrag"].items():
            beitr_sek[sektor_von.get(t, "Treasuries/Platzhalter")] = beitr_sek.get(sektor_von.get(t, "Treasuries/Platzhalter"), 0) + float(v)
    etf_r = {s: _r20(hist.get(C.SEKTOR_ETF[s])) for s in C.SEKTOREN}
    spy_r = _r20(hist.get(A.SPX))
    zeilen = ["| Sektor | Richtung | Index % | Ziel % | Ist % Aktienteil | Ist − Ziel Pp. | Titel | Risikobeitrag % (Portfolio) | "
              "Sektor-ETF 20 T. % | ETF − S&P 20 T. Pp. |", "|---|---|---|---|---|---|---|---|---|---|"]
    for _, z in sek_p["sektoren"].iterrows():
        s = z["Sektor"]
        zeilen.append(f"| {s} | {z['Richtung']} | {_f(z['_index'], 2)} | {_f(z['_ziel'], 2)} | {_f(z['_ist'], 2)} | "
                      f"{_f(z['_diff'], 2, True)} | {z['Anzahl Titel']} | {_f(beitr_sek.get(s), 1)} | {_f(etf_r.get(s), 1, True)} | "
                      f"{_f(etf_r[s] - spy_r if etf_r.get(s) is not None and spy_r is not None else None, 1, True)} |")
    teile.append("## Sektoren (Ziel = Index + geplante Abweichung aus der Sektorallokation)\n" + "\n".join(zeilen))

    # E) Titel
    def _u(t, feld):
        return ((U.get(t) or {}).get("info") or {}).get(feld)

    zeilen = ["| Ticker | Name | Sektor | Gew. Portfolio % | Gew. Aktienteil % | Kurs USD | 20 T. % | Rendite 1 J. % | Vola % | Beta | "
              "Risikobeitrag % | Max. DD % | KGV fwd | Kursziel Analysten (Abstand %) | Empfehlung | nächste Zahlen |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for t, w in gew.items():
        ke = (erg or {}).get("einzel", {}).get(t, {})
        kurs = letzte.get(t)
        ziel_k = _u(t, "targetMeanPrice")
        abst = (ziel_k / kurs - 1) * 100 if ziel_k and kurs else None
        termin = _u(t, "earningsTimestampStart") or _u(t, "earningsTimestamp")
        try:
            termin = pd.Timestamp(int(termin), unit="s").strftime("%d.%m.%Y") if termin else ""
        except (ValueError, TypeError, OverflowError):
            termin = ""
        zeilen.append(f"| {t} | {info.get(t, {}).get('name', '')[:28]} | {sektor_von.get(t)} | {_f(w / total * 100, 2)} | "
                      f"{_f(w / ak_w * 100 if ak_w else None, 2)} | {_f(kurs, 2)} | {_f(_r20(hist.get(t)), 1, True)} | "
                      f"{_f(ke.get('rendite'), 1, True)} | {_f(ke.get('vola'), 1)} | {_f(ke.get('beta'), 2)} | "
                      f"{_f((erg or {}).get('beitrag', pd.Series(dtype=float)).get(t), 2)} | {_f(ke.get('mdd'), 1)} | "
                      f"{_f(_u(t, 'forwardPE'), 1)} | {_f(ziel_k, 0)} ({_f(abst, 1, True)}) | {_txt(_u(t, 'recommendationKey'))} | {termin} |")
    teile.append("## Aktien\n" + "\n".join(zeilen)
                 + ("\n(Bewertung/Analysten aus der Sammlung der Seite «Unternehmen»; leer = nicht gesammelt.)" if U else
                    "\n(Keine Bewertungs- und Analystendaten gesammelt.)"))

    # F) Konzentration & Korrelation
    if erg is not None:
        R = erg["R"]
        a_t = [t for t in gew.index if t in R.columns]
        z_ = []
        if len(a_t) >= 3:
            kor = R[a_t].corr()
            import numpy as np

            oben = kor.where(np.triu(np.ones(kor.shape, dtype=bool), 1)).stack()
            z_.append(f"Mittlere paarweise Korrelation der Aktien {_de(float(oben.mean()), 2)}")
            z_.append("Am stärksten verbunden: " + ", ".join(f"{a}/{b} {_de(v, 2)}" for (a, b), v in oben.sort_values(ascending=False).head(8).items()))
        b_ = erg["beitrag"].sort_values(ascending=False)
        if len(b_):
            z_.append("Grösste Risikobeiträge (% des Portfolio-Risikos, Gewicht in Klammern): " + ", ".join(
                f"{A.PROXY_NAME.get(t, t)} {_de(float(v), 1)} ({_de(erg['gew'].get(t, 0) * 100, 1)})" for t, v in b_.head(10).items()))
        teile.append("## Konzentration und Korrelation\n" + "\n".join(f"- {x}" for x in z_))

    # G) Treasuries
    if len(tr):
        dur = float(sum(A.PROXY[b][1] * w for b, w in zip(tr["band"], tr["wert"])) / tr_w)
        teile.append("## Treasuries\n" + ", ".join(f"{b}: {_de(w, 0)} USD" for b, w in tr.groupby("band")["wert"].sum().items())
                     + f" · Duration ≈ {_de(dur, 1)} J. (ETF-Näherung)")

    # H) Regeln
    checks = A.pruefungen(pos, cash, total, info, ctx.alloc)
    meld = [t for s_, t in checks if s_ != L.GRUEN] + [t for _, t in sek_p["meldungen"]]
    teile.append("## Regel-Prüfung der App (Verstösse und Hinweise)\n" + ("\n".join(f"- {x}" for x in meld) if meld else "- keine"))

    # I) Makro-Lage
    z_ = []
    try:
        sz = ctx.szen[ctx.szen["thema"] == "gesamt"]
        z_.append("Szenarien (Wahrscheinlichkeit Team/IC): " + ", ".join(f"{r['name']} {_de(float(r['p']), 0)} %" for _, r in sz.iterrows()))
    except Exception:  # noqa: BLE001
        pass
    try:
        rot = ctx.status[ctx.status["ampel"] == L.ROT]
        z_.append("Rote Ampeln: " + (", ".join(f"{r['name']} {r['anzeige']}" for _, r in rot.iterrows()) or "keine"))
    except Exception:  # noqa: BLE001
        pass
    try:
        tl = SK.trigger_liste(sa, fred, ctx.kurse)
        z_.append("Sektor-Trigger: " + "; ".join(f"{t['sektor']}: {t['regel']} (aktuell {t['wert']}, {t['text']})" for t in tl
                                                  if t["regel"] not in ("–", "")))
    except Exception:  # noqa: BLE001
        pass
    teile.append("## Makro-Lage (aus dem Dashboard)\n" + "\n".join(f"- {x}" for x in z_))

    # J) Messbare Grössen mit aktuellen Werten
    kurse_m = {**(ctx.kurse or {}), **hist}
    akt = {g: wert(g, "", "", fred, kurse_m) for g in ("brent", "wti", "us10j", "hy", "dxy", "vix", "spx_dd")}
    z_ = [f"- `{g}` – {GROESSEN[g][0]} ({GROESSEN[g][1] or 'Punkte'}): aktuell {_f(akt[g], 2)}" for g in akt]
    z_.append("- `datum` – Termin (Wert = Datum JJJJ-MM-TT, z. B. Quartalszahlen oder Fed-Sitzung)")
    for g in ("titel_kurs", "titel_20t", "titel_rel20", "titel_gewicht"):
        z_.append(f"- `{g}` – {GROESSEN[g][0]} ({GROESSEN[g][1]}), braucht «ticker» (nur Titel aus der Liste oben); aktuelle Werte "
                  "in der Aktientabelle" + (" (Gewicht: wirkt erst mit Bestand)" if g == "titel_gewicht" else ""))
    for g in ("sektor_20t", "sektor_rel20", "sektor_gewicht"):
        z_.append(f"- `{g}` – {GROESSEN[g][0]} ({GROESSEN[g][1]}), braucht «sektor» (einer von: {', '.join(C.SEKTOREN)}); aktuelle "
                  "Werte in der Sektortabelle" + (" (wirkt erst mit Bestand)" if g == "sektor_gewicht" else ""))
    z_.append("- `aktienquote` – Aktienquote des Bestands (%) (wirkt erst mit Bestand)")
    teile.append("## Messbare Grössen für Beobachtungspunkte (nur diese Schlüssel verwenden)\n" + "\n".join(z_))

    # K) Was das Monitoring schon überwacht
    s = M.schwellen(ctx.tabellen.get("einstellungen"))
    z_ = [f"{M.SCHWELLEN[k][0]} {_de(v, 1)} {M.SCHWELLEN[k][2]}" for k, v in s.items() if k in M.SCHWELLEN
          and M.SCHWELLEN[k][3] in ("Titel", "Einhaltung")]
    faelle = M.lade_faelle(ctx.tabellen.get("faelle"))
    offen = faelle[faelle["status"].isin(M.OFFEN)]["titel"].astype(str).tolist()[:20] if len(faelle) else []
    aktiv = [f"{p['titel']} ({regel_text(p)})" for p in punkte(ctx.tabellen.get(T_PUNKTE))]
    teile.append("## Bereits im Monitoring (nicht doppelt vorschlagen)\n"
                 "- Automatische Regeln für jeden gehaltenen Titel und die Einhaltung: " + "; ".join(z_) + "\n"
                 "- Sektor-Trigger (siehe Makro-Lage), rote Ampeln, Kombi- und Risiko-Warnsignale, IPS-Bänder, Titel-Maximum, Sektor Ist vs. Ziel\n"
                 + (f"- Offene Fälle: {'; '.join(offen)}\n" if offen else "")
                 + (f"- Aktive KI-Beobachtungspunkte: {'; '.join(aktiv)}" if aktiv else ""))

    kv = (erg or {}).get("k", {}).get("Vorauswahl", {})
    ka = (erg or {}).get("k", {}).get("Aktienteil", {})
    kennz = dict(total=round(total, 0), titel=len(gew), aktien=round(ak_w / total * 100, 1), treas=round(tr_w / total * 100, 1),
                 cash=round(cash / total * 100, 1), vola=kv.get("vola"), beta=kv.get("beta"), te=kv.get("te_neutral"),
                 mdd=kv.get("mdd"), vola_aktien=ka.get("vola"), beta_aktien=ka.get("beta"), kurse=zeit)
    return dict(text="\n\n".join(teile), ticker=set(gew.index), sektor_von=sektor_von, name=q["name"], kennzahlen=kennz,
                hist=hist, fehlt=fehlt)


AUFTRAG = """Du bist Risiko- und Portfoliomanager im Investment Committee unseres Teams. Analysiere das Portfolio unten kritisch.

Ziele:
1. Die wichtigsten Schwachpunkte – konkret, mit Zahlen aus dem Material belegt (Konzentration, Faktor- und Zinsrisiken,
   Sektorwetten gegen die Makro-Lage, korrelierte Klumpen, Bewertung, Abhängigkeit von wenigen Titeln, Treasuries/Duration,
   Abweichungen von IPS, Taktik oder Sektorzielen, Datenlücken).
2. Worauf wir besonders achten müssen, wenn sich etwas verschiebt: Ergebnisse (Quartalszahlen, Makrodaten), Kennzahlen, Kurse,
   Spreads, Zinsen – als Beobachtungspunkte mit einer messbaren Regel, die unser Monitoring automatisch prüfen kann.

Regeln für Beobachtungspunkte:
- «groesse» nur aus der Liste «Messbare Grössen»; «ticker» nur Titel aus der Aktientabelle; «sektor» nur die genannten Sektornamen.
- Schwelle («wert») realistisch zum aktuellen Wert setzen (aktuelle Werte stehen im Material): Sie soll eine Verschiebung anzeigen,
  die für unser Portfolio wirklich zählt – nicht schon heute ausgelöst sein (ausser du begründest es). Bei «datum» ist «wert» ein
  Datum JJJJ-MM-TT, «op» dann ">=".
- Keine Doppelungen mit dem, was das Monitoring schon prüft (siehe letzter Abschnitt) – ergänze, wo es Lücken gibt.
- Wenn sich etwas nicht messbar machen lässt, «groesse» leer lassen (dann prüft das Team von Hand).
- «stufe»: Info, Prüfen oder Handeln. «reaktion»: was wir dann konkret tun bzw. prüfen.
- 6 bis 12 Beobachtungspunkte, wichtigste zuerst. 4 bis 8 Schwachpunkte, wichtigste zuerst.

Antworte NUR mit JSON in diesem Format (keine Erklärung davor oder danach):
{"fazit": "3–5 Sätze",
 "schwachpunkte": [{"titel": "...", "schwere": "hoch|mittel|tief", "beschreibung": "...", "belege": "Zahlen aus dem Material",
                    "betroffen": ["Ticker oder Sektor"], "massnahme": "Vorschlag"}],
 "beobachten": [{"titel": "kurz", "warum": "...", "groesse": "Schlüssel oder leer", "ticker": "", "sektor": "", "op": "<|>|<=|>=",
                 "wert": 0, "stufe": "Prüfen", "reaktion": "..."}],
 "staerken": ["..."],
 "fragen_ans_team": ["..."]}
"""


def prompt(mat: dict) -> str:
    return AUFTRAG + "\n\n# Material\n\n" + mat["text"]


def kosten_schaetzung(text: str, modell: str = MODELL) -> float:
    import ki

    return ki.kosten(modell, len(text) // 3 + 600, 4500)


def pruefe_antwort(j, mat: dict) -> dict:
    """KI-Antwort säubern: nur bekannte Felder, Regeln gegen die Liste der Grössen, Ticker und Sektoren prüfen."""
    if not isinstance(j, dict):
        raise ValueError("Die KI hat kein gültiges JSON geliefert.")

    def s(x, n=600):
        return _txt(x).strip()[:n]

    sp = []
    for x in j.get("schwachpunkte") or []:
        if not isinstance(x, dict) or not s(x.get("titel")):
            continue
        sw = s(x.get("schwere")).lower()
        sp.append(dict(titel=s(x.get("titel"), 120), schwere=sw if sw in SCHWERE else "mittel", beschreibung=s(x.get("beschreibung"), 900),
                       belege=s(x.get("belege"), 600), betroffen=[s(b, 40) for b in (x.get("betroffen") or []) if s(b)][:12],
                       massnahme=s(x.get("massnahme"), 500)))
    bo = []
    for x in j.get("beobachten") or []:
        if not isinstance(x, dict) or not s(x.get("titel")):
            continue
        g = s(x.get("groesse")).lower()
        t = s(x.get("ticker")).upper().replace(".", "-")
        sek = s(x.get("sektor"))
        op = s(x.get("op"))
        w = x.get("wert")
        notiz = ""
        if g and g not in GROESSEN:
            notiz, g = f"unbekannte Grösse «{g}» – von Hand prüfen", ""
        if g:
            braucht = GROESSEN[g][2]
            if braucht == "ticker":
                if t not in mat["ticker"]:
                    notiz, g = f"Ticker «{t}» nicht im Portfolio – von Hand prüfen", ""
                else:
                    sek = mat["sektor_von"].get(t, sek)
            elif braucht == "sektor" and sek not in C.SEKTOREN:
                notiz, g = f"Sektor «{sek}» unbekannt – von Hand prüfen", ""
            elif braucht == "":
                t = t if t in mat["ticker"] else ""
        if g == "datum":
            try:
                w = pd.Timestamp(s(w)[:10]).date().isoformat()
                op = ">="
            except (ValueError, TypeError):
                notiz, g, w = "Datum ungültig – von Hand prüfen", "", None
        elif g:
            w = zahl(w)
            if w is None or op not in OPS:
                notiz, g = "Regel unvollständig – von Hand prüfen", ""
        if t and not sek:
            sek = mat["sektor_von"].get(t, "")
        st_ = s(x.get("stufe")).capitalize()
        st_ = "Prüfen" if st_ in ("Pruefen", "Prufen") else st_
        bo.append(dict(titel=s(x.get("titel"), 100), warum=s(x.get("warum"), 600), groesse=g, ticker=t if t in mat["ticker"] else "",
                       sektor=sek if sek in C.SEKTOREN else "", op=op if g else "", wert=w if g else None,
                       stufe=st_ if st_ in STUFEN else "Prüfen", reaktion=s(x.get("reaktion"), 500), notiz=notiz))
    return dict(fazit=s(j.get("fazit"), 1500), schwachpunkte=sp[:10], beobachten=bo[:15],
                staerken=[s(x, 300) for x in (j.get("staerken") or []) if s(x)][:8],
                fragen=[s(x, 300) for x in (j.get("fragen_ans_team") or []) if s(x)][:8])


def analysen(df: pd.DataFrame | None, qid: str | None = None) -> list[dict]:
    """Gespeicherte KI-Analysen (neuste zuerst), optional nur für ein Portfolio."""
    if df is None or df.empty:
        return []
    d = df.sort_values("zeit", ascending=False)
    out = []
    for _, r in d.iterrows():
        if qid and _txt(r["quelle_id"]) != qid:
            continue
        try:
            e = json.loads(r["ergebnis"])
        except Exception:  # noqa: BLE001
            continue
        try:
            k = json.loads(r["kennzahlen"]) if _txt(r["kennzahlen"]) else {}
        except Exception:  # noqa: BLE001
            k = {}
        out.append(dict(zeit=_txt(r["zeit"]), person=_txt(r["person"]), id=_txt(r["id"]), quelle_id=_txt(r["quelle_id"]),
                        quelle_name=_txt(r["quelle_name"]), modell=_txt(r["modell"]), ergebnis=e, kennzahlen=k, hash=_txt(r["hash"])))
    return out


# ---------------------------------------------------------------------------
# Darstellung (Streamlit)
# ---------------------------------------------------------------------------
SCHWERE_FARBE = {"hoch": "#B93A32", "mittel": "#B7791F", "tief": "#6B7685"}


def _api_key() -> str | None:
    import streamlit as st

    try:
        return str(st.secrets.get("anthropic_api_key") or "").strip() or None
    except Exception:  # noqa: BLE001
        return None


def _modell() -> str:
    import streamlit as st

    try:
        return str(st.secrets.get("anthropic_model_analyse") or MODELL)
    except Exception:  # noqa: BLE001
        return MODELL


def _datum(z) -> str:
    s = _txt(z)
    return f"{s[8:10]}.{s[5:7]}.{s[:4]} {s[11:16]}" if len(s) >= 16 else s


def _bestand_bewertung(tabellen: dict, fred: dict, kurse: dict):
    import portfolio as P

    tx = P.transaktionen(tabellen.get("transaktionen"))
    if not len(tx):
        return None
    kapital, _ = P._einstellungen(tabellen.get("einstellungen"))
    pos, cash_delta, _ = P.positionen(tx)
    b = P.bewerte(pos, kurse, fred, pd.Timestamp.today().normalize())
    import cashzins as CZ

    total = ((float(b["wert"].sum()) if len(b) else 0.0) + kapital + cash_delta + (float(b["zins"].sum()) if len(b) else 0.0)
             + CZ.zins_bis(tx, kapital, fred))
    return (b, total)


def _kurse_fuer(punkte_: list[dict], lade_kurse, extra: dict | None = None, tabellen: dict | None = None) -> dict:
    need = set()
    if tabellen is not None:                       # Bestand (für Gewichts-Regeln)
        import portfolio as P

        tx = P.transaktionen(tabellen.get("transaktionen"))
        if len(tx):
            need |= set(tx.loc[tx["klasse"] == "Aktie", "ticker"].str.upper())
    for p in punkte_:
        if p.get("ticker"):
            need.add(p["ticker"])
        if p.get("sektor") in C.SEKTOR_ETF:
            need.add(C.SEKTOR_ETF[p["sektor"]])
    need.add(SPY)
    need -= set(extra or {})
    k = dict(extra or {})
    if need and lade_kurse is not None:
        try:
            k.update(lade_kurse(tuple(sorted(need)))[0] or {})
        except Exception:  # noqa: BLE001
            pass
    return k


def punkte_tabelle(ctx, fred: dict, kurse: dict, bestand, key: str):
    """Aktive Beobachtungspunkte mit aktuellem Wert und Status; entfernen per Häkchen."""
    import streamlit as st

    ps = punkte(ctx.tabellen.get(T_PUNKTE))
    if not ps:
        st.caption("Noch keine KI-Beobachtungspunkte im Monitoring. Auf der Seite «Portfolio» → Reiter «KI-Analyse» eine Analyse "
                   "starten und Punkte übernehmen.")
        return
    zeilen = []
    for p in ps:
        s_ = status(p, fred, kurse, bestand)
        zeilen.append({"Entfernen": False, "Stufe": p["stufe"], "Punkt": p["titel"], "Regel": regel_text(p), "Aktuell": s_["anzeige"],
                       "Status": ("● " if s_["ausgeloest"] else "") + s_["text"], "Bereich": p["sektor"] or p["bereich"],
                       "Warum": p["grund"], "Reaktion": p["reaktion"], "Seit": _datum(p["zeit"])[:10], "Von": p["person"]})
    df = pd.DataFrame(zeilen)
    ed = st.data_editor(df, hide_index=True, width="stretch", key=key, disabled=[c for c in df.columns if c != "Entfernen"],
                        column_config={"Entfernen": st.column_config.CheckboxColumn("✕", width="small"),
                                       "Warum": st.column_config.TextColumn(width="large"),
                                       "Reaktion": st.column_config.TextColumn(width="large")})
    n_aus = sum(1 for z in zeilen if z["Status"].startswith("●"))
    st.caption(f"{len(ps)} aktive Punkte · {n_aus} ausgelöst (werden beim nächsten Monitoring-Lauf zu Fällen «KI-Beobachtung: …») · "
               "«erst mit Bestand» = Gewichts-Regeln wirken ab dem Kauf.")
    weg = [ps[i] for i, v in enumerate(ed["Entfernen"]) if v]
    if weg and st.button(f"{len(weg)} Punkt(e) aus dem Monitoring entfernen", key=key + "_weg", icon=":material/delete:",
                         disabled=ctx.nur_lesen):
        z = jetzt()
        ctx.speichern(T_PUNKTE, [{**p, "zeit": z, "person": ctx.person, "geloescht": "1"} for p in weg],
                      f"{len(weg)} KI-Beobachtungspunkt(e) entfernt.")


def monitoring_tab(ctx, lage: dict, lade_kurse=None):
    """Reiter im Monitoring: aktive KI-Beobachtungspunkte mit aktuellem Stand."""
    import streamlit as st

    st.markdown("**KI-Beobachtungspunkte** · aus der KI-Analyse des Portfolios übernommen, werden bei jedem Lauf geprüft")
    ps = punkte(ctx.tabellen.get(T_PUNKTE))
    kurse = _kurse_fuer(ps, lade_kurse, lage.get("kurse"))
    bestand = (lage.get("b"), lage.get("total")) if lage.get("hat_tx") else None
    punkte_tabelle(ctx, lage.get("fred") or ctx.fred, kurse, bestand, key="mon_ki_pkt")


def _schwachpunkte(sp: list[dict]):
    import streamlit as st

    for x in sp:
        farbe = SCHWERE_FARBE.get(x["schwere"], "#6B7685")
        betr = (" · betrifft " + ", ".join(x["betroffen"])) if x["betroffen"] else ""
        st.markdown(
            f'<div style="border-left:4px solid {farbe};padding:6px 12px;margin:6px 0;background:#FAFBFC">'
            f'<span style="color:{farbe};font-weight:600;font-size:0.8em;text-transform:uppercase">{x["schwere"]}</span>'
            f'<span style="color:#6B7685;font-size:0.8em">{_esc(betr)}</span><br>'
            f'<b>{_esc(x["titel"])}</b><br>{_esc(x["beschreibung"])}'
            + (f'<br><span style="color:#4A5568;font-size:0.9em">Belege: {_esc(x["belege"])}</span>' if x["belege"] else "")
            + (f'<br><span style="font-size:0.9em">→ {_esc(x["massnahme"])}</span>' if x["massnahme"] else "")
            + "</div>", unsafe_allow_html=True)


def _esc(t: str) -> str:
    import html

    return html.escape(_txt(t))


def _beobachten(ctx, a: dict, fred: dict, kurse: dict, bestand):
    """Vorschläge der Analyse: Schwelle und Stufe anpassen, auswählen, ins Monitoring übernehmen."""
    import streamlit as st

    bo = a["ergebnis"].get("beobachten") or []
    if not bo:
        st.caption("Keine Beobachtungspunkte vorgeschlagen.")
        return
    schon = {(p["analyse_id"], p["titel"]) for p in punkte(ctx.tabellen.get(T_PUNKTE))}
    zeilen = []
    for x in bo:
        p = dict(groesse=x["groesse"], ticker=x["ticker"], sektor=x["sektor"], op=x["op"], wert=x["wert"])
        s_ = status(p, fred, kurse, bestand)
        drin = (a["id"], x["titel"]) in schon
        zeilen.append({"Übernehmen": False if drin else bool(x["groesse"]), "Stufe": x["stufe"], "Punkt": x["titel"],
                       "Grösse": groesse_text(p),
                       "Op": x["op"] or "", "Schwelle": (x["wert"] if x["groesse"] != "datum" else None) if x["groesse"] else None,
                       "Datum": _txt(x["wert"])[:10] if x["groesse"] == "datum" else "",
                       "Aktuell": s_["anzeige"], "Status": ("bereits im Monitoring · " if drin else "") + s_["text"]
                       + (f" · {x['notiz']}" if x.get("notiz") else ""),
                       "Warum": x["warum"], "Reaktion": x["reaktion"]})
    df = pd.DataFrame(zeilen)
    ed = st.data_editor(
        df, hide_index=True, width="stretch", key=f"kp_bo_{a['id']}",
        disabled=["Punkt", "Grösse", "Aktuell", "Status", "Warum"],
        column_config={"Übernehmen": st.column_config.CheckboxColumn("✓", width="small", help="ins Monitoring übernehmen"),
                       "Stufe": st.column_config.SelectboxColumn(options=STUFEN, width="small"),
                       "Op": st.column_config.SelectboxColumn(options=["", *OPS], width="small"),
                       "Schwelle": st.column_config.NumberColumn(format="%.2f", help="Schwelle anpassen, bevor ihr übernehmt"),
                       "Datum": st.column_config.TextColumn(help="nur bei Terminen: JJJJ-MM-TT"),
                       "Warum": st.column_config.TextColumn(width="large"),
                       "Reaktion": st.column_config.TextColumn(width="large", help="wird im Monitoring-Fall als Hinweis angezeigt")})
    wahl = [i for i, v in enumerate(ed["Übernehmen"]) if v]
    c1, c2 = st.columns([1.4, 3])
    los = c1.button(f"{len(wahl)} Punkt(e) ins Monitoring übernehmen", type="primary", icon=":material/notifications_active:",
                    disabled=ctx.nur_lesen or not wahl, key=f"kp_los_{a['id']}", width="stretch")
    c2.caption("Nur Punkte mit Regel werden automatisch geprüft; Punkte «von Hand prüfen» erscheinen als Liste im Monitoring. "
               "Ausgelöste Punkte werden zu Fällen mit Zuständigkeit nach Sektor (sonst ganzes Team).")
    if los:
        z, neu, fehler = jetzt(), [], []
        for i in wahl:
            x, r = bo[i], ed.iloc[i]
            g = x["groesse"]
            op = _txt(r["Op"]) or x["op"]
            if g == "datum":
                w = _txt(r["Datum"]).strip()
                try:
                    w = pd.Timestamp(w).date().isoformat()
                except (ValueError, TypeError):
                    fehler.append(f"{x['titel']}: Datum ungültig")
                    continue
            else:
                w = zahl(r["Schwelle"]) if g else None
                if g and (w is None or op not in OPS):
                    fehler.append(f"{x['titel']}: Schwelle oder Operator fehlt")
                    continue
            neu.append(dict(zeit=z, person=ctx.person, id=uuid.uuid4().hex[:10], analyse_id=a["id"],
                            bereich=x["sektor"] or ("Makro" if g in MAKRO else "Allokation"), sektor=x["sektor"], ticker=x["ticker"],
                            titel=x["titel"], grund=x["warum"], reaktion=_txt(r["Reaktion"]) or x["reaktion"], groesse=g, op=op if g else "",
                            wert="" if w is None else w, stufe=_txt(r["Stufe"]) if _txt(r["Stufe"]) in STUFEN else "Prüfen", geloescht=""))
        for f_ in fehler:
            st.error(f_)
        if neu:
            ctx.speichern(T_PUNKTE, neu, f"{len(neu)} KI-Beobachtungspunkt(e) ins Monitoring übernommen.")


def bereich_portfolio(ctx, fred: dict, lade_kurse):
    """Reiter «KI-Analyse» auf der Seite Portfolio."""
    import streamlit as st

    import ki

    ss = st.session_state
    st.markdown("**KI-Analyse des Portfolios** · Schwachpunkte und worauf wir achten müssen, wenn sich etwas verschiebt – "
                "die Vorschläge lassen sich als Beobachtungspunkte ins Monitoring übernehmen.")
    ids, namen = quellen(ctx.tabellen)
    if not ids:
        st.info("Noch kein Portfolio zum Analysieren: Entweder Transaktionen erfassen oder auf der Seite «Analyse» ein "
                "Analyseportfolio speichern.")
        return
    alle = analysen(ctx.tabellen.get(T_ANALYSE))
    standard = alle[0]["quelle_id"] if alle and alle[0]["quelle_id"] in ids else ids[0]
    if ss.get("kp_quelle") not in ids:
        ss["kp_quelle"] = standard
    c1, c2, c3 = st.columns([2.4, 1.2, 1])
    qid = c1.selectbox("Portfolio", ids, format_func=namen.get, key="kp_quelle")
    liste = analysen(ctx.tabellen.get(T_ANALYSE), qid)
    api, modell = _api_key(), _modell()
    b = ki.app_budget(api, modell)
    c2.caption(f"KI: {modell} · Budget diesen Monat noch {_de(max(b.rest, 0), 2)} von {_de(b.deckel, 2)} USD · "
               "eine Analyse kostet etwa 0,05–0,15 USD")
    los = c3.button("Neu analysieren" if liste else "KI-Analyse starten", type="primary", icon=":material/psychology:",
                    width="stretch", disabled=ctx.nur_lesen or not b.aktiv,
                    help=None if b.aktiv else f"KI pausiert: {b.grund_inaktiv()}")
    if not b.aktiv:
        st.caption(f"KI pausiert ({b.grund_inaktiv()}). API-Key als Streamlit-Secret `anthropic_api_key`; Deckel auf der Seite "
                   "«Sektor-News» → KI & Einstellungen.")
    if los:
        with st.status("KI-Analyse läuft …", expanded=True) as stat:
            try:
                st.write("Kennzahlen, Sektoren und Makro-Lage zusammenstellen …")
                mat = material(ctx, qid, fred, lade_kurse)
                if mat is None:
                    stat.update(label="Nichts zu analysieren – das Portfolio hat keine Positionen.", state="error")
                    return
                pr = prompt(mat)
                st.write(f"Material: {len(mat['ticker'])} Titel, ca. {_de(len(pr) / 3 / 1000, 0)}k Tokens · geschätzte Kosten "
                         f"{_de(kosten_schaetzung(pr, modell), 2)} USD")
                st.write("KI analysiert (dauert 30–90 Sekunden) …")
                text = b.anfrage(pr, "Portfolio-Analyse", max_tokens=MAX_OUT, schaetzung_out=4500, timeout=300)
                ki.app_protokoll(b)
                if text is None:
                    stat.update(label="KI nicht verfügbar: " + ("; ".join(b.fehler) or "unbekannt"), state="error")
                    return
                erg = pruefe_antwort(ki.json_aus(text), mat)
                stat.update(label="Analyse fertig – wird gespeichert.", state="complete")
            except Exception as e:  # noqa: BLE001
                stat.update(label=f"Analyse fehlgeschlagen: {e}", state="error")
                return
        aid = uuid.uuid4().hex[:10]
        ctx.speichern(T_ANALYSE, [dict(zeit=jetzt(), person=ctx.person, id=aid, quelle="bestand" if qid == BESTAND else "analyse",
                                       quelle_id=qid, quelle_name=mat["name"], modell=modell,
                                       ergebnis=json.dumps(erg, ensure_ascii=False, default=str),
                                       kennzahlen=json.dumps(mat["kennzahlen"], ensure_ascii=False, default=str),
                                       hash=quelle_hash(ctx.tabellen, qid))], "KI-Analyse gespeichert.")
        return

    if not liste:
        st.info("Für dieses Portfolio gibt es noch keine KI-Analyse. «KI-Analyse starten» – die App stellt Kennzahlen, Sektoren, "
                "Korrelationen, IPS-Regeln und die Makro-Lage zusammen, die KI sucht Schwachpunkte und schlägt Beobachtungspunkte vor.")
    else:
        if len(liste) > 1:
            i = st.selectbox("Analyse", range(len(liste)), key=f"kp_vers_{qid}",
                             format_func=lambda i: f"{_datum(liste[i]['zeit'])} · {liste[i]['person']}" + (" (neuste)" if i == 0 else ""))
        else:
            i = 0
        a = liste[i]
        e, k = a["ergebnis"], a["kennzahlen"]
        veraltet = a["hash"] and a["hash"] != quelle_hash(ctx.tabellen, qid)
        st.caption(f"Analyse vom {_datum(a['zeit'])} (UTC) · {a['person']} · {a['modell']}"
                   + (f" · Stand: {k.get('titel')} Titel, Aktien/Treasuries/Cash {k.get('aktien')} / {k.get('treas')} / {k.get('cash')} %, "
                      f"Vola {_de(k.get('vola'), 1)} %, Beta {_de(k.get('beta'), 2)}" if k else ""))
        if veraltet:
            st.warning("Das Portfolio wurde seit dieser Analyse geändert – «Neu analysieren», damit die Punkte zum aktuellen Stand passen.",
                       icon=":material/update:")
        if e.get("fazit"):
            st.info(e["fazit"], icon=":material/psychology:")
        st.markdown("##### Schwachpunkte")
        _schwachpunkte(e.get("schwachpunkte") or [])
        st.markdown("##### Worauf wir achten müssen – Beobachtungspunkte")
        kurse_hist = _kurse_fuer([dict(ticker=x["ticker"], sektor=x["sektor"]) for x in e.get("beobachten") or []], lade_kurse, ctx.kurse,
                                 ctx.tabellen)
        bestand = _bestand_bewertung(ctx.tabellen, fred, kurse_hist)
        _beobachten(ctx, a, fred, kurse_hist, bestand)
        c1, c2 = st.columns(2)
        with c1:
            if e.get("staerken"):
                st.markdown("##### Stärken")
                st.markdown("\n".join(f"- {_esc(x)}" for x in e["staerken"]))
        with c2:
            if e.get("fragen"):
                st.markdown("##### Fragen ans Team")
                st.markdown("\n".join(f"- {_esc(x)}" for x in e["fragen"]))
        st.caption("Die KI bekommt nur die Zahlen der App (Kurse 1 Jahr, Sektoren, IPS, Makro-Lage, gesammelte Analystendaten) und "
                   "kann sich irren – die Punkte sind Vorschläge, entscheiden tut das Team.")
    st.divider()
    st.markdown("##### Aktive Beobachtungspunkte im Monitoring")
    ps = punkte(ctx.tabellen.get(T_PUNKTE))
    kurse = _kurse_fuer(ps, lade_kurse, ctx.kurse, ctx.tabellen)
    punkte_tabelle(ctx, fred, kurse, _bestand_bewertung(ctx.tabellen, fred, kurse), key="pf_ki_pkt")
