"""Test della stabilizzazione (servono PyAV e PySide6). Esegui: python tests/test_stab.py"""
import os, subprocess, sys, tempfile
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from moviola import project as P, audio, export, media, paths, stabilize as S

W, H, FPS, N = 640, 360, 25, 75


def texture(seed=3):
    """Immagine ricca di dettagli (rumore ammorbidito + rettangoli)."""
    rng = np.random.default_rng(seed)
    bh, bw = H + 240, W + 320
    f = np.fft.rfft2(rng.standard_normal((bh, bw)))
    ky = np.fft.fftfreq(bh)[:, None]; kx = np.fft.rfftfreq(bw)[None, :]
    g = np.fft.irfft2(f / (1e-3 + np.hypot(kx, ky)) ** 1.1, s=(bh, bw))
    g = (g - g.min()) / (g.max() - g.min())
    img = np.stack([g, np.roll(g, 37, 1), np.roll(g, 53, 0)], -1)
    for _ in range(40):
        x, y = rng.integers(0, bw - 60), rng.integers(0, bh - 40)
        img[y:y + rng.integers(8, 40), x:x + rng.integers(8, 60)] = rng.random(3)
    return (img * 255).astype(np.uint8)


def shake(seed=5):
    """Spostamenti (pixel interi) e rotazioni per fotogramma: panoramica lenta + tremolio."""
    rng = np.random.default_rng(seed)
    k = np.arange(N)
    sx = np.round(0.8 * k + rng.normal(0, 5, N)).astype(int)   # panoramica + vibrazione
    sy = np.round(8 * np.sin(k / 9) + rng.normal(0, 4, N)).astype(int)
    th = rng.normal(0, 0.008, N)
    return sx, sy, th


