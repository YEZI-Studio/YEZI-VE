# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""Easing + timing helpers (inputs clamped to 0..1). Motion grammar: enter ease-out, exit ease-in, hard cuts on beats.
CHROMA names kept (ease_out_cubic ... / eo3 eo5 eoexp ei3 eio3 eoback spring) so CHROMA shot code ports as is."""
import math


def clamp01(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else float(x)


def lerp(a, b, t):
    return a + (b - a) * t


def prog(f, a, b):
    """0..1 progress of frame f between frames a and b"""
    return clamp01((f - a) / float(b - a))


def smooth(t):
    t = clamp01(t); return t * t * (3 - 2 * t)


def eo3(t):
    t = clamp01(t); return 1 - (1 - t) ** 3


def eo5(t):
    t = clamp01(t); return 1 - (1 - t) ** 5


def eoexp(t):
    t = clamp01(t); return 1.0 if t >= 1 else 1 - 2 ** (-10 * t)


def ei3(t):
    t = clamp01(t); return t ** 3


def eiexp(t):
    t = clamp01(t); return 0.0 if t <= 0 else 2 ** (10 * t - 10)


def eio3(t):
    t = clamp01(t); return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def eioexp(t):
    t = clamp01(t)
    if t <= 0 or t >= 1:
        return t
    return 2 ** (20 * t - 10) / 2 if t < 0.5 else (2 - 2 ** (-20 * t + 10)) / 2


def eoback(t, s=1.70158):
    """overshoot then settle (s=1.7 -> ~10 % overshoot, s=2.4 -> ~16 %)"""
    t = clamp01(t); c3 = s + 1
    return 1 + c3 * (t - 1) ** 3 + s * (t - 1) ** 2


def spring(t, freq=1.6, damp=0.42):
    """damped spring 0 -> 1, t in 'periods' (t = frames / period_frames). Overshoots and rings out."""
    if t <= 0:
        return 0.0
    w = 2 * math.pi * freq
    return 1 - math.exp(-damp * w * t) * math.cos(w * math.sqrt(max(1e-6, 1 - damp * damp)) * t)


# CHROMA v1 names
ease_out_cubic, ease_in_cubic, ease_in_out_cubic, ease_out_quint = eo3, ei3, eio3, eo5
ease_out_expo, ease_in_expo, ease_in_out_expo, ease_out_back = eoexp, eiexp, eioexp, eoback

EASE = {'lin': clamp01, 'smooth': smooth, 'eo3': eo3, 'eo5': eo5, 'eoexp': eoexp, 'ei3': ei3, 'eiexp': eiexp, 'eio3': eio3,
        'eioexp': eioexp, 'eoback': eoback, 'hold': lambda t: 0.0}


def keys(f, ks):
    """keyframes: ks = [(frame, value[, ease_name_for_the_segment_that_starts_here]), ...] sorted by frame.
    Holds the first/last value outside. Values may be floats or tuples."""
    if f <= ks[0][0]:
        return ks[0][1]
    for a, b in zip(ks, ks[1:]):
        if f < b[0]:
            e = EASE[a[2] if len(a) > 2 else 'lin']((f - a[0]) / float(b[0] - a[0]))
            if isinstance(a[1], tuple):
                return tuple(x + (y - x) * e for x, y in zip(a[1], b[1]))
            return a[1] + (b[1] - a[1]) * e
    return ks[-1][1]


def integrate(f, ks):
    """ks = [(frame, rate_per_frame), ...] piecewise-linear rate; returns its integral from ks[0][0] to f
    (e.g. a spin angle from an angular speed curve). Constant rate beyond both ends."""
    if f <= ks[0][0]:
        return ks[0][1] * (f - ks[0][0])
    a = 0.0
    for (f0, w0), (f1, w1) in zip(ks, ks[1:]):
        if f <= f0:
            break
        t1 = min(f, f1)
        w_t = w0 + (w1 - w0) * (t1 - f0) / (f1 - f0)
        a += 0.5 * (w0 + w_t) * (t1 - f0)
    if f > ks[-1][0]:
        a += ks[-1][1] * (f - ks[-1][0])
    return a


def typed(text, t, cps=1.0):
    """visible prefix after t frames at cps chars per frame"""
    if t is None:
        return text
    return text[:max(0, min(len(text), int(t * cps + 1e-6)))]
