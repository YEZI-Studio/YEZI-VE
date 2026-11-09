# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""素材请求通道 + 开拍前素材盘点。「这种东西不要向我确认呀，你反正自己去 pexels 之类的……素材网站上去找啊，
包括他们也应该自己要会去找啊」。照生图通道（lib/imagegen.py）的写法，默认开（DeepSeek、Codex 都一样；系列配置 stock.enabled、
环境变量 STOCK_OFF=1 关）：

  1. 代理缺真材料时在 work/out/footage_requests.json 写需求：
       [{need: 一句话描述画面, use: overlay | world | subject, seconds, why, query?: 英文搜索词, kind?: 叠层种类}]
     每镜每阶段（制作 / 返修）最多 per_shot 条、找 rounds_per_shot 次（系列 assets.rounds_per_shot，默认 3），全片最多 per_film 条。
  2. 代理跑完这一次，编排器在沙箱外调 p2/tools/stock.py（F 维护；独立进程、引擎 Python）：search（Pexels、Wikimedia Commons……，
     只要免费授权）→ 文字对题（lib/stockrel：标题 / 描述 vs need，橙子当碎屑、倒水当碎玻璃这类直接拒）→ 挑一条（1080p 附近、
     够长、横屏、叠层优先黑底；这一镜给过的不再给）→ fetch（下进素材包 footage/，sources.json / CATALOG.md 记来源和授权）→ 出到货联系表
     （<素材包>/_review/stock_<文件>.jpg）→ overlay 类再 bake_overlay（自动烘焙进素材包的 overlays.json）；然后 inputs.ensure 把新素材
     接进这一集的清单（新编号 F..），引擎素材目录刷新。
  3. 找到了，马上再叫一次代理「素材到了，接进去」（新素材名、Take / SR.element 用法；ctx/ 里的素材表、叠层表换成新的，联系表在
     ctx/look/stock/）；代理看图觉得不对题，改写那一条的 need / query 再提，同一阶段接着找（轮数用完为止）。找不到只报告、
  4. 开拍前盘点 inventory（stages 在第 2、3 环节开头调；按镜头表指纹只做一次）：按题材（母题素材包、系列 stock.motif_needs、开题的
     footage_needs）和镜头表（每镜的主体 / 世界层 / 叠层、导演写的 footage_wish）查素材包；缺的自动去找（inventory_max 条以内），
     找不到的记进 stock_inventory.json 和交付报告。
  5. 镜头表的待补素材（空包起跑时镜头表不死锁）：镜头表的 footage_wish 可以带 request_id，subject_src / footage / aux_footage
     引用它（还没有 F 号；只在 event 里提到的不算引用）。镜头表通过后 stages.fill_wishes 调 resolve_wishes 先按代理写的 need / query 找
     （被引用的优先、找过的不重找、计入盘点上限），找到的登记成新 F 号，rebind 把镜头表里的 id 换成 F 号；没找到的 failed_lines
     给发回镜头表的说明（发回、停由 stages 定）；联系表跟着进引用它那一镜的 ctx/look/stock/（wish_arrived，制作代理看图再用）。镜头表的校验（缺 need / query、id 不唯一）归 stages.wish_index / wish_errs。
  开题的 footage_needs 每条可以是一句话（旧写法），也可以是 {need, query, use, seconds}：有代理的 query 就用，en_query 只兜底。

mock / STOCK_FAKE=1：stock.py 用假站点（不联网），下载 = 复制一条已有素材；素材包换成本集的 <集>/stock_pack/（不碰 p2/assets/）。
登记：<集>/inputs/stock.json（每条请求的来源、授权、状态）；每镜 work/assets/stock/manifest.json；盘点 <集>/stock_inventory.json。
不读、不打印、不记任何 key：搜索 API key 由 stock.py 在运行时自己从环境变量 / 配置文件读，这里只把同名环境变量原样传给子进程。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading

from . import inputs, stockrel as REL, util

LOCK = threading.Lock()                         # 几镜并行时排队：素材包、全片登记、inputs/footage.json 是共享的

REQ = os.path.join("out", "footage_requests.json")
MAN = os.path.join("assets", "stock", "manifest.json")
REG = os.path.join("inputs", "stock.json")
INV = "stock_inventory.json"
CTX = os.path.join("ctx", "stock.json")
LOOK = os.path.join("ctx", "look", "stock")                    # 到货联系表（每条一张 <素材名>.jpg；叠层另有烘焙检查图 <素材名>_<kind>.jpg）
STOCK_PY = os.path.join(util.P2, "tools", "stock.py")
USES = ("overlay", "world", "subject")
USE_CN = {"overlay": "叠层", "world": "世界层", "subject": "主体"}
_USE_ALIAS = {"叠层": "overlay", "元素": "overlay", "element": "overlay", "elements": "overlay", "叠加": "overlay", "overlays": "overlay",
              "世界": "world", "世界层": "world", "背景": "world", "bg": "world", "background": "world", "底": "world", "辅助": "world",
              "aux": "world", "主体": "subject", "主角": "subject", "hero": "subject"}
DEFAULTS = {"enabled": True, "per_shot": 3, "per_film": 12, "rounds_per_shot": 3, "followup": True, "inventory": True,
            "inventory_max": 6, "candidates": 12, "pack_min_clips": 10, "sites": None, "seconds": 4.0, "motif_needs": {},
            "max_mb": 300, "timeout_s": {"search": 180, "fetch": 900, "bake_overlay": 900, "sheet": 180}}
BY = "stockreq"                                 # 自动通道写进叠层表的窗口带 by = stockreq（stock.py 不让它碰人工整理过的 kind）
OK_ST = ("ok", "reused")                        # 找到了；failed = 没找到（只报告）；deferred = 站点限流 / 超时 / 出错，下一轮 / 下次盘点再试；
#                                                 skipped_cap = 超过每镜 / 全片上限没找（也进报告）
WISH_KEYS = ("request_id", "shots")             # 镜头表待补素材（resolve_wishes）的登记条目另带：哪个 request_id、哪几镜引用

# 真材料种类：kind（叠层表里的名字）、认得出的中文词、英文搜索词、叠层表里算同一种的 kind 名片段。顺序有用：先认长词（海雪先于雪）。
MATERIALS = [
    ("marine_snow", ("海雪", "悬浮颗粒", "悬浮微粒", "水中颗粒", "浮尘", "悬浮物", "闪粒"), "marine snow particles underwater dark",
     ("marine", "seasnow", "particle", "particulate")),
    ("glints", ("闪点", "波光粼粼", "阳光闪", "闪光点"), "sun glints sparkle water surface", ("glint", "sparkle")),   # 海面阳光闪点 ≠ 海雪
    ("jellies", ("水母",), "jellyfish black background", ("jell",)),
    ("plankton", ("浮游", "发光生物", "生物发光", "荧光生物", "夜光藻"), "bioluminescent plankton dark water", ("plankton", "biolum", "glow")),
    ("shrimp", ("磷虾", "小虾", "虾"), "shrimp krill underwater dark", ("shrimp", "krill", "prawn")),   # 生物叠层
    ("bubbles", ("气泡", "泡泡"), "bubbles rising black background", ("bubble",)),
    ("rays", ("光束", "丁达尔", "光柱", "耶稣光"), "underwater light rays dark", ("ray", "beam", "godray")),
    ("caustics", ("焦散", "水纹光", "波光", "水面光纹"), "water caustics light pattern", ("caustic", "surface")),
    ("ink", ("墨团", "墨汁", "墨水", "墨云", "沉积物", "泥沙", "扬尘"), "ink in water black background", ("ink", "silt", "sediment")),
    ("shards", ("冰屑", "碎冰", "冰碴", "冰渣", "碎屑"), "crushed ice slow motion black background", ("shard", "debris", "crush", "burst")),
    ("diamond_dust", ("冰尘", "钻石尘", "冰晶尘"), "ice crystals glitter backlit black background", ("diamond", "icedust", "ice_dust", "glitter")),
    ("snow", ("雪",), "snow falling black background", ("snow",)),
    ("fog", ("雾", "烟", "白汽", "蒸汽", "水汽"), "smoke fog black background", ("fog", "smoke", "mist", "steam", "vapor")),
    ("drip", ("水滴", "滴水", "水珠"), "water drops falling black background", ("drip", "drop")),
    ("sparks", ("火花", "火星"), "sparks slow motion black background", ("spark",)),
    ("dust", ("尘埃", "灰尘", "粉尘"), "dust particles backlit black background", ("dust",)),
    ("powder", ("彩粉", "色粉", "粉末爆", "粉爆", "颜料粉"), "colored powder explosion black background", ("powder", "holi")),
    ("rubble", ("砖渣", "碎砖", "瓦砾", "碎石", "石渣", "墙渣", "砖屑"), "debris falling black background", ("rubble", "brick", "debris", "concrete")),
    ("glass", ("碎玻璃", "玻璃碎", "玻璃渣", "玻璃碴"), "glass shatter black background slow motion", ("glass",)),
]
MAT = {k: {"zh": zh, "en": en, "syn": syn} for k, zh, en, syn in MATERIALS}
_IGNORE = ("雪花", "雪山", "雪白", "烟花")
MOTIF_EN = {"火": "fire", "电": "electricity lightning", "光": "light", "水": "water",
            "金属": "metal", "沙": "sand desert", "云": "clouds", "星": "stars space"}
VOCAB = {
    "黑底": "black background", "深色底": "dark background", "暗底": "dark background", "慢动作": "slow motion", "延时": "time lapse",
    "微距": "macro", "逆光": "backlit", "特写": "close up", "俯拍": "top view", "航拍": "aerial", "全景": "wide shot",
    "深海": "deep sea", "海底": "seabed", "水下": "underwater", "海面": "ocean surface", "水面": "water surface", "海浪": "ocean waves",
    "水母": "jellyfish", "鱼群": "school of fish", "鲸": "whale", "鲨": "shark", "珊瑚": "coral", "章鱼": "octopus", "潜水": "diving",
    "深渊": "abyss", "海沟": "ocean trench", "海水": "sea water", "水体": "water", "漂移": "drifting", "上升": "rising", "下沉": "sinking",
    "漂浮": "floating", "冰山": "iceberg", "冰川": "glacier", "冰层": "ice sheet", "冰面": "ice surface", "冰纹": "ice pattern",
    "冰块": "ice cube", "冰晶": "ice crystal", "结冰": "freezing", "冻结": "freezing", "融化": "melting", "崩塌": "collapse",
    "裂纹": "cracks", "裂开": "cracking", "霜": "frost", "暴风雪": "blizzard", "极光": "aurora", "液氮": "liquid nitrogen",
    "干冰": "dry ice", "酒杯": "glass", "玻璃": "glass", "透光": "light shining through", "生长": "growing", "火焰": "flames",
    "熔岩": "lava", "岩浆": "magma", "铁水": "molten metal", "金属": "metal", "闪电": "lightning", "电弧": "electric arc",
    "霓虹": "neon", "城市": "city", "夜景": "night city", "星空": "starry sky", "烟花": "fireworks", "爆炸": "explosion",
    "溅": "splash", "水花": "splash", "颜料": "paint", "风": "wind", "冰": "ice", "火": "fire", "光": "light", "云": "clouds", "沙": "sand",
    "热液喷口": "hydrothermal vent", "热液": "hydrothermal", "喷口": "vent", "黑烟囱": "black smoker", "烟柱": "smoke plume",
    "纸屑": "confetti", "碎片": "shards", "碎块": "debris chunks", "炸开": "explosion", "爆散": "explosion burst", "碎裂": "shatter",
    "破碎": "shatter", "砸碎": "smash", "撞碎": "smash", "橡胶": "rubber", "浑浊": "murky",
    "火山": "volcano", "喷发": "eruption", "钢水": "molten steel", "雪花": "snowflake snow",
    # 最长匹配先认整词，「火箭」「火车」不拆出「火」= fire
    "火箭": "rocket", "火车": "train"}   # 雪花：materials_in 照旧不当叠层（_IGNORE），对题时算雪
_RE_CJK = re.compile(r"[㐀-鿿]")


def cfg(c):
    ser = getattr(c, "series", None) or {}
    d = dict(DEFAULTS)
    d.update(ser.get("stock") or {})
    d["timeout_s"] = dict(DEFAULTS["timeout_s"], **(d.get("timeout_s") or {}))
    # 一镜每阶段找几次：系列 assets.rounds_per_shot（看了到货联系表能改写 need 再找，默认 3）；旧的 stock.rounds_per_shot 不再读
    d["rounds_per_shot"] = int((ser.get("assets") or {}).get("rounds_per_shot") or DEFAULTS["rounds_per_shot"])
    return d


def enabled(c):
    """默认开。关：系列配置 stock.enabled = false、环境变量 STOCK_OFF=1、命令行 --stock off（有这个参数时）。"""
    a = getattr(getattr(c, "args", None), "stock", None)
    if a in ("off", False) or os.environ.get("STOCK_OFF") == "1":
        return False
    return bool(cfg(c).get("enabled", True))


