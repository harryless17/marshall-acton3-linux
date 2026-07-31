"""marshall_ui -- la facade dessinee de l'applet Marshall.

Ce module ne connait NI BlueZ, NI Speaker, NI D-Bus. Il expose des widgets et
des signaux ; c'est marshall-applet qui les relie a l'enceinte. Cette
frontiere est ce qui rend l'interface jugeable et testable sans materiel.

La regle d'architecture du projet s'applique ici : aucun widget n'appelle le
transport. Une Knob emet value-changed, et le debounce de l'applet ecrit.
"""
import math
import os

import cairo
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")        # sinon PyGIWarning a l'import
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GLib, GObject, Pango, PangoCairo   # noqa: E402

# Course de glisse, en pixels, pour parcourir toute la plage.
# Distinctes a dessein : le volume a 32 crans et l'EQ 11. A course egale, le
# volume serait environ trois fois plus nerveux pour un meme geste.
TRAVEL_VOLUME_PX = 200      # ~6 px par cran
TRAVEL_EQ_PX = 140          # ~13 px par cran


def _round_half_up(x):
    """Arrondi au plus proche, la moitie vers le haut.

    round() de Python arrondit vers le pair (round(0.5) == 0), ce qui
    demanderait le double de geste pour franchir le premier cran.
    """
    return int(math.floor(x + 0.5))


class KnobModel:
    """L'arithmetique d'une molette. Aucun GTK : c'est ici que vit toute la
    logique delicate, pour qu'elle se teste sans afficheur.

    Le glisse est RELATIF : begin_drag() fige la valeur de depart, et
    drag_to() applique un deplacement depuis ce point. Cliquer sur une molette
    ne deplace donc jamais la valeur -- on attrape, on tire.
    """

    def __init__(self, maximum, travel_px, value=0):
        self.maximum = max(0, int(maximum))
        self.travel_px = max(1, int(travel_px))
        self._value = self._clamp(value)
        self._origin = self._value

    def _clamp(self, v):
        return max(0, min(self.maximum, int(v)))

    @property
    def value(self):
        return self._value

    @property
    def fraction(self):
        """0.0 a la butee basse, 1.0 a la butee haute. Ce que la peinture
        consomme."""
        if self.maximum <= 0:
            return 0.0
        return self._value / self.maximum

    def _set(self, v):
        """Rend True seulement si la valeur a change : un glisse de trois
        pixels sans franchir de cran ne doit rien emettre."""
        nouveau = self._clamp(v)
        if nouveau == self._value:
            return False
        self._value = nouveau
        return True

    def set_value(self, v):
        return self._set(v)

    def begin_drag(self):
        self._origin = self._value

    def drag_to(self, dy_up_px):
        """dy_up_px POSITIF = geste vers le haut = valeur qui monte.

        L'axe y de GTK descend, donc l'appelant passe (y_depart - y_courant).

        RE-ANCRAGE A LA SATURATION : sans lui, tirer au-dela d'une butee mettait
        le depassement en reserve, et il fallait le rendre en entier avant que la
        valeur ne reparte -- 130 px mesures a la souris, qui sous la main se
        lisent comme une molette bloquee. On redefinit donc l'origine pour que le
        deplacement courant retombe pile sur la butee atteinte : le demi-tour
        repart alors au cran suivant.

        La calibration n'y touche pas : le pas reste maximum / travel_px par
        pixel, et hors saturation _origin n'est jamais reecrit, donc les courses
        pleines (197 px au volume, 133 px a l'EQ) sont inchangees.
        """
        pas = _round_half_up(dy_up_px * self.maximum / self.travel_px)
        brut = self._origin + pas
        changed = self._set(brut)
        if brut != self._value:
            # brut a ete rogne : on etait dehors, donc on recale l'ancre
            self._origin = self._value - pas
        return changed

    def step(self, delta):
        return self._set(self._value + delta)

    def set_maximum(self, maximum):
        """Le maximum du volume vient du registre 0x08 de l'enceinte, pas d'une
        constante : il n'est connu qu'apres lecture de l'etat."""
        self.maximum = max(0, int(maximum))
        return self._set(self._value)


# -- palette --------------------------------------------------------------
# Relevee sur les photos produit : tolex noir legerement bleute, laiton chaud,
# toile "salt and pepper" gris tres sombre.
TOLEX = (0.063, 0.063, 0.067)
# La toile n'est pas noire, elle est anthracite. Le nom "salt and pepper" dit
# exactement ca : des fils clairs ET des fils sombres qui se voient. Sur un fond
# a 0.086 la passe sombre du tissage ne pouvait plus creuser que de 0.03 alors
# que la passe claire montait de 0.16, donc un seul des deux sens ressortait et
# la toile prenait l'air d'un cotele diagonal. A 0.14 les deux ont de la place.
CLOTH = (0.140, 0.140, 0.157)
# Le fil clair de la toile, NEUTRE et non creme. Une teinte chaude faisait virer
# beige tout le champ -- mesure de l'ecart R-B sur le bandeau : +2,1 avec un fil
# creme, contre -2,8 ici, quand le fond CLOTH lui-meme est a -2,7. Le "salt" du
# salt and pepper est du fil blanc, pas du lin.
CLOTH_THREAD = (0.82, 0.82, 0.80)
GOLD_PIPING = (0.808, 0.659, 0.235)

# La molette parcourt 280 degres, comme un potentiometre reel : les butees
# doivent se voir, une rotation complete ne dirait pas ou est le zero.
ANGLE_MIN = math.radians(-140)
ANGLE_MAX = math.radians(140)


def _rounded_path(cr, x, y, w, h, radius):
    """Rectangle a coins arrondis. Borne le rayon : sur une bande fine, un
    rayon trop grand produit des arcs qui se croisent."""
    r = max(0.0, min(radius, w / 2, h / 2))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, math.radians(-90), 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.radians(90))
    cr.arc(x + r, y + h - r, r, math.radians(90), math.radians(180))
    cr.arc(x + r, y + r, r, math.radians(180), math.radians(270))
    cr.close_path()


def _weave(cr, x, y, w, h, step, light, dark):
    """Chaine et trame ORTHOGONALES, en tirets alternes.

    Tout tient dans l'entrelacement : chaque fil passe DESSUS sur une maille et
    DESSOUS sur la suivante, ce qu'on obtient en dephasant les tirets d'un pas
    entre les horizontaux et les verticaux. Deux series de traits pleins ne
    donnent qu'un grillage ; ce sont les passages alternes qui font lire du
    tissu.

    La trame etait diagonale, et c'est precisement ce qui ratait dans un champ
    large et court. Mesure sur le champ REEL de 446x149 : l'ecart-type de
    luminance du grain passe de 9,7 en diagonal a 26,5 en orthogonal. A 9,7 le
    motif se moyenne a l'oeil des 1:1 et la toile prend l'air d'un carbone tisse
    ou d'une tole perforee -- pas d'un textile.
    """
    cr.save()
    cr.set_line_width(1)
    # L'ordre compte : les fils clairs d'abord, les sombres par-dessus, sinon
    # les creux du tissage ne se referment pas sur les bosses.
    for phase, rgba, vertical in ((0, light, False), (step, light, True),
                                  (step, dark, False), (0, dark, True)):
        cr.set_source_rgba(*rgba)
        cr.set_dash([step, step], phase)
        limite = w if vertical else h
        p = 0.5
        while p < limite:
            if vertical:
                cr.move_to(x + p, y)
                cr.line_to(x + p, y + h)
            else:
                cr.move_to(x, y + p)
                cr.line_to(x + w, y + p)
            p += step
        cr.stroke()
    cr.restore()


def _flecks(cr, x, y, w, h, step, rgba, one_in):
    """Le "salt" du salt and pepper : des fils clairs semes irregulierement.

    Tirage pseudo-aleatoire fait a la main, et deterministe a dessein -- la
    toile ne doit pas scintiller d'un redessin a l'autre. C'est cette
    irregularite qui separe un textile d'une grille : l'entrelacement seul,
    parfaitement periodique, se lit encore comme une moustiquaire.
    """
    cr.save()
    cr.set_source_rgba(*rgba)
    graine = 1
    yy = y
    while yy < y + h:
        xx = x
        while xx < x + w:
            # congruence lineaire minuscule ; les bits de poids faible d'un LCG
            # sont notoirement mauvais, d'ou le decalage de 16 avant le modulo
            graine = (graine * 1103515245 + 12345) & 0x7FFFFFFF
            if (graine >> 16) % one_in == 0:
                cr.rectangle(xx, yy, 1, 1)
            xx += step
        yy += step
    cr.fill()
    cr.restore()


def paint_tolex(cr, w, h):
    """Le revetement du caisson : noir, avec un grain de vinyle grene.

    Le grain est fait de tirets et non de traits pleins. Un quadrillage plein
    de pas constant se lit comme du papier millimetre -- essaye et regarde. En
    dephasant une ligne sur deux d'une demi-periode, les croisements ne
    s'alignent plus et la trame se lit comme un grain irregulier.
    """
    cr.save()
    cr.set_source_rgb(*TOLEX)
    cr.rectangle(0, 0, w, h)
    cr.fill()
    cr.set_line_width(1)
    # Deux passes claires croisees pour les bosses, une passe sombre pour les
    # creux entre elles. Les alphas restent sous le seuil de perception a 1:1 :
    # on doit sentir une matiere, pas voir un motif.
    for step, rgba, vertical in ((3, (1, 1, 1, 0.045), False),
                                 (3, (1, 1, 1, 0.032), True),
                                 (3, (0, 0, 0, 0.34), True)):
        cr.set_source_rgba(*rgba)
        limit = w if vertical else h
        # Le dephasage appartient au trace, pas a la ligne : on regroupe donc
        # toutes les lignes paires en un stroke et toutes les impaires en un
        # autre, au pixel pres le meme resultat qu'un stroke par ligne pour
        # 4,3 -> 3,4 ms en 420x330. Le gros du cout reste la mise en tirets
        # elle-meme (0,2 ms avec des traits pleins) : c'est le prix du grain,
        # et il ne se paye qu'au reaffichage du fond, pas pendant un glisse.
        for dash_offset, first in ((0.0, 0.5), (2.5, 0.5 + step)):
            # 2 px de tiret, 3 px de vide : le tiret est plus court que le pas
            # de la trame croisee, donc les intersections sont clairsemees.
            cr.set_dash([2, 3], dash_offset)
            p = first
            while p < limit:
                if vertical:
                    cr.move_to(p, 0)
                    cr.line_to(p, h)
                else:
                    cr.move_to(0, p)
                    cr.line_to(w, p)
                p += 2 * step
            cr.stroke()
    cr.restore()


def paint_piping(cr, w, h, radius=7):
    """Le lisere dore du pourtour. Un des rares vrais indices de profondeur :
    il n'y a aucun moteur 3D ici, tout est peint."""
    cr.save()
    # Un creux sombre juste en dedans, avant le fil dore. Sans lui le lisere
    # n'est qu'un filet peint SUR le tolex ; avec lui, le fil a l'air pose
    # dessus et c'est cette ombre de 1 px qui porte tout le relief.
    _rounded_path(cr, 3.5, 3.5, w - 7, h - 7, max(0.0, radius - 2))
    cr.set_source_rgba(0, 0, 0, 0.6)
    cr.set_line_width(1.5)
    cr.stroke()

    _rounded_path(cr, 1.5, 1.5, w - 3, h - 3, radius)
    cr.set_source_rgba(*GOLD_PIPING, 0.7)
    cr.set_line_width(1.5)
    cr.stroke()
    cr.restore()


def paint_brass(cr, x, y, w, h, radius=4):
    """La plaque de laiton BROSSE qui porte les molettes.

    Trois choix la separent d'une feuille d'or polie, ce qu'elle a longtemps
    eu l'air d'etre :

    - la plage de valeurs est resserree. Un degrade creme -> or profond etale
      sur toute la hauteur est un reflet de chrome ; une plaque brossee sous
      une lumiere diffuse ne fait qu'un ecart de 1.7 entre son point le plus
      clair et le plus sombre, contre 2.9 dans la version d'avant.
    - la lumiere est une bande douce vers la moitie de la hauteur, pas une
      rampe monotone du haut vers le bas.
    - le brossage suit le grand axe de la plaque, donc horizontal. Sur 390 px
      de large par 100 de haut, des stries verticales contrarient la forme.
    """
    cr.save()
    _rounded_path(cr, x, y, w, h, radius)
    cr.clip()

    # Canal bleu autour de 51 % du rouge. A 42 % la plaque restait un or vif ;
    # le laiton vieilli d'un panneau de commande est nettement plus grise et
    # plus sombre que l'or neuf.
    g = cairo.LinearGradient(0, y, 0, y + h)
    for pos, rgb in ((0.00, (0.463, 0.396, 0.235)),
                     (0.16, (0.561, 0.482, 0.286)),
                     (0.46, (0.667, 0.580, 0.361)),
                     (0.58, (0.655, 0.569, 0.353)),
                     (0.84, (0.494, 0.420, 0.259)),
                     (1.00, (0.392, 0.329, 0.200))):
        g.add_color_stop_rgb(pos, *rgb)
    cr.set_source(g)
    cr.paint()

    # Brossage : traits clairs et sombres alternes au pas de 2 px, donc une
    # ligne sur deux de chaque teinte et aucune ligne vierge. Une seule serie
    # claire au pas de 3 px laissait deux lignes vides entre chaque trait, ce
    # qui se lit comme du velours cotele et non comme du metal brosse.
    cr.set_line_width(1)
    for phase, rgba in ((0.5, (1, 1, 1, 0.065)), (1.5, (0, 0, 0, 0.055))):
        cr.set_source_rgba(*rgba)
        yi = y + phase
        while yi < y + h:
            cr.move_to(x, yi)
            cr.line_to(x + w, yi)
            yi += 2
        cr.stroke()

    # Biseau volontairement sourd. Un filet quasi blanc sur une arete deja
    # pale etait le principal responsable de l'effet feuille polie ; ici le
    # degrade repart du sombre en haut, et 0.22 suffit a marquer l'arete.
    #
    # Le biseau SUIT LE CHEMIN, il n'est plus fait de deux traits droits d'un
    # bord a l'autre. C'etait sans consequence sur un rectangle a coins de 4 px ;
    # sur une capsule -- rayon = h / 2, cf. BrassPanel -- les deux traits ne
    # touchaient plus l'arete que sur la partie droite du contour, et les deux
    # bouts semi-circulaires restaient sans biseau, donc plats. Le trace est
    # ecrete au chemin, donc seule la moitie interieure du trait se voit, et le
    # degre de gris qu'il porte suit la hauteur : clair en haut, sombre en bas.
    biseau = cairo.LinearGradient(0, y, 0, y + h)
    biseau.add_color_stop_rgba(0.00, 1, 0.980, 0.925, 0.22)
    biseau.add_color_stop_rgba(0.45, 1, 0.980, 0.925, 0.05)
    biseau.add_color_stop_rgba(0.55, 0, 0, 0, 0.10)
    biseau.add_color_stop_rgba(1.00, 0, 0, 0, 0.38)
    _rounded_path(cr, x, y, w, h, radius)
    cr.set_source(biseau)
    cr.set_line_width(2)
    cr.stroke()
    cr.restore()


