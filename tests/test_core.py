"""Test del motore (senza interfaccia). Esegui: python -m tests.test_core"""
import os, subprocess, sys, tempfile, json
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from moviola import project as P, color, audio, media, export, paths

def make_clip(path, dur, colorname, freq):
    ff = paths.ffmpeg_exe()
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=25:duration={dur}",
                    "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path], check=True)

def info(dur): return {"duration": dur, "fps": 25, "width": 640, "height": 360, "has_video": True, "has_audio": True}

def test_model():
    pr = P.Project()
    s1 = pr.add_source("/x/a.mp4", info(10)); s2 = pr.add_source("/x/b.mp4", info(8))
    assert s1["id"] == "clip1" and s2["id"] == "clip2"
    assert pr.size == (640, 360)
    c11 = pr.add_subclip("clip1", 1, 3); c12 = pr.add_subclip("clip1", 5, 7); c21 = pr.add_subclip("clip2", 0, 2)
    assert [c["id"] for c in pr.subclips] == ["clip1.1", "clip1.2", "clip2.1"]
    d = pr.duplicate_subclip("clip1.1"); assert d["id"] == "clip1.3"
    pr.delete_subclip("clip1.2"); n = pr.add_subclip("clip1", 0, 1); assert n["id"] == "clip1.4"  # numeri stabili
    its = pr.append_to_timeline(["clip1.1", "clip2.1"])
    assert len(its) == 4 and abs(pr.timeline_end() - 4) < 1e-6
    # la timeline resta com'era se cambio la sotto-clip
    pr.update_subclip("clip1.1", tout=4)
    v = [i for i in pr.items if i["kind"] == "video"][0]; assert abs(v["src_out"] - 3) < 1e-6
    # sovrascrittura: metto clip1.4 (1 s) a 1.6 su V1
    pr.add_to_timeline("clip1.4", 1.6)
    vids = pr.items_on("video", 0)
    assert [round(i["start"], 3) for i in vids] == [0, 1.6, 2.6], [i["start"] for i in vids]
    # taglio
    pr.split_at(3.0)
    assert len(pr.items_on("video", 0)) == 4 and len(pr.items_on("audio", 0)) == 4
    # collegamenti dopo split: ogni coppia ha link proprio
    links = {}
    for i in pr.items:
        links.setdefault(i["link"], []).append(i["kind"])
    assert all(sorted(v) == ["audio", "video"] for k, v in links.items() if k), links
    # spostamento su V2 e trim
    last = pr.items_on("video", 0)[-1]
    pr.move_items([last["id"]], 1.0, dv=1)
    assert pr.item(last["id"])["track"] == 1
    pr.trim_item(last["id"], "right", pr.item_end(pr.item(last["id"])) + 100)  # limitato dalla sorgente
    it = pr.item(last["id"]); assert it["src_out"] <= 8 + 1e-6
    # pezzo visibile: traccia alta vince
    assert pr.video_item_at(it["start"] + 0.1)["id"] == it["id"]
    segs = pr.video_segments()
    assert abs(segs[-1]["end"] - pr.timeline_end()) < 1e-6
    # annulla
    pr.checkpoint(); pr.delete_items([it["id"]], ripple=True); assert pr.item(it["id"]) is None
    pr.undo(); assert pr.item(it["id"]) is not None
    pr.redo(); assert pr.item(it["id"]) is None
    print("modello ok")

def test_color():
    g = color.default_grade(); assert color.is_identity(g)
    lut = color.build_lut([g, None, {}], 17)
    assert np.allclose(lut.reshape(-1, 3), color.identity_grid(17))
    g2 = {"saturation": -100}
    out = color.apply_grade(np.array([[0.8, 0.2, 0.1]], np.float32), g2)
    assert np.ptp(out) < 1e-4
    # HSL: desaturo solo i blu
    g3 = color.normalize({}); g3["hsl"]["sat"][5] = -100
    blue = color.apply_grade(np.array([[0.1, 0.1, 0.9]], np.float32), g3)
    red = color.apply_grade(np.array([[0.9, 0.1, 0.1]], np.float32), g3)
    assert np.ptp(blue) < 0.05 and np.ptp(red) > 0.7
    print("colore ok")

