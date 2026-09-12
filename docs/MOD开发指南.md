# MOD 开发指南（从零到能用 · 面向内容作者）

> **本文怎么用**：按你要做的东西挑一节，照步骤做，最后看 §8 生效与排错。
> **契约细节**（字段校验、协议、权限）一律查 **`接口文档.md`**——本文只讲"怎么做"。
> **三种扩展的成本**：内容包（改 JSON）< 皮肤包（改页面）< 工具包（写 Python，默认不加载）。
> **零代码优先**：九成的 mod 需求用**内容包**就能满足——先确认你真需要写代码。

---

## 0. 五分钟：做一个角色 mod

```bash
# ① 复制活示例（它是测试夹具，改坏它 smoke_test 会告诉你）
cp -r qq-bot/packs/example.sakura  data/packs/yourname.sakura
```

② 改 `data/packs/yourname.sakura/pack.json`：

```json
{ "spec": "robot-pack-v1", "name": "yourname.sakura", "version": "1.0.0", "type": "card",
  "title": "你的角色名", "author": "yourname", "license": "CC0-1.0",
  "compat": { "core_api": ">=1.0" },
  "card": { "key": "sakura", "cls": "看板娘", "star": 4, "uni": "YOUR-WORLD",
            "desc": "一句话简介（≤40 字，超长截断）" } }
```

③ 写 `card.json`（照 `qq-bot/data/personas/_template.json` 填，格式 `chara_card_v2`）——

```json
{ "spec": "chara_card_v2", "data": {
  "name": "樱", "description": "角色设定…", "personality": "…",
  "universe": "your-world",          // ★ 建议填：关联场景词库 / 世界观注入 / 同源角色互认
  "sprite_expressions": ["得意", "脸红"],
  "response_rules": ["…"] } }
```

④ 加素材（**必须是真实立绘位，包内 `sprites/` v1 不自动生效**）：

```
launcher/cards/fullw/<卡键>.webp            全身（详情页/主页）
launcher/cards/bust4w/<卡键>.webp           半身（GAL 页/卡片墙）
launcher/cards/bust4w/<卡键>_<词>.webp      半身差分（有几个情绪就放几个）
```

⑤ 导出卡片数据 + 重启：

```bash
cd qq-bot && .venv/Scripts/python.exe tools/export_cards.py     # 追加进 data/cards.json
```

⑥ 重启 bot → `/人设 <卡键>` 切换 → 启动器卡片墙出新卡。

> **没有立绘的卡会被启动器网格过滤**（不报错、不亮卡）。想先看效果，随便丢一张图到
> `launcher/cards/bust4w/<卡键>.webp` 即可。

---

## 1. 五种 mod 速查

| 你想做 | 包类型 | 放哪 | 要不要写代码 | 详情 |
|---|---|---|---|---|
| 一个新角色 | `card` | `data/packs/<作者.名>/` | 否 | §0、接口文档 §3.2 |
| 一套新音色 | `voice` | 同上（或卡包内嵌 `voice.json`） | 否 | §3 |
| 换 GAL 舞台（背景+差分） | `gal` | 同上 | 否 | §4 |
| 换启动器外观 | 皮肤包 | `launcher/web/skins/<名>/` | **写页面** | §5 |
| 新台词 | 卡包 `quotes.json` | 同上 | 否 | 接口文档 §3.2 |
| 新道具 | `item` | 同上 | 否 | 接口文档 §3.4 |
| 世界观/世界书 | `world` | 同上（或内嵌卡包） | 否 | §6 |
| 新情绪基调 | `emotion` | 同上 | 否 | 接口文档 §3.6 |
| 真·新能力（联网/计算） | `tool` | 同上 | **是**（Python） | §7 |

**一个包可以同时是多种**：`type=card` 的包内可以带 `quotes.json` / `voice.json` / `world.json` / `sprites/`。
`type` 只决定**主身份**（谁给你回落、谁给你索引）。

### 1.1 内容落点总表（**"我这东西到底该放哪、界面哪一页能看到"**）

