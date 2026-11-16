"""Märkte heute, News & Tagesupdate, Verbindung.

Märkte: Yahoo Finance (über yfinance), Fallback FRED. News: Google-News-RSS je Thema.
Optional: KI-Zusammenfassung über die Claude-API (Secret anthropic_api_key).
"""
from __future__ import annotations

import html
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

from makro import config as C
from makro import ui

SIM_START = pd.Timestamp(C.SIMULATION_START)

# (Name, Yahoo-Ticker, FRED-Fallback, Art) – Art: pct = Veränderung in %, pkt = in Punkten, bp = in Basispunkten
MARKT = {
    "Indizes": [
        ("S&P 500", "^GSPC", "SP500", "pct"),
        ("Dow Jones", "^DJI", "DJIA", "pct"),
        ("Nasdaq Composite", "^IXIC", "NASDAQCOM", "pct"),
        ("Russell 2000", "^RUT", None, "pct"),
        ("VIX", "^VIX", "VIXCLS", "pkt"),
    ],
    "Sektoren": [  # S&P-500-Sektoren über die SPDR-Sektor-ETFs
        ("IT", "XLK", None, "pct"), ("Finanzen", "XLF", None, "pct"), ("Gesundheit", "XLV", None, "pct"),
        ("Zykl. Konsum", "XLY", None, "pct"), ("Kommunikation", "XLC", None, "pct"), ("Industrie", "XLI", None, "pct"),
        ("Basiskonsum", "XLP", None, "pct"), ("Energie", "XLE", None, "pct"), ("Versorger", "XLU", None, "pct"),
        ("Immobilien", "XLRE", None, "pct"), ("Grundstoffe", "XLB", None, "pct"),
    ],
    "Zinsen": [
        ("US-Treasury 2 J.", None, "DGS2", "bp"),
        ("US-Treasury 10 J.", "^TNX", "DGS10", "bp"),
        ("US-Treasury 30 J.", "^TYX", "DGS30", "bp"),
    ],
    "Währungen": [
        ("EUR/USD", "EURUSD=X", "DEXUSEU", "pct"),
        ("USD/CHF", "CHF=X", "DEXSZUS", "pct"),
        ("USD/JPY", "JPY=X", "DEXJPUS", "pct"),
        ("GBP/USD", "GBPUSD=X", "DEXUSUK", "pct"),
        ("USD/CNY", "CNY=X", "DEXCHUS", "pct"),
        ("Dollar-Index (DXY)", "DX-Y.NYB", None, "pct"),
    ],
    "Rohstoffe": [
        ("Brent (USD/bbl)", "BZ=F", "DCOILBRENTEU", "pct"),
        ("WTI (USD/bbl)", "CL=F", "DCOILWTICO", "pct"),
        ("Erdgas Henry Hub (USD/MMBtu)", "NG=F", "DHHNGSP", "pct"),
        ("Gold (USD/oz)", "GC=F", None, "pct"),
        ("Silber (USD/oz)", "SI=F", None, "pct"),
        ("Kupfer (USD/lb)", "HG=F", None, "pct"),
    ],
    "Krypto": [
        ("Bitcoin (USD)", "BTC-USD", "CBBTCUSD", "pct"),
        ("Ethereum (USD)", "ETH-USD", "CBETHUSD", "pct"),
        ("Solana (USD)", "SOL-USD", None, "pct"),
    ],
}
FRED_MARKT = sorted({f for gruppe in MARKT.values() for _, _, f, _ in gruppe if f})
YAHOO = tuple(sorted({y for gruppe in MARKT.values() for _, y, _, _ in gruppe if y}))

NEWS_THEMEN = [
    ("markt", "📈 Märkte allgemein", "stock market today S&P 500 Dow", ["gewinne"]),
    ("fed", "🏛️ Fed & Inflation", "Federal Reserve OR Powell OR CPI OR inflation", ["inflation", "zinsen"]),
    ("zinsen", "💵 Treasuries & Zinsen", "Treasury yields OR bond market", ["zinsen"]),
    ("arbeit", "👷 Arbeitsmarkt", "jobs report OR jobless claims OR payrolls OR unemployment rate", ["arbeitsmarkt"]),
    ("konsum", "🛒 Konsum & Stimmung", "consumer spending OR consumer confidence OR retail sales", ["konsum"]),
    ("gewinne", "📊 Unternehmen & KI", "earnings OR Nvidia OR AI stocks", ["gewinne"]),
    ("oel", "🛢️ Öl & Iran", "oil prices OR Brent OR Iran OR Hormuz", ["geopolitik", "kredit"]),
    ("china", "🇨🇳 USA–China & Zölle", "US China trade OR tariffs OR rare earths", ["geopolitik"]),
    ("krypto", "₿ Krypto", "bitcoin OR crypto market", []),
]


