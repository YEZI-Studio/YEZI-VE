#!/usr/bin/env python3
# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""卡点实测（打击点表 music_map、整片卡点门禁、逐镜卡点）：跑在引擎 Python 里（numpy / scipy / librosa），编排器用 subprocess 调。

  syncmeas.py map MUSIC --bpm B --downbeat-s D --start-s S --bars N [--fps 60] [--total-f N] --out OUT.json
  syncmeas.py check VIDEO (--map MAP.json | --bpm B [--phase-s P]) [--music MUSIC --start-s S | --own-audio]
                    [--beatmap BM.json] [--shotlist SL.json] [--thr JSON] [--cache X.npz] --out R.json
  syncmeas.py shot RENDER --start-f START_F --table TABLE.json [--thr JSON] --out R.json

约定：fps = 60；第 0 帧 = 音频 start_s 秒 = 第 1 小节第 1 拍。帧号都是这一集片内帧号。
  grid_f   = 16 分音符格点帧（小数）= (phase_s + i × 16 分) × 60
  attack_f = 真实起音帧（小数，高频 1 ms 包络「后 3 ms 均值 − 前 3 ms 均值」最大的点）：比格点早约 11 ms
  F        = 画面该变的整数帧 = round(attack_f)（没有起音的格 = round(grid_f)）：画面和起音差 ≤ 8.3 ms（60 fps 能做到的最好）
  g_on     = 判 ON 的锚 = ceil(attack_f)（起音之后第一个开始显示的帧）；ON = 第一下强变化在 g_on−1 或 g_on
