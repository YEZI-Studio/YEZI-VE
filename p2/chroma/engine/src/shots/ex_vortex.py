# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""示例 3 · 万花筒「时光穿梭」：段落之间的转场。A 折叠 → 越转越快 → 漩涡眼里打开 B；转的时候片名字母逐个砸入。
引擎自带的示例镜头（演示写法，只当结构参考）。bars 3-4 = 帧 192-383。
  192-305  Vortex(f0=192, f1=306)：fold 0-22 % / spin 22-80 % / portal 80-100 %（A = 环岛夜景，取样中心 = 环岛中心）
  240-     字母 CHROMA 在 32 分音符上逐个砸入，portal 时随画面飞出
  306-383  B = 火焰实拍落定；第 4 小节第 3 拍（336）一个 post3 冲击簇；字 HEAT 砸入、落地
渲染：.venv/bin/python -m src.render video --range 3.2 6.4 --out out/ex/vortex.mp4（时间线模式，帧号 = 片子帧号）
"""
from src import timeline as TL
from src.engine import shot
from src.footage import Take
from src.fx import motif as MO, series as SR, kaleido as KV
from src.kit import fx, type as TY
from src.kit.anim import keys, eo3, eo5, clamp01, lerp
from src.kit.core import PAPER

P = MO.palette('#FF6A1F')
F0, F1 = TL.F(3), TL.F(4, 1, 3)                           # 192, 306 (portal ends on a 16th)
A = Take('K3', film=(F0, F1), src=0.2, speed=0.7)
B = Take('K4', film=(F1 - 30, TL.END), src=0.5, speed=0.8)  # B is visible inside the portal before F1
V = KV.Vortex(F0, F1, end='portal', nseg=6, nseg_end=12, src_c=(960, 540), wedge=-90.0, eye=(960, 540), **MO.vortex_cols(P))
LET = [TL.f(3, 3) + 3.0 * i for i in range(6)]            # one letter per 32nd from bar 3 beat 3
HITS = [SR.hit(TL.F(4, 3), 'post3_blue', c=(980, 520), k=1.0, **MO.hit_cols(P))]


def plate_a(E, T):
    pose = lambda f: dict(zoom=keys(f, [(F0, 1.0, 'eio3'), (F1, 1.15)]), piv=(960, 540), at=(960, 540))
    return MO.world(E, fx.cam(E, T, E.clip(A, T.F), pose, n=2), P, keep='bright')


def plate_b(E, T):
    pose = lambda f: dict(zoom=keys(f, [(F1, 1.12, 'eo3'), (F1 + 12, 1.02), (TL.END, 1.08)]), piv=(900, 480), at=(960, 540))
    return MO.world(E, fx.cam(E, T, E.clip(B, T.F), pose, n=2), P, keep='bright')


def type_layer(E, T):
    cv = E.canvas(); f = T.f
    if f < F1:
        out = clamp01((f - F1 + 12) / 10.0)              # portal: the word flies past the camera
        sz = TY.size_for_cap('hero', 200)

        def each(i, n):
            t = f - LET[i]
            if t < 0:
                return None
            return (lerp(2.4, 1.0, eo5(t / 4.0)) * (1.0 + 2.0 * out ** 2), 0.0, 0.0, clamp01(t / 1.2) * (1.0 - out))
        TY.letters(cv, 'CHROMA', 960, 540 + 100, sz, 'hero', PAPER, 'center', each=each)
        TY.label(cv, ['TIME TUNNEL · 6 → 12 FOLDS'], TY.MARGIN, 126, 24, PAPER, 0.85 * clamp01((f - F0 - 6) / 4.0) * (1 - out))
    else:
        h = TL.F(4, 3)
        sz = TY.size_for_cap('hero', 230)
        if f >= h:
            TY.word(cv, 'HEAT', TY.MARGIN, 960, sz, 'hero', PAPER, 1.0, scale=1.0 + 0.3 * (1.0 - eo5((f - h) / 4.0)))
        TY.label(cv, ['OPEN FLAME', 'BAR 4'], TY.MARGIN, 126, 24, PAPER, 0.9 * eo3((f - F1) / 6.0))
    return cv.texture()


@shot(subframes=1)
def ex_vortex(E, T):
    if T.f < F1:
        nxt = plate_b(E, T) if V.phase(T.f) == 'portal' else None
        img = V.frame(E, T, plate_a(E, T), nxt)
    else:
        img = plate_b(E, T)
    img = E.over(img, type_layer(E, T))
    return SR.post(E, T, img, HITS)


ex_vortex.length = TL.END
