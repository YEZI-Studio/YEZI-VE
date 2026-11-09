# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""素材候选对不对题：标题 / 描述 vs 需求的文字判定。在 rank 之前跑，便宜、本地、确定，离线可重放。
光看标题就判得出来的明显跑题（要碎玻璃来了往杯子里倒水、要水下生物来了岸上的人）在这里拒掉。

两条，都只看候选自带的字段（title、descr、tags），中文需求经 stockreq 现有的 MATERIALS / VOCAB 转成英文：
  R1 主体：候选的标题 / 描述 / 标签里至少要有一个主体词或它的同义词（中文标题也按词表转成英文再对；复合词按前半对：
     Steelworks → steel、Sparklers → spark，本身就在词表里的词不拆：Fireworks 不当 fire）。
     need / query 里写的英文词都算主体词（代理补一个英文词就能对上），单位（km、mph……）、纯数字、数字打头（1080p）、≤ 2 个字母的
     不算；QX-7、A320 这类型号算。底色、景别、慢动作、深海、颜色、往下落这些只算环境（搜索词全是 OR 召回，撞上「black」「slow」
     不说明什么）——一个主体词都没有时才拿环境词对（「一道光扫过」对 light）；连环境词都没有（只剩单位、数字）就不放行：「没有可判定的主体词」。
  R2 跑题：候选的标题 / 描述里有人物、食物、水果、饮料、车、新闻、桌游，而需求没提到同一类，拒。
     饮料：酒倒 / 掉进杯子照拒；只撞上「倒 / 掉进杯子」或只撞上 wine、milk、soda 这类词的，主体词（glass、cup 这些
     容器词不算）对上了就不拒：酒杯碎裂、牛奶飞溅、汽水气泡、冰块掉进一杯水是对题的，玻璃杯里冒啤酒泡不是碎玻璃。
     风景、机器只在需求在水下（水下没有树、天）时才算跑题：熔岩流下山、闪电划过夜空、林间落雪、排气口冒的白烟都对题
     （主体认不出时也不拿风景拒）。
  R1 泛词：side、view、twin、race、start、engine、cockpit、landscape 这类只说「哪一面 / 什么动作 / 几个 / 什么地方」的词（GENERIC）不单独
     算对上：需求里还有别的主体词时，候选要对上那几个里的一个（「Side Mirror View」不算要的主体，「Start of Karting Race」不算 runner）。
     需求只有泛词时照旧全算。排序（coverage）照旧数全部的词。
  R1 专名：query 里的型号（QX-7、A320，带数字）或专名（词中有大写）就是要那一个东西，候选里要有（复数也算）；没有型号时才看专名。
  R1 多义词（POLY）：sleeper 在候选里挨着 soft / hard / train / car / berth……是卧铺，不是轨枕；需求没说卧铺时拒。
  R2 跑题词只按复数对（training 不是 train）；「车」那一类在铁路类需求下（railway、rail、铁路、铁轨、轨枕……）算提到了；
     卫星图（satellite pictures / imagery，Commons 分类里也算）需求没提卫星就拒（要地面景色来了卫星动画）；
     需求要的是某一种人（ROLES：运动员、飞行员）时，候选里的人点了这种人（runner、pilot）就不算跑题·人物。
