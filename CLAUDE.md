# Hinweise für Claude

Dieses Repo ist ein Französisch-Vokabeltrainer. Der häufigste Auftrag hier ist:
**neue Vokabeln hinzufügen** – oft vom Handy aus, unterwegs.

## Vokabeln hinzufügen

Die Vokabelliste ist `vocab/vocab.jsonl`: eine JSON-Zeile pro Eintrag,
UTF-8, Akzente ausgeschrieben (kein `é`).

```json
{"fr": "avoir le cafard", "de": "Trübsal blasen", "tags": ["idiom"]}
```

Vorgehen:

1. Neue Zeilen **ans Ende anhängen**, bestehende Zeilen nicht umsortieren.
   Am einfachsten über die CLI, die Duplikate erkennt und die Datei validiert:

   ```bash
   python3 -m vocabtrainer.cli add --no-sync - <<'EOF'
   tomber dans les pommes | in Ohnmacht fallen | idiom
   poser un lapin à quelqu'un | jemanden versetzen | idiom
   EOF
   ```

   `--no-sync` überspringt den Datenbank-Abgleich – in einer Web-Session gibt
   es die lokale Datenbank des Nutzers ohnehin nicht.

2. Danach prüfen und committen:

   ```bash
   python3 -m vocabtrainer.cli check
   git add vocab/vocab.jsonl && git commit -m "Vokabeln: +N Ausdrücke"
   git push -u origin <branch>
   ```

3. Kurz zurückmelden, welche Einträge dazugekommen sind (und welche als
   Duplikat übersprungen wurden).

### Regeln für die Einträge

* `fr` = französischer Ausdruck, `de` = deutsche Übersetzung. Nie vertauschen.
* Infinitiv bzw. die idiomatische Grundform verwenden
  (`avoir le cafard`, nicht `j'ai le cafard`).
* Mehrere Bedeutungen mit Komma in **ein** Feld:
  `"de": "zurückstecken, Zugeständnisse machen"`.
* Falsche Freunde mit Klarstellung: `"la veste", "die Jacke (nicht: die Weste)"`.
* Tags aus dem bestehenden Bestand wiederverwenden – aktuell:
  `idiom`, `alltag`, `verb`, `connecteur`, `faux-ami`, `familier`.
  Passendes Tag wählen, notfalls ein neues, aber sparsam.
* Vor dem Anhängen kurz prüfen, ob es den Ausdruck schon gibt
  (`grep -i "suchbegriff" vocab/vocab.jsonl`). Die CLI filtert Duplikate
  zusätzlich selbst heraus (Vergleich ohne Gross-/Kleinschreibung und Akzente).

### Was nicht passieren darf

* `data/vocab.db` **nicht** anfassen oder committen – das ist der lokale
  Lernfortschritt des Nutzers und bewusst gitignored.
* Zeilen in `vocab/vocab.jsonl` nicht löschen oder neu formatieren, ausser der
  Nutzer bittet ausdrücklich darum. Der Schlüssel einer Karte ergibt sich aus
  `fr` + `de`; wird eine Zeile umgeschrieben, gilt sie als neue Karte und der
  Lernstand der alten wird deaktiviert. Reine Tippfehler-Korrekturen an einer
  Übersetzung sind trotzdem in Ordnung – sie sind selten und der Verlust
  betrifft nur diese eine Karte.

## Aufbau (Kurzfassung)

| Datei | Zweck |
|---|---|
| `app.py` | Streamlit-Dashboard |
| `vocabtrainer/vocab_file.py` | Vokabeldatei lesen/schreiben, Duplikate |
| `vocabtrainer/db.py` | SQLite: Abgleich, Labels, Statistik |
| `vocabtrainer/scheduler.py` | gewichtete Zufallsauswahl der nächsten Karte |
| `vocabtrainer/netinfo.py` | WLAN-Adresse und QR-Code für den Zugriff vom Handy |
| `vocabtrainer/cli.py` | Kommandozeile |
| `tests/` | `python3 -m unittest discover -s tests` |

Nach Änderungen am Python-Code die Tests laufen lassen. Für reine
Vokabel-Ergänzungen genügt `python3 -m vocabtrainer.cli check`.
