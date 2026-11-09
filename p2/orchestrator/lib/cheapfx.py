# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""劣质特效静态扫描（第三轮修复，2026-11-05）。

核心规则不是「2D 一律不许」，而是「2D 不许冒充物质」：雪、冰屑、碎片、雾、霜、裂缝、气泡、水滴、电弧余辉只能来自实拍 / 叠层
蓄力 / 冲击集中线、万花筒细边、展示用空心字、整帧设计帧里的图解线和纸窗墨线框、从实拍热源喷出的短命火花——这些不扫。

扫描（交付前查代码，不要 GPU，几毫秒；只发回、不当门禁，每次 make_shot 最多发回 1 次）：
  C1  2D 粒子当物质：SR.particles(...)；旧引擎副本里的 SR.track(front='字符串')（新引擎的字符串前景是实拍叠层 SR.element，
      不算）                                                          → 发回
  Z   放大倍数：实拍素材的有效放大（zoom × 1080 ÷ 解码高度）                              → 大的发回、略大的只提醒
  C3  冲击簇带火花 / 电弧 / 闪点（非火、电母题）                                            → 只提醒
  C5  引擎默认的线和环（万花筒边光写死成非签名色、旧引擎的 slab 描边字 / 锁定环、冲击预设没接 MO.hit_cols、SR.ring_hud、
      SR.shatter 没给 nxt / 张开量给得很小）                                              → 只提醒
  CG  CG 科普片 / 带烧录字幕的素材当主体                                                   → 只提醒（镜头表那一层由导演环节拦）
  C4  程序化矢量场（skia 图元在循环里大量画）                                              → 不发回，只给审片当 100 % 裁图的坐标
  C6  循环里手画的图元（skia / cv2 / PIL 在 for 里画点、线、多边形：冒充雪、碎片、裂缝就是劣质特效）→ 只提醒 + 裁图坐标

纯标准库，python3 -I 可跑。命令行：
  python3 cheapfx.py check out/shot.py [--footage ctx/footage.json] [--spec ctx/spec.json]   # ctx/check.sh 里自查用，退出码恒 0
  <引擎 Python> cheapfx.py locate a.jpg b.jpg …                                              # 像素定位器（细线连通域最密的窗口）
门禁线数字只在本文件的常量里（不写进给代理的文字）。"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys

# ----------------------------------------------------------------------------- 常量（线：只在这里）
C2_PLACE_MIN = 3          # 同一函数里同一张渲染图的放置调用 ≥ 这么多处（或任意一处在循环里）→ 复制拼装
ZOOM_WARN = 1.4           # 有效放大 > 这个只提醒
ZOOM_SEND = 2.0           # 有效放大 > 这个发回
C4_SITES = 12             # 一个函数里可见的 skia 图元调用 > 这么多处，或循环里 > C4_LOOP 处 → 记成矢量场（只给裁图坐标）
C4_LOOP = 10
HUD_W = 1.6               # 写死线宽 ≤ 这个的算 HUD 细线（白名单 ≤1.4 px，留一点余量），不算矢量场

MODS = {"src.fx.series": "SR", "src.kit.fx": "fx", "src.fx.kaleido": "KV", "src.kit.hero": "HE",
        "src.fx.motif": "MO", "src.fx.impact": "IM", "src.kit.type": "TY", "src.kit.design": "D", "src.fx.heat": "HT",
        "src.footage": "FT"}
DRAW = {"line", "polyline", "circle", "rect", "arc", "poly", "polygon", "path", "tri", "triangle", "ring",
        "drawPath", "drawLine", "drawCircle", "drawRect", "drawPoints", "drawOval", "drawArc", "drawPolygon", "drawRRect",
        "drawVertices", "drawPoint"}
NOT_RECV = {"TY", "cv2", "np", "math", "plt", "skia", "D", "SR", "IM", "HE", "KV", "MO", "E", "fx", "HT", "os", "re",
            "json", "random", "FT", "TL"}
PLACE = {"fx.cam", "fx.camera", "E.affine", "E.warp", "E.place", "fx.place"}
NO_TAINT_THRU = {"E.over", "E.add", "E.screen", "E.mix", "E.multiply", "SR.track", "SR.post", "SR.tx_frame", "SR.element",
                 "SR.shatter", "MO.world"}
TEX_ARGS = {"tex", "sprite", "img3", "src_tex", "layer"}
CLIP_ARGS = {"raw", "clip", "plate", "src", "footage", "take", "video", "source"}   # 帮手函数的参数叫这些名字的，当成实拍
HIT_CALLS = {"SR.hit", "SR.card", "SR.tx"}
BLUE_PRESETS = {"post3_blue", "duo_blue", "half_blue", "half", "voronoi", "lines", "lines_focus", "kick", "plasma"}   # pal blue / pink
DRAW_CV = {"cv2.line", "cv2.circle", "cv2.ellipse", "cv2.fillPoly", "cv2.polylines", "cv2.rectangle", "cv2.fillConvexPoly",
           "cv2.drawContours", "cv2.arrowedLine"}
