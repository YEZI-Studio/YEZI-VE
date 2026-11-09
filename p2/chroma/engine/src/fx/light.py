# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""LT.integrate: put an added layer INTO the plate instead of on top of it -- three things:

  1. the layer gets the plate's own softness (its equivalent Gaussian blur, measured on the plate's edges) -- CG is sharper
     than any footage, a razor edge on a soft plate reads as a sticker;
  2. the layer gets the plate's own grain (pixel noise measured on the plate's flat areas), only where it covers;
  3. its light spills: an energy-conserving multi-scale bloom of the layer's light (VOLT's look-v4 bloom, tinted in the
     signature colour), and -- off by default -- a fill light on the plate's bright medium around it (VOLT's flash()).

Lifted from fx/volt.py (bloom :544, plate_stats :566, flash :596) without the VOLT colour grading. Defaults are conservative:

    from src.fx import light as LT
    if w3 is not None:
        img = LT.integrate(E, img, w3)                       # instead of E.over(img, w3)
    img = LT.integrate(E, img, lay, blend='screen', bloom=0.3) # a light-only layer (img=None from SR.element)"""
import math
from src.kit.core import run
from src.fx import volt as VT

GRAIN_FS = '''uniform sampler2D a; uniform float nz; uniform float seed;
float h(vec2 p){ return fract(sin(dot(p, vec2(12.9898, 78.233)) + seed) * 43758.5453); }
void main(){ vec4 c = texture(a, UV()); vec2 p = floor(gl_FragCoord.xy);
  float g = (h(p) + h(p + 17.31) + h(p + 41.7) - 1.5) * 1.414;            // ~unit-variance, cheap triangular-ish noise
  vec3 rgb = c.rgb + vec3(g * nz) * c.a;                                  // premultiplied: the grain lives where the layer is
  o = vec4(clamp(rgb, 0.0, c.a), c.a); }'''
LIN_FS = '''uniform sampler2D a; uniform float k;
void main(){ vec4 c = texture(a, UV()); o = vec4(lin(c.rgb) * k, 1.0); }'''
SCREEN_FS = '''uniform sampler2D a; uniform sampler2D b;
void main(){ vec2 uv = UV(); vec3 A = texture(a, uv).rgb; vec3 B = texture(b, uv).rgb;
  o = vec4(1.0 - (1.0 - A) * (1.0 - B), 1.0); }'''


def _tints():
    from src.fx import motif as MO
    P = MO.current()
    if P is None:
        return None
    return (P['core'], P['pale'], P['pale'], P['acc'], P['acc'], P['deep'])


def integrate(E, plate, layer, k=1.0, blend='over', soft=1.0, grain=1.0, bloom=0.22, fill=0.0, seed=None, tints=None):
    """plate = the opaque picture under the layer; layer = premultiplied RGBA (blend 'over') or light (blend 'screen' / 'add',
    alpha ignored). soft x the plate's measured softness given to the layer (0 = keep it sharp); grain x the plate's noise;
    bloom = energy-conserving glow of the layer's light (0.15-0.35); fill = flash fill of the plate's bright medium from the
    layer's light (0 = off; 0.3-0.6 for a light source); tints = 6 colours inner -> outer (default the signature colour).
    Returns the composite. Deterministic per frame (the grain seed = E.frame_id unless given)."""
    if layer is None:
        return plate
    s_px, noise = VT.plate_stats(E, plate)
    lay = layer
    if soft > 0:
        sg = math.sqrt(max(s_px * s_px - 0.6 * 0.6, 0.0)) * float(soft)      # CG ~0.6 px of its own anti-aliasing
        if sg > 0.3:
            lay = E.blur(lay, sg, fast=False)
    if grain > 0:
        sd = float(getattr(E, 'frame_id', 0) * 0.6180339 % 97.0) if seed is None else float(seed)
        lay = run(E, GRAIN_FS, {'a': lay}, {'nz': float(noise) * float(grain), 'seed': sd})
    if blend == 'over':
        out = E.over(plate, lay, k)
    elif blend == 'screen':
        out = run(E, SCREEN_FS, {'a': plate, 'b': lay if k == 1.0 else E.multiply(lay, E.solid((k, k, k)))})
    else:
        out = E.add(plate, lay, k)
    if bloom > 0 or fill > 0:
        src = run(E, LIN_FS, {'a': lay}, {'k': float(k)})
        tt = tints or _tints()
        if bloom > 0:
            out = VT.bloom(E, out, src, amount=float(bloom), s0=2.5, r=2.4, q=0.55, **({'tints': tt} if tt else {}))
        if fill > 0:
            out = VT.flash(E, out, src, k=float(fill), medium=plate, tint=(tt[3] if tt else 'ICE'), tsat=0.35)
    return out