def test_export(tmp):
    a = os.path.join(tmp, "a.mp4"); b = os.path.join(tmp, "b.mp4")
    make_clip(a, 6, "red", 440); make_clip(b, 5, "blue", 660)
    pr = P.Project(); store = audio.AudioStore()
    for p_ in (a, b):
        s = pr.add_source(p_, info(6 if p_ == a else 5))
        store.register(s["id"], media.make_audio_cache(p_))
    assert store.get("clip1").shape[0] > 6 * 47000
    pk = media.make_peaks(a); assert pk.max() > 0.05, pk.max()
    pr.add_subclip("clip1", 1, 3); pr.add_subclip("clip2", 2, 4)
    pr.append_to_timeline(["clip1.1", "clip2.1"])
    pr.add_to_timeline("clip2.1", 5.0)  # lascia un buco nero 4-5
    v = pr.items_on("video", 0)[0]; v["grade"]["saturation"] = -100
    pr.data["global_grade"]["contrast"] = 30
    m = audio.mix_timeline(pr, store, 0.5, 48000); assert np.abs(m).max() > 0.05
    out = os.path.join(tmp, "out.mp4")
    cmd, wd, dur = export.build_job(pr, store, out)
    err = export.run_job(cmd, wd, dur, progress=lambda f: None)
    assert err is None, err
    r = subprocess.run([paths.ffmpeg_exe(), "-i", out], capture_output=True, text=True).stderr
    assert "Duration: 00:00:07" in r or "Duration: 00:00:06.9" in r, r
    # verifica colore: fotogramma a 0.5 s deve essere grigio (saturazione -100)
    fr = subprocess.run([paths.ffmpeg_exe(), "-v", "error", "-ss", "0.5", "-i", out, "-frames:v", "1",
                         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    img = np.frombuffer(fr, np.uint8).reshape(360, 640, 3).astype(int)
    assert np.abs(img[..., 0] - img[..., 2]).mean() < 6, np.abs(img[..., 0] - img[..., 2]).mean()
    fr = subprocess.run([paths.ffmpeg_exe(), "-v", "error", "-ss", "4.5", "-i", out, "-frames:v", "1",
                         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    img = np.frombuffer(fr, np.uint8).reshape(360, 640, 3)
    assert img.mean() < 20  # buco nero
    # proxy
    px = media.make_proxy(a, 6, progress=lambda f: None); assert os.path.getsize(px) > 1000
    th = media.make_thumb(a); assert os.path.exists(th)
    pr.save(os.path.join(tmp, "prova.moviola")); P.Project.load(os.path.join(tmp, "prova.moviola"))
    print("export ok:", out)

def main():
    test_model(); test_color()
    with tempfile.TemporaryDirectory() as t:
        test_export(t)
        test_effects(t)
    print("TUTTI I TEST OK")


def test_effects(tmp):
    """Velocità, dissolvenze, zoom/posizione, scritte."""
    a = os.path.join(tmp, "a.mp4")
    make_clip(a, 8, "red", 440)
    pr = P.Project(); store = audio.AudioStore()
    s = pr.add_source(a, info(8)); store.register(s["id"], media.make_audio_cache(a))
    pr.add_subclip("clip1", 0, 4)
    its = pr.append_to_timeline(["clip1.1"])
    v = [i for i in its if i["kind"] == "video"][0]
    pr.append_to_timeline(["clip1.1"])
    # velocità 2x: il primo pezzo dura 2 s e il secondo slitta indietro
    pr.set_speed([v["id"]], 2.0)
    assert abs(pr.item_dur(v) - 2.0) < 1e-6
    second = [i for i in pr.items_on("video", 0)][1]
    assert abs(second["start"] - 2.0) < 1e-6, second["start"]
    assert abs(pr.timeline_end() - 6.0) < 1e-6
    a_it = [i for i in pr.linked(v) if i["kind"] == "audio"][0]
    assert abs(pr.item_dur(a_it) - 2.0) < 1e-6
    m = audio.mix_timeline(pr, store, 0.0, 48000 * 2); assert np.abs(m).max() > 0.05
    # dissolvenza da bianco sul primo, a nero sul secondo; zoom e posizione sul secondo
    v["fade_in"] = 1.0; v["fade_in_color"] = "white"
    second["fade_out"] = 1.0
    second["zoom"] = 0.5; second["pos_x"] = 0.25
    amt, col = pr.fade_at(v, 0.0); assert amt == 1.0 and col == (1.0, 1.0, 1.0)
    # taglio dentro la dissolvenza: il pezzo a destra non deve rifarla
    # scritta
    t = pr.add_text(1.0, dur=2.0, text="Ciao")
    assert t["track"] == 2 and pr.texts_at(1.5) == [t]
    pr.trim_item(t["id"], "right", 3.6)
    assert abs(pr.item_end(t) - 3.6) < 1e-6
    rendered = []

    try:
        import PIL  # noqa: F401  (solo per il test: nell'app le scritte le disegna Qt)
    except ImportError:
        print("PIL non installato: salto la verifica delle scritte"); return

    def renderer(item, W, H, path):
        from PIL import Image, ImageDraw
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(im).rectangle([0, 0, 60, 40], fill=(0, 255, 0, 255))
        im.save(path); rendered.append(item["id"]); return True

    out = os.path.join(tmp, "fx.mp4")
    cmd, wd, dur = export.build_job(pr, store, out, text_renderer=renderer)
    err = export.run_job(cmd, wd, dur); assert err is None, err
    assert rendered == [t["id"]]

    def frame(ts):
        fr = subprocess.run([paths.ffmpeg_exe(), "-v", "error", "-ss", f"{ts}", "-i", out, "-frames:v", "1",
                             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        return np.frombuffer(fr, np.uint8).reshape(360, 640, 3).astype(int)

    f0 = frame(0.0); assert f0.mean() > 230, f0.mean()          # bianco all'inizio
    f2 = frame(2.0)
    assert f2[20, 30, 1] > 200 and f2[20, 30, 0] < 60          # scritta verde in alto a sinistra
    f3 = frame(3.6)
    assert f3[:, :160].mean() < 15                              # zoom 0.5 spostato a destra: sinistra nera
    assert f3[:, 330:620].mean() > 40
    f5 = frame(5.9); assert f5.mean() < 30                      # dissolvenza a nero alla fine
    r = subprocess.run([paths.ffmpeg_exe(), "-i", out], capture_output=True, text=True).stderr
    assert "Duration: 00:00:06" in r or "Duration: 00:00:05.9" in r, r
    print("effetti ok")


if __name__ == "__main__":
    main()
