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
    def connect(self, timeout_s=30):
        """Resout l'enceinte par son SERVICE, jamais par une adresse figee.

        L'adresse BLE de l'Acton III est privee et tournante : une adresse en
        dur echoue systematiquement, et la connexion pend au lieu d'echouer.
        """
        if self._path(UUID_EQ):
            return True

        objs = self._managed()
        self._scan_chars(objs)
        if self._path(UUID_EQ):
            return True

        target = None
        for path, ifaces in objs.items():
            if DEV_IF not in ifaces:
                continue
            d = ifaces[DEV_IF]
            name = d.get("Name") or ""
            if "acton" in name.lower() and d.get("Paired"):
                target = path
                break
        if not target:
            return False

        try:
            self._bus.call_sync(BLUEZ, target, DEV_IF, "Connect", None, None,
                                Gio.DBusCallFlags.NONE, timeout_s * 1000, None)
        except GLib.Error:
            return False

        for _ in range(timeout_s * 2):
            try:
                p = self._bus.call_sync(
                    BLUEZ, target, PROP_IF, "Get",
                    GLib.Variant("(ss)", (DEV_IF, "ServicesResolved")),
                    GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 5000, None)
                if p.unpack()[0]:
                    break
            except GLib.Error:
                pass
            GLib.usleep(500000)

        self._scan_chars()
        return self._path(UUID_EQ) is not None

    def is_connected(self):
        return self._path(UUID_EQ) is not None

    def close(self):
        for sub in self._subs:
            self._bus.signal_unsubscribe(sub)
        self._subs = []
        self._chars = {}


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
