"""Build ``dist/MolPOV.exe`` (single file, windowed) with PyInstaller.

Usage::

    python tools/build_exe.py

PySide6 is bundled by PyInstaller's own hook; the molecular samples and the WSL
render daemon script are added as data so the frozen app behaves like the source
checkout (``molpov.app._sample_path`` looks for ``samples/`` next to the
executable's extraction root).
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile", "--windowed",
        "--name", "MolPOV",
        "--distpath", os.path.join(ROOT, "dist"),
        "--workpath", os.path.join(ROOT, "build"),
        "--specpath", os.path.join(ROOT, "build"),
        "--add-data", os.path.join(ROOT, "samples") + os.pathsep + "samples",
        "--add-data",
        os.path.join(ROOT, "molpov", "wsl_server.py") + os.pathsep + "molpov",
        os.path.join(ROOT, "main.py"),
    ]
    print(" ".join(args))
    return subprocess.call(args)


if __name__ == "__main__":
    raise SystemExit(main())