# -- le creux dans lequel la plaque est encastree --------------------------
# Sur le panneau reel la plaque n'est pas POSEE sur le tolex, elle est NOYEE
# dedans : le cuir remonte autour d'elle en une levre arrondie, et la plaque
# repose au fond de la depression. Ce qui vend une depression, ce n'est pas la
# forme de la plaque -- c'est l'ombre du cuir qui la surplombe.
#
# Marge, en pixels, que la plaque laisse libre autour d'elle pour ce creux. Elle
# sort du trace : les trois passes d'ombre font 2 * spread, spread et 2 px de
# large, ecretees a leur moitie exterieure, donc l'ombre porte sur spread px, et
# la levre eclairee se pose sur le dernier. 5 px a la taille reelle -- a 3 px les
# trois passes se confondent en un seul lisere qui se lit comme un contour, a
# 8 px la bande sombre devient un cadre et la plaque a l'air posee sur un
# passe-partout.
RECESS_SPREAD = 5


def paint_recess(cr, x, y, w, h, radius, *, spread=RECESS_SPREAD):
    """Le creux autour de la plaque (x, y, w, h) : peint DEHORS, jamais dedans.

    Fonction separee, et non un parametre de paint_brass, precisement pour ca :
    paint_brass ne peint rien hors de sa boite -- un test l'exige -- alors que
    tout ce qui suit est de l'ombre POSEE SUR LE TOLEX. Les deux ne peuvent pas
    partager un ecretage.

    A appeler AVANT paint_brass : l'ombre est tracee a cheval sur l'arete de la
    plaque et seule sa moitie exterieure survit a l'ecretage, donc la plaque doit
    ensuite recouvrir ce qui deborde a l'interieur.

    Ou va la lumiere, et pourquoi : toute la facade est eclairee d'en haut, comme
    le point chaud des molettes. Dans un creux, la paroi du HAUT tourne le dos a
    la lumiere -- elle est donc sombre, et c'est la que l'ombre est la plus
    dense -- tandis que la paroi du BAS lui fait face et s'eclaire. C'est le
    meme raisonnement que l'arete basse de la fente du levier, cf. paint_toggle.
    Une ombre uniforme tout autour ne donnerait qu'un halo, donc une plaque
    DECOUPEE et collee, pas une plaque encastree.
    """
    cr.save()
    # Ecretage a l'EXTERIEUR de la plaque : deux chemins et la regle du
    # pair-impair, le seul moyen d'obtenir un trou dans un ecretage cairo.
    cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
    cr.rectangle(x - spread, y - spread, w + 2 * spread, h + 2 * spread)
    _rounded_path(cr, x, y, w, h, radius)
    cr.clip()

    # Trois passes de plus en plus fines et opaques, comme l'ombre interne du
    # caisson dans paint_grille : ca donne un degrade vers l'exterieur sans
    # avoir a construire un vrai flou. Le degrade vertical porte la
    # dissymetrie haut / bas.
    for width, haut, bas in ((2.0 * spread, 0.34, 0.13),
                             (1.0 * spread, 0.42, 0.17),
                             (2.0, 0.55, 0.26)):
        ombre = cairo.LinearGradient(0, y, 0, y + h)
        ombre.add_color_stop_rgba(0.0, 0, 0, 0, haut)
        ombre.add_color_stop_rgba(1.0, 0, 0, 0, bas)
        _rounded_path(cr, x, y, w, h, radius)
        cr.set_source(ombre)
        cr.set_line_width(width)
        cr.stroke()

    # La paroi BASSE du creux, eclairee, collee a l'arete de la plaque. Elle
    # passe apres l'ombre, sinon l'ombre l'eteindrait.
    paroi = cairo.LinearGradient(0, y + h * 0.45, 0, y + h)
    paroi.add_color_stop_rgba(0.0, 1, 0.965, 0.878, 0.0)
    paroi.add_color_stop_rgba(1.0, 1, 0.965, 0.878, 0.34)
    _rounded_path(cr, x, y, w, h, radius)
    cr.set_source(paroi)
    cr.set_line_width(2.4)
    cr.stroke()

    # Un cran d'ombre en plus sur le BOUT DROIT. La lumiere vient du haut a
    # GAUCHE -- c'est la convention de toute la facade, cf. le point chaud des
    # molettes -- donc la levre de droite se retourne dans l'ombre. Sans cette
    # passe les deux bouts de la capsule se peignaient a l'identique, et un creux
    # symetrique n'a pas de source de lumiere.
    bout = cairo.LinearGradient(x + w - h * 0.55, 0, x + w + spread, 0)
    bout.add_color_stop_rgba(0.0, 0, 0, 0, 0.0)
    bout.add_color_stop_rgba(1.0, 0, 0, 0, 0.45)
    _rounded_path(cr, x, y, w, h, radius)
    cr.set_source(bout)
    cr.set_line_width(2.0 * spread)
    cr.stroke()

    # La crete de la levre, au bord EXTERIEUR du creux : le cuir s'y retourne
    # vers le haut, donc elle accroche la lumiere. Le chemin est la plaque
    # dilatee de spread - 1 ; comme le rayon grandit d'autant, une capsule
    # dilatee reste une capsule.
    #
    # DEUX passes, une par direction de la lumiere, et c'est la seule facon
    # d'articuler les bouts : le tolex est deja a une clarte de 16 sur 255, il ne
    # reste presque rien a lui retirer, donc aux deux bouts de la capsule -- ou
    # l'ombre du haut ne porte pas -- le creux ne peut se lire que par la
    # LUMIERE. Mesure le long du bout gauche a mi-hauteur avant ces passes :
    # 16,1 sans creux contre 10,1 avec, soit un ecart de six niveaux qu'on ne
    # voit pas. La crete, elle, monte a 52.
    d = max(0.0, spread - 1.0)
    _rounded_path(cr, x - d, y - d, w + 2 * d, h + 2 * d, radius + d)
    cr.set_line_width(1.6)
    for depart, arrivee, alpha in (
            ((0, y - d), (0, y + h * 0.55), 0.20),                 # arete haute
            ((x - d, 0), (x + h * 0.55, 0), 0.16)):                # bout gauche
        crete = cairo.LinearGradient(*depart, *arrivee)
        crete.add_color_stop_rgba(0.0, 1, 0.949, 0.855, alpha)
        crete.add_color_stop_rgba(1.0, 1, 0.949, 0.855, 0.0)
        cr.set_source(crete)
        cr.stroke_preserve()
    cr.new_path()
    cr.restore()


def paint_grille(cr, x, y, w, h, radius=3):
    """La toile tissee, et l'ombre interne qui creuse le caisson."""
    cr.save()
    _rounded_path(cr, x, y, w, h, radius)
    cr.clip()
    cr.set_source_rgb(*CLOTH)
    cr.paint()

    # Le tissage doit se voir A 1:1, sans loupe, DANS LE CHAMP REEL : c'est une
    # bonne part de ce qui rend une facade Marshall reconnaissable. Le maillon
    # faible etait la : cale sur une planche d'essai a peu pres carree, la trame
    # diagonale disparaissait dans les 446x149 du vrai bandeau.
    #
    # Maille de 2 px, entrelacee. Le sombre porte 0.55 contre 0.22 au clair, et
    # ce n'est pas une symetrie ratee : un fil clair a du champ libre sur un fond
    # a 0.14, un fil sombre n'en a presque pas. Le sombre sert aussi a retenir la
    # luminance, que les fils clairs font monter -- mesure sur le champ reel,
    # moyenne 40,6 contre 33,6 avant, pour un grain qui passe de 9,7 a 26,5. Une
    # toile noire qui s'eclaircit de sept niveaux reste une toile noire ; une
    # toile sans grain n'est pas une toile.
    _weave(cr, x, y, w, h, 2, CLOTH_THREAD + (0.22,), (0, 0, 0, 0.55))
    _flecks(cr, x, y, w, h, 2, CLOTH_THREAD + (0.30,), 5)

    # Ombre interne, en deux temps. Le voile radial seul devait monter tres haut
    # en opacite pour creuser les bords, et il noircissait alors le centre au
    # point d'y noyer le logo. On separe donc les deux roles : voile radial
    # doux pour la mise en volume, liseres sombres colles au bord pour le creux.
    #
    # Rayon pilote sur la DEMI-DIAGONALE, et non sur max(w, h) : dans un champ
    # large et court, max(w, h) * 0.72 posait la butee sombre tres au-dehors --
    # 321 px pour un coin situe a 235 -- donc toute la surface baignait dans la
    # rampe au lieu de ses seuls coins, et le voile rongeait les fils clairs sur
    # les deux tiers de la largeur. Sur la demi-diagonale la rampe epouse le
    # champ quelle que soit sa forme, et 0.30 suffit alors la ou il fallait 0.45.
    cx, cy = x + w / 2, y + h / 2
    demi = math.hypot(w, h) / 2
    shade = cairo.RadialGradient(cx, cy, demi * 0.25, cx, cy, demi)
    shade.add_color_stop_rgba(0, 0, 0, 0, 0.0)
    shade.add_color_stop_rgba(1, 0, 0, 0, 0.30)
    cr.set_source(shade)
    cr.paint()

    # Trois passes de plus en plus fines et opaques : le clip coupe la moitie
    # exterieure de chaque trait, ce qui donne un degrade d'ombre vers le bord
    # sans avoir a construire un vrai flou.
    for width, alpha in ((9, 0.16), (5, 0.20), (2, 0.26)):
        _rounded_path(cr, x, y, w, h, radius)
        cr.set_source_rgba(0, 0, 0, alpha)
        cr.set_line_width(width)
        cr.stroke()
    cr.restore()


# Chaine de repli pour le lettrage. Z003 (clone d'URW Chancery, livre avec les
# polices base-35) est present sur la machine cible et c'est vers lui que
# resout fc-match cursive. FRAGILITE ASSUMEE : sans police calligraphique, le
# logo retombe sur un italique quelconque -- la facade reste correcte, le
# lettrage moins juste.
LOGO_FONT = "Z003,URW Chancery L,Brush Script MT,cursive"


def paint_logo(cr, cx, cy, size, text="Marshall"):
    """Le lettrage dore, centre sur (cx, cy).

    Approximation typographique d'une marque deposee, pour un outil personnel
    non affilie. Aucun fichier de police ni visuel du fabricant n'est embarque
    dans le depot.
    """
    layout = PangoCairo.create_layout(cr)
    desc = Pango.FontDescription()
    desc.set_family(LOGO_FONT)
    desc.set_style(Pango.Style.ITALIC)
    desc.set_absolute_size(size * Pango.SCALE)
    layout.set_font_description(desc)
    layout.set_text(text, -1)
    lw, lh = layout.get_pixel_size()
    x, y = cx - lw / 2.0, cy - lh / 2.0

    cr.save()
    cr.set_source_rgba(0, 0, 0, 0.85)        # ombre portee du lettrage
    cr.move_to(x + 1, y + 1)
    PangoCairo.show_layout(cr, layout)

    g = cairo.LinearGradient(0, y, 0, y + lh)
    for pos, rgb in ((0.00, (0.992, 0.953, 0.788)),
                     (0.45, (0.851, 0.729, 0.314)),
                     (0.75, (0.647, 0.514, 0.110)),
                     (1.00, (0.788, 0.651, 0.227))):
        g.add_color_stop_rgb(pos, *rgb)
    cr.set_source(g)
    cr.move_to(x, y)
    PangoCairo.show_layout(cr, layout)
    cr.restore()


# -- l'icone de la barre systeme ------------------------------------------
# Un M dore, TRACE ICI et non repris du fabricant : meme arbitrage que
# paint_logo, une approximation typographique dans l'esprit du badge d'ampli
# pour un outil personnel non affilie. Aucun visuel Marshall n'est embarque.
#
# La contrainte qui a decide de toutes les proportions ci-dessous est 16 px :
# GNOME rend les icones de barre petites, et a 16 px il ne reste que 2 px de
# fut. Un serif filiforme ou une contre-forme etroite y disparaissent purement
# et simplement, donc le glyphe est LARGE, GRAS, et ses aretes verticales sont
# recalees sur la grille de pixels (cf. _m_path). Toutes ces valeurs ont ete
# reglees en regardant le rendu a 16 px d'abord, puis verifiees au-dessus.
M_WIDTH = 0.90          # largeur d'encre, en fraction du cote de la boite
M_HEIGHT = 0.76
M_STEM = 0.175          # futs verticaux : 3 px a 16, 2 px se lisait maigre
M_FLARE = 0.095         # debord du serif de pied, de chaque cote du fut
M_FLARE_H = 0.40        # part de la hauteur sur laquelle le pied s'evase
# Obliques plus MINCES que les futs, et c'est le contraste de trace d'une
# lettre a serifs -- pas une economie. A epaisseur egale (0.155 essaye
# d'abord) les deux contre-formes se reduisaient a des fentes et le M se
# lisait comme un bloc raye, y compris a 128 px.
M_DIAG = 0.115          # epaisseur PERPENDICULAIRE des obliques du V
# Plancher en PIXELS, pas en fraction, et il ne mord qu'en dessous de 19 px de
# cote -- donc a 16 seulement parmi les tailles installees. A cette taille
# 0.115 du cote ne fait que 1,8 px d'oblique : cairo la rend en deux colonnes a
# moitie couvertes, le V devient gris et la lettre perd son milieu. 2,2 px
# suffit a ce qu'une colonne soit pleine. Compare a l'oeil sur agrandissement
# x10 contre 2.0, 2.5 et 2.8 : a 2,5 le V engraisse et ferme les contre-formes.
M_DIAG_MIN = 2.2
# 0.80 : le V descend bas SANS toucher la ligne de pied, il reste un cinquieme
# de la hauteur sous sa pointe. A 0.74 le creux remontait si haut que le milieu
# de la lettre devenait une masse pleine.
M_TIP = 0.80            # profondeur de la pointe du V, en fraction de hauteur
M_TIP_W = 0.10          # largeur du plat de la pointe