def fake(c):
    """mock、STOCK_FAKE=1：stock.py 用假站点（不联网），素材包换成本集的 <集>/stock_pack/。"""
    return bool(getattr(c, "mock", False)) or os.environ.get("STOCK_FAKE") == "1"


def mode(c):
    return "off" if not enabled(c) else ("fake" if fake(c) else "net")


def motif(c):
    return str((c.j("topic.json") or {}).get("motif") or "").strip()


def pack_for(c):
    """下载进哪个素材包：真跑 = 母题素材包 p2/assets/<母题>/（没有就建）；mock / STOCK_FAKE = 本集的 <集>/stock_pack/。"""
    if fake(c):
        d = c.p(inputs.LOCAL_PACK)
    else:
        m = re.sub(r"[/\\\0]", "", inputs.pack_name(c.series, motif(c)) or "") or "_stock"
        d = os.path.join(util.PARENT, c.series["assets"]["dir"], m)
    os.makedirs(os.path.join(d, "footage"), exist_ok=True)
    if not os.path.isfile(os.path.join(d, "sources.json")):
        util.write_json(os.path.join(d, "sources.json"), {"motif": motif(c), "items": [],
                                                           "note": "素材请求通道建的素材包（p2/tools/stock.py 下载、记来源和授权）"})
    return d


# ----------------------------------------------------------------------------- 调 stock.py（独立进程）
RUNNER = r'''
import json, sys, traceback
sys.path.insert(0, sys.argv[1])
import stock
with open(sys.argv[2], encoding="utf-8") as fh:
    a = json.load(fh)
try:
    r = getattr(stock, a["fn"])(*a.get("args", []), **a.get("kw", {}))
except Exception as e:
    traceback.print_exc()
    sys.stdout.write("\n@@STOCKERR@@ " + json.dumps({"type": type(e).__name__, "msg": str(e)[:300]}, ensure_ascii=False) + "\n")
    sys.exit(1)
sys.stdout.write("\n@@STOCK@@ " + json.dumps(r, ensure_ascii=False, default=str) + "\n")
'''
_PASS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "all_proxy", "no_proxy", "SSL_CERT_FILE",
         "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "LANG", "LC_ALL", "TMPDIR")
_PASS_PREFIX = ("PEXELS", "PIXABAY", "STOCK_", "COVERR", "MIXKIT", "WIKIMEDIA", "COMMONS")


def _py(c):
    for p in (getattr(getattr(c, "renderer", None), "py", None),
              os.path.join(util.PARENT, ((getattr(c, "series", None) or {}).get("engine") or {}).get("python") or "_")):
        if p and os.path.exists(p):
            return p
    return sys.executable


def _env(c):
    """子进程环境：PATH / HOME + 代理、证书 + 素材站相关的环境变量原样传（key 由 stock.py 自己读，这里不看值、不记）。"""
    env = {"PATH": util.BIN_PATH, "HOME": util.HOME, "PYTHONDONTWRITEBYTECODE": "1"}
    env.update({k: v for k, v in os.environ.items() if k in _PASS or k.upper().startswith(_PASS_PREFIX)})
    if fake(c):
        env["STOCK_FAKE"] = "1"
    return env


def call(c, fn, *args, **kw):
    """在沙箱外跑 p2/tools/stock.py 的一个函数（search / fetch / bake_overlay），独立进程、引擎 Python。返回 (结果, 错误)；
    错误串以异常类名开头（RateLimited: … = 站点限流，算「稍后再下」）。等子进程时每 0.2 s 看一次 util.HALT（Ctrl-C / SIGTERM
    收工时杀掉子进程树，不干等到超时）。输出尾巴进 <集>/logs/stock.log。selftest 会把这个函数换成假的。"""
    import tempfile
    from .render import run_watched
    if not os.path.exists(STOCK_PY):
        return None, f"没有 {os.path.relpath(STOCK_PY, util.ROOT)}"
    lp = c.p("logs", "stock.log")
    os.makedirs(os.path.dirname(lp), exist_ok=True)
    t = cfg(c)["timeout_s"].get(fn, 600)
    with tempfile.TemporaryDirectory(prefix="stockreq-") as td:
        ip, op = os.path.join(td, "in.json"), os.path.join(td, "out.txt")
        with open(ip, "w", encoding="utf-8") as fh:
            json.dump({"fn": fn, "args": list(args), "kw": kw}, fh, ensure_ascii=False, default=str)
        try:
            with open(op, "w", encoding="utf-8") as fo:
                rc, why = run_watched([_py(c), "-I", "-c", RUNNER, os.path.dirname(STOCK_PY), ip], cwd=util.P2, env=_env(c),
                                      stdout=fo, stderr=subprocess.STDOUT, timeout=t)
        except Exception as e:  # noqa: BLE001
            return None, f"stock.{fn} 起不来：{type(e).__name__}: {str(e)[:160]}"
        out = util.read(op) if os.path.exists(op) else ""
    with util.LOCK, open(lp, "a", encoding="utf-8") as f:
        f.write(f"\n[{util.iso()}] stock.{fn} rc={rc}{' ' + why if why else ''}\n" + out[-4000:].split("@@STOCK@@")[0][-3000:])
    if why == "interrupted":
        raise KeyboardInterrupt
    if why == "timeout":
        return None, f"Timeout: stock.{fn} 超时（{t}s）"
    if "@@STOCKERR@@" in out:
        try:
            e = json.loads(out.rsplit("@@STOCKERR@@", 1)[1].strip().splitlines()[0])
            return None, f"{e.get('type')}: {e.get('msg')}"
        except (ValueError, IndexError):
            pass
    if "@@STOCK@@" not in out:
        return None, f"stock.{fn} 出错（rc {rc}）：" + (out.strip().splitlines() or ["没有输出"])[-1][:200]
    try:
        return json.loads(out.rsplit("@@STOCK@@", 1)[1].strip().splitlines()[0]), None
    except (ValueError, IndexError) as e:
        return None, f"stock.{fn} 的结果读不了：{e}"


_TRANSIENT = ("RateLimited", "限流", "Timeout", "起不来", "出错（rc", "读不了")


def deferred_err(err):
    """临时失败：站点限流（Commons upload.wikimedia.org 429 / Retry-After 600 s 之类）、超时、stock.py 子进程起不来 / 出错 / 结果读不了。
    不算没找到（登记 deferred，不按 need / query 缓存成「没找到」），下一轮 / 下次 resolve / 下次盘点再试。"""
    return bool(err) and any(k in str(err) for k in _TRANSIENT)


# ----------------------------------------------------------------------------- 请求
def _txt(v, n):
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def norm_use(u):
    u = str(u or "").strip().lower()
    return u if u in USES else _USE_ALIAS.get(u, "")


def _sha(r):
    return hashlib.sha256(json.dumps([r["use"], r["need"], r.get("query", ""), r.get("kind", "")], ensure_ascii=False).encode()).hexdigest()[:12]


def norm_request(x, i, default_s=4.0):
    """一条请求（dict 或一句话）→ {id, need, use, seconds, why, query, kind, sha}；不合格返回 (None, 提醒)。"""
    if isinstance(x, str):
        x = {"need": x}
    if not isinstance(x, dict):
        return None, f"第 {i + 1} 条不是对象，跳过"
    need = _txt(x.get("need") or x.get("what"), 200)
    if not need:
        return None, f"第 {i + 1} 条没写 need（一句话描述画面），跳过"
    note = ""
    use = norm_use(x.get("use"))
    if not use:
        note = f"第 {i + 1} 条：use「{x.get('use')}」不认识，按 world（世界层）找（只认 overlay / world / subject）"
        use = "world"
    try:
        sec = float(x.get("seconds") or default_s)
    except (TypeError, ValueError):
        sec = default_s
    q = _txt(x.get("query"), 100)
    kind = re.sub(r"[^a-z0-9_]+", "_", str(x.get("kind") or "").strip().lower()).strip("_")[:24]
    r = {"need": need, "use": use, "seconds": round(min(30.0, max(1.0, sec)), 1), "why": _txt(x.get("why"), 200), "query": q,
         "kind": kind if use == "overlay" else ""}
    r["sha"] = _sha(r)
    return r, note


def topic_need(x, why=""):
    """开题 footage_needs 的一条 → 请求（交给 norm_request）。字符串 = 旧写法（只有中文，搜索词靠 en_query 兜底）；对象
    {need, query, use: world | subject | overlay, seconds} = 代理自己写英文搜索词，use 没写 / 不认识按 world。"""
    if isinstance(x, dict):
        r = {k: x.get(k) for k in ("need", "query", "seconds", "kind") if x.get(k) not in (None, "")}
        r.update(use=norm_use(x.get("use")) or "world", why=_txt(x.get("why"), 200) or why)
        r["need"] = _txt(r.get("need") or x.get("what"), 120)
        return r
    return {"need": _txt(x, 120), "use": "world", "why": why}


def parse_requests(work):
    """读 work/out/footage_requests.json → (请求列表, 提醒)。认 [..]、{"requests": [..]}、{"footage": [..]}。"""
    p = os.path.join(work, REQ)
    if not os.path.exists(p):
        return [], []
    try:
        raw = json.loads(util.read(p))
    except ValueError as e:
        return [], [f"out/footage_requests.json 不是合法 JSON（{str(e)[:80]}），这次没找"]
    items = raw if isinstance(raw, list) else (raw.get("requests") or raw.get("footage") if isinstance(raw, dict) else None)
    if not isinstance(items, list):
        return [], ["out/footage_requests.json 要写成 [{need, use, seconds, why, query}, ...]，这次没找"]
    out, notes, seen = [], [], set()
    for i, x in enumerate(items):
        r, note = norm_request(x, i)
        if note:
            notes.append(note)
        if not r:
            continue
        if r["sha"] in seen:
            notes.append(f"第 {i + 1} 条和前面重复，只收一条")
            continue
        seen.add(r["sha"])
        r["id"] = f"r{len(out) + 1}"
        out.append(r)
    return out, notes


def manifest(work):
    m = util.read_json(os.path.join(work, MAN)) if work else None
    return m or {"rounds": 0, "rounds_by": {}, "requests": []}


def phase_of(job_or_kind):
    """制作（shot）和返修（fix）各算各的轮数：制作时找过一次，返修时照样能再提一次（05 号提示词就是这么写的）。"""
    k = job_or_kind.get("kind") if isinstance(job_or_kind, dict) else job_or_kind
    return "fix" if k == "fix" else "make"


def rounds_used(man, phase):
    rb = man.get("rounds_by") or {}
    if phase in rb:
        return int(rb[phase] or 0)
    return int(man.get("rounds") or 0) if phase == "make" and not rb else 0     # 老 manifest 只有 rounds：算制作的


def film(c):
    return c.j(REG) or {"note": "素材请求通道登记（编排器写）：requests = 每条需求（代理 agent / 开拍前盘点 inventory）找没找到、下到哪、来源授权"
                               "（status：ok / reused 找到；failed 没找到；deferred 站点限流 / 超时 / 出错、下一轮再试；skipped_cap 超上限没找）；"
                               "calls = 每次去找（几条、用时、假站点还是联网）。全片上限按 from=agent 找过的条数计。", "requests": [], "calls": []}


def used(c, origin="agent"):
    return sum(1 for x in film(c).get("requests") or [] if x.get("from") == origin and x.get("status") not in ("skipped_cap", "deferred"))


def pending(c, work, reqs, phase="make"):
    """还要找的：footage_requests.json 里原样留着的旧需求（同一个 need / query / kind）已经处理过，跳过（限流推迟的 deferred 再试）；
    代理改写了 need / query 就是新需求，照找（这一镜给过的素材不会再给）。一镜每个阶段（制作 / 返修）找 rounds_per_shot 次、
    最多 per_shot 条；全片 per_film 条。返回 (todo, 提醒, 超上限没找的)。"""
    g = cfg(c)
    man = manifest(work)
    done = {}
    for x in man["requests"]:
        done[x.get("sha")] = x
    todo, notes, cap = [], [], []
    for r in reqs:
        old = done.get(r["sha"])
        if old and old.get("status") not in ("deferred", "skipped_cap"):
            notes.append(f"{r['id']}「{r['need'][:30]}」已经找过（{'找到 ' + str(old.get('name')) if old.get('status') in OK_ST else '没找到'}）；"
                         "要换一条就改写 need / query")
            continue
        todo.append(r)
    retry_only = bool(todo) and all((done.get(r["sha"]) or {}).get("status") == "deferred" for r in todo)   # 只是重试限流推迟的：不占轮数
    if todo and not retry_only and rounds_used(man, phase) >= int(g["rounds_per_shot"]):
        why = f"这一镜{'返修' if phase == 'fix' else '制作'}时已经找过 {rounds_used(man, phase)} 次素材（每阶段最多 {g['rounds_per_shot']} 次）"
        notes.append(f"{why}，这次的 {len(todo)} 条没找")
        return [], notes, [dict(r, cap=why) for r in todo]
    have = sum(1 for x in man["requests"] if x.get("status") not in ("deferred", "skipped_cap") and (x.get("phase") or "make") == phase)
    room = max(0, int(g["per_shot"]) - have)                        # 每镜条数也按阶段算：返修时不会因为制作时用满了就提不了
    for r in todo[room:]:
        notes.append(f"{r['id']}：每镜最多 {g['per_shot']} 条，没找")
        cap.append(dict(r, cap=f"每镜最多 {g['per_shot']} 条"))
    todo = todo[:room]
    left = max(0, int(g["per_film"]) - used(c))
    for r in todo[left:]:
        notes.append(f"{r['id']}：全片最多 {g['per_film']} 条（已用 {used(c)}），没找")
        cap.append(dict(r, cap=f"全片最多 {g['per_film']} 条"))
    return todo[:left], notes, cap


