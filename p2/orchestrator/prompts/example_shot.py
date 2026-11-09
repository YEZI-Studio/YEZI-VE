# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""整镜示例（CHROMA）：跟拍——有纹理的实拍世界单向流过，每个计划切点换世界，前景是实拍叠层（比世界快 = 视差），
重帧上冲击帧盖在新世界上、字换版式。函数名、帧号、素材号、叠层 kind 编排器已换成这一镜的。
照它的结构写，内容按 spec 改：切点、重帧照「本镜卡点」表抄全；spec 的主体有自己的纹理（生图、抠好的实拍）就写进 hero。"""
from src.engine import shot
from src.footage import Take
from src.fx import motif as MO, series as SR
from src.kit import type as TY
from src.kit.anim import eo5
from src.kit.core import PAPER

P = MO.palette('{{HEX}}')  # 签名色，全片同一个
F0, F1 = {{START_F}}, {{END_F}}  # 这一镜（止帧不含）
CUTS = [{{CUT_F}}]  # 计划切点：照「本镜卡点」表把「切」全抄进来
HIT = {{HIT_F}}  # 「重」帧：冲击帧盖在新世界上、字换版式
AT, R = (1110.0, 455.0), 340.0                               # 冲击中心、主角大致半径
LABEL = 'LABEL'                                              # 换成 spec 的 label
CLIPS = ['{{CLIP}}', '{{CLIP2}}']  # 世界层：分给这一镜的实拍（ctx/footage.md）
EDGES = [F0] + [F for F in CUTS if F0 < F < F1] + [F1]
WORLDS = [Take(CLIPS[k % len(CLIPS)], film=(EDGES[k] - 8, EDGES[k + 1] + 8), src=0.8 * (k // len(CLIPS)), speed=1.6)
          for k in range(len(EDGES) - 1)]                    # 每段换一条素材或同一条的另一个时段（= 真切），慢素材变速
HITS = [SR.hit(HIT, 'post3_blue', c=AT, k=1.0, **MO.hit_cols(P))]


def hero(E, T):
    return None  # 主角层：返回预乘纹理（生图、抠好的实拍）就钉在 AT；None = 只要流动的世界


def words(E, T):
    cv, f = E.canvas(), T.f
    big = 1.0 + 0.3 * (1.0 - eo5((f - HIT) / 4.0)) if T.F >= HIT else SR.slam(f - F0, 4, 1.3)
    TY.word(cv, '{{WORD}}', TY.MARGIN, 960 if T.F < HIT else 760, TY.size_for_cap('hero', 230), 'hero', PAPER, 1.0, scale=big)
    TY.label(cv, [LABEL], TY.MARGIN, 126, 24, PAPER, 0.9)
    return cv.texture()


@shot(subframes=1)                                           # SR.track 自带运动模糊；不用它的快镜头照必做 9
def {{FN}}(E, T):
    img = SR.track(E, T, hero, WORLDS, F0, F1, move='truck', px_per_f=10, cuts=CUTS, P=P, at=AT, hero_r=R,
                   front='{{OVERLAY}}')  # 前景：这一集叠层表里的一种（ctx/overlays.json）；None = 这一集还没有叠层
    img = E.over(img, words(E, T))
    return SR.post(E, T, img, HITS)                          # 冲击簇永远最后


{{FN}}.length = F1
