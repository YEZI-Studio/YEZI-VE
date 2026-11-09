#!/usr/bin/env python3
# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""不联网、不读 key 的快速自检：python3 -I selftest.py。端到端流程用 run_episode.py --mock 测。"""
import copy
import datetime as dt
import json
import math
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gates as G  # noqa: E402
from lib import agent, cost, inputs, llm, media, mockdata, mockshot, render, schema, stages, util  # noqa: E402
from lib import tree as TR  # noqa: E402
from lib import codex as CX, imagegen as IG  # noqa: E402

OK = []

_P2 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _have_file(*parts):
    return os.path.exists(os.path.join(_P2, *parts))


# 要你先准备的东西：没有时这几项记「跳过」，准备好了照常查
_NEEDS = [
    (("体检：开题读 doctor.json",), lambda: _have_file("orchestrator", "doctor.json"), "先运行一次 p2/tools/doctor.py"),
]


def _needs_first(name):
    for pre, have, why in _NEEDS:
        if name.startswith(pre) and not have():
            return why
    return None


def check(name, cond):
    why = None if cond else _needs_first(name)
    if why:
        print("跳过 " + name + "（" + why + "）")
        return
    OK.append((name, bool(cond)))
    print(("通过 " if cond else "失败 ") + name)


def main():
    tmp = os.path.join(util.P2, "episodes", "_selftest-" + util.now().strftime("%Y%m%d-%H%M%S"))   # 只写 p2 下
    os.makedirs(tmp)
    cfg = util.read_json(os.path.join(util.HERE, "config.json"))
    series = util.read_json(os.path.join(util.HERE, "series", "chroma.json"))
    log = util.Log(tmp, "selftest")
    _log_call = util.Log.__call__                     # 自检时编排器日志只写文件，终端只打通过 / 失败
    util.Log.__call__ = lambda self, msg, echo=True: _log_call(self, msg, echo=False)
    os.environ["STOCK_OFF"] = "1"         # 素材请求通道（默认开）在下面的旧用例里关掉，免得 mock 代理的请求改了旧用例的轮次；stock_ok 单独测

    # 1 schema
    sc = util.read_json(os.path.join(util.HERE, "schemas", "review.json"))
    good = {"shots": [{"id": "s01", "pass": True, "score": 4, "issues": []}]}
    bad = {"shots": [{"id": "x1", "pass": "yes", "score": 9}]}
    check("schema：合格的通过", schema.validate(good, sc) == [])
    check("schema：不合格的报 4 处（id、pass、score、缺 issues）", len(schema.validate(bad, sc)) == 4)

    # 2 模型输出不合格 → 重试；一直不合格 → 停
    led = cost.Ledger(os.path.join(tmp, "cost.json"), cfg["pricing"], None)
    seq = iter(["不是 JSON", "```json\n" + json.dumps(good) + "\n```"])
    m = llm.LLM(cfg, led, tmp, log, mock_fn=lambda *a: next(seq))
    o, info = m.ask_json("review", "t1", "sys", "prompt", schema=sc)
    check("重试：第 1 次不合格、第 2 次合格", info["attempts"] == 2 and o == good)
    m2 = llm.LLM(cfg, led, tmp, log, mock_fn=lambda *a: json.dumps(bad))
    try:
        m2.ask_json("review", "t2", "sys", "prompt", schema=sc)
        check("重试上限：3 次都不合格就停", False)
    except util.Stop as e:
        check("重试上限：3 次都不合格就停（退出码 5）", e.code == 5 and len(os.listdir(os.path.join(tmp, "calls"))) == 5)

    # 2a 规则只提醒：有建议就发回改一次，之后照收；后面反而不合格就收前面那版，不停
    g1 = {"shots": [{"id": "s01", "pass": True, "score": 2, "issues": []}]}
    g2 = {"shots": [{"id": "s01", "pass": True, "score": 4, "issues": []}]}
    adv = lambda o: ["分太低"] if o["shots"][0]["score"] < 3 else []  # noqa: E731
    seq = iter([json.dumps(g1), json.dumps(g2)])
    o, info = llm.LLM(cfg, led, tmp, log, mock_fn=lambda *a: next(seq)).ask_json("review", "t3", "sys", "p", schema=sc, soft=adv)
    seq = iter([json.dumps(g1), json.dumps(g1)])
    o2, info2 = llm.LLM(cfg, led, tmp, log, mock_fn=lambda *a: next(seq)).ask_json("review", "t4", "sys", "p", schema=sc, soft=adv)
    seq = iter([json.dumps(g1), "坏的", "坏的"])
    o3, info3 = llm.LLM(cfg, led, tmp, log, mock_fn=lambda *a: next(seq)).ask_json("review", "t5", "sys", "p", schema=sc, soft=adv)
    check("规则只提醒：发回改一次就收（改好了 / 没改都收）；改了反而不合格就收前面那版，不停",
          o == g2 and info["attempts"] == 2 and info["advice"] == [] and o2 == g1 and info2["attempts"] == 2
          and info2["advice"] == ["分太低"] and o3 == g1 and info3["advice"] == ["分太低"])
    # 2b T2-7：同一条建议这一集发回提醒过的不再为它多问；「只报警」的不触发发回；新的建议照旧发回一次
    adv2 = lambda o: {2: ["分太低", "第 1 秒构图数 7（只报警）"], 3: ["分太低", "换个词"]}.get(o["shots"][0]["score"], [])  # noqa: E731
    g3 = {"shots": [{"id": "s01", "pass": True, "score": 3, "issues": []}]}
    seq = iter([json.dumps(g1), json.dumps(g1), json.dumps(g1), json.dumps(g3), json.dumps(g3)])
    m3 = llm.LLM(cfg, led, tmp, log, mock_fn=lambda *a: next(seq))
    _, i_a = m3.ask_json("review", "t6", "sys", "p", schema=sc, soft=adv2)
    _, i_b = m3.ask_json("review", "t7", "sys", "p", schema=sc, soft=adv2)
    _, i_c = m3.ask_json("review", "t8", "sys", "p", schema=sc, soft=adv2)
    m4 = llm.LLM(cfg, led, tmp, log, mock_fn=lambda *a: json.dumps(g1))
    _, i_d = m4.ask_json("review", "t9", "sys", "p", schema=sc, soft=lambda o: ["x（只报警）"])
    check("T2-7 建议轮：第一次有新建议发回一次；同一条这一集已经提醒过的、标「只报警」的不再多问一轮；又来新的照旧发回一次",
          i_a["attempts"] == 2 and i_b["attempts"] == 1 and i_b["advice"] == ["分太低", "第 1 秒构图数 7（只报警）"]
          and i_c["attempts"] == 2 and i_d["attempts"] == 1 and m3.advised == {"分太低", "换个词"})
    # 2c T2-5：开题 number_ladder.value 写数字也收，编排器规整成字符串
    tsc = util.read_json(os.path.join(util.HERE, "schemas", "topic.json"))
    tnum = json.loads(util.read(os.path.join(util.HERE, "mock", "topic.json")).strip())
    lad0 = tnum["candidates"][tnum["chosen"]]["number_ladder"]
    lad0[0]["value"], lad0[1]["value"], lad0[2]["value"] = 321.5, 3.0e8, 7
    e_num = schema.validate(tnum, tsc)
    stages.norm_topic(tnum)
    check("T2-5 开题 number_ladder.value 收数字（schema 不发回），规整成字符串：321.5 → \"321.5\"、3.0e8 → \"300000000\"、7 → \"7\"",
          e_num == [] and [x["value"] for x in lad0[:3]] == ["321.5", "300000000", "7"])

    # 3 没有 key → 阻塞（不读真实 key 文件：指向临时路径）
    saved_env, saved_file = os.environ.pop("DEEPSEEK_API_KEY", None), util.KEY_FILE
    util.KEY_FILE = os.path.join(tmp, "no-such.key")
    try:
        check("没有 key：get_key 返回空", llm.get_key() == (None, None))
        try:
            llm.LLM(cfg, led, tmp, log)._send({})
            check("没有 key：停在阻塞（退出码 4）", False)
        except util.Stop as e:
            check("没有 key：停在阻塞（退出码 4），没有发请求", e.code == 4)
    finally:
        util.KEY_FILE = saved_file
        if saved_env is not None:
            os.environ["DEEPSEEK_API_KEY"] = saved_env

    # 4 记账、错峰、预算
    bj = cost.BJ
    pw = cfg["pricing"]["peak_beijing"]
    check("错峰：北京周一 10:00 是高峰", not cost.is_offpeak(dt.datetime(2026, 11, 2, 10, 0, tzinfo=bj), pw))
    check("错峰：北京周一 13:00 是错峰", cost.is_offpeak(dt.datetime(2026, 11, 2, 13, 0, tzinfo=bj), pw))
    check("错峰：北京周六 10:00 是错峰", cost.is_offpeak(dt.datetime(2026, 11, 7, 10, 0, tzinfo=bj), pw))
    nu = cost.norm_usage({"prompt_cache_hit_tokens": 1_000_000, "prompt_cache_miss_tokens": 1_000_000, "completion_tokens": 1_000_000})
    check("价格：命中 1M + 未命中 1M + 输出 1M = $1.506（高峰）", abs(cost.usd(nu, cfg["pricing"], False) - 1.506) < 1e-9)
    nu2 = cost.norm_usage({"input_tokens": 100, "cache_read_input_tokens": 900, "output_tokens": 10})
    check("usage：Anthropic 兼容字段也能读", nu2 == {"in_miss": 100, "in_hit": 900, "out": 10})
    led2 = cost.Ledger(os.path.join(tmp, "cost2.json"), cfg["pricing"], 0.001)
    led2.add("x", "y", "api", {"in_miss": 10000, "in_hit": 0, "out": 0})
    try:
        led2.guard(0.0001, "下一步")
        check("预算：超了就停", False)
    except util.Stop as e:
        check("预算：超了就停（退出码 3）", e.code == 3)

    # 5 代理外壳：沙箱设置、命令行里没有 key
    st = agent.make_settings(["/a"], [], ["/a"])
    cmd = agent.build_cmd(cfg["agent"], "/s.json", "/m.json", "TASK")
    check("代理：沙箱开、不许出沙箱、网络空白名单、key 环境变量对沙箱不可见",
          st["sandbox"]["enabled"] and not st["sandbox"]["allowUnsandboxedCommands"]
          and st["sandbox"]["network"]["allowedDomains"] == []
          and {"name": "ANTHROPIC_AUTH_TOKEN", "mode": "deny"} in st["sandbox"]["credentials"]["envVars"])
    check("代理：固定工具清单 Bash,Read,Edit,Write、关技能、空 MCP、不存会话",
          cmd[cmd.index("--tools") + 1] == "Bash,Read,Edit,Write" and "--disable-slash-commands" in cmd
          and "--strict-mcp-config" in cmd and "--no-session-persistence" in cmd and "sk-" not in " ".join(cmd))

    # 6 节拍换算、镜头展开、计划门禁（mock 节拍表 / 镜头表按配乐约束现编）
    raw = mockdata.beatmap(150, 16, 9)
    check("节拍表校验：mock 节拍表合格", stages.check_beatmap(raw, series["gates"]) == [])
    bm = stages.expand_beatmap(raw)
    sl = stages.expand_shotlist(mockdata.shotlist({"beatmap": bm}), bm, bm["sections"])
    check("换算：150 BPM × 16 小节 = 1536 帧", bm["total_f"] == 1536 and sl["shots"][-1]["end_f"] == 1536)
    allc = [x for s_ in sl["shots"] for x in s_["cuts_f"]]
    check("屏息那一拍（第 8 小节第 4 拍 = 744–768 帧）里不切", not [x for x in allc if 744 < x < 768])
    check("DROP 后一拍（768–792 帧）里不切", not [x for x in allc if 768 < x < 792])
    chk = G.plan_checks(bm, sl, series)
    check("计划门禁：mock 镜头表全部通过（拍上硬切占比只报告）", all(x["status"] in ("ok", "info") for x in chk))
    bad_sl = json.loads(json.dumps(sl))
    bad_sl["shots"][0]["hero_word"] = "零点"
    for s in bad_sl["shots"][:3]:
        s["image"] = "同一个"
    warn_ids = {x["id"] for x in G.plan_checks(bm, bad_sl, series) if x["status"] == "warn"}
    _, hard_e, soft_e = stages.plan_errors(bm, bad_sl, series)
    check("计划门禁：中文大字、同一意象 3 次抓到了，但只报警（规则只提醒，）",
          {"p_english", "p_image"} <= warn_ids and hard_e == [] and len(soft_e) >= 2)
    thin = json.loads(json.dumps(sl))
    for s in thin["shots"]:
        s["cut_every"] = "bar"
    _, hard_t, soft_t = stages.plan_errors(bm, stages.expand_shotlist(thin, bm, bm["sections"]), series)
    check("计划门禁：切点密度、第 1 秒不够只提醒；时长、铺满全片仍是硬的",
          hard_t == [] and any("切点密度" in e for e in soft_t) and any("第 1 秒" in e for e in soft_t)
          and any(x["id"] == "p_cover" and x["status"] == "fail"
                  for x in G.plan_checks(bm, dict(sl, shots=sl["shots"][:-1]), series)))
    raw2 = mockdata.beatmap(100, 10, 6)                              # 24 s：只有 BPM 不对
    check("节拍表校验：100 BPM 只提醒，不当不合格",
          stages.check_beatmap(raw2, series["gates"]) == [] and any("bpm" in e for e in stages.beatmap_rules(raw2, series["gates"])))
    mu = {"bpm": 144.993, "bars": 16, "drop_bar": 5}
    raw3 = mockdata.beatmap(150, 16, 9)
    e3 = stages.check_beatmap(raw3, series["gates"], mu)
    check("配乐约束：BPM 由代码写死、DROP 不在配乐的小节被拒", raw3["bpm"] == 144.993 and any("drop" in e for e in e3))
    check("配乐约束：照配乐现编的节拍表合格", stages.check_beatmap(mockdata.beatmap(144.993, 16, 5), series["gates"], mu) == [])

    # 6a 转场声明（只报警，不拦）
    sh = sl["shots"]
    check("转场：每镜带出点 = 下一镜的入点，最后一镜接回 s01",
          all(a_["transition_out"]["kind"] == b_["transition_in"]["kind"] for a_, b_ in zip(sh, sh[1:]))
          and sh[-1]["transition_out"]["to"] == "s01")
    ser2 = copy.deepcopy(series)
    ser2["transitions"]["hard_cut_share_max"] = 0.0
    hc = [x for x in G.plan_checks(bm, sl, ser2) if x["id"] == "p_hard_cut"][0]
    check("转场：拍上硬切占比 CHROMA 不设上限只报告；系列设了上限、超了报警",
          [x for x in chk if x["id"] == "p_hard_cut"][0]["status"] == "info" and hc["status"] == "warn")
    blk = stages.transitions_block(series["transitions"]).splitlines()
    check("提示词：镜头表的转场说明 ≤15 行、8 种 kind 都在", len(blk) <= 15 and all(any(k in x for x in blk) for k in series["transitions"]["kinds"]))
    check("切点像素检查：闪黑、跨切点混帧、对位接不上都抓到，好的切点不报，全部只报警", cut_pixels_ok(tmp, series))

    # 6c 6 分锚定评分：到 6 分就停返修；没到 6 分但返修满 3 轮也交付
    ls = stages.loop_stop
    shots_ok = {k: {"id": k, "pass": True, "score": sc_, "issues": []} for k, sc_ in (("s02", 4), ("s05", 3), ("s07", 3), ("s09", 5))}
    picked = stages.below_line(shots_ok, {"anchor_score": 4, "anchor_why": "主体不清楚"}, 6)
    check("返修循环：没到 6 分、审片却全过：挑分最低的两镜接着修；到 6 分不挑",
          picked == ["s05", "s07"] and not shots_ok["s05"]["pass"] and shots_ok["s02"]["pass"]
          and stages.below_line({"s01": {"id": "s01", "pass": True, "score": 2, "issues": []}}, {"anchor_score": 6.5}, 6) == [])
    check("返修循环：内部评分 6.5 ≥ 6 就停（哪怕还有镜头没过）；5 分第 2 轮接着修；5 分返修满 3 轮也交付",
          "≥ 6" in (ls({"failed": ["s06"], "global": {"anchor_score": 6.5}}, 2, 3, 6) or "")
          and ls({"failed": ["s06"], "global": {"anchor_score": 5}}, 2, 3, 6) is None
          and "返修已满 3 轮" in (ls({"failed": ["s06"], "global": {"anchor_score": 5}}, 4, 3, 6) or ""))
    check("返修循环：≥6 分但没过的镜头 4 个 > 3 接着修；还有镜内运动量不够的镜头（交付硬门禁）也接着修",
          ls({"failed": ["a", "b", "c", "d"], "global": {"anchor_score": 6.5}}, 2, 3, 6) is None
          and "≥ 6" in (ls({"failed": ["a", "b", "c"], "global": {"anchor_score": 6.5}}, 2, 3, 6) or "")
          and ls({"failed": ["a"], "motion_failed": ["a"], "global": {"anchor_score": 6.5}}, 2, 3, 6) is None
          and "返修已满" in (ls({"failed": ["a"], "motion_failed": ["a"], "global": {"anchor_score": 6.5}}, 4, 3, 6) or ""))
    mc_ = dict(stages.MOTION, need=5.22)
    rs = {k: {"id": k, "pass": True, "score": 4, "issues": []} for k in ("s01", "s02", "s03")}
    st_ = lambda mean, still, ex, ok: {"mean": mean, "median": mean, "still": still, "still_ex": ex, "n": 80, "ok": ok}  # noqa: E731
    mo_ = {"all": {"ok": True}, "s01": st_(9.0, 0, 0, True), "s02": st_(3.1, 0.3, 0.3, False),
           "s03": st_(8.0, 0.5, 0.05, True), "s04": st_(2.0, 0.6, 0.6, False)}
    mf, fz = stages.motion_force(rs, mo_, mc_, {"s04": {}})
    rs2 = {k: {"id": k, "pass": True, "score": 4, "issues": []} for k in ("s01", "s03")}
    mf2, _ = stages.motion_force(rs2, dict(mo_, all={"ok": False, "mean": 7, "median": 6, "still": 0.2, "n": 300}), mc_)
    check("审片：机器量镜内运动量不够的镜头判不过（不会被冻结）；已冻结的只报告；整片静止超线时点名静止最多的一镜（片尾海报不算）",
          mf == ["s02"] and not rs["s02"]["pass"] and rs["s01"]["pass"] and fz == ["s04"] and mf2 == ["s03"])
    tp_ = util.read_json(os.path.join(util.HERE, "mock", "topic.json"))
    tp_ = tp_["candidates"][tp_["chosen"]] if "candidates" in tp_ else tp_
    names_ = [f"F{i:02d}" for i in range(1, 40)]
    rg = util.read_json(os.path.join(util.HERE, "schemas", "review_global.json"))
    check("schema：全片审片要给 anchor_score（0–10），审片可带切点核对 cuts",
          schema.validate({"issues": [], "verdict": "x", "anchor_score": 6.5, "anchor_why": "y"}, rg) == []
          and schema.validate({"issues": [], "verdict": "x"}, rg) != []
          and schema.validate(dict(good, cuts=[{"cut": "s02", "declared": "甩", "seen": "硬切", "ok": False, "fix": "z"}]), sc) == [])

    pack = inputs.pack_dir(series, "霓虹")
    m = inputs.pick_music(pack, series["gates"]) if pack else None
    ns = {}
    exec(compile(render.timeline_py(bm, sl), "timeline.py", "exec"), ns)
    check("时间线代码：能执行、首尾相接、F(9) = DROP", ns["check"]() and ns["END"] == 1536 and ns["F"](9) == ns["DROP"] == 768)

    # 7 提示词模板
    c = stages.Ctx(tmp, series, cfg, None, log, led, None, None, True)
    p = c.prompt("00_topic", SERIES="CHROMA", N="4", EPISODES="- x", ENV="- 主角物：实拍或生图", CATEGORY="CHROMA = 卡点炫技")
    check("提示词：卡片段落按小节引进来、没有残留占位符", "## 开新一集" in p and "{{" not in p and "Archivo" in p)
    tree_ok(tmp, series, cfg, c, bm, tp_, names_)
    sync_motion_ok(tmp, series, cfg, log, tp_)
    final2_ok(tmp, series, cfg, log, tp_)
    cheapfx_ok(tmp, series, cfg, log, tp_)
    notes_ok(tmp, series, cfg, log, tp_)
    codex_ok(tmp, series, cfg)
    stock_ok(tmp, series, cfg, log)
    stage7_ok(tmp, series, cfg, log, led)
    json_close_ok(tmp, cfg, led)
    try:
        util.render_prompt("{{X}}", {}, {})
        check("提示词：漏填占位符会报错", False)
    except KeyError:
        check("提示词：漏填占位符会报错", True)

    n_bad = sum(1 for _, ok in OK if not ok)
    print(f"\n{len(OK) - n_bad}/{len(OK)} 通过（临时目录 {tmp}）")
    return 1 if n_bad else 0


