"""Lance Omar Sy Valide, l'agent de code interactif avec validation humaine.

    python omar.py [chemin/du/depot]

Sans chemin, le dépôt cible est le dossier courant. Si les dépendances ne sont pas importables (venv non
activé), le script se relance avec le Python de `.venv/`.
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # works from any directory


def _venv_python() -> Path | None:
    for candidate in (HERE / ".venv" / "bin" / "python", HERE / ".venv" / "Scripts" / "python.exe"):
        if candidate.exists():
            return candidate
    return None


if __name__ == "__main__":
    try:
        from src.tui.app import main
    except ModuleNotFoundError as exc:
        venv = _venv_python()
        if venv is None or Path(sys.prefix).resolve() == (HERE / ".venv").resolve():  # already in the venv
            sys.exit(f"{exc}\nInstalle les dépendances : python -m venv .venv && .venv/bin/pip install -e .")
        os.execv(venv, [str(venv), str(Path(__file__).resolve()), *sys.argv[1:]])
    main()
