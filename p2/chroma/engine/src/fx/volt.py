# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""VOLT signature look: the world is black & white, only ELECTRICITY glows.

  colours        the plasma ramp DEEP_VIOLET -> VIOLET -> ELECTRIC -> ICE -> ARC_WHITE, built in OKLCh. The HUES are physical
                 (computed at import from the CIE 1931 2-deg CMF table assets/data/ciexyz31_1.csv, `python -m src.fx.volt`
                 prints the check):
                   N2 second positive system 357-406 nm (the violet corona / glow of any air discharge)    -> h ~ 295 deg
                   N2+ first negative system 391.4 / 427.8 nm (brighter, ionised regions)                 -> h ~ 288 deg
                   the hot channel: ~28 000 K continuum (blackbody, Planck x CMF)                           -> h ~ 268.5 deg
                 so hue walks violet -> blue as brightness rises, and the core is the over-exposed continuum (white with a
                 breath of blue). CHROMA is kept low on purpose (over-done colour reads cheap; violet is the easiest colour to
                 cheapen): colour only in the highlights and in the glow, dark tones are true neutral B/W.
                 Aurora (the ONLY green in the film, ORBIT section): oxygen lines OI 557.7 nm (AURORA_GREEN) and 630.0 nm
                 (AURORA_RED) -- the only warm colour in the film, aurora only.
                 Shot code takes colours from here (DEEP_VIOLET VIOLET ELECTRIC ICE ARC_WHITE ACCENT INK_VIOLET
                 AURORA_GREEN AURORA_RED,
                 INK PAPER from kit.core) -- never type a hex.
  ramps          'volt' (ink -> ... -> arc white), 'volt_hi' (no ink), 'plasma' (false colour), 'volt_neg' (negative impact
                 frames: paper -> violet-ink -> ink), registered in kit.core.RAMPS (ramp_tex(E, 'volt')).
  volt(E, img, look, **kw)     THE filter (see LOOKS). volt_mask(E, img, look, **kw) -> RGB mask (R electric, G aurora green,
                               B aurora red). plasma(E, img, ...) = plasma false colour (impact style 'plasma').
