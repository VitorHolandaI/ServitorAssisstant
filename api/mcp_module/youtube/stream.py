"""What is new on the channels you follow, read out three at a time.

A spoken list has to be short: nobody can hold twenty titles in their head
while a voice reads them. So this hands over three, remembers where it got
to, and gives the next three when asked. Saying nothing ends it, because the
cursor is only advanced by being asked.

Feeds come from YouTube's public per-channel Atom feed, which needs no key,
no login and no scraping of a page whose markup changes without warning. The
cost is that the channel list has to be obtained once - see channels.py.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import re
import time
from dataclasses import dataclass
from xml.etree import ElementTree  # nosec B405 - payload is guarded in _parse

from mcp.server.fastmcp import FastMCP

from mcp_module.browser.open_url import open_url
from mcp_module.youtube import channels as channel_store

logger = logging.getLogger(__name__)

MCP_HOST = os.getenv("YOUTUBE_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("YOUTUBE_MCP_PORT", "8007"))

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}

# One spoken mouthful.
PAGE_SIZE = int(os.getenv("YOUTUBE_PAGE_SIZE", "3"))
# Feeds are refetched at most this often: a subscription feed does not change
# between "read me the next three" and the three after that.
CACHE_SECONDS = float(os.getenv("YOUTUBE_CACHE_SECONDS", "600"))
# What "new" means. 364 channels carry roughly 5000 entries between them, and
# paging through those three at a time never ends. The subscriptions page
# answers "what appeared recently", so this does too.
DEFAULT_AGE_HOURS = float(os.getenv("YOUTUBE_MAX_AGE_HOURS", "24"))
# When a day is empty, widening once beats saying nothing at all.
FALLBACK_AGE_HOURS = float(os.getenv("YOUTUBE_FALLBACK_AGE_HOURS", "168"))

mcp = FastMCP("YouTube", host=MCP_HOST, port=MCP_PORT, stateless_http=True)


@dataclass(frozen=True)
class Video:
    title: str
    channel: str
    video_id: str
    published: dt.datetime

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


class _Feed:
    """The merged feed, and how far through it the conversation has read."""

    def __init__(self):
        self.videos: list[Video] = []
        self.fetched_at = 0.0
        self.cursor = 0
        self.offered: list[Video] = []
        self.window: list[Video] = []
        self.window_hours = 0.0


_state = _Feed()


def _ago(when: dt.datetime) -> str:
    """A spoken age. "2026-09-05T09:12:00+00:00" is not something to say aloud."""
    seconds = (dt.datetime.now(dt.timezone.utc) - when).total_seconds()
    if seconds < 3600:
        minutes = max(1, int(seconds // 60))
        return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    days = int(seconds // 86400)
    if days < 14:
        return f"{days} day{'s' if days > 1 else ''} ago"
    weeks = days // 7
    return f"{weeks} week{'s' if weeks > 1 else ''} ago"


# An Atom feed needs neither a doctype nor entity declarations, and those are
# how XML parsers are made to eat memory ("billion laughs") or reach for local
# files. Refusing them at the door is cheaper and more certain than a
# defusedxml dependency for one parse of one known feed shape.
_XML_BOMB = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)
# A channel feed is ~2 KB; the largest seen is well under this.
MAX_FEED_BYTES = 2_000_000


def _parse(payload: bytes) -> list[Video]:
    if len(payload) > MAX_FEED_BYTES:
        logger.warning(f"[YouTube] refusing a {len(payload)}-byte feed")
        return []
    if _XML_BOMB.search(payload):
        logger.warning("[YouTube] refusing a feed carrying a doctype or entity declaration")
        return []
    try:
        root = ElementTree.fromstring(payload)  # nosec B314 - guarded above
    except ElementTree.ParseError:
        return []
    channel = (root.findtext("a:title", namespaces=NS) or "").strip()
    videos = []
    for entry in root.findall("a:entry", NS):
        title = (entry.findtext("a:title", namespaces=NS) or "").strip()
        video_id = (entry.findtext("yt:videoId", namespaces=NS) or "").strip()
        published = (entry.findtext("a:published", namespaces=NS) or "").strip()
        if not title or not video_id:
            continue
        try:
            when = dt.datetime.fromisoformat(published)
        except ValueError:
            continue
        videos.append(Video(title, channel, video_id, when))
    return videos


# 364 subscriptions is an ordinary number, and fetching them one after another
# takes minutes. Spoken answers cannot wait that long, and these are small
# HTTPS GETs that spend nearly all their time waiting.
FEED_WORKERS = int(os.getenv("YOUTUBE_FEED_WORKERS", "16"))
FEED_TIMEOUT = float(os.getenv("YOUTUBE_FEED_TIMEOUT", "10"))
# Only the newest videos are ever read out, so old channels do not have to be
# waited on. Whatever has not arrived by now is left out of this answer.
FETCH_BUDGET = float(os.getenv("YOUTUBE_FETCH_BUDGET", "25"))


def _fetch_all(channel_ids: list[str]) -> list[Video]:
    """Fetch every feed in parallel, skipping the ones that fail or are slow.

    One unreachable channel must not silence the whole list - the point of
    asking is to hear what is new, not to hear about a 404.
    """
    import requests
    from concurrent.futures import ThreadPoolExecutor, as_completed

    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (X11; Linux x86_64; rv:154.0) Gecko/20100101 Firefox/154.0"
    adapter = requests.adapters.HTTPAdapter(pool_connections=FEED_WORKERS, pool_maxsize=FEED_WORKERS)
    session.mount("https://", adapter)

    def one(channel_id: str) -> list[Video]:
        try:
            response = session.get(FEED_URL.format(channel_id=channel_id), timeout=FEED_TIMEOUT)
        except Exception as error:  # noqa: BLE001 - one bad feed, not a bad answer
            logger.warning(f"[YouTube] {channel_id} failed: {error}")
            return []
        if response.status_code != 200:
            logger.warning(f"[YouTube] {channel_id} returned {response.status_code}")
            return []
        return _parse(response.content)

    videos: list[Video] = []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=FEED_WORKERS) as pool:
        futures = {pool.submit(one, channel_id): channel_id for channel_id in channel_ids}
        for future in as_completed(futures, timeout=None):
            videos.extend(future.result())
            if time.monotonic() - started > FETCH_BUDGET:
                logger.warning(f"[YouTube] stopped fetching after {FETCH_BUDGET:.0f}s")
                for pending in futures:
                    pending.cancel()
                break
    videos.sort(key=lambda video: video.published, reverse=True)
    logger.info(f"[YouTube] {len(videos)} videos from {len(channel_ids)} channels "
                f"in {time.monotonic() - started:.1f}s")
    return videos


async def _videos(force: bool = False) -> list[Video]:
    now = asyncio.get_running_loop().time()
    if not force and _state.videos and (now - _state.fetched_at) < CACHE_SECONDS:
        return _state.videos
    followed = [channel_id for channel_id, _ in channel_store.load()]
    if not followed:
        return []
    # requests is blocking and there may be dozens of feeds.
    _state.videos = await asyncio.to_thread(_fetch_all, followed)
    _state.fetched_at = now
    return _state.videos


def _speak(batch: list[Video], start: int) -> str:
    lines = [
        f"Video {index}: {video.title}, from {video.channel}, {_ago(video.published)}."
        for index, video in enumerate(batch, start=start + 1)
    ]
    return " ".join(lines)


def _recent(videos: list[Video], hours: float) -> list[Video]:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    return [video for video in videos if video.published >= cutoff]


def _window_label(hours: float) -> str:
    if hours <= 24:
        return "today"
    if hours <= 48:
        return "in the last two days"
    return f"in the last {int(hours // 24)} days"


async def _page(reset: bool, hours: float | None = None) -> str:
    videos = await _videos()
    if not videos:
        return (
            "No channels are being followed yet. Import a Takeout subscriptions file "
            "or follow a channel first."
        )
    if reset:
        _state.cursor = 0
        wanted = DEFAULT_AGE_HOURS if hours is None else max(1.0, float(hours))
        window = _recent(videos, wanted)
        if not window and hours is None:
            # A quiet day is not an error, but neither is silence an answer.
            wanted = FALLBACK_AGE_HOURS
            window = _recent(videos, wanted)
        _state.window = window
        _state.window_hours = wanted
        if not window:
            return f"Nothing new {_window_label(wanted)}."

    window = _state.window or videos
    batch = window[_state.cursor:_state.cursor + PAGE_SIZE]
    if not batch:
        _state.cursor = 0
        return f"That is everything from {_window_label(_state.window_hours)}."
    start = _state.cursor
    _state.cursor += len(batch)
    _state.offered = batch
    remaining = len(window) - _state.cursor
    tail = " Say which one to play, or ask for more." if remaining > 0 else " Say which one to play."
    head = ""
    if start == 0:
        head = f"{len(window)} new {'video' if len(window) == 1 else 'videos'} {_window_label(_state.window_hours)}. "
    return head + _speak(batch, start) + tail


@mcp.tool()
async def list_new_videos(since_hours: float = 0) -> str:
    """The newest videos from the channels the user follows, three at a time.

    Covers the last day by default, like the subscriptions page - not the
    whole backlog. `since_hours` widens it when the user asks for a longer
    stretch: "this week" is 168.

    Use when asked what is new, what to watch, or for their subscriptions.
    Read the result out as it is and then stop: the user chooses one, asks
    for more, or says nothing.
    """
    return await _page(reset=True, hours=since_hours or None)


@mcp.tool()
async def more_videos() -> str:
    """The next three videos, continuing the list already read out.

    Use when the user asks for more, others, or the next ones.
    """
    return await _page(reset=False)


@mcp.tool()
async def play_video(number: int) -> str:
    """Open one of the videos just read out, by its spoken number.

    `number` is what was said aloud - "video 2" is 2 - not an index.
    """
    if not _state.offered:
        return "No videos have been read out yet."
    position = int(number) - 1 - (_state.cursor - len(_state.offered))
    if position < 0 or position >= len(_state.offered):
        first = _state.cursor - len(_state.offered) + 1
        return f"Only videos {first} to {_state.cursor} were just read out."
    video = _state.offered[position]
    if not await open_url(video.url):
        return "No browser found on this machine."
    return f"Playing {video.title}."


@mcp.tool()
async def follow_channel(target: str) -> str:
    """Follow a YouTube channel so its videos appear in the list.

    `target` can be a channel URL, an @handle, or a channel name.
    """
    target = (target or "").strip()
    if not target:
        return "Which channel?"
    resolved = await asyncio.to_thread(channel_store.resolve, target)
    if resolved is None:
        return f"Could not find a channel for {target}."
    channel_id, title = resolved
    name = title or channel_id
    if not channel_store.add(channel_id, title):
        return f"Already following {name}."
    _state.videos = []  # the merged feed is stale now
    return f"Now following {name}."


@mcp.tool()
async def followed_channels() -> str:
    """How many channels are followed, and a few of their names."""
    followed = channel_store.load()
    if not followed:
        return "No channels are being followed yet."
    names = [title or channel_id for channel_id, title in followed[:5]]
    tail = f", and {len(followed) - len(names)} more" if len(followed) > len(names) else ""
    return f"Following {len(followed)} channels: {', '.join(names)}{tail}."


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    logger.info(f"[YouTube] serving 5 tools on http://{MCP_HOST}:{MCP_PORT}/mcp")
    mcp.run(transport="streamable-http")
