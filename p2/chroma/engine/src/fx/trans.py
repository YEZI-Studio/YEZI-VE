# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""TX · 跨剪口的镜头运动转场库。

每个剪口 = 一段跨过剪口的连续镜头运动：上一镜加速（ease-in）冲出、下一镜同方向同速度冲进来再减速（ease-out），速度在剪口那一
帧最大、两边速度接得上；运动模糊沿真实运动路径积分（线性光，180° 快门 = 半帧位移），长度和速度一致；全程不加白 / 黑，亮度跳变
只来自闪电本身（boltwipe / impact）。剪口藏在运动模糊最大的那两帧里。

    from src.fx import trans as TR
    CUTS = [TR.Cut(TL.F(6), 'whip', dir='R'),                       # 下一镜从这一帧开始
            TR.Cut(TL.F(6, 2), 'push', at=(1290, 840), at_in=(960, 400)),
            TR.Cut(TL.F(6, 3), 'boltwipe', n_out=6, n_in=7, dir='R')]

    @shot(subframes=1)                       # 运动模糊由本库在一帧内积分（加 subframes 只会更糊）
    def my_shot(E, T):
        w = TR.window(CUTS, T.F)             # -> (cut, side, u) | None ；side 'out'（剪口前）/ 'in'（剪口后），u 0 -> 1
        if w is None:
            return render(E, T, shot_at(T.F))
        cut, side, u = w
        if TR.needs_both(cut):               # boltwipe / occlude / light：两边画面同时出现
            a, b = render(E, T, shot_before(cut)), render(E, T, shot_after(cut))
            return TR.mix(E, a, b, cut, T.F, T.f)
        return TR.transform(E, render(E, T, shot_at(T.F)), cut, side, u)
    # 省事写法：img = TR.apply(E, img, CUTS, T.F)（单边 kind）；冲击帧：TR.impact(E, img_out, img_in, 329, T.f, at, 'neg')

Cut(frame, kind, n_out=5, n_in=6, dir=None, at=None, strength=1.0, **kw)
  frame     剪口那一帧（下一镜的第一帧）。out 窗口 = [frame - n_out, frame)，in 窗口 = [frame, frame + n_in)。
  strength  运动量的倍数（速度 / 推进量 / 角度）。窗口重叠时 window() 自动按比例缩短两边（cut.eff = 实际的 (n_out, n_in)）。
  dir       whip / smear / ramp：**相机**甩的方向 'L' 'R' 'U' 'D'（或角度°、向量）——'R' = 镜头往右甩、画面往左拖走、下一镜从右边
            冲进来（和 v7 m2_storm.Whip 同一约定）。occlude / boltwipe / light：遮挡物 / 闪电 / 光带在屏幕上**走的方向**（'R' =
            从左扫到右，扫过的左边先露出下一镜）。spin：'cw' / 'ccw'（画面顺 / 逆时针转）。
  at        push / pull / shape：上一镜里的屏幕点（闪电落点、光点、城市灯）；at_in=下一镜里对位的点（默认 = at）。
  kw        whip: wob（0 = 不晃，默认 1）· push/pull: zoom（倍数，默认 1.6 / 1.55）· spin: deg（默认 150）· shape: b=下一镜的点
            （= at_in）、zoom（默认 1.18）· smear: trail（帧，默认 2.8）、step（抽帧，帧）· ramp: base='whip'|'push'|'pull'|'spin'
            （+ base 的参数）、rate（素材变速峰值，默认 3）· occlude: width（遮挡带宽 / 屏宽，默认 0.82）· boltwipe: seed、
            illum（补光，默认 0.9）· 全部：shutter（快门，帧；默认 0.5 = 180°）、edge='mirror'|'clamp'（画面外怎么补）、pan、cap（模糊帧均值
            ≤ cap x 清晰帧均值，默认 1.05；0 = 关）。

建议（60 fps，1 拍 = 20.56 帧，8 分 = 10.28 帧）：
  kind      适合的剪点                                       n_out / n_in
  whip      8 分音符上的方向剪（方向跟画面里的运动走）        4–6 / 5–7     中间 2 帧几乎全是拖影（≤ 0.35 屏宽）
  push      小节第 1 拍 / 最强 808：推进闪电落点 / 光点        5–6 / 6–8     1 → 1.6 倍 + 径向模糊；下一镜从 ~1.5 倍落回 1
  pull      段落收束、空间变大（云层上、地球、太空）           5–6 / 6–8     画面缩到 0.65（边外用镜像补，全在模糊里）
  smear     碎 8 分、高能连切里的短接                          3–4 / 3–5     拖影带着下一镜进来，位移小、拖尾长
  spin      全片 ≤ 2 次：DROP / 最后一击附近                   6–8 / 7–9     150° + 推近 1.3
  ramp      过门 / 升起来那一下（加速冲过剪口）                8–10 / 9–12   陡加速 + 360° 快门（帧混合）+ 慢回来
  occlude   换场景 / 换段落（暗云从镜头前扫过）                6–7 / 6–8     暗云带 0.82 屏宽，切口藏在最厚处
  boltwipe  小节第 1 拍的大卡点（一道闪电横过画面）            6–7 / 6–8     切口边藏在闪电最亮的通道里，2–3 个回击台阶
  shape     闪电接闪电、球接球（形状对位）                     5 / 6         a → b 对位 + 1.18 倍小推进
  light     偶尔一次：一束冷光扫过（非闪白，光带 ≈ 0.12 屏宽）  6 / 7
  impact    只用在 DROP 329 和最后一击 1645（TR.impact）       3 帧蓄力 + 6 帧：下一镜被自己的闪电照亮，上一镜碎块飞散 + 冲击波