DRAW_PIL = {"line", "ellipse", "polygon", "rectangle", "point", "arc", "chord", "pieslice", "regular_polygon"}   # ImageDraw.Draw(...)
SHAT_PX_MIN = 360.0       # 引擎 SR.shatter_open 的下限（张开量小 = 一张等宽直线网）
HOT_MOTIFS = ("火", "电", "fire", "volt", "雷")            # 火花、电弧是这些母题自己的物质

RULE = {"C1": "劣质特效 · 2D 粒子冒充物质", "C2": "劣质特效 · 渲染图复制拼装", "Z": "素材放大过头", "C3": "劣质特效 · 冲击簇带火花 / 电弧",
        "C5": "劣质特效 · 引擎默认的线和环", "CG": "素材 · CG / 烧录字幕当主体", "C4": "程序化矢量场（只给审片定位）",
        "C6": "劣质特效嫌疑 · 循环里手画图元"}
FIX = {
    "C1": "删掉这层 2D 粒子，在同一处换一件会动的真东西：雪 / 冰尘 / 雾 / 滴水 / 气泡用实拍叠层 SR.element（ctx/overlays.json 列了"
          "能用的 kind、素材号、窗口），单向飘、迎面推；碎屑、爆散用 SR.shatter（把这一帧画面本身碎开）。"
          "SR.track 的前景用 front='snow'（实拍雪）或 SR.element 那一层。删一层补一层，运动量不许掉",
    "C2": "删掉复制 / 挤扁拼装：同一张渲染图每帧只出画一次；要多片雪花、碎屑用实拍叠层 SR.element 或 SR.shatter",
    "Z": "实拍放大过头会糊、出马赛克：Take 写 tier=2160（4K 素材）或换一条分辨率够的素材；推近交给切点换景别，不靠一张图一直放大",
    "C3": "非火、电母题的冲击簇不要火花：删掉 sparks=…；电弧、闪点引擎默认就关着（只有电母题开），不用另写 arcs / glints，"
          "直接接 **MO.hit_cols(P)；冲击帧本身照留",
    "C5": "万花筒边光用签名色（不写 rim，或 rim=签名色）；HE.hero 用实心字 style='slam' / 'card'；冲击预设接 **MO.hit_cols(P)；"
          "HUD 细环 ≤1.4 px、不圈住主体；SR.shatter 写 nxt=下一张画面，张开量用 SR.shatter_open 的默认（一下就碎开）",
    "C6": "循环里手画的点、线、多边形如果冒充雪 / 碎屑 / 裂缝 / 霜：删掉，换实拍叠层 SR.element、SR.shatter；"
          "是 HUD 刻度、整帧设计帧里的图解线就留着（≤1.4 px、不圈主体）",
    "CG": "CG 科普片、带烧录字幕的素材只当辅助：主体换 aux_footage / 素材清单里的实拍，非用不可就取景避开字幕、不放大",
}


# ----------------------------------------------------------------------------- AST 扫描
def _dotted(f):
    parts = []
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        parts.append(f.id)
        return ".".join(reversed(parts))
    return None


def _num(n, env=None):
    """数字常量（含负号、模块级常量名）；不是返回 None。"""
    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
        return float(n.value)
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
        v = _num(n.operand, env)
        return -v if v is not None else None
    if isinstance(n, ast.Name) and env and n.id in env:
        return env[n.id]
    return None


def _lead(n, env=None):
    """放大倍数表达式的「起步值」：常量；乘法里常量因子的积（exp(...)、(1 + k·u) 这类增长项按 1 算）；加法里常量项之和。
    算不出来返回 None。"""
    v = _num(n, env)
    if v is not None:
        return v
    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mult):
        a, b = _lead(n.left, env), _lead(n.right, env)
        if a is None and b is None:
            return None
        return (a if a is not None else 1.0) * (b if b is not None else 1.0)
    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
        a, b = _num(n.left, env), _num(n.right, env)
        if a is not None or b is not None:
            return (a or 0.0) + (b or 0.0)
    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Pow):
        return _num(n.left, env) if _num(n.left, env) is not None and _num(n.right, env) is not None else None
    return None


def _xy(node, env=None):
    """(x, y) 数字元组 → (x, y)；不是返回 None。"""
    if isinstance(node, (ast.Tuple, ast.List)) and len(node.elts) >= 2:
        x, y = _num(node.elts[0], env), _num(node.elts[1], env)
        if x is not None and y is not None:
            return x, y
    return None


