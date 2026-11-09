#!/usr/bin/env bash
# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
# sheet.sh — 从视频均匀抽 N 帧拼成带时间戳的联系表 PNG（只用 ffmpeg/ffprobe）
set -uo pipefail

usage() {
  cat <<'EOF'
用法：sheet.sh <video.mp4> <out.png> [N] [选项]

在 <video.mp4> 上均匀取 N 个时刻（默认 12，取每段中点：t_i = 时长 × (i+0.5)/N），
每帧缩到同一宽度、左上角印 "#序号 t=秒"，拼成一张网格 PNG。用于让模型/人快速看全片。

选项：
  --cols C     每行几张（默认：N≤4 时等于 N，否则 4）
  --width W    每张缩略图宽度像素（默认 480）
  -h, --help   显示本帮助

输出：一行 "OK sheet: <out.png> (N 帧, C 列, 时长 Xs)"，随后一行列出所有取帧时刻。
退出码：0 成功；1 ffmpeg 失败；2 参数错误。
EOF
}
die_usage() { echo "ERROR: $*" >&2; echo "（运行 sheet.sh --help 看用法）" >&2; exit 2; }

[ $# -ge 1 ] && { [ "$1" = "-h" ] || [ "$1" = "--help" ]; } && { usage; exit 0; }
[ $# -ge 2 ] || die_usage "缺少参数：需要 <video.mp4> <out.png> [N]"
VIDEO="$1"; OUT="$2"; shift 2
N=12
if [ $# -gt 0 ] && [[ "$1" =~ ^[0-9]+$ ]]; then N="$1"; shift; fi
COLS=""; WIDTH=480
while [ $# -gt 0 ]; do
  case "$1" in
    --cols) [ $# -ge 2 ] || die_usage "--cols 需要一个值"; COLS="$2"; shift 2;;
    --width) [ $# -ge 2 ] || die_usage "--width 需要一个值"; WIDTH="$2"; shift 2;;
    -h|--help) usage; exit 0;;
    *) die_usage "不认识的参数：$1";;
  esac
done
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
command -v ffmpeg >/dev/null || die_usage "找不到 ffmpeg"
command -v ffprobe >/dev/null || die_usage "找不到 ffprobe"
[ -f "$VIDEO" ] || die_usage "视频不存在：$VIDEO"
case "$OUT" in *.png|*.PNG) ;; *) die_usage "输出文件必须以 .png 结尾：$OUT";; esac
{ [ "$N" -ge 1 ] && [ "$N" -le 100 ]; } 2>/dev/null || die_usage "N 必须是 1–100 的整数：$N"
[[ "$WIDTH" =~ ^[0-9]+$ ]] && [ "$WIDTH" -ge 64 ] || die_usage "--width 必须是 ≥64 的整数：$WIDTH"
if [ -z "$COLS" ]; then if [ "$N" -le 4 ]; then COLS="$N"; else COLS=4; fi; fi
[[ "$COLS" =~ ^[0-9]+$ ]] && [ "$COLS" -ge 1 ] || die_usage "--cols 必须是正整数：$COLS"
ROWS=$(( (N + COLS - 1) / COLS ))

DUR="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$VIDEO" 2>/dev/null)"
awk -v d="$DUR" 'BEGIN{exit !(d+0>0)}' || { echo "ERROR: 读不到视频时长：$VIDEO" >&2; exit 1; }

FONT=""
for f in /System/Library/Fonts/Menlo.ttc /System/Library/Fonts/SFNSMono.ttf /usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf "C:/Windows/Fonts/consola.ttf"; do
  [ -f "$f" ] && { FONT="$f"; break; }
done
OUT_DIR="$(dirname "$OUT")"; mkdir -p "$OUT_DIR" || die_usage "无法创建输出目录：$OUT_DIR"
TMP="$(mktemp -d "$OUT_DIR/.sheet-XXXXXX")" || { echo "ERROR: 无法创建临时目录" >&2; exit 1; }
trap 'rm -rf "$TMP"' EXIT

FS=$(( WIDTH / 18 )); [ "$FS" -lt 12 ] && FS=12
TIMES=""
i=0
while [ $i -lt "$N" ]; do
  T="$(awk -v d="$DUR" -v i="$i" -v n="$N" 'BEGIN{printf "%.3f", d*(i+0.5)/n}')"
  LABEL="#$((i+1)) t=${T}s"
  VF="scale=${WIDTH}:-2"
  if [ -n "$FONT" ]; then
    VF="$VF,drawtext=fontfile='${FONT}':text='${LABEL}':x=8:y=8:fontsize=${FS}:fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=6"
  fi
  if ! ffmpeg -v error -y -ss "$T" -i "$VIDEO" -frames:v 1 -vf "$VF" "$TMP/f_$(printf %03d $i).png" 2>"$TMP/err.txt"; then
    echo "ERROR: ffmpeg 抽帧失败（t=${T}s）：" >&2; tail -20 "$TMP/err.txt" >&2; exit 1
  fi
  TIMES="$TIMES ${T}"
  i=$((i+1))
done
if ! ffmpeg -v error -y -framerate 1 -i "$TMP/f_%03d.png" \
     -vf "tile=${COLS}x${ROWS}:padding=6:margin=6:color=0x1a1a1a" -frames:v 1 "$OUT" 2>"$TMP/err.txt"; then
  echo "ERROR: ffmpeg 拼图失败：" >&2; tail -20 "$TMP/err.txt" >&2; exit 1
fi
echo "OK sheet: $OUT ($N 帧, $COLS 列, 时长 ${DUR}s)"
echo "  取帧时刻(s):$TIMES"
exit 0
