# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""IGNITION footage layer: catalog-driven, window-cached 8-bit frames on the work disk (replaces CHROMA's u16 full caches).

  catalog   footage/meta/catalog.json (footage group, read-only here) + footage/test/catalog.json (engine tests). Entry format:
            ({file, code, source, id, ..., best_windows:[[in, out, "what"]], ...}); a list, or a dict
            with 'clips' / 'items' / 'footage', or {name: entry}. Name of a clip = file stem (A4_01_pexels-5846313); aliases:
            the code+number prefix (A4_01), entry['alias'], the source id (5846313) when unique.
  cache     cache/frames/<clip>/<tier>/<srcframe:06d>.jpg -- JPEG q95 4:4:4 (libjpeg-turbo), cover-cropped to the tier size:
            1080 = 1920x1080 (default), 1440 = 2560x1440, 2160 = 3840x2160 (for push-ins > 1.3x; never above the source).
            Only the windows the shots use are decoded (Take.span -> Store.ensure); a frame that is missing is decoded lazily
            (2 s around it) with a warning. Atomic writes (tmp + rename): several processes may share the cache.
  colour    ffmpeg decodes with the stream's own matrix/range (untagged HD -> bt709, SD -> bt601), lanczos + full chroma
            to the tier; PQ / HLG sources are tone-mapped (zscale linear -> hable -> bt709). Plates are display-referred
            Rec.709, exactly what the old engine composited in.
  time      Take(clip, film=(f0, f1), src=sec, speed=...) maps FILM frames (60 fps) to SOURCE frames: constant rate, rate
            keyframes (ramps, pre-impact slow-down, freeze = 0, reverse < 0), loop; E.clip(take, T.F) does the rest
            ('nearest' or 'blend' of the two neighbours).
