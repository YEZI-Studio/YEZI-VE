# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""CHROMA engine render CLI. Never overwrites; logs to logs/render_<cmd>_<time>.log.

  .venv/bin/python -m src.render status                                         timeline + footage cache
  .venv/bin/python -m src.render check [--fn M:F]                               no GPU: imports, @shot, Takes inside clips
  .venv/bin/python -m src.render warm [--fn src.shots.demo:demo]                decode every Take's window (cache/frames)
  .venv/bin/python -m src.render still --t 1.5 [--fn M:F] --out out/x.png      one frame (8-bit PNG, full res)
  .venv/bin/python -m src.render sheet --range 0 3 --step 0.1 --out out/x_sheet.jpg     contact sheet (time / frame / beat)
  .venv/bin/python -m src.render video --out out/x.mp4 [--range a b] [--audio a.wav --audio-start 0] [--jobs 1]
  .venv/bin/python -m src.render density out/x.mp4 [--ref other.mp4]           change-density metrics
  .venv/bin/python -m src.render bench --range 0 3 [--fn M:F]                   fps + peak memory
--fn module:function renders any shot function on its own (row 0..fn.length) instead of the timeline (src/timeline.py).
video: 1920x1080 60p H.264 High yuv420p ~28 Mbps, bt709 tv range (rgb48 pipe -> swscale accurate_rnd), + AAC 320k with --audio.
--jobs N > 1 renders N chunks in parallel processes (each a heavy process: only when the memory budget allows), then concats.
"""
import os, sys, math, time, argparse, subprocess, shutil, resource, importlib
import numpy as np
import cv2
from src import timeline as TL
from src import shots

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FF = os.path.expanduser('~/.local/bin/ffmpeg')
PY = sys.executable
COLOR = ['-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709', '-color_range', 'tv']
H264 = ['-c:v', 'libx264', '-preset', 'slow', '-profile:v', 'high', '-pix_fmt', 'yuv420p', '-b:v', '28M', '-maxrate', '34M',
        '-bufsize', '56M', '-x264-params', 'aq-mode=3:colorprim=bt709:transfer=bt709:colormatrix=bt709']
TOYUV = 'scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int,format=yuv420p'
_LOG = {'f': None}


def log(*a, quiet=False):
    s = ' '.join(str(x) for x in a)
    if _LOG['f']:
        _LOG['f'].write(s + '\n'); _LOG['f'].flush()
    if not quiet:
        print(s, flush=True)


def peak_mb():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / 2 ** 20 if sys.platform == 'darwin' else r / 1024


def no_overwrite(p):
    if os.path.exists(p):
        raise SystemExit(f'{p} exists -- never overwrite; pick a new name')


class Src:
    """frame -> (fn, row) for the timeline or for --fn"""

    def __init__(self, a):
        self.spec = getattr(a, 'fn', None)
        self.fixed = shots.load_fn(self.spec) if self.spec else None

    def __call__(self, F):
        return self.fixed if self.fixed else shots.resolve(F)

    def end(self):
        return self.fixed[1][3] if self.fixed else TL.END

    def modules(self):
        if self.fixed:
            return [self.spec.split(':')[0]]
        return sorted({f'src.shots.{r[1]}' for r in TL.SHOTS})


def frames_of(a, src):
    if getattr(a, 'range', None):
        f0, f1 = int(round(a.range[0] * TL.FPS)), int(round(a.range[1] * TL.FPS))
    else:
        f0, f1 = (0 if src.fixed else TL.START), src.end()       # v6: the film starts at code frame 72
    return f0, min(f1, src.end())


def engine():
    from src.engine import Engine
    return Engine(1)


# ------------------------------------------------------------------ commands
def cmd_status(a):
    TL.check()
    print(f'BPM {TL.BPM:g}  beat {TL.BEAT:.3f} f  end {TL.END} f = {TL.END / TL.FPS:.2f} s')
    for r in TL.SHOTS:
        print(f'  {r[0]:14s} {r[1]:10s} {r[2]:5d}-{r[3]:5d}  {r[4]}')
    subprocess.run([PY, '-m', 'src.footage', 'status'], cwd=ROOT)


def cmd_check(a):
    """no GPU needed (works inside the Claude Code sandbox): timeline contiguous, every shot module imports, every shot
    function exists and is @shot, every Take resolves to a catalog clip and stays inside it"""
    from src import footage
    bad = []
    try:
        TL.check()
    except AssertionError as e:
        bad.append(f'timeline: SHOTS not contiguous {e}')
    src = Src(a)
    for m in src.modules():
        try:
            importlib.import_module(m)
        except Exception as e:                                   # noqa: BLE001
            bad.append(f'{m}: import failed: {type(e).__name__}: {e}')
    if not src.fixed:
        for r in TL.SHOTS:
            try:
                fn, _ = shots.impl(r[0])
                if not getattr(fn, '_is_shot', False):
                    bad.append(f'{r[0]}: missing @shot decorator')
            except Exception as e:                               # noqa: BLE001
                bad.append(f'{r[0]}: {type(e).__name__}: {e}')
    for tk in footage.TAKES:
        try:
            n = footage.probe(tk.key)['n']
            raw = [tk.src_time(F) * (footage.probe(tk.key)['fps'] or 0) for F in range(int(tk.film[0]), int(tk.film[1]))]
            if raw and (min(raw) < -0.5 or max(raw) > n - 0.5):
                bad.append(f'{tk}: source frames {min(raw):.0f}..{max(raw):.0f} outside the clip (0..{n - 1}): freezes at the edge')
        except Exception as e:                                   # noqa: BLE001
            bad.append(f'{tk}: {type(e).__name__}: {e}')
    for x in bad:
        log('PROBLEM', x)
    log(f'check: {len(TL.SHOTS)} timeline rows, {len(footage.TAKES)} takes, {len(bad)} problems' + (' -- OK' if not bad else ''))
    if bad:
        raise SystemExit(1)


def cmd_warm(a):
    from src import footage
    src = Src(a)
    for m in src.modules():
        importlib.import_module(m)
    t0 = time.time()
    n = footage.warm()
    log(f'warm: {len(footage.TAKES)} takes, {n} frames written in {time.time() - t0:.1f}s')
    for tk in footage.TAKES:
        i0, i1 = tk.span()
        log(f'  {tk}  source frames {i0}-{i1 - 1} @{footage.tier_for(tk.key, tk.tier)}')


def cmd_still(a):
    src = Src(a)
    F = a.F if a.F is not None else int(round(a.t * TL.FPS))
    out = a.out or f'{ROOT}/out/engine/still_F{F:04d}.png'
    no_overwrite(out)
    E = engine()
    t0 = time.time()
    fn, row = src(F)
    img = E.frame_rgb(F, fn, row, 8, n_sub=a.sub)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    cv2.imwrite(out, img[..., ::-1])
    log(out, f'{time.time() - t0:.2f}s  peak {peak_mb():.0f} MB')


def compose_sheet(cells, cols, cw, ch, title, out):
    import skia
    from src.kit import type as TY
    pad, lab, top = 8, 24, 44
    n = len(cells); rows = math.ceil(n / cols)
    Wd, Hd = cols * (cw + pad) + pad, top + rows * (ch + lab + pad) + pad
    arr = np.empty((Hd, Wd, 4), np.uint8); arr[...] = (24, 24, 27, 255)
    for i, (im, _) in enumerate(cells):
        r, c = divmod(i, cols)
        x, y = pad + c * (cw + pad), top + r * (ch + lab + pad)
        arr[y:y + ch, x:x + cw, :3] = im
    cvs = skia.Surface(arr, colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kPremul_AlphaType).getCanvas()
    p = skia.Paint(AntiAlias=True); p.setColor(skia.ColorSetARGB(255, 235, 235, 235))
    cvs.drawString(title, pad, 28, TY.font('mono', 17.0), p)
    for i, (_, lbl) in enumerate(cells):
        r, c = divmod(i, cols)
        cvs.drawString(lbl, pad + c * (cw + pad), top + r * (ch + lab + pad) + ch + 17, TY.font('mono', 13.0), p)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    cv2.imwrite(out, np.ascontiguousarray(arr[..., :3])[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, 92])


def cmd_sheet(a):
    src = Src(a)
    no_overwrite(a.out)
    t = np.arange(a.range[0], a.range[1] + 1e-9, a.step)
    end = src.end()
    frames = sorted(set(min(end - 1, max(0, int(round(x * TL.FPS)))) for x in t))
    E = engine()
    ch = a.cell * 9 // 16
    cells, t0 = [], time.time()
    full_dir = os.path.splitext(a.out)[0] + '_full'
    for F in frames:
        fn, row = src(F)
        img = E.frame_rgb(F, fn, row, 8, max_sub=a.max_sub)
        bar, beat, six = TL.pos(F)
        cells.append((cv2.resize(img, (a.cell, ch), interpolation=cv2.INTER_AREA), f'{F / TL.FPS:5.2f}s F{F} {bar}.{beat}.{six}'))
    compose_sheet(cells, a.cols, a.cell, ch, a.title or f'CHROMA  {a.range[0]:.2f}-{a.range[1]:.2f}s  step {a.step}s  '
                  f'({src.spec or "timeline"})', a.out)
    for F in (a.full or []):                              # 100 % stills of chosen frames (any frame, not only the steps)
        os.makedirs(full_dir, exist_ok=True)
        fn, row = src(F)
        cv2.imwrite(f'{full_dir}/F{F:04d}.png', E.frame_rgb(F, fn, row, 8, max_sub=a.max_sub)[..., ::-1])
    log(a.out, f'{len(frames)} frames in {time.time() - t0:.1f}s  peak {peak_mb():.0f} MB' + (f'  full: {full_dir}' if a.full else ''))


# Output grade: lift / lift2 = brighter mids for phone screens, toe and top kept. 'lift' = monotone PCHIP curve in display space: toe kept (0.03 -> 0.032, blacks stay black), mids up
# (0.25 -> 0.35, 0.5 -> 0.64), top kept (1 -> 1). Applied to the final 16-bit frames only (env VOLT_GRADE, set by --grade).
# 'lift2': same toe (0.03 -> 0.032: blacks stay black), mids further
# up (0.16 -> 0.30, 0.30 -> 0.51, 0.5 -> 0.725), top kept (0.75 -> 0.89); with the look v4 light (fx.volt)
GRADES = {'lift': [(0, 0), (0.03, 0.032), (0.10, 0.14), (0.25, 0.35), (0.5, 0.64), (0.75, 0.87), (1, 1)],
          'lift2': [(0, 0), (0.03, 0.032), (0.08, 0.135), (0.16, 0.30), (0.30, 0.51), (0.50, 0.725), (0.75, 0.89), (1, 1)]}
_LUT16 = {}


def out16(E, F, fn, row):
    img = E.frame_rgb(F, fn, row, 16)
    g = os.environ.get('VOLT_GRADE')
    if not g:
        return img
    if g not in _LUT16:
        from scipy.interpolate import PchipInterpolator
        x, y = zip(*GRADES[g])
        _LUT16[g] = (np.clip(PchipInterpolator(x, y)(np.arange(65536) / 65535.0), 0, 1) * 65535.0 + 0.5).astype(np.uint16)
    return _LUT16[g][img]


def _encoder(out, extra_in=()):
    cmd = [FF, '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb48le', '-s', '1920x1080', '-r', str(TL.FPS), '-i', '-',
           *extra_in, '-vf', TOYUV, *H264, *COLOR, '-r', str(TL.FPS), '-movflags', '+faststart', out]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def cmd_chunk(a):
    """worker: frames [f0, f1) -> H.264 chunk (same settings as the final file, concat with -c copy)"""
    src = Src(a)
    E = engine()
    p = _encoder(a.out)
    t0 = time.time()
    for i, F in enumerate(range(a.f0, a.f1)):
        fn, row = src(F)
        p.stdin.write(out16(E, F, fn, row).tobytes())
        if i % 30 == 29 or F == a.f1 - 1:
            log(f'chunk {a.f0}-{a.f1}: {i + 1}/{a.f1 - a.f0}  {(i + 1) / (time.time() - t0):.2f} fps  peak {peak_mb():.0f} MB')
    p.stdin.close()
    if p.wait():
        raise SystemExit('ffmpeg failed')


def mux(video, audio, out, start, n_frames):
    subprocess.run([FF, '-y', '-v', 'error', '-i', video, '-ss', f'{start:.6f}', '-i', audio, '-map', '0:v', '-map', '1:a',
                    '-c:v', 'copy', '-c:a', 'aac', '-b:a', '320k', '-af', 'apad', '-t', f'{n_frames / TL.FPS:.6f}',
                    '-movflags', '+faststart', out], check=True)


def cmd_video(a):
    src = Src(a)
    out = os.path.abspath(a.out)
    no_overwrite(out)
    f0, f1 = frames_of(a, src)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = f'{ROOT}/out/tmp_{os.path.splitext(os.path.basename(out))[0]}_{os.getpid()}'
    os.makedirs(tmp, exist_ok=True)
    vid = f'{tmp}/video.mp4' if a.audio else out
    t0 = time.time()
    if a.jobs <= 1:
        E = engine()
        p = _encoder(vid)
        log(f'video {f0}-{f1} ({f1 - f0} f) -> {out}  render pid {os.getpid()} encoder pid {p.pid}')
        for i, F in enumerate(range(f0, f1)):
            fn, row = src(F)
            p.stdin.write(out16(E, F, fn, row).tobytes())
            if i % 30 == 29 or F == f1 - 1:
                st = E.footage.stats if E._store is not None else {}
                log(f'  {i + 1}/{f1 - f0}  {(i + 1) / (time.time() - t0):.2f} fps  peak {peak_mb():.0f} MB  {st}', quiet=True)
        p.stdin.close()
        if p.wait():
            raise SystemExit('ffmpeg failed')
    else:
        e = np.linspace(f0, f1, a.jobs + 1).round().astype(int)
        chunks = [(int(e[i]), int(e[i + 1])) for i in range(a.jobs) if e[i + 1] > e[i]]
        procs = []
        for i, (c0, c1) in enumerate(chunks):
            lf = open(f'{tmp}/c{i:03d}.log', 'w')
            procs.append(subprocess.Popen([PY, '-m', 'src.render', '_chunk', '--f0', str(c0), '--f1', str(c1), '--out',
                                           f'{tmp}/c{i:03d}.mp4'] + (['--fn', a.fn] if a.fn else []),
                                          cwd=ROOT, stdout=lf, stderr=subprocess.STDOUT))
        log('chunk PIDs', [p.pid for p in procs])
        while any(p.poll() is None for p in procs):
            time.sleep(2)
        if any(p.returncode for p in procs):
            raise SystemExit(f'chunks failed (see {tmp}/c*.log)')
        lst = f'{tmp}/list.txt'
        open(lst, 'w').write(''.join(f"file '{tmp}/c{i:03d}.mp4'\n" for i in range(len(chunks))))
        subprocess.run([FF, '-y', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', lst, '-c', 'copy', '-movflags', '+faststart',
                        vid], check=True)
    if a.audio:
        mux(vid, a.audio, out, a.audio_start, f1 - f0)
    shutil.rmtree(tmp, ignore_errors=True)
    log(f'DONE {out}  {f1 - f0} frames  {time.time() - t0:.1f}s  = {(f1 - f0) / (time.time() - t0):.2f} fps  peak {peak_mb():.0f} MB')


def _small(path, w=160, h=90):
    cmd = [FF, '-v', 'error', '-i', path, '-vf', f'scale={w}:{h}:flags=area,format=rgb24', '-f', 'rawvideo', '-']
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3).astype(np.float32) / 255.0


def density(path):
    v = _small(path)
    fps = TL.FPS
    d = np.abs(np.diff(v, axis=0)).mean(axis=(1, 2, 3))
    y = (v @ np.array([0.2126, 0.7152, 0.0722], np.float32)).mean(axis=(1, 2))
    dy = np.abs(np.diff(y))
    secs = len(v) / fps
    step = int(round(0.1 * fps))
    s = v[::step]
    lab = [cv2.cvtColor((x * 255).astype(np.uint8), cv2.COLOR_RGB2LAB).astype(np.float32) for x in s]
    dl = np.array([np.abs(b - a).mean() for a, b in zip(lab, lab[1:])])
    return dict(frames=len(v), secs=secs, big=float((d > 0.08).mean()), big_per_s=float((d > 0.08).sum() / secs),
                lum_jumps=int((dy > 0.15).sum()), lum_jumps_per_s=float((dy > 0.15).sum() / secs),
                states_per_s={t: float((dl > t).sum() / secs) for t in (8, 12, 16)}, mean_d=float(d.mean()),
                jump_frames=[int(i + 1) for i in np.nonzero(dy > 0.15)[0]])


def cmd_density(a):
    for name, p in (('ours', a.video), ('ref', a.ref)):
        if not p:
            continue
        r = density(p)
        log(f'{name}: {os.path.basename(p)}  {r["frames"]} f / {r["secs"]:.2f}s | big changes (160x90 mean|d| > 0.08): '
            f'{100 * r["big"]:.0f} % of frames = {r["big_per_s"]:.1f}/s | luma jumps > 0.15: {r["lum_jumps"]} '
            f'({r["lum_jumps_per_s"]:.1f}/s) | 0.1 s states dLab>8/12/16: ' + ' / '.join(f'{x:.1f}' for x in r['states_per_s'].values())
            + '/s')
        if name == 'ours':
            log(f'      luma-jump frames (frame n vs n-1): {r["jump_frames"]}')


def cmd_bench(a):
    src = Src(a)
    f0, f1 = frames_of(a, src)
    E = engine()
    fn, row = src(f0)
    E.frame_rgb(f0, fn, row, 16)                         # compile shaders, first reads
    t0 = time.time()
    for F in range(f0, f1):
        fn, row = src(F)
        E.frame_rgb(F, fn, row, 16)
    dt = time.time() - t0
    st = E.footage.stats if E._store is not None else {}
    log(f'bench {f0}-{f1}: {(f1 - f0) / dt:.2f} fps ({1000 * dt / (f1 - f0):.1f} ms/frame incl. 16-bit readback)  '
        f'peak {peak_mb():.0f} MB  footage {st}  gpu {E.stats}')


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)

    def common(s):
        s.add_argument('--fn'); return s
    common(sp.add_parser('status')).set_defaults(go=cmd_status)
    common(sp.add_parser('warm')).set_defaults(go=cmd_warm)
    common(sp.add_parser('check')).set_defaults(go=cmd_check)
    s = common(sp.add_parser('still')); s.add_argument('--t', type=float, default=0.0); s.add_argument('--F', type=int)
    s.add_argument('--out'); s.add_argument('--sub', type=int); s.set_defaults(go=cmd_still)
    s = common(sp.add_parser('sheet')); s.add_argument('--range', type=float, nargs=2, required=True)
    s.add_argument('--step', type=float, default=0.1); s.add_argument('--cols', type=int, default=6)
    s.add_argument('--cell', type=int, default=480); s.add_argument('--max-sub', type=int); s.add_argument('--out', required=True)
    s.add_argument('--title'); s.add_argument('--full', type=int, nargs='*', help='also save these frames at 100 %%')
    s.set_defaults(go=cmd_sheet)
    s = common(sp.add_parser('video')); s.add_argument('--out', required=True); s.add_argument('--range', type=float, nargs=2)
    s.add_argument('--audio'); s.add_argument('--audio-start', type=float, default=0.0); s.add_argument('--jobs', type=int, default=1)
    s.add_argument('--grade', choices=sorted(GRADES), help='final output curve (sets VOLT_GRADE for this run and its chunk workers)')
    s.set_defaults(go=cmd_video)
    s = common(sp.add_parser('_chunk')); s.add_argument('--f0', type=int, required=True); s.add_argument('--f1', type=int, required=True)
    s.add_argument('--out', required=True); s.set_defaults(go=cmd_chunk)
    s = sp.add_parser('density'); s.add_argument('video'); s.add_argument('--ref'); s.set_defaults(go=cmd_density)
    s = common(sp.add_parser('bench')); s.add_argument('--range', type=float, nargs=2); s.set_defaults(go=cmd_bench)
    a = ap.parse_args()
    os.makedirs(f'{ROOT}/logs', exist_ok=True)
    _LOG['f'] = open(f'{ROOT}/logs/render_{a.cmd.strip("_")}_{time.strftime("%m%d_%H%M%S")}_{os.getpid()}.log', 'w')
    log(' '.join(sys.argv), quiet=True)
    if getattr(a, 'grade', None):
        os.environ['VOLT_GRADE'] = a.grade          # inherited by --jobs chunk workers
    a.go(a)


if __name__ == '__main__':
    main()