def _m_path(cr, size):
    """Pose le contour du M dans une boite size x size a l'origine, et rend
    les deux ordonnees d'encre (haut, bas) dont le degrade a besoin.

    UN SEUL chemin ferme, et non trois traits empiles : les jonctions entre les
    futs et les obliques doivent etre franches. Deux formes qui se chevauchent
    laissent une couture des qu'un degrade ou un contour passe dessus, et a
    16 px cette couture fait un pixel entier.

    RECALAGE SUR LA GRILLE : les quatre aretes verticales des futs et les deux
    horizontales sont arrondies a l'entier. Cairo ne hinte rien, donc un fut de
    3 px pose a x = 1.34 se rend en quatre colonnes grises -- le M devient flou
    a la seule taille qui compte vraiment.
    """
    stem = max(2.0, float(round(M_STEM * size)))
    x0 = float(round((size - M_WIDTH * size) / 2.0))
    top = float(round((size - M_HEIGHT * size) / 2.0))
    base = size - top
    h = base - top

    # Futs : bord exterieur sur x0 / x1, largeur entiere, donc les deux aretes
    # de chaque fut tombent sur la grille.
    l_out, l_in = x0, x0 + stem
    r_out, r_in = size - x0, size - x0 - stem
    cx = size / 2.0

    flare = max(0.6, M_FLARE * size)
    flare_h = max(1.0, M_FLARE_H * h)
    tip_half = max(0.4, M_TIP_W * size / 2.0)
    y_tip = top + M_TIP * h

    # Oblique gauche : son bord GAUCHE va de (l_in, top) a (cx - tip_half,
    # y_tip) -- il part donc de l'arete interne du fut, ce qui place le sommet
    # de la contre-forme pile sur la ligne de tete.
    #
    # Son bord DROIT est ce meme bord decale PERPENDICULAIREMENT de M_DIAG puis
    # recoupe a y = top, d'ou le dx = epaisseur / uy. C'est la seule facon
    # d'obtenir une oblique d'epaisseur constante dont le sommet arrive lui
    # aussi pile sur la ligne de tete : un simple decalage horizontal donnerait
    # un trait qui maigrit avec la pente, et la pente change des qu'on touche a
    # M_TIP.
    dx_span = (cx - tip_half) - l_in
    dy_span = y_tip - top
    seg = math.hypot(dx_span, dy_span)
    ux, uy = dx_span / seg, dy_span / seg
    # Borne : passe cette epaisseur, le bord droit de l'oblique franchirait
    # l'axe avant la ligne de tete, le creux du V remonterait AU-DESSUS du haut
    # du glyphe et le contour se croiserait lui-meme. Meme precaution que le
    # rayon borne de _rounded_path ou le nombre de dents de paint_knob : ca ne
    # mord qu'a proportions absurdes, mais alors ca ne produit pas un monstre.
    dx_top = min(max(M_DIAG_MIN, M_DIAG * size) / uy,
                 dx_span - 0.10 * h * ux / uy)
    y_notch = top + (cx - (l_in + dx_top)) * uy / ux

    cr.new_path()
    # Pied gauche, cote exterieur. Serif EVASE et non rectangulaire : les
    # controles poses sur l'angle donnent une gorge concave, le raccord en
    # trompette du pied d'un badge d'ampli. A 16 px il ne reste qu'un pixel
    # d'evasement de chaque cote -- assez pour que le pied paraisse plus large
    # que le fut, ce qui est tout ce qu'on lui demande a cette taille.
    cr.move_to(l_out - flare, base)
    cr.curve_to(l_out, base, l_out, base, l_out, base - flare_h)
    cr.line_to(l_out, top)
    cr.line_to(l_in + dx_top, top)          # haut du fut + de l'oblique
    cr.line_to(cx, y_notch)                 # fond de l'encoche entre obliques
    cr.line_to(r_in - dx_top, top)
    cr.line_to(r_out, top)
    cr.line_to(r_out, base - flare_h)
    cr.curve_to(r_out, base, r_out, base, r_out + flare, base)
    cr.line_to(r_in - flare, base)          # pied droit, cote interieur
    cr.curve_to(r_in, base, r_in, base, r_in, base - flare_h)
    cr.line_to(r_in, top)                   # sommet de la contre-forme droite
    cr.line_to(cx + tip_half, y_tip)        # pointe du V
    cr.line_to(cx - tip_half, y_tip)
    cr.line_to(l_in, top)                   # sommet de la contre-forme gauche
    cr.line_to(l_in, base - flare_h)
    cr.curve_to(l_in, base, l_in, base, l_in + flare, base)
    cr.close_path()
    return top, base


def paint_m(cr, size, actif=True):
    """Le M dore, centre dans une boite size x size, sur fond TRANSPARENT.

    Aucune plaque derriere le glyphe : la barre de GNOME est sombre, et un fond
    opaque se lirait comme un autocollant colle sur la barre au lieu d'une
    icone. C'est l'alpha qui porte la forme.
    """
    cr.save()
    top, base = _m_path(cr, size)

    # Contour sombre TRACE AVANT le remplissage, donc a moitie recouvert par
    # lui : il ne deborde que vers l'exterieur et ne mange pas le fut, deja a
    # 3 px a 16. Il n'est pas decoratif -- l'or est a environ 0.72 de luminance,
    # donc sur une barre de theme CLAIR un M dore nu ne fait quasiment aucun
    # contraste. Le lisere sombre est ce qui le tient sur les deux themes.
    # PLAFONNE, et pas seulement proportionnel : a 0.055 du cote il faisait 7 px
    # a 128, soit un cadre noir qui devenait la forme dominante de l'icone. Le
    # lisere a un role de lisibilite, pas de dessin -- 1 px la ou il faut, et
    # jamais plus de 2,6 quelle que soit la taille.
    cr.set_source_rgba(0, 0, 0, 0.55)
    cr.set_line_width(max(1.0, min(size * 0.055, 2.6)))
    cr.set_line_join(cairo.LINE_JOIN_ROUND)      # sinon les pointes fusent
    cr.stroke_preserve()

    # Degrade plus resserre que celui de paint_logo, et decale vers le clair :
    # le lettrage de la toile est pose sur du tissu presque noir et peut se
    # permettre de plonger a 0.65 en bas, mais ici les pieds tomberaient dans le
    # noir de la barre et le M paraitrait coupe a mi-hauteur.
    g = cairo.LinearGradient(0, top, 0, base)
    for pos, rgb in ((0.00, (0.988, 0.941, 0.749)),
                     (0.42, (0.898, 0.780, 0.376)),
                     (1.00, (0.757, 0.612, 0.212))):
        g.add_color_stop_rgb(pos, *rgb)
    cr.set_source(g)

    if actif:
        cr.fill()
    else:
        cr.fill_preserve()
        # Voile NEUTRE ET CLAIR, a l'inverse de celui de paint_knob. La molette
        # est posee sur du laiton, un voile sombre l'y eteint ; le M est pose
        # sur une barre presque noire, et un voile sombre l'y EFFACERAIT au lieu
        # de le desactiver. On tire donc l'or vers un gris de clarte voisine :
        # la forme reste entiere et lisible, c'est la couleur qui s'eteint --
        # exactement ce que fait un theme d'icones pour un element inactif.
        cr.set_source_rgba(0.612, 0.608, 0.596, 0.78)
        cr.fill()
    cr.restore()


# Deux images possibles seulement, et _do_refresh de l'applet passe ici jusqu'a
# huit fois par seconde tant qu'on tourne la molette physique de l'enceinte.
# On garde donc les Pixbuf par (taille, etat) : un Pixbuf n'est jamais modifie
# ici, donc le partager entre appels est sans risque.
_ICON_CACHE = {}

ICON_NAME = "marshall-applet"
# Les tailles que reclame un theme hicolor. 16 et 24 servent la barre, les
# grandes le lanceur d'applications et l'apercu d'Alt-Tab.
ICON_THEME_SIZES = (16, 24, 32, 48, 64, 128)


def icon_pixbuf(size, actif=True):
    """Le M en GdkPixbuf, pret pour Gtk.StatusIcon.

    Gdk.pixbuf_get_from_surface et NON une copie d'octets : l'ARGB32 de cairo
    est pre-multiplie et range dans l'ordre de la machine -- donc BGRA en
    petit-boutien -- la RGBA de GdkPixbuf ni l'un ni l'autre. Une copie naive
    donne un M bleu, et c'est verifie dans les tests par la couleur du pixel et
    non par la seule absence d'exception.
    """
    size = int(size)
    cle = (size, bool(actif))
    pixbuf = _ICON_CACHE.get(cle)
    if pixbuf is None:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        paint_m(cairo.Context(surface), size, actif=actif)
        surface.flush()
        pixbuf = Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
        _ICON_CACHE[cle] = pixbuf
    return pixbuf


def install_icon_theme(base=None):
    """Ecrit le M dans le theme d'icones hicolor, et rend les chemins ecrits.

    Le Icon= d'un .desktop veut un nom de theme ou un chemin absolu. On pose
    donc le nom `marshall-applet` dans le theme de l'utilisateur, ce qui laisse
    GNOME choisir la taille selon l'endroit ou il affiche l'entree -- un chemin
    absolu le figerait a une seule image.

    Les PNG sont rendus PAR paint_m, et non depuis un SVG pose a cote : une
    seule source pour le glyphe, sinon les deux divergent au premier
    ajustement et personne ne s'en apercoit avant une capture d'ecran.

    L'environnement est lu A L'APPEL et non a l'import, comme autostart_path()
    dans marshall-applet : les tests pointent XDG_DATA_HOME sur un repertoire
    temporaire, et le vrai theme de l'utilisateur ne doit pas etre touche.
    """
    if base is None:
        base = os.environ.get("XDG_DATA_HOME",
                              os.path.expanduser("~/.local/share"))
    ecrits = []
    for taille in ICON_THEME_SIZES:
        dossier = os.path.join(base, "icons", "hicolor",
                               f"{taille}x{taille}", "apps")
        os.makedirs(dossier, exist_ok=True)
        chemin = os.path.join(dossier, f"{ICON_NAME}.png")
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, taille, taille)
        paint_m(cairo.Context(surface), taille)
        surface.write_to_png(chemin)         # ecrase : reinstaller est normal
        ecrits.append(chemin)
    return ecrits


# -- la molette, DEUX TONS -------------------------------------------------
# La commande reelle n'est pas une bille doree : c'est un CORPS conique presque
# noir, molete sur son flanc, coiffe d'un CAPUCHON de laiton brosse TOURNE en
# cercles concentriques, et le repere est une ligne sombre gravee sur ce
# capuchon. Le dome dore integral de la version d'avant se lisait ; il se lisait
# comme un bouton de table de mixage, pas comme une molette d'ampli.
#
# Rayon du capuchon, en fraction du rayon du corps. C'est LE reglage de la
# piece, compare a 1:1 sur planche a 0.62, 0.66, 0.68, 0.72 et 0.74 : le flanc
# molete mesure (1 - KNOB_CAP) * R, soit 9,0 px a r28. A 0.74 il tombe a 7,3 px
# et l'anneau noir -- la signature de la molette, ce qui la distingue d'une
# piece de laiton massif -- s'amincit au point de passer pour un simple contour ;
# a 0.62 il monte a 10,6 px et c'est le capuchon qui n'a plus la place de porter
# a la fois le tournage et le repere.
KNOB_CAP = 0.68
# Rampe du CORPS : presque noir, mais CHAUD -- meme famille que TICK_DARK, pour
# la meme raison. Un noir neutre au milieu d'un decor entierement chaud se lit
# comme un trou dans l'image. Le point chaud est decale en haut a gauche, comme
# partout sur cette facade, et il monte jusqu'a 0.24 : c'est le flanc d'un cone
# vu de dessus, il prend la lumiere sur sa moitie eclairee.
KNOB_BODY = ((0.00, (0.243, 0.216, 0.180)),
             (0.38, (0.145, 0.126, 0.098)),
             (0.72, (0.086, 0.072, 0.055)),
             (1.00, (0.031, 0.025, 0.020)))
# Rampe du CAPUCHON, volontairement PLATE au milieu et qui ne plonge qu'au bord.
# Une rampe a la maniere de l'ancien dome -- clair au point chaud, sombre des
# 0.5 R -- donnait une bille bombee : le capuchon reel est un disque a peine
# galbe. Verifie en x6 sur planche : avec la rampe plate le tournage se voit sur
# les deux tiers du disque, avec la rampe bombee il est avale par le degrade.
KNOB_CAP_RAMP = ((0.00, (0.941, 0.894, 0.749)),
                 (0.50, (0.878, 0.784, 0.522)),
                 (0.82, (0.769, 0.643, 0.349)),
                 (1.00, (0.494, 0.396, 0.192)))
# Periode du tournage, en pixels : un cercle clair et un cercle sombre. Compare
# en x6 a 1.8, 2.4 et 2.8, puis a 1:1 -- a 1.8 les anneaux se moyennent et le
# capuchon retombe a un degrade lisse, a 2.8 ils se comptent et le disque prend
# l'air d'une cible. 2.4 est le pas ou la texture se voit sans se compter.
# En PIXELS et non en fraction du rayon : c'est un etat de surface, il ne
# grandit pas avec la piece.
KNOB_TURN_STEP = 2.4


