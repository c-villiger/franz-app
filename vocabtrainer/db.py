"""SQLite-Datenbank mit dem Lernfortschritt.

Aufgabenteilung:
  * ``vocab/vocab.jsonl`` = Quelle der Wahrheit fuer *welche* Vokabeln es gibt.
  * ``data/vocab.db``     = Quelle der Wahrheit fuer *wie gut* sie sitzen.

``sync_from_file`` gleicht beides ab: neue Zeilen werden angelegt, geaenderte
Texte aktualisiert, aus der Datei entfernte Karten deaktiviert (nicht
geloescht - so bleibt die Historie erhalten, falls sie zurueckkommen).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from .config import DB_PATH, LABELS
from .vocab_file import Entry, load_entries

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    key             TEXT    NOT NULL UNIQUE,
    fr              TEXT    NOT NULL,
    de              TEXT    NOT NULL,
    tags            TEXT    NOT NULL DEFAULT '',
    note            TEXT    NOT NULL DEFAULT '',
    label           TEXT,                              -- NULL = noch nicht gefragt
    times_asked     INTEGER NOT NULL DEFAULT 0,
    last_asked_at   TEXT,
    last_labeled_at TEXT,
    created_at      TEXT    NOT NULL,
    active          INTEGER NOT NULL DEFAULT 1,
    CHECK (label IS NULL OR label IN ('sicher', 'mittel', 'unsicher'))
);

CREATE TABLE IF NOT EXISTS reviews (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id    INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    label      TEXT    NOT NULL,
    direction  TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reviews_card ON reviews (card_id);
CREATE INDEX IF NOT EXISTS idx_cards_active ON cards (active, label);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:  # pragma: no cover - alte/kaputte Werte
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Card:
    id: int
    key: str
    fr: str
    de: str
    tags: tuple[str, ...]
    note: str
    label: str | None
    times_asked: int
    last_asked_at: str | None
    last_labeled_at: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Card":
        raw_tags = row["tags"] or ""
        return cls(
            id=row["id"],
            key=row["key"],
            fr=row["fr"],
            de=row["de"],
            tags=tuple(t for t in raw_tags.split(",") if t),
            note=row["note"] or "",
            label=row["label"],
            times_asked=row["times_asked"],
            last_asked_at=row["last_asked_at"],
            last_labeled_at=row["last_labeled_at"],
        )

    def prompt(self, direction: str) -> str:
        return self.fr if direction == "fr2de" else self.de

    def answer(self, direction: str) -> str:
        return self.de if direction == "fr2de" else self.fr


@dataclass
class SyncResult:
    added: int = 0
    updated: int = 0
    deactivated: int = 0
    reactivated: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.deactivated or self.reactivated)

    def summary(self) -> str:
        if not self.changed:
            return "Keine Änderungen – Datenbank ist aktuell."
        bits = []
        if self.added:
            bits.append(f"{self.added} neu")
        if self.updated:
            bits.append(f"{self.updated} aktualisiert")
        if self.reactivated:
            bits.append(f"{self.reactivated} reaktiviert")
        if self.deactivated:
            bits.append(f"{self.deactivated} entfernt")
        return ", ".join(bits)


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def sync_from_file(
    conn: sqlite3.Connection, entries: Iterable[Entry] | None = None
) -> SyncResult:
    """Gleicht die Datenbank mit der Vokabeldatei ab."""
    entry_list = list(entries) if entries is not None else load_entries()
    result = SyncResult()
    existing = {
        row["key"]: row
        for row in conn.execute("SELECT id, key, fr, de, tags, note, active FROM cards")
    }
    seen: set[str] = set()

    for entry in entry_list:
        seen.add(entry.key)
        tags = ",".join(entry.tags)
        row = existing.get(entry.key)
        if row is None:
            conn.execute(
                "INSERT INTO cards (key, fr, de, tags, note, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (entry.key, entry.fr, entry.de, tags, entry.note, now_iso()),
            )
            result.added += 1
            continue
        text_changed = (
            row["fr"] != entry.fr
            or row["de"] != entry.de
            or (row["tags"] or "") != tags
            or (row["note"] or "") != entry.note
        )
        if text_changed:
            conn.execute(
                "UPDATE cards SET fr = ?, de = ?, tags = ?, note = ? WHERE id = ?",
                (entry.fr, entry.de, tags, entry.note, row["id"]),
            )
            result.updated += 1
        if not row["active"]:
            conn.execute("UPDATE cards SET active = 1 WHERE id = ?", (row["id"],))
            result.reactivated += 1

    stale = [row["id"] for key, row in existing.items() if key not in seen and row["active"]]
    for card_id in stale:
        conn.execute("UPDATE cards SET active = 0 WHERE id = ?", (card_id,))
        result.deactivated += 1

    conn.commit()
    return result


def all_cards(
    conn: sqlite3.Connection,
    *,
    tags: Sequence[str] | None = None,
    include_inactive: bool = False,
) -> list[Card]:
    sql = "SELECT * FROM cards"
    if not include_inactive:
        sql += " WHERE active = 1"
    cards = [Card.from_row(row) for row in conn.execute(sql)]
    if tags:
        wanted = {t.lower() for t in tags}
        cards = [c for c in cards if wanted & {t.lower() for t in c.tags}]
    return cards


def get_card(conn: sqlite3.Connection, card_id: int) -> Card | None:
    row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    return Card.from_row(row) if row else None


def all_tags(conn: sqlite3.Connection) -> list[str]:
    tags: set[str] = set()
    for row in conn.execute("SELECT tags FROM cards WHERE active = 1"):
        tags.update(t for t in (row["tags"] or "").split(",") if t)
    return sorted(tags)


def set_label(
    conn: sqlite3.Connection, card_id: int, label: str, direction: str = ""
) -> None:
    """Speichert eine Bewertung und protokolliert sie in ``reviews``."""
    if label not in LABELS:
        raise ValueError(f"Unbekanntes Label: {label!r} (erlaubt: {LABELS})")
    stamp = now_iso()
    conn.execute(
        "UPDATE cards SET label = ?, last_labeled_at = ?, last_asked_at = ?,"
        " times_asked = times_asked + 1 WHERE id = ?",
        (label, stamp, stamp, card_id),
    )
    conn.execute(
        "INSERT INTO reviews (card_id, label, direction, created_at) VALUES (?, ?, ?, ?)",
        (card_id, label, direction, stamp),
    )
    conn.commit()


def mark_skipped(conn: sqlite3.Connection, card_id: int) -> None:
    """Karte wurde uebersprungen: nur den Zeitstempel setzen, kein Label."""
    conn.execute("UPDATE cards SET last_asked_at = ? WHERE id = ?", (now_iso(), card_id))
    conn.commit()


def stats(conn: sqlite3.Connection, cards: Sequence[Card] | None = None) -> dict[str, int]:
    """Zaehler fuer die Kopfzeile des Dashboards."""
    cards = all_cards(conn) if cards is None else cards
    counts = {label: 0 for label in LABELS}
    counts["unseen"] = 0
    for card in cards:
        if card.label is None:
            counts["unseen"] += 1
        else:
            counts[card.label] += 1
    counts["total"] = len(cards)
    return counts


def reviews_today(conn: sqlite3.Connection) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM reviews WHERE created_at >= ?", (today,)
    ).fetchone()
    return int(row["n"])


def recent_reviews(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT r.created_at, r.label, r.direction, c.fr, c.de"
            " FROM reviews r JOIN cards c ON c.id = r.card_id"
            " ORDER BY r.id DESC LIMIT ?",
            (limit,),
        )
    )


def reset_progress(conn: sqlite3.Connection) -> None:
    """Setzt alle Labels zurueck - die Vokabeln selbst bleiben erhalten."""
    conn.execute(
        "UPDATE cards SET label = NULL, times_asked = 0,"
        " last_asked_at = NULL, last_labeled_at = NULL"
    )
    conn.execute("DELETE FROM reviews")
    conn.commit()
