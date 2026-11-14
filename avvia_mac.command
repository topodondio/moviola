#!/bin/bash
# Avvio di Moviola su Mac: doppio clic su questo file (o sull'app Moviola, se
# l'hai installata con installa_mac.command).
# Al primo avvio crea un ambiente Python dedicato e installa le librerie
# (serve internet, qualche minuto). Le volte successive parte subito.
cd "$(dirname "$0")"
export PATH="$PATH:/usr/local/bin:/opt/homebrew/bin:/Library/Frameworks/Python.framework/Versions/Current/bin"

avviso() {  # messaggio a chi guarda: nel Terminale, o come notifica se avviata dall'app
  echo "$1"
  [ -t 1 ] || osascript -e "display notification \"$1\" with title \"Moviola\"" >/dev/null 2>&1
}
errore() {
  echo "$1"
  if [ -t 0 ]; then read -n 1 -s -r -p "Premi un tasto per chiudere."
  else osascript -e "display dialog \"$1\" with title \"Moviola\" buttons {\"OK\"} default button 1" >/dev/null 2>&1
  fi
  exit 1
}

# L'ambiente sta fuori dalla cartella del programma (così non finisce su iCloud o GitHub)
VENV="$HOME/Library/Application Support/Moviola/venv"
if [ ! -x "$VENV/bin/python" ]; then
  PY=$(command -v python3)
  [ -z "$PY" ] && errore "Python 3 non trovato. Installalo da https://www.python.org/downloads/ e riprova."
  avviso "Primo avvio: preparo l'ambiente Python…"
  mkdir -p "$(dirname "$VENV")"
  "$PY" -m venv "$VENV" || errore "Creazione dell'ambiente Python non riuscita."
fi
VPY="$VENV/bin/python"

if ! cmp -s requirements.txt "$VENV/requirements.installati.txt"; then
  avviso "Installo o aggiorno le librerie: può volerci qualche minuto…"
  "$VPY" -m pip install --upgrade pip
  if "$VPY" -m pip install -r requirements.txt; then
    cp requirements.txt "$VENV/requirements.installati.txt"
  else
    errore "Installazione delle librerie non riuscita: controlla la connessione e riprova."
  fi
fi

exec "$VPY" -m moviola "$@"