def paint_knob(cr, cx, cy, radius, fraction, actif=True):
    """Une molette deux tons, tournee selon fraction (0..1).

    `radius` est le rayon du CORPS, pas du capuchon : paint_knob_ticks et
    Knob._on_draw se placent tous les deux dessus.

    Le degrade du corps, celui du capuchon et le voile de lumiere ne tournent
    PAS -- seuls le moletage et le repere le font. C'est ce qui donne l'illusion
    d'un objet eclaire par le haut qu'on fait pivoter, plutot que d'une image
    qu'on fait tourner.
    """
    angle = ANGLE_MIN + (ANGLE_MAX - ANGLE_MIN) * max(0.0, min(1.0, fraction))
    cap_r = radius * KNOB_CAP
    cr.save()

    # Ombre portee degradee. Un disque noir plein simplement decale ne laissait
    # voir qu'un croissant a bord net sous la molette, qui se lit comme un
    # defaut de trace ; le degrade donne une ombre qui se dilue.
    sy = cy + max(1.0, radius * 0.12)
    shadow = cairo.RadialGradient(cx, sy, radius * 0.82, cx, sy, radius * 1.16)
    shadow.add_color_stop_rgba(0, 0, 0, 0, 0.50)
    shadow.add_color_stop_rgba(1, 0, 0, 0, 0.0)
    cr.set_source(shadow)
    cr.arc(cx, sy, radius * 1.16, 0, 2 * math.pi)
    cr.fill()

    # Le corps occupe TOUT le rayon : le capuchon se pose dessus ensuite, donc
    # l'anneau molete est ce qui reste visible du corps.
    body = cairo.RadialGradient(cx - radius * 0.42, cy - radius * 0.48,
                                max(0.5, radius * 0.10), cx, cy, radius * 1.04)
    for pos, rgb in KNOB_BODY:
        body.add_color_stop_rgb(pos, *rgb)
    cr.set_source(body)
    cr.arc(cx, cy, radius, 0, 2 * math.pi)
    cr.fill()

    cr.save()                            # moletage : ca tourne avec la valeur
    cr.translate(cx, cy)
    cr.rotate(angle)
    # Le nombre de dents suit le rayon, sinon les deux extremes ratent : 42
    # dents sur un rayon de 4 px, ca fait un demi-pixel d'arc par dent, donc une
    # bouillie grise. Le facteur 1.5 donne 42 dents a r28, soit un pas de 4,4 px
    # d'arc au milieu du flanc -- la place d'un creux ET d'une crete, ce qu'il
    # faut pour lire un moletage et non des rayons de roue.
    teeth = max(8, min(46, int(radius * 1.5)))
    cr.set_line_width(max(0.7, radius * 0.028))
    r0, r1 = cap_r * 1.01, radius * 0.985
    for i in range(teeth):
        a = 2 * math.pi * i / teeth
        # Le creux sombre PUIS la crete claire, decalee d'un demi-pas. Une seule
        # serie ne donne que des rayures gravees, comme le brossage de
        # paint_brass ou le moletage du levier : il faut les deux.
        for aa, rgba in ((a, (0, 0, 0, 0.48)),
                         (a + math.pi / teeth, (1, 0.945, 0.855, 0.16))):
            cr.set_source_rgba(*rgba)
            cr.move_to(r0 * math.cos(aa), r0 * math.sin(aa))
            cr.line_to(r1 * math.cos(aa), r1 * math.sin(aa))
            cr.stroke()
    cr.restore()

    # Arete du corps : le moletage s'arrete a 0.985 R, ce filet ferme la piece.
    # Sans lui les dents claires touchent le bord et la silhouette s'effiloche.
    cr.set_line_width(1.0)
    cr.set_source_rgba(0, 0, 0, 0.55)
    cr.arc(cx, cy, radius - 0.5, 0, 2 * math.pi)
    cr.stroke()

    disc = cairo.RadialGradient(cx - cap_r * 0.34, cy - cap_r * 0.40,
                                max(0.5, cap_r * 0.10), cx, cy, cap_r)
    for pos, rgb in KNOB_CAP_RAMP:
        disc.add_color_stop_rgb(pos, *rgb)
    cr.set_source(disc)
    cr.arc(cx, cy, cap_r, 0, 2 * math.pi)
    cr.fill()

    # Le TOURNAGE : des cercles concentriques, pas un degrade. C'est l'etat de
    # surface reel d'une piece passee au tour, et c'est ce qui separe un
    # capuchon de metal d'une pastille de plastique -- verifie en retirant les
    # anneaux sur planche, le disque redevient aussitot une bille vernie.
    cr.save()
    cr.arc(cx, cy, cap_r, 0, 2 * math.pi)
    cr.clip()
    cr.set_line_width(1.0)
    rr = cap_r - 0.6
    clair = True
    # On s'arrete a 2 px : en dessous, les cercles font moins de 13 px de
    # circonference et se rendent en une bouillie de pixels a moitie couverts au
    # centre exact du capuchon, pile ou le repere prend son origine.
    while rr > 2.0:
        cr.set_source_rgba(*((1, 1, 1, 0.12) if clair else (0, 0, 0, 0.14)))
        cr.arc(cx, cy, rr, 0, 2 * math.pi)
        cr.stroke()
        rr -= KNOB_TURN_STEP / 2.0
        clair = not clair

    # Voile de lumiere FIXE, ecrete au capuchon, pose APRES le tournage donc il
    # le glace -- meme role que l'ancien reflet du dome. Il ne tourne pas : la
    # lumiere de la piece vient de la fenetre, pas de la valeur.
    voile = cairo.LinearGradient(0, cy - cap_r, 0, cy + cap_r * 0.3)
    voile.add_color_stop_rgba(0, 1, 1, 1, 0.14)
    voile.add_color_stop_rgba(1, 1, 1, 1, 0.0)
    cr.set_source(voile)
    cr.paint()
    cr.restore()

    # Ce qui pose le capuchon SUR le corps plutot que dedans : son ombre de
    # contact tout autour, et son arete eclairee du cote de la lumiere. Le
    # premier filet est trace juste DEHORS, sur le pied des dents ; le second
    # juste DEDANS, en haut a gauche, sur 160 degres seulement -- un filet clair
    # tout autour ferait un anneau, donc une piece rapportee et non un relief.
    cr.set_line_width(1.4)
    cr.set_source_rgba(0, 0, 0, 0.45)
    cr.arc(cx, cy, cap_r + 0.5, 0, 2 * math.pi)
    cr.stroke()
    cr.set_line_width(1.0)
    cr.set_source_rgba(1, 0.976, 0.918, 0.42)
    cr.arc(cx, cy, cap_r - 0.6, math.radians(160), math.radians(320))
    cr.stroke()

    # Repere : une SEULE encre sombre, sans le halo creme de la version d'or
    # plein. Ce halo n'etait pas un ornement, il compensait un vrai probleme, et
    # les deux fonds sont mesures sur le rendu, halo exclu : sous l'ancien
    # repere le dome descendait a une clarte de 76 contre 26 pour l'ame, soit
    # 2,9:1 -- un trait de 2 px a ce contraste-la se noie. Le capuchon ne
    # descend pas sous 109 contre 20 ici, soit 5,4:1 a la pire fraction, donc
    # l'ame porte partout et retirer le halo rend au repere sa nettete de trait
    # grave. Il court du centre au bord du capuchon, comme sur la piece reelle.
    cr.save()
    cr.translate(cx, cy)
    cr.rotate(angle)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_source_rgba(0.098, 0.078, 0.043, 1)
    cr.set_line_width(max(1.4, radius * 0.085))
    cr.move_to(0, -cap_r * 0.94)
    cr.line_to(0, -cap_r * 0.12)
    cr.stroke()
    cr.restore()

    if not actif:
        # Voile gris : la molette reste lisible mais visiblement hors service.
        # Volontairement neutre et un peu froid -- teinte trop chaude ou trop
        # opaque, le laiton tourne au brun sale et ca ne dit plus "inactif"
        # mais "abime".
        cr.set_source_rgba(0.14, 0.145, 0.165, 0.56)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.fill()
    cr.restore()


# -- la graduation imprimee autour des molettes ---------------------------
# ONZE reperes, et non un par cran : c'est un choix d'OBJET, pas d'affichage. Le
# panneau reel porte une echelle 0..10 imprimee sur le laiton, alors que le
# volume compte 32 crans en interne. Un repere par cran donnerait un bargraphe
# de 32 segments, qui ne ressemble a rien de ce qui s'imprime sur une plaque --
# et 32 traits sur les 280 degres de la course laisseraient 5,4 px d'arc entre
# deux traits de 2 px, donc un peigne. La precision est deja rendue par le
# chiffre pose sous la molette ; la graduation ne sert qu'a lire le niveau SANS
# lire le nombre.
TICK_COUNT = 11
# Fractions du rayon du dome. Le vide de 0.10 R (2,8 px a r28) est ce qui separe
# la graduation du moletage, qui monte deja a 0.98 R : des reperes colles au dome
# se liraient comme des dents de plus.
TICK_GAP = 0.10
# 0.21 R, soit 5,9 px a r28. Compare a l'oeil sur planche a 1:1 contre 0.16 et
# 0.24 : a 0.16 les traits sont trapus et l'arc se lit comme un pointille, a
# 0.24 ils fusent et la molette prend l'air d'un soleil dessine. A 0.21 le trait
# est deux fois plus long que large et se lit comme un trait imprime.
TICK_LEN = 0.21
# Butees plus longues, comme le 0 et le 10 imprimes sur le panneau : ce sont les
# deux seules positions de la course qui ont un nom, et l'arc a besoin de bornes
# visibles pour se lire comme une echelle et non comme un semis de traits.
# Verifie a 1:1 : en les ramenant a TICK_LEN, l'arc perd ses extremites et on ne
# voit plus ou la course commence.
TICK_LEN_END = 0.29
# Largeur de trait, en fraction du rayon : 2,0 px a r28. Le plancher en PIXELS
# est ce qui tient les petits rayons, comme M_DIAG_MIN tient le M a 16 px -- un
# trait sous 1,5 px se rend en deux colonnes a moitie couvertes, et un rouge a
# moitie couvert devient rose.
TICK_WIDTH = 0.072
# Rayon exterieur de l'arc, en fraction du rayon du dome. Sert DEUX fois, d'ou
# la constante : elle pose KNOB_MARGIN, et elle place l'anneau de focus
# au-dela de la graduation au lieu de le faire passer dedans.
TICK_EXTENT = 1.0 + TICK_GAP + TICK_LEN_END          # 1.39

# Rouge IMPRIME, pas voyant d'alarme. R eleve, mais V franchement au-dessus de B
# (+0,11), donc un rouge CHAUD qui tire vers l'orange sans y basculer -- essaye a
# (0.800, 0.259, 0.075), l'arc devient orange et ce n'est plus la meme marque.
# Et volontairement CLAIR pour un rouge : sur planche a 1:1 un rouge profond
# (0.706, 0.137, 0.071) s'enfonce dans le laiton sombre du bas de la plaque, et
# les deux reperes de butee -- ceux qui portent l'information "au minimum" --
# sont justement les plus bas. Un trait de 2 px n'a pas la surface qu'il faut
# pour se permettre d'etre sombre.
TICK_RED = (0.816, 0.196, 0.086)
# Presque noir mais CHAUD, de la meme famille que le libelle .marshall-cap
# (#3a2c06) : un noir neutre sur du laiton fait une tache etrangere.
TICK_DARK = (0.086, 0.071, 0.043)
# Hors connexion, le rouge s'ETEINT. Meme intention que le voile de
# paint_knob : un gris neutre a peine froid, de clarte voisine du rouge qu'il
# remplace, donc l'arc garde sa forme et perd sa couleur. Une enceinte
# deconnectee ne doit pas avoir l'air de jouer.
TICK_RED_OFF = (0.404, 0.396, 0.412)

# -- les deux bornes chiffrees de l'arc ------------------------------------
# Le panneau reel imprime un "0" et un "10" de part et d'autre de l'arc, en bas a
# gauche et en bas a droite de chaque molette. On les pose, et il faut trancher un
# probleme d'HONNETETE au passage : le volume compte 32 crans en interne, donc un
# "10" imprime au bout de sa course serait un mensonge, et pas un mensonge
# inoffensif -- le chiffre pose SOUS la molette, lui, affiche la valeur interne,
# et une plaque qui annonce une echelle 0..10 sous un chiffre qui monte a 31 se
# contredit. L'oeil croit ce qui est imprime.
#
# On imprime donc le MAXIMUM REEL de la molette : 0..10 aux graves et aux aigus,
# ou c'est exactement ce que porte le panneau reel, et 0..31 au volume. Les deux
# autres options ont ete pesees. "0" / "MAX" met un mot au milieu de chiffres et
# fait perdre aux deux molettes d'EQ leur echelle juste, la seule qu'on ait le
# droit d'ecrire. Ne rien imprimer sur le volume laisse une molette sur trois sans
# bornes, et un arc avec un "0" solitaire se lit comme un dessin inacheve. Le
# maximum du volume est d'ailleurs LU sur l'enceinte (set_maximum_silently), donc
# la borne suit le materiel au lieu de le supposer.
#
# 16 degres au-dela de la butee : les reperes de butee occupent l'angle exact de
# la course, et un chiffre pose dessus se lirait comme un trait de plus. Compare
# en x3 a 12, 15 et 20 degres -- a 12 le "31" vient toucher le dernier repere, a
# 20 les deux bornes se rapprochent du bas et cessent de FLANQUER l'arc. A 16 il
# reste 9,2 px d'arc entre l'axe du repere et le centre du chiffre, dont 6,7 de
# vide franc.
TICK_LABEL_ANGLE = math.radians(16)
# Rayon du centre du chiffre, en fraction du rayon du dome. 1.18 le pose entre le
# pied et la tete des reperes (1.10 et 1.39), donc il ne deborde pas de la boite
# de la molette, et l'anneau de focus -- a 1.39 R + 2,2 px, soit 41,1 -- passe
# DEHORS sans le traverser : le coin le plus lointain d'un "31" tombe a 37,7.
TICK_LABEL_RADIUS = 1.18
# Corps du chiffre, en fraction du rayon : 8,4 px a r28. Subordonne au chiffre de
# la valeur (10 pt, ~13 px) et un cran sous le libelle .marshall-cap (8 pt,
# ~11 px) : sur la plaque reelle ces bornes sont la plus petite encre du panneau.
TICK_LABEL_SIZE = 0.30


