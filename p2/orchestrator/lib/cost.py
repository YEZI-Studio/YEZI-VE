# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""记账：每次调用的 token 与费用累加到 <episode>/cost.json，超预算就停（Stop code 3）。

价格（config.json → pricing，USD / 百万 token）：未命中输入 0.30、缓存命中 0.006、输出 1.20；错峰减半。
错峰规则：北京时间工作日 9:00–12:00、14:00–18:00 为高峰，其余为错峰。
法定节假日没建模（按高峰算，只会高估）。"""
from __future__ import annotations

import datetime as dt

from . import util

BJ = dt.timezone(dt.timedelta(hours=8))


def is_offpeak(t: dt.datetime, peak_windows) -> bool:
    b = t.astimezone(BJ)
    if b.weekday() >= 5:
        return True
    x = b.hour * 60 + b.minute
    for a, e in peak_windows:
        ah, am = map(int, a.split(":"))
        eh, em = map(int, e.split(":"))
        if ah * 60 + am <= x < eh * 60 + em:
            return False
    return True


def norm_usage(u: dict) -> dict:
    """OpenAI 兼容（prompt_cache_hit/miss_tokens）、Anthropic 兼容（input/cache_read）、Claude Code 流 → 统一三项。"""
    u = u or {}
    if "prompt_cache_hit_tokens" in u or "prompt_tokens" in u:
        hit = int(u.get("prompt_cache_hit_tokens") or 0)
        miss = int(u.get("prompt_cache_miss_tokens") if u.get("prompt_cache_miss_tokens") is not None
                   else int(u.get("prompt_tokens") or 0) - hit)
        return {"in_miss": max(0, miss), "in_hit": hit, "out": int(u.get("completion_tokens") or 0)}
    return {"in_miss": int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0),
            "in_hit": int(u.get("cache_read_input_tokens") or 0),
            "out": int(u.get("output_tokens") or 0)}


def usd(nu: dict, pr: dict, offpeak: bool) -> float:
    c = (nu["in_miss"] * pr["input_miss"] + nu["in_hit"] * pr["input_hit"] + nu["out"] * pr["output"]) / 1e6
    return c * (pr.get("offpeak_factor", 1.0) if offpeak else 1.0)


class Ledger:
    def __init__(self, path, pricing, budget_usd):
        self.path, self.pr = path, pricing
        self.d = util.read_json(path) or {"currency": "USD", "pricing": pricing, "total_usd": 0.0,
                                          "tokens": {"in_miss": 0, "in_hit": 0, "out": 0},
                                          "by_stage": {}, "calls": []}
        self.d["budget_usd"] = budget_usd          # 每次运行以命令行为准（续跑时可以加预算）
        self.save()

    @property
    def total(self):
        return self.d["total_usd"]

    @property
    def budget(self):
        return self.d["budget_usd"]

    def left(self):
        return None if self.budget is None else self.budget - self.total

    def price(self, nu, t=None):
        off = is_offpeak(t or util.now(), self.pr["peak_beijing"])
        return usd(nu, self.pr, off), off

    def guard(self, est_usd=0.0, what=""):
        if self.budget is not None and self.total + est_usd > self.budget:
            raise util.Stop(3, "budget", f"预算 ${self.budget:.4f} 不够：已花 ${self.total:.4f}，"
                                         f"下一步「{what}」预计 ${est_usd:.4f}。加大 --budget-usd 后续跑。")

    def add(self, stage, name, kind, usage_raw, mock=False, extra=None):
        with util.LOCK:
            return self._add(stage, name, kind, usage_raw, mock, extra)

    def _add(self, stage, name, kind, usage_raw, mock=False, extra=None):
        nu = norm_usage(usage_raw) if "in_miss" not in (usage_raw or {}) else dict(usage_raw)
        c, off = self.price(nu)
        rec = {"t": util.iso(), "stage": stage, "name": name, "kind": kind, "usage": nu,
               "usd": round(c, 6), "offpeak": off, "mock": mock}
        if extra:
            rec.update(extra)
        self.d["calls"].append(rec)
        self.d["total_usd"] = round(self.d["total_usd"] + c, 6)
        for k in nu:
            self.d["tokens"][k] += nu[k]
        st = self.d["by_stage"].setdefault(stage, {"usd": 0.0, "calls": 0})
        st["usd"] = round(st["usd"] + c, 6)
        st["calls"] += 1
        self.save()
        return rec

    def save(self):
        with util.LOCK:
            util.write_json(self.path, self.d)
