# NOTICE：第三方组件、素材和不随包分发的材料

> 本软件的许可（LICENSE）**不适用于**下面的第三方材料，也不限制它们自身许可给你的权利（LICENSE 第 7 条）。第三方条款以各自官网当前版本为准。

## 0. 一屏结论

| 类别 | 随包分发？ | 你怎么拿到 |
|---|---|---|
| 引擎自带 8 款字体（全部 SIL OFL 1.1） | 随包，附 OFL 全文（`OFL.txt`）和每款版权声明 | 随包 |
| 色匹配函数表 `p2/chroma/engine/assets/data/ciexyz31_1.csv` | 随包；按公开的解析近似公式生成的数据，不是 CIE 原表（见 1.2） | 随包 |
| Python 依赖（`p2/chroma/engine/requirements.txt`） | 不随包 | 你按 SKILL.md 用 uv 安装 |
| ffmpeg、uv、Claude Code、Codex CLI | 不随包 | 你自己安装、自己同意其条款 |
| 配乐 | 不随包 | 你自己准备有权使用的曲子，放进曲库（SKILL.md「自带配乐」） |
| Pexels / Mixkit / Wikimedia Commons（以及你配了 key 的 Pixabay、开启时的 NASA）实拍 | 不随包 | 跑片时素材请求通道用 `p2/tools/stock.py` 自己找、下 |

## 1. 随包分发的第三方组件

### 1.1 字体：`p2/chroma/engine/assets/fonts/`

8 个字体文件，许可按每个文件自带的 name 表写（ID 0 版权、ID 13 授权说明、ID 14 授权链接）。Smiley Sans、Noto CJK 有保留字体名（Reserved Font Name，见下表，原文照上游 LICENSE）；本包没改字形，按原名原样分发。

