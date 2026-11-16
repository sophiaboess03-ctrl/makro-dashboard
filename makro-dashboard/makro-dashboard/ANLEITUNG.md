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

- **Gesamtbild:** Übersicht über Zyklus, Ampeln, Signale, Allokation, Szenarien, Risiken und Termine.
- **Kapitelseiten:** Kennzahlen, Charts, Annahmen, Aktionen und Notizen je Kapitel der Makroanalyse.
- **Daten eintragen:** Werte, die nicht automatisch kommen (ISM, Conference Board, NFIB, BofA-Umfrage, Forward-KGV, Forward-EPS, DXY), immer mit 2 Quellen. Die Tabelle «Fällige Updates» zeigt, was veraltet ist.
- **Bewerten:** Jede Person schätzt Szenarien, Risiken und den Zyklus. Die App mittelt automatisch.
- **Allokation:** Empfehlung für Aktienquote, Duration, TIPS-Anteil und Sektoren, mit Herleitung jeder Zahl.
- **Einstellungen:** Neutrale Aktienquote per Regler.

FRED-Daten (BIP, Arbeitsmarkt, Inflation, Zinsen, Spreads, Öl usw.) werden automatisch alle 6 Stunden geladen. Für alle Werte gilt: Der Eintrag mit dem **neusten Datum** zählt.

## Bitte im Team prüfen

- **Schwellen «Vorschlag»:** Die Schwellen bei den Indikatoren, die Claude ergänzt hat, sind in der App als «Vorschlag» markiert.
- **Grösse der Allokations-Signale:** Wo die Analyse nur eine Richtung nennt, steht auf der Seite «Allokation» «Grösse: Vorschlag».
- **Startwerte «Gewinne & Bewertung»:** Für diese Szenarien gilt 25/50/25 %, bis ihr selbst bewertet.

Inhalte wie Schwellen und Szenarien stehen in `makro/config.py`, die Allokationsregeln in `makro/logic.py`. Für Änderungen frag am einfachsten Claude.
