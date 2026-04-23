"""Time ruler widget: tk.Canvas with tick marks + movable playhead.

Ticks: every MINOR_TICK_SEC (minor) and MAJOR_TICK_SEC (major, labeled).
Padding lets caller align the ruler with the matplotlib plot area.
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable

from .palette import MAJOR_TICK_SEC, MINOR_TICK_SEC, PALETTE, RULER_HEIGHT


SeekCallback = Callable[[float], None]


class TimeRuler:
    def __init__(
        self,
        parent: tk.Widget,
        duration: float,
        on_seek: SeekCallback,
    ) -> None:
        if duration <= 0:
            raise ValueError(
                f"TimeRuler duration must be > 0, got {duration!r} (seconds)"
            )
        self.duration: float = duration
        self._on_seek: SeekCallback = on_seek
        self._left_pad: int = 0
        self._right_pad: int = 0
        self._playhead_line: int | None = None

        self.canvas: tk.Canvas = tk.Canvas(
            parent,
            height=RULER_HEIGHT,
            bg=PALETTE["ruler_bg"],
            highlightthickness=0,
            bd=0,
        )
        self.canvas.bind("<Configure>", self._redraw)
        self.canvas.bind("<Button-1>", self._handle_click)

    def pack(self, **kwargs: object) -> None:
        self.canvas.pack(**kwargs)

    def set_padding(self, left: int, right: int) -> None:
        """Match the matplotlib axes inset so tick t=0 lines up with the plot."""
        self._left_pad = left
        self._right_pad = right
        self._redraw(None)

    def set_playhead(self, position_sec: float) -> None:
        if self._playhead_line is None:
            return
        pos = _clamp(position_sec, 0.0, self.duration)
        x = self._time_to_x(pos)
        self.canvas.coords(self._playhead_line, x, 0, x, RULER_HEIGHT)

    def destroy(self) -> None:
        self.canvas.destroy()

    def _time_to_x(self, t: float) -> float:
        width = self.canvas.winfo_width()
        plot_width = max(width - self._left_pad - self._right_pad, 1)
        return self._left_pad + (t / self.duration) * plot_width

    def _handle_click(self, event: tk.Event) -> None:
        width = self.canvas.winfo_width()
        plot_width = max(width - self._left_pad - self._right_pad, 1)
        frac = _clamp((event.x - self._left_pad) / plot_width, 0.0, 1.0)
        self._on_seek(frac * self.duration)

    def _redraw(self, _event: tk.Event | None) -> None:
        self.canvas.delete("all")
        if self.canvas.winfo_width() <= 1:
            return
        self._draw_ticks()
        self._playhead_line = self._draw_playhead()

    def _draw_ticks(self) -> None:
        t = 0.0
        while t <= self.duration + 1e-6:
            self._draw_single_tick(t)
            t += MINOR_TICK_SEC

    def _draw_single_tick(self, t: float) -> None:
        is_major = _is_multiple(t, MAJOR_TICK_SEC)
        x = self._time_to_x(t)
        color = PALETTE["ruler_major"] if is_major else PALETTE["ruler_tick"]
        height = 10 if is_major else 5
        width = 2 if is_major else 1
        self.canvas.create_line(x, 0, x, height, fill=color, width=width)
        if is_major:
            self.canvas.create_text(
                x, 14, text=f"{t:.1f}s",
                fill=PALETTE["ruler_major"], anchor="n",
                font=("TkDefaultFont", 8),
            )

    def _draw_playhead(self) -> int:
        x = self._time_to_x(0.0)
        return self.canvas.create_line(
            x, 0, x, RULER_HEIGHT, fill=PALETTE["playhead"], width=2,
        )


def _clamp(value: float, lo: float, hi: float) -> float:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _is_multiple(value: float, step: float) -> bool:
    ratio = value / step
    return abs(ratio - round(ratio)) < 1e-6
