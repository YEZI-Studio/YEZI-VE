# 曲库：你自己的配乐

开跑时编排器从这里挑配乐（系列配置 `p2/orchestrator/series/chroma.json` 的 `music.library` = `曲库`）。
曲子由你自己准备，只放你有权使用的（例如 NCS 这类允许视频使用、要求署名的曲库）；音频文件只留在你自己的机器上，不要提交到任何公开仓库。

## 三步

1. 把曲子（`.mp3` 或 `.wav`）放进 `music/`。CHROMA 要节奏感强、有明显 DROP（低频突然变满）的曲子，125–155 BPM，phonk 最合适。
2. 建议写（用要求署名的曲子时必须写 credit）：在 `music/` 里写一份 `tracks.json`，记来源和署名；报告末尾「署名」一条照它提醒你贴进视频简介。
   不写的话报告会记一条「配乐缺来源和授权」，发片时也没有 credit 原文可贴。格式（键 = 文件名）：

   ```json
   {
     "我的曲子.mp3": {
       "title": "曲名", "artist": "作者", "source": "来源站",
       "page_url": "https://…", "license": "这首曲子的使用条款（一句话）",
       "credit": "要贴进视频简介的署名原文"
     }
   }
   ```

3. 在包根目录运行 `bash p2/tools/music_lib.sh`：用引擎虚拟环境跑 `p2/tools/beats.py`，生成 `beats.json`，以及 `music/` 里每首的
   `<曲名>.events.json` 和 `<曲名>.music_map.json`。加了、换了曲子就再跑一次。

## 编排器怎么挑

只看推荐窗口在全片片长范围内（21–27 秒）、BPM 在 125–155 之间的曲子：先挑 135 BPM 以上的，再挑低频对拍最准的一首，写进这一集的
`inputs/music.json`（暂定，你可以改这个文件再 `--from 1` 重做）。曲库里没有合格的曲子时，启动脚本开跑前就停下来提示
（直接调 `run_episode.py` 时要到声音环节才停）。
