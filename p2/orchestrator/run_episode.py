#!/usr/bin/env python3
# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""yezi-ve P2 编排器：按环节 0–7 跑一集，流程由代码控制，环节之间是文件 + 门禁 + 轮数上限。

  python3 -I run_episode.py --series chroma --out ../episodes/ep-mock --mock                # 假回复跑通全流程
  python3 -I run_episode.py --series chroma --out ../episodes/ep01 --budget-usd 30         # 真跑（DeepSeek；不给 --budget-usd 就用 config.json 的 budget.default_usd）
  python3 -I run_episode.py --series chroma --out ../episodes/ep01-codex --backend codex   # 真跑（Codex 制作：你的 ChatGPT 登录；审片走 DeepSeek）
  python3 -I run_episode.py --series chroma --out ../episodes/ep01-short --short           # 试跑短片：8 小节（铺垫 4 + DROP 后 4，约 13 s），不做 5 秒样片
  再运行同一条命令 = 断点续跑；--from N 从第 N 环节重做；--until N 跑到第 N 环节停。
  python3 -I run_episode.py --series chroma --out ../episodes/ep01 --notes ../notes/ep01.notes.json --max-rounds 1
      看片意见轮：notes 复制进这一集的目录，审片环节开头按 notes 合成问题单、返修（同一份只消费一次；--max-rounds 从这一轮起算）

退出码：0 完成；2 参数错误；3 预算停；4 被阻塞（要你处理：key、配乐）；5 硬门禁不过或环节失败。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import cost, llm, mockdata, render, stages, util  # noqa: E402
from lib import codex  # noqa: E402


def lock(ep):
    p = os.path.join(ep, ".lock")
    if os.path.exists(p):
        try:
            pid = int(util.read(p).strip())
            os.kill(pid, 0)
            util.die(f"另一个编排器（pid {pid}）正在跑这一集：{ep}")
        except (ValueError, OSError):
            pass
    util.write(p, str(os.getpid()))
    return p