# ----------------------------------------------------------------------------- 搜索词、挑选
def materials_in(text):
    """一段中文里提到的真材料 [(kind, 认出来的词)]（先认长词，认过的词不再算别的）。"""
    t = str(text or "")
    for w in _IGNORE:
        t = t.replace(w, " ")
    out = []
    for k, zh, _, _ in MATERIALS:
        hit = next((w for w in zh if w in t), None)
        if hit:
            out.append((k, hit))
            for w in zh:
                t = t.replace(w, " ")
    return out


_OV_PARTS = {"element", "叠层", "overlay", "elements", "实拍叠层"}
_OV_WORDS = ("叠层", "SR.element", "element(", "前景", "叠上", "叠在")
_F_REF = re.compile(r"F\d{2,3}")


def overlay_needs(s):
    """一镜要的叠层种类 [(kind, 词)]：只在这一镜打算用叠层时算（parts 带 叠层 / element，或 event / action 写了 叠层、SR.element、前景）；
    词出现在 subject / image 里的不算（那是主体本身：实拍的白雾），紧跟在素材号后面的不算（「F30 雨夜街景」是世界层）。"""
    text = " ".join(str(s.get(k) or "") for k in ("event", "action"))
    parts = {str(p).strip().lower() for p in s.get("parts") or []}
    if not (parts & _OV_PARTS or any(w in text for w in _OV_WORDS)):
        return []
    own = " ".join(str(s.get(k) or "") for k in ("subject", "image"))
    out = []
    for k, w in materials_in(text):
        if any(z in own for z in MAT[k]["zh"]):
            continue
        spots = [m.start() for m in re.finditer(re.escape(w), text)]
        if spots and all(_F_REF.search(text[max(0, i - 8):i]) for i in spots):
            continue
        out.append((k, w))
    return out


def en_query(text, mot="", overlay=False):
    """中文需求 → 英文搜索词（素材站按英文搜）：英文原样留下，中文按词表最长匹配；什么都认不出来就用母题的英文。
    只是兜底（代理在 query 里写了英文搜索词就用代理的）：丢掉单位、纯数字、≤ 2 个字母的词（stockrel.junk：不从 km/h 抠出 km 去搜），
    QX-7、A320 这类型号留着。"""
    t = str(text or "")
    if t and not _RE_CJK.search(t):
        q = _txt(" ".join(w for w in t.split() if not REL.junk(re.sub(r"[^\w\-]", "", w))), 100) or MOTIF_EN.get(mot, "")
    else:
        words = [w for w in REL._ENG.findall(t) if not REL.junk(w)]
        table = dict(VOCAB)
        for k, zh, en, _ in MATERIALS:
            for w in zh:
                table.setdefault(w, en)
        keys = sorted(table, key=len, reverse=True)
        i = 0
        while i < len(t):
            k = next((k for k in keys if t.startswith(k, i)), None)
            if k:
                words.append(table[k])
                i += len(k)
            else:
                i += 1
        seen, out = set(), []
        for w in " ".join(words).split():
            if w.lower() not in seen:
                seen.add(w.lower())
                out.append(w)
        q = " ".join(out[:8]) or MOTIF_EN.get(mot, "")
    if overlay and "background" not in q.lower() and "isolated" not in q.lower():
        q = (q + " black background").strip()
    return q


def kind_for(r):
    if r.get("kind"):
        return r["kind"]
    m = materials_in(r.get("need"))
    if m:
        return m[0][0]
    return "el_" + r["sha"][:6]


def kind_have(kinds, k):
    """叠层表 kinds 里有没有和 k 同一种的（同名、同义的 kind 名片段、what 里写了那几个中文词）：返回那个 kind 名或 None。"""
    if not kinds or not k:
        return None
    if k in kinds:
        return k
    m = MAT.get(k)
    syn = tuple((m or {}).get("syn") or ()) + (k,)
    for name in kinds:
        if any(s in name.lower() for s in syn):
            return name
    for name, v in kinds.items():                                  # what 里写了那几个中文词（「不是海雪」「没有焦散」这种否定的不算）
        what = re.sub(r"(不是|不当|不算|没有|别当|并非|不像)[^，。；、（）()|]{0,10}", " ", str(v.get("what") or ""))
        if m and any(w in what for w in m["zh"]):
            return name
    return None


def overlay_kinds(c, mot=None):
    ov = inputs.overlays_merged(c.series, mot if mot is not None else motif(c), c.ep)
    return dict((ov or {}).get("kinds") or {})


_BAD_LIC = re.compile(r"(^|[^a-z])(nc|nd|sa|editorial|unknown|未知|不明|royalty[- ]managed)([^a-z]|$)")


def lic_ok(it):
    lic = str(it.get("license") or "").strip().lower()
    return bool(lic) and not _BAD_LIC.search(lic)


_PX_HD = re.compile(r"uhd_\d+_\d+_|hd_1920_1080_|_1920_1080_|_2048_1080_")
_MK_HD = re.compile(r"/videos/(\d+)/\1-\d+\.mp4")


def score(it, r):
    """候选打分（只用搜索结果自带的字段）：1080p 附近、横屏、够长、叠层优先黑底 / 深色底、帧率高一点好。同分保持站点给的顺序。
    stock.fetch 只会把 Pexels 老文件名（…-uhd_3840_2160_…）换成 1080p 那一份；新文件名（<数字>_3840_2160_60fps.mp4）猜不出 1080p，
    会整条下 4K（几百 MB）：往后排。Mixkit 只有 /videos/<id>/<id>-720.mp4 这种地址能换 1080p，其余（active_storage、哈希名）只有 720p。"""
    w, h = int(it.get("w") or 0), int(it.get("h") or 0)
    short, dur = min(w, h) if w and h else 0, float(it.get("dur") or 0)
    url, site = str(it.get("url") or ""), str(it.get("site") or "")
    s = (2.0 if short >= 1000 else 0.5 if short >= 700 else -3.0) if short else 1.5
    if site == "pexels" and max(w, h) > 2048 and not _PX_HD.search(url):
        s -= 2.5                                                  # 4K、换不了 1080p：下得慢、可能超过 max_mb
    if site == "mixkit":
        s += 0.0 if _MK_HD.search(url) else -1.0                  # 只有 720p
        if it.get("license_checked"):
            s += 0.3                                              # 条目页已经核过是 Free License
    s += 1.0 if w >= h else -1.5
    s += 1.0 if dur >= r["seconds"] + 1 else (-2.0 if 0 < dur < r["seconds"] else 0.0)
    tags = it.get("tags") or ""
    words = " ".join([str(it.get("title") or ""), str(it.get("descr") or ""), tags if isinstance(tags, str) else " ".join(map(str, tags))]).lower()
    if r["use"] == "overlay":
        s += 1.5 if any(k in words for k in ("black background", "dark background", "isolated", "black")) else 0.0
    if str(it.get("stype_guess") or "").startswith(("CG", "带字幕")):
        s -= 4.0 if r["use"] != "overlay" else 2.0                   # CG、带字幕的放大就糊、字幕会露出来：不当主体 / 世界层
    s += 0.5 if float(it.get("fps") or 0) >= 47 else 0.0
    return s


def rank(items, r):
    """免费授权、能下载的候选按 score 排。NASA 的搜索结果没有直链（url 空，fetch 时再读文件清单），照样收。
    先比标题对上几个主体词（stockrel.coverage：先数非泛词的，再数全部），再比 score（不然技术分高的泛泛画面会排在对题的档案前面）。"""
    ok = [it for it in items or [] if isinstance(it, dict) and (it.get("url") or it.get("site") == "nasa") and lic_ok(it)]
    t = REL.need_terms(r) if ok else {}
    return [it for *_, it in sorted(((-REL.coverage(it, t, core=True), -REL.coverage(it, t), -score(it, r), i, it) for i, it in enumerate(ok)),
                                    key=lambda x: x[:4])]


def cand_keys(it):
    """候选 / 登记条目的「是哪一条」：来源页、下载地址、站:编号（登记条目的 id 是请求号，站内编号在 src_id）。"""
    return {str(x) for x in (it.get("page"), it.get("page_url"), it.get("url"), f"{it.get('site')}:{it.get('src_id') or it.get('id')}") if x}


GAVE = "这一镜已经给过"
NOLIC = "对题，但没有免费商用授权 / 不能直接下载"


def pick(items, r, skip=()):
    """搜索结果 → (排好的候选, 拒的 [(候选, 原因)])：先文字对题（lib/stockrel：标题 / 描述 vs need），再去掉这一镜已经给过的
    （skip = 来源页 / 站:编号），剩下的 rank（rank 只留免费授权、能下载的：对题但被它去掉的记成 NOLIC，原因里分得清「不对题」和「没授权」）。"""
    keep, rej = REL.split(items, r)
    out = []
    for it in keep:
        if cand_keys(it) & set(skip or ()):
            rej.append((it, GAVE))
        else:
            out.append(it)
    ranked = rank(out, r)
    rej += [(it, NOLIC) for it in out if not any(it is x for x in ranked)]
    return ranked, rej


def _rej_rows(rej, n=6):
    return [f"{_txt(it.get('title'), 48)}：{why}" for it, why in rej[:n]]


def _offtopic(rej):
    return [(it, why) for it, why in rej if why not in (GAVE, NOLIC)]


def reason_of(st, rej=(), lic=0, errs=(), q=""):
    """没找到的真原因：一句话，按这个顺序认——临时失败（限流、超时、出错：下次再试）/ 没有候选 /
    候选都不对题（列 2–3 条拒因）/ 对题的都没有免费授权（Mixkit 只许个人用这类受限条目只算这一条的计数，不当原因本身）/ 下载失败。"""
    off = _offtopic(rej)
    nolic = lic + sum(1 for _, why in rej if why == NOLIC)
    gave = sum(1 for _, why in rej if why == GAVE)
    ex = "；".join(_rej_rows(off, 3))
    if st == "deferred":
        return "临时失败（站点限流 / 超时 / 出错，下次再试）：" + _txt("；".join(errs) or "没说原因", 160)
    if nolic:
        return (f"对题的候选 {nolic} 条都没有免费商用授权（只许个人用 / 不能直接下载）"
                + (f"；另外 {len(off)} 条不对题：{ex}" if off else ""))
    if off:
        return f"候选都不对题（{len(off)} 条）：{ex}" + (f"；另有 {gave} 条已经给过别的镜头" if gave else "")
    if gave:
        return f"对题的 {gave} 条已经给过别的镜头 / 别的 request_id，没有别的候选"
    real = [x for x in errs if not ("ValueError" in str(x) and "授权" in str(x))]
    if real:
        return "下载失败：" + _txt("；".join(real[-2:]), 160)
    return f"没有候选（搜「{q}」没有免费授权、能直接下载的结果）" if q else "没有搜索词"


_ID_PAT = (("pexels", re.compile(r"pexels\.com/(?:video|photo)/(?:[^/]*-)?(\d+)/?$")), ("mixkit", re.compile(r"mixkit\.co/[^/]+/(?:[^/]*-)?(\d+)/?$")),
           ("pexels", re.compile(r"(?:^|[_/-])pexels-(\d+)\.")), ("mixkit", re.compile(r"(?:^|[_/-])mixkit-(\d+)\.")))


def site_keys(it):
    """sources.json 一条的「站:编号」（site_id、来源页、文件名都试）：Pexels 来源页有 /video/<id>/ 和 /video/<slug>-<id>/ 两种写法。"""
    keys = set()
    if it.get("site_id"):
        keys.add(str(it["site_id"]))
    for k in ("page_url", "file"):
        v = str(it.get(k) or "")
        for site, rx in _ID_PAT:
            m = rx.search(v)
            if m:
                keys.add(f"{site}:{m.group(1)}")
    return keys


