# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""src/kit/hero.py -- 主角字 (hero type) v12.

Why: mirror-chrome numerals took the plate's colour and reflections, so contrast and legibility dropped:
the voltage numbers are no longer mirror chrome reflecting the plate behind them (face and plate the same colour and
brightness, dark reflections cutting the glyphs up, standing still). They are the series' FLAT hero type (IGNITION SPARK /
HEAT / 525 °C / MELT / LIFTOFF): HEAVY = size (>= 55 % of the frame width) + a solid fill + a hard 2.5D extrusion;
COOL = an electric arc writes the outline before the slam, the slam (1.12 -> 1.0, zoom blur, shake, focus lines, real
sparks), a light sweep while it holds; LEGIBLE = the plate under the word is blurred and pressed down.

One call does the whole life of a hero word (a pure function of f: any frame renders alone):

    img = HE.hero(E, img, T.f, '5 kV', start=S - 7, land=S, leave=X, style='slab', label='NEON', volts=5e3, prev=220.0)

  img    the finished plate at f (camera + look); the hero word is composited on it and the result returned
  start  first frame of the reveal: the arc starts round the outline (card / knock: the card's circle mask starts)
  land   the slam frame (on the beat)
  leave  the first frame without the word (= the cut it rides out); the exit runs over the `n_out` frames before it
styles (STYLES)
  slab   solid PAPER face + hard extrusion to the lower right (12-24 px, a block of iron) + a fine bevel light; the plate
         under the word is blurred and pressed down (calm); arc reveal -> slam -> sweep -> whip / pull out
  card   full-bleed paper card: circle mask in (>= 6 frames, its rim an arc of light), the outline written in ink, INK word
         slams, a voltage ladder (log scale 100 V .. 1 GV) fills to the value, DM Mono footnote; diagonal wipe out
  knock  the same card with the word CUT OUT: the footage shows through the letters (pushed, lifted), the paper's cut edge
         throws a shadow into them, the rims carry the arc; circle in, strips out
  slam   a HOOK cell word (6-12 frames): no reveal, the slab slams on `land` (1.25 -> 1.0) + a short aftermath (outline
         flash, focus lines, shake), gone on `leave` (exit 'cut')
v2: the extrusion is a hard-edged block of iron (sides shaded by facing, ink with a touch of cold, depth
18-28 px, the light LIP kept); the arc is ONE head running the outline letter by letter with an afterglow; the voltage
Other parts: lay() (geometry, cached), calm(), ladder(), tag(), arc_light(). Chinese aliases: 主角字 = hero, 标签 = tag.
Colours: INK / PAPER / WHITE (kit.core) and the named volt colours (fx.volt) only. Numbers on screen: checked values only.
"""
import math
import functools
import numpy as np
import skia
import moderngl
from src.engine.canvas import paint
from src.kit import type as TY, fx
from src.kit.core import INK, PAPER, WHITE, run, ramp_tex
from src.kit.anim import clamp01, lerp, eo3, ei3, eio3
from src.fx import volt as VT
from src.fx import discharge as DC
from src.fx import impact as IM

W, H = 1920, 1080
NS = ' '                                  # narrow no-break space (number groups / units)
STYLES = ('slab', 'card', 'knock', 'slam')
JUMPK = 0.35                                   # an arc jump between two letters counts this much of its length in the run
EXT_ANG = 45.0                                 # extrusion direction (deg, 0 = right, 90 = down): to the lower right


def _mix(a, b, t):
    return tuple(float(a[i] + (b[i] - a[i]) * t) for i in range(3))


FACE_TOP = _mix(PAPER, WHITE, 0.6)             # slab face: paper, lit from above
FACE_BOT = _mix(PAPER, INK, 0.07)
SIDE_D = _mix(INK, VT.ELECTRIC, 0.03)          # the extrusion = a block of iron: ink, a touch cold; sides facing down
SIDE_L = _mix(INK, VT.ELECTRIC, 0.16)          # ... sides facing right (a little fill light)
LIP = _mix(INK, PAPER, 0.42)                   # the first px of the side next to the face (catches the light)
LADDER = ((220.0, f'220{NS}V'), (5e3, f'5{NS}kV'), (1.1e6, f'±1100{NS}kV'), (3e8, f'300{NS}MV'))   # the film's steps
LADDER_LAB = dict(LADDER)
BEVEL_LO = _mix(PAPER, INK, 0.35)


def _c4(c, a=1.0):
    return skia.Color4f(float(c[0]), float(c[1]), float(c[2]), float(a)).toColor()


def _off(path, dx, dy):
    q = skia.Path(path)
    q.offset(float(dx), float(dy))
    return q


def _union(paths):
    ob = skia.OpBuilder()
    for p in paths:
        ob.add(p, skia.PathOp.kUnion_PathOp)
    return ob.resolve()


# ================================================================== geometry
class Lay:
    """geometry of one hero word in 1080p px. width = fraction of the frame width (ignored if cap is given);
    (x, cy) = anchor x (align) and the vertical centre of the letters' ink. key None = 'wide' for numbers, 'hero' for words;
    caps None = keep case for numbers (kV), caps for words."""

    def __init__(self, text, key=None, width=0.62, cap=None, x=960.0, cy=560.0, align='center', caps=None, depth=None):
        num = any(ch.isdigit() for ch in text)
        self.text = text
        self.key = key or ('wide' if num else 'hero')
        self.caps = (not num) if caps is None else bool(caps)
        if cap:
            size = TY.size_for_cap(self.key, cap)
        else:
            size = width * W / max(TY.measure(text, self.key, 100.0, caps=self.caps), 1e-3) * 100.0
        self.size = float(size)
        self.w = TY.measure(text, self.key, self.size, caps=self.caps)
        self.cap = TY.cap_height(self.key, self.size)
        x0 = x - (self.w if align == 'right' else self.w / 2 if align == 'center' else 0.0)
        p = TY.word_path(text, x0, 0.0, self.size, self.key, 'left', caps=self.caps)
        b = p.computeTightBounds()
        p.offset(0.0, cy - 0.5 * (b.top() + b.bottom()))
        self.path = p
        b = p.computeTightBounds()
        self.box = (b.left(), b.top(), b.right(), b.bottom())
        self.cx, self.cy = 0.5 * (b.left() + b.right()), 0.5 * (b.top() + b.bottom())
        self.depth = float(depth) if depth is not None else min(28.0, max(18.0, 0.085 * self.cap))
        self._geo = None
        cs = sorted(_contours(p), key=lambda cl: cl[0][:, 0].min())     # the run: letter by letter, left -> right
        self.contours, self.run_start, self.run_step, self.run_jump = [], [], [], []
        S, prev = 0.0, None
        for C, Lc in cs:
            i0 = int(np.argmin(C[:, 0] + 0.25 * C[:, 1])) if prev is None else int(np.argmin(np.hypot(*(C - prev).T)))
            C = np.roll(C, -i0, 0)
            jump = 0.0 if prev is None else float(np.hypot(*(C[0] - prev))) * JUMPK
            S += jump
            self.contours.append(C); self.run_jump.append(jump); self.run_start.append(S); self.run_step.append(Lc / len(C))
            S += Lc
            prev = C[0]
        self.run_total = S

    def geo(self):
        """cached paths: ext (the extrusion), lip (its first px), hi / lo (bevel slivers), all (face + ext),
        paper (a huge sheet with the word cut out), hole_sh (shadow caster of the paper's cut edge)"""
        if self._geo is None:
            d, a = self.depth, math.radians(EXT_ANG)
            ux, uy = math.cos(a), math.sin(a)
            n = max(2, int(math.ceil(d / 1.2)))
            ext = _union([_off(self.path, ux * d * i / n, uy * d * i / n) for i in range(1, n + 1)])
            lip = _union([_off(self.path, ux * q, uy * q) for q in (0.8, 1.6, 2.4)])
            e = 0.0045 * self.cap + 0.9
            hi = skia.Op(self.path, _off(self.path, e, e), skia.PathOp.kDifference_PathOp)
            lo = skia.Op(self.path, _off(self.path, -e, -e), skia.PathOp.kDifference_PathOp)
            big = skia.Path()
            big.addRect(skia.Rect.MakeLTRB(-3000.0, -3000.0, 5000.0, 4000.0))
            paper = skia.Op(big, self.path, skia.PathOp.kDifference_PathOp)
            sd = 0.035 * self.cap + 3.0
            hole_sh = skia.Op(big, _off(self.path, sd, sd * 1.2), skia.PathOp.kDifference_PathOp)
            self._geo = dict(ext=ext, lip=lip, hi=hi, lo=lo, all=_union([self.path, ext]), paper=paper, hole_sh=hole_sh,
                             sides=_sides(self))
        return self._geo


def _contours(path, step=3.0):
    """the outline as closed polylines (one per contour, ~step px apart), in path order"""
    out = []
    pm = skia.PathMeasure(path, True, 1.0)
    while True:
        L = pm.getLength()
        if L > 6.0:
            n = max(8, int(L / step))
            pts = np.empty((n, 2), np.float64)
            for i in range(n):
                p, _ = pm.getPosTan(L * i / n)
                pts[i] = (p.x(), p.y())
            out.append((pts, L))
        if not pm.nextContour():
            break
    return out


def _sides(L, nb=8):
    """the visible side faces of the extrusion (outward normal towards the extrusion direction), one quad per outline
    segment, shaded by the way they face (right = a little fill light, down = darkest) -> [(colour, path)] by shade"""
    a = math.radians(EXT_ANG)
    dh = np.array([math.cos(a), math.sin(a)])
    D = dh * L.depth
    ls = np.array([0.9, -0.45]) / math.hypot(0.9, 0.45)
    paths = [skia.Path() for _ in range(nb)]
    for C in L.contours:
        Q = np.roll(C, -1, 0)
        e = Q - C
        nrm = np.stack([e[:, 1], -e[:, 0]], 1) / (np.hypot(e[:, 0], e[:, 1]) + 1e-9)[:, None]
        mid = 0.5 * (C + Q)
        idx = np.linspace(0, len(C) - 1, 9).astype(int)
        votes = sum(-1 if L.path.contains(float(mid[i, 0] + 1.8 * nrm[i, 0]), float(mid[i, 1] + 1.8 * nrm[i, 1])) else 1
                    for i in idx)
        if votes < 0:
            nrm = -nrm
        sh = np.clip(0.5 + 0.5 * (nrm @ ls), 0.0, 1.0)
        b = np.minimum((sh * nb).astype(int), nb - 1)
        for i in np.nonzero(nrm @ dh > 0.0)[0]:
            p0, p1 = C[i], Q[i]
            paths[b[i]].addPoly([skia.Point(float(p0[0]), float(p0[1])), skia.Point(float(p1[0]), float(p1[1])),
                                 skia.Point(float(p1[0] + D[0]), float(p1[1] + D[1])),
                                 skia.Point(float(p0[0] + D[0]), float(p0[1] + D[1]))], True)
    return [(_mix(SIDE_D, SIDE_L, (k + 0.5) / nb), q) for k, q in enumerate(paths) if not q.isEmpty()]


@functools.lru_cache(64)
def lay(text, key=None, width=0.62, cap=None, x=960.0, cy=560.0, align='center', caps=None, depth=None):
    return Lay(text, key, width, cap, x, cy, align, caps, depth)


def _place(c, L, s, off):
    """skia transform: scale s about the word centre, then translate by off"""
    c.save()
    c.translate(L.cx + off[0], L.cy + off[1])
    c.scale(s, s)
    c.translate(-L.cx, -L.cy)


def _xf(L, s, off, P):
    return (P - (L.cx, L.cy)) * s + (L.cx + off[0], L.cy + off[1])


def _xpath(L, s, off, path):
    m = skia.Matrix()
    m.setTranslate(-L.cx, -L.cy)
    m.postScale(s, s)
    m.postTranslate(L.cx + off[0], L.cy + off[1])
    q = skia.Path(path)
    q.transform(m)
    return q


# ================================================================== light (the discharge look: Gaussian cross-section
# segments, max-blended, then the shared bloom + the 'volt_ch' ramp -- the same light as fx.discharge bolts)
GT = ('ARC_WHITE', 'ICE', 'ICE', 'ELECTRIC', 'VIOLET', 'DEEP_VIOLET')
ARC_RAMP = 'volt_ch'                       # p2: the reveal arc's colour ramp (ramp name or hex stops); set_motif() changes both


def set_motif(P):
    """p2: draw the reveal arc / its glow in the episode's motif colours (P = fx.motif.palette(...)); call once per module"""
    global GT, ARC_RAMP
    GT = (P['core'], P['pale'], P['pale'], P['acc'], P['deep'], P['deep'])
    ARC_RAMP = list(P['ramp'][1:])
    from src.fx import motif as MO
    MO.set_current(P)


def _arcs_on():
    """p2 11-05: the electric-arc outline / rim lights (arc_segs, ring_segs) and the slam focus lines are the 'volt' devices:
    on only when timeline.SERIES_FX == 'volt'. Every other motif: solid reveals (「贴图、线段」 = 劣质特效)."""
    from src.fx import motif as MO
    return MO.series_fx() == 'volt'


def _seg_raw(E, S):
    dst = E.new(clear=(0, 0, 0, 0))
    if S is None or len(S) == 0:
        return dst
    S = np.asarray(S, np.float32).copy()
    S[:, :5] *= E.s
    a, b, w, I = S[:, 0:2], S[:, 2:4], S[:, 4], S[:, 5]
    d = b - a
    L = np.hypot(d[:, 0], d[:, 1]) + 1e-6
    tdir = d / L[:, None]
    nrm = np.stack([-tdir[:, 1], tdir[:, 0]], 1)
    e = (3.2 * w + 1.5)[:, None]
    c0, c1 = a - tdir * e - nrm * e, b + tdir * e - nrm * e
    c2, c3 = b + tdir * e + nrm * e, a - tdir * e + nrm * e
    ab = np.concatenate([a, b], 1)
    wi = np.stack([np.maximum(w, 0.6 * E.s), I], 1)
    V = np.stack([np.concatenate([c, ab, wi], 1) for c in (c0, c1, c2, c0, c2, c3)], 1).reshape(-1, 8).astype(np.float32)
    prog = E.program(DC.SEG_FS, DC.SEG_VS)
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


def light(E, img, segs, glow=0.3, k=1.0, seed=0.0):
    """add drawn light (rows ax ay bx by sigma intensity, 1080p px) to img like fx.discharge does"""
    if not segs:
        return img
    raw = _seg_raw(E, segs)
    hb = VT.bloom(E, None, raw, amount=glow, s0=4.5, r=2.5, q=0.65, tints=GT, sat=0.95, tk=1.0, rch=True, light_only=True)
    return run(E, DC.LIGHT_FS, {'a': img, 'i0': raw, 'i1': E.blur(raw, 1.6), 'hb': hb, 'ramp': ramp_tex(E, ARC_RAMP)},
               {'c1': 0.35, 'expo': 1.0, 'k': float(k), 'sh': 0.8, 'mode': 0, 'gn': 0.011, 'gs': E.s, 'seed': float(seed % 97.0)})


def _poly_segs(P, I, w, closed=False):
    """polyline points (n, 2) + per-point intensity (n,) -> segment rows"""
    Q = np.roll(P, -1, 0) if closed else P[1:]
    A = P if closed else P[:-1]
    Ii = I if closed else I[:-1]
    m = Ii > 0.02
    if not m.any():
        return []
    return np.concatenate([A[m], Q[m], np.full((m.sum(), 1), w), Ii[m][:, None]], 1).tolist()


def _jump(a, b, I, rng, sc):
    """the arc leaping from one letter to the next: a short jagged channel a -> b"""
    d = b - a
    Ld = float(np.hypot(*d))
    if Ld < 2.0 or I < 0.05:
        return []
    nrm = np.array([-d[1], d[0]]) / Ld
    k = np.linspace(0.0, 1.0, 7)
    P = a + np.outer(k, d) + np.outer(np.sin(k * math.pi) * rng.normal(0.0, min(18.0, 0.12 * Ld + 3.0 * sc), 7), nrm)
    return _poly_segs(P, np.full(7, I), 0.9)


def arc_segs(L, f, start, land, s=1.0, off=(0.0, 0.0), seed=0, hot=6.0, cool=0.9, lam=None, after=7.0, mode='run'):
    """「电弧描边显影」. mode 'run' (default): ONE bright head runs along the outline letter by letter (left -> right,
    leaping between contours as a short arc), crackling (jitter + two forks), the afterglow behind it cooling to a thin
    glowing outline (lam = afterglow length, px; default ~ half the head's travel per frame); 'all' = every contour at
    once (v1). On `land` the whole outline flashes and dies over `after` frames. -> segment rows"""
    t = f - land
    if f < start or t > after:
        return []
    segs = []
    rng = np.random.default_rng(int(seed * 7919 + math.floor(f * 2.0)) & 0x7fffffff)
    sc = L.cap / 250.0
    if t < 0 and mode == 'run':
        u = clamp01((f - start) / max(land - start, 1e-3))
        sh = u * L.run_total
        lam = lam or max(140.0, 0.5 * L.run_total / max(land - start, 1.0))
        for j, C in enumerate(L.contours):
            S0, step, n = L.run_start[j], L.run_step[j], len(C)
            if sh <= S0:
                if j > 0 and sh > S0 - L.run_jump[j]:                   # leaping towards this letter right now
                    a = _xf(L, s, off, L.contours[j - 1][:1])[0]
                    b = _xf(L, s, off, C[:1])[0]
                    segs += _jump(a, a + (b - a) * (sh - S0 + L.run_jump[j]) / max(L.run_jump[j], 1e-3), hot, rng, sc)
                break
            P = _xf(L, s, off, C)
            if j > 0 and sh - S0 < 1.5 * lam:                         # the leap that brought it here, fading
                segs += _jump(_xf(L, s, off, L.contours[j - 1][:1])[0], P[0], hot * 0.7 * math.exp(-(sh - S0) / (0.5 * lam)),
                              rng, sc)
            hi = min(n, int((sh - S0) / step) + 1)
            i = np.arange(hi)
            I = cool + (hot - cool) * np.exp(-(sh - (S0 + i * step)) / lam)
            Q = P[:hi].copy()
            if hi < n:                                                  # the head is on this contour: crackle + forks
                m = 22
                nb = np.roll(P, -1, 0)[i] - np.roll(P, 1, 0)[i]
                nrm = np.stack([-nb[:, 1], nb[:, 0]], 1) / (np.hypot(nb[:, 0], nb[:, 1]) + 1e-6)[:, None]
                Q += nrm * (rng.normal(0.0, 4.5 * sc, hi) * np.clip((i - (hi - m)) / m, 0.0, 1.0) ** 1.5)[:, None]
                segs += _poly_segs(Q, I, 1.05, closed=False)
                hp = Q[-1]
                tg = P[hi - 1] - P[max(hi - 4, 0)]
                tg = tg / (np.hypot(*tg) + 1e-6)
                for q in range(2):
                    ang = math.atan2(tg[1], tg[0]) + (1 if q else -1) * rng.uniform(0.5, 1.4)
                    Lf = rng.uniform(16.0, 40.0) * sc
                    pts = np.array([hp] + [hp + Lf * kk / 4 * np.array([math.cos(ang), math.sin(ang)])
                                           + rng.normal(0.0, 3.0 * sc, 2) for kk in range(1, 5)])
                    segs += _poly_segs(pts, hot * 0.55 * np.linspace(1.0, 0.3, len(pts)), 0.8)
            else:
                segs += _poly_segs(Q, I, 1.05, closed=True)
        return segs
    lam = lam or 110.0
    for j, C in enumerate(L.contours):
        n = len(C)
        P = _xf(L, s, off, C)
        if t >= 0:                                                   # the slam: the whole outline flashes, then dies
            k = hot * 0.9 * math.exp(-t / 1.7)
            segs += _poly_segs(P, np.full(n, k), 1.3 * max(s, 0.5), closed=True)
            continue
        u = clamp01((f - start) / max(land - start, 1e-3))
        h = (1.0 - (1.0 - u) ** 2.0) * n                             # head position (fast, settling)
        hi = int(h)
        if hi < 2:
            continue
        i = np.arange(hi + 1)
        I = cool + (hot - cool) * np.exp(-(h - i) * 3.0 / lam)
        Q = P[np.minimum(i, n - 1)].copy()
        m = 22                                                        # the crackling head (~65 px)
        if hi > 3:
            nb = np.roll(P, -1, 0)[np.minimum(i, n - 1)] - np.roll(P, 1, 0)[np.minimum(i, n - 1)]
            nl = np.hypot(nb[:, 0], nb[:, 1])[:, None] + 1e-6
            nrm = np.stack([-nb[:, 1], nb[:, 0]], 1) / nl
            g = np.clip((i - (h - m)) / m, 0.0, 1.0) ** 1.5
            Q += nrm * (rng.normal(0.0, 4.5 * sc, len(i)) * g)[:, None]
        segs += _poly_segs(Q, I, 1.05, closed=False)
        hp = Q[-1]
        tg = P[min(hi, n - 1)] - P[max(hi - 3, 0)]
        tg = tg / (np.hypot(*tg) + 1e-6)
        for q in range(2):                                            # two short forks off the head
            ang = math.atan2(tg[1], tg[0]) + (1 if q else -1) * rng.uniform(0.5, 1.4)
            Lf = rng.uniform(16.0, 40.0) * sc
            pts = [hp]
            for kk in range(1, 5):
                d = Lf * kk / 4
                pts.append(hp + d * np.array([math.cos(ang), math.sin(ang)]) + rng.normal(0.0, 3.0 * sc, 2))
            pts = np.array(pts)
            segs += _poly_segs(pts, hot * 0.55 * np.linspace(1.0, 0.3, len(pts)), 0.8)
    return segs


def ring_segs(c, r, f, u, seed=0, base=1.3, head=4.0):
    """the circle mask's rim as an arc of light: jittered circle, a bright head running round it as it grows"""
    n = max(48, int(2 * math.pi * r / 5.0))
    rng = np.random.default_rng(int(seed * 131 + math.floor(f * 2.0)) & 0x7fffffff)
    a = np.linspace(0, 2 * math.pi, n, endpoint=False)
    ph = rng.uniform(0.0, 2 * math.pi, 3)                               # a low wobble that hugs the mask edge (no saw teeth)
    rj = r + 2.2 * np.sin(5 * a + ph[0]) + 1.3 * np.sin(11 * a + ph[1]) + 0.7 * np.sin(23 * a + ph[2])
    P = np.stack([c[0] + rj * np.cos(a), c[1] + rj * np.sin(a)], 1)
    ah = (-math.pi / 2 + 2 * math.pi * 1.25 * u) % (2 * math.pi)
    da = np.abs((a - ah + math.pi) % (2 * math.pi) - math.pi)
    I = base + head * np.exp(-0.5 * (da / 0.35) ** 2)
    return _poly_segs(P, I, 1.2, closed=True)


# ================================================================== the plate under the word
CALM_FS = '''uniform sampler2D a; uniform sampler2D b; uniform sampler2D m; uniform float k; uniform float dim;
uniform float comp; uniform float glob;
void main(){ vec2 uv = UV(); vec3 A = texture(a, uv).rgb; vec3 B = texture(b, uv).rgb;
  float w = clamp(texture(m, uv).a, 0.0, 1.0) * k;
  float y = luma(B); vec3 Bd = B * (dim / (1.0 + comp * y));
  o = vec4(mix(A * mix(1.0, glob, k), Bd, w), 1.0); }'''


def calm(E, img, L, s=1.0, off=(0.0, 0.0), k=1.0, dim=0.85, comp=3.2, blur=9.0, spread=0.6, soft=0.32, glob=0.94):
    """blur + press down the plate around the word (a soft blob = the letters dilated by spread*cap, feathered by
    soft*cap): x dim / (1 + comp*y) -- dark plates keep most of their level, highlights are compressed (y = 1 -> 0.2), so a
    bright tube behind the letters becomes a soft glow (graded mean under the letters <= 0.30 on the v11 plates);
    the rest of the frame x glob. k = 0..1 (ramps in with the reveal)"""
    if k <= 0.002:
        return img
    cv = E.canvas(); c = cv.c
    _place(c, L, s, off)
    c.drawPath(L.path, paint(WHITE, 1.0))
    st = paint(WHITE, 1.0, spread * L.cap)
    st.setStrokeJoin(skia.Paint.kRound_Join)
    c.drawPath(L.path, st)
    c.restore()
    m = E.blur(cv.texture(), soft * L.cap * s)
    return run(E, CALM_FS, {'a': img, 'b': E.blur(img, blur), 'm': m},
               {'k': float(clamp01(k)), 'dim': float(dim), 'comp': float(comp), 'glob': float(glob)})


# ================================================================== detail layer: labels, the voltage ladder
def vfmt(v):
    if v < 1e3:
        return f'{v:.0f}{NS}V'
    if v < 1e4:
        return f'{v / 1e3:.1f}{NS}kV'
    if v < 1e6:
        return f'{v / 1e3:.0f}{NS}kV'
    if v < 1e7:
        k = f'{v / 1e3:,.0f}'.replace(',', NS)
        return f'{k}{NS}kV'
    return f'{v / 1e6:.0f}{NS}MV'


def tag(cv, lines, x, y, t=1e9, cps=2.0, size=22.0, color=WHITE, alpha=0.9, align='left', lead=1.45):
    """DM Mono sub-label, typed on at cps characters per frame (t = frames since it starts); lines: str or list"""
    lines = [lines] if isinstance(lines, str) else list(lines)
    done = 0
    for i, s in enumerate(lines):
        n = max(0, min(len(s), int(t * cps) - done))
        done += len(s)
        if n:
            TY.word(cv, s[:n], x, y + i * size * lead, size, 'mono', color, alpha, align, caps=False)


LAD_MAJ = ('100 V', '1 kV', '10 kV', '100 kV', '1 MV', '10 MV', '100 MV', '1 GV')


def ladder(cv, x0, x1, y, v, prev=None, p=1.0, fill=1.0, color=INK, live=None, alpha=1.0, marks=(), label=None, bar=10.0,
           size=24.0):
    """voltage ladder: log scale 100 V .. 1 GV (decade ticks + 2..9 minor ticks, drawn on left -> right by p), a bar
    filled to v (the new part prev -> v in `live` colour, grows with `fill`), a marker + readout over the value
    (counting up with fill), earlier steps (`marks`) as faint markers. y = top of the bar."""
    c = cv.c

    def X(val):
        return x0 + (x1 - x0) * (math.log10(max(val, 100.0)) - 2.0) / 7.0
    ink = paint(color, alpha)
    ticks = paint(color, 0.85 * alpha, 1.6)
    xp = x0 + (x1 - x0) * clamp01(p)
    for dec in range(2, 9):
        for m_ in range(1, 10):
            xv = X(m_ * 10.0 ** dec)
            if xv > xp + 0.5:
                break
            h = 18.0 if m_ == 1 else 8.0
            c.drawLine(xv, y - 6.0, xv, y - 6.0 - h, ticks)
    if X(1e9) <= xp + 0.5:
        c.drawLine(X(1e9), y - 6.0, X(1e9), y - 24.0, ticks)
    for i, s in enumerate(LAD_MAJ):
        xv = X(10.0 ** (i + 2))
        if xv <= xp + 0.5:
            TY.word(cv, s, xv, y + bar + 34.0, 20.0, 'mono', color, 0.75 * alpha, 'center', caps=False)
    if p <= 0.02:
        return
    lv = math.log10(max(prev or 100.0, 100.0)) + (math.log10(v) - math.log10(max(prev or 100.0, 100.0))) * clamp01(fill)
    vn = 10.0 ** lv
    xa, xv = X(prev or 100.0), X(vn)
    c.drawRect(skia.Rect.MakeLTRB(x0, y, min(xa, xv), y + bar), ink)
    if xv > xa:
        c.drawRect(skia.Rect.MakeLTRB(xa, y, xv, y + bar), paint(live or color, alpha))
    for mv in marks:                                                     # the steps already climbed
        xm = X(mv)
        tri = skia.Path(); tri.moveTo(xm - 7, y - 30); tri.lineTo(xm + 7, y - 30); tri.lineTo(xm, y - 19); tri.close()
        c.drawPath(tri, paint(color, 0.5 * alpha))
        if abs(xm - xv) > 60.0:
            TY.word(cv, LADDER_LAB.get(mv, vfmt(mv)), xm, y - 38.0, 20.0, 'mono', color, 0.55 * alpha, 'center', caps=False)
    tri = skia.Path(); tri.moveTo(xv - 11, y - 46); tri.lineTo(xv + 11, y - 46); tri.lineTo(xv, y - 30); tri.close()
    c.drawPath(tri, ink)
    TY.word(cv, label if (label and fill >= 0.999) else vfmt(vn), xv, y - 58.0, size, 'mono', color, alpha, 'center', caps=False)


def focus(cv, L, s, off, seed, color, alpha, n=150):
    """集中线 converging on the word, stopped by an ellipse round it (they never cross the letters)"""
    c = cv.c
    cx, cy = L.cx + off[0], L.cy + off[1]
    ov = skia.Path()
    ov.addOval(skia.Rect.MakeLTRB(cx - 0.60 * L.w * s, cy - 1.05 * L.cap * s, cx + 0.60 * L.w * s, cy + 1.05 * L.cap * s))
    c.save()
    c.clipPath(ov, skia.ClipOp.kDifference, True)
    IM.focus_lines(cv, cx, cy, seed, n=n, r_in=0.45 * L.cap * s, wmin=1.5, wmax=14.0, jitter=0.6, color=color, alpha=alpha)
    c.restore()


# ================================================================== the slab
def _slab_tex(E, L, s, off, alpha=1.0, lines=0.0, seed=0, line_col=WHITE, extrude=True):
    cv = E.canvas(); c = cv.c
    if lines > 0.01:                                                  # focus lines behind the block
        focus(cv, L, s, off, seed, line_col, 0.6 * lines)
    g = L.geo()
    _place(c, L, s, off)
    src = g['ext'] if extrude else L.path
    for dx, dy, a_, bl in ((0.45, 0.7, 0.35, 0.06 * L.cap), (0.22, 0.32, 0.75, 2.5)):   # wide soft + tight hard contact shadow
        sp = paint(INK, a_ * alpha)
        sp.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, bl))
        c.save(); c.translate(dx * L.depth, dy * L.depth); c.drawPath(src, sp); c.restore()
    if extrude:                                                       # the block: hard-edged sides, shaded by facing
        c.drawPath(g['ext'], paint(SIDE_D, alpha))
        c.save()
        c.clipPath(g['ext'], skia.ClipOp.kIntersect, True)
        for col, pth in g['sides']:
            pp = paint(col, alpha)
            pp.setAntiAlias(False)                                    # quads tile exactly; the clip gives the AA edge
            c.drawPath(pth, pp)
        c.restore()
        c.drawPath(g['lip'], paint(LIP, alpha))
    fp = skia.Paint(AntiAlias=True)
    fp.setShader(skia.GradientShader.MakeLinear([skia.Point(0, L.box[1]), skia.Point(0, L.box[3])],
                                                [_c4(FACE_TOP), _c4(FACE_BOT)], [0.0, 1.0]))
    fp.setAlphaf(float(alpha))
    c.drawPath(L.path, fp)
    c.drawPath(g['hi'], paint(WHITE, alpha))
    c.drawPath(g['lo'], paint(BEVEL_LO, 0.85 * alpha))
    c.restore()
    return cv.texture()


