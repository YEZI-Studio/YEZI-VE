# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""生图通道（run_episode.py --imagegen codex；--backend codex 时默认开，DeepSeek 默认关 = 和以前完全一样）。

  1. 代理（DeepSeek 或 Codex）在 work/out/images.json 写生图请求：{"images": [{id, use, ratio, prompt, light?}]}；
     每镜 ≤ per_shot（默认 6）个 id，全片 ≤ per_film（默认 40）次生成（config_codex.json 的 imagegen）。
  2. 代理跑完这一次后，编排器在沙箱外跑一次 codex exec（-s workspace-write，工作目录 = 该镜 work/assets/gen/，用 Codex 内置
     image_gen = 你的 ChatGPT 额度）
     不出现人和字、照片级、不要麻麻赖赖、纹理正俯视平铺、自检重来一次）。图存进 work/assets/gen/<id>.png，清单
     work/assets/gen/manifest.json；全片登记 <集>/inputs/gen_images.json，同时并进引擎素材目录（镜头里 Take('<镜头>_<id>', ...)，
     静图）。
  3. 生成成功的，马上再叫一次代理「图到了，接进去」（imagegen.followup，默认开）；之后的返修、续跑任务书里也带着清单。
     生成失败只报告（日志、manifest、报告），不拦。
默认用途：远景、背景、纹理、主体参考（照片和 CG 混用容易「AI 感重」）；规则写在 prompts_codex/imagegen_rules.md。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import threading
import zlib

from . import codex as CX
from . import util

GEN_LOCK = threading.Lock()                     # 几镜并行时生图排队：全片张数上限和 inputs/gen_images.json 是共享的

REQ = os.path.join("out", "images.json")
GEN = os.path.join("assets", "gen")
USES = ("远景", "背景", "纹理", "主体参考", "主体")
RATIOS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "4:3": (4, 3), "3:4": (3, 4), "3:2": (3, 2), "2:3": (2, 3),
          "21:9": (21, 9)}
EXTS = (".png", ".jpg", ".jpeg", ".webp")
LICENSE = "AI 生成（Codex 内置 image_gen，你的 ChatGPT 账号）"


def mode(c):
    m = getattr(getattr(c, "args", None), "imagegen", None)
    if m in (None, "auto"):
        return "codex" if CX.backend(c) == "codex" else "off"
    return m


def enabled(c):
    return mode(c) == "codex"


def gcfg(c):
    return (getattr(c, "cc", None) or CX.DEFAULTS)["imagegen"]


# ----------------------------------------------------------------------------- 登记
def film(c):
    return c.j(os.path.join("inputs", "gen_images.json")) or {
        "note": "生图通道登记（编排器写）：clips = 生成成功的（并进引擎素材目录，镜头里 Take(name, ...)）；failed = 失败的；"
                "calls = 每次 codex 生图调用（token、用时）。全片上限按 clips + failed 计。", "clips": [], "failed": [], "calls": []}


def used(c):
    d = film(c)
    return len(d.get("clips") or []) + len(d.get("failed") or [])


def manifest(work):
    return util.read_json(os.path.join(work, GEN, "manifest.json")) or {"images": []}


def clip_name(sid, iid):
    return f"{sid}_{iid}"


def _sha(r):
    return hashlib.sha256(json.dumps([r["use"], r["ratio"], r["prompt"], r.get("light", "")], ensure_ascii=False).encode()).hexdigest()[:12]


