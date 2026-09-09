"""Every MCP server this laptop can offer, in one process.

api/mcp_module/host.py serves the three that run anywhere. This adds the four
that need a screen: writing into the focused window, driving Firefox, MPRIS
playback, and the YouTube subscription feed. Same ports as always, so nothing
that talks to them needs to know which host started them.

Run it from the repository root:

    python -m servitor_local_notebook.mcp_host
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# The shared servers live under api/, which is a source root rather than a
# package. The ear's launcher puts it on the path; doing it here as well means
# this module runs the same way from a shell.
_API = Path(__file__).resolve().parent.parent / "api"
if str(_API) not in sys.path:
    sys.path.insert(0, str(_API))

from mcp_module import host  # noqa: E402

DESKTOP_SERVERS = (
    ("servitor_local_notebook.mcp.desktop.stream", "desktop"),
    ("servitor_local_notebook.mcp.browser.stream", "browser"),
    ("servitor_local_notebook.mcp.media.stream", "media"),
    ("servitor_local_notebook.mcp.youtube.stream", "youtube"),
)

logger = logging.getLogger(__name__)


async def main() -> None:
    host.SERVERS = host.SERVERS + DESKTOP_SERVERS
    await host.main()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    )
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
