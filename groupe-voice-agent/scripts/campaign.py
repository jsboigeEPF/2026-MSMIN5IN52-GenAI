"""Run the 13 unique conditions (520 turns); --full-grid repeats clean three times."""

import argparse
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.lock.yaml"))
    parser.add_argument("--corpus", type=Path, default=Path("eval/corpus"))
    parser.add_argument("--noise-file", type=Path, default=Path("eval/noise/room.wav"))
    parser.add_argument("--full-grid", action="store_true")
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()
    for kind in ("white", "pink", "real"):
        levels = ["clean", "20", "10", "5", "0"]
        if kind != "white" and not args.full_grid:
            levels = levels[1:]
        for snr in levels:
            command = [
                sys.executable,
                "-m",
                "scripts.launch",
                "replay",
                str(args.corpus),
                "--config",
                str(args.config),
                "--noise",
                kind,
                "--snr",
                snr,
            ]
            if kind == "real":
                command.extend(["--noise-file", str(args.noise_file)])
            if args.mock:
                command.append("--mock")
            subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
