# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""镜头模板 = HANDBOOK.md §1 的代码（实测能渲）：.venv/bin/python -m src.render still --fn src.shots.template:my_shot --F 100 --out out/me/t.png"""
from src import timeline as TL
from src.engine import shot
from src.footage import Take
from src.fx import motif as MO, series as SR
from src.kit import fx, type as TY
from src.kit.anim import keys, eo3, eo5
from src.kit.core import PAPER

P = MO.palette('#3FB8FF')                         # 这一集的签名色，全片同一个
HIT = TL.F(2)                                      # 第 2 小节第 1 拍（整数帧）；有「本镜卡点」表时直接写表里的帧，如 HIT = 1352
PLATE = Take('K2', film=(TL.F(2), TL.F(3)), src=1.2, speed=[(TL.F(2), 0.35), (TL.F(2) + 10, 1.0)])
HITS = [SR.hit(HIT, 'post3_blue', c=(960, 600), k=1.0, **MO.hit_cols(P))]

@shot(subframes=1)
def my_shot(E, T):
    pose = lambda f: dict(zoom=keys(f, [(HIT, 1.3, 'eo3'), (HIT + 10, 1.1), (TL.F(3), 1.18)]), piv=(960, 640), at=(960, 540))
    img = MO.world(E, fx.cam(E, T, E.clip(PLATE, T.F), pose, n=3), P, keep='bright')   # 先相机，再调色
    cv = E.canvas()
    TY.word(cv, 'SPARK', TY.MARGIN, 960, TY.size_for_cap('hero', 230), 'hero', PAPER, scale=1 + 0.3 * (1 - eo5((T.f - HIT) / 4)))
    TY.label(cv, ['FIREWORK'], TY.MARGIN, 126, 24, PAPER, 0.9)
    return SR.post(E, T, E.over(img, cv.texture()), HITS)   # 冲击簇永远最后


my_shot.length = TL.F(3)
