# QQ AI 陪伴机器人 · 启动器

本地 AI 陪伴机器人启动器（Windows）。默认皮肤为内置的**简洁控制台（generic）**；「明日方舟」风为可选皮肤包（`skins/arknights` 本机样板，公开发行不带），皮肤机制见 `docs/皮肤包接口规范-v1.md`。

- **版本**：3.9.79（2026-09-12，exe 版本元数据实测）
  - 3.9.70：新增身份区 / 生成与思考三态 / 10 项 provider 预设；`$root` 改为脚本自身位置推导
  - 3.9.71：**修设置页纵向溢出**（用户实测"提示语超出页面长度"）——根因＝内容列估算高约 1231px 而可用仅约 636px，且设置页是五个页面里**唯一没有 ScrollViewer** 的。修法两件一起：① 内容改「左控件 + 右提示说明」两列（提示语从 440px 窄列移到右侧宽栏，实测右列约 319px **完全不溢出**）；② 整体套 `VerticalScrollBarVisibility="Auto"` 兜底。控件 `x:Name` 一个未改；顺带修掉一处 `&quot;` 实体漏出（原会原样显示成 `&quot;10001&quot;`）
  - 3.9.72–3.9.78：顶栏导航 7 项 + 8 个二级页接通（不再依赖皮肤）／常驻四服务灯／组件页／关于页／内容包页四类分组+详情栏／日志页"仅报错↔全文尾部"+体积一览——**逐轮记录见 `docs/启动器UI规划.md` §7.1–§7.7**（同一件事只写一遍）
  - 3.9.79：**隐私修复**——源码注释与设置页示例文案里原来写着**主人真实 QQ 号**，而 ps2exe 会把脚本**含注释**一起嵌进 exe，于是 `release/qqai-launcher-*.zip` 里的 exe 真的带着它（实测 5 处）。已全部换成占位号 `10001` 并重编译；审计 **O 项**现已机器盯住"exe/源码里出现本机身份"（值从 `.env` 运行时派生，报告只印掩码）
- **作者**：@晓咕咕Max
- **运行**：双击 `QQAI-Launcher.exe`（无控制台，纯 GUI）

---

## 1. 启动方式

```
双击 <安装根>\launcher\QQAI-Launcher.exe
```

- 单实例保护：启动时自动清理其它残留实例（不会双开冲突）
- 关闭窗口 = 进程完全退出（含 WebView2 浏览器子进程，无残留）
- `Run.bat` 为外壳（等价于直接启动 exe）

## 2. 功能总览

| 区域 | 功能 |
|---|---|
| 主页 | 场景背景 + 立绘 + 心跳对话（bot 正在干什么）+ 主页时间（本地时钟）；页面由活动皮肤提供（默认 generic 简洁控制台） |
| 人设一览 | 卡网格（数据源 `data\cards.json` 外置——新卡改 JSON 即生效，免重编译 exe；启动预解码缓存，秒开） |
| 人设详情 | 全身立绘 + 星级 + 简介 + 「设为启动人设」 |
| 设置 | 推理引擎 / 语音开关 / **皮肤下拉**（简洁控制台恒在；已装皮肤包按 manifest 入列，保存即时重导航；配置写 `data\launcher.json`）；直播开关：B 站弹幕接入 `LIVE_DANMAKU_ENABLED`、TTS 本地回放 `LIVE_AUDIO_PLAYBACK`（写 `qq-bot\.env`，重启机器人后生效） |
| 日志 | 报错日志（只读最近报错） |
| 状态 | 场景 / 正在做什么 / 心情计划 / 最近心跳时间线 / 四灯（引擎·记忆·NapCat·机器人） |
| 启动全部 | 引擎 + 记忆 + NapCat + 机器人（含 LLM，就绪 30-60 秒） |
| 停止全部 | 全部停止（含 LLM，释放显存） |
| 仅停机器人 | 只停 bot.py（LLM 保持） |
| 退出 | 立即退（先关窗再清理） |

## 3. 架构

```
┌─────────────── QQAI-Launcher.exe（PowerShell 5.1 + ps2exe）───────────────┐
│  WPF 窗口（1366×720）                                                      │
│  ├─ TopBar（标题 / 秒级时钟 / 退出）                                        │
│  └─ Grid.Row1                                                            │
│     ├─ PageHome：WebView2（Microsoft.Web.WebView2.Wpf v1.0.3351.48）      │
│     │   └─ 导航活动皮肤入口页（Get-SkinEntryUrl：generic=web 根             │
│     │      index.html；自包含皮肤=skins\<name>\index.html；SPA 视图切换）   │
│     └─ PageCfg / PageLog / PageState（WPF 页面，覆盖式切换）                │
├─ 定时刷新（3s）：Refresh-Overview（四灯探测→1s 顶栏时钟→Push-Web）          │
├─ Push-Web（唯一 state.json 写入者）：约每 3s 对活动皮肤目录 state.json      │
│   整体覆写（WriteAllText，非原子；轮询侧解析失败跳帧自愈）。字段：           │
│   time/dialog/scene/name/star/idtext/portrait/page/op/cards               │
│   + doing/mood/plan/wake（生活状态）+ svc（四服务灯）                       │
└─ 消息通道（window.chrome.webview.postMessage → WebMessageReceived）        │
    └─ 按钮 → Handle-WebCmd（start/stop/stopbot/exit/cfg/log/state/setpersona）│
└───────────────────────────────────────────────────────────────────────────┘
```

