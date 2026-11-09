# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""Codex 后端（run_episode.py --backend codex）：问答环节和写代码的代理环节改用 codex exec。

登录：只用你现有的 ChatGPT 登录（默认 CODEX_HOME = ~/.codex）。子进程环境从零搭，不放 OPENAI_API_KEY / CODEX_API_KEY /
CODEX_HOME / DEEPSEEK_API_KEY；第一次调用前查 `codex login status`，不是 ChatGPT 登录就停下等你（退出码 4），不走 API key。

每次都是一次独立的 codex exec（2026-11-03 在 codex-cli 0.166 上实测过这些参数）：
  --ephemeral（不存会话）、--ignore-user-config（不加载 ~/.codex/config.toml 里的 MCP、插件、通知钩子；模型和思考档位从
  config_codex.json 或 config.toml 顶层读出来显式传 -m / -c model_reasoning_effort）、--ignore-rules（不用用户交互时攒下的
  命令放行规则）、--disable 记忆 / 插件 / 应用 / 电脑操控 / 浏览器 / 子代理、web_search 关、子代理线程数 1、不列技能清单。
  你的全局 ~/.codex/AGENTS.md Codex 照样会读进去（关不掉；内容是项目目录规范，无害）。
  问答（开题、导演、镜头表、审片）：-s read-only，工作目录是一个临时空目录；图片用 -i 附上，图注写在正文；要求最后一条回复只放
    一个 JSON → 现有 schema 校验、业务校验和重试（llm.LLM.ask_json 原样复用；重试时把上一次输出和不合格原因拼进新的一次调用）。
  问答和代理都 --disable image_generation（内置 image_gen 只在 imagegen.generate 那一次开，生图受每镜 / 全片上限管）。
  写代码的代理（样片、逐镜、返修、代表帧）：workspace-write，工作目录 = 该镜 work/（只能写它和这一次自己的临时目录
    run_dir/tmp（TMPDIR / TMPPREFIX 指进去，跑完删）；网络关；/tmp、HOME、~/.codex 不可写），
    --json 事件流：轮数 = 非 reasoning 的 item 数，token 从 turn.completed 的 usage 累加；超轮数 / 超时就结束进程。
  读权限（11-04 收紧，config_codex.json 的 sandbox_read = "job"，默认）：不用 -s，改用一个只在这次命令行里定义的权限档
    （-c permissions.p2={...} + default_permissions="p2"）：盘根不可读（":root"="none"）、只留系统最小集（":minimal"）、
    /tmp 不可读写，再按需放行——代理：这一镜 job 的 allow_read（和 Claude Code 沙箱同一份：引擎副本、引擎素材 / 工具、
    实拍和生图目录、~/.local/bin、引擎 Python 的 venv 和每一跳软链）+ uv 的 Python 目录 + Homebrew / 系统字体 + codex 自己；
    work/ 和临时目录可写。问答：只有那个临时空目录（图用 -i 由 codex 进程自己读，不经过沙箱）。生图：work/assets/gen 可写，
    ~/.codex/generated_images 可读（把 image_gen 的图拷进来）。HOME（~/.config、~/.codex、~/.ssh……）、别的集、编排器代码、
    工作盘其他地方都读不到。sandbox_read = "all" 回到 11-03 的写法（-s，Codex 沙箱能读整个磁盘）。
记账：tokens 照记，费用记 0（ChatGPT 订阅额度，不按美元计）；预算守卫对 codex 只看轮数和时长。"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time

from . import agent as A
from . import cost, llm, util

