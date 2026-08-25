"""Kommandozeile - vor allem zum Hinzufuegen neuer Vokabeln.

    python3 -m vocabtrainer.cli add "avoir le cafard | Trübsal blasen"
    python3 -m vocabtrainer.cli add --tag idiom --tag familier - <<'EOF'
    tomber dans les pommes | in Ohnmacht fallen
    poser un lapin | jemanden versetzen
    EOF

    python3 -m vocabtrainer.cli check     # Datei validieren
    python3 -m vocabtrainer.cli sync      # Datenbank abgleichen
    python3 -m vocabtrainer.cli stats     # Zähler anzeigen
    python3 -m vocabtrainer.cli list --label unsicher
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import db as dbmod
from .config import DB_PATH, LABELS, VOCAB_FILE
from .vocab_file import (
    Entry,
    VocabFileError,
    append_entries,
    load_entries,
    parse_pair_text,
)


def _read_text(sources: list[str]) -> str:
    if not sources or sources == ["-"]:
        return sys.stdin.read()
    return "\n".join(sources)


def cmd_add(args: argparse.Namespace) -> int:
    text = _read_text(args.pairs)
    try:
        entries = parse_pair_text(text)
    except VocabFileError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2
    if not entries:
        print("Nichts hinzuzufügen (keine verwertbaren Zeilen).", file=sys.stderr)
        return 1
    if args.tag:
        extra = tuple(args.tag)
        entries = [
            Entry(
                fr=e.fr,
                de=e.de,
                tags=tuple(dict.fromkeys(e.tags + extra)),
                note=e.note,
                explicit_id=e.explicit_id,
            )
            for e in entries
        ]

    if args.dry_run:
        for entry in entries:
            print(entry.to_line())
        return 0

    result = append_entries(entries, args.file)
    for entry in result.added:
        print(f"+ {entry.fr}  →  {entry.de}")
    for entry in result.duplicates:
        print(f"= {entry.fr} (schon vorhanden, übersprungen)")
    print(f"\n{result.n_added} neu in {args.file}")

    if not args.no_sync:
        conn = dbmod.connect(args.db)
        sync = dbmod.sync_from_file(conn, load_entries(args.file))
        print(f"Datenbank: {sync.summary()}")
        conn.close()
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    try:
        entries = load_entries(args.file)
    except VocabFileError as exc:
        print(f"Fehler in {args.file}: {exc}", file=sys.stderr)
        return 1
    seen: dict[str, Entry] = {}
    dupes = 0
    for entry in entries:
        if entry.key in seen:
            dupes += 1
        seen[entry.key] = entry
    print(f"{args.file}: {len(entries)} Einträge, {dupes} Duplikate, JSON in Ordnung.")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    conn = dbmod.connect(args.db)
    result = dbmod.sync_from_file(conn, load_entries(args.file))
    print(result.summary())
    conn.close()
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    conn = dbmod.connect(args.db)
    dbmod.sync_from_file(conn, load_entries(args.file))
    counts = dbmod.stats(conn)
    print(f"Gesamt:             {counts['total']}")
    print(f"🔴 unsicher:         {counts['unsicher']}")
    print(f"🟡 mittel:           {counts['mittel']}")
    print(f"🟢 sicher:           {counts['sicher']}")
    print(f"⚪️ noch nicht gefragt: {counts['unseen']}")
    print(f"Heute bewertet:     {dbmod.reviews_today(conn)}")
    conn.close()
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    conn = dbmod.connect(args.db)
    dbmod.sync_from_file(conn, load_entries(args.file))
    cards = dbmod.all_cards(conn, tags=args.tag)
    if args.label == "unseen":
        cards = [c for c in cards if c.label is None]
    elif args.label:
        cards = [c for c in cards if c.label == args.label]
    for card in sorted(cards, key=lambda c: c.fr.lower()):
        mark = {"sicher": "🟢", "mittel": "🟡", "unsicher": "🔴", None: "⚪️"}[card.label]
        tags = f"  [{', '.join(card.tags)}]" if card.tags else ""
        print(f"{mark} {card.fr}  →  {card.de}{tags}")
    print(f"\n{len(cards)} Einträge")
    conn.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vocabtrainer",
        description="Vokabeln verwalten für den Französisch-Trainer.",
    )
    parser.add_argument("--file", type=Path, default=VOCAB_FILE, help="Vokabeldatei")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="SQLite-Datenbank")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="Vokabeln hinzufügen")
    p_add.add_argument(
        "pairs",
        nargs="*",
        help="Zeilen 'français | deutsch [| tags]'; ohne Argumente wird stdin gelesen",
    )
    p_add.add_argument("--tag", action="append", default=[], help="Tag für alle neuen Einträge")
    p_add.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts schreiben")
    p_add.add_argument("--no-sync", action="store_true", help="Datenbank nicht abgleichen")
    p_add.set_defaults(func=cmd_add)

    p_check = sub.add_parser("check", help="Vokabeldatei validieren")
    p_check.set_defaults(func=cmd_check)

    p_sync = sub.add_parser("sync", help="Datenbank mit der Vokabeldatei abgleichen")
    p_sync.set_defaults(func=cmd_sync)

    p_stats = sub.add_parser("stats", help="Zähler anzeigen")
    p_stats.set_defaults(func=cmd_stats)

    p_list = sub.add_parser("list", help="Vokabeln auflisten")
    p_list.add_argument("--label", choices=[*LABELS, "unseen"], help="nur dieses Label")
    p_list.add_argument("--tag", action="append", default=[], help="nach Tag filtern")
    p_list.set_defaults(func=cmd_list)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
