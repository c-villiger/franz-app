"""Lesen und Schreiben der Vokabelliste (`vocab/vocab.jsonl`).

Format: eine JSON-Zeile pro Eintrag, z.B.

    {"fr": "avoir le cafard", "de": "Trübsal blasen", "tags": ["idiom"]}

Warum JSONL?
  * Anhaengen ist trivial (eine Zeile ans Ende) -> gut fuer Claude/Handy.
  * Keine Kommas/Klammern, die beim Anhaengen kaputtgehen koennen.
  * Kommata und Anfuehrungszeichen im Text sind sauber escaped.
  * Git-Diffs bleiben zeilenweise und mergen konfliktarm.

Pflichtfelder: ``fr`` und ``de``. Optional: ``tags`` (Liste), ``note`` (Text),
``id`` (stabiler Schluessel, falls ein Eintrag spaeter umformuliert wird).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from .config import VOCAB_FILE


@dataclass(frozen=True)
class Entry:
    fr: str
    de: str
    tags: tuple[str, ...] = ()
    note: str = ""
    explicit_id: str | None = None

    @property
    def key(self) -> str:
        """Stabiler Schluessel, ueber den DB und Datei verknuepft werden."""
        if self.explicit_id:
            return f"id:{self.explicit_id}"
        return f"{normalize(self.fr)}|{normalize(self.de)}"

    def to_json_obj(self) -> dict:
        obj: dict = {"fr": self.fr, "de": self.de}
        if self.tags:
            obj["tags"] = list(self.tags)
        if self.note:
            obj["note"] = self.note
        if self.explicit_id:
            obj["id"] = self.explicit_id
        return obj

    def to_line(self) -> str:
        return json.dumps(self.to_json_obj(), ensure_ascii=False)


class VocabFileError(ValueError):
    """Die Vokabeldatei enthaelt eine kaputte Zeile."""


_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Vergleichsform: klein, ohne Akzente, ohne Mehrfach-Leerzeichen.

    Nur fuer Duplikat-Erkennung und Schluessel - angezeigt wird immer das
    Original.
    """
    text = unicodedata.normalize("NFKD", text.strip().lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return _WS.sub(" ", text)


def _coerce_tags(raw) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        raw = [t for t in re.split(r"[,;]", raw)]
    return tuple(t.strip() for t in raw if str(t).strip())


def parse_line(line: str, *, lineno: int | None = None) -> Entry | None:
    """Parst eine Zeile. Gibt ``None`` fuer Leerzeilen/Kommentare zurueck."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or stripped.startswith("//"):
        return None
    where = f"Zeile {lineno}: " if lineno else ""
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError as exc:  # pragma: no cover - Fehlerpfad
        raise VocabFileError(f"{where}kein gültiges JSON ({exc.msg})") from exc
    if not isinstance(obj, dict):
        raise VocabFileError(f"{where}erwartet wird ein JSON-Objekt {{...}}")
    fr = str(obj.get("fr", "")).strip()
    de = str(obj.get("de", "")).strip()
    if not fr or not de:
        raise VocabFileError(f"{where}'fr' und 'de' dürfen nicht leer sein")
    explicit_id = obj.get("id")
    return Entry(
        fr=fr,
        de=de,
        tags=_coerce_tags(obj.get("tags")),
        note=str(obj.get("note", "")).strip(),
        explicit_id=str(explicit_id).strip() if explicit_id else None,
    )


def iter_entries(path: Path | None = None) -> Iterator[Entry]:
    path = Path(path or VOCAB_FILE)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            entry = parse_line(line, lineno=lineno)
            if entry is not None:
                yield entry


def load_entries(path: Path | None = None) -> list[Entry]:
    """Liest alle Eintraege; spaetere Duplikate (gleicher Key) gewinnen."""
    by_key: dict[str, Entry] = {}
    for entry in iter_entries(path):
        by_key[entry.key] = entry
    return list(by_key.values())


@dataclass
class AddResult:
    added: list[Entry] = field(default_factory=list)
    duplicates: list[Entry] = field(default_factory=list)

    @property
    def n_added(self) -> int:
        return len(self.added)


def append_entries(new_entries: Iterable[Entry], path: Path | None = None) -> AddResult:
    """Haengt Eintraege an die Datei an und ueberspringt Duplikate.

    Geschrieben wird atomar (tempfile + ``os.replace``), damit die Datei bei
    einem Abbruch nicht halb beschrieben zurueckbleibt.
    """
    path = Path(path or VOCAB_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = {e.key for e in iter_entries(path)}
    result = AddResult()
    lines: list[str] = []
    for entry in new_entries:
        if entry.key in existing:
            result.duplicates.append(entry)
            continue
        existing.add(entry.key)
        result.added.append(entry)
        lines.append(entry.to_line())
    if not lines:
        return result

    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if old and not old.endswith("\n"):
        old += "\n"
    new_text = old + "\n".join(lines) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".vocab-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        os.replace(tmp, path)
    except BaseException:  # pragma: no cover - Fehlerpfad
        Path(tmp).unlink(missing_ok=True)
        raise
    return result


_PAIR_SEP = re.compile(r"\s*(?:\||=|;|\t|\s->\s|\s–\s|\s—\s)\s*")


def parse_pair_text(text: str) -> list[Entry]:
    """Parst frei getippte Zeilen wie ``avoir le cafard | Trübsal blasen``.

    Erlaubte Trenner: ``|``, ``=``, ``;``, Tab, `` -> ``, `` – ``.
    Ein dritter Teil wird als kommagetrennte Tag-Liste gelesen.
    Zeilen, die bereits JSON sind, werden ebenfalls akzeptiert.
    """
    entries: list[Entry] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip().lstrip("-*• ").strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("{"):
            entry = parse_line(line, lineno=lineno)
            if entry:
                entries.append(entry)
            continue
        parts = [p.strip() for p in _PAIR_SEP.split(line)]
        parts = [p for p in parts if p]
        if len(parts) < 2:
            raise VocabFileError(
                f"Zeile {lineno}: kein Trennzeichen gefunden in {line!r} "
                "(erwartet z.B. 'français | deutsch')"
            )
        fr, de = parts[0], parts[1]
        tags = _coerce_tags(parts[2]) if len(parts) > 2 else ()
        entries.append(Entry(fr=fr, de=de, tags=tags))
    return entries