def main():
    ap = argparse.ArgumentParser(description="按环节 0–7 跑一集（流程由代码控制）")
    ap.add_argument("--series", required=True, choices=("chroma",), help="系列：chroma")
    ap.add_argument("--out", required=True, help="这一集的目录（必须在 p2/ 下、不在 orchestrator/ 里）")
    ap.add_argument("--mock", action="store_true", help="模型回复用假数据（不读 key、不调模型）；渲染、门禁、抽帧都是真的")
    ap.add_argument("--fake-render", action="store_true", help="连渲染也换成 ffmpeg 占位（只测流程，几分钟内跑完）")
    ap.add_argument("--motif", default=None, help="你定的母题（如 霓虹、沙丘）；不给就由开题环节从 3 个候选里选。"
                                                  "母题可以还没有素材包（空包起跑：配乐从系列 music.library 曲库挑，实拍由素材请求通道去找）")
    ap.add_argument("--title", default=None, help="你起的中文片名；不给就用开题环节的暂定名")
    ap.add_argument("--max-rounds", type=int, default=None, help="审片后返修的轮数上限（不写用默认上限）")
    ap.add_argument("--short", nargs="?", type=int, const=-1, default=None, metavar="N",
                    help="试跑短片模式（试跑只要 10–30 秒）：配乐窗口起点不变、只做前 N 小节（默认 config.json short.bars = 8 = 铺垫 4 + DROP 后 4，"
                         "系列配置 short.bars 优先；DROP 要落在里面）；不做 5 秒样片和静帧代表帧；返修轮数用默认上限。建集时定下（记进 state.json），续跑可以不再写"),
    ap.add_argument("--budget-usd", type=float, default=None,
                    help="本集模型费用上限（累计，含续跑前已花的）；真跑不给就用 config.json 的 budget.default_usd")
    ap.add_argument("--from", dest="from_", type=int, default=None, help="从第 N 环节起重做（之前的保留）")
    ap.add_argument("--until", type=int, default=None, help="跑完第 N 环节就停")
    ap.add_argument("--backend", choices=("deepseek", "codex"), default="deepseek",
                    help="模型后端（默认 deepseek，和以前一样）；codex = 开题、导演和写代码的代理用 codex exec（你的 ChatGPT 登录）；审片固定走 DeepSeek（config.json 的 review.model，要 DeepSeek key）")
    ap.add_argument("--imagegen", choices=("auto", "off", "codex"), default="auto",
                    help="生图通道：代理写 work/out/images.json，编排器在沙箱外用 codex image_gen 生成；auto = codex 后端开、deepseek 后端关")
    ap.add_argument("--codex-config", default=None, help="Codex 配置（默认 orchestrator/config_codex.json）")
    ap.add_argument("--codex-effort", default=None, help="覆盖 Codex 思考档位（问答和代理；默认照 config_codex.json / ~/.codex/config.toml）")
    ap.add_argument("--notes", dest="review_notes", default=None, metavar="文件",
                    help="看片意见轮：你看片后的原话（JSON：{\"said\": [你的原话，一句一条], \"mode\": \"append\"}）。"
                         "只放原话：逐镜去找、去定改法交给审片和劣质特效扫描（append = 先正常审一轮，你的原话放在每镜返修任务书最前面）；"
                         "不写 shots / 代码行号 / 参数处方。restore / unfreeze / redo_fixed / motion_floor_r0 / shots 这些键还认，只给编排器调试用。"
                         "复制进这一集的目录，按内容哈希只用一次；已经交付过的集会从第 4 环节重跑")
    args = ap.parse_args()
    if args.review_notes and not os.path.isfile(args.review_notes):
        util.die(f"--notes 文件不存在：{args.review_notes}")

    series_p = os.path.join(util.HERE, "series", args.series + ".json")
    if not os.path.exists(series_p):
        util.die(f"没有系列配置 {series_p}")
    ep = os.path.abspath(args.out)
    if not util.inside(ep, util.P2) or util.inside(ep, util.HERE):
        util.die(f"--out 必须在 {util.P2} 下（且不在 orchestrator/ 里）：{ep}")
    cfg = util.read_json(os.path.join(util.HERE, "config.json"))
    if not args.mock and args.budget_usd is None and args.backend == "deepseek":
        args.budget_usd = (cfg.get("budget") or {}).get("default_usd")     # 没给就用 config.json 的单集上限（只防失控）
        if args.budget_usd is None:
            util.die("真跑必须给 --budget-usd（超了就停），或在 config.json 写 budget.default_usd")
    os.makedirs(ep, exist_ok=True)
    series = util.read_json(series_p)
    state_p = os.path.join(ep, "state.json")
    sc = dict(cfg.get("short") or {}, **(series.get("short") or {}))
    short = (int(sc.get("bars", 8)) if args.short == -1 else args.short) if args.short is not None else None
    if short is not None and short < 2:
        util.die(f"--short 的小节数要 ≥ 2：{short}")
    state = util.read_json(state_p) or {"series": args.series, "mock": args.mock, "created": util.iso(), "stages": {},
                                        **({"backend": args.backend} if args.backend != "deepseek" else {}),
                                        **({"short": short} if short else {})}
    if state["mock"] != args.mock:
        util.die(f"这一集是用 {'--mock' if state['mock'] else '真实模式'} 建的，不能混跑；换一个 --out")
    if state.get("backend", "deepseek") != args.backend:
        util.die(f"这一集是用 --backend {state.get('backend', 'deepseek')} 建的，不能换成 {args.backend} 续跑；换一个 --out")
    if short and state.get("short") != short:
        util.die(f"这一集是{'试跑短片 ' + str(state['short']) + ' 小节' if state.get('short') else '全片'}建的，不能换成 --short {short}；换一个 --out")
    short = state.get("short")                                          # 续跑不写 --short 也照建集时的
    if short:
        g = dict(series["gates"])                                       # 片长门禁换成试跑的（只在内存里，系列配置不动）；挑曲子照全片的
        g["duration_s_full"] = g["duration_s"]
        g["duration_s"] = list(sc.get("duration_s") or [10, 30])
        sfo = [k for k in g.get("soft_full_only") or [] if k in (g.get("soft") or {})]
        if sfo:
            g["soft_full"] = g["soft"]
            g["soft"] = {k: v for k, v in g["soft"].items() if k not in sfo}
        series = dict(series, gates=g)
        if args.max_rounds is None:
            args.max_rounds = int(sc.get("max_rounds", 2))
    if args.max_rounds is None:
        args.max_rounds = 3
    old_sl = util.read_json(os.path.join(ep, "shotlist.json"))
    if old_sl and not old_sl.get("alloc") and (args.from_ is None or args.from_ > 1):
        util.die("这一集是旧版流水线建的（镜头表没有全片分配，例如试跑稿 v1）：新代码续跑会沿用旧镜头表、跳过已完成的环节，"
                 "全片分配、5 秒样片、必做动作都用不上。换一个新的 --out（推荐），或加 --from 1 从导演起重做（旧产物挪到 _old/，不删）")
    lk = lock(ep)
    stamp = util.now().strftime("%Y%m%d-%H%M%S")
    log = util.Log(ep, f"{os.path.basename(ep)}-{stamp}")
    if args.from_ is not None:
        for k in list(state["stages"]):
            if int(k) >= args.from_:
                del state["stages"][k]
        moved = stages.archive_from(ep, args.from_, stamp)
        log(f"--from {args.from_}：{len(moved)} 个旧产物挪到 _old/{stamp}/（不删）")
    if args.review_notes:
        try:
            got = stages.notes_intake(ep, args.review_notes, state, log)
        except util.Stop as e:
            os.remove(lk)
            util.die(e.msg, e.code)
        if got == "pending" and any(int(k) >= 4 for k in state["stages"]):
            for k in [k for k in state["stages"] if int(k) >= 4]:
                del state["stages"][k]                                  # 已经审过 / 交付过：从第 4 环节重跑（产物不挪，用户轮自己存档）
            log("看片意见轮：第 4 环节以后标成没做，从审片环节接着跑")
        util.write_json(state_p, state)
    cc = None
    if args.backend == "codex" or args.imagegen == "codex":
        cc = codex.load_cfg(args.codex_config, {"effort": args.codex_effort})
        if args.codex_effort:
            cc["agent"]["effort"] = args.codex_effort
    if args.backend == "codex":
        ledger = codex.Ledger(os.path.join(ep, "cost.json"), args.budget_usd)
        model = codex.CodexLLM(cfg, cc, ledger, ep, log, mock_fn=mockdata.llm_reply if args.mock else None)
    else:
        ledger = cost.Ledger(os.path.join(ep, "cost.json"), cfg["pricing"], args.budget_usd)
        model = llm.LLM(cfg, ledger, ep, log, mock_fn=mockdata.llm_reply if args.mock else None)
    rend = (render.FakeRenderer if args.fake_render else render.ChromaEngine)(ep, series, log, args.mock)
    c = stages.Ctx(ep, series, cfg, args, log, ledger, model, rend, args.mock)
    c.backend, c.cc, c.short = args.backend, cc, short
    c.state = state                                                     # 看片意见轮在 state.json 的 notes_rounds 里记消费过（同一个 dict）
    if short:
        log(f"试跑短片模式：配乐窗口前 {short} 小节（窗口起点不变），片长门禁 {series['gates']['duration_s'][0]}–{series['gates']['duration_s'][1]} s，"
            f"不做 5 秒样片和静帧代表帧，返修最多 {args.max_rounds} 轮"
            + (f"；全片才降成报警的门禁 {'、'.join(k for k in series['gates']['soft_full'] if k not in series['gates']['soft'] and not k.startswith('_'))}"
               "在试跑里照旧硬（系列 gates.soft_full_only）" if series["gates"].get("soft_full") else ""))
    if args.backend == "codex":
        log(f"开始：{series['name']} → {ep}（{'mock 回复' if args.mock else 'Codex（ChatGPT 登录）'}，模型 {cc['model']} / {cc['effort']}，"
            f"渲染 {rend.name}，生图通道 {stages.IG.mode(c)}；费用不按美元计（订阅额度），守卫只看代理轮数和时长）")
    else:
        log(f"开始：{series['name']} → {ep}（{'mock 回复' if args.mock else 'DeepSeek'}，渲染 {rend.name}，预算 "
            f"{'不限' if args.budget_usd is None else '$%.2f' % args.budget_usd}，已花 ${ledger.total:.4f}）"
            + (f"；生图通道 {stages.IG.mode(c)}" if stages.IG.enabled(c) else ""))
    code, at = 0, ""
    state.pop("stop_msg", None)                                         # 上一次停下的原因不留到这一次（被 kill -9 时收尾不会说成上一次的）
    state.pop("stop_stage", None)
    try:
        for n, name, fn, outs in stages.STAGES:
            at = f"第 {n} 环节「{name}」"
            outs = stages.stage_outputs(c, n, outs)
            st = state["stages"].get(str(n), {})
            if st.get("status") == "done" and all(os.path.exists(os.path.join(ep, o)) for o in outs):
                log(f"[{n}] {name}：已完成，跳过")
            else:
                log(f"[{n}] {name}")
                state["status"] = f"running:{n}"
                util.write_json(state_p, state)
                t0 = util.now()
                fn(c)
                state["stages"][str(n)] = {"status": "done", "at": util.iso(), "outputs": outs,
                                           "wall_s": round((util.now() - t0).total_seconds(), 1), "usd_total": ledger.total}
                if n == 4:
                    state["stages"]["5"] = {"status": "done", "at": util.iso(), "note": "返修在第 4 环节的循环里"}
                util.write_json(state_p, state)
            if args.until is not None and n >= args.until:
                log(f"按 --until {args.until} 停")
                state["status"] = f"paused:{n}"
                break
        else:
            state["status"] = "done"
            log(f"完成：{os.path.join(ep, 'report.md')}；费用 ${ledger.total:.4f}")
    except util.Stop as e:
        state["status"] = f"stopped:{e.status}"
        state["stop_msg"], state["stop_stage"] = e.msg, at             # start_*.sh 收尾照它说停在哪个环节、为什么
        log(f"停：{e.msg}")
        code = e.code
    except Exception as e:  # noqa: BLE001  程序错误：记下来再抛出（带调用栈）
        state["status"] = "stopped:error"
        state["stop_msg"], state["stop_stage"] = f"{type(e).__name__}: {e}"[:500], at
        log(f"程序错误：{state['stop_msg']}")
        raise
    except KeyboardInterrupt:
        state["status"] = "stopped:interrupted"
        log("被 Ctrl-C 中断；再运行同一条命令续跑")
        code = 130
    finally:
        state["usd_total"] = ledger.total
        state["updated"] = util.iso()
        util.write_json(state_p, state)
        if os.path.exists(lk):
            os.remove(lk)
    return code


if __name__ == "__main__":
    sys.exit(main())