| 内容 | 权威落点 | 装完在哪看得见 |
|---|---|---|
| 角色卡（人设） | `qq-bot\data\personas\<卡名>.json`（本地，**优先**）或内容包 `card.json` | 启动器「干员」页（本地卡与包卡合并列出）；也可私聊 `/人设 卡名` |
| 音色 `voice` | 内容包（`qq-bot\packs\` 随仓示例 / `data\packs\` 用户安装） | 启动器「内容包」页，行尾 `⟨voice⟩` |
| 道具 `item` / 情绪 `emotion` / 世界观 `world` / 工具 `tool` | 同上 | 同上，行尾标 `⟨item⟩` / `⟨emotion⟩` / `⟨world⟩` / `⟨tool⟩` |
| 舞台（背景 + 差分）`gal` | 同上（`type=gal`） | 「内容包」页的**舞台包**组 |
| 皮肤（启动器外观） | `launcher\web\skins\<名>\`（含 `manifest.json`） | 「内容包」页的**皮肤**组；「设置 → 皮肤」切换 |
| 第三方**代码**插件 | `data\plugins\`（子包目录或单个 `.py`；重启 bot 生效） | 「内容包」页的**插件**组 |
| **道具 / 特殊状态的文案**（不是包！） | `data\special_content.json`（**规范根**，gitignored）——状态白名单在代码里 | 没有页面：它是给模型的文案层，见 接口文档 §6.5 |
| 我的实例现在装了什么 | —— | 启动器「内容包」页四组 + 无界面 `Launcher.ps1 -Mode packs` |

> ⚠️ **两个最容易踩的认知差**：
> ① 启动器那页叫「内容包」但**按"给的东西"分四组**（内容包 / 舞台包 / 皮肤 / 插件），**不按 `type` 分组**——
> 所以 `item`/`emotion`/`voice`/`world`/`tool` 五类**都在「内容包」组里**，靠行尾 `⟨type⟩` 区分。
> ② **道具与"特殊状态"不是内容包**：能力（状态白名单、`set_state` 的行为）在 `core/special.py` 里，
> 文案在 `data\special_content.json`；`type=item` 的包是"道具**定义**"（名称/效果/时长），两者互补不重叠。

### 1.2 每类都有一个最小示例可抄

`qq-bot\packs\` 下随仓带**七类各一个**最小示例（实测：`example.sakura`=card、`example.voice`、`example.item`、
`example.emotion`、`example.world`、`example.tool`、`example.stage`=gal，均为 `root=builtin`）：
把整个目录复制到 `data\packs\` 改名，就是"能跑起来的最小包"。**先抄再改**比对着文档从零写快得多。

---

## 2. 内容包的三条铁律（**违反不会报错，但一定不生效**）

1. **`name` 必须点分小写**：`作者.包名`，如 `yourname.sakura`。大写、空格、缺小数点 → 整包跳过（warning 在 `data/bot.log`）。
2. **`version` 必须三段数字**：`1.0.0`。写 `1.0` → 整包跳过。
3. **卡文件必须无 BOM**：用记事本"另存为 UTF-8"时**别选**"UTF-8 带 BOM"。有 BOM → 解析失败。

放好之后：**重启 bot**（或调 `core.packs.reload()`）。`data/packs/` 同名覆盖 `qq-bot/packs/` 的示例包。

---

## 3. 做一套音色（`voice`）

```json
{ "key": "mysound", "name": "我的音色", "lang": "zh", "personas": ["樱"],
  "gpt":   "C:\\GPT-SoVITS\\models\\my.ckpt",
  "sovits":"C:\\GPT-SoVITS\\models\\my.pth",
  "ref_dir": "C:\\GPT-SoVITS\\refs",
  "emo_refs": { "温柔": "温柔参考条目名" } }
