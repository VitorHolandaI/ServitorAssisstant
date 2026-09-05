"""Every MCP server, in one process.

Each server is a FastMCP instance that would otherwise run its own uvicorn in
its own Python. Measured on this machine, that floor is 59 MB per process -
17 MB of interpreter and 42 MB of fastmcp, uvicorn and starlette - and seven
of them cost 354 MB of PSS, nearly all of it that floor paid seven times.

Hosting them together keeps one copy of the floor. The addresses do not
change: each server still answers on its own port, so nothing that talks to
them needs to know this happened.

Run it as a module from `api/`:

    python -m mcp_module.host

Individual servers still run standalone (`python -m mcp_module.youtube.stream`),
which is what makes one of them easy to restart or debug on its own.
"""
from __future__ import annotations

import asyncio
import logging

import uvicorn

logger = logging.getLogger(__name__)

# Imported lazily inside main() so a broken server is reported by name rather
# than taking the whole host down with an import error at module scope.
SERVERS = (
    ("mcp_module.stremable_http.stream2", "general"),
    ("mcp_module.dev_activity.stream", "dev-activity"),
    ("mcp_module.nextcloud_slim.stream", "nextcloud"),
    ("mcp_module.desktop.stream", "desktop"),
    ("mcp_module.browser.stream", "browser"),
    ("mcp_module.media.stream", "media"),
    ("mcp_module.youtube.stream", "youtube"),
)


def _load() -> list[tuple[str, object]]:
    import importlib

    loaded = []
    for module_path, label in SERVERS:
        try:
            module = importlib.import_module(module_path)
        except Exception:  # noqa: BLE001 - one bad server must not hide the rest
            logger.exception(f"[Host] {label} failed to import; skipping it")
            continue
        loaded.append((label, module.mcp))
    return loaded


async def _serve(label: str, mcp) -> None:
    config = uvicorn.Config(
        mcp.streamable_http_app(),
        host=mcp.settings.host,
        port=mcp.settings.port,
        log_level="warning",
        access_log=False,
    )
    logger.info(f"[Host] {label} on http://{mcp.settings.host}:{mcp.settings.port}/mcp")
    await uvicorn.Server(config).serve()


async def main() -> None:
    servers = _load()
    if not servers:
        raise SystemExit("no MCP servers could be imported")
    logger.info(f"[Host] serving {len(servers)} MCP servers in one process")
    await asyncio.gather(*(_serve(label, mcp) for label, mcp in servers))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
