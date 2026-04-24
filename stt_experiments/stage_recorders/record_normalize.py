"""Record audio or reuse a wav, then run the normalize cleaner."""
from stt_experiments.stage_recorders.common import run_program


def main() -> None:
    run_program(
        cleaner_name="normalize",
        description="Record audio and write a normalized wav output.",
    )


if __name__ == "__main__":
    main()