判不出来的交给到货联系表（ctx/look/stock/）：代理和审片看图再定。"""
from __future__ import annotations

import re
import unicodedata

# 真材料（stockreq.MATERIALS 的 kind）对应的英文主体词：stockreq 那边的英文是搜索词（shards = crushed ice），这里另列。
KIND_EN = {
    "marine_snow": ("particle", "particulate", "speck", "mote", "flake", "dust", "snow"),
    "glints": ("glint", "sparkle", "glitter", "shimmer", "twinkle"),
    "jellies": ("jellyfish", "jelly", "medusa"),
    "plankton": ("plankton", "bioluminescent", "bioluminescence", "luminous", "glowing", "fluorescent"),
    "bubbles": ("bubble", "foam", "fizz"),
    "rays": ("ray", "beam", "godray", "sunbeam", "shaft", "sunlight"),
    "caustics": ("caustic", "caustics", "refraction", "ripple"),
    "ink": ("ink", "silt", "sediment", "mud"),
    "shards": ("shard", "debris", "fragment", "piece", "chunk", "rubble", "splinter", "shrapnel", "crumb", "crushed", "shatter", "smash",
               "broken", "burst"),
    "diamond_dust": ("glitter", "sparkle", "crystal", "dust", "particle"),
    "snow": ("snow", "snowflake", "snowfall", "flake", "blizzard"),
    "fog": ("fog", "mist", "misty", "haze", "smoke", "steam", "vapor", "vapour", "murky", "turbid", "plume"),
    "drip": ("drip", "drop", "droplet"),
    "sparks": ("spark", "ember"),
    "dust": ("dust", "particle", "powder", "speck"),
    "shrimp": ("shrimp", "krill", "prawn", "crevette", "crustacean", "amphipod"),
    "powder": ("powder", "holi", "pigment", "dust"),
    "rubble": ("rubble", "debris", "brick", "concrete", "rock", "stone", "gravel", "demolition", "fragment", "chunk"),
    "glass": ("glass", "shatter", "smash", "broken", "break", "crack", "shard", "bottle", "window", "mirror", "pane", "windshield"),
}
# 同义词组（英文这边）：需求里出现组里任何一个词，整组都算主体词
GROUPS = (
    ("explosion", "explode", "blast", "detonation", "burst", "eruption"),
    ("volcano", "volcanic", "erupt", "eruption", "lava", "magma"),      # 岩浆、熔岩的需求收火山喷发（铁水的 molten 不连到火山）
    ("shatter", "smash", "broken", "break", "crack", "crash", "crush", "crushed", "splinter"),
    ("debris", "shard", "fragment", "piece", "chunk", "rubble", "splinter", "shrapnel", "crumb"),
    ("confetti",), ("rubber",),
    ("hydrothermal", "vent", "smoker", "chimney", "cheminee", "fumarole"),
    ("smoke", "plume", "fog", "mist", "steam", "vapor"),
    ("splash", "splatter"),
    ("fire", "flame", "blaze", "burning", "campfire", "bonfire", "wildfire", "fireball", "fireplace", "inferno"),
    ("lava", "magma", "molten"),
    ("lightning", "thunder", "thunderstorm", "thunderbolt", "electric", "electricity", "arc", "plasma"),
    ("ice", "icy", "frozen", "frost", "glacier", "iceberg", "crystal"),
    ("snow", "snowflake", "snowfall", "snowstorm", "snowy", "snowing", "blizzard"),
    ("fish", "shoal"), ("star", "starry", "galaxy", "nebula"), ("city", "skyline", "urban"), ("sand", "desert", "dune"),
    ("wave", "surf"), ("metal", "metallic", "steel", "chrome", "iron"), ("cloud",), ("neon",), ("firework", "pyrotechnic"), ("paint",),
    ("railway", "railroad", "rail", "railtrack"), ("sprinter", "sprint", "runner"),
)
# 泛词：只说哪一面、什么视角、什么动作、几个、什么地方，单独撞上不说明是要的东西。需求里还有别的主体词时，候选要对上那几个
GENERIC = set("""side front rear back top bottom low high angle pov cab cockpit window onboard forward overhead ground level start race pass passing
approach acceleration accelerate stride drive ride travel journey twin double single pair multiple engine motor detail part body structure
terrain landscape scenery countryside field area scene outdoor nature track speed fast real actual archive archival historic historical vintage""".split())
# 多义词：候选里挨着这些词时是另一个意思（soft sleeper / sleeper train = 卧铺，不是轨枕）；需求里写了这个意思（英文同一个搭配
# 或中文词）就不拒
POLY = {"sleeper": (("soft", "hard", "train", "trains", "car", "cars", "bus", "berth", "berths", "cabin", "compartment", "carriage", "coach",
                     "class", "sleeping"), ("卧铺", "软卧", "硬卧", "卧车"))}
_SAT = re.compile(r"\bsatellite\s+(?:image|imagery|picture|photo|view|animation|loop|data)s?\b|\b(?:image|picture|photo|animation)s?\s+(?:of|from)\s+"
                  r"(?:a\s+|the\s+)?satellites?\b", re.I)                # 卫星图：Commons 的「Animations of satellite pictures」分类也算
# 只算环境、不算主体：底色、景别、速度、深海 / 水下、颜色、往哪走、素材站套话
CONTEXT = set("""black white background backdrop dark darkness slow motion slowmo time lapse timelapse macro backlit close closeup up top
view aerial wide shot video footage clip stock free 4k hd uhd deep sea ocean underwater water surface marine seabed abyss trench rov
submersible drifting rising sinking floating falling fall moving flying jumping swirling spinning flowing growing descending through
shining isolated texture effect abstract cinematic beautiful orange red blue green yellow purple pink gold golden silver colored colorful
color colour multicolor vibrant bright dry liquid night light element overlay layer world""".split())
STOP = set("a an the of in on with and or to for at by from into onto its is are this that it as".split())
# 跑题的几类：候选的标题 / 描述里有，需求里（英文词或中文字）没提到同一类，就拒。SOFT 里的几类只在需求在水下时才拒
FAMILIES = {
    "人物": (("man", "men", "woman", "women", "person", "people", "girl", "boy", "guy", "lady", "child", "children", "kid", "baby", "couple",
             "family", "crowd", "portrait", "diver", "diving", "swimmer", "mermaid", "surfer", "dancer", "worker", "businessman", "lgbtq",
             "gay", "tuxedo", "fashion", "explorer", "selfie"), ("人", "潜水", "男", "女", "脸", "模特")),
    "食物": (("food", "burger", "hamburger", "fries", "pasta", "fried", "grilled", "skewer", "dish", "restaurant", "cuisine", "meal",
             "cooking", "chef", "kitchen", "pizza", "bread", "cake", "dessert", "sushi", "barbecue", "breakfast", "dinner", "lunch",
             "gourmet", "bean", "snack", "candy", "chocolate"), ("食物", "菜肴", "吃", "餐")),
    "水果蔬菜": (("fruit", "oranges", "lime", "lemon", "citrus", "kiwi", "avocado", "apple", "banana", "strawberry", "cherry", "grape",
               "watermelon", "berry", "vegetable", "broccoli", "tomato", "cucumber", "halves", "sliced"),
             ("水果", "橙子", "柠檬", "苹果", "蔬菜")),
    "饮料": (("drink", "drinking", "wine", "juice", "beer", "coffee", "tea", "milk", "cocktail", "alcohol", "whiskey", "soda",
             "goblet", "cup", "mug"), ("倒水", "倒入", "倒进", "酒", "饮", "咖啡", "茶", "奶", "杯")),
    "车": (("car", "truck", "bus", "train", "motorcycle", "bike", "bicycle", "traffic", "taxi", "kart", "karting"),
          ("车", "铁路", "铁轨", "轨枕", "轨道交通", "地铁")),   # 不收 vehicle：航天的 launch / research vehicle
    "机器": (("fan", "exhaust", "ceiling", "appliance", "ventilation", "duct", "office", "laptop", "computer", "phone", "smartphone"),
           ("风扇", "机器", "电脑", "手机")),
    "风景": (("river", "tree", "forest", "lake", "mountain", "meadow", "road", "street", "beach", "sunrise", "sunset", "park",
             "garden", "highland", "coast", "sky"), ("河", "树", "森林", "湖", "山", "路", "街", "海滩", "日出", "日落", "公园", "天空", "海岸")),
    "新闻": (("bodycam", "dashcam", "news", "interview", "police", "cop", "deputy", "deputies", "suspect", "robbery", "gun",
             "shotgun", "machete", "weapon", "army", "military", "soldier", "war"), ("新闻", "警", "枪", "军")),
    "桌游": (("poker", "casino", "domino", "dominoes", "chess", "gaming"), ("牌", "棋")),
    "卫星图": (("satellite", "orbit", "orbital"), ("卫星", "轨道上", "太空")),            # 候选一侧按 _SAT 的搭配认，不按单词
}
# 需求本身是风景 / 城市（海面、海浪、天空、城市夜景……）时，画面里有风景、车、人是正常的：这几类算提到了
SOFT = ("机器", "风景")
UNDER = (("underwater", "seabed", "abyss", "trench", "rov", "submersible", "hydrothermal"), ("深海", "水下", "海底", "海沟", "深渊", "热液"))
IMPLY = {"风景": ("ocean", "wave", "surf", "beach", "coast", "sky", "cloud", "mountain", "aerial", "landscape", "city", "street", "river",
                "lake", "forest", "sunrise", "sunset", "iceberg", "glacier", "aurora"),
         "车": ("city", "street", "road", "urban", "skyline", "traffic", "railway", "railroad", "rail", "railtrack", "locomotive", "tram",
               "subway", "metro"),                                   # 铁路类需求下 train / railway 不判跑题
         "人物": ("city", "street", "crowd")}
# 要的就是某一种人（运动员、飞行员）：候选里有人、同时点了这种人（runner、sprinting）就不判跑题·人物；只有人、没点这种人的照拒（「Children playing with building blocks」不因为需求是短跑运动员就收）
ROLES = (("sprinter", "sprint", "runner", "athlete", "pilot", "astronaut", "racer", "jockey", "cyclist", "skier", "swimmer"),
         ("运动员", "选手", "飞行员", "宇航员", "车手", "骑手"))
VESSEL = {"glass", "cup", "mug", "bottle", "jug", "goblet"}
_POUR = re.compile(r"\b(?:pour|fall|drop)\w*\s+(?:\w+\s+){0,4}?in(?:to)?\s+(?:a|an|the)\s+(?:\w+\s+)?(?:glass|cup|mug|bottle|jug)", re.I)   # 倒 / 掉进杯子 = 饮料；倒铁水不算
_NEG = re.compile(r"(不是|不当|不算|没有|别当|并非|不像|不要|不含|别有|别用|避免)[^，。；、,;（）()|/]{0,12}")   # 「不要有潜水员」不算提到人
_MIXKIT_TAIL = re.compile(r"\b(is a free stock video|this video is perfect for projects)\b.*$", re.I)       # Mixkit 描述的套话（标签清单）
UNITS = set("km kmh kph kmph mph khz mhz ghz kwh rpm fps bpm lbs sec mins hrs hdr hdr10".split())   # ≤ 2 个字母的（m、s、kg、hz）按长度就丢了；HDR10 是规格不是型号
META = set("video footage clip stock free".split())                                       # 没有主体词、拿环境词对时也不算（素材站套话）
NOT_NAMES = set("hd uhd fhd pov rov cg cgi gif diy usa uk eu tv nasa noaa esa".split())       # 大写但不是要的东西本身（机构名只说来源）
_KNOWN = (CONTEXT | VESSEL | {w for g in GROUPS for w in g} | {w for v in KIND_EN.values() for w in v}
          | {w for en, _ in FAMILIES.values() for w in en} | {w for v in IMPLY.values() for w in v})   # 词表里的词：对题时不当复合词拆


_ENG = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9\-]+")    # 中文需求里的英文词（左边界：4K60、1080p60 里不抠出 K60、p60）


def junk(w):
    """不算搜索词 / 主体词的英文词：≤ 2 个字母、数字打头（纯数字、12.5、1080p、60fps）、单位（km/h 的 km、mph……）。
    型号（QX-7、A320、B-2）不算：字母打头、去掉连字符 ≥ 3 位。stockreq.en_query 兜底时也用这一条。"""
    w = str(w or "").lower().replace("-", "")
    return len(w) <= 2 or w[:1].isdigit() or w in UNITS


def _tok(text):
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"(?<=[a-z])-(?=\d)", "", t)                      # 型号 QX-7 / F-16 → qx7 / f16（需求和标题同一个写法）
    return [w for w in re.findall(r"[a-z0-9]+", t) if len(w) > 1 and w not in STOP]


def _joined(text):
    """候选一侧的型号写法：「Falcon 9」「Apollo 11」「Boeing 747」拆成了两个词，并成 falcon9、apollo11、boeing747 再和需求的型号比。"""
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return {a + b for a, b in re.findall(r"([a-z]+)[\s_]+(\d+)", t)}


def _vars(t):
    """一个英文词的几种写法（复数、-ing、-ed、法语阴性 / 复数）：Commons 上 Ifremer 的标题是法语（crevettes、hydrothermale）。"""
    out = {t}
    if len(t) > 3:
        if t.endswith("ies"):
            out.add(t[:-3] + "y")
        if t.endswith("es"):
            out.add(t[:-2])
        if t.endswith("s") and not t.endswith("ss"):
            out.add(t[:-1])
        if t.endswith("ing") and len(t) > 5:
            out |= {t[:-3], t[:-3] + "e"}
        if t.endswith("ed") and len(t) > 4:
            out |= {t[:-2], t[:-1]}
        if t.endswith("aux"):
            out.add(t[:-3] + "al")
        if t.endswith("e"):
            out.add(t[:-1])
    return out


def _plural(w):
    """跑题词只按复数对（workers → worker、cars → car）：-ing / -ed 不拆（training 不是 train）。"""
    out = {w}
    if len(w) > 3 and w.endswith("ies"):
        out.add(w[:-3] + "y")
    if len(w) > 3 and w.endswith("es"):
        out.add(w[:-2])
    if len(w) > 2 and w.endswith("s") and not w.endswith("ss"):
        out.add(w[:-1])
    return out


def _generic(w):
    return bool(_vars(w) & GENERIC)


_CAPS = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9\-]*[A-Z][A-Za-z0-9\-]*)")   # 词中（不只开头）有大写：QuickTime、LED、iPhone


def _names(text, need=""):
    """query 里的专名，小写：大小写混写的词（QuickTime、iPhone）；全大写的缩写（SUV、LED）只在需求原文里也写了它时才算
    （代理在 need 里点名了某个缩写 = 要那一个东西；只在 query 里的 SUV、LED、BMX、DJI 是类别词）。
    单位、规格、词表里认识的词（HD、UHD、POV、ROV、CG）不算。"""
    out = []
    for w in _CAPS.findall(str(text or "")):
        t = w.lower().replace("-", "")
        if w.replace("-", "").isupper() and not re.search(r"(?<![A-Za-z0-9])" + re.escape(w) + r"(?![A-Za-z0-9])", str(need or "")):
            continue
        if not junk(t) and not any(ch.isdigit() for ch in t) and t not in _KNOWN and t not in GENERIC and t not in NOT_NAMES:
            out.append(t)
    return list(dict.fromkeys(out))


def _poly_other(seen, terms):
    """候选里多义词用的是另一个意思（soft sleeper、sleeper train）→ 那个搭配；需求里写了这个意思的不算。"""
    said = terms.get("text") or []
    pairs = {(a, b) for a, b in zip(said, said[1:])}
    for i, w in enumerate(seen):
        for p, (other, zh) in POLY.items():
            if p not in _vars(w) or p not in terms.get("mention", ()) or any(z in terms.get("need", "") for z in zh):
                continue
            for a, b in ((seen[i - 1] if i else "", w), (w, seen[i + 1] if i + 1 < len(seen) else "")):
                x = a if b == w else b
                if x in other and not any(_vars(c) & {p} and d == x or _vars(d) & {p} and c == x for c, d in pairs):
                    return f"{a} {b}"
    return None


def _expand(words):
    """主体词 → (所有写法 + 同义词组, 原词 + 同义词组：拆复合词时只拿这些当前半，不拿去掉 e 的写法——flam 不配 flamingo)。"""
    out, roots = set(), set()
    for w in words:
        vs = _vars(w)
        out |= vs
        roots.add(w)
        for g in GROUPS:
            if vs & set(g):
                out |= set(g)
                roots |= set(g)
    return out, roots


def _hit(w, subj, roots):
    """候选的一个词对不对得上主体词：词形（复数、-ing、-ed……）对上，或者是复合词、前半是主体词（steelworks → steel、ironworks → iron、
    sparklers → spark：主体词 ≥ 4 个字母，按词干算后半 ≥ 3 个字母，所以 sparkling、starting 的词干 sparkl、start 不算）。
    本身就在词表里的词不拆（fireworks 是烟花，不当 fire）。"""
    vs = _vars(w)
    if vs & subj:
        return True
    if vs & _KNOWN:
        return False
    stem = min(vs, key=len)
    return any(len(r) >= 4 and len(stem) - len(r) >= 3 and stem.startswith(r) for r in roots)


def _zh_en(text):
    """候选标题 / 描述里的中文按 stockreq.VOCAB 最长匹配转成英文词（中文标题「岩浆流动」→ magma，和需求的主体词同一套同义词组对）。"""
    from .stockreq import VOCAB, _RE_CJK
    t = str(text or "")
    if not _RE_CJK.search(t):
        return []
    out, i, keys = [], 0, sorted(VOCAB, key=len, reverse=True)
    while i < len(t):
        k = next((k for k in keys if t.startswith(k, i)), None)
        out += _tok(VOCAB[k]) if k else []
        i += len(k) if k else 1
    return out


def need_terms(r):
    """一条需求 → {subject: 主体词（含同义词；一个都没有时是环境词；空 = 没有可判定的主体词，judge 全拒）, roots: 原词 + 同义词组（拆复合词用）,
    words: 认出来的主体词（报告用）, mention: 需求提到的所有英文词, families: 需求提到的跑题类, zh: 认出来的中文词（对中文标题用）,
    under: 需求在水下}。中文经 stockreq.MATERIALS（真材料 kind）和 VOCAB（词表，最长匹配）转英文；need / query 里的英文词（不算环境词、
    不是单位 / 数字 / 短词）原样都算主体词。"""
    from .stockreq import MAT, MATERIALS, VOCAB, materials_in
    need = _NEG.sub(" ", str(r.get("need") or ""))
    kind = str(r.get("kind") or "").lower()
    mats = materials_in(need)
    kinds = [k for k, _ in mats]
    kinds += [k for k, _, _, syn in MATERIALS if kind and k not in kinds and (kind == k or any(s in kind for s in syn))]
    words, zh, i, keys = [], [z for _, z in mats], 0, sorted(VOCAB, key=len, reverse=True)
    while i < len(need):                                            # 中文按词表最长匹配（和 stockreq.en_query 一样走法）
        k = next((k for k in keys if need.startswith(k, i)), None)
        words += _tok(VOCAB[k]) if k else []
        zh += [k] if k and set(_tok(VOCAB[k])) - CONTEXT else []
        i += len(k) if k else 1
    eng = [w for w in _tok(" ".join(_ENG.findall(need)) + " " + str(r.get("query") or "")) if not junk(w)]
    said = [w for w in dict.fromkeys(words + eng) if w not in CONTEXT]
    core = [w for w in said if not _generic(w)] or said              # 泛词（side、twin、race、start……）只在没有别的主体词时才算
    subj = core + [w for k in kinds for w in KIND_EN.get(k) or MAT[k]["syn"]]
    env = [] if subj else [w for w in dict.fromkeys(words + eng) if w in CONTEXT and w not in META]   # 没有主体词：拿环境词对（一道光 → light）
    mention = set()
    for w in words + eng + subj:
        mention |= _vars(w)
    fams = {f for f, (en, zh) in FAMILIES.items() if mention & set(en + IMPLY.get(f, ())) or any(z in need for z in zh)}
    under = bool(mention & set(UNDER[0])) or any(z in need for z in UNDER[1])
    roles = sorted(mention & set(ROLES[0])) + [z for z in ROLES[1] if z in need]
    q = str(r.get("query") or "").strip()                           # 型号（qx7、a320）按代理现在的 query 算（代理改写了 query = 不要原来的型号了）
    models = [w for w in dict.fromkeys(_tok(q or " ".join(_ENG.findall(need)))) if not junk(w) and any(ch.isdigit() for ch in w)]
    models = models or _names(q or " ".join(_ENG.findall(need)), need)   # 没有型号时，专名（大小写混写的词、need 里点名的缩写）也是要那一个东西
    subject, roots = _expand(subj or env)
    concepts = []                                                   # 主体词按「一个东西」分组：同义词组、同一种材料、写法有重叠的并成一个
    for s, rt in ([_expand([w]) for w in said] + [_expand(KIND_EN.get(k) or MAT[k]["syn"]) for k in kinds]) if subj else [_expand([w]) for w in env]:
        for m in [m for m in concepts if m[0] & s]:
            concepts.remove(m)
            s, rt = s | m[0], rt | m[1]
        concepts.append((s, rt))
    return {"subject": subject, "roots": roots, "words": list(dict.fromkeys(core + said + kinds + env))[:8], "mention": mention, "families": fams,
            "kinds": kinds, "zh": list(dict.fromkeys(zh)), "under": under, "concepts": concepts,
            "models": models, "roles": roles, "need": need, "text": _tok(" ".join(_ENG.findall(need)) + " " + q)}


def coverage(it, terms, core=False):
    """候选的标题对上了几个主体词（按需求 / query 里的原词数，同义词组、同一种材料算一个）。stockreq.rank 用：对题的里面标题对上词多的排前面
    （几条都过了文字对题时，不然技术分高的泛泛画面会排在对题的档案前面）。
    只看标题：Pexels 的描述是一长段自动生成的话，数进去就是谁的描述长谁占便宜。core = 只数非泛词的（rank 先比这个：「start、race、track」
    三个泛词对上的不排在主体词对上的前面）。"""
    pool = _tok(it.get("title")) + _zh_en(it.get("title"))
    return sum(1 for subj, roots in terms.get("concepts") or [] if (not core or not subj & GENERIC) and any(_hit(w, subj, roots) for w in pool))


def judge(it, terms):
    """一条候选对不对题 → (收不收, 原因)。原因只在拒的时候写，例：「跑题·水果蔬菜（oranges）」「标题 / 描述里没有 debris、confetti……」。"""
    title = str(it.get("title") or "")
    descr = _MIXKIT_TAIL.sub("", str(it.get("descr") or ""))
    tags = it.get("tags") or ""
    tags = tags if isinstance(tags, str) else " ".join(map(str, tags))
    seen = _tok(title + " " + descr)
    subj, roots = terms["subject"], terms.get("roots") or set()
    if not subj:                                                    # 只剩单位 / 数字 / 短词：搜回来的是 OR 撞上的
        return False, "没有可判定的主体词（need / query 里只有单位、数字或太短的词）"
    for f, (en, _) in FAMILIES.items():
        if f in terms["families"] or (f in SOFT and not terms["under"]):
            continue
        if f == "饮料":
            m = _POUR.search(title + " " + descr)
            d = next((w for w in seen if _vars(w) & (set(en) - VESSEL)), None)
            sub = any(_hit(w, subj - VESSEL, roots - VESSEL) for w in seen)
            hit = (m.group(0) if m else d) if (m and d) or ((m or d) and not sub) else None
        elif f == "卫星图":
            m = _SAT.search(" ".join([title, descr, tags]))
            hit = m.group(0) if m else None
        else:
            hit = next((w for w in seen if _plural(w) & set(en)), None)
            if hit and f == "人物" and terms.get("roles") and any(_vars(w) & set(ROLES[0]) for w in seen):
                hit = None                                          # 要短跑运动员，候选里的人是 runner / sprinting：不跑题
        if hit:
            return False, f"跑题·{f}（{hit}）"
    other = _poly_other(seen, terms)
    if other:                                                       # soft sleeper / sleeper train = 卧铺（要轨枕来了卧铺车厢）
        return False, f"多义词：候选里是「{other}」（另一个意思）"
    pool = seen + _tok(tags) + _zh_en(title + " " + descr + " " + tags)
    if not any(_hit(w, subj, roots) for w in pool) and not any(z in title + descr + tags for z in terms["zh"]):
        core = [w for w in terms["words"] if not _generic(w)]
        gen = [w for w in terms["words"] if _generic(w) and _vars(w) & set().union(*[_vars(p) for p in pool])] if core else []
        return False, ((f"只对上泛词 {'、'.join(gen[:3])}，要的 {'、'.join(core[:4])} 没有" if gen else   # 复核 L2：拒因不把泛词说成「没有」
                        "标题 / 描述里没有要的东西（" + "、".join((core or terms["words"])[:5]) + "）"))
    have = set().union(*[_plural(w) for w in pool]) | _joined(title + " " + descr + " " + tags)
    if terms.get("models") and not set(terms["models"]) & have:     # 写了型号 / 专名就是要那一个东西：撞上 speed、record、car 不算
        return False, f"要的是 {'、'.join(terms['models'])}（型号 / 专名），标题 / 描述里没有"
    return True, ""


def split(items, r):
    """候选 → (对题的, [(拒的候选, 原因)])，顺序不变。"""
    terms = need_terms(r)
    keep, rej = [], []
    for it in items or []:
        ok, why = judge(it, terms) if isinstance(it, dict) else (False, "不是条目")
        if ok:
            keep.append(it)
        else:
            rej.append((it, why))
    return keep, rej
