# 开题：{{SERIES}} 第 {{N}} 集

往期（母题不能重复，可以和前集无关）：
{{EPISODES}}

## 先判大类（规则树目录卡）
{{CATEGORY}}

{{include:catalog#大类|判断问题}}

## 制作环境（体检结果；按它选路线，缺的东西不要指望）
{{ENV}}

## 规则（根 + 本大类类型卡，开题用得上的几行）
{{rules:主线,素材,配色|do,floor;主线|param}}
{{rule:root.flow.1}}

## 系列卡（节选）
{{include:card#开新一集|必须做到|参数}}


## 要你做
给 3 个新母题候选，选 1 个。每个候选：
- motif：母题（一个字或一个词，能发光、能一路升级或降级）
- title_cn：暂定中文片名（2–4 字，你最后起名）
- screen_title：屏上英文片名大字（全大写英文）
- signature_color：签名色 {name, hex}（世界去色，只给母题这一种颜色）
- hero_object：每集一个主角物（母题物），写清形状和材质；要用实拍或生图做得出来
- number_ladder：屏上真数字阶梯，≥3 级，{value, unit, what, source}，必须是真的物理数（像 525 °C→1538 °C→5772 K）；source = 出处一句（教科书 / 百科 / 机构）
- lexicon：≥12 个大词（屏上字{{TEXT_LANG}}；开头几个构图和每镜的大字都从这里取，全片不重复）
- footage_needs：需要的满屏实拍素材，6–12 条，每条 {"need": "中文一句话", "query": "英文搜索词", "use": "world", "seconds": 6}：素材包是空的或不够时，编排器照 query 去免费素材站找、下。query 写画面里的东西本身：3–5 个英文词，主体名词或型号在前，不写数值、单位和 footage、moving、high speed 这类泛词
- why_new：和前几集「一样又不一样」在哪
最后 chosen = 选中的序号（0–2），reason = 一句话；再写这一集（选中的那个）的：
- category：大类（填「卡点炫技」）；category_why：一句理由（判断问题第 1 问：这片卖不卖节奏和冲击）
- differs：这一集和上一集、和同类最火的那条哪里不同（一句）
- line_kind：这一集的变化线（尺度 / 数值 / 时间或光线 / 情绪 / 剧情，选一条再补半句）

输出 JSON：{"candidates":[...3 个...],"chosen":0,"reason":"...","category":"...","category_why":"...","differs":"...","line_kind":"..."}
