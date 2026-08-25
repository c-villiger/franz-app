#!/usr/bin/env bash
# Startet den Vokabeltrainer lokal und zeigt vorher die Adresse fürs Handy.
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
HOST=${FRANZ_HOST:-0.0.0.0}
PORT=${FRANZ_PORT:-8501}

if [ ! -d .venv ]; then
  echo "→ Lege virtuelle Umgebung an (.venv) ..."
  "$PY" -m venv .venv
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
