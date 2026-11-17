"""Costanti condivise della finestra principale."""
from .bin_panel import TAGS

TAB_CLIP, TAB_EFFECTS = 0, 1
PAGE_PROJECTS, PAGE_CLIP, PAGE_MONTAGE, PAGE_COLOR, PAGE_EXPORT = 0, 1, 2, 3, 4

TAB_QSS = ("QTabWidget::pane { border: none; }"
           "QTabBar::tab { padding: 8px 6px; font-weight: 700; letter-spacing: 1px; color: #8d8d8d;"
           " background: #1f2022; border: none; border-bottom: 2px solid #2c2d30; min-width: 70px; }"
           "QTabBar::tab:selected { color: #f0f0f0; background: #26272a; border-bottom: 2px solid #e8a33d; }"
           "QTabBar::tab:hover:!selected { color: #cfcfcf; }")
GREEN_TAG, YELLOW_TAG, RED_TAG = TAGS[0][1], TAGS[1][1], TAGS[3][1]

SHORTCUTS = """
<b>Riproduzione</b><br>
Spazio: play / pausa · J / K / L: indietro / stop / avanti (premi più volte per accelerare)<br>
← / →: un fotogramma; tenendole premute scorre, sempre più veloce · Maiusc+← / →: un secondo · ↑ / ↓: taglio precedente / successivo<br>
Home / Fine: inizio / fine<br><br>
<b>Selezione delle parti (visore sorgente)</b><br>
I / O: ingresso / uscita (su una parte selezionata la modificano subito)<br>
Invio o M: crea la parte (clip1.1, clip1.2, …) · Ctrl/Cmd+D: duplica · F2: rinomina<br>
E: accoda alla timeline · F9: inserisci alla testina (spinge avanti il resto) · F10: sovrascrivi alla testina<br>
Sulla timeline I / O segnano un tratto: Maiusc+X lo estrae, Alt+Maiusc+X lo rimuove, e l'export può usare solo quel tratto<br><br>
<b>Timeline</b><br>
C: taglia alla testina · Canc: elimina · Maiusc+Canc: elimina e chiudi lo spazio<br>
N: aggancio magnetico on/off (Maiusc mentre trascini lo sospende) · + / −: zoom · Maiusc+Z: adatta · Alt+trascina: muovi solo video o solo audio<br>
M: segnaposto alla testina (anche mentre suona, per i battiti) · Maiusc+M: segnaposto sul pezzo<br>
Ctrl/Cmd+T: dissolvenza incrociata con la clip precedente · Maiusc+F: fermo immagine alla testina<br>
Y: ritaglio dinamico on/off (con la modalità accesa, trascina una clip per scorrere il contenuto lasciando posizione e durata)<br>
Ctrl/Cmd+Alt+trascina una clip, oppure , e . : lo stesso senza la modalità<br>
Quadratini in alto agli angoli delle clip: dissolvenze · linea azzurra su scritte e riquadri: opacità<br>
Volume disegnato: clic sulla linea gialla di una clip audio, trascina il punto, doppio clic per toglierlo<br>
1 / 2 / 3 / 0: valuta la clip (verde, giallo, rosso, nessuna) · Maiusc+R: ripple on/off<br>
T: aggiungi una scritta · R: aggiungi un riquadro colorato · trascina nel visore: sposta scritta, riquadro o inquadratura<br>
Ctrl/Cmd+clic: selezione multipla · Ctrl/Cmd+A: seleziona tutto<br><br>
<b>Colore</b> (scheda COLORE, Ctrl/Cmd+Maiusc+4)<br>
Striscia dei pezzi sotto il monitor: clic per sceglierne uno, Ctrl/Cmd+clic per più pezzi, Maiusc+clic per un gruppo<br>
Livelli: Clip (tutti i pezzi di quella clip) · Pezzo (solo quello scelto) · Generale (tutto il montaggio)<br>
Ctrl/Cmd+Maiusc+C / V: copia / incolla regolazioni · \\ : prima / dopo · Doppio clic su un cursore: azzera<br><br>
<b>Progetto</b><br>
Ctrl/Cmd+Z: annulla · Ctrl/Cmd+Maiusc+Z: ripeti · Ctrl/Cmd+S: salva · Ctrl/Cmd+I: importa · Ctrl/Cmd+E: esporta
"""


