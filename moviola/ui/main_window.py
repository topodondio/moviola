"""Finestra principale: collega lista clip, visore, timeline e colore."""
from __future__ import annotations

import copy
import json
import os
import time

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence, QPixmap
from PySide6.QtWidgets import (QComboBox, QDockWidget, QTabWidget, QToolBar, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
                               QMainWindow, QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget)

from .. import audio, color, media, paths
from .. import speed as spd
from ..playback import Engine
from ..project import Project
from .bin_panel import TAGS, BinPanel
from .color_panel import ColorPanel
from .export_dialog import ExportDialog
from . import icons
from .inspector import Inspector
from .meters import DB_MIN, LevelMeter
from .welcome import WelcomeDialog
from .project_settings import ProjectSettingsDialog
from .text_render import default_family, render_png, text_box
from .source_bar import SourceBar
from .timeline import TimelinePanel, tc
from .viewer import CpuViewer, GlViewer

TAB_CLIP, TAB_EFFECTS, TAB_COLOR = 0, 1, 2
GREEN_TAG, YELLOW_TAG, RED_TAG = TAGS[0][1], TAGS[1][1], TAGS[3][1]

SHORTCUTS = """
<b>Riproduzione</b><br>
Spazio: play / pausa · J / K / L: indietro / stop / avanti (premi più volte per accelerare)<br>
← / →: un fotogramma · Maiusc+← / →: un secondo · ↑ / ↓: taglio precedente / successivo<br>
Home / Fine: inizio / fine<br><br>
<b>Selezione delle parti (visore sorgente)</b><br>
I / O: ingresso / uscita (su una parte selezionata la modificano subito)<br>
Invio o M: crea la parte (clip1.1, clip1.2, …) · Ctrl/Cmd+D: duplica · F2: rinomina<br>
E: accoda alla timeline · F9: inserisci alla testina della timeline<br><br>
<b>Timeline</b><br>
B: taglia alla testina · Canc: elimina · Maiusc+Canc: elimina e chiudi lo spazio<br>
N: aggancio magnetico on/off (Maiusc mentre trascini lo sospende) · + / −: zoom · Maiusc+Z: adatta · Alt+trascina: muovi solo video o solo audio<br>
M: segnaposto alla testina (anche mentre suona, per i battiti) · Maiusc+M: segnaposto sul pezzo<br>
Ctrl/Cmd+T: dissolvenza incrociata con la clip precedente · Maiusc+F: fermo immagine alla testina<br>
Ctrl/Cmd+Alt+trascina una clip, oppure , e . : scorre il contenuto lasciando posizione e durata<br>
Quadratini in alto agli angoli delle clip: dissolvenze · linea azzurra su scritte e riquadri: opacità<br>
Volume disegnato: clic sulla linea gialla di una clip audio, trascina il punto, doppio clic per toglierlo<br>
1 / 2 / 3 / 0: valuta la clip (verde, giallo, rosso, nessuna) · Maiusc+R: ripple on/off<br>
T: aggiungi una scritta · R: aggiungi un riquadro colorato · trascina nel visore: sposta scritta, riquadro o inquadratura<br>
Ctrl/Cmd+clic: selezione multipla · Ctrl/Cmd+A: seleziona tutto<br><br>
<b>Colore</b><br>
Ctrl/Cmd+Maiusc+C / V: copia / incolla regolazioni · \\ : prima / dopo<br>
Doppio clic su un cursore: azzera<br><br>
<b>Progetto</b><br>
Ctrl/Cmd+Z: annulla · Ctrl/Cmd+Maiusc+Z: ripeti · Ctrl/Cmd+S: salva · Ctrl/Cmd+I: importa · Ctrl/Cmd+E: esporta
"""


