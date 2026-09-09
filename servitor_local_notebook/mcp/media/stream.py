"""Playback control for whatever is playing, YouTube included.

Firefox publishes the tab that is playing over MPRIS, the same D-Bus
interface Spotify and every other player uses, so one set of tools covers
"pause the video" and "next song" without caring which app is in front.

It talks to D-Bus through `busctl --json=short`, which is part of systemd and
already here. That avoids adding a Python D-Bus binding (none is installed)
and avoids playerctl (not installed either) for what is four method calls and
two property reads.

Nothing here can start playback of a particular thing - MPRIS has no "play
this video" - so finding something to watch belongs to the browser server's
`search_youtube`, and these tools drive it once it is playing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

MCP_HOST = os.getenv("MEDIA_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MEDIA_MCP_PORT", "8006"))

MPRIS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS_PATH = "/org/mpris/MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"

mcp = FastMCP("Media", host=MCP_HOST, port=MCP_PORT, stateless_http=True)


async def _busctl(*args: str) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        "busctl", "--user", "--json=short", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await process.communicate()
    return process.returncode or 0, (out or b"").decode("utf-8", "replace").strip()


async def _players() -> list[str]:
    """MPRIS bus names, browsers first.

    A browser tab is what "the video" means here; a music player that happens
    to also be running should not win the ambiguity.
    """
    process = await asyncio.create_subprocess_exec(
        "busctl", "--user", "list", "--acquired", "--no-pager",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await process.communicate()
    names = [
        line.split()[0]
        for line in (out or b"").decode("utf-8", "replace").splitlines()
        if line.startswith(MPRIS_PREFIX)
    ]
    browsers = [n for n in names if "firefox" in n.lower() or "chrom" in n.lower()]
    return browsers + [n for n in names if n not in browsers]


async def _first_player() -> str | None:
    players = await _players()
    return players[0] if players else None


async def _command(method: str, spoken: str) -> str:
    player = await _first_player()
    if player is None:
        return "Nothing is playing."
    code, output = await _busctl("call", player, MPRIS_PATH, PLAYER_IFACE, method)
    if code != 0:
        logger.warning(f"[Media] {method} failed: {output}")
        return f"Could not {spoken}."
    logger.info(f"[Media] {method} on {player}")
    return f"{spoken.capitalize()}."


def _metadata_text(payload: str) -> tuple[str, str]:
    """Pull title and artist out of the MPRIS metadata dictionary."""
    try:
        data = json.loads(payload).get("data", {})
    except json.JSONDecodeError:
        return "", ""
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        return "", ""

    def value(key: str) -> str:
        entry = data.get(key)
        if isinstance(entry, dict):
            entry = entry.get("data")
        if isinstance(entry, list):
            entry = ", ".join(str(item) for item in entry)
        return str(entry or "").strip()

    return value("xesam:title"), value("xesam:artist")


@mcp.tool()
async def now_playing() -> str:
    """What is playing right now. No arguments."""
    player = await _first_player()
    if player is None:
        return "Nothing is playing."
    code, payload = await _busctl("get-property", player, MPRIS_PATH, PLAYER_IFACE, "Metadata")
    if code != 0:
        return "Nothing is playing."
    title, artist = _metadata_text(payload)
    if not title:
        return "Something is playing, but it does not say what."
    return f"{title} by {artist}." if artist else f"{title}."


@mcp.tool()
async def pause_playback() -> str:
    """Pause or resume whatever is playing, including a YouTube video."""
    return await _command("PlayPause", "paused or resumed playback")


@mcp.tool()
async def next_track() -> str:
    """Skip to the next video or song."""
    return await _command("Next", "skipped to the next one")


@mcp.tool()
async def previous_track() -> str:
    """Go back to the previous video or song."""
    return await _command("Previous", "went back to the previous one")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    logger.info(f"[Media] serving 4 tools on http://{MCP_HOST}:{MCP_PORT}/mcp")
    mcp.run(transport="streamable-http")
