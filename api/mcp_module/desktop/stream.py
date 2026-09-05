"""Typing into whatever window has focus.

The wake word already produces an accurate transcript; this turns it into
keystrokes, so "hey oracle, write to window <something>" lands the text in
the editor or chat box that is focused, the way voxtype's dictation does.

Wayland has no way for an ordinary process to synthesise input, so this goes
through the same helper chain voxtype uses (`src/lib.rs`): wtype first,
falling back to the clipboard when it is unavailable. wtype talks the
virtual-keyboard protocol directly and needs no daemon; the clipboard
fallback cannot press keys, so it stages the text and says so rather than
pretending it typed.

This is deliberately the only tool here, and the server binds loopback. It
is the one tool in the set that acts on the desktop rather than reporting on
it: whatever it is given is typed wherever the cursor happens to be.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

MCP_HOST = os.getenv("DESKTOP_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("DESKTOP_MCP_PORT", "8004"))

# Long enough for a dictated sentence, short of a pasted document.
MAX_CHARS = 2000
# wtype presses each key in turn; without a gap a fast client drops
# characters in some toolkits. voxtype defaults to the same idea.
KEY_DELAY_MS = int(os.getenv("DESKTOP_TYPE_DELAY_MS", "8"))
# The window manager needs a moment to settle after the assistant's own
# window activity before the keystrokes land in the right place.
PRE_TYPE_DELAY_S = float(os.getenv("DESKTOP_PRE_TYPE_DELAY", "0.15"))

mcp = FastMCP("Desktop", host=MCP_HOST, port=MCP_PORT, stateless_http=True)


async def _run(argv: list[str], stdin: bytes | None = None) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await process.communicate(stdin)
    return process.returncode or 0, (out or b"").decode("utf-8", "replace").strip()


@mcp.tool()
async def type_into_focused_window(text: str) -> str:
    """Type text into whichever window currently has keyboard focus.

    Use for dictation: when the user asks to write, type or dictate something
    into the window they are looking at. The text is typed verbatim - do not
    add quotes, commentary or a preamble.
    """
    text = (text or "").strip()
    if not text:
        return "Nothing to type."
    if len(text) > MAX_CHARS:
        return f"Refusing to type {len(text)} characters; the limit is {MAX_CHARS}."

    if shutil.which("wtype"):
        await asyncio.sleep(PRE_TYPE_DELAY_S)
        # `--` so a transcript starting with a dash is text, not a flag.
        code, output = await _run(["wtype", "-d", str(KEY_DELAY_MS), "--", text])
        if code == 0:
            logger.info(f"[Desktop] typed {len(text)} chars")
            return f"Typed into the focused window: {text}"
        logger.warning(f"[Desktop] wtype failed ({code}): {output}")

    if shutil.which("wl-copy"):
        code, output = await _run(["wl-copy"], text.encode("utf-8"))
        if code == 0:
            return f"Could not type, so the text is on the clipboard - press paste: {text}"
        logger.warning(f"[Desktop] wl-copy failed ({code}): {output}")

    return "No way to type on this session: install wtype, or wl-clipboard for the clipboard fallback."


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    logger.info(f"[Desktop] serving 1 tool on http://{MCP_HOST}:{MCP_PORT}/mcp")
    mcp.run(transport="streamable-http")
