#!/bin/bash
# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
# 一键开跑 CHROMA 试跑短片：母题、输出目录都由参数定；不给 OUT 时输出到 episodes/<母题>-short（DeepSeek）。
# 配乐窗口起点不变、只做前 SHORT 小节（默认 8：145 BPM 时约 13 s，DROP 在第 5 小节），不做 5 秒样片和静帧代表帧，返修最多 2 轮。
#   bash start_chroma_short.sh <母题>                  DeepSeek 版，输出 episodes/<母题>-short/（要 DeepSeek key 文件）
#   bash start_chroma_short.sh 霓虹 "片名"               第 1 个参数 = 母题（也可以 MOTIF=霓虹），第 2 个 = 你起的中文片名（可省）
#   OUT=episodes/xxx bash start_chroma_short.sh 沙丘     换输出目录（相对 p2/ 或绝对路径）；BACKEND=codex 默认输出 <OUT>-codex
#   SHORT=12 / BUDGET=30 / MAX_ROUNDS=2 / STOP_AT=3 / FROM=4 / IMAGEGEN=off / EFFORT=high / NOTES=notes/<集>.notes.json
#   （NOTES 只放你的原话：said + mode append；逐镜问题由审片和劣质特效扫描自己找，不写 shots、行号、参数处方）
# 空包起跑：母题没有素材包 p2/assets/<母题>/ 也能开跑，只要曲库里有能用的曲子（系列配置 music.library）；实拍由开题 footage_needs、
# 开拍前盘点和素材请求通道去找，下进 p2/assets/<母题>/。
# 断了、停了：再运行同一条命令就续跑（母题、短片小节数建集时定下，续跑不能改）。不读 key 内容：DeepSeek 只查 key 文件在不在；
# Codex 只查 codex login status。素材请求通道默认开（缺真材料编排器自己去 Pexels / Mixkit / Commons 找，不问你）；关：STOCK_OFF=1。
set -u
P2="$(cd "$(dirname "$0")" && pwd)"
BACKEND="${BACKEND:-deepseek}"
case "$BACKEND" in codex|deepseek) ;; *) echo "BACKEND 只能是 codex 或 deepseek"; exit 2;; esac
MOTIF="${1:-${MOTIF:-}}"
if [ -z "$MOTIF" ]; then echo "用法：bash p2/start_chroma_short.sh <母题> [片名]（也可以 MOTIF=<母题>）"; exit 2; fi
TITLE="${2:-${TITLE:-}}"
SHORT="${SHORT:-8}"
if [ ! -f "$P2/assets/$MOTIF/beats.json" ] && [ ! -f "$P2/assets/$MOTIF/music.json" ] && [ ! -f "$P2/assets/$MOTIF/inputs/music.json" ]; then
  # 这一集还没有配乐：曲库（系列配置 music.library）里要有能给 CHROMA 用的曲子（和 music_lib.sh 同一个判法）
  LIBOK="$(python3 -I - "$P2" <<'PY'
import json, os, sys
p2 = sys.argv[1]
try:
    s = json.load(open(os.path.join(p2, "orchestrator", "series", "chroma.json"), encoding="utf-8"))
except (OSError, ValueError):
    s = {}
n = 0
for lib in (s.get("music") or {}).get("library") or []:
    try:
        d = json.load(open(os.path.join(p2, "assets", lib, "beats.json"), encoding="utf-8"))
    except (OSError, ValueError):
        continue
    for t in d.get("tracks") or []:
        w = t.get("window") or {}
        if (w and 21 <= (w.get("dur_s") or 0) <= 27 and 125 <= (t.get("bpm") or 0) <= 155
                and os.path.isfile(os.path.join(p2, "assets", lib, str(t.get("file") or "")))):
            mf = os.path.join(p2, "assets", lib, t["music_map_file"]) if t.get("music_map_file") else ""
            if mf and os.path.isfile(mf):
                try:
                    mm = json.load(open(mf, encoding="utf-8"))
                except (OSError, ValueError):
                    mm = {}
                if not mm.get("drop"):
                    continue
            n += 1
