"""Turn a written answer into something worth hearing.

A model told not to use markdown mostly obeys, and then returns

    You have the following pending tasks:
    - **Post Cluster ARM vs X86** (due 2026-05-10)

Read aloud, the asterisks and hyphens are either pronounced or produce odd
pauses, and a numbered list loses its shape entirely once it is a single
stream of speech. The prompt is the wrong place to enforce this - it is a
request, and a small model grants it unevenly. Stripping it after the fact
always works.

This only touches presentation. No word the model chose is removed.
"""
from __future__ import annotations

import re

# Fenced blocks first: their contents are not prose and must go whole.
_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]*)`")
# **bold**, __bold__, *italic*, _italic_ - keep the word, drop the marker.
_EMPHASIS = re.compile(r"(\*{1,3}|_{1,3})(\S(?:.*?\S)?)\1", re.DOTALL)
# [label](url) reads as the label; a bare url is unspeakable either way.
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_BULLET = re.compile(r"^[ \t]*[-*+•][ \t]+", re.MULTILINE)
_NUMBERED = re.compile(r"^[ \t]*\d+[.)][ \t]+", re.MULTILINE)
_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]*", re.MULTILINE)
_RULE = re.compile(r"^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$", re.MULTILINE)
_BLOCKQUOTE = re.compile(r"^[ \t]*>[ \t]?", re.MULTILINE)
# Anything outside the Basic Multilingual Plane is an emoji or a symbol here.
_ASTRAL = re.compile(r"[\U00010000-\U0010FFFF]")
_MISC_SYMBOLS = re.compile(r"[←-⇿⌀-⏿■-➿️‍]")
_BLANK_RUN = re.compile(r"\n{2,}")
_SPACE_RUN = re.compile(r"[ \t]{2,}")


def to_spoken(text: str) -> str:
    """Strip markup and symbols that only make sense on a screen."""
    if not text:
        return ""

    spoken = _FENCE.sub(" ", text)
    spoken = _INLINE_CODE.sub(r"\1", spoken)
    spoken = _LINK.sub(r"\1", spoken)
    spoken = _RULE.sub("", spoken)
    spoken = _HEADING.sub("", spoken)
    spoken = _BLOCKQUOTE.sub("", spoken)
    # Emphasis last of the inline rules: a bullet's "*" must not be read as
    # the opening half of an italic run that never closes.
    spoken = _BULLET.sub("", spoken)
    spoken = _NUMBERED.sub("", spoken)
    spoken = _EMPHASIS.sub(r"\2", spoken)
    spoken = _ASTRAL.sub("", spoken)
    spoken = _MISC_SYMBOLS.sub("", spoken)

    # A list is separate sentences when spoken, not separate lines.
    lines = [line.strip() for line in spoken.splitlines()]
    kept = [line for line in lines if line]
    spoken = ". ".join(kept) if len(kept) > 1 else "".join(kept)
    spoken = _BLANK_RUN.sub(" ", spoken)
    spoken = _SPACE_RUN.sub(" ", spoken)
    # Joining with ". " can double a stop the model already wrote.
    spoken = re.sub(r"([.!?:;,])\.\s", r"\1 ", spoken)
    return spoken.strip()
