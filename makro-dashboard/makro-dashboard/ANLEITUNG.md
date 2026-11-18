# Makro-Dashboard USA – Anleitung

Die Einrichtung dauert etwa 20 Minuten. Du brauchst nur dein Google-Konto, GitHub und Streamlit. Du musst nichts programmieren und nichts im Code anpassen, du kopierst nur an drei Stellen etwas hinein.

Du hast von Claude 3 Dateien bekommen:

| Datei | Wofür |
|---|---|
| **makro-team.zip** | Die App selbst – kommt auf GitHub |
| **google-script.txt** | Das Speicher-Skript – kommt in die Google-Tabelle |
| **secrets.txt** | Passwort, Namen und Schlüssel – kommt in Streamlit (**nicht** auf GitHub hochladen) |

---

## Teil A – Google-Tabelle (Speicher fürs Team)

1. Gehe auf **sheets.google.com** und erstelle eine **leere Tabelle**. Nenne sie z. B. «Makro-Dashboard Daten».
2. Klicke im Menü oben auf **Erweiterungen → Apps Script**. Es öffnet sich ein neuer Tab mit einem Code-Fenster.
3. Lösche im Code-Fenster alles, was dort steht (Ctrl + A, dann Entf).
4. Öffne **google-script.txt** (Doppelklick), markiere alles (Ctrl + A), kopiere es (Ctrl + C) und füge es ins Code-Fenster ein (Ctrl + V).
5. Klicke auf das **Speichern-Symbol** (Diskette) oder drücke Ctrl + S.
6. Klicke oben rechts auf den blauen Button **Bereitstellen → Neue Bereitstellung**.
7. Klicke links neben «Typ auswählen» auf das **Zahnrad** und wähle **Web-App**.
8. Stelle ein:
   - **Ausführen als:** Ich (deine E-Mail)
   - **Zugriff:** **Jeder**
   
   Dann **Bereitstellen** klicken.
9. Google fragt nach der Berechtigung, weil du das Skript selbst geschrieben hast:
   - **Zugriff autorisieren** → dein Google-Konto wählen
   - Erscheint die Warnung «Google hat diese App nicht überprüft», klicke auf **Erweitert**, dann auf **Zu … wechseln (unsicher)**. Das ist dein eigenes Skript und es greift nur auf diese Tabelle zu.
   - **Zulassen**
10. Jetzt siehst du die **Web-App-URL**. Sie beginnt mit `https://script.google.com/macros/s/…` und endet auf `/exec`. Klicke auf **Kopieren**.
11. Öffne **secrets.txt** und ersetze `HIER-DIE-WEB-APP-URL-EINFUEGEN` durch diese URL. Die Anführungszeichen bleiben stehen. Dann speichern.

So sieht die Zeile danach aus:
```
script_url = "https://script.google.com/macros/s/AKfy…lange Zeichenfolge…/exec"
```

## Teil B – GitHub (Code hochladen)

1. Entpacke **makro-team.zip**: Rechtsklick → **Alle extrahieren**. Du bekommst einen Ordner «makro-team». Darin liegen direkt `app.py`, `requirements.txt`, der Ordner `makro` und so weiter.
2. Auf **github.com** oben rechts **+ → New repository**. Name: **makro-team**, **Public**, keine Häkchen setzen, dann **Create repository**.
3. Im blauen Kasten auf den Link **uploading an existing file** klicken.
4. Öffne den Ordner «makro-team», markiere **alles darin** (Ctrl + A) und ziehe es ins Browserfenster. Ziehe nicht den Ordner selbst hinein.
5. Warten, bis alle Dateien aufgelistet sind, dann **Commit changes**.
6. Kontrolle: Im Repository muss `app.py` auf der **obersten Ebene** stehen und nicht in einem Unterordner.

## Teil C – Streamlit (App online stellen)

1. Auf **share.streamlit.io** gehen und mit GitHub anmelden.
2. Die alte App kannst du löschen: rechts neben der alten App auf **⋮ → Delete**. Das ist freiwillig.
3. **Create app** → App aus einem GitHub-Repository erstellen.
4. Repository **makro-team**, Branch **main**, Main file path **app.py**.
5. **Advanced settings**:
   - Python version: **3.12**
   - **Secrets**: den ganzen Inhalt von **secrets.txt** hineinkopieren (mit deiner URL aus Teil A).
   - **Save**
6. **Deploy** klicken und 2–5 Minuten warten.
7. Passwort eingeben: **portfolioSIM26**. Wenn alles stimmt, steht links in der Seitenleiste **«Speicher: Google-Tabelle»**.
8. Den Link der App und das Passwort an Georg, Mathieu und Lorenzo schicken.