def _sweep_tex(E, L, s, off, p, width=0.55):
    """a soft slanted band of light crossing the block (p 0 -> 1 = left -> right): full on the face, a glint on the side"""
    g = L.geo()
    cv = E.canvas(); c = cv.c
    _place(c, L, s, off)
    bw = width * L.cap
    x = L.box[0] - 2 * bw + (L.box[2] - L.box[0] + 4 * bw) * p
    a = math.radians(-24.0)
    dx, dy = math.cos(a) * bw, math.sin(a) * bw
    cols = [_c4(WHITE, 0.0), _c4(_mix(WHITE, VT.ICE, 0.35), 1.0), _c4(WHITE, 0.0)]
    rect = skia.Rect.MakeLTRB(L.box[0] - 40, L.box[1] - 40, L.box[2] + L.depth + 40, L.box[3] + L.depth + 40)
    for clip, k in ((g['ext'], 0.75), (L.path, 1.0)):
        c.save()
        c.clipPath(clip, skia.ClipOp.kIntersect, True)
        sp = skia.Paint(AntiAlias=True)
        sp.setShader(skia.GradientShader.MakeLinear([skia.Point(x - dx, L.cy - dy), skia.Point(x + dx, L.cy + dy)],
                                                    cols, [0.0, 0.5, 1.0]))
        sp.setAlphaf(k)
        c.drawRect(rect, sp)
        c.restore()
    c.restore()
    return cv.texture()