```

- `gpt` / `sovits` / `ref_dir` **必须是绝对路径**（相对路径条目会被跳过 + warning）。
- `key` 撞上内置音色（如 `amiya`）时**内置优先**，你的包不生效——换个 key。
- **权重不随发行版分发**（在 `tools/`、gitignored）：你把自己的权重路径写进 `voice.json`。
- `emo_refs` 的键用 9 类口径：战斗/高冷/讥讽/温柔/无语/娇嗔/疑问/纯H/傲娇；缺类自动回「默认」参考音。

---

## 4. 做 GAL 舞台包（`gal` · 背景 + 差分）

**这是"换舞台"的正路**——不要塞进人设卡（卡是文本，背景不属于任何角色），也不要塞进皮肤（皮肤是外壳）。

```
data/packs/yourname.stage-room/
  pack.json
  bg/room_night.webp                 ← 背景（文件名主段就是 id）
  bg/rooftop.webp
  sprites/diff/sakura_Smile.webp     ← 差分：<卡键>_<词>.webp（**命名与既有素材完全一致，可纯拷贝**）
  sprites/manifest.json              ← 可选：{"map": {"温柔": "Smile"}}
```

`pack.json`：

```json
{ "spec": "robot-pack-v1", "type": "gal", "name": "yourname.stage-room", "version": "1.0.0",
  "title": "有窗的房间", "author": "yourname", "license": "私用",
  "compat": { "core_api": ">=1.0" },
  "stage": { "default_bg": "room_night", "bg_alt": "rooftop",
             "bg_by_scene": { "天台": "rooftop", "教室": "classroom" } } }
```

**要点**：
- **场景词是模型自由生成的文本**（来自 lifesim 的 `scene`，如"天台上看星星"）——匹配顺序是 **精确 → 最长键包含 → 默认**，所以键写 `天台` 就能命中"天台上看星星"。**键越短越容易误命中**（写 `天` 会抢走所有含"天"的场景），所以用**具体短词**。
- 背景 id 指向的文件**必须真的存在**：不存在的 id 不会进索引，前端就继续用它自己的默认背景（不会出现空块）。
- 扩展名不用写（`.png/.jpg/.jpeg/.webp/.gif` 自动探测）；写了也只用主名。
- 素材解析优先级：`随仓示例包 < data/packs/<舞台包> < launcher/cards/`（本机私有素材最高）。
- 想给某张卡纠"词→素材词"映射：在舞台包 `pack.json` 里加 `"card": {"key": "<卡键>"}`，并在 `sprites/manifest.json` 写 `{"map": {...}}`——它会**覆盖**该卡包的同名键（不声明 `card.key` 的舞台包其 map 不生效，避免"这份 map 属于哪张卡"的歧义）。

> **已可用**（2026-09-12）：`type=gal` 已实现——背景经 `/gal/stage/<包名>/bg/...` 提供，差分词表按
> `舞台包 sprites/diff/` → `launcher/cards/bust4w` 多根扫描（**包在前**：同一个词优先用包里的图）。
> **活示例在哪**：`example.stage`（随仓 `qq-bot/packs/`；占位背景由 `qq-bot/tools/dev/gen_example_stage.py` 生成，
> 零 IP、一眼可辨）。⚠️ **它不在 SDK 包里**——SDK 发行面禁任何图片（见 SDK 的 RELEASE-NOTES 自检声明），
> 而舞台包天然由图片构成。要抄真实形状，从 **Core 发行包**或源码仓的 `qq-bot/packs/example.stage/` 看。

---

## 5. 做皮肤包（`launcher-skin-v1` · 唯一需要写页面的 mod）

```
launcher/web/skins/<你的皮肤名>/
  manifest.json        必填，见接口文档 §4.1
  index.html           入口页（manifest.entry 指向它）
  ...                  你的 css/js/图片
```

**最小可用**：直接抄 `launcher/web/skins/generic/`（零 IP、零图片、4 个文件）。

**必须遵守的三条**（否则页面会"半死不活"）：

1. 页面上**必须**定义这两个全局函数——启动器会直接调用它们显示遮罩/横幅：
   ```js
   window.showWebBlock = function (text) { /* … */ };
   window.hideWebBlock = function () { /* … */ };
   ```
2. **每秒轮询 `state.json?t=<毫秒>`**（`?t=` 防缓存不可省）拿运行状态；`state.json` 由启动器约 3 秒整体覆写一次。
3. 素材引用用 `https://app.local/...`（= `launcher/web/`）或目录内相对路径；
   **`http://127.0.0.1:8080` 只用于 GAL 页与 bot 通道**。