def tree_ok(tmp, series, cfg, c, bm, tp_, names_):
    """规则树接进编排器（v2.1）：开题判大类、提示词按维度引用、可检查项按档位接门禁。"""
    t = c.tree
    rid = {r["id"] for r in TR.ledger(t, "beat", [])["rows"]}
    want = {k for k, v in t["_idx"].items() if v["layer"] in ("root", "beat")}
    check("规则树：tree.json 读进来；总账列满根 + 卡点炫技的每一条规则（%d 条）" % len(want), rid == want and len(want) > 80)
    blk = TR.rules_block(t, "beat", [(["镜头"], ["floor", "param"])])
    check("规则树：{{rules:镜头|floor,param}} = 三档说明 + 根的镜头行 + 卡点炫技的镜头行，不带必做、不带别的维度",
          blk.count("\n") == 2 and "根·镜头｜底线：①整帧静止" in blk and "卡点炫技·镜头｜底线：①每镜镜内都有进展" in blk
          and "必做" not in blk.split("\n", 1)[1] and "转场" not in blk.split("\n", 1)[1])
    # 阈值、密度数不进给代理的文字；final = 最后一个重音（不是「原曲自己的拍」）
    big = TR.rules_block(t, "beat", [(["镜头", "节奏", "开头结尾"], ["do", "floor", "param"])])
    check("规则树：给代理的规则里没有 5.22、3.8 个/秒、第 1 秒 10 个构图、「原曲自己的拍」，换成有效运动量 / 最后一个重音的说法",
          not any(k in big for k in ("5.22", "3.8 个", "4.6–5.3", "10 个构图", "原曲自己的拍", "帧差均值"))
          and "最后一个重音" in big and "原地来回" in big and "缩放泵" in big
          and "5.22" in t["_idx"]["beat.floor1"]["text"])
    # 每个提示词模板都渲得出来（占位符填假值），没有 global 引用、没有残留
    lens, bad = {}, []
    for f in sorted(os.listdir(os.path.join(util.HERE, "prompts"))):
        if not f.endswith(".md") or f == "system.md":
            continue
        raw = util.read(os.path.join(util.HERE, "prompts", f))
        names = set(__import__("re").findall(r"\{\{([A-Z_0-9]+)\}\}", raw))
        try:
            out = c.prompt(f[:-3], **{k: "x" for k in names})
            lens[f[:-3]] = len(out)
            if "{{" in out or "include:global" in raw:
                bad.append(f)
        except KeyError as e:
            bad.append(f"{f}：{e}")
    nt = sum(1 for f in os.listdir(os.path.join(util.HERE, "prompts")) if f.endswith(".md") and f != "system.md")
    check(f"提示词：{nt} 个模板都按规则树渲得出来，不再引用全局.md（最长 {max(lens.values()) if lens else 0} 字：{max(lens, key=lens.get) if lens else ''}）",
          not bad and len(lens) == nt and "根·主线" in c.prompt("00_topic", SERIES="C", N="4", EPISODES="-", ENV="-", CATEGORY="-")
          and "卡点炫技·镜头" in c.prompt("03_shot_agent", **{k: "x" for k in __import__("re").findall(r"\{\{([A-Z_0-9]+)\}\}", util.read(os.path.join(util.HERE, "prompts", "03_shot_agent.md")))}))
    # 开题：大类、出处
    # 导演：节拍表
    mb = mockdata.beatmap(150, 16, 9)
    nb = json.loads(json.dumps(mb))
    for s_ in nb["sections"]:
        s_["energy"] = "high"
        s_.pop("color_from")
    nb.pop("wow_7s")
    check("导演：mock 节拍表的低谷、第 1 秒 / 7 秒王炸、色表都过；能量一样平、没写 7 秒王炸、没写 color_from → 发回",
          not TR.hard_msgs(TR.beat_eval(t, "beat", mb)) and len(TR.hard_msgs(TR.beat_eval(t, "beat", nb))) == 3)
    # 镜头表
    sl0 = stages.expand_shotlist(mockdata.shotlist({"beatmap": bm, "topic": tp_, "footage": list(names_)}), bm, bm["sections"])
    rows0 = TR.shot_eval(t, "beat", tp_, bm, sl0, series)
    sb = json.loads(json.dumps(sl0))
    sb.pop("route")
    sb["shots"][2]["sustain"] = "随便"
    for s_ in sb["shots"][3:6]:
        s_["type_fx"] = {"font": "hero", "entry": "SLAM", "layout": "居中"}
    for s_ in sb["shots"][6:9]:
        s_["transition_in"]["kind"] = "甩"
    sb["shots"][4]["transition_in"]["why"] = "闪白过渡"
    sb["shots"][5]["event"] = "加一圈声波环"
    sb["shots"][7]["action"] = "镜头推近"
    hb_ = " ".join(TR.hard_msgs(TR.shot_eval(t, "beat", tp_, bm, sb, series)))
    check("镜头表：mock 表规则树必做 / 底线全过（转场 6 种只报警、写了理由记「偏离有理由」）；缺 route、sustain 乱写、字效连 3 次、"
          "同一转场连 3 次、写闪白、声波环、action 只写推近 → 都发回",
          not TR.hard_msgs(rows0) and next(r for r in rows0 if r["id"] == "root.transition.param1")["status"] == "info"
          and all(x in hb_ for x in ("route", "sustain", "字效连着", "连用 ≤2 次", "闪白", "声波环", "推近")))
    check("镜头表：「不用闪白」「没有叠化」这种否定说法不算", not TR._neg_hit("不用闪白，也没有叠化", TR.DIP_WORDS + TR.MIX_WORDS)
          and TR._neg_hit("闪白过渡", TR.DIP_WORDS) == ["闪白"])
    # 代码级必做：转场函数、运动模糊、纯函数
    ep = os.path.join(tmp, "tree_ep")
    os.makedirs(os.path.join(ep, "inputs"), exist_ok=True)
    sl1 = dict(sl0, alloc={"words": {}})
    util.write_json(os.path.join(ep, "shotlist.json"), sl1)
    util.write_json(os.path.join(ep, "inputs", "footage.json"), {"clips": []})
    c2 = stages.Ctx(ep, series, cfg, None, c.log, c.ledger, None, None, True)
    s_ = next(x for x in sl1["shots"][1:-1] if x["transition_in"]["kind"] not in ("拍上硬切",) and x["transition_out"]["kind"] != "拍上硬切")
    code = mockshot.shot_code(s_["id"], stages.spec_for_agent(s_, tp_, series["transitions"]), [], "#5FF2FF")
    p1 = os.path.join(ep, "a.py")
    util.write(p1, code)
    r1 = [r for r, _ in stages.must_do_tagged(c2, s_, ep, p1)]
    util.write(p1, code.replace("SR.tx(", "notx(").replace("fx.cam(", "camx(") + "\nimport time\nX = time.time()\n")
    r2 = [r for r, _ in stages.must_do_tagged(c2, s_, ep, p1)]
    check("制作：mock 镜头代码按声明调了 SR.tx、有运动模糊；去掉转场函数、运动模糊、用 time.time() → 都发回代理（规则树必做 / 底线）",
          not ({"root.transition.do2", "root.quality.do1", "root.quality.floor5"} & set(r1))
          and {"root.transition.do2", "root.quality.do1", "root.quality.floor5"} <= set(r2))
    # 门禁分档和规则树档位一致；成片检查 → 规则行
    check("门禁分档 = 规则树档位：必做 / 底线里机器量得稳的（定稿片都过）是 hard，量不稳的和默认只报警",
          TR.gate_level_errors(t, G.HARD) == [])
    vr = TR.video_rows(t, "beat", [G._chk("still", "s", "warn", 1, 0, ["3.0s 起（镜头 s04）"]), G._chk("cut_dip", "d", "warn", 1, 0, ["x"]),
                                   G._chk("tint", "t", "ok")])
    vv = {r["id"]: r for r in vr}
    check("成片门禁 → 规则行：still（稳的底线）制作中途记 warn、level hard；cut_dip（量不稳）level warn；tint（默认）过",
          vv["root.camera.floor1"]["status"] == "warn" and vv["root.camera.floor1"]["level"] == "hard"
          and vv["root.transition.floor1"]["level"] == "warn" and vv["root.color.param1"]["status"] == "ok")
    fs = {"s04": {"id": "s04", "pass": True, "issues": []}, "s05": {"id": "s05", "pass": True, "issues": []}}
    ff, _ = stages.floor_force(fs, [G._chk("still", "≥1 s 的静止段", "warn", 1, 0, ["3.0s 起（镜头 s04）"]),
                                    G._chk("cut_dip", "闪", "warn", 1, 0, ["s05 入点 300（5s，甩）：闪黑"])], sl0)
    check("审片：稳的底线没过的镜头判不过（s04）；量不稳的只进问题单当建议（s05 照样过）",
          ff == ["s04"] and not fs["s04"]["pass"] and fs["s05"]["pass"] and fs["s05"]["issues"])
    check("返修循环：底线没过、必做没做到的镜头还在时，到 6 分也不停",
          stages.loop_stop({"failed": ["a"], "floor_failed": ["a"], "global": {"anchor_score": 6.5}}, 2, 3, 6) is None
          and stages.loop_stop({"failed": ["a"], "mustdo_failed": ["a"], "global": {"anchor_score": 6.5}}, 2, 3, 6) is None)
    env = stages.load_env(c)
    check("体检：开题读 doctor.json 的 hints（mock 不重跑体检）", env["read"] and "footage_download" in env["hints"]
          and "主角物" in stages.env_text(env))


def synth_map(bpm=150.0, bars=8, drop_bar=5, stop_bar=7, start_s=10.0):
    """合成的音乐打击点表（接口 1 的格式，不靠音频）：8 分位置有起音（B 档），每拍第 3 拍的反拍是重音（A 档），
    drop（第 5 小节第 1 拍）、6.3.2、8.1.2 是 S 档；第 4 小节第 1 拍整拍没有起音（屏息）；第 7 小节第 1 拍急停。
    有起音的格 F = round(格点 − 0.7 帧)（真实起音比格点早）。"""
    six_f = 3600.0 / bpm / 4
    S = {(drop_bar, 1, 0): 3.8, (6, 3, 2): 3.2, (8, 1, 2): 3.3}
    slots = []
    for i in range(bars * 16):
        bar, beat, sub = i // 16 + 1, (i % 16) // 4 + 1, i % 4
        tot = {0: 0.9, 2: 0.7}.get(sub, 0.0)
        if sub == 2 and beat == 3 and bar >= 2:
            tot = 2.0
        if (bar, beat) == (4, 1) or (bar, beat, sub) == (stop_bar, 1, 0):
            tot = 0.0
        tot = S.get((bar, beat, sub), tot)
        g = i * six_f
        slots.append({"i": i, "bar": bar, "beat": beat, "sub": sub, "grid_f": round(g, 2), "attack_f": round(g - 0.7, 2) if tot else None,
                      "F": int(round(g - 0.7)) if tot >= 0.6 else int(round(g)), "total": tot, "low": tot / 2, "mid": 0.1, "high": 0.1,
                      "band": "L", "tier": "S" if (bar, beat, sub) in S else ("A" if tot >= 2.0 else ("B" if tot >= 0.6 else "C")),
                      "accent": (bar, beat, sub) in S or tot >= 2.0})
    on = sorted((e for e in slots if e["total"] >= 0.6), key=lambda e: -e["total"])
    for k, e in enumerate(on, 1):
        e["rank"] = k
    for e in slots:
        e.setdefault("rank", None)
    stop_F = int(round((stop_bar - 1) * 16 * six_f)) + 1
    la = max((e for e in slots if e["tier"] in ("S", "A")), key=lambda e: e["F"])
    dr = slots[(drop_bar - 1) * 16]
    return {"version": 1, "fps": 60, "bpm": bpm, "beat_f": 4 * six_f, "bar_f": 16 * six_f, "start_s": start_s, "downbeat_s": 0.0,
            "bars": bars, "total_f": int(math.floor(bars * 16 * six_f + 0.5)), "slots": slots,
            "drop": {"F": dr["F"], "bar": drop_bar, "beat": 1, "sub": 0, "attack_f": dr["attack_f"], "jump_db": 20.0},
            "stops": [{"F": stop_F, "bar": stop_bar, "beat": 1, "sub": 0, "drop_db": 18.0}],
            "last_accent": {k: la[k] for k in ("F", "bar", "beat", "sub")},
            "breath": {"F": int(round(48 * six_f)), "bar": 4, "beat": 1, "sub": 0},
            "bar_map": ["L.l. L.l. L.L. L.l."] * bars}


