#!/usr/bin/env bash
# Installe marshall-applet et marshall-ctl par LIENS SYMBOLIQUES : une modif du
# depot est prise en compte sans reinstaller.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODDIR="$HOME/.local/share/marshall"
BIN="$HOME/bin"
AUTOSTART="$HOME/.config/autostart"
APPS="$HOME/.local/share/applications"

mkdir -p "$MODDIR" "$BIN" "$AUTOSTART" "$APPS"

chmod +x "$SRC/marshall-applet" "$SRC/marshall-ctl"
ln -sf "$SRC/marshall_ble.py" "$MODDIR/marshall_ble.py"
ln -sf "$SRC/marshall-applet" "$BIN/marshall-applet"
ln -sf "$SRC/marshall-ctl"    "$BIN/marshall-ctl"

# Entree du lanceur d'applications : c'est elle qui rend l'applet trouvable en
# tapant "marshall" (ou "enceinte", "bass"...) dans la recherche GNOME.
# Les Keywords sont indexes par la recherche, pas seulement le Name.
write_desktop() {
    cat > "$1" <<EOF
[Desktop Entry]
Type=Application
Name=Marshall Acton III
GenericName=Egaliseur d'enceinte
Comment=Reglage du volume, du bass et du treble de l'enceinte Marshall
Exec=$BIN/marshall-applet
Icon=audio-speakers
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

# rafraichir l'index du lanceur pour que l'entree soit trouvable tout de suite
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" 2>/dev/null || true

echo "Installe."
echo "  applet    : $BIN/marshall-applet"
echo "  cli       : $BIN/marshall-ctl"
echo "  module    : $MODDIR/marshall_ble.py"
echo "  lanceur   : $APPS/marshall-applet.desktop   (cherche \"marshall\")"
echo "  autostart : $AUTOSTART/marshall-applet.desktop"

case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo
     echo "ATTENTION : $BIN n'est pas dans le PATH. Pour taper juste 'marshall-ctl' :"
     echo "  echo 'export PATH=\"\$HOME/bin:\$PATH\"' >> ~/.zshrc && exec zsh" ;;
esac
