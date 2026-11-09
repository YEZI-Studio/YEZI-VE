# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""渲染后端（都在沙箱外、由编排器执行；模型在沙箱里只写代码、跑 check）。

ChromaEngine   真实 CHROMA 引擎（p2/chroma/engine）。每集一份副本 <集>/engine/：src 复制（源只读不动），assets、tools 软链回源，
               素材目录 footage/meta/catalog.json 由 inputs/footage.json 写入。按节拍表、镜头表生成 src/timeline.py，
               把每镜的 shot.py 装进 src/shots/<id>.py，逐镜 check → warm → video（同一套 x264 参数，拼接直接拷流，冻结镜头逐帧不变）。
FakeRenderer   --fake-render：ffmpeg 占位视频，只测流程（几秒跑完），不碰引擎。"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import time

from . import inputs, media, util


def stop_tree(proc, grace=5.0):
    """杀子进程树：子进程用 start_new_session 起的（自己一个进程组，收不到终端的 Ctrl-C），先 SIGTERM 整组 + 后代，
    grace 秒内没退就 SIGKILL。"""
    from . import agent as A
    try:
        kids = A._desc(proc.pid, A._ps())
    except Exception:  # noqa: BLE001
        kids = []
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for fn, x in [(os.killpg, proc.pid)] + [(os.kill, k) for k in kids]:
            try:
                fn(x, sig)
            except OSError:
                pass
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            continue
        if not any(A._alive(k) for k in kids):
            return


def run_watched(cmd, cwd=None, env=None, stdout=None, stderr=None, timeout=3600):
    """跑子进程，等它的时候每 0.2 s 查一次 util.HALT（Ctrl-C）和超时（停 Codex 集时在跑的线程照样往下渲、起下一镜的
    返回 (退出码, None | "timeout" | "interrupted")；主线程收到 KeyboardInterrupt 也先杀进程树再抛。"""
    if util.HALT.is_set():
        return -2, "interrupted"
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL, start_new_session=True)
    t0, why = time.time(), None
    try:
        while proc.poll() is None:
            if util.HALT.is_set():
                why = "interrupted"
            elif time.time() - t0 > timeout:
                why = "timeout"
            if why:
                stop_tree(proc)
                break
            time.sleep(0.2)
    except BaseException:
        stop_tree(proc)
        raise
    rc = proc.poll()
    return (rc if rc is not None else -9), why


def _hue_of(sid, variant):
    n = int(re.sub(r"\D", "", sid) or 0)
    return (n * 47 + variant * 90) % 360