def paint_knob_ticks(cr, cx, cy, radius, fraction, actif=True, *,
                     maximum=None):
    """La graduation autour d'une molette : rouge jusqu'a la valeur, sombre
    au-dela.

    `maximum` est le nombre imprime au bout HAUT de l'arc, "0" tenant l'autre
    bout. Il est facultatif et vaut None par defaut, auquel cas AUCUNE borne n'est
    imprimee : la fonction reste appelable par qui ne connait pas l'echelle, et
    surtout elle n'invente pas un "10" que rien ne garantit -- cf. le pave de
    commentaire de TICK_LABEL_ANGLE. Knob._on_draw passe toujours le maximum de
    son modele.

    A appeler AVANT paint_knob, et pas apres : l'ombre portee du dome tombe
    alors sur le pied des reperes, ce qui pose la graduation SOUS la molette --
    de l'encre sur la plaque, que la molette ombre -- au lieu de par-dessus, ou
    elle aurait l'air collee sur l'ombre.

    Le repere i est allume si sa position i / 10 est ATTEINTE par fraction. Le
    premier l'est donc toujours, sans cas particulier, puisque 0 <= fraction
    quelle que soit la valeur -- et c'est voulu : un arc entierement eteint se
    lit comme un dessin casse, pas comme une molette au minimum.

    Aucun epsilon dans cette comparaison, a l'inverse de SCROLL_EPSILON, et
    c'est verifie et non suppose : a l'EQ les deux membres sont la MEME division
    (i / 10 d'un cote, value / 10 de l'autre, maximum 10 aux graves comme aux
    aigus), or une division IEEE est correctement arrondie, donc les deux
    doubles sont egaux bit a bit. Ca compte : le preset "Voix / podcast" pose
    justement bass a 3, et un repere qui s'eteindrait pile sur un preset serait
    la premiere chose qu'on remarque.

    Cout mesure a r28 : 0,04 ms, contre 0,22 ms pour paint_knob. La question
    valait d'etre posee -- la molette est redessinee a chaque trame d'un glisse,
    releve a 31 dessins pour 200 px en 954 ms -- mais 22 segments de 6 px ne
    pesent rien face au dome et a ses quatre degrades.
    """
    fraction = max(0.0, min(1.0, fraction))
    span = ANGLE_MAX - ANGLE_MIN
    inner = radius * (1.0 + TICK_GAP)
    cr.save()
    # BUTT et non ROUND : un bout arrondi ajoute une demi-largeur de trait a
    # chaque extremite, donc l'arc deborderait de TICK_EXTENT -- sur quoi
    # KNOB_MARGIN est calcule au pixel pres.
    cr.set_line_cap(cairo.LINE_CAP_BUTT)
    cr.set_line_width(max(1.5, radius * TICK_WIDTH))
    for i in range(TICK_COUNT):
        position = i / (TICK_COUNT - 1)
        angle = ANGLE_MIN + span * position
        # Meme convention que le repere de paint_knob : le zero pointe vers le
        # HAUT puis tourne de angle, donc la direction vaut (sin, -cos). C'est
        # ce qui garantit que la pointe du repere de la molette arrive pile sur
        # le dernier trait allume -- sans quoi l'arc et l'aiguille se
        # contrediraient, et l'oeil croit l'aiguille.
        ux, uy = math.sin(angle), -math.cos(angle)
        bout = TICK_LEN_END if i in (0, TICK_COUNT - 1) else TICK_LEN
        outer = inner + radius * bout
        if position <= fraction:
            couleur = TICK_RED + (1.0,) if actif else TICK_RED_OFF + (0.88,)
        else:
            couleur = TICK_DARK + (0.92,)
        # Une ombre d'un demi-pixel avant l'encre, comme le lettrage de
        # paint_logo : sur du laiton brosse, un trait pose sans ombre flotte
        # au-dessus de la plaque. Elle est tracee pour TOUS les reperes, y
        # compris les sombres ou elle ne se voit pas -- de l'encre rouge et de
        # l'encre noire ont le meme relief, et un decalage n'aurait aucune
        # raison physique.
        for decalage, rgba in ((0.7, (0, 0, 0, 0.38)), (0.0, couleur)):
            cr.set_source_rgba(*rgba)
            cr.move_to(cx + ux * inner + decalage, cy + uy * inner + decalage)
            cr.line_to(cx + ux * outer + decalage, cy + uy * outer + decalage)
            cr.stroke()

    if maximum is not None:
        # Les bornes chiffrees. Elles ne s'eteignent PAS hors connexion, a
        # l'inverse du rouge : c'est de l'encre sur le laiton, la course reste
        # imprimee que l'enceinte reponde ou non -- meme regle que les reperes
        # sombres.
        cr.select_font_face("sans", cairo.FONT_SLANT_NORMAL,
                            cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(max(6.0, radius * TICK_LABEL_SIZE))
        rayon = radius * TICK_LABEL_RADIUS
        for angle, texte in ((ANGLE_MIN - TICK_LABEL_ANGLE, "0"),
                             (ANGLE_MAX + TICK_LABEL_ANGLE, str(int(maximum)))):
            ux, uy = math.sin(angle), -math.cos(angle)
            # On centre sur l'encre REELLE du texte et non sur son avance : un
            # "0" et un "31" n'ont ni la meme largeur ni le meme centre optique,
            # et deux bornes qui ne seraient pas symetriques par rapport a l'axe
            # de la molette se verraient immediatement.
            bx, by, bw, bh = cr.text_extents(texte)[:4]
            px = cx + ux * rayon - bw / 2.0 - bx
            py = cy + uy * rayon - bh / 2.0 - by
            for decalage, rgba in ((0.7, (0, 0, 0, 0.38)),
                                   (0.0, TICK_DARK + (0.96,))):
                cr.set_source_rgba(*rgba)
                cr.move_to(px + decalage, py + decalage)
                cr.show_text(texte)
    cr.restore()


# r28, remonte de 24. Le 24 datait du temps ou le border_width de la plaque lui
# mangeait 20 px de hauteur : la place manquait alors pour le libelle et la
# valeur. Depuis que le vide vient des marges des colonnes, elle est la, et un
# disque de 48 px n'occupait que 32 % d'une cellule de 148 -- trois petits plots
# sur une large plaque. A 56 px il en occupe 38 % et se lit comme une commande
# qu'on attrape. Il ne bouge PAS avec l'arrivee de la graduation : le dome garde
# sa taille, c'est la marge qui grandit pour loger l'arc.
KNOB_RADIUS = 28
# 15, monte de 6, et le chiffre est CALCULE et non choisi : l'arc va jusqu'a
# KNOB_RADIUS * TICK_EXTENT, soit 38,9 px, donc 10,9 px au-dela du dome ;
# l'anneau de focus se pose 2,2 px plus loin, sur 2 px de large, donc son bord
# exterieur tombe a 42,1. Il faut 43 px de demi-boite, soit 15 de marge. Cette
# marge n'est plus du vide decoratif : elle EST le logement de la graduation, ce
# que la marge des colonnes de BrassPanel doit savoir (cf. TICK_TOP_INSET).
KNOB_MARGIN = 15
# Vide reel entre le haut de la boite de la molette et la premiere encre de
# l'arc. Le repere du milieu (i = 5) pointe pile vers le haut, donc c'est lui qui
# borne : (KNOB_RADIUS + KNOB_MARGIN) - KNOB_RADIUS * TICK_EXTENT = 4,1 px.
# BrassPanel s'en sert pour que le laiton VISIBLE reste egal en haut et en bas ;
# sans ca, la marge de colonne compterait les 15 px de KNOB_MARGIN comme du vide
# alors que l'arc en occupe 10,9.
TICK_TOP_INSET = (KNOB_RADIUS + KNOB_MARGIN) - KNOB_RADIUS * TICK_EXTENT

# Un cran de roulette physique vaut 1.0 sur l'axe de defilement lisse. On
# accumule les fractions et on ne franchit un cran de valeur qu'a 1.0 atteint :
# un pave tactile envoie des dizaines de fractions par geste, et un cran de
# valeur par fraction enverrait le volume a la butee d'un seul effleurement.
SCROLL_NOTCH = 1.0
# Tolerance sur la comparaison, et elle n'est pas cosmetique : dix ajouts
# successifs de 0.1 -- un pave tactile qui envoie un cran en dix fractions --
# donnent 0.9999999999999999 en binaire, soit STRICTEMENT moins que 1.0. Le
# cran etait donc avale, puis la fraction suivante amenait le total a exactement
# 2.0 et en franchissait deux d'un coup. Mesure ici meme, pas une precaution
# theorique. Un milliardieme de cran d'avance est imperceptible ; perdre un cran
# puis en sauter deux ne l'est pas.
SCROLL_EPSILON = 1e-9


class Knob(Gtk.DrawingArea):
    """Une molette rotative. Enveloppe GTK autour d'un KnobModel.

    N'appelle JAMAIS le transport : elle emet value-changed, et le debounce de
    l'applet ecrit. C'est la regle d'architecture du projet.
    """

    __gsignals__ = {
        # l'entier seulement : la cle est portee par l'attribut .key, que
        # l'appelant a deja sous la main quand il connecte le signal
        "value-changed": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self, key, maximum, travel_px, value=0):
        super().__init__()
        self.key = key
        self._m = KnobModel(maximum=maximum, travel_px=travel_px, value=value)
        self._y_start = None
        self._scroll_accu = 0.0
        self._smooth_time = None
        self._discrete_time = None

        cote = (KNOB_RADIUS + KNOB_MARGIN) * 2
        self.set_size_request(cote, cote)
        self.set_can_focus(True)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                        | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.POINTER_MOTION_MASK
                        | Gdk.EventMask.SCROLL_MASK
                        | Gdk.EventMask.SMOOTH_SCROLL_MASK
                        | Gdk.EventMask.KEY_PRESS_MASK)
        self.connect("draw", self._on_draw)
        self.connect("button-press-event", self._on_press)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("button-release-event", self._on_release)
        self.connect("scroll-event", self._on_scroll)
        self.connect("key-press-event", self._on_key)

    @property
    def value(self):
        return self._m.value

    def _emit_if_changed(self, changed):
        if changed:
            self.queue_draw()
            self.emit("value-changed", self._m.value)
        return changed

    def step(self, delta):
        """Un cran. Rend True si la valeur a bouge.

        C'est ICI que le controle de sensibilite porte : step() est appelable
        depuis le code, ou rien ne filtre. Ceux des gestionnaires d'evenements
        sont en revanche des ceintures : GTK ne livre aucun evenement d'entree a
        un widget insensible (verifie -- aucun gestionnaire n'est meme atteint).
        Ils ne servent qu'aux evenements emis a la main sur le signal, qui
        court-circuitent la propagation de GTK.
        """
        if not self.get_sensitive():
            return False
        return self._emit_if_changed(self._m.step(delta))

    def set_value_silently(self, v):
        """Mise a jour venue de l'enceinte : ne doit PAS emettre, sinon on
        reflechirait vers le transport ce qu'il vient de nous dire."""
        if self._m.set_value(v):
            self.queue_draw()

    def set_maximum_silently(self, maximum):
        """Le maximum du volume vient du registre 0x08, connu seulement apres
        lecture de l'etat. La graduation change meme quand la valeur tient."""
        self._m.set_maximum(maximum)
        self.queue_draw()

    # Aucune surcharge pour repeindre au changement de sensibilite, et c'est
    # delibere : GTK 3 met deja la zone du widget a redessiner quand le drapeau
    # INSENSITIVE bouge (mesure : un seul dessin, identique avec et sans
    # queue_draw explicite). Surtout, ne pas croire qu'un do_set_sensitive
    # ferait l'affaire -- set_sensitive() n'est pas une vfunc en GTK 3, donc une
    # telle surcharge ne serait JAMAIS appelee et passerait pour du code utile.

    def _on_draw(self, _w, cr):
        alloc = self.get_allocation()
        rayon = max(6, min(alloc.width, alloc.height) / 2 - KNOB_MARGIN)
        actif = self.get_sensitive()
        # La graduation AVANT le dome : l'ombre portee de la molette doit tomber
        # sur le pied des reperes, cf. paint_knob_ticks.
        #
        # Le maximum vient du MODELE et n'est pas fige : set_maximum_silently le
        # remplace par celui que l'enceinte annonce, donc la borne imprimee au
        # bout de l'arc suit le materiel. C'est tout l'interet de le passer ici
        # plutot que de l'ecrire dans paint_knob_ticks.
        paint_knob_ticks(cr, alloc.width / 2, alloc.height / 2, rayon,
                         self._m.fraction, actif=actif,
                         maximum=self._m.maximum)
        paint_knob(cr, alloc.width / 2, alloc.height / 2, rayon,
                   self._m.fraction, actif=actif)
        if self.has_visible_focus():
            # Anneau pour le focus clavier seulement. Nuance mesuree : le
            # drapeau est porte par la FENETRE, pas par le geste. Il est faux
            # tant que le clavier n'a pas servi -- une session purement souris
            # n'a donc aucun cerclage -- puis vrai ensuite, y compris apres un
            # clic. C'est l'heuristique de GTK, la meme que ses widgets natifs.
            #
            # L'anneau se pose AU-DELA de l'arc, et non plus a rayon + 3 : les
            # reperes occupent maintenant cette bande, et un cercle qui les
            # traverse ne se lit ni comme un focus ni comme une graduation.
            cr.save()
            cr.set_source_rgba(*GOLD_PIPING, 0.9)
            cr.set_line_width(2)
            cr.arc(alloc.width / 2, alloc.height / 2, rayon * TICK_EXTENT + 2.2,
                   0, 2 * math.pi)
            cr.stroke()
            cr.restore()
        return False

    def _on_press(self, _w, ev):
        if not self.get_sensitive() or ev.button != 1:
            return False
        self.grab_focus()
        # Glisse RELATIF : on memorise le point d'attache, la valeur ne saute
        # pas la ou on a clique.
        self._y_start = ev.y_root
        self._m.begin_drag()
        return True

    def _on_motion(self, _w, ev):
        if self._y_start is None:
            return False
        # l'axe y de GTK descend : (depart - courant) est positif vers le haut.
        # On travaille en coordonnees ECRAN (y_root) et non widget : la course
        # utile est de 200 px pour une molette de 60, donc le pointeur sort
        # forcement du widget en cours de geste, et y deviendrait negatif ou
        # borne. La saisie implicite du bouton continue de nous livrer les
        # deplacements ; y_root reste juste.
        self._emit_if_changed(self._m.drag_to(self._y_start - ev.y_root))
        return True

    def _on_release(self, _w, _ev):
        self._y_start = None
        return True

    def _on_scroll(self, _w, ev):
        """Un cran de molette vaut EXACTEMENT un cran de valeur. C'est ce qui
        rend la precision non negociable, quel que soit le nombre de crans.

        Deux chemins s'excluent, et il faut les deux : get_scroll_direction()
        rend son drapeau a False sur un evenement lisse -- en rendant quand
        meme GDK_SCROLL_UP, valeur par defaut trompeuse qu'on ne doit donc
        jamais lire sans le drapeau -- et get_scroll_deltas() rend False sur un
        evenement a crans.

        D'ou la garde d'horodatage. Une souris moderne fait defiler par
        valuateur (releve sur celle de cette machine : XIScrollClass, increment
        120 sur l'axe vertical), donc un cran physique arrive en lisse a
        dy = 1.0, et le serveur X emule EN PLUS un appui de bouton 4/5. Si les
        deux nous parvenaient, un cran physique en vaudrait deux -- la promesse
        precisement a ne pas casser. Les deux evenements portent l'horodatage
        de la meme cause physique : on ecarte donc le jumeau d'un evenement de
        l'autre nature deja compte. Jamais entre evenements de meme nature, car
        un pave tactile en envoie legitimement plusieurs par milliseconde. La
        garde ne peut donc pas avaler un evenement isole : si GDK ne double
        pas, elle ne se voit pas.
        """
        if not self.get_sensitive():
            return False
        if ev.is_stop:
            # fin de geste inertiel : la fraction restante n'ira nulle part
            self._scroll_accu = 0.0
            return True

        ok, direction = ev.get_scroll_direction()
        if ok:
            if ev.time and ev.time == self._smooth_time:
                return True         # jumeau emule d'un lisse deja compte
            self._discrete_time = ev.time
            # roulette a crans : le systeme a deja quantifie, rien a accumuler
            self._scroll_accu = 0.0
            if direction == Gdk.ScrollDirection.UP:
                self.step(1)
            elif direction == Gdk.ScrollDirection.DOWN:
                self.step(-1)
            else:
                return False        # horizontal : ce n'est pas notre axe
            return True

        # defilement lisse (pave tactile, roulette haute resolution) :
        # dy < 0 = vers le haut
        _got, _dx, dy = ev.get_scroll_deltas()
        if dy == 0:
            return False
        if ev.time and ev.time == self._discrete_time:
            return True             # jumeau lisse d'un cran deja compte
        self._smooth_time = ev.time
        self._scroll_accu -= dy
        seuil = SCROLL_NOTCH - SCROLL_EPSILON
        while self._scroll_accu >= seuil:
            self._scroll_accu -= SCROLL_NOTCH
            self.step(1)
        while self._scroll_accu <= -seuil:
            self._scroll_accu += SCROLL_NOTCH
            self.step(-1)
        return True

    def _on_key(self, _w, ev):
        if not self.get_sensitive():
            return False
        touche = ev.keyval
        if touche in (Gdk.KEY_Up, Gdk.KEY_Right):
            self.step(1)
            return True
        if touche in (Gdk.KEY_Down, Gdk.KEY_Left):
            self.step(-1)
            return True
        if touche == Gdk.KEY_Home:
            self._emit_if_changed(self._m.set_value(0))
            return True
        if touche == Gdk.KEY_End:
            self._emit_if_changed(self._m.set_value(self._m.maximum))
            return True
        return False


