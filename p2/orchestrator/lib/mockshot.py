# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""--mock 用的「假代理」镜头代码：按镜头表机械拼出一段真能被 CHROMA 引擎渲染的 shot.py（照 ex_open 的写法：
只用来测流程和渲染通路，不代表审美。variant 是返修版本号：改了它，构图轮换和取材起点就变，渲染一定不同。"""
from __future__ import annotations

KINDS = {
    "design": ["paper", "post3_blue", "plate", "duo_blue", "paper", "lines", "plate", "half_blue"],
    "footage": ["plate", "plate", "post3_blue", "plate", "paper", "plate", "duo_blue", "plate"],
}
HIT_PRESET = {"drop": "voronoi", "big": "post3_blue", "final": "lines_focus", "small": "lines"}
# 转场声明的 kind → 引擎函数（HANDBOOK §9 的单边写法；双边的 kind 两边都画自己这一镜，mock 只为走通「切点调对应函数」这条必做）
TX_CODE = {"甩": "SR.tx({F}, 'whip', dir='L')", "推穿": "SR.tx({F}, 'push')", "运动接运动": "SR.tx({F}, 'smear', dir='L')",
           "对位": "SR.tx({F}, 'shape')", "遮挡": "SR.tx({F}, 'occlude', dir='R')",
           "冲击帧盖切点": "SR.tx({F}, 'hit', preset='post3_blue', c=(960, 540), **MO.hit_cols(P))", "万花筒": "SR.tx({F}, 'kaleido')"}


def tx_list(spec):
    """入点（首镜第 0 帧不做：那是首尾循环）、出点（接回 s01 的不做）对应的 SR.tx 调用。"""
    out = []
    ti, to = spec.get("transition_in") or {}, spec.get("transition_out") or {}
    if spec["start_f"] > 0 and ti.get("kind") in TX_CODE:
        out.append(TX_CODE[ti["kind"]].format(F=spec["start_f"]))
    if to.get("to") not in (None, "s01") and to.get("kind") in TX_CODE:
        out.append(TX_CODE[to["kind"]].format(F=spec["end_f"]))
    return out


def shot_code(fn, spec, clips, hexcol, variant=0, tag=None, static=False, cheap=False):
    """static=True：故意做成「PPT 感」——每个构图切进来就停住（只靠切点换画面），用来走一遍镜内运动量门禁：
    机器量判不过 → 返修（variant+1，static 关掉）→ 镜头里一直在动、过关。
    cheap=True：代码里多一个从不调用的函数，里面是 2D 粒子（SR.particles、SR.track(front='snow')），用来走一遍劣质特效静态扫描：
    交付前查代码发回一次 → 假代理看到发回就交干净版。从不调用，所以真引擎渲染也不受影响。"""
    a, b = spec["start_f"], spec["end_f"]
    hits_sync = {r["F"] for r in spec.get("sync") or [] if r.get("lvl") in ("重", "轻", "切") and a < r["F"] < b}
    begins = sorted({a} | {c for c in spec.get("cuts_f", []) if a < c < b} | hits_sync)   # 「本镜卡点」表的重 / 轻帧都换构图
    look = spec.get("look", "design")
    cyc = KINDS.get(look, KINDS["design"])
    kinds = [cyc[(i + variant) % len(cyc)] for i in range(len(begins))]
    if not clips:
        kinds = ["paper" if k == "plate" else k for k in kinds]
    words = [w for w in (spec.get("words") or []) if w] or [spec.get("hero_word") or spec["id"].upper()]
    words = [words[i % len(words)] for i in range(len(begins))]
    takes = []
    for i, f0 in enumerate(begins):
        f1 = begins[i + 1] if i + 1 < len(begins) else b
        if not clips:
            break
        c = clips[(i + variant + int(spec["id"][1:] or 0)) % len(clips)]
        seg = (f1 - f0) / 60.0
        room = max(0.0, float(c.get("dur") or 4.0) - seg - 0.6)
        src = round((i * 3.1 + variant * 1.7 + int(spec["id"][1:] or 0) * 0.9) % room, 2) if room > 0 else 0.0
        takes.append((c["name"], f0, f1, src))
    hits = [(h["F"], HIT_PRESET[h["kind"]]) for h in spec.get("hits", []) if h["kind"] in HIT_PRESET and a <= h["F"] < b]
    zoom = round(1.15 + 0.12 * (variant % 3), 2)
    return f'''"""mock 镜头 {fn}（--mock：按镜头表机械拼出来的画面，只测流程和渲染通路，不代表审美）。
