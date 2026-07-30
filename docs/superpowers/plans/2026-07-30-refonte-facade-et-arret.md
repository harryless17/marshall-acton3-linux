# Façade dessinée et arrêt de l'applet — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rendre l'applet arrêtable depuis la fenêtre, et remplacer ses trois sliders GTK par une façade d'amplificateur Marshall dessinée en Cairo avec des molettes rotatives.

**Architecture:** Toute l'interface part dans un nouveau module `marshall_ui.py` qui ne connaît ni BlueZ ni `Speaker` : il expose des widgets et des signaux, et `marshall-applet` les relie à l'enceinte. L'arithmétique des molettes vit dans une classe `KnobModel` sans aucun GTK, testable sans écran. Chaque unité de dessin peint son propre rectangle, sans système de coordonnées partagé.

**Tech Stack:** Python 3.9+, PyGObject (Gtk 3.0, Gdk, GLib, Gio), pycairo, PangoCairo. Aucune dépendance PyPI, aucun environnement virtuel. Tests en `unittest`.

**Spec :** [`docs/superpowers/specs/2026-07-30-refonte-facade-et-arret-design.md`](../specs/2026-07-30-refonte-facade-et-arret-design.md)

---

## À lire avant de commencer

1. **La règle d'architecture du projet**, en tête de `marshall-applet` : *aucun appel BLE depuis un handler GTK*. Elle s'applique aux nouveaux widgets. Une `Knob` n'appelle **jamais** le transport — elle émet un signal, et le debounce existant écrit.
2. **Le tableau « Ce qui est réutilisé sans y toucher »** de la spec. Ces comportements ont été acquis contre le matériel. On les recâble, on ne les réécrit pas.
3. **Convention d'écriture du dépôt** : commentaires et docstrings en français **sans accents** (voir n'importe quel fichier `.py`) ; documentation Markdown **avec** accents. Noms de tests en français.
4. **@superpowers:test-driven-development** — le test échoue d'abord, toujours.
5. **@superpowers:verification-before-completion** — aucune affirmation de succès sans la sortie de commande sous les yeux.
6. **Pour tout correctif de correction, muter avant de conclure.** Leçon payée sur la tâche 1 : les quatre tests étaient rouges avant le correctif, ce qui semblait suffire — mais ils l'étaient parce qu'un attribut n'existait pas encore, pas parce qu'un timer fuyait. Une fois l'attribut posé, on pouvait vider entièrement le corps de `stop_watchdog()` en laissant le vrai timer armé, et **les quatre tests restaient verts**. « Rouge avant, vert après » ne prouve pas qu'on garde le bon comportement. La seule preuve est de casser volontairement le correctif et de vérifier qu'un test tombe :

   ```bash
   # 1. neutraliser le coeur du correctif dans le code
   # 2. lancer la suite -- un test DOIT echouer, et il doit etre celui qu'on croit
   # 3. restaurer, relancer, tout doit repasser
   ```

   À faire pour les tâches **1, 2, 4** (correctifs de comportement). Inutile pour les tâches 5 à 11, qui ajoutent du code neuf plutôt que d'en corriger.

### Commandes de test

```bash
# la suite complete
python3 -m unittest discover -s tests -t . -v

# un fichier
python3 -m unittest tests.test_knob_model -v

# un cas
python3 -m unittest tests.test_knob_model.TestGlisseRelatif.test_deux_glisses_partent_de_lorigine -v
```

> ⚠️ `tests/test_speaker.py` demande l'enceinte allumée et **monopolise le canal BLE**. Arrête l'applet avant les tests d'intégration :
> ```bash
> pkill -f "bin/marshall-appl[e]t"
> ```

## Structure des fichiers

| Fichier | Responsabilité | Tâches |
|---|---|---|
| `marshall_ble.py` | protocole et transport BLE. **Modifié** : `stop_watchdog()`, source du watchdog mémorisée, délais de garde d'arrêt bornés | 1, 2 |
| `marshall_ui.py` | **nouveau.** `KnobModel`, les fonctions `paint_*`, `Knob`, `BrassPanel`, `Grille`, `Facade`, la feuille CSS. Ne connaît ni BlueZ ni `Speaker` | 5–9 |
| `marshall-applet` | icône de barre, orchestration BLE, autostart, câblage de la fenêtre. **Allégé** de toute l'interface | 3, 4, 10 |
| `marshall-ctl` | CLI | inchangé |
| `install.sh` | installation | 3 (commentaire seulement) |
| `tests/test_knob_model.py` | **nouveau.** L'arithmétique des molettes, sans écran | 5 |
| `tests/test_paint.py` | **nouveau.** Fumée sur les fonctions de peinture, sans écran | 6 |
| `tests/test_knob_widget.py` | **nouveau.** Le widget `Knob`, sauté sans afficheur | 7 |
| `tests/test_applet.py` | autostart, séquence d'arrêt | 3, 4 |
| `tests/test_speaker_faux_bus.py` | source du watchdog, délais d'arrêt | 1, 2 |
| `README.md`, la spec | documentation | 11 |

**Écart assumé par rapport à la spec :** elle plaçait les tests de `KnobModel` dans `test_pure.py`. Ce fichier est explicitement *« tests des fonctions pures du protocole »* et importe `marshall_ble` ; `KnobModel` n'est pas du protocole. Il va donc dans son propre fichier, ce qui est plus fidèle à l'organisation existante.

## Ordre des tâches, et point de livraison

Les tâches **1 à 4** règlent le besoin n°1 — pouvoir arrêter l'applet — et ne touchent pas au dessin. **À la fin de la tâche 4, l'applet est utilisable et le problème rapporté est résolu**, avec les sliders actuels. C'est un point d'arrêt légitime si la suite traîne.

Les tâches **5 à 10** construisent la façade. La tâche **11** documente et vérifie sur la machine réelle.

---

## Tâche 1 : le watchdog laisse une source GLib derrière lui

`close()` remet `_watchdog_on` à `False` mais ne retire pas le timer déjà planifié, et `_tick_inner` ne consulte jamais ce drapeau. Le drapeau est inerte : `close()` puis `start_watchdog()` créent deux chaînes concurrentes. La séquence d'arrêt de la tâche 3 en dépend.

**Files:**
- Modify: `marshall_ble.py:88-95` (init), `marshall_ble.py:211-224` (`close`), `marshall_ble.py:488-540` (watchdog)
- Test: `tests/test_speaker_faux_bus.py:370-395` (classe `TestWatchdog`)

- [ ] **Step 1 : écrire les tests qui échouent**

Ajouter dans `tests/test_speaker_faux_bus.py`, dans la classe `TestWatchdog` :

```python
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
        """L'ancienne implementation appelait _tick_inner meme apres close() :
        le cycle repartait, et pouvait rouvrir la connexion pendant qu'on la
        fermait. Espionner _tick_inner est le seul moyen de le voir -- se fier
        a la valeur de retour de _tick ne discrimine pas, tous les chemins
        rendent False."""
        s = faire_speaker(bus_nominal())
        s.start_watchdog(lambda _st: None)
        s.close()
        entres = []
        s._tick_inner = lambda: entres.append(1) or False
        self.assertFalse(s._tick())
        self.assertEqual(entres, [], "le cycle est reparti apres close()")
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
```

- [ ] **Step 2 : vérifier qu'ils échouent — les quatre**

Run : `python3 -m unittest tests.test_speaker_faux_bus.TestWatchdog -v`
Expected : FAIL — `AttributeError: 'Speaker' object has no attribute '_watchdog_source'`, et `no attribute 'stop_watchdog'`.

⚠️ Vérifier que **chacun des quatre** échoue, pas seulement que la classe échoue. Un test qui passe déjà sur l'implémentation fautive ne garde rien : ses assertions y sont vraies par vacuité. C'est précisément le piège dans lequel la première rédaction de `test_un_cycle_ne_replanifie_pas_apres_close` était tombée — `assertIsNone(s._watchdog_source)` est trivialement vrai quand l'attribut n'est jamais posé, et tous les chemins de `_tick` rendent `False`.

- [ ] **Step 3 : mémoriser la source et ajouter `stop_watchdog()`**

Dans `__init__`, à côté de `self._watchdog_on = False` :

```python
        self._watchdog_source = None   # id du timer en cours, cf. stop_watchdog
```

Remplacer `start_watchdog` / `_tick` / `_tick_inner` par :

```python
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
        self._reschedule(self.BACKOFF[0])

    def _reschedule(self, delay_s):
        """Un seul endroit ou une source est creee, pour que _watchdog_source
        soit toujours l'id du timer reellement en attente."""
        if not self._watchdog_on:
            return          # coupe pendant le cycle : ne pas ressusciter la chaine
        self._watchdog_source = GLib.timeout_add_seconds(delay_s, self._tick)

    def stop_watchdog(self):
        """Coupe la chaine de timers, et rien d'autre.

        Separe de close() parce que la sequence d'arret a besoin de couper le
        watchdog AVANT d'appeler disconnect(), qui lui a encore besoin de
        _dev_path -- que close() effacerait.
        """
        self._watchdog_on = False
        if self._watchdog_source is not None:
            GLib.source_remove(self._watchdog_source)
            self._watchdog_source = None

    def _tick(self):
        """Un cycle de surveillance. Ne doit JAMAIS laisser filer d'exception :
        PyGObject retire la source quand un callback leve, ce qui tuait la
        reconnexion pour le reste de la session -- et sans trace, la sortie
        d'erreur allant dans le vide."""
        # La source qui nous appelle s'acheve en rendant False : on l'oublie
        # avant tout. Sinon stop_watchdog retirerait un id mort -- et GLib
        # RECYCLE les ids, donc ce retrait pourrait tuer en silence une source
        # sans rapport. C'est ici que se joue la protection, pas dans un
        # try/except autour de source_remove : source_remove ne leve pas, il
        # rend False en avertissant.
        self._watchdog_source = None
        if not self._watchdog_on:
            return False             # coupe pendant l'attente : on s'arrete la
        try:
            return self._tick_inner()
        except Exception:
            log.exception("watchdog: cycle en echec, on replanifie")
            self._reschedule(POLL_INTERVAL_S)
            return False
```

