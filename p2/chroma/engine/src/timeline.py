# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""CHROMA engine timeline (DIRECTOR-OWNED: the director sets BPM / OFFSET / END / SHOTS once per episode; shot code reads only).
1920x1080, 60 fps. Every cut / impact / slam lands on an integer frame of the music grid.

    f(bar, beat=1, sub=0) -> float film frame (motion, easing)        sub = 16th inside the beat (0..3, may be fractional)
    F(bar, beat=1, sub=0) -> int film frame = round(f): where a cut / impact / slam lands
    pos(F) -> (bar, beat, 16th) 1-based                                sec(F) -> seconds
Grid source (first that exists):
    audio/grid.txt  "<phase_s> <sixteenth_s>"  (tools/beatgrid_fit.py prints phase + the BEAT period: sixteenth = period / 4)
    else BPM / OFFSET_S below.  Prefer a BPM whose beat is a whole number of frames at 60 fps (120 -> 30 f, 150 -> 24 f).
This copy ships the EXAMPLE film: 150 BPM (beat 24 f, 16th 6 f, bar 96 f), 4 bars = 384 f = 6.4 s, three example shots.
"""
import math
import os

FPS = 60
W, H = 1920, 1080
BPM = 150.0                         # placeholder; the director replaces it (or writes audio/grid.txt)
OFFSET_S = 0.0                      # seconds from film frame 0 to bar 1 beat 1

_GRID = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'audio', 'grid.txt')
if os.path.exists(_GRID):
    _PH, _SIX_S = [float(v) for v in open(_GRID).read().split()[:2]]
    BPM, OFFSET_S = 60.0 / (4 * _SIX_S), _PH

OFFSET = OFFSET_S * FPS             # frames
BEAT = 60.0 * FPS / BPM             # 24 f at 150 BPM
SIXTEENTH = BEAT / 4.0
BAR = 4.0 * BEAT


def f(bar, beat=1, sub=0.0):
    return OFFSET + (bar - 1) * BAR + (beat - 1) * BEAT + sub * SIXTEENTH


def F(bar, beat=1, sub=0.0):
    return int(math.floor(f(bar, beat, sub) + 0.5))


def pos(frame):
    x = (frame - OFFSET) / BEAT
    b = int(math.floor(x + 1e-6))
    return b // 4 + 1, b % 4 + 1, int(math.floor((x - b) * 4 + 1e-6)) + 1


def sec(frame):
    return frame / FPS


START = 0
END = F(5)                          # 4 bars = 384 f
DROP = F(2)                         # the example's big hit (bar 2 beat 1)

# name, module (src/shots/<module>.py), start, end (exclusive), what
SHOTS = [
    ('ex_open', 'ex_open', 0, F(2), 'bar 1: poster frame 0 -> ten cells on the 16ths (series signature cold open)'),
    ('ex_hit', 'ex_hit', F(2), F(3), 'bar 2: DROP = impact cluster + hero word slam on a mono-world plate'),
    ('ex_vortex', 'ex_vortex', F(3), END, 'bars 3-4: kaleidoscope vortex A -> portal -> B, letters slam during the spin'),
]


def check():
    assert SHOTS[0][2] == START == 0 and SHOTS[-1][3] == END
    for a, b in zip(SHOTS, SHOTS[1:]):
        assert a[3] == b[2], (a[0], b[0])
    return True


if __name__ == '__main__':
    check()
    print(f'BPM {BPM:.3f}  beat {BEAT:.4f} f  16th {SIXTEENTH:.4f} f  bar {BAR:.4f} f  film {START}-{END - 1} = {(END - START) / FPS:.2f} s')
    for name, mod, s, e, what in SHOTS:
        print(f'{name:12s} {mod:10s} {s:5d}-{e:5d}  {sec(s):6.2f}-{sec(e):6.2f}s  {what}')
