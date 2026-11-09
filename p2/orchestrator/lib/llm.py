# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""直连 DeepSeek API（开题、导演、审片）。两种接口：OpenAI 兼容 /chat/completions（默认）或 Anthropic 兼容 /v1/messages。
图片一律放在 user 消息里。输出要求 JSON → 抽取 → schema 校验 → 业务校验
不合格把错误发回去重试（≤ max_retries 次）。每次请求的 usage 记进 cost.json，请求/回复（图片换成文件路径）存到 calls/。

key：只在第一次真实调用时从环境变量 DEEPSEEK_API_KEY 或 ~/.config/yezi-ve/deepseek.key 读进内存；
不打印、不写日志、不落盘。mock 模式完全不读。"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request

from . import schema as S
from . import util

RETRY_HTTP = (429, 500, 502, 503, 504, -1)


def get_key():
    k = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if k:
        return k, "env:DEEPSEEK_API_KEY"
    if os.path.isfile(util.KEY_FILE):
        with open(util.KEY_FILE) as f:
            k = f.read().strip()
        if k:
            return k, "file:~/.config/yezi-ve/deepseek.key"
    return None, None


def _mime(p):
    return {".png": "image/png", ".webp": "image/webp"}.get(os.path.splitext(p)[1].lower(), "image/jpeg")


