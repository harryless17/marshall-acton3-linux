"""Fumee sur les fonctions de peinture. Cairo rend en memoire, donc aucun
afficheur n'est requis.

Ces tests attestent l'absence de plantage et le fait que quelque chose est
reellement peint -- PAS la beaute, qui se juge a l'oeil.
"""
import os
import sys
import tempfile
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


class TestGlypheM(unittest.TestCase):
    """Le M de l'icone. La contrainte reelle est 16 px : GNOME rend les icones
    de barre petites, et un glyphe qui ne survit pas a cette taille ne sert a
    rien, quelle que soit son allure en 128."""

    TAILLES_M = (16, 22, 24, 32, 48, 64, 128)

    def test_toutes_les_tailles_peignent(self):
        for taille in self.TAILLES_M:
            s, cr = surface_et_contexte(taille, taille)
            ui.paint_m(cr, taille)
            self.assertTrue(a_peint_quelque_chose(s), f"M vide en {taille} px")

    def test_seize_pixels_couvre_une_part_utile_de_la_case(self):
        """Un M qui ne remplit presque rien serait techniquement "peint" tout
        en etant illisible. On exige une couverture plancher."""
        s, cr = surface_et_contexte(16, 16)
        ui.paint_m(cr, 16)
        s.flush()
        octets = s.get_data()
        opaques = sum(1 for i in range(3, len(octets), 4) if octets[i] > 80)
        self.assertGreater(opaques, 16 * 16 * 0.10,
                           f"seulement {opaques} pixels sur 256 en 16 px")

    def test_letat_desactive_differe(self):
        rendus = []
        for actif in (True, False):
            s, cr = surface_et_contexte(48, 48)
            ui.paint_m(cr, 48, actif=actif)
            s.flush()
            rendus.append(bytes(s.get_data()))
        self.assertNotEqual(rendus[0], rendus[1],
                            "le M eteint est identique au M allume")

    def test_taille_degeneree_ne_leve_pas(self):
        for taille in (1, 2, 5):
            s, cr = surface_et_contexte(taille, taille)
            ui.paint_m(cr, taille)      # ne doit pas lever


class TestPixbufDeLicone(unittest.TestCase):
    def test_taille_demandee_et_canal_alpha(self):
        pb = ui.icon_pixbuf(22)
        self.assertEqual((pb.get_width(), pb.get_height()), (22, 22))
        self.assertTrue(pb.get_has_alpha(), "pas de canal alpha : fond opaque")

    def test_pas_uniformement_transparent(self):
        pb = ui.icon_pixbuf(22)
        self.assertTrue(any(pb.get_pixels()), "pixbuf vide")

    def test_le_m_est_dore_et_non_bleu(self):
        """L'ARGB32 de cairo est pre-multiplie et range dans l'ordre de la
        machine -- BGRA en petit-boutien -- la RGBA de GdkPixbuf ni l'un ni
        l'autre. Une copie d'octets naive donne un M BLEU. Ce test est la
        seule chose qui distingue les deux, l'absence d'exception ne dit rien.
        """
        pb = ui.icon_pixbuf(64)
        octets, pas, canaux = pb.get_pixels(), pb.get_rowstride(), pb.get_n_channels()
        rouge = vert = bleu = compte = 0
        for y in range(64):
            for x in range(64):
                o = y * pas + x * canaux
                if canaux < 4 or octets[o + 3] > 200:
                    rouge += octets[o]
                    vert += octets[o + 1]
                    bleu += octets[o + 2]
                    compte += 1
        self.assertGreater(compte, 0, "aucun pixel opaque")
        rouge, vert, bleu = rouge / compte, vert / compte, bleu / compte
        self.assertGreater(rouge, bleu,
                           f"moyenne R={rouge:.0f} B={bleu:.0f} : le M est bleu, "
                           "l'ordre des canaux est inverse")
        self.assertGreater(vert, bleu, "le doré demande R et V au-dessus de B")

    def test_le_cache_rend_le_meme_objet(self):
        """_do_refresh tourne jusqu'a ~8 fois par seconde quand la molette
        physique tourne : re-rendre le glyphe a chaque fois serait du gaspillage
        pur."""
        self.assertIs(ui.icon_pixbuf(22, True), ui.icon_pixbuf(22, True))
        self.assertIsNot(ui.icon_pixbuf(22, True), ui.icon_pixbuf(22, False))


class TestInstallationDuTheme(unittest.TestCase):
    """install_icon_theme ecrit dans le HOME de l'utilisateur : il faut donc
    garder le vrai theme, exactement comme TestAutostart garde ~/.config."""

    def setUp(self):
        self.rep = tempfile.TemporaryDirectory()
        self.ancien = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.rep.name

    def tearDown(self):
        if self.ancien is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self.ancien
        self.rep.cleanup()

    def test_ecrit_toutes_les_tailles_du_theme(self):
        ecrits = ui.install_icon_theme()
        self.assertEqual(len(ecrits), len(ui.ICON_THEME_SIZES))
        for taille in ui.ICON_THEME_SIZES:
            attendu = os.path.join(self.rep.name, "icons", "hicolor",
                                   f"{taille}x{taille}", "apps",
                                   f"{ui.ICON_NAME}.png")
            self.assertTrue(os.path.exists(attendu), f"manque {attendu}")
            self.assertGreater(os.path.getsize(attendu), 0)

    def test_suit_xdg_data_home(self):
        for chemin in ui.install_icon_theme():
            self.assertTrue(
                chemin.startswith(self.rep.name),
                "install_icon_theme ignore XDG_DATA_HOME, donc le test "
                "ecrirait dans le vrai theme de l'utilisateur")

    def test_reinstaller_est_idempotent(self):
        premier = ui.install_icon_theme()
        contenu = [open(c, "rb").read() for c in premier]
        second = ui.install_icon_theme()
        self.assertEqual(premier, second)
        self.assertEqual(contenu, [open(c, "rb").read() for c in second])

    def test_base_explicite_ignore_lenvironnement(self):
        """install.sh peut vouloir imposer la destination."""
        with tempfile.TemporaryDirectory() as autre:
            for chemin in ui.install_icon_theme(base=autre):
                self.assertTrue(chemin.startswith(autre))


if __name__ == "__main__":
    unittest.main()
