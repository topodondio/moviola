import sys,os,time; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from PySide6.QtWidgets import QApplication
app=QApplication([])
from moviola.ui.main_window import MainWindow
from moviola.ui.common import *
from moviola import media
w=MainWindow({"gpu":False}); w.show()
import subprocess, tempfile
from moviola import paths
a=os.path.join(tempfile.mkdtemp(),'a.mp4'); subprocess.run([paths.ffmpeg_exe(),'-v','error','-f','lavfi','-i','testsrc2=s=320x180:r=25:d=20','-pix_fmt','yuv420p',a],check=True); pr=w.project
s=pr.add_source(a,{"duration":20,"fps":25,"width":1920,"height":1080,"has_video":True,"has_audio":True})
pr.add_subclip("clip1",0,4); pr.add_subclip("clip1",6,10); pr.add_subclip("clip1",12,16)
pr.append_to_timeline(["clip1.1","clip1.2","clip1.3"])
w.refresh_all(); app.processEvents()
w.set_page(PAGE_MONTAGE); app.processEvents()
w.set_page(PAGE_COLOR); app.processEvents()
cp=w.color_page
print("viewer in colore:", cp.view_holder.indexOf(w.viewer)>=0, "tiles", len(cp.strip.body.items), "sel", w.timeline.view.selection, cp.info.text())
print(w.color_panel.info.text())
# seleziona 2 e 3 con ctrl
ids=[i["id"] for i in cp.strip.body.items]
from PySide6.QtCore import Qt
cp.strip._clicked(ids[0], Qt.NoModifier); cp.strip._clicked(ids[2], Qt.ControlModifier)
assert sorted(w.timeline.view.selection)==sorted([ids[0],ids[2]])
w.color_panel.sliders["saturation"].set_value(40); w._grade_begin("saturation"); w._grade_edit("saturation",40)
print([round(i["grade"].get("saturation",0)) for i in pr.items if i["kind"]=="video"])
w.copy_grade(); cp.strip._clicked(ids[1], Qt.NoModifier); w.paste_grade()
print([round(i.get("grade",{}).get("saturation",0)) for i in pr.items if i["kind"]=="video"])
w.set_page(PAGE_MONTAGE); app.processEvents(); print("viewer back:", w.viewer_holder.indexOf(w.viewer)>=0)
w.set_page(PAGE_EXPORT); print(w.pages.currentWidget() is w.export_page)

assert [round(i.get("grade",{}).get("saturation",0)) for i in pr.items if i["kind"]=="video"]==[40,40,40]
print("SCHEDA COLORE OK")
# --- passaggio del mouse sulla striscia: anteprima del punto della timeline, clic = posizione
from PySide6.QtCore import QPointF
st = w.color_page.strip
st.refresh(); app.processEvents()
body = st.body
it0 = body.items[1]
n_pos = w.engine.position
iid = it0["id"]
st._hover(iid, 0.5); st._hover_timer.stop(); st._hover_flush()
exp = it0["start"] + 0.5 * w.project.item_dur(it0)
assert abs(w.engine.position - exp) < 0.1, (w.engine.position, exp)
st._hover_end(); assert abs(w.engine.position - n_pos) < 0.1
st._clicked(iid, Qt.NoModifier, 0.25)
assert abs(w.engine.position - (it0["start"] + 0.25 * w.project.item_dur(it0))) < 0.1
# istogramma aggiornato nella scheda Colore
w._parade_update(force=True)
assert w.color_panel.curve.hist is not None and w.color_panel.curve.hist.ndim == 2
print("STRISCIA E ISTOGRAMMA OK")
