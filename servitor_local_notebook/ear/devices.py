"""Which accelerator a stage is allowed to run on.

This machine has one integrated GPU and no discrete one. The compositor draws
on it, and voxtype runs its Whisper on it through Vulkan. Adding a language
model on top of that hung the device twice:

    i915 0000:00:02.0: [drm] GPU HANG: ecode 12:10:cd64c000, in python [611668]
    i915 0000:00:02.0: [drm] python[611668] context reset due to GPU hang

Both coredumps have `py_openvino_genai` on the stack, so it was the model, not
voxtype. The likely mechanism is the driver's single-buffer ceiling:

    GPU_DEVICE_MAX_ALLOC_MEM_SIZE = 4294959104   # 4.00 GiB
    qwen3-8b-int4-cw-ov weights   = 4.75 GB      # over it
    qwen3-4b-int4-ov weights      = 2.29 GB      # under it, with room

So the GPU is not banned outright any more. While a display is attached it is
allowed only for a model whose weights fit inside that ceiling with margin,
which keeps the fast device available for the 4B and still refuses the 8B that
hung it. A caller that passes no `model_dir` cannot be checked, and is refused.
`EAR_ALLOW_SHARED_GPU=true` skips the whole check for someone who means it.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DRM_ROOT = Path("/sys/class/drm")
FALLBACK_DEVICE = "NPU"

# Weights are not the only GPU allocation a pipeline makes: the KV cache and
# the intermediate buffers come out of the same budget. Leave a quarter of the
# ceiling for them rather than approving anything that merely fits.
GPU_ALLOC_HEADROOM = 0.75


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


def gpu_max_alloc_bytes() -> int:
    """The largest single buffer this GPU's driver will hand out.

    Zero when it cannot be read, which the caller must treat as "unknown", not
    as "unlimited".
    """
    try:
        import openvino as ov

        return int(ov.Core().get_property("GPU", "GPU_DEVICE_MAX_ALLOC_MEM_SIZE"))
    except Exception as error:  # noqa: BLE001 - any failure here means "unknown"
        logger.warning(f"[Devices] could not read the GPU allocation ceiling: {error}")
        return 0


def model_weight_bytes(model_dir: Path) -> int:
    """Size of the model's weight files, which is what has to fit on device."""
    try:
        return sum(f.stat().st_size for f in model_dir.glob("*.bin") if f.is_file())
    except OSError:
        return 0


def fits_on_shared_gpu(model_dir: Path | None) -> tuple[bool, str]:
    """Whether this model is small enough to share the compositor's GPU."""
    if model_dir is None:
        return False, "no model directory was given to size-check"
    ceiling = gpu_max_alloc_bytes()
    if ceiling <= 0:
        return False, "the GPU allocation ceiling could not be read"
    weights = model_weight_bytes(Path(model_dir))
    if weights <= 0:
        return False, f"no weight files found under {model_dir}"
    budget = ceiling * GPU_ALLOC_HEADROOM
    detail = (
        f"weights {weights / 1e9:.2f} GB vs a {budget / 1e9:.2f} GB budget "
        f"({ceiling / 1e9:.2f} GB ceiling x {GPU_ALLOC_HEADROOM:.2f})"
    )
    return weights <= budget, detail


def guard_device(
    device: str,
    stage: str,
    drm_root: Path = DRM_ROOT,
    model_dir: Path | None = None,
) -> str:
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

    fits, detail = fits_on_shared_gpu(model_dir)
    if fits:
        logger.warning(
            f"[Devices] {stage} on the GPU while it drives {', '.join(displays)}: {detail}. "
            f"Small enough to share, but a hang here still costs the session."
        )
        return device

    logger.error(
        f"[Devices] refusing to run {stage} on the GPU: it is driving "
        f"{', '.join(displays)} and {detail}. A model that overran this GPU has hung "
        f"it before (i915 GPU HANG, context reset). Using {FALLBACK_DEVICE} instead; "
        f"set EAR_ALLOW_SHARED_GPU=true to override."
    )
    return FALLBACK_DEVICE
