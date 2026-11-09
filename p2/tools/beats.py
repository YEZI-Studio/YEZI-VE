# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""配乐节拍分析 → beats.json + 每首一个 events 文件。

用法（librosa 用引擎虚拟环境 p2/chroma/.venv；-B 不写字节码，numba 缓存放 p2 外的临时目录）：
  NUMBA_CACHE_DIR=<临时目录> <venv>/bin/python -I -B beats.py <素材包目录> [--hint 文件名=BPM ...]
读 <素材包目录>/music/*.mp3|wav，写 <素材包目录>/beats.json、<素材包目录>/music/<名>.events.json。

做法：
1. 拍子跟低频（底鼓 / 808）：在提示 BPM ±1.5 里扫速度和相位，低频冲量落在格点上最多的为准，再最小二乘精修；全曲当恒定速度。
2. 小节线：4 个相位里，选「整拍能量跳升」最集中的那个（段落、DROP 都从小节第 1 拍开始）。
3. DROP：低频（<120 Hz）一小节比前两小节均值高 ≥10 dB，且之后两小节也满。
4. 推荐窗口（CHROMA 21–27 s）：DROP 前 4 小节起，向后取整小节到 21–27 s，挑 DROP 后最弱一小节最强的那个长度。
5. events：窗口内每个 16 分音符的低/中/高频冲量，前 25 % 记 accent；frame = (格点时间 − 窗口起点) × 60。
6. 打击点表 music/<名>.music_map.json（推荐窗口、全曲网格）：每个 16 分格的真实起音帧 F、名次、档位、DROP、急停、
   最后一个重音、DROP 前最安静的一拍。算法和门禁同一套（syncmeas.build_map，不另抄一份）；--no-map 跳过。
"""
import argparse, json, os, sys
import numpy as np, librosa, scipy.signal as ss
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))     # -I 不把脚本目录放进 sys.path
import syncmeas  # noqa: E402  打击点表（和 gates 的卡点门禁同一套量法）

FPS = 60
ap = argparse.ArgumentParser()
ap.add_argument('pack')
ap.add_argument('--hint', nargs='*', default=[], help='文件名片段=BPM')
ap.add_argument('--tmin', type=float, default=21.0)
ap.add_argument('--tmax', type=float, default=27.0)
ap.add_argument('--no-map', action='store_true', help='不生成 <名>.music_map.json')
a = ap.parse_args()
hints = dict(h.split('=') for h in a.hint)
mdir = os.path.join(a.pack, 'music')
meta_path = os.path.join(mdir, 'tracks.json')          # 曲目元数据（来源、许可、署名），可选
meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}


def low_grid(y, sr, dur, bpm0):
    """拍子跟低频（底鼓 / 808）走：在 bpm0 ±1.5 里扫速度和相位，取低频冲量在格点上均值最大的；再用格点附近的低频峰最小二乘精修。
    （只用打击乐起音峰会锁到反拍的拍手 / 踩镲上：实测会差半拍。）"""
    h = 128
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=h)); fr = librosa.fft_frequencies(sr=sr, n_fft=2048)
    t3 = librosa.frames_to_time(np.arange(S.shape[1]), sr=sr, hop_length=h)
    e = np.log1p(S[(fr >= 25) & (fr < 140)].sum(0)); lf = np.maximum(0, np.diff(e, prepend=e[0]))
    lf = np.maximum(lf, np.r_[lf[1:], 0])                    # 容 ±1 格（2.9 ms）
    best = (-1, bpm0, 0.0)
    for bpm in np.arange(bpm0 - 1.5, bpm0 + 1.5, 0.005):
        P = 60 / bpm
        phis = np.arange(0, P, 0.004)
        g = phis[:, None] + P * np.arange(int((dur - 0.05) / P))[None, :]
        idx = np.clip(np.searchsorted(t3, g), 0, len(lf) - 1)
        sc = lf[idx].mean(1); j = int(np.argmax(sc))
        if sc[j] > best[0]: best = (float(sc[j]), bpm, float(phis[j]))
    sc, bpm, ph = best; P = 60 / bpm
    pk, _ = ss.find_peaks(lf, height=np.percentile(lf, 98), distance=int(0.1 * sr / h)); pt = t3[pk]
    k = np.round((pt - ph) / P); d = pt - (ph + k * P); sel = np.abs(d) < 0.03
    if sel.sum() > 20:
        A = np.vstack([k[sel], np.ones(sel.sum())]).T
        (P, ph), *_ = np.linalg.lstsq(A, pt[sel], rcond=None)
        res = pt[sel] - (ph + k[sel] * P)
    else:
        res = d[sel]
    ph = ph % P
    return P, ph, float(np.mean(np.abs(d) < 0.03)), int(sel.sum()), float(1000 * np.sqrt(np.mean(res ** 2))) if len(res) else float('nan')


def analyze(path, bpm0):
    y, sr = librosa.load(path, sr=44100, mono=True)
    dur = len(y) / sr
    P, ph, score, n_used, rms_ms = low_grid(y, sr, dur, bpm0)
    beats = np.arange(ph, dur, P)
    # 频段包络（hop 512）
    h2 = 512
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=h2)); fr = librosa.fft_frequencies(sr=sr, n_fft=2048)
    tt = librosa.frames_to_time(np.arange(S.shape[1]), sr=sr, hop_length=h2)
    def band_db(lo, hi):
        return 10 * np.log10((S[(fr >= lo) & (fr < hi)] ** 2).sum(0) + 1e-10)
    tot_db, low_db, high_db = band_db(20, 16000), band_db(20, 120), band_db(2500, 16000)
    def seg_mean(x, t0, t1):
        m = (tt >= t0) & (tt < t1)
        return float(np.mean(x[m])) if m.any() else float('nan')
    # 每拍能量 → 选小节相位
    be = np.array([seg_mean(tot_db, b, b + P) for b in beats])
    rise = np.maximum(0, np.diff(be, prepend=be[0]))
    lowb = np.array([seg_mean(low_db, b, b + 0.12) for b in beats])
    sc4 = [rise[p::4].sum() + 0.25 * lowb[p::4].mean() for p in range(4)]
    p0 = int(np.argmax(sc4))
    down = beats[p0::4]
    bar_s = 4 * P
    bars = []
    for i, d0 in enumerate(down):
        if d0 + bar_s > dur + 1e-6: break
        bars.append(dict(i=i + 1, t=round(float(d0), 4), rms_db=round(seg_mean(tot_db, d0, d0 + bar_s), 2),
                         low_db=round(seg_mean(low_db, d0, d0 + bar_s), 2), high_db=round(seg_mean(high_db, d0, d0 + bar_s), 2),
                         low_beats_db=[round(seg_mean(low_db, d0 + j * P, d0 + (j + 1) * P), 1) for j in range(4)]))
    L = np.array([b['low_db'] for b in bars])
    full = np.percentile(L, 75)
    # 低频一小节跳升 ≥8 dB 且之后两小节都满：前两小节都弱 = drop（真正的砸）；只弱一小节 = stop（急停后再砸）
    hits = []
    for i in range(1, len(bars) - 2):
        if L[i] - L[i - 1] >= 8 and min(L[i], L[i + 1]) >= full - 6:
            kind = 'drop' if i < 2 or L[i - 2] < full - 6 else 'stop'
            hits.append((i, kind))
    drops = [i for i, k in hits if k == 'drop']
    mains = drops or [i for i, k in hits if k == 'stop']        # 没有 drop 的曲子退而用 stop 当主击
    # 推荐窗口：每个 drop，前面蓄力 4 小节（不够就 2），总长 21–27 s 的整小节；
    # 先挑 drop 后弱小节最少的，再挑 drop 后小节数是 4 的倍数（乐句结束），再挑离 25 s 最近
    wins = []
    for di in mains:
        for build in (4, 2):
            s = di - build
            if s < 0: continue
            for n in range(build + 1, len(bars) - s + 1):
                length = n * bar_s
                if not (a.tmin <= length <= a.tmax): continue
                weak = int((L[di:s + n] < full - 8).sum())
                wins.append((weak, 0 if (n - build) % 4 == 0 else 1, abs(length - 25), s, n, di, build))
            break
    wins.sort()
    win = None
    if wins:
        *_, s, n, di, build = wins[0]
        w0 = bars[s]['t']; w1 = w0 + n * bar_s
        rel = lambda x: round((x - w0) * FPS, 2)
        win = dict(main_hit_kind='drop' if di in drops else 'stop', start_s=round(w0, 4), end_s=round(w1, 4), dur_s=round(n * bar_s, 4), n_bars=n, build_bars=build,
                   drop_s=round(bars[di]['t'], 4), drop_in_window_s=round(bars[di]['t'] - w0, 4), drop_frame=rel(bars[di]['t']),
                   last_bar_frame=rel(bars[s + n - 1]['t']), end_frame=rel(w1),
                   bar_frames=[rel(bars[j]['t']) for j in range(s, s + n)],
                   other_hits_in_window=[dict(kind=k, song_s=bars[j]['t'], frame=rel(bars[j]['t'])) for j, k in hits if s <= j < s + n and j != di],
                   weakest_bar_after_drop_low_db=round(float(L[di:s + n].min()), 2),
                   full_low_db=round(float(full), 2))
    # events（窗口内，每个 16 分音符）
    events = []
    if win:
        six = P / 4; w0 = win['start_s']; w1 = win['end_s']
        h3 = 128
        St = np.abs(librosa.stft(y, n_fft=2048, hop_length=h3)); f3 = librosa.fft_frequencies(sr=sr, n_fft=2048)
        t3 = librosa.frames_to_time(np.arange(St.shape[1]), sr=sr, hop_length=h3)
        def flux(lo, hi):
            e = np.log1p(St[(f3 >= lo) & (f3 < hi)].sum(0)); d = np.maximum(0, np.diff(e, prepend=e[0]))
            m = (t3 >= w0) & (t3 < w1)
            return d / (np.percentile(d[m], 99.5) + 1e-9)
        merged = {}
        for name, f in dict(low=flux(25, 140), mid=flux(150, 2500), high=flux(2500, 14000)).items():
            pks, pr = ss.find_peaks(f, height=0.35, distance=int(0.09 * sr / h3))
            for p, hgt in zip(pks, pr['peak_heights']):
                x = t3[p]
                if not (w0 - six / 2 <= x < w1 - six / 2): continue
                k16 = int(round((x - w0) / six)); g = w0 + k16 * six
                m = merged.setdefault(k16, dict(six=k16, grid_t=round(g - w0, 4), song_t=round(g, 4), frame=round((g - w0) * FPS, 2),
                                                bar=k16 // 16 + 1, beat=(k16 % 16) // 4 + 1, sub=k16 % 4 + 1, low=0.0, mid=0.0, high=0.0))
                m[name] = round(max(m[name], float(min(hgt, 1.5))), 3)
        events = sorted(merged.values(), key=lambda m: m['six'])
        for m in events: m['total'] = round(m['low'] * 1.3 + m['mid'] + 0.6 * m['high'], 3)
        if events:
            thr = np.percentile([m['total'] for m in events], 75)
            for m in events: m['accent'] = bool(m['total'] >= thr)
    return dict(dur_s=round(dur, 3), bpm=round(60 / P, 3), beat_s=round(P, 6), bar_s=round(bar_s, 6),
                beat_frames_60=round(P * FPS, 3), grid_fit=dict(low_peak_hit_rate=round(score, 3), n_peaks=n_used, resid_rms_ms=round(rms_ms, 1)),
                first_downbeat_s=round(float(down[0]), 4), downbeat_phase=p0,
                beats_s=[round(float(b), 3) for b in beats], bars=bars, drops_s=[bars[i]['t'] for i in drops],
                hits=[dict(kind=k, bar=bars[i]['i'], song_s=bars[i]['t']) for i, k in hits],
                window=win), events


out = dict(fps=FPS, note='全曲时间 = 原曲秒；window.*_frame = 从窗口起点算的 60 fps 帧（小数，落帧时 round）；'
                         '全部是程序分析，没有人耳听过。', tracks=[])
for fn in sorted(os.listdir(mdir)):
    if not fn.lower().endswith(('.mp3', '.wav')): continue
    bpm0 = next((float(v) for k, v in hints.items() if k in fn), None)
    if bpm0 is None:
        y, sr = librosa.load(os.path.join(mdir, fn), sr=22050, mono=True, duration=90)
        bpm0 = float(np.atleast_1d(librosa.beat.beat_track(y=y, sr=sr)[0])[0])
    res, events = analyze(os.path.join(mdir, fn), bpm0)
    stem = os.path.splitext(fn)[0]
    if events:
        json.dump(dict(bpm=res['bpm'], phase=0.0, beat_s=res['beat_s'], fps=FPS, window_start_song_s=res['window']['start_s'],
                       note='six = 从窗口起点算的 16 分音符序号；bar/beat/sub 从 1 起；frame = 格点时间（窗口内）× 60',
                       events=events), open(os.path.join(mdir, stem + '.events.json'), 'w'), indent=0, ensure_ascii=False)
    map_file = None
    if res['window'] and not a.no_map:
        w = res['window']
        mm = syncmeas.build_map(os.path.join(mdir, fn), res['bpm'], res['first_downbeat_s'], w['start_s'], w['n_bars'], FPS)
        syncmeas._save_json(os.path.join(mdir, stem + '.music_map.json'), mm)
        map_file = 'music/' + stem + '.music_map.json'
        print(f"  music_map：档位 {mm['tiers']}，drop {mm['drop'] and mm['drop']['F']}，急停 {[x['F'] for x in mm['stops']]}，"
              f"最后一个重音 {mm['last_accent'] and mm['last_accent']['F']}，起音−格点 {mm['grid_check'].get('attack_ms_med')} ms")
    tr = dict(file='music/' + fn, **meta.get(fn, {}), **res, events_file=('music/' + stem + '.events.json') if events else None,
              music_map_file=map_file)
    out['tracks'].append(tr)
    w = res['window']
    print('  bar low_db: ' + ' '.join(f"{b['i']}:{b['low_db']:.0f}" for b in res['bars']))
    print(f"{fn[:46]:46s} {res['bpm']:8.3f} BPM  fit {res['grid_fit']['low_peak_hit_rate']:.2f}/{res['grid_fit']['resid_rms_ms']:.1f}ms  "
          f"bars {len(res['bars'])}  drops {res['drops_s']} stops {[h['song_s'] for h in res['hits'] if h['kind']=='stop']}  "
          + (f"win {w['start_s']:.2f}-{w['end_s']:.2f} ({w['n_bars']} 小节 {w['dur_s']:.2f}s) drop@{w['drop_in_window_s']:.2f}s f{w['drop_frame']}" if w else 'win -'))
json.dump(out, open(os.path.join(a.pack, 'beats.json'), 'w'), indent=1, ensure_ascii=False)
print('wrote', os.path.join(a.pack, 'beats.json'))