def _frame_test(n):
    """if 的条件里看的是帧号（T.F / T.f / F / f / SR.seg(...)）：分支 = 按时间分段，各分支同一帧只走一个。"""
    for x in ast.walk(n):
        if isinstance(x, ast.Attribute) and x.attr in ("F", "f", "t") and isinstance(x.value, ast.Name) and x.value.id == "T":
            return True
        if isinstance(x, ast.Name) and x.id in ("F", "f", "fr", "frame", "seg", "i_seg"):
            return True
        if isinstance(x, ast.Call) and (_dotted(x.func) or "").split(".")[-1] in ("seg", "since", "track_seg"):
            return True
    return False


class _Scan(ast.NodeVisitor):
    def __init__(self, alias, env, motif, old_engine=False):
        self.alias, self.env, self.motif, self.old_engine = alias, env, motif, old_engine
        self.loop = 0
        self.ctx = []                     # ('L',) 循环 / ('B', 链, 臂) 按帧号分段的分支，从外到内
        self.taint = set()
        self.c1, self.c3, self.c5, self.place, self.vec, self.zoom = [], [], [], [], [], []
        self.loopdraw, self.zoomx = [], []
        self.pil = set()
        self.div = set()                  # 局部变量的值里有除法（z = size / h）：zoom=z 算不出来
        self.seen = set()
        self.has_clip = False

    def name(self, f):
        d = _dotted(f)
        if not d:
            return ""
        head, _, rest = d.partition(".")
        h2 = self.alias.get(head)
        if h2:
            return h2 + ("." + rest if rest else "")
        return d

    def _loop(self, node):
        self.loop += 1
        self.ctx.append(("L",))
        self.generic_visit(node)
        self.ctx.pop()
        self.loop -= 1
    visit_For = visit_While = visit_AsyncFor = _loop
    visit_ListComp = visit_SetComp = visit_DictComp = visit_GeneratorExp = _loop

    def visit_If(self, node, chain=None, arm=0):
        self.visit(node.test)
        if not _frame_test(node.test):
            for x in node.body + node.orelse:
                self.visit(x)
            return
        cid = chain if chain is not None else id(node)
        self.ctx.append(("B", cid, arm))
        for x in node.body:
            self.visit(x)
        self.ctx.pop()
        if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):   # elif：同一条链的下一臂
            self.visit_If(node.orelse[0], cid, arm + 1)
        elif node.orelse:
            self.ctx.append(("B", cid, arm + 1))
            for x in node.orelse:
                self.visit(x)
            self.ctx.pop()

    def visit_IfExp(self, node):
        self.visit(node.test)
        if not _frame_test(node.test):
            self.visit(node.body)
            self.visit(node.orelse)
            return
        for arm, x in enumerate((node.body, node.orelse)):
            self.ctx.append(("B", id(node), arm))
            self.visit(x)
            self.ctx.pop()

    def _where(self):
        """(在循环里、而且这一圈里没有按帧号分段, 最外层分段分支的 (链, 臂) 或 None)"""
        last_l = max((i for i, c in enumerate(self.ctx) if c[0] == "L"), default=-1)
        in_loop = last_l >= 0 and not any(c[0] == "B" for c in self.ctx[last_l + 1:])
        br = next(((c[1], c[2]) for c in self.ctx if c[0] == "B"), None)
        return in_loop, br

    def visit_Attribute(self, node):
        if id(node) not in self.seen and self.name(node) == "SR.particles":   # partial(SR.particles, …) / p = SR.particles
            self.c1.append({"line": node.lineno, "call": "SR.particles（引用）", "kind": None, "xy": None, "f0": None})
        self.generic_visit(node)

    def visit_Name(self, node):
        if id(node) not in self.seen and self.alias.get(node.id) == "SR.particles" and isinstance(node.ctx, ast.Load):
            self.c1.append({"line": node.lineno, "call": "SR.particles（引用）", "kind": None, "xy": None, "f0": None})

    def visit_Assign(self, node):
        calls = [self.name(x.func) for x in ast.walk(node.value) if isinstance(x, ast.Call)]
        names = {x.id for x in ast.walk(node.value) if isinstance(x, ast.Name)}
        tg = [x.id for t in node.targets for x in ast.walk(t) if isinstance(x, ast.Name)]
        top = self.name(node.value.func) if isinstance(node.value, ast.Call) else None
        if names & self.taint and top not in NO_TAINT_THRU and not any(c.endswith(".canvas") for c in calls):
            self.taint.update(tg)
        if top and (top.endswith("ImageDraw.Draw") or top == "Draw"):
            self.pil.update(tg)
        if any(isinstance(x, ast.BinOp) and isinstance(x.op, ast.Div) for x in ast.walk(node.value)):
            self.div.update(tg)
        self.generic_visit(node)

    def visit_Call(self, node):
        nm = self.name(node.func)
        last = nm.rsplit(".", 1)[-1] if nm else ""
        ln = node.lineno
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        if nm in ("E.clip", "Take", "FT.Take") or last == "src_time":
            self.has_clip = True
        # C1：2D 粒子
        if nm == "SR.particles":
            self.seen.add(id(node.func))
        if nm == "getattr" and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and node.args[1].value == "particles" \
                and self.name(node.args[0]) == "SR":
            self.c1.append({"line": ln, "call": "getattr(SR, 'particles')", "kind": None, "xy": None, "f0": None})
        if nm == "SR.particles":
            kind = None
            if len(node.args) >= 4 and isinstance(node.args[3], ast.Constant):
                kind = node.args[3].value
            if isinstance(kw.get("kind"), ast.Constant):
                kind = kw["kind"].value
            self.c1.append({"line": ln, "call": "SR.particles", "kind": kind, "xy": _xy(kw.get("c"), self.env),
                            "f0": _num(kw.get("f0"), self.env)})
        if nm == "SR.track" and isinstance(kw.get("front"), ast.Constant) and isinstance(kw["front"].value, str):
            fv = kw["front"].value                                   # 11-05 起引擎把字符串前景换成实拍叠层（SR.element）：旧副本才是 2D 粒子
            if self.old_engine:
                self.c1.append({"line": ln, "call": "SR.track(front)", "kind": fv, "xy": None, "f0": None})
        # C3 / C5：冲击簇的线、火花，引擎默认的线和环
        if nm in HIT_CALLS:
            pre = node.args[1].value if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) else None
            if isinstance(kw.get("preset"), ast.Constant):
                pre = kw["preset"].value
            vals = {k: _num(kw[k], self.env) for k in ("sparks", "glints", "arcs") if k in kw}
            hot = any(m in (self.motif or "") for m in HOT_MOTIFS)
            if not hot and any((v or 0) > 0 for v in vals.values()):
                self.c3.append({"line": ln, "call": nm, "what": f"{pre or ''} " + "、".join(f"{k}={v:g}" for k, v in vals.items() if v)})
            if pre == "voronoi" and (_num(kw.get("lk"), self.env) or 0) > 0:
                self.c3.append({"line": ln, "call": nm, "what": f"voronoi lk={_num(kw.get('lk'), self.env):g}（碎裂线）"})
            splat = any(k.arg is None for k in node.keywords)          # **MO.hit_cols(P)
            if pre in BLUE_PRESETS and not splat and not ({"glints", "arcs"} & set(kw)) and not hot:
                self.c5.append({"line": ln, "call": nm, "what": f"{pre} 没接 **MO.hit_cols(P)：" + (
                    "这一集的旧引擎里 'blue' 调色板默认带电弧和闪点" if self.old_engine else "冲击帧的颜色不是签名色")})
            if nm == "SR.tx" and pre == "kaleido":
                self._rim(node, kw, ln, "SR.tx('kaleido')")
            if nm == "SR.tx" and pre == "circle" and self.old_engine:
                self.c5.append({"line": ln, "call": nm, "what": "'circle'：这一集的旧引擎里是 3 px 冰蓝环 + 十字准星 + 锁定环，圈在主体上"})
        if nm == "SR.ring_hud":
            self.c5.append({"line": ln, "call": nm, "what": "HUD 锁定环（十字准星 + 刻度环 + 两道大弧）：≤1.4 px、签名色、不圈住主体"})
        if nm == "SR.shatter" and "nxt" not in kw and len(node.args) < 8:
            self.c5.append({"line": ln, "call": nm, "what": "没给 nxt：缝里露出的是这一帧自己压暗的一份，写 nxt=下一张画面更好"})
        if nm == "SR.shatter_open":
            px = _num(kw.get("px"), self.env) if "px" in kw else (_num(node.args[2], self.env) if len(node.args) > 2 else None)
            if px is not None and px < SHAT_PX_MIN:
                self.c5.append({"line": ln, "call": nm, "what": f"px={px:g} 张开量很小（等宽细缝 = 假裂缝），引擎会抬到下限"})
        if nm == "KV.Vortex":
            self._rim(node, kw, ln, "KV.Vortex")
        if nm == "HE.hero" and isinstance(kw.get("style"), ast.Constant) and kw["style"].value == "slab" and self.old_engine \
                and not any(m in (self.motif or "") for m in HOT_MOTIFS):   # 新引擎 slab 在非电母题是实心字揭开
            self.c5.append({"line": ln, "call": nm, "what": "style='slab'：这一集的旧引擎里是电弧描边空心字"})
        # C2：同一张渲染图的放置
        if nm in PLACE or re.match(r"_?place", last.lower()) or last.lower().endswith("_place"):
            args = list(node.args) + [k.value for k in node.keywords]
            hit = [x.id for a in args for x in ast.walk(a) if isinstance(x, ast.Name) and x.id in self.taint]
            if hit:
                lp, br = self._where()
                self.place.append({"line": ln, "call": nm, "var": hit[0], "loop": lp, "br": br})
        # C4：skia 图元（接收者不是模块名）
        if last in DRAW and "." in nm and nm.split(".")[0] not in NOT_RECV and not nm.startswith("cv2."):
            w = None
            for k in ("w", "width", "lw", "stroke", "sw", "wid"):
                if k in kw:
                    w = _num(kw[k], self.env)
            nums = [_num(a, self.env) for a in node.args[:4]]
            xy = (nums[0], nums[1]) if len(nums) >= 2 and nums[0] is not None and nums[1] is not None else None
            if w is None or w > HUD_W:
                self.vec.append({"line": ln, "call": nm, "loop": self.loop > 0, "xy": xy})
                if self.loop > 0:
                    self.loopdraw.append({"line": ln, "call": nm, "xy": xy})
        # C6：循环里 cv2 / PIL 画的图元（numpy 上手画雪、碎片）
        if self.loop > 0 and (nm in DRAW_CV or (last in DRAW_PIL and "." in nm and nm.split(".")[0] in self.pil)):
            xy = _xy(node.args[1], self.env) if nm in DRAW_CV and len(node.args) > 1 else None
            self.loopdraw.append({"line": ln, "call": nm, "xy": xy})
        # Z：放大倍数（zoom= 关键字）
        if "zoom" in kw:
            v = _lead(kw["zoom"], self.env)
            if v is not None:
                self.zoom.append({"line": ln, "zoom": v})
            elif self._divish(kw["zoom"]):
                self.zoomx.append({"line": ln})                      # zoom=size/h 这类：算不出来，只提醒
        self.generic_visit(node)

    def visit_Dict(self, node):
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and k.value == "zoom":
                z = _lead(v, self.env)
                if z is not None:
                    self.zoom.append({"line": node.lineno, "zoom": z})
                elif self._divish(v):
                    self.zoomx.append({"line": node.lineno})
        self.generic_visit(node)

    def _divish(self, n):
        return any((isinstance(x, ast.BinOp) and isinstance(x.op, ast.Div)) or (isinstance(x, ast.Name) and x.id in self.div)
                   for x in ast.walk(n))

    def _rim(self, node, kw, ln, what):
        for k in ("rim", "rim2"):
            v = kw.get(k)
            if v is None:
                continue
            d = _dotted(v) or ""
            if (isinstance(v, ast.Constant) and isinstance(v.value, str)) or re.search(r"\b(ICE|VIOLET|CYAN|MAGENTA|WHITE)\b", d):
                self.c5.append({"line": ln, "call": what, "what": f"{k}={ast.unparse(v)[:40]}：边光写死成固定色（用签名色）"})


