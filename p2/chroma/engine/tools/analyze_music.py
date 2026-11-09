# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""Compare phonk candidates: tempo, beat grid, onset/accent density, energy curve, drop position, loudness.

usage: .venv/bin/python tools/analyze_music.py audio/candidates/*.mp3 --out audio/analysis
Writes <out>/<name>.json + <out>/<name>.png (energy / onset / beat plot) and prints a comparison table.
"""
import sys, os, json, argparse
import numpy as np
import librosa
import soundfile as sf


def analyze(path, sr=44100):
    y, sr = librosa.load(path, sr=sr, mono=True)
    dur = len(y) / sr
    hop = 512
    # tempo / beats (try both raw and half/double; report the one in 120-170 as main)
    oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop, aggregate=np.median)
    tempo, beats = librosa.beat.beat_track(onset_envelope=oenv, sr=sr, hop_length=hop, units='time', tightness=120)
    tempo = float(np.atleast_1d(tempo)[0])
    # low band (kick / 808) onset
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    low = S[freqs < 150].sum(0)
    lowdb = librosa.amplitude_to_db(low + 1e-9)
    lowon = np.maximum(0, np.diff(lowdb, prepend=lowdb[0]))
    t = librosa.frames_to_time(np.arange(len(oenv)), sr=sr, hop_length=hop)
    # rms energy per 0.5 s
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    win = int(0.5 * sr / hop)
    nseg = len(rms) // win
    e = np.array([rms[i * win:(i + 1) * win].mean() for i in range(nseg)])
    edb = 20 * np.log10(e + 1e-9)
    # drop = biggest rise of 2 s mean energy (after first 2 s)
    k = 4
    sm = np.convolve(edb, np.ones(k) / k, mode='same')
    rise = sm[k:] - sm[:-k]
    di = int(np.argmax(rise[2:])) + 2 + k // 2 if len(rise) > 4 else 0
    drop_t = di * 0.5
    # accents: strong onsets (top 15 %) per second
    on = librosa.onset.onset_detect(onset_envelope=oenv, sr=sr, hop_length=hop, units='time', backtrack=False)
    on_str = np.interp(on, t, oenv)
    thr = np.percentile(on_str, 85) if len(on_str) else 0
    strong = on[on_str >= thr]
    # loudness proxy (integrated, not true LUFS) + crest
    peak = float(np.abs(y).max())
    rmsall = float(np.sqrt(np.mean(y ** 2)))
    # low-end weight (share of energy < 150 Hz)
    lowshare = float((S[freqs < 150] ** 2).sum() / (S ** 2).sum())
    # pulse clarity: autocorr peak of onset env at beat lag
    lag = int(round(60.0 / tempo * sr / hop)) if tempo > 0 else 0
    ac = librosa.autocorrelate(oenv - oenv.mean())
    clarity = float(ac[lag] / ac[0]) if lag and lag < len(ac) else 0.0
    return dict(file=os.path.basename(path), dur=round(dur, 2), tempo=round(tempo, 2), n_beats=len(beats),
                beats=[round(float(b), 3) for b in beats], drop_t=drop_t, onsets_per_s=round(len(on) / dur, 2),
                strong_per_s=round(len(strong) / dur, 2), strong=[round(float(s), 3) for s in strong],
                pulse_clarity=round(clarity, 3), low_share=round(lowshare, 3), crest_db=round(20 * np.log10(peak / (rmsall + 1e-9)), 1),
                energy_db_0p5s=[round(float(x), 1) for x in edb]), (t, oenv, lowon, edb)


def plot(name, res, curves, out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    t, oenv, lowon, edb = curves
    fig, ax = plt.subplots(3, 1, figsize=(16, 7), sharex=True)
    ax[0].plot(np.arange(len(edb)) * 0.5, edb, color='k'); ax[0].set_ylabel('RMS dB/0.5s')
    ax[0].axvline(res['drop_t'], color='r')
    ax[1].plot(t, oenv, lw=0.6); ax[1].set_ylabel('onset')
    for s in res['strong']:
        ax[1].axvline(s, color='orange', lw=0.4, alpha=0.6)
    ax[2].plot(t, lowon, lw=0.6, color='purple'); ax[2].set_ylabel('low onset')
    for b in res['beats']:
        ax[2].axvline(b, color='grey', lw=0.3, alpha=0.5)
    ax[2].set_xlabel('s')
    fig.suptitle(f"{name}  tempo {res['tempo']}  drop~{res['drop_t']}s  strong/s {res['strong_per_s']}  clarity {res['pulse_clarity']}")
    fig.tight_layout(); fig.savefig(out, dpi=80); plt.close(fig)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='+')
    ap.add_argument('--out', default='audio/analysis')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    rows = []
    for f in a.files:
        name = os.path.splitext(os.path.basename(f))[0]
        res, curves = analyze(f)
        json.dump(res, open(os.path.join(a.out, name + '.json'), 'w'), indent=1)
        plot(name, res, curves, os.path.join(a.out, name + '.png'))
        rows.append(res)
    print(f"{'file':48s} {'dur':>6s} {'bpm':>7s} {'drop':>5s} {'on/s':>5s} {'str/s':>5s} {'clar':>5s} {'low':>5s} {'crest':>5s}")
    for r in rows:
        print(f"{r['file'][:48]:48s} {r['dur']:6.1f} {r['tempo']:7.2f} {r['drop_t']:5.1f} {r['onsets_per_s']:5.2f} {r['strong_per_s']:5.2f} {r['pulse_clarity']:5.2f} {r['low_share']:5.2f} {r['crest_db']:5.1f}")
