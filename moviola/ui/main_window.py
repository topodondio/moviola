"""Finestra principale: collega lista clip, visore, timeline e colore."""
from __future__ import annotations

import os

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QMainWindow, QMessageBox)

from .. import audio, media, paths
from ..playback import Engine
from ..project import Project
from .scrub import ArrowScrubber
from .text_render import default_family
from .timeline import tc


from .common import PAGE_CLIP, PAGE_COLOR, PAGE_MONTAGE, PAGE_PROJECTS, TAB_EFFECTS  # noqa: F401  (ri-esportate per i test)
from .win_build import BuildMixin
from .win_system import SystemMixin
from .win_bin import BinMixin
from .win_timeline import TimelineMixin
from .win_inspect import InspectMixin
from .win_monitor import MonitorMixin
from .win_color import ColorMixin
from .win_media import MediaMixin
from .win_project import ProjectMixin
from .win_links import LinksMixin
from .win_refs import RefsMixin


class MainWindow(QMainWindow, BuildMixin, SystemMixin, BinMixin, TimelineMixin, InspectMixin, MonitorMixin, ColorMixin, RefsMixin, LinksMixin, MediaMixin, ProjectMixin):
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


    def _connect(self):
        e = self.engine
        e.frame_ready.connect(self._on_frame)
        e.position_changed.connect(self._on_position)
        e.playing_changed.connect(self._playing_changed)
        self.bin.current_changed.connect(self.show_ref)
        self.bin.selection_changed.connect(self._bin_selection)
        self.bin.request.connect(self.bin_action)
        self.bin._uses_cb = self.uses_menu_entries
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
        ins.rot_anim.connect(self._rot_anim_edit)
        ins.set_stretch(bool(self.cfg.get("speed_stretch", False)))
        ins.stretch_toggled.connect(self._stretch_toggled)
        ins.rot_seek.connect(self._rot_seek)
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
        self.color_page.fill_refs()
        self._apply_compare()
        self.refresh_inspector()
        self._update_title()
        self._apply_aspect()
        self.engine.refresh()
        self._refresh_marker_dialog()

    def _update_title(self):
        self._update_settings_label()
        self._update_undo_state()
        name = os.path.basename(self.project.path) if self.project.path else "Senza titolo"
        self.setWindowTitle(f"{'• ' if self.project.dirty else ''}{name} — Moviola")

    def _mark_dirty(self):
        self.project.dirty = True
        self._update_title()
        self._refresh_marker_dialog()

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
        self.color_page.play_btn.setText("❚❚" if playing and not src else "▶")
        self.src_play_btn.setText("❚❚" if playing and src else "▶")

    def _stretch_toggled(self, on):
        self.cfg["speed_stretch"] = bool(on)
        self._save_cfg()

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
            self.src_info.setText(self._specs(src))
            self.src_dur.setText(f"/ {tc(src['duration'], src['fps'])}")
        else:
            self.src_name.setText("Seleziona una clip nella lista")
            self.src_info.setText("")
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
        if f.mode != "source" and self.page == PAGE_COLOR:
            self._parade_update()

    def _on_position(self, t):
        fps = self.engine.fps()
        if self.engine.mode == "source":
            self.src_tc.setText(tc(t, fps))
            self.source_bar.set_position(t)
        else:
            self.tc_label.setText(tc(t, fps))
            self.color_page.tc_label.setText(tc(t, fps))
            ctx = self._anim_context() if self.page == PAGE_COLOR else None
            if ctx is not None:
                self.color_panel.set_playhead(t - ctx[0])
            elif self.right_tabs.currentIndex() == TAB_EFFECTS:
                v = self._video_items(list(self.timeline.view.selection))
                if v and v[0].get("rotate_anim"):
                    self.inspector.rot.set_playhead(t - v[0]["start"])
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

