# Façade dessinée et arrêt de l'applet — design

Date : 2026-07-30
État : validé par l'utilisateur, prêt pour le plan d'implémentation

Suite de [`2026-07-30-marshall-applet-design.md`](2026-07-30-marshall-applet-design.md),
qui reste la référence pour le protocole BLE et les pièges du firmware. Ce
document ne couvre que l'interface et le cycle de vie du process.

## Objectif

Deux besoins remontés après une journée d'usage réel de l'applet :

1. **Pouvoir l'arrêter.** Aucune commande d'arrêt n'est atteignable sur la
   machine cible : l'applet tourne en permanence et revient à chaque ouverture
   de session.
2. **Une interface désirable.** Remplacer les trois sliders GTK par une façade
   d'amplificateur Marshall dessinée, avec des molettes rotatives.

## Le vrai défaut : le menu de l'icône est inatteignable

Vérifié sur la machine cible le 2026-07-30 — GNOME Shell 46, X11, extension
`ubuntu-appindicators` active : **un clic droit sur l'icône de la barre ne
produit rien.** Aucun menu ne s'ouvre.

Le clic gauche, lui, fonctionne et ouvre la fenêtre de réglages.

Or tout le menu est construit dans `_rebuild_menu()` et n'existe **que** là :

| Entrée | Ligne | Conséquence de l'inaccessibilité |
|---|---|---|
| État et valeurs | `marshall-applet:331` | cosmétique — la fenêtre les montre déjà |
| Les quatre presets | `marshall-applet:353` | doublonnés par les boutons de la fenêtre |
| « Réglages… » | `marshall-applet:363` | doublonné par le clic gauche |
| **« Reconnecter »** | `marshall-applet:369` | **enceinte éteinte = applet muet jusqu'au relogin** |
| **« Quitter »** | `marshall-applet:374` | **aucun moyen d'arrêter le process** |

La fenêtre de réglages n'offre ni l'un ni l'autre, et sa croix se contente de la
cacher (`marshall-applet:152`). D'où le symptôme rapporté : « l'icône est
toujours là, sans option pour quitter ».

**Décision : ne pas tenter de réparer le menu.** Le design d'origine documente
déjà (`### Mécanisme d'icône`) que `AyatanaAppIndicator3` et `AppIndicator3` ne
s'enregistrent pas auprès du `StatusNotifierWatcher` de GNOME, et que
`Gtk.StatusIcon` est le seul mécanisme qui affiche quelque chose ici. Le rendu du
menu contextuel dépend de l'extension, hors de notre portée.

Le menu est **conservé** — il fonctionne sur d'autres environnements et ne coûte
rien — mais il cesse d'être le seul chemin vers quoi que ce soit. **Toute
fonction doit être atteignable depuis la fenêtre.**

## Décisions validées

| Sujet | Décision | Écarté |
|---|---|---|
| Direction visuelle | Façade d'ampli vue de face : bandeau de laiton, toile tissée, logo script | Perspective 3/4 de l'enceinte ; gros plan sur le panneau de commandes |
| Contrôles | Molettes rotatives seules, avec valeur chiffrée | Sliders ; molettes + sliders ; molette pour l'EQ et slider pour le volume |
| Technique de rendu | Cairo, en unités séparées peignant chacune son rectangle | Un seul `DrawingArea` monolithique ; WebKitGTK et de la 3D rotative |
| Arrêt | Bouton « Quitter » visible dans la fenêtre | La croix de la fenêtre qui quitte ; une commande `marshall-ctl quit` |
| Persistance de l'arrêt | Interrupteur « Démarrer avec la session » dans la fenêtre | — |
| Taille de fenêtre | Fixe, non redimensionnable | Redimensionnable |

WebKitGTK est le seul chemin vers une enceinte qu'on ferait tourner à la souris.
Écarté : ~150 Mo de dépendance et la fin du « aucune dépendance PyPI, PyGObject
seulement » revendiqué par le README, pour trois molettes.

## Architecture

### Découpage en fichiers

`marshall-applet` fait 495 lignes et mélange trois métiers : l'icône de barre,
l'orchestration BLE, et la fenêtre. Y ajouter une façade dessinée le ferait
exploser.

| Fichier | Rôle | État |
|---|---|---|
| `marshall_ble.py` | protocole et transport BLE | existant, deux corrections |
| `marshall_ui.py` | **tout le dessin et les contrôles** | **nouveau** |
| `marshall-applet` | icône de barre, orchestration, câblage, autostart | existant, allégé |
| `marshall-ctl` | CLI | inchangé |