**Test:** Auf «Bewerten» bei einem Thema auf Speichern klicken. In deiner Google-Tabelle erscheint dann ein neues Blatt «szenario_votes» mit deinem Eintrag.

---

## Persönliche Logins

Statt eines gemeinsamen Team-Passworts kann jede Person ein eigenes Passwort haben. Dann wird jede Eingabe automatisch der angemeldeten Person zugeschrieben, und die Auswahl «Ich bin» fällt weg.

In Streamlit unter **Manage app → Settings → Secrets** ganz **am Ende** einfügen (Passwörter frei wählbar):

```toml
[benutzer]
Sophy = "eigenes-passwort"
Georg = "eigenes-passwort"
Mathieu = "eigenes-passwort"
Lorenzo = "eigenes-passwort"

[lesezugriff]
Dozent = "eigenes-passwort"
```

- Sobald `[benutzer]` vorhanden ist, gilt das Team-Passwort nicht mehr.
- `[lesezugriff]` ist optional: Diese Personen können alles ansehen, aber nichts speichern.
- Neue Person: einfach eine Zeile ergänzen. Passwort ändern: Zeile anpassen und speichern.

## Design

Farben und Schrift kommen aus der Datei `.streamlit/config.toml` **im obersten Ordner des Repositorys** (nicht im App-Ordner). Ohne diese Datei funktioniert die App trotzdem, nur mit den Standardfarben von Streamlit.

## Analyse, Berichte und Warnungen

- **Analyse** (Seite «Analyse»): Aktien mit Stückzahl oder Betrag und Treasuries je Laufzeitband eingeben → «Kennzahlen berechnen». Die App rechnet Sharpe, Sortino, Volatilität, Beta, Alpha, Max. Drawdown, Value at Risk, Tracking Error, Korrelationen und Risikobeiträge und prüft IC-Korridor, Klumpen (> 10 %), S&P-500-Mandat, Duration und Sektorempfehlung. Mit «Aktuelles Portfolio übernehmen» lässt sich ein geplanter Kauf vorher durchrechnen. Vorauswahlen können gespeichert werden.
- **Portfolio → Performance & Risiko**: Rendite seit Start gegenüber dem neutralen IC-Portfolio und dem S&P 500 sowie das Risiko der heutigen Positionen.
- **Berichte**: Wochenbericht und Abgabe-Dossier (PDF).
- **Angemeldet bleiben**: Beim Login ankreuzen – gilt 7 Tage auf diesem Browser. Ändert sich ein Passwort, muss man sich neu anmelden.
- **Signal-Warnung**: GitHub Actions prüft Mo–Fr alle 2 Stunden, ob ein neues rotes Signal dazugekommen ist, und schickt dann eine Mail (gleiche GitHub-Secrets wie die Morgenmail).

---

## Wenn etwas nicht klappt

| Meldung in der App | Lösung |
|---|---|
| 🔌 «Noch keine Google-Tabelle verbunden» | In den Secrets steht noch der Platzhalter statt deiner URL (Teil A, Schritt 11). |
| 🔌 «Das Skript antwortet nicht richtig» | In Apps Script bei **Zugriff** «Jeder» wählen (Teil A, Schritt 8). Prüfen, ob die URL auf `/exec` endet. |
| 🔌 «Skript meldet: Falscher Schlüssel» | Das Skript oder die Secrets wurden verändert: google-script.txt und secrets.txt nochmals unverändert einfügen. |
| Die App ist eingeschlafen | Passiert nach 12 Stunden ohne Nutzung: auf «Yes, get this app back up» klicken und etwa 1 Minute warten. Die Daten bleiben in der Tabelle. |

Secrets später ändern: in der App unten rechts **Manage app → ⋮ → Settings → Secrets**.

---

## Bedienung

**Logik in einem Satz:** Kapitel (Daten) → Szenarien (Base/Bull/Bear mit Zahlen und Wahrscheinlichkeiten) → Erwartungen (Dozentenfragen) und Taktik (Allokation pro Monat) → Anleihen-Tool und Entscheide. Alles, was ihr irgendwo eintragt, wirkt sofort im ganzen Dashboard.

