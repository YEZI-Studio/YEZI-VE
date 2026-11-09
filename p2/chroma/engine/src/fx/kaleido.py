# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""VOLT kaleidoscope 「时光穿梭」 transition (IGNITION's, re-coloured for electricity -- quiet,
one-directional base; light edges ICE core + VIOLET glow; portal halo along the 'volt_hi' plasma ramp; the R/G/B split keeps
chroma only on the ELECTRIC hues (violet .. electric blue), everything else goes grey -- no warm / green fringes).
Original docstring (IGNITION): GPU port of the CHROMA toolkit kaleidoscope.

Timeline of a Vortex(f0, f1) (proportions of the duration, as in CHROMA):
  fold   0-22 %   a fold circle closes in from outside the frame: outside it the picture folds into N mirrored segments and
                  starts to turn, a thin white-hot rim on the edge; inside, the plate itself (slight push + roll)
  spin  22-80 %   full-screen kaleidoscope: clockwise, faster and faster, pushing in (picture flies outward), mirrored
                  log-radius tunnel (rings rushing inward), swirl (outer rings turn more), radial chromatic split
                  (R/G/B by radius), motion blur in the shader from the angular + radial speed; segments step 6->8->10->12;
                  rotation per frame capped at 42 % of a segment so it never reads as a wagon wheel turning backwards
  end   80-100 %  'portal': the next plate opens out of the vortex eye, a heat-coloured halo on the edge (blackbody, no
                  spectrum), the vortex slows and unfolds;  'disc': everything shrinks into a small disc at the eye, ink
                  around, white-hot rim;  'none': keeps spinning