def make_shaky(path):
    big = texture(); sx, sy, th = shake()
    ff = paths.ffmpeg_exe()
    p = subprocess.Popen([ff, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                          "-r", str(FPS), "-i", "-", "-f", "lavfi", "-i", f"sine=frequency=440:duration={N / FPS}",
                          "-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path],
                         stdin=subprocess.PIPE)
    oy, ox = 120, 160
    for k in range(N):
        fr = np.ascontiguousarray(big[oy - sy[k]:oy - sy[k] + H, ox - sx[k]:ox - sx[k] + W])
        if th[k]:
            fr = S.warp(fr, 0, 0, th[k], 1.0)   # ruota il contenuto attorno al centro
        p.stdin.write(fr.tobytes())
    p.stdin.close(); p.wait()
    return sx, sy, th


def residual(path_arr):
    """Tremolio: scarto dei movimenti fotogramma-fotogramma tolta la tendenza lenta."""
    d = np.diff(path_arr, axis=0)
    d = d - S._smooth(d, 6)
    return d.std(axis=0)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        old = paths.media_cache_setting(); paths.set_media_cache(tmp)
        try:
            clip = os.path.join(tmp, "mosso.mp4")
            sx, sy, th = make_shaky(clip)
            assert S.track_for(clip) is None
            seen = []
            out = S.analyze(clip, progress=seen.append)
            assert out == S.stab_path(clip) and seen and seen[-1] == 1.0
            tr = S.track_for(clip)
            assert len(tr.t) == N and abs(tr.fps - FPS) < 0.01
            # 1) l'analisi ritrova spostamenti e rotazioni messi a mano
            ex = np.abs(tr.path[:, 0] * W - (sx - sx[0])); ey = np.abs(tr.path[:, 1] * H - (sy - sy[0]))
            eth = np.abs(np.diff(tr.path[:, 2]) - np.diff(th))
            print(f"analisi: errore x {ex.max():.2f}px  y {ey.max():.2f}px  rotazione {eth.max():.4f} rad")
            assert ex.max() < 2.0 and ey.max() < 2.0 and eth.max() < 0.004
            # 2) i bordi non si vedono mai: gli angoli dell'uscita cadono dentro l'immagine
            for strength, crop in ((0.5, 0.10), (1.0, 0.05), (1.0, 0.25), (0.0, 0.10)):
                c, z = tr.corrections(strength, crop)
                for dx, dy, a in c:
                    for px, py in ((0, 0), (W, 0), (0, H), (W, H)):
                        x = px - W / 2 - dx * z * W; y = py - H / 2 - dy * z * H
                        qx = (np.cos(a) * x + np.sin(a) * y) / z + W / 2
                        qy = (-np.sin(a) * x + np.cos(a) * y) / z + H / 2
                        assert -0.5 <= qx <= W + 0.5 and -0.5 <= qy <= H + 0.5, (strength, crop, qx, qy)
            # 3) anteprima: i fotogrammi corretti tremano molto meno
            import av
            def measure(frames):
                est = S._Estimator(frames[0].shape[0], frames[0].shape[1]); steps = [(0, 0, 0)]
                for a, b in zip(frames[:-1], frames[1:]):
                    steps.append(est.motion(a, b))
                return np.cumsum(np.array(steps), axis=0)
            raw, fixed = [], []
            from moviola.decode import FrameSource
            fs = FrameSource(clip)
            for k in range(N):
                img = np.ascontiguousarray(fs.get(k / FPS, H))
                assert abs(fs.last_time - k / FPS) < 1e-3
                raw.append(img.mean(2).astype(np.float32))
                fixed.append(S.warp(img, *tr.at(fs.last_time, 0.6, 0.12)).mean(2).astype(np.float32))
            r0, r1 = residual(measure(raw)), residual(measure(fixed))
            print(f"anteprima: tremolio prima {r0[0]:.2f},{r0[1]:.2f}px {r0[2]:.4f}rad  dopo {r1[0]:.2f},{r1[1]:.2f}px {r1[2]:.4f}rad")
            assert r1[0] < r0[0] * 0.2 and r1[1] < r0[1] * 0.2 and r1[2] < r0[2] * 0.35
            # 4) export: stesso risultato, anche con velocità cambiata e pezzo che parte a metà
            pr = P.Project()
            src = pr.add_source(clip, media.probe(clip))
            pr.add_subclip(src["id"], 0.4, 2.6)
            pr.append_to_timeline([src["id"] + ".1"])
            store = audio.AudioStore()
            raw_p = media.make_audio_cache(clip); store.register(src["id"], raw_p)
            def export_and_measure(name):
                out = os.path.join(tmp, name)
                cmd, wd, dur = export.build_job(pr, store, out, quality="alta")
                err = export.run_job(cmd, wd, dur)
                assert err is None, err
                frames = []
                with av.open(out) as c:
                    for f in c.decode(video=0):
                        frames.append(f.to_ndarray(format="gray").astype(np.float32))
                return residual(measure(frames)), len(frames)
            e0, n0 = export_and_measure("senza.mp4")
            v = [i for i in pr.items if i["kind"] == "video"][0]
            v["stab"] = True; v["stab_strength"] = 0.6; v["stab_crop"] = 0.12
            e1, n1 = export_and_measure("con.mp4")
            print(f"export: tremolio prima {e0[0]:.2f},{e0[1]:.2f}px {e0[2]:.4f}rad  dopo {e1[0]:.2f},{e1[1]:.2f}px {e1[2]:.4f}rad")
            assert n0 == n1 and e1[0] < e0[0] * 0.2 and e1[1] < e0[1] * 0.2 and e1[2] < e0[2] * 0.35
            pr.set_speed([v["id"]], 0.5)   # rallentato: i fotogrammi si ripetono, la correzione resta giusta
            v["zoom"] = 1.3; v["pos_x"] = 0.05
            e2, _ = export_and_measure("lento.mp4")
            print(f"export al 50% e zoom 130%: dopo {e2[0]:.2f},{e2[1]:.2f}px")
            assert e2[0] < e0[0] * 0.3 and e2[1] < e0[1] * 0.3
        finally:
            paths.set_media_cache(old)
    print("STABILIZZAZIONE OK")


if __name__ == "__main__":
    main()