def sync_motion_ok(tmp, series, cfg, log, tp_):
    """11-04 两个问题的修复：卡点（music_map → 节拍表硬错误、起音帧、切点从音乐取、本镜卡点表、sync_force）、
    抽搐（有效运动量口径、没有进展 / 原地摆动分开判）、试跑短片（截断窗口、跳过样片）。不跑模型、不渲染。"""
    g = series["gates"]
    mm = synth_map()
    mu = {"bpm": 150.0, "bars": 8, "drop_bar": 5}
    g8 = dict(g, duration_s=[10, 30])
    # 节拍表：mock 照表排的过；旧写法（按拍子排）被新硬错误拦下
    raw = mockdata.beatmap_mm(mm, 150.0, 8, 5)
    e_ok = stages.check_beatmap(json.loads(json.dumps(raw)), g8, mu, mm)

    def bad(edit):
        r = json.loads(json.dumps(raw))
        edit(r["hits"])
        return " ".join(stages.check_beatmap(r, g8, mu, mm))
    e1 = bad(lambda hs: hs.append({"bar": 2, "beat": 2, "sub": 1, "kind": "big", "what": "x"}))           # 没有起音的格
    e2 = bad(lambda hs: hs.append({"bar": 6, "beat": 3, "sub": 0, "kind": "big", "what": "x"}))           # B 档（旁边 6.3.2 是 S 档）
    e7 = bad(lambda hs: hs.append({"bar": 2, "beat": 1, "sub": 0, "kind": "big", "what": "x"}))           # B 档小节线（v2 的 795 那种）
    e3 = bad(lambda hs: hs.__setitem__(slice(None), [h for h in hs if (h["bar"], h["beat"], h["sub"]) != (8, 1, 2)]))   # S 档没卡点
    e4 = bad(lambda hs: [h.update(kind="small") for h in hs if (h["bar"], h["beat"], h["sub"]) == (7, 1, 0)])   # 急停不是 big
    e5 = bad(lambda hs: [h.update(bar=8, beat=1, sub=0) for h in hs if h["kind"] == "final"])              # final 不是最后一个重音
    e6 = bad(lambda hs: hs.append({"bar": 3, "beat": 2, "sub": 3, "kind": "small", "what": "x"}))         # small 落在没有起音的格
    check("卡点：mock 照音乐打击点表排的节拍表合格；大卡点落在没有起音的格、旁边有更强的格、S 档没卡点、急停不是 big、"
          "final 不是最后一个重音、small 没有起音 → 都是硬错误（发回导演）",
          e_ok == [] and "没有起音" in e1 and "只是 B 档" in e2 and "挪到 6.3.2" in e2 and "S 档 8.1.2" in e3 and "急停 7.1.0" in e4
          and "final 必须" in e5 and "没有起音" in e6 and "只是 B 档" in e7 and "S / A 档" in e7)
    fin_off = json.loads(json.dumps(raw))
    check("卡点：beatmap_rules 不再要求 final 在拍上 / 在最后一小节第 1 拍（final = 窗口里最后一个重音，可以在反拍）",
          not any("final" in x for x in stages.beatmap_rules(fin_off, g8, mu, mm))
          and next(h for h in raw["hits"] if h["kind"] == "final")["sub"] == 2)
    # 起音帧（slot_F）：有起音用起音帧，没有用格点，急停用急停帧；片长不变
    bm = stages.expand_beatmap(raw, mm)
    bm0 = stages.expand_beatmap(raw)
    fF = {h["kind"] + f"{h['bar']}.{h['beat']}.{h['sub']}": h["F"] for h in bm["hits"]}
    check("卡点：slot_F——有起音的格用真实起音帧（比格点早 1 帧）、没有起音的格用格点、急停用急停帧；total_f 照旧按格点",
          stages.slot_F(bm, mm, 6, 3, 2) == mm["slots"][90]["F"] == stages.frame_at(bm, 6, 3, 2) - 1
          and stages.slot_F(bm, mm, 4, 1, 0) == stages.frame_at(bm, 4, 1, 0)
          and stages.slot_F(bm, mm, 7, 1, 0) == mm["stops"][0]["F"]
          and bm["total_f"] == bm0["total_f"] == stages.frame_at(bm, 9) and fF.get("big7.1.0") == mm["stops"][0]["F"]
          and fF.get("final8.3.2") == mm["last_accent"]["F"])
    # 切点从音乐起音里取（cut_every = 密度档）
    a_f, b_f = stages.slot_F(bm, mm, 5), stages.slot_F(bm, mm, 7)
    c16 = stages.music_cuts(a_f, b_f, "16th", mm)
    c32 = stages.music_cuts(a_f, b_f, "32nd", mm)
    cbeat = stages.music_cuts(a_f, b_f, "beat", mm)
    cbar = stages.music_cuts(a_f, b_f, "bar", mm)
    chold = stages.music_cuts(a_f, b_f, "16th", mm, holds={17})                # DROP 后第二拍（第 17 拍）不切
    ons = {e["F"] for e in mm["slots"] if e["total"] >= 0.6}
    check("切点从音乐起音里取：16th = 本镜所有有起音的格（32nd 按 16th）、beat = 只取重音、bar = 每小节最强一下、none = 不切；"
          "离镜头边界 ≥4 帧、相邻 ≥5 帧、hold 的拍里不切；镜头表展开用它",
          c16 and set(c16) <= ons and c16 == c32 and min(c16) >= a_f + 4 and max(c16) < b_f - 4
          and all(y - x >= 5 for x, y in zip(c16, c16[1:])) and len(cbeat) == 2 and set(cbeat) <= set(c16) and len(cbar) == 2
          and stages.music_cuts(a_f, b_f, "none", mm) == [] and not set(chold) & {mm["slots"][i]["F"] for i in (69, 70, 71)} and len(chold) < len(c16)
          and not set(c16) & {mm["slots"][i]["F"] for i in (64 + 2, 90 - 2, 90 + 2)})       # 重音前后半拍的非重音格不切（选择性）
    sl = stages.expand_shotlist(mockdata.shotlist({"beatmap": bm}), bm, bm["sections"], mm)
    allc = {x for s_ in sl["shots"] for x in s_["cuts_f"]}
    old = {x for s_ in stages.expand_shotlist(mockdata.shotlist({"beatmap": bm0}), bm0, bm0["sections"])["shots"] for x in s_["cuts_f"]}
    check("镜头表展开：有 music_map 时镜头内切点全落在起音上（150 BPM：起音帧 = 6k − 1）、镜头边界用起音帧；没有 music_map 照旧按格均分（6k）",
          allc and allc <= ons and all(x % 6 == 5 for x in allc) and old and all(x % 6 == 0 for x in old)
          and sl["shots"][0]["start_f"] == 0 and mm["slots"][0]["F"] == -1                  # 第 0 格起音帧是 −1：第一镜照样从 0 起
          and all(s_["start_f"] == stages.slot_F(bm, mm, s_["bars"][0]) for s_ in sl["shots"][1:]) and sl["shots"][-1]["end_f"] == bm["total_f"])
    # 切点密度由编排器提档（导演把前奏排成 beat / none，整片 1.66/s，两轮对话都不肯提）
    raw_d = mockdata.shotlist({"beatmap": bm})
    for s_ in raw_d["shots"]:
        s_["cut_every"] = "none"
    notes = stages.densify(raw_d, bm, mm)
    sl_d = stages.expand_shotlist(raw_d, bm, bm["sections"], mm)
    shots_d, _, have_d, _, need_d = stages.plan_density(sl_d, mm)
    check("切点密度：导演全排 none 时编排器提档——开头那一镜 16th（钩子）、整片到线、不留相邻两镜都不切（最后一镜除外）、记下改动；"
          "提档后 plan_density_errs 不再报",
          notes and shots_d[0]["cut_every"] == "16th" and have_d >= need_d and stages.plan_density_errs(sl_d, mm) == []
          and not any(not a["cuts_f"] and not b["cuts_f"] for a, b in zip(shots_d[:-2], shots_d[1:-1]))
          and stages.densify(raw_d, bm, mm) == [])
    long_ = {"shots": [{"id": "s01", "bars": [1, 4], "start_f": 0, "end_f": 300}, {"id": "s02", "bars": [4, 6], "start_f": 300, "end_f": 500},
                       {"id": "s03", "bars": [6, 9], "start_f": 500, "end_f": 800}]}
    check("镜头长度：开头那一镜 ≤1 小节、其余 ≤2 小节；mock 镜头表照样过",
          [x.split(" ")[0] for x in stages.plan_shape_errs(long_)] == ["s01", "s03"] and stages.plan_shape_errs(sl_d) == [])
    # 本镜卡点表：等级、表外不许强变化、不写门槛
    s4 = next(s_ for s_ in sl["shots"] if s_["start_f"] <= mm["drop"]["F"] < s_["end_f"])
    rows = stages.sync_rows(s4, mm)
    m16 = synth_map(bars=16, stop_bar=12)                                       # 16 小节：重音比 16 个多，才有「轻」
    lv = {r["F"]: r["lvl"] for r in rows}
    lv16 = [r["lvl"] for r in stages.sync_rows({"start_f": 0, "end_f": m16["total_f"], "hits": [], "cuts_f": []}, m16)]
    blk = stages.sync_block(rows, s4)
    must = stages.must_do_block(s4, stages.spec_for_agent(s4, tp_, series["transitions"], rows))
    check("本镜卡点表：drop / S 档 / 节拍表 big = 重，其余重音 = 轻，其余起音 = 可；表外的帧不许强变化；给代理的文字里没有门禁阈值（倍数、5.22、4.33）",
          lv.get(mm["drop"]["F"]) == "重" and lv.get(mm["slots"][90]["F"]) == "重" and "可" in lv.values()
          and lv16.count("重") == 17 and "轻" in lv16
          and "表里没有的帧" in blk and "T.F >= F" in blk and not any(x in blk + must for x in ("3 倍", "2 倍", "5.22", "4.33", "≥"))
          and "只读自己的 work/ 和 ctx/" in must and "原地来回" in must and stages.sync_table(rows)[0].keys() == {"F", "lvl", "attack_f", "desc", "kind"})
    # 急停那一格只出一行（急停帧）；试跑短片的「重」按 top_n 折算、最后一个重音之后的格不算「重」
    mst = json.loads(json.dumps(m16))
    st0 = mst["stops"][0]
    i_st = stages.six(st0["bar"], st0["beat"], st0.get("sub", 0))
    mst["slots"][i_st].update(total=0.97, tier="B", attack_f=st0["F"] - 2.4, F=st0["F"] - 2)     # 急停那一格自己也有个起音
    r_st = stages.sync_rows({"start_f": st0["F"] - 40, "end_f": st0["F"] + 40, "hits": [], "cuts_f": []}, mst)
    sm8 = inputs.short_map(m16, 8, 150.0)
    la8 = sm8["last_accent"]["F"]
    r8 = stages.sync_rows({"start_f": 0, "end_f": sm8["total_f"], "hits": [], "cuts_f": []}, sm8)
    tb, _ = stages.music_slots(mst, 16)
    check("本镜卡点表：急停那一格只出一行（急停帧、kind=stop），不再和起音帧两行打架；试跑短片「重」= 折算的 top_n 个（16 × 8/16 = 8），"
          "最后一个重音之后的格只算「可」；导演表里急停那一行写急停帧",
          [r["F"] for r in r_st if abs(r["F"] - st0["F"]) <= 3] == [st0["F"]] and next(r for r in r_st if r["F"] == st0["F"])["kind"] == "stop"
          and sm8["top_n"] == 8 and sum(1 for r in r8 if r["lvl"] == "重") <= 8 + 2 and all(r["lvl"] == "可" for r in r8 if r["F"] > la8 + 1)
          and any(ln.startswith(f"| {st0['F']} |") and "急停" in ln for ln in tb.splitlines())
          and not any(ln.startswith(f"| {st0['F'] - 2} |") for ln in tb.splitlines()))
    slots_md, map_md = stages.music_slots(mm, 8)
    check("导演提示词的音乐打击点表：S / A 档、drop、急停、最后一个重音、DROP 前最安静的一拍都在，逐小节谱 8 行",
          all(x in slots_md for x in ("drop（唯一", "stop 急停", "最后一个重音 = final", "DROP 前最安静的一拍 = breath", "乐句最强"))
          and len(map_md.splitlines()) == 8)
    # sync_force：没卡上的判不过（冻结的只报告），无起音强变化只当建议；loop_stop 有 sync_failed 不按分数停、不以全过收尾
    rs = {k: {"id": k, "pass": True, "score": 4, "issues": []} for k in ("s01", "s02", "s03")}
    sy = {"issues": [{"shot": "s01", "F": 459, "kind": "weak", "lvl": "重", "msg": "第 459 帧：没卡上，1.1 倍，要 ≥ 3 倍"},
                     {"shot": "s02", "F": 600, "kind": "silent", "lvl": "", "msg": "第 600 帧：没有起音却有强变化"},
                     {"shot": "s04", "F": 900, "kind": "late", "lvl": "轻", "msg": "第 900 帧：晚 1 帧"}]}
    sf, sfz = stages.sync_force(rs, sy, {"s04": {}})
    ls = stages.loop_stop
    check("审片：实测卡点没卡上的镜头判不过（带帧号、要多少倍进问题单），无起音处强变化只当建议；已冻结的只报告；"
          "还有 sync_failed 时到 6 分也不停",
          sf == ["s01"] and not rs["s01"]["pass"] and "1.1 倍" in rs["s01"]["issues"][0]["where"]
          and rs["s01"]["issues"][0]["fix"] == stages.FIX_SYNC and rs["s02"]["pass"] and rs["s02"]["issues"] and sfz == ["s04"]
          and ls({"failed": ["s01"], "sync_failed": ["s01"], "global": {"anchor_score": 6.5}}, 2, 3, 6) is None
          and ls({"failed": [], "sync_failed": ["s01"], "global": {"anchor_score": 4}}, 2, 3, 6) is None)
    # motion_force：没有进展、原地摆动充数分开判不过、各给各的修法
    mc_ = dict(stages.MOTION, need=5.22, need_eff=4.33)
    st_ = lambda eff, J, ok, sw: {"mean": 8.0, "median": 7.0, "still": 0.0, "still_ex": 0.0, "n": 80, "eff": eff, "J": J,  # noqa: E731
                                  "pingpong": 0, "ok": ok, "sway_ok": sw}
    mo_ = {"all": {"ok": True}, "s05": st_(6.0, 0.6, True, False), "s06": st_(3.0, 0.1, False, True), "s07": st_(2.0, 0.7, False, False),
           "s08": st_(7.0, 0.1, True, True)}
    rs2 = {k: {"id": k, "pass": True, "score": 4, "issues": []} for k in ("s05", "s06", "s07", "s08")}
    mf, _ = stages.motion_force(rs2, mo_, mc_)
    r5, r6, r7 = ([i["rule"] for i in rs2[k]["issues"]] for k in ("s05", "s06", "s07"))
    check("审片：原地摆动充数（FIX_SWAY：删掉来回）和没有进展（FIX_MOVE：单向做完一件事）分开判不过；两样都有的两条都进问题单；够的照样过",
          mf == ["s05", "s06", "s07"] and r5 == [stages.RULE_SWAY] and r6 == [stages.RULE_MOVE] and r7 == [stages.RULE_SWAY, stages.RULE_MOVE]
          and rs2["s05"]["issues"][0]["fix"] == stages.FIX_SWAY and rs2["s06"]["issues"][0]["fix"] == stages.FIX_MOVE
          and rs2["s08"]["pass"] and "本镜卡点" in stages.FIX_MOVE and "全部删掉" in stages.FIX_SWAY
          and "原地摆动充数" in stages.motion_line("s05", mo_["s05"]) and "没有进展" in stages.motion_line("s06", mo_["s06"])
          and not any(x in stages.motion_want(dict(stages.MOTION)) for x in ("4.33", "5.22", "≥")))
    # 试跑短片：截断窗口
    full = synth_map(bars=16, stop_bar=12)
    sm = inputs.short_map(full, 8, 150.0)
    check("试跑短片：music_map 截成前 8 小节（片长 = 8 小节、slots 128 条、谱 8 行），窗口外的急停去掉，最后一个重音在截断窗口里重取，drop 不变",
          sm["total_f"] == stages.frame_at({"bar_f": 4 * 24.0, "beat_f": 24.0}, 9) == 768 and len(sm["slots"]) == 128
          and len(sm["bar_map"]) == 8 and sm["stops"] == [] and sm["last_accent"]["bar"] == 8 and sm["drop"] == full["drop"]
          and sm["last_accent"] != full["last_accent"] and sm["breath"]["bar"] == 4)
    ep = os.path.join(tmp, "short_ep")
    os.makedirs(os.path.join(ep, "inputs"))
    mp_full = os.path.join(ep, "full.music_map.json")
    util.write_json(mp_full, full)
    m0 = {"file": os.path.join(ep, "no-such.mp3"), "bpm": 150.0, "downbeat_s": 0.0, "start_s": 10.0, "bars": 16, "drop_bar": 5,
          "music_map": mp_full, "window_events": [{"kind": "stop", "bar": 12}]}
    m8 = inputs.music_ready(m0, 8, None, ep)
    m8b = inputs.music_ready(m8, None, None, ep)
    try:
        inputs.music_ready(dict(m0, drop_bar=9), 8, None, ep)
        drop_err = False
    except util.Stop as e_:
        drop_err = "DROP" in e_.msg
    check("试跑短片：music.json 记 short / bars_full、bars 截成 8、窗口外的事件去掉；截断的打击点表写到 inputs/music_map.json、全窗口的记在 "
          "music_map_full；再跑一次不变；DROP 不在截断窗口里就停",
          m8["short"] == 8 and m8["bars"] == 8 and m8["bars_full"] == 16 and m8["window_events"] == []
          and m8["music_map"] == os.path.join(ep, "inputs", "music_map.json") and m8["music_map_full"] == mp_full
          and util.read_json(m8["music_map"])["total_f"] == 768 and m8b == m8 and drop_err and m8["start_s"] == m0["start_s"])
    # 短片模式：第 2 环节跳过，交付行「先做 5 秒样片」记不适用；任务书模板不残留 {{SYNC}} 之类
    util.write_json(os.path.join(ep, "inputs", "music.json"), m8)
    util.write_json(os.path.join(ep, "inputs", "footage.json"), {"clips": []})
    util.write_json(os.path.join(ep, "topic.json"), dict(tp_, episode=4, category_id="beat"))

    class R:
        root, py = os.path.join(ep, "engine"), None

        def setup(self, *a):
            pass
    c = stages.Ctx(ep, series, cfg, None, log, None, None, R(), True)
    c.short = 8
    stages.s2_sample(c)
    smp = util.read_json(os.path.join(ep, "sample.json"))
    rows_t = {r["id"]: r for r in stages.deliver_rows(c)}
    check("试跑短片：第 2 环节跳过（sample.json {skipped: short}，产物清单只看 sample.json）；交付行「先做 5 秒样片」记不适用（不挡交付）",
          smp.get("skipped") == "short" and stages.stage_outputs(c, 2, ["sample.json", "keyframes.json", "film/sample.mp4"]) == ["sample.json"]
          and rows_t["root.flow.2"]["status"] == "na" and c.mm()["total_f"] == 768)
    bm8 = stages.expand_beatmap(mockdata.beatmap_mm(c.mm(), 150.0, 8, 5), c.mm())
    sl8 = stages.expand_shotlist(mockdata.shotlist({"beatmap": bm8, "topic": tp_}), bm8, bm8["sections"], c.mm())
    left = []
    for kind in ("shot", "fix"):
        try:
            t_, _ = stages.shot_task(c, sl8["shots"][1], kind, tp_, sl8, "- ctx/x", [{"rule": "r", "where": "w", "fix": "f"}], 1)
            left += __import__("re").findall(r"\{\{[^}]+\}\}", t_)
            sync_in = ("{{SYNC}}" not in util.read(os.path.join(util.HERE, "prompts", "03_shot_agent.md"))) or "本镜卡点" in t_
        except KeyError as e_:
            left.append(str(e_))
            sync_in = False
    try:
        dp = c.prompt("01_director", SERIES="C", N="4", TITLE="t", TOPIC="x", BPM_RULE="r", DUR="10–30",
                      MUSIC_SLOTS=stages.music_slots(c.mm(), 8)[0], MUSIC_MAP=stages.music_slots(c.mm(), 8)[1])
        left += __import__("re").findall(r"\{\{[^}]+\}\}", dp)
    except KeyError as e_:
        left.append(str(e_))
    check("提示词：制作 / 返修任务书（SYNC = 本镜卡点表，CUTS / HITS 照传）、导演提示词（MUSIC_SLOTS / MUSIC_MAP）都填满，不残留 {{…}}",
          not left and sync_in)
    # 渲完逐镜量卡点（G.shot_sync 换成假的）：没做到发回 1 次，第二次照样收下、记进 done.json 的 sync_misses
    wk = os.path.join(ep, "shots", "s09", "work")
    os.makedirs(os.path.join(wk, "out"))
    s9 = {"id": "s09", "start_f": 0, "end_f": 12, "cuts_f": [6], "hits": [], "look": "design", "spec_sha": "x"}
    util.write(os.path.join(wk, "out", "shot.py"), mockshot.shot_code("s09", dict(s9, signature_color={"hex": "#5FF2FF"}), [], "#5FF2FF"))

    class RR:
        root, py = os.path.join(ep, "engine"), sys.executable

        def render_shot(self, spec, code, out):
            media.placeholder_shot(out, spec["end_f"] - spec["start_f"], [6], "s09", 0)
            return {"ok": True}
    calls_ = []
    real = G.shot_sync
    G.shot_sync = lambda render, start_f, table, py, cfg=None: calls_.append((start_f, len(table))) or {  # noqa: E731
        "ok": False, "misses": [{"msg": "第 6 帧（重）：没卡上，1.0 倍，要 ≥ 3 倍"}], "silent": [], "text": "## 卡点没做到（编排器渲完逐帧量的）\n- 第 6 帧"}
    try:
        c.renderer = RR()
        stt = {"sync_sent": 0, "table": [{"F": 6, "lvl": "重", "attack_f": 5.6, "desc": "x"}]}
        rp, dp_ = os.path.join(ep, "shots", "s09", "render.mp4"), os.path.join(ep, "shots", "s09", "done.json")
        d1, e1_ = stages.render_and_accept(c, s9, "shot", None, wk, os.path.join(wk, "out", "shot.py"), rp, dp_, strict=True, st=stt)
        d2, _ = stages.render_and_accept(c, s9, "shot", None, wk, os.path.join(wk, "out", "shot.py"), rp, dp_, strict=True, st=stt)
    finally:
        G.shot_sync = real
    check("制作：渲完按「本镜卡点」表逐帧量，没做到把问题单发回代理（每次 make_shot 最多 1 次）；第二次照样收下，没做到的记进 done.json 的 sync_misses",
          d1 is None and "卡点没做到" in e1_ and "第 6 帧" in e1_ and bool(d2) and d2["sync"]["ok"] is False and d2["sync"]["sent_back"] == 1
          and "1.0 倍" in d2["sync_misses"][0] and len(calls_) == 2 and calls_[0] == (0, 1) and os.path.exists(rp))
    # 片尾淡出、切线图（纯 ffmpeg）
    v = os.path.join(tmp, "fade_v.mp4")
    wav = os.path.join(tmp, "fade_a.wav")
    media.ff(["-f", "lavfi", "-i", "testsrc2=s=320x180:r=60:d=2", "-frames:v", "120"] + media.H264 + [v])
    media.ff(["-f", "lavfi", "-i", "sine=f=440:d=4:sample_rate=48000", "-ac", "2", wav])
    out = os.path.join(tmp, "fade_av.mp4")
    media.mux(v, wav, out, audio_start=0.5)
    rms = lambda a, b: float(__import__("re").findall(r"RMS level dB:\s*(-?[\d.]+|-inf)", subprocess.run(  # noqa: E731
        ["ffmpeg", "-hide_banner", "-nostdin", "-i", out, "-af", f"atrim=start={a}:end={b},astats", "-f", "null", "-"],
        capture_output=True, text=True, env=media.ENV).stderr)[0].replace("-inf", "-200"))
    ky = media.kymo(v, 10, 70, os.path.join(tmp, "kymo.jpg"))
    pk = media.probe(ky)
    check("混流：片尾 0.25 s 淡出（最后 0.1 s 比中段低 ≥ 10 dB）；切线图：每帧一行 3 px（60 帧 → 960×180）",
          rms(1.0, 1.5) - rms(1.92, 2.0) >= 10 and pk.get("width") == 960 and pk.get("height") == 180)


def final2_ok(tmp, series, cfg, log, tp_):
    """11-04 晚第二轮验收修复（审查 F1–F9 / intent F1–F12 / e2e R1–R8）：必做检查认新零件
    低帧率素材没 blend 的提示、慢段、静图 + 推拉、预算算 samples、报告逐镜一行、SIGHUP / SIGTERM 走 Ctrl-C。不跑模型、不真渲。"""
    import signal as SG
    eng_src = os.path.join(util.PARENT, series["engine"]["src"])
    ep = os.path.join(tmp, "final2_ep")
    os.makedirs(os.path.join(ep, "inputs"))
    util.write_json(os.path.join(ep, "inputs", "footage.json"), {"clips": [{"name": "F01", "fps": 24.0}, {"name": "F02", "fps": 60.0}]})
    sx = {"id": "s05", "start_f": 0, "end_f": 192, "hits": [{"kind": "drop", "F": 96, "what": "x"}], "subject_src": {"kind": "设计"},
          "transition_in": "拍上硬切", "transition_out": "拍上硬切"}
    util.write_json(os.path.join(ep, "shotlist.json"), {"alloc": {"numbers": []}, "shots": [sx], "total_f": 192})
    c = stages.Ctx(ep, series, cfg, None, log, None, None, None, True)
    wk = os.path.join(ep, "shots", "s05", "work")
    os.makedirs(os.path.join(wk, "out"), exist_ok=True)
    recipe = os.path.join(wk, "out", "recipe.py")
    util.write(recipe, "from src.engine import shot\nfrom src.fx import series as SR\nCAM = SR.AutoCam(0, 192, move='truck', px_per_f=8)\n\n"
               "@shot(subframes=1)\ndef s05(E, T):\n    img = CAM.render(E, T, E.solid('#000000'))\n"
               "    img = SR.element(E, T, img, kind='snow')\n    return SR.whip(E, img, T.f, 96)\n")
    only_p = os.path.join(wk, "out", "only.py")
    util.write(only_p, "from src.fx import series as SR\n\ndef s05(E, T):\n    return SR.element(E, T, E.solid('#000000'))\n")
    p2_ = os.path.join(wk, "out", "p2d.py")                            # 2D 粒子 + HUD 不再算零件
    util.write(p2_, "from src.fx import series as SR\n\ndef s05(E, T):\n    img = SR.particles(E, T, E.solid('#000000'))\n"
               "    return SR.ring_hud(E, img)\n")
    # 4 缓存键：单向键（cam / grow / travel）并进 frames 起点（travel 再加止点），只改 frames 就重渲
    # 5 旧引擎副本标记
    od = os.path.join(tmp, "old_src")
    os.makedirs(os.path.join(od, "fx"))
    util.write(os.path.join(od, "fx", "series.py"), "def seg(F, cuts):\n    pass\n")
    msgs = []
    eo = render.ChromaEngine(ep, series, lambda m, echo=True: msgs.append(m))
    eo._stale_note(od)
    en = render.ChromaEngine(ep, series, lambda m, echo=True: msgs.append("NEW " + m))
    en._stale_note(eng_src)
    # 6 静止词：否定、读数归零不拦
    sh_ = stages.still_hits
    check("计划门禁静止词：「不等停稳就切」「不会越来越慢」「读数归零」不拦；「画面归零」「灯管停稳」「几乎不动」照拦",
          sh_("上一镜不等停稳就切进来") == [] and sh_("读数一路往下走、不会越来越慢") == [] and sh_("温度读数归零") == []
          and sh_("画面归零") == ["归零"] and sh_("最后灯管停稳") == ["停稳"] and sh_("灯管几乎不动") == ["几乎不动"])
    s9 = {"id": "s09", "start_f": 400, "end_f": 480, "hits": []}
    wk9 = os.path.join(ep, "shots", "s09", "work")
    os.makedirs(os.path.join(wk9, "out"), exist_ok=True)
    code9 = os.path.join(wk9, "out", "shot.py")
    # 8 低帧率素材没 blend（审查 intent F1）、慢段 / 没量死区（F5 / F12）、静图 + 推拉（F3）
    util.write(code9, "from src.footage import Take\nA = Take('F01', film=(0, 40), src=1.0)\nB = Take('F01', film=(0, 40), interp='blend')\n"
               "C = Take('F01', film=(0, 40), speed=2.6)\nD = Take('F02', film=(0, 40))\nE2 = Take('F01', film=(0, 40), speed=1.5)\n")
    stk = stages.slow_takes(c, code9)
    z = {"a": 410, "b": 433, "n": 24, "d96": 1.6, "px_f": 1.2, "px_f_max": 2.0, "moving": 3, "grid": "······ / ··■■·· / ······ / ······"}
    mc = stages.motion_cfg(c)
    sb = stages.motion_sendback(s9, {"eff": 6.1, "J": 0.1, "mean": 7.0, "still": 0.0, "n": 70, "ok": True, "sway_ok": True, "pan": True,
                                     "dead": [], "slow": [z]}, mc, slow_takes=stk)
    check("运动量发回（审查 intent F1 / F3 / F5 / F12）：低于 60 fps、没写 interp='blend' 又没变速够的 Take 点名（blend、speed 2.6、60 fps 的不点）；"
          "静图 + 推拉单独给修法；没有死区但慢的给「运动量最低的几段」；整片量的写「这一版没量死区」；门禁线数字不写",
          stk == ["F01（24 fps，speed 1）", "F01（24 fps，speed 1.5）"] and "interp='blend'" in sb and "静图 + 推拉" in sb and "第 410–433 帧" in sb
          and "运动量最低的几段" in sb and "这一版没量死区" in stages.dead_text(None)
          and "没有（" in stages.dead_text([]) and not any(x in sb for x in ("4.33", "0.45", "0.57", "0.9 ", "0.5）")))
    # 9 报告逐镜一行；SIGHUP / SIGTERM 走 Ctrl-C
    util.write_json(os.path.join(ep, "shots", "s06", "done.json"), {"motion": {"measured": True, "eff": 3.1, "ok": False, "sent_back": 1},
                                                                   "sync": {"ok": True, "sent_back": 1}})
    hs = [SG.getsignal(x) for x in (SG.SIGHUP, SG.SIGTERM)]
    # 10 给代理的标定（审查 intent F2 / F7）：SR.track 默认参数的实测、主角推近累计上限、粒子只当陪衬


