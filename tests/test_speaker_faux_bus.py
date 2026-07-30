"""Tests de la couche D-Bus avec un faux bus : aucun materiel, aucun BlueZ.

Speaker n'accede a D-Bus que par self._bus, ce qui rend toute la classe
testable en le remplacant.

Priorite : epingler les regressions qui ont deja coute cher.
  - is_connected() ne doit PAS se fier aux caracteristiques presentes : BlueZ
    les garde en cache apres deconnexion (25 chars observees avec
    Connected=False), ce qui faisait croire a une connexion active
  - connect() doit essayer TOUTES les identites appairees, les [LE] d'abord :
    l'enceinte expose aussi une identite audio sans aucun service GATT, et s'y
    connecter "reussit" sans rien exposer
  - _on_prop_changed ne doit jamais livrer un etat partiel
  - le watchdog doit survivre a une GLib.Error
"""
import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gi.repository import GLib  # noqa: E402

import marshall_ble as m  # noqa: E402

DEV_LE = "/org/bluez/hci0/dev_C1_3F_B3_48_69_09"
DEV_AUDIO = "/org/bluez/hci0/dev_74_68_59_6F_AD_B1"


def chars_de(dev):
    """Table des caracteristiques du service de controle pour un device."""
    return {
        f"{dev}/service0200/char0206": m.UUID_VOLUME,
        f"{dev}/service0200/char021b": m.UUID_MAXVOL,
        f"{dev}/service0200/char020f": m.UUID_EQ,
    }


class FauxBus:
    """Bus D-Bus minimal, scriptable.

    devices  : {chemin: {"Name":…, "Paired":…, "Connected":…}}
    chars    : {chemin_char: uuid} — peut contenir des chars de devices
               deconnectes, exactement comme le cache de BlueZ
    valeurs  : {chemin_char: bytes}
    """

    def __init__(self, devices=None, chars=None, valeurs=None,
                 notifying=None, lever_sur=()):
        self.devices = devices or {}
        self.chars = chars or {}
        self.valeurs = valeurs or {}
        self.notifying = notifying or {}
        self.lever_sur = set(lever_sur)      # noms de methodes qui echouent
        self.appels = []                     # (methode, chemin)
        self.ecritures = []                  # (chemin, bytes)
        self.connects = []                   # chemins sur lesquels Connect

    # -- infrastructure ---------------------------------------------------
    def _peut_lever(self, methode):
        if methode in self.lever_sur:
            raise GLib.Error(f"faux echec sur {methode}")

    def call_sync(self, _dest, chemin, iface, methode, params,
                  _rtype, _flags, _timeout, _cancellable):
        self.appels.append((methode, chemin))
        self._peut_lever(methode)

        if methode == "GetManagedObjects":
            # a{sv} exige des GLib.Variant en valeurs ; Speaker fait ensuite
            # .unpack(), qui les reconvertit en types Python natifs.
            objets = {}
            for d, props in self.devices.items():
                objets[d] = {m.DEV_IF: {
                    "Name": GLib.Variant("s", props.get("Name", "")),
                    "Paired": GLib.Variant("b", bool(props.get("Paired"))),
                    "Connected": GLib.Variant("b", bool(props.get("Connected"))),
                }}
            for c, uuid in self.chars.items():
                objets[c] = {m.CHAR_IF: {"UUID": GLib.Variant("s", uuid)}}
            return GLib.Variant("(a{oa{sa{sv}}})", (objets,))

        if methode == "Connect":
            self.connects.append(chemin)
            if chemin in self.devices:
                self.devices[chemin]["Connected"] = True
            return None

        if methode == "Disconnect":
            if chemin in self.devices:
                self.devices[chemin]["Connected"] = False
            return None

        if methode == "Get":
            _i, prop = params.unpack()
            if prop in ("Connected", "ServicesResolved"):
                inner = GLib.Variant(
                    "b", bool(self.devices.get(chemin, {}).get("Connected")))
            elif prop == "Notifying":
                inner = GLib.Variant("b", bool(self.notifying.get(chemin)))
            elif prop == "Value":
                inner = GLib.Variant("ay", list(self.valeurs.get(chemin, b"")))
            else:
                raise GLib.Error(f"propriete inconnue {prop}")
            return GLib.Variant("(v)", (inner,))

        if methode == "ReadValue":
            v = self.valeurs.get(chemin)
            if v is None:
                raise GLib.Error("pas de valeur")
            return GLib.Variant("(ay)", (list(v),))

        if methode == "WriteValue":
            payload = bytes(params.unpack()[0])
            self.ecritures.append((chemin, payload))
            self.valeurs[chemin] = payload
            return None

        if methode == "StartNotify":
            self.notifying[chemin] = True
            return None

        if methode == "StopNotify":
            self.notifying[chemin] = False
            return None

        raise GLib.Error(f"methode non simulee {methode}")

    def signal_subscribe(self, *_a, **_k):
        return 1

    def signal_unsubscribe(self, _id):
        pass