渲染：TR.window / transform / mix 只依赖 (cut, F)——任何一帧单独渲染结果一样。每个单边转场帧 = 2 次全屏 pass（路径积分 ≤ 32
样本 + 局部平滑），boltwipe 帧 ≈ 一次 Discharge.draw。python -m src.fx.trans check  →  自检（速度连续、亮度不跳、性能）。
"""
import math
import numpy as np
from src.kit.core import run, mat3s, rnd, clamp01, INK, PAPER, ramp_tex
from src.fx import volt as VT

W, H = 1920.0, 1080.0
KINDS = ('whip', 'push', 'pull', 'smear', 'spin', 'ramp', 'occlude', 'boltwipe', 'shape', 'light')
BOTH = ('occlude', 'boltwipe', 'light')
SINGLE = tuple(k for k in KINDS if k not in BOTH)
SHUTTER = 0.5                       # 180 degrees: blur = half a frame of motion (same as the engine)

# per kind: (p_out, q_in) = ease-in exponent of the out side (velocity ~ u^p) and ease-out exponent of the in side
# (velocity ~ (1-u)^q). The velocity at the cut is the SAME on both sides (amounts follow from it).
PROFILE = {'whip': (2.0, 3.0), 'push': (2.0, 3.0), 'pull': (2.0, 3.0), 'smear': (1.6, 2.6), 'spin': (2.2, 3.0),
           'shape': (2.0, 2.6), 'ramp': (4.0, 5.0), 'occlude': (2.0, 2.4), 'boltwipe': (1.8, 2.4), 'light': (2.0, 2.4)}
_DIRS = {'R': (1.0, 0.0), 'L': (-1.0, 0.0), 'U': (0.0, -1.0), 'D': (0.0, 1.0)}


def _dvec(d, default='R'):
    """'L' 'R' 'U' 'D' / 'left' ... / angle in degrees (0 = right, 90 = down) / (dx, dy) -> unit vector (screen, y down)"""
    if d is None:
        d = default
    if isinstance(d, str):
        k = d.strip().upper()
        k = {'LEFT': 'L', 'RIGHT': 'R', 'UP': 'U', 'DOWN': 'D'}.get(k, k[:1])
        if k not in _DIRS:
            raise ValueError(f'dir {d!r}: use L R U D, an angle or a vector')
        return np.array(_DIRS[k])
    if isinstance(d, (int, float)):
        a = math.radians(float(d))
        return np.array([math.cos(a), math.sin(a)])
    v = np.array(d, np.float64)
    return v / max(1e-9, float(np.hypot(*v)))


class Cut:
    """one designed cut (see the module doc). frame = the first frame of the next shot."""

    def __init__(self, frame, kind, n_out=5, n_in=6, dir=None, at=None, strength=1.0, **kw):
        if kind not in KINDS:
            raise ValueError(f'kind {kind!r} not in {KINDS}')
        self.frame, self.kind = int(round(frame)), kind
        self.n_out, self.n_in = max(1, int(n_out)), max(1, int(n_in))
        self.dir, self.strength, self.kw = dir, float(strength), dict(kw)
        self.at = None if at is None else (float(at[0]), float(at[1]))
        self.seed = int(kw.get('seed', self.frame * 7 + 3))
        self.eff = (self.n_out, self.n_in)
        self._m = {}

    def __repr__(self):
        x = f' dir={self.dir}' if self.dir is not None else ''
        x += f' at={self.at}' if self.at else ''
        return f'Cut({self.frame}, {self.kind!r}, n_out={self.eff[0]}, n_in={self.eff[1]}{x})'

    def span(self):
        """[first frame of the out window, first frame after the in window)"""
        return self.frame - self.eff[0], self.frame + self.eff[1]

    @property
    def base(self):
        """the camera move behind a ramp (kw base), else the kind itself"""
        return self.kw.get('base', 'whip') if self.kind == 'ramp' else self.kind


# ------------------------------------------------------------------ windows
def _trim(cuts):
    """sorted cuts with .eff set: neighbouring windows never overlap (both sides shrink in proportion)"""
    cs = sorted(cuts, key=lambda c: c.frame)
    for c in cs:
        c.eff = (c.n_out, c.n_in)
    for a, b in zip(cs, cs[1:]):
        gap = b.frame - a.frame
        ai, bo = a.eff[1], b.eff[0]
        if ai + bo > gap:
            if gap < 2:
                ai2, bo2 = max(1, gap), 0
            else:
                ai2 = min(gap - 1, max(1, int(round(gap * ai / float(ai + bo)))))
                bo2 = gap - ai2
            a.eff, b.eff = (a.eff[0], ai2), (bo2, b.eff[1])
    return cs


def side_of(cut, F):
    """('out' | 'in', u) of film frame F (int, or float = sub-frame) for this cut, or (None, None) outside its window"""
    t = float(F)
    no, ni = cut.eff
    c = cut.frame - 0.5                                   # the cut sits between frame-1 and frame
    if c - no <= t < c and no > 0:
        return 'out', (t - (c - no)) / no
    if c <= t < c + ni:
        return 'in', (t - c) / ni
    return None, None


def window(cuts, F):
    """-> (cut, side, u) when frame F is inside a cut's window, else None. side 'out' = before the cut (u 0 -> 1 faster and
    faster), 'in' = from the cut (u 0 -> 1 slower and slower). Overlapping windows are trimmed (cut.eff)."""
    for c in _trim(cuts):
        s, u = side_of(c, F)
        if s is not None:
            return c, s, u
    return None


def needs_both(cut):
    """True for the cuts whose window shows both pictures at once (boltwipe / occlude / light): call TR.mix"""
    return cut.kind in BOTH


def apply(E, img, cuts, F):
    """single-side cuts in one line: img unchanged outside every window"""
    w = window(cuts, F)
    if w is None:
        return img
    c, s, u = w
    return transform(E, img, c, s, u)


# ------------------------------------------------------------------ motion profiles
def _po(u, p):
    """out side: 0 -> 1, velocity ~ u^p (ease in), slope p+1 at u = 1; linear past 1 (exposure taps beyond the cut)"""
    if u <= 0.0:
        return 0.0
    if u >= 1.0:
        return 1.0 + (p + 1.0) * (u - 1.0)
    return u ** (p + 1.0)


def _gi(u, q):
    """in side: the part of the move still to come, 1 -> 0, velocity ~ (1-u)^q (ease out); linear before 0"""
    if u >= 1.0:
        return 0.0
    if u <= 0.0:
        return 1.0 - (q + 1.0) * u
    return (1.0 - u) ** (q + 1.0)


def _ks(cut):
    """(k_out, k_in): amount = peak velocity x k (so both sides meet the cut at the same speed)"""
    p, q = PROFILE[cut.kind]
    no, ni = cut.eff
    return max(no, 1) / (p + 1.0), max(ni, 1) / (q + 1.0)


def _sweep(cut, tau, c0, c1):
    """position c0 -> c1 over the whole window (two-sided kinds), fastest at the cut, velocity continuous"""
    p, q = PROFILE[cut.kind]
    no, ni = cut.eff
    ko, ki = _ks(cut)
    cc = c0 + (c1 - c0) * ko / (ko + ki)
    if tau < 0:
        return c0 + (cc - c0) * _po((tau + no) / max(no, 1), p)
    return c1 - (c1 - cc) * _gi(tau / ni, q)


def _sweep_v(cut, tau, c0, c1):
    return _sweep(cut, tau + 0.25, c0, c1) - _sweep(cut, tau - 0.25, c0, c1)


# ------------------------------------------------------------------ the camera of one side
def _Hm(ci, cs, z, roll):
    """image point ci lands on screen point cs, zoom exp(z) and roll (deg) about it -> 3x3 (image px -> screen px, 1080p)"""
    s = math.exp(z)
    c, n = math.cos(math.radians(roll)), math.sin(math.radians(roll))
    return np.array([[s * c, -s * n, cs[0] - s * (c * ci[0] - n * ci[1])],
                     [s * n, s * c, cs[1] - s * (n * ci[0] + c * ci[1])],
                     [0.0, 0.0, 1.0]])


class _Motion:
    """pose(tau) of ONE side: tau = frames from the cut (frame - 0.5); out side tau in [-n_out, 0), in side [0, n_in)"""

    def __init__(self, cut, side):
        self.cut, self.side = cut, side
        kind = cut.base
        k = cut.strength
        kw = cut.kw
        self.p, self.q = PROFILE[cut.kind]
        no, ni = cut.eff
        self.no, self.ni = max(no, 1), max(ni, 1)
        ko, ki = _ks(cut)
        rk = 1.18 if cut.kind == 'ramp' else 1.0
        C = (W / 2, H / 2)
        sd = rnd(cut.seed, 'wob')
        sg = 1.0 if rnd(cut.seed, 'sg') < 0.5 else -1.0
        self.ci_o = self.ci_i = C
        self.cs = lambda f, side: C
        self.tr = (0.0, 0.0)                        # (out amount, in amount) of translation along v
        self.v = np.zeros(2)
        self.zo = self.zi = 0.0                     # log zoom amounts
        self.ro = self.ri = 0.0                     # roll amounts (deg)
        self.zsign_in = 1.0                         # in side: +1 = starts magnified, -1 = starts small
        self.wob = (0.0, 0.0)                       # (perpendicular fraction, roll deg) per unit of travel
        if kind in ('whip', 'smear'):
            vmax = (0.70 if kind == 'whip' else 0.10) * W * k * rk     # px / frame at the cut
            self.v = -_dvec(cut.dir, 'R')                                # the picture moves against the camera
            self.tr = (vmax * ko, vmax * ki)
            w = float(kw.get('wob', 1.0))
            if kind == 'whip':
                self.wob = (sg * (0.03 + 0.04 * sd) * w, -sg * (0.8 + 0.9 * sd) * w)
        elif kind in ('push', 'pull', 'shape'):
            dz = {'push': 1.6, 'pull': 1.55, 'shape': 1.18}[kind]
            zo = math.log(float(kw.get('zoom', dz))) * k * rk
            zi = zo * ki / ko
            a = cut.at or C
            b = tuple(kw.get('at_in', kw.get('b', a)))
            if kind == 'pull':
                self.zo, self.zi = -zo, zi                               # out shrinks, in starts magnified (still pulling back)
            else:
                self.zo, self.zi = zo, zi                                # out grows, in from magnified down to 1 (brief)
            self.ci_o, self.ci_i = a, b
            m = np.array(a) + (np.array(b) - np.array(a)) * ko / (ko + ki)   # where a and b meet on screen at the cut
            self.m = m
            self.ro = sg * (0.8 + 1.4 * sd) * float(kw.get('wob', 1.0)) * (0.5 if kind == 'shape' else 1.0)
            self.ri = self.ro * ki / ko
        elif kind == 'spin':
            d = cut.dir if isinstance(cut.dir, str) else 'cw'
            sgn = -1.0 if d.lower().startswith('cc') else 1.0
            ro = float(kw.get('deg', 150.0)) * k * rk
            self.ro, self.ri = sgn * ro, sgn * ro * ki / ko
            zo = math.log(float(kw.get('zoom', 1.3)))
            self.zo, self.zi = zo, zo * ki / ko
            a = cut.at or C
            b = tuple(kw.get('at_in', a))
            self.ci_o, self.ci_i, self.m = a, b, np.array(a)
        self.kind = kind

    def pose(self, tau):
        """3x3 image -> screen (1080p px) at time tau (frames from the cut)"""
        if self.side == 'out':
            f = _po((tau + self.no) / self.no, self.p)
            sgn = 1.0
        else:
            f = _gi(tau / self.ni, self.q)
            sgn = -1.0
        kind = self.kind
        if kind in ('whip', 'smear'):
            amt = self.tr[0] * f if sgn > 0 else -self.tr[1] * f
            pv = np.array([-self.v[1], self.v[0]])
            d = self.v * amt + pv * (self.wob[0] * amt * (abs(amt) / W) ** 0.15)    # a slightly tilted, organic path
            roll = self.wob[1] * amt / W
            return _Hm((W / 2, H / 2), (W / 2 + d[0], H / 2 + d[1]), 0.0, roll)
        if kind in ('push', 'pull', 'shape', 'spin'):
            ci = self.ci_o if sgn > 0 else self.ci_i
            cs = np.array(ci) + (self.m - np.array(ci)) * f            # a -> m (out), m -> b (in): the eye stays on it
            if sgn > 0:
                z, roll = self.zo * f, self.ro * f
            else:
                z, roll = self.zi * f, -self.ri * f                    # same turning direction through the cut
            if kind == 'spin':                                         # zoom so the turned frame keeps covering the screen
                z = max(z, 0.92 * math.log(_cover(roll)))
            return _Hm(ci, cs, z, roll)
        return np.eye(3)


def _cover(deg):
    """zoom that keeps a 16:9 frame turned by deg covering the 16:9 screen (about the centre)"""
    c, s = abs(math.cos(math.radians(deg))), abs(math.sin(math.radians(deg)))
    return max(1.0, c + (H / W) * s, s * (W / H) + c)


def _motion(cut, side):
    key = (side, cut.eff)
    m = cut._m.get(key)
    if m is None:
        m = cut._m[key] = _Motion(cut, side)
    return m


# ------------------------------------------------------------------ path-integrated motion blur
WARP_FS = '''uniform sampler2D src; uniform mat3 Hi[33]; uniform float wt[32]; uniform int n; uniform vec2 ss; uniform int edge;
uniform float seed;
vec2 mirror2(vec2 p, vec2 s){ vec2 m = mod(p, 2.0 * s); return s - abs(m - s); }
float ign(vec2 p){ return fract(52.9829189 * fract(dot(p, vec2(0.06711056, 0.00583715)))); }
void main(){
  vec2 x = gl_FragCoord.xy;
  float j = n > 1 ? fract(ign(x) + seed) : 0.5;            // stratified: one jittered tap per slice of the exposure
  vec4 acc = vec4(0.0); float ws = 0.0;
  for (int i = 0; i < 32; i++){
    if (i >= n) break;
    vec3 qa = Hi[i] * vec3(x, 1.0); vec3 qb = Hi[i + 1] * vec3(x, 1.0);
    vec2 p = mix(qa.xy / qa.z, qb.xy / qb.z, j);
    p = edge == 0 ? mirror2(p, ss) : clamp(p, vec2(0.5), ss - vec2(0.5));
    vec4 c = texture(src, p / ss);
    acc += vec4(lin(c.rgb), c.a) * wt[i]; ws += wt[i];
  }
  acc /= max(ws, 1e-6);
  o = vec4(enc(acc.rgb), acc.a);
}'''

SMOOTH_FS = '''uniform sampler2D a; uniform mat3 S; uniform int n;
void main(){
  vec2 x = gl_FragCoord.xy; vec3 q = S * vec3(x, 1.0); vec2 d = q.xy / q.z - x;   // local screen motion of one slice
  vec4 acc = vec4(0.0);
  for (int i = 0; i < 12; i++){
    if (i >= n) break;
    float t = (float(i) + 0.5) / float(n) - 0.5;
    vec4 c = texture(a, (x + d * t) / u_size);
    acc += vec4(lin(c.rgb), c.a);
  }
  acc /= float(n);
  o = vec4(enc(acc.rgb), acc.a);
}'''

_PROBE = np.array([[0, 0], [W, 0], [0, H], [W, H], [W / 2, H / 2], [W / 2, 0], [0, H / 2], [W, H / 2], [W / 2, H]], np.float64)


def _apply_pts(M, P):
    q = np.c_[P, np.ones(len(P))] @ M.T
    return q[:, :2] / q[:, 2:3]


def _path_len(poses):
    """max screen travel (1080p px) of the probe points along a list of poses (consecutive differences summed)"""
    Hi0 = np.linalg.inv(poses[0])
    src = _apply_pts(Hi0, _PROBE)                   # image points that sit on the probes at the first tap
    pts = [_apply_pts(M, src) for M in poses]
    L = np.zeros(len(_PROBE))
    for a, b in zip(pts, pts[1:]):
        L += np.hypot(*(b - a).T)
    return float(L.max())


def warp(E, img, pose_fn, t0, t1, weights=None, edge='mirror', nmax=32):
    """the picture seen through a moving camera over the exposure [t0, t1] (pose_fn(t) -> 3x3 image -> screen, 1080p px),
    averaged in linear light with a stratified jittered path integral + a local smoothing pass (no ghost copies).
    weights(t) (optional) weights the exposure (smear trails). Returns a new texture at the output size."""
    probe = [pose_fn(t0 + (t1 - t0) * i / 4.0) for i in range(5)]
    L = _path_len(probe) * E.s
    n = int(min(nmax, max(1, math.ceil(L / 14.0)))) if L > 0.6 else 1
    ts = [t0 + (t1 - t0) * i / n for i in range(n + 1)] if n > 1 else [0.5 * (t0 + t1)] * 2
    sc_o = np.diag([E.s, E.s, 1.0])
    sc_i = np.diag([img.w / W, img.h / H, 1.0])
    His = [sc_i @ np.linalg.inv(pose_fn(t)) @ np.linalg.inv(sc_o) for t in ts]
    if weights is not None and n > 1:
        wt = [max(1e-4, float(weights(0.5 * (ts[i] + ts[i + 1])))) for i in range(n)]
    else:
        wt = [1.0] * max(n, 1)
    Hpad = His + [His[-1]] * (33 - len(His))
    wpad = wt + [0.0] * (32 - len(wt))
    out = run(E, WARP_FS, {'src': img}, {'n': n, 'ss': (float(img.w), float(img.h)), 'edge': 0 if edge == 'mirror' else 1,
                                         'seed': float((t0 * 0.6180339) % 1.0)},
              size=E.size, arrays={'Hi': mat3s(Hpad, 33), 'wt': np.array(wpad, np.float32)})
    if n > 1:
        seg = L / n                                              # output px per slice
        n2 = int(min(10, max(2, math.ceil(seg / 2.0))))
        if seg > 1.5:
            tm, dt = 0.5 * (t0 + t1), (t1 - t0) / n
            Sm = sc_o @ pose_fn(tm + 0.5 * dt) @ np.linalg.inv(pose_fn(tm - 0.5 * dt)) @ np.linalg.inv(sc_o)
            out = run(E, SMOOTH_FS, {'a': out}, {'S': tuple(np.asarray(Sm, np.float32).T.flatten()), 'n': n2})
    return out


MEAN_FS = '''uniform sampler2D a; uniform vec2 bs;
void main(){ vec2 c = (gl_FragCoord.xy - 0.5) * bs; vec2 ts = vec2(textureSize(a, 0)); float s = 0.0;
  for (int j = 0; j < 8; j++) for (int i = 0; i < 8; i++)
    s += luma(clamp(texture(a, (c + (vec2(float(i), float(j)) + 0.5) / 8.0 * bs) / ts).rgb, 0.0, 1.0));
  o = vec4(s / 64.0, 0.0, 0.0, 1.0); }'''
SCALE_FS = 'uniform sampler2D a; uniform float k; void main(){ vec4 c = texture(a, UV()); o = vec4(c.rgb * k, c.a); }'


def mean_luma(E, tex):
    """mean display luma of a texture (32 x 18 block means, 64 taps each; one tiny read-back)"""
    t = run(E, MEAN_FS, {'a': tex}, {'bs': (tex.w / 32.0, tex.h / 18.0)}, out=E.new((32, 18), dtype='f4'))
    return float(t.read()[..., 0].mean())


def _cap(E, sharp, out, cut, posed=None):
    """blurring a bright shot must not turn it into white fog -- the blurred frame's mean display luma
    stays <= cap x the sharp frame's (the larger of the untouched frame and the same camera pose without blur, so a push
    into a bright subject is not dimmed; default cap 1.05; Cut(..., cap=0) turns it off)"""
    cap = float(cut.kw.get('cap', 1.05))
    if cap <= 0:
        return out
    m1 = mean_luma(E, out)
    if m1 < 0.02:
        return out
    lim = cap * max(mean_luma(E, sharp), mean_luma(E, posed) if posed is not None else 0.0)
    if m1 <= lim:
        return out
    return run(E, SCALE_FS, {'a': out}, {'k': lim / m1})


def _tau(cut, side, u):
    no, ni = cut.eff
    return -no * (1.0 - u) if side == 'out' else u * ni


def transform(E, img, cut, side, u, shutter=None):
    """ONE side of a cut: the camera move + matching motion blur on the finished picture `img` (Engine texture in, out).
    Two-sided kinds (boltwipe / occlude / light) need TR.mix; here they only get their camera drift."""
    u = clamp01(u)
    tau = _tau(cut, side, u)
    if cut.kind in BOTH:
        return _drift(E, img, cut, side, tau)
    m = _motion(cut, side)
    sh = float(cut.kw.get('shutter', 1.0 if cut.kind == 'ramp' else SHUTTER) if shutter is None else shutter)
    edge = cut.kw.get('edge', 'mirror')
    if cut.base == 'smear' or cut.kind == 'smear':
        trail = float(cut.kw.get('trail', 2.8)) * cut.strength
        step = float(cut.kw.get('step', 0.0))
        tq = math.floor(tau / step) * step if step > 0 else tau
        lam = 0.42 * trail
        pf = (lambda t: m.pose(math.floor(t / step) * step)) if step > 0 else m.pose
        out = warp(E, img, pf, tq - trail, tq + 0.5 * sh, weights=lambda t: math.exp(-max(0.0, tq - t) / lam), edge=edge)
        tq0 = tq
    else:
        out = warp(E, img, m.pose, tau - 0.5 * sh, tau + 0.5 * sh, edge=edge)
        tq0 = tau
    posed = warp(E, img, m.pose, tq0, tq0, edge=edge) if float(cut.kw.get('cap', 1.05)) > 0 else None
    return _cap(E, img, out, cut, posed)


def velocity(cut, side, u, pt=(W / 2, H / 2)):
    """screen velocity (px / frame, 1080p) of the image point that sits on screen point pt -- e.g. to move type with the
    camera, or to check continuity"""
    m = _motion(cut, side)
    tau = _tau(cut, side, clamp01(u))
    a, b = m.pose(tau - 0.25), m.pose(tau + 0.25)
    p = _apply_pts(np.linalg.inv(m.pose(tau)), np.array([pt], np.float64))
    return (_apply_pts(b, p) - _apply_pts(a, p))[0] / 0.5


def timewarp(cuts, f):
    """content time for ramp cuts (pass it to your Takes: E.clip(take, TR.timewarp(CUTS, T.f))): the out shot runs up to
    `rate` x real time into the cut, the in shot starts that fast and slows back to 1x; identity elsewhere"""
    w = window([c for c in cuts if c.kind == 'ramp'], f)
    if w is None:
        return f
    c, side, u = w
    R = float(c.kw.get('rate', 3.0)) - 1.0
    p, q = PROFILE['ramp']
    no, ni = c.eff
    if side == 'out':
        return f + R * no * u ** (p + 1.0) / (p + 1.0)          # integral of (rate - 1)
    return f - R * ni * (1.0 - u) ** (q + 1.0) / (q + 1.0)


# ------------------------------------------------------------------ two-sided kinds
def mix(E, img_out, img_in, cut, F, f=None):
    """inside a cut's window with BOTH pictures rendered at this time (img_out = the shot before the cut, img_in = the shot
    after it). Two-sided kinds composite; single-side kinds just pick their side and call transform."""
    t = float(F if f is None else f)
    side, u = side_of(cut, F if f is None else f)
    if side is None:
        return img_out if t < cut.frame - 0.5 else img_in
    if cut.kind not in BOTH:
        return transform(E, img_out if side == 'out' else img_in, cut, side, u)
    tau = _tau(cut, side, u)
    if cut.kind == 'occlude':
        return _occlude(E, img_out, img_in, cut, tau)
    if cut.kind == 'boltwipe':
        return _boltwipe(E, img_out, img_in, cut, tau)
    return _light(E, img_out, img_in, cut, tau)


def _extent(d):
    """range of s = dot(x, d) over the screen corners"""
    s = _PROBE[:4] @ d
    return float(s.min()), float(s.max())


def _drift(E, img, cut, side, tau):
    """the camera follows the sweep a little (pan x the sweep speed): both pictures drift along the sweep direction"""
    d = _dvec(cut.dir, 'R')
    pan = float(cut.kw.get('pan', 0.06 if cut.kind != 'light' else 0.035)) * cut.strength
    s0, s1 = _extent(d)
    L = (s1 - s0) * pan

    def pose(t):
        off = _sweep(cut, t, 0.0, L) - (L if side == 'in' else 0.0)
        return _Hm((W / 2, H / 2), (W / 2 + d[0] * off, H / 2 + d[1] * off), 0.0, 0.0)
    return warp(E, img, pose, tau - 0.5 * SHUTTER, tau + 0.5 * SHUTTER, edge='mirror', nmax=12)


NOISE_GLSL = '''float h21(vec2 p){ p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
float vn(vec2 p){ vec2 i = floor(p); vec2 f = fract(p); vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(h21(i), h21(i + vec2(1.0, 0.0)), u.x), mix(h21(i + vec2(0.0, 1.0)), h21(i + vec2(1.0, 1.0)), u.x), u.y); }
float fbm(vec2 p){ float a = 0.5, s = 0.0; for (int i = 0; i < 5; i++){ s += a * vn(p); p = p * 2.03 + vec2(17.13, 9.21); a *= 0.5; }
  return s / 0.97; }
'''

OCC_FS = NOISE_GLSL + '''uniform sampler2D A; uniform sampler2D B; uniform sampler2D L; uniform vec2 d; uniform float c; uniform float bw;
uniform float vel; uniform float sm; uniform float seed; uniform float tt; uniform vec3 c0; uniform vec3 c1; uniform vec3 rimc;
uniform float rimk;
float dens(float z, float r, vec2 wq, float e1, float e2){
  float lead = 0.5 * bw + e1, trail = -0.5 * bw + e2;
  float n = fbm(vec2(z * 0.0021, r * 0.0021) + wq);              // body
  float m = fbm(vec2(z * 0.0062, r * 0.0062) + wq * 1.7 + 11.0);  // lumps on the edges
  float dl = (lead - z) / (0.16 * bw) + (m - 0.5) * 2.2;          // > 0 inside, lumpy
  float dt = (z - trail) / (0.16 * bw) + (m - 0.5) * 2.2;
  float prof = clamp(min(dl, dt) * 0.6 + 0.5, 0.0, 1.0);
  return prof * (0.70 + 0.75 * n);
}
void main(){
  vec2 x = gl_FragCoord.xy / u_s; vec2 dp = vec2(-d.y, d.x);
  float s = dot(x, d), r = dot(x, dp);
  float z0 = s - c;
  vec2 q = vec2(r * 0.0016, seed);
  float e1 = (fbm(q + vec2(3.1, 0.0)) - 0.5) * 0.55 * bw;          // billowing leading / trailing edges
  float e2 = (fbm(q + vec2(-7.7, 4.2)) - 0.5) * 0.55 * bw;
  vec2 wq = vec2(seed * 1.7, tt * 0.05) + 0.9 * vec2(fbm(vec2(z0, r) * 0.0011 + seed), fbm(vec2(z0, r) * 0.0011 - seed + 5.3));
  float a = 0.0, nn = 0.0;
  for (int i = 0; i < 6; i++){                                    // motion blur of the moving cloud (it is near the lens)
    float z = z0 - vel * sm * ((float(i) + 0.5) / 6.0 - 0.5);
    float D = dens(z, r, wq, e1, e2);
    a += smoothstep(0.30, 0.72, D); nn += D;
  }
  a /= 6.0; nn /= 6.0;
  float core = 1.0 - smoothstep(0.10 * bw, 0.26 * bw, abs(z0 - 0.04 * bw * (fbm(q * 3.0) - 0.5)));
  a = clamp(max(a, core) * 1.08, 0.0, 1.0);                       // the seam always sits under thick cloud
  float sw = smoothstep(-28.0, 28.0, z0 + 0.06 * bw * (fbm(q * 2.0 + 1.3) - 0.5));
  vec3 bg = mix(texture(B, UV()).rgb, texture(A, UV()).rgb, sw);  // swept side (z < 0) = the next shot
  vec3 lb = texture(L, UV()).rgb;                                  // the scene light behind the cloud (blurred)
  float thin = (1.0 - smoothstep(0.30, 0.62, nn)) * smoothstep(0.02, 0.30, a);   // only the thinnest rim lets light through
  vec3 cl = mix(c0, c1, smoothstep(0.55, 1.25, nn));
  cl = lin(cl) + lin(lb) * (0.025 + rimk * thin) + lin(rimc) * rimk * 0.3 * thin * luma(lb);
  o = vec4(mix(bg, enc(cl), a), 1.0);
}'''


def _occlude(E, A, B, cut, tau):
    d = _dvec(cut.dir, 'R')
    bw = float(cut.kw.get('width', 0.82)) * W * cut.strength ** 0.3
    s0, s1 = _extent(d)
    c0, c1 = s0 - 0.82 * bw, s1 + 0.82 * bw                         # the billowing edges stay off screen at both ends
    c = _sweep(cut, tau, c0, c1)
    v = _sweep_v(cut, tau, c0, c1)
    A2 = _drift(E, A, cut, 'out', tau)
    B2 = _drift(E, B, cut, 'in', tau)
    Lb = E.blur(E.mix(B2, A2, 0.5), 70.0)
    return run(E, OCC_FS, {'A': A2, 'B': B2, 'L': Lb},
               {'d': (float(d[0]), float(d[1])), 'c': float(c), 'bw': float(bw), 'vel': float(v), 'sm': SHUTTER,
                'seed': float(rnd(cut.seed, 'occ') * 50.0), 'tt': float(tau), 'c0': (0.012, 0.012, 0.015),
                'c1': (0.070, 0.067, 0.080), 'rimc': VT.ICE, 'rimk': 0.35})


LIGHT_FS = VT.SHOULDER_GLSL + '''uniform sampler2D A; uniform sampler2D B; uniform vec2 d; uniform float c; uniform float w; uniform float k;
uniform vec3 lc; uniform vec3 lc2; uniform float slant;
void main(){
  vec2 x = gl_FragCoord.xy / u_s; vec2 dp = vec2(-d.y, d.x);
  float s = dot(x, d) + slant * dot(x, dp);
  float z = (s - c) / w;
  float band = exp(-z * z);
  float wide = exp(-z * z * 0.12);
  vec3 a = lin(texture(A, UV()).rgb), b = lin(texture(B, UV()).rgb);
  vec3 bg = mix(b, a, smoothstep(-0.12, 0.12, z));                 // the swept side shows the next shot
  float yl = luma(enc(bg));
  bg *= 1.0 + 1.6 * wide * k * smoothstep(0.25, 0.9, yl);          // highlights catch the light first (not a white veil)
  vec3 col = bg + (lin(lc) * band + lin(lc2) * 0.35 * wide) * k * (0.35 + 0.65 * smoothstep(0.02, 0.5, yl));
  o = vec4(enc(shoulder(col, 0.82)), 1.0);
}'''


def _light(E, A, B, cut, tau):
    d = _dvec(cut.dir, 'R')
    slant = 0.28
    corners = _PROBE[:4]
    dp = np.array([-d[1], d[0]])
    s = corners @ d + slant * (corners @ dp)
    w = 0.06 * W
    c0, c1 = float(s.min()) - 3.0 * w, float(s.max()) + 3.0 * w
    c = _sweep(cut, tau, c0, c1)
    A2 = _drift(E, A, cut, 'out', tau)
    B2 = _drift(E, B, cut, 'in', tau)
    return run(E, LIGHT_FS, {'A': A2, 'B': B2}, {'d': (float(d[0]), float(d[1])), 'c': float(c), 'w': w,
                                                 'k': 0.9 * cut.strength, 'lc': VT.ICE, 'lc2': VT.ELECTRIC, 'slant': slant})


# ------------------------------------------------------------------ boltwipe: one bolt crosses the frame
EDGE_FS = '''uniform sampler2D A; uniform sampler2D B; uniform sampler2D eg; uniform vec2 p0; uniform vec2 off; uniform vec2 rc;
uniform vec2 yr; uniform float sgn; uniform float soft;
void main(){
  vec2 x = gl_FragCoord.xy / u_s;
  vec2 q = x - p0 - off; vec2 l = vec2(rc.x * q.x + rc.y * q.y, -rc.y * q.x + rc.x * q.y) + p0;   // screen -> bolt frame
  float xe = texture(eg, vec2(clamp((l.y - yr.x) / (yr.y - yr.x), 0.0, 1.0), 0.5)).r;
  float m = smoothstep(-soft, soft, (l.x - xe) * sgn);              // 1 = ahead of the bolt (still the old shot)
  o = vec4(mix(texture(B, UV()).rgb, texture(A, UV()).rgb, m), 1.0);
}'''

_BOLTS = {}


def _bolt(seed, slant, illum):
    from src.fx import discharge as DC
    key = (int(seed), round(float(slant), 1), round(float(illum), 3))
    if key not in _BOLTS:
        D = DC.Discharge('bolt', seed=int(seed), p0=(960.0, -70.0), p1=(960.0 + slant, 1150.0), cell=7, eta=1.8,
                         width=3.6, wmin=0.8, inten=6.4, main_k=1.8, tau_b=3.2, tau_m=6.0, restrike=(), illum=illum,
                         fcap=2.0, fadd=0.06, grow=6.0)
        g = D.g
        P = g['pos'][g['main']]
        P = P[np.argsort(P[:, 1], kind='stable')]
        ys = np.linspace(-260.0, 1340.0, 801)
        xs = np.interp(ys, P[:, 1], P[:, 0]).astype(np.float32)
        mx = float(P[:, 0].mean())
        dev = float(np.abs(P[:, 0] - mx).max())
        _BOLTS[key] = (D, xs, (ys[0], ys[-1]), mx, dev)
    return _BOLTS[key]


def _bolt_plan(cut):
    """the strokes of a boltwipe: (tau of the stroke, geometry seed, slant); geometry changes at every re-stroke"""
    no, ni = cut.eff
    sd = cut.seed
    st = [(-no + 0.6, sd * 3 + 1), (-0.9, sd * 3 + 2), (0.45 * ni, sd * 3 + 3)]
    return [(t, s, rr_(s)) for t, s in st]


def rr_(s):
    return (rnd(s, 'slant') - 0.5) * 360.0


def _boltwipe(E, A, B, cut, tau):
    d = _dvec(cut.dir, 'R')
    illum = float(cut.kw.get('illum', 0.9))
    plan = _bolt_plan(cut)
    j = max(i for i, (t, _, _) in enumerate(plan) if tau >= t or i == 0)
    ts, seed, slant = plan[j]
    D, xs, yr, mx, dev = _bolt(seed, slant, illum)
    phi = math.atan2(d[1], d[0])
    th = (phi + math.pi / 2) % math.pi - math.pi / 2                  # the bolt lies across the sweep, sky side up
    if abs(abs(th) - math.pi / 2) < 1e-6:
        th = -math.pi / 2
    R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    p0 = D.g['pos'][0]
    s0, s1 = _extent(d)
    span = dev + 40.0
    c = _sweep(cut, tau, s0 - span, s1 + span)
    ctr = np.array([W / 2, H / 2])
    target = ctr + d * (c - float(ctr @ d))                        # where the bolt's mean channel point goes
    mref = np.array([mx, 540.0])
    off = target - (R @ (mref - p0) + p0)
    dl = R.T @ d                                                   # sweep direction in the bolt frame
    sgn = 1.0 if dl[0] >= 0 else -1.0
    A2 = _drift(E, A, cut, 'out', tau)
    B2 = _drift(E, B, cut, 'in', tau)
    eg = E.persistent(('tx_edge', seed, round(slant, 1)), lambda: E.texture_from(xs.reshape(1, -1, 1)))
    img = run(E, EDGE_FS, {'A': A2, 'B': B2, 'eg': eg},
              {'p0': (float(p0[0]), float(p0[1])), 'off': (float(off[0]), float(off[1])),
               'rc': (math.cos(th), math.sin(th)), 'yr': (float(yr[0]), float(yr[1])), 'sgn': sgn, 'soft': 2.5})
    tb = tau - ts
    k = 1.0 if tb >= 0 else 0.0
    return D.draw(E, img, max(tb, 0.0), k=k * cut.strength ** 0.5, offset=(float(off[0]), float(off[1])), rot=th)


# ------------------------------------------------------------------ impact (DROP 329, the last hit 1645 only)
# v2 (the v1 ink negative was ~90 % paper = a white flash). Nothing fills the screen:
# the next shot is visible from the hit frame, lit only by its own bolt; the previous shot breaks into Voronoi shards that
# fly out of `at` (motion-blurred, thin ink edges) and uncover it; a refractive shock ring runs out.
FLY_FS = '''uniform sampler2D bg; uniform sampler2D sh; uniform vec4 A[64]; uniform vec4 Bv[64]; uniform int n; uniform int nt;
uniform float tc; uniform float sht; uniform float acc; uniform float gr; uniform float fa; uniform float fb; uniform vec3 ink;
uniform float ek; uniform float ew; uniform float facet; uniform float jit;
float ign(vec2 p){ return fract(52.9829189 * fract(dot(p, vec2(0.06711056, 0.00583715)))); }
vec2 Qof(int i, float t){ float x = max(t - A[i].z, 0.0); return A[i].xy + Bv[i].xy * (A[i].w * x + 0.5 * acc * x * x); }
void main(){
  vec2 p = gl_FragCoord.xy;
  vec3 b = lin(texture(bg, UV()).rgb);
  vec3 acc3 = vec3(0.0);
  float jj = ign(p) * jit;
  for (int k = 0; k < 8; k++){
    if (k >= nt) break;
    float t = tc + sht * ((float(k) + jj + 0.5 * (1.0 - jit)) / float(nt) - 0.5);
    float d1 = 1e9, d2 = 1e9; int i1 = 0, i2 = 0;
    for (int i = 0; i < 64; i++){ if (i >= n) break; float d = distance(p, Qof(i, t));
      if (d < d1){ d2 = d1; i2 = i1; d1 = d; i1 = i; } else if (d < d2){ d2 = d; i2 = i; } }
    vec3 col = b;
    for (int m = 0; m < 2; m++){
      int c = m == 0 ? i1 : i2;
      float x = max(t - A[c].z, 0.0);
      float sc = 1.0 + gr * x; float ang = Bv[c].z * x; float cs = cos(ang), sn = sin(ang);
      vec2 v = (p - Qof(c, t)) / sc; vec2 q = vec2(cs * v.x + sn * v.y, -sn * v.x + cs * v.y) + A[c].xy;   // rest position
      float dc = distance(q, A[c].xy); bool own = true; float e = 1e9;
      for (int j = 0; j < 64; j++){ if (j >= n) break; if (j == c) continue;
        float dj = distance(q, A[j].xy); if (dj < dc){ own = false; break; }
        e = min(e, (dj * dj - dc * dc) / (2.0 * max(distance(A[j].xy, A[c].xy), 1e-3))); }
      if (!own) continue;
      float al = (1.0 - smoothstep(fa, fb, t)) * step(0.0, Bv[c].w);          // Bv.w < 0: the shard the bolt destroyed
      vec3 s3 = lin(texture(sh, clamp(q, vec2(0.5), u_size - 0.5) / u_size).rgb) * (1.0 + facet * (abs(Bv[c].w) - 0.5));
      s3 = mix(s3, lin(ink), ek * (1.0 - smoothstep(0.5 * ew, ew, e * sc)));    // thin ink edge
      col = mix(b, s3, al);
      break;
    }
    acc3 += col;
  }
  o = vec4(enc(acc3 / float(nt)), 1.0);
}'''

_IMPACT_BOLTS = {}


def _impact_bolt(at, seed, illum):
    from src.fx import discharge as DC
    key = (round(at[0]), round(at[1]), int(seed), round(float(illum), 3))
    if key not in _IMPACT_BOLTS:
        sl = (rnd(seed, 'isl') - 0.5) * 260.0
        _IMPACT_BOLTS[key] = DC.Discharge('bolt', seed=int(seed), p0=(at[0] + sl, -70.0), p1=(float(at[0]), float(at[1])),
                                          cell=7, eta=1.8, width=3.8, wmin=0.9, inten=6.6, main_k=1.9, tau_b=4.0, tau_m=7.0,
                                          restrike=(4.0,), illum=illum, fcap=2.0, fadd=0.06, grow=8.0)
    return _IMPACT_BOLTS[key]


def impact_span(F, ch=3, n=6):
    """frames that need TR.impact around a hit on F: [F - ch, F + n)"""
    return int(F) - ch, int(F) + n


def impact(E, img_out, img_in, F, f, at=(960.0, 540.0), style='neg', k=1.0, ch=3, n=6, seed=None, arcs=2, neg_src=None,
           bolt=True, illum=0.8):
    """multi-frame impact on the hit frame F (int; f = the current frame, float ok). Render BOTH shots on [F - ch, F + n):
    img_out = the shot before (it charges, then shatters), img_in = the strike (visible from F). No frame is filled with
    a colour (mean luma <= 0.55, near-white <= 20 %, near-black <= 60 % on every frame):
      F-ch .. F-1  charge: the picture is sucked toward `at` (push 1 -> 1.07, radial blur, a little desaturated)
      F .. F+n-1   the strike: the next shot, lit by its own bolt (DBM channel down to `at` + bloom + flash fill; bolt=False if
                   the shot draws its own, or pass a Discharge = drawn at t = f - F); the previous shot breaks into ~60
                   Voronoi shards (thin ink edges; style 'plain' = none, 'plasma' = false colour) that fly out of `at` with
                   motion blur, turn, grow toward the lens and are gone by F+5 (the shard under `at` is destroyed on F);
                   a refractive shock ring runs out (2-5 frames); a decaying shake; a faint cool aftershock; `arcs` arcs.
    neg_src = another picture for the shards (e.g. a design frame) instead of img_out."""
    from src.fx import impact as IM
    from src.kit import fx
    from src.kit.anim import ei3
    if isinstance(F, Cut):
        F = F.frame
    lf = float(f) - int(F)
    cx, cy = float(at[0]), float(at[1])
    sd = int(F) * 7 + 3 if seed is None else int(seed)
    if lf < -ch - 0.5:
        return img_out
    if lf < -0.5:
        u = clamp01((lf + ch + 0.5) / ch)
        return run(E, IM.CHARGE_FS, {'a': img_out}, {'c': (cx * E.s, cy * E.s), 'z': 1.0 + 0.07 * ei3(u) * k,
                                                     'blur': 0.07 * u * k, 'dark': 0.12 * u * k, 'desat': 0.3 * u * k, 'n': 12})
    if lf >= n - 0.5:
        return img_in
    t = max(0.0, lf)
    img = img_in
    if bolt is True:
        img = _impact_bolt((cx, cy), sd, illum).draw(E, img, t)
    elif bolt:
        img = bolt.draw(E, img, t)
    src = neg_src if neg_src is not None else img_out
    if src is not None and t < n - 0.6:
        P, mul, rot = IM._seeds(round(cx), round(cy), sd)
        m = len(P)
        v = P - np.array([cx, cy])
        dist = np.hypot(v[:, 0], v[:, 1])
        dirs = np.where(dist[:, None] > 1e-3, v / np.maximum(dist, 1e-3)[:, None], np.array([[1.0, 0.0]]))
        Av = np.zeros((64, 4), np.float32)
        Bw = np.zeros((64, 4), np.float32)
        Av[:m, 0:2] = P * E.s
        Av[:m, 2] = -1.2 + 1.4 * np.clip(dist / 1100.0, 0, 1)                          # inner shards leave first
        Av[:m, 3] = (34.0 + 46.0 * (1.0 - np.clip(dist / 1200.0, 0, 1))) * mul * k * E.s   # px / frame
        Bw[:m, 0:2] = dirs
        Bw[:m, 2] = np.array([(rnd(sd, i, 'om') - 0.5) * 0.12 for i in range(m)]) * k
        Bw[:m, 3] = np.array([0.05 + 0.9 * rnd(sd, i, 'fc') for i in range(m)])
        Bw[0, 3] = -0.5                                                                  # under the strike: gone on F
        Av[m:, 0:2] = 1e6
        if style == 'plasma':
            src = VT.plasma(E, src)
        img = run(E, FLY_FS, {'bg': img, 'sh': src},
                  {'n': m, 'nt': 6, 'tc': float(t), 'sht': SHUTTER, 'acc': 30.0 * k * E.s, 'gr': 0.05, 'fa': 2.6,
                   'fb': 4.9, 'ink': INK, 'ek': 0.0 if style == 'plain' else 0.75, 'ew': 1.6 * E.s, 'facet': 0.10, 'jit': 1.0},
                  arrays={'A': Av.flatten(), 'Bv': Bw.flatten()})
    u = clamp01((t + 1.0) / (n + 1.0))
    img = run(E, IM.SHOCK_FS, {'a': img}, {'c': (cx * E.s, cy * E.s), 'r': (60.0 + 1400.0 * k) * (1.0 - (1.0 - u) ** 2.2) * E.s,
                                          'w': (110.0 + 170.0 * u) * E.s, 'amp': 26.0 * k * (1.0 - u) ** 1.3 * E.s,
                                          'rim': 0.04 * k * (1.0 - u) ** 1.5, 'ca': 7.0 * k * (1.0 - u) * E.s,
                                          'rc': VT.ICE, 't1': VT.VIOLET, 't2': VT.ELECTRIC})
    Hs = []
    for tt in (t - 0.2, t, t + 0.2):
        sx, sy, sr = fx.shake(tt, -0.5, 14.0 * k, decay=2.6, seed=sd + 5)
        zz = 1.0 + 0.03 * k * math.exp(-max(0.0, tt) / 2.5)
        Hs.append(fx.pose_H(zoom=zz, roll=math.degrees(sr) * 0.8, piv=(cx, cy), at=(cx + sx, cy + sy)))
    img = fx.camera(E, img, Hs, edge='clamp')
    img = IM.cool_split(E, img, 0.006 * k * math.exp(-t / 2.2), cx * E.s, cy * E.s, dx=4.0 * k * math.exp(-t / 1.8) * E.s)
    if arcs:
        h = IM.Hit(int(F), 'neg', c=(cx, cy), k=k, n=1, af=n, arcs=arcs, seed=sd)
        img = IM.arcs(E, img, h, t, cx, cy)
    return img


# ------------------------------------------------------------------ table checks (for shot authors)
def check(cuts, quiet=False):
    """lint a CUTS table: overlaps (trimmed), the same kind 3+ times in a row, > 2 spins, two-sided kinds on very short
    shots, missing at / dir. Returns the list of warnings."""
    cs = _trim(cuts)
    msg = []
    run_k, n_run = None, 0
    for c in cs:
        n_run = n_run + 1 if c.kind == run_k else 1
        run_k = c.kind
        if n_run >= 3:
            msg.append(f'{c.frame}: {c.kind} {n_run}x in a row')
        if c.eff != (c.n_out, c.n_in):
            msg.append(f'{c.frame}: window trimmed to {c.eff} (asked {c.n_out}/{c.n_in})')
        if c.kind in ('push', 'pull', 'shape') and c.at is None:
            msg.append(f'{c.frame}: {c.kind} without at (uses the frame centre)')
    if sum(c.kind == 'spin' for c in cs) > 2:
        msg.append('more than 2 spins (brief: at most 2 in the film)')
    if not quiet:
        for m in msg:
            print('TR.check:', m)
    return msg


# ------------------------------------------------------------------ self test
def _selftest():
    """velocity continuity of every single-side kind (no GPU) + a whip frame by frame"""
    pt = (1500.0, 300.0)
    print('kind      speed into the cut  out of the cut   last-out frame  first-in frame   (px/frame of a point at', pt, ')')
    for kind, kw in (('whip', dict(dir='R')), ('smear', dict(dir='L')), ('push', dict(at=(1200, 400))),
                     ('pull', dict(at=(900, 500))), ('spin', dict(dir='cw')), ('shape', dict(at=(700, 300), b=(1300, 700))),
                     ('ramp', dict(base='whip', dir='U')), ('ramp', dict(base='push', at=(960, 540)))):
        c = Cut(100, kind, **kw)
        no, ni = c.eff
        v1, v2 = np.hypot(*velocity(c, 'out', 1.0, pt)), np.hypot(*velocity(c, 'in', 0.0, pt))
        f1, f2 = np.hypot(*velocity(c, 'out', (no - 0.5) / no, pt)), np.hypot(*velocity(c, 'in', 0.5 / ni, pt))
        print(f'{kind:8s} {v1:12.1f} {v2:16.1f} {f1:16.1f} {f2:15.1f}   {kw}')
    c = Cut(100, 'whip', dir='R')
    print('whip R frame by frame: centre x offset / blur length (180 deg)')
    for F in range(94, 107):
        s, u = side_of(c, F)
        if s is None:
            print(f'  {F} --'); continue
        m = _motion(c, s)
        tau = _tau(c, s, u)
        x0 = _apply_pts(m.pose(tau), np.array([[960.0, 540.0]]))[0]
        bl = np.hypot(*(_apply_pts(m.pose(tau + 0.25), np.array([[960.0, 540.0]]))[0]
                        - _apply_pts(m.pose(tau - 0.25), np.array([[960.0, 540.0]]))[0]))
        print(f'  {F} {s:3s} u={u:.2f}  offset {x0[0] - 960:8.1f},{x0[1] - 540:6.1f}  blur {bl:6.1f} px ({bl / W:.2f} W)')


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'check':
        _selftest()
