#!/usr/bin/env python3
# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""有效运动量（渲染后量法，门禁 motion_eff / motion_sway 用）：分块平移 + 一拍窗口里的净位移 / 路程。只读输入视频。
跑在引擎 Python 里（numpy + cv2）；编排器 gates.eff_stats 用 subprocess 调它。

  effmotion.py VIDEO --out X.json [--cache X.npz] --groups '{"s01":[0,99],...}' [--hits 99,397] [--exempt 1092-1117,1490-1589]
               [--line 4.33] [--jmax 0.45] [--ppmax 3] [--windows]

为什么分块：整幅仿射（ECC / LK）会被钉在屏上的字、半屏版式拉成「没动」（试跑里有一镜右半屏是静止版式，左半屏素材
±80 px 来回摆，整幅估计只认出 39 % 是刚性运动）。分成 6×4 块，每块单独估平移，摆的那几块就单独被认出来。

每帧（t-1 -> t）：
  d96      门禁原量：96×54 灰度相邻帧平均绝对差（ffmpeg scale，和 gates.motion_series 同口径）
  切点      门禁切点（d96 > max(14, 4×(全片中位+1))）∪ 局部峰（d96>8 且 > 2.5×邻域 ±6 中位+2）。
           切点前后两帧用 ORB+RANSAC 相似变换配准：配得上 = 同素材换构图（reframe，跳变算一次镜头运动），配不上 = 硬切。
  分块      480×270 灰度切 6×4 块；每块 phaseCorrelate 得平移 s_i（换算成 1920 宽的 px），
           raw_i = 块帧差，res_i = 按 s_i 平移后的帧差，刚性份额 σ_i = 1 − res_i/raw_i。
  进展      q_i(t) = 窗口 [t−24, t+24]（≈ 前后各一拍，硬切截断）里 |Σ s_i| / Σ|s_i|（净位移 / 路程）。
  有效运动  eff(t) = d96(t) × Σ_i w_i·k_i，w_i = raw_i / Σraw（这一帧的变化落在哪几块），k_i = (1−σ_i) + σ_i·q_i；
           持续来回的块（路程 ≥3 px/帧、前后半窗路程比在 0.5–2）k_i = q_i：摆动带出的透视 / 缩放残差也按来回算，不当内容（kmode=2）。
           冲击余波豁免：硬切、镜头表 big/drop/final 之后 8 帧 eff = d96，但只豁免真衰减的（后 8 帧运动 < 前 8 帧一半，exmode=2）。
每组（--groups 的 [起帧, 止帧)，切点帧、--exempt 的帧不算）：
  d96 均值、eff 均值、原地来回占比 J = 1 − Σeff/Σd96、同素材缩放乒乓（reframe 缩放方向反转次数）、
  非刚性内容 content = Σ d96·(1−刚性份额) 的均值（抓「静图 + 推拉」）；ok_eff = eff ≥ line，ok_sway = J ≤ jmax 且乒乓 < ppmax。
11-04 验收补的四处：
  · 切点门槛（analyse 的 group_thr，默认关）：门禁切点照旧按全片中位定，别的镜头一改，这一镜认出的切点、有效量会跟着变
    （parts_demo 96–144 帧像素没变，有效量 5.40 → 4.31）。按组定门槛试过、会误伤原片（见 analyse 的说明），先不开；
    提取时已对 d96 > 14 的帧都做配准和分块，以后按组重校准不用重提。
  · 回到旧构图 returns：硬切 / 换构图之后第一帧（32×18 灰度）和本组更早、不相邻的段几乎同图（corr > 0.95）记一次；
    「同一版式每拍跳一下再回来」「两张图轮换」靠它抓（原片每镜最多 3 次、100 帧窗最多 4 次；Codex 初剪 s06 13 次）。
  · 来回的亮度：整屏平均亮度差在 ±hwin 窗（切点截断）里来回的部分 |ΔL|·(1−qL) 从 eff 和 content 里扣掉（只扣 eff，不进 J：
    闪电画面本来就有 0.24）。phaseCorrelate 对亮度不敏感，不扣的话每拍明暗呼吸会被当成满额内容。
  · 长窗口 J_long（±hwin_long 帧，默认 48 ≈ 两拍）：周期 ≥ 2 s 的慢摆、漂浮在一拍窗口里看着是单向的，J 抓不到。
  ok_sway = J ≤ jmax 且乒乓 < ppmax 且 J_long ≤ jlong 且 returns 没超（任一 100 帧窗 ≥ ret_win，或整组 ≥ ret_n 次且占段数 ≥ ret_frac）。
