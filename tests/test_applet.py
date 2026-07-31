"""Tests de l'applet avec un faux Speaker : aucun materiel, aucun D-Bus.

Couvre en priorite les bugs trouves par la revue, pour qu'ils ne reviennent pas :
  - un etat partiel ne doit plus faire lever KeyError en boucle
  - un preset doit s'appliquer meme quand aucun preset n'etait actif
  - l'annulation du debounce doit etre par registre, pas globale
  - un preset ne touche jamais au volume
"""
import importlib.util
import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

RACINE = os.path.join(os.path.dirname(__file__), "..")

# Construire la fenetre demande un Gdk.Display : les tests qui en fabriquent une
# sont sautes sans afficheur, comme dans test_knob_widget.py. Toute la logique
# testee ici hors fenetre reste couverte sans ecran.
AFFICHEUR = Gtk.init_check([])[0]

_MODULE = None


def charger_applet():
    """Charge le script marshall-applet (sans extension .py) comme module.

    Charge UNE SEULE FOIS : le module installe un handler de log au chargement,
    et le recharger par classe de test faisait ecrire chaque message autant de
    fois qu'il y avait de classes.

    Le journal est redirige vers un handler nul : les tests ne doivent pas
    polluer ~/.local/state/marshall/applet.log, qui sert au diagnostic reel.
    """
    global _MODULE
    if _MODULE is not None:
        return _MODULE

    # journal neutralise AVANT le chargement, qui appelle _setup_log()
    racine_log = logging.getLogger()
    nul = logging.NullHandler()
    nul._marshall_tag = "marshall-applet"      # fait passer _setup_log pour fait
    racine_log.addHandler(nul)

    chemin = os.path.join(RACINE, "marshall-applet")
    spec = importlib.util.spec_from_loader(
        "applet_sous_test", loader=None, origin=chemin)
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = chemin
    with open(chemin) as f:
        code = compile(f.read(), chemin, "exec")
    sys.modules["applet_sous_test"] = mod
    exec(code, mod.__dict__)
    logging.getLogger("marshall").propagate = False   # silence total
    _MODULE = mod
    return mod


class FauxSpeaker:
    """Speaker minimal : enregistre les ecritures, ne touche a rien."""

    def __init__(self, etat=None, connecte=True):
        self.etat = etat or {"volume": 12, "max_volume": 31, "bass": 8, "treble": 6}
        self.connecte = connecte
        self.ecritures = []          # liste de (registre, valeur)
        self.abonnements = 0
        self.watchdogs = 0
        self.deconnexions = 0
        self.ordre = []              # sequence des appels, pour l'arret

    def is_connected(self):
        return self.connecte

    def connect(self, timeout_s=30):
        return self.connecte

    def get_state(self):
        return dict(self.etat) if self.etat else None

    def cached_state(self):
        return self.get_state()

    def subscribe(self, cb):
        self.abonnements += 1
        self.callback = cb

    def start_watchdog(self, cb):
        self.watchdogs += 1

    def stop_watchdog(self):
        self.ordre.append("stop_watchdog")

    def set_eq(self, bass, treble):
        self.ecritures.append(("eq", (bass, treble)))
        self.etat.update(bass=bass, treble=treble)
        return True

    def set_bass(self, v):
        self.ecritures.append(("bass", v))
        self.etat["bass"] = v
        return True

    def set_treble(self, v):
        self.ecritures.append(("treble", v))
        self.etat["treble"] = v
        return True

    def set_volume(self, v):
        self.ecritures.append(("volume", v))
        self.etat["volume"] = v
        return True

    def disconnect(self):
        self.ordre.append("disconnect")
        self.deconnexions += 1

    def close(self):
        self.ordre.append("close")


class AppletTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = charger_applet()

    NON_FOURNI = object()      # distingue "non precise" d'un None explicite

    def faire_applet(self, speaker=None, state=NON_FOURNI):
        """Applet non demarree : on n'appelle jamais run(), donc pas d'icone."""
        app = self.mod.Applet.__new__(self.mod.Applet)
        # on court-circuite Gtk.Application.__init__ : inutile hors run()
        app.spk = speaker or FauxSpeaker()
        app.state = app.spk.get_state() if state is self.NON_FOURNI else state
        app.win = None
        app.icon = None
        app._menu = None
        app._pending = {}
        app._timer = None
        app._refresh_timer = None
        app._connecting = False
        app._refreshes = 0
        # neutralise l'UI : on teste la logique, pas le rendu
        app._refresh = lambda: setattr(app, "_refreshes", app._refreshes + 1)
        return app


