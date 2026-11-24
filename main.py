"""Frozen entry point used by ``tools/build_exe.py`` (PyInstaller)."""

from molpov.app import run

if __name__ == "__main__":
    raise SystemExit(run())