# 「一点实拍火花」: SR.overlay = real sparks keyed off a temporal-median background (only the hot cores,
# feathered, no plate edges / scratches), recoloured white-hot -> amber -> sodium, their glow regenerated. On by default for
# 'slab' words landing in SPARK_SPAN (bars 2-8, A2: power-on, grid, welding); off for the hook cells, in the storm (no metal
# sparks in a thunderstorm) and in the sky after it. hero(sparks_k=number) forces it (0 = off).
SPARKS_K = 0.6
SPARK_SPAN = (0, 0)


def sparks(E, img, f, land, at, k=1.0, scale=0.7, rot=0.0, dur=18.0, speed=1.0, kind='burst', src=217, flip=True):
    """「一点实拍火花」 thrown off the slab's lower edge at `at` from the slam on: SR.overlay(kind) -- by default the sample
    plasma-cutting burst from source frame `src` (217 = just after its 7.1 s flash: distinct radial streaks, not a haze --
    the slam's own impact frames and shake cover the first frames anyway), mirrored so the fan sprays outwards, falling off
    linearly over `dur` frames"""
    if k <= 0 or f < land or f >= land + dur:
        return img
    from src.fx import series as SR
    return SR.overlay(E, img, f, kind, k=k, pos=at, scale=scale, rot=rot, f0=land, f1=land + dur, speed=speed, src=src,
                      flip=flip, decay=1.0, fout=1.0)


