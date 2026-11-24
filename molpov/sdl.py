"""POV-Ray scene description (SDL) generation.

The generator turns a :class:`~molpov.structure.Structure` plus a set of UI
parameters into a complete, self contained ``.pov`` file: camera, lights,
material finish, ground plane and one primitive per atom / half bond.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

from . import elements
from .structure import Structure, visible_atoms, visible_bonds

Vec3 = Tuple[float, float, float]

STYLES = ("ball_stick", "spacefill", "wireframe")
PALETTES = ("cpk", "vmd", "pastel", "custom")
MATERIALS = ("matte", "standard", "glossy", "metal", "avogadro")
BACKGROUNDS = ("studio", "dark", "light", "black", "custom", "transparent")

# A preset bundles the choices that only look right together (finish, lights,
# background, floor).  Mixing them by hand is possible, but that is exactly
# what produced odd results like "Avogadro finish + studio lights".
PRESETS = ("studio", "avogadro", "custom")

# Avogadro 1.2 lighting constants, verbatim from libavogadro/src/global.h:
#   LIGHT_AMBIENT  {0.2, 0.2, 0.2, 1.0}
#   LIGHT0_DIFFUSE {1.0, 1.0, 1.0, 1.0}  LIGHT0_POSITION { 0.8, 0.7,  1.0, 0.0}
#   LIGHT1_DIFFUSE {0.3, 0.3, 0.3, 1.0}  LIGHT1_POSITION {-0.8, 0.7, -0.5, 0.0}
# The positions are eye-space directions (w = 0) and are rotated into world
# space with the camera basis, exactly like POVPainterDevice::initializePOV().
AVOGADRO_AMBIENT = 0.2
AVOGADRO_LIGHTS = (
    ((0.8, 0.7, 1.0), 1.0),    # key: upper right, in front of the molecule
    ((-0.8, 0.7, -0.5), 0.3),  # fill: upper left, behind the molecule
)

_DEFAULT_ATOM_SCALE = {"ball_stick": 0.28, "spacefill": 1.0, "wireframe": 0.10}


@dataclass
class CameraParams:
    yaw: float = 25.0          # degrees, orbit around the molecule
    pitch: float = 18.0        # degrees above the equator
    zoom: float = 3.6          # camera distance in units of the bounding radius
    fov: float = 38.0          # vertical field of view, degrees
    # Free trackball orientation (x, y, z, w).  When set it overrides yaw/pitch,
    # so the camera can tumble past the poles instead of clamping at ±89°.
    quaternion: Optional[Tuple[float, float, float, float]] = None


@dataclass
class LightParams:
    azimuth: float = 35.0      # degrees, key light direction
    elevation: float = 42.0    # degrees
    key_intensity: float = 1.15
    fill_intensity: float = 0.35
    softness: float = 0.35     # area light radius, fraction of bounding radius
    warmth: float = 0.06       # 0 = neutral white, 1 = warm
    style: str = "studio"      # "studio" (three lights) or "avogadro" (headlights)
    headlight: bool = False    # extra parallel light fixed to the camera
    headlight_intensity: float = 0.45


@dataclass
class SceneParams:
    width: int = 960
    height: int = 540
    style: str = "ball_stick"
    palette: str = "cpk"
    material: str = "standard"
    background: str = "studio"
    atom_scale: float = 0.28
    bond_radius: float = 0.11
    radiosity: bool = False
    radiosity_count: int = 80
    camera: CameraParams = field(default_factory=CameraParams)
    light: LightParams = field(default_factory=LightParams)
    floor: bool = True
    alpha: bool = True
    max_trace_level: int = 5
    area_samples: int = 5      # NxN grid for the key area light
    jitter: bool = True        # jittered sampling -> soft, slightly grainy shadows
    transparency: float = 0.0  # 0 = opaque, up to 0.85 (rgbt transmit)
    pack_scene: bool = True    # wrap the opaque atoms + bonds in a union
    custom_colors: Dict[str, Tuple[int, int, int]] = field(default_factory=dict)
    background_color: Vec3 = (0.5, 0.5, 0.5)
    hide_hydrogens: bool = False
    full_command: bool = False  # render with Avogadro's complete switch set
    antialias: bool = True
    aa_threshold: float = 0.25
    quality: int = 9           # POV-Ray +Q (0..11)

    def for_style(self) -> "SceneParams":
        """Return a copy whose atom scale matches the selected style."""
        if self.style == "spacefill":
            return replace(self, atom_scale=1.0, bond_radius=0.0)
        if self.style == "wireframe":
            return replace(self, atom_scale=0.10, bond_radius=0.05)
        return self

    @property
    def aspect(self) -> float:
        return self.width / max(1.0, float(self.height))


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _vec(v: Vec3) -> str:
    return f"<{v[0]:.5f}, {v[1]:.5f}, {v[2]:.5f}>"


def _linear(symbol: str, palette: str,
            custom: Optional[Dict[str, Tuple[int, int, int]]] = None) -> Vec3:
    return elements.linear_rgb(symbol, palette, custom)


def _finish(name: str) -> str:
    if name == "avogadro":
        # Copied verbatim from Avogadro 1.x POVPainterDevice::initializePOV()
        # (GPL-2.0 upstream; re-created here as data, not copied code).
        return (
            "finish { ambient 0.8 diffuse 1.0 specular 1.0 roughness 0.005 "
            "metallic 0.5 }"
        )
    if name == "matte":
        return "finish { ambient 0 diffuse 0.92 specular 0.06 roughness 0.08 }"
    if name == "glossy":
        return (
            "finish { ambient 0 diffuse 0.45 specular 0.75 roughness 0.004 "
            "reflection { 0.12 fresnel on } }"
        )
    if name == "metal":
        return (
            "finish { ambient 0 diffuse 0.32 metallic 0.85 specular 0.65 "
            "roughness 0.02 reflection { 0.35 } }"
        )
    return (
        "finish { ambient 0 diffuse 0.62 specular 0.38 roughness 0.015 "
        "reflection { 0.04 } }"
    )


def _camera_location(center: Vec3, radius: float, cam: CameraParams) -> Vec3:
    dist = max(0.2, radius * max(0.5, cam.zoom))
    if cam.quaternion is not None:
        _, _, forward = camera_basis(cam)
        return (center[0] - dist * forward[0],
                center[1] - dist * forward[1],
                center[2] - dist * forward[2])
    yaw = math.radians(cam.yaw)
    pitch = math.radians(max(-89.0, min(89.0, cam.pitch)))
    return (
        center[0] + dist * math.cos(pitch) * math.sin(yaw),
        center[1] + dist * math.sin(pitch),
        center[2] - dist * math.cos(pitch) * math.cos(yaw),
    )


def _quat_rotate(q: Tuple[float, float, float, float],
                 v: Vec3) -> Vec3:
    x, y, z, w = q
    qv = (x, y, z)
    t = _cross(qv, v)
    t = (2.0 * t[0], 2.0 * t[1], 2.0 * t[2])
    c = _cross(qv, t)
    return (v[0] + w * t[0] + c[0],
            v[1] + w * t[1] + c[1],
            v[2] + w * t[2] + c[2])


def camera_basis(cam: CameraParams) -> Tuple[Vec3, Vec3, Vec3]:
    """Return ``(right, up, forward)`` for the current camera orientation."""
    if cam.quaternion is not None:
        q = cam.quaternion
        return (_normalize(_quat_rotate(q, (1.0, 0.0, 0.0))),
                _normalize(_quat_rotate(q, (0.0, 1.0, 0.0))),
                _normalize(_quat_rotate(q, (0.0, 0.0, -1.0))))
    yaw = math.radians(cam.yaw)
    pitch = math.radians(max(-89.0, min(89.0, cam.pitch)))
    forward = (-math.cos(pitch) * math.sin(yaw),
               -math.sin(pitch),
               math.cos(pitch) * math.cos(yaw))
    right = _cross(forward, (0.0, 1.0, 0.0))
    if _dot(right, right) < 1e-9:
        right = (1.0, 0.0, 0.0)
    right = _normalize(right)
    up = _cross(right, forward)
    return right, up, forward


def _basis(direction: Vec3) -> Tuple[Vec3, Vec3]:
    """Two unit vectors perpendicular to *direction* (and to each other)."""
    d = _normalize(direction)
    helper = (0.0, 1.0, 0.0) if abs(d[1]) < 0.9 else (1.0, 0.0, 0.0)
    u = _normalize(_cross(d, helper))
    v = _normalize(_cross(d, u))
    return u, v


def _normalize(v: Vec3) -> Vec3:
    length = math.sqrt(sum(c * c for c in v)) or 1.0
    return (v[0] / length, v[1] / length, v[2] / length)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _bond_offsets(a: Vec3, b: Vec3, order: int, bond_radius: float,
                  view_dir: Vec3) -> List[Vec3]:
    """Parallel offsets for double/triple bonds (Avogadro-style multi-cylinder).

    The offset direction is perpendicular to the bond and lies in the image
    plane, so multiple bonds stay readable from the current camera.
    """
    if order <= 1:
        return [(0.0, 0.0, 0.0)]
    axis = _normalize(_sub(b, a))
    side = _cross(axis, view_dir)
    if math.sqrt(_dot(side, side)) < 1e-4:
        side = _cross(axis, (0.0, 1.0, 0.0))
        if math.sqrt(_dot(side, side)) < 1e-4:
            side = _cross(axis, (1.0, 0.0, 0.0))
    side = _normalize(side)
    step = bond_radius * 1.5
    return [_mul(side, (i - (order - 1) / 2.0) * step) for i in range(order)]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _mul(a: Vec3, k: float) -> Vec3:
    return (a[0] * k, a[1] * k, a[2] * k)


def _light_position(center: Vec3, radius: float, azimuth: float,
                    elevation: float, distance: float = 2.6) -> Vec3:
    az = math.radians(azimuth)
    el = math.radians(elevation)
    d = radius * distance
    return (
        center[0] + d * math.cos(el) * math.sin(az),
        center[1] + d * math.sin(el),
        center[2] - d * math.cos(el) * math.cos(az),
    )


# --------------------------------------------------------------------------
# generator
# --------------------------------------------------------------------------

def generate_pov(structure: Structure, params: SceneParams) -> str:
    """Return the complete POV-Ray source for *structure*."""
    p = params.for_style()
    atoms = visible_atoms(structure, p.hide_hydrogens)
    bonds = visible_bonds(structure, p.hide_hydrogens)
    if atoms:
        n = float(len(atoms))
        center = (
            sum(a.x for a in atoms) / n,
            sum(a.y for a in atoms) / n,
            sum(a.z for a in atoms) / n,
        )
        radius = max(
            math.dist((a.x, a.y, a.z), center) for a in atoms
        ) or 1.0
    else:
        center = structure.center()
        radius = 1.0
    radius = max(0.6, radius)
    out: List[str] = []

    out.append("// Generated by MolPOV - molecular viewer with POV-Ray preview")
    out.append("#version 3.7;")
    if not p.radiosity:
        out.append("#declare RADIOSITY = 0;")
    else:
        out.append("#ifndef(RADIOSITY) #declare RADIOSITY = 1; #end")
    out.append(f"#ifndef(MAXTRACE) #declare MAXTRACE = {p.max_trace_level}; #end")

    out.append("global_settings {")
    out.append("    assumed_gamma 1.0")
    out.append("    max_trace_level MAXTRACE")
    if p.light.style == "avogadro":
        out.append(f"    ambient_light rgb <{AVOGADRO_AMBIENT}, {AVOGADRO_AMBIENT}, "
                   f"{AVOGADRO_AMBIENT}>")
    out.append("    #if (RADIOSITY)")
    out.append(
        f"        radiosity {{ count {p.radiosity_count} error_bound 0.25 "
        "recursion_limit 1 nearest_count 4 brightness 0.55 }"
    )
    out.append("    #end")
    out.append("}")
    out.append("")

    # ------------------------------------------------------------- backdrop
    top, bottom = _background_colors(p.background)
    if p.radiosity:
        # radiosity gathers the sky sphere; keep it from flooding the scene
        top = _mul(top, 0.55)
        bottom = _mul(bottom, 0.55)
    if p.alpha:
        out.append("background { color rgbt <1, 1, 1, 1> }")
    elif p.background == "black":
        # Avogadro 1.x keeps the GL clear colour (black by default) as a flat
        # backdrop; a sky sphere would wash that look out.
        out.append("background { color rgb <0, 0, 0> }")
    elif p.background == "custom":
        out.append(f"background {{ color rgb {_vec(p.background_color)} }}")
    else:
        out.append("sky_sphere {")
        out.append("    pigment {")
        out.append("        gradient y")
        out.append("        color_map {")
        out.append(f"            [0.0 rgb {_vec(bottom)}]")
        out.append(f"            [1.0 rgb {_vec(top)}]")
        out.append("        }")
        out.append("        scale 2")
        out.append("        translate -1*y")
        out.append("    }")
        out.append("}")

    # --------------------------------------------------------------- camera
    cam = p.camera
    loc = _camera_location(center, radius, cam)
    # Explicit camera basis: avoids POV-Ray's `angle` (which spans the
    # horizontal field of view) and keeps the projection identical to the
    # interactive viewport.
    right_v, up_v, fwd = camera_basis(cam)
    tan_v = math.tan(math.radians(max(5.0, cam.fov)) / 2.0)
    asw = p.aspect
    # POV-Ray spans the image from -up/2 to +up/2, hence the factor of two.
    out.append("camera {")
    out.append(f"    location {_vec(loc)}")
    out.append(f"    direction {_vec(fwd)}")
    out.append(f"    up {_vec(_mul(up_v, 2.0 * tan_v))}")
    out.append(f"    right {_vec(_mul(right_v, 2.0 * tan_v * asw))}")
    out.append("}")

    # --------------------------------------------------------------- lights
    l = p.light
    if l.style == "avogadro":
        # Avogadro's two directional lights live in eye space; rotate them into
        # world space with the camera basis (+z of eye space points at viewer).
        # The intensity sliders scale relative to the upstream defaults.
        huge = 10.0 * radius
        key_scale = max(0.0, l.key_intensity) / 1.15
        fill_scale = max(0.0, l.fill_intensity) / 0.35
        out.append("// Avogadro 1.x lighting: two parallel lights in eye space")
        for eye_dir, diffuse, scale in (
            (AVOGADRO_LIGHTS[0][0], AVOGADRO_LIGHTS[0][1], key_scale),
            (AVOGADRO_LIGHTS[1][0], AVOGADRO_LIGHTS[1][1], fill_scale),
        ):
            direction = _normalize(
                _add(_mul(right_v, eye_dir[0]),
                     _add(_mul(up_v, eye_dir[1]), _mul(fwd, -eye_dir[2])))
            )
            pos = _add(center, _mul(direction, huge))
            value = diffuse * scale
            out.append("light_source {")
            out.append(f"    {_vec(pos)}")
            out.append(f"    color rgb {_vec((value, value, value))}")
            out.append(f"    fade_distance {2.0 * huge:.4f}")
            out.append("    fade_power 0")
            out.append("    parallel")
            out.append(f"    point_at {_vec(center)}")
            out.append("}")
    else:
        key_pos = _light_position(center, radius, l.azimuth, l.elevation)
        key_dir = _normalize(_sub(key_pos, center))
        u, v = _basis(key_dir)
        area = max(0.05, radius * l.softness)
        warm = p.light.warmth
        key_color = (1.0, 1.0 - 0.04 * warm, 1.0 - 0.10 * warm)
        out.append(f"// key light: area {area:.3f}")
        out.append("light_source {")
        out.append(f"    {_vec(key_pos)}")
        out.append(f"    color rgb {_vec(_mul(key_color, l.key_intensity))}")
        grid = max(1, int(p.area_samples))
        out.append(
            f"    area_light {_vec(_mul(u, area))}, {_vec(_mul(v, area))}, {grid}, {grid}"
        )
        out.append("    adaptive 1")
        if p.jitter:
            out.append("    jitter")
        out.append("}")

        fill_pos = _light_position(center, radius, l.azimuth + 145.0, 18.0)
        out.append("light_source {")
        out.append(f"    {_vec(fill_pos)}")
        out.append(
            f"    color rgb {_vec(_mul((0.72, 0.82, 1.0), l.fill_intensity))}"
        )
        out.append("}")

        rim_pos = _light_position(center, radius, l.azimuth + 195.0, 55.0)
        out.append("light_source {")
        out.append(f"    {_vec(rim_pos)}")
        out.append(f"    color rgb {_vec(_mul((1.0, 1.0, 1.0), 0.45 * l.key_intensity))}")
        out.append("}")

    if l.headlight:
        # A camera-mounted parallel light; can be combined with either mode
        # (with the Avogadro preset it adds a front fill on top of its key).
        value = max(0.0, l.headlight_intensity)
        out.append("// headlight: parallel light fixed to the camera")
        out.append("light_source {")
        out.append(f"    {_vec(loc)}")
        out.append(f"    color rgb {_vec((value, value, value))}")
        out.append("    parallel")
        out.append(f"    point_at {_vec(center)}")
        out.append("}")

    # ---------------------------------------------------------------- floor
    if p.floor and not p.alpha:
        floor_y = center[1] - radius * 1.02
        floor_diffuse = 0.45 if p.radiosity else 0.75
        out.append("plane {")
        out.append(f"    y, {floor_y:.5f}")
        out.append(
            "    pigment { color rgb <0.80, 0.81, 0.85> }"
        )
        out.append(
            f"    finish {{ ambient 0 diffuse {floor_diffuse:.2f} specular 0.12 "
            "roughness 0.05 reflection { 0.05 } }"
        )
        out.append("}")

    # ------------------------------------------------------------ molecules
    finish = _finish(p.material)
    transmit = max(0.0, min(0.85, p.transparency))
    packed = p.pack_scene and transmit <= 0.0

    def pigment(colour: Vec3) -> str:
        base = _vec(colour)                     # "<r, g, b>"
        if transmit > 0:
            return f"pigment {{ rgbt {base[:-1]}, {transmit:.3f}> }}"
        return f"pigment {{ rgb {base} }}"

    out.append("")
    if packed:
        # Unions give POV-Ray an automatic bounding box; a single union of all
        # atoms and bonds is markedly faster than thousands of loose primitives.
        out.append("// ---- molecule (union: opaque, gets a bounding box) ----")
        out.append("union {")
    else:
        out.append("// ---- molecule ----")

    out.append("// atoms")
    for atom in atoms:
        r = max(0.08, elements.vdw_radius(atom.element) * p.atom_scale)
        out.append("sphere {")
        out.append(f"    {_vec(atom.pos)}, {r:.4f}")
        out.append(f"    {pigment(_linear(atom.element, p.palette, p.custom_colors))}")
        out.append(f"    {finish}")
        out.append("}")

    if p.bond_radius > 0 and p.style != "spacefill":
        out.append("// bonds")
        br = p.bond_radius
        for bond in bonds:
            a = structure.atoms[bond.a]
            b = structure.atoms[bond.b]
            order = int(round(bond.order))
            if order < 1 or order > 3:  # aromatic (4) and odd values -> single
                order = 1
            radius = br * (0.62 if order > 1 else 1.0)
            offsets = _bond_offsets(a.pos, b.pos, order, br, fwd)
            pa_colour = pigment(_linear(a.element, p.palette, p.custom_colors))
            pb_colour = pigment(_linear(b.element, p.palette, p.custom_colors))
            for shift in offsets:
                pa = _add(a.pos, shift)
                pb = _add(b.pos, shift)
                mid = (
                    (pa[0] + pb[0]) / 2.0,
                    (pa[1] + pb[1]) / 2.0,
                    (pa[2] + pb[2]) / 2.0,
                )
                out.append(
                    f"cylinder {{ {_vec(pa)}, {_vec(mid)}, {radius:.4f} "
                    f"{pa_colour} {finish} }}"
                )
                out.append(
                    f"cylinder {{ {_vec(mid)}, {_vec(pb)}, {radius:.4f} "
                    f"{pb_colour} {finish} }}"
                )

    if packed:
        out.append("}")

    out.append("")
    return "\n".join(out)


def apply_preset(params: "SceneParams", name: str) -> "SceneParams":
    """Apply a coherent look to *params* in place."""
    if name == "studio":
        params.material = "standard"
        params.light.style = "studio"
        params.light.headlight = False
        params.light.softness = 0.35
        params.light.fill_intensity = 0.35
        params.floor = True
        params.background = "studio"
        params.transparency = 0.0
        params.max_trace_level = 5
    elif name == "avogadro":
        params.material = "avogadro"
        params.light.style = "avogadro"
        params.light.headlight = False
        params.light.fill_intensity = 0.35
        params.floor = False
        params.background = "black"
        params.transparency = 0.0
        params.max_trace_level = 15
    return params


def _background_colors(name: str) -> Tuple[Vec3, Vec3]:
    if name == "black":
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    if name == "dark":
        return (0.045, 0.055, 0.075), (0.012, 0.014, 0.020)
    if name == "light":
        return (1.0, 1.0, 1.0), (0.92, 0.93, 0.95)
    if name == "transparent":
        return (1.0, 1.0, 1.0), (1.0, 1.0, 1.0)
    # "studio": soft grey sweep
    return (0.94, 0.95, 0.97), (0.86, 0.88, 0.92)


def write_pov(structure: Structure, params: SceneParams, path: str) -> str:
    text = generate_pov(structure, params)
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def camera_block(structure: Structure, params: SceneParams) -> str:
    """A ready-to-insert ``camera { ... }`` block for *structure*."""
    center = structure.center()
    radius = max(0.6, structure.radius())
    cam = params.camera
    loc = _camera_location(center, radius, cam)
    right_v, up_v, fwd = camera_basis(cam)
    tan_v = math.tan(math.radians(max(5.0, cam.fov)) / 2.0)
    aspect = params.aspect
    return "\n".join([
        "camera {",
        f"    location {_vec(loc)}",
        f"    direction {_vec(fwd)}",
        f"    up {_vec(_mul(up_v, 2.0 * tan_v))}",
        f"    right {_vec(_mul(right_v, 2.0 * tan_v * aspect))}",
        "}",
    ])


def background_block(params: SceneParams) -> str:
    """A ready-to-insert ``background``/``sky_sphere`` block."""
    if params.alpha:
        return "background { color rgbt <1, 1, 1, 1> }"
    if params.background == "black":
        return "background { color rgb <0, 0, 0> }"
    if params.background == "custom":
        return f"background {{ color rgb {_vec(params.background_color)} }}"
    top, bottom = _background_colors(params.background)
    if params.radiosity:
        top = _mul(top, 0.55)
        bottom = _mul(bottom, 0.55)
    return "\n".join([
        "sky_sphere {",
        "    pigment {",
        "        gradient y",
        "        color_map {",
        f"            [0.0 rgb {_vec(bottom)}]",
        f"            [1.0 rgb {_vec(top)}]",
        "        }",
        "        scale 2",
        "        translate -1*y",
        "    }",
        "}",
    ])
