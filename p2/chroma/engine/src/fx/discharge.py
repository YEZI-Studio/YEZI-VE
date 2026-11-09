# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""Branching discharge: lightning / Lichtenberg figures (signature element: DROP, final hit, big
impacts, letters being powered).

GEOMETRY  the dielectric breakdown model (Niemeyer, Pietronero & Wiesmann 1984) -- the physics of how a discharge branches
          -- with the fast rational-potential update of Kim, Sewall, Mahajan & Lin 2007 ("Fast simulation of Laplacian
          growth"): every channel cell is a charge, a candidate cell's potential is sum_j (1 - R1/r_ij), growth picks a
          candidate with probability ~ phi^eta (eta 1 = dense Lichtenberg tree, 2-3 = lightning). A uniform field (bolts:
          cloud -> ground) or a target (arcs) steers it. The tree is then analysed like a real channel: the current through a
          segment = the size of the sub-tree it feeds -> trunk thick and bright, branches thinner and dimmer, tips finest;
          the path that reached the ground is the return-stroke channel. Grid steps are removed (jitter + smoothing +
          perpendicular midpoint detail), so it never reads as a grid or a vector drawing.
          Deterministic (seed), cached in RAM and on disk (cache/discharge/<hash>.npz).
LIGHT     drawn as LIGHT, not as lines: each segment is a Gaussian cross-section of HDR intensity (max-blended, no hot joints),
          then a 6-radius point-spread glow (2 .. 160 px), tone-mapped v = 1 - exp(-E * expo) through the 'volt_hi' ramp and
          ADDED in linear light: the core over-exposes to ARC_WHITE, the halo walks ICE -> ELECTRIC -> VIOLET as it falls
          off, faint branches stay violet; the flash also lights the world: illum = strength of the 闪光补光 flash fill
          (fx.volt.flash on the raw light: wide blurs 40/110/320 px, soft cap; 1.0 = DROP strength; it dies with the bolt).
TIME      t < 0: the stepped leader grows (birth order of the cells; dim channel, bright tips) over `grow` frames;
          t = 0: return stroke (main channel x main_k, wider for 2 frames); t > 0: branches die fast (tau_b), the main
          channel slower (tau_m) with re-strokes (restrike frames) -- real lightning flickers.

    D = DC.Discharge('bolt', seed=3, p0=(900, -30), p1=(1010, 1080), cell=7)       # cloud -> ground
    D = DC.Discharge('radial', seed=5, p0=(960, 540), r=900)                     # Lichtenberg burst from a point
    D = DC.Discharge('arc', seed=8, p0=(400, 200), p1=(1300, 700))               # point -> point
    img = D.draw(E, img, t)          # t = film frames since the stroke (float; < 0 while it grows)
    light = D.light(E, t)            # just the HDR light (R), e.g. to invert for 'neg' impact frames
"""
import os, math, hashlib
import numpy as np
import moderngl
from src.kit.core import run, ramp_tex
from src.fx import volt as VT  # noqa: F401  (registers the volt ramps)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE = f'{ROOT}/cache/discharge'
N8 = ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1))
GEO_VERSION = 6


# ------------------------------------------------------------------ dielectric breakdown growth (Laplace)
class _Laplace:
    """phi on a gw x gh grid, red-black SOR with warm start; fixed cells (channel = 0, ground / target = 1) re-imposed after
    every half sweep; Neumann (zero-gradient) outer border."""

    def __init__(self, gw, gh, init):
        self.gw, self.gh = gw, gh
        self.pp = np.zeros((gh + 2, gw + 2), np.float32)
        self.phi = self.pp[1:-1, 1:-1]
        self.phi[:] = init
        self.fy, self.fx, self.fv = [], [], []
        self._f = None

    def fix(self, ys, xs, v):
        self.fy += list(np.atleast_1d(ys)); self.fx += list(np.atleast_1d(xs)); self.fv += [float(v)] * len(np.atleast_1d(ys))
        self._f = None

    def _impose(self):
        if self._f is None:
            self._f = (np.array(self.fy, np.int64), np.array(self.fx, np.int64), np.array(self.fv, np.float32))
        fy, fx, fv = self._f
        self.phi[fy, fx] = fv
        pp = self.pp
        pp[0, :] = pp[1, :]; pp[-1, :] = pp[-2, :]; pp[:, 0] = pp[:, 1]; pp[:, -1] = pp[:, -2]

    def relax(self, k, omega=1.85):
        H, W, pp = self.gh, self.gw, self.pp
        self._impose()
        for _ in range(k):
            for lat in (((0, 0), (1, 1)), ((0, 1), (1, 0))):
                for r0, c0 in lat:
                    c = pp[1 + r0:1 + H:2, 1 + c0:1 + W:2]
                    avg = 0.25 * (pp[r0:H:2, 1 + c0:1 + W:2] + pp[2 + r0:2 + H:2, 1 + c0:1 + W:2]
                                  + pp[1 + r0:1 + H:2, c0:W:2] + pp[1 + r0:1 + H:2, 2 + c0:2 + W:2])
                    c += omega * (avg - c)
                self._impose()


def dbm(gw, gh, start, eta=2.0, n_max=3000, seed=0, mode='bolt', target=None, r_max=None, sweeps=12):
    """dielectric breakdown growth on a gw x gh cell grid from `start` (x, y). mode 'bolt': ground = bottom row (phi 1);
    'radial': phi 1 on a circle of radius min(gw, gh)/2 - 1; 'arc': phi 1 on a small disc around `target`.
    Growth picks a candidate (8-neighbour of the channel) with probability ~ phi^eta (the potential from a real Laplace
    solve). Returns x, y, parent (birth order = index; parent -1 for the root) and the index of the cell that arrived (-1)."""
    rng = np.random.default_rng(int(seed) & 0x7FFFFFFF)
    yy, xx = np.mgrid[0:gh, 0:gw].astype(np.float32)
    if mode == 'bolt':
        L = _Laplace(gw, gh, yy / (gh - 1))
        L.fix(np.full(gw, gh - 1), np.arange(gw), 1.0)
    elif mode == 'radial':
        R = min(gw, gh) / 2 - 1
        rr = np.hypot(xx - start[0], yy - start[1])
        L = _Laplace(gw, gh, np.clip(np.log(np.maximum(rr, 1.0)) / math.log(R), 0, 1))
        ys, xs = np.nonzero(rr >= R)
        L.fix(ys, xs, 1.0)
    else:
        tx, ty = target
        dist = np.hypot(xx - tx, yy - ty)
        L = _Laplace(gw, gh, 1.0 - dist / max(1.0, dist.max()))
        ys, xs = np.nonzero(dist <= 1.6)
        L.fix(ys, xs, 1.0)
    occ = np.full((gh, gw), -1, np.int32)
    cand = np.zeros((gh, gw), bool)
    nodes_x, nodes_y, par = [], [], []
    hit = -1

    def add(x, y, p):
        i = len(nodes_x)
        nodes_x.append(x); nodes_y.append(y); par.append(p)
        occ[y, x] = i; cand[y, x] = False
        L.fix(y, x, 0.0)
        for dx, dy in N8:
            X, Y = x + dx, y + dy
            if 0 <= X < gw and 0 <= Y < gh and occ[Y, X] < 0:
                cand[Y, X] = True
        return i

    add(int(start[0]), int(start[1]), -1)
    L.relax(80)
    while len(nodes_x) < n_max:
        ys, xs = np.nonzero(cand)
        if len(xs) == 0:
            break
        w = np.power(np.maximum(L.phi[ys, xs], 0.0), eta)
        c = np.cumsum(w)
        if c[-1] <= 0:
            break
        k = min(int(np.searchsorted(c, rng.random() * c[-1])), len(xs) - 1)
        X, Y = int(xs[k]), int(ys[k])
        best, bd = -1, 9
        for dx, dy in N8:
            Xa, Ya = X + dx, Y + dy
            if 0 <= Xa < gw and 0 <= Ya < gh and occ[Ya, Xa] >= 0:
                d = dx * dx + dy * dy
                if d < bd:
                    bd, best = d, int(occ[Ya, Xa])
        i = add(X, Y, best)
        if mode == 'bolt' and Y >= gh - 2:
            hit = i; break
        if mode == 'arc' and math.hypot(X - target[0], Y - target[1]) <= 2.2:
            hit = i; break
        if mode == 'radial' and r_max is not None and math.hypot(X - start[0], Y - start[1]) >= r_max:
            hit = i; break
        L.relax(sweeps)
    return np.array(nodes_x), np.array(nodes_y), np.array(par, np.int32), hit


# ------------------------------------------------------------------ tree -> channel geometry
def _children(par):
    ch = [[] for _ in range(len(par))]
    for i, p in enumerate(par):
        if p >= 0:
            ch[p].append(i)
    return ch


def prune(X, Y, P, hit, min_len, keep, rng):
    """drop side branches shorter than min_len cells (each survives with probability keep): real leaders leave a few long
    branches, not a fur of 1-2 cell twigs. The channel that arrived (hit -> root) is never touched."""
    n = len(P)
    if n < 3 or min_len <= 1:
        return X, Y, P, hit
    ch = _children(P)
    reach = np.zeros(n)
    for i in range(n - 1, 0, -1):
        if P[i] >= 0:
            reach[P[i]] = max(reach[P[i]], reach[i] + 1)
    main = np.zeros(n, bool)
    i = hit
    while i >= 0:
        main[i] = True; i = P[i]
    drop = np.zeros(n, bool)
    for p_ in range(n):
        cs = ch[p_]
        if len(cs) < 2:
            continue
        mc = [c for c in cs if main[c]]
        cont = mc[0] if mc else max(cs, key=lambda c: reach[c])
        for c in cs:
            if c != cont and not main[c] and reach[c] + 1 < min_len and rng.random() > keep:
                drop[c] = True
    for i in range(1, n):
        if P[i] >= 0 and drop[P[i]]:
            drop[i] = True
    k = ~drop
    idx = np.full(n, -1, np.int64)
    idx[k] = np.arange(int(k.sum()))
    P2 = np.where(P[k] >= 0, idx[np.maximum(P[k], 0)], -1).astype(np.int32)
    return X[k], Y[k], P2, (int(idx[hit]) if hit >= 0 else -1)


def build(kind, seed, p0, p1=None, r=None, cell=7.0, eta=None, n_max=None, gamma=None, spread=1.0, fork=0,
          twig=None, keep=None):
    """-> dict of float arrays (px, 1080p units): pos (n,2), parent, size (sub-tree), main (bool), order (0..1), depth"""
    key = hashlib.sha1(repr((GEO_VERSION, kind, seed, tuple(np.round(p0, 2)), None if p1 is None else tuple(np.round(p1, 2)),
                             r, cell, eta, n_max, gamma, spread, fork, twig, keep)).encode()).hexdigest()[:16]
    path = f'{CACHE}/{kind}_{key}.npz'
    if os.path.exists(path):
        d = np.load(path)
        return {k: d[k] for k in d.files}
    rng = np.random.default_rng(int(seed) * 7919 + 17)
    if kind == 'bolt':
        # grid covers p0 -> p1 with margin; field cloud -> ground; stop at the ground row
        (ax, ay), (bx, by) = p0, p1
        L = math.hypot(bx - ax, by - ay)
        ang = math.atan2(by - ay, bx - ax)
        gh = int(L / cell) + 3
        gw = int(L * 0.62 * spread / cell) | 1
        X, Y, P, hit = dbm(gw, gh, (gw // 2, 0), eta=eta or 1.9, n_max=n_max or 6000, seed=seed, mode='bolt')
        # cell -> px: grid y axis along p0->p1, x across
        u = (X - gw // 2).astype(np.float64)
        v = Y.astype(np.float64)
    elif kind == 'radial':
        R = float(r or 700.0)
        gr = int(R / cell) + 3
        gw = gh = 2 * gr + 1
        X, Y, P, hit = dbm(gw, gh, (gr, gr), eta=eta or 1.3, n_max=n_max or 4000, seed=seed, mode='radial',
                           r_max=int(0.92 * (gr - 1)))
        u = (X - gr).astype(np.float64)
        v = (Y - gr).astype(np.float64)
        ang = 0.0
    elif kind == 'arc':
        (ax, ay), (bx, by) = p0, p1
        L = math.hypot(bx - ax, by - ay)
        ang = math.atan2(by - ay, bx - ax)
        gh = int(L / cell) + 5
        gw = int(L * 0.5 * spread / cell) | 1
        tgt = (gw // 2, gh - 3)
        X, Y, P, hit = dbm(gw, gh, (gw // 2, 2), eta=eta or 2.4, n_max=n_max or 5000, seed=seed, mode='arc', target=tgt)
        u = (X - gw // 2).astype(np.float64)
        v = (Y - 2).astype(np.float64)
    else:
        raise ValueError(kind)
    tw, kp = {'bolt': (6, 0.15), 'arc': (5, 0.2), 'radial': (3, 0.4)}[kind]
    X, Y, P, hit = prune(X, Y, P, hit, tw if twig is None else twig, kp if keep is None else keep, rng)
    if kind == 'bolt':
        u, v = (X - gw // 2).astype(np.float64), Y.astype(np.float64)
    elif kind == 'radial':
        u, v = (X - gr).astype(np.float64), (Y - gr).astype(np.float64)
    else:
        u, v = (X - gw // 2).astype(np.float64), (Y - 2).astype(np.float64)
    n = len(X)
    # jitter inside the cell (kills the grid), in cell units
    u = u + rng.uniform(-0.38, 0.38, n)
    v = v + rng.uniform(-0.38, 0.38, n)
    ch = _children(P)
    size = np.ones(n)
    for i in range(n - 1, -1, -1):
        if P[i] >= 0:
            size[P[i]] += size[i]
    depth = np.zeros(n)
    for i in range(n):
        if P[i] >= 0:
            depth[i] = depth[P[i]] + 1
    main = np.zeros(n, bool)
    if kind in ('bolt', 'arc'):
        i = hit if hit >= 0 else int(np.argmax(v))
        while i >= 0:
            main[i] = True
            i = P[i]
    # smoothing along the channel (2 passes): node -> mean of parent and its main child
    big = [max(c, key=lambda j: size[j]) if c else -1 for c in ch]
    for _ in range(1):                                        # light smoothing only: real channels keep sharp kinks
        u2, v2 = u.copy(), v.copy()
        for i in range(n):
            p, c = P[i], big[i]
            if p >= 0 and c >= 0:
                u2[i] = 0.6 * u[i] + 0.2 * (u[p] + u[c])
                v2[i] = 0.6 * v[i] + 0.2 * (v[p] + v[c])
        u, v = u2, v2
    # to px
    if kind == 'radial':
        px = p0[0] + u * cell
        py = p0[1] + v * cell
    else:
        ca, sa = math.cos(ang - math.pi / 2), math.sin(ang - math.pi / 2)
        L = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        vs = v * cell * (L / max(1.0, v.max() * cell)) if kind in ('bolt', 'arc') and main.any() else v * cell
        us = u * cell
        px = p0[0] + us * ca - vs * sa
        py = p0[1] + us * sa + vs * ca
    order = np.arange(n) / max(1, n - 1)
    out = dict(pos=np.stack([px, py], 1), parent=P.astype(np.int32), size=size, main=main, order=order, depth=depth,
               hit=np.array([hit]))
    os.makedirs(CACHE, exist_ok=True)
    tmp = f'{path}.{os.getpid()}.tmp.npz'
    np.savez_compressed(tmp, **out)
    os.replace(tmp, path)
    return out


# ------------------------------------------------------------------ GPU drawing (light, not lines)
SEG_VS = '''#version 410
in vec2 in_pos; in vec4 in_ab; in vec2 in_wi;
uniform vec2 u_size;
out vec2 v_p; flat out vec4 v_ab; flat out vec2 v_wi;
void main(){ v_p = in_pos; v_ab = in_ab; v_wi = in_wi; gl_Position = vec4(in_pos / u_size * 2.0 - 1.0, 0.0, 1.0); }'''
SEG_FS = '''in vec2 v_p; flat in vec4 v_ab; flat in vec2 v_wi;
void main(){ vec2 pa = v_p - v_ab.xy, ba = v_ab.zw - v_ab.xy; float h = clamp(dot(pa, ba) / max(dot(ba, ba), 1e-6), 0.0, 1.0);
  float d = length(pa - ba * h); float w = v_wi.x;
  o = vec4(v_wi.y * exp(-0.5 * d * d / (w * w)), 0.0, 0.0, 1.0); }'''

LIGHT_FS = VT.SHOULDER_GLSL + '''uniform sampler2D a; uniform sampler2D i0; uniform sampler2D i1; uniform sampler2D hb;
uniform sampler2D ramp; uniform float c1; uniform float expo; uniform float k; uniform float sh; uniform int mode;
uniform float gn; uniform float gs; uniform float seed;
float h21(vec2 p){ p = fract(p * vec2(443.897, 441.423)); p += dot(p, p.yx + 19.19); return fract((p.x + p.y) * p.x); }
float vn(vec2 p){ vec2 i = floor(p); vec2 f = fract(p); f = f * f * (3.0 - 2.0 * f);
  return mix(mix(h21(i), h21(i + vec2(1.0, 0.0)), f.x), mix(h21(i + vec2(0.0, 1.0)), h21(i + vec2(1.0, 1.0)), f.x), f.y); }
void main(){ vec2 uv = UV();
  float Ec = texture(i0, uv).r + c1 * texture(i1, uv).r;                   // the channel itself (softened like the plate)
  float v = 1.0 - exp(-Ec * expo);
  vec3 col = lin(texture(ramp, vec2(clamp(v, 0.0, 1.0), 0.5)).rgb) * v;    // white where it over-exposes, faint = pale
  col += texture(hb, uv).rgb * expo;                                      // energy-conserving bloom: air scattering + lens
  if (mode == 1){ vec3 c = shoulder(col * k, sh); o = vec4(enc(c), clamp(max(c.r, max(c.g, c.b)), 0.0, 1.0)); return; }
  vec3 base = lin(texture(a, uv).rgb);
  vec3 res = enc(shoulder(base + col * k, sh));
  // grain: the footage's own noise level on the light we add (none where it clips); slightly correlated like the plate's
  float ad = luma(res) - luma(enc(base));
  vec2 q = gl_FragCoord.xy / gs + seed;
  float n = (vn(q) + vn(q * 1.73 + 17.1) - 1.0) * 3.2;
  float amp = gn * smoothstep(0.0, 0.10, ad) * (1.0 - smoothstep(0.82, 0.99, luma(res)));
  o = vec4(clamp(res + n * amp, 0.0, 1.0), 1.0); }'''


class Discharge:
    """one discharge (geometry fixed by its arguments + seed). Times in film frames relative to the return stroke.
    look v3 (the drawn bolt must not read as generated next to footage): soft = 'auto' measures the
    picture it is drawn over (fx.volt.plate_stats) and softens the channel to the same blur (a float = fixed px);
    grain = the picture's own noise level on the added light (x grain); the trunk carries the current (branches br_k x
    dimmer at the stroke, thinner); the halo is the shared energy-conserving bloom (bloom x the channel's energy, scales
    sig[0] * 2.5^i: air scattering along the channel + lens, white -> ICE -> pale violet far out); the return stroke and
    every re-stroke are brightness STEPS (held ~1 frame, a fast fall to a continuing-current glow): 2-3 flashes.
    look v4: the channel ramp 'volt_ch' and the halo carry a clear colour (white core -> cyan-blue ->
    electric blue -> a little violet far out, the fx.volt L4 palette): hsat 0.75 -> 0.95, bloom 0.16 -> 0.25; the flash
    on the clouds is LAVENDER (ftsat 0.2 -> 0.35) and pales with distance (ftfar)."""

    def __init__(self, kind='bolt', seed=1, p0=(960.0, -30.0), p1=(960.0, 1080.0), r=None, cell=7.0, eta=None,
                 n_max=None, gamma=None, spread=1.0, twig=None, keep=None, width=2.4, wmin=0.7, inten=5.0, bmin=0.15, main_k=1.5,
                 grow=5.0, tau_b=2.2, tau_m=5.0, restrike=(5.0, 9.0), expo=1.0, glow=1.0, illum=0.0, sh=0.8,
                 ramp='volt_ch', c1=0.35, halo=(0.3, 0.15, 0.06, 0.02), hsat=0.95,
                 tints=('ARC_WHITE', 'ICE', 'ICE', 'ELECTRIC', 'VIOLET', 'DEEP_VIOLET'), sig=(1.6, 5.0, 14.0, 40.0, 110.0),
                 fgain=60.0, fcap=1.8, fw=(0.35, 0.35, 0.30), fadd=0.05, ftsat=0.35, fdk=1.2, ftint='LAVENDER', ftfar=0.4,
                 soft='auto', grain=1.0, br_k=0.45, bloom=0.25, bq=0.65, bs0=4.5):
        self.kind, self.seed = kind, int(seed)
        self.g = build(kind, seed, p0, p1, r, cell, eta, n_max, gamma, spread, 0, twig, keep)
        self.width, self.wmin, self.inten, self.bmin, self.main_k = width, wmin, inten, bmin, main_k
        self.grow, self.tau_b, self.tau_m, self.restrike = float(grow), tau_b, tau_m, restrike
        self.expo, self.glow, self.illum, self.sh, self.ramp = expo, glow, illum, sh, ramp
        self.c1, self.halo, self.sig = c1, halo, sig
        self.fgain, self.fcap, self.fw, self.fadd, self.ftsat, self.fdk = fgain, fcap, fw, fadd, ftsat, fdk
        self.ftint, self.ftfar = ftint, ftfar
        self.soft, self.grain, self.br_k, self.bloom, self.bq, self.hsat = soft, grain, br_k, bloom, bq, hsat
        self.bs0 = bs0
        self.tint_names = tuple(tints)
        self.tints = [VT._tint4(x, hsat) for x in tints]
        g = self.g
        P, size, main = g['parent'], g['size'], g['main']
        n = len(P)
        smax = max(1.0, size[1:].max() if n > 1 else 1.0)
        f = np.clip(size / smax, 0, 1)
        fm = np.where(main, np.maximum(f, 0.3), f)                      # the channel that arrived carries the current
        # look v3.1 (a uniform trunk reads as a thick white tube): the trunk is ~0.6x as wide and NOT uniform -- kinks and the
        # nodes near a fork are a little brighter / wider, straight runs thinner / dimmer (a photographed channel)
        pos = g['pos']
        ch = _children(P)
        cont = np.array([(next((c for c in cs if main[c]), max(cs, key=lambda j: size[j])) if cs else -1) for cs in ch])
        bend = np.zeros(n)
        ii = np.nonzero((P >= 0) & (cont >= 0))[0]
        if len(ii):
            va, vb = pos[ii] - pos[P[ii]], pos[cont[ii]] - pos[ii]
            bend[ii] = 1.0 - (va * vb).sum(1) / (np.hypot(va[:, 0], va[:, 1]) * np.hypot(vb[:, 0], vb[:, 1]) + 1e-9)
        par = np.where(P >= 0, P, np.arange(n))
        con = np.where(cont >= 0, cont, np.arange(n))
        for _ in range(3):                                            # along the channel, not across it
            bend = 0.5 * bend + 0.25 * (bend[par] + bend[con])
        fk = np.array([1.0 if len(cs) >= 2 else 0.0 for cs in ch])
        for _ in range(3):
            fk = np.maximum(fk, 0.6 * np.maximum(fk[par], fk[con]))
        v = 0.8 * bend + 0.6 * fk
        ref = v[main] if main.sum() > 8 else v
        lo, hi = np.percentile(ref, 30), np.percentile(ref, 92)
        mod = np.clip((v - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
        self.mod = mod
        wbase = np.where(main, 0.6 * (wmin + (width - wmin) * 0.85 * fm ** 0.55), wmin + (width - wmin) * f ** 0.8)
        self.w = wbase * (0.7 + 0.6 * mod)                               # core sigma (px)
        self.w *= 0.85 + 0.15 * np.sin(g['depth'] * 0.37 + seed) ** 2 + 0.08 * np.sin(g['depth'] * 0.11)  # beads
        self.b = bmin + (1.0 - bmin) * np.where(main, fm ** 0.45, f ** 0.7)   # brightness from the current
        self.b *= 0.75 + 0.5 * mod
        rng = np.random.default_rng(self.seed + 99)
        ph = rng.uniform(0, 6.283, 3)
        dd = g['depth']
        self.b *= 0.82 + 0.18 * np.sin(dd * 0.21 + ph[0]) * np.sin(dd * 0.057 + ph[1])   # brightness varies along a channel
        # perpendicular midpoint detail for each segment (finer tortuosity than the cell)
        A = g['pos'][np.maximum(P, 0)]
        B = g['pos']
        d = B - A
        L = np.hypot(d[:, 0], d[:, 1]) + 1e-6
        nrm = np.stack([-d[:, 1] / L, d[:, 0] / L], 1)
        m2 = (A + B) / 2 + nrm * (rng.normal(0, 0.19, n) * L)[:, None]            # 2-level midpoint displacement
        m1 = (A + m2) / 2 + nrm * (rng.normal(0, 0.09, n) * L)[:, None]
        m3 = (m2 + B) / 2 + nrm * (rng.normal(0, 0.09, n) * L)[:, None]
        self.sub = np.stack([A, m1, m2, m3, B], 1)                                 # (n, 5, 2)
        self.valid = P >= 0

    # -------------------------------------------------- per-node intensity at time t
    def strokes(self, t):
        """-> (main, branch, width) multipliers of the return strokes at t >= 0. Each stroke is a STEP (on its frame,
        held ~0.12 tau_m), then a fast fall (0.22 tau_m) to a continuing-current glow (32 %) that decays with tau_m;
        re-strokes (restrike times) step the channel up again -- real lightning flickers 2-4 times. Branches light with
        the first stroke (fading with tau_b) and only faintly with the re-strokes."""
        tm = max(float(self.tau_m), 1e-3)
        hold, tf, fl = max(0.6, 0.12 * tm), max(0.45, 0.22 * tm), 0.32

        def prof(x, tau_slow, tau_fast):
            if x < 0.0:
                return 0.0
            if x <= hold:
                return 1.0
            y = x - hold
            return fl * math.exp(-y / tau_slow) + (1.0 - fl) * math.exp(-y / tau_fast)
        M = prof(t, tm, tf)
        Bm = prof(t, max(float(self.tau_b), 1e-3), 0.5 * max(float(self.tau_b), 1e-3))
        for j, r_ in enumerate(self.restrike or ()):
            a = 0.85 * 0.88 ** j
            M = max(M, a * prof(t - r_, 0.6 * tm, tf))
            Bm = max(Bm, 0.22 * a * prof(t - r_, tf, 0.5 * tf))
        W = 1.0 + 0.2 * max(0.0, M - fl) / (1.0 - fl)
        return M, Bm, W

    def state(self, t):
        """-> (visible mask, intensity multiplier, width multiplier) per node at t (frames from the return stroke)"""
        g = self.g
        order, main = g['order'], g['main']
        if t < 0:
            gu = 1.0 + t / max(self.grow, 1e-3)                        # 0 -> 1 over `grow` frames
            if gu <= 0:
                return None
            vis = order <= gu
            tip = np.clip(1.0 - (gu - order) / 0.06, 0.0, 1.0)          # freshly grown cells glow (leader tips)
            mul = 0.22 + 0.9 * tip
            return vis, mul, np.ones_like(order)
        vis = np.ones(len(order), bool)
        M, Bm, Wm = self.strokes(t)
        mul = np.where(main, self.main_k * M, self.br_k * (1.0 + 0.7 * math.exp(-t / 0.8)) * Bm)   # branches flare with the stroke
        wid = np.where(main, Wm, 1.0)
        return vis, mul, wid

    def _segments(self, E, t, scale=1.0, offset=(0.0, 0.0), rot=0.0, soft=0.0):
        st = self.state(t)
        if st is None:
            return None
        vis, mul, wid = st
        sel = self.valid & vis & (mul * self.b > 0.01)
        if not sel.any():
            return None
        idx = np.nonzero(sel)[0]
        SP = self.sub[idx]
        w = self.w[idx] * wid[idx]
        I = self.inten * self.b[idx] * mul[idx]
        s = E.s
        o = np.array(offset, np.float64)
        if rot or scale != 1.0:                                        # about the geometry's own origin p0
            c_, s_ = math.cos(rot), math.sin(rot)
            Rm = np.array([[c_, -s_], [s_, c_]]) * scale
            p0 = self.g['pos'][0]
            SP = (SP - p0) @ Rm.T + p0
            w = w * scale
        if soft > 0.0:                                                 # the plate's blur: wider, same energy per length
            we = np.sqrt(w * w + float(soft) ** 2)
            I = I * w / we
            w = we
        segs = []
        for q in range(4):                                             # four sub-segments (fractal detail below the cell)
            segs.append(np.concatenate([(SP[:, q] + o) * s, (SP[:, q + 1] + o) * s, (w * s)[:, None], I[:, None]], 1))
        S = np.concatenate(segs, 0).astype(np.float32)
        return S

    def light_raw(self, E, t, scale=1.0, offset=(0.0, 0.0), rot=0.0, soft=None):
        """sharp HDR intensity (R) texture at output size, or None before the leader starts (soft = extra Gaussian
        blur in px; None = the fixed `soft` of this discharge, or SOFT0 when it is 'auto')"""
        if soft is None:
            soft = VT.SOFT0 if self.soft == 'auto' else float(self.soft or 0.0)
        S = self._segments(E, t, scale, offset, rot, soft)
        dst = E.new(clear=(0, 0, 0, 0))
        if S is None:
            return dst
        a, b, w, I = S[:, 0:2], S[:, 2:4], S[:, 4], S[:, 5]
        d = b - a
        L = np.hypot(d[:, 0], d[:, 1]) + 1e-6
        tdir = d / L[:, None]
        nrm = np.stack([-tdir[:, 1], tdir[:, 0]], 1)
        e = (3.2 * w + 1.5)[:, None]
        c0 = a - tdir * e - nrm * e
        c1 = b + tdir * e - nrm * e
        c2 = b + tdir * e + nrm * e
        c3 = a - tdir * e + nrm * e
        ab = np.concatenate([a, b], 1)
        wi = np.stack([np.maximum(w, 0.6 * E.s), I], 1)
        verts = []
        for c in (c0, c1, c2, c0, c2, c3):
            verts.append(np.concatenate([c, ab, wi], 1))
        V = np.stack(verts, 1).reshape(-1, 8).astype(np.float32)
        prog = E.program(SEG_FS, SEG_VS)
        E._bind(prog, None, None, dst.w, dst.h)
        buf = E.ctx.buffer(V.tobytes())
        vao = E.ctx.vertex_array(prog, [(buf, '2f 4f 2f', 'in_pos', 'in_ab', 'in_wi')])
        dst.fbo.use()
        E.ctx.enable(moderngl.BLEND)
        E.ctx.blend_equation = moderngl.MAX
        vao.render(moderngl.TRIANGLES)
        E.ctx.blend_equation = moderngl.FUNC_ADD
        E.ctx.disable(moderngl.BLEND)
        vao.release(); buf.release()
        return dst

    def _composite(self, E, img, t, mode, k=1.0, scale=1.0, offset=(0.0, 0.0), illum=None, rot=0.0):
        if img is not None and self.soft == 'auto':
            soft, noise = VT.plate_stats(E, img)
        else:
            soft = VT.SOFT0 if self.soft == 'auto' else float(self.soft or 0.0)
            noise = VT.NOISE0
        raw = self.light_raw(E, t, scale, offset, rot, soft=soft)
        b1 = E.blur(raw, float(self.sig[0]) * scale)
        il = float(self.illum if illum is None else illum)
        if mode == 0 and img is not None and il > 0.003:     # 闪光补光: the flash lights the clouds around it (fx.volt.flash)
            img = VT.flash(E, img, raw, k=il * min(1.0, k), rch=True, gain=self.fgain, cap=self.fcap, w=self.fw, add=self.fadd,
                           tsat=self.ftsat, scale=scale, dk=self.fdk, tint=self.ftint, tfar=self.ftfar)     # fdk ~1: the dim leader flashes ~1/6 of the stroke
        hb = VT.bloom(E, None, raw, amount=self.bloom * self.glow, s0=float(self.bs0), r=2.5, q=self.bq,
                      tints=self.tint_names, sat=self.hsat, tk=1.0, scale=scale, rch=True, light_only=True)
        seed = (float(t) * 0.618034 + self.seed * 0.137) % 97.0
        return run(E, LIGHT_FS, {'a': img if img is not None else raw, 'i0': raw, 'i1': b1, 'hb': hb,
                                 'ramp': ramp_tex(E, self.ramp)},
                   {'c1': float(self.c1), 'expo': float(self.expo), 'k': float(k), 'sh': float(self.sh), 'mode': int(mode),
                    'gn': float(self.grain) * float(noise) * 1.1, 'gs': max(1.0, 0.9 * soft) * E.s, 'seed': float(seed)})

    def draw(self, E, img, t, k=1.0, scale=1.0, offset=(0.0, 0.0), illum=None, rot=0.0):
        """composite the discharge at time t over img (linear-light add + shoulder; the flash lights the world).
        scale / rot (radians) act about the geometry's origin p0, then offset (px) moves it: one cached geometry, many uses."""
        if self.state(t) is None:
            return img
        return self._composite(E, img, t, 0, k, scale, offset, illum, rot)

    def light(self, E, t, k=1.0, scale=1.0, offset=(0.0, 0.0), rot=0.0):
        """the coloured light alone (rgb, alpha = intensity 0..1) -- for custom compositing / negatives"""
        return self._composite(E, None, t, 1, k, scale, offset, None, rot)
