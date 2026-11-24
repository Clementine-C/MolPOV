"""Structure model and readers for XYZ / PDB / MOL(SDF) files."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

from . import elements

Vec3 = Tuple[float, float, float]


@dataclass
class Atom:
    element: str
    x: float
    y: float
    z: float
    name: str = ""
    residue: str = ""
    index: int = 0

    @property
    def pos(self) -> Vec3:
        return (self.x, self.y, self.z)


@dataclass
class Bond:
    a: int
    b: int
    order: float = 1.0


@dataclass
class Structure:
    atoms: List[Atom] = field(default_factory=list)
    bonds: List[Bond] = field(default_factory=list)
    title: str = ""
    source: str = ""

    # ------------------------------------------------------------------ info
    def __len__(self) -> int:
        return len(self.atoms)

    @property
    def elements(self) -> List[str]:
        return sorted({a.element for a in self.atoms})

    def center(self) -> Vec3:
        if not self.atoms:
            return (0.0, 0.0, 0.0)
        n = float(len(self.atoms))
        return (
            sum(a.x for a in self.atoms) / n,
            sum(a.y for a in self.atoms) / n,
            sum(a.z for a in self.atoms) / n,
        )

    def radius(self) -> float:
        """Radius of the bounding sphere around :meth:`center`."""
        if not self.atoms:
            return 1.0
        cx, cy, cz = self.center()
        return max(
            math.dist((a.x, a.y, a.z), (cx, cy, cz)) for a in self.atoms
        ) or 1.0

    def summary(self) -> str:
        counts = {}
        for a in self.atoms:
            counts[a.element] = counts.get(a.element, 0) + 1
        formula = "".join(f"{k}{v if v > 1 else ''}" for k, v in sorted(counts.items()))
        return f"{len(self.atoms)} atoms, {len(self.bonds)} bonds  [{formula}]"

    # --------------------------------------------------------------- edits
    def translate(self, dx: float, dy: float, dz: float) -> None:
        for a in self.atoms:
            a.x += dx
            a.y += dy
            a.z += dz

    def recenter(self) -> None:
        cx, cy, cz = self.center()
        self.translate(-cx, -cy, -cz)

    def bonded(self, i: int, j: int) -> bool:
        return any(
            (b.a == i and b.b == j) or (b.a == j and b.b == i) for b in self.bonds
        )

    # ---------------------------------------------------------------- bonds
    def perceive_bonds(self, tolerance: float = 1.25, min_distance: float = 0.35) -> None:
        """Infer bonds from distances using covalent radii.

        ``tolerance`` multiplies the sum of covalent radii; 1.25 keeps typical
        single/aromatic bonds while rejecting non-bonded contacts.
        """
        bonds: List[Bond] = []
        atoms = self.atoms
        for i in range(len(atoms)):
            ai = atoms[i]
            ri = elements.covalent_radius(ai.element)
            for j in range(i + 1, len(atoms)):
                aj = atoms[j]
                if ai.element == "H" and aj.element == "H":
                    continue
                d = math.dist(ai.pos, aj.pos)
                if d < min_distance:
                    continue
                if d <= tolerance * (ri + elements.covalent_radius(aj.element)):
                    bonds.append(Bond(i, j, 1.0))
        self.bonds = bonds

    def add_missing_bonds(self, tolerance: float = 1.25) -> None:
        known = {(min(b.a, b.b), max(b.a, b.b)) for b in self.bonds}
        probe = Structure(atoms=self.atoms)
        probe.perceive_bonds(tolerance=tolerance)
        for b in probe.bonds:
            key = (min(b.a, b.b), max(b.a, b.b))
            if key not in known:
                self.bonds.append(b)
                known.add(key)


# --------------------------------------------------------------------------
# readers
# --------------------------------------------------------------------------

def _to_float(text: str, default: float = 0.0) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def read_xyz(text: str, source: str = "") -> Structure:
    lines = text.splitlines()
    if not lines:
        raise ValueError("empty XYZ file")
    try:
        count = int(lines[0].split()[0])
    except (IndexError, ValueError) as exc:
        raise ValueError("first line of an XYZ file must contain the atom count") from exc
    title = lines[1].strip() if len(lines) > 1 else ""
    atoms: List[Atom] = []
    for line in lines[2:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        if len(atoms) >= count:
            break
        symbol = elements.normalize_symbol(parts[0])
        atoms.append(
            Atom(symbol, _to_float(parts[1]), _to_float(parts[2]), _to_float(parts[3]),
                 index=len(atoms))
        )
    if not atoms:
        raise ValueError("no atoms found in XYZ file")
    st = Structure(atoms=atoms, title=title, source=source)
    st.perceive_bonds()
    return st


def _pdb_element(line: str, atom_name: str) -> str:
    raw = line[76:78].strip() if len(line) >= 78 else ""
    if raw:
        return elements.normalize_symbol(raw)
    name = atom_name.strip()
    # PDB atom names are right aligned and may start with a digit ("1H", "CA").
    name = re.sub(r"^\d+", "", name)
    if len(name) >= 2 and name[:2].capitalize() in elements.COVALENT_RADII:
        return name[:2].capitalize()
    return elements.normalize_symbol(name[:1] or "C")


def read_pdb(text: str, source: str = "") -> Structure:
    atoms: List[Atom] = []
    title = ""
    for line in text.splitlines():
        record = line[:6].strip()
        if record in ("TITLE",):
            title += line[10:].strip() + " "
        if record not in ("ATOM", "HETATM"):
            continue
        altloc = line[16:17]
        if altloc not in (" ", "", "A", "1"):
            continue
        atom_name = line[12:16]
        residue = line[17:20].strip()
        symbol = _pdb_element(line, atom_name)
        atoms.append(
            Atom(
                element=symbol,
                x=_to_float(line[30:38]),
                y=_to_float(line[38:46]),
                z=_to_float(line[46:54]),
                name=atom_name.strip(),
                residue=residue,
                index=len(atoms),
            )
        )
    if not atoms:
        raise ValueError("no ATOM/HETATM records found in PDB file")
    st = Structure(atoms=atoms, title=title.strip(), source=source)
    st.perceive_bonds()
    return st


def read_mol(text: str, source: str = "") -> Structure:
    lines = text.splitlines()
    if len(lines) < 4:
        raise ValueError("file is too short to be a MOL/SDF file")
    if "V3000" in lines[3]:
        raise ValueError("V3000 MOL files are not supported yet; save as V2000")
    counts = lines[3]
    try:
        natoms = int(counts[0:3])
        nbonds = int(counts[3:6])
    except ValueError as exc:
        raise ValueError("invalid MOL counts line") from exc
    atoms: List[Atom] = []
    bonds: List[Bond] = []
    for i in range(natoms):
        line = lines[4 + i]
        symbol = elements.normalize_symbol(line[31:34].strip())
        atoms.append(
            Atom(
                symbol,
                _to_float(line[0:10]),
                _to_float(line[10:20]),
                _to_float(line[20:30]),
                index=i,
            )
        )
    for j in range(nbonds):
        line = lines[4 + natoms + j]
        a = int(line[0:3]) - 1
        b = int(line[3:6]) - 1
        order = _to_float(line[6:9], 1.0)
        if 0 <= a < natoms and 0 <= b < natoms and a != b:
            bonds.append(Bond(a, b, order))
    title = lines[0].strip()
    st = Structure(atoms=atoms, bonds=bonds, title=title, source=source)
    st.add_missing_bonds()
    return st


_READERS = {".xyz": read_xyz, ".pdb": read_pdb, ".mol": read_mol, ".sdf": read_mol}


def read_structure(path: str | os.PathLike[str]) -> Structure:
    path = os.fspath(path)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pov":
        # Imported lazily: povparse imports this module.
        from .povparse import read_pov

        reader = read_pov
    else:
        reader = _READERS.get(ext)
    if reader is None:
        raise ValueError(f"unsupported file type: {ext or '(none)'}")
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    if ext == ".sdf":
        text = text.split("$$$$")[0]
    st = reader(text, source=path)
    st.recenter()
    return st


# --------------------------------------------------------------------------
# helpers used by the tests and the sample generator
# --------------------------------------------------------------------------

def from_atoms(symbols: Sequence[str], coords: Iterable[Sequence[float]],
               bonds: Optional[Sequence[Tuple[int, int]]] = None) -> Structure:
    atoms = [
        Atom(elements.normalize_symbol(sym), float(c[0]), float(c[1]), float(c[2]), index=i)
        for i, (sym, c) in enumerate(zip(symbols, coords))
    ]
    st = Structure(atoms=atoms)
    if bonds is None:
        st.perceive_bonds()
    else:
        st.bonds = [Bond(a, b) for a, b in bonds]
    return st


def visible_atoms(structure: Structure, hide_hydrogens: bool) -> List[Atom]:
    """Atoms to draw; with *hide_hydrogens* every H is dropped."""
    if not hide_hydrogens:
        return list(structure.atoms)
    return [atom for atom in structure.atoms if atom.element != "H"]


def visible_bonds(structure: Structure, hide_hydrogens: bool) -> List[Bond]:
    """Bonds to draw; ones touching a hidden hydrogen are dropped."""
    if not hide_hydrogens:
        return list(structure.bonds)
    return [
        bond for bond in structure.bonds
        if structure.atoms[bond.a].element != "H"
        and structure.atoms[bond.b].element != "H"
    ]