相位只能用全曲拟合（music.json 的 downbeat_s 就是 beats.py 全曲低频网格的结果）：只在 26 s 窗口里拟合会锁到反拍（实测 +185.9 ms）。
所以 map 用 --bpm / --downbeat-s 给的网格；不给 --downbeat-s 才在整首歌上重新拟合。
门禁（check）现场从音轨重算起音（cut_rN 没声音 → 配乐从 start_s 起按样点切；交付成片 → 自己的音轨），MAP 只用来给网格、
标签（第几小节第几拍、全窗第几强）和计划对照。阈值默认值见 THR，编排器从 gates.V2["sync"] 用 --thr 覆盖。
原型按定稿片校准过（定稿片全过、两版初剪和试跑稿全不过）。写文件一律先写唯一临时名再 os.replace（编排器会同时对不同视频调用）。
"""
import argparse
import json
import math
import os
import subprocess
import tempfile
import uuid

os.environ.setdefault("NUMBA_CACHE_DIR", os.path.join(tempfile.gettempdir(), "yezi-ve-numba"))   # librosa 的 numba 缓存不写进 venv
import numpy as np  # noqa: E402
import scipy.signal as ss  # noqa: E402

VERSION = 1
FPS = 60
SR = 48000
BIAS = 0.5
W, H = 160, 90
THR = dict(grid_ms=25.0, grid_drift_ms=8.0, drop_f=1.0, top_n=16, top_on=13, top_c=3.0, top_c_n=10, select=0.12,
           plan_c=3.0, plan_c_big=4.0, silent_max=3, onset_min=0.6, mux_lag_ms=1.0, mux_corr=0.95,
           shot_heavy=[3.0, 0.08], shot_light=[2.0, 0.04], shot_silent=[4.0, 0.08], drop_db=10.0, stop_db=10.0, map_dev_ms=8.0,
           flash_corr=0.9, cut_corr=0.7, cut_min=0.75)
BAND = {"low": "L", "mid": "M", "high": "h"}
BAND_SAY = {"L": "808", "M": "军鼓 / 拍手", "h": "镲", "-": "无起音"}
KIND = {"WEAK": "weak", "LATE": "late", "EARLY": "early", "ON": "weak"}   # 另有 flash_return（逐镜）、unselective（整片选择性不过时逐镜）


# ------------------------------------------------------------------ 读写
def _save_json(path, obj):
    tmp = f"{path}.{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1, default=float)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _load(p):
    if not p:
        return None
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return json.loads(p)


def _fl(x):
    return int(math.floor(x + BIAS))


# ------------------------------------------------------------------ 解码
def decode_audio(path, start=0.0, dur=None, sr=SR):
    """整段解码再按样点切片（不用 -ss：sync-verify 已证实三种切法 0 偏移，按样点切最稳）。-vn：mp3 的封面、mp4 的画面都不要。"""
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    y = np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, np.float32)
    y = y[int(round(start * sr)):]
    if dur is not None:
        y = y[:int(round(dur * sr))]
    return y.astype(np.float64)


def _src_sig(path):
    st = os.stat(path)
    return np.array([VERSION, st.st_size, int(st.st_mtime_ns)], np.int64)


def video_diff(path, cache=None, gray=False):
    """d[n] = mean|f_n − f_{n−1}|，160×90 RGB 0..1（与引擎 rhythm_map 同一口径）。cache 记着源文件大小和修改时间。
    gray=True 另返回 160×90 灰度帧（逐镜卡点查「闪完回到旧构图」用，不走缓存）：返回 (d, 灰度帧)。"""
    if cache and os.path.exists(cache) and not gray:
        try:
            z = np.load(cache, allow_pickle=False)
            if np.array_equal(z["src"], _src_sig(path)):
                return z["d"]
        except (OSError, ValueError, KeyError):
            pass
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-map", "0:v:0", "-vf", f"scale={W}:{H}:flags=area",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3).astype(np.float32) / 255.0
    d = np.zeros(len(fr))
    d[1:] = np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2, 3))
    if gray:
        return d, fr.mean(axis=3)
    if cache:
        tmp = f"{cache}.{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp.npz"
        try:
            np.savez_compressed(tmp, src=_src_sig(path), d=d)
            os.replace(tmp, cache)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    return d


# ------------------------------------------------------------------ 频段冲量（与 beats.py 同一算法）
def _stft_bands(y, sr, hop=128):
    import librosa
    S = np.abs(librosa.stft(y.astype(np.float32), n_fft=2048, hop_length=hop))
    fr = librosa.fft_frequencies(sr=sr, n_fft=2048)
    t = librosa.frames_to_time(np.arange(S.shape[1]), sr=sr, hop_length=hop)

    def flux(lo, hi):
        e = np.log1p(S[(fr >= lo) & (fr < hi)].sum(0))
        return np.maximum(0, np.diff(e, prepend=e[0]))
    return t, dict(low=flux(25, 140), mid=flux(150, 2500), high=flux(2500, 14000))


# ------------------------------------------------------------------ 网格（只在没给 downbeat_s 时用：整首歌上拟合）
def fit_grid(y, sr, bpm0, span=1.0):
    """低频冲量扫速度 + 相位（beats.py low_grid 的做法），再用格点附近的低频峰最小二乘精修。
    返回 bpm、P、phase_s（第 0 拍相对第 0 样点的秒，模一拍取到 [−P/2, P/2)）、残差、前后半漂移。"""
    t, B = _stft_bands(y, sr)
    lf = B["low"]
    lf = np.maximum(lf, np.r_[lf[1:], 0])
    dur = len(y) / sr
    best = (-1, bpm0, 0.0)
    for bpm in np.arange(bpm0 - span, bpm0 + span, 0.005):
        P = 60 / bpm
        phis = np.arange(0, P, 0.002)
        g = phis[:, None] + P * np.arange(int((dur - 0.05) / P))[None, :]
        idx = np.clip(np.searchsorted(t, g), 0, len(lf) - 1)
        sc = lf[idx].mean(1)
        j = int(np.argmax(sc))
        if sc[j] > best[0]:
            best = (float(sc[j]), bpm, float(phis[j]))
    _, bpm, ph = best
    P = 60 / bpm
    pk, _ = ss.find_peaks(lf, height=np.percentile(lf, 98), distance=int(0.1 * sr / 128))
    pt = t[pk]
    k = np.round((pt - ph) / P)
    dd = pt - (ph + k * P)
    sel = np.abs(dd) < 0.03
    drift = 0.0
    if sel.sum() > 12:
        A = np.vstack([k[sel], np.ones(sel.sum())]).T
        (P, ph), *_ = np.linalg.lstsq(A, pt[sel], rcond=None)
        res = pt[sel] - (ph + k[sel] * P)
        half = k[sel] < np.median(k[sel])
        if half.any() and (~half).any():
            drift = float(1000 * (res[~half].mean() - res[half].mean()))
    else:
        res = dd[sel]
    ph = (ph + P / 2) % P - P / 2
    return dict(bpm=float(60 / P), P=float(P), phase_s=float(ph), n=int(sel.sum()),
                resid_ms=float(1000 * np.sqrt(np.mean(res ** 2))) if len(res) else float("nan"), drift_ms=drift)


def attack_times(y, sr, cands, lo=-0.025, hi=0.015):
    """每个候选时刻附近找真实起音：高频（>2.5 kHz）1 ms 包络（dB），找「后 3 ms 均值 − 前 3 ms 均值」最大的点（不用单点斜率：
    808 的低频波纹会骗过单点斜率，p1b_attack.log 里 −29 ms 那一簇就是）。窗 [−25, +15] ms（32 分音符 = 52 ms，窗不能更宽）。
    返回 [(attack_s, jump_db)]。"""
    sos = ss.butter(4, 2500, "high", fs=sr, output="sos")
    yh = ss.sosfiltfilt(sos, y)
    w = int(sr * 0.001)
    e = 10 * np.log10(np.convolve(yh ** 2, np.ones(w) / w, mode="same") + 1e-12)
    c3 = int(0.003 * sr)
    cs = np.r_[0, np.cumsum(e)]

    def mean(a, b):
        a, b = max(0, a), min(len(e), b)
        return (cs[b] - cs[a]) / max(1, b - a)
    out = []
    for c in cands:
        i0, i1 = max(c3, int((c + lo) * sr)), min(len(e) - c3 - 1, int((c + hi) * sr))
        if i1 <= i0 + 2:
            out.append((c, 0.0))
            continue
        idx = np.arange(i0, i1)
        jump = (cs[idx + c3] - cs[idx]) / c3 - (cs[idx] - cs[idx - c3]) / c3
        k = int(idx[np.argmax(jump)])
        out.append((k / sr, float(mean(k, k + int(0.02 * sr)) - mean(k - int(0.02 * sr), k))))
    return out


def _rms_db(y, sr):
    w = int(0.01 * sr)
    return 10 * np.log10(np.convolve(y ** 2, np.ones(w) / w, mode="same") + 1e-12)


def _seg_mean(x, sr, t0, t1):
    a, b = int(max(0.0, t0) * sr), int(max(0.0, t1) * sr)
    b = min(len(x), b)
    return float(x[a:b].mean()) if b > a else float("nan")


def find_drop(rms, sr, P, ph, n16, bar_only=True, min_db=10.0):
    """DROP = 宽频 RMS 一小节跳升最大（≥ min_db）的那个 16 分格。bar_only：只在小节线上找（map：网格是对的）；
    门禁里不限（音画差半拍、差几个 16 分时 DROP 会落在小节中间，sync_grid 靠它抓）。返回 (k16, 跳升 dB) 或 None。"""
    six = P / 4
    best = None
    for k in range(16, n16):
        if bar_only and k % 16:
            continue
        t = ph + k * six
        pre, post = _seg_mean(rms, sr, t - 4 * P, t), _seg_mean(rms, sr, t, t + 4 * P)
        if math.isnan(pre) or math.isnan(post):
            continue
        j = post - pre
        if j >= min_db and (best is None or j > best[1]):
            best = (k, j)
    return best


def onsets(y, sr, bpm, n_frames, phase_s=0.0, fps=FPS, thr=None):
    """窗口 [0, n_frames) 里的打击点：每个 16 分音符一格（有起音才收），带三频段强度、名次、档位、真实起音帧、画面帧 F；
    另收两类整体事件：急停 stop（某 16 分格宽频比前 4 格均值掉 ≥10 dB）、DROP（宽频一小节跳升 ≥10 dB）。sub 从 0 起。"""
    thr = dict(THR, **(thr or {}))
    P = 60.0 / bpm
    six = P / 4
    ph = phase_s
    dur = n_frames / fps
    t, B = _stft_bands(y, sr)
    m = (t >= 0) & (t < dur)
    merged = {}
    for name, f in B.items():
        f = f / (np.percentile(f[m], 99.5) + 1e-9)
        pks, pr = ss.find_peaks(f, height=0.35, distance=int(0.09 * sr / 128))
        for p, hgt in zip(pks, pr["peak_heights"]):
            k16 = int(round((t[p] - ph) / six))
            gt = ph + k16 * six
            if k16 < 0 or gt * fps >= n_frames:
                continue
            e = merged.setdefault(k16, dict(six=k16, grid_s=gt, low=0.0, mid=0.0, high=0.0))
            e[name] = max(e[name], float(min(hgt, 1.5)))
    ev = sorted(merged.values(), key=lambda e: e["six"])
    for e in ev:
        e["total"] = round(e["low"] * 1.3 + e["mid"] + 0.6 * e["high"], 3)
        e["bar"], e["beat"], e["sub"] = e["six"] // 16 + 1, (e["six"] % 16) // 4 + 1, e["six"] % 4
        e["grid_f"] = round(e["grid_s"] * fps, 2)
        e["band"] = BAND[max(("low", "mid", "high"), key=lambda b: e[b])]
    tot = np.array([e["total"] for e in ev])
    thr75 = float(np.percentile(tot, 75)) if len(tot) else 0.0
    for r, i in enumerate(np.argsort(-tot, kind="stable")):
        ev[i]["rank"] = r + 1
    n_s = max(3, round(0.05 * len(ev)))
    att = attack_times(y, sr, [e["grid_s"] for e in ev])
    for e, (a, j) in zip(ev, att):
        e["accent"] = bool(e["total"] >= thr75)
        e["attack_f"] = round(a * fps, 2)
        e["jump_db"] = round(j, 1)
        e["attack_ms"] = round(1000 * (a - e["grid_s"]), 1)
        e["F"] = _fl(a * fps) if e["total"] >= thr["onset_min"] else _fl(e["grid_f"])
        e["tier"] = "S" if e["rank"] <= n_s else "A" if e["accent"] else "B" if e["total"] >= thr["onset_min"] else "C"
        e["kind"] = "onset"
    rms = _rms_db(y, sr)
    n16 = int(dur / six)
    r16 = np.array([_seg_mean(rms, sr, ph + k * six, ph + (k + 1) * six) for k in range(n16)])
    stops = []
    for k in range(4, n16):
        ref = float(np.nanmean(r16[k - 4:k]))
        if ref - r16[k] >= thr["stop_db"] and not (stops and k - stops[-1]["six"] < 8):
            i0, i1 = int(max(0.0, ph + k * six - six / 2) * sr), int((ph + (k + 1) * six) * sr)
            jj = np.where(rms[i0:i1] < ref - 10)[0]
            ts = (i0 + jj[0]) / sr if len(jj) else ph + k * six
            stops.append(dict(kind="stop", six=k, grid_f=round((ph + k * six) * fps, 2), attack_f=round(float(ts) * fps, 2),
                              F=_fl(float(ts) * fps), drop_db=round(float(ref - r16[k]), 1),
                              bar=k // 16 + 1, beat=(k % 16) // 4 + 1, sub=k % 4, tier="S"))
    return dict(events=ev, stops=stops, rms=rms, r16=r16, thr75=thr75, P=P, phase_s=ph, n16=n16)


def _drop_entry(on, y, sr, fps, bar_only=True, min_db=10.0):
    P, ph = on["P"], on["phase_s"]
    fd = find_drop(on["rms"], sr, P, ph, on["n16"], bar_only, min_db)
    if not fd:
        return None
    k, jump = fd
    e = next((x for x in on["events"] if x["six"] == k), None)
    if e is None:                                  # 小节线上没收到起音峰：直接在格点附近找起音
        a, _ = attack_times(y, sr, [ph + k * P / 4])[0]
        att = round(a * fps, 2)
    else:
        att = e["attack_f"]
        e["kind"], e["tier"] = "drop", "S"
    return dict(F=_fl(att), six=k, bar=k // 16 + 1, beat=(k % 16) // 4 + 1, sub=k % 4, attack_f=att, jump_db=round(jump, 1),
                grid_f=round((ph + k * P / 4) * fps, 2))


def _halves(xs):
    """按个数对半分（xs 已按时间排好），返回 (前半中位, 后半中位)。"""
    h = len(xs) // 2
    return float(np.median(xs[:h] or xs)), float(np.median(xs[h:] or xs))


def grid_stats(ev, n, onset_min=0.6):
    """起音 − 格点（ms）：中位、IQR、前后半中位（标签串没串、漂没漂）。
    前后半按起音个数对半分、有 ≥12 个重音就只用重音（按 n/2 帧分的话，8 小节短片的 n/2 正好落在 DROP 上，
    前半只剩前奏里偏软的起音（−3 ms），后半 −11 ms，曲子本身就差 8 ms，和画面无关）。有打击点表时 sync_check 另按
    「现场起音 − 打击点表」分半判漂移，这里的前后半只报告。"""
    on6 = sorted([e for e in ev if e["total"] >= onset_min and e["grid_f"] > 6], key=lambda e: e["grid_f"])
    if len(on6) < 6:
        return None
    am = np.array([e["attack_ms"] for e in on6])
    acc = [e["attack_ms"] for e in on6 if e.get("accent")]
    h1, h2 = _halves(acc if len(acc) >= 12 else [e["attack_ms"] for e in on6])
    return dict(attack_ms_med=round(float(np.median(am)), 1),
                iqr=[round(float(np.percentile(am, 25)), 1), round(float(np.percentile(am, 75)), 1)],
                first_half=round(h1, 1), second_half=round(h2, 1), n=len(on6), halves="重音按个数分" if len(acc) >= 12 else "起音按个数分")


# ------------------------------------------------------------------ map：打击点表
def _bar_char(e):
    if not e:
        return "."
    c = "L" if e["low"] >= 0.5 else ("M" if e["mid"] >= 0.5 else ("H" if e["high"] >= 0.5 else ","))
    return c if e["accent"] or c == "," else c.lower()


def build_map(music, bpm, downbeat_s=None, start_s=0.0, bars=16, fps=FPS, total_f=None, thr=None):
    """打击点表（接口 1）。网格 = 全曲网格（downbeat_s 给了就用它，否则整首歌重新拟合）；第 0 帧 = start_s = 第 1 小节第 1 拍。"""
    thr = dict(THR, **(thr or {}))
    fit = None
    if downbeat_s is None:
        fit = fit_grid(decode_audio(music), SR, bpm)
        bpm, downbeat_s = fit["bpm"], fit["phase_s"] % fit["P"]
    P = 60.0 / bpm
    six = P / 4
    beat_f, bar_f, six_f = P * fps, 4 * P * fps, six * fps
    nb = (start_s - downbeat_s) / P                      # start_s 离第一个小节线几拍（应是 4 的整数倍）
    ph = ((downbeat_s - start_s + P / 2) % P) - P / 2     # 窗口第 0 帧附近那一拍的格点（秒，相对第 0 帧）
    n = int(total_f) if total_f else _fl(bars * bar_f)
    y = decode_audio(music, start_s, n / fps + 0.25)
    on = onsets(y, SR, bpm, n, ph, fps, thr)
    drop = _drop_entry(on, y, SR, fps, True, thr["drop_db"])
    ev = on["events"]
    byk = {e["six"]: e for e in ev}
    n_on = len(ev)
    slots = []
    for i in range(int(math.ceil(n / six_f)) + 1):
        gf = (ph + i * six) * fps
        if gf >= n:
            break
        e = byk.get(i)
        base = dict(i=i, bar=i // 16 + 1, beat=(i % 16) // 4 + 1, sub=i % 4, grid_f=round(gf, 2))
        if e:
            base.update(attack_f=e["attack_f"], F=e["F"], total=e["total"], low=round(e["low"], 3), mid=round(e["mid"], 3),
                        high=round(e["high"], 3), band=e["band"], rank=e["rank"], tier=e["tier"], accent=e["accent"])
            if e["kind"] == "drop":
                base["kind"] = "drop"
        else:
            base.update(attack_f=round(gf, 2), F=_fl(gf), total=0.0, low=0.0, mid=0.0, high=0.0, band="-", rank=n_on + 1,
                        tier="C", accent=False)
        slots.append(base)
    sa = [s for s in slots if s["tier"] in ("S", "A")]
    last = max(sa, key=lambda s: s["F"]) if sa else None
    breath = None
    if drop:
        b0, b1 = max(0, (drop["bar"] - 3) * 4), (drop["bar"] - 1) * 4          # DROP 前 2 小节里最安静的一拍
        q = [(sum(byk[k]["total"] for k in range(bt * 4, bt * 4 + 4) if k in byk), bt) for bt in range(b0, b1)]
        if q:
            qt, qb = min(q)
            breath = dict(F=_fl((ph + qb * P) * fps), bar=qb // 4 + 1, beat=qb % 4 + 1, sub=0, total=round(qt, 2))
    nbars = int(math.ceil(len(slots) / 16))
    bar_map = [" ".join("".join(_bar_char(byk.get(k)) for k in range(b * 16 + j * 4, b * 16 + j * 4 + 4)) for j in range(4))
               for b in range(nbars)]
    bar_db = [round(_seg_mean(on["rms"], SR, ph + b * 4 * P, min(n / fps, ph + (b + 1) * 4 * P)), 1) for b in range(nbars)]
    tiers = {t: sum(1 for s in slots if s["tier"] == t) for t in "SABC"}
    out = {"version": VERSION, "music": os.path.abspath(music), "fps": fps, "bpm": round(bpm, 4), "beat_f": round(beat_f, 4),
           "bar_f": round(bar_f, 4), "six_f": round(six_f, 4), "start_s": start_s, "downbeat_s": round(downbeat_s, 4),
           "phase_s": round(ph, 6), "bars": int(bars), "total_f": n,
           "note": "帧号都是片内帧号（第 0 帧 = start_s = 第 1 小节第 1 拍）；F = 画面该变的整数帧 = round(真实起音帧)，没有起音的格 = round(格点)；"
                   "tier：S = 有起音格里前 5 %，A = 前 25 %（accent），B = total ≥ 0.6，C = 其余；sub 从 0 起；"
                   "bar_map 一格一个 16 分（四格一拍）：大写 = 重音（L 低频 808 / M 军鼓拍手 / H 镲），小写 = 有起音但不重，, = 很弱，. = 没有起音。"
                   "相位用全曲网格（music.json 的 downbeat_s），不在窗口里拟合（会锁到反拍）。",
           "slots": slots, "drop": drop,
           "stops": [{k: x[k] for k in ("F", "bar", "beat", "sub", "attack_f", "grid_f", "drop_db")} for x in on["stops"]],
           "last_accent": {k: last[k] for k in ("F", "bar", "beat", "sub", "tier", "rank")} if last else None,
           "breath": breath, "bar_map": bar_map, "bar_db": bar_db, "tiers": tiers,
           "onsets_per_s": round(sum(1 for e in ev if e["total"] >= thr["onset_min"]) / (n / fps), 2),
           "grid_check": dict(grid_stats(ev, n, thr["onset_min"]) or {}, start_beats_from_downbeat=round(nb, 3))}
    if fit:
        out["grid_fit"] = fit
    return out


# ------------------------------------------------------------------ 画面判法（sync_check）
def classify(d, g):
    """sync_check 判法 + 对比度。g = 整数帧。返回 (cls, pk, peak, base, contrast)；contrast = max(d[g−1], d[g]) / 中位(±12)。"""
    n = len(d)
    if not (3 <= g < n - 3):
        return None
    lo, hi = max(1, g - 12), min(n, g + 13)
    base = float(np.median(d[lo:hi])) + 1e-4
    th = max(2.0 * base, 0.03)
    st = {k: d[g + k] >= th for k in range(-3, 4)}
    if st[0] or st[-1]:
        cls, pk = "ON", (0 if st[0] else -1)
    elif any(st[k] for k in (1, 2, 3)):
        cls, pk = "LATE", min(k for k in (1, 2, 3) if st[k])
    elif st[-2] or st[-3]:
        cls, pk = "EARLY", (-2 if st[-2] else -3)
    else:
        cls, pk = "WEAK", int(np.argmax(d[g - 3:g + 4])) - 3
    return cls, pk, float(d[g + pk]), base, float(max(d[g], d[g - 1]) / base)


def g_on(attack_f):
    return int(math.ceil(attack_f - 1e-6))


def count(d, frames, attack=False):
    """frames = 整数帧（sync_check 原样，g = 帧）或 attack=True 时的起音帧（小数，g = ceil）。"""
    c = dict(ON=0, LATE=0, EARLY=0, WEAK=0)
    for g in frames:
        r = classify(d, g_on(g) if attack else int(g))
        if r:
            c[r[0]] += 1
    c["n"] = sum(c.values())
    c["on_pct"] = round(c["ON"] / max(1, c["n"]), 3)
    return c


def _what(cls, pk, con, k):
    if cls == "ON":
        return "卡上了但不够狠" if con < k else "卡上了"
    if cls == "LATE":
        return f"晚了 {pk} 帧"
    if cls == "EARLY":
        return f"早了 {-pk} 帧"
    return "没卡上"


# ------------------------------------------------------------------ check：整片卡点门禁
def mux_offset(video, music, start_s, sr=SR, segs=(1.0, 8.0, 15.0, 22.0), win=2.0, thr=None):
    """交付用：成片音轨和配乐（从 start_s 起）分段做 FFT 互相关，返回每段的偏移（ms，正 = 成片音轨晚）和相关系数。
    门槛：每段 |偏移| ≤ 1 ms、相关 ≥ 0.95（编码器前置延迟、-ss 不准、loudnorm 改了时间都会挂在这里）。"""
    thr = dict(THR, **(thr or {}))
    a = decode_audio(video, 0.0)
    b = decode_audio(music, start_s, len(a) / sr + 0.5)
    out = []
    for t in segs:
        i0, n = int(t * sr), int(win * sr)
        x, y = a[i0:i0 + n], b[i0:i0 + n]
        if len(x) < n or len(y) < n:
            continue
        x = (x - x.mean()) / (x.std() + 1e-12)
        y = (y - y.mean()) / (y.std() + 1e-12)
        L = 1 << int(np.ceil(np.log2(2 * n)))
        c = np.fft.irfft(np.fft.rfft(x, L) * np.conj(np.fft.rfft(y, L)), L)
        c = np.r_[c[-n + 1:], c[:n]]
        k = int(np.argmax(c)) - (n - 1)
        out.append(dict(t=t, lag_ms=round(1000 * k / sr, 3), corr=round(float(c.max() / n), 3)))
    ok = bool(out) and all(abs(o["lag_ms"]) <= thr["mux_lag_ms"] and o["corr"] >= thr["mux_corr"] for o in out)
    return dict(status="ok" if ok else "fail", value=out, want=f"每段 |偏移| ≤{thr['mux_lag_ms']:g} ms、相关 ≥{thr['mux_corr']:g}")


def sync_check(d, y, sr, M, plan_hits=None, shots=None, fps=FPS, thr=None):
    """整片卡点（接口 2b）。d = 画面帧差，y = 和画面对齐的音轨（第 0 样点 = 第 0 帧），M = music_map（或只含 bpm / phase_s）。"""
    thr = dict(THR, **(thr or {}))
    n = len(d)
    bpm = float(M["bpm"])
    P = 60.0 / bpm
    six = P / 4
    phase_s = float(M.get("phase_s", (M["slots"][0]["grid_f"] / fps) if M.get("slots") else 0.0))
    on = onsets(y, sr, bpm, n, phase_s, fps, thr)
    ev = on["events"]
    stops = on["stops"]
    mslot = {s["i"]: s for s in M.get("slots") or []}
    checks, issues = {}, []

    def shot_of(f):
        return next((s["id"] for s in shots or [] if s["start_f"] <= f < s["end_f"]), None)

    def label(k, e=None):
        s = mslot.get(k)
        src = s if s and s.get("total") else e
        band = (src or {}).get("band", "-")
        rk = f"全窗第 {s['rank']} 强" if s and s.get("total") else (f"本片第 {e['rank']} 强" if e else "无起音")
        return f"{k // 16 + 1}.{(k % 16) // 4 + 1}.{k % 4}", BAND_SAY.get(band, band), rk

    # ---- sync_grid：网格对不对（同时验混流）：起音 − 格点中位、前后半漂移；DROP 锚防半拍 / 整拍错
    gs = grid_stats(ev, n, thr["onset_min"])
    drop = _drop_entry(on, y, sr, fps, False, thr["drop_db"])
    plan_drop = next((h["F"] for h in plan_hits or [] if h.get("kind") == "drop"), None)
    map_drop = (M.get("drop") or {}).get("F") if M.get("drop") else None
    anchor, anchor_src = (plan_drop, "计划 drop 帧") if plan_drop is not None else (map_drop, "打击点表 drop 帧")
    where, st = [], "ok"
    if gs is None:
        st, val = "fail", "音轨里量不到起音（没声音、或者不是这首歌）"
        where.append(val)
    else:
        val = dict(gs)
        # 漂移（前后半差）：有打击点表时比「现场起音 − 打击点表起音」（同一首歌同一段，正常前后都是 0，和曲子内容无关）；
        # 没有表才比「起音 − 格点」（这时网格来自节拍表的 BPM，漂了说明 BPM 不对）。
        dv = [(e["grid_f"], e["attack_f"] - mslot[e["six"]]["attack_f"]) for e in sorted(ev, key=lambda e: e["grid_f"])
              if e["total"] >= thr["onset_min"] and mslot.get(e["six"], {}).get("total", 0) >= thr["onset_min"]] if mslot else []
        if len(dv) >= 6:
            m1, m2 = _halves([1000 * x / fps for _, x in dv])
            val.update(drift_ms=round(m2 - m1, 1), drift_src="现场起音 − 打击点表（前后半按个数分）",
                       map_halves=[round(m1, 1), round(m2, 1)])
        else:
            val.update(drift_ms=round(gs["second_half"] - gs["first_half"], 1), drift_src="起音 − 格点（前后半按个数分）")
        if abs(gs["attack_ms_med"]) > thr["grid_ms"] or abs(val["drift_ms"]) > thr["grid_drift_ms"]:
            st = "fail"
            where.append(f"起音 − 格点中位 {gs['attack_ms_med']:+.1f} ms，前后半漂移 {val['drift_ms']:+.1f} ms（{val['drift_src']}）："
                         "节拍表的 BPM / 第 0 帧和音乐对不上，或者音轨被拉伸了")
        if dv:                                      # 现场起音对打击点表：交付时自己的音轨被挪了、换了也会挂在这里
            val["map_dev_ms"] = round(1000 * float(np.median([x for _, x in dv])) / fps, 1)
            if abs(val["map_dev_ms"]) > thr["map_dev_ms"]:
                st = "fail"
                where.append(f"现场起音比打击点表平均差 {val['map_dev_ms']:+.1f} ms：音轨不是从 start_s 起的那一段")
        if drop:
            val["drop_attack_f"], val["drop_pos"] = drop["attack_f"], f"{drop['bar']}.{drop['beat']}.{drop['sub']}"
        if anchor is None:
            if st == "ok":
                st = "warn"
            where.append("没有 DROP 锚（计划和打击点表都没有 drop）：半拍 / 整拍错位查不出")
        elif drop is None:
            if st == "ok":
                st = "warn"
            where.append(f"音轨里量不到 DROP（{anchor_src} {anchor}）：半拍 / 整拍错位查不出")
        else:
            val["drop_anchor_f"], val["drop_anchor"] = anchor, anchor_src
            if abs(drop["attack_f"] - anchor) > thr["drop_f"]:
                st = "fail"
                where.append(f"音乐的 DROP 起音在第 {drop['attack_f']:.1f} 帧（{val['drop_pos']}），{anchor_src}是 {anchor}："
                             f"差 {drop['attack_f'] - anchor:+.1f} 帧（{(drop['attack_f'] - anchor) / (six * fps):+.1f} 个 16 分）")
    checks["sync_grid"] = dict(status=st, value=val, where=where,
                               want=f"起音−格点中位 |·|≤{thr['grid_ms']:g} ms，前后半漂移≤{thr['grid_drift_ms']:g} ms，"
                                    f"DROP 起音离计划 drop 帧 ≤{thr['drop_f']:g} 帧，现场起音对打击点表 ≤{thr['map_dev_ms']:g} ms")

    # ---- sync_top：最强 16 个重音。最后一个重音（final）之后是海报停留，不算；试跑短片的打击点表带 top_n（按窗口长短折算，
    # 和 stages.sync_rows 的「重」同一个口径），阈值按 top_on / top_n、top_c_n / top_n 的比例折算
    la_F = ((M.get("last_accent") or {}).get("F"))
    top_n = int(M.get("top_n") or thr["top_n"])
    cand = [e for e in sorted(ev, key=lambda e: -e["total"]) if 3 <= e["F"] < n - 3 and (la_F is None or e["F"] <= la_F + 1)][:top_n]
    rows = [(e, classify(d, g_on(e["attack_f"]))) for e in cand]
    rows = [(e, r) for e, r in rows if r]
    on_n = sum(r[0] == "ON" for _, r in rows)
    c3 = sum(r[4] >= thr["top_c"] for _, r in rows)
    peaks = [max(d[g_on(e["attack_f"])], d[g_on(e["attack_f"]) - 1]) for e, _ in rows]
    top = dict(on=on_n, c3=c3, n=len(rows), contrast_med=round(float(np.median([r[4] for _, r in rows])), 2) if rows else None,
               peak_med=round(float(np.median(peaks)), 3) if rows else None)
    tw = []
    for (e, r), pkv in zip(rows, peaks):
        if r[0] != "ON" or r[4] < thr["top_c"]:
            pos, say, rk = label(e["six"], e)
            msg = (f"第 {e['F']} 帧（{pos}，{say}，{rk}，重）：{_what(r[0], r[1], r[4], thr['top_c'])}，d {pkv:.3f}，"
                   f"周围 {r[3]:.3f}（{r[4]:.1f} 倍），要 ≥ {thr['top_c']:g} 倍")
            issues.append(dict(check="sync_top", shot=shot_of(e["F"]), F=e["F"], kind=KIND[r[0]], lvl="重", msg=msg, pos=pos,
                               d=round(pkv, 4), base=round(r[3], 4), ratio=round(r[4], 2), need=thr["top_c"]))
            tw.append(msg)
    sc = len(rows) / max(1, thr["top_n"])          # 窗口太短、重音不到 16 个时按比例（试跑短片）
    need_on, need_c = math.ceil(thr["top_on"] * sc - 1e-9), math.ceil(thr["top_c_n"] * sc - 1e-9)
    ok = len(rows) >= 4 and on_n >= need_on and c3 >= need_c
    checks["sync_top"] = dict(status="ok" if ok else "fail", where=tw,
                              value=f"ON {on_n}/{len(rows)}，对比度≥{thr['top_c']:g} 的 {c3}/{len(rows)}（中位 {top['contrast_med']}）",
                              want=(f"ON ≥{thr['top_on']}/{thr['top_n']} 且对比度≥{thr['top_c']:g} 的 ≥{thr['top_c_n']}/{thr['top_n']}"
                                    + (f"（这一集只算 {len(rows)} 个：≥{need_on}、≥{need_c}）" if len(rows) != thr["top_n"] else "")
                                    + ("；最后一个重音之后不算" if la_F is not None else "")))

    # ---- sync_select：选择性（每个 8 分都切、到处抖不会过）
    # 重音 = 打击点表的 S / A 档（全窗口口径，和计划、切点用的是同一套；试跑短片的窗口自己算前 25 % 会多出几个 B 档），没有表用现场的
    is_acc = (lambda e: bool((mslot.get(e["six"]) or {}).get("accent"))) if mslot else (lambda e: e["accent"])
    acc = [e["attack_f"] for e in ev if is_acc(e) and 3 <= e["F"] < n - 3]
    hb = 0.5 * P * fps
    on0 = count(d, acc, attack=True)["on_pct"]
    onm = count(d, [f - hb for f in acc], attack=True)["on_pct"]
    onp = count(d, [f + hb for f in acc], attack=True)["on_pct"]
    sel = on0 - (onm + onp) / 2
    sw = []
    if sel < thr["select"] and shots:               # 整片不过：逐镜找「平移半拍的位置也一样强」的镜头，出问题单（stages.sync_force 判不过）
        def is_on(f):
            r = classify(d, g_on(f))
            return bool(r) and r[0] == "ON"
        per_s = {}
        for f in acc:
            sid = shot_of(int(round(f)))
            if sid:
                per_s.setdefault(sid, []).append((is_on(f), is_on(f - hb), is_on(f + hb), int(round(f))))
        for sid, xs in sorted(per_s.items()):
            if len(xs) < 3:
                continue
            s0 = sum(x[0] for x in xs) / len(xs)
            s1 = (sum(x[1] for x in xs) + sum(x[2] for x in xs)) / (2 * len(xs))
            if s0 - s1 < thr["select"]:
                msg = (f"{sid}：{len(xs)} 个重音上 ON {s0:.0%}，平移半拍的位置也 ON {s1:.0%}（选择性 {s0 - s1:+.2f}）："
                       "变化平均铺开了，轻重没跟着音乐走——重音以外的切点收一收（「可」格不切，或换成持续运动），力气留给「重 / 轻」帧")
                issues.append(dict(check="sync_select", shot=sid, F=xs[0][3], kind="unselective", lvl="轻",
                                   msg=msg, select=round(s0 - s1, 3)))
                sw.append(msg)
    checks["sync_select"] = dict(status="ok" if sel >= thr["select"] else "fail", where=sw,
                                 value=f"重音 ON {on0:.2f}，平移半拍 {onm:.2f}/{onp:.2f}，选择性 {sel:+.2f}（{len(acc)} 个重音）",
                                 want=f"≥+{thr['select']:.2f}")

    # ---- sync_plan：计划大卡点（big / drop / final）都 ON 且够狠；计划错位只出问题单
    pw, pos_w, bad, nb = [], [], [], 0
    byk = {s["i"]: s for s in M["slots"]} if M.get("slots") else {e["six"]: e for e in ev}
    for h in plan_hits or []:
        if h.get("kind") not in ("big", "drop", "final") or not (3 <= h["F"] < n - 3):
            continue
        nb += 1
        F = int(h["F"])
        k16 = int(round((F / fps - phase_s) / six))
        pos, say, rk = label(k16)
        r = classify(d, F)
        need = thr["plan_c_big"] if h["kind"] == "final" else thr["plan_c"]      # drop 和 big 一样（有的原片 DROP 只有 3.3 倍）
        if r and (r[0] != "ON" or r[4] < need):
            bad.append(F)
            pkv = max(d[F], d[F - 1])
            msg = (f"第 {F} 帧（{pos}，{say}，{rk}，计划 {h['kind']}，重）：{_what(r[0], r[1], r[4], need)}，d {pkv:.3f}，"
                   f"周围 {r[3]:.3f}（{r[4]:.1f} 倍），要 ≥ {need:g} 倍")
            issues.append(dict(check="sync_plan", shot=shot_of(F), F=F, kind=KIND[r[0]], lvl="重", msg=msg, pos=pos, plan=h["kind"],
                               d=round(pkv, 4), base=round(r[3], 4), ratio=round(r[4], 2), need=need))
            pw.append(msg)
        if any(abs(x["F"] - F) <= 1 for x in stops) or any(abs(x["F"] - F) <= 1 for x in M.get("stops") or []):
            continue                                # 急停：整体事件，不按起音判位置
        e = byk.get(k16)
        tot = (e or {}).get("total", 0.0)
        stronger = [x for kk in range(k16 - 2, k16 + 3) if kk != k16 for x in [byk.get(kk)] if x and x.get("total", 0) > tot + 0.3]
        if tot < thr["onset_min"] or stronger:
            s_ = max(stronger, key=lambda x: x["total"]) if stronger else None
            mv = (f"，挪到 {s_['bar']}.{s_['beat']}.{s_['sub']}（第 {s_['F']} 帧，total {s_['total']:.2f}）" if s_ else "")
            msg = (f"第 {F} 帧（{pos}，计划 {h['kind']}）：计划错位，这一格音乐 total {tot:.2f}"
                   + ("（没有起音）" if tot < thr["onset_min"] else "，旁边有更强的重音") + mv)
            issues.append(dict(check="sync_plan", shot=shot_of(F), F=F, kind="misplaced", lvl="重", msg=msg, pos=pos, plan=h["kind"],
                               to=s_["F"] if s_ else None))
            pos_w.append(msg)
    checks["sync_plan"] = (dict(status="ok" if not bad else "fail", value=f"{nb - len(bad)}/{nb}" + (f"，计划错位 {len(pos_w)} 个" if pos_w else ""),
                                where=pw + pos_w,
                                want=f"计划的 big / drop / final 全部 ON 且对比度 ≥{thr['plan_c']:g}（final ≥{thr['plan_c_big']:g}）；"
                                     "位置对不上音乐的只出问题单")
                           if plan_hits is not None else dict(status="skip", value="没有节拍表", want="", where=[]))

    # ---- sync_silent：画面最重的 K 下落在无起音处（只 warn）
    base = np.array([np.median(d[max(1, i - 12):i + 13]) + 1e-4 for i in range(n)])
    C = d / base
    C[:2] = 0
    pk, _ = ss.find_peaks(C, distance=4)
    K = int(n / fps)
    topv = sorted(int(x) for x in pk[np.argsort(-C[pk])[:K]])
    ons = np.array(sorted([e["F"] for e in ev if e["total"] >= thr["onset_min"]] + [x["F"] for x in stops]))
    silent = [f for f in topv if not len(ons) or np.min(np.abs(ons - f)) > 2]
    sw = []
    for f in silent:
        msg = f"第 {f} 帧：音乐这里没有起音，画面却有 {C[f]:.1f} 倍的强变化 → 挪到最近的重音上，或者收成持续运动"
        issues.append(dict(check="sync_silent", shot=shot_of(f), F=f, kind="silent", lvl="轻", msg=msg,
                           d=round(float(d[f]), 4), base=round(float(base[f]), 4), ratio=round(float(C[f]), 2)))
        sw.append(msg)
    checks["sync_silent"] = dict(status="ok" if len(silent) <= thr["silent_max"] else "warn", where=sw,
                                 value=f"画面最重 {K} 下里 {len(silent)} 下没有声音", want=f"≤{thr['silent_max']}")

    # ---- sync_on：默认集合（accent + 第 1、3 拍低频）ON%（只报告，和 sync_check 对账）
    dflt = [e for e in ev if e["accent"] or (e["sub"] == 0 and e["beat"] in (1, 3) and e["low"] >= 0.5)]
    dn = count(d, [e["attack_f"] for e in dflt], attack=True)
    checks["sync_on"] = dict(status="info", value=dn, want="只报告", where=[])

    stats = dict(top16_on=on_n, top16_c3=c3, top16_n=len(rows), contrast_med=top["contrast_med"], peak_med=top["peak_med"],
                 select=round(sel, 3), on_pct=dn["on_pct"], silent=len(silent), visual_K=K, frames=n,
                 onsets=sum(1 for e in ev if e["total"] >= thr["onset_min"]),
                 attack_ms_med=(gs or {}).get("attack_ms_med"), drop_attack_f=(drop or {}).get("attack_f"))
    per = {}
    for it in issues:
        if it.get("shot"):
            p = per.setdefault(it["shot"], dict(n=0, heavy=0, frames=[], kinds={}))
            p["n"] += 1
            p["heavy"] += it["lvl"] == "重"
            p["frames"].append(it["F"])
            p["kinds"][it["kind"]] = p["kinds"].get(it["kind"], 0) + 1
    return dict(checks=checks, issues=issues, stats=stats, shots=per)


# ------------------------------------------------------------------ shot：逐镜卡点（接口 2c）
def _corr(a, b):
    a, b = a - a.mean(), b - b.mean()
    return float((a * b).sum() / math.sqrt((a * a).sum() * (b * b).sum() + 1e-12))


def same_view(a, b, r=3):
    """两帧（160×90 灰度）是不是同一个构图：平移 ±r 像素（≈ ±36 px / 1920 宽，背景慢推慢移的那点位移）里最大的相关系数。"""
    h, w = a.shape
    best = -1.0
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            aa = a[max(0, dy):h + min(0, dy), max(0, dx):w + min(0, dx)]
            bb = b[max(0, -dy):h + min(0, -dy), max(0, -dx):w + min(0, -dx)]
            best = max(best, _corr(aa, bb))
    return best


def cut_same(gray, i):
    """片段帧号 i 处的切点前后像不像：第 i−2 帧对第 i+1 帧（提前 1 帧也算：取 i、i−1 两处里更不像的）。
    11-04 在试跑稿v3 Codex 初剪上量：真切 −0.4–0.5，连续运动 0.85–1.0，同一张图挪一点 / 放大一点 0.65–0.85。"""
    vals = [same_view(gray[j - 2], gray[j + 1]) for j in (i, i - 1) if 2 <= j and j + 1 < len(gray)]
    return min(vals) if vals else None


def cuts_real(gray, a, frames, thr):
    """计划切点有没有真切出来：[(F, 像不像, 切了没有)]（a = 这段画面第 0 帧的片内帧号；首尾 4 帧内的不查）。"""
    out = []
    for F in sorted(set(int(x) for x in frames)):
        i = F - a
        if i < 4 or len(gray) - i < 4:
            continue
        s = cut_same(gray, i)
        if s is not None:
            out.append((F, s, s < thr["cut_corr"]))
    return out


def cut_msgs(res, thr, who=""):
    """计划切点真切出来的不到 cut_min：没切的逐条列出来（够了就不报）。"""
    if not res or sum(1 for r in res if r[2]) >= thr["cut_min"] * len(res):
        return []
    n_ok = sum(1 for r in res if r[2])
    head = f"（这一镜计划切点 {len(res)} 个只真切了 {n_ok} 个；切 = 换素材 / 景别 / 机位、实拍和字卡交替，挪一点放大一点不算）"
    bad = [(F, s) for F, s, ok in res if not ok]
    return [dict(F=F, lvl="切", kind="nocut", same=round(s, 3),
                 msg=f"{who}第 {F} 帧（计划切点）：没切，前后还是同一个构图（相似度 {s:.2f}）" + (head if j == 0 else ""))
            for j, (F, s) in enumerate(bad)]


def shot_check(d, start_f, table, thr=None, gray=None):
    """单镜 render（第 0 帧 = 片内 start_f）对「本镜卡点」表逐帧量：重 = ±1 帧内第一下强变化、对比度 ≥3 倍且 d ≥0.08；
    轻 = ≥2 倍且 d ≥0.04；可 = 不查；表外 ±2 帧内没有起音却出现 ≥4 倍且 d ≥0.08 的强变化 = silent。
    镜头首尾 4 帧内的格不查（要和上一镜 / 下一镜一起量，整片门禁管）。
    gray = 160×90 灰度帧（给了才查）：「重」帧卡上了，但变化前一帧和变化后第 4 帧几乎同图（平移 ±3 像素内最大相关 > flash_corr）= flash_return
    （闪白、反相、整屏亮度跳、冲击闪完回到旧构图都不算卡点；急停行不查）。原片最强 16 个重音里这样的：点火 1/11、Light 0/9，所以只放逐镜（发回代理），不进整片 sync_top。"""
    thr = dict(THR, **(thr or {}))
    n = len(d)
    a, b = int(start_f), int(start_f) + n
    need = {"重": thr["shot_heavy"], "轻": thr["shot_light"]}
    misses, silent, seen = [], [], 0
    rows = sorted(table or [], key=lambda r: r["F"])
    for r in rows:
        lvl, F = r.get("lvl"), int(r["F"])
        if lvl not in need or F - a < 4 or b - F < 4:
            continue
        g = g_on(r["attack_f"]) if r.get("attack_f") is not None else F
        c = classify(d, g - a)
        if not c:
            continue
        seen += 1
        cls, pk, _, base, con = c
        k, absmin = need[lvl]
        pkv = float(max(d[g - a], d[g - a - 1]))
        same = None
        if gray is not None and lvl == "重" and r.get("kind") != "stop" and cls == "ON" and con >= k and pkv >= absmin:
            p = g - a - 1 if d[g - a - 1] >= max(2.0 * base, 0.03) else g - a   # 变化第一次出现的那一帧（本镜帧号；g−1 也算 ON）
            if 1 <= p and p + 4 < len(gray):
                same = same_view(gray[p - 1], gray[p + 4])
        if same is not None and same > thr["flash_corr"]:
            desc = r.get("desc") or ""
            msg = (f"第 {F} 帧（{desc + '，' if desc else ''}{lvl}）：这一帧有变化（{con:.1f} 倍），但 4 帧后又回到变化前的构图"
                   f"（相似度 {same:.2f}）：闪白、反相、整屏亮度跳、冲击闪完回原图都不算卡点——新构图要从这一帧起、留下来")
            misses.append(dict(F=F, lvl=lvl, kind="flash_return", d=round(pkv, 4), base=round(base, 4), ratio=round(con, 2),
                               need=round(max(k * base, absmin), 4), same=round(same, 3), msg=msg))
        elif cls != "ON" or con < k or pkv < absmin:
            desc = r.get("desc") or ""
            what = _what(cls, pk, con, k) if not (cls == "ON" and con >= k) else f"卡上了但变化太小（d {pkv:.3f}）"
            msg = (f"第 {F} 帧（{desc + '，' if desc else ''}{lvl}）：{what}，d {pkv:.3f}，周围 {base:.3f}（{con:.1f} 倍），"
                   f"要 ≥ {k:g} 倍且 d ≥ {absmin:g}")
            misses.append(dict(F=F, lvl=lvl, kind=KIND[cls], d=round(pkv, 4), base=round(base, 4), ratio=round(con, 2),
                               need=round(max(k * base, absmin), 4), msg=msg))
    if gray is not None:                                               # 「切」= 计划切点：要真的换构图（标成「可」时代理一刀不切）
        res = cuts_real(gray, a, [r["F"] for r in rows if r.get("lvl") == "切"], thr)
        seen += len(res)
        misses += [dict(m, d=None, base=None, ratio=None, need=None) for m in cut_msgs(res, thr)]
    ks, absmin = thr["shot_silent"]
    marks = [int(r["F"]) for r in rows]
    for i in range(4, n - 1):
        F = a + i
        base = float(np.median(d[max(1, i - 12):i + 13])) + 1e-4
        if d[i] >= max(ks * base, absmin) and d[i] >= d[i - 1] and d[i] >= d[i + 1] and not any(abs(F - x) <= 2 for x in marks):
            silent.append(dict(F=F, d=round(float(d[i]), 4), ratio=round(float(d[i] / base), 2),
                               msg=f"第 {F} 帧：音乐这里没有起音，画面却有 {d[i] / base:.1f} 倍的强变化 → 挪到最近的「重 / 轻」帧，或者收成持续运动"))
    ok = not misses and not silent
    text = "" if ok else "\n".join(["## 卡点没做到（编排器渲完逐帧量的）"] + [f"- {x['msg']}" for x in misses + silent])
    return dict(ok=ok, checked=seen, misses=misses, silent=silent, text=text, frames=n, start_f=a)


# ------------------------------------------------------------------ CLI
def main():
    ap = argparse.ArgumentParser(description="卡点实测：map 打击点表 / check 整片卡点门禁 / shot 逐镜卡点")
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("map", help="从配乐量打击点表 music_map.json")
    p.add_argument("music")
    p.add_argument("--bpm", type=float, required=True)
    p.add_argument("--downbeat-s", type=float, help="全曲网格的一个小节线（music.json 的 downbeat_s）；不给就整首歌重新拟合")
    p.add_argument("--start-s", type=float, required=True)
    p.add_argument("--bars", type=int, required=True)
    p.add_argument("--fps", type=int, default=FPS)
    p.add_argument("--total-f", type=int, help="窗口帧数（默认 round(bars × 小节帧)）")
    p.add_argument("--out", required=True)
    p = sp.add_parser("check", help="整片卡点门禁（sync_grid / top / select / plan / silent / on / mux）")
    p.add_argument("video")
    p.add_argument("--map")
    p.add_argument("--bpm", type=float, help="没有打击点表时：节拍表的 BPM（第 0 帧在拍上）")
    p.add_argument("--phase-s", type=float, default=0.0)
    p.add_argument("--music")
    p.add_argument("--start-s", type=float, default=0.0)
    p.add_argument("--own-audio", action="store_true", help="用视频自己的音轨（交付成片）；同时给了 --music 就做 sync_mux 互相关")
    p.add_argument("--beatmap")
    p.add_argument("--shotlist")
    p.add_argument("--thr", help="阈值覆盖 JSON（gates.V2['sync']）")
    p.add_argument("--cache", help="画面帧差缓存 .npz")
    p.add_argument("--fps", type=int, default=FPS)
    p.add_argument("--out", required=True)
    p = sp.add_parser("shot", help="逐镜卡点（单镜 render.mp4 对「本镜卡点」表）")
    p.add_argument("render")
    p.add_argument("--start-f", type=int, required=True)
    p.add_argument("--table", required=True)
    p.add_argument("--thr")
    p.add_argument("--out", required=True)
    a = ap.parse_args()
    thr = {k: v for k, v in (_load(getattr(a, "thr", None)) or {}).items() if k in THR}

    if a.cmd == "map":
        M = build_map(a.music, a.bpm, a.downbeat_s, a.start_s, a.bars, a.fps, a.total_f, thr)
        _save_json(a.out, M)
        sa = [s for s in M["slots"] if s["tier"] in ("S", "A")]
        print(f"{os.path.basename(a.music)}：{M['bpm']} BPM，{M['bars']} 小节 {M['total_f']} 帧，档位 {M['tiers']}，S/A {len(sa)} 格；"
              f"起音−格点 {M['grid_check'].get('attack_ms_med')} ms；drop {M['drop']}；急停 {M['stops']}；"
              f"最后一个重音 {M['last_accent']}；屏息 {M['breath']}")
        return 0
    if a.cmd == "shot":
        d, gray = video_diff(a.render, gray=True)
        r = shot_check(d, a.start_f, _load(a.table), thr, gray)
        _save_json(a.out, r)
        print(f"{os.path.basename(a.render)}：查了 {r['checked']} 格，没卡上 {len(r['misses'])}，无声强变化 {len(r['silent'])}")
        return 0
    # check
    d = video_diff(a.video, a.cache)
    n = len(d)
    M = _load(a.map) if a.map else None
    if M is None:
        if not a.bpm:
            ap.error("check 要 --map 或 --bpm")
        M = dict(bpm=a.bpm, phase_s=a.phase_s, slots=[], stops=[], drop=None)
    if a.own_audio:
        y = decode_audio(a.video, 0.0)
        src = "own"
    else:
        if not a.music:
            ap.error("check 要 --music（没声音的 cut）或 --own-audio")
        y = decode_audio(a.music, a.start_s, n / a.fps + 0.25)
        src = "music"
    bm, sl = _load(a.beatmap), _load(a.shotlist)
    r = sync_check(d, y, SR, M, (bm or {}).get("hits") if bm else None, (sl or {}).get("shots"), a.fps, thr)
    if sl and sl.get("shots"):                                         # sync_cuts：计划切点真切出来（每镜 ≥ cut_min）
        th = dict(THR, **thr)
        _, gray = video_diff(a.video, gray=True)
        bad, where = [], []
        for s in sl["shots"]:
            ms = cut_msgs(cuts_real(gray, 0, [F for F in s.get("cuts_f") or [] if s["start_f"] + 4 <= F < s["end_f"] - 4], th),
                          th, f"{s['id']} ")
            if ms:
                bad.append(s["id"])
                where.append(f"{s['id']}：计划切点没切 {len(ms)} 个（{', '.join(str(m['F']) for m in ms[:12])}）")
                for m in ms:
                    r["issues"].append(dict(m, check="sync_cuts", shot=s["id"]))
                    p = r["shots"].setdefault(s["id"], dict(n=0, heavy=0, frames=[], kinds={}))
                    p["n"] += 1
                    p["frames"].append(m["F"])
                    p["kinds"]["nocut"] = p["kinds"].get("nocut", 0) + 1
        r["checks"]["sync_cuts"] = dict(status="fail" if bad else "ok", where=where,
                                        value=f"{len(sl['shots']) - len(bad)}/{len(sl['shots'])} 镜计划切点真切出来",
                                        want=f"每镜计划切点 ≥{th['cut_min']:.0%} 真切（前后两帧相似度 < {th['cut_corr']:g}）")
    if a.own_audio and a.music:
        r["checks"]["sync_mux"] = dict(mux_offset(a.video, a.music, a.start_s, thr=thr), where=[])
    r.update(version=VERSION, video=os.path.abspath(a.video), audio=src, thr=dict(THR, **thr))
    _save_json(a.out, r)
    print(" ".join(f"{k}={v['status']}" for k, v in r["checks"].items()), "|", json.dumps(r["stats"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