# -- le levier de mise en service -----------------------------------------
# La commande POWER d'une Acton III n'est pas un interrupteur logiciel : c'est un
# petit levier dore MOLETE qui traverse une fente sombre percee dans le laiton,
# en haut pour allumer. C'est ce qu'on dessine ici, a la place du Gtk.Switch qui
# arrivait dans la couleur d'accent de la session -- un violet Yaru -- et
# restait, meme repeint en laiton par la feuille de style, la seule piece de la
# fenetre a ne pas appartenir a l'objet.
#
# 32 x 44, monte de 28 x 34. Le 34 n'etait pas un choix libre : c'etait la hauteur
# du bouton "Quitter", que le levier partageait au pied de la fenetre, et un pixel
# de plus aurait agrandi la bande pour lui seul. Cette contrainte a DISPARU avec le
# demenagement : le levier vit maintenant dans une boite de calage de 86 px sur la
# plaque, la hauteur d'une molette, et il ne paye plus rien. On lui rend donc de la
# course, puisque ce qui se lit a l'oeil est la POSITION du dome clair. Compare a
# 1:1 sur laiton contre 34, 52 et 56 : a 34 le levier est timide a cote d'une
# molette de 56 px de diametre, a 52 il s'allonge en tige. A 44 il fait les trois
# quarts du diametre de la molette, le rapport qu'a la commande POWER reelle.
TOGGLE_WIDTH = 32
TOGGLE_HEIGHT = 44
# Vide reserve au bord de la boite pour l'anneau de focus, qui se pose donc au bord
# de l'allocation sans mordre sur la fente.
TOGGLE_INSET = 2

# Rampe du dome du levier : celle de paint_knob, reduite a ses trois bornes. Un
# dome de 8 px de diametre ne peut pas resoudre cinq arrets -- les deux
# intermediaires tombent a moins d'un pixel l'un de l'autre -- mais c'est bien la
# meme famille de laiton, et c'est ce qui fait que le levier et les molettes se
# lisent comme le meme metal.
TOGGLE_DOME = ((0.00, (0.980, 0.949, 0.831)),
               (0.46, (0.776, 0.635, 0.259)),
               (1.00, (0.298, 0.239, 0.110)))
# Rampe TRANSVERSALE du fut, sombre aux deux bords et claire un peu a gauche du
# milieu : c'est elle, et pas le contour, qui fait lire un cylindre. Le pic est a
# 0.38 et non a 0.50 parce que toute la facade est eclairee du haut a gauche, cf.
# le point chaud du dome de paint_knob.
TOGGLE_SHAFT = ((0.00, (0.298, 0.239, 0.110)),
                (0.20, (0.741, 0.604, 0.243)),
                (0.38, (0.980, 0.949, 0.831)),
                (0.68, (0.757, 0.616, 0.251)),
                (1.00, (0.325, 0.263, 0.125)))


def paint_toggle(cr, x, y, w, h, on, actif=True):
    """Le levier dore dans sa fente, dans une boite w x h. HAUT = en service.

    Ce qui fait qu'il TRAVERSE la plaque au lieu d'y etre pose, et c'est tout
    l'enjeu du dessin :

    - la fente est peinte AVANT le levier et porte une ombre interne sur tout
      son pourtour, donc elle se lit comme un trou et non comme un trait ;
    - le pied du levier est NOYE dans cette ombre, par un degrade ecrete a la
      fente qui plonge du cote oppose a l'inclinaison. La moitie vide de la
      fente reste donc noire, et le fut s'y enfonce progressivement ;
    - le dome, lui, porte une ombre PORTEE sur le laiton, parce qu'il depasse la
      fente et surplombe la plaque.

    Sans le premier point on obtient un bonbon dore pose sur une rayure ; sans le
    troisieme, un decalque.

    PLUS DE PLATINE sous le levier depuis qu'il est monte sur la plaque. Elle
    peignait ici le meme laiton que BrassPanel, et c'etait juste TANT QUE le
    levier vivait au pied, sur le tolex : elle s'y lisait comme l'ecusson d'un
    interrupteur visse au dos d'un ampli. Sur la plaque elle n'est plus qu'un
    rectangle de laiton biseaute POSE sur du laiton -- un autocollant, verifie a
    1:1 et en x4 -- alors que le panneau reel perce sa fente directement dans la
    plaque. La fonction ne peint donc plus que la fente et le levier ; ce qui est
    dessous appartient a l'appelant.
    """
    cr.save()
    plate_x, plate_y = x + TOGGLE_INSET, y + TOGGLE_INSET
    plate_w = max(6.0, w - 2 * TOGGLE_INSET)
    plate_h = max(8.0, h - 2 * TOGGLE_INSET)
    # Tout ce qui suit est ECRETE a cette boite. Ce n'est pas une precaution
    # theorique : a la butee le dome arrive a 1,3 px du bord, et son ombre
    # portee -- un degrade de rayon 1,7 fois le dome -- baverait donc au-dela de
    # l'allocation du widget, sur le laiton nu de la plaque et sous l'anneau de
    # focus.
    _rounded_path(cr, plate_x, plate_y, plate_w, plate_h, 3)
    cr.clip()

    slot_w = max(4.0, plate_w * 0.40)
    slot_h = max(8.0, plate_h * 0.62)
    sx = plate_x + (plate_w - slot_w) / 2.0
    sy = plate_y + (plate_h - slot_h) / 2.0
    rayon_fente = slot_w / 2.0
    cx = plate_x + plate_w / 2.0
    pivot = plate_y + plate_h / 2.0

    creux = cairo.LinearGradient(0, sy, 0, sy + slot_h)
    creux.add_color_stop_rgb(0.0, 0.024, 0.024, 0.024)
    creux.add_color_stop_rgb(1.0, 0.118, 0.106, 0.078)
    _rounded_path(cr, sx, sy, slot_w, slot_h, rayon_fente)
    cr.set_source(creux)
    cr.fill()

    # Ombre interne : le contour trace ECRETE a la fente, donc seule sa moitie
    # interieure se voit. Meme procede que l'ombre du caisson dans paint_grille,
    # et pour la meme raison -- ca donne un degrade vers le bord sans avoir a
    # construire un flou.
    cr.save()
    _rounded_path(cr, sx, sy, slot_w, slot_h, rayon_fente)
    cr.clip()
    _rounded_path(cr, sx, sy, slot_w, slot_h, rayon_fente)
    cr.set_source_rgba(0, 0, 0, 0.72)
    cr.set_line_width(2.6)
    cr.stroke()
    cr.restore()

    # Arete BASSE de la fente eclairee, et elle seule : dans un creux eclaire par
    # le haut, c'est la paroi du fond qui recoit la lumiere. Un filet clair pose
    # tout autour ferait un anneau, donc un objet POSE sur la plaque.
    cr.save()
    cr.set_line_width(1.0)
    cr.set_source_rgba(1, 0.965, 0.878, 0.30)
    cr.arc(cx, sy + slot_h - rayon_fente, rayon_fente + 0.5,
           math.radians(20), math.radians(160))
    cr.stroke()
    cr.restore()

    sens = -1.0 if on else 1.0
    # Proportions reglees sur planche a 1:1 puis en x5, cf. les essais : un fut a
    # 0.74 de la fente laisse trop de noir de chaque cote et le levier flotte
    # dedans, un fut a 0.90 la bouche et la fente cesse de se lire comme un trou.
    shaft_w = slot_w * 0.82
    longueur = slot_h * 0.44
    # Dome nettement plus large que le fut : c'est la forme d'une poignee de
    # manette, et a cette taille c'est aussi le seul detail qui porte l'etat. A
    # 0.62 il ne se distingue plus du fut, a 0.76 il tourne au champignon.
    dome_r = shaft_w * 0.70
    bout = pivot + sens * longueur
    # Le pied repart LEGEREMENT du cote oppose, sinon le fut s'arreterait pile au
    # pivot et on verrait sa section au milieu de la fente. Juste 0.06 : au-dela,
    # le levier empiete sur la moitie vide de la fente, et c'est cette moitie
    # noire qui dit de quel cote il est bascule.
    pied = pivot - sens * slot_h * 0.06

    # Ombre portee du levier sur le laiton, avant lui. Degradee et non pleine,
    # meme raison que l'ombre de paint_knob : un aplat decale ne donne qu'un
    # croissant a bord net, qui se lit comme un defaut de trace.
    ombre = cairo.RadialGradient(cx + 1.0, bout + 1.2, dome_r * 0.5,
                                 cx + 1.0, bout + 1.2, dome_r * 1.7)
    ombre.add_color_stop_rgba(0, 0, 0, 0, 0.55)
    ombre.add_color_stop_rgba(1, 0, 0, 0, 0.0)
    cr.set_source(ombre)
    cr.arc(cx + 1.0, bout + 1.2, dome_r * 1.7, 0, 2 * math.pi)
    cr.fill()

    fut = cairo.LinearGradient(cx - shaft_w / 2.0, 0, cx + shaft_w / 2.0, 0)
    for pos, rgb in TOGGLE_SHAFT:
        fut.add_color_stop_rgb(pos, *rgb)
    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_width(shaft_w)
    cr.set_source(fut)
    cr.move_to(cx, pied)
    cr.line_to(cx, bout)
    cr.stroke()

    # Moletage : des rainures PERPENDICULAIRES au fut, par paires sombre puis
    # claire. Une seule serie sombre donne des traits graves, pas un moletage --
    # il faut le creux ET la crete, exactement comme le brossage de paint_brass.
    # Trois rainures : sur les 9,3 px de fut visible il n'y a pas la place d'en
    # poser plus sans que les paires se touchent, et deux paires collees ne font
    # plus qu'une bande grise.
    cr.set_line_cap(cairo.LINE_CAP_BUTT)
    cr.set_line_width(0.9)
    demi = shaft_w * 0.40
    for i in range(3):
        yy = pied + (bout - pied) * (0.28 + 0.22 * i)
        for decalage, rgba in ((0.0, (0, 0, 0, 0.42)),
                               (0.9 * sens, (1, 0.976, 0.898, 0.34))):
            cr.set_source_rgba(*rgba)
            cr.move_to(cx - demi, yy + decalage)
            cr.line_to(cx + demi, yy + decalage)
            cr.stroke()
    cr.restore()

    dome = cairo.RadialGradient(cx - dome_r * 0.34, bout - dome_r * 0.40,
                                max(0.4, dome_r * 0.12), cx, bout, dome_r)
    for pos, rgb in TOGGLE_DOME:
        dome.add_color_stop_rgb(pos, *rgb)
    cr.set_source(dome)
    cr.arc(cx, bout, dome_r, 0, 2 * math.pi)
    cr.fill()

    # Le pied noye dans la fente. Le degrade est oppose a l'inclinaison : la
    # moitie vide de la fente reste noire, et le fut s'y enfonce.
    cr.save()
    _rounded_path(cr, sx, sy, slot_w, slot_h, rayon_fente)
    cr.clip()
    noyage = cairo.LinearGradient(0, sy, 0, sy + slot_h)
    haut, bas = (0.0, 0.78) if on else (0.78, 0.0)
    noyage.add_color_stop_rgba(0.0, 0, 0, 0, haut)
    noyage.add_color_stop_rgba(1.0, 0, 0, 0, bas)
    cr.set_source(noyage)
    cr.paint()
    cr.restore()

    if not actif:
        # Meme voile que paint_knob, et pour la meme raison : le levier reste
        # lisible mais visiblement hors service. L'ecretage a la platine est
        # deja pose plus haut, donc un simple paint() suffit.
        cr.set_source_rgba(0.14, 0.145, 0.165, 0.56)
        cr.paint()
    cr.restore()


