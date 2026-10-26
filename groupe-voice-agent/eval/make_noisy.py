"""python -m eval.make_noisy clean.wav noisy.wav --noise pink --snr 10"""

import argparse
from pathlib import Path

import numpy as np

from voiceagent.audio.wav import read_wav, write_wav
from voiceagent.evaluation.noise import generate_noise, mix_noise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--noise", choices=["white", "pink"], default="pink")
    parser.add_argument("--snr", type=float, default=10)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    samples = read_wav(args.source)
    noise = generate_noise(args.noise, len(samples), np.random.default_rng(args.seed))
    mixed, metadata = mix_noise(samples, noise, args.snr)
    write_wav(args.destination, mixed)
    print(metadata)


if __name__ == "__main__":
    main()
