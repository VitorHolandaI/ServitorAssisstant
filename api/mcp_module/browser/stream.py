"""Opening things in the browser, by voice.

Firefox is asked through its own command line rather than through any
automation protocol: `firefox <url>` hands the URL to the already-running
instance and opens a tab, which is exactly what "hey oracle, open YouTube"
means and needs no extension, no marionette port and no profile surgery.

Only http and https are accepted. A voice transcript is untrusted text - it
is whatever the room sounded like - and `file://`, `javascript:` and friends
have no business being reachable from it.
"""
from __future__ import annotations

import logging
import os
import re
from urllib.parse import quote_plus, urlparse

from mcp.server.fastmcp import FastMCP

from mcp_module.browser.open_url import open_url

logger = logging.getLogger(__name__)

MCP_HOST = os.getenv("BROWSER_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("BROWSER_MCP_PORT", "8005"))

SEARCH_URL = os.getenv("BROWSER_SEARCH_URL", "https://duckduckgo.com/?q={query}")
YOUTUBE_SEARCH_URL = "https://www.youtube.com/results?search_query={query}"
YOUTUBE_MUSIC_URL = "https://music.youtube.com/search?q={query}"

mcp = FastMCP("Browser", host=MCP_HOST, port=MCP_PORT, stateless_http=True)


# "http://x", "mailto:a@b", "javascript:..." - anything that names a scheme.
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def _explicit_scheme(target: str) -> str | None:
    match = _SCHEME.match(target)
    return match.group(0)[:-1].lower() if match else None


def _looks_like_url(target: str) -> bool:
    parsed = urlparse(target if "://" in target else f"https://{target}")
    return bool(parsed.netloc) and "." in parsed.netloc and " " not in target


def _safe_url(target: str) -> str | None:
    """Normalise to an http(s) URL, or None if it is not one."""
    candidate = target if "://" in target else f"https://{target}"
    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return candidate


async def _open(url: str) -> str:
    if not await open_url(url):
        return "No browser found on this machine."
    return url


@mcp.tool()
async def open_website(target: str) -> str:
    """Open a site in the browser, or search the web for it.

    `target` is an address like "youtube.com" or a thing to look up like
    "opening hours of the museum". Use it whenever the user asks to open,
    go to, visit, or look something up on the web.
    """
    target = (target or "").strip()
    if not target:
        return "Nothing to open."
    # A scheme the user named is a request, not a search term. Refuse the ones
    # that are not the web rather than quietly searching for their text, which
    # looks like it worked and is not what was asked.
    scheme = _explicit_scheme(target)
    if scheme is not None and scheme not in ("http", "https"):
        return f"Refusing to open a {scheme} address; only web pages are allowed."
    if _looks_like_url(target):
        url = _safe_url(target)
        if url is None:
            return f"Refusing to open {target}: only http and https addresses are allowed."
        return f"Opened {await _open(url)}"
    url = SEARCH_URL.format(query=quote_plus(target))
    await _open(url)
    return f"Searching the web for {target}."


@mcp.tool()
async def search_youtube(query: str) -> str:
    """Open YouTube search results for something to watch.

    Use when the user asks to watch, find or look for a video, a song, a
    channel or a topic on YouTube.
    """
    query = (query or "").strip()
    if not query:
        return "Nothing to search for."
    await _open(YOUTUBE_SEARCH_URL.format(query=quote_plus(query)))
    return f"Opened YouTube results for {query}."


@mcp.tool()
async def search_youtube_music(query: str) -> str:
    """Open YouTube Music for a song, an album or a musician.

    Use when the user asks for music by name - "put on <artist>", "play the
    <album> album", "find <song>" - rather than for a video to watch, which
    is search_youtube.
    """
    query = (query or "").strip()
    if not query:
        return "Nothing to search for."
    await _open(YOUTUBE_MUSIC_URL.format(query=quote_plus(query)))
    return f"Opened YouTube Music for {query}."


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    logger.info(f"[Browser] serving 3 tools on http://{MCP_HOST}:{MCP_PORT}/mcp")
    mcp.run(transport="streamable-http")
