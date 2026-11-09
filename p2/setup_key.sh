#!/bin/bash
# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
# 一次性：把 DeepSeek key 存进只有你的账户能读的文件，之后跑批和编排器在后台自动读取，不用再粘贴。
# 先用余额接口验证，验证通过才保存。沙箱里的模型读不到这个位置。撤销：rm ~/.config/yezi-ve/deepseek.key
set -u
D="$HOME/.config/yezi-ve"; F="$D/deepseek.key"
printf '\n请粘贴 DeepSeek key，然后按回车。屏幕上不会显示任何字符。\n> '
IFS= read -rs K; echo
K="$(printf '%s' "$K" | tr -d '[:space:]')"
case "$K" in
  sk-*) ;;
  *) if printf '%s' "$K" | grep -Eq '^[A-Za-z0-9]{32}$'; then K="sk-$K"; echo "（没带 sk- 前缀，已自动补上再验证）"; fi ;;
esac
case "$K" in sk-*) ;; *) echo "收到的不像 DeepSeek key（${#K} 个字符）。请重新运行。"; exit 1 ;; esac

DEEPSEEK_API_KEY="$K" python3 -I - <<'PY'
import json, os, sys, urllib.error, urllib.request as u
req = u.Request("https://api.deepseek.com/user/balance",
                headers={"Authorization": "Bearer " + os.environ["DEEPSEEK_API_KEY"], "Accept": "application/json"})
try:
    d = json.load(u.urlopen(req, timeout=20))
    print("key 有效。账户余额：" + "，".join(f"{b.get('total_balance')} {b.get('currency')}" for b in d.get("balance_infos") or []))
except urllib.error.HTTPError as e:
    if e.code in (401, 403):
        print(f"key 无效（HTTP {e.code}），没有保存。请到 platform.deepseek.com 的 API keys 页重新复制完整的 key，再运行一次。")
        sys.exit(1)
    print(f"余额查询返回 HTTP {e.code}，先保存。")
except Exception as e:  # noqa: BLE001
    print(f"余额查询失败（{type(e).__name__}），先保存。")
PY
[ $? -eq 0 ] || exit 1
umask 077; mkdir -p "$D"; printf '%s' "$K" > "$F"; chmod 600 "$F"
echo "已保存（权限 600）。启动脚本和编排器会自动读取它，可以关掉这个窗口。"
