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
# Firefox starting up composites and decodes video on the same integrated GPU
# the language model computes on, and the two together have hung it: two of
# three i915 GPU HANGs today landed 8 and 12 seconds after a browser launch
# ordered mid-turn. Handing back at once and opening a moment later lets the
# turn finish and the model be freed first, so they do not overlap.
LAUNCH_DELAY = float(os.getenv("BROWSER_LAUNCH_DELAY", "4"))


async def _spawn(browser: str, url: str) -> None:
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


async def open_url(url: str, play: bool = False, defer: bool = False) -> bool:
    """Open a URL in the user's browser, reporting whether a browser existed.

    `defer` returns as soon as the browser is known to exist and does the
    launch on a background task. The caller is a tool inside a turn that is
    still holding the model on the GPU; starting Firefox into that is what
    hangs the device.
    """
    browser = shutil.which("firefox") or shutil.which("xdg-open")
    if not browser:
        return False
    if defer:
        asyncio.get_running_loop().create_task(_launch_later(browser, url, play))
        return True
    await _spawn(browser, url)
    logger.info(f"[Browser] opened {url}")
    if play and NUDGE_PLAY:
        await press_play()
    return True


async def _launch_later(browser: str, url: str, play: bool) -> None:
    """Open once the turn that asked for it has let go of the GPU."""
    await asyncio.sleep(LAUNCH_DELAY)
    try:
        await _spawn(browser, url)
        logger.info(f"[Browser] opened {url}")
        if play and NUDGE_PLAY:
            await press_play()
    except Exception:  # noqa: BLE001 - a failed launch must not kill the server
        logger.exception("[Browser] deferred launch failed")