def faire_speaker(bus):
    """Speaker sans toucher au vrai bus systeme."""
    s = m.Speaker.__new__(m.Speaker)
    s._bus = bus
    s._chars = {}
    s._cache = {}
    s._callback = None
    s._subs = []
    s._attempt = 0
    s._dev_path = None
    s._callback_change = None
    s._watchdog_on = False
    s._watchdog_source = None
    return s


EQ_LE = f"{DEV_LE}/service0200/char020f"
VOL_LE = f"{DEV_LE}/service0200/char0206"
MAX_LE = f"{DEV_LE}/service0200/char021b"


def bus_nominal(connected=True):
    return FauxBus(
        devices={DEV_LE: {"Name": "ACTON III [LE]", "Paired": True,
                          "Connected": connected}},
        chars=chars_de(DEV_LE),
        valeurs={EQ_LE: bytes([8, 0xFF, 0xFF, 0xFF, 6]),
                 VOL_LE: bytes([13]), MAX_LE: bytes([31])},
        notifying={EQ_LE: True},
    )


class TestCacheGattApresDeconnexion(unittest.TestCase):
    """LE bug du commit 37ee647 : ne pas se fier aux chars en cache."""

    def test_chars_en_cache_mais_device_deconnecte_nest_pas_connecte(self):
        bus = bus_nominal(connected=False)     # chars presentes, lien coupe
        s = faire_speaker(bus)
        s._scan_chars()
        s._dev_path = DEV_LE
        self.assertFalse(
            s.is_connected(),
            "les caracteristiques en cache ont ete prises pour une connexion")

    def test_le_cache_est_purge_a_la_detection_de_coupure(self):
        bus = bus_nominal(connected=False)
        s = faire_speaker(bus)
        s._dev_path = DEV_LE
        s._cache = {"volume": 13, "max_volume": 31, "bass": 8, "treble": 6}
        s.is_connected()
        self.assertEqual(s._cache, {},
                         "un etat perime a survecu a la coupure")

    def test_get_state_rend_none_hors_connexion(self):
        bus = bus_nominal(connected=False)
        s = faire_speaker(bus)
        s._dev_path = DEV_LE
        self.assertIsNone(s.get_state())


class TestChoixDeLidentite(unittest.TestCase):
    """L'identite audio n'expose aucun service : il faut essayer les autres."""

    def bus_deux_identites(self):
        return FauxBus(
            devices={
                DEV_AUDIO: {"Name": "ACTON III", "Paired": True,
                            "Connected": True},
                DEV_LE: {"Name": "ACTON III [LE]", "Paired": True,
                         "Connected": False},
            },
            # seules les chars de l'identite LE existent
            chars=chars_de(DEV_LE),
            valeurs={EQ_LE: bytes([8, 0xFF, 0xFF, 0xFF, 6]),
                     VOL_LE: bytes([13]), MAX_LE: bytes([31])},
            notifying={EQ_LE: True},
        )

    def test_connect_retient_lidentite_qui_porte_le_service(self):
        bus = self.bus_deux_identites()
        s = faire_speaker(bus)
        self.assertTrue(s.connect(timeout_s=1))
        self.assertEqual(s._dev_path, DEV_LE)

    def test_lidentite_le_est_essayee_en_premier(self):
        bus = self.bus_deux_identites()
        s = faire_speaker(bus)
        s.connect(timeout_s=1)
        self.assertEqual(bus.connects[0], DEV_LE,
                         "l'identite audio a ete tentee avant la LE")

    def test_une_char_dun_autre_device_ne_valide_pas_la_connexion(self):
        # chars de l'identite LE en cache, mais c'est l'audio qui est connectee
        bus = FauxBus(
            devices={DEV_AUDIO: {"Name": "ACTON III", "Paired": True,
                                 "Connected": True}},
            chars=chars_de(DEV_LE),
        )
        s = faire_speaker(bus)
        self.assertFalse(s.connect(timeout_s=1))

    def test_aucun_device_appaire_echoue_proprement(self):
        bus = FauxBus(devices={DEV_LE: {"Name": "ACTON III [LE]",
                                        "Paired": False, "Connected": False}})
        s = faire_speaker(bus)
        self.assertFalse(s.connect(timeout_s=1))


