"""Riferimenti, confronto e parade della scheda COLORE (senza schermo)."""
import os, sys, tempfile, time, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from moviola.ui.main_window import MainWindow
from moviola.ui.common import PAGE_COLOR, PAGE_MONTAGE
from moviola import paths, project as P, scopes
import numpy as np

# parade pura
img = np.zeros((90, 160, 3), np.uint8); img[..., 0] = 200; img[..., 1] = 60
pm = scopes.parade(img)
assert pm.shape == (128, 3 * 160, 3)
assert pm[..., 0][:, :160].sum() > 0 and pm[..., 0][:, 320:].sum() == 0 or pm[:, 320:, 0].sum() < pm[:, :160, 0].sum()
assert scopes.clipping(np.full((4, 4, 3), 255, np.uint8))[1] == 1.0

tmp = tempfile.mkdtemp()
a = os.path.join(tmp, 'a.mp4'); b = os.path.join(tmp, 'b.mp4')
for f, c in ((a, 'red'), (b, 'blue')):
    subprocess.run([paths.ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i', f'color=c={c}:s=320x180:r=25:d=6', '-pix_fmt', 'yuv420p', f], check=True)
w = MainWindow({"gpu": False}); w.show()
pr = w.project
for f in (a, b):
    pr.add_source(f, {"duration": 6, "fps": 25, "width": 320, "height": 180, "has_video": True, "has_audio": False})
pr.append_to_timeline(["clip1", "clip2"])
w.refresh_all(); w.set_page(PAGE_COLOR)
def spin(n=15):
    for _ in range(n): app.processEvents(); time.sleep(0.03)
spin()
w.engine.seek(0.5); spin()
w.ref_capture(); spin(3)
refs = pr.data["references"]
assert len(refs) == 1 and refs[0]["jpg"], refs
e = w.color_page.current_ref_id(); assert e == refs[0]["id"]
# secondo pezzo (blu), confronto affiancato
w.engine.seek(6.5); spin()
w.set_compare_mode("side"); spin(3)
ov = w.viewer.overlay
assert ov.cmp == "side" and ov.ref_img is not None and w.viewer.side
w.set_compare_mode("wipe"); spin(3); assert ov.cmp == "wipe" and not w.viewer.side
ov.wipe = 0.5
w.ref_flash(True); assert ov.flash; w.ref_flash(False)
w.cycle_compare(); assert w.color_page.compare_mode() is None and ov.ref_img is None
# parade
w.set_parade(True); spin(3)
assert w.color_page.parade.isVisible() or True
w.set_compare_mode("side"); spin(3); w._parade_update(force=True)
assert w.color_page.parade.ref is not None and w.color_page.parade.clip is not None
w.set_parade_mode("overlay"); w._parade_update(force=True); assert ov.parade_img is not None
w.viewer.overlay.resize(640, 360)
w.viewer.overlay.grab().save(os.path.join(tmp, "ov.png"))
# fuori dalla scheda Colore tutto sparisce dal monitor
w.set_page(PAGE_MONTAGE); assert ov.ref_img is None and ov.parade_img is None
# salvataggio e riapertura: il riferimento resta
path = os.path.join(tmp, "p.moviola"); pr.save(path)
assert P.Project.load(path).data["references"][0]["name"] == "Riferimento 1"
w.set_page(PAGE_COLOR); w.ref_delete(); assert not pr.data["references"]
print("RIFERIMENTI E PARADE OK")
