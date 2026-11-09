# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""VOLT impact clusters (IGNITION's engine re-coloured for electricity: ink / electric violet / paper).

A cluster is SLAMMED, not flashed:
  charge  2-4 f before F: push-in + radial blur + darken (edges more) + desaturate + focus lines closing on the centre
          (the lines invert what is under them, so they read on black and on white alike)
  impact  1-3 f from F, broken from both neighbours; the 2nd frame is a variant (+3.5 % snap per frame):
          'neg'     THE style of this film: a negative -- the bolt turns into INK branches on PAPER, its glow into a thin
                    violet-grey band (adaptive: reads at any exposure; never colour-inverts violet into yellow);
                    2nd: 1-bit negative + ink focus lines
          'duo'     ink + electric violet (ACCENT) two-tone (adaptive threshold, subject only); 2nd: colours swapped
          'post3'   ink / ACCENT / paper 3-tone posterize of the SUBJECT (the accent tone only around the paper areas,
                    no stray blobs); 2nd: + ink focus lines
          'lines'   black & white high contrast + dense radial focus lines; 2nd: inverted
          'half'    halftone on a plain INK ground: log-polar dots sized by the subject's BRIGHTNESS (ICE dots; 2nd: ACCENT)
          'plasma'  plasma false colour (ramp 'plasma', replaces IGNITION's thermal; 'thermal' still accepted);
                    2nd: iso-brightness bands + focus lines
          + SHATTER (Voronoi shards, ARC_WHITE crack lines; 'neg': no lines, only the bolt breaks; aftermath: shards heal
          without lines) -- OFF by default: only the two biggest hits of the film
          (432 DROP, 1584 last hit) pass Hit(shatter=1.0)
  after   4-10 f: shards settle back, SOFT shock ring (refraction + faint wide ICE glow, no edge line), ELECTRIC ARC
          AFTERGLOW (brief Lichtenberg flashes out of the centre, fx.discharge -- no sparks in this film), decaying shake with
          motion blur, zoom rebound, RGB aftershock.
Use (one line at the end of a shot):
    HITS = [IM.Hit(TL.F(4, 0), 'neg', c=(960, 540), shatter=1.0), IM.Hit(TL.F(5, 2), 'duo', k=0.8)]
    return IM.post(E, T, img, HITS)
Charge / impact are keyed to the integer frame T.F (crisp); the aftermath moves with T.f (sub-frames give motion blur).
impact_frames(HITS) lists the impact frames (for the 有音无画 check)."""
import math
import numpy as np
import skia
from src.engine.canvas import paint
from src.kit.core import run, ramp_tex, INK, PAPER, WHITE, rr, clamp01, lerp, eo3, ei3
from src.kit import fx
from src.fx import volt as VT

STYLES = ('duo', 'post3', 'neg', 'lines', 'half', 'plasma')
ALIAS = {'thermal': 'plasma'}
TAU = 2 * math.pi


class Hit:
    """F = first impact frame (int film frame, e.g. TL.F(bar, beat, sub)); style in STYLES; c = centre (1080p px) or
    callable(F) -> (x, y); k = strength 0..1.5; ch / n / af = charge / impact / aftermath frames; col = the accent colour
    (default VT.ACCENT); shake = shake amplitude scale; arcs = electric-arc afterglow flashes (0 = none);
    shatter = Voronoi shards (0 by default -- only the two biggest hits of the film)."""

    def __init__(self, F, style='neg', c=(960.0, 540.0), k=1.0, ch=3, n=2, af=8, seed=None, col=None, shake=1.0,
                 arcs=3, ring=True, dark=0.30, shatter=0.0, sparks=None):
        style = ALIAS.get(style, style)
        assert style in STYLES, style
        self.F, self.style, self.c, self.k = int(F), style, c, float(k)
        self.ch, self.n, self.af = int(ch), int(n), int(af)
        self.seed = int(F) * 7 + 3 if seed is None else int(seed)
        self.col = tuple(col) if col else VT.ACCENT
        self.arcs = int(arcs if sparks is None else min(4, sparks))
        self.shake, self.ring, self.dark, self.shatter = shake, ring, dark, float(shatter)

    def phase(self, F):
        lf = F - self.F
        if -self.ch <= lf < 0:
            return 'charge', (lf + self.ch + 1) / float(self.ch)
        if 0 <= lf < self.n:
            return 'impact', lf
        if self.n <= lf < self.n + self.af:
            return 'after', lf - self.n
        return None, 0

    def center(self, F):
        return self.c(F) if callable(self.c) else self.c

    def span(self):
        return self.F - self.ch, self.F + self.n + self.af


def impact_frames(hits):
    return sorted({h.F + j for h in hits for j in range(h.n)})


# ------------------------------------------------------------------ shaders
CHARGE_FS = '''uniform sampler2D a; uniform vec2 c; uniform float z; uniform float blur; uniform float dark; uniform float desat;
uniform int n;
void main(){ vec2 p = gl_FragCoord.xy; vec3 acc = vec3(0.0);
  for (int i = 0; i < 16; i++){ if (i >= n) break; float t = (float(i) + 0.5) / float(n);
    float zz = z * (1.0 + blur * t); vec2 q = c + (p - c) / zz;
    acc += lin(texture(a, clamp(q, vec2(0.5), u_size - 0.5) / u_size).rgb); }
  vec3 col = enc(acc / float(n)); col = mix(col, vec3(luma(col)), desat);
  float r = length(p - c) / u_size.y; col *= 1.0 - dark * (0.45 + 0.55 * smoothstep(0.10, 0.85, r));
  o = vec4(col, 1.0); }'''

INVW_FS = '''uniform sampler2D a; uniform sampler2D m; uniform float k;
void main(){ vec4 c = texture(a, UV()); float mm = clamp(texture(m, UV()).a * k, 0.0, 1.0);
  o = vec4(mix(c.rgb, vec3(1.0) - c.rgb, mm), 1.0); }'''

# adaptive value: luma vs its neighbourhood (two rings) -> silhouettes read at any exposure
ADAPT_GLSL = '''float Lp(vec2 p){ return luma(texture(a, clamp(p, vec2(0.5), u_size - 0.5) / u_size).rgb); }
float adapt(vec2 p){ float l = Lp(p); float m = 0.0;
  for (int i = 0; i < 12; i++){ float an = float(i) * 0.5236;
    m += Lp(p + vec2(cos(an), sin(an)) * 26.0 * u_s) + Lp(p + vec2(cos(an + 0.26), sin(an + 0.26)) * 72.0 * u_s); }
  m /= 24.0; return clamp(0.5 + 1.5 * (l - m) + 0.6 * (l - 0.5), 0.0, 1.0); }
'''
STYLE_FS = 'uniform sampler2D a;\n' + ADAPT_GLSL + '''uniform sampler2D ramp; uniform sampler2D nramp; uniform int mode; uniform int j; uniform vec3 ink;
uniform vec3 col; uniform vec3 pap; uniform float var; uniform float bands;
void main(){ vec2 p = gl_FragCoord.xy; vec4 t4 = texture(a, UV()); vec3 c = t4.rgb; float r = var > 0.0 ? t4.a : 0.5; vec3 res;
  float sw = step(1.0 - 0.3 * var, r);                       // ~30 % of the shards flip
  float off = (r - 0.5) * 0.55 * var;                         // per-shard threshold offset: the frame breaks up
  if (mode == 0){ float v = smoothstep(0.47 + off, 0.53 + off, adapt(p)); bool s = (j % 2 == 1) != (sw > 0.5);
    res = s ? mix(col, ink, v) : mix(ink, col, v); }
  else if (mode == 1){ float v = adapt(p); if (sw > 0.5 && r > 0.9) v = 1.0 - v; float d = 0.06 * var;
    res = mix(ink, col, smoothstep(0.38 + off - d, 0.42 + off - d, v)); res = mix(res, pap, smoothstep(0.61 + off, 0.65 + off, v)); }
  else if (mode == 2){                                        // negative: light -> ink branches on paper
    float y = luma(c); float m9 = 0.0;
    for (int i = 0; i < 12; i++){ float an = float(i) * 0.5236; m9 += Lp(p + vec2(cos(an), sin(an)) * 9.0 * u_s); }
    float rd = y - m9 / 12.0;                                  // the channel: brighter than its own 9 px ring
    float glow = 0.46 * pow(smoothstep(0.12, 1.0, y), 1.3);    // lit cloud / halo -> a pale violet-grey wash
    float chn = smoothstep(0.04, 0.20, rd) * smoothstep(0.40, 0.85, y);
    float v = clamp(max(glow, chn) + 0.6 * off, 0.0, 1.0);
    if (j % 2 == 0) res = texture(nramp, vec2(v, 0.5)).rgb;
    else res = mix(pap, ink, smoothstep(0.45, 0.55, chn + 0.6 * glow + off)); }
  else if (mode == 3){ float y = clamp((luma(c) - 0.22) / 0.48, 0.0, 1.0); y = mix(y, y * y * (3.0 - 2.0 * y), 0.5);
    res = mix(ink, vec3(1.0), y); if ((j % 2 == 1) != (sw > 0.5)) res = 1.0 - res; }
  else { float y = clamp((luma(c) - 0.5) * 1.35 + 0.5 + off * 0.6, 0.0, 1.0); y = mix(y, y * y * (3.0 - 2.0 * y), 0.35);
    if (bands > 0.5) y = (floor(y * bands) + 0.5) / bands; res = texture(ramp, vec2(y, 0.5)).rgb; }
  o = vec4(res, 1.0); }'''

# post3: pass A = paper / mid masks (adaptive, per-shard thresholds); pass B keeps the accent tone only
# around the subject (the paper areas, blurred) and drops paper specks -> no stray violet blobs from posterised cloud
P3A_FS = 'uniform sampler2D a;\n' + ADAPT_GLSL + '''uniform float var; uniform int mode;
void main(){ vec2 p = gl_FragCoord.xy; vec4 t4 = texture(a, UV()); float r = var > 0.0 ? t4.a : 0.5;
  float sw = step(1.0 - 0.3 * var, r); float off = (r - 0.5) * 0.55 * var; float d = 0.06 * var;
  float v = adapt(p); if (mode == 1 && sw > 0.5 && r > 0.9) v = 1.0 - v;
  float mid = mode == 1 ? smoothstep(0.38 + off - d, 0.42 + off - d, v) : smoothstep(0.47 + off, 0.53 + off, v);
  o = vec4(smoothstep(0.61 + off, 0.65 + off, v), mid, sw, 1.0); }'''
P3B_FS = '''uniform sampler2D a; uniform sampler2D n6; uniform sampler2D n30; uniform vec3 ink; uniform vec3 col; uniform vec3 pap;
uniform int mode; uniform int j;
void main(){ vec2 uv = UV(); vec3 m = texture(a, uv).rgb;
  float keep = smoothstep(0.035, 0.09, texture(n6, uv).r);        // paper specks smaller than ~4 px go
  float near = smoothstep(0.025, 0.09, texture(n30, uv).r);       // the second tone only within ~50 px of the subject
  vec3 res;
  if (mode == 1){ res = mix(ink, col, m.g * near); res = mix(res, pap, m.r * keep); }
  else { bool s = (j % 2 == 1) != (m.b > 0.5); float v = m.g * near; res = s ? mix(col, ink, v) : mix(ink, col, v); }
  o = vec4(res, 1.0); }'''


def post3(E, img, var, col, mode=1, j=0):
    """post3 (mode 1) / duo (mode 0) restricted to the subject: no stray blobs from posterised cloud"""
    A = run(E, P3A_FS, {'a': img}, {'var': float(var), 'mode': int(mode)})
    return run(E, P3B_FS, {'a': A, 'n6': E.blur(A, 6.0), 'n30': E.blur(A, 30.0)},
               {'ink': INK, 'col': tuple(col), 'pap': PAPER, 'mode': int(mode), 'j': int(j)})


# Voronoi shatter (CHROMA x_cape_impact CRACK_FS): shards pushed out of c and turned, crack lines; alpha = shard random
SHATTER_FS = '''uniform sampler2D img; uniform vec4 S[64]; uniform float R[64]; uniform int n; uniform float lw; uniform vec3 lc;
uniform vec3 ec; uniform float lk; uniform float facet; uniform float gw;
void main(){ vec2 p = gl_FragCoord.xy; float d1 = 1e9, d2 = 1e9; int i1 = 0, i2 = 0;
  for (int i = 0; i < 64; i++){ if (i >= n) break; float d = distance(p, S[i].xy);
    if (d < d1){ d2 = d1; i2 = i1; d1 = d; i1 = i; } else if (d < d2){ d2 = d; i2 = i; } }
  vec2 s1 = S[i1].xy, s2 = S[i2].xy;
  float e = (d2 * d2 - d1 * d1) / (2.0 * max(distance(s1, s2), 1e-3));
  float ang = R[i1]; float cs = cos(ang), sn = sin(ang);
  vec2 q = p - S[i1].zw - s1; q = vec2(cs * q.x + sn * q.y, -sn * q.x + cs * q.y) + s1;
  vec3 col = texture(img, clamp(q, vec2(0.5), u_size - 0.5) / u_size).rgb;
  float h = fract(sin(float(i1) * 12.9898 + 4.1) * 43758.5453);
  col = clamp(col * (1.0 + facet * (h - 0.5)), 0.0, 1.0);
  float line = 1.0 - smoothstep(lw * 0.5, lw * 0.5 + 1.1, e);
  float gap = 1.0 - smoothstep(gw, gw + 1.2, e);
  float sh = exp(-max(e - gw, 0.0) / 9.0);
  col = mix(col, ec, clamp(gap + sh * 0.45, 0.0, 1.0) * lk);
  col = mix(col, lc, line * lk);
  o = vec4(col, h); }'''
NMAX = 64


def _seeds(cx, cy, seed):
    import numpy as _np
    rng = _np.random.default_rng(int(seed) & 0x7FFFFFFF)
    pts = [(cx + rng.uniform(-12, 12), cy + rng.uniform(-12, 12))]
    for r, m in ((110, 6), (300, 8), (560, 10), (900, 12), (1350, 12), (1900, 12)):
        a0 = rng.uniform(0, TAU)
        for k in range(m):
            a = a0 + TAU * (k + rng.uniform(-0.3, 0.3)) / m
            rr_ = r * rng.uniform(0.82, 1.18)
            pts.append((cx + rr_ * math.cos(a), cy + rr_ * math.sin(a)))
    P = _np.array(pts[:NMAX], _np.float64)
    return P, rng.uniform(0.5, 1.4, len(P)), rng.uniform(-1.0, 1.0, len(P))


def shatter(E, img, cx, cy, seed, open_, lk=None, lw=2.4, gw=5.0, lc=None, ec=None, facet=None, seeds=None):
    """break the frame into ~60 radial Voronoi shards (pushed out by open_ px, turned), ARC_WHITE crack lines with ink gaps
    (lc / ec override: 'neg' passes INK for both, so the cracks vanish into the paper and only the broken bolt shows).
    p2 11-05: lk None = no crack lines (0) unless timeline.SERIES_FX == 'volt' (no thin lines); facet = per-shard light
    variation (default 0.36 x lk); seeds = (P, mul, rot) to use instead of the default radial rings (SR.shatter)."""
    if lk is None:
        from src.fx import motif as MO
        lk = 1.0 if MO.series_fx() == 'volt' else 0.0
    P, mul, rot = _seeds(round(cx), round(cy), seed) if seeds is None else seeds
    v = P - np.array([cx, cy]); d = np.hypot(v[:, 0], v[:, 1]) + 1e-3
    disp = v / d[:, None] * (open_ * mul * (0.35 + 0.65 * np.clip(d / 500.0, 0, 1)))[:, None]
    S = np.zeros((NMAX, 4), np.float32); S[:len(P), :2] = P * E.s; S[:len(P), 2:] = disp * E.s
    S[len(P):, :2] = 1e6
    Rr = np.zeros(NMAX, np.float32); Rr[:len(P)] = rot * 0.0013 * open_
    return run(E, SHATTER_FS, {'img': img}, {'n': len(P), 'lw': float(lw * E.s), 'lc': tuple(lc or VT.ARC_WHITE), 'ec': tuple(ec or INK),
                                             'lk': float(lk), 'facet': 0.36 * float(lk) if facet is None else float(facet),
                                             'gw': float(gw * E.s)},
               arrays={'S': S.flatten(), 'R': Rr})


HALF_FS = '''uniform sampler2D a; uniform sampler2D bl; uniform vec2 c; uniform float s; uniform float D; uniform vec3 bg;
uniform vec3 ink; uniform float gain; uniform float rot; uniform int inv;
float dotcov(vec2 v, float i, float th){
  float rr = D * (exp(i * s / D) - 1.0); float sp = s * exp(i * s / D);
  float n = max(1.0, floor(6.2831853 * rr / sp)); float st = 6.2831853 / n; float off = mod(i, 2.0) * 0.5;
  float jj = floor((th + rot) / st - off + 0.5); float ang = (jj + off) * st - rot;
  vec2 dc = rr * vec2(cos(ang), sin(ang));
  vec2 uv = clamp(c + dc, vec2(0.5), u_size - 0.5) / u_size;
  float L = luma(texture(a, uv).rgb); L += 1.4 * (L - luma(texture(bl, uv).rgb));
  float dk = inv == 1 ? smoothstep(0.30, 0.85, L) : smoothstep(0.30, 0.82, 1.0 - L);
  float r = sp * 0.64 * pow(clamp(dk * gain, 0.0, 1.0), 0.8) * mix(0.55, 1.12, smoothstep(0.0, 950.0 * u_s, rr));
  return clamp(r - length(v - dc) + 0.5, 0.0, 1.0); }
void main(){ vec2 v = gl_FragCoord.xy - c; float d = length(v); float th = atan(v.y, v.x);
  float u = D * log(1.0 + d / D); float i0 = floor(u / s + 0.5); float cov = 0.0;
  for (int k = -1; k <= 1; k++){ float i = i0 + float(k); if (i < 0.0) continue; cov = max(cov, dotcov(v, i, th)); }
  o = vec4(mix(bg, ink, cov), 1.0); }   // plain ink ground (no radial wedges)'''

SHOCK_FS = '''uniform sampler2D a; uniform vec2 c; uniform float r; uniform float w; uniform float amp; uniform float rim; uniform float ca;
uniform vec3 rc; uniform vec3 t1; uniform vec3 t2;
void main(){ vec2 p = gl_FragCoord.xy; vec2 v = p - c; float d = length(v); vec2 nn = d > 1e-3 ? v / d : vec2(0.0);
  float x = (d - r) / w; float g = exp(-x * x); vec2 q = p - nn * (amp * 2.0 * x * g);   // a soft refractive lens ring
  vec3 c0 = texture(a, clamp(q, vec2(0.5), u_size - 0.5) / u_size).rgb; float l0 = luma(c0);
  float l1 = luma(texture(a, clamp(q - nn * ca * g, vec2(0.5), u_size - 0.5) / u_size).rgb);
  float l2 = luma(texture(a, clamp(q + nn * ca * g, vec2(0.5), u_size - 0.5) / u_size).rgb);
  vec3 col = lin(clamp(c0 + t1 * max(l1 - l0, 0.0) + t2 * max(l2 - l0, 0.0), 0.0, 1.0));   // cool dispersion only
  col += lin(rc) * rim * g * smoothstep(0.0, 1.2, (d - r) / w + 1.2);   // faint WIDE glow on the ring, soft both sides
  o = vec4(enc(min(col, vec3(1.0))), 1.0); }'''

COOLSPLIT_FS = '''uniform sampler2D a; uniform vec2 c; uniform float amt; uniform vec2 dd; uniform vec3 t1; uniform vec3 t2;
void main(){ vec2 p = gl_FragCoord.xy; vec2 v = p - c;
  vec3 c0 = texture(a, p / u_size).rgb; float l0 = luma(c0);
  float l1 = luma(texture(a, clamp(c + v * (1.0 + amt) + dd, vec2(0.5), u_size - 0.5) / u_size).rgb);
  float l2 = luma(texture(a, clamp(c + v * (1.0 - amt) - dd, vec2(0.5), u_size - 0.5) / u_size).rgb);
  o = vec4(clamp(c0 + t1 * max(l1 - l0, 0.0) + t2 * max(l2 - l0, 0.0), 0.0, 1.0), 1.0); }'''


def cool_split(E, img, amt, cx, cy, dx=0.0, dy=0.0):
    """chromatic aftershock in this film's colours: the picture's own echo, shifted out (VIOLET) and in (ELECTRIC) --
    an RGB split would throw red / yellow fringes, and warm colours belong to the aurora only"""
    return run(E, COOLSPLIT_FS, {'a': img}, {'c': (float(cx), float(cy)), 'amt': float(amt), 'dd': (float(dx), float(dy)),
                                             't1': VT.VIOLET, 't2': VT.ELECTRIC})


# ------------------------------------------------------------------ vector parts (skia, 1080p units)
def focus_lines(cv, cx, cy, seed, n=120, r_in=380.0, r_out=2600.0, wmin=1.5, wmax=20.0, jitter=0.8, color=WHITE, alpha=1.0):
    """manga focus lines (集中線): n thin wedges from off-screen toward (cx, cy), tips at r_in .. r_in*(1+jitter)"""
    pt = paint(color, alpha)
    for i in range(n):
        a = TAU * (i + rr(-0.48, 0.48, seed, i, 'a')) / n
        ri = r_in * (1 + jitter * rr(0, 1, seed, i, 'r') ** 1.6)
        w = wmin + (wmax - wmin) * rr(0, 1, seed, i, 'w') ** 2.2
        ca, sa = math.cos(a), math.sin(a)
        bx, by = cx + r_out * ca, cy + r_out * sa
        nx, ny = -sa, ca
        q = skia.Path()
        q.moveTo(cx + ri * ca, cy + ri * sa)
        q.lineTo(bx + nx * w / 2, by + ny * w / 2); q.lineTo(bx - nx * w / 2, by - ny * w / 2); q.close()
        cv.c.drawPath(q, pt)


def rings(cv, cx, cy, t, dur, k=1.0):
    """(unused -- no thin lines) thin double shock ring as a mask"""
    for lag, wk in ((0.0, 1.0), (1.7, 0.4)):
        uu = clamp01((t - lag) / dur)
        if 0 < uu < 1:
            r = lerp(70.0, 1500.0, eo3(uu)) * (0.7 + 0.3 * k)
            cv.c.drawCircle(cx, cy, r, paint(WHITE, 1 - uu * uu, lerp(20.0, 1.5, eo3(uu)) * wk))


_ARC_LIB = {}


def _arc_geo(q):
    """a small library of Lichtenberg flashes (geometry at the origin, cached on disk by fx.discharge), re-used at any
    centre / angle / size"""
    if q not in _ARC_LIB:
        from src.fx import discharge as DC
        _ARC_LIB[q] = DC.Discharge('radial', seed=900 + q, p0=(0.0, 0.0), r=260.0, cell=4.5, eta=1.45, n_max=900,
                                   width=1.5, wmin=0.5, inten=4.5, main_k=1.0, grow=1.2, tau_b=1.4, tau_m=1.4, restrike=(),
                                   halo=(0.35, 0.2, 0.08, 0.02))
    return _ARC_LIB[q]


def arcs(E, img, h, t, cx, cy):
    """electric-arc afterglow (replaces IGNITION's sparks): a few brief branching flashes out of the impact centre,
    each fires once (grows ~1 f, dies in ~2 f), smaller and dimmer as the aftermath ends"""
    life = float(h.af)
    for q in range(h.arcs):
        t0 = rr(0.0, 0.55 * life, h.seed, q, 't0')
        lt = t - t0
        if lt < -1.3 or lt > 4.0:
            continue
        D = _arc_geo(q % 4)
        sc = rr(0.6, 1.2, h.seed, q, 's') * (0.75 + 0.25 * h.k) * (1.0 - 0.35 * clamp01(t / life))
        ang = rr(0.0, TAU, h.seed, q, 'a')
        kk = (1.0 - clamp01(t / (life + 2.0))) ** 1.2 * min(1.0, h.k)
        img = D.draw(E, img, lt, k=kk, scale=sc, offset=(cx, cy), rot=ang)
    return img


# ------------------------------------------------------------------ the three phases
def charge(E, img, F, h, u, cx, cy):
    k = clamp01(u) * h.k
    img = run(E, CHARGE_FS, {'a': img}, {'c': (float(cx) * E.s, float(cy) * E.s), 'z': 1.0 + 0.08 * ei3(clamp01(u)) * h.k,
                                         'blur': 0.06 * k, 'dark': min(0.85, h.dark * k), 'desat': min(1.0, 0.25 * k), 'n': 12})
    cv = E.canvas()
    focus_lines(cv, cx, cy, h.seed + F * 7, n=int(50 + 80 * k), r_in=lerp(1050.0, 430.0, eo3(u)), wmax=10.0 + 16.0 * k,
                jitter=0.6)
    return run(E, INVW_FS, {'a': img, 'm': cv.texture()}, {'k': min(1.0, 0.45 + 0.55 * k)})


def impact(E, img, F, h, j, cx, cy):
    st = h.style
    c = (float(cx) * E.s, float(cy) * E.s)
    var = 0.0
    if h.shatter > 0:                                   # 满屏碎: the frame breaks into shards first (CHROMA v3 impact frames)
        dark = (INK, INK) if st == 'neg' else (None, None)       # neg: no crack lines (no thin lines) -- broken bolt only
        img = shatter(E, img, cx, cy, h.seed, (30.0 + 18.0 * j) * h.k * h.shatter, lc=dark[0], ec=dark[1])
        var = 1.0
    lines = None
    if st in ('duo', 'post3', 'neg', 'lines', 'plasma'):
        mode = {'duo': 0, 'post3': 1, 'neg': 2, 'lines': 3, 'plasma': 4}[st]
        if st in ('post3', 'duo'):
            img = post3(E, img, var, h.col, mode=1 if st == 'post3' else 0, j=j)
        else:
            img = run(E, STYLE_FS, {'a': img, 'ramp': ramp_tex(E, 'plasma'), 'nramp': ramp_tex(E, 'volt_neg')},
                      {'mode': mode, 'j': int(j), 'ink': INK, 'col': h.col, 'pap': PAPER, 'var': var,
                       'bands': 0.0 if j % 2 == 0 else 7.0})
        if st == 'duo':
            lines = (INK if j % 2 == 0 else h.col, 60, 345.0, 16.0, 0.9)
        elif st == 'post3' and j % 2 == 1:
            lines = (INK, 110, 300.0, 18.0, 1.0)
        elif st == 'neg' and j % 2 == 1:
            lines = (INK, 120, 300.0, 16.0, 0.85)
        elif st == 'lines':
            lines = (INK if j % 2 == 0 else WHITE, 130, 230.0, 20.0, 1.0)
        elif st == 'plasma' and j % 2 == 1:
            lines = (WHITE, 110, 300.0, 16.0, 0.85)
    elif st == 'half':                                  # 墨底: ink ground, dots sized by brightness
        dots = VT.ICE if j % 2 == 0 else h.col
        img = run(E, HALF_FS, {'a': img, 'bl': E.blur(img, 14.0)},
                  {'c': c, 's': 11.0 * E.s * (1.0 + 0.45 * j), 'D': (520.0 + 260.0 * j) * E.s, 'bg': INK,
                   'ink': dots, 'gain': 1.2, 'rot': 0.21 * F, 'inv': 1})
    if lines is not None:
        cv = E.canvas()
        col, n, r_in, wmax, al = lines
        focus_lines(cv, cx, cy, h.seed + F * 13, n=n, r_in=r_in, wmax=wmax, jitter=0.9, color=col, alpha=al)
        img = E.over(img, cv.texture())
    if j > 0:
        img = E.affine(img, 1.0 + 0.035 * j * h.k, 0.0, (cx, cy))
    return img


def after(E, T, img, h, t, cx, cy):
    """t = frames since the last impact frame (0 .. af), float (sub-frame motion)"""
    Tn, k = float(h.af), h.k
    u = clamp01((t + 1.0) / Tn)
    c = (float(cx) * E.s, float(cy) * E.s)
    if h.shatter > 0:
        q = 1.0 - eo3(clamp01((t + 0.5) / (0.6 * Tn)))
        if q > 0.01:
            img = shatter(E, img, cx, cy, h.seed, (30.0 + 18.0 * (h.n - 1)) * 1.2 * k * h.shatter * q, lk=0.0)  # shards
            #   settle back WITHOUT crack lines (a polygon line grid over the picture reads cheap)
    if h.ring:                                          # soft shock ring: refraction + a faint wide glow, never a line
        img = run(E, SHOCK_FS, {'a': img}, {'c': c, 'r': (60.0 + 1400.0 * k) * (1.0 - (1.0 - u) ** 2.2) * E.s,
                                           'w': (110.0 + 170.0 * u) * E.s, 'amp': 24.0 * k * (1.0 - u) ** 1.4 * E.s,
                                           'rim': 0.06 * k * (1.0 - u) ** 1.5, 'ca': 7.0 * k * (1.0 - u) * E.s,
                                           'rc': VT.ICE, 't1': VT.VIOLET, 't2': VT.ELECTRIC})
    Hs = []
    f_end = h.F + h.n - 1
    for tt in T.taps(3):
        sx, sy, sr = fx.shake(tt, f_end, 16.0 * k * h.shake, decay=3.2, seed=h.seed + 5)
        zz = 1.0 + 0.04 * k * math.exp(-max(0.0, tt - f_end) / 3.0)
        Hs.append(fx.pose_H(zoom=zz, roll=math.degrees(sr) * 0.8, piv=(cx, cy), at=(cx + sx, cy + sy)))
    img = fx.camera(E, img, Hs, edge='clamp')
    img = cool_split(E, img, 0.007 * k * math.exp(-t / 2.5), c[0], c[1], dx=4.0 * k * math.exp(-t / 2.0) * E.s)
    if h.arcs:
        img = arcs(E, img, h, t, cx, cy)
    return img


def active(hits, F):
    return [(h, *h.phase(F)) for h in hits if h.phase(F)[0] is not None]


def post(E, T, img, hits):
    """apply the active phase of every cluster at T.F to the finished frame `img` (call last in the shot)"""
    for h, ph, u in active(hits, T.F):
        cx, cy = h.center(T.F)
        if ph == 'charge':
            img = charge(E, img, T.F, h, u, cx, cy)
        elif ph == 'impact':
            img = impact(E, img, T.F, h, u, cx, cy)
        else:
            img = after(E, T, img, h, T.f - (h.F + h.n), cx, cy)
    return img