Dans `_tick_inner`, remplacer les trois `GLib.timeout_add_seconds(..., self._tick)` par `self._reschedule(...)` :

```python
            self._reschedule(POLL_INTERVAL_S)      # les deux premiers cas
            ...
        self._attempt = min(self._attempt + 1, len(self.BACKOFF) - 1)
        self._reschedule(self.BACKOFF[self._attempt])
        return False
```

Dans `close()`, remplacer `self._watchdog_on = False` par :

```python
        self.stop_watchdog()
```

- [ ] **Step 4 : vérifier que les tests passent**

Run : `python3 -m unittest tests.test_speaker_faux_bus -v`
Expected : PASS, y compris les tests existants `test_le_cycle_survit_a_une_erreur_dbus`, `test_start_watchdog_est_idempotent` et `test_lien_up_mais_etat_inconnu_est_resolu`.

- [ ] **Step 5 : commit**

```bash
git add marshall_ble.py tests/test_speaker_faux_bus.py
git commit -m "fix: le watchdog laissait une source GLib apres close()"
```

---

## Tâche 2 : borner les délais de garde du chemin d'arrêt

`disconnect()` utilise 4 s, 4 s et 8 s. Jusqu'à ~16 s d'attente pendant l'arrêt.

**Files:**
- Modify: `marshall_ble.py:546-566` (`disconnect`), plus deux constantes en tête de fichier
- Test: `tests/test_speaker_faux_bus.py` (classe `TestFermeture`)

- [ ] **Step 1 : écrire le test qui échoue**

Dans `tests/test_speaker_faux_bus.py`, le `FauxBus` ignore le timeout. Il faut d'abord qu'il l'enregistre. Modifier `call_sync` pour tracer le délai :

```python
    def call_sync(self, _dest, chemin, iface, methode, params,
                  _rtype, _flags, timeout, _cancellable):
        self.appels.append((methode, chemin))
        self.delais.append((methode, timeout))
```

et dans `FauxBus.__init__` : `self.delais = []          # (methode, timeout_ms)`

Puis, dans `TestFermeture` :

```python
    def test_larret_est_borne_a_trois_secondes(self):
        """Une icone qui reste 16 s dans la barre se lit comme "ca ne quitte
        pas". Le cumul du pire cas doit tenir sous 3 s."""
        bus = bus_nominal()
        s = faire_speaker(bus)
        s.connect(timeout_s=1)
        bus.delais.clear()
        s.disconnect()
        pire = sum(t for meth, t in bus.delais
                   if meth in ("StopNotify", "Disconnect"))
        self.assertLessEqual(pire, 3000, f"pire cas d'arret : {pire} ms")

    def test_disconnect_survit_a_un_bluez_muet(self):
        bus = bus_nominal()
        bus.lever_sur.update({"StopNotify", "Disconnect"})
        s = faire_speaker(bus)
        s.connect(timeout_s=1)
        s.disconnect()          # ne doit pas propager
```

- [ ] **Step 2 : vérifier qu'il échoue**

Run : `python3 -m unittest tests.test_speaker_faux_bus.TestFermeture -v`
Expected : FAIL — `pire cas d'arret : 16000 ms`.

- [ ] **Step 3 : borner les délais**

En tête de `marshall_ble.py`, près des autres constantes de délai :

```python
# Delais du chemin d'ARRET, volontairement courts. Pendant tout ce temps,
# l'utilisateur a deja clique "Quitter" ; il ne doit pas attendre BlueZ.
# Pire cas cumule : 700 + 700 + 1500 = 2900 ms.
STOP_NOTIFY_TIMEOUT_MS = 700
STOP_DISCONNECT_TIMEOUT_MS = 1500
```

Puis dans `disconnect()`, remplacer les deux littéraux `4000` et `8000` par ces constantes.

- [ ] **Step 4 : vérifier que les tests passent**

Run : `python3 -m unittest tests.test_speaker_faux_bus -v`
Expected : PASS.

- [ ] **Step 5 : commit**

```bash
git add marshall_ble.py tests/test_speaker_faux_bus.py
git commit -m "fix: plafonner l'arret a ~3 s au lieu de ~16 s"
```

---

## Tâche 3 : l'autostart, posé et retiré depuis Python

**Files:**
- Modify: `marshall-applet` (fonctions au niveau module, après `_setup_log`)
- Modify: `install.sh:22-36` (commentaire de duplication)
- Test: `tests/test_applet.py` (nouvelle classe)

- [ ] **Step 1 : écrire les tests qui échouent**

Ajouter dans `tests/test_applet.py`, en tête : `import tempfile`.

Puis une nouvelle classe :

```python
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
```

- [ ] **Step 2 : vérifier qu'ils échouent**

Run : `python3 -m unittest tests.test_applet.TestAutostart -v`
Expected : FAIL — `AttributeError: module 'applet_sous_test' has no attribute 'autostart_enabled'`.

- [ ] **Step 3 : implémenter les trois fonctions**

Dans `marshall-applet`, après `log = _setup_log()` :

```python
# -- autostart ------------------------------------------------------------
# Le meme fichier que celui pose par install.sh. Son CONTENU est donc duplique
# entre le shell et ici : six lignes d'ini, duplication assumee et signalee des
# deux cotes. Si tu touches a l'un, touche a l'autre.
AUTOSTART_DESKTOP = """[Desktop Entry]
Type=Application
Name=Marshall Acton III
GenericName=Egaliseur d'enceinte
Comment=Reglage du volume, du bass et du treble de l'enceinte Marshall
Exec={exec}
Icon=audio-speakers
Terminal=false
Categories=AudioVideo;Audio;Mixer;
Keywords=marshall;acton;enceinte;speaker;bass;basses;treble;aigus;egaliseur;equalizer;volume;son;audio;bluetooth;
StartupNotify=false
X-GNOME-Autostart-enabled=true
"""


def applet_exec_path():
    """Chemin a mettre dans Exec=.

    Le lien pose par install.sh d'abord : c'est lui qui survit a un
    deplacement du depot. Repli sur le script reel pour un lancement direct
    depuis le depot, sans installation.
    """
    lien = os.path.expanduser("~/bin/marshall-applet")
    return lien if os.path.exists(lien) else os.path.realpath(__file__)


def autostart_path():
    """Lu a chaque appel, et non mis en cache : les tests deplacent
    XDG_CONFIG_HOME, et le vrai ~/.config ne doit pas etre touche."""
    base = os.environ.get("XDG_CONFIG_HOME",
                          os.path.expanduser("~/.config"))
    return os.path.join(base, "autostart", "marshall-applet.desktop")


def autostart_enabled():
    """La presence du fichier EST l'etat. Pas de memorisation en parallele :
    il peut avoir ete retire a la main."""
    return os.path.exists(autostart_path())


def set_autostart(actif):
    """Pose ou retire le fichier d'autostart.

    Retirer, plutot que poser X-GNOME-Autostart-enabled=false : c'est deja ce
    que le README documente pour la desinstallation, et ca evite deux
    representations du meme etat.
    """
    chemin = autostart_path()
    if actif:
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        with open(chemin, "w") as f:
            f.write(AUTOSTART_DESKTOP.format(exec=applet_exec_path()))
    else:
        try:
            os.remove(chemin)
        except FileNotFoundError:
            pass
```

- [ ] **Step 4 : vérifier que les tests passent**

Run : `python3 -m unittest tests.test_applet -v`
Expected : PASS.

Vérifier aussi qu'aucun fichier n'a été écrit dans le vrai `~/.config` :

Run : `ls -l ~/.config/autostart/marshall-applet.desktop`
Expected : le fichier existe encore, avec sa **date d'origine** — les tests ne l'ont pas réécrit.

- [ ] **Step 5 : signaler la duplication dans `install.sh`**

Au-dessus de `write_desktop()`, dans `install.sh` :

```bash
# ATTENTION : ce contenu est duplique dans AUTOSTART_DESKTOP, en tete de
# marshall-applet -- l'interrupteur "Demarrer avec la session" de la fenetre
# reecrit ce meme fichier. Si tu touches a l'un, touche a l'autre.
```

- [ ] **Step 6 : commit**

```bash
git add marshall-applet install.sh tests/test_applet.py
git commit -m "feat: activer et desactiver l'autostart depuis Python"
```

---

## Tâche 4 : la fenêtre devient autosuffisante — Quitter, Reconnecter, autostart

C'est la tâche qui règle le problème rapporté. Elle porte encore sur `SettingsWindow` et ses sliders ; la façade viendra ensuite les remplacer.

**Files:**
- Modify: `marshall-applet:90-183` (`SettingsWindow`), `marshall-applet:255-270` (`do_shutdown`), `marshall-applet:374-376` (entrée de menu)
- Test: `tests/test_applet.py` (nouvelle classe)

- [ ] **Step 1 : écrire les tests qui échouent**

Ajouter au `FauxSpeaker` de `tests/test_applet.py` la trace de l'ordre des appels :

```python
    def __init__(self, etat=None, connecte=True):
        ...
        self.ordre = []              # sequence des appels, pour l'arret
```

et, dans chaque méthode concernée, une ligne en tête :

```python
    def disconnect(self):
        self.ordre.append("disconnect")
        self.deconnexions += 1

    def close(self):
        self.ordre.append("close")

    def stop_watchdog(self):
        self.ordre.append("stop_watchdog")
```

Puis la nouvelle classe :

```python
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
    """Le bug percu : jusqu'a ~16 s entre le clic sur Quitter et la
    disparition de l'icone, parce que do_shutdown parlait a BlueZ d'abord."""

    def faire_applet_arretable(self):
        spk = FauxSpeaker()
        app = self.faire_applet(spk)
        app.icon = FausseIcone(spk.ordre)
        app._quits = 0
        app.quit = lambda: setattr(app, "_quits", app._quits + 1)
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
```

- [ ] **Step 2 : vérifier qu'ils échouent**