def _aliases(tree):
    al = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            for a in n.names:
                full = f"{n.module}.{a.name}"
                if full in MODS:
                    al[a.asname or a.name] = MODS[full]
                elif n.module in MODS:                                 # from src.fx.series import particles
                    al[a.asname or a.name] = f"{MODS[n.module]}.{a.name}"
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name in MODS:
                    al[a.asname or a.name] = MODS[a.name]
    al.pop("Take", None)
    return al


def _consts(tree):
    env = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            v = _num(n.value, env)
            if v is not None:
                env[n.targets[0].id] = v
    return env


def _takes(tree, env, dyn=None):
    """[(素材名, tier)]：Take('F15', ..., tier=2160)。素材名不是字面量（Take(n, …) for n in CLIPS）的记进 dyn（列表）。"""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and (_dotted(n.func) or "").split(".")[-1] == "Take" and n.args:
            tier = 1080.0
            for k in n.keywords:
                if k.arg == "tier":
                    tier = _num(k.value, env) or 1080.0
            if isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                out.append((n.args[0].value, tier))
            elif dyn is not None:
                dyn.append(tier)
    return out


def _c2_count(place):
    """同一条按帧号分段的 if 链只算放得最多的那一臂。"""
    n = sum(1 for p in place if not p.get("br"))
    chains = {}
    for p in place:
        if p.get("br"):
            c, a = p["br"]
            chains.setdefault(c, {}).setdefault(a, 0)
            chains[c][a] += 1
    return n + sum(max(v.values()) for v in chains.values())


