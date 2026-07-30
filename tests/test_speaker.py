"""Tests d'integration : necessitent l'enceinte allumee et son identite BLE
appairee. Sautes automatiquement sinon, pour que la suite reste verte sur une
machine sans le materiel.

Chaque test qui ecrit restaure les valeurs d'origine dans son tearDown.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from marshall_ble import Speaker, decode_eq, UUID_EQ


class SpeakerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spk = Speaker()
        if not cls.spk.connect(timeout_s=30):
            raise unittest.SkipTest("enceinte Acton III indisponible")

    @classmethod
    def tearDownClass(cls):
        cls.spk.close()


class TestConnexion(SpeakerTestCase):
    def test_connectee(self):
        self.assertTrue(self.spk.is_connected())

    def test_caracteristique_eq_resolue(self):
        self.assertIsNotNone(self.spk._path(UUID_EQ))


class TestLecture(SpeakerTestCase):
    def test_etat_complet(self):
        st = self.spk.get_state()
        self.assertIsNotNone(st)
        for k in ("volume", "max_volume", "bass", "treble"):
            self.assertIn(k, st)

    def test_bornes_plausibles(self):
        st = self.spk.get_state()
        self.assertTrue(0 <= st["bass"] <= 10, st)
        self.assertTrue(0 <= st["treble"] <= 10, st)
        self.assertTrue(0 <= st["volume"] <= st["max_volume"], st)

    def test_volume_max_du_firmware(self):
        self.assertEqual(self.spk.get_state()["max_volume"], 31)

    def test_lecture_eq_passe_par_notify(self):
        # ReadValue direct sur l'EQ ne repond pas sur ce firmware ;
        # read_eq doit quand meme renvoyer une trame exploitable.
        raw = self.spk.read_eq()
        self.assertIsNotNone(raw)
        self.assertIsNotNone(decode_eq(raw))


if __name__ == "__main__":
    unittest.main()