# ---------------------------------------------------------------------------
# Daten laden
# ---------------------------------------------------------------------------
@st.cache_data(ttl=15 * 60, show_spinner="Lade Marktdaten …")
def lade_yahoo(tickers: tuple[str, ...]):
    """Tagesschlusskurse (bzw. letzter Kurs) für ~14 Monate. Gibt ({ticker: Series}, fehler, zeit) zurück."""
    daten, fehler = {}, []
    try:
        import yfinance as yf

        df = yf.download(list(tickers), period="14mo", interval="1d", group_by="ticker", auto_adjust=False,
                         progress=False, threads=True, timeout=20)
    except Exception as e:  # noqa: BLE001
        return {}, [f"Yahoo Finance: {type(e).__name__}"], datetime.now().strftime("%d.%m.%Y %H:%M")
    for t in tickers:
        try:
            s = _spalte(df, t)
            if s is not None and len(s.dropna()):
                s = s.dropna()
                s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
                daten[t] = s[~s.index.duplicated(keep="last")]
            else:
                fehler.append(t)
        except Exception:  # noqa: BLE001
            fehler.append(t)
    return daten, fehler, datetime.now().strftime("%d.%m.%Y %H:%M")


def _spalte(df: pd.DataFrame, t: str):
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        for lvl in (0, 1):
            if t in df.columns.get_level_values(lvl):
                sub = df.xs(t, axis=1, level=lvl)
                for c in ("Close", "Adj Close"):
                    if c in sub.columns:
                        return sub[c]
        return None
    return df["Close"] if "Close" in df.columns else None


@st.cache_data(ttl=30 * 60, show_spinner="Lade News …")
def lade_news(stunden: int = 30):
    out, fehler = {}, []
    jetzt = datetime.now(timezone.utc)
    for tid, _, query, _ in NEWS_THEMEN:
        url = (f"https://news.google.com/rss/search?q={quote_plus(query + ' when:2d')}"
               f"&hl=en-US&gl=US&ceid=US:en")
        try:
            r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (Makro-Dashboard FHGR)"})
            root = ET.fromstring(r.content)
            items = []
            for it in root.iter("item"):
                titel = (it.findtext("title") or "").strip()
                quelle = (it.findtext("source") or "").strip()
                if quelle and titel.endswith(" - " + quelle):
                    titel = titel[: -len(quelle) - 3]
                try:
                    zeit = parsedate_to_datetime(it.findtext("pubDate"))
                except Exception:  # noqa: BLE001
                    zeit = None
                if zeit is not None and (jetzt - zeit).total_seconds() > stunden * 3600:
                    continue
                items.append(dict(titel=titel, link=it.findtext("link") or "", quelle=quelle, zeit=zeit))
            gesehen, eindeutig = set(), []
            for i in sorted(items, key=lambda x: x["zeit"] or jetzt, reverse=True):
                k = i["titel"].lower()[:70]
                if k not in gesehen:
                    gesehen.add(k)
                    eindeutig.append(i)
            out[tid] = eindeutig[:6]
        except Exception as e:  # noqa: BLE001
            out[tid] = []
            fehler.append(f"{tid}: {type(e).__name__}")
    return out, fehler, datetime.now().strftime("%d.%m.%Y %H:%M")


@st.cache_data(ttl=6 * 3600, show_spinner="KI fasst den Tag zusammen …")
def ki_zusammenfassung(tag: str, prompt: str, api_key: str, modell: str):
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=90,
                          headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                                   "content-type": "application/json"},
                          json={"model": modell, "max_tokens": 900,
                                "system": ("Du schreibst ein kurzes tägliches Makro-Update auf Deutsch (Schweizer Rechtschreibung, "
                                           "ss statt ß) für ein studentisches Team, das in einer Portfoliosimulation USD 10 Mio. "
                                           "in S&P-500-Aktien und US-Treasuries verwaltet. Nüchtern, präzise, keine Floskeln. "
                                           "Nur Fakten aus den gelieferten Daten und Schlagzeilen verwenden, nichts erfinden."),
                                "messages": [{"role": "user", "content": prompt}]})
        if r.status_code != 200:
            return None, f"KI-Zusammenfassung nicht verfügbar (HTTP {r.status_code})."
        return "".join(b.get("text", "") for b in r.json().get("content", [])), None
    except Exception as e:  # noqa: BLE001
        return None, f"KI-Zusammenfassung nicht verfügbar ({type(e).__name__})."