# ================================================================== masks (card / knock in and out)
MASK_FS = '''uniform sampler2D a; uniform sampler2D b; uniform int mode; uniform vec2 c; uniform float r; uniform vec2 n;
uniform float d; uniform float shk;
void main(){ vec2 p = gl_FragCoord.xy / u_s; vec4 A = texture(a, UV()); vec4 B = texture(b, UV()); float m; float sh = 0.0;
  if (mode == 0){ float q = length(p - c); m = clamp(r - q + 0.5, 0.0, 1.0); sh = exp(-max(q - r, 0.0) / 22.0) * (1.0 - m); }
  else { float q = dot(p, n) - d; m = clamp(q + 0.5, 0.0, 1.0); sh = exp(-max(-q, 0.0) / 22.0) * (1.0 - m); }
  A.rgb *= 1.0 - shk * sh;                                   // the card's edge throws a soft shadow on the footage
  o = mix(A, B, m); }'''


def _circle_in(E, bg, card, c, u):
    rmax = max(math.hypot(c[0] - x, c[1] - y) for x in (0.0, W) for y in (0.0, H)) + 30.0
    r = rmax * eio3(clamp01(u)) ** 0.6                                  # visible growth, no single-frame jump
    return run(E, MASK_FS, {'a': bg, 'b': card}, {'mode': 0, 'c': tuple(map(float, c)), 'r': float(r), 'n': (1.0, 0.0),
                                                  'd': 0.0, 'shk': 0.45}), r


