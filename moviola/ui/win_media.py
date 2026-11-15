"""Importazione, eventi dei processi in background e miniature."""
from __future__ import annotations

import os

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFileDialog, QMessageBox)

from .. import media



class MediaMixin:
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
        if kind == "audio_clean":
            return self._on_audio_clean()
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
            self._prepare_clean([i for i in pr.items if i["kind"] == "audio" and i["source"] == key])
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