def _pack_index(pack):
    """素材包里已有的（下载过的）：来源页 / 下载地址 / 站:编号 → (本地文件, sources.json 那一条)。"""
    out = {}
    for it in (util.read_json(os.path.join(pack, "sources.json"), {}) or {}).get("items", []):
        p = os.path.abspath(os.path.join(pack, it.get("file", "")))
        if os.path.isfile(p):
            for k in ("page_url", "file_url", "url"):
                if it.get(k):
                    out[str(it[k])] = (p, it)
            for k in site_keys(it):
                out[k] = (p, it)
    return out


# ----------------------------------------------------------------------------- 找、下、入库
def _window(dur, r, shot=""):
    """叠层窗口：从 0.5 s 起取这一镜要的秒数（素材不够长就取到尾巴前 0.6 s）；去背景先用 median（减时间中位数，黑底实拍最稳）。"""
    dur = float(dur or 0)
    d = r["seconds"] if not dur else max(1.0, min(r["seconds"], dur - 0.6))
    src = 0.5 if dur and dur - 0.5 >= d else 0.0
    return {"src": round(src, 2), "dur": round(d, 2), "key": "median", "what": f"素材请求{(' ' + shot) if shot else ''}：{r['need'][:60]}"}


def _ov_ok(ov):
    """bake_overlay 的结果收不收（自动验收，没人看图）：stock.py 在临时表里烘焙、量过（覆盖率 0.05 %–50 %、不能满屏短划线、
    不能大片平的、检查图要出得来），过了才并进叠层表（_bake.ok / merged）。这里再核一遍：烘焙 rc 0、量到了覆盖率、检查图出了。
    STOCK_FAKE（mock）不烘，算收。返回 (收不收, 原因)。"""
    if not isinstance(ov, dict):
        return False, "烘焙没返回叠层条目"
    if ov.get("error") or ov.get("reject"):
        return False, str(ov.get("error") or ov.get("reject"))[:200]
    b = ov.get("_bake") or {}
    if b.get("skipped"):
        return True, ""
    if b.get("rc") != 0:
        return False, f"引擎烘焙失败（rc {b.get('rc')}）：{_txt(b.get('err'), 120)}"
    if b.get("sheet_rc") not in (0,):
        return False, f"检查图出不来（rc {b.get('sheet_rc')}）"
    if (b.get("stats") or {}).get("cov") is None:
        return False, "没量到覆盖率"
    if not b.get("ok") or not b.get("merged"):
        return False, _txt(b.get("why") or "自动验收没过", 200)
    return True, ""


def _sheet(c, pack, f):
    """到货联系表（stock.sheet：8 帧拼一张）：<素材包>/_review/stock_<文件名>.jpg，出过就不再出。ctx_note 复制进 ctx/look/stock/。"""
    out = os.path.join(pack, "_review", "stock_" + os.path.splitext(os.path.basename(f))[0] + ".jpg")
    if not os.path.isfile(out):
        _, err = call(c, "sheet", f, out)
        if err:
            c.log(f"  素材请求：联系表没出来（{_txt(err, 120)}）", echo=False)
    return out if os.path.isfile(out) else None


SHEETS = os.path.join("inputs", "sheets")                        # 这一集现做的联系表（素材包里没有现成的那几条；只写本集目录）


def contact_sheets(c, names):
    """这一集的 F 号 → 8 帧联系表（镜头表代理看图定对题 shotlist_check、制作代理的 ctx/look/stock/ 用）。
    {F 号: 绝对路径}；清单里没有的名字、文件不在的、出不来图的不列。先找现成的，路径稳定、可重用，同一条不重出：
      1. 登记里这条素材的到货联系表（inputs/stock.json 的 requests / wishes 的 sheet：wish 回填来的、开题找来的、制作时请求来的）；
      2. 素材包现有的：<素材包>/_review/stock_<文件名>.jpg（_sheet 出的）、<素材包>/_review/<文件名>.jpg（建包时出的）；
      3. 这一集出过的：<集>/inputs/sheets/stock_<文件名>.jpg；
    都没有就用 stock.sheet 现出一张到第 3 个位置（只写本集目录，不往素材包里写：素材包可能是别的集、别的母题共用的）。"""
    clips = {str(x.get("name")): x for x in (c.j("inputs/footage.json") or {}).get("clips") or [] if x.get("name")}
    reg = film(c)
    have = {}
    for x in list(reg.get("requests") or []) + list((reg.get("wishes") or {}).values()):
        if x.get("file") and x.get("sheet") and os.path.isfile(x["sheet"]):
            have.setdefault(os.path.abspath(x["file"]), x["sheet"])
    out = {}
    for nm in dict.fromkeys(str(n) for n in names or [] if n):
        f = str((clips.get(nm) or {}).get("file") or "")
        if not f:
            continue
        f = f if os.path.isabs(f) else os.path.join(util.PARENT, f)
        base = os.path.splitext(os.path.basename(f))[0]
        pack = os.path.dirname(os.path.dirname(f))
        mine = c.p(SHEETS, "stock_" + base + ".jpg")
        hit = next((p for p in (have.get(os.path.abspath(f)), os.path.join(pack, "_review", "stock_" + base + ".jpg"),
                                os.path.join(pack, "_review", base + ".jpg"), mine) if p and os.path.isfile(p)), None)
        if not hit and os.path.isfile(f):
            os.makedirs(os.path.dirname(mine), exist_ok=True)
            _, err = call(c, "sheet", f, mine)
            if err:
                c.log(f"  联系表：{nm} 没出来（{_txt(err, 120)}）", echo=False)
            hit = mine if os.path.isfile(mine) else None
        if hit:
            out[nm] = os.path.abspath(hit)
    return out


def fulfil_one(c, r, pack, skip=()):
    """一条需求：搜（必要时换一个更宽的词再搜一次）→ 文字对题、挑（skip = 这一镜已经给过的，不再给）→ 下（素材包里已有同一条就直接用）
    → 到货联系表 → overlay 再烘焙。返回登记条目（不含名字；rejected = 文字对题拒掉的前几条，sheet = 到货联系表）。"""
    g = cfg(c)
    e = {k: r.get(k) for k in ("id", "need", "use", "seconds", "why", "kind", "sha")}
    e.update({k: r[k] for k in WISH_KEYS if r.get(k)})
    q = r.get("query") or en_query(r["need"], motif(c), r["use"] == "overlay")
    if _RE_CJK.search(q):
        q = en_query(q, motif(c), r["use"] == "overlay")
    e["query"] = q
    qs = [q]
    q2 = re.sub(r"\b(slow motion|4k|hd|macro|close up)\b", "", q, flags=re.I)
    q2 = re.sub(r"\s+", " ", q2).strip()
    if q2 and q2 != q:
        qs.append(q2)
    cands, errs, rej, seen = [], [], [], set()
    if not qs or not qs[0]:
        return dict(e, status="failed", err="没有搜索词", reason="没有搜索词")
    for qq in qs:
        items, err = call(c, "search", qq, kind="video", n=int(g["candidates"]), sites=g.get("sites"))
        if err:
            errs.append(err)
        cands, rj = pick(items, dict(r, query=q), skip)              # 对题按真搜的英文词（兜底词、母题词也算主体词）
        for it, why in rj:                                            # 换词再搜常常搜回同几条：拒的只记一次
            if f"{it.get('site')}:{it.get('id')}" not in seen:
                seen.add(f"{it.get('site')}:{it.get('id')}")
                rej.append((it, why))
        if cands:
            e["query"] = qq
            break
    if rej:
        e["rejected"], e["rejected_n"] = _rej_rows(rej), len(rej)
    if not cands:
        off = _offtopic(rej)
        why = (f"{len(off)} 条候选的标题 / 描述都不对题（{'；'.join(_rej_rows(off, 3))}）" if off and len(off) == len(rej) else
               reason_of("failed", rej, q=q) if rej else "；".join(errs) or f"搜「{q}」没有免费授权、能直接下载的结果")
        st = "deferred" if not rej and any(deferred_err(x) for x in errs) else "failed"   # 搜索超时 / 出错：没看到候选，不算没找到
        return dict(e, status=st, err=why[:300], reason=reason_of(st, rej, errs=errs, q=q))
    have = _pack_index(pack)
    got = None
    tries = 0
    derrs = []                                                        # 下载这一步的错（搜索的错不算进「下载失败」）
    for it in cands[:8]:                                               # 真下载最多试 3 条；授权核下来不能用的（Mixkit 只许个人用）不下、不算
        if tries >= 3:
            break
        meta = dict({k: it.get(k) for k in ("site", "title", "author", "license", "w", "h", "fps", "dur", "url")},
                    page_url=it.get("page"), src_id=it.get("id"))
        hit = have.get(str(it.get("page") or "")) or have.get(str(it.get("url") or "")) or have.get(f"{it.get('site')}:{it.get('id')}")
        if hit:
            got = dict(meta, file=hit[0], fname=os.path.basename(hit[0]), status="reused",
                       **{k: hit[1][k] for k in ("w", "h", "fps", "dur", "license") if hit[1].get(k)})   # 素材包里那条（下载时实测的）
            break
        kw = {"stype": "元素（待烘焙）"} if r["use"] == "overlay" and not str(it.get("stype_guess") or "").startswith(("CG", "带字幕")) else {}
        f, err = call(c, "fetch", it, pack, use=f"{USE_CN[r['use']]}：{r['need'][:60]}",
                      note=(f"镜头表待补素材（{'、'.join(map(str, r.get('shots') or []))} {USE_CN[r['use']]}）" if r.get("request_id") else
                            f"素材请求 {r.get('shot', '')} {r['id']}：{r.get('why', '')}")[:200], query=e["query"], max_mb=g.get("max_mb"), **kw)
        p = (f or {}).get("path")
        p = p if not p or os.path.isabs(p) else os.path.join(pack, p)
        if p and os.path.isfile(p):
            src = (f or {}).get("source") or {}
            got = dict(meta, file=os.path.abspath(p), fname=(f or {}).get("name") or os.path.basename(p),
                       status="reused" if (f or {}).get("reused") else "ok",
                       **{k: src[k] for k in ("w", "h", "fps", "dur", "license") if src.get(k)})    # 下完 ffprobe 实测的为准
            break
        errs.append(err or "下载没出文件")
        derrs.append(err or "下载没出文件")
        tries += not (err and "ValueError" in err and "授权" in err)
    if not got:
        st = "deferred" if any(deferred_err(x) for x in errs) else "failed"       # 有一条是限流没下成：过后再试，不算没找到
        lic = [x for x in derrs if "ValueError" in str(x) and "授权" in str(x)]   # 核下来只许个人用的（Mixkit videoRestricted）：只计数，不当原因
        real = [x for x in derrs if x not in lic]
        why = (reason_of(st, errs=real or derrs) if st == "deferred" else
               "下载失败：" + _txt("；".join(real[-2:]), 160) + (f"；另 {len(lic)} 条对题的没有免费商用授权" if lic else "") if real else
               reason_of(st, rej, lic=len(lic), q=e["query"]))
        return dict(e, status=st, err=("；".join(errs[-2:]) or "下载失败")[:300], reason=why)
    e.update({k: v for k, v in got.items() if v not in (None, "")})
    sh = _sheet(c, pack, got["file"])
    if sh:
        e["sheet"] = sh
    if r["use"] == "overlay":
        kind = overlay_kind_name(c, r, pack)
        fname = got.get("fname") or os.path.basename(got["file"])
        win = _window(got.get("dur"), r, r.get("shot", ""))
        ov, err = call(c, "bake_overlay", pack, fname, kind, win, by=BY, timeout=int(g["timeout_s"].get("bake_overlay", 900)) - 30)
        ok, why = _ov_ok(ov) if not err else (False, err)
        if not ok:                                                    # 没过：stock.py 本来就没并进表；万一并了（老 stock.py），只撤自动通道
            call(c, "drop_overlay", pack, kind, fname, win["src"], f"素材请求自动验收没过：{why}", only_by=True)   # 自己写的窗口
        b = (ov or {}).get("_bake") or {}
        e["overlay"] = {"kind": kind, "ok": ok, "pos_ok": bool(b.get("pos_ok")) if ok else False,
                        **({"err": why} if not ok else {"entry": {k: v for k, v in ov.items() if k != "_bake"}, "bake": b})}
        if ok:                                                        # 这个 kind 原来就有：告诉代理新窗口是第几个（SR.element 的 win=）
            wins = (overlay_kinds_of(pack).get(kind) or {}).get("wins") or []
            e["overlay"]["win"] = next((i for i, w in enumerate(wins) if w.get("file") == fname
                                        and abs(float(w.get("src") or 0) - win["src"]) < 1e-6), max(0, len(wins) - 1))
    return e


