#!/usr/bin/env bash
# Installe marshall-applet et marshall-ctl par LIENS SYMBOLIQUES : une modif du
# depot est prise en compte sans reinstaller.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODDIR="$HOME/.local/share/marshall"
BIN="$HOME/bin"
AUTOSTART="$HOME/.config/autostart"
APPS="$HOME/.local/share/applications"
# Meme repli que install_icon_theme() cote Python, et pour la meme raison : c'est
# XDG_DATA_HOME qui decide, ~/.local/share n'est que son defaut.
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"

mkdir -p "$MODDIR" "$BIN" "$AUTOSTART" "$APPS"

chmod +x "$SRC/marshall-applet" "$SRC/marshall-ctl"
ln -sf "$SRC/marshall_ble.py" "$MODDIR/marshall_ble.py"
ln -sf "$SRC/marshall-applet" "$BIN/marshall-applet"
ln -sf "$SRC/marshall-ctl"    "$BIN/marshall-ctl"

# Entree du lanceur d'applications : c'est elle qui rend l'applet trouvable en
# tapant "marshall" (ou "enceinte", "bass"...) dans la recherche GNOME.
# Les Keywords sont indexes par la recherche, pas seulement le Name.
#
# ATTENTION : ce contenu est duplique dans AUTOSTART_DESKTOP, en tete de
# marshall-applet -- l'interrupteur "Demarrer avec la session" de la fenetre
# reecrit ce meme fichier. Si tu touches a l'un, touche a l'autre.
write_desktop() {
    cat > "$1" <<EOF
[Desktop Entry]
Type=Application
Name=Marshall Acton III
GenericName=Egaliseur d'enceinte
Comment=Reglage du volume, du bass et du treble de l'enceinte Marshall
Exec=$BIN/marshall-applet
Icon=marshall-applet
Terminal=false
Categories=AudioVideo;Audio;Mixer;
Keywords=marshall;acton;enceinte;speaker;bass;basses;treble;aigus;egaliseur;equalizer;volume;son;audio;bluetooth;
StartupNotify=false
EOF
}

write_desktop "$APPS/marshall-applet.desktop"
write_desktop "$AUTOSTART/marshall-applet.desktop"
echo "X-GNOME-Autostart-enabled=true" >> "$AUTOSTART/marshall-applet.desktop"
chmod +x "$APPS/marshall-applet.desktop"

# Le Icon= ci-dessus est un NOM DE THEME, pas un chemin : il faut donc poser le
# glyphe dans le theme hicolor de l'utilisateur, sinon l'entree du lanceur
# s'affiche avec l'icone generique de repli. Les six PNG sont rendus par le meme
# paint_m que l'icone de la barre -- une seule source pour le glyphe, aucun SVG
# a cote qui pourrait diverger.
#
# Apres les liens, et non avant : si PyGObject manque, l'import echoue, set -e
# arrete ici et "Installe." ne s'affiche pas -- mais les binaires, eux, sont
# deja en place et l'applet dira lui-meme ce qui lui manque.
python3 - "$SRC" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import marshall_ui
marshall_ui.install_icon_theme()
PY

# Rafraichir les deux index, pour que l'entree et son icone soient prises tout de
# suite. Les deux commandes sont gardees : elles n'existent pas partout, et leur
# absence n'est pas une erreur d'installation -- GNOME rattrape a la session
# suivante.
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f "$ICONS" 2>/dev/null || true

echo "Installe."
echo "  applet    : $BIN/marshall-applet"
echo "  cli       : $BIN/marshall-ctl"
echo "  module    : $MODDIR/marshall_ble.py"
echo "  lanceur   : $APPS/marshall-applet.desktop   (cherche \"marshall\")"
echo "  autostart : $AUTOSTART/marshall-applet.desktop"
echo "  icone     : $ICONS/<taille>/apps/marshall-applet.png"

case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo
     echo "ATTENTION : $BIN n'est pas dans le PATH. Pour taper juste 'marshall-ctl' :"
     echo "  echo 'export PATH=\"\$HOME/bin:\$PATH\"' >> ~/.zshrc && exec zsh" ;;
esac
