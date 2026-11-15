"""Timeline: modifica, azioni e proprietà dei pezzi."""
from __future__ import annotations

import copy

from PySide6.QtWidgets import (QInputDialog, QMessageBox)

from .. import color
from .. import speed as spd
from .project_settings import ProjectSettingsDialog
from .timeline import tc

from .common import PAGE_MONTAGE, TAB_COLOR, TAB_EFFECTS


class TimelineMixin:
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
            pr.add_marker(float(ids[0]), kind=ids[1] if len(ids) > 1 else "note")
        elif action == "toggle_marker_kind":
            mk = pr.marker(ids[0])
            if mk is None:
                return
            pr.checkpoint()
            mk["kind"] = "note" if pr.is_chapter(mk) else "chapter"
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
        ph = (self._timeline_pos() - it["start"]) if it else 0.0
        self.inspector.show_item(it, n, dur, ph)
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
        keep = not self.inspector.stretch_on()
        d_before = {i["id"]: pr.item_dur(i) for i in its}
        if key == "speed":
            pr.set_speed([i["id"] for i in its], value, keep_length=keep)
        elif key == "reverse":
            pr.set_reverse([i["id"] for i in its], bool(value))
            long_ = [i for i in its if i["kind"] == "video" and bool(value) and i["src_out"] - i["src_in"] > 25]
            if long_:
                self.statusBar().showMessage("Clip lunga all'indietro: in esportazione serve molta memoria "
                                             "(meglio sotto i 25 secondi, o tagliala a pezzi).", 7000)
        elif key == "trans":
            if value:
                pr.set_transition([i["id"] for i in its], value["type"], value["dur"], value["audio"])
            else:
                pr.set_transition([i["id"] for i in its], None)
        elif key == "speed_curve":
            pr.set_speed_curve([i["id"] for i in its], value, keep_length=keep)
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
            if kind == "audio" and key in ("hp_hz", "denoise", "normalize"):
                self._audio_clean_changed(its, key)
            if key.startswith("stab"):
                self._ensure_stab(its)
                self._update_stab_info()
        if keep and key in ("speed", "speed_curve") and any(
                abs(pr.item_dur(i) - d_before[i["id"]]) > 0.02 for i in its if i["id"] in d_before):
            self.statusBar().showMessage("La clip ha raggiunto la fine del file originale: "
                                         "si accorcia (spunta «Allunga la clip» per cambiarne la durata).", 6000)
        self._mark_dirty()
        self.timeline.view.update()
        if key in ("speed", "speed_curve", "duration"):
            self.timeline.sync()
            self._update_name_label()
            self.refresh_inspector()
        self.engine.refresh()