Run : `python3 -m unittest tests.test_applet.TestSequenceDarret -v`
Expected : FAIL — `AttributeError: 'Applet' object has no attribute 'on_quit'`.

- [ ] **Step 3 : implémenter `on_quit` et réordonner `do_shutdown`**

Dans `Applet`, section « cycle de vie » :

```python
    def on_quit(self, *_a):
        """Arret demande par l'utilisateur.

        L'interface part AVANT la liberation BLE. do_shutdown parle a BlueZ,
        et meme borne a ~3 s c'est assez long pour qu'une icone toujours
        visible se lise comme "ca ne quitte pas" -- symptome rapporte.

        quit() ignore le compteur de hold() : le hold() pose par do_activate
        n'empeche donc pas la sortie de boucle.
        """
        if self.icon is not None:
            self.icon.set_visible(False)
        if self.win is not None:
            self.win.destroy()
            self.win = None
        self.quit()
```

Remplacer `do_shutdown` :

```python
    def do_shutdown(self):
        """Envoie ce qui attend, puis libere le canal BLE.

        Sans le Disconnect, BlueZ gardait le lien ouvert apres "Quitter" : le
        canal restait monopolise alors que l'utilisateur venait de fermer.

        Le watchdog est coupe EN PREMIER : un cycle qui se declencherait ici
        rouvrirait la connexion qu'on est en train de fermer. stop_watchdog()
        et non close(), qui effacerait le _dev_path dont disconnect a besoin.
        """
        try:
            self.spk.stop_watchdog()
            if self._pending:
                pending, self._pending = dict(self._pending), {}
                for key, value in pending.items():
                    getattr(self.spk, f"set_{key}")(value)
            self.spk.disconnect()
            self.spk.close()
        except Exception:
            log.exception("arret : liberation incomplete")
        Gtk.Application.do_shutdown(self)
```

Rebrancher l'entrée de menu sur le même chemin, dans `_rebuild_menu` :

```python
        quit_it = Gtk.MenuItem(label="Quitter")
        quit_it.connect("activate", self.on_quit)
        m.append(quit_it)
```

- [ ] **Step 4 : vérifier que les tests passent**

Run : `python3 -m unittest tests.test_applet -v`
Expected : PASS.

- [ ] **Step 5 : ajouter les trois commandes à `SettingsWindow`**

Dans `SettingsWindow.__init__`, après le `Gtk.Label` de statut, remplacer la ligne de statut par une barre d'état cliquable et un pied :

```python
        # La zone d'etat devient un BOUTON quand le lien est tombe : sur GNOME
        # 46 le clic droit sur l'icone ne sort aucun menu, donc l'entree
        # "Reconnecter" du menu est inatteignable -- enceinte eteinte signifiait
        # applet muet jusqu'au relogin.
        self.status = Gtk.Button(label="")
        self.status.set_relief(Gtk.ReliefStyle.NONE)
        self.status.connect("clicked", self.on_reconnect)
        grid.attach(self.status, 0, len(self.RANGES) + 1, 2, 1)

        pied = Gtk.Box(spacing=8)
        self.autostart = Gtk.Switch()
        self.autostart.set_active(autostart_enabled())
        self.autostart.connect("notify::active", self.on_autostart)
        pied.add(self.autostart)
        pied.add(Gtk.Label(label="Démarrer avec la session", xalign=0))
        quitter = Gtk.Button(label="Quitter")
        quitter.connect("clicked", lambda _w: self.app.on_quit())
        pied.pack_end(quitter, False, False, 0)
        grid.attach(pied, 0, len(self.RANGES) + 2, 2, 1)
```

et les deux handlers :

```python
    def on_reconnect(self, _w):
        """Ne fait rien quand le lien est deja la : le bouton n'est actif que
        deconnecte, mais un double-clic rapide pourrait passer avant l'update.
        """
        if not self.app._connected():
            GLib.idle_add(self.app._initial_connect)

    def on_autostart(self, switch, _param):
        set_autostart(switch.get_active())
```

Dans `SettingsWindow.update`, remplacer la ligne de statut :

```python
            self.status.set_label("● connectée" if connected
                                  else "○ déconnectée — reconnecter")
            # Toujours sensible. Griser quand tout va bien affichait l'etat
            # normal comme un controle desactive, ce qui se lit comme une
            # panne. C'est le libelle qui porte l'etat ; le clic est simplement
            # sans effet une fois connecte (cf. on_reconnect).
            # relu a chaque update : le fichier peut avoir bouge a la main
            self.autostart.set_active(autostart_enabled())
```

⚠️ `set_active` réémet `notify::active`, ce qui rappellerait `set_autostart`. C'est idempotent, donc inoffensif — mais garder le drapeau `_loading` autour, comme pour les sliders, évite l'écriture de fichier inutile à chaque rafraîchissement. Placer cette ligne **dans** le bloc protégé par `self._loading = True`, et tester `if self._loading: return` en tête de `on_autostart`.

- [ ] **Step 6 : rendre la fenêtre atteignable même déconnectée**

Dans `_rebuild_menu`, l'entrée « Réglages… » est aujourd'hui `set_sensitive(connected)` (`marshall-applet:365`). Or c'est désormais le seul chemin vers « Quitter » et « Reconnecter ». Retirer la condition :

```python
        settings = Gtk.MenuItem(label="Réglages…")
        settings.connect("activate", self.on_settings)
        # plus de set_sensitive(connected) : la fenetre porte maintenant
        # Quitter et Reconnecter, qui servent justement quand c'est deconnecte.
        m.append(settings)
```

Le clic gauche (`on_settings`) n'a jamais été conditionné, donc rien d'autre à changer.

- [ ] **Step 7 : vérifier la suite complète**

Run : `python3 -m unittest discover -s tests -t . -v`
Expected : PASS, `tests/test_speaker.py` sauté sans matériel.

- [ ] **Step 8 : vérifier à la main sur la machine**

```bash
pkill -f "bin/marshall-appl[e]t"
~/bin/marshall-applet &
```

Cliquer sur l'icône, vérifier dans l'ordre :
1. l'interrupteur « Démarrer avec la session » est **allumé** (le fichier existe) ;
2. l'éteindre → `ls ~/.config/autostart/marshall-applet.desktop` renvoie « No such file » ;
3. le rallumer → le fichier revient ;
4. cliquer « Quitter » → **l'icône disparaît immédiatement**, et `pgrep -f marshall-applet` ne rend plus rien au bout de ~3 s.

- [ ] **Step 9 : commit**

```bash
git add marshall-applet tests/test_applet.py
git commit -m "feat: Quitter, Reconnecter et autostart dans la fenetre

Le clic droit sur l'icone ne sort aucun menu sur GNOME 46 / X11 avec
ubuntu-appindicators, donc Quitter et Reconnecter etaient inatteignables.
L'interface disparait maintenant avant la liberation BLE, pour que l'arret
soit percu comme immediat."
```

> **Point de livraison.** Le besoin n°1 est résolu. La suite est la façade.

---

## Tâche 5 : `KnobModel`, l'arithmétique des molettes

**Files:**
- Create: `marshall_ui.py`
- Test: `tests/test_knob_model.py`

- [ ] **Step 1 : écrire les tests qui échouent**