def overlay_kind_name(c, r, pack):
    """自动入库的叠层用新 kind 名：叠层表（母题包 + 本集包 + 下载的包）里已经有这个名字（人工整理过的）就换成 <kind>_r<需求 sha 前 6 位>，
    绝不往人工整理过的 kind 里加窗口、也不替换（同名同起点会把调好的 c / lo / hi / cut 换掉，SR.element 按
    (win + 段号) % 窗口数轮换，所有用这个 kind 的镜头都会变）。同一条需求再来一次复用同一个新名字。"""
    kind = kind_for(r)
    names = set(overlay_kinds(c)) | set(overlay_kinds_of(pack)) | set(overlay_kinds_of(inputs.pack_dir(c.series, motif(c))))
    if kind not in names:
        return kind
    k2 = f"{kind}_r{r['sha'][:6]}"
    mine = overlay_kinds_of(pack).get(k2)
    if k2 in names and mine and any(not w.get("by") for w in mine.get("wins") or []):
        k2 = f"{kind}_r{r['sha'][:10]}"
    return k2


def overlay_kinds_of(pack):
    return dict(((util.read_json(os.path.join(pack, inputs.OVERLAYS)) if pack else None) or {}).get("kinds") or {})


def _attach(c, files):
    """新素材接进这一集：自动生成的清单重扫素材包（旧名字不变）；手写的清单（没有 auto）在末尾追加。返回 {绝对路径: 名字}。"""
    topic = c.j("topic.json") or {}
    try:
        inputs.ensure(c, topic)
    except Exception as e:  # noqa: BLE001
        c.log(f"  素材请求：清单重扫出错（{type(e).__name__}: {str(e)[:120]}），新素材手动追加", echo=False)
    fp = c.p("inputs", "footage.json")
    fj = util.read_json(fp) or {"clips": []}
    names = {x["file"]: x["name"] for x in fj.get("clips", [])}
    miss = [f for f in files if f not in names]
    if miss:
        n = max([int(v[1:]) for v in names.values() if v[1:].isdigit()] or [0])
        idx = {}
        for d in {os.path.dirname(os.path.dirname(f)) for f in miss}:
            for it in (util.read_json(os.path.join(d, "sources.json"), {}) or {}).get("items", []):
                idx[os.path.abspath(os.path.join(d, it.get("file", "")))] = it
        for f in miss:
            n += 1
            fj.setdefault("clips", []).append(inputs.clip_entry(idx.get(f) or {"content": os.path.basename(f)}, f, f"F{n:02d}",
                                                                (c.series.get("assets") or {}).get("flags")))
            names[f] = f"F{n:02d}"
        util.write_json(fp, fj)
        util.write(c.p("inputs", "footage.md"), inputs.footage_md(fj, inputs.load_motion(fj.get("clips", []))))
    try:
        c.renderer.setup()                                            # 引擎素材目录并进新素材（Take 能找到）
    except Exception as e:  # noqa: BLE001
        c.log(f"  素材请求：引擎素材目录没刷新（{type(e).__name__}: {str(e)[:100]}）", echo=False)
    return names


def fulfil_all(c, todo, shot, origin, work=None, phase="make", skip=()):
    """找、下、入库一批需求（调用方拿着 LOCK）。登记全片 inputs/stock.json 和（有 work 时）这一镜的 manifest（轮数按制作 / 返修分开算；
    全部是限流推迟的那一次不算一轮）。skip = 另外不给的候选（cand_keys；resolve_wishes：别的 request_id 已经拿到的）。返回登记条目列表。"""
    t0 = util.now()
    pack = pack_for(c)
    got = []
    gave = [x for x in manifest(work)["requests"] if x.get("status") in OK_ST] if work else []
    gave += list((((c.j(INV) or {}).get("shots") or {}).get(shot) or {}).get("found") or [])
    for r in todo:
        if util.HALT.is_set():
            raise KeyboardInterrupt
        sk = set(skip).union(*[cand_keys(x) for x in gave + [x for x in got if x.get("status") in OK_ST]])   # 这一镜给过的不再给
        try:
            e = fulfil_one(c, dict(r, shot=shot), pack, sk)
        except Exception as ex:  # noqa: BLE001  找素材出错只报告
            e = dict({k: r.get(k) for k in ("id", "need", "use", "seconds", "why", "kind", "sha", "query")}, status="failed",
                     err=f"{type(ex).__name__}: {str(ex)[:200]}", reason=f"编排器找素材时出错（{type(ex).__name__}）",
                     **{k: r[k] for k in WISH_KEYS if r.get(k)})
        got.append(dict(e, **{"from": origin, "shot": shot, "at": util.iso(), "mode": mode(c), "phase": phase}))
    files = [e["file"] for e in got if e.get("file")]
    names = _attach(c, files) if files else {}
    for e in got:
        if e.get("file"):
            e["name"] = names.get(e["file"])
    reg = film(c)
    redo = {e.get("sha") for e in got}                                # 重试成功 / 又推迟的：旧的 deferred / skipped_cap 那条换掉
    reg["requests"] = [x for x in reg.get("requests") or [] if not (x.get("sha") in redo and x.get("shot") == shot
                                                                    and x.get("status") in ("deferred", "skipped_cap"))] + got
    wall = round((util.now() - t0).total_seconds(), 1)
    reg["calls"] = (reg.get("calls") or []) + [{"from": origin, "shot": shot, "n": len(got), "ok": sum(1 for e in got if e["status"] in OK_ST),
                                               "wall_s": wall, "mode": mode(c), "pack": os.path.relpath(pack, util.PARENT), "at": util.iso()}]
    util.write_json(c.p(REG), reg)
    if work:
        man = manifest(work)
        if any(e["status"] != "deferred" for e in got):
            rb = dict(man.get("rounds_by") or ({"make": int(man.get("rounds") or 0)} if man.get("rounds") else {}))
            rb[phase] = int(rb.get(phase) or 0) + 1
            man["rounds_by"] = rb
            man["rounds"] = sum(rb.values())
        shas = {e.get("sha") for e in got}
        man["requests"] = [x for x in man["requests"] if not (x.get("sha") in shas and x.get("status") in ("deferred", "skipped_cap"))] + got
        man["note"] = ("编排器写的素材请求清单：status ok / reused 的已入库（name = 素材清单里的名字，Take(name, ...)；overlay 类另有叠层 kind）；"
                       "deferred = 站点限流 / 超时 / 出错、下一轮再试；rounds_by = 制作 / 返修各找过几次")
        util.write_json(os.path.join(work, MAN), man)
    ok = [e for e in got if e["status"] in OK_ST]
    c.log(f"  素材请求 {shot}：{len(ok)}/{len(got)} 条找到"
          + (f"（{'、'.join(str(e.get('name')) + ('·叠层 ' + e['overlay']['kind'] if (e.get('overlay') or {}).get('ok') else '') for e in ok)}）" if ok else "")
          + (f"，没找到 {[e['need'][:20] for e in got if e['status'] == 'failed']}（只报告不拦）" if any(e['status'] == 'failed' for e in got) else "")
          + (f"，限流推迟 {[e['need'][:20] for e in got if e['status'] == 'deferred']}（下一轮再试）" if any(e['status'] == 'deferred' for e in got) else "")
          + f"（{'假站点' if fake(c) else '联网'}，{wall:.0f}s）")
    return got


def record_skipped(c, cap, shot, origin, work=None, phase="make"):
    """超过每镜 / 全片 / 每阶段上限没找的：也登记（status skipped_cap），交付报告里列出来（调用方拿着 LOCK）。"""
    if not cap:
        return
    rows = [dict({k: r.get(k) for k in ("id", "need", "use", "seconds", "why", "kind", "sha", "query")}, status="skipped_cap",
                 err=r.get("cap") or "超过上限", **{"from": origin, "shot": shot, "at": util.iso(), "mode": mode(c), "phase": phase},
                 **{k: r[k] for k in WISH_KEYS if r.get(k)})
            for r in cap]
    reg = film(c)
    old = {(x.get("shot"), x.get("sha")) for x in reg.get("requests") or [] if x.get("status") == "skipped_cap"}
    reg["requests"] = (reg.get("requests") or []) + [x for x in rows if (shot, x.get("sha")) not in old]
    util.write_json(c.p(REG), reg)
    if work:
        man = manifest(work)
        have = {x.get("sha") for x in man["requests"]}
        man["requests"] = list(man["requests"]) + [x for x in rows if x.get("sha") not in have]
        util.write_json(os.path.join(work, MAN), man)


# ----------------------------------------------------------------------------- 接进代理
def _row(e):
    nm = e.get("name") or "?"
    src = f"{e.get('site') or '?'} {e.get('src_id') or ''}「{_txt(e.get('title'), 50)}」".strip()
    spec = (f"{e.get('w')}×{e.get('h')}" + (f"，{float(e['fps']):g} fps" if e.get("fps") else "") + (f"，{float(e['dur']):g} s" if e.get("dur") else ""))
    ov = e.get("overlay") or {}
    look = f"；联系表 {LOOK}/{nm}.jpg" if e.get("sheet") else ""
    if e.get("use") == "overlay" and ov.get("ok"):
        wn = f", win={ov['win']}" if ov.get("win") else ""
        how = ("能整屏用，也能 pos 放到一点" if ov.get("pos_ok") else
               "只能整屏用（pos=None）：素材贴着画面边，pos 放到一点会露出矩形直边")
        return (f"- 叠层 `SR.element(E, T, img, '{ov['kind']}'{wn}, f0=…, f1=…, px_per_f=… 或 zoom_pct_per_f=…)`：新烘焙的实拍（素材 {nm}，{src}，"
                f"{spec}；授权 {_txt(e.get('license'), 60)}；{how}；自动验收只量了覆盖率，内容对不对题看图{look}；ctx/overlays.json 已更新）"
                f"— 你要的「{e['need']}」")
    extra = f"（去背景没烘成：{_txt(ov.get('err'), 60)}；能当世界层用，不当叠层）" if e.get("use") == "overlay" else ""
    return (f"- `Take('{nm}', film=(起帧, 止帧), src=秒)`：{USE_CN.get(e.get('use'), '世界层')}，{src}，{spec}；授权 {_txt(e.get('license'), 60)}"
            f"{look}{extra}— 你要的「{e['need']}」")


def wish_arrived(c, sid):
    """镜头表待补素材里分给这一镜的（stages.fill_wishes 回填进 shotlist.json 的主体 / 世界层 / 辅助素材）：shotlist_wish.json 的 results
    里找到的、F 号在这一镜用着的。11-09 复核 G1：回填进来的素材以前没有代理看过图（盘点拿到的是回填后的表，联系表进不了 ctx/look/stock/）。"""
    s = next((x for x in (c.j("shotlist.json") or {}).get("shots") or [] if x.get("id") == sid), None)
    if not s:
        return []
    src = s.get("subject_src") if isinstance(s.get("subject_src"), dict) else {}
    mine = _names(s) | {str(src.get("file") or "")}
    return [v for v in ((c.j("shotlist_wish.json") or {}).get("results") or {}).values() if v.get("status") in OK_ST and v.get("name") in mine]


def listing(c, sid, work):
    rows = []
    for e in manifest(work)["requests"]:
        st = e.get("status")
        rows.append(_row(e) if st in OK_ST else
                    f"- 「{e['need'][:40]}」{'站点限流 / 临时出错，下一轮再试' if st == 'deferred' else '超上限没找' if st == 'skipped_cap' else '没找到'}"
                    f"（{_txt(e.get('reason') or e.get('err'), 160)}）")
    for e in wish_arrived(c, sid):
        rows.append("- 镜头表要的、开拍前找到的：" + _row(e)[2:])
    inv = ((c.j(INV) or {}).get("shots") or {}).get(sid) or {}
    for e in inv.get("found") or []:
        rows.append("- 开拍前盘点补的：" + _row(e)[2:])
    for e in inv.get("missing") or []:
        rows.append(f"- 开拍前盘点没找到：{e}")
    return "\n".join(rows) if rows else "（这一镜还没有素材请求）"


def task_block(c, sid, work):
    """给制作 / 返修代理任务书加的「缺真材料就提请求」一节（通道开着时）：格式、上限、叠层表现有的种类、这一镜已经到的素材。"""
    g = cfg(c)
    kinds = overlay_kinds(c)
    return "\n\n" + c.prompt("stock_rules", SHOT=sid, PER_SHOT=str(g["per_shot"]), ROUNDS=str(g["rounds_per_shot"]),
                             PER_FILM=str(g["per_film"]), USED=str(used(c)),
                             KINDS="、".join(f"{k}（{_txt(v.get('what'), 24)}）" for k, v in kinds.items()) or "（这一集还没有叠层表）",
                             MANIFEST=listing(c, sid, work)).rstrip() + "\n"


