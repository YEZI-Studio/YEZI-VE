# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""hero    Inter Display Black (opsz 32, wght 900), caps, -0.035 em  -- the one big word of a frame (cap height 15-25 % of H)
  hero7   Inter Display Bold  (opsz 32, wght 700)                   -- second big word (rare)
  accent  Inter Display Medium (opsz 32, wght 500), TRACKED CAPS +0.12 em -- secondary phrase (no serif italic, ever)
  mono    DM Mono Medium, caps, +0.08 em                            -- data labels, >= 22 px at 1080p (MONO_MIN)
  text    Inter Text Medium (opsz 14)                               -- small sans when mono is wrong
  sc      Noto Serif SC Black (思源宋 Black)                         -- Chinese only if ever needed
  smiley    得意黑 Smiley Sans Oblique                               -- every Chinese word / label / slogan (oblique = forward)
  unbounded Unbounded Black (900), caps, -0.02 em                    -- English big words, numbers 01-07, dates (never outlined)
  jbmono    JetBrains Mono ExtraBold (800), caps, +0.12 em           -- notes / HUD / readouts, >= 22 px; CJK inside a jbmono
            or unbounded string falls back to 得意黑 (x 1.2 in jbmono), not to Inter / 思源
Allowed: solid fill (ink / paper / white / a heat colour from fx.heat), one clean outline, knockout (word reveals footage),
Not allowed: stripe / candy fills, stacked offset outlines, hard drop shadows, bevel, rainbow text, two hero words fighting.
Grid (1080p units): margin 96, 12 columns, gutter 24, baseline 8: col_x(i), snap(y).
All functions take an engine Canvas (or anything with a `.c` skia canvas); units are 1080p pixels."""
import os, functools
import skia

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FDIR = f'{ROOT}/assets/fonts'
INST = f'{ROOT}/cache/fonts'
W, H = 1920, 1080
MARGIN, COLS, GUTTER, BASE = 96, 12, 24, 8
COL_W = (W - 2 * MARGIN - (COLS - 1) * GUTTER) / COLS
MONO_MIN = 22.0

FACES = {
    'hero':    ('Inter.ttf', (('opsz', 32), ('wght', 900))),
    'hero7':   ('Inter.ttf', (('opsz', 32), ('wght', 700))),
    'text':    ('Inter.ttf', (('opsz', 14), ('wght', 500))),
    'accent':  ('Inter.ttf', (('opsz', 32), ('wght', 500))),
    'wide':    'Archivo-ExpandedBlack.ttf',
    'mono':    'DMMono-Medium.ttf',
    'sc':      'NotoSerifSC-Black.otf',
    'sc_sans': 'NotoSansSC-Black.otf',
    'smiley':    'SmileySans-Oblique.ttf',
    'unbounded': 'Unbounded-Black.ttf',
    'jbmono':    'JetBrainsMono-ExtraBold.ttf',
}
TRACK = {'hero': -0.035, 'hero7': -0.03, 'text': 0.0, 'accent': 0.12, 'wide': -0.02, 'mono': 0.08, 'sc': 0.02, 'sc_sans': 0.02,
         'smiley': 0.0, 'unbounded': -0.02, 'jbmono': 0.12}
CAPS = ('hero', 'hero7', 'wide', 'mono', 'accent', 'unbounded', 'jbmono')
FALLBACK = ('text', 'sc_sans')
# per-face fallback chains (key, size factor); faces not listed use FALLBACK at 1.0 (unchanged for the CHROMA faces)
FALLBACK_BY = {'jbmono': (('smiley', 1.2), ('sc_sans', 1.0)), 'unbounded': (('smiley', 1.0), ('sc_sans', 1.0)),
               'smiley': (('sc_sans', 1.0),)}
MONO_KEYS = ('mono', 'jbmono')                      # small data faces: never below MONO_MIN


def col_x(i):
    """left edge of grid column i (0..11)"""
    return MARGIN + i * (COL_W + GUTTER)


def snap(y):
    return round(y / BASE) * BASE


def _path(spec):
    if isinstance(spec, str):
        return f'{FDIR}/{spec}'
    fn, loc = spec
    out = f'{INST}/{os.path.splitext(fn)[0]}-' + '-'.join(f'{a}{int(v)}' for a, v in loc) + '.ttf'
    if not os.path.exists(out):
        from fontTools.ttLib import TTFont
        from fontTools.varLib import instancer
        f = TTFont(f'{FDIR}/{fn}')
        full = {a.axisTag: a.defaultValue for a in f['fvar'].axes}
        full.update(dict(loc))
        os.makedirs(INST, exist_ok=True)
        tmp = f'{out}.{os.getpid()}.tmp'
        instancer.instantiateVariableFont(f, full).save(tmp)
        os.replace(tmp, out)
    return out


@functools.lru_cache(None)
def typeface(key):
    return skia.Typeface.MakeFromFile(_path(FACES[key]))


@functools.lru_cache(4096)
def font(key, size):
    f = skia.Font(typeface(key), float(size))
    f.setSubpixel(True); f.setEdging(skia.Font.Edging.kAntiAlias); f.setHinting(skia.FontHinting.kNone)
    return f


@functools.lru_cache(None)
def _has(key, ch):
    return typeface(key).unicharToGlyph(ord(ch)) != 0


def cap_height(key, size):
    m = font(key, float(size)).getMetrics()
    return m.fCapHeight if m.fCapHeight > 0 else 0.72 * size


def size_for_cap(key, cap_px):
    """font size whose cap height is cap_px"""
    return cap_px / (cap_height(key, 100.0) / 100.0)


@functools.lru_cache(4096)
def layout(s, key, size, tracking):
    """-> (((ch, font, dx), ...), width) with per-glyph fallback and tracking (em)"""
    f = font(key, size)
    runs, x, tr = [], 0.0, tracking * size
    for ch in s:
        ff = f
        if not _has(key, ch):
            for fb in FALLBACK_BY.get(key, FALLBACK):
                fk, fs = (fb, 1.0) if isinstance(fb, str) else fb
                if _has(fk, ch):
                    ff = font(fk, size * fs); break
        runs.append((ch, ff, x)); x += ff.measureText(ch) + tr
    return tuple(runs), (x - tr if s else 0.0)


def _prep(s, key, caps):
    return s.upper() if (caps and key in CAPS) else s


def measure(s, key='hero', size=100.0, tracking=None, caps=True):
    s = _prep(s, key, caps)
    return layout(s, key, float(size), TRACK.get(key, 0.0) if tracking is None else float(tracking))[1]


def _canvas(cv):
    return cv.c if hasattr(cv, 'c') else cv


def paint(color=(1, 1, 1), alpha=1.0, stroke=None):
    p = skia.Paint(); p.setAntiAlias(True)
    p.setColor4f(skia.Color4f(float(color[0]), float(color[1]), float(color[2]), float(max(0.0, min(1.0, alpha)))))
    if stroke:
        p.setStyle(skia.Paint.kStroke_Style); p.setStrokeWidth(float(stroke)); p.setStrokeJoin(skia.Paint.kRound_Join)
    return p


def word(cv, s, x, y, size, key='hero', color=(1, 1, 1), alpha=1.0, align='left', tracking=None, stroke=None,
         scale=1.0, caps=True, soft_shadow=0.0):
    """draw s with its BASELINE at y. align left/center/right relative to x. scale = slam scale about the word centre.
    soft_shadow (0..1) = a large, low-contrast ambient shadow for legibility on footage (NOT a hard drop shadow).
    Returns (x0, y_top, x1, y_base) in canvas units (unscaled layout)."""
    if key in MONO_KEYS and size < MONO_MIN:
        size = MONO_MIN                                   # CHROMA lesson: 11-14 px labels were unreadable
    c = _canvas(cv)
    s = _prep(s, key, caps)
    tr = TRACK.get(key, 0.0) if tracking is None else float(tracking)
    runs, w = layout(s, key, float(size), tr)
    x0 = x - (w if align == 'right' else w / 2 if align == 'center' else 0.0)
    ch = cap_height(key, size)
    if alpha <= 0.002:
        return (x0, y - ch, x0 + w, y)
    c.save()
    if scale != 1.0:
        cx, cy = x0 + w / 2, y - ch / 2
        c.translate(cx, cy); c.scale(scale, scale); c.translate(-cx, -cy)
    if soft_shadow > 0:
        sp = paint((0, 0, 0), alpha * 0.35 * soft_shadow)
        sp.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, size * 0.12))
        for chh, ff, dx in runs:
            c.drawString(chh, x0 + dx, y + size * 0.03, ff, sp)
    p = paint(color, alpha, stroke)
    for chh, ff, dx in runs:
        c.drawString(chh, x0 + dx, y, ff, p)
    c.restore()
    return (x0, y - ch, x0 + w, y)


def letters(cv, s, x, y, size, key='hero', color=(1, 1, 1), align='left', tracking=None, caps=True, each=None):
    """per-letter animation: each(i, n) -> (scale, dx, dy, alpha) or None (letter hidden); scale is about the letter's centre.
    e.g. IGNITION letters slamming in one by one. Layout is the word's, so the settled word is identical to word()."""
    c = _canvas(cv)
    s = _prep(s, key, caps)
    tr = TRACK.get(key, 0.0) if tracking is None else float(tracking)
    runs, w = layout(s, key, float(size), tr)
    x0 = x - (w if align == 'right' else w / 2 if align == 'center' else 0.0)
    ch = cap_height(key, size)
    n = len(runs)
    for i, (chh, ff, dx) in enumerate(runs):
        st = each(i, n) if each else (1.0, 0.0, 0.0, 1.0)
        if st is None or st[3] <= 0.002:
            continue
        sc, ox, oy, a = st
        lw = ff.measureText(chh)
        cx, cy = x0 + dx + lw / 2 + ox, y - ch / 2 + oy
        c.save(); c.translate(cx, cy); c.scale(sc, sc); c.translate(-cx, -cy)
        c.drawString(chh, x0 + dx + ox, y + oy, ff, paint(color, a))
        c.restore()
    return (x0, y - ch, x0 + w, y)


def label(cv, lines, x, y, size=24, color=(1, 1, 1), alpha=1.0, align='left', leading=1.45, key='mono'):
    """mono data block; y = baseline of the first line. Readable size by default (24 px at 1080p, never < 22).
    key='jbmono' = JetBrains Mono ExtraBold mono labels (Chinese runs in 得意黑)."""
    out = None
    for i, ln in enumerate(lines):
        b = word(cv, ln, x, y + i * size * leading, size, key, color, alpha, align)
        out = b if out is None else (min(out[0], b[0]), out[1], max(out[2], b[2]), b[3])
    return out


def word_path(s, x, y, size, key='hero', align='left', tracking=None, caps=True):
    """the word as one skia.Path (for knockouts / masks: word reveals footage)"""
    s = _prep(s, key, caps)
    tr = TRACK.get(key, 0.0) if tracking is None else float(tracking)
    runs, w = layout(s, key, float(size), tr)
    x0 = x - (w if align == 'right' else w / 2 if align == 'center' else 0.0)
    path = skia.Path()
    for chh, ff, dx in runs:
        for g in ff.textToGlyphs(chh):
            gp = ff.getPath(g)
            if gp is not None:
                gp.offset(x0 + dx, y)
                path.addPath(gp)
    return path
