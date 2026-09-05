"""The list of channels to watch, and where it comes from.

YouTube publishes a public Atom feed per channel and needs no key or login
for it, so the only thing that has to be obtained once is the list of channel
IDs. Two ways in: a Google Takeout subscriptions export, or following a
channel one at a time from its page.

Kept as a plain CSV under ~/.config so it can be read, edited and backed up
without this code.
"""
from __future__ import annotations

import csv
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

CHANNELS_FILE = Path(
    os.getenv("YOUTUBE_CHANNELS_FILE", "~/.config/servitor/youtube-channels.csv")
).expanduser()

CHANNEL_ID = re.compile(r"^UC[\w-]{22}$")
# The page states its own id in a meta tag. Scraping "channelId" out of the
# body instead also matches the recommended channels alongside it.
IDENTITY = re.compile(r'<meta itemprop="identifier" content="(UC[\w-]{22})"')
PAGE_TITLE = re.compile(r'<meta property="og:title" content="([^"]*)"')


def load() -> list[tuple[str, str]]:
    """Every followed channel as (channel_id, title)."""
    if not CHANNELS_FILE.is_file():
        return []
    channels: list[tuple[str, str]] = []
    seen: set[str] = set()
    with CHANNELS_FILE.open(newline="", encoding="utf-8") as handle:
        for row in csv.reader(handle):
            if not row or not CHANNEL_ID.match(row[0].strip()):
                continue  # header, blank line, or something hand-edited badly
            channel_id = row[0].strip()
            if channel_id in seen:
                continue
            seen.add(channel_id)
            channels.append((channel_id, (row[1].strip() if len(row) > 1 else "")))
    return channels


def save(channels: list[tuple[str, str]]) -> None:
    CHANNELS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CHANNELS_FILE.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["channel_id", "title"])
        writer.writerows(channels)
    logger.info(f"[YouTube] {len(channels)} channel(s) in {CHANNELS_FILE}")


def add(channel_id: str, title: str) -> bool:
    """Follow a channel. False if it was already followed."""
    channels = load()
    if any(existing == channel_id for existing, _ in channels):
        return False
    channels.append((channel_id, title))
    save(channels)
    return True


def import_takeout(path: Path) -> tuple[int, int]:
    """Read a Takeout subscriptions.csv. Returns (added, already known).

    The export's columns are Channel Id, Channel Url, Channel Title, but the
    header has been renamed before now, so rows are matched on the shape of
    the id rather than on a column name.
    """
    existing = {channel_id for channel_id, _ in load()}
    found: list[tuple[str, str]] = []
    with Path(path).expanduser().open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.reader(handle):
            ids = [cell.strip() for cell in row if CHANNEL_ID.match(cell.strip())]
            if not ids:
                continue
            title = next(
                (cell.strip() for cell in row
                 if cell.strip() and not CHANNEL_ID.match(cell.strip()) and "://" not in cell),
                "",
            )
            found.append((ids[0], title))

    added = [(cid, title) for cid, title in found if cid not in existing]
    if added:
        save(load() + added)
    return len(added), len(found) - len(added)


def resolve(target: str) -> tuple[str, str] | None:
    """Turn a channel URL, @handle or bare id into (channel_id, title)."""
    import requests

    target = target.strip()
    if CHANNEL_ID.match(target):
        return target, ""
    if target.startswith("@"):
        url = f"https://www.youtube.com/{target}"
    elif "youtube.com" in target:
        url = target if target.startswith("http") else f"https://{target}"
    else:
        url = f"https://www.youtube.com/@{target.replace(' ', '')}"

    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:154.0) Gecko/20100101 Firefox/154.0"},
        timeout=20,
    )
    if response.status_code != 200:
        return None
    identity = IDENTITY.search(response.text)
    if not identity:
        return None
    title = PAGE_TITLE.search(response.text)
    return identity.group(1), (title.group(1).strip() if title else "")