CF_SNIP = {
    "c1": "from src.fx import series as SR\n\ndef s01(E, T):\n    img = E.solid('#000000')\n    return SR.particles(E, T, img, 'snow', c=(900, 300))\n",
    "c1_alias": "from src.fx import series as S2\nfrom src.fx.series import particles as P2\n\ndef s01(E, T):\n"
                "    img = S2.particles(E, T, None, 'shards')\n    return P2(E, T, img, 'frost')\n",
    "c1_front": "from src.fx import series as SR\n\ndef s01(E, T):\n    return SR.track(E, T, None, [], 0, 99, front='snow')\n",
    "zoom": "from src.footage import Take\nfrom src.kit import fx\nA = Take('F15', film=(0, 99))\nB = Take('F01', film=(0, 99), tier=2160)\n\n"
            "def big(E, T):\n    return fx.cam(E, T, E.clip(A, T.F), lambda f: dict(zoom=6.0 * (1 + 0.01 * f)), n=3)\n\n"
            "def ok4k(E, T, raw):\n    return fx.cam(E, T, raw, lambda f: dict(zoom=1.3), n=3)\n",
    "zoom_4k": "from src.footage import Take\nfrom src.kit import fx\nB = Take('F01', film=(0, 99), tier=2160)\n\n"
               "def s01(E, T):\n    return fx.cam(E, T, E.clip(B, T.F), lambda f: dict(zoom=2.0), n=3)\n",
    "hits": "from src.fx import series as SR, kaleido as KV\nfrom src.kit import hero as HE\n"
            "H = [SR.hit(10, 'post3_blue', sparks=40), SR.hit(20, 'lines'), SR.hit(30, 'post3_blue')]\n"
            "V = KV.Vortex(0, 99, rim=KV.ICE)\nW = KV.Vortex(0, 99)\nX = HE.hero('ICE', 10, style='slab')\nY = HE.hero('ICE', 10, style='card')\n",
    "hud": "def s01(E, T):\n    cv = E.canvas()\n" + "".join(f"    cv.line({i}, 0, {i}, 9, w=1.2)\n" for i in range(20)) + "    return cv.texture()\n",
    "cg": "from src.footage import Take\nA = Take('F34', film=(0, 99))\n",
}
CF_CLIPS = [{"name": "F15", "w": 1920, "h": 1080}, {"name": "F01", "w": 3840, "h": 2160},
            {"name": "F34", "w": 1920, "h": 1080, "cg": True, "burned_text": True}]


def cheapfx_ok(tmp, series, cfg, log, tp_):
    """11-05 第三轮（不许劣质特效）：lib/cheapfx.py 静态扫描（C1 2D 粒子、C2 渲染图复制拼装、Z 放大、C3 / C5 只提醒、CG 主体、C4 只给坐标）、
    不跑模型、不真渲。"""
    from lib import cheapfx as CF
    sc = lambda k, spec=None, motif="霓虹": CF.rows(CF.scan(CF_SNIP[k], CF_CLIPS, spec, motif))  # noqa: E731
    rl = lambda rows: sorted(r["rule"] for r in rows)  # noqa: E731
    s1, w1 = sc("c1")
    sa, _ = sc("c1_alias")
    sf, wf = sc("c1_front")
    sfo, _ = CF.rows(CF.scan(CF_SNIP["c1_front"], CF_CLIPS, None, "霓虹", old_engine=True))
    sz, wz = sc("zoom")
    s4k, w4k = sc("zoom_4k")
    sh, wh = sc("hits")
    shf, whf = sc("hits", motif="火")
    _, wu = sc("hud")
    scg, wcg = sc("cg", {"subject_src": {"kind": "实拍", "file": "F34"}})
    r1 = CF.scan(CF_SNIP["c1"], CF_CLIPS)
    check("劣质特效扫描（第三轮 2 条）：C1 SR.particles 发回（换了别名、from 导入也认），SR.track(front='snow') 新引擎是实拍雪不算、"
          "旧引擎副本里算；"
          "Z 1080p 素材 zoom 6 发回、4K tier=2160 的 zoom 2 不算；C3 非火电题材 sparks=40 只提醒、"
          "火题材不提醒、集中线（lines）白名单；C5 万花筒边光写死 ICE、slab 描边字只提醒；≤1.4 px 的 HUD 细线不算矢量场；"
          "CG 素材当主体只提醒；C1 的 c=(x, y) 给审片当裁图坐标",
          rl(s1) == ["C1"] and w1 == [] and rl(sa) == ["C1", "C1"] and sf == [] and wf == [] and rl(sfo) == ["C1"]
          and rl(sz) == ["Z"] and sz[0]["line"] == 7 and s4k == [] and w4k == [] and sh == []
          and rl(wh) == ["C3", "C5", "C5", "C5", "C5"] and shf == [] and rl(whf) == ["C5"] and wu == [] and scg == []
          and rl(wcg) == ["CG"] and r1["coords"] and r1["coords"][0]["x"] == 900)
    ep = os.path.join(tmp, "cheapfx_ep")
    os.makedirs(os.path.join(ep, "inputs"))
    util.write_json(os.path.join(ep, "inputs", "footage.json"), {"clips": CF_CLIPS})
    util.write_json(os.path.join(ep, "topic.json"), dict(tp_, episode=4, category_id="beat", motif="霓虹"))

    class RB:
        name, mock = "stub", False

        def __init__(self):
            self.root, self.py, self.calls = os.path.join(ep, "engine"), None, []

        def render_shot(self, spec, code, out):
            self.calls.append(spec["id"])
            media.placeholder_shot(out, spec["end_f"] - spec["start_f"], [20], spec["id"], 0)
            return {"ok": True}
    c = stages.Ctx(ep, series, cfg, None, log, None, None, RB(), False)
    os.makedirs(os.path.join(ep, "engine", "logs"))
    s7 = {"id": "s07", "start_f": 0, "end_f": 40, "cuts_f": [20], "hits": [], "look": "design"}
    wk = os.path.join(ep, "shots", "s07", "work")
    os.makedirs(os.path.join(wk, "out"), exist_ok=True)
    code = os.path.join(wk, "out", "shot.py")
    util.write(code, mockshot.shot_code("s07", dict(s7, signature_color={"hex": "#5FF2FF"}), [], "#5FF2FF", 0, None, cheap=True))
    rp, dp = os.path.join(ep, "shots", "s07", "render.mp4"), os.path.join(ep, "shots", "s07", "done.json")
    st = {"sync_sent": 0, "motion_sent": 0, "fx_sent": 0, "table": []}
    d1, e1 = stages.render_and_accept(c, s7, "shot", None, wk, code, rp, dp, strict=True, st=st)
    d2, e2 = stages.render_and_accept(c, s7, "shot", None, wk, code, rp, dp, strict=True, st=st)
    d3, e3 = stages.render_and_accept(c, s7, "shot", None, wk, code, rp, dp, strict=True, st=st)
    c.mock = True
    st2 = {"sync_sent": 0, "motion_sent": 0, "fx_sent": 1, "table": []}
    d4, _ = stages.render_and_accept(c, s7, "shot", None, wk, code, rp, dp, strict=True, st=st2)
    c.mock = False
    # check.sh / ctx：同一份检查进沙箱，叠层表进 ctx
    c.renderer = render.FakeRenderer(ep, series, log)
    c.renderer.setup()
    files = stages.prepare_ctx(c, wk, dict(s7, parts=["particles", "element"]), dict(tp_, motif="霓虹"), {"fn": "s07"})
    ck = util.read(os.path.join(wk, "ctx", "check.sh"))
    out = subprocess.run(["bash", "-c", f'cd "{wk}" && "{sys.executable}" -I ctx/cheapfx.py check out/shot.py --footage ctx/footage.json '
                          f'--spec ctx/spec.json --motif 霓虹'], capture_output=True, text=True, timeout=60)
    ov_ok = (not inputs.overlays_path(series, "霓虹")) or (os.path.exists(os.path.join(wk, "ctx", "overlays.json")) and "ctx/overlays.json" in files)
    check("ctx/check.sh（第三轮 2 条）：先跑同一份劣质特效自查（ctx/cheapfx.py，退出码恒 0，不挡后面的引擎自查）；叠层表 ctx/overlays.json "
          "进 ctx；旧零件名 particles 按叠层 + 碎裂给文件",
          "ctx/cheapfx.py\" check" in ck and "|| true" in ck and "--motif \"霓虹\"" in ck and os.path.exists(os.path.join(wk, "ctx", "cheapfx.py"))
          and ov_ok and "series.py" in files and out.returncode == 0 and "劣质特效" in out.stdout and "SR.particles" in out.stdout)
    mp = os.path.join(wk, "out", "parts.py")
    util.write(mp, "from src.fx import series as SR\n\ndef s07(E, T):\n    return SR.element(E, T, SR.shatter(E, E.solid('#000'), T.F))\n")
    # 素材来源类型（第三轮 17 条）


def _sync_issue():
    """sync_force 真生成的卡点硬门禁条目（规则名改过一次，用户轮靠「交付硬门禁」几个字认，这里不写死）。"""
    v = {"s01": {"pass": True, "issues": []}}
    stages.sync_force(v, {"issues": [{"shot": "s01", "kind": "late", "F": 20, "msg": "第 20 帧晚了 2 帧"}]})
    return v["s01"]["issues"][0]


def _notes_ep(tmp, name, series, log, tp_, freeze_s02=True):
    """看片意见轮的测试集：两镜（s01 第 1 轮返修已收、s02 一稿 + 返修到一半的代码），cut_r0 由 FakeRenderer 渲一稿拼成（md5 对得上）。"""
    ep = os.path.join(tmp, name)
    for d in ("inputs", "film", "logs", "shots/s01/work/out", "shots/s02/work/out", "engine/src/fx"):
        os.makedirs(os.path.join(ep, d), exist_ok=True)
    util.write_json(os.path.join(ep, "topic.json"), dict(tp_, episode=4, category_id="beat", motif="霓虹"))
    util.write_json(os.path.join(ep, "inputs", "footage.json"), {"clips": []})
    util.write_json(os.path.join(ep, "inputs", "music.json"), {"note": "selftest"})
    bm = {"bpm": 150.0, "bars": 2, "beat_f": 24.0, "bar_f": 96.0, "total_f": 80, "hits": [], "sections": [{"id": "S1", "name": "A", "bars": [1, 3]}]}
    base = {"section": "S1", "hits": [], "look": "footage", "subject": "霓虹", "action": "裂开", "hero_word": "ICE", "words": ["ICE"],
            "label": "ICE", "event": "换构图", "image": "霓虹", "parts": ["camera"], "footage": "none",
            "transition_in": {"kind": "拍上硬切"}, "transition_out": {"kind": "拍上硬切"}}
    shots = [dict(base, id="s01", start_f=0, end_f=40, cuts_f=[20]), dict(base, id="s02", start_f=40, end_f=80, cuts_f=[60])]
    sl = {"total_f": 80, "shots": shots}
    util.write_json(os.path.join(ep, "beatmap.json"), bm)
    util.write_json(os.path.join(ep, "shotlist.json"), sl)
    util.write(os.path.join(ep, "engine", "src", "fx", "series.py"), "# old engine copy\\n")
    fr = render.FakeRenderer(ep, series, log)
    rends = []
    for sp in shots:
        sd = os.path.join(ep, "shots", sp["id"])
        r0 = mockshot.shot_code(sp["id"], dict(sp, signature_color={"hex": "#5FF2FF"}), [], "#5FF2FF", 0, None, cheap=sp["id"] == "s01")
        util.write(os.path.join(sd, "shot_shot.py"), r0)
        fr.render_shot(sp, os.path.join(sd, "shot_shot.py"), os.path.join(sd, "render.mp4"))
        rends.append(os.path.join(sd, "render.mp4"))
        if sp["id"] == "s01":                                          # 第 1 轮返修已收：work/out 是返修版、done.json kind=fix
            fx1 = mockshot.shot_code("s01", dict(sp, signature_color={"hex": "#5FF2FF"}), [], "#5FF2FF", 1, None)
            util.write(os.path.join(sd, "work", "out", "shot.py"), fx1)
            util.write(os.path.join(sd, "shot_fix1.py"), fx1)
            util.write_json(os.path.join(sd, "done.json"), {"id": "s01", "kind": "fix", "round": 1, "code_sha": "x", "render_sha": "y",
                                                            "motion": {"measured": True, "eff": 9.1}})
        else:                                                          # 一稿 + 返修被打断的半截代码（没渲过）
            util.write(os.path.join(sd, "work", "out", "shot.py"), "variant = 7\\n# half\\n")
            util.write_json(os.path.join(sd, "done.json"), {"id": "s02", "kind": "shot", "round": None, "code_sha": "a",
                                                            "render_sha": util.sha256_file(rends[-1]), "motion": {"measured": True, "eff": 5.0}})
    media.concat(rends, os.path.join(ep, "film", "cut_r0.mp4"))
    util.write_json(os.path.join(ep, "film", "cut_r0.md5.json"), media.framemd5(os.path.join(ep, "film", "cut_r0.mp4")))
    hard = _sync_issue()
    soft = {"rule": "审片意见 x", "where": "图 2", "fix": "换构图"}
    util.write_json(os.path.join(ep, "review_1.json"), {
        "round": 1, "cut": "film/cut_r0.mp4", "shots": {"s01": {"id": "s01", "pass": False, "score": 3, "issues": [hard, soft]},
                                                        "s02": {"id": "s02", "pass": False, "score": 3, "issues": [soft]}},
        "global": {"issues": [], "anchor_score": 4.0}, "cuts": [], "motion": {"s01": {"eff": 7.7}, "s02": {"eff": 5.0}},
        "failed": ["s01", "s02"], "passed": [], "sync_failed": ["s01"]})
    util.write_json(os.path.join(ep, "freeze.json"), {"s02": {"start_f": 40, "end_f": 80, "md5": "z"}} if freeze_s02 else {})
    state = {"series": "chroma", "mock": True, "stages": {str(k): {"status": "done"} for k in (0, 1, 2, 3)}}
    util.write_json(os.path.join(ep, "state.json"), state)
    return ep, state, sl