```python
"""Tests de l'arithmetique des molettes. Aucun GTK, aucun ecran.

C'est precisement pour rendre cette logique testable sans afficheur qu'elle
vit dans KnobModel et non dans le widget Knob.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from marshall_ui import KnobModel, TRAVEL_VOLUME_PX, TRAVEL_EQ_PX


def volume(v=0):
    return KnobModel(maximum=31, travel_px=TRAVEL_VOLUME_PX, value=v)


def bass(v=0):
    return KnobModel(maximum=10, travel_px=TRAVEL_EQ_PX, value=v)


class TestBornes(unittest.TestCase):
    def test_valeur_initiale_saturee_en_haut(self):
        self.assertEqual(volume(99).value, 31)

    def test_valeur_initiale_saturee_en_bas(self):
        self.assertEqual(volume(-5).value, 0)

    def test_step_ne_depasse_pas_le_maximum(self):
        k = volume(31)
        self.assertFalse(k.step(1), "step rend True alors que rien n'a change")
        self.assertEqual(k.value, 31)

    def test_step_ne_descend_pas_sous_zero(self):
        k = volume(0)
        self.assertFalse(k.step(-1))
        self.assertEqual(k.value, 0)


class TestStep(unittest.TestCase):
    def test_un_cran_de_molette_vaut_exactement_un(self):
        k = volume(20)
        self.assertTrue(k.step(1))
        self.assertEqual(k.value, 21)
        k.step(-1)
        self.assertEqual(k.value, 20)

    def test_step_rend_vrai_seulement_si_la_valeur_change(self):
        k = bass(5)
        self.assertTrue(k.step(1))
        self.assertFalse(k.step(0))


class TestGlisseRelatif(unittest.TestCase):
    """Cliquer sur une molette ne doit JAMAIS faire sauter la valeur la ou on
    a clique. On attrape, on tire."""

    def test_glisse_de_zero_ne_change_rien(self):
        k = volume(12)
        k.begin_drag()
        self.assertFalse(k.drag_to(0))
        self.assertEqual(k.value, 12)

    def test_toute_la_course_vers_le_haut_atteint_le_maximum(self):
        k = volume(0)
        k.begin_drag()
        k.drag_to(TRAVEL_VOLUME_PX)
        self.assertEqual(k.value, 31)

    def test_toute_la_course_vers_le_bas_atteint_zero(self):
        k = volume(31)
        k.begin_drag()
        k.drag_to(-TRAVEL_VOLUME_PX)
        self.assertEqual(k.value, 0)

    def test_deux_glisses_partent_de_lorigine(self):
        """Le piege : un drag_to cumulatif ferait doubler le deplacement.
        La moitie de la course, deux fois, doit rester la moitie."""
        k = volume(0)
        k.begin_drag()
        k.drag_to(TRAVEL_VOLUME_PX / 2)
        milieu = k.value
        k.drag_to(TRAVEL_VOLUME_PX / 2)
        self.assertEqual(k.value, milieu)

    def test_un_nouveau_begin_drag_repart_de_la_valeur_courante(self):
        k = volume(0)
        k.begin_drag()
        k.drag_to(TRAVEL_VOLUME_PX / 2)
        atteint = k.value
        k.begin_drag()
        k.drag_to(0)
        self.assertEqual(k.value, atteint)

    def test_un_micro_glisse_ne_change_pas_le_volume(self):
        """200 px pour 31 crans : 3 px ne doivent pas suffire a bouger."""
        k = volume(12)
        k.begin_drag()
        self.assertFalse(k.drag_to(3))
        self.assertEqual(k.value, 12)


class TestCourseDifferenciee(unittest.TestCase):
    """Sans courses distinctes, le volume (32 crans) serait environ trois fois
    plus nerveux que l'EQ (11 crans) pour un meme geste."""

    def test_le_volume_a_une_course_plus_longue_que_leq(self):
        self.assertGreater(TRAVEL_VOLUME_PX, TRAVEL_EQ_PX)

    def test_un_meme_geste_bouge_moins_le_volume_en_proportion(self):
        v, b = volume(0), bass(0)
        v.begin_drag()
        b.begin_drag()
        v.drag_to(50)
        b.drag_to(50)
        self.assertLess(v.value / 31, b.value / 10)


class TestMaximumVariable(unittest.TestCase):
    """Le maximum du volume vient du registre 0x08 de l'enceinte, pas d'une
    constante : il n'est connu qu'apres lecture de l'etat."""

    def test_reduire_le_maximum_sature_la_valeur(self):
        k = volume(31)
        self.assertTrue(k.set_maximum(20))
        self.assertEqual(k.value, 20)

    def test_augmenter_le_maximum_ne_touche_pas_la_valeur(self):
        k = volume(12)
        self.assertFalse(k.set_maximum(40))
        self.assertEqual(k.value, 12)

    def test_le_maximum_change_le_pas_du_glisse(self):
        k = KnobModel(maximum=10, travel_px=100, value=0)
        k.set_maximum(100)
        k.begin_drag()
        k.drag_to(100)
        self.assertEqual(k.value, 100)


class TestFraction(unittest.TestCase):
    """La fraction est ce que la peinture consomme : 0 = butee basse,
    1 = butee haute."""

    def test_zero_et_un(self):
        self.assertEqual(volume(0).fraction, 0.0)
        self.assertEqual(volume(31).fraction, 1.0)

    def test_maximum_nul_ne_divise_pas_par_zero(self):
        k = KnobModel(maximum=0, travel_px=100, value=0)
        self.assertEqual(k.fraction, 0.0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2 : vérifier qu'ils échouent**

Run : `python3 -m unittest tests.test_knob_model -v`
Expected : FAIL — `ModuleNotFoundError: No module named 'marshall_ui'`.

- [ ] **Step 3 : créer `marshall_ui.py` avec `KnobModel`**

```python
"""marshall_ui -- la facade dessinee de l'applet Marshall.

Ce module ne connait NI BlueZ, NI Speaker, NI D-Bus. Il expose des widgets et
des signaux ; c'est marshall-applet qui les relie a l'enceinte. Cette
frontiere est ce qui rend l'interface jugeable et testable sans materiel.

La regle d'architecture du projet s'applique ici : aucun widget n'appelle le
transport. Une Knob emet value-changed, et le debounce de l'applet ecrit.
"""
import math

# Course de glisse, en pixels, pour parcourir toute la plage.
# Distinctes a dessein : le volume a 32 crans et l'EQ 11. A course egale, le
# volume serait environ trois fois plus nerveux pour un meme geste.
TRAVEL_VOLUME_PX = 200      # ~6 px par cran
TRAVEL_EQ_PX = 140          # 14 px par cran exactement


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
        self._value = self._borner(value)
        self._origine = self._value

    def _borner(self, v):
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

    def _poser(self, v):
        """Rend True seulement si la valeur a change : un glisse de trois
        pixels sans franchir de cran ne doit rien emettre."""
        nouveau = self._borner(v)
        if nouveau == self._value:
            return False
        self._value = nouveau
        return True

    def set_value(self, v):
        return self._poser(v)

    def begin_drag(self):
        self._origine = self._value

    def drag_to(self, dy_haut_px):
        """dy_haut_px POSITIF = geste vers le haut = valeur qui monte.

        L'axe y de GTK descend, donc l'appelant passe (y_depart - y_courant).
        """
        return self._poser(
            self._origine + _round_half_up(dy_haut_px * self.maximum / self.travel_px))

    def step(self, delta):
        return self._poser(self._value + delta)

    def set_maximum(self, maximum):
        """Le maximum du volume vient du registre 0x08 de l'enceinte, pas d'une
        constante : il n'est connu qu'apres lecture de l'etat."""
        self.maximum = max(0, int(maximum))
        return self._poser(self._value)
```

- [ ] **Step 4 : vérifier que les tests passent**

Run : `python3 -m unittest tests.test_knob_model -v`
Expected : PASS, 19 tests.

- [ ] **Step 5 : commit**

```bash
git add marshall_ui.py tests/test_knob_model.py
git commit -m "feat: KnobModel, l'arithmetique des molettes sans GTK"
```

---

## Tâche 6 : les fonctions de peinture

**Files:**
- Modify: `marshall_ui.py`
- Test: `tests/test_paint.py`

- [ ] **Step 1 : écrire les tests qui échouent**

```python
"""Fumee sur les fonctions de peinture. Cairo rend en memoire, donc aucun
afficheur n'est requis.

Ces tests attestent l'absence de plantage et le fait que quelque chose est
reellement peint -- PAS la beaute, qui se juge a l'oeil.
"""
import os
import sys
import unittest

import cairo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import marshall_ui as ui


TAILLES = ((420, 330), (120, 90), (900, 700))


def surface_et_contexte(w, h):
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    return s, cairo.Context(s)


def a_peint_quelque_chose(surface):
    """Vrai si au moins un pixel n'est plus transparent."""
    surface.flush()
    return any(surface.get_data())


class TestPeintureNePlantePas(unittest.TestCase):
    def test_tolex(self):
        for w, h in TAILLES:
            s, cr = surface_et_contexte(w, h)
            ui.paint_tolex(cr, w, h)
            self.assertTrue(a_peint_quelque_chose(s), f"tolex vide en {w}x{h}")

    def test_piping(self):
        for w, h in TAILLES:
            s, cr = surface_et_contexte(w, h)
            ui.paint_piping(cr, w, h)
            self.assertTrue(a_peint_quelque_chose(s))

    def test_brass(self):
        for w, h in TAILLES:
            s, cr = surface_et_contexte(w, h)
            ui.paint_brass(cr, 4, 4, w - 8, h - 8)
            self.assertTrue(a_peint_quelque_chose(s))

    def test_grille(self):
        for w, h in TAILLES:
            s, cr = surface_et_contexte(w, h)
            ui.paint_grille(cr, 4, 4, w - 8, h - 8)
            self.assertTrue(a_peint_quelque_chose(s))

    def test_logo(self):
        for w, h in TAILLES:
            s, cr = surface_et_contexte(w, h)
            ui.paint_logo(cr, w / 2, h / 2, h / 5)
            self.assertTrue(a_peint_quelque_chose(s),
                            "le logo n'a rien peint : police introuvable ?")


class TestPeintureDeLaMolette(unittest.TestCase):
    def test_toutes_les_fractions(self):
        for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
            s, cr = surface_et_contexte(80, 80)
            ui.paint_knob(cr, 40, 40, 30, fraction)
            self.assertTrue(a_peint_quelque_chose(s), f"fraction {fraction}")

    def test_rayon_minuscule(self):
        s, cr = surface_et_contexte(12, 12)
        ui.paint_knob(cr, 6, 6, 4, 0.5)      # ne doit pas lever
        self.assertTrue(a_peint_quelque_chose(s))

    def test_etat_inactif_peint_aussi(self):
        s, cr = surface_et_contexte(80, 80)
        ui.paint_knob(cr, 40, 40, 30, 0.5, actif=False)
        self.assertTrue(a_peint_quelque_chose(s))

    def test_les_deux_butees_ne_donnent_pas_le_meme_dessin(self):
        """Si le repere ne tournait pas, la molette serait muette."""
        rendus = []
        for fraction in (0.0, 1.0):
            s, cr = surface_et_contexte(80, 80)
            ui.paint_knob(cr, 40, 40, 30, fraction)
            s.flush()
            rendus.append(bytes(s.get_data()))
        self.assertNotEqual(rendus[0], rendus[1],
                            "le repere de la molette ne tourne pas")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2 : vérifier qu'ils échouent**

Run : `python3 -m unittest tests.test_paint -v`
Expected : FAIL — `AttributeError: module 'marshall_ui' has no attribute 'paint_tolex'`.

- [ ] **Step 3 : implémenter les fonctions de peinture**

Ajouter en tête de `marshall_ui.py`, après `import math` :

```python
import cairo
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")        # sinon PyGIWarning a l'import
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GLib, GObject, Pango, PangoCairo   # noqa: E402
```

Puis, après `KnobModel` :

