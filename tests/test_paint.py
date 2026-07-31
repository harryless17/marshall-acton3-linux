"""Fumee sur les fonctions de peinture. Cairo rend en memoire, donc aucun
afficheur n'est requis.

Ces tests attestent l'absence de plantage et le fait que quelque chose est
reellement peint -- PAS la beaute, qui se juge a l'oeil.
"""
import math
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


def clarte_de_bande(surface, x0, y0, w, h):
    """Clarte moyenne d'un rectangle de pixels. Meme ordre d'octets que
    clarte_moyenne : BGRA."""
    surface.flush()
    o, pas = surface.get_data(), surface.get_stride()
    somme = 0
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            i = y * pas + x * 4
            somme += 0.114 * o[i] + 0.587 * o[i + 1] + 0.299 * o[i + 2]
    return somme / max(1, w * h)


class TestPlaqueEnCapsule(unittest.TestCase):
    """La plaque reelle est une CAPSULE -- rayon egal a la moitie de la hauteur,
    donc des bouts demi-circulaires -- encastree dans le tolex. Deux proprietes
    se verifient et ne s'admettent pas : les arcs de _rounded_path tiennent a ce
    rayon-la, et rien n'est peint hors de la boite annoncee."""

    # La bande reelle : 446 de large pour 143 de haut, moins le creux.
    REELLE = (436, 133)

    def test_peint_a_tous_les_rapports_de_forme(self):
        for w, h in (self.REELLE, (60, 60), (400, 24), (30, 200)):
            s, cr = surface_et_contexte(w, h)
            ui.paint_brass(cr, 0, 0, w, h, radius=h / 2)
            self.assertTrue(a_peint_quelque_chose(s), f"capsule vide en {w}x{h}")

    def test_les_bouts_sont_bien_demi_circulaires(self):
        """Le vrai risque : a rayon = h / 2 les deux arcs d'un meme bout
        partagent leur centre, et une construction fragile donnerait des arcs
        croises ou une boucle. On compare le premier pixel peint de chaque ligne
        au bord theorique de la capsule."""
        w, h = self.REELLE
        s, cr = surface_et_contexte(w, h)
        ui.paint_brass(cr, 0, 0, w, h, radius=h / 2)
        s.flush()
        o, pas = s.get_data(), s.get_stride()
        rayon = h / 2.0
        for dy in (0, 20, 40, 60, 66):
            y = min(h - 1, int(rayon + dy))
            peints = [x for x in range(w) if o[y * pas + x * 4 + 3] > 120]
            attendu = rayon - math.sqrt(max(0.0, rayon ** 2 - dy ** 2))
            self.assertLess(
                abs(peints[0] - attendu), 2.5,
                f"a dy={dy} le bord est a {peints[0]} au lieu de {attendu:.1f}")
            self.assertLess(abs((w - 1 - peints[-1]) - attendu), 2.5,
                            f"a dy={dy} le bord droit ne suit pas la capsule")

    def test_ne_peint_rien_hors_de_sa_boite(self):
        """C'est ce qui autorise paint_recess a exister a part : la plaque ne
        peint que dedans, le creux ne peint que dehors. Le biseau suit maintenant
        le chemin sur 2 px de large, donc sans ecretage il baverait."""
        w, h, marge = self.REELLE[0], self.REELLE[1], 8
        for rayon in (4, h / 2):
            s, cr = surface_et_contexte(w + 2 * marge, h + 2 * marge)
            ui.paint_brass(cr, marge, marge, w, h, radius=rayon)
            s.flush()
            o, pas = s.get_data(), s.get_stride()
            dehors = 0
            for y in range(h + 2 * marge):
                for x in range(w + 2 * marge):
                    dedans = (marge <= x < marge + w and marge <= y < marge + h)
                    if not dedans and o[y * pas + x * 4 + 3] > 0:
                        dehors += 1
            self.assertEqual(dehors, 0,
                             f"{dehors} pixels peints hors de la boite "
                             f"(rayon {rayon})")


