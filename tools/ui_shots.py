"""Render the UI with a few parameter combinations (development helper)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from molpov import sdl  # noqa: E402
from molpov.app import MainWindow  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "_out")


def shot(app, window, name, **overrides):
    for key, value in overrides.items():
        target = window.params
        if key.startswith("light_"):
            setattr(target.light, key[6:], value)
        else:
            setattr(target, key, value)
    window.params.for_style()
    window.viewport.set_scene_params(window.params)
    window.viewport.update()
    window._auto_preview = False
    window._debounce.stop()
    loop = QEventLoop()
    window.worker.done.connect(lambda *_: loop.quit())
    QTimer.singleShot(50, lambda: window._submit(final=False))
    QTimer.singleShot(90000, loop.quit)
    loop.exec()
    app.processEvents()
    path = os.path.join(OUT, f"shot_{name}.png")
    window.grab().save(path)
    print("saved", path)


def main() -> None:
    app = QApplication(sys.argv[:1])
    window = MainWindow()
    window.load(os.path.join(ROOT, "samples", "caffeine.sdf"))
    window.show()

    shot(app, window, "spacefill_dark", style="spacefill", background="dark",
         material="glossy")
    shot(app, window, "wireframe_light", style="wireframe", background="light",
         palette="vmd")
    shot(app, window, "metal_pastel", style="ball_stick", palette="pastel",
         material="metal", background="studio")
    window.close()


if __name__ == "__main__":
    main()