`marshall_ui.py` ne connaît ni BlueZ, ni `Speaker`, ni D-Bus. Il expose des
widgets et des signaux ; c'est `marshall-applet` qui les relie à l'enceinte.
Cette frontière est ce qui rend l'interface jugeable et testable sans matériel.

### Unités de `marshall_ui.py`

Chaque unité peint **son propre rectangle** et rien d'autre. Pas de système de
coordonnées partagé, donc pas de couplage entre le décor et les contrôles.

**`KnobModel`** — l'arithmétique de la molette, **sans aucun GTK**.

```
KnobModel(maximum, travel_px)
  .value                      -> int, saturé dans [0, maximum]
  .begin_drag()               -> mémorise la valeur de départ
  .drag_to(dy_px)             -> valeur = départ + dy * maximum / travel_px
  .step(delta)                -> ± n crans
  .set_maximum(m)             -> resature la valeur si besoin
```

Le glissé est **relatif** : `begin_drag()` fige la valeur de départ, et
`drag_to()` applique un déplacement depuis ce point. Cliquer sur une molette ne
déplace donc jamais la valeur. Toute la logique délicate vit ici, testable sans
écran.

**`Knob(Gtk.DrawingArea)`** — l'enveloppe GTK autour d'un `KnobModel`.

- Peint la molette : dôme doré, moletage, repère, reflet, ombre portée.
- Entrées : glissé vertical avec capture du pointeur (le geste continue hors du
  widget), molette de souris à **±1 cran exactement**, flèches ↑↓←→ à ±1,
  `Home`/`End` aux bornes. Prend le focus au clic.
- Émet `value-changed` **uniquement quand l'entier change** — un glissé de trois
  pixels sans franchir de cran n'émet rien.
- `set_value_silently(v)` pour les mises à jour venues de l'enceinte : n'émet
  pas. C'est l'équivalent du drapeau `_loading` de l'actuelle `SettingsWindow`,
  ramené à l'échelle du widget.
- `set_sensitive(False)` le peint désaturé et ignore les entrées.

Course de glissé calibrée par usage : **200 px** pour le volume (32 crans, ≈6 px
par cran) et **140 px** pour bass et treble (11 crans, ≈13 px par cran). Sans
cette distinction, le volume serait trois fois plus nerveux que l'EQ.

**`BrassPanel(Gtk.Box)`** — la plaque de laiton. Peint son fond (dégradé de
laiton, biseau, brossage) et contient les trois `Knob`, chacune sous son libellé
gravé et au-dessus de sa valeur chiffrée.

**`Grille(Gtk.DrawingArea)`** — la toile tissée et le logo. Occupe la place
restante.

**`Facade(Gtk.Box)`** — l'assemblage, sur un fond de tolex, plus le pied. Expose
les signaux que la fenêtre relie : `knob-changed(key, value)`,
`preset-chosen(name)`, `reconnect-requested`, `autostart-toggled(bool)`,
`quit-requested`.

### Disposition

Taille fixe ~420 × 330, non redimensionnable — comme aujourd'hui. Le dessin est
vectoriel et supporterait le redimensionnement ; le figer garantit que la façade
tombe juste, et évite les cas limites de mise en page. Ouvrable plus tard.

```
┌──────────────────────────────────────────┐
│ ░░  tolex, liseré doré  ░░░░░░░░░░░░░░░░ │
│  ┌────────────────────────────────────┐  │
│  │ ◉ laiton    ◉         ◉            │  │  BrassPanel
│  │ VOLUME     BASS      TREBLE        │  │
│  │   20        10         7           │  │
│  └────────────────────────────────────┘  │
│  ┌────────────────────────────────────┐  │
│  │ ▨▨▨▨▨▨   Marshall   ▨▨▨▨▨▨▨▨▨▨▨▨▨ │  │  Grille
│  └────────────────────────────────────┘  │
│  [Musique] [Films] [Voix] [Neutre]  ● co │  pied : presets + état
│  ───────────────────────────────────────  │
│  ◯━ Démarrer avec la session    [Quitter] │  bas de fenêtre
└──────────────────────────────────────────┘
```

La profondeur vient uniquement d'effets peints : liseré doré autour de la
façade, ombre interne sur la toile, reflet en haut de chaque molette, biseau sur
la plaque. **Aucun moteur 3D.**

Le pied et le bas de fenêtre restent en widgets GTK ordinaires, habillés par une
feuille CSS GTK. Inutile de peindre des boutons à la main : GTK gère déjà le
survol, le focus et le clavier.

### Logo

