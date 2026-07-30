> ## ⚠️ DOCUMENT HISTORIQUE — PÉRIMÉ
>
> Ce plan décrit l'intention *avant* implémentation. Plusieurs de ses prémisses
> se sont révélées fausses à l'exécution, et son code de référence ne correspond
> plus à ce qui tourne. En particulier :
>
> - le mécanisme d'icône est **`Gtk.StatusIcon`**, pas `AyatanaAppIndicator3` ;
> - la lecture de l'EQ ne passe plus par une `GLib.MainLoop` imbriquée ;
> - `is_connected()` s'appuie sur `Device1.Connected`, pas sur la présence des
>   caractéristiques (que BlueZ garde en cache après déconnexion) ;
> - `connect()` essaie **toutes** les identités appairées, identités `[LE]`
>   d'abord ;
> - les latences réelles sont ~90 ms, pas 1–2 s.
>
> **Pour l'état réel du projet : lire le README, la spec, puis le code.**
>
> **Verdict de la Task 0** (qui était bloquante) : l'enceinte notifie bien les
> changements faits sur ses molettes physiques — 70 notifications observées, et
> cran par cran, jusqu'à ~20 par seconde sur le volume. Le reflet en direct est
> donc réalisable et implémenté. C'est cette mesure qui a imposé le regroupement
> des rafraîchissements d'UI, absent de ce plan.

# Applet Marshall Acton III — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Piloter volume, bass et treble d'une enceinte Marshall Acton III depuis la barre système GNOME, avec un module protocole partagé entre l'applet et le CLI.

**Architecture:** Un module `marshall_ble.py` porte tout le protocole BLE en Gio/GLib, avec sa logique pure (encodage EQ, bornes, presets) séparée de sa couche D-Bus pour être testable sans matériel. L'applet GTK3 et le CLI consomment ce module ; une seule boucle d'événements GLib, aucune dépendance hors PyGObject déjà présent.

**Tech Stack:** Python 3.12, PyGObject (Gtk 3.0, Gio, GLib), AyatanaAppIndicator3 0.1, `unittest` (stdlib), BlueZ via D-Bus.

**Spec:** `docs/superpowers/specs/2026-07-30-marshall-applet-design.md`

---

## Rappels critiques du protocole

À garder sous les yeux, ce sont les pièges qui ont coûté le plus cher :

- **Le registre EQ `0x0f` ne répond PAS à `ReadValue`** (l'appel pend jusqu'au timeout) mais **pousse sa valeur dès `StartNotify`**. Toute lecture d'EQ passe par l'abonnement.
- **Jamais d'adresse BLE en dur** : elle est privée et tournante. On résout le device en cherchant la caractéristique EQ dans `GetManagedObjects`.
- **`WriteValue` avec `{"type": "request"}`**, repli `"command"`.
- L'identité BLE doit être **bonded** au préalable (déjà fait sur la machine cible : `C1:3F:B3:48:69:09`).
- Ne **jamais** faire de `remove` sur `74:68:59:6F:AD:B1` — c'est l'appairage audio.
- Valeurs d'origine de l'utilisateur à restaurer après tout test : **bass=10, treble=7, volume=12**.

## Structure des fichiers

| Fichier | Responsabilité |
|---|---|
| `marshall_ble.py` | Protocole : logique pure + couche D-Bus + `Speaker` |
| `marshall-applet` | UI : indicateur, menu, fenêtre de réglages, debounce |
| `marshall-ctl` | CLI sur le module |
| `install.sh` | Liens symboliques vers `~/bin`, module, autostart |
| `tests/test_pure.py` | Tests des fonctions pures — aucun matériel requis |
| `tests/test_speaker.py` | Tests d'intégration — sautés si l'enceinte est absente |

Séparation voulue : `marshall_ble.py` ne connaît rien de GTK, `marshall-applet` ne fait aucun appel D-Bus direct.

## Deux points d'architecture identifiés en relecture

**1. Pas de `GLib.MainLoop` imbriquée dans l'applet.** La lecture de l'EQ passe
par `StartNotify`, donc par une boucle d'événements. Dans le CLI c'est anodin.
Dans l'applet, lancer une boucle imbriquée depuis un callback GTK provoque une
réentrance de la boucle principale : l'UI peut geler ou traiter des événements
dans un ordre inattendu. Règle :

- `read_eq()` / `get_state()` sont **réservés au CLI et à l'initialisation**.
- Dans l'applet, après une écriture, l'état est mis à jour **avec la valeur
  écrite**, sans relecture. Ce n'est pas de l'optimisme aveugle : `WriteValue`
  est émis avec `type=request`, c'est-à-dire un write-with-response que
  l'enceinte acquitte au niveau ATT. Un retour OK signifie qu'elle a accepté —
  vérifié empiriquement (écriture bass=3 puis relecture = 3).
- Le reste du temps, l'état vient des notifications permanentes.

**2. Le reflet des molettes physiques est une hypothèse non vérifiée.** Les
tests ont montré que l'enceinte pousse sa valeur *à l'abonnement*, et qu'elle
ne notifie **pas** nos propres écritures. Qu'elle notifie les changements
d'origine physique reste à démontrer. La Task 0 le tranche avant que le design
en dépende.