Pipeline: 1) mask = brightness knee x "emitter" (brighter than its own neighbourhood: thin bolts, filaments, lamps -- NOT
sun-lit clouds) x electric hue window (or near-white core; 'grid': any hue) 2) world -> neutral B/W (channel mix + curve)
3) masked pixels re-coloured along the ramp by brightness (luminance kept) 4) glow = the emitting light blurred at four
radii, each radius tinted (ICE -> ELECTRIC -> VIOLET -> DEEP_VIOLET outward) and ADDED in linear light, then a soft shoulder:
that is the 「芯 + 晕」 (core over-exposes to white, halo turns violet as it spreads), not a colour filter over the frame.
"""
import math
import numpy as np
from src.kit.core import run, RAMPS, ramp_array, INK, PAPER, WHITE, hx, _srgb2lin, _lin2srgb, _M1, _M2

import os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CMF_CSV = f'{ROOT}/assets/data/ciexyz31_1.csv'

# ------------------------------------------------------------------ spectral basis (hues from real emission lines)
_M1X = np.array([[0.8189330101, 0.3618667424, -0.1288597137],     # OKLab: XYZ (D65) -> LMS (Ottosson 2020)
                 [0.0329845436, 0.9293118715, 0.0361456387],
                 [0.0482003018, 0.2643662691, 0.6338517070]])
_CMF = np.loadtxt(CMF_CSV, delimiter=',')

# relative intensities: typical air-discharge spectra (visible part; N2 2P dominates corona / glow, N2+ 1N the ionised core)
N2_2P = [(357.7, 0.85), (371.0, 0.15), (375.5, 0.45), (380.5, 0.6), (389.5, 0.07), (394.3, 0.15), (399.8, 0.32),
         (405.9, 0.10), (420.0, 0.04), (426.9, 0.05), (434.4, 0.04)]
N2P_1N = [(391.4, 1.0), (423.6, 0.07), (427.8, 0.45), (470.9, 0.08)]
OI_GREEN = [(557.7, 1.0)]
OI_RED = [(630.0, 1.0), (636.4, 0.33)]
T_CHANNEL = 28000.0           # lightning channel temperature (~28 000 C)


def lines_xyz(lines):
    lam, cmf = _CMF[:, 0], _CMF[:, 1:4]
    return sum(w * np.array([np.interp(l, lam, cmf[:, k]) for k in range(3)]) for l, w in lines)


def blackbody_xyz(T):
    lam = _CMF[:, 0] * 1e-9
    B = 1.0 / lam ** 5 / (np.exp(1.438776877e-2 / (lam * float(T))) - 1.0)
    return (B[:, None] * _CMF[:, 1:4]).sum(0)


def xyz_hue(X):
    """OKLab hue (deg) of an XYZ colour (any intensity; hue does not depend on it)"""
    lab = _M2 @ np.cbrt(_M1X @ (np.asarray(X, np.float64) / X[1]))
    return math.degrees(math.atan2(lab[2], lab[1])) % 360.0


H_N2_2P = xyz_hue(lines_xyz(N2_2P))            # ~295: violet glow
H_N2P_1N = xyz_hue(lines_xyz(N2P_1N))          # ~288: blue-violet
H_CHANNEL = xyz_hue(blackbody_xyz(T_CHANNEL))  # ~268.5: the hot channel (pale blue)
H_OI_GREEN = xyz_hue(lines_xyz(OI_GREEN))      # ~133
H_OI_RED = xyz_hue(lines_xyz(OI_RED))          # ~24


def oklch(L, C, h):
    """OKLCh -> sRGB (0..1). Out-of-gamut colours keep L and h; chroma is reduced until they fit (no hue shift)."""
    c = float(C)
    while True:
        a, b = c * math.cos(math.radians(h)), c * math.sin(math.radians(h))
        lin = np.linalg.inv(_M1) @ ((np.linalg.inv(_M2) @ np.array([L, a, b])) ** 3)
        if (lin.min() >= -1e-5 and lin.max() <= 1 + 1e-5) or c <= 0:
            break
        c = max(0.0, c - 0.0025)
    return tuple(float(v) for v in _lin2srgb(np.clip(lin, 0, 1)))


def _mixh(h1, h2, t):
    d = (h2 - h1 + 540.0) % 360.0 - 180.0
    return (h1 + d * t) % 360.0


# plasma ramp (brightness rising): restrained chroma, physical hues.
# look v3 (real electric light = white core + a faint blue-violet halo): the glow / halo colours carry HALF the chroma of
# v2 (v2: DEEP_VIOLET .105, VIOLET .145, ELECTRIC .145, ACCENT .16, INK_VIOLET .045); the core colours ICE / ARC_WHITE stay.
DEEP_VIOLET = oklch(0.30, 0.052, H_N2_2P)                      # dim corona glow (only the far halo keeps a breath of violet)
VIOLET = oklch(0.47, 0.072, _mixh(H_N2_2P, H_N2P_1N, 0.5))     # glow
ELECTRIC = oklch(0.64, 0.072, _mixh(H_CHANNEL, H_N2P_1N, 0.4))  # bright ionised air: pale electric blue
ICE = oklch(0.87, 0.065, H_CHANNEL)                            # the 28 000 K channel, nearly over-exposed
ARC_WHITE = oklch(0.975, 0.012, H_CHANNEL)                     # over-exposed core
AURORA_GREEN = oklch(0.80, 0.175, H_OI_GREEN)                  # OI 557.7 nm (ORBIT)
AURORA_RED = oklch(0.56, 0.15, H_OI_RED)                       # OI 630.0 nm
INK_VIOLET = oklch(0.17, 0.022, H_N2_2P)                       # ink with a breath of violet (negative frames, deep shadows)
ACCENT = oklch(0.55, 0.08, _mixh(H_N2_2P, H_N2P_1N, 0.5))      # 「电紫」: flat accent (duo / post3 impact frames, labels)
# look v4:
# the LIGHT carries a clear colour again, like a night photograph of lightning: white core, a clear cyan-blue / electric blue
# halo (photographic hues, bluer than the 28 000 K continuum), a little violet only far out (the N2 glow). That palette is
# L4 below -- used by the look itself: the ramps, the bloom tints, the flash on the clouds, the drawn discharges. The NAMED
H_ICE = 232.0                       # pale cyan-blue (the halo next to the core in lightning photographs)
H_ELECTRIC = 250.0                  # electric blue
H_BB = xyz_hue(blackbody_xyz(2000.0))   # ~2000 K: hot metal sparks, filaments: where warm light is pulled (~56.5)
LAVENDER = oklch(0.70, 0.11, 278.0)                            # the flash on the clouds (a lightning-lit cloud: lavender-blue)
L4 = dict(DEEP_VIOLET=oklch(0.30, 0.065, H_N2_2P),             # far corona glow: a little violet (wide = faint: no veil)
          VIOLET=oklch(0.47, 0.125, _mixh(H_N2_2P, H_N2P_1N, 0.5)),   # outer glow
          ELECTRIC=oklch(0.66, 0.145, H_ELECTRIC),             # bright ionised air: clear electric blue
          ICE=oklch(0.87, 0.085, H_ICE),                       # next to the core: pale cyan-blue
          ARC_WHITE=ARC_WHITE, LAVENDER=LAVENDER)
NAMED = dict(DEEP_VIOLET=DEEP_VIOLET, VIOLET=VIOLET, ELECTRIC=ELECTRIC, ICE=ICE, ARC_WHITE=ARC_WHITE,
             AURORA_GREEN=AURORA_GREEN, AURORA_RED=AURORA_RED, INK_VIOLET=INK_VIOLET, ACCENT=ACCENT, LAVENDER=LAVENDER)


def _hexs(cols):
    return ['#%02X%02X%02X' % tuple(int(round(v * 255)) for v in c) for c in cols]


def hexof(c):
    return _hexs([c])[0]


_L4R = [L4['DEEP_VIOLET'], L4['VIOLET'], L4['ELECTRIC'], L4['ICE'], ARC_WHITE]     # v4: the look's ramps carry the L4 light
RAMPS['volt'] = [hexof(INK)] + _hexs(_L4R)
RAMPS['volt_hi'] = _hexs(_L4R)
# v3.1: plasma impact frames -- the darks are neutral ink / grey, only the brightest steps turn cold
RAMPS['plasma'] = [hexof(INK)] + _hexs([oklch(0.22, 0.0, 0.0), oklch(0.38, 0.003, H_N2P_1N), oklch(0.55, 0.006, H_CHANNEL),
                                        oklch(0.72, 0.014, H_CHANNEL), oklch(0.86, 0.038, H_CHANNEL), ARC_WHITE])
RAMPS['volt_neg'] = [hexof(PAPER), hexof(oklch(0.89, 0.012, H_N2P_1N)), hexof(oklch(0.52, 0.045, H_N2P_1N)), hexof(INK_VIOLET),
                     hexof(INK)]        # v3: half the chroma (v2: '#D9DAE6', C .09)
# the drawn channel (fx.discharge): white core, ICE, only the faintest light keeps a little violet
# v4: the faint channel is violet-blue, the body electric blue -> cyan-blue, only the over-exposed core is white
RAMPS['volt_ch'] = _hexs([oklch(0.34, 0.085, _mixh(H_N2P_1N, H_N2_2P, 0.5)), oklch(0.60, 0.13, H_ELECTRIC),
                          oklch(0.80, 0.10, H_ICE), oklch(0.92, 0.045, H_ICE), ARC_WHITE])

# named LIGHT colours for the series impact / HUD presets
# (src/fx/series.py). ADDITIVE ONLY -- the look (GRADE, the 'volt*' ramps, bloom, flash) does not use them; nothing above
# changed. Physical hues like the colours above: a 220 V tungsten filament (~2700 K), a high-pressure sodium street lamp
# (~2000 K = H_BB), a plasma globe (neon + a little xenon: the Ne I orange-red lines + Xe I blue lines, luminance-weighted).
NE_I = [(585.2, 1.0), (588.2, 0.5), (594.5, 0.5), (597.6, 0.3), (603.0, 0.3), (607.4, 0.4), (609.6, 0.5), (614.3, 0.6),
        (616.4, 0.3), (621.7, 0.2), (626.6, 0.4), (633.4, 0.6), (638.3, 0.6), (640.2, 1.0), (650.6, 0.6), (659.9, 0.3),
        (692.9, 0.4), (703.2, 0.5)]
XE_I = [(450.1, 0.6), (452.5, 0.4), (462.4, 1.0), (467.1, 0.9), (473.4, 0.5), (480.7, 0.4), (484.3, 0.3), (492.3, 0.6)]
H_FILAMENT = xyz_hue(blackbody_xyz(2700.0))                                                   # ~64
_NE, _XE = lines_xyz(NE_I), lines_xyz(XE_I)
H_PLASMA = xyz_hue(0.8 * _NE / _NE[1] + 0.2 * _XE / _XE[1])                                  # ~333: pink-violet
AMBER = oklch(0.80, 0.14, H_FILAMENT)          # 琥珀: the filament / hot-metal spark colour
SODIUM = oklch(0.70, 0.17, H_BB)               # 钠灯橙: sodium street lamps (and the orange of post3_orange)
PLASMA_PINK = oklch(0.64, 0.15, H_PLASMA)      # 等离子粉紫: plasma-globe filaments
EMBER = oklch(0.40, 0.12, _mixh(H_BB, H_OI_RED, 0.5))   # a cooling spark's last red (spark ramp end)
KIT = dict(ELECTRIC=L4['ELECTRIC'], ICE=L4['ICE'], VIOLET=L4['VIOLET'], DEEP_VIOLET=L4['DEEP_VIOLET'], AMBER=AMBER,
           SODIUM=SODIUM, PLASMA_PINK=PLASMA_PINK, EMBER=EMBER, ARC_WHITE=ARC_WHITE)
# Accent colours (a pastel blue or a flat orange reads as camouflage / flat fill -- the blue must be dense,
# not pastel): two DENSE impact accents, additive like the colours above (the look does not use them).
# ELECTRIC_DEEP = the electric blue of a lightning halo where it turns from ELECTRIC towards the violet glow (look v4 bloom
# tints ELECTRIC -> VIOLET), darker and denser than ELECTRIC; FORGE = hot metal at 1300 K (the post3 orange).
H_HALO = _mixh(H_ELECTRIC, _mixh(H_N2_2P, H_N2P_1N, 0.5), 0.25)                            # ~260
ELECTRIC_DEEP = oklch(0.50, 0.18, H_HALO)      # #1B5CC7  post3_blue / duo_blue / voronoi accent
H_FORGE = xyz_hue(blackbody_xyz(1300.0))       # ~42
FORGE = oklch(0.62, 0.20, H_FORGE)             # #DD5406  post3_orange accent
KIT.update(ELECTRIC_DEEP=ELECTRIC_DEEP, FORGE=FORGE)
# 等离子假色 (impact style 'plasma'): ink -> deep violet -> plasma pink -> electric blue -> pale cyan -> white
RAMPS['plasma4'] = [hexof(INK)] + _hexs([L4['DEEP_VIOLET'], PLASMA_PINK, L4['ELECTRIC'], L4['ICE'], ARC_WHITE])
# 分档时最暗一档取档中间会让纯黑落到深紫，黑天变成平涂紫；前面垫一个墨色。
# 前面多垫一个墨色，最暗的一两档保持墨黑，颜色只在亮的地方出现。
RAMPS['plasma4k'] = [hexof(INK)] + RAMPS['plasma4']
# a spark cooling: white-hot -> amber -> sodium orange -> ember red
RAMPS['spark4'] = _hexs([ARC_WHITE, AMBER, SODIUM, EMBER])


def ramp_tex(E, name='volt'):
    from src.kit.core import ramp_tex as _rt
    return _rt(E, name)


def tint_lin(c):
    """linear-light RGB of a colour, normalised to luminance 1 (for tinting glows)"""
    l = _srgb2lin(np.array(c))
    return tuple(float(v) for v in l / max(1e-6, float(l @ np.array([0.2126, 0.7152, 0.0722]))))


# ------------------------------------------------------------------ shaders
HSV_GLSL = '''float hdist(float h, vec2 w){ float c = 0.5 * (w.x + w.y); float hw = 0.5 * (w.y - w.x);
  float d = abs(mod(h - c + 540.0, 360.0) - 180.0); return max(d - hw, 0.0); }
vec2 hs(vec3 c){ float mx = max(c.r, max(c.g, c.b)); float mn = min(c.r, min(c.g, c.b)); float d = mx - mn; float h = 0.0;
  if (d > 1e-5){ if (mx == c.r) h = mod((c.g - c.b) / d, 6.0); else if (mx == c.g) h = (c.b - c.r) / d + 2.0; else h = (c.r - c.g) / d + 4.0; }
  return vec2(h * 60.0, d / max(mx, 1e-4)); }
'''
CLIP_FS = '''uniform sampler2D a; void main(){ o = vec4(vec3(smoothstep(0.94, 0.995, luma(texture(a, UV()).rgb))), 1.0); }'''
MASK_FS = HSV_GLSL + '''uniform sampler2D a; uniform sampler2D bs; uniform sampler2D bw; uniform sampler2D cb;
uniform float lo; uniform float hi; uniform float vmix; uniform vec2 hue; uniform float hsoft; uniform float smin;
uniform float hot0; uniform float hotk; uniform vec2 er; uniform vec2 ew; uniform vec2 rd; uniform float rlo; uniform float emk;
uniform float anyh;
float Lt(vec2 p){ return luma(texture(a, clamp(p, vec2(0.5), u_size - 0.5) / u_size).rgb); }
uniform float rbig;
float ridge(vec2 p, float y, int nk){        // bright LINE / point: darker on BOTH sides (a step edge is not a ridge)
  float r = -1.0; float R[4] = float[4](2.5, 6.0, 13.0, 28.0);
  for (int k = 0; k < 4; k++){ if (k >= nk) break; float d = R[k] * u_s * (u_size.x / (1920.0 * u_s));
    r = max(r, y - max(Lt(p + vec2(d, 0.0)), Lt(p - vec2(d, 0.0))));
    r = max(r, y - max(Lt(p + vec2(0.0, d)), Lt(p - vec2(0.0, d))));
    r = max(r, y - max(Lt(p + vec2(d, d) * 0.7071), Lt(p - vec2(d, d) * 0.7071)));
    r = max(r, y - max(Lt(p + vec2(d, -d) * 0.7071), Lt(p - vec2(d, -d) * 0.7071))); }
  return r; }
uniform float ag; uniform vec2 ghue; uniform float ar; uniform vec2 rhue; uniform float av; uniform vec2 vhue;
uniform float alo; uniform float ahi; uniform float k; uniform float cfree;
void main(){ vec2 uv = UV(); vec3 c = texture(a, uv).rgb; vec2 q = hs(c); float h = q.x, s = q.y;
  float y = luma(c); float mx = max(c.r, max(c.g, c.b));
  float wv = smoothstep(lo, hi, mix(y, mx, vmix));                     // brightness knee: colour only in the highlights
  float rs = y / (luma(texture(bs, uv).rgb) + 0.03);                  // an emitter is much brighter than its surroundings
  float rw = y / (luma(texture(bw, uv).rgb) + 0.03);                  // (a RATIO: sun-lit clouds / lit haze stay ~1-1.4)
  float em = max(smoothstep(er.x, er.y, rs), smoothstep(ew.x, ew.y, rw));
  em = mix(1.0, max(em, smoothstep(rd.x, rd.y, ridge(gl_FragCoord.xy, y, rbig > 0.5 ? 4 : 2)) * smoothstep(rlo, rlo + 0.2, y)), emk);  // + bright channels inside their own glow
  float hw = (1.0 - smoothstep(0.0, hsoft, hdist(h, hue))) * smoothstep(smin, smin + 0.15, s);
  float core = hotk * smoothstep(hot0, 1.0, y);
  float e = wv * em * max(max(hw, core), anyh);
  e = max(e, cfree * smoothstep(0.93, 1.0, y) * smoothstep(0.5, 0.25, q.y));   // 'strike': clipped channel = electric
  float g = ag * (1.0 - smoothstep(0.0, hsoft, hdist(h, ghue))) * smoothstep(0.10, 0.28, s) * smoothstep(alo, ahi, mx);
  float r = ar * (1.0 - smoothstep(0.0, hsoft, hdist(h, rhue))) * smoothstep(0.22, 0.45, s) * smoothstep(alo, ahi, mx);
  r = max(r, av * (1.0 - smoothstep(0.0, hsoft, hdist(h, vhue))) * smoothstep(0.18, 0.4, s) * smoothstep(alo, ahi, mx));
  // A = CORE: the part of the light allowed to reach the top of the ramp (white). Thin channels: their own ridge; fat
  // over-exposed columns: only the interior (the plate blurred at sig_w is still clipped) -- the rest becomes the halo colour
  float thin = smoothstep(0.02, 0.08, ridge(gl_FragCoord.xy, y, 4)) * smoothstep(0.80, 0.95, y);
  float fat = smoothstep(0.93, 0.99, texture(cb, uv).r);        // the clipped area blurred: deep inside a fat column only
  o = vec4(clamp(e * k, 0.0, 1.0), clamp(g, 0.0, 1.0), clamp(r, 0.0, 1.0), max(thin, fat)); }'''

SHOULDER_GLSL = '''vec3 shoulder(vec3 x, float k){ vec3 y = k + (1.0 - k) * (1.0 - exp(-(x - k) / max(1.0 - k, 1e-3)));
  return mix(x, y, step(vec3(k), x)); }
'''
# look v4: OKLab in GLSL (linear sRGB <-> OKLab, Ottosson 2020) -- chroma is boosted / hue is read without shifting the hue
OKLAB_GLSL = '''vec3 oklab(vec3 c){ c = max(c, vec3(0.0));
  vec3 q = pow(vec3(0.4122214708 * c.r + 0.5363325363 * c.g + 0.0514459929 * c.b,
                    0.2119034982 * c.r + 0.6806995451 * c.g + 0.1073969566 * c.b,
                    0.0883024619 * c.r + 0.2817188376 * c.g + 0.6299787005 * c.b) + 1e-12, vec3(1.0 / 3.0));
  return vec3(0.2104542553 * q.x + 0.7936177850 * q.y - 0.0040720468 * q.z,
              1.9779984951 * q.x - 2.4285922050 * q.y + 0.4505937099 * q.z,
              0.0259040371 * q.x + 0.7827717662 * q.y - 0.8086757660 * q.z); }
vec3 oklab_lin(vec3 L){ vec3 q = vec3(L.x + 0.3963377774 * L.y + 0.2158037573 * L.z, L.x - 0.1055613458 * L.y - 0.0638541728 * L.z,
                                      L.x - 0.0894841775 * L.y - 1.2914855480 * L.z); q = q * q * q;
  return vec3(4.0767416621 * q.x - 3.3077115913 * q.y + 0.2309699292 * q.z, -1.2684380046 * q.x + 2.6097574011 * q.y - 0.3413193965 * q.z,
              -0.0041960863 * q.x - 0.7034186147 * q.y + 1.7076147010 * q.z); }
vec3 ingamut(vec3 c){ float Y = dot(c, vec3(0.2126, 0.7152, 0.0722)); float mn = min(c.r, min(c.g, c.b));   // no negative channel:
  return mn >= 0.0 ? c : mix(vec3(Y), c, clamp(Y / max(Y - mn, 1e-6), 0.0, 1.0)); }                          // towards its own grey
float okhue(vec3 L){ return mod(degrees(atan(L.z, L.y)) + 360.0, 360.0); }
float hwin(float h, vec2 w, float s){ float c = 0.5 * (w.x + w.y); float hw = 0.5 * (w.y - w.x);   // 1 inside the hue window
  float d = abs(mod(h - c + 540.0, 360.0) - 180.0); return 1.0 - smoothstep(hw, hw + s, d); }
// chroma x k with a soft ceiling cm (L and hue kept), linear in / linear out
vec3 boostc(vec3 c, float k, float cm){ vec3 L = oklab(c); float C = length(L.yz); if (C < 1e-5) return c;
  float Cn = cm * tanh(C * k / cm); return ingamut(oklab_lin(vec3(L.x, L.yz * (Cn / C)))); }
'''
# look v4 LIGHT mask (any hue): an emitter -- brighter than its own neighbourhood (ratio at sig_s / sig_w, or a bright ridge),
# above a brightness knee on the max channel. Sparks, filaments, lamps, neon, the welding arc, the plasma globe's core: they
# keep their OWN colour in the grade (and glow in it). Lit clouds / sky (ratio ~1) never qualify: the world stays B/W.
LIGHTM_FS = '''uniform sampler2D a; uniform sampler2D bs; uniform sampler2D bw; uniform vec2 kn; uniform vec2 er; uniform vec2 ew;
uniform vec2 rd; uniform float k;
float Lt(vec2 p){ return luma(texture(a, clamp(p, vec2(0.5), u_size - 0.5) / u_size).rgb); }
float ridge(vec2 p, float y){ float r = -1.0; float R[3] = float[3](2.5, 6.0, 13.0);
  for (int i = 0; i < 3; i++){ float d = R[i] * u_s * (u_size.x / (1920.0 * u_s));
    r = max(r, y - max(Lt(p + vec2(d, 0.0)), Lt(p - vec2(d, 0.0))));
    r = max(r, y - max(Lt(p + vec2(0.0, d)), Lt(p - vec2(0.0, d)))); }
  return r; }
void main(){ vec2 uv = UV(); vec3 c = texture(a, uv).rgb; float y = luma(c); float mx = max(c.r, max(c.g, c.b));
  float rs = y / (luma(texture(bs, uv).rgb) + 0.03); float rw = y / (luma(texture(bw, uv).rgb) + 0.03);
  float em = max(max(smoothstep(er.x, er.y, rs), smoothstep(ew.x, ew.y, rw)), smoothstep(rd.x, rd.y, ridge(gl_FragCoord.xy, y)));
  float v = k * smoothstep(kn.x, kn.y, mix(y, mx, 0.6)) * em;
  o = vec4(vec3(clamp(v, 0.0, 1.0)), 1.0); }'''

GRADE_FS = SHOULDER_GLSL + OKLAB_GLSL + '''uniform sampler2D a; uniform sampler2D m; uniform sampler2D ramp; uniform sampler2D lb;
uniform sampler2D lm;
uniform vec3 bw; uniform float con; uniform float bgam; uniform float blk; uniform float wht; uniform float bwk; uniform vec3 tint;
uniform float clar; uniform float wexp; uniform float wcap; uniform float wk0;
uniform float tlo; uniform float thi; uniform float tgam; uniform float tmax; uniform float own; uniform float sat;
uniform float keepl; uniform float gain; uniform float mgam; uniform float sh; uniform float ausat; uniform float augain;
uniform float rsat; uniform float tcap;
uniform float tc; uniform float lsat; uniform float lcm; uniform float lgain; uniform vec2 eh; uniform float eown; uniform float wown;
uniform float wcr; uniform float wlift; uniform float wtint; uniform vec3 wtc; uniform vec2 wh; uniform float wfl; uniform float wfe; uniform float wpull; uniform float hbb; uniform float wfa;
float softcap(float g){ if (wcap >= 0.999 || g <= wk0) return g;
  float cap = wcap + (1.0 - wcap) * wcr; float k0 = wk0 + (cap - wcap) * 0.5; if (g <= k0) return g;   // v4: ceiling raised by wcr
  float s = max(cap - k0, 1e-3); return k0 + s * (1.0 - exp(-(g - k0) / s)); }
void main(){ vec2 uv = UV(); vec3 c = texture(a, uv).rgb; vec4 M = texture(m, uv);
  float y = dot(c, bw);
  y += clar * clamp(y - dot(texture(lb, uv).rgb, bw), -0.12, 0.12);    // local contrast: cloud layering survives the ceiling
  float g = clamp((y - blk) / max(wht - blk, 1e-3), 0.0, 1.0); g = pow(g, bgam); g = mix(g, g * g * (3.0 - 2.0 * g), con);
  g = softcap(g * wexp);                                               // WORLD CEILING: only the light may be bright
  float g0 = g;
  g += wlift * smoothstep(0.02, 0.30, g) * g * (1.0 - g) * (1.0 - g);  // v4: mid-tones up; toe (blacks) and highlights untouched
  vec3 world = mix(c, vec3(g) * tint, bwk);
  // v4: the part of the world ABOVE the ceiling knee is lit by the storm's own lightning (in-cloud flashes): it carries a
  // breath of the flash colour, graded by how far it climbs -- the dark and mid world stays neutral
  world = mix(world, world * wtc, wtint * smoothstep(wk0 + 0.15 * (1.0 - wk0), wk0 + 0.6 * (1.0 - wk0), g0) * bwk);
  float yl = luma(c); float mx = max(c.r, max(c.g, c.b));
  float t = pow(clamp((mix(yl, mx, 0.5) - tlo) / max(thi - tlo, 1e-3), 0.0, 1.0), tgam) * tmax;
  t = mix(min(t, tcap), t, clamp(M.a, 0.0, 1.0));     // only the CORE climbs to white; the rest of the light keeps the halo colour
  vec3 rl = lin(texture(ramp, vec2(t, 0.5)).rgb);
  vec3 LW = vec3(0.2126, 0.7152, 0.0722);
  float Ys = dot(lin(c), LW); float Yr = dot(rl, LW);
  rl *= pow(max(Ys, 1e-5) / max(Yr, 1e-5), keepl);                   // keep the source's luminance (detail)
  rl = mix(vec3(dot(rl, LW)), rl, rsat);
  vec3 ow = lin(clamp(vec3(yl) + (c - vec3(yl)) * sat, 0.0, 1.0));
  // v4: the light's OWN colour, chroma x lsat (soft ceiling lcm). In the electric light the own colour wins where it is clearly
  // not electric (warm sparks, amber filaments, sodium lamps, green phosphor, pink plasma core): wown; neutral / blue-violet
  // light takes the electric ramp (white core -> cyan-blue -> electric blue -> violet), keeping eown of its own colour
  vec3 cl = lin(c); vec3 LB = oklab(cl); float Cr = length(LB.yz) / max(LB.x, 1e-3); float hb = okhue(LB);
  // warm-leaning light (clipped sparks, a white-hot filament, tungsten / sodium lamps) reads at its colour temperature:
  // relative chroma floored at wfl along its own (warm) hue -- the camera clipped it to white, the metal is ~2000 K
  float wlean = hwin(hb, wh, 20.0) * smoothstep(0.004, 0.025, Cr);
  vec3 ob = boostc(cl, lsat, lcm);
  vec3 LO = oklab(ob); float Co = length(LO.yz);
  float hw = okhue(LO); hw = radians(hw + (mod(hbb - hw + 540.0, 360.0) - 180.0) * wpull);   // towards the ~2000 K hue
  vec3 obw = ingamut(oklab_lin(vec3(LO.x, max(Co, wfl * LO.x) * vec2(cos(hw), sin(hw)))));
  float cold = max((1.0 - smoothstep(0.035, 0.10, Cr)) * (1.0 - wfe * wlean), hwin(hb, eh, 18.0));
  vec3 e3 = mix(rl, ow, own);
  vec3 elec = enc(shoulder(mix(e3, mix(ob, obw, wfe * wlean), tc * mix(wown, eown, cold)) * gain, sh));
  vec3 au = enc(shoulder(lin(clamp(vec3(yl) + (c - vec3(yl)) * ausat, 0.0, 1.0)) * augain, sh));
  vec3 res = mix(world, elec, pow(M.r, mgam));
  res = mix(res, au, clamp(max(M.g, M.b), 0.0, 1.0));
  // v4: every other emitter keeps its own (boosted) colour -- never the world (lit clouds are not emitters)
  float wl = tc * texture(lm, uv).r * (1.0 - clamp(M.r, 0.0, 1.0)) * (1.0 - clamp(max(M.g, M.b), 0.0, 1.0));
  res = mix(res, enc(shoulder(mix(ob, obw, wfa * wlean) * lgain, sh)), wl);
  o = vec4(clamp(res, 0.0, 1.0), 1.0); }'''

GLOWSRC_FS = '''uniform sampler2D a; uniform sampler2D m; uniform sampler2D lm; uniform float thr; uniform float ak; uniform float hdr;
uniform float lk;
void main(){ vec2 uv = UV(); vec3 c = texture(a, uv).rgb; vec4 M = texture(m, uv);
  float w = max(max(M.r, ak * max(M.g, M.b)), lk * texture(lm, uv).r) * smoothstep(thr, 1.0, luma(c));
  float mx = max(c.r, max(c.g, c.b)); float clip = smoothstep(0.86, 1.0, mx);
  // a clipped CORE is far brighter than "1" (thin channels, the interior of a column); a big clipped area that is not a
  // channel (a lit cloud, a globe's milky glass) is not boosted -- the bloom follows the light, it does not make fog
  o = vec4(lin(c) * w * (1.0 + hdr * clip * clip * (0.25 + 0.75 * clamp(M.a, 0.0, 1.0))), 1.0); }'''

# look v3 BLOOM: ONE point-spread function for the footage's light (volt) and the drawn discharges: six
# Gaussians, sigma s0 * r^i, weights q^i normalised to 1 -- energy-conserving (the halo carries `amount` x the source's
# energy however many scales), narrow = bright, wide = dim (a ~1/r^1.5 tail, no visible rings, no grey fog); each scale
# tinted (white -> ICE -> pale ELECTRIC -> pale VIOLET outward: only the far halo keeps a breath of violet)
# look v4: the tints act on COLD light only (neutral, or blue-violet: lightning, arcs) -- its halo walks white -> cyan-blue ->
# electric blue -> violet outward; light with its own clear colour (sparks, filaments, sodium lamps, plasma pink, phosphor
# green) keeps that colour in its glow (chroma x bsat): orange sparks glow orange, never blue
BLOOM_FS = SHOULDER_GLSL + OKLAB_GLSL + '''uniform sampler2D a; uniform sampler2D g0; uniform sampler2D g1; uniform sampler2D g2;
uniform sampler2D g3; uniform sampler2D g4; uniform sampler2D g5; uniform vec3 wa; uniform vec3 wb;
uniform vec3 t0; uniform vec3 t1; uniform vec3 t2; uniform vec3 t3; uniform vec3 t4; uniform vec3 t5; uniform float tk;
uniform float sh; uniform int mode; uniform int rch; uniform vec2 eh; uniform float bsat; uniform float bcm; uniform float hbb;
vec3 G(sampler2D t, vec2 uv){ vec3 v = texture(t, uv).rgb; return rch == 1 ? vec3(v.r) : v; }
vec3 tinted(vec3 g, vec3 t){ float Y = dot(g, vec3(0.2126, 0.7152, 0.0722)); if (Y < 1e-7) return g;
  vec3 L = oklab(g); float Cr = length(L.yz) / max(L.x, 1e-4);
  float cold = max(1.0 - smoothstep(0.035, 0.10, Cr), hwin(okhue(L), eh, 18.0));
  vec3 own = bsat > 1.001 ? boostc(g, bsat, bcm * max(L.x, 1e-4)) : g;
  float hg = okhue(L); float wk = 0.45 * hwin(hg, vec2(-15.0, 105.0), 20.0) * smoothstep(0.04, 0.12, Cr);   // warm glow -> amber
  if (wk > 0.001){ vec3 Lo = oklab(own); float h2 = radians(hg + (mod(hbb - hg + 540.0, 360.0) - 180.0) * wk);
    own = ingamut(oklab_lin(vec3(Lo.x, length(Lo.yz) * vec2(cos(h2), sin(h2))))); }
  return mix(own, mix(g, Y * t, tk), cold); }
void main(){ vec2 uv = UV();
  vec3 gl = tinted(G(g0, uv), t0) * wa.x + tinted(G(g1, uv), t1) * wa.y + tinted(G(g2, uv), t2) * wa.z
          + tinted(G(g3, uv), t3) * wb.x + tinted(G(g4, uv), t4) * wb.y + tinted(G(g5, uv), t5) * wb.z;
  if (mode == 1){ o = vec4(gl, 1.0); return; }
  o = vec4(enc(shoulder(lin(texture(a, uv).rgb) + gl, sh)), 1.0); }'''

# 闪光补光 (flash fill), look v3 (no smooth radial disc over the picture): the light no longer multiplies a smooth disc into
# the picture. It falls off with distance (multi-scale, log-compressed: strong next to the channel, weak far away) and
# lands on a MEDIUM = the plate's own cloud: its brightness (albedo knee alb, contrast agam), its billows (local contrast
# ck: tops catch more than folds) and its relief (rk: the side of a billow that faces the light is lit, the far side stays
# in shade) -- gaps and the dark ground stay dark. A very faint cold tint; the air glow (add) is kept near the channel.
FLASH_FS = SHOULDER_GLSL + '''uniform sampler2D a; uniform sampler2D mb; uniform sampler2D ml;
uniform sampler2D l0; uniform sampler2D l1; uniform sampler2D l2;
uniform vec3 w; uniform vec3 tint; uniform float k; uniform float add; uniform float cap; uniform float sh; uniform float gain;
uniform int rch; uniform vec2 alb; uniform float agam; uniform float ck; uniform float rk; uniform float rr; uniform float G;
uniform float gd; uniform sampler2D tx; uniform vec2 txk; uniform float tsm; uniform float tfar;
float I(sampler2D t, vec2 uv){ vec3 v = texture(t, uv).rgb; return rch == 1 ? v.r : dot(v, vec3(0.2126, 0.7152, 0.0722)); }
float irr3(vec2 p){ vec2 uv = clamp(p, vec2(0.5), u_size - 0.5) / u_size;
  return gain * (w.x * I(l0, uv) + w.y * I(l1, uv) + w.z * I(l2, uv)); }
float Lm(sampler2D t, vec2 p){ return luma(texture(t, clamp(p, vec2(0.5), u_size - 0.5) / u_size).rgb); }
void main(){ vec2 uv = UV(); vec2 p = gl_FragCoord.xy; vec3 c = lin(texture(a, uv).rgb);
  float e = k * irr3(p);
  float es = e / (1.0 + e / cap);                            // falls off with distance; a huge flash saturates
  float m = Lm(mb, p); float lm = Lm(ml, p);
  float A = pow(clamp((m - alb.x) / max(alb.y - alb.x, 1e-3), 0.0, 1.0), agam);       // the cloud's own brightness
  A *= clamp(1.0 + ck * (m - lm) / (lm + 0.04), 0.25, 2.0);                          // billow tops catch more
  A *= mix(tsm, 1.0, smoothstep(txk.x, txk.y, texture(tx, uv).r));                 // smooth water / haze: a faint sheen
  float d = rr * u_s; float D = gd * u_s;
  vec2 gh = vec2(Lm(mb, p + vec2(d, 0.0)) - Lm(mb, p - vec2(d, 0.0)), Lm(mb, p + vec2(0.0, d)) - Lm(mb, p - vec2(0.0, d)));
  vec2 gi = vec2(irr3(p + vec2(D, 0.0)) - irr3(p - vec2(D, 0.0)), irr3(p + vec2(0.0, D)) - irr3(p - vec2(0.0, D)));
  float rel = -dot(gh, gi / (length(gi) + 1e-6)) / (length(gh) + 0.02);              // > 0: faces the light
  float lit = G * es * A * max(0.0, 1.0 + rk * clamp(rel, -1.0, 1.0));
  float en = k * gain * w.x * I(l0, uv);                                              // air glow: near the channel only
  // v4: the light keeps its colour next to the bolt and pales with distance (tfar of the tint far away): no tinted sky
  float i0 = w.x * I(l0, uv); float near = clamp(1.6 * i0 / max(i0 + w.y * I(l1, uv) + w.z * I(l2, uv), 1e-6), 0.0, 1.0);
  vec3 tn = mix(vec3(1.0), tint, mix(tfar, 1.0, near));
  vec3 res = c + (lit + 0.25 * add * en / (1.0 + en / cap) * (0.4 + 0.6 * A)) * tn;
  o = vec4(enc(shoulder(res, sh)), 1.0); }'''

# texture of the medium (mean absolute detail at ~8 px): billowy cloud lights fully, smooth water / haze only faintly
TEXM_FS = '''uniform sampler2D a; uniform sampler2D b; void main(){ vec2 uv = UV();
  o = vec4(vec3(abs(luma(texture(a, uv).rgb) - luma(texture(b, uv).rgb))), 1.0); }'''

# flash source: the light softly dilated (thin bolts and fat columns get comparable reach) and, for footage, gated to
# LINE-like light (a lamp is a dot: its wide blur is tiny; a bolt is a line or a column) -- the flash comes from lightning
FDIL_FS = '''uniform sampler2D a; uniform sampler2D g; uniform float dk; uniform int rch; uniform int useg;
void main(){ vec2 uv = UV(); vec3 v = texture(a, uv).rgb; float s = rch == 1 ? v.r : dot(v, vec3(0.2126, 0.7152, 0.0722));
  float d = min(1.0, dk * s); if (useg == 1) d *= smoothstep(0.02, 0.05, texture(g, uv).r);
  o = vec4(vec3(d), 1.0); }'''

# plate statistics (look v3: generated light matches the footage): per pixel -> edge-weighted gradient energy at two blur
# levels (the re-blur sharpness estimate) + pixel noise in flat areas; averaged by the mip chain, read back as 1 texel
STATS_FS = '''uniform sampler2D a; uniform sampler2D b0; uniform sampler2D b1;
float L(sampler2D t, vec2 p){ return luma(texture(t, clamp(p, vec2(0.5), u_size - 0.5) / u_size).rgb); }
void main(){ vec2 p = gl_FragCoord.xy;
  vec2 g0 = 0.5 * vec2(L(b0, p + vec2(1.0, 0.0)) - L(b0, p - vec2(1.0, 0.0)), L(b0, p + vec2(0.0, 1.0)) - L(b0, p - vec2(0.0, 1.0)));
  vec2 g1 = 0.5 * vec2(L(b1, p + vec2(1.0, 0.0)) - L(b1, p - vec2(1.0, 0.0)), L(b1, p + vec2(0.0, 1.0)) - L(b1, p - vec2(0.0, 1.0)));
  float m1 = length(g1);
  float e = smoothstep(0.006, 0.02, m1);                      // a real edge survives the 2 px blur (noise does not)
  float fl = (1.0 - smoothstep(0.0012, 0.0035, m1)) * smoothstep(0.015, 0.05, L(b1, p));   // flat, not black
  float hp = L(a, p) - L(b0, p);
  o = vec4(e * dot(g0, g0) * 1000.0, e * dot(g1, g1) * 1000.0, fl * hp * hp * 1000.0, fl); }'''

GT6 = ('ARC_WHITE', 'ICE', 'ICE', 'ELECTRIC', 'VIOLET', 'DEEP_VIOLET')      # bloom tints, inner -> outer scale
EH = (195.0, 305.0)                # v4: OKLab hue window of ELECTRIC light (cyan .. blue .. violet): takes the electric tints
BSAT, BCM = 1.25, 0.32             # v4: own-colour glows: chroma x BSAT (soft ceiling BCM x L)

# ------------------------------------------------------------------ parameters
DEFAULT = dict(
    # mask
    lo=0.42, hi=0.80, vmix=0.35, hue=(195.0, 330.0), hsoft=22.0, smin=0.06, hot0=0.78, hotk=1.0,
    er=(1.22, 1.85), ew=(1.45, 2.6), rd=(0.05, 0.16), rlo=0.70, rbig=0.0, cfree=0.0, emk=1.0, anyh=0.0, sig_s=5.0, sig_w=26.0,
    sig_c=22.0,
    ag=0.0, ghue=(80.0, 175.0), ar=0.0, rhue=(-25.0, 22.0), av=0.0, vhue=(235.0, 300.0), alo=0.10, ahi=0.34,
    mk=1.0, soft=1.2, asoft=7.0, mgam=1.0,
    # B/W world. clar = local contrast (sigma sig_w); wexp = world exposure; wcap = WORLD CEILING (display value the
    # non-electric picture may reach, soft above wk0; 1 = off) -- in storm / strike only electricity is bright
    bwk=1.0, bw=(0.2126, 0.7152, 0.0722), con=0.30, bgam=1.0, blk=0.035, wht=0.97, tint=(1.0, 1.0, 1.0),
    clar=0.0, wexp=1.0, wcap=1.0, wk0=0.25,
    # electric re-colour (ramp by brightness)
    ramp='volt', tlo=0.22, thi=1.0, tgam=1.0, tmax=1.0, tcap=1.0, own=0.20, sat=0.80, keepl=0.70, gain=1.0, rsat=1.0, sh=0.78,
    ausat=0.95, augain=1.0,
    # glow (the 晕), look v3: energy-conserving bloom, 6 scales gsig * gr^i weighted gq^i (sum 1) x glow, tinted gt
    # (white -> ICE -> pale ELECTRIC -> pale VIOLET outward); hdr = boost of clipped CORES (not of big clipped areas)
    glow=0.0, gsig=2.0, gr=2.5, gq=0.5, gthr=0.30, gtint=0.9, gts=1.0, gak=0.6, hdr=4.0, gt=GT6,
    # 闪光补光 flash fill: the plate's own electric light, blurred at fsig, lights the world around it
    flash=0.0, fgain=60.0, fcap=1.6, fadd=0.04, fsig=(40.0, 120.0, 320.0), fw=(0.4, 0.35, 0.25), ftint='LAVENDER', ftsat=0.45, ftfar=0.4,
    falb=(0.10, 0.85), fagam=1.5, fck=0.8, frk=0.7,       # v3: the flash lands on the plate's own clouds (fx.volt.flash)
    # look v4: TRUE-COLOUR LIGHT. tc = weight (x mk); every emitter keeps its own colour, chroma x lsat (soft
    # ceiling lcm, OKLab C), exposure lgain; the light mask = knee lkn on mix(luma, max, 0.6) x emitter (ratios ler / lew at
    # sig_s / sig_w, ridge lrd). Inside the electric mask the own colour weighs wown, or eown where the light is cold
    # (neutral / OKLab hue in eh) -> cold light takes the electric ramp. lk = own-colour lights feed the bloom. World: the
    # ceiling wcap is raised by wcr of its headroom, wlift lifts the mid-tones (toe untouched). The new knobs have their own
    # names on purpose: shot overrides of v3 knobs (sat, rsat, gt, gts ...) keep working, the v4 colour sits on top.
    tc=1.0, lsat=1.7, lcm=0.20, lgain=1.08, eh=EH, eown=0.30, wown=0.90, lk=1.0,
    lkn=(0.30, 0.72), ler=(1.25, 1.9), lew=(1.5, 2.6), lrd=(0.05, 0.16),
    wcr=0.12, wlift=0.40, wtint=0.0, gtail=0.5,
    wh=(-15.0, 105.0), wfl=0.13, wfe=0.0, wfa=1.0, wpull=0.55,   # warm floor: OKLab hue window (pink-red .. yellow), relative chroma
                                               # floor, hue pulled wpull towards the ~2000 K blackbody hue H_BB; also inside the
                                               # electric mask with wfe = 1 ('grid': man-made light -- sparks, filaments, lamps)
)
# film voltage presets (the colour climbs with the voltage): CHARGE dark violet -> STORM violet-white ->
# STRIKE ice-white core + electric halo -> ORBIT + the aurora's own green
LOOKS = {
    'neutral': dict(tc=0.0),                       # B/W only (v4: no true-colour light either)
    'charge':  dict(tmax=0.80, keepl=0.5, sat=0.75, glow=0.8, bgam=1.12, blk=0.045, lo=0.22, hi=0.58, hue=(185.0, 348.0), emk=0.35,
                    eown=0.6),                        # v4: the globe's own violet / pink shows through the ramp
    'storm':   dict(bw=(0.40, 0.52, 0.08), con=0.42, blk=0.05, wht=0.95, glow=0.8, tmax=0.9, keepl=0.55, sat=0.7,
                    clar=0.35, wcap=0.42, wk0=0.26, flash=1.0, tcap=0.9, wtint=0.4, wfa=0.0),   # wfa 0: no warm floor in clouds
    'strike':  dict(tmax=0.86, keepl=0.45, glow=1.0, sat=0.7, hot0=0.72, wht=1.02, sh=0.72, rbig=1.0, cfree=1.0,
                    clar=0.4, wcap=0.40, wk0=0.22, flash=1.0, tgam=1.5, tcap=0.62, sig_c=40.0, hdr=2.0, gq=0.55, wfa=0.0,
                    gt=('ARC_WHITE', 'ICE', 'ELECTRIC', 'ELECTRIC', 'VIOLET', 'DEEP_VIOLET')),
    'orbit':   dict(ag=1.0, ar=0.3, av=0.5,       # v4: + a little of the 630 nm red (极光 = 绿 + 一点红)
                    gtint=0.25, glow=0.5, gak=1.0, ausat=1.0, augain=1.45, blk=0.08, lo=0.36, hi=0.72),
    'grid':    dict(anyh=1.0, own=0.0, lo=0.30, hi=0.72, glow=1.0, rsat=1.0, tmax=0.78, keepl=0.35, hdr=2.0, bgam=1.3,
                    gq=0.42, gtint=1.0, gts=0.85, wfe=1.0,
                    gt=('ICE', 'ELECTRIC', 'ELECTRIC', 'VIOLET', 'VIOLET', 'DEEP_VIOLET')),
    'raw':     dict(own=1.0, sat=1.0, glow=0.0, keepl=1.0, tc=0.0),
}
# bright / daytime plates (overcast sky ~0.8): day-for-night -- the world is exposed down (wexp) so the sky keeps its
# layering UNDER the ceiling instead of being flattened against it; plates without lightning also pass mk=0
DAY = dict(wexp=0.5, clar=0.55, bgam=1.1, flash=0.3, wtint=0.0)     # the plate's sky is already lit: little extra fill
LOOKS['storm_day'] = dict(LOOKS['storm'], **DAY)
LOOKS['strike_day'] = dict(LOOKS['strike'], **DAY)


def params(look=None, **kw):
    p = dict(DEFAULT)
    if look:
        p.update(LOOKS[look])
    p.update(kw)
    return p


def volt_mask(E, img, look=None, ret_blur=False, blurs=None, **kw):
    """RGB mask of a plate: R = electric light (re-coloured along the ramp), G = aurora green, B = aurora red (own colour).
    ret_blur=True also returns the wide blur (sig_w) of the plate. blurs = (sig_s blur, sig_w blur) if already made."""
    p = params(look, **kw)
    bs, bw = blurs if blurs is not None else (E.blur(img, p['sig_s']), E.blur(img, p['sig_w']))
    cb = E.blur(run(E, CLIP_FS, {'a': img}, size=(img.w, img.h)), p['sig_c'])
    m = run(E, MASK_FS, {'a': img, 'bs': bs, 'bw': bw, 'cb': cb},
            {'lo': float(p['lo']), 'hi': float(p['hi']), 'vmix': float(p['vmix']), 'hue': tuple(map(float, p['hue'])),
             'hsoft': float(p['hsoft']), 'smin': float(p['smin']), 'hot0': float(p['hot0']), 'hotk': float(p['hotk']),
             'er': tuple(map(float, p['er'])), 'ew': tuple(map(float, p['ew'])), 'rd': tuple(map(float, p['rd'])),
             'rlo': float(p['rlo']), 'rbig': float(p['rbig']), 'emk': float(p['emk']), 'anyh': float(p['anyh']),
             'ag': float(p['ag']), 'ghue': tuple(map(float, p['ghue'])), 'ar': float(p['ar']),
             'rhue': tuple(map(float, p['rhue'])), 'av': float(p['av']), 'cfree': float(p['cfree']), 'vhue': tuple(map(float, p['vhue'])), 'alo': float(p['alo']), 'ahi': float(p['ahi']), 'k': float(p['mk'])},
            size=(img.w, img.h))
    if p['soft'] > 0.3:
        m = E.blur(m, p['soft'], fast=False)
    if (p['ag'] > 0 or p['ar'] > 0 or p['av'] > 0) and p['asoft'] > p['soft']:      # aurora: soft edges (dim, compressed skies are blocky)
        ma = E.blur(m, p['asoft'])
        m = run(E, 'uniform sampler2D a; uniform sampler2D b; void main(){ vec2 uv = UV(); '
                   'o = vec4(texture(a, uv).r, texture(b, uv).gb, texture(a, uv).a); }', {'a': m, 'b': ma}, size=(m.w, m.h))
    return (m, bw) if ret_blur else m


def _tint(name_or_rgb, sat=1.0):
    t = np.array(tint_lin(NAMED[name_or_rgb] if isinstance(name_or_rgb, str) else name_or_rgb))
    return tuple(float(v) for v in 1.0 + (t - 1.0) * float(sat))       # sat < 1: towards white (same luminance)


# ------------------------------------------------------------------ look v3: bloom, plate statistics, flash fill
BLOOM_N = 6
BLOOM_E = 1.25                     # global bloom energy (x glow): the halo carries glow x BLOOM_E of the source's energy
#                                    (v4: 1.0 -> 1.25 with the wide tail halved, gtail: brighter light, no haze in the blacks)
FLASH_G = 0.15                     # flash exposure -> linear light added on a fully lit cloud (albedo 1)
FLASH_GS = 0.25                    # v3 gain scale: v2 gains (60) saturated into a flat disc; x0.25 keeps the fall-off


def bloom_weights(q=0.5, n=BLOOM_N, tail=1.0):
    w = np.array([q ** i * (tail if i >= 3 else 1.0) for i in range(n)], np.float64)   # v4: tail < 1 = less haze
    return w / w.sum()


def _tint4(name_or_rgb, sat=1.0):
    """look v4: like _tint, names resolved in the L4 light palette first (the look's own glows / discharges)"""
    return _tint(L4.get(name_or_rgb, name_or_rgb) if isinstance(name_or_rgb, str) else name_or_rgb, sat)


def _tints6(names, sat):
    names = tuple(names) if names else GT6
    if len(names) < BLOOM_N:                                   # a v2 4-tuple: stretched over the six scales
        names = tuple(names[min(len(names) - 1, i * len(names) // BLOOM_N)] for i in range(BLOOM_N))
    return [_tint4(x, sat) for x in names[:BLOOM_N]]


def bloom(E, img, src, amount=1.0, s0=2.0, r=2.5, q=0.5, tints=GT6, sat=1.0, tk=0.9, sh=0.80, scale=1.0, rch=False,
          light_only=False, eh=EH, bsat=BSAT, bcm=BCM, tail=1.0):
    """energy-conserving multi-scale bloom of `src` (linear light; rch=True: intensity in R) added to img in linear light
    (+ shoulder). Scales s0 * r^i (i < 6, 1080p px x scale), weights q^i normalised to 1, x amount. light_only=True
    returns the bloom alone (linear, HDR). look v4: the tints colour COLD light only (neutral or inside the hue window eh);
    light with its own clear colour keeps it (chroma x bsat)."""
    if amount <= 0.003 or src is None:
        return img
    wts = bloom_weights(q, tail=tail) * float(amount)
    bl = [E.blur(src, float(s0) * float(r) ** i * float(scale)) for i in range(BLOOM_N)]
    t = _tints6(tints, sat)
    return run(E, BLOOM_FS, {'a': img if img is not None else src, 'g0': bl[0], 'g1': bl[1], 'g2': bl[2], 'g3': bl[3],
                             'g4': bl[4], 'g5': bl[5]},
               {'wa': tuple(float(x) for x in wts[:3]), 'wb': tuple(float(x) for x in wts[3:6]), 't0': t[0], 't1': t[1],
                't2': t[2], 't3': t[3], 't4': t[4], 't5': t[5], 'tk': float(tk), 'sh': float(sh),
                'mode': int(bool(light_only)), 'rch': int(bool(rch)), 'eh': tuple(map(float, eh)), 'bsat': float(bsat),
                'bcm': float(bcm), 'hbb': float(H_BB)}, size=(src.w, src.h))


SOFT0, NOISE0 = 1.0, 0.010          # fallbacks (1080p px of Gaussian blur, display-luma noise sigma)


def plate_stats(E, img):
    """-> (soft, noise) of a picture: its equivalent Gaussian blur (1080p px, re-blur method on edge pixels: the ratio of
    gradient energy after a 0.7 and a 2 px blur) and its pixel noise (display luma sigma, flat non-black areas).
    One small GPU reduction (mip chain, one texel read back); cached per rendered frame."""
    import moderngl
    cache = E.persistent(('volt_stats', 'cache'), dict)
    key = (getattr(E, 'frame_id', 0), img.w, img.h)
    if key in cache:
        return cache[key]
    t = E.persistent(('volt_stats', img.w, img.h), lambda: E.new((img.w, img.h), persistent=True))
    run(E, STATS_FS, {'a': img, 'b0': E.blur(img, 0.7, fast=False), 'b1': E.blur(img, 2.0, fast=False)}, out=t)
    t.gt.build_mipmaps()
    lvl = int(math.floor(math.log2(max(img.w, img.h))))
    v = np.frombuffer(t.gt.read(level=lvl, alignment=1), np.float16).astype(np.float64)[:4]
    t.gt.filter = (moderngl.LINEAR, moderngl.LINEAR)
    R, Gq, N, Fl = (float(x) for x in v)
    s0, sr = 0.92 * E.s, 2.09 * E.s                    # 0.7 / 2.0 px blurs incl. the central difference
    soft = SOFT0
    if R > 2e-3 and math.isfinite(R) and math.isfinite(Gq):
        Q = min(max(Gq / R, 0.0), 0.985)
        soft = math.sqrt(max((Q * Q * sr * sr - s0 * s0) / (1.0 - Q * Q), 0.0)) / E.s
    noise = math.sqrt(max(N, 0.0) / max(Fl, 1e-6) / 1000.0 / 0.52) if Fl > 2e-3 else NOISE0
    res = (min(max(soft, 0.5), 4.0), min(max(noise, 0.002), 0.06))
    if os.environ.get('VOLT_DEBUG'):
        print(f'  plate_stats F{key[0]} R {R:.4f} G {Gq:.4f} N {N:.4f} Fl {Fl:.3f} -> soft {res[0]:.2f} px noise {res[1]:.4f}', flush=True)
    cache.clear()
    cache[key] = res
    return res


def flash(E, img, src, k=1.0, sig=(40.0, 120.0, 320.0), w=(0.4, 0.35, 0.25), gain=60.0, cap=1.6, add=0.04,
          tint='ELECTRIC', tsat=0.25, sh=0.86, rch=False, gate=None, scale=1.0, dk=40.0, medium=None, alb=None,
          agam=1.5, ck=0.8, rk=0.7, G=None, tfar=1.0):
    """闪光补光 (flash fill; look v3): the lightning lights the clouds around it. src = the emitting light in linear light
    (volt(): its glow source; fx.discharge: the raw channel light, rch=True reads R). The source is softly dilated
    (blur 8 px x dk, clipped at 1: a dim 3 px bolt and a 200 px over-exposed column get comparable reach), gated to
    line-like light (gate = the electric mask: lamps are dots and do not flash), blurred at sig x gain (falls off with
    distance, log-compressed above cap). It lands on a MEDIUM -- the plate's own cloud (medium = the ungraded plate, or
    the picture itself): albedo knee alb (display luma; default (0.10, 0.85) for a plate, (0.03, 0.45) for a graded
    picture) ^ agam, billow tops (ck) and relief facing the light (rk) -- so gaps and the dark ground stay dark and the
    clouds are lit from the bolt's side, never a smooth disc pressed on the picture. + `add` = a little air glow near the
    channel. tint = a named colour pulled towards white by tsat (a breath of cold light). It follows the light: when the
    bolt dies, so does the fill."""
    if k <= 0.003 or src is None:
        return img
    b8 = E.blur(src, 8.0 * scale)
    g = E.blur(gate, 24.0 * scale) if gate is not None else b8
    d = run(E, FDIL_FS, {'a': b8, 'g': g}, {'dk': float(dk), 'rch': int(bool(rch)), 'useg': int(gate is not None)},
            size=(img.w, img.h))
    bl = [E.blur(d, float(s_) * scale) for s_ in sig]
    med = medium if medium is not None else img
    if alb is None:
        alb = (0.10, 0.85) if medium is not None else (0.03, 0.45)
    mb = E.blur(med, 3.0 * scale)
    ml = E.blur(med, 60.0 * scale)
    tx = E.blur(run(E, TEXM_FS, {'a': med, 'b': E.blur(med, 8.0 * scale)}, size=(med.w, med.h)), 24.0 * scale)
    txk = (0.006, 0.03) if medium is not None else (0.004, 0.02)
    return run(E, FLASH_FS, {'a': img, 'mb': mb, 'ml': ml, 'tx': tx, 'l0': bl[0], 'l1': bl[1], 'l2': bl[2]},
               {'w': tuple(map(float, w)), 'tint': _tint(tint, tsat), 'k': float(k), 'add': float(add), 'cap': float(cap),
                'sh': float(sh), 'gain': float(gain) * FLASH_GS, 'rch': 1, 'alb': tuple(map(float, alb)), 'agam': float(agam),
                'ck': float(ck), 'rk': float(rk), 'rr': 6.0 * float(scale), 'gd': 18.0 * float(scale),
                'G': float(FLASH_G if G is None else G), 'txk': txk, 'tsm': 0.3, 'tfar': float(tfar)}, size=(img.w, img.h))


def light_mask(E, img, p, blurs=None):
    """look v4: the any-hue emitter mask (R) -- the light that keeps its own colour (see LIGHTM_FS); x tc x mk"""
    bs, bw = blurs if blurs is not None else (E.blur(img, p['sig_s']), E.blur(img, p['sig_w']))
    lm = run(E, LIGHTM_FS, {'a': img, 'bs': bs, 'bw': bw},
             {'kn': tuple(map(float, p['lkn'])), 'er': tuple(map(float, p['ler'])), 'ew': tuple(map(float, p['lew'])),
              'rd': tuple(map(float, p['lrd'])), 'k': float(p['tc']) * float(p['mk'])}, size=(img.w, img.h))
    return E.blur(lm, p['soft'], fast=False) if p['soft'] > 0.3 else lm


def volt(E, img, look=None, mask=None, ret_mask=False, **kw):
    """THE filter. img = display-referred plate (any size; the result has the plate's size). look = LOOKS key; kw override
    DEFAULT. Returns the graded plate (and the mask with ret_mask=True).
    Order: mask -> B/W world (+ clarity, world ceiling, v4 mid lift) with the electric light re-coloured and every other
    emitter in its own colour (v4) -> flash fill (on the plate's own clouds) -> bloom (energy-conserving, 6 scales, cold
    light tinted electric, coloured light glowing in its own colour)."""
    p = params(look, **kw)
    bl = None
    if mask is not None:
        m = mask
    else:
        bl = (E.blur(img, p['sig_s']), E.blur(img, p['sig_w']))
        m = volt_mask(E, img, blurs=bl, **p)
    tcv = float(p['tc']) * float(p['mk'])
    lm = m                                                       # (bound but unused when tc = 0)
    if tcv > 0.003:
        if bl is None:
            bl = (E.blur(img, p['sig_s']), E.blur(img, p['sig_w']))
        lm = light_mask(E, img, p, bl)
    if bl is not None:
        lb = bl[1]
    else:
        lb = E.blur(img, p['sig_w']) if p['clar'] > 0 else img
    out = run(E, GRADE_FS, {'a': img, 'm': m, 'ramp': ramp_tex(E, p['ramp']), 'lb': lb, 'lm': lm},
              {'bw': tuple(map(float, p['bw'])), 'con': float(p['con']), 'bgam': float(p['bgam']), 'blk': float(p['blk']),
               'wht': float(p['wht']), 'bwk': float(p['bwk']), 'tint': tuple(map(float, p['tint'])),
               'clar': float(p['clar']), 'wexp': float(p['wexp']), 'wcap': float(p['wcap']), 'wk0': float(p['wk0']),
               'tlo': float(p['tlo']), 'thi': float(p['thi']), 'tgam': float(p['tgam']), 'tmax': float(p['tmax']),
               'own': float(p['own']), 'sat': float(p['sat']), 'keepl': float(p['keepl']), 'gain': float(p['gain']),
               'mgam': float(p['mgam']), 'sh': float(p['sh']), 'ausat': float(p['ausat']), 'augain': float(p['augain']),
               'rsat': float(p['rsat']), 'tcap': float(p['tcap']),
               'tc': float(p['tc']) if tcv > 0.003 else 0.0, 'lsat': float(p['lsat']), 'lcm': float(p['lcm']),
               'lgain': float(p['lgain']), 'eh': tuple(map(float, p['eh'])), 'eown': float(p['eown']),
               'wown': float(p['wown']), 'wcr': float(p['wcr']), 'wlift': float(p['wlift']),
               'wtint': float(p['wtint']) * min(1.0, float(p['mk'])), 'wtc': _tint4('LAVENDER', 0.55),
               'wh': tuple(map(float, p['wh'])), 'wfl': float(p['wfl']), 'wfe': float(p['wfe']), 'wpull': float(p['wpull']),
               'hbb': float(H_BB), 'wfa': float(p['wfa'])}, size=(img.w, img.h))
    src = None
    if p['glow'] > 0.003 or p['flash'] > 0.003:
        src = run(E, GLOWSRC_FS, {'a': out, 'm': m, 'lm': lm}, {'thr': float(p['gthr']), 'ak': float(p['gak']),
                                                                 'hdr': float(p['hdr']),
                                                                 'lk': float(p['lk']) if tcv > 0.003 else 0.0},
                  size=(img.w, img.h))
    if p['flash'] > 0.003:
        fsrc = src if tcv <= 0.003 else run(E, GLOWSRC_FS, {'a': out, 'm': m, 'lm': lm},
                                            {'thr': float(p['gthr']), 'ak': float(p['gak']), 'hdr': float(p['hdr']), 'lk': 0.0},
                                            size=(img.w, img.h))   # the flash comes from the ELECTRIC light only
        out = flash(E, out, fsrc, k=p['flash'], sig=p['fsig'], w=p['fw'], gain=p['fgain'], cap=p['fcap'], add=p['fadd'],
                    tint=p['ftint'], tsat=p['ftsat'], sh=max(float(p['sh']), 0.80), gate=m, medium=img, alb=p['falb'],
                    agam=p['fagam'], ck=p['fck'], rk=p['frk'], tfar=p['ftfar'])
    if p['glow'] > 0.003:
        out = bloom(E, out, src, float(p['glow']) * BLOOM_E, s0=p['gsig'], r=p['gr'], q=p['gq'], tints=p['gt'],
                    sat=p['gts'], tk=p['gtint'], sh=p['sh'], eh=p['eh'], tail=p['gtail'])
    return (out, m) if ret_mask else out


def plasma(E, img, ramp='plasma', bands=0, gain=1.25, bias=0.0, k=1.0, scurve=0.35):
    """plasma false colour (replaces IGNITION's thermal): luma -> ink -> violet -> electric -> ice -> arc white;
    bands > 0 = iso-brightness bands"""
    from src.kit.fx import false_color
    return false_color(E, img, ramp, k=k, gain=gain, bias=bias, scurve=scurve, bands=bands)


if __name__ == '__main__':
    print('hues (OKLab deg, from CIE 1931 2-deg CMF):')
    for n in ('H_N2_2P', 'H_N2P_1N', 'H_CHANNEL', 'H_OI_GREEN', 'H_OI_RED'):
        print(f'  {n:11s} {globals()[n]:6.1f}')
    for n, c in NAMED.items():
        print(f'{n:13s} {hexof(c)}  rgb {tuple(round(v, 3) for v in c)}')
    for r in ('volt', 'volt_hi', 'plasma', 'volt_neg'):
        print(r, RAMPS[r])
    print('series kit (src/fx/series.py):', f'H_FILAMENT {H_FILAMENT:.1f}  H_BB {H_BB:.1f}  H_PLASMA {H_PLASMA:.1f}')
    for n, c in KIT.items():
        print(f'  {n:13s} {hexof(c)}')
    for r in ('plasma4', 'spark4'):
        print(r, RAMPS[r])
