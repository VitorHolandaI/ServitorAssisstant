"""Which accelerator a stage is allowed to run on.

This machine has one integrated GPU and no discrete one. The compositor draws
on it, and voxtype runs its Whisper on it through Vulkan. Adding a language
model on top of that hung the device:

    i915 0000:00:02.0: [drm] GPU HANG: ecode 12:10:cd64c000, in python [611668]
    i915 0000:00:02.0: [drm] python[611668] context reset due to GPU hang

The desktop survived the reset but stayed degraded until the session was
restarted. So the GPU is refused whenever it is also driving a display, and
the work goes to the NPU, which is a separate engine and contends with
neither. `EAR_ALLOW_SHARED_GPU=true` overrides this for someone who means it.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DRM_ROOT = Path("/sys/class/drm")
FALLBACK_DEVICE = "NPU"


def connected_displays(drm_root: Path = DRM_ROOT) -> list[str]:
    """Names of DRM connectors reporting a plugged-in display."""
    if not drm_root.is_dir():
        return []
    connected = []
    for status_file in sorted(drm_root.glob("card*/card*-*/status")):
        try:
            if status_file.read_text(encoding="utf-8").strip() == "connected":
                connected.append(status_file.parent.name)
        except OSError:
            continue
    return connected


def allow_shared_gpu() -> bool:
    return os.getenv("EAR_ALLOW_SHARED_GPU", "").strip().lower() == "true"


def guard_device(device: str, stage: str, drm_root: Path = DRM_ROOT) -> str:
    """Return the device this stage should actually use.

    Anything other than the GPU passes straight through: the NPU is a separate
    engine, and the CPU cannot hang a display.
    """
    device = device.strip().upper()
    if device != "GPU":
        return device
    if allow_shared_gpu():
        logger.warning(
            f"[Devices] {stage} on the GPU because EAR_ALLOW_SHARED_GPU=true. "
            f"This GPU also drives the desktop; a hang here takes the session with it."
        )
        return device

    displays = connected_displays(drm_root)
    if not displays:
        return device

    logger.error(
        f"[Devices] refusing to run {stage} on the GPU: it is driving "
        f"{', '.join(displays)}. A model sharing the compositor's GPU has hung it "
        f"before (i915 GPU HANG, context reset). Using {FALLBACK_DEVICE} instead; "
        f"set EAR_ALLOW_SHARED_GPU=true to override."
    )
    return FALLBACK_DEVICE
