"""Markup that a screen renders and a voice cannot."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ear.spoken_text import to_spoken  # noqa: E402


class SpokenTextTests(unittest.TestCase):
    def test_plain_prose_is_left_alone(self):
        text = "The current temperature is 24.5 degrees with 71 percent humidity."
        self.assertEqual(to_spoken(text), text)

    def test_bullets_become_sentences(self):
        spoken = to_spoken("Your tasks:\n- SQL final semana\n- Comprar cursos")
        self.assertEqual(spoken, "Your tasks: SQL final semana. Comprar cursos")

    def test_emphasis_keeps_the_word_and_drops_the_marker(self):
        self.assertEqual(to_spoken("It is **22 degrees** and *raining*"),
                         "It is 22 degrees and raining")

    def test_numbered_lists_lose_their_numbering(self):
        self.assertEqual(to_spoken("1. first\n2. second"), "first. second")

    def test_headings_are_spoken_without_the_hashes(self):
        self.assertEqual(to_spoken("## Weather\n\nIt is warm."), "Weather. It is warm.")

    def test_a_link_is_read_as_its_label(self):
        self.assertEqual(to_spoken("See [the docs](https://example.com) now."),
                         "See the docs now.")

    def test_inline_code_keeps_its_contents(self):
        self.assertEqual(to_spoken("Run `systemctl status` please."),
                         "Run systemctl status please.")

    def test_a_fenced_block_is_not_read_aloud(self):
        spoken = to_spoken("Here:\n```\nrm -rf /\n```\nDone.")
        self.assertNotIn("rm -rf", spoken)
        self.assertIn("Done.", spoken)

    def test_emoji_are_dropped(self):
        self.assertEqual(to_spoken("It is raining 🌧️ today"), "It is raining today")

    def test_a_horizontal_rule_leaves_nothing_behind(self):
        self.assertEqual(to_spoken("Before\n---\nAfter"), "Before. After")

    def test_the_model_s_own_full_stop_is_not_doubled(self):
        self.assertEqual(to_spoken("First line.\nSecond line."), "First line. Second line.")

    def test_empty_input_stays_empty(self):
        self.assertEqual(to_spoken(""), "")

    def test_a_single_line_is_not_given_a_trailing_stop(self):
        self.assertEqual(to_spoken("- just one"), "just one")

    def test_a_bullet_star_is_not_mistaken_for_emphasis(self):
        self.assertEqual(to_spoken("* alpha\n* beta"), "alpha. beta")
