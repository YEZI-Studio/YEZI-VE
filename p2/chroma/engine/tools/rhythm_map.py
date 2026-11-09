# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""节奏地图：每小节画面「重击」落在哪里、有多密（卡点分主次用）。

    .venv/bin/python tools/rhythm_map.py VIDEO [--start FRAME] [--ref OTHER_VIDEO]

重击 = 逐帧 160x90 平均差 d[n] 的局部峰，且 d >= max(2 x 周围 ±12 帧中位数, 0.03)。
每小节输出：拍上（第 1–4 拍正拍）有几下重击、反拍 16 分音符（12 个）上有几下、每秒重击数。
参考：CHROMA 定稿全片 5.3 下/秒，忙闲对比大（每 1.5 s 窗口 2–10.7）；IGNITION v2 7.9 下/秒且一直在 6–10.7（太密、没有呼吸）。
目标：全片 4.5–6 下/秒；普通小节反拍重击 ≤ 4/12；开头 10 格预告、第 3 小节屏息、第 11 小节 STOP 除外。
"""
import argparse, subprocess
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument('video')
ap.add_argument('--start', type=int, default=0)
ap.add_argument('--ref')
a = ap.parse_args()

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import timeline as TL      # 100 BPM, beat 36 f, offset 0
BEAT = TL.BEAT
OFF = TL.OFFSET
SIX = BEAT / 4.0


def diffs(path):
    W, H = 160, 90
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-vf', f'scale={W}:{H}:flags=area', '-f', 'rawvideo',
                          '-pix_fmt', 'rgb24', '-'], capture_output=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3).astype(np.float32) / 255.0
    d = np.zeros(len(fr))
    d[1:] = np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2, 3))
    return d


def peaks(d):
    n = len(d)
    out = []
    for i in range(1, n - 1):
        thr = max(2.0 * np.median(d[max(1, i - 12):i + 13]), 0.03)
        if d[i] >= thr and d[i] >= d[i - 1] and d[i] >= d[i + 1]:
            out.append(i)
    return out


def report(path, start):
    d = diffs(path)
    n = len(d)
    pk = set(p + start for p in peaks(d))
    f_end = start + n
    print(f'{path}  frames {start}-{f_end - 1}  hits/s overall {len(pk) / (n / 60):.1f}')
    print(' bar  frames       on-beat  off-beat16  hits/s')
    for b in range(1, 18):
        f0 = int(round(OFF + (b - 1) * 4 * BEAT))
        f1 = int(round(OFF + b * 4 * BEAT))
        if f1 <= start or f0 >= f_end:
            continue
        on = off = 0
        for k in range(16):
            g = int(round(OFF + (b - 1) * 4 * BEAT + k * SIX))
            hit = any(x in pk for x in (g - 1, g, g + 1))
            if k % 4 == 0:
                on += hit
            else:
                off += hit
        c = sum(1 for p in pk if max(f0, start) <= p < min(f1, f_end))
        dur = (min(f1, f_end) - max(f0, start)) / 60.0
        print(f' {b:>3}  {f0:4d}-{f1 - 1:4d}   {on}/4     {off:2d}/12      {c / dur:.1f}')


report(a.video, a.start)
if a.ref:
    d = diffs(a.ref)
    print(f'ref {a.ref}: hits/s overall {len(peaks(d)) / (len(d) / 60):.1f}')