class ChromaEngine:
    name = "chroma-engine"

    def __init__(self, ep, series, log, mock=False):
        self.ep, self.series, self.log, self.mock = ep, series, log, mock
        e = series["engine"]
        self.root = os.path.join(ep, "engine")
        self.src_root = os.path.join(util.PARENT, e["root"])
        self.src_dir = os.path.join(util.PARENT, e["src"])
        self.py = os.path.join(util.PARENT, e["python"])
        self.logs = os.path.join(ep, "logs")

    # ------------------------------------------------------------------ 工程副本
    def _stale_note(self, dst):
        """续跑旧集：引擎副本只在第一次复制，旧集的 src 可能没有 11-04 的 SR.seg / SR.env / SR.slots、11-04 晚第二轮的 SR.track /
        而任务书、HANDBOOK 是新的（会叫代理用这些）。
        旧模板悄悄忽略。"""
        if getattr(self, "_stale_told", False):
            return
        self._stale_told = True
        try:
            ser = util.read(os.path.join(dst, "fx", "series.py"))
        except OSError:
            return
        lack = [n for n, k in (("SR.seg / SR.env / SR.slots", "def seg("), ("SR.track", "def track("),
                               ("AutoCam(px_per_f=…)", "px_per_f"), ("实拍叠层 SR.element", "def element("),
                               ("画面自碎 SR.shatter", "def shatter(")) if k not in ser]
        if lack and self.log:
            self.log(f"注意：这一集的引擎副本 engine/src 是旧的（没有 {'、'.join(lack)}），新任务书、HANDBOOK 会叫代理用这些，"
                     "渲染会报 AttributeError / TypeError。要用新引擎：把 engine/src 挪到 engine/_old_src/ 后续跑"
                     "（编排器会重新复制；已渲好的镜头不受影响，返修的镜头用新引擎）。", echo=True)

    def setup(self, beatmap=None, shotlist=None):
        """第一次：复制引擎代码、软链 assets/tools、写素材目录；有镜头表后：重写 timeline.py、补占位镜头。可重复调用。"""
        dst = os.path.join(self.root, "src")
        if not os.path.isdir(dst):
            shutil.copytree(self.src_dir, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            for d in self.series["engine"].get("link", []):
                if not os.path.lexists(os.path.join(self.root, d)):   # 换新引擎（src 挪走后重新复制）时软链已经在
                    os.symlink(os.path.join(self.src_root, d), os.path.join(self.root, d))
            for d in ("footage/meta", "footage/test", "audio", "cache", "out", "logs"):
                os.makedirs(os.path.join(self.root, d), exist_ok=True)
            util.write_json(os.path.join(self.root, "footage", "test", "catalog.json"), {"clips": []})
            util.write(os.path.join(self.root, "SOURCE.md"),
                       f"从 {self.src_dir} 复制（{util.iso()}），源目录只读不动；assets、tools 是指回源的软链。\n"
                       "footage/meta/catalog.json 由 inputs/footage.json 写入；src/timeline.py 由编排器按节拍表生成。\n")
        fj = util.read_json(os.path.join(self.ep, "inputs", "footage.json"), {"clips": []})
        cat = os.path.join(self.root, "footage", "meta", "catalog.json")
        clips = fj.get("clips", []) + self.gen_clips()                # 生图通道生成的静图（没开过就是空的，和以前一样）
        if util.read_json(cat) != {"clips": clips}:
            util.write_json(cat, {"clips": clips})
        self._stale_note(dst)
        mu = util.read_json(os.path.join(self.ep, "inputs", "music.json"))
        evp = os.path.join(self.root, "audio", "events.json")
        if mu and mu.get("events") and os.path.exists(mu["events"]) and not os.path.exists(evp):
            ev = util.read_json(mu["events"])                     # 16 分音符重音表（窗口内帧号）→ SR.accent_frames / auto_cam
            if abs(float(ev.get("window_start_song_s", -1)) - float(mu["start_s"])) < 0.01:
                shutil.copy2(mu["events"], evp)
        mp = (mu or {}).get("music_map")                          # 音乐打击点表（真实起音帧）→ SR.slots / accent_frames / auto_cam
        mp = (mp if os.path.isabs(mp) else os.path.join(self.ep, mp)) if mp else None
        mmp = os.path.join(self.root, "audio", "music_map.json")
        if mp and os.path.exists(mp) and (not os.path.exists(mmp) or util.read(mmp) != util.read(mp)):
            os.makedirs(os.path.dirname(mmp), exist_ok=True)
            shutil.copy2(mp, mmp)                                 # 短片模式是截断过的那份；换了配乐 / 重截就跟着换
        if beatmap and shotlist:
            tl = timeline_py(beatmap, shotlist, series_fx(util.read_json(os.path.join(self.ep, "topic.json"))))
            p = os.path.join(dst, "timeline.py")
            if not os.path.exists(p) or util.read(p) != tl:
                util.write(p, tl)
            for s in shotlist["shots"]:
                p = os.path.join(dst, "shots", f"{s['id']}.py")
                if not os.path.exists(p):
                    util.write(p, stub_shot(s))

    def gen_clips(self):
        """<集>/inputs/gen_images.json 里生成成功、文件还在的静图（lib/imagegen.py 写；镜头里 Take(name, ...)）。"""
        g = util.read_json(os.path.join(self.ep, "inputs", "gen_images.json"))
        return [x for x in (g or {}).get("clips", []) if os.path.isfile(x.get("file", ""))]

    def footage_dirs(self):
        fj = util.read_json(os.path.join(self.ep, "inputs", "footage.json"), {"clips": []})
        return sorted({os.path.dirname(c["file"]) for c in fj.get("clips", []) + self.gen_clips()})

    def install(self, sid, code_path):
        shutil.copy2(code_path, os.path.join(self.root, "src", "shots", f"{sid}.py"))

    def element_tables(self):
        """这一集的叠层表文件（母题素材包 → 系列借的包 assets.shared_overlays → 本集素材包），给渲染子进程的 CHROMA_ELEMENTS。
        规划 A3：引擎副本原来只能从素材清单反查到母题包的 overlays.json（ROOT/../../assets/* 在集副本里指到 p2/episodes/assets，找不到），
        叠层在别的包里（借别的素材包的叠层，或素材请求通道下到别的包）SR.element 就找不到。"""
        topic = util.read_json(os.path.join(self.ep, "topic.json")) or {}
        try:
            return inputs.element_tables(self.series, topic.get("motif"), self.ep)
        except Exception:  # noqa: BLE001
            return []

    def _run(self, args, log_name, timeout=3600, module="src.render"):
        lp = os.path.join(self.logs, log_name)
        os.makedirs(self.logs, exist_ok=True)
        env = {"PATH": util.BIN_PATH, "HOME": util.HOME, "PYTHONDONTWRITEBYTECODE": "1"}
        els = self.element_tables()
        if els:
            env["CHROMA_ELEMENTS"] = os.pathsep.join(els)
        with open(lp, "a", encoding="utf-8") as f:
            f.write(f"\n$ python -m {module} {' '.join(args)}\n")
            f.flush()
            rc, why = run_watched([self.py, "-B", "-m", module] + args, cwd=self.root, env=env, stdout=f,
                                  stderr=subprocess.STDOUT, timeout=timeout)
            if why == "timeout":
                f.write(f"\n超时（{timeout}s）\n")
                rc = -9
            elif why == "interrupted":
                f.write("\nCtrl-C：编排器收工，子进程树已杀\n")
        return {"ok": rc == 0 and not why, "rc": rc, "log_tail": util.tail(lp, 20), "log": lp,
                **({"interrupted": True} if why == "interrupted" else {})}

    def bake_elements(self):
        """预烘实拍叠层（引擎 src.tools.elements bake：参数没变就跳过）。旧引擎副本没有这个工具就跳过。"""
        if not os.path.exists(os.path.join(self.root, "src", "tools", "elements.py")):
            return {"ok": True, "skipped": "这一集的引擎副本没有实拍叠层"}
        r = self._run(["bake"], "elements_bake.log", timeout=900, module="src.tools.elements")
        return dict(r, why=r.get("log_tail"))

    # ------------------------------------------------------------------ 镜头
    def check_shot(self, sid, code_path):
        """不要 GPU：导入、@shot、Take 越界（和模型在沙箱里跑的是同一个 check）。"""
        self.install(sid, code_path)
        return self._run(["check", "--fn", f"src.shots.{sid}:{sid}"], f"check_{sid}.log", timeout=600)

    def render_shot(self, spec, code_path, out):
        sid = spec["id"]
        rc = self.check_shot(sid, code_path)
        if not rc["ok"]:
            return rc
        w = self._run(["warm", "--fn", f"src.shots.{sid}:{sid}"], f"render_{sid}.log")
        if not w["ok"]:
            return w
        if os.path.exists(out):
            os.remove(out)
        a, b = spec["start_f"] / 60.0, spec["end_f"] / 60.0
        return self._run(["video", "--out", out, "--range", f"{a:.6f}", f"{b:.6f}"], f"render_{sid}.log")

    def render_still(self, sid, code_path, frame, out):
        mod = f"key_{sid}"
        rc = self.check_shot(mod, code_path)
        if not rc["ok"]:
            return rc
        if os.path.exists(out):
            os.remove(out)
        return self._run(["still", "--F", str(frame), "--fn", f"src.shots.{mod}:{mod}", "--out", out], f"still_{sid}.log")

class FakeRenderer(ChromaEngine):
    """--fake-render：引擎代码照样复制（测上下文打包），渲染换成 ffmpeg 占位。"""
    name = "fake"

    def __init__(self, ep, series, log, mock=True):
        super().__init__(ep, series, log, mock)
        self.ffl = os.path.join(ep, "logs", "ffmpeg.log")

    def install(self, sid, code_path):
        pass

    def check_shot(self, sid, code_path):
        return {"ok": True}

    def bake_elements(self):
        return {"ok": True, "skipped": "fake"}

    def _variant(self, code_path):
        m = re.search(r"variant\s*=\s*(\d+)", util.read(code_path)) if os.path.exists(code_path) else None
        return int(m.group(1)) if m else 0

    def render_shot(self, spec, code_path, out):
        """占位：画面一路横移（单向，有效运动量量得到）；在「本镜卡点」表的重 / 轻帧（work/ctx/sync.json，没有就用 cuts_f）
        切到反相图——卡点落在真实起音上、表外的帧不出强变化（和真代理该做的一样，好让新门禁在 fake-render 下也量得过）。"""
        v = self._variant(code_path)
        rows = (util.read_json(os.path.join(os.path.dirname(os.path.dirname(code_path)), "ctx", "sync.json")) or {}).get("rows")
        fr = [r["F"] for r in rows if r.get("lvl") in ("重", "轻", "切")] if rows else spec["cuts_f"]   # 切 = 计划切点（sync_cuts 要真切）
        local = [c - spec["start_f"] for c in fr]
        media.placeholder_shot(out, spec["end_f"] - spec["start_f"], local,
                               f"{spec['id']} v{v} {spec.get('hero_word', '')}", _hue_of(spec["id"], v), log_path=self.ffl)
        return {"ok": True}

    def render_still(self, sid, code_path, frame, out):
        media.placeholder_still(out, f"KEY {sid} F{frame}", _hue_of(sid, self._variant(code_path)), log_path=self.ffl)
        return {"ok": True}


def series_fx(topic):
    """timeline.SERIES_FX（引擎按它开火花 / 电弧风格的线：'volt' = 电弧描边 / 裂线 / 砸字集中线，'ignition' = 火花风格）。
    导演定的母题（topic.motif）是火类 → 'ignition'，电类 → 'volt'，其余 → None（引擎默认全关）。"""
    m = str((topic or {}).get("motif") or "").lower()
    if any(k in m for k in ("雷", "电", "volt", "lightning", "electric")):
        return "volt"
    if any(k in m for k in ("火", "fire", "ignition", "flame")):
        return "ignition"
    return None


def timeline_py(bm, sl, sfx=None):
    rows = ",\n".join(f"    ({s['id']!r}, {s['id']!r}, {s['start_f']}, {s['end_f']}, {s.get('subject', '')[:60]!r})"
                      for s in sl["shots"])
    hits = [(h["F"], h["kind"]) for h in bm["hits"]]
    drop = next((h["F"] for h in bm["hits"] if h["kind"] == "drop"), 0)
    return f'''"""本集时间线（编排器按 beatmap.json / shotlist.json 生成，不要手改）。1920x1080, 60 fps。
第 1 小节第 1 拍 = 第 0 帧；配乐在声音环节对齐到原曲的拍上。
HITS / SHOTS 的帧号和 beatmap.json、shotlist.json、「本镜卡点」表同一个来源（有 music_map 时 = 真实起音帧，比 F(bar, beat) 早的就是早一帧）；
音乐打击点表在 audio/music_map.json（SR.slots 读它）。"""
import math

FPS = 60
W, H = 1920, 1080
BPM = {float(bm["bpm"])!r}
OFFSET = 0.0
BEAT = 60.0 * FPS / BPM
SIXTEENTH = BEAT / 4.0
BAR = 4.0 * BEAT


def f(bar, beat=1, sub=0.0):
    return OFFSET + (bar - 1) * BAR + (beat - 1) * BEAT + sub * SIXTEENTH


def F(bar, beat=1, sub=0.0):
    return int(math.floor(f(bar, beat, sub) + 0.5))


def pos(frame):
    x = (frame - OFFSET) / BEAT
    b = int(math.floor(x + 1e-6))
    return b // 4 + 1, b % 4 + 1, int(math.floor((x - b) * 4 + 1e-6)) + 1


def sec(frame):
    return frame / FPS


START = 0
END = {sl["total_f"]}
BARS = {bm["bars"]}
DROP = {drop}
HITS = {hits!r}
{f"SERIES_FX = {sfx!r}          # 火花 / 电弧风格开关（编排器按母题写；其余母题不写 = 全关）" + chr(10) if sfx else ""}
# name, module (src/shots/<module>.py), start, end (exclusive), what
SHOTS = [
{rows},
]


def check():
    assert SHOTS[0][2] == START == 0 and SHOTS[-1][3] == END
    for a, b in zip(SHOTS, SHOTS[1:]):
        assert a[3] == b[2], (a[0], b[0])
    return True
'''


def stub_shot(s):
    return f'''"""{s['id']} 占位（镜头代码还没做好）：纸白底 + 镜头号。"""
from src.engine import shot
from src.kit import type as TY
from src.kit.core import INK, PAPER


@shot(subframes=1)
def {s['id']}(E, T):
    cv = E.canvas()
    TY.word(cv, {s['id'].upper()!r}, TY.MARGIN, 540, 160, 'hero', INK)
    return E.over(E.solid(PAPER), cv.texture())
'''