def scan(src, clips=None, spec=None, motif=None, old_engine=False):
    """扫一份镜头代码。clips = 素材清单（ctx/footage.json 的 clips 或 inputs/footage.json 的 clips：name、w、h、cg、burned_text）；
    spec = 这一镜的 spec（subject_src）；old_engine = 这一集的引擎副本是 11-05 以前的（SR.track 的字符串前景还是 2D 粒子）。
    返回 {c1, c2, c3, c5, c4, zoom, cg, coords, error}，每条带行号。"""
    out = {"c1": [], "c2": [], "c3": [], "c5": [], "c4": [], "c6": [], "zoom": [], "cg": [], "coords": [], "error": None}
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        out["error"] = f"语法错（第 {e.lineno} 行）：{e.msg}"
        return out
    alias, env = _aliases(tree), _consts(tree)
    lines = src.splitlines()
    code = lambda ln: lines[ln - 1].strip()[:140] if 0 < ln <= len(lines) else ""  # noqa: E731
    units = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for cl in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
        units += [m for m in cl.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
    mod = ast.Module(body=[n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))],
                     type_ignores=[])
    vec, ldraw, zx = [], [], []
    for u in units + [mod]:
        sc = _Scan(alias, env, motif, old_engine)
        args = set() if isinstance(u, ast.Module) else {a.arg for a in u.args.args}
        sc.visit(u)
        fn = getattr(u, "name", "<module>")
        out["c1"] += [dict(x, fn=fn, code=code(x["line"])) for x in sc.c1]
        out["c3"] += [dict(x, fn=fn, code=code(x["line"])) for x in sc.c3]
        out["c5"] += [dict(x, fn=fn, code=code(x["line"])) for x in sc.c5]
        neff = _c2_count(sc.place)
        if neff >= C2_PLACE_MIN or any(p["loop"] for p in sc.place):
            out["c2"] += [dict(p, fn=fn, n=neff, code=code(p["line"])) for p in sc.place]
        vec += [dict(v, fn=fn) for v in sc.vec]
        ldraw += [dict(v, fn=fn, code=code(v["line"])) for v in sc.loopdraw]
        foot = sc.has_clip or bool(args & CLIP_ARGS)
        if foot or not args & TEX_ARGS:
            out["zoom"] += [dict(z, fn=fn, code=code(z["line"]), sure=foot) for z in sc.zoom]
            zx += [dict(z, fn=fn, code=code(z["line"])) for z in sc.zoomx]
    nv, nl = len(vec), sum(1 for v in vec if v["loop"])          # 矢量场按整份代码数（只给审片定位，不发回）
    if nv > C4_SITES or nl > C4_LOOP:
        out["c4"] = [dict(v, n=nv, n_loop=nl) for v in sorted(vec, key=lambda v: (not v["loop"], v["line"]))][:8]
    # Z：有效放大 = zoom × 1080 ÷ 解码高度（min(素材高, tier)）；一份代码里几条素材解码高度不同时取最高的（宽松，免得误发回）
    by = {c.get("name"): c for c in clips or []}
    dyn = []
    tk = _takes(tree, env, dyn)
    dec = [min(float(by[n].get("h") or 1080), t) for n, t in tk if n in by] or [1080.0]
    if dyn and not tk:                                         # 素材名是变量：按素材清单里最低的解码高度估，只提醒
        dec = [min(min(float(c.get("h") or 1080) for c in clips or [{"h": 1080}]), max(dyn))]
    hmax = max(dec)
    zs = []
    for z in out["zoom"] if (tk or dyn) else []:
        eff = z["zoom"] * 1080.0 / hmax
        if eff > ZOOM_WARN:
            zs.append(dict(z, eff=round(eff, 2), send=eff > ZOOM_SEND and z["sure"] and bool(tk), mixed=len(set(dec)) > 1))
    for z in zx if (tk or dyn) else []:
        zs.append(dict(z, zoom=None, eff=None, send=False, sure=False, calc=True))
    out["zoom"] = zs
    out["c6"] = ldraw[:8]
    out["c6_n"] = len(ldraw)
    # CG：主体素材是 CG 科普片 / 带烧录字幕
    sub = ((spec or {}).get("subject_src") or {}).get("file")
    used = {n for n, _ in tk}
    for n in sorted(used):
        c = by.get(n) or {}
        if c.get("cg") or c.get("burned_text"):
            why = "、".join(w for w, b in (("CG 科普片", c.get("cg")), ("带烧录字幕", c.get("burned_text"))) if b)
            out["cg"].append({"clip": n, "why": why, "subject": n == sub,
                              "line": next((ln for ln, s in enumerate(lines, 1) if f"'{n}'" in s or f'"{n}"' in s), 0)})
    # 裁图坐标：C1 / C2 / C4 命中调用里写死的 (x, y)
    for k in ("c1", "c4", "c6"):
        for x in out[k]:
            if x.get("xy") and 0 <= x["xy"][0] <= 1920 and 0 <= x["xy"][1] <= 1080:
                out["coords"].append({"x": x["xy"][0], "y": x["xy"][1], "rule": k.upper(), "line": x["line"], "f0": x.get("f0")})
    return out


