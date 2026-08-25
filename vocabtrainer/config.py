"""Zentrale Pfade und Konstanten des Vokabeltrainers."""

from __future__ import annotations

import os
from pathlib import Path

# Projektwurzel = Ordner, der dieses Paket enthaelt.
ROOT = Path(__file__).resolve().parent.parent

# Die Vokabelliste im Repo ist die *Quelle der Wahrheit*.
# Sie wird versioniert, damit Woerter von ueberall (z.B. per Claude vom Handy)
# ergaenzt werden koennen.
VOCAB_FILE = Path(os.environ.get("FRANZ_VOCAB_FILE", ROOT / "vocab" / "vocab.jsonl"))

# Die lokale Datenbank haelt den Lernfortschritt (Labels, Statistik, Historie).
# Sie wird NICHT versioniert (siehe .gitignore).
DB_PATH = Path(os.environ.get("FRANZ_DB_PATH", ROOT / "data" / "vocab.db"))

# Die drei Bewertungen. `None` in der Datenbank bedeutet "noch nicht gefragt".
LABELS = ("sicher", "mittel", "unsicher")

LABEL_EMOJI = {
    "sicher": "🟢",
    "mittel": "🟡",
    "unsicher": "🔴",
    None: "⚪️",
}

DIRECTION_LABELS = {
    "fr2de": "🇫🇷 Französisch → 🇩🇪 Deutsch",
    "de2fr": "🇩🇪 Deutsch → 🇫🇷 Französisch",
    "mixed": "🔀 Gemischt",
}