**谁负责什么（分层规则，设计页面之前先读）**

启动器窗口 = **常驻顶栏**（导航：主页 / 干员 / 设置 / 内容包 / 运行状态 / 日志 / 组件 / 关于，
外加 ▶ 启动、■ 停止、✕ 退出、四服务灯、时钟）＋ **主体**（要么是你的**皮肤页**＝ WebView 显示你的入口文件，
要么是**框架页**＝ WPF 原生）。

| 层 | 拥有 | 不做 |
|---|---|---|
| 顶栏 + 框架页 | 导航、全局操作（启动/停止/退出）、框架自有的视图（设置/内容包/运行状态/日志/组件/关于） | 不决定你的外观；**不顶替你做的页面** |
| 你的皮肤页 | 氛围与只读状态（立绘/场景/心情/计划/台词/服务灯）、**你自己的视图**、用 `postMessage({cmd})` 调框架的按钮 | 不重复全局操作（启动/停止——同一动作两个入口状态迟早对不上）；同一批数据不摆两套界面 |

两条你会真碰到的结论：

- **皮肤可以自己拥有一整个视图**。若你做了自己的干员一览，在 `manifest.json` 里写 `"operaPage": true`：
  顶栏「干员」与 `cmd:"operators"` 就会打开**你的**视图（框架推 `page:"opera"`，你自己渲染）；
  不写这个键才用框架原生干员页。无论哪种，用户只看到**一个**干员页。
- **语言跟着启动器走**（2026-09-12 全局语言切换）：`state.json` 新增 `lang` 字段（`zh`/`en`，来自启动器设置页）。
  皮肤据此换自己的文案——generic 样板的做法是在 HTML 上打 `data-i18n="键"` 标记 + 一张 `STR` 表（不复制页面，避免两份漂移）；
  跳 GAL 页时把这个值带上（`?lang=<值>`），GAL 页自己也认这个参数。**缺 `lang` 字段 = 按中文**（旧启动器兼容）。
- **主页总会回到你的入口文件**。顶栏「主页」发现 WebView 停在别处（GAL 页、或加载失败的错误页）时，
  会自动导航回你的 `manifest.entry`。所以别假设你的页面会常驻整个会话——状态放 `state.json` 轮询里，
  不要只放在页内变量里。

**GAL 页需要 bot 在跑**：`http://127.0.0.1:8080/gal` 由 bot 提供，所以皮肤上的 GAL 按钮只有 bot 起来才打得开。
端口 8080 不通时，启动器会**拦下这次跳转**并提示"机器人未启动…"，而不是让 WebView 落到错误页
（错误页是死角：它不轮询 `state.json`，也不认主页键）。你也可以在自己页面里加同样的前置判断——
`state.json` 的 `svc.bot` 就是"bot 在不在"：

```js
if (state.svc && state.svc.bot === false) { /* 在你自己的界面里提示"先启动机器人" */ return; }
```

**可选但常用**：`window.GAL_SKIN = { assets: {...} }` 覆盖 GAL 页素材（背景等）。
注意 `/gal/content.json` 的键（`quotesByCard`/`quoteFallback`/`spriteMap` + 舞台包用的 `stage`）**后到覆盖**你的设置；
其中 `quoteFallback` 的读点是模块变量而不是 `ASSETS`（已单列采纳，见皮肤包规范 §7）。

> 完整字段表（含 `operaPage`）、整条命令通道（`start`/`stop`/`stopbot`/`exit`/`opera`/`home`/`operators`/
> `cfg`/`plugins`/`log`/`state`/`deps`/`about`/`setpersona`）、state.json 字段与分层小节：
> **接口文档 §4** + `皮肤包接口规范-v1.md`（§2 字段表、§4 命令通道、§9 分层）。

