# 🇫🇷 Französisch-Vokabeltrainer

Ein kleines, lokal laufendes Dashboard zum Üben französischer Ausdrücke mit
deutschen Übersetzungen – im Stil von Anki, aber auf das Nötigste reduziert:
eine Karte, drei Knöpfe (**sicher · mittel · unsicher**) und ein Zähler oben.

Neue Vokabeln kommen als Zeile in `vocab/vocab.jsonl` – das kann Claude vom
Handy aus erledigen; der Fortschritt liegt getrennt davon in einer lokalen
SQLite-Datenbank und geht beim Hinzufügen nie verloren.

---

## Schnellstart (macOS, Linux, WSL, Git Bash)

```bash
git clone <dieses-repo>
cd franz-app
./run.sh
```

`run.sh` legt beim ersten Mal eine virtuelle Umgebung unter `.venv` an,
installiert dort die Abhängigkeiten und startet das Dashboard. Der Browser
öffnet sich von selbst; sonst <http://localhost:8501> aufrufen.

Bei jedem weiteren Start vergleicht das Skript `requirements.txt` mit dem
Stand in der `.venv` und installiert nur nach, wenn sich etwas geändert hat –
nach einem `git pull` mit neuen Paketen läuft es also ohne Zutun weiter.

## Ohne das Skript (z.B. Windows-PowerShell)

Der Umweg über eine virtuelle Umgebung lohnt sich auch von Hand: `pip install`
ohne aktives `.venv` schreibt Streamlit und rund dreissig Abhängigkeiten in die
globale Python-Installation – und auf aktuellem macOS (Homebrew) sowie
Debian/Ubuntu bricht pip dort ohnehin mit `externally-managed-environment` ab.

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Unter macOS/Linux dasselbe mit `python3 -m venv .venv` und
`source .venv/bin/activate`. Statt zu aktivieren geht auch direkt
`.venv/bin/streamlit run app.py` (Windows: `.venv\Scripts\streamlit run app.py`) –
so macht es `run.sh`.

## Vom Handy im gleichen WLAN

`run.sh` zeigt beim Start alle Adressen und einen QR-Code – Handykamera drauf,
fertig, ohne IP abzutippen:

```
  Auf diesem Rechner     http://localhost:8501
  Im WLAN (fürs Handy)   http://192.168.1.42:8501
  Oder per Name          http://macbook.local:8501

  Mit der Handykamera scannen:
  █████████████████████████████████
  ████ ▄▄▄▄▄ █▄▄▄▀▀███▄█ ▄▄▄▄▄ ████
  ...
```

Wichtig: `localhost` zeigt auf dem Handy auf das Handy selbst. Es braucht die
WLAN-Adresse – die funktioniert, weil `run.sh` an `0.0.0.0` bindet. Von Hand
gestartet dafür `streamlit run app.py --server.address 0.0.0.0` verwenden.

Ein QR-Code im Terminal besteht aus Blockzeichen, seine Farben hängen also am
Hintergrund des Terminals. Voreingestellt ist die Variante für **dunkle**
Terminals. Erkennt die Kamera nichts, ist er für dein Terminal invertiert:

```bash
FRANZ_QR_INVERT=0 ./run.sh
```

Die Adressen und den Code gibt es auch ohne Neustart:

```bash
.venv/bin/python -m vocabtrainer.netinfo
```

Die IP kann sich ändern, wenn der Router neu vergibt. `http://<name>.local:8501`
bleibt dagegen stabil (den Namen liefert `scutil --get LocalHostName` auf macOS,
`hostname` sonst) – vom iPhone aus zuverlässig, bei Android je nach Version.
Dauerhaft am robustesten ist eine feste IP per DHCP-Reservierung im Router.

---

## Bedienung

| Element | Bedeutung |
|---|---|
| Zähler oben | Wie viele Vokabeln sind 🟢 sicher, 🟡 mittel, 🔴 unsicher, ⚪️ noch nicht gefragt |
| Karte | Zeigt je nach Richtung den französischen **oder** den deutschen Ausdruck |
| 👁️ Antwort zeigen | Blendet die Übersetzung ein (Tastenkürzel: `Leertaste`) |
| 🟢 / 🟡 / 🔴 | Bewertung speichern und nächste Karte ziehen (Kürzel `1` / `2` / `3`) |
| ⏭️ Überspringen | Nächste Karte, ohne zu bewerten |
| 🔁 Richtung drehen | Diese Karte in der anderen Richtung ansehen |
| Seitenleiste | Abfragerichtung, Tag-Filter, Vokabeln hinzufügen, `git pull`, Zurücksetzen |

