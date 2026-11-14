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


def main():
    test_model(); test_color(); test_ripple(); test_duplicate()
    with tempfile.TemporaryDirectory() as t:
        test_bins(t)
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
