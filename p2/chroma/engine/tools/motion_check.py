# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""运动量验收。
用法：.venv/bin/python tools/motion_check.py <mp4> [--start <成片帧号>] [--flat 0.45]
逐帧（256×144）算 Farneback 光流中位数、剪口（相关 < 0.45 且差 > 0.035）、静止帧（光流 < 0.25 且差 < 0.012），
按剪口切镜头，列出光流均值 < --flat 的「平」镜头。帧号加上 --start 就是成片帧号。
对照：一集 静止 21.7 % / 光流中位 0.74；二集 17.5 % / 0.69；v11 27.0 % / 0.29。
标准：除片尾海报外每个镜头光流均值 ≥ 0.45 px，全片中位 ≥ 0.6 px。
补充（实测）：暗底片只有 10–26 % 的像素有纹理，全画面中位落在黑天黑路上，相机怎么动都读 0。
所以另算「纹理光流」= 只取梯度够大的像素的光流中位；平镜头判定改用纹理光流（< --flat 才算平）。
"""
import subprocess, sys, argparse, numpy as np, cv2

W, H = 256, 144


def measure(path):
    n = W * H * 3
    p = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', path, '-vf', f'scale={W}:{H}:flags=area,format=rgb24',
                          '-f', 'rawvideo', '-'], stdout=subprocess.PIPE)
    prev = None; rec = []
    while True:
        b = p.stdout.read(n)
        if len(b) < n:
            break
        g = cv2.cvtColor(np.frombuffer(b, np.uint8).reshape(H, W, 3), cv2.COLOR_RGB2GRAY)
        if prev is None:
            rec.append((0.0, 1.0, 0.0, 0.0))
        else:
            fl = cv2.calcOpticalFlowFarneback(prev, g, None, 0.5, 3, 15, 3, 5, 1.2, 0)
            a = prev.astype(np.float32) - prev.mean(); c = g.astype(np.float32) - g.mean()
            corr = float((a * c).sum() / np.sqrt((a * a).sum() * (c * c).sum() + 1e-9))
            mag = np.hypot(fl[..., 0], fl[..., 1])
            gx = cv2.Sobel(prev, cv2.CV_32F, 1, 0); gy = cv2.Sobel(prev, cv2.CV_32F, 0, 1)
            tex = np.hypot(gx, gy) > 40
            tflow = float(np.median(mag[tex])) if tex.sum() > 200 else 0.0
            rec.append((float(np.median(mag)), corr,
                        float(np.abs(g.astype(np.float32) - prev).mean() / 255), tflow))
        prev = g
    p.wait()
    return np.array(rec, np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mp4'); ap.add_argument('--start', type=int, default=0); ap.add_argument('--flat', type=float, default=0.45)
    a = ap.parse_args()
    r = measure(a.mp4); flow, corr, diff, tflow = r.T; N = len(r)
    cut = (corr < 0.45) & (diff > 0.035); cut[0] = True
    ok = ~cut
    static = (flow < 0.25) & (diff < 0.012) & ok
    print(f"{a.mp4}: {N} 帧  静止帧 {static.sum() / max(ok.sum(), 1) * 100:.1f}%  光流中位 {np.median(flow[ok]):.2f}px  均值 {flow[ok].mean():.2f}  纹理光流中位 {np.median(tflow[ok]):.2f}px")
    b = list(np.where(cut)[0]) + [N]
    segs = [(b[i], b[i + 1] - 1) for i in range(len(b) - 1) if b[i + 1] - b[i] >= 4]
    flat = [(s, e, float(tflow[s + 1:e + 1].mean())) for s, e in segs if tflow[s + 1:e + 1].mean() < a.flat]
    tot = sum(e - s + 1 for s, e in segs)
    print(f"镜头段 {len(segs)}，平的（纹理光流均值 < {a.flat}）{len(flat)} 段 = {sum(e - s + 1 for s, e, _ in flat) / max(tot, 1) * 100:.0f}% 时长")
    for s, e, m in flat:
        print(f"  平：{s + a.start}–{e + a.start}（{e - s + 1} 帧）纹理光流 {m:.2f}")


if __name__ == '__main__':
    main()
