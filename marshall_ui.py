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


# r28, remonte de 24. Le 24 datait du temps ou le border_width de la plaque lui
# mangeait 20 px de hauteur : la place manquait alors pour le libelle et la
# valeur. Depuis que le vide vient des marges des colonnes, elle est la, et un
# disque de 48 px n'occupait que 32 % d'une cellule de 148 -- trois petits plots
# sur une large plaque. A 56 px il en occupe 38 % et se lit comme une commande
# qu'on attrape. C'est aussi le maximum tenable : la plaque grandit de 2 px par
# pixel de rayon, donc r29 porterait le minimum de la facade a 401 px et ferait
# grandir une fenetre dont la taille est arretee a 470x400.
KNOB_RADIUS = 28
KNOB_MARGIN = 6

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
        paint_knob(cr, alloc.width / 2, alloc.height / 2, rayon,
                   self._m.fraction, actif=self.get_sensitive())
        if self.has_visible_focus():
            # Anneau pour le focus clavier seulement. Nuance mesuree : le
            # drapeau est porte par la FENETRE, pas par le geste. Il est faux
            # tant que le clavier n'a pas servi -- une session purement souris
            # n'a donc aucun cerclage -- puis vrai ensuite, y compris apres un
            # clic. C'est l'heuristique de GTK, la meme que ses widgets natifs.
            cr.save()
            cr.set_source_rgba(*GOLD_PIPING, 0.9)
            cr.set_line_width(2)
            cr.arc(alloc.width / 2, alloc.height / 2, rayon + 3, 0, 2 * math.pi)
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


# -- assemblage -----------------------------------------------------------
# Taille par defaut de la fenetre. Ce n'est PAS un cadrage : la facade exige
# deja 461x393 avec la police du theme de cette machine (mesure), et une fenetre
# non redimensionnable plus petite que le minimum grandit d'elle-meme. Ces
# valeurs laissent donc juste un peu de mou, que la Grille absorbe puisqu'elle
# est le seul enfant extensible. La largeur est dictee par la rangee des presets
# plus le bouton d'etat ; la hauteur par la somme des quatre bandes.
WINDOW_WIDTH = 470
WINDOW_HEIGHT = 400
MARGIN = 12
# Laiton visible entre le bord de la plaque et le bloc molette + libelle + valeur.
PLATE_PADDING = 16

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
.marshall-footer .marshall-cap { color: #8d8d92; }
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
/* L'interrupteur du theme arrive dans la couleur d'accent de la session -- ici
   un violet Yaru, qui sur du laiton et du tolex noir est la seule tache de
   couleur etrangere de la fenetre. On le repeint en laiton. Priorite
   APPLICATION, donc ces regles passent devant celles du theme. */
.marshall-footer switch {
  background-image: none; background-color: rgba(0,0,0,0.55);
  border: 1px solid rgba(201,162,39,0.30);
}
.marshall-footer switch:checked {
  background-image: none; background-color: #bf9a1f; border-color: #8d6f1b;
}
.marshall-footer switch slider {
  background-image: none; background-color: #ded0aa;
  border: 1px solid rgba(0,0,0,0.45);
}
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
    """La plaque de laiton : peint son fond, et porte les trois molettes."""

    def __init__(self, maximums):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL,
                         homogeneous=True)
        # AUCUN border_width ici : il rognerait la plaque peinte au lieu de
        # l'encadrer, cf. _cached_background. Le laiton autour du bloc de
        # commandes vient des marges des colonnes.
        self._background = None          # cf. _on_draw
        self._background_size = None
        self.knobs = {}
        for key, travel in REGISTERS:
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            # Haut et bas dissymetriques a dessein : la colonne commence par une
            # Knob, qui porte deja ses KNOB_MARGIN px de vide interne, et finit
            # par une etiquette qui s'arrete pile a son texte. A marge egale la
            # plaque montrerait donc KNOB_MARGIN px de laiton de moins sous la
            # valeur qu'au-dessus de la molette, et la valeur aurait l'air de
            # tomber du bord. On vise du laiton VISIBLE egal des deux cotes.
            column.set_margin_top(PLATE_PADDING - KNOB_MARGIN)
            column.set_margin_bottom(PLATE_PADDING)
            knob = Knob(key, maximum=maximums[key], travel_px=travel)
            caption = Gtk.Label(label=key.upper())
            caption.get_style_context().add_class("marshall-cap")
            value = Gtk.Label(label="0")
            value.get_style_context().add_class("marshall-val")
            column.pack_start(knob, False, False, 0)
            column.pack_start(caption, False, False, 0)
            column.pack_start(value, False, False, 0)
            self.add(column)
            self.knobs[key] = knob
            knob._value_label = value
        self.connect("draw", self._on_draw)

    def _paint_background(self, cr, w, h):
        paint_brass(cr, 0, 0, w, h)

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
        # 140 et non 96 : sur une facade d'ampli la toile DOMINE, le panneau de
        # commandes n'est qu'un bandeau. A 96 la plaque (123 px) etait plus haute
        # que la toile et l'ensemble se lisait comme une barre d'outils posee sur
        # une bande decorative. A 140 le rapport s'inverse enfin.
        self.set_size_request(-1, 140)
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
        row.pack_end(self.etat, False, False, 0)
        bands.pack_start(row, False, False, 0)

        footer = Gtk.Box(spacing=8)
        footer.get_style_context().add_class("marshall-footer")
        self.autostart = Gtk.Switch()
        self.autostart.connect("notify::active", self._on_autostart)
        footer.pack_start(self.autostart, False, False, 0)
        caption = Gtk.Label(label="Démarrer avec la session", xalign=0)
        caption.get_style_context().add_class("marshall-cap")
        footer.pack_start(caption, False, False, 0)
        quit_button = Gtk.Button(label="Quitter")
        quit_button.get_style_context().add_class("marshall-quit")
        quit_button.connect("clicked", lambda _w: self.emit("quit-requested"))
        footer.pack_end(quit_button, False, False, 0)
        bands.pack_start(footer, False, False, 0)

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

    def _on_autostart(self, switch, _param):
        if self._loading:
            return
        self.emit("autostart-toggled", switch.get_active())

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
            self.autostart.set_active(bool(autostart))
        finally:
            self._loading = previous