---

### Task 0 : Vérifier si l'enceinte notifie les changements physiques

**Files:**
- Create: `tests/manual_notify_probe.py`

Cette tâche est **bloquante** : son résultat décide si le reflet en direct des
molettes est réalisable ou doit être retiré du périmètre.

- [ ] **Step 1: Écrire la sonde**

```python
# tests/manual_notify_probe.py
"""Sonde manuelle : l'enceinte notifie-t-elle les changements faits sur ses
molettes physiques ?

Lancer, puis tourner les molettes BASS puis TREBLE puis VOLUME sur l'enceinte.
Chaque notification recue s'affiche avec un horodatage.
"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import gi
from gi.repository import GLib
from marshall_ble import Speaker, UUID_EQ, UUID_VOLUME

spk = Speaker()
if not spk.connect(timeout_s=30):
    sys.exit("enceinte indisponible")

recu = []
def on_state(state):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] NOTIFICATION -> {state}")
    recu.append(state)

spk.subscribe(on_state)
print("Abonne. TOURNE MAINTENANT les molettes BASS, TREBLE et VOLUME")
print("sur l'enceinte. 45 s d'ecoute. Ctrl-C pour arreter.\n")

loop = GLib.MainLoop()
GLib.timeout_add_seconds(45, lambda: (loop.quit(), False)[1])
try:
    loop.run()
except KeyboardInterrupt:
    pass
print(f"\n>>> {len(recu)} notification(s) recue(s).")
print(">>> REFLET PHYSIQUE " + ("REALISABLE" if recu else "IMPOSSIBLE — retirer du perimetre"))
```

- [ ] **Step 2: Exécuter et manipuler l'enceinte**

Run: `python3 -u tests/manual_notify_probe.py`
Puis tourner physiquement les molettes.

Expected, deux issues possibles :
- **≥1 notification** → le reflet en direct est réalisable, le plan continue tel quel.
- **0 notification** → retirer du périmètre : supprimer `on_external_change` du
  chemin des molettes, retirer la ligne « molette physique tournée » de la
  checklist Task 11, et noter la limite dans la spec. Le reste du plan est
  inchangé (les notifications restent utilisées pour la lecture d'EQ, qui elle
  est prouvée).

Cette tâche dépend de Task 3 (`Speaker.connect`) et Task 6 (`subscribe`) ; la
dérouler juste après Task 6, avant de construire l'UI en Task 8.

- [ ] **Step 3: Commit**

```bash
git add tests/manual_notify_probe.py
git commit -m "test: sonde manuelle des notifications de changement physique"
```

---

### Task 1 : Fonctions pures d'encodage de l'EQ

**Files:**
- Create: `marshall_ble.py`
- Test: `tests/test_pure.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pure.py
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from marshall_ble import clamp, decode_eq, encode_eq, BASS_MAX, TREBLE_MAX


class TestClamp(unittest.TestCase):
    def test_dans_les_bornes(self):
        self.assertEqual(clamp(5, 0, 10), 5)

    def test_sous_la_borne(self):
        self.assertEqual(clamp(-3, 0, 10), 0)

    def test_au_dessus(self):
        self.assertEqual(clamp(99, 0, 10), 10)


class TestDecodeEq(unittest.TestCase):
    def test_valeurs_reelles_de_lenceinte(self):
        # trame observee sur le materiel : bass=10, treble=7
        self.assertEqual(decode_eq(bytes([0x0A, 0xFF, 0xFF, 0xFF, 0x07])), (10, 7))

    def test_trame_trop_courte(self):
        self.assertIsNone(decode_eq(bytes([0x0A, 0xFF])))

    def test_trame_vide(self):
        self.assertIsNone(decode_eq(b""))

    def test_none(self):
        self.assertIsNone(decode_eq(None))


class TestEncodeEq(unittest.TestCase):
    def test_les_trois_bandes_du_milieu_restent_intouchees(self):
        out = encode_eq(bass=6, treble=8)
        self.assertEqual(out, bytes([6, 0xFF, 0xFF, 0xFF, 8]))

    def test_bornage(self):
        self.assertEqual(encode_eq(bass=99, treble=-4),
                         bytes([BASS_MAX, 0xFF, 0xFF, 0xFF, 0]))

    def test_longueur_toujours_5(self):
        self.assertEqual(len(encode_eq(0, 0)), 5)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Bureau/marshall-applet && python3 -m unittest discover tests -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'marshall_ble'`

- [ ] **Step 3: Write minimal implementation**

```python
# marshall_ble.py
"""Protocole de controle BLE d'une enceinte Marshall Acton III.

Le service de controle 0000fccd-... expose des registres adresses par un octet.
Voir docs/superpowers/specs/ pour la carte complete et les pieges.
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

    L'Acton III n'expose que les deux bandes extremes d'un EQ 5 bandes ;
    les trois du milieu doivent rester 0xff = intouchees.
    """
    return bytes([
        clamp(bass, 0, BASS_MAX), 0xFF, 0xFF, 0xFF, clamp(treble, 0, TREBLE_MAX),
    ])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover tests -v`
Expected: PASS, 10 tests

- [ ] **Step 5: Commit**

