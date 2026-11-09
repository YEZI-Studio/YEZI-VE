# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""kit design (the useful parts of the CHROMA design kit + v1 crop marks / tick ring). GLSL elements draw IN PLACE onto
`dst` (premultiplied over); skia elements draw onto a Canvas `cv` (1080p units). Angles in degrees, 0 = right, 90 = down.
Dropped on purpose: barcodes, dot grids / dot rows, long black bars, black slabs, gradient discs."""
import math
import numpy as np
import skia
from src.engine.canvas import paint
from src.kit import type as TY
from src.kit.core import run, ramp_tex, PAPER, INK, WHITE, W, H, clamp01, rr
from src.engine.core import PREMUL

TAU = 2 * math.pi


def dummy(E):
    return E.persistent(('dummy4',), lambda: E.texture_from(np.zeros((4, 4, 4), np.float32)))


# ------------------------------------------------------------------ fan / polygon windows (GLSL, in place)
FAN_FS = '''uniform sampler2D ramp; uniform sampler2D content; uniform int use_c; uniform vec2 c; uniform float a0; uniform float a1;
uniform float r0; uniform float r1; uniform float k; uniform float fall; uniform vec3 lc; uniform float lw; uniform float gamma;
void main(){ vec2 d = gl_FragCoord.xy - c; float r = length(d); float ang = atan(d.y, d.x);
  float mid = 0.5 * (a0 + a1), hw = 0.5 * (a1 - a0);
  float da = mod(ang - mid + 3.14159265, 6.28318531) - 3.14159265;
  float side = (hw - abs(da)) * max(r, 1.0); float rad = min(r - r0, r1 - r); float sd = min(side, rad);
  float m = clamp(sd + 0.5, 0.0, 1.0);
  vec4 col;
  if (use_c == 1) col = texture(content, UV());
  else { float u = clamp((da + hw) / (2.0 * hw), 0.0, 1.0); col = vec4(texture(ramp, vec2(u, 0.5)).rgb, 1.0); }
  float fa = mix(1.0, pow(clamp((r1 - r) / max(r1 - r0, 1.0), 0.0, 1.0), gamma), fall);
  vec4 res = col * m * k * fa;
  if (lw > 0.0){ float l = clamp(lw * 0.5 - abs(sd) + 0.5, 0.0, 1.0) * step(-lw, sd) * k; res = res * (1.0 - l) + vec4(lc, 1.0) * l; }
  o = res; }'''


def fan(E, dst, cx, cy, a0, a1, r0=0.0, r1=2400.0, k=1.0, content=None, ramp='heat', fall=0.0, gamma=1.0, line=None, lw=1.2):
    """sector (wedge / cone) from apex (cx, cy) between angles a0..a1 deg, radii r0..r1. Fill: a ramp across the angle,
    or `content` (premultiplied texture in screen space). fall>0 fades toward r1; line=(rgb) draws the outline."""
    if a1 <= a0 or k <= 0:
        return dst
    return run(E, FAN_FS, {'ramp': ramp_tex(E, ramp), 'content': content if content is not None else dummy(E)},
               {'use_c': int(content is not None), 'c': (float(cx), float(cy)), 'a0': math.radians(a0), 'a1': math.radians(a1),
                'r0': float(r0), 'r1': float(r1), 'k': float(k), 'fall': float(fall), 'gamma': float(gamma),
                'lc': tuple(line or (0, 0, 0)), 'lw': float(lw if line else 0.0)}, out=dst, blend=PREMUL)


POLY_FS = '''uniform sampler2D content; uniform sampler2D ramp; uniform vec2 P[8]; uniform int np; uniform float k; uniform vec3 lc;
uniform float lw; uniform int mode; uniform vec2 g0; uniform vec2 g1; uniform vec3 fc0; uniform vec3 fc1; uniform float a0; uniform float a1;
void main(){ vec2 p = gl_FragCoord.xy; float dmin = 1e9;
  for (int i = 0; i < 8; i++){ if (i >= np) break; vec2 a = P[i]; vec2 b = P[(i + 1) % np]; vec2 e = b - a;
    vec2 nrm = normalize(vec2(-e.y, e.x)); dmin = min(dmin, dot(p - a, nrm)); }
  float m = clamp(dmin + 0.5, 0.0, 1.0);
  vec2 g = g1 - g0; float u = clamp(dot(p - g0, g) / max(dot(g, g), 1e-6), 0.0, 1.0);
  vec4 col;
  if (mode == 0) col = texture(content, UV());
  else if (mode == 1) col = vec4(mix(fc0, fc1, u), 1.0) * mix(a0, a1, u);
  else col = vec4(texture(ramp, vec2(u, 0.5)).rgb, 1.0) * mix(a0, a1, u);
  vec4 res = col * m * k;
  if (lw > 0.0){ float l = clamp(lw * 0.5 - abs(dmin) + 0.5, 0.0, 1.0) * k; res = res * (1.0 - l) + vec4(lc, 1.0) * l; }
  o = res; }'''


def poly(E, dst, pts, content=None, k=1.0, line=None, lw=1.2, grad=None, ramp=None):
    """convex polygon window (<= 8 points, 1080p px). Fill: content texture (screen space) | grad=(g0, g1, c0, c1, a0, a1)
    linear gradient | ramp=(name, g0, g1, a0, a1) ramp along g0->g1."""
    pts = [tuple(map(float, p)) for p in pts][:8]
    area = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
    if area < 0:
        pts = pts[::-1]
    arr = np.zeros(16, np.float32); arr[:2 * len(pts)] = np.array(pts, np.float32).flatten()
    mode, g0, g1, c0, c1, a0, a1, rname = 0, (0, 0), (1, 0), (0, 0, 0), (0, 0, 0), 1.0, 1.0, 'ink_paper'
    if grad is not None:
        mode = 1; g0, g1, c0, c1, a0, a1 = grad
    elif ramp is not None:
        mode = 2; rname, g0, g1, a0, a1 = ramp
    return run(E, POLY_FS, {'content': content if content is not None else dummy(E), 'ramp': ramp_tex(E, rname)},
               {'np': len(pts), 'k': float(k), 'lc': tuple(line or (0, 0, 0)), 'lw': float(lw if line else 0.0), 'mode': mode,
                'g0': tuple(map(float, g0)), 'g1': tuple(map(float, g1)), 'fc0': tuple(c0), 'fc1': tuple(c1),
                'a0': float(a0), 'a1': float(a1)}, out=dst, blend=PREMUL, arrays={'P': arr})


# ------------------------------------------------------------------ skia: lines that draw themselves
def spline_path(pts, closed=False, tension=0.5):
    """Catmull-Rom through pts -> skia.Path of cubics"""
    P = [tuple(map(float, p)) for p in pts]
    path = skia.Path(); path.moveTo(*P[0])
    n = len(P)
    for i in range(n if closed else n - 1):
        p0 = P[(i - 1) % n] if (closed or i > 0) else P[i]
        p1 = P[i]; p2 = P[(i + 1) % n]
        p3 = P[(i + 2) % n] if (closed or i + 2 < n) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) * tension / 3, p1[1] + (p2[1] - p0[1]) * tension / 3)
        c2 = (p2[0] - (p3[0] - p1[0]) * tension / 3, p2[1] - (p3[1] - p1[1]) * tension / 3)
        path.cubicTo(*c1, *c2, *p2)
    return path


def draw_path(cv, path, a=0.0, b=1.0, color=WHITE, alpha=1.0, width=1.4, cap='round', dash=None):
    """stroke the [a, b] fraction of `path` (trim path -> 'draws itself' when b animates, erases when a animates)"""
    a, b = clamp01(a), clamp01(b)
    if b - a <= 1e-4 or alpha <= 0:
        return
    pm = skia.PathMeasure(path, False, 1.0)
    L = pm.getLength()
    seg = skia.Path()
    if a <= 0 and b >= 1:
        seg = path
    else:
        pm.getSegment(a * L, b * L, seg, True)
    cv.c.drawPath(seg, paint(color, alpha, width, cap, dash, join='round'))


# ------------------------------------------------------------------ skia: marks, scales, labels
def crop_marks(cv, color=WHITE, alpha=1.0, inset=36.0, arm=22.0, width=1.4):
    """four corner marks (CHROMA v1): 36 px from the edge, 22 px arms, pixel-snapped"""
    p = paint(color, alpha, width)
    for x, y, sx, sy in ((inset, inset, 1, 1), (W - inset, inset, -1, 1), (inset, H - inset, 1, -1), (W - inset, H - inset, -1, -1)):
        x, y = round(x) + 0.5 * sx * 0, round(y)
        cv.c.drawLine(x, y, x + sx * arm, y, p); cv.c.drawLine(x, y, x, y + sy * arm, p)


def crosshair(cv, x, y, s=26.0, color=WHITE, alpha=1.0, width=1.4, ring=True, gap=5.0):
    p = paint(color, alpha, width)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        cv.c.drawLine(x + dx * gap, y + dy * gap, x + dx * s, y + dy * s, p)
    if ring:
        cv.c.drawCircle(x, y, s * 0.55, p)


def plus(cv, x, y, s=7.0, color=WHITE, alpha=1.0, width=1.6):
    p = paint(color, alpha, width)
    cv.c.drawLine(x - s, y, x + s, y, p); cv.c.drawLine(x, y - s, x, y + s, p)


def ruler(cv, x, y, length, n=24, major=6, color=WHITE, alpha=1.0, p=1.0, vertical=False, tick=8.0, width=1.4):
    """scale ticks drawn on by p (detail layer: temperature scales, gauges)"""
    m = int(n * clamp01(p))
    pp = paint(color, alpha, width)
    for i in range(m + 1):
        t = tick * (1.9 if i % major == 0 else 1.0)
        if vertical:
            yy = y + length * i / n; cv.c.drawLine(x, yy, x + t, yy, pp)
        else:
            xx = x + length * i / n; cv.c.drawLine(xx, y, xx, y - t, pp)


def tick_ring(cv, cx, cy, r, p=1.0, ticks=120, major=10, color=WHITE, alpha=1.0, width=1.4, tick=10.0, a0=-90.0, ring=True):
    """ring + ticks drawn on clockwise from a0 (dial / gauge)"""
    p = clamp01(p)
    if p <= 0:
        return
    if ring:
        cv.arc(cx, cy, r, a0, 360.0 * p, color, alpha, width)
    pp = paint(color, alpha, width)
    for i in range(int(ticks * p) + 1):
        a = math.radians(a0 + 360.0 * i / ticks)
        L = tick * (2.0 if i % major == 0 else 1.0)
        cv.c.drawLine(cx + r * math.cos(a), cy + r * math.sin(a), cx + (r - L) * math.cos(a), cy + (r - L) * math.sin(a), pp)


def big_arc(cv, cx, cy, r, a0, sweep, p=1.0, color=WHITE, alpha=1.0, width=1.4):
    """thin large circle arc drawn on (p)"""
    cv.arc(cx, cy, r, a0, sweep * clamp01(p), color, alpha, width)


def tag_block(cv, x, y, lines, t=1e9, cps=3.0, size=22.0, lead=1.45, color=WHITE, alpha=1.0, bar=True, align='left',
              caps=True):
    """stacked mono labels typed on at cps chars/frame (t = frames since start); small bar before the first line.
    caps=False keeps the strings as given (series.caps() upper-cases labels but keeps units like kV)"""
    n_total = 0
    for i, s in enumerate(lines):
        vis = s[:max(0, min(len(s), int(t * cps) - n_total))]
        n_total += len(s)
        if vis:
            TY.word(cv, vis, x, y + i * size * lead, size, 'mono', color, alpha, align, caps=caps)
    if bar and t > 0:
        ch = TY.cap_height('mono', max(size, TY.MONO_MIN))
        bx = x - 16 if align == 'left' else x + 8
        cv.c.drawRect(skia.Rect.MakeXYWH(bx, y - ch, 6, ch), paint(color, alpha))


def giant(cv, s, x, y, size=600.0, key='wide', color=WHITE, alpha=1.0, stroke=None, tracking=None, align='left', rot=0.0):
    """oversized type bleeding off frame (fill or outline). rot about (x, y) in deg"""
    if rot:
        cv.c.save(); cv.c.rotate(rot, x, y)
    TY.word(cv, s, x, y, size, key, color, alpha, align, tracking, stroke=stroke)
    if rot:
        cv.c.restore()


def contours(cv, cx, cy, r0, dr, n=6, seed=0, phase=0.0, color=WHITE, alpha=0.85, width=1.2, p=1.0, sq=0.8, amp=0.16):
    """isotherm-like wobbly concentric closed curves, drawn on by p; phase animates the wobble"""
    m = int(math.ceil(n * clamp01(p)))
    for i in range(m):
        r = r0 + i * dr
        pts = []
        for j in range(72):
            a = j / 72 * TAU
            w = 1 + amp * (math.sin(3 * a + rr(0, 6, seed, i) + phase) * 0.6 + math.sin(5 * a + rr(0, 6, seed, i, 'b') - phase * 0.7) * 0.4)
            pts.append((cx + math.cos(a) * r * w, cy + math.sin(a) * r * w * sq))
        frac = clamp01(n * clamp01(p) - i)
        draw_path(cv, spline_path(pts, closed=True), 0, frac, color, alpha, width)
