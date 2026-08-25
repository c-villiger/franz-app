"""Tests fuer Vokabeldatei, Datenbank und Auswahl-Algorithmus.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import random
import sys
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vocabtrainer import db as dbmod  # noqa: E402
from vocabtrainer.scheduler import (  # noqa: E402
    BASE_WEIGHT,
    cooldown_factor,
    pick_card,
    pick_direction,
    weight_of,
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

    def conn_with(self, entries):
        append_entries(entries, self.vocab)
        conn = dbmod.connect(self.db)
        dbmod.sync_from_file(conn, load_entries(self.vocab))
        self.addCleanup(conn.close)
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

    def test_progress_survives_a_restart(self):
        conn = self.conn_with([Entry("a", "A")])
        card = dbmod.all_cards(conn)[0]
        dbmod.set_label(conn, card.id, "unsicher")
        conn.close()

        conn2 = dbmod.connect(self.db)
        self.addCleanup(conn2.close)
        dbmod.sync_from_file(conn2, load_entries(self.vocab))
        self.assertEqual(dbmod.all_cards(conn2)[0].label, "unsicher")


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

    def test_pick_direction(self):
        self.assertEqual(pick_direction("fr2de"), "fr2de")
        self.assertEqual(pick_direction("de2fr"), "de2fr")
        rng = random.Random(1)
        picked = {pick_direction("mixed", rng) for _ in range(50)}
        self.assertEqual(picked, {"fr2de", "de2fr"})


if __name__ == "__main__":
    unittest.main()
