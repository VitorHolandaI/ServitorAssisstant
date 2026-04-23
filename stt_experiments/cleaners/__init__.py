from typing import Callable, Dict

from .auto_gain import clean as auto_gain
from .bandpass_voice import clean as bandpass_voice
from .highpass import clean as highpass
from .normalize import clean as normalize
from .passthrough import clean as passthrough
from .preemphasis import clean as preemphasis
from .spectral_gate import clean as spectral_gate
from .trim_silence import clean as trim_silence

Cleaner = Callable[[str, str], None]

CLEANERS: Dict[str, Cleaner] = {
    "passthrough": passthrough,
    "normalize": normalize,
    "auto_gain": auto_gain,
    "highpass": highpass,
    "bandpass_voice": bandpass_voice,
    "preemphasis": preemphasis,
    "trim_silence": trim_silence,
    "spectral_gate": spectral_gate,
}