# ----------------------------------------------------------------------------- 请求
def parse_requests(work):
    """读 work/out/images.json → (请求列表, 提醒)。id 规范成小写英文 / 数字 / 下划线；用途、比例不认识就换成默认并提醒。"""
    p = os.path.join(work, REQ)
    if not os.path.exists(p):
        return [], []
    try:
        raw = json.loads(util.read(p))
    except ValueError as e:
        return [], [f"out/images.json 不是合法 JSON（{str(e)[:80]}），这次没生成"]
    items = raw.get("images") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return [], ["out/images.json 要写成 {\"images\": [...]}，这次没生成"]
    out, notes, seen = [], [], set()
    for i, x in enumerate(items):
        if not isinstance(x, dict):
            notes.append(f"第 {i + 1} 条不是对象，跳过")
            continue
        iid = re.sub(r"[^a-z0-9_]+", "_", str(x.get("id") or "").strip().lower()).strip("_")[:24]
        if not iid or not iid[0].isalpha():
            iid = f"img{i + 1}"
        if iid in seen:
            notes.append(f"id {iid} 重复，只收第一条")
            continue
        seen.add(iid)
        use = str(x.get("use") or "背景").strip()
        if use not in USES:
            notes.append(f"{iid}：用途「{use}」不认识，按「背景」生成（只认 {'、'.join(USES)}）")
            use = "背景"
        ratio = str(x.get("ratio") or "16:9").replace("：", ":").replace(" ", "")
        if ratio not in RATIOS:
            notes.append(f"{iid}：比例 {ratio} 不认识，按 16:9 生成")
            ratio = "16:9"
        prompt = re.sub(r"\s+", " ", str(x.get("prompt") or "")).strip()[:800]
        if not prompt:
            notes.append(f"{iid}：没写 prompt，跳过")
            continue
        out.append({"id": iid, "use": use, "ratio": ratio, "prompt": prompt,
                    "light": re.sub(r"\s+", " ", str(x.get("light") or "")).strip()[:200]})
    return out, notes


def pending(c, work, reqs):
    """还要生成的（新的 id，或同一 id 改了 prompt / 比例 / 用途）；同样的请求失败过不再重试。按每镜 / 全片上限截断。"""
    g = gcfg(c)
    man = {x["id"]: x for x in manifest(work)["images"]}
    todo, notes = [], []
    for r in reqs:
        old = man.get(r["id"])
        if old and old.get("sha") == _sha(r):
            if old.get("status") != "ok":
                notes.append(f"{r['id']}：同样的请求上次没生成出来（{str(old.get('err'))[:60]}），不再重试；改 prompt 才会再生成")
            continue
        todo.append(r)
    have = set(man)
    new = [r for r in todo if r["id"] not in have]
    room = max(0, int(g["per_shot"]) - len(have))
    for r in new[room:]:
        notes.append(f"{r['id']}：每镜最多 {g['per_shot']} 张，没生成")
        todo.remove(r)
    left = max(0, int(g["per_film"]) - used(c))
    for r in todo[left:]:
        notes.append(f"{r['id']}：全片最多 {g['per_film']} 张（已用 {used(c)}），没生成")
    return todo[:left], notes