```python
# -- palette --------------------------------------------------------------
# Relevee sur les photos produit : tolex noir legerement bleute, laiton chaud,
# toile "salt and pepper" gris tres sombre.
TOLEX = (0.063, 0.063, 0.067)
TOILE = (0.086, 0.086, 0.102)
OR_LISERE = (0.808, 0.659, 0.235)

# La molette parcourt 280 degres, comme un potentiometre reel : les butees
# doivent se voir, une rotation complete ne dirait pas ou est le zero.
ANGLE_MIN = math.radians(-140)
ANGLE_MAX = math.radians(140)


def _chemin_round_half_up(cr, x, y, w, h, rayon):
    """Rectangle a coins arrondis. Borne le rayon : sur une bande fine, un
    rayon trop grand produit des arcs qui se croisent."""
    r = max(0.0, min(rayon, w / 2, h / 2))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, math.radians(-90), 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.radians(90))
    cr.arc(x + r, y + h - r, r, math.radians(90), math.radians(180))
    cr.arc(x + r, y + r, r, math.radians(180), math.radians(270))
    cr.close_path()


def _hachures(cr, x, y, w, h, pas, rgba, montante):
    """Trame diagonale, utilisee pour le tissage de la toile."""
    cr.save()
    cr.set_source_rgba(*rgba)
    cr.set_line_width(1)
    d = -h if montante else 0
    fin = w if montante else w + h
    while d < fin:
        cr.move_to(x + d, y + h if montante else y)
        cr.line_to(x + d + h, y if montante else y + h)
        d += pas
    cr.stroke()
    cr.restore()


def paint_tolex(cr, w, h):
    """Le revetement du caisson : noir, avec un grain regulier tres discret."""
    cr.set_source_rgb(*TOLEX)
    cr.rectangle(0, 0, w, h)
    cr.fill()
    cr.set_line_width(1)
    for pas, alpha, vertical in ((4, 0.030, False), (4, 0.022, True)):
        cr.set_source_rgba(1, 1, 1, alpha)
        p = 0.5
        while p < (w if vertical else h):
            if vertical:
                cr.move_to(p, 0)
                cr.line_to(p, h)
            else:
                cr.move_to(0, p)
                cr.line_to(w, p)
            p += pas
        cr.stroke()


def paint_piping(cr, w, h, rayon=7):
    """Le lisere dore du pourtour. Un des rares vrais indices de profondeur :
    il n'y a aucun moteur 3D ici, tout est peint."""
    cr.save()
    _chemin_round_half_up(cr, 1.5, 1.5, w - 3, h - 3, rayon)
    cr.set_source_rgba(*OR_LISERE, 0.55)
    cr.set_line_width(1.5)
    cr.stroke()
    cr.restore()


def paint_brass(cr, x, y, w, h, rayon=4):
    """La plaque de laiton qui porte les molettes : degrade chaud, brossage
    vertical, biseau clair en haut et sombre en bas."""
    cr.save()
    _chemin_round_half_up(cr, x, y, w, h, rayon)
    cr.clip()

    g = cairo.LinearGradient(0, y, 0, y + h)
    for pos, rgb in ((0.00, (0.953, 0.890, 0.659)),
                     (0.22, (0.847, 0.714, 0.263)),
                     (0.52, (0.753, 0.608, 0.133)),
                     (0.78, (0.612, 0.478, 0.086)),
                     (1.00, (0.435, 0.333, 0.063))):
        g.add_color_stop_rgb(pos, *rgb)
    cr.set_source(g)
    cr.paint()

    cr.set_source_rgba(1, 1, 1, 0.10)       # brossage
    cr.set_line_width(1)
    xi = x + 0.5
    while xi < x + w:
        cr.move_to(xi, y)
        cr.line_to(xi, y + h)
        xi += 3
    cr.stroke()

    cr.set_line_width(1)                     # biseau
    cr.set_source_rgba(1, 1, 1, 0.55)
    cr.move_to(x, y + 0.5)
    cr.line_to(x + w, y + 0.5)
    cr.stroke()
    cr.set_source_rgba(0, 0, 0, 0.45)
    cr.move_to(x, y + h - 0.5)
    cr.line_to(x + w, y + h - 0.5)
    cr.stroke()
    cr.restore()


def paint_grille(cr, x, y, w, h, rayon=3):
    """La toile tissee, et l'ombre interne qui creuse le caisson."""
    cr.save()
    _chemin_round_half_up(cr, x, y, w, h, rayon)
    cr.clip()
    cr.set_source_rgb(*TOILE)
    cr.paint()

    _hachures(cr, x, y, w, h, 3, (0.886, 0.839, 0.698, 0.075), True)
    _hachures(cr, x, y, w, h, 3, (0, 0, 0, 0.55), False)

    cx, cy = x + w / 2, y + h / 2
    ombre = cairo.RadialGradient(cx, cy, min(w, h) * 0.25,
                                 cx, cy, max(w, h) * 0.78)
    ombre.add_color_stop_rgba(0, 0, 0, 0, 0.0)
    ombre.add_color_stop_rgba(1, 0, 0, 0, 0.9)
    cr.set_source(ombre)
    cr.paint()
    cr.restore()


# Chaine de repli pour le lettrage. Z003 (clone d'URW Chancery, livre avec les
# polices base-35) est present sur la machine cible et c'est vers lui que
# resout fc-match cursive. FRAGILITE ASSUMEE : sans police calligraphique, le
# logo retombe sur un italique quelconque -- la facade reste correcte, le
# lettrage moins juste.
POLICE_LOGO = "Z003,URW Chancery L,Brush Script MT,cursive"


def paint_logo(cr, cx, cy, taille, texte="Marshall"):
    """Le lettrage dore, centre sur (cx, cy).

    Approximation typographique d'une marque deposee, pour un outil personnel
    non affilie. Aucun fichier de police ni visuel du fabricant n'est embarque
    dans le depot.
    """
    layout = PangoCairo.create_layout(cr)
    desc = Pango.FontDescription()
    desc.set_family(POLICE_LOGO)
    desc.set_style(Pango.Style.ITALIC)
    desc.set_absolute_size(taille * Pango.SCALE)
    layout.set_font_description(desc)
    layout.set_text(texte, -1)
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


def paint_knob(cr, cx, cy, rayon, fraction, actif=True):
    """Une molette doree moletee, tournee selon fraction (0..1).

    Le dome et le reflet ne tournent PAS -- seuls le moletage et le repere le
    font. C'est ce qui donne l'illusion d'un objet eclaire par le haut qu'on
    fait pivoter, plutot que d'une image qu'on fait tourner.
    """
    angle = ANGLE_MIN + (ANGLE_MAX - ANGLE_MIN) * max(0.0, min(1.0, fraction))
    cr.save()

    cr.set_source_rgba(0, 0, 0, 0.55)        # ombre portee
    cr.arc(cx, cy + max(1.0, rayon * 0.10), rayon, 0, 2 * math.pi)
    cr.fill()

    dome = cairo.RadialGradient(cx - rayon * 0.28, cy - rayon * 0.34,
                                max(0.5, rayon * 0.05), cx, cy, rayon)
    for pos, rgb in ((0.00, (0.996, 0.969, 0.824)),
                     (0.16, (0.914, 0.831, 0.533)),
                     (0.46, (0.788, 0.635, 0.153)),
                     (0.78, (0.553, 0.435, 0.106)),
                     (1.00, (0.341, 0.263, 0.063))):
        dome.add_color_stop_rgb(pos, *rgb)
    cr.set_source(dome)
    cr.arc(cx, cy, rayon, 0, 2 * math.pi)
    cr.fill()

    cr.save()                                 # moletage + repere : ca tourne
    cr.translate(cx, cy)
    cr.rotate(angle)
    cr.set_line_width(max(0.8, rayon * 0.04))
    dents = 48
    for i in range(dents):
        a = 2 * math.pi * i / dents
        cr.set_source_rgba(*((1, 1, 1, 0.20) if i % 2 else (0, 0, 0, 0.45)))
        cr.move_to(rayon * 0.78 * math.cos(a), rayon * 0.78 * math.sin(a))
        cr.line_to(rayon * 0.98 * math.cos(a), rayon * 0.98 * math.sin(a))
        cr.stroke()

    cr.set_source_rgb(0.129, 0.102, 0.016)
    cr.set_line_width(max(1.6, rayon * 0.09))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.move_to(0, -rayon * 0.80)
    cr.line_to(0, -rayon * 0.42)
    cr.stroke()
    cr.restore()

    reflet = cairo.LinearGradient(0, cy - rayon, 0, cy)   # reflet fixe
    reflet.add_color_stop_rgba(0, 1, 1, 1, 0.28)
    reflet.add_color_stop_rgba(1, 1, 1, 1, 0.0)
    cr.set_source(reflet)
    cr.arc(cx, cy, rayon * 0.97, 0, 2 * math.pi)
    cr.fill()

    if not actif:
        # voile gris : la molette reste lisible mais visiblement hors service
        cr.set_source_rgba(0.10, 0.10, 0.11, 0.62)
        cr.arc(cx, cy, rayon, 0, 2 * math.pi)
        cr.fill()
    cr.restore()
```

- [ ] **Step 4 : vérifier que les tests passent**

Run : `python3 -m unittest tests.test_paint -v`
Expected : PASS.

Si `test_logo` échoue avec une surface vide, la chaîne de polices n'a rien résolu. Vérifier :

Run : `fc-match "Z003"`
Expected : `Z003-MediumItalic.otf: "Z003" "Medium Italic"`

- [ ] **Step 5 : regarder le rendu à l'œil**

La fumée n'atteste pas la beauté. Produire une planche et la regarder :

```bash
python3 - <<'PY'
import cairo, marshall_ui as ui
w, h = 420, 330
s = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
cr = cairo.Context(s)
ui.paint_tolex(cr, w, h)
ui.paint_brass(cr, 12, 12, w - 24, 96)
for i, f in enumerate((0.65, 1.0, 0.7)):
    ui.paint_knob(cr, 78 + i * 132, 60, 30, f)
ui.paint_grille(cr, 12, 122, w - 24, 130)
ui.paint_logo(cr, w / 2, 187, 40)
ui.paint_piping(cr, w, h)
s.write_to_png("/tmp/facade.png")
print("ecrit /tmp/facade.png")
PY
xdg-open /tmp/facade.png
```

Itérer sur la palette et les dégradés jusqu'à ce que ça tienne. **Ne pas commiter un rendu qu'on n'a pas regardé.**

- [ ] **Step 6 : commit**

```bash
git add marshall_ui.py tests/test_paint.py
git commit -m "feat: les fonctions de peinture de la facade"
```

---

## Tâche 7 : le widget `Knob`

**Files:**
- Modify: `marshall_ui.py`
- Test: `tests/test_knob_widget.py`