def notes_ok(tmp, series, cfg, log, tp_):
    """11-05 第三轮 4 条：看片意见入口（--notes）。targeted：存档 → 恢复一稿（rerender 的用这一集现有引擎副本重渲、逐帧 md5 对 cut_r0；
    rerender=false 的只换回代码）→ 升级引擎副本 → 解冻 → 合成 source='notes' 的问题单（用户条目 + 劣质特效扫描 + 机器量硬门禁，审片主观意见不带）
    → 用户条目的 100 % 裁图；同一份 notes 只消费一次、续跑不重做；返修任务书带 {{NOTES_BLOCK}} 块。append：redo_fixed 只发用户条目和扫描、
    done.json 挪进 _old 留 fix_pre_notes 副本，其余镜头用户条目插在原问题单前面。不跑模型、不真渲（FakeRenderer）。"""
    ep, state, sl = _notes_ep(tmp, "notes_ep", series, log, tp_)
    notes = {"said": ["不能用劣质特效：贴图、线段"], "mode": "targeted", "scope_note": "这一轮只换特效来源（测试）", "archive": ["review_1.json"],
             "restore": {"s01": {"code": "shots/s01/shot_shot.py"}, "s02": {"code": "shots/s02/shot_shot.py", "rerender": False}},
             "unfreeze": ["s02"], "motion_floor_r0": {"s01": 7.78, "s02": 5.0},
             "shots": {"s01": [{"rule": "用户 · 劣质特效 · 线段", "frames": [5, 30], "box": [560, 200, 1360, 880], "where": "灯管边缘", "fix": "换实拍光斑"}],
                       "s02": [{"rule": "劣质特效 · 贴图", "frames": [45, 60], "where": "碎片", "fix": "SR.shatter"}],
                       "s09": [{"rule": "x", "fix": "y"}]}}
    np_ = os.path.join(tmp, "review_notes_t.json")
    util.write_json(np_, notes)
    bad = os.path.join(tmp, "notes_bad.json")
    util.write_json(bad, {"mode": "later", "shots": {"s01": [{"rule": "x"}]}})
    try:
        stages.notes_intake(ep, bad, dict(state), log)
        bad_ok = False
    except util.Stop as e:
        bad_ok = "mode" in e.msg and "fix" in e.msg
    got1 = stages.notes_intake(ep, np_, state, log)
    sha = list(state["notes_rounds"])[0]
    c = stages.Ctx(ep, series, cfg, None, log, None, None, render.FakeRenderer(ep, series, log), True)
    c.state = state
    r = stages.notes_round(c)
    pr = util.read_json(os.path.join(ep, "notes_round.json"))
    stamp = pr["stamp"]
    rv = util.read_json(os.path.join(ep, "review_1.json"))
    i1, i2 = rv["shots"]["s01"]["issues"], rv["shots"]["s02"]["issues"]
    d1 = util.read_json(os.path.join(ep, "shots", "s01", "done.json"))
    o1 = os.path.join(ep, "shots", "s01", "_old", stamp)
    check("看片意见轮 targeted（第三轮 4 条）：notes 校验（mode、fix）；复制成 review_notes.json、state 记 pending；review_1.json 挪进 _old；"
          "s01 的 work/out、done.json、render、shot_fix1.py 挪进 shots/s01/_old/<时间>/，一稿代码用这一集现有引擎副本重渲（kind=restore），"
          "逐帧 md5 和 cut_r0 一样；s02（rerender=false）只换回一稿代码、done.json 不动",
          bad_ok and got1 == "pending" and r == 1 and pr.get("done") and os.path.exists(os.path.join(ep, "_old", stamp, "review_1.json"))
          and util.read(os.path.join(o1, "work", "out", "shot.py")).find("variant = 1") >= 0 and os.path.exists(os.path.join(o1, "shot_fix1.py"))
          and util.read_json(os.path.join(o1, "done.json"))["kind"] == "fix" and d1["kind"] == "restore" and d1["round"] == 1
          and pr["restore"]["s01"]["md5_same"] is True and pr["restore"]["s02"].get("rerender") is False
          and util.read(os.path.join(ep, "shots", "s02", "work", "out", "shot.py")) == util.read(os.path.join(ep, "shots", "s02", "shot_shot.py"))
          and util.read_json(os.path.join(ep, "shots", "s02", "done.json"))["kind"] == "shot"
          and os.path.exists(os.path.join(ep, "shots", "s02", "_old", stamp, "work", "out", "shot.py")))
    eng = os.path.join(ep, "engine")
    check("看片意见轮：恢复重渲之后才换引擎（engine/src → engine/_old_src/<时间>，重新复制主引擎）；解冻 s02；合成的问题单 source=notes、"
          "两镜都不过：用户条目在最前（rule 补「用户 ·」、写帧号和框）+ 劣质特效扫描发回行 + 原审片里机器量的交付硬门禁（sync_force 真生成的卡点条目，"
          "sync_failed 认得出），审片主观意见不带；"
          "不在镜头表里的 s09 不管；用户条目有 100 % 裁图（框原尺寸）+ 中间帧 + 切线图",
          util.read(os.path.join(eng, "_old_src", stamp, "fx", "series.py")).startswith("# old")
          and "def track(" in util.read(os.path.join(eng, "src", "fx", "series.py"))
          and "s02" not in (util.read_json(os.path.join(ep, "freeze.json")) or {}) and rv.get("source") == "notes"
          and rv["failed"] == ["s01", "s02"] and i1[0]["rule"].startswith("用户") and "第 5–30 帧" in i1[0]["where"]
          and any("劣质特效（编排器查代码）" in x["rule"] for x in i1) and any("交付硬门禁" in x["rule"] for x in i1)
          and rv.get("sync_failed") == ["s01"]
          and not any(x["rule"] == "审片意见 x" for x in i1 + i2) and i2[0]["rule"] == "用户 · 劣质特效 · 贴图"
          and rv["global"]["anchor_score"] is None and os.path.exists(os.path.join(ep, "frames", "r1", "zoom_s01_notes0_f00005.jpg"))
          and os.path.exists(os.path.join(ep, "frames", "r1", "kymo_s01.jpg")) and os.path.exists(os.path.join(ep, "frames", "r1", "mid_f00020.jpg")))
    t1, _ = stages.shot_task(c, sl["shots"][0], "fix", dict(tp_, motif="霓虹"), sl, "- ctx/x", i1, 1)
    t2, _ = stages.shot_task(c, sl["shots"][0], "fix", dict(tp_, motif="霓虹"), sl, "- ctx/x", i1, 2)
    n_old = len(os.listdir(os.path.join(ep, "_old")))
    r2 = stages.notes_round(c)
    got2 = stages.notes_intake(ep, np_, state, log)
    check("看片意见轮：state.json 的 notes_rounds 记 done（马上落盘）；再跑一次 notes_round 不重做（不再存档）、返回同一轮；同一份 notes 再给就跳过；"
          "这一轮的返修任务书带 {{NOTES_BLOCK}} 块（原话、范围、本镜一稿运动量），下一轮不带",
          state["notes_rounds"][sha]["status"] == "done" and util.read_json(os.path.join(ep, "state.json"))["notes_rounds"][sha]["status"] == "done"
          and r2 == 1 and len(os.listdir(os.path.join(ep, "_old"))) == n_old and got2 == "done"
          and "不能用劣质特效" in t1 and "这一轮只换特效来源（测试）" in t1 and "7.78" in t1 and "看片意见轮" in t1
          and "不能用劣质特效" not in t2 and stages.notes_for(c, 1) and stages.notes_for(c, 2) is None)
    # append：redo_fixed 只发用户条目和扫描；别的镜头用户条目插在原问题单前面；原审片存一份
    ep2, st2, sl2 = _notes_ep(tmp, "notes_ep2", series, log, tp_, freeze_s02=False)
    n2 = {"said": ["Codex 还得再改"], "mode": "append", "redo_fixed": ["s01"], "motion_floor_r0": {"s01": 6.4, "s02": 5.0},
          "shots": {"s01": [{"rule": "用户 · 劣质特效 · 贴图拼装", "frames": [0, 39], "fix": "snowflake"}],
                    "s02": [{"rule": "用户 · 劣质特效 · 线段", "frames": [40, 79], "fix": "删掉"}]}}
    np2 = os.path.join(tmp, "review_notes_a.json")
    util.write_json(np2, n2)
    stages.notes_intake(ep2, np2, st2, log)
    c2 = stages.Ctx(ep2, series, cfg, None, log, None, None, render.FakeRenderer(ep2, series, log), True)
    c2.state = st2
    stages.notes_round(c2)
    pr2 = util.read_json(os.path.join(ep2, "notes_round.json"))
    rv2 = util.read_json(os.path.join(ep2, "review_1.json"))
    a1, a2 = rv2["shots"]["s01"]["issues"], rv2["shots"]["s02"]["issues"]
    dd = util.read_json(os.path.join(ep2, "shots", "s01", "done.json"))
    check("看片意见轮 append：原审片复制一份进 _old；redo_fixed 的 s01 只发用户条目 + 劣质特效扫描（这一版代码干净：只剩用户条目；旧意见、旧硬门禁都不重发），"
          "done.json 原件进 _old、原地留 kind=fix_pre_notes（fix_round 不会当成已修过跳过）；s02 用户条目在前、原问题单照留；没 restore 不重渲",
          rv2.get("source") == "notes+review" and os.path.exists(os.path.join(ep2, "_old", pr2["stamp"], "review_1.json"))
          and a1[0]["rule"].startswith("用户") and not any(x["rule"] in ("审片意见 x", _sync_issue()["rule"]) for x in a1)
          and len(a1) == 1 and a2[0]["rule"].startswith("用户") and a2[-1]["rule"] == "审片意见 x"
          and dd["kind"] == "fix_pre_notes" and dd["pre_notes"]["round"] == 1
          and util.read_json(os.path.join(ep2, "shots", "s01", "_old", pr2["stamp"], "done.json"))["kind"] == "fix"
          and rv2["failed"] == ["s01", "s02"] and not pr2.get("restore"))
    # 11-05 终修：返修失败保留上一版 / 要冻结的镜头，work/out/shot.py 换回出画的那份；恢复重渲按 PSNR 对 cut_r0
    sd = os.path.join(ep2, "shots", "s02")
    good = util.read(os.path.join(sd, "shot_shot.py"))
    d2 = util.read_json(os.path.join(sd, "done.json"))
    util.write_json(os.path.join(sd, "done.json"), dict(d2, code_sha=util.sha256_file(os.path.join(sd, "shot_shot.py"))))
    util.write(os.path.join(sd, "work", "out", "shot.py"), "variant = 9\n# failed fix\n")
    sw = stages.sync_work_code(c2, "s02", "selftest", "st")
    sw2 = stages.sync_work_code(c2, "s02", "selftest", "st2")
    ps = stages.restore_psnr(c2, os.path.join(ep2, "shots", "s02", "render.mp4"), os.path.join(ep2, "film", "cut_r0.mp4"), 40, 80)
    check("终修：work/out/shot.py 和 done.json 对不上时换回出画的那份（原来那份挪进 _old），对得上不动；恢复重渲按 PSNR 对 cut_r0（同一份 = 满分）",
          sw and not sw2 and util.read(os.path.join(sd, "work", "out", "shot.py")) == good
          and "failed fix" in util.read(os.path.join(sd, "_old", "st", "work_out_shot.py"))
          and ps and ps["n"] == 40 and ps["median"] >= 90)


FAKE_CODEX = r'''#!{PY}
# 假 codex（selftest 用，不联网、不花额度）：按同目录 plan.json 依次回放；每次调用的 argv、stdin 记进 calls.jsonl
import json, os, sys, time
D = os.path.dirname(os.path.abspath(__file__))
plan = json.load(open(os.path.join(D, "plan.json")))
a = sys.argv[1:]
if a[:2] == ["login", "status"]:
    print(plan.get("login", "Logged in using ChatGPT")); sys.exit(0)
stdin = sys.stdin.read()
st = os.path.join(D, "state.json")
i = json.load(open(st))["i"] if os.path.exists(st) else 0
json.dump({"i": i + 1}, open(st, "w"))
with open(os.path.join(D, "calls.jsonl"), "a") as f:
    f.write(json.dumps({"argv": a, "stdin": stdin, "cwd": os.getcwd(), "env": sorted(os.environ)}, ensure_ascii=False) + "\n")
step = plan["steps"][min(i, len(plan["steps"]) - 1)]
cwd, last = a[a.index("-C") + 1], a[a.index("-o") + 1]
ev = lambda o: print(json.dumps(o, ensure_ascii=False), flush=True)
ev({"type": "thread.started", "thread_id": "t%d" % i}); ev({"type": "turn.started"})
m = step["mode"]
if step.get("stderr"):
    sys.stderr.write(step["stderr"] + "\n")
for rel, text in (step.get("pre_write") or {}).items():
    p = os.path.join(cwd, rel); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "w").write(text)
if m == "error":
    ev({"type": "error", "message": step["msg"]}); ev({"type": "turn.failed", "error": {"message": step["msg"]}}); sys.exit(1)
for k in range(step.get("items", 1)):
    ev({"type": "item.completed", "item": {"id": "c%d" % k, "type": "command_execution", "command": "ls", "status": "completed"}})
    if step.get("sleep"):
        time.sleep(0.05)
for rel, text in (step.get("write") or {}).items():
    p = os.path.join(cwd, rel); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "w").write(text)
if m == "image":
    import struct, zlib
    def png(p, w, h):
        ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        raw = b"".join(b"\x00" + b"\x80" * (w * 3) for _ in range(h))
        open(p, "wb").write(b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b""))
    for iid in step["ids"]:
        png(os.path.join(cwd, iid + ".png"), 32, 18)
if step.get("sleep"):
    time.sleep(step["sleep"])
text = step.get("text", "done")
open(last, "w").write(text)
ev({"type": "item.completed", "item": {"id": "m", "type": "agent_message", "text": text}})
ev({"type": "turn.completed", "usage": {"input_tokens": 1000, "cached_input_tokens": 600, "output_tokens": 50, "reasoning_output_tokens": 20}})
'''


