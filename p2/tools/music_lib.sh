#!/bin/bash
# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
# 曲库节拍分析：把你有权使用的曲子（mp3 / wav）放进 p2/assets/曲库/music/，再运行
#   bash p2/tools/music_lib.sh                    # 分析默认曲库
#   bash p2/tools/music_lib.sh <曲库目录> [--hint 文件名片段=BPM ...]
# 用引擎虚拟环境（p2/chroma/.venv）跑 p2/tools/beats.py：写 <曲库>/beats.json 和 music/<曲名>.events.json、music/<曲名>.music_map.json。
set -euo pipefail
P2="$(cd "$(dirname "$0")/.." && pwd)"
LIB="${1:-$P2/assets/曲库}"
[ $# -gt 0 ] && shift
PY="$P2/chroma/.venv/bin/python"
[ -x "$PY" ] || { echo "没找到引擎虚拟环境 $PY：先按 SKILL.md「环境要求」建好"; exit 1; }
[ -d "$LIB/music" ] || { echo "没有 $LIB/music/：把曲子放进去再运行"; exit 2; }
N="$(find "$LIB/music" -maxdepth 1 -type f \( -iname '*.mp3' -o -iname '*.wav' \) | wc -l | tr -d ' ')"
[ "$N" -gt 0 ] || { echo "$LIB/music/ 里还没有 mp3 / wav：先放曲子"; exit 2; }
export NUMBA_CACHE_DIR="${TMPDIR:-/tmp}/yezi-ve-numba"
mkdir -p "$NUMBA_CACHE_DIR"
echo "分析 $N 首：$LIB/music/"
"$PY" -I -B "$P2/tools/beats.py" "$LIB" "$@"
python3 -I - "$LIB/beats.json" <<'PY'
import json, os, sys
lib = os.path.dirname(os.path.abspath(sys.argv[1]))
d = json.load(open(sys.argv[1], encoding="utf-8"))
ok = 0
for t in d.get("tracks", []):
    w = t.get("window") or {}
    good = bool(w) and 21 <= (w.get("dur_s") or 0) <= 27 and 125 <= t["bpm"] <= 155
    why = ""
    mf = os.path.join(lib, t["music_map_file"]) if t.get("music_map_file") else ""
    if good and mf and os.path.isfile(mf):                # 打击点表里没有 DROP：编排器卡不上大拍，不算能用（表不在不判：编排器现生成）
        try:
            mm = json.load(open(mf, encoding="utf-8"))
        except (OSError, ValueError):
            mm = {}
        if not mm.get("drop"):
            good, why = False, "；打击点表里没找到 DROP"
    ok += good
    print(("能用  " if good else "用不上") + f"  {t['file']}  {t['bpm']:.1f} BPM  " +
          (f"窗口 {w['dur_s']:.1f} s、DROP 在 {w['drop_in_window_s']:.1f} s" if w else "没找到 DROP 窗口") + why)
print(f"曲库里能给 CHROMA 用的：{ok} 首" + ("" if ok else "（要 125–155 BPM、有明显 DROP 的曲子）"))
PY