class Toggle(Gtk.DrawingArea):
    """Le levier a deux positions. Meme contrat que Knob : il n'appelle jamais
    rien, il emet, et c'est l'applet qui ecrit.

    La distinction set_on_silently / toggle est la meme que
    set_value_silently / step, et pour la meme raison concrete : Facade.update
    passe ici jusqu'a huit fois par seconde tant que la molette PHYSIQUE de
    l'enceinte tourne, et chaque emission de "toggled" ferait reecrire le fichier
    d'autostart.
    """

    __gsignals__ = {
        "toggled": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }

    def __init__(self, on=False):
        super().__init__()
        self._on = bool(on)
        self.set_size_request(TOGGLE_WIDTH, TOGGLE_HEIGHT)
        self.set_can_focus(True)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                        | Gdk.EventMask.KEY_PRESS_MASK)
        self.connect("draw", self._on_draw)
        self.connect("button-press-event", self._on_press)
        self.connect("key-press-event", self._on_key)

    @property
    def on(self):
        return self._on

    def toggle(self):
        """Bascule sur GESTE de l'utilisateur, et emet. Rend True si ca a bascule.

        Le controle de sensibilite porte ICI et non seulement dans les
        gestionnaires d'evenements, exactement comme Knob.step : GTK ne livre
        aucun evenement d'entree a un widget insensible (verifie), donc les gardes
        des gestionnaires ne servent qu'aux evenements emis a la main. C'est cette
        methode-ci qui est appelable depuis du code, ou rien ne filtre.
        """
        if not self.get_sensitive():
            return False
        self._on = not self._on
        self.queue_draw()
        self.emit("toggled", self._on)
        return True

    def set_on_silently(self, on):
        """Reflet de l'etat lu ailleurs : ne doit PAS emettre. Emettre ici
        ferait reecrire le fichier d'autostart a chaque rafraichissement."""
        on = bool(on)
        if on != self._on:
            self._on = on
            self.queue_draw()

    def _on_draw(self, _w, cr):
        alloc = self.get_allocation()
        paint_toggle(cr, 0, 0, alloc.width, alloc.height, self._on,
                     actif=self.get_sensitive())
        if self.has_visible_focus():
            # Meme anneau que Knob, en rectangle arrondi puisque la piece l'est :
            # il se pose dans les TOGGLE_INSET px que paint_toggle laisse libres
            # autour de la platine.
            cr.save()
            cr.set_source_rgba(*GOLD_PIPING, 0.9)
            cr.set_line_width(2)
            _rounded_path(cr, 1, 1, alloc.width - 2, alloc.height - 2, 4)
            cr.stroke()
            cr.restore()
        return False

    def _on_press(self, _w, ev):
        """N'importe ou sur la piece : la platine entiere est la commande.
        Viser une fente de 10 px de large a la souris serait une punition."""
        if not self.get_sensitive() or ev.button != 1:
            return False
        # grab_focus AVANT la bascule, et sous la garde de sensibilite : donner le
        # focus a un widget insensible le retirerait au precedent sans le rendre.
        self.grab_focus()
        self.toggle()
        return True

    def _on_key(self, _w, ev):
        # space et Return, les deux touches dont GTK fait des activations sur ses
        # propres boutons. Pas de fleches : ce n'est pas un axe, c'est un etat.
        #
        # La garde de sensibilite rend False plutot que d'avaler la touche : un
        # widget hors service ne doit pas retenir l'espace, que la fenetre peut
        # vouloir router ailleurs.
        if not self.get_sensitive():
            return False
        if ev.keyval in (Gdk.KEY_space, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.toggle()
            return True
        return False


# -- assemblage -----------------------------------------------------------
# Taille par defaut de la fenetre. Ce n'est PAS un cadrage : la facade exige
# 461x423 avec la police du theme de cette machine (mesure hors ecran sur une
# Gtk.OffscreenWindow), et une fenetre non redimensionnable plus petite que son
# minimum grandit d'elle-meme. Ces valeurs laissent donc juste un peu de mou, que
# la Grille absorbe puisqu'elle est le seul enfant extensible. La largeur est
# dictee par la rangee des presets plus le bouton d'etat ; la hauteur par la
# somme des quatre bandes.
#
# 530 x 401, contre 470 x 425 avant ces trois changements. La fenetre s'est donc
# ELARGIE de 60 px et RACCOURCIE de 24, et les deux mouvements viennent de la meme
# decision : le pied a fusionne avec la rangee des presets (cf. Facade.__init__),
# ce qui rend une bande de 46 px et reclame 47 px de large.
#
# Le minimum mesure est 521 x 399, et la largeur n'est plus dictee par la meme
# piece qu'avant : la rangee fusionnee demande 497, la plaque 450. Detail mesure a
# 530 x 401 : bande de la plaque 153, toile 166, rangee 34.
WINDOW_WIDTH = 530
WINDOW_HEIGHT = 401
MARGIN = 12
# Laiton visible entre le bord de la plaque et le bloc molette + libelle +
# valeur. Descendu de 16 a 12 en meme temps que la graduation arrivait : l'arc
# apporte deja de l'encre tout autour de la molette, donc 16 px de laiton NU
# par-dessus faisaient une plaque enflee -- 151 px de haut pour 140 de toile,
# soit exactement l'inversion que la Grille cherche a eviter. A 12 la plaque
# retombe a 143 et la toile la domine de nouveau.
PLATE_PADDING = 12

# -- la colonne du levier, au bout droit de la plaque ----------------------
# Le levier de mise en service vit maintenant SUR la plaque, comme la commande
# POWER du panneau reel, et non plus au pied de la fenetre. Deux chiffres
# gouvernent son logement, et tous deux sont contraints par la CAPSULE : plus on
# descend vers le bas de la plaque, plus le bord se rapproche, et le libelle
# grave est a la fois la piece la plus large de la colonne et la plus basse.
#
# 16 px de marge de bout, et le calcul : le libelle occupe la bande de 25,5 a
# 39,5 px sous l'axe de la plaque, et a 39,5 px de l'axe le bord de la capsule
# rentre de 71,5 - sqrt(71,5^2 - 39,5^2) = 11,9 px. Releve sur les allocations
# reelles, l'encre du libelle -- 49 px centres dans les 88 de la colonne --
# s'arrete a 30,5 px du bord de la plaque, soit 18,6 px de vide franc devant
# l'arc. Le lettrage ne vient donc pas mourir sur le trait, et la marge tient meme
# si la police du theme grossit d'un tiers.
SESSION_INSET = 16
# 88 px de large, et la cale de gauche fait la meme largeur MARGE COMPRISE pour
# que le trio de molettes reste centre sur la plaque. C'est la largeur du libelle
# SESSION (49 px avec la police du theme, mesure) plus de quoi le centrer -- pas
# la largeur du levier, qui ne fait que 28 : la colonne est dimensionnee par son
# ECRITURE, comme les colonnes de molettes le sont par leur arc et non par leur
# dome.
SESSION_COLUMN = 88

# Les trois registres pilotables, avec leur course de glisse. L'ordre est celui
# du panneau de commandes de l'enceinte.
REGISTERS = (("volume", TRAVEL_VOLUME_PX),
             ("bass", TRAVEL_EQ_PX),
             ("treble", TRAVEL_EQ_PX))

# Les deux libelles du bouton d'etat. Nommes parce que la largeur du bouton est
# figee sur le plus long, ce qui les rend solidaires : voir Facade.__init__.
#
# Poses en balisage Pango, et pas en texte simple, pour une seule raison : le
# point du voyant connecte doit etre vert quand le mot reste chaud et sourd, et
# GTK ne sait pas colorer un caractere isole en CSS. Le span ne porte donc QUE le
# point ; le texte n'en a pas et herite de la couleur de la feuille de style, qui
# reste ainsi le seul endroit ou vivent les teintes des deux etats.
STATUS_CONNECTED = '<span foreground="#6dc46a">●</span> connectée'
STATUS_DISCONNECTED = "○ déconnectée — reconnecter"


# Le pied reste en widgets GTK ordinaires : inutile de peindre des boutons a la
# main, GTK gere deja le survol, le focus et le clavier.
CSS = b"""
.marshall-facade { background-color: transparent; }
.marshall-cap {
  font-size: 8pt; font-weight: 600; letter-spacing: 1px;
  color: #3a2c06;
}
/* Plus de regle .marshall-footer : il n'y a plus de pied. Les quatre libelles
   .marshall-cap sont TOUS graves sur le laiton -- VOLUME, BASS, TREBLE et
   SESSION -- donc un seul brun sombre suffit, la ou il fallait avant un gris
   pour celui qui vivait sur le tolex. */
.marshall-val { font-size: 10pt; font-weight: 700; color: #2c2105; }
.marshall-preset, .marshall-quit {
  font-size: 8pt; font-weight: 600; padding: 4px 9px;
  color: #c8b47a; background-image: none; background-color: rgba(201,162,39,0.07);
  border: 1px solid rgba(201,162,39,0.35); border-radius: 3px; text-shadow: none;
}
.marshall-preset:hover, .marshall-quit:hover {
  background-color: rgba(201,162,39,0.18);
}
.marshall-preset-actif {
  color: #241b02;
  background-image: linear-gradient(to bottom, #e8ca63, #bf9a1f);
  border-color: #8d6f1b;
}
/* Sans cette regle un preset mort est INDISCERNABLE d'un preset vivant : la
   couleur ci-dessus s'applique aussi a l'etat :disabled, et notre provider
   passe devant la regle de grisement du theme (mesure : #c8b47a a l'identique,
   sensible ou non). Hors connexion, un clic ne fait rien -- ca doit se voir. */
.marshall-preset:disabled {
  color: rgba(200,180,122,0.32);
  background-color: transparent;
  border-color: rgba(201,162,39,0.13);
}
/* Le voyant etait la seule note froide de la fenetre : #8d8d92 est un gris a
   R-B = -5, et au milieu d'un decor entierement chaud un gris froid se lit comme
   du bleu (mesure sur les pixels du libelle : 141,141,146). Deux etats separes,
   parce que l'un informe et l'autre propose d'agir -- d'ou l'or pale hors
   connexion, la meme famille que le contour des presets, et un survol dans les
   deux etats pour que le bouton se donne comme cliquable. L'ordre des regles
   fait la cascade : .marshall-etat-off passe apres .marshall-etat:hover, a
   specificite egale. */
.marshall-etat { font-size: 8pt; color: #9a9384; }
.marshall-etat:hover {
  color: #c6bda7; background-image: none;
  background-color: rgba(201,162,39,0.10);
}
.marshall-etat.marshall-etat-off { color: #c9b47c; }
.marshall-etat.marshall-etat-off:hover {
  color: #ecd79c; background-image: none;
  background-color: rgba(201,162,39,0.20);
}
/* Aucune regle pour `switch` : le pied ne contient plus de Gtk.Switch, mais un
   Toggle dessine en Cairo, cf. paint_toggle. Ces trois regles repeignaient en
   laiton l'interrupteur du theme, qui arrivait sinon dans la couleur d'accent
   de la session -- un violet Yaru. Le probleme est resolu a la racine : la
   piece n'appartient plus au theme. */
"""

_css_installed = False


def _cached_background(widget, painter):
    """Rend la surface de fond de `widget`, construite au besoin puis gardee.

    POURQUOI UN CACHE : la facade et la plaque sont les PARENTS des molettes,
    donc un glisse les fait redessiner a chaque trame -- mesure a la souris
    reelle, 31 dessins de la facade pour un glisse de 200 px en 954 ms, GTK ne
    saute PAS les ancetres. Cairo decoupe le RENDU, pas la construction des
    chemins, donc sans cache paint_tolex se rejouerait en entier a chaque
    trame : 4,7 ms mesurees en 470x400, sur les 16,7 ms d'une trame a 60 Hz.

    La Grille ne se redessine PAS pendant un glisse (0 fois sur ces 31 trames)
    mais passe par ici quand meme : depuis que sa toile est reellement tissee
    elle coute 16,9 ms dans le champ reel, soit une trame pleine a chaque simple
    reaffichage de la fenetre.

    A NE JAMAIS FAIRE, et c'est le piege qui a coute cette fonction : poser un
    border_width sur un conteneur qui peint son fond. GtkContainer retire le
    border_width de l'allocation dans son adjust_size_allocation, et l'ecretage
    du draw suit cette allocation reduite. Le vide se retrouve donc DEHORS :
    plaque annoncee 125 px de haut et peinte 97, molettes mordant sur ses deux
    bords, et un lisere trace a 1,5 px du bord du caisson tout simplement
    ecrete, donc invisible. Le vide interne doit venir des marges des enfants
    (BrassPanel) ou d'une boite interne (Facade).
    """
    alloc = widget.get_allocation()
    taille = (alloc.width, alloc.height)
    if widget._background_size != taille:
        widget._background = cairo.ImageSurface(cairo.FORMAT_ARGB32, *taille)
        painter(cairo.Context(widget._background), *taille)
        widget._background_size = taille
    return widget._background


def install_css():
    """Idempotent : deux appels ne doivent pas empiler deux providers, sinon
    les regles seraient evaluees deux fois."""
    global _css_installed
    if _css_installed:
        return
    screen = Gdk.Screen.get_default()
    if screen is None:
        # Sans afficheur il n'y a rien a styler, et surtout
        # add_provider_for_screen(None, ...) ne leve pas : il declenche un
        # Gtk-ERROR, qui AVORTE le processus (verifie -- SIGTRAP). Rendre la
        # main garde ce module importable et appelable sans ecran, ce qui est
        # exactement la promesse de son en-tete. Le drapeau reste a False, donc
        # un appel ulterieur avec un ecran installe bien la feuille.
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        screen, provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_installed = True


class BrassPanel(Gtk.Box):
    """La plaque de laiton : peint son fond, porte les trois molettes, et le
    levier de mise en service a son bout droit."""

    def __init__(self, maximums):
        # PLUS homogeneous : la colonne du levier n'a aucune raison de reclamer
        # la largeur d'une colonne de molette -- 110 px pour un levier de 28. Les
        # trois molettes gardent leur espacement egal parce qu'elles vivent
        # maintenant dans une boite interne, elle homogene, qui prend toute la
        # place laissee par le levier et par la cale qui lui repond.
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        # AUCUN border_width ici : il rognerait la plaque peinte au lieu de
        # l'encadrer, cf. _cached_background. Le laiton autour du bloc de
        # commandes vient des marges des colonnes.
        self._background = None          # cf. _on_draw
        self._background_size = None
        self.knobs = {}
        trio = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                       homogeneous=True)
        for key, travel in REGISTERS:
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            # Haut et bas dissymetriques a dessein : la colonne commence par une
            # Knob, qui porte deja du vide interne au-dessus de son encre, et
            # finit par une etiquette qui s'arrete pile a son texte. A marge
            # egale la plaque montrerait donc plus de laiton au-dessus de la
            # molette que sous la valeur, et la valeur aurait l'air de tomber du
            # bord. On vise du laiton VISIBLE egal des deux cotes.
            #
            # C'est TICK_TOP_INSET et non KNOB_MARGIN qu'on defalque depuis que
            # la graduation existe : la marge de la molette n'est plus vide, l'arc
            # en occupe 10,9 px sur 15, et il ne reste que 4,1 px de laiton nu
            # au-dessus du repere du haut. Defalquer les 15 entiers ferait
            # remonter la plaque de 11 px sur la graduation.
            # RECESS_SPREAD en plus des deux cotes : la plaque n'occupe plus
            # toute l'allocation de la bande, elle laisse cette marge au creux,
            # cf. _paint_background. Sans ca le laiton visible se reduirait
            # d'autant et les molettes toucheraient l'arete de la capsule.
            column.set_margin_top(round(PLATE_PADDING - TICK_TOP_INSET)
                                  + RECESS_SPREAD)
            column.set_margin_bottom(PLATE_PADDING + RECESS_SPREAD)
            knob = Knob(key, maximum=maximums[key], travel_px=travel)
            caption = Gtk.Label(label=key.upper())
            caption.get_style_context().add_class("marshall-cap")
            value = Gtk.Label(label="0")
            value.get_style_context().add_class("marshall-val")
            column.pack_start(knob, False, False, 0)
            column.pack_start(caption, False, False, 0)
            column.pack_start(value, False, False, 0)
            trio.add(column)
            self.knobs[key] = knob
            knob._value_label = value

        # La cale de GAUCHE, de la largeur exacte de la colonne du levier. Sans
        # elle le trio se decalerait de SESSION_COLUMN / 2 vers la gauche, et sur
        # une capsule ca se voit tout de suite : les deux bouts demi-circulaires
        # sont le repere de symetrie de la plaque. Le panneau reel centre lui aussi
        # ses trois molettes, avec l'AUX a un bout et le POWER a l'autre.
        # SESSION_COLUMN + SESSION_INSET et non SESSION_COLUMN : en GTK 3 une marge
        # s'ajoute DEHORS de la taille demandee, donc la colonne du levier occupe
        # bien la somme des deux. Mesure avec la cale a 88 seulement : molettes
        # centrees en 152,5 / 257,5 / 362, soit un axe du trio a 257,2 pour une
        # plaque centree en 265 -- les 8 px de biais se voyaient sur la capsule.
        cale = Gtk.Box()
        cale.set_size_request(SESSION_COLUMN + SESSION_INSET, -1)
        self.pack_start(cale, False, False, 0)
        self.pack_start(trio, True, True, 0)
        self.pack_end(self._session_column(), False, False, 0)
        self.connect("draw", self._on_draw)

    def _session_column(self):
        """La quatrieme colonne : le levier au bout droit de la plaque, et son
        libelle grave dessous.

        SESSION et non POWER, et ce n'est pas de la pudeur : le panneau reel grave
        POWER parce que ce levier-la coupe l'alimentation de l'enceinte. Celui-ci
        ne coupe rien du tout, il decide si l'applet se lance avec la session. Lui
        ecrire POWER serait un decor qui mentirait sur la fonction ; l'infobulle
        porte la phrase entiere, a un survol."""
        self.autostart = Toggle()
        self.autostart.set_tooltip_text("Démarrer avec la session")
        self.autostart.set_halign(Gtk.Align.CENTER)
        self.autostart.set_valign(Gtk.Align.CENTER)
        # Boite de calage a la hauteur EXACTE d'une molette, et c'est ce qui fait
        # tenir les deux alignements a la fois : le levier, centre dedans, tombe
        # sur l'axe des molettes, et SESSION tombe sur la meme ligne que VOLUME,
        # BASS et TREBLE. Les quatre libelles se lisent alors comme une seule
        # ligne gravee, ce qu'ils sont sur le panneau reel.
        calage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        calage.set_size_request(-1, (KNOB_RADIUS + KNOB_MARGIN) * 2)
        calage.pack_start(self.autostart, True, False, 0)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        column.set_size_request(SESSION_COLUMN, -1)
        column.set_margin_top(round(PLATE_PADDING - TICK_TOP_INSET)
                              + RECESS_SPREAD)
        column.set_margin_bottom(PLATE_PADDING + RECESS_SPREAD)
        # Marge de bout : le libelle est la piece la plus large de la colonne et
        # la plus basse, donc c'est lui qui touche le premier l'arc de la capsule.
        # Cf. SESSION_INSET, le chiffre est calcule.
        column.set_margin_end(SESSION_INSET)
        caption = Gtk.Label(label="SESSION")
        caption.get_style_context().add_class("marshall-cap")
        caption.set_tooltip_text("Démarrer avec la session")
        column.pack_start(calage, False, False, 0)
        column.pack_start(caption, False, False, 0)
        return column

    def _paint_background(self, cr, w, h):
        """La plaque est une CAPSULE encastree, et elle n'occupe pas toute la
        bande : elle laisse RECESS_SPREAD px de tolex tout autour, ou se peint le
        creux. Le fond de la bande reste donc partiellement TRANSPARENT, et c'est
        le tolex de la facade -- deja peint dessous -- qui se voit a travers.

        Rayon = moitie de la hauteur, donc des bouts exactement demi-circulaires.
        Ce n'est pas un arrondi decoratif : la plaque reelle est une capsule.
        Verifie et non suppose, _rounded_path tient ce rayon-la sans tordre ses
        arcs -- releve du premier pixel peint a 30, 60 et 71 px du centre : 6, 33
        et 65, contre 6,6, 32,6 et 63,1 pour la capsule theorique.
        """
        x = y = RECESS_SPREAD
        plate_w = max(2.0, w - 2 * RECESS_SPREAD)
        plate_h = max(2.0, h - 2 * RECESS_SPREAD)
        paint_recess(cr, x, y, plate_w, plate_h, plate_h / 2.0)
        paint_brass(cr, x, y, plate_w, plate_h, radius=plate_h / 2.0)

    def _on_draw(self, _w, cr):
        cr.set_source_surface(
            _cached_background(self, self._paint_background), 0, 0)
        cr.paint()
        return False        # les enfants se dessinent par-dessus

    def set_display(self, key, value):
        self.knobs[key]._value_label.set_text(str(value))


