"""Finestra principale: collega lista clip, visore, timeline e colore."""
from __future__ import annotations

import copy
import json
import os
import sys
import time

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence, QPixmap
from PySide6.QtWidgets import (QComboBox, QSizePolicy, QStackedWidget, QTabBar, QTabWidget, QToolBar, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
                               QMainWindow, QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget)

from .. import __version__, stabilize, audio, color, media, paths, update
from .. import speed as spd
from ..playback import Engine
from ..project import Project, is_overlay
from .bin_panel import TAGS, BinPanel
from .color_panel import ColorPanel
from .export_dialog import ExportDialog
from . import icons
from .inspector import Inspector
from .meters import DB_MIN, LevelMeter
from .scrub import ArrowScrubber
from .preferences import DEFAULT_LIMIT_GB, PreferencesDialog
from .welcome import ProjectsPanel
from .project_settings import ProjectSettingsDialog
from .text_render import default_family, render_png, text_box
from .source_bar import SourceBar
from .timeline import TimelinePanel, tc
from .viewer import CpuViewer, GlViewer

TAB_CLIP, TAB_EFFECTS, TAB_COLOR = 0, 1, 2
PAGE_PROJECTS, PAGE_CLIP, PAGE_MONTAGE = 0, 1, 2
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
E: accoda alla timeline · F9: inserisci alla testina della timeline<br><br>
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
        self._sub_thumb_at: dict[str, float] = {}  # parte -> istante della miniatura già richiesta
        self.peaks: dict[str, object] = {}
        self.proxies: dict[str, str] = {}
        self.marks: dict[str, list] = {}
        self.selected_ref = None
        self.active_panel = "bin"
        self.grade_clip = None
        self._grade_ck = (None, 0.0)
        self._busy_proxy = set()
        self._busy_stab = {}  # clip in analisi per la stabilizzazione -> avanzamento
        self._pending_imports = []
        self._import_results = {}
        self._import_bins = {}  # file -> cartella della lista clip
        self._import_target = None
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
        self.scrubber = ArrowScrubber(self, self.engine,
                                      lambda msg, ms: self.statusBar().showMessage(msg, ms) if msg
                                      else self.statusBar().clearMessage())
        self.meter_timer = QTimer(self)
        self.meter_timer.setInterval(40)
        self.meter_timer.timeout.connect(self._update_meter)
        self.meter_timer.start()
        self.set_page(PAGE_PROJECTS)
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
        self.mon_holder = QVBoxLayout()
        self.mon_holder.setContentsMargins(0, 0, 0, 0)
        vrow = QHBoxLayout()
        vrow.setSpacing(8)

        def caption():
            c = QLabel("")
            c.setMinimumWidth(10)
            c.setTextFormat(Qt.RichText)
            c.setFixedHeight(34)
            c.setStyleSheet("color:#9a9a9a;")
            return c

        # a sinistra lo schermo della sorgente, a destra quello della timeline
        self.mon_box = QWidget()
        mv = QVBoxLayout(self.mon_box)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(2)
        mv.addLayout(self.mon_holder, 1)
        self.mon_info = caption()
        mv.addWidget(self.mon_info)
        vrow.addWidget(self.mon_box, 1)
        tv = QVBoxLayout()
        tv.setSpacing(2)
        tv.addLayout(self.viewer_holder, 1)
        self.tl_info = caption()
        tv.addWidget(self.tl_info)
        vrow.addLayout(tv, 1)
        self.meter = LevelMeter()
        vrow.addWidget(self.meter)
        tl.addLayout(vrow, 1)
        self.src_viewer_holder = QVBoxLayout()
        self.src_viewer_holder.setContentsMargins(0, 0, 0, 0)
        self.viewer = None
        self.src_viewer = None
        self.mon_viewer = None
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
        work = QSplitter(Qt.Vertical)
        work.addWidget(top)
        work.addWidget(self.timeline)
        work.setStretchFactor(0, 3)
        work.setStretchFactor(1, 2)
        work.setSizes([520, 330])

        # colonna della scheda MONTAGGIO: CLIP (solo elenco) · EFFETTI · COLORE
        self.bin = BinPanel()
        self.color_panel = ColorPanel()
        self.inspector = Inspector()
        self.right_tabs = QTabWidget()
        self.right_tabs.setDocumentMode(True)
        self.right_tabs.addTab(self.bin, "CLIP")
        self.right_tabs.addTab(self.inspector, "EFFETTI")
        self.right_tabs.addTab(self.color_panel, "COLORE")
        self.right_tabs.tabBar().setExpanding(True)
        self.right_tabs.setCurrentIndex(TAB_EFFECTS)
        self.right_tabs.setMinimumWidth(220)
        self.right_tabs.setStyleSheet(TAB_QSS)

        self.mont_split = QSplitter(Qt.Horizontal)
        self.mont_split.addWidget(self.right_tabs)
        self.mont_split.addWidget(work)
        self.mont_split.setStretchFactor(0, 0)
        self.mont_split.setStretchFactor(1, 1)
        self.mont_split.setSizes([360, 1080])

        # scheda CLIP: elenco per cartelle a sinistra, visore grande a destra
        self.clip_split = QSplitter(Qt.Horizontal)
        self.clip_split.setChildrenCollapsible(False)
        self.clip_split.addWidget(self.src_panel)
        self.clip_split.setStretchFactor(0, 1)

        # scheda PROGETTI
        self.projects = ProjectsPanel(self.cfg)
        self.projects.chosen.connect(self._project_chosen)
        self.projects.changed.connect(lambda: (self._save_cfg(), self._fill_recent()))

        self.pages = QStackedWidget()
        self.pages.addWidget(self.projects)
        self.pages.addWidget(self.clip_split)
        self.pages.addWidget(self.mont_split)
        self.setCentralWidget(self.pages)
        self.page = None
        self.statusBar().showMessage("Pronto.")

    # ------------------------------------------------------------ schede
    def _build_page_bar(self):
        """Le tre schede principali, al centro della barra in alto."""
        self.page_bar = QTabBar()
        self.page_bar.setDrawBase(False)
        self.page_bar.setExpanding(False)
        for lab in ("PROGETTI", "CLIP", "MONTAGGIO"):
            self.page_bar.addTab(lab)
        self.page_bar.setStyleSheet(
            "QTabBar::tab { padding: 7px 26px; font-weight: 700; letter-spacing: 2px; color: #8d8d8d;"
            " background: transparent; border: none; border-bottom: 2px solid transparent; }"
            "QTabBar::tab:selected { color: #f2f2f2; border-bottom: 2px solid #e8a33d; }"
            "QTabBar::tab:hover:!selected { color: #d0d0d0; }")
        self.page_bar.setToolTip("Progetti (Ctrl/Cmd+Maiusc+1) · Clip (Ctrl/Cmd+Maiusc+2) · "
                                 "Montaggio (Ctrl/Cmd+Maiusc+3)")
        self.page_bar.currentChanged.connect(self.set_page)
        return self.page_bar

    def set_page(self, index):
        """Passa a una delle tre schede (0 progetti, 1 clip, 2 montaggio)."""
        if index == self.page:
            return
        prev = self.page
        self.page = index
        if self.page_bar.currentIndex() != index:
            self.page_bar.blockSignals(True)
            self.page_bar.setCurrentIndex(index)
            self.page_bar.blockSignals(False)
        if self.engine.playing:
            self.engine.stop()
        if index == PAGE_CLIP:
            # l'elenco delle clip passa a sinistra del visore grande
            keep = self.right_tabs.currentIndex()
            i = self.right_tabs.indexOf(self.bin)
            if i >= 0:
                self.right_tabs.removeTab(i)
                self.right_tabs.setCurrentIndex(max(0, keep - 1))
            self.clip_split.insertWidget(0, self.bin)
            self.bin.show()
            self.clip_split.setSizes(self.cfg.get("clip_split_v2", [470, 970]))
        elif prev == PAGE_CLIP:
            self.cfg["clip_split_v2"] = self.clip_split.sizes()
        if index != PAGE_CLIP and self.right_tabs.indexOf(self.bin) < 0:
            keep = self.right_tabs.currentIndex()
            self.right_tabs.insertTab(0, self.bin, "CLIP")
            self.right_tabs.setCurrentIndex(keep + 1)
        self.pages.setCurrentIndex(index)
        if index == PAGE_PROJECTS:
            self.projects.set_current(self.project.path)
            self.projects.refresh()
        elif index == PAGE_CLIP:
            self.show_current_source()
        else:
            self.set_viewer_mode("timeline")
            self._update_monitor()

    def _project_chosen(self, kind, path):
        if kind == "open":
            self.open_project(path)
        elif kind == "browse":
            self.open_project()
        elif kind == "new":
            self.create_project(path)

    def _after_project_loaded(self):
        """Aperto o creato un progetto: si va alle clip se la timeline è vuota,
        altrimenti al montaggio."""
        self.set_page(PAGE_MONTAGE if self.project.items else PAGE_CLIP)

    def _build_source_panel(self):
        """Scheda CLIP: visore grande per guardare le clip e sceglierne le parti."""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 6)
        v.setSpacing(4)
        head = QHBoxLayout()
        self.src_name = QLabel("Seleziona una clip nell'elenco")
        self.src_name.setStyleSheet("color:#cfcfcf; font-size: 13px;")
        self.src_name.setMinimumWidth(10)
        head.addWidget(self.src_name, 1)
        head.addWidget(QLabel("Anteprima"))
        self.src_quality_combo = QComboBox()
        for k, lab in (("piena", "Piena"), ("meta", "1/2"), ("terzo", "1/3"), ("quarto", "1/4 (la più fluida)")):
            self.src_quality_combo.addItem(lab, k)
        self.src_quality_combo.setToolTip("Qualità dell'anteprima delle clip: più bassa = più fluida.\n"
                                          "Fino a 540 righe si usano i proxy, se ci sono.")
        self.src_quality_combo.setFocusPolicy(Qt.NoFocus)
        i = self.src_quality_combo.findData(self.cfg.get("src_quality", "meta"))
        self.src_quality_combo.setCurrentIndex(max(0, i))
        self.engine.src_quality = self.src_quality_combo.currentData()
        self.src_quality_combo.currentIndexChanged.connect(
            lambda _: self.set_src_quality(self.src_quality_combo.currentData()))
        head.addWidget(self.src_quality_combo)
        v.addLayout(head)
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
        r1.setSpacing(4)
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
        r2 = r1
        r2.addSpacing(12)
        btn(r2, "I", "Ingresso (I)", lambda: self._src(self.mark_in), 30)
        btn(r2, "O", "Uscita (O)", lambda: self._src(self.mark_out), 30)
        btn(r2, "+ Parte", "Crea una parte dai segni I/O (Invio)", lambda: self._src(self.create_subclip))
        r2.addStretch(1)
        btn(r2, "Accoda ⤵", "Accoda alla timeline (E)", self.append_selected)
        btn(r2, "Alla testina", "Inserisci alla testina della timeline (F9)", self.insert_at_playhead)
        v.addLayout(r1)
        return w

    def set_src_quality(self, q):
        self.engine.src_quality = q
        self.cfg["src_quality"] = q
        self.engine.invalidate_source(None)

    def _bin_hover(self, rid, frac):
        """Passando il mouse sulla lista, il visore piccolo mostra la clip sotto
        al puntatore (da sinistra a destra = dall'inizio alla fine)."""
        if self.page == PAGE_MONTAGE:
            # nel montaggio il visore della timeline non si tocca: si muove lo schermo sorgente
            if not rid:
                self._update_monitor()
                return
            src, a, b = self.project.range_of(rid)
            if src is None or not src.get("has_video"):
                return
            fr = 1.0 / max(src.get("fps") or 25, 1)
            t = a + frac * max(0.0, b - a - fr)
            self.engine.peek(src["id"], t)
            self._mon_caption(rid, src, a, b, t)
            return
        if self.engine.playing or self.page != PAGE_CLIP:
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
        if self.page != PAGE_MONTAGE:
            self.set_page(PAGE_MONTAGE)
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
        v.setMinimumSize(200, 112)
        return v

    def _make_viewer(self, gpu: bool):
        """Crea i due visori: quello grande (timeline) e quello piccolo (clip)."""
        for old, holder in ((self.viewer, self.viewer_holder), (self.src_viewer, self.src_viewer_holder),
                            (self.mon_viewer, self.mon_holder)):
            if old is not None:
                holder.removeWidget(old)
                old.deleteLater()
        v = self._new_viewer(gpu)
        sv = self._new_viewer(gpu)
        mv = self._new_viewer(gpu)
        self.engine.cpu_grade = isinstance(v, CpuViewer)
        self.viewer_holder.addWidget(v)
        self.src_viewer_holder.addWidget(sv)
        self.mon_holder.addWidget(mv)
        self.viewer = v
        self.src_viewer = sv
        self.mon_viewer = mv
        v.overlay.drag_begin.connect(self._viewer_drag_begin)
        v.overlay.drag_move.connect(self._viewer_drag_move)
        v.overlay.drag_end.connect(self._viewer_drag_end)
        sv.overlay.drag_begin.connect(lambda *_: self._src(lambda: None))
        self.engine._lut_cache.clear()

    def _gl_failed(self, msg):
        QTimer.singleShot(0, lambda: self._switch_cpu(msg))

    def _switch_cpu(self, msg):
        if all(isinstance(x, CpuViewer) for x in (self.viewer, self.src_viewer, self.mon_viewer)):
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
        act(m, "Progetti (gestione)…", self.show_welcome, "Ctrl+Shift+O")
        act(m, "Preferenze (cartella progetti, cache)…", self.preferences)
        act(m, "Copie di sicurezza dei progetti…", lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(paths.internal_dir("copie_di_sicurezza"))))
        self.recent_menu = m.addMenu("Apri recente")
        self._fill_recent()
        self.act_save = act(m, "Salva", self.save, "Ctrl+S")
        act(m, "Salva con nome…", self.save_as, "Ctrl+Shift+S")
        m.addSeparator()
        self.act_import = act(m, "Importa clip…", self.import_dialog, "Ctrl+I")
        act(m, "Importa cartella…", self.import_folder_dialog)
        act(m, "Nuova cartella nella lista clip", lambda: self.bin_action("new_bin", self.bin.selected_ids()),
            "Ctrl+Shift+N")
        m.addSeparator()
        self.act_export = act(m, "Esporta video…", self.export_dialog, "Ctrl+E")
        m.addSeparator()
        act(m, "Esci", self.close, "Ctrl+Q")

        m = mb.addMenu("Modifica")
        self.act_undo = act(m, "Annulla", self.undo, "Ctrl+Z")
        self.act_redo = act(m, "Ripeti", self.redo, ["Ctrl+Shift+Z", "Ctrl+Y"])
        m.addSeparator()
        act(m, "Taglia alla testina", lambda: self.timeline_action("split", list(self.timeline.view.selection)),
            "C")
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
        act(m, "Duplica (parte nella lista, o pezzi in timeline)", self.duplicate_selected, "Ctrl+D")
        act(m, "Rinomina", self._rename_in_bin, "F2")
        act(m, "Accoda alla timeline", self.append_selected, "E")
        act(m, "Inserisci alla testina", self.insert_at_playhead, "F9")

        m = mb.addMenu("Riproduzione")
        act(m, "Play / pausa", self.engine.toggle, "Space")
        act(m, "Indietro (J)", lambda: self.engine.shuttle(-1), "J")
        act(m, "Stop (K)", lambda: self.engine.shuttle(0), "K")
        act(m, "Avanti (L)", lambda: self.engine.shuttle(1), "L")
        # ← / → sono gestite a parte (tenendole premute lo scorrimento accelera)
        act(m, "Fotogramma precedente\t←", lambda: self.engine.step(-1))
        act(m, "Fotogramma successivo\t→", lambda: self.engine.step(1))
        act(m, "Indietro 1 s", lambda: self.engine.seek(self.engine.position - 1), "Shift+Left")
        act(m, "Avanti 1 s", lambda: self.engine.seek(self.engine.position + 1), "Shift+Right")
        act(m, "Taglio precedente", lambda: self.jump_edit(-1), "Up")
        act(m, "Taglio successivo", lambda: self.jump_edit(1), "Down")
        act(m, "Inizio", lambda: self.engine.seek(0), "Home")
        act(m, "Fine", lambda: self.engine.seek(self.engine.duration()), "End")
        m.addSeparator()

        m = mb.addMenu("Timeline")
        self.act_snap = act(m, "Aggancio", lambda on: self.timeline.btn_snap.setChecked(on), "N",
                            checkable=True, checked=True)
        act_r = act(m, "Ripple", lambda on: self.timeline.btn_ripple.setChecked(on), "Shift+R", checkable=True)
        self.timeline.btn_ripple.toggled.connect(act_r.setChecked)
        act_s = act(m, "Ritaglio dinamico", lambda on: self.timeline.btn_slip.setChecked(on), "Y", checkable=True)
        self.timeline.btn_slip.toggled.connect(act_s.setChecked)
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
        self.act_mon2 = act(m, "Secondo schermo con la clip originale (montaggio)", self.set_monitor2, checkable=True,
                            checked=self.cfg.get("monitor2", True))
        self.mon_box.setVisible(self.cfg.get("monitor2", True))
        self.act_gpu = act(m, "Anteprima con la scheda video (OpenGL)", self.set_gpu, checkable=True,
                           checked=not self.engine.cpu_grade)

        self.act_column = act(m, "Mostra la colonna (clip, effetti, colore)", self.set_column_visible, "Ctrl+L",
                              checkable=True, checked=True)
        act(m, "Progetti", lambda: self.set_page(PAGE_PROJECTS), "Ctrl+Shift+1")
        act(m, "Clip", lambda: self.set_page(PAGE_CLIP), "Ctrl+Shift+2")
        act(m, "Montaggio", lambda: self.set_page(PAGE_MONTAGE), "Ctrl+Shift+3")
        m.addSeparator()
        act(m, "Scheda Clip (montaggio)", lambda: self.show_tab(TAB_CLIP), "Ctrl+3")
        act(m, "Scheda Effetti (velocità, inquadratura, dissolvenze, scritte, riquadri)",
            lambda: self.show_tab(TAB_EFFECTS), "Ctrl+4")
        act(m, "Scheda Colore", lambda: self.show_tab(TAB_COLOR), "Ctrl+5")

        m = mb.addMenu("Aiuto")
        act(m, "Scorciatoie da tastiera", self.show_shortcuts, "F1")
        act(m, "Cartella cache (proxy e audio)", lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(paths.cache_root())))
        m.addSeparator()
        act(m, "Cerca aggiornamenti…", lambda: self.check_updates(manual=True))
        a = act(m, f"Moviola {__version__}", lambda: None)
        a.setEnabled(False)

    def _build_toolbar(self):
        """Barra in alto: solo icone (il nome compare passando sopra col mouse)."""
        tb = QToolBar("Progetto", self)
        tb.setObjectName("toolbar_progetto")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.act_recent_tb = QAction("Progetti", self)
        self.act_recent_tb.triggered.connect(lambda _=False: self.show_welcome())
        groups = [
            [(self.act_new, "new", "Nuovo progetto (Ctrl/Cmd+N)"),
             (self.act_open, "open", "Apri progetto (Ctrl/Cmd+O)"),
             (self.act_save, "save", "Salva progetto (Ctrl/Cmd+S)"),
             (self.act_recent_tb, "recent", "Progetti: elenco, nuovo, duplica, rinomina (Ctrl/Cmd+Maiusc+O)")],
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
        left = QWidget()
        left.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        right = QWidget()
        right.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(left)
        tb.addWidget(self._build_page_bar())
        tb.addWidget(right)
        tb.setStyleSheet("QToolBar { background: #1a1b1d; border: none; spacing: 2px; padding: 3px 6px; }"
                         "QToolButton { padding: 4px; border: none; background: transparent; }"
                         "QToolButton:hover { background: #34363a; border-radius: 4px; }"
                         "QToolBar::separator { background: #3a3c40; width: 1px; margin: 4px 6px; }")
        self.addToolBar(Qt.TopToolBarArea, tb)

    def _update_undo_state(self):
        self.act_undo.setEnabled(self.project.can_undo())
        self.act_redo.setEnabled(self.project.can_redo())

    def show_welcome(self):
        """Scheda PROGETTI (all'avvio e dall'icona «Progetti»)."""
        self.set_page(PAGE_PROJECTS)

    def create_project(self, path):
        """Nuovo progetto già salvato nella cartella scelta."""
        if not self._confirm_discard():
            return
        self._clear_autosave()
        self.project = Project()
        self._reset_state()
        try:
            self.project.save(path)
        except Exception as e:
            QMessageBox.warning(self, "Nuovo progetto", f"Non riesco a salvare il progetto:\n{e}")
            self.project.path = None
        if self.project.path:
            self._remember_recent(path)
        self.refresh_all([])
        self.statusBar().showMessage(f"Nuovo progetto: {os.path.basename(path)}", 4000)
        self._after_project_loaded()

    def _save_cfg(self):
        try:
            with open(paths.config_path(), "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=1)
        except Exception:
            pass

    # ------------------------------------------------------------- cache
    def _project_cache_files(self):
        out = []
        for s in self.project.sources:
            p = s["path"]
            if os.path.exists(p):
                out += [media.audio_cache_path(p), media.peaks_path(p), media.thumb_path(p), media.proxy_path(p),
                        stabilize.stab_path(p)]
        return out

    def _schedule_cache_check(self):
        """Dopo aver creato file in cache controlla il limite (con calma, in background)."""
        if not hasattr(self, "_cache_timer"):
            self._cache_timer = QTimer(self)
            self._cache_timer.setSingleShot(True)
            self._cache_timer.setInterval(8000)
            self._cache_timer.timeout.connect(self._enforce_cache)
        self._cache_timer.start()

    def _enforce_cache(self):
        limit = float(self.cfg.get("cache_limit_gb", DEFAULT_LIMIT_GB)) * 1024 ** 3
        keep = self._project_cache_files()
        self.jobs.submit(paths.enforce_cache_limit, int(limit), keep)

    def preferences(self):
        old_root = paths.cache_root()
        d = PreferencesDialog(self.cfg, self._project_cache_files(), self)
        if not d.exec():
            return
        v = d.values()
        self.cfg["projects_dir"] = v["projects_dir"]
        self.cfg["cache_limit_gb"] = v["cache_limit_gb"]
        self.cfg["auto_update_check"] = v["auto_update_check"]
        self.cfg["github_token"] = v["github_token"]
        moved = (v["cache_dir"] or "") != (self.cfg.get("cache_dir") or "")
        self.cfg["cache_dir"] = v["cache_dir"]
        paths.set_media_cache(v["cache_dir"])
        self._save_cfg()
        if moved:
            if v["clear_old"] and os.path.isdir(old_root) and os.path.abspath(old_root) != \
                    os.path.abspath(paths.cache_root()):
                self.jobs.submit(paths.clear_cache, (), old_root)
            self._reload_media()
            self.statusBar().showMessage("Cache spostata: i file si ricreano nella nuova posizione.", 5000)
        self._enforce_cache()

    # ------------------------------------------------------- aggiornamenti
    def check_updates(self, manual=False):
        """Controlla su GitHub se c'è una versione più nuova (in background)."""
        if update.is_checkout():
            if manual:
                QMessageBox.information(self, "Aggiornamenti",
                                        "Stai usando la cartella di GitHub Desktop: per aggiornare fai Pull lì.\n\n"
                                        "Con l'app installata (vedi LEGGIMI.txt) l'aggiornamento si fa da qui.")
            return
        if getattr(self, "_update_busy", False):
            return
        self._update_busy = True
        self.cfg["last_update_check"] = time.time()
        token = self.cfg.get("github_token") or None
        if manual:
            self.statusBar().showMessage("Cerco aggiornamenti su GitHub…", 4000)
        emit = self.media_event.emit

        def do():
            try:
                newer, v = update.check(token)
                emit("update_checked", "manual" if manual else "", (newer, v, None))
            except Exception as e:
                emit("update_checked", "manual" if manual else "", (False, "", str(e)))

        self.jobs.submit(do)

    def _update_checked(self, manual, newer, version, error):
        self._update_busy = False
        if error:
            if manual:
                QMessageBox.warning(self, "Aggiornamenti", error)
            return
        if not newer:
            if manual:
                QMessageBox.information(self, "Aggiornamenti", f"Moviola è aggiornata (versione {__version__}).")
            return
        if not manual and self.cfg.get("skip_version") == version:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Aggiornamento disponibile")
        box.setText(f"È disponibile Moviola {version} (hai la {__version__}).")
        box.setInformativeText("L'aggiornamento scarica meno di 1 MB e riavvia il programma. "
                               "Progetti, impostazioni e cache non vengono toccati.")
        now = box.addButton("Aggiorna ora", QMessageBox.AcceptRole)
        box.addButton("Più tardi", QMessageBox.RejectRole)
        skip = None if manual else box.addButton("Salta questa versione", QMessageBox.DestructiveRole)
        box.exec()
        if skip is not None and box.clickedButton() is skip:
            self.cfg["skip_version"] = version
            self._save_cfg()
        elif box.clickedButton() is now:
            self._install_update()

    def _install_update(self):
        if not self._confirm_discard():
            return
        self.statusBar().showMessage("Scarico l'aggiornamento…")
        token = self.cfg.get("github_token") or None
        emit = self.media_event.emit

        def do():
            try:
                emit("update_installed", "", (update.update(token), None))
            except Exception as e:
                emit("update_installed", "", ("", str(e)))

        self.jobs.submit(do)

    def _update_installed(self, version, error):
        self.statusBar().clearMessage()
        if error:
            QMessageBox.warning(self, "Aggiornamento non riuscito", error)
            return
        QMessageBox.information(self, "Aggiornamento installato",
                                f"Moviola {version} è installata: ora il programma si riavvia.")
        update.restart()
        self.project.dirty = False  # già chiesto prima di aggiornare
        self.close()

    def _reload_media(self):
        """Ricollega audio, forme d'onda, miniature e proxy alla cache attuale."""
        self.engine.invalidate_source(None)
        self.proxies.clear()
        self.peaks.clear()
        self.store = audio.AudioStore()
        self.engine.store = self.store
        for src in self.project.sources:
            raw = media.audio_cache_path(src["path"])
            if os.path.exists(raw):
                self.store.register(src["id"], raw)
            self.start_source_jobs(src)
        self.timeline.view.update()

    def _cache_notice(self):
        if self.cfg.get("cache_dir") and not paths.media_cache_available():
            self.statusBar().showMessage("Il disco della cache non è collegato: per ora uso una cache temporanea "
                                         "sul computer.", 10000)

    def _connect(self):
        e = self.engine
        e.frame_ready.connect(self._on_frame)
        e.position_changed.connect(self._on_position)
        e.playing_changed.connect(self._playing_changed)
        self.bin.current_changed.connect(self.show_ref)
        self.bin.selection_changed.connect(self._bin_selection)
        self.bin.request.connect(self.bin_action)
        self.bin.hover.connect(self._bin_hover)
        e.peek_ready.connect(self._on_peek)
        self.bin.rebuilt.connect(self._schedule_sub_thumbs)
        self.bin.import_files.connect(lambda paths, target: self.import_paths(paths, target or False))
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
        if self.page != PAGE_CLIP:  # nel montaggio l'elenco serve solo a scegliere e trascinare
            self._update_name_label()
            return
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
        self._update_monitor()

    # ============================================================ lista clip
    def _bin_selection(self, ids):
        self.active_panel = "bin"
        if self.color_panel.target() == "item":
            self.color_panel.set_target("source")
        self.refresh_color_panel()
        self._update_monitor()

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
            self._schedule_sub_thumbs()

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
            self._schedule_sub_thumbs()
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

    def _rename_in_bin(self):
        ids = self.bin.selected_ids()
        if ids:
            return self.bin_action("rename", ids[:1])
        bins = self.bin.tree.selected_bins()
        if bins:
            self.bin_action("rename_bin:" + bins[0], [])

    def _bins_changed(self, select=None, msg=None):
        self.bin.rebuild(self.project, select)
        self._mark_dirty()
        if msg:
            self.statusBar().showMessage(msg, 3000)

    def _bin_folder_action(self, action, ids):
        """Cartelle della scheda Clip. Restituisce True se l'azione era di questo tipo."""
        pr = self.project
        if action in ("new_bin",) or action.startswith("new_bin_in:"):
            parent = action.split(":", 1)[1] if ":" in action else None
            if parent is None and not ids:
                parent = self.bin.target_bin()  # dentro la cartella selezionata
            srcs = [pr.source(pr.source_id_of(i)) for i in ids]
            music = bool(srcs) and all(x is not None and not x["has_video"] for x in srcs)
            name, ok = QInputDialog.getText(self, "Nuova cartella", "Nome della cartella:",
                                            text="Musica" if music else "")
            if not ok:
                return True
            pr.checkpoint()
            b = pr.add_bin(name, parent)
            n = pr.move_to_bin(ids, b["id"]) if ids else 0
            if parent and pr.bin(parent):
                pr.bin(parent)["open"] = True
            self._bins_changed(ids or None, f"Creata la cartella «{b['name']}»" + (f" con {n} clip." if n else "."))
            return True
        if action.startswith("rename_bin:"):
            b = pr.bin(action.split(":", 1)[1])
            if b is None:
                return True
            name, ok = QInputDialog.getText(self, "Rinomina cartella", "Nome della cartella:", text=b["name"])
            if ok and name.strip() and name.strip() != b["name"]:
                pr.checkpoint()
                pr.rename_bin(b["id"], name)
                self._bins_changed()
            return True
        if action.startswith("delete_bin:"):
            b = pr.bin(action.split(":", 1)[1])
            if b is None:
                return True
            pr.checkpoint()
            pr.delete_bin(b["id"])
            self._bins_changed(None, f"Cartella «{b['name']}» eliminata: le clip sono ancora nella lista "
                                     "(Annulla per ripristinarla).")
            return True
        if action.startswith("move_to_bin:"):
            bid = action.split(":", 1)[1] or None
            if not ids:
                return True
            before = {s["id"]: s.get("bin") for s in pr.sources}
            pr.checkpoint()
            pr.move_to_bin(ids, bid)
            if {s["id"]: s.get("bin") for s in pr.sources} == before:
                pr.drop_checkpoint()
                return True
            if bid and pr.bin(bid):
                pr.bin(bid)["open"] = True
            where = f"in «{pr.bin(bid)['name']}»" if bid else "fuori dalle cartelle"
            self._bins_changed(ids, f"Spostate {len(set(pr.source_id_of(i) for i in ids))} clip {where}.")
            return True
        if action.startswith("move_bins:"):
            dest = action.split(":", 1)[1] or None
            pr.checkpoint()
            moved = [b for b in ids if pr.move_bin(b, dest)]
            if not moved:
                pr.drop_checkpoint()
                return True
            if dest and pr.bin(dest):
                pr.bin(dest)["open"] = True
            self._bins_changed()
            return True
        if action.startswith("import_in:"):
            self._import_target = action.split(":", 1)[1]
            self.import_dialog()
            self._import_target = None
            return True
        if action.startswith("import_audio_in:"):
            self._import_target = action.split(":", 1)[1]
            self.import_audio_dialog()
            self._import_target = None
            return True
        return False

    def bin_action(self, action, ids):
        pr = self.project
        if self._bin_folder_action(action, ids):
            return
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
                more = pr.relink_folder(os.path.dirname(p))  # gli altri file mancanti nella stessa cartella
                self._reload_media()
                self.refresh_all([src["id"]])
                if more:
                    self.statusBar().showMessage(f"Ricollegati anche altri {more} file dalla stessa cartella.", 5000)
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
        self._update_monitor()

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
            ids = self.bin.selected_ids()
            if ids:
                self.bin_action("delete", ids)
            else:
                for b in self.bin.tree.selected_bins():
                    self.bin_action("delete_bin:" + b, [])

    def duplicate_selected(self):
        if self.active_panel == "timeline" and self.timeline.view.selection:
            self.timeline_action("duplicate", list(self.timeline.view.selection))
        else:
            self.bin_action("duplicate", self.bin.selected_ids())

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
        elif action == "duplicate":
            if not ids:
                self.statusBar().showMessage("Seleziona in timeline quello che vuoi duplicare.", 4000)
                return
            pr.checkpoint()
            copies = pr.duplicate_items(ids)
            if not copies:
                pr.drop_checkpoint()
                return
            view.set_selection([c["id"] for c in copies])
            first = min(c["start"] for c in copies)
            self.statusBar().showMessage(
                f"Duplicato: {len(copies)} {'pezzo' if len(copies) == 1 else 'pezzi'} a {tc(first, pr.fps)} "
                "(selezionati: trascinali dove servono).", 5000)
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
        self.right_tabs.setVisible(on)
        if self.act_column.isChecked() != on:
            self.act_column.setChecked(on)

    def show_tab(self, index):
        """Mostra il pannello Effetti (0) o Colore (1), anche se era stato chiuso o staccato."""
        if self.page != PAGE_MONTAGE:
            self.set_page(PAGE_MONTAGE)
        self.set_column_visible(True)
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
        self._update_stab_info()
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
            if key.startswith("stab"):
                self._ensure_stab(its)
                self._update_stab_info()
        self._mark_dirty()
        self.timeline.view.update()
        if key in ("speed", "speed_curve", "duration"):
            self.timeline.sync()
            self._update_name_label()
            self.refresh_inspector()
        self.engine.refresh()

    # ------------------------------------------------------ stabilizzazione
    def _ensure_stab(self, items=None):
        """Avvia l'analisi del movimento per le clip stabilizzate che non l'hanno ancora."""
        pr = self.project
        for it in (pr.items if items is None else items):
            if it.get("kind") == "video" and it.get("stab"):
                src = pr.source(it["source"])
                if src and os.path.exists(src["path"]) and not stabilize.has_analysis(src["path"]):
                    self.start_stab(src)

    def start_stab(self, src):
        sid = src["id"]
        if sid in self._busy_stab:
            return
        self._busy_stab[sid] = 0.0
        path = src["path"]
        # il proxy si legge molto più in fretta e ha gli stessi fotogrammi
        video = self.proxies.get(sid)
        if not video or not os.path.exists(video):
            video = path
        emit = self.media_event.emit
        cancel = self.jobs.cancel
        last = [0.0]

        def prog(f):
            if f - last[0] >= 0.02 or f >= 1.0:
                last[0] = f
                emit("stab_progress", sid, f)

        def do():
            try:
                emit("stab_progress", sid, 0.0)
                emit("stab_ready", sid, stabilize.analyze(video, progress=prog, cancel=cancel, src_path=path))
            except Exception as e:
                emit("stab_ready", sid, "")
                emit("error", sid, f"stabilizzazione: {e}")

        self.jobs.submit(do)

    def _update_stab_info(self):
        it, _ = self._primary_item()
        if it is None or it["kind"] != "video":
            return
        src = self.project.source(it["source"])
        if not it.get("stab"):
            self.inspector.set_stab_info("L'immagine viene ingrandita del «ritaglio» per nascondere i bordi. "
                                         "Funziona meglio su tremolio e passi che su scossoni molto forti.")
        elif src is None or not os.path.exists(src["path"]):
            self.inspector.set_stab_info("File non trovato: impossibile analizzare il movimento.")
        elif src["id"] in self._busy_stab:
            self.inspector.set_stab_info(f"Analisi del movimento… {int(self._busy_stab[src['id']] * 100)}%  "
                                         "(puoi continuare a lavorare)", busy=True)
        elif stabilize.has_analysis(src["path"]):
            z = stabilize.margins(stabilize.params(it)[1], 1.0)[0]
            self.inspector.set_stab_info(f"Attiva: immagine ingrandita al {z * 100:.0f}%. "
                                         "Nell'export i pezzi stabilizzati richiedono più tempo.")
        else:
            self.inspector.set_stab_info("In attesa dell'analisi del movimento…", busy=True)

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
        """Durante lo scorrimento mostra il primo fotogramma del pezzo (e, a fianco,
        quello della clip originale da cui parte ora)."""
        it = self.project.item(iid)
        if it is None:
            return
        if self.engine.mode != "timeline":
            self.set_viewer_mode("timeline")
        self.engine.seek(it["start"])
        self._edge_preview(it, "left", "Il pezzo ora parte da")

    def trim_preview(self, iid, edge):
        """Allungando o accorciando un pezzo: nello schermo sorgente il fotogramma
        del bordo che si sta trascinando; nel visore della timeline, quello nel montaggio."""
        pr = self.project
        it = pr.item(iid)
        if it is None:
            return
        if self.engine.mode != "timeline":
            self.set_viewer_mode("timeline")
        vit = it
        if is_overlay(it) or not (pr.source(it.get("source", "")) or {}).get("has_video"):
            vit = next((g for g in pr.linked(it) if not is_overlay(g)
                        and (pr.source(g.get("source", "")) or {}).get("has_video")), None)
        if vit is None:
            return
        self.engine.seek(vit["start"] if edge == "left" else pr.item_end(vit) - pr.frame())
        self._edge_preview(vit, edge, "Inizio del pezzo" if edge == "left" else "Fine del pezzo")

    def _edge_preview(self, it, edge, text):
        pr = self.project
        src = pr.source(it.get("source", ""))
        if src is None or not src.get("has_video") or self.page != PAGE_MONTAGE:
            return
        fps = src.get("fps") or 25
        if it.get("freeze") is not None:
            t = float(it["freeze"])
        elif edge == "left":
            t = float(it["src_in"])
        else:
            t = max(float(it["src_in"]), float(it["src_out"]) - 1.0 / max(fps, 1))
        self.engine.peek(src["id"], t, it["id"])
        a, b = float(it.get("src_in", t)), float(it.get("src_out", t))
        self.mon_info.setText(
            f"<b style='color:#e8a33d'>{text}</b>  <span style='color:#cfcfcf'>{tc(t, fps)}</span> del file"
            f"<br>{self._specs(src)}  ·  usa {tc(a, fps)} → {tc(b, fps)}")

    # ------------------------------------------------------ secondo schermo
    @staticmethod
    def _specs(src):
        """«1920×1080 · 29.97 fps · hevc» della clip originale."""
        if not src.get("has_video"):
            return "solo audio"
        fps = f"{float(src.get('fps') or 0):.2f}".rstrip("0").rstrip(".")
        out = [f"{src.get('width')}×{src.get('height')}", f"{fps} fps"]
        if src.get("codec"):
            out.append(str(src["codec"]).upper())
        return " · ".join(out)

    def _on_peek(self, f):
        if f.img is not None:
            h, w = f.img.shape[:2]
            self.mon_viewer.aspect = w / max(h, 1)
        self.mon_viewer.set_frame(f)

    def set_monitor2(self, on):
        self.mon_box.setVisible(on)
        self.cfg["monitor2"] = bool(on)
        if self.act_mon2.isChecked() != on:
            self.act_mon2.setChecked(on)
        if on:
            self._update_monitor()

    def _mon_caption(self, ref, src, a, b, t=None):
        pr = self.project
        fps = src.get("fps") or pr.fps
        name = f"<b style='color:#e8a33d'>{ref}</b>  <span style='color:#cfcfcf'>{src.get('label') or src['name']}</span>"
        rng = f"  ·  {tc(a, fps)} → {tc(b, fps)}" if (a > 1e-6 or abs(b - src['duration']) > 1e-6) else ""
        at = f"<br>{self._specs(src)}{rng}"
        if t is not None:
            at += f"  ·  <span style='color:#cfcfcf'>{tc(t, fps)}</span>"
        self.mon_info.setText(name + at)

    def _update_monitor(self):
        """Schermo sorgente: la clip scelta nell'elenco o in timeline, con le sue
        caratteristiche (dimensione, fps, codec)."""
        if self.page != PAGE_MONTAGE or not self.mon_box.isVisible():
            return
        pr = self.project
        pw, ph = pr.size
        self.tl_info.setText(f"<b style='color:#e8a33d'>TIMELINE</b><br>{pw}×{ph} · {pr.fps:g} fps")
        sel = [pr.item(i) for i in self.timeline.view.selection]
        sel = [i for i in sel if i is not None]
        if self.active_panel == "timeline" and sel:
            vids = sorted((i for i in sel if not is_overlay(i) and pr.source(i.get("source", ""))),
                          key=lambda i: (i["start"], i["track"]))
            if not vids:
                kinds = {"text": "Scritta", "box": "Riquadro"}
                self.mon_info.setText(f"<b style='color:#e8a33d'>{kinds.get(sel[0].get('kind'), 'Elemento')}</b>"
                                      "<br>senza clip originale")
                self.engine.peek("", 0.0)
                return
            it = vids[0]
            src = pr.source(it["source"])
            t0 = self.engine.position
            if it.get("freeze") is not None:
                st = float(it["freeze"])
            elif it["start"] <= t0 < pr.item_end(it):
                st = float(pr.src_time(it, t0))
            else:
                st = float(it["src_in"])
            self.engine.peek(src["id"], st, it["id"] if src.get("has_video") else None)
            fps = src.get("fps") or pr.fps
            sp = pr.speed(it)
            more = f"  ·  +{len(vids) - 1} selezionati" if len(vids) > 1 else ""
            spd_txt = f"  ·  velocità {sp:g}×" if abs(sp - 1.0) > 1e-6 else ""
            ref = it.get("origin") or src["id"]
            self.mon_info.setText(
                f"<b style='color:#e8a33d'>{ref}</b>  <span style='color:#cfcfcf'>{src.get('label') or src['name']}</span>"
                f"{more}<br>{self._specs(src)}  ·  usa {tc(it['src_in'], fps)} → {tc(it['src_out'], fps)}{spd_txt}")
            return
        ids = self.bin.selected_ids()
        ref = ids[0] if ids else self.selected_ref
        src, a, b = pr.range_of(ref or "")
        if src is None:
            self.mon_info.setText("<span style='color:#8d8d8d'>SORGENTE<br>scegli una clip</span>")
            self.engine.peek("", 0.0)
            return
        self.engine.peek(src["id"], a)
        self._mon_caption(ref, src, a, b)

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

    def import_paths(self, items, target=None):
        """target: cartella della lista clip dove mettere i file (None = quella
        selezionata, se c'è)."""
        if target is None:
            target = getattr(self, "_import_target", None) or self.bin.target_bin()
        elif target is False:
            target = None  # lasciati fuori dalle cartelle
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
            self._import_bins[f] = target
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
                bid = self._import_bins.get(f)
                if bid and pr.bin(bid):
                    src["bin"] = bid
                    pr.bin(bid)["open"] = True
                added.append(src)
            self._pending_imports = []
            self._import_results = {}
            self._import_bins = {}
            if not added:
                pr.drop_checkpoint()
            for src in added:
                self.start_source_jobs(src)
            self.refresh_all([added[0]["id"]] if added else None)
            if added and self.engine.mode != "source":
                self.show_ref(added[0]["id"])
            msg = f"Importate {len(added)} clip."
            dest = {src.get("bin") for src in added} - {None}
            if len(dest) == 1 and pr.bin(next(iter(dest))):
                msg = f"Importate {len(added)} clip in «{pr.bin(next(iter(dest)))['name']}»."
            if errors:
                QMessageBox.warning(self, "Alcuni file non sono stati importati", "\n".join(errors[:15]))
            self.statusBar().showMessage(msg, 5000)
        elif kind == "audio_ready":
            self.store.register(key, payload)
            self._set_status(key)
            self._schedule_cache_check()
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
            self._schedule_cache_check()
            if payload:
                self.proxies[key] = payload
                src = pr.source(key)
                if src:
                    self.engine.invalidate_source(src["path"])
            self._set_status(key)
        elif kind == "update_checked":
            self._update_checked(key == "manual", *payload)
        elif kind == "update_installed":
            self._update_installed(*payload)
        elif kind == "stab_progress":
            if key in self._busy_stab:
                self._busy_stab[key] = float(payload)
                self._update_stab_info()
        elif kind == "stab_ready":
            self._busy_stab.pop(key, None)
            self._schedule_cache_check()
            self._update_stab_info()
            self.engine.refresh()
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

    def _schedule_sub_thumbs(self):
        """Ogni parte ha la sua miniatura (il fotogramma d'ingresso). Si aspetta un
        attimo, così trascinando i segni I/O non parte un lavoro per ogni pixel."""
        if not hasattr(self, "_sub_thumb_timer"):
            self._sub_thumb_timer = QTimer(self)
            self._sub_thumb_timer.setSingleShot(True)
            self._sub_thumb_timer.setInterval(350)
            self._sub_thumb_timer.timeout.connect(self._make_sub_thumbs)
        self._sub_thumb_timer.start()

    def _make_sub_thumbs(self):
        emit = self.media_event.emit
        for src in self.project.sources:
            path = src["path"]
            if not src.get("has_video") or not os.path.exists(path):
                continue
            for sub in self.project.subclips_of(src["id"]):
                at = round(float(sub["in"]), 3)
                if self._sub_thumb_at.get(sub["id"]) == at:
                    continue
                self._sub_thumb_at[sub["id"]] = at

                def do(sid=sub["id"], path=path, at=at):
                    try:
                        emit("thumb", sid, media.make_thumb(path, at, part=True))
                    except Exception:
                        pass

                self.jobs.submit(do)

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
            # clip stabilizzate senza analisi (cache svuotata, altro computer): si rifà
            self._ensure_stab([i for i in self.project.items if i.get("source") == sid])

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
        self._sub_thumb_at.clear()
        self.bin._icons.clear()
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
        self.set_page(PAGE_CLIP)

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
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("File non trovati")
            box.setText(f"{len(missing)} file originali non sono stati trovati (il disco esterno è collegato?).")
            box.setDetailedText("\n".join(f"{s['id']}: {s['path']}" for s in missing))
            search = box.addButton("Cerca in una cartella…", QMessageBox.AcceptRole)
            box.addButton("Più tardi", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is search:
                d = QFileDialog.getExistingDirectory(self, "Cartella dove cercare i video")
                if d:
                    pr.checkpoint()
                    n = pr.relink_folder(d)
                    self._reload_media()
                    self.refresh_all([])
                    self.statusBar().showMessage(f"Ricollegati {n} file su {len(missing)}.", 6000)
        self._after_project_loaded()

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
        return os.path.join(paths.internal_dir("autosalvataggi"), f"{base}_{key}.moviola")

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
            d = paths.internal_dir("copie_di_sicurezza")
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
        self._cache_notice()
        self._enforce_cache()
        self.show_welcome()
        if update.due(self.cfg, time.time()):
            QTimer.singleShot(3000, self.check_updates)

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
        # cartella proposta: l'ultima usata, altrimenti quella del progetto, altrimenti Filmati
        start = self.cfg.get("last_export_dir")
        if not start or not os.path.isdir(start) or os.path.abspath(start) == os.path.expanduser("~"):
            start = os.path.dirname(os.path.abspath(self.project.path)) if self.project.path else ""
        if not start or not os.path.isdir(start):
            movies = os.path.join(os.path.expanduser("~"), "Movies" if sys.platform == "darwin" else "Videos")
            start = movies if os.path.isdir(movies) else os.path.expanduser("~")
        d = ExportDialog(self.project, self.store, start, self,
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