CLI:  python -m src.footage list | probe NAME | warm NAME IN OUT [--tier] | status | bench NAME [--tier --n]
"""
import os, sys, json, math, time, threading, subprocess, argparse
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOGS = [f'{ROOT}/footage/meta/catalog.json', f'{ROOT}/footage/test/catalog.json']
CACHE = f'{ROOT}/cache/frames'
FFMPEG = os.path.expanduser('~/.local/bin/ffmpeg')
FFPROBE = os.path.expanduser('~/.local/bin/ffprobe')
TIERS = {1080: (1920, 1080), 1440: (2560, 1440), 2160: (3840, 2160)}
JPEG_Q = 95
FILM_FPS = 60
LAZY_SEC = 2.0
IMG_EXT = ('.jpg', '.jpeg', '.png', '.tif', '.tiff', '.webp', '.bmp', '.exr')
_RGB = getattr(cv2, 'IMREAD_COLOR_RGB', None)       # OpenCV >= 4.10: libjpeg-turbo decodes straight to RGB


def log(*a):
    print('[footage]', *a, file=sys.stderr, flush=True)


# ------------------------------------------------------------------ catalog
_cat = {'mt': None, 'clips': {}, 'alias': {}}
_EXTRA = {}                                          # clips registered at run time (SR.element sources, see register)


def register(name, path, **meta):
    """add a clip that is not in the catalog files (an element source the motif's overlays.json names by file) -> name.
    Same entry format as the catalog; kept for the life of the process, merged after the catalog files."""
    path = os.path.abspath(path)
    if name not in _EXTRA or _EXTRA[name]['path'] != path:
        _EXTRA[name] = dict(meta, file=path, path=path, name=name)
        _cat['mt'] = None
    return name


def _entries(data):
    if isinstance(data, list):
        return data
    for k in ('clips', 'items', 'footage', 'entries'):
        if isinstance(data.get(k), list):
            return data[k]
    return [dict(v, **({'name': k} if isinstance(v, dict) and 'name' not in v else {})) for k, v in data.items()
            if isinstance(v, dict) and 'file' in v]


def _abs(entry, catdir):
    f = entry.get('path') or entry['file']
    cands = [f] if os.path.isabs(f) else [os.path.join(ROOT, f), os.path.join(catdir, f)]
    cands += [os.path.join(ROOT, 'footage', 'raw', os.path.basename(f)), os.path.join(ROOT, 'footage', 'test', os.path.basename(f))]
    for c in cands:
        if os.path.exists(c):
            return os.path.abspath(c)
    return os.path.abspath(cands[0])


def catalog():
    """{name: entry} merged from CATALOGS (reloaded when a catalog file changes); entry['path'] is absolute"""
    mt = tuple(os.path.getmtime(p) if os.path.exists(p) else 0 for p in CATALOGS) + (len(_EXTRA),)
    if mt != _cat['mt']:
        clips, alias, ids = {}, {}, {}
        for p in CATALOGS:
            if not os.path.exists(p):
                continue
            for e in _entries(json.load(open(p))):
                e = dict(e)
                e['path'] = _abs(e, os.path.dirname(p))
                name = e.get('name') or os.path.splitext(os.path.basename(e['file']))[0]
                e['name'] = name
                clips[name] = e
                parts = name.split('_')
                if len(parts) >= 2 and parts[1].isdigit():
                    alias.setdefault('_'.join(parts[:2]), name)
                if e.get('alias'):
                    alias[e['alias']] = name
                if e.get('id') is not None:
                    ids.setdefault(str(e['id']), []).append(name)
        for k, e in _EXTRA.items():
            clips.setdefault(k, dict(e))
        for i, names in ids.items():
            if len(names) == 1:
                alias.setdefault(i, names[0])
        codes = {}
        for k, e in clips.items():
            codes.setdefault(str(e.get('code', '')), []).append(k)
        for c, names in codes.items():
            if c and len(names) == 1:
                alias.setdefault(c, names[0])
        _cat.update(mt=mt, clips=clips, alias=alias)
    return _cat['clips']


def resolve(name):
    """catalog name or alias -> catalog name"""
    cl = catalog()
    if name in cl:
        return name
    if name in _cat['alias']:
        return _cat['alias'][name]
    near = [k for k in cl if str(name).lower() in k.lower()]
    raise KeyError(f'footage {name!r} not in {CATALOGS}' + (f' (did you mean {near[:5]})' if near else ''))


def entry(name):
    return catalog()[resolve(name)]


# ------------------------------------------------------------------ probe
def _rate(s):
    if not s or s in ('0/0', 'N/A'):
        return None
    a, _, b = s.partition('/')
    return float(a) / float(b or 1) if float(b or 1) else None


def probe(name):
    """stream facts for a clip (cached in cache/frames/<name>/probe.json, refreshed when the file changes):
    w, h (display, after rotation), fps, rate ('num/den'), n frames, dur, cfr, pix_fmt, space, trc, prim, range, rot, image"""
    key = resolve(name)
    path = entry(key)['path']
    st = os.stat(path)
    pj = f'{CACHE}/{key}/probe.json'
    if os.path.exists(pj):
        d = json.load(open(pj))
        if d.get('_sig') == [st.st_size, int(st.st_mtime)]:
            return d
    if path.lower().endswith(IMG_EXT):
        im = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        d = dict(w=int(im.shape[1]), h=int(im.shape[0]), fps=0.0, rate='0/1', n=1, dur=0.0, cfr=True, pix_fmt=str(im.dtype),
                 space='', trc='', prim='', range='pc', rot=0, image=True)
    else:
        out = subprocess.run([FFPROBE, '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                              'stream=width,height,r_frame_rate,avg_frame_rate,nb_frames,pix_fmt,color_space,color_transfer,'
                              'color_primaries,color_range,duration:stream_side_data=rotation:format=duration',
                              '-of', 'json', path], capture_output=True, text=True, check=True).stdout
        j = json.loads(out)
        s = j['streams'][0]
        rot = 0
        for sd in s.get('side_data_list', []) or []:
            if 'rotation' in sd:
                rot = int(round(float(sd['rotation'])))
        r, a = _rate(s.get('r_frame_rate')), _rate(s.get('avg_frame_rate'))
        fps = a or r
        dur = float(s.get('duration') or j.get('format', {}).get('duration') or 0)
        n = int(s['nb_frames']) if str(s.get('nb_frames', '')).isdigit() else int(math.floor(dur * fps + 0.5))
        w, h = int(s['width']), int(s['height'])
        if abs(rot) % 180 == 90:
            w, h = h, w
        d = dict(w=w, h=h, fps=fps, rate=s.get('avg_frame_rate') if a else s.get('r_frame_rate'), n=n, dur=dur,
                 cfr=bool(r and a and abs(r - a) < 1e-3), pix_fmt=s.get('pix_fmt', ''), space=s.get('color_space', ''),
                 trc=s.get('color_transfer', ''), prim=s.get('color_primaries', ''), range=s.get('color_range', ''), rot=rot,
                 image=False)
        if w < h:
            log(f'{key}: portrait {w}x{h} -- cover-crop to 16:9 throws away {100 * (1 - (w * 9 / 16) / h):.0f} % of the height')
    d['_sig'] = [st.st_size, int(st.st_mtime)]
    os.makedirs(os.path.dirname(pj), exist_ok=True)
    json.dump(d, open(pj, 'w'), indent=1)
    return d


def tier_for(name, tier=1080):
    """largest cache tier <= the requested one that the source can fill without upscaling (1080 is always allowed)"""
    p = probe(name)
    ok = [t for t in sorted(TIERS) if t <= max(1080, int(tier)) and (t == 1080 or min(p['h'], p['w'] * 9 // 16) >= t)]
    return ok[-1]


def frame_path(name, tier, i):
    return f'{CACHE}/{name}/{tier}/{int(i):06d}.jpg'


# ------------------------------------------------------------------ decode -> JPEG cache
def _chain(p, tw, th):
    """ffmpeg filter chain: decoded stream -> display-referred bt709 full-range BGR24, cover-cropped to tw x th"""
    sc = (f'scale={tw}:{th}:force_original_aspect_ratio=increase:flags=lanczos+accurate_rnd+full_chroma_int'
          f'{{cm}}:out_range=pc,crop={tw}:{th},format=bgr24')
    trc = (p.get('trc') or '').lower()
    if trc in ('smpte2084', 'arib-std-b67'):                 # HDR -> SDR (as CHROMA did for Tokyo); untested in this film
        pre = ('zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,'
               'zscale=t=bt709:m=bt709:r=tv,format=yuv444p,')
        return pre + sc.format(cm=':in_color_matrix=bt709:in_range=tv')
    space = (p.get('space') or '').lower()
    if space in ('bt709',):
        cm = 'bt709'
    elif space in ('smpte170m', 'bt470bg', 'fcc'):
        cm = 'bt601'
    elif space.startswith('bt2020'):
        cm = 'bt2020'
    else:
        cm = 'bt709' if p['h'] >= 720 else 'bt601'
    rng = 'pc' if (p.get('range') or '').lower() in ('pc', 'jpeg') or (p.get('pix_fmt') or '').startswith('yuvj') else 'tv'
    if (p.get('pix_fmt') or '').startswith(('rgb', 'bgr', 'gbr')):
        return sc.format(cm='')
    return sc.format(cm=f':in_color_matrix={cm}:in_range={rng}')


def _jpeg(img):
    ok, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q, cv2.IMWRITE_JPEG_SAMPLING_FACTOR,
                                         cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444])
    assert ok
    return buf


def _put(path, img):
    tmp = f'{path}.{os.getpid()}.{threading.get_ident()}.tmp'
    _jpeg(img).tofile(tmp)
    os.replace(tmp, path)


def decode_window(name, i0, i1, tier, pool=None, quiet=False):
    """decode SOURCE frames [i0, i1) of a clip at `tier` into the cache (overwrites nothing that exists). Returns #written."""
    key = resolve(name)
    p = probe(key)
    tw, th = TIERS[tier]
    d = f'{CACHE}/{key}/{tier}'
    os.makedirs(d, exist_ok=True)
    i0, i1 = max(0, int(i0)), min(p['n'], int(i1))
    if i1 <= i0:
        return 0
    t0 = time.time()
    src = entry(key)['path']
    if p.get('image'):
        im = cv2.imread(src, cv2.IMREAD_COLOR)
        s = max(tw / im.shape[1], th / im.shape[0])
        im = cv2.resize(im, (max(tw, round(im.shape[1] * s)), max(th, round(im.shape[0] * s))),
                        interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LANCZOS4)
        y, x = (im.shape[0] - th) // 2, (im.shape[1] - tw) // 2
        _put(frame_path(key, tier, 0), np.ascontiguousarray(im[y:y + th, x:x + tw]))
        return 1
    cmd = [FFMPEG, '-v', 'error', '-nostdin']
    if i0 > 0:
        cmd += ['-ss', f'{(i0 - 0.5) / p["fps"]:.6f}']
    vf = _chain(p, tw, th)
    if not p.get('cfr', True):
        vf = f'fps={p["rate"]}:round=near,' + vf
    cmd += ['-i', src, '-map', '0:v:0', '-an', '-sn', '-dn', '-vf', vf, '-fps_mode', 'passthrough' if p.get('cfr', True) else 'cfr',
            '-frames:v', str(i1 - i0), '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-']
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=0)
    own = pool is None
    pool = pool or ThreadPoolExecutor(4)
    nb, futs, k = tw * th * 3, [], 0
    try:
        while k < i1 - i0:
            buf = bytearray(nb); mv = memoryview(buf); got = 0
            while got < nb:
                r = proc.stdout.readinto(mv[got:])
                if not r:
                    break
                got += r
            if got < nb:
                break
            path = frame_path(key, tier, i0 + k)
            if not os.path.exists(path):
                futs.append(pool.submit(_put, path, np.frombuffer(buf, np.uint8).reshape(th, tw, 3)))
            k += 1
        for fu in futs:
            fu.result()
    finally:
        proc.stdout.close()
        proc.wait()
        if own:
            pool.shutdown()
    if k < i1 - i0:
        log(f'{key}: got {k} of {i1 - i0} frames from {i0} (end of stream?)')
    if not quiet:
        log(f'{key} @{tier}: frames {i0}-{i0 + k - 1} cached ({len(futs)} new) in {time.time() - t0:.1f}s')
    return len(futs)