def followup_text(got, expect, left=0):
    """「素材到了」：新素材怎么用、联系表在哪；不对题怎么换（改写 need / query 再提，left = 这一阶段还能找几次）。"""
    ok = [e for e in got if e.get("status") in OK_ST]
    bad = [e for e in got if e.get("status") not in OK_ST]
    rows = "\n".join(_row(e) for e in ok)
    rows += "".join(f"\n- 「{e['need'][:40]}」没找到（{_txt(e.get('reason') or e.get('err'), 160)}）：不画 2D 替身，用现有的实拍 / 叠层 / 画面派生，"
                    "在 out/NOTES.md 写一句" for e in bad)
    again = (f"在 out/footage_requests.json 里改写那一条的 need / query（换个说法，写清要什么、不要什么）再提，编排器在你跑完这次后马上重新找、"
             f"换一条（这一镜给过的不会再给），这一阶段还能找 {left} 次" if left else "这一阶段找素材的次数用完了")
    return (f"\n\n## 素材到了（编排器刚在沙箱外找到、下好、入了库；这一次只做一件事：把它们接进 {expect}）\n{rows}\n"
            f"先看图再接：到货联系表在 {LOOK}/（每条一张 <素材名>.jpg，8 帧；叠层另有烘焙检查图 <素材名>_<kind>.jpg）。编排器只按标题 / 描述"
            f"对过题，内容对不对以图为准；不对题（要碎玻璃来的是倒水、要虾来的是人）就别用，{again}。"
            "ctx/footage.md、ctx/footage.json、ctx/overlays.json 已经换成新的（新素材在表里）。世界层 / 主体用 Take（低于 60 fps 写 "
            "interp='blend' 或变速，慢素材 2–3 倍速）；叠层用 SR.element，单向飘 / 迎面推，只当陪衬（主角不动 + 满屏叠层 = 静图）。"
            "用不上就不用，在 out/NOTES.md 写一句为什么。其余保持不变，改完跑 ctx/check.sh。\n")


def ctx_note(c, work, sid):
    """ctx/stock.json：这一镜已经到的素材（请求的 + 镜头表回填进来的 + 开拍前盘点补的；mock 代理、调试看）；到货联系表复制进 ctx/look/stock/
    （<素材名>.jpg；叠层烘焙检查图 <素材名>_<kind>.jpg），每次整个换掉。prepare_ctx、素材到了以后（refresh）都经 write_footage_ctx 调到这里。"""
    inv = ((c.j(INV) or {}).get("shots") or {}).get(sid) or {}
    arr = [e for e in manifest(work)["requests"] if e.get("status") in OK_ST] + list(inv.get("found") or [])
    arr += [e for e in wish_arrived(c, sid) if e.get("name") not in {x.get("name") for x in arr}]   # 镜头表回填进来的主体 / 世界层
    look = os.path.join(work, LOOK)
    if os.path.isdir(look):
        shutil.rmtree(look)
    rows = []
    for e in arr:
        ov = e.get("overlay") or {}
        pics = []
        for src, tag in ((e.get("sheet"), ""), ((ov.get("bake") or {}).get("sheet") if ov.get("ok") else None, f"_{ov.get('kind')}")):
            if src and os.path.isfile(src):
                os.makedirs(look, exist_ok=True)
                dst = os.path.join(look, re.sub(r"[^\w.-]+", "_", f"{e.get('name') or 'x'}{tag}") + ".jpg")
                shutil.copy2(src, dst)
                pics.append(os.path.relpath(dst, work))
        rows.append(dict({k: e.get(k) for k in ("name", "use", "need", "site", "title", "license")},
                         overlay=ov.get("kind") if ov.get("ok") else None, sheet=pics))
    util.write_json(os.path.join(work, CTX), {"shot": sid, "arrived": rows,
                                              "note": "这一镜已经到的素材（素材请求 + 开拍前盘点补的）：name 用 Take，overlay 是新叠层的 kind，"
                                                      "sheet = 到货联系表（先看图：编排器只按标题 / 描述对过题）"})


def after_agent(c, job, res, rerun, refresh=None):
    """代理跑完一次后：有新的素材请求就去找、下、入库；找到了就（refresh 刷新 ctx/ 的素材表、ctx/look/stock/ 联系表后）再叫一次代理
    把素材接进去。接素材那次代理看了联系表、改写了 need / query，就接着找、再叫它接（同一阶段最多 rounds_per_shot 次，pending 管）。
    都不拦：找不到只报告，返回原来的结果（接过素材就是最后一次接素材的结果）。"""
    if not enabled(c) or job.get("stock_followup") or job.get("kind") == "keyframe":
        return res
    work, sid = job["work"], job["name"]
    phase = phase_of(job)
    n_ok = n_all = 0
    for n in range(1, int(cfg(c)["rounds_per_shot"]) + 1):
        if not os.path.exists(os.path.join(work, REQ)):
            return res
        reqs, notes = parse_requests(work)
        with LOCK:
            todo, n2, cap = pending(c, work, reqs, phase)
            if notes + n2:
                c.log(f"  素材请求 {sid}：" + "；".join(notes + n2)[:400], echo=False)
            record_skipped(c, cap, sid, "agent", work, phase)
            if not todo:
                return res
            try:
                got = fulfil_all(c, todo, sid, "agent", work, phase)
            except KeyboardInterrupt:
                raise
            except Exception as e:  # noqa: BLE001  找素材出错只报告，不拦制作
                c.log(f"  素材请求 {sid}：出错，跳过（{type(e).__name__}: {str(e)[:160]}）")
                return res
        ok = [e for e in got if e["status"] in OK_ST]
        if refresh:
            try:
                refresh(job)
            except Exception as e:  # noqa: BLE001
                c.log(f"  素材请求 {sid}：ctx/ 的素材表没刷新（{type(e).__name__}: {str(e)[:100]}）", echo=False)
        have = all(os.path.exists(os.path.join(work, x)) for x in job["expect"])
        if not (ok and cfg(c).get("followup", True) and have and res.get("status") in ("ok", "max_turns", "timeout")):
            return res
        reads = list(job.get("allow_read") or [])
        try:
            reads += [d for d in c.renderer.footage_dirs() if d not in reads]   # 新素材包目录（沙箱放行读）
        except Exception:  # noqa: BLE001
            pass
        left = max(0, int(cfg(c)["rounds_per_shot"]) - rounds_used(manifest(work), phase))
        look = os.path.join(work, LOOK)
        pics = sorted(os.path.join(look, f) for f in os.listdir(look)) if os.path.isdir(look) else []
        job2 = dict(job, task=job["task"] + followup_text(got, job["expect"][0], left), run_dir=job["run_dir"] + "-stock" + (f"{n}" if n > 1 else ""),
                    stock_followup=True, gen_followup=True, allow_read=reads,
                    images=list(dict.fromkeys(pics + [p for p in job.get("images") or [] if os.path.isfile(p)]))[:10])   # 到货图在前、去重、10 张
        res2 = rerun(job2)
        if res2.get("status") != "ok":
            c.log(f"  素材请求 {sid}：接素材那次代理 {res2.get('status')}，用上一版代码", echo=False)
            return res
        n_ok, n_all = n_ok + len(ok), n_all + len(got)
        res = dict(res2, stock={"ok": n_ok, "failed": n_all - n_ok})
    return res


# ----------------------------------------------------------------------------- 镜头表的待补素材（request_id）
def _wishes(s):
    w = s.get("footage_wish") or []
    return [x for x in (w if isinstance(w, list) else [w]) if isinstance(x, dict)]


def _rid(v):
    return str(v).strip() if isinstance(v, (str, int)) and not isinstance(v, bool) else ""


def _wish_index(sl):
    """镜头表里带 request_id 的 footage_wish → {request_id: {"wish": 那一条, "shot": 写在哪一镜, "dup": [又写了同一个 id 的镜]}}。
    同一个 id 写了几次取第一条（「全集唯一」的校验归 stages）。"""
    out = {}
    for s in (sl or {}).get("shots") or []:
        for x in _wishes(s):
            rid = _rid(x.get("request_id"))
            if rid and rid in out:
                out[rid]["dup"].append(s.get("id"))
            elif rid:
                out[rid] = {"wish": x, "shot": s.get("id"), "dup": []}
    return out


def _id_rx(i):
    return re.compile(r"(?<![A-Za-z0-9_\-])" + re.escape(i) + r"(?![A-Za-z0-9_\-])")


def _ids_in(v, ids):
    """footage（字符串）/ aux_footage（列表）里写的 request_id（整词，只认 ids 里有的；id 里可以有 @ . : 这类字符）。"""
    text = "\n".join(str(x or "") for x in (v if isinstance(v, list) else [v]))
    return [i for i in ids if i and _id_rx(i).search(text)]


def _wish_refs(sl, idx=None):
    """镜头表哪里引用了 request_id → [(request_id, 镜号, 字段)]：subject_src.request_id（照收：没写 footage_wish 的 resolve 判没找到）、
    footage、aux_footage 里写的 id（只认 footage_wish 里有的，不猜 REQ_ 前缀；和 stages.wish_refs 同一个口径）。只在 event 文字里提到的
    不算引用：不找、不回填（event 里提到的找来了也没分给哪一镜；stages.wish_unused 提醒代理写进 footage）。
    主体在前，然后世界层、辅助素材，各按镜头顺序。"""
    idx = _wish_index(sl) if idx is None else idx
    shots = (sl or {}).get("shots") or []
    out = []
    for s in shots:
        src = s.get("subject_src") if isinstance(s.get("subject_src"), dict) else {}
        if _rid(src.get("request_id")):
            out.append((_rid(src["request_id"]), s.get("id"), "subject_src"))
    for fld in ("footage", "aux_footage"):
        for s in shots:
            out += [(rid, s.get("id"), fld) for rid in _ids_in(s.get(fld), list(idx))]
    return out


def _wish_out(e, shots):
    """登记条目 → resolve_wishes 返回的一条（没找到的带 reason：真原因一句话）。"""
    base = {k: e.get(k) for k in ("need", "query", "use") if e.get(k)}
    if e.get("status") in OK_ST and e.get("name"):
        return dict(base, status="ok", name=e["name"], shots=shots,
                    **{k: e[k] for k in ("site", "title", "license", "page_url", "file", "sheet") if e.get(k)})
    err = e.get("err") or ("下到了，但没接进素材清单（没拿到 F 号）" if e.get("status") in OK_ST else "没找到")
    return dict(base, status="failed", err=err, reason=e.get("reason") or err, shots=shots, rejected=list(e.get("rejected") or []),
                **({"deferred": True} if e.get("status") == "deferred" else {}))


def _name_of(c, e):
    """登记过的条目现在在素材清单里叫什么（按文件对；清单重扫过、名字没了返回 None）。"""
    clips = (c.j("inputs/footage.json") or {}).get("clips") or []
    return next((x.get("name") for x in clips if e.get("file") and x.get("file") == e.get("file")), None)


def _round(v):
    """一次 resolve 里这个 id 自己的结果（wishes[id].history 的一条）：用了什么 query、找没找到、为什么。"""
    keep = ("need", "query", "status", "name", "title", "reason", "deferred", "cached", "kept")
    return dict({k: v[k] for k in keep if v.get(k) not in (None, "", False)}, at=util.iso())


def _still_ok(x, r):
    """缓存里判收过的那一条（登记里只有标题），按现在的 stockrel 再判一次（旧判法收进来的错题——后视镜、Cessna、卡丁车——
    续跑时不原样回到看图那一步）。没过就当没找过、重搜：同一条要是真对题，搜回来带上描述照样会收、复用素材包里那一条。"""
    if not x.get("title"):
        return True
    return REL.judge({"title": x["title"], "descr": "", "tags": ""}, REL.need_terms(dict(r, query=x.get("query") or r.get("query"))))[0]


