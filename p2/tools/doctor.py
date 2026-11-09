#!/usr/bin/env python3
# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""P2 环境体检：开做之前看这台机器缺什么，缺了怎么补、补不了怎么降级。

    python3 -I p2/tools/doctor.py           # 全查（本机约 3–10 秒）
    python3 -I .../doctor.py --no-net          # 不联网（只查本机）
    python3 -I .../doctor.py --fix-script      # 只打印「能自动补的」一键命令（不执行）

输出：屏幕上一张表（项 / 状态 / 现在 / 怎么补 / 降级方案）；结果写 doctor.json（路径见 orchestrator/config.json 的
doctor.path，默认 orchestrator/doctor.json），编排器开题时读 `hints`（例如能不能下素材、有没有生图通道）。
表的全文另存 logs/p2/doctor.log。

只判存在、不读值：OPENAI_API_KEY 等环境变量、DeepSeek key 文件、~/.codex/auth.json。不下载、不安装、不改任何文件
（doctor.json 和日志除外）；联网检查每个地址最多读 4 KB。纯标准库，3.9 起。
退出码：0 = 硬依赖都在（能出片）；1 = 缺硬依赖（ffmpeg / 引擎 Python / 磁盘），表里写了怎么补。
"""
import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

P2 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MD = os.path.dirname(P2)
ENGINE = os.path.join(P2, "chroma", "engine")
VENV_PY = os.path.join(P2, "chroma", ".venv", "bin", "python")
CFG = os.path.join(P2, "orchestrator", "config.json")
LOGDIR = os.path.join(MD, "logs", "p2")
HOME = os.path.expanduser("~")
DS_KEY = os.path.join(HOME, ".config", "yezi-ve", "deepseek.key")
UA = "yezi-ve-doctor/1.0 (local environment check)"

# 实拍素材的下载点：读 4 KB 就够判断「能下」。Pexels / Pixabay 网站本身挡脚本（403），能下的是 CDN；搜索要 API key 或浏览器。
NET_PROBES = {
    "pexels": [("CDN 视频文件", "https://videos.pexels.com/video-files/855709/855709-hd_1920_1080_25fps.mp4")],
    "pixabay": [("CDN 图片", "https://cdn.pixabay.com/photo/2015/04/23/22/00/tree-736885_640.jpg")],
    "wikimedia": [("文件", "https://upload.wikimedia.org/wikipedia/commons/a/a9/Example.jpg"),
                  ("Commons API", "https://commons.wikimedia.org/w/api.php?action=query&meta=siteinfo&format=json")],
}
SEARCH_KEYS = {"pexels": "PEXELS_API_KEY", "pixabay": "PIXABAY_API_KEY", "wikimedia": None}

# 开源字体菜单（OFL，Google Fonts 仓库，11-03 实测地址都能下）。只给菜单、不写死用哪款：模型按片子挑。
FONT_DIR = os.path.join(ENGINE, "assets", "fonts", "extra")
GF = "https://github.com/google/fonts/raw/main/ofl/"
FONT_MENU = [
    ("Anton-Regular.ttf", GF + "anton/Anton-Regular.ttf", "窄粗大字"),
    ("BebasNeue-Regular.ttf", GF + "bebasneue/BebasNeue-Regular.ttf", "窄体全大写"),
    ("BigShouldersDisplay-VF.ttf", GF + "bigshouldersdisplay/BigShouldersDisplay%5Bwght%5D.ttf", "窄高展示体，可变粗细"),
    ("SpaceGrotesk-VF.ttf", GF + "spacegrotesk/SpaceGrotesk%5Bwght%5D.ttf", "几何科技感"),
    ("Unbounded-VF.ttf", GF + "unbounded/Unbounded%5Bwght%5D.ttf", "宽圆体"),
    ("Syne-VF.ttf", GF + "syne/Syne%5Bwght%5D.ttf", "艺术感宽体"),
    ("Orbitron-VF.ttf", GF + "orbitron/Orbitron%5Bwght%5D.ttf", "科幻"),
    ("ArchivoBlack-Regular.ttf", GF + "archivoblack/ArchivoBlack-Regular.ttf", "粗黑无衬线"),
    ("RubikMonoOne-Regular.ttf", GF + "rubikmonoone/RubikMonoOne-Regular.ttf", "粗等宽"),
    ("JetBrainsMono-VF.ttf", GF + "jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf", "等宽（数字、HUD）"),
    ("ZCOOLQingKeHuangYou-Regular.ttf", GF + "zcoolqingkehuangyou/ZCOOLQingKeHuangYou-Regular.ttf", "中文粗黑展示"),
    ("ZCOOLKuaiLe-Regular.ttf", GF + "zcoolkuaile/ZCOOLKuaiLe-Regular.ttf", "中文活泼"),
    ("MaShanZheng-Regular.ttf", GF + "mashanzheng/MaShanZheng-Regular.ttf", "中文毛笔"),
]
ENGINE_FONTS = ["Archivo-ExpandedBlack.ttf", "DMMono-Medium.ttf", "Inter.ttf", "NotoSansSC-Black.otf", "NotoSerifSC-Black.otf"]

# 生图的其他通道：只看环境变量在不在
IMG_ENV = [("GEMINI_API_KEY", "Gemini"), ("GOOGLE_API_KEY", "Google"), ("REPLICATE_API_TOKEN", "Replicate"),
           ("FAL_KEY", "fal"), ("STABILITY_API_KEY", "Stability"), ("ARK_API_KEY", "火山方舟/即梦"),
           ("DASHSCOPE_API_KEY", "通义万相"), ("BFL_API_KEY", "FLUX")]


def run(cmd, timeout=30, env=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, "not found"
    except subprocess.TimeoutExpired:
        return 124, f"timeout {timeout}s"
    except Exception as e:  # noqa: BLE001
        return 1, repr(e)


def first(*paths):
    for p in paths:
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def which(name, *extra):
    return first(*extra, shutil.which(name) or "", f"/opt/homebrew/bin/{name}", f"{HOME}/.local/bin/{name}")


def load_cfg():
    try:
        with open(CFG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def fetch(url, n=4096, timeout=10):
    """GET 前 n 字节。返回 (ok, 说明)。"""
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": f"bytes=0-{n - 1}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(n)
            ok = r.status in (200, 206) and len(data) > 0
            return ok, f"HTTP {r.status}，{len(data)} B，{(time.time() - t0) * 1000:.0f} ms"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return False, type(e).__name__ + (f": {e.reason}" if hasattr(e, "reason") else "")


# ------------------------------------------------------------------ 检查项
class Doc:
    def __init__(self):
        self.rows = []

    def add(self, id_, name, status, now, fix="", fix_cmd=None, who="", fallback="", hard=False, **data):
        """status: ok 有 / warn 要注意 / missing 没有。who: auto = 一键命令能补；用户 = 要本人准备。"""
        self.rows.append(dict(id=id_, name=name, status=status, now=now, fix=fix, fix_cmd=fix_cmd, who=who,
                              fallback=fallback, hard=hard, data=data))
        return self.rows[-1]

    def get(self, id_):
        return next((r for r in self.rows if r["id"] == id_), None)


def check_net(d, on):
    if not on:
        d.add("net", "联网", "warn", "没查（--no-net）", fallback="按没网处理：只用本地素材包和曲库里的配乐")
        for k in NET_PROBES:
            d.add(f"footage_{k}", f"素材下载 {k.capitalize()}", "warn", "没查（--no-net）")
        return
    try:
        socket.getaddrinfo("videos.pexels.com", 443)
        dns = True
    except Exception:  # noqa: BLE001
        dns = False
    any_ok = False
    for k, probes in NET_PROBES.items():
        res = [(lab, *fetch(u)) for lab, u in probes]
        ok = all(r[1] for r in res)
        any_ok = any_ok or any(r[1] for r in res)
        key = SEARCH_KEYS[k]
        ks = "" if key is None else f"；搜索 API key {key}：{'有' if key in os.environ else '没有'}"
        now = "；".join(f"{lab} {'能下' if o else '不通'}（{msg}）" for lab, o, msg in res) + ks
        if key and key not in os.environ and ok:
            fix = f"能下载；要让模型自己搜片，你去 {k} 官网免费申请 API key，在 ~/.zshrc 里 export {key}=…（不交给 AI）"
        elif ok:
            fix = ""
        else:
            fix = "查网络 / 代理（HTTPS_PROXY）；或先用浏览器手动下好放进 p2/assets/<母题>/footage/"
        d.add(f"footage_{k}", f"素材下载 {k.capitalize()}", "ok" if ok else "missing", now, fix=fix,
              who="" if ok else "用户", fallback="用本地素材包（p2/assets/<母题>/footage）+ 生图补主体",
              cdn=ok, search_key=(key in os.environ) if key else None)
    if any_ok:
        d.add("net", "联网", "ok", "能连外网（DNS、HTTPS 正常）")
    else:
        d.add("net", "联网", "missing", "DNS " + ("正常" if dns else "解析不了") + "，HTTPS 都不通",
              fix="接上网络；公司网 / 代理环境在 shell 里设 HTTPS_PROXY", who="用户",
              fallback="只用本地素材包和曲库里的配乐；不选要下载素材的方案")


def codex_bin():
    """编排器实际用的 codex：环境变量 CODEX_BIN，没有就用 config_codex.json 的 bin（默认 ~/.local/bin/codex）。"""
    b = None
    try:
        with open(os.path.join(os.path.dirname(CFG), "config_codex.json"), encoding="utf-8") as f:
            b = json.load(f).get("bin")
    except Exception:  # noqa: BLE001
        pass
    return os.path.expanduser(os.environ.get("CODEX_BIN") or b or "~/.local/bin/codex")


def link_cmd(name, dst):
    """PATH 里找得到、编排器用的固定位置没有：建软链的一键命令。"""
    return f'mkdir -p "{os.path.dirname(dst)}" && ln -sf "$(command -v {name})" "{dst}"'


def check_imagegen(d, on_net):
    want = codex_bin()
    cx = first(want)
    other = None if cx else which("codex")
    codex_ok = False
    if not cx:
        d.add("img_codex", "生图 codex CLI", "missing",
              f"codex 在 {other}，编排器只认 {want.replace(HOME, '~', 1)}" if other else "没装 codex",
              fix=(f"建软链到 {want.replace(HOME, '~', 1)}（或设 CODEX_BIN）" if other else
                   "装 Codex CLI 并用 ChatGPT 账号登录（npm i -g @openai/codex，然后你本人 codex login）"),
              fix_cmd=link_cmd("codex", want) if other else None, who="auto" if other else "用户",
              fallback="走 OpenAI API；都没有就用实拍做主体")
    else:
        rc, ver = run([cx, "--version"], 20)
        rc2, feat = run([cx, "features", "list"], 30)
        m = re.search(r"^image_generation\s+\S+\s+(true|false)", feat, re.M)
        img_on = bool(m and m.group(1) == "true")
        auth = os.path.isfile(os.path.join(HOME, ".codex", "auth.json"))   # 只看文件在不在，不读
        codex_ok = img_on and auth
        now = f"{ver.strip().splitlines()[0] if ver.strip() else cx}；image_generation " + \
              ("开" if img_on else ("关" if m else "没查到")) + "；登录文件" + ("在" if auth else "不在")
        d.add("img_codex", "生图 codex CLI", "ok" if codex_ok else "warn", now,
              fix="" if codex_ok else ("你本人 codex login" if not auth else "打开生图开关"),
              who="" if codex_ok else ("用户" if not auth else "auto"),
              fix_cmd=None if codex_ok or not auth else f"{cx} features enable image_generation",
              fallback="走 OpenAI API；都没有就用实拍做主体", path=cx, image_generation=img_on, auth=auth)
    has = "OPENAI_API_KEY" in os.environ                      # 只判存在
    if has:
        d.add("img_openai", "生图 OpenAI API", "ok", "OPENAI_API_KEY 环境变量在（只判存在，没读值）", present=True)
    else:
        d.add("img_openai", "生图 OpenAI API", "warn" if codex_ok else "missing",
              "OPENAI_API_KEY 环境变量不在（只判存在，没读值）" + ("；codex 能生图，这条可选" if codex_ok else ""),
              fix=("可选：" if codex_ok else "") + "你本人在 ~/.zshrc 里 export OPENAI_API_KEY=…（编排器只读环境变量，不落盘）",
              who="" if codex_ok else "用户", fallback="走 codex 生图；都没有就用实拍做主体", present=False)
    others = [lab for k, lab in IMG_ENV if k in os.environ]
    comfy = False
    try:
        with socket.create_connection(("127.0.0.1", 8188), timeout=0.5):
            comfy = True
    except Exception:  # noqa: BLE001
        pass
    if comfy:
        others.append("本机 ComfyUI :8188")
    d.add("img_other", "生图 其他通道", "ok" if others else "warn",
          "有：" + "、".join(others) if others else "没有（Gemini / Replicate / fal / 即梦 / 通义 / FLUX 的 key 都不在，本机没开 ComfyUI）",
          fix="" if others else "可选，不是必须：有一条生图通道就够", who="", fallback="同上", channels=others)



def check_ffmpeg(d):
    ff = first(f"{HOME}/.local/bin/ffmpeg")                         # 引擎固定用 ~/.local/bin/ffmpeg、ffprobe（不查 PATH）
    fp = first(f"{HOME}/.local/bin/ffprobe")
    if not ff or not fp:
        miss = [n for n, p in (("ffmpeg", ff), ("ffprobe", fp)) if not p]
        elsewhere = all(which(n) for n in miss)
        ln = " && ".join(link_cmd(n, f"{HOME}/.local/bin/{n}") for n in miss)
        d.add("ffmpeg", "ffmpeg / ffprobe", "missing",
              "~/.local/bin/ 里没有 " + "、".join(miss) + ("（PATH 里有，引擎不查 PATH）" if elsewhere else ""),
              fix=("建软链到 ~/.local/bin/" if elsewhere else "brew install ffmpeg，再建软链到 ~/.local/bin/（引擎固定从那里调用）"),
              fix_cmd=(ln if elsewhere else "brew install ffmpeg && " + ln),
              who="auto", fallback="没有降级：出不了片（硬依赖）", hard=True)
        return
    _, v = run([ff, "-hide_banner", "-version"], 15)
    _, enc = run([ff, "-hide_banner", "-encoders"], 15)
    x264 = "libx264" in enc
    ver = v.splitlines()[0].split(" Copyright")[0] if v else ff
    d.add("ffmpeg", "ffmpeg / ffprobe", "ok" if x264 else "missing", f"{ver}；libx264 " + ("有" if x264 else "没有"),
          fix="" if x264 else "brew reinstall ffmpeg（要带 libx264）", fix_cmd=None if x264 else "brew reinstall ffmpeg",
          who="" if x264 else "auto", fallback="" if x264 else "没有降级：交付要 H.264", hard=True, path=ff, ffprobe=fp)


def check_python(d):
    ok_sys = sys.version_info >= (3, 9)
    d.add("python_sys", "Python（编排器）", "ok" if ok_sys else "missing", f"{sys.executable} {platform.python_version()}",
          fix="" if ok_sys else "brew install python@3.12", fix_cmd=None if ok_sys else "brew install python@3.12",
          who="" if ok_sys else "auto", fallback="", hard=True)
    uv = which("uv")
    rebuild = (f'cd "{os.path.join(P2, "chroma")}" && {uv or "uv"} venv --python 3.12 .venv && '
               f'{uv or "uv"} pip install --python .venv/bin/python -r engine/requirements.txt')
    if not os.path.isfile(VENV_PY):
        d.add("python_engine", "Python（渲染引擎）", "missing", "p2/chroma/.venv 不在", fix="一键重建（uv）",
              fix_cmd=("brew install uv && " if not uv else "") + rebuild, who="auto",
              fallback="没有降级：渲染靠它（硬依赖）", hard=True)
        return
    code = ("import json,sys\nr={'py':sys.version.split()[0]}\nfor m in ('numpy','cv2','skia','moderngl','librosa','soundfile','fontTools'):\n"
            "  try:\n    __import__(m); r[m]=True\n  except Exception as e:\n    r[m]=repr(e)[:80]\n"
            "try:\n  import moderngl; c=moderngl.create_standalone_context(); r['gl']=c.info['GL_RENDERER']; c.release()\n"
            "except Exception as e:\n  r['gl']=None; r['gl_err']=repr(e)[:80]\nprint('DOCTOR_JSON '+json.dumps(r))")
    rc, txt = run([VENV_PY, "-I", "-c", code], 60)
    m = re.search(r"DOCTOR_JSON (\{.*\})", txt)
    r = json.loads(m.group(1)) if m else {}
    bad = [k for k in ("numpy", "cv2", "skia", "moderngl", "librosa", "soundfile", "fontTools") if r.get(k) is not True]
    gl = r.get("gl")
    ok = bool(r) and not bad and gl
    now = (f"Python {r.get('py', '?')}，" + ("库齐" if not bad else "缺 " + "、".join(bad)) +
           (f"，OpenGL {gl}" if gl else "，开不了 OpenGL 上下文")) if r else f"跑不起来（rc={rc}）"
    d.add("python_engine", "Python（渲染引擎）", "ok" if ok else "missing", now,
          fix="" if ok else "一键重装依赖（uv）", fix_cmd=None if ok else rebuild, who="" if ok else "auto",
          fallback="" if ok else "没有降级：渲染靠它（硬依赖）", hard=True, **{k: v for k, v in r.items() if k in ("py", "gl")})


def check_fonts(d):
    have = [f for f in ENGINE_FONTS if os.path.isfile(os.path.join(ENGINE, "assets", "fonts", f))]
    miss = [f for f in ENGINE_FONTS if f not in have]
    extra = sorted(f for f in os.listdir(FONT_DIR)) if os.path.isdir(FONT_DIR) else []
    todo = [(f, u) for f, u, _ in FONT_MENU if f not in extra]
    cjk = 0
    fc = which("fc-list")
    if fc:
        _, out = run([fc, ":lang=zh", "family"], 20)
        cjk = len({ln.strip() for ln in out.splitlines() if ln.strip()})
    cmd = None
    if todo:
        cmd = f'mkdir -p "{FONT_DIR}" && cd "{FONT_DIR}" && ' + " && ".join(f"curl -fsSL -o '{f}' '{u}'" for f, u in todo)
    status = "missing" if miss else ("warn" if len(extra) < 6 else "ok")
    now = f"引擎自带 {len(have)}/{len(ENGINE_FONTS)} 款（Inter、Archivo 宽黑、DM Mono、思源黑 / 宋 Black）；" \
          f"开源字体菜单已下 {len(extra)} 款；系统中文字体 {cjk} 个家族"
    d.add("fonts", "字体库", status, now + ("；缺：" + "、".join(miss) if miss else ""),
          fix=(f"下载开源字体菜单 {len(todo)} 款（OFL，约 18 MB）到 engine/assets/fonts/extra/；引擎 FACES 要另外接" if todo else ""),
          fix_cmd=cmd, who="auto" if todo else "",
          fallback="只用自带 5 款：靠字重、字宽、大小写、描边 / 实心 / 镂空换样式，三次之内换一种", engine=have,
          extra=extra, menu=[dict(file=f, use=u) for f, _, u in FONT_MENU])


def check_deepseek(d):
    f_ok = os.path.isfile(DS_KEY) and os.path.getsize(DS_KEY) > 0          # stat，不读内容
    env_ok = "DEEPSEEK_API_KEY" in os.environ
    ok = f_ok or env_ok
    d.add("deepseek_key", "DeepSeek key", "ok" if ok else "missing",
          ("key 文件在" if f_ok else "key 文件不在") + f"（{DS_KEY.replace(HOME, '~', 1)}，只判存在）；DEEPSEEK_API_KEY 环境变量" +
          ("在" if env_ok else "不在"),
          fix="" if ok else f'你本人跑 bash "{os.path.join(P2, "setup_key.sh")}"', who="" if ok else "用户",
          fallback="" if ok else "只能 --mock 跑通流程，出不了真片", file=f_ok, env=env_ok)


def check_claude(d, cfg):
    want = os.path.expanduser((cfg.get("agent") or {}).get("claude_bin", "~/.local/bin/claude"))
    cb = first(want)                                               # 编排器只用这个固定位置（config.json agent.claude_bin）
    other = None if cb else which("claude")
    if not cb:
        d.add("claude_cli", "Claude Code CLI（写代码的代理）", "missing",
              f"claude 在 {other}，编排器只认 {want.replace(HOME, '~', 1)}" if other else "没找到",
              fix=(f"建软链到 {want.replace(HOME, '~', 1)}" if other else "curl -fsSL https://claude.ai/install.sh | bash"),
              fix_cmd=(link_cmd("claude", want) if other else "curl -fsSL https://claude.ai/install.sh | bash"),
              who="auto", fallback="写代码的环节（关键帧、每镜制作）跑不了，只能 --mock")
        return
    _, v = run([cb, "--version"], 20)
    d.add("claude_cli", "Claude Code CLI（写代码的代理）", "ok", f"{cb} {v.strip().splitlines()[0] if v.strip() else ''}",
          path=cb)


def check_music(d):
    """曲库（系列 music.library，默认 p2/assets/曲库/）里有没有分析好、能给 CHROMA 用的曲子（21–27 s 窗口、125–155 BPM、打击点表里有 DROP）。"""
    lib = os.path.join(P2, "assets", "曲库")
    md = os.path.join(lib, "music")
    n = len([f for f in os.listdir(md) if f.lower().endswith((".mp3", ".wav"))]) if os.path.isdir(md) else 0
    ok_n, analysed, tracks = 0, False, []
    try:
        with open(os.path.join(lib, "beats.json"), encoding="utf-8") as f:
            tracks = json.load(f).get("tracks") or []
        analysed = True
    except Exception:  # noqa: BLE001
        pass
    for t in tracks:
        w = t.get("window") or {}
        if (w and 21 <= (w.get("dur_s") or 0) <= 27 and 125 <= (t.get("bpm") or 0) <= 155
                and os.path.isfile(os.path.join(lib, str(t.get("file") or "")))):
            mf = os.path.join(lib, str(t["music_map_file"])) if t.get("music_map_file") else ""
            if mf and os.path.isfile(mf):                         # 打击点表里没有 DROP 不算能用（和 music_lib.sh 同一判法；表不在不判）
                try:
                    with open(mf, encoding="utf-8") as f:
                        mm = json.load(f)
                except Exception:  # noqa: BLE001
                    mm = {}
                if not (isinstance(mm, dict) and mm.get("drop")):
                    continue
            ok_n += 1
    good = ok_n > 0
    now = f"曲库 {n} 首（mp3 / wav）；" + (f"能给 CHROMA 用的 {ok_n} 首" if analysed else "还没跑节拍分析（music_lib.sh）")
    d.add("music", "配乐（曲库 p2/assets/曲库/）", "ok" if good else "warn", now,
          fix="" if good else "按 SKILL.md「自带配乐」放曲子，再运行 bash p2/tools/music_lib.sh", who="" if good else "用户",
          fallback="" if good else "没有能用的配乐：声音环节会停下，出不了交付成片", tracks=n, usable=ok_n)


def check_disk(d):
    rows = []
    for lab, p, warn, bad in (("项目盘", MD, 30, 8), ("系统盘（临时文件）", HOME, 20, 5)):
        try:
            u = shutil.disk_usage(p)
            rows.append((lab, u.free / 1e9, warn, bad))
        except Exception:  # noqa: BLE001
            pass
    worst = "ok"
    for _, free, warn, bad in rows:
        if free < bad:
            worst = "missing"
        elif free < warn and worst == "ok":
            worst = "warn"
    now = "；".join(f"{lab}剩 {free:.0f} GB" for lab, free, _, _ in rows) + "（一集约 4 GB：引擎副本 + 渲染缓存）"
    d.add("disk", "磁盘空间", worst, now,
          fix="" if worst == "ok" else "清理系统盘（下载、废纸篓、Xcode DerivedData）——你本人删，AI 不删", who="" if worst == "ok" else "用户",
          fallback="" if worst == "ok" else "旧集的 engine/cache 挪到项目盘外", hard=(worst == "missing"),
          free_gb={lab: round(free, 1) for lab, free, _, _ in rows})


# ------------------------------------------------------------------ 汇总
def hints(d):
    g = lambda i: d.get(i) or {}  # noqa: E731
    srcs = [k for k in NET_PROBES if g(f"footage_{k}").get("status") == "ok"]
    img = "codex" if g("img_codex").get("status") == "ok" else ("openai_api" if g("img_openai").get("status") == "ok" else
                                                                ("other" if g("img_other").get("status") == "ok" else "none"))
    notes = []
    if not srcs:
        notes.append("不能下载素材：只用本地素材包")
    if img == "none":
        notes.append("没有生图通道：「该生图就生图」降级为实拍做主体，报告里写明")
    if g("fonts").get("status") != "ok":
        notes.append("字体菜单只有自带几款：换样式靠字重 / 宽 / 描边，下了开源字体菜单再放开")
    hard = [r["name"] for r in d.rows if r["hard"] and r["status"] == "missing"]
    if hard:
        notes.append("缺硬依赖：" + "、".join(hard) + "（出不了片）")
    return dict(
        footage_download=bool(srcs), footage_sources=srcs,
        footage_search_keys=[k for k in srcs if (g(f"footage_{k}").get("data") or {}).get("search_key")],
        image_gen=img, music_download=g("music").get("status") == "ok",
        fonts_extra=len((g("fonts").get("data") or {}).get("extra") or []),
        can_render=not hard, can_run_real=g("deepseek_key").get("status") == "ok" and g("claude_cli").get("status") == "ok",
        notes=notes)


MARK = {"ok": "有", "warn": "要注意", "missing": "没有"}


def table(d):
    def cell(s):
        return (s or "—").replace("|", "／").replace("\n", " ")
    lines = ["| 项 | 状态 | 现在 | 怎么补（谁） | 降级方案 |", "|---|---|---|---|---|"]
    for r in d.rows:
        fix = r["fix"] + (f"（{'一键命令见下' if r['who'] == 'auto' and r['fix_cmd'] else r['who']}）" if r["who"] else "")
        lines.append(f"| {r['name']} | {MARK[r['status']]} | {cell(r['now'])} | {cell(fix)} | "
                     f"{cell(r['fallback'] if r['status'] != 'ok' else '')} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="P2 环境体检")
    ap.add_argument("--no-net", action="store_true", help="不联网")
    ap.add_argument("--no-render", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--out", default="", help="doctor.json 路径（默认 config.json 的 doctor.path）")
    ap.add_argument("--fix-script", action="store_true", help="只打印能自动补的一键命令")
    a = ap.parse_args()
    cfg = load_cfg()
    dc = cfg.get("doctor") or {}
    out = a.out or os.path.join(P2, "orchestrator", dc.get("path", "doctor.json"))
    t0 = time.time()
    d = Doc()
    check_net(d, not a.no_net)
    check_imagegen(d, not a.no_net)
    check_ffmpeg(d)
    check_python(d)
    check_fonts(d)
    check_deepseek(d)
    check_claude(d, cfg)
    check_music(d)
    check_disk(d)
    order = ["net", "footage_pexels", "footage_pixabay", "footage_wikimedia", "img_openai", "img_codex", "img_other",
             "ffmpeg", "python_sys", "python_engine", "fonts", "music", "deepseek_key", "claude_cli", "disk"]
    d.rows.sort(key=lambda r: order.index(r["id"]) if r["id"] in order else 99)
    fixes = [r for r in d.rows if r["status"] != "ok" and r["who"] == "auto" and r["fix_cmd"]]
    if a.fix_script:
        for r in fixes:
            print(f"# {r['name']}\n{r['fix_cmd']}")
        return 0
    h = hints(d)
    res = dict(version=1, t=time.strftime("%Y-%m-%d %H:%M:%S"), ttl_h=dc.get("ttl_h", 24), host=platform.node(),
               machine=f"{platform.system()} {platform.machine()} {platform.mac_ver()[0]}", seconds=round(time.time() - t0, 1),
               hints=h, checks={r["id"]: r for r in d.rows},
               user_todo=[dict(name=r["name"], fix=r["fix"]) for r in d.rows if r["status"] != "ok" and r["who"] == "用户"],
               auto_fix=[dict(name=r["name"], cmd=r["fix_cmd"]) for r in fixes])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    os.replace(tmp, out)
    tbl = table(d)
    head = f"环境体检 {res['t']}（{res['seconds']} s）：素材下载={'、'.join(h['footage_sources']) or '不能'}，" \
           f"生图={h['image_gen']}，能出片={'是' if h['can_render'] else '否'}，能真跑={'是' if h['can_run_real'] else '否'}"
    tail = ""
    if fixes:
        tail += "\n\n能自动补的（一键命令，要跑就复制；doctor 自己不执行）：\n" + "\n".join(f"  # {r['name']}\n  {r['fix_cmd']}" for r in fixes)
    if res["user_todo"]:
        tail += "\n\n要你本人准备的：\n" + "\n".join(f"  - {x['name']}：{x['fix']}" for x in res["user_todo"])
    if h["notes"]:
        tail += "\n\n编排器开题时会照这些降级：\n" + "\n".join(f"  - {n}" for n in h["notes"])
    text = f"{head}\n\n{tbl}{tail}\n\n结果：{out}"
    os.makedirs(LOGDIR, exist_ok=True)
    with open(os.path.join(LOGDIR, "doctor.log"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text)
    return 0 if h["can_render"] else 1


if __name__ == "__main__":
    sys.exit(main())
