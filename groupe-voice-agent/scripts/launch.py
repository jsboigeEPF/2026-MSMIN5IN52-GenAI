"""Expose pip-installed CUDA shared libraries before starting the Python process."""

import os
import site
import subprocess
import sys
from pathlib import Path


def main():
    libraries = []
    for directory in site.getsitepackages():
        libraries.extend(str(path) for path in (Path(directory) / "nvidia").glob("*/lib"))
    existing = os.environ.get("LD_LIBRARY_PATH")
    if existing:
        libraries.append(existing)
    environment = dict(os.environ)
    environment["LD_LIBRARY_PATH"] = ":".join(libraries)
    result = subprocess.run(
        [sys.executable, "-m", "voiceagent", *sys.argv[1:]], env=environment
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