class TestEtatsPartiels(unittest.TestCase):
    """_on_prop_changed ne doit jamais livrer un dict incomplet."""

    def faire_connecte(self):
        bus = bus_nominal()
        s = faire_speaker(bus)
        s.connect(timeout_s=1)
        return s

    def notifier(self, s, chemin, payload):
        params = GLib.Variant("(sa{sv}as)",
                              (m.CHAR_IF, {"Value": GLib.Variant("ay", list(payload))}, []))
        s._on_prop_changed(None, None, chemin, None, None, params)

    def test_notification_sur_cache_vide_nemet_rien(self):
        s = self.faire_connecte()
        s._cache = {}                       # get_state jamais passe
        recus = []
        s.subscribe(recus.append)
        self.notifier(s, EQ_LE, bytes([8, 0xFF, 0xFF, 0xFF, 6]))
        self.assertEqual(recus, [],
                         "un etat partiel a ete livre au consommateur")

    def test_notification_emet_un_etat_complet_quand_le_cache_lest(self):
        s = self.faire_connecte()
        s.get_state()                       # remplit les 4 cles
        recus = []
        s.subscribe(recus.append)
        self.notifier(s, EQ_LE, bytes([3, 0xFF, 0xFF, 0xFF, 9]))
        self.assertEqual(len(recus), 1)
        self.assertEqual(set(recus[0]), {"volume", "max_volume", "bass", "treble"})
        self.assertEqual((recus[0]["bass"], recus[0]["treble"]), (3, 9))

    def test_trame_eq_absurde_est_ignoree(self):
        s = self.faire_connecte()
        s.get_state()
        recus = []
        s.subscribe(recus.append)
        self.notifier(s, EQ_LE, bytes([0xFF] * 5))   # 255 : hors plage
        self.assertEqual(recus, [], "une trame ff ff ff ff ff a ete acceptee")
        self.assertEqual(s._cache["bass"], 8, "le cache a ete pollue")

    def test_volume_hors_plage_est_ignore(self):
        s = self.faire_connecte()
        s.get_state()
        recus = []
        s.subscribe(recus.append)
        self.notifier(s, VOL_LE, bytes([99]))        # > max 31
        self.assertEqual(recus, [])
        self.assertEqual(s._cache["volume"], 13)


class TestLectureDetat(unittest.TestCase):
    def test_get_state_rend_les_quatre_cles(self):
        s = faire_speaker(bus_nominal())
        s.connect(timeout_s=1)
        st = s.get_state()
        self.assertEqual(st, {"volume": 13, "max_volume": 31,
                              "bass": 8, "treble": 6})

    def test_volume_illisible_rend_none_plutot_que_zero(self):
        bus = bus_nominal()
        del bus.valeurs[VOL_LE]              # lecture du volume impossible
        s = faire_speaker(bus)
        s.connect(timeout_s=1)
        self.assertIsNone(s.get_state(),
                          "un volume illisible a ete rapporte comme 0")

    def test_max_volume_a_zero_ne_borne_pas_tout_a_zero(self):
        bus = bus_nominal()
        bus.valeurs[MAX_LE] = bytes([0])     # 0x00 absurde
        s = faire_speaker(bus)
        s.connect(timeout_s=1)
        self.assertEqual(s.max_volume(), m.VOLUME_MAX_FALLBACK)

    def test_cached_state_incomplet_rend_none(self):
        s = faire_speaker(bus_nominal())
        s._cache = {"bass": 8, "treble": 6}
        self.assertIsNone(s.cached_state())


class TestEcriture(unittest.TestCase):
    def faire_connecte(self):
        s = faire_speaker(bus_nominal())
        s.connect(timeout_s=1)
        s.get_state()
        return s

    def test_set_eq_ecrit_une_seule_trame(self):
        s = self.faire_connecte()
        avant = len(s._bus.ecritures)
        self.assertTrue(s.set_eq(3, 9))
        self.assertEqual(len(s._bus.ecritures) - avant, 1)
        self.assertEqual(s._bus.ecritures[-1][1], bytes([3, 0xFF, 0xFF, 0xFF, 9]))

    def test_set_bass_preserve_le_treble(self):
        s = self.faire_connecte()
        s.set_bass(2)
        self.assertEqual(s._bus.ecritures[-1][1], bytes([2, 0xFF, 0xFF, 0xFF, 6]))

    def test_setters_refusent_hors_connexion_sans_bloquer(self):
        bus = bus_nominal(connected=False)
        s = faire_speaker(bus)
        s._dev_path = DEV_LE
        self.assertFalse(s.set_bass(4))
        self.assertFalse(s.set_treble(4))
        self.assertFalse(s.set_volume(4))
        self.assertFalse(s.set_eq(4, 4))
        self.assertEqual(bus.ecritures, [], "une ecriture est partie hors connexion")

    def test_pas_de_repli_en_write_without_response(self):
        # le repli type=command masquait les echecs : il ne doit plus exister
        bus = bus_nominal()
        s = faire_speaker(bus)
        s.connect(timeout_s=1)
        s.get_state()
        bus.lever_sur.add("WriteValue")
        self.assertFalse(s.set_volume(20))

    def test_bornage_a_lecriture(self):
        s = self.faire_connecte()
        s.set_eq(99, -5)
        self.assertEqual(s._bus.ecritures[-1][1],
                         bytes([m.BASS_MAX, 0xFF, 0xFF, 0xFF, 0]))


