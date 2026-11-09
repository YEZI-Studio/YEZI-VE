# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""逐镜头自查。
交付前必跑，并且**每一格都要用眼睛看**：按剪口把整片切成镜头，每个镜头取 4 帧（头 / 1/3 / 2/3 / 尾）一行，
旁边写帧段、时间、打分。打分只是预警（红字），不能代替看图：
  blob  = 去掉细线（HUD、字的笔画）以后，亮块（luma > 0.75）占画面的比例的镜头内最大值 —— 有没有真的光
  gray  = 不饱和中灰（0.22–0.75、色度 < 0.08）占画面的比例的镜头内平均 —— 是不是一片灰天灰云
  act   = 镜头内相邻帧变化的平均 —— 有没有在动
  空镜头预警：blob < 0.006 且 gray > 0.35（灰天灰云、没有光）。
用法：.venv/bin/python tools/empty_scan.py <mp4> <out.jpg> [--start 0]
"""
import subprocess, argparse, numpy as np, cv2
from PIL import Image, ImageDraw, ImageFont

ap = argparse.ArgumentParser(); ap.add_argument('mp4'); ap.add_argument('out'); ap.add_argument('--start', type=int, default=0)
a = ap.parse_args()
W, H = 256, 144; n = W * H * 3
p = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', a.mp4, '-vf', f'scale={W}:{H}:flags=area,format=rgb24', '-f', 'rawvideo', '-'],
                     stdout=subprocess.PIPE)
fr = []
while True:
    b = p.stdout.read(n)
    if len(b) < n:
        break
    fr.append(np.frombuffer(b, np.uint8).reshape(H, W, 3))
p.wait(); N = len(fr)
g = [cv2.cvtColor(f, cv2.COLOR_RGB2GRAY).astype(np.float32) for f in fr]
cut = [0]
for i in range(1, N):
    x0 = g[i - 1] - g[i - 1].mean(); x1 = g[i] - g[i].mean()
    corr = (x0 * x1).sum() / np.sqrt((x0 * x0).sum() * (x1 * x1).sum() + 1e-9)
    if corr < 0.55 and np.abs(g[i] - g[i - 1]).mean() / 255 > 0.03 and i - cut[-1] >= 4:
        cut.append(i)
cut.append(N)
segs = [(cut[k], cut[k + 1] - 1) for k in range(len(cut) - 1)]
K = np.ones((3, 3), np.uint8)
rows = []
for s, e in segs:
    blob = 0.0; gray = []; act = []
    for i in range(s, e + 1):
        x = fr[i].astype(np.float32) / 255; y = g[i] / 255
        m = cv2.morphologyEx((y > 0.75).astype(np.uint8), cv2.MORPH_OPEN, K)      # 细线、字的笔画被去掉
        blob = max(blob, float(m.mean()))
        sat = x.max(-1) - x.min(-1); gray.append(float(((y > 0.22) & (y < 0.75) & (sat < 0.08)).mean()))
        if i > s:
            act.append(float(np.abs(g[i] - g[i - 1]).mean() / 255))
    gm = float(np.mean(gray)); am = float(np.mean(act)) if act else 0.0
    rows.append((s, e, blob, gm, am, blob < 0.006 and gm > 0.35))
cw, chh = 224, 126; lab = 300
font = ImageFont.truetype('assets/fonts/DMMono-Medium.ttf', 15)
PER = 24                                                     # 每页 24 个镜头，一页一张图（太长的图看不清）
pages = [rows[i:i + PER] for i in range(0, len(rows), PER)]
for pi, page in enumerate(pages):
  sh = Image.new('RGB', (lab + 4 * cw, len(page) * chh), (14, 14, 14)); d = ImageDraw.Draw(sh)
  for r, (s, e, blob, gm, am, warn) in enumerate(page):
    y0 = r * chh; col = (255, 70, 70) if warn else (255, 220, 0)
    d.text((6, y0 + 6), f'{s + a.start}-{e + a.start} ({e - s + 1}f)', font=font, fill=col)
    d.text((6, y0 + 28), f'{(s + a.start) / 60:5.2f}s', font=font, fill=(200, 200, 200))
    d.text((6, y0 + 50), f'blob {blob:.3f} gray {gm:.2f}', font=font, fill=col)
    d.text((6, y0 + 72), f'act {am:.3f}' + ('  ← 空镜头?' if warn else ''), font=font, fill=col)
    for c, F in enumerate(np.linspace(s, e, 4).round().astype(int)):
        sh.paste(Image.fromarray(fr[F]).resize((cw, chh)), (lab + c * cw, y0))
  sh.save(a.out.replace('.jpg', f'_p{pi + 1}.jpg'), quality=82)
print('pages:', [a.out.replace('.jpg', f'_p{i + 1}.jpg') for i in range(len(pages))])
print(f'{len(rows)} 个镜头；空镜头预警：', [(s + a.start, e + a.start) for s, e, *_, w in rows if w])
