"""Record audio or reuse a wav, then run the highpass cleaner."""
from stt_experiments.stage_recorders.common import run_program


def main() -> None:
    run_program(
        cleaner_name="highpass",
        description="Record audio and write a highpass-filtered wav output.",
    )


if __name__ == "__main__":
    main()
