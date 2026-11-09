#!/usr/bin/env python3
# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""引擎 API 索引（试跑时一镜首轮翻源码 24 次、series.py 14 次——函数名、参数、默认值）。
从引擎源码（ast，不导入、不跑引擎）生成一份按名字查的索引（markdown，一行一个，不带说明：用法在手册里）：
  1. 镜头代码能调的函数：SR / E / TY / HE / fx / KV / MO / LT / Take，每个一行签名（参数和默认值照源码）。
     给了 keep（手册 + 示例的原文）就只列里面点过名的（全列 68 个 SR 函数带文档有 34k 字，比任务书删掉的还多，还把
     手册说「不对镜头开放」「别用」的、别的母题的零件摆成平级的菜单）；手册里写着「不对镜头开放 / 别用」的那一句点名的不列。
     源码文档不输出（英文、截断，还带别的片子的出处注和别的母题的词；11-09 复核：代理按名字查签名就够，整份不用读）；
  2. 转场窗口：SR.tx(F, kind) 在 [F − n_out, F + n_in) 里两镜都要渲（n_out 帧伸进上一镜：occlude 这类窗口会盖住上一镜的尾巴），一行；


编排器（lib/stages.prepare_ctx）每集生成一次，复制成每镜的 ctx/API.md。只用标准库，Python 3.9 起能跑。"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys

# (别名, 文件, 说明, 只取哪个类的方法)：镜头代码里的写法（template.py / ex_*.py 的 import）
MODS = [("SR", "fx/series.py", "系列零件：冲击簇、转场、跟拍、叠层、画面自碎", None),
        ("E", "engine/core.py", "引擎（镜头函数的第一个参数 E）", "Engine"),
        ("TY", "kit/type.py", "字", None),
        ("HE", "kit/hero.py", "主角字一生", None),
        ("fx", "kit/fx.py", "相机、模糊、调色", None),
        ("KV", "fx/kaleido.py", "万花筒", None),
        ("MO", "fx/motif.py", "母题调色", None),
        ("LT", "fx/light.py", "光效融合（from src.fx import light as LT）", None),
        ("Take", "footage.py", "实拍素材（from src.footage import Take）", "Take")]
SKIP_FN = {"main"}


def _parse(path):
    try:
        with open(path, encoding="utf-8") as f:
            return ast.parse(f.read())
    except (OSError, SyntaxError, ValueError):
        return None


def _sig(fn, drop_self=False):
    a = fn.args
    pos = list(a.posonlyargs) + list(a.args)
    if drop_self and pos and pos[0].arg in ("self", "cls"):
        pos = pos[1:]
    dflt = [None] * (len(pos) - len(a.defaults)) + list(a.defaults) if len(a.defaults) <= len(pos) else list(a.defaults[-len(pos):])
    out = []
    for p, d in zip(pos, dflt):
        out.append(p.arg if d is None else f"{p.arg}={_src(d)}")
    if a.vararg:
        out.append("*" + a.vararg.arg)
    elif a.kwonlyargs:
        out.append("*")
    for p, d in zip(a.kwonlyargs, a.kw_defaults):
        out.append(p.arg if d is None else f"{p.arg}={_src(d)}")
    if a.kwarg:
        out.append("**" + a.kwarg.arg)
    return ", ".join(out)


def _src(node, n=40):
    try:
        s = ast.unparse(node)
    except Exception:  # noqa: BLE001
        s = "…"
    return s if len(s) <= n else s[:n - 1] + "…"