Rendu avec Pango, en italique, sur une chaîne de repli
`Z003, URW Chancery L, Brush Script MT, cursive`.

`Z003` (clone d'URW Chancery, livré avec les polices base-35) est présent sur la
machine cible et c'est vers lui que résout `fc-match cursive`. **Fragilité
assumée** : sur une machine sans police calligraphique, le logo retombera sur un
italique quelconque. La façade reste correcte, le lettrage moins juste. Dessiner
le mot en courbes de Bézier serait la solution robuste ; hors scope.

Le lettrage Marshall est une **marque déposée**. On en produit une approximation
typographique pour un outil personnel non affilié, comme le README le précise
déjà. Aucun fichier de police ni aucun visuel du fabricant n'est embarqué dans
le dépôt.

### État déconnecté

Aujourd'hui la fenêtre grise les sliders et affiche « ○ déconnectée ». C'est une
impasse : « Reconnecter » n'existe que dans le menu mort.

Désormais, quand `_connected()` est faux :

- les molettes sont peintes désaturées et n'acceptent plus d'entrée ;
- les presets sont insensibles ;
- **la zone d'état devient un bouton « Reconnecter »**, qui appelle le même
  `_initial_connect` que l'entrée de menu, via `GLib.idle_add`.

Le watchdog continue de tenter la reprise seul toutes les 30 s ; le bouton sert à
ne pas attendre.

## Cycle de vie et arrêt

### Le symptôme au-delà de la découvrabilité

`do_shutdown` (`marshall-applet:255`) vide les écritures en attente, puis appelle
`StopNotify` deux fois et `Disconnect`, avec des délais de garde de 4 s, 4 s et
8 s (`marshall_ble.py:546`). **Jusqu'à ~16 s pendant lesquelles l'icône est
toujours dans la barre.** Même avec un bouton bien visible, ça se lirait comme
« ça ne quitte pas ».

### Séquence d'arrêt

1. **L'interface disparaît d'abord** : `icon.set_visible(False)` et destruction
   de la fenêtre. Effet perçu immédiat.
2. `self.quit()`. `g_application_quit()` **ignore le compteur de `hold()`** — le
   `hold()` posé par `do_activate` n'empêche donc pas la sortie de boucle.
3. `do_shutdown` conserve la libération BLE, mais **bornée** : les délais de
   garde du chemin d'arrêt sont raccourcis pour plafonner l'arrêt à 2–3 s au lieu
   de ~16 s. Le watchdog est coupé **avant** le démontage, pour qu'un cycle ne
   rouvre pas la connexion pendant qu'on la ferme.

L'ordre 1-avant-3 est le cœur du correctif : la libération du canal BLE reste
faite (elle a sa raison, documentée à `marshall-applet:257`), mais elle n'est
plus dans le chemin visible par l'utilisateur.

### Correction d'un défaut latent du watchdog

`close()` remet `_watchdog_on` à `False` (`marshall_ble.py:224`) mais **ne retire
pas la source GLib déjà planifiée**, et `_tick_inner` ne consulte jamais ce
drapeau. Le drapeau est donc inerte :

- un `close()` suivi d'un `start_watchdog()` créerait **deux chaînes de timers**
  concurrentes — exactement ce que l'idempotence de `start_watchdog` cherchait à
  empêcher ;
- un cycle peut se déclencher pendant le démontage de la connexion.

Aujourd'hui invisible, parce que la boucle GLib sort avant le prochain cycle. La
séquence d'arrêt ci-dessus en dépend, donc on le corrige : mémoriser l'id de la
source, la retirer dans `close()`, et sortir de `_tick` si `_watchdog_on` est
faux.

### Autostart

Trois fonctions au niveau module de `marshall-applet` — c'est une préoccupation
de session, ni du protocole ni du dessin :

```
autostart_path()          -> ~/.config/autostart/marshall-applet.desktop
autostart_enabled()       -> bool, le fichier existe
set_autostart(bool)       -> écrit ou supprime le fichier
```

Retirer le fichier plutôt que poser `X-GNOME-Autostart-enabled=false` : c'est
déjà ce que le README documente pour la désinstallation, et ça évite deux
représentations du même état.

`Exec` pointe sur `~/bin/marshall-applet` — le lien posé par `install.sh` — avec
repli sur le chemin réel du script si ce lien n'existe pas (cas d'un lancement
depuis le dépôt).

`install.sh` continue d'écrire ce fichier à l'installation. Le contenu est donc
dupliqué entre le shell et Python : six lignes de `.desktop`, duplication
assumée et signalée par un commentaire de part et d'autre.