def scan_file(path, clips=None, spec=None, motif=None, old_engine=False):
    try:
        with open(path, encoding="utf-8") as f:
            return scan(f.read(), clips, spec, motif, old_engine)
    except OSError as e:
        return dict(scan("", clips, spec, motif), error=f"读不了 {path}：{e}")


def engine_is_old(engine_root):
    """这一集的引擎副本是不是 11-05 以前的（src/fx/series.py 里没有 SR.element）：那时 SR.track 的字符串前景还是 2D 粒子。"""
    try:
        with open(os.path.join(engine_root, "src", "fx", "series.py"), encoding="utf-8") as f:
            return "def element(" not in f.read()
    except OSError:
        return False


# ----------------------------------------------------------------------------- 发回 / 提醒
def rows(res):
    """(发回的, 只提醒的)：每条 {rule, line, code, what, fix}。发回 = C1、C2、Z（大）；提醒 = C3、C5、Z（略大）、CG。"""
    send, warn = [], []
    for x in res.get("c1") or []:
        what = f"{x['call']}（{x.get('kind') or '?'}）" + ("：这一集的旧引擎里字符串前景是 2D 粒子" if x["call"] == "SR.track(front)" else "：2D 粒子画出来的")
        send.append({"rule": "C1", "line": x["line"], "code": x.get("code", ""), "what": what, "fix": FIX["C1"]})
    c2 = res.get("c2") or []
    for x in c2:
        n = sum(1 for y in c2 if y.get("fn") == x.get("fn"))
        send.append({"rule": "C2", "line": x["line"], "code": x.get("code", ""),
                     "what": f"{x['call']} 把同一张渲染图（{x['var']}）放进画面" + ("（在循环里）" if x.get("loop") else "")
                             + f"，{x.get('fn')} 里一共放了 {n} 处", "fix": FIX["C2"]})
    for x in res.get("zoom") or []:
        what = ("放大倍数是运行时算出来的（编排器算不出来），按目标像素高 ÷ 素材高放大很容易糊；先看清素材分辨率" if x.get("calc")
                else f"有效放大约 {x['eff']:g} 倍（zoom {x['zoom']:g}，按素材解码高度折算）")
        (send if x.get("send") else warn).append({"rule": "Z", "line": x["line"], "code": x.get("code", ""), "what": what, "fix": FIX["Z"]})
    for x in res.get("c3") or []:
        warn.append({"rule": "C3", "line": x["line"], "code": x.get("code", ""), "what": f"{x['call']} {x['what']}", "fix": FIX["C3"]})
    for x in res.get("c5") or []:
        warn.append({"rule": "C5", "line": x["line"], "code": x.get("code", ""), "what": f"{x['call']}：{x['what']}", "fix": FIX["C5"]})
    c6 = res.get("c6") or []
    if c6:
        ls = sorted({x["line"] for x in c6})
        warn.append({"rule": "C6", "line": ls[0], "code": c6[0].get("code", ""),
                     "what": f"循环里手画的图元 {res.get('c6_n', len(c6))} 处（第 {'、'.join(map(str, ls[:6]))} 行）", "fix": FIX["C6"]})
    for x in res.get("cg") or []:
        if x.get("subject"):
            warn.append({"rule": "CG", "line": x.get("line") or 0, "code": "", "what": f"主体素材 {x['clip']} 是{x['why']}", "fix": FIX["CG"]})
    key = lambda r: (r["rule"], r["line"])  # noqa: E731
    return sorted(send, key=key), sorted(warn, key=key)


