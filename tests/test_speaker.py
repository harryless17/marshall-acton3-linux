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


class TestEcriture(SpeakerTestCase):
    def setUp(self):
        self.origine = self.spk.get_state()
        self.assertIsNotNone(self.origine)

    def tearDown(self):
        # restauration systematique : on ne laisse jamais les reglages modifies
        self.spk.set_bass(self.origine["bass"])
        self.spk.set_treble(self.origine["treble"])
        self.spk.set_volume(self.origine["volume"])

    def test_set_bass_applique_et_relu(self):
        cible = 2 if self.origine["bass"] > 5 else 9
        self.assertTrue(self.spk.set_bass(cible))
        self.assertEqual(self.spk.get_state()["bass"], cible)

    def test_set_bass_preserve_le_treble(self):
        t0 = self.origine["treble"]
        self.spk.set_bass(2 if self.origine["bass"] > 5 else 9)
        self.assertEqual(self.spk.get_state()["treble"], t0)

    def test_set_treble_preserve_le_bass(self):
        b0 = self.origine["bass"]
        self.spk.set_treble(2 if self.origine["treble"] > 5 else 9)
        self.assertEqual(self.spk.get_state()["bass"], b0)

    def test_volume(self):
        cible = 6 if self.origine["volume"] > 10 else 15
        self.assertTrue(self.spk.set_volume(cible))
        self.assertEqual(self.spk.get_state()["volume"], cible)

    def test_valeur_hors_bornes_est_bornee(self):
        self.spk.set_bass(99)
        self.assertEqual(self.spk.get_state()["bass"], 10)


if __name__ == "__main__":
    unittest.main()