```bash
git add marshall_ble.py tests/test_pure.py
git commit -m "feat: encodage/decodage de l'EQ et bornage"
```

---

### Task 2 : Presets

**Files:**
- Modify: `marshall_ble.py`
- Test: `tests/test_pure.py`

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/test_pure.py` :

```python
from marshall_ble import PRESETS, match_preset


class TestPresets(unittest.TestCase):
    def test_les_quatre_presets_valides(self):
        self.assertEqual(
            {n: (p["bass"], p["treble"]) for n, p in PRESETS.items()},
            {"Neutre": (5, 5), "Films": (8, 6),
             "Musique": (10, 7), "Voix / podcast": (3, 8)},
        )

    def test_musique_correspond_aux_reglages_dorigine(self):
        # l'utilisateur doit retrouver son son d'un clic
        self.assertEqual((PRESETS["Musique"]["bass"], PRESETS["Musique"]["treble"]),
                         (10, 7))

    def test_match_exact(self):
        self.assertEqual(match_preset(8, 6), "Films")

    def test_aucun_match(self):
        self.assertIsNone(match_preset(4, 4))

    def test_un_preset_ne_definit_pas_de_volume(self):
        for p in PRESETS.values():
            self.assertNotIn("volume", p)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover tests -v`
Expected: FAIL — `ImportError: cannot import name 'PRESETS'`

- [ ] **Step 3: Write minimal implementation**

Ajouter à `marshall_ble.py` :

```python
# Presets bass/treble. Volontairement SANS volume : un preset ne doit jamais
# changer le volume, qui reste sous le controle exclusif de l'utilisateur.
PRESETS = {
    "Neutre": {"bass": 5, "treble": 5},
    "Films": {"bass": 8, "treble": 6},
    "Musique": {"bass": 10, "treble": 7},
    "Voix / podcast": {"bass": 3, "treble": 8},
}


def match_preset(bass, treble):
    """Nom du preset correspondant exactement, sinon None."""
    for name, p in PRESETS.items():
        if p["bass"] == bass and p["treble"] == treble:
            return name
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover tests -v`
Expected: PASS, 15 tests

- [ ] **Step 5: Commit**

```bash
git add marshall_ble.py tests/test_pure.py
git commit -m "feat: presets bass/treble et detection du preset actif"
```

---

### Task 3 : Couche D-Bus — découverte et connexion

**Files:**
- Modify: `marshall_ble.py`
- Test: `tests/test_speaker.py`

Code déjà validé expérimentalement contre BlueZ ; le reprendre tel quel.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_speaker.py
"""Tests d'integration : necessitent l'enceinte allumee et son identite BLE
appairee. Sautes automatiquement sinon, pour que la suite reste verte sur une
machine sans materiel.
"""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from marshall_ble import Speaker, decode_eq, UUID_EQ


class SpeakerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spk = Speaker()
        if not cls.spk.connect(timeout_s=30):
            raise unittest.SkipTest("enceinte Acton III indisponible")

    @classmethod
    def tearDownClass(cls):
        cls.spk.close()


class TestConnexion(SpeakerTestCase):
    def test_connectee(self):
        self.assertTrue(self.spk.is_connected())

    def test_caracteristique_eq_resolue(self):
        self.assertIsNotNone(self.spk._path(UUID_EQ))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_speaker -v`
Expected: FAIL — `ImportError: cannot import name 'Speaker'`

- [ ] **Step 3: Write minimal implementation**

Ajouter à `marshall_ble.py` :

```python
import gi
from gi.repository import Gio, GLib

BLUEZ = "org.bluez"
CHAR_IF = "org.bluez.GattCharacteristic1"
DEV_IF = "org.bluez.Device1"
PROP_IF = "org.freedesktop.DBus.Properties"


class Speaker:
    """Connexion au canal de controle BLE de l'enceinte.

    Ne connait rien de l'UI. Tous les appels se font sur la boucle GLib.
    """

    def __init__(self):
        self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self._chars = {}

    # -- introspection ----------------------------------------------------
    def _managed(self):
        res = self._bus.call_sync(
            BLUEZ, "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects",
            None, GLib.VariantType("(a{oa{sa{sv}}})"),
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
        """Resout l'enceinte par son SERVICE, jamais par une adresse figee
        (l'adresse BLE est privee et tournante)."""
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
            name = (d.get("Name") or "")
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

        deadline = timeout_s * 2
        for _ in range(deadline):
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
        self._chars = {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_speaker -v`
Expected: PASS (ou SKIP si l'enceinte est éteinte — allumer l'enceinte pour valider réellement)

- [ ] **Step 5: Commit**

```bash
git add marshall_ble.py tests/test_speaker.py
git commit -m "feat: decouverte et connexion de l'enceinte par service BLE"
```

---

### Task 4 : Lecture d'état (avec le contournement du registre EQ)

**Files:**
- Modify: `marshall_ble.py`
- Test: `tests/test_speaker.py`

- [ ] **Step 1: Write the failing test**

