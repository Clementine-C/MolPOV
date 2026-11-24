"""Element data: CPK colours, covalent radii and van der Waals radii.

The tables cover every element that shows up in ordinary organic / inorganic
structures.  Unknown elements fall back to a neutral grey carbon-like look so a
scene never fails to build because of an exotic atom.
"""

from __future__ import annotations

from typing import Dict, Tuple

Rgb = Tuple[float, float, float]

# Cordero et al. covalent radii (angstrom), used for bond perception.
COVALENT_RADII: Dict[str, float] = {
    "H": 0.31, "He": 0.28, "Li": 1.28, "Be": 0.96, "B": 0.84, "C": 0.76,
    "N": 0.71, "O": 0.66, "F": 0.57, "Ne": 0.58, "Na": 1.66, "Mg": 1.41,
    "Al": 1.21, "Si": 1.11, "P": 1.07, "S": 1.05, "Cl": 1.02, "Ar": 1.06,
    "K": 2.03, "Ca": 1.76, "Sc": 1.70, "Ti": 1.60, "V": 1.53, "Cr": 1.39,
    "Mn": 1.39, "Fe": 1.32, "Co": 1.26, "Ni": 1.24, "Cu": 1.32, "Zn": 1.22,
    "Ga": 1.22, "Ge": 1.20, "As": 1.19, "Se": 1.20, "Br": 1.20, "Kr": 1.16,
    "Rb": 2.20, "Sr": 1.95, "Y": 1.90, "Zr": 1.75, "Nb": 1.64, "Mo": 1.54,
    "Ru": 1.46, "Rh": 1.42, "Pd": 1.39, "Ag": 1.45, "Cd": 1.44, "In": 1.42,
    "Sn": 1.39, "Sb": 1.39, "Te": 1.38, "I": 1.39, "Xe": 1.40,
    "Cs": 2.44, "Ba": 2.15, "La": 2.07, "Ce": 2.04, "W": 1.62, "Pt": 1.36,
    "Au": 1.36, "Hg": 1.32, "Pb": 1.46, "Bi": 1.48,
}

# Bondi van der Waals radii (angstrom).
VDW_RADII: Dict[str, float] = {
    "H": 1.20, "He": 1.40, "Li": 1.82, "Be": 1.53, "B": 1.92, "C": 1.70,
    "N": 1.55, "O": 1.52, "F": 1.47, "Ne": 1.54, "Na": 2.27, "Mg": 1.73,
    "Al": 1.84, "Si": 2.10, "P": 1.80, "S": 1.80, "Cl": 1.75, "Ar": 1.88,
    "K": 2.75, "Ca": 2.31, "Sc": 2.15, "Ti": 2.11, "V": 2.07, "Cr": 2.06,
    "Mn": 2.05, "Fe": 2.04, "Co": 2.00, "Ni": 1.97, "Cu": 1.96, "Zn": 2.01,
    "Ga": 1.87, "Ge": 2.11, "As": 1.85, "Se": 1.90, "Br": 1.85, "Kr": 2.02,
    "Rb": 3.03, "Sr": 2.49, "Y": 2.32, "Zr": 2.23, "Nb": 2.18, "Mo": 2.17,
    "Ru": 2.07, "Rh": 2.02, "Pd": 2.05, "Ag": 2.03, "Cd": 2.18, "In": 1.93,
    "Sn": 2.17, "Sb": 2.06, "Te": 2.06, "I": 1.98, "Xe": 2.16,
    "Cs": 3.43, "Ba": 2.68, "La": 2.43, "Ce": 2.42, "W": 2.10, "Pt": 2.13,
    "Au": 2.14, "Hg": 2.23, "Pb": 2.02, "Bi": 2.07,
}

# Jmol/CPK colours (sRGB, 0-255).
CPK_COLORS: Dict[str, Tuple[int, int, int]] = {
    "H": (255, 255, 255), "He": (217, 255, 255), "Li": (204, 128, 255),
    "Be": (194, 255, 0), "B": (255, 181, 181), "C": (144, 144, 144),
    "N": (48, 80, 248), "O": (255, 13, 13), "F": (144, 224, 80),
    "Ne": (179, 227, 245), "Na": (171, 92, 242), "Mg": (138, 255, 0),
    "Al": (191, 166, 166), "Si": (240, 200, 160), "P": (255, 128, 0),
    "S": (255, 255, 48), "Cl": (31, 240, 31), "Ar": (128, 209, 227),
    "K": (143, 64, 212), "Ca": (61, 255, 0), "Sc": (230, 230, 230),
    "Ti": (191, 194, 199), "V": (166, 166, 171), "Cr": (138, 153, 199),
    "Mn": (156, 122, 199), "Fe": (224, 102, 51), "Co": (240, 144, 160),
    "Ni": (80, 208, 80), "Cu": (200, 128, 51), "Zn": (125, 128, 176),
    "Ga": (194, 143, 143), "Ge": (102, 143, 143), "As": (189, 128, 227),
    "Se": (255, 161, 0), "Br": (166, 41, 41), "Kr": (92, 184, 209),
    "Rb": (112, 46, 176), "Sr": (0, 255, 0), "Y": (148, 255, 255),
    "Zr": (148, 224, 224), "Nb": (115, 194, 201), "Mo": (84, 181, 181),
    "Ru": (36, 143, 143), "Rh": (10, 125, 140), "Pd": (0, 105, 133),
    "Ag": (192, 192, 192), "Cd": (255, 217, 143), "In": (166, 117, 115),
    "Sn": (102, 128, 128), "Sb": (158, 99, 181), "Te": (212, 122, 0),
    "I": (148, 0, 148), "Xe": (66, 158, 176), "Cs": (87, 23, 143),
    "Ba": (0, 201, 0), "La": (112, 212, 255), "Ce": (255, 255, 199),
    "W": (33, 148, 214), "Pt": (208, 208, 224), "Au": (255, 209, 35),
    "Hg": (184, 184, 208), "Pb": (87, 89, 97), "Bi": (158, 79, 181),
}

