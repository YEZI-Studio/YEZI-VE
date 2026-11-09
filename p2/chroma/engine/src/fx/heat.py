# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""IGNITION signature look: the world is black & white, only HEAT has colour.

  blackbody(K) -> sRGB      Planck's law integrated with the CIE 1931 2-degree colour-matching functions (CVRL table
                            assets/data/ciexyz31_1.csv, 1 nm, 360-830 nm) -> XYZ -> linear sRGB (D65) -> negatives clipped
                            (out of gamut below ~1900 K) -> max channel = 1 -> sRGB encoded. Table 800-12000 K every 10 K
                            in assets/data/blackbody_srgb.npy (+ .json with xy for checking). `python -m src.fx.heat` prints
                            the check against the Planckian-locus chromaticities.
  EMBER .. BLUE_WHITE        named heat colours from the table (never type a heat hex)
  heat ramps                 'heat' (ink -> dull red 800 K -> orange 1900 K -> amber 3000 K -> white-hot 6000 K -> blue-white
                            12000 K, brightness rising with T), 'heat_hi', 'thermal_bb'; registered in kit.core.RAMPS
  heat(E, img, look, **kw)   the filter: neutral B/W world + heat sources keep their own colour (or are re-coloured along
                            the blackbody curve by brightness: bbk), soft mask edges, heat lift, optional glow
  heat_mask(E, img, **kw)    just the mask (R)
  thermal(E, img, ...)       thermal-camera false colour (impact style 'thermal')