# ---------------------------------------------------------------------------
# Kennzahlen
# ---------------------------------------------------------------------------
def _asof(s: pd.Series, t: pd.Timestamp):
    s = s[s.index <= t]
    return float(s.iloc[-1]) if len(s) else None


def _veraenderung(neu, alt, art):
    if neu is None or alt is None or (art == "pct" and alt == 0):
        return None
    if art == "pct":
        return (neu / alt - 1) * 100
    if art == "bp":
        return (neu - alt) * 100
    return neu - alt


def kennzahlen(s: pd.Series, art: str) -> dict:
    s = s.dropna().sort_index()
    if s.empty:
        return {}
    letzt, datum = float(s.iloc[-1]), s.index[-1]
    vor = float(s.iloc[-2]) if len(s) > 1 else None
    w = _asof(s, datum - pd.Timedelta(days=7))
    m = _asof(s, datum - pd.DateOffset(months=1))
    start = _asof(s, SIM_START - pd.Timedelta(days=1)) if datum >= SIM_START else None
    ytd = _asof(s, pd.Timestamp(year=datum.year, month=1, day=1) - pd.Timedelta(days=1))
    return dict(kurs=letzt, datum=datum, d1=_veraenderung(letzt, vor, art), w1=_veraenderung(letzt, w, art),
                m1=_veraenderung(letzt, m, art), start=_veraenderung(letzt, start, art),
                ytd=_veraenderung(letzt, ytd, art), art=art)


def markt_tabelle(yahoo: dict, fred: dict) -> pd.DataFrame:
    zeilen = []
    for gruppe, liste in MARKT.items():
        for name, y, f, art in liste:
            s, quelle = None, ""
            if y and y in yahoo:
                s, quelle = yahoo[y], "Yahoo Finance"
                if y in ("^TNX", "^TYX") and s.dropna().iloc[-1] > 20:  # ältere Notierung ×10
                    s = s / 10
            fs = fred.get(f) if f else None
            if fs is not None and len(fs):
                if s is None or (fs.index[-1] > s.index[-1] + pd.Timedelta(days=2)):
                    s, quelle = fs, "FRED"
            k = kennzahlen(s, art) if s is not None else {}
            zeilen.append(dict(gruppe=gruppe, name=name, ticker=y or f, quelle=quelle if k else "keine Daten", **k))
    return pd.DataFrame(zeilen)


def _fmt_kurs(name, x):
    if x is None or pd.isna(x):
        return "–"
    if "Treasury" in name:
        return f"{ui.de(x, 2)} %"
    if name in ("EUR/USD", "GBP/USD", "USD/CHF", "USD/CNY"):
        return ui.de(x, 4)
    if abs(x) >= 10000:
        return ui.de(x, 0)
    if abs(x) >= 1000:
        return ui.de(x, 1)
    return ui.de(x, 2)


def _fmt_ver(x, art):
    if x is None or pd.isna(x):
        return "–"
    if art == "pct":
        return f"{ui.de(x, 2, True)} %"
    if art == "bp":
        return f"{ui.de(x, 0, True)} bp"
    return ui.de(x, 2, True)


def _farbe(x, art: str = "pct"):
    """Grün = gut für Aktien, Rot = schlecht. Renditen (bp) neutral, VIX (pkt) umgekehrt."""
    if x is None or pd.isna(x) or abs(x) < 1e-9 or art == "bp":
        return "#5A6475"
    if art == "pkt":
        x = -x
    return "#2E9E5B" if x > 0 else "#D64545"


# ---------------------------------------------------------------------------
# Ticker-Leiste (oben auf jeder Seite)
# ---------------------------------------------------------------------------
LEISTE = ["S&P 500", "Dow Jones", "Nasdaq Composite", "VIX", "US-Treasury 10 J.", "EUR/USD", "USD/CHF",
          "Brent (USD/bbl)", "Gold (USD/oz)", "Bitcoin (USD)"]
