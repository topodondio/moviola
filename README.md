# Moviola

Editor video personale, leggero e **non distruttivo**, scritto in Python.
Pensato per chi viene dalla fotografia: si lavora "a clip" e il colore si
corregge con comandi fotografici (esposizione, contrasto, bilanciamento del
bianco, curva, HSL per colore), senza nodi.

> Stato: **prototipo 0.7**. Le funzioni base ci sono tutte; da collaudare
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

All'avvio compare l'elenco dei **progetti recenti** (doppio clic per
riaprirne uno), con i pulsanti *Nuovo progetto* e *Apri altro progetto*.
In alto c'è sempre una barra di sole icone (il nome compare passandoci
sopra col mouse): nuovo, apri, salva, recenti · annulla, ripeti · importa,
esporta. Le impostazioni del progetto stanno nel pulsante sopra la timeline.

```
┌───────────────────────────┬──────────────────────────────────────┐
│ CLIP │ EFFETTI │ COLORE   │  VISORE GRANDE: sempre la timeline    │
├───────────────────────────┤                                      │
│ [visore piccolo: clip]    │                                      │
│ barra con le parti        │  ⏮ ◀◀ ▶ ▶▶ ⏭   timecode               │
│ ◀◀ ▶ ▶▶  I O + Parte      ├──────────────────────────────────────┤
├───────────────────────────┤  TIMELINE   V3 V2 V1  │  A1 A2 A3     │
│ CLIP  + Video + ♪ Audio   │                                      │
│  clip1 / clip1.1 …        │                                      │
│  ♪ clip3 (musica)         │                                      │
└───────────────────────────┴──────────────────────────────────────┘
```

La colonna a sinistra ha tre schede, scelte con le linguette in alto:
**CLIP** (in alto un piccolo visore per guardare le clip singole e
sceglierne le parti, sotto l'elenco di clip video e musica), **EFFETTI** e
**COLORE**. Il visore grande mostra sempre la timeline. La colonna si
allarga o si stringe trascinandone il bordo, e si nasconde del tutto con
l'icona della barra in alto o **Ctrl/Cmd+L** (si riapre da sola se serve,
per esempio con un doppio clic su un pezzo).

1. **Importa** le clip (Ctrl/Cmd+I, o trascina file e cartelle nella lista).
   Diventano `clip1`, `clip2`, … e restano riferimenti ai file originali.
   I file solo audio (mp3, wav, m4a, aiff…) finiscono in fondo alla stessa
   lista, segnati con ♪ («+ ♪ Audio» per importarli). Si scelgono le parti
   come per i video e in timeline vanno da soli sull'**ultima traccia audio**
   (A3), la traccia della musica; trascinandoli su un'altra traccia audio
   restano dove li lasci.
2. **Scegli le parti**: seleziona una clip, scorri con J/K/L o le frecce,
   segna **I** (ingresso) e **O** (uscita), premi **Invio** (o M).
   Nasce `clip1.1`, poi `clip1.2`, … I numeri non cambiano mai, anche se
   elimini una parte.
   Con il clic destro puoi dare a clip e parti un **colore di etichetta**
   (verde «mi piace», rosso «da scartare», …): la riga si colora e in
   timeline i pezzi che ne derivano hanno una striscia dello stesso colore.
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
   **Colore animato nel tempo** (solo per *Pezzo* e *Timeline*): la casella
   davanti a ogni cursore, anche quelli HSL, lo trasforma in un grafico nel
   tempo (orizzontale il pezzo o la timeline, verticale il valore). Clic
   aggiunge un punto, trascinandolo il visore mostra quel momento, doppio
   clic lo toglie; tra un punto e l'altro il valore cambia gradualmente.
   Togliendo la spunta il cursore torna fisso al valore che aveva alla
   testina.
6. **Scheda EFFETTI** (si apre anche con un doppio clic su un pezzo o dal
   clic destro → «Velocità ed effetti…»): seleziona un pezzo in timeline e
   regola
   - **velocità** (10–800 %, con pulsanti 25/50/100/200/400 %): il pezzo
     cambia durata e ciò che segue si sposta; l'audio collegato segue;
   - **velocità variabile (curva)**: spunta la casella e disegna la curva
     (orizzontale: dall'inizio alla fine del pezzo; verticale: velocità da
     0,1x a 8x, 1x al centro). Clic aggiunge un punto, trascina lo sposta,
     doppio clic lo toglie; ci sono dei modelli pronti (es. rampa lento al
     centro). La parte di clip mostrata resta la stessa, cambia il ritmo e
     quindi la durata in timeline. Tagliando il pezzo il movimento non cambia;
   - **transizione con la clip precedente** (attaccata sulla stessa traccia):
     dissolvenza incrociata, passaggio dal nero o dal bianco, tendina e
     scorrimento verso sinistra o destra, con durata e incrocio dell'audio.
     È centrata sul taglio; se mancano fotogrammi prima o dopo, si tiene
     fermo il primo o l'ultimo. Scorciatoia: **Ctrl/Cmd+T** = dissolvenza
     incrociata; anche dal clic destro su un pezzo. In timeline la
     transizione è un riquadro con la X sopra al taglio;
   - **fermo immagine**: durata e «Ferma alla testina» (**Maiusc+F**): il
     fotogramma si blocca, quello che viene dopo slitta in avanti, la musica
     che sta già suonando continua;
   - **curva di velocità**: trascinando un nodo, il visore mostra il
     fotogramma che si trova in quel punto;
   - **inquadratura**: zoom, spostamento orizzontale/verticale, *Adatta*,
     *Riempi* (niente bande), *Centra*; oppure trascina l'immagine nel visore;
   - **dissolvenze** in entrata e uscita, da/verso **nero** o **bianco**,
     anche sull'audio collegato (spunta);
   - per i pezzi audio: volume e dissolvenze audio. Il **volume si disegna**
     anche direttamente sulla clip in timeline: la linea gialla è il volume;
     clic per aggiungere un punto, trascina per alzare/abbassare (0 dB si
     aggancia), doppio clic per togliere. I punti restano legati al suono
     anche se sposti o rifili la clip.