### 虚拟主机映射（WebView2）

| 主机 | 目录 |
|---|---|
| `app.local` | `<安装根>\launcher\web`（skins 子树天然可达） |
| `cards.local` | `<安装根>\launcher\cards` |
| `ui.local` | `<安装根>\launcher\web\skins\arknights\ui`（历史兼容：原 `launcher\ui` 已随皮肤包迁移删除，重指新址使样板既有引用不变；新皮肤一律走 `app.local/skins/<skin>/ui/…`） |

### 通道设计（关键约定）

- **JS → PS 命令**：`postMessage({cmd})` 消息通道（**唯一可靠**；app.local 映射不触发 WebResourceRequested，fetch 通道不可用）
- **PS → JS 状态**：**活动皮肤目录**下的 `state.json`（Push-Web 约 3s 整体覆写（非原子）+ 皮肤页 1s 轮询同目录 `state.json?t=<毫秒>`）
- **页面切换**：SPA 视图 `display` 切换（零导航零重建）；`page` 字段变化才切一次
- **缓存**：启动页保持到 32 张卡图解码完成 → 此后内存秒切（bust4w 特写 + fullw 全身，WebP q92 原分辨率）
- **WebView2 参数**：GPU 光栅 + 磁盘缓存 0（纯内存；本地映射无需磁盘 IO）

## 4. 性能判决表（防回退）

| 症状 | 根因 | 现方案 |
|---|---|---|
| 3-4s 周期性冻结 | WMI / HTTP 2s×2 / Get-NetTCPConnection 阻塞主线程 | 全部 `Test-TcpFast`（异步 Connect **500ms 超时**）+ 探测缓存 **120s** |
| 干员页白屏 | `display=''` 回落 CSS none | 显示一律 `'block'` |
| 状态写竞态 | Push-WebPage 与 Push-Web 双写 | Push-Web 唯一写者 |
| 按钮失效 | fetch 通道不触发（app.local 映射） | postMessage 消息通道 |
| 页面秒切回 / 跳变 | page 字段重放 | 一次性切换（变化才 pShow） |
| 时间跳变 | state 文件时间 | 页内本地时钟（秒级） |
| 启动白屏 | WebView2 初始化白底 | 深色默认底色 + 场景负载页（预解码完成才收起） |
| 双实例 | 残留进程抢 profile | 启动杀其它实例 + 关窗 Exit(0) |
| 无图卡溢出 | 无立绘卡图像候选回落互递归 | 候选全缺返回空串（无图卡被卡网格过滤，不炸 Push-Web） |

## 5. 文件结构

```
<安装根>\launcher\
├─ QQAI-Launcher.exe        启动器（3.9.71）
├─ Launcher.ps1             源码（UTF-8 BOM；ps2exe 构建）
├─ Run.bat                  外壳
├─ Microsoft.Web.WebView2.*  WebView2 v1.0.3351.48（四件版本须一致）
├─ webdata3\                WebView2 用户数据（可删重建）
├─ web\                     WebView2 虚拟主机 app.local 根
│  ├─ index.html / home.css / app.js   generic 默认主页（简洁控制台，原创零 IP 零图片）
│  ├─ gal.html / gal.js / gal.css      GAL 页（bot serve: http://127.0.0.1:8080/gal）
│  ├─ state.json             运行时状态（generic 活动时 Push-Web 写这里；gitignore）
│  └─ skins\
│     ├─ generic\            内置默认皮肤样板（manifest.json + palette.css + README.md）
│     └─ arknights\          方舟样板皮肤（gitignore，公开发行不带；完整 SPA + ui\ 素材 + manifest.json）
└─ cards\
   ├─ <key>_full.png        全身立绘源（1536 侧）
   ├─ bust4\ <key>.png      半身特写裁切（人工精修，720×960 起）
   ├─ bust4w\ <key>.webp    特写 WebP（q92 原分辨率；选择页与差分）
   └─ fullw\ <key>.webp     全身 WebP（q92；详情页）
```

## 6. 人设与语音联动

