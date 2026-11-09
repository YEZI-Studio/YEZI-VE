# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""规则树（yezi-ve/p1/tree/）接进编排器（流水线 v2.1）。

1. 开题先读 `目录卡.md` 判大类（系列已知大类的直接归类，CHROMA = 卡点炫技），topic.json 记 category / category_why / differs。
2. 各环节提示词按需引用，保持上下文小：
     {{rules:维度,维度|档,档;维度|档}}  根.md 那几个维度的行 + 本大类类型卡同维度的条目（从 tree.json 取，档 = do / floor / param / flow）
     {{human:维度,维度}}                 这几个维度里机器量不了、交给审片看图判的条目
     {{rule:root.flow.1,…}}              指定的几条
     {{include:type#尺子|菜单}}           本大类类型卡的小节（Ctx 按大类把别名 type 指到那张卡）
     系列卡照旧 {{include:card#…}}。
3. tree.json 里能查的项接到门禁，按档位定级：必做 / 流程 = hard（没做不放行：计划阶段发回导演，制作阶段发回代理，交付时挡）；
   底线 = 计划、代码上的 hard；成片上的只有「定稿片 Light、点火都过」的稳的检查才 hard（见 VIDEO），量不稳的只报警 + 进返修问题单；
   默认 = 只报警，偏离写一句理由（镜头表 deviations 或代理 NOTES.md）；人判的交给审片。
   每一集的结果写 <集>/gates_tree.json：根 + 本大类的每一条规则一行（档位、怎么查、结果、位置），没接的标「未接」。
纯标准库。"""
from __future__ import annotations

import os
import re

from . import util

TREE_DIR = os.path.join(util.ROOT, "p1", "tree")
TREE_JSON = os.path.join(TREE_DIR, "tree.json")
TIER = {"do": "必做", "floor": "底线", "param": "默认", "flow": "流程"}
ALIAS = {"卡点炫技": "beat"}
SHOW = {}
CIRC = "①②③④⑤⑥⑦⑧⑨⑩"
HEAD = ("（三档：必做 = 一个动作，交付前脚本查做没做，没做不放行；底线 = 硬线，不达标返修；默认 = 参数，可以按这一片改，"
        "偏离写一句理由。说法不同听更具体的：系列卡 > 类型卡 > 根。长相（具体字体、颜色、构图、招式）只给菜单，不写死。）")
ROUTES = ("连续", "蒙太奇", "混合")
SUSTAIN = ("分批揭示", "运镜跟着主体", "东西在干活", "动作接力")
LINE_KINDS = ("尺度", "数值", "时间", "光线", "情绪", "剧情")
ENERGY = {"low": 1, "mid": 2, "high": 3, "peak": 4}
DIP_WORDS = ("闪黑", "闪白", "黑场", "白场", "渐黑", "淡入", "淡出", "fade", "dip to")
MIX_WORDS = ("叠化", "溶解", "dissolve", "crossfade")
FX_BAN = ("点连线", "引导线", "准星", "锁形框", "声波环", "虚线", "往外扩的波纹", "扩散波纹", "圈出", "大头针", "闪三下",
          "黑杠", "贴纸", "彩纸", "平面贴图", "黑洞吸")
LOOK_BAN = ("颗粒", "扫描线", "色差", "老电视", "vhs", "film grain", "scanline", "chromatic aberration", "crt")
NEG = ("不", "别", "没", "无", "非", "免", "no ", "not ", "without ")
CAM_ONLY = ("推近", "拉远", "推拉", "推进", "拉出", "摇镜", "横移", "平移", "缓推", "慢推", "展示", "呈现", "出现", "飞入", "飞进",
            "飞出", "淡入", "滑入", "弹出", "镜头", "运镜", "大字", "字", "画面", "缓缓", "慢慢", "从", "到", "向", "往", "地", "的",
            "和", "并", "再", "然后", "推", "拉", "摇", "移", "进", "入")
TEXTURE = ("雾", "烟", "肌理", "纹理", "渐变", "背景", "粒子", "霜纹", "光斑", "噪点", "底纹", "色块", "光晕", "雾气", "白雾")
# 成片门禁 id → 规则 id。STABLE = 定稿片 Light、点火都过的检查：
# 底线落在这些上就是 hard；不在里面的（切点像素检查、无声重击……定稿片自己也会报）只报警、进返修问题单。
# 根「镜头」底线①的「原地呼吸、漂浮不算动」接 motion_sway（原地来回占比 + 同素材乒乓），卡点炫技底线 1 换成有效运动量
# motion_eff（旧帧差 motion_shot 只报告）；根「节奏」底线 1「大卡点那一帧有画面事件」原来按 p1/tree/build_tree.py 写的是
# 现在接实测卡点 sync_plan（big_hits 删掉；sync_top / sync_select 11-06 G10 起挂参数档、只报告）；「配乐」底线 1 的节拍表和配乐一致
# 加 sync_grid（网格）、sync_mux（交付混流）。这几条在点火、Light 上都过（gates.V2 的 calibration），所以是稳的。
VIDEO = {
    "root.quality.floor1": ("spec_file", "spec_size", "spec_fps", "spec_codec", "frames"),
    "root.camera.floor1": ("still", "motion_sway"), "root.camera.param1": ("in_median", "motion_content"),
    "root.transition.floor1": ("cut_dip",), "root.transition.floor2": ("cut_blend",), "root.transition.floor4": ("cut_moving",),
    "root.transition.param2": ("cut_carry",),
    "root.rhythm.floor1": ("sync_plan",), "root.rhythm.floor2": ("similar",),
    "root.rhythm.param1": ("cut_density", "hits", "sync_silent", "sync_top", "sync_select"),   # 倍数、选择性只报告
    "root.audio.floor1": ("m_bpm", "m_length", "sync_grid", "sync_mux"), "root.audio.floor3": ("audio", "loudness"),
    "root.type.floor1": ("repeat",),
    "root.color.floor1": ("colors",), "root.color.floor2": ("grey",), "root.color.param1": ("tint",),
    "root.openend.floor1": ("first_motion",), "root.openend.floor2": ("end_black",), "root.openend.param2": ("loop",),
    "root.flow.5": ("frozen",),
    "beat.floor1": ("motion_eff", "motion_still"), "beat.param1": ("in_median",), "beat.param2": ("duration",),
    "beat.param4": ("cut_density", "hits"), "beat.param5": ("first_second",), "beat.do4": ("m_final",),
}
STABLE = {"spec_file", "spec_size", "spec_fps", "spec_codec", "frames", "audio", "m_bpm", "m_length", "frozen", "repeat",
          "motion_eff", "motion_sway", "motion_still", "still", "similar", "colors", "grey", "first_motion", "end_black", "loudness",
          "sync_grid", "sync_top", "sync_select", "sync_plan", "sync_mux"}
# 给代理的说法（只换进提示词：{{rules:}} {{rule:}} {{human:}}；总账、门禁行照用 tree.json 原文）。11-04 验收：
# 这几条原文写着门禁阈值和只奖励数量的密度数（「每镜镜内帧差均值 ≥5.22」「≥3.8 个/秒」「第 1 秒 10 个构图」），
# 违反「阈值、密度数不进给代理的文字」（防 Goodhart：代理以前就是照 5.22 搭尺子凑帧差、拿同一张图挪一点凑切点）；
# 「最后一击落在原曲自己的拍上」和 11-04 的 final = 窗口里最后一个重音（不一定在拍上）冲突。p1/tree/卡点炫技.md 不在这次授权改的范围里，
# 原文留着，改了原文以后把这里对应的条目删掉。
AGENT_TEXT = {
    "beat.floor1": "每镜镜内都有进展：一拍里原地来回的（摆、呼吸、缩放泵、整帧抖、慢慢漂过去又漂回来、同一版式跳一下再回原位、"
                   "两张图来回轮换、亮度一明一暗）机器量出来是 0；屏息那一拍和最后一击之后不算；整片近乎静止帧少"
                   "（编排器渲完量有效运动量、原地来回占比、近乎静止帧占比）。",
    "beat.param1": "整片镜头里一直往前走，像本系列代表作那样（编排器量有效运动量，原片实测见尺子）；切点不追高，先让镜头里动起来。",
    "beat.param4": "切点由编排器从音乐起音里取（cut_every 只管密度），有忙有闲、轻重跟着音乐走：重音上的变化最大，其余的可以不切；"
                   "每个切点都要换来新东西。拍上硬切不设上限，只报告占比。",
    "beat.param5": "CHROMA 第 1 秒就有变化（片名砸下、一格一个大词）：构图跟着前奏的起音换，不拿同一张图挪一点来凑数。",
    "beat.do4": "屏息全片只一次，放在 DROP 前；最后一击 = 原曲窗口里最后一个重音（音乐打击点表标的 final，不一定在拍上）。",
    "beat.floor2": "拍上硬切要真兜住：切点 ±2 帧内有冲击帧，或形状对得上。",
    "root.camera.param1": "镜内要有进展、不是原地动：编排器量有效运动量（分块量位移，一拍里来回的部分不算，切点帧不算）；"
                          "多加切点抵不了镜头里不动。",
    # 原文的物质清单和白名单、手册 §5b 重复；给代理只留规则本身
    "root.fx.floor4": "冒充物质的特效（碎片、粉尘、雾、烟、裂缝、气泡、水滴……）只能来自实拍（素材或抠像叠层）、画面自己派生"
                      "（碎开、位移、折射），不用 2D 画的点、线、多边形冒充，也不把同一张渲染图复制拼成别的物体（劣质特效"
                      "「贴图、线段」是致命错误）；能画的只有原片自己在用的图形手段（白名单）",
}
_T = {}


# ----------------------------------------------------------------------------- 读树
def load(path=TREE_JSON):
    p = os.path.abspath(path)
    m = os.path.getmtime(p)
    if _T.get(p, (None,))[0] != m:
        t = util.read_json(p)
        idx = {}
        for layer, dims in t["layers"].items():
            for dim, tiers in dims.items():
                for tier, items in tiers.items():
                    for it in items:
                        idx[it["id"]] = dict(it, layer=layer, dim=dim, tier=tier)
        t["_idx"], t["_dim"] = idx, {v: k for k, v in t["dims"].items()}
        _T[p] = (m, t)
    return _T[p][1]


def cat_id(t, name):
    n = re.sub(r"\s+", "", str(name or ""))
    if not n:
        return None
    for c in t["categories"]:
        if n in (c["id"], re.sub(r"\s+", "", c["name"])):
            return c["id"]
    for k, v in ALIAS.items():
        if n.startswith(k) or k in n:
            return v
    return None


def category(t, cid):
    return next((x for x in t["categories"] if x["id"] == cid), None)


def cat_name(t, cid):
    return SHOW.get(cid) or (category(t, cid) or {}).get("name") or str(cid or "?")


def _clean(s):
    s = re.sub(r"（全 \d+）", "", str(s or ""))
    return re.sub(r"（全 \d+；", "（", s).strip()


def _text(x):
    """规则给代理看的文字：AGENT_TEXT 里有就用它（去掉阈值的说法），否则用规则树原文。"""
    return AGENT_TEXT.get(x.get("id"), x.get("text"))


def _split(s):
    return [x.strip() for x in re.split(r"[,，、]", s or "") if x.strip()]


# ----------------------------------------------------------------------------- 提示词
def _relax_for(cat, series):
    """类型卡的「放宽根上的」条目。"""
    return list(cat.get("relax_root") or [])


def rules_block(t, cid, groups, series=None):
    """groups = [(维度中文名列表, 档列表)]。每个维度一行根 + 一行类型卡（有就写），第一行是三档说明。"""
    lines, cname, cat = [HEAD], cat_name(t, cid), category(t, cid) or {}
    relax = _relax_for(cat, series)
    for dims, tiers in groups:
        for dn in dims:
            did = t["_dim"].get(dn, dn)
            for layer, label in (("root", "根"), (cid, cname)):
                L = ((t["layers"].get(layer) or {}).get(did) or {}) if layer else {}
                seg = []
                for tr in tiers:
                    items = L.get(tr) or []
                    if items:
                        seg.append(TIER.get(tr, tr) + "：" + "".join(f"{CIRC[i % 10]}{_clean(_text(x))}" for i, x in enumerate(items)))
                if seg:
                    lines.append(f"- {label}·{dn}｜" + "｜".join(seg))
            if dn == "特效" and relax:
                lines.append(f"- {cname}·放宽根上的｜" + "；".join(relax))
    return "\n".join(lines)


def human_block(t, cid, dims):
    rows = []
    for dn in dims:
        did = t["_dim"].get(dn, dn)
        for layer in ("root", cid):
            for tr in ("floor", "param"):
                for x in ((t["layers"].get(layer) or {}).get(did) or {}).get(tr) or []:
                    ck = x.get("check") or {}
                    if "人判" in ck.get("status", ""):
                        how = re.sub(r"审片(代理)?问[:：]?", "", ck.get("how") or "").strip("；; ") or "看图判"
                        rows.append(f"- {TIER[tr]}·{dn}：{_clean(_text(x))[:70]}（{how[:60]}）")
    return "\n".join(rows) or "（无）"


def expand(tpl, t, cid, series=None):
    """把 {{rules:…}} {{human:…}} {{rule:…}} 换成规则树原文（其余占位符留给 util.render_prompt）。series = 系列配置（放宽条按系列过滤）。"""
    def rep(m):
        kind, arg = m.group(1), m.group(2)
        if kind == "rules":
            groups = []
            for g in arg.split(";"):
                dims, _, tiers = g.partition("|")
                groups.append((_split(dims), _split(tiers) or ["do", "floor", "param"]))
            return rules_block(t, cid, groups, series)
        if kind == "human":
            return human_block(t, cid, _split(arg))
        return "\n".join("- " + _clean(_text(t["_idx"][x])) for x in _split(arg) if x in t["_idx"])
    return re.sub(r"\{\{(rules|human|rule):([^}]*)\}\}", rep, tpl)


def catalog_brief(series_name, known):
    """开题提示词里那句「系列已知属于哪类」。"""
    return (f"任务点名了系列：{series_name} 已知属于「{known}」（目录卡：任务点名了系列或旧片 → 按「系列卡」那列直接归类），"
            "category 写它；category_why 用第 1 问（这片卖什么）说一句；differs 写这一集和上一集、和同类最火的那条哪里不同。")


# ----------------------------------------------------------------------------- 结果行
def level(t, rid, hard=None):
    if hard is not None:
        return "hard" if hard else "warn"
    return "hard" if (t["_idx"].get(rid) or {}).get("tier") in ("do", "floor", "flow") else "warn"


def row(t, rid, ok, where=(), value=None, how="", stage="", hard=None, why=None):
    """ok：True 做到 / False 没做到 / None 不适用。默认档没做到、但写了理由（why）的记 info「偏离·有理由」。"""
    r = t["_idx"].get(rid) or {}
    lv = level(t, rid, hard)
    st = "ok" if ok is True else ("na" if ok is None else ("fail" if lv == "hard" else "warn"))
    if st == "warn" and why:
        st = "info"
    d = {"id": rid, "tier": TIER.get(r.get("tier"), "?"), "dim": t["dims"].get(r.get("dim"), r.get("dim", "?")),
         "level": lv, "status": st, "stage": stage, "how": how, "value": value, "where": [str(x) for x in where][:12],
         "rule": _clean(r.get("text", ""))[:90]}
    if why:
        d["why"] = str(why)[:200]
    return d


def say(r):
    """发回给模型的一句话。"""
    return f"[{r['tier']}·{r['dim']}] {r['how']}" + (f"：{'；'.join(r['where'][:6])}" if r["where"] else "")


def hard_msgs(rows):
    return [say(r) for r in rows if r["status"] == "fail"]


def soft_msgs(rows):
    return [say(r) + "（是默认值：可以改，或者在 deviations 里写一句理由）" for r in rows if r["status"] == "warn"]


def _neg_hit(text, words):
    """text 里出现了 words 里的词、且前面没跟着「不 / 别 / 没……」。"""
    s, out = str(text or "").lower(), []
    for w in words:
        i = s.find(w)
        while i >= 0:
            if not any(n in s[max(0, i - 4):i] for n in NEG):
                out.append(w)
                break
            i = s.find(w, i + 1)
    return out


def _cam_only(action):
    rest = str(action or "")
    for w in sorted(CAM_ONLY, key=len, reverse=True):
        rest = rest.replace(w, "")
    return len(re.sub(r"[\W\d_a-zA-Z]+", "", rest)) < 2


def _texture_only(subject):
    rest = str(subject or "")
    for w in sorted(TEXTURE, key=len, reverse=True):
        rest = rest.replace(w, "")
    return bool(str(subject or "").strip()) and len(re.sub(r"[\W\d_的和与上里中层]+", "", rest)) < 2


def sustain_of(v):
    v = re.sub(r"\s+", "", str(v or "")).replace("自己", "")
    return next((k for k in SUSTAIN if k == v or k in v), None)


def type_fx(s):
    x = s.get("type_fx") if isinstance(s.get("type_fx"), dict) else {}
    return tuple(re.sub(r"\s+", "", str(x.get(k) or "")).lower() for k in ("font", "entry", "layout"))


def _frame(bpm, h):
    beat = 3600.0 / bpm
    return (h.get("bar", 1) - 1) * 4 * beat + (h.get("beat", 1) - 1) * beat + float(h.get("sub", 0) or 0) * beat / 4


# ----------------------------------------------------------------------------- 开题
def topic_eval(t, o, series, env=None):
    """o = 开题模型的原样输出（candidates / chosen + category …）。"""
    out = []
    ch = o["candidates"][o["chosen"]] if o.get("candidates") else o
    cid, want = cat_id(t, o.get("category")), cat_id(t, series.get("category"))
    w = []
    if not cid:
        w.append(f"category「{o.get('category') or '没写'}」不是这个系列的大类（卡点炫技）")
    elif want and cid != want:
        w.append(f"{series.get('name')} 系列已知属于「{cat_name(t, want)}」（目录卡：任务点名了系列 → 直接归类），现在写的是「{o.get('category')}」")
    for k, n in (("category_why", "大类理由：这片卖什么"), ("differs", "和上一集、和同类最火的那条哪里不同")):
        if len(str(o.get(k) or "").strip()) < 6:
            w.append(f"{k}（{n}）要写一句")
    out.append(row(t, "root.flow.1", not w, w, o.get("category"), "开题先读目录卡判大类：写 category、category_why、differs", "开题"))
    bad = [f"{x.get('value')} {x.get('unit', '')}（{x.get('what', '')}）" for x in ch.get("number_ladder") or []
           if len(str(x.get("source") or "").strip()) < 2]
    out.append(row(t, "root.arc.do2", not bad, [f"没写出处：{b}" for b in bad], how="屏上的数字写出处：number_ladder 每一站加 source（一句：教科书 / 百科 / 机构）", stage="开题"))
    lk = str(o.get("line_kind") or "").strip()
    out.append(row(t, "root.arc.param1", any(k in lk for k in LINE_KINDS), [f"line_kind = {lk or '没写'}"], lk,
                   "line_kind：变化线从尺度、数值、时间或光线、情绪、剧情里选一条", "开题"))
    rd = bool(env and env.get("read"))
    out.append(row(t, "root.assets.do1", rd, [] if rd else [(env or {}).get("note") or "没读到体检结果"],
                   (env or {}).get("t"), "环境体检（p2/tools/doctor.py → orchestrator/doctor.json），按结果选路线", "开题",
                   hard=False if not rd else None))
    return out


WORLD_MIN = 30


def world_min(series):
    """(下限, 理由)：系列配置 assets.world_min（没写 = 30）和 world_min_note。"""
    a = (series or {}).get("assets") or {}
    v = a.get("world_min")
    return (WORLD_MIN if v is None else int(v)), str(a.get("world_min_note") or "")


def candidates_eval(t, cid, n_cand, n_local, min_n=WORLD_MIN, why=""):
    """卡点炫技必做：实拍候选 ≥ min_n 条（素材包 sources.json 里搜到的条目；min_n = 系列 assets.world_min，默认 30）。
    min_n < 30 时 why 写进位置。"""
    note = [] if min_n >= WORLD_MIN else [f"系列 assets.world_min = {min_n}" + (f"：{why[:80]}" if why else "")]
    ok = n_cand >= min_n
    out = [row(t, "root.assets.param1", ok, [f"候选 {n_cand} 条"] + note, n_cand, f"先多下、多生再挑：每类候选 ≥{min_n}", "开题")]
    if cid == "beat":
        out.append(row(t, "beat.do1", ok, ([] if ok else [f"素材包候选 {n_cand} 条（本地 {n_local} 条）"]) + note,
                       dict({"candidates": n_cand, "local": n_local}, **({"min": min_n} if min_n != WORLD_MIN else {})), f"实拍候选 ≥{min_n} 条（按母题搜，Pexels 等）", "开题"))
    return out


# ----------------------------------------------------------------------------- 导演：节拍表
def beat_eval(t, cid, bm):
    out = []
    secs = bm.get("sections") or []
    en = [ENERGY.get(s.get("energy"), s.get("energy")) for s in secs]
    trough = [secs[i]["id"] for i in range(1, len(en) - 1)
              if all(isinstance(x, (int, float)) for x in en[i - 1:i + 2]) and en[i] < en[i - 1] and en[i] < en[i + 1]]
    out.append(row(t, "root.rhythm.do2", bool(trough), [] if trough else ["能量 " + " → ".join(f"{s.get('id')} {s.get('energy')}" for s in secs)],
                   trough, "每段标能量，至少一个低谷（比前后两段都低的一段）", "导演"))
    bpm = bm.get("bpm") or 150
    hk, wo, w = bm.get("hook_1s") or {}, bm.get("wow_7s") or {}, []
    if not (isinstance(hk, dict) and hk.get("bar") and len(str(hk.get("what") or "")) >= 2 and _frame(bpm, hk) < 60):
        w.append(f"hook_1s = {{bar, beat, what}}：第 1 秒（前 60 帧）里的变化（现在 {hk or '没写'}）")
    big = {(h.get("bar"), h.get("beat")) for h in bm.get("hits") or [] if h.get("kind") in ("big", "drop")}
    if not (isinstance(wo, dict) and (wo.get("bar"), wo.get("beat", 1)) in big and _frame(bpm, wo) < 420 and len(str(wo.get("what") or "")) >= 2):
        w.append(f"wow_7s = {{bar, beat, what}}：指向 7 s（420 帧）内一个 big 或 drop 卡点（现在 {wo or '没写'}）")
    out.append(row(t, "root.openend.do1", not w, w, how="节拍表标出第 1 秒的变化（hook_1s）、7 s 内的王炸（wow_7s）", stage="导演"))
    miss = [s.get("id") for s in secs if len(str(s.get("color_from") or "").strip()) < 2]
    out.append(row(t, "root.color.do1", not miss, [f"{x} 没写 color_from" for x in miss],
                   how="色表：每段写 color_from（这段的颜色长在哪个东西上：母题物、实拍原色、主角物的反光……）", stage="导演"))
    k = [h.get("kind") for h in bm.get("hits") or []]
    out.append(row(t, "root.rhythm.do1", k.count("breath") == 1 and k.count("drop") == 1 and k.count("final") == 1,
                   [f"屏息 {k.count('breath')}、DROP {k.count('drop')}、最后一击 {k.count('final')}"],
                   how="节拍表标出高潮（DROP）、高潮前的一次收（屏息）、最后一击，各一个", stage="导演"))
    if cid == "beat":
        b = 60.0 / bpm
        out.append(row(t, "beat.param7", 0.3 <= b <= 0.75, [f"屏息一拍 = {b:.2f} s"], round(b, 2), "屏息 0.3–0.75 s", "导演"))
        out.append(row(t, "beat.param3", 120 <= bpm <= 155, [f"BPM {bpm}"], bpm, "配乐 120–155 BPM（一拍最好是整数帧）", "导演"))
    return out


PLAN_MAP = {"p_label": ("root.type.floor4", False), "p_breath": ("beat.do4", None), "p_drop_hold": ("beat.param7", None),
            "p_cut_density": ("beat.param4", None), "p_first_second": ("beat.param5", None), "p_duration": ("beat.param2", None)}


def plan_rows(t, cid, checks):
    """gates.plan_checks 里和规则树对得上的几条（计划门禁的 hard / warn 照旧，这里只记进总账）。"""
    out = []
    for ck in checks:
        rid, hard = PLAN_MAP.get(ck["id"], (None, None))
        if rid and rid in t["_idx"] and t["_idx"][rid]["layer"] in ("root", cid):
            out.append(row(t, rid, ck["status"] in ("ok", "info"), ck.get("where") or [f"{ck['value']}（要求 {ck['want']}）"],
                           ck["value"], f"计划门禁 {ck['id']}：{ck['name']}", "镜头表",
                           hard=hard if hard is not None else ck.get("level") == "hard"))
    return out


# ----------------------------------------------------------------------------- 导演：镜头表
def shot_eval(t, cid, topic, bm, sl, series, clips=()):
    """sl = expand_shotlist 的结果（带 route / signature / deviations）。返回规则行（fail 发回导演，warn 提醒一次）。"""
    from . import stages as S
    out, shots, st = [], sl["shots"], "镜头表"
    devs = {d.get("id"): d.get("why") for d in sl.get("deviations") or [] if isinstance(d, dict) and d.get("why")}
    title = S._up((topic or {}).get("screen_title"))
    rt = str(sl.get("route") or "").strip()
    out.append(row(t, "root.transition.do1", rt in ROUTES, [] if rt in ROUTES else [f"现在 {rt or '没写'}"], rt,
                   "镜头表顶上写 route：连续 / 蒙太奇 / 混合 三选一", st))
    bad = [f"{s['id']}：{s.get('sustain') or '没写'}" for s in shots if not sustain_of(s.get("sustain"))]
    out.append(row(t, "root.camera.do2", not bad, bad, how="每镜写 sustain（怎么撑到出画）：" + " / ".join(SUSTAIN) + " 四选一", stage=st))
    bad = [f"{s['id']}：{s.get('action')}" for s in shots if s.get("action") and _cam_only(s["action"])]
    out.append(row(t, "root.camera.do1", not bad, bad, how="action 写主体自己发生了什么（裂开、生长、撞上、变形、点亮……）；只写推拉摇、字飞进来不算", stage=st))
    bad = [f"{s['id']}：{s.get('subject')}" for s in shots if _texture_only(s.get("subject"))]
    out.append(row(t, "root.subject.do1", not bad, bad, how="subject 是一个具体的东西；雾、肌理、渐变、背景、粒子、霜纹不算主体", stage=st))
    bad = [s["id"] for s in shots if S.shot_words(s) and (s.get("subject_src") or {}).get("kind") not in S.SUBJECT_KINDS]
    out.append(row(t, "root.subject.floor4", not bad, bad, how="有大字的镜头同时有实拍或生图主体（不许「一个大字 + 一层底纹」）", stage=st))
    # 字：词表（每个大字的字效）+ 同一种字效连着 ≤2 次
    bad = [s["id"] for s in shots if S.shot_words(s) and not all(type_fx(s))]
    out.append(row(t, "root.type.do2", not bad, [f"{x} 缺 type_fx" for x in bad],
                   how="词表：每镜大字写 type_fx = {font, entry, layout}（字体、出场方式、版式）", stage=st))
    runs, prev, k = [], None, 0
    for s in shots:
        cur = type_fx(s)
        k = k + 1 if (cur == prev and all(cur)) else 1
        if k == 3:
            runs.append(f"{s['id']} 和前两镜字效一样（{' / '.join(cur)}）")
        prev = cur
    out.append(row(t, "root.type.floor2", not runs, runs, how="同一种字效连着 ≤2 次：第 3 次把字体、出场方式、版式换一样（老系列定稿字体不拆，换出场和版式）", stage=st))
    # 转场
    dip, mix = [], []
    for s in shots:
        tx = s.get("transition_in") or {}
        txt = " ".join(str(tx.get(x) or "") for x in ("kind", "eye", "why"))
        dip += [f"{s['id']}：{w}" for w in _neg_hit(txt, DIP_WORDS)]
        mix += [f"{s['id']}：{w}" for w in _neg_hit(txt, MIX_WORDS)]
    out.append(row(t, "root.transition.floor1", not dip, dip, how="转场声明里不拿闪黑闪白、渐黑、淡入淡出当转场", stage=st))
    out.append(row(t, "root.transition.floor2", not mix, mix, how="转场声明里不写叠化 / 溶解", stage=st))
    kinds = [((s.get("transition_in") or {}).get("kind") or "") for s in shots[1:]]
    runs, k = [], 1
    for i in range(1, len(kinds)):
        k = k + 1 if (kinds[i] == kinds[i - 1] and kinds[i] and kinds[i] != "拍上硬切") else 1
        if k == 3:
            runs.append(f"{shots[i + 1]['id']}：第 3 次连用「{kinds[i]}」")
    out.append(row(t, "root.transition.floor3", not runs, runs, how="同一种转场招式（拍上硬切不算）连用 ≤2 次", stage=st))
    hc = [s["id"] for s in shots[1:] if (s.get("transition_in") or {}).get("kind") == "拍上硬切"
          and len(str((s.get("transition_in") or {}).get("why") or "").strip()) < 4]
    out.append(row(t, "root.transition.do3", not hc, hc, how="拍上硬切写理由（why：落在大拍上 / 被冲击帧盖住 / 形状对位 / 主角钉在中心）", stage=st))
    dist = sorted({x for x in kinds if x and x != "拍上硬切"})
    out.append(row(t, "root.transition.param1", 2 <= len(dist) <= 3, [f"用了 {len(dist)} 种：{'、'.join(dist)}"], len(dist),
                   "一部片 2–3 种转场手法反复用（拍上硬切不算）", st, why=devs.get("root.transition.param1")))
    # 特效：事件、签名镜头、黑名单；画质黑名单
    bad = [s["id"] for s in shots if len(str(s.get("event") or "").strip()) < 4]
    out.append(row(t, "root.fx.do1", not bad, bad, how="每镜写 event：这一镜的动画事件，挂在主体上（为什么在这：剧情或卡点）", stage=st))
    sg = sl.get("signature") if isinstance(sl.get("signature"), dict) else {}
    okg = sg.get("shot") in {s["id"] for s in shots} and len(str(sg.get("why") or "").strip()) >= 6
    out.append(row(t, "root.fx.do2", okg, [] if okg else [f"现在 {sg or '没写'}"], sg.get("shot"),
                   "镜头表顶上写 signature = {shot, why}：全片 1 个即梦、可灵做不出来的签名镜头，why 写为什么做不出来", st))
    fx_bad, look_bad = [], []
    for s in shots:
        tx = s.get("transition_in") or {}
        txt = " ".join(str(s.get(x) or "") for x in ("subject", "action", "event", "label", "image")) + " " + \
            " ".join(map(str, s.get("words") or [])) + " " + " ".join(str(tx.get(x) or "") for x in ("eye", "why"))
        fx_bad += [f"{s['id']}：{w}" for w in _neg_hit(txt, FX_BAN)]
        look_bad += [f"{s['id']}：{w}" for w in _neg_hit(txt, LOOK_BAN)]
    out.append(row(t, "root.fx.floor2", not fx_bad, fx_bad, how="否掉过的不再出现（点连线、准星、锁形框、声波环、虚线、扩散波纹、贴纸、彩纸、黑洞吸人……）", stage=st))
    out.append(row(t, "root.quality.floor3", not look_bad, look_bad, how="不拿颗粒、扫描线、色差、老电视当风格", stage=st))
    # 素材：主体实拍 ≥1080p（背景只报警）
    cl = {c["name"]: c for c in clips or []}
    low_s, low_b = [], []
    for s in shots:
        src = s.get("subject_src") or {}
        for nm, lst in ((src.get("file") if src.get("kind") == "实拍" else None, low_s), (s.get("footage"), low_b)):
            c = cl.get(nm)
            if c and c.get("w") and c.get("h") and min(c["w"], c["h"]) < 1080:
                lst.append(f"{s['id']} {nm} {c['w']}×{c['h']}")
    out.append(row(t, "root.assets.floor1", not low_s, low_s, how="主体实拍 ≥1080p", stage=st))
    if low_b:
        out.append(row(t, "root.assets.floor1", False, low_b, how="背景实拍低于 1080p（只报警）", stage=st, hard=False))
    if cid == "beat":
        firsts = [s for i, s in enumerate(shots) if i and s["section"] != shots[i - 1]["section"]]
        bad = [f"{s['id']}（{s['section']} 第一镜）" for s in firsts
               if ((s.get("transition_in") or {}).get("kind") or "拍上硬切") == "拍上硬切" and "钉" not in str((s.get("transition_in") or {}).get("why"))]
        out.append(row(t, "beat.do3", not bad, bad, how="段与段之间用一次招牌转场（万花筒、冲击帧盖切点、推穿……；拍上硬切只在「主角钉在中心、世界在身后换」时算）", stage=st))
        hitf = [h["F"] for s in shots for h in s.get("hits") or [] if h.get("kind") in ("big", "drop", "final", "small")]
        bad = []
        for s in shots[1:]:
            tx = s.get("transition_in") or {}
            if tx.get("kind") != "拍上硬切":
                continue
            covered = any(abs(f - s["start_f"]) <= 2 for f in hitf)
            shaped = any(w in str(tx.get("why") or "") + str(tx.get("eye") or "") for w in ("对位", "形状", "钉", "同一位置", "同一大小"))
            if not (covered or shaped):
                bad.append(f"{s['id']} 入点 {s['start_f']}：±2 帧内没有卡点，why / eye 也没写形状怎么对上")
        out.append(row(t, "beat.floor2", not bad, bad, how="拍上硬切要真兜住：切点 ±2 帧内有冲击帧（节拍表的卡点），或在 why / eye 写清形状怎么对上", stage=st))
        ok5 = bool(title) and bool(shots) and title in S.shot_words(shots[-1])
        out.append(row(t, "beat.do5", ok5, [] if ok5 else [f"最后一镜 {shots[-1]['id'] if shots else '?'} 的大字里没有片名 {title}"],
                       how="收在片名海报帧上：最后一镜 = 主角物 + 片名大字", stage=st))
        per = {}
        for s in shots:
            for F in [s["start_f"]] + list(s.get("cuts_f") or []) + [h["F"] for h in s.get("hits") or []]:
                b = int(F // bm["bar_f"]) + 1
                per[b] = per.get(b, 0) + 1
        vals = [per.get(b, 0) for b in range(1, bm["bars"] + 1)]
        out.append(row(t, "beat.floor4", len(set(vals)) > 1, [f"每小节画面事件数都是 {vals[0] if vals else 0}"], vals,
                       "不平均：每小节的画面事件数（切点 + 卡点）不能全一样", st))
        bad = [s["id"] for s in shots if not (s.get("subject") and s.get("hero_word") and s.get("label"))]
        out.append(row(t, "beat.param6", not bad, bad, how="每帧三层：主体 + 主角大字 + 一层小标签", stage=st, why=devs.get("beat.param6")))
    return out


# ----------------------------------------------------------------------------- 成片门禁 → 规则
def video_rows(t, cid, checks, stage="成片"):
    by = {c["id"]: c for c in checks}
    out = []
    for rid, ids in VIDEO.items():
        if rid not in t["_idx"] or t["_idx"][rid]["layer"] not in ("root", cid):
            continue
        cs = [by[i] for i in ids if i in by]
        if not cs:
            continue
        worst = max(cs, key=lambda c: {"fail": 3, "warn": 2, "info": 1, "ok": 0, "skip": 0}.get(c["status"], 0))
        hard = any(c.get("level") == "hard" for c in cs)
        ok = worst["status"] in ("ok", "info", "skip")
        where = [f"{c['name']}：{c['value']}（要求 {c['want']}）" for c in cs if c["status"] in ("warn", "fail")]
        where += [w for c in cs if c["status"] in ("warn", "fail") for w in (c.get("where") or [])[:3]]
        r = row(t, rid, ok, where, "、".join(i for i in ids if i in by), "成片门禁 " + "、".join(i for i in ids if i in by), stage, hard=hard)
        if not ok and worst["status"] == "warn" and hard:
            r["status"] = "warn"                                     # 制作中途：硬门禁先记 warn（交付时才 fail）
        out.append(r)
    return out


def gate_level_errors(t, hard_ids):
    """一致性：门禁的 hard 名单和规则树的档位对得上（selftest 用）。返回对不上的说明。"""
    errs = [f"{rid} 不在 tree.json" for rid in VIDEO if rid not in t["_idx"]]
    for i in sorted({x for v in VIDEO.values() for x in v}):
        tiers = {t["_idx"][r]["tier"] for r, v in VIDEO.items() if i in v and r in t["_idx"]}
        want = bool(tiers & {"do", "floor", "flow"}) and i in STABLE
        if want != (i in hard_ids):
            errs.append(f"{i}：挂在{'、'.join(TIER[x] for x in sorted(tiers))}档、{'稳' if i in STABLE else '不稳'}，"
                        f"应该{'是' if want else '不是'} hard")
    return errs


# ----------------------------------------------------------------------------- 总账
ORDER = {"fail": 5, "warn": 4, "info": 3, "ok": 2, "human": 1, "na": 0}


def ledger(t, cid, rows):
    """根 + 本大类每条规则一行：查过的取最坏那次（位置合并）；没查的标「人判」（交给审片）或「未接」。"""
    by = {}
    for r in rows:
        cur = by.get(r["id"])
        if cur is None:
            by[r["id"]] = dict(r, stages=[r["stage"]])
            continue
        cur["stages"] = list(dict.fromkeys(cur["stages"] + [r["stage"]]))
        if ORDER[r["status"]] > ORDER[cur["status"]]:
            keep = cur["stages"]
            cur.clear()
            cur.update(r, stages=keep)
        elif r["status"] in ("fail", "warn") and r["where"]:
            cur["where"] = (cur.get("where") or []) + r["where"][:4]
    for rid, it in t["_idx"].items():
        if it["layer"] not in ("root", cid) or rid in by:
            continue
        ck = it.get("check") or {}
        human = "人判" in ck.get("status", "")
        by[rid] = {"id": rid, "tier": TIER.get(it["tier"], it["tier"]), "dim": t["dims"].get(it["dim"], it["dim"]),
                   "level": level(t, rid), "status": "human" if human else "na", "stage": "审片" if human else "",
                   "how": ck.get("how", ""), "value": None, "where": [], "rule": _clean(it.get("text", ""))[:90],
                   "note": "交给审片看图判" if human else ("未接：" + (ck.get("status_note") or ck.get("status") or "没有检查"))}
    dims = list(t["dims"])
    tiers = list(TIER)
    rws = sorted(by.values(), key=lambda r: (0 if r["id"].startswith("root.") else 1,
                                             dims.index(t["_idx"][r["id"]]["dim"]) if r["id"] in t["_idx"] else 99,
                                             tiers.index(t["_idx"][r["id"]]["tier"]) if r["id"] in t["_idx"] else 9, r["id"]))
    summ = {}
    for r in rws:
        s = summ.setdefault(r["tier"], {"rules": 0, "ok": 0, "fail": 0, "warn": 0, "info": 0, "human": 0, "na": 0})
        s["rules"] += 1
        s[r["status"]] += 1
    return {"tree": t.get("version"), "category": cid, "category_name": cat_name(t, cid), "summary": summ, "rows": rws}


def summary_line(led):
    parts = []
    for tier, s in led["summary"].items():
        bits = [f"做到 {s['ok']}"] + ([f"没做到 {s['fail']}"] if s["fail"] else []) + ([f"报警 {s['warn']}"] if s["warn"] else []) \
            + ([f"偏离有理由 {s['info']}"] if s["info"] else []) + ([f"人判 {s['human']}"] if s["human"] else []) \
            + ([f"未接 {s['na']}"] if s["na"] else [])
        parts.append(f"{tier} {s['rules']} 条（{'、'.join(bits)}）")
    return "；".join(parts)
