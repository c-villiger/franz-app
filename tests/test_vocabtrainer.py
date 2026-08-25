"""Tests fuer Vokabeldatei, Datenbank und Auswahl-Algorithmus.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import builtins
import os
import random
import sys
import tempfile
import unittest
from unittest import mock
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vocabtrainer import config  # noqa: E402
from vocabtrainer import db as dbmod  # noqa: E402
from vocabtrainer import netinfo  # noqa: E402
from vocabtrainer.scheduler import (  # noqa: E402
    BASE_WEIGHT,
    cooldown_factor,
    pick_card,
    pick_direction,
    weight_of,
    weights_from_json,
    weights_to_json,
)
from vocabtrainer.vocab_file import (  # noqa: E402
    Entry,
    VocabFileError,
    append_entries,
    load_entries,
    normalize,
    parse_pair_text,
)


class TempPaths(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.vocab = self.tmp / "vocab.jsonl"
        self.db = self.tmp / "vocab.db"
        self.addCleanup(self._tmp.cleanup)

    # Ueberschrieben von TestDatabaseLibsql, damit dieselben Tests auch gegen
    # den libSQL-Adapter laufen.
    BACKEND = "sqlite3"

    def open_conn(self, path=None):
        """Einzige Stelle, an der eine Verbindung entsteht - Unterklassen
        hängen sich hier ein, um andere Treiber nachzustellen."""
        conn = dbmod.connect(path or self.db, backend=self.BACKEND)
        self.addCleanup(conn.close)
        return conn

    def conn_with(self, entries):
        append_entries(entries, self.vocab)
        conn = self.open_conn()
        dbmod.sync_from_file(conn, load_entries(self.vocab))
        return conn


class TestVocabFile(TempPaths):
    def test_parse_pair_text_accepts_several_separators(self):
        entries = parse_pair_text(
            "avoir le cafard | Trübsal blasen | idiom\n"
            "- il fait beau = das Wetter ist schön\n"
            "poser un lapin -> jemanden versetzen\n"
            "\n"
            "# Kommentar wird ignoriert\n"
            '{"fr": "du coup", "de": "also", "tags": ["familier"]}'
        )
        self.assertEqual(len(entries), 4)
        self.assertEqual(entries[0].tags, ("idiom",))
        self.assertEqual(entries[1].fr, "il fait beau")
        self.assertEqual(entries[3].de, "also")

    def test_parse_pair_text_rejects_line_without_separator(self):
        with self.assertRaises(VocabFileError):
            parse_pair_text("nur ein französischer Ausdruck")

    def test_commas_and_quotes_survive_a_roundtrip(self):
        tricky = Entry(fr='dire "oui", puis partir', de="Ja sagen, dann gehen")
        append_entries([tricky], self.vocab)
        loaded = load_entries(self.vocab)
        self.assertEqual(loaded[0].fr, tricky.fr)
        self.assertEqual(loaded[0].de, tricky.de)

    def test_append_skips_duplicates_ignoring_case_and_accents(self):
        append_entries([Entry("Avoir le cafard", "Trübsal blasen")], self.vocab)
        result = append_entries(
            [
                Entry("avoir le  CAFARD", "trübsal blasen"),  # dasselbe
                Entry("jeter l'éponge", "das Handtuch werfen"),  # neu
            ],
            self.vocab,
        )
        self.assertEqual(result.n_added, 1)
        self.assertEqual(len(result.duplicates), 1)
        self.assertEqual(len(load_entries(self.vocab)), 2)

    def test_normalize_strips_accents_and_extra_spaces(self):
        self.assertEqual(normalize("  Ça  Va Très  bien "), "ca va tres bien")

    def test_append_creates_file_and_keeps_one_entry_per_line(self):
        append_entries([Entry("a", "b"), Entry("c", "d")], self.vocab)
        lines = self.vocab.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)

    def test_shipped_vocab_file_is_valid(self):
        entries = load_entries()  # vocab/vocab.jsonl aus dem Repo
        self.assertGreater(len(entries), 50)
        self.assertEqual(len({e.key for e in entries}), len(entries))


class TestDatabase(TempPaths):
    def test_sync_adds_updates_and_deactivates(self):
        conn = self.conn_with([Entry("a", "A"), Entry("b", "B")])
        self.assertEqual(len(dbmod.all_cards(conn)), 2)

        # Neues Wort in der Datei -> Karte kommt dazu.
        append_entries([Entry("c", "C")], self.vocab)
        result = dbmod.sync_from_file(conn, load_entries(self.vocab))
        self.assertEqual((result.added, result.updated), (1, 0))

        # Uebersetzung korrigiert -> Text wird aktualisiert, Label bleibt.
        card = next(c for c in dbmod.all_cards(conn) if c.fr == "a")
        dbmod.set_label(conn, card.id, "mittel")
        entries = [Entry("a", "A", note="korrigiert"), Entry("b", "B"), Entry("c", "C")]
        result = dbmod.sync_from_file(conn, entries)
        self.assertEqual(result.updated, 1)
        self.assertEqual(dbmod.get_card(conn, card.id).label, "mittel")

        # Aus der Datei entfernt -> deaktiviert, aber nicht geloescht.
        result = dbmod.sync_from_file(conn, [Entry("a", "A", note="korrigiert")])
        self.assertEqual(result.deactivated, 2)
        self.assertEqual(len(dbmod.all_cards(conn)), 1)
        self.assertEqual(len(dbmod.all_cards(conn, include_inactive=True)), 3)

        # Und wieder aufgenommen -> reaktiviert.
        result = dbmod.sync_from_file(conn, entries)
        self.assertEqual(result.reactivated, 2)

    def test_sync_is_idempotent(self):
        conn = self.conn_with([Entry("a", "A"), Entry("b", "B")])
        result = dbmod.sync_from_file(conn, load_entries(self.vocab))
        self.assertFalse(result.changed)

    def test_set_label_updates_counts_and_history(self):
        conn = self.conn_with([Entry("a", "A"), Entry("b", "B"), Entry("c", "C")])
        cards = sorted(dbmod.all_cards(conn), key=lambda c: c.fr)
        dbmod.set_label(conn, cards[0].id, "sicher", "fr2de")
        dbmod.set_label(conn, cards[1].id, "unsicher", "de2fr")
        dbmod.set_label(conn, cards[1].id, "mittel", "fr2de")  # Meinung geändert

        counts = dbmod.stats(conn)
        self.assertEqual(counts["sicher"], 1)
        self.assertEqual(counts["mittel"], 1)
        self.assertEqual(counts["unsicher"], 0)
        self.assertEqual(counts["unseen"], 1)
        self.assertEqual(counts["total"], 3)

        self.assertEqual(dbmod.get_card(conn, cards[1].id).times_asked, 2)
        self.assertEqual(len(dbmod.recent_reviews(conn)), 3)
        self.assertEqual(dbmod.reviews_today(conn), 3)

    def test_set_label_rejects_unknown_label(self):
        conn = self.conn_with([Entry("a", "A")])
        card = dbmod.all_cards(conn)[0]
        with self.assertRaises(ValueError):
            dbmod.set_label(conn, card.id, "vielleicht")

    def test_reset_progress_keeps_cards(self):
        conn = self.conn_with([Entry("a", "A"), Entry("b", "B")])
        for card in dbmod.all_cards(conn):
            dbmod.set_label(conn, card.id, "sicher")
        dbmod.reset_progress(conn)
        counts = dbmod.stats(conn)
        self.assertEqual(counts["unseen"], 2)
        self.assertEqual(dbmod.recent_reviews(conn), [])

    def test_tag_filter(self):
        conn = self.conn_with(
            [Entry("a", "A", tags=("idiom",)), Entry("b", "B", tags=("alltag",))]
        )
        self.assertEqual(dbmod.all_tags(conn), ["alltag", "idiom"])
        self.assertEqual([c.fr for c in dbmod.all_cards(conn, tags=["idiom"])], ["a"])

    def test_settings_survive_a_restart(self):
        conn = self.conn_with([Entry("a", "A")])
        self.assertIsNone(dbmod.get_setting(conn, "weights"))
        self.assertEqual(dbmod.get_setting(conn, "weights", "fallback"), "fallback")
        dbmod.set_setting(conn, "weights", '{"sicher": 0.5}')
        dbmod.set_setting(conn, "weights", '{"sicher": 2.0}')  # überschreiben
        conn.close()

        conn2 = self.open_conn()
        self.assertEqual(dbmod.get_setting(conn2, "weights"), '{"sicher": 2.0}')
        dbmod.delete_setting(conn2, "weights")
        self.assertIsNone(dbmod.get_setting(conn2, "weights"))

    def test_progress_survives_a_restart(self):
        conn = self.conn_with([Entry("a", "A")])
        card = dbmod.all_cards(conn)[0]
        dbmod.set_label(conn, card.id, "unsicher")
        conn.close()

        conn2 = self.open_conn()
        dbmod.sync_from_file(conn2, load_entries(self.vocab))
        self.assertEqual(dbmod.all_cards(conn2)[0].label, "unsicher")


class TestDatabaseLibsql(TestDatabase):
    """Dieselben Tests noch einmal über den libSQL-Adapter.

    Der Client liefert nackte Tupel statt benannter Zeilen - die
    Adapterschicht muss das so überdecken, dass kein einziger Test es merkt.
    Geprüft wird gegen eine lokale Datei, das braucht kein Netz.
    """

    BACKEND = "libsql"

    @classmethod
    def setUpClass(cls) -> None:
        try:
            import libsql  # noqa: F401
        except ImportError:  # pragma: no cover - Paket nicht installiert
            raise unittest.SkipTest("Paket 'libsql' ist nicht installiert")

    def test_creates_the_directory_of_a_fresh_database(self):
        # libSQL legt fehlende Verzeichnisse - anders als sqlite3 - nicht an.
        fresh = self.tmp / "noch" / "nicht" / "da" / "vocab.db"
        self.open_conn(fresh)
        self.assertTrue(fresh.exists())


class _CursorOhneSpaltennamen:
    """Cursor, der Zeilen liefert, aber keine Metadaten - so verhielt sich die
    gehostete Turso-Verbindung. Lokal meldet derselbe Client die Spalten."""

    description = None

    def __init__(self, cursor) -> None:
        self._cursor = cursor

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()


class _VerbindungOhneSpaltennamen:
    def __init__(self, conn) -> None:
        self._conn = conn

    def execute(self, sql, parameters=()):
        return _CursorOhneSpaltennamen(self._conn.execute(sql, parameters))

    def executescript(self, script):
        self._conn.executescript(script)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


class _CursorMitGrossemSchluesselwort:
    """Cursor, der Spaltennamen so meldet wie die gehostete Datenbank.

    Turso gab ``key`` als ``KEY`` zurück – es ist ein SQL-Schlüsselwort.
    Genau daran scheiterte jedes row["key"].
    """

    def __init__(self, cursor) -> None:
        self._cursor = cursor
        self.description = tuple(
            (str(d[0]).upper() if str(d[0]).lower() == "key" else d[0],) + tuple(d[1:])
            for d in (cursor.description or ())
        )

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()


class _VerbindungMitGrossemSchluesselwort:
    def __init__(self, conn) -> None:
        self._conn = conn

    def execute(self, sql, parameters=()):
        return _CursorMitGrossemSchluesselwort(self._conn.execute(sql, parameters))

    def executescript(self, script):
        self._conn.executescript(script)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


class TestDatabaseGrossgeschrieben(TestDatabaseLibsql):
    """Die gesamte Suite gegen einen Treiber, der `key` als `KEY` meldet."""

    def open_conn(self, path=None):
        conn = super().open_conn(path)
        conn._conn = _VerbindungMitGrossemSchluesselwort(conn._conn)
        return conn


class TestDatabaseOhneSpaltennamen(TestDatabaseLibsql):
    """Der Fehler, der erst gegen echtes Turso auftrat.

    Dort kamen die Zeilen an, aber ohne Spaltennamen - jedes row["key"] lief
    ins Leere. Hier läuft die gesamte Suite noch einmal gegen einen Treiber,
    der genau das tut; die Namen müssen dann aus der Abfrage selbst kommen.
    """

    def open_conn(self, path=None):
        conn = super().open_conn(path)
        conn._conn = _VerbindungOhneSpaltennamen(conn._conn)
        return conn


class TestNamedRow(unittest.TestCase):
    """Die Zeilen des Adapters müssen sich wie sqlite3.Row verhalten."""

    def setUp(self) -> None:
        self.row = dbmod._NamedRow(("abc", 7, None), {"fr": 0, "times_asked": 1, "label": 2})

    def test_access_by_column_name(self):
        self.assertEqual(self.row["fr"], "abc")
        self.assertEqual(self.row["times_asked"], 7)
        self.assertIsNone(self.row["label"])

    def test_access_by_position(self):
        self.assertEqual(self.row[0], "abc")

    def test_unknown_column_raises_key_error(self):
        with self.assertRaises(KeyError) as fehler:
            self.row["gibtsnicht"]
        # Die Meldung muss die vorhandenen Spalten nennen - erst dadurch liess
        # sich der Turso-Fehler überhaupt einkreisen.
        self.assertIn("fr", str(fehler.exception))

    def test_lookup_ignores_case(self):
        # Die gehostete Datenbank meldete "KEY" statt "key".
        row = dbmod._NamedRow(("x",), {"key": 0})
        self.assertEqual(row["key"], "x")
        self.assertEqual(row["KEY"], "x")
        self.assertEqual(row["Key"], "x")

    def test_keys_and_len(self):
        self.assertEqual(self.row.keys(), ["fr", "times_asked", "label"])
        self.assertEqual(len(self.row), 3)


class TestBackendSelection(unittest.TestCase):
    def test_url_selects_libsql(self):
        self.assertTrue(config.vocab_readonly("libsql://irgendwo.turso.io"))

    def test_no_url_keeps_the_vocab_file_writable(self):
        self.assertFalse(config.vocab_readonly(""))

    def test_readonly_can_be_forced_off(self):
        with mock.patch.dict(os.environ, {"FRANZ_VOCAB_READONLY": "0"}):
            self.assertFalse(config.vocab_readonly("libsql://irgendwo.turso.io"))

    def test_readonly_can_be_forced_on(self):
        with mock.patch.dict(os.environ, {"FRANZ_VOCAB_READONLY": "1"}):
            self.assertTrue(config.vocab_readonly(""))


def make_card(card_id: int, label: str | None, last_asked: str | None = None) -> dbmod.Card:
    return dbmod.Card(
        id=card_id, key=str(card_id), fr=f"fr{card_id}", de=f"de{card_id}",
        tags=(), note="", label=label, times_asked=0,
        last_asked_at=last_asked, last_labeled_at=last_asked,
    )


class TestScheduler(unittest.TestCase):
    def test_priority_order_of_base_weights(self):
        self.assertGreater(BASE_WEIGHT[None], BASE_WEIGHT["unsicher"])
        self.assertGreater(BASE_WEIGHT["unsicher"], BASE_WEIGHT["mittel"])
        self.assertGreater(BASE_WEIGHT["mittel"], BASE_WEIGHT["sicher"])

    def test_weight_order_for_fresh_cards(self):
        weights = [weight_of(make_card(i, l)) for i, l in
                   enumerate([None, "unsicher", "mittel", "sicher"])]
        self.assertEqual(weights, sorted(weights, reverse=True))

    def test_cooldown_reduces_but_never_zeroes_the_weight(self):
        now = datetime.now(timezone.utc)
        just_now = make_card(1, "sicher", (now - timedelta(minutes=1)).isoformat())
        long_ago = make_card(2, "sicher", (now - timedelta(days=7)).isoformat())
        self.assertLess(cooldown_factor(just_now, now), cooldown_factor(long_ago, now))
        self.assertGreater(weight_of(just_now, now=now), 0)
        self.assertAlmostEqual(cooldown_factor(long_ago, now), 1.0)

    def test_unseen_cards_have_no_cooldown(self):
        self.assertEqual(cooldown_factor(make_card(1, None)), 1.0)

    def test_recently_shown_cards_are_avoided(self):
        card = make_card(1, None)
        self.assertLess(weight_of(card, recent_ids=[1]), weight_of(card))

    def test_distribution_follows_the_priority_order(self):
        cards = (
            [make_card(i, None) for i in range(10)]
            + [make_card(100 + i, "unsicher") for i in range(10)]
            + [make_card(200 + i, "mittel") for i in range(10)]
            + [make_card(300 + i, "sicher") for i in range(10)]
        )
        rng = random.Random(20240501)
        counts: Counter[str] = Counter()
        for _ in range(6000):
            picked = pick_card(cards, rng=rng)
            counts[picked.label or "unseen"] += 1
        self.assertGreater(counts["unseen"], counts["unsicher"])
        self.assertGreater(counts["unsicher"], counts["mittel"])
        self.assertGreater(counts["mittel"], counts["sicher"])
        # "sicher" wird seltener, aber nicht nie gefragt.
        self.assertGreater(counts["sicher"], 0)

    def test_every_card_can_come_up(self):
        cards = [make_card(i, "sicher") for i in range(5)] + [make_card(9, None)]
        rng = random.Random(7)
        seen = {pick_card(cards, rng=rng).id for _ in range(2000)}
        self.assertEqual(seen, {c.id for c in cards})

    def test_pick_card_handles_edge_cases(self):
        self.assertIsNone(pick_card([]))
        only = make_card(1, "sicher")
        self.assertIs(pick_card([only]), only)

    def test_no_immediate_repeat_in_a_reasonable_deck(self):
        cards = [make_card(i, None) for i in range(30)]
        rng = random.Random(3)
        recent: list[int] = []
        repeats = 0
        for _ in range(300):
            card = pick_card(cards, recent_ids=recent, rng=rng)
            if recent and card.id == recent[0]:
                repeats += 1
            recent = [card.id, *[i for i in recent if i != card.id]][:12]
        self.assertEqual(repeats, 0)

    def test_custom_weights_change_the_distribution(self):
        cards = [make_card(i, "sicher") for i in range(5)] + [
            make_card(100 + i, "unsicher") for i in range(5)
        ]
        eigene = {None: 1.0, "unsicher": 1.0, "mittel": 1.0, "sicher": 20.0}
        rng = random.Random(11)
        counts = Counter(
            pick_card(cards, weights=eigene, rng=rng).label for _ in range(2000)
        )
        # Umgekehrt zur Voreinstellung: jetzt kommt "sicher" häufiger.
        self.assertGreater(counts["sicher"], counts["unsicher"])

    def test_weight_zero_excludes_a_group(self):
        cards = [make_card(i, "sicher") for i in range(5)] + [
            make_card(100 + i, "unsicher") for i in range(5)
        ]
        ohne_sicher = {None: 1.0, "unsicher": 1.0, "mittel": 1.0, "sicher": 0.0}
        self.assertEqual(weight_of(cards[0], weights=ohne_sicher), 0.0)
        rng = random.Random(5)
        labels = {pick_card(cards, weights=ohne_sicher, rng=rng).label for _ in range(500)}
        self.assertEqual(labels, {"unsicher"})

    def test_all_weights_zero_still_returns_a_card(self):
        # Sonst bliebe die Abfrage stehen, statt einfach gleichverteilt zu ziehen.
        cards = [make_card(i, "sicher") for i in range(4)]
        alles_null = {None: 0.0, "unsicher": 0.0, "mittel": 0.0, "sicher": 0.0}
        rng = random.Random(2)
        gezogen = {pick_card(cards, weights=alles_null, rng=rng).id for _ in range(300)}
        self.assertEqual(gezogen, {c.id for c in cards})

    def test_weights_survive_a_json_roundtrip(self):
        eigene = {None: 3.5, "unsicher": 2.0, "mittel": 1.0, "sicher": 0.0}
        self.assertEqual(weights_from_json(weights_to_json(eigene)), eigene)

    def test_weights_from_json_falls_back_to_the_defaults(self):
        for kaputt in (None, "", "kein json", "[1, 2]", '{"unbekannt": 5}'):
            self.assertEqual(weights_from_json(kaputt), BASE_WEIGHT)

    def test_weights_from_json_keeps_defaults_for_missing_groups(self):
        teil = weights_from_json('{"sicher": 9.0}')
        self.assertEqual(teil["sicher"], 9.0)
        self.assertEqual(teil[None], BASE_WEIGHT[None])

    def test_weights_from_json_rejects_negative_values(self):
        self.assertEqual(weights_from_json('{"sicher": -5}')["sicher"], 0.0)

    def test_pick_direction(self):
        self.assertEqual(pick_direction("fr2de"), "fr2de")
        self.assertEqual(pick_direction("de2fr"), "de2fr")
        rng = random.Random(1)
        picked = {pick_direction("mixed", rng) for _ in range(50)}
        self.assertEqual(picked, {"fr2de", "de2fr"})




class TestNetinfo(unittest.TestCase):
    """Adresse und QR-Code fuer den Zugriff vom Handy."""

    def test_urls_always_offer_localhost(self):
        labels = dict((label, url) for label, url in netinfo.urls(8501))
        self.assertIn("http://localhost:8501", labels.values())

    def test_urls_use_the_given_port(self):
        for _, url in netinfo.urls(9000):
            self.assertTrue(url.endswith(":9000"), url)

    def test_lan_ip_is_never_loopback(self):
        # Sonst stünde auf dem QR-Code eine Adresse, die auf dem Handy
        # auf das Handy selbst zeigt.
        ip = netinfo.lan_ip()
        if ip is not None:
            self.assertFalse(ip.startswith("127."), ip)

    def test_mdns_hostname_has_no_domain_and_ends_in_local(self):
        host = netinfo.mdns_hostname()
        if host is not None:
            self.assertTrue(host.endswith(".local"), host)
            self.assertEqual(host.count("."), 1, host)

    def test_qr_lines_are_rectangular_and_use_only_block_characters(self):
        lines = netinfo.qr_lines("http://192.168.1.42:8501")
        self.assertIsNotNone(lines, "qrcode ist nicht installiert")
        self.assertEqual(len({len(line) for line in lines}), 1, "Zeilen unterschiedlich lang")
        self.assertLessEqual(len(lines[0]), 80, "passt nicht in ein Terminal")
        self.assertLessEqual(set("".join(lines)), set("█▄▀\xa0 "))

    def test_qr_polarity_actually_flips(self):
        url = "http://192.168.1.42:8501"
        self.assertNotEqual(netinfo.qr_lines(url, invert=True), netinfo.qr_lines(url, invert=False))

    def test_qr_has_a_quiet_zone(self):
        # Ohne Ruhebereich am Rand erkennen Kameras den Code schlecht.
        lines = netinfo.qr_lines("http://192.168.1.42:8501", invert=False)
        self.assertEqual(set(lines[0]), {"\xa0"}, "oberste Zeile ist nicht leer")
        self.assertEqual({line[0] for line in lines}, {"\xa0"}, "linke Spalte ist nicht leer")

    def test_banner_mentions_the_phone_url(self):
        text = netinfo.banner(8501)
        url = netinfo.phone_url(8501)
        if url:
            self.assertIn(url, text)

    def test_banner_survives_a_missing_qrcode_package(self):
        real_import = builtins.__import__

        def without_qrcode(name, *args, **kwargs):
            if name == "qrcode":
                raise ImportError("nicht installiert")
            return real_import(name, *args, **kwargs)

        with mock.patch.object(builtins, "__import__", without_qrcode):
            self.assertIsNone(netinfo.qr_lines("http://example.invalid"))
            self.assertIn("http://localhost:8501", netinfo.banner(8501))


if __name__ == "__main__":
    unittest.main()
