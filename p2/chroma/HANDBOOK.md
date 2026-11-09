# CHROMA 引擎手册（给写镜头的模型）

一个镜头 = 一个 Python 函数 `fn(E, T) -> Tex`：给帧号，出这一帧。GPU（moderngl）合成实拍 + 调色 + 字 + 冲击帧，60 fps、1920×1080。
路径都相对引擎根（你手上的副本在 `ctx/src/` 下）；你只写 `out/shot.py`。先看 §1 的整镜示例和 §3b 正向配方；渲出来什么样看 §7。

## 1. 整镜示例（照它的结构写）
`ctx/example_shot.py` 是这个系列一段实测能渲的整镜：函数名、帧号、素材号、叠层 kind 已经换成这一镜的，内容按 spec 改。
骨架：常量（签名色、帧号、素材表、冲击簇）→ 画一帧（素材 → 相机 / 跟拍 → 调色 → 叠加的层、字 → `SR.post`）→ `@shot` 包起来，末尾 `fn.length = 止帧`。

## 2. 规矩
- 镜头是时间的纯函数：任何一帧单独渲都一样。状态（用哪段素材、切点、冲击帧）看整数帧 `T.F`；运动（相机、字、缓动）看 `T.f`。
- 顺序：素材 → `fx.cam` 相机 → 调色（`MO.world`）→ 叠加（叠层、字）→ `SR.post` 冲击簇。
- 坐标一律 1080p 像素，(0,0) 在左上，y 向下。颜色只用 `P[...]` 和 `INK PAPER WHITE`，不手写十六进制。
- 大字只用分给这一镜的词（spec 的 hero_word / words），一个词可以跨几个构图留着；一帧一种大字体（hero 或 wide）+ 一层小标签（DM Mono，≥22 px，默认 24）。屏上字的语言照任务书。屏上数字只用 spec 的 number。
- 冲击簇跨切点时，前后两个镜头都要 `SR.post` 同一个 `HITS`（蓄力在前一镜，见 ex_open 引 ex_hit.HITS）。

## 3. 时间（150 BPM 示例：一拍 24 帧、16 分音符 6 帧、一小节 96 帧）
`TL.f(小节, 拍=1, 16分=0)` 浮点帧 · `TL.F(...)` 整数帧 · `TL.pos(F)` → (小节,拍,16分) · `TL.BEAT TL.SIXTEENTH TL.BAR TL.END`
`T.f` 帧（含子帧）· `T.F` 整数帧 · `T.lf` 镜头内帧 · `T.prog(a, b)` a→b 的 0..1 · 缓动 `keys(f, [(帧, 值, 'eo3'), ...])`、`eo3 eo5 eoexp ei3 eio3 eoback lerp clamp01`

### 卡点和运动的写法
- 卡点帧从「本镜卡点」表来：F = 画面该变的那一帧，已按真实起音算好。直接写成整数常量（`HIT, C2, C3 = 1352, 1365, 1390`），不要自己从 bar / beat 换算。
- 切换按整数帧：`if T.F >= HIT:`，或 `i = SR.seg(T.F, [C2, C3])`（切点那一帧当帧就换）。不要用 `T.f`、`TL.f()` 的小数判切换。
- 打击包络 `SR.env(T.F, HIT, hold=0, decay=6.0)`：HIT 之前 0、HIT 那一帧 1、之后衰减；`SR.since(T.F, HIT)` = 过了几帧。一下多大跟着音乐走：`SR.slots(f0, f1, ('S', 'A'))` → `[(F, 档位, k)]`，k = 音乐强度 0..1。
- 「重」帧 = 整屏事件：新构图从这一帧起，或冲击帧盖在新构图上（`img = NEW(T) if T.F >= HIT else OLD(T)`，再 `SR.post(E, T, img, [SR.hit(HIT, …)])`：蓄力落在旧构图、冲击和余波落在新构图），或 `HE.hero` 砸字。冲击闪完不许回到旧构图。跨两镜用 `SR.tx(HIT, 'hit', …)` + `SR.tx_frame`。
- 表外的帧不许有跳变（比前后几帧突然大好几倍）；连续的单向运动不受限，越多越好。
- 运镜的节拍器默认关：`SR.auto_cam` / `SR.AutoCam` 的 kick / six / drift 默认 0；要单向运镜就直接给 `px_per_f` / `zoom_pct_per_f`（§5）。每拍一抖不是卡点，也不算动。
- 震屏只绑在大卡点上、8 帧内衰减完：`SR.hit` 自带，或 `fx.shake(T.f, HIT - 0.5, amp, decay=3)`；`SR.rumble(f, amp, f0, f1)` 只给发射 / 爆炸，窗口 ≤1 小节。