def codex_ok(tmp, series, cfg):
    """Codex 后端 + 生图通道：命令行、环境、事件流、问答重试、代理轮数守卫、生图请求 / 上限 / 失败只报告、默认（deepseek）不变。
    全用假 codex（plan.json 回放），不联网、不花额度、不读任何 key。"""
    import argparse
    d = os.path.join(tmp, "codex")
    os.makedirs(d)
    fake = os.path.join(d, "codex")
    util.write(fake, FAKE_CODEX.replace("{PY}", sys.executable))
    os.chmod(fake, 0o755)
    plan = lambda steps, **k: util.write_json(os.path.join(d, "plan.json"), dict(k, steps=steps))  # noqa: E731

    def reset():
        for f in ("state.json", "calls.jsonl"):
            if os.path.exists(os.path.join(d, f)):
                os.remove(os.path.join(d, f))
        CX._LOGIN.clear()

    def calls():
        p = os.path.join(d, "calls.jsonl")
        return [json.loads(x) for x in open(p)] if os.path.exists(p) else []

    cc = CX.load_cfg(os.path.join(d, "no-such.json"))
    cc["bin"] = fake
    cc["qa"].update(retry_wait_s=[0], transient_retries=1)
    log = util.Log(tmp, "selftest-codex")

    # 命令行、环境（sandbox_read = "all" 是 11-03 的 -s 写法；默认 "job" 是 11-04 收紧读的权限档）
    cca = dict(cc, sandbox_read="all")
    q = CX.build_cmd(cca, "read-only", "/q", "/q/last.md", images=["/a.jpg", "/b.jpg"])
    w = CX.build_cmd(cca, "workspace-write", "/w", "/w/last.md")
    js = " ".join(q + w)
    check("codex（sandbox_read=all）：每次 exec 都 --ephemeral --ignore-user-config --ignore-rules --json、问答 -s read-only、代理 -s workspace-write + 网络关 + /tmp 不可写；"
          "任务从 stdin（-- -），图 -i 在前；模型和思考档位显式传",
          all(x in q for x in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "--json", "--skip-git-repo-check"))
          and q[q.index("-s") + 1] == "read-only" and w[w.index("-s") + 1] == "workspace-write" and q[-2:] == ["--", "-"]
          and q.index("/b.jpg") < q.index("--") and "sandbox_workspace_write.network_access=false" in w
          and "sandbox_workspace_write.exclude_slash_tmp=true" in w and "sandbox_workspace_write.network_access=false" not in q
          and q[q.index("-m") + 1] == cc["model"] and any(x.startswith("model_reasoning_effort=") for x in q)
          and "multi_agent" in q and "api_key" not in js.lower() and "sk-" not in js)
    dis = lambda c_: any(c_[i] == "--disable" and c_[i + 1] == "image_generation" for i in range(len(c_) - 1))  # noqa: E731
    check("codex：问答和代理关掉内置 image_gen（--disable image_generation：生图只走编排器的通道、受每镜 / 全片上限管）；生图那一次不关",
          dis(q) and dis(w) and not dis(CX.build_cmd(cca, "workspace-write", "/g", "/g/last.md", imagegen=True))
          and dis(CX.build_cmd(cc, "workspace-write", "/g", "/g/last.md")) and not dis(CX.build_cmd(cc, "workspace-write", "/g", "/g/last.md", imagegen=True)))
    qd, wd, rd_, td = (os.path.join(d, x) for x in ("pq", "pw", "pr", "pt"))
    for x in (qd, wd, rd_, os.path.join(wd, "assets", "gen")):
        os.makedirs(x, exist_ok=True)

    def pv(c_):                                                       # 命令行里的权限档（-c permissions.p2={...}）
        i = next((k for k, x in enumerate(c_) if x.startswith("permissions.p2=")), None)
        return c_[i] if i and c_[i - 1] == "-c" else ""

    q2 = CX.build_cmd(cc, "read-only", qd, qd + "/last.md", images=["/a.jpg"])
    rr_ = [rd_, os.path.join(wd, "assets", "gen"), util.HOME, "/"]
    w2_ = CX.build_cmd(cc, "workspace-write", wd, wd + "/last.md", tmpdir=td, reads=rr_)
    fq, fw = CX.perm_fs(cc, read=[qd]), CX.perm_fs(cc, write=[wd, td], read=rr_)
    check("codex（默认 sandbox_read=job）：不传 -s，改用命令行里的权限档 p2（default_permissions）：盘根不可读、系统最小集可读、/tmp 不可读写、网络关；"
          "问答只放行临时目录读；代理 work/ + 临时目录可写、job 的 allow_read 可读（work 里面的、HOME、盘根这种太宽的不收）；codex 自己可读",
          "-s" not in q2 and "-s" not in w2_ and 'default_permissions="p2"' in q2 and 'default_permissions="p2"' in w2_
          and "network={enabled=false}" in pv(w2_) and '":root"="none"' in pv(q2) and '":slash_tmp"="none"' in pv(w2_)
          and json.dumps(wd, ensure_ascii=False) + '="write"' in pv(w2_)
          and fq.get(qd) == "read" and "write" not in fq.values() and fw.get(wd) == "write" and fw.get(td) == "write"
          and fw.get(rd_) == "read" and os.path.join(wd, "assets", "gen") not in fw and util.HOME not in fw and "/" not in fw
          and any(k.endswith(".app") or k == os.path.dirname(os.path.realpath(cc["bin"])) for k in fw)
          and "--add-dir" not in w2_ and not any(x.startswith("sandbox_workspace_write") for x in w2_) and w2_[-2:] == ["--", "-"])
    saved = {k: os.environ.get(k) for k in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_HOME", "DEEPSEEK_API_KEY")}
    for k in saved:
        os.environ[k] = "x-test"
    try:
        env = CX.clean_env(os.path.join(d, "t"))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    check("codex：子进程环境不带任何 key、不带 CODEX_HOME（= 用默认 ~/.codex 的 ChatGPT 登录）",
          not set(saved) & set(env) and env.get("HOME") and env["TMPDIR"].endswith("/t"))
    wt = CX.build_cmd(cca, "workspace-write", "/w", "/w/last.md", tmpdir=os.path.join(d, "t"))
    check("codex 代理（sandbox_read=all）：自己的临时目录（run_dir/tmp）--add-dir 成可写、TMPPREFIX 指进去（zsh here-doc / mktemp 能用）；/tmp 照样排除；问答不加",
          wt[wt.index("--add-dir") + 1] == os.path.join(d, "t") and env["TMPPREFIX"].startswith(env["TMPDIR"])
          and "sandbox_workspace_write.exclude_slash_tmp=true" in wt and "--add-dir" not in q and "--add-dir" not in w)
    ev = CX.Events("/nonexistent")
    for ln in ['{"type":"thread.started","thread_id":"T"}', '{"type":"item.completed","item":{"id":"r","type":"reasoning"}}',
               '{"type":"item.started","item":{"id":"c1","type":"command_execution"}}',
               '{"type":"item.completed","item":{"id":"c1","type":"command_execution"}}',
               '{"type":"item.completed","item":{"id":"m","type":"agent_message","text":"{\\"a\\": 1}"}}',
               '{"type":"turn.completed","usage":{"input_tokens":12145,"cached_input_tokens":9472,"output_tokens":70,"reasoning_output_tokens":5}}']:
        ev.feed(ln)
    check("codex：事件流 → 轮数（非 reasoning 的 item，按 id 去重）、token（未命中 = 输入 − 缓存）、最后一条回复",
          ev.turns() == 2 and ev.usage() == {"in_miss": 2673, "in_hit": 9472, "out": 70, "reasoning": 5} and ev.final == '{"a": 1}'
          and ev.thread == "T")

    # 问答：不合格 → 带上一次输出和原因重试；记 token 不记钱
    sc = util.read_json(os.path.join(util.HERE, "schemas", "review.json"))
    good = {"shots": [{"id": "s01", "pass": True, "score": 4, "issues": []}]}
    reset()
    plan([{"mode": "reply", "text": "不是 JSON"}, {"mode": "reply", "text": "```json\n" + json.dumps(good) + "\n```"}])
    led = CX.Ledger(os.path.join(d, "cost.json"))
    m = CX.CodexLLM(cfg, cc, led, d, log)
    img = os.path.join(d, "x.png")
    IG.placeholder_png(img, 16, 9)
    o, info = m.ask_json("review", "q1", "SYS", "PROMPT", images=[("联系表", img)], schema=sc)
    cl = calls()
    a0 = cl[0]["argv"] if cl else []
    check("codex 问答：第 1 次不合格、第 2 次合格（第 2 次的正文带上一次输出和不合格原因）；图 -i 附上、图注在正文；工作目录是临时空目录且用完删掉",
          o == good and info["attempts"] == 2 and len(cl) == 2 and "你上一次的输出" in cl[1]["stdin"] and "JSON 解析失败" in cl[1]["stdin"]
          and "图 1：联系表" in cl[0]["stdin"] and a0[a0.index("-i") + 1] == img and "-s" not in a0 and 'default_permissions="p2"' in a0
          and json.dumps(cl[0]["cwd"], ensure_ascii=False) + '="read"' in pv(a0) and '="write"' not in pv(a0)
          and not os.path.exists(cl[0]["cwd"]) and "SYS" in cl[0]["stdin"])
    check("codex 记账：token 照记（未命中 400 / 命中 600 / 输出 50 每次），费用 0，标 billing；预算守卫不按美元停",
          led.total == 0 and led.d["tokens"] == {"in_miss": 800, "in_hit": 1200, "out": 100} and led.d.get("billing")
          and led.d["calls"][0].get("backend") == "codex" and led.guard(1e9) is None)
    reset()
    plan([], login="Logged in using an API key")
    try:
        CX.CodexLLM(cfg, cc, led, d, log)._send({"system": "", "prompt": "p", "images": [], "history": []})
        check("codex：不是 ChatGPT 登录 → 停下等你（退出码 4），不发调用", False)
    except util.Stop as e:
        check("codex：不是 ChatGPT 登录 → 停下等你（退出码 4），不发调用", e.code == 4 and not calls())
    reset()
    plan([{"mode": "error", "msg": "You've hit your usage limit"}])
    try:
        CX.CodexLLM(cfg, cc, led, d, log).ask_json("review", "q2", "S", "P", schema=sc)
        check("codex：额度 / 限流 → 重试后停下等你（退出码 4）", False)
    except util.Stop as e:
        check("codex：额度 / 限流 → 重试 1 次后停下等你（退出码 4）", e.code == 4 and len(calls()) == 2)

    # 写代码的代理：-s workspace-write、cwd = work/，轮数守卫
    work = os.path.join(d, "shot", "work")
    os.makedirs(work)
    job = {"stage": "production", "kind": "shot", "name": "s01", "work": work, "run_dir": os.path.join(d, "shot", "runs", "01-shot"),
           "task": "TASK", "expect": ["out/shot.py"], "timeout_s": 60, "max_turns": 9, "allow_read": [rd_]}
    reset()
    plan([{"mode": "agent", "items": 3, "write": {"out/shot.py": "x = 1\n"}, "text": "done -- OK"}])
    r = CX.run_agent(job, cfg, cc, led, log)
    a1 = calls()[0]["argv"]
    check("codex 代理：工作目录 = work/、work 和 run_dir/tmp 可写、job 的 allow_read 可读（权限档），交出 out/shot.py → ok，轮数按事件流数（3 次命令 + 1 条回复）",
          r["status"] == "ok" and r["turns"] == 4 and a1[a1.index("-C") + 1] == work and "-s" not in a1
          and json.dumps(work, ensure_ascii=False) + '="write"' in pv(a1)
          and json.dumps(os.path.join(job["run_dir"], "tmp"), ensure_ascii=False) + '="write"' in pv(a1)
          and json.dumps(rd_, ensure_ascii=False) + '="read"' in pv(a1) and dis(a1)
          and not os.path.exists(os.path.join(job["run_dir"], "tmp"))
          and calls()[0]["cwd"] == work and r["usd"] == 0 and os.path.exists(os.path.join(work, "TASK.md")))
    reset()
    plan([{"mode": "agent", "items": 40, "sleep": 20, "text": "x"}])
    cc2 = json.loads(json.dumps(cc))
    cc2["agent"]["max_turns"]["shot"] = 5
    t0 = util.now()
    r2 = CX.run_agent(dict(job, run_dir=job["run_dir"] + "b"), cfg, cc2, led, log)
    check("codex 代理：超过轮数上限就结束进程（status max_turns，不等它跑完）",
          r2["status"] == "max_turns" and (util.now() - t0).total_seconds() < 15)
    reset()
    plan([{"mode": "error", "msg": "Usage limit reached. You've reached your usage limit. Increase your limits to continue using codex.",
           "pre_write": {"out/shot.py": "x = 2\n"}}])
    try:
        CX.run_agent(dict(job, run_dir=job["run_dir"] + "q"), cfg, cc, led, log)
        q_stop = None
    except util.Stop as e:
        q_stop = e.code
    reset()
    plan([{"mode": "error", "msg": "sandbox error: exec failed", "stderr": "WARN failed to fetch codex rate limits: timeout"}])
    os.remove(os.path.join(work, "out", "shot.py"))
    try:
        r4 = CX.run_agent(dict(job, run_dir=job["run_dir"] + "e"), cfg, cc, led, log)
    except util.Stop:
        r4 = {"status": "stop"}
    check("codex 代理：额度用完（codex 原文 Usage limit reached，交了半截也算）→ 停下等你（退出码 4）；别的报错、stderr 里只是「rate limits」日志 → 不当额度，照常返回 error",
          q_stop == 4 and r4["status"] == "error" and CX.is_quota("Your workspace is out of credits. Add credits to continue using Codex.")
          and CX.is_quota('{"code":"usage_limit_reached"}') and not CX.is_quota("tokens 14290, done"))

    # 生图通道
    ep = os.path.join(d, "ep")
    os.makedirs(os.path.join(ep, "inputs"))
    led3 = CX.Ledger(os.path.join(ep, "cost.json"))

    class R:
        root = os.path.join(ep, "engine")
        n = 0

        def setup(self, *a):
            R.n += 1

        def footage_dirs(self):
            return []

    args = argparse.Namespace(backend="codex", imagegen="auto")
    c = stages.Ctx(ep, series, cfg, args, log, led3, None, R(), False)
    c.backend, c.cc = "codex", json.loads(json.dumps(cc))
    c.cc["imagegen"].update(per_shot=2, per_film=3)
    w2 = os.path.join(ep, "shots", "s02", "work")
    os.makedirs(os.path.join(w2, "out"))
    util.write_json(os.path.join(w2, "out", "images.json"), {"images": [
        {"id": "Night Sky!", "use": "远景", "ratio": "16:9", "prompt": "城市夜景远景"}, {"id": "tex", "use": "怪", "ratio": "5:4", "prompt": "灯管纹理"},
        {"id": "third", "use": "背景", "prompt": "多出来的"}, {"id": "nop", "use": "背景"}]})
    reqs, notes = IG.parse_requests(w2)
    todo, n2 = IG.pending(c, w2, reqs)
    check("生图请求：id 规范成小写下划线、不认识的用途 / 比例换默认并提醒、没写 prompt 跳过；每镜上限截断（2 张）",
          [x["id"] for x in reqs] == ["night_sky", "tex", "third"] and reqs[1]["use"] == "背景" and reqs[1]["ratio"] == "16:9"
          and len(notes) == 3 and [x["id"] for x in todo] == ["night_sky", "tex"] and any("每镜最多 2 张" in x for x in n2))
    reset()
    plan([{"mode": "image", "ids": ["night_sky"], "text": '{"images": [{"id": "tex", "ok": false, "note": "工具拒绝"}]}'}])
    reruns = []

    def rerun(j):
        reruns.append(j)
        return {"status": "ok", "turns": 1}

    util.write(os.path.join(w2, "out", "shot.py"), "x = 1\n")
    job2 = dict(job, name="s02", work=w2, run_dir=os.path.join(ep, "shots", "s02", "runs", "01-shot"), imagegen=True)
    res = IG.after_agent(c, job2, {"status": "ok"}, rerun)
    man = IG.manifest(w2)
    reg = util.read_json(os.path.join(ep, "inputs", "gen_images.json"))
    st = {x["id"]: x["status"] for x in man["images"]}
    a2 = calls()[0]["argv"] if calls() else []
    check("生图：一次 codex exec（workspace-write，cwd = work/assets/gen，任务书有「不要麻麻赖赖」、不出现人和字）；成功的进 manifest、"
          "全片登记、引擎素材目录（刷新了）；失败的只报告；图到了再叫一次代理（任务书带「生图到了」和 Take 名字）",
          st == {"night_sky": "ok", "tex": "failed"} and a2[a2.index("-C") + 1] == os.path.join(w2, "assets", "gen") and not dis(a2)
          and json.dumps(os.path.join(w2, "assets", "gen"), ensure_ascii=False) + '="write"' in pv(a2)
          and (not os.path.isdir(CX.GEN_IMAGES) or json.dumps(CX.GEN_IMAGES, ensure_ascii=False) + '="read"' in pv(a2))
          and "麻麻赖赖" in calls()[0]["stdin"] and "不出现任何人" in calls()[0]["stdin"]
          and [x["name"] for x in reg["clips"]] == ["s02_night_sky"] and reg["clips"][0]["w"] == 32 and len(reg["failed"]) == 1
          and R.n == 1 and len(reruns) == 1 and reruns[0]["gen_followup"] and "生图到了" in reruns[0]["task"]
          and "Take('s02_night_sky'" in reruns[0]["task"] and res["status"] == "ok" and res.get("gen") == {"ok": 1, "failed": 1}
          and "工具拒绝" in [x for x in man["images"] if x["id"] == "tex"][0]["err"])
    reset()
    plan([{"mode": "image", "ids": []}])
    res2 = IG.after_agent(c, job2, {"status": "ok"}, rerun)
    util.write_json(os.path.join(w2, "out", "images.json"), {"images": [{"id": "night_sky", "use": "远景", "prompt": "城市夜景远景，换个角度"},
                                                                        {"id": "tex", "use": "背景", "prompt": "灯管纹理"}]})
    todo3, n3 = IG.pending(c, w2, IG.parse_requests(w2)[0])
    check("生图：同样的请求成功过不重生、失败过不重试（没有新调用、不再叫代理）；改了 prompt 才重生；全片上限按次数截断（3 次）",
          not calls() and len(reruns) == 1 and res2 == {"status": "ok"} and [x["id"] for x in todo3] == ["night_sky"]
          and not any("全片" in x for x in n3) and IG.used(c) == 2)
    c.cc["imagegen"]["per_film"] = 2
    check("生图：全片已用满就不再生成", IG.pending(c, w2, IG.parse_requests(w2)[0])[0] == []
          and any("全片最多 2 张" in x for x in IG.pending(c, w2, IG.parse_requests(w2)[0])[1]))
    eng = render.ChromaEngine(ep, series, log)
    check("生图：引擎素材目录并进生图（文件在的才收），沙箱可读目录带上生图目录",
          [x["name"] for x in eng.gen_clips()] == ["s02_night_sky"] and os.path.join(w2, "assets", "gen") in eng.footage_dirs())
    check("生图：mock 占位图和读尺寸（PNG 头）", IG.img_size(img) == (16, 9))
    import shutil
    gh, gd = os.path.join(d, "home"), os.path.join(d, "gd")
    th = os.path.join(gh, "generated_images", "T1")
    os.makedirs(th)
    os.makedirs(gd)
    IG.placeholder_png(os.path.join(th, "a.png"), 8, 8, seed=1)
    IG.placeholder_png(os.path.join(th, "b.png"), 8, 8, seed=2)
    shutil.copy2(os.path.join(th, "a.png"), os.path.join(gd, "one.png"))
    n_bad = IG.rescue(gd, [{"id": "one"}, {"id": "two"}, {"id": "three"}], "T1", home=gh)
    n_ok = IG.rescue(gd, [{"id": "one"}, {"id": "two"}], "T1", home=gh)
    check("生图：Codex 没把图拷进来时，从这一次 thread 的 generated_images 补拷（没拷过的正好和缺的一样多才补；对不上不动）",
          n_bad == 0 and n_ok == 1 and os.path.exists(os.path.join(gd, "two.png")) and not os.path.exists(os.path.join(gd, "three.png")))

    # 默认（deepseek，不传新参数）：任务书、代理调用、环境说明都和以前一样
    c0 = stages.Ctx(ep, series, cfg, argparse.Namespace(), log, led3, None, render.FakeRenderer(ep, series, log), True)
    j0 = stages.agent_job(c0, "shot", "s02", os.path.join(ep, "shots", "s02"), "原任务书", ["out/shot.py"])
    c.cc["imagegen"]["per_film"] = 40
    c1 = stages.Ctx(ep, series, cfg, args, log, led3, None, render.FakeRenderer(ep, series, log), True)
    c1.backend, c1.cc = "codex", c.cc
    j1 = stages.agent_job(c1, "shot", "s02", os.path.join(ep, "shots", "s02"), "原任务书", ["out/shot.py"])
    env0 = {"read": True, "hints": {"image_gen": "codex"}, "t": "x"}
    check("默认 deepseek：代理任务书原样、不开生图、环境说明不变；codex：任务书加「生图」一节和 Codex 运行环境",
          j0["task"] == "原任务书" and "imagegen" not in j0 and not IG.enabled(c0) and CX.backend(c0) == "deepseek"
          and stages.env_text(env0) == stages.env_text(env0, IG.mode(c0)) and "已接上" in stages.env_text(env0, "codex")
          and j1["task"].startswith("原任务书") and "## 生图" in j1["task"] and "运行环境（Codex" in j1["task"] and j1["imagegen"]
          and "Take('s02_night_sky'" in j1["task"])
    ok_tpl = True
    for f in sorted(os.listdir(CX.PROMPTS)):
        raw = util.read(os.path.join(CX.PROMPTS, f))
        try:
            out = CX.prompt_file(f[:-3], **{k: "x" for k in set(__import__("re").findall(r"\{\{([A-Z_0-9]+)\}\}", raw))})
            ok_tpl = ok_tpl and "{{" not in out
        except KeyError:
            ok_tpl = False
    check(f"codex 提示词：prompts_codex/ 里 {len(os.listdir(CX.PROMPTS))} 个模板都渲得出来（prompts/ 里的没动）", ok_tpl)


def stock_ok(tmp, series, cfg, log):
    """11-05 第四件事：素材请求通道（代理写 out/footage_requests.json → 编排器在沙箱外找、下、入库 → 再叫代理接进去）和开拍前素材盘点。
    stock.py 换成假的（不联网、不起子进程），最后一条用真的 stock.py 走一遍 STOCK_FAKE 假站点（子进程、复制一条小素材）。"""
    import argparse
    from lib import stockreq as SQ
    saved_off = os.environ.pop("STOCK_OFF", None)
    ep = os.path.join(tmp, "stock_ep")
    for d in ("inputs", "logs", "shots/s02/work/out", "shots/s02/runs"):
        os.makedirs(os.path.join(ep, d), exist_ok=True)
    util.write_json(os.path.join(ep, "topic.json"), {"title_cn": "水下试", "motif": "水下测试", "episode": 5, "category_id": "beat",
                                                     "signature_color": {"name": "青", "hex": "#2FD8FF"}})
    util.write_json(os.path.join(ep, "inputs", "footage.json"), {"auto": True, "clips": []})
    led = CX.Ledger(os.path.join(ep, "cost.json"))

    class R:
        root = os.path.join(ep, "engine")
        n = 0
        py = None

        def setup(self, *a):
            R.n += 1

        def footage_dirs(self):
            fj = util.read_json(os.path.join(ep, "inputs", "footage.json"), {"clips": []})
            return sorted({os.path.dirname(x["file"]) for x in fj.get("clips", [])})

    ser = json.loads(json.dumps(series))
    ser["stock"].update(per_shot=2, per_film=3, inventory_max=4)
    c = stages.Ctx(ep, ser, cfg, argparse.Namespace(), log, led, None, R(), True)
    c.backend, c.cc = "deepseek", None
    calls, seq = [], iter(range(1, 1000))

    def fake_call(c_, fn, *a, **kw):
        calls.append((fn, a, kw))
        if fn == "search":
            q, k = a[0], next(seq)
            if "nothing" in q:
                return [], None
            base = dict(site="fake", title=f"{q} black background", fps=25.0, dur=10.0, license="Pexels License：免费商用", stype_guess="实拍")
            return [dict(base, id="lo", url="file://lo", page="https://fake/lo", w=640, h=360),
                    dict(base, id="nc", url="file://nc", page="https://fake/nc", w=1920, h=1080, license="CC BY-NC 4.0"),
                    dict(base, id="cg", url="file://cg", page="https://fake/cg", w=1920, h=1080, stype_guess="CG"),
                    dict(base, id=f"hd{k}", url=f"file://hd{k}", page=f"https://fake/{q}", w=1920, h=1080)], None
        if fn == "fetch":
            it, pack = a[0], a[1]
            if FAIL.get("fetch"):
                return None, FAIL["fetch"]
            name = f"fake-{it['id']}.mp4"
            path = os.path.join(pack, "footage", name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(b"\0" * 64)
            sj = util.read_json(os.path.join(pack, "sources.json"), {"items": []})
            ent = {"file": f"footage/{name}", "content": it["title"], "use": kw.get("use"), "w": 1920, "h": 1080, "fps": 25.0, "dur": 9.0,
                   "license": it["license"], "page_url": it["page"], "stype": kw.get("stype") or "实拍"}
            sj["items"].append(ent)
            util.write_json(os.path.join(pack, "sources.json"), sj)
            return {"path": path, "name": name, "source": ent}, None
        if fn == "bake_overlay":                                    # 照 stock.py 的约定：自动通道过了验收才并进表
            pack, name, kind, win = a
            cov = 0.9 if kind.startswith("ink") else 0.05
            ok = cov <= 0.5
            if ok:
                ov = util.read_json(os.path.join(pack, "overlays.json"), {"kinds": {}})
                ov["kinds"].setdefault(kind, {"what": win.get("what"), "wins": []})["wins"].append(dict(win, file=name, by=kw.get("by")))
                util.write_json(os.path.join(pack, "overlays.json"), ov)
            return dict(win, file=name, by=kw.get("by"), _bake={"rc": 0, "sheet_rc": 0, "stats": {"cov": cov}, "ok": ok, "merged": ok,
                                                               "pos_ok": kind != "marine_snow", "why": "" if ok else "去背景没去干净（像贴图）"}), None
        if fn == "drop_overlay":
            pack, kind = a[0], a[1]
            ov = util.read_json(os.path.join(pack, "overlays.json"), {"kinds": {}})
            ov["kinds"].pop(kind, None)
            util.write_json(os.path.join(pack, "overlays.json"), ov)
            return ov, None
        return None, "unknown"

    FAIL = {}
    real_call = SQ.call
    SQ.call = fake_call
    try:
        w2 = os.path.join(ep, "shots", "s02", "work")
        util.write_json(os.path.join(w2, "out", "footage_requests.json"), [
            {"need": "黑底上升的气泡，慢动作", "use": "叠层", "seconds": 40, "why": "主角身后冒泡", "kind": "Bubbles!"},
            {"need": "深色水体里漂移的悬浮颗粒", "use": "怪", "query": "particles underwater dark"},
            {"need": "黑底上升的气泡，慢动作", "use": "叠层", "seconds": 40, "why": "重复", "kind": "Bubbles!"},
            {"use": "world"}, {"need": "多出来的一条", "use": "world"}])
        reqs, notes = SQ.parse_requests(w2)
        todo, n2, cap = SQ.pending(c, w2, reqs)
        check("素材请求：use 别名（叠层 → overlay）、不认识的按 world 并提醒、seconds 夹到 30、kind 规范成小写下划线、重复和没写 need 的跳过；"
              "每镜上限截断（2 条），截掉的那条另外返回（要登记进报告）",
              [r["use"] for r in reqs] == ["overlay", "world", "world"] and reqs[0]["seconds"] == 30.0 and reqs[0]["kind"] == "bubbles"
              and len(notes) == 3 and [r["id"] for r in todo] == ["r1", "r2"] and any("每镜最多 2 条" in x for x in n2)
              and [r["id"] for r in cap] == ["r3"])
        rk = SQ.rank(fake_call(c, "search", "x")[0], dict(reqs[0]))
        check("素材请求：候选只收免费授权（CC BY-NC 不要）、1080p 横屏优先、CG 往后排、低分辨率垫底",
              [x["id"] for x in rk][:1] == ["hd1"] and "nc" not in [x["id"] for x in rk] and [x["id"] for x in rk][-1] == "lo"
              and SQ.lic_ok({"license": "CC BY 4.0；要署名"}) and not SQ.lic_ok({"license": "CC BY-SA 4.0"}) and not SQ.lic_ok({}))
        px = dict(site="pexels", license="Pexels License", fps=30.0, dur=10.0, title="bubbles black background")
        rk2 = SQ.rank([dict(px, id="4k", w=3840, h=2160, url="https://videos.pexels.com/video-files/1/13977647_3840_2160_60fps.mp4"),
                       dict(px, id="uhd", w=3840, h=2160, url="https://videos.pexels.com/video-files/2/2-uhd_3840_2160_30fps.mp4"),
                       dict(site="mixkit", id="mk", license="Mixkit Free", title="bubbles black background", url="https://assets.mixkit.co/active_storage/x.mp4", dur=10),
                       dict(site="nasa", id="n1", license="NASA 媒体使用指南", title="bubbles", url="", dur=10)], dict(reqs[0]))
        check("素材请求：换得了 1080p 的老文件名排在新文件名 4K（整条下 4K）前面；Mixkit 只有 720p 的往后排；NASA 没直链也照收",
              [x["id"] for x in rk2][:1] == ["uhd"] and [x["id"] for x in rk2].index("uhd") < [x["id"] for x in rk2].index("4k")
              and "n1" in [x["id"] for x in rk2])
        calls.clear()
        reruns, refreshed = [], []

        def rerun(j):
            reruns.append(j)
            return {"status": "ok", "turns": 1}

        util.write(os.path.join(w2, "out", "shot.py"), "x = 1\n")
        job = {"kind": "shot", "name": "s02", "work": w2, "run_dir": os.path.join(ep, "shots", "s02", "runs", "01-shot"), "task": "原任务书",
               "expect": ["out/shot.py"], "allow_read": ["/eng"]}
        res = SQ.after_agent(c, job, {"status": "ok"}, rerun, refresh=lambda j: refreshed.append(j["name"]))
        man, reg = SQ.manifest(w2), SQ.film(c)
        fj = util.read_json(os.path.join(ep, "inputs", "footage.json"))
        pk = os.path.join(ep, "stock_pack")
        ovk = (util.read_json(os.path.join(pk, "overlays.json")) or {}).get("kinds") or {}
        names = [x["name"] for x in fj["clips"]]
        t2 = reruns[0]["task"] if reruns else ""
        check("素材请求：编排器搜 → 挑 → 下进本集素材包（mock 不碰真素材包）→ 叠层烘焙进 overlays.json → 接进素材清单（F01、F02）、引擎素材目录刷新；"
              "登记全片 inputs/stock.json 和这一镜 manifest；刷新 ctx 后再叫一次代理（任务书带「素材到了」、Take 名字、SR.element 的新 kind、"
              "沙箱放行新素材目录）",
              [f for f, *_ in calls].count("search") == 2 and [f for f, *_ in calls].count("fetch") == 2 and names == ["F01", "F02"]
              and "bubbles" in ovk and man["rounds"] == 1 and len([x for x in man["requests"] if x["status"] != "skipped_cap"]) == 2
              and [x["status"] for x in reg["requests"]] == ["skipped_cap", "ok", "ok"] and reg["requests"][1]["overlay"]["ok"]
              and calls[1][2].get("stype") == "元素（待烘焙）" and R.n >= 1 and refreshed == ["s02"] and len(reruns) == 1
              and reruns[0]["stock_followup"] and "素材到了" in t2 and "Take('F02'" in t2 and "SR.element(E, T, img, 'bubbles'" in t2
              and os.path.join(pk, "footage") in reruns[0]["allow_read"] and res.get("stock") == {"ok": 2, "failed": 0}
              and fj["clips"][0]["license"].startswith("Pexels"))
        util.write_json(os.path.join(w2, "out", "footage_requests.json"), [{"need": "换一条：黑底上的墨团", "use": "overlay", "kind": "ink"}])
        todo2, n3, _ = SQ.pending(c, w2, SQ.parse_requests(w2)[0])
        res2 = SQ.after_agent(c, job, {"status": "ok"}, rerun)
        asr = ser.setdefault("assets", {})
        keep_r, asr["rounds_per_shot"] = asr.get("rounds_per_shot"), 1     # 轮数到顶的那条路也测一下（默认 3）
        todo3, n4, _ = SQ.pending(c, w2, SQ.parse_requests(w2)[0])
        asr["rounds_per_shot"] = keep_r
        check("素材请求：改写的 need 是新需求，但这一阶段每镜条数到顶（per_shot 2 条）时只提醒、不找、不再叫代理；"
              "assets.rounds_per_shot = 1 时按轮数截住；代理接素材那次不再触发",
              todo2 == [] and any("每镜最多 2 条" in x for x in n3) and todo3 == [] and any("已经找过 1 次素材" in x for x in n4)
              and res2 == {"status": "ok"} and len(reruns) == 1
              and SQ.after_agent(c, dict(job, stock_followup=True), {"status": "ok"}, rerun) == {"status": "ok"})
        ok_b, why_b = SQ._ov_ok({"src": 0, "_bake": {"rc": 0, "sheet_rc": 0, "ok": False, "merged": False, "why": "去背景没去干净（像贴图）",
                                                     "stats": {"cov": 0.8}}})
        good = {"rc": 0, "sheet_rc": 0, "ok": True, "merged": True, "stats": {"cov": 0.03}}
        check("叠层自动验收：stock.py 判不过的（像贴图 / 短划线 / 大片平的）不收，烘焙失败不收，检查图出不来不收（S3），没量到覆盖率不收，"
              "STOCK_FAKE 不烘照收",
              not ok_b and "像贴图" in why_b and not SQ._ov_ok({"_bake": {"rc": 1, "err": "x"}})[0]
              and not SQ._ov_ok({"_bake": dict(good, sheet_rc=1)})[0] and not SQ._ov_ok({"_bake": dict(good, stats={})})[0]
              and not SQ._ov_ok({"_bake": {"rc": 0, "stats": {"cov": 0.03}}})[0]
              and SQ._ov_ok({"_bake": {"skipped": "STOCK_FAKE"}})[0] and SQ._ov_ok({"_bake": good})[0])
        # 自动通道不往人工整理过的 kind 里加；S2：素材包里只写了 /video/<id>/ 来源页的条目也认得出是同一条（直接复用）
        pkk = os.path.join(tmp, "stock_s1_pack")                     # 另一个素材包（别混进这一集的清单）
        os.makedirs(os.path.join(pkk, "footage"), exist_ok=True)
        ov0 = util.read_json(os.path.join(pkk, "overlays.json")) or {"kinds": {}}
        ov0["kinds"]["jellies"] = {"what": "人工整理过的", "wins": [{"file": "x.mp4", "src": 0.5, "c": [1, 2], "lo": 0.04}]}
        util.write_json(os.path.join(pkk, "overlays.json"), ov0)
        rj = SQ.norm_request({"need": "黑底发光水母", "use": "overlay", "kind": "jellies"}, 0)[0]
        kn = SQ.overlay_kind_name(c, rj, pkk)
        sj = util.read_json(os.path.join(pkk, "sources.json"), {"items": []})
        util.write(os.path.join(pkk, "footage", "pexels-123.mp4"), "x")
        sj["items"].append({"file": "footage/pexels-123.mp4", "page_url": "https://www.pexels.com/video/123/", "use": "人工写的"})
        util.write_json(os.path.join(pkk, "sources.json"), sj)
        idx = SQ._pack_index(pkk)
        check("素材请求（11-05 复核 S1 / S2）：叠层表里已有人工整理过的 jellies → 自动入库换新名 jellies_r<sha>，不碰原窗口；"
              "素材包里只写了 /video/<id>/ 的条目按 pexels:<id> 认出来（直接复用，不调 fetch 改登记）",
              kn.startswith("jellies_r") and kn != "jellies" and "pexels:123" in idx
              and SQ.site_keys({"page_url": "https://www.pexels.com/video/light-reflections-on-sea-1000002/"}) == {"pexels:1000002"}
              and SQ.site_keys({"file": "footage/local_test-b_E1_04_pexels-1000002.mp4"}) == {"pexels:1000002"})

        # 开拍前盘点：水下测试（叠层表里只有刚才的 bubbles）；s03 写了海雪、墨团（墨团烘出来覆盖 90 %，自动验收不收、撤下），s04 导演写了 footage_wish
        shots = [{"id": "s03", "look": "footage", "subject": "水下", "subject_src": {"kind": "实拍", "file": "F01"}, "action": "漂",
                  "event": "主角身后海雪单向漂过，气泡往上冒，墨团扬起", "image": "水下", "parts": ["element"], "footage": "F01"},
                 {"id": "s04", "look": "footage", "subject": "鱼", "subject_src": {"kind": "生图", "gen": "一群发光的鱼"}, "action": "游", "event": "跟拍",
                  "image": "鱼群", "parts": ["track"], "footage": "none",
                  "footage_wish": [{"need": "夜里一排霓虹招牌从身后流过", "use": "world", "seconds": 4, "query": "neon signs night street"}]},
                 {"id": "s05", "look": "design", "subject": "片名", "subject_src": {"kind": "生图", "gen": "片名海报"}, "action": "砸入", "event": "海报",
                  "image": "海报", "parts": ["type"], "footage": "none"}]
        sl = {"total_f": 300, "shots": shots}
        calls.clear()
        inv = SQ.inventory(c, sl)
        sh = inv["shots"]
        ovk = (util.read_json(os.path.join(pk, "overlays.json")) or {}).get("kinds") or {}
        check("开拍前盘点：海雪 / 墨团叠层表里没有 → 自动去找（同一种只找一次）；气泡已有不找；s04 的 footage_wish 和缺世界层各找一条；"
              "墨团烘出来像贴图 → 不收、从叠层表撤下；片尾海报不要世界层；结果写 stock_inventory.json",
              {o["kind"]: bool(o["have"]) for o in sh["s03"]["overlays"]} == {"marine_snow": False, "bubbles": True, "ink": False}
              and sh["s04"]["world"] == "缺" and sh["s05"]["world"] == "不需要" and inv["gaps"] == 4
              and [f for f, *_ in calls].count("fetch") == 4 and "marine_snow" in ovk and "ink" not in ovk
              and len(inv["failed"]) == 0 and any("ink" == (e.get("overlay") or {}).get("kind") and not e["overlay"]["ok"]
                                                  for e in SQ.film(c)["requests"])
              and os.path.exists(os.path.join(ep, SQ.INV)) and [e["use"] for e in sh["s04"]["found"]] == ["world", "world"]
              and [e["name"] for e in sh["s04"]["found"]] == ["F03", "F04"] and len({x["name"] for x in SQ.film(c)["requests"] if x.get("name")}) == 6)
        n_fetch = len(calls)
        inv2 = SQ.inventory(c, sl)
        check("开拍前盘点：镜头表没变就不再盘点（按指纹）", inv2["sha"] == inv["sha"] and len(calls) == n_fetch)
        wk = os.path.join(ep, "shots", "s04", "work")
        fj, fmo, asg, ov = stages.write_footage_ctx(c, wk, shots[1], "水下测试")
        md = util.read(os.path.join(wk, "ctx", "footage.md"))
        check("开拍前盘点：补到的素材标进这一镜的 ctx/footage.md（「盘点补的世界层」），ctx/overlays.json 并进本集素材包的叠层，ctx/stock.json 列出已到的",
              any(u == "盘点补的世界层" for _, u in asg) and "盘点补的世界层" in md and "marine_snow" in (ov or {}).get("kinds", {})
              and util.read_json(os.path.join(wk, "ctx", "stock.json"))["arrived"])
        rl = SQ.report_line(c)
        check("交付报告：「素材请求」一行写盘点缺几处、补到几条、代理请求几条、新叠层", "开拍前盘点缺 4 处" in rl and "代理请求 2 条" in rl
              and "bubbles" in rl)
        # S5：返修阶段另算一轮；S7：限流（RateLimited）记 deferred、不占轮数、下一次再试
        util.write_json(os.path.join(w2, "out", "footage_requests.json"), [{"need": "暗色海面慢慢起伏", "use": "world", "query": "dark ocean"}])
        FAIL["fetch"] = "RateLimited: upload.wikimedia.org 限流（Retry-After 600 s），过后再下"
        n_re = len(reruns)
        jf = dict(job, kind="fix", run_dir=os.path.join(ep, "shots", "s02", "runs", "02-fix"))
        SQ.after_agent(c, jf, {"status": "ok"}, rerun)
        m_d = SQ.manifest(w2)
        st_d = [x["status"] for x in SQ.film(c)["requests"] if x.get("need") == "暗色海面慢慢起伏"]
        FAIL.clear()
        SQ.after_agent(c, jf, {"status": "ok"}, rerun)
        m_ok = SQ.manifest(w2)
        st_ok = [x["status"] for x in SQ.film(c)["requests"] if x.get("need") == "暗色海面慢慢起伏"]
        check("素材请求（11-05 复核 S5 / S7）：制作时找过一次，返修时还能再提一次（轮数按制作 / 返修分开算）；站点限流记 deferred、不算一轮、"
              "下一次照样去找，找到后登记里那条 deferred 换成 ok",
              st_d == ["deferred"] and (m_d.get("rounds_by") or {}).get("fix", 0) == 0 and st_ok == ["ok"]
              and (m_ok.get("rounds_by") or {}).get("fix") == 1 and (m_ok.get("rounds_by") or {}).get("make") == 1 and len(reruns) == n_re + 1)
        rl0 = SQ.report_line(c)
        check("素材请求：超上限没找的登记成 skipped_cap，交付报告那一行列出来", "超上限没找" in rl0
              and any(x.get("status") == "skipped_cap" for x in SQ.film(c)["requests"]))
        j1 = stages.agent_job(c, "shot", "s02", os.path.join(ep, "shots", "s02"), "原任务书", ["out/shot.py"])
        jk = stages.agent_job(c, "keyframe", "s02", os.path.join(ep, "shots", "s02"), "原任务书", ["out/key.py"])
        os.environ["STOCK_OFF"] = "1"
        j0 = stages.agent_job(c, "shot", "s02", os.path.join(ep, "shots", "s02"), "原任务书", ["out/shot.py"])
        os.environ.pop("STOCK_OFF")
        env0 = {"read": True, "hints": {"footage_download": True, "footage_sources": ["pexels"]}, "t": "x"}
        check("任务书：制作 / 返修代理加「缺真材料就提请求」一节（格式、上限、叠层表现有种类、这一镜已到的素材），关键帧不加；STOCK_OFF=1 原样；"
              "镜头表的制作环境说「素材请求通道已接上」、可以写 footage_wish",
              "## 缺真材料就提请求" in j1["task"] and "bubbles" in j1["task"] and "Take('F02'" in j1["task"] and j1.get("stock")
              and "footage_requests" not in jk["task"] and j0["task"] == "原任务书" and "stock" not in j0
              and "footage_wish" in stages.env_text(env0, None, "net") and stages.env_text(env0) == stages.env_text(env0, None, "off"))
    finally:
        SQ.call = real_call
    # 真的 stock.py：STOCK_FAKE 假站点（子进程、引擎 Python、不联网），搜 → 下一条小素材到本集素材包
    if os.path.exists(SQ.STOCK_PY):
        items, err = SQ.call(c, "search", "bubbles rising black background", kind="video", n=2)
        f, err2 = SQ.call(c, "fetch", items[0], os.path.join(ep, "stock_pack"), use="世界层：测试", note="selftest") if items else (None, err)
        check("素材请求：真的 stock.py 在 STOCK_FAKE 下走子进程（假站点、不联网），搜得到、下得下来（复制一条小素材进本集素材包）",
              bool(items) and items[0].get("site") == "fake" and bool(f) and os.path.isfile(f["path"])
              and f["path"].startswith(os.path.join(ep, "stock_pack")))
        pk2 = os.path.join(ep, "stock_pack")
        ovp = os.path.join(pk2, "overlays.json")
        ov = util.read_json(ovp) or {"kinds": {}}
        ov.setdefault("kinds", {})["fogx"] = {"what": "人工整理过的", "wins": [{"file": "a.mp4", "src": 0.0}]}
        util.write_json(ovp, ov)
        b1, e1 = SQ.call(c, "bake_overlay", pk2, f["name"], "zz_auto", {"src": 0.5, "dur": 2.0}, by="stockreq") if f else (None, "没下到")
        b2, e2 = SQ.call(c, "bake_overlay", pk2, f["name"], "fogx", {"src": 0.0, "dur": 2.0}, by="stockreq") if f else (None, "没下到")
        ov2 = util.read_json(ovp) or {"kinds": {}}
        check("叠层（真 stock.py、STOCK_FAKE）：自动通道（by=stockreq）的新 kind 并进表、窗口带 by；往人工整理过的 kind 里加 → "
              "ValueError（编排器拿到「ValueError: …」），原窗口一个字没动",
              bool(b1) and (b1.get("_bake") or {}).get("merged") and ov2["kinds"]["zz_auto"]["wins"][0].get("by") == "stockreq"
              and bool(e2) and e2.startswith("ValueError") and ov2["kinds"]["fogx"]["wins"] == [{"file": "a.mp4", "src": 0.0}])
    else:
        check("素材请求：没有 p2/tools/stock.py（F 还没交），子进程这一条跳过", True)
    mf = os.path.join(ep, "inputs", "song.mp3")
    util.write(mf, "x")
    mp = os.path.join(ep, "inputs", "song.music_map.json")
    gg = []
    for mm in ({"drop": {"F": 397, "jump_db": 8.2}, "thr": {"drop_db": 8.0}}, {"drop": {"F": 397, "jump_db": 20.6}}, {"drop": {"F": 397, "jump_db": 8.1}}):
        util.write_json(mp, mm)
        util.write_json(os.path.join(ep, "inputs", "music.json"), {"file": mf, "start_s": 1.0, "music_map": mp})
        gg.append(stages.sync_cfg(c))
    os.remove(os.path.join(ep, "inputs", "music.json"))
    check("门禁实测卡点：打击点表按 8 dB 建的（thr.drop_db = 8），门禁现场量 DROP 也用 8 dB；DROP 跳 20.6 dB 的门槛不变；"
          "没写 thr 但 DROP 只跳 8.1 dB 的按 6.6 dB",
          gg[0].get("drop_db") == 8.0 and "drop_db" not in gg[1] and gg[2].get("drop_db") == 6.6)
    os.environ["STOCK_OFF"] = saved_off if saved_off is not None else "1"


def stage7_ok(tmp, series, cfg, log, led):
    """11-06：代理自己看画面（ctx/look/self、Codex -i）、示例进 ctx
    不调模型。"""
    import re
    ep = os.path.join(tmp, "s7_ep")
    for d in ("inputs", "logs", "film", "frames"):
        os.makedirs(os.path.join(ep, d), exist_ok=True)
    tp = {"title_cn": "测", "motif": "霓虹", "signature_color": {"name": "霓虹粉", "hex": "#FF3BD4"}, "number_ladder": [], "episode": 4}
    util.write_json(os.path.join(ep, "topic.json"), tp)
    util.write_json(os.path.join(ep, "inputs", "footage.json"), {"clips": [
        {"name": "F01", "file": "/x/F01.mp4", "dur": 5, "w": 1920, "h": 1080, "fps": 60},
        {"name": "F02", "file": "/x/F02.mp4", "dur": 5, "w": 1920, "h": 1080, "fps": 60}]})
    s2 = {"id": "s02", "section": "S2", "start_f": 120, "end_f": 240, "cuts_f": [150, 180], "look": "footage", "subject": "灯管",
          "subject_src": {"kind": "生图", "gen": "霓虹灯管"}, "hits": [{"F": 150, "kind": "big", "what": "砸"}], "hero_word": "NEON", "words": ["NEON"],
          "footage": "F02", "transition_in": {}, "transition_out": {}, "parts": []}
    sl = {"total_f": 360, "shots": [dict(s2, id="s01", section="S1", start_f=0, end_f=120, cuts_f=[60], hits=[]), s2,
                                    dict(s2, id="s03", section="S3", start_f=240, end_f=360, cuts_f=[300], hits=[])]}
    util.write_json(os.path.join(ep, "shotlist.json"), sl)

    class RR:
        name, root, py, mock = "fake", os.path.join(ep, "engine"), None, True

        def footage_dirs(self):
            return []
    c = stages.Ctx(ep, series, cfg, None, log, led, None, RR(), True)

    sp = os.path.join(ep, "spec.json")
    util.write_json(sp, {"id": "s02", "start_f": 120, "end_f": 240, "hits": []})


    # #11 API 签名表：签名、转场窗口、模板键；白名单和源码对不上的单列（11-06 对齐后只剩有意不开放的内部键）
    import importlib.util
    spec_ = importlib.util.spec_from_file_location("apisig", os.path.join(util.P2, "tools", "apisig.py"))
    AS = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(AS)
    src = os.path.join(util.PARENT, series["engine"]["src"])
    md = AS.build(src)
    hb = util.read(os.path.join(util.PARENT, "yezi-ve", "p2", "chroma", "HANDBOOK.md"))
    md2 = AS.build(src, hb)
    check("API 索引（11-06 复核 G1）：给了手册原文就只列手册点过名的函数（SR.track 在，手册没提的 SR.sparks 不在），"
          "手册写「不对镜头开放」「别用」的 SR.particles、SR.overlay 不列；只有签名（源码文档、"
          "出处注 IGNITION 都不输出）；≤ 7k 字",
          "`SR.track(" in md2 and "`SR.sparks(" not in md2 and "`SR.particles(" not in md2 and "`SR.overlay(" not in md2
          and "IGNITION" not in md2 and "—  " not in md2
          and not any(ln.startswith("- `") and not ln.endswith("`") for ln in md2.splitlines())
          and len(md2) < 7000 < len(md))

    work = os.path.join(ep, "shots", "s02", "work")
    os.makedirs(os.path.join(work, "out"), exist_ok=True)
    vid = os.path.join(ep, "s02.mp4")
    media.placeholder_shot(vid, 120, [30, 60], "s02", 0)
    got = stages.look_self(c, s2, work, vid, prio=[180])
    imgs = stages.agent_images(work)
    cc = CX.load_cfg(overrides={"bin": "/bin/echo"})
    w2 = os.path.join(ep, "imgs_work")                                 # 点名的卡点帧排在联系表后面，10 张一截也附得上
    for rel in (["ctx/look/self/sheet.jpg", "ctx/look/stock/F41.jpg", "ctx/look/stock/F41_shards.jpg"]
                + [f"ctx/review/{x}.jpg" for x in ("kymo_s04", "mid_f00496", "zoom_s04_f00496", "zoom_s04_fx0_f00496")]
                + [f"ctx/look/self/hit_f{F:05d}.jpg" for F in (421, 434, 459, 483, 496, 508)]):
        os.makedirs(os.path.dirname(os.path.join(w2, rel)), exist_ok=True)
        util.write(os.path.join(w2, rel), "")
    i2 = [os.path.basename(x) for x in stages.agent_images(w2, "## 先看图再改\n- ctx/look/self/hit_f00508.jpg：第 508 帧（重）（这次点名的帧）\n"
                                                                "- ctx/look/self/hit_f00421.jpg：第 421 帧（重）\n")]
    check("Codex 附图：发回点名的 hit_f00508 排在 sheet 后面、10 张一截照样附上；没点名的卡点帧排最后",
          len(i2) == 10 and i2[:2] == ["sheet.jpg", "hit_f00508.jpg"] and "hit_f00421.jpg" not in i2[:2] and len(set(i2)) == 10)
    job = {"run_dir": os.path.join(ep, "runs", "01-shot"), "work": work, "kind": "shot", "stage": "production", "name": "s02",
           "task": "t", "expect": [], "timeout_s": 60, "max_turns": 5, "images": imgs}
    CX.run_agent(job, cfg, cc, cost.Ledger(os.path.join(ep, "cost_cx.json"), cfg["pricing"], None), log, mock_fn=lambda j: {})
    cj = util.read_json(os.path.join(job["run_dir"], "harness", "cmd.json"))
    check("Codex 代理：cmd.json 记着附了哪几张图（相对工作目录），命令行里每张一个 -i",
          cj["images"][0] == os.path.join("ctx", "look", "self", "sheet.jpg") and cj["cmd"].count("-i") == len(imgs))

    rd = os.path.join(ep, "shots", "s02", "render.mp4")
    shutil.copy2(vid, rd)
    files = stages.prepare_ctx(c, work, s2, tp, {"kind": "fix", "fn": "s02"})
    ex = os.path.join(work, "ctx", "example_shot.py")
    exs = util.read(ex) if os.path.exists(ex) else ""
    if os.path.isfile(c.find("prompts", "example_shot", ".py")):
        check("系列示例：prompts/<系列>/example_shot.py 的占位换成这一镜的（函数名、帧号、素材号先取分给这一镜的、叠层种类取本集表里的），"
              "不残留 {{…}}；能被 Python 解析",
              "def s02(E, T)" in exs and "{{" not in exs and ("'F02'" in exs or "F02" not in util.read(c.find("prompts", "example_shot", ".py")))
              and bool(__import__("ast").parse(exs)))
    tp2 = os.path.join(ep, "ex_tpl.py")
    util.write(tp2, "def {{FN}}(E, T):\n    return ('{{CLIP}}', '{{OVERLAY}}', {{HIT_F}}, {{CUT_F}}, '{{TAG}}', '{{WORD}}', '{{HEX}}')\n")
    c.find = lambda kind, name, ext: tp2 if name == "example_shot" else os.path.join(ep, "nope")
    lines_ = stages.example_files(c, work, s2, tp, [("F02", "世界层")], {"kinds": {"powder": {}, "dust": {}}}, [{"F": 150, "lvl": "重"}])
    del c.find
    exs2 = util.read(os.path.join(work, "ctx", "example_shot.py"))
    check("系列示例填法：素材号先取分给这一镜的 F02、叠层种类取这一集表里的第一种、重帧 150、切点 150、tag s02_hero",
          "def s02(E, T)" in exs2 and "('F02', 'powder', 150, 150, 's02_hero', 'NEON', '#FF3BD4')" in exs2 and len(lines_) == 1)
    tp3 = os.path.join(ep, "ex_tpl3.py")                             # 没有叠层 / 没有素材时不拿 dust、F01 充数
    util.write(tp3, "OV = '{{OVERLAY}}'\ndef {{FN}}(E, T):\n    return OV\n")
    c.find = lambda kind, name, ext: (tp3 if name == "example_shot" else os.path.join(ep, "nope"))
    lines3 = stages.example_files(c, work, s2, tp, [], {"kinds": {}}, [{"F": 150, "lvl": "重"}])
    exs3 = util.read(os.path.join(work, "ctx", "example_shot.py"))
    c.find = lambda kind, name, ext: (tp2 if name == "example_shot" else os.path.join(ep, "nope"))
    fj_ = c.j("inputs/footage.json")
    util.write_json(c.p("inputs/footage.json"), {"clips": []})
    lines4 = stages.example_files(c, work, s2, tp, [], {"kinds": {}}, [{"F": 150, "lvl": "重"}])
    util.write_json(c.p("inputs/footage.json"), fj_ or {"clips": []})
    del c.find
    check("系列示例：这一集还没有叠层 → '{{OVERLAY}}' 换成 None、FILES 写明「这一集还没有叠层」（不填 dust）；素材表是空的 → 要实拍的示例不写、FILES 说明",
          "OV = None" in exs3 and "dust" not in exs3 and "这一集还没有叠层" in lines3[0]
          and len(lines4) == 1 and "素材表还是空的" in lines4[0])

    # #8 缺的工具：NOTES.md 几种写法都认，「无」不算；render 收下时进 done.json，report.md 有「代理说缺的工具」一节
    util.write(os.path.join(work, "out", "NOTES.md"), "做了 x\n缺的工具：细长针的立体形状；能弯的管子\n- missing_tools: 无\n"
                                                       "## 缺的工具 / 形状\n- 头盔模型（glb）\n1. 能弯的管子\n## 别的\n- 不是工具\n")
    mt = stages.missing_tools(work)
    util.write_json(os.path.join(ep, "shots", "s02", "done.json"), {"id": "s02", "missing_tools": mt})
    util.write_json(os.path.join(ep, "shots", "s01", "done.json"), {"id": "s01", "missing_tools": ["细长针的立体形状"]})
    tm = "\n".join(stages.tools_md(c, sl))
    check("缺的工具：NOTES.md 里「缺的工具：a；b」、「## 缺的工具」下的条目都收、去重，「无」和别的小标题下的不收；"
          "report.md「代理说缺的工具」按句合并、记哪几镜提过（两镜都提的排前面）",
          mt == ["细长针的立体形状", "能弯的管子", "头盔模型（glb）"] and "## 代理说缺的工具" in tm
          and tm.index("细长针的立体形状 | s01, s02") < tm.index("头盔模型"))

    # #10 卡点：硬线只留「计划的拍上有画面事件」，倍数只报告
    ev, noev = {"kind": "weak", "ratio": 2.6, "d": 0.05}, {"kind": "weak", "ratio": 1.1, "d": 0.02}
    rs = {k: {"id": k, "pass": True, "score": 4, "issues": []} for k in ("s01", "s02", "s03")}
    sy = {"issues": [dict(ev, shot="s01", F=60, lvl="重", msg="第 60 帧：卡上了但不够狠，2.6 倍"),
                     dict(noev, shot="s02", F=150, lvl="重", msg="第 150 帧：没卡上，1.1 倍"),
                     {"shot": "s03", "F": 300, "kind": "unselective", "lvl": "轻", "msg": "s03 选择性"},
                     {"shot": "s03", "F": 301, "kind": "late", "lvl": "轻", "msg": "晚 1 帧", "check": "sync_top"}]}
    sf, _ = stages.sync_force(rs, sy, {}, soft={"sync_top"})
    check("卡点硬线：拍上有变化只是倍数不够（ratio ≥ 2）、选择性、本系列降成报警的只当建议（照样过）；拍上没有画面事件的判不过",
          sf == ["s02"] and rs["s01"]["pass"] and rs["s03"]["pass"] and not rs["s02"]["pass"]
          and "倍数" in rs["s01"]["issues"][0]["rule"] and stages.sync_hard({"kind": "late"}) and stages.sync_hard({"kind": "nocut"})
          and not stages.sync_hard(ev) and stages.sync_hard(noev) and not stages.sync_hard({"kind": "silent"}))
    wk9 = os.path.join(ep, "shots", "s09", "work")
    os.makedirs(os.path.join(wk9, "out"))
    s9 = {"id": "s09", "start_f": 0, "end_f": 12, "cuts_f": [6], "hits": [], "look": "design", "spec_sha": "x"}
    util.write(os.path.join(wk9, "out", "shot.py"), mockshot.shot_code("s09", dict(s9, signature_color={"hex": "#5FF2FF"}), [], "#5FF2FF"))

    class R9:
        root, py, name = os.path.join(ep, "engine"), sys.executable, "fake"

        def render_shot(self, spec, code, out):
            media.placeholder_shot(out, spec["end_f"] - spec["start_f"], [6], "s09", 0)
            return {"ok": True}
    real = G.shot_sync
    G.shot_sync = lambda render, start_f, table, py, cfg=None: {"ok": False, "misses": [dict(ev, F=6, lvl="重", msg="第 6 帧：不够狠")],  # noqa: E731
                                                              "silent": [{"F": 9, "msg": "第 9 帧：没起音"}], "text": "## 卡点没做到\n- 第 6 帧"}
    try:
        c.renderer = R9()
        stt = {"sync_sent": 0, "table": [{"F": 6, "lvl": "重", "attack_f": 5.6, "desc": "x"}]}
        d9, e9 = stages.render_and_accept(c, s9, "shot", None, wk9, os.path.join(wk9, "out", "shot.py"),
                                          os.path.join(ep, "shots", "s09", "render.mp4"), os.path.join(ep, "shots", "s09", "done.json"), st=stt)
        G.shot_sync = lambda render, start_f, table, py, cfg=None: {  # noqa: E731
            "ok": False, "misses": [dict(noev, F=6, lvl="重", msg="第 6 帧：没卡上"), dict(ev, F=7, lvl="轻", msg="第 7 帧：卡上了但变化太小")],
            "silent": [{"F": 9, "msg": "第 9 帧：没起音"}], "text": "## 卡点没做到\n- 第 6 帧：没卡上\n- 第 7 帧：卡上了但变化太小\n- 第 9 帧：没起音"}
        d10, e10 = stages.render_and_accept(c, s9, "shot", None, wk9, os.path.join(wk9, "out", "shot.py"),
                                            os.path.join(ep, "shots", "s09", "render.mp4"), os.path.join(ep, "shots", "s09", "done.json"),
                                            st={"sync_sent": 0, "table": stt["table"]})
    finally:
        G.shot_sync = real
        c.renderer = RR()
    check("卡点硬线（制作中）：只是倍数不够、表外强变化不发回，第一次就收下，done.json 记 sync.hard = 0；收下时 look/self 是这一版",
          bool(d9) and not e9 and stt["sync_sent"] == 0 and d9["sync"]["hard"] == 0 and d9["sync"]["ok"] is False
          and "ctx/look/self/sheet.jpg" in d9.get("look", []))
    check("发回文字：拍上没有画面事件的发回一次，问题单末尾「先看图再改」点名 ctx/look/self/ 的联系表和这次点名的卡点帧；"
          "同时量到的倍数不够、无起音处强变化不进发回文字（G10 只报告）",
          d10 is None and "先看图再改" in e10 and "ctx/look/self/sheet.jpg" in e10 and "ctx/look/self/hit_f00006.jpg：第 6 帧" in e10 and "这次点名的帧" in e10
          and "第 6 帧：没卡上" in e10 and "变化太小" not in e10 and "没起音" not in e10)

    # #10 低谷镜头：节拍表里能量比前后都低的那一段，线放低（编排器、门禁同一口径）
    bm = {"sections": [{"id": "S1", "energy": "high"}, {"id": "S2", "energy": "low"}, {"id": "S3", "energy": "high"}], "hits": []}
    mc = stages.motion_cfg(c)
    nb = stages.motion_needs(sl, bm, mc)
    E = G._eff_cfg(G._cfg(series["gates"], "motion"))
    eff = {"shots": {k: {"eff": 3.0, "d96": 5.0, "J": 0.1, "pingpong": 0, "content": 3.0} for k in ("s01", "s02")}}
    ck = {x["id"]: x for x in G.eff_checks(eff, G._cfg(series["gates"], "motion"), True, troughs={"s02"})}
    check("低谷镜头：节拍表 S2 比前后都低 → s02 是低谷，线 = 线 × 0.55（首尾段不算低谷）；门禁 motion_eff 只点名 s01",
          G.trough_shots(bm, sl) == {"s02"} and set(nb) == {"s02"} and abs(nb["s02"] - mc["need_eff"] * 0.55) < 1e-9
          and G.trough_shots({"sections": [{"id": "S1", "energy": "low"}, {"id": "S2", "energy": "high"}]}, sl) == set()
          and E["trough_ratio"] == 0.55 and any("s01" in w for w in ck["motion_eff"]["where"])
          and not any("s02" in w for w in ck["motion_eff"]["where"]))

    # #6 审片：模型固定一个（不跟制作后端走）；来源表、前几轮意见接进提示词；返修任务书「点名的必改，同类一起改」
    class L0:
        mock_fn, c = None, {"model": cfg["api"]["model"]}
    c.llm = L0()
    same = stages.review_llm(c) is c.llm
    c._review_llm, c.backend = None, "codex"
    ds = stages.review_llm(c)
    c._review_llm, c.backend, c.cfg = None, "deepseek", dict(cfg, review={"model": "codex"})
    cx_ = stages.review_llm(c)
    c._review_llm, c.cfg, c.backend = None, cfg, None
    check("审片模型：config review.model 没写 = DeepSeek：DeepSeek 后端直接用同一个；Codex 后端另起一个 DeepSeek（费用记 cost_review.json）；"
          "写 codex 就换成 Codex",
          same and isinstance(ds, llm.LLM) and not isinstance(ds, CX.CodexLLM) and ds.c["model"] == cfg["api"]["model"]
          and isinstance(cx_, CX.CodexLLM))
    util.write_json(os.path.join(ep, "review_1.json"), {"shots": {"s02": {"pass": False, "score": 3, "issues": [
        {"rule": "劣质特效", "where": "第 150 帧 2D 碎片", "fix": "换 SR.shatter"}]}}})
    ri = stages.review_inputs(c, 2, [s2])
    tpl = os.path.join(ep, "rv.md")
    util.write(tpl, "审 {{TITLE}}\n{{NOTES}}\n")
    c.find = lambda kind, name, ext: tpl if kind == "prompts" else stages.Ctx.find(c, kind, name, ext)
    pv = stages.prompt_with(c, "04_review", ri, TITLE="t")
    del c.find
    check("审片输入：每镜来源表（out/NOTES.md）、这一镜前几轮的意见都送进审片；模板写了 {{NOTES}} 就填在那里，没写的（HISTORY）接在末尾",
          "缺的工具：细长针" in ri["NOTES"][1] and "第 1 轮：不过（3 分）" in ri["HISTORY"][1] and "换 SR.shatter" in ri["HISTORY"][1]
          and pv.index("### s02") < pv.index("## 这几镜前几轮") and pv.count("每镜来源表") == 0)

    # #12 停止原因写清没满足的是哪一项；每版自动出预览三件
    why = stages.loop_stop({"failed": ["s02", "s05"], "sync_failed": ["s05"], "global": {"anchor_score": 6.5}}, 3, 2, 6)
    why2 = stages.loop_stop({"failed": ["a", "b", "c", "d"], "global": {"anchor_score": 6.5}}, 3, 2, 6)
    check("停止原因：6.5 ≥ 6 时不再写「内部评分没到线」，写真正没满足的（还有镜头卡点没画面事件 / 没过的镜头 4 个 > 3）",
          "内部评分" not in why and "计划的拍上没有画面事件 s05" in why and "没过的镜头 4 个 > 3" in why2 and "内部评分" not in why2)
    mu = os.path.join(ep, "m.wav")
    media.ff(["-f", "lavfi", "-i", "sine=f=220:d=6:sample_rate=48000", "-ac", "2", mu])
    util.write_json(os.path.join(ep, "inputs", "music.json"), {"file": mu, "start_s": 1.0, "downbeat_s": 1.0, "bpm": 150, "title": "测"})
    cut = os.path.join(ep, "film", "cut_r0.mp4")
    media.placeholder_shot(cut, 240, [60, 120], "cut", 0)
    stages.round_preview(c, 0, cut, [G._chk("motion_eff", "每镜有效运动量", "warn", "1/2 镜够")],
                         {"cuts_per_s": 1.5, "motion": {"eff": {"per_shot": {"s01": {"eff": 6.1}}}}})
    pj = util.read_json(os.path.join(ep, "film", "preview_r0.json"))
    pr = media.probe(os.path.join(ep, "film", "preview_r0.mp4"))
    check("每版预览：cut 拼好就出带配乐的 film/preview_r0.mp4（A 版：原曲直接混）、12 帧联系表、数（门禁、每镜有效运动量）",
          pr.get("audio") and os.path.exists(os.path.join(ep, "frames", "preview_r0.jpg")) and pj["eff"] == {"s01": 6.1}
          and "A 版" in pj["sound"] and pj["not_ok"] and "film/preview_r[1-9]*" in stages.PRODUCTS[4])


# DeepSeek 把 cuts 写进了镜头对象里，收尾少写了 ]}（花括号开 6 关 5，方括号开 3 关 2）
S7_REPLY = (
    "{\"shots\":[{\"id\":\"s07\",\"pass\":false,\"score\":3,\"issues\":[{\"rule\":\"根·转场｜底线①：不拿叠化（两帧混合）当冲击过渡\",\"wh"
    "ere\":\"图 1 第 3 行左（13.433s，约第 806–810 帧）：亮灯、GLOW 出现时整块字重影、边缘发虚，白色爆点糊成一团，同一帧里能看到落位前后两版字\",\"fix\":\"把这处两帧交叉混合"
    "换成单一冲击帧：用整屏反白或满屏集中线底盖住旧画面的那一帧，派亮灯、GLOW 从画面外砸入，落到最终位置后固定，不再回摆；同一拍里补上灯管字继续下沉到画面底部（单向），运动量不能比现在少。\"},{\"rule\""
    ":\"根·主体｜底线①：一个主体，不被挡、一眼认得出，随便截一帧都能当海报\",\"where\":\"图 1 第 2 行中、右（12.9–13.0s）与图 2：灯管字从正中滑到右上角、缩到画面约 1/12 大小，右侧"
    "被画框裁掉一条边，画面中央和左侧都让给亮灯两个大字，主体退成配角\",\"fix\":\"让灯管字走单向：从切点起一路推近长大，停在画面正中偏上（完整在框内，不贴右边缘），随后沿中心线单向下沉到画面底部当句号；亮灯、G"
    "LOW 往左下压暗让位，保证每帧都能看到完整的主体轮廓。\"},{\"rule\":\"卡点炫技·特效｜底线①：冲击帧满屏，主体剪影仍认得出\",\"where\":\"图 1 第 3 行左（13.4s）：爆发只是左"
    "下角一小团白光和白雾，画面其余部分还是旧构图，看不出这是整屏事件\",\"fix\":\"把这一拍做成整屏事件：用底色反白或纸白冲击帧铺满全幅、盖住紫底，灯管字的黑色剪影压在冲击帧上仍认得出；冲击闪完进入新的版式，字在"
    "下、灯管在底部，不要闪回紫底加两个大字的旧构图。\"}],\"cuts\":[{\"cut\":\"s07\",\"declared\":\"出画「招牌熄灭后露出的紫底」→ 画面上半，同一大小 → 入画「海报帧整块霓虹紫上"
    "的同一颗灯管字」\",\"seen\":\"图 6 左（11.967s）紫底上满是浅色碎屑，下方还有大字和碎片，灯管字只在画面顶端露一小角；中（12.000s）已经是干净紫底，灯管字放大到约占画面 1/4，位置居中"
    "偏左上；右（12.033s）与中基本一致\",\"ok\":false,\"fix\":\"让灯管字在切点前后那两帧位置和大小对得上：s06 结尾就把它送到画面中央，大小接近 s07 第一帧，中心偏差 ≤30 px、面"
    "积比 0.8–1.25；s07 第一帧从这一版直接接住，再在镜内单向推近长大；紫底保持同一块，别在切点上把主体从「顶端露一角」跳到「居中大块」。\"}]}"
)


def json_close_ok(tmp, cfg, led):
    """11-06：回复没被截断、只是少写了收尾的 ] }，util.parse_json 按栈补上（llm.py 记一行日志）；真截断（length）照旧重问。"""
    rs = util.read_json(os.path.join(util.HERE, "schemas", "review.json"))
    o, n = util.parse_json(S7_REPLY, close=True)
    o = stages.hoist_cuts(o)
    try:
        util.extract_json(S7_REPLY)
    except ValueError as e:
        pass
    bad = []
    for t in (S7_REPLY[:700], S7_REPLY + ",", S7_REPLY[:S7_REPLY.rindex('"fix"') + 6], S7_REPLY + "\n以上"):
        try:
            util.parse_json(t, close=True)
            bad.append(t[-20:])
        except ValueError:
            pass
    check("JSON 补闭合符：停在字符串中间、逗号 / 冒号后面、JSON 后面还有别的字的，不补（照旧重问）", bad == [])
    ep = os.path.join(tmp, "jc_ep")
    os.makedirs(ep, exist_ok=True)
    seen = []
    L = llm.LLM(cfg, led, ep, lambda m, echo=True: seen.append(m), mock_fn=lambda *a: S7_REPLY)
    o2, info = L.ask_json("review", "review_r1_S7", "sys", "p", schema=rs)
    rec = util.read_json(os.path.join(ep, "calls", "001-review_r1_S7-a1.json"))
    check("JSON 补闭合符：llm.ask_json 第一次就收下（不重问），日志写「自动补了 2 个闭合符」，calls/ 记 closed = 2",
          info["attempts"] == 1 and len(o2["shots"]) == 1 and any("自动补了 2 个闭合符" in m for m in seen) and rec.get("closed") == 2)


def cut_pixels_ok(tmp, series):
    """合成一条 150 帧小片：s02 入点闪黑、s03 入点一帧是两镜的混合、s04 对位接得上（条纹同位置换颜色）、s05 对位接不上。"""
    w, h = 320, 180

    def frame(kind, t):
        if kind == "black":
            return bytes(w * h * 3)
        if kind == "A":                                  # 灰底，左边一根移动的白竖条
            row = bytes(v for x in range(w) for v in [230 if 40 + t <= x < 60 + t else 128] * 3)
            return row * h
        if kind == "B":                                  # 满屏横条纹
            return b"".join(bytes([200 if (y // 12) % 2 else 60] * 3) * w for y in range(h))
        hi, lo = (220, 30) if kind == "D" else (40, 160)    # D：只有右三分之一有竖条纹；E：同样位置的条纹换了颜色（对位）
        return bytes(v for x in range(w) for v in [hi if x >= 220 and (x // 8) % 2 else lo] * 3) * h

    seq = [frame("A", t) for t in range(30)] + [frame("black", 0)] + [frame("B", t) for t in range(29)]
    seq += [bytes((p + q) // 2 for p, q in zip(frame("B", 30), frame("D", 0)))] + [frame("D", t) for t in range(1, 30)]
    seq += [frame("E", t) for t in range(30)] + [frame("A", t) for t in range(30)]
    out = os.path.join(tmp, "cuts.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", "60", "-i", "-",
                    "-c:v", "libx264", "-crf", "8", "-pix_fmt", "yuv444p", out], input=b"".join(seq), check=True,
                   env={"PATH": util.BIN_PATH, "HOME": util.HOME})
    kinds = [None, "甩", "拍上硬切", "对位", "对位"]
    shots = [{"id": f"s{i + 1:02d}", "start_f": 30 * i, "end_f": 30 * i + 30, "cuts_f": [],
              "transition_in": {"kind": k, "eye": "测试", "why": "测试"} if k else {}} for i, k in enumerate(kinds)]
    chk, _ = G.video_checks(out, series["gates"], None, {"total_f": 150, "shots": shots})
    c = {x["id"]: x for x in chk}
    wh = {k: " ".join(c[k].get("where") or []) for k in ("cut_dip", "cut_blend", "cut_carry")}
    return (all(c[k]["status"] == "warn" for k in wh) and "s02" in wh["cut_dip"] and "s03" in wh["cut_blend"]
            and "s05" in wh["cut_carry"] and "s04" not in wh["cut_carry"] and not any(x in wh["cut_blend"] for x in ("s02", "s04", "s05")))


if __name__ == "__main__":
    sys.exit(main())