Mask = warm hue (HSV -25..70 deg, soft) x saturation gate x brightness knee (mostly luma: vmix 0.35 of the max channel), +
near-white cores, + optional blue band (gas flame / welding arc: blue=1), + optional red-hot band (glowing steel has low luma:
redk=1, on in 'forge'). Not over-saturated by default (over-done colour reads cheap).
"""
import os, json, math
import numpy as np
from src.kit.core import run, RAMPS, ramp_array, INK, PAPER, hx, _srgb2lin, _lin2srgb

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CMF_CSV = f'{ROOT}/assets/data/ciexyz31_1.csv'
TABLE = f'{ROOT}/assets/data/blackbody_srgb.npy'
T_MIN, T_MAX, T_STEP = 800.0, 12000.0, 10.0
C1 = 3.741771852e-16        # 2*pi*h*c^2 (W m^2); the constant cancels in chromaticity
C2 = 1.438776877e-2         # h*c/k (m K), CODATA 2018
XYZ2RGB = np.array([[3.2404542, -1.5371385, -0.4985314],     # IEC 61966-2-1 sRGB (D65)
                    [-0.9692660, 1.8760108, 0.0415560],
                    [0.0556434, -0.2040259, 1.0572252]])


# ------------------------------------------------------------------ blackbody table
def _cmf():
    d = np.loadtxt(CMF_CSV, delimiter=',')
    return d[:, 0] * 1e-9, d[:, 1:4]


def blackbody_xyz(T):
    lam, cmf = _cmf()
    B = C1 / lam ** 5 / (np.exp(C2 / (lam * float(T))) - 1.0)
    return (B[:, None] * cmf).sum(0)


def _build():
    lam, cmf = _cmf()
    Ts = np.arange(T_MIN, T_MAX + 1e-6, T_STEP)
    B = C1 / lam[None] ** 5 / (np.exp(C2 / (lam[None] * Ts[:, None])) - 1.0)
    XYZ = B @ cmf
    xy = XYZ[:, :2] / XYZ.sum(1, keepdims=True)
    rgb = XYZ @ XYZ2RGB.T
    rgb = np.clip(rgb, 0.0, None)
    rgb /= rgb.max(1, keepdims=True)
    srgb = _lin2srgb(rgb).astype(np.float32)
    os.makedirs(os.path.dirname(TABLE), exist_ok=True)
    np.save(TABLE, srgb)
    json.dump({'source': 'Planck x CIE 1931 2-deg CMF (CVRL ciexyz31_1.csv), sRGB D65, max channel = 1, negatives clipped',
               'T': [T_MIN, T_MAX, T_STEP], 'check_xy': {str(int(t)): [round(float(v), 4) for v in xy[int((t - T_MIN) / T_STEP)]]
                                                       for t in (1000, 1500, 2000, 2500, 3000, 4000, 5000, 5772, 6000, 6500, 10000)},
               'hex': {str(int(t)): '#%02X%02X%02X' % tuple(int(round(v * 255)) for v in srgb[int((t - T_MIN) / T_STEP)])
                       for t in (800, 1000, 1500, 1900, 2500, 3000, 4000, 5772, 6000, 6500, 8000, 10000, 12000)}},
              open(TABLE.replace('.npy', '.json'), 'w'), indent=1)
    return srgb


_TAB = np.load(TABLE) if os.path.exists(TABLE) else _build()


def blackbody(T):
    """sRGB (0..1, max channel = 1) of a blackbody at T kelvin (800..12000, linear interpolation in the 10 K table)"""
    x = (min(max(float(T), T_MIN), T_MAX) - T_MIN) / T_STEP
    i = min(int(x), len(_TAB) - 2); f = x - i
    return tuple(float(v) for v in _TAB[i] * (1 - f) + _TAB[i + 1] * f)


def bb_dim(T, v):
    """blackbody colour at brightness v (scaled in linear light)"""
    return tuple(float(x) for x in _lin2srgb(_srgb2lin(blackbody(T)) * float(v)))


# heat colours: dull red ~800 K -> orange ~1900 K (match) -> amber ~3000 K -> white-hot ~6000 K -> blue-white
EMBER = bb_dim(800, 0.55)          # dull red glow (dark)
RED_HOT = blackbody(1100)
ORANGE = blackbody(1900)           # match / candle flame (candle ~1850 K)
AMBER = blackbody(3000)
WHITE_HOT = blackbody(6000)
SUN = blackbody(5772)              # IAU nominal effective temperature of the Sun
BLUE_WHITE = blackbody(12000)
HEAT_ORANGE = blackbody(1700)      # the second colour of duo / post3 impact frames


def heat_ramp_array(n=256, t_lo=800.0, t_hi=12000.0, ink=True, v_lo=0.5):
    """ink -> blackbody(t_lo) dim -> ... -> blackbody(t_hi); T log-spaced; brightness rises until ~3000 K (linear light)"""
    u = np.linspace(0, 1, n)
    out = np.zeros((n, 3))
    k0 = 0.12 if ink else 0.0
    for j, x in enumerate(u):
        if x < k0:
            a = _srgb2lin(np.array(INK)); b = _srgb2lin(np.array(blackbody(t_lo))) * v_lo
            out[j] = a + (b - a) * (x / k0) ** 1.2
        else:
            y = (x - k0) / (1 - k0)
            T = math.exp(math.log(t_lo) + (math.log(t_hi) - math.log(t_lo)) * y)
            v = v_lo + (1 - v_lo) * min(1.0, math.log(T / t_lo) / math.log(max(3000.0 / t_lo, 1.0001))) ** 0.8
            out[j] = _srgb2lin(np.array(blackbody(T))) * v
    return np.ascontiguousarray(_lin2srgb(out)[None].astype(np.float32))


def _hexs(a):
    return ['#%02X%02X%02X' % tuple(int(round(v * 255)) for v in c) for c in a]


RAMPS['heat'] = _hexs(heat_ramp_array(17)[0])
RAMPS['heat_hi'] = _hexs(heat_ramp_array(9, 1900.0, 12000.0, ink=False, v_lo=1.0)[0])
RAMPS['thermal_bb'] = ['#050508', '#141238'] + _hexs(heat_ramp_array(9, 900.0, 9000.0, ink=False, v_lo=0.55)[0])


def bb_tex(E):
    """the 800-12000 K table as a 1121 x 1 RGB32F texture (GLSL: bb(T) from BB_GLSL)"""
    return E.persistent(('bbtab',), lambda: E.texture_from(np.ascontiguousarray(_TAB[None].astype(np.float32))))


BB_GLSL = f'''uniform sampler2D bbt;
vec3 bb(float T){{ float n = {len(_TAB)}.0; float x = clamp((T - {T_MIN:.1f}) / {T_MAX - T_MIN:.1f}, 0.0, 1.0) * (n - 1.0);
  return texture(bbt, vec2((x + 0.5) / n, 0.5)).rgb; }}
'''

# ------------------------------------------------------------------ the filter
MASK_FS = '''uniform sampler2D a; uniform float lo; uniform float hi; uniform vec2 hue; uniform float hsoft; uniform float smin;
uniform float hot0; uniform float hotk; uniform float blue; uniform vec2 bhue; uniform float k; uniform float vmix;
uniform float redk; uniform vec2 rhue;
float hdist(float h, vec2 w){ float c = 0.5 * (w.x + w.y); float hw = 0.5 * (w.y - w.x);
  float d = abs(mod(h - c + 540.0, 360.0) - 180.0); return max(d - hw, 0.0); }
void main(){ vec3 c = texture(a, UV()).rgb; float mx = max(c.r, max(c.g, c.b)); float mn = min(c.r, min(c.g, c.b));
  float s = (mx - mn) / max(mx, 1e-4); float h = 0.0; float d = mx - mn;
  if (d > 1e-5){ if (mx == c.r) h = mod((c.g - c.b) / d, 6.0); else if (mx == c.g) h = (c.b - c.r) / d + 2.0; else h = (c.r - c.g) / d + 4.0; }
  h *= 60.0;
  float wv = smoothstep(lo, hi, mix(luma(c), mx, vmix));      // brightness knee: mostly luma -> colour only in the highlights
  float ws = smoothstep(smin, smin + 0.16, s);
  float warm = (1.0 - smoothstep(0.0, hsoft, hdist(h, hue))) * ws * wv;
  float bl = blue * (1.0 - smoothstep(0.0, hsoft, hdist(h, bhue))) * smoothstep(smin, smin + 0.16, s) * wv;
  float y = luma(c);
  float hot = hotk * smoothstep(hot0, 1.0, y) * smoothstep(0.02, 0.10, c.r - c.b + 0.06);
  float red = redk * (1.0 - smoothstep(0.0, hsoft, hdist(h, rhue))) * smoothstep(0.45, 0.7, s) * smoothstep(0.55, 0.92, mx);
  o = vec4(vec3(clamp(max(max(warm, bl), max(hot, red)) * k, 0.0, 1.0)), 1.0); }'''

HEAT_FS = BB_GLSL + '''uniform sampler2D a; uniform sampler2D m; uniform float bwk; uniform vec3 bw; uniform float con; uniform float bgam;
uniform float blk; uniform float wht; uniform vec3 tint; uniform float sat; uniform float gain; uniform float bbk;
uniform float tlo; uniform float thi; uniform float tgam; uniform float mgam;
void main(){ vec2 uv = UV(); vec3 c = texture(a, uv).rgb; float mm = pow(clamp(texture(m, uv).r, 0.0, 1.0), mgam);
  float y = dot(c, bw);
  float g = clamp((y - blk) / max(wht - blk, 1e-3), 0.0, 1.0); g = pow(g, bgam); g = mix(g, g * g * (3.0 - 2.0 * g), con);
  vec3 world = mix(c, vec3(g) * tint, bwk);
  float yl = luma(c);
  vec3 hc = clamp(vec3(yl) + (c - vec3(yl)) * sat, 0.0, 1.0);
  float mx = max(c.r, max(c.g, c.b));
  float T = exp(mix(log(tlo), log(thi), pow(clamp(yl, 0.0, 1.0), tgam)));
  vec3 bc = bb(T) * mx;
  vec3 heat = enc(lin(mix(hc, bc, bbk)) * gain);
  o = vec4(clamp(mix(world, heat, mm), 0.0, 1.0), 1.0); }'''

GLOWSRC_FS = '''uniform sampler2D a; uniform sampler2D m; uniform float thr;
void main(){ vec3 c = texture(a, UV()).rgb; float mm = texture(m, UV()).r; o = vec4(c * mm * smoothstep(thr, 1.0, max(c.r, max(c.g, c.b))), 1.0); }'''

DEFAULT = dict(lo=0.45, hi=0.80, vmix=0.35, hue=(-25.0, 70.0), hsoft=16.0, smin=0.16, hot0=0.82, hotk=1.0, blue=0.0,
               bhue=(185.0, 250.0), redk=0.0, rhue=(-15.0, 22.0),
               mk=1.0, soft=1.6, mgam=1.0,
               bwk=1.0, bw=(0.2126, 0.7152, 0.0722), con=0.25, bgam=1.0, blk=0.03, wht=0.97, tint=(1.0, 1.0, 1.0),
               sat=0.92, gain=1.0, bbk=0.0, tlo=900.0, thi=6500.0, tgam=1.0, glow=0.0, glow_sigma=26.0, glow_thr=0.45)
# film temperature presets (colours climb with the film): opening dark red/orange -> DROP white-hot -> SUN blue
LOOKS = {
    'neutral': {},
    'spark':   dict(bbk=0.45, tlo=800.0, thi=2600.0, sat=0.88, glow=0.18),
    'forge':   dict(bbk=0.35, tlo=900.0, thi=4000.0, sat=0.9, glow=0.22, gain=1.05, redk=1.0),
    'drop':    dict(bbk=0.3, tlo=1200.0, thi=8000.0, sat=0.92, glow=0.3, gain=1.12, hot0=0.75),
    'sun':     dict(bbk=0.5, tlo=1900.0, thi=12000.0, blue=1.0, sat=0.95, glow=0.32, gain=1.1),
    'raw':     dict(bbk=0.0, sat=1.0, glow=0.0),
}


def params(look=None, **kw):
    p = dict(DEFAULT)
    if look:
        p.update(LOOKS[look])
    p.update(kw)
    return p


def heat_mask(E, img, look=None, **kw):
    """heat mask of a plate (R = 0..1), softened by `soft` px"""
    p = params(look, **kw)
    m = run(E, MASK_FS, {'a': img}, {'lo': float(p['lo']), 'hi': float(p['hi']), 'hue': tuple(map(float, p['hue'])),
                                     'hsoft': float(p['hsoft']), 'smin': float(p['smin']), 'hot0': float(p['hot0']),
                                     'hotk': float(p['hotk']), 'blue': float(p['blue']), 'bhue': tuple(map(float, p['bhue'])),
                                     'k': float(p['mk']), 'vmix': float(p['vmix']), 'redk': float(p['redk']),
                                     'rhue': tuple(map(float, p['rhue']))}, size=(img.w, img.h))
    if p['soft'] > 0.3:
        m = E.blur(m, p['soft'], fast=False)          # (output-size result; sampled by UV, so any plate size works)
    return m


def heat(E, img, look=None, mask=None, ret_mask=False, **kw):
    """THE filter. img = display-referred plate (any size; output has the plate's size). look = LOOKS key; kw override
    DEFAULT. Returns the graded plate (and the mask with ret_mask=True)."""
    p = params(look, **kw)
    m = mask if mask is not None else heat_mask(E, img, **{k: v for k, v in p.items()})
    out = run(E, HEAT_FS, {'a': img, 'm': m, 'bbt': bb_tex(E)},
              {'bwk': float(p['bwk']), 'bw': tuple(map(float, p['bw'])), 'con': float(p['con']), 'bgam': float(p['bgam']),
               'blk': float(p['blk']), 'wht': float(p['wht']), 'tint': tuple(map(float, p['tint'])), 'sat': float(p['sat']),
               'gain': float(p['gain']), 'bbk': float(p['bbk']), 'tlo': float(p['tlo']), 'thi': float(p['thi']),
               'tgam': float(p['tgam']), 'mgam': float(p['mgam'])}, size=(img.w, img.h))
    if p['glow'] > 0.003:
        src = run(E, GLOWSRC_FS, {'a': out, 'm': m}, {'thr': float(p['glow_thr'])}, size=(img.w, img.h))
        out = E.screen(out, E.blur(src, p['glow_sigma']), p['glow']) if (img.w, img.h) == (E.W, E.H) else \
            run(E, 'uniform sampler2D a; uniform sampler2D b; uniform float k; void main(){ vec3 A = texture(a, UV()).rgb; '
                   'vec3 B = texture(b, UV()).rgb; o = vec4(mix(A, 1.0 - (1.0 - A) * (1.0 - B), k), 1.0); }',
                {'a': out, 'b': E.blur(src, p['glow_sigma'])}, {'k': float(p['glow'])}, size=(img.w, img.h))
    return (out, m) if ret_mask else out


def thermal(E, img, ramp='thermal', bands=0, gain=1.2, bias=0.0, k=1.0):
    """thermal-camera false colour (ramp 'thermal' = ironbow toned to this film, or 'thermal_bb'); bands > 0 = isotherms"""
    from src.kit.fx import false_color
    return false_color(E, img, ramp, k=k, gain=gain, bias=bias, scurve=0.35, bands=bands)


if __name__ == '__main__':
    ref = {1000: (0.6528, 0.3444), 1500: (0.5857, 0.3931), 2000: (0.5267, 0.4133), 2500: (0.4770, 0.4137),
           3000: (0.4369, 0.4041), 4000: (0.3805, 0.3768), 5000: (0.3451, 0.3516), 6000: (0.3221, 0.3318),
           10000: (0.2807, 0.2884)}
    print('T(K)   x       y       ref x   ref y   |d|      sRGB')
    for T, (rx, ry) in ref.items():
        X = blackbody_xyz(T); x, y = X[0] / X.sum(), X[1] / X.sum()
        print(f'{T:5d}  {x:.4f}  {y:.4f}  {rx:.4f}  {ry:.4f}  {math.hypot(x - rx, y - ry):.5f}  '
              + '#%02X%02X%02X' % tuple(int(round(v * 255)) for v in blackbody(T)))
    for n in ('EMBER', 'RED_HOT', 'ORANGE', 'HEAT_ORANGE', 'AMBER', 'SUN', 'WHITE_HOT', 'BLUE_WHITE'):
        c = globals()[n]; print(f'{n:12s} ' + '#%02X%02X%02X' % tuple(int(round(v * 255)) for v in c))
    print('heat ramp', RAMPS['heat'])
