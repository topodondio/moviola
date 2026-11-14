#!/bin/bash
# Installa Moviola sul Mac: doppio clic su questo file.
# - copia il programma in  ~/Library/Application Support/Moviola/app
# - prepara le librerie (serve internet, qualche minuto la prima volta)
# - crea l'app "Moviola" in Applicazioni, con la sua icona
# Si può rilanciare quando si vuole: reinstalla sopra, senza toccare progetti e impostazioni.
cd "$(dirname "$0")"
export PATH="$PATH:/usr/local/bin:/opt/homebrew/bin:/Library/Frameworks/Python.framework/Versions/Current/bin"

fine() { echo; echo "$1"; read -n 1 -s -r -p "Premi un tasto per chiudere."; echo; exit "${2:-0}"; }

[ -f moviola/__init__.py ] || fine "Lancia questo file dalla cartella di Moviola (quella con dentro la cartella 'moviola')." 1
command -v python3 >/dev/null || fine "Python 3 non trovato. Installalo da https://www.python.org/downloads/ e rilancia questo file." 1

BASE="$HOME/Library/Application Support/Moviola"
DEST="$BASE/app"
echo "1/3  Copio il programma in: $DEST"
mkdir -p "$DEST" || fine "Non riesco a creare la cartella del programma." 1
rsync -a --delete --exclude ".git" --exclude "__pycache__" --exclude ".DS_Store" ./ "$DEST/" \
  || fine "Copia non riuscita." 1
chmod +x "$DEST/avvia_mac.command" "$DEST/installa_mac.command" 2>/dev/null
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null

echo "2/3  Preparo le librerie (la prima volta ci vuole qualche minuto)…"
VENV="$BASE/venv"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV" || fine "Creazione dell'ambiente Python non riuscita." 1
fi
if ! cmp -s "$DEST/requirements.txt" "$VENV/requirements.installati.txt"; then
  "$VENV/bin/python" -m pip install --upgrade pip
  "$VENV/bin/python" -m pip install -r "$DEST/requirements.txt" \
    || fine "Installazione delle librerie non riuscita: controlla la connessione e rilancia questo file." 1
  cp "$DEST/requirements.txt" "$VENV/requirements.installati.txt"
fi

echo "3/3  Creo l'app Moviola…"
APPS="/Applications"
[ -w "$APPS" ] || { APPS="$HOME/Applications"; mkdir -p "$APPS"; }
APP="$APPS/Moviola.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" || fine "Non riesco a creare l'app in $APPS." 1
cp "$DEST/moviola/risorse/icona.icns" "$APP/Contents/Resources/Moviola.icns"
VERS=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$DEST/moviola/__init__.py")
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>Moviola</string>
<key>CFBundleDisplayName</key><string>Moviola</string>
<key>CFBundleIdentifier</key><string>it.moviola.avvio</string>
<key>CFBundleExecutable</key><string>Moviola</string>
<key>CFBundleIconFile</key><string>Moviola</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>${VERS:-1.0}</string>
<key>CFBundleVersion</key><string>${VERS:-1.0}</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
cat > "$APP/Contents/MacOS/Moviola" <<'AVVIO'
#!/bin/bash
# Avvia la copia installata di Moviola (si aggiorna da sola da GitHub).
exec /bin/bash "$HOME/Library/Application Support/Moviola/app/avvia_mac.command"
AVVIO
chmod +x "$APP/Contents/MacOS/Moviola"
touch "$APP"

echo
echo "Fatto: Moviola ${VERS} è installata."
echo "La trovi in $APPS (e con Spotlight: Cmd+Spazio, scrivi Moviola)."
echo "Puoi trascinarla nel Dock. Questa cartella scaricata ora si può cestinare."
read -r -p "Vuoi aprire Moviola adesso? [S/n] " R
case "$R" in n|N) ;; *) open "$APP" ;; esac