class TestEtatPartiel(AppletTestCase):
    """Le bug le plus grave : un dict incomplet figeait l'applet."""

    CLES = ("volume", "max_volume", "bass", "treble")

    def test_etat_partiel_nest_pas_considere_comme_connecte(self):
        for manquante in self.CLES:
            partiel = {k: 1 for k in self.CLES if k != manquante}
            app = self.faire_applet(state=partiel)
            self.assertFalse(
                app._connected(),
                f"etat sans '{manquante}' accepte comme connecte")

    def test_etat_complet_est_connecte(self):
        app = self.faire_applet()
        self.assertTrue(app._connected())

    def test_etat_none_nest_pas_connecte(self):
        app = self.faire_applet(state=None)
        self.assertFalse(app._connected())

    def test_notification_partielle_ne_leve_pas(self):
        # ce que le module livrait autrefois : un dict sans 'volume'
        app = self.faire_applet(state=None)
        app.on_external_change({"bass": 8, "treble": 6, "max_volume": 31})
        self.assertFalse(app._connected())      # refuse, mais ne leve pas


class TestPresets(AppletTestCase):
    def test_preset_applique_quand_aucun_netait_actif(self):
        # bass 6 / treble 6 ne correspond a aucun preset
        spk = FauxSpeaker({"volume": 12, "max_volume": 31, "bass": 6, "treble": 6})
        app = self.faire_applet(spk)
        app.apply_preset("Neutre")
        self.assertEqual(spk.ecritures, [("eq", (5, 5))])

    def test_preset_deja_applique_nest_pas_reecrit(self):
        spk = FauxSpeaker({"volume": 12, "max_volume": 31, "bass": 8, "treble": 6})
        app = self.faire_applet(spk)
        app.apply_preset("Films")                # Films = 8/6, deja en place
        self.assertEqual(spk.ecritures, [])

    def test_preset_ecrit_les_deux_bandes_en_une_trame(self):
        spk = FauxSpeaker()
        app = self.faire_applet(spk)
        app.apply_preset("Voix / podcast")       # 3/8
        self.assertEqual(len(spk.ecritures), 1)
        self.assertEqual(spk.ecritures[0][0], "eq")

    def test_preset_ne_touche_jamais_au_volume(self):
        spk = FauxSpeaker()
        app = self.faire_applet(spk)
        for nom in self.mod.PRESETS:
            app.apply_preset(nom)
        self.assertNotIn("volume", [reg for reg, _ in spk.ecritures])


class TestDebounceParRegistre(AppletTestCase):
    def test_un_preset_nefface_pas_un_volume_en_attente(self):
        spk = FauxSpeaker()
        app = self.faire_applet(spk)
        app._pending = {"volume": 25}
        app.apply_preset("Neutre")
        self.assertIn("volume", app._pending,
                      "le volume en attente a ete jete par un preset")

    def test_une_notification_de_volume_nefface_pas_un_bass_en_attente(self):
        spk = FauxSpeaker()
        app = self.faire_applet(spk)
        app._pending = {"bass": 4}
        nouveau = dict(app.state)
        nouveau["volume"] = 20               # seul le volume a change
        app.on_external_change(nouveau)
        self.assertIn("bass", app._pending,
                      "le bass en attente a ete jete par une notif de volume")

    def test_une_notification_efface_le_registre_quelle_rapporte(self):
        spk = FauxSpeaker()
        app = self.faire_applet(spk)
        app._pending = {"bass": 4}
        nouveau = dict(app.state)
        nouveau["bass"] = 9                  # l'enceinte contredit l'attente
        app.on_external_change(nouveau)
        self.assertNotIn("bass", app._pending)


class TestReglagesInconnus(AppletTestCase):
    def test_cle_inconnue_est_ignoree_sans_planter(self):
        spk = FauxSpeaker()
        app = self.faire_applet(spk)
        app._apply({"pouet": 3})             # ne doit pas lever AttributeError
        self.assertEqual(spk.ecritures, [])