print(n)
PY
)"
  if [ "${LIBOK:-0}" = 0 ]; then
    echo "曲库里没有能给 CHROMA 用的曲子（p2/assets/曲库/）：按 SKILL.md「自带配乐」放 125–155 BPM、有明显 DROP 的曲子，再运行 bash \"$P2/tools/music_lib.sh\"，它列出的「能用」不是 0 首再开跑"
    exit 2
  fi
fi
if [ ! -d "$P2/assets/$MOTIF" ]; then                              # 空包起跑：实拍由代理和素材请求通道去找
  SOFF="$(python3 -I -c 'import json,sys; print((json.load(open(sys.argv[1])).get("stock") or {}).get("enabled", True))' "$P2/orchestrator/series/chroma.json" 2>/dev/null)"
  if [ "${STOCK_OFF:-}" = 1 ] || [ "$SOFF" = False ]; then   # 空包又不找素材 = 实拍 0 条，镜头表在环节 1 就过不了
    echo "母题「${MOTIF}」没有素材包，素材请求通道又关着（STOCK_OFF=1 或 series/chroma.json stock.enabled=false）：没有实拍可用，不开跑"; exit 2
  fi
  echo "母题「${MOTIF}」还没有素材包：空包起跑（配乐从曲库挑；实拍由代理和素材请求通道去找）"
fi
DEF_OUT="episodes/${MOTIF}-short"
if [ "$BACKEND" = codex ]; then
  CODEX="${CODEX_BIN:-$HOME/.local/bin/codex}"
  if [ ! -x "$CODEX" ]; then echo "找不到 codex（${CODEX}）：先装 Codex CLI"; exit 4; fi
  ST="$(env -u CODEX_HOME -u OPENAI_API_KEY -u CODEX_API_KEY "$CODEX" login status 2>&1 | head -1)"
  case "$ST" in *ChatGPT*) ;; *) echo "Codex 不是用 ChatGPT 账号登录的（${ST}）：请你运行 codex login 选 ChatGPT；不走 API key"; exit 4;; esac
  OUT="${OUT:-$DEF_OUT}"; OUT="${OUT%/}"; case "$OUT" in *-codex) ;; *) OUT="$OUT-codex";; esac
  RVM="$(python3 -I -c 'import json,sys; print(str((json.load(open(sys.argv[1])).get("review") or {}).get("model") or "deepseek").lower())' "$P2/orchestrator/config.json" 2>/dev/null)"
  case "$RVM" in codex*|gpt*) ;; *)                               # 审片固定走 DeepSeek（config.json review.model）：key 文件也要在
    if [ ! -s "$HOME/.config/yezi-ve/deepseek.key" ] && [ -z "${DEEPSEEK_API_KEY:-}" ]; then
      echo "Codex 制作、审片用 ${RVM:-deepseek}（config.json review.model），还没有 DeepSeek key：先运行  bash \"$P2/setup_key.sh\""; exit 4
    fi;; esac
else
  if [ ! -s "$HOME/.config/yezi-ve/deepseek.key" ] && [ -z "${DEEPSEEK_API_KEY:-}" ]; then
    echo "还没有 DeepSeek key：先运行  bash \"$P2/setup_key.sh\""; exit 4
  fi
  OUT="${OUT:-$DEF_OUT}"
