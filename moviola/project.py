"""Modello dati del progetto (non distruttivo, nessuna dipendenza da Qt).

Un progetto contiene solo riferimenti ai file originali e parametri:
  - sorgenti   : le clip intere (clip1, clip2, ...)
  - sotto-clip : le parti scelte (clip1.1, clip1.2, ...) con ingresso/uscita
  - timeline   : tracce video e audio con i pezzi montati
  - regolazioni colore a tre livelli: clip intera, singolo pezzo, timeline

I tempi sono in secondi. Il file di progetto è un JSON (.moviola).
"""
from __future__ import annotations

import copy
import json
import os
import uuid

from . import color

VERSION = 1
MIN_LEN_FRAMES = 1


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def new_project_data() -> dict:
    return {
        "version": VERSION,
        "app": "Moviola",
        "settings": {"fps": 25.0, "width": 1920, "height": 1080, "auto": True},
        "next_source": 1,
        "sources": [],
        "subclips": [],
        "tracks": {
            "video": [{"name": f"V{i}", "muted": False} for i in (1, 2, 3)],
            "audio": [{"name": f"A{i}", "muted": False} for i in (1, 2, 3)],
        },
        "items": [],
        "global_grade": color.default_grade(),
        "use_proxies": True,
    }


class Project:
    def __init__(self, data: dict | None = None, path: str | None = None):
        self.data = data or new_project_data()
        self.path = path
        self._undo: list[str] = []
        self._redo: list[str] = []
        self.dirty = False

    # ------------------------------------------------------------------ I/O
    @classmethod
    def load(cls, path: str) -> "Project":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        base = new_project_data()
        for k, v in base.items():
            data.setdefault(k, v)
        return cls(data, path)

    def save(self, path: str | None = None):
        path = path or self.path
        if not path:
            raise ValueError("percorso mancante")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=1, ensure_ascii=False)
        os.replace(tmp, path)
        self.path = path
        self.dirty = False

    # ----------------------------------------------------------- Annulla
    def checkpoint(self):
        """Da chiamare PRIMA di ogni modifica che si vuole poter annullare."""
        self._undo.append(json.dumps(self.data))
        if len(self._undo) > 200:
            self._undo.pop(0)
        self._redo.clear()
        self.dirty = True

    def drop_checkpoint(self):
        """Elimina l'ultimo checkpoint (se l'azione non ha cambiato nulla)."""
        if self._undo:
            self._undo.pop()

    def can_undo(self):
        return bool(self._undo)

    def can_redo(self):
        return bool(self._redo)

    def undo(self):
        if not self._undo:
            return False
        self._redo.append(json.dumps(self.data))
        self.data = json.loads(self._undo.pop())
        self.dirty = True
        return True

    def redo(self):
        if not self._redo:
            return False
        self._undo.append(json.dumps(self.data))
        self.data = json.loads(self._redo.pop())
        self.dirty = True
        return True

    # ------------------------------------------------------------ Impostazioni
    @property
    def fps(self) -> float:
        return float(self.data["settings"]["fps"])

    @property
    def size(self):
        s = self.data["settings"]
        return int(s["width"]), int(s["height"])

    def frame(self) -> float:
        return 1.0 / self.fps

    def snap(self, t: float) -> float:
        f = self.fps
        return round(t * f) / f

    # ----------------------------------------------------------- Sorgenti
    @property
    def sources(self):
        return self.data["sources"]

    @property
    def subclips(self):
        return self.data["subclips"]

    @property
    def items(self):
        return self.data["items"]

    def source(self, sid):
        for s in self.sources:
            if s["id"] == sid:
                return s
        return None

    def subclip(self, cid):
        for c in self.subclips:
            if c["id"] == cid:
                return c
        return None

    def item(self, iid):
        for it in self.items:
            if it["id"] == iid:
                return it
        return None

    def find_source_by_path(self, path):
        ap = os.path.abspath(path)
        for s in self.sources:
            if os.path.abspath(s["path"]) == ap:
                return s
        return None

    def add_source(self, path: str, info: dict) -> dict:
        n = self.data["next_source"]
        self.data["next_source"] = n + 1
        src = {
            "id": f"clip{n}",
            "n": n,
            "path": os.path.abspath(path),
            "name": os.path.basename(path),
            "label": "",
            "duration": float(info.get("duration", 0.0)),
            "fps": float(info.get("fps") or 25.0),
            "width": int(info.get("width") or 0),
            "height": int(info.get("height") or 0),
            "has_video": bool(info.get("has_video", True)),
            "has_audio": bool(info.get("has_audio", False)),
            "codec": info.get("codec", ""),
            "next_sub": 1,
            "grade": color.default_grade(),
        }
        self.sources.append(src)
        st = self.data["settings"]
        if st.get("auto") and src["has_video"] and src["width"]:
            st["fps"] = round(src["fps"], 3)
            st["width"] = src["width"]
            st["height"] = src["height"]
            st["auto"] = False
        return src

    def delete_source(self, sid):
        self.data["sources"] = [s for s in self.sources if s["id"] != sid]
        self.data["subclips"] = [c for c in self.subclips if c["source"] != sid]
        self.data["items"] = [i for i in self.items if i["source"] != sid]

    def relink_source(self, sid, new_path):
        s = self.source(sid)
        if s:
            s["path"] = os.path.abspath(new_path)
            s["name"] = os.path.basename(new_path)

    # --------------------------------------------------------- Sotto-clip
    def subclips_of(self, sid):
        return [c for c in self.subclips if c["source"] == sid]

    def _clamp_range(self, src, tin, tout):
        fr = 1.0 / max(src["fps"], 1.0)
        dur = src["duration"]
        tin = max(0.0, min(tin, dur - fr))
        tout = max(tin + fr * MIN_LEN_FRAMES, min(tout, dur))
        return tin, tout

    def add_subclip(self, sid, tin, tout, label="") -> dict:
        src = self.source(sid)
        if src is None:
            raise KeyError(sid)
        if tout < tin:
            tin, tout = tout, tin
        tin, tout = self._clamp_range(src, tin, tout)
        n = src["next_sub"]
        src["next_sub"] = n + 1
        sub = {"id": f"{sid}.{n}", "source": sid, "n": n, "label": label, "in": tin, "out": tout}
        # mantiene l'ordine: dopo le altre sotto-clip della stessa sorgente
        idx = len(self.subclips)
        for i, c in enumerate(self.subclips):
            if c["source"] == sid:
                idx = i + 1
        self.subclips.insert(idx, sub)
        return sub

    def update_subclip(self, cid, tin=None, tout=None, label=None):
        c = self.subclip(cid)
        if c is None:
            return None
        src = self.source(c["source"])
        a = c["in"] if tin is None else tin
        b = c["out"] if tout is None else tout
        if b < a:
            a, b = b, a
        c["in"], c["out"] = self._clamp_range(src, a, b)
        if label is not None:
            c["label"] = label
        return c

    def duplicate_subclip(self, cid):
        c = self.subclip(cid)
        if c is None:
            return None
        return self.add_subclip(c["source"], c["in"], c["out"], c.get("label", ""))

    def delete_subclip(self, cid):
        self.data["subclips"] = [c for c in self.subclips if c["id"] != cid]

    def range_of(self, ref_id):
        """(sorgente, in, out) per una clip intera o una sotto-clip."""
        c = self.subclip(ref_id)
        if c:
            return self.source(c["source"]), c["in"], c["out"]
        s = self.source(ref_id)
        if s:
            return s, 0.0, s["duration"]
        return None, 0.0, 0.0

    # ------------------------------------------------------------ Tracce
    def tracks(self, kind):
        return self.data["tracks"][kind]

    def add_track(self, kind):
        tr = self.tracks(kind)
        tr.append({"name": f"{'V' if kind == 'video' else 'A'}{len(tr) + 1}", "muted": False})
        return len(tr) - 1

    def remove_track(self, kind, index):
        tr = self.tracks(kind)
        if len(tr) <= 1:
            return False
        if any(i["kind"] == kind and i["track"] == index for i in self.items):
            return False
        del tr[index]
        for i in self.items:
            if i["kind"] == kind and i["track"] > index:
                i["track"] -= 1
        for k, t in enumerate(tr):
            t["name"] = f"{'V' if kind == 'video' else 'A'}{k + 1}"
        return True

    # ------------------------------------------------------- Pezzi timeline
    @staticmethod
    def item_end(it):
        return it["start"] + (it["src_out"] - it["src_in"])

    def items_on(self, kind, track):
        return sorted((i for i in self.items if i["kind"] == kind and i["track"] == track),
                      key=lambda i: i["start"])

    def linked(self, it):
        if not it.get("link"):
            return [it]
        return [i for i in self.items if i.get("link") == it["link"]]

    def expand_linked(self, ids):
        out = set(ids)
        for iid in list(ids):
            it = self.item(iid)
            if it:
                out.update(i["id"] for i in self.linked(it))
        return out

    def timeline_end(self):
        return max((self.item_end(i) for i in self.items), default=0.0)

    def clear_range(self, kind, track, a, b, exclude=()):
        """Sovrascrittura: libera l'intervallo [a, b) su una traccia
        accorciando, dividendo o eliminando i pezzi che lo occupano."""
        eps = 1e-6
        new_items = []
        for it in self.items:
            if it["kind"] != kind or it["track"] != track or it["id"] in exclude:
                new_items.append(it)
                continue
            s, e = it["start"], self.item_end(it)
            if e <= a + eps or s >= b - eps:
                new_items.append(it)
                continue
            if s >= a - eps and e <= b + eps:
                continue  # completamente coperto: eliminato
            if s < a and e > b:
                right = copy.deepcopy(it)
                right["id"] = _uid()
                right["link"] = None
                it["src_out"] = it["src_in"] + (a - s)
                right["src_in"] = right["src_in"] + (b - s)
                right["start"] = b
                it["link"] = None
                new_items.extend([it, right])
                continue
            if s < a:
                it["src_out"] = it["src_in"] + (a - s)
            else:
                cut = b - s
                it["src_in"] += cut
                it["start"] = b
            new_items.append(it)
        self.data["items"] = new_items
        self._cleanup_links()

    def _cleanup_links(self):
        counts = {}
        for i in self.items:
            if i.get("link"):
                counts[i["link"]] = counts.get(i["link"], 0) + 1
        for i in self.items:
            if i.get("link") and counts[i["link"]] < 2:
                i["link"] = None

    def place_items(self, items):
        """Inserisce pezzi già costruiti, sovrascrivendo ciò che c'è sotto."""
        ids = {i["id"] for i in items}
        for it in items:
            self.clear_range(it["kind"], it["track"], it["start"], self.item_end(it), exclude=ids)
        self.items.extend(items)
        self._cleanup_links()

    def make_items(self, ref_id, start, vtrack=0, atrack=0, video=True, audio=True):
        """Costruisce i pezzi (video e/o audio collegati) per una clip.
        Il pezzo copia ingresso/uscita della sotto-clip: modifiche successive
        alla sotto-clip non cambiano la timeline."""
        src, tin, tout = self.range_of(ref_id)
        if src is None:
            return []
        link = _uid() if (src["has_video"] and src["has_audio"] and video and audio) else None
        start = max(0.0, self.snap(start))
        out = []
        base = {"source": src["id"], "origin": ref_id, "start": start, "src_in": tin, "src_out": tout, "link": link}
        if src["has_video"] and video:
            it = dict(base, id=_uid(), kind="video", track=vtrack, grade=color.default_grade())
            out.append(it)
        if src["has_audio"] and audio:
            it = dict(base, id=_uid(), kind="audio", track=atrack, gain_db=0.0)
            out.append(it)
        return out

    def add_to_timeline(self, ref_id, start, vtrack=0, atrack=0):
        items = self.make_items(ref_id, start, vtrack, atrack)
        self.place_items(items)
        return items

    def append_to_timeline(self, ref_ids, vtrack=0, atrack=0):
        """Accoda una o più clip alla fine della traccia."""
        t = max([self.item_end(i) for i in self.items
                 if (i["kind"] == "video" and i["track"] == vtrack) or (i["kind"] == "audio" and i["track"] == atrack)],
                default=0.0)
        placed = []
        for rid in ref_ids:
            its = self.add_to_timeline(rid, t, vtrack, atrack)
            if its:
                t = max(self.item_end(i) for i in its)
            placed.extend(its)
        return placed

    def move_items(self, ids, dt, dv=0, da=0, linked=True):
        """Sposta i pezzi (e i collegati) di dt secondi e di dv/da tracce,
        sovrascrivendo ciò che trovano. Da chiamare sullo stato di partenza."""
        ids = self.expand_linked(ids) if linked else set(ids)
        moving = [i for i in self.items if i["id"] in ids]
        if not moving:
            return
        min_start = min(i["start"] for i in moving)
        dt = max(dt, -min_start)
        nv = len(self.tracks("video"))
        na = len(self.tracks("audio"))
        vt = [i["track"] for i in moving if i["kind"] == "video"]
        at = [i["track"] for i in moving if i["kind"] == "audio"]
        if vt:
            dv = max(-min(vt), min(dv, nv - 1 - max(vt)))
        if at:
            da = max(-min(at), min(da, na - 1 - max(at)))
        for i in moving:
            i["start"] = self.snap(i["start"] + dt)
            i["track"] += dv if i["kind"] == "video" else da
        self.data["items"] = [i for i in self.items if i["id"] not in ids]
        self.place_items(moving)

    def trim_item(self, iid, edge, t, linked=True):
        """Allunga/accorcia un pezzo dal bordo 'left' o 'right' fino al tempo t
        della timeline, entro i limiti del file sorgente."""
        it = self.item(iid)
        if it is None:
            return
        group = self.linked(it) if linked else [it]
        t = self.snap(t)
        fr = self.frame()
        targets = []
        for g in group:
            src = self.source(g["source"])
            dur = src["duration"] if src else g["src_out"]
            if edge == "left":
                if abs(g["start"] - it["start"]) > 1e-6:
                    continue
                d = t - g["start"]
                d = max(d, -g["src_in"], -g["start"])
                d = min(d, (g["src_out"] - g["src_in"]) - fr)
                g["start"] += d
                g["src_in"] += d
                targets.append(g)
            else:
                if abs(self.item_end(g) - self.item_end(it)) > 1e-6:
                    continue
                d = t - self.item_end(g)
                d = min(d, dur - g["src_out"])
                d = max(d, -((g["src_out"] - g["src_in"]) - fr))
                g["src_out"] += d
                targets.append(g)
        ids = {g["id"] for g in targets}
        for g in targets:
            self.clear_range(g["kind"], g["track"], g["start"], self.item_end(g), exclude=ids)

    def split_at(self, t, ids=None):
        """Taglia al tempo t i pezzi che lo attraversano (solo quelli indicati
        e i loro collegati, oppure tutti se ids è None)."""
        t = self.snap(t)
        pool = self.expand_linked(ids) if ids else None
        new_links = {}
        created = []
        for it in list(self.items):
            if pool is not None and it["id"] not in pool:
                continue
            s, e = it["start"], self.item_end(it)
            if not (s + 1e-6 < t < e - 1e-6):
                continue
            right = copy.deepcopy(it)
            right["id"] = _uid()
            if it.get("link"):
                right["link"] = new_links.setdefault(it["link"], _uid())
            it["src_out"] = it["src_in"] + (t - s)
            right["src_in"] = it["src_out"]
            right["start"] = t
            self.items.append(right)
            created.append(right)
        return created

    def delete_items(self, ids, ripple=False):
        ids = set(ids)
        gone = [i for i in self.items if i["id"] in ids]
        if not gone:
            return
        a = min(i["start"] for i in gone)
        b = max(self.item_end(i) for i in gone)
        self.data["items"] = [i for i in self.items if i["id"] not in ids]
        self._cleanup_links()
        if ripple:
            # chiude il buco solo se nessun altro pezzo occupa l'intervallo
            busy = any(i["start"] < b - 1e-6 and self.item_end(i) > a + 1e-6 for i in self.items)
            if not busy:
                for i in self.items:
                    if i["start"] >= b - 1e-6:
                        i["start"] = self.snap(i["start"] - (b - a))

    def unlink(self, ids):
        for iid in ids:
            it = self.item(iid)
            if it:
                for g in self.linked(it):
                    g["link"] = None

    def link(self, ids):
        its = [self.item(i) for i in ids if self.item(i)]
        if len(its) >= 2:
            lk = _uid()
            for i in its:
                i["link"] = lk

    # ---------------------------------------------------- Ricerca in timeline
    def video_item_at(self, t):
        """Pezzo video visibile al tempo t (la traccia più alta vince)."""
        tracks = self.tracks("video")
        best = None
        for it in self.items:
            if it["kind"] != "video" or tracks[it["track"]].get("muted"):
                continue
            if it["start"] - 1e-9 <= t < self.item_end(it) - 1e-9:
                if best is None or it["track"] > best["track"]:
                    best = it
        return best

    def snap_points(self, exclude=()):
        pts = {0.0}
        for it in self.items:
            if it["id"] in exclude:
                continue
            pts.add(it["start"])
            pts.add(self.item_end(it))
        return sorted(pts)

    # ------------------------------------------------------------- Colore
    def grades_for_item(self, it):
        """Regolazioni da applicare a un pezzo, nell'ordine:
        clip intera -> pezzo -> timeline (generale)."""
        src = self.source(it["source"])
        return [src.get("grade") if src else None, it.get("grade"), self.data.get("global_grade")]

    def grades_for_source(self, sid):
        src = self.source(sid)
        return [src.get("grade") if src else None]

    def video_segments(self, start=0.0, end=None):
        """Scompone la timeline in tratti consecutivi: per ogni tratto il
        pezzo video visibile (o None = nero). Usato dall'export."""
        end = self.timeline_end() if end is None else end
        cuts = {start, end}
        for it in self.items:
            if it["kind"] == "video":
                for t in (it["start"], self.item_end(it)):
                    if start < t < end:
                        cuts.add(t)
        cuts = sorted(cuts)
        segs = []
        for a, b in zip(cuts[:-1], cuts[1:]):
            if b - a < 1e-6:
                continue
            it = self.video_item_at((a + b) / 2)
            if segs and segs[-1]["item"] is it and (it is None or abs(segs[-1]["end"] - a) < 1e-6):
                segs[-1]["end"] = b
                continue
            segs.append({"start": a, "end": b, "item": it})
        return segs

    def missing_sources(self):
        return [s for s in self.sources if not os.path.exists(s["path"])]