KURZ = {"Nasdaq Composite": "Nasdaq", "US-Treasury 10 J.": "10 J.", "Brent (USD/bbl)": "Brent",
        "Gold (USD/oz)": "Gold", "Bitcoin (USD)": "Bitcoin"}


def ticker_leiste(tab: pd.DataFrame):
    if tab is None or tab.empty:
        return
    teile = []
    for n in LEISTE:
        r = tab[tab["name"] == n]
        if r.empty or pd.isna(r.iloc[0].get("kurs", np.nan)):
            continue
        r = r.iloc[0]
        d1 = r.get("d1")
        teile.append(f'<span style="white-space:nowrap"><b>{html.escape(KURZ.get(n, n))}</b> {_fmt_kurs(n, r["kurs"])} '
                     f'<span style="color:{_farbe(d1, r["art"])}">'
                     f'{_fmt_ver(d1, r["art"])}</span></span>')
    if teile:
        st.markdown('<div style="font-size:0.8rem; color:#374151; background:#F4F6FA; border-radius:8px; '
                    'padding:6px 12px; margin-bottom:6px; line-height:1.9">' + " &nbsp;·&nbsp; ".join(teile) + "</div>",
                    unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Tagesupdate (regelbasiert + optional KI)
# ---------------------------------------------------------------------------
def _r(tab, name):
    r = tab[tab["name"] == name]
    return None if r.empty or pd.isna(r.iloc[0].get("kurs", np.nan)) else r.iloc[0]


def _teil(tab, name, kurz=None):
    r = _r(tab, name)
    if r is None:
        return None
    return f"{kurz or name} {_fmt_kurs(name, r['kurs'])} ({_fmt_ver(r['d1'], r['art'])})"


def tagesupdate_saetze(tab: pd.DataFrame, alloc: dict | None, signale: int | None) -> list[str]:
    s = []
    idx = [x for x in (_teil(tab, "S&P 500"), _teil(tab, "Dow Jones", "Dow"), _teil(tab, "Nasdaq Composite", "Nasdaq"),
                       _teil(tab, "Russell 2000", "Russell 2000")) if x]
    if idx:
        vix = _r(tab, "VIX")
        s.append("**US-Aktien:** " + ", ".join(idx) + (f". VIX {ui.de(vix['kurs'], 1)} ({_fmt_ver(vix['d1'], 'pkt')})." if vix is not None else "."))
    sek = tab[(tab["gruppe"] == "Sektoren") & tab["d1"].notna()] if "d1" in tab else pd.DataFrame()
    if len(sek):
        top, flop = sek.sort_values("d1").iloc[-1], sek.sort_values("d1").iloc[0]
        plus = int((sek["d1"] > 0).sum())
        txt = (f"**Sektoren:** stärkster {top['name']} ({_fmt_ver(top['d1'], 'pct')}), schwächster {flop['name']} "
               f"({_fmt_ver(flop['d1'], 'pct')}); {plus} von {len(sek)} im Plus.")
        if alloc is not None:
            emp = dict(zip(alloc["sektoren"]["sektor"], alloc["sektoren"]["empfehlung"]))
            if emp.get(top["name"], "Neutral") != "Neutral" or emp.get(flop["name"], "Neutral") != "Neutral":
                txt += (f" Eure Empfehlung: {top['name']} {emp.get(top['name'], 'Neutral').lower()}, "
                        f"{flop['name']} {emp.get(flop['name'], 'Neutral').lower()}.")
        s.append(txt)
    z = [x for x in (_teil(tab, "US-Treasury 2 J.", "2 J."), _teil(tab, "US-Treasury 10 J.", "10 J.")) if x]
    if z:
        s.append("**Treasuries:** " + ", ".join(z) + ".")
    fx = [x for x in (_teil(tab, "EUR/USD"), _teil(tab, "USD/CHF"), _teil(tab, "USD/JPY"), _teil(tab, "Dollar-Index (DXY)", "DXY")) if x]
    if fx:
        s.append("**Währungen:** " + ", ".join(fx) + ".")
    ro = [x for x in (_teil(tab, "Brent (USD/bbl)", "Brent"), _teil(tab, "WTI (USD/bbl)", "WTI"), _teil(tab, "Gold (USD/oz)", "Gold"),
                      _teil(tab, "Kupfer (USD/lb)", "Kupfer"), _teil(tab, "Erdgas Henry Hub (USD/MMBtu)", "Erdgas")) if x]
    if ro:
        s.append("**Rohstoffe:** " + ", ".join(ro) + ".")
    kr = [x for x in (_teil(tab, "Bitcoin (USD)", "Bitcoin"), _teil(tab, "Ethereum (USD)", "Ethereum")) if x]
    if kr:
        s.append("**Krypto:** " + ", ".join(kr) + ".")
    check = []
    brent, vix, dxy = _r(tab, "Brent (USD/bbl)"), _r(tab, "VIX"), _r(tab, "Dollar-Index (DXY)")
    if brent is not None:
        b = brent["kurs"]
        check.append("Brent ≥ 110 → Zykliker reduzieren" if b >= 110 else "Brent ≥ 100 → Duration 2,5 J., TIPS 25 %" if b >= 100
                     else "Brent im Basisband 85–100 USD" if b >= 85 else "Brent < 85 → Energie-Übergewicht abbauen")
    if dxy is not None:
        check.append("DXY > 103 (rot)" if dxy["kurs"] > 103 else "DXY unter 103")
    if vix is not None:
        check.append("VIX ≥ 30 (rot)" if vix["kurs"] >= 30 else "VIX ≥ 20 (gelb)" if vix["kurs"] >= 20 else "VIX ruhig (< 20)")
    if check:
        s.append("**Ampel-Check:** " + "; ".join(check) + (f". Aktive Signale gesamt: {signale}." if signale is not None else "."))
    return s


def _ki_prompt(saetze, news, signale_namen):
    kopf = "\n".join(x.replace("**", "") for x in saetze)
    schlag = []
    for tid, name, _, _ in NEWS_THEMEN:
        for n in news.get(tid, [])[:4]:
            schlag.append(f"[{name}] {n['titel']} ({n['quelle']})")
    return (f"Marktdaten von heute:\n{kopf}\n\nSchlagzeilen der letzten 30 Stunden:\n" + "\n".join(schlag) +
            f"\n\nAktive Warnsignale im Team-Dashboard: {', '.join(signale_namen) or 'keine'}\n"
            "Szenarien des Teams: Zinsen (Hawkishe Fed / Inflationsschock / Risk-off), Öl & Iran (Basis / Eskalation / Deal), "
            "USA–China bis 10.01.2027 (Teil-Deal / Eskalation / Entspannung), Gewinne (Positiv / Basis / Negativ).\n\n"
            "Schreibe: 1) «Das Wichtigste heute» – 4 bis 6 kurze Punkte (Markt + News). "
            "2) «Bedeutung für unsere Szenarien» – 2 bis 3 Punkte, welche Szenarien oder Warnsignale die News stützen "
            "oder schwächen. Insgesamt höchstens 180 Wörter, Markdown-Aufzählungen.")


# ---------------------------------------------------------------------------
# Seiten
# ---------------------------------------------------------------------------
def seite_maerkte(tab: pd.DataFrame, yahoo: dict, fred: dict, quelle_info: str, alloc: dict | None):
    st.title("🌐 Märkte heute")
    st.caption(f"Automatisch aktualisiert (alle 15 Minuten) · {quelle_info} · Sektoren über die SPDR-Sektor-ETFs · "
               "«seit Start» ab 01.12.2026")
    if tab.empty or tab["kurs"].isna().all():
        st.error("Keine Marktdaten verfügbar – Yahoo Finance und FRED waren nicht erreichbar. Später nochmals versuchen.")
        return

    top = ["S&P 500", "Dow Jones", "Nasdaq Composite", "VIX", "US-Treasury 10 J.", "EUR/USD", "USD/CHF",
           "Brent (USD/bbl)", "Gold (USD/oz)", "Bitcoin (USD)"]
    for i in range(0, len(top), 5):
        cols = st.columns(5)
        for c, n in zip(cols, top[i:i + 5]):
            r = _r(tab, n)
            if r is None:
                continue
            farbe = _farbe(r["d1"], r["art"])
            with c.container(border=True):
                st.markdown(f'<div class="kpi-name" style="min-height:0">{html.escape(n)}</div>'
                            f'<div class="kpi-wert">{_fmt_kurs(n, r["kurs"])}</div>'
                            f'<div style="color:{farbe}; font-weight:700">{_fmt_ver(r["d1"], r["art"])} '
                            f'<span class="kpi-sub" style="font-weight:400">1 Tag · Stand {ui.datum_txt(r["datum"])}</span></div>',
                            unsafe_allow_html=True)

    st.subheader("S&P-500-Sektoren")
    zeitraum = st.segmented_control("Zeitraum", ["1 Tag", "1 Woche", "1 Monat", "Seit Start", "YTD"], default="1 Tag",
                                    key="sek_zeit") or "1 Tag"
    spalte = {"1 Tag": "d1", "1 Woche": "w1", "1 Monat": "m1", "Seit Start": "start", "YTD": "ytd"}[zeitraum]
    sek = tab[tab["gruppe"] == "Sektoren"].copy()
    spx = _r(tab, "S&P 500")
    c1, c2 = st.columns([1.2, 1])
    with c1:
        d = sek.dropna(subset=[spalte]).sort_values(spalte)
        if d.empty:
            st.info("Für diesen Zeitraum noch keine Daten (Seit Start: ab 01.12.2026).")
        else:
            fig = go.Figure(go.Bar(x=d[spalte], y=d["name"], orientation="h",
                                   marker_color=[_farbe(x) for x in d[spalte]],
                                   text=[_fmt_ver(x, "pct") for x in d[spalte]], textposition="outside",
                                   hovertemplate="%{y}: %{x:+.2f} %<extra></extra>"))
            if spx is not None and spx.get(spalte) is not None and not pd.isna(spx.get(spalte)):
                fig.add_vline(x=spx[spalte], line=dict(color="#2F5DA8", dash="dash"),
                              annotation_text=f"S&P 500 {_fmt_ver(spx[spalte], 'pct')}", annotation_position="top")
            m = max(1.0, float(d[spalte].abs().max()) * 1.35)
            fig.update_layout(height=380, margin=dict(l=8, r=8, t=30, b=8), plot_bgcolor="white", showlegend=False)
            fig.update_xaxes(range=[-m, m], ticksuffix=" %", zeroline=True, zerolinecolor="#94A3B8", gridcolor="#EEF0F3")
            st.plotly_chart(fig, width="stretch", config=ui.PLOT_CFG)
    with c2:
        emp = {}
        if alloc is not None:
            emp = dict(zip(alloc["sektoren"]["sektor"], alloc["sektoren"]["empfehlung"]))
        t = sek.copy()
        t["rel"] = t[spalte] - (spx[spalte] if spx is not None and spx.get(spalte) is not None else np.nan)
        out = pd.DataFrame({"Sektor": t["name"], "ETF": t["ticker"], zeitraum: t[spalte].map(lambda x: _fmt_ver(x, "pct")),
                            "vs. S&P": t["rel"].map(lambda x: _fmt_ver(x, "pct").replace(" %", " PP")),
                            "Eure Empfehlung": t["name"].map(lambda n: emp.get(n, "–"))})
        out = out.iloc[t[spalte].fillna(-999).argsort()[::-1]]
        st.dataframe(out, hide_index=True, width="stretch", height=420)
        st.caption("«Eure Empfehlung» kommt aus der Seite Allokation (Szenarien × Wirkung + Signale).")

    st.subheader("Alle Kurse")
    tabs = st.tabs(list(MARKT.keys()))
    for tb, gruppe in zip(tabs, MARKT.keys()):
        with tb:
            d = tab[tab["gruppe"] == gruppe]
            out = pd.DataFrame({
                "Instrument": d["name"], "Kurs": [_fmt_kurs(n, k) for n, k in zip(d["name"], d["kurs"])],
                "1 Tag": [_fmt_ver(x, a) for x, a in zip(d["d1"], d["art"])],
                "1 Woche": [_fmt_ver(x, a) for x, a in zip(d["w1"], d["art"])],
                "1 Monat": [_fmt_ver(x, a) for x, a in zip(d["m1"], d["art"])],
                "Seit Start": [_fmt_ver(x, a) for x, a in zip(d["start"], d["art"])],
                "YTD": [_fmt_ver(x, a) for x, a in zip(d["ytd"], d["art"])],
                "Stand": d["datum"].map(ui.datum_txt), "Quelle": d["quelle"], "Ticker": d["ticker"]})
            st.dataframe(out, hide_index=True, width="stretch")

    st.subheader("Verlauf")
    namen = [n for n in tab["name"] if _r(tab, n) is not None]
    c1, c2 = st.columns([2, 1])
    wahl = c1.multiselect("Instrumente (normiert auf 100)", namen, default=[n for n in ("S&P 500", "Dow Jones", "Nasdaq Composite") if n in namen])
    monate = c2.segmented_control("Zeitraum", [1, 3, 6, 12], default=3, format_func=lambda m: f"{m} Mt.", key="verl") or 3
    fig = go.Figure()
    for n in wahl:
        row = tab[tab["name"] == n].iloc[0]
        s = _reihe(row, yahoo, fred)
        if s is None:
            continue
        s = s[s.index >= s.index[-1] - pd.DateOffset(months=monate)]
        if len(s) and s.iloc[0] != 0:
            fig.add_trace(go.Scatter(x=s.index, y=s / s.iloc[0] * 100, name=n, mode="lines",
                                     hovertemplate=n + ": %{y:.1f}<extra></extra>"))
    fig.update_layout(height=340, margin=dict(l=8, r=8, t=10, b=8), plot_bgcolor="white", hovermode="x unified",
                      legend=dict(orientation="h", y=-0.15))
    fig.update_yaxes(gridcolor="#EEF0F3")
    if pd.Timestamp.today() >= SIM_START:
        fig.add_vline(x=SIM_START.timestamp() * 1000, line=dict(color="#94A3B8", dash="dot"))
    st.plotly_chart(fig, width="stretch", config=ui.PLOT_CFG)


def _reihe(row, yahoo, fred):
    name = row["name"]
    for gruppe in MARKT.values():
        for n, y, f, _ in gruppe:
            if n == name:
                if row["quelle"] == "Yahoo Finance" and y in yahoo:
                    return yahoo[y].dropna()
                if f and f in fred:
                    return fred[f].dropna()
    return None


def seite_news(tab: pd.DataFrame, news: dict, news_fehler: list, news_zeit: str, alloc, signale: list,
               speichern, person: str, notizen: pd.DataFrame, api_key: str | None, modell: str):
    st.title("📰 Tagesupdate & News")
    heute = datetime.now().strftime("%d.%m.%Y")
    st.caption(f"Stand {news_zeit} · aktualisiert sich automatisch (News alle 30 Min., Kurse alle 15 Min.)")

    saetze = tagesupdate_saetze(tab, alloc, len(signale)) if not tab.empty else []
    st.subheader(f"Tagesupdate {heute}")
    with st.container(border=True):
        if api_key:
            text, fehler = ki_zusammenfassung(datetime.now().strftime("%Y-%m-%d-%H")[:13], _ki_prompt(saetze, news, [s["titel"] for s in signale]),
                                              api_key, modell)
            if text:
                st.markdown(text)
                st.caption("KI-Zusammenfassung (Claude) aus den Kursen und Schlagzeilen unten – Fakten bitte an den Quellen prüfen.")
                st.divider()
            elif fehler:
                st.caption(fehler)
        if saetze:
            st.markdown("\n".join(f"- {x}" for x in saetze))
        else:
            st.info("Keine Marktdaten verfügbar.")

    st.subheader("Schlagzeilen der letzten 30 Stunden")
    if news_fehler and len(news_fehler) == len(NEWS_THEMEN):
        st.warning("News konnten nicht geladen werden (Google News nicht erreichbar). Später nochmals versuchen.")
    cols = st.columns(3)
    for i, (tid, name, _, kap) in enumerate(NEWS_THEMEN):
        with cols[i % 3].container(border=True):
            kap_txt = ", ".join(C.KAPITEL_BY_ID[k]["name"] for k in kap if k in C.KAPITEL_BY_ID)
            st.markdown(f"**{name}**" + (f"<br><span class='klein'>Kapitel: {html.escape(kap_txt)}</span>" if kap_txt else ""),
                        unsafe_allow_html=True)
            items = news.get(tid, [])
            if not items:
                st.caption("Keine neuen Meldungen.")
            for n in items[:5]:
                zeit = n["zeit"].astimezone().strftime("%d.%m. %H:%M") if n["zeit"] else ""
                st.markdown(f"<div class='klein' style='margin-bottom:6px'><a href='{html.escape(n['link'])}' target='_blank'>"
                            f"{html.escape(n['titel'])}</a><br><span style='color:#7A8699'>{html.escape(n['quelle'])} · {zeit}</span></div>",
                            unsafe_allow_html=True)
    st.caption("Quelle: Google News (englischsprachige US-Ausgabe). Für eure Analyse gilt weiterhin: jede Information mit 2 Quellen belegen.")

    st.divider()
    st.subheader("Tagesbriefing des Dozenten")
    st.caption("Hier könnt ihr das tägliche Briefing einfügen – so ist es fürs ganze Team archiviert und neben euren Daten sichtbar.")
    with st.form("briefing", clear_on_submit=True):
        text = st.text_area("Briefing (Text einfügen)", height=140)
        if st.form_submit_button("Briefing speichern", type="primary") and text.strip():
            speichern("notizen", [dict(zeit=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), person=person,
                                       kapitel="briefing", typ="briefing", text=text.strip())], "Briefing gespeichert.")
    if notizen is not None and not notizen.empty:
        b = notizen[notizen["kapitel"] == "briefing"].sort_values("zeit", ascending=False)
        for _, r in b.head(10).iterrows():
            with st.expander(f"Briefing vom {r['zeit'][:10]} (eingefügt von {r['person']})", expanded=False):
                st.text(r["text"])


