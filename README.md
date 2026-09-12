# QQ AI 陪伴机器人（QQAI Companion Framework）

**本地运行**的 AI 陪伴机器人框架 + Windows 启动器：QQ 侧接入、GAL 网页端呈现、生活模拟与长期记忆、内容包 / 皮肤包双层扩展。

> **English**: see [`README.en.md`](README.en.md)。
>
> **不想 clone？** 到 [**Releases**](../../releases) 下「懒人包」（`qqai-lazybundle-framework-*.tar`，约 7.5 MB：
> 解压 → 跑 `安装.ps1` → 填 `.env` 即可），或单独下启动器 `QQAI-Launcher.exe`。收包人只需自备 **Python 3.13**
> 与**一个模型或一个 API Key**。

> **本项目是框架，不含模型、音色、第三方素材与任何角色内容**——那些由使用者自备或由内容包提供。
> 成人向内容**不随本仓**：框架只提供能力与接口，内容以独立内容包形式安装（见 [`docs/接口文档.md`](docs/接口文档.md)）。

## 特性

- **本地推理**：对接本地 llama.cpp 端点（亦可切外部 OpenAI 兼容端点），对话数据不出本机
- **三出口**：QQ（NoneBot2 + OneBot v11）、**Telegram**（国际 IM，2026-09-12 接入，**不需要 QQ 也能跑**）与 GAL 网页端（HTTP + WebSocket，舞台/差分/语音同源驱动）；三者**共用同一份身份与记忆**
- **活人感链路**：生活模拟心跳、主动发起、日历式约定、情绪与关系状态、日复盘与事实提取
- **内容包体系 `robot-pack-v1`**：角色卡 / 语音 / 道具 / 情绪 / 世界观 / 舞台 / 工具包七类，双根扫描、坏包隔离
- **皮肤包体系 `launcher-skin-v1`**：启动器界面可整套替换（manifest + 页面 + 调色板），GAL 素材口可覆盖
- **启动器**：一键启停、依赖检测、内容包管理、日志与状态查看；另有 `-Mode status|deps|packs|logs` 无界面模式（**界面逻辑可被脚本验证**）
- **可审计**：自带一致性审计工具（环境漂移 / 绝对路径 / 原子写 / 文档契约 / 引用完整性 / 隐私外泄等 60+ 项，每项含正负例自检）

## 快速开始

**先选安装路径**——两种都行，区别只在"启动器怎么启动"：

| 路径 | 你拿到的东西 | 怎么启动 |
|---|---|---|
| **懒人包**（[Releases](../../releases) 的 `qqai-lazybundle-*.tar`） | 框架 + **预编译好的 `QQAI-Launcher.exe`** + `安装.ps1` | 解压 → 跑 `安装.ps1` → 双击 `launcher\Run.bat` |
| **克隆本仓**（`git clone`） | 只有源码：**没有 exe**（它是构建产物，不随仓）；WebView2 的 4 个包装 DLL 随仓 | 双击 `launcher\Run.bat`（**它会自动回落到 `Launcher.ps1`**）；或自己 `launcher\build_exe.ps1` 生成 exe |

```
1. 装 Python 3.13 + 依赖（qq-bot/pyproject.toml）与本地推理引擎（llama.cpp）；
   不想用本地模型就跳过引擎，改在启动器「模型与接口」里填任意 OpenAI 兼容 API（模型可选，不是必须）
2. 复制 qq-bot/.env.example → .env，至少填 SUPERUSERS（主人 QQ）。
   人设开箱可用：默认 PERSONA=_starter（随仓的中性占位卡），要自己的角色就复制改名再改这一行
3. 启动启动器（上表两种路径）→「组件」页会列出缺什么、缺了会怎样、去哪拿
4. 点顶栏「▶ 启动」→ 引擎约 30-60 秒就绪 → 私聊机器人
```

> **克隆用户须知（三件事）**：① `QQAI-Launcher.exe` 不在仓里（见上表）；② WebView2 的**包装 DLL**（`launcher\*.dll` 四个）随仓，
> 但**运行时本体**（Evergreen，约 100 MB 级）项目不分发——用系统的即可（Win10/11 多半已内置；缺了到微软官网装 Evergreen Runtime，
> 装不上时启动器仍可用，只是主页/GAL 页渲染不出来，失败详情见 `launcher\webview2_error.log`）；
> ③ 模型、语音权重、NapCat 一律不随仓（许可与体积所限），按「组件」页指引自备。

细节见 [`docs/部署指南.md`](docs/部署指南.md)（安装 / 升级 / 换盘 / 卸载 + 功能→依赖矩阵）与 [`docs/维护手册.md`](docs/维护手册.md)（启停 / 日志 / 状态 / 排障）。

## 目录结构

```
qq-bot\       机器人核心（bot.py + core\ + plugins\ + agent\ + harness\ + packs\ + tests\ + tools\）
launcher\     启动器（Launcher.ps1 + build_exe.ps1 + web\（含默认皮肤与 GAL 客户端） + deps.json）
build\        发行打包（build_release.ps1：SDK / Core / 启动器三包 + 自检）
docs\         文档（契约 / 做法 / 运维 / 部署 / 第三方清单）
```

> 运行期目录（`data\`、`tools\`、`launcher\cards\`、`vault\`）**不入库**：里面是使用者本机的模型、音色、素材与状态。

## 扩展开发

| 想做什么 | 读哪份 |
|---|---|
| 做一个内容包（角色 / 语音 / 舞台 / 世界观 …） | [`docs/MOD开发指南.md`](docs/MOD开发指南.md) |
| 可依赖的稳定契约（包格式、GAL 协议、皮肤格式、代码钩子） | [`docs/接口文档.md`](docs/接口文档.md) |
| 皮肤包写法与启动器接口（state.json / postMessage / manifest） | [`docs/皮肤包接口规范-v1.md`](docs/皮肤包接口规范-v1.md) |
| 架构与模块划分 | [`docs/项目拆解.md`](docs/项目拆解.md) |
| 第三方组件与许可 | [`docs/THIRD_PARTY.md`](docs/THIRD_PARTY.md) |

**兼容承诺**：`CORE_API_VERSION`（当前 `1.0.0`）在 v1.x 内**只增不改不删**——接口文档列出的字段与钩子点不会被打断。

## 许可

- **本仓代码**：[GNU Affero General Public License v3.0](LICENSE)（AGPL-3.0）。
  可以自由使用、修改、分发；**若修改后作为网络服务提供给他人使用，必须同样以 AGPL 提供源码**。
- **版权**：Copyright (C) 2026 **@晓咕咕Max**。
- **内容包 / 皮肤包**：著作权归**其作者**，不因使用本框架格式而转移；每份包在 `pack.json` / `manifest.json` 里自声明许可（例如随仓示例包 `example.sakura` 为 CC0-1.0）。
- **第三方**：模型、音色、UI 素材、第三方运行时**均不随本仓分发**，其许可见 [`docs/THIRD_PARTY.md`](docs/THIRD_PARTY.md)；使用者需自行取得合法来源。
- **免责**：本项目与任何第三方即时通讯平台无关；使用者应自行遵守所接入平台的服务条款及所在地法律法规。

## 贡献

欢迎 issue 与 PR：内容包 / 皮肤包 / 文档 / 示例都算贡献。见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。
