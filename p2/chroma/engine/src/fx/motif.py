# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""一集一个签名色 (p2, 2026-11-02): ONE motif colour -> every colour the parts need + the one-call mono world.
CHROMA rule: the world is B/W, only the motif keeps colour; impact frames = ink + motif colour + paper.

    P = MO.palette('#3FB8FF')                     # the episode's signature colour (or MO.palette(hue=200), HSV degrees)
    img = MO.world(E, plate, P)                   # B/W world, only light in the motif hue window keeps its own colour
    img = MO.world(E, plate, P, keep='bright')    # any bright light keeps colour (fire, lamps) -- for warm / mixed plates
    HITS = [SR.hit(F, 'post3_blue', c=(960, 540), **MO.hit_cols(P))]       # impact colours = the motif
    V = KV.Vortex(f0, f1, end='portal', **MO.vortex_cols(P))                 # vortex rims / halo / split = the motif
    P['acc'] P['deep'] P['pale'] P['glow'] (sRGB tuples)  P['hex']  P['hue'] (HSV deg)  P['ramp'] (5 hex stops, dark -> white)
Built on fx.heat.heat (bbk=0: no blackbody re-colouring) -- its knobs pass through: lo / hi (brightness knee), sat, glow,
con / blk / wht (B/W contrast), soft. Never type other colours in a shot: use P[...] or kit.core INK / PAPER / WHITE.
"""
import colorsys
import numpy as np
from src import timeline as TL
from src.kit.core import INK, PAPER, hx, _to_oklab
from src.fx import heat as HT
from src.fx import volt as VT

_CUR = {'P': None}          # the episode's signature palette (the last MO.palette / set_current): engine defaults read it


def series_fx():
    """which spark / arc line devices are on: 'volt' (electric-arc afterglow, slab arc outline, focus
    lines on every slam) | 'ignition' (spark style) | None (every other motif, the default: none of them). Director-owned:
    timeline.SERIES_FX (a shot never sets it)."""
    v = getattr(TL, 'SERIES_FX', None)
    return str(v).lower() if v else None


def set_current(P):
    """make P the signature palette the engine defaults use (MO.palette does this itself; HE.set_motif too)"""
    _CUR['P'] = P
    return P


def current():
    """the signature palette of this episode (the last MO.palette(...) made), or None (no motif colour yet = old defaults)"""
    return _CUR['P']


def _okh(rgb):
    lab = _to_oklab(np.array([rgb], np.float64))[0]
    return float(np.degrees(np.arctan2(lab[2], lab[1])) % 360.0), float(np.hypot(lab[1], lab[2])), float(lab[0])


def palette(color=None, hue=None, width=28.0, chroma=0.16):
    """color = '#RRGGBB' or rgb 0..1 (the motif colour as seen in the footage); or hue = HSV degrees.
    width = half-width (HSV deg) of the hue window that keeps colour in MO.world."""
    if color is None:
        color = colorsys.hsv_to_rgb((hue % 360.0) / 360.0, 0.85, 1.0)
    rgb = hx(color) if isinstance(color, str) else tuple(map(float, color))
    h, c, _ = _okh(rgb)
    c = max(0.06, min(chroma, c if c > 0.02 else chroma))
    P = dict(
        base=rgb,
        acc=VT.oklch(0.62, c, h),                 # duo / post3 accent (mid-bright, reads on ink and on paper)
        deep=VT.oklch(0.48, min(0.19, c * 1.1), h),   # rich duo / post3 accent (more ink)
        glow=VT.oklch(0.72, c * 0.8, h),          # glows, aftershock
        pale=VT.oklch(0.88, min(0.07, c * 0.45), h),  # rims, rings, the core next to white
        core=VT.oklch(0.97, 0.015, h),            # over-exposed core
    )
    P['ramp'] = [VT.hexof(INK), VT.hexof(VT.oklch(0.30, c * 0.5, h)), VT.hexof(P['acc']), VT.hexof(P['pale']), VT.hexof(P['core'])]
    P['hue'] = 360.0 * colorsys.rgb_to_hsv(*P['acc'])[0]
    P['win'] = (P['hue'] - width, P['hue'] + width)
    P['hex'] = VT.hexof(P['acc'])
    return set_current(P)


def world(E, plate, P, keep='hue', **kw):
    """B/W world, only the motif keeps colour. keep='hue': light inside P['win'] keeps its own colour; keep='bright': any bright
    saturated light keeps colour (warm footage: fire / lamps). kw -> fx.heat knobs (lo hi sat glow con blk wht soft ...)."""
    p = dict(bbk=0.0, sat=0.95, glow=0.18, hue=P['win'])
    if keep == 'bright':
        p.update(hue=(-180.0, 180.0))
    p.update(kw)
    return HT.heat(E, plate, None, **p)


def hit_cols(P):
    """kwargs for SR.hit(...): impact frames in ink + motif + paper; arcs=0 (the electric-arc afterglow belongs to 'volt').
    11-05: glints are NOT in this dict any more (shots that already pass glints=0 next to **MO.hit_cols(P) would raise
    'got multiple values'); SR.hit itself turns the 'blue' palettes' cold micro-glints and the preset arcs off unless the
    series asks for them (timeline.SERIES_FX == 'volt') -- they read as thin streaks after every hit."""
    return dict(col=P['deep'], col2=P['acc'], ring_col=P['pale'], split_cols=(P['acc'], P['pale']), line_col=P['pale'], arcs=0)


def vortex_cols(P):
    """kwargs for KV.Vortex(...): rims, portal halo and the chromatic split in the motif colour"""
    return dict(rim=P['pale'], rim2=P['acc'], halo_ramp=P['ramp'], pal_hue=P['hue'])


if __name__ == '__main__':
    for c in ('#3FB8FF', '#FF5A1F', '#9BFF3A', '#FF3FA8'):
        P = palette(c)
        print(c, '->', {k: VT.hexof(P[k]) for k in ('acc', 'deep', 'glow', 'pale', 'core')}, 'hue %.0f' % P['hue'], P['ramp'])