# ----------------------------------------------------------------------------- 图片小工具（标准库）
def img_size(p):
    """PNG / JPEG / WebP 的像素尺寸（读文件头）；认不出返回 None。"""
    try:
        with open(p, "rb") as f:
            d = f.read(1 << 16)
    except OSError:
        return None
    if d[:8] == b"\x89PNG\r\n\x1a\n" and len(d) >= 24:
        return struct.unpack(">II", d[16:24])
    if d[:4] == b"RIFF" and d[8:12] == b"WEBP":
        if d[12:16] == b"VP8X":
            return 1 + int.from_bytes(d[24:27], "little"), 1 + int.from_bytes(d[27:30], "little")
        if d[12:16] == b"VP8 ":
            return struct.unpack("<H", d[26:28])[0] & 0x3FFF, struct.unpack("<H", d[28:30])[0] & 0x3FFF
        if d[12:16] == b"VP8L":
            b = int.from_bytes(d[21:25], "little")
            return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
    if d[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(d):
            if d[i] != 0xFF:
                i += 1
                continue
            m = d[i + 1]
            if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", d[i + 5:i + 9])
                return w, h
            i += 2 + struct.unpack(">H", d[i + 2:i + 4])[0]
    return None


def placeholder_png(p, w, h, seed=0):
    """--mock 用的占位图：一张带渐变的 PNG（标准库 zlib 写）。"""
    a, b = (seed * 37) % 200 + 30, (seed * 91) % 200 + 30
    rows = b"".join(b"\x00" + bytes(v for x in range(w) for v in ((a + x * 120 // w) % 256, (b + y * 120 // h) % 256, 128))
                    for y in range(h))
    chunk = lambda t, data: struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF)  # noqa: E731
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows, 6)) \
        + chunk(b"IEND", b"")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(png)


def _find(gdir, iid):
    for e in EXTS:
        p = os.path.join(gdir, iid + e)
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            return p
    return None


def rescue(gdir, todo, thread, home=None):
    """image_gen 默认存在 $CODEX_HOME/generated_images/<这一次的 thread_id>/。Codex 没把图拷进来时：这一次的目录里
    没被拷过的图（按内容比）正好和缺的 id 一样多，就按生成顺序对上补拷；对不上就不动（算失败，只报告）。返回补了几张。"""
    miss = [r for r in todo if not _find(gdir, r["id"])]
    d = os.path.join(home or CX.CODEX_HOME, "generated_images", str(thread or ""))
    if not miss or not thread or not os.path.isdir(d):
        return 0
    dig = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()  # noqa: E731
    have = {dig(os.path.join(gdir, f)) for f in os.listdir(gdir) if f.lower().endswith(EXTS)}
    cand = sorted((os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(EXTS)), key=os.path.getmtime)
    cand = [p for p in cand if dig(p) not in have]
    if len(cand) != len(miss):
        return 0
    for r, p in zip(miss, cand):
        shutil.copy2(p, os.path.join(gdir, r["id"] + os.path.splitext(p)[1].lower()))
    return len(miss)


def _reply_notes(text):
    """codex 最终回复里的 {"images": [{id, ok, note}]} → {id: note}（只用来写失败原因）。"""
    try:
        o = util.extract_json(text or "")
    except ValueError:
        return {}
    return {str(x.get("id")): str(x.get("note") or "") for x in (o.get("images") or []) if isinstance(x, dict)}


# ----------------------------------------------------------------------------- 生成
def task_text(c, sid, gdir, todo):
    topic = c.j("topic.json") or {}
    col = topic.get("signature_color") or {}
    look = (f"世界是去色的，只留签名色 {col.get('name', '')}（{col.get('hex', '')}）：图里以中性灰阶为主，这种颜色只出现在光和发光的地方；"
            "引擎会再统一调色" if col else "以中性灰阶为主，引擎会再统一调色")
    rows = "\n".join(f"| {r['id']} | {r['ratio']} | {r['use']} | {r['prompt']}" + (f"；光：{r['light']}" if r.get("light") else "")
                     + " |" for r in todo)
    return CX.prompt_file("imagegen_task", TITLE=topic.get("title_cn", "?"), SHOT=sid, N=str(len(todo)), DIR=gdir,
                          EXAMPLE=todo[0]["id"], LOOK=look, ROWS=rows)


def generate(c, sid, work, todo):
    """生成 todo 里的图（一次 codex exec），更新 manifest、全片登记、引擎素材目录。失败只报告。返回 {ok: [...], failed: [...]}。"""
    g = gcfg(c)
    gdir = os.path.join(work, GEN)
    os.makedirs(gdir, exist_ok=True)
    runs = os.path.join(os.path.dirname(work.rstrip("/")), "runs")
    n = len(os.listdir(runs)) + 1 if os.path.isdir(runs) else 1
    rd = os.path.join(runs, f"{n:02d}-gen")
    os.makedirs(rd, exist_ok=True)
    task = task_text(c, sid, gdir, todo)
    util.write(os.path.join(rd, "TASK.md"), task)
    usage, err_all, notes, wall = {"in_miss": 0, "in_hit": 0, "out": 0, "reasoning": 0}, "", {}, 0.0
    if c.mock:
        for i, r in enumerate(todo):
            a, b = RATIOS[r["ratio"]]
            s = 640 / max(a, b)
            placeholder_png(os.path.join(gdir, r["id"] + ".png"), int(a * s) // 2 * 2, int(b * s) // 2 * 2, seed=i + len(sid))
        usage = {"in_miss": 3000, "in_hit": 9000, "out": 400 * len(todo), "reasoning": 0}
    else:
        try:
            cc = c.cc
            CX.ensure_login(cc)
            last = os.path.join(rd, "last.md")
            cmd = CX.build_cmd(cc, "workspace-write", gdir, last, effort=g.get("effort"), imagegen=True, tmpdir=os.path.join(rd, "tmp"),
                               reads=[CX.GEN_IMAGES])                 # 收紧读时只放行 image_gen 存图的目录（拷图用）
            util.write_json(os.path.join(rd, "cmd.json"), {"backend": "codex", "cmd": CX.shown(cmd), "cwd": gdir})
            r = CX.run_exec(cmd, task, rd, gdir, CX.clean_env(os.path.join(rd, "tmp")),
                            g["timeout_s_base"] + g["timeout_s_per_image"] * len(todo), max_turns=40 + 15 * len(todo))
            ev = r["ev"]
            usage, wall = ev.usage(), r["wall_s"]
            notes = _reply_notes(util.read(last) if os.path.exists(last) else ev.final)
            err_all = "；".join(ev.errors[-2:]) or ("" if r["rc"] == 0 else util.tail(os.path.join(rd, "stderr.log"), 5)[-300:])
            got = rescue(gdir, todo, ev.thread)                       # Codex 生了图但没拷过来：从这一次的 generated_images 补拷
            if got:
                c.log(f"  生图 {sid}：{got} 张是编排器从 ~/.codex/generated_images/{ev.thread}/ 按生成顺序补拷的", echo=False)
            if r["killed"]:
                err_all = f"codex 生图 {r['killed']}" + (f"：{err_all}" if err_all else "")
        except util.Stop as e:                                         # 登录不对、找不到 codex：只报告，不拦
            err_all = e.msg
        except Exception as e:  # noqa: BLE001
            err_all = f"{type(e).__name__}: {str(e)[:200]}"
        finally:
            shutil.rmtree(os.path.join(rd, "tmp"), ignore_errors=True)
    man = manifest(work)
    by = {x["id"]: x for x in man["images"]}
    reg = film(c)
    ok, failed = [], []
    for r in todo:
        f = _find(gdir, r["id"])
        wh = img_size(f) if f else None
        e = dict(r, name=clip_name(sid, r["id"]), sha=_sha(r), at=util.iso(), run=os.path.relpath(rd, c.ep))
        if f and wh:
            e.update(status="ok", file=os.path.abspath(f), w=wh[0], h=wh[1])
            ok.append(e)
            reg["clips"] = [x for x in reg["clips"] if x["name"] != e["name"]] + [{
                "name": e["name"], "file": e["file"], "content": r["prompt"][:200], "use": f"生图 · {r['use']}", "dur": 0,
                "w": wh[0], "h": wh[1], "fps": 0, "license": LICENSE, "gen": True, "shot": sid}]
        else:
            e.update(status="failed", err=(notes.get(r["id"]) or err_all or "没有生成出文件")[:300])
            failed.append(e)
            reg["failed"].append({"name": e["name"], "shot": sid, "err": e["err"], "at": e["at"]})
        by[r["id"]] = e
    man["images"] = list(by.values())
    man["note"] = "编排器写的生图清单（status ok 的在镜头里 Take(name, film=(起帧, 止帧), src=0)）"
    util.write_json(os.path.join(gdir, "manifest.json"), man)
    reg["calls"].append({"shot": sid, "run": os.path.relpath(rd, c.ep), "n": len(todo), "ok": len(ok), "wall_s": wall,
                         "tokens": usage, "rule_ok": "麻麻赖赖" in task, "mock": bool(c.mock), "at": util.iso()})
    util.write_json(c.p("inputs", "gen_images.json"), reg)
    if isinstance(c.ledger, CX.Ledger):
        c.ledger.add("imagegen", f"{sid}_gen", "imagegen", dict(usage), mock=bool(c.mock), extra={"images": len(ok), "wall_s": wall})
    try:
        c.renderer.setup()                                            # 引擎素材目录并进新图（Take 能找到）
    except Exception as e:  # noqa: BLE001
        c.log(f"  生图 {sid}：素材目录没刷新（{type(e).__name__}: {str(e)[:100]}）", echo=False)
    c.log(f"  生图 {sid}：{len(ok)}/{len(todo)} 张" + (f"，失败 {[x['id'] for x in failed]}（{failed[0]['err'][:80]}，只报告不拦）" if failed else "")
          + f"（{wall:.0f}s，token 入 {usage['in_miss'] + usage['in_hit']} / 出 {usage['out']}）")
    return {"ok": ok, "failed": failed}


# ----------------------------------------------------------------------------- 接进代理
def listing(c, sid, work):
    man = manifest(work)
    ok = [x for x in man["images"] if x.get("status") == "ok"]
    bad = [x for x in man["images"] if x.get("status") != "ok"]
    rows = [f"- `Take('{x['name']}', film=(起帧, 止帧), src=0)`：assets/gen/{os.path.basename(x['file'])}，{x['w']}×{x['h']}，"
            f"{x['use']}：{x['prompt'][:90]}" for x in ok]
    rows += [f"- {x['id']}：没生成出来（{str(x.get('err'))[:80]}）" for x in bad]
    return "\n".join(rows) if rows else "（这一镜还没有生图）"


def task_block(c, sid, work):
    """给代理任务书加的「生图」一节（通道开着时）：规则 + 这一镜现有的图。"""
    g = gcfg(c)
    col = (c.j("topic.json") or {}).get("signature_color") or {}
    return "\n\n" + CX.prompt_file("imagegen_rules", SHOT=sid, PER_SHOT=str(g["per_shot"]), PER_FILM=str(g["per_film"]),
                                   USED=str(used(c)), COLOR=f"{col.get('name', '')} {col.get('hex', '')}".strip() or "（开题定的签名色）",
                                   MANIFEST=listing(c, sid, work))


def followup_text(g, expect):
    rows = "\n".join(f"- `Take('{x['name']}', film=(起帧, 止帧), src=0)`：assets/gen/{os.path.basename(x['file'])}（{x['w']}×{x['h']}，"
                     f"{x['use']}）" for x in g["ok"])
    bad = "".join(f"\n- {x['id']} 没生成出来（{x['err'][:60]}）：继续用替身" for x in g["failed"])
    return (f"\n\n## 生图到了（编排器刚在沙箱外生成；这一次只做一件事：把它们接进 {expect}）\n{rows}{bad}\n"
            "按上面「生图」一节的规矩用（远景 / 背景 / 纹理 / 主体参考；静图只当底：配 fx.cam / SR.auto_cam 做单向推进或横移、一镜里不回头，这一镜的进展靠主体动作或切点换新东西，不靠摆和漂移；"
            "调色照样走 MO.world）。用不上就不用，在 out/NOTES.md 写一句为什么。其余保持不变，改完跑 ctx/check.sh。\n")


def after_agent(c, job, res, rerun):
    """代理跑完一次后：有新的生图请求就生成；生成出来了就再叫一次代理把图接进去。都不拦：失败只报告，返回原来的结果。"""
    if not enabled(c) or job.get("gen_followup"):
        return res
    work, sid = job["work"], job["name"]
    if not os.path.exists(os.path.join(work, REQ)):
        return res
    reqs, notes = parse_requests(work)
    with GEN_LOCK:
        todo, n2 = pending(c, work, reqs)
        if notes + n2:
            c.log(f"  生图 {sid}：" + "；".join(notes + n2)[:400], echo=False)
        if not todo:
            return res
        try:
            g = generate(c, sid, work, todo)
        except Exception as e:  # noqa: BLE001  生图出错只报告，不拦制作
            c.log(f"  生图 {sid}：出错，跳过（{type(e).__name__}: {str(e)[:160]}）")
            return res
    have = all(os.path.exists(os.path.join(work, e)) for e in job["expect"])
    if not (g["ok"] and g_follow(c) and have and res.get("status") in ("ok", "max_turns", "timeout")):
        return res
    job2 = dict(job, task=job["task"] + followup_text(g, job["expect"][0]), run_dir=job["run_dir"] + "-followup", gen_followup=True)
    res2 = rerun(job2)
    if res2.get("status") == "ok":
        return dict(res2, gen={"ok": len(g["ok"]), "failed": len(g["failed"])})
    c.log(f"  生图 {sid}：接图那次代理 {res2.get('status')}，用上一版代码", echo=False)
    return res


def g_follow(c):
    return bool(gcfg(c).get("followup", True))
