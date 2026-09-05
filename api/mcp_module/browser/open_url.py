"""Handing a URL to the browser.

Shared by the browser server and the YouTube one, so "open this" behaves the
same however it was asked for.
"""
from __future__ import annotations

import asyncio
import logging
import shutil

logger = logging.getLogger(__name__)


async def open_url(url: str) -> bool:
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
    return True
