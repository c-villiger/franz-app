#!/usr/bin/env bash
# Startet den Vokabeltrainer lokal und zeigt vorher die Adresse fürs Handy.
set -euo pipefail
cd "$(dirname "$0")"

HOST=${FRANZ_HOST:-0.0.0.0}
PORT=${FRANZ_PORT:-8501}

# Python heisst je nach System anders. Unter Windows kommt eine Falle dazu:
# dort liegen "python3.exe" und "python.exe" als Platzhalter im PATH, die nur
# auf den Microsoft Store verweisen. Sie sind ausführbar, sind aber kein
# Python. Deshalb wird jeder Kandidat nicht bloss gesucht, sondern ausprobiert.
find_python() {
  local candidate
  for candidate in "${PYTHON:-}" "py -3" python3 python; do
    [ -n "$candidate" ] || continue
    # Absichtlich ohne Anführungszeichen: "py -3" ist Befehl plus Argument.
    # shellcheck disable=SC2086
    if $candidate -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
        >/dev/null 2>&1; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

if ! PY=$(find_python); then
  cat >&2 <<'FEHLER'

Kein funktionierendes Python 3.9 oder neuer gefunden.

Unter Windows meldet sich hier gern der Microsoft Store mit
"Python wurde nicht gefunden...". Das ist ein Platzhalter, kein Python.

Zum Prüfen, ob überhaupt eines installiert ist:   py -V

Falls nicht:
  * Python von https://www.python.org/downloads/ installieren
    und dabei "Add python.exe to PATH" ankreuzen.

Falls doch, aber der Platzhalter dazwischenfunkt:
  * Einstellungen > Apps > Erweiterte App-Einstellungen >
    App-Ausführungsaliase  ->  die Einträge für Python abschalten.

Liegt Python an einer bekannten Stelle, geht es auch direkt:
  PYTHON="C:/Users/DeinName/AppData/Local/Programs/Python/Python312/python.exe" ./run.sh

FEHLER
  exit 1
fi

if [ ! -d .venv ]; then
  echo "→ Lege virtuelle Umgebung an (.venv) mit: $PY"
  # shellcheck disable=SC2086
  $PY -m venv .venv
fi

# Unter Git Bash legt venv die Programme nach Scripts/ statt bin/.
if [ -d .venv/Scripts ]; then BIN=.venv/Scripts; else BIN=.venv/bin; fi

# Abhängigkeiten nachziehen, sobald sich requirements.txt geändert hat -
# sonst fehlen nach einem git pull neue Pakete.
STAMP=.venv/.requirements.txt
if ! cmp -s requirements.txt "$STAMP"; then
  echo "→ Installiere Abhängigkeiten ..."
  "$BIN/python" -m pip install --quiet --upgrade pip
  "$BIN/python" -m pip install --quiet -r requirements.txt
  cp requirements.txt "$STAMP"
fi

# Adresse + QR-Code fürs Handy. FRANZ_QR_INVERT=0 für helle Terminals.
QR_FLAG=""
if [ "${FRANZ_QR_INVERT:-1}" = "0" ]; then QR_FLAG="--no-invert"; fi
"$BIN/python" -m vocabtrainer.netinfo --port "$PORT" $QR_FLAG || true

# --server.address 0.0.0.0 macht das Dashboard im WLAN erreichbar.
exec "$BIN/streamlit" run app.py --server.address "$HOST" --server.port "$PORT"