@unittest.skipUnless(AFFICHEUR, "aucun Gdk.Display : fenetre GTK inconstructible")
class TestFenetreEtatIncomplet(AppletTestCase):
    """La fenetre doit encaisser un etat absent ou partiel.

    _do_refresh appelle self.win.update(self.state, connected) sans condition,
    et self.state vaut None jusqu'a la premiere lecture reussie -- une enceinte
    eteinte a l'ouverture de session suffit. C'est la forme exacte du bug qui a
    deja mordu ce projet : un etat partiel levait KeyError a chaque
    rafraichissement, en boucle, et figeait l'applet pour de bon.
    """

    def faire_fenetre(self, app):
        w = self.mod.SpeakerWindow(app)
        self.addCleanup(w.destroy)
        return w

    def test_update_avec_etat_none_ne_leve_pas(self):
        app = self.faire_applet(state=None)
        self.faire_fenetre(app).update(None, False)

    def test_update_avec_etat_partiel_ne_leve_pas(self):
        """Ce que le module a deja livre : un dict sans 'volume'."""
        partiel = {"bass": 8, "treble": 6, "max_volume": 31}
        app = self.faire_applet(state=partiel)
        self.assertFalse(app._connected())     # donc connected=False
        self.faire_fenetre(app).update(partiel, False)

    def test_update_avec_etat_complet_ne_leve_pas(self):
        app = self.faire_applet()
        self.faire_fenetre(app).update(app.state, True)

    def test_update_nemet_aucune_ecriture(self):
        """Refleter l'etat de l'enceinte ne doit pas reecrire vers l'enceinte.
        Sans le garde _loading de la Facade, poser la valeur d'une molette
        emettrait "knob-changed", donc un schedule_write, donc une ecriture --
        une boucle infernale a chaque notification de molette physique.
        """
        spk = FauxSpeaker()
        app = self.faire_applet(spk)
        self.faire_fenetre(app).update(app.state, True)
        self.assertEqual(spk.ecritures, [])
        self.assertEqual(app._pending, {})


class TestAutostart(AppletTestCase):
    """L'interrupteur de la fenetre pose ou retire le fichier d'autostart.

    Le fichier est la SEULE source de verite : il peut avoir ete retire a la
    main entre deux ouvertures de la fenetre.
    """

    def setUp(self):
        self.rep = tempfile.TemporaryDirectory()
        self.ancien = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.rep.name
        # HOME est seulement memorise ici : un seul test le deplace, mais la
        # restauration est inconditionnelle pour qu'un echec en cours de test
        # ne laisse pas les suivants avec un faux HOME.
        self.ancien_home = os.environ.get("HOME")

    def tearDown(self):
        if self.ancien is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self.ancien
        if self.ancien_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.ancien_home
        self.rep.cleanup()

    def test_desactive_par_defaut_quand_le_fichier_manque(self):
        self.assertFalse(self.mod.autostart_enabled())

    def test_activer_cree_le_fichier(self):
        self.mod.set_autostart(True)
        self.assertTrue(self.mod.autostart_enabled())
        with open(self.mod.autostart_path()) as f:
            contenu = f.read()
        self.assertIn("[Desktop Entry]", contenu)
        self.assertIn("Type=Application", contenu)
        self.assertIn("Exec=", contenu)

    def test_lexec_pointe_sur_un_chemin_absolu(self):
        self.mod.set_autostart(True)
        with open(self.mod.autostart_path()) as f:
            ligne = [l for l in f if l.startswith("Exec=")][0]
        chemin = ligne.split("=", 1)[1].strip()
        self.assertTrue(chemin.startswith("/"), f"Exec relatif : {chemin}")
        self.assertTrue(chemin.endswith("marshall-applet"))

    def test_desactiver_retire_le_fichier(self):
        self.mod.set_autostart(True)
        self.mod.set_autostart(False)
        self.assertFalse(os.path.exists(self.mod.autostart_path()))

    def test_desactiver_deux_fois_ne_leve_pas(self):
        self.mod.set_autostart(False)
        self.mod.set_autostart(False)     # le fichier n'a jamais existe
        self.assertFalse(self.mod.autostart_enabled())

    def test_activer_deux_fois_est_idempotent(self):
        self.mod.set_autostart(True)
        with open(self.mod.autostart_path()) as f:
            premier = f.read()
        self.mod.set_autostart(True)
        with open(self.mod.autostart_path()) as f:
            self.assertEqual(f.read(), premier)

    def test_lexec_retombe_sur_le_script_sans_installation(self):
        """Sans le lien de install.sh -- lancement direct depuis le depot --
        Exec doit pointer sur le script reel, et pas sur un lien absent.

        La branche de repli n'etait jamais parcourue : le lien existe sur la
        machine de developpement, donc le premier terme gagnait toujours. On
        deplace HOME sur un repertoire vide, ou ~/bin/marshall-applet ne peut
        pas exister.
        """
        os.environ["HOME"] = self.rep.name
        attendu = os.path.realpath(self.mod.__file__)
        self.assertEqual(self.mod.applet_exec_path(), attendu)
        # garde-fou : si le repli n'avait pas ete pris, on lirait le chemin du
        # lien sous le HOME temporaire au lieu du script
        self.assertFalse(
            self.mod.applet_exec_path().startswith(self.rep.name),
            "applet_exec_path a renvoye le lien absent au lieu du script")

    def test_le_chemin_suit_xdg_config_home(self):
        self.assertTrue(
            self.mod.autostart_path().startswith(self.rep.name),
            "autostart_path ignore XDG_CONFIG_HOME, donc le test polluerait "
            "le vrai ~/.config")