def _row_line(r):
    at = f"第 {r['line']} 行 `{r['code']}`" if r.get("line") and r.get("code") else (f"第 {r['line']} 行" if r.get("line") else "")
    return f"- [{RULE.get(r['rule'], r['rule'])}] {at}：{r['what']}"


def sendback_text(send, warn=()):
    """发回给代理的那一节（不写门禁线数字：只写查到了什么、在哪一行、换成什么）。"""
    if not send:
        return ""
    L = ["## 劣质特效（编排器查代码查出来的，改了再交）",
         "用视频代替运动量可以，但绝对不能用劣质特效——贴图、线段这类简单东西冒充物质。冒充物质的东西（雪、冰屑、碎片、雾、霜、"
         "裂缝、气泡、水滴、电弧余辉）只能来自实拍叠层 SR.element 或画面派生（SR.shatter）。"]
    L += [_row_line(r) for r in send]
    fixes = list(dict.fromkeys(r["fix"] for r in send))
    L.append("改法：" + "；".join(fixes))
    if warn:
        L.append("顺带看一眼（只提醒）：" + "；".join(f"{RULE.get(r['rule'], r['rule'])} 第 {r['line']} 行 {r['what']}" for r in warn[:6]))
    return "\n".join(L) + "\n"


def summary(res, send, warn, sent=0):
    """done.json 的 cheapfx 字段。"""
    return {"sent_back": sent, "send": [_brief(r) for r in send], "warn": [_brief(r) for r in warn],
            "c4_sites": len(res.get("c4") or []), "coords": (res.get("coords") or [])[:6], "error": res.get("error")}


def _brief(r):
    return {"rule": r["rule"], "line": r.get("line"), "what": r.get("what", "")[:160]}


