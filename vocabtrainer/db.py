"""Datenbank mit dem Lernfortschritt.

Aufgabenteilung:
  * ``vocab/vocab.jsonl`` = Quelle der Wahrheit fuer *welche* Vokabeln es gibt.
  * die Datenbank         = Quelle der Wahrheit fuer *wie gut* sie sitzen.

``sync_from_file`` gleicht beides ab: neue Zeilen werden angelegt, geaenderte
Texte aktualisiert, aus der Datei entfernte Karten deaktiviert (nicht
geloescht - so bleibt die Historie erhalten, falls sie zurueckkommen).

Zwei Ablagen, gleiches SQL:
  * **lokal** ``data/vocab.db`` ueber das ``sqlite3`` der Standardbibliothek -
    braucht kein Internet und keine Zusatzpakete.
  * **gehostet** eine libSQL-/Turso-Datenbank, sobald ``FRANZ_DB_URL`` gesetzt
    ist - noetig, wenn das Dashboard auf einem Server liegt, dessen
    Dateisystem beim Neustart geleert wird.

Der libSQL-Client liefert Zeilen als nackte Tupel und kennt keine
``row_factory``. Damit der restliche Code nichts davon merkt, legt
``_LibsqlConnection`` eine duenne Schicht darueber, die Zeilen wie
``sqlite3.Row`` per Spaltenname zugaenglich macht.
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from .config import DB_PATH, DB_TOKEN, DB_URL, LABELS

# Ausgeschrieben statt "SELECT *": nur so lassen sich die Spaltennamen im
# Notfall aus der Abfrage selbst ableiten (siehe _columns_from_sql).
CARD_COLUMNS = (
    "id, key, fr, de, tags, note, label, times_asked,"
    " last_asked_at, last_labeled_at, created_at, active"
)
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

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT NOT NULL PRIMARY KEY,
    value TEXT NOT NULL
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
    def from_row(cls, row) -> "Card":
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


class _NamedRow:
    """Zeile mit Zugriff ueber den Spaltennamen - wie ``sqlite3.Row``."""

    __slots__ = ("_values", "_columns")

    def __init__(self, values: Sequence, columns: dict[str, int]) -> None:
        self._values = values
        self._columns = columns

    def __getitem__(self, key: str | int):
        if isinstance(key, str):
            try:
                # Kleingeschrieben nachschlagen: die Schreibweise, die der
                # Treiber meldet, ist nicht verlaesslich (siehe oben).
                return self._values[self._columns[key.lower()]]
            except KeyError:
                bekannt = ", ".join(self._columns) or "keine"
                raise KeyError(
                    f"Unbekannte Spalte: {key!r} (vorhanden: {bekannt})"
                ) from None
        return self._values[key]

    def keys(self) -> list[str]:
        return list(self._columns)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:  # pragma: no cover - nur fuer Fehlersuche
        return f"_NamedRow({dict(zip(self._columns, self._values))!r})"


def _columns_from_description(description) -> dict[str, int]:
    """Spaltennamen aus den Metadaten des Cursors.

    Treiber sind sich uneinig, was in ``description`` steht: meist Tupel nach
    DB-API, manchmal blosse Strings, und Namen koennen als ``tabelle.spalte``
    kommen. Alles drei wird hier auf den nackten Spaltennamen gebracht.

    Auch die Schreibweise ist nicht verlaesslich: die gehostete Datenbank gab
    ``key`` als ``KEY`` zurueck, weil es ein SQL-Schluesselwort ist. Deshalb
    wird durchgehend kleingeschrieben und spaeter genauso nachgeschlagen.
    """
    columns: dict[str, int] = {}
    for position, entry in enumerate(description or ()):
        name = entry if isinstance(entry, str) else (entry[0] if entry else "")
        if not name:
            return {}
        columns[str(name).split(".")[-1].strip('"').lower()] = position
    return columns


_SELECT = re.compile(r"^\s*SELECT\s+(.*?)\s+FROM\s", re.IGNORECASE | re.DOTALL)


def _columns_from_sql(sql: str) -> dict[str, int]:
    """Spaltennamen notfalls aus dem SELECT selbst ableiten.

    Die gehostete Verbindung lieferte keine brauchbare ``description``, wohl
    aber die Zeilen - ohne diesen Notnagel waere jeder Zugriff ueber den
    Spaltennamen ins Leere gelaufen. Deshalb steht in diesem Modul auch kein
    ``SELECT *`` mehr: nur bei ausgeschriebenen Spalten ist das hier verlaesslich.
    """
    match = _SELECT.match(sql)
    if not match:
        return {}
    columns: dict[str, int] = {}
    for position, part in enumerate(match.group(1).split(",")):
        part = part.strip()
        # Ein Komma innerhalb von Klammern - COALESCE(a, b) - wuerde die
        # Aufteilung zerlegen. Kommt hier nicht vor; falls doch, lieber nichts
        # zurueckgeben als etwas Falsches.
        if part.count("(") != part.count(")"):
            return {}
        alias = re.split(r"\s+AS\s+", part, flags=re.IGNORECASE)
        name = alias[-1].split(".")[-1].strip().strip('"')
        if not name or name == "*":
            return {}
        columns[name.lower()] = position
    return columns


class _LibsqlCursor:
    """Cursor, dessen Zeilen sich wie ``sqlite3.Row`` verhalten."""

    def __init__(self, cursor, sql: str = "") -> None:
        self._cursor = cursor
        self._sql = sql
        self._known: dict[str, int] | None = None

    def _columns(self) -> dict[str, int]:
        # Erst nach dem Holen der Zeilen abfragen: manche Treiber fuellen
        # description erst dann. Ergebnis merken, damit es pro Cursor einmal
        # ermittelt wird.
        if self._known is None:
            self._known = _columns_from_description(
                getattr(self._cursor, "description", None)
            ) or _columns_from_sql(self._sql)
        return self._known

    def _wrap(self, values):
        return None if values is None else _NamedRow(values, self._columns())

    def fetchone(self):
        return self._wrap(self._cursor.fetchone())

    def fetchall(self) -> list[_NamedRow]:
        return [self._wrap(values) for values in self._cursor.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


class _LibsqlConnection:
    """``sqlite3``-aehnliche Fassade um eine libSQL-Verbindung."""

    def __init__(self, conn) -> None:
        self._conn = conn

    def execute(self, sql: str, parameters: Sequence = ()) -> _LibsqlCursor:
        return _LibsqlCursor(self._conn.execute(sql, tuple(parameters)), sql)

    def executescript(self, script: str) -> None:
        self._conn.executescript(script)

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _connect_libsql(target: str, token: str = "") -> _LibsqlConnection:
    try:
        import libsql
    except ImportError as exc:  # pragma: no cover - fehlendes Paket
        raise RuntimeError(
            "Für eine gehostete Datenbank fehlt das Paket 'libsql'. "
            "Installieren mit: pip install -r requirements.txt"
        ) from exc
    return _LibsqlConnection(libsql.connect(target, auth_token=token))


def _prepared_path(db_path: Path | str | None) -> Path:
    """Pfad der lokalen Datenbank, Verzeichnis angelegt."""
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect_sqlite(db_path: Path | str | None) -> sqlite3.Connection:
    path = _prepared_path(db_path)
    # check_same_thread=False, weil Streamlit das Skript in wechselnden
    # Threads ausfuehrt, die Verbindung aber zwischengespeichert wird.
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def connect(
    db_path: Path | str | None = None,
    *,
    url: str | None = None,
    token: str | None = None,
    backend: str | None = None,
):
    """Verbindung aufbauen und das Schema sicherstellen.

    Ohne Argumente entscheidet die Umgebung: ist ``FRANZ_DB_URL`` gesetzt,
    geht es zur gehosteten Datenbank, sonst in die lokale Datei.

    ``backend='libsql'`` (auch per ``FRANZ_DB_BACKEND``) erzwingt den
    libSQL-Client fuer eine lokale Datei - so laesst sich diese Schicht ohne
    Turso-Konto und ohne Netzzugang ausprobieren.
    """
    target = (DB_URL if url is None else url).strip()
    if backend is None:
        backend = os.environ.get("FRANZ_DB_BACKEND", "").strip() or None
    if backend is None:
        backend = "libsql" if target else "sqlite3"

    if backend == "libsql" and target:
        conn = _connect_libsql(target, DB_TOKEN if token is None else token)
        conn.execute("PRAGMA foreign_keys = ON")
    elif backend == "libsql":
        # Lokale Datei über den libSQL-Client (nur zum Ausprobieren des
        # Adapters). Ohne Token, und das Verzeichnis muss existieren -
        # anders als sqlite3 legt libSQL es nicht selbst an.
        conn = _connect_libsql(str(_prepared_path(db_path)))
        conn.execute("PRAGMA foreign_keys = ON")
    else:
        conn = _connect_sqlite(db_path)

    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def sync_from_file(
    conn, entries: Iterable[Entry] | None = None
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
    conn,
    *,
    tags: Sequence[str] | None = None,
    include_inactive: bool = False,
) -> list[Card]:
    sql = f"SELECT {CARD_COLUMNS} FROM cards"
    if not include_inactive:
        sql += " WHERE active = 1"
    cards = [Card.from_row(row) for row in conn.execute(sql)]
    if tags:
        wanted = {t.lower() for t in tags}
        cards = [c for c in cards if wanted & {t.lower() for t in c.tags}]
    return cards


def get_card(conn, card_id: int) -> Card | None:
    row = conn.execute(
        f"SELECT {CARD_COLUMNS} FROM cards WHERE id = ?", (card_id,)
    ).fetchone()
    return Card.from_row(row) if row else None


def all_tags(conn) -> list[str]:
    tags: set[str] = set()
    for row in conn.execute("SELECT tags FROM cards WHERE active = 1"):
        tags.update(t for t in (row["tags"] or "").split(",") if t)
    return sorted(tags)


def set_label(
    conn, card_id: int, label: str, direction: str = ""
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


def mark_skipped(conn, card_id: int) -> None:
    """Karte wurde uebersprungen: nur den Zeitstempel setzen, kein Label."""
    conn.execute("UPDATE cards SET last_asked_at = ? WHERE id = ?", (now_iso(), card_id))
    conn.commit()


def stats(conn, cards: Sequence[Card] | None = None) -> dict[str, int]:
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


def reviews_today(conn) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM reviews WHERE created_at >= ?", (today,)
    ).fetchone()
    return int(row["n"])


def recent_reviews(conn, limit: int = 20) -> list:
    return list(
        conn.execute(
            "SELECT r.created_at, r.label, r.direction, c.fr, c.de"
            " FROM reviews r JOIN cards c ON c.id = r.card_id"
            " ORDER BY r.id DESC LIMIT ?",
            (limit,),
        )
    )


def get_setting(conn, key: str, default: str | None = None) -> str | None:
    """Eine gespeicherte Einstellung lesen.

    Einstellungen liegen in derselben Datenbank wie der Fortschritt - damit
    ueberleben sie einen Neustart und gelten gehostet auf allen Geraeten.
    """
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def delete_setting(conn, key: str) -> None:
    conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    conn.commit()


def reset_progress(conn) -> None:
    """Setzt alle Labels zurueck - die Vokabeln selbst bleiben erhalten."""
    conn.execute(
        "UPDATE cards SET label = NULL, times_asked = 0,"
        " last_asked_at = NULL, last_labeled_at = NULL"
    )
    conn.execute("DELETE FROM reviews")
    conn.commit()
