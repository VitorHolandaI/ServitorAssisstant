"""Run cleaning strategies + STT strategies against a wav.

For each selected cleaner, writes `<base>__<cleaner>.wav` next to the
input, then runs each selected STT strategy on it. Results printed as
a table: cleaner | stt | elapsed | text.

Two modes:
  --clean a,b,c   → run each cleaner INDEPENDENTLY on raw input.
                    Writes one wav per cleaner, one STT line per (clean, stt).
  --chain a>b>c   → run cleaners in PIPELINE (a then b then c on result of a).
                    Writes one wav: <base>__chain_a+b+c.wav.

Usage:
    uv run python -m stt_experiments.run <wav> [--clean a,b] [--chain a>b>c] [--stt x,y]
    uv run python -m stt_experiments.run samples/rec.wav --clean all
    uv run python -m stt_experiments.run samples/rec.wav --chain highpass>spectral_gate>trim_silence>normalize

Special: --clean all / --stt all = every registered one.
"""
import argparse
import sys
import time
from pathlib import Path

from stt_experiments.cleaners import CLEANERS
from stt_experiments.strategies import STRATEGIES


def _parse_list(value: str, registry: dict) -> list[str]:
    if value == "all":
        return list(registry)
    names = [n.strip() for n in value.split(",") if n.strip()]
    for n in names:
        if n not in registry:
            sys.exit(f"unknown name: {n}. known: {list(registry)}")
    return names


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("wav")
    p.add_argument("--clean", default=None,
                   help="comma-separated cleaner names (each run alone), or 'all'")
    p.add_argument("--chain", default=None,
                   help="'>'-separated cleaner names (pipeline), e.g. highpass>normalize")
    p.add_argument("--stt", default="all",
                   help="comma-separated STT strategy names, or 'all'")
    args = p.parse_args()

    in_path = Path(args.wav)
    if not in_path.is_file():
        sys.exit(f"file not found: {in_path}")

    if not args.clean and not args.chain:
        args.clean = "passthrough"

    stts = _parse_list(args.stt, STRATEGIES)
    print(f"{'cleaner':<40} {'stt':<14} {'sec':>6}  text")
    print("-" * 100)

    jobs: list[tuple[str, Path]] = []  # (label, wav_path)

    if args.clean:
        for cname in _parse_list(args.clean, CLEANERS):
            out_wav = in_path.with_name(f"{in_path.stem}__{cname}.wav")
            t0 = time.perf_counter()
            CLEANERS[cname](str(in_path), str(out_wav))
            print(f"# cleaned → {out_wav.name}  ({time.perf_counter()-t0:.2f}s)")
            jobs.append((cname, out_wav))

    if args.chain:
        names = [n.strip() for n in args.chain.split(">") if n.strip()]
        for n in names:
            if n not in CLEANERS:
                sys.exit(f"unknown cleaner in chain: {n}")
        label = "chain_" + "+".join(names)
        final_wav = in_path.with_name(f"{in_path.stem}__{label}.wav")
        src = in_path
        t0 = time.perf_counter()
        for i, n in enumerate(names):
            dst = (
                final_wav if i == len(names) - 1
                else in_path.with_name(f"{in_path.stem}__{label}_step{i}_{n}.wav")
            )
            CLEANERS[n](str(src), str(dst))
            src = dst
        print(f"# chain → {final_wav.name}  ({time.perf_counter()-t0:.2f}s)")
        jobs.append((label, final_wav))

    for label, wav in jobs:
        for sname in stts:
            t0 = time.perf_counter()
            try:
                text = STRATEGIES[sname](str(wav))
            except Exception as e:
                text = f"<error: {e}>"
            dt = time.perf_counter() - t0
            print(f"{label:<40} {sname:<14} {dt:>6.2f}  {text!r}")


if __name__ == "__main__":
    main()
