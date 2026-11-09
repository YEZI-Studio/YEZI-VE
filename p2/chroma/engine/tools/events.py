# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""音乐重音表：读 audio/grid.txt（"<phase_s> <sixteenth_s>"，tools/beatgrid_fit.py 算出），
把混音里每个 16 分音符的低/中/高频起音强度、是否重音写进 audio/events.json（tools/sync_check.py 和 SR.AutoCam 读它），
并按小节打印：L/M/H = 低/中/高频强，大写 = 重音。片子第 0 帧 = 混音第 0 秒。
用法：../.venv/bin/python tools/events.py audio/mix.wav"""
import sys, numpy as np, librosa, json, scipy.signal as ss
SRC = sys.argv[1] if len(sys.argv) > 1 else 'audio/mix.wav'; FPS = 60
PH, SIX = [float(v) for v in open('audio/grid.txt').read().split()[:2]]; BEAT = 4 * SIX   # grid.txt = "<phase_s> <sixteenth_s>"
y, sr = librosa.load(SRC, sr=48000, mono=True)
hop = 128
St = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop)); fr = librosa.fft_frequencies(sr=sr, n_fft=2048)
t = librosa.frames_to_time(np.arange(St.shape[1]), sr=sr, hop_length=hop)
def flux(lo, hi):
    e = np.log1p(St[(fr >= lo) & (fr < hi)].sum(0)); d = np.maximum(0, np.diff(e, prepend=e[0])); return d / (np.percentile(d, 99.5) + 1e-9)
bands = dict(low=flux(25, 140), mid=flux(150, 2500), high=flux(2500, 14000))
events = []
for name, f in bands.items():
    pk, pr = ss.find_peaks(f, height=0.35, distance=int(0.09 * sr / hop))
    for p, h in zip(pk, pr['peak_heights']):
        tt = t[p]; k16 = round((tt - PH) / SIX); g = PH + k16 * SIX   # g = FILE time of the grid 16th
        events.append(dict(t=round(float(tt), 4), band=name, strength=round(float(min(h, 1.5)), 3), six=int(k16),
                           grid_t=round(g, 4), off_ms=round(1000 * (tt - g), 1), frame=round(g * FPS, 3), film_t=round(g, 4),
                           bar=int(k16 // 16) + 1, beat=int((k16 % 16) // 4) + 1, sub=int(k16 % 4) + 1))
events.sort(key=lambda e: (e['six'], e['band']))
# merge per 16th: keep strongest per band, total strength
merged = {}
for e in events:
    m = merged.setdefault(e['six'], dict(six=e['six'], grid_t=e['grid_t'], film_t=e['film_t'], frame=e['frame'], bar=e['bar'], beat=e['beat'], sub=e['sub'], low=0, mid=0, high=0))
    m[e['band']] = max(m[e['band']], e['strength'])
rows = sorted(merged.values(), key=lambda m: m['six'])
for m in rows: m['total'] = round(m['low'] * 1.3 + m['mid'] + 0.6 * m['high'], 3)
tot = np.array([m['total'] for m in rows]); thr = np.percentile(tot, 75)
for m in rows: m['accent'] = bool(m['total'] >= thr)
json.dump(dict(bpm=round(60 / BEAT, 3), phase=PH, beat_s=BEAT, fps=FPS, start_frame=0, note='frame = 60 * film_t (float; cut on round(frame)); six = 16th index; bar/beat/sub 1-based film bars',
               events=rows), open('audio/events.json', 'w'), indent=0)
# per-bar strings: one char per 16th: L (low strong) M H . ; uppercase if accent
for b in range(1, int(rows[-1]['bar']) + 1 if rows else 1):
    s = ''
    for k in range((b - 1) * 16, b * 16):
        m = merged.get(k)
        if not m: s += '.'; continue
        c = 'L' if m['low'] >= 0.5 else ('M' if m['mid'] >= 0.5 else ('h' if m['high'] >= 0.5 else ','))
        s += c if m.get('accent') else c.lower()
        if (k % 4) == 3: pass
    print(f'bar {b:2d} V {PH + (b-1)*4*BEAT:6.3f} f{(b-1)*4*BEAT*FPS:7.1f}  ' + ' '.join(s[i:i+4] for i in range(0, 16, 4)))
print('accents', sum(m['accent'] for m in rows), 'of', len(rows))
