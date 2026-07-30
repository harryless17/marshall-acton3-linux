"""Tests de l'arithmetique des molettes. Aucun GTK, aucun ecran.

C'est precisement pour rendre cette logique testable sans afficheur qu'elle
vit dans KnobModel et non dans le widget Knob.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from marshall_ui import KnobModel, TRAVEL_VOLUME_PX, TRAVEL_EQ_PX


def volume(v=0):
    return KnobModel(maximum=31, travel_px=TRAVEL_VOLUME_PX, value=v)


def bass(v=0):
    return KnobModel(maximum=10, travel_px=TRAVEL_EQ_PX, value=v)


class TestBornes(unittest.TestCase):
    def test_valeur_initiale_saturee_en_haut(self):
        self.assertEqual(volume(99).value, 31)

    def test_valeur_initiale_saturee_en_bas(self):
        self.assertEqual(volume(-5).value, 0)

    def test_step_ne_depasse_pas_le_maximum(self):
        k = volume(31)
        self.assertFalse(k.step(1), "step rend True alors que rien n'a change")
        self.assertEqual(k.value, 31)

    def test_step_ne_descend_pas_sous_zero(self):
        k = volume(0)
        self.assertFalse(k.step(-1))
        self.assertEqual(k.value, 0)


class TestStep(unittest.TestCase):
    def test_un_cran_de_molette_vaut_exactement_un(self):
        k = volume(20)
        self.assertTrue(k.step(1))
        self.assertEqual(k.value, 21)
        k.step(-1)
        self.assertEqual(k.value, 20)

    def test_step_rend_vrai_seulement_si_la_valeur_change(self):
        k = bass(5)
        self.assertTrue(k.step(1))
        self.assertFalse(k.step(0))


class TestGlisseRelatif(unittest.TestCase):
    """Cliquer sur une molette ne doit JAMAIS faire sauter la valeur la ou on
    a clique. On attrape, on tire."""

    def test_glisse_de_zero_ne_change_rien(self):
        k = volume(12)
        k.begin_drag()
        self.assertFalse(k.drag_to(0))
        self.assertEqual(k.value, 12)

    def test_toute_la_course_vers_le_haut_atteint_le_maximum(self):
        k = volume(0)
        k.begin_drag()
        k.drag_to(TRAVEL_VOLUME_PX)
        self.assertEqual(k.value, 31)

    def test_toute_la_course_vers_le_bas_atteint_zero(self):
        k = volume(31)
        k.begin_drag()
        k.drag_to(-TRAVEL_VOLUME_PX)
        self.assertEqual(k.value, 0)

    def test_deux_glisses_partent_de_lorigine(self):
        """Le piege : un drag_to cumulatif ferait doubler le deplacement.
        La moitie de la course, deux fois, doit rester la moitie."""
        k = volume(0)
        k.begin_drag()
        k.drag_to(TRAVEL_VOLUME_PX / 2)
        milieu = k.value
        k.drag_to(TRAVEL_VOLUME_PX / 2)
        self.assertEqual(k.value, milieu)

    def test_un_nouveau_begin_drag_repart_de_la_valeur_courante(self):
        k = volume(0)
        k.begin_drag()
        k.drag_to(TRAVEL_VOLUME_PX / 2)
        atteint = k.value
        k.begin_drag()
        k.drag_to(0)
        self.assertEqual(k.value, atteint)

    def test_un_micro_glisse_ne_change_pas_le_volume(self):
        """200 px pour 31 crans : 3 px ne doivent pas suffire a bouger."""
        k = volume(12)
        k.begin_drag()
        self.assertFalse(k.drag_to(3))
        self.assertEqual(k.value, 12)


class TestCourseDifferenciee(unittest.TestCase):
    """Sans courses distinctes, le volume (32 crans) serait environ trois fois
    plus nerveux que l'EQ (11 crans) pour un meme geste."""

    def test_le_volume_a_une_course_plus_longue_que_leq(self):
        self.assertGreater(TRAVEL_VOLUME_PX, TRAVEL_EQ_PX)

    def test_un_meme_geste_bouge_moins_le_volume_en_proportion(self):
        v, b = volume(0), bass(0)
        v.begin_drag()
        b.begin_drag()
        v.drag_to(50)
        b.drag_to(50)
        self.assertLess(v.value / 31, b.value / 10)


class TestMaximumVariable(unittest.TestCase):
    """Le maximum du volume vient du registre 0x08 de l'enceinte, pas d'une
    constante : il n'est connu qu'apres lecture de l'etat."""

    def test_reduire_le_maximum_sature_la_valeur(self):
        k = volume(31)
        self.assertTrue(k.set_maximum(20))
        self.assertEqual(k.value, 20)

    def test_augmenter_le_maximum_ne_touche_pas_la_valeur(self):
        k = volume(12)
        self.assertFalse(k.set_maximum(40))
        self.assertEqual(k.value, 12)

    def test_le_maximum_change_le_pas_du_glisse(self):
        k = KnobModel(maximum=10, travel_px=100, value=0)
        k.set_maximum(100)
        k.begin_drag()
        k.drag_to(100)
        self.assertEqual(k.value, 100)


class TestFraction(unittest.TestCase):
    """La fraction est ce que la peinture consomme : 0 = butee basse,
    1 = butee haute."""

    def test_zero_et_un(self):
        self.assertEqual(volume(0).fraction, 0.0)
        self.assertEqual(volume(31).fraction, 1.0)

    def test_maximum_nul_ne_divise_pas_par_zero(self):
        k = KnobModel(maximum=0, travel_px=100, value=0)
        self.assertEqual(k.fraction, 0.0)


if __name__ == "__main__":
    unittest.main()
