"""Top-level Tk app: toolbar, scrollable panel list, playback wiring."""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .audio_player import AudioPlayer
from .palette import PALETTE
from .panel import SpectrogramPanel


class SpectrogramComparatorApp:
    def __init__(self, root: tk.Tk, initial_files: list[Path]) -> None:
        self.root: tk.Tk = root
        self.root.title("Spectrogram Comparator")
        self.root.geometry("1280x860")
        self.root.configure(bg=PALETTE["bg"])

        _setup_ttk_style()

        self.panels: list[SpectrogramPanel] = []
        self.selected_panel: SpectrogramPanel | None = None
        self.playback_panel: SpectrogramPanel | None = None
        self.player: AudioPlayer = AudioPlayer()

        self._build_layout()
        if initial_files:
            self.add_files(initial_files)

        self.root.bind("<space>", self._handle_space)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def add_files_dialog(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Choose WAV files",
            filetypes=[("WAV files", "*.wav"), ("All files", "*.*")],
        )
        self.add_files([Path(p) for p in paths])

    def add_files(self, paths: list[Path]) -> None:
        for path in paths:
            self._add_single_file(path)

    def clear_panels(self) -> None:
        self.stop_audio()
        for panel in self.panels:
            panel.destroy()
        self.panels.clear()
        self.selected_panel = None
        self.playback_panel = None

    def stop_audio(self) -> None:
        self.player.stop()

    def seek_and_play(self, panel: SpectrogramPanel, position_sec: float) -> None:
        self._select(panel)
        frame = max(0, min(int(position_sec * panel.sample_rate), len(panel.data)))
        if self.player.playing and self.playback_panel is panel:
            self._start_playback(panel, frame)
            return
        if self.player.playing:
            self.stop_audio()
        self.playback_panel = panel
        self.player.set_frame(frame)
        panel.set_playhead(frame / panel.sample_rate)

    def toggle_play_pause(self) -> None:
        panel = self.selected_panel or self.playback_panel
        if panel is None:
            return
        if self.player.playing:
            self.stop_audio()
            return
        start = self.player.current_frame if self.playback_panel is panel else 0
        if start >= len(panel.data):
            start = 0
        self._start_playback(panel, start)

    def on_close(self) -> None:
        self.stop_audio()
        self.scroll_canvas.unbind_all("<MouseWheel>")
        self.root.destroy()

    def _add_single_file(self, path: Path) -> None:
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            messagebox.showerror("Missing file", f"File not found:\n{resolved}")
            return
        if any(panel.wav_path == resolved for panel in self.panels):
            return
        try:
            panel = SpectrogramPanel(self.inner, resolved, on_seek=self.seek_and_play)
        except Exception as exc:
            messagebox.showerror("Load failed", f"Could not open {resolved.name}:\n{exc}")
            return
        self.panels.append(panel)

    def _select(self, panel: SpectrogramPanel) -> None:
        for item in self.panels:
            item.set_selected(item is panel)
        self.selected_panel = panel

    def _start_playback(self, panel: SpectrogramPanel, start_frame: int) -> None:
        self.playback_panel = panel
        panel.set_playhead(start_frame / panel.sample_rate)
        self.player.start(
            data=panel.data,
            sample_rate=panel.sample_rate,
            start_frame=start_frame,
            on_tick=lambda _frame: None,
            on_finish=self._on_playback_finished,
        )
        self._schedule_playhead_update()

    def _schedule_playhead_update(self) -> None:
        panel = self.playback_panel
        if panel is not None and panel.sample_rate > 0:
            panel.set_playhead(self.player.current_frame / panel.sample_rate)
        if self.player.playing:
            self.root.after(40, self._schedule_playhead_update)

    def _on_playback_finished(self) -> None:
        self.root.after(0, self._finalize_playback_ui)

    def _finalize_playback_ui(self) -> None:
        panel = self.playback_panel
        if panel is not None and panel.sample_rate > 0:
            end = min(self.player.current_frame / panel.sample_rate, panel.duration)
            panel.set_playhead(end)

    def _build_layout(self) -> None:
        self._build_toolbar()
        self._build_hint()
        self._build_scroll_area()

    def _build_toolbar(self) -> None:
        toolbar = tk.Frame(self.root, bg=PALETTE["bg"])
        toolbar.pack(fill="x", padx=18, pady=(16, 6))
        tk.Label(
            toolbar, text="Spectrogram Comparator",
            bg=PALETTE["bg"], fg=PALETTE["text"],
            font=("TkDefaultFont", 14, "bold"),
        ).pack(side="left")
        for text, cmd in (
            ("Add WAVs", self.add_files_dialog),
            ("Clear", self.clear_panels),
            ("Stop", self.stop_audio),
        ):
            ttk.Button(toolbar, text=text, command=cmd, style="Modern.TButton").pack(
                side="right", padx=(8, 0)
            )

    def _build_hint(self) -> None:
        tk.Label(
            self.root,
            text="Click spectrogram or ruler to seek and play. Space = play/pause selected.",
            bg=PALETTE["bg"], fg=PALETTE["text_dim"],
            anchor="w", font=("TkDefaultFont", 9),
        ).pack(fill="x", padx=18, pady=(0, 10))

    def _build_scroll_area(self) -> None:
        outer = tk.Frame(self.root, bg=PALETTE["bg"])
        outer.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.scroll_canvas = tk.Canvas(outer, bg=PALETTE["bg"], highlightthickness=0)
        self.scroll_canvas.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(
            outer, orient="vertical", command=self.scroll_canvas.yview,
            style="Modern.Vertical.TScrollbar",
        )
        scrollbar.pack(side="right", fill="y")
        self.scroll_canvas.configure(yscrollcommand=scrollbar.set)
        self.inner = tk.Frame(self.scroll_canvas, bg=PALETTE["bg"])
        self.window_id = self.scroll_canvas.create_window(
            (0, 0), window=self.inner, anchor="nw"
        )
        self.inner.bind("<Configure>", self._sync_scroll_region)
        self.scroll_canvas.bind("<Configure>", self._on_canvas_resize)
        self.scroll_canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _sync_scroll_region(self, _event: tk.Event) -> None:
        self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all"))

    def _on_canvas_resize(self, event: tk.Event) -> None:
        self.scroll_canvas.itemconfigure(self.window_id, width=event.width)
        for panel in self.panels:
            panel.request_ruler_realign()

    def _on_mousewheel(self, event: tk.Event) -> None:
        if event.delta:
            self.scroll_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _handle_space(self, _event: tk.Event) -> str:
        widget = self.root.focus_get()
        if isinstance(widget, (tk.Entry, tk.Text)):
            return "break"
        self.toggle_play_pause()
        return "break"


def _setup_ttk_style() -> None:
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(
        "Modern.TButton",
        background=PALETTE["button_bg"], foreground=PALETTE["text"],
        borderwidth=0, focusthickness=0, padding=(14, 6),
        font=("TkDefaultFont", 9, "bold"),
    )
    style.map(
        "Modern.TButton",
        background=[("active", PALETTE["button_active"])],
        foreground=[("active", PALETTE["selected"])],
    )
    style.configure(
        "Modern.Vertical.TScrollbar",
        background=PALETTE["panel_bg"], troughcolor=PALETTE["bg"],
        bordercolor=PALETTE["bg"], arrowcolor=PALETTE["text_dim"],
    )
