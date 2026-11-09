# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""Series kit: the CHROMA effects in the film's
colours, one call each. Colours come from fx.volt only (COL below = its named light colours); nothing here changes the look.

IMPACT CLUSTERS (charge 2-4 f -> impact 1-3 f -> aftermath 4-10 f), one line per hit, `post` last in the shot:
    HITS = [SR.hit(TL.F(9), 'post3_blue', c=(960, 520)), SR.hit(TL.F(9, 3), 'lines', c=lambda F: SR.at(pose, F, 812, 400))]
    return SR.post(E, T, img, HITS)                      # plain IM.Hit objects may sit in the same list
  presets: duo_blue duo_amber post3_blue post3_orange lines lines_focus half (= half_blue) half_amber plasma voronoi kick
  - impact frames are LUMA-MATCHED: duo / post3 thresholds are this frame's own percentiles, chosen so the impact frame's
    mean brightness stays within ~0.1 of the frame it replaces (paper-white only as fragments <= 16 % of the area, ink
    >= 45 %); no full-screen white. 'voronoi' (shatter) only for 936 and 1756.
  - aftermath (all presets): refracting shock ring, decaying (v13 default ring='soft' = no thin white edge lines; 
    too many circles. ring='full' restores the thin edge + second thin ring)
    shake with motion blur + zoom rebound, colour aftershock in the preset's two colours, debris = IGNITION's cooling
    sparks (warm palettes) or the electric-arc afterglow (blue palettes).
  - SR.card(E, img, F, preset, c) = the impact frame look alone (trailer cells, design cards).
HUD (skia canvas `cv`, 1080p units; line width 1.4, DM Mono 22 px; follows the subject: x, y = SR.at(pose, f, px, py)):
  ring_hud (crosshair + tick ring + two big arcs, draws on / spins / retracts) · callout (label hanging off a ring) ·
  readout (number stepping on the 16ths) · tag (typed mono block) · ring_text (text around a circle) · corners ·
  frame_hud (IGNITION hud: corners + section tag + frame counter + subject label) · volt_scale (log voltage gauge) ·
  hud_over(E, img, cv, split) (composite with a little chromatic split).
CAMERA (pure functions): punch step slam whip whip_in drift rumble moves kick push_thru
area_r at.
DESIGNED TRANSITIONS: SR.tx(F, kind, n_out, n_in, **kw) -> a cut; kinds circle iris arcburn stripes blocks kaleido
  hit (both shots rendered in the window) + every TR kind (whip push pull smear spin ramp occlude boltwipe shape light,
  cap=0 = full strength). In a shot:  img = SR.tx_frame(E, T, TXS, lambda X: render_shot_owning_frame(E, T, X))
  or by hand: w = SR.window(TXS, T.F); tx, side, u = w; img = SR.tx_mix(E, img_out, img_in, tx, T.F, T.f).
VIRTUAL CAMERA: cam = SR.AutoCam(f0, f1, move='truck', eye=(x, y)); img = cam.render(E, T, plate);
  x, y = cam.at(T.f, px, py) (HUD follows); move push pull pan truck crane orbit spiral drift + accent punch / shake on the
  big hits + motion blur, never shows the plate edge. One line: SR.auto_cam(E, T, plate, f0, f1, ...).
  p2 11-04: kick / six / drift 默认 0（每拍、每 16 分整帧泵和每拍重构图要显式打开），accents 默认 'hits'（只在 TL.HITS 的
  big / drop / final）。第二轮：AutoCam(..., px_per_f=, zoom_pct_per_f=) 直接给每帧单向速度，predict() 报 px/帧。
跟拍：img = SR.track(E, T, hero, WORLDS, f0, f1, move='truck', px_per_f=10, cuts=CUTS, front='snow', P=P)
  主角钉住、身后有纹理的世界单向流过、每个计划切点换世界。
实拍叠层：img = SR.element(E, T, img, 'snow' | 'fog' | 'drip', f0=F0, f1=F1, px_per_f=18, ang=180) —— 母题素材包
  overlays.json 里的黑底实拍窗口，去背景后叠上来（素材自己在动 + 整层单向走 / 迎面推）。SR.particles 不对镜头开放（raise）。
画面自碎：img = SR.shatter(E, img, F, c, cells=28, open=SR.shatter_open(T.f, F), nxt=下一张) —— Voronoi 碎块里是这一帧
  画面本身，单向张开、露出下一张，边上是画面自己的折射（不画裂线）。
卡点（整数帧，读编排器拷进来的 audio/music_map.json = 这首歌实测的起音）：
  SR.slots(f0, f1, ('S', 'A')) -> [(F, 档位, k)] · i = SR.seg(T.F, CUTS) 第几个构图 · SR.env(T.F, F0, hold, decay) 打击包络
  · SR.since(T.F, F0) · SR.accent_frames(f0, f1) · SR.kick(..., frames=[F, ...]) 只在这些帧踢一次.
REAL SPARKS / ARCS: img = SR.overlay(E, img, T.f, 'burst', k=0.8, pos=(x, y), scale=0.6, rot=0, f0=F0, f1=F0 + 30)
  kinds sparks burst spray arc (sample spark clips): real footage baked once (temporal-median background out, only
  the hot cores, feathered: no plate edges, no scratches), recoloured on look v4 ramps, glow regenerated. Not in the storm.
