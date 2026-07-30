"""Fumee sur les fonctions de peinture. Cairo rend en memoire, donc aucun
afficheur n'est requis.

Ces tests attestent l'absence de plantage et le fait que quelque chose est
reellement peint -- PAS la beaute, qui se juge a l'oeil.
"""
import os
import sys
import unittest

import cairo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import marshall_ui as ui


TAILLES = ((420, 330), (120, 90), (900, 700))


def surface_et_contexte(w, h):
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    return s, cairo.Context(s)


def a_peint_quelque_chose(surface):
    """Vrai si au moins un pixel n'est plus transparent."""
    surface.flush()
    return any(surface.get_data())


class TestPeintureNePlantePas(unittest.TestCase):
    def test_tolex(self):
        for w, h in TAILLES:
            s, cr = surface_et_contexte(w, h)
            ui.paint_tolex(cr, w, h)
            self.assertTrue(a_peint_quelque_chose(s), f"tolex vide en {w}x{h}")

    def test_piping(self):
        for w, h in TAILLES:
            s, cr = surface_et_contexte(w, h)
            ui.paint_piping(cr, w, h)
            self.assertTrue(a_peint_quelque_chose(s))

    def test_brass(self):
        for w, h in TAILLES:
            s, cr = surface_et_contexte(w, h)
            ui.paint_brass(cr, 4, 4, w - 8, h - 8)
            self.assertTrue(a_peint_quelque_chose(s))

    def test_grille(self):
        for w, h in TAILLES:
            s, cr = surface_et_contexte(w, h)
            ui.paint_grille(cr, 4, 4, w - 8, h - 8)
            self.assertTrue(a_peint_quelque_chose(s))

    def test_logo(self):
        for w, h in TAILLES:
            s, cr = surface_et_contexte(w, h)
            ui.paint_logo(cr, w / 2, h / 2, h / 5)
            self.assertTrue(a_peint_quelque_chose(s),
                            "le logo n'a rien peint : police introuvable ?")


class TestPeintureDeLaMolette(unittest.TestCase):
    def test_toutes_les_fractions(self):
        for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
            s, cr = surface_et_contexte(80, 80)
            ui.paint_knob(cr, 40, 40, 30, fraction)
            self.assertTrue(a_peint_quelque_chose(s), f"fraction {fraction}")

    def test_rayon_minuscule(self):
        s, cr = surface_et_contexte(12, 12)
        ui.paint_knob(cr, 6, 6, 4, 0.5)      # ne doit pas lever
        self.assertTrue(a_peint_quelque_chose(s))

    def test_etat_inactif_peint_aussi(self):
        s, cr = surface_et_contexte(80, 80)
        ui.paint_knob(cr, 40, 40, 30, 0.5, actif=False)
        self.assertTrue(a_peint_quelque_chose(s))

    def test_les_deux_butees_ne_donnent_pas_le_meme_dessin(self):
        """Si le repere ne tournait pas, la molette serait muette."""
        rendus = []
        for fraction in (0.0, 1.0):
            s, cr = surface_et_contexte(80, 80)
            ui.paint_knob(cr, 40, 40, 30, fraction)
            s.flush()
            rendus.append(bytes(s.get_data()))
        self.assertNotEqual(rendus[0], rendus[1],
                            "le repere de la molette ne tourne pas")


if __name__ == "__main__":
    unittest.main()
