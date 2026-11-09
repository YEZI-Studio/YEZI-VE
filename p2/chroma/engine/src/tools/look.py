# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""Look grid: source frames x look variants, side by side (tuning the signature filter per clip; the director picks).

  .venv/bin/python -m src.tools.look --src T2@2.40 T6@1.2 --tier 2160 --v plate storm "storm|glow=0.8,lc0=0.08" mask \\
      --out out/look/x.jpg [--cell 960] [--full out/look/full] [--label "..."]
  (old form still works: python -m src.tools.look T2 2.40 --v ...)
A variant is 'LOOK' or 'LOOK|key=value,...' (keys of fx.volt.DEFAULT; tuples as a:b, e.g. hue=195:330; strings stay strings,
e.g. ramp=plasma); 'mask' shows the mask of the previous variant (R electric, G aurora green, B aurora red), 'plate' the
ungraded plate, 'plasma' the plasma false colour. --fx heat switches to IGNITION's heat filter (fx.heat).
Rows = sources, columns = variants (cells 16:9, labelled). --full DIR also writes every graded cell at 1920x1080.
--stats appends the near-white share (display luma >= 0.9; keep it <= 15 %) and the mean luma."""
import os, sys, argparse
import numpy as np
import cv2
from src.engine import Engine
from src.footage import resolve, probe, Store


def parse(v):
    look, _, kv = v.partition('|')
    kw = {}
    for item in filter(None, kv.split(',')):
        k, _, x = item.partition('=')
        k = k.strip()
        if ':' in x:
            try:
                kw[k] = tuple(float(y) for y in x.split(':'))
            except ValueError:
                kw[k] = tuple(y.strip() for y in x.split(':'))
        else:
            try:
                kw[k] = float(x)
            except ValueError:
                kw[k] = x.strip()
    return look.strip(), kw


def to_img(t, w, h):
    img = (np.clip(t.read()[..., :3], 0, 1) * 255 + 0.5).astype(np.uint8)
    if (img.shape[1], img.shape[0]) != (w, h):
        img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
    return img[..., ::-1].copy()


def white_share(t):
    """(share of pixels with display luma >= 0.9, mean luma) of a texture -- the 「大半屏近白」 check"""
    a = np.clip(t.read()[..., :3], 0, 1)
    y = a @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    return float((y >= 0.9).mean()), float(y.mean())


def label(img, s, scale=0.62):
    cv2.putText(img, s[:96], (12, 28), cv2.FONT_HERSHEY_SIMPLEX, scale, (30, 30, 30), 4, cv2.LINE_AA)
    cv2.putText(img, s[:96], (12, 28), cv2.FONT_HERSHEY_SIMPLEX, scale, (245, 245, 245), 1, cv2.LINE_AA)


def plate_1080(E, key, i, tier):
    g = E.clip(key, i, tier=tier)
    if g.w != E.W:                                     # 2160p / 1440p plate -> 1080p (trilinear)
        E.mips(g)
        g = E.pass_('uniform sampler2D a; void main(){ o = vec4(texture(a, UV()).rgb, 1.0); }', {'a': g})
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pos', nargs='*', help='old form: CLIP T')
    ap.add_argument('--src', nargs='+', default=[], help='CLIP@SEC ...')
    ap.add_argument('--tier', type=int, default=1080)
    ap.add_argument('--v', nargs='+', default=['plate', 'neutral', 'storm', 'mask'])
    ap.add_argument('--fx', default='volt', choices=('volt', 'heat'))
    ap.add_argument('--cell', type=int, default=960)
    ap.add_argument('--full', default=None)
    ap.add_argument('--names', nargs='*', default=None, help='labels for the columns (default: the variant strings)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--stats', action='store_true', help='append near-white share and mean luma to each label')
    a = ap.parse_args()
    if os.path.exists(a.out):
        raise SystemExit(f'{a.out} exists')
    srcs = list(a.src)
    if a.pos:
        srcs.append(f'{a.pos[0]}@{a.pos[1]}')
    if a.fx == 'volt':
        from src.fx import volt as VT
        FX, fx_mask, LOOKS = VT.volt, VT.volt_mask, VT.LOOKS
    else:
        from src.fx import heat as HT
        FX, fx_mask, LOOKS = HT.heat, HT.heat_mask, HT.LOOKS
    st = Store()
    keys = []
    for s in srcs:
        c, _, t = s.rpartition('@')
        key = resolve(c); i = int(round(float(t) * probe(key)['fps']))
        st.ensure(key, i, i + 1, a.tier)
        keys.append((key, i, s))
    E = Engine(1, store=st)
    cw, ch = a.cell, a.cell * 9 // 16
    rows = []
    for key, i, s in keys:
        cells, last_kw = [], {}
        for j, v in enumerate(a.v):
            g = plate_1080(E, key, i, a.tier)
            look, kw = parse(v)
            if look == 'plate':
                t = g
            elif look == 'mask':
                t = fx_mask(E, g, **last_kw)
            elif look == 'plasma' and a.fx == 'volt':
                from src.fx import volt as VT
                t = VT.plasma(E, g, **kw)
            else:
                last_kw = dict(LOOKS.get(look, {}), **kw)
                t = FX(E, g, **last_kw)
            img = to_img(t, cw, ch)
            name = a.names[j] if a.names and j < len(a.names) else v
            if a.stats and look != 'mask':
                ws, ym = white_share(t)
                name = f'{name}  [white {100 * ws:.1f} %, mean {ym:.2f}]'
            label(img, f'{s}  {name}' if j == 0 else name, 0.62 * cw / 960)
            cells.append(img)
            if a.full and look not in ('plate', 'mask'):
                os.makedirs(a.full, exist_ok=True)
                stem = os.path.splitext(os.path.basename(a.out))[0]
                fn = f'{a.full}/{stem}_{s.replace("@", "_")}_{j}_{look}.jpg'
                if not os.path.exists(fn):
                    cv2.imwrite(fn, to_img(t, E.W, E.H), [cv2.IMWRITE_JPEG_QUALITY, 95])
            E.end_subframe()
        rows.append(np.hstack(cells))
    grid = np.vstack(rows)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    cv2.imwrite(a.out, grid, [cv2.IMWRITE_JPEG_QUALITY, 92])
    print(a.out)


if __name__ == '__main__':
    main()