def functions(src_dir):
    """[(别名, 说明, [(名字, 签名)])]。"""
    out = []
    for alias, rel, what, cls in MODS:
        tree = _parse(os.path.join(src_dir, rel))
        if tree is None:
            continue
        rows = []
        if cls:
            for n in tree.body:
                if isinstance(n, ast.ClassDef) and n.name == cls:
                    for m in n.body:
                        if isinstance(m, ast.FunctionDef) and (m.name == "__init__" or not m.name.startswith("_")):
                            nm = cls if m.name == "__init__" else m.name
                            rows.append((nm, _sig(m, drop_self=True)))
        else:
            for n in tree.body:
                if isinstance(n, ast.FunctionDef) and not n.name.startswith("_") and n.name not in SKIP_FN:
                    rows.append((n.name, _sig(n)))
                elif isinstance(n, ast.ClassDef) and not n.name.startswith("_"):
                    init = next((m for m in n.body if isinstance(m, ast.FunctionDef) and m.name == "__init__"), None)
                    if init is not None:
                        rows.append((n.name, _sig(init, drop_self=True)))
        out.append((alias, what, rows))
    return out


def tx_windows(src_dir):
    """fx/series.py 的 TX_N / TR_N：{kind: (n_out, n_in)}。"""
    tree = _parse(os.path.join(src_dir, "fx", "series.py"))
    out = {}
    for n in (tree.body if tree else []):
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ("TX_N", "TR_N") for t in n.targets):
            v = n.value
            if isinstance(v, ast.Call) and getattr(v.func, "id", "") == "dict":
                for k in v.keywords:
                    try:
                        out[k.arg] = tuple(ast.literal_eval(k.value))
                    except ValueError:
                        pass
            elif isinstance(v, ast.Dict):
                try:
                    out.update({str(k): tuple(x) for k, x in ast.literal_eval(v).items()})
                except ValueError:
                    pass
    return out


BAN_WORDS = ("不对镜头开放", "别用")


def mentioned(text):
    """手册 / 示例原文里点过名的 {'SR.track', 'Take', …}，减去「不对镜头开放」「别用」那一句点名的。"""
    pat = re.compile(r"\b(" + "|".join(a for a, _, _, _ in MODS if a != "Take") + r")\.([A-Za-z_]\w*)")
    names = {f"{a}.{n}" for a, n in pat.findall(text or "")} | ({"Take"} if re.search(r"\bTake\(", text or "") else set())
    for sent in re.split(r"[；。\n]", text or ""):
        if any(w in sent for w in BAN_WORDS):
            names -= {f"{a}.{n}" for a, n in pat.findall(sent)}
    return names


def build(src_dir, keep=None):
    """生成 markdown（API.md 的全文）。keep = 手册 + 示例的原文：只列里面点过名的函数（None = 全列）。"""
    use = mentioned(keep) if keep is not None else None
    L = ["# 引擎 API 索引（从这一集引擎源码生成）：函数签名和默认值、转场窗口。用法看手册；"
         "按名字搜（例 `rg -n 'SR.track' ctx/API.md`），不用整份读", ""]
    for alias, what, rows in functions(src_dir):
        if use is not None:
            rows = [r for r in rows if (r[0] if alias == "Take" else f"{alias}.{r[0]}") in use]
        if rows:
            L += [f"## {alias} — {what}"] + [f"- `{nm if alias == 'Take' and nm == 'Take' else alias + '.' + nm}({sig})`" for nm, sig in rows] + [""]
    tw = tx_windows(src_dir)
    if tw:
        L += ["## SR.tx 转场窗口 kind n_out/n_in（[F − n_out, F + n_in) 两镜都要渲；F = 下一镜第一帧，n_out 帧盖住上一镜的尾巴）",
              " · ".join(f"{k} {v[0]}/{v[1]}" for k, v in sorted(tw.items())), ""]
    return "\n".join(L).rstrip() + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="从引擎源码生成 API 索引（markdown）")
    ap.add_argument("src", help="引擎 src 目录（…/engine/src）")
    ap.add_argument("--keep", nargs="*", help="手册 / 示例文件：只列里面点过名的函数（不给 = 全列）")
    ap.add_argument("--out", help="写到这个文件（默认打印）")
    a = ap.parse_args(argv)
    keep = None
    if a.keep:
        keep = ""
        for p in a.keep:
            with open(p, encoding="utf-8") as f:
                keep += f.read() + "\n"
    md = build(a.src, keep)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(md)
    else:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