def _b64(p):
    with open(p, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _post(url, headers, body, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", **headers})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode()), time.time() - t0
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw), time.time() - t0
        except Exception:  # noqa: BLE001
            return e.code, {"raw": raw[:800]}, time.time() - t0
    except Exception as e:  # noqa: BLE001  网络错误、超时
        return -1, {"error": f"{type(e).__name__}: {e}"[:300]}, time.time() - t0


class LLM:
    def __init__(self, cfg, ledger, ep_dir, log, mock_fn=None):
        self.c = cfg["api"]
        self.ledger, self.ep, self.log = ledger, ep_dir, log
        self.mock_fn = mock_fn
        self._key = None
        self.advised = set()                                          # 这一集已经发回提醒过的建议（同一条不再为它多问一轮）
        self.calls_dir = os.path.join(ep_dir, "calls")
        os.makedirs(self.calls_dir, exist_ok=True)

    # ------------------------------------------------------------------ 请求体
    def _messages(self, system, prompt, images, history):
        kind = self.c["kind"]
        content = [{"type": "text", "text": prompt}]
        for i, (label, path) in enumerate(images, 1):
            content.append({"type": "text", "text": f"图 {i}：{label}"})
            if kind == "openai":
                content.append({"type": "image_url", "image_url": {"url": f"data:{_mime(path)};base64,{_b64(path)}"}})
            else:
                content.append({"type": "image", "source": {"type": "base64", "media_type": _mime(path),
                                                             "data": _b64(path)}})
        msgs = [{"role": "user", "content": content}] + history
        if kind == "openai":
            return [{"role": "system", "content": system}] + msgs
        return msgs

    def _body(self, system, msgs, sc, json_mode):
        b = {"model": self.c["model"], "max_tokens": sc.get("max_tokens", 8000), "messages": msgs}
        if sc.get("temperature") is not None:
            b["temperature"] = sc["temperature"]
        if sc.get("thinking"):
            b["thinking"] = {"type": sc["thinking"]}
        if self.c["kind"] == "openai":
            if json_mode:
                b["response_format"] = {"type": "json_object"}
        else:
            b["system"] = system
        return b

    def _send(self, body):
        if self._key is None:
            self._key, src = get_key()
            if not self._key:
                raise util.Stop(4, "blocked", "找不到 DeepSeek key：请你运行 p2/setup_key.sh（或设置环境变量 DEEPSEEK_API_KEY）后续跑。")
            self.log(f"DeepSeek key 来源：{src}（内容不显示）", echo=False)
        if self.c["kind"] == "openai":
            url = self.c["openai_base"].rstrip("/") + "/chat/completions"
            headers = {"authorization": "Bearer " + self._key}
        else:
            url = self.c["anthropic_base"].rstrip("/") + "/v1/messages"
            headers = {"x-api-key": self._key, "anthropic-version": "2023-06-01"}
        for wait in (5, 15, 45, None):
            st, resp, secs = _post(url, headers, body, self.c.get("timeout_s", 300))
            if st not in RETRY_HTTP or wait is None:
                return st, resp, secs
            self.log(f"API 返回 {st}，{wait} 秒后重试", echo=False)
            time.sleep(wait)

    def _reply(self, resp):
        if self.c["kind"] == "openai":
            ch = (resp.get("choices") or [{}])[0]
            return (ch.get("message") or {}).get("content") or "", ch.get("finish_reason")
        txt = " ".join(b.get("text", "") for b in resp.get("content", []) if isinstance(b, dict) and b.get("type") == "text")
        return txt, resp.get("stop_reason")

    def _record(self, name, attempt, system, prompt, images, history, reply, usage, rec, status, extra=None):
        n = len([f for f in os.listdir(self.calls_dir) if f.endswith(".json")]) + 1
        util.write_json(os.path.join(self.calls_dir, f"{n:03d}-{name}-a{attempt}.json"), {
            "name": name, "attempt": attempt, "status": status, "api": self.c["kind"], "model": self.c["model"],
            "system": system, "prompt": prompt, "images": [{"label": l, "path": os.path.relpath(p, self.ep)} for l, p in images],
            "history": history, "reply": reply, "usage": usage, "usd": rec and rec["usd"], **(extra or {})})

    # ------------------------------------------------------------------ 主接口
    def ask_json(self, stage, name, system, prompt, images=(), schema=None, check=None, ctx=None, soft=None):
        """返回 (obj, info)。check(obj) -> [错误]：工程底线，不合格发回重做，超重试上限抛 Stop(5)。
        soft(obj) -> [建议]：规则，只提醒不拦——第一次有新建议就发回改一次，之后合格的照收（取建议最少的一版），
        info["advice"] = 收下那版还剩的建议。新建议 = 这一集还没发回提醒过的、不带「只报警」的（只剩已经提醒过的、或只报警的，
        不再为它多问一轮）。"""
        sc = dict(self.c.get("defaults", {}))
        sc.update(self.c.get("stages", {}).get(stage, {}))
        retries = sc.get("max_retries", 2)
        json_mode = self.c.get("json_mode", True)
        images = list(images)
        history, last_errs = [], []
        best, asked = None, False                                     # best = (建议数, 第几次, obj, 建议)
        for attempt in range(1, retries + 2):
            est_in = (len(system) + len(prompt) + sum(len(h["content"]) if isinstance(h["content"], str) else 0
                                                      for h in history)) // 2 + 1100 * len(images)
            est = (est_in * self.ledger.pr["input_miss"] + min(sc.get("max_tokens", 8000), 4000) * self.ledger.pr["output"]) / 1e6
            try:
                self.ledger.guard(est, f"{stage}/{name}")
            except util.Stop:
                if best:                                              # 多问那次只为提醒：钱不够就收已合格的那版
                    return best[2], {"attempts": best[1], "advice": best[3]}
                raise
            if self.mock_fn:
                reply = self.mock_fn(stage, name, prompt, images, attempt, ctx or {})
                usage = {"in_miss": est_in if attempt == 1 else 200 + len(json.dumps(history, ensure_ascii=False)) // 2,
                         "in_hit": 0 if attempt == 1 else est_in, "out": len(reply) // 2}
                status, finish = 200, "stop"
                rec = self.ledger.add(stage, name, "api", usage, mock=True)
            else:
                msgs = self._messages(system, prompt, images, history)
                body = self._body(system, msgs, sc, json_mode)
                status, resp, secs = self._send(body)
                if status == 400 and json_mode and "response_format" in json.dumps(resp):
                    json_mode = False                                   # 接口不支持 JSON 模式就退回纯提示词
                    body = self._body(system, msgs, sc, json_mode)
                    status, resp, secs = self._send(body)
                usage = resp.get("usage") or {}
                rec = self.ledger.add(stage, name, "api", usage) if usage else None
                if not (isinstance(status, int) and 200 <= status < 300):
                    self._record(name, attempt, system, prompt, images, history, None, usage, rec, status,
                                 {"error": json.dumps(resp, ensure_ascii=False)[:800]})
                    raise util.Stop(5, "api_error", f"{stage}/{name}: API 返回 {status}（详情见 calls/）")
                reply, finish = self._reply(resp)
            errs = []
            obj, closed = None, 0
            cut = finish in ("length", "max_tokens")
            try:
                obj, closed = util.parse_json(reply, close=not cut)        # 没被截断、只少了收尾的 ] }：补上（真截断照旧重问）
            except ValueError as e:
                errs = [f"JSON 解析失败：{e}" + ("（输出被截断，写短一点）" if cut else "")]
            if closed:
                self.log(f"{stage}/{name} 第 {attempt} 次回复少了收尾的括号：自动补了 {closed} 个闭合符", echo=False)
            if obj is not None and schema:
                errs = S.validate(obj, schema)
            if obj is not None and not errs and check:
                errs = check(obj) or []
            adv = (soft(obj) or []) if (obj is not None and not errs and soft) else []
            if obj is not None and not errs and (best is None or len(adv) <= best[0]):
                best = (len(adv), attempt, obj, adv)
            self._record(name, attempt, system, prompt, images, history, reply, usage, rec, status,
                         {"errors": errs, "advice": adv, "finish": finish, **({"closed": closed} if closed else {})})
            fresh = [a for a in adv if "只报警" not in a and a not in self.advised]
            if not errs and (not fresh or asked or attempt > retries):
                return best[2], {"attempts": attempt, "advice": best[3]}
            if errs:
                last_errs = errs
                self.log(f"{stage}/{name} 第 {attempt} 次输出不合格：{errs[:3]}", echo=False)
                say = "上一次输出不合格：\n- " + "\n- ".join(errs[:15]) + "\n只输出修正后的完整 JSON，不要解释。"
            else:
                asked = True
                self.advised.update(fresh)
                self.log(f"{stage}/{name} 第 {attempt} 次输出能用，有 {len(fresh)} 条规则没做到，发回提醒一次：{fresh[:3]}", echo=False)
                say = ("上一次输出能用，但这几条规则没做到（只提醒，不拦：改得到就改，别的保持不变）：\n- " + "\n- ".join(fresh[:15]) +
                       "\n只输出修改后的完整 JSON，不要解释。")
            history += [{"role": "assistant", "content": reply}, {"role": "user", "content": say}]
        if best:                                                      # 后面几次反而不合格：收前面合格（只是有建议）的那版
            return best[2], {"attempts": best[1], "advice": best[3]}
        raise util.Stop(5, "schema", f"{stage}/{name} 重试 {retries} 次仍不合格：{last_errs[:5]}")