7. **Scritte**: tasto **T** (o «T Scritta» sulla timeline) aggiunge una
   scritta alla testina sulla traccia video più alta libera. Testo su più
   righe, carattere, dimensione, colore, grassetto/corsivo, allineamento,
   ombra, riquadro con opacità, durata, comparsa e scomparsa in dissolvenza.
   Si sposta trascinandola nel visore. Le scritte stanno sempre sopra al video.
   **Riquadri colorati**: tasto **R** (o «Riquadro» sulla timeline); nascono
   bianchi, opachi e a schermo pieno:
   colore, opacità, angoli arrotondati, larghezza, altezza, posizione
   (anche trascinandolo nel visore), durata e dissolvenze; formati pronti
   *Schermo pieno*, *Fascia in basso*, *Sotto un titolo*. Una scritta su una
   traccia più alta finisce sopra al riquadro.
8. **Esporta** (Ctrl/Cmd+E): MP4 H.264 o MOV ProRes 422, sempre dai file
   originali.

### Segnaposti

- **M** mette un segnaposto sulla timeline alla testina, anche durante la
  riproduzione (comodo per battere il tempo della musica). Sul visore
  sorgente M crea invece la parte, come Invio.
- **Maiusc+M** mette un segnaposto **sul pezzo** selezionato (la musica, se
  c'è): si sposta insieme al pezzo.
- I segnaposti attirano l'aggancio magnetico e le frecce ↑/↓ ci saltano
  sopra. Sul righello si trascinano; clic destro per nome, colore,
  eliminazione; doppio clic per rinominare.

### Salvataggio automatico e copie di sicurezza

Ogni 2 minuti, se ci sono modifiche, Moviola salva una copia automatica.
Se il programma si chiude male, alla riapertura propone di recuperarla.
A ogni salvataggio la versione precedente del progetto viene conservata
(le ultime 10): File → Copie di sicurezza dei progetti.

### Impostazioni del progetto

Pulsante con fps e risoluzione sulla barra della timeline, icona
dell'ingranaggio in alto o Ctrl/Cmd+,: risoluzione, **fotogrammi al
secondo** e **bande nere** su tutto il montaggio.
Gli fps del progetto sono quelli dell'anteprima della timeline e del video
esportato; le clip con fps diversi vengono adattate ripetendo o saltando
fotogrammi. Il visore sorgente mostra invece ogni clip ai suoi fps originali.

### Timeline

- 3 tracce video + 3 audio; se ne aggiungono con **+V / +A** (in alto a sinistra).
- Video: vince la traccia più alta. ON/OFF accanto al nome spegne una traccia.
- Trascina per spostare (anche su un'altra traccia), i bordi per rifilare.
  Quello che finisce sotto viene sovrascritto (come in Resolve).
- **B** taglia alla testina · **Canc** elimina · **Maiusc+Canc** elimina e chiude lo spazio.
- Finché video e audio sono collegati, allungarne o accorciarne uno cambia
  anche l'altro della stessa quantità; per lavorarli separati: clic destro →
  Scollega (o Alt mentre trascini).
- **Alt**+trascina muove solo il video o solo l'audio; clic destro → scollega/collega.
- **Scorri il contenuto** (slittamento): **Ctrl/Cmd+Alt+trascina** una clip,
  oppure **,** e **.** di un fotogramma: cambia la parte di clip originale
  mostrata, lasciando invariate posizione e durata in timeline (il visore
  mostra il nuovo primo fotogramma).
- **Maniglie sulle clip**: i quadratini bianchi agli angoli in alto si
  trascinano per le dissolvenze in entrata e in uscita; su scritte e
  riquadri la linea azzurra si trascina su e giù per l'opacità.
- Clic destro su un audio → Volume.
- **Aggancio magnetico** (pulsante con la calamita, tasto **N**): spostando,
  rifilando o trascinando clip dalla lista, i bordi si attaccano a quelli
  delle altre clip **su qualsiasi traccia**, alla testina e all'inizio; una
  linea arancione tratteggiata mostra il punto. Tieni premuto **Maiusc**
  mentre trascini per sospenderlo.
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
- **Risoluzione dell'anteprima separata da quella del progetto**: menu
  «Anteprima» sopra al visore (Piena, 1/2, 1/3, 1/4, con i pixel effettivi).
  Più è bassa, più la riproduzione è fluida; l'export resta alla risoluzione
  del progetto (o a quella scelta nella finestra di esportazione). Fino a
  540 righe l'anteprima usa i proxy, sopra legge gli originali.

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
