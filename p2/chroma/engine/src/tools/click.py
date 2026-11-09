# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""Temporary click track (no music yet): kick on beats, hat on 16ths, accent on the bar. -> 48 kHz stereo WAV.
  .venv/bin/python -m src.tools.click --bpm 128 --sec 3 --out audio/tmp/click_128.wav"""
import os, argparse, wave
import numpy as np


def click(bpm, sec, sr=48000, offset=0.0):
    N = int(round(sec * sr)); y = np.zeros(N); rng = np.random.default_rng(3)

    def put(t, sig):
        i = int(round(t * sr))
        if 0 <= i < N:
            m = min(len(sig), N - i); y[i:i + m] += sig[:m]
    tt = np.arange(int(0.25 * sr)) / sr
    kick = np.sin(2 * np.pi * (45 * tt + 90 * (1 - np.exp(-tt * 30)) / 30)) * np.exp(-tt * 14) * 0.9
    hn = np.diff(rng.standard_normal(int(0.05 * sr)), prepend=0); hat = hn * np.exp(-np.arange(len(hn)) / sr * 90) * 0.16
    cn = np.diff(rng.standard_normal(int(1.0 * sr)), prepend=0); crash = cn * np.exp(-np.arange(len(cn)) / sr * 3.5) * 0.10
    six = 60.0 / bpm / 4
    k = 0
    while offset + k * six < sec:
        t = offset + k * six
        put(t, hat * (1.3 if k % 4 == 0 else 0.8))
        if k % 4 == 0:
            put(t, kick)
        if k % 16 == 0:
            put(t, crash)
        k += 1
    y = np.clip(y / max(1.0, np.abs(y).max() / 0.85), -1, 1)
    return (np.stack([y, y], 1) * 32767).astype(np.int16), sr


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--bpm', type=float, required=True); ap.add_argument('--sec', type=float, required=True)
    ap.add_argument('--offset', type=float, default=0.0); ap.add_argument('--out', required=True); a = ap.parse_args()
    st, sr = click(a.bpm, a.sec, offset=a.offset)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with wave.open(a.out, 'wb') as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr); w.writeframes(st.tobytes())
    print(a.out)