def resolve_wishes(c, sl, reject=(), hold=None):
    """镜头表通过以后、开拍前盘点之前：镜头表引用的 request_id（subject_src.request_id、footage、aux_footage）按代理
    写的 footage_wish（need / query / use / seconds）去找：搜 → stockrel 文字对题 → 下 → 和开拍前盘点同一套入库（新 F 号）。
    返回 {request_id: {"status": "ok", "name": "F07", "shots", "need", "query", "use", "site", "title", "license", "file", "sheet"}
          | {"status": "failed", "err", "reason": 真原因一句话, "need", "query", "use", "shots", "rejected": 拒掉的候选（标题：原因）,
             "deferred": 站点限流}}，顺序 = 引用顺序（主体在前）。
    - 找过的不重找：同一条 need / query / use（sha 一样）登记过 ok / failed 就直接用（代理改写了 query 才再找）；临时失败（deferred：
      限流、超时、出错）不算找过，下次再试。
    - 前一轮找到的不丢：这个 id 上一次 resolve 拿到了 F 号（还在素材清单里），这一次代理只改了措辞（query、use
      没变）就不重找、照用；改了 query 重找没找到（不是临时失败），也保留上一次的 F 号——返回那一条带 kept = True、retry = 这一次重找的结果。
      reject = 代理看图判不对的 request_id（shotlist_check 的 verdict reject）：上一次的结果不保留，那一条候选这一次也不再给。
      hold = {request_id: 原因}：这一次不找的（看图判不对、又没给新的搜索词）：直接判没找到、照登记（以前照旧 query 重搜重下，
      下回来的没人用、两份登记对不上）。
    - 缓存里判收过的，先按现在的 stockrel 拿登记的标题再判一次，不过就重搜（_still_ok）。
    - 上限：引用的 id 最多找 wish_max 条（系列 stock.wish_max，没写 = inventory_max），多的不找、判没找到；用上的也计入开拍前盘点的上限。
    - 不同的 request_id 不给同一条素材（一段素材只当一次主体）。
    - 登记：inputs/stock.json 的 requests（from = wish，带 request_id、shots），wishes 里记每个 id 现在的结果，history 记每一次 resolve
      这个 id 自己的结果（用了什么 query、找没找到、为什么、有没有保留上一次的）。
    通道关着：每条都判没找到（原因写通道关着）。回填镜头表用 rebind，发回镜头表的说明用 failed_lines。"""
    idx = _wish_index(sl)
    refs = _wish_refs(sl, idx)
    order = list(dict.fromkeys(r for r, _, _ in refs))
    if not order:
        return {}
    g = cfg(c)
    cap = int(g.get("wish_max") or g["inventory_max"])
    reject = {str(x) for x in (reject or ())}
    hold = {str(k): str(v) for k, v in (hold or {}).items()}
    out, todo, capped, bound, n = {}, [], [], [], 0
    with LOCK:
        reg0 = film(c)
        prev = {x.get("sha"): x for x in reg0.get("requests") or [] if x.get("from") == "wish"}
        last = reg0.get("wishes") or {}
        keep, banned = {}, set()                                       # keep = {id: (上一次找到的, 现在的 F 号)}；banned = 代理判不对的那条候选
        for x in reg0.get("requests") or []:                           # reject 里写的 F 号（看图判不对题的开题素材）：那条候选也不再给
            if x.get("status") in OK_ST and x.get("name") and x["name"] in reject:
                banned |= cand_keys(x)
        for rid in order:
            pw = last.get(rid) or {}
            nm = _name_of(c, pw) if pw.get("status") == "ok" else None
            if rid in reject or (pw.get("name") and pw["name"] in reject):
                banned |= cand_keys(pw) if pw.get("status") == "ok" else set()
            elif nm:
                keep[rid] = (pw, nm)
        for rid in order:
            shots = list(dict.fromkeys(sid for r, sid, _ in refs if r == rid))
            w = (idx.get(rid) or {}).get("wish")
            r, _ = norm_request(w, 0) if w else (None, "")
            if not r:
                why = ("footage_wish 里这一条没写 need（一句话描述画面）" if w else
                       f"镜头表引用了 request_id「{rid}」，但没有一镜的 footage_wish 写了它（要有 need + query）")
                out[rid] = {"status": "failed", "shots": shots, "need": _txt((w or {}).get("need"), 120), "query": _txt((w or {}).get("query"), 100),
                            "err": why, "reason": why}
                continue
            r.update(id=rid, request_id=rid, shots=shots, why=r["why"] or f"{'、'.join(map(str, shots))} 引用的待补素材")
            if rid in hold:
                out[rid] = _wish_out(dict(r, status="failed", err=hold[rid], reason=hold[rid]), shots)
                continue
            if rid in keep and not _still_ok(keep[rid][0], r):              # 上一次找到的，按现在的判法不对题：不保留
                keep.pop(rid)
            n += 1
            if not enabled(c):
                out[rid] = dict(_wish_out(dict(r, status="failed", err="素材请求通道关着（STOCK_OFF / stock.enabled = false），不去找"), shots))
                continue
            if n > cap:
                capped.append(dict(r, cap=f"镜头表引用的 request_id 最多找 {cap} 条（stock.wish_max，没写 = inventory_max）"))
                continue
            old = prev.get(r["sha"])
            pk = keep.get(rid)
            if old and old.get("status") in OK_ST and rid not in reject and not (cand_keys(old) & banned) and _still_ok(old, r):
                nm = _name_of(c, old)
                if nm:                                                  # 素材清单里还在：直接用（清单重扫丢了就再找一次，包里有会复用）
                    bound.append(old)
                    out[rid] = dict(_wish_out(dict(old, name=nm), shots), cached=True)
                    continue
            elif old and old.get("status") == "failed" and rid not in reject:   # 同一条没找到过：不重找（超上限没找的、限流推迟的照找）
                out[rid] = dict(_wish_out(old, shots), cached=True)
                if pk:
                    out[rid] = dict(_wish_out(dict(pk[0], status="ok", name=pk[1]), shots), kept=True, retry=_round(out[rid]))
                continue
            if pk and pk[0].get("query") == r["query"] and norm_use(pk[0].get("use")) == r["use"]:   # 只改了措辞：不重找，照用上一次的
                bound.append(pk[0])
                out[rid] = dict(_wish_out(dict(pk[0], status="ok", name=pk[1]), shots), kept=True, cached=True)
                continue
            todo.append(r)
        skip = banned.union(*[cand_keys(x) for x in bound], *[cand_keys(pk[0]) for rid, pk in keep.items() if rid not in {r["request_id"] for r in todo}])
        got = fulfil_all(c, todo, "镜头表", "wish", skip=skip) if todo else []
        record_skipped(c, capped, "镜头表", "wish")
        for e in got:
            rid = e["request_id"]
            out[rid] = _wish_out(e, e.get("shots") or [])
            pk = keep.get(rid)
            if pk and out[rid]["status"] != "ok" and not out[rid].get("deferred"):   # 重找没找到：保留上一次的 F 号
                out[rid] = dict(_wish_out(dict(pk[0], status="ok", name=pk[1]), out[rid]["shots"]), kept=True, retry=_round(out[rid]))
        for r in capped:
            why = r["cap"] + "，这一条没找：少引用几条，或这一镜改用设计帧 / 生图 / 清单里已有的 F 号"
            out[r["request_id"]] = _wish_out(dict(r, status="failed", err=why, reason="超上限没找：" + r["cap"]), r["shots"])
        reg = film(c)
        wishes = dict(reg.get("wishes") or {})
        for rid in order:
            v = out[rid]
            hist = list((wishes.get(rid) or {}).get("history") or [])
            hist.append(_round(v.get("retry") or v))
            if v.get("kept") and v.get("retry"):
                hist[-1]["kept"] = (v.get("name"), v.get("query"))           # 这一次没找到，保留了上一次的
            wishes[rid] = dict({k: x for k, x in v.items() if k not in ("rejected", "retry")}, at=util.iso(), history=hist[-6:])
        reg["wishes"] = wishes
        util.write_json(c.p(REG), reg)
    res = {rid: out[rid] for rid in order}
    bad = [rid for rid, v in res.items() if v["status"] != "ok"]
    kept = [rid for rid, v in res.items() if v.get("kept")]
    oks = "、".join(rid + "→" + v["name"] for rid, v in res.items() if v["status"] == "ok")
    c.log(f"镜头表待补素材：引用 {len(res)} 条 request_id，找到 {len(res) - len(bad)}" + (f"（{oks}）" if oks else "")
          + (f"，其中 {'、'.join(kept)} 保留上一次的" if kept else "")
          + (f"，没找到 {'、'.join(bad)}" if bad else "") + f"（这次去找 {len(got)} 条，{'假站点' if fake(c) else '联网'}；明细 inputs/stock.json 的 wishes）")
    return res


def rebind(sl, res):
    """回填（stages.fill_wishes 和 inventory 共用这一份）：镜头表里引用的 request_id 换成 resolve_wishes 找到的 F 号
    → (新镜头表, 没回填的 [(request_id, 镜号, 字段)])。不改传进来的 sl。引用字段只动每一镜自己引用的 id：subject_src（file = F 号、
    去掉 request_id）、footage、aux_footage（不是 REQ_ 开头的 id，例如 QX7，只在这几处换）。找到的、REQ_ 开头的 id 另外在每一镜别的字符串里
    整词换掉（event、transition_in / out 的交接、subject_handoffs、代理自己加的键；11-09 复核 G8：REQ_ 前缀不会误伤），屏上字（SCREEN：
    hero_word、words、label、number）不碰。找到的那几条 footage_wish 删掉（已经在素材清单里，开拍前盘点不再当心愿找）。没找到的原样留着。"""
    import copy
    new = copy.deepcopy(sl or {})
    ok = {rid: v["name"] for rid, v in (res or {}).items() if v.get("status") == "ok" and v.get("name")}
    refs = _wish_refs(sl)
    left = [(r, sid, f) for r, sid, f in refs if r not in ok]
    if not ok:
        return new, left
    for s in new.get("shots") or []:
        own = {r for r, sid, _ in refs if sid == s.get("id") and r in ok}
        if "footage_wish" in s:
            s["footage_wish"] = [x for x in _wishes(s) if _rid(x.get("request_id")) not in ok]
            if not s["footage_wish"]:
                s.pop("footage_wish")
        if not own:
            continue
        rx = re.compile(r"(?<![A-Za-z0-9_\-])(" + "|".join(map(re.escape, sorted(own, key=len, reverse=True))) + r")(?![A-Za-z0-9_\-])")
        src = s.get("subject_src")
        if isinstance(src, dict) and _rid(src.get("request_id")) in own:
            src["file"] = ok[_rid(src.pop("request_id"))]
        if isinstance(s.get("footage"), str):
            s["footage"] = rx.sub(lambda m: ok[m.group(1)], s["footage"])
        if isinstance(s.get("aux_footage"), list):
            s["aux_footage"] = [ok[_rid(a)] if _rid(a) in own else a for a in s["aux_footage"]]
    req = sorted((r for r in ok if r.startswith("REQ_")), key=len, reverse=True)
    if req:
        rx = re.compile(r"(?<![A-Za-z0-9_\-])(" + "|".join(map(re.escape, req)) + r")(?![A-Za-z0-9_\-])")

        def sub(v):
            if isinstance(v, str):
                return rx.sub(lambda m: ok[m.group(1)], v)
            if isinstance(v, list):
                return [sub(x) for x in v]
            if isinstance(v, dict):
                return {k: x if k in SCREEN else sub(x) for k, x in v.items()}
            return v
        for s in new.get("shots") or []:
            for k in list(s):
                if k not in SCREEN and k not in ("footage_wish", "missing_footage", "unseen_footage"):   # 后两样记的是 id 本身
                    s[k] = sub(s[k])
    return new, left


SCREEN = ("hero_word", "words", "label", "number", "number_what")      # 屏上字：rebind 不换


def failed_lines(res):
    """没找到的 request_id → 发回镜头表（或停下的 stop_msg、制作任务书的 missing_footage）用的几行：哪条、哪几镜、要什么、搜了什么、
    为什么（reason：真原因，受限条目的 ValueError 不当原因；没有 reason 的老结果用 err）。"""
    rows = []
    for rid, v in (res or {}).items():
        if v.get("status") == "ok":
            continue
        err = _txt(v.get("reason") or v.get("err"), 300)               # 和源头 fulfil_one 一样截 300 字（原因里只有事实，截掉的是后几条拒掉的候选）
        rej = "；".join(x for x in (v.get("rejected") or [])[:3] if _txt(x, 30) not in err)
        rows.append(f"- {rid}（{'、'.join(map(str, v.get('shots') or [])) or '?'}）「{_txt(v.get('need'), 60)}」，搜「{v.get('query') or '（没有搜索词）'}」："
                    f"{err}" + (f"；拒掉的候选：{rej}" if rej else ""))
    return rows


# ----------------------------------------------------------------------------- 开拍前盘点
def _sl_sha(sl):
    keep = ("id", "look", "subject", "subject_src", "action", "event", "image", "parts", "footage", "aux_footage", "footage_wish")
    return hashlib.sha256(json.dumps([{k: s.get(k) for k in keep} for s in sl.get("shots") or []], ensure_ascii=False,
                                     sort_keys=True).encode()).hexdigest()[:12]


def _names(s):
    fo = str(s.get("footage") or "")
    return set(re.findall(r"(?<![A-Za-z0-9])F\d{2,3}(?![0-9])", fo)) | {str(x) for x in s.get("aux_footage") or []}


def _wish_used(c, sl, wres):
    """计入开拍前盘点上限的待补素材（request_id）：这一次 resolve 的每一条 + 登记的 wishes 里找到了、F 号还在这份镜头表里用着的
    （stages 先 resolve、回填过再交给 inventory 的）。"""
    used = set(wres or {})
    names = set()
    for s in (sl or {}).get("shots") or []:
        src = s.get("subject_src") if isinstance(s.get("subject_src"), dict) else {}
        names |= {str(src.get("file") or "")} | _names(s)
    for rid, v in (film(c).get("wishes") or {}).items():
        if v.get("status") == "ok" and v.get("name") in names:
            used.add(rid)
    return used


def world_opts(series):
    """CHROMA 不写：(True, ())，行为不变。"""
    from . import tree as TR
    wp = tuple(map(str, (((series or {}).get("gates") or {}).get("plan_world") or {}).get("world_parts") or ()))
    return TR.world_min(series)[0] > 0, wp


