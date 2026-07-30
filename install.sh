#!/usr/bin/env bash
# Installe marshall-applet et marshall-ctl par LIENS SYMBOLIQUES : une modif du
# depot est prise en compte sans reinstaller.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODDIR="$HOME/.local/share/marshall"
BIN="$HOME/bin"
AUTOSTART="$HOME/.config/autostart"

mkdir -p "$MODDIR" "$BIN" "$AUTOSTART"

chmod +x "$SRC/marshall-applet" "$SRC/marshall-ctl"
ln -sf "$SRC/marshall_ble.py" "$MODDIR/marshall_ble.py"
ln -sf "$SRC/marshall-applet" "$BIN/marshall-applet"
ln -sf "$SRC/marshall-ctl"    "$BIN/marshall-ctl"

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
echo "  applet    : $BIN/marshall-applet"
echo "  cli       : $BIN/marshall-ctl"
echo "  module    : $MODDIR/marshall_ble.py"
echo "  autostart : $AUTOSTART/marshall-applet.desktop"

case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo
     echo "ATTENTION : $BIN n'est pas dans le PATH. Pour taper juste 'marshall-ctl' :"
     echo "  echo 'export PATH=\"\$HOME/bin:\$PATH\"' >> ~/.zshrc && exec zsh" ;;
esac