look={look}；{len(begins)} 个构图。"""
from src.engine import shot
from src.footage import Take
from src.fx import motif as MO, series as SR
from src.kit import fx, type as TY
from src.kit.anim import eo3, eo5, clamp01
from src.kit.core import INK, PAPER

variant = {variant}
STATIC = {bool(static)!r}
P = MO.palette({hexcol!r})
START, END = {a}, {b}
BEGINS = {begins!r}
KINDS = {kinds!r}
WORDS = {words!r}
LABEL = {(spec.get("label") or "")!r}
ZOOM = {zoom}
TAKES = [Take(c, film=(f0, f1), src=s) for c, f0, f1, s in {takes!r}]
HITS = [SR.hit(F, pre, c=(960, 560), k=1.0, **MO.hit_cols(P)) for F, pre in {hits!r}]
TXS = [{", ".join(tx_list(spec))}]


def cell(F):
    k = 0
    for i, f0 in enumerate(BEGINS):
        if F >= f0:
            k = i
    return k


def plate(E, T, k):
    kind, f0 = KINDS[k], BEGINS[k]
    end = BEGINS[k + 1] if k + 1 < len(BEGINS) else END
    if not TAKES:
        return E.solid(PAPER if kind == "paper" else INK)
    sx = 1.0 if k % 2 else -1.0

    def pose(f):
        if STATIC:                                     # PPT 感：切进来就停住
            return dict(zoom=ZOOM, piv=(960, 540), at=(1240, 470) if kind == "paper" else (960, 540))
        u = clamp01((f - f0) / max(1, end - f0))       # 每个构图里镜头一直在走：推近 + 横移 + 转一点
        return dict(zoom=ZOOM * (1.0 + 0.10 * (1.0 - eo3((f - f0) / 6.0)) + 0.35 * u),
                    roll=sx * (2.0 + 4.0 * u), piv=(960, 540),
                    at=((1240, 470) if kind == "paper" else (960 + sx * 260 * (u - 0.5), 540 - 70 * (u - 0.5))))
    img = MO.world(E, fx.cam(E, T, E.clip(TAKES[k], T.F), pose, n=2), P, keep="bright")
    if kind == "paper":
        cv = E.canvas()
        u = 0.0 if STATIC else clamp01((T.f - f0) / max(1, end - f0))
        cv.circle(1240 - sx * 220 * u, 470 + 60 * u, 300 * (0.6 + 0.4 * eo5((T.f - f0) / 4.0) + (0.0 if STATIC else 0.5 * u)), (1, 1, 1))
        img = E.mix(E.solid(PAPER), img, cv.texture(), ch="a")
    elif kind != "plate":
        img = SR.card(E, img, T.F, kind, c=(960, 540), j=(T.F - f0) // 3 % 2, **MO.hit_cols(P))
    return img


def type_layer(E, T, k):
    kind, f0 = KINDS[k], BEGINS[k]
    ink = INK if kind == "paper" or not TAKES else PAPER
    cv = E.canvas()
    TY.label(cv, [LABEL], TY.MARGIN, 126, 24, ink, 0.9)
    sz = TY.size_for_cap("hero", 210)
    end = BEGINS[k + 1] if k + 1 < len(BEGINS) else END
    u = 0.0 if STATIC else clamp01((T.f - f0) / max(1, end - f0))
    TY.word(cv, WORDS[k], TY.MARGIN + 220 * u, 960 - 40 * u, sz, "hero", ink, 1.0,
            scale=1.0 + 0.25 * (1.0 - eo5((T.f - f0) / 4.0)) + 0.12 * u)
    return cv.texture()


def frame(E, T):
    k = cell(T.F)
    img = plate(E, T, k)
    return E.over(img, type_layer(E, T, k))


@shot(subframes=1)
def {fn}(E, T):
    img = SR.tx_frame(E, T, TXS, lambda X: frame(E, T)) if TXS else frame(E, T)
    return SR.post(E, T, img, HITS)


{fn}.length = END
''' + (CHEAP if cheap else "")


CHEAP = '''

def _cheap_snow(E, T, img):
    """mock：故意留的 2D 粒子（劣质特效静态扫描的靶子；从不调用）。"""
    img = SR.particles(E, T, img, 'snow', f0=START, n=60, c=(960, 300))
    return SR.track(E, T, None, TAKES, START, END, front='snow')
'''