fi
case "$OUT" in /*) ;; *) OUT="$P2/$OUT";; esac
OUT="${OUT%/}"
case "$OUT" in "$P2"/episodes/*) ;; *) echo "OUT 要在 p2/episodes/ 下：$OUT"; exit 2;; esac
if [ -f "$OUT/topic.json" ]; then                                  # 续跑：母题要和建集时一样
  HAVE="$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1])).get("motif",""))' "$OUT/topic.json" 2>/dev/null)"
  if [ -n "$HAVE" ] && [ "$HAVE" != "$MOTIF" ]; then echo "$OUT 建集时的母题是「${HAVE}」，不是「${MOTIF}」：换个 OUT"; exit 2; fi
fi
if pgrep -f "run_episode.py .*--out $OUT( |$)" >/dev/null 2>&1; then echo "已经有一个 run_episode 在跑 $OUT"; exit 2; fi
LOG="$P2/../logs/p2/$(basename "$OUT")-$BACKEND-$(date +%Y%m%d-%H%M%S).log"
mkdir -p "$(dirname "$LOG")"
ARGS=(--series chroma --out "$OUT" --backend "$BACKEND" --short "$SHORT" --motif "$MOTIF")
[ -n "$TITLE" ] && ARGS+=(--title "$TITLE")
[ -n "${BUDGET:-}" ] && ARGS+=(--budget-usd "$BUDGET")
[ -n "${IMAGEGEN:-}" ] && ARGS+=(--imagegen "$IMAGEGEN")
[ -n "${EFFORT:-}" ] && ARGS+=(--codex-effort "$EFFORT")
[ -n "${MAX_ROUNDS:-}" ] && ARGS+=(--max-rounds "$MAX_ROUNDS")
[ -n "${STOP_AT:-}" ] && ARGS+=(--until "$STOP_AT")
if [ -n "${NOTES:-}" ]; then
  case "$NOTES" in /*) ;; *) NOTES="$P2/$NOTES";; esac
  [ -f "$NOTES" ] || { echo "NOTES 文件不存在：$NOTES"; exit 2; }
  ARGS+=(--notes "$NOTES")
fi
[ -n "${FROM:-}" ] && ARGS+=(--from "$FROM")
cd "$P2/orchestrator" || exit 2
echo "母题 ${MOTIF}，输出 ${OUT}，后端 ${BACKEND}，短片 ${SHORT} 小节；日志：${LOG}"
T0=$(date +%s)
python3 -I run_episode.py "${ARGS[@]}" 2>&1 | tee -a "$LOG"
code=${PIPESTATUS[0]}
case $code in                                                       # 按 state.json 的 status / stop_msg 说停在哪个环节、为什么
  3) echo "预算停：BUDGET=更大的数 再运行同一条命令续跑";;
  4) echo "在等你（key、登录、额度或配乐）：见上面最后一行；处理完再运行同一条命令";;
  130) echo "被中断：再运行同一条命令续跑";;
  *) python3 -I - "$OUT" "$code" "$T0" <<'PY'
import json, os, sys
ep, code, t0 = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
try:
    sp = os.path.join(ep, "state.json")
    s = json.load(open(sp, encoding="utf-8")) if os.path.getmtime(sp) >= t0 - 1 else {}   # 这一次没写过 = 没跑起来（旧的不算）
except (OSError, ValueError):
    s = {}
st, why = s.get("status") or "", " ".join(str(s.get("stop_msg") or "").split())[:600]
have = "；".join(os.path.join(ep, f) for f in ("report.md", "deliver", "film/with_audio.mp4") if os.path.exists(os.path.join(ep, f)))
if code == 0:
    print(("完成" if st == "done" else f"按 STOP_AT 停（{st}）") + "：" + (have or "这一集还没有报告和成片"))
elif not s:
    print(f"没跑起来（退出码 {code}）：原因见上面最后几行")
else:
    print(f"停在{s.get('stop_stage') or '某个环节'}（{st or '?'}，退出码 {code}）：{why or '见 state.json'}")
    print("现有：" + (have or "还没有报告和成片") + ("（挡在哪见 report.md 的「不过」）" if st == "stopped:hard_gate" and "report.md" in have else "")
          + f"；断点 {os.path.join(ep, 'state.json')}，处理完再运行同一条命令续跑")
PY
  ;;
esac
exit $code