```python
class TestLecture(SpeakerTestCase):
    def test_etat_complet(self):
        st = self.spk.get_state()
        self.assertIsNotNone(st)
        for k in ("volume", "max_volume", "bass", "treble"):
            self.assertIn(k, st)

    def test_bornes_plausibles(self):
        st = self.spk.get_state()
        self.assertTrue(0 <= st["bass"] <= 10, st)
        self.assertTrue(0 <= st["treble"] <= 10, st)
        self.assertTrue(0 <= st["volume"] <= st["max_volume"], st)

    def test_volume_max_du_firmware(self):
        self.assertEqual(self.spk.get_state()["max_volume"], 31)

    def test_lecture_eq_passe_par_notify(self):
        # ReadValue direct sur l'EQ ne repond pas sur ce firmware ;
        # read_eq doit quand meme renvoyer une valeur.
        self.assertIsNotNone(self.spk.read_eq())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_speaker -v`
Expected: FAIL — `AttributeError: 'Speaker' object has no attribute 'get_state'`

- [ ] **Step 3: Write minimal implementation**

```python
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

    def _read_via_notify(self, path, timeout_ms=9000):
        """Le registre EQ ne repond pas a ReadValue mais pousse sa valeur des
        StartNotify. On s'abonne, on prend la premiere valeur, on se desabonne."""
        loop = GLib.MainLoop()
        box = {}

        def on_signal(_c, _s, _o, _i, _sig, params):
            _iface, changed, _inv = params.unpack()
            if "Value" in changed:
                box["v"] = bytes(changed["Value"])
                loop.quit()

        sub = self._bus.signal_subscribe(
            BLUEZ, PROP_IF, "PropertiesChanged", path, None,
            Gio.DBusSignalFlags.NONE, on_signal)
        try:
            self._bus.call_sync(BLUEZ, path, CHAR_IF, "StartNotify", None, None,
                                Gio.DBusCallFlags.NONE, 6000, None)
        except GLib.Error:
            self._bus.signal_unsubscribe(sub)
            return None

        tid = GLib.timeout_add(timeout_ms, lambda: (loop.quit(), False)[1])
        loop.run()
        GLib.source_remove(tid)
        self._bus.signal_unsubscribe(sub)
        try:
            self._bus.call_sync(BLUEZ, path, CHAR_IF, "StopNotify", None, None,
                                Gio.DBusCallFlags.NONE, 4000, None)
        except GLib.Error:
            pass
        return box.get("v")

    def read_eq(self):
        p = self._path(UUID_EQ)
        if not p:
            return None
        return self._read_direct(p) or self._read_via_notify(p)

    def get_state(self):
        """Lecture synchrone complete.

        ATTENTION : peut lancer une GLib.MainLoop imbriquee (via read_eq).
        Reserve au CLI et a l'initialisation de l'applet -- jamais depuis un
        callback GTK, sous peine de reentrance de la boucle principale.
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
        self._cache.update(state)      # sert de base aux mises a jour partielles
        return state
```

