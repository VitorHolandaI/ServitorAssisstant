"""A small Nextcloud MCP server, sized for a spoken assistant.

`stream2.py` exposes ten Nextcloud tools covering the full CalDAV surface:
create, get, list, update, complete, delete, move, reminders, events and an
agenda sync. That is right for a typed session against a large model. It is
wrong here - the laptop runs qwen3-4b, every schema it never uses is context
spent, and half those verbs destroy data on a misheard sentence.

This server keeps four: what is on today, what is pending, add one, finish
one. The heavy client is shared with `stream2.py` rather than reimplemented,
so there is one place where Nextcloud behaviour lives.

Answers are shortened on purpose. The full list tool returns roughly 1.3 KB
for ten tasks - descriptions, UIDs, list names. Spoken back, almost none of
that is wanted, and it crowds a small model's context.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from mcp_module.stremable_http.nextcloud_tasks import (
    NextcloudError,
    NextcloudTasksClient,
)

# Run as a module from `api/`, so `mcp_module` is already importable:
#     python -m mcp_module.nextcloud_slim.stream
PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")

logger = logging.getLogger(__name__)

# Loopback by default. The full server binds 0.0.0.0 because it is reached
# from another machine; this one only ever serves the agent in this process
# tree, and it can create and complete real tasks.
MCP_HOST = os.getenv("NEXTCLOUD_SLIM_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("NEXTCLOUD_SLIM_MCP_PORT", "8003"))

mcp = FastMCP("NextcloudSlim", host=MCP_HOST, port=MCP_PORT, stateless_http=True)

# One spoken answer's worth. Past this the reply stops being listenable.
SPOKEN_LIMIT = 6

_client: NextcloudTasksClient | None = None


def _nextcloud() -> NextcloudTasksClient:
    global _client
    if _client is None:
        _client = NextcloudTasksClient.from_env()
    return _client


def _condense(report: str, limit: int = SPOKEN_LIMIT) -> str:
    """Strip the full listing down to one title per line.

    The verbose form is `[c34490b7] Title | pending | due ... | list Aprender`
    followed by an indented description. Keep the title and the due date.
    """
    lines: list[str] = []
    for raw in report.splitlines():
        if not raw.strip() or raw.startswith(" "):
            continue  # indented continuation: the description
        match = re.match(r"^\[[0-9a-f]+\]\s*(.+)$", raw.strip())
        if not match:
            continue
        fields = [f.strip() for f in match.group(1).split("|")]
        title = fields[0]
        due = next((f[4:].strip() for f in fields[1:] if f.startswith("due ")), "")
        lines.append(f"{title} (due {due})" if due else title)

    if not lines:
        return report.strip()
    shown = lines[:limit]
    tail = f"\n...and {len(lines) - len(shown)} more" if len(lines) > len(shown) else ""
    return "\n".join(shown) + tail


@mcp.tool()
async def nextcloud_today() -> str:
    """What is on the calendar today. No arguments."""
    try:
        return _nextcloud().list_events(date=None, limit=SPOKEN_LIMIT)
    except NextcloudError as error:
        return f"Could not read the Nextcloud calendar: {error}"


@mcp.tool()
async def nextcloud_pending_tasks(limit: int = SPOKEN_LIMIT) -> str:
    """The tasks still open, newest due first. Titles and due dates only."""
    try:
        report = _nextcloud().list_tasks(show_completed=False, limit=max(1, min(int(limit), 20)))
    except NextcloudError as error:
        return f"Could not read the Nextcloud tasks: {error}"
    return _condense(report, max(1, min(int(limit), 20)))


@mcp.tool()
async def nextcloud_add_task(title: str, due_at: str) -> str:
    """Add a task. `due_at` is natural language, e.g. 'tomorrow at 6 pm'."""
    try:
        return _nextcloud().create_task(title=title, due_at=due_at)
    except NextcloudError as error:
        return f"Could not create the Nextcloud task: {error}"


@mcp.tool()
async def nextcloud_finish_task(task: str) -> str:
    """Mark a task complete. `task` is its title or its short id."""
    try:
        return _nextcloud().complete_task(task=task)
    except NextcloudError as error:
        return f"Could not complete the Nextcloud task: {error}"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    logger.info(f"[NextcloudSlim] serving 4 tools on http://{MCP_HOST}:{MCP_PORT}/mcp")
    mcp.run(transport="streamable-http")
