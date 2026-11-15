"""Pannello colore, animazioni dei parametri e preset."""
from __future__ import annotations

import copy
import json
import os
import time

from PySide6.QtWidgets import (QInputDialog, QMessageBox)

from .. import color, paths



class ColorMixin:
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

    def _anim_context(self):
        """(inizio, durata) del tempo su cui si anima il colore: il pezzo
        selezionato o tutta la timeline; None per la clip intera."""
        t = self.color_panel.target()
        if t == "item":
            its = self._video_items(list(self.timeline.view.selection))
            if its:
                return its[0]["start"], self.project.item_dur(its[0])
            return None
        if t == "global":
            return 0.0, max(self.project.timeline_end(), 1.0)
        return None

    def _timeline_pos(self):
        return self.engine.position if self.engine.mode == "timeline" else \
            self.engine.source_positions.get("__timeline__", 0.0)

    def refresh_color_panel(self):
        grades, info = self.color_targets()
        self.color_panel.set_editable(bool(grades))
        ctx = self._anim_context()
        anim_ctx = (ctx[1], self._timeline_pos() - ctx[0]) if ctx else None
        self.color_panel.show_grade(grades[0] if grades else None, info, anim_ctx)

    def _anim_edit(self, key, pts):
        grades, _ = self.color_targets()
        ctx = self._anim_context()
        if not grades or ctx is None:
            return
        rel = self._timeline_pos() - ctx[0]
        for g in grades:
            anim = g.setdefault("anim", {})
            if pts:
                anim[key] = copy.deepcopy(pts)
            else:
                old = anim.pop(key, None)
                if old:  # torna fisso al valore che aveva alla testina
                    v = color.value_at(old, rel)
                    if key.startswith("hsl:"):
                        _, k, i = key.split(":")
                        g["hsl"][k][int(i)] = v
                    else:
                        g[key] = v
            if not anim:
                g.pop("anim", None)
        self._mark_dirty()
        self.engine.refresh()
        self.timeline.view.update()

    def _anim_seek(self, t_rel):
        ctx = self._anim_context()
        if ctx is None:
            return
        if self.engine.mode != "timeline":
            self.set_viewer_mode("timeline")
        self.engine.seek(ctx[0] + max(0.0, min(t_rel, ctx[1] - self.project.frame())))

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
