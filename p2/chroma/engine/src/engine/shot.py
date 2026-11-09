# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""Shot protocol: a shot is a function  fn(E, T) -> Tex  decorated with @shot(subframes=...).
STATE (cuts, footage frames, impact frames) is chosen from the integer frame T.F; MOTION (camera, type, particles) uses T.f."""
from src import timeline as TL


def clamp01(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else float(x)


class T:
    """time handle. f = film frame incl. sub-frame offset (graphics/motion); F = integer film frame (footage: E.clip(take, T.F))."""
    __slots__ = ('f', 'F', 'name', 'module', 'start', 'end', 'what', 'n_sub', 'k_sub')

    def __init__(self, f, F, row, n_sub=1, k_sub=0):
        self.f, self.F = float(f), int(F)
        self.name, self.module, self.start, self.end, self.what = row
        self.n_sub, self.k_sub = n_sub, k_sub

    lf = property(lambda self: self.f - self.start)       # local frame (float)
    lF = property(lambda self: self.F - self.start)       # local frame (int)
    sec = property(lambda self: self.f / TL.FPS)          # film seconds

    @staticmethod
    def at(bar, beat=1, sub=0):
        """exact (float) film frame of bar/beat/16th on the timeline grid"""
        return TL.f(bar, beat, sub)

    def since(self, bar, beat=1, sub=0):
        return self.f - TL.f(bar, beat, sub)

    def prog(self, a, b):
        """0..1 progress of f between film frames a and b"""
        return clamp01((self.f - a) / float(b - a))

    def beat_pos(self):
        """(bar, beat, sixteenth) 1-based, from F"""
        return TL.pos(self.F)

    def slice(self):
        """[t0, t1] film-frame interval this sub-frame exposes (180 deg shutter split over n_sub)"""
        h = 0.5 / max(1, self.n_sub) / 2
        return self.f - h, self.f + h

    def taps(self, n):
        """n sample times inside this sub-frame's exposure (in-shader / multi-tap motion blur)"""
        a, b = self.slice()
        if n <= 1:
            return [self.f]
        return [a + (b - a) * (i + 0.5) / n for i in range(n)]


def shot(subframes=1):
    """@shot(subframes=4) or @shot(subframes=lambda T: 6 if T.lF < 40 else 1). 180-degree shutter is applied by the engine."""
    def deco(fn):
        fn._is_shot = True
        fn.subframes = subframes
        return fn
    return deco


def n_subframes(fn, t):
    s = getattr(fn, 'subframes', 1)
    return int(s(t) if callable(s) else s)
