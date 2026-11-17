"""Rimandi tra le schede e «Torna indietro» (senza schermo)."""
import os, sys, tempfile, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from moviola.ui.main_window import MainWindow
from moviola.ui.common import PAGE_CLIP, PAGE_COLOR, PAGE_MONTAGE
from moviola import paths
tmp = tempfile.mkdtemp()
a = os.path.join(tmp, 'a.mp4')
subprocess.run([paths.ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=320x180:r=25:d=20', '-pix_fmt', 'yuv420p', a], check=True)
w = MainWindow({"gpu": False}); w.show()
pr = w.project
pr.add_source(a, {"duration": 20, "fps": 25, "width": 320, "height": 180, "has_video": True, "has_audio": False})
pr.add_subclip("clip1", 1, 5); pr.add_subclip("clip1", 8, 12)
pr.append_to_timeline(["clip1.1", "clip1.2", "clip1.1"])    # clip1.1 usata due volte
w.refresh_all(); w.set_page(PAGE_MONTAGE)
vids = sorted((i for i in pr.items if i["kind"] == "video"), key=lambda i: i["start"])
# Montaggio -> Colore (livello Pezzo) e ritorno
w.timeline.view.set_selection({vids[1]["id"]})
w.open_in_color([vids[1]["id"]], "item")
assert w.page == PAGE_COLOR and w.color_panel.target() == "item" and w.timeline.view.selection == {vids[1]["id"]}
assert w.back_tb_act.isVisible() and "Montaggio" in w.back_btn.text()
w.nav_back(); assert w.page == PAGE_MONTAGE and w.timeline.view.selection == {vids[1]["id"]}
assert not w.back_tb_act.isVisible()
# Montaggio -> clip: il pezzo 3 (clip1.1 di nuovo) è tra 1 e 5 s
w.show_item_in_clip(vids[2]["id"])
assert w.page == PAGE_CLIP and w.selected_ref == "clip1.1", w.selected_ref
w.nav_back(); assert w.page == PAGE_MONTAGE
# pezzo accorciato: parte diversa -> si evidenzia il tratto con I/O sulla clip intera
vids[0]["src_out"] = vids[0]["src_in"] + 2.0
w.show_item_in_clip(vids[0]["id"])
assert w.selected_ref == "clip1" and w.marks["clip1"][1] - w.marks["clip1"][0] > 1.9, (w.selected_ref, w.marks)
# Clip -> Montaggio: usata due volte -> due voci; una sola per clip1.2
ents = w.uses_menu_entries("clip1.1"); assert len(ents) == 2, ents
assert len(w.uses_menu_entries("clip1.2")) == 1
w.bin_action("show_item:" + ents[1][1], ["clip1.1"])
assert w.page == PAGE_MONTAGE and w.timeline.view.selection == {ents[1][1]}
pr.add_subclip("clip1", 14, 16)
assert w.uses_menu_entries("clip1.3") == []
w.bin_action("open_color", ["clip1.2"])
assert w.page == PAGE_COLOR and w.color_panel.target() == "source"
# Colore -> Montaggio / clip dalla striscia
w.color_page._strip_action("montage", vids[2]["id"]); assert w.page == PAGE_MONTAGE
w.set_page(PAGE_COLOR); w.color_page._strip_action("clip", vids[2]["id"]); assert w.page == PAGE_CLIP
w.nav_back(); assert w.page == PAGE_COLOR
print("RIMANDI OK")
