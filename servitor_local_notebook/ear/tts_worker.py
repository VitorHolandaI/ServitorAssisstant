"""Kokoro speech synthesis, run inside its own virtualenv.

kokoro-onnx requires numpy 2.x. This project pins numpy 1.26.4, and gruut —
Piper's phonemizer — caps numpy below 2.0, so the two cannot share an
interpreter. Rather than give up one of them, Kokoro lives in `.venv-tts` and
is driven through this script: one JSON request per line on stdin, one JSON
response per line on stdout, with the audio handed over as a file.

The model loads once and stays resident, so the subprocess boundary costs a
few milliseconds per reply rather than the 0.6s of a fresh load.
"""
import json
import sys
import tempfile
from pathlib import Path


def main() -> int:
    import soundfile as sf
    from kokoro_onnx import Kokoro

    model_path, voices_path = sys.argv[1], sys.argv[2]
    kokoro = Kokoro(model_path, voices_path)
    out_dir = Path(tempfile.mkdtemp(prefix="servitor-tts-"))

    # Announce readiness only once the model can actually answer.
    print(json.dumps({"ready": True}), flush=True)

    for index, line in enumerate(sys.stdin):
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            audio, sample_rate = kokoro.create(
                request["text"],
                voice=request.get("voice", "af_heart"),
                speed=float(request.get("speed", 1.0)),
                lang=request.get("lang", "en-us"),
            )
            path = out_dir / f"reply-{index}.wav"
            sf.write(str(path), audio, sample_rate)
            print(json.dumps({"path": str(path), "sample_rate": int(sample_rate)}), flush=True)
        except Exception as error:  # the ear must hear about it, not hang
            print(json.dumps({"error": f"{type(error).__name__}: {error}"}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