# VMD-ish palette: carbon in cyan, hydrogens white.
VMD_COLORS: Dict[str, Tuple[int, int, int]] = dict(CPK_COLORS)
VMD_COLORS.update({"C": (128, 255, 255), "H": (255, 255, 255)})

# Soft pastel palette, closer to the "粉彩球棍" look of modern viewers.
PASTEL_COLORS: Dict[str, Tuple[int, int, int]] = {
    "H": (245, 245, 245), "C": (120, 144, 156), "N": (121, 134, 203),
    "O": (239, 154, 154), "S": (255, 204, 128), "P": (255, 171, 145),
    "F": (129, 199, 132), "Cl": (77, 182, 172), "Br": (161, 136, 127),
    "I": (149, 117, 205), "B": (255, 183, 197), "Si": (188, 170, 164),
    "Fe": (230, 145, 100), "Zn": (144, 164, 174), "Cu": (210, 150, 100),
    "Na": (179, 157, 219), "K": (149, 117, 205), "Mg": (174, 213, 129),
    "Ca": (197, 225, 165), "Se": (255, 204, 128),
}

PALETTES: Dict[str, Dict[str, Tuple[int, int, int]]] = {
    "cpk": CPK_COLORS,
    "vmd": VMD_COLORS,
    "pastel": PASTEL_COLORS,
    "custom": {},
}

# Long names used by PDB files ("CARBON", "FE", ...) -> symbol.
_LONG_NAMES = {
    "CARBON": "C", "HYDROGEN": "H", "NITROGEN": "N", "OXYGEN": "O",
    "SULFUR": "S", "PHOSPHORUS": "P", "FLUORINE": "F", "CHLORINE": "Cl",
    "BROMINE": "Br", "IODINE": "I", "BORON": "B", "SILICON": "Si",
    "SELENIUM": "Se", "SODIUM": "Na", "POTASSIUM": "K", "MAGNESIUM": "Mg",
    "CALCIUM": "Ca", "IRON": "Fe", "ZINC": "Zn", "COPPER": "Cu",
    "MANGANESE": "Mn", "COBALT": "Co", "NICKEL": "Ni", "MOLYBDENUM": "Mo",
    "LITHIUM": "Li", "ALUMINIUM": "Al", "ALUMINUM": "Al",
}


def normalize_symbol(raw: str) -> str:
    """Turn ``C1``, ``CA``, ``FE2+`` or ``CARBON`` into a usable symbol."""
    if not raw:
        return "X"
    text = raw.strip()
    if not text:
        return "X"
    upper = text.upper()
    if upper in _LONG_NAMES:
        return _LONG_NAMES[upper]
    if upper in ("X", "XX", "DU", "DUM"):
        return "X"
    # Drop trailing digits / charges / dots.
    letters = "".join(ch for ch in text if ch.isalpha())
    if not letters:
        return "X"
    if len(letters) >= 2:
        two = letters[0].upper() + letters[1].lower()
        if two in COVALENT_RADII:
            return two
    one = letters[0].upper()
    if one in COVALENT_RADII:
        return one
    two = letters[:2].capitalize()
    return two if two in COVALENT_RADII else one


def covalent_radius(symbol: str) -> float:
    return COVALENT_RADII.get(symbol, 0.77)


def vdw_radius(symbol: str) -> float:
    return VDW_RADII.get(symbol, 1.70)


def color(symbol: str, palette: str = "cpk",
          custom: Dict[str, Tuple[int, int, int]] | None = None) -> Tuple[int, int, int]:
    """sRGB colour for *symbol*.

    ``custom`` (symbol -> RGB) wins over the named palette; with
    ``palette="custom"`` unlisted elements fall back to CPK.
    """
    if custom and symbol in custom:
        return tuple(custom[symbol])  # type: ignore[return-value]
    table = PALETTES.get(palette) or CPK_COLORS
    return table.get(symbol, (160, 160, 160))


def color_unit(symbol: str, palette: str = "cpk",
               custom: Dict[str, Tuple[int, int, int]] | None = None) -> Rgb:
    """sRGB colour in 0..1 (not linearised; see :func:`srgb_to_linear`)."""
    r, g, b = color(symbol, palette, custom)
    return (r / 255.0, g / 255.0, b / 255.0)


def srgb_to_linear(value: float) -> float:
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def linear_rgb(symbol: str, palette: str = "cpk",
               custom: Dict[str, Tuple[int, int, int]] | None = None) -> Rgb:
    r, g, b = color_unit(symbol, palette, custom)
    return (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b))
