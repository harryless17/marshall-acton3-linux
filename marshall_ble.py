"""Protocole de controle BLE d'une enceinte Marshall Acton III.

Le service de controle 0000fccd-... expose des registres adresses par un octet.
Voir docs/superpowers/specs/2026-07-30-marshall-applet-design.md pour la carte
complete des registres et les pieges du firmware.

Ce module ne connait rien de GTK.
"""
import logging

from gi.repository import Gio, GLib

log = logging.getLogger("marshall")

BLUEZ = "org.bluez"
CHAR_IF = "org.bluez.GattCharacteristic1"
DEV_IF = "org.bluez.Device1"
PROP_IF = "org.freedesktop.DBus.Properties"
OBJMGR_IF = "org.freedesktop.DBus.ObjectManager"

BASS_MAX = 10
TREBLE_MAX = 10
VOLUME_MAX_FALLBACK = 31

_STATE_KEYS = ("volume", "max_volume", "bass", "treble")

# Timeouts. Mesures sur le materiel, lien etabli : ReadValue et WriteValue
# repondent en ~90 ms. Les valeurs ci-dessous sont donc genereuses tout en
# bornant le gel de l'appelant si l'enceinte ne repond plus.
WRITE_TIMEOUT_MS = 4000
READ_TIMEOUT_MS = 2000
CACHED_READ_TIMEOUT_MS = 3000
CONNECT_TIMEOUT_S = 20
POLL_INTERVAL_S = 30        # sondage quand le lien est deja etabli


def _plausible_eq(eq):
    """Ecarte les trames absurdes (0xff = "intouche", ou autre semantique).

    Le chemin ecriture borne partout ; le chemin lecture ne bornait rien, si
    bien qu'une trame ff ff ff ff ff donnait bass=255 dans l'interface.
    """
    bass, treble = eq
    return 0 <= bass <= BASS_MAX and 0 <= treble <= TREBLE_MAX

_BASE = "1337-1dea-feed-c0ffee70c0de"
UUID_VOLUME = f"00000007-{_BASE}"
UUID_MAXVOL = f"00000008-{_BASE}"
UUID_EQ = f"0000000f-{_BASE}"


# Presets bass/treble. Volontairement SANS volume : un preset ne doit jamais
# changer le volume, qui reste sous le controle exclusif de l'utilisateur.
PRESETS = {
    "Neutre": {"bass": 5, "treble": 5},
    "Films": {"bass": 8, "treble": 6},
    "Musique": {"bass": 10, "treble": 7},
    "Voix / podcast": {"bass": 3, "treble": 8},
}


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def match_preset(bass, treble):
    """Nom du preset correspondant exactement aux valeurs, sinon None."""
    for name, p in PRESETS.items():
        if p["bass"] == bass and p["treble"] == treble:
            return name
    return None


