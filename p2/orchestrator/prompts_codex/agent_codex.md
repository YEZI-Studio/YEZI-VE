## 运行环境（Codex，这一次是{{KIND}}代理）
- 你在 codex exec 里，沙箱 workspace-write：只能写当前目录（这一镜的 work/），不联网，/tmp 不可写；渲染由编排器在沙箱外做，你写代码、跑 `ctx/check.sh`。
- 这条消息附的图（有的话）就是 ctx/look/self/（你这一镜上一次渲出来的画面）、ctx/review/（审片看到的帧）和 ctx/look/stock/（到货素材的联系表，不是你渲的）；别的图用 view_image 打开（任务书写的「用 Read 打开」就是它）。
- 临时文件放 `$TMPDIR`（`mktemp -p "$TMPDIR"`、Python tempfile、zsh here-doc 都行；不带参数的 `mktemp` 会去 /var/folders，被沙箱拦）。
- 读文件用 shell（cat、sed -n、rg），改文件用 apply_patch 或直接写；不要装东西，不要上网查，不要开子代理，不要调用 image_gen（要图就按任务书里「生图」一节写 out/images.json；没有这一节就不生图）。
- 引擎副本、素材都只读；不要改 ctx/ 里的文件（每次都会被编排器重新放一遍）。沙箱只让你读：当前目录、引擎副本（ctx/check.sh 里的 ENG）、实拍 / 生图素材目录、引擎的 Python 和系统工具；别的镜头、编排器、HOME 都读不到（读不到就是沙箱拦了，不要换办法绕，要的东西都在 ctx/ 里）。
- 任务书和 ctx/HANDBOOK.md 就是全部规则；这里不重复。
- 做完：最后一条回复写一两行：做了什么、`ctx/check.sh` 的最后一行。
