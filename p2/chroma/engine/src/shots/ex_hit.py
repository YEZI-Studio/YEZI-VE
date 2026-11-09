# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""示例 2 · 一拍一冲击（DROP）：蓄力 → 冲击 → 余波 + 主角字砸入，字在原位留一拍再切（底下的火花实拍照样在跑，画面不停：
不是定格；素材慢的母题照 HANDBOOK §3b 让字被推着走或主体接着动）。
引擎自带的示例镜头（演示写法，只当结构参考）。bar 2 = 帧 96-191（150 BPM：一拍 24 帧）。
  96      DROP：voronoi 冲击簇（碎裂），蓄力 93-95 在上一镜（ex_open 也 post 了同一个 HITS）
  96-143  火花实拍（单色世界），主角字 SPARKS 用 HE.hero 'slam' 砸在 96，字在原位留一拍（火花照样在跑），144 走
  144     第 3 拍：'lines' 冲击帧盖住切点（火花 -> 火焰），火焰实拍 + 第二个大字慢慢推
渲染：.venv/bin/python -m src.render video --fn src.shots.ex_hit:ex_hit --out out/ex/hit.mp4
（--fn 单镜渲染时帧号从 0 开始，所以本镜用 TL.F(2) 等绝对帧号写的事件，单渲要看 --range 1.6 3.2 的整片版）
"""
from src import timeline as TL
from src.engine import shot
from src.footage import Take
from src.fx import motif as MO, series as SR
from src.kit import fx, type as TY, hero as HE
from src.kit.anim import keys, eo3, eo5
from src.kit.core import PAPER

P = MO.palette('#FF6A1F')
HE.set_motif(P)                                           # hero-type reveal arcs in the motif colour

DROP, B3 = TL.F(2), TL.F(2, 3)                            # 96, 144
SPARKS = Take('K2', film=(DROP, B3), src=1.2, speed=[(DROP, 0.35), (DROP + 10, 1.0)])   # slow on the hit, then real time
FLAME = Take('K4', film=(B3, TL.F(3)), src=1.6, speed=0.8)
HITS = [SR.hit(DROP, 'voronoi', c=(960, 640), k=1.2, **MO.hit_cols(P)),
        SR.hit(B3, 'lines', c=(940, 520), k=0.9, **MO.hit_cols(P))]


def plate(E, T):
    if T.F < B3:
        pose = lambda f: dict(zoom=keys(f, [(DROP, 1.35, 'eo3'), (DROP + 10, 1.12), (B3, 1.2)]), piv=(960, 700), at=(960, 600))
        return MO.world(E, fx.cam(E, T, E.clip(SPARKS, T.F), pose, n=3), P, keep='bright')
    pose = lambda f: dict(zoom=keys(f, [(B3, 1.25, 'eo3'), (B3 + 8, 1.08), (TL.F(3), 1.16)]),
                          roll=keys(f, [(B3, -3.0, 'eo3'), (B3 + 8, 0.0)]), piv=(900, 500), at=(960, 540))
    return MO.world(E, fx.cam(E, T, E.clip(FLAME, T.F), pose, n=3), P, keep='bright')


def type_layer(E, T):
    cv = E.canvas(); f = T.f
    if T.F < B3:
        TY.label(cv, ['FIREWORK SPARKS', 'DROP · BAR 2'], TY.MARGIN, 126, 24, PAPER, 0.9 * eo3((f - DROP - 4) / 4.0))
    else:
        sz = TY.size_for_cap('wide', 150)
        TY.word(cv, 'BURN', 1920 - TY.MARGIN, 960, sz, 'wide', PAPER, 1.0, 'right',
                scale=1.0 + 0.3 * (1.0 - eo5((f - B3) / 4.0)) + 0.0008 * (f - B3))
        TY.label(cv, ['OPEN FLAME'], 1920 - TY.MARGIN, 126, 24, PAPER, 0.9, 'right')
    return cv.texture()


@shot(subframes=1)
def ex_hit(E, T):
    img = plate(E, T)
    img = HE.hero(E, img, T.f, 'SPARKS', start=DROP, land=DROP, leave=B3, style='slam', label='BAR 2 · BEAT 1')
    img = E.over(img, type_layer(E, T))
    return SR.post(E, T, img, HITS)                       # impact phases last, on the finished frame


ex_hit.length = TL.F(3)
