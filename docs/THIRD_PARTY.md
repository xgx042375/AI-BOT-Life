# 第三方组件清单（THIRD_PARTY）

> 本框架使用以下第三方开源组件，感谢原作者。
> **素材边界声明**：方舟 UI 克隆与游戏素材仅存于本机 gitignored 皮肤包
> （`launcher/web/skins/arknights/` 等），**不随任何发行物分发**。
> 许可标注以项目官方仓库为准；无法当场核实者标"待核"，不臆测。

## Python 依赖（见 `qq-bot/pyproject.toml`，版本列为当前 venv 实测）

| 组件 | 版本 | 许可 | 用途 |
|---|---|---|---|
| nonebot2 | 2.5.0 | MIT | QQ 机器人异步框架本体 |
| nonebot-adapter-onebot | 2.4.6 | MIT | OneBot v11 协议适配器（对接 NapCat） |
| openai (openai-python) | 3.3.1 | Apache-2.0 | OpenAI 兼容端点客户端（core/llm.py 统一收口） |
| langgraph | 1.2.11 | MIT | agent 层状态图编排 |
| langgraph-checkpoint-sqlite | 3.1.1 | MIT | agent 检查点 SQLite 存储 |
| aiosqlite | 0.22.1 | MIT | 异步 SQLite 访问（记忆/检查点） |
| loguru | 0.7.3 | MIT | 日志 |
| fastapi | 0.141.1 | MIT | Web 服务层（nonebot2[fastapi] 驱动 + GAL/卡片接口） |
| uvicorn | 0.52.4 | BSD-3-Clause | ASGI 服务器 |
| python-dotenv | 1.2.3 | BSD-3-Clause | .env 环境配置加载 |

## 桌面/工具链

| 组件 | 版本或来源 | 许可 | 用途 |
|---|---|---|---|
| ps2exe | PowerShell Gallery 模块 | MIT | 启动器 PowerShell 脚本打包为 QQAI-Launcher.exe |
| WebView2 Loader（Microsoft.Web.WebView2.*） | Microsoft 官方再分发运行时 | 微软 WebView2 再分发条款 | 启动器内嵌浏览器内核 |

## 外置运行时（随发行版使用但不随包分发，用户自备）

| 组件 | 版本或来源 | 许可 | 用途 |
|---|---|---|---|
| llama.cpp | ggerganov/llama.cpp | MIT | 本地 LLM 推理引擎（llama-server） |
| GPT-SoVITS | RVC-Boss/GPT-SoVITS | MIT | 语音合成引擎（voice 插件外挂） |
| NapCat | NapNeko/NapCatQQ | 自定义混合许可（详见其仓库，待核） | OneBot 协议端（NTQQ） |

## 前端库（仅存在于本机 gitignored 皮肤包内，不随发行物分发）

| 组件 | 版本或来源 | 许可 | 用途 |
|---|---|---|---|
| jQuery | jQuery Foundation | MIT | 方舟皮肤页脚本依赖 |
| jquery.pjax | defunkt/jquery.pjax | MIT | 方舟皮肤页无刷新路由 |

## 已移除的第三方集成（备查）

| 组件 | 处置 | 说明 |
|---|---|---|
| ALAPI（alapi.cn，联网斗图/表情包接口） | **已移除（2026-09-12）** | 此前从未在本清单登记（漏登）。已删除 `.claude/skills/alapi/`、`skills-lock.json` 条目、`.env`/`.env.example` 中的 `ALAPI_TOKEN` 段。表情包改为**纯本地素材**发送（`qq-bot/plugins/sticker/`）。历史记录见 `docs/archive/` 与 `docs/决策日志-通用化改造-2026-09-10.md`（归档区只读，不改）。 |

---
*本清单随发行物（SDK 包 docs/）分发；新增第三方依赖时须同步更新。*
*方舟 UI 克隆（mashirozx/arknights-ui，MIT）及其引用的游戏素材仅限本机私用皮肤包。*
