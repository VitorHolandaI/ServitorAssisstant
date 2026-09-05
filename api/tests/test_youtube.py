"""The channel list and the spoken paging over it."""
from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_module.youtube import channels  # noqa: E402
from mcp_module.youtube.stream import (  # noqa: E402
    Video,
    _understand,
    _ago,
    _parse,
    _recent,
    _speak,
    _window_label,
)

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns="http://www.w3.org/2005/Atom">
  <title>Computerphile</title>
  <entry>
    <yt:videoId>abc12345678</yt:videoId>
    <title>How Watermarks Work</title>
    <published>2026-09-03T10:00:00+00:00</published>
  </entry>
  <entry>
    <yt:videoId>def12345678</yt:videoId>
    <title>Broken entry with no id</title>
  </entry>
</feed>"""


class FeedParsingTests(unittest.TestCase):
    def test_entries_become_videos(self):
        videos = _parse(FEED)
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0].title, "How Watermarks Work")
        self.assertEqual(videos[0].channel, "Computerphile")
        self.assertEqual(videos[0].url, "https://www.youtube.com/watch?v=abc12345678")

    def test_a_broken_feed_yields_nothing_rather_than_raising(self):
        self.assertEqual(_parse(b"not xml at all"), [])

    def test_an_entity_bomb_is_refused_before_parsing(self):
        bomb = (b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
                b'<!ENTITY lol2 "&lol;&lol;&lol;">]><feed>&lol2;</feed>')
        self.assertEqual(_parse(bomb), [])

    def test_a_doctype_alone_is_enough_to_refuse(self):
        self.assertEqual(_parse(b'<!DOCTYPE feed SYSTEM "file:///etc/passwd"><feed/>'), [])

    def test_an_oversized_feed_is_refused(self):
        self.assertEqual(_parse(b"<feed/>" + b" " * 2_000_001), [])


class SpokenAgeTests(unittest.TestCase):
    def _ago_for(self, **delta):
        when = dt.datetime.now(dt.timezone.utc) - dt.timedelta(**delta)
        return _ago(when)

    def test_minutes(self):
        self.assertEqual(self._ago_for(minutes=5), "5 minutes ago")

    def test_one_hour_is_singular(self):
        self.assertEqual(self._ago_for(hours=1, minutes=1), "1 hour ago")

    def test_days(self):
        self.assertEqual(self._ago_for(days=3), "3 days ago")

    def test_past_a_fortnight_it_speaks_weeks(self):
        self.assertEqual(self._ago_for(days=21), "3 weeks ago")


class SpokenListTests(unittest.TestCase):
    def _video(self, title):
        return Video(title, "Some Channel", "x" * 11,
                     dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2))

    def test_numbering_starts_at_one(self):
        spoken = _speak([self._video("First")], 0)
        self.assertTrue(spoken.startswith("Video 1: First, from Some Channel, 2 hours ago."))

    def test_numbering_continues_across_pages(self):
        spoken = _speak([self._video("Fourth")], 3)
        self.assertTrue(spoken.startswith("Video 4: Fourth"))


class ChannelStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "channels.csv"
        patcher = mock.patch.object(channels, "CHANNELS_FILE", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_absent_list_is_empty_not_an_error(self):
        self.assertEqual(channels.load(), [])

    def test_a_channel_survives_a_round_trip(self):
        self.assertTrue(channels.add("UC9-y-6csu5WGm29I7JiwpnA", "Computerphile"))
        self.assertEqual(channels.load(), [("UC9-y-6csu5WGm29I7JiwpnA", "Computerphile")])

    def test_following_twice_is_refused(self):
        channels.add("UC9-y-6csu5WGm29I7JiwpnA", "Computerphile")
        self.assertFalse(channels.add("UC9-y-6csu5WGm29I7JiwpnA", "Computerphile"))

    def test_takeout_rows_are_matched_on_the_id_not_the_header(self):
        source = Path(self.tmp.name) / "subscriptions.csv"
        source.write_text(
            "Channel Id,Channel Url,Channel Title\n"
            "UC9-y-6csu5WGm29I7JiwpnA,http://www.youtube.com/channel/UC9-y-6csu5WGm29I7JiwpnA,Computerphile\n"
            "UCHnyfMqiRRG1u-2MsSQLbXA,http://www.youtube.com/channel/UCHnyfMqiRRG1u-2MsSQLbXA,Veritasium\n",
            encoding="utf-8",
        )
        added, known = channels.import_takeout(source)
        self.assertEqual((added, known), (2, 0))
        self.assertEqual([title for _, title in channels.load()], ["Computerphile", "Veritasium"])

    def test_importing_the_same_export_twice_adds_nothing(self):
        source = Path(self.tmp.name) / "subscriptions.csv"
        source.write_text("Channel Id,Channel Url,Channel Title\n"
                          "UC9-y-6csu5WGm29I7JiwpnA,http://x,Computerphile\n", encoding="utf-8")
        channels.import_takeout(source)
        self.assertEqual(channels.import_takeout(source), (0, 1))

    def test_a_hand_edited_line_is_skipped_rather_than_loaded(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("channel_id,title\nnot-a-channel,Nope\n"
                             "UC9-y-6csu5WGm29I7JiwpnA,Computerphile\n", encoding="utf-8")
        self.assertEqual(channels.load(), [("UC9-y-6csu5WGm29I7JiwpnA", "Computerphile")])


class RecencyWindowTests(unittest.TestCase):
    """364 subscriptions carry thousands of entries; a day is what "new" means."""

    def _video(self, hours_old):
        return Video("Title", "Channel", "x" * 11,
                     dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_old))

    def test_only_videos_inside_the_window_survive(self):
        videos = [self._video(1), self._video(10), self._video(30)]
        self.assertEqual(len(_recent(videos, 24)), 2)

    def test_a_wider_window_takes_more(self):
        videos = [self._video(1), self._video(30), self._video(200)]
        self.assertEqual(len(_recent(videos, 168)), 2)

    def test_an_empty_window_is_empty_not_everything(self):
        self.assertEqual(_recent([self._video(100)], 24), [])

    def test_the_window_is_described_in_words_not_hours(self):
        self.assertEqual(_window_label(24), "today")
        self.assertEqual(_window_label(48), "in the last two days")
        self.assertEqual(_window_label(168), "in the last 7 days")


class SpokenChoiceTests(unittest.TestCase):
    """What the user says when a tool stops to ask which video they want."""

    def test_a_spoken_number_plays(self):
        self.assertEqual(_understand("play video two"), ("play", 2))

    def test_a_digit_plays(self):
        self.assertEqual(_understand("video 3"), ("play", 3))

    def test_two_misheard_as_too_still_plays(self):
        # Whisper produced exactly this: "Play video too."
        self.assertEqual(_understand("play video too"), ("play", 2))

    def test_asking_for_more_pages(self):
        self.assertEqual(_understand("more please"), ("more", 0))
        self.assertEqual(_understand("mais"), ("more", 0))

    def test_refusing_stops(self):
        self.assertEqual(_understand("no I don't want that"), ("stop", 0))
        self.assertEqual(_understand("para"), ("stop", 0))

    def test_silence_stops(self):
        self.assertEqual(_understand(""), ("stop", 0))

    def test_refusal_wins_over_a_number_in_the_same_sentence(self):
        # "no, not video 2" must not play video 2.
        self.assertEqual(_understand("no not video 2"), ("stop", 0))

    def test_something_unreadable_is_not_guessed_at(self):
        self.assertEqual(_understand("uh what"), ("unclear", 0))

    def test_portuguese_numbers_are_understood(self):
        self.assertEqual(_understand("quero o 5"), ("play", 5))
        self.assertEqual(_understand("toca o dois"), ("play", 2))
