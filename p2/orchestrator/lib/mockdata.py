# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""--mock：假回复（模型）和假代理（写一份真能渲染的 shot.py）。只用于测流程，不代表任何真实判断。
开题用 mock/topic.json；导演、镜头表按配乐的约束（BPM、小节数、DROP 小节）现编，保证过得了计划门禁；
审片第 1 轮固定让 s04、s06 不过（内部评分 4.5），第 2 轮 s06 还不过但内部评分 6.5 ≥ 6，用来走一遍「返修 + 冻结 + 到 6 分就停」；
切点核对第 1 轮让 s04、s05 的切点不 ok（s04 进返修问题单，s05 只记建议）。
镜头表带全片分配（allocate：数字链一站一镜、大词不重复、主体来源、动作、每个切点的交接）；5 秒样片第 1 轮让第一镜不过，走一遍样片返修。
规则树：开题带大类 / 理由 / 哪里不同 / 数字出处，节拍表带低谷 / 第 1 秒 / 7 秒王炸 / 色表，镜头表带 route / signature / deviations、
每镜 sustain / type_fx；假代理按声明调 SR.tx、镜头里一直在动——只有 STATIC_V0 里的镜头第一版故意做成 PPT 感（切进来就停住），
用来走一遍「机器量镜内运动量判不过 → 样片返修 → 动起来过关」。
有音乐打击点表（music_map）时导演照表排卡点（beatmap_mm：drop / final / breath / 急停 / S 档都从表里取），过得了新的 check_beatmap。
素材请求通道：镜头表 WISH_SHOT 带一条 footage_wish（走开拍前盘点），制作时 STOCK_SHOT 的假代理写一条
out/footage_requests.json（编排器假装找到、入库、再叫它接进去：ctx/stock.json 里到了的素材排到最前面，返修也接着用）。
空包起跑时镜头表的主体 / 世界层写 request_id（wish_shots），一条故意找不到。
看图确认（shotlist_check）判一条不对题、给新 query 重找；故意找不到的那条进 missing_footage，照样出片。"""

from __future__ import annotations

import json
import os
import re

from . import mockshot, util

MOCK_DIR = os.path.join(util.HERE, "mock")
STATIC_V0 = {"s02"}                                   # 第一版故意不动（PPT 感）的镜头：5 秒样片窗口里的第一镜
CHEAPFX_V0 = {"s03"}                                  # 第一版故意留 2D 粒子的镜头：走一遍劣质特效静态扫描（发回一次 → 交干净版）
CHEAPFX_MARK = "劣质特效（编排器查代码"                 # 发回那一节的标题（lib/cheapfx.sendback_text）：假代理看到它就交干净版
FAIL = {1: ("s04", "s06"), 2: ("s06",)}
CUT_BAD_ROUND1 = ("s04", "s05")
ANCHOR = {1: 4.5}                                     # 之后各轮 6.5
# 素材请求通道：制作时 STOCK_SHOT 这一镜的假代理提一条世界层请求（编排器假装找到、入库、再叫它接进去）；
# 镜头表 WISH_SHOT 这一镜带一条导演的 footage_wish（叠层），走一遍开拍前盘点。都是 mock / STOCK_FAKE 的假站点，不联网。
STOCK_SHOT = "s04"
MISSING_SHOT, MISSING_LINE = "s02", "缺的工具：细长针的实拍特写（素材站搜不到 needle）"   # 11-06 M3 走一遍汇总
STOCK_REQUEST = {"need": "深色水体里往上漂的细小气泡和悬浮颗粒，慢动作，满屏", "use": "world", "seconds": 3,
                 "why": "mock：世界层想换一条会自己往上走的实拍", "query": "underwater bubbles rising dark slow motion"}
WISH_SHOT = "s03"
WISH = {"need": "黑底上碎玻璃屑往外爆散，慢动作", "use": "overlay", "seconds": 3, "why": "mock：导演想要玻璃屑爆散的实拍叠层",
        "query": "glass shards explosion black background slow motion", "kind": "shards"}
# 11-09 T1-6（空包时镜头表死锁）：空包起跑（开题前实拍候选 0 条：topic.assets_candidates.before == 0）时，第 1 个实拍主体、第 1 个带世界层的
# 第 2 个实拍主体写一条假站点故意找不到的（query 只有排除词，搜不出对题的），走一遍「看图确认再找一次 → 还没有 → missing_footage」。
WISH_SUBJ = {"request_id": "REQ_SUBJ", "need": "mock：实拍主体（空包，镜头表写待补素材）", "use": "subject", "seconds": 3,
             "why": "mock：清单里还没有主体实拍", "query": "lightning strike night sky slow motion"}
WISH_WORLD = {"request_id": "REQ_WORLD", "need": "mock：主角身后单向流过的世界层实拍", "use": "world", "seconds": 4,
              "why": "mock：清单里还没有世界层", "query": "storm clouds moving timelapse"}
WISH_MISS = {"request_id": "REQ_MISS", "need": "mock：素材站没有的实拍主体（故意找不到，走一遍 missing_footage）", "use": "subject", "seconds": 3,
             "why": "mock：找不到的那条", "query": "-mocknothing"}


def transition(n, sec_first, sec_id, hero, dsec="S4"):
    """mock 转场声明：s01 接片尾；段落第一镜用万花筒（DROP 用冲击帧盖切点；dsec = DROP 那一段）；其余轮着来。"""
    if n == 1:
        return {"kind": "对位", "eye": "片尾灯管海报 → 第 0 帧同一张海报", "why": "接意思：首尾循环", "slug": "loop-ending",
                "handoff": {"out": "片尾海报上的灯管", "at": "画面正中，同一大小", "in": "第 0 帧海报的灯管"}}
    if sec_first:
        if sec_id == dsec:
            return {"kind": "冲击帧盖切点", "eye": f"招牌中心 → 冲击帧圆心 → {hero}", "why": "卡大拍：DROP", "slug": "impact-match",
                    "handoff": {"out": "炸亮的招牌中心", "at": "画面正中", "in": f"冲击帧圆心里的 {hero}"}}
        if dsec != "S4" and n % 2 == 0:               # DROP 很早（段落都只有 1 小节）：段首轮着用推穿，免得万花筒连用 3 次
            return {"kind": "推穿", "eye": f"上一段主体中心 → 推穿那一点 → {hero}", "why": "换地点：撞开一道界进下一段",
                    "handoff": {"out": "上一段的主体中心", "at": "画面正中推进穿过", "in": f"穿出来的 {hero}"}}
        return {"kind": "万花筒", "eye": f"上一段主体 → 万花筒中心的眼 → {hero}", "why": "换地点：进下一段",
                "handoff": {"out": "上一段的主体", "at": "折进万花筒中心", "in": f"从中心的眼里钻出的 {hero}"}}
    k = ["甩", "推穿", "拍上硬切", "对位", "运动接运动", "遮挡"][n % 6]
    return {"kind": k, "eye": f"上一镜结尾的大字 → {hero}，同一位置", "why": "卡大拍：小节第 1 拍",
            "handoff": {"out": "上一镜结尾的主体", "at": "同一位置、同一方向", "in": f"这一镜的主体和 {hero}"}}


def mock_file(name, series=None):
    """假数据文件：mock/<系列 id>/<名> 优先，没有用 mock/<名>（试跑稿）。"""
    if series and re.fullmatch(r"[A-Za-z0-9_\-]+", str(series)):
        p = os.path.join(MOCK_DIR, str(series), name)
        if os.path.isfile(p):
            return p
    return os.path.join(MOCK_DIR, name)


# ----------------------------------------------------------------------------- 导演 / 镜头表（现编）
def _early_drop(bars, drop_bar):
    """DROP 很早（第 2、3 小节）：DROP 前只放一段 HOOK（屏息在它最后一拍）
    DROP 后依次 DROP（顶）→ COOL（低谷）→ BUILD（高）→ END（高）。段落首尾相接、id 照顺序 S1…；返回 (段落, 卡点, DROP 那一段的 id)。"""
    post = bars + 1 - drop_bar
    n_drop = max(1, (post - 2) // 2)
    n_build = 1 if post - n_drop - 1 >= 2 else 0
    lens = [("DROP", "peak", n_drop, "第一次大爆发：主角撞碎第一道界"), ("COOL", "mid", 1, "低谷：下一道界压住主角"),
            ("BUILD", "high", n_build, "主角蓄力，再撞"), ("END", "high", post - n_drop - 1 - n_build, "冲出最后一道界，海报接回第 0 帧")]
    secs = [{"id": "S1", "name": "HOOK", "bars": [1, drop_bar], "energy": "peak", "purpose": "片名砸下 + 主角入画，DROP 前一拍屏息"}]
    a = drop_bar
    for name, en, n, what in lens:
        if n <= 0:
            continue
        secs.append({"id": f"S{len(secs) + 1}", "name": name, "bars": [a, a + n], "energy": en, "purpose": what})
        a += n
    hits = [{"bar": 1, "beat": 1, "kind": "big", "what": "片名砸下"},
            {"bar": drop_bar - 1, "beat": 4, "kind": "breath", "what": "全片唯一一次屏息"},
            {"bar": drop_bar, "beat": 1, "kind": "drop", "what": "主角撞碎第一道界（冲击帧）"}]
    hits += [{"bar": s["bars"][0], "beat": 1, "kind": "big", "what": f"{s['name']}：换一道界"} for s in secs[2:]]
    hits.append({"bar": bars, "beat": 1, "kind": "final", "what": "主角海报"})
    return secs, hits, secs[1]["id"]


def drop_sec(bm):
    d = next(((h.get("bar"), h.get("beat", 1)) for h in (bm or {}).get("hits") or [] if h.get("kind") == "drop"), None)
    for s in (bm or {}).get("sections") or []:
        if d and s["bars"][0] == d[0]:
            return s["id"]
    return "S4"


def beatmap(bpm=150, bars=16, drop_bar=9):
    h = 1 if drop_bar <= 6 else 2
    if drop_bar - h <= 1 + h:                         # DROP 前放不下 HOOK / COOL / BUILD 三段（drop_bar ≤ 3）：段落会反、会空，换一种排法
        secs, hits, _ = _early_drop(bars, drop_bar)
        s1 = secs[0]["bars"]
    else:
        s1, s3 = [1, 1 + h], [drop_bar - h, drop_bar]
        s2 = [s1[1], s3[0]]
        s4 = [drop_bar, drop_bar + (bars + 1 - drop_bar) // 2]
        s5 = [s4[1], bars + 1]
        secs = [{"id": "S1", "name": "HOOK", "bars": s1, "energy": "peak", "purpose": "第 1 秒 10 格 + 片名"},
                {"id": "S2", "name": "GLOW", "bars": s2, "energy": "mid", "purpose": "变亮阶梯 1 → 100 cd/m²"},
                {"id": "S3", "name": "BUILD", "bars": s3, "energy": "high", "purpose": "主角物灯管蓄力，屏息一拍"},
                {"id": "S4", "name": "DROP", "bars": s4, "energy": "peak", "purpose": "整条街的招牌一起炸亮"},
                {"id": "S5", "name": "BLAZE", "bars": s5, "energy": "high", "purpose": "全亮的那一帧定成海报，接回第 0 帧"}]
        hits = [{"bar": 1, "beat": 1, "kind": "big", "what": "片名 NEON 砸下"},
                {"bar": s2[0], "beat": 1, "kind": "big", "what": "1 cd/m²"},
                {"bar": s3[0], "beat": 1, "kind": "big", "what": "灯管入画"},
                {"bar": drop_bar - 1, "beat": 4, "kind": "breath", "what": "全片唯一一次屏息"},
                {"bar": drop_bar, "beat": 1, "kind": "drop", "what": "招牌炸亮冲击帧"},
                {"bar": min(drop_bar + 2, bars - 1), "beat": 1, "kind": "big", "what": "5000 cd/m²"},
                {"bar": s5[0], "beat": 1, "kind": "big", "what": "100000 cd/m²"},
                {"bar": bars, "beat": 1, "kind": "final", "what": "灯管海报"}]
    seen, uniq = set(), []
    for x in hits:
        k = (x["bar"], x["beat"])
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    cf = {"S1": "片名海报里灯管边缘的光晕", "S2": "实拍湿地面上霓虹的倒影", "S3": "主角物灯管里的辉光",
          "S4": "炸亮的招牌墙", "S5": "灯管海报边缘的光晕"}
    for s in secs:                                    # 规则树：每段的颜色长在哪个东西上（root.color.do1）
        s["color_from"] = cf.get(s["id"]) or f"{s['name']} 这一段主角身后的整屏色"
    wow = next(x for x in uniq if x["kind"] == "big" and x["bar"] > 1)
    return {"bpm": bpm, "bars": bars, "sections": secs, "hits": uniq,
            "hook_1s": {"bar": 1, "beat": 1, "what": "片名 NEON 砸下，第 1 秒 10 格轮换"},
            "wow_7s": {"bar": wow["bar"], "beat": wow["beat"], "what": f"第一个大卡点：{wow['what']}"}}


def _six(bar, beat=1, sub=0):
    return int(round(((bar - 1) * 4 + (beat - 1) + sub / 4) * 4))


def beatmap_mm(mm, bpm=150, bars=16, drop_bar=9):
    """有音乐打击点表（music_map）时照它排卡点（和导演提示词的规则一样，保证过得了 check_beatmap 的新硬错误）：
    drop = 表里的 drop；final = 最后一个重音；breath = DROP 前最安静的一拍；急停排 big、急停后的第一个重音也排 big；
    S 档都排 big（前后 2 拍内已经有 big 的排 small）；每 2 小节最强的重音排 big；第 1 小节第 1 拍有起音就排 big（片名砸下）。
    big 旁边 ±2 个 16 分有更强的格就降成 small。段落、色表、hook / wow 照 beatmap() 的写法。"""
    bm = beatmap(bpm, bars, drop_bar)
    by = {int(e["i"]): e for e in mm["slots"] if int(e["i"]) < bars * 16}
    on = lambda e: bool(e) and (e.get("total") or 0) >= 0.6  # noqa: E731
    acc = lambda e: on(e) and (e.get("accent") or e.get("tier") in ("S", "A"))  # noqa: E731
    hits = {}

    def put(i, kind, what, force=False):
        e = by.get(i)
        if e is None or (i in hits and not force):
            return
        hits[i] = {"bar": e["bar"], "beat": e["beat"], "sub": e.get("sub", 0), "kind": kind, "what": what}

    for k, x, what in (("drop", mm.get("drop"), "招牌炸亮冲击帧（DROP）"), ("final", mm.get("last_accent"), "灯管海报落下（最后一个重音）"),
                       ("breath", mm.get("breath"), "全片唯一一次屏息")):
        if x and x.get("bar"):
            put(_six(x["bar"], x["beat"], x.get("sub", 0)), k, what, force=True)
    beat_f = 3600.0 / float(mm.get("bpm") or bpm)
    stop_i = {_six(x["bar"], x["beat"], x.get("sub", 0)) for x in mm.get("stops") or []}
    br = mm.get("breath") or {}
    br_i = _six(br["bar"], br["beat"], br.get("sub", 0)) if br.get("bar") else None
    for st in mm.get("stops") or []:
        if _six(st["bar"], st["beat"], st.get("sub", 0)) == br_i:
            continue
        put(_six(st["bar"], st["beat"], st.get("sub", 0)), "big", "急停：画面同帧停住、抽空", force=True)
        nxt = min((e for e in by.values() if acc(e) and e["F"] > st["F"] + 2 * beat_f), key=lambda e: e["F"], default=None)
        if nxt:
            put(int(nxt["i"]), "big", "急停后再砸")
            stop_i.add(int(nxt["i"]))                                  # 急停后再砸：照规则必须是 big，不降
    big_i = lambda: [i for i, h in hits.items() if h["kind"] in ("big", "drop", "final")]  # noqa: E731
    if acc(by.get(0)):                                                 # 大卡点只能放在 S / A 档（check_beatmap 的硬错误）
        put(0, "big", "片名 NEON 砸下")
    for e in sorted((e for e in by.values() if e.get("tier") == "S" and on(e)), key=lambda e: -e["total"]):
        put(int(e["i"]), "small" if any(abs(int(e["i"]) - j) <= 8 for j in big_i()) else "big", f"S 档第 {e.get('rank')} 强：整屏换构图")
    for b0 in range(1, bars + 1, 2):
        seg = [e for e in by.values() if acc(e) and b0 <= e["bar"] < b0 + 2]
        if seg:
            e = max(seg, key=lambda e: e["total"])
            if not any(abs(int(e["i"]) - j) <= 8 for j in big_i()):
                put(int(e["i"]), "big", f"乐句最强（第 {e.get('rank')} 强）")
    for i, h in hits.items():                                         # big 旁边有更强的格：降成 small（导演规则：挪到更强的那一格）
        if h["kind"] == "big" and i not in stop_i:
            t = by[i]["total"]
            if any((by.get(j) or {}).get("total", 0) >= t + 0.3 for j in range(i - 2, i + 3) if j != i):
                h["kind"] = "small"
    out = sorted(hits.values(), key=lambda h: _six(h["bar"], h["beat"], h["sub"]))
    bm["hits"] = out
    early = [h for h in out if h["kind"] in ("big", "drop") and _six(h["bar"], h["beat"], h["sub"]) * beat_f / 4 < 400
             and (h["bar"], h["beat"]) != (1, 1)]
    if early:
        bm["wow_7s"] = {"bar": early[0]["bar"], "beat": early[0]["beat"], "sub": early[0]["sub"], "what": f"第一个大卡点：{early[0]['what']}"}
    return bm


def director(ctx):
    m = ctx.get("music") or {}
    if ctx.get("mm"):
        bm = beatmap_mm(ctx["mm"], m.get("bpm", 150), m.get("bars", 16), m.get("drop_bar", 9))
    else:
        bm = beatmap(m.get("bpm", 150), m.get("bars", 16), m.get("drop_bar", 9))
    bible = util.read_json(mock_file("director.json", ctx.get("series")))["bible"]
    return {"bible": bible, "beatmap": bm}


WORDS = ["NEON", "GLOW", "TUBE", "SIGN", "NIGHT", "CITY", "FLICKER", "BUZZ", "GAS", "PINK", "HALO", "BEAM", "SHINE", "BRIGHT", "STREET", "RAIN", "GLASS", "WIRE", "BLAZE", "SPARKLE"]
LABELS = ["1 cd/m²", "10 cd/m²", "100 cd/m²", "1000 cd/m²", "5000 cd/m²", "20000 cd/m²", "100000 cd/m²", "100000 cd/m²"]


def empty_pack(ctx):
    return ((ctx.get("topic") or {}).get("assets_candidates") or {}).get("before") == 0


def wish_shots(shots):
    """空包 mock 的镜头表：第 1 个实拍主体 → REQ_SUBJ，第 2 个 → REQ_MISS（找不到）。"""
    real = [s for s in shots if s["subject_src"].get("kind") == "实拍"]
    for s, w in zip(real, (WISH_SUBJ, WISH_MISS)):
        s.update(subject_src={"kind": "实拍", "request_id": w["request_id"]}, subject="实拍里的霓虹（待补素材）",
                 footage_wish=list(s.get("footage_wish") or []) + [dict(w)])
    w3 = next((s for s in shots[:-1] if s["subject_src"].get("kind") == "生图" and s.get("aux_footage")), None)
    if w3:
        w3.update(event=w3["event"].replace(w3["footage"], WISH_WORLD["request_id"]), footage=WISH_WORLD["request_id"],
                  footage_wish=list(w3.get("footage_wish") or []) + [dict(WISH_WORLD)])
    return shots


def shotlist_check(ctx):
    """看图确认的假回复：世界层 REQ_WORLD（没有就第 2 条找到的）判不对题、给新 query；故意找不到的（missing）
    再给一个照样搜不出的 query（STOCK_FAKE 下最后进 missing_footage，整集照样出片）；别的都 ok。"""
    items = ctx.get("check_items") or []
    found = [x for x in items if x.get("kind") != "missing"]
    rej = next((x["id"] for x in found if x["id"] == WISH_WORLD["request_id"]), found[1]["id"] if len(found) > 1 else None)
    out = []
    for x in items:
        if x["id"] == rej:
            out.append({"id": x["id"], "verdict": "reject", "query": "dark storm clouds rolling timelapse", "route": "retry",
                        "why": "mock：联系表里不是要的那种东西"})
        elif x.get("kind") == "missing":
            out.append({"id": x["id"], "verdict": "missing", "query": "-mocknothing -again", "route": "生图", "why": "mock：素材站没有"})
        else:
            out.append({"id": x["id"], "verdict": "ok", "why": "mock：对题"})
    return {"items": out}


def shotlist(ctx):
    bm = ctx["beatmap"]
    shots, n = [], 0
    looks = ["footage", "design"]
    dsec = drop_sec(bm)
    for sec in bm["sections"]:
        a, b = sec["bars"]
        first = True
        while a < b:
            e = min(b, a + (1 if sec["id"] in ("S1", "S3") else 2))
            n += 1
            sid = f"s{n:02d}"
            if sec["id"] == "S1":
                look, cut = "design", "16th"
            elif sec["id"] == "S3":
                look, cut = "footage", "8th"
            else:                                                     # 整片切点密度要接近原片（plan_density_errs：≥ min(3.0/s, 全用 16th 的 65 %)）
                look, cut = looks[n % 2], "16th"
            s = {"id": sid, "section": sec["id"], "bars": [a, e], "cut_every": cut, "look": look,
                 "subject": f"{sec['name'].lower()} {look}", "hero_word": WORDS[(n * 3) % len(WORDS)],
                 "words": [WORDS[(n * 3 + i) % len(WORDS)] for i in range(6)],
                 "label": LABELS[min(n - 1, len(LABELS) - 1)], "label_px": 24,
                 "event": "每个切点换构图，大卡点冲击帧", "image": f"意象{n}",
                 "transition_in": transition(n, first, sec["id"], WORDS[(n * 3) % len(WORDS)], dsec),
                 "parts": {"design": ["type", "impact", "mono_motif"], "footage": ["mono_motif", "camera", "impact"]}[look],
                 "footage": "none" if look != "footage" else "街头夜景实拍"}
            if sec["id"] == "S1":
                s["holds"] = [[1, 4]]                      # 片名格立住最后一拍
            if e == bm["bars"] + 1:
                s["holds"] = [[bm["bars"], 2], [bm["bars"], 3], [bm["bars"], 4]]
            if sid == WISH_SHOT:
                s["footage_wish"] = [dict(WISH)]
            shots.append(s)
            a, first = e, False
    shots = allocate(shots, ctx, dsec)
    if empty_pack(ctx):
        shots = wish_shots(shots)
    sig =next((s["id"] for s in shots if s["section"] == dsec), shots[len(shots) // 2]["id"])
    return {"route": "蒙太奇", "signature": {"shot": sig, "why": "mock：DROP 那一镜灯管在冲击帧里一节节炸亮、透出下面的实拍，生成模型做不出同一个物件跨切点不跳"},
            "deviations": [{"id": "root.transition.param1", "why": "mock：转场轮着用 6 种，只为把引擎的每种转场函数都走一遍"}],
            "shots": shots}


SUSTAIN = ["动作接力", "东西在干活", "运镜跟着主体", "分批揭示"]
TYPE_FX = [("hero", "SLAM 砸入", "出血裁切"), ("wide", "逐字母弹出", "半屏对撞"), ("hero", "被冲击帧打出", "居中定版"),
           ("wide", "描边拉伸", "出血裁切")]


def allocate(shots, ctx, dsec="S4"):
    """mock 的全片分配（照导演提示词的规则现编
    数字链按顺序一站一镜；大词全片不重复；实拍主体一段素材只用一次。"""
    topic = ctx.get("topic") or {}
    title = (topic.get("screen_title") or WORDS[0]).upper()
    lad = [f"{x['value']} {x['unit']}".strip() for x in topic.get("number_ladder") or []]
    names = list(ctx.get("footage") or [])
    worlds = list(reversed(names))
    pool = [w for w in (topic.get("lexicon") or WORDS) if w.upper() != title and not any(ch.isdigit() for ch in w)]
    mid = shots[1:-1]
    take = mid if len(mid) >= len(lad) else (shots[1:] if len(shots) - 1 >= len(lad) else shots)
    num = {s["id"]: lad[i] for i, s in enumerate(take[:len(lad)])}
    k = 0
    for i, s in enumerate(shots):
        end = i in (0, len(shots) - 1)
        hero = title if end else pool[k % len(pool)]
        extra = [] if end or k + len(mid) >= len(pool) else [pool[k + len(mid)]]
        k += 0 if end else 1
        if end or not names:
            src, subj, act = {"kind": "生图", "gen": "霓虹灯管主角物：弯成字形的玻璃灯管，通电发光、管壁有反光，暗底，居中构图"}, "霓虹灯管主角物（生图）", "从画面外旋入中心、在大卡点上一节节亮起"
        else:
            f = names.pop(0)
            src, subj, act = {"kind": "实拍", "file": f}, f"实拍 {f} 里的霓虹", "招牌从一点亮起、镜头跟着光推进"
        s.update(hero_word=hero, words=[hero] + extra, number=num.get(s["id"], ""), label=num.get(s["id"]) or "NEON LOG",
                 subject=subj, subject_src=src, action=act, sustain=SUSTAIN[i % len(SUSTAIN)],
                 type_fx=dict(zip(("font", "entry", "layout"), TYPE_FX[i % len(TYPE_FX)])))
        if src["kind"] == "生图" and i != len(shots) - 1 and worlds:
            w = [worlds[(3 * i + j) % len(worlds)] for j in range(3)]
            s.update(footage=w[0], aux_footage=w[1:],
                     event=f"每个计划切点换掉世界层（{w[0]} → {w[1]} → {w[2]}，或同一条往后跳一段），主角钉住不跳、一路推近；大卡点冲击帧")
            if s["look"] == "design":
                s["look"] = "footage"
        s["transition_in"] = transition(int(s["id"][1:]), s["transition_in"]["kind"] in ("万花筒", "冲击帧盖切点"), s["section"], hero, dsec)
    return shots


def llm_reply(stage, name, prompt, images, attempt, ctx):
    if stage == "topic":                             # --motif 给了（任务书里「你已定 母题 = 「X」」）：选中的候选换成那个母题，
        t = util.read(mock_file("topic.json", (ctx or {}).get("series")))   # 素材包、配乐、叠层表跟着走（其余假数据还是试跑稿的）
        m = re.search(r"母题 = 「([^」]+)」", prompt or "")
        if m:
            o = json.loads(t)
            o["candidates"][o["chosen"]]["motif"] = m.group(1)
            t = json.dumps(o, ensure_ascii=False, indent=1)
        return "```json\n" + t + "\n```"
    if stage == "director":
        return json.dumps(director(ctx), ensure_ascii=False)
    if stage == "shotlist" and name == "shotlist_check":
        return json.dumps(shotlist_check(ctx), ensure_ascii=False)
    if stage == "shotlist":
        return json.dumps(shotlist(ctx), ensure_ascii=False)
    if stage == "keyframe_review":
        return json.dumps({"sections": [{"id": s, "pass": True, "issues": []} for s in ctx["sections"]]}, ensure_ascii=False)
    if stage == "sample_review":                     # 5 秒样片：第 1 轮让第一镜不过（走一遍样片返修），之后都过
        bad = ctx["shots"][:1] if ctx["round"] == 1 else []
        return json.dumps({"shots": [{"id": s, "pass": s not in bad, "score": 2 if s in bad else 4,
                                      "issues": [{"rule": "必做 · 主体有动作", "where": "图 2 连续 3 帧",
                                                  "fix": "mock：主体做它的动作"}] if s in bad else []} for s in ctx["shots"]],
                           "motion_ok": True, "verdict": "mock：样片"}, ensure_ascii=False)
    if stage == "review" and name.startswith("review_global"):
        sc = ANCHOR.get(ctx["round"], 6.5)
        return json.dumps({"issues": [], "verdict": "mock：全片层面没有意见", "anchor_score": sc,
                           "anchor_why": "mock：主体和冲击不如原片，比基线强" if sc >= 6 else "mock：画面是机械拼的，离原片差得远"},
                          ensure_ascii=False)
    if stage == "review":
        out = []
        for sid in ctx["shots"]:
            bad = sid in FAIL.get(ctx["round"], ())
            out.append({"id": sid, "pass": not bad, "score": 2 if bad else 4,
                        "issues": [{"rule": "全局 10 / 不要-2", "where": "图 2 中间帧，主体周围",
                                    "fix": "mock：换构图轮换、推近"}] if bad else []})
        cuts = []
        for cid in ctx.get("cuts", []):
            ok = not (ctx["round"] == 1 and cid in CUT_BAD_ROUND1)
            cuts.append({"cut": cid, "declared": "mock：声明的转场", "seen": "mock：看到的" + ("和声明一致" if ok else "只是硬切"),
                         "ok": ok, "fix": "" if ok else "mock：入点照声明用 SR.tx 做出来"})
        return json.dumps({"shots": out, "cuts": cuts}, ensure_ascii=False)
    raise KeyError(f"mock 没有 {stage}/{name} 的回复")


# ----------------------------------------------------------------------------- 假代理
def agent(job):
    """读 work/ctx/ 里给真代理的同一批文件（task.json、spec.json、footage.json），写出 out/ 里的代码。
    返修时 variant +1（构图轮换、取材起点都变），冻结镜头不受影响。"""
    work = job["work"]
    out = os.path.join(work, "out")
    os.makedirs(out, exist_ok=True)
    ctxd = os.path.join(work, "ctx")
    task = util.read_json(os.path.join(ctxd, "task.json"), {})
    spec = util.read_json(os.path.join(ctxd, "spec.json"))
    clips = (util.read_json(os.path.join(ctxd, "footage.json"), {}) or {}).get("clips", [])
    hexcol = spec["signature_color"]["hex"]
    kind = job["kind"]
    src = spec.get("subject_src") or {}
    world = set(re.findall(r"F\d{2,3}", str(spec.get("footage") or ""))) | set(spec.get("aux_footage") or [])
    allc = list(clips)
    if src.get("kind") == "实拍" and any(x["name"] == src.get("file") for x in clips):
        clips = [x for x in clips if x["name"] == src["file"]]      # 主体素材：只用分给这一镜的那条
    elif world and any(x["name"] in world for x in clips):
        clips = [x for x in clips if x["name"] in world]
    arr = [a["name"] for a in (util.read_json(os.path.join(ctxd, "stock.json"), {}) or {}).get("arrived", [])
           if a.get("name") and a.get("use") in ("world", "subject")]
    if arr and (job.get("stock_followup") or kind == "fix"):        # 素材请求通道「素材到了，接进去」（返修也接着用）：新素材排到最前面
        clips = [x for x in allc if x["name"] in arr] + [x for x in clips if x["name"] not in arr]
    if kind == "keyframe":
        util.write(os.path.join(out, "key.py"), mockshot.shot_code(task["fn"], spec, clips, hexcol, 0))
    elif kind == "shot" and job.get("stock_followup"):
        util.write(os.path.join(out, "shot.py"), mockshot.shot_code(task["fn"], spec, clips, hexcol, 0))   # 接上新到的素材
    elif kind == "shot":
        cheap = (spec["id"] in CHEAPFX_V0 and CHEAPFX_MARK not in (job.get("task") or "")
                 and not os.path.exists(os.path.join(out, "shot.py")))      # 只有第一版带：之后再叫（别的问题发回）也交干净版
        util.write(os.path.join(out, "shot.py"), mockshot.shot_code(task["fn"], spec, clips, hexcol, 0, static=spec["id"] in STATIC_V0,
                                                                    cheap=cheap))
    elif kind == "fix":
        p = os.path.join(out, "shot.py")
        mv = re.search(r"^variant = (\d+)", util.read(p), re.M) if os.path.exists(p) else None
        v = int(mv.group(1)) + 1 if mv else 1                       # 真代理写的代码（看片意见轮恢复的一稿）没有 variant 行：从 1 起
        util.write(p, mockshot.shot_code(task["fn"], spec, clips, hexcol, v))
    if job.get("imagegen") and kind == "shot" and not job.get("gen_followup") and (int(spec["id"][1:] or 0) % 3 == 1
                                                                                     or src.get("kind") == "生图"):
        req = [{"id": "far", "use": "远景", "ratio": "16:9", "prompt": f"{spec.get('subject') or 'neon'} 远景，冷光从左上来，薄雾，无人无字"}]
        if src.get("kind") == "生图":
            req.append({"id": "subject", "use": "主体", "ratio": "1:1", "prompt": src.get("gen") or spec.get("subject") or "neon"})
        util.write_json(os.path.join(out, "images.json"), {"images": req})   # 生图通道开着时：mock 代理也提生图请求，走一遍通道
    if job.get("stock") and kind == "shot" and not job.get("stock_followup") and spec["id"] == STOCK_SHOT:
        util.write_json(os.path.join(out, "footage_requests.json"), [dict(STOCK_REQUEST)])   # 素材请求通道：mock 代理提一条，走一遍
    util.write(os.path.join(out, "NOTES.md"), f"mock {kind} {job['name']}"
               + ("（素材到了：" + "、".join(x["name"] for x in clips[:1]) + " 排在最前）" if job.get("stock_followup") else "") + "\n"
               + (MISSING_LINE + "\n" if spec["id"] == MISSING_SHOT and kind in ("shot", "fix") else ""))   # 走一遍「缺的工具」汇总
    return {"in_miss": 20000, "in_hit": 180000, "out": 6000}
