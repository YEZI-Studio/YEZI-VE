# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""环节 0–7。每个环节：读文件 → （调模型 / 跑代理 / 渲染）→ 校验 → 写文件。流程、轮数、门禁都在代码里，不靠模型临场发挥。"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import operator
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import gates as G

from . import agent as A
from . import cheapfx as CF
from . import codex as CX
from . import imagegen as IG
from . import inputs, media, util
from . import stockreq as SQ
from . import tree as TR

ENERGY_NOTE = {"low": "低", "mid": "中", "high": "高", "peak": "顶"}


def _hangup_as_interrupt():
    """关终端（SIGHUP）、外面 kill（SIGTERM）也走 Ctrl-C 那条路：设 util.HALT、在主线程抛 KeyboardInterrupt
    量法工具由 run_watched 杀进程树（它们用 start_new_session 起，收不到终端的信号；11-04 审查：不装的话编排器一死它们就成孤儿接着渲）。
    只在主线程装（signal 的限制）；装过不重装。"""
    if threading.current_thread() is not threading.main_thread() or getattr(_hangup_as_interrupt, "done", False):
        return

    def h(signum, frame):
        util.HALT.set()
        raise KeyboardInterrupt
    for sig in (getattr(signal, "SIGHUP", None), signal.SIGTERM):
        if sig is not None:
            try:
                signal.signal(sig, h)
            except (ValueError, OSError):
                pass
    _hangup_as_interrupt.done = True


class Ctx:
    def __init__(self, ep, series, cfg, args, log, ledger, llm, renderer, mock):
        self.ep, self.series, self.cfg, self.args = ep, series, cfg, args
        self.log, self.ledger, self.llm, self.renderer, self.mock = log, ledger, llm, renderer, mock
        self.g = series["gates"]
        self.includes = {k: os.path.join(util.PARENT, v) for k, v in series["includes"].items()}
        self.tree = TR.load(self.includes["tree"]) if "tree" in self.includes else TR.load()
        self.system = util.read(self.find("prompts", "system", ".md")).strip()
        self.short = None                                           # 试跑短片模式（--short N）：小节数；run_episode 设
        self._mm = None
        _hangup_as_interrupt()

    def p(self, *a):
        return os.path.join(self.ep, *a)

    def mm(self):
        """这一集的音乐打击点表（inputs/music.json 的 music_map；短片模式是截断过的那份）。没有就是 None。"""
        mu = self.j("inputs/music.json") or {}
        p = mu.get("music_map")
        if not p:
            return None
        p = p if os.path.isabs(p) else self.p(p)
        if not os.path.exists(p):
            return None
        if not (self._mm and self._mm[0] == p and self._mm[1] == os.path.getmtime(p)):
            self._mm = (p, os.path.getmtime(p), util.read_json(p))
        return self._mm[2]

    def cat(self):
        """本集大类（规则树 id）：开题判的；还没开题就用系列已知的（CHROMA = 卡点炫技 = beat）。"""
        t = self.j("topic.json") or {}
        return t.get("category_id") or TR.cat_id(self.tree, self.series.get("category")) or "beat"

    def find(self, kind, name, ext):
        """提示词 / schema 按 <系列 id> → <大类 id> → 默认 覆盖查找：<kind>/<系列 id>/<名> → <kind>/<大类 id>/<名> → <kind>/<名>。"""
        base = os.path.join(util.HERE, kind)
        for sub in (self.series.get("id"), self.cat()):
            if sub and re.fullmatch(r"[A-Za-z0-9_\-]+", str(sub)):
                q = os.path.join(base, str(sub), name + ext)
                if os.path.isfile(q):
                    return q
        return os.path.join(base, name + ext)

    def series_vars(self):
        """每个提示词都能用的系列参数（调用方传了同名的以调用方为准）：TEXT_LANG = 屏上字语言（系列 text.say / lang），SERIES = 系列名，
        COLOR_RULE / COLOR_RULE_KF = 颜色怎么给（系列 text.color_rule / color_rule_kf；CHROMA = 颜色只给母题）。
        SUBFRAMES = 每帧子帧数（系列 engine.subframes，没写 = 1）；BLUR_RULE = 「快速运动带运动模糊」怎么做到（系列 text.blur_rule，没写 = BLUR_RULE）。"""
        t = self.series.get("text") or {}
        return {"TEXT_LANG": text_say(self.series), "SERIES": self.series.get("name", ""),
                "LADDER_NAME": str(t.get("ladder") or "数字链"),
                "COLOR_RULE": color_rule(self.series), "COLOR_RULE_KF": color_rule(self.series, long=True),
                "SHAPE_MENU": shape_menu(self.series), "SUBFRAMES": str(int((self.series.get("engine") or {}).get("subframes") or 1)),
                "BLUR_RULE": blur_rule(self.series)}

    def prompt(self, name, **v):
        """提示词模板：{{rules:…}} / {{human:…}} 从规则树取（根 + 本大类类型卡），include 别名 type = 本大类的类型卡。"""
        cid = self.cat()
        inc = dict(self.includes)
        cat = TR.category(self.tree, cid)
        if cat:
            inc["type"] = os.path.join(util.PARENT, cat["file"])
        tpl = TR.expand(util.read(self.find("prompts", name, ".md")), self.tree, cid, self.series)
        return util.render_prompt(tpl, dict(self.series_vars(), **v), inc)

    def schema(self, name):
        return util.read_json(self.find("schemas", name, ".json"))

    def j(self, name, default=None):
        return util.read_json(self.p(name), default)

    def title(self):
        t = self.j("topic.json") or {}
        return t.get("title_cn", "?")


LANG_SAY = {"en": "全英文", "zh": "全中文", "zh+en": "中文为主、英文为辅"}


def text_lang(series):
    """屏上字语言（系列 text.lang；没写 = en，CHROMA 全英文）。"""
    return str(((series or {}).get("text") or {}).get("lang") or "en").strip().lower()


COLOR_RULE = "颜色只给母题"
COLOR_RULE_KF = "颜色只给母题，而且是母题在画面里发生的东西（主体本身、反光、边缘的光），不是整屏染色。"


def color_rule(series, long=False):
    """提示词里的 {{COLOR_RULE}}（审片「颜色只给母题」）/ {{COLOR_RULE_KF}}（代表帧那一句）：系列 text.color_rule / color_rule_kf，
    没写 = CHROMA 原文。"""
    t = (series or {}).get("text") or {}
    if long:
        return str(t.get("color_rule_kf") or (t.get("color_rule") and t["color_rule"] + "。") or COLOR_RULE_KF)
    return str(t.get("color_rule") or COLOR_RULE)


BLUR_RULE = ("快速运动带运动模糊：@shot(subframes=4)，或用自带模糊的 fx.cam(…, n=3) / SR.auto_cam / SR.AutoCam / SR.track / E.blur"
             "（编排器查代码里有没有这些）")


def blur_rule(series):
    """必做 9 和提示词 {{BLUR_RULE}}：系列 text.blur_rule 优先，没写 = BLUR_RULE。"""
    return str(((series or {}).get("text") or {}).get("blur_rule") or BLUR_RULE)


def shape_menu(series):
    """开题的主角物路线（一句话）。"""
    return "主体用实拍或生图，物质用实拍叠层，冲击用画面派生"


def text_say(series):
    """提示词里的 {{TEXT_LANG}}（「屏上字{{TEXT_LANG}}」）：系列 text.say 优先，没写按 lang 给（en = 全英文）。"""
    t = (series or {}).get("text") or {}
    return str(t.get("say") or LANG_SAY.get(text_lang(series), text_lang(series)))


def dumps(o):
    """给提示词用的紧凑 JSON：顶层一键一行，列表里的对象一项一行。"""
    one = lambda x: json.dumps(x, ensure_ascii=False, separators=(", ", ": "))  # noqa: E731
    if isinstance(o, dict):
        rows = []
        for k, v in o.items():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                rows.append(f"{one(k)}: [\n    " + ",\n    ".join(one(x) for x in v) + "]")
            else:
                rows.append(f"{one(k)}: {one(v)}")
        return "{\n  " + ",\n  ".join(rows) + "\n}"
    return one(o)


# ============================================================================ 节拍换算
def frame_at(bm, bar, beat=1, sub=0.0):
    return int(math.floor((bar - 1) * bm["bar_f"] + (beat - 1) * bm["beat_f"] + sub * bm["beat_f"] / 4 + 0.5))


# 音乐打击点表（music_map，p2/tools/syncmeas.py map 生成）：每个 16 分格一条，
# F = 画面该变的整数帧（真实起音，比格点早约 11 ms）。卡点、切点的帧号全部从这里取，不再 round(格点)。
ONSET = 0.6                                                       # total ≥ 0.6 = 这一格有起音（B 档线）
BAND = {"L": "808 低频", "M": "军鼓 / 拍手", "h": "镲", "H": "镲"}


def six(bar, beat=1, sub=0):
    """(小节, 拍, 16 分) → 窗口里第几个 16 分格（0 起）。"""
    return int(round(((bar - 1) * 4 + (beat - 1) + sub / 4) * 4))


def mm_slots(mm):
    return {int(e["i"]): e for e in (mm or {}).get("slots") or []}


def mm_slot(mm, bar, beat=1, sub=0):
    return mm_slots(mm).get(six(bar, beat, sub)) if mm else None


def has_onset(e):
    return bool(e) and (e.get("total") or 0) >= ONSET


def is_accent(e):
    return bool(e) and (bool(e.get("accent")) or e.get("tier") in ("S", "A"))


def slot_pos(e):
    return f"{e['bar']}.{e['beat']}.{e.get('sub', 0)}"


def slot_F(bm, mm, bar, beat=1, sub=0):
    """卡点 / 切点的画面帧：急停那一格用急停的帧（声音掉下去的那一刻）；这一格有起音就用起音帧（music_map 的 F），
    没有就照旧 round(格点)。片长 total_f 不走这里。"""
    i = six(bar, beat, sub)
    st = next((x for x in (mm or {}).get("stops") or [] if six(x["bar"], x["beat"], x.get("sub", 0)) == i), None)
    if st:
        return int(st["F"])
    e = mm_slot(mm, bar, beat, sub)
    return int(e["F"]) if has_onset(e) else frame_at(bm, bar, beat, sub)


def expand_beatmap(raw, mm=None):
    """mm = 音乐打击点表：卡点、hook_1s / wow_7s、段落边界（小节线）这一格有起音时用起音帧；total_f 照旧按格点（片长不变）。"""
    bm = dict(raw)
    bm["beat_f"] = 3600.0 / bm["bpm"]
    bm["bar_f"] = 4 * bm["beat_f"]
    bm["sixteenth_f"] = bm["beat_f"] / 4
    bm["total_f"] = frame_at(bm, bm["bars"] + 1)
    bar_F = lambda b: bm["total_f"] if b > bm["bars"] else 0 if b <= 1 else slot_F(bm, mm, b)  # noqa: E731   第 1 小节钉在 0
    bm["sections"] = [dict(s, start_f=bar_F(s["bars"][0]), end_f=bar_F(s["bars"][1])) for s in raw["sections"]]
    bm["hits"] = sorted([dict(h, sub=h.get("sub", 0), F=slot_F(bm, mm, h["bar"], h["beat"], h.get("sub", 0)))
                         for h in raw["hits"]], key=lambda h: h["F"])
    for k in ("hook_1s", "wow_7s"):                                   # 规则树「开头结尾」必做：第 1 秒的变化、7 s 内的王炸
        h = raw.get(k)
        if isinstance(h, dict) and h.get("bar"):
            bm[k] = dict(h, F=slot_F(bm, mm, h["bar"], h.get("beat", 1), h.get("sub", 0)))
    if mm:
        bm["frames_from"] = "music_map（真实起音帧）"
    return bm


def stop_breath(mm, st):
    """19.5 s 全停 = 大爆发前的断拍真空，系列卡「大爆发前 ≤0.5 s 断拍」）时，这一格排 breath 而不是 big；急停后 2 拍内就是 DROP 时，
    DROP 就是急停后的那一下再砸。只看打击点表，不看导演把 breath 放在哪（导演挪一格 breath
    不能绕过「急停必须排 big」）。急停和屏息不同格的曲子行为不变。"""
    br = (mm or {}).get("breath") or {}
    same = bool(br.get("bar")) and six(br["bar"], br["beat"], br.get("sub", 0)) == six(st["bar"], st["beat"], st.get("sub", 0))
    if not same:
        return False, False
    drop = (mm or {}).get("drop") or {}
    beat_f = 3600.0 / float((mm or {}).get("bpm") or 150)
    rehit = drop.get("F") is not None and 0 < float(drop["F"]) - float(st["F"]) <= 2 * beat_f + 0.5
    return True, rehit


def music_slots(mm, bm_bars=None):
    """导演提示词的 {{MUSIC_SLOTS}}（音乐打击点表）和 {{MUSIC_MAP}}（逐小节重音谱）。返回 (表, 谱)；没有 music_map 返回说明。"""
    if not mm:
        none = "（这一集没有实测的音乐打击点表：卡点按拍子排，大卡点放在小节第 1 拍 / 第 3 拍的重拍上）"
        return none, none
    by = mm_slots(mm)
    rows = [e for e in by.values() if e.get("tier") in ("S", "A") and has_onset(e)]
    drop, stops = mm.get("drop") or {}, mm.get("stops") or []
    la, br = mm.get("last_accent") or {}, mm.get("breath") or {}
    keep = {six(x["bar"], x["beat"], x.get("sub", 0)) for x in [drop, la, br] + list(stops) if x.get("bar")}
    rows += [by[i] for i in keep if i in by and by[i] not in rows]
    top = {}                                                          # 每 2 小节最强的那一格（每个乐句至少一个 big）
    for e in rows:
        if is_accent(e):
            k = (e["bar"] - 1) // 2
            if k not in top or e["total"] > top[k]["total"]:
                top[k] = e
    tops = {id(e) for e in top.values()}
    stop_F = {six(st["bar"], st["beat"], st.get("sub", 0)): int(st["F"]) for st in stops}
    again = set()                                                     # 急停后再砸 = 急停后 2 拍以外的第一个重音（music_errs 同一个算法）
    beat_f = 3600.0 / float(mm.get("bpm") or 150)
    for st in stops:
        if stop_breath(mm, st)[1]:
            continue                                                  # 急停 = 屏息、2 拍内就是 DROP：DROP 就是那一下再砸
        nxt = min((e for e in rows if is_accent(e) and has_onset(e) and e["F"] > st["F"] + 2 * beat_f), key=lambda e: e["F"], default=None)
        if nxt:
            again.add(int(nxt["i"]))
    sb = {six(st["bar"], st["beat"], st.get("sub", 0)): stop_breath(mm, st) for st in stops}
    STOP_BR = "stop 急停 = 屏息（声音一下掉 {db} dB、全停：DROP 前的断拍真空）：排 breath（不切、画面停住），不排 big"
    out = ["| F（画面帧） | 位置 bar.beat.sub | 档位 | 名次 | 主导频段 | 说明 |", "|---|---|---|---|---|---|"]
    for e in sorted(rows, key=lambda e: stop_F.get(int(e["i"]), e["F"])):
        i, notes = int(e["i"]), []
        if drop.get("bar") and i == six(drop["bar"], drop["beat"], drop.get("sub", 0)):
            notes.append(f"drop（唯一，低频跳升 {drop.get('jump_db', '?')} dB）"
                         + ("；急停后的那一下再砸就是它" if any(v[1] for v in sb.values()) else ""))
        for st in stops:
            if i == six(st["bar"], st["beat"], st.get("sub", 0)):
                notes.append(STOP_BR.format(db=st.get("drop_db", "?")) if sb[i][0]
                             else f"stop 急停（声音一下掉 {st.get('drop_db', '?')} dB）：必须排 big")
        if la.get("bar") and i == six(la["bar"], la["beat"], la.get("sub", 0)):
            notes.append("最后一个重音 = final")
        if br.get("bar") and i == six(br["bar"], br["beat"], br.get("sub", 0)) and not (sb.get(i) or (False,))[0]:
            notes.append("DROP 前最安静的一拍 = breath")
        if i in again:
            notes.append("急停后再砸：必须排 big")
        if id(e) in tops:
            notes.append("乐句最强")
        tier = e.get("tier") if has_onset(e) else "—"
        out.append(f"| {stop_F.get(i, e['F'])} | {slot_pos(e)} | {tier} | {e.get('rank') if has_onset(e) else '—'} | "
                   f"{BAND.get(e.get('band'), e.get('band') or '—')} | {'；'.join(notes)} |")
    for st in stops:                                                  # 急停那一格常常没有起音（声音是掉下去的）：单独列
        if six(st["bar"], st["beat"], st.get("sub", 0)) not in {int(e["i"]) for e in rows}:
            say_ = (STOP_BR.format(db=st.get("drop_db", "?")) if sb[six(st["bar"], st["beat"], st.get("sub", 0))][0]
                    else f"stop 急停（声音一下掉 {st.get('drop_db', '?')} dB）：必须排 big")
            out.append(f"| {st['F']} | {st['bar']}.{st['beat']}.{st.get('sub', 0)} | S | — | — | {say_} |")
    bmap = mm.get("bar_map") or []
    n = bm_bars or mm.get("bars") or len(bmap)
    tab = [f"  第 {b + 1:2d} 小节  {m}" for b, m in enumerate(bmap[:n])]
    return "\n".join(out), "\n".join(tab) or "（没有逐小节谱）"


def music_errs(raw, mm):
    """节拍表对音乐的硬错误（「卡点卡得不准」）：卡点必须落在实测重音上。raw = 导演给的节拍表（bar / beat / sub）。"""
    by, errs = mm_slots(mm), []
    stops = mm.get("stops") or []
    stop_i = {six(st["bar"], st["beat"], st.get("sub", 0)) for st in stops}
    hits = [h for h in raw.get("hits") or [] if h.get("kind") in ("big", "small", "drop", "final")]
    say = lambda h: f"{h['kind']} {h['bar']}.{h['beat']}.{h.get('sub', 0)}"  # noqa: E731
    for h in hits:
        i = six(h["bar"], h["beat"], h.get("sub", 0))
        if i in stop_i:
            continue                                                  # 急停：整体事件，不看起音
        e = by.get(i)
        tot = (e or {}).get("total") or 0.0
        near = [x for x in (by.get(j) for j in range(i - 2, i + 3) if j != i) if x and has_onset(x)]
        if tot < ONSET:
            best = max(near, key=lambda x: x["total"], default=None)
            errs.append(f"{say(h)} 这一格没有起音（音乐打击点表里没有它）：" +
                        (f"挪到 {slot_pos(best)}（F{best['F']}）" if best and h["kind"] != "small" else "删掉，或换到表里有的格"))
            continue
        if h["kind"] in ("big", "drop", "final") and not is_accent(e):
            acc = [x for x in (by.get(j) for j in range(i - 4, i + 5) if j != i) if x and has_onset(x) and is_accent(x)]
            s_ = min(acc, key=lambda x: (abs(int(x["i"]) - i), -x["total"]), default=None)
            errs.append(f"{say(h)} 这一格只是 {e.get('tier', '?')} 档（强度 {tot:.2f}，不在音乐打击点表里）：大卡点只能放在表里的 S / A 档"
                        + (f"，挪到 {slot_pos(s_)}（F{s_['F']}，{s_.get('tier')} 档）" if s_ else "，换到表里的格") + "，或者改成 small")
            continue
        if h["kind"] == "big":
            st = [x for x in near if x["total"] >= tot + 0.3]
            if st:
                s_ = max(st, key=lambda x: x["total"])
                errs.append(f"{say(h)}（强度 {tot:.2f}）旁边 {slot_pos(s_)} 更强（{s_['total']:.2f}）：挪到 {slot_pos(s_)}（F{s_['F']}）")
    cover = {six(h["bar"], h["beat"], h.get("sub", 0)) for h in hits}
    coverF = {int(by[i]["F"]) for i in cover if i in by}
    for e in sorted(by.values(), key=lambda e: e["F"]):
        if e.get("tier") == "S" and has_onset(e) and int(e["i"]) not in cover and not any(abs(e["F"] - F) <= 1 for F in coverF):
            errs.append(f"S 档 {slot_pos(e)}（F{e['F']}，第 {e.get('rank')} 强）没有卡点：排 big（前后 2 拍内已经有 big 就排 small）")
    beat_f = 3600.0 / float(mm.get("bpm") or raw.get("bpm") or 150)
    br_i = {six(h["bar"], h["beat"], h.get("sub", 0)) for h in raw.get("hits") or [] if h.get("kind") == "breath"}
    for st in stops:
        i = six(st["bar"], st["beat"], st.get("sub", 0))
        same, rehit = stop_breath(mm, st)
        big_i = any(h["kind"] == "big" and six(h["bar"], h["beat"], h.get("sub", 0)) == i for h in hits)
        pos_ = f"{st['bar']}.{st['beat']}.{st.get('sub', 0)}（F{st['F']}）"
        if same and big_i and i in br_i:
            errs.append(f"急停 {pos_}同一格既排了 breath 又排了 big：这一格是 DROP 前的断拍真空（音乐全停），只排 breath（画面停住、不切）")
        elif same and not big_i and i not in br_i:
            errs.append(f"急停 {pos_}就是屏息那一拍（音乐打击点表把 DROP 前最安静的一拍也标在这一格）：排 breath（画面停住、不切）")
        elif not same and not big_i:
            errs.append(f"急停 {pos_}必须排 big")
        if rehit:
            continue                                                  # 急停后 2 拍内就是 DROP：DROP 就是那一下再砸（check_beatmap 已要求 drop 落在配乐的 DROP 上）
        nxt = min((e for e in by.values() if is_accent(e) and has_onset(e) and e["F"] > st["F"] + 2 * beat_f), key=lambda e: e["F"], default=None)
        if nxt and not any(h["kind"] in ("big", "drop", "final") and six(h["bar"], h["beat"], h.get("sub", 0)) == int(nxt["i"]) for h in hits):
            errs.append(f"急停后的第一个重音 {slot_pos(nxt)}（F{nxt['F']}）必须排 big（再砸）")
    la = mm.get("last_accent") or {}
    fin = [h for h in hits if h["kind"] == "final"]
    if la.get("bar") and fin and six(fin[0]["bar"], fin[0]["beat"], fin[0].get("sub", 0)) != six(la["bar"], la["beat"], la.get("sub", 0)):
        errs.append(f"final 必须是表里「最后一个重音」{la['bar']}.{la['beat']}.{la.get('sub', 0)}（F{la['F']}），"
                    f"现在是 {fin[0]['bar']}.{fin[0]['beat']}.{fin[0].get('sub', 0)}")
    return errs


def check_beatmap(raw, g, music=None, mm=None):
    """工程底线（不合格发回重做）。music = inputs/music.json（有配乐时：BPM 由代码写死成配乐的，小节数、DROP 位置必须跟配乐）。
    mm = 音乐打击点表：卡点必须落在实测重音上（music_errs）。规则项（BPM 区间、屏息在 DROP 前）在 beatmap_rules，只提醒。"""
    errs = []
    if music:
        raw["bpm"] = music["bpm"]                                   # 小数 BPM 不让模型抄，代码直接定
    bpm, bars = raw["bpm"], raw["bars"]
    if music and music.get("bars") and bars != music["bars"]:
        errs.append(f"bars 必须等于 {music['bars']}（配乐窗口的小节数）")
    if music and music.get("drop_bar"):
        d = [(h["bar"], h["beat"], h.get("sub", 0)) for h in raw["hits"] if h["kind"] == "drop"]
        if d and d[0] != (music["drop_bar"], 1, 0):
            errs.append(f"drop 必须在第 {music['drop_bar']} 小节第 1 拍（配乐的 DROP），现在是 {d[0]}")
    dur = bars * 240.0 / bpm
    dlo, dhi = g["duration_s"]
    if not dlo <= dur <= dhi:
        errs.append(f"片长 {dur:.2f}s 不在 {dlo}–{dhi}s（bars × 4 × 60/bpm）")
    secs = raw["sections"]
    want = 1
    for s in secs:
        a, b = s["bars"]
        if a != want or b <= a:
            errs.append(f"段落 {s['id']} 小节 {s['bars']} 没有接上（应从第 {want} 小节开始，结束 > 开始）")
        want = b
    if want != bars + 1:
        errs.append(f"段落没有铺到第 {bars} 小节末（最后一段应结束于 {bars + 1}）")
    for h in raw["hits"]:
        if not 1 <= h["bar"] <= bars:
            errs.append(f"卡点 {h} 不在 1–{bars} 小节")
    k = [h["kind"] for h in raw["hits"]]
    if k.count("breath") != 1 or k.count("drop") != 1 or k.count("final") != 1:
        errs.append(f"breath、drop、final 各要恰好 1 个（现在 {k.count('breath')}、{k.count('drop')}、{k.count('final')}）")
    if mm:
        errs += music_errs(raw, mm)
    return errs


def beatmap_rules(raw, g, music=None, mm=None):
    """节拍表的规则项（只提醒、不拦）。在 check_beatmap 之后调（BPM 已按配乐写死）。
    final 不再要求在拍上、在最后一小节第 1 拍（final = 窗口里最后一个重音，check_beatmap 按 music_map 核）。"""
    adv, bpm = [], raw["bpm"]
    lo, hi = g["bpm"]
    if not music and not lo <= bpm <= hi:
        adv.append(f"bpm {bpm} 不在 {lo}–{hi}")
    pos = {h["kind"]: (h["bar"], h["beat"], h.get("sub", 0)) for h in raw["hits"] if h["kind"] in ("breath", "drop", "final")}
    if "breath" in pos and "drop" in pos and not pos["breath"] < pos["drop"]:
        adv.append("屏息要在 drop 之前")
    br = (mm or {}).get("breath") or {}
    if br.get("bar") and "breath" in pos and pos["breath"] != (br["bar"], br["beat"], br.get("sub", 0)):
        adv.append(f"breath 建议放在音乐打击点表标的「DROP 前最安静的一拍」{br['bar']}.{br['beat']}.{br.get('sub', 0)}（F{br['F']}）")
    return adv


# 镜头内切点的密度档（切点从音乐起音里取，cut_every 只管密度）。旧镜头表的 "32nd" 按 16th。
# STEP（按格均分）只在没有 music_map 时兜底用。
STEP = {"16th": 0.25, "8th": 0.5, "beat": 1.0, "half": 2.0, "bar": 4.0, "none": None}
CUT_ALIAS = {"32nd": "16th"}


def music_cuts(start_f, end_f, cut_every, mm, holds=()):
    """镜头 [start_f, end_f) 内的切点 = 音乐起音帧（不再按 cut_every 均分）。密度档：16th = 本镜所有有起音的 16 分格（起音按本镜轻重算）；
    8th = 重音 + 8 分位置上有起音的格（16th / 8th 都不切在重音前后半拍的非重音格上：那一格也切的话重音和半拍外一样强，
    实测选择性只有 +0.05 / +0.07，过不了 sync_select）；beat = 只取重音；half = 每 2 拍最强的重音；bar = 每小节最强的重音；none = 不切。
    holds = 不切的拍（全片第几拍，0 起：屏息那一拍、DROP 后一拍、镜头自己的 holds）。切点离镜头边界 ≥ 4 帧，相邻切点 ≥ 5 帧。"""
    ce = CUT_ALIAS.get(cut_every, cut_every)
    if ce not in STEP or STEP[ce] is None:
        return []
    inner = [e for e in mm_slots(mm).values() if start_f + 4 <= e["F"] < end_f - 4
             and not any(4 * hb < int(e["i"]) < 4 * hb + 4 for hb in holds)]
    # 有起音按本镜自己的轻重算：前奏整段是弱底纹，按全片口径（≥0.6）一格都不算，结果前奏 6.6 s 只剩 3 刀；
    # 原片前奏照样每秒 3–5 刀（实测点火 22 刀、Light 32 刀）。线 = min(全片线, 本镜最强 × 0.35)
    top = max([e.get("total") or 0 for e in inner] or [0])
    thr = min(ONSET, 0.35 * top)
    hook = start_f == 0 and ce == "16th"                            # 开头那一镜是钩子：第 1 秒里弱起音也切（原片第 1 秒 7–9 个构图）
    cand = [e for e in inner if (e.get("total") or 0) > 0 and ((e.get("total") or 0) >= thr or (hook and e["F"] < 60))]
    acc_i = {int(e["i"]) for e in mm_slots(mm).values() if has_onset(e) and is_accent(e)}
    off = {j for i in acc_i for j in (i - 2, i + 2)} - acc_i       # 重音前后半拍的格：不是重音就不切（轻重跟着音乐走）
    if ce == "16th":
        pick = [e for e in cand if int(e["i"]) not in off or (hook and e["F"] < 60)]
    elif ce == "8th":                                               # 8 分位置有起音就切（前奏没有 808，原来要求低频 ≥0.5 会一刀不剩）
        pick = [e for e in cand if is_accent(e) or (e.get("sub") in (0, 2) and int(e["i"]) not in off)]
    elif ce == "beat":
        pick = [e for e in cand if is_accent(e)]
    else:
        span, best = (8 if ce == "half" else 16), {}
        for e in cand:
            k = int(e["i"]) // span
            if is_accent(e) and (k not in best or e["total"] > best[k]["total"]):
                best[k] = e
        pick = list(best.values())
    out = []
    for e in sorted(pick, key=lambda e: e["F"]):
        if out and e["F"] - out[-1] < 5:
            continue
        out.append(int(e["F"]))
    return out


def _cut_level(s):
    return CUT_ALIAS.get(s.get("cut_every"), s.get("cut_every"))


PLAN_CUTS = {"need_per_s": 3.0, "hook_16th": True, "gap_fix": True, "ref": "原片 3.0–5.3 个/秒（点火 3.8、Light 5.3），前奏也照样快切"}


def plan_cuts_cfg(series):
    """镜头表切点密度的系列参数（gates.plan_cuts，没写 = CHROMA）：need_per_s = 计划切点的线（每秒几刀，再按这首歌全用 16th 的 65 % 封顶）；
    hook_16th = 开头那一镜必须 16th（CHROMA 第 1 秒 10 格）；gap_fix = 相邻两镜都一刀不切时提一镜的档；ref = 发回导演时说的原片参照。"""
    return dict(PLAN_CUTS, **(((series or {}).get("gates") or {}).get("plan_cuts") or {}))


def plan_density(sl, mm, opt=None):
    """(按开始帧排好的镜头, 每镜全用 16th 的切点, 现在每秒几刀, 全用 16th 每秒几刀, 线)。
    线按这首歌能给的封顶 = min(need_per_s, 全片都用 16th 时的 65 %)：CHROMA need_per_s = 3.0（原片 3.0–5.3/s：点火 3.8、Light 5.3）。"""
    o = dict(PLAN_CUTS, **(opt or {}))
    shots = sorted(sl["shots"], key=lambda s: s["start_f"])
    dur = sl["total_f"] / 60
    c16 = {s["id"]: music_cuts(s["start_f"], s["end_f"], "16th", mm) for s in shots}
    have = (len({x for s in shots for x in s["cuts_f"]}) + len(shots) - 1) / dur
    full = (len({x for v in c16.values() for x in v}) + len(shots) - 1) / dur
    return shots, c16, have, full, min(float(o["need_per_s"]), 0.65 * full)


def densify(raw, bm, mm, opt=None):
    """导演选的切点档位太稀时，编排器直接提档，不再靠提示词劝（DeepSeek 导演两轮对话都不肯把前奏从 beat / none 提上去，
    镜头表停在 2.64/s）。开头那一镜定成 16th（钩子）；整片不到线时，每次挑「提到 16th 多出刀数最多」的那一镜提档，最后一镜（海报）最后才动。
    导演只管每镜做什么，密度是音乐给的，所以由编排器定。原地改 raw，返回改动说明。"""
    if not mm or not raw.get("shots"):
        return []
    notes = []
    for _ in range(len(raw["shots"]) + 1):
        try:
            sl = expand_shotlist(raw, bm, bm["sections"], mm)
        except ValueError:
            return notes                                              # 镜头表本身有错：交给 errs 报
        shots, c16, have, full, need = plan_density(sl, mm, opt)
        if dict(PLAN_CUTS, **(opt or {}))["hook_16th"] and _cut_level(shots[0]) != "16th":
            pick, why = shots[0], "开头那一镜是钩子"
        elif have < need:
            cand = [s for s in shots[:-1] if _cut_level(s) != "16th"] or [s for s in shots if _cut_level(s) != "16th"]
            if not cand:
                break
            pick = max(cand, key=lambda s: len(c16[s["id"]]) - len(s["cuts_f"]))
            why = f"整片切点 {have:.2f}/s，线 {need:.2f}/s"
        elif not dict(PLAN_CUTS, **(opt or {}))["gap_fix"]:
            break
        else:                                                         # 相邻两镜都一刀不切（最后一镜海报除外）：提能多出刀数多的那一镜
            gap = [(a, b) for a, b in zip(shots[:-2], shots[1:-1]) if not a["cuts_f"] and not b["cuts_f"]
                   and (len(c16[a["id"]]) or len(c16[b["id"]]))]
            if not gap:
                break
            pick = max(gap[0], key=lambda s: len(c16[s["id"]]))
            why = f"{gap[0][0]['id']}、{gap[0][1]['id']} 连着两镜一刀不切"
        if _cut_level(pick) == "16th":
            break
        r = next(x for x in raw["shots"] if x["id"] == pick["id"])
        notes.append(f"{r['id']} {r.get('cut_every')} → 16th（{why}）")
        r["cut_every"] = "16th"
    return notes


def plan_shape_errs(sl):
    """镜头长度：开头那一镜（钩子）≤1 小节，其余 ≤2 小节。一镜只有一个主体，排长了代理只能对着同一个东西换角度原地耗
    （11-04 Codex 试跑：s01 排了 3 小节 / 5 s，有效运动量 1.4）。"""
    out = []
    for j, s in enumerate(sorted(sl["shots"], key=lambda s: s["start_f"])):
        n, cap = s["bars"][1] - s["bars"][0], (1 if j == 0 else 2)
        if n > cap:
            out.append(f"{s['id']} 排了 {n} 小节（{(s['end_f'] - s['start_f']) / 60:.1f} s）：" + ("开头那一镜是钩子，只给 1 小节" if j == 0 else "每镜最多 2 小节")
                       + "——一镜只有一个主体，撑不了更久，拆成几镜、换主体")
    return out


# 计划门禁：镜头表就把「不动」拦下（核实者 fix_plan 第 9 条）。只拦明确的静止词；屏息那一拍、片尾海报除外。
STILL_WORDS = ("停稳", "定住", "归零", "几乎不动", "越来越慢", "悬着")
STILL_NEG = ("不", "没", "别", "勿", "绝不", "不会", "不等", "不许", "不要", "无需")   # 「不等停稳就切」「不会越来越慢」是反着说的
NUM_WORDS = ("读数", "数字", "计数", "温度", "刻度", "示数", "指针", "°", "K", "℃", "数")  # 「读数归零」说的是数字，不是画面停


def still_hits(cl):
    """一句话里真的在说「停 / 不动」的静止词（前 1–3 个字是否定的不算；「归零」和读数、温度连用的不算）。"""
    out = []
    for w in STILL_WORDS:
        for m in re.finditer(re.escape(w), cl):
            pre = cl[max(0, m.start() - 3):m.start()]
            if any(pre.endswith(n) or n in pre for n in STILL_NEG):
                continue
            if w == "归零" and any(n in cl for n in NUM_WORDS):
                continue
            out.append(w)
            break
    return out
_NONE = ("", "none", "无", "null", "—", "-", "n/a")
AUX_MAX = 4


def _has_name(n, text):
    """text 里写了素材名 n：F 号照旧（F05 后面不跟数字）；request_id（REQ_A1）要整个词（不把 REQ_A1_LOOP 认成它）。"""
    tail = r"(?![0-9])" if re.fullmatch(r"F\d{2,3}", str(n)) else r"(?![A-Za-z0-9_])"
    return bool(re.search(rf"(?<![A-Za-z0-9_]){re.escape(str(n))}{tail}", str(text or "")))


def _world_names(s, names=None):
    """这一镜的世界层 / 辅助素材里认得出的素材名（footage 文本里出现的名字 + aux_footage）。names = None 时只看 aux_footage。
    names 可以带镜头表 footage_wish 的 request_id（还没回填成 F 号的待补素材）。"""
    fo = str(s.get("footage") or "")
    got = [n for n in (names or []) if _has_name(n, fo)]
    return list(dict.fromkeys(got + [str(a) for a in s.get("aux_footage") or [] if str(a).strip()]))


def has_world(s, names=None, world_parts=()):
    """镜头写了世界层：footage 不是 none（有素材清单时要写清单里的名字或 aux_footage）；world_parts 给了时，parts 里带了其中一个也算。"""
    if world_parts and set(map(str, s.get("parts") or [])) & set(world_parts):
        return True
    fo = str(s.get("footage") or "").strip()
    if fo.lower() in _NONE and not s.get("aux_footage"):
        return False
    return True if not names else bool(set(_world_names(s, names)) & set(names))


def plan_motion_errs(sl, names=None, beat_f=None, opt=None):
    """(a) action / event 里出现静止词（停稳 / 定住 / 归零 / 几乎不动 / 越来越慢 / 悬着）就拦，屏息那一拍（同一句写明「屏息」、
    aux_footage 最多 AUX_MAX 条、都在素材清单里（「event 写明每个切点换哪层」11-09 起不查：文字规则拦过 T1 的正确写法，交给审片看画面判）。
    硬错误（发回导演）。names = 素材清单里的名字 + 镜头表的 request_id（空 = 没有实拍，(c) 不查）。线的数字不写进给导演的文字，只给原片参照。"""
    shots = sorted(sl.get("shots") or [], key=lambda s: s["start_f"])
    if not shots:
        return []
    names = list(names or [])
    out = []
    poster = lambda s: s is shots[-1] or any(h.get("kind") == "final" for h in s.get("hits") or [])  # noqa: E731
    for s in shots:
        if poster(s):
            continue
        breath = any(h.get("kind") == "breath" for h in s.get("hits") or []) or bool(s.get("holds"))
        for fld in ("action", "event"):
            for cl in re.split(r"[，。；;,!！？?\n]", str(s.get(fld) or "")):
                hit = still_hits(cl)
                if hit and not (breath and "屏息" in cl):
                    out.append(f"{s['id']} 的 {fld} 写了「{'、'.join(hit)}」（「{cl.strip()[:40]}」）：镜头里不许停下来——从入画到出画一直有一件单向的事在走"
                               "（推近、横移、长出来、裂开、世界从主角身后流过、素材变速往前跑）；停只留给屏息那一拍（同一句里写明「屏息」）和片尾海报")
    for s in shots:
        aux = s.get("aux_footage")
        if aux in (None, []):
            continue
        if not isinstance(aux, list):
            out.append(f"{s['id']}：aux_footage 要写成素材名的列表（如 [\"F05\", \"F12\"]）")
            continue
        bad = [a for a in aux if names and a not in names]
        if bad:
            out.append(f"{s['id']}：aux_footage 里 {bad} 不在素材清单里（写清单里的名字，如 F05，或 footage_wish 里的 request_id）")
        if len(aux) > AUX_MAX:
            out.append(f"{s['id']}：aux_footage 最多 {AUX_MAX} 条，现在 {len(aux)} 条")
    return out


def plan_source_errs(sl, clips):
    """11-05 第三轮第 17 条：CG 科普片 / 带烧录字幕的素材（inputs/footage.json 的 cg / burned_text，素材包 flags 标的，如 F34、F35）
    不当主体（subject_src.file）——当辅助、取景避开字幕可以。硬错误（发回导演）。"""
    bad = {x["name"]: x for x in clips or [] if x.get("cg") or x.get("burned_text")}
    out = []
    for s in sl.get("shots") or []:
        f = (s.get("subject_src") or {}).get("file")
        if f in bad:
            why = "、".join(w for w, b in (("CG 科普片", bad[f].get("cg")), ("画面带烧录字幕", bad[f].get("burned_text"))) if b)
            out.append(f"{s['id']} 的主体素材 {f} 是{why}：不当主体（放大就糊、字幕露出来；用户要实拍）。主体换一条实拍，"
                       f"{f} 只能当辅助（aux_footage），取景避开字幕")
    return out


def plan_density_errs(sl, mm, opt=None):
    """镜头表的切点密度要接近原片：11-04 导演照「前奏音乐稀、切点自然少」把前奏 6.6 s 排成 3 刀、整片 1.66/s，原片 3.0–5.3/s、
    第 1 秒 7–9 个构图。开头那一镜必须 16th（钩子）。densify 先自动提档，这里只剩提满了还不够的情况。
    只查计划：导演只选档位，切点由编排器从起音取，所以这里写数字不会让代理去凑；不进给制作代理的文字。"""
    if not mm or not sl.get("shots"):
        return []
    o = dict(PLAN_CUTS, **(opt or {}))
    shots, c16, have, full, need = plan_density(sl, mm, o)
    ce = _cut_level
    out = []
    if o["hook_16th"] and ce(shots[0]) != "16th":
        out.append(f"开头那一镜 {shots[0]['id']} 是钩子：cut_every 用 \"16th\"（原片第 1 秒 7–9 个构图），现在是 {shots[0].get('cut_every')}")
    if have < need:
        low = [f"{s['id']}（{s.get('cut_every')}：{len(s['cuts_f'])} 刀，换 16th 有 {len(c16[s['id']])} 刀）" for s in shots if ce(s) != "16th"]
        out.append(f"计划切点 {have:.2f} 个/秒，太稀：{o['ref']}，这首歌全用 16th 能到 {full:.2f}。"
                   "把下面几镜的 cut_every 往上提（8th 或 16th；只有屏息那一拍、最后海报那一镜可以少切）：" + "；".join(low))
    return out


# ============================================================================ 转场声明
def norm_tx(t):
    """transition_in 统一成 {kind, eye, why, slug, handoff:{out, at, in}}（旧的一句话写法放进 why）。"""
    if isinstance(t, dict):
        d = {k: str(t[k]).strip() for k in ("kind", "eye", "why", "slug") if t.get(k) not in (None, "")}
        h = t.get("handoff")
        if isinstance(h, dict):
            h = {k: str(h[k]).strip() for k in ("out", "at", "in") if h.get(k) not in (None, "")}
            if h:
                d["handoff"] = h
        return d
    return {"why": str(t).strip()} if t else {}


def handoff_text(h):
    h = h or {}
    return f"出画「{h.get('out', '?')}」→ {h.get('at', '?')} → 入画「{h.get('in', '?')}」" if h else "没写交接"


def tx_text(t):
    t = norm_tx(t)
    return "｜".join([t.get("kind") or "没写 kind"] + [f"{n}：{t[k]}" for k, n in (("eye", "眼睛"), ("why", "为什么")) if t.get(k)]
                    + ([f"交接：{handoff_text(t['handoff'])}"] if t.get("handoff") else []))


def transitions_block(tr):
    """镜头表提示词里 transition_in 的写法（≤15 行），从系列配置 transitions.kinds 生成。"""
    lines = ["- transition_in：{kind, eye, why, slug}，从上一镜怎么接进来（s01 写片尾怎么接回第 0 帧）。kind 只能选（括号里是条目名）："]
    lines += [f"  · {k}（{v['slug']}）：{v['say']}" for k, v in tr["kinds"].items()]
    lines += ["  不收：闪黑、闪白、淡入淡出、叠化（全局 6）。",
              "  eye：眼睛跟着什么过切点，写成「上一镜结尾的 X → 下一镜开头的 Y」；why：换时间 / 换地点 / 换情绪 / 接意思 / 卡大拍，五选一再补一句；"
              "slug：可选，填括号里的条目或 playbook 别的条目。",
              '  好：{"kind": "对位", "eye": "NEON 的 N 斜笔 → 招牌灯管的主线，同一位置", "why": "接意思：字变成灯管", "slug": "match-cut"}、'
              '{"kind": "冲击帧盖切点", "eye": "招牌中心 → 冲击帧圆心 → 主角物", "why": "卡大拍：DROP"}；坏：{"kind": "淡入", "eye": "下一镜", "why": "过渡"}']
    return "\n".join(lines)


def expand_shotlist(raw, bm, sections, mm=None):
    """mm = 音乐打击点表：镜头边界（小节线）有起音时用起音帧，镜头内切点从音乐起音里取（music_cuts）；没有就照旧按格均分。"""
    shots = sorted(raw["shots"], key=lambda s: s["bars"][0])
    ids = [s["id"] for s in shots]
    if len(set(ids)) != len(ids):
        raise ValueError("镜头 id 重复")
    secs = {s["id"] for s in sections}
    auto = []
    for h in bm["hits"]:
        if h["kind"] == "breath":
            auto.append((h["bar"] - 1) * 4 + h["beat"] - 1)
        if h["kind"] == "drop":
            auto.append((h["bar"] - 1) * 4 + h["beat"] - 1)
    out = []
    for s in shots:
        a, b = s["bars"]
        if b <= a or a < 1 or b > bm["bars"] + 1:
            raise ValueError(f"{s['id']} 小节 {s['bars']} 不对（结束不含，最大 {bm['bars'] + 1}）")
        if s["section"] not in secs:
            raise ValueError(f"{s['id']} 的段落 {s['section']} 不在节拍表里")
        holds = set(auto) | {(hb[0] - 1) * 4 + hb[1] - 1 for hb in s.get("holds") or []}
        ce = CUT_ALIAS.get(s["cut_every"], s["cut_every"])
        if ce not in STEP:
            raise ValueError(f"{s['id']} 的 cut_every「{s['cut_every']}」不认识（只能是 {' / '.join(STEP)}）")
        a_f = 0 if a <= 1 else slot_F(bm, mm, a)                     # 第一镜从第 0 帧起（第 0 格起音晚几 ms 也不挪，和片尾 total_f 对称）
        b_f = bm["total_f"] if b > bm["bars"] else slot_F(bm, mm, b)
        cuts = []
        if mm:
            cuts = music_cuts(a_f, b_f, ce, mm, holds)
        elif STEP[ce]:
            st, p = STEP[ce], (a - 1) * 4 + STEP[ce]
            while p < (b - 1) * 4 - 1e-9:
                if not any(hb < p < hb + 1 for hb in holds):
                    cuts.append(int(math.floor(p * bm["beat_f"] + 0.5)))
                p += st
        e = dict(s, start_f=a_f, end_f=b_f, cuts_f=cuts, transition_in=norm_tx(s.get("transition_in")))
        if "subject_src" in s:
            e["subject_src"] = norm_src(s.get("subject_src"))
        e["hits"] = [{"F": h["F"], "kind": h["kind"], "what": h.get("what", "")} for h in bm["hits"] if e["start_f"] <= h["F"] < e["end_f"]]
        out.append(e)
    for e, nxt in zip(out, out[1:] + out[:1]):                     # 出点 = 下一镜的入点（最后一镜接回 s01：首尾循环）
        e["transition_out"] = dict(nxt["transition_in"], to=nxt["id"])
    d = {"total_f": bm["total_f"], "fps": 60, "shots": out}
    for k in ("route", "signature", "deviations"):                    # 规则树：转场路线、签名镜头、默认值偏离的理由
        if raw.get(k) not in (None, "", [], {}):
            d[k] = raw[k]
    return d


def plan_errors(bm, sl, series):
    """(checks, 不合格, 建议)：fail = 工程底线，发回重做；warn = 规则，发回提醒一次、改不改都收。"""
    checks = G.plan_checks(bm, sl, series)
    say = lambda c: f"{c['name']}：{c['value']}（要求 {c['want']}）" + ("；" + "；".join(c["where"]) if c.get("where") else "")  # noqa: E731
    return checks, [say(c) for c in checks if c["status"] == "fail"], [say(c) for c in checks if c["status"] == "warn"]


# ============================================================================ 全片分配（主线、重复、无主体）
# 导演在镜头表里把数字链每一站、每个大词、每个意象、每段主体素材逐镜分配写死；制作代理只准用分给自己的。
# 三档：底线（不重复、数字不回头、有主体）和必做（主体来源、主体动作、每个切点的交接）不合格发回重做；长相不在这里管。
SUBJECT_KINDS = ("实拍", "生图")
_KIND_ALIAS = {"footage": "实拍", "video": "实拍", "实拍素材": "实拍", "image": "生图", "gen": "生图", "ai生图": "生图"}


def norm_src(src):
    """别的附加键如 requires_rebind、asset_id 丢掉）。"""
    if not isinstance(src, dict):
        return {"kind": str(src or "").strip()} if src else {}
    d = {k: str(src[k]).strip() for k in ("kind", "file", "gen", "request_id") if src.get(k) not in (None, "")}
    if d.get("kind"):
        d["kind"] = _KIND_ALIAS.get(d["kind"].lower().replace(" ", ""), d["kind"])
    return d


def _up(s):
    s = re.sub("[−–—]", "-", str(s or "").strip().upper()).replace("℃", "°C")
    return re.sub(r"\s+", " ", s)


def _nk(s):
    return re.sub(r"\s+", "", _up(s))


def ladder(topic):
    """开题的数字链 → [{i, text, what, full, val}]（full / val 是比对用的归一写法）。"""
    out = []
    for i, x in enumerate((topic or {}).get("number_ladder") or []):
        text = f"{x.get('value', '')} {x.get('unit', '')}".strip()
        out.append({"i": i, "text": text, "what": x.get("what", ""), "full": _nk(text), "val": _nk(x.get("value", ""))})
    return out


def station_of(v, lad):
    """number 字段 → 第几站（0 起）；空 → None；认不出 → -1。可写数字链原文（如 "100 °C"）、只写数值（不重名时），或序号。"""
    if v is None or isinstance(v, bool) or (isinstance(v, str) and not v.strip()):
        return None if not isinstance(v, bool) else -1
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v) if float(v).is_integer() and 0 <= v < len(lad) else -1
    k = _nk(v)
    full = [st["i"] for st in lad if k == st["full"]]
    if full:
        return full[0]
    vals = [st["i"] for st in lad if k == st["val"]]
    return vals[0] if len(vals) == 1 else -1


def stations_in(text, lad, loose=True):
    """屏上字里提到了数字链的哪几站：带单位的整写（"0 °C"）都算；loose 时数值本身 ≥2 位数字、只写数值也算（镜头表的字），
    不 loose 时只有整个字符串就是那个数才算（查代码里的字符串：'F18'、'#18FFAA' 这种不误报）。"""
    t, hits = _nk(text), set()
    if not t:
        return hits
    for st in lad:
        if st["full"] != st["val"] and re.search(rf"(?<![\d.]){re.escape(st['full'])}", t):
            hits.add(st["i"])
            continue
        v = st["val"].lstrip("-+")
        if loose and len(re.sub(r"\D", "", v)) >= 2 and re.search(rf"(?<![\d.]){re.escape(v)}(?![\d.])", t):
            hits.add(st["i"])
        elif not loose and len(re.sub(r"\D", "", v)) >= 2 and re.fullmatch(rf"[-+]?{re.escape(v)}", t):
            hits.add(st["i"])
    return hits


def shot_words(s):
    """这一镜分到的大词（hero_word + words，归一成大写、去重）。"""
    return list(dict.fromkeys(w for w in (_up(x) for x in [s.get("hero_word")] + list(s.get("words") or [])) if w))


ALLOC_RIDS = ("root.arc.do1", "beat.do2", "root.type.floor1", "root.subject.do1", "root.assets.do2", "root.assets.do3",
              "root.camera.do1", "root.assets.do4", "beat.do1", "root.rhythm.floor2", "root.transition.do2")


def subject_dups(shots, names):
    """fill_wishes 发回那一轮拿回填后的镜头表再查一次（REQ_A1 → F04 和直接写的 F04 算同一段）。"""
    use = {}
    for s in shots:
        src = norm_src(s.get("subject_src"))
        f = str(src.get("request_id") or src.get("file") or "")
        if (src.get("kind") == "实拍" or (src.get("kind") == "生图" and f)) and f in names:
            use.setdefault(f, []).append(s.get("id"))
    return [f"主体素材 {f} 分给了 {'、'.join(ids)}：一段素材只当一次主体" for f, ids in use.items() if len(ids) > 1]


def alloc_check(sl, topic, names, series, tags=None, partial=False):
    """全片分配的底线和必做（不合格发回导演重做）。sl = expand_shotlist 的结果；names = 素材清单里的名字。
    返回 (不合格, 建议)。tags = 列表时，每条不合格按规则树 id 记进去（(rid, 一句话)，给 gates_tree.json）。
    partial = 试跑短片（只做原曲窗口的前几小节）：数字链可以只分前几站（照样按顺序、不回头、不重复），没分完的只提醒。"""
    lad, title = ladder(topic), _up((topic or {}).get("screen_title"))
    shots, hard, soft = sl["shots"], [], []
    tags = [] if tags is None else tags

    class _H(list):
        rid = None

        def append(self, msg):                                       # hard.append 照旧用；顺手按当前段落记规则 id
            super().append(msg)
            for r in (self.rid if isinstance(self.rid, tuple) else (self.rid,)):
                if r:
                    tags.append((r, msg))

        def extend(self, msgs):
            for m in msgs:
                self.append(m)

        def __iadd__(self, msgs):
            self.extend(msgs)
            return self
    hard = _H()
    hard.rid = "root.type.floor1"
    # 大词：全片不重复（片名可以 2 镜：开头海报、结尾海报）；数字不进大词
    use = {}
    for s in shots:
        ws = shot_words(s)
        dig = [w for w in ws if re.search(r"\d", w) and w != title]
        if dig:
            hard.append(f"{s['id']}：大词里有数字 {dig}——数字只写在 number（label 可以放这一镜自己的那一站）")
        for w in ws:
            use.setdefault(w, []).append(s["id"])
    ends = {shots[0]["id"], shots[-1]["id"]} if shots else set()
    for w, ids in use.items():
        if w == title and (len(ids) == 1 or (len(ids) == 2 and set(ids) == ends)):
            continue                                                  # 片名：首尾两镜是同一张海报（首尾循环），算一次
        if len(ids) > 1:
            hard.append(f"大词「{w}」用在 {'、'.join(ids)}：全片大词不重复" + ("（片名只放首尾两镜：开头海报、结尾海报）" if w == title else "，每个词只分给一镜"))
    # 数字链：每一站分给一镜，按顺序、不回头、不重复；别的镜的数字不出现在这一镜的屏上字里
    hard.rid = ("root.arc.do1", "beat.do2")
    seq, owner = [], {}
    for s in shots:
        k = station_of(s.get("number"), lad)
        if k == -1:
            hard.append(f"{s['id']}：number「{s.get('number')}」不在数字链里（写数字链原文：{' / '.join(x['text'] for x in lad)}；没有就写 \"\"）")
        elif k is not None:
            seq.append((s["id"], k))
            owner.setdefault(k, s["id"])
    for (a, i), (b, j) in zip(seq, seq[1:]):
        if j <= i:
            hard.append(f"数字回头或重复：{a} 第 {i + 1} 站 {lad[i]['text']} → {b} 第 {j + 1} 站 {lad[j]['text']}（数字按顺序走，一站只分给一镜）")
    miss = [x for x in lad if x["i"] not in owner]
    if miss and lad:
        msg = "数字链没分完：" + "、".join(f"第 {x['i'] + 1} 站 {x['text']}" for x in miss) + "（每一站分给一镜，一镜最多一站）"
        if partial:
            soft.append(msg + "——试跑短片可以只分前几站，照样按顺序")
        else:
            hard.append(msg)
    for s in shots:
        k = station_of(s.get("number"), lad)
        for fld, txt in [("hero_word", s.get("hero_word")), ("label", s.get("label"))] + [("words", w) for w in s.get("words") or []]:
            for j in sorted(stations_in(txt, lad) - {k}):
                hard.append(f"{s['id']} 的 {fld}「{txt}」是数字链第 {j + 1} 站 {lad[j]['text']}"
                            + (f"（分给了 {owner[j]}）" if j in owner else "") + "：别的镜的数字不能出现，这一镜的数字写在 number")
        for h in s.get("hits") or []:                                # 节拍表卡点的说明写了别的数：只提醒（数字以 number 为准）
            for j in sorted(stations_in(h.get("what"), lad) - {k}):
                soft.append(f"{s['id']} 第 {h['F']} 帧的卡点写的是 {lad[j]['text']}，number 分的是 {s.get('number') or '无'}：要么把那一站分给这一镜，要么就这样（制作时屏上数字以 number 为准）")
    for s in shots:
        src = s.get("subject_src") or {}
        kind, f = src.get("kind"), str(src.get("request_id") or src.get("file") or "")   # 待补素材按 request_id 算（names 带上它）
        hard.rid = "root.subject.do1"
        if kind not in SUBJECT_KINDS:
            hard.append(f"{s['id']}：subject_src.kind 要写 实拍 / 生图（主体是一个真东西；大字、雾、霜纹这类肌理不算主体），现在是 {kind!r}")
        elif kind == "实拍" or (kind == "生图" and f):
            hard.rid = "root.assets.do2"
            if f not in names:
                gone = any(m.get("id") == f for m in s.get("missing_footage") or [] if isinstance(m, dict))
                hard.append(f"{s['id']}：{kind}主体" + (f"「{f}」不在素材清单里" if f else "没写 file") +
                            ("（看图确认后仍没找到，已交制作代理自己选路）" if gone else      # 复核 L4
                             "（写清单里的名字，如 F01；清单里没有的写 request_id，同一个 id 在 footage_wish 里写 need + 英文 query，开拍前编排器去找）"))
        elif kind == "生图" and len(str(src.get("gen") or "").strip()) < 6:
            hard.rid = "root.assets.do3"
            hard.append(f"{s['id']}：生图主体要写 gen（给生图模型的一句话：主体、材质、光、构图），或写素材清单里现成的图名")
        hard.rid = "root.camera.do1"
        if len(str(s.get("action") or "").strip()) < 4:
            hard.append(f"{s['id']}：action 要写主体在这一镜做的一个动作（砸下、裂开、旋入、升起……；「展示」「推近」不算）")
    hard.rid = "root.subject.do1"
    hard += subject_dups(shots, names)
    # 意象：全片 ≤ same_image_max 次
    hard.rid = "root.rhythm.floor2"
    mx, imgs = series["gates"].get("same_image_max", 2), {}
    for s in shots:
        if _up(s.get("image")):
            imgs.setdefault(_up(s.get("image")), []).append(s["id"])
    hard += [f"意象「{k}」用了 {len(v)} 次（{'、'.join(v)}）：同一意象全片 ≤{mx} 次" for k, v in imgs.items() if len(v) > mx]
    # 交接：每个切点写明出画的东西、方向 / 位置、入画的东西（s01 = 片尾接回第 0 帧）
    hard.rid = "root.transition.do2"
    for s in shots:
        h = (s.get("transition_in") or {}).get("handoff") or {}
        bad = [k for k in ("out", "at", "in") if len(str(h.get(k) or "").strip()) < 2]
        if bad:
            hard.append(f"{s['id']}：transition_in.handoff 缺 {bad}（out = 上一镜出画的东西，at = 方向 / 位置，in = 这一镜入画的东西"
                        + ("；s01 写片尾怎么接回第 0 帧" if s is shots[0] else "") + "）")
    return list(hard), soft            # 拍上硬切写理由：规则树必做（tree.shot_eval 的 root.transition.do3，硬）


def alloc_rows(t, tags, stage="镜头表"):
    """全片分配（alloc_check）覆盖的规则：每条 id 一行，有不合格就是没做到。"""
    out = []
    for rid in ALLOC_RIDS:
        if rid not in t["_idx"]:
            continue
        msgs = [m for r, m in tags if r == rid]
        out.append(TR.row(t, rid, not msgs, msgs, how="全片分配（stages.alloc_check）", stage=stage))
    return out


def apply_alloc(sl, topic):
    """导演定稿后：number 换成数字链原文 + 序号 + 是什么，镜头表顶上加一张分配总表（制作、审片、报告都读它）。"""
    lad, title = ladder(topic), _up((topic or {}).get("screen_title"))
    for s in sl["shots"]:
        k = station_of(s.get("number"), lad)
        if k is not None and k >= 0:
            s.update(number=lad[k]["text"], number_i=k, number_what=lad[k]["what"])
        else:
            s.update(number="", number_i=None)
            s.pop("number_what", None)
    sl["alloc"] = {
        "title": title,
        "numbers": [{"i": x["i"], "text": x["text"], "what": x["what"],
                     "shot": next((s["id"] for s in sl["shots"] if s.get("number_i") == x["i"]), None)} for x in lad],
        "words": {s["id"]: shot_words(s) for s in sl["shots"]},
        "subjects": {s["id"]: dict(s.get("subject_src") or {}, subject=s.get("subject", ""), action=s.get("action", ""),
                                   **({"world": s["footage"]} if str(s.get("footage") or "").strip().lower() not in _NONE else {}),
                                   **({"aux_footage": list(s["aux_footage"])} if s.get("aux_footage") else {}))
                     for s in sl["shots"]},
        "gen_requests": [{"shot": s["id"], "subject": s.get("subject", ""),
                          "gen": _no_mush((s.get("subject_src") or {}).get("gen", ""))}
                         for s in sl["shots"] if (s.get("subject_src") or {}).get("kind") == "生图"
                         and not (s.get("subject_src") or {}).get("file")],
    }
    return sl


def _no_mush(gen):
    """根「画质」必做②：生图提示词里写「不要麻麻赖赖」（同光同影、有体积、不像贴图）——编排器自己补上。"""
    g = str(gen or "").strip()
    return g if (not g or "麻麻赖赖" in g) else g.rstrip("。.") + "；同光同影、有体积、不像贴图，不要麻麻赖赖"


def alloc_md(sl):
    rows = ["| 镜 | 数字 | 大字 | 主体（来源） | 动作 | 交接（入点） |", "|---|---|---|---|---|---|"]
    for s in sl["shots"]:
        src = s.get("subject_src") or {}
        wl = str(s.get("footage") or "").strip()
        world = ("；世界层 " + wl if wl.lower() not in _NONE and wl != src.get("file") else "") + \
            ("；辅助 " + "、".join(s["aux_footage"]) if s.get("aux_footage") else "")
        rows.append(f"| {s['id']} | {s.get('number') or '—'} | {' / '.join(shot_words(s))} | {s.get('subject', '')}"
                    f"（{src.get('kind', '?')} {src.get('file') or src.get('gen') or ''}{world}） | {s.get('action', '')} | "
                    f"{(s.get('transition_in') or {}).get('kind', '?')}：{handoff_text((s.get('transition_in') or {}).get('handoff'))} |")
    return "\n".join(rows)


# ============================================================================ 规则树总账（gates_tree.json）
def tree_save(c, stage, rows):
    """把这一环节查过的规则行记进 <集>/gates_tree.json（同一环节重跑就覆盖），再算一遍总账（根 + 本大类每条规则一行）。"""
    p = c.p("gates_tree.json")
    d = util.read_json(p) or {}
    st = d.get("stages") or {}
    st[stage] = rows
    order = ["开题", "导演", "镜头表", "制作", "成片", "交付"]
    st = {k: st[k] for k in sorted(st, key=lambda k: order.index(k) if k in order else 99)}
    led = TR.ledger(c.tree, c.cat(), [r for v in st.values() for r in v])
    util.write_json(p, dict(led, note="规则树每条规则一行（根 + 本大类）：档位、怎么查、结果、位置。hard = 没做到不放行（计划阶段发回导演、"
                                        "制作阶段发回代理、交付时挡）；warn = 只报警（默认值偏离写理由）；human = 交给审片看图判；na = 还没接检查。",
                            stages=st, at=util.iso()))
    return led


# ============================================================================ 0 开题
def load_env(c):
    """环境体检（根「素材」必做①）：读 orchestrator/doctor.json 的 hints。不在或过了 ttl_h 小时：真跑时先重跑一遍体检
    （tools/doctor.py，约 10–20 秒，输出进 <集>/logs/doctor.log），mock 不跑（有就读）。返回写进 topic.json 的 env。"""
    dc = c.cfg.get("doctor") or {}
    p = os.path.join(util.HERE, dc.get("path", "doctor.json"))
    ttl = float(dc.get("ttl_h", 24))
    age = lambda: (util.now().timestamp() - os.path.getmtime(p)) / 3600 if os.path.exists(p) else None  # noqa: E731
    a, ran = age(), None
    if (a is None or a > ttl) and not c.mock:
        try:
            env = dict(os.environ, PATH=util.BIN_PATH + ":" + os.environ.get("PATH", ""))   # 体检只判环境变量在不在，不读值
            r = subprocess.run([sys.executable, "-I", os.path.join(util.P2, "tools", "doctor.py"), "--out", p], cwd=util.HERE,
                               capture_output=True, text=True, timeout=int(dc.get("timeout_s", 300)), env=env)
            util.write(c.p("logs", "doctor.log"), (r.stdout or "")[-20000:] + (r.stderr or "")[-4000:])
            ran = f"开题前重跑了体检（退出码 {r.returncode}，表在 logs/doctor.log）"
        except Exception as e:  # noqa: BLE001
            ran = f"体检跑不起来：{type(e).__name__}: {str(e)[:120]}"
        a = age()
    d = util.read_json(p) if os.path.exists(p) else None
    if not d:
        return {"read": False, "hints": {}, "ran": ran,
                "note": (ran + "；" if ran else "") + "没有体检结果（orchestrator/doctor.json 不在），按默认路线做（能下素材）"}
    return {"read": True, "path": os.path.relpath(p, util.ROOT), "t": d.get("t"), "age_h": round(a or 0, 1),
            "stale": bool(a and a > ttl), "ran": ran, "hints": d.get("hints") or {}, "user_todo": d.get("user_todo") or []}


def env_text(env, imagegen=None, stock=None):
    """给开题、镜头表看的「制作环境」几行（体检 hints → 路线）。imagegen = 生图通道（imagegen.mode）：codex 时生图那一行改说「已接上」。
    stock = 素材请求通道（stockreq.mode）：net / fake 时实拍那一行改说「缺的自己去找」（footage_wish、开拍前盘点、代理请求）。"""
    if not env.get("read"):
        return "- " + env.get("note", "没有体检结果")
    h = env.get("hints") or {}
    hero_line = "主角物用实拍或生图做，物质用实拍叠层，冲击用画面派生"
    gen = ("没有生图通道：主体用实拍" if h.get("image_gen") == "none" else
           "这一集没开：gen 记成待办，这一镜先用实拍，所以能用实拍就别选生图")
    if imagegen == "codex":
        gen = ("codex 通道已接上：镜头代理在 out/images.json 提请求，编排器在沙箱外用 codex 内置 image_gen 生成（每镜 ≤6、全片 ≤40 张）；"
               "照片和 CG 混用容易「AI 感重」，生图默认只当远景、背景、纹理和主体参考，主体能用实拍就别选生图")
    src = "、".join(h.get("footage_sources") or [])
    foot = ("素材请求通道关着：只用素材清单里本地已有的" if h.get("footage_download") else "不能联网下载：只用素材清单里本地已有的")
    if stock in ("net", "fake"):
        foot = ("素材请求通道已接上" + ("（mock：假站点）" if stock == "fake" else f"（{src or 'Pexels、Wikimedia Commons'}，只收免费授权）")
                + "：缺的真材料写进 footage_needs / footage_wish（中文 need + 英文 query），编排器去找、下、入库；不用向你确认，也不用 2D 画替身")
    lines = [f"- 主角物：{hero_line}", "- 实拍：" + foot,
             f"- 生图：{gen}", "- 配乐：" + ("曲库里有能用的曲子，本集配乐由编排器挑" if h.get("music_download") else "从曲库挑（曲库里还没有能用的曲子时，声音环节会停下）")]
    lines += [f"- 注意：{x}" for x in (h.get("notes") or [])[:4]]
    return "\n".join(lines) + f"\n（体检 {env.get('t')}{'，已过期，按它降级' if env.get('stale') else ''}）"


def norm_topic(o):
    """开题原稿就地规整（代理写数字，405 s 白跑）：number_ladder.value 写成数字的 → 字符串（321.5 → "321.5"，3.0e8 → "300000000"）。"""
    for cd in o.get("candidates") or []:
        for x in (cd.get("number_ladder") if isinstance(cd, dict) else None) or []:
            v = x.get("value") if isinstance(x, dict) else None
            if _num(v):
                x["value"] = str(int(v)) if float(v).is_integer() else str(v)
    return o


def s0_topic(c):
    eps = c.series["episodes"]
    prev = {e["母题"] for e in eps}

    want = getattr(c.args, "motif", None)
    env = load_env(c)
    c.log("环境体检：" + (f"读 {env['path']}（{env['t']}，{env['age_h']} 小时前）；生图 "
                         f"{env['hints'].get('image_gen')}、素材下载 {env['hints'].get('footage_download')}" if env["read"] else env["note"])
          + (f"；{env['ran']}" if env.get("ran") else ""))
    kid = TR.cat_id(c.tree, c.series.get("category"))
    wmin, wwhy = TR.world_min(c.series)
    if want:                                                       # 卡点炫技必做：实拍候选 ≥ 下限（母题已定就先查；不够也不停，开题后自己去找）
        n_c, n_l = candidates_n(c, want)
        if n_c == 0 and wmin > 0 and not SQ.enabled(c):            # 空包又不找素材 = 实拍 0 条，镜头表在环节 1 才挂（rc 5）
            raise util.Stop(2, "bad_args", f"母题「{want}」没有实拍素材（素材包「{inputs.pack_name(c.series, want)}」0 条），素材请求通道又关着"
                                           "（STOCK_OFF=1 / --stock off / 系列 stock.enabled=false）：这个系列要实拍世界层，不开跑。"
                                           "打开素材请求通道（去掉 STOCK_OFF）空包起跑，或者先给这个母题建素材包")
        r0 = TR.candidates_eval(c.tree, kid, n_c, n_l, wmin, wwhy)
        if any(r["status"] == "fail" for r in r0):
            c.log(f"{TR.cat_name(c.tree, kid)}必做：实拍候选 ≥{wmin} 条，素材包「{inputs.pack_name(c.series, want)}」只有 {n_c} 条（本地 {n_l}）："
                  + ("开题后按 footage_needs 自动去找（素材请求通道），不停下来等人" if SQ.enabled(c) else "素材请求通道关着：只记进报告，不停"))

    def check(o):                                                  # 你定的母题、规则树开题必做（大类、出处）：必须
        norm_topic(o)
        errs = []
        if want and not c.mock and o["candidates"][o["chosen"]]["motif"] != want:
            errs.append(f"母题你已定为「{want}」：chosen 指向的候选 motif 必须是「{want}」")
        return errs + TR.hard_msgs(TR.topic_eval(c.tree, o, c.series, env))

    def rules(o):                                                  # 规则：只提醒一次，不拦
        errs = []
        for i, cd in enumerate(o["candidates"]):
            if cd["motif"] in prev:
                errs.append(f"候选 {i} 母题「{cd['motif']}」和往期重复")
            bad = [w for w in cd["lexicon"] + [cd["screen_title"]] if not util.ascii_only(w)] if "zh" not in text_lang(c.series) else []
            if bad:
                errs.append(f"候选 {i} 屏上字要全英文：{bad}")
        one = [k + 1 for k, x in enumerate(o["candidates"][o["chosen"]].get("footage_needs") or []) if not isinstance(x, dict)]
        if one and wmin > 0 and SQ.enabled(c):                     # 复核 G10：只写一句中文，编排器只能猜英文搜索词（试跑时 8 条里 5 条猜不出）
            errs.append(f"选中候选的 footage_needs 第 {'、'.join(map(str, one))} 条只写了一句话：写成 {{need, query, use, seconds}}，"
                        "query 是英文搜索词（编排器照它去免费素材站找）")
        return errs + TR.soft_msgs([r for r in TR.topic_eval(c.tree, o, c.series, env) if r["id"] != "root.assets.do1"])

    prompt = c.prompt("00_topic", SERIES=c.series["name"], N=str(len(eps) + 1),
                      EPISODES="\n".join(f"- 第 {e['n']} 集「{e['片名']}」：母题 {e['母题']}（{e['状态']}）" for e in eps) or "（还没有）",
                      ENV=env_text(env, IG.mode(c), SQ.mode(c)), CATEGORY=TR.catalog_brief(c.series["name"], TR.cat_name(c.tree, kid)) if kid
                      else "没点名系列：照下面四问判大类，拿不准选最接近的一类，category_why 写一句理由。")
    if want:
        prompt += f"\n\n## 你已定\n母题 = 「{want}」：chosen 必须指向它，另两个候选照常给（备选）。"
    o, info = c.llm.ask_json("topic", "topic", c.system, prompt, schema=c.schema("topic"), check=check, soft=rules,
                             ctx={"series": c.series.get("id")})
    if info.get("advice"):
        c.log(f"开题提醒（只提醒，没拦）：{info['advice'][:3]}")
    ch = o["candidates"][o["chosen"]]
    title = getattr(c.args, "title", None)
    rows = TR.topic_eval(c.tree, o, c.series, env)
    cid = TR.cat_id(c.tree, o.get("category")) or kid
    n_c, n_l = candidates_n(c, ch["motif"])
    topic = dict(ch, series=c.series["id"], episode=len(eps) + 1, reason=o["reason"],
                 片名状态="用户起的" if title else "暂定，待你起名", 数字状态="模型给的物理数（每站写了 source），交付前要核实",
                 category=TR.cat_name(c.tree, cid), category_id=cid, category_why=str(o.get("category_why") or ""),
                 differs=str(o.get("differs") or ""), line_kind=str(o.get("line_kind") or ""), env=env,
                 assets_candidates={"candidates": n_c, "local": n_l}, tree=c.tree.get("version"), candidates=o["candidates"])
    if wmin != TR.WORLD_MIN:
        topic["assets_candidates"]["min"] = wmin
    if title:
        topic["title_cn"] = title
    util.write_json(c.p("topic.json"), topic)
    if n_c < wmin and SQ.enabled(c):                               # 规划 A1：不够就按开题的 footage_needs 自动找、下，再数一遍（不再停）
        got = SQ.world_fill(c, topic, wmin, n_c)
        n0 = n_c
        n_c, n_l = candidates_n(c, ch["motif"])
        topic["assets_candidates"].update(candidates=n_c, local=n_l, before=n0,
                                          fill={"asked": len(got), "found": sum(1 for e in got if e.get("status") in SQ.OK_ST)})
        util.write_json(c.p("topic.json"), topic)
        c.log(f"实拍候选：开题前 {n0} 条，按 footage_needs 找了 {len(got)} 条、到了 "
              f"{topic['assets_candidates']['fill']['found']}，现在 {n_c} 条（明细 inputs/stock.json）")
    rows += TR.candidates_eval(c.tree, cid, n_c, n_l, wmin, wwhy)
    led = tree_save(c, "开题", rows)
    c.log(f"开题：母题「{ch['motif']}」，暂定片名「{ch['title_cn']}」，屏上 {ch['screen_title']}（{info['attempts']} 次）；"
          f"大类「{topic['category']}」（{topic['category_why'][:40]}）；实拍候选 {n_c} 条（本地 {n_l}）；规则树 {TR.summary_line(led)[:160]}")
    if any(r["status"] == "fail" for r in rows if r["id"] in ("beat.do1", "root.assets.param1")):
        c.log(f"{topic['category']}必做「实拍候选 ≥{wmin} 条」开题时只报告：现在 {n_c} 条（开题按 footage_needs 每条找 1 条，空包时到不了 {wmin}），"
              "记进 gates_tree.json 和交付报告；镜头表的 footage_wish、开拍前盘点和素材请求通道接着找")


def candidates_n(c, motif):
    """实拍候选数 (搜到的, 本地的)：这一集的素材包（系列 assets.motif 或母题）+ 本集素材包（stock_pack：mock / STOCK_FAKE 下的下载）。"""
    n_c, n_l = inputs.candidates(inputs.pack_dir(c.series, motif))
    for d in inputs.extra_packs(c.ep):
        a, b = inputs.candidates(d)
        n_c, n_l = n_c + a, n_l + b
    return n_c, n_l


def topic_brief(t):
    keep = ("category", "motif", "title_cn", "screen_title", "signature_color", "hero_object", "number_ladder", "lexicon",
            "footage_needs", "why_new", "differs", "line_kind")
    return dumps({k: t[k] for k in keep if k in t})


# ============================================================================ 镜头表的待补素材（空包时镜头表死锁）
# 主体 / 世界层要的素材清单里还没有：镜头表写 request_id（footage_wish 里同一个 id 写 need + 英文 query），校验照收；
# 镜头表通过后编排器先去找（stockreq.resolve_wishes），找到的登记成 F 号、镜头表里的 id 换成 F 号，找不到的发回导演一次。
# 写法：REQ_ 开头、字母数字下划线（REQ_A1、REQ_B2……）。
_RID = re.compile(r"(?<![A-Za-z0-9_])REQ_[A-Za-z0-9_]+(?![A-Za-z0-9_])")
WISH_USE = {"subject_src": "主体", "footage": "世界层", "aux_footage": "辅助", "event": "event 里写了"}


def _ints(xs):
    return all(isinstance(x, (int, float)) and not isinstance(x, bool) and float(x).is_integer() for x in xs)


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def norm_shotlist(o, notes=None):
    """导演原稿的小毛病就地规整、不发回：bars 写了小数（2.0、1.375）→ 取整到最近的小节线（一整轮 594 s 白跑），相邻镜头共用的边界
    跟着挪；取整后变空的镜头从邻镜借 1 小节（首尾不动、总长不变；镜头首尾相接时），借不开（镜头比小节多）的至少留 1 小节、由校验发回；
    规整了几处记进 notes（一条「只报警」的提醒，不为它整轮重问）；
    holds 里整数值的小数 → 整数；subject_src.file / gen / request_id、footage 写 null → ""，aux_footage 写 null → []；subject_src.file
    写成了 REQ_ 开头的 id（request_id 空着）→ 挪进 request_id；反过来 request_id 写成了 F 号（file 空着）→ 挪进 file。
    返回规整不了的（bars 不是两个数）错误。"""
    bad, moved, m = [], [], {}
    shots = [s for s in o.get("shots") or [] if isinstance(s, dict)]
    frac = any(isinstance(s.get("bars"), list) and len(s["bars"]) == 2 and all(map(_num, s["bars"])) and not _ints(s["bars"]) for s in shots)
    seq = sorted([s for s in shots if isinstance(s.get("bars"), list) and len(s["bars"]) == 2 and all(map(_num, s["bars"]))],
                 key=lambda s: float(s["bars"][0]))
    if frac and seq and all(float(seq[i]["bars"][0]) == float(seq[i - 1]["bars"][1]) for i in range(1, len(seq))):
        bd = [int(float(x) + 0.5) for x in [seq[0]["bars"][0]] + [s["bars"][1] for s in seq]]   # 镜头首尾相接：只取整边界
        if bd[-1] - bd[0] >= len(seq):                              # 每镜至少 1 小节借得开：变空的从邻镜借（首尾不动）
            for i in range(1, len(seq)):
                bd[i] = max(bd[i], bd[i - 1] + 1)
            for i in range(len(seq) - 1, 0, -1):
                bd[i] = min(bd[i], bd[i + 1] - 1)
            for s, a2, e2 in zip(seq, bd, bd[1:]):
                if [a2, e2] != s["bars"]:
                    moved.append(f"{s.get('id', '?')} {s['bars']} → [{a2}, {e2}]")
                s["bars"] = [a2, e2]
            frac = False
            seq = []
    for s in seq:
        b = s.get("bars")
        if not frac:
            s["bars"] = [int(x) for x in b] if _ints(b) else b
            continue
        a2 = m.get(b[0], int(float(b[0]) + 0.5))                   # 原边界 → 取整后的（m：相邻镜头共用的边界一起挪）
        e2 = max(m.get(b[1], int(float(b[1]) + 0.5)), a2 + 1)
        m[b[0]], m[b[1]] = a2, e2
        if [a2, e2] != b:
            moved.append(f"{s.get('id', '?')} {b} → [{a2}, {e2}]")
        s["bars"] = [a2, e2]
    if moved and notes is not None:
        notes.append("bars 写了小数，编排器取整到最近的小节线（只报警）：" + "、".join(moved)
                     + "。镜头边界落在小节线上，想在小节中间换画面用这一镜 cut_every 的切点")
    for s in shots:
        if isinstance(s.get("holds"), list):
            s["holds"] = [[int(x) for x in h] if isinstance(h, list) and _ints(h) else h for h in s["holds"]]
        src = s.get("subject_src")
        if isinstance(src, dict):
            for k in ("file", "gen", "request_id"):
                if k in src and src[k] is None:
                    src[k] = ""
            if not str(src.get("request_id") or "").strip() and _RID.fullmatch(str(src.get("file") or "").strip()):
                src["request_id"], src["file"] = str(src["file"]).strip(), ""
            elif not str(src.get("file") or "").strip() and re.fullmatch(r"F\d{2,3}", str(src.get("request_id") or "").strip()):
                src["file"] = str(src.pop("request_id")).strip()
        if "footage" in s and s["footage"] is None:
            s["footage"] = ""
        if "aux_footage" in s and s["aux_footage"] is None:
            s["aux_footage"] = []
    return bad


def wish_index(shots):
    """footage_wish 里带 request_id 的需求 → ({id: 需求（带 shot = 写在哪一镜）}, 错误)。缺 need / query、同一个 id 写了两条不一样的需求算错。"""
    idx, bad = {}, []
    for s in shots:
        for w in s.get("footage_wish") or []:
            rid = str(w.get("request_id") or "").strip() if isinstance(w, dict) else ""
            if not rid:
                continue
            need, q = str(w.get("need") or "").strip(), str(w.get("query") or "").strip()
            miss = [k for k, v in (("need", need), ("query", q)) if not v]
            if miss:
                bad.append(f"{s['id']} 的 footage_wish「{rid}」缺 {' / '.join(miss)}（need = 中文一句话要什么画面，query = 英文搜索词）")
            old = idx.get(rid)
            if old and (old["need"], old["query"]) != (need, q):
                bad.append(f"request_id「{rid}」在 {old['shot']}、{s['id']} 写了两条不一样的需求：一条需求一个 id，全集唯一")
            elif not old:
                idx[rid] = dict(w, request_id=rid, need=need, query=q, shot=s["id"])
    return idx, bad


def wish_refs(shots, rids=()):
    """镜头里引用的 request_id → {id: [(镜, 字段)]}：subject_src.request_id（file 写成 id 的 norm_shotlist 已挪过去）、footage 文本、aux_footage。
    rids = footage_wish 里写了的 id；没写需求、REQ_ 开头的也算引用（校验时报错）。只在 event 里提到的不算（代理把世界层
    写在 event 或自创的键里时，找来了也分不到哪一镜；wish_unused 提醒它写进 footage）。"""
    out, known = {}, set(rids)
    one = lambda v: v in known or bool(_RID.fullmatch(v))  # noqa: E731
    for s in shots:
        src = s.get("subject_src") if isinstance(s.get("subject_src"), dict) else {}
        v = str(src.get("request_id") or "").strip()
        if v:
            out.setdefault(v, []).append((s["id"], "subject_src"))
        fo = str(s.get("footage") or "")
        for v in dict.fromkeys(_RID.findall(fo) + [r for r in known if _has_name(r, fo)]):
            out.setdefault(v, []).append((s["id"], "footage"))
        for a in s.get("aux_footage") or []:
            if one(str(a).strip()):
                out.setdefault(str(a).strip(), []).append((s["id"], "aux_footage"))
    return out


def wish_unused(shots, idx):
    """footage_wish 里写了 request_id、却没有一镜的 subject_src / footage / aux_footage 用它（建议，提醒一次）：编排器只找被引用的，
    写在 event 或别的键里的不会分给哪一镜（例如世界层 REQ_LOOP 只写在 event 里）。"""
    used = set(wish_refs(shots, idx))
    return [f"{w['shot']} 的 footage_wish「{rid}」没有一镜在 subject_src / footage / aux_footage 里用它：编排器只找被引用的 id。"
            "要当主体、世界层、辅助素材就把 id 写进那一镜的 subject_src.request_id / footage / aux_footage；只当叠层、参考的不用写 id"
            for rid, w in idx.items() if rid not in used]


def wish_errs(shots, names=(), idx=None):
    """镜头表引用了 request_id、footage_wish 里却没写这条需求（硬错误，发回导演）。"""
    idx = wish_index(shots)[0] if idx is None else idx
    return [f"{'、'.join(dict.fromkeys(f'{a} 的 {b}' for a, b in where))} 写了 request_id「{rid}」，footage_wish 里没有这条需求："
            "在用它的那一镜 footage_wish 里写 {request_id, need（中文一句话）, query（英文搜索词）, use, seconds}"
            for rid, where in wish_refs(shots, idx).items() if rid not in idx and rid not in names]


def shotlist_errs(c, o, bm, mm, topic, names, clips):
    """镜头表的校验（s1_director 发回用）→ (不合格, 建议)。先就地规整小毛病（norm_shotlist）；按提档后的样子检查（切点密度由编排器提档，
    不拿它发回导演），不改导演的原稿。names = 素材清单里的名字；footage_wish 里带 request_id 的待补素材也算名字。"""
    notes = []
    bad = norm_shotlist(o, notes)                                     # 小数 bars 就地取整（只报警，不为它整轮重问）
    o = json.loads(json.dumps(o))
    densify(o, bm, mm, plan_cuts_cfg(c.series))
    try:
        sl_ = expand_shotlist(o, bm, bm["sections"], mm)
    except (ValueError, TypeError) as e:
        return bad + [str(e)], []
    idx, h0 = wish_index(sl_["shots"])
    h0 += wish_errs(sl_["shots"], names, idx)
    nm = list(names) + [r for r in idx if r not in names]
    _, h1, s1 = plan_errors(bm, sl_, c.series)
    h2, s2 = alloc_check(sl_, topic, nm, c.series, partial=bool(is_short(c)))
    rows = [dict(r, status="warn", how=r["how"] + "（镜头表只报警：唯一的真档案可以用，审片看画面判）")
            if r["id"] == "root.assets.floor1" and r["status"] == "fail" else r     # 主体实拍 ≥1080p 在镜头表只提醒（回填阶段同样只记）
            for r in TR.shot_eval(c.tree, c.cat(), topic, bm, sl_, c.series, clips)]
    return (bad + h0 + h1 + h2 + plan_shape_errs(sl_) + plan_density_errs(sl_, mm, plan_cuts_cfg(c.series))
            + plan_motion_errs(sl_, nm, bm.get("beat_f")) + plan_source_errs(sl_, clips) + TR.hard_msgs(rows),
            notes + s1 + s2 + wish_unused(sl_["shots"], idx) + TR.soft_msgs(rows))


def _why(v):
    """没找到的真实原因：stockreq 的 reason，没有就用 err。"""
    v = v or {}
    return str(v.get("reason") or v.get("err") or "没找到").strip()


def _footage_now(c, cur):
    fj = c.j("inputs/footage.json", {"clips": []}) or {"clips": []}
    cur["clips"] = fj.get("clips", [])
    cur["names"] = [x["name"] for x in cur["clips"]]


def _prev_ok(c, rid, clips):
    """这一集前面某一轮已经找到过的 request_id（inputs/stock.json 的 requests，from = wish）→ 现在清单里叫什么（发回后改了 query、
    重找失败，第一轮找到的那条就丢了）。没有返回 None。"""
    by_file = {x.get("file"): x.get("name") for x in clips if x.get("file")}
    for e in reversed((c.j(SQ.REG) or {}).get("requests") or []):
        if e.get("from") == "wish" and e.get("request_id") == rid and e.get("status") in SQ.OK_ST and by_file.get(e.get("file")):
            return dict({k: e[k] for k in ("need", "use", "site", "title", "license", "page_url", "file", "sheet") if e.get(k)},
                        query=e.get("query") or "", status="ok", name=by_file[e["file"]], kept=True)
    return None


def contact_sheets(c, names):
    """素材清单里这些名字的联系表 {名字: 图路径}：stockreq.contact_sheets（先拿现成的，没有才在本集 inputs/sheets/ 现出）。
    出错不挡回填：这几条只给文字（按标题判）。"""
    try:
        return SQ.contact_sheets(c, list(names)) or {}
    except Exception as e:  # noqa: BLE001
        c.log(f"  看图确认：联系表没出来（{type(e).__name__}: {str(e)[:120]}）", echo=False)
        return {}


def stock_names(c):
    """这一集素材请求通道找来的素材名（inputs/stock.json 里找到的）：看图确认只看这些，用户整理过的素材包不再看。"""
    return {e.get("name") for e in (c.j(SQ.REG) or {}).get("requests") or [] if e.get("status") in SQ.OK_ST and e.get("name")}


def stock_subjects(o, stock):
    """[(镜, F 号)]：主体直接写了素材请求通道找来的 F 号（开题 footage_needs 找的，文字对题判收、还没人看过图）。"""
    out = []
    for s in o.get("shots") or []:
        src = s.get("subject_src") if isinstance(s.get("subject_src"), dict) else {}
        f = str(src.get("file") or "")
        if src.get("kind") == "实拍" and not src.get("request_id") and f in stock:
            out.append((s, f))
    return out


def check_items(c, o, res, refs, idx, clips):
    """看图确认（shotlist_check）要判的几条 → (条目, 附图)。条目：回填进来的 request_id（found：找到的，带联系表）、没找到的（missing：need、
    搜了什么、真实原因）、当主体用的开题 F 号（subject：这一集素材请求通道找来的，用户整理过的素材包不再看）。临时没找成（deferred）的不放。"""
    stock = stock_names(c)
    title = {x["name"]: str(x.get("content") or "").split("原标题：")[-1][:80] for x in clips}
    items = []
    for rid, where in refs.items():
        v, w = res.get(rid) or {}, idx.get(rid) or {}
        at = "、".join(dict.fromkeys(f"{a} {WISH_USE.get(b, b)}" for a, b in where))
        base = {"id": rid, "at": at, "need": w.get("need") or v.get("need") or "", "query": w.get("query") or v.get("query") or ""}
        if v.get("status") in SQ.OK_ST and v.get("name"):
            items.append(dict(base, kind="found", name=v["name"], title=v.get("title") or title.get(v["name"], ""), sheet=v.get("sheet")))
        elif not v.get("deferred"):
            items.append(dict(base, kind="missing", why=_why(v)))
    for s, f in stock_subjects(o, stock):
        items.append({"id": s["id"], "at": f"{s['id']} 主体", "kind": "subject", "name": f, "need": str(s.get("subject") or ""),
                      "title": title.get(f, "")})
    sh = contact_sheets(c, [x["name"] for x in items if x["kind"] != "missing" and not (x.get("sheet") and os.path.isfile(x["sheet"]))])
    imgs = []
    for x in items:
        p = x.get("sheet") if x.get("sheet") and os.path.isfile(x["sheet"]) else sh.get(x.get("name"))
        if x["kind"] != "missing" and p:
            imgs.append((f"{x['id']} → {x['name']}（{x['at']}）", p))
            x["img"] = len(imgs)
    return items, imgs


def check_lines(items):
    """看图确认提示词里的清单：找到的每条一行（id、哪一镜、要什么、找到的 F 号和标题、第几张图），没找到的另起一段（need、搜了什么、为什么）。"""
    got = [f"- {x['id']}（{x['at']}）要「{x['need'][:80]}」→ {x['name']}「{(x.get('title') or '?')[:80]}」"
           + (f"：图 {x['img']}" if x.get("img") else "：没有联系表，按标题判") for x in items if x["kind"] != "missing"]
    miss = [f"- {x['id']}（{x['at']}）要「{x['need'][:80]}」，搜「{x['query'] or '（没写）'}」：{x['why'][:200]}" for x in items if x["kind"] == "missing"]
    return "\n".join(got) or "（没有）", "\n".join(miss) or "（没有）"


def _set_query(o, rid, q):
    for s in o.get("shots") or []:
        for w in s.get("footage_wish") or []:
            if isinstance(w, dict) and str(w.get("request_id") or "").strip() == rid:
                w["query"] = q


def fill_wishes(c, o, cur, ctx=None):
    """镜头表过了校验之后：引用的 request_id（subject_src / footage / aux_footage；只在 event 里提到的不算）先去找
    （stockreq.resolve_wishes），然后一次看图确认（shotlist_check）：把回填进来的素材（以及当主体用的、素材请求通道找来的开题 F 号）的
    联系表和没找到的清单给镜头表代理，逐条判 ok / reject / missing；reject、missing 给了新 query 的按它重找一次。还没有的不停整集：
    写进引用它的那几镜的 missing_footage [{id, need, searched, why, route}]（制作任务书里讲清，制作代理自己选路），footage_wish 那一条删掉。
    进了镜头表、可没人看过图的（看图后重找来的、route 写 F 号按标题选的、看图那一次没拿到判定的）写进 unseen_footage [{id, name, need, why, at}]
    （任务书里标出来，制作代理先看图再用；11-09 复核 G1–G3）。看图判不对、没给新词的这一次不再找（resolve_wishes 的 hold）。
    前一轮找到过、这一轮重找失败的保留前一轮的；临时失败（deferred）的 id 留着给开拍前盘点再试。只有整集一条实拍都没有、
    系列又要实拍世界层时才停（stopped:stock_wish）。原稿另存 shotlist_wish.json（pending = 回填 / 看图还没走完，续跑从这里接着走）。
    cur = {"names", "clips"}，回填后换成新的素材清单。返回回填后的镜头表。"""
    wp = c.p("shotlist_wish.json")
    idx = wish_index(o.get("shots") or [])[0]
    refs = {r: w for r, w in wish_refs(o.get("shots") or [], idx).items() if r not in cur["names"]}
    old = c.j("shotlist_wish.json") or {}
    orig = json.loads(json.dumps(o))                                # 导演原稿原样另存（续跑从它重走回填、看图；下面会就地改 o）
    res, chk, retried, missing, no_chk = {}, {}, {}, {}, ""

    def save(**kw):
        got = {r: x["name"] for r, x in res.items() if x.get("status") in SQ.OK_ST and x.get("name")}
        util.write_json(wp, dict({
            "note": "导演镜头表原稿（带 request_id 的待补素材）和回填结果：shotlist.json 是回填成 F 号之后的；resolved = id → 素材清单里的名字，"
                    "results = 回填结果（制作时分给这一镜的联系表进 ctx/look/stock/），check = 看图确认的逐条判定，retried = 按判定换了 query 重找的，"
                    "missing = 最后还没找到、写进镜头 missing_footage 的；pending = 回填 / 看图还没走完（续跑从这里接着走）",
            "shotlist": orig, "resolved": got, "results": res, "check": chk, "retried": retried, "missing": missing, "at": util.iso()}, **kw))
        return got
    if not refs and not stock_subjects(o, stock_names(c)):
        if os.path.exists(wp):                                      # 上一版镜头表留下的（续跑、重做第 1 环节）：这一版没有待补素材
            os.remove(wp)
        return o
    save(pending=True)
    if refs:
        res.update(SQ.resolve_wishes(c, o) or {})
    _footage_now(c, cur)
    for r in refs:                                                  # 前一轮（上一次运行 / 发回前）找到过的，这一轮没找到就保留前一轮的
        v = res.get(r) or {}
        if not (v.get("status") in SQ.OK_ST and v.get("name")) and not v.get("deferred") \
                and ((old.get("check") or {}).get(r) or {}).get("verdict") != "reject":   # 上一次看图判过不对题的不捡回来
            p = (old.get("results") or {}).get(r)
            p = p if p and p.get("status") in SQ.OK_ST and p.get("name") in cur["names"] else _prev_ok(c, r, cur["clips"])
            if p:
                res[r] = dict(p, shots=v.get("shots") or p.get("shots"), kept=True)
                c.log(f"镜头表回填：{r} 这一轮没找到，保留前一轮找到的 {p['name']}（看图确认时一起判）")
    later = [r for r in refs if (res.get(r) or {}).get("deferred") and (res.get(r) or {}).get("status") not in SQ.OK_ST]
    if later:
        c.log(f"镜头表回填：{'、'.join(later)} 这次临时没找成（限流 / 超时 / 出错），id 留着，开拍前盘点再试")
    items, imgs = check_items(c, o, res, refs, idx, cur["clips"])
    if items:
        got_t, miss_t = check_lines(items)
        known = {x["id"]: x for x in items}
        shown, stock = {x.get("name") for x in items}, stock_names(c)     # 清单只列素材请求通道找来的（用户的包在制作代理的 footage.md 里）
        clips_t = "\n".join(f"- {x['name']}「{str(x.get('content') or '').split('原标题：')[-1][:60]}」{x.get('w') or '?'}×{x.get('h') or '?'}"
                            for x in cur["clips"] if x.get("name") not in shown and x.get("name") in stock) or "（没有）"
        try:
            o_chk, _ = c.llm.ask_json("shotlist", "shotlist_check", c.system,
                                      c.prompt("01_shotlist_check", TITLE=(c.j("topic.json") or {}).get("title_cn", ""), FOUND=got_t, MISSING=miss_t,
                                               CLIPS=clips_t),
                                      images=imgs, schema=c.schema("shotlist_check"),
                                      ctx=dict(ctx or {}, check_items=[{k: x.get(k) for k in ("id", "kind", "name", "query")} for x in items]))
        except util.Stop as e:                                      # 复核 G3：这一次只是确认，没拿到判定不停整集（额度用完 blocked 照停）
            if e.status not in ("schema", "api_error"):
                raise
            o_chk, no_chk = {}, f"看图确认这一次没拿到判定（{e.status}）"
            c.log(f"看图确认：{no_chk}：{e.msg[:160]}；不停，回填的素材都当没人看过图，交制作代理判")
        for it in o_chk.get("items") or []:
            i = str(it.get("id") or "").strip()
            if i in known:
                chk[i] = {k: str(it.get(k) or "").strip() for k in ("verdict", "query", "route", "why")}
        todo, rej, hold = {}, set(), {}                             # rej = 看图判不对题的 request_id / 开题 F 号：重找时那条候选不再给
        for i, v in chk.items():
            x, q = known[i], v["query"]
            if v["verdict"] == "ok" or (x["kind"] == "missing" and v["verdict"] != "missing" and not q):
                continue
            if v["verdict"] == "reject":
                rej |= {i, x.get("name")} - {None, ""}
            if x["kind"] == "subject":                              # 开题 F 号当主体、看图不对题：这一镜主体改成待补素材，按新 query 找
                rid = f"REQ_{i.upper()}_SUBJ"
                s = next(s for s in o["shots"] if s["id"] == i)
                s["subject_src"] = {"kind": "实拍", "request_id": rid}
                s["footage_wish"] = [w for w in s.get("footage_wish") or [] if not (isinstance(w, dict) and w.get("request_id") == rid)] + [
                    {"request_id": rid, "need": x["need"], "query": q, "use": "subject", "why": f"看图确认：{x['name']} 不对题（{v['why'][:60]}）"}]
                idx[rid], refs[rid] = dict(s["footage_wish"][-1], shot=i), [(i, "subject_src")]
                res[rid] = {"status": "failed", "err": f"{x['name']} 看图判不对题：{v['why'][:100]}", "need": x["need"], "query": q, "shots": [i]}
                i = rid
            elif x["kind"] == "found":                              # 找到的看图不对题：作废（新 query 再找不到就算没找到）
                res[i] = {"status": "failed", "err": f"找到的 {x['name']}「{x.get('title') or ''}」看图判不对题：{v['why'][:100]}",
                          "need": x["need"], "query": x["query"], "shots": (res.get(i) or {}).get("shots"), "rejected_name": x["name"]}
            if q and not re.fullmatch(r"F\d{2,3}", q):
                todo[i] = q
            elif v["verdict"] == "reject":                          # 判不对、没给新词：这一次不找（以前照旧词重搜重下）
                rej.add(i)
                hold[i] = f"看图判不对题（{(x.get('name') or '')}：{v['why'][:60]}），没给新的搜索词，不再找"
        if todo:                                                    # 按看图给的新 query 重找一次（stockreq.resolve_wishes：query 改了才再找）
            for r, q in todo.items():
                _set_query(o, r, q)
                retried[r] = q
            c.log(f"看图确认：{'、'.join(f'{r}（{q}）' for r, q in todo.items())} 按新的搜索词重找一次")
            new = SQ.resolve_wishes(c, o, reject=sorted(rej), hold=hold) or {}
            _footage_now(c, cur)
            for r in todo:
                v = new.get(r) or {"status": "failed", "err": "没去找"}
                if v.get("status") in SQ.OK_ST and v.get("name") and v["name"] == (res.get(r) or {}).get("rejected_name"):
                    v = dict(v, status="failed", name=None, err=f"新的搜索词又找回看图判不对题的 {v['name']}")
                res[r] = v
        c.log("看图确认：" + "；".join(f"{i} {v['verdict']}" + (f"（{v['why'][:40]}）" if v["verdict"] != "ok" and v["why"] else "")
                                     for i, v in chk.items()) + f"（{len(imgs)} 张联系表）")
    bad = {}
    for r, where in refs.items():                                   # 最后还没有的：写进那几镜的 missing_footage，制作代理自己选路
        v = res.get(r) or {"status": "failed", "err": "没去找"}
        if (v.get("status") in SQ.OK_ST and v.get("name")) or v.get("deferred"):
            continue
        w = idx.get(r) or {}
        tried = list(dict.fromkeys(q for q in ((old.get("results") or {}).get(r, {}).get("query"), w.get("query"), retried.get(r)) if q))
        route = ((chk.get(r) or {}).get("route") or "").strip()
        f_ok = route if re.fullmatch(r"F\d{2,3}", route) else ""   # 复核 M1 / G2：route 整格就是 F 号才算（query 里的 F16 型号、「别用 F05」不算）
        taken = {str((s.get("subject_src") or {}).get("file") or "") for s in o.get("shots") or [] if isinstance(s.get("subject_src"), dict)}
        if f_ok in cur["names"] and not (any(b == "subject_src" for _, b in where) and f_ok in taken):
            clip = next((x for x in cur["clips"] if x.get("name") == f_ok), {})
            res[r] = {"status": "ok", "name": f_ok, "need": w.get("need"), "query": w.get("query"), "shots": [a for a, _ in where],
                      "title": str(clip.get("content") or "").split("原标题：")[-1][:80], "by": "shotlist_check",
                      **({"sheet": sh} if (sh := contact_sheets(c, [f_ok]).get(f_ok)) else {})}   # 复核 L1：联系表进制作代理的 ctx/look/stock/
            continue
        bad[r] = missing[r] = {"id": r, "need": str(w.get("need") or v.get("need") or ""), "searched": "；".join(tried) or "（没写搜索词）",
                               "why": _why(v), **({"route": route} if route else {})}
    for s in o.get("shots") or []:
        mine = {r for r in bad if any(a == s["id"] for a, _ in refs[r])}
        if not mine:
            continue
        s["missing_footage"] = [dict(missing[r], at="、".join(WISH_USE.get(b, b) for a, b in refs[r] if a == s["id"])) for r in mine]
        s["footage_wish"] = [w for w in s.get("footage_wish") or [] if not (isinstance(w, dict) and str(w.get("request_id") or "").strip() in mine)]
        if not s["footage_wish"]:
            s.pop("footage_wish")
    unseen = {}                                                     # 复核 G1 / G2 / G3：进了镜头表、可没人看过图的：任务书里标出来，制作代理先看图
    for r in refs:
        v = res.get(r) or {}
        why = ("" if not (v.get("status") in SQ.OK_ST and v.get("name")) else
               f"看图后按「{retried[r]}」重找来的，没再看图" if r in retried else
               "看图时按清单标题选的，没看过它的图" if v.get("by") == "shotlist_check" else
               no_chk or ("看图确认没判这一条" if items and r not in chk else ""))
        if why:
            unseen[r] = {"id": r, "name": v["name"], "need": str((idx.get(r) or {}).get("need") or v.get("need") or ""), "why": why,
                         "where": refs[r]}
    for x in items:                                                 # 当主体用的开题 F 号：看图那一次没拿到判定
        if x["kind"] == "subject" and (no_chk or x["id"] not in chk):
            unseen[x["id"]] = {"id": x["id"], "name": x["name"], "need": x["need"], "why": no_chk or "看图确认没判这一条",
                               "where": [(x["id"], "subject_src")]}
    for s in o.get("shots") or []:
        u = [dict({k: m[k] for k in ("id", "name", "need", "why")}, at="、".join(WISH_USE.get(b, b) for a, b in m["where"] if a == s["id"]))
             for m in unseen.values() if any(a == s["id"] for a, _ in m["where"])]
        if u:
            s["unseen_footage"] = u
    if bad and not cur["clips"] and TR.world_min(c.series)[0] > 0:   # 真没法往下走：一条实拍都没有、系列又要实拍世界层
        save(stopped=sorted(bad))
        raise util.Stop(5, "stock_wish", f"镜头表回填：这一集素材清单 0 条，系列要实拍世界层，停在镜头表（看图确认后还没找到 {len(bad)} 条）："
                        + " ".join(f"{r}「{m['need'][:40]}」搜「{m['searched']}」：{m['why'][:120]}" for r, m in bad.items()))
    got = save(**({"unseen": {k: {kk: x for kk, x in m.items() if kk != "where"} for k, m in unseen.items()}} if unseen else {}))
    if bad:
        c.log(f"镜头表回填：{'、'.join(bad)} 看图确认后还没找到，不停：写进那几镜的 missing_footage，制作代理自己选路")
    if unseen:
        names_u = "、".join(k + "→" + m["name"] for k, m in unseen.items())
        c.log(f"镜头表回填：{names_u} 没人看过图（重找来的 / 按标题选的），写进那几镜的 unseen_footage，制作代理先看图再用")
    c.log(f"镜头表回填：{len(got)} 条 request_id 换成 F 号" + (f"；按看图重找 {len(retried)} 条" if retried else "") + "；原稿 shotlist_wish.json")
    return SQ.rebind(o, res)[0]


def wish_resume(c):
    """续跑：上一次停在回填 / 看图确认（shotlist_wish.json 带 pending，或旧版的 stopped）、导演产物还在 → 返回那份
    镜头表原稿，第 1 环节不再问导演、镜头表，从回填接着走；别的情况返回 None。"""
    wj = c.j("shotlist_wish.json") or {}
    if (wj.get("pending") or wj.get("stopped")) and isinstance(wj.get("shotlist"), dict) and all(
            os.path.exists(c.p(f)) for f in ("beatmap.json", "bible.md")):
        return wj["shotlist"]
    return None


# ============================================================================ 1 导演
def s1_director(c):
    topic = c.j("topic.json")
    inp = inputs.ensure(c, topic)
    music = inp["music"]
    c.log(f"输入：素材包 {os.path.relpath(inp['pack'], util.PARENT) if inp['pack'] else '无'}，实拍 {inp['footage_n']} 条，"
          f"配乐 {music['title'] + ('（自动挑的，暂定）' if music.get('auto') else '') if music else '无（第 6 环节会停下等）'}")
    g = c.g
    mm = c.mm() if music else None
    if music:
        rule = (f"已定 = {music['bpm']}（配乐「{music['title']}」，一拍 {3600 / music['bpm']:.3f} 帧，代码会直接写进去）；"
                f"bars 必须 = {music.get('bars')}；drop 必须在第 {music.get('drop_bar')} 小节第 1 拍（原曲的 DROP）")
        if music.get("short"):
            rule += (f"（试跑短片：只做原曲窗口的前 {music['bars']} 小节，DROP 前铺垫 {music['drop_bar'] - 1} 小节、"
                     f"DROP 后 {music['bars'] - music['drop_bar'] + 1} 小节）")
        if mm:
            rule += "；所有卡点的位置只能从下面的「音乐打击点表」里选，编排器会逐条对音乐核"
        else:
            stops = [e for e in music.get("window_events", []) if e.get("kind") == "stop"]
            if stops:
                rule += "；原曲在 " + "、".join(f"第 {e['bar']} 小节" for e in stops) + " 有急停再砸，可以放一个 big"
    else:
        rule = f"{g['bpm'][0]}–{g['bpm'][1]} 之间选一个；60 fps 下一拍最好是整数帧（150 BPM = 24 帧，144 会抖）"
    slots_md, map_md = music_slots(mm, (music or {}).get("bars"))
    prompt = c.prompt("01_director", SERIES=c.series["name"], N=str(topic["episode"]), TITLE=topic["title_cn"],
                      TOPIC=topic_brief(topic), BPM_RULE=rule, DUR=f"{g['duration_s'][0]}–{g['duration_s'][1]}",
                      MUSIC_SLOTS=slots_md, MUSIC_MAP=map_md)
    cid = c.cat()
    resume = wish_resume(c)                                           # 续跑：上一次停在回填 / 看图确认
    if resume is not None:
        bm, bible = c.j("beatmap.json"), util.read(c.p("bible.md"))
        c.log("续跑：上一次停在镜头表回填 / 看图确认，导演、镜头表不再问，从回填接着走（shotlist_wish.json 的原稿）")
    else:
        o, info = c.llm.ask_json("director", "director", c.system, prompt, schema=c.schema("director"),
                                 check=lambda o: check_beatmap(o["beatmap"], g, music, mm) + TR.hard_msgs(TR.beat_eval(c.tree, cid, o["beatmap"])),
                                 soft=lambda o: beatmap_rules(o["beatmap"], g, music, mm) + TR.soft_msgs(TR.beat_eval(c.tree, cid, o["beatmap"])),
                                 ctx={"music": music, "mm": mm, "series": c.series.get("id")})
        if info.get("advice"):
            c.log(f"节拍表提醒（只提醒，没拦）：{info['advice']}")
        bm = expand_beatmap(o["beatmap"], mm)
        b = o["bible"]
        bible = (f"# 「{topic['title_cn']}」导演说明（暂定片名）\n\n"
                 + "\n".join(f"- **{k}**：{v}" for k, v in b.items())
                 + f"\n\n节拍：{bm['bpm']} BPM，{bm['bars']} 小节，{bm['total_f']} 帧 = {bm['total_f'] / 60:.2f} s。\n\n| 段落 | 小节 | 能量 | 干什么 |\n|---|---|---|---|\n"
                 + "\n".join(f"| {s['id']} {s['name']} | {s['bars'][0]}–{s['bars'][1] - 1} | {ENERGY_NOTE[s['energy']]} | {s['purpose']} |"
                             for s in bm["sections"]) + "\n"
                 + "\n".join(f"\n{n}：第 {bm[k]['bar']} 小节第 {bm[k].get('beat', 1)} 拍（第 {bm[k]['F']} 帧）{bm[k].get('what', '')}"
                             for k, n in (("hook_1s", "第 1 秒的变化"), ("wow_7s", "7 秒内的王炸")) if bm.get(k))
                 + ("\n\n色表（颜色长在哪个东西上）：" + "；".join(f"{s['id']} {s.get('color_from')}" for s in bm["sections"] if s.get("color_from"))
                    if any(s.get("color_from") for s in bm["sections"]) else "") + "\n")
        util.write(c.p("bible.md"), bible)
        util.write_json(c.p("beatmap.json"), bm)
    tree_save(c, "导演", TR.beat_eval(c.tree, cid, bm))
    c.log(f"导演：{bm['bpm']} BPM × {bm['bars']} 小节 = {bm['total_f'] / 60:.2f}s，{len(bm['sections'])} 段，{len(bm['hits'])} 个卡点")

    parts = "\n".join(f"- {k}：{v['what']}" for k, v in c.series["parts"].items() if not v.get("legacy"))   # 旧零件名（particles）不列
    bm_short = {"bpm": bm["bpm"], "bars": bm["bars"], "total_f": bm["total_f"],
                "sections": [{k: s[k] for k in ("id", "name", "bars", "energy", "purpose", "start_f", "end_f")} for s in bm["sections"]],
                "hits": [dict({k: h.get(k) for k in ("bar", "beat", "sub", "kind", "what")}, frame=h.get("F")) for h in bm["hits"]]}   # frame：和素材名 F07 分开
    fj = c.j("inputs/footage.json", {"clips": []}) or {"clips": []}
    names = [x["name"] for x in fj.get("clips", [])]
    lad = ladder(topic)
    prompt = c.prompt("01_shotlist", TITLE=topic["title_cn"], BIBLE=bible, BEATMAP=dumps(bm_short),
                      BEAT_F=f"{bm['beat_f']:.3f}", SIXTEENTH_F=f"{bm['sixteenth_f']:.2f}", PARTS=parts,
                      TRANSITIONS=transitions_block(c.series["transitions"]),
                      FOOTAGE=util.read(c.p("inputs", "footage.md")).strip(), TOPIC=topic_brief(topic),
                      SCREEN_TITLE=topic.get("screen_title", ""), NL=str(len(lad)),
                      LADDER="\n".join(f"{x['i'] + 1}. {x['text']}（{x['what']}）" for x in lad) or "（开题没给数字链）",
                      HERO_OBJECT=str(topic.get("hero_object", "")), ENV=env_text(topic.get("env") or {}, IG.mode(c), SQ.mode(c)),
                      FONTS="、".join(f"{f['role']} = {f['font']}" for f in c.series.get("fonts") or []) or "（系列没定字体）")
    if is_short(c):                                                   # 试跑短片：镜头少、每镜做透；数字链不必分完
        prompt += (f"\n\n## 试跑短片（试跑只要 10–30 秒）\n这一集只做原曲窗口的前 {bm['bars']} 小节（{bm['total_f'] / 60:.1f} s）："
                   "镜头少一点、每一镜做透。上面「数字链每一站都要分到」这一版不适用：站数比镜头多时可以跳过中间几站"
                   "（照样按顺序、不回头、一镜最多一站），但数字链最后一站（片名那一站）留给收尾那一镜；没分到的站这一版不出现。")
    cur = {"names": names, "clips": fj.get("clips", [])}               # 回填（fill_wishes）之后素材清单多了新 F 号，校验跟着换

    def errs(o):
        return shotlist_errs(c, o, bm, mm, topic, cur["names"], cur["clips"])

    extra, info2, o2 = "", {"attempts": 0}, resume
    for sess in (1, 2) if resume is None else ():                     # 全片分配的底线多：一个对话重试满了，换个新对话再来一次
        try:
            o2, info2 = c.llm.ask_json("shotlist", "shotlist" if sess == 1 else "shotlist_s2", c.system, prompt + extra,
                                       schema=c.schema("shotlist"), check=lambda o: errs(o)[0], soft=lambda o: errs(o)[1],
                                       ctx={"beatmap": bm, "topic": topic, "footage": names})
            break
        except util.Stop as e:
            if sess == 2 or e.status != "schema":
                raise
            c.log(f"镜头表一个对话里重试满了还不合格，换新对话再来一次：{e.msg[:300]}")
            extra = "\n\n## 上一次（另一个对话）没过的地方，这次一次做对\n" + e.msg
    norm_shotlist(o2)
    o2 = fill_wishes(c, o2, cur, {"beatmap": bm, "topic": topic})   # 待补素材（request_id）→ 找、看图确认、回填成 F 号；没找到的进 missing_footage
    names, clips = cur["names"], cur["clips"]
    dens = densify(o2, bm, mm, plan_cuts_cfg(c.series))
    sl = apply_alloc(expand_shotlist(o2, bm, bm["sections"], mm), topic)
    if dens:
        sl["density_notes"] = dens
        c.log("切点密度：导演选的档位太稀，编排器提档：" + "；".join(dens))
    checks = plan_errors(bm, sl, c.series)[0]
    util.write_json(c.p("shotlist.json"), sl)
    util.write_json(c.p("gates_plan.json"), {"summary": G.summarize(checks), "checks": checks})
    tags = []
    alloc_check(sl, topic, names, c.series, tags, partial=bool(is_short(c)))
    led = tree_save(c, "镜头表", alloc_rows(c.tree, tags) + TR.shot_eval(c.tree, cid, topic, bm, sl, c.series, clips)
                    + TR.plan_rows(c.tree, cid, checks) + [write_fonts(c)])
    c.log(f"规则树（计划）：{TR.summary_line(led)[:200]}；明细 gates_tree.json")
    util.write(c.p("bible.md"), bible + "\n## 全片分配（镜头表写死，制作代理只准用分给自己的）\n\n" + alloc_md(sl) + "\n")
    ncut = len({x for s in sl["shots"] for x in s["cuts_f"]}) + len(sl["shots"]) - 1
    c.log(f"镜头表：{len(sl['shots'])} 镜，计划切点 {ncut} 个 = {ncut / (sl['total_f'] / 60):.2f}/s"
          f"（{'从音乐起音取' if mm else '按格均分（没有音乐打击点表）'}）；计划门禁 {G.fmt(G.summarize(checks))}"
          + (f"（问了 {info2['attempts']} 次；规则项只提醒，没做到的记在报警里）" if info2["attempts"] > 1 else ""))
    al = sl["alloc"]
    c.log(f"全片分配：数字 {sum(1 for x in al['numbers'] if x['shot'])}/{len(al['numbers'])} 站，"
          f"大词 {sum(len(v) for v in al['words'].values())} 个不重复，主体 "
          + "、".join(f"{k} {sum(1 for v in al['subjects'].values() if v.get('kind') == k)}" for k in SUBJECT_KINDS)
          + (f"；要生图 {len(al['gen_requests'])} 张（还没有图，记在 shotlist.json 的 alloc.gen_requests）" if al["gen_requests"] else ""))


def write_fonts(c):
    """字体表（根「字」必做①）：系列定稿字体（真字体文件）写进 fonts.json，查文件在不在。"""
    fs = [dict(f, file=os.path.join(util.PARENT, f["file"])) for f in c.series.get("fonts") or []]
    keys = [str(f["key"]) for f in fs if f.get("key")]
    use = " | ".join(f"'{k}'" for k in keys) if keys else "'hero' | 'wide' | 'mono'"
    util.write_json(c.p("fonts.json"), {"note": f"字体表（根「字」必做①）：系列定稿字体，镜头里 TY.word(…, {use})，"
                                                "只用真字体文件，不用代码画字", "fonts": fs})
    miss = [f["file"] for f in fs if not os.path.isfile(f["file"])]
    return TR.row(c.tree, "root.type.do1", bool(fs) and not miss, [f"字体文件不在：{m}" for m in miss] or ([] if fs else ["系列配置没写 fonts"]),
                  len(fs), "字体表：每个角色用哪款、文件从哪来（fonts.json）", "镜头表")


# ============================================================================ 代理任务的上下文
def shot_at(sl, F):
    for s in sl["shots"]:
        if s["start_f"] <= F < s["end_f"]:
            return s
    return sl["shots"][-1]


def spec_for_agent(s, topic, tr=None, sync=None):
    """tr = 系列的 transitions：入点 / 出点各带切点帧 F 和该 kind 对应的引擎函数（HANDBOOK §9）。
    sync = 这一镜的「本镜卡点」表（sync_rows）：有就放进 spec 的 sync（帧 / 等级 / 音乐）。"""
    keep = ("id", "section", "start_f", "end_f", "cuts_f", "hits", "look", "subject", "subject_src", "action", "sustain", "type_fx",
            "hero_word", "words", "number", "number_what", "label", "label_px", "event", "image", "transition_in", "transition_out",
            "parts", "footage", "aux_footage")                  # missing_footage / unseen_footage 只在任务书末节讲（不重复）
    d = {k: s[k] for k in keep if k in s}
    for k, F in (("transition_in", s["start_f"]), ("transition_out", s["end_f"])):
        if k in d:
            t = dict(d[k]) if isinstance(d[k], dict) else norm_tx(d[k])     # 旧集的镜头表是一句话
            t["F"] = F
            eng = ((tr or {}).get("kinds", {}).get(t.get("kind")) or {}).get("engine")
            if eng:
                t["engine"] = eng
            d[k] = t
    d["signature_color"] = topic["signature_color"]
    if "number_i" in s or "subject_src" in s:                         # 有全片分配：只给这一镜的那一站，不给整条数字链
        lad = ladder(topic)
        k = s.get("number_i")
        d["hits"] = hits_for_agent(s, lad)
        d["number_note"] = (f"数字链第 {k + 1}/{len(lad)} 站；只用这一个数字（前后站分给了别的镜头，不回头、不预告）"
                            if isinstance(k, int) else "这一镜没有分到数字：屏上不出现数字链里的数")
    else:
        d["numbers"] = topic["number_ladder"]                         # 旧集（没有分配）照旧
    if sync:
        d["sync"] = [{k: r[k] for k in ("F", "lvl", "pos", "music") if k in r} for r in sync]
    return d


# 「本镜卡点」表（卡点卡得不准）：发给制作 / 返修代理，渲完编排器按同一张表逐帧量（G.shot_sync）。
# 等级：重 = 全窗最强 16 个重音（试跑短片按窗口长短折算 top_n；最后一个重音之后不算）、drop、急停、节拍表的 big / drop / final；
# 轻 = 其余重音（S / A 档）和 small；可 = 其余有起音的格（最后一个重音之后的海报停留里都是「可」）。
# 门槛（几倍）不写进给代理的文字，只在渲完量出来的问题单里出现（防 Goodhart）。
SYNC_WANT = {"重": "整屏事件：新构图从这一帧起（或冲击帧盖在新构图上、主角字砸入），这一帧的变化要明显大过周围",
             "轻": "看得见的一下：真切（构图换掉）、字换词、主体被打一下，大过背景运动",
             "切": "计划切点：这一帧真切（见表下第一条）",
             "可": "可以切在这里（切就要真切），也可以不切"}


def sync_rows(s, mm):
    """这一镜的「本镜卡点」表：[{F, lvl, attack_f, pos, music, desc}]（片内帧号，F = 画面该变的那一帧）。没有 music_map 返回 []。"""
    if not mm:
        return []
    by = mm_slots(mm)
    ons = [e for e in by.values() if has_onset(e)]
    la_F = (mm.get("last_accent") or {}).get("F")                     # 最后一个重音（final）之后是海报停留：不算「重」（和 sync_top 同一口径）
    top_n = int(mm.get("top_n") or 16)                                # 试跑短片按窗口长短折算（inputs.short_map 写的）
    top = {int(e["i"]) for e in sorted([e for e in ons if e["F"] >= 3 and (la_F is None or e["F"] <= la_F + 1)],
                                       key=lambda e: -e["total"])[:top_n]}
    drop_i = six(mm["drop"]["bar"], mm["drop"]["beat"], mm["drop"].get("sub", 0)) if (mm.get("drop") or {}).get("bar") else None
    stops = {six(st["bar"], st["beat"], st.get("sub", 0)): st for st in mm.get("stops") or []}
    kind = {}
    for h in s.get("hits") or []:
        kind[int(h["F"])] = h["kind"]
    cuts = set(s.get("cuts_f") or [])
    a, b = s["start_f"], s["end_f"]
    rows = []
    for e in sorted(ons, key=lambda e: e["F"]):
        F = int(e["F"])
        if not a <= F < b or int(e["i"]) in stops:                     # 急停那一格只出下面一行（急停帧），不出起音帧那一行
            continue
        k, i = kind.get(F, ""), int(e["i"])
        late = la_F is not None and F > la_F + 1 and k not in ("big", "drop", "final")
        lvl = ("可" if late else "重" if i in top or i == drop_i or k in ("big", "drop", "final")
               else "轻" if is_accent(e) or k == "small" else "可")
        if lvl == "可" and F in cuts:                                  # 计划切点不是「可切可不切」：11-04 Codex 初剪 52 个只真切了 13 个
            lvl = "切"
        mus = f"{BAND.get(e.get('band'), '起音')}，第 {e.get('rank')} 强" + ("，drop" if i == drop_i else "")
        tag = [x for x in (k, "镜头入点" if F == a else "", "计划切点" if F in cuts else "") if x]
        rows.append({"F": F, "lvl": lvl, "attack_f": e.get("attack_f"), "pos": slot_pos(e), "music": mus,
                     "desc": f"{slot_pos(e)} {mus}" + (f"（{'、'.join(tag)}）" if tag else "")})
    by_F = {int(e["F"]): e for e in by.values()}
    for F in sorted(cuts - {r["F"] for r in rows}):                   # 弱起音上的计划切点（music_cuts 按本镜轻重取的）也进表：不进表就成了「表外不许强变化」
        if a <= F < b:
            e = by_F.get(F)
            pos = slot_pos(e) if e else f"第 {F} 帧"
            rows.append({"F": F, "lvl": "切", "attack_f": (e or {}).get("attack_f"), "pos": pos, "music": "弱起音",
                         "desc": f"{pos} 弱起音（计划切点）"})
    for i, st in stops.items():                                       # 急停：按整体事件单列（帧 = 声音掉下去的那一帧）
        if a <= st["F"] < b:
            pos = f"{st['bar']}.{st['beat']}.{st.get('sub', 0)}"
            rows.append({"F": int(st["F"]), "lvl": "重", "attack_f": None, "pos": pos, "music": f"急停：声音一下掉 {st.get('drop_db', '?')} dB",
                         "desc": f"{pos} 急停（声音一下掉 {st.get('drop_db', '?')} dB）", "kind": "stop"})
    return sorted(rows, key=lambda r: r["F"])


def sync_block(rows, s=None):
    """给代理的「本镜卡点」表（任务书 {{SYNC}}；小标题在模板里，这里只出说明 + 表 + 几条要求）。"""
    if not rows:
        return ("这一集没有实测的音乐打击点表：切点照 spec 的 cuts_f，大卡点照 spec 的 hits"
                + (f"（{', '.join(str(h['F']) for h in s.get('hits') or [] if h['kind'] in ('big', 'drop', 'final'))}）" if s else "") + "。")
    L = ["「这一帧」= 画面第一下明显变化出现在这一帧（提前 1 帧也算对，晚 1 帧就算没卡上）。帧号已经按真实起音算好，直接写成整数常量，"
         "切换用 `T.F >= F`（不要用 `T.f`、`TL.f()` 的小数，也不要自己从 bar / beat 换算）。",
         "| 帧 | 音乐 | 等级 | 要做到 |", "|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['F']} | {r['desc']} | {r['lvl']} | {SYNC_WANT[r['lvl']]} |")
    L += ["- 「切」= 计划切点：每一个都要真的切——换素材 / 景别 / 机位，或者实拍和字卡交替，前后两帧不是同一张图（不用比背景大很多，但必须换）；"
          "编排器渲完会比这一帧前后两帧的构图，没切的逐条发回。素材只有一条时，在切点之间换景别 / 机位"
          "（一路往前，不回到用过的构图）、实拍和白底大字 / 色块字卡交替。",
          "- 表里没有的帧 = 音乐在这里没有起音：不许有跳变（这一帧的变化比前后几帧突然大好几倍：不切、不闪、不砸字）；"
          "连续的单向运动（主体动作、运镜一路推 / 移、世界从主角身后流过、素材往前跑、字进场）不受限，用它把卡点之间连起来。",
          "- 切点 = 构图真的换了（换主体 / 换景别 / 换机位）。同一张素材挪一点、放大缩小一点、转 1–2°、轻推、轻压再弹回，都不算切点，"
          "也不许拿来填卡点——这正是「抽搐、轻微摆动」。",
          "- 大事件只放在「重」上；「重」的变化要明显大过「轻」，「轻」要大过背景运动：轻重跟着音乐走。",
          "- 冲击帧（SR.hit）：新画面从 F 起、冲击帧画的是新画面；不许闪两帧又回到旧构图。闪白、反相、整屏亮度跳一下再回来都不算卡点"
          "（编排器会比这一帧前后的构图）。SR.kick、SR.auto_cam 的 kick / six 不算卡点。"]
    return "\n".join(L)


def sync_table(rows):
    """G.shot_sync 的表（接口 2c 的 TABLE.json）。"""
    return [{"F": r["F"], "lvl": r["lvl"], "attack_f": r.get("attack_f"), "desc": r["desc"], "kind": r.get("kind", "onset")} for r in rows]


def int_consts(code):
    """shot.py 里的整数常量（≥ 0）：卡点没做到时，提醒哪些「重」帧代码里根本没写。语法错返回空集。"""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, int)
            and not isinstance(n.value, bool) and n.value >= 0}


def hits_for_agent(s, lad):
    """卡点说明里提到的数（节拍表先写的）和这一镜分到的不一样时，标上「以 number 为准」，免得代理照着写错数。"""
    k, out = s.get("number_i"), []
    for h in s.get("hits") or []:
        bad = sorted(stations_in(h.get("what"), lad) - {k})
        out.append(dict(h, note=f"说明里的 {'、'.join(lad[j]['text'] for j in bad)} 不是这一镜的数，屏上数字以 number 为准") if bad else h)
    return out


def tx_parts(s, tr):
    """入点、出点的 kind 要用到的零件（transition / impact / kaleido）。"""
    out = []
    for k in ("transition_in", "transition_out"):
        out += ((tr or {}).get("kinds", {}).get(norm_tx(s.get(k)).get("kind")) or {}).get("parts", [])
    return list(dict.fromkeys(out))


# ============================================================================ 必做动作（制作代理）：提示词清单 + 交付前查代码
# 动效零件：代码里出现这些调用就算用了（同一行的两种写法算一个零件）。
# 不许劣质特效：删掉「粒子」「HUD」（2D 画的雪 / 碎片、细线圈不算会动的零件），加「叠层」（实拍元素 SR.element）、
MOTION_PARTS = {
    "冲击簇": ("SR.hit", "SR.card"), "转场": ("SR.tx", "SR.tx_frame"), "万花筒": ("KV.Vortex",),
    "运镜": ("fx.cam", "SR.auto_cam", "SR.AutoCam"), "跟拍": ("SR.track",), "叠层": ("SR.element",),
    "碎裂": ("SR.shatter",), "落地": ("SR.whip", "SR.push_thru", "SR.slam"), "主角字一生": ("HE.hero",),
    "逐字母": ("TY.letters",), "素材变速": ("Take(speed=[]",),
}
PART_SHOW = {}   # 必做清单里零件后面括号写的调用（默认写第一个）


def _code_facts(code):
    """(调用名集合, 屏上字候选)：调用名如 "SR.hit"；字符串常量去掉 docstring。语法错返回 None（交给渲染去报错）。
    另记：@shot(subframes=N) 的 N 进 calls 里的 "subframes=N"；random / time 模块的调用照原名记（纯函数检查用）。"""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    calls, strs, docs = set(), [], set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Attribute) and isinstance(f.value.value, ast.Name):
                calls.add(f"{f.value.value.id}.{f.value.attr}.{f.attr}")          # np.random.rand、datetime.datetime.now
            if isinstance(f, ast.Name) and f.id == "shot":
                for k in n.keywords:
                    if k.arg == "subframes" and isinstance(k.value, ast.Constant) and isinstance(k.value.value, int):
                        calls.add(f"subframes={k.value.value}")
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in ("random", "rng") and n.args:
                calls.add(f"{f.value.id}.{f.attr}(seed)")
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body \
                and isinstance(n.body[0], ast.Expr) and isinstance(getattr(n.body[0], "value", None), ast.Constant):
            docs.add(id(n.body[0].value))
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                calls.add(f"{f.value.id}.{f.attr}")
            elif isinstance(f, ast.Name):
                calls.add(f.id)
                if f.id == "Take" and any(k.arg == "speed" and isinstance(k.value, (ast.List, ast.ListComp, ast.Tuple)) for k in n.keywords):
                    calls.add("Take(speed=[]")
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
            strs.append(n.value)
    return calls, strs


# 规则树的代码级必做 / 底线（v2.1）：转场按声明调引擎函数、快速运动带运动模糊、画面是帧号的纯函数
TX_CALLS = {"万花筒": ("SR.tx", "KV.Vortex"), "冲击帧盖切点": ("SR.tx", "SR.hit")}
BLUR_CALLS = ("fx.cam", "SR.auto_cam", "SR.AutoCam", "SR.track", "E.blur")   # SR.track / AutoCam 自带 taps 运动模糊
IMPURE = ("time.time", "time.perf_counter", "time.monotonic", "time.time_ns", "datetime.now", "datetime.datetime.now",
          "random.random", "random.uniform", "random.randint", "random.choice", "random.shuffle", "random.gauss",
          "np.random.rand", "np.random.randn", "np.random.random", "np.random.uniform", "np.random.randint",
          "numpy.random.rand", "numpy.random.random")
HIT_CALLS = ("SR.hit", "SR.card", "SR.tx", "HE.hero")             # SR.kick 是每拍整帧泵，不算卡点
CODE_RIDS = ("root.camera.do2", "root.subject.do1", "root.assets.do4", "root.type.floor1", "root.transition.do2",
             "root.quality.do1", "root.quality.floor5", "root.fx.do3")


def must_do_misses(c, s, work, code_path):
    """交付前查代码（不要 GPU，几毫秒）。返回没做到的（中文，一条一句）。规则 id 见 must_do_tagged。"""
    return [m for _, m in must_do_tagged(c, s, work, code_path)]


def must_do_tagged(c, s, work, code_path):
    """[(规则 id, 一句话)]：主体素材用上了、≥2 个动效零件、没用别的镜头分到的大词和数字；规则树（v2.1）：
    入点 / 出点的转场调了声明 kind 对应的引擎函数（首镜入点、末镜出点是首尾循环，不查）、快速运动带运动模糊（@shot(subframes≥4)
    或 fx.cam / SR.auto_cam / E.blur）、不用实时钟和没种子的随机数。旧镜头表（没有全片分配）只查动效零件。"""
    if not os.path.exists(code_path):
        return []
    code = util.read(code_path)
    facts = _code_facts(code)
    if facts is None:
        return []
    calls, strs = facts
    miss = []
    used = [k for k, v in MOTION_PARTS.items() if calls & set(v)]
    if len(used) < 2:
        miss.append(("root.camera.do2", f"本镜只用了 {len(used)} 个动效零件（{'、'.join(used) or '无'}）：至少 2 个不同的（" + "、".join(MOTION_PARTS) + "）"))
    src = s.get("subject_src") or {}
    fjc = (c.j("inputs/footage.json", {"clips": []}) or {}).get("clips", [])
    names = {x["name"] for x in fjc}
    cg = {x["name"] for x in fjc if x.get("cg") or x.get("burned_text")}   # CG 科普片 / 烧录字幕：不当主体，不逼代理用它
    if src.get("kind") in ("实拍", "生图") and src.get("file") in names and src["file"] not in strs and src["file"] not in cg:
        miss.append(("root.subject.do1", f"主体「{s.get('subject', '')}」的素材 {src['file']} 没用上：要 Take('{src['file']}', ...) 把它放进画面当主体"))
    sl = c.j("shotlist.json") or {}
    shots = sl.get("shots") or []
    if sl.get("alloc") and shots:
        n_tx, sides = len(re.findall(r"\b(?:SR\.tx|KV\.Vortex|SR\.hit)\(", code)), []
        for k, skip, side in (("transition_in", s["id"] == shots[0]["id"], "入点"), ("transition_out", s["id"] == shots[-1]["id"], "出点")):
            kind = norm_tx(s.get(k)).get("kind")
            if skip or not kind or kind == "拍上硬切":
                continue
            sides.append(side)
            need = TX_CALLS.get(kind, ("SR.tx",))
            if not calls & set(need):
                F = s["start_f"] if side == "入点" else s["end_f"]
                miss.append(("root.transition.do2", f"{side}第 {F} 帧声明的是「{kind}」，代码里没调对应的引擎函数（{' 或 '.join(need)}，见 ctx/HANDBOOK.md §9）："
                             f"把交接的东西「{(norm_tx(s.get(k)).get('handoff') or {}).get('out' if side == '出点' else 'in', '?')}」带过切点"))
        if len(sides) == 2 and 0 < n_tx < 2 and not any(r == "root.transition.do2" for r, _ in miss):
            miss.append(("root.transition.do2", f"入点第 {s['start_f']} 帧、出点第 {s['end_f']} 帧都要转场，代码里只写了 1 个：TXS 里入点、出点各放一个 SR.tx"))
        sub = max([int(x.split("=")[1]) for x in calls if x.startswith("subframes=")] or [1])
        if sub < 4 and not calls & set(BLUR_CALLS):
            miss.append(("root.quality.do1", blur_rule(c.series)))
        big = [h["F"] for h in s.get("hits") or [] if h.get("kind") in ("big", "drop", "final")]
        if big and not calls & set(HIT_CALLS):
            miss.append(("root.fx.do3", f"大卡点第 {'、'.join(map(str, big))} 帧要有蓄力 → 冲击 → 余波：用冲击簇 SR.hit（自带三段）、SR.card，或 HE.hero 砸字（SR.kick 不算）"))
        bad = sorted(x for x in calls & set(IMPURE))
        if bad:
            miss.append(("root.quality.floor5", f"画面要是帧号的纯函数：不用 {'、'.join(bad)}（随机用 src.kit.core 的 rnd(键…)，动画按 T.F / T.f 算）"))
    sl = c.j("shotlist.json") or {}
    if sl.get("alloc"):
        mine = set(shot_words(s))
        others = {w: x["id"] for x in sl["shots"] if x["id"] != s["id"] for w in shot_words(x) if w not in mine}
        lad = ladder(c.j("topic.json"))
        k = s.get("number_i")
        leak = []
        for t in strs:
            for j in stations_in(t, lad, loose=False) - {k}:
                leak.append(f"数字 {lad[j]['text']}（数字链第 {j + 1} 站）")
            if re.search(r"[a-z]", t) or _up(t) == _up(s.get("label")) or len(_up(t).split()) > 3:
                continue                                              # 带小写的是代码里的名字（预设、方向、tag）；自己的小标签、长句不是大字
            rest = _up(t)
            for w in sorted(mine, key=len, reverse=True):
                rest = re.sub(rf"(?<![A-Z0-9]){re.escape(w)}(?![A-Z0-9])", " ", rest)
            for w, sid in others.items():
                if re.search(rf"(?<![A-Z0-9]){re.escape(w)}(?![A-Z0-9])", rest):
                    leak.append(f"「{w}」（分给了 {sid}）")
        if leak:
            miss.append(("root.type.floor1", "用了不是分给这一镜的字：" + "、".join(dict.fromkeys(leak)) + "。屏上大字只用 hero_word / words，数字只用 number"))
    return miss


def must_do_block(s, sp, need=None, series=None):
    """制作 / 返修代理提示词里的「必做动作」清单（ 三档里的第一档：做没做查得出）。
    need 不再用：门禁阈值不写进给代理的文字（11-04 防 Goodhart：只给实测值和原片参照），参数留着兼容旧调用。
    series：第 5 条（颜色）、第 9 条（运动模糊）照系列卡说（color_rule_kf / blur_rule；11-06 G9：以前写死 CHROMA「不许整屏染色」，"""
    src = s.get("subject_src") or {}
    kind = src.get("kind")
    miss = kind == "实拍" and not src.get("file") and any("主体" in str(m.get("at") or "") for m in s.get("missing_footage") or [] if isinstance(m, dict))
    unseen = kind == "实拍" and any(m.get("name") == src.get("file") for m in s.get("unseen_footage") or [] if isinstance(m, dict))
    how = {"实拍": ("主体实拍没找到（见任务书末节）：自己选路——设计帧、生图、清单里别的镜没当主体的 F 号，或写 out/footage_requests.json 再找"
                  if miss else f"用 `Take('{src.get('file', '?')}', ...)` 把它放进画面" + ("（这条没人看过图：先看末节）" if unseen else "")),
           "生图": (f"用 `Take('{src['file']}', ...)`（生成好的图）" if src.get("file") else
                  f"图还没生成（{src.get('gen', '')}）：先用实拍替身，NOTES.md 写明")}.get(kind, "")
    ti, to = sp.get("transition_in") or {}, sp.get("transition_out") or {}
    hi, ho = ti.get("handoff") or {}, to.get("handoff") or {}
    words = shot_words(s)
    lines = [
        f"1. 主体：「{s.get('subject', '?')}」（{kind or '?'}）必须出现、全画面最清楚，并且做这个动作：{s.get('action') or s.get('event', '')}。{how}。",
        "2. 镜内要有进展：每个构图从入画到出画，主体或画面做完一件有方向、不回头的事（长出来、裂开、被揭开、冲出去、"
        "推进 / 横移到下一个位置、换成下一个东西），这一格的头帧和尾帧并排看得出事情往前走了；计划切点上换掉主体周围占画面大头的东西"
        "（素材、素材时段、世界层、色块底）——纯色底上钉住的主角切不出来。要动多大照 ctx/HANDBOOK.md §3b 的实测标定（编排器同一把尺子）："
        "世界层 / 素材层横移每帧 8 px 左右或推近每帧 1 % 左右，或者 SR.track 钉住主角、让世界从身后单向流过；纯色底上的东西量出来接近 0。"
        "钉住只管计划切点前后那两帧（主角中心、大小不跳），钉住的主角自己整镜累计最多长到 1.3–1.5 倍，"
        "要更大就交给 grow / hit。运镜不能单独撑一格（不许「静图 + 推拉」），实拍叠层 SR.element 只当陪衬。"
        "冒充物质的东西（碎片、粉尘、雾、烟、裂缝、气泡、水滴）只能来自实拍叠层 SR.element、生图"
        "或画面派生（SR.shatter），不用 2D 画。"
        "原地来回不算动，也不许拿来凑（摆、缩放呼吸、来回转、整帧抖、每拍整帧泵、推了又拉回、亮度闪烁）："
        "编排器量的是一拍里画面净走了多远，原地来回量出来是 0，交付时是硬门禁。"
        "震屏只放在 spec hits 的 big / drop / final 那一帧起、8 帧内衰减完（SR.hit / fx.shake 自带衰减）。",
        "3. 本镜至少用 2 个不同的动效零件：" + "、".join(f"{k}（{PART_SHOW.get(k, v[0])}）" for k, v in MOTION_PARTS.items()
                                                    if k != "素材变速")
        + "、素材变速（Take(speed=[...])）。每个特效事件写明来源（out/NOTES.md 一行一件：素材号 / 叠层种类），"
        "说不出来源的特效不做。",
        f"4. 转场按交接做（kind 用 ctx/HANDBOOK.md §9 对应的函数）：入点第 {ti.get('F', s.get('start_f'))} 帧「{ti.get('kind', '?')}」——"
        + (f"上一镜结尾「{hi.get('out')}」出画（{hi.get('at')}），你开头就让「{hi.get('in')}」在那里接住" if hi else "没写交接，照 eye 做")
        + f"；出点第 {to.get('F', s.get('end_f'))} 帧「{to.get('kind', '?')}」——"
        + (f"你结尾让「{ho.get('out')}」出画（{ho.get('at')}），下一镜开头是「{ho.get('in')}」" if ho else "没写交接，照 eye 做") + "。",
        f"5. {color_rule(series, long=True)}",
        f"6. 只用分给你的：大字 {' / '.join(words) or '（没有）'}（字效照 spec 的 type_fx：{_tfx_text(s)}）；"
        f"数字 {s.get('number') or '（这一镜没有数字）'}。别的镜头分到的大词、数字一律不出现（编排器查代码里的字符串，用了就发回）。",
        f"7. 撑到出画：照 spec 的 sustain「{s.get('sustain') or '?'}」做（分批揭示 / 运镜跟着主体 / 东西在干活 / 动作接力），"
        "镜头最后一帧之前主体都还在干活，不等停稳再切。",
        "8. 切点要把东西带过去：除了拍上硬切，入点、出点都调 kind 对应的引擎函数（第 4 条；编排器查代码里有没有 SR.tx / KV.Vortex / SR.hit）。",
        f"9. {blur_rule(series)}；画面是帧号的纯函数：不用 time.time()、没种子的 random（随机用 src.kit.core 的 rnd）。",
        "10. 「本镜卡点」表里每个「重」帧：新构图从这一帧起（或冲击帧 SR.hit(F, …) 盖在新构图上，或主角字 HE.hero 砸入），"
        "这一帧的变化要明显大过周围；冲击闪完不许回到旧构图。「轻」帧是看得见的一下（真切、字换词、主体被打一下）。"
        "SR.kick、推拉、滚转 snap、轻压回弹都不算卡点。表外的帧不许有跳变（这一帧的变化比前后几帧突然大好几倍）；"
        "连续的单向运动不受限（一路推、一路移、世界流过、素材往前跑都照做）。编排器渲完逐帧量，没做到发回。",
        "11. 只读自己的 work/ 和 ctx/：不要去读、不要抄别的镜头的代码（引擎 src/shots/ 里别的镜头、别的镜头的工作目录）；"
        "要参考就看 ctx/ 里给的签名镜头和手册。",
    ]
    return "\n".join(lines)


def _tfx_text(s):
    x = s.get("type_fx") if isinstance(s.get("type_fx"), dict) else {}
    return " / ".join(str(x.get(k) or "?") for k in ("font", "entry", "layout")) if x else "镜头表没写，照系列定稿字体"



def _venv_dirs(py):
    """venv 目录 + 软链每一跳指向的 Python 安装目录（uv：.venv/bin/python → cpython-3.12-… → cpython-3.12.14-…，
    沙箱要每一跳都能读，否则 exec 报 Operation not permitted）。"""
    out = []
    if not os.path.exists(py):
        return out
    out.append(os.path.dirname(os.path.dirname(py)))
    p, seen = py, set()
    while os.path.islink(p) and p not in seen:
        seen.add(p)
        t = os.readlink(p)
        p = t if os.path.isabs(t) else os.path.normpath(os.path.join(os.path.dirname(p), t))
        out.append(os.path.dirname(os.path.dirname(p)))
    out.append(os.path.dirname(os.path.dirname(os.path.realpath(py))))
    return list(dict.fromkeys(out))


CHECK_SH = r'''#!/bin/bash
# 用法：bash ctx/check.sh out/shot.py <函数名>
# 不要 GPU 的自查（沙箱里能跑）：劣质特效、导入、@shot、Take 有没有越出素材。在 .check/ 里搭「引擎 + 你的代码」的影子目录，只写工作目录。
# 整镜画面由编排器在沙箱外渲；每次渲完，这一镜的联系表、卡点帧放进 ctx/look/self/（用 Read 打开看）。
set -eu
CODE="$1"; FN="$2"
W="$(pwd)"; P="$W/.check"; ENG="{ENGINE}"
# 劣质特效自查（和编排器交付后查的是同一份代码：2D 粒子冒充物质、同一张渲染图复制拼装、实拍放大过头……），只打印、不挡后面的自查
if [ -f "$W/ctx/cheapfx.py" ]; then "{PY}" -I -B "$W/ctx/cheapfx.py" check "$CODE" --footage "$W/ctx/footage.json" --spec "$W/ctx/spec.json" --motif "{MOTIF}" --engine "$ENG" || true; fi
rm -rf "$P"; mkdir -p "$P/src/shots" "$P/logs" "$P/out" "$P/cache"
for x in "$ENG"/src/*; do b="$(basename "$x")"; [ "$b" = shots ] || ln -s "$x" "$P/src/$b"; done
for x in "$ENG"/src/shots/*.py; do ln -s "$x" "$P/src/shots/$(basename "$x")"; done
rm -f "$P/src/shots/$FN.py"; cp "$CODE" "$P/src/shots/$FN.py"
for d in assets footage tools audio; do [ -e "$ENG/$d" ] && ln -s "$ENG/$d" "$P/$d"; done
cd "$P" && "{PY}" -B -m src.render check --fn "src.shots.$FN:$FN"
'''


DESC = {"HANDBOOK.md": "引擎手册（先读；路径相对引擎根）",
        "ex_open.py": "签名镜头：开头 10 格", "ex_hit.py": "签名镜头：DROP 冲击 + 主角字砸入",
        "ex_vortex.py": "签名镜头：万花筒「时光穿梭」",
        "heat.py": "MO.world 的底层（黑白 + 只留一种颜色）",
        "series.py": "SR.*：冲击簇、转场、虚拟摄影机、跟拍 SR.track、实拍叠层 SR.element、画面自碎 SR.shatter",
        "impact.py": "冲击帧和画面碎裂（SR.hit / SR.shatter 的底层）",
        "parts_demo.py": "零件验收样例（转场 SR.tx 的用法）"}


def assigned_footage(s):
    """分给这一镜的素材 [(名字, 用法)]：主体实拍 / 生图、世界层（footage 里写的素材名）、辅助素材（aux_footage）。"""
    out, src = [], s.get("subject_src") or {}
    if src.get("kind") in ("实拍", "生图") and src.get("file"):
        out.append((src["file"], "主体"))
    for n in re.findall(r"(?<![A-Za-z0-9])F\d{2,3}(?![0-9])", str(s.get("footage") or "")):
        if n not in {x for x, _ in out}:
            out.append((n, "世界层"))
    for n in s.get("aux_footage") or []:
        if str(n) not in {x for x, _ in out}:
            out.append((str(n), "辅助"))
    return out


def write_footage_ctx(c, work, shot, motif):
    """ctx/footage.json / footage.md（能用的实拍、分给这一镜的、自带运动量）、ctx/overlays.json（叠层表：母题素材包 + 本集素材包）、
    ctx/stock.json（这一镜已经到的素材）。prepare_ctx 和素材请求到了以后（refresh_footage_ctx）都用它。返回 (fj, fmo, asg, ov)。"""
    ctx = os.path.join(work, "ctx")
    os.makedirs(ctx, exist_ok=True)
    fj = c.j("inputs/footage.json", {"clips": []}) or {"clips": []}
    fmo = inputs.load_motion(fj.get("clips", []))                      # 素材自带运动量（素材包 footage_motion.json）
    asg = assigned_footage(shot)                                       # 分给这一镜的：主体、世界层、辅助素材（aux_footage）
    names = {x["name"] for x in fj.get("clips", [])}
    asg += [(n, u) for n, u in SQ.assigned_extra(c, shot.get("id")) if n in names and n not in {x for x, _ in asg}]   # 开拍前盘点补的
    util.write_json(os.path.join(ctx, "footage.json"), {
        "assigned": [{"name": n, "use": u} for n, u in asg],
        "clips": [dict({k: x.get(k) for k in ("name", "dur", "w", "h", "fps", "use", "content", "kind", "cg", "burned_text",
                                              "kbps")},
                       eff_1x=(fmo.get(x["name"]) or {}).get("eff_1x"), speed_hint=(fmo.get(x["name"]) or {}).get("speed"))
                  for x in fj.get("clips", [])]})
    util.write(os.path.join(ctx, "footage.md"), inputs.footage_md(fj, fmo, asg))
    ov = inputs.overlays_merged(c.series, motif, c.ep)                 # 实拍元素叠层表（SR.element 的 kind / 素材 / 窗口；引擎组维护）
    if ov:
        util.write_json(os.path.join(ctx, "overlays.json"), ov)
    if SQ.enabled(c):
        SQ.ctx_note(c, work, shot.get("id"))
    return fj, fmo, asg, ov


# ---------------------------------------------------------------------------- 代理自己的眼睛（11-06 G1 A 步）
LOOK_SELF = os.path.join("ctx", "look", "self")
LOOK_SAY = ("- ctx/look/self/ — 你这一镜最近一次渲出来的画面（编排器在沙箱外渲，每次渲完整个换掉；用 Read 打开看图，改之前先看）："
            "sheet.jpg = 整镜均匀 12 帧（左上角是片内帧号），hit_fNNNNN.jpg = 「本镜卡点」第 NNNNN 帧前后三帧（F−1 / F / F+1）")


def look_self(c, s, work, video, prio=()):
    """ctx/look/self/：这一镜刚渲出来的画面（代理看不到自己的画面是 G1：沙箱没有 GL，22/42 次运行说渲不了）。整个目录换掉：
    sheet.jpg（整镜均匀 12 帧，印片内帧号）、hit_fNNNNN.jpg（「本镜卡点」重 / 切 / 轻帧的 F−1 / F / F+1，prio 里的帧排最前，最多 6 张）、
    返回 [(相对 work 的路径, 说明)]；抽帧失败只记日志、不拦。"""
    d = os.path.join(work, LOOK_SELF)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    a, n = s["start_f"], s["end_f"] - s["start_f"]
    ffl, out = c.p("logs", "ffmpeg.log"), []
    os.makedirs(os.path.dirname(ffl), exist_ok=True)

    def one(rel, what, fn):
        try:
            fn(os.path.join(work, rel))
            out.append((rel, what))
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            c.log(f"  {s['id']}：{rel} 没抽成（{str(e)[:120]}）", echo=False)
    if n >= 2:
        one(os.path.join(LOOK_SELF, "sheet.jpg"), f"整镜均匀 12 帧（第 {a}–{s['end_f'] - 1} 帧）",
            lambda p: media.sheet(video, [int((i + 0.5) * n / 12) for i in range(12)], p, cols=4, cell_w=480, max_n=12, f0=a, log_path=ffl))
    rows = sync_rows(s, c.mm())
    rank = {"重": 0, "切": 1, "轻": 2}
    want = [int(F) for F in prio] + [r["F"] for r in sorted(rows, key=lambda r: (rank.get(r["lvl"], 9), r["F"])) if r["lvl"] in rank]
    lvl = {r["F"]: r["lvl"] for r in rows}
    for F in list(dict.fromkeys(F for F in want if 1 <= F - a < n - 1))[:6]:
        one(os.path.join(LOOK_SELF, f"hit_f{F:05d}.jpg"), f"第 {F} 帧（{lvl.get(F, '卡点')}）前后三帧：F−1 / F / F+1",
            lambda p, i=F - a: media.sheet(video, [i - 1, i, i + 1], p, cols=3, cell_w=480, f0=a, log_path=ffl))
    return out


def look_text(items, frames=()):
    """发回文字里「先看图」那一节：编排器刚渲的这一版放在哪、哪几张对应这次发回的问题（frames = 点名的片内帧号）。"""
    if not items:
        return ""
    hot = {f"hit_f{int(F):05d}.jpg" for F in frames}
    L = ["## 先看图再改（编排器刚渲的这一版，ctx/look/self/，用 Read 打开）"]
    L += [f"- {rel}：{what}" + ("（这次点名的帧）" if os.path.basename(rel) in hot else "") for rel, what in items]
    return "\n".join(L) + "\n"


EXAMPLE = ("example_shot.py",)


def example_files(c, work, shot, topic, asg, ov, rows):
    """这个系列一段实测能渲的整镜示例（文字由提示词组写在 prompts/<系列>/example_shot.py
    没有系列的用 prompts/ 下的），编排器把占位换成这一集、这一镜的：{{FN}} {{START_F}} {{END_F}} {{HIT_F}} {{CUT_F}} {{CLIP}} {{CLIP2}}
    {{OVERLAY}} {{HEX}} {{TAG}} {{WORD}}。素材号先取分给这一镜的、再取素材表里的；叠层种类取这一集叠层表里的。写进 ctx/，返回 FILES 的几行。
    这一集还没有叠层：'{{OVERLAY}}' 换成 None（引擎遇到表外的 kind 抛 KeyError，不拿别的集的 kind 充数）；素材表是空的：要实拍的示例不写，FILES 说明。"""
    fj = c.j("inputs/footage.json", {"clips": []}) or {"clips": []}
    names = [x["name"] for x in fj.get("clips", []) if not (x.get("cg") or x.get("burned_text"))] or [x["name"] for x in fj.get("clips", [])]
    clips = list(dict.fromkeys([n for n, _ in asg if n in names] + names))
    kinds = list(((ov or {}).get("kinds") or {}).keys())
    inside = lambda F: shot["start_f"] + 4 <= F < shot["end_f"] - 4  # noqa: E731   镜头首尾 4 帧不当示例（要和邻镜一起量）
    heavy = [r["F"] for r in rows if r["lvl"] == "重"] + [h["F"] for h in shot.get("hits") or []]
    heavy = [F for F in heavy if inside(F)] or heavy
    cuts = [F for F in shot.get("cuts_f") or [] if inside(F)] or [F for F in shot.get("cuts_f") or [] if shot["start_f"] < F < shot["end_f"]]
    mid = (shot["start_f"] + shot["end_f"]) // 2
    words = [w for w in [shot.get("hero_word")] + list(shot.get("words") or []) if w]
    v = {"FN": shot["id"], "START_F": str(shot["start_f"]), "END_F": str(shot["end_f"]), "HIT_F": str(heavy[0] if heavy else mid),
         "CUT_F": str(cuts[0] if cuts else mid), "CLIP": clips[0] if clips else "", "CLIP2": (clips[1:] or clips or [""])[0],
         "OVERLAY": kinds[0] if kinds else "",
         "HEX": str((topic.get("signature_color") or {}).get("hex") or "#FFFFFF"), "TAG": f"{shot['id']}_hero",
         "WORD": str(words[0] if words else shot["id"].upper())}
    lines = []
    for name in EXAMPLE:
        base, ext = os.path.splitext(name)
        src = c.find("prompts", base, ext)
        if not os.path.isfile(src):
            continue
        txt = util.read(src)
        if "{{CLIP}}" in txt and not clips:
            lines.append(f"- ctx/{name} 这一集没写：素材表还是空的，这个示例的世界层要实拍（先提素材请求，或者世界层用整屏色）")
            continue
        what = (f"素材号 {v['CLIP']}、" if "{{CLIP}}" in txt else "") + (f"叠层种类 {v['OVERLAY']}" if kinds else "叠层留 None（这一集还没有叠层）")
        if not kinds:
            txt = txt.replace("'{{OVERLAY}}'", "None")
        for k, x in v.items():
            txt = txt.replace("{{" + k + "}}", x)
        left = sorted(set(re.findall(r"\{\{[A-Z_0-9]+\}\}", txt)))
        if left:
            c.log(f"  注意：{os.path.relpath(src, util.HERE)} 里有编排器不认识的占位 {left}，示例照写（代理会看到原样的占位）", echo=False)
        util.write(os.path.join(work, "ctx", name), txt)
        lines.append(f"- ctx/{name} — 这个系列一段实测能渲的整镜示例（函数名、帧号、{what} 已经换成这一镜的；"
                     "照它的结构写，内容按 spec 改）" if ext == ".py" else f"- ctx/{name} — 示例（帧号已经换成这一镜的）")
    return lines


_API, _API_LOCK = {}, threading.Lock()


def api_md(c):
    """引擎 API 签名表（p2/tools/apisig.py 从这一集引擎副本的源码生成），每集生成一次（按引擎目录 + 白名单记住），
    放在引擎副本根下 API.md。生成不了返回 None（只记日志）。几镜并行准备 ctx 时排队（只生成一次）。"""
    with _API_LOCK:
        return _api_md(c)


def _api_md(c):
    src = os.path.join(getattr(c.renderer, "root", "") or "", "src")
    if not os.path.isdir(src):
        src = os.path.join(util.PARENT, c.series["engine"]["src"])
    keep = ""                                                       # 只列手册、系列示例、签名镜头里点过名的函数
    for f in list(c.series["engine"].get("always") or []) + ["#example_shot.py"]:
        q = (os.path.join(util.PARENT, f[1:]) if f.startswith("@") else c.find("prompts", "example_shot", ".py") if f.startswith("#")
             else os.path.join(os.path.dirname(src), f))
        if q.endswith((".md", ".py")) and os.path.isfile(q):
            keep += util.read(q) + "\n"
    key = (src, hashlib.sha256(keep.encode()).hexdigest())
    p = _API.get(key)
    if p and os.path.exists(p):
        return p
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("apisig", os.path.join(util.P2, "tools", "apisig.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        md = mod.build(src, keep or None)
    except Exception as e:  # noqa: BLE001  签名表只是参考：生成不了不拦制作
        c.log(f"  注意：引擎 API 签名表没生成（{type(e).__name__}: {str(e)[:160]}）", echo=False)
        return None
    root = getattr(c.renderer, "root", "") or ""
    p = os.path.join(root if os.path.isdir(root) else c.ep, "API.md")
    util.write(p, md)
    _API[key] = p
    return p


def missing_tools(work):
    """代理在 out/NOTES.md 里写的「缺的工具」：一行「缺的工具：…」（也认「缺的形状」「missing_tools:」），
    或「## 缺的工具」小标题下面的条目。返回 [str]（去重，最多 10 条；「无」「没有」不算）。"""
    try:
        text = util.read(os.path.join(work, "out", "NOTES.md"))
    except OSError:
        return []
    out, sec = [], False
    for ln in text.splitlines():
        m = re.match(r"^\s*#{1,6}\s*(.*)$", ln)
        if m:
            sec = bool(MISS_KEY.match(m.group(1).strip()))
            continue
        m = re.match(r"^\s*(?:[-*]\s*)?(?:\*\*)?(缺的工具(?:\s*/\s*形状)?|缺的形状|missing_tools)(?:\*\*)?\s*[:：]\s*(.+)$", ln, re.I)
        if m:
            out += [x.strip() for x in re.split(r"[；;]", m.group(2))]
        elif sec and re.match(r"^\s*[-*\d]", ln):
            out.append(re.sub(r"^\s*(?:[-*]|\d+[.)、])\s*", "", ln).strip())
    out = [x[:200] for x in out if x and x.strip("。. ") not in ("无", "没有", "none", "None", "暂无", "—", "-")]
    return list(dict.fromkeys(out))[:10]


MISS_KEY = re.compile(r"^(缺的工具|缺的形状|missing[_ ]tools)", re.I)


def prepare_ctx(c, work, shot, topic, task_meta, extra_files=()):
    """把这一镜需要的文件复制进 work/ctx（只读参考），返回给提示词的文件清单。
    同时写 ctx/spec.json（这一镜的要求）、ctx/task.json（函数名、帧）、ctx/footage.md / .json（能用的实拍）、ctx/check.sh；
    ctx/look/self/（这一镜现在那一版的画面，返修时）。"""
    ctx = os.path.join(work, "ctx")
    if os.path.isdir(ctx):
        shutil.rmtree(ctx)
    os.makedirs(os.path.join(work, "out"), exist_ok=True)
    eng = c.renderer.root
    lines = []
    files = list(c.series["engine"]["always"])
    sp_ = c.series["parts"]
    for p in list(shot.get("parts", [])) + tx_parts(shot, c.series.get("transitions")):
        pp = sp_.get(part_name(c.series, p)) or {}
        files += pp.get("files") or []
        for q in pp.get("use") or []:                                   # 旧零件名（particles）：换成它的替代品的文件（叠层、碎裂）
            files += (sp_.get(q) or {}).get("files") or []
    seen = set()
    for f in files:
        if f in seen:
            continue
        seen.add(f)
        if f.startswith("@"):
            src, dst = os.path.join(util.PARENT, f[1:]), os.path.join(ctx, os.path.basename(f))
            rel = "ctx/" + os.path.basename(f)
        else:
            src, dst, rel = os.path.join(eng, f), os.path.join(ctx, f), "ctx/" + f
        if not os.path.exists(src):
            c.log(f"  注意：零件文件不存在 {src}", echo=False)
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        what = DESC.get(os.path.basename(f)) or next((v["what"] for v in sp_.values() if (v.get("files") or [None])[0] == f),
                                                      "引擎 API / 样例")
        lines.append(f"- {rel} — {what}")
    for src, rel, what in extra_files:
        dst = os.path.join(work, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        lines.append(f"- {rel} — {what}")
    rows = sync_rows(shot, c.mm())
    util.write_json(os.path.join(ctx, "spec.json"), spec_for_agent(shot, topic, c.series.get("transitions"), rows))
    util.write_json(os.path.join(ctx, "task.json"), task_meta)
    if rows:                                                           # 「本镜卡点」表（任务书里那张表的数据版；渲完按它逐帧量）
        util.write_json(os.path.join(ctx, "sync.json"), {"shot": shot["id"], "note": "本镜卡点：F = 画面第一下明显变化必须出现的那一帧"
                                                         "（片内帧号）；等级 重 / 轻 / 切 / 可；表外的帧不许有跳变（比前后几帧突然大好几倍），"
                                                         "连续的单向运动不受限", "rows": rows})
        lines.append("- ctx/sync.json — 本镜卡点表（和任务书「本镜卡点」同一张：帧、等级、音乐）")
    fj, fmo, asg, ov = write_footage_ctx(c, work, shot, topic.get("motif"))
    lines.append(f"- ctx/footage.md — 这一集能用的实拍（{len(fj.get('clips', []))} 条；镜头里 Take('F01', ...)，只能用这些名字"
                 + ("；「分给这一镜的」先用：" + "、".join(f"{u} {n}" for n, u in asg) if asg else "")
                 + ("；「自带运动量」是 1× 速度下素材自己有多快，慢的配变速" if fmo else "")
                 + "；「来源」列标了 CG / 带字幕的不当主体）")
    if ov:
        lines.append("- ctx/overlays.json — 实拍元素叠层表：SR.element(E, T, img, kind, …) 能用的 kind、"
                     "每个 kind 的素材号和窗口；冒充物质的叠层只从这里来，不用 2D 画"
                     + ("；缺的种类按任务书「缺真材料就提请求」写 out/footage_requests.json" if SQ.enabled(c) else ""))
    shutil.copy2(os.path.join(util.HERE, "lib", "cheapfx.py"), os.path.join(ctx, "cheapfx.py"))
    util.write(os.path.join(ctx, "check.sh"),
               CHECK_SH.replace("{ENGINE}", eng).replace("{PY}", os.path.join(util.PARENT, c.series["engine"]["python"]))
               .replace("{MOTIF}", re.sub(r"[\"$`\\]", "", str(topic.get("motif") or "")))
               .replace("{STILL}", "--still" if task_meta.get("kind") == "keyframe" else ""))
    lines.append("- ctx/check.sh — 交付前自查：`bash ctx/check.sh <代码> <函数名>`（不要 GPU，沙箱里能跑；劣质特效、导入、@shot、Take）")
    ap = api_md(c)
    if ap:
        shutil.copy2(ap, os.path.join(ctx, "API.md"))
        lines.append("- ctx/API.md — 引擎函数签名索引（参数默认值、SR.tx 转场窗口）：要哪个按名字搜"
                     "（`rg -n 'SR.track' ctx/API.md`），不用整份读、不用翻 src/ 源码")
    lines += example_files(c, work, shot, topic, asg, ov, rows)
    if task_meta.get("kind") in ("shot", "fix"):                       # 返修 / 续跑：这一镜现在那一版的画面先放进去
        rd = c.p("shots", shot["id"], "render.mp4")
        got = look_self(c, shot, work, rd) if os.path.exists(rd) else []
        lines.append(LOOK_SAY + ("；现在有：" + "、".join(os.path.basename(r) for r, _ in got) if got else "；现在还没有（第一次渲完就有）"))
    return "\n".join(lines)


def part_name(series, p):
    """镜头表里的零件名 → 系列配置的键：中文别名（叠层 → element、碎裂 → shatter、粒子 → particles）按 parts.*.alias 换。"""
    parts = series.get("parts") or {}
    if p in parts:
        return p
    for k, v in parts.items():
        if p in (v.get("alias") or []):
            return k
    return p


def agent_job(c, kind, name, sdir, task, expect):
    n = len(os.listdir(os.path.join(sdir, "runs"))) + 1 if os.path.isdir(os.path.join(sdir, "runs")) else 1
    eng = c.renderer.root
    py = os.path.join(util.PARENT, c.series["engine"]["python"])
    src_root = os.path.join(util.PARENT, c.series["engine"]["root"])
    ac = c.cfg["agent"]
    k = "keyframe" if kind == "keyframe" else ("fix" if kind == "fix" else "shot")
    reads = [eng] + [os.path.join(src_root, d) for d in c.series["engine"].get("link", [])] + c.renderer.footage_dirs()
    reads += [os.path.join(util.HOME, ".local", "bin")] + _venv_dirs(py)
    job = {"stage": {"keyframe": "keyframe", "shot": "production", "fix": "fix"}[kind], "kind": kind, "name": name,
           "work": os.path.join(sdir, "work"), "run_dir": os.path.join(sdir, "runs", f"{n:02d}-{kind}"), "task": task,
           "allow_read": reads, "allow_write": [], "readonly": [eng], "expect": expect,
           "timeout_s": 60 * ac["timeout_min"][k], "max_turns": ac["max_turns"][k]}
    if IG.enabled(c):                                                 # 生图通道开着：任务书加「生图」一节（规则 + 这一镜现有的图）
        job["task"] += IG.task_block(c, name, job["work"])
        job["imagegen"] = True
    if SQ.enabled(c) and kind in ("shot", "fix"):                     # 素材请求通道（默认开）：任务书加「缺真材料就提请求」一节
        job["task"] += SQ.task_block(c, name, job["work"])
        job["stock"] = True
    if CX.backend(c) == "codex":                                      # Codex 代理：末尾加运行环境说明（沙箱、工具、不生图）
        job["task"] += CX.agent_suffix(kind)
        job["images"] = agent_images(job["work"], task)               # 图用 codex exec -i 附上（31/31 次运行以前都没传图）
    return job


def agent_images(work, task="", k=10):
    """附给 Codex 代理的图（work 里的绝对路径），最多 k 张，按这个顺序：ctx/look/self/ 这一镜刚渲的联系表，发回文字点名的卡点帧
    看到的帧（返修时），ctx/look/stock/ 到货素材的联系表（素材请求通道写的），再是其余卡点帧。DeepSeek（Claude Code）代理用 Read 打开同一批文件（都在工作目录里，blockReadsOutsideWorkingDirectories 不挡）。"""
    ls = lambda sub: [os.path.join(work, sub, f) for f in sorted(os.listdir(os.path.join(work, sub)))  # noqa: E731
                      if f.lower().endswith((".jpg", ".jpeg", ".png"))] if os.path.isdir(os.path.join(work, sub)) else []
    me, rv, st = ls(LOOK_SELF), ls(os.path.join("ctx", "review")), ls(os.path.join("ctx", "look", "stock"))
    pick = lambda pre: [p for p in me if os.path.basename(p).startswith(pre)]  # noqa: E731
    named = {m for ln in str(task or "").splitlines() if "这次点名的帧" in ln for m in re.findall(r"hit_f\d+\.jpg", ln)}
    hot = [p for p in pick("hit_") if os.path.basename(p) in named]
    return list(dict.fromkeys(pick("sheet") + hot + rv + st + pick("hit_")))[:k]


RENDER_LOCK = threading.Lock()                                  # 几镜并行时渲染排队（一次只跑一个）
def par_map(c, fn, items):
    """几镜同时做（config agent.parallel，默认 4）：各镜目录独立，记账、日志、生图、渲染各有锁。
    哪一镜抛错（Stop、Ctrl-C）：没开始的取消，在跑的做完，再把第一个错抛出去。"""
    n = max(1, int(c.cfg["agent"].get("parallel", 4)))
    if n == 1 or len(items) <= 1:
        return [fn(x) for x in items]
    out, err = [], None
    with ThreadPoolExecutor(max_workers=n) as ex:
        futs = [ex.submit(fn, x) for x in items]
        for f in futs:
            try:
                out.append(f.result())
            except BaseException as e:  # noqa: BLE001
                err = err or e
                out.append(None)
                if isinstance(e, KeyboardInterrupt):
                    util.HALT.set()                                 # 在跑的代理 2 秒内收工
                for g in futs:
                    g.cancel()
    if err:
        raise err
    return out


def run_agent(c, job):
    from . import mockdata
    mf = mockdata.agent if c.mock else None
    if CX.backend(c) == "codex":
        runner = lambda j: CX.run_agent(j, c.cfg, c.cc, c.ledger, c.log, mock_fn=mf)  # noqa: E731
    else:
        runner = lambda j: A.run_agent(j, c.cfg, c.ledger, c.log, mock_fn=mf)  # noqa: E731
    res = runner(job)
    if util.HALT.is_set():
        raise KeyboardInterrupt
    if IG.enabled(c):                                                 # 生图通道：有请求就生成，图到了再叫一次代理接进去
        res = IG.after_agent(c, job, res, runner)
    if SQ.enabled(c):                                                 # 素材请求通道：有请求就去素材站找、下、入库，到了再叫一次代理接进去
        res = SQ.after_agent(c, job, res, runner, refresh=lambda j: refresh_footage_ctx(c, j))
    return res


def refresh_footage_ctx(c, job):
    """素材请求到了以后、再叫代理之前：这一镜 ctx/ 里的素材表、叠层表、ctx/stock.json 换成新的（新素材在表里）。"""
    work = job["work"]
    spec = util.read_json(os.path.join(work, "ctx", "spec.json")) or {"id": job["name"]}
    write_footage_ctx(c, work, spec, (c.j("topic.json") or {}).get("motif"))


# ============================================================================ 2 五秒动态样片（主关口）+ 各段代表帧（辅助）
# PPT 感 = 切得快、镜内不动。所以先把全片最要劲的 5 秒（第一个 DROP 前后）真做成动态、渲成 mp4，
# 审片看动态联系表 + 机器量的镜内运动量；不过就改（最多改 sample_rounds 轮），过了它就是后面每个镜头的尺子。
# 静帧代表帧保留为样片没覆盖的段落的辅助参考：做一次、审一次，不过也不重做。
SAMPLE = {"seconds": 5.0, "before_s": 2.0, "fix_rounds": 2}
# 镜内运动量的参数默认值：和 gates.py 的 V2["motion"] 同一个量法（那边有就以那边为准，系列 gates.motion 再覆盖），
# 样片过了，交付时的硬门禁「每镜镜内运动量」才不会再卡。
# 11-04（拿抽搐、轻微摆动代替动效）：判「镜内有没有进展」改用有效运动量 eff（分块量位移，一拍窗口里来回的部分不算，
# G.eff_stats 调 p2/tools/effmotion.py），J = 原地来回占比，pingpong = 同一张图宽紧来回跳的次数。拿不到 eff（没有引擎 Python）
# 时退回旧的帧差口径。eff 这一组的默认值和 gates.V2["motion"]["eff"] 一致（那边有就以那边为准）。
MOTION = {"size": [96, 54], "cut_abs": 14, "cut_k": 4, "still_eps": 0.5, "ref_mean": {"Light": 12.52, "点火": 10.44},
          "shot_min_ratio": 0.5, "still_frac_max": 0.12, "min_frames": 12,
          "eff": {"grid": [6, 4], "hwin": 24, "exempt_f": 8, "ppath": 3.0, "ref_eff": {"Light": 9.45, "点火": 7.60},
                  "ratio": 0.57, "j_max": 0.45, "pp_max": 3, "content_warn": 2.5, "hwin_long": 48, "j_long_max": 0.5,
                  "ret_win": 6, "ret_n": 5, "ret_frac": 0.4,
                  # 逐镜渲完的「静图 + 推拉」：几乎全是刚性位移、图里自己没变化（静图横移 rigid 0.98 / content 0.17；SR.track 正规跟拍
                  # rigid 0.66–0.74 / content 1.2–2.1；原片 100 帧窗 content 最低 2.99）。只在逐镜发回里用，整片门禁照旧只报警
                  "pan_rigid": 0.9, "pan_content": 0.5,
                  "trough_ratio": 0.55}}                          # 计划里的低谷镜头线放低（G.trough_shots）
MOTION_REF = ("有效运动量（一拍里来回的部分、来回的亮度都已扣掉）：Light 9.5、点火 7.6，原地来回占比 12–21 %；"
              "试跑稿 v1（判为 PPT 感）4.8；近乎静止 Light 10.5 %、点火 3.2 %")


def motion_ref(mc):
    """给审片 / 样片 / 报告的运动量参照一句话：系列 gates.motion.ref_say优先，没写 = CHROMA 的 MOTION_REF"""
    return str((mc or {}).get("ref_say") or MOTION_REF)
# 给代理的字不写具体物质，换成通用的「叠层」）
TRACK_CAL = ("默认参数下世界单色、压暗、略虚：世界每帧 8 px 约 3.5–4.7、10 px 约 5.0（这一格的进展靠世界在走、主角在变）；"
             "前景实拍叠层（front=<叠层 kind> = SR.element）只当陪衬，算不进这一格的进展——它会把量出来的数抬高，但主角不动 + 满屏叠层审片照样判静图；"
             "细节见 ctx/HANDBOOK.md §3b 和 §5b")
FIX_MOVE = ("镜内没有进展：每一格做完一件单向的事，按量给（实测标定，有纹理的画面）——横移每帧 8 px 左右（约 6.9）或推近每帧 1 % 左右，"
            f"和内容变化一起用；或者 SR.track 钉住主角、让有纹理的世界从身后单向流过（{TRACK_CAL}）；"
            "慢素材配两三倍变速（Take(speed=…)；低于 60 fps 的素材写 interp='blend' 或变速到 60 ÷ 帧率倍以上）；"
            "切点上换新画面（换 Take 或 src 时段），落地用 SR.slam / SR.whip / SR.push_thru；主体把 spec 的 action 做完一段（长、裂、冲、变形）。"
            "先看「几乎不动的帧段」（有的话）：就在那几段补一件单向的事。纯色底上的东西量出来接近 0（纯色底就让主体自己做完 action，或换掉占画面大头的东西）。"
            "原地来回（摆、呼吸、抖、整帧泵拍）量出来是 0，不拿它凑；打击只放在「本镜卡点」表的重 / 轻帧上")
FIX_SWAY = ("原地摆动充数：这一镜的运动大半是原地来回（sin/cos 摆、缩放呼吸、来回转、整帧抖、每拍 / 16 分整帧泵、关键帧推了又拉回、同一张图宽紧来回跳、"
            "慢慢漂过去又漂回来、同一版式每拍跳一下再回到原位、两张图来回轮换、亮度一明一暗）。"
            "把这些来回全部删掉（不是减小幅度），在同一处换成一件单向的事：横移 / 推近一路走到出画（横移每帧 8 px 左右、推近每帧 1 % 左右）、"
            "SR.track 让世界从主角身后单向流过、素材变速往前跑、主体做完 spec 的 action，或者在切点上换成新东西；"
            "震屏只留在大卡点，8 帧内衰减完")
# 渲完每镜马上量运动量、发回时给的一行实测标定（量纲可以写，门禁线不写）
MOTION_CAL = ("实测标定（编排器同一把尺子）：有纹理的素材横移 8 px/帧 ≈ 6.9；SR.track 默认参数（世界单色、压暗、略虚）8 px/帧 ≈ 3.5–4.7、"
              "10 px/帧 ≈ 5.0（素材纹理、亮度不同差三成上下；前景实拍叠层只当陪衬，不拿它凑数）；"
              "纯色底 ≈ 0；纯色底上主体每帧长 1 % ≈ 1.0；低于 60 fps 的素材 1× 不写 interp='blend' ≈ 0.2–0.9（重复帧被量成切点）")
FIX_PAN = ("画面大半是同一张图整体平移 / 缩放（静图 + 推拉，量出来几乎全是刚性位移、图里自己没有变化）：运镜只当配角——"
           "换成自己在动的素材（Take 变速往前跑）、SR.track 的世界每个计划切点换素材或时段、主体把 spec 的 action 做完（长、裂、冲），"
           "或者切点上换新画面；实拍叠层 SR.element 只当陪衬，主角不动 + 满屏叠层也算静图")
FIX_REGRESS = ("返修不许把运动量改倒退：按意见删掉一个运动来源（运镜、光圈、碎片、2D 粒子、纹理）时，在同一处换一件会动的真东西补上"
               "（实拍叠层 SR.element 单向飘 / 迎面推、世界从主角身后流过、素材变速往前跑、主体一路长 / 裂 / 冲，或者切点上换新画面）")
FIX_BRIEF = ("点名的必改，同类一起改：下面每一条点名的地方都要改到；同一类毛病（同一种零件、同一种做法、同一种特效来源）"
             "在这一镜别处也有的，一起改掉，不要只改点名的那一处")
FIX_SYNC = ("卡点没做到（编排器渲完按「本镜卡点」表逐帧量的）：每条写了这一帧现在的帧差、周围中位、要多少倍。「重」帧让新构图从这一帧起"
            "（或冲击帧盖在新构图上、主角字砸入），「轻」帧真切或字换词，「切」帧真的换构图（换素材 / 景别 / 机位、实拍和字卡交替）；"
            "帧号写成整数常量、用 T.F >= F 切换。"
            "闪白、反相、整屏亮度跳、冲击闪完回到旧构图都不算卡点（编排器比这一帧前后的构图）；"
            "没起音的帧上出现的跳变（比前后几帧突然大好几倍），挪到最近的「重 / 轻」帧，或者收成连续的单向运动（不受限）")


def sample_cfg(c):
    """参数默认值（系列配置 sample 可覆盖；config.json 的 rounds.sample_rounds = 最多改几轮）。"""
    d = dict(SAMPLE)
    d.update(c.series.get("sample") or {})
    d["fix_rounds"] = int((c.cfg.get("rounds") or {}).get("sample_rounds", d["fix_rounds"]))
    return d


def motion_cfg(c):
    g2 = (getattr(G, "V2", None) or {}).get("motion") or {}
    ser = c.g.get("motion") or {}
    mc = dict(MOTION)
    mc.update(g2)
    mc.update(ser)
    mc["eff"] = {**MOTION["eff"], **(g2.get("eff") or {}), **(ser.get("eff") or {})}     # eff 一组逐项合并（后面的覆盖前面的；
    #                                                       dict(a, **b, **c) 在 b、c 有同名键时报 TypeError：系列写了 eff.ref_eff 就会撞上）
    refs = mc["ref_mean"]
    mc["need"] = mc["shot_min_ratio"] * (min(refs.values()) if mc.get("ref_pick", "min") == "min" else sum(refs.values()) / len(refs))
    try:
        mc["need_eff"] = float(G.eff_need(mc))
    except Exception:  # noqa: BLE001  gates 还没有 eff_need：按同一个公式算
        e = mc["eff"]
        mc["need_eff"] = e["ratio"] * min(e["ref_eff"].values())
    return mc


def renderer_py(c):
    """引擎 Python（有 numpy / cv2 / librosa；门禁的量法工具跑在它里面）。没有就 None（门禁记「没量」）。"""
    py = getattr(getattr(c, "renderer", None), "py", None)
    return py if py and os.path.exists(py) else None


def sample_window(bm, sl, sec=5.0, before=2.0):
    """第一个 DROP 前 before 秒起、共 sec 秒（没有 DROP 就片头 sec 秒）。返回 (起帧, 止帧(不含), 覆盖到的镜头)。"""
    n, total = int(round(sec * 60)), sl["total_f"]
    drop = next((h["F"] for h in bm["hits"] if h["kind"] == "drop"), None)
    a = 0 if drop is None else int(drop - round(before * 60))
    a = max(0, min(a, total - n))
    b = min(total, a + n)
    return a, b, [s for s in sl["shots"] if s["start_f"] < b and s["end_f"] > a]


def _gray_frames(video, w=96, h=54):
    n = w * h
    p = subprocess.Popen(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i", video, "-map", "0:v:0",
                          "-vf", f"scale={w}:{h},format=gray", "-f", "rawvideo", "-"], stdout=subprocess.PIPE, env=media.ENV)
    try:
        while True:
            b = p.stdout.read(n)
            if len(b) < n:
                break
            yield b
    finally:
        p.stdout.close()
        p.wait()


def motion_exempt(bm, a, b):
    """不算镜内运动量的帧（视频 [a, b) 里的本地帧号）：屏息那一拍、最后一击之后的海报停留（原片这两处本来就静）。"""
    ex, beat = set(), int(round(bm.get("beat_f") or 0))
    for h in bm.get("hits") or []:
        if h["kind"] == "breath" and beat:
            ex |= {f - a for f in range(h["F"], h["F"] + beat) if a <= f < b}
        if h["kind"] == "final":
            ex |= {f - a for f in range(h["F"], b) if a <= f}
    return ex


def motion_stats(video, groups, mc, exempt=(), py=None, hits=(), need_by=None):
    """镜内运动量，和 gates.py 同一口径。先照旧量帧差（96×54 灰度相邻帧平均绝对差 0–255；
    帧差 > max(cut_abs, cut_k × (全段中位 + 1)) 的是切点帧，不算镜内；镜内帧差 < still_eps 算近乎静止），
    再用 G.eff_stats 量有效运动量（py = 引擎 Python；hits = 大卡点帧，冲击余波豁免）。
    groups = {名字: (起帧, 止帧)}（这条视频里的帧号）；exempt 里的帧不算均值（屏息、片尾海报）。
    返回 {"all": 统计, 名字: 统计}，统计 = {mean, median, still, n, ok, eff, J, pingpong, content, sway_ok}；
    量到 eff 时 ok = eff ≥ need_eff（没有进展）、sway_ok = J ≤ j_max 且乒乓 < pp_max（原地摆动充数）；量不到 eff 时 ok 照旧按帧差。
    need_by = {名字: 线}：这几组的线不用 need_eff（计划里的低谷镜头放低，motion_needs）。"""
    gen = getattr(media, "gray_frames", None) or _gray_frames
    d, prev = [0.0], None
    for b in gen(video, *mc["size"]):
        if prev is not None:
            d.append(sum(map(abs, map(operator.sub, b, prev))) / len(b))
        prev = b
    med = statistics.median(d[1:]) if len(d) > 1 else 0.0
    thr = max(mc["cut_abs"], mc["cut_k"] * (med + 1))
    cut, ex = {i for i in range(1, len(d)) if d[i] > thr}, set(exempt)

    def st(a, b, is_all=False):
        idx = [i for i in range(max(1, a + 1), min(len(d), b)) if i not in cut]
        v = [d[i] for i in idx if i not in ex]
        if not idx:
            return {"mean": None, "median": None, "still": None, "n": 0, "ok": None}
        still = sum(1 for i in idx if d[i] < mc["still_eps"]) / len(idx)
        still_ex = sum(1 for x in v if x < mc["still_eps"]) / len(v) if v else 0.0   # 不算屏息、片尾海报（返修点名用）
        mean = sum(v) / len(v) if v else 0.0
        ok = (still <= mc["still_frac_max"]) if is_all else (mean >= mc["need"] if len(v) >= mc["min_frames"] else None)
        return {"mean": round(mean, 2), "median": round(statistics.median(v), 2) if v else None, "still": round(still, 3),
                "still_ex": round(still_ex, 3), "n": len(v), "ok": ok}
    out = {"all": st(0, len(d), True)}
    for k, (a, b) in (groups or {}).items():
        out[k] = st(a, b)
    out["all"]["cuts"], out["all"]["cut_thr"] = len(cut), round(thr, 2)
    ef, err = None, None
    if py and groups and hasattr(G, "eff_stats"):
        try:
            ef = G.eff_stats(video, {k: (int(a), int(b)) for k, (a, b) in groups.items()}, mc, py, hits=sorted(hits), exempt=sorted(ex))
        except Exception as e:  # noqa: BLE001  量法工具出错：退回帧差口径，原因记下来
            err = f"{type(e).__name__}: {str(e)[:200]}"
    e_ = mc.get("eff") or {}
    need_e = mc.get("need_eff") or e_.get("ratio", 0.5) * min((e_.get("ref_eff") or {"点火": 8.65}).values())
    for k in (groups or {}):
        v = ((ef or {}).get("shots") or {}).get(k)
        m = out[k]
        if not v or v.get("eff") is None:
            m["sway_ok"] = None
            continue
        m.update(eff=round(float(v["eff"]), 2), J=round(float(v.get("J") or 0), 3), pingpong=int(v.get("pingpong") or 0),
                 content=None if v.get("content") is None else round(float(v["content"]), 2), d96=v.get("d96"),
                 J_long=v.get("J_long"), returns=int(v.get("returns") or 0), sway_why=list(v.get("sway_why") or []),
                 rigid=None if v.get("rigid") is None else round(float(v["rigid"]), 2))
        m["need"] = round(float((need_by or {}).get(k, need_e)), 3)
        m["ok"] = (m["eff"] >= m["need"]) if m["n"] >= mc["min_frames"] else None
        m["sway_ok"] = (bool(v["ok_sway"]) if "sway_why" in v else
                        m["J"] <= e_.get("j_max", 0.45) and m["pingpong"] < e_.get("pp_max", 3))
    if ef and ef.get("film"):
        fm = ef["film"]
        out["all"].update(eff=None if fm.get("eff") is None else round(float(fm["eff"]), 2),
                          J=None if fm.get("J") is None else round(float(fm["J"]), 3))
    out["all"]["eff_measured"] = bool(ef)
    if err:
        out["all"]["eff_error"] = err
    return out


def motion_line(name, m):
    if not m or m.get("mean") is None:
        return f"{name}：帧太少，没量"
    if m.get("eff") is not None:
        pp = m.get("pingpong") or 0
        why = m.get("sway_why") or []
        return (f"{name}：有效运动量 {m['eff']:g}（帧差 {m['mean']:g}，其中原地来回 {100 * (m.get('J') or 0):.0f} %"
                + (f"，同一张图宽紧来回跳 {pp} 次" if pp >= 2 else "")
                + (f"，两拍窗口里原地来回 {100 * (m.get('J_long') or 0):.0f} %（慢摆 / 漂浮）" if "J_long" in why else "")
                + (f"，切了又回到这一镜前面的构图 {m.get('returns')} 次" if "returns" in why else "")
                + f"）、近乎静止 {100 * m['still']:.0f} %（{m['n']} 帧）"
                + (" ← 没有进展" if m.get("ok") is False else "") + (" ← 原地摆动充数" if m.get("sway_ok") is False else ""))
    return (f"{name}：镜内帧差均值 {m['mean']:g}，中位 {m['median'] if m.get('median') is not None else '?'}，"
            f"近乎静止 {100 * m['still']:.0f} %（{m['n']} 帧）" + ("" if m.get("ok") is not False else " ← 没有进展"))


def motion_want(mc):
    """给审片 / 返修看的「要什么」：不写门禁阈值（11-04 防 Goodhart），只给原片参照。"""
    refs = (mc.get("eff") or {}).get("ref_eff") or {}
    rng = f"{min(refs.values()):.1f}–{max(refs.values()):.1f}" if refs else "8.7–10.1"
    return f"每格都往前走一步（原片有效运动量 {rng}），原地来回不算"


def motion_needs(sl, bm, mc):
    """{镜头: 线}：计划里的低谷镜头（节拍表里能量比前后两段都低的那一段，G.trough_shots）线 = need_eff × trough_ratio；别的镜头不在里面。"""
    tr = G.trough_shots(bm, sl) if hasattr(G, "trough_shots") else set()
    k = float((mc.get("eff") or {}).get("trough_ratio", 1.0))
    return {sid: mc["need_eff"] * k for sid in tr}


def big_frames(bm, a=0, b=None):
    """大卡点（big / drop / final）的帧，换成视频 [a, b) 里的本地帧号（有效运动量的冲击余波豁免用）。"""
    return [h["F"] - a for h in (bm or {}).get("hits") or [] if h["kind"] in ("big", "drop", "final")
            and a <= h["F"] < (b if b is not None else 1 << 30)]


def motion_by_shot(c, video, sl, bm):
    """成片按镜头量（审片参考；有效运动量结果同时存到 video + ".eff.json"）。"""
    mc = motion_cfg(c)
    return motion_stats(video, {s["id"]: (s["start_f"], s["end_f"]) for s in sl["shots"]}, mc, motion_exempt(bm, 0, sl["total_f"]),
                        py=renderer_py(c), hits=big_frames(bm), need_by=motion_needs(sl, bm, mc))


def render_sample(c, r, shots, a, b):
    """样片 = 覆盖窗口的几个镜头的渲染拼起来、裁到窗口（同一套 x264 参数）。返回无声视频的路径。"""
    ffl = c.p("logs", "ffmpeg.log")
    os.makedirs(c.p("film"), exist_ok=True)
    full = c.p("film", f"sample_r{r}_shots.mp4")
    media.concat([c.p("shots", s["id"], "render.mp4") for s in shots], full, log_path=ffl)
    off = shots[0]["start_f"]
    out = c.p("film", f"sample_r{r}.mp4")
    media.ff(["-i", full, "-vf", f"trim=start_frame={a - off}:end_frame={b - off},setpts=PTS-STARTPTS", "-r", "60"]
             + media.H264 + [out], log_path=ffl)
    os.remove(full)
    return out


def sample_with_audio(c, video, a):
    """给用户看的样片带上配乐（从原曲对应的位置起）；没配乐就是无声的。"""
    out = c.p("film", "sample.mp4")
    music = c.j("inputs/music.json")
    path = (music["file"] if os.path.isabs(music["file"]) else c.p(music["file"])) if music else None
    if path and os.path.exists(path):
        try:
            media.mux(video, path, out, audio_start=float(music.get("start_s", music["downbeat_s"])) + a / 60.0,
                      log_path=c.p("logs", "ffmpeg.log"), loud=media.LOUD)
            return out
        except RuntimeError as e:
            c.log(f"  样片配乐没混上（{str(e)[:120]}），先给无声的", echo=False)
    shutil.copy2(video, out)
    return out


def shot_brief(s):
    src = s.get("subject_src") or {}
    return (f"主体「{s.get('subject', '')}」（{src.get('kind', '?')} {src.get('file') or src.get('gen') or ''}）"
            f"做「{s.get('action') or s.get('event', '')}」；大字 {' / '.join(shot_words(s)) or '—'}；数字 {s.get('number') or '无'}")


def sample_review(c, r, video, shots, a, b, sc):
    topic = c.j("topic.json")
    fdir, ffl, n = c.p("frames", "sample", f"r{r}"), c.p("logs", "ffmpeg.log"), b - a
    groups = {s["id"]: (max(s["start_f"], a) - a, min(s["end_f"], b) - a) for s in shots}
    mc = motion_cfg(c)
    bm_ = c.j("beatmap.json")
    mo = motion_stats(video, groups, mc, motion_exempt(bm_, a, b), py=renderer_py(c), hits=big_frames(bm_, a, b),
                      need_by=motion_needs(c.j("shotlist.json"), bm_, mc))
    imgs = [(f"样片 3×3 联系表（第 {a}–{b} 帧均匀 9 帧）",
             media.sheet(video, [int((i + 0.5) * n / 9) for i in range(9)], os.path.join(fdir, "sheet.jpg"), log_path=ffl))]
    for s in shots:
        s0, s1 = groups[s["id"]]
        if s1 - s0 < 3:
            continue
        fr = sorted({s0, (s0 + s1) // 2, s1 - 1})                     # 头 / 中 / 尾：事情有没有单向往前走（原来的 3 帧最多跨 16 帧，看不出摆）
        imgs.append((f"{s['id']} 头 / 中 / 尾 3 帧（样片第 {fr[0]}、{fr[1]}、{fr[-1]} 帧）：事情有没有单向往前走（不是只在动）",
                     media.sheet(video, fr, os.path.join(fdir, f"strip_{s['id']}.jpg"), cols=3, log_path=ffl)))
        imgs.append((f"{s['id']} 切线图（正中一条横线，每帧叠 3 px、往下是时间：斜纹 / 展开 = 推进，竖直线 = 静止，锯齿 / 波浪 = 原地摆）",
                     media.kymo(video, s0, s1, os.path.join(fdir, f"kymo_{s['id']}.jpg"), log_path=ffl)))
    cut_lines = []
    for p_, s in zip(shots, shots[1:]):
        F = s["start_f"] - a
        if 2 <= F < n - 2:
            imgs.append((f"{p_['id']} → {s['id']} 切点：左 F-2、中 F（{s['id']} 第一帧）、右 F+2",
                         media.sheet(video, [F - 2, F, F + 2], os.path.join(fdir, f"cut_{s['id']}.jpg"), cols=3, log_path=ffl)))
            cut_lines.append(f"- 图 {len(imgs)}：{p_['id']} → {s['id']}：{tx_text(s.get('transition_in'))}")
    lines = "\n".join(f"- {s['id']}（第 {s['start_f']}–{s['end_f']} 帧，{s.get('look', '')}）：{shot_brief(s)}" for s in shots)
    drop = any(h["kind"] == "drop" and a <= h["F"] < b for s in shots for h in s["hits"])
    prompt = c.prompt("02_sample_review", TITLE=topic["title_cn"], ROUND=str(r), A=str(a), B=str(b), AS=f"{a / 60:.2f}",
                      BS=f"{b / 60:.2f}", WHERE="，第一个 DROP 前后" if drop else "，片头", SHOTS=lines,
                      CUTS="\n".join(cut_lines) or "（样片里没有镜头之间的切点）",
                      MOTION="\n".join([motion_line("整段", mo["all"])] + [motion_line(s["id"], mo[s["id"]]) for s in shots]),
                      MOTION_REF=motion_ref(mc), WANT=motion_want(mc))
    ids = sorted(s["id"] for s in shots)
    o, _ = review_llm(c).ask_json("sample_review", f"sample_review_{r}", c.system, prompt, imgs, c.schema("sample_review"),
                          check=lambda o: [] if sorted(x["id"] for x in o["shots"]) == ids else [f"shots 要正好是 {ids}"],
                          ctx={"shots": ids, "round": r})
    res = {x["id"]: dict(x, issues=list(x.get("issues") or [])) for x in o["shots"]}
    for s in shots:                                                   # 机器量：没有进展、原地摆动充数分开判不过（交付时都是硬门禁）
        m = mo[s["id"]]
        if m.get("sway_ok") is False:
            res[s["id"]]["pass"] = False
            res[s["id"]]["issues"].append({"rule": "底线 · 原地摆动充数（机器量）", "fix": FIX_SWAY, "where": motion_line(s["id"], m)})
        if m.get("ok") is False:
            res[s["id"]]["pass"] = False
            res[s["id"]]["issues"].append({"rule": "底线 · 镜内有进展（机器量：有效运动量）", "fix": FIX_MOVE,
                                           "where": f"{motion_line(s['id'], m)}；要{motion_want(mc)}"})
    whole = mo["all"].get("ok") is not False
    if all(v["pass"] for v in res.values()) and (not whole or o.get("motion_ok") is False):
        low = min(res, key=lambda k: ((mo.get(k) or {}).get("eff", (mo.get(k) or {}).get("mean")) or 0, res[k].get("score", 5)))
        res[low]["pass"] = False
        res[low]["issues"].append({"rule": "必做 · 镜内有进展", "fix": FIX_MOVE,
                                   "where": (motion_line("整段", mo["all"]) if not whole else "审片看图觉得镜内没有进展") + f"；从有效运动量最低的 {low} 改起"})
    failed = sorted(k for k, v in res.items() if not v["pass"])
    rv = {"round": r, "window": [a, b], "video": os.path.relpath(video, c.ep), "motion": mo, "motion_ok": whole,
          "model_motion_ok": o.get("motion_ok"), "verdict": o.get("verdict", ""), "shots": res, "failed": failed,
          "pass": not failed, "at": util.iso()}
    util.write_json(c.p(f"sample_review_{r}.json"), rv)
    per = ", ".join(f"{k} {mo[k].get('eff', mo[k]['mean'])}" + (f"（来回 {100 * mo[k]['J']:.0f} %）" if mo[k].get("J") is not None else "")
                    for k in groups)
    c.log(f"样片第 {r} 轮：{'过' if not failed else '不过 ' + str(failed)}；{motion_line('镜内运动量', mo['all'])}；"
          f"各镜{'有效运动量' if mo['all'].get('eff_measured') else '帧差均值'} {per}（要{motion_want(mc)}）")
    return rv


def is_short(c):
    """试跑短片模式（--short N）：返回小节数，不是就 None。命令行没给时看 inputs/music.json 记的 short。"""
    return getattr(c, "short", None) or (c.j("inputs/music.json") or {}).get("short") or None


def stage_outputs(c, n, outs):
    """环节 n 的产物清单（判断「已完成，跳过」用）：短片模式第 2 环节只写 sample.json（跳过的记录）。"""
    return ["sample.json"] if n == 2 and is_short(c) else outs


def stock_inventory(c, topic, sl):
    """开拍前素材盘点（镜头表之后、制作之前；第 2、3 环节开头都调，按镜头表指纹只做一次）：每镜的主体 / 世界层 / 叠层够不够、导演的
    footage_wish；缺的自动去素材站找（lib/stockreq.inventory），找不到的记进 stock_inventory.json 和报告。出错只记日志，不拦。"""
    if not SQ.enabled(c):
        return None
    try:
        return SQ.inventory(c, sl, topic)
    except (KeyboardInterrupt, util.Stop):
        raise
    except Exception as e:  # noqa: BLE001
        c.log(f"  注意：开拍前素材盘点出错（{type(e).__name__}: {str(e)[:160]}），照现有素材开拍", echo=False)
        return None


def s2_sample(c):
    topic, bm, sl = c.j("topic.json"), c.j("beatmap.json"), c.j("shotlist.json")
    inputs.ensure(c, topic)
    stock_inventory(c, topic, sl)                                     # 开拍前素材盘点：缺的真材料自动去找（不要向我确认）
    if is_short(c):                                                   # 试跑短片（试跑只要 10–30 秒）：不做样片和代表帧
        util.write_json(c.p("sample.json"), {"skipped": "short", "bars": is_short(c), "at": util.iso(),
                                             "note": "试跑短片模式（--short）：不做 5 秒样片和静帧代表帧，直接全片制作；样片的尺子作用由全片审片代替"})
        util.write_json(c.p("keyframes.json"), {"aux": True, "sections": [], "skipped": "short", "note": "试跑短片模式：不做代表帧"})
        c.log(f"试跑短片模式（{is_short(c)} 小节）：跳过 5 秒样片和静帧代表帧，直接进制作")
        return
    c.renderer.setup(bm, sl)
    sc = sample_cfg(c)
    a, b, shots = sample_window(bm, sl, sc["seconds"], sc["before_s"])
    c.log(f"5 秒样片：第 {a}–{b} 帧（{a / 60:.2f}–{b / 60:.2f}s），镜头 {[s['id'] for s in shots]}；不过就改，最多改 {sc['fix_rounds']} 轮")
    rounds, rv = [], None
    for r in range(1, sc["fix_rounds"] + 2):
        rv = c.j(f"sample_review_{r}.json")
        if rv is None:
            for s in shots:                                           # 第 1 轮真做；之后是上一轮返修过的版本（done.json 缓存）
                make_shot(c, s, "shot", topic, sl)
            rv = sample_review(c, r, render_sample(c, r, shots, a, b), shots, a, b, sc)
        rounds.append({"round": r, "pass": rv["pass"], "failed": rv["failed"], "motion": rv["motion"]["all"]})
        if rv["pass"] or r > sc["fix_rounds"]:
            break
        fdir = c.p("frames", "sample", f"r{r}")
        for sid in rv["failed"]:
            s = next(x for x in sl["shots"] if x["id"] == sid)
            d = c.j(f"shots/{sid}/done.json") or {}
            if d.get("kind") == "fix" and d.get("round") == f"sample{r}":
                continue                                              # 续跑：这一轮已经返修过
            imgs = [p for p in (os.path.join(fdir, f"strip_{sid}.jpg"), os.path.join(fdir, f"kymo_{sid}.jpg"),
                                os.path.join(fdir, f"cut_{sid}.jpg")) if os.path.exists(p)]
            make_shot(c, s, "fix", topic, sl, issues=rv["shots"][sid]["issues"], rnd=f"sample{r}", review_imgs=imgs)
    sample_with_audio(c, c.p(rv["video"]), a)
    sheet = c.p("frames", "sample", "sheet.jpg")
    shutil.copy2(c.p("frames", "sample", f"r{rv['round']}", "sheet.jpg"), sheet)
    util.write_json(c.p("sample.json"), {
        "window": [a, b], "seconds": [round(a / 60, 2), round(b / 60, 2)], "shots": [s["id"] for s in shots],
        "pass": rv["pass"], "rounds": rounds, "unresolved": rv["failed"], "motion": rv["motion"]["all"],
        "motion_ref": motion_ref(motion_cfg(c)), "want": motion_want(motion_cfg(c)),
        "video": "film/sample.mp4", "sheet": os.path.relpath(sheet, c.ep), "at": util.iso()})
    c.log(f"样片{'过了' if rv['pass'] else '改满 ' + str(sc['fix_rounds']) + ' 轮还没全过（' + ', '.join(rv['failed']) + '），照样往下做，写进报告'}："
          f"film/sample.mp4；它的联系表给后面每个镜头当尺子")
    keyframe_stills(c, topic, bm, sl, skip={s["section"] for s in shots})


def keyframe_stills(c, topic, bm, sl, skip=()):
    """各段代表帧（辅助，不再是关口）：样片没覆盖的段落各做一张静帧、审一次，意见只当参考；不过也不重做。
    config.json 的 rounds.keyframe_rounds = 0 时不做。"""
    if not int((c.cfg.get("rounds") or {}).get("keyframe_rounds", 1)):
        util.write_json(c.p("keyframes.json"), {"aux": True, "sections": [], "note": "keyframe_rounds = 0：不做代表帧"})
        return
    kfs = c.j("keyframes.json") or {"aux": True, "sections": []}
    if not kfs["sections"]:
        for s in bm["sections"]:
            if s["id"] in skip:
                continue
            hs = [h["F"] for h in bm["hits"] if s["start_f"] <= h["F"] < s["end_f"] and h["kind"] in ("big", "drop", "final")]
            F = min(hs[0] + 3, s["end_f"] - 1) if hs else (s["start_f"] + s["end_f"]) // 2
            kfs["sections"].append({"id": s["id"], "F": F, "shot": shot_at(sl, F)["id"], "pass": False, "issues": []})
    kfs["note"] = "辅助参考（颜色、质感）：做一次、审一次，不过也不重做；主关口是 5 秒样片（sample.json）"
    for k in kfs["sections"]:
        if k.get("made_round") == 1 and os.path.exists(c.p("frames", f"key_{k['id']}.png")):
            continue                                                  # 续跑：已经做过
        make_keyframe(c, topic, sl, k, 1)
        k["made_round"] = 1
        util.write_json(c.p("keyframes.json"), kfs)
    if kfs["sections"]:
        rv = c.j("keyframe_review_1.json")
        if rv is None:
            todo = kfs["sections"]
            imgs = [(f"{k['id']} 代表帧（第 {k['F']} 帧，镜头 {k['shot']}）", c.p("frames", f"key_{k['id']}.png")) for k in todo]
            ids = [k["id"] for k in todo]
            o, _ = review_llm(c).ask_json("keyframe_review", "keyframe_review_1", c.system, c.prompt("02_keyframe_review", TITLE=topic["title_cn"],
                                  NK=str(len(todo))), imgs, c.schema("keyframe_review"),
                                  check=lambda o: [] if sorted(x["id"] for x in o["sections"]) == sorted(ids) else [f"sections 要正好是 {ids}"],
                                  ctx={"sections": ids})
            rv = {"round": 1, "sections": o["sections"]}
            util.write_json(c.p("keyframe_review_1.json"), rv)
        for x in rv["sections"]:
            k = next(k for k in kfs["sections"] if k["id"] == x["id"])
            k["pass"], k["issues"] = x["pass"], x["issues"]
    kfs["unresolved"] = [k["id"] for k in kfs["sections"] if not k["pass"]]
    util.write_json(c.p("keyframes.json"), kfs)
    if kfs["sections"]:
        c.log(f"代表帧（辅助）：{[k['id'] for k in kfs['sections']]}，审片觉得要改的 {kfs['unresolved'] or '无'}（只当参考，不重做）")


def make_keyframe(c, topic, sl, k, r):
    sdir = c.p("keyframes", k["id"])
    work = os.path.join(sdir, "work")
    shot = next(s for s in sl["shots"] if s["id"] == k["shot"])
    fn = f"key_{k['id']}"
    files = prepare_ctx(c, work, shot, topic, {"kind": "keyframe", "fn": fn, "frame": k["F"], "shot": shot["id"]})
    task = c.prompt("02_keyframe_agent", TITLE=topic["title_cn"], SECTION=k["id"], FRAME=str(k["F"]), SEC=f"{k['F'] / 60:.2f}",
                    SHOT=shot["id"], SPEC=dumps(spec_for_agent(shot, topic, c.series.get("transitions"))), TOPIC=topic_brief(topic),
                    FILES=files)
    if k.get("issues"):
        task += "\n## 上一轮审查意见（在 out/key.py 上改）\n" + "\n".join(f"- {i['rule']}｜{i['where']}｜{i['fix']}" for i in k["issues"])
    err = ""
    if os.path.exists(os.path.join(work, "out", "key.py")):
        err = ("\n## 接着上一版改\nout/key.py 是之前写到一半的版本，直接在它上面改。"
               "不要重读已经读过的文件，先跑 ctx/check.sh 看现状。\n")
    for attempt in range(c.cfg["agent"]["render_retries"] + 1):
        _halt()
        res = run_agent(c, agent_job(c, "keyframe", k["id"], sdir, task + err, ["out/key.py"]))
        have = os.path.exists(os.path.join(work, "out", "key.py"))
        if res["status"] != "ok" and not (have and res["status"] in ("max_turns", "timeout")):
            err = f"\n## 上次没交付（{res['status']}）\n请写出 out/key.py。\n"
            continue
        if res["status"] != "ok":
            c.log(f"  代表帧 {k['id']}：代理 {res['status']}，但 out/key.py 已写出，先拿它渲染")
            err = ("\n## 接着上一版改\nout/key.py 是你上一次写到一半的版本，直接在它上面改。"
                   "不要重读已经读过的文件，先跑 ctx/check.sh 看现状。\n")
        err = ""
        out = c.p("frames", f"key_{k['id']}.png")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        rr = c.renderer.render_still(f"{k['id']}", os.path.join(work, "out", "key.py"), k["F"], out)
        if rr.get("interrupted"):
            raise KeyboardInterrupt
        if rr.get("ok") and os.path.exists(out):
            c.log(f"  代表帧 {k['id']}：第 {k['F']} 帧（镜头 {shot['id']}）")
            return
        err = "\n## 上次渲染报错（改 out/key.py）\n```\n" + (rr.get("log_tail") or "")[-1500:] + "\n```\n"
    raise util.Stop(5, "keyframe_failed", f"段落 {k['id']} 的代表帧做了 {c.cfg['agent']['render_retries'] + 1} 次都没渲染出来")


# ============================================================================ 3 制作
def spec_sha(s):
    return util.sha256_bytes(json.dumps(s, sort_keys=True, ensure_ascii=False).encode())[:16]


def cur_motion(c, sid):
    """这一镜现在那一版的运动量：done.json 里渲完马上量的（有死区）；没有就取最近一轮审片整片量的（没有死区）。没有返回 None。"""
    m = (c.j(f"shots/{sid}/done.json") or {}).get("motion") or {}
    if m.get("measured") and m.get("eff") is not None:
        return m
    rs = sorted((int(f[7:-5]) for f in os.listdir(c.ep) if re.fullmatch(r"review_\d+\.json", f)), reverse=True) if os.path.isdir(c.ep) else []
    for r in rs:
        v = ((c.j(f"review_{r}.json") or {}).get("motion") or {}).get(sid) or {}
        if v.get("eff") is not None:
            return dict(v, measured=True, dead=None, src=f"review_{r}")      # 整片量的没有死区：dead=None =「这一版没量死区」
    return None


def _zone_text(z):
    return (f"第 {z['a']}–{z['b']} 帧（{z['n']} 帧，帧差 {z['d96']:g}，块速度 {z['px_f']:g} px/帧、最快一块 {z.get('px_f_max', 0):g}；"
            f"6×4 分块在动的 {z['moving']} 块，上到下：{z['grid']}）")


def dead_text(dead, k=6, slow=None):
    """死区 → 一句话（片内帧号、块速度、6×4 分块里哪几块在动）。dead = None：这一版没量死区（整片审片量的、量法出错）；
    slow = 运动量最低的几个 24 帧窗（没过线又没有死区时补上，「慢但没停」的镜头也有地方下手）。"""
    if dead is None:
        return "这一版没量死区（只有整片审片量的运动量）"
    if not dead:
        if slow:
            return ("没有连续 12 帧以上几乎不动的帧段，但整镜偏慢；运动量最低的几段：" + "；".join(_zone_text(z) for z in slow[:3])
                    + "——先在这几段补一件单向的事")
        return "没有（没有连续 12 帧以上几乎不动的帧段）"
    rows = [_zone_text(z) for z in dead[:k]]
    return "；".join(rows) + (f"；另有 {len(dead) - k} 段" if len(dead) > k else "")


def motion_now_line(m, mc):
    """返修任务书 ISSUES 前面那一行（每一镜都写，包括已经过线的）。"""
    if not m or m.get("eff") is None:
        return ""
    return (f"- [现在的运动量（编排器量的，返修不许改倒退）] 有效运动量 {m['eff']:g}；要{motion_want(mc)}；"
            f"几乎不动的帧段：{dead_text(m.get('dead'), slow=m.get('slow'))}。{FIX_REGRESS}\n")


def shot_task(c, s, kind, topic, sl, files, issues=None, rnd=None):
    """制作（03_shot_agent）/ 返修（05_fix_agent）代理的任务书。返回 (任务书, 本镜卡点表)。
    模板变量：旧的 CUTS、HITS 照传（多传无害），新的 SYNC = 「本镜卡点」表（接口 5）。
    返修：ISSUES 前面加一行这一镜现在的有效运动量和死区（不管过没过线；返修不许改倒退）。"""
    i = sl["shots"].index(s)
    look = lambda x: f"{x['id']}：{x['look']}，{shot_brief(x)}，实拍 {x.get('footage', 'none')}"  # noqa: E731
    nb = [f"上一镜 {look(x)}，最后一帧 {x['end_f'] - 1}" for x in sl["shots"][max(0, i - 1):i]]
    nb += [f"下一镜 {look(x)}，第一帧 {x['start_f']}，接法「{tx_text(x['transition_in'])}」" for x in sl["shots"][i + 1:i + 2]]
    cuts = ", ".join(map(str, s["cuts_f"])) or "无"
    tr = c.series.get("transitions")
    rows = sync_rows(s, c.mm())
    sp = spec_for_agent(s, topic, tr, rows)
    hits = ", ".join(f"{h['F']}（{h['kind']}：{h['what']}" + (f"；{h['note']}" if h.get("note") else "") + "）" for h in sp["hits"]) or "无"
    txs = lambda t: tx_text(t) + (f"；引擎：{t['engine']}" if t.get("engine") else "")  # noqa: E731
    common = dict(TITLE=topic["title_cn"], SHOT=s["id"], START_F=str(s["start_f"]), END_F=str(s["end_f"]),
                  SPEC=dumps({k: v for k, v in sp.items() if k != "sync"}),     # 「本镜卡点」表是同一张（ctx/spec.json 照旧带 sync）
                  FILES=files, CUTS=cuts, HITS=hits, SYNC=sync_block(rows, s), MUST=must_do_block(s, sp, series=c.series),
                  TX_IN=txs(sp.get("transition_in") or {}), TX_OUT=txs(sp.get("transition_out") or {}))
    if kind == "shot":
        task = c.prompt("03_shot_agent", START_S=f"{s['start_f'] / 60:.2f}", END_S=f"{s['end_f'] / 60:.2f}",
                        NEIGHBORS="\n".join(nb) or "无", **common)
    else:
        rl = f"5 秒样片第 {rnd[6:]} 轮" if isinstance(rnd, str) and rnd.startswith("sample") else f"审片第 {rnd} 轮"
        rnotes = notes_for(c, rnd)
        if rnotes:
            rl += "（看片意见轮）"
        now = motion_now_line(cur_motion(c, s["id"]), motion_cfg(c))
        iv = notes_vars(c, rnotes, s["id"])
        task = c.prompt("05_fix_agent", ROUND=rl,
                        ISSUES=f"- [{FIX_BRIEF.split('：')[0]}] {FIX_BRIEF.split('：', 1)[1]}\n" + now
                        + "\n".join(f"- [{x.get('rule', '')}] {x.get('where', '')}：{x['fix']}" for x in issues or []),
                        **iv, **common)
    return task + missing_block(s), rows


ROUTES = "换个英文说法写 out/footage_requests.json 再找、设计帧、生图，或清单里别的镜没当主体的 F 号"


def missing_block(s):
    """镜头表回填、看图确认之后→ 任务书末尾：还没找到的实拍（镜头的 missing_footage：要什么、搜过什么、为什么没有）、
    分给了这一镜但没人看过图的（unseen_footage：看图后重找来的、看图时按标题选的、看图那一次没拿到判定的）。路由制作代理
    自己选。都没有就是空串。"""
    ms = [m for m in s.get("missing_footage") or [] if isinstance(m, dict)]
    us = [m for m in s.get("unseen_footage") or [] if isinstance(m, dict)]
    out = ""
    if ms:
        out += ("\n\n## 这一镜要的实拍没找到\n" + "\n".join(
            f"- {m.get('id')}（{m.get('at') or '实拍'}）要「{m.get('need')}」，搜过「{m.get('searched')}」：{m.get('why')}"
            + (f"；看图时建议：{m['route']}" if m.get("route") else "") for m in ms)
            + f"\n你自己选路：{ROUTES}；NOTES.md 写一句用了哪条路。")
    if us:
        out += ("\n\n## 分给你的这几条实拍没人看过图\n" + "\n".join(
            f"- {m.get('name')}（{m.get('at') or '实拍'}，{m.get('id')} 要「{str(m.get('need') or '')[:80]}」）：{m.get('why')}" for m in us)
            + f"\n先看 ctx/look/stock/ 里它的联系表：对题就照分配用；不对题就当没找到，照这几条路选：{ROUTES}。"
              "这几条不受「只准用分给你的」约束；NOTES.md 写一句看图的结论。")
    return out


def _halt():
    """Ctrl-C 以后在跑的线程每一步之前查（停集以后它又渲了 s02）。"""
    if util.HALT.is_set():
        raise KeyboardInterrupt


def make_shot(c, s, kind, topic, sl, issues=None, rnd=None, review_imgs=()):
    _halt()
    sdir = c.p("shots", s["id"])
    work = os.path.join(sdir, "work")
    done_p = os.path.join(sdir, "done.json")
    render = os.path.join(sdir, "render.mp4")
    done = util.read_json(done_p)
    if kind == "shot" and done and done.get("spec_sha") == spec_sha(s) and os.path.exists(render):
        return done
    code = os.path.join(work, "out", "shot.py")
    old = util.read_json(os.path.join(sdir, "spec.json"))
    if kind == "shot" and os.path.exists(code) and old is not None and spec_sha(old) != spec_sha(s):
        dst = os.path.join(sdir, "_old", util.now().strftime("%Y%m%d-%H%M%S"))   # 镜头表改过（--from 1 重做导演）：旧代码挪走，不拿它续跑
        os.makedirs(dst, exist_ok=True)
        shutil.move(os.path.join(work, "out"), os.path.join(dst, "out"))
        man = os.path.join(work, SQ.MAN)                                      # 素材请求清单（轮数、找过的）跟着旧版本走：新 spec 从头算
        if os.path.exists(man):
            os.makedirs(os.path.join(dst, "stock"), exist_ok=True)
            shutil.move(man, os.path.join(dst, "stock", "manifest.json"))
        c.log(f"  {s['id']}：spec 变了，上一版代码挪到 {os.path.relpath(dst, c.ep)}/（不删），从头做", echo=False)
    util.write_json(os.path.join(sdir, "spec.json"), s)
    key = c.p("frames", f"key_{s['section']}.png")
    extra = [(key, "ctx/key.png", "本段代表帧（辅助参考：颜色、质感，不是定稿）")] if os.path.exists(key) else []
    smp, sheet = c.j("sample.json"), c.p("frames", "sample", "sheet.jpg")
    if smp and not smp.get("skipped") and os.path.exists(sheet) and s["id"] not in smp.get("shots", []):
        extra.append((sheet, "ctx/sample.jpg", "5 秒样片联系表（" + ("已过审" if smp.get("pass") else "没全过，只学它做对的地方")
                      + "）：动效密度、每一格都往前走一步照它（别学它的摆动和泵拍）"))
    extra += [(p, f"ctx/review/{os.path.basename(p)}", "审片看到的帧") for p in review_imgs]
    files = prepare_ctx(c, work, s, topic, {"kind": kind, "fn": s["id"], "start_f": s["start_f"], "end_f": s["end_f"]}, extra)
    task, rows = shot_task(c, s, kind, topic, sl, files, issues, rnd)
    err = ""
    last = c.cfg["agent"]["render_retries"]
    prev = cur_motion(c, s["id"]) if kind == "fix" else None      # 返修前那一版的运动量（返修不许改倒退）
    old_eff = (prev or {}).get("eff")
    rnotes = notes_for(c, rnd) if kind == "fix" else None
    if rnotes and (rnotes.get("motion_floor_r0") or {}).get(s["id"]) is not None and s["id"] not in (rnotes.get("redo_fixed") or []):
        old_eff = float(rnotes["motion_floor_r0"][s["id"]])           # 看片意见轮：倒退底线取一稿（cut_r0）的运动量（notes 里写的）；
        #                                                            redo_fixed 的镜头照原规则取上一版（已经按旧约束修过的那一版）
    st = {"sync_sent": 0, "motion_sent": 0, "fx_sent": 0, "table": sync_table(rows), "old_eff": old_eff}
    if kind == "shot" and os.path.exists(code):                 # 续跑：上次已写出代码，先直接渲，不先叫代理
        d, why = render_and_accept(c, s, kind, rnd, work, code, render, done_p, st=st)
        if d:
            return d
        err = ("\n## 接着上一版改\nout/shot.py 是之前写到一半的版本，直接在它上面改。"
               "不要重读已经读过的文件，先跑 ctx/check.sh 看现状。\n" + why)
    for attempt in range(last + 1):
        _halt()
        res = run_agent(c, agent_job(c, kind, s["id"], sdir, task + err, ["out/shot.py"]))
        if res["status"] != "ok" and not (os.path.exists(code) and res["status"] in ("max_turns", "timeout")):
            err = f"\n## 上次没交付（{res['status']}）\n请写出 out/shot.py。\n"
            continue
        if res["status"] != "ok":
            c.log(f"  {s['id']} {kind}：代理 {res['status']}，但 out/shot.py 已写出，先拿它渲染")
            err = ("\n## 接着上一版改\nout/shot.py 是你上一次写到一半的版本，直接在它上面改。"
                   "不要重读已经读过的文件，先跑 ctx/check.sh 看现状。\n")
        d, err = render_and_accept(c, s, kind, rnd, work, code, render, done_p, strict=attempt < last, st=st)
        if d:
            return d
    if os.path.exists(render) and kind == "fix":
        c.log(f"  {s['id']} 返修失败，保留上一版", echo=True)
        sync_work_code(c, s["id"], "返修失败、保留上一版")         # work/out/shot.py 换回出画的那份（下一轮审片扫的、冻结比的都是它）
        return util.read_json(done_p)
    raise util.Stop(5, "shot_failed", f"镜头 {s['id']} 做了 {c.cfg['agent']['render_retries'] + 1} 次都没渲染出合格的视频")


def shot_sync(c, s, render, table):
    """渲完一镜按「本镜卡点」表逐帧量（G.shot_sync，接口 2c）。量不了（没有表、没有引擎 Python、工具出错）返回 None。"""
    py = renderer_py(c)
    if not table or not py or not hasattr(G, "shot_sync"):
        return None
    try:
        import inspect
        kw = {"cfg": sync_cfg(c)} if "cfg" in inspect.signature(G.shot_sync).parameters else {}
        r = G.shot_sync(render, s["start_f"], table, py, **kw)
        why = (getattr(G, "LAST_ERR", None) or {}).get("shot") if r is None else None     # 按线程存的，取一次
        if why:
            c.log(f"  {s['id']}：逐镜卡点没量成（{str(why)[:160]}）", echo=False)
        return r
    except Exception as e:  # noqa: BLE001  量法工具出错不拦镜头：记一笔，整片门禁照样会量
        c.log(f"  {s['id']}：逐镜卡点没量成（{type(e).__name__}: {str(e)[:160]}）", echo=False)
        return None


def shot_motion(c, s, video):
    """渲完一镜马上量有效运动量（G.eff_stats，镜内帧号：groups = {sid: (0, n)}，大卡点余波、屏息、片尾海报
    换成镜内帧号豁免），再从同一份逐帧缓存里找死区（G.eff_deadzones，片内帧号）。量到返回 {measured: True, eff, J, ok, sway_ok, dead, …}；
    量不了（没有引擎 Python、工具出错、帧太少）返回 {measured: False, warn}——只记一笔，不发回。"""
    py = renderer_py(c)
    if not py:
        return {"measured": False, "warn": "没有引擎 Python：这一镜渲完没量有效运动量"}
    bm = c.j("beatmap.json") or {}
    a, b = s["start_f"], s["end_f"]
    n = b - a
    mc = motion_cfg(c)
    ex = motion_exempt(bm, a, b)
    try:
        mo = motion_stats(video, {s["id"]: (0, n)}, mc, ex, py=py, hits=big_frames(bm, a, b),
                          need_by=motion_needs(c.j("shotlist.json"), bm, mc))
    except Exception as e:  # noqa: BLE001
        return {"measured": False, "warn": f"有效运动量没量成：{type(e).__name__}: {str(e)[:160]}"}
    m = mo.get(s["id"]) or {}
    if m.get("eff") is None:
        why = (mo.get("all") or {}).get("eff_error") or (getattr(G, "LAST_ERR", None) or {}).get("eff")   # 工具非零退出 / 超时：原因在 LAST_ERR（本线程）
        return {"measured": False, "warn": "有效运动量没量成：" + str(why or "帧太少 / 全是豁免帧")[:200]}
    dead = G.eff_deadzones(video, py, 0, n, offset=a, exempt=ex) if hasattr(G, "eff_deadzones") else None
    out = {k: m.get(k) for k in ("eff", "J", "J_long", "pingpong", "returns", "content", "rigid", "mean", "still", "n", "ok", "sway_ok",
                                 "sway_why", "need")}
    out.update(measured=True, need_ok=m.get("ok") is not False, dead=None if dead is None else list(dead)[:12])
    if dead is None:
        out["dead_warn"] = str((getattr(G, "LAST_ERR", None) or {}).get("dead") or "死区没量成")[:160]
    e_ = mc.get("eff") or {}
    out["pan"] = bool(m.get("rigid") is not None and m.get("content") is not None and m["rigid"] >= e_.get("pan_rigid", 0.9)
                      and m["content"] < e_.get("pan_content", 0.5))
    if m.get("ok") is False and not dead and hasattr(G, "eff_slowest"):  # 慢但没停：死区抓不到，补「运动量最低的几个 24 帧窗」
        out["slow"] = list(G.eff_slowest(video, py, 0, n, offset=a, exempt=ex) or [])[:3]
    return out


def motion_sendback(s, m, mc, regress=None, slow_takes=()):
    """渲完量出来运动量不够 / 原地摆动 / 静图 + 推拉 / 返修倒退时发回的那一节（门禁线数字不写，只给实测值、死区、量纲标定、原片参照）。
    slow_takes = 代码里低于 60 fps、没写 interp='blend' 又没变速够的 Take（每个新源帧都被量成一次切，运动量只剩零头）。"""
    L = ["## 镜内运动量不够（编排器渲完这一镜马上量的）", f"- {motion_line(s['id'], m)}；要{motion_want(mc)}"]
    if regress:
        L.append(f"- 返修前那一版有效运动量 {regress:g}，这一版 {m['eff']:g}（掉了 {100 * (1 - m['eff'] / max(regress, 1e-6)):.0f} %）：{FIX_REGRESS}")
    L.append(f"- 几乎不动的帧段（片内帧号）：{dead_text(m.get('dead'), slow=m.get('slow'))}")
    L.append(f"- {MOTION_CAL}")
    if slow_takes:
        L.append("- 这些素材低于 60 fps、1× 放进来是重复帧（编排器每个新源帧都量成一次切，运动量只剩零头）："
                 + "；".join(slow_takes) + "。写 Take(..., interp='blend')，或者 speed 提到 60 ÷ 帧率倍以上")
    if m.get("sway_ok") is False:
        L.append(f"- 修法（原地摆动）：{FIX_SWAY}")
    if m.get("pan"):
        L.append(f"- 修法（静图 + 推拉）：{FIX_PAN}")
    if m.get("ok") is False or regress:
        L.append(f"- 修法（没有进展）：{FIX_MOVE}")
    return "\n".join(L)


def slow_takes(c, code):
    """代码里 Take('名字', …) 用的素材低于 60 fps、没写 interp='blend'、speed 也没到 60 ÷ 帧率倍的（引擎默认最近帧取样：1× 放进 60 fps
    的片子是重复帧，effmotion 把每个新源帧当成切点扣掉；11-04 审查实测 24 fps 素材 1× 量出 0.2、blend 1.95）。返回一句一条。"""
    try:
        tree = ast.parse(util.read(code)) if os.path.exists(code) else None
    except SyntaxError:
        return []
    if tree is None:
        return []
    fps = {x["name"]: x.get("fps") for x in (c.j("inputs/footage.json", {"clips": []}) or {}).get("clips", [])}
    out = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "Take" and n.args
                and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
            continue
        nm, kw = n.args[0].value, {k.arg: k.value for k in n.keywords if k.arg}
        f = fps.get(nm)
        if not f or float(f) >= 59:
            continue
        it = kw.get("interp")
        if isinstance(it, ast.Constant) and it.value == "blend":
            continue
        sp = kw.get("speed")
        v = sp.value if isinstance(sp, ast.Constant) and isinstance(sp.value, (int, float)) else (1.0 if sp is None else None)
        if v is None or abs(v) * float(f) >= 59:
            continue
        out.append(f"{nm}（{float(f):g} fps，speed {v:g}）")
    return list(dict.fromkeys(out))


def fx_scan(c, s, code):
    """劣质特效静态扫描（lib/cheapfx.py）：(结果, 发回的行, 只提醒的行)。读不了代码 / 语法错时两样都空（语法错交给渲染去报）。"""
    clips = (c.j("inputs/footage.json", {"clips": []}) or {}).get("clips", [])
    old = CF.engine_is_old(c.renderer.root) if getattr(c, "renderer", None) is not None else False
    res = CF.scan_file(code, clips, s, (c.j("topic.json") or {}).get("motif"), old)
    if res.get("error"):
        return res, [], []
    send, warn = CF.rows(res)
    return res, send, warn


def _overlap(a, b):
    """两个 (x, y, w, h) 框的交集占小框的比例。"""
    ix = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    return ix * iy / max(1, min(a[2] * a[3], b[2] * b[3]))


def fx_crops(c, cut, todo, mids, fdir):
    """审片每镜 1–2 张 100 % 裁图（640×360，原尺寸不缩放）：位置先取劣质特效扫描命中调用里写死的坐标
    （SR.particles 的 c=、矢量图元的起点），拿不到就用像素定位器（cheapfx.locate：细线 / 短划线连通域最密的窗口，引擎 Python 跑）。
    返回 {镜头号: [(标签, 图)]}；抽帧 / 定位失败不拦审片。文件名 zoom_<id>_fx*，返修时跟着进 ctx/review/。"""
    ffl = c.p("logs", "ffmpeg.log")
    plan, need = {}, []
    for s in todo:
        sid, m = s["id"], mids[s["id"]]
        res, _, _ = fx_scan(c, s, c.p("shots", sid, "work", "out", "shot.py"))
        boxes = []
        for x in (res.get("coords") or [])[:2]:
            F = m
            if x.get("f0") is not None and s["start_f"] <= x["f0"] + 6 < s["end_f"]:
                F = int(x["f0"] + 6)
            boxes.append((tuple(CF.crop_box(x["x"], x["y"])), F, f"代码第 {x['line']} 行（{x['rule']}）写的位置"))
        plan[sid] = boxes
        if len(boxes) < 2:
            need.append(os.path.join(fdir, f"mid_f{m:05d}.jpg"))
    py = renderer_py(c)
    loc = CF.locate_sub(py, [p for p in need if os.path.exists(p)]) if (need and py) else {}
    out = {}
    for s in todo:
        sid, m = s["id"], mids[s["id"]]
        boxes = plan[sid]
        lb = loc.get(os.path.join(fdir, f"mid_f{m:05d}.jpg"))
        if lb and not any(_overlap(tuple(lb), b[0]) > 0.5 for b in boxes):
            boxes.append((tuple(lb), m, "像素定位器：细线 / 短划线最密的地方"))
        if not boxes:
            boxes = [((640, 360, 640, 360), m, "画面正中")]
        imgs = []
        for k, (b, F, why) in enumerate(boxes[:2]):
            try:
                p = media.frames_jpg(cut, [F], fdir, f"zoom_{sid}_fx{k}", width=b[2], crop=b, log_path=ffl)[0]
                imgs.append((f"{sid} 100 % 裁图（第 {F} 帧，x {b[0]} y {b[1]}，{b[2]}×{b[3]}，原尺寸；{why}）："
                             "看这里有没有 2D 画的东西冒充物质（短划线雪、扁平碎片、细线裂缝、复制拼贴）", p))
            except Exception as e:  # noqa: BLE001
                c.log(f"  {sid}：100 % 裁图没抽成（{str(e)[:120]}）", echo=False)
        out[sid] = imgs
    return out


def fx_review_text(c, todo):
    """审片提示词里每镜一行：编排器查代码查到的劣质特效（只给位置。"""
    rows = []
    for s in todo:
        sid = s["id"]
        res, send, warn = fx_scan(c, s, c.p("shots", sid, "work", "out", "shot.py"))
        xy = {x["line"]: x for x in res.get("coords") or []}          # 代码里写死的位置（审片照坐标去图里找）
        at = lambda ln: (f"（画面 x {xy[ln]['x']:g} y {xy[ln]['y']:g}" + (f"，第 {xy[ln]['f0']} 帧起" if xy[ln].get("f0") is not None else "")  # noqa: E731
                         + "）") if ln in xy else ""
        bits = [f"{CF.RULE.get(x['rule'], x['rule'])} 第 {x['line']} 行：{x['what']}{at(x['line'])}" for x in send[:4]]
        bits += [f"{CF.RULE.get(x['rule'], x['rule'])}（提醒）第 {x['line']} 行：{x['what']}{at(x['line'])}" for x in warn[:2]]
        if bits:
            rows.append(f"- [查代码] {sid} 劣质特效：" + "；".join(bits) + "（只给位置，图里核对；判不判照下面「劣质特效」7 问）")
    return ("\n" + "\n".join(rows)) if rows else ""


def render_and_accept(c, s, kind, rnd, work, code, render, done_p, strict=True, st=None):
    """合格返回 (done, "")，不合格返回 (None, 发回给代理的说明)。
    strict = 必做动作没做到就发回（不渲染）；最后一次机会 strict=False：照样渲、收下，没做到的记进 done.json 和报告。
    st = make_shot 这一次的状态 {sync_sent, motion_sent, fx_sent, table, old_eff}：卡点、运动量（含返修倒退）、
    劣质特效只发回、不当门禁。每一步之前查 util.HALT（Ctrl-C）。"""
    st = st if st is not None else {"sync_sent": 0, "table": []}
    _halt()
    miss = must_do_misses(c, s, work, code)
    fxr, fx_send, fx_warn = fx_scan(c, s, code)
    fx_now = bool(fx_send) and strict and st.get("fx_sent", 0) < 1
    if (miss and strict) or fx_now:
        txt = []
        if miss and strict:
            txt.append("## 必做动作没做到（编排器查代码查出来的，改了再交）\n" + "\n".join("- " + m for m in miss))
        if fx_now:
            st["fx_sent"] = st.get("fx_sent", 0) + 1
            txt.append(CF.sendback_text(fx_send, fx_warn).rstrip())
        c.log(f"  {s['id']} {kind}：" + "、".join(([f"必做动作没做到 {miss[:2]}"] if miss and strict else [])
                                                   + ([f"劣质特效 {len(fx_send)} 处（{'、'.join(sorted({r['rule'] for r in fx_send}))}）"] if fx_now else []))
              + "，发回（不渲染）", echo=False)
        seen = os.path.isdir(os.path.join(work, LOOK_SELF)) and os.listdir(os.path.join(work, LOOK_SELF))
        return None, ("\n" + "\n\n".join(txt) + ("\n（这一版没渲；上一次渲出来的画面在 ctx/look/self/）" if seen else "")
                      + "\n其余保持不变；改完跑 ctx/check.sh。\n")
    with RENDER_LOCK:
        _halt()
        tmp = render + ".new.mp4"
        t0 = util.now()
        rr = c.renderer.render_shot(s, code, tmp)
    if rr.get("interrupted"):
        raise KeyboardInterrupt
    _halt()
    pr = media.probe(tmp) if rr.get("ok") else {}
    want = s["end_f"] - s["start_f"]
    got = pr.get("nb_frames")
    if rr.get("ok") and pr.get("ok") and got == want and (pr["width"], pr["height"]) == tuple(c.g["size"]):
        sy = shot_sync(c, s, tmp, st.get("table"))
        _halt()
        mo = shot_motion(c, s, tmp)
        _halt()
        mc = motion_cfg(c)
        hard_sy = [x for x in (sy or {}).get("misses") or [] if sync_hard(x)]
        look = look_self(c, s, work, tmp, prio=[x["F"] for x in hard_sy if x.get("F") is not None])   # 渲完马上给代理看
        old = st.get("old_eff")
        regress = (old if kind == "fix" and mo.get("measured") and old and mo["eff"] < 0.8 * old and mo["eff"] < (mo.get("need") or mc["need_eff"])
                   else None)
        move_bad = bool(mo.get("measured")) and (mo.get("ok") is False or mo.get("sway_ok") is False or bool(mo.get("pan")) or bool(regress))
        sync_bad = bool(hard_sy)           # 只有「计划的拍上没有画面事件」才发回；倍数不够、表外强变化只记进 done.json
        parts, why = [], []
        if sync_bad and strict and st.get("sync_sent", 0) < 1:
            st["sync_sent"] = st.get("sync_sent", 0) + 1
            heavy = [r["F"] for r in st.get("table") or [] if r["lvl"] == "重"]
            src = util.read(code) if os.path.exists(code) else ""
            have = set() if any(k in src for k in ("SR.slots", "SR.seg", "SR.music_map", "SR.accent_frames")) else int_consts(src)
            lost = [F for F in heavy if F not in have and F - 1 not in have]
            parts.append("\n".join(["## 卡点没做到（编排器渲完逐帧量的：计划的拍上没有画面事件）"]   # 只发判不过的；倍数不够、silent 只进 done.json
                                   + [f"- {x.get('msg') or '第 %s 帧 %s' % (x.get('F'), x.get('kind'))}" for x in hard_sy])
                         + (f"\n- 代码里没写这些「重」帧的整数常量：{', '.join(map(str, lost))}（照「本镜卡点」表写死，用 T.F >= F 切换）" if lost and have else ""))
            why.append(f"卡点 {len(hard_sy)} 处拍上没有画面事件")
        if move_bad and strict and st.get("motion_sent", 0) < 1:
            st["motion_sent"] = st.get("motion_sent", 0) + 1
            parts.append(motion_sendback(s, mo, mc, regress, slow_takes(c, code)))
            why.append(f"有效运动量 {mo['eff']:g}" + ("（返修倒退）" if regress else "") + ("（原地摆动）" if mo.get("sway_ok") is False else "")
                       + ("（静图 + 推拉）" if mo.get("pan") else ""))
        if parts:
            c.log(f"  {s['id']} {kind}：渲完量出来{'、'.join(why)}，合成一份问题单发回", echo=False)
            parts.append(look_text(look, [x["F"] for x in hard_sy if x.get("F") is not None]).rstrip())
            return None, "\n" + "\n\n".join(x for x in parts if x) + "\n其余保持不变；改完跑 ctx/check.sh。\n"
        os.replace(tmp, render)
        for ext in (".sync.json", ".eff.json", ".eff.npz"):
            if os.path.exists(tmp + ext):
                os.replace(tmp + ext, render + ext)
        d = {"id": s["id"], "status": "ok", "kind": kind, "round": rnd, "spec_sha": spec_sha(s),
             "code_sha": util.sha256_file(code), "render_sha": util.sha256_file(render), "frames": got, "at": util.iso()}
        if miss:
            d["must_do_misses"] = miss
            d["must_do_rules"] = sorted({r for r, _ in must_do_tagged(c, s, work, code)})
        if sy is not None:
            d["sync"] = {"ok": bool(sy.get("ok")), "misses": len(sy.get("misses") or []), "silent": len(sy.get("silent") or []),
                         "hard": len(hard_sy), "sent_back": st.get("sync_sent", 0)}
            if not sy.get("ok"):
                d["sync_misses"] = [x.get("msg") or str(x) for x in (sy.get("misses") or []) + (sy.get("silent") or [])][:20]
        d["motion"] = dict(mo, sent_back=st.get("motion_sent", 0), **({"old_eff": old} if kind == "fix" and old is not None else {}),
                           **({"regress": True} if regress else {}))
        if not mo.get("measured"):
            c.log(f"  {s['id']} {kind}：注意 {mo.get('warn')}", echo=False)
        d["cheapfx"] = CF.summary(fxr, fx_send, fx_warn, st.get("fx_sent", 0))
        d["look"] = [r for r, _ in look]
        mt = missing_tools(work)
        if mt:
            d["missing_tools"] = mt                              # 代理在 NOTES.md 写的「缺的工具」，report.md 按集汇总
        util.write_json(done_p, d)
        shutil.copy2(code, os.path.join(os.path.dirname(done_p), f"shot_{kind}{'' if rnd is None else rnd}.py"))
        c.log(f"  {s['id']} {kind}：渲染 {got} 帧，{(util.now() - t0).total_seconds():.0f}s"
              + (f"；有效运动量 {mo['eff']:g}" + (f"（没过：{'原地摆动' if mo.get('sway_ok') is False else ('静图 + 推拉' if mo.get('pan') and mo.get('ok') is not False else '没有进展')}"
                                                  f"{'、返修倒退' if regress else ''}，照样收下，整片审片会再判）" if move_bad else "")
                 if mo.get("measured") else "；有效运动量没量")
              + (f"；必做动作还有 {len(miss)} 条没做到，照样收下、记进报告" if miss else "")
              + (f"；卡点还有 {len(d['sync_misses'])} 处没做到，照样收下（整片审片会再判）" if d.get("sync_misses") else "")
              + (f"；劣质特效还有 {len(fx_send)} 处（{'、'.join(sorted({r['rule'] for r in fx_send}))}，发回过 {st.get('fx_sent', 0)} 次），"
                 "照样收下、审片看图再判" if fx_send else ""))
        return d, ""
    why = rr.get("log_tail") or f"帧数 {got}，应为 {want}；尺寸 {pr.get('width')}×{pr.get('height')}"
    return None, "\n## 上次渲染不合格（改 out/shot.py）\n```\n" + why[-1500:] + "\n```\n"


def assemble(c, r):
    sl = c.j("shotlist.json")
    out = c.p("film", f"cut_r{r}.mp4")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    media.concat([c.p("shots", s["id"], "render.mp4") for s in sl["shots"]], out, log_path=c.p("logs", "ffmpeg.log"))
    md5 = media.framemd5(out)
    util.write_json(c.p("film", f"cut_r{r}.md5.json"), md5)
    return out, md5


def signature_hex(c):
    """开题定的签名色（整屏染色报警用；没有就让门禁取全片最多的色相）。"""
    return ((c.j("topic.json") or {}).get("signature_color") or {}).get("hex")


def music_for_gates(c):
    """门禁量卡点用的配乐：{file, start_s, map}（music_map 是这一集的那份，短片模式是截断过的）；没有配乐返回 None。
    没有 music_map 时 map = None：G.sync_stats 按节拍表的 BPM 兜底量（标签只有小节.拍.分，没有档位）。"""
    mu = c.j("inputs/music.json") or {}
    if not mu.get("file"):
        return None
    f = mu["file"] if os.path.isabs(mu["file"]) else c.p(mu["file"])
    if not os.path.exists(f):
        return None
    mp = mu.get("music_map")
    mp = (mp if os.path.isabs(mp) else c.p(mp)) if mp else None
    return {"file": f, "start_s": float(mu.get("start_s", mu.get("downbeat_s", 0.0))), "map": mp if mp and os.path.exists(mp) else None}


def sync_cfg(c):
    """门禁实测卡点的参数 = 系列 gates.sync + 这一集配乐打击点表自己的 DROP 门槛。打击点表是按更低的门槛建的（表里 thr.drop_db；
    有的曲子 DROP 宽频只跳 8 dB 左右，按 8 dB 建表），门禁现场量 DROP 也用同一个门槛——不然 sync_grid 量不到 DROP、
    半拍 / 整拍错位查不出（只记 warn）。表里没写 thr 但 drop.jump_db 比门槛低的，按 jump_db − 1.5（不低于 6 dB）。
    别的曲子（DROP ≥ 10 dB）不变。"""
    g = dict(c.g.get("sync") or {})
    mu = music_for_gates(c) or {}
    mp = util.read_json(mu["map"]) if mu.get("map") else None
    if not mp:
        return g
    base = float(g.get("drop_db", 10.0))
    thr = (mp.get("thr") or {}).get("drop_db")
    jd = (mp.get("drop") or {}).get("jump_db")
    want = float(thr) if thr is not None else (max(6.0, float(jd) - 1.5) if jd is not None and float(jd) < base else None)
    if want is not None and want < base:
        g["drop_db"] = round(want, 1)
    return g


def video_checks(c, video, bm, sl, final=False):
    """G.video_checks 加上引擎 Python 和配乐（有效运动量、实测卡点要用；gates 还是旧签名时不传）。实测卡点的 DROP 门槛按这一集
    配乐的打击点表（sync_cfg）。"""
    import inspect
    kw = {"signature": signature_hex(c)}
    ps = inspect.signature(G.video_checks).parameters
    if "py" in ps:
        kw["py"] = renderer_py(c)
    if "music" in ps:
        kw["music"] = music_for_gates(c)
    g = dict(c.g, sync=sync_cfg(c))
    return G.video_checks(video, g, bm, sl, final, **kw)


def run_gates(c, video, name, final=False):
    checks, m = video_checks(c, video, c.j("beatmap.json"), c.j("shotlist.json"), final)
    util.write_json(c.p(name), {"video": os.path.relpath(video, c.ep), "metrics": m, "summary": G.summarize(checks), "checks": checks})
    sy = {x["id"]: x for x in checks if str(x.get("id", "")).startswith(("sync_", "motion_eff", "motion_sway"))}
    c.log(f"门禁 {name}：{G.fmt(G.summarize(checks))}，切点 {m.get('cuts_per_s')}/s，重击 {m.get('hits_per_s')}/s"
          + ("；" + "，".join(f"{k} {v['status']}" for k, v in sy.items()) if sy else ""))
    return checks, m


def round_preview(c, r, cut, checks, m):
    """每一版拼片自动出三件（以前预览是手工出的，一集十几次）：带配乐的预览 film/preview_r{r}.mp4（声音照 A 版：
    原曲从 start_s 起直接混、响度标准化，不另加音效；片尾一击还没合进来，先不加）、联系表 frames/preview_r{r}.jpg（全片均匀 12 帧）、
    数 film/preview_r{r}.json（门禁摘要、没过的门禁、切点每秒、每镜有效运动量、卡点；审片打完分 review_round 再补内部评分和没过的镜头）。
    出错只记日志，不拦。"""
    ffl, out = c.p("logs", "ffmpeg.log"), c.p("film", f"preview_r{r}.mp4")
    sheet, nums = c.p("frames", f"preview_r{r}.jpg"), c.p("film", f"preview_r{r}.json")
    sound = "无声（这一集没有配乐）"
    try:
        mu = c.j("inputs/music.json") or {}
        path = (mu["file"] if os.path.isabs(mu["file"]) else c.p(mu["file"])) if mu.get("file") else None
        if path and os.path.exists(path):
            media.mux(cut, path, out, audio_start=float(mu.get("start_s", mu.get("downbeat_s", 0.0))), log_path=ffl, loud=media.LOUD)
            sound = f"A 版：原曲「{mu.get('title') or os.path.basename(path)}」直接混，响度 {media.LOUD['I']:g} LUFS，不加音效"
        else:
            shutil.copy2(cut, out)
    except (RuntimeError, OSError, KeyError, ValueError) as e:
        c.log(f"  预览第 {r} 版没混上配乐（{str(e)[:120]}），先给无声的", echo=False)
        shutil.copy2(cut, out)
        sound = "无声（混配乐出错）"
    try:
        n = (media.probe(cut).get("nb_frames") or (c.j("shotlist.json") or {}).get("total_f") or 0)
        media.sheet(cut, [int((i + 0.5) * n / 12) for i in range(12)], sheet, cols=4, cell_w=480, max_n=12, f0=0, log_path=ffl)
    except (RuntimeError, OSError) as e:
        c.log(f"  预览第 {r} 版联系表没抽成（{str(e)[:120]}）", echo=False)
        sheet = None
    eff = ((m.get("motion") or {}).get("eff") or {}).get("per_shot") or {}
    util.write_json(nums, {"round": r, "cut": os.path.relpath(cut, c.ep), "video": os.path.relpath(out, c.ep), "sound": sound,
                           "sheet": os.path.relpath(sheet, c.ep) if sheet else None, "gates": G.summarize(checks),
                           "not_ok": [f"{x['name']}：{x['value']}" for x in checks if x["status"] in ("warn", "fail")][:16],
                           "cuts_per_s": m.get("cuts_per_s"), "eff": {k: (v or {}).get("eff") for k, v in eff.items()},
                           "sync": {k: (m.get("sync") or {}).get(k) for k in ("top16_on", "top16_c3", "top16_n", "select", "silent")
                                    if k in (m.get("sync") or {})}, "at": util.iso()})
    c.log(f"预览第 {r} 版：{os.path.relpath(out, c.ep)}（{sound.split('：')[0]}）、{os.path.relpath(sheet, c.ep) if sheet else '没有联系表'}、"
          f"{os.path.relpath(nums, c.ep)}（门禁 {G.fmt(G.summarize(checks))}）")


def s3_production(c):
    topic, bm, sl = c.j("topic.json"), c.j("beatmap.json"), c.j("shotlist.json")
    inputs.ensure(c, topic)                                     # 期间下载了新素材：接上（旧名字不变）
    stock_inventory(c, topic, sl)                               # 开拍前素材盘点（第 2 环节做过、镜头表没变就跳过）
    c.renderer.setup(bm, sl)
    bake_elements(c)                                            # 实拍叠层（SR.element）预烘：并行镜头不各自临时烘
    par_map(c, lambda s: make_shot(c, s, "shot", topic, sl), sl["shots"])
    c.log(f"制作：{len(sl['shots'])} 镜全部渲染")
    cut, _ = assemble(c, 0)
    checks, gm = run_gates(c, cut, "gates_post_production.json")
    round_preview(c, 0, cut, checks, gm)
    tree_save(c, "制作", code_rows(c))
    led = tree_save(c, "成片", TR.video_rows(c.tree, c.cat(), checks, "成片（制作后）"))
    c.log(f"规则树（制作后）：{TR.summary_line(led)[:200]}")
    hard = [x for x in checks if x["status"] == "fail"]
    if hard:
        raise util.Stop(5, "hard_gate", f"制作后硬门禁不过：{[x['name'] for x in hard]}（见 gates_post_production.json）")


# ============================================================================ 4 审片 + 5 修复
# 审片看到的门禁行里不写门槛的检查（11-04 防 Goodhart：审片把「拉过 4.33」「凑到 3.8/s」写进修法，返修就只会凑数）：只给实测值和位置
NO_WANT = ("motion_eff", "motion_sway", "motion_shot", "motion_content", "cut_density", "first_second", "hits")


def review_llm(c):
    """审片用的模型（审片固定成一个，不跟着制作后端走——以前 CX 片由 CX 自己审、DS 片由 DS 审，分数「CX 6.4 > DS 6.3」和用户
    的判断相反）。config.json 的 review.model：没写 / "deepseek" = DeepSeek（api.model）；"deepseek-…" = 换一个 DeepSeek 模型；
    "codex" / "codex:<模型>" / "gpt-…" = Codex。和制作后端一样就直接用 c.llm；不一样另起一个（mock 照样用假回复），
    记账：DeepSeek 审 Codex 片记在 cost_review.json（Codex 那份账不按美元计）。每集只建一次。"""
    if getattr(c, "_review_llm", None) is not None:
        return c._review_llm
    want = str(((c.cfg.get("review") or {}).get("model")) or "deepseek").strip()
    w = want.lower()
    cx_want = w.startswith(("codex", "gpt"))
    cur_cx = CX.backend(c) == "codex"
    mock_fn = getattr(c.llm, "mock_fn", None)
    from . import cost, llm
    if cx_want:
        model = want.split(":", 1)[1].strip() if ":" in want else None
        if cur_cx and (not model or model == (getattr(c, "cc", None) or {}).get("model")):
            c._review_llm = c.llm
        else:
            cc = dict(getattr(c, "cc", None) or CX.load_cfg(), **({"model": model} if model else {}))
            c._review_llm = CX.CodexLLM(c.cfg, cc, CX.Ledger(c.p("cost_review.json")), c.ep, c.log, mock_fn=mock_fn)
    else:
        model = c.cfg["api"]["model"] if w == "deepseek" else want
        if not cur_cx and model == ((getattr(c.llm, "c", None) or {}).get("model") or c.cfg["api"]["model"]):
            c._review_llm = c.llm
        else:
            cfg = dict(c.cfg, api=dict(c.cfg["api"], model=model))
            led = c.ledger if not cur_cx else cost.Ledger(c.p("cost_review.json"), c.cfg["pricing"], None)
            c._review_llm = llm.LLM(cfg, led, c.ep, c.log, mock_fn=mock_fn)
    if c._review_llm is not c.llm:
        c.log(f"审片模型：{want}（config review.model，制作后端 {CX.backend(c)}；审片费用记在 cost_review.json）", echo=False)
    return c._review_llm


def prompt_with(c, name, extra, **v):
    """c.prompt 再加几节可选的输入：extra = {变量: (小标题, 文字)}；模板里写了 {{变量}} 就填在那里，没写就接在提示词末尾
    （提示词组还没改模板时也送得到）。"""
    tpl = util.read(c.find("prompts", name, ".md"))
    out = c.prompt(name, **v, **{k: (t or "（无）") for k, (_, t) in extra.items()})
    return out + "".join(f"\n\n## {h}\n{t}" for k, (h, t) in extra.items() if t and "{{" + k + "}}" not in tpl)


def review_inputs(c, r, todo):
    """审片补的两样输入（劣质特效坐标在 fx_review_text 里）：每镜来源表（制作代理的 out/NOTES.md：每个特效的素材号 / 叠层种类）、
    这一镜前几轮的审片意见（以前 history 是空的，审片前后矛盾、把刚判廉价的零件开成药方）。"""
    notes, hist = [], []
    for s in todo:
        sid = s["id"]
        t = _read_or(c.p("shots", sid, "work", "out", "NOTES.md")).strip()
        if t:
            notes.append(f"### {sid}\n" + (t if len(t) <= 1500 else "…" + t[-1500:]))
        rows = []
        for k in range(1, r):
            v = ((c.j(f"review_{k}.json") or {}).get("shots") or {}).get(sid)
            if not v:
                continue
            iss = "；".join(f"[{i.get('rule', '')}] {str(i.get('where', ''))[:90]} → {str(i.get('fix', ''))[:90]}" for i in (v.get("issues") or [])[:4])
            rows.append(f"- 第 {k} 轮：{'过' if v.get('pass') else '不过'}（{v.get('score', '?')} 分）" + (f"：{iss}" if iss else ""))
        if rows:
            hist.append(f"### {sid}\n" + "\n".join(rows))
    return {"NOTES": ("每镜来源表（制作代理自己写的 out/NOTES.md：每个特效从哪来——素材号 / 叠层种类；图里看到的东西和这里对不上，"
                      "就按图判）", "\n\n".join(notes)),
            "HISTORY": ("这几镜前几轮的审片意见（这一轮的意见别和前面矛盾；前面开过的药方这一轮判了廉价的，换一个来源，别再开同一件）",
                        "\n\n".join(hist))}


def gates_text(gfile, ids=None):
    if not gfile or not os.path.exists(gfile):
        return "（无）"
    d = util.read_json(gfile)
    lines = []
    for x in d["checks"]:
        if x["status"] == "ok" or x.get("off"):
            continue
        where = x.get("where") or []
        if ids is not None:
            where = [w for w in where if any(i in w for i in ids)]
            if not where and x["id"] not in ("cut_density", "hits", "first_second"):
                continue
        want = "" if x["id"] in NO_WANT or str(x["id"]).startswith("sync_") else f"（要求 {x['want']}）"
        lines.append(f"- [{x['status']}] {x['name']}：{x['value']}{want}" + (f"，位置：{'；'.join(where[:6])}" if where else ""))
    return "\n".join(lines) or "（都通过）"


def hoist_cuts(o):
    """审片回复里写进镜头对象的 cuts 挪到顶层（往期 10 次「少写收尾 ]}」全是这一个样子：cuts 写在唯一那个镜头里面，
    收尾却按顶层写；util.parse_json 补上括号以后切点意见还在镜头里，不挪就丢了）。"""
    for x in o.get("shots") or []:
        v = x.pop("cuts", None) if isinstance(x, dict) else None
        if isinstance(v, list):
            o["cuts"] = (o.get("cuts") or []) + [y for y in v if isinstance(y, dict)]
    return o


def review_round(c, r, cut, gfile):
    topic, sl, bm = c.j("topic.json"), c.j("shotlist.json"), c.j("beatmap.json")
    frozen = c.j("freeze.json", {})
    fdir = c.p("frames", f"r{r}")
    ffl = c.p("logs", "ffmpeg.log")
    n = sl["total_f"]
    result = {"round": r, "cut": os.path.relpath(cut, c.ep), "frozen_before": sorted(frozen), "shots": {}, "global": None, "cuts": []}
    order = [s["id"] for s in sl["shots"]]
    live = {s["section"] for s in sl["shots"] if s["id"] not in frozen}       # 还有镜头要评的段落
    mc = motion_cfg(c)
    mo = motion_by_shot(c, cut, sl, bm)                                        # 镜内运动量（机器量，给审片参考）
    result["motion"] = mo
    sheets = []
    for sec in bm["sections"]:
        shots = [s for s in sl["shots"] if s["section"] == sec["id"]]
        if not shots:
            continue
        a, b = shots[0]["start_f"], shots[-1]["end_f"]
        sh = media.sheet(cut, [int(a + (i + 0.5) * (b - a) / 9) for i in range(9)], os.path.join(fdir, f"sheet_{sec['id']}.jpg"), log_path=ffl)
        sheets.append((f"{sec['id']} {sec['name']} 联系表", sh))
        todo = [s for s in shots if s["id"] not in frozen]
        if not todo:
            continue
        imgs = [(f"{sec['id']} 联系表（第 {a}–{b} 帧）", sh)]
        mids = {s["id"]: (s["start_f"] + s["end_f"]) // 2 for s in todo}
        media.frames_jpg(cut, list(mids.values()), fdir, "mid", log_path=ffl)
        crops = fx_crops(c, cut, todo, mids, fdir)                               # 每镜 1–2 张 100 % 裁图（劣质特效审片）
        for s in todo:
            m = mids[s["id"]]
            fm = os.path.join(fdir, f"mid_f{m:05d}.jpg")
            z = media.frames_jpg(cut, [m], fdir, f"zoom_{s['id']}", crop=tuple(s.get("zoom") or (480, 270, 960, 540)), log_path=ffl)[0]
            imgs += [(f"{s['id']} 中间帧（第 {m} 帧，{m / 60:.2f}s）", fm), (f"{s['id']} 局部放大", z),
                     (f"{s['id']} 切线图（第 {s['start_f']}–{s['end_f']} 帧；正中一条横线随时间往下叠：斜纹 / 展开 = 推进，竖直线 = 静止，"
                      f"锯齿 / 波浪 = 原地来回摆）",
                      media.kymo(cut, s["start_f"], s["end_f"], os.path.join(fdir, f"kymo_{s['id']}.jpg"), log_path=ffl))]
            imgs += crops.get(s["id"]) or []
        # 切点核对：每个镜头之间的切点给 F-2、F、F+2 三帧并排 + 声明（只作建议）
        cut_lines, cuts = [], {}
        j = order.index(shots[-1]["id"]) + 1
        nxt = [sl["shots"][j]] if j < len(order) and sl["shots"][j]["section"] not in live else []   # 下一段整段冻结：它的入点在这一段核对
        for s in shots + nxt:
            i = order.index(s["id"])
            if i == 0 or (s["id"] in frozen and order[i - 1] in frozen):
                continue
            F, prev = s["start_f"], order[i - 1]
            tri = media.sheet(cut, [F - 2, F, F + 2], os.path.join(fdir, f"cut_{s['id']}.jpg"), cols=3, log_path=ffl)
            imgs.append((f"{prev} → {s['id']} 切点：左 F-2（第 {F - 2} 帧）、中 F（第 {F} 帧 = {s['id']} 第一帧）、右 F+2", tri))
            cut_lines.append(f"- 图 {len(imgs)}：{prev} → {s['id']}（第 {F} 帧）：{tx_text(s.get('transition_in'))}")
            cuts[s["id"]] = (prev, F)
        lines = "\n".join(f"- {s['id']}：第 {s['start_f']}–{s['end_f']} 帧（{s['start_f'] / 60:.2f}–{s['end_f'] / 60:.2f}s），{s['look']}；"
                          f"{shot_brief(s)}" for s in shots)
        fz = [s["id"] for s in shots if s["id"] in frozen]
        prompt = prompt_with(c, "04_review", review_inputs(c, r, todo), TITLE=topic["title_cn"], ROUND=str(r), SECTION=sec["id"], SHOTS=lines,
                             GATES=gates_text(gfile, [s["id"] for s in shots]) + fx_review_text(c, todo),
                             MOTION="\n".join(motion_line(s["id"], mo.get(s["id"])) for s in shots)
                             + f"\n（要{motion_want(mc)}；{motion_ref(mc)}）",
                             CUTS="\n".join(cut_lines) or "（这一段没有要核对的切点，cuts 给空列表）",
                             FROZEN_NOTE=(f"已冻结、不用评：{'、'.join(fz)}。" if fz else ""))
        ids = sorted(s["id"] for s in todo)
        o, _ = review_llm(c).ask_json("review", f"review_r{r}_{sec['id']}", c.system, prompt, imgs, c.schema("review"),
                              check=lambda o, ids=ids: [] if sorted(x["id"] for x in o["shots"]) == ids else [f"shots 要正好是 {ids}"],
                              ctx={"shots": ids, "round": r, "cuts": list(cuts)})
        for x in hoist_cuts(o)["shots"]:
            result["shots"][x["id"]] = x
        for x in o.get("cuts") or []:                               # 不 ok 的进问题单（作为建议，不改 pass）
            if x.get("cut") not in cuts:
                continue
            prev, F = cuts[x["cut"]]
            result["cuts"].append(dict(x, prev=prev, F=F))
            own = x["cut"] if x["cut"] not in frozen else prev
            if not x.get("ok") and own in result["shots"]:
                result["shots"][own]["issues"].append({"rule": "切点核对（建议）", "where": f"第 {F} 帧切点（{prev} → {x['cut']}）：{x.get('seen', '')}",
                                                       "fix": x.get("fix") or "照声明把转场做出来"})
    # 全片
    first = media.sheet(cut, list(range(0, 54, 6)), os.path.join(fdir, "first_second.jpg"), log_path=ffl)
    loop = media.sheet(cut, [0, n - 1], os.path.join(fdir, "loop.jpg"), cols=2, log_path=ffl)
    imgs = [("第 1 秒，每 6 帧一张", first), ("第 0 帧（左）和最后一帧（右）", loop)] + sheets
    votes = []
    nv = max(1, int((c.cfg.get("rounds") or {}).get("review_votes", 3)))
    for k in range(nv):                                              # 3 次独立审片，取中位数
        prompt = c.prompt("04_review_global", TITLE=topic["title_cn"], ROUND=str(r), NS=str(len(imgs)), GATES=gates_text(gfile),
                          LINE=str((c.series.get("review") or {}).get("deliver_at", 6)), ANCHOR_NOTE="按系列卡打分。",
                          MOTION=motion_line("全片", mo["all"]) + f"（{motion_ref(motion_cfg(c))}）")
        o_, _ = review_llm(c).ask_json("review", f"review_global_r{r}_v{k + 1}", c.system, prompt, imgs, c.schema("review_global"),
                               ctx={"round": r, "vote": k + 1})
        votes.append(o_)
    scores = [v["anchor_score"] for v in votes]
    med = statistics.median(scores)
    og = dict(min(votes, key=lambda v: abs(v["anchor_score"] - med)))  # 意见用分数最接近中位数的那一次
    og.update(anchor_score=med, anchor_votes=scores)
    result["global"] = og
    for it in og["issues"]:
        sid = it.get("shot") or ""
        if sid in result["shots"] and sid not in frozen:
            result["shots"][sid]["pass"] = False
            result["shots"][sid]["issues"].append({"rule": "全片 · " + it["rule"], "where": it["where"], "fix": it["fix"]})
    for sid, v in result["shots"].items():                          # 审片意见旁边附这一镜现在的有效运动量
        e = (mo.get(sid) or {}).get("eff")
        if e is None:
            continue
        v["eff"] = e
        for it in v.get("issues") or []:
            if "有效运动量" not in str(it.get("where") or ""):
                it["where"] = f"{it.get('where', '')}（这一镜现在有效运动量 {e:g}，改完不许掉）"
    result["motion_failed"], result["motion_frozen_bad"] = motion_force(result["shots"], mo, mc, frozen, soft=set(c.g.get("soft") or {}))
    sy = util.read_json(cut + ".sync.json")                        # 整片实测卡点（run_gates 时 G.sync_stats 写在 cut 旁边）
    result["sync"] = (sy or {}).get("stats")
    result["sync_failed"], result["sync_frozen_bad"] = sync_force(result["shots"], sy, frozen, soft=set(c.g.get("soft") or {}))
    gk = ((util.read_json(gfile) or {}).get("checks") or []) if gfile and os.path.exists(gfile) else []
    result["floor_failed"], result["floor_frozen_bad"] = floor_force(result["shots"], gk, sl, frozen,
                                                                       fixes=(c.series.get("text") or {}).get("floor_fix"))
    result["mustdo_failed"] = mustdo_force(c, result["shots"])
    if result["floor_failed"] or result["mustdo_failed"] or result["sync_failed"]:
        c.log(f"  规则树：机器量底线没过 {result['floor_failed'] or '无'}、必做没做到 {result['mustdo_failed'] or '无'}、"
              f"卡点没卡上 {result['sync_failed'] or '无'} → 判不过、进返修", echo=True)
    if result["motion_frozen_bad"] or result["sync_frozen_bad"]:
        c.log(f"  注意：已冻结的 {result['motion_frozen_bad'] or ''}{'（镜内没有进展 / 原地摆动）' if result['motion_frozen_bad'] else ''}"
              f"{result['sync_frozen_bad'] or ''}{'（卡点没卡上）' if result['sync_frozen_bad'] else ''}交付硬门禁会挡；"
              "冻结镜头不返修，要改就删 freeze.json 里这几镜后 --from 4", echo=True)
    below_line(result["shots"], og, (c.series.get("review") or {}).get("deliver_at", 6))
    result["failed"] = sorted(k for k, v in result["shots"].items() if not v["pass"])
    result["passed"] = sorted(k for k, v in result["shots"].items() if v["pass"])
    util.write_json(c.p(f"review_{r}.json"), result)
    pv = c.j(f"film/preview_r{r - 1}.json")
    if pv is not None:                                              # 这一版的数补上审片的结果（round_preview 先写的）
        pv["review"] = {"round": r, "anchor_score": og.get("anchor_score"), "failed": result["failed"],
                        **{k: result.get(k) for k, _ in UNMET if result.get(k)}}
        util.write_json(c.p(f"film/preview_r{r - 1}.json"), pv)
    bad_cuts = [x["cut"] for x in result["cuts"] if not x.get("ok")]
    c.log(f"审片第 {r} 轮：内部评分 {og['anchor_score']:g}/10（{len(scores)} 次独立审片的中位数：{' / '.join(f'{x:g}' for x in scores)}）；"
          f"{motion_line('镜内运动量', mo['all'])}；通过 {result['passed']}，不过 {result['failed']}（已冻结 {len(frozen)} 镜）"
          + (f"，其中机器量镜内没有进展 / 原地摆动、判不过的 {result['motion_failed']}" if result["motion_failed"] else "")
          + (f"，卡点没卡上、判不过的 {result['sync_failed']}" if result["sync_failed"] else "") + "；"
          f"切点核对 {len(result['cuts'])} 个，建议改 {bad_cuts or '无'}")
    return result


SYNC_HARD = ("weak", "late", "early", "nocut", "flash_return")     # 逐镜卡点问题里判不过的，但 weak 里「拍上有变化、只是倍数不够」的不算（sync_hard）
# 11-06 G10（「卡点都卡的不准」= 卡点是画面）：硬线只留「计划的拍上有画面事件」——没卡上（weak 且那一帧根本没有强变化）、早了 / 晚了、
# 计划切点没真切（nocut：syncmeas sync_cuts）、闪一下又回到旧构图（flash_return，逐镜）。倍数（对比度不到 3 / 2 倍）、选择性（unselective）、
# 无起音处强变化（silent）、计划错位（misplaced）都只当建议：试跑时因卡点发回 8 次，大半是倍数
RULE_SYNC = "底线 · 卡点：计划的拍上没有画面事件（机器量，交付硬门禁）"   # 「交付硬门禁」这几个字有用：用户轮 HARD_KEEP、sync_failed 靠它认
EVENT = (2.0, 0.03)                  # syncmeas.classify 判 ON 的口径：那一帧（或前一帧）帧差 ≥ 2 × 周围中位且 ≥ 0.03 = 拍上有画面事件


def sync_hard(x):
    """这一条卡点问题判不判不过：kind 在 SYNC_HARD 里（没写 kind 的按判不过），weak 里拍上其实有变化（ratio ≥ 2、d ≥ 0.03，
    只是倍数不够）的除外。"""
    k = x.get("kind")
    if k is not None and k not in SYNC_HARD:
        return False
    if k == "weak" and x.get("ratio") is not None and x.get("d") is not None:
        return not (float(x["ratio"]) >= EVENT[0] and float(x["d"]) >= EVENT[1])
    return True



FIX_SELECT = ("这一镜重音和半拍外的位置一样强（变化平均铺开了）：只在「重 / 轻」帧上做明显的变化，「可」格不切或换成持续运动，"
              "别每个 8 分 / 16 分都切")


def sync_force(shots, sync, frozen=(), soft=()):
    """实测卡点：G.sync_stats 写在 cut 旁边 .sync.json 的逐镜 issues 里，计划的拍上没有画面事件（sync_hard：没卡上、早 / 晚、计划切点没切）的
    镜头一律判不过、带帧号进返修（和 motion_force 同级：审片看图放过了也不算，不然会被冻结）；拍上有变化只是倍数不够、选择性、
    无起音处的强变化、计划错位只进问题单当建议。shots = 审片结果（只有没冻结的镜头）。返回 (判不过的, 已冻结却没过的)。
    不改 pass（sync_plan / sync_cuts 不在 soft 里，照旧硬；sync_top、sync_select 在 gates 里 11-06 起也只报告）。CHROMA 不写 soft，行为不变。"""
    by = {}
    for it in (sync or {}).get("issues") or []:
        if it.get("shot"):
            by.setdefault(it["shot"], []).append(it)
    soft_ids = set(soft or ())
    out, fz = [], []
    for sid, its in sorted(by.items()):
        hard = [x for x in its if x.get("kind") in SYNC_HARD and sync_hard(x) and x.get("check") not in soft_ids]
        soft = [x for x in its if x not in hard]
        msg = lambda xs: "；".join(str(x.get("msg") or f"第 {x.get('F')} 帧 {x.get('kind')}") for x in xs)[:700]  # noqa: E731
        if sid in shots:
            v = shots[sid]
            if hard:
                v["pass"] = False
                v["issues"].append({"rule": RULE_SYNC, "where": msg(hard), "fix": FIX_SYNC})
                out.append(sid)
            sl_ = [x for x in soft if x.get("kind") in SYNC_HARD + ("unselective",)]   # 倍数不够、选择性、本系列降成报警的：照写修法，只当建议
            if sl_:
                sel = [x for x in sl_ if x.get("kind") == "unselective"]
                v["issues"].append({"rule": "卡点（机器量，倍数 / 选择性只报告：建议）", "where": msg(sl_),
                                    "fix": FIX_SYNC + ("；" + FIX_SELECT if sel else "")})
            soft = [x for x in soft if x.get("kind") not in SYNC_HARD + ("unselective",)]
            if soft:
                v["issues"].append({"rule": "卡点（机器量，建议）", "where": msg(soft),
                                    "fix": "没起音的帧上别做跳变（比前后几帧突然大好几倍）：挪到最近的「重 / 轻」帧，或者收成连续的单向运动"})
        elif sid in frozen and hard:
            fz.append(sid)
    return sorted(out), sorted(fz)


# 底线（根 v1.1）落在成片门禁上：稳的（定稿片都过、交付硬门禁）机器量没过就判不过、返修；量不稳的进问题单当建议、不改 pass。
FLOOR_FORCE = {"still": "这一镜有整帧静止 ≥1 s：主体一直做它的动作，镜头、字跟着切点走（片尾海报、屏息那一拍不算）",
               "similar": "相似画面连着超过 7 s：换景别、换主体的动作或颜色，别让几镜长一个样",
               "colors": "一段只留 1–2 个主色 + 黑白：颜色只给母题，其余去色",
               "grey": "不发灰：暗部压到黑、亮部推到白（MO.world 的 lo / hi，或主体打亮）",
               "first_motion": "第 1 秒要有变化：开头就切、或主体一上来就动；静止标题卡、黑底慢慢淡入都不算",
               "end_black": "结尾不留空黑屏：收在主角物 + 片名大字的海报上"}
FLOOR_HINT = {"cut_dip": "切点别闪黑闪白：换成声明的转场函数", "cut_blend": "两镜别叠在一帧里：用声明的转场函数，不手写过渡",
              "cut_moving": "切点两边都在动：上一镜别等停稳再切，下一镜别从静止起步"}   # big_hits 删了（换成实测卡点 sync_*）


def _shots_in(where, sl):
    """门禁位置字符串 → 镜头 id（写了 sNN 的直接取，只写了秒的按时间段找）。"""
    ids = set(re.findall(r"\bs\d{2}\b", where))
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*[–-]\s*(\d+(?:\.\d+)?)\s*s", where)
    if not ids and m:
        a, b = float(m.group(1)) * 60, float(m.group(2)) * 60
        ids = {s["id"] for s in sl["shots"] if s["start_f"] < b and s["end_f"] > a}
    return ids


def floor_force(shots, checks, sl, frozen=(), fixes=None):
    """shots = 审片结果（只有没冻结的镜头）。返回 (判不过的, 已冻结却没过的)。fixes = 系列改写的修法（{检查 id: 修法}，
    没写 = FLOOR_FORCE / FLOOR_HINT 原文）。"""
    out, fz = set(), set()
    fixes = fixes or {}
    for ck in checks:
        if ck.get("status") not in ("warn", "fail"):
            continue
        tab = FLOOR_FORCE if ck["id"] in FLOOR_FORCE else (FLOOR_HINT if ck["id"] in FLOOR_HINT else None)
        if tab is FLOOR_FORCE and ck.get("soft"):                      # 系列 gates.soft 降成报警的（原片自己过不了）：只当建议
            tab = {ck["id"]: FLOOR_FORCE[ck["id"]]}
        if not tab:
            continue
        for w in ck.get("where") or []:
            for sid in _shots_in(w, sl):
                if sid in shots:
                    v = shots[sid]
                    if tab is FLOOR_FORCE:
                        v["pass"] = False
                        out.add(sid)
                    rule = f"底线 · {ck['name']}" + ("（机器量，交付硬门禁）" if tab is FLOOR_FORCE else "（机器量，建议）")
                    if ck.get("soft") and tab is not FLOOR_FORCE:
                        rule += "（本系列降成报警）"
                    if not any(i.get("rule") == rule for i in v["issues"]):
                        v["issues"].append({"rule": rule, "where": w[:200], "fix": fixes.get(ck["id"]) or tab[ck["id"]]})
                elif sid in frozen and tab is FLOOR_FORCE:
                    fz.add(sid)
    return sorted(out), sorted(fz)


def mustdo_force(c, shots):
    """制作代理最后一次也没做到的必做（done.json 的 must_do_misses）：交付硬门禁会挡，所以这几镜判不过、进返修。"""
    out = []
    for sid, v in shots.items():
        miss = (c.j(f"shots/{sid}/done.json") or {}).get("must_do_misses") or []
        if miss:
            v["pass"] = False
            v["issues"].append({"rule": "必做（编排器查代码，没做到交付不放行）", "where": "；".join(miss)[:300],
                                "fix": "照必做清单补上；同类的一起改"})
            out.append(sid)
    return sorted(out)


def code_rows(c):
    """规则树的代码级行：每条规则看所有镜头最新的 done.json（must_do_rules）。"""
    sl, bad = c.j("shotlist.json") or {}, {}
    for s in sl.get("shots") or []:
        for r in (c.j(f"shots/{s['id']}/done.json") or {}).get("must_do_rules") or []:
            bad.setdefault(r, []).append(s["id"])
    return [TR.row(c.tree, rid, rid not in bad, bad.get(rid, []), how="制作代理交付的代码（编排器交付前查）", stage="制作")
            for rid in CODE_RIDS if rid in c.tree["_idx"]]


RULE_MOVE = "底线 · 镜内有进展（机器量：有效运动量，交付硬门禁）"
RULE_SWAY = "底线 · 原地摆动充数（机器量，交付硬门禁）"


def motion_force(shots, mo, mc, frozen=(), soft=()):
    """镜内运动量是交付硬门禁（gates motion_eff / motion_sway / motion_still）：机器量不过的镜头一律判不过，审片看图放过了也不算——
    不然它会被冻结，返修再也碰不到，到交付才被硬门禁挡住、整集出不了片。两种不过分开判、各给各的修法：
    没有进展（有效运动量不够；整片近乎静止帧超了时，近乎静止占比超线的镜头也算，不算屏息那一拍、片尾海报停留）→ FIX_MOVE；
    原地摆动充数（原地来回占比超线、同一张图宽紧来回跳）→ FIX_SWAY（删掉来回，不是减小幅度）。
    shots = 审片结果（只有没冻结的镜头）。返回 (判不过的, 已冻结却没过的)。
    soft = 系列 gates.soft 里的门禁 id（原片自己过不了、这个系列降成报警的）：motion_eff 在里面时「没有进展」只当建议（问题单照写、
    不改 pass；整片近乎静止 motion_still 照旧强制），motion_sway 在里面时「原地摆动」只当建议。CHROMA 不写 soft，行为不变。"""
    whole_bad = (mo.get("all") or {}).get("ok") is False
    lim = mc["still_frac_max"]
    soft = set(soft or ())
    still_bad = lambda m: whole_bad and (m.get("still_ex", m.get("still")) or 0) > lim  # noqa: E731
    bad_move = lambda m: (m.get("ok") is False and "motion_eff" not in soft) or still_bad(m)  # noqa: E731
    bad_sway = lambda m: m.get("sway_ok") is False and "motion_sway" not in soft  # noqa: E731
    hint_move = lambda m: m.get("ok") is False and "motion_eff" in soft and not still_bad(m)  # noqa: E731
    hint_sway = lambda m: m.get("sway_ok") is False and "motion_sway" in soft  # noqa: E731
    out = []
    for sid, v in shots.items():
        m = mo.get(sid) or {}
        hit = False
        if bad_sway(m):
            v["issues"].append({"rule": RULE_SWAY, "fix": FIX_SWAY, "where": motion_line(sid, m)})
            hit = True
        if bad_move(m):
            v["issues"].append({"rule": RULE_MOVE, "fix": FIX_MOVE, "where": f"{motion_line(sid, m)}；要{motion_want(mc)}"})
            hit = True
        if hint_sway(m):
            v["issues"].append({"rule": RULE_SWAY + "（本系列降成报警：建议）", "fix": FIX_SWAY, "where": motion_line(sid, m)})
        if hint_move(m):
            v["issues"].append({"rule": RULE_MOVE + "（本系列降成报警：建议）", "fix": FIX_MOVE,
                                "where": f"{motion_line(sid, m)}；参照{motion_want(mc)}"})
        if hit:
            v["pass"] = False
            out.append(sid)
    if whole_bad and not out:                                       # 整片超线、却没有一镜单独超：点名近乎静止最多的那一镜
        cand = [(-(mo.get(k) or {}).get("still_ex", 0) or 0, k) for k in shots if ((mo.get(k) or {}).get("still_ex") or 0) > 0]
        if cand:
            sid = min(cand)[1]
            shots[sid]["pass"] = False
            shots[sid]["issues"].append({"rule": RULE_MOVE, "fix": FIX_MOVE,
                                         "where": f"整片{motion_line('', mo['all'])[1:]}；{motion_line(sid, mo.get(sid))}，是近乎静止最多的一镜"})
            out.append(sid)
    fz = [sid for sid in frozen if sid not in shots and (bad_move(mo.get(sid) or {}) or bad_sway(mo.get(sid) or {}))]
    return sorted(out), sorted(fz)


def below_line(shots, og, line, k=2):
    """内部评分没到线、审片却一个镜头都没判不过：挑这一轮（没冻结的）分最低的 k 镜接着修，
    不然返修循环会以「全过」提前停（没到 6 分就接着修，修满 3 轮再交）。返回挑中的 id。"""
    sc = og.get("anchor_score")
    if not (isinstance(sc, (int, float)) and sc < line and shots and all(v["pass"] for v in shots.values())):
        return []
    pick = sorted(shots.values(), key=lambda v: (v.get("score", 5), v["id"]))[:k]
    for v in pick:
        v["pass"] = False
        v["issues"].append({"rule": f"内部评分 {sc:g} 没到 {line}", "where": "全片（审片没点名镜头，按这一轮分数最低挑的）",
                            "fix": og.get("anchor_why") or "照全片意见再提一档"})
    return [v["id"] for v in pick]


def loop_stop(rv, r, max_fix, line, max_failed=3):
    """返修循环停不停：内部评分（3 次审片的中位数）≥ line 且没过的镜头 ≤ max_failed 才停、交你；
    但还有镜内没有进展 / 原地摆动（rv.motion_failed）、卡点没卡上（rv.sync_failed）这类交付硬门禁的镜头时不按分数停，接着修，
    也不能以「全过」收尾；返修满 max_fix 轮也停（照样交付）。不停返回 None。"""
    s = (rv.get("global") or {}).get("anchor_score")
    nf = len(rv["failed"])
    hard = rv.get("motion_failed") or rv.get("floor_failed") or rv.get("mustdo_failed") or rv.get("sync_failed")
    if isinstance(s, (int, float)) and s >= line and nf <= max_failed and not hard:
        return (f"内部评分 {s:g} ≥ {line}、没过的镜头 {nf} 个 ≤ {max_failed}：停止返修，交你打分"
                + (f"（{', '.join(rv['failed'])} 审片没过，写进报告）" if rv["failed"] else ""))
    if not rv["failed"] and not rv.get("sync_failed"):
        return "审片全过" + ("" if s is None else f"（内部评分 {s:g}，没到 {line}，但没有还没冻结的镜头可修）")
    if r > max_fix:
        return f"返修已满 {max_fix} 轮，还没满足：{'；'.join(unmet(rv, line, max_failed))}：照样交付，report.md 写卡在哪"
    return None


UNMET = (("motion_failed", "镜内没有进展 / 原地摆动"), ("floor_failed", "机器量底线没过"), ("mustdo_failed", "必做没做到"),
         ("sync_failed", "计划的拍上没有画面事件"))


def unmet(rv, line, max_failed=3):
    """停止条件里没满足的那几项（以前一律写「内部评分没到线」，真正没满足的是还有 2 镜不过）。"""
    s = (rv.get("global") or {}).get("anchor_score")
    out = []
    if not isinstance(s, (int, float)):
        out.append("内部评分没打出来")
    elif s < line:
        out.append(f"内部评分 {s:g} < {line}")
    if len(rv["failed"]) > max_failed:
        out.append(f"没过的镜头 {len(rv['failed'])} 个 > {max_failed}（{', '.join(rv['failed'])}）")
    out += [f"{say} {', '.join(rv[k])}" for k, say in UNMET if rv.get(k)]
    return out or [f"没过的镜头 {', '.join(rv['failed']) or '无'}"]


def _code_file_for(c, sid, sha):
    """shots/<id>/ 里内容 sha 等于 sha 的那份代码（shot_shot.py / shot_fix<k>.py / shot_restore<k>.py）；没有返回 None。"""
    sd = c.p("shots", sid)
    for f in sorted(os.listdir(sd)) if os.path.isdir(sd) else []:
        if re.fullmatch(r"shot_\w+\.py", f) and util.sha256_file(os.path.join(sd, f)) == sha:
            return os.path.join(sd, f)
    return None


def sync_work_code(c, sid, why, stamp=None):
    """work/out/shot.py 和 done.json 的 code_sha 对不上（返修失败保留上一版、返修半截被打断）：当前那份挪进 shots/<id>/_old/<时间>/，
    换回 done.json（出画的那一版）对应的代码。换过返回 True。11-05 审查：不换的话下一轮审片扫的是失败的那份，冻结时 code_sha 也对不上。"""
    d = c.j(f"shots/{sid}/done.json") or {}
    code = c.p("shots", sid, "work", "out", "shot.py")
    want = d.get("code_sha")
    if not want or (os.path.exists(code) and util.sha256_file(code) == want):
        return False
    src = _code_file_for(c, sid, want)
    if not src:
        c.log(f"  注意：{sid} 的 work/out/shot.py 和 done.json 对不上（{why}），shots/{sid}/ 里也找不到出画的那份代码，没换", echo=True)
        return False
    dst = c.p("shots", sid, "_old", stamp or util.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(dst, exist_ok=True)
    if os.path.exists(code):
        shutil.move(code, os.path.join(dst, "work_out_shot.py"))
    os.makedirs(os.path.dirname(code), exist_ok=True)
    shutil.copy2(src, code)
    c.log(f"  {sid}：{why}——work/out/shot.py 换回出画的那份（{os.path.basename(src)}），原来那份挪进 shots/{sid}/_old/{os.path.basename(dst)}/",
          echo=False)
    return True


def _restored_now(c, r):
    """这一轮（r）是看片意见轮时，恢复一稿后重渲过的镜头（它们的 render.mp4 是新渲的，和 cut_r{r-1} 不逐位一样）。"""
    pr = c.j(NOTES_PROG) or {}
    if pr.get("round") != r:
        return set()
    return {k for k, v in (pr.get("restore") or {}).items() if (v or {}).get("ok") and v.get("rerender", True) is not False}


def fix_round(c, r, rv):
    topic, sl = c.j("topic.json"), c.j("shotlist.json")
    prev_md5 = c.j(f"film/cut_r{r - 1}.md5.json")
    frozen = c.j("freeze.json", {})
    new = [s["id"] for s in sl["shots"] if s["id"] in rv["passed"] and s["id"] not in frozen]
    rec = G.freeze_record(prev_md5, sl["shots"], new)
    restored = _restored_now(c, r)
    for sid in new:                                                 # 冻结的是下一版拼片里真会出现的那一版：拼片是 -c copy，所以按这一镜
        try:                                                        # render.mp4 的逐帧 md5 记（平时和 cut_r{r-1} 那一段一样；用户轮恢复
            h = util.sha256_bytes("".join(media.framemd5(c.p("shots", sid, "render.mp4"))).encode())   # 一稿重渲过的、返修收下过但
        except RuntimeError as e:                                   # 没进上一版拼片的镜头不一样——以前会必然报「冻结镜头被改动」）
            c.log(f"  {sid}：冻结记录的逐帧 md5 没算成（{str(e)[:120]}），按 cut_r{r - 1} 记", echo=False)
            continue
        if h != rec[sid]["md5"]:
            rec[sid].update(md5=h, src="render.mp4")
            c.log(f"  注意：{sid} 判过、冻结，但它现在的 render.mp4 和 cut_r{r - 1} 那一段不一样"
                  + ("（用户轮恢复一稿重渲的）" if sid in restored else "（不是上一版拼片里那一版：之前返修收下过、没进拼片）")
                  + "，按现在的 render.mp4 冻结", echo=True)
    for sid in new:
        d = c.j(f"shots/{sid}/done.json")
        rec[sid].update(round=r, code_sha=d["code_sha"], render_sha=d["render_sha"])
    frozen.update(rec)
    util.write_json(c.p("freeze.json"), frozen)
    fdir = c.p("frames", f"r{r}")
    def one(sid):
        s = next(x for x in sl["shots"] if x["id"] == sid)
        d = c.j(f"shots/{sid}/done.json") or {}
        if d.get("kind") == "fix" and d.get("round") == r:
            return d                                                # 续跑：这一轮已经返修过
        imgs = [os.path.join(fdir, f) for f in os.listdir(fdir) if f.startswith(f"zoom_{sid}")]
        imgs += [os.path.join(fdir, f"mid_f{(s['start_f'] + s['end_f']) // 2:05d}.jpg"), os.path.join(fdir, f"kymo_{sid}.jpg")]
        return make_shot(c, s, "fix", topic, sl, issues=rv["shots"][sid]["issues"], rnd=r, review_imgs=[p for p in imgs if os.path.exists(p)])
    results = dict(zip(rv["failed"], par_map(c, one, rv["failed"])))   # 没过的镜头同时修（各镜目录独立）
    # 冻结镜头：代码和渲染文件不许变
    file_bad = []
    for sid, f in frozen.items():
        code, rend = c.p("shots", sid, "work", "out", "shot.py"), c.p("shots", sid, "render.mp4")
        if not (os.path.exists(code) and os.path.exists(rend)) or util.sha256_file(rend) != f["render_sha"] \
                or util.sha256_file(code) != f["code_sha"]:
            file_bad.append(sid)
    cut, md5 = assemble(c, r)
    fc = G.freeze_checks(md5, frozen)
    checks, gm = run_gates(c, cut, f"gates_r{r}.json")
    round_preview(c, r, cut, checks, gm)
    util.write_json(c.p(f"fix_{r}.json"), {"round": r, "fixed": list(results), "frozen": sorted(frozen), "freeze_check": fc,
                                           "frozen_files_changed": file_bad, "cut": os.path.relpath(cut, c.ep), "at": util.iso()})
    c.log(f"修复第 {r} 轮：重做 {list(results)}；冻结 {len(frozen)} 镜逐帧检查 {fc['status']}（{fc['value']}）")
    if fc["status"] != "ok" or file_bad:
        raise util.Stop(5, "frozen_changed", f"冻结镜头被改动：{fc.get('where') or file_bad}")


# ============================================================================ 看片意见轮（--notes）
# 你看完片子的意见（写好的 review_notes.json）直接进返修：run_episode --notes <文件> 把它复制成 <集>/review_notes.json、
# sha 记进 state.json 的 notes_rounds（同一 sha 只消费一次，续跑跳过）；s4_review_loop 开头 notes_round 按 notes 做：
#   targeted（只改列出的镜头，其余冻结）：存档 → 恢复一稿代码、用这一集现有的引擎副本重渲（逐帧 md5 对 cut_r0，不一致只记警告）→
#            升级这一集的引擎副本（engine/src 挪到 engine/_old_src/<时间>，重新复制主引擎）→ 解冻 → 合成 source='notes' 的 review_<r>.json
#            （问题单 = 用户条目 + 劣质特效扫描发回行 + 原审片里机器量的交付硬门禁条目）→ 照常 fix_round → 下一轮正常审片。
#   append（追加到已有审片的每镜问题单最前面）：存档一份原审片 → 升级引擎 → 合成（redo_fixed 的镜头已经按旧约束修过：只发用户条目和扫描
#            发回、不重发旧意见，done.json 挪进 _old、留一份 kind=fix_pre_notes 的副本）→ 照常 fix_round。
# 返修代理任务书的 {{NOTES_BLOCK}} 块（05_fix_agent.md，提示词组写文字）由 notes_vars 传值：原话、「这一轮只换特效来源……」、本镜一稿运动量。
# 倒退底线取 notes 的 motion_floor_r0（只有新值 < 0.8 × 底线且没到线才发回，和平时一样）。--max-rounds 从用户那一轮起算。
NOTES_FILE, NOTES_PROG, NOTES_DIR = "review_notes.json", "notes_round.json", "review_notes"
NOTES_SCOPE = "这一轮只换特效来源（2D 替身换成实拍叠层或画面派生），构图、素材、切点、卡点、字、转场不动"
NOTES_SWAP = "删掉一层就在同处换一件会动的真东西，配方见手册 §5b"
HARD_KEEP = ("交付硬门禁", "交付不放行")                            # 原审片里要带进用户轮问题单的机器量条目（rule 里有这几个字）


def _sha_file(p):
    with open(p, "rb") as f:
        return util.sha256_bytes(f.read())[:16]


def _resolve(rel, *bases):
    """notes 里写的相对路径按几个起点找；找不到返回 None。"""
    if not rel:
        return None
    if os.path.isabs(rel):
        return rel if os.path.exists(rel) else None
    for b in bases:
        for cand in (os.path.join(b, rel), os.path.join(b, os.path.basename(rel))):
            if b and os.path.exists(cand):
                return cand
    return None


def notes_check(notes):
    """notes 的格式问题（一条一句；空 = 能用）。"""
    if not isinstance(notes, dict):
        return ["不是 JSON 对象"]
    out = []
    if notes.get("mode", "append") not in ("targeted", "append"):
        out.append(f"mode 只能是 targeted / append，不是 {notes.get('mode')!r}")
    if not isinstance(notes.get("shots") or {}, dict):
        out.append("shots 要是 {镜头号: [{rule, frames, box, where, fix}, …]}")
    for sid, its in (notes.get("shots") or {}).items():
        for i, it in enumerate(its if isinstance(its, list) else [None]):
            if not isinstance(it, dict) or not it.get("fix"):
                out.append(f"shots.{sid} 第 {i + 1} 条要有 fix")
    if not isinstance(notes.get("restore") or {}, dict):
        out.append("restore 要是 {镜头号: {code, expect_look?}}")
    return out


def notes_intake(ep, src, state, log):
    """run_episode --notes：校验、复制成 <集>/review_notes.json（旧的挪进 _old/<时间>/）、restore 引用的文件复制进 <集>/review_notes/，
    sha 记进 state["notes_rounds"]。返回 "pending"（这一轮要做）或 "done"（同一份已经用过，跳过）。格式不对抛 util.Stop。"""
    with open(src, "rb") as f:
        raw = f.read()
    sha = util.sha256_bytes(raw)[:16]
    rounds = state.setdefault("notes_rounds", {})
    if (rounds.get(sha) or {}).get("status") == "done":
        log(f"看片意见 {os.path.basename(src)}（sha {sha}）已经用过一轮，跳过（同一份只消费一次；要再来一轮就改 notes 再给）")
        return "done"
    try:
        notes = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise util.Stop(2, "review_notes", f"--notes 不是合法 JSON：{e}")
    bad = notes_check(notes)
    if bad:
        raise util.Stop(2, "review_notes", "--notes 格式不对：" + "；".join(bad))
    dst = os.path.join(ep, NOTES_FILE)
    if os.path.exists(dst) and _sha_file(dst) != sha:
        old = os.path.join(ep, "_old", util.now().strftime("%Y%m%d-%H%M%S"))
        os.makedirs(old, exist_ok=True)
        shutil.move(dst, os.path.join(old, NOTES_FILE))
    with open(dst, "wb") as f:
        f.write(raw)
    files = {}
    rounds[sha] = {"status": "pending", "src": os.path.abspath(src), "at": util.iso(), "mode": notes.get("mode", "append"),
                   "shots": sorted(notes.get("shots") or {}), "files": files}
    log(f"看片意见：{os.path.basename(src)}（sha {sha}，{notes.get('mode', 'append')}，{len(notes.get('shots') or {})} 镜）"
        f"复制成 {NOTES_FILE}，审片环节开头先做看片意见轮")
    return "pending"


def _state(c):
    st = getattr(c, "state", None)
    return st if isinstance(st, dict) else (c.j("state.json") or {})


def notes_pending(c):
    """(notes, sha)：<集>/review_notes.json 在、state.json 的 notes_rounds 里记的是 pending；不然 (None, sha 或 None)。"""
    p = c.p(NOTES_FILE)
    if not os.path.exists(p):
        return None, None
    sha = _sha_file(p)
    rec = (_state(c).get("notes_rounds") or {}).get(sha) or {}
    if rec.get("status") != "pending":
        return None, sha
    return c.j(NOTES_FILE), sha


def notes_for(c, rnd):
    """这一轮（rnd）是不是看片意见轮：是就返回 notes（返修任务书的 {{NOTES_BLOCK}} 块、倒退底线用），不是返回 None。"""
    if rnd is None or isinstance(rnd, str):
        return None
    pr = c.j(NOTES_PROG) or {}
    if pr.get("round") != rnd or not (pr.get("steps") or {}).get("review"):
        return None
    p = c.p(NOTES_FILE)
    if not os.path.exists(p) or _sha_file(p) != pr.get("sha"):
        return None
    return c.j(NOTES_FILE)


def notes_vars(c, rnotes, sid):
    """返修提示词 05_fix_agent.md 的 {{NOTES_BLOCK}}：不是看片意见轮就是空串；是就照 prompts/05_fix_notes.md（提示词组写的文字）填
    原话（SAID）、这一轮的范围（SCOPE = notes 的 scope_note）、本镜一稿的运动量（MOTION_R0 = notes 的 motion_floor_r0）。"""
    if not rnotes:
        return {"NOTES_BLOCK": ""}
    said = rnotes.get("said") or []
    said = [said] if isinstance(said, str) else list(said)
    fl = (rnotes.get("motion_floor_r0") or {}).get(sid)
    v = dict(SAID="\n".join(f"「{x}」" for x in said) or "（没写原话）", SCOPE=str(rnotes.get("scope_note") or NOTES_SCOPE).strip(),
             MOTION_R0=(f"{float(fl):g}" if fl is not None else "（这一镜没量）"))
    try:
        txt = c.prompt("05_fix_notes", **v)
    except (OSError, KeyError):                                     # 模板不在 / 占位符对不上：自己拼（措辞照模板）
        txt = (f"## 你看过一稿的意见（这一轮最先改，优先级最高）\n你的原话：\n{v['SAID']}\n\n- {v['SCOPE']}\n- {NOTES_SWAP}。\n"
               f"- 本镜一稿（你看过的那一版）的有效运动量：{v['MOTION_R0']}。改完不能比它明显少。\n")
    return {"NOTES_BLOCK": txt.rstrip() + "\n"}


def notes_round_no(c):
    """看片意见轮放在第几轮审片：最后一轮审片已经返修完（fix_<k>.json 有 cut）= k + 1，没返修 = k（替换 / 追加到它），一轮都没有 = 1。"""
    rs = sorted(int(f[7:-5]) for f in os.listdir(c.ep) if re.fullmatch(r"review_\d+\.json", f))
    if not rs:
        return 1
    k = rs[-1]
    return k + 1 if (c.j(f"fix_{k}.json") or {}).get("cut") else k


def _mv(src, dst):
    if os.path.lexists(src):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        return True
    return False


def notes_archive(c, notes, r, stamp, mode):
    """存档（只挪不删）：targeted 把 review_<r>.json 和 notes.archive 列的挪进 _old/<时间>/；append 复制一份原审片。
    restore 的镜头：work/out、done.json、render.mp4（连同 .eff / .sync）、shot_fix*.py 挪进 shots/<id>/_old/<时间>/。
    redo_fixed 的镜头：done.json（这一轮按旧约束修过的）挪进 _old，原地留一份 kind=fix_pre_notes 的副本（不让 fix_round 当成已修过跳过）。"""
    old = c.p("_old", stamp)
    moved = []
    names = [f"review_{r}.json"] + [x for x in notes.get("archive") or [] if x != f"review_{r}.json"]
    for f in names:
        if mode == "targeted" or f != f"review_{r}.json":
            if _mv(c.p(f), os.path.join(old, f)):
                moved.append(f)
        elif os.path.exists(c.p(f)):
            os.makedirs(old, exist_ok=True)
            shutil.copy2(c.p(f), os.path.join(old, f))
            moved.append(f + "（复制）")
    for sid, spec in (notes.get("restore") or {}).items():
        sd = c.p("shots", sid)
        dst = os.path.join(sd, "_old", stamp)
        rer = (spec or {}).get("rerender", True) is not False         # rerender=false：done.json、render.mp4 本来就是一稿，只换回代码
        for f in ("work/out", "done.json", "render.mp4", "render.mp4.eff.json", "render.mp4.eff.npz", "render.mp4.sync.json")[:6 if rer else 1]:
            if _mv(os.path.join(sd, f), os.path.join(dst, f)):
                moved.append(f"shots/{sid}/{f}")
        for f in sorted(os.listdir(sd)) if rer and os.path.isdir(sd) else []:
            if re.fullmatch(r"shot_(fix|restore)\d+\.py", f):
                _mv(os.path.join(sd, f), os.path.join(dst, f))
                moved.append(f"shots/{sid}/{f}")
    for sid in notes.get("redo_fixed") or []:
        dp = c.p("shots", sid, "done.json")
        d = util.read_json(dp)
        if not d or d.get("kind") != "fix":
            continue
        dst = c.p("shots", sid, "_old", stamp)
        os.makedirs(dst, exist_ok=True)
        shutil.copy2(dp, os.path.join(dst, "done.json"))
        f = c.p("shots", sid, f"shot_fix{d.get('round')}.py")
        if os.path.exists(f):
            shutil.copy2(f, os.path.join(dst, os.path.basename(f)))
        util.write_json(dp, dict(d, kind="fix_pre_notes", round=None, pre_notes={"kind": d.get("kind"), "round": d.get("round"),
                                                                                "archived": os.path.relpath(dst, c.ep)}))
        moved.append(f"shots/{sid}/done.json（kind fix 第 {d.get('round')} 轮 → fix_pre_notes）")
    c.log(f"  看片意见轮：存档 {len(moved)} 项到 _old/{stamp}/ 和 shots/<id>/_old/{stamp}/（只挪不删）：{moved[:8]}", echo=False)
    return moved


def notes_restore(c, notes, r, prog, save):
    """恢复一稿：shots/<id>/shot_shot.py → work/out/shot.py，用这一集现有的引擎副本重渲
    （render_and_accept kind='restore'、strict=False，不调代理），逐帧 md5 对 film/cut_r0.md5.json（不一致只记警告）。"""
    sl = c.j("shotlist.json")
    ref = c.j("film/cut_r0.md5.json") or []
    res = prog.setdefault("restore", {})
    for sid, spec in (notes.get("restore") or {}).items():
        if (res.get(sid) or {}).get("ok"):
            continue
        s = next((x for x in sl["shots"] if x["id"] == sid), None)
        if not s:
            c.log(f"  看片意见轮：restore 的 {sid} 不在镜头表里，跳过", echo=True)
            continue
        sd = c.p("shots", sid)
        work = os.path.join(sd, "work")
        out = os.path.join(work, "out")
        os.makedirs(out, exist_ok=True)
        code = os.path.join(out, "shot.py")
        src = _resolve((spec or {}).get("code") or f"shots/{sid}/shot_shot.py", c.ep, sd)
        if not src:
            raise util.Stop(5, "notes_restore", f"看片意见轮：{sid} 的一稿代码 {(spec or {}).get('code')} 找不到")
        shutil.copy2(src, code)
        look = None
        if (spec or {}).get("rerender", True) is False:              # 只换回代码：done.json、render.mp4 本来就是一稿
            res[sid] = {"ok": True, "rerender": False, "look": look}
            c.log(f"  看片意见轮：{sid} 换回一稿代码（work/out 里返修到一半的那份挪进 _old；没重渲，done.json / render.mp4 本来就是一稿）",
                  echo=False)
            save()
            continue
        st = {"sync_sent": 0, "motion_sent": 0, "fx_sent": 0, "table": sync_table(sync_rows(s, c.mm()))}
        d, err = render_and_accept(c, s, "restore", r, work, code, os.path.join(sd, "render.mp4"), os.path.join(sd, "done.json"),
                                   strict=False, st=st)
        if not d:
            raise util.Stop(5, "notes_restore", f"看片意见轮：{sid} 恢复一稿后用这一集的引擎副本重渲没成：{err.strip()[-400:]}")
        same, diff, ps = None, None, None
        if ref:
            try:
                now = media.framemd5(os.path.join(sd, "render.mp4"))
                want = ref[s["start_f"]:s["end_f"]]
                diff = sum(1 for a, b in zip(now, want) if a != b) + abs(len(now) - len(want))
                same = diff == 0
            except RuntimeError as e:
                c.log(f"  {sid}：逐帧 md5 没算成（{str(e)[:120]}）", echo=False)
        cut0 = c.p("film", "cut_r0.mp4")
        if not same and os.path.exists(cut0):                       # 引擎的 x264 编码不逐位确定（同一份原始帧编两次 88/100 帧 md5 不同）：
            ps = restore_psnr(c, os.path.join(sd, "render.mp4"), cut0, s["start_f"], s["end_f"])   # 一致与否按 PSNR 判
        ok_ps = bool(ps) and ps["median"] >= RESTORE_PSNR[0] and ps["min"] >= RESTORE_PSNR[1]
        res[sid] = {"ok": True, "md5_same": same, "md5_diff": diff, "psnr": ps, "same_look": True if same else (ok_ps if ps else None),
                    "eff": (d.get("motion") or {}).get("eff"), "look": look,
                    "cheapfx_send": len((d.get("cheapfx") or {}).get("send") or [])}
        if same:
            how = "；逐帧 md5 和 cut_r0 一样"
        elif ps:
            how = (f"；和 cut_r0 比 PSNR 中位 {ps['median']:.1f} dB、最低 {ps['min']:.1f} dB（编码不逐位确定，按 PSNR 判）："
                   + ("和一稿一致" if ok_ps else "注意：和一稿明显不一样（代码可能拿错了，只记警告，不停）"))
        else:
            how = "；没对上 cut_r0（没有 cut_r0.md5.json / PSNR 没量成）"
        c.log(f"  看片意见轮：{sid} 恢复一稿重渲完" + (f"，有效运动量 {res[sid]['eff']:g}" if res[sid]["eff"] is not None else "") + how,
              echo=True)
        save()
    return res


RESTORE_PSNR = (40.0, 30.0)          # 恢复重渲和 cut_r0 同一段：PSNR 中位 ≥ 40 dB、最低 ≥ 30 dB 算一致（实测重渲两次 45 / 36 dB）


def restore_psnr(c, video, ref, a, b):
    """video（一镜）对 ref（整片）的 [a, b) 帧段逐帧 PSNR：{median, min, n}；量不了返回 None。"""
    st = c.p("logs", f"psnr_{os.path.basename(os.path.dirname(video))}.txt")
    os.makedirs(os.path.dirname(st), exist_ok=True)
    fc = (f"[1:v]trim=start_frame={int(a)}:end_frame={int(b)},setpts=PTS-STARTPTS[r];[0:v]setpts=PTS-STARTPTS[v];"
          f"[v][r]psnr=stats_file='{st}'")
    try:
        media.ff(["-i", video, "-i", ref, "-lavfi", fc, "-f", "null", "-"], log_path=c.p("logs", "ffmpeg.log"))
        vals = []
        for ln in util.read(st).splitlines():
            m = re.search(r"psnr_avg:(\S+)", ln)
            if m:
                vals.append(99.0 if m.group(1) == "inf" else float(m.group(1)))
    except (RuntimeError, OSError, ValueError) as e:
        c.log(f"  PSNR 没量成（{str(e)[:160]}）", echo=False)
        return None
    if not vals:
        return None
    vs = sorted(vals)
    return {"median": round(vs[len(vs) // 2], 2), "min": round(vs[0], 2), "n": len(vs)}


def notes_engine(c, stamp):
    """升级这一集的引擎副本：engine/src 挪到 engine/_old_src/<时间>（不删），复制主引擎，重写 timeline / 占位镜头。冻结镜头不重渲。"""
    rd = c.renderer
    src = os.path.join(rd.root, "src")
    if os.path.isdir(src):
        dst = os.path.join(rd.root, "_old_src", stamp)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        c.log(f"  看片意见轮：这一集的引擎副本 engine/src 挪到 engine/_old_src/{stamp}/，换成主引擎（返修用新引擎；冻结镜头不重渲）",
              echo=True)
    if not os.path.isdir(src):
        shutil.copytree(rd.src_dir, src, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    rd._stale_told = False
    rd.setup(c.j("beatmap.json"), c.j("shotlist.json"))
    bake_elements(c)


def bake_elements(c):
    """这一集引擎里预烘实拍叠层（src.tools.elements bake；参数没变就跳过，CPU 约半分钟）。失败只记日志：首次渲染时引擎会自己烘。"""
    fn = getattr(c.renderer, "bake_elements", None)
    if not fn:
        return
    try:
        r = fn()
        if r and not r.get("ok"):
            c.log(f"  注意：实拍叠层预烘没成（{str(r.get('why') or '')[-200:]}），首次用到时引擎会自己烘", echo=False)
    except Exception as e:  # noqa: BLE001
        c.log(f"  注意：实拍叠层预烘出错（{type(e).__name__}: {str(e)[:160]}），首次用到时引擎会自己烘", echo=False)


def _box(b):
    """notes 的 box [x0, y0, x1, y1]（1080p）→ (x, y, w, h)，宽高取偶数、夹在画面里；不对返回 None。"""
    try:
        x0, y0, x1, y1 = (int(round(float(v))) for v in b)
    except (TypeError, ValueError):
        return None
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(1920, x1), min(1080, y1)
    if x1 - x0 < 16 or y1 - y0 < 16:
        return None
    return x0, y0, (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2


def notes_crops(c, s, items, cut, fdir, coords=()):
    """用户条目的 100 % 裁图（frames 的头帧和中间帧，box 给了就裁 box，没给就取代码里的坐标 / 像素定位器的窗口），
    外加这一镜的中间帧和切线图（fix_round 按 zoom_<id>* / mid_f / kymo_<id> 取给返修代理）。返回图的路径。"""
    sid = s["id"]
    ffl = c.p("logs", "ffmpeg.log")
    out, done = [], set()
    a0, b0 = s["start_f"], s["end_f"] - 1
    m = (s["start_f"] + s["end_f"]) // 2
    try:
        out += media.frames_jpg(cut, [m], fdir, "mid", log_path=ffl)
        out.append(media.kymo(cut, s["start_f"], s["end_f"], os.path.join(fdir, f"kymo_{sid}.jpg"), log_path=ffl))
    except Exception as e:  # noqa: BLE001  抽帧失败不拦返修
        c.log(f"  {sid}：中间帧 / 切线图没抽成（{str(e)[:120]}）", echo=False)
    py = renderer_py(c)
    for k, it in enumerate(items or []):
        fr = it.get("frames") or [m, m]
        try:
            a, b = int(fr[0]), int(fr[-1])
        except (TypeError, ValueError, IndexError):
            a = b = m
        a, b = max(a0, min(b0, a)), max(a0, min(b0, b))
        fs = sorted({a, (a + b) // 2})
        box = _box(it.get("box")) if it.get("box") else None
        if not box and coords:
            x = coords[min(k, len(coords) - 1)]
            box = tuple(CF.crop_box(x["x"], x["y"]))
        if not box:
            mids = media.frames_jpg(cut, fs[:1], fdir, f"loc_{sid}_{k}", log_path=ffl)
            bx = CF.locate_sub(py, mids).get(mids[0]) if py else None
            box = tuple(bx) if bx else (640, 360, 640, 360)
        key = (box, tuple(fs))
        if key in done:
            continue
        done.add(key)
        try:
            out += media.frames_jpg(cut, fs, fdir, f"zoom_{sid}_notes{k}", width=box[2], crop=box, log_path=ffl)
        except Exception as e:  # noqa: BLE001
            c.log(f"  {sid}：用户条目 {k + 1} 的裁图没抽成（{str(e)[:120]}）", echo=False)
    return out


_LN = r"\d{1,4}(?:\s*[-–]\s*\d{1,4})?"
_LNS = rf"{_LN}(?:\s*[、,，]\s*{_LN})*"
_UNIT = ("px", "f", "fps", "s", "ms", "x", "frames", "frame")


def _code_lines(text):
    """用户条目 where 里写到的代码行：(单行集合, [(起, 止)])。只认这几种写法（数量、px、坐标、帧号不算行号）——
    `shot.py:231-232、242-243`（冒号后面那一串）、`156、157、166 行` / `第 83 行`（带「行」字）、`307-363 six_facets`（区间后面
    紧跟代码名，单位 px / f 这类除外；`；184 SR.particles` 同理）。"""
    t = str(text or "")
    lists = re.findall(rf"shot(?:_\w+)?\.py:\s*({_LNS})", t)
    lists += re.findall(rf"(?<![\d.])({_LNS})\s*行", t)
    lists += [m.group(1) for m in re.finditer(r"(?:^|(?<=[；;，,、（(\s]))(\d{1,4}(?:\s*[-–]\s*\d{1,4})?)\s+([A-Za-z_][A-Za-z_0-9]*)", t)
              if m.group(2).lower() not in _UNIT]
    one, rng = set(), []
    for L in lists:
        for part in re.split(r"\s*[、,，]\s*", L):
            m = re.fullmatch(r"(\d{1,4})\s*[-–]\s*(\d{1,4})", part.strip())
            if m and 0 <= int(m.group(2)) - int(m.group(1)) <= 400:
                rng.append((int(m.group(1)), int(m.group(2))))
            elif part.strip().isdigit():
                one.add(int(part.strip()))
    return one, rng


# 扫描发回行和用户条目同一处时去重：只去同一类（用户条目的文字里提到这一类做法 / 替代品），别把不相干的发回吃掉
_KIND_WORDS = {"C1": ("particles", "粒子", "SR.element", "front=", "短划线", "2D 雪", "2D 碎", "碎片", "飘"),
               "C2": ("复制", "渲染图", "拼", "fx.cam", "_place", "压扁", "压窄", "snowflake"),
               "Z": ("放大", "zoom", "糊", "马赛克", "分辨率")}


def _same_kind(row, it):
    words = _KIND_WORDS.get(row.get("rule"))
    if not words:
        return True
    txt = " ".join(str(it.get(k) or "") for k in ("rule", "where", "fix"))
    return any(w in txt for w in words)


def notes_review(c, notes, r, mode, stamp):
    """合成 review_<r>.json：targeted = 用户条目 + 劣质特效扫描发回行 + 原审片里机器量的交付硬门禁条目（没列的镜头判过、会被冻结）；
    append = 原审片每镜问题单最前面插用户条目和扫描发回行（redo_fixed 的镜头只发这两样）。"""
    sl = c.j("shotlist.json")
    frozen = c.j("freeze.json", {})
    cut = c.p("film", f"cut_r{r - 1}.mp4")
    fdir = c.p("frames", f"r{r}")
    os.makedirs(fdir, exist_ok=True)
    base = c.j(f"_old/{stamp}/review_{r}.json")                     # 存档的原审片（append 也先读它：合并后的 review_<r>.json 已经
    if base is None and mode == "append":                           # 写出、断在落盘前时续跑不会把用户条目再插一遍）
        base = c.j(f"review_{r}.json")
    if mode == "append" and base is None:
        gfile = c.p("gates_post_production.json" if r == 1 else f"gates_r{r - 1}.json")
        c.log(f"  看片意见轮（append）：第 {r} 轮还没审过，先正常审一轮再追加", echo=True)
        base = review_round(c, r, cut, gfile)
        os.makedirs(c.p("_old", stamp), exist_ok=True)
        util.write_json(c.p("_old", stamp, f"review_{r}.json"), base)
    nsh = notes.get("shots") or {}
    redo = set(notes.get("redo_fixed") or [])
    said = notes.get("said") or []
    shots, n_fx = {}, 0
    for s in sl["shots"]:
        sid = s["id"]
        if sid in frozen:
            if sid in nsh:
                c.log(f"  注意：你点名的 {sid} 是冻结镜头（notes 的 unfreeze 没列它），这一轮不改", echo=True)
            continue
        items = nsh.get(sid) or []
        res, send, _ = fx_scan(c, s, c.p("shots", sid, "work", "out", "shot.py"))
        def covered(x):                                             # 用户条目已经点名的代码行（同一类做法），扫描那一行不再重复列
            for it in items:
                o_, r_ = _code_lines(it.get("where"))
                if (x["line"] in o_ or any(a <= x["line"] <= b for a, b in r_)) and _same_kind(x, it):
                    return True
            return False
        send = [x for x in send if not covered(x)]
        fx_iss = [{"rule": f"劣质特效（编排器查代码）· {CF.RULE.get(x['rule'], x['rule'])}",
                   "where": f"第 {x['line']} 行 `{x.get('code', '')}`：{x['what']}", "fix": x["fix"]} for x in send]
        notes_iss = []
        for it in items:
            fr = it.get("frames")
            at = f"第 {fr[0]}–{fr[-1]} 帧" if isinstance(fr, list) and fr else ""
            bx = f"（框 {it['box']}）" if it.get("box") else ""
            rule = str(it.get("rule") or "用户 · 劣质特效")
            notes_iss.append({"rule": rule if rule.startswith("用户") else "用户 · " + rule,
                            "where": "；".join(x for x in (at + bx, str(it.get("where") or "")) if x), "fix": it["fix"]})
        old = ((base or {}).get("shots") or {}).get(sid) or {}
        if mode == "targeted":
            if sid not in nsh:
                shots[sid] = {"id": sid, "pass": True, "score": old.get("score"), "issues": [], "note": "看片意见轮没点名：冻结"}
                continue
            keep = [i for i in old.get("issues") or [] if any(k in str(i.get("rule")) for k in HARD_KEEP)]
            iss = notes_iss + fx_iss + keep
        else:
            iss = notes_iss + fx_iss + ([] if sid in redo else list(old.get("issues") or []))
        n_fx += len(fx_iss)                                         # 只数进了问题单的（冻结的镜头不算）
        shots[sid] = dict(old, id=sid, issues=iss)
        shots[sid]["pass"] = not iss and bool(old.get("pass", True))
        if items:
            notes_crops(c, s, items, cut, fdir, (res or {}).get("coords") or ())
    for sid, v in shots.items():                                    # 判过、下一步要冻结的镜头：work/out 里如果是返修半截的代码，
        if v.get("pass"):                                           # 换回出画的那份（不然冻结比 code_sha 必然对不上，硬停）
            sync_work_code(c, sid, "看片意见轮判过、要冻结", stamp)
    rnotes = {"source": "notes", "mode": mode, "said": said, "file": NOTES_FILE, "stamp": stamp}
    if mode == "targeted":
        rv = {"round": r, "source": "notes", "notes": rnotes, "cut": os.path.relpath(cut, c.ep), "frozen_before": sorted(frozen),
              "shots": shots, "cuts": [],
              "global": {"issues": [], "verdict": "看片意见轮（编排器按看片意见文件合成，不是审片模型打的分）", "anchor_score": None,
                         "anchor_votes": [], "anchor_why": "看片意见轮"},
              "motion": (base or {}).get("motion") or {}, "sync": (base or {}).get("sync"),
              "motion_failed": [], "floor_failed": [], "mustdo_failed": [],
              "sync_failed": sorted(sid for sid, v in shots.items() if any("卡点" in str(i.get("rule")) and "交付硬门禁" in str(i.get("rule"))
                                                                           for i in v["issues"]))}
    else:
        rv = dict(base or {}, round=r, source="notes+review", rnotes=rnotes, shots=shots)
        rv.setdefault("global", {"issues": [], "anchor_score": None})
    rv["failed"] = sorted(k for k, v in shots.items() if not v["pass"])
    rv["passed"] = sorted(k for k, v in shots.items() if v["pass"])
    util.write_json(c.p(f"review_{r}.json"), rv)
    c.log(f"看片意见轮（第 {r} 轮，{mode}）：合成问题单 review_{r}.json，不过 {rv['failed']}（用户条目 "
          f"{sum(len(nsh.get(k) or []) for k in rv['failed'])} 条、劣质特效扫描 {n_fx} 条），通过 / 冻结 {rv['passed']}", echo=True)
    return rv


def notes_round(c):
    """s4_review_loop 开头：有没消费的 看片意见就做看片意见轮（每步做完记进 <集>/notes_round.json，断了续跑接着做），
    返回用户轮的轮号；这一集没有 / 已经做完过返回那一轮的轮号或 None（做完过的给返修轮数算起点用）。"""
    notes, sha = notes_pending(c)
    prog = c.j(NOTES_PROG) or {}
    if not notes:
        return prog.get("round") if prog.get("done") and prog.get("sha") == sha else None
    if prog.get("sha") != sha:
        prog = {"sha": sha, "started": util.iso(), "stamp": util.now().strftime("%Y%m%d-%H%M%S"), "steps": {}}
    prog.setdefault("round", notes_round_no(c))
    r, stamp, steps = prog["round"], prog["stamp"], prog.setdefault("steps", {})
    mode = notes.get("mode", "append")
    save = lambda: util.write_json(c.p(NOTES_PROG), prog)  # noqa: E731
    save()
    ids = {s["id"] for s in c.j("shotlist.json")["shots"]}
    unk = sorted(set(notes.get("shots") or {}) - ids)
    c.log(f"看片意见轮：第 {r} 轮（{mode}），点名 {sorted(set(notes.get('shots') or {}) & ids)}"
          + (f"；不在镜头表里的 {unk} 不管" if unk else "") + (f"；恢复一稿 {sorted(notes.get('restore') or {})}" if notes.get("restore") else ""),
          echo=True)
    if not steps.get("inputs"):
        inputs.ensure(c, c.j("topic.json"))                         # 素材清单补上来源类型（CG / 烧录字幕）
        steps["inputs"] = True
        save()
    if not steps.get("archive"):
        prog["archived"] = notes_archive(c, notes, r, stamp, mode)
        steps["archive"] = True
        save()
    if notes.get("restore") and not steps.get("restore"):
        notes_restore(c, notes, r, prog, save)                        # 用这一集现有的（旧）引擎副本：必须在升级引擎之前
        steps["restore"] = True
        save()
    if notes.get("upgrade_engine", True) and not steps.get("engine"):
        notes_engine(c, stamp)
        steps["engine"] = True
        save()
    if not steps.get("unfreeze"):
        fz = c.j("freeze.json", {}) or {}
        un = [x for x in notes.get("unfreeze") or [] if x in fz]
        for sid in un:
            fz.pop(sid)
        if un:
            util.write_json(c.p("freeze.json"), fz)
            c.log(f"  看片意见轮：解冻 {un}（从 freeze.json 删掉；这几镜这一轮重修）", echo=True)
        prog["unfrozen"] = un
        steps["unfreeze"] = True
        save()
    if not steps.get("review"):
        notes_review(c, notes, r, mode, stamp)
        steps["review"] = True
        save()
    prog["done"], prog["at"] = True, util.iso()
    save()
    st = _state(c)
    rec = st.setdefault("notes_rounds", {}).setdefault(sha, {})
    rec.update(status="done", round=r, done_at=util.iso())
    if isinstance(getattr(c, "state", None), dict):
        util.write_json(c.p("state.json"), st)                      # 和 run_episode 手里是同一个 dict：马上落盘，断了续跑不再消费
    return r


def s4_review_loop(c):
    max_fix = c.args.max_rounds
    line = (c.series.get("review") or {}).get("deliver_at", 6)
    max_failed = int((c.series.get("review") or {}).get("max_failed", (c.cfg.get("rounds") or {}).get("review_max_failed", 3)))
    ir = notes_round(c)                                                # 看片意见轮（--notes）：合成 review_<ir>.json，返修轮数从这一轮起算
    if ir:
        max_fix = max(max_fix, ir - 1 + max_fix)
    r, scores = 1, {}
    while True:
        cut = c.p("film", f"cut_r{r - 1}.mp4")
        gfile = c.p("gates_post_production.json" if r == 1 else f"gates_r{r - 1}.json")
        rv = c.j(f"review_{r}.json") or review_round(c, r, cut, gfile)
        g = rv.get("global") or {}
        scores[str(r)] = g.get("anchor_score")
        why = None if (r == ir and str(rv.get("source") or "").startswith("notes")) else loop_stop(rv, r, max_fix, line, max_failed)
        if why:
            c.log(why)
            break
        fx = c.j(f"fix_{r}.json")
        if fx and (fx["freeze_check"]["status"] != "ok" or fx["frozen_files_changed"]):
            raise util.Stop(5, "frozen_changed", f"第 {r} 轮返修时冻结镜头被改动（fix_{r}.json）；查清后用 --from 4 重做审片")
        if not (fx or {}).get("cut"):
            fix_round(c, r, rv)
        r += 1
    util.write_json(c.p("loop.json"), {"last_review": r, "cut": f"film/cut_r{r - 1}.mp4",
                                       "unresolved": rv["failed"], "motion_failed": rv.get("motion_failed") or [],
                                       "floor_failed": rv.get("floor_failed") or [], "mustdo_failed": rv.get("mustdo_failed") or [],
                                       "sync_failed": rv.get("sync_failed") or [], "sync": rv.get("sync"),
                                       "rounds_fixed": r - 1, "stop_reason": why,
                                       "anchor_score": g.get("anchor_score"), "anchor_votes": g.get("anchor_votes"),
                                       "anchor_why": g.get("anchor_why"), "deliver_at": line, "max_failed": max_failed,
                                       "scores": scores})


# ============================================================================ 6 声音
def s6_sound(c):
    bm, loop = c.j("beatmap.json"), c.j("loop.json")
    cut = c.p(loop["cut"])
    music = c.j("inputs/music.json")
    os.makedirs(c.p("audio"), exist_ok=True)
    if not music:
        if not c.mock:
            raise util.Stop(4, "blocked", f"需要配乐：{c.series['music']['need']}；BPM 要等于 {bm['bpm']}。"
                                         f"把文件路径、bpm、第一个强拍秒数写进 {c.p('inputs', 'music.json')} 后续跑（格式见 README）。")
        wav = c.p("audio", "mock_click.wav")
        media.click_track(wav, bm["bpm"], bm["total_f"] / 60 + 0.5, 0.0, log_path=c.p("logs", "ffmpeg.log"))
        music = {"file": wav, "bpm": bm["bpm"], "downbeat_s": 0.0, "start_s": 0.0, "title": "mock 节拍器"}
    path = music["file"] if os.path.isabs(music["file"]) else c.p(music["file"])
    start = float(music.get("start_s", music["downbeat_s"]))
    beat_s = 60.0 / music["bpm"]
    checks = []
    off = (start - music["downbeat_s"]) / beat_s
    checks.append(G._chk("m_start", "第 0 帧落在配乐的拍上", "ok" if abs(off - round(off)) * bm["beat_f"] <= 1 else "warn",
                         f"偏 {abs(off - round(off)) * bm['beat_f']:.2f} 帧", "≤1 帧", rule="编排器"))
    checks.append(G._chk("m_bpm", "配乐 BPM = 节拍表", "ok" if abs(music["bpm"] - bm["bpm"]) <= 0.05 else "fail",
                         music["bpm"], bm["bpm"], rule="全局 9"))
    fin = next(h for h in bm["hits"] if h["kind"] == "final")
    mm = c.mm() if c.j("inputs/music.json") else None
    heavy = [e for e in mm_slots(mm).values() if has_onset(e) and e.get("tier") in ("S", "A")] if mm else []
    stopF = [int(x["F"]) for x in (mm or {}).get("stops") or []]
    land = lambda F: next((e for e in heavy if abs(int(e["F"]) - F) <= 1), None)  # noqa: E731   大卡点 ±1 帧内的 A 档以上起音
    if mm:                                                            # final = 窗口里最后一个重音（不必在拍上），按实测起音核
        e = land(fin["F"])
        checks.append(G._chk("m_final", "最后一击落在原曲的重音上（实测起音）", "ok" if e else "warn",
                             f"第 {fin['F']} 帧" + (f" = {slot_pos(e)} {e.get('tier')} 档" if e else "：±1 帧内没有 A 档以上起音"),
                             "±1 帧内有 S / A 档起音", rule="CHROMA·参数·结尾"))
    else:
        k = (start + fin["F"] / 60.0 - music["downbeat_s"]) / beat_s
        checks.append(G._chk("m_final", "最后一击落在原曲自己的拍上", "ok" if abs(k - round(k)) * bm["beat_f"] <= 1 else "warn",
                             f"偏 {abs(k - round(k)) * bm['beat_f']:.2f} 帧", "≤1 帧", rule="CHROMA·参数·结尾"))
    dur_a = None
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
                           capture_output=True, text=True, env=media.ENV, timeout=60)
        dur_a = float(r.stdout.strip())
    except Exception:  # noqa: BLE001
        pass
    need = start + bm["total_f"] / 60.0
    checks.append(G._chk("m_length", "配乐够长（一首顺到底，不拼接）", "ok" if dur_a and dur_a >= need - 0.01 else "fail",
                         dur_a, f"≥{need:.2f}s", rule="全局 9"))
    out = c.p("film", "with_audio.mp4")
    if not any(x["status"] == "fail" for x in checks):
        media.mux(cut, path, out, audio_start=start, log_path=c.p("logs", "ffmpeg.log"), loud=media.LOUD)
    events = []
    for h in bm["hits"]:
        if h["kind"] not in ("big", "drop", "final"):
            continue
        ev = {"F": h["F"], "bar": h["bar"], "beat": h["beat"], "sub": h.get("sub", 0), "hit": h["kind"], "why": h.get("what", "")}
        if mm:                                                        # 实测（music_map）：±1 帧内有 A 档以上起音才算曲子重音
            e = land(h["F"])
            ev.update(kind="曲子重音" if e else ("曲子急停" if any(abs(F - h["F"]) <= 1 for F in stopF) else "无声"),
                      measured=({"F": int(e["F"]), "attack_f": e.get("attack_f"), "tier": e.get("tier"), "rank": e.get("rank"),
                                 "band": e.get("band")} if e else None))
        else:
            ev.update(kind="曲子重音", measured=None)                 # 没有 music_map：照旧按节拍表抄（没实测，sound.json 的 measured = false）
        events.append(ev)
    silent = [e["F"] for e in events if e["kind"] == "无声"]
    util.write_json(c.p("sound.json"), {"music": {k_: v for k_, v in music.items()}, "start_s": start, "checks": checks,
                                        "events": events, "loudnorm": media.LOUD, "measured": bool(mm), "silent_hits": silent,
                                        "events_note": "声音表（根「配乐」必做②）：" + music_only(c) + "，不另加音效。"
                                                       + ("每个大卡点按实测音乐（music_map）核：±1 帧内有 A 档以上起音 = 曲子重音，"
                                                          "急停那一格 = 曲子急停，都没有 = 无声。" if mm else "没有 music_map：按节拍表抄，没实测。")
                                                       + "程序对拍，未人耳试听；片尾 0.25 s 淡出（循环接回第 0 帧不硬切）。",
                                        "out": os.path.relpath(out, c.ep) if os.path.exists(out) else None})
    if silent:
        c.log(f"  声音表：大卡点 {silent} ±1 帧内没有曲子重音（实测），记「无声」", echo=False)
    c.log(f"声音：{music.get('title', os.path.basename(path))}，{G.fmt(G.summarize(checks))}")
    if any(x["status"] == "fail" for x in checks):
        raise util.Stop(4, "blocked", f"配乐不合格：{[x['name'] for x in checks if x['status'] == 'fail']}（见 sound.json）")


# ============================================================================ 7 交付
def s7_deliver(c):
    topic, bm, sl = c.j("topic.json"), c.j("beatmap.json"), c.j("shotlist.json")
    final = c.p("film", "with_audio.mp4")
    checks, m = video_checks(c, final, bm, sl, final=True)
    frozen = c.j("freeze.json", {})
    if frozen:
        checks.append(G.freeze_checks(media.framemd5(final), frozen))
    checks += c.j("sound.json")["checks"]
    tree_save(c, "制作", code_rows(c))
    tree_save(c, "交付", deliver_rows(c))
    led = tree_save(c, "成片", TR.video_rows(c.tree, c.cat(), checks, "成片（交付）"))
    st = (util.read_json(c.p("gates_tree.json")) or {}).get("stages") or {}
    must = [r for k, rows in st.items() if k != "成片" for r in rows if r["status"] == "fail"]
    checks.append(G._chk("tree_must", "规则树：必做、计划 / 代码上的底线", "fail" if must else "ok", f"{len(must)} 条没做到", 0,
                         [TR.say(r) for r in must], "规则树 v1.1 三档（gates_tree.json）"))
    c.log(f"规则树（交付）：{TR.summary_line(led)[:220]}")
    util.write_json(c.p("gates_final.json"), {"video": "film/with_audio.mp4", "metrics": m, "summary": G.summarize(checks), "checks": checks})
    fails = [x for x in checks if x["status"] == "fail"]
    hard = [x for x in fails if x["id"] in G.ENGINEERING]             # 只有工程底线挡出片（见 gates.ENGINEERING）
    soft = [x for x in fails if x["id"] not in G.ENGINEERING]         # 质量项：返修循环里已经判过、发回过，这里只报告
    if soft:
        c.log(f"交付时没过的质量项（只报告，不挡出片）：{[x['name'] for x in soft]}")
    dst = None
    if not hard:
        name = "".join(ch if ch.isalnum() else "_" for ch in topic["screen_title"]).strip("_")
        vm = re.search(r"-v(\d+)", os.path.basename(c.ep))
        dst = c.p("deliver", f"{deliver_prefix(c.series)}{topic['episode']:02d}_{name}_v{vm.group(1) if vm else 1}{'_MOCK' if c.mock else ''}.mp4")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(final, dst)
    util.write(c.p("report.md"), report(c, checks, m, dst))
    c.log(f"交付：{G.fmt(G.summarize(checks))}；{'成片 ' + os.path.relpath(dst, c.ep) if dst else '硬门禁不过，没出成片'}")
    if hard:
        raise util.Stop(5, "hard_gate", f"交付前硬门禁不过：{[x['name'] for x in hard]}（见 gates_final.json）")


SOUND_LAND = ("曲子重音", "曲子急停")                                # 声音表里算「声音落点」的 kind（「无声」不算）


def deliver_prefix(series):
    """交付文件名前缀：系列 deliver_prefix，没写用系列 id 大写。"""
    p = str((series or {}).get("deliver_prefix") or (series or {}).get("id") or "CHROMA")
    return re.sub(r"[^A-Za-z0-9]", "", p).upper() or "EP"


def music_only(c, long=False):
    """声音表、交付规则行里「只用音乐」的说法：系列 music.only；没写照 CHROMA 原话。"""
    say = ((c.series.get("music") or {}).get("only") or "").strip()
    if say:
        return say if long else say.split("：")[0]
    return "CHROMA 只用音乐：大卡点的声音落点是曲子的重音（或急停）" if long else "CHROMA 只用音乐"


def deliver_rows(c):
    """规则树交付环节查的必做：配乐来源和授权、声音表、未人耳试听、5 秒样片、接缝（交接）、四遍验收、生图提示词。"""
    t, st = c.tree, "交付"
    music, snd = c.j("inputs/music.json") or {}, c.j("sound.json") or {}
    bm, sl, smp = c.j("beatmap.json") or {}, c.j("shotlist.json") or {}, c.j("sample.json") or {}
    rows = []
    if music:
        miss = [k for k in ("license",) if not str(music.get(k) or "").strip()] + ([] if (music.get("source") or music.get("credit")) else ["source"])
        rows.append(TR.row(t, "root.audio.do1", not miss, [f"inputs/music.json 缺 {miss}"] if miss else [], music.get("title"),
                           "配乐是一首现成曲子的文件，写来源和授权（inputs/music.json）", st))
    else:
        rows.append(TR.row(t, "root.audio.do1", None if c.mock else False, ["没有配乐（mock 用节拍器）"], how="下歌：配乐文件 + 来源 + 授权", stage=st))
    big = {h["F"] for h in bm.get("hits") or [] if h["kind"] in ("big", "drop", "final")}
    got = {e["F"] for e in snd.get("events") or [] if e.get("kind") in SOUND_LAND}     # 实测（music_map），「无声」不算落点
    rows.append(TR.row(t, "root.audio.do2", bool(big) and big <= got, [f"大卡点 {sorted(big - got)[:6]} 没有声音落点（实测 ±1 帧内没有曲子重音）"]
                       if big - got else [], f"{len(got & big)}/{len(big)}",
                       "声音表：每个大卡点有声音落点（sound.json events，" + ("按 music_map 实测" if snd.get("measured") else "没实测，按节拍表")
                       + "）", st))
    rows.append(TR.row(t, "root.audio.do3", True, value="未人耳试听", how="交付报告写明「未人耳试听」（report.md 固定写）", stage=st))
    if music:
        synth = "合成" in str(music.get("kind") or "") or "synth" in str(music.get("kind") or "")
        rows.append(TR.row(t, "root.audio.param1", (not synth) or bool(music.get("master") and music.get("why")), [music.get("title") or ""],
                           music.get("kind") or "下载的曲子", "代码合成配乐只在有验证过的母版时用，写理由", st))
    if "beat.param8" in t["_idx"] and snd.get("events"):
        k = {e.get("kind") for e in snd["events"]}
        rows.append(TR.row(t, "beat.param8", k <= set(SOUND_LAND), [f"声音表里有 {sorted(k - set(SOUND_LAND))}"
                                                                  + (f"：第 {snd.get('silent_hits')} 帧" if snd.get("silent_hits") else "")], sorted(k),
                           music_only(c, True) + ("，按 music_map 实测" if snd.get("measured") else ""), st))
    if smp.get("skipped") == "short":                               # 试跑短片模式：第 2 环节按设计跳过，不算没做到
        rows.append(TR.row(t, "root.flow.2", None, ["试跑短片模式（--short）：不做 5 秒样片，直接全片"], "跳过（短片）",
                           "先做 5 秒动态样片，审过再做全片", st))
    else:
        okm = bool(smp) and os.path.exists(c.p(smp.get("video") or "film/sample.mp4"))
        rows.append(TR.row(t, "root.flow.2", okm, [] if okm else ["没有 film/sample.mp4"], smp.get("pass"), "先做 5 秒动态样片，审过再做全片", st))
    hand = [s["id"] for s in sl.get("shots") or [] if not (norm_tx(s.get("transition_in")).get("handoff"))]
    rows.append(TR.row(t, "root.flow.3", bool(sl.get("shots")) and not hand, [f"{x} 没写交接" for x in hand],
                       how="接缝表：每个切点的交接画面（shotlist 的 transition_in.handoff，两镜各做自己那一半）", stage=st))
    rows.append(TR.row(t, "root.flow.4", True, value="机器版", how="交付前四遍验收（report.md「四遍验收」一节：正常速度、逐帧接缝、静音、只听声音）", stage=st))
    gens = (sl.get("alloc") or {}).get("gen_requests") or []
    gcalls = (c.j("inputs/gen_images.json") or {}).get("calls") or []          # 生图通道真生成过的（任务书里有没有这条）
    if gcalls:
        bad = [g["shot"] for g in gens if "麻麻赖赖" not in g.get("gen", "")] + [x["shot"] for x in gcalls if not x.get("rule_ok")]
        rows.append(TR.row(t, "root.quality.do2", not bad, bad, f"{len(gens)} 条计划 + {len(gcalls)} 次生图",
                           "生图提示词里写「不要麻麻赖赖」（编排器补上；生图通道的任务书里固定有）", st))
    else:
        rows.append(TR.row(t, "root.quality.do2", (all("麻麻赖赖" in g.get("gen", "") for g in gens)) if gens else None,
                           [g["shot"] for g in gens if "麻麻赖赖" not in g.get("gen", "")], len(gens), "生图提示词里写「不要麻麻赖赖」（编排器补上）", st))
    fj = c.j("inputs/footage.json", {"clips": []}) or {}
    nolic = [x["name"] for x in fj.get("clips", []) if not str(x.get("license") or "").strip()]
    rows.append(TR.row(t, "root.assets.do2", not nolic, [f"{', '.join(nolic[:8])} 没写授权"] if nolic else [], len(fj.get("clips", [])),
                       "素材清单每项写来源和授权（inputs/footage.json；授权缺了只报警）", st, hard=False))
    return rows


def shot_accept_line(c, sl):
    """审查 R7）。"""
    out = []
    for s in sl["shots"]:
        d = c.j(f"shots/{s['id']}/done.json") or {}
        mo, sy, bits = d.get("motion") or {}, d.get("sync") or {}, []
        if mo.get("measured"):
            bad = [w for w, b in (("没有进展", mo.get("ok") is False), ("原地摆动", mo.get("sway_ok") is False), ("静图 + 推拉", mo.get("pan")))
                   if b]
            bits.append(f"运动量 {mo.get('eff'):g}" + (f"（收下时还没过：{'、'.join(bad)}）" if bad else "")
                        + (f"，发回 {mo['sent_back']} 次" if mo.get("sent_back") else ""))
        elif mo.get("warn"):
            bits.append("运动量没量")
        if sy.get("sent_back") or sy.get("ok") is False:
            bits.append(f"卡点{'没过' if sy.get('ok') is False else '过了'}，发回 {sy.get('sent_back', 0)} 次")
        fx = d.get("cheapfx") or {}
        if fx.get("send") or fx.get("sent_back"):
            bits.append(f"劣质特效（查代码）{'还有 ' + str(len(fx['send'])) + ' 处' if fx.get('send') else '已改掉'}"
                        + (f"（{'、'.join(sorted({x['rule'] for x in fx['send']}))}）" if fx.get("send") else "")
                        + (f"，发回 {fx['sent_back']} 次" if fx.get("sent_back") else ""))
        if bits:
            out.append(f"{s['id']} " + "，".join(bits))
    return "；".join(out) or "没记"


def notes_line(c):
    """报告里「看片意见轮」一行（做过才有）。"""
    pr = c.j(NOTES_PROG) or {}
    if not pr.get("done"):
        return None
    n = c.j(NOTES_FILE) or {}
    rs = pr.get("restore") or {}
    rl = "；".join((f"{k} 换回一稿代码（没重渲）" if v.get("rerender") is False else
                   f"{k} 恢复一稿重渲" + ("、逐帧和 cut_r0 一样" if v.get("md5_same") else
                                       (f"、和 cut_r0 PSNR 中位 {v['psnr']['median']:g} dB（{'一致' if v.get('same_look') else '不一致，要看'}）"
                                        if v.get("psnr") else "")))
                  for k, v in sorted(rs.items()))
    return (f"| 看片意见轮 | 第 {pr.get('round')} 轮（{n.get('mode', '?')}），点名 {', '.join(sorted(n.get('shots') or {})) or '无'}"
            + (f"；{rl}" if rl else "") + (f"；解冻 {', '.join(pr.get('unfrozen') or [])}" if pr.get("unfrozen") else "")
            + f"；存档在 _old/{pr.get('stamp')}/；原话：{'／'.join(n.get('said') or [])[:160]} |")


def report(c, checks, m, dst):
    topic, bm, sl = c.j("topic.json"), c.j("beatmap.json"), c.j("shotlist.json")
    loop, cost = c.j("loop.json"), c.j("cost.json")
    music, fj = c.j("inputs/music.json") or {}, c.j("inputs/footage.json", {"clips": []})
    state = c.j("state.json", {}) or {}
    reviews = sorted(f for f in os.listdir(c.ep) if f.startswith("review_") and f.endswith(".json"))
    rv_lines = []
    for f in reviews:
        d = c.j(f)
        rv_lines.append(f"第 {d['round']} 轮不过 {', '.join(d['failed']) or '无'}（全片意见 {len(d['global']['issues'])} 条）")
    plan = c.j("gates_plan.json")["checks"]
    pc = next(x for x in plan if x["id"] == "p_cut_density")["value"]
    warns = [x for x in checks if x["status"] in ("warn", "fail")] + [dict(x, name="计划 · " + x["name"]) for x in plan if x["status"] == "warn"]
    score, line = loop.get("anchor_score"), loop.get("deliver_at", 6)
    sc = "?" if score is None else f"{score:g}"
    last = c.j(f"review_{loop['last_review']}.json") or {}
    cuts = last.get("cuts") or []
    hc = next((x for x in plan if x["id"] == "p_hard_cut"), None)
    names = {x["name"] for x in fj.get("clips", [])}
    used = sorted({n for s in sl["shots"] for n in re.findall(r"['\"](F\d+)['\"]", _read_or(c.p("shots", s["id"], "work", "out", "shot.py")))
                   if n in names})
    walls = {k: v.get("wall_s") for k, v in (state.get("stages") or {}).items() if v.get("wall_s") is not None}
    smp, al = c.j("sample.json") or {}, sl.get("alloc") or {}
    snd_silent = (c.j("sound.json") or {}).get("silent_hits") or []
    mo = (last.get("motion") or {}).get("all") or {}
    misses = {s["id"]: (c.j(f"shots/{s['id']}/done.json") or {}).get("must_do_misses") for s in sl["shots"]}
    misses = {k: v for k, v in misses.items() if v}
    votes = loop.get("anchor_votes") or []
    tled = c.j("gates_tree.json") or {}
    env = topic.get("env") or {}
    st = "被阻塞（硬门禁不过）" if not dst else ("已交付（mock：模型回复是假的，画面是机械拼的，只证明流水线通了）" if c.mock
                                          else "已交付，待你验收")
    lines = [
        f"内部评分 {sc}/10（10 = 本系列定稿片的水平、2 = 基线）", "",
        f"# 「{topic['title_cn']}」交付报告（{c.series['name']} 第 {topic['episode']} 集{'' if topic.get('片名状态') == '用户起的' else '，暂定片名'}）", "",
        f"**状态：{st}**　成片：`{os.path.relpath(dst, c.ep) if dst else '无'}`", "",
        "| 项 | 内容 |", "|---|---|",
        f"| 大类（规则树 {topic.get('tree') or '?'}） | {topic.get('category') or '?'}：{topic.get('category_why') or '没写理由'}；"
        f"和上一集、同类最火的那条哪里不同：{topic.get('differs') or '没写'} |",
        f"| 母题 / 屏上片名 | {topic['motif']} / {topic['screen_title']} |",
        f"| 签名色 / 主角物 | {topic['signature_color']['name']} {topic['signature_color']['hex']} / {topic['hero_object']} |",
        f"| 数字阶梯（待核实） | {' → '.join(x['value'] + ' ' + x['unit'] for x in topic['number_ladder'])} |",
        f"| 配乐 | {music.get('title', '无')}，{bm['bpm']} BPM，原曲 {music.get('start_s', 0):.2f}s 起"
        + ("（编排器自动挑的，暂定）" if music.get("auto") else "") + " |",
        f"| 节拍 | {bm['bars']} 小节 = {bm['total_f'] / 60:.2f} s，{len(sl['shots'])} 镜"
        + (f"（试跑短片：原曲窗口 {music.get('bars_full')} 小节里的前 {bm['bars']} 小节，DROP 在第 {music.get('drop_bar')} 小节）"
           if music.get("short") else "") + "；卡点帧号" + ("按实测起音（music_map）" if bm.get("frames_from") else "按格点") + " |",
        f"| 实拍 | 素材库 {len(fj.get('clips', []))} 条，片里用了 {', '.join(used) or '无'} |",
        f"| 切点 / 秒 | 计划 {pc}，实测 {m.get('cuts_per_s')}（Light 5.30、点火 3.79；1.4 以下明显不行） |",
        f"| 重击 / 秒 | {m.get('hits_per_s')}（Light 5.26、点火 4.61） |",
        f"| 门禁 | {G.fmt(G.summarize(checks))} |",
        f"| 审片 | {'；'.join(rv_lines)}；未解决 {', '.join(loop['unresolved']) or '无'} |",
        f"| 内部评分 | 各轮 {'、'.join(f'第 {k} 轮 {v:g}' for k, v in (loop.get('scores') or {}).items() if v is not None) or '无'}"
        + (f"（最后一轮 {len(votes)} 次独立审片 {' / '.join(f'{x:g}' for x in votes)} 取中位数）" if votes else "")
        + f"；到 {line} 分且没过的镜头 ≤{loop.get('max_failed', 3)} 个才交你；{loop.get('stop_reason') or ''} |",
        f"| 5 秒样片 | " + ("试跑短片模式：跳过（不做 5 秒样片和静帧代表帧，直接全片）" if smp.get("skipped") else
                          f"第 {smp['window'][0]}–{smp['window'][1]} 帧（{smp['seconds'][0]}–{smp['seconds'][1]} s），镜头 "
                          f"{', '.join(smp['shots'])}；{'过了' if smp.get('pass') else '没全过：' + ', '.join(smp.get('unresolved') or [])}"
                          f"（{len(smp.get('rounds') or [])} 轮）；{motion_line('镜内运动量', smp.get('motion'))}；`film/sample.mp4`"
                          if smp else "无") + " |",
        f"| 镜内运动量（成片） | {motion_line('全片', mo) if mo else '没量'}（{motion_ref(motion_cfg(c))}）；"
        + "、".join(f"{x['name']} {x['status']}（{x['value']}）" for x in checks if x.get("id") in ("motion_eff", "motion_sway", "motion_content"))
        + (f"；最后一轮没有进展 / 原地摆动的 {', '.join(loop.get('motion_failed') or [])}" if loop.get("motion_failed") else "") + " |",
        f"| 卡点（实测） | " + ("、".join(f"{x['name']} {x['status']}（{x['value']}）" for x in checks if str(x.get("id", "")).startswith("sync_"))
                             or "没量（没有 music_map 或引擎 Python）")
        + (f"；最后一轮卡点没卡上的 {', '.join(loop.get('sync_failed') or [])}" if loop.get("sync_failed") else "")
        + (f"；声音表里「无声」的大卡点 {snd_silent}" if snd_silent else "") + " |",
        f"| 全片分配 | 数字 {sum(1 for x in al.get('numbers') or [] if x.get('shot'))}/{len(al.get('numbers') or [])} 站按顺序分给 "
        + "、".join(f"{x['shot']} {x['text']}" for x in al.get("numbers") or [] if x.get("shot"))
        + f"；大词 {sum(len(v) for v in (al.get('words') or {}).values())} 个不重复；生图待做 {len(al.get('gen_requests') or [])} 张 |"
        if al else "| 全片分配 | 无（旧镜头表） |",
        f"| 逐镜收下时 | {shot_accept_line(c, sl)} |",
        *([notes_line(c)] if notes_line(c) else []),
        f"| 必做动作 | " + ("；".join(f"{k}：{'；'.join(v)[:120]}" for k, v in misses.items()) if misses else
                          "查代码都做到了（主体素材、≥2 个动效零件、只用分到的字、切点调了声明的转场函数、运动模糊、纯函数、大卡点有冲击簇）") + " |",
        f"| 转场 | 镜头之间拍上硬切 {hc['value'] if hc else '?'}；最后一轮切点核对 {len(cuts)} 个，建议改 "
        f"{', '.join(x['prev'] + '→' + x['cut'] for x in cuts if not x.get('ok')) or '无'}（只作建议） |",
        f"| 规则树 | {TR.summary_line(tled) if tled else '没记'}（每条明细 `gates_tree.json`） |",
        (f"| 费用 | ${cost['total_usd']:.4f}{'（mock 估算，没花钱）' if c.mock else ''}；"
         + "，".join(f"{k} ${v['usd']:.4f}" for k, v in cost["by_stage"].items()) + " |") if not cost.get("billing") else
        (f"| 费用 | Codex（你的 ChatGPT 订阅额度，不按美元计{'；mock 估算' if c.mock else ''}）：token 未命中 {cost['tokens']['in_miss']:,} / 命中 "
         f"{cost['tokens']['in_hit']:,} / 输出 {cost['tokens']['out']:,}；" + "，".join(f"{k} {v['calls']} 次" for k, v in cost["by_stage"].items()) + " |"),
        *([review_cost_line(c)] if c.j("cost_review.json") else []),
        *([gen_line(c)] if (IG.enabled(c) or c.j("inputs/gen_images.json")) else []),
        *([SQ.report_line(c)] if (SQ.enabled(c) or c.j(SQ.REG) or c.j(SQ.INV)) else []),
        f"| 耗时 | 环节 0–6 合计 {sum(walls.values()) / 60:.1f} 分（" + "，".join(f"{k}：{v / 60:.1f}" for k, v in sorted(walls.items()))
        + "）；交付环节另计 |",
        ""]
    if score is None or score < line or len(loop["unresolved"]) > loop.get("max_failed", 3):
        lines += [f"## 卡在哪（内部评分 {sc}、没过的镜头 {len(loop['unresolved'])} 个，没到「{line} 分且 ≤{loop.get('max_failed', 3)} 个」，"
                  f"返修 {loop['rounds_fixed']} 轮后照样交付）", "",
                  f"- 审片模型：{loop.get('anchor_why') or '没给理由'}"]
        for sid in loop["unresolved"]:
            iss = (last.get("shots", {}).get(sid) or {}).get("issues") or []
            lines.append(f"- {sid} 没过：" + ("；".join(f"{i['where']} → {i['fix']}" for i in iss[:2]) or "没写原因"))
        lines += [f"- 全片：{i['where']} → {i['fix']}" for i in ((last.get("global") or {}).get("issues") or [])[:3]]
        lines += [f"- 切点 {x['prev']}→{x['cut']}：声明「{x.get('declared', '')}」，画面「{x.get('seen', '')}」" for x in cuts if not x.get("ok")][:4]
        lines.append("")
    lines += tools_md(c, sl)
    trows = tled.get("rows") or []
    bad_t = [r for r in trows if r["status"] == "fail"]
    dev_t = [r for r in trows if r["status"] in ("warn", "info") and r["tier"] == "默认"]
    lines += [f"## 规则树（{tled.get('tree', '?')}，大类「{tled.get('category_name', '?')}」：根 + 类型卡 + 系列卡）", "",
              f"- 体检：" + (f"读了 `{env.get('path')}`（{env.get('t')}）：生图 "
                            f"{env.get('hints', {}).get('image_gen')}、素材下载 {env.get('hints', {}).get('footage_download')}"
                            if env.get("read") else env.get("note", "没读到")),
              f"- {TR.summary_line(tled) if tled else '没记'}"]
    lines += [f"- 没做到（{r['tier']}·{r['dim']}，{r['id']}）：{r['how']}" + (f"：{'；'.join(r['where'][:3])}" if r["where"] else "") for r in bad_t[:8]]
    lines += [f"- 默认值偏离（{r['dim']}，{r['id']}）：{r['how']}" + (f"：{'；'.join(r['where'][:2])}" if r["where"] else "")
              + (f"。理由：{r['why']}" if r.get("why") else "。理由：没写（制作代理的偏离理由在 shots/<id>/work/out/NOTES.md）")
              for r in dev_t[:8]]
    lines += [f"- 人判 {sum(1 for r in trows if r['status'] == 'human')} 条交给审片看图判；未接 {sum(1 for r in trows if r['status'] == 'na')} 条（gates_tree.json 里写了为什么）", ""]
    gl = {x["id"]: x for x in checks}
    nr = len(c.j(f"review_{loop['last_review']}.json", {}).get("cuts") or [])
    lines += ["## 四遍验收（机器版，都看渲染出的 mp4）", "",
              f"- 正常速度：全片审片 {len(votes) or '?'} 次（各段联系表 + 第 1 秒 + 首尾），内部评分 {sc}/10",
              f"- 逐帧看接缝：最后一轮切点 F-2 / F / F+2 三帧核对 {nr} 个；像素检查 "
              + "、".join(f"{gl[k]['name']} {gl[k]['value']}" for k in ("cut_dip", "cut_blend", "cut_moving") if k in gl),
              "- 静音看：画面门禁 " + "、".join(f"{gl[k]['name']} {gl[k]['status']}" for k in ("motion_eff", "motion_sway", "motion_shot", "still", "grey",
                                                                                             "first_motion", "end_black") if k in gl),
              "- 只听声音：" + "、".join(f"{gl[k]['name']} {gl[k]['value']}" for k in ("loudness", "m_bpm", "m_length", "m_final", "sync_mux") if k in gl)
              + "；**配乐未人耳试听**（只做了程序对拍和响度标准化）", ""]
    lines += ["## 报警（只报位置，不判死）", ""]
    lines += [f"- {x['name']}：{x['value']}（要求 {x['want']}）" + (f"；{'；'.join((x.get('where') or [])[:4])}" if x.get("where") else "")
              for x in warns[:10]] or ["- 无"]
    lines += ["", "## 要你看 / 定", "",
              ("- 成片 `deliver/*.mp4`：只改点名的地方。" if dst else
               "- 没出交付成片（硬门禁挡了）：先看带配乐的 `film/with_audio.mp4`" + ("" if os.path.exists(c.p("film", "with_audio.mp4")) else "（这次也没生成）")
               + "，挡在哪见上面「不过」。")
              + ("试跑短片：没有 5 秒样片和各段代表帧，" if smp.get("skipped") else
                 "5 秒样片 `film/sample.mp4`（审片看过的帧 `frames/sample/`），各段代表帧（辅助）`frames/key_*.png`，")
              + "审片看过的帧在 `frames/r<轮>/`（每镜带切线图 kymo_*.jpg），全片分配表在 `bible.md` 末尾。",
              *([f"- 生图 {len(al['gen_requests'])} 张还没有图（这几镜先用了替身）：" + "；".join(f"{x['shot']}：{x['gen']}" for x in al["gen_requests"])]
                if al.get("gen_requests") else []),
              ("- 片名「" + topic["title_cn"] + "」是用户起的；" if topic.get("片名状态") == "用户起的" else "- 片名（现在是暂定）；")
              + "屏上数字是否属实（topic.json 的 number_ladder）。",
              "- 配乐" + ("是自动挑的：要换就改 `inputs/music.json` 后 `--from 1` 重做。" if music.get("auto") else "：用户指定。")
              + "未人耳试听：响度已标准化到 −14 LUFS，节拍是程序对的。",
              "- 署名：配乐按它的条款在简介里贴 credit（`inputs/music.json` 的 credit）；用到 CC BY 实拍要署名（`inputs/footage.json` 的 license）。", "",
              "文件：topic.json · bible.md · beatmap.json · shotlist.json · fonts.json · gates_tree.json（规则树每条一行）· keyframes.json · "
              "shots/<id>/（代码、每次运行、渲染）· frames/ · "
              "review_<轮>.json · fix_<轮>.json · freeze.json · gates_*.json · sound.json · cost.json · calls/（每次模型调用的原文）· logs/"]
    return "\n".join(lines) + "\n"


def review_cost_line(c):
    """报告里「审片（另记）」一行：审片模型和制作后端不一样时（review_llm），审片的账记在 cost_review.json。"""
    rc = c.j("cost_review.json") or {}
    n = sum(v.get("calls", 0) for v in (rc.get("by_stage") or {}).values())
    return (f"| 审片（另记） | {n} 次调用，" + ("不按美元计（Codex 订阅）" if rc.get("billing") else f"${rc.get('total_usd', 0):.4f}")
            + f"{'（mock 估算，没花钱）' if c.mock else ''}；cost_review.json |")


def tools_md(c, sl):
    """report.md「代理说缺的工具」一节：每镜 done.json 的 missing_tools（没有 done.json 就直接读 out/NOTES.md），
    同一句合并、记哪几镜提过。下一件工具照这张表定（≥2 部片提过才做）。"""
    by = {}
    for s in sl.get("shots") or []:
        d = c.j(f"shots/{s['id']}/done.json") or {}
        for t in d.get("missing_tools") if "missing_tools" in d else missing_tools(c.p("shots", s["id"], "work")):
            by.setdefault(t, []).append(s["id"])
    L = ["## 代理说缺的工具", ""]
    if not by:
        return L + ["- 没有（制作 / 返修代理在 out/NOTES.md 写「缺的工具：…」才会进这张表）", ""]
    L += ["| 缺什么（代理原话） | 哪几镜提过 |", "|---|---|"]
    L += [f"| {t.replace('|', '／')} | {', '.join(ids)} |" for t, ids in sorted(by.items(), key=lambda kv: (-len(kv[1]), kv[0]))]
    return L + [""]


def gen_line(c):
    """报告里「生图」一行（生图通道开过才有）。"""
    g = c.j("inputs/gen_images.json") or {}
    ok, bad, calls = g.get("clips") or [], g.get("failed") or [], g.get("calls") or []
    return (f"| 生图 | 通道 {IG.mode(c)}：{len(calls)} 次调用，成功 {len(ok)} 张、失败 {len(bad)} 张（失败只报告不拦）"
            + (f"；{', '.join(x['name'] for x in ok[:10])}" if ok else "") + "；清单 inputs/gen_images.json、各镜 work/assets/gen/ |")


def _read_or(p):
    return util.read(p) if os.path.exists(p) else ""


STAGES = [
    (0, "开题", s0_topic, ["topic.json"]),
    (1, "导演", s1_director, ["bible.md", "beatmap.json", "shotlist.json"]),
    (2, "5 秒样片（+ 各段代表帧辅助）", s2_sample, ["sample.json", "keyframes.json", "film/sample.mp4"]),
    (3, "制作", s3_production, ["film/cut_r0.mp4", "gates_post_production.json"]),
    (4, "审片 + 修复（5）", s4_review_loop, ["loop.json"]),
    (6, "声音", s6_sound, ["sound.json", "film/with_audio.mp4"]),
    (7, "交付", s7_deliver, ["report.md", "gates_final.json"]),
]

# --from N 时挪到 _old/<时间>/ 的文件（不删）。shots/ 不挪：镜头按 spec 指纹自己判断要不要重做（强制重做就删 shots/<id>/done.json）；
# spec 变了时 make_shot 把旧的 work/out/ 挪到 shots/<id>/_old/<时间>/，不拿旧代码续跑。引擎副本 engine/src 也不挪（要换新引擎就手动挪走）。
PRODUCTS = {
    0: ["topic.json"],
    1: ["bible.md", "beatmap.json", "shotlist.json", "gates_plan.json", "fonts.json", "shotlist_wish.json"],
    2: ["sample.json", "sample_review_*.json", "film/sample*.mp4", "film/sample*.mp4.*", "frames/sample", "keyframes.json", "keyframe_review_*.json",
        "frames/key_*.png"],
    3: ["film/cut_r0.mp4*", "film/cut_r0.md5.json", "gates_post_production.json",       # cut_r0.mp4* 连同旁边的 .eff / .sync 量法结果
        "film/preview_r0.*", "frames/preview_r0.jpg"],
    4: ["review_*.json", "fix_*.json", "freeze.json", "loop.json", "gates_r*.json", "film/cut_r[1-9]*", "frames/r[0-9]*",
        "film/preview_r[1-9]*", "frames/preview_r[1-9]*"],
    6: ["sound.json", "film/with_audio.mp4*", "audio"],
    7: ["report.md", "gates_final.json", "deliver"],
}


def archive_from(ep, n, stamp):
    import glob
    moved = []
    for k, pats in PRODUCTS.items():
        if k < n:
            continue
        for pat in pats:
            for src in glob.glob(os.path.join(ep, pat)):
                dst = os.path.join(ep, "_old", stamp, os.path.relpath(src, ep))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(src, dst)
                moved.append(os.path.relpath(src, ep))
    return moved

