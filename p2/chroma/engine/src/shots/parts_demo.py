# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""零件联系表（不是片子，是验收用）：一次把 HANDBOOK 里列的零件各用一下，单独渲染，帧号从 0 开始，150 BPM 网格。
  0-47     SR.auto_cam 'truck' + ring_hud / callout / readout（HUD 跟着主体走）
  48       SR.tx 'whip'（相机甩过切点）       96  SR.tx 'circle'（圆形开窗）    144 SR.tx 'hit'（冲击帧盖切点）
  96-191   HE.hero 'slab'（签名色弧光描边 -> 砸 -> 扫光 -> 甩出）      192-239  HT.heat 'forge'（黑体色调）
渲染：.venv/bin/python -m src.render sheet --fn src.shots.parts_demo:parts_demo --range 0 3.98 --step 0.1 --out out/ex/parts.jpg
"""
from src.engine import shot
from src.footage import Take
from src.fx import motif as MO, series as SR, heat as HT
from src.kit import fx, hero as HE
from src.kit.core import PAPER

P = MO.palette('#3FB8FF')                                  # a cold motif colour, to see that everything follows it
HE.set_motif(P)
B = 24                                                     # beat = 24 f (150 BPM)
TA = Take('K3', film=(0, 2 * B), src=0.5)
TB = Take('K2', film=(2 * B - 8, 4 * B), src=1.0)
TC = Take('K1', film=(4 * B - 12, 6 * B), src=1.5)
TD = Take('K4', film=(6 * B - 12, 10 * B), src=0.5)
TXS = [SR.tx(2 * B, 'whip', dir='R'), SR.tx(4 * B, 'circle', c=(960, 520)),
       SR.tx(6 * B, 'hit', preset='post3_blue', c=(960, 540), **MO.hit_cols(P))]


def at(E, T, X):
    """the finished frame of whichever plate owns film frame X, rendered at time T (SR.tx_frame calls this)"""
    if X < 2 * B:
        img = MO.world(E, SR.auto_cam(E, T, E.clip(TA, T.F), 0, 2 * B, move='truck', eye=(960, 540)), P, keep='bright')
        cv = E.canvas()
        x, y = 960, 540
        SR.ring_hud(cv, x, y, T.f, 4, r=240, col=PAPER)
        SR.callout(cv, x, y, 240, -38, ['ROUNDABOUT', 'NIGHT'], T.f, 10, col=PAPER)
        SR.readout(cv, 96, 980, T.f, [(6, 0), (40, 2000)], unit=' K', size=28, col=PAPER)
        return E.over(img, cv.texture())
    if X < 4 * B:
        pose = lambda f: dict(zoom=1.15, piv=(960, 640), at=(960, 540))
        return MO.world(E, fx.cam(E, T, E.clip(TB, T.F), pose, n=2), P, keep='bright')
    if X < 6 * B:
        pose = lambda f: dict(zoom=1.3 + 0.002 * (f - 4 * B), piv=(1000, 330), at=(960, 540))
        return MO.world(E, fx.cam(E, T, E.clip(TC, T.F), pose, n=2), P, keep='bright')
    return HT.heat(E, E.clip(TD, T.F), 'forge')


@shot(subframes=1)
def parts_demo(E, T):
    img = SR.tx_frame(E, T, TXS, lambda X: at(E, T, X))
    return HE.hero(E, img, T.f, 'GLOW', start=4 * B + 6, land=4 * B + 14, leave=6 * B, style='slab', label='SLAB · ARC REVEAL')


parts_demo.length = 10 * B
