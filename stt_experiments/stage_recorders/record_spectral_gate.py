"""Record audio or reuse a wav, then run the spectral_gate cleaner."""
from stt_experiments.stage_recorders.common import run_program


def main() -> None:
    run_program(
        cleaner_name="spectral_gate",
        description=(
            "Record audio and write a spectral-gated wav output. "
            "Leave a short silence at the beginning for best results."
        ),
    )


if __name__ == "__main__":
    main()
