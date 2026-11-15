"""Progetto: apri, salva, autosalvataggio, esporta e opzioni."""
from __future__ import annotations

import json
import os
import sys
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QFileDialog, QMessageBox)

from .. import audio, media, paths, update
from ..project import Project
from .export_dialog import ExportDialog
from .text_render import render_png

from .common import PAGE_CLIP, SHORTCUTS


class ProjectMixin:
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
        self._prepare_clean([i for i in pr.items if i["kind"] == "audio"])
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
    AUTOSAVE_SECONDS = 60
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