def _wipe_out(E, bg, card, e, ang=-18.0):
    """diagonal wipe: the footage comes back from the left behind a slanted paper edge"""
    a = math.radians(ang)
    n = (math.cos(a), math.sin(a))
    ds = [x * n[0] + y * n[1] for x in (0.0, W) for y in (0.0, H)]
    d = lerp(min(ds) - 40.0, max(ds) + 40.0, eio3(clamp01(e)))
    return run(E, MASK_FS, {'a': bg, 'b': card}, {'mode': 1, 'c': (0.0, 0.0), 'r': 0.0, 'n': n, 'd': float(d), 'shk': 0.5})


STRIPS_FS = '''uniform sampler2D a; uniform sampler2D b; uniform float e; uniform float n; uniform float ww;
float ei(float x){ x = clamp(x, 0.0, 1.0); return x * x * x; }
void main(){ vec2 p = gl_FragCoord.xy / u_s; float k = floor(p.y / (1080.0 / n));
  float dir = mod(k, 2.0) < 0.5 ? -1.0 : 1.0; float del = 0.09 * mod(k * 2.0, n);
  float ek = clamp((e - del) / max(1.0 - 0.09 * (n - 1.0), 0.2), 0.0, 1.0);
  float s1 = dir * ww * ei(ek), s0 = dir * ww * ei(ek - 0.035);
  vec4 acc = vec4(0.0);
  for (int i = 0; i < 24; i++){ float sh = mix(s0, s1, (float(i) + 0.5) / 24.0); vec2 q = vec2(p.x - sh, p.y);
    vec4 B = (q.x < 0.0 || q.x > 1920.0) ? texture(a, UV()) : texture(b, q * u_s / u_size);
    acc += B; }
  o = acc / 24.0; }'''


