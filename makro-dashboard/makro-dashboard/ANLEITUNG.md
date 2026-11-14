# Makro-Dashboard USA – Anleitung

Team-App für die FHGR-Portfoliosimulation (Dez. 2026 – Feb. 2027). Sie holt Daten automatisch von FRED, speichert Team-Eingaben in einer gemeinsamen Google-Tabelle und rechnet Ampeln, Szenarien, Risiken, Zyklus und eine Allokationsempfehlung laufend neu.

Einrichtung einmalig, ca. 45–60 Minuten. Du brauchst: ein GitHub-Konto, ein Google-Konto.

---

## Was die App kann

| Seite | Inhalt |
|---|---|
| **Gesamtbild** | Zyklusphase, Ampel-Zähler, alle aktiven Signale, Kapitel-Karten, Gesamtempfehlung, Szenarien, Risiko-Matrix, nächste Termine |
| **8 Kapitel** | Wachstum & Zyklus · Arbeitsmarkt · Inflation & Fed · Zinsen & Anleihen · Kredit & Finanzbedingungen · Konsum & Stimmung · Gewinne & Bewertung · Geopolitik & Extremrisiken – je mit Fazit, Kennzahl-Karten, Charts (2/5/10 Jahre, Schwellenlinien, Rezessionsbalken), Annahme, Aktion, Quellen, Szenarien, Risiken, Notizen |
| **Szenarien & Risiken** | Team-Wahrscheinlichkeiten mit Spanne, Hinweis bei grosser Uneinigkeit, Risiko-Matrix |
| **Allokation** | Aktien/Treasuries, Duration, TIPS-Anteil, Sektor-Über-/Untergewichte – mit Herleitung jeder Zahl |
| **Bewerten** | Jede Person schätzt Szenarien, Risiken (inkl. «Warnsignal ausgelöst») und die Zyklusposition – die App mittelt |
| **Daten eintragen** | Werte, die nicht auf FRED sind (ISM, Conference Board, NFIB, BofA-Umfrage, Forward-KGV/-EPS, DXY) – immer mit 2 Quellen |
| **Termine, Protokoll, Einstellungen** | Countdown, alle Eingaben mit Person und Zeit (CSV-Download), neutrale Allokation als Regler |

**Automatisch (FRED):** 38 Zeitreihen, z. B. BIP, Arbeitslosenquote, Stellenaufbau, Erstanträge, Sahm-Regel, CPI/Kern-CPI, Kern-PCE, 5y5y, Leitzins, 2-/10-J.-Renditen, Zinskurven, Realzins, Laufzeitprämie, BB/BBB/CCC-Spreads, NFCI, St. Louis-Stressindex, SLOOS, Brent, Dollar, Sparquote, Realkonsum, Detailhandel, Michigan, Kreditkarten-Ausfälle, S&P 500, VIX, EPU. Die Daten werden alle 6 Stunden neu geladen (oder sofort über «Einstellungen → FRED-Daten jetzt neu laden»).

**Berechnet:** Output Gap (BIP/Potenzial), Aktienrisikoprämie (Forward-EPS ÷ S&P 500 − 10 J.), Kombi-Signale (Stagflation, «Arbeitsmarkt kippt», Realkonsum 2 Monate ≤ 0), Zyklus-Modell, Allokation.

**Regel:** Für jeden Indikator gilt der Wert mit dem **neusten Datum** – egal ob von FRED, aus eurer Analyse (Startwert) oder von einem Teammitglied. So könnt ihr FRED jederzeit mit einer frischeren Zahl überschreiben.

---

## Schritt 1: Google-Tabelle und Dienstkonto (Speicher fürs Team)

Die App braucht einen «Roboter-Benutzer» (Dienstkonto), der in eure Google-Tabelle schreiben darf.

1. Öffne <https://console.cloud.google.com> und melde dich mit deinem Google-Konto an.
2. Oben links auf die Projektauswahl → **Neues Projekt** → Name z. B. `makro-dashboard` → Erstellen.
3. Suchleiste oben: **Google Sheets API** → Aktivieren. Danach dasselbe für **Google Drive API**.
4. Menü → **IAM und Verwaltung → Dienstkonten → Dienstkonto erstellen**. Name z. B. `makro-dashboard`, Rollen kannst du überspringen → Fertig.
5. Klicke auf das neue Dienstkonto → Tab **Schlüssel** → **Schlüssel hinzufügen → Neuen Schlüssel erstellen → JSON**. Eine JSON-Datei wird heruntergeladen. **Diese Datei ist geheim** – nicht teilen, nicht auf GitHub hochladen.
6. Erstelle in Google Drive eine leere Google-Tabelle, z. B. «Makro-Dashboard Daten».
7. Klicke in der Tabelle auf **Freigeben** und füge die `client_email` aus der JSON-Datei (endet auf `…iam.gserviceaccount.com`) als **Bearbeiter** hinzu.
8. Kopiere die URL der Tabelle – die brauchst du in Schritt 3.

Die App legt die Tabellenblätter (`werte`, `szenario_votes`, `risiko_votes`, …) beim ersten Speichern selbst an. Ihr könnt dort alles nachlesen und bei Bedarf einen Tippfehler korrigieren.

## Schritt 2: Code auf GitHub

1. Auf <https://github.com> anmelden → **New repository** → Name z. B. `makro-dashboard`.
   - *Öffentlich* ist am einfachsten (die App ist trotzdem mit dem Team-Passwort geschützt; im Code stehen keine Passwörter).
   - *Privat* geht auch; dann ist die App standardmässig privat und du lädst dein Team in Streamlit unter «Share» per E-Mail ein (Streamlit Community Cloud erlaubt eine private App).