## 3b. 镜内运动的正向配方（带量纲）
原片（CHROMA 定稿片）的运动大部分来自画面内容在变（非刚性占 79–88 %）：每个切点换进一张新画面并大幅落地（切后 1–4 帧拿走 40–49 % 运动量），素材自己在动；镜头刚体运动只占 9–15 %；钉住的主角物只在海报和收尾。所以每一格同时做两件事：**切点上换内容并落地 + 格内一件单向的事**。运镜可以用，但不当这一格唯一的动作。

| 来源 | 引擎调用 | 每格最小幅度 |
|---|---|---|
| ① 切点换新画面 + 落地 | 切点换一条 Take 或同一条的另一个时段（`i = SR.seg(T.F, CUTS)`）；落地 `SR.slam(t, n=4..5, s0=1.28..1.32)`、`SR.step(f, F, amp, dur=3)`、`SR.punch(f, F, amp, dur=6..7)`（只放在切点 / 冲击帧上）；跨切点 `SR.whip(E, img, T.f, F, d=2..3, px=650..1100)`、`SR.push_thru(T.f, F, n=3.5, amt=2.6)`、`SR.hit` 盖在新构图上 | 每个计划切点都换掉占画面大头的东西；落地 4–6 帧内完成 |
| ② 格内一件单向的事 | `fx.cam` 的 pose 只用只增不减的量（`keys` 单调、`SR.moves(f, [(a, b, dzoom, dx, dy, droll), ...])`）；`SR.AutoCam(f0, f1, move, px_per_f=…, zoom_pct_per_f=…)`；钉住的主角配 `SR.track`（§5） | 横移 ≥8 px/帧或推近 ≥1 %/帧（给世界层 / 素材层）；跟拍世界 10–12 px/帧；钉住的主角整镜累计 ≤1.3–1.5 倍 |
| ③ 素材自己在动 + 变速 | `Take(..., speed=1.5..3)`；看 ctx/footage.md 的「自带运动量」（最快一段从第几秒起，src 取那附近）；低于 60 fps 的素材写 `interp='blend'` 或变速到 60 ÷ 帧率倍以上 | 自带运动量低的 2–3 倍速，只当底；每格换一条 |
| ④ 字的一生 | `HE.hero`、`TY.letters`、`TY.word(..., scale=SR.slam(t, 4, 1.3))` + 位置随时间单向走 | 字移 10–16 px/帧；逐字母 3 帧一个 |
| ⑤ 叠层 / 碎裂 | 实拍叠层 `SR.element(E, T, img, kind, f0=…, f1=…, px_per_f=… 或 zoom_pct_per_f=…)`（kind 见 ctx/overlays.json）；画面自己碎开 `SR.shatter(E, img, F, c, cells=28, open=SR.shatter_open(T.f, F), nxt=下一张)` | 叠层每帧走 7–18 px（跟拍前景 = 世界 × 1.8）；碎块初速 ≥40 px/帧；叠层只当陪衬，算不进这一格的进展 |
| ⑥ 全屏图形场 | `KV.Vortex`（段落之间，也可以在镜内一段）、`SR.tx(F, 'kaleido')` | 一段 ≥半小节，单向越转越深 |
| ⑦ HUD（少用，不算动效零件） | `SR.ring_hud` / `SR.readout` / `SR.ring_text` | 线宽 ≤1.4 px、不圈主体 |