class Speaker:
    """Connexion au canal de controle BLE de l'enceinte.

    Tous les appels passent par BlueZ en D-Bus, sur la boucle GLib.
    """

    BACKOFF = [1, 2, 5, 10, 30]

    def __init__(self):
        self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self._chars = {}
        self._cache = {}
        self._callback = None
        self._subs = []
        self._attempt = 0
        self._dev_path = None       # device porteur du service de controle
        self._callback_change = None
        self._watchdog_on = False   # une seule chaine de timers, cf. start_watchdog
        self._watchdog_source = None   # id du timer en cours, cf. stop_watchdog

    # -- introspection ----------------------------------------------------
    def _managed(self):
        res = self._bus.call_sync(
            BLUEZ, "/", OBJMGR_IF, "GetManagedObjects", None,
            GLib.VariantType("(a{oa{sa{sv}}})"),
            Gio.DBusCallFlags.NONE, 10000, None,
        )
        return res.unpack()[0]

    def _scan_chars(self, objs=None):
        objs = objs if objs is not None else self._managed()
        self._chars = {
            ifaces[CHAR_IF].get("UUID", "").lower(): path
            for path, ifaces in objs.items() if CHAR_IF in ifaces
        }
        return self._chars

    def _path(self, uuid):
        return self._chars.get(uuid.lower())

    # -- connexion --------------------------------------------------------
    def _device_connected(self, path):
        """Device1.Connected : la SEULE source de verite.

        BlueZ garde les objets GATT en cache apres deconnexion -- verifie : 25
        caracteristiques encore listees avec Connected=False. Se fier a leur
        presence fait croire a une connexion active alors que rien ne repond.
        """
        if not path:
            return False
        try:
            p = self._bus.call_sync(
                BLUEZ, path, PROP_IF, "Get",
                GLib.Variant("(ss)", (DEV_IF, "Connected")),
                GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 5000, None)
            return bool(p.unpack()[0])
        except GLib.Error:
            return False

    def connect(self, timeout_s=30):
        """Resout l'enceinte par son SERVICE, jamais par une adresse figee.

        L'adresse BLE de l'Acton III est privee et tournante : une adresse en
        dur echoue systematiquement, et la connexion pend au lieu d'echouer.
        """
        if self.is_connected():
            return True

        objs = self._managed()

        # L'enceinte expose PLUSIEURS identites appairees, et une seule offre le
        # service de controle :
        #   ACTON III [LE]  -> identite BLE, expose fccd
        #   ACTON III       -> identite audio BR/EDR, aucun service GATT
        # Se connecter a la mauvaise "reussit" sans rien exposer. Il faut donc
        # les essayer toutes, en commencant par les identites LE.
        cands = []
        for path, ifaces in objs.items():
            if DEV_IF not in ifaces:
                continue
            d = ifaces[DEV_IF]
            name = d.get("Name") or ""
            low = name.lower()
            if ("acton" not in low and "marshall" not in low) or not d.get("Paired"):
                continue
            cands.append((0 if "[le]" in low else 1, path, name))
        if not cands:
            return False
        cands.sort(key=lambda c: c[0])

        for _, path, _name in cands:
            try:
                self._bus.call_sync(BLUEZ, path, DEV_IF, "Connect", None, None,
                                    Gio.DBusCallFlags.NONE, timeout_s * 1000, None)
            except GLib.Error:
                continue

            for _ in range(timeout_s * 2):
                try:
                    p = self._bus.call_sync(
                        BLUEZ, path, PROP_IF, "Get",
                        GLib.Variant("(ss)", (DEV_IF, "ServicesResolved")),
                        GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 5000, None)
                    if p.unpack()[0]:
                        break
                except GLib.Error:
                    pass
                GLib.usleep(500000)

            if not self._device_connected(path):
                continue                     # Connect() a rendu sans connecter

            self._scan_chars()
            eq = self._path(UUID_EQ)
            # la char doit appartenir A CE device : les chars d'un autre device
            # peuvent trainer dans le cache de BlueZ.
            if eq and eq.startswith(path + "/"):
                self._dev_path = path
                return True

        return False

    def is_connected(self):
        """Connexion reellement active ET service de controle accessible.

        Purge le cache a la premiere detection d'une coupure : ses valeurs sont
        alors perimees, et la propriete Value de BlueZ survit a la deconnexion
        (verifie) -- garder l'ancien etat ferait afficher du faux comme du vrai.
        """
        if not self._device_connected(self._dev_path):
            if self._cache:
                self._cache = {}
            return False
        eq = self._path(UUID_EQ)
        if not eq or not eq.startswith(self._dev_path + "/"):
            return False
        return True

    def close(self):
        """Libere tout : abonnements, cache, identite du device.

        Le cache DOIT etre purge, sinon une reconnexion ulterieure melangerait
        des valeurs fraiches (arrivees par notification) avec des valeurs de la
        session precedente, et livrerait ce melange comme un etat coherent.
        """
        for sub in self._subs:
            self._bus.signal_unsubscribe(sub)
        self._subs = []
        self._chars = {}
        self._cache = {}
        self._dev_path = None
        self.stop_watchdog()

    # -- lecture ----------------------------------------------------------
    def _read_direct(self, path, timeout=READ_TIMEOUT_MS):
        try:
            res = self._bus.call_sync(
                BLUEZ, path, CHAR_IF, "ReadValue",
                GLib.Variant("(a{sv})", ({},)), GLib.VariantType("(ay)"),
                Gio.DBusCallFlags.NONE, timeout, None)
            return bytes(res.unpack()[0])
        except GLib.Error:
            return None

    def _char_prop(self, path, prop):
        try:
            return self._bus.call_sync(
                BLUEZ, path, PROP_IF, "Get",
                GLib.Variant("(ss)", (CHAR_IF, prop)),
                GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 5000, None
            ).unpack()[0]
        except GLib.Error:
            return None

    def _start_notify(self, path):
        try:
            self._bus.call_sync(BLUEZ, path, CHAR_IF, "StartNotify", None, None,
                                Gio.DBusCallFlags.NONE, 6000, None)
            return True
        except GLib.Error:
            return False

    def _read_cached(self, path, timeout_ms=CACHED_READ_TIMEOUT_MS):
        """Lit la propriete Value, que BlueZ tient a jour tant que Notifying
        est actif.

        Immediat quand l'abonnement est deja en place. Ne sert que de repli :
        mesure sur ce firmware, lien etabli, ReadValue sur l'EQ repond en ~90 ms,
        donc _read_direct suffit la plupart du temps.

        Deux subtilites verifiees sur le materiel :
          - appeler StartNotify alors que Notifying est DEJA vrai ne pousse
            aucune valeur (elle ne l'est qu'au premier abonnement) ; il faut
            donc tester Notifying avant, et sinon lire Value directement ;
          - sur une connexion fraiche, StartNotify peut echouer et Value rester
            vide une seconde ou deux ; on retente jusqu'au timeout.
        """
        restant = timeout_ms
        while restant > 0:
            if self._char_prop(path, "Notifying"):
                v = self._char_prop(path, "Value")
                if v:
                    return bytes(v)
            else:
                self._start_notify(path)
            GLib.usleep(250000)
            restant -= 250
        return None

    def read_eq(self):
        """Le registre EQ ne repond pas a ReadValue sur ce firmware -- l'appel
        pend. On passe par la propriete Value alimentee par les notifications.
        """
        p = self._path(UUID_EQ)
        if not p:
            return None
        return self._read_direct(p) or self._read_cached(p)

    def get_state(self):
        """Lecture synchrone. Rend un etat COMPLET (4 cles) ou None.

        ATTENTION : bloquant. Aucune reentrance possible (call_sync ne pompe pas
        la boucle GLib), mais l'appelant est gele pendant l'appel. A n'appeler
        que hors handler GTK : initialisation, watchdog, CLI.

        Ne fabrique aucune valeur : si le volume est illisible on rend None
        plutot qu'un "volume 0" qui serait pris pour une lecture reussie.
        """
        if not self.is_connected():
            return None
        eq = decode_eq(self.read_eq())
        if eq is None or not _plausible_eq(eq):
            return None
        pv, pm = self._path(UUID_VOLUME), self._path(UUID_MAXVOL)
        v = self._read_direct(pv) if pv else None
        if not v:
            return None                      # pas d'invention de valeur
        m = self._read_direct(pm) if pm else None
        top = m[0] if m else 0
        if not top:                          # 0x00 illisible ou absurde
            top = VOLUME_MAX_FALLBACK
        state = {
            "volume": min(v[0], top),
            "max_volume": top,
            "bass": eq[0],
            "treble": eq[1],
        }
        self._cache.update(state)     # base des mises a jour partielles
        return state

    # -- ecriture ---------------------------------------------------------
    def _write(self, path, payload):
        """WriteValue en type=request (write-with-response).

        ATTENTION a ne pas surinterpreter le retour : un True signifie que
        l'enceinte a acquitte au niveau ATT, PAS qu'elle a applique la valeur.
        Mesure a l'appui : ecrire bass=12 (hors plage) rend sans erreur et
        l'enceinte ignore purement l'ecriture. L'appelant doit donc verifier par
        relecture ou par notification s'il veut une garantie.

        Pas de repli en type=command : c'est un write-without-response, donc sans
        acquittement d'aucune sorte -- il ferait croire a un succes alors que
        rien ne serait parti.

        A noter : bluetoothctl rend NotSupported sur un payload multi-octets,
        l'appel D-Bus direct fonctionne.
        """
        try:
            self._bus.call_sync(
                BLUEZ, path, CHAR_IF, "WriteValue",
                GLib.Variant("(aya{sv})",
                             (bytes(payload), {"type": GLib.Variant("s", "request")})),
                None, Gio.DBusCallFlags.NONE, WRITE_TIMEOUT_MS, None)
            return True
        except GLib.Error:
            return False

    def _current_eq(self):
        """bass/treble courants, depuis le cache si possible.

        Le cache est alimente en continu par les notifications (~90 ms de
        latence). L'utiliser evite une relecture qui, hors connexion, bloquait
        l'appelant plusieurs secondes.
        """
        if "bass" in self._cache and "treble" in self._cache:
            return self._cache["bass"], self._cache["treble"]
        return decode_eq(self.read_eq())

    def _set_eq_band(self, band, value):
        """Read-modify-write : ne jamais ecraser l'autre bande."""
        if not self.is_connected():
            return False
        cur = self._current_eq()
        if cur is None:
            return False
        bass, treble = cur
        if band == "bass":
            bass = value
        else:
            treble = value
        p = self._path(UUID_EQ)
        if not p or not self._write(p, encode_eq(bass, treble)):
            return False
        self._cache.update(bass=clamp(bass, 0, BASS_MAX),
                           treble=clamp(treble, 0, TREBLE_MAX))
        return True

    def set_bass(self, v):
        return self._set_eq_band("bass", v)

    def set_treble(self, v):
        return self._set_eq_band("treble", v)

    def set_eq(self, bass, treble):
        """Ecrit les deux bandes en UNE trame.

        Deux fois plus rapide qu'un set_bass suivi d'un set_treble, et surtout
        sans fenetre de course entre les deux (le second relisait ce que le
        premier venait d'ecrire). C'est ce que doivent utiliser les presets.
        """
        if not self.is_connected():
            return False
        p = self._path(UUID_EQ)
        if not p or not self._write(p, encode_eq(bass, treble)):
            return False
        self._cache.update(bass=clamp(bass, 0, BASS_MAX),
                           treble=clamp(treble, 0, TREBLE_MAX))
        return True

    def max_volume(self):
        """Volume max, mis en cache : 0x08 est en lecture seule et constant."""
        if self._cache.get("max_volume"):
            return self._cache["max_volume"]
        pm = self._path(UUID_MAXVOL)
        m = self._read_direct(pm) if pm else None
        top = m[0] if m else 0
        if not top:                     # 0x00 renvoye : ne pas borner a 0
            top = VOLUME_MAX_FALLBACK
        self._cache["max_volume"] = top
        return top

    def set_volume(self, v):
        if not self.is_connected():
            return False
        p = self._path(UUID_VOLUME)
        if not p:
            return False
        value = clamp(v, 0, self.max_volume())
        if not self._write(p, [value]):
            return False
        self._cache["volume"] = value
        return True

    # -- notifications ----------------------------------------------------
    def subscribe(self, callback):
        """Appelle callback(state) a chaque changement signale par l'enceinte.

        Le callback est toujours invoque sur la boucle principale GLib, jamais
        depuis un thread : l'appelant peut toucher l'UI directement.

        Peut etre appele hors connexion : le callback est memorise, et le
        watchdog rebranchera les abonnements des que le lien sera etabli. Sans
        cela, une enceinte eteinte au demarrage privait la session entiere des
        notifications de molettes physiques.
        """
        self._callback = callback
        self._resubscribe()

    def _resubscribe(self):
        for sub in self._subs:
            self._bus.signal_unsubscribe(sub)
        self._subs = []
        if not self._callback:
            return

        for uuid in (UUID_EQ, UUID_VOLUME):
            p = self._path(uuid)
            if not p:
                continue
            self._subs.append(self._bus.signal_subscribe(
                BLUEZ, PROP_IF, "PropertiesChanged", p, None,
                Gio.DBusSignalFlags.NONE, self._on_prop_changed))
            self._start_notify(p)

    def _on_prop_changed(self, _c, _s, obj_path, _i, _sig, params):
        _iface, changed, _inv = params.unpack()
        if "Value" not in changed or not self._callback:
            return
        raw = bytes(changed["Value"])
        if obj_path == self._path(UUID_EQ):
            eq = decode_eq(raw)
            if not eq or not _plausible_eq(eq):
                return                  # trame absurde : ne pas polluer le cache
            self._cache.update(bass=eq[0], treble=eq[1])
        elif obj_path == self._path(UUID_VOLUME) and raw:
            top = self._cache.get("max_volume") or VOLUME_MAX_FALLBACK
            if raw[0] > top:
                return                  # hors plage : trame ininterpretable
            self._cache["volume"] = raw[0]
        else:
            return

        # N'emettre que des etats COMPLETS. Le cache peut n'avoir qu'une partie
        # des cles (get_state jamais passe, ou echoue) ; livrer un dict partiel
        # faisait lever KeyError chez le consommateur, en boucle.
        state = self.cached_state()
        if state is not None:
            self._callback(state)

    def cached_state(self):
        """Etat complet depuis le cache, ou None s'il est incomplet."""
        if all(k in self._cache for k in _STATE_KEYS):
            return {k: self._cache[k] for k in _STATE_KEYS}
        return None

    def start_watchdog(self, on_change):
        """Reconnecte en tache de fond, et resynchronise l'etat.

        Le dernier palier (30 s) est conserve indefiniment : rallumer l'enceinte
        doit suffire a la reprendre, sans rien relancer.

        Idempotent : plusieurs appels ne creent qu'une seule chaine de timers.
        Sans cela, chaque clic sur "Reconnecter" en empilait une de plus, chacune
        lancant ses propres connexions bloquantes et se volant le compteur de
        backoff.
        """
        self._callback_change = on_change
        if self._watchdog_on:
            return
        self._watchdog_on = True
        self._attempt = 0
        self._replanifier(self.BACKOFF[0])

    def _replanifier(self, delai_s):
        """Un seul endroit ou une source est creee, pour que _watchdog_source
        soit toujours l'id du timer reellement en attente."""
        self._watchdog_source = GLib.timeout_add_seconds(delai_s, self._tick)

    def stop_watchdog(self):
        """Coupe la chaine de timers, et rien d'autre.

        Separe de close() parce que la sequence d'arret a besoin de couper le
        watchdog AVANT d'appeler disconnect(), qui lui a encore besoin de
        _dev_path -- que close() effacerait.
        """
        self._watchdog_on = False
        if self._watchdog_source is not None:
            try:
                GLib.source_remove(self._watchdog_source)
            except (ValueError, GLib.Error):
                pass                 # deja terminee : rien a retirer
            self._watchdog_source = None

    def _tick(self):
        """Un cycle de surveillance. Ne doit JAMAIS laisser filer d'exception :
        PyGObject retire la source quand un callback leve, ce qui tuait la
        reconnexion pour le reste de la session -- et sans trace, la sortie
        d'erreur allant dans le vide."""
        # la source qui nous appelle s'acheve en rendant False : on l'oublie
        # avant tout, sinon stop_watchdog tenterait de retirer un id mort.
        self._watchdog_source = None
        if not self._watchdog_on:
            return False             # coupe pendant l'attente : on s'arrete la
        try:
            return self._tick_inner()
        except Exception:
            log.exception("watchdog: cycle en echec, on replanifie")
            self._replanifier(POLL_INTERVAL_S)
            return False

    def _tick_inner(self):
        if self.is_connected():
            self._attempt = 0
            # Lien up mais etat inconnu : c'etait une impasse, le watchdog
            # considerait la connexion bonne et ne relisait jamais l'etat.
            if self.cached_state() is None:
                st = self.get_state()
                if st:
                    self._resubscribe()
                    self._notify(st)
            self._replanifier(POLL_INTERVAL_S)
            return False

        if self.connect(timeout_s=CONNECT_TIMEOUT_S):
            self._attempt = 0
            self._resubscribe()          # rebranche meme si subscribe() n'avait
            self._notify(self.get_state())   # jamais reussi avant
            self._replanifier(POLL_INTERVAL_S)
            return False

        self._attempt = min(self._attempt + 1, len(self.BACKOFF) - 1)
        self._replanifier(self.BACKOFF[self._attempt])
        return False

    def _notify(self, state):
        if self._callback_change:
            self._callback_change(state)

    def disconnect(self):
        """Coupe le lien LE et libere le canal de controle.

        Sans cela, quitter l'applet laissait BlueZ connecte : le canal restait
        monopolise alors que l'utilisateur venait justement de fermer pour
        laisser la place a autre chose.
        """
        for uuid in (UUID_EQ, UUID_VOLUME):
            p = self._path(uuid)
            if p:
                try:
                    self._bus.call_sync(BLUEZ, p, CHAR_IF, "StopNotify", None, None,
                                        Gio.DBusCallFlags.NONE, 4000, None)
                except GLib.Error:
                    pass
        if self._dev_path:
            try:
                self._bus.call_sync(BLUEZ, self._dev_path, DEV_IF, "Disconnect",
                                    None, None, Gio.DBusCallFlags.NONE, 8000, None)
            except GLib.Error:
                pass


def decode_eq(raw):
    """(bass, treble) depuis la trame 5 octets, ou None si inexploitable."""
    if not raw or len(raw) < 5:
        return None
    return raw[0], raw[4]


def encode_eq(bass, treble):
    """Trame [bass, 0xff, 0xff, 0xff, treble].

    L'Acton III n'expose que les deux bandes extremes d'un EQ 5 bandes
    (160/400/1k/2.5k/6.25k) ; les trois du milieu doivent rester 0xff,
    c'est-a-dire "intouchees".
    """
    return bytes([
        clamp(bass, 0, BASS_MAX), 0xFF, 0xFF, 0xFF, clamp(treble, 0, TREBLE_MAX),
    ])