逐帧提取（最慢的一步，26 s 的片约 10 s）缓存在 --cache 的 npz（记着源文件大小和修改时间，变了就重提）；同一条视频换 groups 再量只要几秒。
写文件一律先写唯一临时名再 os.replace（编排器会同时对不同视频调用）。
"""
import argparse
import json
import os
import subprocess
import uuid

import cv2
import numpy as np

VERSION = 3                       # 提取口径版本：改了 extract() 就加 1，旧缓存自动作废（3 = 11-04 验收：d96 > 14 的帧都提、加缩略图和亮度）
H_WIN, E_EXEMPT = 24, 8           # 进展窗口半宽（≈一拍）、冲击余波豁免帧数
H_LONG = 48                       # 长窗口半宽（≈两拍）：慢摆 / 漂浮
CUT_ABS, CUT_K = 14.0, 4.0        # 门禁切点：d96 > max(CUT_ABS, CUT_K × (中位 + 1))
GX, GY = 6, 4
W, H = 480, 270
XS = np.linspace(0, W, GX + 1).astype(int)
YS = np.linspace(0, H, GY + 1).astype(int)
SC = 1920 / W


def frames(vid, w, h):
    p = subprocess.Popen(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i", vid, "-map", "0:v:0",
                          "-vf", f"scale={w}:{h},format=gray", "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
    n = w * h
    try:
        while True:
            b = p.stdout.read(n)
            if len(b) < n:
                break
            yield np.frombuffer(b, np.uint8).reshape(h, w)
    finally:
        p.stdout.close()
        p.wait()


def cut_thr(d, a=1, b=None):
    """门禁切点门槛：max(14, 4×(这一段 d96 中位 + 1))。"""
    seg = d[max(1, a):b]
    return max(CUT_ABS, CUT_K * (float(np.median(seg if len(seg) else d[1:])) + 1))


def cut_candidates(d):
    thr = cut_thr(d)
    gate = set(int(i) for i in np.where(d > thr)[0] if i > 0)
    loc = set()
    for t in range(1, len(d)):
        nb = np.r_[d[max(1, t - 6):t], d[t + 1:t + 7]]
        if len(nb) and d[t] > 8 and d[t] > 2.5 * np.median(nb) + 2:
            loc.add(t)
    return gate, loc, thr


ORB = cv2.ORB_create(nfeatures=1500, fastThreshold=7, edgeThreshold=15)
BF = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
CTR = np.array([[(XS[i] + XS[i + 1]) / 2, (YS[j] + YS[j + 1]) / 2] for j in range(GY) for i in range(GX)], np.float64)


def reframe(a, b):
    """同素材换构图：返回 (每块中心的跳变位移 24×2（1920 px）, log 缩放)，否则 None。"""
    ka, da = ORB.detectAndCompute(a, None)
    kb, db = ORB.detectAndCompute(b, None)
    if da is None or db is None or len(ka) < 30 or len(kb) < 30:
        return None
    m = BF.match(da, db)
    if len(m) < 25:
        return None
    pa = np.float32([ka[x.queryIdx].pt for x in m])
    pb = np.float32([kb[x.trainIdx].pt for x in m])
    M, inl = cv2.estimateAffinePartial2D(pa, pb, method=cv2.RANSAC, ransacReprojThreshold=3.0, maxIters=3000)
    if M is None or inl is None:
        return None
    ni = int(inl.sum())
    if ni < 25 or ni / len(m) < 0.3:
        return None
    s = float(np.hypot(M[0, 0], M[1, 0]))
    if not (0.2 < s < 5):
        return None
    w = cv2.warpAffine(a, M, (W, H), borderMode=cv2.BORDER_REPLICATE)
    raw = np.abs(b[50:220].astype(np.float32) - a[50:220]).mean()
    res = np.abs(b[50:220].astype(np.float32) - w[50:220]).mean()
    if res > 0.6 * raw:
        return None
    mc = CTR @ M[:, :2].T + M[:, 2]               # 块中心 c 在 b 里的位置 = M c；位移 = Mc − c（内容的运动方向）
    return (mc - CTR) * SC, float(np.log(s))


def _src_sig(vid):
    st = os.stat(vid)
    return np.array([VERSION, st.st_size, int(st.st_mtime_ns)], np.int64)


def _save_npz(path, **arrs):
    """唯一临时名（必须以 .npz 结尾，否则 numpy 会自己再加一个）→ os.replace。"""
    tmp = f"{path}.{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp.npz"
    try:
        np.savez_compressed(tmp, **arrs)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _save_json(path, obj):
    tmp = f"{path}.{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def extract(vid, cache=None):
    """逐帧提取：d96、每块平移 / 原差 / 残差、切点处的同素材配准。cache 有效（源文件没变、口径版本相同）就直接读。"""
    if cache and os.path.exists(cache):
        try:
            z = np.load(cache, allow_pickle=False)
            if vid is None or ("src" in z.files and np.array_equal(z["src"], _src_sig(vid))):
                return {k: z[k] for k in z.files}
        except (OSError, ValueError, KeyError):
            pass                                  # 坏缓存（被打断的写入等）：重提
    g96 = np.stack(list(frames(vid, 96, 54))).astype(np.float32)
    N = len(g96)
    d = np.zeros(N, np.float32)
    d[1:] = np.abs(g96[1:] - g96[:-1]).mean(axis=(1, 2))
    gate, loc, thr = cut_candidates(d)
    cand = gate | loc | set(int(i) for i in np.where(d > CUT_ABS)[0] if i > 0)    # 按组定切点时门槛只会更高：> 14 的都配准
    nb = GX * GY
    sh = np.zeros((N, nb, 2), np.float32)
    rsp = np.zeros((N, nb), np.float32)
    raw = np.zeros((N, nb), np.float32)
    res = np.zeros((N, nb), np.float32)
    rfj = np.full((N, nb, 2), np.nan, np.float32)
    rfs = np.full(N, np.nan, np.float32)
    win = {}
    prev8 = prev = None
    for t, cur8 in enumerate(frames(vid, W, H)):
        if t >= N:
            break
        cur = cur8.astype(np.float32) / 255.0
        if prev is not None:
            if t in cand:
                j = reframe(prev8, cur8)
                if j is not None:
                    rfj[t], rfs[t] = j
            if True:                              # 分块每帧都做（候选帧按组判下来不是切点时要用；是切点的分析时清零）
                k = 0
                for jy in range(GY):
                    for ix in range(GX):
                        x0, x1, y0, y1 = XS[ix], XS[ix + 1], YS[jy], YS[jy + 1]
                        pa, pb = prev[y0:y1, x0:x1], cur[y0:y1, x0:x1]
                        hw = (x1 - x0, y1 - y0)
                        if hw not in win:
                            win[hw] = cv2.createHanningWindow(hw, cv2.CV_32F)
                        r0 = float(np.abs(pb - pa).mean())
                        raw[t, k] = r0
                        res[t, k] = r0
                        if r0 > 1e-4 and pa.std() > 0.01:
                            (sx, sy), rr = cv2.phaseCorrelate(pa, pb, win[hw])
                            if abs(sx) < hw[0] / 3 and abs(sy) < hw[1] / 3:
                                c = (x0 + (hw[0] - 1) / 2 - sx, y0 + (hw[1] - 1) / 2 - sy)
                                pw = cv2.getRectSubPix(prev, hw, c)
                                r1 = float(np.abs(pb - pw).mean())
                                if r1 < r0:
                                    sh[t, k] = (sx * SC, sy * SC)
                                    rsp[t, k] = rr
                                    res[t, k] = r1
                        k += 1
        prev8, prev = cur8, cur
    th = g96.reshape(N, 18, 3, 32, 3).mean(axis=(2, 4))               # 32×18 缩略图（回到旧构图用）
    out = dict(d=d, sh=sh, rsp=rsp, raw=raw, res=res, rfj=rfj, rfs=rfs,
               gate=np.array(sorted(gate), np.int64), loc=np.array(sorted(loc), np.int64), thr=np.float64(thr),
               cand=np.array(sorted(cand), np.int64), luma=g96.mean(axis=(1, 2)).astype(np.float32), thumb=th.astype(np.float32))
    if cache:
        _save_npz(cache, src=_src_sig(vid), **out)
    return out


def _bounds(N, hard):
    seg_start = np.zeros(N, int)
    seg_end = np.zeros(N, int)
    bounds = [0] + sorted(hard) + [N]
    for a, b in zip(bounds, bounds[1:]):
        seg_start[a:b] = a
        seg_end[a:b] = b
    return seg_start, seg_end


def _progress(v, sp, seg_start, seg_end, hwin):
    """q = 窗口 [t−hwin, t+hwin]（硬切截断）里每块 |Σ位移| / Σ|位移|；rp = 每帧路程；nondecay = 前后半窗路程比在 0.5–2。"""
    N, nb = sp.shape
    cs_v = np.cumsum(np.r_[np.zeros((1, nb, 2)), v], 0)            # 累积位移
    cs_p = np.cumsum(np.r_[np.zeros((1, nb)), sp], 0)
    q = np.ones((N, nb))
    rp = np.zeros((N, nb))
    nondecay = np.zeros((N, nb), bool)
    for t in range(1, N):
        a = max(seg_start[t] + 1, t - hwin)
        b = min(seg_end[t], t + hwin + 1)
        L = b - a
        if L < 6:
            continue
        net = np.hypot(*(cs_v[b] - cs_v[a]).T)                     # (nb,)
        path = cs_p[b] - cs_p[a]
        ok = path > 1e-6
        q[t, ok] = net[ok] / path[ok]
        rp[t] = path / L
        m = (a + b) // 2
        p1, p2 = cs_p[m] - cs_p[a], cs_p[b] - cs_p[m]
        nondecay[t] = (p2 >= 0.5 * p1) & (p1 >= 0.5 * p2)
    return q, rp, nondecay


def _luma_osc(luma, cuts, hwin):
    """整屏平均亮度来回的量 |ΔL|·(1 − qL)：qL = ±hwin 窗（切点截断）里 |ΣΔL| / Σ|ΔL|（单调变亮 / 变暗 qL = 1，不扣）。"""
    N = len(luma)
    dL = np.zeros(N)
    dL[1:] = np.diff(luma.astype(np.float64))
    s0, s1 = _bounds(N, cuts)
    cs, ca = np.r_[0, np.cumsum(dL)], np.r_[0, np.cumsum(np.abs(dL))]
    osc = np.zeros(N)
    for t in range(1, N):
        a, b = max(s0[t] + 1, t - hwin), min(s1[t], t + hwin + 1)
        p = ca[b] - ca[a]
        qL = abs(cs[b] - cs[a]) / p if p > 1e-6 else 1.0
        osc[t] = abs(dL[t]) * (1 - qL)
    return osc


def _returns(thumb, cuts, a, b, thr=0.95, win=100):
    """组 [a, b) 里「回到旧构图」的次数：切点（硬切 / 换构图）之后第一帧和本组更早、不相邻的段（每 2 帧取一帧）几乎同图
    （32×18 灰度 corr > thr）记一次。返回 (次数, 段数, 任一 win 帧窗里最多几次, 发生的帧)。"""
    if thumb is None:
        return 0, 0, 0, []
    cs = sorted(t for t in cuts if a < t < b - 2)
    segs = list(zip([a] + cs, cs + [b]))
    X = thumb.reshape(len(thumb), -1).astype(np.float64)
    X = X - X.mean(1, keepdims=True)
    X /= np.sqrt((X * X).sum(1, keepdims=True)) + 1e-9
    at = []
    for j in range(2, len(segs)):
        probe = X[min(segs[j][0] + 1, b - 1)]
        old = [t for p0, p1 in segs[:j - 1] for t in range(p0 + 1, p1, 2)]
        if old and float((X[old] @ probe).max()) > thr:
            at.append(int(segs[j][0]))
    wmax = max((sum(1 for t in at if s <= t < s + win) for s in at), default=0)
    return len(at), len(segs), wmax, at


def analyse(x, shots=None, hits=(), exempt=(), win=100, step=5, hwin=H_WIN, qmax=0.25, sway_frac=0.4, pmin=1.5,
            kmode=2, ppath=3.0, exmode=2, windows=False, hwin_long=H_LONG, group_thr=False, luma_osc=True):
    """group_thr = 门禁切点门槛按 shots 各组的中位定（组外 = 全片中位）；默认 False = 全片中位（11-04 验收试过按组：
    很忙的段（Light 15–18 s）中位被切点抬高，真切点认不出来，原片 J 涨到 0.35–0.42，贴着 0.45 线，没采用）。
    luma_osc = 从 eff / content 里扣掉来回的亮度（只扣 eff，J 照不扣的算）。"""
    d, raw, res, rfj, rfs = x["d"], x["raw"].copy(), x["res"].copy(), x["rfj"], x["rfs"]
    sh = x["sh"].astype(np.float64)
    N, nb = raw.shape
    loc = set(int(i) for i in x["loc"])
    if group_thr and "cand" in x:
        thr_t = np.full(N, cut_thr(d))
        for _, a, b in shots or []:
            a, b = max(0, int(a)), min(N, int(b))
            if b - a >= 2:
                thr_t[a:b] = cut_thr(d, a + 1, b)
        gate = set(int(i) for i in np.where(d > thr_t)[0] if i > 0)
    else:
        gate = set(int(i) for i in x["gate"])
    cuts = gate | loc
    is_rf = {t for t in cuts if not np.isnan(rfs[t])}
    hard = sorted(cuts - is_rf)
    cl = sorted(cuts)
    if cl:                                                          # 切点帧不做分块（和旧口径一样：配准位移 / 0）
        sh[cl] = 0.0
        raw[cl] = 0.0
        res[cl] = 0.0
    v = sh.copy()
    for t in is_rf:
        v[t] = rfj[t]
    sig = np.where(raw > 1e-6, 1 - res / np.maximum(raw, 1e-6), 0.0).clip(0, 1)
    sp = np.hypot(v[..., 0], v[..., 1])                            # 每块速度（px/帧）
    seg_start, seg_end = _bounds(N, hard)
    wsum = raw.sum(1, keepdims=True)
    w = np.where(wsum > 1e-9, raw / np.maximum(wsum, 1e-9), 1.0 / nb)

    def keep_of(hw):
        q, rp, nondecay = _progress(v, sp, seg_start, seg_end, hw)
        k1 = (1 - sig) + sig * q
        if kmode == 2:                   # 持续来回的块：块内残差也按 q 折（摆动带出的透视 / 缩放残差不当内容）
            osc = (rp >= ppath) & nondecay
            k1 = np.where(osc, q, k1)
        return (w * k1).sum(1), q, rp, nondecay
    keep, q, rp, nondecay = keep_of(hwin)
    eff = d * keep
    ev = set(hard) | {int(h) for h in hits}
    exm = np.zeros(N, bool)
    gsp = (w * sp).sum(1)                                           # 这一帧（按变化加权）的块速度
    for e in ev:
        if exmode == 0 or not (0 <= e < N):
            continue
        a0, a1, a2 = e, min(N, e + E_EXEMPT), min(N, e + 2 * E_EXEMPT)
        if exmode == 2 and a2 > a1:                                 # 只豁免真衰减的：后 8 帧的运动 < 前 8 帧的一半
            if gsp[a1:a2].mean() >= 0.5 * max(gsp[a0:a1].mean(), 1e-6):
                continue
        exm[a0:a1] = True
    eff = np.where(exm, d, eff)
    keep_l = keep_of(hwin_long)[0] if hwin_long else keep
    eff_l = np.where(exm, d, d * keep_l)
    losc = _luma_osc(x["luma"], cuts, hwin) if (luma_osc and "luma" in x) else np.zeros(N)
    losc = np.where(exm, 0.0, losc)
    eff_j = eff                                                     # J 用不扣亮度的 eff（闪电、冲击闪光本来就是亮度来回）
    eff = np.maximum(0.0, eff - losc)
    tsw = (rp >= pmin) & (q <= qmax) & nondecay & (sig > 0.3)
    sway = ((w * tsw).sum(1) >= sway_frac) & ~exm & (d >= 0.5)
    inn = np.ones(N, bool)
    inn[0] = False
    inn[list(cuts)] = False
    ex = [i for i in exempt if 0 <= i < N]
    if ex:
        inn[ex] = False
    thumb = x.get("thumb")

    def st(a, b):
        a, b = max(0, int(a)), min(N, int(b))
        m = inn.copy()
        m[:max(1, a + 1)] = False
        m[b:] = False
        if m.sum() < 12:
            return None
        dd, ee = d[m], eff[m]
        lo = max(1, a + 1)
        sw = sway[lo:b] & inn[lo:b]
        run = best = 0
        for s_ in sw:
            run = run + 1 if s_ else 0
            best = max(best, run)
        z = [float(rfs[t]) for t in sorted(is_rf) if a < t < b and abs(rfs[t]) > 0.05]
        pp = sum(1 for i in range(1, len(z)) if np.sign(z[i]) != np.sign(z[i - 1]))
        nc = sum(1 for t in cuts if a < t < b)
        nrf = sum(1 for t in is_rf if a < t < b)
        rig = (w[m] * sig[m]).sum(1)
        nret, nseg, rwin, rat = _returns(thumb, cuts, a, b)
        return dict(d96=round(float(dd.mean()), 2), eff=round(float(ee.mean()), 2),
                    J=round(float(1 - eff_j[m].sum() / max(dd.sum(), 1e-9)), 3),
                    J_long=round(float(1 - eff_l[m].sum() / max(dd.sum(), 1e-9)), 3),
                    luma_osc=round(float(losc[m].mean()), 2),
                    sway=round(float(sw.sum() / max(1, inn[lo:b].sum())), 3), run=int(best),
                    rigid=round(float((rig * dd).sum() / max(dd.sum(), 1e-9)), 2),
                    content=round(float(np.maximum(0.0, (1 - rig) * dd - losc[m]).mean()), 2),
                    cuts=nc, reframes=nrf, pingpong=pp, returns=nret, segs=nseg, returns_win=rwin, returns_at=rat[:20],
                    n=int(m.sum()))
    out = {"film": st(0, N), "n_frames": int(N), "n_cuts": len(cuts), "n_reframe": len(is_rf), "n_hard": len(hard)}
    if shots:
        out["shots"] = {sid: st(a, b) for sid, a, b in shots}
    if windows:
        out["windows"] = {f"{a}": st(a, min(N, a + win)) for a in range(0, max(1, N - win + 1), step)}
    per = dict(eff=eff, keep=keep, sway=sway, exm=exm, q=q, sig=sig, w=w, rp=rp, d=d, luma_osc=losc)
    return out, per


def parse_ranges(s, N=None):
    """'1092-1117,1490-1589' → 帧集合（每段 [a, b)）；也收单个帧号。"""
    out = set()
    for r_ in (s or "").split(","):
        r_ = r_.strip()
        if not r_:
            continue
        if "-" in r_[1:]:
            i0, i1 = (int(x) for x in r_.split("-", 1))
            out |= set(range(max(0, i0), i1 if N is None else min(N, i1)))
        else:
            out.add(int(r_))
    return out


RET = dict(ret_win=6, ret_n=5, ret_frac=0.4)                     # 回到旧构图：任一 100 帧窗 ≥ 6 次，或整组 ≥ 5 次且占段数 ≥ 40 %


def judge(r, line, jmax, ppmax, jlong=0.5, ret=None):
    """ok_eff / ok_sway（sway_why = 不过的原因：J、乒乓、J_long、returns）。整片那一行不判 returns（片尾接回片头是设计）。"""
    rt = dict(RET, **(ret or {}))
    for k, v in list((r.get("shots") or {}).items()) + [("film", r.get("film"))]:
        if not v:
            continue
        why = []
        if v["J"] > jmax:
            why.append("J")
        if v["pingpong"] >= ppmax:
            why.append("pingpong")
        if v.get("J_long") is not None and v["J_long"] > jlong:
            why.append("J_long")
        if k != "film" and (v.get("returns_win", 0) >= rt["ret_win"]
                            or (v.get("returns", 0) >= rt["ret_n"] and v["returns"] >= rt["ret_frac"] * max(1, v.get("segs", 0) - 2))):
            why.append("returns")
        v["ok_eff"] = bool(v["eff"] >= line)
        v["ok_sway"] = not why
        v["sway_why"] = why
    return r


def main():
    ap = argparse.ArgumentParser(description="有效运动量：每组 eff / 原地来回占比 J / 同素材乒乓 / 非刚性内容")
    ap.add_argument("video")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache", help="逐帧提取缓存 .npz（建议 = 视频路径 + .eff.npz）")
    ap.add_argument("--groups", help='JSON {"id": [起帧, 止帧)}（这条视频里的帧号），或这样一个 JSON 文件的路径；不给就只量整片')
    ap.add_argument("--hits", default="", help="big/drop/final 帧号，逗号分隔（冲击余波豁免）")
    ap.add_argument("--exempt", default="", help="不算的帧段 a-b（[a, b)），逗号分隔（屏息、最后一击之后）")
    ap.add_argument("--line", type=float, default=4.33)
    ap.add_argument("--jmax", type=float, default=0.45)
    ap.add_argument("--ppmax", type=int, default=3)
    ap.add_argument("--hwin", type=int, default=H_WIN)
    ap.add_argument("--ppath", type=float, default=3.0)
    ap.add_argument("--hwin-long", type=int, default=H_LONG, help="长窗口半宽（慢摆）；0 = 不算")
    ap.add_argument("--jlong", type=float, default=0.5, help="长窗口原地来回占比上限")
    ap.add_argument("--ret", default="", help='回到旧构图的线 JSON，默认 {"ret_win": 6, "ret_n": 5, "ret_frac": 0.4}')
    ap.add_argument("--windows", action="store_true", help="另出 100 帧滑窗（步长 5，校准用）")
    a = ap.parse_args()
    x = extract(a.video, a.cache)
    N = len(x["d"])
    shots = None
    if a.groups:
        g = json.loads(open(a.groups).read() if os.path.exists(a.groups) else a.groups)
        shots = [(k, int(v[0]), int(v[1])) for k, v in g.items()]
    hits = [int(h) for h in a.hits.split(",") if h.strip()]
    ex = parse_ranges(a.exempt, N)
    r, _ = analyse(x, shots, hits, ex, hwin=a.hwin, ppath=a.ppath, windows=a.windows, hwin_long=a.hwin_long)
    ret = dict(RET, **(json.loads(a.ret) if a.ret else {}))
    judge(r, a.line, a.jmax, a.ppmax, a.jlong, ret)
    r["params"] = dict(line=a.line, jmax=a.jmax, ppmax=a.ppmax, hwin=a.hwin, ppath=a.ppath, grid=[GX, GY], exempt_f=E_EXEMPT,
                       kmode=2, exmode=2, version=VERSION, n_exempt=len(ex), hits=hits, hwin_long=a.hwin_long, jlong=a.jlong,
                       ret=ret, group_thr=False, luma_osc=True)
    _save_json(a.out, r)
    f = r["film"] or {}
    print(os.path.basename(a.video), f"帧 {N}", "切点", r["n_cuts"], "同素材换构图", r["n_reframe"],
          "整片 d96 %s eff %s J %s" % (f.get("d96"), f.get("eff"), f.get("J")))
    for sid, v in (r.get("shots") or {}).items():
        print("  ", sid, "帧太少 / 豁免" if not v else
              f"d96 {v['d96']} eff {v['eff']} J {v['J']} J长 {v['J_long']} 乒乓 {v['pingpong']} 回旧构图 {v['returns']}/{v['segs']}"
              f"（窗最多 {v['returns_win']}）亮度来回 {v['luma_osc']} 内容 {v['content']} "
              f"{'过' if v['ok_eff'] and v['ok_sway'] else '不过 ' + ','.join(v['sway_why'] + ([] if v['ok_eff'] else ['eff']))}")


if __name__ == "__main__":
    main()
