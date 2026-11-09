# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""Same API as CHROMA (E.pass_ / over / screen / mix / blur / affine / disc / canvas / card / quad / render / output), so shot
authors can copy CHROMA code. What changed:
  * footage: no LUT, no u16 caches. E.clip(take_or_name, frame) reads 8-bit RGB plates from the JPEG window cache
    (src/footage.py, cache/frames/ on the work disk) and uploads them (LRU of RGB8 textures). The look is applied by the shot
    (src/fx/heat.py = the film's signature filter).
  * E.blur(t, sigma) runs at reduced resolution for big sigmas (glows), E.add / E.max helpers, E.mips(t) for 4K plates.
  * render(F, fn, row) takes the shot function explicitly (src/shots resolves the timeline).

Colour pipeline: display-referred Rec.709 gamma (0..1) everywhere; compositing in display space; motion-blur sub-frames
averaged in LINEAR light (gamma 2.4) in RGBA32F; output + TPDF dither of 1 LSB(8-bit) -> 8-bit PNG or 16-bit pipe -> H.264.
Image convention: pixel (0,0) = top-left; in shaders gl_FragCoord.xy IS the image pixel coordinate (y down).
Every size given to the engine/kit is in 1080p px; multiply by E.s for raw output pixels (canvases do it for you)."""
import os, sys, math
from collections import OrderedDict
import numpy as np
import moderngl
from src.engine.canvas import Canvas

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BW, BH = 1920, 1080
PREMUL = (moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA)
ADD = (moderngl.ONE, moderngl.ONE)

HEADER = '''#version 410
uniform vec2 u_size;
uniform float u_s;
out vec4 o;
float luma(vec3 c){ return dot(c, vec3(0.2126, 0.7152, 0.0722)); }
vec2 cbcr(vec3 c){ float y = luma(c); return vec2((c.b - y) / 1.8556, (c.r - y) / 1.5748); }
vec2 UV(){ return gl_FragCoord.xy / u_size; }
vec3 lin(vec3 c){ return pow(max(c, 0.0), vec3(2.4)); }
vec3 enc(vec3 c){ return pow(max(c, 0.0), vec3(1.0 / 2.4)); }
'''
FS_VERT = '''#version 410
void main(){ vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2)); gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0); }
'''
COPY_FS = 'uniform sampler2D a; void main(){ o = texture(a, UV()); }'
MIX2_FS = 'uniform sampler2D a; uniform sampler2D b; uniform float k; void main(){ vec2 uv = UV(); o = vec4(mix(texture(a, uv).rgb, texture(b, uv).rgb, k), 1.0); }'
# [hires] Lanczos-3 resampling widened to max(r,1) texels (r = texels per OUTPUT pixel, e.g. 2/zoom for a 2160p plate);
# r > HQ_SMAX moves to mip level L (needs E.mips(t)). From CHROMA unchanged.
HQ_GLSL = '''
const float HQ_A = 3.0;
const float HQ_SMAX = 3.0;
float hq_k(float x){ x = abs(x); if (x >= HQ_A) return 0.0; if (x < 1e-4) return 1.0;
  float px = 3.14159265358979 * x; return HQ_A * sin(px) * sin(px / HQ_A) / (px * px); }
vec3 texhq(sampler2D t, vec2 uv, float r, int maxlod){
  float s = max(r, 1.0); int L = 0;
  while (s > HQ_SMAX && L < maxlod){ s *= 0.5; L++; }
  s = clamp(s, 1.0, HQ_SMAX);
  ivec2 ts = textureSize(t, L); vec2 p = uv * vec2(ts) - 0.5; vec2 c = floor(p); ivec2 ci = ivec2(c);
  int R = int(ceil(HQ_A * s)); int N = 2 * R; float wx[18];
  for (int i = 0; i < N; i++) wx[i] = hq_k((c.x + float(i - R + 1) - p.x) / s);
  vec3 acc = vec3(0.0); float ws = 0.0;
  for (int j = 0; j < N; j++){
    float wy = hq_k((c.y + float(j - R + 1) - p.y) / s);
    if (wy == 0.0) continue;
    int y = clamp(ci.y + j - R + 1, 0, ts.y - 1); vec3 row = vec3(0.0); float rw = 0.0;
    for (int i = 0; i < N; i++){ float w = wx[i];
      row += texelFetch(t, ivec2(clamp(ci.x + i - R + 1, 0, ts.x - 1), y), L).rgb * w; rw += w; }
    acc += row * wy; ws += rw * wy; }
  return clamp(acc / ws, 0.0, 1.0); }
vec3 texl3(sampler2D t, vec2 uv){ return texhq(t, uv, 1.0, 0); }
'''
OVER_FS = 'uniform sampler2D a; uniform sampler2D b; uniform float k; void main(){ vec2 uv = UV(); vec4 A = texture(a, uv); vec4 B = texture(b, uv) * k; o = B + A * (1.0 - B.a); }'
SCREEN_FS = 'uniform sampler2D a; uniform sampler2D b; uniform float k; void main(){ vec2 uv = UV(); vec4 A = texture(a, uv); vec4 B = texture(b, uv); o = vec4(mix(A.rgb, 1.0 - (1.0 - A.rgb) * (1.0 - B.rgb), k), A.a); }'
MULT_FS = 'uniform sampler2D a; uniform sampler2D b; uniform float k; void main(){ vec2 uv = UV(); vec4 A = texture(a, uv); vec4 B = texture(b, uv); o = vec4(A.rgb * mix(vec3(1.0), B.rgb, k), A.a); }'
ADDT_FS = 'uniform sampler2D a; uniform sampler2D b; uniform float k; void main(){ vec2 uv = UV(); vec4 A = texture(a, uv); o = vec4(A.rgb + texture(b, uv).rgb * k, A.a); }'
MAX_FS = 'uniform sampler2D a; uniform sampler2D b; uniform float k; void main(){ vec2 uv = UV(); vec4 A = texture(a, uv); o = vec4(max(A.rgb, texture(b, uv).rgb * k), A.a); }'
MIX_FS = '''uniform sampler2D a; uniform sampler2D b; uniform sampler2D m; uniform int has_m; uniform float k; uniform int ch; uniform int inv;
void main(){ vec2 uv = UV(); float w = k;
  if (has_m == 1){ vec4 M = texture(m, uv); float v = ch == 0 ? M.r : (ch == 3 ? M.a : luma(M.rgb)); if (inv == 1) v = 1.0 - v; w *= v; }
  o = mix(texture(a, uv), texture(b, uv), w); }'''
BLUR_FS = '''uniform sampler2D a; uniform vec2 dir; uniform float sigma;
void main(){ vec2 uv = UV(); if (sigma < 0.3) { o = texture(a, uv); return; }
  int r = int(ceil(3.0 * sigma)); vec4 acc = vec4(0.0); float ws = 0.0;
  for (int i = -r; i <= r; i++){ float w = exp(-0.5 * float(i * i) / (sigma * sigma)); acc += texture(a, uv + dir * float(i) / u_size) * w; ws += w; }
  o = acc / ws; }'''
AFFINE_FS = '''uniform sampler2D a; uniform mat3 inv; uniform vec2 src_size; uniform vec4 bg;
void main(){ vec3 q = inv * vec3(gl_FragCoord.xy, 1.0); vec2 e = min(q.xy, src_size - q.xy);
  float cov = clamp(min(e.x, e.y) + 0.5, 0.0, 1.0); o = mix(bg, texture(a, q.xy / src_size), cov); }'''
DISC_FS = 'uniform vec2 c; uniform float r; uniform vec4 col; void main(){ float d = length(gl_FragCoord.xy - c); o = col * clamp(r - d + 0.5, 0.0, 1.0); }'
ACC_FS = 'uniform sampler2D a; uniform float w; uniform int lin_; void main(){ vec4 c = texture(a, UV()); if (lin_ == 1) c.rgb = pow(max(c.rgb, 0.0), vec3(2.4)); o = c * w; }'
OUT_FS = '''uniform sampler2D a; uniform float dith; uniform float levels; uniform int seed; uniform int lin_;
uvec3 pcg3d(uvec3 v){ v = v * 1664525u + 1013904223u; v.x += v.y*v.z; v.y += v.z*v.x; v.z += v.x*v.y; v ^= v >> 16u;
  v.x += v.y*v.z; v.y += v.z*v.x; v.z += v.x*v.y; return v; }
void main(){ vec3 c = texture(a, UV()).rgb; if (lin_ == 1) c = pow(max(c, 0.0), vec3(1.0 / 2.4));
  uvec2 p = uvec2(gl_FragCoord.xy); uint s = uint(seed);
  vec3 n1 = vec3(pcg3d(uvec3(p, s))) / 4294967295.0; vec3 n2 = vec3(pcg3d(uvec3(p, s + 7919u))) / 4294967295.0;
  c = clamp(c + (n1 + n2 - 1.0) * dith, 0.0, 1.0); if (levels > 0.0) c = floor(c * levels + 0.5) / levels; o = vec4(c, 1.0); }'''
QUAD_VS = '''#version 410
in vec4 in_pos; in vec2 in_uv; out vec2 v_uv; void main(){ v_uv = in_uv; gl_Position = in_pos; }'''
QUAD_FS = '''in vec2 v_uv; uniform sampler2D tex; uniform sampler2D face; uniform int mode; uniform vec4 col; uniform vec4 face_rect;
uniform float k; uniform int flipu; uniform int aa;
void main(){ vec2 uv = v_uv; float cov = 1.0;
  if (aa == 1){ vec2 fw = max(fwidth(uv), vec2(1e-6)); vec2 e = min(uv, 1.0 - uv) / fw; cov = clamp(min(e.x, e.y) + 0.5, 0.0, 1.0); }
  vec2 t = clamp(uv, 0.0, 1.0); if (flipu == 1) t.x = 1.0 - t.x; vec4 c;
  if (mode == 0) c = texture(tex, t);
  else { float m = texture(tex, t).a;
    if (mode == 1) c = vec4(col.rgb, 1.0) * col.a * m;
    else c = vec4(texture(face, mix(face_rect.xy, face_rect.zw, t)).rgb, 1.0) * col.a * m; }
  o = c * cov * k; }'''


def rot_matrix(rx=0.0, ry=0.0, rz=0.0):
    """degrees; applied X, then Y, then Z (y is down, z goes into the screen)"""
    a, b, c = map(math.radians, (rx, ry, rz))
    X = np.array([[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]])
    Y = np.array([[math.cos(b), 0, math.sin(b)], [0, 1, 0], [-math.sin(b), 0, math.cos(b)]])
    Z = np.array([[math.cos(c), -math.sin(c), 0], [math.sin(c), math.cos(c), 0], [0, 0, 1]])
    return Z @ Y @ X


class Tex:
    __slots__ = ('E', 'gt', 'key', 'w', 'h', '_fbo', 'used')

    def __init__(self, E, gt, key):
        self.E, self.gt, self.key = E, gt, key
        self.w, self.h = gt.size
        self._fbo, self.used = None, -1

    @property
    def fbo(self):
        if self._fbo is None:
            self._fbo = self.E.ctx.framebuffer(color_attachments=[self.gt])
        return self._fbo

    def clear(self, rgba=(0.0, 0.0, 0.0, 0.0)):
        self.fbo.clear(*[float(v) for v in rgba]); return self

    def read(self):
        """debug: -> float32 (h, w, 4), row 0 = image top"""
        return np.frombuffer(self.fbo.read(components=4, dtype='f4'), np.float32).reshape(self.h, self.w, 4)


class Engine:
    shutter = 0.5          # 180 degrees: exposure = half a frame
    linear_blur = True     # sub-frames are averaged in linear light
    ftex_cap = {1080: 18, 1440: 8, 2160: 5}     # uploaded footage frames kept on the GPU per tier (LRU)

    def __init__(self, scale=1, store=None):
        self.s = int(scale)
        self.W, self.H = BW * self.s, BH * self.s
        self.size = (self.W, self.H)
        self.ctx = moderngl.create_standalone_context(require=410)
        self.ctx.enable_only(moderngl.NOTHING)
        self._store = store
        self._progs, self._vaos, self._pool, self._live = {}, {}, {}, []
        self._ft, self._ftfree, self._mipt, self._persist = OrderedDict(), {}, set(), {}
        self._canvases, self._ncv, self._mipped = [], 0, []
        self.frame_id = 0
        self._qbuf = self.ctx.buffer(reserve=4 * 6 * 4, dynamic=True)
        self.acc = self.new(dtype='f4', persistent=True)
        self.stats = {'uploads': 0, 'blends': 0}

    @property
    def footage(self):
        """src.footage.Store of this process (lazy: shots without footage never touch the cache)"""
        if self._store is None:
            from src import footage
            self._store = footage.Store()
        return self._store

    # ------------------------------------------------------------------ programs / textures
    def program(self, frag, vert=None):
        key = (vert, frag)
        p = self._progs.get(key)
        if p is None:
            p = self._progs[key] = self.ctx.program(vertex_shader=vert or FS_VERT, fragment_shader=HEADER + frag)
        return p

    def _vao(self, prog, kind='empty'):
        key = (id(prog), kind)
        v = self._vaos.get(key)
        if v is None:
            if kind == 'quad':
                v = self.ctx.vertex_array(prog, [(self._qbuf, '4f 2f', 'in_pos', 'in_uv')])
            else:
                v = self.ctx.vertex_array(prog, [])
            self._vaos[key] = v
        return v

    def new(self, size=None, comps=4, dtype='f2', clear=None, persistent=False):
        """a render-target texture. Non-persistent textures return to the pool at the end of the sub-frame."""
        w, h = size or self.size
        key = (int(w), int(h), comps, dtype)
        lst = self._pool.get(key)
        if lst:
            t = lst.pop()
        else:
            gt = self.ctx.texture((int(w), int(h)), comps, dtype=dtype)
            gt.repeat_x = gt.repeat_y = False
            gt.filter = (moderngl.LINEAR, moderngl.LINEAR)
            t = Tex(self, gt, key)
        if not persistent:
            self._live.append(t)
        if clear is not None:
            t.clear(clear)
        return t

    def persistent(self, key, build):
        """cache anything (static masks, glyph textures, LUTs...) for the life of the process"""
        if key not in self._persist:
            self._persist[key] = build()
        return self._persist[key]

    def texture_from(self, arr, persistent=True):
        """numpy (h,w,c) uint8 / uint16 / float32 -> Tex (c = 1..4). float32 -> f4, uint16 -> nu2, uint8 -> f1"""
        arr = np.ascontiguousarray(arr)
        if arr.ndim == 2:
            arr = arr[..., None]
        h, w, c = arr.shape
        dt = {np.dtype('uint8'): 'f1', np.dtype('uint16'): 'nu2', np.dtype('float32'): 'f4'}[arr.dtype]
        gt = self.ctx.texture((w, h), c, arr, dtype=dt, alignment=1)
        gt.repeat_x = gt.repeat_y = False
        gt.filter = (moderngl.LINEAR, moderngl.LINEAR)
        t = Tex(self, gt, None)
        if not persistent:
            self._live.append(t)
        return t

    def end_subframe(self):
        for t in self._mipped:                          # pooled targets go back without a (stale) mip chain
            t.gt.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self._mipped = []
        for t in self._live:
            self._pool.setdefault(t.key, []).append(t)
        self._live, self._ncv = [], 0

    def _bind(self, prog, inputs, uniforms, w, h):
        unit = 0
        for name, t in (inputs or {}).items():
            u = prog.get(name, None)
            if u is None:
                continue
            (t.gt if hasattr(t, 'gt') else t).use(unit)
            u.value = unit; unit += 1
        allu = {'u_size': (float(w), float(h)), 'u_s': float(self.s)}
        allu.update(uniforms or {})
        for k, v in allu.items():
            u = prog.get(k, None)
            if u is not None:
                u.value = v

    def pass_(self, frag, inputs=None, uniforms=None, out=None, size=None, blend=None):
        """run a full-screen fragment shader (HEADER is prepended: u_size, u_s, out vec4 o, luma(), cbcr(), UV(), lin(), enc()).
        out=None -> new pooled RGBA16F target (size: default output size). blend=PREMUL/ADD draws in place onto `out`."""
        prog = self.program(frag)
        dst = out if out is not None else self.new(size)
        self._bind(prog, inputs, uniforms, dst.w, dst.h)
        dst.fbo.use()
        if blend:
            self.ctx.enable(moderngl.BLEND); self.ctx.blend_func = blend
        self._vao(prog).render(moderngl.TRIANGLES, vertices=3)
        if blend:
            self.ctx.disable(moderngl.BLEND)
        return dst

    def draw(self, vert, frag, dst, n, mode=moderngl.POINTS, inputs=None, uniforms=None, blend=PREMUL):
        """attribute-less draw (use gl_VertexID), e.g. point clouds. Draws in place onto dst."""
        prog = self.program(frag, vert)
        self._bind(prog, inputs, uniforms, dst.w, dst.h)
        dst.fbo.use()
        if mode == moderngl.POINTS:
            self.ctx.enable(moderngl.PROGRAM_POINT_SIZE)
        if blend:
            self.ctx.enable(moderngl.BLEND); self.ctx.blend_func = blend
        self._vao(prog).render(mode, vertices=int(n))
        self.ctx.disable(moderngl.BLEND | moderngl.PROGRAM_POINT_SIZE)
        return dst

    # ------------------------------------------------------------------ compositing (all return new textures)
    def solid(self, rgb, a=1.0, size=None):
        return self.new(size, clear=(rgb[0] * a, rgb[1] * a, rgb[2] * a, a))

    def copy(self, a, size=None):
        return self.pass_(COPY_FS, {'a': a}, size=size)

    def over(self, dst, src, k=1.0):
        """src (premultiplied RGBA) over dst"""
        return self.pass_(OVER_FS, {'a': dst, 'b': src}, {'k': float(k)})

    def screen(self, a, b, k=1.0):
        return self.pass_(SCREEN_FS, {'a': a, 'b': b}, {'k': float(k)})

    def multiply(self, a, b, k=1.0):
        return self.pass_(MULT_FS, {'a': a, 'b': b}, {'k': float(k)})

    def add(self, a, b, k=1.0):
        """a + b*k (rgb, display space -- for glows; clamp happens at output)"""
        return self.pass_(ADDT_FS, {'a': a, 'b': b}, {'k': float(k)})

    def lighten(self, a, b, k=1.0):
        return self.pass_(MAX_FS, {'a': a, 'b': b}, {'k': float(k)})

    def mix(self, a, b, m=0.5, ch='r', invert=False):
        """a -> b by m (float, or mask Tex: channel 'r' | 'a' | 'luma')"""
        has = not isinstance(m, (int, float))
        return self.pass_(MIX_FS, {'a': a, 'b': b, 'm': m if has else a},
                          {'has_m': int(has), 'k': 1.0 if has else float(m), 'ch': {'r': 0, 'luma': 1, 'a': 3}[ch], 'inv': int(invert)})

    mask = mix

    def blur(self, a, sigma, fast=True):
        """gaussian, sigma in 1080p px. fast: sigma > 6 runs at 1/2..1/8 resolution (glows); the result is output size."""
        sg = float(sigma) * self.s
        if sg < 0.3:
            return a
        d = 1
        if fast:
            while sg / d > 6.0 and d < 8:
                d *= 2
        if d == 1:
            t = self.pass_(BLUR_FS, {'a': a}, {'dir': (1.0, 0.0), 'sigma': sg})
            return self.pass_(BLUR_FS, {'a': t}, {'dir': (0.0, 1.0), 'sigma': sg})
        size = (max(1, a.w // d), max(1, a.h // d))
        small = a
        dd = 1
        while dd < d:                                   # 2x2 box steps (bilinear tap at the texel corner)
            dd *= 2
            small = self.pass_(COPY_FS, {'a': small}, size=(max(1, a.w // dd), max(1, a.h // dd)))
        t = self.pass_(BLUR_FS, {'a': small}, {'dir': (1.0, 0.0), 'sigma': sg / d}, size=size)
        t = self.pass_(BLUR_FS, {'a': t}, {'dir': (0.0, 1.0), 'sigma': sg / d}, size=size)
        return self.pass_(COPY_FS, {'a': t}, size=(a.w, a.h))

    def affine(self, a, scale=1.0, rot=0.0, center=None, offset=(0.0, 0.0), bg=(0, 0, 0, 0)):
        """zoom / rotate (deg) about center (1080p px, default frame centre), then translate by offset (1080p px)"""
        cx, cy = center or (BW / 2, BH / 2)
        s, c, sn = float(scale), math.cos(math.radians(rot)), math.sin(math.radians(rot))
        S = self.s
        M = np.array([[s * c, -s * sn, 0], [s * sn, s * c, 0], [0, 0, 1]])
        T1 = np.array([[1, 0, -cx * S], [0, 1, -cy * S], [0, 0, 1]])
        T2 = np.array([[1, 0, (cx + offset[0]) * S], [0, 1, (cy + offset[1]) * S], [0, 0, 1]])
        fw = np.array([[a.w / self.W, 0, 0], [0, a.h / self.H, 0], [0, 0, 1]])  # src texture may differ in size
        inv = fw @ np.linalg.inv(T2 @ M @ T1)
        return self.pass_(AFFINE_FS, {'a': a}, {'inv': tuple(inv.T.flatten()), 'src_size': (float(a.w), float(a.h)), 'bg': tuple(map(float, bg))})

    def disc(self, dst, cx, cy, r, rgb=(0, 0, 0), a=1.0):
        """in place: anti-aliased filled circle (1080p units)"""
        return self.pass_(DISC_FS, None, {'c': (cx * self.s, cy * self.s), 'r': r * self.s,
                                          'col': (rgb[0] * a, rgb[1] * a, rgb[2] * a, a)}, out=dst, blend=PREMUL)

    # ------------------------------------------------------------------ footage (src/footage.py)
    def _ftex(self, key, i, tier):
        """GPU texture (RGB8, bilinear) of cached source frame i; LRU per tier, reuses evicted textures of the same size"""
        k = (key, int(i), tier)
        t = self._ft.get(k)
        if t is None:
            arr = self.footage.get(key, int(i), tier)
            h, w = arr.shape[:2]
            cap = self.ftex_cap.get(tier, 6)
            same = [kk for kk in self._ft if kk[2] == tier]
            if len(same) >= cap:                           # evict the oldest of this tier not used in this frame
                for kk in same:
                    if self._ft[kk].used != self.frame_id:
                        old = self._ft.pop(kk)
                        self._ftfree.setdefault((old.w, old.h), []).append(old)
                        break
            lst = self._ftfree.get((w, h))
            if lst:
                t = lst.pop()
            else:
                gt = self.ctx.texture((w, h), 3, dtype='f1', alignment=1)
                gt.repeat_x = gt.repeat_y = False
                t = Tex(self, gt, None)
            t.gt.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self._mipt.discard(id(t))
            t.gt.write(np.ascontiguousarray(arr), alignment=1)
            self._ft[k] = t
            self.stats['uploads'] += 1
        self._ft.move_to_end(k)
        t.used = self.frame_id
        return t

    def clip(self, c, x, interp=None, tier=None):
        """footage texture. c = footage.Take (x = FILM frame: use T.F) or a catalog name/alias (x = SOURCE frame, float).
        interp 'nearest' (round) | 'blend' (mix the two neighbours). tier 1080 | 1440 | 2160 (cache resolution; falls back to
        the largest tier the source has). Returns RGB8 (nearest) or RGBA16F (blend), w x h of the tier (1920x1080 at 1080)."""
        from src import footage as FT
        if isinstance(c, FT.Take):
            key, tier, interp = c.key, tier or c.tier, interp or c.interp
            x = c.src_frame(x)
        else:
            key, tier, interp = FT.resolve(c), tier or 1080, interp or 'nearest'
        st = self.footage
        tier = st.tier_for(key, tier)
        n = st.n(key)
        x = min(max(float(x), 0.0), n - 1.0)
        if interp == 'nearest':
            i0, fr = int(math.floor(x + 0.5)), 0.0
        else:
            i0 = int(math.floor(x)); fr = x - i0
            if fr < 1e-3:
                fr = 0.0
            elif fr > 1 - 1e-3:
                i0, fr = i0 + 1, 0.0
        i0 = min(i0, n - 1)
        if i0 >= n - 1:
            fr = 0.0
        st.hint(key, i0, tier)
        t0 = self._ftex(key, i0, tier)
        if fr == 0.0:
            return t0
        t1 = self._ftex(key, i0 + 1, tier)
        self.stats['blends'] += 1
        return self.pass_(MIX2_FS, {'a': t0, 'b': t1}, {'k': float(fr)}, size=(t0.w, t0.h))

    def mips(self, t):
        """build the mip chain of a texture (e.g. a 2160p plate shown at 1080p) -> highest level, for texhq / trilinear.
        Footage textures keep their mips until re-uploaded; pooled targets: call again after you draw into them."""
        if id(t) not in self._mipt or t.key is not None:
            t.gt.build_mipmaps()
            t.gt.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
            if t.key is None:
                self._mipt.add(id(t))
            else:
                self._mipped.append(t)
        return int(math.floor(math.log2(max(t.w, t.h))))

    hq_maxlod = mips

    # ------------------------------------------------------------------ vector layers
    def canvas(self):
        """a cleared skia Canvas (1080p units). Call .texture() to get it on the GPU (premultiplied)."""
        if self._ncv < len(self._canvases):
            cv = self._canvases[self._ncv]
        else:
            cv = Canvas(self); self._canvases.append(cv)
        self._ncv += 1
        return cv.begin()

    def project(self, P, fov=35.0):
        """P (N,3): 1080p px, screen-centred, y down, z into the screen. -> clip coords (N,4), screen px (N,2)"""
        P = np.asarray(P, np.float64)
        D = (BH / 2) / math.tan(math.radians(fov) / 2)
        wc = (D + P[:, 2]) / D
        sx = BW / 2 + P[:, 0] / wc
        sy = BH / 2 + P[:, 1] / wc
        clip = np.stack([(sx / BW * 2 - 1) * wc, (sy / BH * 2 - 1) * wc, np.zeros_like(wc), wc], 1)
        return clip, np.stack([sx, sy], 1)

    def quad(self, dst, clip4, uv4, tex, mode=0, col=(1, 1, 1, 1), face=None, face_rect=(0, 0, 1, 1), k=1.0, flipu=False, aa=True):
        """low level: draw one quad (vertices TL, TR, BR, BL in clip space) onto dst with premultiplied blending.
        mode 0: tex rgba; 1: tex alpha * flat col; 2: tex alpha * face texture (face_rect = uv rect in face)"""
        order = [0, 1, 3, 2]
        data = np.concatenate([np.asarray(clip4, np.float32)[order], np.asarray(uv4, np.float32)[order]], 1)
        self._qbuf.write(data.astype(np.float32).tobytes())
        prog = self.program(QUAD_FS, QUAD_VS)
        self._bind(prog, {'tex': tex, 'face': face if face is not None else tex},
                   {'mode': int(mode), 'col': tuple(map(float, col)), 'face_rect': tuple(map(float, face_rect)),
                    'k': float(k), 'flipu': int(flipu), 'aa': int(aa)}, dst.w, dst.h)
        dst.fbo.use()
        self.ctx.enable(moderngl.BLEND); self.ctx.blend_func = PREMUL
        self._vao(prog, 'quad').render(moderngl.TRIANGLE_STRIP, vertices=4)
        self.ctx.disable(moderngl.BLEND)
        return dst

    def card(self, dst, tex, center, size, rot=(0.0, 0.0, 0.0), z=0.0, fov=35.0, back=None, k=1.0, pivot=(0.0, 0.0)):
        """z > 0 pushes away. Back side shows `back` (mirrored so it reads correctly) or nothing. Edges anti-aliased."""
        w, h = size
        D = (BH / 2) / math.tan(math.radians(fov) / 2)
        m = 1.5 / self.s * max(0.2, (D + z) / D)
        xs = np.array([-w / 2 - m, w / 2 + m, w / 2 + m, -w / 2 - m]); ys = np.array([-h / 2 - m, -h / 2 - m, h / 2 + m, h / 2 + m])
        uv = np.stack([(xs + w / 2) / w, (ys + h / 2) / h], 1)
        L = np.stack([xs - pivot[0], ys - pivot[1], np.zeros(4)], 1)
        P = (rot_matrix(*rot) @ L.T).T + np.array([center[0] - BW / 2 + pivot[0], center[1] - BH / 2 + pivot[1], z])
        clip, scr = self.project(P, fov)
        area = sum(scr[i, 0] * scr[(i + 1) % 4, 1] - scr[(i + 1) % 4, 0] * scr[i, 1] for i in range(4))
        if area > 0:
            return self.quad(dst, clip, uv, tex, k=k)
        if back is not None:
            return self.quad(dst, clip, uv, back, k=k, flipu=True)
        return dst

    # ------------------------------------------------------------------ frame loop
    def accumulate(self, acc, src, w):
        return self.pass_(ACC_FS, {'a': src}, {'w': float(w), 'lin_': int(self.linear_blur)}, out=acc, blend=ADD)

    def render(self, F, fn=None, row=None, n_sub=None, max_sub=None):
        """render integer film frame F (all its motion-blur sub-frames) -> self.acc (RGBA32F, linear if linear_blur).
        fn/row default to the timeline's shot at F (src/shots.find)."""
        from src.engine.shot import T, n_subframes
        if fn is None:
            from src import shots
            fn, row = shots.resolve(F)
        n = n_sub or n_subframes(fn, T(F, F, row))
        if max_sub:
            n = min(n, max_sub)
        n = max(1, int(n))
        self.acc.clear((0, 0, 0, 0))
        for k in range(n):
            f = F + ((k + 0.5) / n - 0.5) * self.shutter if n > 1 else float(F)
            out = fn(self, T(f, F, row, n, k))
            self.accumulate(self.acc, out, 1.0 / n)
            self.end_subframe()
        self.frame_id += 1
        return self.acc

    def output(self, acc=None, bits=8, seed=0):
        """-> numpy (H,W,3) uint8 (bits=8) or uint16 (bits=16), with 1 LSB(8-bit) TPDF dither"""
        acc = acc or self.acc
        t = self.new(comps=4, dtype='f1' if bits == 8 else 'f4')
        self.pass_(OUT_FS, {'a': acc}, {'dith': 1.0 / 255.0, 'levels': 255.0 if bits == 8 else 0.0, 'seed': int(seed) & 0x7fffffff,
                                        'lin_': int(self.linear_blur and acc is self.acc)}, out=t)
        if bits == 8:
            img = np.frombuffer(t.fbo.read(components=3, dtype='f1', alignment=1), np.uint8).reshape(self.H, self.W, 3)
        else:   # (reading u2 from a normalized RGBA16 target is broken on this driver -> read f32 and quantize here)
            f = np.frombuffer(t.fbo.read(components=3, dtype='f4', alignment=1), np.float32).reshape(self.H, self.W, 3)
            img = (f * 65535.0 + 0.5).astype(np.uint16)
        self.end_subframe()
        return img

    def frame_rgb(self, F, fn=None, row=None, bits=8, n_sub=None, max_sub=None):
        self.render(F, fn, row, n_sub, max_sub)
        return self.output(self.acc, bits, seed=F)