- 实测标定（编排器量的有效运动量，96×54 灰度，原地来回扣掉）：纹理素材横移 8 px/帧 ≈ 6.9；推近 1 %/帧 ≈ 5.4；`SR.track` 默认（单色、压暗 0.8、虚 1.5 px）世界 8 px/帧 ≈ 3.5–4.7、10 px/帧 ≈ 5.0；钉住的主角在实拍世界上 + 实拍前景叠层 ≈ 10；`SR.shatter` 碎开那一段 ≈ 7.6，同一处画 2D 三角碎片只有 ≈ 4.0；放到一点漂的雾、烟类叠层只加约 0.5；低于 60 fps 的素材 1× 不写 blend ≈ 0.2–0.9；纯色底 ≈ 0，纯色底上主体每帧长 1 % ≈ 1.0。原片整片 8.7–10.1。前景叠层会把数抬高，但主角不动 + 满屏叠层，审片照样判「静图 + 推拉」。
- 推近、横移的分工：每帧 1 % 的推近、8–12 px 的横移给世界层 / 素材层（`SR.track` 每段单独起机位）；钉住的主角整镜累计最多长到 1.3–1.5 倍（每帧 0.2–0.4 %），要更大就在一格里砸入或长大做完。
- 不算动、不许拿来凑：原地来回（sin / cos 小幅摆、缩放呼吸、来回转、整帧抖、每拍整帧泵、推了又拉回、同一张图在切点上宽紧来回跳）、「静图 + 推拉」、停稳 / 越来越慢到停（屏息拍、片尾海报除外）。删掉一个运动来源时，同一处补一件单向的事。

## 4. API 速查
| 调用 | 作用 |
|---|---|
| `Take('F01', film=(f0, f1), src=秒, speed=1 \| [(帧, 速率)...], tier=1080\|2160, loop=False\|'wrap')` | 一段素材用在片子 f0..f1（名字照 ctx/footage.md）；速率 0.5 慢放、0 定格、<0 倒放；src + 时长不能超出素材 |
| `E.clip(take, T.F)` | 这一帧的素材纹理 |
| `fx.cam(E, T, tex, pose, n=3)` | 2.5D 相机；`pose(f) -> dict(zoom, roll, rx, ry, piv=(素材点), at=(屏幕点))`，n = 运动模糊采样 |
| `E.over(底, 上)` `E.add(a, b, k)` `E.screen` `E.multiply` `E.mix(a, b, 遮罩, ch='a')` `E.solid(PAPER)` `E.blur(t, σ)` `E.affine(t, scale, rot, center, offset)` | 合成（上层是预乘 alpha） |
| `cv = E.canvas()` → `cv.circle/rect/line/arc/polyline(...)`、`cv.texture()` | 矢量层（skia）：只画字和 §5b 白名单里的图形 |
| `TY.word(cv, 文本, x, 基线y, 字号, 'hero'\|'wide'\|'accent'\|'mono', 颜色, alpha, align, scale=)` | 字；`TY.size_for_cap('hero', 230)` = 大写高 230 px 的字号；`TY.MARGIN` = 96 |
| `TY.letters(cv, 文本, x, y, 字号, 'hero', 颜色, 'center', each=lambda i, n: (缩放, dx, dy, alpha) 或 None)` | 逐字母动画 |
| `TY.label(cv, ['行1', '行2'], x, y, 24, 颜色, alpha)` | DM Mono 数据标签 |

