"""Secondo schermo, segnaposti, fermo immagine, testi e riquadri."""
from __future__ import annotations


from PySide6.QtCore import QRectF, Qt
from PySide6.QtWidgets import (QInputDialog)

from ..project import is_overlay
from .marker_dialog import MarkerDialog
from .text_render import text_box
from .timeline import tc

from .common import PAGE_MONTAGE, TAB_EFFECTS


class MonitorMixin:
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

    # ------------------------------------------------- guide di composizione
    def _guides(self):
        g = self.cfg.get("guides")
        if not isinstance(g, dict):
            g = self.cfg["guides"] = {"grids": [], "crop": None}
        return g

    def _apply_guides(self):
        g = self._guides()
        for v in (self.viewer, self.src_viewer, self.mon_viewer):
            if v is not None:
                v.overlay.set_guides(g)
        btn = getattr(self, "guides_btn", None)
        if btn is not None:
            on = bool(g.get("grids") or g.get("crop"))
            btn.setText("Guide ●" if on else "Guide")
            btn.setStyleSheet("color:#e8a33d;" if on else "")

    def _build_guides_button(self):
        from PySide6.QtGui import QAction, QActionGroup
        from PySide6.QtWidgets import QMenu, QPushButton

        from .guides import CROPS, GRIDS
        btn = QPushButton("Guide")
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setToolTip("Guide di composizione e ritaglio per i social: servono solo per guardare, "
                       "non entrano nel video esportato")
        menu = QMenu(btn)
        self._grid_acts = {}
        for key, text in GRIDS:
            a = QAction(text, menu, checkable=True)
            a.setChecked(key in self._guides().get("grids", []))
            a.toggled.connect(lambda on, k=key: self._set_grid(k, on))
            menu.addAction(a)
            self._grid_acts[key] = a
        menu.addSeparator()
        head = menu.addAction("Mostra come verrebbe tagliato per…")
        head.setEnabled(False)
        grp = QActionGroup(menu)
        grp.setExclusive(True)
        self._crop_acts = {}
        for key, text in [(None, "Nessun ritaglio")] + [(k, t) for k, t, _r in CROPS]:
            a = QAction(text, menu, checkable=True)
            a.setChecked(key == self._guides().get("crop"))
            a.triggered.connect(lambda _c=False, k=key: self._set_crop(k))
            grp.addAction(a)
            menu.addAction(a)
            self._crop_acts[key] = a
        menu.addSeparator()
        menu.addAction("Nascondi tutte le guide", self._guides_off)
        btn.setMenu(menu)
        return btn

    def _set_grid(self, key, on):
        g = self._guides()
        s = [k for k in g.get("grids", []) if k != key]
        g["grids"] = s + ([key] if on else [])
        self._apply_guides()
        self._save_cfg()

    def _set_crop(self, key):
        self._guides()["crop"] = key
        self._apply_guides()
        self._save_cfg()

    def _guides_off(self):
        g = self._guides()
        g["grids"], g["crop"] = [], None
        for a in self._grid_acts.values():
            a.blockSignals(True)
            a.setChecked(False)
            a.blockSignals(False)
        self._crop_acts[None].setChecked(True)
        self._apply_guides()
        self._save_cfg()

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
        self.tl_info.setText(f"<br>{pw}×{ph} · {pr.fps:g} fps")
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
        m = self.project.add_marker(self.engine.position, kind="note")
        self.timeline.view.update()
        self._mark_dirty()
        self.statusBar().showMessage(f"Indicatore a {tc(m['t'], self.project.fps)}", 1500)

    def add_named_marker(self):
        t = self.engine.position if self.engine.mode == "timeline" else \
            self.engine.source_positions.get("__timeline__", 0.0)
        text, ok = QInputDialog.getText(self, "Capitolo", "Nome del capitolo:")
        if not ok:
            return
        self.project.checkpoint()
        m = self.project.add_marker(t)
        m["label"] = text.strip()
        self.timeline.view.update()
        self._mark_dirty()
        self._refresh_marker_dialog()

    def jump_marker(self, direction):
        t = self.engine.position
        fr = self.engine.frame_step() / 2
        pts = [m["t"] for m in self.project.markers]
        cand = [p for p in pts if p > t + fr] if direction > 0 else [p for p in pts if p < t - fr]
        if not cand:
            self.statusBar().showMessage("Nessun altro segnaposto.", 2000)
            return
        if self.engine.mode != "timeline":
            self.set_viewer_mode("timeline")
        self.engine.seek(cand[0] if direction > 0 else cand[-1])

    def show_markers(self):
        if getattr(self, "marker_dialog", None) is None:
            d = self.marker_dialog = MarkerDialog(self.project, self)
            d.setWindowFlag(Qt.Tool, True)
            d.goto.connect(self._marker_goto)
            d.checkpoint.connect(lambda: self.project.checkpoint())
            d.changed.connect(lambda: (self.timeline.view.update(), self._mark_dirty()))
        self.marker_dialog.refresh(self.project)
        self.marker_dialog.show()
        self.marker_dialog.raise_()

    def _marker_goto(self, t):
        if self.engine.mode != "timeline":
            self.set_viewer_mode("timeline")
        self.engine.seek(t)

    def _refresh_marker_dialog(self):
        d = getattr(self, "marker_dialog", None)
        if d is not None and d.isVisible():
            d.refresh(self.project)

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
