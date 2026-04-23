"""Color palette + layout constants for the comparator GUI.

Single source of truth so panel/app/ruler stay visually consistent.
Dark GitHub-inspired theme.
"""
from __future__ import annotations

PALETTE: dict[str, str] = {
    "bg":            "#0d1117",
    "panel_bg":      "#161b22",
    "panel_border":  "#30363d",
    "selected":      "#58a6ff",
    "text":          "#c9d1d9",
    "text_dim":      "#8b949e",
    "plot_bg":       "#0d1117",
    "axis":          "#30363d",
    "ruler_bg":      "#161b22",
    "ruler_tick":    "#484f58",
    "ruler_major":   "#8b949e",
    "playhead":      "#58a6ff",
    "button_bg":     "#21262d",
    "button_active": "#30363d",
}

RULER_HEIGHT: int = 32
MAJOR_TICK_SEC: float = 0.5
MINOR_TICK_SEC: float = 0.1