## 5. 零件（每个一行；参数都有默认值）
- 签名色 `P = MO.palette('#hex')`：派生 `acc deep glow pale core ramp hue`。`MO.hit_cols(P)` 给冲击簇，`MO.vortex_cols(P)` 给万花筒。
- 单色世界 `MO.world(E, img, P, keep='hue'|'bright', lo=0.45, hi=0.8, sat=0.95, glow=0.18)`：世界黑白，只有签名色（hue）或所有亮光（bright）留色。
- 冲击簇 `SR.hit(F, 预设, c=(x,y), k=0.6..1.5, **MO.hit_cols(P))`：蓄力 2–4 帧 → 冲击 1–3 帧 → 余波 4–10 帧。预设 `post3_blue duo_blue lines lines_focus half_blue voronoi(全片最多 2 次) kick`。直接接 `**MO.hit_cols(P)`，不加 `sparks=`；`voronoi` 在平滑的底上碎块是平涂多边形，碎开用 `SR.shatter`。
- 冲击风格卡 `SR.card(E, img, T.F, 预设, c, j=0|1, **MO.hit_cols(P))`：只要冲击帧的样子（开头 10 格用）。
- 主角字 `HE.hero(E, img, T.f, '文本', start, land, leave, style='slam'|'slab'|'card'|'knock', label='...')`：一行管一个大字的一生（slab = 实心字从字中心的圆里揭开再砸）；先 `HE.set_motif(P)`。
- 万花筒「时光穿梭」`V = KV.Vortex(f0, f1, end='portal'|'disc', nseg=6, nseg_end=12, src_c=取样点, eye=(960,540), **MO.vortex_cols(P))`；`V.frame(E, T, 当前画面, 下一画面)`，`V.phase(T.f)` = 'fold'|'spin'|'portal'。段落之间做时光穿梭；镜内也可以用一段（≥半小节，单向越转越深）。
- 设计转场 `TXS = [SR.tx(F, 'whip'|'push'|'pull'|'smear'|'spin'|'shape'|'circle'|'iris'|'stripes'|'blocks'|'hit', ...)]`；`img = SR.tx_frame(E, T, TXS, lambda X: 画出拥有帧X的镜头)`（见 parts_demo.py）。kind 对应见 §9。
- 虚拟摄影机 `SR.auto_cam(E, T, tex, f0, f1, move='push'|'pull'|'pan'|'truck'|'crane'|'orbit'|'spiral'|'drift', eye=(x,y), px_per_f=None, zoom_pct_per_f=None, amt=1)`：自动跟拍点 + 单向运镜，不露素材边。速度直接给：`px_per_f` = 每帧走多少 px（`ang` 改方向，0 = 右、90 = 下），`zoom_pct_per_f` = 每帧推近 %（负数拉远）。不给就是预设速度 × `amt`（amt=1 只走 1.4–2.8 px/帧，约 1.2–1.6；amt=4 约 5–10）。`cam = SR.AutoCam(...)`；`cam.predict()` = 每帧带动画面多少 px。长镜头每个计划切点换素材、分段走（`SR.track` 自动分段）。
- 跟拍 `img = SR.track(E, T, hero, WORLDS, f0, f1, move='truck'|'crane'|'push', px_per_f=10, cuts=CUTS, P=P, at=(主角中心), hero_r=主角大致半径, front=叠层kind)`：主角钉住（`hero` = 预乘纹理，或 `callable(E, T)`；None = 只要流动的世界），身后有纹理的世界单向流过，返回整幅画面。`WORLDS` = Take 列表，第 i 段用第 i 条：**每个计划切点换一条素材或同一条的另一个时段**。push 用 `zoom_pct_per_f`（默认 1.2 %/帧）；`parallax=(前景倍速, 世界倍速)` 默认 (1.8, 1.0)；`front` = ctx/overlays.json 里的一种 kind（实拍叠层沿世界流向走、每个计划切点重新起机位；`front_kw=dict(win=…, avoid=True)` 换窗口 / 让开主角），或一条 Take、`callable(E, T, i)`。世界默认先 `MO.world`（给了 P）、压暗 `dim=0.8`、略虚 `soft=1.5`，主角身后垫暗 `shade=0.5`。主角要大（外框占画面三成以上），自己还要做一件事（推近、长大、砸入，只增不减）。
- 实拍叠层 `SR.element(E, T, img, kind, f0=, f1=, px_per_f=0, ang=90, zoom_pct_per_f=0, pos=None, scale=1, k=1, win=0, speed=1, cuts=None, avoid=None, own=False)`：这一集的黑底实拍去背景叠上去（kind、每个 kind 的窗口 win 和说明见 ctx/overlays.json；`SR.elements()` 也列得出）；默认用签名色的冷光 screen，`own=True` 用素材自己的颜色。素材自己在动，再给一件单向的事：整屏（`pos=None`）沿 `ang` 每帧走 `px_per_f`，或 `zoom_pct_per_f` 迎面推（穿镜），要给 `f1` 好算盖边，`cuts` = 每个计划切点换下一个窗口；放到一点（`pos=(x, y)`）= 素材里的发射点落在那里（写着「只能整屏用」的不放到一点）。`img=None` 只返回预乘的光层；`avoid=主角层` 让开主角。
- 画面自碎 `SR.shatter(E, img, F, c=(x,y), cells=28, open=SR.shatter_open(T.f, F, px=620, tau=8), nxt=下一张, ang=None, spread=120, refract=1)`：这一帧画面按 Voronoi 碎成 cells 块，每块里就是画面本身，单向往外飞，缝里露出 `nxt`，边上是画面自己的折射。张开量有下限（`px` ≥ 360、`tau` ≤ 12）：小张开量是一张等宽直线网 = 假裂缝。只会以 `c` 为中心放射碎开（`ang` + `spread` 收成扇面），不沿画面里的裂缝走。冲击帧那一拍用最好，每片 ≤3 次。
- 光效融合 `LT.integrate(E, img, layer, blend='over'|'screen', bloom=0.22, fill=0)`（`from src.fx import light as LT`）：加进来的层按底片的柔度模糊、补颗粒、做能量守恒的辉光。
- `SR.particles` 不对镜头开放（调用直接报错）；`SR.overlay` 不对镜头开放，叠层用 `SR.element`。
- 现成小函数（都在 `SR`，用法见 §3b）：`SR.slam(t, n, s0)` · `SR.step(f, F, amp, dur)` · `SR.punch(f, F, amp, dur)` · `SR.whip(E, img, T.f, F, d, px, vertical, sign, ang)` · `SR.push_thru(T.f, F, n, amt)` · `SR.moves(f, M)` · `SR.whip_in(f, F, dist, n)`。
- HUD（少用，线宽 ≤1.4 px、不圈住主体）`SR.ring_hud(cv, x, y, T.f, f0, r=240)`、`SR.callout(cv, x, y, r, 角度, ['A','B'], T.f, f0)`、`SR.readout(cv, x, y, T.f, [(帧,值),...], unit=' K')`。

