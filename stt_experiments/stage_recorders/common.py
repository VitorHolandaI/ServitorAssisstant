"""Shared CLI helpers for one-cleaner recording programs."""
from __future__ import annotations

import argparse
import subprocess
from datetime import datetime
from pathlib import Path

from stt_experiments.cleaners import CLEANERS

HERE = Path(__file__).resolve().parent
DEFAULT_OUT_DIR = HERE / "output"


def build_parser(cleaner_name: str, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "seconds",
        nargs="?",
        type=int,
        default=5,
        help="recording duration in seconds when --input is not provided",
    )
    parser.add_argument(
        "--input",
        help="existing wav to process instead of recording a new clip",
    )
    parser.add_argument(
        "--output",
        help="exact output wav path; default is <input_stem>__<cleaner>.wav",
    )
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT_DIR),
        help=f"directory for new recordings (default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--mic-card",
        help="ALSA card number; auto-detects the first USB microphone when omitted",
    )
    return parser


def detect_usb_mic_card() -> str:
    result = subprocess.run(
        ["arecord", "-l"],
        check=True,
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped.startswith("card ") or "USB" not in stripped:
            continue
        card = stripped.split(":", 1)[0].split()[1]
        return card
    raise SystemExit("no USB mic found in `arecord -l`; pass --mic-card explicitly")


def record_wav(seconds: int, out_dir: Path, mic_card: str | None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    card = mic_card or detect_usb_mic_card()
    device = f"plughw:{card},0"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_path = out_dir / f"rec_{timestamp}.wav"
    subprocess.run(
        [
            "arecord",
            "-D",
            device,
            "-f",
            "S16_LE",
            "-r",
            "16000",
            "-c",
            "1",
            "-d",
            str(seconds),
            str(raw_path),
        ],
        check=True,
    )
    return raw_path


def resolve_paths(args: argparse.Namespace, cleaner_name: str) -> tuple[Path, Path]:
    if args.input:
        input_path = Path(args.input).expanduser().resolve()
        if not input_path.is_file():
            raise SystemExit(f"input wav not found: {input_path}")
    else:
        input_path = record_wav(
            seconds=args.seconds,
            out_dir=Path(args.out_dir).expanduser().resolve(),
            mic_card=args.mic_card,
        )

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
    else:
        output_path = input_path.with_name(f"{input_path.stem}__{cleaner_name}.wav")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return input_path, output_path


def run_program(cleaner_name: str, description: str) -> None:
    parser = build_parser(cleaner_name=cleaner_name, description=description)
    args = parser.parse_args()
    input_path, output_path = resolve_paths(args, cleaner_name=cleaner_name)
    CLEANERS[cleaner_name](str(input_path), str(output_path))
    print(f"cleaner={cleaner_name}")
    print(f"input={input_path}")
    print(f"output={output_path}")
