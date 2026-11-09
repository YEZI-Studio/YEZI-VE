# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""实拍元素叠层（SR.element）的烘焙和检查。跑在引擎根目录：

  .venv/bin/python -m src.tools.elements list                       这一集能用的 kind 和窗口（母题叠层表 assets/<母题>/overlays.json）
  .venv/bin/python -m src.tools.elements bake [--kind snow] [--force]   烘焙全部窗口（CPU，每个窗口几秒；编排器开镜头前跑一次）
  .venv/bin/python -m src.tools.elements sheet --out out/el_sheet.jpg [--kind fog]
        每个窗口一行：原片中间一帧 | 去背景后的键 | 叠在黑底上（签名色）| 叠在实拍画面上 | 100 % 局部；另写 <out>.json：
        覆盖率、键自身的运动量（96×54 灰度相邻帧平均差 ×100，和 verify/ovtest.py 同口径）
不画任何东西：键全部来自实拍。改了 overlays.json 的窗口参数，下次用到时自动重烘。"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

from src.fx import series as SR, motif as MO


def _key(kind, wi, j):
    return cv2.imread(os.path.join(SR._el_dir(kind, wi), f'{int(j):05d}.png'), cv2.IMREAD_UNCHANGED).astype(np.float32) / 65535.0


def cmd_list(a):
    for k, what, wins in SR.elements():
        print(f'{k:8s} {what}\n         windows: {", ".join(wins)}')
    exc = SR._EL.get('excluded') or {}
    for k, v in exc.items():
        print(f'(not used) {k}: {v}')


def cmd_bake(a):
    out = SR.element_bake_all([a.kind] if a.kind else None, force=a.force)
    for k, m in out.items():
        print(f"{k:10s} {m['clip']:24s} frames {m['i0']}-{m['i1']} ({m['n']} @ {m['fps']:.2f} fps)  cov {m['cov']:.3f}  p999 {m['p999']}")


def _plate(path, w, h):
    im = cv2.imread(path, cv2.IMREAD_COLOR) if path and os.path.exists(path) else None
    if im is None:
        return np.full((h, w, 3), 0.05, np.float32)
    return cv2.resize(im, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32)[..., ::-1] / 255.0


def cmd_sheet(a):
    from src import footage as FT
    P = MO.palette(a.color)
    cols = SR._el_cols(None)
    ramp = np.array([np.array(c, np.float32) for c in cols])
    tw, th = 480, 270
    world = _plate(a.world, tw, th) if a.world else None
    rows, stats = [], {}
    for kind, S in sorted(SR.element_tables().items()):
        if a.kind and kind != a.kind:
            continue
        for wi, w in enumerate(S['wins']):
            m = SR.element_bake(kind, wi)
            n = int(m['n'])
            j = n // 2
            raw = cv2.imread(FT.frame_path(m['clip'], int(m.get('tier', 1080)), m['i0'] + j), cv2.IMREAD_COLOR)
            raw = cv2.resize(raw, (1920, 1080), interpolation=cv2.INTER_AREA)[..., ::-1].astype(np.float32) / 255.0
            k = _key(kind, wi, j)
            kf = cv2.resize(k, (1920, 1080), interpolation=cv2.INTER_LINEAR)
            ks = cv2.resize(kf, (tw, th), interpolation=cv2.INTER_AREA)
            g = float(S.get('gam', 0.6))
            t = float(S.get('tint', 0.55))
            idx = np.clip(1.0 - np.clip(ks, 0, 1) ** g, 0, 1) * (len(ramp) - 1)
            i0 = np.floor(idx).astype(int).clip(0, len(ramp) - 2)
            fr = (idx - i0)[..., None]
            rc = ramp[i0] * (1 - fr) + ramp[i0 + 1] * fr
            c = (1 - t) + t * rc
            light = c * (np.clip(ks, 0, 1.5) ** 1.25)[..., None]
            glow = cv2.GaussianBlur(ks, (0, 0), 3.0)[..., None] * np.array(cols[1], np.float32) * 0.5
            lt = np.clip(light + glow * float(S.get('glow', 0.6)), 0, 1)
            ink = np.full((th, tw, 3), 0.035, np.float32)
            over_ink = 1 - (1 - ink) * (1 - lt)
            base = world if world is not None else np.full((th, tw, 3), 0.25, np.float32)
            over_w = 1 - (1 - base) * (1 - lt)
            # 100 % crop around the brightest 300x170 region of the key
            kb = cv2.blur(kf, (151, 151))
            yy, xx = np.unravel_index(int(np.argmax(kb)), kb.shape)
            x0, y0 = int(np.clip(xx - 240, 0, 1920 - 480)), int(np.clip(yy - 135, 0, 1080 - 270))
            crop = raw[y0:y0 + 270, x0:x0 + 480]
            row = np.concatenate([cv2.resize(raw, (tw, th), interpolation=cv2.INTER_AREA), np.repeat(ks[..., None], 3, -1),
                                  over_ink, over_w, crop], 1)
            row = (np.clip(row, 0, 1) * 255).astype(np.uint8)[..., ::-1].copy()
            lab = f"{kind}/{wi} {w.get('clip')} @{w.get('src')}s  key={w['key']}  cov {m['cov']:.3f}"
            cv2.putText(row, lab, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            for q, tx in enumerate(['source', 'key (alpha)', 'screen on ink', 'screen on plate', '100% crop (source)']):
                cv2.putText(row, tx, (q * tw + 8, th - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            rows.append(row)
            sm = [cv2.resize(_key(kind, wi, q), (96, 54), interpolation=cv2.INTER_AREA) for q in range(0, n)]
            mot = float(np.mean(np.abs(np.diff(np.array(sm), axis=0)))) * 100.0 if n > 1 else 0.0
            stats[f'{kind}/{wi}'] = dict(clip=w.get('clip'), file=w.get('file'), src=w.get('src'), cov=m['cov'],
                                         motion_key_x100=round(mot, 3), frames=n, fps=m['fps'])
    img = np.concatenate(rows, 0)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    cv2.imwrite(a.out, img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    with open(os.path.splitext(a.out)[0] + '.json', 'w') as fh:
        json.dump(stats, fh, indent=1, ensure_ascii=False)
    print(json.dumps(stats, indent=1, ensure_ascii=False))
    print('->', a.out)


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)
    sp.add_parser('list').set_defaults(go=cmd_list)
    s = sp.add_parser('bake'); s.add_argument('--kind'); s.add_argument('--force', action='store_true'); s.set_defaults(go=cmd_bake)
    s = sp.add_parser('sheet'); s.add_argument('--out', required=True); s.add_argument('--kind')
    s.add_argument('--color', default='#3FB8FF'); s.add_argument('--world', help='a still to screen the key over (a plate)')
    s.set_defaults(go=cmd_sheet)
    a = ap.parse_args()
    a.go(a)


if __name__ == '__main__':
    sys.exit(main())
