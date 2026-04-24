from typing import Callable, Dict

from .vosk_basic import transcribe as vosk_basic

Strategy = Callable[[str], str]

STRATEGIES: Dict[str, Strategy] = {
    "vosk_basic": vosk_basic,
}
