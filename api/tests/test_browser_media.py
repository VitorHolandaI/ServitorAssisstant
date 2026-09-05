"""The browser and playback servers, minus the parts that need a desktop."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_module.browser.stream import _explicit_scheme, _looks_like_url, _safe_url  # noqa: E402
from mcp_module.media.stream import _metadata_text  # noqa: E402


class BrowserTargetTests(unittest.TestCase):
    """A voice transcript is whatever the room sounded like, so it is untrusted."""

    def test_a_bare_domain_is_a_url(self):
        self.assertTrue(_looks_like_url("youtube.com"))

    def test_a_sentence_is_not_a_url(self):
        self.assertFalse(_looks_like_url("opening hours of the museum"))

    def test_something_with_a_dot_but_spaces_is_not_a_url(self):
        self.assertFalse(_looks_like_url("see figure 2. then continue"))

    def test_web_schemes_are_recognised(self):
        self.assertEqual(_explicit_scheme("https://example.com"), "https")
        self.assertEqual(_explicit_scheme("http://example.com"), "http")

    def test_other_schemes_are_recognised_so_they_can_be_refused(self):
        self.assertEqual(_explicit_scheme("file:///etc/passwd"), "file")
        self.assertEqual(_explicit_scheme("javascript:alert(1)"), "javascript")

    def test_plain_text_names_no_scheme(self):
        self.assertIsNone(_explicit_scheme("youtube.com"))
        self.assertIsNone(_explicit_scheme("cat videos"))

    def test_a_bare_domain_is_normalised_to_https(self):
        self.assertEqual(_safe_url("youtube.com"), "https://youtube.com")

    def test_a_non_web_url_is_rejected(self):
        self.assertIsNone(_safe_url("file:///etc/passwd"))


class NowPlayingTests(unittest.TestCase):
    """MPRIS metadata as busctl --json=short actually returns it."""

    def test_title_and_artist_are_read(self):
        payload = json.dumps({"type": "a{sv}", "data": [{
            "xesam:title": {"type": "s", "data": "Me at the zoo"},
            "xesam:artist": {"type": "as", "data": ["jawed"]},
        }]})
        self.assertEqual(_metadata_text(payload), ("Me at the zoo", "jawed"))

    def test_several_artists_are_joined(self):
        payload = json.dumps({"data": [{
            "xesam:title": {"data": "Song"},
            "xesam:artist": {"data": ["A", "B"]},
        }]})
        self.assertEqual(_metadata_text(payload), ("Song", "A, B"))

    def test_a_title_with_no_artist_still_reads(self):
        payload = json.dumps({"data": [{"xesam:title": {"data": "Clip"}}]})
        self.assertEqual(_metadata_text(payload), ("Clip", ""))

    def test_garbage_is_not_an_error(self):
        self.assertEqual(_metadata_text("not json"), ("", ""))
        self.assertEqual(_metadata_text(json.dumps({"data": []})), ("", ""))