## 5b. 特效来源和白名单
**来源**：冒充物质的东西（碎片、粉尘、雾、烟、气泡、水滴、裂缝……）只能来自三处——① 实拍：`Take` 当世界层，或 `SR.element` 把黑底实拍抠出来叠上；② 生图：主体参考、远景和纹理，用 `LT.integrate` 融进画面；③ 画面自己派生：`SR.shatter`、位移、折射、模糊、从画面亮度取遮罩。skia 不画任何物质，同一张渲染图一帧只放一次（不复制、压扁、转着拼成别的东西）。包里没有的真材料照任务书「缺真材料就提请求」。`ctx/check.sh` 会列出扫到的 2D 粒子、渲染图复制拼装。

**白名单**（原片自己在用的图形手段，可以用；别常驻）：
| 手段 | 怎么用 |
|---|---|
| 实心字 | 主角字、标签 |
| HUD | 线宽 ≤1.4 px，不圈住主体，一片里最多三分之一的镜头用 |
| 蓄力 / 冲击集中线 | `SR.hit` 自带，≤3 帧，不压主体 |
| 万花筒细边 | `KV.Vortex` / `SR.tx('kaleido')` 的光边，签名色 |
| 展示用空心字 | 一个大字只描边、整帧设计帧里 |
| 整帧设计帧里的图解线、纸窗墨线框 | 纸白 / 墨色整帧设计帧里；纸窗 2 px 墨线，全片 1–2 次，遮罩羽化 ≥8 px |
| 实拍热源喷出的短命火花 | 从实拍的火里喷出，几帧就灭 |
| 冲击帧 | 打在细节丰富的底上 |

线和圈从来不是常驻装饰：要么是一个东西的边，要么是几帧就灭的光事件——冲击波环只活约 9 帧、是画面自己的折射（`SR.hit` 余波自带，不另画圈）；portal 边是高斯剖面的光谱边（`KV.Vortex`）；字的光笔画最多 12 帧，然后变成镂空窗口。

## 6. 签名镜头（ctx/src/shots/，照着改，别重写）
示例里的素材名（K1–K4 这类占位名）、叠层 kind、颜色是示例自己的，改的时候换成 ctx/footage.md、ctx/overlays.json 里的。
| 文件 | 写法要点 |
|---|---|
| `ex_open.py` 开头 10 格 | 一张 `CELLS` 表（素材、秒、风格、放大、焦点、大字、字体、标签）；每格一个 16 分音符 6 帧；风格轮换：实拍单色世界 / `SR.card` 冲击风格 / 纸白设计帧；每格进场 6 帧内推近落定（慢素材照 §3b 每格再加一件单向的事）；第 0 帧 = 海报（全片最后一帧要接回它） |
| `ex_hit.py` DROP 冲击 | `Take` 用 speed 关键帧在冲击处慢放；`voronoi` 砸在小节第 1 拍，`HE.hero(style='slam')` 同帧砸字，字在原位留一拍再切（画面不停：字被推着走或主体接着动）；第 3 拍 `lines` 冲击帧盖住切点换素材 |
| `ex_vortex.py` 时光穿梭 | `KV.Vortex` 1.5 小节；portal 阶段才渲下一画面；字母在 32 分音符上逐个砸入、portal 时飞出 |


