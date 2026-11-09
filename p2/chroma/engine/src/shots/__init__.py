# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""Shot registry = src/timeline.py SHOTS -> src/shots/<module>.<name>. Any function can also be rendered directly with
render.py --fn module:function (style frames, tests)."""
import importlib
from src import timeline as TL


def row(name):
    return [r for r in TL.SHOTS if r[0] == name][0]


def find(F):
    for r in TL.SHOTS:
        if r[2] <= F < r[3]:
            return r
    return TL.SHOTS[-1] if F >= TL.END else TL.SHOTS[0]


def impl(name):
    r = row(name)
    mod = importlib.import_module(f'src.shots.{r[1]}')
    return getattr(mod, name), r


def resolve(F):
    return impl(find(F)[0])


def load_fn(spec, start=0, end=None):
    """'module:function' (e.g. src.shots.demo:demo) -> (function, row)"""
    m, fn = spec.split(':')
    f = getattr(importlib.import_module(m), fn)
    return f, (fn, m, int(start), int(end if end is not None else getattr(f, 'length', TL.END)), 'fn')
