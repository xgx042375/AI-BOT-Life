# 贡献指南

感谢参与。三种贡献都欢迎：**内容包 / 皮肤包**、**代码**、**文档**。

## 一、提 issue

请带上这些信息，能省掉一半来回：

- **版本**：启动器版本（关于页 / `QQAI-Launcher.exe` 元数据）与 `CORE_API_VERSION`（接口文档或关于页）
- **现象与复现**：做了什么、期望什么、实际什么（有截图更好）
- **日志**：启动器日志页（或 `-Mode logs`）里相关片段；bot 侧看 `data/bot.log`
- **环境**：Windows 版本、显存/内存、用的是本地引擎还是外部端点

> 排查顺序建议：先跑一次 `launcher\Launcher.ps1 -Mode status` 与 `-Mode deps`（组件页/依赖检测会直接告诉你"缺什么、缺了会怎样"），
> 再跑 `python qq-bot\tools\dev\audit_consistency.py`（一致性审计，60+ 项判定，多数环境类问题它能直接指出）。

## 二、提 PR（代码 / 文档）

1. **先开 issue 对齐**：涉及行为改动、新接口、新字段的，先说清楚再动手（避免白做）。
2. **跑门禁再提交**（这是本项目的硬纪律，见 [`docs/维护手册.md`](docs/维护手册.md) §7.2）：
   ```
   cd qq-bot
   .venv\Scripts\python.exe tests\smoke_test.py                      # 不得降低通过数
   .venv\Scripts\python.exe -m compileall -q core plugins agent bot.py tests harness tools
   .venv\Scripts\python.exe -m pyflakes core plugins agent bot.py
   .venv\Scripts\python.exe tools\dev\audit_consistency.py            # 0 ❌
   ```
3. **改接口要同批改文档**：`docs/接口文档.md` 是给第三方作者的**合同**——文档里承诺的路由/字段/键必须是代码里真有的（审计 K 项会把不一致判 ❌）。
4. **新增检查请带自检**：往审计工具里加判定，必须同时给**正例与负例**自检样本（一个"永远报 0"或"总在喊狼来了"的检查器比没有更坏）。
5. **小步提交**：一个改动一件事，提交信息写清"改了什么、为什么、怎么验证的"。

## 三、贡献内容包 / 皮肤包

- 内容包：见 [`docs/MOD开发指南.md`](docs/MOD开发指南.md)（角色 / 语音 / 道具 / 情绪 / 世界观 / 舞台 / 工具包七类）
- 皮肤包：见 [`docs/皮肤包接口规范-v1.md`](docs/皮肤包接口规范-v1.md)
- 每份包**自带许可声明**（`pack.json` / `manifest.json` 的 `license` 字段）；著作权归作者，使用本框架格式不改变归属。

## 四、不要提交这些东西

| 不要提交 | 原因 |
|---|---|
| 模型权重、音色模型、第三方 UI/游戏素材 | 体积与许可都不归本仓（第三方清单见 [`docs/THIRD_PARTY.md`](docs/THIRD_PARTY.md)） |
| 真实 QQ 号 / 昵称 / 本机路径 / 任何个人信息 | 审计 **O 项**会扫随仓文本与 exe 二进制，命中即 ❌ |
| 成人内容（提示词 / 剧本 / 素材） | 本仓只提供**能力与接口**；成人内容请做成独立内容包在仓外分发（见 `docs/接口文档.md`） |
| 本地状态（`data/`）、备份（`vault/`）、构建产物（`release/`） | 属运行时数据，`.gitignore` 已排除 |

## 五、许可

- 代码贡献默认按 **AGPL-3.0**（见 [`LICENSE`](LICENSE)）授权。
- 内容包 / 皮肤包贡献按**作者自定许可**（在包内声明即可），但请确保素材来源合法。