No decorative line segments / boxes / barcodes / dot grids; flat stickers and line segments read as cheap effects: lines, rings
and sparks are OFF by default and only where the series asks for them (timeline.SERIES_FX = 'volt' / 'ignition'): SR.hit turns
the preset arcs / cold glints off unless SERIES_FX == 'volt' (MO.hit_cols gives arcs=0), the voronoi shatter has no crack lines (lk=0), the slam word has no focus lines, the slab's arc outline
is 'volt' only. Kept (the author's own films use them): the charge / impact focus lines (<= 3 frames, never over the subject), the
vortex light edge in the signature colour, HUD at 1.4 px. Matter (snow, ice dust, shards, fog, frost, cracks, bubbles, drops) comes only from real footage (SR.element)."""
import bisect
import json
import math
import numbers
import os
import warnings
import numpy as np
import skia
from src import timeline as TL
from src.engine.canvas import paint
from src.kit import fx, design as D, type as TY
from src.kit.anim import keys, smooth, eoback
from src.kit.core import run, ramp_tex, ramp_array, INK, PAPER, WHITE, W, H, rr, clamp01, lerp, eo3, eo5, ei3, eio3
from src.fx import volt as VT, impact as IM, trans as TR, kaleido as KV

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TAU = 2 * math.pi
COL = dict(blue=VT.L4['ELECTRIC'], ice=VT.L4['ICE'], violet=VT.L4['VIOLET'], deep=VT.L4['DEEP_VIOLET'], amber=VT.AMBER,
           orange=VT.SODIUM, pink=VT.PLASMA_PINK, ember=VT.EMBER, white=VT.ARC_WHITE, ink=INK, paper=PAPER,
           blue_deep=VT.ELECTRIC_DEEP, forge=VT.FORGE)


def _y(c):
    """display luma of a colour (what the frame-mean brightness checks measure)"""
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


# ================================================================== palettes + presets
PAL = {
    'blue': dict(acc=COL['blue'], acc2=COL['ice'], rc=COL['ice'], split=(COL['violet'], COL['blue']), sparks=0, arcs=3, glints=16),
    'amber': dict(acc=COL['amber'], acc2=COL['orange'], rc=COL['amber'], split=(COL['orange'], COL['ice']), sparks=40, arcs=0,
                  glints=0),
    'orange': dict(acc=COL['orange'], acc2=COL['amber'], rc=COL['amber'], split=(COL['orange'], COL['ice']), sparks=40, arcs=0,
                   glints=0),
    'pink': dict(acc=COL['pink'], acc2=COL['blue'], rc=COL['ice'], split=(COL['pink'], COL['blue']), sparks=0, arcs=3, glints=12),
}
PRESETS = {
    'duo_blue': dict(style='duo', pal='blue', ch=3, n=2, af=8, rich=True, col=VT.ELECTRIC_DEEP, col2=VT.ELECTRIC_DEEP),
    'duo_amber': dict(style='duo', pal='amber', ch=3, n=2, af=8),
    'post3_blue': dict(style='post3', pal='blue', ch=3, n=2, af=8, rich=True, col=VT.ELECTRIC_DEEP),
    'post3_orange': dict(style='post3', pal='orange', ch=3, n=2, af=8, rich=True, col=VT.FORGE),
    'lines': dict(style='lines', pal='blue', ch=3, n=1, af=8, arcs=2),
    'lines_focus': dict(style='focus', pal='blue', ch=3, n=1, af=7, arcs=2),
    'half': dict(style='half', pal='blue', ch=3, n=2, af=6, arcs=2),
    'half_blue': dict(style='half', pal='blue', ch=3, n=2, af=6, arcs=2),
    'half_amber': dict(style='half', pal='amber', ch=3, n=2, af=6, sparks=36),
    'plasma': dict(style='plasma', pal='pink', ch=3, n=1, af=9),
    'voronoi': dict(style='post3', pal='blue', ch=3, n=2, af=11, shatter=1.0, shake=1.6, arcs=4, k=1.2, rich=True,
                    col=VT.ELECTRIC_DEEP),
    'kick': dict(style='kick', pal='blue', ch=2, n=1, af=7, dark=0.0, arcs=0, snap=0.06),
}


class SHit(IM.Hit):
    """IM.Hit + a series preset (style, palette, aftermath recipe). Use SR.hit(...)."""

    def __init__(self, F, preset, c=(960.0, 540.0), k=None, **kw):
        if preset not in PRESETS:
            raise KeyError(f'unknown preset {preset!r}: {sorted(PRESETS)}')
        p = dict(PRESETS[preset]); p.update(kw)
        pal = p.pop('pal'); P = PAL[pal]
        self.preset, self.sstyle, self.palname = preset, p.pop('style'), pal
        self.acc = tuple(p.pop('col', P['acc'])); self.acc2 = tuple(p.pop('col2', P['acc2']))
        self.rc = tuple(p.pop('ring_col', P['rc'])); self.split_cols = tuple(p.pop('split_cols', P['split']))
        from src.fx import motif as MO
        volt = MO.series_fx() == 'volt'                               # arcs / glints only where the series asks
        self.nsparks = int(p.pop('sparks', P['sparks']))              # ('volt'); a shot that writes arcs= / glints= still gets them
        arcs, self.nglints = int(p.pop('arcs', P['arcs'])), int(p.pop('glints', P['glints']))
        if not volt:
            arcs = arcs if 'arcs' in kw else 0
            self.nglints = self.nglints if 'glints' in kw else 0
        self.ringk = p.pop('ring', 'soft'); self.splitk = float(p.pop('split', 1.0)); self.snap = float(p.pop('snap', 0.04))
        self.line_col = p.pop('line_col', None)                       # p2: 'lines' preset line colour (None = ice)
        self.spark_ramp = p.pop('spark_ramp', None)                   # debris spark colours (None = fire)
        self.lift = float(p.pop('lift', 0.04)); self.focus = float(p.pop('focus', 1.0)); self.rich = bool(p.pop('rich', False))
        #                                                               the voronoi shatter draws NO crack lines by
        self.lk = float(p.pop('lk', 1.0 if MO.series_fx() == 'volt' else 0.0))   # default (no thin lines, impact.py:317);
        #                                                               lk=1 = the white crack lines + ink gaps ('volt' only)
        pk = p.pop('k', 1.0)
        kk = pk if k is None else k
        IM.Hit.__init__(self, F, 'post3', c=c, k=kk, ch=p.pop('ch'), n=p.pop('n'), af=p.pop('af'), seed=p.pop('seed', None),
                        col=self.acc, shake=p.pop('shake', 1.0), arcs=arcs, ring=bool(self.ringk), dark=p.pop('dark', 0.30),
                        shatter=p.pop('shatter', 0.0))
        if p:
            raise TypeError(f'SR.hit({preset!r}): unknown args {sorted(p)}')


def hit(F, preset, c=(960.0, 540.0), k=None, **kw):
    """one impact cluster. F = first impact frame (int, e.g. TL.F(bar, beat)); c = centre (1080p px) or callable(F) -> (x, y);
    k = strength 0.6..1.5. p2 11-05: arcs and the 'blue' / 'pink' cold glints are OFF unless
    timeline.SERIES_FX == 'volt' or the shot passes them explicitly. Overrides: ch n af (frames), shake, ring ('full' | 'soft' | False), sparks (n), arcs (n),
    split (aftershock x), dark (charge), shatter, lk (shatter crack lines: 0 = none, the p2 default; 1 = the 'volt' white cracks),
    col / col2 (accent colours), snap, lift (impact-frame luma offset),
    spark_ramp (debris spark colours new -> old; sparks are the fire motifs' own matter -- hot streaks out of a real heat source; a cold
    focus (focus-line amount), seed, pal ('blue' | 'amber' | 'orange' | 'pink'), rich (duo / post3: the dense look --
    more ink, the deep accent, paper only on the brightest fragments, footage texture in the edges; on by default for
    duo_blue post3_blue post3_orange voronoi, rich=False = the first version)."""
    return SHit(F, preset, c, k, **kw)


def frames(hits):
    """the impact frames of a HITS list (for the 有音无画 / brightness checks)"""
    return IM.impact_frames(hits)


# ================================================================== GPU helpers
COPY_FS = 'uniform sampler2D a; void main(){ o = texture(a, UV()); }'


def _small(E, tex, div=8):
    """-> numpy (h/div, w/div, 4) float32 of a texture (one small read back; row 0 = top)"""
    t = run(E, COPY_FS, {'a': tex}, size=(max(8, tex.w // div), max(8, tex.h // div)))
    return t.read()


# adaptive value (local contrast + global level) with a ragged value-noise break-up ('满屏碎') and, after a Voronoi
# shatter, a per-shard offset (alpha = shard random). R = value, G = luma.
ADAPT2_FS = '''uniform sampler2D a; uniform float seed; uniform float nk; uniform float ns; uniform float shard;
float Lp(vec2 p){ return luma(texture(a, clamp(p, vec2(0.5), u_size - 0.5) / u_size).rgb); }
float h21(vec2 q){ return fract(sin(dot(q, vec2(127.1, 311.7)) + seed * 0.1373) * 43758.5453); }
float vn(vec2 x){ vec2 i = floor(x); vec2 f = fract(x); f = f * f * (3.0 - 2.0 * f);
  return mix(mix(h21(i), h21(i + vec2(1.0, 0.0)), f.x), mix(h21(i + vec2(0.0, 1.0)), h21(i + vec2(1.0, 1.0)), f.x), f.y); }
void main(){ vec2 p = gl_FragCoord.xy; vec4 t4 = texture(a, UV()); float l = luma(t4.rgb); float m = 0.0;
  for (int i = 0; i < 12; i++){ float an = float(i) * 0.5236;
    m += Lp(p + vec2(cos(an), sin(an)) * 26.0 * u_s) + Lp(p + vec2(cos(an + 0.26), sin(an + 0.26)) * 72.0 * u_s); }
  m /= 24.0;
  float v = 0.5 + 1.5 * (l - m) + 0.6 * (l - 0.5);
  float n = (vn(p / (ns * u_s)) - 0.5) + 0.5 * (vn(p / (0.37 * ns * u_s) + 7.1) - 0.5);
  v += nk * n + shard * (t4.a - 0.5) * 0.45;
  o = vec4(v, l, 0.0, 1.0); }'''
MASK2_FS = '''uniform sampler2D A; uniform float ta; uniform float tp; uniform float e; uniform float ga; uniform float gp;
void main(){ vec4 v = texture(A, UV());
  float acc = ta < 9.0 ? smoothstep(ta - e, ta + e, v.r) * smoothstep(ga, ga + 0.05, v.g) : 0.0;
  float pap = tp < 9.0 ? smoothstep(tp - e, tp + e, v.r) * smoothstep(gp, gp + 0.10, v.g) : 0.0;
  o = vec4(acc, pap, 0.0, 1.0); }'''
GATE_A, GATE_P = 0.07, 0.16            # accent / paper only where there is light (no blobs out of the noise in the blacks)


def _thr(v, lum, gate, share):
    """threshold on v so that `share` of ALL pixels pass, counting only pixels brighter than gate (99 = none)"""
    c = v[lum > gate]
    fc = len(c) / max(len(v), 1)
    if share <= 0 or fc < 1e-3:
        return 99.0
    if fc <= share:
        return float(c.min()) - 1e-3
    return float(np.quantile(c, 1.0 - share / fc))
TONE2_FS = '''uniform sampler2D M; uniform vec3 ink; uniform vec3 c1; uniform vec3 pap;
void main(){ vec4 m = texture(M, UV()); float a = smoothstep(0.28, 0.72, m.r); float p = smoothstep(0.38, 0.78, m.g);
  vec3 res = mix(ink, c1, a); res = mix(res, pap, p); o = vec4(res, 1.0); }'''


def _fractions(m, mode, acc, lift):
    """area shares (accent, paper) that keep the impact frame's mean luma near the frame it replaces (m + lift)"""
    Li, La, Lp = _y(INK), _y(acc), _y(PAPER)
    mt = min(max(m + lift, 0.10), 0.36)
    B = max(mt - Li, 0.0)
    if mode == 'duo':
        return float(np.clip(B / max(La - Li, 0.05), 0.14, 0.50)), 0.0
    pp = float(np.clip(0.40 * B / (Lp - Li), 0.04, 0.16))
    pa = float(np.clip((B - pp * (Lp - Li)) / max(La - Li, 0.05), 0.12, 0.42))
    return pa, pp


# rich: 实拍的色调分离, not flat fills -- the value carries the footage's own fine detail
# (high-pass, kd) so fragment edges follow the real texture; the value-noise break-up is weak; ink dominates (accent <= 30 %,
# paper <= 5 % and only where the plate is bright); a little footage texture stays inside the fills (tk)
ADAPT3_FS = ADAPT2_FS.replace('uniform float shard;', 'uniform float shard; uniform sampler2D b1; uniform sampler2D b2; '
                              'uniform float kd; uniform float fl;') \
    .replace('v += nk * n + shard * (t4.a - 0.5) * 0.45;\n  o = vec4(v, l, 0.0, 1.0); }',
             'float lb = luma(texture(b1, UV()).rgb); float l2 = luma(texture(b2, UV()).rgb); float d = lb - l2;\n'
             '  float g = smoothstep(0.012, 0.035, abs(d)); d *= g;   // band-pass, gated: real structure, not grain\n'
             '  float lv = mix(mix(lb, l2, fl), lb, g);   // fl (duo, 10-26 p3): flat areas (sky gradients) key on the 4.5 px blur\n'
             '  v += 2.1 * (lv - l) + kd * d + nk * n + shard * (t4.a - 0.5) * 0.45; l = lb;\n'
             '  o = vec4(v, l, d, 1.0); }')
assert 'kd * d' in ADAPT3_FS
MASK3_FS = MASK2_FS.replace('o = vec4(acc, pap, 0.0, 1.0); }', 'o = vec4(acc, pap, v.b, 1.0); }')
TONE3_FS = '''uniform sampler2D M; uniform vec3 ink; uniform vec3 c1; uniform vec3 pap; uniform float tk;
void main(){ vec4 m = texture(M, UV()); float a = smoothstep(0.30, 0.70, m.r); float p = smoothstep(0.40, 0.75, m.g);
  float t = clamp(1.0 + tk * m.b, 0.72, 1.28);
  vec3 res = mix(ink, clamp(c1 * t, 0.0, 1.0), a); res = mix(res, clamp(pap * mix(1.0, t, 0.5), 0.0, 1.0), p);
  o = vec4(res, 1.0); }'''
GATE_P2 = 0.30


def _fractions2(m, mode, acc):
    """rich: ink first. Shares (accent, paper) that leave the impact frame ~0.05-0.1 darker than the plate on a typical
    storm / night plate (accent <= 30 % (duo 34 %), paper 1.5-5 %)"""
    Li, La, Lp = _y(INK), _y(acc), _y(PAPER)
    B = max(min(max(m, 0.08), 0.36) - Li, 0.0)
    da, dp = max(La - Li, 0.05), Lp - Li
    lo = m - 0.075                                   # never more than 0.075 darker (x 1.5-2 after the lift2 grade)
    if mode == 'duo':
        pa = float(np.clip(0.75 * B / da, 0.10, 0.34))
        return max(pa, min(0.45, (lo - Li) / da)), 0.0
    pa, pp = float(np.clip(0.55 * B / da, 0.10, 0.30)), float(np.clip(0.12 * B / dp, 0.015, 0.05))
    if Li + pa * da + pp * dp < lo:                  # bright plates: more paper on the brightest first, then accent
        pp = min(0.08, (lo - Li - pa * da) / dp)
        pa = max(pa, min(0.38, (lo - Li - pp * dp) / da))
    return pa, pp


def tone(E, img, F, h, j, mode, shard=False):
    """duo (ink + accent) / post3 (ink / accent / paper), full screen, broken into fragments, luma-matched (rich: ink first,
    the footage's texture in the edges)"""
    if getattr(h, 'rich', False):
        return _tone_rich(E, img, F, h, j, mode, shard)
    A = run(E, ADAPT2_FS, {'a': img}, {'seed': float((h.seed * 31 + F * 7 + j * 13) % 9973), 'nk': 0.16, 'ns': 80.0,
                                       'shard': 1.0 if shard else 0.0})
    s = _small(E, A)
    v, lum = s[..., 0].ravel(), s[..., 1].ravel()
    col = h.acc if (mode == 'post3' or j % 2 == 0) else h.acc2
    pa, pp = _fractions(float(lum.mean()), mode, col, h.lift)
    ta = _thr(v, lum, GATE_A, pa + pp)
    tp = max(_thr(v, lum, GATE_P, pp), ta) if pp > 0 else 99.0
    M = run(E, MASK2_FS, {'A': A}, {'ta': ta, 'tp': tp, 'e': 0.012, 'ga': GATE_A, 'gp': GATE_P})
    M = E.blur(M, 1.1, fast=False)
    return run(E, TONE2_FS, {'M': M}, {'ink': INK, 'c1': tuple(col), 'pap': PAPER})


# duo (KIT p3): duo_blue broke up into fine crumbs on smooth sky gradients (B 1024) -> in FLAT areas the key
# runs on the 4.5 px blur (fl), the threshold edge is softer (e) and the mask a little blurrier (mb); textured areas keep the
# 1.4 px detail. post3 / voronoi keep RICH_P3 (= the first rich version, unchanged).
RICH_P3 = dict(fl=0.0, e=0.010, mb=0.8)
RICH_DUO = dict(fl=1.0, e=0.014, mb=1.2)


def _tone_rich(E, img, F, h, j, mode, shard=False):
    sd = RICH_DUO if mode == 'duo' else RICH_P3
    A = run(E, ADAPT3_FS, {'a': img, 'b1': E.blur(img, 1.4, fast=False), 'b2': E.blur(img, 4.5, fast=False)},
            {'seed': float((h.seed * 31 + F * 7 + j * 13) % 9973), 'nk': 0.06, 'ns': 60.0, 'shard': 1.0 if shard else 0.0,
             'kd': 2.6, 'fl': float(sd['fl'])})
    s = _small(E, A)
    v, lum = s[..., 0].ravel(), s[..., 1].ravel()
    col = h.acc if (mode == 'post3' or j % 2 == 0) else h.acc2
    pa, pp = _fractions2(float(lum.mean()), mode, col)
    ta = _thr(v, lum, GATE_A, pa + pp)
    tp = max(_thr(v, lum, GATE_P2, pp), ta) if pp > 0 else 99.0
    M = run(E, MASK3_FS, {'A': A}, {'ta': ta, 'tp': tp, 'e': float(sd['e']), 'ga': GATE_A, 'gp': GATE_P2})
    return run(E, TONE3_FS, {'M': E.blur(M, float(sd['mb']), fast=False)}, {'ink': INK, 'c1': tuple(col), 'pap': PAPER, 'tk': 3.0})


# 实拍轮廓线稿: relative DoG edges of the plate (IGNITION LINE_FS) -> ICE -> white lines on ink; the light itself stays
LINE2_FS = '''uniform sampler2D a; uniform sampler2D b; uniform sampler2D c; uniform vec3 ink; uniform vec3 lc; uniform vec3 lc2;
uniform float th; uniform float ths; uniform float sp; uniform float keep;
float Lb(vec2 p){ return luma(texture(b, clamp(p, vec2(0.5), u_size - 0.5) / u_size).rgb); }
void main(){ vec2 uv = UV(); vec2 p = gl_FragCoord.xy; vec3 c0 = texture(a, uv).rgb; float y = luma(c0);
  float y1 = luma(texture(b, uv).rgb); float y2 = luma(texture(c, uv).rgb); float e = (y1 - y2) / (y2 + 0.10);
  float s = sp * u_s;                                          // Sobel on the lightly blurred plate (silhouettes, IGNITION)
  float tl = Lb(p + vec2(-s, -s)), t = Lb(p + vec2(0.0, -s)), tr = Lb(p + vec2(s, -s));
  float l = Lb(p + vec2(-s, 0.0)), r = Lb(p + vec2(s, 0.0));
  float bl = Lb(p + vec2(-s, s)), bt = Lb(p + vec2(0.0, s)), br = Lb(p + vec2(s, s));
  float gx = (tr + 2.0 * r + br) - (tl + 2.0 * l + bl); float gy = (bl + 2.0 * bt + br) - (tl + 2.0 * t + tr);
  float gs = length(vec2(gx, gy)) / (0.35 + 2.0 * y2);         // relative: dim edges count, noise in the blacks does not
  float ln = max(smoothstep(th, th * 2.2, e), smoothstep(ths, ths * 2.0, gs));
  vec3 col = mix(ink, mix(lc, lc2, smoothstep(th * 2.2, th * 6.0, e)), ln);
  float g = keep * smoothstep(0.62, 0.95, y);
  col = mix(col, max(col, c0), g);
  o = vec4(col, 1.0); }'''


def lineart(E, img, F, h, j, cx, cy):
    b1, b2, sp = (1.2, 4.5, 1.6) if j % 2 == 0 else (2.0, 6.5, 2.6)
    out = run(E, LINE2_FS, {'a': img, 'b': E.blur(img, b1, fast=False), 'c': E.blur(img, b2)},
              {'ink': INK, 'lc': getattr(h, 'line_col', None) or COL['ice'], 'lc2': COL['white'], 'th': 0.12, 'ths': 0.30, 'sp': sp, 'keep': 0.85})
    if j % 2 == 1 and h.focus > 0:
        cv = E.canvas()
        IM.focus_lines(cv, cx, cy, h.seed + F * 13, n=96, r_in=300.0, wmax=13.0, jitter=0.9, color=WHITE, alpha=0.6 * h.focus)
        out = E.over(out, cv.texture())
    return out


def focus_bw(E, img, F, h, j, cx, cy):
    """B/W high contrast on a dark ground (white = this frame's top ~15-25 %) + dense focus lines that invert what is under
    them (IGNITION bw_lines: n 110+50j, r_in 340-70j, wmax 13+8j)"""
    s = _small(E, img)
    lum = (0.2126 * s[..., 0] + 0.7152 * s[..., 1] + 0.0722 * s[..., 2]).ravel()
    lo = max(float(np.quantile(lum, 0.70)), 0.06); hi = max(float(np.quantile(lum, 0.97)), lo + 0.12)
    out = fx.mono(E, img, lo=lo, hi=hi, s=0.6, c0=INK, c1=WHITE)
    cv = E.canvas()
    IM.focus_lines(cv, cx, cy, h.seed + F * 13, n=int((130 + 50 * j) * h.focus), r_in=320.0 - 70.0 * j, wmax=16.0 + 8.0 * j,
                   jitter=0.9)
    return run(E, IM.INVW_FS, {'a': out, 'm': cv.texture()}, {'k': 1.0})


# IM.HALF_FS with the dot radius capped below half the lattice step: bright areas stay dots (no merged flat fill)
_HR = 'return clamp(r - length(v - dc) + 0.5, 0.0, 1.0); }'
assert _HR in IM.HALF_FS
HALF2_FS = IM.HALF_FS.replace(_HR, 'r = min(r, 0.47 * sp); ' + _HR)


def halftone(E, img, F, h, j, cx, cy):
    """网点爆 on a plain ink ground: log-polar dots sized by brightness (2nd frame coarser, turned, the second colour)"""
    dots = (h.acc2, h.acc)[j % 2] if h.palname == 'blue' else (h.acc, h.acc2)[j % 2]     # blue: ICE then ELECTRIC
    s = _small(E, img, 16)
    m = float((0.2126 * s[..., 0] + 0.7152 * s[..., 1] + 0.0722 * s[..., 2]).mean())
    gain = float(np.clip(0.36 / max(m, 0.05), 0.9, 1.5))        # dim plates: bigger dots (luma); dots never merge
    return run(E, HALF2_FS, {'a': img, 'bl': E.blur(img, 14.0)},
               {'c': (float(cx) * E.s, float(cy) * E.s), 's': 11.0 * E.s * (1.0 + 0.45 * j), 'D': (520.0 + 260.0 * j) * E.s,
                'bg': INK, 'ink': tuple(dots), 'gain': gain, 'rot': 0.21 * F, 'inv': 1})


def _lines_over(E, img, F, h, cx, cy, col, n, r_in, wmax, al):
    if h.focus <= 0:
        return img
    cv = E.canvas()
    IM.focus_lines(cv, cx, cy, h.seed + F * 13, n=int(n * h.focus), r_in=r_in, wmax=wmax, jitter=0.9, color=col, alpha=al)
    return E.over(img, cv.texture())


# ================================================================== the three phases
def charge(E, img, F, h, u, cx, cy):
    if h.sstyle == 'kick':                                       # CHROMA 'kick': no darkening, a light zoom smear only
        return fx.zoom_blur(E, img, cx * E.s, cy * E.s, 0.035 * clamp01(u) * h.k)
    return IM.charge(E, img, F, h, u, cx, cy)


def impact(E, img, F, h, j, cx, cy):
    st = h.sstyle
    shard = False
    if h.shatter > 0:
        img = IM.shatter(E, img, cx, cy, h.seed, (30.0 + 18.0 * j) * h.k * h.shatter, lk=getattr(h, 'lk', 0.0))
        shard = True
    if st == 'duo':
        img = tone(E, img, F, h, j, 'duo', shard)
        if j % 2 == 1:                                           # IGNITION: accent focus lines on the 2nd frame
            img = _lines_over(E, img, F, h, cx, cy, h.acc, 60 + 30 * j, 360.0 - 40.0 * j, 14.0 + 4.0 * j, 0.85)
    elif st == 'post3':
        img = tone(E, img, F, h, j, 'post3', shard)
        if j % 2 == 1:
            img = _lines_over(E, img, F, h, cx, cy, INK, 110, 300.0, 18.0, 1.0)
    elif st == 'lines':
        img = lineart(E, img, F, h, j, cx, cy)
    elif st == 'focus':
        img = focus_bw(E, img, F, h, j, cx, cy)
    elif st == 'half':
        img = halftone(E, img, F, h, j, cx, cy)
    elif st == 'plasma':
        img = fx.false_color(E, img, 'plasma4k', k=1.0, gain=1.45, bias=-0.04, scurve=0.35, bands=6 if j % 2 == 0 else 9)
        if j % 2 == 1:
            img = _lines_over(E, img, F, h, cx, cy, WHITE, 110, 300.0, 16.0, 0.85)
    if st == 'kick':                                             # the camera SNAPS in (crisp), the picture is unchanged
        return E.affine(img, 1.0 + h.snap * h.k, 0.0, (cx, cy))
    if j > 0:
        img = E.affine(img, 1.0 + 0.035 * j * h.k, 0.0, (cx, cy))
    return img


# IGNITION shock ring: a soft refracting lens ring (cool dispersion of the picture's own light) + its thin edge and a second,
# lagging thin ring (inverted picture tinted with the ring colour: reads on dark and on bright plates)
RING2_FS = '''uniform sampler2D a; uniform vec2 c; uniform float r; uniform float w; uniform float amp; uniform float ca;
uniform vec3 t1; uniform vec3 t2; uniform vec3 rc; uniform float r1; uniform float lw1; uniform float la1; uniform float r2;
uniform float lw2; uniform float la2; uniform float glow;
void main(){ vec2 p = gl_FragCoord.xy; vec2 v = p - c; float d = length(v); vec2 nn = d > 1e-3 ? v / d : vec2(0.0);
  float x = (d - r) / w; float g = exp(-x * x); vec2 q = p - nn * (amp * 2.0 * x * g);
  vec3 c0 = texture(a, clamp(q, vec2(0.5), u_size - 0.5) / u_size).rgb; float l0 = luma(c0);
  float l1 = luma(texture(a, clamp(q - nn * ca * g, vec2(0.5), u_size - 0.5) / u_size).rgb);
  float l2 = luma(texture(a, clamp(q + nn * ca * g, vec2(0.5), u_size - 0.5) / u_size).rgb);
  vec3 col = clamp(c0 + t1 * max(l1 - l0, 0.0) + t2 * max(l2 - l0, 0.0), 0.0, 1.0);
  float xg = (d - r) / max(0.35 * w, 1.0); col = clamp(col + rc * glow * exp(-xg * xg), 0.0, 1.0);
  float e1 = la1 * clamp(lw1 * 0.5 - abs(d - r1) + 0.5, 0.0, 1.0);
  float e2 = la2 * clamp(lw2 * 0.5 - abs(d - r2) + 0.5, 0.0, 1.0);
  float e = max(e1, e2);
  col = mix(col, mix(vec3(1.0) - col, rc, 0.45), e);
  o = vec4(col, 1.0); }'''


def ring(E, img, h, t, cx, cy):
    """t = frames since the last impact frame (float). IGNITION after(): lens r (60 + 1500 k) * (1 - (1-u)^2.2), w 70 + 90 u,
    amp 30 k (1-u)^1.4, dispersion 9 k (1-u); thin rings rings(): r 70 -> 1500 (x 0.7 + 0.3 k), width 20 -> 1.5, 2nd ring
    1.7 f later at 0.4 width"""
    k, Tn, s = h.k, float(h.af), E.s
    u = clamp01((t + 1.0) / Tn)
    tt = t + 1.0
    rings = []
    for lag, wk in ((0.0, 1.0), (1.7, 0.4)):
        uu = clamp01((tt - lag) / Tn)
        if 0.0 < uu < 1.0:
            rings.append((lerp(70.0, 1500.0, eo3(uu)) * (0.7 + 0.3 * min(k, 1.3)) * s, lerp(20.0, 1.5, eo3(uu)) * wk * s,
                          (1.0 - uu * uu) * 0.9))
        else:
            rings.append((0.0, 0.0, 0.0))
    (r1, lw1, la1), (r2, lw2, la2) = rings
    return run(E, RING2_FS, {'a': img}, {'c': (float(cx) * s, float(cy) * s),
                                         'r': (60.0 + 1500.0 * k) * (1.0 - (1.0 - u) ** 2.2) * s, 'w': (70.0 + 90.0 * u) * s,
                                         'amp': 30.0 * k * (1.0 - u) ** 1.4 * s, 'ca': 9.0 * k * (1.0 - u) * s,
                                         't1': h.split_cols[0], 't2': h.split_cols[1], 'rc': h.rc, 'glow': 0.10 * k * (1.0 - u) ** 1.5,
                                         'r1': r1, 'lw1': lw1, 'la1': la1, 'r2': r2, 'lw2': lw2, 'la2': la2})


_SPARK_RAMP = {}


def _spark_col(age, ramp=None):
    """colour of a spark at age 0..1 on a 64-step ramp: None = the fire ramp VT 'spark4' (white-hot -> amber -> sodium ->
    ember); or any list of colours (sRGB tuples / hex), new -> old, e.g. (P['core'], P['pale'], P['acc'], P['deep'])"""
    key = 'r' if ramp is None else tuple(tuple(c) if not isinstance(c, str) else c for c in ramp)
    if key not in _SPARK_RAMP:
        _SPARK_RAMP[key] = ramp_array(VT.RAMPS['spark4'] if ramp is None else list(ramp), 64)[0]
    r = _SPARK_RAMP[key]
    return tuple(float(x) for x in r[min(63, max(0, int(age * 63.0)))])


def sparks(cv, cx, cy, t, seed, n=40, k=1.0, life=10.0, grav=0.9, ramp=None):
    """IGNITION impact.sparks: hot streaks shot out of (cx, cy), drag + gravity, cooling white-hot -> amber -> sodium orange ->
    ember (VT ramp 'spark4') and thinning; streak = the distance travelled in ~1.4 frames (motion blur).
    ramp = 换配色（新 -> 旧的颜色列表；None = 火的配色）。火花只属于从实拍热源喷出的那一下（点火）；
    冰屑、雪、穿镜冰晶不用它：SR.element（实拍叠层）、SR.shatter（画面自碎）。"""
    if t < 0 or t >= life:
        return
    for i in range(n):
        a = rr(0, TAU, seed, i, 'a')
        v0 = rr(18, 70, seed, i, 'v') * k
        drag = rr(0.10, 0.22, seed, i, 'd')
        r0 = rr(10, 90, seed, i, 'r0')
        tl = rr(0.55, 1.0, seed, i, 'l') * life
        if t >= tl:
            continue

        def pos(tt):
            d = r0 + v0 * (1 - math.exp(-drag * tt)) / drag
            return cx + d * math.cos(a), cy + d * math.sin(a) + 0.5 * grav * tt * tt
        age = t / tl
        x1, y1 = pos(t); x0, y0 = pos(max(0.0, t - 1.4))
        col = _spark_col(age ** 0.6, ramp)
        al = 1 - age ** 2
        wdt = rr(1.6, 4.2, seed, i, 'w') * (1 - 0.5 * age) * (0.7 + 0.3 * k)
        cv.c.drawLine(x0, y0, x1, y1, paint(col, al * 0.35, wdt * 2.6, 'round'))
        cv.c.drawLine(x0, y0, x1, y1, paint(col, al, wdt, 'round'))


def glints(cv, cx, cy, t, seed, n=16, k=1.0, life=8.0):
    """fine cold micro-glints for the blue palettes (no metal sparks in a storm): tiny ICE points thrown
    out of (cx, cy) with strong drag and no gravity, a short streak, fading to ELECTRIC; alpha <= 0.55, 0.8-1.6 px"""
    if t < 0 or t >= life:
        return
    for i in range(n):
        a = rr(0, TAU, seed, i, 'a')
        v0 = rr(14, 46, seed, i, 'v') * k
        drag = rr(0.25, 0.45, seed, i, 'd')
        r0 = rr(40, 160, seed, i, 'r0')
        tl = rr(0.45, 1.0, seed, i, 'l') * life
        if t >= tl:
            continue

        def pos(tt):
            d = r0 + v0 * (1 - math.exp(-drag * tt)) / drag
            return cx + d * math.cos(a), cy + d * math.sin(a)
        age = t / tl
        x1, y1 = pos(t); x0, y0 = pos(max(0.0, t - 0.8))
        col = tuple(lerp(c0, c1, age) for c0, c1 in zip(COL['ice'], COL['blue']))
        al = 0.55 * (1 - age ** 1.5)
        wdt = rr(0.8, 1.6, seed, i, 'w')
        cv.c.drawLine(x0, y0, x1, y1, paint(col, al * 0.25, wdt * 2.4, 'round'))
        cv.c.drawLine(x0, y0, x1, y1, paint(col, al, wdt, 'round'))


def after(E, T, img, h, t, cx, cy):
    """t = frames since the last impact frame (0 .. af), float (sub-frame motion)"""
    Tn, k = float(h.af), h.k
    if h.shatter > 0:                                             # the shards settle back WITHOUT crack lines
        q = 1.0 - eo3(clamp01((t + 0.5) / (0.6 * Tn)))
        if q > 0.01:
            img = IM.shatter(E, img, cx, cy, h.seed, (30.0 + 18.0 * (h.n - 1)) * 1.2 * k * h.shatter * q, lk=0.0)
    if h.ringk == 'soft':
        u = clamp01((t + 1.0) / Tn)
        img = run(E, IM.SHOCK_FS, {'a': img}, {'c': (float(cx) * E.s, float(cy) * E.s),
                                               'r': (60.0 + 1400.0 * k) * (1.0 - (1.0 - u) ** 2.2) * E.s,
                                               'w': (110.0 + 170.0 * u) * E.s, 'amp': 24.0 * k * (1.0 - u) ** 1.4 * E.s,
                                               'rim': 0.0, 'ca': 7.0 * k * (1.0 - u) * E.s,   # v13：暗画面上 rim 是一整圈灰蓝雾带（又是个圈），关掉
                                               'rc': h.rc, 't1': h.split_cols[0], 't2': h.split_cols[1]})
    elif h.ringk:
        img = ring(E, img, h, t, cx, cy)
    Hs = []
    f_end = h.F + h.n - 1
    tau = 2.6 if h.sstyle == 'kick' else 3.0
    for tt in T.taps(3):
        sx, sy, sr = fx.shake(tt, f_end, 16.0 * k * h.shake, decay=3.2, seed=h.seed + 5)
        zz = 1.0 + h.snap * k * math.exp(-max(0.0, tt - f_end) / tau)
        Hs.append(fx.pose_H(zoom=zz, roll=math.degrees(sr) * 0.8, piv=(cx, cy), at=(cx + sx, cy + sy)))
    img = fx.camera(E, img, Hs, edge='clamp')
    sk = h.splitk * (1.4 if h.sstyle == 'kick' else 1.0)
    if sk > 0:
        img = run(E, IM.COOLSPLIT_FS, {'a': img}, {'c': (float(cx) * E.s, float(cy) * E.s), 'amt': 0.007 * k * sk * math.exp(-t / 2.5),
                                                   'dd': (4.0 * k * sk * math.exp(-t / 2.0) * E.s, 0.0),
                                                   't1': h.split_cols[0], 't2': h.split_cols[1]})
    if h.nsparks > 0 or h.nglints > 0:
        cv = E.canvas()
        if h.nsparks > 0:                                         # warm palettes only: welding / cutting / filament
            sparks(cv, cx, cy, t + 1.0, h.seed + 11, n=h.nsparks, k=k, life=Tn + 2.0, ramp=getattr(h, 'spark_ramp', None))
        if h.nglints > 0:                                         # blue: a few fine cold glints, restrained
            glints(cv, cx, cy, t + 1.0, h.seed + 17, n=h.nglints, k=k, life=0.8 * Tn + 2.0)
        img = E.over(img, cv.texture())
    if h.arcs:
        img = IM.arcs(E, img, h, t, cx, cy)
    return img


def post(E, T, img, hits):
    """apply the active phase of every cluster at T.F to the finished frame (call last in the shot). SR hits and plain
    IM.Hit objects can be mixed."""
    for h, ph, u in IM.active(hits, T.F):
        cx, cy = h.center(T.F)
        if not isinstance(h, SHit):
            if ph == 'charge':
                img = IM.charge(E, img, T.F, h, u, cx, cy)
            elif ph == 'impact':
                img = IM.impact(E, img, T.F, h, u, cx, cy)
            else:
                img = IM.after(E, T, img, h, T.f - (h.F + h.n), cx, cy)
            continue
        if ph == 'charge':
            img = charge(E, img, T.F, h, u, cx, cy)
        elif ph == 'impact':
            img = impact(E, img, T.F, h, u, cx, cy)
        else:
            img = after(E, T, img, h, T.f - (h.F + h.n), cx, cy)
    return img


def card(E, img, F, preset, c=(960.0, 540.0), j=0, k=1.0, **kw):
    """the impact-frame look alone on any picture (the trailer's style cells, design cards): SR.card(E, img, T.F, 'duo_amber')"""
    h = SHit(F, preset, c, k, **kw)
    return impact(E, img, F, h, j, c[0], c[1])


# ================================================================== HUD (skia, 1080p units)
UNITS = {'kV', 'kA', 'MV', 'GV', 'mA', 'kW', 'MW', 'GW', 'Hz', 'kHz', 'MHz', 'km', 's', 'ms'}


def caps(s):
    """upper-case a mono label but keep SI units as written: '5 kV' stays '5 kV' (TY's caps would print '5 KV' = kelvin)"""
    import re
    return ''.join(t if t in UNITS else t.upper() for t in re.split(r'(\s+)', s))


def _lines(lines):
    return [caps(s) for s in ([lines] if isinstance(lines, str) else list(lines))]


def at(pose_fn, f, x, y):
    """screen position of plate point (x, y) (plate px in 1080p units) under pose_fn(f) (a pose dict or a 3x3)"""
    p = pose_fn(f)
    Hm = p if isinstance(p, np.ndarray) else fx.pose_H(**{k_: v for k_, v in p.items() if k_ != 'src'})
    return fx.apply_H(Hm, x, y)


def _quart(f, f0, f1, din, dout):
    pin = eo3(clamp01((f - f0 + 1.0) / din))
    pout = eo3(clamp01((f - (f1 - dout)) / dout)) if f1 is not None else 0.0
    return pin, pout


def ring_hud(cv, x, y, f, f0, r=300.0, f1=None, col=WHITE, alpha=0.75, spin=1.5, ticks=None, major=None, arcs=True,
             cross=True, label=None, label_ang=-38.0, din=6.0, dout=5.0, width=1.4):
    """the subject ring (IGNITION): crosshair + tick ring at r + two thin big arcs at 1.40 r / 1.51 r (r 300 -> 420 /
    452 like the sparkler core). In: draws on over din frames while the radius settles 1.3 r -> r; holds spinning slowly
    (spin deg/frame, the arcs the other way); out (f1 given): retracts over dout frames. label = ['NAME', 'VALUE'] hangs
    off the ring at label_ang deg (callout)."""
    u = f - f0
    if u < -0.5:
        return
    pin, pout = _quart(f, f0, f1, din, dout)
    if pout >= 1.0:
        return
    a = alpha * (1.0 - pout)
    R = r * (1.0 + 0.3 * (1.0 - pin)) * (1.0 - 0.12 * pout)
    n = ticks or (96 if r < 230 else 120 if r < 330 else 144)
    mj = major or max(1, n // 12)
    D.tick_ring(cv, x, y, R, p=pin * (1.0 - pout), ticks=n, major=mj, color=col, alpha=a, width=width,
                tick=8.0 if r < 250 else 9.0, a0=-90.0 + spin * u + 360.0 * pout)
    if arcs:
        D.big_arc(cv, x, y, R * 1.40, -90.0 + spin * u + 300.0 * pout, 300.0 * (1.0 - pout), p=eo3(clamp01((u + 1.0) / (din + 1.0))),
                  color=col, alpha=min(1.0, 0.93 * a), width=width)
        D.big_arc(cv, x, y, R * 1.507, 120.0 - 1.7 * spin * u, 160.0 * (1.0 - pout), p=eo3(clamp01(u / (din + 1.0))),
                  color=col, alpha=0.6 * a, width=width * 0.86)
    if cross:
        D.crosshair(cv, x, y, 30.0, col, min(1.0, 1.2 * a) * eo3(clamp01((u + 1.0) / 3.0)), width)
    if label:
        callout(cv, x, y, R, label_ang, label, f, f0 + 2.0, col=col, alpha=min(1.0, 1.15 * a), width=width)


def callout(cv, x, y, r, ang, lines, f, f0, cps=2.0, col=WHITE, alpha=0.88, size=22.0, stub=34.0, shoulder=26.0, width=1.4,
            f1=None, dout=4.0):
    """a label hanging off a ring: a short radial stub from the ring at ang deg + a shoulder, then the mono lines typed on
    (cps chars / frame). Attached to the ring -- never a free-floating line. f1: fades / retracts over dout frames."""
    u = f - f0
    if u < -0.5 or not lines:
        return
    pout = eo3(clamp01((f - (f1 - dout)) / dout)) if f1 is not None else 0.0
    if pout >= 1.0:
        return
    alpha *= 1.0 - pout
    ca, sa = math.cos(math.radians(ang)), math.sin(math.radians(ang))
    side = 1.0 if ca >= 0 else -1.0
    p0 = (x + r * ca, y + r * sa)
    p1 = (x + (r + stub) * ca, y + (r + stub) * sa)
    p2 = (p1[0] + side * shoulder, p1[1])
    path = skia.Path(); path.moveTo(*p0); path.lineTo(*p1); path.lineTo(*p2)
    D.draw_path(cv, path, pout, eo3(clamp01((u + 1.0) / 3.0)), col, alpha, width, cap='butt')
    D.tag_block(cv, p2[0] + side * 10.0, p2[1] + 8.0, _lines(lines), t=u - 1.5, cps=cps, size=size, color=col, alpha=alpha,
                bar=False, align='left' if side > 0 else 'right', caps=False)


def counter(f, kf, step=None, grid=0.0):
    """a value that STEPS on the 16th-note grid (跳数): kf = keys() list [(frame, value[, ease]), ...]; lands exactly on the
    last value from its frame on. (The value it lands on must be a checked number; the steps before it are
    a counter, like IGNITION's 020 -> 525 °C.)"""
    if f >= kf[-1][0]:
        return kf[-1][1]
    st = step or TL.SIXTEENTH
    g0 = TL.f(1) + grid
    fq = g0 + math.floor((f - g0) / st + 1e-6) * st
    return keys(max(fq, kf[0][0]), kf)


def readout(cv, x, y, f, kf, fmt='{:,.0f}', unit='', step=None, size=22.0, col=WHITE, alpha=0.9, align='left', key='mono'):
    """draw counter(f, kf) with fmt (',' -> space: '28 000') + unit (kept as written: ' kV'); returns the value"""
    v = counter(f, kf, step)
    TY.word(cv, caps(fmt.format(v).replace(',', ' ') + unit), x, y, size, key, col, alpha, align, caps=False)
    return v


def tag(cv, x, y, lines, f, f0, cps=2.2, size=22.0, col=WHITE, alpha=0.92, bar=True, align='left'):
    """typed mono label block (IGNITION D.tag_block, cps chars / frame from f0); units stay as written"""
    D.tag_block(cv, x, y, _lines(lines), t=f - f0 + 1.0, cps=cps, size=size, color=col, alpha=alpha, bar=bar, align=align,
                caps=False)


def ring_text(cv, x, y, r, text, f, f0=None, size=22.0, key='mono', speed=-0.6, col=WHITE, alpha=0.8, din=8.0, a0=-90.0,
              f1=None, dout=6.0, safe=(150.0, 952.0)):
    """text around a circle (IGNITION text_ring), repeated to fill the circumference, turning speed deg / frame; with f0
    the letters type on around the ring over din frames, with f1 they un-type over dout frames. Letters whose centre falls
    above safe[0] / below safe[1] (the frame_hud / volt_scale bands) or off the frame fade out (no collisions)."""
    s = caps(text)
    runs, L = TY.layout(s, key, float(size), TY.TRACK.get(key, 0.0))
    if L <= 0 or r <= 0:
        return
    C = TAU * r
    reps = max(1, int(C // L))
    sp = C / (reps * L)
    p = 1.0 if f0 is None else eo3(clamp01((f - f0 + 1.0) / din))
    if f1 is not None:
        p = min(p, 1.0 - eo3(clamp01((f - (f1 - dout)) / dout)))
    if p <= 0:
        return
    base = math.radians(a0 + speed * (f - (f0 or 0.0)))
    ch = TY.cap_height(key, size)
    pt = paint(col, alpha)
    for rep in range(reps):
        for chh, ff, dx in runs:
            pos = (rep * L + dx) / (reps * L)
            if pos > p:
                continue
            gw = ff.measureText(chh)
            ang = base + ((rep * L + dx + gw / 2) * sp) / r
            gx, gy = x + r * math.cos(ang), y + r * math.sin(ang)
            fade = clamp01((gy - safe[0]) / 20.0) * clamp01((safe[1] - gy) / 20.0) * clamp01((gx - 60.0) / 20.0) * \
                clamp01((W - 60.0 - gx) / 20.0) if safe else 1.0
            if fade <= 0.0:
                continue
            pt.setAlphaf(float(alpha * fade))
            cv.c.save()
            cv.c.translate(x + r * math.cos(ang), y + r * math.sin(ang))
            cv.c.rotate(math.degrees(ang) + 90.0)
            cv.c.drawString(chh, -gw / 2, ch / 2, ff, pt)
            cv.c.restore()


def corners(cv, col=WHITE, alpha=0.55, f=None, f0=None, inset=36.0, arm=22.0, width=1.4, din=5.0):
    """four corner marks (CHROMA v1 / IGNITION: 36 px from the edge, 22 px arms); with f0 the arms draw on"""
    if f0 is None or f is None:
        D.crop_marks(cv, col, alpha, inset, arm, width)
        return
    k = eo3(clamp01((f - f0 + 1.0) / din))
    if k <= 0:
        return
    p = paint(col, alpha, width)
    a = arm * k
    for x, y, sx, sy in ((inset, inset, 1, 1), (W - inset, inset, -1, 1), (inset, H - inset, 1, -1), (W - inset, H - inset, -1, -1)):
        x, y = round(x), round(y)
        cv.c.drawLine(x, y, x + sx * a, y, p); cv.c.drawLine(x, y, x, y + sy * a, p)


def frame_hud(cv, T, tag=None, tag_t0=None, label=None, label_t0=None, counter=True, ink=False, alpha=1.0, corners_t0=None):
    """IGNITION hud(): corner marks, section tag top left (typed), frame counter top right, subject label bottom left"""
    col = INK if ink else WHITE
    corners(cv, col, (0.6 if ink else 0.55) * alpha, T.f, corners_t0)
    if tag:
        D.tag_block(cv, 96, 126, _lines(tag), t=1e9 if tag_t0 is None else T.f - tag_t0, cps=1.4, color=col,
                    alpha=0.92 * alpha, caps=False)
    if counter:
        TY.word(cv, f'F {T.F:04d}', 1824, 126, 22, 'mono', col, 0.8 * alpha, 'right')
    if label:
        D.tag_block(cv, 96, 1010, _lines(label), t=1e9 if label_t0 is None else T.f - label_t0 + 1.0, cps=1.5, color=col,
                    alpha=0.88 * alpha, bar=False, caps=False)



VOLT_MARKS = ((220.0, '220 V'), (5e3, '5 kV'), (1.1e6, '±1100 kV'), (3e8, '300 000 000 V'))   # checked values


def volt_scale(cv, x0=600.0, x1=1740.0, y=1000.0, f=0.0, v=220.0, f0=None, col=WHITE, alpha=0.8, mcol=None, vmin=100.0,
               vmax=1e9, marks=VOLT_MARKS, din=8.0):
    """log voltage gauge (the VOLT version of IGNITION's temperature gauge): decade ticks drawn on, the checked voltages
    labelled, a marker + progress line at v (step v with counter() so it jumps on the 16ths). Default place = the bottom
    band right of frame_hud's subject label (clear of ring_text's safe band): SR.volt_scale(cv, f=T.f, v=v, f0=f0)"""
    p = 1.0 if f0 is None else eo3(clamp01((f - f0 + 1.0) / din))
    if p <= 0:
        return
    l0, l1 = math.log10(vmin), math.log10(vmax)
    X = lambda val: x0 + (x1 - x0) * (math.log10(max(val, vmin)) - l0) / (l1 - l0)
    pp = paint(col, alpha * 0.75, 1.4)
    for dec in range(int(l0), int(l1) + 1):
        for m in range(1, 10):
            val = m * 10.0 ** dec
            if val < vmin or val > vmax:
                continue
            xx = X(val)
            if xx > x0 + (x1 - x0) * p:
                continue
            L = 14.0 if m == 1 else 7.0
            cv.c.drawLine(xx, y, xx, y - L, pp)
    mc = mcol or COL['ice']
    for val, lab in marks:
        xx = X(val)
        if xx > x0 + (x1 - x0) * p:
            continue
        on = v >= val * 0.999
        TY.word(cv, caps(lab), xx, y + 32, 22, 'mono', mc if on else col, (0.95 if on else 0.5) * alpha, 'center', caps=False)
    xv = X(v)
    if xv <= x0 + (x1 - x0) * p + 1:
        cv.line(x0, y + 1, xv, y + 1, mc, 0.95 * alpha, 3.0)
        tri = skia.Path(); tri.moveTo(xv - 10, y - 21); tri.lineTo(xv + 10, y - 21); tri.lineTo(xv, y - 6); tri.close()
        cv.c.drawPath(tri, paint(mc, alpha))


HUDSPLIT_FS = '''uniform sampler2D a; uniform sampler2D h; uniform vec2 c; uniform float amt; uniform float d0; uniform vec3 t1;
uniform vec3 t2; uniform float k;
void main(){ vec2 p = gl_FragCoord.xy; vec2 v = p - c; float L = length(v); vec2 n = L > 1e-3 ? v / L : vec2(1.0, 0.0);
  vec2 d = n * (d0 + L * amt);
  vec4 h0 = texture(h, p / u_size); float a1 = texture(h, clamp(p - d, vec2(0.5), u_size - 0.5) / u_size).a;
  float a2 = texture(h, clamp(p + d, vec2(0.5), u_size - 0.5) / u_size).a;
  vec4 b = texture(a, UV()); vec3 col = b.rgb * (1.0 - h0.a) + h0.rgb;
  col = mix(col, t1, clamp(max(a1 - h0.a, 0.0) * k, 0.0, 1.0));
  col = mix(col, t2, clamp(max(a2 - h0.a, 0.0) * k, 0.0, 1.0));
  o = vec4(col, 1.0); }'''


def hud_over(E, img, layer, split=0.0, c=(960.0, 540.0), cols=None):
    """composite a HUD canvas / texture over img; split > 0 adds a little chromatic fringe (ICE out / VIOLET in by default)
    growing with the distance from c (split 1 ~ 2 px + 0.4 % of the radius)"""
    tex = layer.texture() if hasattr(layer, 'texture') else layer
    if split <= 0:
        return E.over(img, tex)
    t1, t2 = cols or (COL['ice'], COL['violet'])
    return run(E, HUDSPLIT_FS, {'a': img, 'h': tex}, {'c': (float(c[0]) * E.s, float(c[1]) * E.s), 'amt': 0.004 * split,
                                                     'd0': 2.0 * split * E.s, 't1': tuple(t1), 't2': tuple(t2),
                                                     'k': min(1.0, 0.85 * split)})


def ruler(*a, **kw):
    """= kit.design.ruler (scale ticks drawn on by p)"""
    return D.ruler(*a, **kw)


# ================================================================== camera (pure functions)
def punch(f, f0, amp, dur=7.0):
    """IGNITION: camera kick -- jumps by amp on f0, relaxes over dur frames (zoom *= 1 + punch(...))"""
    t = f - f0
    return amp * (1.0 - eo3(t / dur)) if t >= 0 else 0.0


def step(f, f0, amp, dur=3.0):
    """snap move that stays"""
    t = f - f0
    return amp * eo5(t / dur) if t >= 0 else 0.0


def slam(t, n=5.0, s0=1.32, back=0.0):
    """type slam scale (IGNITION; back > 0 = overshoot eoback(t / n, back))"""
    if t < 0:
        return 0.0
    e = eoback(t / n, back) if back > 0 else eo5(t / n)
    return 1.0 + (s0 - 1.0) * (1.0 - e)


def whip(E, img, f, f_cut, d=3, px=900.0, vertical=False, sign=1.0, ang=None):
    """甩镜拖影 (IGNITION): directional smear growing into the cut (f_cut - d .. f_cut), decaying after it, the same
    direction on both sides; ang (deg) overrides horizontal / vertical"""
    t = f - f_cut
    if -d <= t < 0:
        a = ei3((t + d + 1) / (d + 1))
    elif 0 <= t < d:
        a = 1.0 - eo3((t + 1) / (d + 1))
    else:
        return img
    v = px * a * sign * E.s
    if ang is not None:
        return fx.dir_blur(E, img, v * math.cos(math.radians(ang)), v * math.sin(math.radians(ang)))
    return fx.dir_blur(E, img, 0.0 if vertical else v, v if vertical else 0.0)


def whip_in(f, f0, dist, n=4.0):
    """camera arriving from a whip: remaining offset (px) n frames after f0 (IGNITION)"""
    return dist * (1.0 - eo3((f - f0) / n)) if f >= f0 else dist


J_FREE = [(0.0, 0.0, 0.0, 0.0), (0.40, -170.0, -70.0, -4.5), (0.16, 180.0, 60.0, 3.0), (0.56, 70.0, -90.0, -5.5)]


def drift(f, a, J=None, j=1, amt=0.3, dur=None):
    """IGNITION: continuous re-framing from the grid point a: amt x J[j] = (dzoom, dpivot x, dpivot y, droll) spread over
    about one beat, half linear / half eased (never snaps)"""
    J = J or J_FREE
    u = clamp01((f - (a - 0.5)) / (dur or TL.BEAT + 3.0))
    u = 0.5 * u + 0.5 * eio3(u)
    return tuple(amt * u * x for x in J[j % len(J)])


def rumble(f, amp, f0=None, f1=None):
    """launch vibration (px): two incommensurate sines per axis (IGNITION) -> (dx, dy).
    只给发射 / 爆炸用，≤1 小节：f0 / f1 = 窗口（片子帧号），首尾各 6 帧淡入淡出，窗口外 (0, 0)。整镜一直抖是原地来回，
    编排器量出来是 0，不算运动。"""
    w = 1.0
    if f0 is not None:
        w *= clamp01((f - f0) / 6.0)
    if f1 is not None:
        w *= clamp01((f1 - f) / 6.0)
    if w <= 0.0:
        return 0.0, 0.0
    a = amp * w
    return (a * (math.sin(f * 2.31) + 0.5 * math.sin(f * 5.13 + 1.0)), a * (math.cos(f * 2.87) + 0.5 * math.sin(f * 4.37 + 2.0)))


def moves(f, M):
    """IGNITION: continuous pushes M = [(a, b, dzoom, dx, dy, droll[, 's'])], linear a -> b ('s' = smoothstep)
    -> (zoom x, dx, dy, droll)"""
    z, x, y, r = 1.0, 0.0, 0.0, 0.0
    for m in M:
        a, b, dz, dx, dy, dr = m[:6]
        u = clamp01((f - a) / float(b - a))
        if len(m) > 6 and m[6] == 's':
            u = smooth(u)
        z *= 1.0 + dz * u; x += dx * u; y += dy * u; r += dr * u
    return z, x, y, r


_WARNED = set()


def _warn_once(key, msg):
    if key not in _WARNED:
        _WARNED.add(key)
        warnings.warn(msg, stacklevel=3)


def _fk(x):
    """卡点帧的几种写法 -> (整数帧, 大小 k)：F、(F, k)、SR.slots 的 (F, 档位, k)"""
    if isinstance(x, (tuple, list)):
        k = x[-1] if len(x) >= 2 and isinstance(x[-1], numbers.Real) else 1.0
        return int(round(x[0])), float(k)
    return int(round(x)), 1.0


def kick(F, f, f0, z=0.05, r=1.4, z16=0.0, beat=None, six=None, piv=(W / 2, H / 2), frames=None):
    """kick -> 3x3 to left-multiply a shot's camera (fx.camera(..., [K @ H])).
    frames = 卡点的整数帧（[F, ...]、[(F, k), ...] 或 SR.slots(...) 的结果）：只在 f 之前最近的那一帧踢一次——zoom +z·k、
    roll ±r·k（按序号左右交替），落在那一帧上、3.5 帧指数衰减；第一帧之前不动。
    不给 frames = 旧的网格泵：从 f0 起每拍一踢（zoom +z、roll 左右交替），z16 > 0 时再每 16 分一泵（2.2 帧衰减；默认 0）。
    网格泵是原地来回（编排器量出来是 0），只能叠在一路走到底的推进上，不能当这一镜唯一的动，也不算卡点；会 warn 一次。"""
    if frames is not None:
        fr = sorted(_fk(x) for x in frames)
        i = bisect.bisect_right([a - 0.5 for a, _ in fr], f) - 1
        if i < 0:
            return fx.pose_H(zoom=1.0, roll=0.0, piv=piv, at=piv)
        Fk, kk = fr[i]
        kb = kk * math.exp(-max(0.0, f - (Fk - 0.5)) / 3.5)
        return fx.pose_H(zoom=1 + z * kb, roll=r * kb * (1 if i % 2 == 0 else -1), piv=piv, at=piv)
    _warn_once('kick', 'SR.kick 没给 frames = 每拍整帧泵：原地来回不算运动、也不算卡点，只叠在一路走到底的推进上'
                       '（卡点用 frames=[表里的帧]）')
    bt, sx = beat or TL.BEAT, six or TL.SIXTEENTH
    b = math.floor((f - f0 + 0.5) / bt)
    kb = math.exp(-max(0.0, f - (round(f0 + bt * b) - 0.5)) / 3.5) if b >= 0 else 0.0
    s = math.floor((f - f0 + 0.5) / sx)
    k16 = math.exp(-max(0.0, f - (round(f0 + sx * s) - 0.5)) / 2.2) if s >= 0 else 0.0
    return fx.pose_H(zoom=(1 + z * kb) * (1 + z16 * k16), roll=r * kb * (1 if b % 2 == 0 else -1), piv=piv, at=piv)


def push_thru(f, f_cut, n=3.5, amt=2.6):
    """推穿 into a cut (IGNITION pose_c3 / pose_match): zoom multiplier 1 -> 1 + amt over the last n frames (ease-in)"""
    return 1.0 + amt * ei3(clamp01((f - (f_cut - n)) / n))


_AREA = {}


def area_r(c, u):
    """radius of the disc (centre c, 1080p px) that covers the fraction u of the frame -> circle wipes that grow at a
    constant in-frame area rate (IGNITION)"""
    key = (int(round(c[0])), int(round(c[1])))
    if key not in _AREA:
        ys, xs = np.mgrid[0:1080:6, 0:1920:6]
        _AREA[key] = np.sort(np.hypot(xs + 3.0 - c[0], ys + 3.0 - c[1]).ravel())
    d = _AREA[key]
    return float(d[int(clamp01(u) * (len(d) - 1))]) if u > 0 else 0.0


def _area_of(c, r):
    """inverse of area_r: the fraction of the frame inside the disc (c, r)"""
    area_r(c, 0.5)
    d = _AREA[(int(round(c[0])), int(round(c[1])))]
    return float(np.searchsorted(d, r)) / len(d)


def _far(cx, cy):
    """distance from (cx, cy) to the farthest frame corner"""
    return max(math.hypot(x - cx, y - cy) for x in (0.0, W) for y in (0.0, H))


# ================================================================== DESIGNED TRANSITIONS
# Every cut is a visible design. Window semantics = TR.Cut:
# frame = the first frame of the next shot, out window [frame - n_out, frame), in window [frame, frame + n_in); inside it
# BOTH shots are rendered (except 'hit') and tx_mix designs the swap. No white / black veil: the swap is a shape (circle,
# burn, stripes, blocks, kaleidoscope), an impact, or a camera move (the TR kinds, cap=0 = full strength).
class _TT:
    """minimal time handle (F, f, taps) for the helpers that want T (impact aftermath, Vortex) inside tx_mix / tests"""
    __slots__ = ('F', 'f')

    def __init__(self, F, f=None):
        self.F, self.f = int(F), float(F if f is None else f)

    def taps(self, n):
        a, b = self.f - 0.25, self.f + 0.25
        return [self.f] if n <= 1 else [a + (b - a) * (i + 0.5) / n for i in range(n)]


TX_KINDS = ('circle', 'iris', 'arcburn', 'stripes', 'blocks', 'kaleido', 'hit')
TX_N = dict(circle=(4, 9), iris=(9, 4), arcburn=(3, 10), stripes=(7, 7), blocks=(5, 6), kaleido=(14, 8))
TR_N = dict(whip=(5, 6), push=(5, 7), pull=(6, 7), smear=(4, 4), spin=(7, 8), ramp=(8, 10), occlude=(6, 7), boltwipe=(6, 7),
            shape=(5, 6), light=(6, 7))
TR_KW = dict(whip=dict(strength=1.3), push=dict(zoom=2.4), pull=dict(strength=1.2), smear=dict(strength=1.3),
             ramp=dict(strength=1.2), shape=dict(strength=1.2))


class Tx:
    """one designed cut (make it with SR.tx). c = the screen point the design centres on (1080p px, both shots; 'hit'
    also takes a callable(F)); kw = the kind's options (see SR.tx)."""

    def __init__(self, frame, kind, n_out=None, n_in=None, c=(960.0, 540.0), seed=None, **kw):
        if kind not in TX_KINDS:
            raise ValueError(f'SR.tx kind {kind!r}: use {TX_KINDS} or a TR kind {TR.KINDS}')
        self.frame, self.kind, self.c, self.kw = int(round(frame)), kind, c, dict(kw)
        self.seed = int(self.frame * 7 + 3 if seed is None else seed)
        self.hit = None
        if kind == 'hit':
            preset, k = self.kw.pop('preset', 'post3_blue'), self.kw.pop('k', None)
            self.hit = SHit(self.frame, preset, c, k, **self.kw)
            d = (self.hit.ch, self.hit.n + self.hit.af)
        else:
            d = TX_N[kind]
        self.n_out = max(1, int(d[0] if n_out is None else n_out))
        self.n_in = max(1, int(d[1] if n_in is None else n_in))
        self.eff = (self.n_out, self.n_in)
        self.dir, self.at, self.strength = self.kw.get('dir'), None, 1.0          # (TR.window / repr compatibility)
        self._v = {}

    def __repr__(self):
        return f'SR.Tx({self.frame}, {self.kind!r}, n_out={self.eff[0]}, n_in={self.eff[1]})'

    def span(self):
        return self.frame - self.eff[0], self.frame + self.eff[1]

    def center(self, F=None):
        c = self.c(F) if callable(self.c) else self.c
        return float(c[0]), float(c[1])


def tx(F, kind, n_out=None, n_in=None, **kw):
    """one designed cut at film frame F (= the next shot's first frame).
    SR kinds (both shots on screen in the window; c=(x, y) = the design's centre, seed):
      circle   (4 / 9)  HUD lock-on ring closes on c in the out shot, then the next shot opens in a circle from c (area
                        at a constant-ish rate, ease-out), ICE edge line + tick ring + big arc riding the edge. r0 (lock
                        radius, 150), push (.10), spin (deg / f), col, label=['NAME', 'VALUE'], alpha
      iris     (9 / 4)  the out shot shrinks into a closing lens on c (ring on its edge), the next shot already around it;
                        the lens closes to a point after the cut (IGNITION 光圈). r0, push, spin, col
      arcburn  (3 / 10) an arc ignites on c, then a ragged hole burns open with a DISCHARGING edge (white core, ICE,
                        ELECTRIC bloom, flicker along the edge every frame, an ionised dark band outside, small arcs on the
                        edge). k (edge light 1.0), arcs (4), dark (.7)
      stripes  (7 / 7)  slanted bands slide in staggered along the slant, motion-blurred, a crisp ICE light on each
                        band head. ang (deg, -62), n (6), alt (False; True = alternate ends), dur (.5), spread (.5),
                        order ('random'), slide (.25: how far the band's picture travels, x the frame), shutter (1.0), k
      blocks   (5 / 6)  拖影块: rows of uneven blocks dragged off along dir, the next shot's blocks dragged in from the
                        other side, each block on its own time around the cut, heavy smear. dir ('L'; the way the blocks
                        travel on screen), amt (1), jit (switch spread, frames), trail (smear, frames)
      kaleido  (14 / 8) KV.Vortex: the out shot folds into a kaleidoscope and spins up, the next shot opens out of its eye
                        at the cut (portal). nseg 6, nseg_end 12, push 1.7, spin 1250, src_c (sample the brightest
                        texture), wedge, level, end, palette (1 = only the motif hue keeps colour; 0 for warm scenes),
                        rim / rim2 / halo_ramp / pal_hue (= **MO.vortex_cols(P); default: the episode's MO.palette)
      hit      (ch / n+af of the preset) 冲击盖切点: SR.hit(F, preset, c, k, ...) -- the charge on the out shot, impact +
                        aftermath on the next. preset ('post3_blue'), k, any SR.hit option
    TR kinds (TR.Cut, camera moves across the cut; at / at_in / dir / strength as TR): whip push pull smear spin ramp
      occlude boltwipe shape light -- here with cap=0 (no brightness cap: fully visible), whip x1.3, push zoom 2.4
      (IGNITION 推穿), pull / smear / ramp / shape x1.2-1.3."""
    if kind in TR.KINDS:
        d = dict(TR_KW.get(kind, {}))
        d.update(kw)
        d.setdefault('cap', 0)
        c = d.pop('c', None)
        if c is not None and kind in ('push', 'pull', 'shape', 'spin'):
            d.setdefault('at', c)
        no, ni = TR_N[kind]
        return TR.Cut(F, kind, no if n_out is None else n_out, ni if n_in is None else n_in, **d)
    return Tx(F, kind, n_out, n_in, **kw)


def window(txs, F):
    """(tx, side, u) when frame F is inside a cut's window, else None -- works on mixed SR.tx / TR.Cut lists
    (overlapping windows are trimmed, tx.eff)"""
    return TR.window(txs, F)


def needs_both(tx):
    """True when the window shows both shots at once (all SR kinds except 'hit'; TR boltwipe / occlude / light)"""
    if isinstance(tx, Tx):
        return tx.kind != 'hit'
    return TR.needs_both(tx)


def tx_frame(E, T, txs, render_at):
    """the whole cut logic in one line: render_at(X) -> the finished frame of the shot that owns film frame X, rendered at
    time T (both shots are asked for inside two-sided windows). Outside every window = render_at(T.F)."""
    w = window(txs, T.F)
    if w is None:
        return render_at(T.F)
    t, side, u = w
    both = needs_both(t)
    a = render_at(t.frame - 1) if (both or side == 'out') else None
    b = render_at(t.frame) if (both or side == 'in') else None
    return tx_mix(E, a, b, t, T.F, T.f)


def tx_mix(E, img_out, img_in, t, F, f=None):
    """inside a cut's window: img_out = the shot before the cut, img_in = the shot after it, both rendered at this time
    (one of them may be None when needs_both(t) is False)"""
    f = float(F if f is None else f)
    if isinstance(t, TR.Cut):
        if TR.needs_both(t):
            return TR.mix(E, img_out, img_in, t, F, f)
        side, u = TR.side_of(t, f)
        if side is None:
            return img_out if f < t.frame - 0.5 else img_in
        img = img_out if side == 'out' else img_in
        return TR.transform(E, img if img is not None else (img_in if side == 'out' else img_out), t, side, u)
    tau = f - (t.frame - 0.5)
    no, ni = t.eff
    if tau < -no or tau >= ni:
        return img_out if tau < 0 else img_in
    return _TXF[t.kind](E, img_out, img_in, t, tau, F, f)


# ---------------------------------------------------------------- circle / iris
CIRCX_FS = '''uniform sampler2D A; uniform sampler2D B; uniform vec2 c; uniform float r; uniform float inv; uniform vec3 lc;
uniform float lw; uniform float la; uniform float sh; uniform float za; uniform float zb; uniform float gl;
vec3 tx(sampler2D t, vec2 p, float z){ return texture(t, clamp(c + (p - c) / z, vec2(0.5), u_size - 0.5) / u_size).rgb; }
void main(){ vec2 p = gl_FragCoord.xy; float d = length(p - c); float x = d - r;
  float m = clamp(0.5 - x, 0.0, 1.0);
  vec3 ins = inv > 0.5 ? tx(A, p, za) : tx(B, p, zb);
  vec3 outs = inv > 0.5 ? tx(B, p, zb) : tx(A, p, za);
  outs *= 1.0 - sh * exp(-max(x, 0.0) / (44.0 * u_s)) * step(0.0, x);        // the edge lifts off the covered picture
  vec3 col = lin(mix(outs, ins, m));
  float e = la * clamp(lw * 0.5 - abs(x) + 0.5, 0.0, 1.0);
  float g = gl * exp(-x * x / (110.0 * u_s * u_s));
  col = col * (1.0 - e) + lin(lc) * (1.6 * e + g);
  o = vec4(enc(col), 1.0); }'''


def _edge_hud(cv, cx, cy, R, tau, col, alpha, spin, p=1.0, arc=True):
    """the HUD riding a circular edge: tick ring just outside (ticks pointing at the edge) + a thin big arc, turning"""
    if alpha <= 0.01 or R <= 1.0:
        return
    n = int(min(240, max(60, TAU * R / 15.0)))
    D.tick_ring(cv, cx, cy, R + 24.0, p=p, ticks=n, major=max(1, n // 12), color=col, alpha=alpha, width=1.4, tick=9.0,
                a0=-90.0 + spin * tau)
    if arc:
        D.big_arc(cv, cx, cy, R * 1.08 + 52.0, 120.0 - 1.7 * spin * tau, 150.0, p=p, color=col, alpha=0.6 * alpha, width=1.2)


def _tx_circle(E, a, b, t, tau, F, f):
    no, ni = t.eff
    cx, cy = t.center(F)
    kw = t.kw
    r0, push, spin = float(kw.get('r0', 150.0)), float(kw.get('push', 0.10)), float(kw.get('spin', 2.2))
    from src.fx import motif as MO                                # p2 11-05 review: the edge line is the signature colour at
    Pm = MO.current()                                             # 1.4 px (was 3 px ICE + glow + crosshair + a lock-on ring
    col = tuple(kw.get('col', Pm['pale'] if Pm else COL['ice']))  # closing ON the subject = a HUD circling the subject)
    al = float(kw.get('alpha', 0.85))
    lab = kw.get('label')
    cv = E.canvas()
    if tau < 0:                                                   # before the cut: a slight push into the subject, no ring on it
        u = clamp01((tau + no) / no)
        R = r0 * (1.0 + 0.7 * (1.0 - eo3(u)))
        img = E.affine(a, 1.0 + 0.04 * ei3(u), 0.0, (cx, cy))
        if lab:
            callout(cv, cx, cy, R, -38.0, lab, f, t.frame - no + 1.0, col=col, alpha=al)
            return E.over(img, cv.texture())
        return img
    v = clamp01(tau / ni)
    a0 = _area_of((cx, cy), r0)
    R = max(r0, area_r((cx, cy), a0 + (1.0 - a0) * (1.0 - (1.0 - v) ** 2.2)))
    fade = clamp01((_far(cx, cy) - R) / 260.0)
    img = run(E, CIRCX_FS, {'A': a, 'B': b}, {'c': (cx * E.s, cy * E.s), 'r': R * E.s, 'inv': 0.0, 'lc': col, 'lw': 1.4 * E.s,
                                             'la': 0.85 * fade, 'sh': 0.5 * fade, 'za': 1.04 + 0.5 * push * eo3(v),
                                             'zb': 1.0 + push * (1.0 - eo3(v)), 'gl': 0.08 * fade})
    if R > 1.6 * r0:                                              # the tick ring only once the circle has opened past the subject
        _edge_hud(cv, cx, cy, R, tau, col, 0.6 * al * fade * clamp01((R - 1.6 * r0) / (0.6 * r0)), spin)
    if lab:
        callout(cv, cx, cy, R + 24.0, -38.0, lab, f, t.frame - no + 1.0, col=col, alpha=al * fade)
    return E.over(img, cv.texture())


def _tx_iris(E, a, b, t, tau, F, f):
    no, ni = t.eff
    cx, cy = t.center(F)
    kw = t.kw
    r0, push, spin = float(kw.get('r0', 120.0)), float(kw.get('push', 0.10)), float(kw.get('spin', 2.2))
    col, al = tuple(kw.get('col', COL['ice'])), float(kw.get('alpha', 0.85))
    Rf = _far(cx, cy)
    if tau < 0:                                                   # the out shot shrinks into a closing lens
        u = clamp01((tau + no) / no)
        a0 = _area_of((cx, cy), r0)
        R = max(r0, area_r((cx, cy), a0 + (1.0 - a0) * (1.0 - u) ** 1.5))
        za = 1.0 - 0.22 * (1.0 - R / Rf)
        zb = 1.0 + push * (1.0 - 0.5 * eo3(u))
        ha, hc = al * clamp01((Rf - R) / 260.0), eo3(clamp01((u - 0.45) / 0.55))
    else:                                                         # ... and closes to a point on the cut
        v = clamp01(tau / ni)
        R = r0 * (1.0 - eo3(v))
        za = (1.0 - 0.22 * (1.0 - r0 / Rf)) * (1.0 - 0.5 * eo3(v))
        zb = 1.0 + 0.5 * push * (1.0 - eo3(v))
        ha, hc = al * (1.0 - eo3(v)), 1.0 - eo3(v)
    img = run(E, CIRCX_FS, {'A': a, 'B': b}, {'c': (cx * E.s, cy * E.s), 'r': R * E.s, 'inv': 1.0, 'lc': col, 'lw': 3.0 * E.s,
                                             'la': 0.95 * min(1.0, ha / max(al, 1e-3)), 'sh': 0.45, 'za': za, 'zb': zb,
                                             'gl': 0.22})
    cv = E.canvas()
    _edge_hud(cv, cx, cy, R, tau, col, ha, spin)
    D.crosshair(cv, cx, cy, 30.0, col, min(1.0, 1.1 * al) * hc, 1.4)
    return E.over(img, cv.texture())


# ---------------------------------------------------------------- arcburn: the edge is a discharge
_RAG = {}


def _rag(seed):
    """a ragged closed edge: 8 static harmonics (IGNITION BURN_FS raggedness .20 / .08) + 4 small ones turning with the frame
    (the edge boils). Python and GLSL evaluate the same sums -> the small arcs sit exactly on the edge."""
    if seed not in _RAG:
        g = np.random.default_rng(seed + 4049)
        mm = np.array([2, 3, 4, 6, 9, 13, 17, 23], np.float64)
        am = np.array([0.075, 0.055, 0.042, 0.030, 0.020, 0.013, 0.009, 0.006]) * g.uniform(0.7, 1.15, 8)
        ph = g.uniform(0.0, TAU, 8)
        bmm, bm = np.array([29.0, 37.0, 47.0, 61.0]), np.array([0.007, 0.006, 0.004, 0.003])
        bph, bw = g.uniform(0.0, TAU, 4), g.uniform(0.35, 0.7, 4) * np.array([1.0, -1.0, 1.0, -1.0])
        an = np.linspace(-math.pi, math.pi, 1440, endpoint=False)
        gs = (am[:, None] * np.sin(mm[:, None] * an + ph[:, None])).sum(0)
        _RAG[seed] = dict(mm=mm, am=am, ph=ph, bmm=bmm, bm=bm, bph=bph, bw=bw, gmin=float(gs.min() - bm.sum()))
    return _RAG[seed]


def _rag_r(R, an, f, g):
    s = (g['am'] * np.sin(g['mm'] * an + g['ph'])).sum() + (g['bm'] * np.sin(g['bmm'] * an + g['bph'] + g['bw'] * f)).sum()
    return R * (1.0 + float(s))


ARCB_GLSL = '''uniform vec2 c; uniform float r; uniform float fr; uniform float seed; uniform float am[8]; uniform float mm[8];
uniform float ph[8]; uniform float bm[4]; uniform float bmm[4]; uniform float bph[4];
float h1(float n){ return fract(sin(n * 12.9898 + seed) * 43758.5453); }
float edgex(vec2 p, out float an){ vec2 d = p - c; an = atan(d.y, d.x); float g = 0.0;
  for (int i = 0; i < 8; i++) g += am[i] * sin(mm[i] * an + ph[i]);
  for (int i = 0; i < 4; i++) g += bm[i] * sin(bmm[i] * an + bph[i]);
  return r * (1.0 + g) - length(d); }
float flick(float an){ float t = (an + 3.14159265) / 6.2831853 * 40.0; float i0 = floor(t); float x = fract(t);
  float a0 = h1(mod(i0, 40.0) + fr * 41.0); float a1 = h1(mod(i0 + 1.0, 40.0) + fr * 41.0);
  float k = mix(a0, a1, x * x * (3.0 - 2.0 * x)); return 0.15 + 0.6 * k * k + 1.6 * pow(k, 8.0); }
'''
ARCB_FS = 'uniform sampler2D A; uniform sampler2D B; uniform vec3 core; uniform vec3 ice; uniform float ek; uniform float dk;\n' \
    + ARCB_GLSL + '''
void main(){ vec2 p = gl_FragCoord.xy / u_s; float an; float x = edgex(p, an);
  float m = clamp(x * u_s + 0.5, 0.0, 1.0);
  vec3 a = texture(A, UV()).rgb; vec3 b = texture(B, UV()).rgb;
  a *= 1.0 - dk * (1.0 - smoothstep(0.0, 70.0, -x)) * step(x, 0.0);            // ionised dark band outside the edge
  vec3 col = lin(mix(a, b, m));
  float k = ek * flick(an);
  col += k * (lin(core) * 1.6 * exp(-x * x / 1.1) + lin(ice) * 0.85 * exp(-x * x / 16.0));
  o = vec4(enc(col), 1.0); }'''
ARCL_FS = 'uniform float ek;\n' + ARCB_GLSL + '''
void main(){ vec2 p = gl_FragCoord.xy / u_s; float an; float x = edgex(p, an);
  o = vec4(ek * flick(an) * (exp(-x * x / 5.0) + 0.3 * exp(-x * x / 50.0)), 0.0, 0.0, 1.0); }'''
ARC_TINTS = ('ARC_WHITE', 'ICE', 'ELECTRIC', 'ELECTRIC', 'VIOLET', 'DEEP_VIOLET')


def _tx_arcburn(E, a, b, t, tau, F, f):
    no, ni = t.eff
    cx, cy = t.center(F)
    kw = t.kw
    ek, narcs, dk = float(kw.get('k', 1.0)), int(kw.get('arcs', 4)), float(kw.get('dark', 0.7))
    if tau < 0:                                                   # ignition: a small arc flickers where the burn starts
        u = clamp01((tau + no) / no)
        img = E.affine(a, 1.0 + 0.03 * ei3(u), 0.0, (cx, cy))
        return IM._arc_geo(t.seed % 4).draw(E, img, tau + 1.2, k=1.2 * ek, scale=0.50, offset=(cx, cy),
                                            rot=rr(0.0, TAU, t.seed, 'ig'))
    g = _rag(t.seed)
    v = clamp01(tau / ni)
    R = area_r((cx, cy), 1.0 - (1.0 - v) ** 1.7) / (1.0 + g['gmin'] - 0.01)
    fade = 1.0 - eo3(clamp01((R * (1.0 + g['gmin']) - _far(cx, cy) + 160.0) / 160.0))
    uni = {'c': (cx, cy), 'r': R, 'fr': float(F), 'seed': float(t.seed % 97), 'ek': ek * fade}
    arr = dict(am=g['am'], mm=g['mm'], ph=g['ph'], bm=g['bm'], bmm=g['bmm'], bph=g['bph'] + g['bw'] * f)
    img = run(E, ARCB_FS, {'A': a, 'B': b}, dict(uni, core=COL['white'], ice=COL['ice'], dk=dk * fade), arrays=arr)
    if fade > 0.01:
        lt = run(E, ARCL_FS, None, uni, arrays=arr)
        img = VT.bloom(E, img, lt, amount=1.25 * ek * fade, s0=2.0, r=2.4, tints=ARC_TINTS, rch=True)
    for q in range(narcs):                                        # small arcs fire on the edge
        lt_ = tau - rr(0.0, 0.55 * ni, t.seed, q, 't0')
        if -1.3 < lt_ < 4.0 and fade > 0.05:
            an = rr(-math.pi, math.pi, t.seed, q, 'an')
            Rq = _rag_r(R, an, f, g)
            img = IM._arc_geo(q % 4).draw(E, img, lt_, k=1.1 * ek * fade, scale=rr(0.42, 0.68, t.seed, q, 's'),
                                          offset=(cx + Rq * math.cos(an), cy + Rq * math.sin(an)), rot=an)
    return img


# ---------------------------------------------------------------- stripes: slanted bands slide in
STRIPES_FS = '''uniform sampler2D A; uniform sampler2D B; uniform vec2 dv; uniform float vmin; uniform float bw; uniform float st[24];
uniform float dur; uniform float Pa; uniform float Pb; uniform float smin; uniform float smax; uniform float Lc; uniform float alt;
uniform vec3 lc; uniform float la; uniform int nt; uniform int nb; uniform float seed;
float ign(vec2 p){ return fract(52.9829189 * fract(dot(p, vec2(0.06711056, 0.00583715)))); }
float eo(float x){ x = clamp(x, 0.0, 1.0); return 1.0 - (1.0 - x) * (1.0 - x) * (1.0 - x); }
float head(float e, float sg){ float L = smax - smin + 60.0; return sg > 0.0 ? smin - 30.0 + e * L : smax + 30.0 - e * L; }
void main(){ vec2 px = gl_FragCoord.xy; vec2 p = px / u_s; vec2 nv = vec2(-dv.y, dv.x);
  float s = dot(p, dv); float v = dot(p, nv);
  float k = clamp(floor((v - vmin) / bw), 0.0, float(nb - 1)); int ki = int(k);
  float sg = (alt > 0.5 && mod(k, 2.0) > 0.5) ? -1.0 : 1.0;
  float j = fract(ign(px) + seed);
  vec3 ca = lin(texture(A, UV()).rgb); vec3 acc = vec3(0.0); float edge = 0.0;
  for (int i = 0; i < 16; i++){ if (i >= nt) break;
    float P = mix(Pa, Pb, (float(i) + j) / float(nt));
    float e = eo((P - st[ki]) / dur);
    float dl = sg * (head(e, sg) - s);                      // >= 0 behind the band's head (the band covers it)
    if (dl >= 0.0){                                          // the band's picture slides in (Lc) and settles
      vec2 q = (p + sg * (1.0 - e) * Lc * dv) * u_s;
      acc += lin(texture(B, clamp(q, vec2(0.5), u_size - 0.5) / u_size).rgb);
      edge += 0.45 * exp(-dl / 26.0) * (1.0 - e);            // the head's streak
    } else acc += ca;
  }
  acc /= float(nt); edge /= float(nt);
  float e1 = eo((Pb - st[ki]) / dur); float d1 = sg * (head(e1, sg) - s);   // the head itself, crisp, where it is now
  if (d1 >= 0.0) edge += (exp(-d1 / 1.5) + 0.3 * exp(-d1 / 12.0)) * (1.0 - e1);
  o = vec4(enc(acc + lin(lc) * la * edge), 1.0); }'''


def _tx_stripes(E, a, b, t, tau, F, f):
    no, ni = t.eff
    kw = t.kw
    ang = math.radians(float(kw.get('ang', -62.0)))
    n = int(min(24, max(2, kw.get('n', 6))))
    dur, spread, sh = float(kw.get('dur', 0.5)), float(kw.get('spread', 0.5)), float(kw.get('shutter', 1.0))
    dv = (math.cos(ang), math.sin(ang))
    nv = (-dv[1], dv[0])
    cs = ((0.0, 0.0), (W, 0.0), (0.0, H), (W, H))
    ss, vs = [x * dv[0] + y * dv[1] for x, y in cs], [x * nv[0] + y * nv[1] for x, y in cs]
    order = list(range(n))
    if kw.get('order') == 'random':
        order.sort(key=lambda k: rr(0.0, 1.0, t.seed, k, 'ord'))
    st = [0.0] * 24
    for rank, k in enumerate(order):
        st[k] = float(np.clip(spread * rank / max(n - 1, 1) + rr(-0.05, 0.05, t.seed, k, 'st'), 0.0, 1.0 - dur))
    N = float(no + ni)
    return run(E, STRIPES_FS, {'A': a, 'B': b}, {
        'dv': dv, 'vmin': min(vs), 'bw': (max(vs) - min(vs)) / n, 'dur': dur, 'Pa': (tau - 0.5 * sh + no) / N,
        'Pb': (tau + 0.5 * sh + no) / N, 'smin': min(ss), 'smax': max(ss),
        'Lc': float(kw.get('slide', 0.25)) * (max(ss) - min(ss)),
        'alt': 1.0 if kw.get('alt', False) else 0.0, 'lc': tuple(kw.get('col', COL['ice'])), 'la': 1.6 * float(kw.get('k', 1.0)),
        'nt': 14, 'nb': n, 'seed': (t.seed * 0.618) % 1.0}, arrays={'st': np.array(st, np.float32)})


# ---------------------------------------------------------------- blocks: 拖影块
BLOCKS_FS = '''uniform sampler2D A; uniform sampler2D B; uniform vec2 dv; uniform float seed; uniform float ta; uniform float tb;
uniform float no; uniform float ni; uniform float L; uniform int nt; uniform float jit;
float h1(float n){ return fract(sin(n * 127.1 + seed * 311.7) * 43758.5453); }
float ign(vec2 p){ return fract(52.9829189 * fract(dot(p, vec2(0.06711056, 0.00583715)))); }
void main(){ vec2 px = gl_FragCoord.xy; vec2 p = px / u_s; vec2 nv = vec2(-dv.y, dv.x);
  float s = dot(p, dv) + 4000.0; float v = dot(p, nv) + 4000.0;
  float cell = floor(v / 120.0); float fy = v - cell * 120.0;                     // 120 px cells -> 1-3 uneven rows
  float c1 = 120.0 * (0.18 + 0.64 * h1(cell * 3.7 + 1.0)); float c2 = c1 + (120.0 - c1) * h1(cell * 5.3 + 2.0);
  float row = cell * 3.0 + (fy < c1 ? 0.0 : (fy < c2 ? 1.0 : 2.0));
  float bw = 90.0 + 520.0 * h1(row * 1.31 + 7.0);                                // blocks along the row: 90-610 px
  float blk = floor((s + h1(row * 5.11 + 2.0) * bw) / bw);
  float id = row * 37.0 + blk * 1.73;
  float ts = (h1(id + 0.37) * 2.0 - 1.0) * jit;                                  // this block's own switch time
  float sp = 0.30 + 1.10 * h1(id + 0.91);
  float j = ign(px);
  vec3 acc = vec3(0.0);
  for (int i = 0; i < 24; i++){ if (i >= nt) break;
    float t = mix(ta, tb, (float(i) + j) / float(nt));
    vec3 c;
    if (t < ts){ float x = clamp((t + no) / (ts + no), 0.0, 1.0); float off = sp * L * x * x * x;
      c = texture(A, clamp((p - off * dv) * u_s, vec2(0.5), u_size - 0.5) / u_size).rgb; }
    else { float y = 1.0 - clamp((t - ts) / (ni - ts), 0.0, 1.0); float off = -sp * L * y * y * y;
      c = texture(B, clamp((p - off * dv) * u_s, vec2(0.5), u_size - 0.5) / u_size).rgb; }
    acc += lin(c); }
  o = vec4(enc(acc / float(nt)), 1.0); }'''


def _tx_blocks(E, a, b, t, tau, F, f):
    no, ni = t.eff
    kw = t.kw
    d = TR._dvec(kw.get('dir', 'L'))
    jit = min(float(kw.get('jit', 1.6)), 0.8 * min(no, ni))
    tr = float(kw.get('trail', 1.6))
    return run(E, BLOCKS_FS, {'A': a, 'B': b}, {'dv': (float(d[0]), float(d[1])), 'seed': float(t.seed % 89) * 0.37,
                                                'ta': tau - 0.5 * tr, 'tb': tau + 0.5 * tr, 'no': float(no), 'ni': float(ni),
                                                'L': 0.5 * W * float(kw.get('amt', 1.0)), 'nt': 20, 'jit': jit})


# ---------------------------------------------------------------- kaleido / hit
def _tx_kaleido(E, a, b, t, tau, F, f):
    no, ni = t.eff
    V = t._v.get(t.eff)
    if V is None:
        kw = t.kw
        cx, cy = t.center(F)
        N = float(no + ni)
        V = t._v[t.eff] = KV.Vortex(t.frame - no - 0.5, t.frame + ni - 0.5, end=kw.get('end', 'portal'), nseg=kw.get('nseg', 6),
                                    nseg_end=kw.get('nseg_end', 12), push=kw.get('push', 1.7), spin=kw.get('spin', 1250.0),
                                    wedge=kw.get('wedge', -90.0), eye=(cx, cy), src_c=kw.get('src_c', (cx, cy)),
                                    fold_end=0.32 * no / N, spin_end=no / N, level=kw.get('level', 0.22),
                                    palette=kw.get('palette', 1.0), grade=kw.get('grade'), rim=kw.get('rim'),
                                    rim2=kw.get('rim2'), halo_ramp=kw.get('halo_ramp'), pal_hue=kw.get('pal_hue'))
    #                                 ^ p2 11-05: the light edge / portal halo in the signature colour (rim / rim2 / halo_ramp /
    #                                   pal_hue = MO.vortex_cols(P); not given = the current MO.palette), never VOLT violet
    return V.frame(E, _TT(F, f), a, b if b is not None else a)


def _tx_hit(E, a, b, t, tau, F, f):
    img = a if (tau < 0 and a is not None) or b is None else b
    return post(E, _TT(F, f), img, [t.hit])


_TXF = dict(circle=_tx_circle, iris=_tx_iris, arcburn=_tx_arcburn, stripes=_tx_stripes, blocks=_tx_blocks, kaleido=_tx_kaleido,
            hit=_tx_hit)


# ================================================================== VIRTUAL CAMERA (SR.AutoCam)
# A shot that does not move reads as a still photo. The kit puts the motion in a base move (pan / truck /
# orbit ...), per-beat re-framing (IGNITION drift), kicks on the beat / 16th grid and accent punches + shake, never showing
# the plate edge. p2 11-04: drift / kick / six are off by default (re-framing and pumps go back and forth = no net motion);
# the one-way base move is what counts, and amt = 1 is slow (textured plate: about 1.2-1.6 effective motion; amt 4 about
# 5-10). Give the speed directly instead: px_per_f (screen px / frame along ang) and zoom_pct_per_f (% / frame, + = push).
# Measured on a textured plate (p2 11-04): 8 px / frame sideways = effective motion ~6.9; 1 % / frame push ~5.4.
CAM_MOVES = {
    'push': dict(z=0.14, t=30.0), 'pull': dict(z=-0.14, t=30.0), 'pan': dict(t=170.0, z=0.02),
    'truck': dict(t=130.0, ry=3.5, z=0.04), 'crane': dict(t=110.0, rx=3.0, z=0.03),
    'orbit': dict(r=5.0, ry=2.5, z=0.06, t=60.0), 'spiral': dict(z=0.16, r=6.0, t=50.0), 'drift': dict()}
CAM_ANG = dict(pan=180.0, truck=180.0, crane=90.0, orbit=180.0, push=180.0, pull=0.0, spiral=200.0)   # the way the PICTURE moves (deg)
_EV = {}


# ================================================================== 卡点（整数帧，读实测音乐 music_map）
# 编排器把这首歌的 music_map.json 拷进 <engine>/audio/（片内帧号，第 0 帧 = 窗口起点）：每个 16 分音符格一条，带画面该变的
# 整数帧 F（= round(真实起音)；起音比格点早约 11 ms，所以 TL.F 的 round(格点) 有两成会晚 1 帧）、档位 tier（S = 有起音的格
# 里前 5 %，A = 前 25 %，B = 有起音，C = 没有起音）、强度 total；另有 drop、stops（急停）。镜头里的卡点一律用表里的 F，
# 写成整数常量，用 T.F 比较：切换 T.F >= F 或 SR.seg，包络 SR.env，都按整数帧算（1352.9 按 1352）。
_MM = {}
_TIERS = ('S', 'A', 'B', 'C')


def music_map():
    """<engine>/audio/music_map.json 的内容（没有 = None）"""
    if 'mm' not in _MM:
        p = os.path.join(ROOT, 'audio', 'music_map.json')
        _MM['mm'] = json.load(open(p)) if os.path.exists(p) else None
    return _MM['mm']


def _mm_rows():
    """music_map 的格子 [dict(F, tier, k, accent, kind)]，F 升序；k = 音乐强度 0..1（窗口里最强一格 = 1；drop、急停 = 1、算 S）"""
    if 'rows' not in _MM:
        mm = music_map() or {}
        sl = mm.get('slots') or mm.get('events') or []              # events = 11-04 原型的写法
        top = max([float(e.get('total', 0.0)) for e in sl] + [1e-6])
        rows = {}
        for e in sl:
            tier = str(e.get('tier', 'C'))
            row = dict(F=int(e['F']), tier=tier if tier in _TIERS else 'C', k=clamp01(float(e.get('total', 0.0)) / top),
                       accent=bool(e.get('accent', tier in ('S', 'A'))), kind=e.get('kind', 'onset'))
            old = rows.get(row['F'])
            if old is None or _TIERS.index(row['tier']) < _TIERS.index(old['tier']):
                rows[row['F']] = row
        big = [dict(e, kind='drop') for e in [mm.get('drop')] if isinstance(e, dict) and 'F' in e]
        big += [dict(e, kind='stop') for e in mm.get('stops') or [] if 'F' in e]
        big += [e for e in mm.get('extra') or [] if 'F' in e]
        for e in big:
            r = rows.setdefault(int(e['F']), dict(F=int(e['F']), tier='S', k=1.0, accent=True, kind=e.get('kind', 'stop')))
            r.update(tier='S', k=1.0, accent=True, kind=e.get('kind', r['kind']))
        _MM['rows'] = sorted(rows.values(), key=lambda r: r['F'])
    return _MM['rows']


def slots(f0=-1e9, f1=1e9, tiers=('S', 'A')):
    """[f0, f1) 里的卡点 [(F, 档位, k)]，F 升序。F = 画面第一下强变化该落的整数帧（已按真实起音算好，直接写成常量）；
    档位 S / A / B / C（见上）；k = 音乐强度 0..1，拿来定这一下的大小（重的大、轻的小）。drop、急停算 S、k = 1。
    没有 music_map 时返回 []。  例：HITS = [SR.hit(F, 'post3_blue', k=0.6 + 0.6 * k) for F, _, k in SR.slots(f0, f1, ('S',))]"""
    return [(r['F'], r['tier'], r['k']) for r in _mm_rows() if f0 <= r['F'] < f1 and r['tier'] in tuple(tiers)]


def accent_frames(f0=-1e9, f1=1e9):
    """[f0, f1) 里重音 16 分音符的整数帧：有 audio/music_map.json 时读它的 F（accent 格 + drop、急停，按真实起音），
    没有时退回旧的 audio/events.json（round(格点)）；都没有 = []"""
    if 'acc' not in _EV:
        if music_map() is not None:
            _EV['acc'] = sorted({r['F'] for r in _mm_rows() if r['accent']})
        else:
            p = os.path.join(ROOT, 'audio', 'events.json')
            ev = json.load(open(p))['events'] if os.path.exists(p) else []
            _EV['acc'] = sorted({int(math.floor(e['frame'] + 0.5)) for e in ev if e.get('accent')})
    return [F for F in _EV['acc'] if f0 <= F < f1]


def seg(F, cuts):
    """第几个构图：cuts = 本镜的切点（整数帧，「本镜卡点」表里的 F）。构图 i 从 cuts[i-1] 那一帧起（含）、当帧就换：
        i = SR.seg(T.F, CUTS); img = PLATES[i](T)
    整数比较（传 1352.9 按 1352），切换永远落在表里那一帧，不会被小数帧推到下一帧。"""
    return bisect.bisect_right(sorted(int(round(c)) for c in cuts), int(math.floor(F)))


def since(F, F0):
    """F0 之后第几帧（整数，F0 当帧 = 0；F0 之前 = -1）。传 T.F；小数按所在整数帧算（1352.9 按 1352）。"""
    d = int(math.floor(F)) - int(round(F0))
    return d if d >= 0 else -1


def env(F, F0, hold=0, decay=6.0):
    """打击包络 0..1（整数帧）：F0 之前 0；F0 .. F0 + hold 是 1（峰值就在 F0 这一帧，不会被缓动推到 F0 + 1）；之后按
    exp(-(t - hold) / decay) 衰减。  用法：新构图从 F0 起（i = SR.seg(T.F, CUTS)），冲击帧盖在新构图上：
    flash = SR.env(T.F, 1352, decay=4.0)；多个卡点取 max(SR.env(T.F, F) for F in FS)。不要拿它做「放大一点再缩回来」的缩放泵：
    那是原地来回，编排器量有效运动量时按 0 算（推进用只增不减的量，比如 SR.since(T.F, F0) 的单调函数）。"""
    t = since(F, F0)
    if t < 0:
        return 0.0
    if t <= hold:
        return 1.0
    return math.exp(-(t - hold) / float(decay))


def _grid_last(f, step):
    """(k, Fk): the last grid point k (OFFSET + k * step, landing on its integer frame Fk) with Fk - 0.5 <= f"""
    k = math.floor((f + 0.5 - TL.OFFSET) / step) + 1
    while math.floor(TL.OFFSET + k * step + 0.5) - 0.5 > f:
        k -= 1
    return k, math.floor(TL.OFFSET + k * step + 0.5)


_CORNERS = np.array([[0.0, 0.0, 1.0], [W, 0.0, 1.0], [0.0, H, 1.0], [W, H, 1.0]])


def _need(p, pw, ph, K=None):
    """zoom factor (>= 1) that keeps the whole screen inside a pw x ph plate (plate px in 1080p units) for pose dict p
    (seen through the screen-space kick K, if given)"""
    m = 1.0
    px, py = p['piv']
    cs = _CORNERS if K is None else _CORNERS @ np.linalg.inv(K).T
    for _ in range(12):
        Hi = np.linalg.inv(fx.pose_H(**dict(p, zoom=p['zoom'] * m)))
        r = 1.0
        for c in cs:
            v = Hi @ c
            X, Y = v[0] / v[2], v[1] / v[2]
            if X < 2.0:
                r = max(r, (px - X) / max(px - 2.0, 1.0))
            if X > pw - 2.0:
                r = max(r, (X - px) / max(pw - 2.0 - px, 1.0))
            if Y < 2.0:
                r = max(r, (py - Y) / max(py - 2.0, 1.0))
            if Y > ph - 2.0:
                r = max(r, (Y - py) / max(ph - 2.0 - py, 1.0))
        if r <= 1.0005:
            break
        m *= r * 1.0005
    return m


class AutoCam:
    """自动运镜 for one shot [f0, f1) (film frames). The camera looks at plate point `eye` (1080p plate units, any tier)
    pinned to screen `at` (default = eye), then:
             or a dict of per-second rates {z, t, r, rx, ry}; amt scales it; ang = the way the picture travels (deg,
             0 = right, 90 = down). The move is centred on the shot's middle (needs half the zoom margin).
      drift  per-beat re-framing between IGNITION's J_FREE targets x drift (0.2: ~ +-35 px, +-1 deg, 0-11 % zoom a beat)
             p2 11-04 默认 0（每拍重构图是原地来回）
      kick   kick on every beat of the grid: zoom +5 % x kick, roll +-1.4 deg x roll_kick (alternating by the
             GLOBAL beat index), decay 3.5 f; six: +1.4 % on every 16th (decay 2.2 f); all x k. The rhythm (kicks, accents,
             rumble) acts in SCREEN space about the frame centre (kick_H, kick @ pose): the subject moves too
             p2 11-04 kick / six 默认 0：每拍、每 16 分整帧泵要显式打开，只叠在一路走到底的推进上（不算运动、不算卡点）
      accents 'hits'（默认）= TL.HITS 里 big / drop / final 的帧；'auto' = music_map 的 S / A 档格（每一下 x 音乐强度 k；
             没有 music_map 时退回 events.json 的重音）；或者一个帧表 [F, ...] / [(F, k), ...]；None = 不打。
             每一下 punch (zoom, 6 f) + shake (px, decay 3.2 f) x k;  rumble = launch vibration px（整镜，少用）
      taps   motion-blur taps inside the 180-degree exposure (fx.cam, linear light)
      px_per_f / zoom_pct_per_f  (p2 11-04 第二轮) 直接按每帧给单向速度，代替 amt 缩放的预设：px_per_f = 画面每帧沿 ang
             走多少 px（屏幕像素，负数反向），zoom_pct_per_f = 每帧推近百分之几（负数拉远）。给了就覆盖这个 move 的平移 / 缩放
             amt = 1 的预设只有 1.2-1.6、amt = 4 约 5-10；纯色底、暗底大片没纹理的地方量出来要打折（纯色 ≈ 0）。
             整镜单向走，盖边放大跟着涨：一镜 2 小节 8 px/帧 ≈ 横移 1500 px，要放大约 1.8 倍（长镜头分段换素材更好，见 SR.track）。
    The plate never shows its edge: a constant cover zoom (max over the shot +-16 f, so two-sided transitions that render
    the shot early / late are covered too) x margin. Use:
        CAM = SR.AutoCam(TL.F(10), TL.F(10, 3), move='truck', eye=(1100, 420), px_per_f=8)
        img = VT.volt(E, CAM.render(E, T, E.clip(take, T.F)), 'storm', **lk)
        x, y = CAM.at(T.f, 1100, 420)          # HUD rings follow the subject
    CAM.predict() = 镜头本身每帧带动画面多少 px（32 x 18 网格点每帧位移的中位数，推近也折成位移，1080p px / 帧）；
    素材自己的运动另算，没纹理的地方量出来更低。predict(units='flow') = 旧的 motion_check 单位（px / 7.5）。"""

    def __init__(self, f0, f1, move='truck', eye=(960.0, 540.0), at=None, amt=1.0, ang=None, k=1.0, kick=0.0, six=0.0,
                 roll_kick=1.0, accents='hits', punch=0.045, shake=7.0, drift=0.0, rumble=0.0, zoom=1.0, roll=0.0, taps=4,
                 margin=1.006, seed=0, J=None, plate=None, pre=16.0, px_per_f=None, zoom_pct_per_f=None):
        self.f0, self.f1 = float(f0), float(f1)
        self.fm = 0.5 * (self.f0 + self.f1)
        if isinstance(move, str):
            if move not in CAM_MOVES:
                raise KeyError(f'AutoCam move {move!r}: {sorted(CAM_MOVES)} or a dict of rates')
            mv, a0 = CAM_MOVES[move], CAM_ANG.get(move, 0.0)
        else:
            mv, a0 = dict(move), 0.0
        self.move = move
        self.mv = {key: float(v) * amt for key, v in mv.items()}
        if px_per_f is not None:                                 # explicit per-frame rates (p2 11-04): not scaled by amt
            self.mv['t'] = float(px_per_f) * TL.FPS
        if zoom_pct_per_f is not None:
            zp = float(zoom_pct_per_f)
            if zp <= -100.0:
                raise ValueError(f'AutoCam zoom_pct_per_f {zp}: must be > -100 (% per frame)')
            self.mv['z'] = math.log1p(zp / 100.0) * TL.FPS
        self.px_per_f, self.zoom_pct_per_f = px_per_f, zoom_pct_per_f
        self.ang = math.radians(a0 if ang is None else ang)
        self.eye = (float(eye[0]), float(eye[1]))
        self.at0 = self.eye if at is None else (float(at[0]), float(at[1]))
        self.k, self.kick, self.six, self.rk = float(k), float(kick), float(six), float(roll_kick)
        if self.kick > 0 or self.six > 0:
            _warn_once('autocam_kick', 'AutoCam kick / six > 0 = 每拍 / 每 16 分整帧泵：原地来回不算运动、也不算卡点，'
                                       '只叠在一路走到底的推进上')
        self.acc = self._accents(accents)
        self.punch_a, self.shake_a, self.drift, self.rum = float(punch), float(shake), float(drift), float(rumble)
        self.zoom0, self.roll0, self.taps, self.margin, self.seed = float(zoom), float(roll), int(taps), float(margin), int(seed)
        self.J, self.pre = J or J_FREE, float(pre)
        self._M = {}
        self._asp = None if plate is None else plate[1] / plate[0]

    def _accents(self, accents):
        """accents -> [(F, k)]：这一镜（含前 8 帧的余波、后 2 帧）里要 punch + shake 的整数帧和各自的大小"""
        a, b = self.f0 - 8.0, self.f1 + 2.0
        if accents == 'hits':                                    # 导演的大卡点（timeline.py 的 HITS = [(F, kind), ...]）
            out = []
            for h in getattr(TL, 'HITS', None) or []:
                F, kind = (h['F'], h['kind']) if isinstance(h, dict) else (h[0], h[1])
                if kind in ('big', 'drop', 'final') and a <= F < b:
                    out.append((int(F), 1.0))
            return sorted(set(out))
        if accents == 'auto':                                    # 实测音乐的 S / A 档，大小跟着音乐强度走
            if music_map() is not None:
                return [(F, k) for F, _, k in slots(a, b, ('S', 'A'))]
            return [(F, 1.0) for F in accent_frames(a, b)]
        if isinstance(accents, str):
            raise ValueError(f"AutoCam accents {accents!r}: 'hits' | 'auto' | [F, ...] | None")
        return sorted(_fk(x) for x in (accents or []))

    # ---- the move
    def raw(self, f):
        """pose dict (pose_H keywords, 1080p units) at film frame f, before the cover zoom"""
        mv = self.mv
        ts = (f - self.fm) / TL.FPS
        z = mv.get('z', 0.0) * ts
        tr = mv.get('t', 0.0) * ts
        ca, sa = math.cos(self.ang), math.sin(self.ang)
        dx, dy = tr * ca, tr * sa
        roll = self.roll0 + mv.get('r', 0.0) * ts
        rx = mv.get('rx', 0.0) * ts * (sa if abs(sa) > 1e-6 else 1.0)
        ry = -mv.get('ry', 0.0) * ts * (ca if abs(ca) > 1e-6 else 1.0)
        if self.drift > 0:                                       # per-beat re-framing between J targets (never snaps)
            x = (f - TL.OFFSET) / TL.BEAT
            b = math.floor(x)
            w = 0.5 * (x - b) + 0.5 * eio3(x - b)
            j0, j1 = self.J[(b + self.seed) % len(self.J)], self.J[(b + 1 + self.seed) % len(self.J)]
            dz_, ddx, ddy, dr = (self.drift * (p + (q - p) * w) for p, q in zip(j0, j1))
            z += dz_; dx += ddx; dy += ddy; roll += dr
        zoom = self.zoom0 * math.exp(z)
        return dict(zoom=zoom, roll=roll, rx=rx, ry=ry, piv=self.eye, at=(self.at0[0] + dx, self.at0[1] + dy))

    def kick_H(self, f):
        """the rhythm, in SCREEN space about the frame centre (kick @ pose): beat kick (zoom + alternating
        roll), 16th kick, accent punch + shake, rumble -> 3x3 (left-multiplies the pose)"""
        k, zoom, roll, dx, dy = self.k, 1.0, 0.0, 0.0, 0.0
        if k > 0 and self.kick > 0:
            b, Fb = _grid_last(f, TL.BEAT)
            kb = math.exp(-(f - (Fb - 0.5)) / 3.5)
            zoom *= 1.0 + 0.05 * self.kick * k * kb
            roll += 1.4 * self.rk * k * kb * (1.0 if b % 2 == 0 else -1.0)
        if k > 0 and self.six > 0:
            _, Fs = _grid_last(f, TL.SIXTEENTH)
            zoom *= 1.0 + 0.014 * self.six * k * math.exp(-(f - (Fs - 0.5)) / 2.2)
        for Fa, ka in self.acc:
            if Fa - 0.5 <= f < Fa + 16.0:
                zoom *= 1.0 + k * punch(f, Fa - 0.5, self.punch_a * ka, 6.0)
                sx, sy, sr = fx.shake(f, Fa - 0.5, self.shake_a * k * ka, decay=3.2, seed=Fa % 17)
                dx += sx; dy += sy; roll += math.degrees(sr) * 0.25
        if self.rum > 0:
            rx_, ry_ = rumble(f, self.rum)
            dx += rx_; dy += ry_
        return fx.pose_H(zoom=zoom, roll=roll, piv=(W / 2, H / 2), at=(W / 2 + dx, H / 2 + dy))

    def cover(self, aspect=None):
        """the constant cover zoom for a plate of this aspect (h / w), max over [f0 - pre, f1 + pre] x margin"""
        asp = round(float(aspect or self._asp or H / W), 4)
        M = self._M.get(asp)
        if M is None:
            pw, ph = W, W * asp
            fs = np.arange(self.f0 - self.pre, self.f1 + self.pre, 0.5)
            M = self._M[asp] = max(_need(self.raw(float(x)), pw, ph, self.kick_H(float(x))) for x in fs) * self.margin
        return M

    def pose(self, f, aspect=None):
        """the subject-relative pose dict (base move + drift + cover zoom); the full camera is kick_H(f) @ pose_H(pose)"""
        p = self.raw(f)
        p['zoom'] *= self.cover(aspect)
        return p

    def H(self, f, aspect=None, src=None):
        """3x3 plate (1080p units, or src=(w, h) pixels) -> screen (1080p px)"""
        return self.kick_H(f) @ fx.pose_H(**dict(self.pose(f, aspect), src=src))

    def at(self, f, x, y):
        """screen position (1080p px) of plate point (x, y) (1080p plate units) at film frame f -- HUD follows the subject"""
        return fx.apply_H(self.H(f), x, y)

    def render(self, E, T, plate, f=None, **kw):
        """the plate through this camera with motion blur (fx.cam, `taps` inside the exposure). f: a different time handle
        (anything with .f / .taps(n)), e.g. when the shot clock is not the film clock."""
        self._asp = plate.h / float(plate.w)
        self.cover(self._asp)
        src = (plate.w, plate.h)
        return fx.cam(E, T if f is None else f, plate, lambda ff: self.H(ff, src=src), n=self.taps, edge='clamp', **kw)

    def predict(self, f0=None, f1=None, units='px'):
        """镜头本身每帧带动画面多少 px：32 x 18 网格点每帧屏幕位移的中位数，对 [f0, f1) 的帧取平均（1080p px / 帧；推近、
        滚转也折成位移）。素材自己的运动另算；没纹理的地方（黑天、纯色底）量出来的有效运动量会低得多。
        units='flow' = 旧的 tools/motion_check 单位（同一个数 / 7.5，256 px 宽的分析图）。"""
        if units not in ('px', 'flow'):
            raise ValueError(f"AutoCam.predict units {units!r}: 'px' | 'flow'")
        f0 = self.f0 if f0 is None else f0
        f1 = self.f1 if f1 is None else f1
        gx, gy = np.meshgrid(np.linspace(30, W - 30, 32), np.linspace(30, H - 30, 18))
        P = np.c_[gx.ravel(), gy.ravel(), np.ones(gx.size)]
        out = []
        for F in np.arange(f0 + 1, f1):
            H0, H1 = self.H(F - 1.0), self.H(F)
            q = P @ np.linalg.inv(H0).T
            a = q @ H1.T
            d = np.hypot(a[:, 0] / a[:, 2] - P[:, 0], a[:, 1] / a[:, 2] - P[:, 1])
            out.append(float(np.median(d)))
        px = float(np.mean(out)) if out else 0.0
        return px / 7.5 if units == 'flow' else px


_CAMS = {}


def auto_cam(E, T, plate, f0, f1, move='truck', **kw):
    """one line: the plate through a (cached) AutoCam(f0, f1, move, **kw) at time T (kw 里可以给 px_per_f / zoom_pct_per_f)"""
    key = (f0, f1, repr(move), tuple(sorted((k_, repr(v)) for k_, v in kw.items())))
    cam = _CAMS.get(key)
    if cam is None:
        cam = _CAMS[key] = AutoCam(f0, f1, move, **kw)
    return cam.render(E, T, plate)


# ================================================================== 跟拍 SR.track
# 跟拍 = 主角钉在画面里不动（切点前后不跳），身后有纹理的世界按 px/帧 单向流过，每个计划切点换世界的素材或时段（切点
# 上换的是占画面大头的东西 = 真切），可加一层更快的前景（视差）。实测（默认参数：世界单色、压暗、略虚，主角钉住）：8 px/帧 ≈ 有效运动量
# 3.5–4.7、10 px ≈ 5.0（不压暗不虚的原色纹理世界 8 px ≈ 5.8、4 px ≈ 3.1）；纯色底 ≈ 0。前景实拍雪的量纲见 HANDBOOK §3b（只当陪衬）。世界默认压暗、略虚，主角身后垫一圈暗，给主体让位（主体②：镜头运动带出的流动不算抢主体）。
TRACK_ANG = dict(truck=180.0, crane=90.0, push=0.0)       # 默认画面往哪走（度，0 = 右，90 = 下）：truck 往左，crane 往下
_TRACK_EL_KW = ('kind', 'win', 'speed', 'col', 'tint', 'glow', 'hot', 'src', 'avoid', 'blend', 'fout', 'fin', 'switch')   # front_kw -> SR.element
_TRACKS = {}


def _segs(f0, f1, cuts):
    """[f0, f1) 按计划切点分段 -> [(a, b), ...]（整数帧；不在 (f0, f1) 里的切点不管）"""
    a, b = int(round(f0)), int(round(f1))
    cs = sorted({int(round(c)) for c in (cuts or []) if a < int(round(c)) < b})
    edges = [a] + cs + [b]
    return list(zip(edges[:-1], edges[1:]))


def _per_seg(x, i):
    """x = 一个值，或每段一个的列表（点 (x, y) 的列表、Take 的列表）-> 第 i 段用的那个"""
    if isinstance(x, (list, tuple)) and x and not isinstance(x[0], numbers.Real):
        return x[i % len(x)]
    return x


def _track_cam(a, b, v, zp, ang, eye, pre, zoom=1.0, taps=4):
    """一段 [a, b) 的世界机位（AutoCam，按每帧速率，不打 accents），缓存"""
    eye = (float(eye[0]), float(eye[1]))
    key = (a, b, round(v, 5), round(zp, 6), round(ang, 4), eye, pre, round(zoom, 4), taps)
    cam = _TRACKS.get(key)
    if cam is None:
        cam = _TRACKS[key] = AutoCam(a, b, move=dict(t=0.0), eye=eye, ang=ang, accents=None, pre=pre, zoom=zoom, taps=taps,
                                     px_per_f=v, zoom_pct_per_f=zp)
    return cam


def track_seg(T, f0, f1, cuts=None):
    """跟拍这一帧在第几段（= SR.seg(T.F, 切点)，夹在 [0, 段数-1]）和这一段的 (起, 止)"""
    sg = _segs(f0, f1, cuts)
    i = max(0, min(len(sg) - 1, seg(T.F, [a for a, _ in sg[1:]])))
    return i, sg[i]


def track(E, T, hero, world, f0, f1, move='truck', px_per_f=None, parallax=(1.8, 1.0), cuts=None, zoom_pct_per_f=None,
          ang=None, eye=(960.0, 540.0), P=None, keep='hue', dim=0.8, soft=1.5, shade=0.5, at=(960.0, 540.0), hero_r=340.0,
          front=None, front_k=0.8, front_kw=None, taps=4):
    """跟拍：主角钉住，身后有纹理的世界单向流过（前后景视差），每个计划切点换世界。返回整幅画面（世界 + 主角 + 前景）。
            None = 只要流动的世界。主角钉在它自己的位置上（SR.track 不挪它）：要「格内一路长大 / 慢慢转」自己在 hero 里做
            （只增不减），切点前后两帧中心 ≤30 px、面积 0.8-1.25 自然满足。主角要大，外框占画面三成以上。
    world   世界层：Take、Take 的列表（第 i 段用 world[i % len]：每个计划切点换一条素材或同一条的另一个时段 = 真切），
            或 callable(E, T, i) -> 不透明纹理。只给一条 Take 时切点上只是机位跳一下（同素材换构图），弱。
    f0, f1  这一镜的帧（片子帧号，止不含）；cuts = 计划切点（「本镜卡点」表里的整数帧），第 i 段 = SR.seg(T.F, cuts)。
    move    'truck' 横着流（默认画面往左走）| 'crane' 竖着流（默认往下走 = 机位上升）| 'push' 世界迎面放大（穿过去）
    px_per_f  世界每帧走多少 px（屏幕像素；truck / crane 默认 10，push 默认 0）；zoom_pct_per_f 每帧推近 %（push 默认 1.2）。
            每段单独算盖边（段越短放大越少）；ang 改方向（度，0 = 右，90 = 下）；eye = 世界素材里对准画面中心的点（可每段一个）。
    parallax (前景倍速, 世界倍速)：世界按 px_per_f x parallax[1] 走，前景按 x parallax[0]（比世界快 = 离镜头近）。
    front   前景：'snow'（实拍雪叠层 SR.element('snow')，按前景速度沿世界流向走、每个计划切点换一条实拍窗口）、'frost'（实拍雪
            迎面推 = 穿镜）、别的叠层 kind（如 'fog'）、Take / Take 列表（只取亮部、虚化、按前景速度流过、绕开主角，front_k 强度）、
            callable(E, T, i) -> 预乘纹理，None = 没有。front_kw = 传给 SR.element 的参数（kind、win、speed、col、tint、glow、
            hot、avoid=True 让开主角）。
    P, keep  给了签名色 P 就先做单色世界 MO.world(E, 世界, P, keep)；dim 世界压暗（1 = 不压），soft 略虚（模糊 px），
            shade 主角身后垫暗的深浅（0 = 不垫），at / hero_r = 主角中心和大致半径（垫暗用）。
    实测见 HANDBOOK §3b（11-05 前景改实拍雪后重测）；纯色底 ≈ 0。"""
    if move not in TRACK_ANG:
        raise KeyError(f"SR.track move {move!r}: {sorted(TRACK_ANG)}")
    i, (a, b) = track_seg(T, f0, f1, cuts)
    nseg = len(_segs(f0, f1, cuts))
    v = (0.0 if move == 'push' else 10.0) if px_per_f is None else float(px_per_f)
    zp = (1.2 if move == 'push' else 0.0) if zoom_pct_per_f is None else float(zoom_pct_per_f)
    a0 = TRACK_ANG[move] if ang is None else float(ang)
    kf, kb = float(parallax[0]), float(parallax[1])
    pre = 8.0 if i in (0, nseg - 1) else 2.0                 # 镜头两头留给双边转场；镜内切点只要动态模糊那一点
    cam = _track_cam(a, b, v * kb, zp * kb, a0, _per_seg(eye, i), pre, taps=taps)
    plate = world(E, T, i) if callable(world) else E.clip(_per_seg(world, i), T.F)
    img = cam.render(E, T, plate)
    if P is not None:
        from src.fx import motif as MO
        img = MO.world(E, img, P, keep=keep)
    if soft > 0:
        img = E.blur(img, soft)
    if dim < 1.0:
        img = E.multiply(img, E.solid((dim, dim, dim)))
    if hero is not None and shade > 0:                       # 主角身后一圈暗：主体最清楚、最先被看到
        cv = E.canvas()
        cv.circle(float(at[0]), float(at[1]), hero_r * 1.1, INK, shade)
        img = E.over(img, E.blur(cv.texture(), hero_r * 0.45))
    if hero is not None:
        h = hero(E, T) if callable(hero) else hero
        if h is not None:
            img = E.over(img, h)
    if front is None:
        return img
    vf, zf = v * kf, zp * kf
    if isinstance(front, str):                               # the string fronts are REAL footage now (SR.element),
        kw = dict(front_kw or {})                            # never 2D particles (「贴图、线段」)
        bad = sorted(k_ for k_ in kw if k_ not in _TRACK_EL_KW)
        if bad:
            _warn_once('track_front_kw', f"SR.track front_kw {bad}：这些是旧 2D 粒子的参数，前景现在是实拍叠层 SR.element，"
                                         f"不认它们（能给的：{sorted(_TRACK_EL_KW)}）")
            for k_ in bad:
                kw.pop(k_)
        K = element_tables()                                 # 表里有这个 kind 就原样用（空包集的叠层表可能只有 shards）
        kind = kw.pop('kind', front if front in K else ('snow' if front in ('shards', 'frost') else front))
        if kind != front and front == 'shards':
            _warn_once('track_front_shards', f"SR.track(front='shards')：这一集叠层表里没有 shards，前景用的是 {kind!r}")
        kw.setdefault('switch', False)                       # one footage window for the whole shot (no brightness jumps)
        if kw.pop('avoid', False) and hero is not None:
            h_ = hero(E, T) if callable(hero) else hero
            if h_ is not None:
                kw['avoid'] = h_
        if front == 'frost' or move == 'push':               # 穿镜：实拍雪迎面推过来（比世界快 = 离镜头近）
            zz = zf if zf > 0 else 2.0
            return element(E, T, img, kind, f0=f0, f1=f1, k=front_k, zoom_pct_per_f=zz, px_per_f=vf if move != 'push' else 0.0,
                           ang=a0, cuts=cuts, **kw)
        return element(E, T, img, kind, f0=f0, f1=f1, k=front_k, px_per_f=max(vf, 2.0), ang=a0, cuts=cuts, **kw)
    if callable(front):
        lay = front(E, T, i)
        return img if lay is None else E.over(img, lay)
    fc = _track_cam(a, b, vf, zf, a0, _per_seg(eye, i), pre, zoom=1.35, taps=taps)
    ft = fc.render(E, T, E.clip(_per_seg(front, i), T.F))
    if P is not None:
        from src.fx import motif as MO
        ft = MO.world(E, ft, P, keep=keep)
    ft = E.blur(fx.mono(E, ft, lo=0.45, hi=1.0, c1=P['pale'] if P is not None else WHITE), 7.0)   # only its bright parts
    if hero is not None:                                       # the near layer passes AROUND the hero, never over it
        cv = E.canvas()
        cv.circle(float(at[0]), float(at[1]), hero_r * 1.1, (1.0, 1.0, 1.0), 1.0)
        ft = E.mix(ft, E.solid((0.0, 0.0, 0.0)), E.blur(cv.texture(), hero_r * 0.25), ch='a')
    return E.screen(img, ft, 0.6 * front_k)


# ================================================================== 粒子 SR.particles
# 原片的粒子 / 碎片单窗口最多占约四成运动量（Light 455-555 卡片倾泻）。三种，都是帧的纯函数、只往一个方向走（不摆、不回）：
#   snow   飘落：一层层雪 / 冰尘沿 ang 匀速流过（近的大、快、虚，远的小、慢 = 视差），整镜连续
#   shards 爆散：f0 那一帧从 c 炸开的冰碎片（细长三角），带阻力往外飞、转着变暗，life 帧后没了
#   frost  穿镜：从消失点 c 迎面飞来的冰晶，越近越大越快，飞出画外（时光隧道感）
PARTICLES = dict(snow=dict(n=150, speed=7.0, size=3.0, ang=95.0, life=None),
                 shards=dict(n=80, speed=44.0, size=34.0, ang=None, life=34.0),
                 frost=dict(n=140, speed=0.045, size=3.0, ang=None, life=None))


PARTICLES_BAN = ("SR.particles 不对镜头开放（「贴图、线段」这种 2D 冒充物质的东西是劣质特效）。换成会动的真东西："
                 "雪 / 雾 / 冰柱用实拍叠层 SR.element(E, T, img, 'snow' | 'fog' | 'drip', f0=…, f1=…, px_per_f=… 或 "
                 "zoom_pct_per_f=…)（SR.elements() 列出这一集有哪些）；碎片爆散用 SR.shatter(E, img, F, c, …)（这一帧画面自己"
                 "碎开、露出下一张）；跟拍前景 SR.track(front='snow') 已经是实拍雪。"
                 "配方和量纲见 HANDBOOK §3b ⑤、§5b。")


def _shot_caller():
    """True when shot code (src/shots/, an episode's shot.py) is anywhere on the call stack of SR.particles -- not only the
    immediate caller: functools.partial(SR.particles, ...) handed to SR.track(front=...) is called from inside the engine
    (11-05 review). Nothing inside the engine calls SR.particles any more, so every shot-driven call is refused."""
    import sys
    fr = sys._getframe(2)
    while fr is not None:
        fn = os.path.abspath(fr.f_code.co_filename)
        if (os.sep + 'shots' + os.sep) in fn or os.path.basename(fn) in ('shot.py', 'shot_shot.py'):
            return True
        fr = fr.f_back
    return False


def _cols(col, default):
    """col = 一个颜色或颜色列表 -> 颜色列表（sRGB 0..1）"""
    if col is None:
        return list(default)
    if isinstance(col, str):
        from src.kit.core import hx
        return [hx(col)]
    if isinstance(col[0], numbers.Real):
        return [tuple(float(x) for x in col)]
    return [tuple(float(x) for x in c) for c in col]


def _col_at(cols, u):
    """颜色列表上 u (0..1) 处的颜色（线性插值）"""
    if len(cols) == 1:
        return cols[0]
    x = clamp01(u) * (len(cols) - 1)
    j = min(int(x), len(cols) - 2)
    w = x - j
    return tuple(lerp(p, q, w) for p, q in zip(cols[j], cols[j + 1]))


def particles(E, T, img=None, kind='snow', f0=0.0, f1=None, col=None, n=None, k=1.0, c=(960.0, 540.0), ang=None,
              speed=None, size=None, spread=360.0, seed=0, life=None, fin=6.0, fout=6.0, glow=0.5, soft=0.6, depth=(0.3, 1.0)):
    """粒子层：kind = 'snow'（单向飘落）| 'shards'（f0 那一帧从 c 爆散）| 'frost'（从消失点 c 穿镜飞来）。
    img 给了就叠在上面返回整幅画面；img=None 只返回预乘的粒子层（自己决定叠在主角前还是后）。
    f0 / f1  起止（片子帧号）：snow / frost 从 f0 起一直流（f0 提前一两秒 = 第一帧就满屏），f1 前 fout 帧淡出；
             shards 在 f0 那一帧炸开（写「本镜卡点」表里的帧），life 帧后飞完。fin = 淡入帧数。
    col      一个颜色或颜色列表（P['core']、(P['pale'], P['acc']) ...；不手写十六进制）：snow / frost 从远到近，shards 从新到旧。
             默认冰白 -> 冰蓝。
    n / speed / size  数量、速度、大小（默认见 PARTICLES）：snow 的 speed = 最近一层每帧 px，frost 的 speed = 每帧迎面多近
             （0.045 ≈ 一颗冰晶从出现到飞出画外 80 帧），shards 的 speed = 初速 px/帧（之后带阻力减速，只往外）。
    ang      snow 流动方向（度，0 = 右，90 = 下，默认 95）；shards 给了 ang 就只往这个方向炸（spread 度的扇面）。
    k        强度（不透明度 x）；glow 辉光；soft 整层略虚（px）；depth = 远近层的范围（snow / frost 用：越近越大越快越虚）；
    seed     换一组粒子。全部是帧的纯函数（同一帧永远一样），一直往一个方向走，不摆不回。"""
    if _shot_caller():
        raise RuntimeError(PARTICLES_BAN)
    if kind not in PARTICLES:
        raise KeyError(f"SR.particles kind {kind!r}: {sorted(PARTICLES)}")
    d = PARTICLES[kind]
    n = int(d['n'] if n is None else n)
    speed = float(d['speed'] if speed is None else speed)
    size = float(d['size'] if size is None else size)
    life = d['life'] if life is None else life
    life = None if life is None else float(life)
    f = float(T.f)
    t = f - float(f0)
    if t < -1e-6 or k <= 0 or (f1 is not None and f >= f1):
        return img if img is not None else None
    env = k * clamp01((t + 1.0) / max(float(fin), 1.0))
    if f1 is not None:
        env *= clamp01((float(f1) - f) / max(float(fout), 1.0))
    if kind == 'shards' and life is not None and t >= life:
        env = 0.0
    if env <= 1e-3:
        return img if img is not None else None
    cols = _cols(col, (COL['white'], COL['ice']))
    cv = E.canvas()
    dz0, dz1 = float(depth[0]), float(depth[1])
    if kind == 'snow':
        m = 80.0
        Wm, Hm = W + 2 * m, H + 2 * m
        for i in range(n):
            z = rr(dz0, dz1, seed, i, 'z')                       # 0 = far .. 1 = near
            a = math.radians((d['ang'] if ang is None else ang) + rr(-7.0, 7.0, seed, i, 'a'))
            v = speed * (0.25 + 0.75 * z)
            vx, vy = v * math.cos(a), v * math.sin(a)
            x0, y0 = rr(0, Wm, seed, i, 'x'), rr(0, Hm, seed, i, 'y')
            x1, y1 = x0 + vx * t, y0 + vy * t
            ox, oy = math.floor(x1 / Wm) * Wm, math.floor(y1 / Hm) * Hm   # wrap: leaves one side, comes back on the other
            x, y = x1 - ox - m, y1 - oy - m
            xp, yp = x - vx * 1.2, y - vy * 1.2
            r = size * (0.45 + 1.4 * z * z) * rr(0.7, 1.3, seed, i, 'r')
            cc = _col_at(cols, z)
            al = env * (0.30 + 0.60 * z)
            cv.c.drawLine(xp, yp, x, y, paint(cc, al * 0.30, r * 2.6, 'round'))
            cv.c.drawLine(xp, yp, x, y, paint(cc, al, r, 'round'))
    elif kind == 'frost':
        zf, zn = 4.0, 0.22
        span = zf - zn
        R0 = 0.42 * math.hypot(W, H)
        cx, cy = float(c[0]), float(c[1])
        for i in range(n):
            th = rr(0, TAU, seed, i, 'a')
            rho = math.sqrt(rr(0.02, 1.0, seed, i, 'r'))
            ph = rr(0, 1, seed, i, 'p')
            sp = speed * rr(0.75, 1.25, seed, i, 'v')

            def pos(tt):
                zz = zf - ((tt * sp + ph * span) % span)
                return cx + R0 * rho * math.cos(th) / zz, cy + R0 * rho * math.sin(th) / zz, zz
            x, y, z = pos(t)
            xp, yp, zp_ = pos(t - 1.0)
            if zp_ < z:                                          # wrapped between the two samples: no streak
                xp, yp = x, y
            near = clamp01((zf - z) / span)
            al = env * clamp01((zf - z) / 0.8) * clamp01((z - zn) / 0.35) * (0.35 + 0.65 * near)
            if al <= 0.01:
                continue
            r = size * (0.6 + 2.2 * near * near) * rr(0.7, 1.3, seed, i, 's')
            cc = _col_at(cols, near)
            cv.c.drawLine(xp, yp, x, y, paint(cc, al * 0.3, r * 2.4, 'round'))
            cv.c.drawLine(xp, yp, x, y, paint(cc, al, r, 'round'))
    else:                                                        # shards
        cx, cy = float(c[0]), float(c[1])
        lf = life or 34.0
        for i in range(n):
            if ang is None:
                a = rr(0, TAU, seed, i, 'a')
            else:
                a = math.radians(ang + rr(-0.5, 0.5, seed, i, 'a') * spread)
            v0 = speed * rr(0.35, 1.0, seed, i, 'v') * (0.7 + 0.3 * k)
            drag = rr(0.06, 0.15, seed, i, 'd')
            tl = rr(0.55, 1.0, seed, i, 'l') * lf
            if t >= tl:
                continue
            r0 = rr(8.0, 60.0, seed, i, 'r0')

            def dist(tt):
                return r0 + v0 * (1.0 - math.exp(-drag * tt)) / drag
            dd, dp = dist(t), dist(max(0.0, t - 1.2))
            x, y = cx + dd * math.cos(a), cy + dd * math.sin(a) + 0.08 * t * t
            xp, yp = cx + dp * math.cos(a), cy + dp * math.sin(a) + 0.08 * max(0.0, t - 1.2) ** 2
            age = t / tl
            L = size * rr(0.5, 1.6, seed, i, 'L') * (1.0 - 0.4 * age)
            wdt = L / rr(2.6, 4.6, seed, i, 'w')
            rot = a + math.radians(rr(-40.0, 40.0, seed, i, 'q')) + rr(-0.5, 0.5, seed, i, 'sp') * (1.0 - math.exp(-0.12 * t)) * 6.0
            ux, uy = math.cos(rot), math.sin(rot)
            cc = _col_at(cols, age)
            al = env * (1.0 - age * age)
            path = skia.Path()
            path.moveTo(x + ux * L * 0.5, y + uy * L * 0.5)
            path.lineTo(x - ux * L * 0.5 - uy * wdt, y - uy * L * 0.5 + ux * wdt)
            path.lineTo(x - ux * L * 0.5 + uy * wdt, y - uy * L * 0.5 - ux * wdt)
            path.close()
            cv.c.drawLine(xp, yp, x, y, paint(cc, al * 0.35, wdt * 1.6, 'round'))
            cv.c.drawPath(path, paint(cc, al))
    lay = cv.texture()
    if soft > 0:
        lay = E.blur(lay, soft)
    if img is None:
        return lay
    out = E.over(img, lay)
    return E.add(out, E.blur(lay, 6.0), glow) if glow > 0 else out


# ================================================================== REAL SPARKS / ARCS OVERLAY (SR.overlay)
# light keyed out of REAL footage, never CG particles. A plain luma key (straight off a busy plate) read as scratches and dragged the plate's edges along. Here every source window is
# BAKED once on the CPU (cache/overlay/<ver>/<kind>/<window>/, 16-bit key PNGs, atomic writes, any process may bake):
#   static plate content (pipe, torch, glove, haze) = the window's temporal MEDIAN, dilated for hand-held jitter, subtracted;
#   dark noise gated on a blurred residual (grain fails, streaks pass); broad blobs (bokeh, glare) opened away; only the hot
#   cores pass (lo / hi remap); a soft window around the emission point x a feather off the source border = no straight edge.
# At render time the cores are placed (pos / scale / rot / flip), recoloured on look v4 ramps (sparks: white-hot -> amber ->
# sodium -> ember = 火花橙金; arcs: white -> ICE -> ELECTRIC -> violet = 电蓝白) and get their glow back (two tinted blurs).
# Not in the storm bars (no metal sparks inside a thunderstorm) -- 'arc' is the electric one, for the build / grid shots.
OV_VER = 'v2'
OV_DIR = os.path.join(ROOT, 'cache', 'overlay', OV_VER)
OVERLAYS = {   # c = the emission point in the source, 1080p px (one per window, measured; 'auto' = the window's
              # persistent hot spot inside `search`); core = radius around c where the persistent light is kept
    'sparks': dict(clip='E1_06', tier=1080, fps=29.97, wins=((236, 261), (274, 297), (322, 335)), c=(960.0, 540.0),
                   pal='spark', bg='median', dil=2, lo=0.055, hi=0.50, blob=0.8, blobr=9, ell=(0.0, 0.0, 1100.0, 660.0),
                   what='black-ground spark rain: streaks + falling embers over an area, no source'),
    'burst': dict(clip='E4_09', tier=1080, fps=29.97, wins=((208, 226), (88, 99)), c=((1275.0, 486.0), (710.0, 515.0)),
                  pal='spark', bg='median', dil=3, lo=0.09, hi=0.55, blob=0.85, blobr=7, rad=(220.0, 600.0),
                  holes=((10.0, -160.0, 170.0),),
                  what='plasma-cutting radial burst: a fan of streaks from one point'),
    'spray': dict(clip='E4_01', tier=2160, fps=24.0, wins=((501, 520), (562, 576), (327, 342)), c='auto',
                  search=(1250.0, 250.0, 1750.0, 650.0), pal='spark', bg='median', dil=5, lo=0.10, hi=0.55, blob=0.85, blobr=7,
                  ell=(-430.0, 40.0, 820.0, 520.0), holes=((0.0, 0.0, 190.0),),
                  what='MIG welding spray (4K): hot droplets thrown off the arc (the arc glare itself is cut out)'),
    'arc': dict(clip='E4_03', tier=1080, fps=50.0, wins=((101, 151),), c=((885.0, 478.0),), pal='arc', bg='median', dil=2,
                lo=0.07, hi=0.55, blob=0.0, blobr=0, ell=(0.0, -60.0, 780.0, 430.0), below=95.0, core=130.0,
                what='blue-white arc + its branching discharge on a steel ring (the ring is subtracted / cut off below)'),
}
OV_PAL = {
    'spark': dict(ramp=(VT.ARC_WHITE, VT.AMBER, VT.SODIUM, VT.EMBER), t1=VT.AMBER, t2=VT.SODIUM, gam=0.55),
    'arc': dict(ramp=(VT.ARC_WHITE, VT.L4['ICE'], VT.L4['ELECTRIC'], VT.L4['VIOLET']), t1=VT.L4['ICE'], t2=VT.L4['ELECTRIC'],
                gam=0.6),
}
OVC_FS = '''uniform sampler2D a; uniform sampler2D L; uniform sampler2D G1; uniform sampler2D G2; uniform sampler2D ramp;
uniform float k; uniform float gk; uniform float gam; uniform vec3 t1; uniform vec3 t2; uniform int mode;
void main(){ vec2 uv = UV(); vec3 A = texture(a, uv).rgb;
  float l = texture(L, uv).r * k; float g1 = texture(G1, uv).r * k; float g2 = texture(G2, uv).r * k;
  vec3 c = texture(ramp, vec2(clamp(1.0 - pow(clamp(l, 0.0, 1.0), gam), 0.004, 0.996), 0.5)).rgb;   // hotter = whiter
  vec3 lt = lin(c) * pow(clamp(l, 0.0, 1.5), 1.4) * 1.25 + gk * (lin(t1) * g1 * 1.7 + lin(t2) * g2 * 1.4);
  vec3 res = mode == 0 ? 1.0 - (1.0 - A) * (1.0 - min(enc(lt), vec3(1.0))) : enc(lin(A) + lt);
  o = vec4(res, 1.0); }'''
_OVM, _OVT = {}, {}


def _ss(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _ov_dir(kind, wi):
    a, b = OVERLAYS[kind]['wins'][wi]
    return os.path.join(OV_DIR, kind, f'{a}_{b}')


def _ov_luma(key, tier, i):
    import cv2
    from src import footage as FT
    p = FT.frame_path(key, tier, i)
    if not os.path.exists(p):
        FT.decode_window(key, i, i + 1, tier, quiet=True)
    im = cv2.imread(p, cv2.IMREAD_COLOR)
    if im.shape[1] != 1920:
        im = cv2.resize(im, (1920, 1080), interpolation=cv2.INTER_AREA)
    return (im.astype(np.float32) @ np.array([0.0722, 0.7152, 0.2126], np.float32)) / 255.0


def ov_bake(kind, wi=0, force=False):
    """bake one source window of an overlay kind (CPU, ~1-4 s; called lazily by SR.overlay). Returns the meta dict."""
    import cv2
    import json
    from src import footage as FT
    S = OVERLAYS[kind]
    d = _ov_dir(kind, wi)
    mp = os.path.join(d, 'meta.json')
    if os.path.exists(mp) and not force:
        with open(mp) as fh:
            return json.load(fh)
    os.makedirs(d, exist_ok=True)
    key = FT.resolve(S['clip'])
    a, b = S['wins'][wi]
    idx = list(range(a, b + 1))
    bg = None
    if S['bg'] == 'median':
        st = np.stack([np.clip(_ov_luma(key, S['tier'], i) * 255.0 + 0.5, 0, 255).astype(np.uint8) for i in idx])
        bg = np.median(st, axis=0).astype(np.float32) / 255.0
        del st
        bg0 = bg.copy()
        if S['dil'] > 0:
            r = int(S['dil'])
            bg = cv2.dilate(bg, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    c = S['c']
    if c == 'auto':                                  # the persistent hot spot (torch tip / arc) inside the search box
        x0, y0, x1, y1 = (int(v) for v in S['search'])
        if bg is not None:
            ref = bg
        else:
            ref = np.zeros((1080, 1920), np.float32)
            for i in idx[::3]:
                ref += _ov_luma(key, S['tier'], i)
        sm = cv2.GaussianBlur(np.ascontiguousarray(ref[y0:y1, x0:x1]), (0, 0), 9.0)
        yy, xx = np.unravel_index(int(np.argmax(sm)), sm.shape)
        c = (float(x0 + xx), float(y0 + yy))
    elif isinstance(c[0], (tuple, list)):
        c = c[wi]
    cx, cy = float(c[0]), float(c[1])
    yy, xx = np.mgrid[0:1080, 0:1920].astype(np.float32)
    M = np.ones((1080, 1920), np.float32)
    if S.get('ell'):
        dx, dy, rx, ry = S['ell']
        M *= 1.0 - _ss(0.55, 1.0, np.sqrt(((xx - cx - dx) / rx) ** 2 + ((yy - cy - dy) / ry) ** 2))
    if S.get('rad'):                                 # radial fall-off from the emission point (sparks cool along their path)
        M *= 1.0 - _ss(float(S['rad'][0]), float(S['rad'][1]), np.hypot(xx - cx, yy - cy))
    if S.get('above') is not None:                   # nothing above the emission point (torch / nozzle)
        M *= _ss(cy - S['above'] - 60.0, cy - S['above'] + 30.0, yy)
    if S.get('below') is not None:                   # nothing below (the steel ring)
        M *= 1.0 - _ss(cy + S['below'] - 70.0, cy + S['below'] + 50.0, yy)
    for dx, dy, rh in S.get('holes', ()):            # cut-outs (the MIG arc glare)
        M *= _ss(0.7 * rh, 1.3 * rh, np.hypot(xx - cx - dx, yy - cy - dy))
    M *= _ss(0.0, 70.0, np.minimum(np.minimum(xx, 1919.0 - xx), np.minimum(yy, 1079.0 - yy)))   # source border feather
    Kc = None
    if S.get('core') and bg is not None:             # the arc's persistent core (the median took it out) comes back near c
        Kc = np.clip((bg0 - 0.5 * float(S['lo'])) / (float(S['hi']) - 0.5 * float(S['lo'])), 0.0, 1.0) * \
            0.8 * np.exp(-(np.hypot(xx - cx, yy - cy) / (0.42 * S['core'])) ** 2)
    del xx, yy
    lo, hi = float(S['lo']), float(S['hi'])
    kb = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * int(S['blobr']) + 1,) * 2) if S['blob'] > 0 else None
    peak = []
    for i in idx:
        y = _ov_luma(key, S['tier'], i)
        r = np.maximum(y - bg, 0.0) if bg is not None else y
        g = _ss(0.6 * lo, 1.4 * lo, cv2.GaussianBlur(r, (0, 0), 0.9))   # dark-noise gate on the blurred residual
        kk = np.clip((r - 0.5 * lo) / (hi - 0.5 * lo), 0.0, 1.0) * g     # only the hot cores
        if kb is not None:
            kk = np.maximum(kk - S['blob'] * cv2.morphologyEx(kk, cv2.MORPH_OPEN, kb), 0.0)
        if Kc is not None:
            kk = np.maximum(kk, Kc * float(np.clip(np.percentile(y[int(cy) - 40:int(cy) + 40, int(cx) - 40:int(cx) + 40], 90)
                                                   / max(float(np.percentile(bg0[int(cy) - 40:int(cy) + 40,
                                                                                int(cx) - 40:int(cx) + 40], 90)), 0.05), 0.0, 1.2)))
        kk *= M
        peak.append(float(np.percentile(kk, 99.9)))
        p = os.path.join(d, f'{i:06d}.png')
        tmp = p[:-4] + f'.tmp{os.getpid()}.png'
        cv2.imwrite(tmp, np.clip(kk * 65535.0 + 0.5, 0, 65535).astype(np.uint16))
        os.replace(tmp, p)
    meta = dict(kind=kind, clip=key, win=[a, b], c=[cx, cy], ver=OV_VER, p999=peak)
    tmp = mp + f'.tmp{os.getpid()}'
    with open(tmp, 'w') as fh:
        json.dump(meta, fh)
    os.replace(tmp, mp)
    _OVM.pop((kind, wi), None)
    return meta


def _ov_meta(kind, wi):
    m = _OVM.get((kind, wi))
    if m is None:
        m = _OVM[(kind, wi)] = ov_bake(kind, wi)
    return m


def _ov_tex(E, kind, wi, i):
    """GPU texture of one baked key frame (small LRU per engine; evicted textures of the same size are rewritten)"""
    import cv2
    k = (id(E), kind, wi, int(i))
    t = _OVT.get(k)
    if t is None:
        _ov_meta(kind, wi)
        arr = cv2.imread(os.path.join(_ov_dir(kind, wi), f'{int(i):06d}.png'), cv2.IMREAD_UNCHANGED)
        mine = [kk for kk in _OVT if kk[0] == id(E)]
        if len(mine) >= 12:
            old = _OVT.pop(mine[0])
            if (old.w, old.h) == (arr.shape[1], arr.shape[0]):
                old.gt.write(np.ascontiguousarray(arr), alignment=1)
                t = old
            else:
                old.gt.release()
        if t is None:
            t = E.texture_from(arr, persistent=True)
        _OVT[k] = t
    else:
        _OVT[k] = _OVT.pop(k)                              # most recent last
    return t


def _ov_loop(p, n):
    """[(index in the window, weight)]: straight through the window, then a seamless loop (period n - X, cross-faded)"""
    if n < 4:
        return [(min(max(int(round(p)), 0), n - 1), 1.0)]
    X = max(2, n // 4)
    P = n - X
    if p < P - 0.5:
        return [(max(int(round(p)), 0), 1.0)]
    q = p % P
    if q >= X:
        return [(min(int(round(q)), n - 1), 1.0)]
    w = q / X
    return [(min(int(round(q)), n - 1), w), (min(int(round(q + P)), n - 1), 1.0 - w)]


def _ov_win(S, src, win):
    if src is None:
        return int(win or 0), 0.0
    best = min(range(len(S['wins'])), key=lambda i: 0 if S['wins'][i][0] <= src <= S['wins'][i][1]
               else min(abs(src - S['wins'][i][0]), abs(src - S['wins'][i][1])))
    a, b = S['wins'][best]
    return best, float(min(max(src, a), b) - a)


def overlay_layer(E, f, kind='sparks', pos=(960.0, 540.0), scale=1.0, rot=0.0, f0=0.0, speed=1.0, src=None, win=None,
                  loop=True, flip=False):
    """the placed key layer (r = core intensity 0..1, 1080p frame) at film frame f, or None when the window ran out"""
    from src.engine.core import AFFINE_FS
    S = OVERLAYS[kind]
    wi, st = _ov_win(S, src, win)
    a, b = S['wins'][wi]
    n = b - a + 1
    p = st + max(float(f) - float(f0), 0.0) * speed * S['fps'] / 60.0
    if not loop and p > n - 0.5:
        return None
    taps = _ov_loop(p, n) if loop else [(min(int(round(p)), n - 1), 1.0)]
    texs = [(_ov_tex(E, kind, wi, a + i), w) for i, w in taps if w > 1e-3]
    lay = texs[0][0] if len(texs) == 1 else E.mix(texs[0][0], texs[1][0], texs[1][1] / (texs[0][1] + texs[1][1]))
    if scale < 0.9:                                         # pre-filter before shrinking (no crawling aliasing on streaks)
        lay = E.blur(lay, 0.5 / max(scale, 0.2) - 0.35, fast=False)
    c = _ov_meta(kind, wi)['c']
    Sx = E.s
    s, cs, sn = float(scale), math.cos(math.radians(rot)), math.sin(math.radians(rot))
    fx_ = -1.0 if flip else 1.0
    Mt = np.array([[s * cs * fx_, -s * sn, 0.0], [s * sn * fx_, s * cs, 0.0], [0.0, 0.0, 1.0]])
    T1 = np.array([[1.0, 0.0, -c[0] * Sx], [0.0, 1.0, -c[1] * Sx], [0.0, 0.0, 1.0]])
    T2 = np.array([[1.0, 0.0, pos[0] * Sx], [0.0, 1.0, pos[1] * Sx], [0.0, 0.0, 1.0]])
    fw = np.array([[lay.w / E.W, 0.0, 0.0], [0.0, lay.h / E.H, 0.0], [0.0, 0.0, 1.0]])
    inv = fw @ np.linalg.inv(T2 @ Mt @ T1)
    return E.pass_(AFFINE_FS, {'a': lay}, {'inv': tuple(inv.T.flatten()), 'src_size': (float(lay.w), float(lay.h)),
                                           'bg': (0.0, 0.0, 0.0, 0.0)})


def overlay(E, img, f, kind='sparks', k=1.0, pos=(960.0, 540.0), scale=1.0, rot=0.0, blend='screen', f0=0.0, f1=None,
            speed=1.0, src=None, win=None, loop=True, fin=1.0, fout=6.0, decay=None, flip=False, glow=1.0, pal=None):
    """实拍火花 / 电弧叠加: real sparks / arcs keyed out of footage, recoloured to look v4, composited on the finished plate.
    kind   'sparks' (black-ground spark rain, an area) | 'burst' (plasma-cutting radial fan from one point) |
           'spray' (MIG droplets thrown off the arc) | 'arc' (blue-white arc + branching discharge)
    pos    screen point (1080p px) where the source's emission point lands (the torch tip / arc; 'sparks' = its centre);
           follow a subject with pos=SR.at(pose, f, px, py) or cam.at(...)
    scale, rot (deg, + = clockwise), flip (mirror)   placement;  k strength (0.4-0.7 = restrained, 1 = full)
    f0, f1 film frames: the source starts at f0 (src = which source frame to start from, win = window index) and plays at
           `speed` (1 = real time); loop=True keeps going past the window (seamless cross-faded loop); f1 None = no end
    fin / fout attack / release frames; decay = exponent of a fall-off over [f0, f1] (a burst: 1.2-1.6)
    blend  'screen' (never clips: default) | 'add' (linear light, hotter);  glow (x the two tinted blurs);
    pal    'spark' (white-hot -> amber -> sodium -> ember) | 'arc' (white -> ICE -> ELECTRIC -> violet); default per kind
    Warm shots (welding / cutting / filament / the slab slam); NOT in the storm (no metal sparks in a thunderstorm)."""
    if k <= 0:
        return img
    f = float(f)
    if f < f0 - 1e-6 or (f1 is not None and f >= f1):
        return img
    t = f - float(f0)
    env = k * clamp01((t + 1.0) / max(float(fin), 1.0))
    if f1 is not None:
        env *= clamp01((float(f1) - f) / max(float(fout), 1.0))
        if decay:
            env *= max(1.0 - t / max(float(f1) - float(f0), 1.0), 0.0) ** float(decay)
    if env <= 1e-3:
        return img
    if kind not in OVERLAYS:
        raise KeyError(f'SR.overlay kind {kind!r}: {sorted(OVERLAYS)}')
    try:
        L = overlay_layer(E, f, kind, pos, scale, rot, f0, speed, src, win, loop, flip)
    except KeyError as e:
        raise KeyError(f"SR.overlay({kind!r}) 不对镜头开放：雪 / 雾 / 冰柱用实拍叠层 SR.element（SR.elements() 列出这一集有哪些），碎屑、爆散用 SR.shatter") from e
    if L is None:
        return img
    P = OV_PAL[pal or OVERLAYS[kind]['pal']]
    gs = max(float(scale), 0.35)
    return run(E, OVC_FS, {'a': img, 'L': L, 'G1': E.blur(L, 2.4 * gs, fast=False), 'G2': E.blur(L, 10.0 * gs),
                           'ramp': ramp_tex(E, list(P['ramp']))},
               {'k': float(env), 'gk': float(glow), 'gam': float(P['gam']), 't1': tuple(map(float, P['t1'])),
                't2': tuple(map(float, P['t2'])), 'mode': 0 if blend == 'screen' else 1})


# ================================================================== SR.element（实拍元素叠层，替掉 2D 粒子）
# 雪、雾、冰尘、水滴、气泡这类「物质」只从实拍来：
# 母题素材包 assets/<母题>/overlays.json 列出能叠的黑底窗口（素材文件 + 起点秒 + 时长 + 去背景方式 + 窗口遮罩），这里每个
# 窗口在 CPU 上烘一次（cache/element/<ver>/<kind>/<窗口>/，16 位单通道 PNG，原子写，任何进程都能烘），出画时：
#   · 素材自己在动（雪在落、雾在翻）：按 speed 播放（雪、冰柱取最近的源帧，和 Take 一样；雾按小数混合两帧），窗口放完无缝循环
#   · 再给一件单向的事：整层沿 ang 每帧 px_per_f 走 / zoom_pct_per_f 迎面推（AutoCam 盖边，不露素材边），或 pos 放到一个点上
#   · 上色：签名色的冷光（亮的接近白，暗的偏签名色），screen（默认，不会顶死）或 add，两道辉光
# 去背景：'median' = 减掉这一窗口的时间中位数（静态的背景、碗、冰堆都去掉，只留在动的东西）；'soft' = 软亮度键（雾）；
# 'luma' = 只留亮部。窗口遮罩 ell / box / below / above 去掉碗、手这类东西。
EL_VER = 'e1'
EL_DIR = os.path.join(ROOT, 'cache', 'element', EL_VER)
_EL = {'sig': None, 'kinds': {}, 'files': [], 'excluded': {}}
_ELM, _ELT = {}, {}
EL_DEF = dict(key='median', lo=0.05, hi=0.45, gam=1.0, dil=1, blur=0.0, res=1.0, gate=True)
TIERS_RES = {1080: 1.0, 1440: 1440.0 / 1080.0, 2160: 2.0}


def _el_cands():
    from src import footage as FT
    c = []
    for p in (os.environ.get('CHROMA_ELEMENTS') or '').split(os.pathsep):
        if p:
            c.append(p)
    c.append(os.path.join(ROOT, 'assets', 'overlays.json'))
    try:
        for e in FT.catalog().values():                      # assets/<母题>/footage/x.mp4 -> assets/<母题>/overlays.json
            c.append(os.path.join(os.path.dirname(os.path.dirname(e['path'])), 'overlays.json'))
    except Exception:
        pass
    import glob
    c += sorted(glob.glob(os.path.join(ROOT, '..', '..', 'assets', '*', 'overlays.json')))   # p2/assets/<母题>/ (engine
    out = []                                                                                  # = p2/chroma/engine or
    for p in c:                                                                               # p2/episodes/<集>/engine)
        p = os.path.abspath(p)
        if p not in out and os.path.exists(p):
            out.append(p)
    return out


def element_tables():
    """{kind: spec} merged from the motif tables (first table that defines a kind wins; reloaded when a table changes).
    spec = dict(what, blend, tint, gam, glow, speed, wins=[window dicts], base=<table dir>)"""
    files = _el_cands()
    sig = tuple((p, os.path.getmtime(p)) for p in files)
    if sig != _EL['sig']:
        kinds, exc = {}, {}
        for p in files:
            with open(p, encoding='utf-8') as fh:
                d = json.load(fh)
            base = os.path.dirname(p)
            for k, v in (d.get('kinds') or {}).items():
                if k in kinds:
                    continue
                v = dict(v)
                v['base'] = base
                v['table'] = p
                v['wins'] = [dict(EL_DEF, **w) for w in v.get('wins', [])]
                kinds[k] = v
            for k, v in dict(d.get('excluded') or {}, **(d.get('tested_not_used') or {})).items():
                exc.setdefault(k, v)
        _EL.update(sig=sig, kinds=kinds, files=files, excluded=exc)
        _ELM.clear()
        _ELK.clear()
    return _EL['kinds']


def elements():
    """[(kind, what, [window labels])] -- what SR.element can lay down in this episode (the motif tables)"""
    return [(k, v.get('what', ''), [f"{w.get('clip', '?')}@{w.get('src', 0)}s" for w in v['wins']])
            for k, v in sorted(element_tables().items())]


def _el_spec(kind):
    K = element_tables()
    if kind not in K:
        tip = f"；可用：{sorted(K)}" if K else "；这一集的素材包里没有 overlays.json（assets/<母题>/overlays.json）"
        raise KeyError(f"SR.element kind {kind!r} 不在母题叠层表里{tip}")
    return K[kind]


def _el_clip(S, w):
    """the footage name of a window: the catalog clip whose file is w['file'] (episode codes F01.. differ per episode, so the
    file decides), else registered from <table dir>/footage/<file>"""
    from src import footage as FT
    fn = w.get('file')
    cat = FT.catalog()
    if fn:
        for k, e in cat.items():
            if os.path.basename(e['path']) == fn:
                return k
        p = fn if os.path.isabs(fn) else os.path.join(S['base'], 'footage', fn)
        if os.path.exists(p):
            return FT.register('el_' + os.path.splitext(os.path.basename(p))[0], p)
        raise KeyError(f"SR.element: 叠层素材 {fn} 不在素材目录里，也不在 {S['base']}/footage/")
    return FT.resolve(w['clip'])


def _el_dir(kind, wi):
    S = _el_spec(kind)
    w = S['wins'][wi]
    stem = os.path.splitext(os.path.basename(w.get('file') or w.get('clip')))[0]
    return os.path.join(EL_DIR, kind, f"{stem}_{float(w.get('src', 0.0)):.2f}_{float(w.get('dur', 4.0)):.2f}")


def _el_mask(w, Wd, Hd, s):
    yy, xx = np.mgrid[0:Hd, 0:Wd].astype(np.float32)
    xx, yy = xx / s, yy / s                                       # 1080p units
    M = _ss(0.0, float(w.get('edge', 60.0)), np.minimum(np.minimum(xx, 1919.0 - xx), np.minimum(yy, 1079.0 - yy)))
    if w.get('ell'):                                              # keep inside a soft ellipse (cx, cy, rx, ry)
        cx, cy, rx, ry = (float(v) for v in w['ell'])
        M *= 1.0 - _ss(0.6, 1.0, np.sqrt(((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2))
    for b in w.get('cut', ()):                                    # cut soft boxes away (x0, y0, x1, y1, feather): the bowl
        x0, y0, x1, y1, fe = (float(v) for v in b)
        inside = _ss(-fe, fe, np.minimum(np.minimum(xx - x0, x1 - xx), np.minimum(yy - y0, y1 - yy)))
        M *= 1.0 - inside
    if w.get('below') is not None:                                # fade out downwards: y or [y_full, y_gone] (1080p)
        b = w['below']; y0, y1 = (float(b) - 40.0, float(b) + 40.0) if isinstance(b, numbers.Real) else map(float, b)
        M *= 1.0 - _ss(y0, y1, yy)
    if w.get('ragged') is not None:                               # fade out below a wavy line (y0, amp, fade): no straight cut
        y0, amp, fe = (float(v) for v in w['ragged'])
        yc = y0 + amp * (0.6 * np.sin(xx / 170.0 + 1.3) + 0.4 * np.sin(xx / 61.0 + 0.4) + 0.25 * np.sin(xx / 23.0 + 2.1))
        M *= 1.0 - _ss(yc - 0.5 * fe, yc + 0.5 * fe, yy)
    if w.get('above') is not None:                                # fade out upwards: y or [y_gone, y_full]
        b = w['above']; y0, y1 = (float(b) - 40.0, float(b) + 40.0) if isinstance(b, numbers.Real) else map(float, b)
        M *= _ss(y0, y1, yy)
    return M


def element_bake(kind, wi=0, force=False):
    """bake one window of an element kind (CPU, a few s; called lazily by SR.element). -> meta dict"""
    import cv2
    from src import footage as FT
    S = _el_spec(kind)
    w = S['wins'][wi]
    d = _el_dir(kind, wi)
    mp = os.path.join(d, 'meta.json')
    if os.path.exists(mp) and not force:
        with open(mp) as fh:
            m = json.load(fh)
        if m.get('spec') == {k: v for k, v in w.items() if k != 'what'}:
            return m
    os.makedirs(d, exist_ok=True)
    key = _el_clip(S, w)
    pr = FT.probe(key)
    fps = float(pr['fps'] or 25.0)
    i0 = int(round(float(w.get('src', 0.0)) * fps))
    i1 = min(int(pr['n']), i0 + max(4, int(round(float(w.get('dur', 4.0)) * fps))))
    res = float(w.get('res', 1.0))                               # key size x 1080p (1.333 = 2560 x 1440 from a 4K source:
    tier = FT.tier_for(key, 1440 if res > 1.01 else 1080)       # fine snow stays crisp under the cover zoom)
    res = min(res, TIERS_RES.get(tier, 1.0))
    FT.decode_window(key, i0, i1, tier, quiet=True)
    Wd, Hd = int(round(1920 * res)), int(round(1080 * res))

    def luma(i):
        im = cv2.imread(FT.frame_path(key, tier, i), cv2.IMREAD_COLOR)
        if im is None:
            FT.decode_window(key, i, i + 1, tier, quiet=True)
            im = cv2.imread(FT.frame_path(key, tier, i), cv2.IMREAD_COLOR)
        if (im.shape[1], im.shape[0]) != (Wd, Hd):
            im = cv2.resize(im, (Wd, Hd), interpolation=cv2.INTER_AREA)
        return (im.astype(np.float32) @ np.array([0.0722, 0.7152, 0.2126], np.float32)) / 255.0
    idx = list(range(i0, i1))
    mode = w['key']
    lo, hi, gam = float(w['lo']), float(w['hi']), float(w['gam'])
    bg = None
    if mode == 'median' or float(w.get('sub', 0.0)) > 0:
        st = np.stack([np.clip(luma(i) * 255.0 + 0.5, 0, 255).astype(np.uint8) for i in idx[::max(1, len(idx) // 48)]])
        bg = np.median(st, axis=0).astype(np.float32) / 255.0
        del st
        r = int(w.get('dil', 1))
        if r > 0:
            bg = cv2.dilate(bg, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    M = _el_mask(w, Wd, Hd, res)
    cov, p999 = [], []
    for j, i in enumerate(idx):
        y = luma(i)
        if mode == 'median':
            r_ = np.maximum(y - bg, 0.0)
            kk = np.clip((r_ - 0.5 * lo) / (hi - 0.5 * lo), 0.0, 1.0)
            if w.get('gate', True):                                # dark-noise gate on the blurred residual (grain fails)
                kk *= _ss(0.6 * lo, 1.4 * lo, cv2.GaussianBlur(r_, (0, 0), 0.9))
        else:                                                     # 'soft' (fog) / 'luma' (bright parts only)
            yy_ = np.maximum(y - float(w.get('sub', 0.0)) * bg, 0.0) if bg is not None else y
            kk = np.clip((yy_ - lo) / (hi - lo), 0.0, 1.0)
        if gam != 1.0:
            kk = kk ** gam
        if float(w['blur']) > 0:
            kk = cv2.GaussianBlur(kk, (0, 0), float(w['blur']) * res)
        kk *= M
        cov.append(float((kk > 0.1).mean()))
        p999.append(float(np.percentile(kk, 99.9)))
        p = os.path.join(d, f'{j:05d}.png')
        tmp = p[:-4] + f'.tmp{os.getpid()}.png'
        cv2.imwrite(tmp, np.clip(kk * 65535.0 + 0.5, 0, 65535).astype(np.uint16))
        os.replace(tmp, p)
    meta = dict(kind=kind, clip=key, file=w.get('file'), i0=i0, i1=i1, n=len(idx), fps=fps, res=res, tier=tier, ver=EL_VER,
                cov=round(float(np.mean(cov)), 4), p999=round(float(np.median(p999)), 3),
                spec={k: v for k, v in w.items() if k != 'what'})
    tmp = mp + f'.tmp{os.getpid()}'
    with open(tmp, 'w') as fh:
        json.dump(meta, fh)
    os.replace(tmp, mp)
    _ELM.pop((kind, wi), None)
    return meta


def element_bake_all(kinds=None, force=False):
    """bake every window of every element kind (the orchestrator calls this before the shots, so the sandbox never has to)"""
    out = {}
    for k, S in element_tables().items():
        if kinds and k not in kinds:
            continue
        for wi in range(len(S['wins'])):
            out[f'{k}/{wi}'] = element_bake(k, wi, force)
    return out


def _el_meta(kind, wi):
    m = _ELM.get((kind, wi))
    if m is None:
        m = _ELM[(kind, wi)] = element_bake(kind, wi)
    return m


def _el_tex(E, kind, wi, j):
    import cv2
    k = (id(E), kind, wi, int(j))
    t = _ELT.get(k)
    if t is None:
        _el_meta(kind, wi)
        arr = cv2.imread(os.path.join(_el_dir(kind, wi), f'{int(j):05d}.png'), cv2.IMREAD_UNCHANGED)
        mine = [kk for kk in _ELT if kk[0] == id(E)]
        if len(mine) >= 16:
            old = _ELT.pop(mine[0])
            if (old.w, old.h) == (arr.shape[1], arr.shape[0]):
                old.gt.write(np.ascontiguousarray(arr), alignment=1)
                t = old
            else:
                old.gt.release()
        if t is None:
            t = E.texture_from(arr, persistent=True)
        _ELT[k] = t
    else:
        _ELT[k] = _ELT.pop(k)
    return t


_ELR = {}                                                       # own-colour textures (11-05), separate from _ELT
_ELK = {}                                                       # (kind, wi) -> footage name in THIS process (own colour)


def _el_rgb_tex(E, kind, wi, j):
    """the footage's own colour (sRGB uint8, the key's size) of baked frame j of window wi -- SR.element(own=True)"""
    import cv2
    from src import footage as FT
    k = (id(E), kind, wi, int(j))
    t = _ELR.get(k)
    if t is not None:
        _ELR[k] = _ELR.pop(k)
        return t
    m = _el_meta(kind, wi)
    i = int(m['i0']) + int(j)
    key = _ELK.get((kind, wi))
    if key is None:                                              # 11-05 review V5: the footage name in meta['clip'] was registered by the
        S = _el_spec(kind)                                       # process that baked the window (el_<file> for a borrowed pack); a new
        key = _ELK[(kind, wi)] = _el_clip(S, S['wins'][wi])      # process must resolve / register it again (KeyError: not in catalog)
    fp = FT.frame_path(key, m['tier'], i)
    im = cv2.imread(fp, cv2.IMREAD_COLOR)
    if im is None:
        FT.decode_window(key, i, i + 1, m['tier'], quiet=True)
        im = cv2.imread(fp, cv2.IMREAD_COLOR)
    Wd, Hd = int(round(1920 * float(m['res']))), int(round(1080 * float(m['res'])))
    if (im.shape[1], im.shape[0]) != (Wd, Hd):
        im = cv2.resize(im, (Wd, Hd), interpolation=cv2.INTER_AREA)
    arr = np.ascontiguousarray(im[..., ::-1])
    mine = [kk for kk in _ELR if kk[0] == id(E)]
    if len(mine) >= 16:
        old = _ELR.pop(mine[0])
        if (old.w, old.h) == (arr.shape[1], arr.shape[0]):
            old.gt.write(arr, alignment=1)
            t = old
        else:
            old.gt.release()
    if t is None:
        t = E.texture_from(arr, persistent=True)
    _ELR[k] = t
    return t


def _el_taps(p, n):
    """[(index, weight)] for a fractional source position p in a looping window of n frames: the two neighbours blended
    (25 / 30 fps footage in a 60 fps film without stutter), past the end a seamless cross-faded loop"""
    out = {}
    i = math.floor(p)
    fr = p - i
    for q, wq in ((i, 1.0 - fr), (i + 1, fr)):
        if wq < 1e-3:
            continue
        for j, wj in _ov_loop(float(q), n):
            out[j] = out.get(j, 0.0) + wq * wj
    return sorted(out.items())


def element_layer(E, f, kind, wi=0, t0=0.0, speed=1.0, st=0.0, interp='nearest'):
    """the baked key (R = intensity 0..1, 1080p frame, unplaced) of window wi at film frame f, source playing from st
    (source frames into the window) at film frame t0. interp 'nearest' (default: a 25 / 30 fps source frame is held, like a
    Take -- blending two sharp snow frames and then lighting them non-linearly flickers every other frame and doubles the
    flakes) | 'blend' (soft elements: fog)"""
    m = _el_meta(kind, wi)
    n = int(m['n'])
    p = float(st) + max(float(f) - float(t0), 0.0) * float(speed) * float(m['fps']) / TL.FPS
    if interp != 'blend':
        p = math.floor(p + 0.5)
    taps = [(j, w) for j, w in _el_taps(p, n) if w > 1e-3]
    lay, acc = None, 0.0
    for j, w in taps:
        t = _el_tex(E, kind, wi, j)
        if lay is None:
            lay, acc = t, w
        else:
            acc += w
            lay = E.mix(lay, t, w / acc)
    return lay


def _el_rgb_layer(E, f, kind, wi=0, t0=0.0, speed=1.0, st=0.0, interp='nearest'):
    """element_layer's twin for the footage colour (same source frame, same taps) -- SR.element(own=True)"""
    m = _el_meta(kind, wi)
    n = int(m['n'])
    p = float(st) + max(float(f) - float(t0), 0.0) * float(speed) * float(m['fps']) / TL.FPS
    if interp != 'blend':
        p = math.floor(p + 0.5)
    lay, acc = None, 0.0
    for j, w in [(j, w) for j, w in _el_taps(p, n) if w > 1e-3]:
        t = _el_rgb_tex(E, kind, wi, j)
        if lay is None:
            lay, acc = t, w
        else:
            acc += w
            lay = E.mix(lay, t, w / acc)
    return lay


# alpha = the baked key, colour = the footage itself UN-PREMULTIPLIED (review V1, 11-05): footage over black is colour x coverage, so a
# half-covered pixel is the material at half brightness -- laid over a saturated field as is, it reads as grey / dark dirt. Keep the
# hue (c / luma) and bring the luma back up to the material's own brightness ref (the window's key `hi` = where the key reads full
# coverage; `lift` overrides), never above max(luma, ref), never clipped to white (a channel over 1 scales the colour, hue kept).
ELO_FS = '''uniform sampler2D a; uniform sampler2D L; uniform sampler2D C; uniform sampler2D av; uniform int has_av;
uniform float k; uniform float hot; uniform float ref;
void main(){ vec2 uv = UV(); vec3 A = texture(a, uv).rgb; float m = has_av == 1 ? 1.0 - texture(av, uv).a : 1.0;
  float al = clamp(texture(L, uv).r * k * m, 0.0, 1.0);
  vec3 c = texture(C, uv).rgb; float l = luma(c); c = c / max(l, 1e-3) * max(l, ref) * hot;
  c /= max(1.0, max(c.r, max(c.g, c.b)));
  o = vec4(mix(A, c, al), 1.0); }'''
ELOP_FS = '''uniform sampler2D L; uniform sampler2D C; uniform sampler2D av; uniform int has_av; uniform float k; uniform float hot;
uniform float ref;
void main(){ vec2 uv = UV(); float m = has_av == 1 ? 1.0 - texture(av, uv).a : 1.0;
  float al = clamp(texture(L, uv).r * k * m, 0.0, 1.0);
  vec3 c = texture(C, uv).rgb; float l = luma(c); c = c / max(l, 1e-3) * max(l, ref) * hot;
  c /= max(1.0, max(c.r, max(c.g, c.b)));
  o = vec4(c * al, al); }'''

ELC_FS = '''uniform sampler2D a; uniform sampler2D L; uniform sampler2D G1; uniform sampler2D G2; uniform sampler2D ramp;
uniform sampler2D av; uniform int has_av; uniform float k; uniform float gk; uniform float gam; uniform float tint;
uniform vec3 t1; uniform vec3 t2; uniform int mode; uniform float hot;
void main(){ vec2 uv = UV(); vec3 A = texture(a, uv).rgb; float m = has_av == 1 ? 1.0 - texture(av, uv).a : 1.0;
  float l = texture(L, uv).r * k * m; float g1 = texture(G1, uv).r * k * m; float g2 = texture(G2, uv).r * k * m;
  vec3 rc = texture(ramp, vec2(clamp(1.0 - pow(clamp(l, 0.0, 1.0), gam), 0.004, 0.996), 0.5)).rgb;   // brighter = whiter
  vec3 c = mix(vec3(1.0), rc, tint);
  vec3 lt = lin(c) * pow(clamp(l, 0.0, 1.5), 1.25) * hot + gk * (lin(t1) * g1 * 0.9 + lin(t2) * g2 * 0.55);
  vec3 res = mode == 0 ? 1.0 - (1.0 - A) * (1.0 - min(enc(lt), vec3(1.0))) : enc(lin(A) + lt);
  o = vec4(res, 1.0); }'''
ELP_FS = '''uniform sampler2D L; uniform sampler2D G1; uniform sampler2D G2; uniform sampler2D ramp; uniform sampler2D av;
uniform int has_av; uniform float k; uniform float gk; uniform float gam; uniform float tint; uniform vec3 t1; uniform vec3 t2;
uniform float hot;
void main(){ vec2 uv = UV(); float m = has_av == 1 ? 1.0 - texture(av, uv).a : 1.0;
  float l = texture(L, uv).r * k * m; float g1 = texture(G1, uv).r * k * m; float g2 = texture(G2, uv).r * k * m;
  vec3 rc = texture(ramp, vec2(clamp(1.0 - pow(clamp(l, 0.0, 1.0), gam), 0.004, 0.996), 0.5)).rgb;
  vec3 lt = lin(mix(vec3(1.0), rc, tint)) * pow(clamp(l, 0.0, 1.5), 1.25) * hot + gk * (lin(t1) * g1 * 0.9 + lin(t2) * g2 * 0.55);
  vec3 c = min(enc(lt), vec3(1.0)); float al = max(c.r, max(c.g, c.b));
  o = vec4(c, al); }'''


def _el_cols(col):
    """ramp (hot -> cold) for the element light: col None = the signature palette (core, pale, acc, deep) of MO.current();
    one colour / a list = those"""
    from src.fx import motif as MO
    if col is not None:
        cs = _cols(col, ())
        return cs if len(cs) > 1 else [cs[0], cs[0]]
    P = MO.current()
    if P is None:
        return [COL['white'], COL['ice'], COL['blue'], COL['deep']]
    return [P['core'], P['pale'], P['acc'], P['deep']]


EL_PUSH_MAX = 1.6                                               # a full-screen element push restarts before it is 1.6x


def element(E, T, img=None, kind='snow', f0=None, f1=None, k=1.0, px_per_f=0.0, ang=None, zoom_pct_per_f=0.0, pos=None,
            scale=1.0, rot=0.0, flip=False, src=None, win=None, speed=1.0, cuts=None, eye=(960.0, 540.0), blend=None,
            col=None, tint=None, glow=None, fin=None, fout=6.0, avoid=None, taps=4, hot=None, switch=True, own=None,
            lift=None):
    """实拍元素叠层（雪 / 雾 / 水滴 / 气泡 …… 这一集母题叠层表 assets/<母题>/overlays.json 里有什么就是什么，SR.elements() 列出）。
    img 给了就叠在上面返回整幅画面；img=None 只返回预乘的光层（自己决定叠在主角前后）。
    素材自己在动（雪在落、雾在翻）；再加一件单向的事，两种放法：
      整屏（pos=None，默认）：这一层盖满画面，沿 ang（度，0 = 右，90 = 下）每帧走 px_per_f，或 zoom_pct_per_f 迎面推（穿过去）；
             f0..f1 = 这一层出现的帧（f1 给了才能算盖边；不给 = 只播放、不平移）；cuts = 计划切点：每段换下一个窗口、重新起机位
             （和 SR.track 一样，段越短放大越少），每段的窗口 = (win + 段号) % 窗口数（switch=False = 一直用 win 这一个窗口，
             只在切点上重新起机位；SR.track 的前景就是这样，免得雪的明暗每拍跳一次）。迎面推没给 cuts 时，放大到约 1.6 倍就自动
             重新起一段（一张平面雪一路放大只剩几团糊光斑）；要和卡点对齐就自己给 cuts。
      放到一点（pos=(x, y)）：素材里的发射点（overlays.json 的 c，默认画面中心）落在 pos，scale / rot / flip；px_per_f + ang
             让它整体单向漂，zoom_pct_per_f 让它越来越大（迎面来）。
    k 强度 0..1.5；speed 播放速度（1 = 实拍原速；src = 从窗口里第几秒起）；win = 用第几个窗口（默认 0）；
    blend 'screen'（默认，不会顶死）| 'add'；col = 光的颜色（默认签名色：亮的接近白、暗的偏签名色），tint 0..1 上色多少；
    glow 辉光；fin / fout 淡入淡出帧；avoid = 预乘纹理（主角层），盖住的地方元素让开；hot = 亮度 x。
    每格只放一种元素当主角的陪衬；量纲（编排器的有效运动量口径）见 HANDBOOK §3b ⑤ / §5b。
    彩粉保留原色、盖住整屏饱和色；半透明处按键值还原成素材本身的亮度（黑底素材 = 颜色 × 覆盖，不还原就是一层灰暗的脏色）。
    col / tint / glow / blend 不起作用；hot = 颜色倍数（默认 = 叠层表的 own_hot，没写 1；表里的 hot 是 screen 光的强度，不用在这里），
    lift = 半透明处还原到的亮度（默认 None = 这个窗口键的 hi，即键读满的亮度）。默认 own=None = 跟叠层表（没写就是 False），
    以前的镜头一个像素不变。尘、烟这类半透明的大团在亮的饱和底上照样会和底色混成灰（补色相混），那里用 screen + col。"""
    S = _el_spec(kind)
    nw = len(S['wins'])
    f = float(T.f)
    a0 = float(T.start if f0 is None else f0)
    if f < a0 - 1e-6 or (f1 is not None and f >= f1) or k <= 0:
        return img
    fin = float(S.get('fin', 1.0) if fin is None else fin)
    env = k * clamp01((f - a0 + 1.0) / max(fin, 1.0))
    if f1 is not None:
        env *= clamp01((float(f1) - f) / max(float(fout), 1.0))
    if env <= 1e-3:
        return img
    w0 = int(win or 0)
    zp_ = float(zoom_pct_per_f or 0.0)
    if not cuts and f1 is not None and pos is None and zp_ > 1e-6:   # 11-05 review: a push with no cuts kept zooming one flat
        seg = max(12, int(math.log(EL_PUSH_MAX) / math.log(1.0 + zp_ / 100.0)))   # sheet (99 f at 2 %/f = 7x: a few soft blobs);
        cuts = list(range(int(round(a0)) + seg, int(f1), seg)) or None          # restart the push every ~1.6x (new window / start)
    if cuts and f1 is not None:
        i, (a, b) = track_seg(T, a0, f1, cuts)
    else:
        i, (a, b) = 0, (a0, float(f1) if f1 is not None else a0)
    wi = (w0 + (i if switch else 0)) % nw
    env *= float(S['wins'][wi].get('gain', 1.0))                # per-window level (near / far layers)
    m = _el_meta(kind, wi)
    st = 0.0 if src is None else float(src) * float(m['fps'])
    st += i * 0.37 * float(m['n'])                              # the next segment starts elsewhere in its window
    lay = element_layer(E, f, kind, wi, a, speed * float(S.get('speed', 1.0)), st % max(1.0, float(m['n'])),
                        S.get('interp', 'nearest'))
    own = bool(S.get('own', False) if own is None else own)
    rgb = _el_rgb_layer(E, f, kind, wi, a, speed * float(S.get('speed', 1.0)), st % max(1.0, float(m['n'])),
                        S.get('interp', 'nearest')) if own else None
    v, zp = float(px_per_f), float(zoom_pct_per_f)
    an = 90.0 if ang is None else float(ang)
    if pos is None:
        if (abs(v) > 1e-6 or abs(zp) > 1e-6) and f1 is None:
            raise ValueError('SR.element：整屏单向走（px_per_f / zoom_pct_per_f）要给 f1（这一层的最后一帧 + 1），好算盖边')
        if abs(v) > 1e-6 or abs(zp) > 1e-6:
            cam = _track_cam(int(round(a)), int(round(b)), v, zp, an, eye, 2.0, taps=taps)
            lay = cam.render(E, T, lay)
            if rgb is not None:
                rgb = cam.render(E, T, rgb)
    else:
        from src.engine.core import AFFINE_FS
        t = max(0.0, f - a)
        c = S['wins'][wi].get('c') or (960.0, 540.0)
        ca, sa = math.cos(math.radians(an)), math.sin(math.radians(an))
        px_, py_ = float(pos[0]) + v * t * ca, float(pos[1]) + v * t * sa
        s = float(scale) * (1.0 + zp / 100.0) ** t
        cs, sn = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        fx_ = -1.0 if flip else 1.0
        Sx = E.s
        Mt = np.array([[s * cs * fx_, -s * sn, 0.0], [s * sn * fx_, s * cs, 0.0], [0.0, 0.0, 1.0]])
        T1 = np.array([[1.0, 0.0, -float(c[0]) * Sx], [0.0, 1.0, -float(c[1]) * Sx], [0.0, 0.0, 1.0]])
        T2 = np.array([[1.0, 0.0, px_ * Sx], [0.0, 1.0, py_ * Sx], [0.0, 0.0, 1.0]])
        fw = np.array([[lay.w / E.W, 0.0, 0.0], [0.0, lay.h / E.H, 0.0], [0.0, 0.0, 1.0]])
        inv = fw @ np.linalg.inv(T2 @ Mt @ T1)
        lay = E.pass_(AFFINE_FS, {'a': lay}, {'inv': tuple(inv.T.flatten()), 'src_size': (float(lay.w), float(lay.h)),
                                              'bg': (0.0, 0.0, 0.0, 0.0)})
        if rgb is not None:
            rgb = E.pass_(AFFINE_FS, {'a': rgb}, {'inv': tuple(inv.T.flatten()), 'src_size': (float(rgb.w), float(rgb.h)),
                                                  'bg': (0.0, 0.0, 0.0, 0.0)})
    if own:
        ref = float(lift) if lift is not None else min(1.0, max(0.2, float(S['wins'][wi].get('hi', 0.5))))
        u = dict(k=float(env), has_av=int(avoid is not None), hot=float(S.get('own_hot', 1.0) if hot is None else hot), ref=ref)
        ins = {'L': lay, 'C': rgb, 'av': avoid if avoid is not None else lay}
        if img is None:
            return run(E, ELOP_FS, ins, u)
        ins['a'] = img
        return run(E, ELO_FS, ins, u)
    gs = max(float(scale), 0.35)
    gk = float(S.get('glow', 0.6) if glow is None else glow)
    cols = _el_cols(col)
    u = dict(k=float(env), gk=gk, gam=float(S.get('gam', 0.6)), tint=float(S.get('tint', 0.55) if tint is None else tint),
             t1=tuple(map(float, cols[min(1, len(cols) - 1)])), t2=tuple(map(float, cols[min(2, len(cols) - 1)])),
             has_av=int(avoid is not None), hot=float(S.get('hot', 1.0) if hot is None else hot))
    ins = {'L': lay, 'G1': E.blur(lay, 2.4 * gs, fast=False), 'G2': E.blur(lay, 12.0 * gs),
           'ramp': ramp_tex(E, [VT.hexof(c_) for c_ in cols]), 'av': avoid if avoid is not None else lay}
    if img is None:
        return run(E, ELP_FS, ins, u)
    u['mode'] = 0 if (blend or S.get('blend', 'screen')) == 'screen' else 1
    ins['a'] = img
    return run(E, ELC_FS, ins, u)


# ================================================================== SR.shatter（画面本身碎开，替掉 2D 碎片）
# 碎片里装的就是这一帧画面：碎块真的分开（逐像素反查哪一块盖住它），单向往外飞、翻、朝镜头来（越来越大），缝里露出下一张
# （nxt），碎块边上是画面自己的折射（玻璃厚度把边上的光往里折），不画任何裂线；碎块在下一张上投一点软阴影。
NSH = 64
SHAT2_FS = '''uniform sampler2D img; uniform vec4 S[64]; uniform vec4 D[64]; uniform vec4 Q[64]; uniform int n; uniform float refr;
uniform float bw; uniform float fk;
void main(){ vec2 p = gl_FragCoord.xy; int hit = -1; float zt = -1.0; vec2 qh = vec2(0.0); float eh = 0.0; vec2 nh = vec2(0.0);
  for (int i = 0; i < 64; i++){ if (i >= n) break; if (Q[i].w < 0.5) continue;
    vec2 si = S[i].xy; float sc = D[i].z; float a = D[i].w; float cs = cos(a), sn = sin(a);
    vec2 v = (p - si - D[i].xy) / sc; vec2 q = vec2(cs * v.x + sn * v.y, -sn * v.x + cs * v.y) + si;   // into the unbroken frame
    if (distance(q, si) > S[i].z) continue;                  // outside this shard's bound
    float d1 = distance(q, si); float e = 1e9; bool own = true;
    for (int j = 0; j < 64; j++){ if (j >= n) break; if (j == i) continue;
      vec2 sj = S[j].xy; vec2 m = sj - si; float L = length(m);
      float ej = (dot(q - si, m) - 0.5 * L * L) / max(L, 1e-3);      // signed: < 0 = on si's side of the bisector
      if (ej > 0.0){ own = false; break; }
      e = min(e, -ej); }
    if (!own) continue;
    if (Q[i].y > zt){ zt = Q[i].y; hit = i; qh = q; eh = e * sc; nh = normalize(si - q + vec2(1e-4)); } }
  if (hit < 0){ o = vec4(0.0); return; }
  float prof = 1.0 - smoothstep(0.0, bw, eh); prof *= prof;
  vec2 qq = qh + nh * refr * 11.0 * u_s * prof;                       // the glass thickness bends the edge light inwards
  vec3 col = texture(img, clamp(qq, vec2(0.5), u_size - 0.5) / u_size).rgb;
  float l = luma(col);
  col *= 1.0 + fk * Q[hit].z + refr * 0.22 * prof * (0.4 + l);         // facet light + the bevel catches the frame's own light
  float cov = clamp(eh + 0.5, 0.0, 1.0);
  o = vec4(clamp(col, 0.0, 1.0) * cov, cov); }'''
SHADE_FS = '''uniform sampler2D a; uniform sampler2D s; uniform float k;
void main(){ vec2 uv = UV(); o = vec4(texture(a, uv).rgb * (1.0 - k * texture(s, uv).a), 1.0); }'''
_SHL = {}


def _shatter_layout(F, c, cells, seed, ang, spread):
    """seed points (radial, denser at c), each shard's bound radius and its own random numbers -- cached per (F, c, ...)"""
    key = (int(F), round(float(c[0]), 1), round(float(c[1]), 1), int(cells), int(seed), ang, float(spread))
    L = _SHL.get(key)
    if L is not None:
        return L
    rng = np.random.default_rng((int(F) * 7919 + int(seed) * 104729 + 17) & 0x7FFFFFFF)
    cx, cy = float(c[0]), float(c[1])
    R = 1.08 * max(math.hypot(cx - x, cy - y) for x in (0.0, W) for y in (0.0, H))
    n = int(min(NSH, max(6, cells)))
    pts = [(cx + rng.uniform(-10, 10), cy + rng.uniform(-10, 10))]
    golden = math.pi * (3.0 - math.sqrt(5.0))
    for k in range(1, n):                                       # sunflower spiral, radius^0.75: small shards near c
        u = (k + rng.uniform(-0.35, 0.35)) / (n - 1)
        r = R * min(1.0, max(0.02, u)) ** 0.78
        a = k * golden + rng.uniform(-0.25, 0.25)
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    P = np.array(pts, np.float64)
    gx, gy = np.meshgrid(np.linspace(-60, W + 60, 160), np.linspace(-60, H + 60, 90))
    G = np.c_[gx.ravel(), gy.ravel()]
    own = np.argmin(((G[:, None, :] - P[None, :, :]) ** 2).sum(-1), axis=1)
    bound = np.array([np.max(np.hypot(*(G[own == i] - P[i]).T)) if np.any(own == i) else 40.0 for i in range(n)]) + 30.0
    v = P - np.array([cx, cy])
    d = np.hypot(v[:, 0], v[:, 1]) + 1e-3
    dirs = v / d[:, None]
    a_c = rng.uniform(0.0, 2.0 * math.pi)                       # the centre shard flies off too (no shard stays put)
    dirs[0] = (math.cos(a_c), math.sin(a_c))
    d[0] = 520.0
    if ang is not None:                                          # one-sided burst: bend every direction into the fan
        a0 = math.radians(float(ang))
        th = np.arctan2(dirs[:, 1], dirs[:, 0])
        dth = (th - a0 + math.pi) % (2 * math.pi) - math.pi
        th = a0 + dth * (float(spread) / 360.0)
        dirs = np.c_[np.cos(th), np.sin(th)]
    L = _SHL[key] = dict(P=P, bound=bound, dirs=dirs, dist=d, mul=rng.uniform(0.6, 1.35, n), rot=rng.uniform(-1.0, 1.0, n),
                         z=rng.uniform(0.0, 1.0, n), fac=rng.uniform(-1.0, 1.0, n), n=n)
    return L


SHAT_PX_MIN, SHAT_TAU_MAX, SHAT_OPEN_MIN = 360.0, 12.0, 48.0


def shatter_open(f, F, px=620.0, tau=8.0, drift=7.0):
    """how far the shards have opened at film frame f for a break on frame F (px, one way only, never closes): fast out of
    the break (px / tau px per frame at first), then a steady drift -- the shards keep flying until they leave the frame.
    11-05 review: px >= 360 and tau <= 12 (a small / slow opening is a net of even straight gaps = a fake crack drawing;
    a crack that only shows is not a shatter -- it is the frame breaking on the hit or nothing)."""
    t = float(f) - float(F)
    if t < -1e-6:
        return 0.0
    t += 1.0                                                     # the break frame itself is already broken
    px, tau = max(float(px), SHAT_PX_MIN), min(max(float(tau), 1.0), SHAT_TAU_MAX)
    return px * (1.0 - math.exp(-t / tau)) + max(0.0, float(drift)) * t


def shatter(E, img, F, c=(960.0, 540.0), cells=28, open=0.0, refract=1.0, nxt=None, seed=0, ang=None, spread=120.0,
            depth=1.0, spin=1.0, facet=0.18, shadow=0.45):
    """画面本身碎开：把 img（这一帧的画面）按 Voronoi 碎成 cells 块（≤ 64，c 附近碎得细），每块装的就是 img 自己。
    F      碎的那一帧（整数，写「本镜卡点」表里的帧；同一个 F 永远同一种碎法）
    open   张开了多少 px（只增不减：open=SR.shatter_open(T.f, F, px=620, tau=8) = 先快后慢、一直往外飞；初速 px / tau px/帧）
    nxt    缝里露出来的下一张画面（写上它：下一个构图 / 下一条素材）；None = 这一帧自己压暗、虚化、推远的一份（碎块后面是同一个
           世界更深的一层，不是平涂墨色）；refract 0..1.5 碎块边上画面自己的折射（玻璃厚度，不画线）；
    ang / spread  给了 ang（度，0 = 右，90 = 下）就只往这个方向的 spread 度扇面炸；depth 朝镜头来的程度（碎块越飞越大）；
    spin 翻转；facet 每块的明暗差；shadow 碎块在下一张上的软阴影。
    用法（冲击帧那一拍碎开、露出下一个构图；镜内可用，每片 ≤3 次，c 放在主体上）：
        if T.F >= BRK: img = SR.shatter(E, A(T), BRK, c=(960, 560), cells=28, open=SR.shatter_open(T.f, BRK), nxt=B(T))
    不画裂线（lk=0 的画面派生碎裂）。"""
    op = max(0.0, float(open))
    if op <= 0.05:
        return img
    op = max(op, SHAT_OPEN_MIN)                                  # broken = visibly open on the first frame (no thin even gaps)
    L = _shatter_layout(F, c, cells, seed, ang, spread)
    n = L['n']
    s = E.s
    S = np.zeros((NSH, 4), np.float32); D = np.zeros((NSH, 4), np.float32); Q = np.zeros((NSH, 4), np.float32)
    S[:, :2] = 1e6
    k_r = (0.55 + 0.45 * np.clip(L['dist'] / 520.0, 0.0, 1.0))
    disp = L['dirs'] * (op * L['mul'] * k_r)[:, None]
    sc = 1.0 + float(depth) * L['z'] * op / 900.0
    S[:n, :2] = L['P'] * s
    S[:n, 2] = L['bound'] * s                                    # bound in the unbroken frame (q space)
    D[:n, :2] = disp * s
    D[:n, 2] = sc
    D[:n, 3] = L['rot'] * float(spin) * 0.0016 * op
    Q[:n, 1] = L['z'] + 0.001 * np.arange(n)
    Q[:n, 2] = L['fac'] * min(1.0, op / 160.0)
    alive = np.ones(n, bool)
    for i in range(n):                                           # gone once the whole shard is off screen
        cx_, cy_ = L['P'][i] + disp[i]
        r = L['bound'][i] * sc[i]
        alive[i] = (-r < cx_ < W + r) and (-r < cy_ < H + r)
    Q[:n, 3] = alive.astype(np.float32)
    lay = run(E, SHAT2_FS, {'img': img}, {'n': n, 'refr': float(refract), 'bw': 10.0 * s, 'fk': float(facet)},
              arrays={'S': S.flatten(), 'D': D.flatten(), 'Q': Q.flatten()})
    if nxt is None:                                             # behind the shards: the same frame, deeper (darker, softer, nearer
        base = E.multiply(E.blur(E.affine(img, 1.10, 0.0, None, (0.0, 0.0)), 14.0), E.solid((0.42, 0.42, 0.42)))  # no edge shows)
    else:
        base = nxt
    if shadow > 0:
        sh = E.blur(E.affine(lay, 1.0, 0.0, None, (10.0, 16.0)), 22.0)
        base = run(E, SHADE_FS, {'a': base, 's': sh}, {'k': float(shadow)})
    return E.over(base, lay)