| 文件 | 字体 | 版权（name ID 0） | 许可 | 大小 |
|---|---|---|---|---|
| `Archivo-ExpandedBlack.ttf` | Archivo Expanded Black | Copyright 2020 The Archivo Project Authors (https://github.com/Omnibus-Type/Archivo) | OFL 1.1（ID 14：http://scripts.sil.org/OFL；ID 13 为空） | 109 KB |
| `DMMono-Medium.ttf` | DM Mono Medium | Copyright 2020 The DM Mono Project Authors (https://www.github.com/googlefonts/dm-mono) | OFL 1.1 | 49 KB |
| `Inter.ttf` | Inter（可变字体） | Copyright 2016 The Inter Project Authors (https://github.com/rsms/inter) | OFL 1.1 | 856 KB |
| `JetBrainsMono-ExtraBold.ttf` | JetBrains Mono ExtraBold | 2020 The JetBrains Mono Project Authors (https://github.com/JetBrains/JetBrainsMono) | OFL 1.1 | 109 KB |
| `NotoSansSC-Black.otf` | Noto Sans CJK SC Black | © 2014-2021 Adobe (http://www.adobe.com/)；保留字体名 'Source'（上游 LICENSE：Copyright 2014-2021 Adobe (http://www.adobe.com/), with Reserved Font Name 'Source'.） | OFL 1.1 | 17.0 MB |
| `NotoSerifSC-Black.otf` | Noto Serif CJK SC Black | © 2017-2024 Adobe (http://www.adobe.com/)；保留字体名 'Source'（上游 LICENSE：Copyright 2017-2024 Adobe (http://www.adobe.com/), with Reserved Font Name 'Source'.） | OFL 1.1 | 22.9 MB |
| `SmileySans-Oblique.ttf` | 得意黑 Smiley Sans Oblique | Copyright © 2022–2024 atelierAnchor；保留字体名 Smiley、得意黑（上游 LICENSE：Copyright (c) 2022--2024, atelierAnchor <https://atelier-anchor.com>, with Reserved Font Name <Smiley> and <得意黑>.） | OFL 1.1 | 2.5 MB |
| `Unbounded-Black.ttf` | Unbounded Black | 2022 The Unbounded Project Authors (github.com/googlefonts/unbounded) | OFL 1.1 | 387 KB |

- OFL 1.1 允许和软件捆绑分发，条件是附上版权声明和许可全文、不单独出售字体文件、改过字形的衍生字体不用原名。成片里渲出来的字不受限制。
- 字体仍按 OFL 授权，LICENSE 的限制不适用于字体本身。许可全文见根目录 `OFL.txt`，各款版权见 `fonts/LICENSES.md`。

### 1.2 色彩数据：`p2/chroma/engine/assets/data/`

| 文件 | 内容 |
|---|---|
| `ciexyz31_1.csv` | CIE 1931 2° 标准观察者色匹配函数的解析近似值（360–830 nm，1 nm 间隔），按 Wyman, Sloan & Shirley,「Simple Analytic Approximations to the CIE XYZ Color Matching Functions」, JCGT 2(2), 2013 的多瓣分段高斯公式计算，不是 CIE 或 CVRL 发布的原表 |
| `blackbody_srgb.npy`、`blackbody_srgb.json` | 不随包；引擎第一次运行时由上面的数据算出来 |

## 2. 你自己安装、不随包分发的软件和服务

### 2.1 Python 依赖：`p2/chroma/engine/requirements.txt`

不打包 wheel，你按 SKILL.md 用 uv 建虚拟环境安装。下面的许可是各项目通常的许可，以你安装的版本自带的许可文件为准。编排器（`p2/orchestrator/`）只用 Python 标准库。

| 包（版本按 requirements.txt） | 通常的许可 | 备注 |
|---|---|---|
| skia-python 144.0.post2 | BSD-3-Clause | Skia 本身 BSD |
| numpy 2.5.4、scipy 1.18.1、scikit-learn 1.9.1 | BSD-3-Clause | wheel 里带 OpenBLAS 等运行库 |
| opencv-python-headless 5.0.0.93 | OpenCV：Apache-2.0；打包脚本：MIT | wheel 里带 FFmpeg（LGPL-2.1）等库 |
| moderngl 5.13.0、glcontext 3.1.0 | MIT | |
| librosa 1.0.0 | ISC | |
| soundfile 0.14.0 | BSD-3-Clause | wheel 里带 libsndfile（LGPL-2.1） |
| soxr 1.1.0 | LGPL-2.1-or-later | |
| pyloudnorm 0.2.0 | MIT | |
| numba 0.68.0、llvmlite 0.50.0 | BSD-2-Clause | llvmlite 带 LLVM（Apache-2.0 WITH LLVM-exception） |
| matplotlib 3.11.2 | Matplotlib License（PSF 式） | |
| pillow 12.3.0 | MIT-CMU（HPND） | |
| fonttools 4.66.1 | MIT | |
| requests 2.34.2 | Apache-2.0 | |
| urllib3 2.8.0、charset-normalizer 3.5.2、cffi 2.1.1、pyparsing 3.3.3、six 1.17.0、narwhals 2.27.1、platformdirs 4.13.0 | MIT | |
| idna 3.20、pycparser 3.11、pybind11 3.1.0、contourpy 1.4.0、cycler 0.12.1、kiwisolver 1.5.1、joblib 1.6.0、threadpoolctl 3.7.0、pooch 1.9.0、lazy-loader 0.6、cloudpickle 3.1.2 | BSD-3-Clause | |
| decorator 5.3.1 | BSD-2-Clause | |
| certifi 2026.7.22 | MPL-2.0 | |
| msgpack 1.2.3 | Apache-2.0 | |
| python-dateutil 2.9.0.post0 | Apache-2.0 / BSD-3-Clause 双许可 | |
| packaging 26.3 | Apache-2.0 / BSD-2-Clause 双许可 | |
| typing-extensions 4.16.0 | PSF-2.0 | |

### 2.2 外部工具和服务

| 组件 | 许可 / 条款 | 随包？ | 备注 |
|---|---|---|---|
| ffmpeg / ffprobe | LGPL-2.1+ 或 GPL（看编译选项） | 不随包 | 你自己装（Homebrew 等）；引擎固定从 `~/.local/bin/` 调用，装完按 SKILL.md 建软链 |
| uv | Apache-2.0 / MIT | 不随包 | 建引擎虚拟环境 |
| Claude Code CLI | Anthropic 专有软件，适用 Anthropic 的条款 | 不随包 | DeepSeek 后端的写代码代理通过它跑（指向 DeepSeek 的 Anthropic 兼容接口）；你自己安装、自己同意条款 |
| Codex CLI | Apache-2.0（OpenAI），使用适用 ChatGPT / OpenAI 条款 | 不随包 | Codex 后端；你自己 `codex login`（ChatGPT 账号） |
| DeepSeek API | DeepSeek 开放平台条款 | — | 你自己的 key（`p2/setup_key.sh` 由你本人运行），费用自负 |
| Pexels（sitemap / 官方 API） | Pexels License、Pexels API 条款 | — | `stock.py` 用 robots.txt 允许的 sitemap 建本地索引；有你自己的 `PEXELS_API_KEY` 时走官方 API；建索引前请自己确认符合 Pexels 当前条款；用官方 API 时按其要求显示来源 |
| Mixkit（sitemap） | Mixkit Stock Video Free License | — | 同上 |
| Wikimedia Commons API | 每个文件自己的许可 | — | `stock.py` 只收 CC0、公有领域、CC BY；不收 CC BY-SA / NC / ND |
| NASA Images API（默认关）、Pixabay API（要你自己的 key） | 各自条款 | — | |

## 3. 不随包分发、由你自己获取的素材

### 3.1 配乐

- 本软件不带任何音频。你自己准备有权使用的曲子，放进曲库 `p2/assets/曲库/music/`，用 `p2/tools/music_lib.sh` 生成节拍表（SKILL.md「自带配乐」）。生成的 `beats.json`、`*.events.json`、`*.music_map.json` 是从音频分析出来的节拍数据，不含音频。
- 用 NCS（NoCopyrightSounds）的曲子时按它的使用政策（https://ncs.io/usage-policy ）：独立创作者的视频可以免费用，条件是在视频简介里贴 credit 原文；商业广告、品牌投放不在免费范围。音频文件本身不要再分发。
- 交付报告末尾「署名」一条会提醒你：配乐的 credit 在本集 `inputs/music.json`（来自曲库 `music/tracks.json`），发片时把原文贴进简介（LICENSE 第 4.2(a) 条）。

### 3.2 实拍：Pexels / Mixkit / Wikimedia Commons（以及 Pixabay、NASA）

- 不随包分发原素材：Pexels License 不允许把素材原样再分发或放到别的素材平台；Mixkit 免费许可不允许把素材作为独立文件再分发；Commons 的素材按各自许可。
- 你怎么拿到：跑片时素材请求通道用 `p2/tools/stock.py` 去这些站找、下，存进 `p2/assets/<母题>/`（`sources.json` 记站点、编号、页面地址、许可、用途）；也可以自己运行 `python3 -I p2/tools/stock.py fetch <站>:<编号> --pack p2/assets/<母题>`。
- `stock.py` 从 Wikimedia Commons 只收 CC0、公有领域、CC BY；不收 CC BY-SA / NC / ND。用了 CC BY 素材要署名：交付报告末尾的「署名」一条会提醒你，作者和许可在本集 `inputs/footage.json` 的 license 里。
- Pixabay（只在你配了自己的 key 时用）：按 Pixabay Content License，不把原文件当独立文件再分发。
- NASA（默认关）：按 NASA 的媒体使用指南，不暗示 NASA 背书。
