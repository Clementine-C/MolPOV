"""Generate the bundled sample structures with RDKit (development helper).

Not needed at run time - the generated files live in ``samples/``.
"""

from __future__ import annotations

import os

from rdkit import Chem
from rdkit.Chem import AllChem

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "samples")

MOLECULES = {
    "benzene": ("c1ccccc1", "Benzene, C6H6"),
    "caffeine": ("Cn1cnc2c1c(=O)n(C)c(=O)n2C", "Caffeine, C8H10N4O2"),
    "ethanol": ("CCO", "Ethanol, C2H6O"),
    "alanine_dipeptide": ("CC(=O)NC(C)C(=O)NC", "Ace-Ala-NMe dipeptide"),
    "aspirin": ("CC(=O)Oc1ccccc1C(=O)O", "Aspirin, C9H8O4"),
    "water_cluster": ("O.O.O", "Water trimer"),
    "cholesterol": (
        "CC(C)CCCC(C)C1CCC2C1(CCC3C2CC=C4C3(CCC(C4)O)C)C",
        "Cholesterol, C27H46O",
    ),
}


def embed(smiles: str):
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    params = AllChem.ETKDGv3()
    params.randomSeed = 0xC0FFEE
    AllChem.EmbedMolecule(mol, params)
    AllChem.MMFFOptimizeMolecule(mol, maxIters=400)
    return mol


def write_xyz(mol, path: str, title: str) -> None:
    conf = mol.GetConformer()
    lines = [str(mol.GetNumAtoms()), title]
    for atom in mol.GetAtoms():
        pos = conf.GetAtomPosition(atom.GetIdx())
        lines.append(
            f"{atom.GetSymbol():2s} {pos.x:10.4f} {pos.y:10.4f} {pos.z:10.4f}"
        )
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


def write_pdb(mol, path: str) -> None:
    block = Chem.MolToPDBBlock(mol, flavor=2)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(block)


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    for name, (smiles, title) in MOLECULES.items():
        mol = embed(smiles)
        write_xyz(mol, os.path.join(OUT, f"{name}.xyz"), title)
        if name == "alanine_dipeptide":
            write_pdb(mol, os.path.join(OUT, f"{name}.pdb"))
        if name in ("caffeine", "aspirin", "cholesterol"):
            with open(os.path.join(OUT, f"{name}.sdf"), "w", encoding="utf-8") as handle:
                handle.write(Chem.MolToMolBlock(mol, kekulize=True) + "$$$$\n")
        print(f"wrote {name}: {mol.GetNumAtoms()} atoms")


if __name__ == "__main__":
    main()
