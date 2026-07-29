"""Protocole de controle BLE d'une enceinte Marshall Acton III.

Le service de controle 0000fccd-... expose des registres adresses par un octet.
Voir docs/superpowers/specs/2026-07-30-marshall-applet-design.md pour la carte
complete des registres et les pieges du firmware.

Ce module ne connait rien de GTK.
"""

BASS_MAX = 10
TREBLE_MAX = 10
VOLUME_MAX_FALLBACK = 31

_BASE = "1337-1dea-feed-c0ffee70c0de"
UUID_VOLUME = f"00000007-{_BASE}"
UUID_MAXVOL = f"00000008-{_BASE}"
UUID_EQ = f"0000000f-{_BASE}"


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


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
