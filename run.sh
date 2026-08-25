#!/usr/bin/env bash
# Startet den Vokabeltrainer lokal auf http://localhost:8501
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}

if [ ! -d .venv ]; then
  echo "→ Lege virtuelle Umgebung an (.venv) ..."
  "$PY" -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
fi

# --server.address 0.0.0.0 macht das Dashboard auch im WLAN erreichbar
# (z.B. vom Handy aus: http://<IP-des-Rechners>:8501).
exec ./.venv/bin/streamlit run app.py \
  --server.address "${FRANZ_HOST:-0.0.0.0}" \
  --server.port "${FRANZ_PORT:-8501}"
