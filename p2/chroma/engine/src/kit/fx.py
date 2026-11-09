# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""kit fx: 2.5D virtual camera, colour operations, transitions. Every function returns a NEW texture
(premultiplied RGBA16F) unless it says 'in place'. Colour maths in display space; motion-blur taps averaged in linear light.
lin()/enc() come from the engine HEADER (do not redefine them in shaders)."""
import math
import numpy as np
import moderngl
from src.kit.core import run, mat3s, ramp_tex, W, H, PAPER, INK, clamp01


# ------------------------------------------------------------------ 2.5D camera
def rot3(rx=0.0, ry=0.0, rz=0.0):
    rx, ry, rz = map(math.radians, (rx, ry, rz))
    X = np.array([[1, 0, 0], [0, math.cos(rx), -math.sin(rx)], [0, math.sin(rx), math.cos(rx)]])
    Y = np.array([[math.cos(ry), 0, math.sin(ry)], [0, 1, 0], [-math.sin(ry), 0, math.cos(ry)]])
    Z = np.array([[math.cos(rz), -math.sin(rz), 0], [math.sin(rz), math.cos(rz), 0], [0, 0, 1]])
    return Z @ Y @ X


def pose_H(zoom=1.0, roll=0.0, rx=0.0, ry=0.0, piv=(W / 2, H / 2), at=(W / 2, H / 2), off=(0.0, 0.0), D=1900.0, src=None):
    """forward homography SOURCE px -> OUTPUT px: the source point `piv` lands on screen at `at`+`off`, scaled by zoom,
    src=(w, h): the source is a plate of that size (e.g. 3840x2160): piv stays in 1080p units of the plate and zoom=1
    fills the frame -- the same pose works for a 1080p or a 2160p plate."""
    if src is not None and (src[0] != W or src[1] != H):
        k = src[0] / W
        Hm = pose_H(zoom / k, roll, rx, ry, (piv[0] * k, piv[1] * k), at, off, D * k)
        return Hm
    T0 = np.array([[1, 0, -piv[0]], [0, 1, -piv[1]], [0, 0, 1.0]])
    R = rot3(rx, ry, 0.0)
    Tilt = np.array([[R[0, 0], R[0, 1], 0], [R[1, 0], R[1, 1], 0], [R[2, 0] / D, R[2, 1] / D, 1.0]])
    c, s = math.cos(math.radians(roll)), math.sin(math.radians(roll))
    Rz = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])
    S = np.diag([zoom, zoom, 1.0])
    T1 = np.array([[1, 0, at[0] + off[0]], [0, 1, at[1] + off[1]], [0, 0, 1.0]])
    return T1 @ S @ Rz @ Tilt @ T0


def apply_H(Hm, x, y):
    v = Hm @ np.array([x, y, 1.0]); return v[0] / v[2], v[1] / v[2]


CAM_FS = '''uniform sampler2D src; uniform mat3 Hi[16]; uniform int n; uniform vec2 ssize; uniform vec4 bg; uniform int edge;
void main(){
  vec4 acc = vec4(0.0);
  for (int i = 0; i < 16; i++){
    if (i >= n) break;
    vec3 q = Hi[i] * vec3(gl_FragCoord.xy, 1.0);
    vec2 p = q.xy / q.z;
    float cov = 1.0;
    if (edge == 0){ vec2 e = min(p, ssize - p); cov = clamp(min(e.x, e.y) + 0.5, 0.0, 1.0); }
    if (q.z <= 0.0) cov = 0.0;
    vec4 c = texture(src, clamp(p, vec2(0.5), ssize - vec2(0.5)) / ssize);
    vec3 cl = c.a > 1e-5 ? lin(c.rgb / c.a) * c.a : vec3(0.0);
    acc += vec4(cl, c.a) * cov + vec4(lin(bg.rgb) * bg.a, bg.a) * (1.0 - cov);
  }
  acc /= float(n);
  o = acc.a > 1e-5 ? vec4(enc(acc.rgb / acc.a) * acc.a, acc.a) : vec4(0.0);
}'''


def camera(E, src, Hs, bg=(0.0, 0.0, 0.0, 0.0), edge='bg', out=None, mip=None):
    """resample `src` (opaque footage or premultiplied layer) through forward homographies Hs (list = motion-blur taps,
    averaged in linear light). edge='bg' -> outside the source shows bg (rgba, straight); 'clamp' -> smear edge pixels.
    mip: trilinear minification (default: on when the source is larger than the output, e.g. a 2160p plate)."""
    Hs = list(Hs)[:16]
    Hi = [np.linalg.inv(M) for M in Hs]
    if mip is None:
        mip = src.w > E.W
    if mip:
        E.mips(src)
    res = run(E, CAM_FS, {'src': src}, {'n': len(Hi), 'ssize': (float(src.w), float(src.h)),
                                        'bg': tuple(map(float, bg)), 'edge': 0 if edge == 'bg' else 1},
              out=out, arrays={'Hi': mat3s(Hi, 16)})
    return res


def cam(E, T, src, pose_fn, n=8, **kw):
    """camera with taps inside this sub-frame: pose_fn(f) -> dict for pose_H (or a 3x3 matrix). The plate size is passed
    to pose_H automatically (src=(w, h)), so poses are written in 1080p units for any tier."""
    Hs = []
    for f in T.taps(n):
        p = pose_fn(f)
        if not isinstance(p, np.ndarray):
            p = dict(p); p.setdefault('src', (src.w, src.h)); p = pose_H(**p)
        Hs.append(p)
    return camera(E, src, Hs, **kw)


def shake(f, f0, amp, decay=5.0, seed=0, freq=0.9):
    """impact shake offset (x, y, roll radians) started at frame f0, decaying"""
    t = f - f0
    if t < 0:
        return 0.0, 0.0, 0.0
    e = amp * math.exp(-t / decay)
    return (e * math.sin(t * freq * 2.1 + seed), e * 0.7 * math.cos(t * freq * 2.7 + seed * 1.3),
            e * 0.012 * math.sin(t * freq * 1.7 + seed * 0.7))


# ------------------------------------------------------------------ colour
FALSE_FS = '''uniform sampler2D a; uniform sampler2D ramp; uniform sampler2D m; uniform int has_m; uniform float k;
uniform float gain; uniform float bias; uniform float scurve; uniform int invm; uniform float bands;
void main(){ vec2 uv = UV(); vec4 c = texture(a, uv); vec3 rgb = c.a > 1e-5 ? c.rgb / c.a : vec3(0.0);
  float y = luma(rgb); float t = clamp((y - 0.5) * gain + 0.5 + bias, 0.0, 1.0);
  t = mix(t, t * t * (3.0 - 2.0 * t), scurve);
  if (bands > 0.5) t = (floor(t * bands) + 0.5) / bands;
  vec3 f = texture(ramp, vec2(t, 0.5)).rgb; float w = k;
  if (has_m == 1){ float mm = texture(m, uv).r; if (invm == 1) mm = 1.0 - mm; w *= mm; }
  o = vec4(mix(rgb, f, w) * c.a, c.a); }'''


def false_color(E, img, ramp='thermal', k=1.0, gain=1.15, bias=0.0, scurve=0.45, mask=None, invert_mask=False, bands=0):
    """gradient map on luma through an OKLab ramp (name in kit.core.RAMPS / fx.heat ramps, or list of stops);
    bands > 0 posterizes into that many isotherm bands (thermal-camera look)"""
    return run(E, FALSE_FS, {'a': img, 'ramp': ramp_tex(E, ramp), 'm': mask if mask is not None else img},
               {'has_m': int(mask is not None), 'k': float(k), 'gain': float(gain), 'bias': float(bias),
                'scurve': float(scurve), 'invm': int(invert_mask), 'bands': float(bands)})


BW_FS = '''uniform sampler2D a; uniform float lo; uniform float hi; uniform float s; uniform vec3 c0; uniform vec3 c1; uniform float k;
void main(){ vec4 c = texture(a, UV()); vec3 rgb = c.a > 1e-5 ? c.rgb / c.a : vec3(0.0);
  float y = clamp((luma(rgb) - lo) / (hi - lo), 0.0, 1.0); y = mix(y, y * y * (3.0 - 2.0 * y), s);
  o = vec4(mix(rgb, mix(c0, c1, y), k) * c.a, c.a); }'''


def mono(E, img, lo=0.05, hi=0.92, s=0.5, c0=(0.0, 0.0, 0.0), c1=(1.0, 1.0, 1.0), k=1.0):
    return run(E, BW_FS, {'a': img}, {'lo': float(lo), 'hi': float(hi), 's': float(s), 'c0': tuple(c0), 'c1': tuple(c1), 'k': float(k)})


HUE_FS = '''uniform sampler2D a; uniform float ang; uniform float sat; uniform float k;
void main(){ vec4 c = texture(a, UV()); vec3 rgb = c.a > 1e-5 ? c.rgb / c.a : vec3(0.0);
  float y = luma(rgb); vec2 cb = cbcr(rgb); float cs = cos(ang), sn = sin(ang);
  cb = vec2(cs * cb.x - sn * cb.y, sn * cb.x + cs * cb.y) * sat;
  vec3 r = vec3(y + 1.5748 * cb.y, y - 0.1873 * cb.x - 0.4681 * cb.y, y + 1.8556 * cb.x);
  o = vec4(mix(rgb, clamp(r, 0.0, 1.0), k) * c.a, c.a); }'''


def hue(E, img, deg=0.0, sat=1.0, k=1.0):
    return run(E, HUE_FS, {'a': img}, {'ang': math.radians(deg), 'sat': float(sat), 'k': float(k)})


def mix_mask(E, a, b, mask, Ai=np.eye(3), k=1.0):
    """a -> b where mask (R channel; geometry mapped by Ai: image px -> mask px)"""
    fs = '''uniform sampler2D a; uniform sampler2D b; uniform sampler2D m; uniform mat3 Ai; uniform vec2 msize; uniform float k;
    void main(){ vec3 q = Ai * vec3(gl_FragCoord.xy, 1.0); float mm = texture(m, q.xy / msize).r * k;
      o = mix(texture(a, UV()), texture(b, UV()), mm); }'''
    return run(E, fs, {'a': a, 'b': b, 'm': mask}, {'Ai': tuple(np.asarray(Ai, np.float32).T.flatten()),
                                                    'msize': (float(mask.w), float(mask.h)), 'k': float(k)})


# ------------------------------------------------------------------ transitions / impacts
DIRBLUR_FS = '''uniform sampler2D a; uniform vec2 v; uniform int n;
void main(){ vec4 acc = vec4(0.0);
  for (int i = 0; i < 48; i++){ if (i >= n) break; float t = (float(i) + 0.5) / float(n) - 0.5;
    vec4 c = texture(a, (gl_FragCoord.xy + v * t) / u_size); acc += vec4(c.a > 1e-5 ? lin(c.rgb / c.a) * c.a : vec3(0.0), c.a); }
  acc /= float(n); o = acc.a > 1e-5 ? vec4(enc(acc.rgb / acc.a) * acc.a, acc.a) : vec4(0.0); }'''


def dir_blur(E, img, vx, vy, n=None):
    """directional (whip) blur over the vector (vx, vy) px, linear light"""
    L = math.hypot(vx, vy)
    if L < 0.75:
        return img
    n = n or int(min(48, max(4, L / 3)))
    return run(E, DIRBLUR_FS, {'a': img}, {'v': (float(vx), float(vy)), 'n': n})


ZOOMBLUR_FS = '''uniform sampler2D a; uniform vec2 c; uniform float amt; uniform int n;
void main(){ vec4 acc = vec4(0.0); vec2 p = gl_FragCoord.xy;
  for (int i = 0; i < 40; i++){ if (i >= n) break; float t = (float(i) + 0.5) / float(n);
    vec4 s = texture(a, (c + (p - c) * (1.0 - amt * t)) / u_size); acc += vec4(s.a > 1e-5 ? lin(s.rgb / s.a) * s.a : vec3(0.0), s.a); }
  acc /= float(n); o = acc.a > 1e-5 ? vec4(enc(acc.rgb / acc.a) * acc.a, acc.a) : vec4(0.0); }'''


def zoom_blur(E, img, cx, cy, amt, n=None):
    """radial (push-in) blur toward (cx, cy); amt = fraction of the radius smeared"""
    if amt < 0.002:
        return img
    n = n or int(min(40, max(6, amt * 160)))
    return run(E, ZOOMBLUR_FS, {'a': img}, {'c': (float(cx), float(cy)), 'amt': float(amt), 'n': n})


TEAR_FS = '''uniform sampler2D a; uniform float amt; uniform float seed; uniform float stretch; uniform float shift;
float h1(float n){ return fract(sin(n * 127.1 + seed * 311.7) * 43758.5453); }
void main(){ vec2 p = gl_FragCoord.xy; float blk = floor(p.y / 90.0);
  float bh = 3.0 + 46.0 * h1(blk * 1.7 + 0.3); float b = floor((p.y + h1(blk) * 40.0) / bh) + blk * 31.0;
  float on = step(1.0 - amt, h1(b * 0.37 + 2.0));
  float anchor = h1(b * 3.1 + 5.0) * u_size.x; float dir = h1(b * 7.7) < 0.5 ? -1.0 : 1.0;
  float d = (p.x - anchor) * dir;
  float sx = d > 0.0 ? anchor + dir * d * (1.0 - stretch) : p.x;
  sx = mix(p.x, sx, on) + on * (h1(b * 9.1) - 0.5) * shift;
  o = texture(a, vec2(sx, p.y) / u_size); }'''


def tear(E, img, amt, seed=0.0, stretch=0.97, shift=260.0):
    """horizontal pixel tearing: amt = fraction of bands torn; torn bands smear one column into long streaks"""
    if amt <= 0.001:
        return img
    return run(E, TEAR_FS, {'a': img}, {'amt': float(clamp01(amt)), 'seed': float(seed), 'stretch': float(stretch), 'shift': float(shift)})


RGB_FS = '''uniform sampler2D a; uniform vec2 c; uniform float amt; uniform vec2 dir;
void main(){ vec2 p = gl_FragCoord.xy; vec2 d = p - c;
  vec4 r = texture(a, (c + d * (1.0 + amt) + dir) / u_size); vec4 g = texture(a, p / u_size); vec4 b = texture(a, (c + d * (1.0 - amt) - dir) / u_size);
  o = vec4(r.r, g.g, b.b, max(max(r.a, g.a), b.a)); }'''


def rgb_split(E, img, amt, cx=W / 2, cy=H / 2, dx=0.0, dy=0.0):
    """RGB separation: radial (amt = scale offset, e.g. 0.01) + directional (dx, dy px)"""
    if abs(amt) < 1e-5 and abs(dx) + abs(dy) < 0.2:
        return img
    return run(E, RGB_FS, {'a': img}, {'c': (float(cx), float(cy)), 'amt': float(amt), 'dir': (float(dx), float(dy))})


def flash(E, img, rgb=(1, 1, 1), k=1.0):
    return E.mix(img, E.solid(rgb), float(k)) if k > 0 else img


INV_FS = '''uniform sampler2D a; uniform float k; void main(){ vec4 c = texture(a, UV()); vec3 rgb = c.a > 1e-5 ? c.rgb / c.a : vec3(0.0);
  o = vec4(mix(rgb, 1.0 - rgb, k), 1.0) * c.a; }'''


def invert(E, img, k=1.0):
    return run(E, INV_FS, {'a': img}, {'k': float(k)})


CIRC_FS = '''uniform sampler2D a; uniform sampler2D b; uniform vec2 c; uniform float r; uniform vec3 lc; uniform float lw; uniform float sq;
void main(){ vec2 d = gl_FragCoord.xy - c; d.y *= sq; float dist = length(d);
  float m = clamp(r - dist + 0.5, 0.0, 1.0); vec4 col = mix(texture(a, UV()), texture(b, UV()), m);
  if (lw > 0.0){ float l = clamp(lw * 0.5 - abs(dist - r) + 0.5, 0.0, 1.0); col = mix(col, vec4(lc, 1.0), l); }
  o = col; }'''


def wipe_circle(E, a, b, cx, cy, r, line=None, lw=0.0, squash=1.0):
    """b inside the circle (cx, cy, r), a outside; optional edge line (rgb, width)"""
    return run(E, CIRC_FS, {'a': a, 'b': b}, {'c': (float(cx), float(cy)), 'r': float(r), 'lc': tuple(line or (1, 1, 1)),
                                             'lw': float(lw if line else 0.0), 'sq': float(squash)})


STRIPE_FS = '''uniform sampler2D a; uniform sampler2D b; uniform float p; uniform float ang; uniform float n;
void main(){ vec2 q = gl_FragCoord.xy / u_size.y; float cs = cos(ang), sn = sin(ang);
  float v = -sn * q.x + cs * q.y;
  float k = floor(v * n); float f = fract(v * n);
  float delay = fract(sin(k * 91.7) * 43758.5) * 0.5;
  float pp = clamp((p - delay) / 0.5, 0.0, 1.0);
  float m = step(f, pp);
  o = mix(texture(a, UV()), texture(b, UV()), m); }'''


def wipe_stripes(E, a, b, p, ang_deg=-12.0, n=9.0):
    """staggered stripes reveal b (p 0..1)"""
    return run(E, STRIPE_FS, {'a': a, 'b': b}, {'p': float(p), 'ang': math.radians(ang_deg), 'n': float(n)})