class Grille(Gtk.DrawingArea):
    """La toile tissee et le logo. Prend la place restante."""

    def __init__(self):
        super().__init__()
        self._background = None
        self._background_size = None
        # 152 et non 96 : sur une facade d'ampli la toile DOMINE, le panneau de
        # commandes n'est qu'un bandeau. A 96 la plaque (123 px) etait plus haute
        # que la toile et l'ensemble se lisait comme une barre d'outils posee sur
        # une bande decorative.
        #
        # Monte de 140 a 152 avec l'arrivee de la graduation, puis de 152 a 164
        # avec le creux de la plaque. A 140 la domination n'etait plus qu'un
        # accident de la taille par defaut : la toile ne depassait la plaque que
        # grace au mou de WINDOW_HEIGHT, et une police de theme plus grande -- qui
        # gonfle le pied et la rangee des presets, sans que la fenetre puisse
        # s'agrandir -- aurait ramene la toile a son minimum, donc SOUS la plaque.
        #
        # 164 est calcule sur la bande de la plaque, pas choisi : la plaque fait
        # 143 px et le creux lui ajoute 2 x RECESS_SPREAD, donc 153. A 152 la
        # toile serait passee SOUS la plaque des le minimum, et l'inversion doit
        # rester structurellement impossible quoi que fasse le theme.
        self.set_size_request(-1, 164)
        self.connect("draw", self._on_draw)

    def _paint_background(self, cr, w, h):
        paint_grille(cr, 0, 0, w, h)
        # Plafond a 72 et non 54 : a 54 dans un champ de 149 px le lettrage
        # tenait sur un tiers de la hauteur et flottait au milieu du vide, comme
        # une legende posee sur la toile. A 72 il en occupe la moitie et 53 % de
        # la largeur, et il appartient enfin au caisson. Le plafond ne mord qu'au
        # dela de 144 px de champ ; en dessous c'est la fraction qui commande.
        paint_logo(cr, w / 2, h / 2, max(20, min(72, h * 0.50)))

    def _on_draw(self, _w, cr):
        # Mise en cache comme les deux conteneurs, cf. _cached_background : la
        # toile tissee coute 16,9 ms dans le champ reel, soit une trame entiere.
        cr.set_source_surface(
            _cached_background(self, self._paint_background), 0, 0)
        cr.paint()
        return False


class Facade(Gtk.Box):
    """L'assemblage complet, sur un fond de tolex.

    Ne connait ni BlueZ, ni Speaker : elle ne sait meme pas qu'une enceinte
    existe. Elle expose des signaux, et marshall-applet les relie.
    """

    __gsignals__ = {
        "knob-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, int)),
        "preset-chosen": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "reconnect-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "autostart-toggled": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        "quit-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, presets, maximums):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        install_css()
        self.get_style_context().add_class("marshall-facade")
        self._background = None
        self._background_size = None
        # True pendant les mises a jour programmees : sans ca, refleter l'etat
        # de l'enceinte declencherait des ecritures vers l'enceinte.
        self._loading = True

        # Une boite interne porte la marge, et NON un border_width sur la facade
        # elle-meme : cf. _cached_background, le border_width ecrete le fond et
        # le lisere dore, trace a 1,5 px du bord, disparaissait completement. Le
        # tolex doit aller jusqu'au bord du caisson comme sur un vrai ampli.
        bands = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=MARGIN)
        bands.set_border_width(MARGIN)
        self.pack_start(bands, True, True, 0)

        self.panel = BrassPanel(maximums)
        bands.pack_start(self.panel, False, False, 0)
        for key, _travel in REGISTERS:
            self.panel.knobs[key].connect("value-changed", self._on_knob, key)
        # Le levier est desormais une piece de la PLAQUE, comme les molettes, et
        # c'est la plaque qui le construit. L'attribut garde son nom et designe le
        # meme objet -- le signal "autostart-toggled" et update() ne changent pas
        # d'un iota, marshall-applet n'a rien a savoir de ce deplacement.
        self.autostart = self.panel.autostart
        self.autostart.connect("toggled", self._on_autostart)

        bands.pack_start(Grille(), True, True, 0)

        row = Gtk.Box(spacing=5)
        self.presets = {}
        for name in presets:
            b = Gtk.Button(label=name)
            b.get_style_context().add_class("marshall-preset")
            b.connect("clicked", lambda _w, n=name: self.emit("preset-chosen", n))
            row.pack_start(b, False, False, 0)
            self.presets[name] = b
        # Bouton et non etiquette : sur GNOME 46 le clic droit sur l'icone ne
        # sort aucun menu, donc "Reconnecter" doit vivre ici ou nulle part.
        self.etat = Gtk.Button()
        self.etat.set_relief(Gtk.ReliefStyle.NONE)
        self.etat.get_style_context().add_class("marshall-etat")
        self.etat.connect("clicked", lambda _w: self.emit("reconnect-requested"))
        # Etiquette explicite, et non Gtk.Button(label=...) : set_label() DETRUIT
        # l'etiquette implicite et en recree une (verifie -- l'objet change et
        # xalign repart a 0.5), donc l'alignement a droite ne survivrait pas a la
        # premiere mise a jour. Or il faut aligner a droite : le bouton a une
        # largeur figee ci-dessous, et un texte centre dedans laisse un trou
        # visible entre le dernier preset et l'etat.
        # Etat initial DECONNECTE, et ce n'est pas arbitraire : rien n'est
        # connecte avant la premiere update(), et c'est aussi le libelle le plus
        # long -- la mesure de largeur ci-dessous n'a donc rien a defaire.
        self._etat_label = Gtk.Label(xalign=1.0)
        self._etat_label.set_markup(STATUS_DISCONNECTED)
        self.etat.add(self._etat_label)
        self.etat.get_style_context().add_class("marshall-etat-off")
        # Largeur figee sur le libelle le plus long, MESUREE et non ecrite en
        # dur : elle depend de la police du theme. Sans ca, tomber en panne
        # faisait grandir la rangee de 85 px (81 -> 166 mesures ici), et une
        # fenetre non redimensionnable en saute de largeur -- observe, elle
        # passait de 420 a 475 px sous le nez de l'utilisateur a chaque perte de
        # lien.
        #
        # show_all() AVANT de mesurer, et ce n'est pas cosmetique : GTK 3
        # court-circuite le calcul de taille d'un widget non visible et rend 0
        # (verifie : 0 avant, 166 apres). Un set_size_request a 0 ne se verrait
        # pas, et la fenetre sauterait quand meme.
        self.etat.show_all()
        self.etat.set_size_request(self.etat.get_preferred_width()[1], -1)
        # UNE SEULE rangee sous la toile, et le pied disparait. Il ne portait plus
        # que le voyant d'etat et "Quitter" depuis que le levier est monte sur la
        # plaque, et une bande de 34 px pour deux boutons ne se justifie pas. Les
        # deux dispositions ont ete MESUREES hors ecran : en gardant un pied, le
        # minimum de la facade est 474 x 445 -- 474 dicte par la plaque elle-meme,
        # qui reclame 450, et non plus par la rangee des presets ; en fusionnant,
        # 521 x 399. L'echange est donc 47 px de large contre 46 px de haut.
        #
        # On prend la fusion, et pas seulement parce que la bande etait vide : la
        # face avant reelle de l'enceinte est nettement plus large que haute
        # (265 x 170 mm, soit 1,56), la fenetre passe de 1,07 a 1,32, et la capsule
        # s'allonge de 436 a 496 px pour la meme hauteur -- rapport de 3,05 a 3,47.
        # C'est ce rapport qui la fait lire comme une capsule plutot que comme un
        # galet.
        #
        # L'ordre des pack_end compte : le premier appele est le plus a droite.
        quit_button = Gtk.Button(label="Quitter")
        quit_button.get_style_context().add_class("marshall-quit")
        quit_button.connect("clicked", lambda _w: self.emit("quit-requested"))
        row.pack_end(quit_button, False, False, 0)
        row.pack_end(self.etat, False, False, 0)
        bands.pack_start(row, False, False, 0)

        self.connect("draw", self._on_draw)
        self._loading = False

    def _paint_background(self, cr, w, h):
        paint_tolex(cr, w, h)
        paint_piping(cr, w, h)

    def _on_draw(self, _w, cr):
        cr.set_source_surface(
            _cached_background(self, self._paint_background), 0, 0)
        cr.paint()
        return False

    def _on_knob(self, _widget, value, key):
        self.panel.set_display(key, value)
        if self._loading:
            return
        self.emit("knob-changed", key, value)

    def _on_autostart(self, _toggle, actif):
        # La garde _loading reste, meme si Toggle ne peut plus emettre depuis
        # set_on_silently : update() est appelee dans les deux sens et le cout
        # d'une ceinture est nul. Le Gtk.Switch, lui, reemettait
        # notify::active depuis set_active(), et c'est ce qui rendait la garde
        # indispensable a l'epoque.
        if self._loading:
            return
        self.emit("autostart-toggled", bool(actif))

    def update(self, state, connected, pending, active_preset, autostart):
        """Mise a jour programmee : ne doit declencher AUCUNE ecriture.

        `pending` est passe explicitement, et non lu dans l'applet : c'est ce
        qui garde ce module ignorant de l'applet. Une valeur encore en vol ne
        doit pas etre ecrasee, sinon la molette sauterait en arriere sous le
        doigt.
        """
        previous = self._loading          # restaurer, pas forcer a False
        self._loading = True
        try:
            if state:
                top = state.get("max_volume")
                if top:
                    self.panel.knobs["volume"].set_maximum_silently(top)
                for key, knob in self.panel.knobs.items():
                    if key in pending or key not in state:
                        continue
                    knob.set_value_silently(state[key])
                    self.panel.set_display(key, state[key])
            for knob in self.panel.knobs.values():
                knob.set_sensitive(bool(connected))
            for name, button in self.presets.items():
                button.set_sensitive(bool(connected))
                context = button.get_style_context()
                if name == active_preset:
                    context.add_class("marshall-preset-actif")
                else:
                    context.remove_class("marshall-preset-actif")
            self._etat_label.set_markup(STATUS_CONNECTED if connected
                                        else STATUS_DISCONNECTED)
            # Une seule classe, portee ou retiree : l'etat connecte est celui de
            # .marshall-etat tout court, inutile de lui en inventer une.
            etat_context = self.etat.get_style_context()
            if connected:
                etat_context.remove_class("marshall-etat-off")
            else:
                etat_context.add_class("marshall-etat-off")
            # Toujours sensible : griser l'etat normal le fait lire comme une
            # panne. Le libelle porte l'etat, le clic est sans effet connecte.
            self.autostart.set_on_silently(autostart)
        finally:
            self._loading = previous
