"""Which MCP tools an agent is allowed to see.

Every MCP server offers everything it has. That suits the server, which runs a
large model and answers typed questions. It does not suit this laptop: the
local agent runs qwen3-4b, and handing a small model all 32 schemas costs
context it needs for the answer, while widening what a misheard sentence can
reach.

A profile is an allow-list of tool names. `full` is everything, which is what
the server wants. `local` is the spoken subset: read-mostly, no destructive
verbs, and no Wake-on-LAN, because a wake packet should not be one misheard
word away.

The one tool here that acts rather than reports is
`write_to_desktop`, which is the point of it - dictation only works
if the words reach the window. It types wherever the cursor is, so what is
focused when you speak is what receives it.

Names that a profile lists but no server offers are reported rather than
ignored - a typo in a profile would otherwise silently shrink the agent.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

DEFAULT_PROFILE = "full"

# Spoken commands, and only the ones worth speaking. Deliberately absent:
# local (sqlite) tasks, which duplicate Nextcloud; both Wake-on-LAN tools,
# because a wake packet should not be one misheard word away; the four
# arithmetic tools, which a language model does not need help with; and
# help/task_help/default_response, which only describe tools the agent is
# already handed as schemas.
#
# Nextcloud comes from the slim server (four tools, shortened answers) rather
# than the ten-tool surface on :8001.
LOCAL_TOOLS = frozenset({
    # weather
    "get_forecast",
    "get_weekly_forecast",
    # nextcloud, slim
    "nextcloud_today",
    "nextcloud_pending_tasks",
    "nextcloud_add_task",
    "nextcloud_finish_task",
    # dev activity
    "summarize_weekly_dev_activity",
    # dictation into the focused window
    "write_to_desktop",
    # browser and playback
    "open_website",
    "search_youtube",
    "search_youtube_music",
    "now_playing",
    "pause_playback",
    "next_track",
    "previous_track",
})

# None means "no allow-list": take whatever the servers offer.
PROFILES: dict[str, frozenset[str] | None] = {
    "full": None,
    "local": LOCAL_TOOLS,
}


def select_tools(tools: list, profile: str = DEFAULT_PROFILE) -> list:
    """Return the tools this profile admits, in the order they were loaded."""
    name = (profile or DEFAULT_PROFILE).strip().lower()
    if name not in PROFILES:
        logger.warning(
            f"[Profiles] unknown profile {profile!r}; known: {sorted(PROFILES)}. "
            f"Falling back to {DEFAULT_PROFILE!r}."
        )
        name = DEFAULT_PROFILE

    allowed = PROFILES[name]
    if allowed is None:
        logger.info(f"[Profiles] {name}: all {len(tools)} tools")
        return list(tools)

    kept = [tool for tool in tools if tool.name in allowed]
    missing = sorted(allowed - {tool.name for tool in tools})
    if missing:
        logger.warning(f"[Profiles] {name} asks for tools no server offers: {missing}")
    logger.info(f"[Profiles] {name}: {len(kept)} of {len(tools)} tools")
    return kept
