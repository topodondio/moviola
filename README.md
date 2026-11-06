# Moviola

Editor video personale, leggero e **non distruttivo**, scritto in Python.
Pensato per chi viene dalla fotografia: si lavora "a clip" e il colore si
corregge con comandi fotografici (esposizione, contrasto, bilanciamento del
bianco, curva, HSL per colore), senza nodi.

> Stato: **prototipo 0.3**. Le funzioni base ci sono tutte; da collaudare
> sul campo su Mac e Windows.

## Avvio

| Sistema | Cosa fare |
|---|---|
| **Mac** | doppio clic su `avvia_mac.command` (la prima volta: clic destro → Apri) |
| **Windows** | doppio clic su `avvia_windows.bat` |

Serve solo **Python 3.9 o successivo** già installato. Al primo avvio lo script
crea un ambiente dedicato e scarica le librerie (qualche minuto, serve
internet); dalle volte successive parte subito. L'ambiente sta fuori dalla
cartella del progetto:

- Mac: `~/Library/Application Support/Moviola/venv`
- Windows: `%LOCALAPPDATA%\Moviola\venv`

FFmpeg è incluso (pacchetto `imageio-ffmpeg`): non va installato a parte.

Se il doppio clic su Mac non funziona, da Terminale: `bash avvia_mac.command`.

## Come si lavora

```
┌──────────┬───────────────────────────────┬──────────┐
│  CLIP    │  VISORE (sorgente / timeline) │  COLORE  │
│ clip1    │                               │ bersaglio│
│  clip1.1 │  barra della clip con le parti│ cursori  │
│  clip1.2 │  ◀◀ ▶ ▶▶  I  O  + Crea parte  │ curva    │
│ clip2    ├───────────────────────────────┤ HSL      │
│  clip2.1 │  TIMELINE  V3 V2 V1 │ A1 A2 A3│          │
└──────────┴───────────────────────────────┴──────────┘
```

1. **Importa** le clip (Ctrl/Cmd+I, o trascina file e cartelle nella lista).
   Diventano `clip1`, `clip2`, … e restano riferimenti ai file originali.
2. **Scegli le parti**: seleziona una clip, scorri con J/K/L o le frecce,
   segna **I** (ingresso) e **O** (uscita), premi **Invio** (o M).
   Nasce `clip1.1`, poi `clip1.2`, … I numeri non cambiano mai, anche se
   elimini una parte.
3. **Correggi le parti quando vuoi**: nella barra sotto il visore ogni parte
   è una fascia colorata; trascinane i bordi per allungarla o accorciarla,
   il centro per spostarla. Oppure selezionala e premi I/O.
   Duplica (Ctrl/Cmd+D), rinomina (F2), elimina (Canc).
4. **Monta**: trascina le parti in timeline, oppure premi **E** (accoda) o
   **F9** (alla testina). Doppio clic su una parte = accoda.
   Il pezzo in timeline è una copia: se poi modifichi la parte nella lista,
   la timeline resta com'era.
