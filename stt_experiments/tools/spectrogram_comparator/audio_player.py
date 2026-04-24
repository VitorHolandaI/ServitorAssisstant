"""Audio playback wrapped around sounddevice.OutputStream.

Separates stream lifecycle from GUI logic. Caller provides a frame
source + sample rate; player drives playback and calls back per chunk
so the UI can move the playhead.
"""
from __future__ import annotations

import threading
from typing import Callable

import numpy as np
import sounddevice as sd


FrameTick = Callable[[int], None]
FinishCallback = Callable[[], None]


class AudioPlayer:
    def __init__(self) -> None:
        self._stream: sd.OutputStream | None = None
        self._data: np.ndarray | None = None
        self._sample_rate: int = 0
        self._frame: int = 0
        self._on_tick: FrameTick | None = None
        self._on_finish: FinishCallback | None = None
        self._lock: threading.Lock = threading.Lock()
        self._playing: bool = False

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def current_frame(self) -> int:
        return self._frame

    def set_frame(self, frame: int) -> None:
        """Update resume position without starting the stream."""
        with self._lock:
            self._frame = max(0, frame)

    def start(
        self,
        data: np.ndarray,
        sample_rate: int,
        start_frame: int,
        on_tick: FrameTick,
        on_finish: FinishCallback,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError(
                f"AudioPlayer.start sample_rate must be > 0, got {sample_rate!r}"
            )
        self.stop()
        self._data = data
        self._sample_rate = sample_rate
        self._frame = max(0, min(start_frame, len(data)))
        self._on_tick = on_tick
        self._on_finish = on_finish

        self._stream = sd.OutputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            callback=self._audio_callback,
            finished_callback=self._finished_callback,
        )
        self._stream.start()
        self._playing = True

    def stop(self) -> None:
        if self._stream is None:
            self._playing = False
            return
        try:
            self._stream.stop()
        finally:
            self._stream.close()
            self._stream = None
        self._playing = False

    def _audio_callback(
        self, outdata: np.ndarray, frames: int, _time: object, status: object
    ) -> None:
        if status:
            print(status)
        with self._lock:
            if self._data is None:
                outdata.fill(0)
                raise sd.CallbackStop()
            chunk = self._data[self._frame : self._frame + frames]
            outdata.fill(0)
            outdata[: len(chunk), 0] = chunk
            self._frame += len(chunk)
            if self._on_tick is not None:
                self._on_tick(self._frame)
            if len(chunk) < frames:
                raise sd.CallbackStop()

    def _finished_callback(self) -> None:
        self._playing = False
        if self._on_finish is not None:
            self._on_finish()
