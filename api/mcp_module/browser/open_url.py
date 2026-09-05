"""Handing a URL to the browser.

Shared by the browser server and the YouTube one, so "open this" behaves the
same however it was asked for.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil

logger = logging.getLogger(__name__)


# Firefox blocks autoplay until the page is interacted with, so a video opens
# paused. Space is what a person presses; pressing it for them is the whole
# difference between "opened the video" and "played the video".
NUDGE_PLAY = os.getenv("BROWSER_NUDGE_PLAY", "true").strip().lower() != "false"
# Long enough for the page to load and take focus, short enough to still feel
# like it happened on its own.
NUDGE_DELAY = float(os.getenv("BROWSER_NUDGE_DELAY", "6"))


async def press_play() -> bool:
    """Press space in the focused window, which is how a video starts."""
    if not shutil.which("wtype"):
        return False
    await asyncio.sleep(NUDGE_DELAY)
    process = await asyncio.create_subprocess_exec(
        "wtype", "-k", "space",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await process.wait()
    logger.info("[Browser] pressed space to start playback")
    return process.returncode == 0


async def open_url(url: str, play: bool = False) -> bool:
    """Open a URL in the user's browser, reporting whether a browser existed."""
    browser = shutil.which("firefox") or shutil.which("xdg-open")
    if not browser:
        return False
    process = await asyncio.create_subprocess_exec(
        browser, url,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    # Firefox hands the URL to the running instance and exits. Starting cold it
    # stays in the foreground, which is not ours to wait on.
    try:
        await asyncio.wait_for(process.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        pass
    logger.info(f"[Browser] opened {url}")
    if play and NUDGE_PLAY:
        await press_play()
    return True