# ------------------------------------------------------------------ per-process frame store (RAM LRU + prefetch)
class Store:
    """get(name, i, tier) -> uint8 (h, w, 3) RGB plate of SOURCE frame i (decoded JPEG; RAM LRU; read-ahead on
    sequential access; missing windows are decoded lazily). One per Engine."""

    def __init__(self, ram_mb=450, threads=4, ahead=6):
        self.lru, self.bytes, self.cap = OrderedDict(), 0, ram_mb * 2 ** 20
        self.pool = ThreadPoolExecutor(threads)
        self.pending, self.have, self.last = {}, {}, {}
        self.ahead = ahead
        self.lock = threading.Lock()
        self.stats = {'hit': 0, 'read': 0, 'wait': 0, 'lazy': 0, 'read_ms': 0.0}

    def n(self, name):
        return probe(name)['n']

    def tier_for(self, name, tier):
        return tier_for(name, tier)

    def _has(self, key, tier, i):
        s = self.have.get((key, tier))
        if s is None:
            d = f'{CACHE}/{key}/{tier}'
            s = self.have[(key, tier)] = {int(f[:6]) for f in os.listdir(d) if f.endswith('.jpg')} if os.path.isdir(d) else set()
        if i in s:
            return True
        if os.path.exists(frame_path(key, tier, i)):          # written by another process
            s.add(i); return True
        return False

    @staticmethod
    def _read(path):
        buf = np.fromfile(path, np.uint8)
        if _RGB is not None:
            return cv2.imdecode(buf, _RGB)
        return cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)

    def _keep(self, k, arr):
        with self.lock:
            if k in self.lru:
                return
            self.lru[k] = arr; self.bytes += arr.nbytes
            while self.bytes > self.cap and len(self.lru) > 2:
                _, old = self.lru.popitem(last=False); self.bytes -= old.nbytes

    def _load(self, k):
        key, tier, i = k
        arr = self._read(frame_path(key, tier, i))
        self._keep(k, arr)
        return arr

    def ensure(self, name, i0, i1, tier):
        key = resolve(name)
        tier = tier_for(key, tier)
        i0, i1 = max(0, int(i0)), min(self.n(key), int(i1))
        miss = [i for i in range(i0, i1) if not self._has(key, tier, i)]
        if not miss:
            return 0
        runs, a = [], miss[0]
        for x, y in zip(miss, miss[1:] + [None]):
            if y != x + 1:
                runs.append((a, x + 1)); a = y
        wrote = 0
        for a, b in runs:
            wrote += decode_window(key, a, b, tier, pool=self.pool)
        self.have.pop((key, tier), None)
        return wrote

    def get(self, name, i, tier=1080):
        key = resolve(name)
        k = (key, tier, int(i))
        with self.lock:
            arr = self.lru.get(k)
            if arr is not None:
                self.lru.move_to_end(k)
        if arr is not None:
            self.stats['hit'] += 1
            return arr
        fu = self.pending.pop(k, None)
        if fu is not None:
            self.stats['wait'] += 1
            return fu.result()
        if not self._has(key, tier, k[2]):
            fps = probe(key)['fps'] or 1.0
            log(f'LAZY decode {key} @{tier} around frame {k[2]} -- declare the Take / run `render warm` to batch this')
            self.stats['lazy'] += 1
            self.ensure(key, k[2] - 4, k[2] + int(LAZY_SEC * fps), tier)
        t0 = time.perf_counter()
        arr = self._load(k)
        self.stats['read'] += 1; self.stats['read_ms'] += (time.perf_counter() - t0) * 1e3
        return arr

    def hint(self, name, i, tier):
        """called by E.clip with every request: on (near-)sequential access, read the next frames ahead in threads"""
        key = resolve(name)
        last = self.last.get((key, tier))
        self.last[(key, tier)] = i
        if last is None or i == last or abs(i - last) > 3:
            return
        step = 1 if i > last else -1
        n = self.n(key)
        for j in range(i + step, i + step * (self.ahead + 1), step):
            k = (key, tier, j)
            if not (0 <= j < n) or k in self.lru or k in self.pending or not self._has(key, tier, j):
                continue
            self.pending[k] = self.pool.submit(self._load, k)
        if len(self.pending) > 64:                        # forget stale read-aheads (they are in the LRU anyway)
            for kk in [kk for kk, fu in self.pending.items() if fu.done()]:
                self.pending.pop(kk, None)

    def close(self):
        self.pool.shutdown(wait=True)