# ----------------------------------------------------------------------------- 像素定位器（审片裁图用；要 numpy / cv2：引擎 Python 跑）
def locate(paths, w=640, h=360):
    """每张图（任意宽，按 1920 宽折算）里细线 / 短划线连通域最密的 w×h 窗口（1080p 坐标）。返回 [{path, box: [x, y, w, h], px, n}]。"""
    import cv2
    import numpy as np
    out = []
    for p in paths:
        im = cv2.imread(p)
        if im is None:
            out.append({"path": p, "box": None})
            continue
        g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
        s = 1920.0 / g.shape[1]
        th = cv2.morphologyEx(g, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)))
        bw = (th > 40).astype(np.uint8)
        n, lab, st, _ = cv2.connectedComponentsWithStats(bw, 8)
        keep = np.zeros(n, bool)
        for i in range(1, n):
            x, y, ww, hh, area = st[i]
            L = max(ww, hh)
            if L < 6 or area < 5:
                continue
            fill = area / float(ww * hh)
            if L / max(1.0, min(ww, hh)) >= 3.0 or fill < 0.35:
                keep[i] = True
        mask = keep[lab].astype(np.float32)
        kw, kh = max(1, int(w / s)), max(1, int(h / s))
        H_, W_ = mask.shape
        if kw >= W_ or kh >= H_:
            out.append({"path": p, "box": [0, 0, 1920, 1080], "px": int(mask.sum()), "n": int(keep.sum())})
            continue
        ii = cv2.integral(mask)
        dens = ii[kh:, kw:] - ii[:-kh, kw:] - ii[kh:, :-kw] + ii[:-kh, :-kw]
        y0, x0 = np.unravel_index(int(np.argmax(dens)), dens.shape)
        sub = mask[y0:y0 + kh, x0:x0 + kw]                            # 窗口里线条像素的重心放到裁图正中
        ys, xs = np.nonzero(sub)
        cx = (x0 + (xs.mean() if len(xs) else kw / 2)) * s
        cy = (y0 + (ys.mean() if len(ys) else kh / 2)) * s
        out.append({"path": p, "box": crop_box(cx, cy, w, h), "px": int(dens[y0, x0]), "n": int(keep.sum())})
    return out


def locate_sub(py, paths, timeout=120):
    """编排器这边：用引擎 Python 跑 locate（这个文件自己当脚本）。失败返回 {}。返回 {path: box}。"""
    if not py or not paths or not os.path.exists(py):
        return {}
    try:
        r = subprocess.run([py, "-I", "-B", os.path.abspath(__file__), "locate", *paths], capture_output=True, text=True,
                           timeout=timeout)
        rows_ = json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 and r.stdout.strip() else []
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return {}
    return {x["path"]: x.get("box") for x in rows_ if x.get("box")}


def crop_box(x, y, w=640, h=360):
    """以 (x, y) 为中心的 w×h 窗口（夹在 1920×1080 里）。"""
    X = int(max(0, min(1920 - w, round(x - w / 2))))
    Y = int(max(0, min(1080 - h, round(y - h / 2))))
    return [X, Y, w, h]


HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# ----------------------------------------------------------------------------- 命令行
def _check_cli(argv):
    """ctx/check.sh 里跑：打印查到的（发回会发的、只提醒的），退出码恒 0（不挡 check.sh 后面的引擎自查）。"""
    code = argv[0] if argv else "out/shot.py"
    opt = {argv[i]: argv[i + 1] for i in range(1, len(argv) - 1) if argv[i].startswith("--")}
    def rj(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError, TypeError):
            return None
    fj = rj(opt.get("--footage")) or {}
    spec = rj(opt.get("--spec")) or {}
    res = scan_file(code, fj.get("clips") or [], spec, opt.get("--motif") or (spec.get("motif") if isinstance(spec, dict) else None),
                    engine_is_old(opt["--engine"]) if opt.get("--engine") else False)
    send, warn = rows(res)
    if res.get("error"):
        print(f"劣质特效自查：{res['error']}")
        return 0
    if not send and not warn:
        print("劣质特效自查：没查到（2D 粒子、渲染图复制拼装、放大过头都没有）")
        return 0
    if send:
        print(sendback_text(send, warn).rstrip())
        print("（交付后编排器会照这个发回一次）")
    elif warn:
        print("劣质特效自查：只提醒——" + "；".join(f"{RULE.get(r['rule'])} 第 {r['line']} 行 {r['what']}" for r in warn[:6]))
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "check":
        sys.exit(_check_cli(a[1:]))
    if a and a[0] == "locate":
        print(json.dumps(locate(a[1:])))
        sys.exit(0)
    if a and a[0] == "scan":
        print(json.dumps({p: scan_file(p) for p in a[1:]}, ensure_ascii=False, indent=1, default=str))
        sys.exit(0)
    print(__doc__)
    sys.exit(2)
