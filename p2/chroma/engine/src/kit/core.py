# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""kit core: colours, OKLab ramps -> 1D textures, deterministic hash, GLSL runner with array
uniforms, motion-blur taps. Heat colours (blackbody) live in src/fx/heat.py -- never type a heat hex, take it from there."""
import math
import numpy as np
import moderngl
from src import timeline as TL
from src.kit.anim import clamp01, lerp, eo3, eo5, eoexp, ei3, eio3, eoback, spring   # noqa: F401  (CHROMA names)

FPS, W, H = TL.FPS, TL.W, TL.H


def hx(h):
    h = h.lstrip('#'); return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


# ------------------------------------------------------------------ colours (display-referred 0..1)
INK = hx('#0A0A0B')          # true black
PAPER = hx('#F4F3EF')        # warm white = the "white-hot" design frames
WHITE = (1.0, 1.0, 1.0)
GREY = (0.62, 0.62, 0.63)
PAPER2 = PAPER               # CHROMA name

# false-colour ramps, dark -> bright (OKLab-interpolated). The heat ramps ('heat', 'heat_hi', 'thermal_bb') are added by
# src/fx/heat.py from the blackbody table; these are the hand-made ones.
RAMPS = {
    # thermal camera (ironbow), toned for this film: ink -> deep indigo (cold) -> crimson -> orange -> amber -> white
    'thermal': ['#050508', '#1A1446', '#7E1640', '#E2461C', '#FF9D2E', '#FFF3D6'],
    'ink_paper': ['#0A0A0B', '#F4F3EF'],
    'chrome': ['#F4F6F8', '#B9BEC6', '#40444C', '#0C0D10', '#E9EEF4', '#7E848D', '#D9DDE2'],
}


def _srgb2lin(c):
    c = np.asarray(c, np.float64); return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _lin2srgb(c):
    c = np.clip(c, 0, 1); return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


_M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929], [0.2119034982, 0.6806995451, 0.1073969566],
                [0.0883024619, 0.2817188376, 0.6299787005]])
_M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468], [1.9779984951, -2.4285922050, 0.4505937099],
                [0.0259040371, 0.7827717662, -0.8086757660]])


def _to_oklab(rgb):
    return (_M2 @ np.cbrt(_M1 @ _srgb2lin(rgb).T)).T


def _from_oklab(lab):
    return _lin2srgb((np.linalg.inv(_M1) @ ((np.linalg.inv(_M2) @ np.asarray(lab).T) ** 3)).T)


def ramp_array(stops, n=256):
    """stops: list of hex / rgb -> (1, n, 3) float32, evenly spaced stops interpolated in OKLab"""
    cols = np.array([hx(s) if isinstance(s, str) else s for s in stops], np.float64)
    lab = _to_oklab(cols)
    x = np.linspace(0, 1, n) * (len(cols) - 1)
    i = np.minimum(x.astype(int), len(cols) - 2); f = (x - i)[:, None]
    out = _from_oklab(lab[i] * (1 - f) + lab[i + 1] * f)
    return np.ascontiguousarray(out[None].astype(np.float32))


def ramp_tex(E, name_or_stops):
    """1D texture (256 x 1) of a ramp name (RAMPS) or a list of stops; cached per process"""
    if isinstance(name_or_stops, str) and name_or_stops not in RAMPS:
        from src.fx import heat  # noqa: F401  (registers the blackbody ramps)
    key = name_or_stops if isinstance(name_or_stops, str) else tuple(map(str, name_or_stops))
    stops = RAMPS[name_or_stops] if isinstance(name_or_stops, str) else name_or_stops
    return E.persistent(('ramp', key), lambda: E.texture_from(ramp_array(stops)))


# ------------------------------------------------------------------ deterministic randomness
def rnd(*keys):
    """stable pseudo-random float in [0, 1) from ints/strings"""
    h = 2166136261
    for k in keys:
        for ch in str(k):
            h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
        h = ((h ^ 0x9E3779B9) * 2654435761) & 0xFFFFFFFF
    h ^= h >> 15; h = (h * 2246822519) & 0xFFFFFFFF; h ^= h >> 13
    return h / 4294967296.0


def rr(a, b, *keys):
    return a + (b - a) * rnd(*keys)


# ------------------------------------------------------------------ GLSL runner with array uniforms
def run(E, frag, inputs=None, uniforms=None, out=None, size=None, blend=None, arrays=None):
    """like E.pass_ but `arrays` = {uniform_name: float array} written raw (mat3 arrays: column-major, see mat3s)"""
    prog = E.program(frag)
    dst = out if out is not None else E.new(size)
    E._bind(prog, inputs, uniforms, dst.w, dst.h)
    for name, data in (arrays or {}).items():
        u = prog.get(name, None)
        if u is not None:
            u.write(np.ascontiguousarray(data, np.float32).tobytes())
    dst.fbo.use()
    if blend:
        E.ctx.enable(moderngl.BLEND); E.ctx.blend_func = blend
    E._vao(prog).render(moderngl.TRIANGLES, vertices=3)
    if blend:
        E.ctx.disable(moderngl.BLEND)
    return dst


def mat3s(Ms, n):
    """list of 3x3 numpy matrices -> flat float32 (n*9) column-major, padded with identity"""
    out = np.tile(np.eye(3, dtype=np.float32).T.flatten(), n)
    for i, M in enumerate(Ms[:n]):
        out[i * 9:(i + 1) * 9] = np.asarray(M, np.float32).T.flatten()
    return out


def taps(T, n):
    """n sample times inside this sub-frame's exposure (for in-shader motion blur)"""
    return T.taps(n)