---

## 6. 世界观包（`world`）——让角色"知道"自己的世界

**两个文件**：`pack.json` 只声明身份，设定文本放同目录的 `world.json`（**必须叫这个名字**，加载器按文件名读）：

```jsonc
// <包目录>/pack.json
{ "spec": "robot-pack-v1", "type": "world", "name": "yourname.world", "version": "1.0.0" }

// <包目录>/world.json          ← 内容在这里；universe / entries 在**根级**
{ "universe": "your-world",
  "entries": [ { "text": "这个世界里…", "always": true, "priority": 0 } ] }
```

> ⚠️ **2026-09-12 两处订正（本文旧样例照抄了不会生效）**：
> ① 设定文本**不在** `pack.json` 里，也不在 `pack.json` 的 `"world": {…}` 子对象里——`core/packs.py` 的
> `_build_world` 只读 `pk.read_json("world.json")`；② 就算写进 `world.json`，`universe`/`entries` 也必须在**根级**
> （包一层 `"world": {…}` 会得到 `world.json skipped (no universe)`）。
> 两处错了都不会报错、只是**静默不注入**。可抄的活样例：`qq-bot\packs\example.world\`（`pack.json` + `world.json`）。
> 审计只核"文档写了、代码有没有"，不核"照文档抄能不能跑"——这一条属于后者，是人读出来的。

或直接内嵌进卡包（放 `world.json`，字段同上不含 `spec`/`type`）。

- `universe` 要和卡的 `universe` 字段**对齐**，否则不注入。
- **v1 只注入 `always: true` 的条目**（整块上限 800 字）。`keys` 关键词触发是 v2 预留，现在填了不生效。
- 层次：`world` 包 = **设定文本**；`data/universe_roles.json` = **角色关系**；`qq-bot/data/scenes.json` = **场景词库**。三者以 `universe` 键对齐，互补不重叠。

---

## 7. 做工具包（`tool` · 写 Python，**默认不加载**）

只有当你需要**真正的代码能力**（联网取数据、复杂计算）时才走这条。**先想清楚**：多数需求是内容包能解决的。

```json
{ "spec": "robot-pack-v1", "type": "tool", "name": "yourname.weather", "version": "1.0.0",
  "permissions": ["net:api.example.com"],
  "limits": { "timeout_s": 5, "result_kb": 64 },
  "tools": [ { "entry": "entry.py", "cards": ["weather.now"] } ] }
```

`entry.py` 里按 `ToolCard` 契约注册（接口文档 §6.1）：

```python
from agent.registry import register, FAIL_RETURN_NONE