- [ ] **Step 1 : écrire les tests qui échouent**

```python
"""Tests du widget Knob. Sautes sans afficheur : construire un widget GTK
demande un Gdk.Display.

L'arithmetique, elle, est testee sans ecran dans test_knob_model.py -- c'est
tout l'interet de la separation.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

import marshall_ui as ui  # noqa: E402

AFFICHEUR = Gtk.init_check([])[0]


@unittest.skipUnless(AFFICHEUR, "aucun Gdk.Display : widget GTK inconstructible")
class TestKnob(unittest.TestCase):
    def faire(self, maximum=31, valeur=12):
        return ui.Knob("volume", maximum=maximum, travel_px=200, value=valeur)

    def test_le_signal_porte_la_cle_et_la_valeur(self):
        k = self.faire()
        recus = []
        k.connect("value-changed", lambda _w, v: recus.append(v))
        k.step(1)
        self.assertEqual(recus, [13])

    def test_un_pas_qui_ne_change_rien_nemet_pas(self):
        k = self.faire(valeur=31)
        recus = []
        k.connect("value-changed", lambda _w, v: recus.append(v))
        k.step(1)                          # deja a la butee
        self.assertEqual(recus, [])

    def test_set_value_silently_nemet_pas(self):
        """C'est le chemin des mises a jour venues de l'enceinte : les
        reflechir vers le transport serait une boucle."""
        k = self.faire()
        recus = []
        k.connect("value-changed", lambda _w, v: recus.append(v))
        k.set_value_silently(25)
        self.assertEqual(recus, [])
        self.assertEqual(k.value, 25)

    def test_set_maximum_silently_nemet_pas(self):
        k = self.faire(valeur=31)
        recus = []
        k.connect("value-changed", lambda _w, v: recus.append(v))
        k.set_maximum_silently(20)         # sature 31 -> 20
        self.assertEqual(recus, [])
        self.assertEqual(k.value, 20)

    def test_insensible_ignore_les_pas(self):
        k = self.faire()
        k.set_sensitive(False)
        recus = []
        k.connect("value-changed", lambda _w, v: recus.append(v))
        k.step(1)
        self.assertEqual(recus, [])

    def test_la_cle_est_exposee(self):
        self.assertEqual(self.faire().key, "volume")

    def test_le_widget_demande_une_taille(self):
        k = self.faire()
        w, h = k.get_preferred_width()[1], k.get_preferred_height()[1]
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2 : vérifier qu'ils échouent**

Run : `python3 -m unittest tests.test_knob_widget -v`
Expected : FAIL — `AttributeError: module 'marshall_ui' has no attribute 'Knob'` (ou 7 tests sautés si la session n'a pas d'afficheur — les lancer alors depuis un terminal graphique).

- [ ] **Step 3 : implémenter `Knob`**

Dans `marshall_ui.py`, après les fonctions de peinture :

```python
KNOB_RADIUS = 24        # r30 laissait trop peu de place au libelle et a la valeur
KNOB_MARGIN = 6


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
        self._y_depart = None

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

    # -- etat -------------------------------------------------------------
    @property
    def value(self):
        return self._m.value

    def _emettre_si_change(self, change):
        if change:
            self.queue_draw()
            self.emit("value-changed", self._m.value)
        return change

    def step(self, delta):
        """Un cran. Rend True si la valeur a bouge."""
        if not self.get_sensitive():
            return False
        return self._emettre_si_change(self._m.step(delta))

    def set_value_silently(self, v):
        """Mise a jour venue de l'enceinte : ne doit PAS emettre, sinon on
        reflechirait vers le transport ce qu'il vient de nous dire."""
        if self._m.set_value(v):
            self.queue_draw()

    def set_maximum_silently(self, maximum):
        """Le maximum du volume vient du registre 0x08, connu seulement apres
        lecture de l'etat."""
        if self._m.set_maximum(maximum):
            self.queue_draw()
        else:
            self.queue_draw()      # la graduation change meme sans la valeur

    def do_set_sensitive(self, sensible):
        Gtk.DrawingArea.do_set_sensitive(self, sensible)
        self.queue_draw()

    # -- rendu ------------------------------------------------------------
    def _on_draw(self, _w, cr):
        alloc = self.get_allocation()
        rayon = max(6, min(alloc.width, alloc.height) / 2 - KNOB_MARGIN)
        paint_knob(cr, alloc.width / 2, alloc.height / 2, rayon,
                   self._m.fraction, actif=self.get_sensitive())
        if self.has_visible_focus():
            cr.save()
            cr.set_source_rgba(*OR_LISERE, 0.9)
            cr.set_line_width(2)
            cr.arc(alloc.width / 2, alloc.height / 2, rayon + 3,
                   0, 2 * math.pi)
            cr.stroke()
            cr.restore()
        return False

    # -- entrees ----------------------------------------------------------
    def _on_press(self, _w, ev):
        if not self.get_sensitive() or ev.button != 1:
            return False
        self.grab_focus()
        # Glisse RELATIF : on memorise le point d'attache, la valeur ne saute
        # pas la ou on a clique.
        self._y_depart = ev.y_root
        self._m.begin_drag()
        return True

    def _on_motion(self, _w, ev):
        if self._y_depart is None:
            return False
        # l'axe y de GTK descend : (depart - courant) est positif vers le haut
        self._emettre_si_change(self._m.drag_to(self._y_depart - ev.y_root))
        return True

    def _on_release(self, _w, _ev):
        self._y_depart = None
        return True

    def _on_scroll(self, _w, ev):
        """Un cran de molette vaut EXACTEMENT un cran de valeur. C'est ce qui
        rend la precision non negociable, quel que soit le nombre de crans."""
        if not self.get_sensitive():
            return False
        direction = ev.get_scroll_direction()
        if direction[0]:
            delta = 1 if direction[1] == Gdk.ScrollDirection.UP else -1
            if direction[1] not in (Gdk.ScrollDirection.UP,
                                    Gdk.ScrollDirection.DOWN):
                return False
        else:
            # souris a defilement lisse (pave tactile) : dy < 0 = vers le haut
            _ok, _dx, dy = ev.get_scroll_deltas()
            if dy == 0:
                return False
            delta = 1 if dy < 0 else -1
        self.step(delta)
        return True

    def _on_key(self, _w, ev):
        if not self.get_sensitive():
            return False
        touche = ev.keyval
        if touche in (Gdk.KEY_Up, Gdk.KEY_Right):
            return self.step(1) or True
        if touche in (Gdk.KEY_Down, Gdk.KEY_Left):
            return self.step(-1) or True
        if touche == Gdk.KEY_Home:
            self._emettre_si_change(self._m.set_value(0))
            return True
        if touche == Gdk.KEY_End:
            self._emettre_si_change(self._m.set_value(self._m.maximum))
            return True
        return False
```

**Note sur le glissé :** `ev.y_root` et non `ev.y`. Le geste doit continuer quand le pointeur sort du widget, et les coordonnées racine sont les seules qui restent cohérentes hors de l'allocation. GTK livre les `motion-notify-event` pendant qu'un bouton est enfoncé sans grab explicite ; si un test manuel montre le contraire, ajouter `Gdk.pointer_grab` — mais ne pas l'ajouter par précaution.

- [ ] **Step 4 : vérifier que les tests passent**

Run : `python3 -m unittest tests.test_knob_widget -v`
Expected : PASS (7 tests) depuis une session graphique.

- [ ] **Step 5 : commit**

```bash
git add marshall_ui.py tests/test_knob_widget.py
git commit -m "feat: le widget Knob, molette rotative au glisse et a la molette"
```

---

## Tâche 8 : `BrassPanel`, `Grille` et `Facade`

**Files:**
- Modify: `marshall_ui.py`

Pas de test automatique nouveau : ce sont des assemblages de widgets, sans logique propre. Ce qu'ils valent se voit à l'œil, à l'étape 3.

- [ ] **Step 1 : implémenter les trois conteneurs**

```python
LARGEUR_FENETRE = 420
HAUTEUR_FENETRE = 330
MARGE = 12

# Les trois registres pilotables, avec leur course de glisse. L'ordre est
# celui du panneau de commandes de l'enceinte.
REGISTRES = (("volume", TRAVEL_VOLUME_PX),
             ("bass", TRAVEL_EQ_PX),
             ("treble", TRAVEL_EQ_PX))


class BrassPanel(Gtk.Box):
    """La plaque de laiton : peint son fond, et porte les trois molettes.

    Peint SON rectangle et rien d'autre -- il n'y a aucun systeme de
    coordonnees partage entre le decor et les controles.
    """

    def __init__(self, maximums):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL,
                         homogeneous=True)
        self.set_border_width(10)
        self._fond = None            # cf. _on_draw : fond mis en cache
        self._fond_taille = None
        self.knobs = {}
        for key, course in REGISTRES:
            colonne = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            knob = Knob(key, maximum=maximums[key], travel_px=course)
            libelle = Gtk.Label(label=key.upper())
            libelle.get_style_context().add_class("marshall-cap")
            valeur = Gtk.Label(label="0")
            valeur.get_style_context().add_class("marshall-val")
            colonne.pack_start(knob, False, False, 0)
            colonne.pack_start(libelle, False, False, 0)
            colonne.pack_start(valeur, False, False, 0)
            self.add(colonne)
            self.knobs[key] = knob
            knob._etiquette_valeur = valeur      # mise a jour par set_display
        self.connect("draw", self._on_draw)

    def _on_draw(self, _w, cr):
        """Fond mis en cache, meme raison que dans Facade : ce conteneur est le
        PARENT des molettes, donc un glisse le fait redessiner a chaque
        evenement de mouvement."""
        alloc = self.get_allocation()
        if (self._fond is None
                or self._fond_taille != (alloc.width, alloc.height)):
            self._fond = cairo.ImageSurface(
                cairo.FORMAT_ARGB32, alloc.width, alloc.height)
            paint_brass(cairo.Context(self._fond), 0, 0,
                        alloc.width, alloc.height)
            self._fond_taille = (alloc.width, alloc.height)
        cr.set_source_surface(self._fond, 0, 0)
        cr.paint()
        return False        # les enfants se dessinent par-dessus

    def set_display(self, key, valeur):
        self.knobs[key]._etiquette_valeur.set_text(str(valeur))