def seite_verbindung(store, store_fehler, team_modus: bool, fred_ok: int, fred_total: int, yahoo_ok: int,
                     yahoo_total: int, news_fehler: list, test_fn):
    st.title("🔌 Speicher & Verbindungen")
    if store_fehler:
        st.error(f"**Google-Tabelle nicht verbunden.** {store_fehler}")
        st.markdown("Solange das so ist, werden **keine** Team-Eingaben gespeichert – die App blockiert das Speichern, "
                    "damit nichts verloren geht.")
    elif store.name == "Google-Tabelle":
        st.success("**Google-Tabelle verbunden.** Jeder Eintrag wird nach dem Speichern aus der Tabelle zurückgelesen und erst dann bestätigt.")
    else:
        st.info("Lokaler Testmodus (keine Secrets gesetzt).")
    if st.button("Verbindung jetzt testen", type="primary"):
        ok, msg = test_fn()
        (st.success if ok else st.error)(msg)

    st.subheader("Datenquellen")
    st.dataframe(pd.DataFrame([
        dict(Quelle="Google-Tabelle (Team-Eingaben)", Status="✅ verbunden" if not store_fehler and store.name == "Google-Tabelle" else "❌ nicht verbunden" if team_modus else "– Testmodus"),
        dict(Quelle="FRED (Makrodaten)", Status=f"{'✅' if fred_ok == fred_total else '⚠️'} {fred_ok} von {fred_total} Reihen"),
        dict(Quelle="Yahoo Finance (Märkte)", Status=f"{'✅' if yahoo_ok == yahoo_total else '⚠️'} {yahoo_ok} von {yahoo_total} Kursen"),
        dict(Quelle="Google News (Schlagzeilen)", Status="✅ geladen" if len(news_fehler) < len(NEWS_THEMEN) else "❌ nicht erreichbar"),
    ]), hide_index=True, width="stretch")

    st.subheader("Checkliste Google-Tabelle")
    st.markdown("""
1. In der Google-Tabelle: **Erweiterungen → Apps Script** – dort steht der Speicher-Code (beginnt mit `const TOKEN = …`) und ist gespeichert.
2. **Bereitstellen → Bereitstellungen verwalten → Stift**:
   - Typ **Web-App**, Ausführen als **Ich**
   - Zugriff **«Jeder»** – *nicht* «Jeder mit Google-Konto»
   - Version **«Neue Version»** → **Bereitstellen**
3. In Streamlit unter **Secrets**: `script_url = "https://script.google.com/macros/s/…/exec"` (genau diese URL) und `script_token` unverändert.
4. **Reboot app** und hier auf **«Verbindung jetzt testen»** klicken.

Test im Browser: Die script_url in einem **privaten Fenster** öffnen. Richtig ist `{"ok":false,"fehler":"Falscher Schlüssel"}`. Erscheint eine Google-Anmeldeseite, steht der Zugriff nicht auf «Jeder».
""")
