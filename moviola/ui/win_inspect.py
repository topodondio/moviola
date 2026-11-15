"""Pulizia audio, stabilizzazione, azioni dell'ispettore e anteprime di taglio."""
from __future__ import annotations

import os


from .. import stabilize, audio
from .. import speed as spd
from ..project import is_overlay
from .timeline import tc

from .common import PAGE_MONTAGE


class InspectMixin:
    # ------------------------------------------------------ pulizia audio
    def _audio_clean_changed(self, items, key):
        """Dopo un cambio di pulizia: prepara l'audio ripulito in sottofondo e
        (ri)misura il volume dei pezzi normalizzati."""
        self._prepare_clean(items)
        self._audio_measure(items)

    def _prepare_clean(self, items):
        todo = [dict(i) for i in items if self.store.clean_key(i) and not self.store.ready(self.store.clean_key(i))]
        if todo:
            def job(todo=todo):
                for it in todo:
                    try:
                        self.store.ensure_clean(it)
                    except Exception:
                        pass
                self.media_event.emit("audio_clean", "", "")
            self.statusBar().showMessage("Preparo l'audio ripulito…", 4000)
            self.jobs.submit(job)

    def _audio_measure(self, items, only_enabled=True):
        n = 0
        for it in items:
            if only_enabled and not it.get("normalize"):
                continue
            g = audio.normalize_gain(self.store, it)
            if g is not None:
                it["norm_db"] = g
                n += 1
        if n:
            self.refresh_inspector()
        return n

    def _on_audio_clean(self):
        """L'audio ripulito è pronto: il volume si misura su quello."""
        self.statusBar().showMessage("Audio ripulito pronto.", 3000)
        self._audio_measure([i for i in self.project.items if i["kind"] == "audio"])
        self._mark_dirty()

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
        if act == "refresh_inspector":
            return self.refresh_inspector()
        if act == "audio_norm":
            its = self._sel_items("audio")
            self.project.checkpoint()
            if self._audio_measure(its, only_enabled=False):
                for it in its:
                    it["normalize"] = True
                self._mark_dirty()
                self.refresh_inspector()
            else:
                self.project.drop_checkpoint()
                self.statusBar().showMessage("Non riesco a misurare questa clip (audio non pronto?).", 4000)
            return
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
