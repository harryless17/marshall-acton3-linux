"""Tests du widget Knob. Sautes sans afficheur : construire un widget GTK
demande un Gdk.Display.

L'arithmetique, elle, est testee sans ecran dans test_knob_model.py -- c'est
tout l'interet de la separation.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk  # noqa: E402

import marshall_ui as ui  # noqa: E402

AFFICHEUR = Gtk.init_check([])[0]


def evenement_molette(direction, dy=0.0, time=1000):
    """Un Gdk.EventScroll fabrique. Emettre le signal a la main court-circuite
    la propagation de GTK, donc aucune fenetre reelle n'est necessaire."""
    ev = Gdk.Event.new(Gdk.EventType.SCROLL)
    ev.scroll.direction = direction
    ev.scroll.delta_y = dy
    ev.scroll.time = time
    return ev


@unittest.skipUnless(AFFICHEUR, "aucun Gdk.Display : widget GTK inconstructible")
class TestKnob(unittest.TestCase):
    def faire(self, maximum=31, valeur=12):
        return ui.Knob("volume", maximum=maximum, travel_px=200, value=valeur)

    def test_le_signal_porte_la_valeur(self):
        k = self.faire()
        recus = []
        k.connect("value-changed", lambda _w, v: recus.append(v))
        k.step(1)
        self.assertEqual(recus, [13])

    def test_un_pas_qui_ne_change_rien_nemet_pas(self):
        k = self.faire(valeur=31)
        recus = []
        k.connect("value-changed", lambda _w, v: recus.append(v))
        k.step(1)                          # deja a la butee
        self.assertEqual(recus, [])

    def test_set_value_silently_nemet_pas(self):
        """C'est le chemin des mises a jour venues de l'enceinte : les
        reflechir vers le transport serait une boucle."""
        k = self.faire()
        recus = []
        k.connect("value-changed", lambda _w, v: recus.append(v))
        k.set_value_silently(25)
        self.assertEqual(recus, [])
        self.assertEqual(k.value, 25)

    def test_set_maximum_silently_nemet_pas(self):
        k = self.faire(valeur=31)
        recus = []
        k.connect("value-changed", lambda _w, v: recus.append(v))
        k.set_maximum_silently(20)         # sature 31 -> 20
        self.assertEqual(recus, [])
        self.assertEqual(k.value, 20)

    def test_insensible_ignore_les_pas(self):
        k = self.faire()
        k.set_sensitive(False)
        recus = []
        k.connect("value-changed", lambda _w, v: recus.append(v))
        k.step(1)
        self.assertEqual(recus, [])

    def test_la_cle_est_exposee(self):
        self.assertEqual(self.faire().key, "volume")

    def test_le_widget_demande_une_taille(self):
        k = self.faire()
        # show() est indispensable : en GTK 3, get_preferred_width() rend 0 pour
        # tout widget non visible et non toplevel, y compris un Gtk.Button nu.
        # Sans lui le test mesurerait ce court-circuit, pas notre taille.
        k.show()
        w, h = k.get_preferred_width()[1], k.get_preferred_height()[1]
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)


@unittest.skipUnless(AFFICHEUR, "aucun Gdk.Display : widget GTK inconstructible")
class TestMolette(unittest.TestCase):
    """La promesse la plus explicite faite a l'utilisateur : un cran de molette
    vaut exactement un cran de valeur."""

    def faire(self):
        return ui.Knob("volume", maximum=31, travel_px=200, value=0)

    def envoyer(self, k, ev):
        k.emit("scroll-event", ev)

    def test_un_cran_vaut_exactement_une_unite(self):
        k = self.faire()
        for i in range(1, 4):
            self.envoyer(k, evenement_molette(Gdk.ScrollDirection.UP, time=1000 + i))
            self.assertEqual(k.value, i)

    def test_un_cran_vers_le_bas_retire_exactement_une_unite(self):
        k = self.faire()
        k.set_value_silently(5)
        self.envoyer(k, evenement_molette(Gdk.ScrollDirection.DOWN, time=2000))
        self.assertEqual(k.value, 4)

    def test_dix_fractions_de_lisse_valent_un_seul_cran(self):
        """Regression sur l'arrondi binaire : dix ajouts de 0.1 donnent
        0.9999999999999999, donc une comparaison stricte a 1.0 avalait le cran,
        puis la fraction suivante en franchissait DEUX d'un coup. Un pave
        tactile envoie exactement ce genre de rafale."""
        k = self.faire()
        for i in range(10):
            self.envoyer(k, evenement_molette(Gdk.ScrollDirection.SMOOTH,
                                              dy=-0.1, time=3000 + i))
        self.assertEqual(k.value, 1, "dix dixiemes de cran doivent en valoir un")
        self.envoyer(k, evenement_molette(Gdk.ScrollDirection.SMOOTH,
                                          dy=-1.0, time=3100))
        self.assertEqual(k.value, 2, "le cran suivant ne doit pas en sauter deux")

    def test_une_rafale_de_pave_tactile_ne_va_pas_a_la_butee(self):
        """Sans accumulation, un cran de valeur par fraction recue : un seul
        effleurement de pave tactile aurait envoye le volume a la butee."""
        k = self.faire()
        for i in range(30):
            self.envoyer(k, evenement_molette(Gdk.ScrollDirection.SMOOTH,
                                              dy=-0.05, time=4000 + i))
        self.assertEqual(k.value, 1)     # 30 x 0.05 = 1.5 cran

    def test_le_jumeau_emule_ne_compte_pas_deux_fois(self):
        """Une souris a valuateurs fait naitre un lisse ET un appui de bouton
        4/5 emule par le serveur X. Meme horodatage, meme cause physique : un
        seul cran de valeur."""
        k = self.faire()
        self.envoyer(k, evenement_molette(Gdk.ScrollDirection.SMOOTH,
                                          dy=-1.0, time=5000))
        self.envoyer(k, evenement_molette(Gdk.ScrollDirection.UP, time=5000))
        self.assertEqual(k.value, 1)

    def test_le_jumeau_compte_dans_lautre_ordre_aussi(self):
        """L'ordre d'arrivee des deux jumeaux n'est pas garanti."""
        k = self.faire()
        self.envoyer(k, evenement_molette(Gdk.ScrollDirection.UP, time=6000))
        self.envoyer(k, evenement_molette(Gdk.ScrollDirection.SMOOTH,
                                          dy=-1.0, time=6000))
        self.assertEqual(k.value, 1)

    def test_des_crans_distincts_ne_sont_pas_avales(self):
        """La garde ne doit jamais manger un cran isole : des horodatages
        differents sont des gestes differents."""
        k = self.faire()
        for i in range(4):
            self.envoyer(k, evenement_molette(Gdk.ScrollDirection.UP, time=7000 + i))
        self.assertEqual(k.value, 4)

    def test_le_defilement_horizontal_ne_touche_a_rien(self):
        k = self.faire()
        k.set_value_silently(9)
        self.envoyer(k, evenement_molette(Gdk.ScrollDirection.LEFT, time=8000))
        self.envoyer(k, evenement_molette(Gdk.ScrollDirection.RIGHT, time=8001))
        self.assertEqual(k.value, 9)


if __name__ == "__main__":
    unittest.main()
