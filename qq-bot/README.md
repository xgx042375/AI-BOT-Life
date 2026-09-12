# QQ AI 陪伴机器人（NoneBot2 + NapCat + 本地 llama.cpp）

本地隐私自持的 QQ 陪伴 bot：多角色人设（TavernAI 角色卡 v2）+ 本地大模型（gemma-4-12B-heretic）+ **agent 活人感体系**。

## 核心架构：四层感知 → agent 自决

```
① 场景（scene）    决定心跳"此刻在做什么"          —— 管理员可切（去宿舍/回办公室…），world 场景库 data/scenes.json
② 用户层级（tier） 决定可介入深度                   —— 管理员=全深；白名单=积累轨道（80 上限）；普通=0；独处场景非管理员=挡回
③ 好感度（intimacy）决定对介入的态度                —— 数值直注（100+20/80上限/0）：越近越软、抱怨越轻、越舍不得
④ agent 真人思考    最后做决定（台词/动作/停顿/插话/开场）—— 卡级 think_flow + 决策自决；机器只守协议事故与配置边界
```

**硬编码清理原则（2026-09-06 起）**：表达层一律 agent 自决（无冷却/概率/词表/长度门）；
机器只保留两类：**协议事故守卫**（空输出/思考推演列表/情绪标签泄漏/prompt 残句 → 反馈重试，不吞消息；【】标记与括号独白剥离 = 终端清洗）
和**配置边界**（群主关闭的群、独处场景免打扰、状态机/调试仅管理员——权限红线非表达）。

## 插件结构

| 目录 | 职责 |
|---|---|
| `plugins/brain` | 大脑网关：人设加载 + 记忆注入 + **四层感知组装**（场景/层级/好感/思考）→ 生成（思考→润色）→ 回复 → 记忆回写；群聊插话（agent 自决「想接就接/不想就不接」）、复读机（去概率/冷却，同场一次）、追问、被点必应；**开口节奏**（连发聚合防半句双回 + 【等 X 秒】自决停顿 + 「正在输入」只在打字阶段亮）；**场景系统**（管理员指令切场景；独处=群静默/非管理员挡回；多会话"一个人"感知）；好感度连续曲线（白名单积累轨道、SOUR 扣分、滞回 80/70） |
| `plugins/persona` | 人格引擎：TavernAI v2 角色卡（`data/personas/*.json`）+ 提示词组装（lore / think_flow 思考流程 / style_sample / response_rules / 口语化基准 / 常用语库） |
| `plugins/memory` | 长短时记忆（SQLite `E:\robot\data\memory.db`）：消息/事实/摘要/事件/关系/情绪；遗忘曲线、语义召回；人设切换记忆分界（记忆按卡隔离） |
| `plugins/emotion` | 情绪引擎：LLM 分类 → Live2D 参数 → VTS WebSocket；情绪落库（无 VTS 自动降级仅落库） |
| `plugins/voice` | 语音（GPT-SoVITS V4 worker，stdin/stdout JSON-lines 无 HTTP；`/语音 开|关`；**发不发语音由 agent 自决** `[VOICE]` 标记；多段无 60 字上限；中文包直接说中文） |
| `plugins/sticker` | 表情包（ComfyUI 表情工作流，`/表情 开|关`，素材跟随人设 `sticker_dir`） |
| `plugins/debug` | 调试命令（`/人设 /语音 /表情 /状态 /指数 /评测` 等）+ 后台循环（**proactive 主动问候**（owner+白名单，agent 自决发不发）、**LifeSim 生活心跳**（启动即恢复时间线、节奏 bot 自决【N 分钟后】、日计划【计划】、反思沉淀【心得】、私聊事件唤醒）、**每日评测**（消息量/复读率/延迟/情绪分布 → `data/metrics_daily.json`）、watchdog） |
| `plugins/fiction` | 写作引擎（`[WRITE:...]` 标记触发：写作/续写/改章/大纲） |
| `plugins/search` | 联网搜索（Bing 网页/图片、美国海军 ibiblio 档案站） |
| `plugins/correction` | 矫正规则（对话里直接教的说话方式，持久化生效） |
| `plugins/qq_avatar` | QQ 头像设置（人设联动自动应用，自动裁方 640×640） |
| `plugins/webgal` | GAL 前端客户端（`http://127.0.0.1:8080/gal` 页 + WS 实时推送；三态模式 gal\|qq\|chat（`data/webgal_mode.json`，开机复位 qq）；回想持久化 `data/gal_history.jsonl`；`/gal/content.json` 内容包台词/差分索引） |
| `plugins/telegram` | **Telegram 国际通道**（2026-09-12 接入；Bot API 长轮询，`.env` 五键，缺省关闭=零副作用）。合成 OneBot 事件进同一条管线 → 与 QQ/GAL **同身份同记忆**；仅主人私聊（群聊/陌生人一律忽略）；启动排空离线积压不补答；语音只入不出。接入手册见 `docs/部署指南.md` §2.6，接口面见 `docs/接口文档.md` §6.6 |