class Grille(Gtk.DrawingArea):
    """La toile tissee et le logo. Prend la place restante."""

    def __init__(self):
        super().__init__()
        self.set_size_request(-1, 96)
        self.connect("draw", self._on_draw)

    def _on_draw(self, _w, cr):
        alloc = self.get_allocation()
        paint_grille(cr, 0, 0, alloc.width, alloc.height)
        paint_logo(cr, alloc.width / 2, alloc.height / 2,
                   max(20, min(54, alloc.height * 0.50)))
        return False
```

Puis la `Facade`, qui porte les signaux :

```python
class Facade(Gtk.Box):
    """L'assemblage complet, sur un fond de tolex.

    Expose les signaux que la fenetre relie a l'applet. La Facade ne connait
    ni BlueZ, ni Speaker : elle ne sait meme pas qu'une enceinte existe.
    """

    __gsignals__ = {
        "knob-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, int)),
        "preset-chosen": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "reconnect-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "autostart-toggled": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        "quit-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, presets, maximums):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=MARGE)
        self.set_border_width(MARGE)
        self.get_style_context().add_class("marshall-facade")
        self._fond = None            # cf. _on_draw : fond mis en cache
        self._fond_taille = None
        # True pendant les mises a jour programmees : sans ca, refleter l'etat
        # de l'enceinte declencherait des ecritures vers l'enceinte.
        self._loading = True

        self.panel = BrassPanel(maximums)
        self.pack_start(self.panel, False, False, 0)
        for key, _course in REGISTRES:
            self.panel.knobs[key].connect("value-changed", self._on_knob, key)

        self.pack_start(Grille(), True, True, 0)

        rang = Gtk.Box(spacing=5)
        self.presets = {}
        for nom in presets:
            b = Gtk.Button(label=nom)
            b.get_style_context().add_class("marshall-preset")
            b.connect("clicked", lambda _w, n=nom: self.emit("preset-chosen", n))
            rang.pack_start(b, False, False, 0)
            self.presets[nom] = b
        # Bouton et non etiquette : sur GNOME 46 le clic droit sur l'icone ne
        # sort aucun menu, donc "Reconnecter" doit vivre ici ou nulle part.
        self.etat = Gtk.Button(label="")
        self.etat.set_relief(Gtk.ReliefStyle.NONE)
        self.etat.get_style_context().add_class("marshall-etat")
        self.etat.connect("clicked",
                          lambda _w: self.emit("reconnect-requested"))
        rang.pack_end(self.etat, False, False, 0)
        self.pack_start(rang, False, False, 0)

        pied = Gtk.Box(spacing=8)
        pied.get_style_context().add_class("marshall-pied")
        self.autostart = Gtk.Switch()
        self.autostart.connect("notify::active", self._on_autostart)
        pied.pack_start(self.autostart, False, False, 0)
        etiquette = Gtk.Label(label="Démarrer avec la session", xalign=0)
        etiquette.get_style_context().add_class("marshall-cap")
        pied.pack_start(etiquette, False, False, 0)
        quitter = Gtk.Button(label="Quitter")
        quitter.get_style_context().add_class("marshall-quit")
        quitter.connect("clicked", lambda _w: self.emit("quit-requested"))
        pied.pack_end(quitter, False, False, 0)
        self.pack_start(pied, False, False, 0)

        self.connect("draw", self._on_draw)
        self._loading = False

    def _on_draw(self, _w, cr):
        """Le fond est MIS EN CACHE, pas repeint a chaque passage.

        Mesure sur la machine : paint_tolex coute 3,4 ms et paint_grille 6,5 ms
        en 420x330. Or GTK redessine les ancetres decoupes a la region
        invalidee, et Cairo decoupe le RENDU, pas la construction des chemins :
        les boucles de paint_tolex se rejoueraient donc entierement a chaque
        evenement de mouvement pendant un glisse de molette. Le fond ne depend
        que de la taille, donc on le peint une fois dans une ImageSurface.
        """
        alloc = self.get_allocation()
        if (self._fond is None
                or self._fond_taille != (alloc.width, alloc.height)):
            self._fond = cairo.ImageSurface(
                cairo.FORMAT_ARGB32, alloc.width, alloc.height)
            fond_cr = cairo.Context(self._fond)
            paint_tolex(fond_cr, alloc.width, alloc.height)
            paint_piping(fond_cr, alloc.width, alloc.height)
            self._fond_taille = (alloc.width, alloc.height)
        cr.set_source_surface(self._fond, 0, 0)
        cr.paint()
        return False

    def _on_knob(self, _widget, valeur, key):
        self.panel.set_display(key, valeur)
        if self._loading:
            return
        self.emit("knob-changed", key, valeur)

    def _on_autostart(self, switch, _param):
        if self._loading:
            return
        self.emit("autostart-toggled", switch.get_active())

    def update(self, state, connected, pending, preset_actif, autostart):
        """Mise a jour programmee : ne doit declencher AUCUNE ecriture.

        `pending` est passe explicitement, et non lu dans l'applet : c'est ce
        qui garde ce module ignorant de l'applet. Une valeur encore en vol ne
        doit pas etre ecrasee, sinon la molette sauterait en arriere sous le
        doigt.
        """
        precedent = self._loading          # restaurer, pas forcer a False
        self._loading = True
        try:
            if state:
                haut = state.get("max_volume")
                if haut:
                    self.panel.knobs["volume"].set_maximum_silently(haut)
                for key, knob in self.panel.knobs.items():
                    if key in pending or key not in state:
                        continue
                    knob.set_value_silently(state[key])
                    self.panel.set_display(key, state[key])
            for knob in self.panel.knobs.values():
                knob.set_sensitive(bool(connected))
            for nom, bouton in self.presets.items():
                bouton.set_sensitive(bool(connected))
                actif = bouton.get_style_context().has_class(
                    "marshall-preset-actif")
                if (nom == preset_actif) != actif:
                    fn = (bouton.get_style_context().add_class if nom == preset_actif
                          else bouton.get_style_context().remove_class)
                    fn("marshall-preset-actif")
            self.etat.set_label("● connectée" if connected
                                else "○ déconnectée — reconnecter")
            # Toujours sensible : griser l'etat normal le fait lire comme une
            # panne. Le libelle porte l'etat, le clic est sans effet connecte.
            self.autostart.set_active(bool(autostart))
        finally:
            self._loading = precedent
```

- [ ] **Step 2 : la feuille CSS**

Toujours dans `marshall_ui.py` :

```python
# Le pied reste en widgets GTK ordinaires : inutile de peindre des boutons a
# la main, GTK gere deja le survol, le focus et le clavier.
CSS = b"""
.marshall-facade { background-color: transparent; }
.marshall-cap {
  font-size: 8pt; font-weight: 600; letter-spacing: 1px;
  color: #3a2c06;
}
.marshall-pied .marshall-cap { color: #8d8d92; }
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
.marshall-etat { font-size: 8pt; color: #8d8d92; }
"""


def installer_css():
    """Idempotent : deux appels ne doivent pas empiler deux providers, sinon
    les regles seraient evaluees deux fois."""
    global _css_pose
    if _css_pose:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_pose = True


_css_pose = False
```

Appeler `installer_css()` en tête de `Facade.__init__`.

- [ ] **Step 3 : regarder l'assemblage à l'œil**

```bash
python3 - <<'PY'
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
from marshall_ble import PRESETS
import marshall_ui as ui

w = Gtk.Window(title="Marshall Acton III")
w.set_default_size(ui.LARGEUR_FENETRE, ui.HAUTEUR_FENETRE)
w.set_resizable(False)
f = ui.Facade(PRESETS, {"volume": 31, "bass": 10, "treble": 10})
f.update({"volume": 20, "max_volume": 31, "bass": 10, "treble": 7},
         True, {}, "Musique", True)
for nom, sig in (("knob", "knob-changed"), ("preset", "preset-chosen")):
    f.connect(sig, lambda *a: print(a[1:]))
