# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""Engine demo (3.0 s on its own 128 BPM grid; beat = 28.125 f). Proves the pipeline end to end:
  footage Takes (2160 push-in with trilinear minification, 'blend' interpolation, speed ramps incl. the pre-impact slow-down),
  the heat look (spark -> forge -> drop presets), impact clusters on beats (post3 on beat 2, thermal on beat 6),
  the kaleidoscope vortex with a portal into the next plate (letters slam during the spin), type system (hero / accent / wide /
  mono), detail layer (crop marks, crosshair + tick ring, temperature scale), shake.
NOTE p2: colours of impact / kaleido are the 'volt' preset defaults here; the ex_*.py shots show the motif palette (fx.motif)."""
import math
from src.engine import shot
from src.footage import Take
from src.fx import heat as HT, impact as IM, kaleido as KV
from src.kit import fx, type as TY, design as D
from src.kit.anim import keys, eo3, eo5, eoback, clamp01, lerp, eio3, prog
from src.kit.core import INK, PAPER, WHITE

BPM = 128.0
BEAT = 3600.0 / BPM


def bf(n):
    """beat n (0-based, may be fractional) -> float frame"""
    return n * BEAT


def BF(n):
    return int(math.floor(n * BEAT + 0.5))


LENGTH = 180
B2, B4, B5, B6 = BF(2), BF(4), BF(5), BF(6)                    # 56, 113, 141, 169

TA = Take('K1', film=(0, B2), src=0.5, speed=[(0, 1.0), (bf(1.5), 0.9), (B2 - 4, 0.22)], tier=2160, interp='blend')
TB = Take('K2', film=(B2, 153), src=0.12, speed=[(B2, 1.7), (B2 + 8, 0.55), (bf(3.4), 0.6), (bf(4.4), 2.6)], interp='blend')
TC = Take('K4', film=(B5 - 1, LENGTH), src=0.25, speed=0.75, interp='blend')

HITS = [IM.Hit(B2, 'post3', c=(960, 600), k=1.0), IM.Hit(B6, 'thermal', c=(960, 560), k=0.9)]
V = KV.Vortex(bf(3.5), 152, end='portal', nseg=6, nseg_end=12, src_c=(960, 430), wedge=-90.0, eye=(960, 540))

FLAME = (1110.0, 300.0)                                          # the match flame in T1 (plate px, 1080p units)


def pose_a(f):
    z = keys(f, [(0, 1.0, 'eio3'), (B2 - 3, 1.26)])
    return dict(zoom=z, roll=keys(f, [(0, -1.2, 'eio3'), (B2, 0.6)]), piv=(1100.0, 420.0),
                at=(1100.0 + 14 * math.sin(f / 23.0), 450.0 + 9 * math.cos(f / 31.0)))


def plate_a(E, T):
    img = fx.cam(E, T, E.clip(TA, T.F), pose_a, n=3)
    return HT.heat(E, img, 'spark')


def pose_b(f):
    z = keys(f, [(B2, 1.16, 'eo3'), (B2 + 14, 1.03), (bf(3.5), 1.0, 'eio3'), (153, 1.12)])
    return dict(zoom=z, roll=keys(f, [(B2, -3.0, 'eo3'), (B2 + 14, 0.0)]), piv=(960.0, 560.0), at=(960.0, 560.0))


def plate_b(E, T):
    return HT.heat(E, fx.cam(E, T, E.clip(TB, T.F), pose_b, n=3), 'forge')


def plate_c(E, T):
    pose = lambda f: dict(zoom=keys(f, [(B5, 1.0, 'eo3'), (LENGTH, 1.09)]), piv=(980.0, 520.0), at=(960.0, 540.0))
    return HT.heat(E, fx.cam(E, T, E.clip(TC, T.F), pose, n=2), 'drop', lo=0.30, hi=0.62)   # darker clip: lower knee


def slam(t, n=5, s0=1.35):
    """scale of a word slammed in at t=0 (frames): big -> 1 with a small overshoot"""
    return 1.0 + (s0 - 1.0) * (1.0 - eoback(t / n, 1.4)) if t >= 0 else 0.0


# ------------------------------------------------------------------ type / detail per section
def type_a(E, T):
    cv = E.canvas(); f = T.f
    D.crop_marks(cv, WHITE, 0.55)
    D.tag_block(cv, 96, 126, ['CANDLE FLAME', 'APPROX. 1850 K'], t=f - 3, cps=1.6, color=WHITE, alpha=0.92)
    Hm = fx.pose_H(**pose_a(f))
    x, y = fx.apply_H(Hm, *FLAME)
    p = eo3((f - 8) / 14.0)
    if p > 0:
        D.crosshair(cv, x, y, 30, WHITE, 0.9 * p)
        D.tick_ring(cv, x, y, 74, p=p, ticks=72, major=6, color=WHITE, alpha=0.8, tick=7, a0=-90 + 0.8 * f)
        TY.word(cv, f'{int(lerp(1500, 1850, eo3((f - 8) / 30.0)))} K', x + 92, y + 9, 24, 'mono', HT.ORANGE, p)
    cap = 214.0
    sz = TY.size_for_cap('hero', cap)
    if f >= 5:
        TY.word(cv, 'SPARK', 96, 960, sz, 'hero', PAPER, clamp01((f - 5) / 1.5), scale=slam(f - 5) * (1.0 + 0.0009 * (f - 5)))
    if f >= bf(1):
        TY.word(cv, 'STRIKE · LIGHT · BURN', 100, 960 - cap - 44, 34, 'accent', PAPER, clamp01((f - bf(1)) / 3.0))
    return cv.texture()


def type_b(E, T):
    cv = E.canvas(); f = T.f
    D.crop_marks(cv, WHITE, 0.55)
    u = eo3(prog(f, B2 + 2, bf(3) + 2))
    c = lerp(525.0, 1538.0, u)
    col = HT.blackbody(c + 273.15)
    sz = TY.size_for_cap('wide', 150.0)
    TY.word(cv, f'{int(round(c))}°C', 960, 600, sz, 'wide', PAPER, 1.0, 'center', scale=slam(f - B2, 6, 1.5))
    lbl = 'DRAPER POINT · SOLIDS GLOW RED' if u < 0.08 else ('IRON · MELTING POINT' if u > 0.995 else 'HEATING')
    TY.word(cv, lbl, 960, 668, 24, 'mono', WHITE, 0.9, 'center')
    x0, x1, yb = 560.0, 1360.0, 770.0                            # temperature scale 0..1600 C
    D.ruler(cv, x0, yb, x1 - x0, n=32, major=8, color=WHITE, alpha=0.75, p=eo3(prog(f, B2, B2 + 16)))
    for v in (0, 400, 800, 1200, 1600):
        TY.word(cv, str(v), x0 + (x1 - x0) * v / 1600.0, yb + 34, 22, 'mono', WHITE, 0.7 * eo3(prog(f, B2 + 4, B2 + 16)), 'center')
    mx = x0 + (x1 - x0) * c / 1600.0
    cv.c.drawPath(_tri(mx, yb - 22, 11), _paint(col))
    cv.line(mx, yb - 22, mx, yb - 4, col, 1.0, 2.0)
    TY.word(cv, 'FE', 1824, 126, 24, 'mono', WHITE, 0.9, 'right')
    return cv.texture()


def _tri(x, y, s):
    import skia
    p = skia.Path(); p.moveTo(x - s, y - s * 1.5); p.lineTo(x + s, y - s * 1.5); p.lineTo(x, y); p.close(); return p


def _paint(col):
    from src.engine.canvas import paint
    return paint(col)


LET = [113.0 + 3.5 * i for i in range(8)]                         # title letters slam in on 32nds during the spin


def type_v(E, T):
    cv = E.canvas(); f = T.f
    D.crop_marks(cv, WHITE, 0.55)
    sz = TY.size_for_cap('wide', 128.0)
    out = clamp01((f - V.f1 + 11) / 9.0)                          # portal: the word flies past the camera

    def each(i, n):
        t = f - LET[i]
        if t < 0:
            return None
        s = lerp(2.6, 1.0, eo5(t / 4.0)) * (1.0 + 2.2 * out ** 2)
        return (s, 0.0, 0.0, clamp01(t / 1.2) * (1.0 - out))
    TY.letters(cv, 'CHROMA', 960, 540 + 64, sz, 'wide', PAPER, 'center', each=each)
    TY.word(cv, 'T−0', 960, 760, 26, 'mono', WHITE, 0.85 * clamp01((f - LET[-1]) / 2.0) * (1 - out), 'center')
    return cv.texture()


def type_c(E, T):
    cv = E.canvas(); f = T.f
    D.crop_marks(cv, WHITE, 0.55)
    a = clamp01((f - 150) / 4.0)
    TY.word(cv, 'FROM A SPARK TO A STAR', 960, 950, 36, 'accent', PAPER, a, 'center')
    D.tag_block(cv, 1824, 126, ['SUN · PHOTOSPHERE', '5772 K'], t=f - 152, cps=1.8, color=WHITE, alpha=0.92, align='right', bar=False)
    return cv.texture()


@shot(subframes=1)
def demo(E, T):
    F = T.F
    if F < B2:
        img = E.over(plate_a(E, T), type_a(E, T))
    elif F < V.f0:
        img = E.over(plate_b(E, T), type_b(E, T))
    elif T.f < V.f1:
        nxt = plate_c(E, T) if V.phase(T.f) == 'portal' else None
        img = E.over(V.frame(E, T, plate_b(E, T), nxt), type_v(E, T))
    else:
        img = E.over(plate_c(E, T), type_c(E, T))
    return IM.post(E, T, img, HITS)


demo.length = LENGTH