class FausseIcone:
    """Gtk.StatusIcon minimal : ne trace que ce qui compte pour l'arret."""

    def __init__(self, journal):
        self.journal = journal
        self.visible = True

    def set_visible(self, v):
        self.visible = v
        if not v:
            self.journal.append("icone_masquee")

    def set_from_icon_name(self, _n):
        pass

    def set_tooltip_text(self, _t):
        pass


class TestSequenceDarret(AppletTestCase):
    """Le bug percu : jusqu'a ~3 s entre le clic sur Quitter et la disparition
    de l'icone, parce que do_shutdown parlait a BlueZ d'abord."""

    def setUp(self):
        """Neutralise le chain-up de do_shutdown, cote test uniquement.

        do_shutdown finit par Gtk.Application.do_shutdown(self), qui exige un
        GObject reellement initialise -- or faire_applet passe par __new__ et
        court-circuite Gtk.Application.__init__, donc l'appel leve
        RuntimeError("not initialized"). Le retirer du code de production
        supprimerait l'arret propre de GTK pour de vrai, donc on le remplace
        ici, a la classe : ni l'instance ni le module ne sont touches, et
        tearDown remet la methode d'origine.
        """
        self._chain_up = Gtk.Application.do_shutdown
        Gtk.Application.do_shutdown = lambda _self: None

    def tearDown(self):
        Gtk.Application.do_shutdown = self._chain_up

    def faire_applet_arretable(self):
        spk = FauxSpeaker()
        app = self.faire_applet(spk)
        app.icon = FausseIcone(spk.ordre)
        app._quits = 0

        # quit() est JOURNALISE, pas seulement compte. C'est lui qui, en vrai,
        # fait emettre "shutdown" par GTK et donc appeler do_shutdown : sans sa
        # position dans la sequence, deplacer quit() avant le masquage de
        # l'icone ne changeait rien au journal, puisque le test appelle
        # do_shutdown a la main juste apres. Mutation verifiee : non detectee
        # sans cette ligne, detectee avec.
        def _quit():
            app._quits += 1
            spk.ordre.append("quit")

        app.quit = _quit
        return app, spk

    def test_licone_est_masquee_avant_la_liberation_ble(self):
        app, spk = self.faire_applet_arretable()
        app.on_quit()
        app.do_shutdown()
        self.assertIn("icone_masquee", spk.ordre)
        self.assertIn("disconnect", spk.ordre)
        self.assertLess(spk.ordre.index("icone_masquee"),
                        spk.ordre.index("disconnect"),
                        "l'icone reste visible pendant que BlueZ repond")
        self.assertLess(spk.ordre.index("icone_masquee"),
                        spk.ordre.index("quit"),
                        "quit() est ce qui declenche do_shutdown : masquer "
                        "l'icone apres, c'est la masquer une fois BlueZ "
                        "deja sollicite")

    def test_le_watchdog_est_coupe_avant_le_demontage(self):
        app, spk = self.faire_applet_arretable()
        app.on_quit()
        app.do_shutdown()
        self.assertLess(spk.ordre.index("stop_watchdog"),
                        spk.ordre.index("disconnect"),
                        "un cycle de watchdog peut rouvrir la connexion "
                        "pendant qu'on la ferme")

    def test_on_quit_demande_bien_la_sortie_de_boucle(self):
        app, _spk = self.faire_applet_arretable()
        app.on_quit()
        self.assertEqual(app._quits, 1)

    def test_les_ecritures_en_attente_partent_quand_meme(self):
        app, spk = self.faire_applet_arretable()
        app._pending = {"volume": 25}
        app.on_quit()
        app.do_shutdown()
        self.assertIn(("volume", 25), spk.ecritures)

    def test_on_quit_sans_icone_ne_leve_pas(self):
        """Quitter avant que do_activate ait construit l'icone."""
        spk = FauxSpeaker()
        app = self.faire_applet(spk)
        app.quit = lambda: None
        app.on_quit()                    # app.icon vaut None


if __name__ == "__main__":
    unittest.main()
