# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""skia raster layer drawn in 1080p units (the canvas is pre-scaled by E.s) and uploaded as a premultiplied RGBA8 texture.
Fonts come from the type system (src/kit/type.py): keys hero / hero7 / accent / wide / mono / text / sc / sc_sans."""
import numpy as np
import skia
from src.kit import type as TY


def paint(color=(1, 1, 1), alpha=1.0, stroke=None, cap='butt', dash=None, join=None):
    p = skia.Paint(); p.setAntiAlias(True)
    p.setColor4f(skia.Color4f(float(color[0]), float(color[1]), float(color[2]), float(max(0.0, min(1.0, alpha)))))
    if stroke is not None:
        p.setStyle(skia.Paint.kStroke_Style); p.setStrokeWidth(float(stroke))
        p.setStrokeCap({'butt': skia.Paint.kButt_Cap, 'round': skia.Paint.kRound_Cap, 'square': skia.Paint.kSquare_Cap}[cap])
        if join == 'round':
            p.setStrokeJoin(skia.Paint.kRound_Join)
        if dash:
            p.setPathEffect(skia.DashPathEffect.Make(list(map(float, dash[:2])), float(dash[2]) if len(dash) > 2 else 0.0))
    return p


class Canvas:
    """Transparent drawing layer. All coordinates / sizes in 1080p px; .c is the raw skia canvas (already scaled)."""
    W, H = 1920, 1080

    def __init__(self, E):
        self.E, self.s = E, E.s
        self.arr = np.zeros((E.H, E.W, 4), np.uint8)
        self.surface = skia.Surface(self.arr, colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kPremul_AlphaType)
        self.c = self.surface.getCanvas()

    def begin(self):
        self.c.restoreToCount(1)
        self.c.clear(skia.ColorTRANSPARENT)
        self.c.save(); self.c.scale(self.s, self.s)
        return self

    def texture(self):
        """upload -> pooled RGBA8 Tex (premultiplied alpha), valid for the current sub-frame"""
        t = self.E.new(comps=4, dtype='f1')
        t.gt.write(self.arr)
        return t

    # ---------- primitives (1080p units) ----------
    def line(self, x0, y0, x1, y1, color=(1, 1, 1), alpha=1.0, width=1.4, cap='butt', dash=None):
        self.c.drawLine(x0, y0, x1, y1, paint(color, alpha, width, cap, dash))

    def polyline(self, pts, color=(1, 1, 1), alpha=1.0, width=1.4, closed=False, cap='butt', dash=None):
        if len(pts) < 2:
            return
        path = skia.Path(); path.moveTo(*map(float, pts[0]))
        for p in pts[1:]:
            path.lineTo(*map(float, p))
        if closed:
            path.close()
        self.c.drawPath(path, paint(color, alpha, width, cap, dash, join='round'))

    def rect(self, x, y, w, h, color=(1, 1, 1), alpha=1.0, stroke=None):
        self.c.drawRect(skia.Rect.MakeXYWH(x, y, w, h), paint(color, alpha, stroke))

    def circle(self, x, y, r, color=(1, 1, 1), alpha=1.0, stroke=None):
        self.c.drawCircle(x, y, r, paint(color, alpha, stroke))

    def arc(self, x, y, r, start_deg, sweep_deg, color=(1, 1, 1), alpha=1.0, width=1.4, cap='butt'):
        if abs(sweep_deg) < 1e-3:
            return
        self.c.drawArc(skia.Rect.MakeLTRB(x - r, y - r, x + r, y + r), start_deg, sweep_deg, False, paint(color, alpha, width, cap))

    def text(self, s, x, y, key='mono', size=24, color=(1, 1, 1), alpha=1.0, tracking=None, align='left', caps=True):
        """y is the baseline. Same rules as type.word (caps for hero/accent/wide/mono). Returns the advance width."""
        b = TY.word(self, s, x, y, size, key, color, alpha, align, tracking, caps=caps)
        return b[2] - b[0]

    @staticmethod
    def measure(s, key='mono', size=24, tracking=None):
        return TY.measure(s, key, size, tracking)

    @staticmethod
    def cap_height(key, size):
        return TY.cap_height(key, size)