Ajouter dans `__init__` : `self._cache = {}` et `self._callback = None`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_speaker -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add marshall_ble.py tests/test_speaker.py
git commit -m "feat: lecture d'etat avec repli StartNotify pour le registre EQ"
```

---

### Task 5 : Écriture avec préservation de l'autre bande

**Files:**
- Modify: `marshall_ble.py`
- Test: `tests/test_speaker.py`

- [ ] **Step 1: Write the failing test**

```python
class TestEcriture(SpeakerTestCase):
    def setUp(self):
        self.origine = self.spk.get_state()
        self.assertIsNotNone(self.origine)

    def tearDown(self):
        # restauration systematique : on ne laisse jamais les reglages modifies
        self.spk.set_bass(self.origine["bass"])
        self.spk.set_treble(self.origine["treble"])
        self.spk.set_volume(self.origine["volume"])

    def test_set_bass_applique_et_relu(self):
        cible = 2 if self.origine["bass"] > 5 else 9
        self.assertTrue(self.spk.set_bass(cible))
        self.assertEqual(self.spk.get_state()["bass"], cible)

    def test_set_bass_preserve_le_treble(self):
        t0 = self.origine["treble"]
        self.spk.set_bass(2 if self.origine["bass"] > 5 else 9)
        self.assertEqual(self.spk.get_state()["treble"], t0)

    def test_set_treble_preserve_le_bass(self):
        b0 = self.origine["bass"]
        self.spk.set_treble(2 if self.origine["treble"] > 5 else 9)
        self.assertEqual(self.spk.get_state()["bass"], b0)

    def test_volume(self):
        cible = 6 if self.origine["volume"] > 10 else 15
        self.assertTrue(self.spk.set_volume(cible))
        self.assertEqual(self.spk.get_state()["volume"], cible)

    def test_valeur_hors_bornes_est_bornee(self):
        self.spk.set_bass(99)
        self.assertEqual(self.spk.get_state()["bass"], 10)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_speaker -v`
Expected: FAIL — `AttributeError: ... 'set_bass'`

- [ ] **Step 3: Write minimal implementation**

```python
    # -- ecriture ---------------------------------------------------------
    def _write(self, path, payload):
        """WriteValue avec type=request, repli sur command.

        bluetoothctl rend NotSupported sur un payload multi-octets ; l'appel
        D-Bus direct fonctionne.
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
        cur = decode_eq(self.read_eq())
        if cur is None:
            return False
        bass, treble = cur
        if band == "bass":
            bass = value
        else:
            treble = value
        p = self._path(UUID_EQ)
        return bool(p) and self._write(p, encode_eq(bass, treble))

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
        return self._write(p, [clamp(v, 0, top)])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_speaker -v`
Expected: PASS

Puis vérifier de visu que les réglages sont revenus à l'origine :
Run: `python3 -c "import sys; sys.path.insert(0,'.'); from marshall_ble import Speaker; s=Speaker(); s.connect(); print(s.get_state())"`
Expected: `{'volume': 12, 'max_volume': 31, 'bass': 10, 'treble': 7}`

- [ ] **Step 5: Commit**

```bash
git add marshall_ble.py tests/test_speaker.py
git commit -m "feat: ecriture volume/bass/treble avec preservation de l'autre bande"
```

---

### Task 6 : Abonnement permanent et reconnexion avec backoff

**Files:**
- Modify: `marshall_ble.py`

Pas de test automatisé : le comportement dépend d'événements matériels asynchrones. Validé manuellement en Task 11.

- [ ] **Step 1: Implémenter l'abonnement permanent**

```python
    # -- notifications ----------------------------------------------------
    def subscribe(self, callback):
        """Appelle callback(state_dict) a chaque changement signale par
        l'enceinte, y compris quand l'utilisateur tourne une molette physique.

        Toujours invoque sur la boucle principale GLib : l'appelant peut
        toucher l'UI directement.
        """
        self._callback = callback
        for uuid in (UUID_EQ, UUID_VOLUME):
            p = self._path(uuid)
            if not p:
                continue
            self._bus.signal_subscribe(
                BLUEZ, PROP_IF, "PropertiesChanged", p, None,
                Gio.DBusSignalFlags.NONE, self._on_prop_changed)
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
        self._callback(dict(self._cache))
```

(`self._cache` et `self._callback` ont été ajoutés à `__init__` en Task 4.)

- [ ] **Step 2: Implémenter la reconnexion**

```python
    BACKOFF = [1, 2, 5, 10, 30]

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
                return True                      # replanifie au meme intervalle

            if self.connect(timeout_s=20):
                self._attempt = 0
                if self._callback:
                    self.subscribe(self._callback)   # re-abonner apres coupure
                on_change(self.get_state())
                return True

            self._attempt = min(self._attempt + 1, len(self.BACKOFF) - 1)
            GLib.timeout_add_seconds(self.BACKOFF[self._attempt], tick)
            return False                         # celui-ci s'arrete, le nouveau prend

        GLib.timeout_add_seconds(self.BACKOFF[0], tick)
```

Note : `get_state()` est appelé ici depuis un timeout GLib, donc hors callback
GTK d'interaction — la boucle imbriquée y est acceptable. C'est le seul endroit
de l'applet où une lecture synchrone est tolérée.

- [ ] **Step 3: Vérifier qu'aucun test ne régresse**

Run: `python3 -m unittest discover tests -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add marshall_ble.py
git commit -m "feat: notifications permanentes et reconnexion avec backoff"
```

---

### Task 7 : Réécriture du CLI sur le module

**Files:**
- Create: `marshall-ctl` (remplace la version dbus-fast)

- [ ] **Step 1: Écrire le CLI**

```python
#!/usr/bin/env python3
"""marshall-ctl -- volume, bass et treble d'une Marshall Acton III.

    marshall-ctl                  # etat
    marshall-ctl bass 6           # 0..10
    marshall-ctl treble 8         # 0..10
    marshall-ctl volume 20        # 0..31
    marshall-ctl bass 6 treble 8  # combine
    marshall-ctl preset Musique
