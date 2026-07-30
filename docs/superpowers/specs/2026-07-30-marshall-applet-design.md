# Applet Marshall Acton III pour GNOME — design

Date : 2026-07-30
État : validé par l'utilisateur, prêt pour le plan d'implémentation

## Objectif

Régler le volume, le bass et le treble d'une enceinte Marshall Acton III depuis
Ubuntu/GNOME, sans toucher aux molettes physiques et sans passer par l'application
mobile Marshall (qui n'existe pas sous Linux).

Un CLI fonctionnel existe déjà (`marshall-ctl`). Ce document couvre l'ajout d'un
applet de barre système, et l'unification du code protocole entre les deux.

## Contexte matériel et protocole

Établi expérimentalement le 2026-07-30 sur le matériel cible.

L'enceinte expose **deux identités Bluetooth distinctes** :

| Identité | Adresse | Usage |
|---|---|---|
| BR/EDR | `74:68:59:6F:AD:B1` | audio A2DP — appairée, **ne jamais y toucher** |
| BLE | privée tournante (`C1:3F:…`, `C9:B0:…`) | canal de contrôle |

Service de contrôle `0000fccd-0000-1000-8000-00805f9b34fb`, caractéristiques
`0000000N-1337-1dea-feed-c0ffee70c0de` :

| Registre | Propriétés | Rôle | Format |
|---|---|---|---|
| `0x07` | read,write,notify | Volume | 1 octet, 0–31 |
| `0x08` | read | Volume max | 1 octet (=31) |
| `0x0f` | read,write,notify | EQ | 5 octets `[bass, 0xff, 0xff, 0xff, treble]`, 0–10 |
| `0x0a` | read,notify | Now-playing | muet en lecture |
| `0x01` `0x09` `0x1b` `0x1e` `0x1f` | divers | non identifiés | valeurs `00`/`01` au repos |

L'Acton III n'expose que les deux bandes extrêmes d'un EQ 5 bandes
(160/400/1k/2.5k/6.25k) ; les trois du milieu restent `0xff` = intouchées.

### Cinq pièges, tous absents de la documentation du projet amont

Le projet [`anpct/marshall-acton3-ble`](https://github.com/anpct/marshall-acton3-ble)
cible le bon modèle et documente correctement les registres, mais ne fonctionne
pas tel quel sous Linux sur ce firmware :

1. **Adresse BLE privée tournante.** Passer une adresse figée à un client BLE
   échoue toujours, et la connexion *pend* au lieu d'échouer. Il faut résoudre
   le device par son service, jamais par une adresse en dur.
2. **Plusieurs identités LE actives simultanément**, et toutes n'exposent pas
   `fccd`. Il faut les essayer une par une.
3. **Bonding LE requis, avec un agent d'authentification.** `bleak.pair()` rend
   `AuthenticationFailed` car il n'en fournit pas. Il faut `bluetoothctl` avec
   `agent on` + `default-agent` et un `scan on` maintenu pendant le `pair`.
   Aucune manipulation physique de l'enceinte n'est nécessaire.
4. **Le registre EQ `0x0f` ne répond pas à `ReadValue`** — l'appel pend. Mais il
   **pousse sa valeur dès `StartNotify`**. L'abonnement sert donc de lecture.
   C'est le défaut de fond du projet amont, qui utilise `read_gatt_char`. Les
   autres registres répondent normalement en lecture.
5. **`bleak` est inutilisable ici** : `BleakDeviceNotFoundError` sur une adresse
   LE *random* bondée que BlueZ voit pourtant connectée. Et
   `bluetoothctl gatt.write` rend `NotSupported` sur un payload multi-octets.
   Solution : D-Bus direct sur BlueZ (`GattCharacteristic1.WriteValue` avec
   `{"type": "request"}`).

## Environnement cible

Ubuntu 24.04.2 LTS, GNOME sur X11. GTK3 et `AyatanaAppIndicator3 0.1`
disponibles, extension `ubuntu-appindicators@ubuntu.com` déjà active.

**Gio.DBus (PyGObject) a été validé expérimentalement** contre BlueZ :
lecture du volume, lecture de l'EQ via `StartNotify`, écriture et restauration,
tout en boucle GLib. L'applet n'a donc besoin d'**aucune dépendance
supplémentaire** — ni `dbus-fast`, ni asyncio, ni pont entre deux boucles
d'événements.

## Décisions validées

| Sujet | Décision |
|---|---|
| Interaction | Hybride : icône permanente + menu, et une fenêtre de réglages à sliders |
| Connexion BLE | **Maintenue en permanence** tant que l'applet tourne, reconnexion automatique silencieuse |
| App mobile | Non prise en compte — l'utilisateur ne s'en sert pas |
| Presets | 4, fixes : Neutre 5/5 · Films 8/6 · Musique 10/7 · Voix-YouTube 3/8 |
| Autostart | Oui, via `~/.config/autostart/` |
| Backend D-Bus | Gio/GLib, module partagé entre applet et CLI |

`marshall-ctl` est **réécrit** sur le module partagé, ce qui supprime le venv
`dbus-fast` et laisse une seule implémentation du protocole.

Les réglages d'origine de l'utilisateur, à préserver : **bass=10, treble=7,
volume=12**. Le preset « Musique » les reprend exactement.

## Architecture

```
~/Bureau/marshall-applet/          (dépôt git, source)
├── marshall_ble.py                module protocole (Gio)
├── marshall-applet                applet GTK3 + AppIndicator
├── marshall-ctl                   CLI
├── install.sh                     liens vers ~/bin + autostart
└── docs/superpowers/specs/

installé vers :
~/bin/marshall-applet, ~/bin/marshall-ctl
~/.local/share/marshall/marshall_ble.py
~/.config/autostart/marshall-applet.desktop
```

### `marshall_ble.py` — module protocole

Une seule responsabilité : parler à l'enceinte. Aucune notion d'UI.

```python
class Speaker:
    def connect() -> bool             # découverte + Connect + attente ServicesResolved
    def is_connected() -> bool
    def get_state() -> dict           # {volume, max_volume, bass, treble}
    def set_volume(v: int)            # borné 0..max
    def set_bass(v: int)              # borné 0..10, préserve le treble
    def set_treble(v: int)            # borné 0..10, préserve le bass
    def subscribe(callback)           # notifié des changements, y compris molettes physiques
```

Le `callback` de `subscribe` est **toujours invoqué sur la boucle principale
GLib**, jamais depuis un thread : l'UI peut donc y toucher directement sans
`idle_add`. C'est possible parce que Gio délivre déjà ses signaux sur la boucle.

Points internes :
- découverte par `GetManagedObjects` en cherchant la caractéristique EQ ;
  si absente, recherche d'un `Device1` appairé nommé « Acton » puis `Connect()`
- `read()` tente `ReadValue`, avec repli sur `StartNotify` (contournement du `0x0f`)
- `set_bass`/`set_treble` font un read-modify-write pour ne pas écraser l'autre bande
- `WriteValue` avec `type=request`, repli `command`
- abonnement permanent aux `PropertiesChanged` de `0x07` et `0x0f`
- reconnexion avec backoff (1s, 2s, 5s, 10s, plafonné à 30s)

Testable en CLI, sans GUI.

### `marshall-applet` — UI

Icône `AyatanaAppIndicator3` avec trois états visuels : connectée, connexion en
cours, déconnectée.

Menu :

```
┌────────────────────────────┐
│ Acton III      ● connectée │   non cliquable, état
│ vol 12 · bass 10 · tr 7    │   non cliquable, valeurs
├────────────────────────────┤
│   Neutre                   │   radio, cochée si valeurs exactes
│   Films                    │
│   Musique                  │
│   Voix / podcast           │
├────────────────────────────┤
│   Réglages…                │
│   Reconnecter              │   visible seulement si déconnectée
│   Quitter                  │
└────────────────────────────┘
```

Fenêtre de réglages (une seule instance, réutilisée) :

```
┌── Marshall Acton III ────────┐
│ Volume  ─────●──────  12/31  │
│ Bass    ──────────●   10/10  │
│ Treble  ───────●───    7/10  │
│                              │
│ [Neutre] [Films] [Musique]   │
│ [Voix / podcast]             │
│                              │
│ ● connectée                  │
└──────────────────────────────┘
```

### Flux

**Démarrage.** Icône en état « connexion », UI immédiatement affichée et
réactive. La connexion se fait sans bloquer la boucle GLib. Une fois établie,
lecture de l'état et abonnement aux notifications.

**Slider déplacé.** Envoi **debounce 150 ms** après le dernier mouvement. Sans
cela, un glissement produit des dizaines d'événements sur un canal à 1–2 s par
commande : le lien sature et l'affichage se désynchronise de la réalité. Le
délai est imperceptible à l'usage.

**Clic sur un preset.** Envoi immédiat, sans debounce. Un preset ne touche que
le **bass et le treble** — jamais le volume, qui reste sous le contrôle exclusif
de l'utilisateur. Un preset est coché dans le menu quand bass et treble
correspondent exactement à ses valeurs.

**Changement externe.** Les notifications remontent les actions faites sur les
molettes physiques ; sliders et menu se mettent à jour tout seuls.

**Course entre un debounce et un preset.** Cliquer un preset alors qu'un envoi
de slider est encore en attente doit **annuler le debounce pendant**, sinon la
valeur du slider écraserait le preset 150 ms plus tard. Même règle pour un
changement externe arrivant par notification : il annule l'envoi en attente,
puisque l'utilisateur vient d'agir sur l'enceinte elle-même.

### Instance unique

L'autostart plus un lancement manuel donneraient deux applets, donc **deux
clients sur un canal BLE qui n'en accepte qu'un** — ils se voleraient la
connexion en boucle. L'applet prend donc un verrou au démarrage
(`Gtk.Application` avec un `application_id`, qui fournit l'unicité nativement
via D-Bus). Une seconde instance présente la fenêtre de réglages de la première
au lieu de démarrer.

### Emplacement du module

`marshall_ble.py` est installé dans `~/.local/share/marshall/`, qui n'est pas
sur le `sys.path` par défaut. Les deux exécutables l'ajoutent explicitement
avant l'import, plutôt que de dépendre d'un `PYTHONPATH` que l'utilisateur
devrait configurer. `install.sh` pose des **liens symboliques** depuis le dépôt
vers `~/bin`, pour qu'un `git pull` suffise à mettre à jour.

### Icône

Icônes du thème système, pour rester cohérent avec GNOME et éviter d'embarquer
des assets : `audio-speakers` (connectée) et `audio-volume-muted` (déconnectée
ou connexion en cours). Vérifiées présentes dans le thème Yaru-purple.

### Mécanisme d'icône : Gtk.StatusIcon, pas AppIndicator

**Corrigé à l'implémentation.** Le design initial prévoyait
`AyatanaAppIndicator3`. Mesuré sur la machine cible (Ubuntu 24.04, GNOME X11,
extension `ubuntu-appindicators` active) :

| Approche | Résultat |
|---|---|
| **`Gtk.StatusIcon`** | **fonctionne** — apparition/disparition vérifiées par capture d'écran |
| `AppIndicator3` 0.1 (Canonical) | fonctionne également (voir rectification ci-dessous) |

**Rectification.** Un premier diagnostic avait conclu qu'aucune variante
d'AppIndicator ne fonctionnait. C'était **faux** : la conclusion reposait sur
l'absence d'un nom D-Bus `org.kde.StatusNotifierItem-*` que `libappindicator`
n'utilise pas, et son icône avait été prise pour celle d'une application tierce
— dans le thème Yaru, `audio-speakers` se dessine en cercles concentriques,
facile à confondre. `AppIndicator3` s'affiche bien.

`Gtk.StatusIcon` est néanmoins retenu : il fonctionne, et son menu est un vrai
`Gtk.Menu` rendu localement plutôt que du DBusMenu. Migrer vers AppIndicator
reste une option valable, celui-ci n'étant pas déprécié.

Note : `Gtk.StatusIcon.is_embedded()` renvoie `False` alors que l'icône
s'affiche correctement — l'API dépréciée est trompeuse sur ce point.

`Gtk.StatusIcon` est déprécié depuis GTK 3.14 ; les avertissements sont filtrés
explicitement. Conséquence favorable : son menu est un **vrai `Gtk.Menu` local**
et non du DBusMenu, donc la contrainte « pas de sliders dans un menu de barre »
tombe. Non exploité pour l'instant — la fenêtre de réglages reste le lieu du
réglage fin, comme validé.

### Démarrage non bloquant

`Speaker.connect()` est synchrone et peut bloquer la boucle GLib jusqu'à 30 s.
La connexion initiale est donc différée de 2 s après la création de l'icône,
pour que celle-ci s'installe dans la barre d'abord — sinon l'applet paraît mort
au lancement.

## Gestion d'erreurs

| Situation | Comportement |
|---|---|
| Enceinte éteinte / hors de portée | Icône grisée, entrée « Reconnecter », reconnexion auto avec backoff 1s→2s→5s→10s→30s. Le palier de 30 s est conservé indéfiniment : l'enceinte peut être rallumée à tout moment et l'applet doit la reprendre sans intervention. Coût négligeable, un appel D-Bus local toutes les 30 s. |
| Endormie après 10 min d'inactivité | Reconnexion transparente à la prochaine action |
| Écriture refusée | L'affichage revient à la valeur réelle plutôt que de mentir |
| Identité BLE non appairée | Message clair renvoyant vers la procédure d'appairage |
| Appel D-Bus lent | Timeouts courts, jamais de blocage de l'UI |

Principe : l'UI ne montre jamais une valeur qu'elle n'a pas vue confirmée par
l'enceinte.

## Tests

- **Module, sans GUI** : connexion, lecture d'état, écriture bass/treble/volume
  avec vérification par relecture, restauration systématique des valeurs
  d'origine, comportement quand l'enceinte est absente.
- **Bornes** : valeurs négatives, au-delà du max, non numériques.
- **Préservation** : `set_bass` ne doit pas modifier le treble, et inversement.
- **Debounce** : un glissement de slider ne doit produire qu'**une seule**
  écriture ; un preset cliqué pendant un debounce doit gagner.
- **Instance unique** : lancer l'applet deux fois ne doit pas créer un second
  client BLE.
- **Manuel** : chaque preset, glissement de slider, rotation d'une molette
  physique (l'UI doit suivre), extinction/rallumage de l'enceinte, autostart
  après réouverture de session.

## Hors scope

- Registres non identifiés (`0x01`, `0x09`, `0x1b`, `0x1e`, `0x1f`, `0x0a`) —
  mappables plus tard avec `probe.py --monitor` du projet amont en manipulant
  les commandes physiques.
- Presets modifiables par l'utilisateur.
- Support d'autres modèles Marshall.
- Wayland (la session cible est X11).
- EQ logiciel PipeWire/EasyEffects — approche alternative écartée, elle ne
  pilote pas le DSP de l'enceinte.

## Limites assumées

- Latence 1–2 s par commande, inhérente au canal BLE.
- Première connexion ~11 s.
- Le canal BLE n'accepte qu'un client : l'application mobile Marshall ne pourra
  pas se connecter pendant que l'applet tourne. Accepté explicitement.
