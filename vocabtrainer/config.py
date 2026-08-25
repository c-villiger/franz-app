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

# Sobald das Dashboard nicht mehr auf dem eigenen Rechner laeuft, taugt eine
# lokale Datei nicht mehr: gehostete Umgebungen haben ein fluechtiges
# Dateisystem. Ist eine URL gesetzt, liegt der Fortschritt stattdessen in
# einer gehosteten, SQLite-kompatiblen Datenbank (libSQL/Turso).
DB_URL = os.environ.get("FRANZ_DB_URL", "").strip()
DB_TOKEN = os.environ.get("FRANZ_DB_TOKEN", "").strip()


def vocab_readonly(url: str | None = None) -> bool:
    """Darf das Dashboard die Vokabeldatei selbst beschreiben?

    Lokal ja - die Datei liegt im Git-Checkout des Nutzers. Auf einem Server
    nicht: dort waere die Ergaenzung beim naechsten Neustart wieder weg, die
    zugehoerige Karte aber schon in der (dauerhaften) Datenbank gelandet und
    wuerde beim Abgleich als "aus der Datei entfernt" deaktiviert.

    Voreinstellung: schreibgeschuetzt, sobald eine gehostete Datenbank
    konfiguriert ist. Mit FRANZ_VOCAB_READONLY=0 laesst sich das aufheben.
    """
    raw = os.environ.get("FRANZ_VOCAB_READONLY")
    if raw is not None:
        return raw.strip().lower() not in ("", "0", "false", "nein", "no")
    return bool(DB_URL if url is None else url.strip())

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
