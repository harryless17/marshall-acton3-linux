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


def evenement_clic(bouton=1):
    """Un Gdk.EventButton fabrique. Comme pour la molette, emettre le signal a
    la main court-circuite la propagation de GTK : aucune fenetre reelle n'est
    necessaire."""
    ev = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
    ev.button.button = bouton
    ev.button.x = 8.0
    ev.button.y = 8.0
    return ev


def evenement_touche(keyval):
    ev = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    ev.key.keyval = keyval
    return ev


@unittest.skipUnless(AFFICHEUR, "aucun Gdk.Display : widget GTK inconstructible")
class TestLevier(unittest.TestCase):
    """Le levier de mise en service. La propriete qui compte est la meme que
    pour Knob : le reflet de l'etat ne doit RIEN emettre, sinon Facade.update --
    jusqu'a huit fois par seconde tant que la molette physique de l'enceinte
    tourne -- ferait reecrire le fichier d'autostart en boucle."""

    def faire(self, on=False):
        levier = ui.Toggle(on=on)
        recus = []
        levier.connect("toggled", lambda _w, v: recus.append(v))
        return levier, recus

    def test_toggle_emet_et_bascule(self):
        levier, recus = self.faire()
        self.assertTrue(levier.toggle())
        self.assertEqual(recus, [True])
        self.assertTrue(levier.on)

    def test_le_clic_bascule_et_emet(self):
        levier, recus = self.faire()
        levier.emit("button-press-event", evenement_clic())
        self.assertEqual(recus, [True])
        self.assertTrue(levier.on)

    def test_le_clic_droit_ne_bascule_pas(self):
        """Le bouton 3 sert au menu contextuel, pas a la commande."""
        levier, recus = self.faire()
        levier.emit("button-press-event", evenement_clic(bouton=3))
        self.assertEqual(recus, [])
        self.assertFalse(levier.on)

    def test_espace_et_entree_basculent(self):
        for touche in (Gdk.KEY_space, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            levier, recus = self.faire()
            levier.emit("key-press-event", evenement_touche(touche))
            self.assertEqual(recus, [True], f"touche {touche} inerte")
            self.assertTrue(levier.on)

    def test_une_touche_de_direction_ne_bascule_pas(self):
        """Ce n'est pas un axe, c'est un etat : les fleches n'ont rien a y faire.
        Renvoyer False les laisse a la navigation de GTK."""
        levier, recus = self.faire()
        levier.emit("key-press-event", evenement_touche(Gdk.KEY_Up))
        self.assertEqual(recus, [])
        self.assertFalse(levier.on)

    def test_set_on_silently_nemet_PAS(self):
        """Le chemin de Facade.update. C'est la raison d'etre de la methode."""
        levier, recus = self.faire()
        levier.set_on_silently(True)
        self.assertEqual(recus, [])
        self.assertTrue(levier.on, ".on ne suit pas set_on_silently")
        levier.set_on_silently(False)
        self.assertEqual(recus, [])
        self.assertFalse(levier.on)

    def test_set_on_silently_repete_ne_change_rien(self):
        levier, recus = self.faire(on=True)
        for _ in range(8):
            levier.set_on_silently(True)
        self.assertEqual(recus, [])
        self.assertTrue(levier.on)

    def test_les_deux_chemins_se_suivent(self):
        """.on doit rester la seule verite, quel que soit le chemin emprunte."""
        levier, recus = self.faire()
        levier.toggle()                    # -> True, emet
        levier.set_on_silently(False)      # -> False, silencieux
        levier.toggle()                    # -> True, emet
        self.assertTrue(levier.on)
        self.assertEqual(recus, [True, True])

    def test_insensible_ne_bascule_pas(self):
        levier, recus = self.faire()
        levier.set_sensitive(False)
        self.assertFalse(levier.toggle())
        # GTK ne livre aucun evenement d'entree a un widget insensible, donc ces
        # deux chemins-la ne sont atteignables qu'en emettant a la main -- ce que
        # fait ce test. Les gardes sont des ceintures, pas des chemins morts.
        levier.emit("button-press-event", evenement_clic())
        levier.emit("key-press-event", evenement_touche(Gdk.KEY_space))
        self.assertEqual(recus, [])
        self.assertFalse(levier.on)

    def test_prend_le_focus_et_demande_une_taille(self):
        levier, _ = self.faire()
        self.assertTrue(levier.get_can_focus(),
                        "sans focus, aucun anneau et aucun clavier")
        # show() est indispensable : cf. TestKnob, GTK 3 rend 0 pour un widget
        # non visible et non toplevel.
        levier.show()
        self.assertGreaterEqual(levier.get_preferred_width()[1], ui.TOGGLE_WIDTH)
        self.assertGreaterEqual(levier.get_preferred_height()[1],
                                ui.TOGGLE_HEIGHT)


@unittest.skipUnless(AFFICHEUR, "aucun Gdk.Display : widget GTK inconstructible")
class TestLevierDansLaFacade(unittest.TestCase):
    """Le contrat que marshall-applet consomme : le nom du signal et la
    signature de update() ne doivent pas bouger, et une mise a jour programmee
    ne doit jamais faire reecrire le fichier d'autostart."""

    def faire(self):
        facade = ui.Facade(["Neutre", "Films"],
                           {"volume": 31, "bass": 10, "treble": 10})
        recus = []
        facade.connect("autostart-toggled", lambda _f, v: recus.append(v))
        return facade, recus

    def test_update_ne_declenche_aucune_emission(self):
        facade, recus = self.faire()
        etat = {"volume": 12, "max_volume": 31, "bass": 5, "treble": 5}
        for autostart in (True, False, True, True):
            facade.update(etat, True, {}, "Neutre", autostart)
        self.assertEqual(recus, [],
                         "update() a emis autostart-toggled : le fichier "
                         "d'autostart serait reecrit ~8 fois par seconde")

    def test_update_reflete_bien_l_etat_sur_le_levier(self):
        facade, _ = self.faire()
        etat = {"volume": 12, "max_volume": 31, "bass": 5, "treble": 5}
        facade.update(etat, True, {}, "Neutre", True)
        self.assertTrue(facade.autostart.on)
        facade.update(etat, True, {}, "Neutre", False)
        self.assertFalse(facade.autostart.on)

    def test_un_geste_sur_le_levier_emet_bien(self):
        facade, recus = self.faire()
        facade.autostart.toggle()
        self.assertEqual(recus, [True])


if __name__ == "__main__":
    unittest.main()
