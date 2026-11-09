# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""写代码的环节（关键帧、制作、修复）：每个镜头一次独立的 headless Claude Code（DeepSeek 后端）。
空 MCP、关技能、--permission-prompts none；key 只放进 claude 子进程的环境变量（沙箱内命令看不到），结束后把
运行目录里出现的 key 原文替换成 <REDACTED>。另外两点：
  1. 工具只给 Bash/Read/Edit/Write（一镜一个小任务，不要子代理）；不附加全局 CLAUDE.md（上下文只给这一镜要的）。
  2. 编排器边跑边读 stream.jsonl：按 usage 现算费用，超出剩余预算或超过轮数上限就结束进程。"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time

from . import llm, util

TOOLS_ALLOWED = ["Bash", "Read", "Edit", "Write"]
COMMON_ENV = {
    "ENABLE_TOOL_SEARCH": "false", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "DISABLE_AUTOUPDATER": "1",
    "DISABLE_ERROR_REPORTING": "1", "DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1",
}
ENV_PASSTHROUGH = ("HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "__CF_USER_TEXT_ENCODING")
BASH_DENY = [
    "git push:*", "git remote:*", "curl:*", "wget:*", "npx:*", "npm install:*", "npm i:*", "npm add:*",
    "pnpm:*", "yarn:*", "pip install:*", "pip3 install:*", "uv pip:*", "brew:*", "sudo:*",
    "security:*", "/usr/bin/security:*", "ssh:*", "scp:*", "rsync:*", "open:*", "osascript:*",
    "launchctl:*", "defaults write:*", "claude:*", "codex:*", "env", "printenv:*",
    "rm -rf /:*", "rm -rf ~:*", "rm -rf $HOME:*", "rm -fr /:*", "rm -Rf /:*",
]


def make_settings(allow_read, allow_write, readonly_dirs):
    deny = ["WebFetch", "WebSearch"] + [f"Bash({c})" for c in BASH_DENY]
    deny += [f"Edit(/{d}/**)" for d in readonly_dirs]
    return {
        "permissions": {"defaultMode": "acceptEdits", "blockReadsOutsideWorkingDirectories": True,
                        "allow": ["Bash"], "deny": deny},
        "disableAllHooks": True, "autoMemoryEnabled": False,
        "sandbox": {
            "enabled": True, "failIfUnavailable": True, "autoAllowBashIfSandboxed": True,
            "allowUnsandboxedCommands": False,
            "network": {"allowedDomains": [], "strictAllowlist": True, "allowLocalBinding": True},
            "filesystem": {"allowRead": list(allow_read), "allowWrite": list(allow_write)},
            "credentials": {"envVars": [{"name": "ANTHROPIC_AUTH_TOKEN", "mode": "deny"},
                                        {"name": "ANTHROPIC_API_KEY", "mode": "deny"}]},
        },
    }


def build_cmd(ac, settings_path, mcp_path, prompt):
    cmd = [os.path.realpath(os.path.expanduser(ac["claude_bin"])), "-p", prompt,
           "--output-format", "stream-json", "--verbose",
           "--model", ac["cli_model"], "--setting-sources", "project", "--settings", settings_path,
           "--strict-mcp-config", "--mcp-config", mcp_path, "--disable-slash-commands",
           "--permission-prompts", "none", "--no-session-persistence", "--no-chrome",
           "--tools", ",".join(TOOLS_ALLOWED), "--disallowedTools", "WebFetch,WebSearch",
           "--permission-mode", "acceptEdits"]
    if ac.get("autocompact"):
        cmd += ["--autocompact", str(ac["autocompact"])]
    if ac.get("effort"):
        cmd += ["--effort", ac["effort"]]
    return cmd


def shown(cmd):
    out = list(cmd)
    out[2] = f"<TASK.md，{len(cmd[2])} 字符>"
    return out


# ----------------------------------------------------------------------------- 进程
def _ps():
    r = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,command="], capture_output=True, text=True)
    rows = []
    for ln in r.stdout.splitlines():
        p = ln.strip().split(None, 2)
        if len(p) >= 2 and p[0].isdigit() and p[1].isdigit():
            rows.append((int(p[0]), int(p[1]), p[2] if len(p) > 2 else ""))
    return rows


def _desc(pid, rows):
    kids = {}
    for p, pp, _ in rows:
        kids.setdefault(pp, []).append(p)
    out, st = [], [pid]
    while st:
        for c in kids.get(st.pop(), []):
            out.append(c)
            st.append(c)
    return out


def _kill(pids, grace=8.0):
    pids = [p for p in dict.fromkeys(pids) if p > 1 and p not in (os.getpid(), os.getppid())]
    for p in pids:
        try:
            os.kill(p, signal.SIGTERM)
        except OSError:
            pass
    end = time.time() + grace
    while time.time() < end:
        if not any(_alive(p) for p in pids):
            return
        time.sleep(0.3)
    for p in pids:
        try:
            os.kill(p, signal.SIGKILL)
        except OSError:
            pass


def _alive(p):
    try:
        os.kill(p, 0)
        return True
    except OSError:
        return False


def _kill_tree(pid):
    _kill([pid] + _desc(pid, _ps()))


def _sweep(work):
    rows = _ps()
    hits = [p for p, _, c in rows if work in c and p != os.getpid()]
    allp = []
    for p in hits:
        allp += [p] + _desc(p, rows)
    if allp:
        _kill(allp, grace=4.0)
    return len(allp)


def _redact(root, key):
    if not key or len(key) < 8:
        return 0
    kb, hits = key.encode(), 0
    for dp, _, fs in os.walk(root):
        for f in fs:
            p = os.path.join(dp, f)
            try:
                if os.path.islink(p) or os.path.getsize(p) > 64 << 20:
                    continue
                with open(p, "rb") as fh:
                    data = fh.read()
                if kb in data:
                    hits += data.count(kb)
                    with open(p, "wb") as fh:
                        fh.write(data.replace(kb, b"<REDACTED>"))
            except OSError:
                pass
    return hits


class StreamWatch:
    """增量读 stream-json：按 message id 去重累加 usage，数 assistant 轮数，留住 result 事件。"""

    def __init__(self, path):
        self.path, self.pos, self.buf = path, 0, b""
        self.msgs, self.result, self.tools = {}, None, {}

    def poll(self):
        try:
            with open(self.path, "rb") as f:
                f.seek(self.pos)
                chunk = f.read()
                self.pos = f.tell()
        except OSError:
            return
        self.buf += chunk
        *lines, self.buf = self.buf.split(b"\n")
        for ln in lines:
            ln = ln.strip()
            if not ln.startswith(b"{"):
                continue
            try:
                ev = json.loads(ln)
            except ValueError:
                continue
            if ev.get("type") == "assistant":
                m = ev.get("message") or {}
                self.msgs[m.get("id") or f"n{len(self.msgs)}"] = m.get("usage") or {}
                for b in m.get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        self.tools[b.get("name")] = self.tools.get(b.get("name"), 0) + 1
            elif ev.get("type") == "result":
                self.result = ev

    def usage(self):
        tot = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        mu = (self.result or {}).get("modelUsage") or {}
        if mu:
            for v in mu.values():
                tot["input_tokens"] += int(v.get("inputTokens") or 0)
                tot["output_tokens"] += int(v.get("outputTokens") or 0)
                tot["cache_read_input_tokens"] += int(v.get("cacheReadInputTokens") or 0)
                tot["cache_creation_input_tokens"] += int(v.get("cacheCreationInputTokens") or 0)
            return tot
        for u in self.msgs.values():
            for k in tot:
                try:
                    tot[k] += int(u.get(k) or 0)
                except (TypeError, ValueError):
                    pass
        return tot


# ----------------------------------------------------------------------------- 主接口
def _wait(proc, watch, ledger, left, job, t0):
    """等代理跑完；超预算 / 轮数 / 时长，或 util.HALT（Ctrl-C）就杀进程树。返回 None 或停的原因。"""
    while proc.poll() is None:
        time.sleep(2)
        watch.poll()
        nu_usd, _ = ledger.price(llm_usage(watch.usage()))
        status = None
        if util.HALT.is_set():
            status = "interrupted"
        elif left is not None and nu_usd > left:
            status = "budget"
        elif len(watch.msgs) > job["max_turns"]:
            status = "max_turns"
        elif time.time() - t0 > job["timeout_s"]:
            status = "timeout"
        if status:
            _kill_tree(proc.pid)
            return status
    return None


def run_agent(job, cfg, ledger, log, mock_fn=None):
    """job: stage name work run_dir task allow_read allow_write readonly expect timeout_s max_turns。
    返回 {status: ok|missing_output|timeout|max_turns|budget|error, usd, turns, outputs, run_dir}。"""
    ac = cfg["agent"]
    run_dir, work = job["run_dir"], job["work"]
    left = ledger.left()
    if left is not None and left <= 0:
        raise util.Stop(3, "budget", f"预算用完（已花 ${ledger.total:.4f}），{job['stage']}/{job['name']} 没有开始。")
    os.makedirs(os.path.join(run_dir, "harness"), exist_ok=True)
    if not job["task"].startswith("# 本镜须知"):                       # 每镜须知（ctx/AI_USAGE.md + TASK.md 开头）
        job["task"] = util.shot_notice(work) + job["task"]
    util.write(os.path.join(work, "TASK.md"), job["task"])
    prompt = "下面是你的任务（同一内容也在工作目录的 TASK.md）。\n\n" + job["task"]
    settings = make_settings(job.get("allow_read", []), job.get("allow_write", []), job.get("readonly", []))
    sp, mp = os.path.join(run_dir, "harness", "settings.json"), os.path.join(run_dir, "harness", "mcp.json")
    util.write_json(sp, settings)
    util.write_json(mp, {"mcpServers": {}})
    cmd = build_cmd(ac, sp, mp, prompt)
    util.write_json(os.path.join(run_dir, "harness", "cmd.json"), {"cmd": shown(cmd), "cwd": work,
                                                                     "timeout_s": job["timeout_s"],
                                                                     "max_turns": job["max_turns"]})
    if mock_fn:
        usage = mock_fn(job)
        rec = ledger.add(job["stage"], job["name"], "agent", usage, mock=True)
        status = "ok" if all(os.path.exists(os.path.join(work, e)) for e in job["expect"]) else "missing_output"
        res = {"status": status, "usd": rec["usd"], "turns": 6, "mock": True, "run_dir": run_dir}
        util.write_json(os.path.join(run_dir, "meta.json"), res)
        return res

    key, _src = llm.get_key()
    if not key:
        raise util.Stop(4, "blocked", "找不到 DeepSeek key：请你运行 p2/setup_key.sh 后续跑。")
    env = {k: os.environ[k] for k in ENV_PASSTHROUGH if k in os.environ}
    env["PATH"] = util.BIN_PATH
    env.update(COMMON_ENV)
    env.update(ac.get("env") or {})
    env.update(job.get("env") or {})
    cfg_dir = os.path.join(run_dir, "cc-config")
    os.makedirs(cfg_dir, exist_ok=True)
    env["CLAUDE_CONFIG_DIR"] = cfg_dir
    env["ANTHROPIC_AUTH_TOKEN"] = key
    stream, errp = os.path.join(run_dir, "stream.jsonl"), os.path.join(run_dir, "stderr.log")
    watch = StreamWatch(stream)
    t0, status = time.time(), None
    with open(stream, "wb") as so, open(errp, "wb") as se:
        proc = subprocess.Popen(cmd, cwd=work, env=env, stdout=so, stderr=se, stdin=subprocess.DEVNULL,
                                start_new_session=True)
        env = None                                                     # 不再持有带 key 的环境
        try:
            status = _wait(proc, watch, ledger, left, job, t0)
        except BaseException:
            _kill_tree(proc.pid)
            raise
        try:
            rc = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            _kill([proc.pid], grace=1)
            rc = proc.poll()
    swept = _sweep(work)
    watch.poll()
    redactions = _redact(os.path.dirname(run_dir.rstrip("/")), key) + _redact(work, key)
    key = None
    rec = ledger.add(job["stage"], job["name"], "agent", watch.usage(),
                     extra={"turns": len(watch.msgs), "wall_s": round(time.time() - t0, 1)})
    if status is None:
        ok = all(os.path.exists(os.path.join(work, e)) for e in job["expect"])
        status = "ok" if ok else ("missing_output" if rc == 0 else "error")
    res = {"status": status, "rc": rc, "usd": rec["usd"], "turns": len(watch.msgs), "tools": watch.tools,
           "wall_s": round(time.time() - t0, 1), "swept": swept, "key_redactions": redactions,
           "final_text": ((watch.result or {}).get("result") or "")[:2000], "stderr_tail": util.tail(errp, 20)[-2000:],
           "run_dir": run_dir}
    util.write_json(os.path.join(run_dir, "meta.json"), res)
    log(f"  agent {job['stage']}/{job['name']}: {status}，{res['turns']} 轮，${rec['usd']:.4f}，{res['wall_s']}s", echo=False)
    if status == "budget":
        raise util.Stop(3, "budget", f"{job['stage']}/{job['name']} 跑到一半超出预算，已结束进程。加大 --budget-usd 后续跑。")
    return res


def llm_usage(u):
    from .cost import norm_usage
    return norm_usage(u)