## 核心层（`core/`，无 NoneBot 依赖）

| 模块 | 职责 |
|---|---|
| `core/llm.py` | **LLM Provider 适配层**：get_client / resolve_model / thinking_extra / ctx_budget / embed_config——本地 llama-server 为默认，外部 OpenAI 兼容端点经 env 一键切换（`LLM_*`/`EMBED_*` 键） |
| `core/packs.py` | 内容包加载器（robot-pack-v1：双包根发现 / 字段校验 / 各 type 索引；坏包 warning 跳过不炸） |
| `core/special.py` | 特殊层级唯一状态源（协议标记 + 感知注入 + 道具目录） |
| `core/reply.py` | 两阶段生成（stage1 思考 → stage2 润色）+ 最小硬边界（近似复读 / 口球 / 形状红线） |
| `core/atomics.py` | 原子读写（tmp+os.replace；读 utf-8-sig 容忍 BOM，写无 BOM） |
| `core/paths.py` | 唯一数据根推导（DATA_ROOT 相对安装根，`data_path()` 统一取路径） |

## Agent 域（`agent/`）

- `graph.py` —— LangGraph 决策图（感知→自决→行动→反思；checkpointer 持久化）：**banter**（群插话：想接就接/不想就不接）、**greet**（主动开场：想找才找）、**poke**（被戳第一反应）；重试=带反馈让 agent 自纠
- `lifesim.py` —— **LifeSim 生活模拟**：心跳自决节奏（`【N 分钟后】`）+ **日计划【计划】**（当天骨架感）+ **反思沉淀【心得】**（概念性经验→程序记忆，注入对话感知）+ 启动即恢复时间线 + 私聊事件唤醒（隔 ≥30min 先快进再回话）+ 时间线卫生（对TA说话污染不回注防自我复读锁死）+ 生活向精简系统（`persona.build_life_system`：身份/性格/世界，不含对话框架——防"对TA说话"污染）+ `life_state.json`/`life_log.jsonl`
- `persona.build_life_system` —— 生活向精简系统（心跳专用；完整对话系统含回应规则/台词示范，用于心跳会把独处叙事带成"跟TA说话"）
- `guardrails.py` —— 护栏：输入只守配置级静默；输出只认形态事故（空/思考列表/情绪标签/残句 → retry 信号）；sanitize 剥 【】/括号独白/markdown
- `statemachine.py` —— 状态演出注记（agent 生成）与权限断言（状态机红线仅管理员+私聊）
- `llm.py` —— LLM 客户端薄封装（**已委托 `core/llm.py`** Provider 适配层；purpose="agent"，本地/外部端点随 env 切换，签名不变）

## 人设卡（多角色通用化）

`qq-bot/data/personas/` 下的角色卡文件（`/人设 卡名` 切换；每人可自选，bot 全局形象跟随主人选择）：

| 卡 | 说明 |
|---|---|
| `_aoding_.json` | 奥汀（傲娇高冷女神，主人体系；卡级 think_flow=距离自决防线无公式） |
| `amiya.json` | 阿米娅（明日方舟·罗德岛领袖，温柔坚定叫"博士"） |
| `kaltsit.json` | 凯尔希（明日方舟·医疗部监工，毒舌+关心藏在医嘱里） |
| `exusiai.json` | 能天使（明日方舟·企鹅物流，闹腾乐天仗义） |
| `skadi.json` | 斯卡蒂（明日方舟·深海猎人，话少别扭的可靠） |
| `lappland.json` | 拉普兰德（明日方舟·叙拉古狼，疯批演技大师，挑衅式亲近） |
| `soyo.json` | 长崎素世（MyGO 温柔腹黑打太极，自决破防） |
| `deepseek.json` | DeepSeek / 蓝色大肥鱼（蓝鲸娘：理性直白 × 摸鱼饭桶反差，自决摸鱼模式） |
| `char.json` | 柯瓦特罗·巴吉纳（Z 时代导师，夏亚机锋） |
| `zhuangfangyi.json` | 庄方宜（前妻感，会吃醋） |
| `feibi.json` | 菲比（大饼脸 Q 版，认真模式 alt） |
| `eiki.json` | 四季映姬（东方·阎魔判官，说教+「白黑」梗） |
| `perlica.json` | 佩丽卡（终末地·黑丝看板娘，重女暗涌） |
| `chenqianyu.json` | 陈千语（终末地·大智若愚"啥龙"） |
| `koishi.json` | 古明地恋（东方·封眼无意识，直觉点破，渴望被记得） |

