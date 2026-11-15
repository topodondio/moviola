"""Test del motore (senza interfaccia). Esegui: python -m tests.test_core"""
import os, subprocess, sys, tempfile, json, threading
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

def test_curve_bars(tmp):
    """Velocità a curva e bande nere: il fotogramma esportato deve venire dal
    punto giusto del file (il rosso codifica il tempo sorgente)."""
    from moviola import speed as spd
    src = os.path.join(tmp, "tempo.mp4")
    subprocess.run([paths.ffmpeg_exe(), "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=black:s=640x360:r=25:d=8,format=rgb24,geq=r='T*25':g='T*25':b='T*25'",
                    "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p", src], check=True)
    pr = P.Project(); store = audio.AudioStore()
    pr.add_source(src, dict(info(8), has_audio=False))
    pr.add_subclip("clip1", 0, 6)
    v = pr.append_to_timeline(["clip1.1"])[0]
    pr.set_speed_curve([v["id"]], [[0, 0.5], [0.5, 1.0], [1, 3.0]])
    T = pr.item_dur(v)
    assert abs(pr.src_time(v, pr.item_end(v)) - 6.0) < 1e-3
    # taglio: il movimento resta identico
    before = [pr.src_time(v, x) for x in (0.5, T * 0.7)]
    pr.split_at(T * 0.4)
    after = [pr.src_time(pr.video_item_at(x), x) for x in (0.5, T * 0.7)]
    assert max(abs(a_ - b_) for a_, b_ in zip(before, after)) < 0.02, (before, after)
    left = pr.video_item_at(0.1)
    left["bars"] = 0.1
    pr.data["settings"]["bars"] = 0.05
    out = os.path.join(tmp, "curva.mp4")
    cmd, wd, dur = export.build_job(pr, store, out)
    err = export.run_job(cmd, wd, dur); assert err is None, err

    def frame(ts):
        fr = subprocess.run([paths.ffmpeg_exe(), "-v", "error", "-ss", f"{ts}", "-i", out, "-frames:v", "1",
                             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        return np.frombuffer(fr, np.uint8).reshape(360, 640, 3).astype(int)

    for x in (0.4, 1.5, T * 0.6, T * 0.9):
        it_ = pr.video_item_at(x)
        want = pr.src_time(it_, x) * 25
        got = frame(x)[180, 320, 0]
        assert abs(got - want) < 8, (x, got, want)
    f = frame(1.0)
    assert f[30, 320].max() < 10 and f[40, 320, 0] > 5      # banda del pezzo (36 px)
    f = frame(T * 0.9)
    assert f[15, 320].max() < 10 and f[25, 320, 0] > 5      # banda generale (18 px)
    print("curva e bande ok")


def test_box_music(tmp):
    """Riquadro colorato sopra al video (con dissolvenza) e musica sull'ultima traccia audio."""
    a = os.path.join(tmp, "v.mp4")
    make_clip(a, 6, "red", 440)
    m = os.path.join(tmp, "musica.wav")
    subprocess.run([paths.ffmpeg_exe(), "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=10",
                    m], check=True)
    pr = P.Project(); store = audio.AudioStore()
    s1 = pr.add_source(a, info(6)); store.register(s1["id"], media.make_audio_cache(a))
    s2 = pr.add_source(m, dict(info(10), has_video=False, width=0, height=0))
    store.register(s2["id"], media.make_audio_cache(m))
    pr.append_to_timeline(["clip1"])
    mus = pr.append_to_timeline(["clip2"])
    assert len(mus) == 1 and mus[0]["kind"] == "audio" and mus[0]["track"] == 2, mus
    assert abs(mus[0]["start"]) < 1e-6  # la musica parte da zero sulla sua traccia
    box = pr.add_shape(1.0, dur=3.0, color="#ff00ff", opacity=1.0, width=0.5, height=0.2, pos_x=0.25, pos_y=0.1,
                       fade_in=0.0, fade_out=1.0)
    assert box["track"] == 2 and pr.texts_at(2.0) == [box]
    pr.split_at(2.0, [box["id"]])
    assert len([i for i in pr.items if i["kind"] == "shape"]) == 2
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("PIL non installato: salto la verifica del riquadro"); return

    def renderer(item, W, H, path):  # stessa geometria di text_render.shape_rect
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        w, h = item["width"] * W, item["height"] * H
        cx, cy = item["pos_x"] * W, item["pos_y"] * H
        ImageDraw.Draw(im).rectangle([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], fill=(255, 0, 255, 255))
        im.save(path); return True

    out = os.path.join(tmp, "box.mp4")
    cmd, wd, dur = export.build_job(pr, store, out, text_renderer=renderer)
    err = export.run_job(cmd, wd, dur); assert err is None, err
    assert abs(dur - 10.0) < 1e-6  # la musica allunga il montaggio

    def frame(ts):
        fr = subprocess.run([paths.ffmpeg_exe(), "-v", "error", "-ss", f"{ts}", "-i", out, "-frames:v", "1",
                             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        return np.frombuffer(fr, np.uint8).reshape(360, 640, 3).astype(int)

    def magenta_share(ts):
        f = frame(ts)
        return ((f[..., 0] > 200) & (f[..., 2] > 200) & (f[..., 1] < 60)).mean()

    base = magenta_share(5.0)  # il video di prova ha già un po' di magenta
    for ts, extra in ((0.5, 0.0), (1.5, 0.1), (2.5, 0.1), (3.95, 0.0)):
        got = magenta_share(ts) - base
        assert abs(got - extra) < 0.03, (ts, got, extra)  # riquadro = 50% × 20% del fotogramma
    print("riquadro e musica ok")


def _solid(path, colr, dur, audio_src):
    subprocess.run([paths.ffmpeg_exe(), "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"color={colr}:s=640x360:r=25:d={dur}", "-f", "lavfi", "-i", audio_src,
                    "-c:v", "libx264", "-pix_fmt", "yuv444p", "-qp", "0", "-c:a", "aac", "-shortest", path],
                   check=True)


def test_transitions(tmp):
    """Transizioni tra due clip, incrocio audio, fermo immagine, volume disegnato, segnaposti."""
    a = os.path.join(tmp, "rosso.mp4"); b = os.path.join(tmp, "blu.mp4")
    _solid(a, "red", 4, "anullsrc=r=48000:cl=stereo:d=4")
    _solid(b, "blue", 4, "sine=frequency=500:duration=4")
    pr = P.Project(); store = audio.AudioStore()
    for p_ in (a, b):
        s_ = pr.add_source(p_, info(4)); store.register(s_["id"], media.make_audio_cache(p_))
    pr.add_subclip("clip1", 1, 3); pr.add_subclip("clip2", 1, 3)
    pr.append_to_timeline(["clip1.1", "clip2.1"])
    va, vb = pr.items_on("video", 0)
    assert abs(vb["start"] - 2.0) < 1e-6

    def frame(out, ts):
        fr = subprocess.run([paths.ffmpeg_exe(), "-v", "error", "-ss", f"{ts}", "-i", out, "-frames:v", "1",
                             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        return np.frombuffer(fr, np.uint8).reshape(360, 640, 3).astype(int)

    def run(name):
        out = os.path.join(tmp, name + ".mp4")
        cmd, wd, dur = export.build_job(pr, store, out)
        err = export.run_job(cmd, wd, dur); assert err is None, (name, err[-800:])
        return out

    # dissolvenza incrociata di 1 s centrata sul taglio
    pr.set_transition([vb["id"]], "dissolve", 1.0)
    tr = pr.transition_at(1.8); assert tr and abs(tr["t0"] - 1.5) < 0.05 and abs(tr["t1"] - tr["t0"] - 1.0) < 1e-6
    assert pr.transition_at(1.4) is None and pr.transition_at(2.6) is None
    segs = pr.video_segments()
    assert len(segs) == 3 and segs[1]["trans"] is not None, segs
    out = run("dissolvenza")
    c = frame(out, 2.0)[180, 320]; assert 90 < c[0] < 170 and 90 < c[2] < 170, c   # metà rosso, metà blu
    assert frame(out, 1.0)[180, 320][0] > 230 and frame(out, 3.0)[180, 320][2] > 230
    # audio: il blu (con il suono) entra già prima del taglio, sfumando
    ext = pr.audio_extensions(); assert any(abs(v[0] - 0.5) < 1e-6 for v in ext.values()), ext
    m = audio.mix_timeline(pr, store, 1.6, 4800); assert 0.005 < np.abs(m).max() < 0.5
    m = audio.mix_timeline(pr, store, 1.0, 4800); assert np.abs(m).max() < 1e-3
    # tendina verso sinistra: il blu entra da destra
    pr.set_transition([vb["id"]], "wipe_l", 1.0)
    f = frame(run("tendina"), 1.75)
    assert f[180, 40][0] > 200 and f[180, 600][2] > 200, (f[180, 40], f[180, 600])
    # scorrimento verso destra: il blu entra da sinistra
    pr.set_transition([vb["id"]], "push_r", 1.0)
    f = frame(run("scorrimento"), 1.75)
    assert f[180, 40][2] > 200 and f[180, 600][0] > 200, (f[180, 40], f[180, 600])
    # passaggio dal nero: nero al taglio
    pr.set_transition([vb["id"]], "dip_black", 1.0)
    out = run("nero")
    assert frame(out, 2.0)[180, 320].max() < 40 and 60 < frame(out, 1.75)[180, 320][0] < 200
    pr.set_transition([vb["id"]], None)

    # fermo immagine: 1 s fermo a 0.5 s, il resto slitta in avanti
    end0 = pr.timeline_end()
    fr = pr.insert_freeze(va["id"], 0.6, 1.0)
    assert fr and abs(pr.timeline_end() - end0 - 1.0) < 1e-6 and abs(pr.item_dur(fr) - 1.0) < 1e-6
    assert abs(pr.src_time(fr, 1.2) - 1.6) < 1e-6
    assert pr.video_item_at(1.0) is fr and pr.video_item_at(1.6)["source"] == "clip1"
    out = run("fermo")
    assert frame(out, 1.0)[180, 320][0] > 230
    r = subprocess.run([paths.ffmpeg_exe(), "-i", out], capture_output=True, text=True).stderr
    assert "Duration: 00:00:05" in r or "Duration: 00:00:04.9" in r, r

    # volume disegnato (sul pezzo audio del blu): da -40 dB a 0 dB
    ab = [i for i in pr.items if i["kind"] == "audio" and i["source"] == "clip2"][0]
    ab["gain_pts"] = [[1.0, -40.0], [3.0, 0.0]]
    t_in = ab["start"]
    q1 = np.abs(audio.mix_timeline(pr, store, t_in + 0.1, 2400)).max()
    q2 = np.abs(audio.mix_timeline(pr, store, t_in + 1.8, 2400)).max()
    assert q2 > q1 * 10, (q1, q2)

    # segnaposti: sulla timeline e sul pezzo (si spostano con il pezzo)
    mk = pr.add_marker(0.52); assert abs(mk["t"] - 0.52) < 1e-6 and 0.52 in pr.snap_points()
    st = pr.add_item_marker(ab["id"], t_in + 0.4)
    assert any(abs(x - (t_in + 0.4)) < 1e-6 for x in pr.item_marker_times(ab))
    pr.move_items([ab["id"]], 0.4, linked=False)
    assert any(abs(x - (t_in + 0.8)) < 1e-6 for x in pr.item_marker_times(ab)), (st, pr.item_marker_times(ab))
    print("transizioni, fermo immagine, volume e segnaposti ok")


def test_color_anim(tmp):
    """Colore animato nel tempo: la saturazione del pezzo va da 0 a -100 in 2 s."""
    a = os.path.join(tmp, "rosso_anim.mp4")
    _solid(a, "red", 4, "anullsrc=r=48000:cl=stereo:d=4")
    pr = P.Project(); store = audio.AudioStore()
    s_ = pr.add_source(a, info(4)); store.register(s_["id"], media.make_audio_cache(a))
    v = [i for i in pr.append_to_timeline(["clip1"]) if i["kind"] == "video"][0]
    v["grade"]["anim"] = {"saturation": [[1.0, 0.0], [3.0, -100.0]]}
    assert pr.anim_cuts(v) == [1.0, 3.0]
    g = pr.grades_at(v, 2.0)[1]; assert abs(g["saturation"] + 50) < 1e-6 and "anim" not in g
    segs = pr.video_segments(); assert len(segs) == 3, segs
    pr.data["global_grade"]["anim"] = {"exposure": [[0.0, 0.0], [4.0, 0.0]]}  # piatta: nessun effetto
    out = os.path.join(tmp, "anim.mp4")
    cmd, wd, dur = export.build_job(pr, store, out)
    err = export.run_job(cmd, wd, dur); assert err is None, err[-800:]

    def px(ts):
        fr = subprocess.run([paths.ffmpeg_exe(), "-v", "error", "-ss", f"{ts}", "-i", out, "-frames:v", "1",
                             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        return np.frombuffer(fr, np.uint8).reshape(360, 640, 3).astype(int)[180, 320]

    def sat(c):
        return c.max() - c.min()

    s0, s1, s2, s3 = sat(px(0.5)), sat(px(2.0)), sat(px(2.9)), sat(px(3.5))
    assert s0 > 200 and s3 < 15 and s0 > s1 > s2 > s3, (s0, s1, s2, s3)
    assert 60 < s1 < 190, s1  # a metà: circa metà saturazione
    print("colore animato ok")


def test_ripple():
    """Ripple: rifilare e eliminare richiudono; la musica che continua sotto resta."""
    pr = P.Project()
    pr.add_source("/x/a.mp4", info(10))
    pr.add_source("/x/m.wav", dict(info(30), has_video=False))
    pr.add_subclip("clip1", 2, 4); pr.add_subclip("clip1", 5, 7)
    pr.append_to_timeline(["clip1.1", "clip1.2"])
    music = pr.append_to_timeline(["clip2"])[0]
    a, b = pr.items_on("video", 0)
    pr.trim_item(a["id"], "right", 1.0, ripple=True)
    assert abs(pr.item_end(a) - 1.0) < 1e-6 and abs(b["start"] - 1.0) < 1e-6
    ab = [i for i in pr.items_on("audio", 0)]
    assert abs(ab[1]["start"] - 1.0) < 1e-6  # anche l'audio collegato segue
    assert abs(music["start"]) < 1e-6        # la musica (iniziata prima) non si sposta
    pr.trim_item(b["id"], "left", 1.6, ripple=True)
    assert abs(b["start"] - 1.0) < 1e-6 and abs(b["src_in"] - 5.6) < 1e-6
    pr.delete_items(pr.expand_linked([a["id"]]), ripple=True)
    assert abs(b["start"]) < 1e-6 and abs(music["start"]) < 1e-6
    print("ripple ok")


def test_cache_and_paths(tmp):
    """Cache su un disco esterno con limite; percorsi relativi del progetto."""
    import time
    ssd = os.path.join(tmp, "SSD")
    os.makedirs(os.path.join(ssd, "Video")); os.makedirs(os.path.join(ssd, "Progetti"))
    cache = os.path.join(ssd, "Moviola Cache"); os.makedirs(cache)
    old = paths.media_cache_setting()
    paths.set_media_cache(cache)
    try:
        assert paths.cache_root() == cache
        v = os.path.join(ssd, "Video", "neve.mp4")
        make_clip(v, 3, "red", 440)
        a = media.make_audio_cache(v)
        assert a.startswith(cache)
        # la chiave non dipende dalla cartella: lo stesso file altrove usa la stessa cache
        k1 = paths.file_key(v)
        pr = P.Project(); pr.add_source(v, info(3)); pr.append_to_timeline(["clip1"])
        pp = os.path.join(ssd, "Progetti", "gita.moviola"); pr.save(pp)
        assert pr.sources[0]["rel"] == "../Video/neve.mp4"
        # il disco cambia nome (o viene collegato a un altro computer)
        ssd2 = os.path.join(tmp, "SSD_rinominato"); os.rename(ssd, ssd2)
        q = P.Project.load(os.path.join(ssd2, "Progetti", "gita.moviola"))
        assert os.path.exists(q.sources[0]["path"]) and not q.missing_sources()
        assert paths.file_key(q.sources[0]["path"]) == k1
        # disco scollegato: cache temporanea sul computer
        assert paths.cache_root() != cache and "cache_temporanea" in paths.cache_root()
        os.rename(ssd2, ssd)
        assert paths.cache_root() == cache
        # limite: i file più vecchi se ne vanno, quelli da tenere restano
        big = []
        for i in range(4):
            f = os.path.join(paths.cache_dir("proxy"), f"vecchio{i}.mov")
            open(f, "wb").write(b"\0" * 400_000)
            os.utime(f, (time.time() - 1000 + i,) * 2)
            big.append(f)
        before = paths.cache_usage()
        freed = paths.enforce_cache_limit(before - 500_000, keep=[a])
        assert freed >= 400_000 and os.path.exists(a) and not os.path.exists(big[0])
        assert paths.clear_cache(keep=[a]) > 0 and os.path.exists(a)
    finally:
        paths.set_media_cache(old)
    print("cache e percorsi ok")


def test_bins(tmp):
    pr = P.Project()
    a = pr.add_source("/x/a.mp4", info(10)); b = pr.add_source("/x/b.mp4", info(8))
    m = pr.add_source("/x/song.mp3", {"duration": 60, "has_video": False, "has_audio": True})
    pr.add_subclip("clip1", 1, 3)
    musica = pr.add_bin("Musica"); sci = pr.add_bin("Sci")
    assert pr.add_bin("Musica")["name"] == "Musica 2"  # nomi unici tra le sorelle
    neve = pr.add_bin("Neve", parent=sci["id"])
    assert pr.move_to_bin(["clip1.1", "clip2"], neve["id"]) == 2  # la parte sposta la clip intera
    pr.move_to_bin([m["id"]], musica["id"])
    assert a["bin"] == neve["id"] and b["bin"] == neve["id"] and m["bin"] == musica["id"]
    assert not pr.move_bin(sci["id"], neve["id"])  # non dentro una sua sotto-cartella
    assert pr.move_bin(neve["id"], None) and pr.bin(neve["id"])["parent"] is None
    pr.move_bin(neve["id"], sci["id"])
    pr.delete_bin(sci["id"])  # elimina anche Neve: le clip restano, fuori dalle cartelle
    assert pr.bin(neve["id"]) is None and "bin" not in a and "bin" not in b
    pr.move_to_bin(["clip1"], None); assert "bin" not in a
    # salvataggio e riapertura; i progetti vecchi senza cartelle si aprono uguale
    f = os.path.join(tmp, "bins.moviola"); pr.save(f)
    pr2 = P.Project.load(f)
    assert [x["name"] for x in pr2.bins] == ["Musica", "Musica 2"] and pr2.source(m["id"])["bin"] == musica["id"]
    d = json.load(open(f)); d.pop("bins"); json.dump(d, open(f, "w"))
    assert P.Project.load(f).bins == []
    # annulla
    pr2.checkpoint(); pr2.delete_bin(musica["id"]); pr2.undo()
    assert pr2.bin(musica["id"]) and pr2.source(m["id"])["bin"] == musica["id"]
    print("cartelle ok")


def test_duplicate():
    pr = P.Project()
    pr.add_source("/x/a.mp4", info(10)); pr.add_subclip("clip1", 1, 3)
    a = pr.append_to_timeline(["clip1.1"]); b = pr.append_to_timeline(["clip1.1"])   # 0-2 e 2-4 su V1/A1
    txt = pr.add_text(0.0, dur=1.0, text="Titolo", color="#ff0000")
    v1 = [i for i in a if i["kind"] == "video"][0]; v1["zoom"] = 1.5; v1["grade"]["exposure"] = 0.7
    v2 = [i for i in b if i["kind"] == "video"][0]; pr.set_transition([v2["id"]], "dissolve", 0.5, True)
    n = len(pr.items)
    cp = pr.duplicate_items([v1["id"]])            # dopo c'è la seconda clip: la copia va nel primo spazio libero
    assert len(cp) == 2 and len(pr.items) == n + 2 and all(abs(c["start"] - 4.0) < 1e-6 for c in cp)
    cv = [c for c in cp if c["kind"] == "video"][0]; ca = [c for c in cp if c["kind"] == "audio"][0]
    assert cv["zoom"] == 1.5 and cv["grade"]["exposure"] == 0.7 and cv["link"] == ca["link"] != v1["link"]
    cv["grade"]["exposure"] = 0.1; assert v1["grade"]["exposure"] == 0.7   # copie indipendenti
    ct = pr.duplicate_items([txt["id"]])[0]
    assert ct["text"] == "Titolo" and ct["color"] == "#ff0000" and abs(ct["start"] - 1.0) < 1e-6 and ct["track"] == txt["track"]
    c2 = pr.duplicate_items([v2["id"]])            # la transizione era con una clip non copiata: non si porta dietro
    assert "trans" not in [c for c in c2 if c["kind"] == "video"][0]
    both = pr.duplicate_items([v1["id"], v2["id"]])  # copiate insieme: la transizione tra le due resta
    assert sum(1 for c in both if c.get("trans")) == 1 and min(c["start"] for c in both) >= 8.0 - 1e-6
    for k in ("video", "audio"):                   # nessuna sovrapposizione
        for tr in range(3):
            its = pr.items_on(k, tr)
            assert all(pr.item_end(x) <= y["start"] + 1e-6 for x, y in zip(its, its[1:]))
    print("duplica ok")


def test_range_edit():
    pr = P.Project()
    pr.add_source("/x/a.mp4", info(20))
    pr.add_subclip("clip1", 0, 4); pr.add_subclip("clip1", 4, 6)
    pr.append_to_timeline(["clip1.1"]); pr.append_to_timeline(["clip1.2"])      # 0-4 e 4-6
    m = pr.add_marker(5.0, "dopo"); m0 = pr.add_marker(1.0, "prima")
    pr.set_tl_mark(0, 1.0); pr.set_tl_mark(1, 3.0)
    assert pr.tl_range == (1.0, 3.0)
    pr.set_tl_mark(0, 3.5); assert pr.tl_range[1] is None and abs(pr.tl_range[0] - 3.5) < 0.05               # I oltre O: O si annulla
    pr.set_tl_mark(0, 1.0); pr.set_tl_mark(1, 3.0)
    # Estrai: il tratto sparisce e il resto scorre indietro
    pr.range_remove(1.0, 3.0, True)
    assert abs(pr.timeline_end() - 4.0) < 1e-6, pr.timeline_end()
    assert abs(m["t"] - 3.0) < 1e-6 and pr.marker(m0["id"]) is not None or abs(m0["t"] - 1.0) < 1e-6
    for k in ("video", "audio"):
        its = pr.items_on(k, 0)
        assert all(abs(pr.item_end(x) - y["start"]) < 1e-6 for x, y in zip(its, its[1:])), "buco rimasto"
        assert sum(pr.item_dur(i) for i in its) > 3.9
    # Rimuovi: il buco resta
    pr2 = P.Project(); pr2.add_source("/x/a.mp4", info(20)); pr2.add_subclip("clip1", 0, 6)
    pr2.append_to_timeline(["clip1.1"])
    pr2.range_remove(2.0, 3.0, False)
    v = pr2.items_on("video", 0); assert len(v) == 2 and abs(v[1]["start"] - 3.0) < 1e-6 and abs(pr2.timeline_end() - 6.0) < 1e-6
    # Inserisci: tutto ciò che segue la testina scorre avanti
    pr3 = P.Project(); pr3.add_source("/x/a.mp4", info(20)); pr3.add_subclip("clip1", 0, 6); pr3.add_subclip("clip1", 8, 10)
    pr3.append_to_timeline(["clip1.1"])
    pr3.insert_gap(2.0, 2.0); pr3.add_to_timeline("clip1.2", 2.0, 0, 0)
    v = pr3.items_on("video", 0)
    assert [round(x["start"], 3) for x in v] == [0.0, 2.0, 4.0], [x["start"] for x in v]
    assert abs(pr3.timeline_end() - 8.0) < 1e-6
    print("segni I/O, estrai e inserisci ok")


def test_audio_clean(tmp):
    """Pulizia audio: taglia-bassi, riduzione rumore e normalizzazione, in anteprima e in export."""
    rate = audio.RATE
    t = np.arange(int(rate * 4)) / rate
    voice = 0.12 * np.sin(2 * np.pi * 440 * t)            # «voce» a 440 Hz
    wind = 0.25 * np.sin(2 * np.pi * 40 * t)              # rombo a 40 Hz
    x = np.stack([voice + wind, voice + wind], 1)
    raw = os.path.join(tmp, "a.s16")
    (np.clip(x, -1, 1) * 32767).astype("<i2").tofile(raw)
    store = audio.AudioStore(); store.register("clip1", raw)
    pr = P.Project(); pr.add_source("/x/a.mp4", info(4)); pr.add_subclip("clip1", 0, 4)
    its = pr.append_to_timeline(["clip1.1"]); a = [i for i in its if i["kind"] == "audio"][0]
    def level(freq_chunk):
        m = audio.mix_timeline(pr, store, 0.5, rate)[:, 0]
        sp = np.abs(np.fft.rfft(m)); fr = np.fft.rfftfreq(len(m), 1 / rate)
        return sp[(fr > freq_chunk - 5) & (fr < freq_chunk + 5)].max() / len(m) * 2
    w0, v0 = level(40), level(440)
    a["hp_hz"] = 150
    assert store.clean_key(a) and store.ensure_clean(a), "pulizia non riuscita"
    w1, v1 = level(40), level(440)
    assert w1 < w0 * 0.1 and v1 > v0 * 0.9, (w0, w1, v0, v1)       # il rombo cala, la voce resta
    g = audio.normalize_gain(store, a)
    assert g is not None and g > 0, g
    a["normalize"] = True; a["norm_db"] = g
    v2 = level(440)
    assert abs(20 * np.log10(v2 / v1) - g) < 0.5, (v1, v2, g)
    a["hp_hz"] = 0; a["denoise"] = 0.5
    assert store.ensure_clean(a)
    print("pulizia audio ok")


def test_speed_keep_length():
    """Velocità senza allungare: la durata in timeline resta, cambia la parte di file usata."""
    pr = P.Project(); pr.add_source("/x/a.mp4", info(20)); pr.add_subclip("clip1", 2, 6)
    it = [i for i in pr.append_to_timeline(["clip1.1"]) if i["kind"] == "video"][0]
    au = [i for i in pr.linked(it) if i["kind"] == "audio"][0]
    d0 = pr.item_dur(it)
    pr.set_speed([it["id"]], 2.0, keep_length=True)
    assert abs(pr.item_dur(it) - d0) < 1e-6 and abs(pr.item_dur(au) - d0) < 1e-6
    assert abs((it["src_out"] - it["src_in"]) - 2 * d0) < 1e-6
    pr.set_speed([it["id"]], 0.5, keep_length=True)
    assert abs(pr.item_dur(it) - d0) < 1e-6 and abs((it["src_out"] - it["src_in"]) - d0 / 2) < 1e-6
    pr.set_speed([it["id"]], 8.0, keep_length=True)            # oltre la fine del file: si ferma lì
    assert it["src_out"] <= 20.0 + 1e-6 and pr.item_dur(it) < d0
    pr2 = P.Project(); pr2.add_source("/x/a.mp4", info(20)); pr2.add_subclip("clip1", 2, 6)
    it2 = [i for i in pr2.append_to_timeline(["clip1.1"]) if i["kind"] == "video"][0]
    pr2.set_speed([it2["id"]], 2.0)                             # senza la spunta nel modello: si accorcia
    assert abs(pr2.item_dur(it2) - d0 / 2) < 1e-6
    print("velocità a durata fissa ok")


def test_reverse(tmp):
    """Riproduzione inversa: tempi sorgente, tagli, audio ed esportazione."""
    from moviola import speed as spd
    pr = P.Project(); pr.add_source("/x/a.mp4", info(20)); pr.add_subclip("clip1", 2, 6)
    its = pr.append_to_timeline(["clip1.1"])
    v = [i for i in its if i["kind"] == "video"][0]; a = [i for i in its if i["kind"] == "audio"][0]
    d = pr.item_dur(v)
    pr.set_reverse([v["id"]], True)
    assert v.get("reverse") and a.get("reverse")                       # anche il collegato
    assert abs(pr.src_time(v, v["start"]) - 6.0) < 1e-9 and abs(pr.src_time(v, v["start"] + d) - 2.0) < 1e-9
    assert abs(spd.tl_time(v, 5.0) - (v["start"] + 1.0)) < 1e-9
    pr.set_speed([v["id"]], 1.25, keep_length=True)                      # a durata fissa: l'inizio visto resta a 6 s
    assert abs(pr.item_dur(v) - d) < 1e-6 and abs(v["src_out"] - 6.0) < 1e-6 and abs(v["src_in"] - (6 - 1.25 * d)) < 1e-6
    pr.set_speed([v["id"]], 1.0, keep_length=True)
    # taglio a metà: la parte dopo il taglio mostra la prima metà del file
    pr.split_at(v["start"] + 2.0, [v["id"]])
    parts = sorted((i for i in pr.items if i["kind"] == "video"), key=lambda i: i["start"])
    assert abs(parts[0]["src_out"] - 6.0) < 1e-6 and abs(parts[0]["src_in"] - 4.0) < 1e-6, parts[0]
    assert abs(parts[1]["src_out"] - 4.0) < 1e-6 and abs(parts[1]["src_in"] - 2.0) < 1e-6, parts[1]
    # bordo sinistro allungato: si prende file dopo src_out
    pr.trim_item(parts[0]["id"], "left", parts[0]["start"], linked=False)
    # audio: la rampa 0..1 diventa 1..0
    rate = 1000
    data = np.tile(np.linspace(0, 30000, 20 * rate, dtype=np.float32)[:, None], (1, 2))
    pr2 = P.Project(); pr2.add_source("/x/a.mp4", info(20)); pr2.add_subclip("clip1", 4, 8)
    its2 = pr2.append_to_timeline(["clip1.1"]); a2 = [i for i in its2 if i["kind"] == "audio"][0]
    c1 = audio._item_chunk(data, a2, a2["start"], a2["start"] + 4, rate)
    a2["reverse"] = True
    c2 = audio._item_chunk(data, a2, a2["start"], a2["start"] + 4, rate)
    assert c1[10, 0] < c1[3000, 0] and c2[10, 0] > c2[3000, 0], (c1[10, 0], c1[3000, 0], c2[10, 0], c2[3000, 0])
    # esportazione: clip con un ramp di colore nel tempo (rosso → blu), all'indietro diventa blu → rosso
    ff = paths.ffmpeg_exe()
    clip = os.path.join(tmp, "ramp.mp4")
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=red:s=320x180:r=25:d=1,format=rgb24[a];color=c=blue:s=320x180:r=25:d=1,format=rgb24[b];"
                    "[a][b]concat=n=2:v=1:a=0", "-pix_fmt", "yuv420p", clip], check=True)
    pr3 = P.Project(); store = audio.AudioStore()
    s3 = pr3.add_source(clip, info(2)); pr3.add_subclip(s3["id"], 0, 2)
    pr3.append_to_timeline(["clip1.1"])
    v3 = pr3.items_on("video", 0)[0]; v3["reverse"] = True
    out = os.path.join(tmp, "rev.mp4")
    cmd, wd, dur = export.build_job(pr3, store, out)
    err = export.run_job(cmd, wd, dur, progress=lambda f: None); assert err is None, err
    def px(at):
        fr = subprocess.run([ff, "-v", "error", "-ss", str(at), "-i", out, "-frames:v", "1", "-f", "rawvideo",
                             "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        return np.frombuffer(fr, np.uint8).reshape(360, 640, 3).reshape(-1, 3).mean(0)
    p0, p1 = px(0.2), px(1.8)
    assert p0[2] > 150 > p0[0] and p1[0] > 150 > p1[2], (p0, p1)
    print("riproduzione inversa ok")


def test_export_options(tmp):
    """Modelli, capitoli, formato verticale che riempie il fotogramma e fps diversi."""
    ff = paths.ffmpeg_exe()
    assert export.preset_size(export.PRESET_BY_KEY["fullhd"], (1280, 720)) == (1920, 1080)
    assert export.preset_size(export.PRESET_BY_KEY["igreel"], (1920, 1080)) == (1080, 1920)
    assert export.preset_size(export.PRESET_BY_KEY["progetto"], (640, 360)) == (640, 360)
    pr = P.Project(); pr.add_source("/x/a.mp4", info(6)); pr.add_subclip("clip1", 0, 6)
    pr.append_to_timeline(["clip1.1"])
    assert export.chapters(pr) == [("Inizio", 0.0, 6.0)]
    pr.add_marker(2.0, "Uno"); pr.add_marker(4.0, "")
    ch = export.chapters(pr)
    assert [(c[0], round(c[1], 2), round(c[2], 2)) for c in ch] == [("Inizio", 0.0, 2.0), ("Uno", 2.0, 4.0),
                                                                    ("Capitolo 3", 4.0, 6.0)], ch
    assert export.safe_name('a/b:c?') == "a_b_c_"
    clip = os.path.join(tmp, "half.mp4")             # metà sinistra bianca, metà destra nera
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=black:s=640x360:r=25:d=2,drawbox=x=0:y=0:w=320:h=360:color=white:t=fill",
                    "-pix_fmt", "yuv420p", clip], check=True)
    pr = P.Project(); store = audio.AudioStore()
    s_ = pr.add_source(clip, info(2)); pr.add_subclip(s_["id"], 0, 2); pr.append_to_timeline(["clip1.1"])

    def frame(out, w, h, at=0.5):
        fr = subprocess.run([ff, "-v", "error", "-ss", str(at), "-i", out, "-frames:v", "1", "-f", "rawvideo",
                             "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        return np.frombuffer(fr, np.uint8).reshape(h, w, 3)
    for fill in (False, True):
        out = os.path.join(tmp, f"v{int(fill)}.mp4")
        cmd, wd, dur = export.build_job(pr, store, out, size=(360, 640), fps=30, fill=fill, start=0.0, end=1.0)
        err = export.run_job(cmd, wd, dur); assert err is None, err
        img = frame(out, 360, 640)
        top = img[:100].mean()                          # fascia in alto del formato verticale
        if fill:
            assert top > 60, top                        # l'immagine riempie: in alto c'è la metà bianca
        else:
            assert top < 10, top                        # bande nere sopra e sotto
        r = subprocess.run([ff, "-i", out], capture_output=True, text=True).stderr
        assert "30 fps" in r and "Duration: 00:00:01" in r, r
    print("opzioni di esportazione ok")


def test_archive(tmp):
    """Archivio del progetto: cartella autonoma con percorsi relativi."""
    import shutil
    from moviola import archive
    ff = paths.ffmpeg_exe()
    src_dir = os.path.join(tmp, "dischi", "SSD"); os.makedirs(src_dir)
    files = []
    for n in range(3):
        f = os.path.join(src_dir, f"c{n}.mp4")
        subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=red:s=160x90:r=25:d=1",
                        "-pix_fmt", "yuv420p", f], check=True)
        files.append(f)
    pr = P.Project()
    for f in files:
        pr.add_source(f, info(1))
    pr.add_subclip("clip1", 0, 1); pr.append_to_timeline(["clip1.1"])          # solo clip1 è in timeline
    proj_dir = os.path.join(tmp, "progetti"); os.makedirs(proj_dir)
    ppath = os.path.join(proj_dir, "Neve.moviola"); pr.save(ppath)
    dest = os.path.join(tmp, "archivi"); os.makedirs(dest)
    pl = archive.plan(ppath, "used"); assert sum(1 for e in pl["items"] if e["copy"]) == 1
    assert sum(1 for e in archive.plan(ppath, "all")["items"] if e["copy"]) == 3
    assert archive.plan(ppath, "project")["bytes"] == 0
    seen = []
    out = archive.run(ppath, dest, "used", progress=seen.append)
    assert os.path.isdir(out) and seen and seen[-1] == 1.0
    assert sorted(os.listdir(os.path.join(out, "media"))) == ["clip1_c0.mp4"], os.listdir(out)
    for f in ("Neve.moviola", "elenco-file.txt", "LEGGIMI.txt"):
        assert os.path.exists(os.path.join(out, f)), f
    # spostando l'archivio altrove si riapre da solo
    moved = os.path.join(tmp, "altro_disco"); shutil.move(out, moved)
    shutil.rmtree(os.path.join(tmp, "dischi"))                                  # spariscono gli originali
    chk = P.Project.load(os.path.join(moved, "Neve.moviola"))
    s1 = chk.source("clip1"); assert os.path.exists(s1["path"]) and s1["path"].startswith(moved), s1["path"]
    assert chk.data["archive"]["mode"] == "used" and chk.source("clip1")["name"] == "c0.mp4"
    assert not os.path.exists(chk.source("clip2")["path"])                      # non copiata: resta dov'era
    # solo progetto + zip
    out2 = archive.run(ppath, dest, "project", make_zip=True)
    assert out2.endswith(".zip") and os.path.isfile(out2) and not os.path.exists(out2[:-4])
    import zipfile
    assert any(n.endswith("Neve.moviola") for n in zipfile.ZipFile(out2).namelist())
    # annullamento: nessuna cartella a metà
    ev = threading.Event(); ev.set()
    try:
        archive.run(ppath, dest, "project", cancel=ev); raised = False
    except Exception:
        raised = True
    print("archivio ok")


def test_orient(tmp):
    """Specchio e rotazione (fissa e animata): anteprima ed esportazione concordano."""
    from moviola import orient
    ff = paths.ffmpeg_exe()
    clip = os.path.join(tmp, "blk.mp4")
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=black:s=640x360:r=25:d=2,drawbox=x=0:y=0:w=160:h=90:color=white:t=fill",
                    "-pix_fmt", "yuv420p", clip], check=True)

    def centroid(img):
        m = img.mean(axis=2) > 128
        ys, xs = np.nonzero(m)
        return xs.mean() / img.shape[1], ys.mean() / img.shape[0]

    base = np.zeros((360, 640, 3), np.uint8); base[:90, :160] = 255
    cx, cy = centroid(orient.apply(base, {"flip_h": True}))
    assert cx > 0.8 and cy < 0.2, (cx, cy)
    r = orient.apply(base, {"rotate": 90.0}); assert r.shape[:2] == (640, 360)
    cx, cy = centroid(r); assert cx > 0.6 and cy < 0.2, (cx, cy)     # orario: in alto a destra
    r = orient.apply(base, {"rotate": 30.0}); assert r.shape[0] > 360 and r.shape[1] > 640
    anim = {"rotate_anim": [[0.0, 0.0], [2.0, 90.0]]}
    assert orient.canvas(640, 360, anim) == orient.canvas(640, 360, anim)
    assert abs(orient.angle_at(anim, 1.0) - 45.0) < 1e-6

    def export_frame(item_props, at):
        pr = P.Project(); store = audio.AudioStore()
        s_ = pr.add_source(clip, info(2)); pr.add_subclip(s_["id"], 0, 2)
        pr.append_to_timeline(["clip1.1"])
        v = pr.items_on("video", 0)[0]; v.update(item_props)
        out = os.path.join(tmp, "o.mp4")
        cmd, wd, dur = export.build_job(pr, store, out)
        err = export.run_job(cmd, wd, dur, progress=lambda f: None); assert err is None, err
        fr = subprocess.run([ff, "-v", "error", "-ss", str(at), "-i", out, "-frames:v", "1", "-f", "rawvideo",
                             "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        return np.frombuffer(fr, np.uint8).reshape(360, 640, 3)
    cx, cy = centroid(export_frame({"flip_h": True}, 0.5)); assert cx > 0.8 and cy < 0.2, (cx, cy)
    cx, cy = centroid(export_frame({"rotate": 90.0}, 0.5)); assert cx > 0.55 and cy < 0.35, (cx, cy)
    img = export_frame({"rotate_anim": [[0.0, 0.0], [1.5, 90.0]]}, 0.04)
    cx, cy = centroid(img); assert cx < 0.4 and cy < 0.4, (cx, cy)      # all'inizio ancora dritta (tela fissa: più piccola)
    img = export_frame({"rotate_anim": [[0.0, 0.0], [1.0, 90.0]]}, 1.8)
    cx, cy = centroid(img); assert cx > 0.55 and cy < 0.4, (cx, cy)    # alla fine ruotata di 90°
    print("specchio e rotazione ok")


def test_update(tmp):
    import io, zipfile
    from moviola import update as U
    assert U.is_newer("0.12.1", "0.12.0") and U.is_newer("0.13", "0.12.9") and U.is_newer("1.0.0", "0.99.9")
    assert not U.is_newer("0.12.0", "0.12.0") and not U.is_newer("0.9.0", "0.10.0")
    root = os.path.join(tmp, "app"); os.makedirs(os.path.join(root, "moviola", "ui"))
    for rel, txt in (("moviola/__init__.py", '__version__ = "0.1.0"\n'), ("moviola/app.py", "vecchio"),
                     ("moviola/vecchio_modulo.py", "da togliere"), ("moviola/ui/vecchia.py", "da togliere"),
                     ("requirements.txt", "numpy\n"), ("mio_file.txt", "resta")):
        open(os.path.join(root, rel), "w").write(txt)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:      # come lo zip di GitHub: tutto dentro una cartella
        for rel, txt in (("moviola/__init__.py", '__version__ = "9.9.9"\n'), ("moviola/app.py", "nuovo"),
                         ("moviola/nuovo/modulo.py", "x"), ("requirements.txt", "numpy\ncertifi\n"),
                         ("avvia_mac.command", "#!/bin/bash\n")):
            z.writestr("topodondio-moviola-abc123/" + rel, txt)
    assert U.apply_zip(buf.getvalue(), root) == "9.9.9"
    rd = lambda rel: open(os.path.join(root, rel)).read()
    assert rd("moviola/app.py") == "nuovo" and rd("requirements.txt") == "numpy\ncertifi\n" and rd("mio_file.txt") == "resta"
    assert os.path.exists(os.path.join(root, "moviola", "nuovo", "modulo.py"))
    assert not os.path.exists(os.path.join(root, "moviola", "vecchio_modulo.py"))
    assert not os.path.exists(os.path.join(root, "moviola", "ui"))
    if os.name != "nt":
        assert os.access(os.path.join(root, "avvia_mac.command"), os.X_OK)
    for bad in (b"non uno zip", None):
        b2 = io.BytesIO()
        if bad is None:                        # zip senza Moviola dentro, o con percorsi che escono
            with zipfile.ZipFile(b2, "w") as z:
                z.writestr("x/../../fuori.txt", "no")
            bad = b2.getvalue()
        try:
            U.apply_zip(bad, root); raise AssertionError("doveva rifiutare")
        except (U.UpdateError, zipfile.BadZipFile):
            pass
    assert rd("moviola/app.py") == "nuovo" and not os.path.exists(os.path.join(tmp, "fuori.txt"))
    os.makedirs(os.path.join(root, ".git"))    # cartella di GitHub Desktop: non si tocca
    try:
        U.apply_zip(buf.getvalue(), root); raise AssertionError("doveva rifiutare")
    except U.UpdateError:
        pass
    print("aggiornamenti ok")


def main():
    test_model(); test_color(); test_ripple(); test_duplicate(); test_range_edit()
    with tempfile.TemporaryDirectory() as t:
        test_bins(t)
        test_audio_clean(t)
        test_export_options(t)
        test_archive(t)
        test_orient(t)
        test_reverse(t)
        test_speed_keep_length()
        test_update(t)
    with tempfile.TemporaryDirectory() as t:
        test_export(t)
        test_effects(t)
        test_curve_bars(t)
        test_box_music(t)
        test_transitions(t)
        test_color_anim(t)
        test_cache_and_paths(t)
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