5. **Colore** su tre livelli, scelti in alto nel pannello:
   - **Clip intera**: vale per tutte le parti di quella clip, ovunque;
   - **Pezzo**: solo i pezzi selezionati in timeline;
   - **Timeline**: correzione generale su tutto il montaggio.

   Si applicano in quest'ordine (clip → pezzo → timeline). Copia/incolla con
   Ctrl/Cmd+Maiusc+C / V su più elementi selezionati (Ctrl/Cmd+clic).
   I preset si salvano dal menu Colore. Tasto `\` = prima/dopo.
6. **Pannello Effetti** (a destra, scheda accanto a Colore; si apre anche
   con il pulsante «Effetti» sulla timeline, con un doppio clic su un pezzo
   o dal clic destro → «Velocità ed effetti…»): seleziona un pezzo in
   timeline e regola
   - **velocità** (10–800 %, con pulsanti 25/50/100/200/400 %): il pezzo
     cambia durata e ciò che segue si sposta; l'audio collegato segue;
   - **velocità variabile (curva)**: spunta la casella e disegna la curva
     (orizzontale: dall'inizio alla fine del pezzo; verticale: velocità da
     0,1x a 8x, 1x al centro). Clic aggiunge un punto, trascina lo sposta,
     doppio clic lo toglie; ci sono dei modelli pronti (es. rampa lento al
     centro). La parte di clip mostrata resta la stessa, cambia il ritmo e
     quindi la durata in timeline. Tagliando il pezzo il movimento non cambia;
   - **bande nere** sopra e sotto: altezza in % o formati pronti 2.39:1,
     2:1, 1.85:1;
   - **inquadratura**: zoom, spostamento orizzontale/verticale, *Adatta*,
     *Riempi* (niente bande), *Centra*; oppure trascina l'immagine nel visore;
   - **dissolvenze** in entrata e uscita, da/verso **nero** o **bianco**,
     anche sull'audio collegato (spunta);
   - per i pezzi audio: volume e dissolvenze audio.
7. **Scritte**: tasto **T** (o «T Scritta» sulla timeline) aggiunge una
   scritta alla testina sulla traccia video più alta libera. Testo su più
   righe, carattere, dimensione, colore, grassetto/corsivo, allineamento,
   ombra, riquadro con opacità, durata, comparsa e scomparsa in dissolvenza.
   Si sposta trascinandola nel visore. Le scritte stanno sempre sopra al video.
8. **Esporta** (Ctrl/Cmd+E): MP4 H.264 o MOV ProRes 422, sempre dai file
   originali.

### Impostazioni del progetto

Pulsante con fps e risoluzione sulla barra della timeline (o Ctrl/Cmd+,):
risoluzione, **fotogrammi al secondo** e bande nere su tutto il montaggio.
Gli fps del progetto sono quelli dell'anteprima della timeline e del video
esportato; le clip con fps diversi vengono adattate ripetendo o saltando
fotogrammi. Il visore sorgente mostra invece ogni clip ai suoi fps originali.

### Timeline

- 3 tracce video + 3 audio; se ne aggiungono con **+V / +A** (in alto a sinistra).
- Video: vince la traccia più alta. ON/OFF accanto al nome spegne una traccia.
- Trascina per spostare (anche su un'altra traccia), i bordi per rifilare.
  Quello che finisce sotto viene sovrascritto (come in Resolve).
- **B** taglia alla testina · **Canc** elimina · **Maiusc+Canc** elimina e chiude lo spazio.
- **Alt**+trascina muove solo il video o solo l'audio; clic destro → scollega/collega.
- Clic destro su un audio → Volume. Aggancio ai bordi: **N**.
- Rotella: scorre · Ctrl/Cmd+rotella o **+/−**: zoom · **Maiusc+Z**: adatta.

Tutte le scorciatoie: menu Aiuto → Scorciatoie (F1).

## Perché l'anteprima resta fluida

- **Colore sulla scheda video**: tutte le regolazioni vengono "cotte" in una
  LUT 3D (33³) che la GPU applica a ogni fotogramma con uno shader. Il costo
  è lo stesso con una o quindici regolazioni. Se OpenGL non funziona,
  Moviola passa da sola al processore (menu Vista per forzarlo).
- **L'audio fa da orologio**: il video lo segue e scarta i fotogrammi in
  ritardo invece di rallentare.
- **Proxy automatici** per i file pesanti (H.265, 4K, oltre 30 fps): copie
  MJPEG a 540p, velocissime da decodificare. Si creano in background e si
  possono creare a mano (clic destro → Crea proxy). L'export usa sempre gli
  originali.
- Qualità anteprima regolabile (menu Vista).

Proxy, audio decodificato e miniature stanno nella cartella cache
(menu Aiuto → Cartella cache) e si possono cancellare in qualsiasi momento.

## Struttura del codice

```
moviola/
  project.py     modello dati (sorgenti, parti, timeline), annulla/ripeti — senza Qt
  color.py       motore colore fotografico → LUT 3D, file .cube — senza Qt
  audio.py       cache audio, mixer, riproduzione con orologio
  media.py       analisi file, proxy, miniature, forme d'onda (FFmpeg)
  decode.py      decodifica fotogrammi (PyAV)
  playback.py    motore di riproduzione (thread di decodifica + orologio)
  export.py      export con FFmpeg (lut3d + mix audio)
  ui/            interfaccia PySide6 (lista clip, visore GPU, timeline, colore)
tests/test_core.py   test del motore senza interfaccia
```

Il progetto si salva in un file `.moviola` (JSON leggibile) con solo
riferimenti e parametri.

Test del motore: `python -m tests.test_core`

## Prossimi passi possibili

Dissolvenza incrociata tra due clip, fermo immagine, rotazione,
maschere/gradiente lineare come in Camera Oscura.
