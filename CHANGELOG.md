# Changelog · 变更记录（行为备份）

> **这个文件是干什么的**：记录**改变可见行为 / 接口 / 默认值**的变更，按日期倒序。
> 每条写清四件事——**改了什么 / 为什么 / 行为上有什么不同（含默认值变化）/ 怎么验证**。
> 目的：某天发现"行为和以前不一样了"，能在这一页查到原因与范围，而不是翻提交历史猜。**这就是"行为备份"的意思。**
> 纪律：只记**行为**（用户能观察到的东西）与**接口**（第三方要照抄的契约）的变化；
> 纯内部重构、注释与格式改动不进来（那些看 git log 即可）。

---

## 2026-09-12 · 启动器 3.9.90 ｜ Telegram 通道 ｜ 在线 API 成本文档

### 1. 新增：Telegram 国际通道（`qq-bot/plugins/telegram/`）

- **是什么**：Telegram Bot API 长轮询通道。海外没有 QQ，这条通道让项目在**完全没装 QQ / NapCat** 的机器上也能对话。
- **行为差异（重要）**：
  - **默认关闭**：`TELEGRAM_ENABLED` 未设 → 插件照常加载但**零副作用**（不建连接、不打日志、不占端口）。
  - 开启后**仅限主人私聊**（`TELEGRAM_OWNER_ID`）：群聊与陌生人来消息**一律忽略**（日志可见、不回复）；
  - **离线积压不补答**：启动时排空 pending update，只记条数；
  - 语音**只入不出**（能察觉你发语音，回复只发文字）；入站图片只做**存在感知**（不拉取图片字节）；
  - 与 QQ、GAL 页**共用同一份主人身份与记忆**（同 uid；`SUPERUSERS` 为空时用 `10**15 + tg_id` 自成一路）。
- **接入范式**：Telegram update → **合成 OneBot v11 事件** → `nonebot.message.handle_event` → 同一条管线
  （brain、记忆、好感、情绪、道具、命令、人设全部复用，**brain 零改动**）；合成轮标记置位，确保**不触发真实 QQ 动作**。
- **配置**：`qq-bot/.env` 的 `TELEGRAM_ENABLED` / `TELEGRAM_BOT_TOKEN` / `TELEGRAM_OWNER_ID` / `TELEGRAM_PROXY` / `TELEGRAM_API_BASE`（见 `docs/部署指南.md` §2.6）。
- **怎么验证**：冒烟 §55（30 条，离线可判）；`qq-bot/tools/dev/tg_loop_check.py`（本地 mock Bot API 服务器跑通
  "排空 → 长轮询 → 门 → 合成事件 → 出站"，**17 OK / 0 FAIL，不需要真 token**）；关闭态实测只有 nonebot 自己那行加载日志。
- **明确未做**：语音出站（silk→ogg，需动 voice 侧）、启动器里的 Telegram 设置项、群聊与多用户（见遗留任务 A15/A16/A17）。

### 2. 新增：启动器 3.9.90 —— API 设置页的两个「隐藏」

- **① API Key 掩码**：`TxtApiKey` 默认只显示 `••••••••••••`，新增「显示/隐藏」按钮；
  **焦点进入自动显示**（否则没法改）、**失焦自动掩回**（直播/录屏镜头扫过只看到圆点）。
  - **正确性要点**：真值单独保存在内存里，掩码态下"保存"与"拉取型号"都取**真值**——
    **不可能把一串圆点写进 `.env`**（那会是静默毁掉 key、界面还看不出来的事故）。
- **② 「设置」页隐藏**：勾选后顶栏「设置」收起，状态存 `data/launcher.json` 的 `hideCfg` 键；
  皮肤网页发来的 `cfg` 命令**同样被挡**；**恢复入口 = `Ctrl+Shift+H`**（新增窗口级快捷键）。
  页面本身不删（`$PAGES` 与导航映射表未动），因此页面/导航的机器判据不受影响。
- **怎么验证**：`vault/verify-tools/verify_launcher_xaml.ps1` 全通过；一致性审计 F/J/O 三片全绿；
  无界面模式 `-Mode status` 实跑正常；exe 重编译 v3.9.90（两处副本 SHA256 一致）。

### 3. 新增：配置档提示会喊出「后端=在线，但本地引擎仍会被拉起」

- **行为差异**：进「设置」页时，配置档提示区会多出一行**诚实的告警**：
  "后端=在线，但「启动引擎（LLM）」仍开着：启动器照样会拉起本地 llama-server（约 8 GB 显存）；
  该开关同时管记忆向量服务（11435，走 CPU 不占显存），关掉会连带丢语义检索"。
- **只提示，不改行为**：启动器仍然按用户的开关来（是否改成"自动跳过"见遗留任务 A18）。

### 4. 文档：在线 API 模式的两件事（`docs/部署指南.md` §2.7 ｜ `docs/DEPLOYMENT.en.md` §2.7）