w.add(f)
w.connect("destroy", Gtk.main_quit)
w.show_all()
Gtk.main()
PY
```

Vérifier : les trois molettes tournent au glissé et à la molette, les valeurs suivent, le preset actif est allumé, le logo est lisible, le liseré fait le tour. Itérer sur les marges et les tailles jusqu'à ce que ça tienne.

- [ ] **Step 4 : la suite complète passe toujours**

Run : `python3 -m unittest discover -s tests -t . -v`
Expected : PASS.

- [ ] **Step 5 : commit**

```bash
git add marshall_ui.py
git commit -m "feat: BrassPanel, Grille et Facade"
```

---

## Tâche 9 : la fenêtre de l'applet devient la façade

**Files:**
- Modify: `marshall-applet` — supprimer `SettingsWindow` (y compris ce qui a été ajouté en tâche 4), ajouter `SpeakerWindow`, ajuster l'import et `on_settings`
- Test: `tests/test_applet.py` (les tests existants doivent passer sans modification)

- [ ] **Step 1 : remplacer la classe de fenêtre**

Étendre l'import en tête de `marshall-applet` :

```python
from marshall_ble import (
    PRESETS, Speaker, match_preset, BASS_MAX, TREBLE_MAX, VOLUME_MAX_FALLBACK,
)
from marshall_ui import Facade, LARGEUR_FENETRE, HAUTEUR_FENETRE
```

Supprimer toute la classe `SettingsWindow` et la remplacer par :

```python
class SpeakerWindow(Gtk.Window):
    """La facade. Cachee et non detruite a la fermeture, pour que l'applet
    continue de vivre dans la barre.

    Ne contient aucune logique : elle relie les signaux de la Facade aux
    methodes de l'applet. Tout ce qui est delicat est soit dans marshall_ui
    (le dessin, l'arithmetique), soit dans Applet (le debounce, le transport).
    """

    def __init__(self, app):
        super().__init__(title="Marshall Acton III")
        self.app = app
        self.set_default_size(LARGEUR_FENETRE, HAUTEUR_FENETRE)
        self.set_resizable(False)

        self.facade = Facade(PRESETS, {"volume": VOLUME_MAX_FALLBACK,
                                       "bass": BASS_MAX,
                                       "treble": TREBLE_MAX})
        self.add(self.facade)

        self.facade.connect("knob-changed", self._on_knob)
        self.facade.connect("preset-chosen",
                            lambda _f, nom: self.app.apply_preset(nom))
        self.facade.connect("reconnect-requested", self._on_reconnect)
        self.facade.connect("autostart-toggled",
                            lambda _f, actif: set_autostart(actif))
        self.facade.connect("quit-requested", lambda _f: self.app.on_quit())
        self.connect("delete-event", self.on_close)

    def on_close(self, *_a):
        self.hide()
        return True                  # cacher, pas detruire

    def _on_knob(self, _facade, key, valeur):
        """Passe par le debounce, JAMAIS directement au transport : c'est la
        regle d'architecture en tete de ce fichier."""
        self.app.schedule_write(key, valeur)

    def _on_reconnect(self, _facade):
        """idle_add : connect() est synchrone et bloque la boucle GLib jusqu'a
        30 s. L'appeler depuis le handler gelerait l'interface avant meme que
        le clic soit rendu."""
        if not self.app._connected():
            GLib.idle_add(self.app._initial_connect)

    def update(self, state, connected):
        """Meme signature qu'avant : _do_refresh n'a pas a changer.

        _pending est passe explicitement a la Facade -- marshall_ui ne doit
        pas connaitre l'applet.
        """
        self.facade.update(
            state, connected, self.app._pending,
            match_preset(state["bass"], state["treble"]) if connected else None,
            autostart_enabled())
```

- [ ] **Step 2 : brancher la nouvelle fenêtre**

Dans `Applet.on_settings`, remplacer `SettingsWindow(self)` par `SpeakerWindow(self)`. Le reste de la méthode est inchangé.

- [ ] **Step 3 : la suite complète passe**

Run : `python3 -m unittest discover -s tests -t . -v`
Expected : PASS. Les tests de `TestSequenceDarret` et `TestAutostart` continuent de passer : `on_quit` détruit `self.win` sans savoir de quelle classe il s'agit, et `set_autostart` n'a pas bougé.

- [ ] **Step 4 : vérifier dans l'applet réel**

```bash
pkill -f "bin/marshall-appl[e]t"
~/bin/marshall-applet &
```

Cliquer l'icône et vérifier, enceinte allumée :
1. les trois molettes reflètent l'état réel de l'enceinte ;
2. tourner une molette change le son, **après le relâchement** (debounce à 150 ms) ;
3. tourner la molette **physique** de l'enceinte fait bouger la molette à l'écran ;
4. cliquer un preset allume la bonne pastille ;
5. éteindre l'enceinte → molettes désaturées et « ○ déconnectée — reconnecter » ;
6. rallumer, cliquer « Reconnecter » → ça revient sans attendre les 30 s du watchdog ;
7. « Quitter » → l'icône disparaît immédiatement.

Le journal en cas de doute : `tail -f ~/.local/state/marshall/applet.log`

- [ ] **Step 5 : commit**

```bash
git add marshall-applet
git commit -m "feat: la fenetre de reglages devient la facade dessinee"
```

---

## Tâche 10 : nettoyage

**Files:**
- Modify: `marshall-applet`

- [ ] **Step 1 : purger les restes**

Vérifier qu'il ne reste rien d'inutilisé :

```bash
grep -n "SettingsWindow\|_retop_volume\|RANGES\|Gtk.Scale" marshall-applet
```

Expected : aucune sortie.

```bash
grep -n "BASS_MAX\|TREBLE_MAX\|VOLUME_MAX_FALLBACK" marshall-applet
```

Expected : les trois apparaissent à l'import **et** dans `SpeakerWindow.__init__` — sinon l'import est mort, à retirer.

- [ ] **Step 2 : mettre à jour la docstring de tête**

Le fichier commence par « Icone permanente + menu (etat, presets) et une fenetre de reglages a sliders. » Ce n'est plus vrai. Remplacer :

```python
"""marshall-applet -- pilote une enceinte Marshall Acton III depuis la barre GNOME.

Icone permanente, et une fenetre en facade d'amplificateur : trois molettes
rotatives, les presets, l'etat du lien, l'autostart et l'arret.

Le menu de l'icone existe encore, mais il n'est PLUS le chemin unique vers
quoi que ce soit : sur GNOME 46 / X11 avec ubuntu-appindicators, un clic droit
sur l'icone ne sort aucun menu, ce qui rendait "Quitter" et "Reconnecter"
inatteignables. Toute fonction est desormais dans la fenetre.

REGLE D'ARCHITECTURE : aucun appel BLE depuis un handler GTK.
...
"""
```

(garder tout le reste de la docstring existante, qui est toujours exact)

- [ ] **Step 3 : la suite complète**

Run : `python3 -m unittest discover -s tests -t . -v`
Expected : PASS.

- [ ] **Step 4 : commit**

```bash
git add marshall-applet
git commit -m "refactor: purger les restes de la fenetre a sliders"
```

---

## Tâche 11 : documentation et vérification finale

**Files:**
- Modify: `README.md`, `docs/images/settings.png`
- Modify: `docs/superpowers/specs/2026-07-30-marshall-applet-design.md` (renvoi)

- [ ] **Step 1 : refaire la capture d'écran**

`docs/images/settings.png` montre les anciens sliders. La remplacer :

```bash
~/bin/marshall-applet &
sleep 4
gnome-screenshot --window --file=docs/images/settings.png
```

(cliquer l'icône pour ouvrir la fenêtre et la mettre au premier plan avant la capture)

- [ ] **Step 2 : mettre à jour `README.md`**

Trois endroits :

1. La liste de la section française — remplacer « clic gauche pour les sliders, clic droit pour le menu et les presets » par une description honnête : la fenêtre porte tout, et le menu clic droit ne fonctionne pas partout.
2. La section **Diagnostic** — ajouter une ligne au tableau :

| Symptôme | Piste |
|---|---|
| Le clic droit sur l'icône ne fait rien | Attendu sur GNOME 46. Tout est dans la fenêtre : clic **gauche** sur l'icône |

3. La section **Désinstallation** — mentionner que l'interrupteur « Démarrer avec la session » de la fenêtre retire le même fichier d'autostart.

Et dans le résumé anglais, remplacer « The tray icon gives sliders (left click) and presets (right click) » par une phrase qui n'affirme pas que le clic droit marche.

- [ ] **Step 3 : renvoyer l'ancienne spec vers la nouvelle**

En tête de `docs/superpowers/specs/2026-07-30-marshall-applet-design.md`, sous la ligne « État » :

```markdown
> L'interface décrite ici (fenêtre à sliders, menu de l'icône) a été remplacée.
> Voir [`2026-07-30-refonte-facade-et-arret-design.md`](2026-07-30-refonte-facade-et-arret-design.md).
> Le protocole BLE et les pièges du firmware, eux, restent exacts.
```

- [ ] **Step 4 : vérification finale**

@superpowers:verification-before-completion — coller les sorties, ne rien affirmer sans elles.

```bash
python3 -m unittest discover -s tests -t . -v 2>&1 | tail -5
```

Puis les tests d'intégration, applet arrêté :

```bash
pkill -f "bin/marshall-appl[e]t"
python3 -m unittest tests.test_speaker -v 2>&1 | tail -5
nohup ~/bin/marshall-applet >/dev/null 2>&1 &
```

Enfin, la liste de la tâche 9 step 4 rejouée en entier, plus :

- quitter, **se déconnecter et se reconnecter à la session** avec l'autostart éteint → l'applet ne revient pas ;
- le rallumer, relogin → l'applet revient.

- [ ] **Step 5 : commit**

```bash
git add README.md docs/
git commit -m "docs: la facade dessinee, et le menu clic droit qui ne marche pas partout"
```

---

## Pièges connus

| Piège | Pourquoi il mord | Parade |
|---|---|---|
| Une mise à jour programmée qui déclenche une écriture | Refléter l'état de l'enceinte passerait par `value-changed` → `schedule_write` → écriture vers l'enceinte, en boucle | `set_value_silently` et le drapeau `_loading` de `Facade` — **restauré** en fin de bloc, jamais forcé à `False` |
| Écraser une valeur encore en vol | La molette sauterait en arrière sous le doigt pendant le debounce | `Facade.update` saute les clés présentes dans `pending` |
| `Gtk.Switch.set_active` réémet `notify::active` | L'autostart serait réécrit à chaque rafraîchissement | `if self._loading: return` en tête de `_on_autostart` |
| Un appel BLE depuis un handler GTK | `connect()` bloque jusqu'à 30 s, l'interface gèle, GNOME affiche « ne répond pas » | `GLib.idle_add` pour « Reconnecter », debounce pour les molettes |
| `GLib.source_remove` sur un id mort | Avertissement GLib, voire exception | `_tick` met `_watchdog_source` à `None` en entrée, et `stop_watchdog` teste avant de retirer |
| `close()` avant `disconnect()` | `close()` efface `_dev_path`, dont `disconnect()` a besoin | `stop_watchdog()` d'abord, `close()` en dernier |
| Les tests qui écrivent dans le vrai `~/.config` | Ils casseraient l'autostart de l'utilisateur | `autostart_path()` relit `XDG_CONFIG_HOME` **à chaque appel** |
| `ev.y` au lieu de `ev.y_root` pour le glissé | Le geste casse dès que le pointeur sort de la molette | coordonnées racine |

## Hors scope, rappelé

Wayland ; la 3D rotative (WebKitGTK) ; la fenêtre redimensionnable ; le lettrage en courbes de Bézier ; `marshall-ctl quit` ; les registres BLE inconnus ; une nouvelle icône de barre.