Knobs: eye (screen), src_c (sampling centre in the plate, 1080p units), wedge (deg, direction of the sampled wedge), nseg /
nseg_end, push, spin (deg/s max), grade (ramp name -> the vortex is gradient-mapped into the film palette, e.g. 'volt'),
level (anti-flicker: while spinning, each frame's mean luma is pulled toward this value, None = off),
rim / rim2 / halo colours (default = the episode's signature colour from MO.palette: pale core + acc glow + its ramp for the
portal halo; only without any motif palette VOLT's ICE + VIOLET / 'volt_hi'; halo_range = positions on that ramp; the light
edge is kept -- IGNITION's vortex has its white-hot edge, kaleido.py fold rima 0.9), palette (1 = the split keeps chroma
only on warm hues, so no cyan/magenta fringes -- it turned out CHROMA's magenta/green/blue vortex bases noisy). Use:
    V = KV.Vortex(TL.f(4, 3), TL.f(5, 3), end='portal', src_c=(980, 420))
    img = V.frame(E, T, plate, nxt)          # plate = the current (graded) plate, nxt = the next one (portal)
    V.phase(T.f), V.u(T.f), V.v(T.f)         # sync type (CHROMA: fold -> accelerate -> type slams -> unfold)
Render vortex frames with subframes=1 (the blur is in the shader)."""
import math
import numpy as np
from src import timeline as TL
from src.kit.core import run, INK, PAPER, WHITE, clamp01, lerp, eo3, ei3, eio3, ramp_tex
import colorsys
from src.fx import volt as VT

RMIN, TUN_L = 40.0, 0.85
PAL_H = 360.0 * colorsys.rgb_to_hsv(*VT.oklch(0.55, 0.14, VT._mixh(VT.H_CHANNEL, VT.H_N2_2P, 0.5)))[0]   # ~241
#        ^ HSV hue at the middle of the electric band (between the channel blue and the N2 violet): the palette clamp keeps
#          chroma within ~50 deg of it

KAL_FS = '''uniform sampler2D src; uniform vec2 eye; uniform vec2 cs; uniform float nseg; uniform float rot; uniform float wedge;
uniform float zoom; uniform float swirl; uniform float tunk; uniform float tunph; uniform float ca; uniform float shrink;
uniform float dang; uniform float dlog; uniform int ntap; uniform vec2 ssize; uniform float sscale;
vec2 mirror(vec2 uv){ return 1.0 - abs(1.0 - mod(uv, 2.0)); }
vec2 srcp(float r, float a){
  float rr = r;
  if (tunk > 0.0){ float l = log(max(r, 1.0) / RMIN) - tunph; float m = mod(l, 2.0 * TUNL); m = m > TUNL ? 2.0 * TUNL - m : m;
    rr = mix(r, RMIN * exp(m), tunk); }
  float th = a;
  if (nseg > 0.5){ float seg = 6.2831853 / nseg; float al = mod(a, seg); if (al > 0.5 * seg) al = seg - al; th = wedge + al - 0.25 * seg; }
  return (cs + rr * zoom * vec2(cos(th), sin(th))) * sscale; }
void main(){
  vec2 p = (gl_FragCoord.xy / u_s - eye) / shrink; float r0 = length(p); float a0 = atan(p.y, p.x);
  vec3 acc = vec3(0.0);
  for (int i = 0; i < 16; i++){ if (i >= ntap) break;
    float tau = ntap > 1 ? (float(i) + 0.5) / float(ntap) - 0.5 : 0.0;
    float r = r0 * exp(-tau * dlog); float a = a0 - rot - tau * dang + swirl * r / 1000.0;
    vec3 c = vec3(texture(src, mirror(srcp(r * (1.0 + ca), a) / ssize)).r, texture(src, mirror(srcp(r, a) / ssize)).g,
                  texture(src, mirror(srcp(r * (1.0 - ca), a) / ssize)).b);
    acc += lin(c); }
  o = vec4(enc(acc / float(ntap)), 1.0); }'''.replace('RMIN', f'{RMIN:.1f}').replace('TUNL', f'{TUN_L:.2f}')

COMP_FS = '''uniform sampler2D k; uniform sampler2D inn; uniform sampler2D ramp; uniform sampler2D hramp; uniform int mode; uniform vec2 eye; uniform float R;
uniform float soft; uniform vec3 rimc; uniform float rima; uniform float rimw; uniform float glow; uniform vec3 base;
uniform float satk; uniform float halo; uniform float hw; uniform float gk; uniform float gbias; uniform float pal;
uniform vec2 hr; uniform float kgain; uniform float palh; uniform vec3 rimc2;
void main(){ vec2 uv = UV(); float r = length(gl_FragCoord.xy / u_s - eye); vec3 K = enc(lin(texture(k, uv).rgb) * kgain);
  if (gk > 0.0){ float y = clamp((luma(K) - 0.5) * 1.1 + 0.5 + gbias, 0.0, 1.0); K = mix(K, texture(ramp, vec2(y, 0.5)).rgb, gk); }
  if (pal > 0.0){                       // palette clamp: chroma survives only on warm hues (the R/G/B split's cyan/blue/magenta
    float mx = max(K.r, max(K.g, K.b)); float mn = min(K.r, min(K.g, K.b)); float h = 0.0; float dd = mx - mn;   // -> grey)
    if (dd > 1e-5){ if (mx == K.r) h = mod((K.g - K.b) / dd, 6.0); else if (mx == K.g) h = (K.b - K.r) / dd + 2.0; else h = (K.r - K.g) / dd + 4.0; }
    h *= 60.0; float dh = abs(mod(h - palh + 540.0, 360.0) - 180.0);
    K = mix(K, vec3(luma(K)), pal * smoothstep(48.0, 70.0, dh)); }
  vec3 col = K;
  if (mode == 1){ float m = smoothstep(R - soft, R + soft, r); col = mix(texture(inn, uv).rgb, K, m); }
  else if (mode == 2){ float m = 1.0 - smoothstep(R - soft, R + soft, r); vec3 Ks = clamp(vec3(luma(K)) + (K - vec3(luma(K))) * satk, 0.0, 1.0);
    col = mix(base, Ks, m); }
  else if (mode == 3){ float m = 1.0 - smoothstep(R - soft, R + soft, r); col = mix(K, texture(inn, uv).rgb, m);
    float d = (r - R) / max(hw, 0.5); float g = exp(-d * d);
    vec3 hc = texture(hramp, vec2(clamp(mix(hr.x, hr.y, 1.0 - abs(d)), 0.0, 1.0), 0.5)).rgb;
    col += halo * g * (0.55 * hc + 0.45 * exp(-d * d * 6.0)); }
  if (rima > 0.0){ float x = r - R;                        // light edge: a soft ICE core + a wider VIOLET glow (added)
    col = mix(col, rimc, clamp(rima * exp(-x * x / (rimw * rimw)), 0.0, 1.0));
    col = enc(lin(col) + lin(rimc2) * 0.45 * rima * exp(-x * x / (glow * glow))); }
  o = vec4(clamp(col, 0.0, 1.0), 1.0); }'''


class Vortex:
    def __init__(self, f0, f1, end='portal', nseg=6, nseg_end=12, push=1.6, spin=1100.0, wedge=-90.0, eye=(960.0, 540.0),
                 src_c=(960.0, 540.0), fold_end=0.22, spin_end=0.80, rim=None, rim2=None, base=INK, grade=None, grade_k=1.0,
                 grade_bias=0.0, max_taps=16, palette=1.0, halo_range=(0.30, 0.85), level=0.22, halo_ramp=None, pal_hue=None):
        self.f0, self.f1, self.end = float(f0), float(f1), end
        self.D = (self.f1 - self.f0) / TL.FPS
        self.tf = fold_end * self.D
        self.te = spin_end * self.D if end in ('disc', 'portal') else self.D
        s0, s1 = int(nseg), int(max(nseg, nseg_end))
        steps = list(range(s0, s1 + 1, 2)) or [s0]
        if steps[-1] != s1:
            steps.append(s1)
        self.steps, self.push, self.wmax, self.wedge = steps, float(push), float(spin), float(wedge)
        self.eye, self.src_c = tuple(map(float, eye)), tuple(map(float, src_c))
        from src.fx import motif as MO                   # no rim given = the episode's signature colour (MO.palette),
        P = MO.current()                                 # never VOLT's ICE + VIOLET on another motif
        self.rim = tuple(rim) if rim else (tuple(P['pale']) if P else VT.ICE)
        self.rim2 = tuple(rim2) if rim2 else (tuple(P['acc']) if P else VT.VIOLET)
        if halo_ramp is None:
            halo_ramp = list(P['ramp']) if P else 'volt_hi'
        if pal_hue is None and P:
            pal_hue = P['hue']
        self.base, self.grade, self.grade_k, self.grade_bias, self.max_taps = tuple(base), grade, grade_k, grade_bias, max_taps
        self.palette, self.halo_range = float(palette), tuple(map(float, halo_range))
        self.level = level
        self.halo_ramp = halo_ramp                       # p2: ramp name or list of hex stops (portal halo)
        self.pal_hue = PAL_H if pal_hue is None else float(pal_hue)   # p2: HSV hue (deg) whose chroma the split keeps
        ex, ey = self.eye
        self.r_full = 1.05 * math.hypot(max(ex, TL.W - ex), max(ey, TL.H - ey))
        self.r_end = 85.0
        self._ang = [0.0]                     # rotation (deg) at film frames f0-1, f0, f0+1, ... (index i <-> f0 + i - 1)
        for i in range(int(math.ceil(self.D * TL.FPS)) + 4):
            t = (i - 0.5) / TL.FPS
            self._ang.append(self._ang[-1] + min(self.w(t) / TL.FPS, 0.42 * 360.0 / self.nseg(t)))

    # ---------- plan (t in seconds since f0) -- same curves as the CHROMA toolkit
    def _t(self, f):
        return (f - self.f0) / TL.FPS

    def u(self, f):
        """spin progress 0..1"""
        return clamp01((self._t(f) - self.tf) / max(self.te - self.tf, 1e-6))

    def v(self, f):
        """end progress 0..1"""
        return clamp01((self._t(f) - self.te) / max(self.D - self.te, 1e-6))

    def phase(self, f):
        t = self._t(f)
        if t < 0:
            return 'pre'
        if t >= self.D:
            return 'post'
        return 'fold' if t < self.tf else 'spin' if t < self.te else self.end

    def nseg(self, t):
        if t < self.tf:
            return self.steps[0]
        if t < self.te:
            return self.steps[min(len(self.steps) - 1, int(clamp01((t - self.tf) / max(self.te - self.tf, 1e-6)) * len(self.steps)))]
        if self.end == 'portal':
            v = clamp01((t - self.te) / max(self.D - self.te, 1e-6))
            return self.steps[::-1][min(len(self.steps) - 1, int(v * len(self.steps)))]
        return self.steps[-1]

    def w(self, t):
        u = clamp01((t - self.tf) / max(self.te - self.tf, 1e-6)); v = clamp01((t - self.te) / max(self.D - self.te, 1e-6))
        if t < self.tf:
            return 36.0 + (420.0 - 36.0) * ei3(t / max(self.tf, 1e-6))
        if t < self.te:
            return 420.0 + (self.wmax - 420.0) * u ** 1.5
        return self.wmax * (1.0 - eo3(v)) if self.end == 'portal' else self.wmax * (1.0 + 0.6 * v)

    def rot(self, f):
        x = f - self.f0 + 1.0
        x = min(max(x, 0.0), len(self._ang) - 1.001)
        i = int(math.floor(x))
        return self._ang[i] + (self._ang[i + 1] - self._ang[i]) * (x - i)

    def zoom(self, t):
        if t < self.tf:
            return 1.0 - 0.05 * eo3(t / max(self.tf, 1e-6))
        if t < self.te:
            return 0.95 * self.push ** (-(clamp01((t - self.tf) / max(self.te - self.tf, 1e-6)) ** 1.2))
        zt = 0.95 / self.push
        return zt * 0.965 ** ((t - self.te) * 60.0) if self.end == 'disc' else zt

    def swirl(self, t):
        u = clamp01((t - self.tf) / max(self.te - self.tf, 1e-6)); v = clamp01((t - self.te) / max(self.D - self.te, 1e-6))
        if t < self.tf:
            return 0.0
        if t < self.te:
            return 0.9 * eo3(u)
        return 0.9 * (1.0 - eo3(v)) if self.end == 'portal' else 0.9 + 0.4 * v

    def tunk(self, t):
        u = clamp01((t - self.tf) / max(self.te - self.tf, 1e-6)); v = clamp01((t - self.te) / max(self.D - self.te, 1e-6))
        if t < self.tf:
            return 0.0
        if t < self.te:
            return 0.55 * eo3(u)
        return 0.55 * (1.0 - eo3(v / 0.6)) if self.end == 'portal' else 0.55

    def tunph(self, t):
        tau = max(0.0, t - self.tf)
        return 3.0 * tau + 14.4 * tau * tau

    def ca(self, t):
        u = clamp01((t - self.tf) / max(self.te - self.tf, 1e-6)); v = clamp01((t - self.te) / max(self.D - self.te, 1e-6))
        if t < self.tf:
            return 0.0
        if t < self.te:
            return 0.024 * eo3(u / 0.5) if u < 0.5 else lerp(0.024, 0.008, (u - 0.5) / 0.5)
        return 0.008 * (1.0 - eo3(v)) if self.end == 'portal' else 0.02

    def disc_R(self, t):
        v = clamp01((t - self.te) / max(self.D - self.te, 1e-6))
        return math.exp(lerp(math.log(self.r_full), math.log(self.r_end), v ** 1.6))

    def eye_R(self, f):
        """radius of the fold circle / disc / portal at f (1080p px) -- for type that sits in the eye"""
        t, ph = self._t(f), self.phase(f)
        if ph == 'fold':
            return self.r_full * (1.0 - (t / self.tf) ** 1.6)
        if ph == 'disc':
            return self.disc_R(t)
        if ph == 'portal':
            return lerp(90.0, self.r_full, eio3(self.v(f)))
        return 0.0

    # ---------- render
    def kal(self, E, src, f, shrink=1.0, dlog_extra=0.0):
        t = self._t(f)
        h = 0.5 * E.shutter
        t0, t1 = t - h / TL.FPS, t + h / TL.FPS
        dang = self.rot(f + h) - self.rot(f - h)
        dlog = math.log(self.zoom(t0) / self.zoom(t1)) + self.tunk(t) * (self.tunph(t1) - self.tunph(t0)) + dlog_extra
        px = max(abs(math.radians(dang)) * self.r_full, abs(dlog) * self.r_full)
        n = int(min(self.max_taps, max(1, math.ceil(px / 2.5))))
        return run(E, KAL_FS, {'src': src}, {
            'eye': self.eye, 'cs': self.src_c, 'nseg': float(self.nseg(t)), 'rot': math.radians(self.rot(f)),
            'wedge': math.radians(self.wedge), 'zoom': float(self.zoom(t)), 'swirl': float(self.swirl(t)), 'tunk': float(self.tunk(t)),
            'tunph': float(self.tunph(t)), 'ca': float(self.ca(t)), 'shrink': float(shrink), 'dang': math.radians(dang),
            'dlog': float(dlog), 'ntap': n, 'ssize': (float(src.w), float(src.h)), 'sscale': float(src.w) / TL.W})

    def frame(self, E, T, src, nxt=None):
        f = T.f
        ph = self.phase(f)
        if ph == 'pre':
            return src
        if ph == 'post':
            return nxt if (nxt is not None and self.end == 'portal') else (E.solid(self.base) if self.end == 'disc' else self.kal(E, src, f))
        t = self._t(f)
        uni = dict(mode=0, eye=self.eye, R=0.0, soft=2.0, rimc=self.rim, rimc2=self.rim2, rima=0.0, rimw=3.2, glow=22.0, base=self.base,
                   satk=1.0, halo=0.0, hw=10.0, gk=float(self.grade_k if self.grade else 0.0), gbias=float(self.grade_bias),
                   pal=self.palette, hr=self.halo_range)
        inn = src
        if ph == 'fold':
            uu = t / self.tf
            K = self.kal(E, src, f)
            inn = E.affine(src, 1.0 + 0.08 * eo3(uu), -8.0 * uu * uu, self.eye)
            uni.update(mode=1, R=self.r_full * (1.0 - uu ** 1.6), rima=0.9 * clamp01((f - self.f0) / 2.0))
        elif ph == 'disc':
            v = self.v(f)
            Rd = self.disc_R(t)
            h = 0.5 * E.shutter / TL.FPS
            K = self.kal(E, src, f, shrink=Rd / self.r_full, dlog_extra=math.log(self.disc_R(t + h) / self.disc_R(t - h)))
            uni.update(mode=2, R=Rd, satk=1.0 + 0.35 * v, rima=0.75 * clamp01(v / 0.25), rimw=2.0 + 1.0 * v, glow=14.0, soft=1.6)
        elif ph == 'portal':
            v = self.v(f); e = eio3(v)
            K = self.kal(E, src, f)
            nx = nxt if nxt is not None else src
            inn = E.affine(nx, lerp(1.32, 1.0, e), lerp(-4.0, 0.0, e), self.eye)
            uni.update(mode=3, R=lerp(90.0, self.r_full, e), halo=0.95, hw=10.0 + 26.0 * (1.0 - abs(2.0 * v - 1.0)))
        else:
            K = self.kal(E, src, f)
        kg = 1.0
        if self.level is not None and ph in ('spin', 'portal', 'disc'):
            # anti-flicker: the spinning black/white pattern changes its mean brightness frame to frame (it flickers);
            # stateless: this frame's mean luma (top mip level) is pulled toward `level` while spinning, released at the end
            L = E.mips(K)
            m = np.frombuffer(K.gt.read(level=L), np.float16).astype(np.float32)[:3]
            mean = max(1e-3, float(m @ np.array([0.2126, 0.7152, 0.0722])))
            ks = clamp01(self.u(f) / 0.25) * (1.0 - eo3(self.v(f)))
            kg = (min(2.2, max(0.45, (self.level / mean) ** 2.4))) ** ks       # gain in linear light
        uni['kgain'] = kg
        uni['palh'] = self.pal_hue
        ramp = ramp_tex(E, self.grade or 'volt')
        return run(E, COMP_FS, {'k': K, 'inn': inn, 'ramp': ramp, 'hramp': ramp_tex(E, self.halo_ramp)},
                   {k: (tuple(map(float, v)) if isinstance(v, tuple) else
                                                                        (int(v) if k == 'mode' else float(v))) for k, v in uni.items()})
