"""Preferenze, cache e aggiornamenti."""
from __future__ import annotations

import json
import os
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QMessageBox)

from .. import __version__, stabilize, audio, media, paths, update
from ..project import Project
from .preferences import DEFAULT_LIMIT_GB, PreferencesDialog

from .common import PAGE_PROJECTS


class SystemMixin:
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

    def customize_shortcuts(self):
        from .shortcuts import ShortcutsDialog
        ShortcutsDialog(self.shortcuts, self).exec()

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
        self._prepare_clean([i for i in self.project.items if i["kind"] == "audio"])
        self.timeline.view.update()

    def _cache_notice(self):
        if self.cfg.get("cache_dir") and not paths.media_cache_available():
            self.statusBar().showMessage("Il disco della cache non è collegato: per ora uso una cache temporanea "
                                         "sul computer.", 10000)
