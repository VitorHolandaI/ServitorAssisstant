"""Simple TUI: one cleaner per run, compare baseline vs cleaned.

Flow:
  1. Pick input (record new or pick existing wav in samples/).
  2. Pick ONE option from a flat menu: single cleaners + preset chains.
  3. Run. Writes one cleaned wav next to the original.
     Transcribes baseline (original) + cleaned, prints both.

No "all" mode. No combinatorial jobs. Keeps RAM bounded: one Vosk
model load, two transcriptions, done.
"""
import subprocess
import sys
import time
from pathlib import Path

from stt_experiments.cleaners import CLEANERS
from stt_experiments.strategies import STRATEGIES

HERE = Path(__file__).resolve().parent
SAMPLES = HERE / "samples"
RECORD_SH = HERE / "record.sh"

PRESETS: dict[str, list[str]] = {
    "chain_fast": ["highpass", "auto_gain"],
    "chain_denoise": ["highpass", "spectral_gate", "auto_gain"],
    "chain_full": ["highpass", "spectral_gate", "trim_silence", "auto_gain"],
    "chain_noisy_room": ["bandpass_voice", "spectral_gate", "auto_gain"],
    "chain_emphasize": ["highpass", "preemphasis", "auto_gain"],
}


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    ans = input(f"{prompt}{suffix}: ").strip()
    return ans or default


def pick_input() -> Path:
    print("\n=== Input ===")
    print("  1) record new clip")
    print("  2) pick existing wav in samples/")
    choice = ask("choose", "2")
    if choice == "1":
        secs = ask("seconds", "5")
        subprocess.run(["bash", str(RECORD_SH), secs, "--clean", "passthrough"], check=True)
        wavs = sorted(SAMPLES.glob("rec_*.wav"), key=lambda p: p.stat().st_mtime)
        base = [w for w in wavs if "__" not in w.stem]
        if not base:
            sys.exit("record produced no base wav")
        return base[-1]
    wavs = sorted(
        [w for w in SAMPLES.glob("*.wav") if "__" not in w.stem],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not wavs:
        sys.exit("no base wavs in samples/")
    for i, w in enumerate(wavs[:20], 1):
        print(f"  {i}) {w.name}")
    idx = int(ask("pick", "1")) - 1
    return wavs[idx]


def build_option_list() -> list[tuple[str, list[str]]]:
    """Flat menu: single cleaners first, then preset chains. Skip passthrough."""
    opts: list[tuple[str, list[str]]] = []
    for name in CLEANERS:
        if name == "passthrough":
            continue
        opts.append((name, [name]))
    for name, steps in PRESETS.items():
        opts.append((name, steps))
    return opts


def pick_option(options: list[tuple[str, list[str]]]) -> tuple[str, list[str]]:
    print("\n=== Cleaning option ===")
    for i, (label, steps) in enumerate(options, 1):
        detail = " > ".join(steps) if len(steps) > 1 else ""
        print(f"  {i:>2}) {label}" + (f"   ({detail})" if detail else ""))
    raw = ask("choose one", "1")
    idx = int(raw) - 1
    if not 0 <= idx < len(options):
        sys.exit(f"invalid pick: {raw}")
    return options[idx]


def run_cleaner_chain(src: Path, steps: list[str], label: str) -> Path:
    out = src.with_name(f"{src.stem}__{label}.wav")
    cur = src
    t0 = time.perf_counter()
    for i, step in enumerate(steps):
        dst = out if i == len(steps) - 1 else src.with_name(
            f"{src.stem}__{label}_step{i}_{step}.wav"
        )
        CLEANERS[step](str(cur), str(dst))
        cur = dst
    print(f"# cleaned → {out.name}  ({time.perf_counter() - t0:.2f}s)")
    return out


def transcribe(name: str, wav: Path) -> None:
    stt = STRATEGIES["vosk_basic"]
    t0 = time.perf_counter()
    try:
        text = stt(str(wav))
    except Exception as e:
        text = f"<error: {e}>"
    dt = time.perf_counter() - t0
    print(f"{name:<20} {dt:>6.2f}s  {text!r}")


def main() -> None:
    wav = pick_input()
    print(f"\ninput: {wav.name}")

    label, steps = pick_option(build_option_list())
    cleaned = run_cleaner_chain(wav, steps, label)

    print("\n=== Transcription ===")
    print(f"{'which':<20} {'sec':>7}  text")
    print("-" * 80)
    transcribe("baseline", wav)
    transcribe(label, cleaned)


if __name__ == "__main__":
    main()
