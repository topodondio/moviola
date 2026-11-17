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
