"""Costruzione dell'interfaccia: schede, pannelli, azioni e barra strumenti."""
from __future__ import annotations


from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (QComboBox, QSizePolicy, QStackedWidget, QTabBar, QTabWidget, QToolBar, QHBoxLayout, QLabel,
                               QPushButton, QSplitter, QVBoxLayout, QWidget)

from .. import __version__, paths
from .bin_panel import BinPanel
from .color_panel import ColorPanel
from . import icons
from .inspector import Inspector
from .meters import DB_MIN, LevelMeter
from .welcome import ProjectsPanel
from .source_bar import SourceBar
from .timeline import TimelinePanel
from .viewer import CpuViewer, GlViewer

from .export_page import ExportPage
from .common import GREEN_TAG, PAGE_CLIP, PAGE_EXPORT, PAGE_MONTAGE, PAGE_PROJECTS, RED_TAG, TAB_CLIP, TAB_COLOR, TAB_EFFECTS, TAB_QSS, YELLOW_TAG


class BuildMixin:
    # ================================================================== UI
    def _build_ui(self):
        # visore
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(6, 6, 6, 4)
        tl.setSpacing(4)
        head = QHBoxLayout()
        self.name_label = QLabel("")
        self.name_label.setStyleSheet("color:#bbb;")
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
        def title(text):
            t = QLabel(text)
            t.setStyleSheet("color:#8d8d8d; font-weight:700; letter-spacing:2px; padding:0 0 2px 2px;")
            return t

        self.mon_box = QWidget()
        mv = QVBoxLayout(self.mon_box)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(2)
        mv.addWidget(title("CLIP"))
        mv.addLayout(self.mon_holder, 1)
        self.mon_info = caption()
        mv.addWidget(self.mon_info)
        vrow.addWidget(self.mon_box, 1)
        tv = QVBoxLayout()
        tv.setSpacing(2)
        tv.addWidget(title("TIMELINE"))
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
        self.guides_btn = self._build_guides_button()
        tr.addWidget(self.guides_btn)
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
        self.mont_split.setSizes([400, 1040])

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
        self.export_page = ExportPage(self)
        self.pages.addWidget(self.export_page)
        self.setCentralWidget(self.pages)
        self.page = None
        self.statusBar().showMessage("Pronto.")

    # ------------------------------------------------------------ schede
    def _build_page_bar(self):
        """Le quattro schede principali, al centro della barra in alto."""
        self.page_bar = QTabBar()
        self.page_bar.setDrawBase(False)
        self.page_bar.setExpanding(False)
        for lab in ("ARCHIVIO PROGETTI", "SELEZIONE CLIP", "MONTAGGIO", "ESPORTAZIONE"):
            self.page_bar.addTab(lab)
        self.page_bar.setStyleSheet(
            "QTabBar::tab { padding: 7px 20px; font-weight: 700; letter-spacing: 1px; color: #8d8d8d;"
            " background: transparent; border: none; border-bottom: 2px solid transparent; }"
            "QTabBar::tab:selected { color: #f2f2f2; border-bottom: 2px solid #e8a33d; }"
            "QTabBar::tab:hover:!selected { color: #d0d0d0; }")
        self.page_bar.setToolTip("Archivio progetti (Ctrl/Cmd+Maiusc+1) · Selezione clip (Ctrl/Cmd+Maiusc+2) · "
                                 "Montaggio (Ctrl/Cmd+Maiusc+3) · Esportazione (Ctrl/Cmd+Maiusc+4)")
        self.page_bar.currentChanged.connect(self.set_page)
        return self.page_bar

    def set_page(self, index):
        """Passa a una delle schede (0 archivio progetti, 1 selezione clip, 2 montaggio, 3 esportazione)."""
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
        elif index == PAGE_EXPORT:
            self.export_page.refresh()
        else:
            self.set_viewer_mode("timeline")
            self._update_monitor()

    def playhead_time(self):
        return float(self._timeline_pos())

    def selected_timeline_items(self):
        ids = set(self.timeline.view.selection)
        return [i for i in self.project.items if i["id"] in ids]

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
        self.src_info = QLabel("")
        self.src_info.setStyleSheet("color:#8d8d8d; font-size: 11px;")
        self.src_info.setMinimumWidth(10)
        names = QVBoxLayout()
        names.setSpacing(1)
        names.addWidget(self.src_name)
        names.addWidget(self.src_info)
        head.addLayout(names, 1)
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
        v.setMinimumSize(120, 80)
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
        self._apply_guides()
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
        self.act_export = act(m, "Esportazione…", self.export_dialog, "Ctrl+E")
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
        act(m, "Inserisci alla testina (spinge avanti il resto)", self.insert_at_playhead, "F9")
        act(m, "Sovrascrivi alla testina", self.overwrite_at_playhead, "F10")

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
        act(sm, "Aggiungi indicatore alla testina (sorgente: crea parte)", self.marker_or_part, "M")
        act(m, "Estrai il tratto tra I e O (chiude il buco)", self.extract_range, "Shift+X")
        act(m, "Rimuovi il tratto tra I e O (lascia il buco)", lambda: self.extract_range(False), "Alt+Shift+X")
        act(m, "Togli i segni I/O dalla timeline", lambda: (self.project.checkpoint(), self.project.clear_tl_range(),
                                                          self._mark_dirty(), self.timeline.view.update()), "Alt+X")
        act(sm, "Capitolo con nome…", self.add_named_marker, "Alt+M")
        act(sm, "Segnaposto precedente", lambda: self.jump_marker(-1), "Shift+Up")
        act(sm, "Segnaposto successivo", lambda: self.jump_marker(1), "Shift+Down")
        act(sm, "Elenco dei segnaposti…", self.show_markers, "Alt+L")
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
        act(m, "Archivio progetti", lambda: self.set_page(PAGE_PROJECTS), "Ctrl+Shift+1")
        act(m, "Selezione clip", lambda: self.set_page(PAGE_CLIP), "Ctrl+Shift+2")
        act(m, "Montaggio", lambda: self.set_page(PAGE_MONTAGE), "Ctrl+Shift+3")
        act(m, "Esportazione", lambda: self.set_page(PAGE_EXPORT), "Ctrl+Shift+4")
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
             (self.act_export, "export", "Scheda Esportazione (Ctrl/Cmd+E)")],
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
