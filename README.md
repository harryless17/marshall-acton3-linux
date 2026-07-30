# marshall-applet

Régler le **volume, le bass et le treble** d'une enceinte **Marshall Acton III**
depuis Linux, sans toucher aux molettes physiques et sans passer par
l'application mobile Marshall (qui n'existe pas sous Linux).

- une **icône dans la barre système** : clic gauche pour les sliders, clic droit
  pour le menu et les presets ;
- un **CLI**, `marshall-ctl` ;
- les changements faits sur les molettes de l'enceinte remontent en direct dans
  l'interface.

Projet personnel, non affilié à Marshall ni à Zound Industries. Le protocole a
été obtenu par reverse engineering, en partant de
[`anpct/marshall-acton3-ble`](https://github.com/anpct/marshall-acton3-ble).

## Prérequis réels

Testé sur **Ubuntu 24.04, GNOME sur X11**.

| Dépendance | Paquet Debian/Ubuntu | Pourquoi |
|---|---|---|
| Python ≥ 3.9 | `python3` | |
| PyGObject | `python3-gi` | tout passe par Gio/GLib |
| Typelib GTK 3 | `gir1.2-gtk-3.0` | **ne vient pas** avec `python3-gi` |
| BlueZ | `bluez` | `org.bluez` sur le bus système, et `bluetoothctl` pour l'appairage initial |
| Un hôte d'icônes de notification | `gnome-shell-extension-appindicator` | **sinon l'applet tourne mais reste invisible** |

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 bluez gnome-shell-extension-appindicator
```

Aucune dépendance PyPI, aucun environnement virtuel.

Deux limites d'environnement à connaître :

- **X11 seulement.** L'applet utilise `Gtk.StatusIcon`, qui n'a pas d'équivalent
  sous Wayland. Le CLI, lui, fonctionne partout.
- L'extension d'icônes doit être **active** (`gnome-extensions list --enabled`).
  Sans elle, l'applet démarre, se connecte, et n'affiche rien.

## Installation

```bash
git clone <ce-depot> ~/Bureau/marshall-applet
cd ~/Bureau/marshall-applet
./install.sh
```

`install.sh` pose des **liens symboliques** vers le dépôt, donc modifier le
source suffit — pas besoin de réinstaller. En contrepartie :

> ⚠️ **Si tu déplaces ou renommes le dossier du dépôt, rejoue `./install.sh`.**
> Sinon les liens pointent dans le vide et l'applet ne démarre plus, sans aucun
> message — tu constaterais juste que l'icône a disparu.

Il installe :

| Chemin | Rôle |
|---|---|
| `~/bin/marshall-applet`, `~/bin/marshall-ctl` | liens vers le dépôt |
| `~/.local/share/marshall/marshall_ble.py` | lien vers le module |
| `~/.local/share/applications/marshall-applet.desktop` | entrée du lanceur |
| `~/.config/autostart/marshall-applet.desktop` | démarrage à l'ouverture de session |

`~/bin` n'est pas forcément dans ton `PATH`. Pour taper `marshall-ctl` sans le
chemin complet :

```bash
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.zshrc && exec zsh
```

## Appairage de l'identité BLE — à faire une fois

L'enceinte expose **deux identités Bluetooth distinctes** :

- `ACTON III` — l'audio (A2DP). Probablement déjà appairée. **Ne jamais y
  toucher** : la retirer casserait le son.
- `ACTON III [LE]` — le canal de contrôle. C'est celle-ci qu'il faut appairer,
  séparément, et **avec un agent d'authentification** — sinon BlueZ refuse.

```bash
# 1. repérer l'adresse BLE (elle s'affiche comme "ACTON III [LE]")
bluetoothctl --timeout 12 scan le | grep -i acton

# 2. appairer, en gardant un scan actif pour que l'adresse reste valide
#    (l'adresse BLE est privée et tournante)
{ echo "agent on"; sleep 1; echo "default-agent"; sleep 1; \
  echo "scan on"; sleep 8; echo "pair XX:XX:XX:XX:XX:XX"; sleep 25; \
  echo "quit"; } | bluetoothctl
# attendre "Pairing successful"

# 3. vérifier
~/bin/marshall-ctl
```

Aucune manipulation physique de l'enceinte n'est nécessaire.

Cette procédure est aussi disponible hors ligne via `marshall-ctl --setup`.

## Usage

```bash
marshall-ctl                   # état courant
marshall-ctl bass 6            # 0..10
marshall-ctl treble 8          # 0..10
marshall-ctl volume 20         # 0..31
marshall-ctl bass 6 treble 8   # plusieurs réglages d'un coup
marshall-ctl preset Musique    # applique un preset
marshall-ctl presets           # liste les presets
```

Presets (bass / treble — ils ne touchent **jamais** au volume) :

| Preset | bass | treble |
|---|---|---|
| Neutre | 5 | 5 |
| Films | 8 | 6 |
| Musique | 10 | 7 |
| Voix / podcast | 3 | 8 |

Trouver l'applet : **Super**, puis `marshall` — ou `acton`, `enceinte`, `bass`,
`treble`, `égaliseur`…

## Tests

```bash
python3 -m unittest discover -s tests -t . -v
```

Les tests d'intégration (`tests/test_speaker.py`) demandent l'enceinte allumée et
appairée ; ils sont **sautés automatiquement** sinon. Les tests purs et ceux de
l'applet tournent sans matériel.

> ⚠️ **Arrête l'applet avant de lancer les tests d'intégration.** Le canal BLE
> n'accepte qu'un seul client : l'applet le monopolise et les tests échoueraient.
> ```bash
> pkill -f "bin/marshall-appl[e]t"     # les crochets évitent que pkill se tue lui-même
> python3 -m unittest discover -s tests -t .
> nohup ~/bin/marshall-applet >/dev/null 2>&1 &
> ```

`tests/manual_notify_probe.py` est une sonde manuelle : elle vérifie que
l'enceinte signale bien les changements faits sur ses molettes physiques.

## Diagnostic

L'applet journalise dans **`~/.local/state/marshall/applet.log`** (rotatif). Lancé
par l'autostart, il n'a aucune sortie visible : c'est le premier endroit à
regarder si l'icône n'apparaît pas ou si l'interface semble figée.

| Symptôme | Piste |
|---|---|
| Aucune icône, mais le process tourne | Extension d'icônes désactivée, ou session Wayland |
| Icône barrée en permanence | Enceinte éteinte, hors de portée, ou identité BLE non appairée |
| « Enceinte introuvable » au CLI | Idem, ou l'applet monopolise le canal |
| L'icône a disparu après un déplacement du dossier | Rejouer `./install.sh` |
| L'interface se figue quelques secondes | Attendu : les appels BLE sont synchrones |

L'enceinte s'endort après ~10 min d'inactivité ; l'applet la reprend
automatiquement (sondage toutes les 30 s, backoff en cas d'échec).

## Désinstallation

```bash
rm -f ~/bin/marshall-applet ~/bin/marshall-ctl
rm -f ~/.local/share/applications/marshall-applet.desktop
rm -f ~/.config/autostart/marshall-applet.desktop
rm -rf ~/.local/share/marshall ~/.local/state/marshall
```

Pour retirer aussi l'appairage BLE — **uniquement l'adresse `[LE]`** :

```bash
bluetoothctl remove <adresse-BLE>
```

## Le protocole, en bref

Service de contrôle `0000fccd-0000-1000-8000-00805f9b34fb`, caractéristiques
`0000000N-1337-1dea-feed-c0ffee70c0de` :

| Registre | Rôle | Format |
|---|---|---|
| `0x07` | volume | 1 octet, 0–31 |
| `0x08` | volume max | lecture seule (=31) |
| `0x0f` | EQ | 5 octets `[bass, 0xff, 0xff, 0xff, treble]`, 0–10 |

L'Acton III n'expose que les deux bandes extrêmes d'un EQ 5 bandes ; les trois
du milieu doivent rester `0xff` (« intouchées »).

Les pièges du firmware, et pourquoi le code est écrit comme il est, sont
documentés dans
[`docs/superpowers/specs/`](docs/superpowers/specs/2026-07-30-marshall-applet-design.md).
Le plan d'implémentation qui l'accompagne est un **document historique périmé**.
