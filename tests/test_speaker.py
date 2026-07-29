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


if __name__ == "__main__":
    unittest.main()