- **排查结论（回应"选 API 还拉不拉本地模型"）**：
  - 本地引擎**不是必选**（`launcher/deps.json` `required:false`，状态页显示「可选」）；
  - 但**启动器仍会拉起它**——`New-StartSteps` 只看「启动引擎」开关、**不看 provider**；
  - 记忆向量服务写在同一个判断块里（关掉开关会**连带**丢语义检索），而它本身走 CPU、**不吃显存**；
  - 另有**三处**仍依赖本地 `11434`：`plugins/voice` 的两处 LLM 润色、`brain` 的模型切换命令、
    以及 `.env.example` 里**注释形态**的 `LLM_SMALL_BASE_URL`（照抄取消注释 = 小模型链重新依赖本地引擎）。
- **成本实测**（DeepSeek `deepseek-flash` = V4.1-Flash 官方价目，2026-09-12）：
  一轮私聊 = **6 次 LLM 调用 ≈ 13,851 prompt token**；单条 **0.007 元（命中 80%）～0.029 元（不命中）**（高峰价，空闲半价）；
  按真实日活（117–254 条/天）≈ **37–71 元/月**（高峰价 80% 命中）。
  - 数据来源工具：`qq-bot/tools/dev/llm_meter.py`（真历史 + 替身客户端，**不出网、不花钱、不动真库**）。
  - 最大省钱杠杆：**输入缓存命中**（未命中 2.0 元/M vs 命中 0.04 元/M）——别在系统提示最前面塞每次都在变的内容。

### 5. 订正：依赖矩阵里的一个体积数字

- `launcher/deps.json` 的 `launcher-exe` 组件 `size_mb`：`1` → `0.25`
  （生成器把 <1 的值渲染成 `<1 MB`；实际 exe 为 262,144 字节 ≈ 0.25 MB）。
  **行为差异**：无，只是文档不再虚报 4 倍。

### 6. 新增：一致性审计 P 节（两条机器判据）

- ① **插件注册闭包**：`plugins/<目录>/` 存在 ⇒ 必须在 `pyproject.toml` 的 `[tool.nonebot].plugins` 里
  （livesource 曾因此**静默失效**：开关写了、文档写了、全链零消费者且零报错）；反向的"幽灵注册"也报。
- ② **合成通道红线**：文件里出现 `handle_event(` ⇒ 必须有 `set_synthetic_round(` 或 `_SYNTHETIC[`
  （漏了会让 Telegram/GAL 这类合成通道**去动真实 QQ 账号**）。
- **顺带修的一处真实缺陷**：`plugins/telegram._cfg` 改为**先读真实环境变量、再读 nonebot 配置**——
  nonebot 的 pydantic 配置只收**写在 `.env` 里**的键，只给环境变量的键在 config 上根本不存在
  （此前会静默落默认值，联调时因此打到了真 Telegram）。

### 7. 已知未做（每条都有编号，见 `docs/遗留任务.md`）

- **A15** Telegram 语音出站（需在 voice 侧留存 wav 再转 ogg/opus）；
- **A16** 启动器里的 Telegram 设置项与通道状态行；
- **A17** Telegram 群聊与多用户（要先定"这是你个人的第二个窗口，还是可给朋友用的入口"）；
- **A18** API 模式下是否**自动跳过**本地引擎 / 拆成「聊天引擎 / 记忆向量」两个开关（当前只做提示）。

---

## English summary (same changes, one line each)

- **Telegram channel (new)** — `qq-bot/plugins/telegram/`: long-polling Bot API channel reusing the whole pipeline via
  synthetic OneBot events. **Off by default** (zero side effects). Owner's private chat only; groups and strangers are
  ignored; the offline backlog is skipped (never answered late); voice is inbound-only; inbound images are presence-only.
  Shares one identity and one memory with QQ and the GAL page. Verify: smoke §55 + `tools/dev/tg_loop_check.py`.
- **Launcher 3.9.90** — ① the API key box shows dots by default (toggle button; auto-reveals on focus, re-masks on blur;
  saving/fetching always uses the real value, so dots can never be written into `.env`); ② the Settings page can be
  hidden from the top bar (persisted as `hideCfg` in `data/launcher.json`; skin-issued `cfg` commands are blocked too;
  restore with **Ctrl+Shift+H**).
- **Settings note** — the profile panel now warns when the backend is a cloud API while "start the local engine" is
  still on (llama-server would take ~8 GB VRAM; that switch also starts the CPU-only embedding service).
- **Docs** — `docs/部署指南.md` §2.7 / `docs/DEPLOYMENT.en.md` §2.7 document that the local engine is **not** required
  in API mode but is still started by the launcher, plus measured API cost (≈13.8k prompt tokens per turn;
  ¥0.007–0.029 per message at peak, half off-peak; ¥37–71/month on the author's real volume).
- **Correction** — the launcher exe size in the dependency matrix: 1 MB → `<1 MB` (actually 262,144 bytes).
- **Auditor section P (new)** — plugin directories must be registered in `pyproject.toml` (livesource once failed
  silently), and every synthetic-event injection point must carry the synthetic-round mark.
- **Not done yet** — Telegram voice output (A15), launcher Telegram settings (A16), Telegram groups/multi-user (A17),
  auto-skipping the local engine in API mode (A18).