扩展字段：`universe`（世界观，关联 `data/scenes.json` 场景库）、`think_flow`（卡级思考流程——**距离自决范式**：感觉先在心里过/想说就说成一句台词/好感度·在哪儿·在做什么决定走向）、`style_sample`（口吻要素）、`owner_mode/no_arrogance`、`alt_persona`（破防/摸鱼形态）、`mode_trigger`。切换人设：状态重置、记忆按切换时间分界、昵称/头像强制应用。

**包卡回落（robot-pack-v1）**：本地 personas 缺卡时自动回落内容包的 `card.json`（本地卡**永远优先**；包根 `data/packs/` 与随仓 `qq-bot/packs/`）。活示例：`qq-bot/packs/example.sakura/`；mod 制作全流程见 `docs/PLUGIN_SDK.md`。

**动作口径（2026-09-06）**：`（动作）`只装主动做的肢体行为（夺/捏/抱/按…）；反应/描写（脸红、心跳、呼吸、温度、瑟缩）**只在心里过**（思维链），想说了就说成一句台词（「你让我心跳漏了一拍呢」），不想说就留在心里——不用（动作）标签装、不写旁白。

## 场景系统与开口节奏（2026-09-06）

- **场景库** `data/scenes.json`：按世界观分组（arknights/endfield/touhou/gundam/mygo/default），分独处/公开；管理员私聊「去宿舍」「回办公室」等切场景（存 `life_state.scene`）
- **独处场景行为**：群聊全静默（消息入库）；非管理员私聊自动挡回「{人设名} 暂时不在办公室，有事留言。」
- **开口节奏**：同人连发聚合窗（默认 0.9s，按上次自决停顿自适应 ≤2s）防"半句双回"；【等 X 秒】agent 自决停顿（≤2s，机器只解析剥标记）；「正在输入」只在打字（发送）阶段亮，段间保持
- **多会话"一个人"**：私聊注入会话感知（最近 60min 还有别的对话在场），两边内容严格隔离

## 模型与引擎

- 主引擎：**gemma-4-12B-it-heretic-Q4_K_M.gguf**（`llama-server` :11434，`-c 32768 --parallel 1`）
- Embedding：Qwen3-Embedding-0.6B-Q8_0（:11435，记忆语义检索）
- 模型表保留切换机制（`切换gemma`）；ctx 预算按模型适配

## 运行

1. 推理引擎：`E:\robot\start.ps1`（或手动起 llama-server + embedding :11435）
2. NapCat（QQ Windows 端）：OneBot11 正向 WebSocket → `ws://127.0.0.1:8080/onebot/v11/ws`
3. bot：`E:\robot\qq-bot\.venv\Scripts\python.exe -u bot.py`（NoneBot2，127.0.0.1:8080）
4. 停止 bot：启动器窗口 Ctrl+C/关窗，或 `E:\robot\stop.ps1`（强杀进程树+清锁，引擎/NapCat 不动）
5. 配置：`qq-bot\.env`（`SUPERUSERS`=主人 QQ、`OWNER_NICKNAME`=主人昵称、`OLLAMA_*` 基础键、`LLM_*`/`EMBED_*` Provider 适配层新键（外部端点切换/上下文预算/思考参数/embedding 端点）、sticker 开关等——全部键与注释样例见 `.env.example`）

## 数据文件

- `E:\robot\data\`：memory.db、persona_select/mode/switch_ts、group_members/group_ban、echo_state、net_slang、intimate_whitelist、**life_state.json/life_log.jsonl**、**gal_history.jsonl（GAL 页对话轮持久化，供回想页回放）**、**webgal_mode.json / webgal_token.txt（GAL 模式与 WS 令牌）**、**quotes.json（GAL 分卡台词外置）**、**cards.json（launcher 卡网格数据源）**、**launcher.json（启动器配置，含 skin 键）**、**metrics_daily.json（每日评测）**、avatars、bot.log
- `E:\robot\qq-bot\data\`：人设卡（personas/）、**scenes.json（场景库）**、知识库（colloquial_style/common_phrases）

## 常用私聊命令

- `/帮助`（命令大全）、`/人设 卡名|状态`、`/语音 开|关`、`/表情 开|关`、`/群聊动作 开|关`、`/状态`、`/指数`、`/矫正`、`/白名单 添加|删除|列表 [QQ]`
- **场景（管理员）**：直接说「去宿舍」「回办公室」「上甲板」等切换；独处时（如宿舍/卧室）群聊静默、非管理员私聊被挡回