class TestWatchdog(unittest.TestCase):
    """Une exception qui s'echappe faisait retirer la source par PyGObject,
    tuant la reconnexion pour toute la session."""

    def test_le_cycle_survit_a_une_erreur_dbus(self):
        bus = bus_nominal(connected=False)
        bus.lever_sur.add("GetManagedObjects")
        s = faire_speaker(bus)
        s._dev_path = DEV_LE
        # le watchdog journalise la trace : attendu, mais inutile ici
        m.log.setLevel(logging.CRITICAL)
        try:
            resultat = s._tick()             # ne doit PAS propager
        except GLib.Error:
            self.fail("une GLib.Error s'est echappee du watchdog")
        finally:
            m.log.setLevel(logging.NOTSET)
        self.assertFalse(resultat)

    def test_start_watchdog_est_idempotent(self):
        s = faire_speaker(bus_nominal())
        s.start_watchdog(lambda _st: None)
        self.assertTrue(s._watchdog_on)
        s.start_watchdog(lambda _st: None)   # ne doit pas empiler une 2e chaine
        s.start_watchdog(lambda _st: None)
        self.assertTrue(s._watchdog_on)

    def test_subscribe_memorise_le_callback_hors_connexion(self):
        bus = bus_nominal(connected=False)
        s = faire_speaker(bus)
        cb = lambda _st: None
        s.subscribe(cb)                      # aucune connexion
        self.assertIs(s._callback, cb,
                      "le callback est perdu si l'enceinte est eteinte")

    def test_lien_up_mais_etat_inconnu_est_resolu(self):
        s = faire_speaker(bus_nominal())
        s.connect(timeout_s=1)
        s._cache = {}                        # etat inconnu
        recus = []
        s._callback_change = recus.append
        s._tick_inner()
        self.assertTrue(recus, "l'impasse 'lien up, etat inconnu' persiste")
        self.assertEqual(set(recus[0]), {"volume", "max_volume", "bass", "treble"})

    def test_close_retire_la_source_du_watchdog(self):
        s = faire_speaker(bus_nominal())
        s.start_watchdog(lambda _st: None)
        self.assertIsNotNone(s._watchdog_source)
        s.close()
        self.assertIsNone(s._watchdog_source)
        self.assertFalse(s._watchdog_on)

    def test_close_puis_start_ne_cree_quune_chaine(self):
        """Le drapeau _watchdog_on etait inerte : close() le remettait a False
        sans retirer le timer, donc un start_watchdog() suivant empilait une
        seconde chaine -- exactement ce que l'idempotence evitait."""
        s = faire_speaker(bus_nominal())
        s.start_watchdog(lambda _st: None)
        premiere = s._watchdog_source
        s.close()
        s.start_watchdog(lambda _st: None)
        self.assertIsNotNone(s._watchdog_source)
        self.assertNotEqual(s._watchdog_source, premiere)

    def test_un_cycle_ne_replanifie_pas_apres_close(self):
        s = faire_speaker(bus_nominal())
        s.start_watchdog(lambda _st: None)
        s.close()
        self.assertFalse(s._tick())          # ne doit pas replanifier
        self.assertIsNone(s._watchdog_source)

    def test_stop_watchdog_preserve_le_chemin_du_device(self):
        """disconnect() a encore besoin de _dev_path : couper le watchdog ne
        doit donc PAS faire le menage de close()."""
        s = faire_speaker(bus_nominal())
        s.connect(timeout_s=1)
        s.start_watchdog(lambda _st: None)
        s.stop_watchdog()
        self.assertFalse(s._watchdog_on)
        self.assertIsNotNone(s._dev_path)


class TestFermeture(unittest.TestCase):
    def test_close_purge_le_cache(self):
        s = faire_speaker(bus_nominal())
        s.connect(timeout_s=1)
        s.get_state()
        s.close()
        self.assertEqual(s._cache, {})
        self.assertIsNone(s._dev_path)

    def test_disconnect_coupe_le_lien(self):
        bus = bus_nominal()
        s = faire_speaker(bus)
        s.connect(timeout_s=1)
        s.disconnect()
        self.assertIn("Disconnect", [meth for meth, _ in bus.appels])
        self.assertFalse(bus.devices[DEV_LE]["Connected"])


if __name__ == "__main__":
    unittest.main()
