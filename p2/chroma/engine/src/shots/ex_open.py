# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""示例 1 · 开头 10 格（系列签名：第 1 秒 10 个构图，一格一个大词 + 一个主体 + 一层小标签）。
引擎自带的示例镜头（演示写法，只当结构参考）。bar 1 = 帧 0-95（150 BPM：16 分音符 = 6 帧）。
  0-5     海报格（首尾循环：全片最后一帧要接回这一格）
  6-59    九格，每格一个 16 分音符：实拍(单色世界) / 冲击帧风格卡 / 纸白设计帧 轮换；每格进场 6 帧内自己在动（落定推）
  60-95   片名格：字母逐个砸在 32 分音符上；93-95 是下一镜 DROP 冲击簇的蓄力（同一个 Hit 两镜都要 post）
渲染：.venv/bin/python -m src.render sheet --fn src.shots.ex_open:ex_open --range 0 1.58 --step 0.1 --out out/ex/open.jpg
"""
from src import timeline as TL
from src.engine import shot
from src.footage import Take
from src.fx import motif as MO, series as SR
from src.kit import fx, type as TY
from src.kit.anim import eo3, eo5, clamp01, lerp
from src.kit.core import INK, PAPER
from src.shots.ex_hit import HITS as NEXT_HITS          # the DROP cluster: its charge frames fall in this shot

P = MO.palette('#FF6A1F')                                # signature colour (example: fire orange)


def S(k):
    """film frame of the k-th 16th of bar 1 (0, 6, 12 ... at 150 BPM)"""
    return TL.F(1 + k // 16, 1 + (k % 16) // 4, k % 4)


# cell: (clip, source s, kind, zoom, focus point in the plate, word, face, label)
#   kind 'plate' = mono world | 'paper' = paper design frame with the plate in a circle | SR preset = impact-look card
CELLS = [
    ('K1', 2.6, 'plate', 1.15, (960, 420), 'CHROMA', 'hero', 'EP 01 · EXAMPLE'),      # 0 poster (= the loop target)
    ('K1', 0.5, 'plate', 1.9, (1010, 260), 'SPARK', 'hero', 'MATCH HEAD'),
    ('K1', 1.4, 'paper', 1.6, (960, 380), '1850 K', 'wide', 'CANDLE FLAME'),          # real number (a candle ~1850 K)
    ('K2', 1.5, 'post3_blue', 1.2, (960, 700), 'FLARE', 'hero', 'FIREWORK'),
    ('K3', 1.0, 'plate', 1.5, (960, 540), 'CITY', 'hero', 'SODIUM · LED'),
    ('K4', 0.8, 'duo_blue', 1.3, (900, 500), 'HEAT', 'hero', 'OPEN FLAME'),
    ('K3', 2.2, 'paper', 1.1, (960, 540), 'GRID', 'hero', 'ROUNDABOUT · NIGHT'),
    ('K2', 3.0, 'lines', 1.4, (960, 760), 'BURST', 'hero', 'SPARKS'),
    ('K4', 2.5, 'plate', 1.6, (1100, 420), 'BURN', 'hero', 'COMBUSTION'),
    ('K2', 2.2, 'half_blue', 1.2, (960, 600), 'GLOW', 'hero', 'AFTERGLOW'),
    ('K1', 3.2, 'plate', 1.05, (960, 420), 'CHROMA', 'hero', 'EP 01'),               # 10 title cell 60-95
]
TAKES = [Take(c, film=(S(k), S(k + 1) if k < 10 else TL.F(2)), src=s) for k, (c, s, *_) in enumerate(CELLS)]
LET0 = S(10) + 2                                          # title letters slam in, one per 32nd


def cell_at(F):
    for k in range(10, -1, -1):
        if F >= S(k):
            return k
    return 0


def plate(E, T, k):
    clip, s, kind, z, at, *_ = CELLS[k]
    f0 = S(k)
    end = TL.F(2) if k == 10 else S(k + 1)
    pose = lambda f: dict(zoom=z * (1.0 + 0.10 * (1.0 - eo3((f - f0) / 6.0)) + 0.06 * clamp01((f - f0) / (end - f0))),
                          roll=(2.0 if k % 2 else -2.0) * (1.0 - eo3((f - f0) / 6.0)), piv=at,
                          at=(1240, 470) if kind == 'paper' else (960, 540))      # paper: the subject sits in the circle
    img = MO.world(E, fx.cam(E, T, E.clip(TAKES[k], T.F), pose, n=3), P, keep='bright')
    if kind == 'paper':                                   # paper design frame: the subject inside a circle on PAPER
        cv = E.canvas()
        cv.circle(1240, 470, 300 * (0.6 + 0.4 * eo5((T.f - f0) / 4.0)), (1, 1, 1))
        img = E.mix(E.solid(PAPER), img, cv.texture(), ch='a')
    elif kind != 'plate':                                 # one of the impact looks as a style card
        img = SR.card(E, img, T.F, kind, c=(960, 540), j=(T.F - f0) // 3 % 2, **MO.hit_cols(P))
    return img


def type_layer(E, T, k):
    *_, kind, _, _, word, face, label = CELLS[k]
    f0, f = S(k), T.f
    ink = INK if kind == 'paper' else PAPER
    cv = E.canvas()
    TY.label(cv, [label], TY.MARGIN, 126, 24, ink, 0.9)
    if k == 10:                                           # title: letters slam in
        sz = TY.size_for_cap('hero', 250)
        each = lambda i, n: None if f < LET0 + 3 * i else (lerp(2.2, 1.0, eo5((f - LET0 - 3 * i) / 4.0)), 0, 0, 1.0)
        TY.letters(cv, word, 960, 540 + 125, sz, 'hero', PAPER, 'center', each=each)
    else:
        sz = TY.size_for_cap(face, 230 if face == 'hero' else 170)
        TY.word(cv, word, TY.MARGIN, 960, sz, face, ink, 1.0, scale=1.0 + 0.25 * (1.0 - eo5((f - f0) / 4.0)))
    return cv.texture()


@shot(subframes=1)
def ex_open(E, T):
    k = cell_at(T.F)
    img = E.over(plate(E, T, k), type_layer(E, T, k))
    return SR.post(E, T, img, NEXT_HITS)                  # the next shot's DROP charges on 93-95


ex_open.length = TL.F(2)
