"""Tests des fonctions pures du protocole. Aucun materiel requis."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from marshall_ble import (
    clamp, decode_eq, encode_eq, match_preset, BASS_MAX, TREBLE_MAX, PRESETS,
)


class TestClamp(unittest.TestCase):
    def test_dans_les_bornes(self):
        self.assertEqual(clamp(5, 0, 10), 5)

    def test_sous_la_borne(self):
        self.assertEqual(clamp(-3, 0, 10), 0)

    def test_au_dessus(self):
        self.assertEqual(clamp(99, 0, 10), 10)


class TestDecodeEq(unittest.TestCase):
    def test_valeurs_reelles_de_lenceinte(self):
        # trame observee sur le materiel : bass=10, treble=7
        self.assertEqual(decode_eq(bytes([0x0A, 0xFF, 0xFF, 0xFF, 0x07])), (10, 7))

    def test_trame_trop_courte(self):
        self.assertIsNone(decode_eq(bytes([0x0A, 0xFF])))

    def test_trame_vide(self):
        self.assertIsNone(decode_eq(b""))

    def test_none(self):
        self.assertIsNone(decode_eq(None))


class TestEncodeEq(unittest.TestCase):
    def test_les_trois_bandes_du_milieu_restent_intouchees(self):
        out = encode_eq(bass=6, treble=8)
        self.assertEqual(out, bytes([6, 0xFF, 0xFF, 0xFF, 8]))

    def test_bornage(self):
        self.assertEqual(encode_eq(bass=99, treble=-4),
                         bytes([BASS_MAX, 0xFF, 0xFF, 0xFF, 0]))

    def test_longueur_toujours_5(self):
        self.assertEqual(len(encode_eq(0, 0)), 5)


class TestPresets(unittest.TestCase):
    def test_les_quatre_presets_valides(self):
        self.assertEqual(
            {n: (p["bass"], p["treble"]) for n, p in PRESETS.items()},
            {"Neutre": (5, 5), "Films": (8, 6),
             "Musique": (10, 7), "Voix / podcast": (3, 8)},
        )

    def test_musique_correspond_aux_reglages_dorigine(self):
        # l'utilisateur doit retrouver son son d'un clic
        self.assertEqual(
            (PRESETS["Musique"]["bass"], PRESETS["Musique"]["treble"]), (10, 7))

    def test_match_exact(self):
        self.assertEqual(match_preset(8, 6), "Films")

    def test_aucun_match(self):
        self.assertIsNone(match_preset(4, 4))

    def test_un_preset_ne_definit_pas_de_volume(self):
        for p in PRESETS.values():
            self.assertNotIn("volume", p)


if __name__ == "__main__":
    unittest.main()
