# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""Fit a precise constant-tempo grid to a track section: refine period/phase by least squares on percussive onset peaks.
usage: python tools/beatgrid_fit.py file --t0 12 --t1 36 --bpm 152"""
import argparse, numpy as np, librosa, scipy.signal as ss
ap = argparse.ArgumentParser(); ap.add_argument('f'); ap.add_argument('--t0', type=float); ap.add_argument('--t1', type=float); ap.add_argument('--bpm', type=float)
a = ap.parse_args()
y, sr = librosa.load(a.f, sr=44100, mono=True)
yh, yp = librosa.effects.hpss(y)
hop = 64
oenv = librosa.onset.onset_strength(y=yp, sr=sr, hop_length=hop)
t = librosa.frames_to_time(np.arange(len(oenv)), sr=sr, hop_length=hop)
m = (t >= a.t0) & (t <= a.t1)
pk, _ = ss.find_peaks(oenv * m, height=np.percentile(oenv[m], 97), distance=int(0.12 * sr / hop))
pt = t[pk]
# grid search period / phase, then LSQ on assigned peaks
best = None
for bpm in np.arange(a.bpm - 2, a.bpm + 2, 0.01):
    P = 60 / bpm
    ph = np.angle(np.exp(2j * np.pi * pt / P).mean()) / (2 * np.pi) * P
    d = (pt - ph + P / 2) % P - P / 2
    score = np.mean(np.abs(d) < 0.02)
    if best is None or score > best[0]:
        best = (score, bpm, ph)
score, bpm, ph = best
P = 60 / bpm
k = np.round((pt - ph) / P); d = pt - (ph + k * P)
sel = np.abs(d) < 0.025
A = np.vstack([k[sel], np.ones(sel.sum())]).T
(P2, ph2), *_ = np.linalg.lstsq(A, pt[sel], rcond=None)
res = pt[sel] - (ph2 + k[sel] * P2)
print(f'coarse bpm {bpm:.2f} score {score:.2f} | LSQ period {P2:.6f}s = {60/P2:.3f} BPM, phase {ph2 % P2:.4f}s, n {sel.sum()}, resid rms {1000*np.sqrt(np.mean(res**2)):.1f} ms, max {1000*np.abs(res).max():.1f} ms')
