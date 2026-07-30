"""marshall_ui -- la facade dessinee de l'applet Marshall.

Ce module ne connait NI BlueZ, NI Speaker, NI D-Bus. Il expose des widgets et
des signaux ; c'est marshall-applet qui les relie a l'enceinte. Cette
frontiere est ce qui rend l'interface jugeable et testable sans materiel.

La regle d'architecture du projet s'applique ici : aucun widget n'appelle le
transport. Une Knob emet value-changed, et le debounce de l'applet ecrit.
"""
import math

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
        """
        return self._set(
            self._origin + _round_half_up(dy_up_px * self.maximum / self.travel_px))

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


def _hatch(cr, x, y, w, h, step, rgba, rising):
    """Trame diagonale, utilisee pour le tissage de la toile.

    Chaque segment est decale de h en x entre son depart et son arrivee, donc
    il faut balayer d de -h a w dans LES DEUX sens : partir de 0 laisserait un
    triangle de h pixels de cote sans trame, et la couture diagonale entre les
    deux zones se voit immediatement sur la toile.
    """
    cr.save()
    cr.set_source_rgba(*rgba)
    cr.set_line_width(1)
    d = -h
    while d < w:
        cr.move_to(x + d, y + h if rising else y)
        cr.line_to(x + d + h, y if rising else y + h)
        d += step
    cr.stroke()
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
    cr.set_line_width(1)
    cr.set_source_rgba(1, 0.980, 0.925, 0.22)
    cr.move_to(x, y + 0.5)
    cr.line_to(x + w, y + 0.5)
    cr.stroke()
    cr.set_source_rgba(0, 0, 0, 0.38)
    cr.move_to(x, y + h - 0.5)
    cr.line_to(x + w, y + h - 0.5)
    cr.stroke()
    cr.restore()


def paint_grille(cr, x, y, w, h, radius=3):
    """La toile tissee, et l'ombre interne qui creuse le caisson."""
    cr.save()
    _rounded_path(cr, x, y, w, h, radius)
    cr.clip()
    cr.set_source_rgb(*CLOTH)
    cr.paint()

    # Le tissage doit se voir a 1:1, sans loupe : c'est une bonne part de ce
    # qui rend une facade Marshall reconnaissable. Deux corrections pour ca.
    #
    # Le pas passe de 3 a 4 px. A 3 px, un fil clair et un fil sombre pour
    # deux pixels de fond : les trois se moyennent a l'oeil et il ne reste
    # qu'un aplati. A 4 px chaque maille garde du fond entre ses fils, donc
    # la maille elle-meme devient l'unite visible.
    #
    # Les deux opacites sont appariees a l'oeil, sur une planche d'essai, et pas
    # calculees : ce qui compte est que les deux sens pesent PAREIL, sinon le
    # sens dominant se lit comme une rayure diagonale au lieu d'un tissu. Le
    # noir part avec un handicap -- sur un fond deja sombre il a moins de marge
    # que le clair -- d'ou 0.45 contre 0.16 et non deux valeurs voisines.
    _hatch(cr, x, y, w, h, 4, (0.886, 0.839, 0.698, 0.16), True)
    _hatch(cr, x, y, w, h, 4, (0, 0, 0, 0.45), False)

    # Ombre interne, en deux temps. Le voile radial seul devait monter tres haut
    # en opacite pour creuser les bords, et il noircissait alors le centre au
    # point d'y noyer le logo. On separe donc les deux roles : voile radial
    # doux pour la mise en volume, liseres sombres colles au bord pour le creux.
    #
    # 0.45 et non 0.55 : le voile est justement ce qui efface le tissage, il
    # tire tout vers le noir, fils clairs compris. Le creux perdu au bord est
    # rendu par les liseres juste en dessous, qui eux n'ecrasent que 5 px.
    cx, cy = x + w / 2, y + h / 2
    shade = cairo.RadialGradient(cx, cy, min(w, h) * 0.20,
                                 cx, cy, max(w, h) * 0.72)
    shade.add_color_stop_rgba(0, 0, 0, 0, 0.0)
    shade.add_color_stop_rgba(1, 0, 0, 0, 0.45)
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