2. **Add file → Upload files** → den ganzen Inhalt des ZIP-Ordners hochladen (inkl. Ordner `makro` und `.streamlit`). Achtung: Die Datei `.streamlit/secrets.toml.example` darf hoch, eine echte `secrets.toml` **nicht**.
   - Tipp: Ordner, die mit einem Punkt beginnen (`.streamlit`), sind auf dem Mac versteckt – im Finder mit `Cmd + Shift + .` sichtbar machen. Notfalls kannst du `.streamlit/config.toml` weglassen, dann fehlt nur das Farbschema.
3. **Commit changes**.

## Schritt 3: App auf Streamlit Community Cloud online stellen

1. Öffne <https://share.streamlit.io> → mit GitHub anmelden → Zugriff erlauben.
2. **Create app** → App aus einem GitHub-Repository bereitstellen.
3. Repository `makro-dashboard`, Branch `main`, Main file path `app.py`.
4. Optional einen eigenen Namen für die URL wählen (z. B. `fhgr-makro-usa`).
5. **Advanced settings**:
   - Python version: **3.12**
   - **Secrets**: Inhalt aus `.streamlit/secrets.toml.example` hineinkopieren und ausfüllen:
     - `app_password` = euer Team-Passwort
     - `team` = eure vier Namen
     - `sheet_url` = URL aus Schritt 1
     - Abschnitt `[gcp_service_account]` = die Werte aus der JSON-Datei (jede Zeile `"schlüssel": "wert"` aus der JSON wird zu `schlüssel = "wert"`). Beim `private_key` die `\n` genau so stehen lassen.
6. **Deploy**. Nach 2–5 Minuten läuft die App. Link ans Team schicken – fertig.

Auf der Startseite steht in der Seitenleiste «Speicher: Google Sheets». Steht dort «Lokale CSV-Dateien», stimmen die Secrets noch nicht.

**Gut zu wissen:** Wird die App 12 Stunden lang nicht benutzt, schläft sie ein. Wer sie dann öffnet, klickt auf «Yes, get this app back up» und wartet etwa eine Minute. Die Daten in der Google-Tabelle bleiben erhalten.

## Optional: FRED-API-Schlüssel

Ohne Schlüssel lädt die App die Daten über den öffentlichen CSV-Download von FRED. Falls dabei Reihen fehlen (Warnung auf dem Gesamtbild), hol dir unter <https://fredaccount.stlouisfed.org> einen kostenlosen API-Schlüssel und trage ihn in den Secrets als `fred_api_key = "…"` ein.

## Optional: Lokal testen

```bash
pip install -r requirements.txt
streamlit run app.py
```

Ohne Secrets speichert die App lokal im Ordner `daten/` (CSV) und fragt kein Passwort ab.

---

## Bedienung im Alltag

- **Neue Daten (ISM, Conference Board, NFIB, BofA, KGV, EPS, DXY):** «Daten eintragen» → Indikator wählen → Wert, Datum (Monatswerte: 1. des Monats), 2 Quellen → Speichern. Die Tabelle «Fällige Updates» zeigt, was veraltet ist.
- **Nach wichtigen Ereignissen** (Fed 9.12., CPI, G20, Frist USA–China 10.01.): auf «Bewerten» eure Szenario-Wahrscheinlichkeiten und Risiken anpassen und bei Risiken ggf. «Warnsignal ausgelöst» ankreuzen. Auf der Kapitelseite eine Notiz oder ein neues Kapitel-Fazit erfassen.
- **Vor Allokationsentscheiden:** Seite «Allokation» – dort steht, welches Signal was auslöst.
- **Korrekturen:** einfach einen neuen Eintrag machen; der neuste zählt. Alles bleibt im Protokoll nachvollziehbar.

## Anpassen

Alles Inhaltliche steht in **`makro/config.py`**: Indikatoren, Schwellen, Annahmen, Aktionen, Quellen, Szenarien (inkl. Duration/TIPS je Szenario und Sektorwirkung), Risiken, Termine. Änderung auf GitHub speichern → die App aktualisiert sich nach einem Neustart automatisch.

Die Allokationsregeln stehen in **`makro/logic.py`**, Funktion `allokation`.

### Bitte im Team prüfen

- **Schwellen mit Herkunft «Vorschlag»**: Bei den neu ergänzten Indikatoren (u. a. BIP, Erstanträge, Sahm-Regel, Stundenlöhne, CPI, 5y5y, 10 J. − 3 Mt., Realzins, CCC, St. Louis-Stressindex, SLOOS, Detailhandel, S&P-Drawdown, VIX) hat Claude die Schwellen vorgeschlagen. Ausserdem stammen die Gelb-Schwellen bei Kern-CPI und Sparquote von Claude. In der App ist das jeweils gekennzeichnet.
- **Grösse der Allokations-Signale**: Wo eure Analyse nur die Richtung nennt (z. B. «Anleihen leicht übergewichten»), hat Claude eine Grösse eingesetzt, z. B. −5 PP. Diese Regeln sind auf der Seite «Allokation» mit «Grösse: Vorschlag» markiert.
- **Startwerte**: Für die Szenarien «Gewinne & Bewertung» gibt es in der Analyse keine Wahrscheinlichkeiten. Bis ihr selbst bewertet, rechnet die App mit 25/50/25 %. Die Auswirkung der Risiken (1–5) ist ebenfalls ein Startwert.
- **DXY**: Der Dollar-Index ist nicht auf FRED und muss manuell eingetragen werden. Als Kontext lädt die App automatisch den breiten Dollar-Index der Fed.
- **Brent auf FRED** erscheint mit etwa einer Woche Verzug. Neuere Kurse könnt ihr selbst eintragen.