CFG_PATH = os.path.join(util.HERE, "config_codex.json")
CODEX_HOME = os.path.join(util.HOME, ".codex")                     # 默认 CODEX_HOME：你的 ChatGPT 登录在这里（不读 auth.json）
PROMPTS = os.path.join(util.HERE, "prompts_codex")
ENV_PASS = ("HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "__CF_USER_TEXT_ENCODING")
ZERO_PRICING = {"input_miss": 0.0, "input_hit": 0.0, "output": 0.0, "offpeak_factor": 1.0, "peak_beijing": [],
                "source": "codex：你的 ChatGPT 订阅额度，只记 token，不按美元计"}
DEFAULTS = {
    "bin": "~/.local/bin/codex",
    "model": None,
    "effort": None,
    "disable": ["memories", "plugins", "apps", "multi_agent", "computer_use", "browser_use"],
    "config": ['web_search="disabled"', "agents.max_threads=1", "skills.include_instructions=false"],
    "stages": {},
    "qa": {"timeout_s": 2400, "transient_retries": 2, "retry_wait_s": [20, 90]},
    "agent": {"timeout_min": {"keyframe": 60, "shot": 90, "fix": 60}, "max_turns": {"keyframe": 200, "shot": 250, "fix": 150},
              "effort": None},
    "imagegen": {"per_shot": 6, "per_film": 40, "effort": "medium", "timeout_s_base": 420, "timeout_s_per_image": 150,
                 "followup": True},
    "sandbox_read": "job",
}
PROFILE = "p2"                                                       # 命令行里临时定义的权限档名（不写进 ~/.codex）
SYS_READ = ("/opt/homebrew", "/usr/local", "/Library/Fonts", "/System/Library/Fonts")   # 系统工具和字体（没有密钥）
HOME_READ = (os.path.join(".local", "share", "uv", "python"),)      # uv 装的 Python：venv 的软链一跳一跳都要能读，否则 exec 报 Operation not permitted
GEN_IMAGES = os.path.join(CODEX_HOME, "generated_images")             # image_gen 默认存图的地方（生图那一次要能读，好拷进 work/assets/gen）
# 额度 / 限流：codex-cli 0.166 里的原文有「Usage limit reached. You've reached your usage limit.」「Your workspace is out of credits.
# Add credits to continue」、错误码 usage_limit_reached / quota_exceeded / usage_not_included / rate_limit_exceeded（11-04 从二进制里核对）。
# 只拿 --json 事件流里的报错比（没有报错事件才看 stderr），免得 stderr 里「failed to fetch codex rate limits」这类日志被当成额度用完。
QUOTA_RE = re.compile(r"usage[ _-]?limit|rate[ _-]?limit|quota|too many requests|\b429\b|insufficient[ _](quota|credits|balance)|"
                      r"exceeded your|plan limit|out of credits|add credits|usage_not_included", re.I)


# ----------------------------------------------------------------------------- 配置
def _merge(a, b):
    out = dict(a)
    for k, v in (b or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def user_defaults(home=None):
    """~/.codex/config.toml 顶层的 model / model_reasoning_effort（只看这两行；--ignore-user-config 时要显式传）。"""
    p = os.path.join(home or CODEX_HOME, "config.toml")
    out = {}
    try:
        text = util.read(p)
    except OSError:
        return out
    for ln in text.splitlines():
        if ln.strip().startswith("["):
            break
        m = re.match(r'\s*(model|model_reasoning_effort)\s*=\s*"([^"]*)"', ln)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def load_cfg(path=None, overrides=None):
    """config_codex.json（没有就用默认）+ 命令行覆盖；model / effort 为 null 时取 ~/.codex/config.toml 的。"""
    p = path or CFG_PATH
    raw = util.read_json(p) if os.path.exists(p) else {}
    cc = _merge(DEFAULTS, {k: v for k, v in raw.items() if not k.startswith("note")})
    cc = _merge(cc, {k: v for k, v in (overrides or {}).items() if v is not None})
    ud = user_defaults()
    cc["model"] = cc.get("model") or ud.get("model") or "gpt-6-astra"
    cc["effort"] = cc.get("effort") or ud.get("model_reasoning_effort") or "high"
    cc["bin"] = os.path.expanduser(os.environ.get("CODEX_BIN") or cc["bin"])     # 装在别处时用 CODEX_BIN 指过去
    cc["_path"] = p if os.path.exists(p) else None
    return cc


def backend(c):
    return getattr(c, "backend", None) or getattr(getattr(c, "args", None), "backend", None) or "deepseek"


def clean_env(tmpdir=None):
    """给 codex 子进程的环境：从零搭，只带登录要的 HOME 等；不带任何 key、不带 CODEX_HOME（= 用默认 ~/.codex）。"""
    env = {k: os.environ[k] for k in ENV_PASS if k in os.environ}
    env.setdefault("HOME", util.HOME)
    env["PATH"] = util.BIN_PATH
    if tmpdir:
        os.makedirs(tmpdir, exist_ok=True)
        env["TMPDIR"] = tmpdir
        env["TMPPREFIX"] = os.path.join(tmpdir, "zsh")              # zsh 的 here-doc 临时文件（默认 /tmp/zsh，沙箱里 /tmp 不可写）
    return env


_LOGIN = {}


def ensure_login(cc):
    """`codex login status` 必须是 ChatGPT 登录（不读 auth.json）。不是就停下等你（退出码 4）。每个进程只查一次。"""
    if _LOGIN.get("ok"):
        return _LOGIN["ok"]
    if not os.path.exists(cc["bin"]):
        raise util.Stop(4, "blocked", f"找不到 codex（{cc['bin']}）：装好 Codex CLI 并用 ChatGPT 账号登录（codex login）后续跑")
    try:
        r = subprocess.run([cc["bin"], "login", "status"], capture_output=True, text=True, env=clean_env(), timeout=90)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:  # noqa: BLE001
        raise util.Stop(4, "blocked", f"codex login status 跑不起来：{type(e).__name__}: {str(e)[:120]}")
    if r.returncode != 0 or "chatgpt" not in out.lower():
        raise util.Stop(4, "blocked", "Codex 不是用 ChatGPT 账号登录的（" + (out.splitlines()[0][:80] if out else "无输出") +
                        "）：按约定只用你的 ChatGPT 登录、不走 API key。请你运行 codex login 选 ChatGPT 后续跑")
    _LOGIN["ok"] = out.splitlines()[0][:80]
    return _LOGIN["ok"]


def _inside(p, root):
    p, root = os.path.realpath(p), os.path.realpath(root)
    return p == root or p.startswith(root.rstrip("/") + "/")


def _too_broad(p):
    """放行读的目录不许是盘根、/Users、/Volumes、HOME 或 HOME 的上级、工作盘根（那样等于没收紧）。"""
    r = os.path.realpath(p)
    return r in ("/", "/Users", "/Volumes", "/private", os.path.realpath(util.PARENT)) or _inside(util.HOME, r)


def _bin_dirs(cc):
    """codex 自己要能被沙箱读到：它在沙箱里 exec 自己当文件系统助手（读 AGENTS.md 之类），读不到就报
    `fs sandbox helper failed … Operation not permitted`。软链所在目录 + 真身所在的 .app（没有 .app 就是真身目录）。"""
    b = cc["bin"]
    out = [os.path.dirname(b)]
    real = os.path.realpath(b)
    p = os.path.dirname(real)
    while p != "/" and not p.endswith(".app"):
        p = os.path.dirname(p)
    out.append(p if p.endswith(".app") else os.path.dirname(real))
    return out


def perm_fs(cc, write=(), read=()):
    """收紧读权限的权限档（filesystem 表）：盘根不可读、系统最小集可读、/tmp 不可读写；read 里的目录可读（在 write 里面的、
    不存在的、太宽的跳过），write 里的目录可写。路径和真身都写上（Codex 按真身比对，软链本身所在目录要另外放行，见 HOME_READ）。"""
    fs = {":root": "none", ":minimal": "read", ":slash_tmp": "none"}
    write = [w for w in write if w]
    reads = list(SYS_READ) + [os.path.join(util.HOME, x) for x in HOME_READ] + _bin_dirs(cc) + list(read or [])
    for p in reads:
        if not p or not os.path.exists(p) or _too_broad(p) or any(_inside(p, w) for w in write):
            continue
        for x in dict.fromkeys([os.path.abspath(p), os.path.realpath(p)]):
            fs.setdefault(x, "read")
    for p in write:
        for x in dict.fromkeys([os.path.abspath(p), os.path.realpath(p)]):
            fs[x] = "write"
    return fs


def perm_arg(fs):
    """-c 的值：permissions.p2={filesystem={...},network={enabled=false}}（TOML 内联表；键、值用 JSON 字符串写，转义和 TOML 一致）。"""
    q = lambda s: json.dumps(s, ensure_ascii=False)  # noqa: E731
    return f"permissions.{PROFILE}={{filesystem={{" + ",".join(f"{q(k)}={q(v)}" for k, v in fs.items()) + "},network={enabled=false}}"


def build_cmd(cc, sandbox, cwd, last_path, effort=None, images=(), schema_path=None, imagegen=False, tmpdir=None, reads=None):
    """一次 codex exec 的命令行；任务正文从 stdin 读（最后的 `-- -`）。问答不用 --output-schema（现有 schemas/ 不是 OpenAI
    严格模式要的写法：要求它把 JSON 写在最后一条回复里，再走现有校验）；schema_path 留给以后。
    imagegen = False（问答、代理）时关掉内置 image_gen：生图只走编排器的通道（受每镜 / 全片上限管），代理不能自己生图绕过上限；
    只有 imagegen.generate 那一次传 True。
    tmpdir（workspace-write 时）：这一次自己的临时目录（run_dir/tmp，跑完就删）加成可写，配合 clean_env 的
    TMPDIR / TMPPREFIX：zsh here-doc、mktemp 才能用（不给的话 `python - <<EOF`、mktemp 在沙箱里都失败）。
    /tmp、HOME、work 外照样不可写。
    reads：除了 cwd 还要能读的目录（代理 = job 的 allow_read）。sandbox_read = "job"（默认）时用权限档 perm_fs（盘根不可读，
    只放行这些），sandbox_read = "all" 时照 11-03 用 -s（Codex 沙箱能读整个磁盘）。"""
    tight = (cc.get("sandbox_read") or "job") == "job" and sandbox in ("read-only", "workspace-write")
    cmd = [cc["bin"], "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules",
           "--json", "--color", "never", "-m", cc["model"], "-c", f'model_reasoning_effort="{effort or cc["effort"]}"']
    if not tight:
        cmd += ["-s", sandbox]
    cmd += ["-C", cwd, "-o", last_path]
    for f in cc.get("disable") or []:
        if not (imagegen and f == "image_generation"):
            cmd += ["--disable", f]
    if not imagegen and "image_generation" not in (cc.get("disable") or []):
        cmd += ["--disable", "image_generation"]
    for kv in cc.get("config") or []:
        cmd += ["-c", kv]
    if tight:                                                         # 收紧读：权限档（-s 不传，免得盖掉权限档）
        ww = sandbox == "workspace-write"
        fs = perm_fs(cc, write=[cwd, tmpdir] if ww else [], read=([] if ww else [cwd]) + list(reads or []))
        cmd += ["-c", perm_arg(fs), "-c", f'default_permissions="{PROFILE}"']
    elif sandbox == "workspace-write":                               # 只能写 cwd：网络关、/tmp 和 TMPDIR 不算可写根
        cmd += ["-c", "sandbox_workspace_write.network_access=false", "-c", "sandbox_workspace_write.exclude_slash_tmp=true",
                "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true"]
        if tmpdir:
            cmd += ["--add-dir", tmpdir]
    if schema_path:
        cmd += ["--output-schema", schema_path]
    for p in images:
        cmd += ["-i", p]
    return cmd + ["--", "-"]


def prompt_file(name, **v):
    """prompts_codex/<name>.md（新提示词都放这里，不动 prompts/ 里已有的）。"""
    return util.render_prompt(util.read(os.path.join(PROMPTS, name + ".md")), v, {})


# ----------------------------------------------------------------------------- 事件流
class Events:
    """增量读 codex exec --json：item 按 id 去重计数，usage 从 turn.completed 累加，留住最后一条 agent_message 和报错。"""

    def __init__(self, path):
        self.path, self.pos, self.buf = path, 0, b""
        self.items, self.usages, self.errors = {}, [], []
        self.thread, self.final, self.done = None, None, False

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
            self.feed(ln)

    def feed(self, ln):
        ln = ln.strip() if isinstance(ln, bytes) else ln.strip().encode()
        if not ln.startswith(b"{"):
            return
        try:
            ev = json.loads(ln)
        except ValueError:
            return
        t = ev.get("type")
        if t == "thread.started":
            self.thread = ev.get("thread_id")
        elif t in ("item.started", "item.updated", "item.completed"):
            it = ev.get("item") or {}
            key = it.get("id") or f"n{len(self.items)}"
            self.items[key] = it.get("type") or "?"
            if t == "item.completed" and it.get("type") == "agent_message" and it.get("text"):
                self.final = it["text"]
            if t == "item.completed" and it.get("type") == "error":
                self.errors.append(str(it.get("message") or it)[:400])
        elif t == "turn.completed":
            self.usages.append(ev.get("usage") or {})
            self.done = True
        elif t == "turn.failed":
            self.errors.append(json.dumps(ev.get("error") or ev, ensure_ascii=False)[:400])
        elif t == "error":
            self.errors.append(str(ev.get("message") or ev)[:400])

    def turns(self):
        return sum(1 for v in self.items.values() if v != "reasoning")

    def kinds(self):
        out = {}
        for v in self.items.values():
            out[v] = out.get(v, 0) + 1
        return out

    def usage(self):
        tot = {"input": 0, "cached": 0, "output": 0, "reasoning": 0}
        for u in self.usages:
            tot["input"] += int(u.get("input_tokens") or 0)
            tot["cached"] += int(u.get("cached_input_tokens") or 0)
            tot["output"] += int(u.get("output_tokens") or 0)
            tot["reasoning"] += int(u.get("reasoning_output_tokens") or 0)
        return {"in_miss": max(0, tot["input"] - tot["cached"]), "in_hit": tot["cached"], "out": tot["output"],
                "reasoning": tot["reasoning"]}


def run_exec(cmd, prompt, run_dir, cwd, env, timeout_s, max_turns=None):
    """跑一次 codex exec：任务正文从文件喂 stdin，事件流写 run_dir/events.jsonl，stderr 写 run_dir/stderr.log。
    返回 {killed: None|timeout|max_turns, rc, ev: Events, wall_s}。"""
    os.makedirs(run_dir, exist_ok=True)
    pp, stream, errp = (os.path.join(run_dir, n) for n in ("prompt.md", "events.jsonl", "stderr.log"))
    util.write(pp, prompt)
    ev = Events(stream)
    t0, killed = time.time(), None
    with open(pp, "rb") as si, open(stream, "wb") as so, open(errp, "wb") as se:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=si, stdout=so, stderr=se, start_new_session=True)
        try:
            while proc.poll() is None:
                time.sleep(0.25 if time.time() - t0 < 4 else 2)    # 短调用（问答、selftest）不白等 2 秒
                ev.poll()
                if util.HALT.is_set():
                    killed = "interrupted"
                elif max_turns and ev.turns() > max_turns:
                    killed = "max_turns"
                elif time.time() - t0 > timeout_s:
                    killed = "timeout"
                if killed:
                    A._kill_tree(proc.pid)
                    break
        except BaseException:                                       # 主线程 Ctrl-C：代理在自己的进程组里，要手动杀
            A._kill_tree(proc.pid)
            raise
        try:
            rc = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            A._kill([proc.pid], grace=1)
            rc = proc.poll()
    ev.poll()
    ev.feed(ev.buf)
    return {"killed": killed, "rc": rc, "ev": ev, "wall_s": round(time.time() - t0, 1)}


def shown(cmd):
    """记进 cmd.json 的命令行：长参数缩写（任务正文走 stdin，不在这里），权限档原样留着（事后查放行了哪些目录）。"""
    return [x if len(x) < 400 or x.startswith(f"permissions.{PROFILE}=") else f"<{len(x)} 字符>" for x in cmd]


def is_quota(text):
    return bool(QUOTA_RE.search(text or ""))


# ----------------------------------------------------------------------------- 记账
class Ledger(cost.Ledger):
    """codex 记账：token 照记，费用恒为 0（订阅额度）。budget_usd 没有意义，守卫只在代理里看轮数和时长。"""

    def __init__(self, path, budget_usd=None):
        super().__init__(path, ZERO_PRICING, budget_usd)
        self.d["billing"] = "codex：你的 ChatGPT 订阅额度，只记 token，不按美元计；预算守卫只看轮数和时长"
        self.d["pricing"] = ZERO_PRICING
        self.d.setdefault("reasoning_tokens", 0)
        self.save()

    def add(self, stage, name, kind, usage_raw, mock=False, extra=None):
        with util.LOCK:
            return self._add_codex(stage, name, kind, usage_raw, mock, extra)

    def _add_codex(self, stage, name, kind, usage_raw, mock=False, extra=None):
        u = dict(usage_raw or {})
        if "in_miss" in u:                                            # codex 的 usage：三项记账，其余（reasoning、轮数、目录）进记录
            nu = {k: int(u.pop(k, 0) or 0) for k in ("in_miss", "in_hit", "out")}
            ex = u
        else:
            nu, ex = cost.norm_usage(u), {}
        ex.update({"backend": "codex", "billing": "subscription"})
        ex.update(extra or {})
        self.d["reasoning_tokens"] = int(self.d.get("reasoning_tokens") or 0) + int(ex.get("reasoning") or 0)
        return super().add(stage, name, kind, nu, mock=mock, extra=ex)


# ----------------------------------------------------------------------------- 问答
class CodexLLM(llm.LLM):
    """和 llm.LLM 同一个 ask_json（schema / 业务校验 / 只提醒 / 重试照旧），只把「发一次请求」换成一次 codex exec -s read-only。"""

    def __init__(self, cfg, cc, ledger, ep_dir, log, mock_fn=None):
        super().__init__(cfg, ledger, ep_dir, log, mock_fn=mock_fn)
        self.cc = cc
        api = cfg["api"]
        st = {k: dict(v) for k, v in (api.get("stages") or {}).items()}
        for k, v in (cc.get("stages") or {}).items():
            st[k] = dict(st.get(k, {}), **v)
        self.c = dict(api, kind="codex", model=cc["model"], json_mode=False, stages=st)
        self.cdir = os.path.join(self.calls_dir, "codex")
        self._n = len(os.listdir(self.cdir)) if os.path.isdir(self.cdir) else 0

    def _messages(self, system, prompt, images, history):
        return {"system": system, "prompt": prompt, "images": list(images), "history": list(history)}

    def _body(self, system, msgs, sc, json_mode):
        return dict(msgs, effort=sc.get("effort"))

    def _reply(self, resp):
        return resp.get("text") or "", "stop"

    def text(self, body):
        parts = [body["system"], prompt_file("qa")]
        if body["images"]:
            parts.append("## 附图（按附件顺序）\n" + "\n".join(f"- 图 {i}：{lab}" for i, (lab, _) in enumerate(body["images"], 1)))
        parts.append(body["prompt"])
        for h in body["history"]:
            c = h["content"] if isinstance(h["content"], str) else json.dumps(h["content"], ensure_ascii=False)
            parts.append(("## 你上一次的输出\n" if h["role"] == "assistant" else "## 编排器的反馈（照它改，再输出完整 JSON）\n") + c)
        return "\n\n".join(parts)

    def _send(self, body):
        ensure_login(self.cc)
        qa = self.cc["qa"]
        waits = list(qa.get("retry_wait_s") or [])[:int(qa.get("transient_retries", 2))] + [None]
        prompt = self.text(body)
        imgs = [os.path.abspath(p) for _, p in body["images"]]
        t_all, last = time.time(), None
        for wait in waits:
            self._n += 1
            rd = os.path.join(self.cdir, f"{self._n:03d}")
            os.makedirs(rd, exist_ok=True)
            lastp = os.path.join(rd, "last.md")
            cwd = tempfile.mkdtemp(prefix="p2-codex-qa-")              # 临时空目录：问答不需要任何文件
            try:
                cmd = build_cmd(self.cc, "read-only", cwd, lastp, effort=body.get("effort"), images=imgs)
                util.write_json(os.path.join(rd, "cmd.json"), {"cmd": shown(cmd), "cwd": cwd, "images": imgs})
                r = run_exec(cmd, prompt, rd, cwd, clean_env(os.path.join(rd, "tmp")), qa.get("timeout_s", 2400))
            finally:
                shutil.rmtree(cwd, ignore_errors=True)
                shutil.rmtree(os.path.join(rd, "tmp"), ignore_errors=True)
            ev = r["ev"]
            text = util.read(lastp) if os.path.exists(lastp) else (ev.final or "")
            nu = dict(ev.usage(), turns=ev.turns(), wall_s=r["wall_s"], thread=ev.thread, codex_dir=os.path.relpath(rd, self.ep))
            if r["rc"] == 0 and text.strip() and not r["killed"]:
                return 200, {"text": text, "usage": nu}, time.time() - t_all
            err = "；".join(ev.errors[-3:]) or util.tail(os.path.join(rd, "stderr.log"), 8)[-600:] or f"rc={r['rc']}"
            last = (r, err, nu)
            self.log(f"codex 问答没拿到回复（{r['killed'] or 'rc=' + str(r['rc'])}）：{err[:200]}"
                     + (f"；{wait} 秒后重试" if wait else ""), echo=False)
            if wait is None:
                break
            if any(nu.get(k) for k in ("in_miss", "in_hit", "out")):       # 重试前那次也用了额度：照样记 token
                self.ledger.add("codex_retry", f"{self._n:03d}", "api", dict(nu))
            time.sleep(wait)
        r, err, nu = last
        if is_quota(err):
            raise util.Stop(4, "blocked", f"Codex（ChatGPT 额度）限流或额度用完：{err[:200]}。额度恢复后再运行同一条命令续跑")
        return (504 if r["killed"] == "timeout" else 500), {"error": err[:800], "usage": nu}, time.time() - t_all


# ----------------------------------------------------------------------------- 写代码的代理
def agent_suffix(kind):
    return "\n\n" + prompt_file("agent_codex", KIND={"keyframe": "代表帧", "shot": "镜头", "fix": "返修"}.get(kind, kind))


def run_agent(job, cfg, cc, ledger, log, mock_fn=None):
    """和 agent.run_agent 同样的输入输出（job / 返回 {status, usd, turns, ...}），换成一次 codex exec -s workspace-write。"""
    run_dir, work = job["run_dir"], job["work"]
    k = "keyframe" if job["kind"] == "keyframe" else ("fix" if job["kind"] == "fix" else "shot")
    ac = cc["agent"]
    timeout_s = 60 * float((ac.get("timeout_min") or {}).get(k, job["timeout_s"] / 60))
    max_turns = int((ac.get("max_turns") or {}).get(k, job["max_turns"]))
    os.makedirs(os.path.join(run_dir, "harness"), exist_ok=True)
    if not job["task"].startswith("# 本镜须知"):                       # 每镜须知（ctx/AI_USAGE.md + TASK.md 开头）
        job["task"] = util.shot_notice(work) + job["task"]
    util.write(os.path.join(work, "TASK.md"), job["task"])
    imgs = [p for p in job.get("images") or () if os.path.isfile(p)]  # 这一镜刚渲的画面（stages.agent_images）用 -i 附上
    prompt = "下面是你的任务（同一内容也在工作目录的 TASK.md）。\n\n" + job["task"]
    if imgs:
        prompt += "\n\n## 附图（按附件顺序；同一批文件在工作目录里）\n" + "\n".join(f"- 图 {i}：{os.path.relpath(p, work)}" for i, p in enumerate(imgs, 1))
    lastp = os.path.join(run_dir, "last.md")
    tmp = os.path.join(run_dir, "tmp")
    cmd = build_cmd(cc, "workspace-write", work, lastp, effort=ac.get("effort"), tmpdir=tmp, reads=job.get("allow_read"), images=imgs)
    util.write_json(os.path.join(run_dir, "harness", "cmd.json"), {"backend": "codex", "cmd": shown(cmd), "cwd": work,
                                                                     "timeout_s": timeout_s, "max_turns": max_turns,
                                                                     "sandbox_read": cc.get("sandbox_read") or "job",
                                                                     "images": [os.path.relpath(p, work) for p in imgs]})
    if mock_fn:
        usage = mock_fn(job)
        rec = ledger.add(job["stage"], job["name"], "agent", usage, mock=True)
        status = "ok" if all(os.path.exists(os.path.join(work, e)) for e in job["expect"]) else "missing_output"
        res = {"status": status, "usd": rec["usd"], "turns": 6, "mock": True, "backend": "codex", "run_dir": run_dir}
        util.write_json(os.path.join(run_dir, "meta.json"), res)
        return res
    ensure_login(cc)
    r = run_exec(cmd, prompt, run_dir, work, clean_env(tmp), timeout_s, max_turns)
    shutil.rmtree(tmp, ignore_errors=True)
    swept = A._sweep(work)
    ev = r["ev"]
    nu = ev.usage()
    rec = ledger.add(job["stage"], job["name"], "agent", nu, extra={"turns": ev.turns(), "wall_s": r["wall_s"]})
    status = r["killed"]
    errs = ev.errors[-3:]
    if status is None:
        ok = all(os.path.exists(os.path.join(work, e)) for e in job["expect"])
        status = "ok" if ok else ("missing_output" if r["rc"] == 0 else "error")
    res = {"status": status, "rc": r["rc"], "usd": rec["usd"], "turns": ev.turns(), "tools": ev.kinds(), "tokens": nu,
           "wall_s": r["wall_s"], "swept": swept, "backend": "codex", "thread": ev.thread, "errors": errs,
           "final_text": ((util.read(lastp) if os.path.exists(lastp) else ev.final) or "")[:2000],
           "stderr_tail": util.tail(os.path.join(run_dir, "stderr.log"), 20)[-2000:], "run_dir": run_dir}
    util.write_json(os.path.join(run_dir, "meta.json"), res)
    log(f"  agent(codex) {job['stage']}/{job['name']}: {status}，{res['turns']} 轮，token 入 {nu['in_miss'] + nu['in_hit']} / "
        f"出 {nu['out']}，{res['wall_s']}s", echo=False)
    qtext = " ".join(errs) if errs else res["stderr_tail"]
    if r["killed"] is None and r["rc"] not in (0, None) and is_quota(qtext):   # 额度用完：没交出东西（error），或交了半截（ok 但 rc≠0）都停
        raise util.Stop(4, "blocked", f"Codex（ChatGPT 额度）限流或额度用完，{job['stage']}/{job['name']} 没做完：{qtext[:200]}。"
                                      "额度恢复后再运行同一条命令续跑")
    return res
