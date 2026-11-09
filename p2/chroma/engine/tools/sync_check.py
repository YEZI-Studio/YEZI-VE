# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""卡点检查：每个音乐重音那一帧，画面有没有「正好在这一帧」的明显变化。

    .venv/bin/python tools/sync_check.py VIDEO [--start FRAME] [--events audio/events.json] [--all]

VIDEO 的第 0 帧 = 影片帧 --start（分段成片用：m2 从 377 开始）。逐帧算 160x90 平均差 d[n] = mean|f_n - f_{n-1}|，
对每个要检查的 16 分音符（默认：accent，加上每小节第 1、3 拍的低频 808；--all = 所有有能量的 16 分音符）：
  基线 = g±12 帧的 d 中位数；「强」= d >= max(2.0 x 基线, 0.03)。按「起变化」判（人对卡点的感觉看的是第一下）：
  ON    : g 或 g-1 那帧就是强变化（提前一帧看起来也是准的）
  LATE  : g、g-1 都不强，第一下强变化出现在 g+1..g+3（画面比声音晚，最伤卡点感）
  EARLY : g、g-1 都不强，但 g-3..g-2 有强变化（抢拍）
  WEAK  : ±3 帧里都没有强变化
输出每类数量和逐条清单；返回码 0。
"""
import sys, json, argparse, subprocess
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument('video')
ap.add_argument('--start', type=int, default=0)
ap.add_argument('--events', default='audio/events.json')
ap.add_argument('--all', action='store_true')
a = ap.parse_args()

W, H = 160, 90
raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', a.video, '-vf', f'scale={W}:{H}:flags=area', '-f', 'rawvideo',
                      '-pix_fmt', 'rgb24', '-'], capture_output=True).stdout
fr = np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3).astype(np.float32) / 255.0
n = len(fr)
d = np.zeros(n)
d[1:] = np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2, 3))
f0, f1 = a.start, a.start + n

ev = json.load(open(a.events))['events']
rows = []
for e in ev:
    is_beat13 = e['sub'] == 1 and e['beat'] in (1, 3) and e['low'] >= 0.5
    if not (a.all and e['total'] > 0.35) and not e.get('accent') and not is_beat13:
        continue
    g = int(round(e['frame']))
    if not (f0 + 3 <= g < f1 - 3):
        continue
    i = g - f0
    lo, hi = max(1, i - 12), min(n, i + 13)
    base = float(np.median(d[lo:hi])) + 1e-4
    thr = max(2.0 * base, 0.03)
    st = {k: d[i + k] >= thr for k in range(-3, 4)}
    if st[0] or st[-1]:
        cls, pk = 'ON', (0 if st[0] else -1)
    elif any(st[k] for k in (1, 2, 3)):
        cls, pk = 'LATE', min(k for k in (1, 2, 3) if st[k])
    elif st[-2] or st[-3]:
        cls, pk = 'EARLY', (-2 if st[-2] else -3)
    else:
        cls, pk = 'WEAK', int(np.argmax(d[i - 3:i + 4])) - 3
    peak = float(d[i + pk])
    tag = ('L' if e['low'] >= 0.5 else '') + ('M' if e['mid'] >= 0.5 else '') + ('H' if e['high'] >= 0.5 else '')
    rows.append((g, f"{e['bar']}.{e['beat']}.{e['sub']}", tag or '-', bool(e.get('accent')), cls, pk, peak, base))

cnt = {k: sum(1 for r in rows if r[4] == k) for k in ('ON', 'LATE', 'EARLY', 'WEAK')}
print(f"{a.video}  film frames {f0}-{f1 - 1}  checked {len(rows)}  ON {cnt['ON']}  LATE {cnt['LATE']}  EARLY {cnt['EARLY']}  WEAK {cnt['WEAK']}")
for g, pos, tag, acc, cls, pk, peak, base in rows:
    if cls != 'ON':
        print(f"  F{g:5d} {pos:7s} {tag:3s} {'ACC' if acc else '   '}  {cls:5s} peak@{pk:+d}  d={peak:.3f}  base={base:.3f}")
