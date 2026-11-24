"""Unit tests for the structure readers and the POV-Ray scene generator.

Run with ``python -m unittest discover -s tests`` (no GUI, no renderer needed).
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from molpov import sdl  # noqa: E402
from molpov.structure import (  # noqa: E402
    read_mol,
    read_pdb,
    read_structure,
    read_xyz,
)

SAMPLES = os.path.join(ROOT, "samples")


class ReadXyzTests(unittest.TestCase):
    def test_benzene_xyz(self) -> None:
        st = read_structure(os.path.join(SAMPLES, "benzene.xyz"))
        self.assertEqual(len(st), 12)
        self.assertEqual(len(st.bonds), 12)  # six C-C and six C-H bonds
        self.assertEqual(sorted(st.elements), ["C", "H"])

    def test_xyz_header_is_parsed(self) -> None:
        text = "2\nwater\nO 0 0 0\nH 0.96 0 0\n"
        st = read_xyz(text)
        self.assertEqual(st.title, "water")
        self.assertEqual(len(st), 2)
        self.assertEqual(len(st.bonds), 1)

    def test_xyz_rejects_garbage(self) -> None:
        with self.assertRaises(ValueError):
            read_xyz("not a number\n")


class ReadOtherFormatsTests(unittest.TestCase):
    def test_pdb(self) -> None:
        st = read_structure(os.path.join(SAMPLES, "alanine_dipeptide.pdb"))
        self.assertEqual(len(st), 22)
        self.assertGreater(len(st.bonds), 15)
        self.assertEqual(st.atoms[0].element, "C")

    def test_sdf_keeps_explicit_bonds(self) -> None:
        st = read_structure(os.path.join(SAMPLES, "caffeine.sdf"))
        self.assertEqual(len(st), 24)
        self.assertEqual(len(st.bonds), 25)
        counts = {}
        for atom in st.atoms:
            counts[atom.element] = counts.get(atom.element, 0) + 1
        self.assertEqual(counts, {"C": 8, "H": 10, "N": 4, "O": 2})

    def test_mol_without_element_column(self) -> None:
        text = (
            "test\n  mol\n\n"
            "  2  1  0  0  0  0  0  0  0  0999 V2000\n"
            "    0.0000    0.0000    0.0000 C   0  0\n"
            "    1.0900    0.0000    0.0000 C   0  0\n"
            "  1  2  1  0  0  0  0\n"
        )
        st = read_mol(text)
        self.assertEqual(len(st), 2)
        self.assertEqual(len(st.bonds), 1)

    def test_unknown_extension(self) -> None:
        with self.assertRaises(ValueError):
            read_structure("molecule.abc")


class SceneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.structure = read_structure(os.path.join(SAMPLES, "caffeine.sdf"))

    def test_scene_contains_one_sphere_per_atom(self) -> None:
        text = sdl.generate_pov(self.structure, sdl.SceneParams())
        self.assertIn("#version 3.7;", text)
        self.assertEqual(text.count("\nsphere {"), len(self.structure.atoms))
        self.assertIn("camera {", text)
        self.assertIn("light_source {", text)

    def test_bonds_are_two_tone_cylinders(self) -> None:
        benzene = read_structure(os.path.join(SAMPLES, "benzene.xyz"))
        text = sdl.generate_pov(benzene, sdl.SceneParams())
        self.assertEqual(text.count("cylinder {"), len(benzene.bonds) * 2)

    def test_double_bonds_use_parallel_cylinders(self) -> None:
        # Caffeine's SDF has explicit double bonds (C=O and ring bonds)
        text = sdl.generate_pov(self.structure, sdl.SceneParams())
        expected = 0
        for bond in self.structure.bonds:
            order = int(round(bond.order))
            order = order if 1 <= order <= 3 else 1
            expected += 2 * order
        self.assertEqual(text.count("cylinder {"), expected)
        self.assertGreater(expected, len(self.structure.bonds) * 2)

    def test_spacefill_has_no_bonds(self) -> None:
        params = sdl.SceneParams(style="spacefill")
        text = sdl.generate_pov(self.structure, params)
        self.assertEqual(text.count("cylinder {"), 0)

    def test_radiosity_can_be_switched_off(self) -> None:
        text = sdl.generate_pov(self.structure, sdl.SceneParams(radiosity=False))
        self.assertIn("#declare RADIOSITY = 0;", text)
        text = sdl.generate_pov(self.structure, sdl.SceneParams(radiosity=True))
        self.assertIn("#ifndef(RADIOSITY)", text)

    def test_transparency_uses_rgbt_and_disables_union(self) -> None:
        text = sdl.generate_pov(self.structure, sdl.SceneParams(transparency=0.5))
        self.assertIn("rgbt <", text)
        self.assertNotIn("union {", text)

    def test_opaque_scene_is_packed_in_a_union(self) -> None:
        text = sdl.generate_pov(self.structure, sdl.SceneParams(alpha=False))
        self.assertEqual(text.count("union {"), 1)
        self.assertNotIn("rgbt <", text)
        packed = sdl.generate_pov(
            self.structure, sdl.SceneParams(alpha=False, pack_scene=False))
        self.assertNotIn("union {", packed)

    def test_transparency_is_clamped(self) -> None:
        text = sdl.generate_pov(self.structure, sdl.SceneParams(transparency=2.0))
        self.assertIn(", 0.850>", text)

    def test_alpha_background(self) -> None:
        text = sdl.generate_pov(self.structure, sdl.SceneParams(alpha=True))
        self.assertIn("rgbt <1, 1, 1, 1>", text)

    def test_avogadro_material_matches_upstream_finish(self) -> None:
        text = sdl.generate_pov(self.structure, sdl.SceneParams(material="avogadro"))
        self.assertIn(
            "ambient 0.8 diffuse 1.0 specular 1.0 roughness 0.005 metallic 0.5",
            text,
        )

    def test_avogadro_lighting_uses_parallel_lights(self) -> None:
        params = sdl.SceneParams()
        params.light.style = "avogadro"
        text = sdl.generate_pov(self.structure, params)
        self.assertEqual(text.count("\n    parallel"), 2)
        self.assertIn("point_at", text)
        self.assertNotIn("area_light", text)
        self.assertIn("ambient_light rgb <0.2, 0.2, 0.2>", text)
        # Upstream diffuse values (LIGHT0 = 1.0, LIGHT1 = 0.3)
        self.assertIn("1.00000, 1.00000, 1.00000", text)
        self.assertIn("0.30000, 0.30000, 0.30000", text)
        self.assertIn("fade_power 0", text)

    def test_avogadro_preset_bundles_a_coherent_look(self) -> None:
        params = sdl.SceneParams(alpha=False)
        sdl.apply_preset(params, "avogadro")
        self.assertEqual(params.material, "avogadro")
        self.assertEqual(params.light.style, "avogadro")
        self.assertFalse(params.floor)
        self.assertEqual(params.background, "black")
        self.assertEqual(params.max_trace_level, 15)
        # The preset must not silently flip the transparent-background choice.
        self.assertFalse(params.alpha)
        text = sdl.generate_pov(self.structure, params)
        self.assertIn("background { color rgb <0, 0, 0> }", text)
        self.assertNotIn("sky_sphere", text)

    def test_black_background_is_flat_colour(self) -> None:
        text = sdl.generate_pov(
            self.structure, sdl.SceneParams(background="black", alpha=False))
        self.assertIn("background { color rgb <0, 0, 0> }", text)
        self.assertNotIn("sky_sphere", text)

    def test_custom_palette_overrides_selected_elements(self) -> None:
        params = sdl.SceneParams(palette="custom",
                                 custom_colors={"C": (255, 0, 0)})
        text = sdl.generate_pov(self.structure, params)
        # Carbon spheres become pure red (linear), hydrogens stay CPK white
        self.assertIn("1.00000, 0.00000, 0.00000", text)
        self.assertIn("1.00000, 1.00000, 1.00000", text)

    def test_custom_colour_beats_named_palette(self) -> None:
        from molpov import elements

        self.assertEqual(elements.color("C", "cpk"), (144, 144, 144))
        self.assertEqual(elements.color("C", "cpk", {"C": (1, 2, 3)}), (1, 2, 3))

    def test_hide_hydrogens_drops_h_atoms_and_bonds(self) -> None:
        params = sdl.SceneParams(hide_hydrogens=True)
        text = sdl.generate_pov(self.structure, params)
        hydrogens = sum(1 for a in self.structure.atoms if a.element == "H")
        self.assertEqual(hydrogens, 10)  # caffeine C8H10N4O2
        self.assertEqual(text.count("\nsphere {"),
                         len(self.structure.atoms) - hydrogens)
        h_bonds = sum(
            1 for b in self.structure.bonds
            if self.structure.atoms[b.a].element == "H"
            or self.structure.atoms[b.b].element == "H")
        remaining = len(self.structure.bonds) - h_bonds
        orders = 0
        for bond in self.structure.bonds:
            if self.structure.atoms[bond.a].element == "H" or \
                    self.structure.atoms[bond.b].element == "H":
                continue
            order = int(round(bond.order))
            orders += order if 1 <= order <= 3 else 1
        self.assertEqual(text.count("cylinder {"), orders * 2)
        self.assertGreater(remaining, 0)

    def test_read_pov_spheres(self) -> None:
        from molpov.povparse import read_pov

        text = (
            "sphere { <0,0,0>, 0.51 pigment { rgb <0.4,0.4,0.4> } }\n"
            "sphere { <1.5,0,0>, 0.51 pigment { rgb <0.4,0.4,0.4> } }\n"
            "sphere { <1.5,1.2,0>, 0.46 pigment { rgbt <1,0.05,0.05,0> } }\n"
        )
        st = read_pov(text)
        self.assertEqual(len(st), 3)
        self.assertEqual([a.element for a in st.atoms], ["C", "C", "O"])
        self.assertEqual(len(st.bonds), 2)

    def test_read_pov_recovers_bond_orders(self) -> None:
        from molpov.povparse import read_pov

        def spheres() -> str:
            return (
                "sphere { <0,0,0>, 0.51 pigment { rgb <0.4,0.4,0.4> } }\n"
                "sphere { <1.5,0,0>, 0.51 pigment { rgb <0.4,0.4,0.4> } }\n")

        single = spheres() + (
            "cylinder { <0,0,0>, <0.75,0,0>, 0.11 }\n"
            "cylinder { <0.75,0,0>, <1.5,0,0>, 0.11 }\n")
        st = read_pov(single)
        self.assertEqual(len(st.bonds), 1)
        self.assertEqual(int(round(st.bonds[0].order)), 1)

        double = spheres() + (
            "cylinder { <0,0.1,0>, <0.75,0.1,0>, 0.11 }\n"
            "cylinder { <0.75,0.1,0>, <1.5,0.1,0>, 0.11 }\n"
            "cylinder { <0,-0.1,0>, <0.75,-0.1,0>, 0.11 }\n"
            "cylinder { <0.75,-0.1,0>, <1.5,-0.1,0>, 0.11 }\n")
        st = read_pov(double)
        self.assertEqual(len(st.bonds), 1)
        self.assertEqual(int(round(st.bonds[0].order)), 2)

    def test_read_pov_keeps_phosphorus(self) -> None:
        from molpov.povparse import guess_element, read_pov
        from molpov.structure import Atom, Structure

        structure = Structure(atoms=[
            Atom("P", 0.0, 0.0, 0.0, index=0),
            Atom("O", 1.5, 0.0, 0.0, index=1),
            Atom("O", -1.5, 0.0, 0.0, index=2),
        ])
        structure.perceive_bonds()
        text = sdl.generate_pov(structure, sdl.SceneParams(alpha=False))
        elements = [atom.element for atom in read_pov(text).atoms]
        self.assertEqual(elements, ["P", "O", "O"])
        # Raw Avogadro GL colour and MolPOV's linearised colour both map to P.
        self.assertEqual(guess_element((1.0, 0.5, 0.0), 0.5), "P")
        self.assertEqual(guess_element((1.0, 0.2158, 0.0), 0.51), "P")
        self.assertEqual(guess_element((1.0, 0.05, 0.05), 0.46), "O")

    def test_read_pov_rejects_empty_scene(self) -> None:
        from molpov.povparse import read_pov

        with self.assertRaises(ValueError):
            read_pov("camera { location <0,0,1> }")

    def test_custom_background_colour(self) -> None:
        params = sdl.SceneParams(background="custom", alpha=False,
                                 background_color=(0.1, 0.2, 0.3))
        text = sdl.generate_pov(self.structure, params)
        self.assertIn("background { color rgb <0.10000, 0.20000, 0.30000> }",
                      text)
        self.assertNotIn("sky_sphere", text)

    def test_pov_scene_camera_hint(self) -> None:
        from molpov.povparse import camera_hint, read_pov_scene

        text = (
            "camera { perspective location <0,0,10> angle 40 up <0,1,0> "
            "right <1.333,0,0> direction <0,0,-1> }\n"
            "sphere { <0,0,0>, 0.5 pigment { rgb <0.4,0.4,0.4> } }\n")
        scene = read_pov_scene(text)
        hint = camera_hint(scene)
        self.assertAlmostEqual(hint["yaw"], 180.0, places=1)
        self.assertAlmostEqual(hint["pitch"], 0.0, places=1)
        self.assertEqual(hint["fov"], 40.0)

    def test_full_command_quality_switches(self) -> None:
        from molpov.render import full_command_quality

        quality = full_command_quality(1024, 768)
        args = quality.cli_args()
        for flag in ("+Q9", "+A", "+AM2", "+UA", "+FN"):
            self.assertIn(flag, args)
        self.assertIn("+W1024", args)
        self.assertIn("+H768", args)
        display = quality.cli_args(display=True)
        for flag in ("+V", "+D", "+P", "+UD", "+UL", "+UV"):
            self.assertIn(flag, display)

    def test_avogadro_render_command_switches(self) -> None:
        from molpov.render import Renderer

        for flag in ("+Q9", "+A", "+AM2", "+UA", "+FN", "+V", "+D"):
            self.assertIn(flag, Renderer.AVOGADRO_SWITCHES)

    def test_headlight_can_be_combined_with_either_mode(self) -> None:
        studio = sdl.SceneParams()
        studio.light.headlight = True
        studio.light.headlight_intensity = 0.75
        text = sdl.generate_pov(self.structure, studio)
        self.assertEqual(text.count("\n    parallel"), 1)
        self.assertIn("0.75000, 0.75000, 0.75000", text)

        avogadro = sdl.SceneParams()
        avogadro.light.style = "avogadro"
        avogadro.light.headlight = True
        text = sdl.generate_pov(self.structure, avogadro)
        self.assertEqual(text.count("\n    parallel"), 3)

    def test_camera_uses_explicit_basis(self) -> None:
        params = sdl.SceneParams(width=800, height=400)
        text = sdl.generate_pov(self.structure, params)
        self.assertIn("direction <", text)
        self.assertNotIn("angle ", text)
        up = [line for line in text.splitlines() if line.strip().startswith("up <")][0]
        right = [line for line in text.splitlines()
                 if line.strip().startswith("right <")][0]
        up_vec = [float(v) for v in up.split("<")[1].split(">")[0].split(",")]
        right_vec = [float(v) for v in right.split("<")[1].split(">")[0].split(",")]
        up_len = sum(v * v for v in up_vec) ** 0.5
        right_len = sum(v * v for v in right_vec) ** 0.5
        # 38 deg vertical fov -> up spans 2*tan(19 deg); width = height * 2
        self.assertAlmostEqual(up_len, 2 * 0.3443276, places=3)
        self.assertAlmostEqual(right_len, up_len * 2.0, places=3)

    def test_deterministic_output(self) -> None:
        a = sdl.generate_pov(self.structure, sdl.SceneParams())
        b = sdl.generate_pov(self.structure, sdl.SceneParams())
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