def shot_needs(s, names, kinds, last=False, world_on=True, world_parts=()):
    """一镜的素材盘点：{subject, world, overlays: [{kind, word, have}], wish: [请求]}，gaps = 缺的（要去找的请求）。"""
    src = s.get("subject_src") or {}
    kind = str(src.get("kind") or "")
    row, gaps = {}, []
    f, rid = str(src.get("file") or ""), _rid(src.get("request_id"))
    if kind == "实拍" and rid:                                       # 还写着 request_id = 没回填（没找到）：file 只是占位；resolve_wishes 按代理的
        row["subject"] = f"缺（待补素材 {rid} 没找到）"                # footage_wish 找过，不再按 subject 文字另找
    elif kind == "实拍":
        row["subject"] = f if f in names else f"缺（{f or '没写'} 不在素材清单）"
        if f not in names:
            gaps.append({"need": _txt(s.get("subject"), 120) or "主体实拍", "use": "subject", "why": f"{s['id']} 的主体实拍不在素材清单"})
    else:
        row["subject"] = kind or "?"
    wn = _names(s) & set(names)
    need_world = s.get("look") == "footage"
    if world_parts and set(map(str, s.get("parts") or [])) & set(world_parts) and s.get("look") != "footage":
        need_world = False                                          # 界的物件在主角身后被撞碎、实拍叠层飘过：也算世界层
    if kind == "实拍" and f in names and not rid:
        wn.add(f)
    row["world"] = sorted(wn) if wn else ("缺" if need_world else "不需要")
    if need_world and not wn:
        gaps.append({"need": _txt(s.get("image") or s.get("subject"), 120) + "（世界层：主角身后单向流过的实拍）", "use": "world",
                     "why": f"{s['id']} 要世界层，镜头表没写清单里的素材"})
    ovs = []
    for k, w in overlay_needs(s):
        h = kind_have(kinds, k)
        ovs.append({"kind": k, "word": w, "have": h})
        if not h:
            gaps.append({"need": f"黑底 / 深色底的实拍{w}（叠层 {k}）", "use": "overlay", "kind": k, "query": MAT[k]["en"],
                         "why": f"{s['id']} 写了「{w}」，叠层表里没有这一种"})
    row["overlays"] = ovs
    wish = s.get("footage_wish") or []
    for i, x in enumerate(wish if isinstance(wish, list) else [wish]):
        r, _ = norm_request(x, i)
        if r:
            r["why"] = r["why"] or f"{s['id']} 导演写的 footage_wish"
            r["wish"] = True
            gaps.append(r)
    row["wish"] = len(wish) if isinstance(wish, list) else 1
    return row, gaps


def inventory(c, sl=None, topic=None, force=False):
    """开拍前盘点（镜头表之后、制作之前）：按题材和镜头表查素材包，缺的自动去找（inventory_max 条以内），找不到的记进报告。
    按镜头表指纹只做一次（stock_inventory.json）。返回盘点结果；通道关着返回 None。"""
    if not enabled(c) or not cfg(c).get("inventory", True):
        return None
    topic = topic or c.j("topic.json") or {}
    sl = sl or c.j("shotlist.json") or {"shots": []}
    sha = _sl_sha(sl)
    old = c.j(INV)
    same = bool(old and old.get("sha") == sha)
    if same and not force and not old.get("deferred"):
        return old                                                  # 同一份镜头表只盘点一次（限流推迟的，下次调用再试那几条）
    g = cfg(c)
    wres = resolve_wishes(c, sl) if _wish_refs(sl) else {}          # 镜头表引用的待补素材先找（stages 先调过的：登记里有，不重找）
    if wres:
        sl = rebind(sl, wres)[0]
    mot = str(topic.get("motif") or "")
    fj = c.j("inputs/footage.json", {"clips": []}) or {"clips": []}
    names = {x["name"] for x in fj.get("clips", [])}
    kinds = overlay_kinds(c, mot)
    w_on, wparts = world_opts(c.series)
    shots, gaps = {}, []
    for i, s in enumerate(sl.get("shots") or []):
        row, gs = shot_needs(s, names, kinds, last=i == len(sl["shots"]) - 1, world_on=w_on, world_parts=wparts)
        shots[s["id"]] = row
        gaps += [dict(x, shot=s["id"]) for x in gs]
    if not kinds:                                                   # 按题材：素材包一个叠层都没有，就按系列给这个母题列的真材料去找
        for k in (g.get("motif_needs") or {}).get(mot) or []:
            if k in MAT:
                gaps.append({"need": f"黑底 / 深色底的实拍{MAT[k]['zh'][0]}（叠层 {k}）", "use": "overlay", "kind": k, "query": MAT[k]["en"],
                             "why": f"母题「{mot}」的素材包没有叠层表", "shot": "题材"})
    if w_on and len(names) < int(g["pack_min_clips"]):               # 按题材：素材包太薄，按开题的 footage_needs 补世界层（world_min = 0 的系列不补）
        for x in topic.get("footage_needs") or []:
            gaps.append(dict(topic_need(x, f"素材清单只有 {len(names)} 条（开题 footage_needs）"), shot="题材"))
    # 去重（同一种叠层只找一次；同样的需求只找一次）、找过的不再找；优先级：导演的 footage_wish > 主体 / 世界层 > 叠层 > 题材
    reg = {x.get("sha"): x for x in film(c).get("requests") or []}
    reqs, by = [], {}
    pri = lambda x: (0 if x.get("wish") else 1 if x["use"] in ("subject", "world") and x["shot"] != "题材" else 2 if x["shot"] != "题材" else 3)  # noqa: E731
    for x in sorted(gaps, key=pri):
        r, _ = norm_request(x, 0)
        if not r:
            continue
        key = ("ov", r["kind"]) if r["use"] == "overlay" and r["kind"] else ("r", r["sha"])
        if key in by:
            by[key]["shots"].append(x["shot"])
            continue
        r.update(shots=[x["shot"]], wish=bool(x.get("wish")))
        old_r = reg.get(r["sha"])
        if old_r and (old_r.get("status") in OK_ST or (same and old_r.get("status") in ("failed", "skipped_cap"))
                      or (old_r.get("from") == "wish" and old_r.get("status") == "failed")):
            r["done"] = old_r                                       # 同一份镜头表重盘（只为限流推迟的）、待补素材找过：没找到的不重找
        by[key] = r
        reqs.append(r)
    todo = [r for r in reqs if not r.get("done")]
    n_wish = len(_wish_used(c, sl, wres))
    cap = max(0, int(g["inventory_max"]) - n_wish)                  # 镜头表用上的待补素材（request_id）计入盘点上限，剩下的给别的缺口
    skipped = todo[cap:]
    todo = todo[:cap]
    for i, r in enumerate(todo):
        r["id"] = f"inv{i + 1}"
    got = []
    if todo:
        with LOCK:
            got = fulfil_all(c, todo, "开拍前盘点", "inventory")
    res = {r["sha"]: r for r in got}
    for rid, v in (wres or {}).items():
        if v.get("status") == "ok" and v.get("name"):
            for sid in v.get("shots") or []:
                shots.setdefault(sid, {}).setdefault("found", []).append({k: v.get(k) for k in ("name", "use", "need", "site", "page_url", "title",
                                                                                                "license", "sheet")})
    for r in reqs:
        e = res.get(r["sha"]) or r.get("done")
        for sid in r["shots"]:
            row = shots.setdefault(sid, {})
            if e and e.get("status") in OK_ST:
                row.setdefault("found", []).append({k: e.get(k) for k in ("name", "use", "need", "site", "src_id", "page_url", "title",
                                                                         "license", "w", "h", "fps", "dur", "overlay", "status", "sheet")})
            elif e:
                row.setdefault("missing", []).append(f"{r['need'][:50]}（{_txt(e.get('err'), 60)}）")
            else:
                row.setdefault("missing", []).append(f"{r['need'][:50]}（超过盘点上限 {cap} 条，没找）")
    out = {"sha": sha, "at": util.iso(), "mode": mode(c), "motif": mot, "clips": len(names), "overlay_kinds": sorted(kinds),
           **({"wishes": {rid: {k: v.get(k) for k in ("status", "name", "err", "shots") if v.get(k)} for rid, v in wres.items()}} if wres else {}),
           **({"cap": cap} if n_wish else {}), "gaps": len(reqs), "found": sum(1 for e in got if e["status"] in OK_ST) + sum(1 for r in reqs if (r.get("done") or {}).get("status") in OK_ST),
           "failed": [{"need": e["need"], "shots": next((r["shots"] for r in reqs if r["sha"] == e["sha"]), []), "err": e.get("err")}
                      for e in got if e["status"] == "failed"],
           "deferred": [{"need": e["need"], "shots": next((r["shots"] for r in reqs if r["sha"] == e["sha"]), []), "err": e.get("err")}
                        for e in got if e["status"] == "deferred"],
           "skipped": [{"need": r["need"], "shots": r["shots"]} for r in skipped],
           "note": "开拍前素材盘点（lib/stockreq.inventory）：每镜的主体 / 世界层 / 叠层够不够、导演的 footage_wish；缺的自动去找。"
                   "found = 补到的（制作代理的 ctx/footage.md 标成「盘点补的」）；failed / skipped = 没找到 / 超过上限（交付报告里列出）",
           "shots": shots}
    util.write_json(c.p(INV), out)
    c.log(f"开拍前素材盘点：{len(shots)} 镜，叠层表 {len(kinds)} 种，缺 {len(reqs)} 处"
          + (f"，补到 {out['found']}" if reqs else "") + (f"，没找到 {len(out['failed'])}" if out["failed"] else "")
          + (f"，超上限没找 {len(skipped)}" if skipped else "") + (f"，限流推迟 {len(out['deferred'])}（下次再试）" if out["deferred"] else "")
          + "（明细 stock_inventory.json）")
    return out


def world_fill(c, topic, need_n, have_n):
    """开题后实拍候选不到 need_n 条（系列 assets.world_min，没写 = 30）：按开题的 footage_needs 自动找、下、入库（inventory_max 条以内），
    不再停下来等人（规划 A1）。找过的需求不再找；通道关着返回 []。返回这一次的登记条目。"""
    if not enabled(c):
        return []
    g = cfg(c)
    reg = {x.get("sha"): x for x in film(c).get("requests") or []}
    todo = []
    for i, x in enumerate((topic or {}).get("footage_needs") or []):
        r, _ = norm_request(topic_need(x, f"开题：素材包实拍候选 {have_n} 条，不到 {need_n} 条（开题 footage_needs）"), i)
        if r and (reg.get(r["sha"]) or {}).get("status") not in OK_ST + ("failed", "skipped_cap"):
            r["id"] = f"topic{len(todo) + 1}"
            todo.append(r)
    todo = todo[:int(g["inventory_max"])]
    if not todo:
        return []
    with LOCK:
        return fulfil_all(c, todo, "开题", "topic")


def assigned_extra(c, sid):
    """开拍前盘点给这一镜补到的素材 [(名字, 用法)]（prepare_ctx 标进 ctx/footage.md 的「分给这一镜的」）。"""
    inv = ((c.j(INV) or {}).get("shots") or {}).get(sid) or {}
    return [(e["name"], "盘点补的" + USE_CN.get(e.get("use"), "素材")) for e in inv.get("found") or [] if e.get("name")]


def report_line(c):
    """交付报告表里「素材请求」一行（通道开过才有）。"""
    reg, inv = film(c), c.j(INV) or {}
    rq = reg.get("requests") or []
    ag = [x for x in rq if x.get("from") == "agent"]
    ok = lambda xs: [x for x in xs if x.get("status") in OK_ST]  # noqa: E731
    bad = [x for x in rq if x.get("status") in ("failed", "deferred", "skipped_cap")]
    say = {"failed": "没找到", "deferred": "限流 / 临时出错没下成", "skipped_cap": "超上限没找"}
    ovs = [x["overlay"]["kind"] + ("" if x["overlay"].get("pos_ok") else "（只能整屏）") for x in rq if (x.get("overlay") or {}).get("ok")]
    ag_n = [x for x in ag if x.get("status") != "skipped_cap"]
    n_rej = sum(int(x.get("rejected_n") or 0) for x in rq)
    return (f"| 素材请求 | 通道 {mode(c)}：开拍前盘点缺 {inv.get('gaps', 0)} 处、补到 {inv.get('found', 0)}；代理请求 {len(ag_n)} 条、找到 {len(ok(ag_n))}"
            + (f"；文字对题拒掉候选 {n_rej} 条" if n_rej else "")
            + (f"；新叠层 {'、'.join(ovs)}（自动验收过，建议看 p2/assets/<母题>/_review/el_<kind>*.jpg）" if ovs else "")
            + ("；到货联系表 <素材包>/_review/stock_*.jpg" if any(x.get("sheet") for x in rq) else "")
            + (f"；没拿到 {len(bad)} 条（只报告不拦）：" + "；".join(f"{x.get('shot')} {x['need'][:24]}（{say.get(x.get('status'), '')}：{_txt(x.get('err'), 40)}）"
                                                     for x in bad[:5]) if bad else "") + "；登记 inputs/stock.json、stock_inventory.json |")
