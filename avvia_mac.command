#!/bin/bash
# Avvio di Moviola su Mac: doppio clic su questo file.
# Al primo avvio crea un ambiente Python dedicato e installa le librerie
# (serve internet, qualche minuto). Le volte successive parte subito.
cd "$(dirname "$0")"

PY=$(command -v python3)
if [ -z "$PY" ]; then
  echo "Python 3 non trovato. Installalo da https://www.python.org/downloads/ e riprova."
  read -n 1 -s -r -p "Premi un tasto per chiudere."
  exit 1
fi

# L'ambiente sta fuori dalla cartella del progetto (così non finisce su iCloud o GitHub)
VENV="$HOME/Library/Application Support/Moviola/venv"
if [ ! -x "$VENV/bin/python" ]; then
  echo "Primo avvio: preparo l'ambiente Python…"
  mkdir -p "$(dirname "$VENV")"
  "$PY" -m venv "$VENV" || { echo "Creazione ambiente non riuscita."; read -n 1 -s -r; exit 1; }
fi
VPY="$VENV/bin/python"

if ! cmp -s requirements.txt "$VENV/requirements.installati.txt"; then
  echo "Installo/aggiorno le librerie (PySide6, PyAV, NumPy, FFmpeg…)…"
  "$VPY" -m pip install --upgrade pip
  if "$VPY" -m pip install -r requirements.txt; then
    cp requirements.txt "$VENV/requirements.installati.txt"
  else
    echo "Installazione non riuscita: controlla la connessione e riprova."
    read -n 1 -s -r -p "Premi un tasto per chiudere."
    exit 1
  fi
fi

exec "$VPY" -m moviola "$@"