class TestCreuxDeLaPlaque(unittest.TestCase):
    """Le creux : le tolex doit remonter autour de la plaque. Ce qui se mesure
    ici n'est pas l'illusion -- ca se juge a l'oeil -- mais le fait que le tolex
    soit REELLEMENT assombri contre la plaque et REELLEMENT eclairci sur la
    levre. Sans ces deux ecarts il n'y a pas de depression, juste un contour."""

    W, H, MARGE = 436, 133, 14

    def rendre(self, creux):
        s, cr = surface_et_contexte(self.W + 2 * self.MARGE,
                                    self.H + 2 * self.MARGE)
        ui.paint_tolex(cr, self.W + 2 * self.MARGE, self.H + 2 * self.MARGE)
        if creux:
            ui.paint_recess(cr, self.MARGE, self.MARGE, self.W, self.H,
                            self.H / 2)
        ui.paint_brass(cr, self.MARGE, self.MARGE, self.W, self.H,
                       radius=self.H / 2)
        return s

    def test_le_tolex_est_assombri_juste_au_dessus_de_la_plaque(self):
        """La paroi HAUTE du creux tourne le dos a la lumiere, donc c'est la que
        l'ombre est la plus dense. On echantillonne au milieu de la longueur, ou
        la capsule est droite."""
        largeur, x0 = 120, self.MARGE + self.W // 2 - 60
        sans = clarte_de_bande(self.rendre(False), x0, self.MARGE - 3,
                               largeur, 3)
        avec = clarte_de_bande(self.rendre(True), x0, self.MARGE - 3,
                               largeur, 3)
        self.assertLess(avec, sans * 0.75,
                        f"le tolex colle a la plaque n'est pas assombri : "
                        f"{avec:.1f} avec creux contre {sans:.1f} sans")

    def test_la_levre_accroche_la_lumiere(self):
        """Au-dela de l'ombre, la crete du cuir remonte et s'eclaire. C'est cette
        inversion sombre puis clair qui se lit comme un relief : une simple bande
        sombre se lirait comme un contour epais."""
        largeur, x0 = 120, self.MARGE + self.W // 2 - 60
        sans = clarte_de_bande(self.rendre(False), x0, self.MARGE - 5,
                               largeur, 2)
        avec = clarte_de_bande(self.rendre(True), x0, self.MARGE - 5,
                               largeur, 2)
        self.assertGreater(avec, sans * 1.6,
                           f"pas de levre claire au-dessus de l'ombre : "
                           f"{avec:.1f} avec creux contre {sans:.1f} sans")

    def test_la_paroi_basse_est_eclairee(self):
        """Symetrique de l'ombre du haut, et c'est le meme raisonnement que
        l'arete basse de la fente du levier : dans un creux eclaire d'en haut, la
        paroi du bas recoit la lumiere."""
        largeur, x0 = 120, self.MARGE + self.W // 2 - 60
        y = self.MARGE + self.H
        sans = clarte_de_bande(self.rendre(False), x0, y, largeur, 1)
        avec = clarte_de_bande(self.rendre(True), x0, y, largeur, 1)
        self.assertGreater(avec, sans * 1.6,
                           f"la paroi basse n'est pas eclairee : {avec:.1f} "
                           f"contre {sans:.1f}")

    def test_le_creux_ne_peint_RIEN_dans_la_plaque(self):
        """L'invariant qui permet de l'appeler AVANT paint_brass sans que la
        plaque ait a le recouvrir : l'ecretage pair-impair laisse l'interieur
        vierge. Si cet ecretage sautait, l'ombre traverserait le laiton."""
        s, cr = surface_et_contexte(self.W + 2 * self.MARGE,
                                    self.H + 2 * self.MARGE)
        ui.paint_recess(cr, self.MARGE, self.MARGE, self.W, self.H, self.H / 2)
        s.flush()
        o, pas = s.get_data(), s.get_stride()
        dedans = 0
        for y in range(self.MARGE + 2, self.MARGE + self.H - 2):
            for x in range(self.MARGE + int(self.H / 2), self.MARGE + self.W
                           - int(self.H / 2)):
                if o[y * pas + x * 4 + 3] > 0:
                    dedans += 1
        self.assertEqual(dedans, 0, f"{dedans} pixels d'ombre dans la plaque")

    def test_les_deux_bouts_ne_sont_pas_peints_a_lidentique(self):
        """La lumiere vient du haut a GAUCHE : un creux dont les deux bouts se
        peignent pareil n'a pas de source de lumiere."""
        s = self.rendre(True)
        gauche = clarte_de_bande(s, self.MARGE - 5, self.MARGE + self.H // 2 - 8,
                                 4, 16)
        droite = clarte_de_bande(s, self.MARGE + self.W + 1,
                                 self.MARGE + self.H // 2 - 8, 4, 16)
        self.assertGreater(gauche, droite * 1.5,
                           f"bout gauche {gauche:.1f} contre bout droit "
                           f"{droite:.1f} : le creux est symetrique")


def clarte_moyenne(surface, cx, cy, r0, r1):
    """Clarte moyenne des pixels de l'anneau r0..r1 autour de (cx, cy).

    L'ARGB32 de cairo est PRE-MULTIPLIE et range dans l'ordre de la machine,
    donc BGRA en petit-boutien -- meme piege que compte_rougeatre. Les
    coefficients suivent cet ordre : 0.114 sur le bleu, 0.299 sur le rouge.
    """
    surface.flush()
    o, pas = surface.get_data(), surface.get_stride()
    somme = compte = 0
    for y in range(surface.get_height()):
        for x in range(surface.get_width()):
            if not (r0 <= math.hypot(x - cx + 0.5, y - cy + 0.5) <= r1):
                continue
            i = y * pas + x * 4
            somme += 0.114 * o[i] + 0.587 * o[i + 1] + 0.299 * o[i + 2]
            compte += 1
    return somme / max(1, compte)


def centre_de_lencre_sombre(surface, cx, cy, rayon, seuil=70):
    """Barycentre des pixels sombres du disque de rayon `rayon`, en coordonnees
    RELATIVES au centre. Sert a suivre le repere sur le capuchon."""
    surface.flush()
    o, pas = surface.get_data(), surface.get_stride()
    sx = sy = compte = 0
    for y in range(surface.get_height()):
        for x in range(surface.get_width()):
            if math.hypot(x - cx + 0.5, y - cy + 0.5) > rayon:
                continue
            i = y * pas + x * 4
            if 0.114 * o[i] + 0.587 * o[i + 1] + 0.299 * o[i + 2] < seuil:
                sx += x - cx
                sy += y - cy
                compte += 1
    return (sx / compte, sy / compte, compte) if compte else (0.0, 0.0, 0)


class TestDeuxTonsDeLaMolette(unittest.TestCase):
    """La molette est une piece DEUX TONS : corps sombre molete, capuchon de
    laiton clair. Ce qui est verrouille ici n'est pas l'allure -- ca se juge a
    l'oeil -- mais le RAPPORT de clarte entre les deux, sans lequel le dessin
    retomberait au dome dore uniforme d'avant sans qu'aucun test ne bronche."""

    RAYON = 28

    def peindre(self, fraction=0.5, actif=True, cote=100):
        """Sur du laiton, comme dans la fenetre reelle."""
        s, cr = surface_et_contexte(cote, cote)
        ui.paint_brass(cr, 0, 0, cote, cote)
        ui.paint_knob(cr, cote / 2, cote / 2, self.RAYON, fraction, actif=actif)
        return s

    def test_le_capuchon_est_franchement_plus_clair_que_le_corps(self):
        s = self.peindre()
        r = self.RAYON
        # Le capuchon monte a KNOB_CAP * R ; on echantillonne bien en dedans
        # (0.40 R) pour ne pas attraper son ombre de contact, et le flanc entre
        # 0.80 et 0.97 R, ou le moletage est trace.
        capuchon = clarte_moyenne(s, 50, 50, 0, r * 0.40)
        flanc = clarte_moyenne(s, 50, 50, r * 0.80, r * 0.97)
        self.assertGreater(capuchon, 120,
                           f"capuchon a {capuchon:.0f} : ce n'est plus du "
                           "laiton clair")
        self.assertLess(flanc, 80,
                        f"flanc a {flanc:.0f} : le corps n'est plus sombre")
        self.assertGreater(capuchon, flanc * 2.5,
                           f"capuchon {capuchon:.0f} contre flanc {flanc:.0f} :"
                           " la molette n'est plus deux tons")

    def test_le_rapport_survit_a_toutes_les_fractions(self):
        """Le capuchon et le corps ne tournent pas, mais le moletage et le
        repere si : le rapport ne doit pas dependre de la valeur."""
        for fraction in (0.0, 0.5, 1.0):
            s = self.peindre(fraction=fraction)
            capuchon = clarte_moyenne(s, 50, 50, 0, self.RAYON * 0.40)
            flanc = clarte_moyenne(s, 50, 50, self.RAYON * 0.80,
                                   self.RAYON * 0.97)
            self.assertGreater(capuchon, flanc * 2.5,
                               f"f={fraction} : capuchon {capuchon:.0f} contre "
                               f"flanc {flanc:.0f}")

    def test_hors_connexion_le_capuchon_reste_le_plus_clair(self):
        """Le voile gris couvre TOUTE la piece : il doit l'eteindre, pas
        l'aplatir. Une molette hors service reste une molette."""
        s = self.peindre(actif=False)
        capuchon = clarte_moyenne(s, 50, 50, 0, self.RAYON * 0.40)
        flanc = clarte_moyenne(s, 50, 50, self.RAYON * 0.80, self.RAYON * 0.97)
        self.assertGreater(capuchon, flanc * 2.0,
                           f"voile pose : capuchon {capuchon:.0f} contre flanc "
                           f"{flanc:.0f}")

    def test_le_repere_bascule_bien_dun_cote_a_lautre_du_capuchon(self):
        """Plus fort que la simple difference d'octets : l'encre sombre DU
        CAPUCHON doit changer de cote. Un moletage qui tournerait tout seul, le
        repere restant fixe, passerait un test de difference."""
        cap = self.RAYON * ui.KNOB_CAP * 0.94
        gauche = centre_de_lencre_sombre(self.peindre(fraction=0.0), 50, 50, cap)
        droite = centre_de_lencre_sombre(self.peindre(fraction=1.0), 50, 50, cap)
        self.assertGreater(gauche[2], 10, "aucune encre sombre sur le capuchon")
        self.assertLess(gauche[0], -1.0,
                        f"a la butee basse le repere n'est pas a gauche : {gauche}")
        self.assertGreater(droite[0], 1.0,
                           f"a la butee haute le repere n'est pas a droite : "
                           f"{droite}")
        # ANGLE_MIN et ANGLE_MAX sont symetriques : les deux butees pointent vers
        # le BAS, et c'est ce qui distingue un repere qui tourne d'un repere qui
        # glisse le long d'un axe.
        self.assertGreater(gauche[1], 0.5, f"butee basse pas vers le bas : {gauche}")
        self.assertGreater(droite[1], 0.5, f"butee haute pas vers le bas : {droite}")


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


def compte_rougeatre(surface):
    """Nombre de pixels franchement rouges.

    L'ARGB32 de cairo est PRE-MULTIPLIE et range dans l'ordre de la machine,
    donc BGRA en petit-boutien -- meme piege que icon_pixbuf. On lit donc
    octets[o + 2] pour le rouge, et on exige un alpha eleve avant de comparer
    les canaux, sans quoi les pixels a moitie couverts du bord des traits
    fausseraient le compte.

    Le seuil R - V > 60 est cale sur les deux teintes en presence : TICK_RED est
    a R - V = 158, et le laiton de la plaque comme TICK_RED_OFF sont a moins de
    30. Aucune des deux ne passe par accident.
    """
    surface.flush()
    octets = surface.get_data()
    compte = 0
    for o in range(0, len(octets), 4):
        b, v, r, a = octets[o], octets[o + 1], octets[o + 2], octets[o + 3]
        if a > 200 and r - v > 60 and r - b > 60:
            compte += 1
    return compte


class TestGraduationDeLaMolette(unittest.TestCase):
    """La graduation rouge autour d'une molette. Ce qui se teste ici n'est pas
    l'allure -- ca se juge a l'oeil -- mais le fait que le NOMBRE de reperes
    allumes suive la valeur, et dans le bon sens."""

    def contexte_sur_laiton(self, cote=120):
        """Sur du laiton et non sur du transparent : c'est le fond reel, et un
        rouge se compte differemment selon ce qu'il recouvre."""
        s, cr = surface_et_contexte(cote, cote)
        ui.paint_brass(cr, 0, 0, cote, cote)
        return s, cr

    def test_tous_les_rayons_et_toutes_les_fractions(self):
        for rayon in (8, 16, 28, 44):
            for fraction in (0.0, 0.5, 1.0):
                for actif in (True, False):
                    cote = int(rayon * 2 * ui.TICK_EXTENT) + 8
                    s, cr = surface_et_contexte(cote, cote)
                    ui.paint_knob_ticks(cr, cote / 2, cote / 2, rayon,
                                        fraction, actif=actif)
                    self.assertTrue(
                        a_peint_quelque_chose(s),
                        f"rien de peint a r{rayon} f{fraction} actif={actif}")

    def test_rayon_minuscule_ne_leve_pas(self):
        s, cr = surface_et_contexte(12, 12)
        ui.paint_knob_ticks(cr, 6, 6, 3, 0.5)
        self.assertTrue(a_peint_quelque_chose(s))

    def test_le_nombre_de_reperes_allumes_MONTE_avec_la_fraction(self):
        """Le coeur du dispositif. On compte les pixels rouges et on exige que
        ca CROISSE -- pas seulement que ca differe : un arc qui tournerait sans
        s'allonger passerait un test de simple difference, alors qu'il ne dirait
        plus le niveau."""
        comptes = []
        for fraction in (0.0, 0.5, 1.0):
            s, cr = self.contexte_sur_laiton()
            ui.paint_knob_ticks(cr, 60, 60, 28, fraction)
            comptes.append(compte_rougeatre(s))
        self.assertGreater(comptes[0], 0,
                           "a fraction 0 aucun repere n'est allume : un arc "
                           "entierement eteint se lit comme une panne")
        self.assertLess(comptes[0], comptes[1],
                        f"0.0 -> 0.5 ne fait pas monter le rouge : {comptes}")
        self.assertLess(comptes[1], comptes[2],
                        f"0.5 -> 1.0 ne fait pas monter le rouge : {comptes}")

    def test_a_zero_un_seul_repere_est_allume(self):
        """Regle explicite : le repere de la butee basse est allume DES la
        fraction 0. Le compte a 0 doit donc valoir a peu pres celui d'un seul
        repere -- ici on le compare a la fraction 0.1, qui en allume deux."""
        comptes = []
        for fraction in (0.0, 0.1):
            s, cr = self.contexte_sur_laiton()
            ui.paint_knob_ticks(cr, 60, 60, 28, fraction)
            comptes.append(compte_rougeatre(s))
        # Le repere 0 est une butee, donc plus long que le repere 1 : on ne peut
        # pas exiger le double exactement, seulement une nette augmentation.
        self.assertGreater(comptes[1], comptes[0] * 1.2,
                           f"le deuxieme repere ne s'allume pas : {comptes}")

    def test_a_un_tous_les_reperes_sont_allumes(self):
        """Aucun repere sombre ne doit rester a la butee haute : la comparaison
        est <= et non <, et un > laisserait le dernier eteint pour toujours."""
        s, cr = self.contexte_sur_laiton()
        ui.paint_knob_ticks(cr, 60, 60, 28, 1.0)
        plein = compte_rougeatre(s)
        s, cr = self.contexte_sur_laiton()
        ui.paint_knob_ticks(cr, 60, 60, 28, 0.95)
        presque = compte_rougeatre(s)
        self.assertGreater(plein, presque,
                           "le repere de butee haute ne s'allume jamais")

    def test_inactif_eteint_le_rouge(self):
        """Une enceinte deconnectee ne doit pas avoir l'air de jouer. On le
        MESURE : le compte de pixels rouges doit s'effondrer, pas simplement
        changer."""
        comptes = {}
        for actif in (True, False):
            s, cr = self.contexte_sur_laiton()
            ui.paint_knob_ticks(cr, 60, 60, 28, 1.0, actif=actif)
            comptes[actif] = compte_rougeatre(s)
        self.assertGreater(comptes[True], 0)
        self.assertLess(comptes[False], comptes[True] * 0.05,
                        f"le rouge ne s'eteint pas hors connexion : {comptes}")

    def test_inactif_peint_quand_meme_larc(self):
        """Eteindre le rouge ne doit pas effacer la graduation : la course
        reste imprimee sur la plaque, connectee ou non."""
        s, cr = surface_et_contexte(120, 120)
        ui.paint_knob_ticks(cr, 60, 60, 28, 0.6, actif=False)
        self.assertTrue(a_peint_quelque_chose(s))

    def test_larc_ne_touche_pas_le_dome(self):
        """Contrainte geometrique, et pas seulement esthetique : KNOB_MARGIN est
        calcule sur TICK_EXTENT, donc si un trait partait de l'interieur du dome
        ou depassait l'extent, le calcul de la marge serait faux."""
        rayon, cote = 28, 160
        s, cr = surface_et_contexte(cote, cote)
        ui.paint_knob_ticks(cr, cote / 2, cote / 2, rayon, 1.0)
        s.flush()
        octets, pas = s.get_data(), s.get_stride()
        dedans = dehors = 0
        for y in range(cote):
            for x in range(cote):
                if octets[y * pas + x * 4 + 3] <= 40:
                    continue
                d = math.hypot(x - cote / 2 + 0.5, y - cote / 2 + 0.5)
                if d < rayon:
                    dedans += 1
                if d > rayon * ui.TICK_EXTENT + 2:
                    dehors += 1
        self.assertEqual(dedans, 0, f"{dedans} pixels d'encre sous le dome")
        self.assertEqual(dehors, 0, f"{dehors} pixels d'encre au-dela de l'arc")


class TestBornesDeLarc(unittest.TestCase):
    """Le "0" et le maximum imprimes aux deux bouts de l'arc. Le point qui compte
    n'est pas leur dessin mais leur HONNETETE : c'est le maximum REEL de la
    molette qui est grave, jamais un 10 suppose, et rien n'est grave quand
    l'appelant ne le connait pas."""

    RAYON, COTE = 28, 100

    def peindre(self, maximum):
        s, cr = surface_et_contexte(self.COTE, self.COTE)
        ui.paint_brass(cr, 0, 0, self.COTE, self.COTE)
        ui.paint_knob_ticks(cr, self.COTE / 2, self.COTE / 2, self.RAYON, 0.5,
                            maximum=maximum)
        return s

    def zone(self, cote_droit):
        """Boite autour du centre theorique d'une borne, cf. TICK_LABEL_*.

        Volontairement etroite -- 12 x 12 -- parce que le repere de butee et son
        ombre passent a 9 px d'arc de la : une boite plus large les compterait et
        le test ne dirait plus rien du chiffre."""
        angle = ((ui.ANGLE_MAX + ui.TICK_LABEL_ANGLE) if cote_droit
                 else (ui.ANGLE_MIN - ui.TICK_LABEL_ANGLE))
        rayon = self.RAYON * ui.TICK_LABEL_RADIUS
        cx = self.COTE / 2 + math.sin(angle) * rayon
        cy = self.COTE / 2 - math.cos(angle) * rayon
        return int(cx - 6), int(cy - 6), 12, 12

    def octets_de_zone(self, surface, boite):
        """Les pixels bruts de la boite, pour comparer deux gravures."""
        surface.flush()
        o, pas = surface.get_data(), surface.get_stride()
        morceaux = []
        for y in range(boite[1], boite[1] + boite[3]):
            debut = y * pas + boite[0] * 4
            morceaux.append(bytes(o[debut:debut + boite[2] * 4]))
        return b"".join(morceaux)

    def encre_sombre(self, surface, boite):
        surface.flush()
        o, pas = surface.get_data(), surface.get_stride()
        compte = 0
        for y in range(boite[1], boite[1] + boite[3]):
            for x in range(boite[0], boite[0] + boite[2]):
                i = y * pas + x * 4
                if (0.114 * o[i] + 0.587 * o[i + 1] + 0.299 * o[i + 2]) < 60:
                    compte += 1
        return compte

    def test_sans_maximum_aucune_borne_nest_gravee(self):
        """Le defaut, et c'est un choix : mieux vaut pas de borne qu'une borne
        inventee. Le laiton doit rester vierge aux deux emplacements."""
        s = self.peindre(None)
        for cote_droit in (False, True):
            self.assertEqual(
                self.encre_sombre(s, self.zone(cote_droit)), 0,
                "de l'encre sombre la ou aucune borne n'a ete demandee")

    def test_les_deux_bornes_apparaissent(self):
        s = self.peindre(10)
        for cote_droit in (False, True):
            self.assertGreater(
                self.encre_sombre(s, self.zone(cote_droit)), 8,
                f"borne {'haute' if cote_droit else 'basse'} absente")

    def test_le_nombre_grave_suit_le_maximum_reel(self):
        """Le coeur de l'affaire : au volume, dont le maximum vaut 31, la borne
        haute ne doit pas etre celle d'une molette d'EQ. Deux maximums differents
        donnent donc deux gravures differentes -- et un mutant qui ecrirait "10"
        en dur passerait tous les autres tests de ce fichier."""
        zone = self.zone(True)
        self.assertNotEqual(self.octets_de_zone(self.peindre(10), zone),
                            self.octets_de_zone(self.peindre(31), zone),
                            'la borne haute est identique a 10 et a 31 : le '
                            "nombre grave ne suit pas le maximum")
        # Et un maximum a un seul chiffre pese moins qu'un maximum a deux.
        un_chiffre = self.encre_sombre(self.peindre(9), zone)
        deux_chiffres = self.encre_sombre(self.peindre(31), zone)
        self.assertGreater(deux_chiffres, un_chiffre * 1.3,
                           f'"31" ne pese pas plus que "9" : {deux_chiffres} '
                           f"contre {un_chiffre}")
        # La borne BASSE, elle, est le meme "0" quel que soit le maximum.
        self.assertEqual(
            self.octets_de_zone(self.peindre(10), self.zone(False)),
            self.octets_de_zone(self.peindre(31), self.zone(False)),
            "la borne basse depend du maximum")

    def test_les_bornes_restent_dans_la_boite_de_la_molette(self):
        """Contrainte geometrique, comme test_larc_ne_touche_pas_le_dome : la
        boite de la Knob fait (KNOB_RADIUS + KNOB_MARGIN) de demi-cote, et
        l'anneau de focus se pose a TICK_EXTENT * R + 2,2. Les bornes doivent
        tenir DEDANS et ne pas traverser l'anneau."""
        rayon = ui.KNOB_RADIUS
        cote = (ui.KNOB_RADIUS + ui.KNOB_MARGIN) * 2
        s, cr = surface_et_contexte(cote, cote)
        ui.paint_knob_ticks(cr, cote / 2, cote / 2, rayon, 0.5, maximum=31)
        s.flush()
        o, pas = s.get_data(), s.get_stride()
        limite = rayon * ui.TICK_EXTENT + 2.2
        dehors = 0
        for y in range(cote):
            for x in range(cote):
                if o[y * pas + x * 4 + 3] <= 40:
                    continue
                if math.hypot(x - cote / 2 + 0.5, y - cote / 2 + 0.5) > limite:
                    dehors += 1
        self.assertEqual(dehors, 0,
                         f"{dehors} pixels d'encre au-dela de l'anneau de focus")


class TestPeintureDuLevier(unittest.TestCase):
    """Le levier de mise en service. Ce qui se teste ici, c'est qu'il peint et
    que les deux positions ne donnent pas le meme dessin -- un levier dont on ne
    voit pas la position ne sert a rien."""

    TAILLES_LEVIER = ((ui.TOGGLE_WIDTH, ui.TOGGLE_HEIGHT), (16, 20), (60, 80))

    def test_les_deux_etats_et_les_deux_sensibilites_peignent(self):
        for w, h in self.TAILLES_LEVIER:
            for on in (True, False):
                for actif in (True, False):
                    s, cr = surface_et_contexte(w, h)
                    ui.paint_toggle(cr, 0, 0, w, h, on, actif=actif)
                    self.assertTrue(
                        a_peint_quelque_chose(s),
                        f"rien de peint en {w}x{h} on={on} actif={actif}")

    def test_haut_et_bas_ne_donnent_pas_le_meme_dessin(self):
        rendus = []
        for on in (True, False):
            s, cr = surface_et_contexte(ui.TOGGLE_WIDTH, ui.TOGGLE_HEIGHT)
            ui.paint_toggle(cr, 0, 0, ui.TOGGLE_WIDTH, ui.TOGGLE_HEIGHT, on)
            s.flush()
            rendus.append(bytes(s.get_data()))
        self.assertNotEqual(rendus[0], rendus[1],
                            "le levier ne bascule pas : les deux etats sont "
                            "peints a l'identique")

    def test_le_levier_bascule_bien_de_part_et_d_autre_du_milieu(self):
        """Plus fort qu'une simple difference : la MASSE d'encre claire doit
        changer de moitie. Un levier qui ne ferait que changer de teinte, ou
        qu'un dessin symetrique, passerait le test precedent."""
        w, h = ui.TOGGLE_WIDTH, ui.TOGGLE_HEIGHT
        clair = {}
        for on in (True, False):
            s, cr = surface_et_contexte(w, h)
            ui.paint_toggle(cr, 0, 0, w, h, on)
            s.flush()
            octets, pas = s.get_data(), s.get_stride()
            haut = bas = 0
            for y in range(h):
                for x in range(w):
                    o = y * pas + x * 4
                    # ARGB32 pre-multiplie, ordre machine : BGRA. Le seuil
                    # attrape le dome et le haut du fut, pas le laiton median.
                    if octets[o + 3] > 200 and octets[o + 2] > 205:
                        if y < h / 2:
                            haut += 1
                        else:
                            bas += 1
            clair[on] = (haut, bas)
        self.assertGreater(clair[True][0], clair[True][1] * 1.5,
                           f"allume, l'encre claire n'est pas en haut : {clair}")
        self.assertGreater(clair[False][1], clair[False][0] * 1.5,
                           f"eteint, l'encre claire n'est pas en bas : {clair}")

    def test_inactif_differe_de_actif(self):
        rendus = []
        for actif in (True, False):
            s, cr = surface_et_contexte(ui.TOGGLE_WIDTH, ui.TOGGLE_HEIGHT)
            ui.paint_toggle(cr, 0, 0, ui.TOGGLE_WIDTH, ui.TOGGLE_HEIGHT, True,
                            actif=actif)
            s.flush()
            rendus.append(bytes(s.get_data()))
        self.assertNotEqual(rendus[0], rendus[1],
                            "le levier hors service est identique au levier "
                            "en service")

    def test_ne_deborde_pas_de_sa_boite(self):
        """L'ombre portee du dome frole le bord de la platine a 1,3 px : sans
        l'ecretage elle baverait sur le tolex du pied. On peint donc dans une
        surface plus grande et on exige que la marge reste vierge."""
        w, h, marge = ui.TOGGLE_WIDTH, ui.TOGGLE_HEIGHT, 6
        for on in (True, False):
            s, cr = surface_et_contexte(w + 2 * marge, h + 2 * marge)
            ui.paint_toggle(cr, marge, marge, w, h, on)
            s.flush()
            octets, pas = s.get_data(), s.get_stride()
            dehors = 0
            for y in range(h + 2 * marge):
                for x in range(w + 2 * marge):
                    dedans = (marge <= x < marge + w and marge <= y < marge + h)
                    if not dedans and octets[y * pas + x * 4 + 3] > 0:
                        dehors += 1
            self.assertEqual(dehors, 0,
                             f"{dehors} pixels peints hors de la boite (on={on})")


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
