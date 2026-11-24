"""MolPOV - molecular structure viewer with a live POV-Ray preview.

Public API
----------
``read_structure(path)``
    Load an XYZ / PDB / MOL / SDF file into a :class:`~molpov.structure.Structure`.
``SceneParams``
    Everything the renderer needs to know: style, palette, lights, camera.
``Renderer``
    Detects a POV-Ray back end (native console build or WSL) and renders.
"""

from .structure import Atom, Bond, Structure, read_structure
from .sdl import CameraParams, LightParams, SceneParams, generate_pov
from .render import (
    PREVIEW_WIDTH,
    FINAL_WIDTH,
    Quality,
    Renderer,
    RenderOutcome,
    final_quality,
    preview_quality,
)

__version__ = "0.1.0"

__all__ = [
    "Atom",
    "Bond",
    "Structure",
    "read_structure",
    "SceneParams",
    "CameraParams",
    "LightParams",
    "generate_pov",
    "Renderer",
    "RenderOutcome",
    "Quality",
    "preview_quality",
    "final_quality",
    "PREVIEW_WIDTH",
    "FINAL_WIDTH",
]