- 设为启动人设 → **HTTP 通道** `POST 127.0.0.1:8080/launcher/persona {name,uid}`
- bot 侧等价 `/人设` 命令处理链（`brain.set_persona`），**bot 在线切换、无需重启**
- 切换即联动：对话人设 / 语音音色 / QQ 昵称头像 / 记忆世界观分界（同链既有逻辑）
- **语音规则**（2026-09-06）：只有中文音色卡触发语音（6 张方舟系）；无音色卡**默认不触发**（不兜底日文）
- 音色包存放：`<安装根>\tools\GPT-SoVITS-v4-package\GPT-SoVITS-v4-20250529\voices\<角色>\`

## 7. 调试

- `state.json`：实时状态（位于**活动皮肤目录**，generic= `web\state.json`；字段 stamp/pid/cards/svc/dialog——pid 变化=双实例）
- `webstep.log`：启动链 S1-S10 + 刷新分步打点（毫秒时间戳）
- `boot_err.log`：脚本异常（trap，含皮肤目录缺失回落留痕）；`web_cmd.log`：命令记录
- Bot 端：`<安装根>\data\bot.log` / `bot.err.log` / `bot_hook_trace.txt`

## 8. 源码构建

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File launcher\build_exe.ps1
```

> 这个脚本做四件事：① 从 `Launcher.ps1` 的 `$script:LAUNCHER_VERSION` **现读版本**——版本只有一个来源，
> 原先要在脚本常量与 `-version` 两处手写，抄漏一次就出现"exe 说 3.9.75、脚本说 3.9.76"的假版本（2026-09-12 实际发生）；
> ② 编译前验 **BOM + PS5.1 解析**（BOM 一丢，编出来的 exe 跑起来就是一堆假语法错）；
> ③ 编译；④ **双处同步并比对 SHA256 + 元数据版本**，不一致直接非零退出。
>
> ⚠️ **本节面向源码仓**：`build_exe.ps1` 与 `Launcher.ps1` 只随仓库，**不随启动器发行包**
> （`qqai-launcher-*.zip` 里只有 `QQAI-Launcher.exe` + 本文件）。只拿发行包的人不需要、也不能执行本节。
>
> 等价手写命令（脚本就是在跑它）：
>
> ```powershell
> Import-Module ps2exe
> Invoke-ps2exe -InputFile "<安装根>\launcher\Launcher.ps1" -OutputFile "<安装根>\launcher\QQAI-Launcher.exe" `
>   -title "QQ AI Launcher" -description "QQ AI companion robot launcher" `
>   -product "QQ AI Launcher" -copyright "(c) 2026" -version "<版本号，须同脚本常量>" `
>   -company "" -noConsole -noOutput -noError -STA -x64 -requireAdmin
> ```
> `-requireAdmin`（2026-09-08 深夜加入）：exe 内嵌 requireAdministrator 清单——启动器恒以管理员运行，
> 才能终止提权启动的 bot 进程树（非提权 taskkill 会被拒绝访问；提权 bot 的 WMI CommandLine 为 NULL，
> 按命令行匹配的旧停止逻辑因此从未真正杀到过它）。
> exe 为**双处同步**：`launcher\QQAI-Launcher.exe` 与 `<安装根>\QQAI-Launcher.exe` 内容须一致。

## 9. 已知约定

- `Launcher.ps1` 必须 **UTF-8 with BOM**（PowerShell 5.1 按 GBK 读无 BOM 文本会损坏中文/结构）
- WebView2 三个 .dll 版本必须一致（v1.0.3351.48）
- 清缓存 = 删 `webdata3`
- 新增立绘/新卡（免重编译）：
  ① `data\cards.json` 追加一条（`key/name/cls/star/uni/desc` 六字段；内容包可跑 `qq-bot\tools\export_cards.py` 自动追加）；
  ② 放素材 `cards\<key>_full.png`（全身）→ `cards\bust4\<key>.png`（特写裁切）→ 可选 WebP 转换（`bust4w\`/`fullw\`）；
  ③ 重启启动器（web 卡网格与 WPF 干员页同源 `Get-Cards`；**无任何立绘文件的卡会被过滤不显示**）
- personas 卡 json 必须**无 BOM**（python json 解析不认 BOM）

## 10. 皮肤包

- 主页界面 = **皮肤包**：`launcher\web\skins\<name>\`（页面 + 素材 + manifest.json）；`data\launcher.json` 的 `skin` 键或设置页下拉选择，保存即时重导航
- 皮肤目录缺失自动回落 generic（`boot_err.log` 留痕），不会白屏
- 全部接口（manifest schema / state.json 契约 / postMessage 命令 / 虚拟主机 / 卡片数据源 / GAL_SKIN 钩子）见 **`docs/皮肤包接口规范-v1.md`**；自制皮肤最小步骤见其 §8
