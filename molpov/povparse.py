"""Read atoms (and a bit of scene setup) back out of a POV-Ray scene.

Only the subset that molecular exporters emit is supported: ``sphere``
primitives (one per atom) with a plain ``rgb``/``rgbt`` pigment.  Element
symbols are inferred from the pigment colour, since POV-Ray itself only stores
geometry.  Bond orders are not recovered; connectivity is perceived from
distances, exactly like an XYZ import.

:func:`read_pov_scene` additionally returns the camera, background colour and a
few lighting hints so the UI can reproduce the original framing.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple

from .structure import Atom, Structure

Vec3 = Tuple[float, float, float]


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", " ", text)


def _blocks(text: str, keyword: str) -> Iterator[str]:
    for match in re.finditer(r"\b" + keyword + r"\s*\{", text):
        depth = 0
        index = match.end() - 1
        while index < len(text):
            char = text[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    yield text[match.end():index]
                    break
            index += 1


def _first_vector(body: str) -> Optional[Tuple[float, float, float]]:
    match = re.search(
        r"<\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*,"
        r"\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*,"
        r"\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*>",
        body,
    )
    if not match:
        return None
    return tuple(float(value) for value in match.groups())  # type: ignore[return-value]


def _first_number(body: str, start: int = 0) -> Optional[float]:
    match = re.search(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", body[start:])
    return float(match.group(0)) if match else None


def _pigment(body: str) -> Tuple[float, float, float]:
    match = re.search(r"rgb[t]?\s*<\s*([^>]*)>", body)
    if match:
        values = [float(v) for v in match.group(1).split(",") if v.strip()]
        if len(values) >= 3:
            return (values[0], values[1], values[2])
    return (0.5, 0.5, 0.5)


def _cylinders(text: str) -> List[Tuple[Tuple[float, float, float],
                                        Tuple[float, float, float]]]:
    """Return ``(p1, p2)`` for every ``cylinder`` primitive in the scene."""
    result = []
    for body in _blocks(text, "cylinder"):
        vectors = _all_vectors(body)
        if len(vectors) >= 2:
            result.append((vectors[0], vectors[1]))
    return result


def _all_vectors(body: str) -> List[Tuple[float, float, float]]:
    vectors = []
    for match in re.finditer(
            r"<\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*,"
            r"\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*,"
            r"\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*>", body):
        vectors.append(tuple(float(v) for v in match.groups()))
    return vectors


def _assign_bond_orders(structure: Structure, cylinders) -> None:
    """Recover bond orders from the parallel cylinders of multi-order bonds.

    Avogadro (and MolPOV) draw a double bond as two parallel cylinders and a
    triple bond as three, so the number of distinct perpendicular offsets
    belonging to a perceived bond is its order.
    """
    if not cylinders or not structure.bonds:
        return
    positions = [(a.x, a.y, a.z) for a in structure.atoms]
    for bond in structure.bonds:
        pa = positions[bond.a]
        pb = positions[bond.b]
        axis = _sub(pb, pa)
        length = math.sqrt(sum(v * v for v in axis))
        if length < 1e-6:
            continue
        axis = tuple(v / length for v in axis)
        ref = _cross(axis, (0.0, 0.0, 1.0))
        if sum(v * v for v in ref) < 1e-6:
            ref = _cross(axis, (1.0, 0.0, 0.0))
        ref = _normalize(ref)
        offsets = []
        for p1, p2 in cylinders:
            direction = _sub(p2, p1)
            span = math.sqrt(sum(v * v for v in direction))
            if span < 1e-6:
                continue
            direction = tuple(v / span for v in direction)
            if abs(sum(direction[i] * axis[i] for i in range(3))) < 0.98:
                continue
            centre = tuple((p1[i] + p2[i]) / 2.0 for i in range(3))
            rel = _sub(centre, pa)
            t = sum(rel[i] * axis[i] for i in range(3)) / length
            if not 0.1 <= t <= 0.9:
                continue
            lateral = tuple(rel[i] - axis[i] * t * length for i in range(3))
            distance = math.sqrt(sum(v * v for v in lateral))
            if distance > 0.4:
                continue
            offsets.append(sum(lateral[i] * ref[i] for i in range(3)))
        clusters = []
        for value in sorted(offsets):
            if not clusters or value - clusters[-1][-1] > 0.07:
                clusters.append([value])
            else:
                clusters[-1].append(value)
        order = len(clusters)
        if 1 <= order <= 3:
            bond.order = float(order)


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _normalize(v):
    length = math.sqrt(sum(x * x for x in v)) or 1.0
    return tuple(x / length for x in v)


def guess_element(colour: Tuple[float, float, float], radius: float) -> str:
    """Heuristic CPK colour -> element mapping.

    Handles both linearised colours (MolPOV output) and the raw GL colours that
    Avogadro writes; greys are split into H/C by brightness.
    """
    r, g, b = colour
    high, low = max(colour), min(colour)
    if high - low < 0.16:  # grey
        brightness = (r + g + b) / 3.0
        if brightness > 0.55:
            return "H" if radius < 0.45 else "C"
        return "C"
    if r > 0.7 and g > 0.7 and b < 0.45:
        return "S"
    # Oxygen is almost pure red (green channel < 0.1, also for the raw GL
    # colours Avogadro writes); phosphorus is orange with a clear green
    # component (~0.22 linear, ~0.5 raw), so it must not fall into the O rule.
    if r > 0.55 and b < 0.4 and g < 0.10:
        return "O"
    if r > 0.55 and b < 0.35 and 0.18 <= g < 0.7:
        return "P"
    if r > 0.45 and b < 0.3 and 0.10 <= g < 0.18:
        return "Fe"
    if b >= 0.45 and r < 0.45:
        return "N"
    if g > 0.45 and r < 0.65 and b < 0.6:
        # F and Cl are both greenish; the red channel separates them (F has a
        # clear yellow component, Cl is almost pure green). Radius is not
        # reliable because the exported sphere size depends on atom_scale.
        return "F" if r >= 0.18 else "Cl"
    return "C"


@dataclass
class PovScene:
    """Everything :func:`read_pov_scene` could recover from a scene."""

    structure: Structure
    location: Optional[Vec3] = None
    target: Optional[Vec3] = None
    angle: Optional[float] = None
    background: Optional[Vec3] = None
    ambient: Optional[Vec3] = None
    parallel_lights: int = 0
    avogadro_finish: bool = False
    has_request: bool = True
    notes: List[str] = field(default_factory=list)


def _vec_keyword(body: str, keyword: str) -> Optional[Vec3]:
    match = re.search(keyword + r"\s*<\s*([^>]*)>", body)
    if not match:
        return None
    values = [float(v) for v in match.group(1).split(",") if v.strip()]
    return tuple(values[:3]) if len(values) >= 3 else None  # type: ignore[return-value]


def _number_keyword(body: str, keyword: str) -> Optional[float]:
    match = re.search(
        keyword + r"\s+(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)", body)
    return float(match.group(1)) if match else None


def read_pov_scene(text: str, source: str = "") -> PovScene:
    clean = _strip_comments(text)
    atoms: List[Atom] = []
    for body in _blocks(clean, "sphere"):
        centre = _first_vector(body)
        if centre is None:
            continue
        after = body[body.index(">") + 1:]
        radius = _first_number(after)
        if radius is None or radius <= 0:
            continue
        symbol = guess_element(_pigment(body), radius)
        atoms.append(
            Atom(symbol, centre[0], centre[1], centre[2], index=len(atoms)))
    if not atoms:
        raise ValueError("no sphere primitives found in POV-Ray file")
    structure = Structure(atoms=atoms, title="POV-Ray import", source=source)
    structure.perceive_bonds()
    _assign_bond_orders(structure, _cylinders(clean))

    scene = PovScene(structure=structure)
    camera = next(iter(_blocks(clean, "camera")), "")
    if camera:
        scene.location = _vec_keyword(camera, "location")
        scene.target = _vec_keyword(camera, "look_at")
        scene.angle = _number_keyword(camera, "angle")
        if scene.target is None and scene.location is not None:
            direction = _vec_keyword(camera, "direction")
            if direction is not None:
                scene.target = tuple(  # type: ignore[assignment]
                    scene.location[i] + direction[i] for i in range(3))
    bg = re.search(r"background\s*\{[^}]*?rgb[t]?\s*<\s*([^>]*)>", clean)
    if bg:
        values = [float(v) for v in bg.group(1).split(",") if v.strip()]
        if len(values) >= 3:
            scene.background = (values[0], values[1], values[2])
    ambient = re.search(r"ambient_light\s+rgb\s*<\s*([^>]*)>", clean)
    if ambient:
        values = [float(v) for v in ambient.group(1).split(",") if v.strip()]
        if len(values) >= 3:
            scene.ambient = (values[0], values[1], values[2])
    scene.parallel_lights = len(re.findall(r"\bparallel\b", clean))
    scene.avogadro_finish = bool(
        re.search(r"ambient\s+\.?8(?:0*)?\b", clean)
        and re.search(r"metallic\s+\.?5\b", clean))
    return scene


def camera_hint(scene: PovScene) -> Optional[dict]:
    """Translate the parsed camera into MolPOV's yaw/pitch/zoom/fov."""
    if scene.location is None or scene.target is None:
        return None
    structure = scene.structure
    centre = structure.center()
    radius = max(0.6, structure.radius())
    dx = scene.target[0] - scene.location[0]
    dy = scene.target[1] - scene.location[1]
    dz = scene.target[2] - scene.location[2]
    length = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
    fx, fy, fz = dx / length, dy / length, dz / length
    dist = math.dist(scene.location, centre)
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, -fy))))
    yaw = math.degrees(math.atan2(-fx, fz))
    return {
        "yaw": yaw % 360.0,
        "pitch": pitch,
        "zoom": max(0.5, dist / radius),
        "fov": scene.angle if scene.angle else 38.0,
    }


def read_pov(text: str, source: str = "") -> Structure:
    return read_pov_scene(text, source).structure


def replace_block(text: str, keyword: str, new_block: str) -> str:
    """Replace the first ``keyword { ... }`` block with *new_block*."""
    match = re.search(r"\b" + keyword + r"\s*\{", text)
    if not match:
        return text.rstrip() + "\n\n" + new_block + "\n"
    depth = 0
    index = match.end() - 1
    while index < len(text):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[:match.start()] + new_block + text[index + 1:]
        index += 1
    return text.rstrip() + "\n\n" + new_block + "\n"


def patch_scene(text: str, structure: Optional[Structure],
                params) -> str:
    """Swap the camera and background of an existing scene for current ones."""
    from . import sdl

    if structure is not None:
        text = replace_block(text, "camera", sdl.camera_block(structure, params))
    return replace_block(text, "background", sdl.background_block(params))