## 7. 看画面（ctx/look/，用 Read 看图）
- `ctx/look/self/`：你这一镜最近一次渲出来的画面（编排器在沙箱外渲，每次渲完整个换掉）：`sheet.jpg` 整镜 12 帧、`hit_fNNNNN.jpg` 第 NNNNN 帧（全片帧号）前后三帧。发回时写明看哪张；改之前先看。
- `ctx/look/stock/`：请求到的素材的联系表（到货对不对题，自己看）。
- 你在沙箱里跑的只有 `bash ctx/check.sh`（劣质特效、导入、@shot、Take 越界，不要 GPU）。

## 8. 绝对不要
- 只写 `out/`；不改引擎（`src/` 只读，零件有问题写进 NOTES.md）；不从零写 GLSL，先找零件和签名镜头。
- 不用 `time.time()`、`random`、全局可变状态；要随机用 `from src.kit.core import rnd, rr` 的 `rnd(键...)`（同样的键永远同一个数）。
- 不白闪 / 黑闪当转场，不整屏频闪，不用纯色渐变底、饱和顶满的颜色、贴纸、长黑杠、结尾闪三下、结尾喊口号。
- 不一小节只放一个效果（平均）；不每个 16 分音符都卡；`voronoi` 全片 ≤2 次；万花筒一段至少半小节。

## 9. 转场：镜头表的 transition_in.kind → 引擎函数
每个切点在镜头表里有声明 `transition_in = {kind, eye, why, slug}`（spec 里还有这一镜的 `transition_out` = 下一镜的入点；都带切点帧 `F` 和 `engine`）。只用下表里和 kind 对应的函数，不手写过渡；不收闪黑、闪白、淡入淡出、叠化。

| kind | 引擎（F = 下一镜第一帧） | 两边画面 |
|---|---|---|
| 拍上硬切 | 不用函数：切点就在表里那一帧（`T.F >= F`）；要劲就在这一帧放冲击簇，盖在新构图上（§3） | — |
| 甩 | `SR.tx(F, 'whip', dir='L'\|'R'\|'U'\|'D')`，方向跟画面里的运动走 | 单边 |
| 推穿 | `SR.tx(F, 'push', c=上一镜里推进的点)`；反方向 `'pull'` | 单边 |
| 运动接运动 | `SR.tx(F, 'smear'\|'ramp', dir=运动方向)`；转着接 `'spin'`（全片 ≤2 次） | 单边 |
| 对位 | `SR.tx(F, 'shape', at=上一镜的点, at_in=下一镜的点)`；圆接圆 `SR.tx(F, 'circle'\|'iris', c=圆心)`（circle 的边是 1.4 px 签名色细线） | shape 单边；circle / iris 双边 |
| 遮挡 | `SR.tx(F, 'occlude', dir='R')`；设计感的 `'stripes'` / `'blocks'` | 双边 |
| 冲击帧盖切点 | `SR.tx(F, 'hit', preset='post3_blue', c=(x, y), **MO.hit_cols(P))` | 单边：上一镜蓄力，下一镜冲击 + 余波 |
| 万花筒 | `SR.tx(F, 'kaleido')`；整镜做时光穿梭用 `KV.Vortex`（ex_vortex.py） | 双边 |

- 两镜各做自己那一半：这一镜的 `TXS` 里放入点 `SR.tx(start_f, 入点 kind, …)` 和出点 `SR.tx(end_f, 出点 kind, …)`，参数照声明写（上一镜、下一镜按同一个声明写同样的参数）；`img = SR.tx_frame(E, T, TXS, lambda X: 画(X))`。单边的 kind 只会向你要自己这一侧的帧。
- 双边的 kind 在窗口里要两边画面同时出现：镜头内部的切点直接用；跨镜头时，对方那一侧画一个替身（用前后镜信息里的同一条实拍、同一个大字），或者换成同一 kind 里单边的写法（对位 → `'shape'`；遮挡 → 前景物自己扫过、在切点那一帧挡满）。
- 编排器会在成片上量切点 F±3：闪黑闪白、两镜混在一帧、对位类两侧接不上，审片也会看 F-2、F、F+2 三帧对照声明；这些都只提醒。
