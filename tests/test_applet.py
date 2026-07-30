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
        self.deconnexions += 1

    def close(self):
        pass


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


class TestAutostart(AppletTestCase):
    """L'interrupteur de la fenetre pose ou retire le fichier d'autostart.

    Le fichier est la SEULE source de verite : il peut avoir ete retire a la
    main entre deux ouvertures de la fenetre.
    """

    def setUp(self):
        self.rep = tempfile.TemporaryDirectory()
        self.ancien = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.rep.name

    def tearDown(self):
        if self.ancien is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self.ancien
        self.rep.cleanup()

    def test_desactive_par_defaut_quand_le_fichier_manque(self):
        self.assertFalse(self.mod.autostart_enabled())

    def test_activer_cree_le_fichier(self):
        self.mod.set_autostart(True)
        self.assertTrue(self.mod.autostart_enabled())
        contenu = open(self.mod.autostart_path()).read()
        self.assertIn("[Desktop Entry]", contenu)
        self.assertIn("Type=Application", contenu)
        self.assertIn("Exec=", contenu)

    def test_lexec_pointe_sur_un_chemin_absolu(self):
        self.mod.set_autostart(True)
        ligne = [l for l in open(self.mod.autostart_path())
                 if l.startswith("Exec=")][0]
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
        premier = open(self.mod.autostart_path()).read()
        self.mod.set_autostart(True)
        self.assertEqual(open(self.mod.autostart_path()).read(), premier)

    def test_le_chemin_suit_xdg_config_home(self):
        self.assertTrue(
            self.mod.autostart_path().startswith(self.rep.name),
            "autostart_path ignore XDG_CONFIG_HOME, donc le test polluerait "
            "le vrai ~/.config")


if __name__ == "__main__":
    unittest.main()