def _strips_out(E, bg, card, e, n=5):
    return run(E, STRIPS_FS, {'a': bg, 'b': card}, {'e': float(clamp01(e)), 'n': float(n), 'ww': 1.15 * W})


# ================================================================== the card / knock layers
def _card_tex(E, L, s, off, f, start, land, lines, seed, knock=False, trace=True):
    cv = E.canvas(); c = cv.c
    g = L.geo()
    t = f - land
    if knock:
        _place(c, L, s, off)
        c.drawPath(g['paper'], paint(PAPER, 1.0))
        c.save()
        c.clipPath(L.path, skia.ClipOp.kIntersect, True)                # the paper's cut edge shadows the footage
        sp = paint(INK, 0.62)
        sp.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 0.018 * L.cap + 2.5))
        c.drawPath(g['hole_sh'], sp)
        c.restore()
        c.restore()
        if lines > 0.01:                                                # ink focus lines on the paper only
            c.save()
            c.clipPath(_xpath(L, s, off, g['paper']), skia.ClipOp.kIntersect, True)
            focus(cv, L, s, off, seed, INK, 0.9 * lines)
            c.restore()
        return cv.texture()
    c.drawRect(skia.Rect.MakeLTRB(-10, -10, W + 10, H + 10), paint(PAPER, 1.0))
    if lines > 0.01:
        focus(cv, L, s, off, seed, INK, 0.9 * lines)
    if t >= 0:
        _place(c, L, s, off)
        c.drawPath(L.path, paint(INK, 1.0))
        c.restore()
    elif trace:                                                         # the outline written in ink (the arc, negated)
        u = clamp01((f - start) / max(land - start, 1e-3))
        pen = paint(INK, 1.0, 2.6)
        pen.setStrokeCap(skia.Paint.kRound_Cap); pen.setStrokeJoin(skia.Paint.kRound_Join)
        rng = np.random.default_rng(int(seed * 7919 + math.floor(f * 2.0)) & 0x7fffffff)
        for C in L.contours:
            n = len(C)
            hi = int((1.0 - (1.0 - u) ** 2.0) * n)
            if hi < 2:
                continue
            P = _xf(L, s, off, C[:hi + 1])
            m = min(18, len(P))
            P[-m:] += rng.normal(0.0, 2.2, (m, 2)) * (np.linspace(0, 1, m) ** 1.5)[:, None]
            path = skia.Path(); path.moveTo(*P[0])
            for q in P[1:]:
                path.lineTo(float(q[0]), float(q[1]))
            c.drawPath(path, pen)
    return cv.texture()


