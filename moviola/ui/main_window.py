"""Finestra principale: collega lista clip, visore, timeline e colore."""
from __future__ import annotations

import copy
import json
import os
import time

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence, QPixmap
from PySide6.QtWidgets import (QDockWidget, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
                               QMainWindow, QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget)

from .. import audio, color, media, paths
from ..playback import Engine
from ..project import Project
from .bin_panel import BinPanel
from .color_panel import ColorPanel
from .export_dialog import ExportDialog
from .source_bar import SourceBar
from .timeline import TimelinePanel, tc
from .viewer import CpuViewer, GlViewer

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
N: aggancio on/off · + / −: zoom · Maiusc+Z: adatta · Alt+trascina: muovi solo video o solo audio<br>
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

        self.engine = Engine(lambda: self.project, self.store, self.path_for)
        self.engine.quality = cfg.get("quality", "meta")

        self.setWindowTitle("Moviola")
        self.resize(1440, 900)
        self._build_ui()
        self._build_actions()
        self._connect()
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
        self.btn_mode = QPushButton("Mostra timeline")
        self.btn_mode.setFocusPolicy(Qt.NoFocus)
        head.addWidget(self.mode_label)
        head.addSpacing(10)
        head.addWidget(self.name_label, 1)
        head.addWidget(self.btn_mode)
        tl.addLayout(head)
        self.viewer_holder = QVBoxLayout()
        self.viewer_holder.setContentsMargins(0, 0, 0, 0)
        tl.addLayout(self.viewer_holder, 1)
        self.viewer = None
        self._make_viewer(self.cfg.get("gpu", True))
        self.source_bar = SourceBar()
        tl.addWidget(self.source_bar)
        tr = QHBoxLayout()
        tr.setSpacing(4)

        def tb(text, tip, slot):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(slot)
            tr.addWidget(b)
            return b

        tb("⏮", "Inizio (Home)", lambda: self.engine.seek(0))
        tb("◀◀", "Indietro (J)", lambda: self.engine.shuttle(-1))
        self.play_btn = tb("▶", "Play / pausa (Spazio)", self.engine.toggle)
        self.play_btn.setFixedWidth(46)
        tb("▶▶", "Avanti veloce (L)", lambda: self.engine.shuttle(1))
        tb("⏭", "Fine (Fine)", lambda: self.engine.seek(self.engine.duration()))
        tr.addSpacing(10)
        self.tc_label = QLabel("00:00:00:00")
        self.tc_label.setObjectName("timecode")
        tr.addWidget(self.tc_label)
        self.dur_label = QLabel("")
        self.dur_label.setStyleSheet("color:#888;")
        tr.addWidget(self.dur_label)
        tr.addStretch(1)
        self.src_buttons = [
            tb("I", "Ingresso (I)", self.mark_in),
            tb("O", "Uscita (O)", self.mark_out),
            tb("+ Crea parte", "Crea una parte dai segni I/O (Invio o M)", self.create_subclip),
            tb("Accoda ⤵", "Accoda alla timeline (E)", self.append_selected),
            tb("Alla testina", "Inserisci alla testina della timeline (F9)", self.insert_at_playhead),
        ]
        tl.addLayout(tr)

        self.timeline = TimelinePanel(self)
        split = QSplitter(Qt.Vertical)
        split.addWidget(top)
        split.addWidget(self.timeline)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([520, 330])
        self.setCentralWidget(split)

        self.bin = BinPanel()
        d = QDockWidget("Clip", self)
        d.setObjectName("dock_clip")
        d.setWidget(self.bin)
        d.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.LeftDockWidgetArea, d)
        self.color_panel = ColorPanel()
        d2 = QDockWidget("Colore", self)
        d2.setObjectName("dock_colore")
        d2.setWidget(self.color_panel)
        d2.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.RightDockWidgetArea, d2)
        self.resizeDocks([d, d2], [330, 330], Qt.Horizontal)
        self.statusBar().showMessage("Pronto. Importa le clip con Ctrl/Cmd+I o trascinandole nella lista.")

    def _make_viewer(self, gpu: bool):
        old = self.viewer
        v = None
        if gpu and GlViewer is not None:
            try:
                import OpenGL.GL  # noqa: F401

                v = GlViewer()
                v.failed.connect(self._gl_failed)
                self.engine.cpu_grade = False
            except Exception:
                v = None
        if v is None:
            v = CpuViewer()
            self.engine.cpu_grade = True
        if old is not None:
            self.viewer_holder.removeWidget(old)
            old.deleteLater()
        self.viewer_holder.addWidget(v)
        self.viewer = v
        self.engine._lut_cache.clear()

    def _gl_failed(self, msg):
        QTimer.singleShot(0, lambda: self._switch_cpu(msg))

    def _switch_cpu(self, msg):
        if isinstance(self.viewer, CpuViewer):
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
        act(m, "Nuovo progetto", self.new_project, "Ctrl+N")
        act(m, "Apri progetto…", self.open_project, "Ctrl+O")
        self.recent_menu = m.addMenu("Apri recente")
        self._fill_recent()
        act(m, "Salva", self.save, "Ctrl+S")
        act(m, "Salva con nome…", self.save_as, "Ctrl+Shift+S")
        m.addSeparator()
        act(m, "Importa clip…", self.import_dialog, "Ctrl+I")
        act(m, "Importa cartella…", self.import_folder_dialog)
        m.addSeparator()
        act(m, "Esporta video…", self.export_dialog, "Ctrl+E")
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
        act(m, "Crea parte dai segni", self.create_subclip, ["Return", "Enter", "M"])
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
        act(m, "Mostra timeline nel visore", lambda: self.set_viewer_mode("timeline"), "Ctrl+2")
        act(m, "Mostra clip nel visore", self.show_current_source, "Ctrl+1")

        m = mb.addMenu("Timeline")
        self.act_snap = act(m, "Aggancio", lambda on: self.timeline.btn_snap.setChecked(on), "N",
                            checkable=True, checked=True)
        act(m, "Zoom avanti", lambda: self.timeline.view.zoom(1.4), ["+", "="])
        act(m, "Zoom indietro", lambda: self.timeline.view.zoom(1 / 1.4), "-")
        act(m, "Adatta alla finestra", self.timeline.view.zoom_fit, "Shift+Z")
        m.addSeparator()
        act(m, "Aggiungi traccia video", lambda: self.timeline_action("add_video_track", []))
        act(m, "Aggiungi traccia audio", lambda: self.timeline_action("add_audio_track", []))
        act(m, "Scollega audio e video", lambda: self.timeline_action("unlink", list(self.timeline.view.selection)))
        act(m, "Collega audio e video", lambda: self.timeline_action("link", list(self.timeline.view.selection)))
        act(m, "Impostazioni timeline…", self.timeline_settings)

        m = mb.addMenu("Colore")
        act(m, "Copia regolazioni", self.copy_grade, "Ctrl+Shift+C")
        act(m, "Incolla regolazioni", self.paste_grade, "Ctrl+Shift+V")
        act(m, "Azzera regolazioni", self.reset_grade)
        self.act_bypass = act(m, "Prima / dopo", self.set_bypass, "\\", checkable=True)
        m.addSeparator()
        act(m, "Salva preset…", self.save_preset)
        self.preset_menu = m.addMenu("Applica preset")
        self.preset_menu.aboutToShow.connect(self._fill_presets)

        m = mb.addMenu("Vista")
        qm = m.addMenu("Qualità anteprima")
        grp = QActionGroup(self)
        for k, lab in (("piena", "Piena (fino a 1080p)"), ("meta", "Metà (consigliata)"), ("quarto", "Leggera")):
            a = QAction(lab, self, checkable=True)
            a.setChecked(self.engine.quality == k)
            a.triggered.connect(lambda _=False, kk=k: self.set_quality(kk))
            grp.addAction(a)
            qm.addAction(a)
        self.act_proxy = act(m, "Usa i proxy per l'anteprima", self.set_use_proxy, checkable=True,
                             checked=self.project.data.get("use_proxies", True))
        self.act_autoproxy = act(m, "Crea proxy automaticamente per i file pesanti", self.set_autoproxy,
                                 checkable=True, checked=self.cfg.get("auto_proxy", True))
        act(m, "Crea proxy per tutte le clip", lambda: self.bin_action("proxy", [s["id"] for s in
                                                                                self.project.sources]))
        m.addSeparator()
        self.act_gpu = act(m, "Anteprima con la scheda video (OpenGL)", self.set_gpu, checkable=True,
                           checked=not self.engine.cpu_grade)

        m = mb.addMenu("Aiuto")
        act(m, "Scorciatoie da tastiera", self.show_shortcuts, "F1")
        act(m, "Cartella cache (proxy e audio)", lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(paths.cache_root())))

    def _connect(self):
        e = self.engine
        e.frame_ready.connect(self._on_frame)
        e.position_changed.connect(self._on_position)
        e.playing_changed.connect(lambda p: self.play_btn.setText("❚❚" if p else "▶"))
        self.bin.current_changed.connect(self.show_ref)
        self.bin.selection_changed.connect(self._bin_selection)
        self.bin.request.connect(self.bin_action)
        self.bin.import_files.connect(self.import_paths)
        self.btn_mode.clicked.connect(self._toggle_mode)
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
        cp.edit.connect(self._grade_edit)
        cp.target_changed.connect(lambda k: self.refresh_color_panel())
        cp.action.connect(self._color_action)
        self.media_event.connect(self._on_media_event)

    # ======================================================= stato e vista
    def path_for(self, src):
        if self.project.data.get("use_proxies", True):
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
        self._update_title()
        self._apply_aspect()
        self.engine.refresh()

    def _update_title(self):
        name = os.path.basename(self.project.path) if self.project.path else "Senza titolo"
        self.setWindowTitle(f"{'• ' if self.project.dirty else ''}{name} — Moviola")

    def _mark_dirty(self):
        self.project.dirty = True
        self._update_title()

    def _apply_aspect(self):
        if self.engine.mode == "source":
            src = self.project.source(self.engine.source_id)
            if src and src.get("height"):
                self.viewer.aspect = src["width"] / src["height"]
        else:
            w, h = self.project.size
            self.viewer.aspect = w / max(h, 1)
        self.viewer.update()

    def set_viewer_mode(self, mode, source_id=None, force=False):
        if not force and mode == self.engine.mode and source_id == self.engine.source_id:
            return
        self.engine.remember_timeline_pos()
        self.engine.set_mode(mode, source_id)
        is_src = mode == "source"
        self.mode_label.setText("SORGENTE" if is_src else "TIMELINE")
        self.btn_mode.setText("Mostra timeline" if is_src else "Mostra clip")
        self.source_bar.setVisible(is_src)
        for b in self.src_buttons[:3]:
            b.setEnabled(is_src)
        self._apply_aspect()
        self._update_name_label()
        self._update_source_bar()

    def _toggle_mode(self):
        if self.engine.mode == "source":
            self.set_viewer_mode("timeline")
        else:
            self.show_current_source()

    def show_current_source(self):
        if self.selected_ref:
            self.show_ref(self.selected_ref)
        elif self.project.sources:
            self.show_ref(self.project.sources[0]["id"])

    def _update_name_label(self):
        if self.engine.mode == "source":
            src, a, b = self.project.range_of(self.selected_ref or "")
            if src:
                txt = f"{self.selected_ref}   ·   {src['name']}"
                self.name_label.setText(txt)
                self.dur_label.setText(f"/ {tc(src['duration'], src['fps'])}")
                return
        self.name_label.setText(self.project.data.get("name", "") if self.engine.mode != "source" else "")
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
        if self.engine.mode != "source":
            return
        src = self.project.source(self.engine.source_id)
        if src is None:
            self.source_bar.set_data(0, 25, [], None)
            return
        subs = [(c["id"], c["in"], c["out"], c["n"]) for c in self.project.subclips_of(src["id"])]
        sel = self.selected_ref if self.project.subclip(self.selected_ref or "") else None
        mk = self.marks.get(src["id"], [None, None])
        self.source_bar.set_data(src["duration"], src["fps"], subs, sel,
                                 mk[0] if sel is None else None, mk[1] if sel is None else None)
        self.source_bar.set_position(self.engine.position)

    def _on_frame(self, f):
        if f.img is None:
            self.viewer.clear()
        else:
            self.viewer.set_frame(f)
        if f.hist is not None:
            self.color_panel.curve.set_hist(f.hist)

    def _on_position(self, t):
        fps = self.engine.fps()
        self.tc_label.setText(tc(t, fps))
        if self.engine.mode == "source":
            self.source_bar.set_position(t)
        else:
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
        self.add_refs_at(ids, t, 0, 0)

    def add_refs_at(self, ids, t, vtrack, atrack):
        self.project.checkpoint()
        placed = []
        for rid in ids:
            its = self.project.add_to_timeline(rid, t, vtrack, atrack)
            if its:
                t = max(self.project.item_end(i) for i in its)
            placed += its
        if not placed:
            self.project.drop_checkpoint()
            return
        self.timeline.view.set_selection([i["id"] for i in placed], emit=False)
        self._after_timeline_edit()

    def _after_timeline_edit(self):
        self.timeline.view.update()
        self.timeline.sync()
        self._mark_dirty()
        self._update_name_label()
        self.refresh_color_panel()
        self.engine.refresh()

    def bin_action(self, action, ids):
        pr = self.project
        if action == "import":
            return self.import_dialog()
        if action == "import_folder":
            return self.import_folder_dialog()
        if action == "append":
            self.bin.select(ids)
            return self.append_selected()
        if action == "insert_playhead":
            self.bin.select(ids)
            return self.insert_at_playhead()
        if not ids:
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

    def _src_target_sticky(self):
        return False

    def _timeline_changed(self):
        self.engine.refresh()
        self.timeline.sync()

    def select_all(self):
        if self.active_panel == "timeline" or self.engine.mode == "timeline":
            self.timeline.view.set_selection([i["id"] for i in self.project.items])

    def delete_selected(self):
        if self.active_panel == "timeline":
            self.timeline_action("delete", list(self.timeline.view.selection))
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
        presets = ["1920×1080 · 25", "1920×1080 · 30", "1920×1080 · 50", "3840×2160 · 25", "1280×720 · 25",
                   "1080×1920 · 30 (verticale)", "1080×1080 · 30 (quadrato)"]
        cur = f"{st['width']}×{st['height']} · {st['fps']:g}"
        if cur not in presets:
            presets.insert(0, cur)
        val, ok = QInputDialog.getItem(self, "Impostazioni timeline", "Risoluzione e fotogrammi al secondo:",
                                       presets, presets.index(cur) if cur in presets else 0, True)
        if not ok:
            return
        try:
            size, fps = val.split("·")
            w, h = size.strip().split("×")
            fps = float(fps.split()[0].replace(",", "."))
            self.project.checkpoint()
            st.update(width=int(w), height=int(h), fps=fps, auto=False)
        except Exception:
            QMessageBox.warning(self, "Formato non valido", "Scrivi ad esempio: 1920×1080 · 25")
            return
        self._apply_aspect()
        self._after_timeline_edit()

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

    def refresh_color_panel(self):
        grades, info = self.color_targets()
        self.color_panel.set_editable(bool(grades))
        self.color_panel.show_grade(grades[0] if grades else None, info)

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
        self.project = Project()
        self._reset_state()
        self.refresh_all([])

    def open_project(self, path=None):
        if not self._confirm_discard():
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Apri progetto", self.cfg.get("last_project_dir", ""),
                                                  "Progetto Moviola (*.moviola)")
        if not path:
            return
        try:
            pr = Project.load(path)
        except Exception as e:
            QMessageBox.warning(self, "Impossibile aprire", str(e))
            return
        self.project = pr
        self._reset_state()
        for src in pr.sources:
            raw = media.audio_cache_path(src["path"])
            if os.path.exists(raw):
                self.store.register(src["id"], raw)
            self.start_source_jobs(src)
        self.act_proxy.setChecked(pr.data.get("use_proxies", True))
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
            self.project.save()
        except Exception as e:
            QMessageBox.warning(self, "Salvataggio non riuscito", str(e))
            return False
        self._remember_recent(self.project.path)
        self._update_title()
        self.statusBar().showMessage("Progetto salvato.", 3000)
        return True

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
        self.cfg["recent"] = [path] + rec[:7]
        self._fill_recent()

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
        d = ExportDialog(self.project, self.store, self.cfg.get("last_export_dir", os.path.expanduser("~")), self)
        if d.exec():
            self.cfg["last_export_dir"] = os.path.dirname(d.path.text())

    # ============================================================== opzioni
    def set_quality(self, q):
        self.engine.quality = q
        self.cfg["quality"] = q
        self.engine.refresh()

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
        self.engine.shutdown()
        self.jobs.shutdown()
        self.cfg["geometry"] = bytes(self.saveGeometry().toHex()).decode()
        self.cfg["state"] = bytes(self.saveState().toHex()).decode()
        e.accept()