L'interrupteur reflète `autostart_enabled()` à l'ouverture de la fenêtre, et non
un état mémorisé — le fichier est la seule source de vérité, et il peut avoir été
retiré à la main.

## Ce qui est réutilisé sans y toucher

Ces comportements ont été acquis contre le matériel ; la refonte les câble
autrement mais ne les réécrit pas.

| Mécanisme | Où | Pourquoi il reste |
|---|---|---|
| Debounce à 150 ms | `schedule_write` / `_flush` | le canal met 1–2 s par commande ; un glissé émet des dizaines de valeurs |
| Regroupement des rafraîchissements, 120 ms | `_refresh` / `_do_refresh` | l'enceinte notifie jusqu'à ~20 fois par seconde à la molette physique |
| Garde sur `_pending` | `SettingsWindow.update` | ne pas faire sauter en arrière une valeur encore en vol sous le doigt |
| Annulation ciblée par registre | `_cancel_pending(keys)` | régler le volume puis cliquer un preset ne doit pas perdre le volume |
| Bass et treble en une trame | `set_eq` | deux fois plus rapide, et pas de course entre les deux écritures |
| Maximum de volume lu sur l'enceinte | `state["max_volume"]` | devient `Knob.set_maximum()` au lieu de `_retop_volume()` |
| Aucun appel BLE bloquant depuis un handler | règle d'architecture en tête de fichier | `value-changed` passe par le debounce, jamais directement au transport |

La règle d'architecture est **inchangée et s'applique aux nouveaux widgets** :
`Knob` n'appelle jamais le transport. Elle émet, le debounce écrit.

## Tests

Tout tourne sans matériel et sans écran, comme le reste du projet.

| Cible | Fichier | Vérifie |
|---|---|---|
| `KnobModel` | `test_pure.py` | glissé relatif, saturation aux bornes, cran de molette à ±1 exactement, `set_maximum` qui resature, course différenciée volume / EQ |
| Fonctions de peinture | `test_paint.py` *(nouveau)* | chaque `paint_*` s'exécute contre une `ImageSurface` à plusieurs tailles sans lever. Atteste l'absence de plantage, **pas** la beauté |
| Séquence d'arrêt | `test_applet.py` | l'icône est masquée **avant** la libération BLE ; le watchdog est coupé avant le démontage ; les écritures en attente partent quand même |
| Watchdog | `test_speaker_faux_bus.py` | `close()` retire la source ; `close()` puis `start_watchdog()` ne crée qu'une chaîne |
| Autostart | `test_applet.py` | activation, désactivation, idempotence, sous un `XDG_CONFIG_HOME` temporaire ; `Exec` résolu |

Cairo rend en mémoire sans afficheur, donc `test_paint.py` tourne partout. Les
widgets GTK, eux, demandent un `Gdk.Display` : c'est précisément pourquoi
l'arithmétique vit dans `KnobModel` et non dans `Knob`.

`tests/test_speaker.py` (matériel, sauté sans enceinte) et
`tests/manual_notify_probe.py` sont inchangés.

## Hors scope

- **Wayland.** `Gtk.StatusIcon` n'a pas d'équivalent ; la limite du README tient.
- **Vraie 3D rotative.** Demanderait WebKitGTK. Le choix est documenté ci-dessus.
- **Fenêtre redimensionnable.** Le dessin le permettrait ; la disposition n'est
  pas conçue pour.
- **Lettrage en courbes de Bézier.** On dépend d'une police calligraphique du
  système.
- **`marshall-ctl quit`.** Écarté explicitement — le bouton suffit.
- **Registres BLE encore inconnus** (source d'entrée, LED, lecture/pause). Une
  façade dessinée les accueillerait bien, mais le protocole n'est pas établi.
- **Nouvelle icône de barre.** L'icône reste `audio-speakers` / `audio-volume-muted`.

## Limites assumées

- Le menu de l'icône reste construit et reste inatteignable sur la machine
  cible. Il est conservé pour les environnements où il fonctionne, mais n'est
  plus le chemin unique vers quoi que ce soit.
- Le logo dépend d'une police du système, et sera moins juste là où aucune police
  calligraphique n'est installée.
- L'arrêt reste borné par BlueZ : si le démon ne répond pas, le process attend
  ses 2–3 s de délai de garde. L'interface, elle, a déjà disparu.
- Une valeur affichée après écriture est la valeur **demandée**, pas une valeur
  confirmée par l'enceinte. Inchangé, et documenté en tête de `marshall-applet`.