class MainWindow(QMainWindow):
    media_event = Signal(str, str, object)

    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self.project = Project()
        self.store = audio.AudioStore()
        self.jobs = media.JobRunner()
        self.thumbs: dict[str, QPixmap] = {}
        self.peaks: dict[str, object] = {}
        self.proxies: dict[str, str] = {}
        self.marks: dict[str, list] = {}
        self.selected_ref = None
        self.active_panel = "bin"
        self.grade_clip = None
        self._grade_ck = (None, 0.0)
        self._busy_proxy = set()
        self._pending_imports = []
        self._import_results = {}
        self._vdrag = None
        default_family()  # carattere di sistema letto subito (serve anche all'export in background)

        self.engine = Engine(lambda: self.project, self.store, self.path_for)
        self.engine.quality = cfg.get("quality", "meta")

        self.setWindowTitle("Moviola")
        self.resize(1440, 900)
        self._build_ui()
        self._build_actions()
        self._build_toolbar()
        self._connect()
        self.meter_timer = QTimer(self)
        self.meter_timer.setInterval(40)
        self.meter_timer.timeout.connect(self._update_meter)
        self.meter_timer.start()
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(self.AUTOSAVE_SECONDS * 1000)
        self.autosave_timer.timeout.connect(self._autosave)
        self.autosave_timer.start()
        self.set_viewer_mode("timeline", force=True)
        self.refresh_all()
        if self.engine.player.error:
            self.statusBar().showMessage(self.engine.player.error + " — la riproduzione sarà muta.", 10000)
        if not paths.ffmpeg_exe():
            QMessageBox.warning(self, "FFmpeg mancante",
                                "FFmpeg non è stato trovato: import di audio, proxy ed export non funzioneranno.\n"
                                "Riesegui il file di avvio per installare le dipendenze.")

    # ================================================================== UI
    def _build_ui(self):
        # visore
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(6, 6, 6, 4)
        tl.setSpacing(4)
        head = QHBoxLayout()
        self.mode_label = QLabel("TIMELINE")
        self.mode_label.setObjectName("modeLabel")
        self.name_label = QLabel("")
        self.name_label.setStyleSheet("color:#bbb;")
        head.addWidget(self.mode_label)
        head.addSpacing(10)
        head.addWidget(self.name_label, 1)
        head.addWidget(QLabel("Anteprima"))
        self.preview_combo = QComboBox()
        self.preview_combo.setToolTip("Risoluzione dell'anteprima: più bassa = riproduzione più fluida.\n"
                                      "Non cambia il video esportato, che usa la risoluzione del progetto.")
        self.preview_combo.setFocusPolicy(Qt.NoFocus)
        head.addWidget(self.preview_combo)
        head.addSpacing(8)
        self.fx_btn = QPushButton("FX")
        self.fx_btn.setCheckable(True)
        self.fx_btn.setChecked(True)
        self.fx_btn.setFocusPolicy(Qt.NoFocus)
        self.fx_btn.setFixedWidth(52)
        self.fx_btn.setToolTip("Correzione colore accesa/spenta nell'anteprima (Ctrl/Cmd+Maiusc+F)\n"
                               "Spenta: si vedono le clip senza colore (clip intera, pezzo e timeline).\n"
                               "Dissolvenze, transizioni, zoom, posizione e bande restano. L'export usa sempre il colore.")
        self.fx_btn.setStyleSheet(
            "QPushButton { font-weight: 700; color: #1b1b1b; background: #e8a33d; border-color: #e8a33d; }"
            "QPushButton:!checked { color: #9a9a9a; background: #2d2f33; border-color: #5a5c60; }")
        self.fx_btn.toggled.connect(self.set_fx_enabled)
        head.addWidget(self.fx_btn)
        tl.addLayout(head)
        self.viewer_holder = QVBoxLayout()
        self.viewer_holder.setContentsMargins(0, 0, 0, 0)
        vrow = QHBoxLayout()
        vrow.setSpacing(4)
        vrow.addLayout(self.viewer_holder, 1)
        self.meter = LevelMeter()
        vrow.addWidget(self.meter)
        tl.addLayout(vrow, 1)
        self.src_viewer_holder = QVBoxLayout()
        self.src_viewer_holder.setContentsMargins(0, 0, 0, 0)
        self.viewer = None
        self.src_viewer = None
        tr = QHBoxLayout()
        tr.setSpacing(4)

        def tb(text, tip, slot):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(slot)
            tr.addWidget(b)
            return b

        tb("⏮", "Inizio (Home)", lambda: self._tl(lambda: self.engine.seek(0)))
        tb("◀◀", "Indietro (J)", lambda: self._tl(lambda: self.engine.shuttle(-1)))
        self.play_btn = tb("▶", "Play / pausa (Spazio)", lambda: self._tl(self.engine.toggle))
        self.play_btn.setFixedWidth(46)
        tb("▶▶", "Avanti veloce (L)", lambda: self._tl(lambda: self.engine.shuttle(1)))
        tb("⏭", "Fine (Fine)", lambda: self._tl(lambda: self.engine.seek(self.engine.duration())))
        tr.addSpacing(10)
        self.tc_label = QLabel("00:00:00:00")
        self.tc_label.setObjectName("timecode")
        tr.addWidget(self.tc_label)
        self.dur_label = QLabel("")
        self.dur_label.setStyleSheet("color:#888;")
        tr.addWidget(self.dur_label)
        tr.addStretch(1)
        tl.addLayout(tr)
        self.src_panel = self._build_source_panel()
        self._make_viewer(self.cfg.get("gpu", True))

        self.timeline = TimelinePanel(self)
        split = QSplitter(Qt.Vertical)
        split.addWidget(top)
        split.addWidget(self.timeline)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([520, 330])
        self.setCentralWidget(split)

        # un'unica colonna con le linguette CLIP · EFFETTI · COLORE
        self.bin = BinPanel()
        self.color_panel = ColorPanel()
        self.inspector = Inspector()
        self.right_tabs = QTabWidget()
        self.right_tabs.setDocumentMode(True)
        clip_tab = QSplitter(Qt.Vertical)
        clip_tab.setChildrenCollapsible(False)
        clip_tab.addWidget(self.src_panel)
        clip_tab.addWidget(self.bin)
        clip_tab.setStretchFactor(0, 2)
        clip_tab.setStretchFactor(1, 3)
        clip_tab.setSizes([330, 420])
        self.right_tabs.addTab(clip_tab, "CLIP")
        self.right_tabs.setMinimumWidth(220)
        self.right_tabs.addTab(self.inspector, "EFFETTI")
        self.right_tabs.addTab(self.color_panel, "COLORE")
        self.right_tabs.tabBar().setExpanding(True)
        self.right_tabs.setCurrentIndex(TAB_CLIP)
        self.right_tabs.setStyleSheet(
            "QTabWidget::pane { border: none; }"
            "QTabBar::tab { padding: 8px 6px; font-weight: 700; letter-spacing: 1px; color: #8d8d8d;"
            " background: #1f2022; border: none; border-bottom: 2px solid #2c2d30; min-width: 70px; }"
            "QTabBar::tab:selected { color: #f0f0f0; background: #26272a; border-bottom: 2px solid #e8a33d; }"
            "QTabBar::tab:hover:!selected { color: #cfcfcf; }")
        d = QDockWidget("", self)
        d.setObjectName("dock_colonna")
        d.setTitleBarWidget(QWidget())  # niente barra del titolo: bastano le linguette
        d.setWidget(self.right_tabs)
        d.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.LeftDockWidgetArea, d)
        self.side_dock = d
        d.visibilityChanged.connect(lambda vis: self.act_column.setChecked(d.isVisible())
                                    if hasattr(self, "act_column") else None)
        self.resizeDocks([d], [370], Qt.Horizontal)
        self.statusBar().showMessage("Pronto. Importa le clip con Ctrl/Cmd+I o trascinandole nella lista.")

    def _build_source_panel(self):
        """Parte alta della scheda CLIP: piccolo visore per guardare le clip
        singole e sceglierne le parti."""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 6, 4, 2)
        v.setSpacing(3)
        self.src_name = QLabel("Seleziona una clip nella lista")
        self.src_name.setStyleSheet("color:#bbb;")
        self.src_name.setMinimumWidth(10)
        v.addWidget(self.src_name)
        v.addLayout(self.src_viewer_holder, 1)
        self.source_bar = SourceBar()
        v.addWidget(self.source_bar)

        def btn(row, text, tip, slot, width=None):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)
            b.setStyleSheet("padding: 3px 6px;")
            if width:
                b.setFixedWidth(width)
            b.clicked.connect(slot)
            row.addWidget(b)
            return b

        r1 = QHBoxLayout()
        r1.setSpacing(3)
        btn(r1, "◀◀", "Indietro (J)", lambda: self._src(lambda: self.engine.shuttle(-1)), 36)
        self.src_play_btn = btn(r1, "▶", "Play / pausa (Spazio)", lambda: self._src(self.engine.toggle), 40)
        btn(r1, "▶▶", "Avanti veloce (L)", lambda: self._src(lambda: self.engine.shuttle(1)), 36)
        self.src_tc = QLabel("00:00:00:00")
        self.src_tc.setStyleSheet("font-family: Menlo, Consolas, monospace; color:#f0f0f0;")
        r1.addSpacing(6)
        r1.addWidget(self.src_tc)
        self.src_dur = QLabel("")
        self.src_dur.setStyleSheet("color:#888;")
        r1.addWidget(self.src_dur)
        r1.addStretch(1)
        v.addLayout(r1)
        r2 = QHBoxLayout()
        r2.setSpacing(3)
        btn(r2, "I", "Ingresso (I)", lambda: self._src(self.mark_in), 30)
        btn(r2, "O", "Uscita (O)", lambda: self._src(self.mark_out), 30)
        btn(r2, "+ Parte", "Crea una parte dai segni I/O (Invio)", lambda: self._src(self.create_subclip))
        r2.addStretch(1)
        btn(r2, "Accoda ⤵", "Accoda alla timeline (E)", self.append_selected)
        btn(r2, "Alla testina", "Inserisci alla testina della timeline (F9)", self.insert_at_playhead)
        v.addLayout(r2)
        return w

    def _bin_hover(self, rid, frac):
        """Passando il mouse sulla lista, il visore piccolo mostra la clip sotto
        al puntatore (da sinistra a destra = dall'inizio alla fine)."""
        if self.engine.playing:
            return
        if not rid:
            back = getattr(self, "_hover_back", None)
            self._hover_back = None
            if back is not None:
                mode, sid, pos = back
                if mode == "source" and sid:
                    self.set_viewer_mode("source", sid)
                    self.engine.seek(pos)
                else:
                    self.set_viewer_mode("timeline")
                self._update_source_bar()
            return
        src, a, b = self.project.range_of(rid)
        if src is None or not src.get("has_video"):
            return
        if getattr(self, "_hover_back", None) is None:
            self._hover_back = (self.engine.mode, self.engine.source_id, self.engine.position)
        if self.engine.mode != "source" or self.engine.source_id != src["id"]:
            self.engine.set_mode("source", src["id"])
            self._apply_aspect()
            self._update_source_bar()
        self.engine.seek(a + frac * max(0.0, b - a - 1.0 / max(src.get("fps") or 25, 1)))

    def rate_clips(self, tag):
        """Valutazione veloce con i tasti 1 (verde), 2 (giallo), 3 (rosso), 0 (nessuna)."""
        ids = self.bin.selected_ids() or ([self.selected_ref] if self.selected_ref else [])
        if not ids:
            return
        self._hover_back = None
        self.bin_action("tag:" + (tag or ""), ids)
        names = {GREEN_TAG: "verde", YELLOW_TAG: "giallo", RED_TAG: "rosso"}
        self.statusBar().showMessage(f"{', '.join(ids)}: " + (names.get(tag, "") or "nessuna etichetta"), 2000)

    def _update_meter(self):
        if self.engine.playing:
            self.meter.push(*self.engine.player.take_levels())
        elif max(self.meter.level) > DB_MIN:
            self.meter.push(0.0, 0.0)  # discesa morbida a riproduzione ferma

    def _tl(self, fn):
        """Comandi del visore grande: agiscono sempre sulla timeline."""
        if self.engine.mode != "timeline":
            self.set_viewer_mode("timeline")
        fn()

    def _src(self, fn):
        """Comandi del visore piccolo: agiscono sempre sulla clip selezionata."""
        if self.engine.mode != "source":
            if not (self.selected_ref or self.project.sources):
                self.statusBar().showMessage("Importa o seleziona una clip.", 3000)
                return
            self.show_current_source()
        fn()

    def _new_viewer(self, gpu):
        v = None
        if gpu and GlViewer is not None:
            try:
                import OpenGL.GL  # noqa: F401

                v = GlViewer()
                v.failed.connect(self._gl_failed)
            except Exception:
                v = None
        if v is None:
            v = CpuViewer()
        v.setMinimumSize(160, 90)
        return v

    def _make_viewer(self, gpu: bool):
        """Crea i due visori: quello grande (timeline) e quello piccolo (clip)."""
        for old, holder in ((self.viewer, self.viewer_holder), (self.src_viewer, self.src_viewer_holder)):
            if old is not None:
                holder.removeWidget(old)
                old.deleteLater()
        v = self._new_viewer(gpu)
        sv = self._new_viewer(gpu)
        self.engine.cpu_grade = isinstance(v, CpuViewer)
        self.viewer_holder.addWidget(v)
        self.src_viewer_holder.addWidget(sv)
        self.viewer = v
        self.src_viewer = sv
        v.overlay.drag_begin.connect(self._viewer_drag_begin)
        v.overlay.drag_move.connect(self._viewer_drag_move)
        v.overlay.drag_end.connect(self._viewer_drag_end)
        sv.overlay.drag_begin.connect(lambda *_: self._src(lambda: None))
        self.engine._lut_cache.clear()

    def _gl_failed(self, msg):
        QTimer.singleShot(0, lambda: self._switch_cpu(msg))

    def _switch_cpu(self, msg):
        if isinstance(self.viewer, CpuViewer) and isinstance(self.src_viewer, CpuViewer):
            return
        self._make_viewer(False)
        self._apply_aspect()
        self.act_gpu.setChecked(False)
        self.statusBar().showMessage(f"Scheda video non utilizzabile ({msg[:80]}): anteprima con il processore.", 12000)
        self.engine.refresh()

    # ============================================================= azioni
    def _build_actions(self):
        mb = self.menuBar()

        def act(menu, text, slot, sc=None, checkable=False, checked=False):
            a = QAction(text, self)
            if sc:
                a.setShortcuts([QKeySequence(s) for s in (sc if isinstance(sc, (list, tuple)) else [sc])])
                a.setShortcutContext(Qt.WindowShortcut)
            if checkable:
                a.setCheckable(True)
                a.setChecked(checked)
                a.toggled.connect(slot)
            else:
                a.triggered.connect(lambda _=False: slot())
            menu.addAction(a)
            return a

        m = mb.addMenu("File")
        self.act_new = act(m, "Nuovo progetto", self.new_project, "Ctrl+N")
        self.act_open = act(m, "Apri progetto…", self.open_project, "Ctrl+O")
        act(m, "Progetti recenti…", self.show_welcome, "Ctrl+Shift+O")
        act(m, "Copie di sicurezza dei progetti…", lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(paths.cache_dir("copie_di_sicurezza"))))
        self.recent_menu = m.addMenu("Apri recente")
        self._fill_recent()
        self.act_save = act(m, "Salva", self.save, "Ctrl+S")
        act(m, "Salva con nome…", self.save_as, "Ctrl+Shift+S")
        m.addSeparator()
        self.act_import = act(m, "Importa clip…", self.import_dialog, "Ctrl+I")
        act(m, "Importa cartella…", self.import_folder_dialog)
        m.addSeparator()
        self.act_export = act(m, "Esporta video…", self.export_dialog, "Ctrl+E")
        m.addSeparator()
        act(m, "Esci", self.close, "Ctrl+Q")

        m = mb.addMenu("Modifica")
        self.act_undo = act(m, "Annulla", self.undo, "Ctrl+Z")
        self.act_redo = act(m, "Ripeti", self.redo, ["Ctrl+Shift+Z", "Ctrl+Y"])
        m.addSeparator()
        act(m, "Taglia alla testina", lambda: self.timeline_action("split", list(self.timeline.view.selection)), "B")
        act(m, "Elimina", self.delete_selected, ["Delete", "Backspace"])
        act(m, "Elimina e chiudi lo spazio", lambda: self.timeline_action(
            "ripple_delete", list(self.timeline.view.selection)), ["Shift+Delete", "Shift+Backspace"])
        act(m, "Seleziona tutto (timeline)", self.select_all, "Ctrl+A")
        m.addSeparator()
        act(m, "Ingresso", self.mark_in, "I")
        act(m, "Uscita", self.mark_out, "O")
        act(m, "Vai all'ingresso", lambda: self.goto_mark(0), "Shift+I")
        act(m, "Vai all'uscita", lambda: self.goto_mark(1), "Shift+O")
        act(m, "Crea parte dai segni", self.create_subclip, ["Return", "Enter"])
        rm = m.addMenu("Valuta la clip")
        act(rm, "Verde (mi piace)", lambda: self.rate_clips(GREEN_TAG), "1")
        act(rm, "Giallo (forse)", lambda: self.rate_clips(YELLOW_TAG), "2")
        act(rm, "Rosso (da scartare)", lambda: self.rate_clips(RED_TAG), "3")
        act(rm, "Nessuna etichetta", lambda: self.rate_clips(None), "0")
        act(m, "Duplica parte", lambda: self.bin_action("duplicate", self.bin.selected_ids()), "Ctrl+D")
        act(m, "Rinomina", lambda: self.bin_action("rename", self.bin.selected_ids()[:1]), "F2")
        act(m, "Accoda alla timeline", self.append_selected, "E")
        act(m, "Inserisci alla testina", self.insert_at_playhead, "F9")

        m = mb.addMenu("Riproduzione")
        act(m, "Play / pausa", self.engine.toggle, "Space")
        act(m, "Indietro (J)", lambda: self.engine.shuttle(-1), "J")
        act(m, "Stop (K)", lambda: self.engine.shuttle(0), "K")
        act(m, "Avanti (L)", lambda: self.engine.shuttle(1), "L")
        act(m, "Fotogramma precedente", lambda: self.engine.step(-1), "Left")
        act(m, "Fotogramma successivo", lambda: self.engine.step(1), "Right")
        act(m, "Indietro 1 s", lambda: self.engine.seek(self.engine.position - 1), "Shift+Left")
        act(m, "Avanti 1 s", lambda: self.engine.seek(self.engine.position + 1), "Shift+Right")
        act(m, "Taglio precedente", lambda: self.jump_edit(-1), "Up")
        act(m, "Taglio successivo", lambda: self.jump_edit(1), "Down")
        act(m, "Inizio", lambda: self.engine.seek(0), "Home")
        act(m, "Fine", lambda: self.engine.seek(self.engine.duration()), "End")
        m.addSeparator()
        act(m, "Comandi sulla clip (visore piccolo)", self.show_current_source, "Ctrl+1")
        act(m, "Comandi sulla timeline (visore grande)", lambda: self.set_viewer_mode("timeline"), "Ctrl+2")

        m = mb.addMenu("Timeline")
        self.act_snap = act(m, "Aggancio", lambda on: self.timeline.btn_snap.setChecked(on), "N",
                            checkable=True, checked=True)
        act_r = act(m, "Ripple", lambda on: self.timeline.btn_ripple.setChecked(on), "Shift+R", checkable=True)
        self.timeline.btn_ripple.toggled.connect(act_r.setChecked)
        self.timeline.btn_snap.toggled.connect(self.act_snap.setChecked)
        act(m, "Zoom avanti", lambda: self.timeline.view.zoom(1.4), ["+", "="])
        act(m, "Zoom indietro", lambda: self.timeline.view.zoom(1 / 1.4), "-")
        act(m, "Adatta alla finestra", self.timeline.view.zoom_fit, "Shift+Z")
        m.addSeparator()
        act(m, "Aggiungi scritta alla testina", self.add_text, "T")
        act(m, "Aggiungi riquadro colorato alla testina", self.add_box, "R")
        act(m, "Fermo immagine alla testina", lambda: self.freeze_at_playhead(), "Shift+F")
        act(m, "Scorri contenuto: 1 fotogramma prima", lambda: self.timeline_action(
            "slip:-1", list(self.timeline.view.selection)), ",")
        act(m, "Scorri contenuto: 1 fotogramma dopo", lambda: self.timeline_action(
            "slip:1", list(self.timeline.view.selection)), ".")
        act(m, "Dissolvenza incrociata sui pezzi selezionati", lambda: self.timeline_action(
            "transition:dissolve", list(self.timeline.view.selection)), "Ctrl+T")
        sm = m.addMenu("Segnaposti")
        act(sm, "Aggiungi segnaposto alla testina (sorgente: crea parte)", self.marker_or_part, "M")
        act(sm, "Segnaposto sul pezzo selezionato", lambda: self.timeline_action(
            "item_marker", list(self.timeline.view.selection)), "Shift+M")
        act(sm, "Elimina tutti i segnaposti", lambda: self.timeline_action("delete_all_markers", []))
        m.addSeparator()
        act(m, "Aggiungi traccia video", lambda: self.timeline_action("add_video_track", []))
        act(m, "Aggiungi traccia audio", lambda: self.timeline_action("add_audio_track", []))
        act(m, "Scollega audio e video", lambda: self.timeline_action("unlink", list(self.timeline.view.selection)))
        act(m, "Collega audio e video", lambda: self.timeline_action("link", list(self.timeline.view.selection)))
        act(m, "Impostazioni progetto (fps, risoluzione, bande)…", self.timeline_settings, "Ctrl+,")

        m = mb.addMenu("Colore")
        act(m, "Copia regolazioni", self.copy_grade, "Ctrl+Shift+C")
        act(m, "Incolla regolazioni", self.paste_grade, "Ctrl+Shift+V")
        act(m, "Azzera regolazioni", self.reset_grade)
        self.act_bypass = act(m, "Prima / dopo", self.set_bypass, "\\", checkable=True)
        act(m, "Correzione colore accesa/spenta nell'anteprima (FX)", lambda: self.set_fx_enabled(not self.fx_btn.isChecked()),
            "Ctrl+Shift+F")
        m.addSeparator()
        act(m, "Salva preset…", self.save_preset)
        self.preset_menu = m.addMenu("Applica preset")
        self.preset_menu.aboutToShow.connect(self._fill_presets)

        m = mb.addMenu("Vista")
        qm = m.addMenu("Risoluzione anteprima")
        grp = QActionGroup(self)
        self.quality_actions = {}
        for k, lab in (("piena", "Piena (come il progetto)"), ("meta", "1/2 (consigliata)"), ("terzo", "1/3"),
                       ("quarto", "1/4 (la più fluida)")):
            a = QAction(lab, self, checkable=True)
            a.setChecked(self.engine.quality == k)
            a.triggered.connect(lambda _=False, kk=k: self.set_quality(kk))
            grp.addAction(a)
            qm.addAction(a)
            self.quality_actions[k] = a
        self.act_proxy = act(m, "Usa i proxy per l'anteprima", self.set_use_proxy, checkable=True,
                             checked=self.project.data.get("use_proxies", True))
        self.act_autoproxy = act(m, "Crea proxy automaticamente per i file pesanti", self.set_autoproxy,
                                 checkable=True, checked=self.cfg.get("auto_proxy", True))
        act(m, "Crea proxy per tutte le clip", lambda: self.bin_action("proxy", [s["id"] for s in
                                                                                self.project.sources]))
        m.addSeparator()
        self.act_gpu = act(m, "Anteprima con la scheda video (OpenGL)", self.set_gpu, checkable=True,
                           checked=not self.engine.cpu_grade)

        self.act_column = act(m, "Mostra la colonna (clip, effetti, colore)", self.set_column_visible, "Ctrl+L",
                              checkable=True, checked=True)
        act(m, "Scheda Clip", lambda: self.show_tab(TAB_CLIP), "Ctrl+3")
        act(m, "Scheda Effetti (velocità, inquadratura, dissolvenze, scritte, riquadri)",
            lambda: self.show_tab(TAB_EFFECTS), "Ctrl+4")
        act(m, "Scheda Colore", lambda: self.show_tab(TAB_COLOR), "Ctrl+5")

        m = mb.addMenu("Aiuto")
        act(m, "Scorciatoie da tastiera", self.show_shortcuts, "F1")
        act(m, "Cartella cache (proxy e audio)", lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(paths.cache_root())))

    def _build_toolbar(self):
        """Barra in alto: solo icone (il nome compare passando sopra col mouse)."""
        tb = QToolBar("Progetto", self)
        tb.setObjectName("toolbar_progetto")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.act_recent_tb = QAction("Progetti recenti", self)
        self.act_recent_tb.triggered.connect(lambda _=False: self.show_welcome())
        groups = [
            [(self.act_new, "new", "Nuovo progetto (Ctrl/Cmd+N)"),
             (self.act_open, "open", "Apri progetto (Ctrl/Cmd+O)"),
             (self.act_save, "save", "Salva progetto (Ctrl/Cmd+S)"),
             (self.act_recent_tb, "recent", "Progetti recenti (Ctrl/Cmd+Maiusc+O)")],
            [(self.act_column, "sidebar", "Mostra o nascondi la colonna di sinistra (Ctrl/Cmd+L)")],
            [(self.act_undo, "undo", "Annulla (Ctrl/Cmd+Z)"),
             (self.act_redo, "redo", "Ripeti (Ctrl/Cmd+Maiusc+Z)")],
            [(self.act_import, "import", "Importa clip (Ctrl/Cmd+I)"),
             (self.act_export, "export", "Esporta il video (Ctrl/Cmd+E)")],
        ]
        for k, grp in enumerate(groups):
            if k:
                tb.addSeparator()
            for a, ic, tip in grp:
                a.setIcon(icons.icon(ic))
                a.setToolTip(tip)
                tb.addAction(a)
        tb.setStyleSheet("QToolBar { background: #1a1b1d; border: none; spacing: 2px; padding: 3px 6px; }"
                         "QToolButton { padding: 4px; border: none; background: transparent; }"
                         "QToolButton:hover { background: #34363a; border-radius: 4px; }"
                         "QToolBar::separator { background: #3a3c40; width: 1px; margin: 4px 6px; }")
        self.addToolBar(Qt.TopToolBarArea, tb)

    def _update_undo_state(self):
        self.act_undo.setEnabled(self.project.can_undo())
        self.act_redo.setEnabled(self.project.can_redo())

    def show_welcome(self):
        """Schermata con i progetti recenti (all'avvio e da File → Progetti recenti)."""
        d = WelcomeDialog(self.cfg.get("recent", []), self)
        if not d.exec():
            return
        kind, path = d.choice
        if kind == "open":
            self.open_project(path)
        elif kind == "browse":
            self.open_project()
        elif kind == "new" and (self.project.sources or self.project.items or self.project.path):
            self.new_project()

    def _connect(self):
        e = self.engine
        e.frame_ready.connect(self._on_frame)
        e.position_changed.connect(self._on_position)
        e.playing_changed.connect(self._playing_changed)
        self.bin.current_changed.connect(self.show_ref)
        self.bin.selection_changed.connect(self._bin_selection)
        self.bin.request.connect(self.bin_action)
        self.bin.hover.connect(self._bin_hover)
        self.bin.import_files.connect(self.import_paths)
        sb = self.source_bar
        sb.seek.connect(lambda t: self.engine.seek(t))
        sb.select_sub.connect(self._select_sub_from_bar)
        sb.edit_begin.connect(lambda sid: self.project.checkpoint())
        sb.edit_sub.connect(self._edit_sub_live)
        sb.edit_end.connect(lambda sid: self._mark_dirty())
        v = self.timeline.view
        v.seek.connect(self._timeline_seek)
        v.selection_changed.connect(self._timeline_selection)
        v.changed.connect(self._timeline_changed)
        v.activated.connect(self._timeline_activated)
        v.action.connect(self.timeline_action)
        cp = self.color_panel
        cp.begin_edit.connect(self._grade_begin)
        cp.anim_edit.connect(self._anim_edit)
        cp.anim_seek.connect(self._anim_seek)
        ins = self.inspector
        ins.begin.connect(self._grade_begin)
        ins.changed.connect(self._item_edit)
        ins.action.connect(self._inspector_action)
        ins.preview_at.connect(self._preview_item_fraction)
        self.timeline.btn_text.clicked.connect(self.add_text)
        self.timeline.btn_settings.clicked.connect(self.timeline_settings)
        self.preview_combo.currentIndexChanged.connect(
            lambda i: self.set_quality(self.preview_combo.itemData(i)) if i >= 0 else None)
        self.timeline.btn_box.clicked.connect(self.add_box)
        cp.edit.connect(self._grade_edit)
        cp.target_changed.connect(lambda k: self.refresh_color_panel())
        cp.action.connect(self._color_action)
        self.media_event.connect(self._on_media_event)

    # ======================================================= stato e vista
    def path_for(self, src, height=None):
        """File da leggere per l'anteprima: il proxy (540p) se basta per la
        risoluzione scelta, altrimenti l'originale."""
        if self.project.data.get("use_proxies", True) and (height is None or height <= media.PROXY_HEIGHT + 20):
            p = self.proxies.get(src["id"])
            if p and os.path.exists(p):
                return p
        return src["path"]

    def refresh_all(self, select=None):
        self.bin.rebuild(self.project, select)
        self.timeline.view.selection &= {i["id"] for i in self.project.items}
        self.timeline.view.update()
        self.timeline.sync()
        self._update_source_bar()
        self.refresh_color_panel()
        self.refresh_inspector()
        self._update_title()
        self._apply_aspect()
        self.engine.refresh()

    def _update_title(self):
        self._update_settings_label()
        self._update_undo_state()
        name = os.path.basename(self.project.path) if self.project.path else "Senza titolo"
        self.setWindowTitle(f"{'• ' if self.project.dirty else ''}{name} — Moviola")

    def _mark_dirty(self):
        self.project.dirty = True
        self._update_title()

    def _apply_aspect(self):
        src = self.project.source(self.engine.source_id or "")
        if src and src.get("height"):
            self.src_viewer.aspect = src["width"] / src["height"]
        w, h = self.project.size
        self.viewer.aspect = w / max(h, 1)
        self.viewer.update()
        self.src_viewer.update()

    def set_viewer_mode(self, mode, source_id=None, force=False):
        if not force and mode == self.engine.mode and source_id == self.engine.source_id:
            return
        self.engine.remember_timeline_pos()
        self.engine.set_mode(mode, source_id)
        self._apply_aspect()
        self._update_name_label()
        self._update_source_bar()

    def _playing_changed(self, playing):
        src = self.engine.mode == "source"
        self.play_btn.setText("❚❚" if playing and not src else "▶")
        self.src_play_btn.setText("❚❚" if playing and src else "▶")

    def show_current_source(self):
        if self.selected_ref:
            self.show_ref(self.selected_ref)
        elif self.project.sources:
            self.show_ref(self.project.sources[0]["id"])

    def _update_name_label(self):
        src, a, b = self.project.range_of(self.selected_ref or "")
        if src:
            self.src_name.setText(f"{self.selected_ref}  ·  {src.get('label') or src['name']}")
            self.src_name.setToolTip(src["path"])
            self.src_dur.setText(f"/ {tc(src['duration'], src['fps'])}")
        else:
            self.src_name.setText("Seleziona una clip nella lista")
            self.src_dur.setText("")
        self.dur_label.setText(f"/ {tc(self.project.timeline_end(), self.project.fps)}")

    def show_ref(self, ref):
        src, a, b = self.project.range_of(ref)
        if src is None:
            return
        self.selected_ref = ref
        self.set_viewer_mode("source", src["id"])
        sub = self.project.subclip(ref)
        if sub and not (sub["in"] - 1e-6 <= self.engine.position <= sub["out"] + 1e-6):
            self.engine.seek(sub["in"])
        self._update_name_label()
        self._update_source_bar()

    def _update_source_bar(self):
        sid = self.engine.source_id if self.engine.mode == "source" else None
        if sid is None:
            src0, _, _ = self.project.range_of(self.selected_ref or "")
            sid = src0["id"] if src0 else None
        src = self.project.source(sid or "")
        if src is None:
            self.source_bar.set_data(0, 25, [], None)
            return
        subs = [(c["id"], c["in"], c["out"], c["n"]) for c in self.project.subclips_of(src["id"])]
        sel = self.selected_ref if self.project.subclip(self.selected_ref or "") else None
        mk = self.marks.get(src["id"], [None, None])
        self.source_bar.set_data(src["duration"], src["fps"], subs, sel,
                                 mk[0] if sel is None else None, mk[1] if sel is None else None)
        if self.engine.mode == "source":
            self.source_bar.set_position(self.engine.position)
        else:
            self.source_bar.set_position(self.engine.source_positions.get(src["id"], 0.0))

    def _on_frame(self, f):
        (self.src_viewer if f.mode == "source" else self.viewer).set_frame(f)
        if f.hist is not None:
            self.color_panel.curve.set_hist(f.hist)

    def _on_position(self, t):
        fps = self.engine.fps()
        if self.engine.mode == "source":
            self.src_tc.setText(tc(t, fps))
            self.source_bar.set_position(t)
        else:
            self.tc_label.setText(tc(t, fps))
            ctx = self._anim_context() if self.right_tabs.currentIndex() == TAB_COLOR else None
            if ctx is not None:
                self.color_panel.set_playhead(t - ctx[0])
            self.timeline.overview.update()
            follow = self.engine.playing
            before = self.timeline.view.scroll_t
            self.timeline.view.set_playhead(t, follow=follow)
            if before != self.timeline.view.scroll_t:
                self.timeline.sync()

    # host della timeline
    def thumb_for(self, sid):
        return self.thumbs.get(sid)

    def peaks_for(self, sid):
        return self.peaks.get(sid)

    def timeline_scrolled(self):
        self.timeline.sync()

    def timeline_edit_done(self):
        self._refresh_used()
        self._mark_dirty()
        self._update_name_label()
        self.engine.refresh()

    # ============================================================ lista clip
    def _bin_selection(self, ids):
        self.active_panel = "bin"
        if self.color_panel.target() == "item":
            self.color_panel.set_target("source")
        self.refresh_color_panel()

    def _select_sub_from_bar(self, sid):
        self.bin.select([sid])
        self.selected_ref = sid
        self._update_name_label()
        self._update_source_bar()
        self.refresh_color_panel()

    def _edit_sub_live(self, sid, a, b):
        sub = self.project.update_subclip(sid, a, b)
        if sub:
            self.bin.update_subclip(sub)
            self._update_source_bar()

    def mark_in(self):
        self._mark(0)

    def mark_out(self):
        self._mark(1)

    def _mark(self, which):
        if self.engine.mode != "source":
            self.statusBar().showMessage("I segni I/O si usano sul visore sorgente: seleziona una clip.", 4000)
            return
        t = self.engine.position
        sub = self.project.subclip(self.selected_ref or "")
        if sub:
            self.project.checkpoint()
            if which == 0:
                self.project.update_subclip(sub["id"], tin=t, tout=max(sub["out"], t + self.engine.frame_step()))
            else:
                self.project.update_subclip(sub["id"], tin=min(sub["in"], t - self.engine.frame_step()), tout=t)
            self.bin.update_subclip(sub)
            self._mark_dirty()
        else:
            mk = self.marks.setdefault(self.engine.source_id, [None, None])
            mk[which] = t
            if mk[0] is not None and mk[1] is not None and mk[1] <= mk[0]:
                mk[1 - which] = None
        self._update_source_bar()

    def goto_mark(self, which):
        if self.engine.mode != "source":
            return
        sub = self.project.subclip(self.selected_ref or "")
        if sub:
            self.engine.seek(sub["in"] if which == 0 else sub["out"] - self.engine.frame_step())
            return
        mk = self.marks.get(self.engine.source_id)
        if mk and mk[which] is not None:
            self.engine.seek(mk[which])

    def create_subclip(self):
        if self.engine.mode != "source":
            return
        sid = self.engine.source_id
        src = self.project.source(sid)
        if src is None:
            return
        mk = self.marks.get(sid, [None, None])
        t = self.engine.position
        if mk[0] is None and mk[1] is None:
            a, b = t, min(src["duration"], t + 3.0)
        else:
            a = mk[0] if mk[0] is not None else 0.0
            b = mk[1] if mk[1] is not None else src["duration"]
        self.project.checkpoint()
        sub = self.project.add_subclip(sid, a, b)
        self.marks[sid] = [None, None]
        keep = self.selected_ref if self.project.source(self.selected_ref or "") else sid
        self.bin.rebuild(self.project, [keep])
        self._mark_dirty()
        self._update_source_bar()
        self.statusBar().showMessage(f"Creata {sub['id']} ({tc(sub['out'] - sub['in'], src['fps'])})", 3000)

    def _bin_refs(self):
        ids = self.bin.selected_ids()
        if not ids and self.selected_ref:
            ids = [self.selected_ref]
        return ids

    def append_selected(self):
        ids = self._bin_refs()
        if not ids:
            return
        self.project.checkpoint()
        placed = self.project.append_to_timeline(ids)
        if not placed:
            self.project.drop_checkpoint()
            return
        self.timeline.view.set_selection([i["id"] for i in placed], emit=False)
        self._after_timeline_edit()
        self.statusBar().showMessage(f"Accodate alla timeline: {', '.join(ids)}", 3000)

    def insert_at_playhead(self):
        ids = self._bin_refs()
        if not ids:
            return
        t = self.engine.source_positions.get("__timeline__", 0.0) if self.engine.mode == "source" \
            else self.engine.position
        self.add_refs_at(ids, t, 0, None)

    def add_refs_at(self, ids, t, vtrack, atrack, audio_row=False):
        """atrack None = automatico (musica sull'ultima traccia audio, il resto su A1).
        audio_row: rilasciato su una traccia audio (allora si usa quella)."""
        self.project.checkpoint()
        placed = []
        for rid in ids:
            at = atrack
            if at is None or (self.project.is_audio_only(rid) and not audio_row):
                at = self.project.music_track() if self.project.is_audio_only(rid) else (atrack or 0)
            its = self.project.add_to_timeline(rid, t, vtrack, at)
            if its:
                t = max(self.project.item_end(i) for i in its)
            placed += its
        if not placed:
            self.project.drop_checkpoint()
            return
        self.timeline.view.set_selection([i["id"] for i in placed], emit=False)
        self._after_timeline_edit()

    def _refresh_used(self):
        """Aggiorna nella lista le spunte delle clip già usate, solo se cambiano."""
        key = frozenset((i.get("origin"), i.get("source")) for i in self.project.items)
        if key != getattr(self, "_used_key", None):
            self._used_key = key
            self.bin.rebuild(self.project)

    def _after_timeline_edit(self):
        self.timeline.view.update()
        self.timeline.sync()
        self._refresh_used()
        self._mark_dirty()
        self._update_name_label()
        self.refresh_color_panel()
        self.refresh_inspector()
        self.engine.refresh()

    def bin_action(self, action, ids):
        pr = self.project
        if action == "import":
            return self.import_dialog()
        if action == "import_folder":
            return self.import_folder_dialog()
        if action == "import_audio":
            return self.import_audio_dialog()
        if action == "append":
            self.bin.select(ids)
            return self.append_selected()
        if action == "insert_playhead":
            self.bin.select(ids)
            return self.insert_at_playhead()
        if not ids:
            return
        if action.startswith("tag:"):
            col = action[4:]
            pr.checkpoint()
            for rid in ids:
                obj = pr.subclip(rid) or pr.source(rid)
                if obj is None:
                    continue
                if col:
                    obj["tag"] = col
                else:
                    obj.pop("tag", None)
            self.bin.rebuild(pr, ids)
            self.timeline.view.update()
            self._mark_dirty()
            return
        if action == "duplicate":
            subs = [i for i in ids if pr.subclip(i)]
            if not subs:
                return
            pr.checkpoint()
            new = [pr.duplicate_subclip(i)["id"] for i in subs]
            self.bin.rebuild(pr, new)
            self._mark_dirty()
            self._update_source_bar()
        elif action == "rename":
            rid = ids[0]
            obj = pr.subclip(rid) or pr.source(rid)
            if obj is None:
                return
            text, ok = QInputDialog.getText(self, "Rinomina", f"Etichetta per {rid} (il codice resta {rid}):",
                                            text=obj.get("label", ""))
            if ok:
                pr.checkpoint()
                obj["label"] = text.strip()
                self.bin.rebuild(pr, [rid])
                self._mark_dirty()
        elif action == "delete":
            srcs = [i for i in ids if pr.source(i)]
            subs = [i for i in ids if pr.subclip(i) and pr.subclip(i)["source"] not in srcs]
            if srcs:
                used = [s for s in srcs if any(it["source"] == s for it in pr.items)]
                msg = f"Rimuovere dal progetto {', '.join(srcs)} e tutte le sue parti?"
                if used:
                    msg += "\n\nAttenzione: i pezzi in timeline che le usano verranno eliminati."
                msg += "\n\n(Il file originale non viene toccato.)"
                if QMessageBox.question(self, "Elimina clip", msg) != QMessageBox.Yes:
                    return
            pr.checkpoint()
            for s in subs:
                pr.delete_subclip(s)
            for s in srcs:
                pr.delete_source(s)
            if self.selected_ref and (pr.subclip(self.selected_ref) is None and pr.source(self.selected_ref) is None):
                self.selected_ref = None
                if self.engine.mode == "source" and pr.source(self.engine.source_id) is None:
                    self.set_viewer_mode("timeline")
            self.refresh_all([])
            self._mark_dirty()
        elif action == "copy_grade":
            src, _, _ = pr.range_of(ids[0])
            if src:
                self.grade_clip = copy.deepcopy(color.normalize(src.get("grade")))
                self.statusBar().showMessage(f"Regolazioni di {src['id']} copiate", 3000)
        elif action == "paste_grade":
            if self.grade_clip is None:
                return
            pr.checkpoint()
            for sid in {pr.range_of(i)[0]["id"] for i in ids if pr.range_of(i)[0]}:
                pr.source(sid)["grade"] = copy.deepcopy(self.grade_clip)
            self.refresh_color_panel()
            self.engine.refresh()
            self.timeline.view.update()
            self._mark_dirty()
        elif action == "proxy":
            for sid in {pr.range_of(i)[0]["id"] for i in ids if pr.range_of(i)[0]}:
                self.start_proxy(pr.source(sid))
        elif action == "relink":
            src, _, _ = pr.range_of(ids[0])
            if src is None:
                return
            p, _ = QFileDialog.getOpenFileName(self, f"Ricollega {src['id']} ({src['name']})",
                                               os.path.dirname(src["path"]))
            if p:
                pr.checkpoint()
                pr.relink_source(src["id"], p)
                self.proxies.pop(src["id"], None)
                self.engine.invalidate_source(None)
                self.start_source_jobs(src)
                self.refresh_all([src["id"]])
        elif action == "reveal":
            src, _, _ = pr.range_of(ids[0])
            if src:
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(src["path"])))

    # ============================================================ timeline
    def _timeline_activated(self):
        self.active_panel = "timeline"
        if self.engine.mode != "timeline":
            self.set_viewer_mode("timeline")

    def _timeline_seek(self, t):
        if self.engine.mode != "timeline":
            self.set_viewer_mode("timeline")
        self.engine.seek(t)

    def _timeline_selection(self, ids):
        self.active_panel = "timeline"
        if ids and self.color_panel.target() == "source" and not self._src_target_sticky():
            self.color_panel.set_target("item")
        self.refresh_color_panel()
        self.refresh_inspector()

    def _src_target_sticky(self):
        return False

    def _timeline_changed(self):
        self.engine.refresh()
        self.timeline.sync()
        self.refresh_inspector()

    def select_all(self):
        if self.active_panel == "timeline" or self.engine.mode == "timeline":
            self.timeline.view.set_selection([i["id"] for i in self.project.items])

    def delete_selected(self):
        if self.active_panel == "timeline":
            kind = "ripple_delete" if self.timeline.view.ripple else "delete"
            self.timeline_action(kind, list(self.timeline.view.selection))
        else:
            self.bin_action("delete", self.bin.selected_ids())

    def jump_edit(self, direction):
        if self.engine.mode == "source":
            src = self.project.source(self.engine.source_id)
            pts = sorted({0.0, src["duration"]} | {x for c in self.project.subclips_of(src["id"])
                                                   for x in (c["in"], c["out"])}) if src else []
        else:
            pts = self.project.snap_points()
        t = self.engine.position
        fr = self.engine.frame_step() / 2
        if direction > 0:
            nxt = [p for p in pts if p > t + fr]
            if nxt:
                self.engine.seek(nxt[0])
        else:
            prv = [p for p in pts if p < t - fr]
            if prv:
                self.engine.seek(prv[-1])

    def timeline_action(self, action, ids):
        pr = self.project
        view = self.timeline.view
        tpos = self.engine.position if self.engine.mode == "timeline" else \
            self.engine.source_positions.get("__timeline__", 0.0)
        if action == "split":
            pr.checkpoint()
            created = pr.split_at(tpos, ids or None)
            if not created:
                pr.drop_checkpoint()
                return
        elif action in ("delete", "ripple_delete"):
            if not ids:
                return
            pr.checkpoint()
            pr.delete_items(ids, ripple=(action == "ripple_delete"))
            view.set_selection([])
        elif action == "unlink":
            pr.checkpoint()
            pr.unlink(ids)
        elif action == "link":
            pr.checkpoint()
            pr.link(ids)
        elif action == "gain":
            its = [pr.item(i) for i in ids if pr.item(i) and pr.item(i)["kind"] == "audio"]
            if not its:
                return
            val, ok = QInputDialog.getDouble(self, "Volume", "Guadagno (dB):", its[0].get("gain_db", 0.0), -60, 24, 1)
            if not ok:
                return
            pr.checkpoint()
            for it in its:
                it["gain_db"] = val
        elif action == "copy_grade":
            vid = self._video_items(ids)
            if vid:
                self.grade_clip = copy.deepcopy(color.normalize(vid[0].get("grade")))
                self.statusBar().showMessage(f"Regolazioni del pezzo {vid[0].get('origin')} copiate", 3000)
            return
        elif action == "paste_grade":
            vid = self._video_items(ids)
            if not vid or self.grade_clip is None:
                return
            pr.checkpoint()
            for it in vid:
                it["grade"] = copy.deepcopy(self.grade_clip)
        elif action == "reset_grade":
            pr.checkpoint()
            for it in self._video_items(ids):
                it["grade"] = color.default_grade()
        elif action == "reveal_in_bin":
            its = [pr.item(i) for i in ids if pr.item(i)]
            if its:
                ref = its[0].get("origin") or its[0]["source"]
                if not (pr.subclip(ref) or pr.source(ref)):
                    ref = its[0]["source"]
                self.bin.select([ref])
                self.selected_ref = ref
            return
        elif action.startswith("transition:"):
            kind = action.split(":", 1)[1] or None
            vids = self._video_items(ids)
            if not vids:
                return
            pr.checkpoint()
            dur = self.inspector.tr_dur.value()
            pr.set_transition([i["id"] for i in vids], kind, dur, self.inspector.tr_audio.isChecked())
            missing = [i for i in vids if kind and pr._prev_adjacent(i) is None]
            if missing:
                self.statusBar().showMessage("Per la transizione serve una clip attaccata prima sulla stessa "
                                             "traccia (usa l'aggancio magnetico).", 6000)
        elif action == "freeze":
            return self.freeze_at_playhead(ids)
        elif action.startswith("slip:"):
            its = [pr.item(i) for i in ids if pr.item(i)]
            its = [i for i in its if not spd.is_static(i)]
            if not its:
                self.statusBar().showMessage("Seleziona in timeline la clip di cui scorrere il contenuto.", 4000)
                return
            pr.checkpoint()
            frames = int(action.split(":", 1)[1])
            for it in its:
                src = pr.source(it["source"]) or {}
                pr.slip([it["id"]], frames / max(src.get("fps") or pr.fps, 1.0))
            self.slip_preview(its[0]["id"])
        elif action == "item_marker":
            its = [pr.item(i) for i in ids if pr.item(i)]
            its = [i for i in its if i["start"] - 1e-6 <= tpos <= pr.item_end(i) + 1e-6]
            if not its:
                self.statusBar().showMessage("Porta la testina sopra al pezzo selezionato.", 4000)
                return
            pr.checkpoint()
            pick = next((i for i in its if i["kind"] == "audio"), its[0])  # prima la musica
            pr.add_item_marker(pick["id"], tpos)
        elif action == "clear_item_markers":
            pr.checkpoint()
            for i in ids:
                if pr.item(i):
                    pr.item(i).pop("markers", None)
        elif action == "clear_gain":
            pr.checkpoint()
            for i in ids:
                if pr.item(i):
                    pr.item(i).pop("gain_pts", None)
        elif action == "add_marker_at":
            pr.checkpoint()
            pr.add_marker(float(ids[0]))
        elif action == "rename_marker":
            mk = pr.marker(ids[0])
            if mk is None:
                return
            text, ok = QInputDialog.getText(self, "Segnaposto", "Nome del segnaposto:", text=mk.get("label", ""))
            if not ok:
                return
            pr.checkpoint()
            mk["label"] = text.strip()
        elif action == "color_marker":
            mk = pr.marker(ids[0])
            if mk is None:
                return
            pr.checkpoint()
            mk["color"] = ids[1]
        elif action == "delete_marker":
            pr.checkpoint()
            pr.delete_marker(ids[0])
        elif action == "delete_all_markers":
            if not pr.markers:
                return
            pr.checkpoint()
            pr.data["markers"] = []
        elif action == "open_effects":
            self.show_tab(TAB_EFFECTS)
            return
        elif action == "open_color":
            self.show_tab(TAB_COLOR)
            return
        elif action == "add_video_track":
            pr.checkpoint()
            pr.add_track("video")
        elif action == "add_audio_track":
            pr.checkpoint()
            pr.add_track("audio")
        elif action == "remove_track":
            kind, idx = ids
            pr.checkpoint()
            if not pr.remove_track(kind, idx):
                pr.drop_checkpoint()
                QMessageBox.information(self, "Traccia", "Si possono rimuovere solo tracce vuote "
                                                          "(e ne deve restare almeno una).")
                return
        self._after_timeline_edit()

    def _video_items(self, ids):
        pr = self.project
        out = []
        seen = set()
        for i in pr.expand_linked(ids):
            it = pr.item(i)
            if it and it["kind"] == "video" and it["id"] not in seen:
                seen.add(it["id"])
                out.append(it)
        return out

    def timeline_settings(self):
        st = self.project.data["settings"]
        fps_src = sorted({f"{x['fps']:g} fps" for x in self.project.sources if x.get("has_video")})
        d = ProjectSettingsDialog(st, ", ".join(fps_src) if fps_src else None, self)
        if not d.exec():
            return
        vals = d.values()
        if all(st.get(k) == v for k, v in vals.items()):
            return
        self.project.checkpoint()
        st.update(vals)
        for it in self.project.items:  # riallinea i pezzi alla nuova griglia di fotogrammi
            it["start"] = self.project.snap(it["start"])
        if self.engine.mode == "timeline":
            self.engine.seek(self.engine.position)  # testina sulla nuova griglia di fotogrammi
        self._apply_aspect()
        self._after_timeline_edit()
        self.statusBar().showMessage(f"Progetto: {vals['width']}×{vals['height']}, {vals['fps']:g} fps", 4000)

    def set_fx_enabled(self, on):
        self.engine.fx_off = not on
        if self.fx_btn.isChecked() != on:
            self.fx_btn.setChecked(on)
        self.engine.refresh()
        self.statusBar().showMessage("Correzione colore accesa" if on else
                                     "Correzione colore spenta nell'anteprima (l'export la usa comunque)", 3000)

    def set_column_visible(self, on):
        self.side_dock.setVisible(on)

    def show_tab(self, index):
        """Mostra il pannello Effetti (0) o Colore (1), anche se era stato chiuso o staccato."""
        self.side_dock.show()
        self.side_dock.raise_()
        self.right_tabs.setCurrentIndex(index)

    def _update_settings_label(self):
        st = self.project.data["settings"]
        self.timeline.btn_settings.setText(f"Progetto: {float(st['fps']):g} fps · {st['width']}×{st['height']}")
        if getattr(self, "_preview_for", None) != self.project.size:
            self._fill_preview_combo()

    # ======================================================= proprietà pezzi
    def _sel_items(self, kind=None):
        pr = self.project
        its = [pr.item(i) for i in self.timeline.view.selection if pr.item(i)]
        if kind:
            its = [i for i in its if i["kind"] == kind]
        return sorted(its, key=lambda i: (i["start"], i["track"]))

    def _primary_item(self):
        for k in ("video", "text", "shape", "audio"):
            its = self._sel_items(k)
            if its:
                return its[0], len(its)
        return None, 0

    def refresh_inspector(self):
        it, n = self._primary_item()
        dur = self.project.item_dur(it) if it else None
        self.inspector.show_item(it, n, dur)
        if it is not None and it["kind"] == "video":
            prev = self.project._prev_adjacent(it)
            if prev is None:
                info = ("Nessuna clip attaccata prima sulla stessa traccia: avvicinala con l'aggancio "
                        "magnetico, oppure usa le dissolvenze da nero/bianco qui sotto.")
            else:
                info = (f"Con {prev.get('origin') or prev['source']}. La transizione è centrata sul taglio: "
                        "se prima o dopo mancano fotogrammi, si tiene fermo il primo o l'ultimo.")
            self.inspector.set_transition_info(info)
        texts = self._sel_items("text") + self._sel_items("shape")
        self.viewer.overlay.highlight = texts[0]["id"] if texts else None
        self.viewer.overlay.update()

    def _item_edit(self, kind, key, value):
        pr = self.project
        its = self._sel_items(kind)
        if not its:
            return
        if key == "speed":
            pr.set_speed([i["id"] for i in its], value)
        elif key == "trans":
            if value:
                pr.set_transition([i["id"] for i in its], value["type"], value["dur"], value["audio"])
            else:
                pr.set_transition([i["id"] for i in its], None)
        elif key == "speed_curve":
            pr.set_speed_curve([i["id"] for i in its], value)
        elif key == "duration":
            for it in its:
                it["src_out"] = it["src_in"] + max(pr.frame(), pr.snap(float(value)))
                pr.clear_range("video", it["track"], it["start"], pr.item_end(it), exclude={it["id"]})
        else:
            targets = list(its)
            if kind == "video" and key in ("fade_in", "fade_out") and self.inspector.fade_audio.isChecked():
                for it in its:
                    targets += [g for g in pr.linked(it) if g["kind"] == "audio"]
            for it in targets:
                it[key] = value
        self._mark_dirty()
        self.timeline.view.update()
        if key in ("speed", "speed_curve", "duration"):
            self.timeline.sync()
            self._update_name_label()
            self.refresh_inspector()
        self.engine.refresh()

    def _inspector_action(self, act):
        if act == "add_text":
            return self.add_text()
        if act == "add_box":
            return self.add_box()
        if act.startswith("freeze:"):
            return self.freeze_at_playhead(dur=float(act.split(":", 1)[1]))
        if act == "clear_gain":
            return self.timeline_action("clear_gain", [i["id"] for i in self._sel_items("audio")])
        pr = self.project
        its = self._sel_items("video")
        if not its:
            return
        self.project.checkpoint()
        W, H = pr.size
        for it in its:
            if act == "center":
                it["pos_x"] = 0.0
                it["pos_y"] = 0.0
            elif act == "fit":
                it["zoom"] = 1.0
                it["pos_x"] = it["pos_y"] = 0.0
            elif act == "fill":
                src = pr.source(it["source"])
                sw = (src or {}).get("width") or W
                sh = (src or {}).get("height") or H
                it["zoom"] = max(W / sw, H / sh) / min(W / sw, H / sh)
                it["pos_x"] = it["pos_y"] = 0.0
        self._after_timeline_edit()

    def _preview_item_fraction(self, u):
        """Mostra nel visore il fotogramma del pezzo selezionato a quella frazione
        della sua durata (nodi della curva di velocità)."""
        it, _ = self._primary_item()
        if it is None or spd.is_static(it):
            return
        if self.engine.mode != "timeline":
            self.set_viewer_mode("timeline")
        t = it["start"] + max(0.0, min(1.0, u)) * self.project.item_dur(it)
        self.engine.seek(min(t, self.project.item_end(it) - self.project.frame()))

    def slip_preview(self, iid):
        """Durante lo scorrimento mostra il primo fotogramma del pezzo."""
        it = self.project.item(iid)
        if it is None:
            return
        if self.engine.mode != "timeline":
            self.set_viewer_mode("timeline")
        self.engine.seek(it["start"])

    def marker_or_part(self):
        """Tasto M: sul visore sorgente crea la parte, in timeline mette un segnaposto
        (anche durante la riproduzione, per segnare i battiti della musica)."""
        if self.engine.mode == "source":
            return self.create_subclip()
        self.project.checkpoint()
        m = self.project.add_marker(self.engine.position)
        self.timeline.view.update()
        self._mark_dirty()
        self.statusBar().showMessage(f"Segnaposto a {tc(m['t'], self.project.fps)}", 1500)

    def freeze_at_playhead(self, ids=None, dur=None):
        pr = self.project
        t = self.engine.position if self.engine.mode == "timeline" else \
            self.engine.source_positions.get("__timeline__", 0.0)
        cand = self._video_items(ids if ids is not None else list(self.timeline.view.selection))
        cand = [i for i in cand if i.get("freeze") is None and i["start"] - 1e-6 <= t < pr.item_end(i) - 1e-6]
        if not cand:
            top = pr.video_item_at(t)
            cand = [top] if top is not None and top.get("freeze") is None else []
        if not cand:
            self.statusBar().showMessage("Porta la testina sopra a una clip video per fermare l'immagine.", 4000)
            return
        dur = self.inspector.freeze_dur.value() if dur is None else dur
        pr.checkpoint()
        fr = pr.insert_freeze(cand[0]["id"], t, dur)
        if fr is None:
            pr.drop_checkpoint()
            return
        self.timeline.view.set_selection([fr["id"]], emit=False)
        self._after_timeline_edit()
        self.statusBar().showMessage(f"Fermo immagine di {dur:g} s inserito.", 3000)

    def add_box(self):
        self.add_text(kind="shape")

    def add_text(self, kind="text"):
        pr = self.project
        if self.engine.mode == "timeline":
            t = self.engine.position
        else:
            t = self.engine.source_positions.get("__timeline__", 0.0)
        pr.checkpoint()
        it = pr.add_shape(t) if kind == "shape" else pr.add_text(t)
        if self.engine.mode != "timeline":
            self.set_viewer_mode("timeline")
        self.timeline.view.set_selection([it["id"]], emit=False)
        self.active_panel = "timeline"
        self.right_tabs.setCurrentIndex(TAB_EFFECTS)
        self._after_timeline_edit()
        if kind == "text":
            self.inspector.text_edit.setFocus()
            self.inspector.text_edit.selectAll()

    # trascinamento nel visore: sposta scritte e inquadratura
    def _viewer_drag_begin(self, nx, ny):
        self._vdrag = None
        if self.engine.mode != "timeline":
            self.set_viewer_mode("timeline")  # clic sul visore grande: si torna alla timeline
            return
        if self.engine.playing:
            return
        pr = self.project
        t = self.engine.position
        W, H = pr.size
        frame = QRectF(0, 0, W, H)
        target = None
        sel = self.timeline.view.selection
        texts = list(reversed(pr.texts_at(t)))
        hit = [x for x in texts if text_box(x, frame).contains(nx * W, ny * H)]
        if hit:
            target = next((x for x in hit if x["id"] in sel), hit[0])
        else:
            v = pr.video_item_at(t)
            if v is not None and (v["id"] in sel or not sel):
                target = v
        if target is None:
            return
        if target["id"] not in sel:
            if target["kind"] == "video":
                self.timeline.view.set_selection(pr.expand_linked([target["id"]]))
            else:
                self.timeline.view.set_selection([target["id"]])
        self.right_tabs.setCurrentIndex(TAB_EFFECTS)
        pr.checkpoint()
        self._vdrag = (target["id"], float(target.get("pos_x", 0.0)), float(target.get("pos_y", 0.0)))

    def _viewer_drag_move(self, dx, dy):
        if not self._vdrag:
            return
        iid, ox, oy = self._vdrag
        it = self.project.item(iid)
        if it is None:
            return
        if it["kind"] in ("text", "shape"):
            it["pos_x"] = min(1.0, max(0.0, ox + dx))
            it["pos_y"] = min(1.0, max(0.0, oy + dy))
        else:
            it["pos_x"] = min(2.0, max(-2.0, ox + dx))
            it["pos_y"] = min(2.0, max(-2.0, oy + dy))
        self.engine.refresh()
        self.refresh_inspector()

    def _viewer_drag_end(self):
        d = self._vdrag
        self._vdrag = None
        if not d:
            return
        it = self.project.item(d[0])
        if it is not None and abs(it.get("pos_x", 0) - d[1]) < 1e-9 and abs(it.get("pos_y", 0) - d[2]) < 1e-9:
            self.project.drop_checkpoint()
        else:
            self._mark_dirty()
            self.timeline.view.update()

    # =============================================================== colore
    def color_targets(self):
        """(lista di dict regolazione, descrizione) secondo il bersaglio scelto."""
        pr = self.project
        t = self.color_panel.target()
        if t == "global":
            pr.data["global_grade"] = color.normalize(pr.data.get("global_grade"))
            return [pr.data["global_grade"]], "Timeline: si applica a tutto il montaggio, sopra il resto."
        if t == "item":
            its = self._video_items(list(self.timeline.view.selection))
            for it in its:
                it["grade"] = color.normalize(it.get("grade"))
            if not its:
                return [], "Pezzo: seleziona uno o più pezzi video nella timeline."
            names = ", ".join(i.get("origin", "") for i in its[:4]) + (" …" if len(its) > 4 else "")
            return [i["grade"] for i in its], f"Pezzo: {names} (solo questi pezzi in timeline)"
        # clip intera
        if self.active_panel == "timeline" and self.timeline.view.selection:
            sids = []
            for it in self._video_items(list(self.timeline.view.selection)):
                if it["source"] not in sids:
                    sids.append(it["source"])
        else:
            sids = []
            for rid in self.bin.selected_ids() or ([self.selected_ref] if self.selected_ref else []):
                src, _, _ = pr.range_of(rid)
                if src and src["id"] not in sids:
                    sids.append(src["id"])
        srcs = [pr.source(s) for s in sids if pr.source(s)]
        for s in srcs:
            s["grade"] = color.normalize(s.get("grade"))
        if not srcs:
            return [], "Clip intera: seleziona una clip nella lista o un pezzo in timeline."
        return [s["grade"] for s in srcs], (f"Clip intera: {', '.join(s['id'] for s in srcs)} "
                                            f"(vale per tutte le sue parti, ovunque)")

    def _anim_context(self):
        """(inizio, durata) del tempo su cui si anima il colore: il pezzo
        selezionato o tutta la timeline; None per la clip intera."""
        t = self.color_panel.target()
        if t == "item":
            its = self._video_items(list(self.timeline.view.selection))
            if its:
                return its[0]["start"], self.project.item_dur(its[0])
            return None
        if t == "global":
            return 0.0, max(self.project.timeline_end(), 1.0)
        return None

    def _timeline_pos(self):
        return self.engine.position if self.engine.mode == "timeline" else \
            self.engine.source_positions.get("__timeline__", 0.0)

    def refresh_color_panel(self):
        grades, info = self.color_targets()
        self.color_panel.set_editable(bool(grades))
        ctx = self._anim_context()
        anim_ctx = (ctx[1], self._timeline_pos() - ctx[0]) if ctx else None
        self.color_panel.show_grade(grades[0] if grades else None, info, anim_ctx)

    def _anim_edit(self, key, pts):
        grades, _ = self.color_targets()
        ctx = self._anim_context()
        if not grades or ctx is None:
            return
        rel = self._timeline_pos() - ctx[0]
        for g in grades:
            anim = g.setdefault("anim", {})
            if pts:
                anim[key] = copy.deepcopy(pts)
            else:
                old = anim.pop(key, None)
                if old:  # torna fisso al valore che aveva alla testina
                    v = color.value_at(old, rel)
                    if key.startswith("hsl:"):
                        _, k, i = key.split(":")
                        g["hsl"][k][int(i)] = v
                    else:
                        g[key] = v
            if not anim:
                g.pop("anim", None)
        self._mark_dirty()
        self.engine.refresh()
        self.timeline.view.update()

    def _anim_seek(self, t_rel):
        ctx = self._anim_context()
        if ctx is None:
            return
        if self.engine.mode != "timeline":
            self.set_viewer_mode("timeline")
        self.engine.seek(ctx[0] + max(0.0, min(t_rel, ctx[1] - self.project.frame())))

    def _grade_begin(self, key):
        now = time.monotonic()
        last_key, last_t = self._grade_ck
        if key == last_key and now - last_t < 1.0:
            self._grade_ck = (key, now)
            return
        self._grade_ck = (key, now)
        self.project.checkpoint()

    def _grade_edit(self, key, value):
        grades, _ = self.color_targets()
        if not grades:
            return
        for g in grades:
            if key.startswith("curve:"):
                g["curves"][key[6:]] = copy.deepcopy(value)
            elif key.startswith("hsl:"):
                _, k, i = key.split(":")
                g["hsl"][k][int(i)] = float(value)
            else:
                g[key] = float(value)
        self._mark_dirty()
        self.engine.refresh()
        self.timeline.view.update()

    def _color_action(self, key):
        if key == "copia":
            self.copy_grade()
        elif key == "incolla":
            self.paste_grade()
        elif key == "azzera":
            self.reset_grade()
        elif key == "prima_dopo":
            self.act_bypass.setChecked(self.color_panel.bypass_btn.isChecked())

    def copy_grade(self):
        grades, _ = self.color_targets()
        if grades:
            self.grade_clip = copy.deepcopy(color.normalize(grades[0]))
            self.statusBar().showMessage("Regolazioni copiate: seleziona altre clip e incolla "
                                         "(Ctrl/Cmd+Maiusc+V).", 4000)

    def paste_grade(self):
        if self.grade_clip is None:
            self.statusBar().showMessage("Niente da incollare: prima copia delle regolazioni.", 3000)
            return
        grades, _ = self.color_targets()
        if not grades:
            return
        self.project.checkpoint()
        for g in grades:
            g.clear()
            g.update(copy.deepcopy(self.grade_clip))
        self.refresh_color_panel()
        self._mark_dirty()
        self.engine.refresh()
        self.timeline.view.update()
        self.statusBar().showMessage(f"Regolazioni incollate su {len(grades)} elementi.", 3000)

    def reset_grade(self):
        grades, _ = self.color_targets()
        if not grades:
            return
        self.project.checkpoint()
        for g in grades:
            g.clear()
            g.update(color.default_grade())
        self.refresh_color_panel()
        self._mark_dirty()
        self.engine.refresh()
        self.timeline.view.update()

    def set_bypass(self, on):
        self.engine.bypass = on
        self.engine._lut_cache.clear()
        self.color_panel.bypass_btn.setChecked(on)
        self.engine.refresh()
        self.statusBar().showMessage("Prima (originale)" if on else "Dopo (con correzione)", 2000)

    def save_preset(self):
        grades, _ = self.color_targets()
        if not grades:
            return
        name, ok = QInputDialog.getText(self, "Salva preset", "Nome del preset:")
        name = "".join(ch for ch in name if ch.isalnum() or ch in " _-").strip()
        if ok and name:
            with open(os.path.join(paths.presets_dir(), name + ".json"), "w", encoding="utf-8") as f:
                json.dump(color.normalize(grades[0]), f, indent=1)
            self.statusBar().showMessage(f"Preset «{name}» salvato.", 3000)

    def _fill_presets(self):
        self.preset_menu.clear()
        files = sorted(f for f in os.listdir(paths.presets_dir()) if f.endswith(".json"))
        if not files:
            a = self.preset_menu.addAction("(nessun preset salvato)")
            a.setEnabled(False)
        for f in files:
            self.preset_menu.addAction(f[:-5], lambda ff=f: self._apply_preset(ff))

    def _apply_preset(self, fname):
        try:
            with open(os.path.join(paths.presets_dir(), fname), encoding="utf-8") as f:
                g = color.normalize(json.load(f))
        except Exception as e:
            QMessageBox.warning(self, "Preset", str(e))
            return
        old = self.grade_clip
        self.grade_clip = g
        self.paste_grade()
        self.grade_clip = old

    # ================================================================ media
    def import_dialog(self):
        exts = " ".join("*" + e for e in sorted(media.MEDIA_EXT | {e.upper() for e in media.MEDIA_EXT}))
        files, _ = QFileDialog.getOpenFileNames(self, "Importa clip", self.cfg.get("last_import_dir", ""),
                                                f"Video e audio ({exts});;Tutti i file (*)")
        if files:
            self.cfg["last_import_dir"] = os.path.dirname(files[0])
            self.import_paths(files)

    def import_audio_dialog(self):
        exts = " ".join("*" + e for e in sorted(media.AUDIO_EXT | {e.upper() for e in media.AUDIO_EXT}))
        files, _ = QFileDialog.getOpenFileNames(self, "Importa musica e suoni", self.cfg.get("last_audio_dir", ""),
                                                f"Audio ({exts});;Tutti i file (*)")
        if files:
            self.cfg["last_audio_dir"] = os.path.dirname(files[0])
            self.import_paths(files)

    def import_folder_dialog(self):
        d = QFileDialog.getExistingDirectory(self, "Importa cartella", self.cfg.get("last_import_dir", ""))
        if d:
            self.cfg["last_import_dir"] = d
            self.import_paths([d])

    def import_paths(self, items):
        files = []
        for p in items:
            if os.path.isdir(p):
                for root, dirs, fs in os.walk(p):
                    dirs[:] = [d for d in sorted(dirs) if not d.startswith(".")]
                    files += [os.path.join(root, f) for f in sorted(fs) if media.is_media(f)
                              and not f.startswith("._")]
            elif media.is_media(p):
                files.append(p)
        files = [f for f in files if self.project.find_source_by_path(f) is None]
        if not files:
            self.statusBar().showMessage("Nessun nuovo file video o audio da importare.", 4000)
            return
        self.statusBar().showMessage(f"Analisi di {len(files)} file…")
        self._pending_imports += files
        for f in files:
            fut = self.jobs.submit(media.probe, f)
            fut.add_done_callback(lambda fu, ff=f: self.media_event.emit(
                "probed", ff, fu.exception() or fu.result()))

    def _on_media_event(self, kind, key, payload):
        pr = self.project
        if kind == "probed":
            self._import_results[key] = payload
            if not all(f in self._import_results for f in self._pending_imports):
                return
            # aggiunge nell'ordine scelto, dopo che tutti i file sono stati analizzati
            pr.checkpoint()
            errors = []
            added = []
            for f in self._pending_imports:
                res = self._import_results[f]
                if isinstance(res, BaseException):
                    errors.append(f"{os.path.basename(f)}: {res}")
                    continue
                if pr.find_source_by_path(f):
                    continue
                src = pr.add_source(f, res)
                added.append(src)
            self._pending_imports = []
            self._import_results = {}
            if not added:
                pr.drop_checkpoint()
            for src in added:
                self.start_source_jobs(src)
            self.refresh_all([added[0]["id"]] if added else None)
            if added and self.engine.mode != "source":
                self.show_ref(added[0]["id"])
            msg = f"Importate {len(added)} clip."
            if errors:
                QMessageBox.warning(self, "Alcuni file non sono stati importati", "\n".join(errors[:15]))
            self.statusBar().showMessage(msg, 5000)
        elif kind == "audio_ready":
            self.store.register(key, payload)
            self._set_status(key)
        elif kind == "peaks":
            self.peaks[key] = payload
            self.timeline.view.update()
        elif kind == "thumb":
            pm = QPixmap(payload)
            if not pm.isNull():
                self.thumbs[key] = pm.scaled(QSize(160, 90), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.bin.set_thumb(key, payload)
                self.timeline.view.update()
        elif kind == "proxy_progress":
            self.bin.set_status(key, f"proxy {int(payload * 100)}%")
        elif kind == "proxy_ready":
            self._busy_proxy.discard(key)
            if payload:
                self.proxies[key] = payload
                src = pr.source(key)
                if src:
                    self.engine.invalidate_source(src["path"])
            self._set_status(key)
        elif kind == "error":
            self._busy_proxy.discard(key)
            self.bin.set_status(key, "errore")
            self.statusBar().showMessage(f"{key}: {payload}", 8000)

    def _set_status(self, sid):
        parts = []
        if sid in self.proxies:
            parts.append("proxy ✓")
        src = self.project.source(sid)
        if src and src.get("has_audio") and not self.store.ready(sid):
            parts.append("audio…")
        self.bin.set_status(sid, " · ".join(parts))

    def start_source_jobs(self, src):
        sid = src["id"]
        path = src["path"]
        if not os.path.exists(path):
            return
        emit = self.media_event.emit

        if src.get("has_audio"):
            def do_audio():
                try:
                    raw = media.make_audio_cache(path)
                    emit("audio_ready", sid, raw)
                    emit("peaks", sid, media.make_peaks(path))
                except Exception as e:
                    emit("error", sid, f"audio: {e}")

            self.bin.set_status(sid, "audio…")
            self.jobs.submit(do_audio)
        if src.get("has_video"):
            def do_thumb():
                try:
                    emit("thumb", sid, media.make_thumb(path, min(1.0, src["duration"] / 3)))
                except Exception:
                    pass

            self.jobs.submit(do_thumb)
            pp = media.proxy_path(path)
            if os.path.exists(pp):
                self.proxies[sid] = pp
                self._set_status(sid)
            elif self.cfg.get("auto_proxy", True) and media.needs_proxy(src):
                self.start_proxy(src)

    def start_proxy(self, src):
        if src is None or not src.get("has_video") or src["id"] in self._busy_proxy:
            return
        sid = src["id"]
        if sid in self.proxies:
            return
        self._busy_proxy.add(sid)
        path = src["path"]
        dur = src["duration"]
        emit = self.media_event.emit
        cancel = self.jobs.cancel
        last = [0.0]

        def prog(f):
            if f - last[0] >= 0.02:
                last[0] = f
                emit("proxy_progress", sid, f)

        def do():
            try:
                emit("proxy_progress", sid, 0.0)
                emit("proxy_ready", sid, media.make_proxy(path, dur, progress=prog, cancel=cancel))
            except Exception as e:
                emit("error", sid, f"proxy: {e}")

        self.jobs.submit_proxy(do)

    # ============================================================== progetto
    def _confirm_discard(self):
        if not self.project.dirty:
            return True
        r = QMessageBox.question(self, "Modifiche non salvate", "Salvare le modifiche al progetto?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def _reset_state(self):
        self.engine.stop()
        self.store = audio.AudioStore()
        self.engine.store = self.store
        self.engine.invalidate_source(None)
        self.engine._lut_cache.clear()
        self.thumbs.clear()
        self.peaks.clear()
        self.proxies.clear()
        self.marks.clear()
        self.selected_ref = None
        self.timeline.view.selection.clear()
        self.engine.source_positions.clear()
        self.engine.position = 0.0
        self.timeline.view.set_playhead(0.0)
        self.set_viewer_mode("timeline", force=True)

    def new_project(self):
        if not self._confirm_discard():
            return
        self._clear_autosave()
        self.project = Project()
        self._reset_state()
        self.refresh_all([])

    def open_project(self, path=None, recover_from=None):
        """recover_from: file di autosalvataggio da cui ripartire (dopo una
        chiusura anomala); il progetto resta associato a path."""
        if recover_from is None:
            if not self._confirm_discard():
                return
            self._clear_autosave()
        if not path and recover_from is None:
            path, _ = QFileDialog.getOpenFileName(self, "Apri progetto", self.cfg.get("last_project_dir", ""),
                                                  "Progetto Moviola (*.moviola)")
            if not path:
                return
        try:
            pr = Project.load(recover_from or path)
        except Exception as e:
            QMessageBox.warning(self, "Impossibile aprire", str(e))
            return
        pr.path = path or None
        pr.dirty = recover_from is not None
        self.project = pr
        self._reset_state()
        for src in pr.sources:
            raw = media.audio_cache_path(src["path"])
            if os.path.exists(raw):
                self.store.register(src["id"], raw)
            self.start_source_jobs(src)
        self.act_proxy.setChecked(pr.data.get("use_proxies", True))
        if path:
            self._remember_recent(path)
        self.refresh_all([])
        self.timeline.view.zoom_fit()
        missing = pr.missing_sources()
        if missing:
            QMessageBox.warning(self, "File non trovati",
                                "Questi file originali non sono stati trovati:\n\n"
                                + "\n".join(f"{s['id']}: {s['path']}" for s in missing[:15])
                                + "\n\nUsa «Ricollega file…» (clic destro sulla clip).")

    def save(self):
        if not self.project.path:
            return self.save_as()
        try:
            self._backup_before_save(self.project.path)
            self.project.save()
            self._clear_autosave()
        except Exception as e:
            QMessageBox.warning(self, "Salvataggio non riuscito", str(e))
            return False
        self._remember_recent(self.project.path)
        self._update_title()
        self.statusBar().showMessage("Progetto salvato.", 3000)
        return True

    # ------------------------------------------- autosalvataggio e copie
    AUTOSAVE_SECONDS = 120
    BACKUPS_KEPT = 10

    def _autosave_path(self):
        import hashlib

        base = os.path.splitext(os.path.basename(self.project.path))[0] if self.project.path else "senza_titolo"
        key = hashlib.sha1((self.project.path or f"nuovo-{id(self.project)}").encode()).hexdigest()[:8]
        return os.path.join(paths.cache_dir("autosalvataggi"), f"{base}_{key}.moviola")

    def _autosave(self):
        pr = self.project
        if not pr.dirty or not (pr.sources or pr.items):
            return
        try:
            target = self._autosave_path()
            tmp = target + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(pr.data, f, ensure_ascii=False)
            os.replace(tmp, target)
            self.cfg["autosave"] = {"file": target, "project": pr.path, "time": time.time()}
            with open(paths.config_path(), "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=1)
            self.statusBar().showMessage("Salvataggio automatico eseguito.", 2000)
        except Exception as e:
            self.statusBar().showMessage(f"Salvataggio automatico non riuscito: {e}", 5000)

    def _clear_autosave(self):
        info = self.cfg.pop("autosave", None)
        if info and info.get("file"):
            try:
                os.remove(info["file"])
            except OSError:
                pass

    def _backup_before_save(self, path):
        """Prima di sovrascrivere il progetto ne tiene una copia (le ultime 10)."""
        import shutil

        if not os.path.exists(path):
            return
        try:
            d = paths.cache_dir("copie_di_sicurezza")
            base = os.path.splitext(os.path.basename(path))[0]
            stamp = time.strftime("%Y%m%d-%H%M%S")
            shutil.copy2(path, os.path.join(d, f"{base}_{stamp}.moviola"))
            olds = sorted(f for f in os.listdir(d) if f.startswith(base + "_") and f.endswith(".moviola"))
            for f in olds[:-self.BACKUPS_KEPT]:
                os.remove(os.path.join(d, f))
        except Exception:
            pass

    def startup(self):
        """All'avvio: se l'ultima volta Moviola non si è chiusa bene propone il
        recupero, altrimenti mostra i progetti recenti."""
        info = self.cfg.get("autosave")
        if info and info.get("file") and os.path.exists(info["file"]):
            name = os.path.basename(info["project"]) if info.get("project") else "un progetto non salvato"
            when = time.strftime("%d/%m alle %H:%M", time.localtime(info.get("time", time.time())))
            r = QMessageBox.question(
                self, "Recupero progetto",
                f"L'ultima volta Moviola non si è chiusa normalmente.\n\n"
                f"C'è un salvataggio automatico di {name} del {when}.\nVuoi recuperarlo?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if r == QMessageBox.Yes:
                self.open_project(info.get("project"), recover_from=info["file"])
                self.statusBar().showMessage("Progetto recuperato: salvalo per confermare le modifiche.", 8000)
                return
            self._clear_autosave()
        self.show_welcome()

    def save_as(self):
        start = self.project.path or os.path.join(self.cfg.get("last_project_dir", os.path.expanduser("~")),
                                                  "montaggio.moviola")
        path, _ = QFileDialog.getSaveFileName(self, "Salva progetto", start, "Progetto Moviola (*.moviola)")
        if not path:
            return False
        if not path.endswith(".moviola"):
            path += ".moviola"
        self.project.path = path
        return self.save()

    def _remember_recent(self, path):
        self.cfg["last_project_dir"] = os.path.dirname(path)
        rec = [p for p in self.cfg.get("recent", []) if p != path]
        self.cfg["recent"] = [path] + rec[:11]
        self._fill_recent()
        try:  # salvato subito, così l'elenco resta anche se il programma si chiude male
            with open(paths.config_path(), "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=1)
        except Exception:
            pass

    def _fill_recent(self):
        self.recent_menu.clear()
        rec = [p for p in self.cfg.get("recent", []) if os.path.exists(p)]
        if not rec:
            a = self.recent_menu.addAction("(nessuno)")
            a.setEnabled(False)
        for p in rec:
            self.recent_menu.addAction(os.path.basename(p), lambda pp=p: self.open_project(pp))

    def undo(self):
        if self.project.undo():
            self.refresh_all()
            self.statusBar().showMessage("Annullato.", 1500)

    def redo(self):
        if self.project.redo():
            self.refresh_all()
            self.statusBar().showMessage("Ripetuto.", 1500)

    def export_dialog(self):
        if self.project.timeline_end() <= 0:
            QMessageBox.information(self, "Esporta", "La timeline è vuota.")
            return
        self.engine.stop()
        d = ExportDialog(self.project, self.store, self.cfg.get("last_export_dir", os.path.expanduser("~")), self,
                         text_renderer=render_png)
        if d.exec():
            self.cfg["last_export_dir"] = os.path.dirname(d.path.text())

    # ============================================================== opzioni
    def set_quality(self, q):
        self.engine.quality = q
        self.cfg["quality"] = q
        if q in self.quality_actions:
            self.quality_actions[q].setChecked(True)
        self._fill_preview_combo()
        self.engine.invalidate_source(None)

    def _fill_preview_combo(self):
        """Elenco delle risoluzioni di anteprima calcolate su quella del progetto."""
        from ..playback import preview_height

        W, H = self.project.size
        cb = self.preview_combo
        cb.blockSignals(True)
        cb.clear()
        for k, lab in (("piena", "Piena"), ("meta", "1/2"), ("terzo", "1/3"), ("quarto", "1/4")):
            h = preview_height(H, k)
            w = int(round(W * h / H))
            w -= w % 2
            cb.addItem(f"{lab}  ·  {w}×{h}", k)
        i = cb.findData(self.engine.quality)
        cb.setCurrentIndex(i if i >= 0 else 1)
        cb.blockSignals(False)
        self._preview_for = (W, H)

    def set_use_proxy(self, on):
        self.project.data["use_proxies"] = on
        self.engine.invalidate_source(None)

    def set_autoproxy(self, on):
        self.cfg["auto_proxy"] = on

    def set_gpu(self, on):
        self.cfg["gpu"] = on
        self._make_viewer(on)
        self._apply_aspect()
        self.engine.refresh()

    def show_shortcuts(self):
        QMessageBox.about(self, "Scorciatoie da tastiera", SHORTCUTS)

    def closeEvent(self, e):
        if not self._confirm_discard():
            e.ignore()
            return
        self._clear_autosave()  # chiusura regolare: niente da recuperare
        self.engine.shutdown()
        self.jobs.shutdown()
        self.cfg["geometry"] = bytes(self.saveGeometry().toHex()).decode()
        self.cfg["state"] = bytes(self.saveState().toHex()).decode()
        e.accept()