# ================================================================== the one call
def hero(E, img, f, text, start, land, leave, style='slab', label=None, volts=None, prev=None, marks=None, value=None,
         width=None, x=960.0, cy=None, align='center', key=None, at=None, exit=None, exit_dir=1.0, n_out=6, seed=1,
         push=0.035, shake=None, sweep=(6.0, 26.0), sparks_k=None, ladder_on=True, calm_k=1.0, calm_dim=0.85, fill_at=None,
         fill_zoom=1.08, extrude=True, reveal='run', focus=None):
    """the whole life of a hero word (see the module doc). Returns the composite; img is returned untouched outside
    [start, leave). exit None = per style ('slab' 'whip' | 'pull'; 'card' 'wipe'; 'knock' 'strips').
    label = DM Mono footnote (str or lines); volts / prev / marks = the voltage ladder (None = no ladder);
    value = the ladder's final readout text (default: the hero text); prev / marks None = automatic from LADDER (the
    style 'slam' = a HOOK cell word: no reveal (start is ignored), slams on `land` (1.25 -> 1.0) with a short aftermath,
    exit 'cut' (gone on `leave`). exit 'cut' works for every style. extrude False = flat face (no block).
    reveal 'run' = one arc head runs the outline letter by letter; 'all' = every contour at once (v1). sparks_k = the real-spark burst at the
    slam (SR.overlay): None = auto (SPARKS_K on 'slab' words landing in SPARK_SPAN = A2 bars 2-8; off in the
    hook cells, the storm and after), a number forces it (0 = off).
    When timeline.SERIES_FX != 'volt': no electric arc anywhere -- 'slab' is revealed SOLID (the block
    shows through a growing soft circle from `start`, like the card, then slams on `land`), the card / knock circle has no arc
    rim, the knock holes no arc edge; focus = the focus lines on the slam (None = 0 unless SERIES_FX == 'volt', then 1)."""
    if f < start - 1e-6 or f >= leave:
        return img
    st = style if style in STYLES else 'slab'
    cell = st == 'slam'
    if sparks_k is None:                                                # auto: a little real spark on A2 slab slams only
        sparks_k = SPARKS_K if (st == 'slab' and SPARK_SPAN[0] <= land < SPARK_SPAN[1]) else 0.0
    if cell:
        start = land
        if f < land - 1e-6:
            return img
    if width is None:
        width = {'slab': 0.62, 'card': 0.62, 'knock': 0.80, 'slam': 0.60}[st]
        if len(text) > 10:                                              # long numbers (300 000 000 V) get wider
            width = max(width, 0.86)
    if cy is None:
        cy = {'slab': 650.0, 'card': 520.0, 'knock': 500.0, 'slam': 540.0}[st]
    if volts:
        below = [v for v, _ in LADDER if v < volts * 0.999]
        prev = (below[-1] if below else None) if prev is None else prev
        marks = tuple(below) if marks is None else marks
    marks = marks or ()
    L = lay(text, key, float(width), None, float(x), float(cy), align)
    t = f - land
    ex = exit or {'slab': 'whip', 'card': 'wipe', 'knock': 'strips', 'slam': 'cut'}[st]
    e = 0.0 if ex == 'cut' else clamp01((f - (leave - n_out)) / float(n_out))   # exit progress
    # scale: slam 1.12 -> 1.0 in 3 frames (cells 1.25), then a slow push through the hold
    if t < 0:
        s = 1.0
    else:
        s = (1.0 + (0.25 if cell else 0.12) * (1.0 - eo3(min(t, 3.0) / 3.0))) * \
            (1.0 + (0.02 if cell else push) * clamp01(t / max(leave - land, 1.0)))
    off = [0.0, 0.0]
    zb = ([0.08, 0.035, 0.01] if cell else [0.065, 0.03, 0.01])[int(t)] if 0 <= t < 3 else 0.0   # slam zoom blur
    arcs = _arcs_on()
    fk = (1.0 if arcs else 0.0) if focus is None else float(focus)
    lines = fk * [1.0, 0.6, 0.28][int(t)] if 0 <= t < 3 else 0.0
    sh_amp = shake if shake is not None else {'slab': 16.0, 'card': 12.0, 'knock': 14.0, 'slam': 12.0}[st]
    ulab = t + 2.0                                                      # labels start typing 2 frames before the slam
    if st in ('slab', 'slam'):
        out = _slab(E, img, f, L, s, off, t, start, land, leave, e, ex, exit_dir, zb, lines, seed,
                    None if cell else sweep, calm_k * (0.75 if cell else 1.0), calm_dim, sparks_k, label, volts, prev,
                    marks, value, ladder_on, ulab, extrude=extrude, reveal=reveal, cell=cell, arcs=arcs)
    else:
        out = _paper(E, img, f, L, s, off, t, start, land, leave, e, ex, zb, lines, seed, st == 'knock', at, label, volts,
                     prev, marks, value, ladder_on, ulab, fill_at, fill_zoom, arcs=arcs)
    if t >= 0 and sh_amp > 0:
        dk = math.exp(-t / 3.2)
        dx = sh_amp * dk * math.sin(t * 2.3 + seed)
        dy = 0.75 * sh_amp * dk * math.cos(t * 2.9 + 1.3 * seed)
        rz = 0.45 * dk * math.sin(t * 1.9 + 0.7 * seed)                 # degrees
        if abs(dx) + abs(dy) > 0.05:
            sc = 1.0 + max(abs(dx) / 960.0, abs(dy) / 540.0) + abs(math.radians(rz)) * W / H + 0.003
            out = E.affine(out, sc, rz, None, (dx, dy))
    return out