# ------------------------------------------------------------------ time mapping
TAKES = []


class Take:
    """one use of a clip in the film: maps FILM frames to SOURCE frames.
    film   (f0, f1) film frames the take covers (end exclusive) -- what `render warm` decodes
    src    source second shown at film frame `at` (default f0)
    speed  1 = real time, 0.5 = half (slow motion), 0 = freeze, < 0 = reverse; or keyframes [(film_frame, rate), ...]
           (piecewise linear in film frames, constant outside) -> ramps, the pre-impact slow-down, speed-ups
    tier   1080 | 1440 | 2160 (push-ins > 1.3x);  interp 'nearest' | 'blend';  loop False | 'wrap' | 'pingpong'
    Use: E.clip(take, T.F)  (T.F, the integer frame: sub-frames must not smear the plate)."""

    def __init__(self, clip, film, src=0.0, speed=1.0, at=None, tier=1080, interp='nearest', loop=False, name=None):
        self.key = resolve(clip)
        self.film = (float(film[0]), float(film[1]))
        self.src, self.speed, self.at = float(src), speed, float(film[0] if at is None else at)
        self.tier, self.interp, self.loop, self.name = int(tier), interp, loop, name or self.key
        TAKES.append(self)

    def _int(self, f):
        if isinstance(self.speed, (int, float)):
            return self.speed * (f - self.at)
        from src.kit.anim import integrate
        return integrate(f, self.speed) - integrate(self.at, self.speed)

    def src_time(self, f):
        return self.src + self._int(f) / FILM_FPS

    def src_frame(self, f):
        p = probe(self.key)
        n = p['n']
        x = self.src_time(f) * (p['fps'] or 0.0)
        if self.loop == 'wrap':
            x = x % n
        elif self.loop == 'pingpong':
            x = x % (2 * n - 2) if n > 1 else 0.0
            x = 2 * n - 2 - x if x > n - 1 else x
        return min(max(x, 0.0), n - 1.0)

    def span(self, pad=1):
        """(i0, i1) source frames needed over the take's film range (end exclusive)"""
        fr = [self.src_frame(F) for F in range(int(math.floor(self.film[0])), int(math.ceil(self.film[1])))]
        if not fr:
            fr = [self.src_frame(self.film[0])]
        return max(0, int(math.floor(min(fr))) - pad), min(probe(self.key)['n'], int(math.ceil(max(fr))) + 1 + pad)

    def warm(self, store=None):
        st = store or Store()
        i0, i1 = self.span()
        return st.ensure(self.key, i0, i1, self.tier)

    def __repr__(self):
        return f'Take({self.name} film {self.film[0]:g}-{self.film[1]:g} src {self.src:g}s tier {self.tier})'