- **Gesamtbild:** Hauptszenario, Fristen (IPS, Taktik, Erwartungen), Szenario-Werte, Erwartungen des Monats, Taktik, Signale, Kapitel-Kurzfazits, Risiken, Zyklus, Termine.
- **Kapitelseiten:** oben «Kurz zusammengefasst» (worum es geht, was die Zahlen sagen, welches Szenario sie stützen), dann Fazit, Kennzahlen, Charts, Bezug zu Szenarien und Risiken, Notizen und **Dokumente** (Analysen hochladen und öffnen). Auf «Wachstum & Zyklus» gebt ihr auch eure Zyklus-Einordnung ab.
- **Szenarien:** Base/Bull/Bear mit Monatswerten (S&P 500, Fed, 2 J., 10 J., CPI, WTI, VIX, Growth/Value, Sektoren …). Einzelschätzung jeder Person + Team-Beschluss (der gilt). «Datenlage» zeigt, welche Indizien heute erfüllt sind und ob die Daten ein anderes Szenario stützen.
- **Was ist eingepreist?** Markt gegen eure gewichtete Sicht, jede Zahl mit Quelle (FRED-Links, CME FedWatch als Team-Eintrag).
- **Risiken & Chancen:** jedes Risiko mit Richtung (Bear/Bull); ein ausgelöstes Warnsignal zählt in der Datenlage des Szenarios. Hier wird auch bewertet.
- **IPS:** alle Felder der Dozenten-App mit Prüfung (Bänder, Summen, leere Felder, Volatilität). Speist neutralen Anker, Bänder und Duration-Grenzen.
- **Erwartungen (Dozent):** die 10 Monatsfragen, Wahrscheinlichkeit jeder Antwort aus den Szenarien, Vorschlag für Antwort und Überzeugung, eure Antwort mit Begründung, Hauptszenario, Auflösung nach Monatsende.
- **Taktik:** Aktien/Anleihen/Cash und 11 Sektor-Zielgewichte wie in der Dozenten-App, plus Duration, TIPS, Begründung. Prüfung gegen IPS und Dozenten-Bandbreiten, Orientierung aus Szenarien, Signalen und Erwartungen.
- **Anleihen & Duration:** Zinskurve, Carry/Roll, Markt-Forward gegen eure Erwartung, Laufzeiten-Plan gegen Benchmark, DV01, Stresstest und Ideen für Mehrrendite.
- **PDF:** Oben auf den Seiten «PDF erstellen» → «PDF herunterladen» (Bericht im Dashboard-Design).
- **Risiko & Risikobudget:** Tracking Error der Taktik gegenüber der Benchmark 50/50 und wofür er gebraucht wird (Asset Allocation, Sektor-Wetten, Duration & Kurve), VaR, Expected Shortfall, Safety-First, Stresstests (eure Szenarien und historische Monate), Reverse-Stresstest, Prüfung gegen die Risikoziele im IPS.
- **CFA-Bausteine:** CME-Prozess und Verhaltensfehler (Szenarien → Tab «CFA-Check»), Konsistenz-Check der Szenarien, Taylor-Regel und Grinold-Kroner («Was ist eingepreist?»), Key-Rate-Durations und Zerlegung relativ zur Benchmark (Anleihen), Brier-Score und Kalibrierung (Erwartungen).
- **Zusammenarbeit:** Speichert jemand anderes IPS, Taktik, Erwartungen oder Szenarien, laden sich die Felder bei den anderen automatisch neu (Hinweis «Inzwischen neu gespeichert von …»). So überschreibt niemand versehentlich den Stand der anderen.
- **Daten eintragen:** Werte, die nicht automatisch kommen, immer mit 2 Quellen.

---

## Bitte im Team prüfen

- **Szenario-Werte:** Die Monatswerte von Base/Bull/Bear sind Startwerte (Richtung aus IC-Statement und Themen-Szenarien, Höhe Vorschlag Claude). Auf der Seite «Szenarien» anpassen.
- **Überzeugungs-Stufen:** In der App stehen tief 55 % / mittel 70 % / hoch 85 %. Sichtbar war nur «mittel (70 %)» – bitte mit der Dozenten-App abgleichen und in `makro/config.py` (UEBERZEUGUNG) korrigieren.
- **Anleihen-Benchmark:** Den genauen Treasury-Index der Dozenten-App kennen wir nicht. Unter «Einstellungen» wählbar (Standard: gesamter Treasury-Markt ≈ GOVT).
- **IPS-Startwerte:** 45/50/5 mit Bändern 40–50 / 45–55 / 0–10 aus dem IC-Statement, Rest wie in der Dozenten-App vorgeschlagen.
- **Schwellen «Vorschlag»:** bei den von Claude ergänzten Indikatoren in der App markiert.
