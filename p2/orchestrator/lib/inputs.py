# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""开题之后把母题素材包（p2/assets/<母题>/）接成这一集的输入文件（已有就不动，手写的优先）：

  inputs/footage.json   CHROMA 引擎素材目录格式 {"clips": [{name, file(绝对路径), content, use, dur, w, h, license}]}
                        只收已在本地的文件；镜头里 Take('F01', ...)。
  inputs/music.json     {file, bpm, downbeat_s, start_s, bars, drop_bar, title, credit, music_map, ...}
                        从 beats.json 挑「推荐窗口在片长范围内、低频对拍率最高」的一首（暂定，你可换）；素材包没有配乐、
                        或者母题还没有素材包（空包起跑），从系列 music.library 列的曲库里挑。
                        music_map = 音乐打击点表（每个 16 分格的真实起音帧、档位、名次）：
                        素材包里有（<曲名>.music_map.json）就用，没有就用引擎 Python 跑 tools/syncmeas.py map 生成到 inputs/。
                        已有的 music.json 缺这个字段时由这里补上（bpm / start_s / downbeat_s 三个数不动）。
                        试跑短片模式（--short N）：窗口起点不变、bars 截成 N（记 short、bars_full），music_map 按截断窗口过滤成
                        inputs/music_map.json（全窗口那份记在 music_map_full），last_accent / breath 在截断窗口里重新取。
  inputs/footage.md     给模型看的素材清单（一行一条）。"""
from __future__ import annotations

import json
import math
import os
import subprocess

from . import util

SYNCMEAS = os.path.join(util.P2, "tools", "syncmeas.py")          # A 的量法工具（跑在引擎 Python 里）


def pack_name(series, motif):
    """这一集用哪个素材包（p2/assets/<名>/）：系列配置写了 assets.motif（系列固定的素材包）就用它
    开题的母题是什么都一样；没写（CHROMA）= 开题的母题。"""
    fixed = str(((series or {}).get("assets") or {}).get("motif") or "").strip()
    return fixed or motif


def pack_dir(series, motif):
    root = os.path.join(util.PARENT, series["assets"]["dir"])
    name = pack_name(series, motif)
    d = os.path.join(root, name or "")
    return d if name and os.path.isdir(d) else None


OVERLAYS = "overlays.json"                                        # 母题素材包里的实拍元素叠层表（SR.element；引擎组维护）


def overlays_path(series, motif):
    """这一集母题的叠层表 <素材包>/overlays.json；没有返回 None。"""
    pack = pack_dir(series, motif)
    p = os.path.join(pack, OVERLAYS) if pack else None
    return p if p and os.path.isfile(p) else None


LOCAL_PACK = "stock_pack"                                         # 素材请求通道在 mock / STOCK_FAKE 下的本集素材包（lib/stockreq.py）


def extra_packs(ep):
    """这一集自己的素材包（<集>/stock_pack/，素材请求通道 mock / STOCK_FAKE 时下到这里，不碰 p2/assets/ 的真素材包）；没有就是 []。"""
    d = os.path.join(ep, LOCAL_PACK) if ep else None
    return [d] if d and os.path.isfile(os.path.join(d, "sources.json")) else []


def shared_packs(series):
    """系列配置 assets.shared_overlays 列的叠层表目录（存在的；没写 = []）。"""
    root = os.path.join(util.PARENT, series["assets"]["dir"])
    out = []
    for n in ((series or {}).get("assets") or {}).get("shared_overlays") or []:
        d = os.path.join(root, str(n).strip())
        if str(n).strip() and os.path.isfile(os.path.join(d, OVERLAYS)) and d not in out:
            out.append(d)
    return out


def element_tables(series, motif, ep=None):
    """这一集能用的叠层表文件（绝对路径，按优先级）：母题素材包 → assets.shared_overlays → 本集素材包（stock_pack）。
    渲染子进程的 CHROMA_ELEMENTS 照这个顺序传给引擎（引擎「先定义的 kind 赢」）；ctx/overlays.json 按同一顺序合并（规划 A3：
    每集的引擎副本原来只能从素材清单反查到母题包的叠层表，别的包里的叠层 SR.element 找不到）。"""
    out = []
    for p in [overlays_path(series, motif)] + [os.path.join(d, OVERLAYS) for d in shared_packs(series)] \
            + [os.path.join(d, OVERLAYS) for d in extra_packs(ep)]:
        if p and os.path.isfile(p) and os.path.abspath(p) not in out:
            out.append(os.path.abspath(p))
    return out


def overlays_merged(series, motif, ep=None):
    """这一集能用的叠层表：母题素材包的 overlays.json + assets.shared_overlays + 本集素材包（extra_packs）的；
    同名 kind 排在前面的优先（和引擎 SR.element_tables「先定义的赢」一致）。都没有返回 None。"""
    tabs = element_tables(series, motif, ep)
    if not tabs:
        return None
    out = None
    for p in tabs:
        d = util.read_json(p) or {}
        if out is None:
            out = json.loads(json.dumps(d))
            out.setdefault("kinds", {})
            continue
        for k, v in (d.get("kinds") or {}).items():
            out["kinds"].setdefault(k, v)
    return out


def _element_files(pack):
    """叠层表里当元素用的素材文件名 → kind（雪、雾、滴水……）；tested_not_used 里的不算。"""
    ov = util.read_json(os.path.join(pack, OVERLAYS)) if pack else None
    out = {}
    for k, v in ((ov or {}).get("kinds") or {}).items():
        for w in v.get("wins") or []:
            if w.get("file"):
                out.setdefault(os.path.basename(w["file"]), k)
    return out


def clip_source(it, path, flags=None, elements=None):
    """素材来源类型：cg（CG / 科普动画）、burned_text（画面带烧录字幕）、kbps（码率，低的放大就糊）、
    kind（实拍 / CG / 带字幕 / 元素，一格可以几样）、note（补充：比如「是竖着流下的液体，不是碎屑」）。
    来源：素材包 sources.json 条目自己写的字段优先，其次系列配置 assets.flags（按文件名），码率按文件大小 ÷ 时长算。"""
    fl = dict((flags or {}).get(os.path.basename(path)) or {})
    for k in ("cg", "burned_text", "note"):
        if it.get(k) is not None:
            fl[k] = it[k]
    size = it.get("size")
    if not size:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = None
    dur = it.get("dur")
    kbps = round(float(size) * 8 / float(dur) / 1000) if size and dur else None
    el = (elements or {}).get(os.path.basename(path))
    kind = [k for k, b in (("CG", fl.get("cg")), ("带字幕", fl.get("burned_text"))) if b] or ["实拍"]
    if el:
        kind.append(f"元素（叠层 {el}）")
    out = {"kind": " / ".join(kind), "cg": bool(fl.get("cg")), "burned_text": bool(fl.get("burned_text")), "kbps": kbps}
    if fl.get("note"):
        out["note"] = fl["note"]
    return out


def clip_entry(it, p, name, flags=None, els=None):
    """素材包 sources.json 的一条 → inputs/footage.json 的一条（引擎素材目录格式）。"""
    return {"name": name, "file": p, "content": it.get("content", ""),
            "use": it.get("use", ""), "dur": it.get("dur"), "w": it.get("w"), "h": it.get("h"),
            "fps": it.get("fps"), "license": it.get("license", ""), "page_url": it.get("page_url", ""),
            **clip_source(it, p, flags, els)}


def build_footage(pack, old=None, flags=None, extra=()):
    """old = 已有的 inputs/footage.json：已经发过名字的文件名字不变（镜头代码里写的是 F01…），新下载的接着编号。
    flags = 系列配置 assets.flags（按文件名标 CG / 烧录字幕 / 备注）；每条另记 kind / cg / burned_text / kbps（clip_source）。
    extra = 别的素材包（本集的 stock_pack，素材请求通道 mock 时下到这里）：接在母题素材包后面编号。"""
    names = {c["file"]: c["name"] for c in (old or {}).get("clips", [])}
    n = max([int(v[1:]) for v in names.values() if v[1:].isdigit()] or [0])
    clips, seen = [], set()
    packs = [d for d in [pack] + list(extra or []) if d]
    for d in packs:
        src = util.read_json(os.path.join(d, "sources.json"), {}) or {}
        els = _element_files(d)
        for it in src.get("items", []):
            p = os.path.abspath(os.path.join(d, it["file"]))
            if not os.path.isfile(p) or p in seen:
                continue                                        # 待下载的不收
            seen.add(p)
            if p not in names:
                n += 1
                names[p] = f"F{n:02d}"
            clips.append(clip_entry(it, p, names[p], flags, els))
    clips.sort(key=lambda c: c["name"])
    return {"note": "由 " + " + ".join(f"{os.path.relpath(d, util.PARENT)}/sources.json" for d in packs)
                    + " 生成（只收已在本地的；手写时删掉 auto）", "auto": True, "clips": clips}


def candidates(pack):
    """素材包里搜到的实拍候选数、已在本地的条数（规则树：卡点炫技必做「实拍候选 ≥30」）。"""
    if not pack:
        return 0, 0
    items = (util.read_json(os.path.join(pack, "sources.json"), {}) or {}).get("items", [])
    return len(items), sum(1 for it in items if os.path.isfile(os.path.join(pack, it.get("file", ""))))


def footage_md(fj, motion=None, assigned=None):
    """素材表（导演的 inputs/footage.md、代理的 ctx/footage.md）。motion = load_motion 的 {名字: 记录}：多两列「自带运动量」
    （1× 速度下最快约 1.67 s 窗口的有效运动量）和「建议变速」。assigned = 分给这一镜的 [(名字, 用法)]（主体、世界层、辅助素材）：
    表前写一行、表里标出来（代理优先用这几条）。"""
    motion = motion or {}
    mark = {n: u for n, u in (assigned or [])}
    head = []
    if mark:
        head.append("分给这一镜的：" + "；".join(f"{u} {n}" for n, u in (assigned or [])) + "（先用这几条；别的镜头的主体素材不拿来当主体）\n")
    if motion:
        head.append("自带运动量 = 编排器按原速（1×）量的有效运动量（原片整片 7.6–9.5；纹理画面横移 8 px/帧约 6.9）：「最快一段」是素材里最快那 1.67 s，"
                    "「从」= 那一段从素材第几秒起（Take 的 src 取那附近才有这个数），「整条」是整条平均。口径是 Take(..., interp='blend') 1× 放进 60 fps 的片子；"
                    "低于 60 fps 的素材不写 interp='blend'、也不变速到 60 ÷ 帧率倍以上，1× 放进来是重复帧，编排器量出来只剩零头（0.2–0.9）。"
                    "慢素材只能当底：配 2–3 倍变速（Take(speed=…)），或者叠跟拍（SR.track）；实拍叠层（SR.element）只当陪衬。\n")
        rows = ["| 名字 | 来源 | 时长 | 分辨率 | 帧率 | 自带运动量（最快一段 / 整条） | 最快一段从 | 建议变速 | 用在哪 | 内容 |",
                "|---|---|---|---|---|---|---|---|---|---|"]
    else:
        rows = ["| 名字 | 来源 | 时长 | 分辨率 | 用在哪 | 内容 |", "|---|---|---|---|---|---|"]
    if any(c.get("cg") or c.get("burned_text") for c in fj.get("clips", [])):
        head.append("「来源」= 实拍 / CG / 带字幕 / 元素（实拍叠层 SR.element 用的那几条）：CG、带字幕的不当主体（放大就糊、字幕会露出来），"
                    "只当辅助，取景避开字幕。\n")
    for c in fj.get("clips", []):
        nm = c["name"] + (f"（{mark[c['name']]}）" if c["name"] in mark else "")
        kind = (c.get("kind") or "实拍") + (f"（{c['kbps']} kbps）" if c.get("cg") and c.get("kbps") else "")
        content = c.get("content", "") + (f"｜注意：{c['note']}" if c.get("note") else "")
        if motion:
            m = motion.get(c["name"]) or {}
            e, w, at = m.get("eff_1x"), m.get("eff_film_1x"), m.get("at_s")
            fps = c.get("fps") or m.get("fps")
            hint = m["speed"] if str(m.get("speed") or "").startswith("静图") else speed_hint(e, w)   # speed_hint(None) = 没量
            rows.append(f"| {nm} | {kind} | {c.get('dur') or '?'} s | {c.get('w')}×{c.get('h')} | {'?' if not fps else f'{float(fps):g}'} | "
                        f"{'—' if e is None else f'{e:g}'} / {'—' if w is None else f'{w:g}'} | {'—' if at is None else f'{at:g} s'} | "
                        f"{hint} | {c.get('use', '')} | {content} |")
        else:
            rows.append(f"| {nm} | {kind} | {c.get('dur') or '?'} s | {c.get('w')}×{c.get('h')} | {c.get('use', '')} | {content} |")
    if not fj.get("clips"):
        rows.append("| （没有实拍素材：只能做设计帧和整屏色） | | | | | |" + (" | | | |" if motion else ""))
    return "".join(head) + ("\n" if head else "") + "\n".join(rows) + "\n"


# ----------------------------------------------------------------------------- 素材自带运动量
# 慢的素材能比快的慢 4–5 倍（同一个包里也有快有慢）：导演和代理看不到每条素材自己有多快，就会拿慢素材当主体、
# 不变速。这里用 p2/tools/effmotion.py 按原速量一遍，缓存在素材包的 footage_motion.json（源文件大小 / 修改时间变了才重量）。
# 量法：素材先缩到 480×270（铺满裁切，无损），按原帧率提取；窗口 = 1.67 s（60 fps 下 100 帧）、一拍窗口 / 两拍窗口也按帧率折算；
# 1× 放进 60 fps 的片子按 Take(..., interp='blend') 的口径：每帧的量 = 原帧率量 × fps / 60（> 60 fps 的先抽成 60 fps）。
# 引擎 Take 默认 interp='nearest'（最近帧取样 = 重复帧）：effmotion 会把每个新源帧当成切点扣掉，量出来只剩零头（11-04 审查实测
# 24 fps 素材表上 4.47、nearest 1× 实测 0.20；25 fps 素材 blend 1.95 和表一致）。所以素材表、手册都写明低于 60 fps 要 blend 或变速。
EFFMOTION = os.path.join(util.P2, "tools", "effmotion.py")
FOOT_MOTION = "footage_motion.json"
FM_VERSION = 1
FM_PY = r'''
import json, os, subprocess, sys
sys.path.insert(0, sys.argv[1])
import effmotion as EM
src, fps, tmpd = sys.argv[2], float(sys.argv[3] or 0), sys.argv[4]
vf = "scale=480:270:force_original_aspect_ratio=increase,crop=480:270"
if fps > 61 or fps <= 0:
    vf = "fps=60," + vf
    fps = 60.0
small = os.path.join(tmpd, "small.mp4")
subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", src, "-map", "0:v:0", "-an", "-t", "120", "-vf", vf,
                "-c:v", "libx264", "-qp", "0", "-preset", "ultrafast", small], check=True)
x = EM.extract(small)
k = fps / 60.0
win = max(12, int(round(100 * k)))
r, _ = EM.analyse(x, None, (), (), win=win, step=max(1, int(round(5 * k))), hwin=max(6, int(round(24 * k))), windows=True,
                  hwin_long=max(6, int(round(48 * k))))
best, at = None, None
for a0, v in (r.get("windows") or {}).items():
    if v and (best is None or v["eff"] > best):
        best, at = v["eff"], int(a0)
f = r.get("film") or {}
print(json.dumps({"eff_1x": None if best is None else round(best * min(1.0, k), 2), "eff_native": None if best is None else round(best, 2),
                  "at_s": None if at is None else round(at / fps, 2),
                  "eff_film_1x": None if f.get("eff") is None else round(f["eff"] * min(1.0, k), 2), "fps": fps,
                  "n": int(r.get("n_frames") or 0), "win": win}))
'''


def speed_hint(e, whole=None):
    """1× 自带运动量（最快一段 e、整条 whole）→ 建议变速（导演 / 代理看的一句话）。「1 倍就够」只给最快一段已经接近原片口径的
    （3.0 就标「1 倍就够」，单靠它撑不起一镜）；整条偏慢的提醒 src 取最快那段附近。"""
    if e is None:
        return "没量"
    if e < 1.5:
        h = "只能当底，配 2–3 倍变速，或叠跟拍"
    elif e < 3.0:
        h = "1.5–2 倍变速，或叠单向运镜 / 跟拍"
    elif e < 4.5:
        h = "1–1.5 倍，再叠一件单向的事（运镜、切点换画面）"
    else:
        h = "1 倍就够，可以当主体"
    if whole is not None and e >= 1.5 and whole < 0.5 * e:
        h += "（只有最快那段这么快，src 取「从」附近；别的时段按整条配变速）"
    return h


def _fm_one(py, path, fps):
    import tempfile
    with tempfile.TemporaryDirectory(prefix="fm-") as td:
        try:
            r = subprocess.run([py, "-I", "-c", FM_PY, os.path.dirname(EFFMOTION), path, str(fps or 0), td], capture_output=True,
                               text=True, timeout=1800, env={"PATH": util.BIN_PATH, "HOME": util.HOME})
        except Exception as e:  # noqa: BLE001
            return None, f"{type(e).__name__}: {str(e)[:160]}"
        if r.returncode != 0:
            return None, (r.stderr or r.stdout or "")[-300:]
        try:
            return json.loads(r.stdout.strip().splitlines()[-1]), None
        except (ValueError, IndexError) as e:
            return None, f"输出读不了：{e}"


def _probe_fps(path):
    """素材清单没写帧率（webm 之类）时现场 ffprobe 一下；读不到返回 0（量的时候先抽成 60 fps）。"""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate,r_frame_rate",
                            "-of", "json", path], capture_output=True, text=True, timeout=60, env={"PATH": util.BIN_PATH, "HOME": util.HOME})
        st = (json.loads(r.stdout or "{}").get("streams") or [{}])[0]
        for k in ("avg_frame_rate", "r_frame_rate"):
            a, _, b = str(st.get(k) or "0/0").partition("/")
            if float(b or 0) > 0 and float(a) > 0:
                return round(float(a) / float(b), 3)
    except Exception:  # noqa: BLE001
        pass
    return 0


def footage_motion(pack, py=None, log=None, compute=True, workers=3):
    """素材包每条本地实拍的自带运动量（缓存 <素材包>/footage_motion.json，键 = 文件名）。compute = 缺的 / 源文件变了的现场量
    （每条几秒到一分钟，几条同时量）；False 只读缓存。返回 {文件名: {eff_1x, at_s, eff_film_1x, fps, speed, base_only, ...}}。"""
    if not pack:
        return {}
    p = os.path.join(pack, FOOT_MOTION)
    cache = util.read_json(p) or {}
    clips = dict(cache.get("clips") or {})
    if cache.get("version") != FM_VERSION:
        clips = {}
    items = (util.read_json(os.path.join(pack, "sources.json"), {}) or {}).get("items", [])
    todo = []
    for it in items:
        f = os.path.abspath(os.path.join(pack, it["file"]))
        if not os.path.isfile(f):
            continue
        st = os.stat(f)
        old = clips.get(os.path.basename(f)) or {}
        if old.get("size") == st.st_size and old.get("mtime") == int(st.st_mtime):
            continue
        todo.append((f, it, st))
    if todo and compute and py and os.path.exists(py) and os.path.exists(EFFMOTION):
        if log:
            log(f"  素材自带运动量：量 {len(todo)} 条（{os.path.relpath(pack, util.PARENT)}，按原速，缓存到 {FOOT_MOTION}）", echo=False)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            res = list(ex.map(lambda t: _fm_one(py, t[0], t[1].get("fps") or _probe_fps(t[0])), todo))
        for (f, it, st), (r, err) in zip(todo, res):
            if r is None:
                if log:
                    log(f"  素材自带运动量没量成 {os.path.basename(f)}：{err}", echo=False)
                continue
            clips[os.path.basename(f)] = dict(r, file=os.path.relpath(f, pack), size=st.st_size, mtime=int(st.st_mtime),
                                              dur=it.get("dur"), speed=speed_hint(r.get("eff_1x"), r.get("eff_film_1x")),
                                              base_only=r.get("eff_1x") is not None and r["eff_1x"] < 1.5, at=util.iso())
        util.write_json(p, {"version": FM_VERSION, "note": "素材自带运动量（inputs.footage_motion 生成；源文件大小 / 修改时间变了自动重量）："
                            "eff_1x = 1× 速度下最快约 1.67 s 窗口（60 fps 的 100 帧）的有效运动量（p2/tools/effmotion.py，和门禁同一口径）；"
                            "at_s = 那个窗口从素材第几秒起；eff_native = 同一窗口按原帧率逐帧量的（2 倍变速以上大致是这个量）；eff_film_1x = 整条的；speed = 建议变速；base_only = 太慢，只能当底（配 2–3 倍变速，"
                            "或叠跟拍）。原片整片 7.6–9.5，最快的火、电素材 3.9–5.6",
                            "params": {"window_s": round(100 / 60, 2), "size": [480, 270], "fill": "铺满裁切", "speed": 1.0},
                            "clips": dict(sorted(clips.items()))})
    return clips


def load_motion(clips_list):
    """这一集素材清单的自带运动量：{名字: 记录}（按文件名到素材包的 footage_motion.json 里找；生图的静图记 0）。"""
    out, seen = {}, {}
    for c in clips_list or []:
        f = c.get("file") or ""
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            out[c["name"]] = {"eff_1x": 0.0, "speed": "静图：只能当底，要运镜或叠层才动"}
            continue
        d = os.path.dirname(f)
        for up in (d, os.path.dirname(d)):
            if up not in seen:
                seen[up] = (util.read_json(os.path.join(up, FOOT_MOTION)) or {}).get("clips") or {}
            rec = seen[up].get(os.path.basename(f))
            if rec:
                out[c["name"]] = rec
                break
    return out


# ----------------------------------------------------------------------------- 音乐打击点表（music_map）
def map_path_for(music_file):
    """素材包里和曲子同名的打击点表：music/<曲名>.music_map.json（beats.py / syncmeas.py map 生成）。"""
    return os.path.splitext(music_file)[0] + ".music_map.json"


def map_ok(mm, m):
    """这份 music_map 是不是这一集的窗口：起点、BPM 一样，小节数够（全窗口）。"""
    if not mm or not mm.get("slots"):
        return False
    need = int(m.get("bars_full") or m.get("bars") or 0)
    return (abs(float(mm.get("start_s", -1)) - float(m["start_s"])) < 0.01 and abs(float(mm.get("bpm", 0)) - float(m["bpm"])) < 0.01
            and int(mm.get("bars") or 0) >= need)


def syncopation(mm):
    """重音落在反拍 and（sub = 2）上的比例（本曲 21/41）：导演提示词提醒「别按第 1、3 拍套路排」用。"""
    acc = [e for e in mm.get("slots") or [] if e.get("accent")]
    return round(sum(1 for e in acc if e.get("sub") == 2) / len(acc), 2) if acc else None


def make_map(py, m, out, log=None):
    """用引擎 Python 跑 tools/syncmeas.py map 生成全窗口的 music_map（相位全曲拟合；只在窗口里拟合会锁到反拍）。
    成功返回 out，跑不起来返回 None（原因进日志）。"""
    why = ("没有引擎 Python" if not (py and os.path.exists(py)) else
           f"没有 {os.path.relpath(SYNCMEAS, util.ROOT)}" if not os.path.exists(SYNCMEAS) else
           "配乐文件不在" if not os.path.exists(m["file"]) else None)
    if why:
        if log:
            log(f"  音乐打击点表生成不了：{why}", echo=False)
        return None
    cmd = [py, "-I", SYNCMEAS, "map", m["file"], "--bpm", str(m["bpm"]), "--downbeat-s", str(m["downbeat_s"]),
           "--start-s", str(m["start_s"]), "--bars", str(int(m.get("bars_full") or m["bars"])), "--fps", "60", "--out", out]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env={"PATH": util.BIN_PATH, "HOME": util.HOME})
    except Exception as e:  # noqa: BLE001
        if log:
            log(f"  音乐打击点表生成失败：{type(e).__name__}: {str(e)[:160]}", echo=False)
        return None
    if r.returncode != 0 or not os.path.exists(out):
        if log:
            log(f"  音乐打击点表生成失败（rc {r.returncode}）：{(r.stderr or r.stdout or '')[-300:]}", echo=False)
        return None
    return out


def find_map(m, pack=None, ep=None, py=None, log=None):
    """全窗口的 music_map：素材包里同名的 → inputs/ 里生成过的 → 现场生成到 inputs/。都不行返回 None。"""
    cand = [map_path_for(m["file"])]
    if ep:
        cand.append(os.path.join(ep, "inputs", os.path.basename(map_path_for(m["file"]))))
    for p in cand:
        if os.path.exists(p) and map_ok(util.read_json(p), m):
            return p
    if ep:
        out = make_map(py, m, cand[-1], log)
        if out and map_ok(util.read_json(out), m):
            return out
    return None


def total_frames(bpm, bars):
    """bars 小节的片长（帧）：和 stages.frame_at(bars + 1) 同一个算法。"""
    return int(math.floor(bars * 4 * 3600.0 / bpm + 0.5))


def short_map(mm, bars, bpm):
    """试跑短片：music_map 按截断窗口（前 bars 小节）过滤。drop 在窗口里就不变；急停只留窗口里的；
    last_accent = 截断窗口里最后一个 S / A 档重音；breath 不在窗口里（或不在 DROP 前）就重新取 DROP 前 2 小节里最安静的一拍。"""
    total = total_frames(bpm, bars)
    keep = [dict(e) for e in mm["slots"] if int(e["i"]) < bars * 16]
    d = {k: v for k, v in mm.items() if k != "slots"}
    d.update(bars=bars, total_f=total, slots=keep, bar_map=list(mm.get("bar_map") or [])[:bars], short=bars,
             bars_full=mm.get("bars"), note=f"试跑短片：原曲窗口前 {bars} 小节（编排器从 {mm.get('bars')} 小节的全窗口表截出来的）")
    d["stops"] = [x for x in mm.get("stops") or [] if x["F"] < total and x["bar"] <= bars]
    drop = mm.get("drop") if (mm.get("drop") or {}).get("F", total) < total else None
    d["drop"] = drop
    acc = [e for e in keep if e.get("tier") in ("S", "A") and (e.get("total") or 0) >= 0.6 and e["F"] < total]
    la = max(acc, key=lambda e: e["F"], default=None)
    d["last_accent"] = {k: la[k] for k in ("F", "bar", "beat", "sub")} if la else None
    br = mm.get("breath") or {}
    if not (br.get("F") is not None and br["F"] < total and (not drop or br["F"] < drop["F"])):
        by = {int(e["i"]): e for e in keep}
        b_drop = (drop["bar"] - 1) * 4 + drop["beat"] - 1 if drop else bars * 4
        q = [(sum((by.get(b * 4 + k) or {}).get("total") or 0 for k in range(4)), -b, b) for b in range(max(0, b_drop - 8), b_drop)]
        if q:
            b = min(q)[2]
            br = {"F": int(math.floor(b * 3600.0 / bpm + 0.5)), "bar": b // 4 + 1, "beat": b % 4 + 1, "sub": 0}
        else:
            br = None
    d["breath"] = br
    # 「重」（stages.sync_rows）和整片 sync_top 的最强 N 个重音按窗口长短折算（全窗口 16 个 ≈ 每 1.6 s 一个；8 小节就是 8 个），
    # 不然 DROP 后 4 小节里要做 16 个整屏事件（每拍一个），连最后一个重音之后海报里的 B 档也被算成「重」
    nfull = int(mm.get("bars") or bars)
    d["top_n"] = max(4, int(round(16 * bars / max(1, nfull))))
    return d


def music_ready(m, short=None, pack=None, ep=None, py=None, log=None):
    """music.json 补齐（返回新的 dict；只写截断的打击点表，不写 music.json）：music_map（全窗口）、syncopation；
    short = N 时截成前 N 小节（窗口起点不变，DROP 必须落在截断的窗口里），截断的打击点表写到 <ep>/inputs/music_map.json。"""
    m2 = dict(m)
    n = short or m2.get("short")
    if n and not m2.get("short"):
        if m2.get("bars") and int(n) > int(m2["bars"]):
            raise util.Stop(2, "bad_args", f"试跑短片 {n} 小节：配乐窗口只有 {m2['bars']} 小节（inputs/music.json 的 bars），"
                                           f"--short 最多给 {m2['bars']}（窗口起点不变）")
        drop = int(m2.get("drop_bar") or 0)
        if drop and drop > n:
            raise util.Stop(2, "bad_args", f"试跑短片 {n} 小节：配乐的 DROP 在第 {drop} 小节，不在截断的窗口里（窗口起点不变）；"
                                           f"--short 至少给 {drop}（DROP 前铺垫 {drop - 1} 小节 + DROP 后至少 1 小节）")
        m2.update(short=n, bars_full=m2["bars"], bars=n)
        m2["window_events"] = [e for e in m2.get("window_events") or [] if int(e.get("bar") or 0) <= n]
        m2["short_note"] = (f"试跑短片模式：原曲窗口 {m2['bars_full']} 小节只做前 {n} 小节（DROP 前 {drop - 1} 小节 + DROP 后 {n - drop + 1} 小节），"
                            "窗口起点不变；全窗口的打击点表在 music_map_full")
    full = m2.get("music_map_full") or m2.get("music_map")        # 刚截断时 music_map 还是全窗口那份；截过的那份小节数不够，map_ok 不认
    fm = util.read_json(full) if full and os.path.exists(full) else None
    if not map_ok(fm, m2):
        full = find_map(m2, pack, ep, py, log)
        fm = util.read_json(full) if full else None
    if not fm:
        return m2
    m2["syncopation"] = syncopation(fm)
    if m2.get("short"):
        out = os.path.join(ep, "inputs", "music_map.json")
        sm = short_map(fm, int(m2["short"]), float(m2["bpm"]))
        if util.read_json(out) != sm:
            util.write_json(out, sm)
        m2.update(music_map=out, music_map_full=full)
    else:
        m2["music_map"] = full
    return m2


PREFER_BPM = 135.5                                              # CHROMA：BPM > 135 优先（第 1 秒 10 格落在 16 分音符上）


def pick_music(pack, g, prefer=PREFER_BPM):
    """prefer = 这个 BPM 以上优先（系列 music.prefer_bpm_min；None = 不分，只按低频对拍率）。"""
    bt = util.read_json(os.path.join(pack, "beats.json"), {}) or {}
    lo, hi = g.get("duration_s_full") or g["duration_s"]          # 短片模式把片长门禁换掉了：挑曲子照全片的窗口挑
    blo, bhi = g["bpm"]
    best = None
    for t in bt.get("tracks", []):
        w = t.get("window") or {}
        p = os.path.join(pack, t["file"])
        if not (os.path.isfile(p) and lo <= (w.get("dur_s") or 0) <= hi and blo <= t["bpm"] <= bhi):
            continue
        mf = os.path.join(pack, t["music_map_file"]) if t.get("music_map_file") else None
        if mf and os.path.isfile(mf) and not (util.read_json(mf, {}) or {}).get("drop"):
            continue                                              # 打击点表里没有 DROP：卡不上大拍（和 tools/music_lib.sh 同一判法）
        # 先看 BPM > 135（CHROMA 第 1 秒 10 格落在 16 分音符上：9 个 16 分音符要 < 60 帧；系列 music.prefer_bpm_min 可改、null 不分），
        # 再看低频对拍率
        score = (prefer is not None and t["bpm"] > prefer, (t.get("grid_fit") or {}).get("low_peak_hit_rate") or 0)
        if best is None or score > best[0]:
            best = (score, t, w, p)
    if not best:
        return None
    score, t, w, p = best
    ev = os.path.join(pack, t["events_file"]) if t.get("events_file") else None
    bf = w.get("bar_frames") or []
    wev = []
    for e in w.get("other_hits_in_window", []):
        if bf:
            k = min(range(len(bf)), key=lambda i: abs(bf[i] - e["frame"]))
            wev.append({"kind": e["kind"], "bar": k + 1, "frame": round(e["frame"], 2)})
    m = {"file": os.path.abspath(p), "bpm": t["bpm"], "downbeat_s": t["first_downbeat_s"], "start_s": w["start_s"],
         "bars": w["n_bars"], "drop_bar": w["build_bars"] + 1, "title": f"{t.get('artist', '')} – {t.get('title', '')}",
         "credit": t.get("credit", ""), "license": t.get("license", ""), "source": t.get("source", ""),
         "page_url": t.get("page_url", ""), "events": ev if ev and os.path.isfile(ev) else None,
         "window_events": wev, "auto": True, "low_peak_hit_rate": score[1],
         "note": "编排器从素材包自动挑的（" + (f"BPM > {prefer:g} 优先，再" if prefer is not None else "")
                 + "按低频对拍率；推荐窗口在片长范围内）；暂定，你可换：改本文件后 --from 1 重做"}
    mp = map_path_for(m["file"])
    if os.path.exists(mp) and map_ok(util.read_json(mp), m):
        m["music_map"] = mp
    return m


def music_library(series, pack=None):
    """曲库：自动挑配乐时看哪几个素材包（有 beats.json 的目录，按顺序）：这一集的素材包在前，再接系列配置 music.library 列的包。
    空包起跑：新母题还没有素材包、或包里没有配乐时从曲库挑；实拍由开题 footage_needs 和素材请求通道去找。"""
    root = os.path.join(util.PARENT, series["assets"]["dir"])
    out = [pack] if pack and os.path.isfile(os.path.join(pack, "beats.json")) else []
    for n in ((series or {}).get("music") or {}).get("library") or []:
        d = os.path.join(root, str(n).strip())
        if str(n).strip() and os.path.isfile(os.path.join(d, "beats.json")) and d not in out:
            out.append(d)
    return out


def pick_from_library(series, pack, g, prefer=PREFER_BPM):
    """这一集的素材包挑得出就用它的（和以前一样）；挑不出再在曲库里挑（同一个排序：BPM 优先档，再低频对拍率）。"""
    got = []
    for d in music_library(series, pack):
        m = pick_music(d, g, prefer)
        if m and d == pack:
            return m
        if m:
            m["library"] = os.path.relpath(d, util.PARENT)
            m["note"] += f"；这一集的素材包没有能用的配乐，从曲库 {m['library']} 挑的（系列 music.library）"
            got.append(m)
    return max(got, key=lambda x: (prefer is not None and x["bpm"] > prefer, x.get("low_peak_hit_rate") or 0), default=None)


TEMPLATES = ("inputs/music.json", "music.json")                # 素材包里手写的 music.json 样板（相对素材包）


def music_template(pack):
    """素材包里手写的配乐样板（<包>/inputs/music.json 或 <包>/music.json，格式同 <集>/inputs/music.json）：file / music_map / events
    写的相对路径按素材包解析成绝对路径；文件不在返回 None。返回的 dict 带 template（样板路径），不带 auto（手写的，不是自动挑的）。"""
    for rel in TEMPLATES:
        p = os.path.join(pack, rel)
        m = util.read_json(p) if os.path.isfile(p) else None
        if not (isinstance(m, dict) and m.get("file") and m.get("bpm") and m.get("start_s") is not None):
            continue
        m = dict(m)
        for k in ("file", "music_map", "events"):
            v = m.get(k)
            if isinstance(v, str) and v and not os.path.isabs(v):
                cands = [os.path.join(pack, v), os.path.join(os.path.dirname(p), v)]
                m[k] = os.path.abspath(next((x for x in cands if os.path.exists(x)), cands[0]))
        if not os.path.isfile(m["file"]):
            continue
        if m.get("music_map") and not os.path.isfile(m["music_map"]):
            m.pop("music_map")                                     # 打击点表没有就让 music_ready 现场生成
        if m.get("events") and not os.path.isfile(m["events"]):
            m["events"] = None
        m.setdefault("downbeat_s", 0.0)
        m.pop("auto", None)
        m["template"] = os.path.abspath(p)
        return m
    return None


def template_len(m):
    """样板窗口长度（秒）：bars × 4 拍 × 60 / bpm。"""
    return float(m.get("bars") or 0) * 240.0 / float(m.get("bpm") or 120)


def template_fits(m, g):
    """样板窗口放得进全片片长门禁（g.duration_s_full 或 duration_s）才给全片用
    全片 30–62 s，导演每一轮都过不了 bars / 片长）。"""
    lo, hi = g.get("duration_s_full") or g["duration_s"]
    return lo <= template_len(m) <= hi


def ensure(c, topic):
    """返回 {pack, footage_n, music}；只写 inputs/ 下还没有的文件。music.json 已有时只补 music_map（和短片模式的截断），别的不动。"""
    pack = pack_dir(c.series, topic.get("motif"))
    extra = extra_packs(c.ep)                                 # 本集素材包（素材请求通道 mock / STOCK_FAKE 时下到这里）
    os.makedirs(c.p("inputs"), exist_ok=True)
    fp, mp = c.p("inputs", "footage.json"), c.p("inputs", "music.json")
    old = util.read_json(fp)
    if old is None or (old.get("auto") and (pack or extra)):  # 自动生成的每次都重扫：素材下载后自动接上，旧名字不变
        fj = (build_footage(pack, old, (c.series.get("assets") or {}).get("flags"), extra) if pack or extra
              else {"note": f"母题「{topic.get('motif')}」没有素材包（空包起跑：实拍由开题 footage_needs 和素材请求通道去找）",
                    "clips": []})
        if fj != old:
            util.write_json(fp, fj)
    fj = util.read_json(fp)
    for d in [pack] + extra:                                  # 素材自带运动量（缓存在素材包；mock 只读缓存）
        if d:
            footage_motion(d, getattr(getattr(c, "renderer", None), "py", None), c.log, compute=not c.mock)
    util.write(c.p("inputs", "footage.md"), footage_md(fj, load_motion(fj.get("clips", []))))
    if not os.path.exists(mp):
        m = music_template(pack) if pack else None
        if m and not getattr(c, "short", None) and not template_fits(m, c.g):
            c.log(f"配乐：素材包的手写样板 {os.path.relpath(m['template'], util.PARENT)} 是 {m.get('bars')} 小节"
                  f"（{template_len(m):.1f} s），不在全片片长 {c.g['duration_s'][0]}–{c.g['duration_s'][1]} s 里：只给试跑短片（--short）用，"
                  "全片照 beats.json 挑窗口")
            m = None
        if m:
            c.log(f"配乐：照素材包的手写样板 {os.path.relpath(m['template'], util.PARENT)}（{m.get('title', '')}，窗口 {m.get('start_s')} s 起 "
                  f"{m.get('bars')} 小节，DROP 第 {m.get('drop_bar')} 小节）")
        else:
            mu = (getattr(c, "series", None) or {}).get("music") or {}
            m = pick_from_library(c.series, pack, c.g, mu.get("prefer_bpm_min", PREFER_BPM) if "prefer_bpm_min" in mu else PREFER_BPM)
            if m and m.get("library"):
                c.log(f"配乐：从曲库 {m['library']} 挑了「{m['title']}」")
            if not m and pack and music_template(pack) and not getattr(c, "short", None):
                raise util.Stop(2, "bad_args", f"全片没有能用的配乐窗口：素材包 {os.path.basename(pack)} 只有试跑样板（inputs/music.json，"
                                               f"{music_template(pack).get('bars')} 小节），beats.json 里没有 {c.g['duration_s'][0]}–"
                                               f"{c.g['duration_s'][1]} s 的窗口。试跑加 --short；全片先在素材包里配一首全片的曲子 / 窗口")
        if m:
            util.write_json(mp, m)
    m = util.read_json(mp)
    if m and m.get("file") and os.path.isabs(m["file"]):
        m2 = music_ready(m, getattr(c, "short", None), pack, c.ep, getattr(getattr(c, "renderer", None), "py", None), c.log)
        if m2 != m:
            util.write_json(mp, m2)
            c.log("inputs/music.json 补上了：" + "、".join(k for k in ("music_map", "short", "syncopation") if m2.get(k) != m.get(k))
                  + (f"（音乐打击点表 {os.path.relpath(m2['music_map'], util.ROOT)}）" if m2.get("music_map") else "（没有音乐打击点表）"))
            m = m2
        if not m.get("music_map"):
            c.log("  注意：没有音乐打击点表（music_map）：卡点只能按格点排，实测卡点门禁量不了", echo=False)
    return {"pack": pack, "footage_n": len(fj.get("clips", [])), "music": m}
