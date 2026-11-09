# mock 固定回复
`--mock` 时模型调用不发请求：开题用 `topic.json`（固定选第一个候选：霓虹）；导演用 `director.json` 的 bible，节拍表和镜头表由
`lib/mockdata.py` 按配乐约束（BPM、小节数、DROP 小节）现编（转场声明也现编）；审片回复现编：第 1 轮固定让 s04、s06 不过、内部评分 4.5，
第 2 轮 s06 还不过但内部评分 6.5 ≥ 6，用来走一遍「返修 + 冻结 + 到 6 分就停」；切点核对第 1 轮让 s04、s05 不 ok。假代理（`lib/mockshot.py`）写出真能被 CHROMA 引擎渲染的镜头代码，渲染、门禁、抽帧都是真的。
内容是占位，不代表真实开题或审美判断。

规则树：`topic.json` 多了 `category`（卡点炫技）/ `category_why` / `differs` / `line_kind`、数字链每站 `source`；
`lib/mockdata.py` 的节拍表多了每段 `color_from`、`hook_1s`、`wow_7s`，镜头表多了 `route` / `signature` / `deviations`（转场轮着用 6 种，
写了偏离理由）、每镜 `sustain` / `type_fx`。假代理按转场声明调 `SR.tx`，每个构图里镜头一直在走（推近 + 横移）；只有 `STATIC_V0`（s02）
第一版故意切进来就停住（PPT 感），机器量镜内运动量判它不过 → 样片返修（variant 1 动起来）→ 过关：用来证明这道门禁在流程里起作用。
