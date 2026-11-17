"""Rimandi tra le schede: «Mostra nella clip», «Mostra in Montaggio», «Apri in Colore» e «Torna indietro»."""
from __future__ import annotations


from .common import PAGE_CLIP, PAGE_COLOR, PAGE_MONTAGE
from .timeline import tc

PAGE_NAMES = {0: "Archivio progetti", PAGE_CLIP: "Selezione clip", PAGE_MONTAGE: "Montaggio",
              PAGE_COLOR: "Colore", 4: "Esportazione"}


class LinksMixin:
    # ------------------------------------------------------ torna indietro
    def _nav_remember(self):
        """Ricorda scheda, selezione e posizione di partenza prima di un salto."""
        self.__dict__["_nav_back"] = {
            "page": self.page, "sel": list(self.timeline.view.selection), "t": float(self.playhead_time()),
            "ref": self.selected_ref, "panel": self.active_panel,
            "target": self.color_panel.target(),
        }
        self._update_back_button()

    def nav_back(self):
        b = self.__dict__.get("_nav_back")
        if not b:
            self.statusBar().showMessage("Non c'è un punto a cui tornare.", 2500)
            return
        self.__dict__["_nav_back"] = None
        pr = self.project
        self.timeline.view.set_selection({i for i in b["sel"] if pr.item(i)})
        self.active_panel = b["panel"]
        if b["ref"] and (pr.subclip(b["ref"]) or pr.source(b["ref"])):
            self.selected_ref = b["ref"]
            self.bin.select([b["ref"]])
        self.color_panel.set_target(b["target"])
        self.set_page(b["page"])
        if b["page"] != PAGE_CLIP:
            self._timeline_seek(b["t"])
            self.timeline.view.set_playhead(b["t"], follow=True)
            self.timeline.sync()
        self.refresh_color_panel()
        self._update_back_button()
        self.statusBar().showMessage(f"Tornato a «{PAGE_NAMES.get(b['page'], '')}».", 2500)

    def _update_back_button(self):
        b = self.__dict__.get("_nav_back")
        btn = getattr(self, "back_btn", None)
        if btn is None:
            return
        self.back_tb_act.setVisible(bool(b))
        if b:
            btn.setText(f"← Torna a {PAGE_NAMES.get(b['page'], '')}")
        if hasattr(self, "act_back"):
            self.act_back.setEnabled(bool(b))

    # ------------------------------------------------------------- utilità
    def _items_of_ref(self, ref):
        """Pezzi della timeline che usano quella parte (o, per una clip intera, qualsiasi sua parte)."""
        pr = self.project
        if pr.subclip(ref):
            its = [i for i in pr.items if i["kind"] == "video" and i.get("origin") == ref]
        else:
            its = [i for i in pr.items if i["kind"] == "video" and i.get("source") == ref]
        return sorted(its, key=lambda i: (i["start"], i["track"]))

    def _item_label(self, n, it):
        return f"{n} · a {tc(it['start'], self.project.fps)[:-3]} · {self.project.item_dur(it):.1f} s"

    # --------------------------------------------------------------- salti
    def show_item_in_clip(self, item_id):
        pr = self.project
        it = pr.item(item_id)
        if it is None or not it.get("source"):
            return
        self._nav_remember()
        src = pr.source(it["source"])
        origin = it.get("origin")
        sub = pr.subclip(origin) if origin else None
        a, b = float(it.get("src_in", 0.0)), float(it.get("src_out", 0.0))
        lo, hi = min(a, b), max(a, b)
        ref = src["id"]
        if sub and abs(sub["in"] - lo) < 0.05 and abs(sub["out"] - hi) < 0.05:
            ref = origin                                       # la parte coincide col pezzo: basta sceglierla
        else:
            self.marks[src["id"]] = [lo, hi]                   # altrimenti il tratto usato si vede tra I e O
        self.set_page(PAGE_CLIP)
        self.bin.select([ref])
        self.selected_ref = ref
        self.show_ref(ref)
        self.engine.seek(lo)
        self._update_source_bar()
        self.statusBar().showMessage(f"Il pezzo va da {tc(lo, src['fps'])} a {tc(hi, src['fps'])} di {src['id']}.", 6000)

    def show_item_in_montage(self, item_id):
        pr = self.project
        it = pr.item(item_id)
        if it is None:
            return
        self._nav_remember()
        self.active_panel = "timeline"
        self.timeline.view.set_selection({item_id})
        self.set_page(PAGE_MONTAGE)
        self._timeline_seek(it["start"])
        self.timeline.view.set_playhead(it["start"], follow=True)
        self.timeline.sync()
        self.timeline.view.update()

    def open_in_color(self, item_ids, level="item"):
        pr = self.project
        ids = [i for i in item_ids if pr.item(i) and pr.item(i)["kind"] == "video"]
        if not ids:
            return
        self._nav_remember()
        self.active_panel = "timeline"
        self.timeline.view.set_selection(set(ids))
        self.color_panel.set_target(level)
        self.set_page(PAGE_COLOR)
        first = min((pr.item(i) for i in ids), key=lambda i: i["start"])
        self._timeline_seek(first["start"] + min(0.1, pr.item_dur(first) / 2))
        self.refresh_color_panel()

    def ref_show_in_montage(self, ref, which=None):
        its = self._items_of_ref(ref)
        if not its:
            self.statusBar().showMessage("Questa clip non è usata nella timeline.", 3000)
            return
        it = next((i for i in its if i["id"] == which), its[0])
        self.show_item_in_montage(it["id"])

    def ref_open_in_color(self, ref):
        its = self._items_of_ref(ref)
        if not its:
            self.statusBar().showMessage("Questa clip non è usata nella timeline: non c'è nulla da vedere nel "
                                         "monitor del colore.", 4000)
            return
        self.open_in_color([its[0]["id"]], level="source")

    # --------------------------------------- azioni da menu (bin e timeline)
    def links_bin_action(self, action, ids):
        """Azioni «show_montage», «show_item:<id>», «open_color» dal menu della lista clip."""
        if action == "show_montage" and ids:
            self.ref_show_in_montage(ids[0])
        elif action.startswith("show_item:"):
            self.show_item_in_montage(action.split(":", 1)[1])
        elif action == "open_color" and ids:
            self.ref_open_in_color(ids[0])
        else:
            return False
        return True

    def uses_menu_entries(self, ref):
        """[(testo, id del pezzo)] per il sottomenu «Mostra in Montaggio»."""
        return [(self._item_label(n, it), it["id"]) for n, it in enumerate(self._items_of_ref(ref), 1)]