@register(name="weather.now", description="查当前天气", failure=FAIL_RETURN_NONE)
async def now(city: str): ...
```

**三条硬约束**：

1. **默认不加载**：用户要显式开 `PACKS_ENABLE_PY`。**给普通用户的包不要依赖它**。
2. **必须声明权限**：缺省 `permissions: []` = **零能力**（不能读文件、不能联网、不能用模型）。
   声明由**内核**执行（AppContainer + ACL）：未声明的文件读不到、写不了；**子进程网络恒为 0**，要联网得声明 `net:` 并由主进程代取。
3. **不能指望主进程环境**：子进程不继承 `.env`、拿不到 API Key、拿不到主进程内存。

> 沙箱设计与边界：**接口文档 §6.3**；完整规格 `S4-沙盒与声明式权限-spec-2026-09-12.md`。

---

## 8. 生效、测试与排错

### 8.1 生效规则

| 改了什么 | 怎么生效 |
|---|---|
| 包内任何文件 | **重启 bot**（或 `core.packs.reload()`） |
| `data/personas/*.json` | 重启 bot |
| 立绘素材（`launcher/cards/`） | **刷新/重启启动器**（bot 不用重启） |
| 皮肤 | 设置页换皮肤 = **立即重导航**，不用重启 |
| `.env` | **必须重启 bot**（Python 不热加载配置） |

### 8.2 自测清单

- [ ] `pack.json` 能被 JSON 解析（用编辑器校验，别手改出尾逗号）
- [ ] `name` 点分小写 / `version` 三段数字
- [ ] 卡文件无 BOM
- [ ] 卡键不与已有卡重复（重名时**本地卡优先**，你的包卡会被忽略）
- [ ] 立绘素材名与卡键一致（`<卡键>.webp`）
- [ ] 差分素材名是 `<卡键>_<词>.webp`，且 `<词>` 在 `sprite_expressions` 或 `sprites/manifest.json` 的 `map` 里
- [ ] 跑一次 `tools/export_cards.py`（卡包）
- [ ] 改完**重启 bot**，看 `data/bot.log` 有无你包名的 warning

### 8.3 排错

| 现象 | 原因 |
|---|---|
| 包完全不生效，`bot.log` 有该包名 warning | `spec`/`name`/`version`/`type` 校验不过——报错文本直接写了原因 |
| `/人设` 里没有你的卡 | 卡包没被扫到（重启过没？）或本地有同名卡把它盖了 |
| 卡出来了但**卡片墙不亮** | **没有立绘**（网格会过滤无图卡）；或没跑 `export_cards.py` |
| 立绘不显示 | 文件名不是 `<卡键>.webp` / 不在 `launcher/cards/bust4w/` / 启动器没刷新 |
| 差分永远不换 | 文件名不是 `<卡键>_<词>.webp`，或 `<词>` 不在映射表里（**软校验：表外的词会被丢弃**） |
| 台词不出现 | `quotes.json` 不在包根 / 格式不是 `{"quotes":[{"q","by"}]}` / 被 `data/quotes.json` 整键覆盖了 |
| 音色不生效 | `key` 撞了内置音色（内置优先）/ `gpt`/`sovits` 不是绝对路径 |
| 世界观不注入 | 卡的 `universe` 与包的 `universe` 不一致 / 条目没有 `always: true` |
| 工具包不跑 | `PACKS_ENABLE_PY` 没开 / 未声明 `permissions` / 沙箱不可用（沙箱**尚未实现**，见 `docs/S4-沙盒与声明式权限-spec-2026-09-12.md`；实现后审计写入 `data/packsandbox_audit.jsonl`） |

---

## 9. 发布你的 mod（**先读这节再发**）

### 9.1 许可与 IP 红线

- 内容包**自身**要写 `license`（`CC0-1.0` / `MIT` / `私用` 都行，但**要写**）。
- **不要包含第三方 IP 素材**（动漫/游戏角色立绘、游戏 UI 图）。项目有自动检查：
  `build/build_release.ps1` 的 SDK 自检会扫描 IP 词表，**命中即构建失败**。
- 你自己画的/生成的素材：确认你有权分发。
- **成人向内容**：走**私有分发**（`license: 私用`，别进公开仓）。

### 9.2 打包与分享

内容包就是**一个目录**——直接压缩该目录分享，对方解压到 `data/packs/` 即用。
**不要**把包放进 `qq-bot/packs/`（那是随仓示例，更新发行版会覆盖）。

### 9.3 让别人能看懂你的包

- `title` / `author` / `note` 都填上（启动器与文档会展示）。
- 包里放一个 `README.md` 说明：依赖什么、怎么装、已知问题。
- 用 `_` 前缀写给机器/自己看的说明键（如 `"_说明"`），与未来规范字段不冲突。

---

## 10. 想深入？

| 主题 | 去哪 |
|---|---|
| 全部字段与协议 | `接口文档.md` |
| 皮肤包完整细节 | `皮肤包接口规范-v1.md` |
| 框架怎么跑起来、日志在哪 | `维护手册.md` |
| 代码结构与决策史 | `HANDOFF.md`、`docs/archive/` |
| 活示例（改坏会被测试抓到） | `qq-bot/packs/example.sakura/`（卡包）· `qq-bot/packs/example.stage/`（舞台包） |
| 你要扩展的代码在哪 | `qq-bot/plugins/`（框架自带）、`data/plugins/`（你的，永不被覆盖） |
