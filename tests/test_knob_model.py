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


class TestReAncrageALaSaturation(unittest.TestCase):
    """Tirer au-dela d'une butee ne doit pas mettre le depassement en reserve.

    Sans re-ancrage, 100 px pousses au-dela du maximum devaient etre rendus en
    entier avant que la valeur ne redescende : 130 px mesures a la souris, et
    sous la main ca se lit comme une molette bloquee. C'est exactement le defaut
    qui ferait rejeter les molettes rotatives.
    """

    def test_demi_tour_immediat_apres_depassement_en_haut(self):
        k = volume(31)
        k.begin_drag()
        k.drag_to(100)                     # 100 px pousses au-dela de la butee
        self.assertEqual(k.value, 31, "la butee doit tenir")
        self.assertTrue(k.drag_to(90),
                        "10 px de demi-tour et la valeur ne bouge pas : le "
                        "depassement a ete capitalise")
        self.assertLess(k.value, 31)

    def test_demi_tour_immediat_apres_depassement_en_bas(self):
        k = volume(0)
        k.begin_drag()
        k.drag_to(-100)
        self.assertEqual(k.value, 0)
        self.assertTrue(k.drag_to(-90))
        self.assertGreater(k.value, 0)

    def test_le_depassement_nest_pas_capitalise_meme_enorme(self):
        """Pousser trois fois plus loin ne doit pas rendre le demi-tour trois
        fois plus long : chaque deplacement sature recale l'ancre."""
        loin, tres_loin = volume(31), volume(31)
        for k, pousse in ((loin, 100), (tres_loin, 600)):
            k.begin_drag()
            k.drag_to(pousse)
            k.drag_to(pousse - 10)
            # sans cette assertion le test serait creux : les deux resteraient
            # coinces a 31 et l'egalite tiendrait toute seule
            self.assertLess(k.value, 31,
                            f"pousse a {pousse} px, le demi-tour de 10 px n'a "
                            f"rien rendu")
        self.assertEqual(loin.value, tres_loin.value)

    def test_le_demi_tour_coute_au_plus_un_cran(self):
        """Le re-ancrage pose l'ancre pile sur la butee, donc le premier cran
        rendu ne peut pas demander plus d'un cran de geste."""
        for fabrique, maxi, course in ((volume, 31, TRAVEL_VOLUME_PX),
                                       (bass, 10, TRAVEL_EQ_PX)):
            k = fabrique(maxi)
            k.begin_drag()
            k.drag_to(100)
            px_par_cran = course / maxi
            recul = 0
            while k.value == maxi and recul <= px_par_cran + 1:
                recul += 1
                k.drag_to(100 - recul)
            self.assertLess(k.value, maxi,
                            f"apres {recul} px de demi-tour la valeur n'a "
                            f"toujours pas bouge (un cran vaut "
                            f"{px_par_cran:.1f} px)")

    def test_hors_saturation_la_calibration_est_intacte(self):
        """Le re-ancrage ne doit se voir QUE contre une butee : dans la plage,
        l'ancre posee par begin_drag reste la seule reference."""
        k = volume(0)
        k.begin_drag()
        k.drag_to(TRAVEL_VOLUME_PX / 2)
        milieu = k.value
        k.drag_to(TRAVEL_VOLUME_PX / 2)    # meme deplacement, meme valeur
        self.assertEqual(k.value, milieu)
        k.drag_to(0)                       # retour a l'attache : retour a zero
        self.assertEqual(k.value, 0)

    def test_la_course_pleine_reste_la_course_annoncee(self):
        """La promesse faite a l'utilisateur : toute la plage pour toute la
        course, ni plus ni moins."""
        k = volume(0)
        k.begin_drag()
        self.assertTrue(k.drag_to(TRAVEL_VOLUME_PX))
        self.assertEqual(k.value, 31)
        b = bass(0)
        b.begin_drag()
        self.assertTrue(b.drag_to(TRAVEL_EQ_PX))
        self.assertEqual(b.value, 10)


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
