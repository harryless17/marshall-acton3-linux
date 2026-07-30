"""Protocole de controle BLE d'une enceinte Marshall Acton III.

Le service de controle 0000fccd-... expose des registres adresses par un octet.
Voir docs/superpowers/specs/2026-07-30-marshall-applet-design.md pour la carte
complete des registres et les pieges du firmware.

Ce module ne connait rien de GTK.
"""
import gi
from gi.repository import Gio, GLib

BLUEZ = "org.bluez"
CHAR_IF = "org.bluez.GattCharacteristic1"
DEV_IF = "org.bluez.Device1"
PROP_IF = "org.freedesktop.DBus.Properties"
OBJMGR_IF = "org.freedesktop.DBus.ObjectManager"

BASS_MAX = 10
TREBLE_MAX = 10
VOLUME_MAX_FALLBACK = 31

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
        self._dev_path = None    # device porteur du service de controle

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
        """Connexion reellement active ET service de controle accessible."""
        if not self._device_connected(self._dev_path):
            return False
        eq = self._path(UUID_EQ)
        return bool(eq) and eq.startswith(self._dev_path + "/")

    def close(self):
        for sub in self._subs:
            self._bus.signal_unsubscribe(sub)
        self._subs = []
        self._chars = {}

    # -- lecture ----------------------------------------------------------
    def _read_direct(self, path, timeout=4000):
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

    def _read_cached(self, path, timeout_ms=8000):
        """Lit la propriete Value, que BlueZ tient a jour tant que Notifying
        est actif.

        Remplace l'ancienne approche par GLib.MainLoop imbriquee : plus aucun
        risque de reentrance depuis un callback GTK, et c'est immediat quand
        l'abonnement est deja en place.

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
        """Lecture synchrone complete.

        ATTENTION : peut lancer une GLib.MainLoop imbriquee (via read_eq).
        Reserve au CLI, a l'initialisation et au watchdog -- jamais depuis un
        callback GTK d'interaction.
        """
        if not self.is_connected():
            return None
        eq = decode_eq(self.read_eq())
        if eq is None:
            return None
        pv, pm = self._path(UUID_VOLUME), self._path(UUID_MAXVOL)
        v = self._read_direct(pv) if pv else None
        m = self._read_direct(pm) if pm else None
        state = {
            "volume": v[0] if v else 0,
            "max_volume": m[0] if m else VOLUME_MAX_FALLBACK,
            "bass": eq[0],
            "treble": eq[1],
        }
        self._cache.update(state)     # base des mises a jour partielles
        return state

    # -- ecriture ---------------------------------------------------------
    def _write(self, path, payload):
        """WriteValue avec type=request, repli sur command.

        type=request est un write-with-response : un retour sans erreur signifie
        que l'enceinte a acquitte la valeur au niveau ATT. C'est ce qui autorise
        l'appelant a considerer la valeur comme appliquee sans relire.

        A noter : bluetoothctl rend NotSupported sur un payload multi-octets,
        l'appel D-Bus direct fonctionne.
        """
        for kind in ("request", "command"):
            try:
                self._bus.call_sync(
                    BLUEZ, path, CHAR_IF, "WriteValue",
                    GLib.Variant("(aya{sv})",
                                 (bytes(payload), {"type": GLib.Variant("s", kind)})),
                    None, Gio.DBusCallFlags.NONE, 8000, None)
                return True
            except GLib.Error:
                continue
        return False

    def _set_eq_band(self, band, value):
        """Read-modify-write : ne jamais ecraser l'autre bande."""
        cur = decode_eq(self.read_eq())
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

    def set_volume(self, v):
        p, pm = self._path(UUID_VOLUME), self._path(UUID_MAXVOL)
        if not p:
            return False
        m = self._read_direct(pm) if pm else None
        top = m[0] if m else VOLUME_MAX_FALLBACK
        value = clamp(v, 0, top)
        if not self._write(p, [value]):
            return False
        self._cache["volume"] = value
        return True

    # -- notifications ----------------------------------------------------
    def subscribe(self, callback):
        """Appelle callback(state) a chaque changement signale par l'enceinte.

        Le callback est toujours invoque sur la boucle principale GLib, jamais
        depuis un thread : l'appelant peut toucher l'UI directement.
        """
        self._callback = callback
        for sub in self._subs:
            self._bus.signal_unsubscribe(sub)
        self._subs = []

        for uuid in (UUID_EQ, UUID_VOLUME):
            p = self._path(uuid)
            if not p:
                continue
            self._subs.append(self._bus.signal_subscribe(
                BLUEZ, PROP_IF, "PropertiesChanged", p, None,
                Gio.DBusSignalFlags.NONE, self._on_prop_changed))
            try:
                self._bus.call_sync(BLUEZ, p, CHAR_IF, "StartNotify", None, None,
                                    Gio.DBusCallFlags.NONE, 6000, None)
            except GLib.Error:
                pass

    def _on_prop_changed(self, _c, _s, obj_path, _i, _sig, params):
        _iface, changed, _inv = params.unpack()
        if "Value" not in changed or not self._callback:
            return
        raw = bytes(changed["Value"])
        if obj_path == self._path(UUID_EQ):
            eq = decode_eq(raw)
            if eq:
                self._cache.update(bass=eq[0], treble=eq[1])
        elif obj_path == self._path(UUID_VOLUME) and raw:
            self._cache["volume"] = raw[0]
        else:
            return
        self._cache.setdefault("max_volume", VOLUME_MAX_FALLBACK)
        self._callback(dict(self._cache))

    def start_watchdog(self, on_change):
        """Reconnecte en tache de fond.

        Le dernier palier (30 s) est conserve indefiniment : rallumer l'enceinte
        doit suffire a la reprendre, sans rien relancer. Le cout est negligeable,
        un appel D-Bus local toutes les 30 s.
        """
        self._attempt = 0

        def tick():
            if self.is_connected():
                self._attempt = 0
                return True                    # replanifie au meme intervalle

            if self.connect(timeout_s=20):
                self._attempt = 0
                if self._callback:
                    self.subscribe(self._callback)   # re-abonner apres coupure
                on_change(self.get_state())
                return True

            self._attempt = min(self._attempt + 1, len(self.BACKOFF) - 1)
            GLib.timeout_add_seconds(self.BACKOFF[self._attempt], tick)
            return False                       # celui-ci s'arrete, le nouveau prend

        GLib.timeout_add_seconds(self.BACKOFF[0], tick)


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