def _labels(cv, L, s, f, land, label, volts, prev, marks, value, ladder_on, ulab, color, live, y_lad, x0, x1, e_alpha=1.0,
            dur=40.0):
    if label and ulab >= 0:
        tag(cv, label, L.box[0] + 4.0, L.cy - 0.5 * L.cap * s - 34.0, t=ulab, cps=1.6, color=color, alpha=0.92 * e_alpha)
    if ladder_on and volts and f >= land - 1:
        tl = f - land
        span = max(4.0, min(16.0, 0.42 * dur))                          # the climb fits short holds (HOOK cells) too
        ladder(cv, x0, x1, y_lad, volts, prev, p=eo3(clamp01((tl + 1.0) / min(8.0, span))),
               fill=eio3(clamp01((tl - 0.25 * span) / span)),
               color=color, live=live, alpha=e_alpha, marks=marks, label=value or L.text)


def _slab(E, img, f, L, s, off, t, start, land, leave, e, ex, exit_dir, zb, lines, seed, sweep, calm_k, calm_dim, sparks_k,
          label, volts, prev, marks, value, ladder_on, ulab, extrude=True, reveal='run', cell=False, arcs=True):
    k_calm = calm_k * (eo3(clamp01((f - start) / max(land - start, 1.0))) if t < 0 else 1.0) * (1.0 - eo3(e))
    alpha = 1.0
    if ex == 'pull':
        s *= lerp(1.0, 0.42, ei3(e))
        zb = max(zb, 0.10 * ei3(e))
        alpha = 1.0 - ei3(e) ** 3
    elif ex == 'whip' and e > 0:                                        # whip: thrown out sideways
        off[0] = exit_dir * 1.15 * W * ei3(e)
    base = calm(E, img, L, s, off, k_calm, dim=calm_dim)
    if t >= 0:
        tex = _slab_tex(E, L, s, tuple(off), alpha, lines, seed, extrude=extrude)
        if zb > 0:
            tex = fx.zoom_blur(E, tex, L.cx + off[0], L.cy + off[1], zb)
        if ex == 'whip' and e > 0:
            v = exit_dir * 1.15 * W * 3.0 * e * e / 6.0
            tex = fx.dir_blur(E, tex, 0.5 * v, 0.0)
        out = E.over(base, tex)
        if sweep and sweep[0] <= t <= sweep[1] + 2:
            sw = _sweep_tex(E, L, s, tuple(off), eio3(clamp01((t - sweep[0]) / (sweep[1] - sweep[0]))))
            out = E.add(out, sw, 0.85 * alpha)
            out = E.add(out, E.blur(sw, 0.10 * L.cap), 0.55 * alpha)
        out = sparks(E, out, f, land, (L.box[0] + 0.78 * L.w, L.box[3] + 0.1 * L.cap), k=sparks_k * alpha)
    elif arcs or cell or land <= start:
        out = base
    else:                                                               # p2 11-05 solid reveal (no arc outline): the block
        u = clamp01((f - start) / max(land - start, 1.0))               # shows through a growing soft circle, then slams
        tex = _slab_tex(E, L, 1.0, tuple(off), 1.0, 0.0, seed, extrude=extrude)
        c = (L.cx + off[0], L.cy + off[1])
        r = 1.12 * math.hypot(0.5 * L.w, 0.75 * L.cap + L.depth) * eio3(u) ** 0.6     # the word's own circle, not the frame's
        out = run(E, MASK_FS, {'a': base, 'b': E.over(base, tex)}, {'mode': 0, 'c': tuple(map(float, c)), 'r': float(r),
                                                                      'n': (1.0, 0.0), 'd': 0.0, 'shk': 0.35})
    if arcs:
        segs = arc_segs(L, f, start, land, s, tuple(off), seed, after=5.0 if cell else 7.0, mode=reveal)
        out = light(E, out, segs, glow=0.32, seed=f * 0.618 + seed)
    cv = E.canvas()
    _labels(cv, L, s, f, land, label, volts, prev, marks, value, ladder_on, ulab, PAPER, None,
            L.box[3] + 0.5 * (s - 1.0) * L.cap + L.depth + 96.0, L.box[0], L.box[2], e_alpha=1.0 - eo3(e), dur=leave - land)
    return E.over(out, cv.texture())


def _paper(E, img, f, L, s, off, t, start, land, leave, e, ex, zb, lines, seed, knock, at, label, volts, prev, marks,
           value, ladder_on, ulab, fill_at=None, fill_zoom=1.08, arcs=True):
    u = clamp01((f - start) / max(land - start, 1.0))
    c = at or (L.cx, L.cy)
    card = _card_tex(E, L, s, tuple(off), f, start, land, lines, seed, knock=knock)
    if knock:
        z = fill_zoom * (1.0 + 0.05 * clamp01((f - land) / max(leave - land, 1.0)))
        fa = fill_at or (L.cx, L.cy)
        fill = E.affine(img, z, 0.0, fa, (L.cx - fa[0], L.cy - fa[1]))
        fill = run(E, 'uniform sampler2D a; void main(){ vec3 c = texture(a, UV()).rgb; '
                      'o = vec4(c * 1.12 / (1.0 + 0.35 * luma(c)), 1.0); }', {'a': fill})
        if zb > 0:
            fill = fx.zoom_blur(E, fill, L.cx, L.cy, zb * 1.4)
        comp = E.over(fill, card)
    else:
        if zb > 0:
            card = fx.zoom_blur(E, card, L.cx, L.cy, zb)
        comp = E.over(img, card)
    cv = E.canvas()
    _labels(cv, L, s, f, land, label, volts, prev, marks, value, ladder_on, ulab, INK, None,
            min(L.box[3] + 150.0, 930.0), 240.0, 1680.0, dur=leave - land)
    comp = E.over(comp, cv.texture())
    if knock and arcs:                                                  # the rims of the holes carry the arc ('volt' only)
        segs = arc_segs(L, f, start, land, s, tuple(off), seed, hot=5.0, cool=1.1)  # (mode 'run': one head)
        comp = light(E, comp, segs, glow=0.28, seed=f * 0.618 + seed)
    if t < 0:                                                           # circle in (>= 6 frames), its rim an arc of light
        out, r = _circle_in(E, img, comp, c, u)
        if arcs:
            out = light(E, out, ring_segs(c, r, f, u, seed), glow=0.2, seed=f * 0.618 + seed)
        return out
    if e <= 0 or ex == 'cut':
        return comp
    if ex == 'strips':
        return _strips_out(E, img, comp, e)
    return _wipe_out(E, img, comp, e)


主角字 = hero
标签 = tag
