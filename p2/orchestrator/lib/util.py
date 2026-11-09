# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""公共小工具：路径、读写、日志、JSON 提取、提示词模板。纯标准库，Python 3.9+。"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sys
import threading

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))      # p2/orchestrator
P2 = os.path.dirname(HERE)                                              # p2
ROOT = os.path.dirname(P2)                                              # yezi-ve
PARENT = os.path.dirname(ROOT)                                           # 根目录 yezi-ve/ 的上一级
LOG_DIR = os.path.join(ROOT, "logs", "p2")
HOME = os.path.expanduser("~")
KEY_FILE = os.path.join(HOME, ".config", "yezi-ve", "deepseek.key")
HALT = threading.Event()                                                # Ctrl-C：并行的各镜代理看到它就收工（代理在自己的进程组里，收不到 SIGINT）
LOCK = threading.RLock()                                                # 几镜并行时：记账、日志、共享登记表的读改写都过这把锁
BIN_PATH = ":".join([os.path.join(HOME, ".local", "bin"), "/opt/homebrew/bin", "/usr/local/bin",
                     "/usr/bin", "/bin", "/usr/sbin", "/sbin"])


class Stop(Exception):
    """流程按规则停下（预算、阻塞、硬门禁）。code 是退出码。"""

    def __init__(self, code: int, status: str, msg: str):
        super().__init__(msg)
        self.code, self.status, self.msg = code, status, msg


def now():
    return dt.datetime.now().astimezone()


def iso(t=None):
    return (t or now()).isoformat(timespec="seconds")


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def write(p, text):
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, p)


def read_json(p, default=None):
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write_json(p, obj):
    write(p, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def inside(path, root):
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root + os.sep)


def tail(path, n=20):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return ""


# ----------------------------------------------------------------------------- 日志
class Log:
    """同时写 <episode>/logs/run.log 和 yezi-ve/logs/p2/<name>.log；终端只打一行摘要。"""

    def __init__(self, ep_dir, name):
        os.makedirs(os.path.join(ep_dir, "logs"), exist_ok=True)
        os.makedirs(LOG_DIR, exist_ok=True)
        self.paths = [os.path.join(ep_dir, "logs", "run.log"), os.path.join(LOG_DIR, name + ".log")]

    def __call__(self, msg, echo=True):
        line = f"[{now().strftime('%H:%M:%S')}] {msg}"
        with LOCK:
            for p in self.paths:
                with open(p, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            if echo:
                print(line, flush=True)


# ----------------------------------------------------------------------------- JSON 提取
def extract_json(text: str):
    """从模型回复里取出第一个完整 JSON 对象（容忍 ```json 围栏和前后说明）。失败抛 ValueError。"""
    return parse_json(text)[0]


def parse_json(text: str, close=False):
    """extract_json 的带计数版本，返回 (对象, 补了几个闭合符)。
    close=True（llm.py 只在 finish_reason 不是 length / max_tokens 时开）：回复干净地停在一个值的末尾（不在字符串里，最后一个字符是
    743 个输出 token，不是截断），就按栈倒序补上再解析。停在字符串中间、逗号后面的照旧抛错重问。"""
    if text is None:
        raise ValueError("空回复")
    s = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, re.S)
    if m:
        s = m.group(1)
    start = s.find("{")
    if start < 0:
        raise ValueError("回复里没有 JSON 对象")
    stack, in_str, esc = [], False, False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            stack.append(c)
        elif c in "}]":
            stack.pop()
            if not stack:
                return json.loads(s[start:i + 1]), 0
    t = s[start:].rstrip()
    if close and not in_str and re.search(r'([}\]"\d]|true|false|null)$', t):
        try:
            return json.loads(t + "".join("}" if x == "{" else "]" for x in reversed(stack))), len(stack)
        except ValueError:
            pass
    raise ValueError("JSON 对象没有闭合（可能被截断）")


# ----------------------------------------------------------------------------- 提示词模板
def md_section(text: str, heading: str) -> str:
    """取 markdown 里以 heading 开头的那一节（到同级或更高级标题为止）。heading 可写「必须做到」或「3.1」。"""
    lines = text.splitlines()
    out, level = None, None
    for ln in lines:
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            lv, title = len(m.group(1)), m.group(2).strip()
            if out is not None:
                if lv <= level:
                    break
            elif title == heading or any(title.startswith(heading + c) for c in (" ", "　", "（", "(", "：")):
                out, level = [ln], lv
                continue
        if out is not None:
            out.append(ln)
    if out is None:
        raise KeyError(f"找不到小节「{heading}」")
    return "\n".join(out).strip()


def md_items(text: str, numbers) -> str:
    """取编号列表里的若干条（全局.md 的 1–10 条）。"""
    keep = []
    for ln in text.splitlines():
        m = re.match(r"^(\d+)\.\s", ln)
        if m and int(m.group(1)) in numbers:
            keep.append(ln)
    return "\n".join(keep)


def render_prompt(template: str, vars_: dict, includes: dict) -> str:
    """{{name}} 换成变量；{{include:别名#小节}}、{{include:别名#1,5,6}}（编号条目）、{{include:别名}}（全文）换成卡片段落。
    includes = {别名: 文件路径}。没填的占位符直接报错，防止把半截提示词发出去。"""

    def inc(m):
        alias, _, sec = m.group(1).partition("#")
        path = includes.get(alias)
        if not path:
            raise KeyError(f"提示词引用了未知文件别名 {alias}")
        text = read(path)
        if not sec:
            return text.strip()
        if re.fullmatch(r"[\d,\s]+", sec):
            return md_items(text, {int(x) for x in sec.split(",") if x.strip()})
        return "\n\n".join(md_section(text, s.strip()) for s in sec.split("|"))

    out = re.sub(r"\{\{include:([^}]+)\}\}", inc, template)
    for k, v in vars_.items():
        out = out.replace("{{" + k + "}}", v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=1))
    left = sorted(set(re.findall(r"\{\{[^}]+\}\}", out)))
    if left:
        raise KeyError(f"提示词占位符没有填：{left}")
    return out


def ascii_only(s: str) -> bool:
    """屏上字全英文：允许 ASCII 和 °、×、−、–、—、→ 等符号，不许汉字/假名。"""
    return not re.search(r"[぀-ヿ㐀-鿿豈-﫿＀-￯]", s or "")


def die(msg, code=2):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def shot_notice(work):
    """每镜须知：从包根目录 AI_USAGE.md 末尾「镜头代理…须知」取原文，写成 <work>/ctx/AI_USAGE.md，
    返回要插到 TASK.md 开头的文字（读不到 AI_USAGE.md 就返回空串）。"""
    try:
        with open(os.path.join(ROOT, "AI_USAGE.md"), encoding="utf-8") as f:
            txt = f.read()
    except OSError:
        return ""
    m = re.search(r"镜头代理[^\n须]{0,6}须知.*?```\n(.*?)```", txt, re.S)
    if not m:
        return ""
    body = re.sub(r"（AI_USAGE[^）\n]*）", "", m.group(1)).strip() + "\n"
    try:
        os.makedirs(os.path.join(work, "ctx"), exist_ok=True)
        with open(os.path.join(work, "ctx", "AI_USAGE.md"), "w", encoding="utf-8") as f:
            f.write(body)
    except OSError:
        pass
    return body + "\n---\n\n"