"""
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.local/share/marshall"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from marshall_ble import PRESETS, Speaker, BASS_MAX, TREBLE_MAX


def main(argv):
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    spk = Speaker()
    if not spk.connect():
        print("Enceinte introuvable. Allumee ? Identite BLE appairee ?",
              file=sys.stderr)
        return 1

    if argv and argv[0] == "preset":
        if len(argv) < 2:
            print("Presets : " + ", ".join(PRESETS), file=sys.stderr)
            return 2
        name = " ".join(argv[1:])
        if name not in PRESETS:
            print(f"Preset inconnu : {name}\nDisponibles : {', '.join(PRESETS)}",
                  file=sys.stderr)
            return 2
        spk.set_bass(PRESETS[name]["bass"])
        spk.set_treble(PRESETS[name]["treble"])
    else:
        for i in range(0, len(argv) - 1, 2):
            key, raw = argv[i].lower(), argv[i + 1]
            if key not in ("bass", "treble", "volume"):
                print(f"Reglage inconnu : {key}", file=sys.stderr)
                return 2
            try:
                val = int(raw)
            except ValueError:
                print(f"Valeur non numerique pour {key} : {raw}", file=sys.stderr)
                return 2
            getattr(spk, f"set_{key}")(val)

    st = spk.get_state()
    if not st:
        print("Enceinte connectee mais muette. Reessayer.", file=sys.stderr)
        return 1
    print(f"volume : {st['volume']}/{st['max_volume']}")
    print(f"bass   : {st['bass']}/{BASS_MAX}")
    print(f"treble : {st['treble']}/{TREBLE_MAX}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 2: Vérifier la parité fonctionnelle avec l'ancienne version**

```bash
chmod +x marshall-ctl
./marshall-ctl                      # attendu : volume 12/31, bass 10/10, treble 7/10
./marshall-ctl bass 4 treble 9      # attendu : bass 4/10, treble 9/10
./marshall-ctl preset Musique       # attendu : retour a bass 10, treble 7
./marshall-ctl bass 99              # attendu : borne a 10
./marshall-ctl pouet 3              # attendu : erreur claire, code 2
```

Expected: chaque commande renvoie l'état réel relu depuis l'enceinte.

- [ ] **Step 3: Commit**

```bash
git add marshall-ctl
git commit -m "refactor: CLI reecrit sur le module partage, sort de dbus-fast"
```

---

### Task 8 : Applet — indicateur, menu, instance unique

**Files:**
- Create: `marshall-applet`

- [ ] **Step 1: Écrire le squelette de l'applet**

```python
#!/usr/bin/env python3
"""marshall-applet -- pilote une Marshall Acton III depuis la barre GNOME."""
import os
import sys

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import Gtk, Gio, GLib
from gi.repository import AyatanaAppIndicator3 as AppIndicator

sys.path.insert(0, os.path.expanduser("~/.local/share/marshall"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from marshall_ble import PRESETS, Speaker, match_preset, BASS_MAX, TREBLE_MAX

APP_ID = "org.aghiles.MarshallApplet"
ICON_OK = "audio-speakers"
ICON_OFF = "audio-volume-muted"

DEBOUNCE_MS = 150


class Applet(Gtk.Application):
    """Gtk.Application fournit l'unicite d'instance via D-Bus : l'autostart
    plus un lancement manuel ne peuvent pas creer deux clients BLE."""

    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.spk = Speaker()
        self.state = None
        self.win = None
        self.ind = None
        self._pending = {}       # reglages en attente d'envoi (debounce)
        self._timer = None       # id du timeout de debounce en cours

    def do_activate(self):
        if self.ind is None:
            self.hold()                      # pas de fenetre => ne pas quitter
            self._build_indicator()
            GLib.idle_add(self._initial_connect)
        else:
            self.on_settings(None)           # 2e lancement : montrer la fenetre

    def _build_indicator(self):
        self.ind = AppIndicator.Indicator.new(
            APP_ID, ICON_OFF, AppIndicator.IndicatorCategory.HARDWARE)
        self.ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.ind.set_title("Marshall Acton III")
        self._rebuild_menu()

    def _initial_connect(self):
        if self.spk.connect(timeout_s=30):
            self.state = self.spk.get_state()
            self.spk.subscribe(self.on_external_change)
        self.spk.start_watchdog(self.on_external_change)
        self._refresh()
        return False

    def on_external_change(self, state):
        """Molette physique tournee, ou reconnexion. Annule un envoi en
        attente : l'utilisateur vient d'agir sur l'enceinte elle-meme."""
        self._cancel_pending()
        self.state = state
        self._refresh()

    def _refresh(self):
        connected = self.spk.is_connected() and self.state
        self.ind.set_icon_full(ICON_OK if connected else ICON_OFF,
                               "Marshall Acton III")
        self._rebuild_menu()
        if self.win:
            self.win.update(self.state, connected)
```

- [ ] **Step 2: Construire le menu**

```python
    def _rebuild_menu(self):
        m = Gtk.Menu()
        connected = self.spk.is_connected() and self.state

        head = Gtk.MenuItem(label="Acton III   "
                            + ("● connectée" if connected else "○ déconnectée"))
        head.set_sensitive(False)
        m.append(head)

        if connected:
            s = self.state
            vals = Gtk.MenuItem(
                label=f"vol {s['volume']} · bass {s['bass']} · tr {s['treble']}")
            vals.set_sensitive(False)
            m.append(vals)

        m.append(Gtk.SeparatorMenuItem())

        active = match_preset(self.state["bass"], self.state["treble"]) if connected else None
        group = None
        for name in PRESETS:
            it = Gtk.RadioMenuItem(label=name, group=group)
            group = group or it
            it.set_active(name == active)
            it.set_sensitive(bool(connected))
            it.connect("toggled", self.on_preset, name)
            m.append(it)

        m.append(Gtk.SeparatorMenuItem())

        settings = Gtk.MenuItem(label="Réglages…")
        settings.connect("activate", self.on_settings)
        settings.set_sensitive(bool(connected))
        m.append(settings)

        if not connected:
            rec = Gtk.MenuItem(label="Reconnecter")
            rec.connect("activate", lambda _w: GLib.idle_add(self._initial_connect))
            m.append(rec)

        quit_it = Gtk.MenuItem(label="Quitter")
        quit_it.connect("activate", lambda _w: self.quit())
        m.append(quit_it)

        m.show_all()
        self.ind.set_menu(m)
        self._menu = m          # garder une reference, sinon GC

    def on_preset(self, widget, name):
        if not widget.get_active():
            return
        p = PRESETS[name]
        if (self.state and p["bass"] == self.state["bass"]
                and p["treble"] == self.state["treble"]):
            return                       # deja applique, ne pas re-ecrire
        self.apply_preset(name)

    # -- ecriture ---------------------------------------------------------
    # Task 9 ajoutera par-dessus schedule_write/_flush (le debounce des sliders).

    def _cancel_pending(self):
        """Annule un envoi de slider en attente. Un preset, ou un changement
        venu de l'enceinte, doit gagner sur un debounce encore en vol."""
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None
        self._pending.clear()

    def _apply(self, values):
        """Ecrit puis met a jour l'etat AVEC LES VALEURS ECRITES.

        Pas de get_state() ici : on est dans un callback GTK, et get_state()
        lance une boucle GLib imbriquee -> reentrance. Ce n'est pas de
        l'optimisme aveugle : WriteValue est emis en type=request, un
        write-with-response que l'enceinte acquitte. Un retour True signifie
        qu'elle a accepte la valeur ; un echec laisse l'affichage inchange.
        """
        for key, value in values.items():
            if getattr(self.spk, f"set_{key}")(value) and self.state:
                self.state[key] = value
        self._refresh()

    def apply_preset(self, name):
        self._cancel_pending()
        p = PRESETS[name]
        self._apply({"bass": p["bass"], "treble": p["treble"]})
