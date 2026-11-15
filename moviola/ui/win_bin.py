"""Lista clip: selezione, segni, sottoclip e azioni sul contenitore."""
from __future__ import annotations

import copy
import os

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QFileDialog, QInputDialog, QMessageBox)

from .common import PAGE_CLIP
from .. import color
from .timeline import tc



class BinMixin:
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
            # sulla timeline I/O segnano un tratto: lo si estrae, rimuove o esporta da solo
            self.project.checkpoint()
            self.project.set_tl_mark(which, self.engine.position)
            self._mark_dirty()
            self.timeline.view.update()
            a, b = self.project.tl_range
            fps = self.project.fps
            self.statusBar().showMessage(
                "Tratto: " + (tc(a, fps) if a is not None else "—") + " → " + (tc(b, fps) if b is not None else "—")
                + "  ·  Maiusc+X estrae, Alt+Maiusc+X rimuove, Alt+X toglie i segni", 6000)
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
            t = self.project.tl_range[which]
            if t is not None:
                self.engine.seek(t)
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

    def _playhead_t(self):
        return self.engine.source_positions.get("__timeline__", 0.0) if self.engine.mode == "source" \
            else self.engine.position

    def insert_at_playhead(self):
        """F9: inserisce le clip alla testina spingendo avanti tutto il resto."""
        ids = self._bin_refs()
        if not ids:
            return
        pr = self.project
        total = 0.0
        for rid in ids:
            src, a, b = pr.range_of(rid)
            if src is not None:
                total += b - a
        t = self._playhead_t()
        pr.checkpoint()
        pr.insert_gap(t, total)
        pr.drop_checkpoint()  # add_refs_at ne fa uno suo: l'inserimento è un'azione sola
        self.add_refs_at(ids, t, 0, None)

    def overwrite_at_playhead(self):
        """F10: mette le clip alla testina sopra a ciò che c'è (non sposta nulla)."""
        ids = self._bin_refs()
        if ids:
            self.add_refs_at(ids, self._playhead_t(), 0, None)

    def extract_range(self, close_gap=True):
        pr = self.project
        a, b = pr.tl_range
        if a is None or b is None:
            self.statusBar().showMessage("Metti i segni I (ingresso) e O (uscita) sulla timeline.", 4000)
            return
        pr.checkpoint()
        if not pr.range_remove(a, b, close_gap):
            pr.drop_checkpoint()
            self.statusBar().showMessage("Nel tratto non c'è niente da togliere.", 3000)
            return
        pr.clear_tl_range()
        self.engine.seek(min(a, pr.timeline_end()))
        self._after_timeline_edit()

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
        if action == "open":
            # doppio clic: nella scheda Clip la clip resta nel monitor anche se il mouse
            # esce dalla riga (finché non si fa doppio clic su un'altra); nel montaggio accoda
            if self.page == PAGE_CLIP:
                self.bin.select(ids)
                self.show_ref(ids[0])
                self._hover_back = None
                return
            action = "append"
        if action == "append":
            self.bin.select(ids)
            return self.append_selected()
        if action == "insert_playhead":
            self.bin.select(ids)
            return self.insert_at_playhead()
        if action == "overwrite_playhead":
            self.bin.select(ids)
            return self.overwrite_at_playhead()
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