Eine Bewertung lässt sich jederzeit überschreiben – gespeichert wird immer die
letzte, und jede einzelne Bewertung landet zusätzlich in einer Historie.

---

## Vokabeln hinzufügen

### Vom Handy aus, per Claude (der eigentliche Zweck)

Claude auf dem Handy öffnen, dieses Repo als Kontext wählen und schreiben:

> Füge diese Vokabeln hinzu: *tomber dans les pommes*, *poser un lapin*

Claude hängt die Zeilen an `vocab/vocab.jsonl` an und pusht. Auf dem Rechner
dann in der Seitenleiste **„git pull“** drücken (oder selbst `git pull`) – die
neuen Wörter sind sofort in der Abfrage, der bisherige Fortschritt bleibt.

Die genaue Anleitung dafür steht in [`CLAUDE.md`](CLAUDE.md).

### In der Seitenleiste des Dashboards

Eine Vokabel pro Zeile, Trennzeichen `|`, `=` oder `->`:

```
avoir le cafard | Trübsal blasen | idiom
poser un lapin  | jemanden versetzen
```

### Auf der Kommandozeile

```bash
python3 -m vocabtrainer.cli add "jeter l'éponge | das Handtuch werfen | idiom"

python3 -m vocabtrainer.cli add --tag alltag - <<'EOF'
Ça te dit ?      | Hast du Lust?
Je n'en peux plus | Ich kann nicht mehr
EOF

python3 -m vocabtrainer.cli check           # Datei validieren
python3 -m vocabtrainer.cli sync            # Datenbank abgleichen
python3 -m vocabtrainer.cli stats           # Zähler anzeigen
python3 -m vocabtrainer.cli list --label unsicher
```

---

## Wie die Auswahl funktioniert

Die nächste Karte wird **zufällig gezogen, aber gewichtet** – ähnlich wie bei
Anki, nur ohne Intervall-Rechnerei:

| Zustand | Grundgewicht | kommt … |
|---|---|---|
| ⚪️ noch nicht gefragt | 24 | am häufigsten |
| 🔴 unsicher | 12 | oft |
| 🟡 mittel | 5 | gelegentlich |
| 🟢 sicher | 1 | selten – aber nie „nie“ |

Zwei Zusätze:

* **Abklingzeit** – gerade bewertete Karten werden kurz abgewertet (unsicher 3 h,
  mittel 12 h, sicher 48 h), maximal aber auf 15 % ihres Gewichts. Auch eine
  kleine Liste bleibt dadurch spielbar.
* **Keine Wiederholung** – die zuletzt gezeigte Karte kommt nie direkt noch
  einmal; die davor gezeigten sind stark abgewertet.

Nachzulesen und anzupassen in [`vocabtrainer/scheduler.py`](vocabtrainer/scheduler.py).

---

## Aufbau

```
app.py                     Streamlit-Dashboard
run.sh                     Startskript (venv + streamlit run)
vocab/vocab.jsonl          Vokabelliste – Quelle der Wahrheit, versioniert
data/vocab.db              Fortschritt (SQLite) – lokal, nicht versioniert
vocabtrainer/
  config.py                Pfade und Konstanten
  vocab_file.py            Vokabeldatei lesen/schreiben, Duplikat-Erkennung
  db.py                    Schema, Abgleich Datei→DB, Labels, Statistik
  scheduler.py             gewichtete Zufallsauswahl
  netinfo.py               WLAN-Adresse ermitteln, QR-Code fürs Handy
  cli.py                   Kommandozeile (add / check / sync / stats / list)
tests/                     Tests (unittest, ohne Zusatzabhängigkeiten)
```

**Trennung von Wörtern und Fortschritt:** die Vokabelliste liegt im Repo und
wird geteilt, der Lernstand bleibt auf dem jeweiligen Gerät. Beim Start
gleicht das Dashboard beides ab: neue Zeilen werden angelegt, korrigierte
Übersetzungen übernommen (Label bleibt erhalten), gelöschte Zeilen nur
deaktiviert – so überlebt die Historie auch ein versehentliches Löschen.

Format von `vocab/vocab.jsonl` – eine JSON-Zeile pro Vokabel:

```json
{"fr": "avoir le cafard", "de": "Trübsal blasen", "tags": ["idiom"]}
```

Pflicht sind `fr` und `de`; `tags` und `note` sind optional.

---

## Tests

```bash
python3 -m unittest discover -s tests -v
```