```

- [ ] **Step 3: Vérifier que l'applet démarre et que le menu s'affiche**

Run: `chmod +x marshall-applet && ./marshall-applet`
Expected: une icône apparaît dans la barre GNOME ; le menu montre l'état et les 4 presets ; « Musique » est cochée si bass=10/treble=7.

Vérifier l'unicité : relancer `./marshall-applet` dans un autre terminal.
Expected: aucune seconde icône.

- [ ] **Step 4: Commit**

```bash
git add marshall-applet
git commit -m "feat: applet de barre systeme avec menu, presets et instance unique"
```

---

### Task 9 : Fenêtre de réglages avec sliders et debounce

**Files:**
- Modify: `marshall-applet`

- [ ] **Step 1: Ajouter la fenêtre**

```python
class SettingsWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(title="Marshall Acton III")
        self.app = app
        self.set_default_size(360, 200)
        self.set_border_width(16)
        self._loading = False        # True pendant une mise a jour programmee

        grid = Gtk.Grid(row_spacing=10, column_spacing=12)
        self.add(grid)

        self.scales = {}
        for row, (key, top) in enumerate(
                (("volume", 31), ("bass", BASS_MAX), ("treble", TREBLE_MAX))):
            lbl = Gtk.Label(label=key.capitalize(), xalign=0)
            sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, top, 1)
            sc.set_hexpand(True)
            sc.set_digits(0)
            sc.set_value_pos(Gtk.PositionType.RIGHT)
            for t in range(0, top + 1, max(1, top // 5)):
                sc.add_mark(t, Gtk.PositionType.BOTTOM, None)
            sc.connect("value-changed", self.on_scale, key)
            grid.attach(lbl, 0, row, 1, 1)
            grid.attach(sc, 1, row, 1, 1)
            self.scales[key] = sc

        box = Gtk.Box(spacing=6)
        for name in PRESETS:
            b = Gtk.Button(label=name)
            b.connect("clicked", lambda _w, n=name: self.app.apply_preset(n))
            box.add(b)
        grid.attach(box, 0, 3, 2, 1)

        self.status = Gtk.Label(label="", xalign=0)
        grid.attach(self.status, 0, 4, 2, 1)

        self.connect("delete-event", self.on_close)

    def on_close(self, *_a):
        self.hide()
        return True            # cacher, pas detruire : l'applet continue

    def update(self, state, connected):
        """Mise a jour programmee : ne doit PAS declencher d'ecriture."""
        self._loading = True
        try:
            if state:
                for k, sc in self.scales.items():
                    if sc.get_value() != state[k]:
                        sc.set_value(state[k])
            self.status.set_text("● connectée" if connected else "○ déconnectée")
            for sc in self.scales.values():
                sc.set_sensitive(bool(connected))
        finally:
            self._loading = False

    def on_scale(self, scale, key):
        if self._loading:
            return
        self.app.schedule_write(key, int(scale.get_value()))
```

- [ ] **Step 2: Ajouter le debounce et l'ouverture de la fenêtre**

`_cancel_pending`, `_apply` et `apply_preset` existent déjà depuis Task 8. Il ne
reste que le debounce des sliders et l'ouverture de la fenêtre :

```python
    def schedule_write(self, key, value):
        """Un glissement de slider emet des dizaines d'evenements ; sur un canal
        a 1-2 s par commande il faut n'en envoyer qu'un, apres l'arret du geste.

        Chaque nouveau mouvement repousse l'echeance : l'envoi ne part que
        DEBOUNCE_MS apres le dernier deplacement.
        """
        self._pending[key] = value
        if self._timer is not None:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(DEBOUNCE_MS, self._flush)

    def _flush(self):
        self._timer = None
        pending, self._pending = dict(self._pending), {}
        self._apply(pending)
        return False

    def on_settings(self, _w):
        if self.win is None:
            self.win = SettingsWindow(self)
        self.win.update(self.state, self.spk.is_connected() and bool(self.state))
        self.win.show_all()
        self.win.present()


if __name__ == "__main__":
    Applet().run(sys.argv)
```

- [ ] **Step 3: Vérifier le comportement du debounce**

Run: `./marshall-applet` puis ouvrir « Réglages… » et faire glisser le slider Bass d'un bout à l'autre.
Expected: **une seule** écriture part à la fin du geste ; le slider ne « saute » pas pendant le glissement ; la valeur finale correspond à la position relâchée.

Vérifier la priorité du preset : commencer un glissement puis cliquer immédiatement un preset.
Expected: c'est le preset qui est appliqué, pas la valeur du slider.

Vérifier le reflet des molettes physiques : tourner la molette Bass sur l'enceinte.
Expected: le slider suit sans déclencher d'écriture en retour.

- [ ] **Step 4: Commit**

```bash
git add marshall-applet
git commit -m "feat: fenetre de reglages avec sliders et debounce a 150 ms"
```

---

### Task 10 : Installation et autostart

**Files:**
- Create: `install.sh`

- [ ] **Step 1: Écrire l'installeur**

```bash
#!/usr/bin/env bash
# Installe marshall-applet et marshall-ctl par LIENS SYMBOLIQUES : une modif
# du depot est prise en compte sans reinstaller.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODDIR="$HOME/.local/share/marshall"
BIN="$HOME/bin"
AUTOSTART="$HOME/.config/autostart"

mkdir -p "$MODDIR" "$BIN" "$AUTOSTART"

ln -sf "$SRC/marshall_ble.py" "$MODDIR/marshall_ble.py"
ln -sf "$SRC/marshall-applet" "$BIN/marshall-applet"
ln -sf "$SRC/marshall-ctl"    "$BIN/marshall-ctl"
chmod +x "$SRC/marshall-applet" "$SRC/marshall-ctl"

cat > "$AUTOSTART/marshall-applet.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Marshall Acton III
Comment=Reglage du volume, du bass et du treble de l'enceinte
Exec=$BIN/marshall-applet
Icon=audio-speakers
Terminal=false
Categories=AudioVideo;
X-GNOME-Autostart-enabled=true
EOF

echo "Installe."
echo "  applet   : $BIN/marshall-applet"
echo "  cli      : $BIN/marshall-ctl"
echo "  module   : $MODDIR/marshall_ble.py"
echo "  autostart: $AUTOSTART/marshall-applet.desktop"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo
     echo "ATTENTION : $BIN n'est pas dans le PATH."
     echo "  echo 'export PATH=\"\$HOME/bin:\$PATH\"' >> ~/.zshrc" ;;
esac
```

- [ ] **Step 2: Installer et retirer l'ancien venv dbus-fast**

```bash
chmod +x install.sh && ./install.sh
rm -rf ~/.local/share/marshall-ctl        # ancien venv dbus-fast, plus utilise
```

Expected: les liens existent, `marshall-ctl` fonctionne toujours, et plus aucune dépendance hors PyGObject.

Vérifier : `python3 -c "import gi" && ~/bin/marshall-ctl`
Expected: l'état s'affiche.

- [ ] **Step 3: Commit**

```bash
git add install.sh
git commit -m "feat: installeur par liens symboliques et entree d'autostart"
```

---

### Task 11 : Validation manuelle de bout en bout

**Files:** aucun

- [ ] **Step 1: Dérouler la checklist**

| Vérification | Attendu |
|---|---|
| `python3 -m unittest discover tests -v` | tout passe |
| Chaque preset depuis le menu | bass/treble appliqués, preset coché |
| Glissement de slider | une seule écriture, pas de saut |
| Preset cliqué pendant un debounce | le preset gagne |
| Molette physique tournée | l'UI suit, aucune écriture en retour |
| Enceinte éteinte | icône grisée, « Reconnecter » apparaît |
| Enceinte rallumée sans rien relancer | reprise automatique sous ~30 s |
| Deuxième lancement de l'applet | pas de seconde icône, la fenêtre s'affiche |
| Fermeture de la fenêtre | l'applet reste dans la barre |
| Déconnexion/reconnexion de session | l'applet redémarre seul |
| État final | **bass=10, treble=7, volume=12** |

- [ ] **Step 2: Remettre les réglages d'origine**

```bash
~/bin/marshall-ctl preset Musique && ~/bin/marshall-ctl volume 12
```
Expected: `volume 12/31, bass 10/10, treble 7/10`

- [ ] **Step 3: Commit final**

```bash
git add -A
git commit -m "docs: validation manuelle de bout en bout"
```

---

## Points de vigilance

- **Ne jamais** faire de `bluetoothctl remove` sur `74:68:59:6F:AD:B1` : c'est l'appairage audio, le son serait à réappairer.
- Après chaque test d'écriture, **restaurer** bass=10 / treble=7 / volume=12.
- Si l'EQ devient muet, l'enceinte s'est endormie (10 min d'inactivité) : la reconnexion doit s'en charger, sinon c'est un bug du watchdog.
- Garder une référence Python sur le `Gtk.Menu` passé à l'indicateur, sinon le ramasse-miettes le fait disparaître.
