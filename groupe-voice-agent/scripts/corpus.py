"""Record human prompts, synthesize the second voice, or create test fixtures."""

import argparse
import csv
from pathlib import Path

import numpy as np

from voiceagent.audio.wav import resample, write_wav


def record(rows, directory: Path, seconds: float, device):
    import sounddevice as sd

    for row in rows:
        output = directory / row["file"]
        if output.exists():
            print(f"Déjà présent, conservé : {output}")
            continue
        input(
            f"\n{row.get('reference', row.get('instruction', ''))}\nEntrée pour enregistrer : "
        )
        samples = sd.rec(
            int(seconds * 16000), samplerate=16000, channels=1, dtype="float32", device=device
        )
        sd.wait()
        write_wav(output, samples[:, 0])
        print(f"Écrit : {output}")


def synthesize(rows, directory: Path, model: Path):
    from piper import PiperVoice

    voice = PiperVoice.load(str(model))
    for row in rows:
        output = directory / row["file"]
        if output.exists():
            continue
        chunks = list(voice.synthesize(row["reference"]))
        samples = np.concatenate([chunk.audio_float_array for chunk in chunks])
        samples = resample(samples, chunks[0].sample_rate, 16000)
        write_wav(output, samples)
        print(f"Écrit : {output}")


def fixtures(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    samples = (0.2 * np.sin(2 * np.pi * 180 * np.arange(8192) / 16000)).astype(np.float32)
    write_wav(directory / "tone.wav", samples)
    (directory / "refs.csv").write_text(
        "file,reference,source\ntone.wav,Question de simulation.,simulated-tone\n",
        encoding="utf-8",
    )
    write_wav(directory / "interrupt.wav", np.full(8192, 0.3, dtype=np.float32))
    write_wav(directory / "cough.wav", np.full(2048, 0.3, dtype=np.float32))
    (directory / "interruptions.csv").write_text(
        "file,expected,experiment\ninterrupt.wav,true,E3\ncough.wav,false,E4\n",
        encoding="utf-8",
    )
    print("Fixtures techniques créées. Ce ne sont PAS des enregistrements de parole.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["record", "synthesize", "fixtures", "noise"])
    parser.add_argument("--directory", type=Path, default=Path("eval/corpus"))
    parser.add_argument("--manifest", default="refs.csv")
    parser.add_argument("--seconds", type=float, default=6)
    parser.add_argument("--device")
    parser.add_argument("--model", type=Path, default=Path("models/fr_FR-upmc-medium.onnx"))
    args = parser.parse_args()
    if args.action == "fixtures":
        fixtures(args.directory)
        return
    if args.seconds <= 0:
        parser.error("--seconds doit être positif")
    if args.action == "noise":
        record(
            [{"file": "room.wav", "instruction": "Garder la pièce silencieuse"}],
            args.directory,
            args.seconds,
            args.device,
        )
        return
    with (args.directory / args.manifest).open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if args.action == "record":
        rows = [row for row in rows if row.get("source", "human") == "human"]
        record(rows, args.directory, args.seconds, args.device)
    else:
        rows = [row for row in rows if row.get("source") == "synthetic"]
        synthesize(rows, args.directory, args.model)


if __name__ == "__main__":
    main()
