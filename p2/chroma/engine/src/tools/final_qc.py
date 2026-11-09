# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""usage: .venv/bin/python -m src.tools.final_qc <film.mp4> [--out out/qc/<name>]
checks: container/stream spec, frame count & duration vs timeline, black / pure-white / frozen frames (outside the
intended holds), photosensitive flash count (WCAG 2.3.1-style: >3 general flashes in any 1 s window = FAIL),
loudness + true peak of the muxed audio, and an overview contact sheet (4 frames / s) for the director's eyes.
Writes <out>/report.md and <out>/overview.jpg; prints a one-line verdict."""
import os, sys, json, argparse, subprocess
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from src import timeline as TL

FF = os.path.expanduser('~/.local/bin/ffmpeg')
FP = os.path.expanduser('~/.local/bin/ffprobe')
# intended holds / pure-white moments (film frames), not errors
HOLDS = [(396, 432, 'BREATH last beat: black + leader tip'), (1566, 1584, 'STOP freeze'), (1600, 1728, 'poster hold')]


def probe(path):
    p = subprocess.run([FP, '-v', 'error', '-count_frames', '-show_streams', '-show_format', '-of', 'json', path],
                       capture_output=True, text=True, check=True)
    return json.loads(p.stdout)


def frames_small(path, w=192, h=108):
    p = subprocess.run([FF, '-v', 'error', '-i', path, '-vf', f'scale={w}:{h}:flags=area,format=rgb24', '-f', 'rawvideo', '-'],
                       capture_output=True, check=True)
    return np.frombuffer(p.stdout, np.uint8).reshape(-1, h, w, 3).astype(np.float32) / 255.0


def rel_lum(img):
    c = np.where(img <= 0.04045, img / 12.92, ((img + 0.055) / 1.055) ** 2.4)
    return c[..., 0] * 0.2126 + c[..., 1] * 0.7152 + c[..., 2] * 0.0722


def in_hold(i):
    return next((n for a, b, n in HOLDS if a <= i < b), None)


def flashes(lum):
    """per-frame general flash transitions: relative luminance change >= 0.1 on >= 25 % of the frame where the darker
    state is < 0.8; a flash = a pair of opposing transitions. Returns list of frame indices of transitions (signed)."""
    ev = []
    for i in range(1, len(lum)):
        d = lum[i] - lum[i - 1]
        dark = np.minimum(lum[i], lum[i - 1]) < 0.8
        up = ((d >= 0.1) & dark).mean(); dn = ((d <= -0.1) & dark).mean()
        if up >= 0.25:
            ev.append((i, +1))
        elif dn >= 0.25:
            ev.append((i, -1))
    return ev


def loudness(path):
    p = subprocess.run([FF, '-hide_banner', '-nostats', '-i', path, '-map', '0:a:0', '-af', 'ebur128=peak=true', '-f', 'null', '-'],
                       capture_output=True, text=True)
    txt = p.stderr
    I = TP = None
    for line in txt.splitlines()[::-1]:
        s = line.strip()
        if I is None and s.startswith('I:') and 'LUFS' in s:
            I = float(s.split()[1])
        if TP is None and s.startswith('Peak:') and 'dBFS' in s:
            TP = float(s.split()[1])
    return I, TP


def overview(path, out, fps=4, cols=8):
    n = int(TL.END / TL.FPS * fps)
    rows = int(np.ceil(n / cols))
    vf = (f"fps={fps},scale=320:-2,drawtext=fontfile=/System/Library/Fonts/SFNSMono.ttf:text='%{{pts\\:hms}}':"
          f"x=6:y=6:fontsize=14:fontcolor=white:box=1:boxcolor=black@0.55,tile={cols}x{rows}:padding=4:color=0xF3F2EE")
    subprocess.run([FF, '-v', 'error', '-y', '-i', path, '-vf', vf, '-frames:v', '1', '-q:v', '3', out], check=True)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('film'); ap.add_argument('--out')
    a = ap.parse_args()
    global TL, HOLDS
    name = os.path.splitext(os.path.basename(a.film))[0]
    out = a.out or f'{ROOT}/out/qc/{name}'
    os.makedirs(out, exist_ok=True)
    rep, fails, warns = [], [], []
    info = probe(a.film)
    v = next(s for s in info['streams'] if s['codec_type'] == 'video')
    au = [s for s in info['streams'] if s['codec_type'] == 'audio']
    nf = int(v.get('nb_read_frames', 0)); dur = float(info['format']['duration'])
    rep.append(f"- 视频：{v['codec_name']} {v.get('profile')} {v['width']}×{v['height']} {v['pix_fmt']} {v['r_frame_rate']} fps，"
               f"{nf} 帧，{dur:.3f} s，总码率 {int(info['format']['bit_rate']) / 1e6:.1f} Mbps，"
               f"色彩 {v.get('color_primaries')}/{v.get('color_transfer')}/{v.get('color_space')}")
    if (v['width'], v['height']) != (1920, 1080): fails.append('分辨率不是 1920×1080')
    if v['r_frame_rate'] not in ('60/1',): fails.append(f"帧率 {v['r_frame_rate']} 不是 60")
    if nf != TL.END: fails.append(f'帧数 {nf} ≠ {TL.END}')
    if au:
        s = au[0]
        rep.append(f"- 音频：{s['codec_name']} {s.get('sample_rate')} Hz {s.get('channels')} ch，{int(s.get('bit_rate', 0)) / 1e3:.0f} kbps")
        I, TP = loudness(a.film)
        rep.append(f'- 响度（成片内 AAC）：{I} LUFS，峰值 {TP} dBFS')
        if I is None or abs(I + 14) > 1.5: warns.append(f'响度 {I} LUFS 偏离 −14')
        if TP is not None and TP > -0.5: warns.append(f'峰值 {TP} dBFS 偏高')
    else:
        fails.append('没有音轨')
    fr = frames_small(a.film)
    lum = rel_lum(fr)
    mean = lum.mean((1, 2))
    black = [i for i in range(len(fr)) if mean[i] < 0.01 and not in_hold(i)]
    white = [i for i in range(len(fr)) if fr[i].min() > 0.985 and not in_hold(i)]
    same = [i for i in range(1, len(fr)) if np.abs(fr[i] - fr[i - 1]).max() < 1.5 / 255 and not in_hold(i)]
    runs, cur = [], []
    for i in same:
        if cur and i == cur[-1] + 1: cur.append(i)
        else:
            if len(cur) >= 3: runs.append((cur[0] - 1, cur[-1]))
            cur = [i]
    if len(cur) >= 3: runs.append((cur[0] - 1, cur[-1]))
    rep.append(f'- 黑帧（保留段外）：{black[:20] or "无"}')
    rep.append(f'- 纯白帧（保留段外）：{white[:20] or "无"}')
    rep.append(f'- 冻结 ≥4 帧的段（保留段外）：{runs or "无"}')
    if black: fails.append(f'黑帧 {len(black)} 个')
    if runs: warns.append(f'冻结段 {runs}')
    ev = flashes(lum)
    worst, worst_at = 0, None
    for i, _ in ev:
        win = [e for e in ev if i <= e[0] < i + TL.FPS]
        pairs = sum(1 for k in range(1, len(win)) if win[k][1] != win[k - 1][1])
        if pairs > worst: worst, worst_at = pairs, i
    rep.append(f'- 闪烁：大面积亮度跳变 {len(ev)} 次；任意 1 s 内最多 {worst} 次往返（{worst_at} 帧起）；阈值 3')
    # 不用管光敏 -> flash count is informational only (no FAIL/WARN)
    overview(a.film, f'{out}/overview.jpg')
    verdict = 'FAIL' if fails else ('WARN' if warns else 'PASS')
    md = [f'# QC · {os.path.basename(a.film)}', '', f'**结论：{verdict}**', ''] + rep + ['', '## FAIL'] + [f'- {x}' for x in fails or ['无']] + \
         ['', '## WARN'] + [f'- {x}' for x in warns or ['无']] + ['', f'总览图：`{out}/overview.jpg`（每秒 4 格）']
    open(f'{out}/report.md', 'w').write('\n'.join(md) + '\n')
    print(f'{verdict}  fails={fails}  warns={warns}  report={out}/report.md')


if __name__ == '__main__':
    main()