def paint_knob(cr, cx, cy, radius, fraction, actif=True):
    """Une molette doree moletee, tournee selon fraction (0..1).

    Le dome et le reflet ne tournent PAS -- seuls le moletage et le repere le
    font. C'est ce qui donne l'illusion d'un objet eclaire par le haut qu'on
    fait pivoter, plutot que d'une image qu'on fait tourner.
    """
    angle = ANGLE_MIN + (ANGLE_MAX - ANGLE_MIN) * max(0.0, min(1.0, fraction))
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

    # Meme famille de laiton que la plaque, un cran plus clair pour que la
    # molette se detache du support. Le point chaud demarre a 0.10 R et non a
    # 0.05 : plus resserre, il donnait une bille de plastique vernie.
    dome = cairo.RadialGradient(cx - radius * 0.28, cy - radius * 0.34,
                                max(0.5, radius * 0.10), cx, cy, radius)
    for pos, rgb in ((0.00, (0.980, 0.949, 0.831)),
                     (0.16, (0.898, 0.808, 0.510)),
                     (0.46, (0.776, 0.635, 0.259)),
                     (0.78, (0.545, 0.443, 0.180)),
                     (1.00, (0.298, 0.239, 0.110))):
        dome.add_color_stop_rgb(pos, *rgb)
    cr.set_source(dome)
    cr.arc(cx, cy, radius, 0, 2 * math.pi)
    cr.fill()

    cr.save()                                 # moletage + repere : ca tourne
    cr.translate(cx, cy)
    cr.rotate(angle)
    cr.set_line_width(max(0.8, radius * 0.04))
    # Le nombre de dents suit le rayon, sinon les deux extremes ratent : 48
    # dents sur un rayon de 4 px, ca fait un demi-pixel d'arc par dent, donc une
    # bouillie grise. Le facteur 1.4 donne ~2.7 px d'arc par dent, soit un
    # trait plus un vide -- la limite en dessous de laquelle ca ne se lit plus.
    teeth = max(10, min(44, int(radius * 1.4)))
    for i in range(teeth):
        a = 2 * math.pi * i / teeth
        cr.set_source_rgba(*((1, 1, 1, 0.26) if i % 2 else (0, 0, 0, 0.34)))
        # Bande courte, collee au bord : de 0.78 a 0.98 R les dents faisaient
        # un cinquieme du rayon et la molette prenait l'air d'un pignon.
        cr.move_to(radius * 0.86 * math.cos(a), radius * 0.86 * math.sin(a))
        cr.line_to(radius * 0.98 * math.cos(a), radius * 0.98 * math.sin(a))
        cr.stroke()

    # Repere : ame sombre cerclee de creme. Le dome est clair en haut a gauche
    # et sombre en bas a droite, et le repere balaye les deux ; une seule
    # couleur disparaitrait forcement a une butee ou a l'autre. Avec le contour,
    # c'est l'ame qui porte sur le clair et le halo qui porte sur le sombre.
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    for width, rgba in ((max(2.4, radius * 0.17), (1, 0.976, 0.898, 0.80)),
                        (max(1.2, radius * 0.085), (0.129, 0.102, 0.016, 1))):
        cr.set_source_rgba(*rgba)
        cr.set_line_width(width)
        cr.move_to(0, -radius * 0.84)
        cr.line_to(0, -radius * 0.36)
        cr.stroke()
    cr.restore()

    # Reflet fixe : il passe APRES le moletage, donc il le glace. A 0.28 il
    # delavait aussi le haut du repere ; 0.18 suffit a poser la lumiere.
    reflect = cairo.LinearGradient(0, cy - radius, 0, cy)
    reflect.add_color_stop_rgba(0, 1, 1, 1, 0.18)
    reflect.add_color_stop_rgba(1, 1, 1, 1, 0.0)
    cr.set_source(reflect)
    cr.arc(cx, cy, radius * 0.97, 0, 2 * math.pi)
    cr.fill()

    if not actif:
        # Voile gris : la molette reste lisible mais visiblement hors service.
        # Volontairement neutre et un peu froid -- teinte trop chaude ou trop
        # opaque, le laiton tourne au brun sale et ca ne dit plus "inactif"
        # mais "abime".
        cr.set_source_rgba(0.14, 0.145, 0.165, 0.56)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.fill()
    cr.restore()
