# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""够用的 JSON Schema 子集校验（标准库）：type / required / properties / additionalProperties / items /
enum / const / minItems / maxItems / minLength / maxLength / minimum / maximum / pattern。返回错误列表（中文，带路径）。"""
from __future__ import annotations

import re

_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "integer": int, "number": (int, float), "null": type(None),
}


def _is(v, t):
    if t in ("integer", "number") and isinstance(v, bool):
        return False
    if t == "integer" and isinstance(v, float):
        return v.is_integer()
    return isinstance(v, _TYPES[t])


def validate(v, s, path="$", errs=None, limit=30):
    errs = [] if errs is None else errs
    if len(errs) >= limit:
        return errs
    t = s.get("type")
    if t:
        ts = t if isinstance(t, list) else [t]
        if not any(_is(v, x) for x in ts):
            errs.append(f"{path}: 应为 {'/'.join(ts)}，实际是 {type(v).__name__}")
            return errs
    if "const" in s and v != s["const"]:
        errs.append(f"{path}: 应为 {s['const']!r}")
    if "enum" in s and v not in s["enum"]:
        errs.append(f"{path}: {v!r} 不在 {s['enum']}")
    if isinstance(v, str):
        if len(v) < s.get("minLength", 0):
            errs.append(f"{path}: 太短（至少 {s['minLength']} 字符）")
        if "maxLength" in s and len(v) > s["maxLength"]:
            errs.append(f"{path}: 太长（最多 {s['maxLength']} 字符）")
        if "pattern" in s and not re.search(s["pattern"], v):
            errs.append(f"{path}: {v!r} 不匹配 {s['pattern']}")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if "minimum" in s and v < s["minimum"]:
            errs.append(f"{path}: {v} < 最小值 {s['minimum']}")
        if "maximum" in s and v > s["maximum"]:
            errs.append(f"{path}: {v} > 最大值 {s['maximum']}")
    if isinstance(v, list):
        if len(v) < s.get("minItems", 0):
            errs.append(f"{path}: 至少 {s['minItems']} 项，实际 {len(v)}")
        if "maxItems" in s and len(v) > s["maxItems"]:
            errs.append(f"{path}: 最多 {s['maxItems']} 项，实际 {len(v)}")
        if "items" in s:
            for i, x in enumerate(v):
                validate(x, s["items"], f"{path}[{i}]", errs, limit)
    if isinstance(v, dict):
        for k in s.get("required", []):
            if k not in v:
                errs.append(f"{path}: 缺字段 {k}")
        props = s.get("properties", {})
        for k, x in v.items():
            if k in props:
                validate(x, props[k], f"{path}.{k}", errs, limit)
            elif s.get("additionalProperties") is False:
                errs.append(f"{path}: 多余字段 {k}")
            elif isinstance(s.get("additionalProperties"), dict):
                validate(x, s["additionalProperties"], f"{path}.{k}", errs, limit)
    return errs
