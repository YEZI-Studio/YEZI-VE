## 缺真材料就提请求（编排器在沙箱外去 Pexels、Wikimedia Commons 这些免费素材站找、下、入库；你自己不联网、不问你）
清单和叠层表里没有这一镜要的真材料（某种黑底元素、世界层、主体），不画 2D 替身、不拿不对的素材硬凑：写 `out/footage_requests.json`。这一次先用现有的把镜头做完整（代码里先别引用还没到的素材）；编排器找到了下进素材包，再叫你一次「素材到了，接进去」，到货的联系表在 ctx/look/stock/。到货不对题，就把 need 写得更具体（东西、底色、景别、动作）、query 里写上那样东西的英文名，再提一次，编排器会重新找。

格式：`[{"need": "画面本身：什么东西、什么底色、往哪走、景别、要不要慢动作", "use": "overlay", "seconds": 4, "why": "这一镜哪里要它", "query": "2-6 english words black background", "kind": "小写英文种类名"}]`
- use：`overlay` = 黑底元素，烘焙成新的 SR.element kind；`world` = 世界层（Take）；`subject` = 主体（Take）。query 写英文（overlay 带 black background）；kind 只给 overlay。
- 叠层表（ctx/overlays.json）现有的种类：{{KINDS}}。对题的别再要；用了以后在 ctx/look/self/ 看到不是要的东西（名字和内容对不上），就照样再要一次。
- 数量：这一镜制作、返修时各最多 {{PER_SHOT}} 条、各找 {{ROUNDS}} 次；全片最多 {{PER_FILM}} 条（已用 {{USED}} 条）。只收免费授权（Mixkit 只收 Free License）。
- overlay 到了用任务书写的 kind 名（和现有的同类时是 `<kind>_r<编号>`）；写着「只能整屏用」的整屏铺（pos=None），别放到一点。

这一镜现在的素材请求：
{{MANIFEST}}