def warm(takes=None, store=None):
    """decode every registered take's window (one ffmpeg at a time). -> frames written"""
    st = store or Store()
    total = 0
    for tk in (takes if takes is not None else TAKES):
        total += tk.warm(st)
    return total


# ------------------------------------------------------------------ CLI
def _du(path):
    tot = 0
    for dp, _, fs in os.walk(path):
        tot += sum(os.path.getsize(os.path.join(dp, f)) for f in fs)
    return tot


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)
    sp.add_parser('list')
    s = sp.add_parser('probe'); s.add_argument('name')
    s = sp.add_parser('warm'); s.add_argument('name'); s.add_argument('t_in', type=float); s.add_argument('t_out', type=float)
    s.add_argument('--tier', type=int, default=1080)
    sp.add_parser('status')
    s = sp.add_parser('bench'); s.add_argument('name'); s.add_argument('--tier', type=int, default=1080); s.add_argument('--n', type=int, default=40)
    a = ap.parse_args()
    if a.cmd == 'list':
        for k, e in sorted(catalog().items()):
            print(f'{k:44s} {e.get("code", ""):5s} {os.path.exists(e["path"])!s:5s} {e.get("width", "?")}x{e.get("height", "?")} '
                  f'{e.get("fps", "?")} fps  windows {e.get("best_windows", [])}')
    elif a.cmd == 'probe':
        print(json.dumps(probe(a.name), indent=1))
    elif a.cmd == 'warm':
        p = probe(a.name)
        Store().ensure(a.name, int(a.t_in * p['fps']), int(math.ceil(a.t_out * p['fps'])) + 1, a.tier)
    elif a.cmd == 'status':
        tot = 0
        for k in sorted(os.listdir(CACHE)) if os.path.isdir(CACHE) else []:
            for t in sorted(TIERS):
                d = f'{CACHE}/{k}/{t}'
                if os.path.isdir(d):
                    fs = [f for f in os.listdir(d) if f.endswith('.jpg')]
                    b = _du(d); tot += b
                    print(f'{k:44s} {t:5d}  {len(fs):5d} frames  {b / 2 ** 20:8.1f} MB  {b / max(1, len(fs)) / 2 ** 10:6.0f} KB/frame')
        print(f'total {tot / 2 ** 30:.2f} GB in {CACHE}')
    elif a.cmd == 'bench':
        import fcntl, random
        key = resolve(a.name); tier = tier_for(key, a.tier)
        d = f'{CACHE}/{key}/{tier}'
        fs = sorted(f for f in os.listdir(d) if f.endswith('.jpg'))
        random.seed(1); pick = random.sample(fs, min(a.n, len(fs)))
        cold, warm_, dec = [], [], []
        for f in pick:                                    # F_NOCACHE: read straight from the drive (cold), then decode
            t0 = time.perf_counter()
            fd = os.open(f'{d}/{f}', os.O_RDONLY); fcntl.fcntl(fd, fcntl.F_NOCACHE, 1)
            data = b''
            while True:
                ch = os.read(fd, 1 << 22)
                if not ch:
                    break
                data += ch
            os.close(fd)
            t1 = time.perf_counter()
            im = cv2.imdecode(np.frombuffer(data, np.uint8), _RGB if _RGB is not None else cv2.IMREAD_COLOR)
            t2 = time.perf_counter()
            cold.append((t2 - t0) * 1e3); dec.append((t2 - t1) * 1e3)
        for f in pick:
            t0 = time.perf_counter(); Store._read(f'{d}/{f}'); warm_.append((time.perf_counter() - t0) * 1e3)
        sz = np.mean([os.path.getsize(f'{d}/{f}') for f in pick]) / 2 ** 10
        print(f'{key} @{tier} {im.shape[1]}x{im.shape[0]}: {len(pick)} random frames, {sz:.0f} KB avg | uncached read+decode '
              f'median {np.median(cold):.1f} ms (p90 {np.percentile(cold, 90):.1f}) | decode only {np.median(dec):.1f} ms | '
              f'page-cached read+decode median {np.median(warm_):.1f} ms (p90 {np.percentile(warm_, 90):.1f})')


if __name__ == '__main__':
    main()
