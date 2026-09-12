# ============================================================
#  QQ AI 陪伴机器人 · 启动器  © @晓咕咕Max
#  引擎/机器人 一键管控 | 启动设置 | 报错日志 | 状态监控 | 退出全清（含 LLM 显存）
#  风格：明日方舟·罗德岛终端（近黑蓝灰 / 米白 / 琥珀橙 / 切角几何 / 数据感）
#  运行：双击 QQAI-Launcher.exe（GUI）或 powershell -File Launcher.ps1（可 -Mode start|stop|status）
# ============================================================
#requires -version 5.1
param(
    [string]$Mode = ""   # 空=UI；start/stop/status=无界面命令行
)
$ErrorActionPreference = "Continue"
# 2026-09-06：无控制台（GUI exe）时设置编码会抛"句柄无效"——安全化
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
# 2026-09-12 T7.3：$root 由脚本自身位置推导（本文件位于 <安装根>\launcher\），不再写死盘符。
# ★ 2026-09-12 热修十四（用户实测报障）：**exe 历史上有两份**（<安装根>\ 与 <安装根>\launcher\），
#   而从**根目录那份**双击时 `$PSScriptRoot` = 安装根 → `launcherDir` 被当成安装根 →
#   `Get-Deps` 去根目录找 `deps.json`（实际在 launcher\）→ 组件页显示"依赖矩阵缺失，无法检测"；
#   同时 `$root` 只是**碰巧**被硬编码回落救回（换盘就废）。
#   现在改为**按证据判两件事**：本目录下有没有 `launcher\deps.json`（有 → 本目录是安装根）、
#   或本目录自己有没有 `deps.json`/`Launcher.ps1`（有 → 本目录是 launcher 目录）。
$script:appDir = $PSScriptRoot
if (-not $script:appDir) {
    # ps2exe 打包后 $PSScriptRoot 理论上可用，但实测环境有差异 —— 用当前进程的 exe 路径兜底
    try { $script:appDir = Split-Path -Parent ([System.Diagnostics.Process]::GetCurrentProcess().MainModule.FileName) } catch {}
}
$script:launcherDir = $script:appDir
$root = $null
try {
    if (Test-Path (Join-Path $script:appDir "launcher\deps.json")) {
        # 情形 A：exe 在安装根 → launcher 子目录才是启动器目录
        $root = $script:appDir
        $script:launcherDir = Join-Path $script:appDir "launcher"
    } elseif ((Test-Path (Join-Path $script:appDir "deps.json")) -or (Test-Path (Join-Path $script:appDir "Launcher.ps1"))) {
        # 情形 B：脚本/ exe 就在 launcher 目录里 → 父目录是安装根
        $script:launcherDir = $script:appDir
        $root = Split-Path -Parent $script:appDir
    }
} catch {}
# 兜底链：判不出来或判错（父目录没有 qq-bot/data）时，逐级试到"看起来像安装根"的那个
if (-not $root -or -not ((Test-Path (Join-Path $root "qq-bot")) -or (Test-Path (Join-Path $root "data")) -or (Test-Path (Join-Path $root "start.bat")))) {
    foreach ($cand in @($script:appDir, (Split-Path -Parent $script:appDir), (Split-Path -Parent (Split-Path -Parent $script:appDir)))) {
        if ($cand -and ((Test-Path (Join-Path $cand "qq-bot")) -or (Test-Path (Join-Path $cand "start.bat")))) {
            $root = $cand
            if (-not (Test-Path (Join-Path $script:launcherDir "deps.json"))) {
                $ld = Join-Path $cand "launcher"
                if (Test-Path (Join-Path $ld "deps.json")) { $script:launcherDir = $ld }
            }
            break
        }
    }
}
if (-not $root) { $root = $script:appDir }   # 最后仍判不出：用自身目录（至少日志能落盘）
$script:logDir = if ($script:launcherDir -and (Test-Path $script:launcherDir)) { $script:launcherDir } else { "$root\launcher" }
trap {
    try { [System.IO.File]::AppendAllText("$script:logDir\boot_err.log", ($_ | Out-String) + "`n---`n") } catch {}
    # 2026-09-12 实测事故：无界面模式（-Mode X）下 `continue` 会把调用点的异常反复吞掉，
    # 表现为**进程静默挂死**（无输出、不退出）——排查半小时才定位到"函数在 dispatch 之后才定义"。
    # 所以无界面模式必须让错误**响亮地终止**：打印 + 退出码 1（脚本化调用才有意义）。
    # 有界面时保持原行为（continue，界面横幅自己会显示错误，用户看到的是"操作中断"）。
    # 注意：这里**不能**用 W()——W 定义在本文件后段（约行 1252），此刻还不存在（同一个"函数后定义"坑）；
    # 而且 exe 是 -noConsole 构建，Write-Host 可能抛"句柄无效"，所以自己包一层 try。
    if ($Mode -ne "") { try { Write-Host ("[launcher] 内部错误: " + $_.Exception.Message) } catch {}; exit 1 }
    continue
}
$cfgFile = "$root\data\launcher.json"

# ---------------- 关于页常量（2026-09-12 UI 改版：docs\启动器UI规划.md §4） ----------------
# 纪律：**绝不显示编造的署名**。未填 → 界面显示"（未设置）"，这本身即提醒。
#   双重署名：FRAMEWORK_AUTHOR=本人署名（A6 已裁决）；SOURCE_URL=本仓地址（A1 已裁决全开源 AGPL-3.0）。
$script:LAUNCHER_VERSION = "3.9.89"   # 启动器自身版本（改 UI 即升；重编译 exe 时用同一值）
$script:FRAMEWORK_AUTHOR = "@晓咕咕Max"  # 框架作者（双重署名之"框架作者"位）；2026-09-12 A6 裁决
$script:SOURCE_URL       = "https://github.com/xgx042375/AI-BOT-Life"   # 本仓地址（A1 全开源已裁决；公开仓已建，填 URL 即生效）

Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase

# ---------------- 配置 ----------------
function Get-LauncherCfg {
    $d = @{ llm = $true; voice = $true; skin = "generic" }
    try { if (Test-Path $cfgFile) {
        $j = Get-Content $cfgFile -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($k in @("llm", "voice")) { if ($null -ne $j.$k) { $d[$k] = [bool]$j.$k } }
        # 2026-09-10 Phase2：皮肤键（generic|arknights）；未知/缺失值由 Get-SkinDir 回落 generic
        if ($j.skin) { $d["skin"] = [string]$j.skin }
    } } catch {}
    return $d
}
function Set-LauncherCfg($d) {
    try { $d | ConvertTo-Json | Set-Content $cfgFile -Encoding UTF8 } catch {}
}

# ---------------- LLM 配置档：本地 / 在线（2026-09-12 用户实测"切在线后再切回去没变"） ----------------
# 病根有两个，都在"只有一份 .env"这件事上：
#   ① **保存只写非空值**（旧实现 `if ($u) { Set-EnvValue ... }`）→ 从在线切回本地时，在线那套 URL/型号
#      赖在 .env 里清不掉，界面上三格也还显示在线的值 = 用户看到的"没变"。
#   ② 更危险的是它**静默**：`core/llm.py` 里 `llm_provider` 只决定"要不要注入 thinking"，
#      而端点取的是 `llm_base_url`——所以 `LLM_PROVIDER=local` + 在线 URL 的组合**仍然会把请求发到云端**。
#      这种"以为切回来了、其实还在烧 API"的状态，界面上必须自己喊出来（见 txtProfileNote 的告警）。
# 现在的分工：
#   · **档**（local / online）= 两组保存好的值，存在 launcher.json（本机状态，gitignored），切换**不动** .env；
#   · **激活** = 把该档的值**显式**写进 .env（含空串=清空，让回落链生效：11434 + model=gemma + key=ollama）。
$script:LLM_PROFILE_KEYS = @("local", "online")
# 程序性选中档（Refresh-Cfg 回显）不该被当成"用户切换档"——否则回显时会把三格再改一遍，与 .env 回显打架
$script:llmProfileGuard = $false
function Get-LlmProfiles {
    $d = @{
        active = "local"
        # 本地档**存空值**：空串=未设，core/llm.py 的回落链给出 11434/v1 + model=gemma + key=ollama。
        # 为什么不写死具体值：万一将来引擎别名变了，写死的值会静默失效，而回落链跟着代码走。
        local  = @{ provider = "local";         url = ""; key = "ollama"; model = "" }
        online = @{ provider = "openai_compat"; url = ""; key = "";       model = "" }
    }
    try {
        if (Test-Path $cfgFile) {
            $j = Get-Content $cfgFile -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($j.llmProfile -and ($script:LLM_PROFILE_KEYS -contains [string]$j.llmProfile)) { $d.active = [string]$j.llmProfile }
            foreach ($k in $script:LLM_PROFILE_KEYS) {
                $p = $j.llmProfiles.$k
                if (-not $p) { continue }
                foreach ($f in @("provider", "url", "key", "model")) {
                    $v = "$($p.$f)"
                    if ($v -ne "") { $d[$k][$f] = $v }
                }
            }
        }
    } catch {}
    return $d
}
function Set-LlmProfiles($prof) {
    # 读-改-写：只覆盖 llmProfile / llmProfiles 两个键，llm / voice / skin 原样保留。
    # 这份文件同时被「保存 SAVE」写（Set-LauncherCfg 是整体覆写）——两边都必须读-改-写，否则互相抹键。
    try {
        $j = @{}
        if (Test-Path $cfgFile) { try { $j = Get-Content $cfgFile -Raw -Encoding UTF8 | ConvertFrom-Json } catch {} }
        $out = @{}
        foreach ($k in @("llm", "voice", "skin")) { if ($null -ne $j.$k) { $out[$k] = $j.$k } }
        if (-not $out.ContainsKey("llm")) { $out["llm"] = $true }
        if (-not $out.ContainsKey("voice")) { $out["voice"] = $true }
        if (-not $out.ContainsKey("skin")) { $out["skin"] = "generic" }
        $out["llmProfile"] = [string]$prof.active
        $out["llmProfiles"] = @{ local = $prof.local; online = $prof.online }
        $out | ConvertTo-Json -Depth 6 | Set-Content $cfgFile -Encoding UTF8
    } catch {}
}
function Get-LlmEnvNow {
    # .env 里**当前真正生效**的那一套（空串=未设，按 core/llm.py 的回落链理解）
    return @{
        provider = "$(Get-EnvValue 'LLM_PROVIDER')"
        url      = "$(Get-EnvValue 'LLM_BASE_URL')"
        key      = "$(Get-EnvValue 'LLM_API_KEY')"
        model    = "$(Get-EnvValue 'LLM_MODEL')"
    }
}
function Test-ExternalUrl($u) {
    # 是否指向"非本机"端点（用于 provider=local 却指着云端的静默告警）
    if (-not $u) { return $false }
    return -not ($u -match "(?i)://(127\.0\.0\.1|localhost|\[::1\])")
}
function Set-LlmProfileToEnv($p) {
    # 显式四键写入（含空串）。空值必须能写进去——这正是"切回本地"能生效的关键。
    Set-EnvValue "LLM_PROVIDER" ([string]$p.provider)
    Set-EnvValue "LLM_BASE_URL" ([string]$p.url)
    Set-EnvValue "LLM_MODEL"    ([string]$p.model)
    Set-EnvValue "LLM_API_KEY"  ([string]$p.key)
}
function Get-LlmEffective($provider, $url, $model) {
    # "有效值"口径：空串不是"空"，而是**回落链解析出来的那个值**（core/llm.py 的铁律）。
    # 界面上若把"空"当空显示，用户会以为坏了；对照时若拿空串比 11434/v1，又会**误报"不一致"**。
    $isLocal = (-not $provider) -or ($provider -eq "local")
    return @{
        url   = if ($url) { $url } elseif ($isLocal) { "http://127.0.0.1:11434/v1（本地默认）" } else { "" }
        model = if ($model) { $model } elseif ($isLocal) { "gemma（本地默认）" } else { "" }
    }
}
function Get-LlmBoxes {
    return @{
        url   = "$($window.FindName('TxtApiUrl').Text)".Trim()
        key   = "$($window.FindName('TxtApiKey').Text)".Trim()
        model = "$($window.FindName('TxtModel').Text)".Trim()
    }
}
function Load-LlmProfileToBoxes($key) {
    # 把某档的值填进三格（**只改界面，不落 .env**）。界面上"切换档"必须看得见变化——
    # 用户报的"切回本地没变"，最直接的一层就是三格纹丝不动。
    try {
        $prof = Get-LlmProfiles
        if (-not ($script:LLM_PROFILE_KEYS -contains $key)) { return }
        $p = $prof[$key]
        $u = $window.FindName("TxtApiUrl"); if ($u) { $u.Text = [string]$p.url }
        $k = $window.FindName("TxtApiKey"); if ($k) { $k.Text = [string]$p.key }
        $m = $window.FindName("TxtModel");  if ($m) { $m.Text = [string]$p.model }
        # 档里存的是空串（本地默认）时，界面显示**有效值**——空框看着像坏了，且用户无从知道它会回落到哪
        $effEmpty = (-not [string]$p.url) -or (-not [string]$p.model)
        if ($effEmpty) {
            $e = Get-LlmEffective ([string]$p.provider) ([string]$p.url) ([string]$p.model)
            if ($u -and -not $u.Text) { $u.Text = $e.url }
            if ($m -and -not $m.Text) { $m.Text = $e.model }
        }
        if ($k -and -not $k.Text) { $k.Text = "ollama" }
    } catch {}
}
function Update-LlmProfileNote {
    # "当前生效（.env）" vs "本档" 的诚实对照 + 两条必须喊出来的告警。
    # 为什么非要有这一行：旧实现里"以为切回本地了、其实还在请求云端"是**完全静默**的。
    try {
        $t = $window.FindName("txtProfileNote")
        if (-not $t) { return }
        $prof = Get-LlmProfiles
        $sel = $window.FindName("CmbProfile")
        $key = if ($sel -and $sel.SelectedItem) { [string]$sel.SelectedItem.Tag } else { [string]$prof.active }
        $p = $prof[$key]
        $now = Get-LlmEnvNow
        $pvNow = if ($now.provider) { $now.provider } else { "local" }
        # 对照用**有效值**：空串在两边都解析成同一个回落值，否则"本地档(空) vs .env(空)"会被误判成不一致
        $effP = Get-LlmEffective ([string]$p.provider) ([string]$p.url) ([string]$p.model)
        $effN = Get-LlmEffective $pvNow $now.url $now.model
        $same = ($pvNow -eq [string]$p.provider) -and ($effN.url -eq $effP.url) -and ($effN.model -eq $effP.model)
        $nmShow = if ($key -eq "local") { "本地" } else { "在线" }
        $lines = @()
        $lines += "当前生效（.env）：provider=" + $(if ($now.provider) { $now.provider } else { "（未设＝local）" }) +
                  " ｜ URL=" + $effN.url +
                  " ｜ 模型=" + $effN.model
        $lines += "本档【" + $nmShow + "】：provider=" + [string]$p.provider +
                  " ｜ URL=" + $effP.url +
                  " ｜ 模型=" + $effP.model
        if ($same) { $lines += "→ 一致：本档就是当前生效的配置。" }
        else { $lines += "→ ⚠️ 不一致：点「激活此档 ↦ 写入 .env」让本档生效；或点「三格 ↦ 存入此档」把 .env 现状收进档里。" }
        if ($pvNow -eq "local" -and (Test-ExternalUrl $now.url)) {
            $lines += "→ ⚠️⚠️ .env 里 provider=local 但 URL 指向外部端点（$($now.url)）——core/llm.py 的 provider 只决定 thinking 注入，" +
                      "端点仍取这个 URL，**请求会发到云端**。要回本地：激活「本地」档（会把 URL/模型清空，走 11434 回落链）。"
        }
        if ($key -eq "local" -and (Test-ExternalUrl ([string]$p.url))) {
            $lines += "→ ⚠️ 本地档里存着外部 URL，激活它不会回到本地引擎。"
        }
        $t.Text = ($lines -join "`n")
    } catch {}
}

# ---------------- 界面语言（zh / en · 2026-09-12 用户裁决"做全局语言切换"） ----------------
# 三条设计决定，都是为了"能验证 + 不引入第二份真相"：
#   ① **表的键是中文原文**（zh→en 单向维护）。切换回中文靠"记住原文"（$script:uiOrig 注册表），
#      不写第二张反查表——两张表必然漂移。
#   ② **翻译作用在"显示出口"**（界面树），不是 35 处调用点。XAML 静态文案、ListBox 条目、
#      代码里拼出来的句子，全都在树上被扫到（含变量插值的句子走 $UI_LANG_PATTERNS 正则表）。
#      这样做的好处：漏翻会**看得见**（界面上留着中文），而不是散落在代码里没人发现；
#      将来新增界面文案也一样自动被覆盖，不必记得同步。
#   ③ 审计 J 项加一片：**XAML 里的每个中文字面量都必须在表里**（单向、零噪声、正负例自检）。
#      它保证"静态界面"这一层的覆盖率是机器判定的，不靠人肉点数。
$script:UI_LANG_TABLE = @{
    # —— 顶栏导航 / 全局按钮 ——
    "◈ 主页" = "◈ Home"; "← 主页" = "← Home"; "干员" = "Operators"; "设置" = "Settings"
    "内容包" = "Content packs"; "运行状态" = "Runtime status"; "日志" = "Log"; "组件" = "Components"; "关于" = "About"
    "▶ 启动" = "▶ Start"; "■ 停止" = "■ Stop"
    # —— 设置页 ——
    "启动设置" = "Settings"; "启动选项" = "Startup options"
    "启动本地推理引擎（LLM）" = "Start the local inference engine (LLM)"
    "启动语音（TTS 服务）" = "Start voice (TTS service)"
    "直播开关" = "Live-stream switches"
    "B 站直播弹幕接入（LIVE_DANMAKU_ENABLED）" = "Bilibili live chat (danmaku) input (LIVE_DANMAKU_ENABLED)"
    "TTS 语音本地回放到直播音频（LIVE_AUDIO_PLAYBACK）" = "Play TTS audio into the live-stream mix (LIVE_AUDIO_PLAYBACK)"
    "聊天软件模式跨重启保留" = "Keep chat-app mode across restarts"
    "皮肤（主页外观）" = "Skin (home page look)"
    "插件管理（已装插件 / 快速安装）" = "Pack manager (installed / quick install)"
    "模型与接口" = "Model & API"; "配置档" = "Profile"; "后端类型" = "Backend type"
    "接口地址 URL" = "API base URL"; "模型名" = "Model name"; "拉取型号" = "Fetch models"
    "套用预设 ↦ 三格" = "Preset ↦ boxes"; "激活此档 ↦ 写入 .env" = "Activate ↦ write .env"
    "三格 ↦ 存入此档" = "Boxes ↦ save to profile"
    "身份" = "Identity"; "主人 QQ" = "Owner QQ"; "主人昵称" = "Owner nickname"
    "我确认要更换主人 QQ（必须同步迁移记忆库，否则等于换了个全新用户）" = "I confirm I am changing the owner QQ (you must run the memory migration, otherwise it is a brand-new user)"
    "生成与思考" = "Generation & thinking"; "思考模式" = "Thinking mode"
    "保存  SAVE" = "Save"; "说明" = "Notes"
    "＊ 所有 .env 项保存后都需重启机器人才生效（Python 进程启动时才读配置，不热加载）。" = "＊ Every .env item takes effect only after restarting the bot (the Python process reads its config at startup; it is not hot-reloaded)."
    "＊ 直播开关：写入 qq-bot\.env。保存后需重启机器人（先停止再启动）。" = "＊ Live switches: written to qq-bot\.env; restart the bot afterwards (stop, then start)."
    "＊ 聊天软件模式跨重启保留：写入 WEBGAL_CHAT_PERSIST。GAL 模式不受此开关影响，始终不跨重启。" = "＊ Keep chat-app mode across restarts: writes WEBGAL_CHAT_PERSIST. GAL mode is unaffected and never persists across restarts."
    "＊ 皮肤：写 data\launcher.json 的 skin 键，保存后立即重新导航主页视图（不必重启）。" = "＊ Skin: writes the `skin` key of data\launcher.json; the home view re-navigates immediately on save (no restart needed)."
    "＊ 模型与接口：本地引擎=llama-server(11434)，模型名仅作标识；API=任意 OpenAI 兼容端点（url / key / 模型名三格照填）。切换后需重启机器人。" = "＊ Model & API: local engine = llama-server (11434), where the model name is just a label; API = any OpenAI-compatible endpoint (fill the three boxes: URL / key / model name). Restart the bot after switching."
    "＊ 「套用预设」：选中后端类型后点它，会把该家的 base_url 与建议模型名填进三格（Key 留空自填），填完仍可自由改。" = "＊ Preset: pick a backend type and click it — its base_url plus a suggested model name fill the three boxes (the key is left to you). Everything stays editable."
    "＊ 协议边界：全部预设都走 OpenAI 兼容协议。原生 Anthropic / Gemini 本项目不写转换代码——请在本机跑一个协议代理（LiteLLM / llm-rosetta 等），把代理地址填到「接口地址 URL」。" = "＊ Protocol boundary: every preset speaks the OpenAI-compatible protocol. This project writes no converters for native Anthropic / Gemini — run a protocol proxy locally (LiteLLM / llm-rosetta, …) and put the proxy address in 'API base URL'."
    "＊ SUPERUSERS 在 .env 里必须写成 JSON 数组，例：SUPERUSERS=['10001']（真实文件里用双引号）。保存时自动按此格式写入；手工改 .env 时务必照此格式——写成裸数字会让机器人启动失败（启动器侧却看不出问题）。" = "＊ In .env, SUPERUSERS must be a JSON array, e.g. SUPERUSERS=['10001'] (use double quotes in the real file). Saving writes that format automatically; if you edit .env by hand, keep it — a bare number makes the bot fail to start while the launcher still looks fine."
    "＊ 改 QQ 号 = 换身份：记忆 / 关系数值 / 事实 / 人设选择全部按 user_id 隔离，直接改 .env 会全部断链。正确流程＝① 勾选左侧确认框再保存；② 先跑迁移工具 qq-bot\tools\migrate_uid.py --from 旧QQ --to 新QQ --dry 看各表影响行数；③ 去掉 --dry 落盘执行（工具自动备份 memory.db）；④ 重启机器人。" = "＊ Changing the QQ number changes the identity: memory, relationship stats, facts and persona choice are all keyed by user_id, so editing .env directly breaks every link. Correct order: (1) tick the confirmation box on the left, then save; (2) run the migration tool qq-bot\tools\migrate_uid.py --from OLD --to NEW --dry to see how many rows each table is affected; (3) run it without --dry to apply (it backs up memory.db automatically); (4) restart the bot."
    "＊ 思考模式：自决（默认）=删除该 uid 的键；恒定开启/关闭=把该 uid 锁成 on / off，与聊天里 /思考 开|关 等价。写入 data\think_mode.json，需重启机器人生效。" = "＊ Thinking mode: Self (default) deletes that uid's key; Always on / Always off locks it to on / off, equivalent to /思考 on|off in chat. Writes data\think_mode.json and takes effect after restarting the bot."
    "＊ 换 QQ 有二次确认：不勾选左侧确认框则 QQ 号不写入，其余项照常保存。" = "＊ Changing the QQ number needs confirmation: unless the checkbox on the left is ticked, the QQ number is not written; the other items still save."
    # —— 干员页 ——
    "干员一览" = "Operators"; "设为启动人设" = "Set as start persona"
    # —— 日志页 ——
    "运行日志" = "Runtime log"; "看全文尾部（不勾=仅报错）" = "Tail the full log (unchecked = errors only)"; "刷新" = "Refresh"
    # —— 内容包 / 插件页 ——
    "插件管理" = "Pack manager"; "从文件夹安装" = "Install from folder"; "从 zip 安装" = "Install from zip"
    "刷新列表" = "Refresh list"; "作者" = "Author"; "许可" = "License"; "来源" = "Source"; "目录" = "Directory"
    "装了什么（读包内文件得出）" = "What it contains (read from the pack)"; "生效条件" = "Takes effect"; "名称" = "Name"
    # —— 状态页 ——
    "当前场景" = "Current scene"; "服务状态" = "Service status"
    "心跳时间线（滚动翻阅 · 最近 100 条）" = "Heartbeat timeline (last 100 entries)"
    "仅停机器人（LLM 保持）" = "Stop bot only (keep the LLM)"
    # —— 组件页 ——
    "依赖与组件" = "Dependencies & components"; "提供什么" = "What it provides"
    "缺了会怎样（降级路径）" = "What breaks without it (fallback path)"; "组件明细" = "Components"
    "重新检测" = "Re-detect"; "打开组件目录" = "Open component folder"; "打开下载页" = "Open download page"
    # —— 关于页 ——
    "QQ AI 陪伴机器人（框架 + 启动器）" = "QQ AI companion bot (framework + launcher)"
    "启动器版本" = "Launcher version"; "核心接口版本 CORE_API_VERSION" = "Core API version CORE_API_VERSION"
    "框架作者" = "Framework author"; "安装根目录" = "Install root"; "当前皮肤" = "Current skin"
    "名称 / 标识" = "Name / id"; "皮肤作者" = "Skin author"; "皮肤目录" = "Skin directory"
    "源码 / 发布页" = "Source / releases"; "框架" = "Framework"; "第三方组件" = "Third-party components"
    "见安装根下 docs\THIRD_PARTY.md（模型 / 语音 / 引擎 / 前端库各自许可）" = "See docs\THIRD_PARTY.md under the install root (licenses for the models / voice / engine / front-end libraries)"
    "发行条款" = "Distribution terms"
    "见安装根下 LICENSE（含第三方 IP 素材的处置约定）" = "See LICENSE under the install root (including how third-party IP assets are handled)"
    # —— 顶栏服务灯 tooltip ——
    "推理引擎 11434" = "Inference engine 11434"; "记忆服务 11435" = "Memory service 11435"
    "QQ 协议侧（NapCat）" = "QQ protocol side (NapCat)"; "机器人本体 8080" = "Bot itself 8080"
    # —— 运行期固定句（Show-Msg / 列表项；带变量的走下面的模式表）——
    "已有操作进行中，请稍候…" = "An operation is already running, please wait…"
    "已有操作进行中，请稍候" = "An operation is already running, please wait"
    "正在启动全部…（引擎就绪约 30-60 秒）" = "Starting everything… (the engine is ready in about 30-60 s)"
    "正在停止全部（含 LLM，显存释放）…" = "Stopping everything (including the LLM; VRAM is released)…"
    "正在退出并清空全部进程（含 LLM 引擎，显存释放）…" = "Exiting and clearing every process (including the LLM engine; VRAM is released)…"
    "该组件是外部程序，没有安装根内的目录" = "This component is an external program and has no folder inside the install root"
    "该组件没有可直接打开的下载页" = "This component has no download page to open directly"
    "机器人未启动：GAL 页要先让 bot 跑起来（顶栏「▶ 启动」，约 30-60 秒）" = "The bot is not running: the GAL page needs it up first (top bar ▶ Start, about 30-60 s)"
    "（未发现任何已装件——四类各自的「放哪里」见下面各组的提示行）" = "(Nothing installed — each of the four groups below says where its files belong)"
    "（读不到 launcher\deps.json——依赖矩阵缺失，无法检测）" = "(launcher\deps.json is unreadable — the dependency matrix is missing, cannot detect anything)"
    "（未选择）" = "(nothing selected)"; "在左侧点一个条目看详情。" = "Click an item on the left to see its details."
    "未声明" = "not declared"; "（未设置）" = "(not set)"; "（暂无人设卡数据——等待启动器 state.json 提供）" = "(No persona cards yet — waiting for the launcher's state.json)"
    "警告" = "Warning"; "错误" = "Error"
    "⚠（正在启动全部…）" = "⚠ (starting everything…)"
    "⚠（正在停止全部…）" = "⚠ (stopping everything…)"
    "界面语言" = "UI language"; "中文" = "中文"; "英文" = "English"
}
# 带变量插值的句子：正则 + 替换（$1… 为捕获组）。**只加"看得见"的整句**，
# 别把短词也塞进来（短词走精确表，正则会误伤）。
$script:UI_LANG_PATTERNS = @(
    @('^正在启动全部…（引擎就绪约 30-60 秒）$', 'Starting everything… (the engine is ready in about 30-60 s)'),
    @('^正在停止全部（含 LLM，显存释放）…$', 'Stopping everything (including the LLM; VRAM is released)…'),
    @('^正在停止全部（含 LLM）…$', 'Stopping everything (including the LLM)…'),
    @('^正在停止全部…$', 'Stopping everything…'),
    @('^正在停止机器人…$', 'Stopping the bot…'),
    @('^正在退出…$', 'Exiting…'),
    @('^正在启动全部…$', 'Starting everything…'),
    @('^正在载入…$', 'Loading…'),
    @('^就绪 (\d+) / 缺失 (\d+)（缺的都可以缺——每项都有降级路径，见右侧说明）$', 'Ready $1 / missing $2 (all of them are optional — each has a fallback, see the notes on the right)'),
    @('^已切换人设：(.+)（bot 在线切换，无需重启）$', 'Switched persona: $1 (switched live, no restart needed)'),
    @('^已设为主用人设：(.+)（重启机器人后生效）$', 'Set as the main persona: $1 (takes effect after restarting the bot)'),
    @('^已激活【(.+)】档：(.+)$', 'Activated profile [$1]: $2'),
    @('^已载入【(.+)】档的值到三格（\*\*尚未写入 \.env\*\*）。(.+)$', 'Loaded the [$1] profile into the three boxes (**not written to .env yet**). $2'),
    @('^三格已存入【(.+)】档（.*launcher\.json；\*\*未动 \.env\*\*）。$', 'The three boxes were saved into the [$1] profile (launcher.json; .env untouched).'),
    @('^已取到 (\d+) 个型号（下拉里选一个即写入模型名）：(.+)$', 'Fetched $1 models (pick one in the dropdown to fill the model name): $2'),
    @('^内容包 (\d+) · 舞台包 (\d+) · 皮肤 (\d+) · 插件 (\d+)$', 'Content packs $1 · Stage packs $2 · Skins $3 · Plugins $4'),
    @('^页面加载失败，已返回启动器主页：(.+)$', 'The page failed to load; returned to the launcher home: $1'),
    @('^页面加载失败：(.+)（启动器主页也打不开——检查 launcher\\web 是否完整）$', 'The page failed to load: $1 (the launcher home will not open either — check that launcher\web is complete)'),
    @('^切换失败：(.+)（bot 无此卡或未注册）$', 'Switch failed: $1 (the bot has no such card or it is not registered)'),
    @('^安装失败：(.+)$', 'Install failed: $1'),
    @('^打开失败：(.+)$', 'Open failed: $1'),
    @('^目录不存在：(.+)（把组件装到这里，或按右侧『装法』操作）$', 'No such folder: $1 (install the component there, or follow the "how to install" note on the right)'),
    @('^API 模式需要至少填写『接口地址 URL \+ 模型名』——模型段未保存$', 'API mode needs at least "API base URL + model name" — the model section was not saved'),
    @('^写入失败（检查 data 目录权限）$', 'Write failed (check the permissions of the data folder)'),
    @('^正在：(.+)$', 'Doing: $1'),
    @('^心情：(.+)    今天打算：(.+)$', 'Mood: $1    Plan for today: $2'),
    @('^当前生效（\.env）：(.+)$', 'Live now (.env): $1'),
    @('^本档【(.+)】(.+)$', 'This profile [$1]$2'),
    @('^→ 一致：本档就是当前生效的配置。$', '→ In sync: this profile is what is live.'),
    @('^→ ⚠️ 不一致：(.+)$', '→ ⚠️ Out of sync: $1'),
    @('^✗ (.+)$', '✗ $1'), @('^⚠️ (.+)$', '⚠️ $1'), @('^  （暂无——(.+)）$', '  (none yet — $1)')
)
function Get-UiLang {
    # 语言存 data/launcher.json 的 lang 键（gitignored 本机状态）；缺省 zh。
    # 只认 zh/en 两个值——未知值一律按 zh（宁可不翻，也不要出现半截英文界面）。
    try {
        $c = Get-LauncherCfg
        if ($c.ContainsKey("lang") -and ([string]$c["lang"]) -eq "en") { return "en" }
    } catch {}
    return "zh"
}
function Set-UiLang($lang) {
    $lang = if ($lang -eq "en") { "en" } else { "zh" }
    try {
        $d = @{ llm = [bool]$window.FindName("chkLlm").IsChecked; voice = [bool]$window.FindName("chkVoice").IsChecked }
    } catch { $d = @{ llm = $true; voice = $true } }
    try { $sel = $window.FindName("CmbSkin"); if ($sel -and $sel.SelectedItem) { $d["skin"] = [string]$sel.SelectedItem.Tag } } catch {}
    try {
        $profKeep = Get-LlmProfiles
        $d["llmProfile"] = [string]$profKeep.active
        $d["llmProfiles"] = @{ local = $profKeep.local; online = $profKeep.online }
    } catch {}
    $d["lang"] = $lang
    Set-LauncherCfg $d
    $script:uiLang = $lang
    return $lang
}
function Convert-UiText($s, $lang) {
    # 译文转换的唯一入口：先查精确表，再走正则表。**只处理"含中文且表里有"的串**，
    # 其余原样返回——绝不猜、绝不做逐词替换（那会把英文界面弄得面目全非）。
    if (-not $s -or $s.Length -eq 0) { return $s }
    $t = [string]$s
    if ($lang -eq "en") {
        if ($script:UI_LANG_TABLE.ContainsKey($t)) { return $script:UI_LANG_TABLE[$t] }
        foreach ($p in $script:UI_LANG_PATTERNS) {
            try { if ($t -match $p[0]) { $r = [regex]::Replace($t, $p[0], $p[1]); if ($r -ne $t) { return $r } } } catch {}
        }
        return $t
    }
    return $t
}
function Apply-UiLang($lang) {
    # 在**界面树**上换文（静态 XAML 文案 + ListBox 条目 + 代码拼出的句子都在树上被扫到）。
    # 切回中文靠注册表里的原文，不写第二张表；首次见到某个中文字符串时把原文记下来。
    try {
        if (-not $window) { return }
        $script:uiOrig = if ($script:uiOrig) { $script:uiOrig } else { @{} }
        $walk = { param($el)
            try {
                if ($el -is [System.Windows.Controls.TextBlock] -or $el -is [System.Windows.Controls.TextBox]) {
                    $cur = [string]$el.Text
                    if ($cur -match '[\u4e00-\u9fff]') {
                        if (-not $script:uiOrig.ContainsKey($el)) { $script:uiOrig[$el] = $cur }
                        if ($lang -eq "en") { $el.Text = Convert-UiText $cur "en" }
                    } elseif ($lang -eq "zh" -and $script:uiOrig.ContainsKey($el)) {
                        if ($cur -eq (Convert-UiText $script:uiOrig[$el] "en")) { $el.Text = $script:uiOrig[$el] }
                    }
                }
                if ($el -is [System.Windows.Controls.ContentControl] -and $el.Content -is [string]) {
                    $cur = [string]$el.Content
                    if ($cur -match '[\u4e00-\u9fff]') {
                        if (-not $script:uiOrig.ContainsKey($el)) { $script:uiOrig[$el] = $cur }
                        if ($lang -eq "en") { $el.Content = Convert-UiText $cur "en" }
                    } elseif ($lang -eq "zh" -and $script:uiOrig.ContainsKey($el)) {
                        if ($cur -eq (Convert-UiText $script:uiOrig[$el] "en")) { $el.Content = $script:uiOrig[$el] }
                    }
                }
                if ($el -is [System.Windows.Controls.Primitives.ButtonBase] -or $el -is [System.Windows.Controls.Control]) {
                    $tt = [string]$el.ToolTip
                    if ($tt -match '[\u4e00-\u9fff]' -and $lang -eq "en") {
                        $el.ToolTip = Convert-UiText $tt "en"
                    }
                }
                # ListBox / ComboBox 的字符串条目：动态生成的"整句"主要落在这里
                if ($el -is [System.Windows.Controls.ItemsControl]) {
                    for ($i = 0; $i -lt $el.Items.Count; $i++) {
                        $it = $el.Items[$i]
                        if ($it -is [string] -and $it -match '[\u4e00-\u9fff]' -and $lang -eq "en") {
                            $el.Items[$i] = Convert-UiText $it "en"
                        }
                    }
                }
            } catch {}
            foreach ($ch in ([System.Windows.LogicalTreeHelper]::GetChildren($el))) { & $walk $ch }
        }
        & $walk $window
    } catch {}
}
function Get-UiLangItems {
    # 语言下拉的两项（Content 用**各自语言的自称**，Tag 是存盘值）：中文项在两种语言下都写"中文"，
    # 英文项都写"English"——这是语言选择器的通行做法（用户可能正看着自己不熟的语言找出口）。
    return @(
        @{ text = "中文"; tag = "zh" },
        @{ text = "English"; tag = "en" }
    )
}

# ---------------- 皮肤包（2026-09-10 Phase2：launcher-skin-v1，规范见 docs/皮肤包接口规范-v1.md） ----------------
# generic=web 根（默认主页在根上）；arknights=web\skins\arknights（gitignore 皮肤包，公开发行不带→自动回落）
function Get-SkinDir {
    $skin = "generic"
    try { $c = Get-LauncherCfg; if ($c["skin"]) { $skin = [string]$c["skin"] } } catch {}
    if ($skin -ne "generic") {
        if (Test-Path "$root\launcher\web\skins\$skin\manifest.json") { return "$root\launcher\web\skins\$skin" }
        # 皮肤目录缺失（公开精简发行）→ 回落 generic 并留痕
        try { [System.IO.File]::AppendAllText("$script:logDir\boot_err.log", ((Get-Date).ToString("HH:mm:ss") + "  SKIN '$skin' 目录缺失，回落 generic" + [Environment]::NewLine)) } catch {}
    }
    return "$root\launcher\web"
}
function Get-SkinManifest {
    # 读活动皮肤 manifest.json（no-BOM UTF8 读，失败回落默认哈希）；字段集合与 skins/generic 样板完全一致
    $def = @{
        spec = "launcher-skin-v1"; name = "generic"; title = "简洁控制台"; entry = "../../index.html"
        palette = @{ bg = "#0f1216"; fg = "#e8eaed"; accent = "#4da3ff"; muted = "#8a919c" }
        statePath = "../../state.json"; author = ""; license = "MIT"
        note = "内置默认皮肤：通用样板实现（零第三方 IP，零图片素材）"
        # operaPage（2026-09-12）：本皮肤**自带干员页**吗？true = 框架顶栏「干员」与 cmd=operators
        # 都打开皮肤自己的干员视图（推 page:"opera" 由皮肤渲染）；false/缺省 = 用框架原生干员页。
        # 为什么要有这个键：皮肤自己设计的干员界面（如方舟样板的 #view-opera/detail.html）是**用户的**界面，
        # 框架不该拿自己的页面去顶替它；反过来第三方皮肤没做这个页时，框架必须兜底——两边都靠它区分。
        operaPage = $false
    }
    try {
        $mf = Join-Path (Get-SkinDir) "manifest.json"
        if (Test-Path $mf) {
            $j = [System.IO.File]::ReadAllText($mf, [System.Text.UTF8Encoding]::new($false)) | ConvertFrom-Json
            if ($j) {
                $def.spec = [string]$j.spec; $def.name = [string]$j.name; $def.title = [string]$j.title
                $def.entry = [string]$j.entry; $def.statePath = [string]$j.statePath
                $def.author = [string]$j.author; $def.license = [string]$j.license; $def.note = [string]$j.note
                # 只认显式 true（字符串 "true"/1 也照收）：写成 "false"/其他值一律按"没有自带页"处理 = 安全兜底
                if ($null -ne $j.operaPage) { $def.operaPage = [bool]($j.operaPage -match "^(?i:true|1|yes|on)$") }
                if ($j.palette) {
                    $def.palette = @{ bg = [string]$j.palette.bg; fg = [string]$j.palette.fg; accent = [string]$j.palette.accent; muted = [string]$j.palette.muted }
                }
            }
        }
    } catch {}
    return $def
}
function Get-SkinEntryUrl {
    # 皮肤入口页的 WebView2 导航 URL（虚拟主机 app.local→launcher\web 固定映射，skins 在其下天然可达）
    if ((Get-SkinDir) -eq "$root\launcher\web") { return "https://app.local/index.html" }
    return "https://app.local/skins/" + (Split-Path (Get-SkinDir) -Leaf) + "/index.html"
}

# ---------------- .env 开关（LIVE_DANMAKU_ENABLED / LIVE_AUDIO_PLAYBACK / WEBGAL_CHAT_PERSIST，2026-09-09） ----------------
# 读写 E:\robot\qq-bot\.env：键存在→原行改值；缺失→文件尾追加；保持 UTF-8 无 BOM 与原行尾
# （逐行正则只替换命中行，未触行的 CRLF/LF 原样保留；文件无尾换行时先补一个再追加，不拼进末行）
$script:envFile = "$root\qq-bot\.env"  # bot(nonebot) 从 cwd=qq-bot 读此文件——根 .env 不生效（2026-09-09 修正）
function Get-EnvValue($key) {
    try {
        if (-not (Test-Path $script:envFile)) { return "" }
        $raw = [System.IO.File]::ReadAllText($script:envFile, [System.Text.UTF8Encoding]::new($false))
        # 仅认未注释的 KEY=... 行（# 开头的注释行不算已设置）
        foreach ($m in [regex]::Matches($raw, "(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^\r\n]*)")) {
            if ($m.Groups[1].Value -eq $key) { return $m.Groups[2].Value.Trim() }
        }
    } catch {}
    return ""
}
function Get-EnvFlag($key) {
    # 与 bot 侧 _env_flag 同口径：true/1/yes/on（不分大小写）为开；缺省/空/注释=关
    $v = Get-EnvValue $key
    return ($v -ne "" -and $v -match "^(?i:true|1|yes|on)$")
}
function Set-EnvValue($key, $value) {
    try {
        $enc = [System.Text.UTF8Encoding]::new($false)
        $raw = ""
        if (Test-Path $script:envFile) { $raw = [System.IO.File]::ReadAllText($script:envFile, $enc) }
        # 追加行行尾跟随文件现状（含 CRLF→CRLF；否则 LF）
        $nl = if ($raw -match "`r`n") { "`r`n" } else { "`n" }
        $rx = "(?m)^(\s*" + [regex]::Escape($key) + "\s*=\s*)([^\r\n]*)"
        $m = [regex]::Match($raw, $rx)
        if ($m.Success) {
            # 子串手术（Remove+Insert）：不经过替换语法，value 含 $ 等字符也安全；不破坏该行 CRLF
            $raw = $raw.Remove($m.Groups[2].Index, $m.Groups[2].Length).Insert($m.Groups[2].Index, $value)
        } else {
            if ($raw.Length -gt 0 -and -not ($raw.EndsWith("`n"))) { $raw += $nl }
            $raw += "$key=$value$nl"
        }
        [System.IO.File]::WriteAllText($script:envFile, $raw, $enc)
        return $true
    } catch { return $false }
}

# ---------------- 设置页 JSON 配置读写（2026-09-12 T4.2：think_mode.json） ----------------
function Get-CfgJsonFile($file) {
    $d = @{}
    try {
        if (Test-Path $file) {
            $j = [System.IO.File]::ReadAllText($file, [System.Text.UTF8Encoding]::new($false)) | ConvertFrom-Json
            if ($j) { foreach ($p in $j.PSObject.Properties) { $d[$p.Name] = $p.Value } }
        }
    } catch {}
    return ,$d
}
function Write-CfgJsonFile($file, $data) {
    # 与项目原子写同口径：UTF-8 无 BOM 整写（ConvertTo-Json 缩进 2，与 atomics.write_json_atomic 一致）
    try {
        $dir = Split-Path -Parent $file
        if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
        [System.IO.File]::WriteAllText($file, ($data | ConvertTo-Json -Depth 6), [System.Text.UTF8Encoding]::new($false))
        return $true
    } catch { return $false }
}

# ---------------- 身份区（2026-09-12 T5.1） ----------------
function Get-OwnerQqEnv {
    # .env 的 SUPERUSERS 是 JSON 数组（["10001"]）——只取首个元素；解析失败回落空串（调用方据此不写入，不误删）
    try {
        $raw = Get-EnvValue "SUPERUSERS"
        if (-not $raw) { return "" }
        $j = $raw | ConvertFrom-Json
        $first = @($j)[0]
        if ($null -ne $first) { return [string]$first }
    } catch {}
    return ""
}
function Normalize-Qq($v) {
    # 去掉引号/方括号/空白，只留数字（保存时由 Format-QqArray 重新格式化为 JSON 数组）。
    # 注：PowerShell 双引号串内反斜杠-引号 不构成转义（"会截断字符串，实测解析失败），
    # 故不用字符类正则，改为逐个字符过滤，只留 0-9。
    $o = ""
    foreach ($ch in ([string]$v).ToCharArray()) {
        if ($ch -match "[0-9]") { $o += $ch }
    }
    return $o
}
function Format-QqArray($qq) {
    # 关键：SUPERUSERS 必须写成 JSON 数组。两个实测坑：
    #   ① @(...) | ConvertTo-Json 对【单元素数组】会解包成裸字符串 —— 得到 "10001"，
    #      经 ConvertFrom-Json 变 String，再取 @(j)[0] 只拿到第一个字符 "1"，
    #      Get-FirstSuperuser 会静默返回错 uid（比抛异常更危险）。
    #   ② PS 5.1 没有 ConvertTo-Json -AsArray（那是 PS 7+）。
    # 故按 JSON 规范显式拼接，并用 ConvertFrom-Json 自校验（写坏宁可返回空串，调用方不写入）。
    $v = ([string]$qq).Trim()
    if (-not ($v -match "^[0-9]+$")) { return "" }
    $js = '["' + $v + '"]'
    try {
        $back = $js | ConvertFrom-Json
        if (@($back)[0] -ne $v) { return "" }
    } catch { return "" }
    return $js
}
function Get-ThinkModeState {
    # 读 think_mode.json 的首个 uid 及其三态值；文件缺失/为空 = 自决
    $st = @{ uid = ""; mode = "auto" }
    $f = "$root\data\think_mode.json"
    if (-not (Test-Path $f)) { return $st }
    try {
        $raw = [System.IO.File]::ReadAllText($f, [System.Text.UTF8Encoding]::new($false)).Trim()
        if (-not $raw) { return $st }
        $j = $raw | ConvertFrom-Json
        $props = @($j.PSObject.Properties)
        if ($props.Count -gt 0) {
            $st.uid = [string]$props[0].Name
            $v = [string]$props[0].Value
            if ($v -eq "on" -or $v -eq "off") { $st.mode = $v }
        }
    } catch {}
    return $st
}

# ---------------- Provider 预设（2026-09-12 T5.2；全部走 OpenAI 兼容协议） ----------------
# Tag 决定 .env 的 LLM_PROVIDER（仍是 local / openai_compat 二值，core/llm.py 语义不变）：
# 只有「本地引擎」是 local；其余全部 OpenAI 兼容端点。url="" 表示该项不指定地址（三格留原值/待填）。
# 协议边界：本项目不写协议转换代码——原生 Anthropic / Gemini 请在本机跑协议代理，把代理 URL 填到这里。
#
# ★★ 型号名是有保质期的（2026-09-12 用户指出：DeepSeek 早已不用 `deepseek-chat`，现行无版本号写法
#    是 `deepseek-flash`＝V4.1 Flash）。这张表因此采用**两条策略**，别再往里塞"看着像对的"型号名：
#      ① 预设**只保证端点地址正确**，另给一个"此刻有效"的型号作起点（拿不准的**一律留空**）；
#      ② 型号由界面上的「拉取型号」按钮按端点**实时取**（OpenAI 兼容的 GET {url}/models）——
#         模型清单归服务商维护，启动器不维护一张必然过期的表。
#    维护约定：改这里的型号名必须写日期 + 依据（官方文档/服务端 /models 实测），否则半年后没人敢动。
function Get-ProviderPresets {
    $p = @{}
    $p["本地引擎（local · llama-server）"] = @{ tag = "local"; url = "http://127.0.0.1:11434/v1"; model = ""
        note = "本机 llama-server：型号名随便（引擎只加载一个 gguf）；也点「拉取型号」看它在服务哪个名字" }
    $p["OpenAI 兼容（通用）"] = @{ tag = "openai_compat"; url = ""; model = ""
        note = "任何 OpenAI 兼容端点；型号名照服务商文档填，或填好 URL+Key 后点「拉取型号」" }
    $p["DeepSeek"] = @{ tag = "openai_compat"; url = "https://api.deepseek.com/v1"; model = "deepseek-flash"
        note = "现行无版本号写法 = deepseek-flash（V4.1 Flash，2026-09-12 核实）；更高档位/思考档按官方文档与 LLM_THINKING_PARAM 定" }
    $p["阿里云百炼 DashScope（兼容模式）"] = @{ tag = "openai_compat"; url = "https://dashscope.aliyuncs.com/compatible-mode/v1"; model = ""
        note = "qwen 系列档位更迭快，故**不预填**型号名：填好 Key 后点「拉取型号」现取（见官方模型列表）" }
    $p["Ollama"] = @{ tag = "openai_compat"; url = "http://127.0.0.1:11434/v1"; model = ""
        note = "本机 Ollama：型号 = 你用 ollama pull 过的名字，点「拉取型号」能列出来" }
    $p["vLLM / LM Studio（自建）"] = @{ tag = "openai_compat"; url = ""; model = ""
        note = "自建端点：URL 形如 http://<主机>:<端口>/v1；型号名以自建服务的 /models 为准" }
    $p["OpenRouter"] = @{ tag = "openai_compat"; url = "https://openrouter.ai/api/v1"; model = ""
        note = "型号名形如 vendor/model（如 deepseek/deepseek-flash），点「拉取型号」现取最稳" }
    $p["智谱 GLM"] = @{ tag = "openai_compat"; url = "https://open.bigmodel.cn/api/paas/v4"; model = ""
        note = "GLM 档位更迭快，故**不预填**型号名：点「拉取型号」现取" }
    $p["Moonshot Kimi"] = @{ tag = "openai_compat"; url = "https://api.moonshot.cn/v1"; model = ""
        note = "Kimi 型号名更迭快（kimi-k2 系列等），故**不预填**：点「拉取型号」现取" }
    $p["自定义 / 经协议代理"] = @{ tag = "openai_compat"; url = ""; model = ""
        note = "原生 Anthropic / Gemini 请在本机跑协议代理，把代理地址与型号填这里" }
    return ,$p
}
function Get-ModelIds {
    # 按当前三格里的 URL+Key，向端点要**模型清单**（OpenAI 兼容 GET {base}/models）。
    # 为什么做这个：预设里的型号名会过期（用户实测：deepseek-chat 早没了），
    # 而"清单"本来就是服务商的事实——让用户一键现取，比在启动器里维护一张表可靠。
    # 返回 @{ ok=bool; ids=@(); err="" }，不抛异常（界面要好文案，不要红栈）。
    $res = @{ ok = $false; ids = @(); err = "" }
    try {
        $u = "$($window.FindName('TxtApiUrl').Text)".Trim()
        $k = "$($window.FindName('TxtApiKey').Text)".Trim()
        if (-not $u) { $res.err = "先把『接口地址 URL』填上"; return $res }
        $url = $u.TrimEnd("/") + "/models"
        $hdr = @{}
        if ($k) { $hdr["Authorization"] = "Bearer $k" }
        $j = Invoke-RestMethod -Uri $url -Headers $hdr -TimeoutSec 12 -ErrorAction Stop
        $ids = @()
        foreach ($it in @($j.data)) { if ($it -and $it.id) { $ids += [string]$it.id } }
        if (-not $ids.Count) {
            # 有些端点把清单放在别的键（或返回裸数组）：退一步全树找 id 字段，仍取不到就算失败
            foreach ($it in @($j)) { if ($it -and $it.id) { $ids += [string]$it.id } }
        }
        if (-not $ids.Count) { $res.err = "端点返回里没有型号清单（data[].id）——该端点可能不支持 GET /models"; return $res }
        $res.ok = $true; $res.ids = @($ids | Sort-Object -Unique)
    } catch {
        $res.err = "拉取失败：" + $_.Exception.Message
    }
    return $res
}
function Get-ProviderPresetNames {
    return @(
        "本地引擎（local · llama-server）", "OpenAI 兼容（通用）", "DeepSeek",
        "阿里云百炼 DashScope（兼容模式）", "Ollama", "vLLM / LM Studio（自建）",
        "OpenRouter", "智谱 GLM", "Moonshot Kimi", "自定义 / 经协议代理"
    )
}

# ---------------- 探测（双证据：端口 + 进程——防端口僵尸/进程残留导致的灯误判） ----------------
function Test-Http($url) {
    try { Invoke-RestMethod $url -TimeoutSec 2 | Out-Null; return $true } catch { return $false }
}
$script:probeCache = @{}
# WebView 导航状态（2026-09-12）：NavigationCompleted 的 args **不带 Uri**，回退导航要靠自己记；
# navCanceled 用来区分"我们主动拦下的一次导航"（同样以 IsSuccess=false 回来）与真正的加载失败。
$script:lastNavUri = ""
$script:navCanceled = $false
$script:navFailCount = 0
function Probe($name, $scriptBlock) {
    $now = Get-Date
    if ($script:probeCache.ContainsKey($name) -and ($now - $script:probeCache[$name].t).TotalSeconds -lt 120) { return $script:probeCache[$name].v }
    $v = $false
    try { $v = & $scriptBlock } catch {}
    $script:probeCache[$name] = @{ v = $v; t = $now }
    return $v
}
function Test-Engine {
    try { return ([bool](Get-Process -Name "llama-server" -ErrorAction SilentlyContinue)) -and (Test-TcpFast "127.0.0.1" 11434) } catch { return $false }
}
# 快速 TCP 探测（TcpClient 直连：毫秒级，替代慢速 Get-NetTCPConnection）
function Test-TcpFast($hostName, $port) {
    try {
        $tc = New-Object System.Net.Sockets.TcpClient
        try {
            $ar = $tc.BeginConnect($hostName, $port, $null, $null)
            if (-not $ar.AsyncWaitHandle.WaitOne(500)) { return $false }
            $tc.EndConnect($ar)
            return $true
        } catch { return $false }
        finally { try { $tc.Close() } catch {} }
    } catch { return $false }
}
function Test-Embedding {
    Probe "embed" { return (Test-TcpFast "127.0.0.1" 11435) }
}
function Test-Napcat {
    Probe "nap" { if (Get-Process -Name "NapCatWinBootMain" -ErrorAction SilentlyContinue) { return $true }; $n = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq "node.exe" -and $_.CommandLine -match "napcat" }; return [bool]$n }
}
function Test-Bot {
    Probe "bot" { return (Test-TcpFast "127.0.0.1" 8080) }
}
# ---------------- 依赖/能力检测（S5，2026-09-12） ----------------
# ★ 唯一事实源是 launcher/deps.json（部署指南的矩阵表由它生成）。这里**只读不写**，
#   绝不在启动器里另抄一份依赖关系——同一事实两处写必然漂移，这是本项目的慢性病。
function Get-Deps {
    try {
        $f = Join-Path $script:launcherDir "deps.json"
        if (-not (Test-Path $f)) { return @() }
        $j = Get-Content $f -Raw -Encoding UTF8 | ConvertFrom-Json
        return ,@($j.features)
    } catch { return @() }
}
# 检测分发：kind 由 deps.json 声明，启动器只负责"怎么做这个探测"。
# 新增探测类型时**两处一起改**（这里 + deps.json 的 kind 取值），且默认分支必须是 false
# （未知 kind 视为"没检测到"，不是"检测通过"——fail-closed）。
function Test-DepFeature($f) {
    try {
        $d = $f.detect
        if (-not $d) { return $false }
        switch ("$($d.kind)") {
            "port"    { return (Test-TcpFast "127.0.0.1" ([int]$d.value)) }
            "process" { return (Test-Napcat) }
            "path"    { return (Test-Path (Join-Path $root "$($d.value)")) }
            "env"     { $v = Get-EnvValue "$($d.value)"; return ($v -and (@("1", "true", "yes", "on") -contains $v.Trim().ToLower())) }
            default   { return $false }
        }
    } catch { return $false }
}
# ---------------- 已装内容包扫描（2026-09-12 上移至此：见下方说明） ----------------
# ★ 为什么这个函数在**这里**而不是跟插件管理区块放一起：PowerShell 的函数是"解释到定义那一行"
#   才存在，不是解析期注册。而无界面模式（-Mode X）在**窗口创建之前**就 exit 了，于是它只能调用
#   定义在 dispatch 之前的函数。原先本函数定义在文件后半段（插件管理区），
#   `-Mode packs` → Get-InstalledItems → Get-PackDirs 就拿到 CommandNotFoundException——
#   而脚本级 trap 会吞掉它并 continue，表现为**进程静默挂死**（实测：无任何输出、也不退出）。
#   教训：数据层读取器必须定义在 dispatch 之前。加新的无界面模式时先看这里。
function Get-PackDirs {
    $out = @()
    foreach ($rootDir in @("$root\qq-bot\packs", "$root\data\packs")) {
        if (-not (Test-Path $rootDir)) { continue }
        $src = if ($rootDir -like "*\data\*") { "用户" } else { "内置" }
        foreach ($d in (Get-ChildItem $rootDir -Directory -ErrorAction SilentlyContinue)) {
            $mf = Join-Path $d.FullName "pack.json"
            if (-not (Test-Path $mf)) { continue }
            try {
                $j = Get-Content $mf -Raw -Encoding UTF8 | ConvertFrom-Json
                if ("$($j.spec)" -ne "robot-pack-v1") { continue }
                # 2026-09-12：把 pack.json 里本来就写着的**作者/许可**带出来——列表有这两列却恒显示"未声明"，
                # 而"来源可见 + 双重署名"（S6）正是这一页存在的理由。包声明什么就显示什么，取不到才写"未声明"。
                $out += [pscustomobject]@{ name = "$($j.name)"; type = "$($j.type)"; ver = "$($j.version)"
                                           title = "$($j.title)"; src = $src; dir = $d.FullName
                                           author = "$($j.author)"; license = "$($j.license)" }
            } catch {}
        }
    }
    return ,$out
}
function Get-PackSummary($p) {
    # 「装了什么」——只读包内文件给**事实**，不猜。四类关心的东西不同：
    #   内容包 → 卡键 / 台词条数 / 差分映射条数 / 世界观段 / 素材文件数
    #   舞台包 → 背景张数与 id / 差分张数 / 场景映射条数 / 默认背景
    #   皮肤   → spec / 入口 / 落盘路径 / 主色
    #   插件   → 形态（子包 or 单文件）与有没有清单
    # 返回字符串数组：GUI 右栏与 `-Mode packs` 用**同一份**——否则"界面说的"和"命令行说的"会分家。
    $out = @()
    $d = [string]$p.dir
    if (-not $d -or -not (Test-Path $d)) { return @("（目录不存在：$d）") }
    $j = $null
    $pj = Join-Path $d "pack.json"
    if (Test-Path $pj) { try { $j = Get-Content $pj -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $j = $null } }
    switch ("$($p.kind)") {
        "内容包" {
            if ($j -and $j.card) { $out += ("卡键 card.key：" + [string]$j.card.key) }
            $q = Join-Path $d "quotes.json"
            if (Test-Path $q) {
                try {
                    $qj = Get-Content $q -Raw -Encoding UTF8 | ConvertFrom-Json
                    $out += ("台词 quotes.json：" + @($qj.quotes).Count + " 条")
                } catch { $out += "quotes.json 存在但读不出来（格式问题）" }
            } else { $out += "无 quotes.json（GAL 页签名台词走兜底）" }
            $sm = Join-Path $d "sprites\manifest.json"
            if (Test-Path $sm) {
                try {
                    $sj = Get-Content $sm -Raw -Encoding UTF8 | ConvertFrom-Json
                    $out += ("差分词映射 map：" + @($sj.map.PSObject.Properties).Count + " 条")
                } catch { $out += "sprites\manifest.json 读不出来（格式问题）" }
            }
            foreach ($f in @("card.json", "world.json")) {
                if (Test-Path (Join-Path $d $f)) { $out += ("有 " + $f) }
            }
            $sp = Join-Path $d "sprites"
            if (Test-Path $sp) {
                $n = @(Get-ChildItem $sp -Recurse -File -ErrorAction SilentlyContinue).Count
                $out += ("sprites\ 素材 $n 个（v1 不自动搬运；立绘位仍是 launcher\cards\）")
            }
        }
        "舞台包" {
            $bg = Join-Path $d "bg"
            if (Test-Path $bg) {
                $files = @(Get-ChildItem $bg -File -ErrorAction SilentlyContinue)
                $ids = ($files | Select-Object -First 6 | ForEach-Object { $_.BaseName }) -join ", "
                $out += ("背景 bg\：" + $files.Count + " 张 ｜ " + $ids)
            } else { $out += "无 bg\ 目录（没有背景 = 不会产生任何可见变化）" }
            $df = Join-Path $d "sprites\diff"
            if (Test-Path $df) {
                $out += ("差分 sprites\diff\：" + @(Get-ChildItem $df -File -ErrorAction SilentlyContinue).Count + " 张（**会覆盖**本机同名差分）")
            } else { $out += "无 sprites\diff\（不覆盖本机 launcher\cards\bust4w 的差分）" }
            if ($j -and $j.stage) {
                $out += ("默认背景 default_bg：" + [string]$j.stage.default_bg + " ｜ 备选 " + [string]$j.stage.bg_alt)
                $map = $j.stage.bg_by_scene
                if ($map) {
                    $keys = @($map.PSObject.Properties) | ForEach-Object { $_.Name }
                    $out += ("场景映射 " + @($keys).Count + " 条：" + ($keys -join ", "))
                }
            }
        }
        "皮肤" {
            $mf = Join-Path $d "manifest.json"
            if (Test-Path $mf) {
                try {
                    $sj = Get-Content $mf -Raw -Encoding UTF8 | ConvertFrom-Json
                    $out += ("spec：" + [string]$sj.spec + " ｜ 入口：" + [string]$sj.entry)
                    $out += ("state.json 落盘：" + [string]$sj.statePath)
                    if ($sj.palette) { $out += ("配色：底 " + [string]$sj.palette.bg + " ｜ 强调 " + [string]$sj.palette.accent) }
                } catch { $out += "manifest.json 读不出来（格式问题）" }
            }
            # 入口要按清单里的 entry 解析，**不能硬查 index.html**：generic 皮肤的 entry 是
            # `../../index.html`（设计上默认主页就放在 web 根，不各自复制一份）——
            # 硬查会给它报"皮肤不完整"的假警告（本条正是 -Mode packs 实跑时抓到的）。
            $entry = [string]$sj.entry
            if ($entry) {
                $resolved = Join-Path $d $entry
                if (Test-Path $resolved) {
                    $out += ("入口可加载：" + $entry + " → " + (Resolve-Path $resolved).Path)
                } else {
                    $out += ("⚠ 入口 " + $entry + " 不存在（皮肤不完整，启动器会回落 generic）")
                }
            } else { $out += "⚠ 清单没写 entry——启动器无法定位皮肤页" }
        }
        default {
            $out += $(if (Test-Path (Join-Path $d "__init__.py")) { "形态：子包（__init__.py）" } else { "形态：单文件 .py" })
            $out += $(if (Test-Path $pj) { "有 pack.json 清单" } else { "无清单（代码插件不需要；作者/许可按未声明显示）" })
        }
    }
    if (-not @($out).Count) { $out = @("（没有可摘要的内容）") }
    return $out
}
function Get-PackNote($p) {
    # 「生效条件」：装了不等于生效——这是装包最常见的困惑，写清到底要做什么。
    switch ("$($p.kind)") {
        "内容包" { return "重启 bot 后生效（或调 core.packs.reload()）。卡包还要刷新 launcher\data\cards.json 才会出现在「干员」页——用 qq-bot\tools\export_cards.py。" }
        "舞台包" { return "重启 bot 后并入 /gal/content.json；GAL 页刷新即见。背景按 URL 取图，差分则优先用包内同名文件。" }
        "皮肤" { return "重启启动器（或在「设置」页切皮肤）。皮肤是外观层，不影响 bot 行为。" }
        default { return "重启 bot 生效（bot.py 自动发现 data\plugins\；坏插件会被隔离并记 WARNING，不会拖垮 bot）。" }
    }
}
function Show-PackDetail($item) {
    try {
        $s = { param($n, $v) $t = $window.FindName($n); if ($t) { $t.Text = $v } }
        if (-not $item) {
            & $s "pkTitle" "（未选择）"
            & $s "pkKind" ""; & $s "pkAuthor" ""; & $s "pkLicense" ""
            & $s "pkSrc" ""; & $s "pkDir" ""; & $s "pkSummary" "在左侧点一个条目看详情。"
            & $s "pkNote" ""
            return
        }
        & $s "pkTitle" $(if ($item.title) { [string]$item.title } else { [string]$item.name })
        & $s "pkKind" ([string]$item.kind + " ｜ type=" + [string]$item.type)
        & $s "pkAuthor" $(if ($item.author) { [string]$item.author } else { "未声明" })
        & $s "pkLicense" $(if ($item.license) { [string]$item.license } else { "未声明" })
        & $s "pkSrc" ([string]$item.src)
        & $s "pkDir" ([string]$item.dir)
        & $s "pkSummary" ((Get-PackSummary $item) -join "`n")
        & $s "pkNote" (Get-PackNote $item)
    } catch {}
}
function Get-InstalledItems {
    # 三类已装件统一清单：内容包（双根 robot-pack-v1）· 皮肤（launcher-skin-v1）· 第三方插件（data/plugins）。
    # 为什么要合并展示：它们在目录约定上是"三层分离"，但对用户是同一个问题——"我装了什么、谁做的、什么许可"。
    $out = @()
    foreach ($p in (Get-PackDirs)) {
        # 2026-09-12：舞台包（type=gal）单列一类——它给的是**图**（bg/ 背景 + sprites/diff/ 差分），
        # 与卡包给的"词"不是一回事；混在"内容包"里，用户看不出自己装的到底是哪一类。
        $kd = if ("$($p.type)" -eq "gal") { "舞台包" } else { "内容包" }
        $out += [pscustomobject]@{ kind = $kd; name = $p.name; type = $p.type; title = $p.title
                                   src = $p.src; dir = $p.dir; author = $p.author; license = $p.license }
    }
    $sk = Join-Path $root "launcher\web\skins"
    if (Test-Path $sk) {
        foreach ($d in (Get-ChildItem $sk -Directory -ErrorAction SilentlyContinue)) {
            $mf = Join-Path $d.FullName "manifest.json"
            if (-not (Test-Path $mf)) { continue }
            try {
                $j = Get-Content $mf -Raw -Encoding UTF8 | ConvertFrom-Json
                if ("$($j.spec)" -ne "launcher-skin-v1") { continue }
                $out += [pscustomobject]@{ kind = "皮肤"; name = "$($j.name)"; type = "skin"; title = "$($j.title)"
                                           src = "用户/内置"; dir = $d.FullName; author = "$($j.author)"; license = "$($j.license)" }
            } catch {}
        }
    }
    $dp = Join-Path $root "data\plugins"
    if (Test-Path $dp) {
        foreach ($d in (Get-ChildItem $dp -ErrorAction SilentlyContinue)) {
            if ($d.Name.StartsWith(".") -or $d.Name.StartsWith("_")) { continue }
            if ($d.PSIsContainer) {
                if (-not (Test-Path (Join-Path $d.FullName "__init__.py"))) { continue }
                $nm = $d.Name
            } elseif ($d.Extension -eq ".py") {
                $nm = $d.Name -replace "\.py$", ""
            } else { continue }
            # 无清单的代码插件：作者/许可能为空——如实显示"未声明"，不编
            $out += [pscustomobject]@{ kind = "插件"; name = $nm; type = "code"; title = "（无清单）"
                                       src = "第三方"; dir = $d.FullName; author = ""; license = "" }
        }
    }
    return ,$out
}
function Get-LifeState {
    $s = @{ scene = "-"; doing = "-"; mood = "-"; plan = "-"; wake = "-" }
    try { if (Test-Path "$root\data\life_state.json") {
        $j = Get-Content "$root\data\life_state.json" -Raw -Encoding UTF8 | ConvertFrom-Json
        $s.scene = $(if ($j.scene) { $j.scene } else { "—" })
        $s.doing = $(if ($j.doing) { $j.doing } else { "—" })
        $s.mood  = $(if ($j.mood)  { $j.mood  } else { "—" })
        $s.plan  = $(if ($j.plan)  { $j.plan  } else { "—" })
        $wake = [double]$j.wake_min; $ts = [double]$j.ts
        if ($wake -and $ts) {
            $next = (Get-Date -UnixTimeSeconds ([long]($ts + $wake * 60 + 1))) - (Get-Date)
            $s.wake = if ($next.TotalMinutes -ge 1) { "下次心跳：约 $([math]::Ceiling($next.TotalMinutes)) 分钟后" } else { "下次心跳：$([math]::Ceiling($next.TotalSeconds)) 秒后" }
        } elseif ($ts) { $s.wake = "下次心跳：默认 30 分钟" }
    } } catch {}
    return $s
}
function Get-LifeLog($n = 100) {
    $out = @()
    try {
        if (Test-Path "$root\data\life_log.jsonl") {
            foreach ($l in (Get-Content "$root\data\life_log.jsonl" -Encoding UTF8 -Tail $n)) {
                try { $e = $l | ConvertFrom-Json
                    # 2026-09-07 修复：Get-Date -UnixTimeSeconds 是 PS7 参数；DateTimeOffset 链式调用在循环内
                    # 也不稳定——改用 AddSeconds（实测可用）
                    $t = (Get-Date '1970-01-01Z').AddSeconds([long]$e.ts).ToLocalTime().ToString('MM-dd HH:mm')
                    $m = $(if ($e.mood) { "[" + $e.mood + "] " } else { "" })
                    $out += "$t  $m$($e.doing)"
                } catch {}
            }
            # 2026-09-11 用户裁决：时间线倒序——最新在最上（文件序为旧→新，整体反转）
            $out = @($out); [array]::Reverse($out)
        }
    } catch {}
    return $out
}


# ---------------- 报错收集（仅报错） ----------------
$logFiles = @(
    "$root\data\bot.log", "$root\data\bot.err.log", "$root\data\llama-server.err.log", "$root\data\embedding.err.log"
)
$errRe = 'ERROR|CRITICAL|Traceback|Exception|失败|错误|failed'
function Get-Errors {
    $out = @()
    try {
        foreach ($f in $logFiles) {
            if (-not (Test-Path $f)) { continue }
            $lines = Get-Content $f -Encoding UTF8 -Tail 300 -ErrorAction SilentlyContinue |
                Where-Object { $_ -match $errRe } | Select-Object -Last 40
            foreach ($l in $lines) { if ($l.Length -gt 300) { $l = $l.Substring(0, 300) + "…" }; $out += "[$(Split-Path $f -Leaf)] $l" }
        }
    } catch {}
    if ($out.Count -gt 150) { $out = $out[($out.Count - 150)..($out.Count - 1)] }
    return $out
}
function Format-Size($b) {
    # 人类可读体积（日志页与 -Mode logs 共用）
    if ($b -ge 1048576) { return ("{0:N1} MB" -f ($b / 1048576)) }
    if ($b -ge 1024) { return ("{0:N0} KB" -f ($b / 1024)) }
    return ("$b B")
}
function Get-LogFiles {
    # 存在的日志文件（按体积降序）。界面用它把"日志有多大"摆出来——维护手册 §3.2 讲日志增长，
    # 界面不显示体积，用户就没机会发现"这个日志一直在长"（webstep.log 曾涨到 9.3 MB 无人察觉）。
    $out = @()
    foreach ($f in $logFiles) {
        if (Test-Path $f) {
            $i = Get-Item $f
            $out += [pscustomobject]@{ path = $f; name = $i.Name; size = $i.Length; mtime = $i.LastWriteTime }
        }
    }
    return ,@($out | Sort-Object size -Descending)
}
function Get-LogTail($n = 200) {
    # 各日志文件的**尾部**（与 Get-Errors 同形状：带 [文件名] 前缀、超长行截断），供"看全文"视图。
    # 为什么要有它：仅报错视图看不到"错误之前发生了什么"，而排查往往正是要看那一段。
    $out = @()
    foreach ($f in $logFiles) {
        if (-not (Test-Path $f)) { continue }
        $lines = Get-Content $f -Encoding UTF8 -Tail $n -ErrorAction SilentlyContinue
        foreach ($l in $lines) { if ($l.Length -gt 300) { $l = $l.Substring(0, 300) + "…" }; $out += "[$(Split-Path $f -Leaf)] $l" }
    }
    if ($out.Count -gt $n) { $out = $out[($out.Count - $n)..($out.Count - 1)] }
    return $out
}

# ---------------- 启动 ----------------
function Start-Engine {
    if (Test-Engine) { return "推理引擎已在运行。" }
    $modelMode = "gemma"
    try { $mm = Get-Content "$root\data\model_mode.json" -Raw -Encoding UTF8 | ConvertFrom-Json
        $vals = @($mm.PSObject.Properties | ForEach-Object { $_.Value })
        if ($vals.Count -gt 0) { $modelMode = [string]$vals[-1] } } catch {}
    $args = @()
    if ($modelMode -eq "gemma") {
        # 2026-09-09：与 start.ps1 权威参数对齐（parallel 1 + repeat-penalty——曾漂移 parallel 2/无 penalty=虚空 bug 源）
        $args = @("-m", "$root\data\models\gemma4-12b\gemma-4-12B-it-heretic-Q4_K_M.gguf",
            "--host", "127.0.0.1", "--port", "11434", "-c", "32768", "-ngl", "99", "--parallel", "1",
            "-ctk", "q8_0", "-ctv", "q8_0", "--reasoning", "on", "--reasoning-budget", "200",
            "--repeat-penalty", "1.15", "--repeat-last-n", "256", "--spec-type", "ngram-simple")
    } else {
        # Qwen3-14B 已删除（与 start.ps1 同款报错口径）——防死分支拉起已不存在模型
        return "模型模式异常：Qwen3-14B 已删除，请设为 gemma"
    }
    Start-Process -FilePath "$root\tools\llama.cpp\llama-server.exe" -ArgumentList $args -WindowStyle Hidden `
        -RedirectStandardOutput "$root\data\llama-server.log" -RedirectStandardError "$root\data\llama-server.err.log"
    return "推理引擎启动中（约 15 秒就绪）..."
}
function Start-Embedding {
    if (Test-Embedding) { return "记忆服务已在运行。" }
    $m = "$root\data\models\embedding\Qwen3-Embedding-0.6B-Q8_0.gguf"
    if (-not (Test-Path $m)) { return "记忆模型缺失: $m" }
    try {
        Start-Process -FilePath "$root\tools\llama.cpp\llama-server.exe" -ArgumentList @(
            "-m", $m, "--host", "127.0.0.1", "--port", "11435", "--embedding",
            "-c", "8192", "-ngl", "0", "--parallel", "1"
        ) -WindowStyle Hidden -RedirectStandardOutput "$root\data\embedding.log" -RedirectStandardError "$root\data\embedding.err.log"
        return "记忆服务启动中..."
    } catch { return "记忆服务启动失败: $_" }
}
function Start-Napcat {
    if (Test-Napcat) { return "NapCat 已在运行。" }
    try {
        $nd = "$root\tools\napcat\napcat"
        $qqExe = $null
        try {
            $r = reg.exe query "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\QQ" /v UninstallString 2>$null
            foreach ($line in $r) {
                if ($line -match 'REG_\w+\s+(?<p>.+?\.exe)') { $qqExe = $Matches.p; break }
            }
        } catch {}
        if ($qqExe) { $qqExe = Join-Path (Split-Path $qqExe -Parent) "QQ.exe" }
        if (-not $qqExe -or -not (Test-Path $qqExe)) { return "NapCat 启动失败：未找到 QQ.exe" }
        $env:NAPCAT_PATCH_PACKAGE = "$nd\qqnt.json"
        $env:NAPCAT_LOAD_PATH = "$nd\loadNapCat.js"
        $env:NAPCAT_INJECT_PATH = "$nd\NapCatWinBootHook.dll"
        $env:NAPCAT_LAUNCHER_PATH = "$nd\NapCatWinBootMain.exe"
        $env:NAPCAT_MAIN_PATH = ($nd + "\napcat.mjs") -replace "\\", "/"
        try { Set-Content "$nd\loadNapCat.js" ("(async () => {await import(""file:///" + $env:NAPCAT_MAIN_PATH + """)})()") -Encoding ASCII } catch {}
        $napCmd = "chcp 65001 >nul & `"$nd\NapCatWinBootMain.exe`" `"$qqExe`" `"$nd\NapCatWinBootHook.dll`""
        Start-Process -FilePath "$env:ComSpec" -ArgumentList "/c", $napCmd -WorkingDirectory $nd -WindowStyle Hidden
        return "NapCat 启动中（扫码登录请留意窗口）..."
    } catch { return "NapCat 启动失败: $_" }
}
function Start-Bot {
    if (Test-Bot) { return "机器人已在运行。" }
    try {
        $p = Start-Process -FilePath "$root\qq-bot\.venv\Scripts\python.exe" -ArgumentList "-u", "bot.py" `
            -WorkingDirectory "$root\qq-bot" -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput "$root\data\bot.out.log" -RedirectStandardError "$root\data\bot.err.log"
        if ($p) { return "机器人启动中（PID $($p.Id)）..." }
    } catch { return "机器人启动失败: $_" }
    return "机器人启动失败（未知）"
}
function Show-Banner($txt) {
    try {
        $script:bannerShownAt = Get-Date
        $b = $window.FindName("AlertBanner"); if ($b) { $b.Visibility = "Visible"; $b.UpdateLayout() }
        $tb = $window.FindName("AlertText")
        if ($tb) { $tb.Text = $txt }
        # 2026-09-06 主页空域修复：主页=WebView2（HWND 控件永远盖过 WPF 元素）——同步驱动网页侧遮挡条
        try {
            if ($script:wv -and $script:wv.CoreWebView2) {
                # 2026-09-10 P3-3：真转义（先反斜杠后单引号；PS 单引号串里反斜杠即字面量，PS5.1 兼容）——原 .Replace("'","'") 为历史无操作
                $js = "showWebBlock('" + (("$txt").Replace('\', '\\').Replace("'", "\'")) + "')"
                $script:wv.CoreWebView2.ExecuteScriptAsync($js) | Out-Null
            }
        } catch {}
        # 90 秒超时自愈（防启动流程卡死导致遮罩永久锁屏）
        try {
            if (-not $script:bannerT) {
                $script:bannerT = New-Object System.Windows.Threading.DispatcherTimer
                $script:bannerT.Interval = [TimeSpan]::FromSeconds(90)
                $script:bannerT.Add_Tick({ try { $script:bannerT.Stop(); Hide-Banner } catch {} })
            }
            $script:bannerT.Stop()
            $script:bannerT.Start()
        } catch {}
    } catch {}
}
function Hide-Banner {
    try {
        $b = $window.FindName("AlertBanner")
        if ($b -and $b.Visibility.ToString() -ne "Collapsed") {
            if ($script:bannerShownAt) {
                $el = ((Get-Date) - $script:bannerShownAt).TotalSeconds
                if ($el -lt 2) {
                    $wait = [Math]::Max(0.1, 2 - $el)
                    $t2 = New-Object System.Windows.Threading.DispatcherTimer
                    $t2.Interval = [TimeSpan]::FromSeconds($wait)
                    $t2.Add_Tick({ $t2.Stop(); try { $b2 = $window.FindName("AlertBanner"); if ($b2) { $b2.Visibility = "Collapsed" } } catch {}; try { if ($script:wv -and $script:wv.CoreWebView2) { $script:wv.CoreWebView2.ExecuteScriptAsync("hideWebBlock()") | Out-Null } } catch {} })
                    $t2.Start()
                    return
                }
            }
            $b.Visibility = "Collapsed"
        }
        try {
            if ($script:wv -and $script:wv.CoreWebView2) {
                $script:wv.CoreWebView2.ExecuteScriptAsync("hideWebBlock()") | Out-Null
            }
        } catch {}
    } catch {}
}
# ---- 步进状态机（2026-09-06 修复：原同步实现冻结 UI 线程——延迟/横幅一闪/关闭崩溃三病同源）----
# 每步只做一个小操作，步间由 DispatcherTimer 呼吸，UI 全程可渲染；不用线程（ps2exe 下线程回调会崩）。
$script:busy = $false
$script:steps = @()
$script:stepIdx = 0
$script:waitUntil = [datetime]::MinValue
$script:opLog = New-Object System.Collections.ArrayList
$script:exitAfterStop = $false
$script:opText = ""
$script:stepTimer = New-Object System.Windows.Threading.DispatcherTimer
$script:stepTimer.Interval = [TimeSpan]::FromMilliseconds(200)
$script:stepTimer.Add_Tick({
    try {
        if (-not $script:busy) { try { $script:stepTimer.Stop() } catch {}; return }
        $now = Get-Date
        if ($now -lt $script:waitUntil) { return }
        if ($script:stepIdx -ge $script:steps.Count) {
            try { $script:stepTimer.Stop() } catch {}
            $script:busy = $false
            $script:opText = ""
            try { Hide-Banner } catch {}
            try { Push-Web } catch {}
            try { Show-Msg (($script:opLog | Where-Object { $_ }) -join " · ") } catch {}
            # 2026-09-12：顶栏启动/停止与网页内按钮同批启用（原先只找 BtnStart/BtnStop，
            # 而那两个名字在 WPF XAML 里已不存在——网页里找得到、界面上摸不到）
            foreach ($bn in @("BtnStart", "BtnTopStart")) { try { $b = $window.FindName($bn); if ($b) { $b.IsEnabled = $true } } catch {} }
            foreach ($bn in @("BtnStop", "BtnTopStop")) { try { $b = $window.FindName($bn); if ($b) { $b.IsEnabled = $true } } catch {} }
            if ($script:exitAfterStop) {
                $script:exitAfterStop = $false
$script:opText = ""
                try { $clockT.Stop() } catch {}
                try { $timer.Stop() } catch {}
                try { $window.Close() } catch {}
            }
            return
        }
        $st = $script:steps[$script:stepIdx]
        $script:stepIdx++
        if ($st.k -eq 'w') { $script:waitUntil = $now.AddSeconds([double]$st.s); return }
        & $st.s
    } catch {
        try { $script:stepTimer.Stop() } catch {}
        $script:busy = $false
        $script:opText = ""
        try { Hide-Banner } catch {}
        try { Push-Web } catch {}
        try { Show-Msg ("操作中断: " + $_.Exception.Message) } catch {}
    }
})



function Stop-ProcessByFilter($filter) {
    $targets = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -match $filter }
    foreach ($t in $targets) { taskkill.exe /PID $t.ProcessId /T /F 2>&1 | Out-Null }
    return @($targets).Count
}
function Stop-ByName($name) {
    $ps = Get-Process -Name $name -ErrorAction SilentlyContinue
    foreach ($x in $ps) { taskkill.exe /PID $x.Id /T /F 2>&1 | Out-Null }
    return @($ps).Count
}
function Stop-ByPort($port, $nameGuard) {
    $l = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($l) {
        # C13：可选 $nameGuard 进程名守卫——带守卫时仅当监听者进程名匹配才杀
        # （8080→python：不误杀占用端口的其他应用；11434/11435→llama-server：引擎端口同理）
        if ($nameGuard) {
            $p = Get-Process -Id $l.OwningProcess -ErrorAction SilentlyContinue
            if (-not ($p -and $p.ProcessName -match $nameGuard)) { return 0 }
        }
        taskkill.exe /PID $l.OwningProcess /T /F 2>&1 | Out-Null; return 1
    }
    return 0
}

function Invoke-StopBotOnly {
    # 2026-09-08 深夜：提权 bot 进程 CommandLine 经 WMI 查询为 NULL → 按命令行匹配永远"未运行"（假阴性）。
    # 补端口兜底：8080 监听者即 bot 本体（python 进程名守卫，不误杀其他服务）。
    $bots = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*bot.py*' })
    foreach ($b in $bots) { taskkill.exe /PID $b.ProcessId /T /F 2>&1 | Out-Null }
    if (-not $bots) {
        $l = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($l -and $l.OwningProcess) {
            $p = Get-Process -Id $l.OwningProcess -ErrorAction SilentlyContinue
            if ($p -and $p.ProcessName -match 'python') {
                taskkill.exe /PID $p.Id /T /F 2>&1 | Out-Null
                $bots = @($p)
            }
        }
    }
    Start-Sleep -Milliseconds 800
    if (Test-TcpFast "127.0.0.1" 8080) {
        "机器人：权限不足无法终止（进程仍在）——请以【管理员身份】运行启动器后再停止"
    } elseif ($bots) {
        "机器人：已停止（进程树 ×$(@($bots).Count)）（LLM 引擎保持运行）"
    } else {
        "机器人：未运行"
    }
}

# ---------------- 干员数据表（人设中心：方舟干员卡风；图=launcher\cards\<key> 优先，否则 QQ 头像） ----------------
# 2026-09-10 Phase2 外置：内置表改名为 $CARDS_FALLBACK，运行时经 Get-Cards 优先读 data\cards.json（新卡免重编译）
$CARDS_FALLBACK = @(
    [pscustomobject]@{ key = "skadi";       name = "斯卡蒂";        cls = "近卫";   star = 6; uni = "ARKNIGHTS"; desc = "深海猎人 · 孤独而可靠" },
    [pscustomobject]@{ key = "amiya";       name = "阿米娅";        cls = "术师";   star = 6; uni = "ARKNIGHTS"; desc = "罗德岛领袖 · 温柔坚定" },
    [pscustomobject]@{ key = "kaltsit";     name = "凯尔希";        cls = "医疗";   star = 6; uni = "ARKNIGHTS"; desc = "医疗部监工 · 毒舌藏关心" },
    [pscustomobject]@{ key = "priestess";   name = "普瑞赛斯";      cls = "术师";   star = 6; uni = "ARKNIGHTS"; desc = "先驱者观测者 · 温和的古老目光" },
    [pscustomobject]@{ key = "exusiai";     name = "能天使";        cls = "狙击";   star = 6; uni = "ARKNIGHTS"; desc = "企鹅物流 · 闹腾乐天" },
    [pscustomobject]@{ key = "lappland";    name = "拉普兰德";      cls = "近卫";   star = 5; uni = "ARKNIGHTS"; desc = "叙拉古狼 · 疯批演技" },
    [pscustomobject]@{ key = "_aoding_";    name = "小奥丁";   cls = "术师";   star = 6; uni = "RHODES-THRONE"; desc = "天空女神 · 主仆体系" },
    [pscustomobject]@{ key = "soyo";        name = "长崎素世";      cls = "支援";   star = 4; uni = "BANG-DREAM"; desc = "月之森 · 温柔腹黑" },
    [pscustomobject]@{ key = "koishi";      name = "古明地恋";      cls = "术师";   star = 5; uni = "GENSOKYO"; desc = "封眼无意识 · 直觉点破" },
    [pscustomobject]@{ key = "eiki";        name = "四季映姬";      cls = "辅助";   star = 6; uni = "GENSOKYO"; desc = "阎魔判官 · 说教白黑" },
    [pscustomobject]@{ key = "chenqianyu";  name = "陈千语";        cls = "近卫";   star = 6; uni = "ENDFIELD"; desc = "大智若愚 · 看破不说破" },
    [pscustomobject]@{ key = "perlica";     name = "佩丽卡";        cls = "狙击";   star = 6; uni = "ENDFIELD"; desc = "黑丝看板娘 · 重女暗涌" },
    [pscustomobject]@{ key = "zhuangfangyi";name = "庄方宜";        cls = "医护";   star = 5; uni = "ENDFIELD"; desc = "前妻感 · 会吃醋" },
    [pscustomobject]@{ key = "char";        name = "柯瓦特罗";      cls = "先锋";   star = 6; uni = "A.E.U.G"; desc = "Z时代导师 · 夏亚机锋" },
    [pscustomobject]@{ key = "deepseek";    name = "蓝色大肥鱼";    cls = "辅助";   star = 5; uni = "DEEP-SEA"; desc = "理性直白 × 摸鱼反差" },
    [pscustomobject]@{ key = "feibi";       name = "菲比";          cls = "支援";   star = 4; uni = "WUTHERING"; desc = "大饼脸 Q 版 · 认真模式" },
    [pscustomobject]@{ key = "yukari";      name = "八云紫";       cls = "术师";   star = 6; uni = "东方"; desc = "境界妖怪 · 隙间之理" }
)
function Get-Cards {
    # 卡片数据源：data\cards.json（no-BOM UTF8，JSON 数组 [{key,name,cls,star,uni,desc}]）存在且解析成功→用之；
    # 否则用内置表 $CARDS_FALLBACK，并首次导出一份初始 cards.json（新卡改 JSON 即可，免重编译 exe）。
    $f = "$root\data\cards.json"
    try {
        if (Test-Path $f) {
            $j = Get-Content $f -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($j -and @($j).Count -gt 0) { return ,@($j) }
        }
    } catch {}
    try {
        if (-not (Test-Path $f)) {
            [System.IO.File]::WriteAllText($f, ($CARDS_FALLBACK | ConvertTo-Json -Depth 4), [System.Text.UTF8Encoding]::new($false))
        }
    } catch {}
    return ,$CARDS_FALLBACK
}
function Get-CardImage($key) {
    # 半身立绘：cards\<key>.{png,jpg,jpeg,webp} → 头像 persona_<key>_0.jpg；全缺=空串
    # （2026-09-10 P2 修：原全缺时调 Get-CardImageFull 与之互递归且无基座——无图卡（如 cards.json
    #  里的 sakura）调用深度溢出→Push-Web catch→state.json cards=[] 全量丢失；改空串兜底）
    foreach ($ext in @(".png", ".jpg", ".jpeg", ".webp")) {
        $p = "$root\launcher\cards\$key$ext"
        if (Test-Path $p) { return $p }
    }
    $p2 = "$root\data\avatars\persona_${key}_0.jpg"
    if (Test-Path $p2) { return $p2 }
    return ""
}
function Get-CardImageFull($key) {
    # 详情竖版立绘：cards\<key>_full 优先 → Get-CardImage 兜底（其全缺返回空串=互递归基座，
    # 2026-09-10 P2 修：无立绘卡不再深度溢出，由 Get-CardsPayload 判空 continue 过滤）
    foreach ($ext in @(".png", ".jpg", ".jpeg", ".webp")) {
        $p = "$root\launcher\cards\${key}_full$ext"
        if (Test-Path $p) { return $p }
    }
    return (Get-CardImage $key)
}
function Get-CardsPayload {
    # 2026-09-10 Phase2：数据源改 Get-Cards（cards.json 外置）；星级图 URL 皮肤感知
    # （ui.local→launcher\ui 已随皮肤包迁移退役，改走 app.local/skins/<skin>/ui/ark/；
    #   2026-09-10 P3-2：generic 皮肤无此图→rar 为空串（不产生 skins/web/... 无效地址；前端判空跳过），arknights 正常出 URL）
    # 2026-09-10 P2：无立绘卡（Get-CardImageFull 空串，如 sakura）continue 过滤——无立绘卡不进 cards 载荷
    $skinLeaf = Split-Path (Get-SkinDir) -Leaf
    $arr = @()
    foreach ($c in (Get-Cards)) {
        $hi = Get-CardImageFull $c.key
        if (-not $hi) { continue }
        $rar = ""
        if ($skinLeaf -ne "generic") { $rar = "https://app.local/skins/$skinLeaf/ui/ark/rarity_$($c.star).png" }
        $arr += @{
            key = $c.key; name = $c.name; cls = $c.cls; uni = $c.uni; desc = $c.desc
            starText = (New-StarText $c.star)
            img = "https://cards.local/bust4w/" + $c.key + ".webp"
            big = "https://cards.local/fullw/" + $c.key + ".webp"
            rar = $rar
        }
    }
    return ,$arr
}
function Get-CardImageHome($key) {
    # 主页半身立绘：cards\<key>_home 优先 → _full → 头像（2026-09-10 P2 同步核对：
    # 兜底链终点=Get-CardImage 空串基座，全缺返回空串不再溢出；调用方 Push-Web `if ($pf)` 已判空）
    foreach ($ext in @(".png", ".jpg", ".jpeg", ".webp")) {
        $hp = "$root\launcher\cards\${key}_home$ext"
        if (Test-Path $hp) { return $hp }
    }
    return (Get-CardImageFull $key)
}
function Get-OwnerId {
    try { $s = Get-Content "$root\data\persona_select.json" -Raw -Encoding UTF8 | ConvertFrom-Json
        $props = @($s.PSObject.Properties)
        if ($props.Count) { return [string]$props[0].Name }
    } catch {}
    return ""
}
function Get-FirstSuperuser {
    # 2026-09-10 P2 参数化（原 Set-OwnerPersona 硬编码回落 uid "10001" 属本机身份）：
    # qq-bot\.env 的 SUPERUSERS（JSON 数组）取首个 → 解析失败回落 persona_select.json 首键
    # → 仍无则空串（调用方不写入并返回 $false）。本机 .env 已有 SUPERUSERS=["10001"]，行为零变化。
    try {
        $raw = Get-EnvValue "SUPERUSERS"
        if ($raw) {
            $j = $raw | ConvertFrom-Json
            $first = @($j)[0]
            if ($first) { return [string]$first }
        }
    } catch {}
    return (Get-OwnerId)
}
function Set-OwnerPersona($key) {
    try {
        $f = "$root\data\persona_select.json"
        $d = @{}
        if (Test-Path $f) { $j = Get-Content $f -Raw -Encoding UTF8 | ConvertFrom-Json
            foreach ($p in $j.PSObject.Properties) { $d[$p.Name] = $p.Value } }
        $uid = Get-FirstSuperuser
        if (-not $uid) { return $false }   # 2026-09-10 P2：无主身份可落 → 不写入（原硬编码 uid 已移除）
        $d[$uid] = $key
        [System.IO.File]::WriteAllText($f, ($d | ConvertTo-Json), [System.Text.UTF8Encoding]::new($false))
        return $true
    } catch { return $false }
}
function Get-OwnerPersona {
    try { $j = Get-Content "$root\data\persona_select.json" -Raw -Encoding UTF8 | ConvertFrom-Json
        $props = @($j.PSObject.Properties)
        if ($props.Count) { return [string]$props[0].Value }
    } catch {}
    return ""
}

# ---------------- XAML（明日方舟主页 · 真实主页式） ----------------
[xml]$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="QQ AI 陪伴机器人 · 启动器" Height="720" Width="1366"
        WindowStartupLocation="CenterScreen" WindowStyle="None" ResizeMode="NoResize">
  <!-- 2026-09-10 Phase2：窗口背景不再硬编码 home_bg.png——由代码后置按活动皮肤赋值（generic=manifest.palette.bg 纯色刷；arknights=skins\arknights\ui\home_bg.png 图刷），见 XamlReader::Load 之后的赋值块 -->
  <Window.Resources>
    <Style x:Key="CutBtn" TargetType="Button">
      <Setter Property="FontWeight" Value="SemiBold"/>
      <Setter Property="Cursor" Value="Hand"/>
    </Style>
    <Style x:Key="IconBtn" TargetType="Button">
      <Setter Property="Foreground" Value="#A9B0BA"/>
      <Setter Property="FontSize" Value="11"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Background" Value="Transparent"/>
      <Setter Property="BorderThickness" Value="0"/>
    </Style>
    <Style x:Key="CardPath" TargetType="Path">
      <Setter Property="Fill" Value="#161A21"/>
      <Setter Property="Stroke" Value="#2A2F37"/>
      <Setter Property="StrokeThickness" Value="1"/>
    </Style>
    <Style x:Key="Dot" TargetType="Rectangle">
      <Setter Property="Width" Value="8"/><Setter Property="Height" Value="8"/><Setter Property="Fill" Value="#4A515B"/>
    </Style>
    <Style x:Key="FuncBlock" TargetType="Button">
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
      <Setter Property="Foreground" Value="#E6E1D4"/>
    </Style>
    <!-- 顶栏导航项（2026-09-12）：透明底 + 当前页底部 2px 强调条（高亮由 Set-NavActive 切换） -->
    <Style x:Key="NavBtn" TargetType="Button">
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Background" Value="Transparent"/>
      <Setter Property="BorderThickness" Value="0,0,0,2"/>
      <Setter Property="BorderBrush" Value="Transparent"/>
      <Setter Property="Foreground" Value="#8A93A0"/>
      <Setter Property="FontSize" Value="12"/>
      <Setter Property="Height" Value="42"/>
      <Setter Property="Padding" Value="11,0"/>
    </Style>
    <Style x:Key="AboutK" TargetType="TextBlock">
      <Setter Property="FontSize" Value="9"/><Setter Property="Foreground" Value="#6A7076"/>
    </Style>
    <Style x:Key="AboutV" TargetType="TextBlock">
      <Setter Property="FontSize" Value="12"/><Setter Property="Foreground" Value="#D8DCE2"/>
      <Setter Property="TextWrapping" Value="Wrap"/><Setter Property="Margin" Value="0,2,0,10"/>
    </Style>
  </Window.Resources>
  <Grid>
    <Grid.RowDefinitions>
      <RowDefinition Height="44"/><RowDefinition Height="*"/><RowDefinition Height="26"/>
    </Grid.RowDefinitions>

    <Rectangle Grid.RowSpan="3" VerticalAlignment="Bottom" Height="200" Opacity="0.55">
      <Rectangle.Fill><LinearGradientBrush StartPoint="0,0" EndPoint="0,1"><GradientStop Color="Transparent" Offset="0"/><GradientStop Color="#FF0B0D10" Offset="1"/></LinearGradientBrush></Rectangle.Fill>
    </Rectangle>
    <!-- 顶条（2026-09-12 UI 改版：导航组左置 + 四服务灯常驻右置）
         设计权威 docs\启动器UI规划.md §3。改这里之前先读它。
         为什么导航要放在顶栏：原先二级页只能由**皮肤页面**用 postMessage 的 cmd 打开，
         换个不带那些块的皮肤就再也进不去设置/日志/状态（规划 §1 P1）。 -->
    <Border x:Name="TopBarBorder" Grid.Row="0" Background="#A010141A" BorderBrush="#28FFFFFF" BorderThickness="0,0,0,1" Padding="10,0">
      <Grid>
        <StackPanel Orientation="Horizontal" VerticalAlignment="Center">
          <Button x:Name="BtnHome" Style="{StaticResource NavBtn}" Content="◈ 主页"/>
          <Button x:Name="NavOpe"   Style="{StaticResource NavBtn}" Content="干员"/>
          <Button x:Name="NavCfg"   Style="{StaticResource NavBtn}" Content="设置"/>
          <Button x:Name="NavPack"  Style="{StaticResource NavBtn}" Content="内容包"/>
          <Button x:Name="NavState" Style="{StaticResource NavBtn}" Content="运行状态"/>
          <Button x:Name="NavLog"   Style="{StaticResource NavBtn}" Content="日志"/>
          <Button x:Name="NavDeps"  Style="{StaticResource NavBtn}" Content="组件"/>
          <Button x:Name="NavAbout" Style="{StaticResource NavBtn}" Content="关于"/>
        </StackPanel>
        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" VerticalAlignment="Center">
          <!-- 启动/停止（2026-09-12 UI 改版）：原先 WPF 层**没有**这两个按钮——
               它们只存在于皮肤网页里（网页 postMessage cmd=start/stop）。
               等于"换一个不带这两个按钮的皮肤，界面上就无法启动机器人"。
               功能不该由外观决定，故移到顶栏常驻。 -->
          <Button x:Name="BtnTopStart" Content="▶ 启动" Style="{StaticResource CutBtn}" Foreground="#0B0D10" FontSize="11" Height="26" Padding="10,0" Margin="0,0,6,0"/>
          <Button x:Name="BtnTopStop"  Content="■ 停止" Style="{StaticResource CutBtn}" Foreground="#E6E1D4" Background="#2A2F37" FontSize="11" Height="26" Padding="10,0" Margin="0,0,14,0"/>
          <Rectangle x:Name="tbDotEngine" Width="8" Height="8" Fill="#4A515B" VerticalAlignment="Center" ToolTip="推理引擎 11434"/>
          <TextBlock x:Name="tbTxtEngine" FontSize="9" Foreground="#8A93A0" Margin="4,0,10,0" VerticalAlignment="Center"/>
          <Rectangle x:Name="tbDotEmbed" Width="8" Height="8" Fill="#4A515B" VerticalAlignment="Center" ToolTip="记忆服务 11435"/>
          <TextBlock x:Name="tbTxtEmbed" FontSize="9" Foreground="#8A93A0" Margin="4,0,10,0" VerticalAlignment="Center"/>
          <Rectangle x:Name="tbDotNap" Width="8" Height="8" Fill="#4A515B" VerticalAlignment="Center" ToolTip="QQ 协议侧（NapCat）"/>
          <TextBlock x:Name="tbTxtNap" FontSize="9" Foreground="#8A93A0" Margin="4,0,10,0" VerticalAlignment="Center"/>
          <Rectangle x:Name="tbDotBot" Width="8" Height="8" Fill="#4A515B" VerticalAlignment="Center" ToolTip="机器人本体 8080"/>
          <TextBlock x:Name="tbTxtBot" FontSize="9" Foreground="#8A93A0" Margin="4,0,14,0" VerticalAlignment="Center"/>
          <TextBlock x:Name="topClock" FontFamily="Consolas" FontSize="10" Foreground="#8A93A0" Margin="0,0,10,0" VerticalAlignment="Center"/>
          <Button x:Name="BtnExit" Content="✕" Style="{StaticResource IconBtn}" Width="28"/>
        </StackPanel>
      </Grid>
    </Border>
    <!-- 主体 -->
    <Grid Grid.Row="1">
      <Grid x:Name="PageHome" xmlns:wv2="clr-namespace:Microsoft.Web.WebView2.Wpf;assembly=Microsoft.Web.WebView2.Wpf">
        <wv2:WebView2 x:Name="WebHost" Margin="0" HorizontalAlignment="Stretch" VerticalAlignment="Stretch"/>
        <TextBlock x:Name="subTitle" FontSize="10" Foreground="#C8E6E1D4" HorizontalAlignment="Left" VerticalAlignment="Bottom" Margin="8,0,0,4" TextWrapping="Wrap" MaxWidth="300"/>
      </Grid>
      <!-- 干员选择 -->
      <Grid x:Name="PageOperators" Visibility="Collapsed" Background="#E610151B">
        <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/></Grid.RowDefinitions>
        <StackPanel Orientation="Horizontal" Margin="18,10,0,0" VerticalAlignment="Center">
          <Button x:Name="BtnBack1" Content="← 主页" Style="{StaticResource IconBtn}" Width="60"/>
          <Rectangle Width="4" Height="18" Fill="#F5A623" VerticalAlignment="Center" Margin="10,0,0,0"/>
          <TextBlock Text="干员一览" FontSize="16" FontWeight="Bold" Foreground="#E6E1D4" Margin="10,0,0,0"/>
        </StackPanel>
        <Grid Grid.Row="1" Margin="18,10,18,14">
          <Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="300"/></Grid.ColumnDefinitions>
          <ScrollViewer VerticalScrollBarVisibility="Auto" Padding="0,0,6,0"><WrapPanel x:Name="opeGrid"/></ScrollViewer>
          <Grid Grid.Column="1" Margin="14,0,0,0">
            <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
            <Border Background="#EDF1F5" BorderBrush="#3B82C4" BorderThickness="1" Padding="10"><StackPanel><TextBlock x:Name="detailName" FontSize="18" FontWeight="Bold" Foreground="#17191D"/><TextBlock x:Name="detailMeta" FontSize="10" Foreground="#F5A623" Margin="0,2,0,0"/><TextBlock x:Name="detailUni" FontFamily="Consolas" FontSize="9" Foreground="#6A7076" Margin="0,2,0,0"/></StackPanel></Border>
            <Image x:Name="detailImg" Grid.Row="1" Stretch="Uniform" Height="400" Margin="4" RenderOptions.BitmapScalingMode="HighQuality"/>
            <StackPanel Grid.Row="2">
              <TextBlock x:Name="detailDesc" FontSize="11" Foreground="#A9B0BA" TextWrapping="Wrap" Margin="2,6,2,8" MaxHeight="72"/>
              <Button x:Name="BtnSetPersona" Content="设为启动人设" FontSize="12" Height="34" Margin="0,4,0,0" Style="{StaticResource CutBtn}" Foreground="#0B0D10"/>
              <TextBlock x:Name="personaMsg" FontSize="10" Foreground="#7CB87C" Margin="2,6,0,0" TextWrapping="Wrap"/>
            </StackPanel>
          </Grid>
        </Grid>
      </Grid>
      <!-- 设置 -->
      <Grid x:Name="PageCfg" Visibility="Collapsed" Background="#E610151B">
        <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/></Grid.RowDefinitions>
        <StackPanel Orientation="Horizontal" Margin="18,10,0,0" VerticalAlignment="Center">
          <Button x:Name="BtnBack2" Content="← 主页" Style="{StaticResource IconBtn}" Width="60"/>
          <Rectangle Width="4" Height="18" Fill="#F5A623" VerticalAlignment="Center" Margin="10,0,0,0"/>
          <TextBlock Text="启动设置" FontSize="16" FontWeight="Bold" Foreground="#E6E1D4" Margin="10,0,0,0"/>
        </StackPanel>
        <!-- 2026-09-12 设置页布局修复（用户实测"提示语超出页面长度"）：
             根因=内容列估算高约 1231px 而可用仅约 636px（窗口 720 - 顶栏 - 底栏），
             且设置页是五个页面里**唯一没有 ScrollViewer** 的（日志页/状态页都有）。
             修法两件一起：① 内容一栏改为「左控件 + 右提示」两列（提示语从 440px 窄列
             移到右侧宽栏，长句换行行数大减）；② 整体套 VerticalScrollBarVisibility=Auto 兜底。
             控件 x:Name 一个不改（PowerShell 侧 FindName 全部照旧）。 -->
        <ScrollViewer Grid.Row="1" VerticalScrollBarVisibility="Auto" Padding="0,0,10,0">
          <Grid Margin="24,18,0,10">
            <Grid.ColumnDefinitions>
              <ColumnDefinition Width="470"/>
              <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>

            <!-- ============ 左列：控件（紧凑，无长提示） ============ -->
            <StackPanel Grid.Column="0" Width="440" HorizontalAlignment="Left">
              <!-- 2026-09-12 全局语言切换：这一行是入口；语言的实现见文件前部 Apply-UiLang（树上换文） -->
              <TextBlock Text="界面语言" FontSize="13" FontWeight="SemiBold" Foreground="#E6E1D4"/>
              <ComboBox x:Name="CmbLang" Width="160" Height="26" Margin="0,8,0,0" HorizontalAlignment="Left" VerticalContentAlignment="Center"/>
              <TextBlock Text="启动选项" FontSize="13" FontWeight="SemiBold" Foreground="#E6E1D4" Margin="0,24,0,0"/>
              <CheckBox x:Name="chkLlm" Content="启动本地推理引擎（LLM）" Foreground="#E6E1D4" Margin="0,12,0,0" FontSize="12"/>
              <CheckBox x:Name="chkVoice" Content="启动语音（TTS 服务）" Foreground="#E6E1D4" Margin="0,10,0,0" FontSize="12"/>

              <TextBlock Text="直播开关" FontSize="13" FontWeight="SemiBold" Foreground="#E6E1D4" Margin="0,24,0,0"/>
              <CheckBox x:Name="chkLiveDanmaku" Content="B 站直播弹幕接入（LIVE_DANMAKU_ENABLED）" Foreground="#E6E1D4" Margin="0,12,0,0" FontSize="12"/>
              <CheckBox x:Name="chkLiveAudio" Content="TTS 语音本地回放到直播音频（LIVE_AUDIO_PLAYBACK）" Foreground="#E6E1D4" Margin="0,10,0,0" FontSize="12"/>
              <!-- 2026-09-09 新增：聊天软件模式跨重启保留（写 WEBGAL_CHAT_PERSIST） -->
              <CheckBox x:Name="chkChatPersist" Content="聊天软件模式跨重启保留" Foreground="#E6E1D4" Margin="0,10,0,0" FontSize="12"/>

              <TextBlock Text="皮肤（主页外观）" FontSize="13" FontWeight="SemiBold" Foreground="#E6E1D4" Margin="0,24,0,0"/>
              <ComboBox x:Name="CmbSkin" Width="240" Height="26" Margin="0,8,0,0" HorizontalAlignment="Left" VerticalContentAlignment="Center"/>
              <Button x:Name="BtnOpenPlugins" Content="插件管理（已装插件 / 快速安装）" FontSize="12" Height="32" Style="{StaticResource CutBtn}" Foreground="#0B0D10" Margin="0,16,0,0" HorizontalAlignment="Left" Width="260"/>

              <!-- 2026-09-11 热修六：模型与接口（写 qq-bot\.env 的 LLM_* 键） -->
              <TextBlock Text="模型与接口" FontSize="13" FontWeight="SemiBold" Foreground="#E6E1D4" Margin="0,24,0,0"/>
              <!-- 2026-09-12 配置档（本地/在线）：两套值分别存 launcher.json，.env 只承载"激活的那一套"。
                   为什么要有它：只有一份 .env 时，切到在线就把本地那组覆盖掉了——切回来自然"什么都没变"。 -->
              <StackPanel Orientation="Horizontal" Margin="0,8,0,0">
                <TextBlock Text="配置档" Width="90" FontSize="12" Foreground="#A9B0BA" VerticalAlignment="Center"/>
                <ComboBox x:Name="CmbProfile" Width="150" Height="26" VerticalContentAlignment="Center"/>
                <Button x:Name="BtnActivateProfile" Content="激活此档 ↦ 写入 .env" FontSize="11" Width="150" Height="26" Margin="8,0,0,0" Style="{StaticResource CutBtn}" Foreground="#0B0D10"/>
                <Button x:Name="BtnSaveProfile" Content="三格 ↦ 存入此档" FontSize="11" Width="130" Height="26" Margin="8,0,0,0" Style="{StaticResource CutBtn}" Foreground="#A9B0BA"/>
              </StackPanel>
              <TextBlock x:Name="txtProfileNote" FontSize="10" Foreground="#6A7076" TextWrapping="Wrap" MaxWidth="560" Margin="90,4,0,0"/>
              <StackPanel Orientation="Horizontal" Margin="0,10,0,0">
                <TextBlock Text="后端类型" Width="90" FontSize="12" Foreground="#A9B0BA" VerticalAlignment="Center"/>
                <ComboBox x:Name="CmbProvider" Width="180" Height="26" VerticalContentAlignment="Center"/>
                <Button x:Name="BtnApplyPreset" Content="套用预设 ↦ 三格" FontSize="11" Width="120" Height="26" Margin="8,0,0,0" Style="{StaticResource CutBtn}" Foreground="#0B0D10"/>
              </StackPanel>
              <StackPanel Orientation="Horizontal" Margin="0,8,0,0">
                <TextBlock Text="接口地址 URL" Width="90" FontSize="12" Foreground="#A9B0BA" VerticalAlignment="Center"/>
                <TextBox x:Name="TxtApiUrl" Width="330" Height="24" FontSize="11" VerticalContentAlignment="Center"/>
              </StackPanel>
              <StackPanel Orientation="Horizontal" Margin="0,8,0,0">
                <TextBlock Text="API Key" Width="90" FontSize="12" Foreground="#A9B0BA" VerticalAlignment="Center"/>
                <TextBox x:Name="TxtApiKey" Width="330" Height="24" FontSize="11" VerticalContentAlignment="Center"/>
              </StackPanel>
              <StackPanel Orientation="Horizontal" Margin="0,8,0,0">
                <TextBlock Text="模型名" Width="90" FontSize="12" Foreground="#A9B0BA" VerticalAlignment="Center"/>
                <TextBox x:Name="TxtModel" Width="330" Height="24" FontSize="11" VerticalContentAlignment="Center"/>
                <!-- 2026-09-12：型号名会过期（DeepSeek 的 deepseek-chat 早没了）——所以别靠预设里那张表，
                     直接向端点要清单（OpenAI 兼容 GET {url}/models）。拉回来进右边下拉，选中即写入模型名。 -->
                <Button x:Name="BtnFetchModels" Content="拉取型号" FontSize="11" Width="80" Height="24" Margin="8,0,0,0" Style="{StaticResource CutBtn}" Foreground="#0B0D10"/>
                <ComboBox x:Name="CmbModels" Width="220" Height="24" Margin="8,0,0,0" FontSize="11" VerticalContentAlignment="Center"/>
              </StackPanel>
              <TextBlock x:Name="txtModelNote" FontSize="10" Foreground="#6A7076" TextWrapping="Wrap" MaxWidth="520" Margin="90,4,0,0"/>

              <!-- 2026-09-12 T5.1：身份区（写 qq-bot\.env 的 SUPERUSERS / OWNER_NICKNAME） -->
              <TextBlock Text="身份" FontSize="13" FontWeight="SemiBold" Foreground="#E6E1D4" Margin="0,24,0,0"/>
              <StackPanel Orientation="Horizontal" Margin="0,8,0,0">
                <TextBlock Text="主人 QQ" Width="90" FontSize="12" Foreground="#A9B0BA" VerticalAlignment="Center"/>
                <TextBox x:Name="TxtOwnerQq" Width="200" Height="24" FontSize="11" VerticalContentAlignment="Center" HorizontalAlignment="Left"/>
              </StackPanel>
              <StackPanel Orientation="Horizontal" Margin="0,8,0,0">
                <TextBlock Text="主人昵称" Width="90" FontSize="12" Foreground="#A9B0BA" VerticalAlignment="Center"/>
                <TextBox x:Name="TxtOwnerNick" Width="200" Height="24" FontSize="11" VerticalContentAlignment="Center" HorizontalAlignment="Left"/>
              </StackPanel>
              <CheckBox x:Name="ChkOwnerQqConfirm" Foreground="#F5A623" Margin="0,10,0,0" FontSize="10">
                <TextBlock Text="我确认要更换主人 QQ（必须同步迁移记忆库，否则等于换了个全新用户）" TextWrapping="Wrap"/>
              </CheckBox>

              <!-- 2026-09-12 T4.2：生成与思考（写 data\think_mode.json） -->
              <TextBlock Text="生成与思考" FontSize="13" FontWeight="SemiBold" Foreground="#E6E1D4" Margin="0,24,0,0"/>
              <StackPanel Orientation="Horizontal" Margin="0,8,0,0">
                <TextBlock Text="思考模式" Width="90" FontSize="12" Foreground="#A9B0BA" VerticalAlignment="Center"/>
                <ComboBox x:Name="CmbThink" Width="200" Height="26" VerticalContentAlignment="Center"/>
              </StackPanel>

              <Button x:Name="BtnSaveCfg" Content="保存  SAVE" FontSize="12" Width="110" Height="34" Style="{StaticResource CutBtn}" Foreground="#0B0D10" Margin="0,24,0,0" HorizontalAlignment="Left"/>
              <TextBlock x:Name="txtCfgMsg" FontSize="10" Foreground="#7CB87C" Margin="2,10,0,0" TextWrapping="Wrap"/>
            </StackPanel>

            <!-- ============ 右列：提示与说明（宽栏，长句不再挤成 3-4 行） ============ -->
            <StackPanel Grid.Column="1" Margin="24,2,0,0">
              <TextBlock Text="说明" FontSize="13" FontWeight="SemiBold" Foreground="#E6E1D4"/>
              <TextBlock Text="＊ 所有 .env 项保存后都需重启机器人才生效（Python 进程启动时才读配置，不热加载）。" FontSize="10" Foreground="#F5A623" TextWrapping="Wrap" Margin="0,10,0,0"/>
              <TextBlock Text="＊ 直播开关：写入 qq-bot\.env。保存后需重启机器人（先停止再启动）。" FontSize="10" Foreground="#F5A623" TextWrapping="Wrap" Margin="0,8,0,0"/>
              <TextBlock Text="＊ 聊天软件模式跨重启保留：写入 WEBGAL_CHAT_PERSIST。GAL 模式不受此开关影响，始终不跨重启。" FontSize="10" Foreground="#F5A623" TextWrapping="Wrap" Margin="0,8,0,0"/>
              <TextBlock Text="＊ 皮肤：写 data\launcher.json 的 skin 键，保存后立即重新导航主页视图（不必重启）。" FontSize="10" Foreground="#F5A623" TextWrapping="Wrap" Margin="0,8,0,0"/>
              <TextBlock Text="＊ 模型与接口：本地引擎=llama-server(11434)，模型名仅作标识；API=任意 OpenAI 兼容端点（url / key / 模型名三格照填）。切换后需重启机器人。" FontSize="10" Foreground="#F5A623" TextWrapping="Wrap" Margin="0,8,0,0"/>
              <TextBlock Text="＊ 「套用预设」：选中后端类型后点它，会把该家的 base_url 与建议模型名填进三格（Key 留空自填），填完仍可自由改。" FontSize="10" Foreground="#F5A623" TextWrapping="Wrap" Margin="0,8,0,0"/>
              <TextBlock Text="＊ 协议边界：全部预设都走 OpenAI 兼容协议。原生 Anthropic / Gemini 本项目不写转换代码——请在本机跑一个协议代理（LiteLLM / llm-rosetta 等），把代理地址填到「接口地址 URL」。" FontSize="10" Foreground="#F5A623" TextWrapping="Wrap" Margin="0,8,0,0"/>
              <TextBlock Text="＊ SUPERUSERS 在 .env 里必须写成 JSON 数组，例：SUPERUSERS=['10001']（真实文件里用双引号）。保存时自动按此格式写入；手工改 .env 时务必照此格式——写成裸数字会让机器人启动失败（启动器侧却看不出问题）。" FontSize="10" Foreground="#F5A623" TextWrapping="Wrap" Margin="0,8,0,0"/>
              <TextBlock Text="＊ 改 QQ 号 = 换身份：记忆 / 关系数值 / 事实 / 人设选择全部按 user_id 隔离，直接改 .env 会全部断链。正确流程＝① 勾选左侧确认框再保存；② 先跑迁移工具 qq-bot\tools\migrate_uid.py --from 旧QQ --to 新QQ --dry 看各表影响行数；③ 去掉 --dry 落盘执行（工具自动备份 memory.db）；④ 重启机器人。" FontSize="10" Foreground="#F5A623" TextWrapping="Wrap" Margin="0,8,0,0"/>
              <TextBlock Text="＊ 思考模式：自决（默认）=删除该 uid 的键；恒定开启/关闭=把该 uid 锁成 on / off，与聊天里 /思考 开|关 等价。写入 data\think_mode.json，需重启机器人生效。" FontSize="10" Foreground="#F5A623" TextWrapping="Wrap" Margin="0,8,0,0"/>
              <TextBlock Text="＊ 换 QQ 有二次确认：不勾选左侧确认框则 QQ 号不写入，其余项照常保存。" FontSize="10" Foreground="#F5A623" TextWrapping="Wrap" Margin="0,8,0,0"/>
            </StackPanel>
          </Grid>
        </ScrollViewer>
      </Grid>
        <!-- 日志（默认仅报错，可切全文尾部）
             2026-09-12 §7.6：原先只有"仅报错"一种视图。但排查时**经常要先看错误之前的上下文**
             （哪一步开始不对的），所以补一个"看全文尾部"开关；顺便把各日志文件的**体积**摆出来——
             维护手册 §3.2 讲日志增长，界面却看不到大小，用户没机会发现"这个日志一直在长"。 -->
      <Grid x:Name="PageLog" Visibility="Collapsed" Background="#E610151B">
        <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
        <StackPanel Orientation="Horizontal" Margin="18,10,0,0" VerticalAlignment="Center">
          <Button x:Name="BtnBack3" Content="← 主页" Style="{StaticResource IconBtn}" Width="60"/>
          <Rectangle Width="4" Height="18" Fill="#F5A623" VerticalAlignment="Center" Margin="10,0,0,0"/>
          <TextBlock Text="运行日志" FontSize="16" FontWeight="Bold" Foreground="#E6E1D4" Margin="10,0,0,0"/>
          <CheckBox x:Name="ChkLogFull" Content="看全文尾部（不勾=仅报错）" Foreground="#A9B0BA" FontSize="11" VerticalAlignment="Center" Margin="18,0,0,0"/>
        </StackPanel>
        <ScrollViewer x:Name="logScroll" Grid.Row="1" Margin="18,8,18,4" VerticalScrollBarVisibility="Auto">
          <Border Background="#D8262C36" BorderBrush="#3B82C4" BorderThickness="1" Padding="10"><TextBlock x:Name="txtLog" FontFamily="Consolas" FontSize="11" Foreground="#E6E1D4" TextWrapping="Wrap"/></Border>
        </ScrollViewer>
        <StackPanel Grid.Row="2" Margin="18,0,18,10">
          <StackPanel Orientation="Horizontal">
            <Button x:Name="BtnRefreshLog" Content="刷新" FontSize="12" Width="80" Height="30" Style="{StaticResource CutBtn}" Foreground="#A9B0BA"/>
            <TextBlock x:Name="txtLogInfo" FontSize="10" Foreground="#6A7076" Margin="12,8,0,0"/>
          </StackPanel>
          <TextBlock x:Name="txtLogFiles" FontSize="10" Foreground="#6A7076" Margin="0,4,0,0" TextWrapping="Wrap"/>
        </StackPanel>
      </Grid>
      <!-- 状态 -->
      <Grid x:Name="PageState" Visibility="Collapsed" Background="#E610151B">
        <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/></Grid.RowDefinitions>
        <StackPanel Orientation="Horizontal" Margin="18,10,0,0" VerticalAlignment="Center">
          <Button x:Name="BtnBack4" Content="← 主页" Style="{StaticResource IconBtn}" Width="60"/>
          <Rectangle Width="4" Height="18" Fill="#F5A623" VerticalAlignment="Center" Margin="10,0,0,0"/>
          <TextBlock Text="运行状态" FontSize="16" FontWeight="Bold" Foreground="#E6E1D4" Margin="10,0,0,0"/>
        </StackPanel>
        <Grid Grid.Row="1" Margin="18,10,18,14">
          <Grid.ColumnDefinitions><ColumnDefinition Width="310"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>
          <StackPanel>
            <Border Background="#EDF1F5" BorderBrush="#3B82C4" BorderThickness="1" Padding="12,8"><StackPanel><TextBlock Text="当前场景" FontSize="9" Foreground="#6A7076"/><TextBlock x:Name="txtScene" FontSize="16" FontWeight="SemiBold" Foreground="#17191D" Margin="0,6,0,0"/></StackPanel></Border>
            <TextBlock x:Name="txtDoing" FontSize="11" Foreground="#3A4048" Margin="4,10,0,0" TextWrapping="Wrap"/>
            <TextBlock x:Name="txtMoodPlan" FontSize="11" Foreground="#3A4048" Margin="4,4,0,0" TextWrapping="Wrap"/>
            <TextBlock x:Name="txtWake" FontSize="11" Foreground="#F5A623" Margin="4,4,0,0" TextWrapping="Wrap"/>
            <Border Background="#EDF1F5" BorderBrush="#3B82C4" BorderThickness="1" Padding="12,8" Margin="0,14,0,0">
              <StackPanel>
                <TextBlock Text="服务状态" FontSize="9" Foreground="#6A7076"/>
                <StackPanel Orientation="Horizontal" Margin="0,8,0,0"><Rectangle x:Name="dotEngine2" Width="8" Height="8" Fill="#4A515B" VerticalAlignment="Center"/><TextBlock x:Name="txtEngine2" FontSize="11" Foreground="#A9B0BA" Margin="8,0,0,0"/></StackPanel>
                <StackPanel Orientation="Horizontal" Margin="0,6,0,0"><Rectangle x:Name="dotEmbed2" Width="8" Height="8" Fill="#4A515B" VerticalAlignment="Center"/><TextBlock x:Name="txtEmbed2" FontSize="11" Foreground="#A9B0BA" Margin="8,0,0,0"/></StackPanel>
                <StackPanel Orientation="Horizontal" Margin="0,6,0,0"><Rectangle x:Name="dotNap2" Width="8" Height="8" Fill="#4A515B" VerticalAlignment="Center"/><TextBlock x:Name="txtNap2" FontSize="11" Foreground="#A9B0BA" Margin="8,0,0,0"/></StackPanel>
                <StackPanel Orientation="Horizontal" Margin="0,6,0,0"><Rectangle x:Name="dotBot2" Width="8" Height="8" Fill="#4A515B" VerticalAlignment="Center"/><TextBlock x:Name="txtBot2" FontSize="11" Foreground="#A9B0BA" Margin="8,0,0,0"/></StackPanel>
                <!-- 2026-09-12 分层去重：原先「仅停机器人」只存在于皮肤网页（cmd=stopbot），
                     而顶栏只有"启动全部/停止全部"——按"功能不该由外观决定"的同一口径补进框架页。
                     位置选在"服务状态"旁边：它本就是一次服务控制动作。 -->
                <Button x:Name="BtnStopBotOnly" Content="仅停机器人（LLM 保持）" FontSize="11" Height="26" HorizontalAlignment="Left" Margin="0,12,0,0" Style="{StaticResource CutBtn}" Foreground="#A9B0BA"/>
              </StackPanel>
            </Border>
          </StackPanel>
          <Border Grid.Column="1" Margin="14,0,0,0" Background="#161A21" BorderBrush="#2A2F37" BorderThickness="1" Padding="12">
            <DockPanel>
              <TextBlock DockPanel.Dock="Top" Text="心跳时间线（滚动翻阅 · 最近 100 条）" FontSize="9" Foreground="#6A7076" Margin="0,0,0,5"/>
              <ScrollViewer VerticalScrollBarVisibility="Auto">
                <TextBlock x:Name="txtTimeline" FontSize="11" Foreground="#3A4048" TextWrapping="Wrap" LineHeight="21"/>
              </ScrollViewer>
            </DockPanel>
          </Border>
        </Grid>
      </Grid>
        <!-- 插件管理（2026-09-11 热修六：已装插件目录 + 快速安装通道，自动解析 robot-pack-v1 种类） -->
      <Grid x:Name="PagePlugins" Visibility="Collapsed" Background="#E610151B">
        <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="*"/></Grid.RowDefinitions>
        <StackPanel Orientation="Horizontal" Margin="18,10,0,0" VerticalAlignment="Center">
          <Button x:Name="BtnBack5" Content="← 主页" Style="{StaticResource IconBtn}" Width="60"/>
          <Rectangle Width="4" Height="18" Fill="#F5A623" VerticalAlignment="Center" Margin="10,0,0,0"/>
          <TextBlock Text="插件管理" FontSize="16" FontWeight="Bold" Foreground="#E6E1D4" Margin="10,0,0,0"/>
        </StackPanel>
        <StackPanel Grid.Row="1" Orientation="Horizontal" Margin="24,14,0,0">
          <Button x:Name="BtnPackFolder" Content="从文件夹安装" FontSize="12" Width="120" Height="32" Style="{StaticResource CutBtn}" Foreground="#0B0D10"/>
          <Button x:Name="BtnPackZip" Content="从 zip 安装" FontSize="12" Width="110" Height="32" Margin="12,0,0,0" Style="{StaticResource CutBtn}" Foreground="#0B0D10"/>
          <Button x:Name="BtnPackRefresh" Content="刷新列表" FontSize="12" Width="90" Height="32" Margin="12,0,0,0" Style="{StaticResource CutBtn}" Foreground="#A9B0BA"/>
          <TextBlock x:Name="txtPackMsg" Margin="16,0,0,0" FontSize="10" Foreground="#7CB87C" VerticalAlignment="Center" TextWrapping="Wrap"/>
        </StackPanel>
        <!-- 2026-09-12 §7.5：左列表 + 右 400px 详情栏（原先只有一列全宽列表）。
             为什么加详情栏：① 其它二级页（组件/干员）都有详情位，"我装的这个包到底装了什么"无处可看；
             ② 原先把 名称/标题/作者/许可/来源 塞进一行用 {0,-22} 补空格对齐——那是**按字符数**补的，
             中文标题必然错位。行内只留最能扫读的"名字 + 类型 + 标题"，其余移到右栏。 -->
        <Grid Grid.Row="2" Margin="24,12,24,18">
          <Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="400"/></Grid.ColumnDefinitions>
          <ListBox x:Name="LstPacks" Background="#D820242C" BorderBrush="#2A2F37" BorderThickness="1" Foreground="#E6E1D4" FontSize="12" Padding="8"/>
          <Border Grid.Column="1" Margin="14,0,0,0" Background="#161A21" BorderBrush="#2A2F37" BorderThickness="1" Padding="14">
            <ScrollViewer VerticalScrollBarVisibility="Auto">
              <StackPanel>
                <TextBlock x:Name="pkTitle" FontSize="14" FontWeight="Bold" Foreground="#E6E1D4" TextWrapping="Wrap"/>
                <TextBlock x:Name="pkKind" FontSize="10" Foreground="#F5A623" Margin="0,4,0,12"/>
                <TextBlock Text="作者" Style="{StaticResource AboutK}"/>
                <TextBlock x:Name="pkAuthor" Style="{StaticResource AboutV}"/>
                <TextBlock Text="许可" Style="{StaticResource AboutK}"/>
                <TextBlock x:Name="pkLicense" Style="{StaticResource AboutV}"/>
                <TextBlock Text="来源" Style="{StaticResource AboutK}"/>
                <TextBlock x:Name="pkSrc" Style="{StaticResource AboutV}"/>
                <TextBlock Text="目录" Style="{StaticResource AboutK}"/>
                <TextBlock x:Name="pkDir" FontFamily="Consolas" FontSize="10" Foreground="#A9B0BA" TextWrapping="Wrap" Margin="0,2,0,10"/>
                <TextBlock Text="装了什么（读包内文件得出）" FontSize="10" FontWeight="Bold" Foreground="#F5A623" Margin="0,4,0,0"/>
                <Rectangle Height="1" Fill="#2A2F37" Margin="0,4,0,8"/>
                <TextBlock x:Name="pkSummary" FontSize="11" Foreground="#A9B0BA" TextWrapping="Wrap" LineHeight="18"/>
                <TextBlock Text="生效条件" FontSize="10" FontWeight="Bold" Foreground="#F5A623" Margin="0,10,0,0"/>
                <Rectangle Height="1" Fill="#2A2F37" Margin="0,4,0,8"/>
                <TextBlock x:Name="pkNote" FontSize="11" Foreground="#A9B0BA" TextWrapping="Wrap" LineHeight="18"/>
              </StackPanel>
            </ScrollViewer>
          </Border>
        </Grid>
      </Grid>
      <!-- 关于（2026-09-12 新增 · docs\启动器UI规划.md §4）
           为什么单列一页：分发策略是"整合包 → 框架更新 → 把人导向源码仓"，那么
           "这是谁的框架 / 这套皮肤是谁做的 / 源码在哪" 必须界面可见，不能藏在 exe 属性里。
           双重署名：框架作者（本页左栏，来自 exe 内常量）| 皮肤作者（右栏，来自皮肤 manifest）。 -->
      <Grid x:Name="PageAbout" Visibility="Collapsed" Background="#E610151B">
        <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/></Grid.RowDefinitions>
        <StackPanel Orientation="Horizontal" Margin="18,10,0,0" VerticalAlignment="Center">
          <Button x:Name="BtnBack6" Content="← 主页" Style="{StaticResource IconBtn}" Width="60"/>
          <Rectangle Width="4" Height="18" Fill="#F5A623" VerticalAlignment="Center" Margin="10,0,0,0"/>
          <TextBlock Text="关于" FontSize="16" FontWeight="Bold" Foreground="#E6E1D4" Margin="10,0,0,0"/>
        </StackPanel>
        <Grid Grid.Row="1" Margin="18,14,18,14">
          <Grid.ColumnDefinitions><ColumnDefinition Width="440"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>
          <ScrollViewer VerticalScrollBarVisibility="Auto" Padding="0,0,8,0">
            <StackPanel>
              <TextBlock Text="框架" FontSize="10" FontWeight="Bold" Foreground="#F5A623"/>
              <Rectangle Height="1" Fill="#2A2F37" Margin="0,4,0,12"/>
              <TextBlock Text="名称" Style="{StaticResource AboutK}"/>
              <TextBlock Text="QQ AI 陪伴机器人（框架 + 启动器）" Style="{StaticResource AboutV}"/>
              <TextBlock Text="启动器版本" Style="{StaticResource AboutK}"/>
              <TextBlock x:Name="abVer" Style="{StaticResource AboutV}"/>
              <TextBlock Text="核心接口版本 CORE_API_VERSION" Style="{StaticResource AboutK}"/>
              <TextBlock x:Name="abCoreApi" Style="{StaticResource AboutV}"/>
              <TextBlock Text="框架作者" Style="{StaticResource AboutK}"/>
              <TextBlock x:Name="abAuthor" Style="{StaticResource AboutV}"/>
              <TextBlock Text="安装根目录" Style="{StaticResource AboutK}"/>
              <TextBlock x:Name="abRoot" FontFamily="Consolas" FontSize="11" Foreground="#A9B0BA" TextWrapping="Wrap" Margin="0,2,0,10"/>
            </StackPanel>
          </ScrollViewer>
          <ScrollViewer Grid.Column="1" Margin="18,0,0,0" VerticalScrollBarVisibility="Auto">
            <StackPanel>
              <TextBlock Text="当前皮肤" FontSize="10" FontWeight="Bold" Foreground="#F5A623"/>
              <Rectangle Height="1" Fill="#2A2F37" Margin="0,4,0,12"/>
              <TextBlock Text="名称 / 标识" Style="{StaticResource AboutK}"/>
              <TextBlock x:Name="abSkinName" Style="{StaticResource AboutV}"/>
              <TextBlock Text="皮肤作者" Style="{StaticResource AboutK}"/>
              <TextBlock x:Name="abSkinAuthor" Style="{StaticResource AboutV}"/>
              <TextBlock Text="许可" Style="{StaticResource AboutK}"/>
              <TextBlock x:Name="abSkinLicense" Style="{StaticResource AboutV}"/>
              <TextBlock Text="皮肤目录" Style="{StaticResource AboutK}"/>
              <TextBlock x:Name="abSkinDir" FontFamily="Consolas" FontSize="11" Foreground="#A9B0BA" TextWrapping="Wrap" Margin="0,2,0,10"/>
              <TextBlock Text="来源" FontSize="10" FontWeight="Bold" Foreground="#F5A623" Margin="0,6,0,0"/>
              <Rectangle Height="1" Fill="#2A2F37" Margin="0,4,0,12"/>
              <TextBlock Text="源码 / 发布页" Style="{StaticResource AboutK}"/>
              <TextBlock x:Name="abSource" Style="{StaticResource AboutV}"/>
              <TextBlock Text="第三方组件" Style="{StaticResource AboutK}"/>
              <TextBlock Text="见安装根下 docs\THIRD_PARTY.md（模型 / 语音 / 引擎 / 前端库各自许可）" Style="{StaticResource AboutV}"/>
              <TextBlock Text="发行条款" Style="{StaticResource AboutK}"/>
              <TextBlock Text="见安装根下 LICENSE（含第三方 IP 素材的处置约定）" Style="{StaticResource AboutV}"/>
            </StackPanel>
          </ScrollViewer>
        </Grid>
      </Grid>
      <!-- 依赖与组件（S5，2026-09-12 · docs\启动器UI规划.md）
           为什么单列一页：这是"功能全部拆开"的兑现面——用户要能一眼看出
           「哪个功能要装什么、我现在缺什么、缺了会怎样、去哪拿」。
           只报告不代劳：缺件不等于坏件，每项都有降级路径。 -->
      <Grid x:Name="PageDeps" Visibility="Collapsed" Background="#E610151B">
        <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="*"/></Grid.RowDefinitions>
        <StackPanel Orientation="Horizontal" Margin="18,10,0,0" VerticalAlignment="Center">
          <Button x:Name="BtnBack7" Content="← 主页" Style="{StaticResource IconBtn}" Width="60"/>
          <Rectangle Width="4" Height="18" Fill="#F5A623" VerticalAlignment="Center" Margin="10,0,0,0"/>
          <TextBlock Text="依赖与组件" FontSize="16" FontWeight="Bold" Foreground="#E6E1D4" Margin="10,0,0,0"/>
        </StackPanel>
        <StackPanel Grid.Row="1" Orientation="Horizontal" Margin="24,12,0,0">
          <Button x:Name="BtnDepRefresh" Content="重新检测" FontSize="12" Width="100" Height="30" Style="{StaticResource CutBtn}" Foreground="#0B0D10"/>
          <Button x:Name="BtnDepOpenDir" Content="打开组件目录" FontSize="12" Width="120" Height="30" Margin="12,0,0,0" Style="{StaticResource CutBtn}" Foreground="#E6E1D4" Background="#2A2F37"/>
          <Button x:Name="BtnDepOpenSrc" Content="打开下载页" FontSize="12" Width="110" Height="30" Margin="12,0,0,0" Style="{StaticResource CutBtn}" Foreground="#E6E1D4" Background="#2A2F37"/>
          <TextBlock x:Name="txtDepMsg" Margin="16,0,0,0" FontSize="10" Foreground="#7CB87C" VerticalAlignment="Center" TextWrapping="Wrap"/>
        </StackPanel>
        <Grid Grid.Row="2" Margin="24,12,24,18">
          <Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="400"/></Grid.ColumnDefinitions>
          <ListBox x:Name="LstDeps" Background="#D820242C" BorderBrush="#2A2F37" BorderThickness="1" Foreground="#E6E1D4" FontSize="12" Padding="8"/>
          <Border Grid.Column="1" Margin="14,0,0,0" Background="#161A21" BorderBrush="#2A2F37" BorderThickness="1" Padding="12">
            <DockPanel>
              <StackPanel DockPanel.Dock="Top">
                <TextBlock x:Name="depName" FontSize="15" FontWeight="Bold" Foreground="#E6E1D4"/>
                <TextBlock x:Name="depState" FontSize="11" Margin="0,4,0,8"/>
                <TextBlock Text="提供什么" Style="{StaticResource AboutK}"/>
                <TextBlock x:Name="depRole" FontSize="11" Foreground="#A9B0BA" TextWrapping="Wrap" Margin="0,2,0,8"/>
                <TextBlock Text="缺了会怎样（降级路径）" Style="{StaticResource AboutK}"/>
                <TextBlock x:Name="depDegrade" FontSize="11" Foreground="#F5A623" TextWrapping="Wrap" Margin="0,2,0,8"/>
                <TextBlock Text="组件明细" Style="{StaticResource AboutK}"/>
              </StackPanel>
              <ScrollViewer VerticalScrollBarVisibility="Auto">
                <TextBlock x:Name="depComps" FontSize="10" Foreground="#8A93A0" TextWrapping="Wrap" FontFamily="Consolas"/>
              </ScrollViewer>
            </DockPanel>
          </Border>
        </Grid>
      </Grid>
      <Grid x:Name="AlertBanner" Panel.ZIndex="999" Visibility="Collapsed" Background="#DD0D0F12" IsHitTestVisible="True">
    <!-- 2026-09-09 静态警戒带样式：横贯全宽的黄底黑边警告条（⚠ + 文字，居中）；无任何动画（原动态滚动进度条已删除，与 web 侧 #web-warnbar 全宽警戒带观感统一） -->
    <Border VerticalAlignment="Top" Margin="0,72,0,0" Background="#F7C948" BorderBrush="#1A1A1A" BorderThickness="3" Padding="14,10">
      <TextBlock x:Name="AlertText" Foreground="#141414" FontSize="26" FontWeight="Bold" Text="⚠（正在启动全部…）" HorizontalAlignment="Center" TextAlignment="Center" TextWrapping="Wrap"/>
    </Border>
  </Grid> </Grid>
  </Grid>
</Window>


'@
function Start-StepMachine($steps, $banner) {
    if ($script:busy) { try { Show-Msg "已有操作进行中，请稍候…" } catch {}; return $false }
    $script:busy = $true
    $script:steps = $steps; $script:stepIdx = 0
    $script:waitUntil = [datetime]::MinValue
    $script:opLog = New-Object System.Collections.ArrayList
    foreach ($bn in @("BtnStart", "BtnTopStart")) { try { $b = $window.FindName($bn); if ($b) { $b.IsEnabled = $false } } catch {} }
    foreach ($bn in @("BtnStop", "BtnTopStop")) { try { $b = $window.FindName($bn); if ($b) { $b.IsEnabled = $false } } catch {} }
    if ($banner) { Show-Banner $banner }
    $script:opText = $banner
    try { Push-Web } catch {}
    $script:stepTimer.Start()
    return $true
}

function New-StartSteps {
    $cfg = Get-LauncherCfg
    $lst = @()
    if ($cfg.llm) {
        $lst += @{ k = 'x'; s = { try { $m = Start-Engine; if ($m) { $script:opLog.Add($m) } } catch {} } }
        $lst += @{ k = 'w'; s = 1 }
        $lst += @{ k = 'x'; s = { try { $m = Start-Embedding; if ($m) { $script:opLog.Add($m) } } catch {} } }
    }
    if ($cfg.voice) {
        # 2026-09-09 修复：原路径 "$root\data<VT>oice_mode.json" 中 voice 的 v 实为竖制表符 0x0B——写出的文件名错乱（显示为 E:\robot\datavoice_mode.json），bot 读方 data\voice_mode.json 永远读不到 → 改正 "$root\data\voice_mode.json"
        $lst += @{ k = 'x'; s = { try { [System.IO.File]::WriteAllText("$root\data\voice_mode.json", (@{ enabled = $true } | ConvertTo-Json -Compress), [System.Text.UTF8Encoding]::new($false)); $script:opLog.Add("语音功能：启用") } catch { $script:opLog.Add("语音配置写入失败") } } }
    } else {
        # 2026-09-09 补对称停用写入：原为单向开关（voice=false 时不写文件，磁盘残留 enabled=true）
        $lst += @{ k = 'x'; s = { try { [System.IO.File]::WriteAllText("$root\data\voice_mode.json", (@{ enabled = $false } | ConvertTo-Json -Compress), [System.Text.UTF8Encoding]::new($false)); $script:opLog.Add("语音功能：停用") } catch { $script:opLog.Add("语音配置写入失败") } } }
    }
    $lst += @{ k = 'x'; s = { try { $m = Start-Napcat; if ($m) { $script:opLog.Add($m) } } catch {} } }
    $lst += @{ k = 'w'; s = 2 }
    $lst += @{ k = 'w'; s = 2 }
    $lst += @{ k = 'w'; s = 2 }
    $lst += @{ k = 'x'; s = { try { $m = Start-Bot; if ($m) { $script:opLog.Add($m) } } catch {} } }
    $lst += @{ k = 'x'; s = { $script:probeCache = @{}; try { Push-Web } catch {}; $script:opLog.Add("完成。") } }
    return ,$lst
}

function New-StopSteps {
    # 2026-09-06 修复：python/llama 的 CommandLine 经 WMI 查询为 NULL（提权/隐藏窗口启动）——
    # 旧 CommandLine 匹配从未真正杀到进程。改为：端口定位 + 进程名（Get-Process，不依赖 WMI）。
    $lst = @()
    $lst += @{ k = 'x'; s = { try {
        # 2026-09-09 修复：原第一步仅按 8080 端口找 bot——bot 启动中尚未绑端口时会漏杀成孤儿。
        # 复用 Invoke-StopBotOnly（cmdline+端口双兜底，与「仅停机器人」同口径）；返回串自带“机器人：”前缀，与停全部汇报格式兼容。
        $script:opLog.Add((Invoke-StopBotOnly))
    } catch { $script:opLog.Add("机器人：停止失败") } } }
    $lst += @{ k = 'x'; s = { try {
        $ps = Get-Process -Name 'llama-server' -ErrorAction SilentlyContinue
        foreach ($x in $ps) { taskkill.exe /PID $x.Id /T /F 2>&1 | Out-Null }
        if (-not $ps) { $script:opLog.Add("LLM 引擎：未运行"); return }
        Start-Sleep -Milliseconds 600
        if (Get-Process -Name 'llama-server' -ErrorAction SilentlyContinue) {
            $script:opLog.Add("LLM 引擎：权限不足无法终止——请以【管理员身份】运行启动器")
        } else {
            $script:opLog.Add("LLM 引擎：已停 ×$(@($ps).Count)")
        }
    } catch { $script:opLog.Add("LLM 引擎：停止失败") } } }
    $lst += @{ k = 'x'; s = { try {
        $n1 = Stop-ByPort 11434 'llama-server'; $n2 = Stop-ByPort 11435 'llama-server'
        $script:opLog.Add("端口清理：$($n1 + $n2) 个残留进程")
    } catch {} } }
    $lst += @{ k = 'x'; s = { try {
        # NapCat：只杀引导链，绝不杀 QQ（用户日常登录同进程）
        $n3 = Stop-ByName 'NapCatWinBootMain'
        $script:opLog.Add("NapCat：已清（×$n3）——QQ 客户端保留复用登录")
    } catch { $script:opLog.Add("NapCat：清理失败") } } }
    $lst += @{ k = 'x'; s = {
        try { $script:probeCache = @{} } catch {}
        try { Push-Web } catch {}
        Remove-Item "$root\data\.launcher.lock" -Force -ErrorAction SilentlyContinue
        $script:opLog.Add("完成（显存已释放）。")
    } }
    $lst += @{ k = 'x'; s = {
        # 2026-09-11 热修五：停止后实测端口，未死自动补杀一次并如实汇报——
        # 治"极快结束但组件仍在"的观感与真实性分离（快是步进机常态，关键是杀没杀掉）。
        try {
            foreach ($port in @(@{ p = 8080; g = 'python' }, @{ p = 11434; g = 'llama-server' }, @{ p = 11435; g = 'llama-server' })) {
                if (Test-TcpFast "127.0.0.1" $port.p) {
                    $null = Stop-ByPort $port.p $port.g
                    Start-Sleep -Milliseconds 600
                }
            }
            $alive = @()
            if (Test-TcpFast "127.0.0.1" 8080) { $alive += "机器人(8080)" }
            if (Test-TcpFast "127.0.0.1" 11434) { $alive += "LLM引擎(11434)" }
            if (Test-TcpFast "127.0.0.1" 11435) { $alive += "记忆服务(11435)" }
            if ($alive.Count) { $script:opLog.Add("验证：仍有在跑 —— " + ($alive -join "、") + "（权限不足请以管理员身份运行）") }
            else { $script:opLog.Add("验证：全部组件已关闭") }
        } catch {}
    } }
    return ,$lst
}

function Get-GpuMem {
    try {
        $out = (nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits 2>$null | Select-Object -First 1)
        $parts = $out.Split(',')
        return ("显存: {0:N1}/{1:N1} GB" -f ([double]$parts[0] / 1024), ([double]$parts[1] / 1024))
    } catch { return "显存: 未知" }
}

# ---------------- 命令行模式（无 UI：start/stop/status） ----------------
function W($s) { try { Write-Host $s } catch {} }   # GUI exe 无控制台时安全输出
# 单实例保护：清理其它残留实例
try {
    $me = $PID
    Get-Process -Name "QQAI-Launcher" -ErrorAction SilentlyContinue | Where-Object { $_.Id -ne $me } | ForEach-Object { try { Stop-Process -Id $_.Id -Force -ErrorAction Stop } catch {} }
} catch {}
if ($Mode -ne "") {
    function Invoke-StepsSync($steps) {
        foreach ($st in $steps) {
            if ($st.k -eq 'w') { Start-Sleep -Seconds ([int]$st.s); continue }
            try { & $st.s } catch { W ("step error: " + $_.Exception.Message) }
        }
    }
    if ($Mode -eq "start")   { Invoke-StepsSync (New-StartSteps) }
    elseif ($Mode -eq "stop") { Invoke-StepsSync (New-StopSteps) }
    elseif ($Mode -eq "status") {
        W ("推理引擎: " + $(if (Test-Engine) { "运行中" } else { "未运行" }))
        W ("记忆服务: " + $(if (Test-Embedding) { "运行中" } else { "未运行" }))
        W ("NapCat:    " + $(if (Test-Napcat) { "运行中" } else { "未运行" }))
        W ("机器人:    " + $(if (Test-Bot) { "运行中" } else { "未运行" }))
        $s = Get-LifeState
        W ("场景: " + $s.scene)
        W ("正在: " + $s.doing)
        W ("心情: " + $s.mood + "  计划: " + $s.plan + "  " + $s.wake)
        W (Get-GpuMem)
    } elseif ($Mode -eq "deps") {
        # 依赖矩阵无界面输出（2026-09-12 S5）：与界面同源（同一个 Get-Deps / Test-DepFeature），
        # 好处是**探测逻辑可被脚本验证**——否则它只能靠"人打开界面看一眼"来确认。
        $fs = Get-Deps
        if (-not @($fs).Count) {
            W "读不到 launcher\deps.json——依赖矩阵缺失"
        } else {
            $ok = 0; $miss = 0
            foreach ($f in $fs) {
                $alive = Test-DepFeature $f
                if ($alive) { $ok++ } else { $miss++ }
                $tag = if ($f.required) { "必须" } else { "可选" }
                W (("{0} {1,-22} 〔{2}〕 {3}" -f $(if ($alive) { "[●]" } else { "[ ]" }), $f.name, $tag, "$(($f.detect).label)"))
                if (-not $alive) {
                    W ("        └ 缺了会怎样: " + [string]$f.degrade)
                    foreach ($c in @($f.components)) {
                        W ("        └ 需要: " + [string]$c.name + "  →  " + [string]$c.install)
                    }
                }
            }
            W ("就绪 $ok / 缺失 $miss（缺的都可以缺——每项都有降级路径）")
        }
    } elseif ($Mode -eq "packs") {
        # 已装件清单无界面输出（2026-09-12）：与「内容包」页**同源**（同一个 Get-InstalledItems /
    # Get-PackSummary / Get-PackNote），理由同 -Mode deps——分类与"装了什么"都可被脚本验证，
    # 不必靠"打开界面看一眼"。新增分类或改摘要时两处一起改，否则这条模式会悄悄说假话。
        $items = Get-InstalledItems
        if (-not @($items).Count) {
            W "（未发现任何已装件）"
        } else {
            foreach ($k in @("内容包", "舞台包", "皮肤", "插件")) {
                $grp = @($items | Where-Object { $_.kind -eq $k })
                if (-not $grp.Count) { continue }
                W ("[$k] " + $grp.Count + " 件")
                foreach ($p in $grp) {
                    $au = if ($p.author) { $p.author } else { "未声明" }
                    $li = if ($p.license) { $p.license } else { "未声明" }
                    W ("   - {0}  type={1}  作者={2}  许可={3}  来源={4}" -f $p.name, $p.type, $au, $li, $p.src)
                    W ("       目录: " + [string]$p.dir)
                    foreach ($line in (Get-PackSummary $p)) { W ("       " + $line) }
                    W ("       生效: " + (Get-PackNote $p))
                }
            }
        }
    } elseif ($Mode -eq "logs") {
        # 日志概览无界面输出（2026-09-12 §7.6）：与「日志」页**同源**（同一个 Get-LogFiles /
        # Get-Errors / Format-Size），所以"界面看到的"和"命令行看到的"不会分家。
        $fs = Get-LogFiles
        if (-not @($fs).Count) {
            W "未找到日志文件（bot 还没跑过？）"
        } else {
            W "日志文件（按体积降序）："
            foreach ($lf in $fs) {
                W ("  {0,-30} {1,10}   改 {2}" -f [string]$lf.name, (Format-Size $lf.size), $lf.mtime.ToString("MM-dd HH:mm"))
            }
            $errs = Get-Errors
            W ("报错（近 300 行内匹配 ERROR/CRITICAL/Traceback/Exception/失败/错误/failed）：" + @($errs).Count + " 条")
            $i = 0
            foreach ($e in $errs) {
                if ($i -ge 20) { W ("  … 另有 " + (@($errs).Count - 20) + " 条"); break }
                W ("  " + $e)
                $i++
            }
        }
    } elseif ($Mode -match "^view-") {
        # view 模式穿透到主流程（需 window 创建后切换页面）
    } else { W "用法: -Mode start|stop|status|deps|packs|logs|view-opera|view-cfg|view-log|view-state|view-deps（无参=打开界面）"; exit }
    if ($Mode -notlike "view-*") { exit }
}
[System.Reflection.Assembly]::LoadFrom("$script:logDir\Microsoft.Web.WebView2.Core.dll") | Out-Null
[System.Reflection.Assembly]::LoadFrom("$script:logDir\Microsoft.Web.WebView2.Wpf.dll") | Out-Null
$window = [Windows.Markup.XamlReader]::Load([System.Xml.XmlNodeReader]$xaml)

# ---------- 皮肤化窗口背景（2026-09-10 Phase2，XAML 中原硬编码 ImageBrush 已删）----------
# generic=manifest.palette.bg 纯色刷；arknights=skins\arknights\ui\home_bg.png 图刷（皮肤包缺失时按回落规则自然走纯色）
try {
    $sm0 = Get-SkinManifest
    $bgBrush = $null
    if ((Get-SkinDir) -ne "$root\launcher\web") {
        $bgPath = (Get-SkinDir) + "\ui\home_bg.png"
        if (Test-Path $bgPath) {
            $bi = New-Object System.Windows.Media.Imaging.BitmapImage
            $bi.BeginInit()
            $bi.UriSource = [Uri]::new(("file:///" + ($bgPath -replace '\\', '/')))
            $bi.CacheOption = [System.Windows.Media.Imaging.BitmapCacheOption]::OnLoad
            $bi.EndInit()
            $bi.Freeze()
            $bgBrush = New-Object System.Windows.Media.ImageBrush
            $bgBrush.ImageSource = $bi
            $bgBrush.Stretch = [System.Windows.Media.Stretch]::UniformToFill
        }
    }
    if (-not $bgBrush) {
        $cvt = New-Object System.Windows.Media.BrushConverter
        $bgBrush = $cvt.ConvertFromString([string]$sm0.palette.bg)
    }
    $window.Background = $bgBrush
} catch {}

# ---------- 工具 ----------
function New-StarText($n) { $s = ""; for ($i = 0; $i -lt [int]$n; $i++) { $s += "★" }; return $s }
$script:imgCache = @{}
function New-CardImage($path) {
    if (-not $path) { return $null }
    try {
        if ($script:imgCache.ContainsKey($path)) { return $script:imgCache[$path] }
        $bi = New-Object System.Windows.Media.Imaging.BitmapImage
        $bi.BeginInit()
        $bi.UriSource = [Uri]::new(("file:///" + ($path -replace '\\', '/')))
        $bi.CacheOption = [System.Windows.Media.Imaging.BitmapCacheOption]::OnLoad
        $bi.EndInit()
        $bi.Freeze()
        $script:imgCache[$path] = $bi
        return $bi
    } catch { return $null }
}
function Set-Lamp($dotName, $textName, $ok, $label) {
    $dot = $window.FindName($dotName); $txt = $window.FindName($textName)
    if (-not $dot -or -not $txt) { return }
    $c = if ($ok) { [System.Windows.Media.Color]::FromRgb(0x7C, 0xB8, 0x7C) } else { [System.Windows.Media.Color]::FromRgb(0x4A, 0x51, 0x5B) }
    $dot.Fill = [System.Windows.Media.SolidColorBrush]::new($c)
    $txt.Text = "$label · " + $(if ($ok) { "运行中" } else { "已关闭" })
}
# 顶栏迷你灯（2026-09-12）：只有 8px 点 + 9px 短标签，为常驻四灯留位。
# 诚实边界：探测只有"在跑/没在跑"两态，**不编造红色异常态**——没有信号就不要显示。
function Set-LampMini($dotName, $textName, $ok, $label) {
    try {
        $dot = $window.FindName($dotName); $txt = $window.FindName($textName)
        if (-not $dot -or -not $txt) { return }
        $c = if ($ok) { [System.Windows.Media.Color]::FromRgb(0x7C, 0xB8, 0x7C) } else { [System.Windows.Media.Color]::FromRgb(0x4A, 0x51, 0x5B) }
        $dot.Fill = [System.Windows.Media.SolidColorBrush]::new($c)
        $txt.Text = $label
        $txt.Foreground = [System.Windows.Media.SolidColorBrush]::new(
            $(if ($ok) { [System.Windows.Media.Color]::FromRgb(0xB8, 0xD8, 0xB8) } else { [System.Windows.Media.Color]::FromRgb(0x6A, 0x70, 0x76) }))
    } catch {}
}
# 顶栏当前页高亮（底部 2px 强调条）。映射表是唯一的"页名→导航项"出处。
function Set-NavActive($page) {
    try {
        $map = @{ home = "BtnHome"; opera = "BtnHome"; operators = "NavOpe"; cfg = "NavCfg"
                  plugins = "NavPack"; state = "NavState"; log = "NavLog"; about = "NavAbout"
                  deps = "NavDeps" }
        $on  = [System.Windows.Media.BrushConverter]::new().ConvertFromString("#F5A623")
        $off = [System.Windows.Media.Brushes]::Transparent
        $onFg  = [System.Windows.Media.BrushConverter]::new().ConvertFromString("#F0EDE4")
        $offFg = [System.Windows.Media.BrushConverter]::new().ConvertFromString("#8A93A0")
        foreach ($k in $map.Keys) {
            $b = $window.FindName($map[$k])
            if (-not $b) { continue }
            $sel = ($k -eq $page)
            $b.BorderBrush = $(if ($sel) { $on } else { $off })
            $b.Foreground  = $(if ($sel) { $onFg } else { $offFg })
        }
    } catch {}
}
# 关于页填充（2026-09-12）：**绝不显示编造的署名**——未设置就显示"（未设置）"。
function Refresh-About {
    try {
        $s = { param($n, $v) $t = $window.FindName($n); if ($t) { $t.Text = $v } }
        & $s "abVer" ([string]$script:LAUNCHER_VERSION)
        # CORE_API_VERSION 从 core\packs.py 现读（与 build_release.ps1 同一提取口径，不复制字面量）
        $api = "（读取失败）"
        try {
            $pk = Join-Path $root "qq-bot\core\packs.py"
            if (Test-Path $pk) {
                $m = Select-String -LiteralPath $pk -Pattern 'CORE_API_VERSION\s*=\s*"([^"]+)"' -List
                if ($m) { $api = $m.Matches[0].Groups[1].Value }
            }
        } catch {}
        & $s "abCoreApi" $api
        & $s "abAuthor"  $(if ([string]::IsNullOrWhiteSpace($script:FRAMEWORK_AUTHOR)) { "（未设置）" } else { $script:FRAMEWORK_AUTHOR })
        & $s "abRoot"    $root
        & $s "abSource"  $(if ([string]::IsNullOrWhiteSpace($script:SOURCE_URL)) { "（未设置）" } else { $script:SOURCE_URL })
        # 皮肤三字段来自活动皮肤 manifest（Get-SkinManifest 已有）；取不到一律 "—"
        $mf = $null
        try { $mf = Get-SkinManifest } catch {}
        $skinDir = ""
        try { $skinDir = [string](Get-SkinDir) } catch {}
        & $s "abSkinDir" $(if ($skinDir) { $skinDir } else { "—" })
        if ($mf) {
            $nm = "$([string]$mf.name) · $([string]$mf.title)"
            if ([string]::IsNullOrWhiteSpace([string]$mf.title)) { $nm = [string]$mf.name }
            & $s "abSkinName" $(if ([string]::IsNullOrWhiteSpace($nm)) { "—" } else { $nm })
            & $s "abSkinAuthor" $(if ([string]::IsNullOrWhiteSpace([string]$mf.author)) { "（未设置）" } else { [string]$mf.author })
            & $s "abSkinLicense" $(if ([string]::IsNullOrWhiteSpace([string]$mf.license)) { "—" } else { [string]$mf.license })
        } else {
            & $s "abSkinName" "—"; & $s "abSkinAuthor" "—"; & $s "abSkinLicense" "—"
        }
    } catch {}
}
function Show-Msg($m) {
    $t = $window.FindName("subTitle")
    if ($t) { $t.Text = $m }
    if ($script:wv -and $script:wv.CoreWebView2) {
        try { $json = ConvertTo-Json @{ __push = $true; dialog = $m } -Compress
            $script:wv.CoreWebView2.PostWebMessageAsString($json) | Out-Null } catch {}
    }
}

# ---------- 依赖与组件（S5）----------
# 只**报告**：不自动下载、不自动改配置。缺什么、为什么缺、去哪拿，三件事说清楚即可——
# 「一键下载」涉及镜像可信度与网络失败路径，属需人工裁决项（见 docs/遗留任务.md）。
function Refresh-Deps {
    try {
        $lst = $window.FindName("LstDeps")
        if (-not $lst) { return }
        $lst.Items.Clear()
        $feats = Get-Deps
        if (-not @($feats).Count) {
            $lst.Items.Add("（读不到 launcher\deps.json——依赖矩阵缺失，无法检测）") | Out-Null
            return
        }
        $ok = 0; $miss = 0
        foreach ($f in $feats) {
            $alive = Test-DepFeature $f
            if ($alive) { $ok = $ok + 1 } else { $miss = $miss + 1 }
            $tag = if ($f.required) { "必须" } else { "可选" }
            $dot = if ($alive) { "[●]" } else { "[ ]" }
            $lst.Items.Add(("{0} {1,-16} 〔{2}〕 {3}" -f $dot, $f.name, $tag, "$(($f.detect).label)")) | Out-Null
        }
        $msg = $window.FindName("txtDepMsg")
        if ($msg) {
            $msg.Text = "就绪 $ok / 缺失 $miss（缺的都可以缺——每项都有降级路径，见右侧说明）"
        }
        $lst.SelectedIndex = 0
    } catch {}
}
function Show-DepDetail($f) {
    try {
        $s = { param($n, $v) $t = $window.FindName($n); if ($t) { $t.Text = $v } }
        if (-not $f) { return }
        & $s "depName" ([string]$f.name)
        $alive = Test-DepFeature $f
        & $s "depState" $(if ($alive) { "● 已就绪" } else { "○ 未检测到" })
        $st = $window.FindName("depState")
        if ($st) {
            $c = if ($alive) { [System.Windows.Media.Color]::FromRgb(0x7C, 0xB8, 0x7C) } else { [System.Windows.Media.Color]::FromRgb(0xF5, 0xA6, 0x23) }
            $st.Foreground = [System.Windows.Media.SolidColorBrush]::new($c)
        }
        & $s "depRole" ([string]$f.provides)
        & $s "depDegrade" ([string]$f.degrade)
        $comps = @()
        foreach ($c in @($f.components)) {
            $p = [string]$c.path
            $exists = if ($p -and -not $p.StartsWith("（")) { Test-Path (Join-Path $root $p) } else { $false }
            $comps += ("{0} {1}`n    路径 {2}`n    来源 {3}`n    装法 {4}" -f $(if ($exists) { "●" } else { "○" }), $c.name, $p, $c.source, $c.install)
        }
        & $s "depComps" ($comps -join "`n`n")
        $script:depSel = $f
    } catch {}
}
function Open-DepTarget($which) {
    try {
        $f = $script:depSel
        if (-not $f) { return }
        if ($which -eq "dir") {
            $c = @($f.components) | Select-Object -First 1
            $p = [string]$c.path
            if (-not $p -or $p.StartsWith("（")) { Show-Msg "该组件是外部程序，没有安装根内的目录"; return }
            $full = Join-Path $root $p
            if (Test-Path $full) { Start-Process explorer.exe $full }
            else { Show-Msg "目录不存在：$full（把组件装到这里，或按右侧『装法』操作）" }
        } else {
            $c = @($f.components) | Select-Object -First 1
            $u = [string]$c.source
            if ($u -match "^https?://") { Start-Process $u }
            else { Show-Msg "该组件没有可直接打开的下载页（来源：$u）" }
        }
    } catch { Show-Msg "打开失败：$($_.Exception.Message)" }
}
# ---------- 页面切换（主页/干员/设置/日志/状态/内容包/关于） ----------
# 2026-09-12 UI 改版：二级页集合由这里唯一决定（新增页必须同时改此处 + Set-NavActive 映射表）。
$script:PAGES = @("PageCfg", "PageLog", "PageState", "PagePlugins", "PageAbout", "PageOperators", "PageDeps")
function Ensure-SkinPageVisible {
    # 2026-09-12 修复（用户实测："未启动时点 GAL → 网页未加载，主页键一直被盖住"）：
    # **主页 = PageHome = WebView 本身**。若 WebView 停在皮肤页以外的地方（GAL 页、或加载失败后
    # WebView2 自己的错误页），只把 WPF 那层切回 PageHome 是**没用的**——用户看到的仍是那张死页面，
    # 表现就是"主页键按了没反应、被盖住"。所以切主页前必须确认 WebView 真的在皮肤页上，
    # 不在就导航回去（皮肤入口 URL 由 Get-SkinEntryUrl 决定，换皮肤自动跟随）。
    try {
        if (-not $script:wv -or -not $script:wv.CoreWebView2) { return }
        $src = $script:wv.Source
        if ($src -and $src.Host -eq "app.local") { return }
        WEBLOG ("HOME navback from " + $(if ($src) { $src.AbsoluteUri } else { "(null)" }))
        $script:wv.CoreWebView2.Navigate((Get-SkinEntryUrl))
    } catch { WEBLOG "HOME navback ERR: $($_.Exception.Message)" }
}
function Get-SkinOwnsOperaPage {
    # 活动皮肤是否**自带**干员页（manifest 的 operaPage 键，见 docs/皮肤包接口规范-v1.md §2）。
    # 取不到 manifest = 一律 false（→ 用框架原生页兜底），绝不因为"读不到"就当成"皮肤有"。
    try { return [bool](Get-SkinManifest).operaPage } catch { return $false }
}
function Show-OperaPage {
    # 「干员」入口的**唯一路由**（顶栏 NavOpe 与皮肤 cmd=operators 都走这里）。
    # 2026-09-12 订正：上一轮我把皮肤自带的干员入口改去开框架原生页——**方向反了**。
    # 皮肤自己设计的干员界面（方舟样板的 #view-opera / detail.html）是用户的作品，框架不该顶替它；
    # 框架那张 PageOperators 只是"皮肤没提供时"的兜底。判据来自皮肤自己的声明（operaPage），
    # 不是框架猜——所以两套皮肤都不需要为这件事打补丁。
    if (Get-SkinOwnsOperaPage) { Show-Page "opera" } else { Show-Page "operators" }
}
function Show-Page($page) {
    if ($page -eq "home" -or $page -eq "opera") {
        foreach ($nm in $script:PAGES) {
            $pg = $window.FindName($nm)
            if ($pg) { $pg.Visibility = "Collapsed" }
        }
        $ph = $window.FindName("PageHome")
        if ($ph) { $ph.Visibility = "Visible" }
        # 主页/皮肤视图的前提：WebView 得真在皮肤页上（否则就是"主页键被盖住"，见 Ensure-SkinPageVisible）
        Ensure-SkinPageVisible
        Push-WebPage $page
        # 导航高亮：`opera` 是"皮肤自己的视图"，而它在皮肤里就是「干员」这个一级页——
        # 皮肤声明了 operaPage 时高亮「干员」，否则（如 generic 的滚动区）仍算主页。
        Set-NavActive $(if ($page -eq "opera" -and (Get-SkinOwnsOperaPage)) { "operators" } else { "home" })
    } else {
        foreach ($nm in $script:PAGES) {
            $pg = $window.FindName($nm)
            if ($pg) { $pg.Visibility = if ($nm -eq ("Page" + ($page.Substring(0,1).ToUpper()) + $page.Substring(1))) { "Visible" } else { "Collapsed" } }
        }
        if ($page -eq "cfg") { Refresh-Cfg }
        elseif ($page -eq "log") { Refresh-Log }
        elseif ($page -eq "state") { Refresh-State }
        elseif ($page -eq "plugins") { Refresh-Plugins }
        elseif ($page -eq "about") { Refresh-About }
        elseif ($page -eq "deps") { Refresh-Deps }
        elseif ($page -eq "operators") { Build-OperatorGrid }
        $phh = $window.FindName("PageHome")
        if ($phh) { $phh.Visibility = "Collapsed" }
        Set-NavActive $page
    }
}

# ---------- 干员一览 ----------
$script:selectedCard = ""
function Build-OperatorGrid {
    try {
        $wp = $window.FindName("opeGrid")
        $wp.Children.Clear()
        foreach ($c in (Get-Cards)) {
            $gb = New-Object System.Windows.Controls.Border
            $gb.Width = 128; $gb.Height = 186; $gb.Margin = [System.Windows.Thickness]::new(0, 0, 10, 10)
            $gb.Background = [System.Windows.Media.SolidColorBrush]::new([System.Windows.Media.Color]::FromRgb(0xED, 0xF1, 0xF5))
            $gb.BorderBrush = [System.Windows.Media.SolidColorBrush]::new([System.Windows.Media.Color]::FromRgb(0x3B, 0x82, 0xC4))
            $gb.BorderThickness = [System.Windows.Thickness]::new(1)
            $gb.Cursor = [System.Windows.Input.Cursors]::Hand
            $gb.Tag = $c.key
            $sp = New-Object System.Windows.Controls.StackPanel
            $img = New-Object System.Windows.Controls.Image
            $img.Height = 104; $img.Stretch = [System.Windows.Media.Stretch]::UniformToFill
            $img.Source = New-CardImage (Get-CardImage $c.key)
            $img.Margin = [System.Windows.Thickness]::new(4, 4, 4, 0)
            $sp.Children.Add($img) | Out-Null
            $nm = New-Object System.Windows.Controls.TextBlock
            $nm.Text = $c.name; $nm.FontSize = 11; $nm.FontWeight = [System.Windows.FontWeights]::SemiBold; $nm.TextTrimming = [System.Windows.TextTrimming]::CharacterEllipsis; $nm.TextWrapping = [System.Windows.TextWrapping]::NoWrap; $nm.MaxWidth = 120
            $nm.Foreground = [System.Windows.Media.SolidColorBrush]::new([System.Windows.Media.Color]::FromRgb(0x17, 0x19, 0x1D))
            $nm.HorizontalAlignment = [System.Windows.HorizontalAlignment]::Center
            $nm.Margin = [System.Windows.Thickness]::new(2, 4, 2, 0)
            $sp.Children.Add($nm) | Out-Null
            $st = New-Object System.Windows.Controls.TextBlock
            $st.Text = (New-StarText $c.star) + "  " + $c.cls
            $st.FontSize = 9; $st.FontFamily = [System.Windows.Media.FontFamily]::new("Consolas")
            $st.Foreground = [System.Windows.Media.SolidColorBrush]::new([System.Windows.Media.Color]::FromRgb(0xF5, 0xA6, 0x23))
            $st.HorizontalAlignment = [System.Windows.HorizontalAlignment]::Center
            $sp.Children.Add($st) | Out-Null
            $gb.Child = $sp
            $gb.Add_MouseLeftButtonUp({ Select-Operator ([string]$this.Tag) })
            $wp.Children.Add($gb) | Out-Null
        }
    } catch {}
}
function Select-Operator($key) {
    try {
        $script:selectedCard = $key
        $c = (Get-Cards) | Where-Object { $_.key -eq $key } | Select-Object -First 1
        if (-not $c) { return }
        $w = $window
        ($w.FindName("detailName")).Text = $c.name
        ($w.FindName("detailMeta")).Text = (New-StarText $c.star) + "  " + $c.cls + "  " + $c.uni
        ($w.FindName("detailUni")).Text = "--- " + $c.uni + " ---"
        ($w.FindName("detailDesc")).Text = $c.desc
        $img = $w.FindName("detailImg")
        if ($img) { $img.Source = New-CardImage (Get-CardImageFull $key) }
        $wp = $w.FindName("opeGrid")
        if ($wp) {
            foreach ($child in $wp.Children) {
                $sel = ([string]$child.Tag -eq $key)
                $child.BorderBrush = [System.Windows.Media.SolidColorBrush]::new([System.Windows.Media.Color]::FromRgb($(if ($sel) { 0xB0 } else { 0x3B }), $(if ($sel) { 0x7A } else { 0x82 }), $(if ($sel) { 0x1E } else { 0xC4 })))
                $child.BorderThickness = [System.Windows.Thickness]::new($(if ($sel) { 2 } else { 1 }))
            }
        }
        Update-Home
    } catch {}
}

# ---------- 主页（当前人设立绘 + 信息） ----------
$script:lastPersona = ""
function Get-PersonaQuip($key) {
    # 从人设卡取一句台词（mes_example / style_sample 标志句）
    try {
        $f = "$root\qq-bot\data\personas\$key.json"
        if (Test-Path $f) {
            $j = Get-Content $f -Raw -Encoding UTF8 | ConvertFrom-Json
            $d = $j.data
            if ($d.mes_example) { $q = ($d.mes_example -split "`n")[0].Trim(); if ($q) { return ($q -replace '^\s*[「"“]|[」"”]\s*$', '') } }
            if ($d.style_sample) {
                $m = [regex]::Match([string]$d.style_sample, '标志句】([^\n]+)')
                if ($m.Success) { return $m.Groups[1].Value.Trim() }
            }
        }
    } catch {}
    return ""
}

function Update-Home {
    try {
        $script:lastPersona = Get-OwnerPersona
        Push-Web
    } catch {}
}

# ---------- 刷新 ----------
function Refresh-Overview {
    try {
        WEBLOG "R tick"
        # 顶栏常驻四灯：**无条件刷新**（2026-09-12 修 P3——原先只在运行状态页可见时才更新，
        # 结果人停在主页时四灯永远是灰的，值班根本看不见服务掉线）
        Set-LampMini "tbDotEngine" "tbTxtEngine" (Test-Engine)    "引擎"
        Set-LampMini "tbDotEmbed"  "tbTxtEmbed"  (Test-Embedding) "记忆"
        Set-LampMini "tbDotNap"    "tbTxtNap"    (Test-Napcat)    "QQ"
        Set-LampMini "tbDotBot"    "tbTxtBot"    (Test-Bot)       "机器人"
        WEBLOG "R toplamps done"
        $psv2 = $window.FindName("PageState")
        if ($psv2 -and $psv2.Visibility.ToString() -eq "Visible") {
            $script:probeCache = @{}
            Set-Lamp "dotEngine2" "txtEngine2" (Test-Engine) "推理引擎"
            Set-Lamp "dotEmbed2"  "txtEmbed2"  (Test-Embedding) "记忆服务"
            Set-Lamp "dotNap2"    "txtNap2"    (Test-Napcat) "NapCat"
            Set-Lamp "dotBot2"    "txtBot2"    (Test-Bot) "机器人"
        }
        WEBLOG "R lamps done"
        $psv = $window.FindName("PageState")
        if ($psv -and $psv.Visibility.ToString() -eq "Visible") { Refresh-State }
        $cl = $window.FindName("topClock")
        if ($cl) { $cl.Text = (Get-Date).ToString("HH:mm:ss") }
        WEBLOG "R clock done"
        Push-Web
        WEBLOG "R push done"
        # QQ 内切人设同步（persona_select 变化 → 主页立绘/详情切换）
        $op = Get-OwnerPersona
        if ($op -ne $script:lastPersona) {
            Update-Home
            $pw = $window.FindName("PageOperators")
            if ($pw -and $pw.Visibility.ToString() -eq "Visible") { Select-Operator $op }
        }
    } catch {}
}
function Refresh-Cfg {
    try {
        $cfg = Get-LauncherCfg
        $window.FindName("chkLlm").IsChecked = $cfg.llm
        $window.FindName("chkVoice").IsChecked = $cfg.voice
        # 2026-09-09 新增：直播开关回显（读 E:\robot\qq-bot\.env）
        $window.FindName("chkLiveDanmaku").IsChecked = (Get-EnvFlag "LIVE_DANMAKU_ENABLED")
        $window.FindName("chkLiveAudio").IsChecked = (Get-EnvFlag "LIVE_AUDIO_PLAYBACK")
        # 2026-09-09 新增：聊天软件模式跨重启保留开关回显（读 E:\robot\qq-bot\.env；缺省=关）
        $window.FindName("chkChatPersist").IsChecked = (Get-EnvFlag "WEBGAL_CHAT_PERSIST")
        # 2026-09-10 Phase2：皮肤下拉回显（arknights 仅在皮肤包目录存在时入列；公开精简发行只有 generic 一项）
        $cb = $window.FindName("CmbSkin")
        if ($cb) {
            if ($cb.Items.Count -eq 0) {
                $i1 = New-Object System.Windows.Controls.ComboBoxItem
                $i1.Content = "简洁控制台（generic）"; $i1.Tag = "generic"
                $cb.Items.Add($i1) | Out-Null
                if (Test-Path "$root\launcher\web\skins\arknights\manifest.json") {
                    $i2 = New-Object System.Windows.Controls.ComboBoxItem
                    $i2.Content = "明日方舟（arknights）"; $i2.Tag = "arknights"
                    $cb.Items.Add($i2) | Out-Null
                }
            }
            $cur = [string](Get-LauncherCfg)["skin"]
            foreach ($it in @($cb.Items)) { if ([string]$it.Tag -eq $cur) { $cb.SelectedItem = $it; break } }
            if (-not $cb.SelectedItem -and $cb.Items.Count -gt 0) { $cb.SelectedIndex = 0 }
        }
        # 2026-09-11 热修六：模型与接口回显（读 qq-bot\.env 的 LLM_*，回落 OLLAMA_*/本地默认）
        # 2026-09-12 T5.2：provider 改预设下拉（10 项）。Tag 仍是 local / openai_compat，
        # 保证 core/llm.py 的 local/非local 二分语义与既有 .env 取值完全不变（旧配置照旧回显）。
        $cbp = $window.FindName("CmbProvider")
        if ($cbp) {
            if ($cbp.Items.Count -eq 0) {
                $presets = Get-ProviderPresets
                foreach ($nm in (Get-ProviderPresetNames)) {
                    $it = New-Object System.Windows.Controls.ComboBoxItem
                    $it.Content = $nm; $it.Tag = [string]$presets[$nm].tag
                    $cbp.Items.Add($it) | Out-Null
                }
            }
            $prov = Get-EnvValue "LLM_PROVIDER"
            $curP = if ($prov -ne "" -and $prov -ne "local") { "openai_compat" } else { "local" }
            $hitP = $false
            foreach ($it in @($cbp.Items)) { if ([string]$it.Tag -eq $curP) { $cbp.SelectedItem = $it; $hitP = $true; break } }
            if (-not $hitP -and $cbp.Items.Count -gt 0) { $cbp.SelectedIndex = 0 }
        }
        $u = $window.FindName("TxtApiUrl"); if ($u) { $u.Text = Get-EnvValue "LLM_BASE_URL"; if (-not $u.Text) { $u.Text = Get-EnvValue "OLLAMA_BASE_URL" }; if (-not $u.Text) { $u.Text = "http://127.0.0.1:11434/v1" } }
        $k = $window.FindName("TxtApiKey"); if ($k) { $k.Text = Get-EnvValue "LLM_API_KEY"; if (-not $k.Text) { $k.Text = "ollama" } }
        $m = $window.FindName("TxtModel"); if ($m) { $m.Text = Get-EnvValue "LLM_MODEL"; if (-not $m.Text) { $m.Text = Get-EnvValue "OLLAMA_MODEL" }; if (-not $m.Text) { $m.Text = "gemma" } }
        # 2026-09-12 界面语言回显 + 应用（放在这一处，Refresh-Cfg 每次进设置页都会跑；幂等）
        $clg = $window.FindName("CmbLang")
        if ($clg) {
            if ($clg.Items.Count -eq 0) {
                foreach ($li in (Get-UiLangItems)) {
                    $it = New-Object System.Windows.Controls.ComboBoxItem
                    $it.Content = [string]$li["text"]; $it.Tag = [string]$li["tag"]
                    $clg.Items.Add($it) | Out-Null
                }
            }
            $script:uiLang = Get-UiLang
            $script:langGuard = $true
            try {
                foreach ($it in @($clg.Items)) { if ([string]$it.Tag -eq $script:uiLang) { $clg.SelectedItem = $it; break } }
            } finally { $script:langGuard = $false }
        }
        Apply-UiLang $script:uiLang
        # 2026-09-12 配置档回显（2026-09-12 用户报"切在线后再切回去没变"）：
        # 档下拉 + **把活动档的值载进三格**（界面必须跟着档变）+ 状态对照行。
        # 注意顺序：三格先按 .env 填（上一行），再由档覆盖——这样"档"是编辑对象、".env"是生效事实，
        # 两者不一致时由 Update-LlmProfileNote 明确喊出来，而不是让用户猜。
        $cpf = $window.FindName("CmbProfile")
        if ($cpf) {
            if ($cpf.Items.Count -eq 0) {
                foreach ($pair in @(@("本地（local 引擎）", "local"), @("在线（API 端点）", "online"))) {
                    $it = New-Object System.Windows.Controls.ComboBoxItem
                    $it.Content = [string]$pair[0]; $it.Tag = [string]$pair[1]
                    $cpf.Items.Add($it) | Out-Null
                }
            }
            $prof = Get-LlmProfiles
            $script:llmProfileGuard = $true      # 抑制"程序性选中"触发载档（否则会与上面的 .env 回显打架）
            try {
                $hitPf = $false
                foreach ($it in @($cpf.Items)) { if ([string]$it.Tag -eq $prof.active) { $cpf.SelectedItem = $it; $hitPf = $true; break } }
                if (-not $hitPf -and $cpf.Items.Count -gt 0) { $cpf.SelectedIndex = 0 }
            } finally { $script:llmProfileGuard = $false }
            Load-LlmProfileToBoxes $prof.active
            Update-LlmProfileNote
        }
        # 2026-09-12 T5.1：身份区回显（SUPERUSERS 是 JSON 数组，只取首个元素展示；OWNER_NICKNAME 直读）
        $oq = $window.FindName("TxtOwnerQq"); if ($oq) { $oq.Text = Get-OwnerQqEnv }
        $ont = $window.FindName("TxtOwnerNick"); if ($ont) { $ont.Text = Get-EnvValue "OWNER_NICKNAME" }
        # 换 QQ 的二次确认勾选框：每次刷新都复位（防上一次的勾选被当成本次确认）
        $cq = $window.FindName("ChkOwnerQqConfirm"); if ($cq) { $cq.IsChecked = $false }
        # 2026-09-12 T4.2：思考三态回显——读 think_mode.json 首个 uid 的键值定位；
        # 该用户键缺失 = 自决（与 brain.set_think_mode(uid, "auto") 删除该键等价）。
        $cth = $window.FindName("CmbThink")
        if ($cth) {
            if ($cth.Items.Count -eq 0) {
                $pairs = @(@("自决（默认）", "auto"), @("恒定开启", "on"), @("恒定关闭", "off"))
                foreach ($pair in $pairs) {
                    $it = New-Object System.Windows.Controls.ComboBoxItem
                    $it.Content = [string]$pair[0]; $it.Tag = [string]$pair[1]
                    $cth.Items.Add($it) | Out-Null
                }
            }
            $ts = Get-ThinkModeState
            $want = "auto"
            if ($ts.uid -and ($ts.mode -eq "on" -or $ts.mode -eq "off")) { $want = [string]$ts.mode }
            $hitT = $false
            foreach ($it in @($cth.Items)) { if ([string]$it.Tag -eq $want) { $cth.SelectedItem = $it; $hitT = $true; break } }
            if (-not $hitT -and $cth.Items.Count -gt 0) { $cth.SelectedIndex = 0 }
        }
        # 模型名行的常驻说明（2026-09-12）：预设里的型号名会过期，界面必须自己说清"去哪儿拿现的"。
        $mnote = $window.FindName("txtModelNote")
        if ($mnote) {
            $mnow = "$(Get-EnvValue 'LLM_MODEL')"
            $mnote.Text = "型号名由服务商维护、会过期（例：DeepSeek 早年的 deepseek-chat 已停用，现行为 deepseek-flash）。" +
                          "填好『接口地址 URL』+『API Key』后点「拉取型号」按端点现取；当前保存值：" + $(if ($mnow) { $mnow } else { "（未设置＝本地引擎默认）" })
        }
    } catch {}
}
# ---------------- 插件管理（2026-09-11 热修六：已装目录 + 快速安装通道） ----------------
# 注：Get-PackDirs 已上移到文件前部（无界面模式要能在 dispatch 前调用它，见那里的说明）。
function Refresh-Plugins {
    # 内容包页列表：三类分开列出（内容包 / 皮肤 / 第三方插件）。
    # 2026-09-12 S5：原先只列 robot-pack-v1，皮肤与 data/plugins 里的代码插件在界面上**看不见**——
    # 而"能不能看见自己装了什么"恰恰是这一页存在的理由。
    try {
        $lst = $window.FindName("LstPacks")
        if (-not $lst) { return }
        $lst.Items.Clear()
        # 列表里既有分组标题又有条目 → 行号不再等于条目号。用**同序映射**把行号→条目记下来，
        # 点选时按它取详情（组件页没有分组标题，所以它可以直接按下标重算；这里不行）。
        $script:packRows = @()
        $items = Get-InstalledItems
        $n = @{ "内容包" = 0; "舞台包" = 0; "皮肤" = 0; "插件" = 0 }
        # 每类"放哪里"的唯一出处：空组提示与全空提示共用（原先这段文案写死在下面的全空分支里，
        # 于是"只有插件为空"时用户什么都看不到——2026-09-12 用户实测："之前说改为插件的也没看到"）。
        $hint = @{
            "内容包" = "把含 pack.json 的目录放进 data\packs\（或点上方「从文件夹/zip 安装」）"
            "舞台包" = "同上，pack.json 的 type 写 gal（给背景 bg/ 与差分 sprites/diff/）"
            "皮肤"   = "把皮肤目录放进 launcher\web\skins\（含 manifest.json）"
            "插件"   = "把代码插件放进 data\plugins\（子包目录或单个 .py；重启 bot 生效）"
        }
        foreach ($k in @("内容包", "舞台包", "皮肤", "插件")) {
            $grp = @($items | Where-Object { $_.kind -eq $k })
            # 空组不再整组消失：**类别消失**会让用户以为"这个功能不存在"（真发生过）。
            # 分组标题恒在，空组下面挂一行"放哪里"——比让人猜准得多。
            $lst.Items.Add("── $k（$($grp.Count)） ─────────────────────────────") | Out-Null
            $script:packRows += $null
            if (-not $grp.Count) {
                $lst.Items.Add("  （暂无——" + $hint[$k] + "）") | Out-Null
                $script:packRows += $null
                continue
            }
            foreach ($p in $grp) {
                # 行内只留能扫读的：名字 + 类型 + 标题。作者/许可/来源移到右栏——
                # 原先一行用 {0,-22} **按字符数**补空格对齐，中文标题必然错位（视觉 bug）。
                $tag = if ($p.type -and $k -ne "皮肤" -and $k -ne "插件") { " ⟨" + [string]$p.type + "⟩" } else { "" }
                $lst.Items.Add(("  " + [string]$p.name + $tag + "  ·  " + [string]$p.title)) | Out-Null
                $script:packRows += $p
                $n[$k] = $n[$k] + 1
            }
        }
        if (-not @($items).Count) {
            # 全空时不再重复列四行"放哪里"——那四段文案已经各自挂在四组标题下面了（$hint 唯一出处）。
            $lst.Items.Add("（未发现任何已装件——四类各自的「放哪里」见下面各组的提示行）") | Out-Null
            $script:packRows += $null
        }
        $msg = $window.FindName("txtPackMsg")
        # 汇总行**恒显示四类计数**（含 0）：原先只有"至少有 1 件"才显示汇总，
        # 于是"插件 0 件"这种信息在界面上完全不存在，用户只能看到少了一组（2026-09-12 用户实测）。
        if ($msg) { $msg.Text = "内容包 $($n['内容包']) · 舞台包 $($n['舞台包']) · 皮肤 $($n['皮肤']) · 插件 $($n['插件'])" }
        # 自动选中第一个**真条目**（可能是第 2 行：第 1 行是分组标题），右栏立刻有内容——
        # 否则"点一下才显示"会让空右栏看起来像坏了。
        $first = -1
        for ($i = 0; $i -lt @($script:packRows).Count; $i++) { if ($script:packRows[$i]) { $first = $i; break } }
        if ($first -ge 0) { $lst.SelectedIndex = $first; Show-PackDetail $script:packRows[$first] }
        else { Show-PackDetail $null }
    } catch {}
}
function Install-Pack($srcPath) {
    try {
        $tmp = $null
        $p = $srcPath
        if ($p -like "*.zip" -and (Test-Path $p -PathType Leaf)) {
            $tmp = Join-Path $env:TEMP ("packinst_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
            Expand-Archive -LiteralPath $p -DestinationPath $tmp -Force
            if (Test-Path (Join-Path $tmp "pack.json")) { $p = $tmp }
            else {
                $sub = Get-ChildItem $tmp -Directory | Where-Object { Test-Path (Join-Path $_.FullName "pack.json") } | Select-Object -First 1
                if ($sub) { $p = $sub.FullName } else { Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue; return "安装失败：zip 内未找到 robot-pack-v1 插件（pack.json）" }
            }
        }
        if (-not (Test-Path (Join-Path $p "pack.json"))) { return "安装失败：目标不含 pack.json（不是 robot-pack-v1 插件包）" }
        $j = Get-Content (Join-Path $p "pack.json") -Raw -Encoding UTF8 | ConvertFrom-Json
        if ("$($j.spec)" -ne "robot-pack-v1") { return "安装失败：spec='$($j.spec)' 不是 robot-pack-v1" }
        $name = [IO.Path]::GetFileName($p)
        if (-not $name) { $name = [guid]::NewGuid().ToString("N").Substring(0, 8) }
        $dst = "$root\data\packs\$name"
        if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
        Copy-Item $p $dst -Recurse -Force
        if ($tmp) { Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue }
        $type = "$($j.type)"
        $py = "$root\qq-bot\.venv\Scripts\python.exe"
        if ($type -eq "card" -and (Test-Path $py)) {
            try { & $py "$root\qq-bot\tools\export_cards.py" 2>&1 | Out-Null } catch {}
        }
        Refresh-Plugins
        return "已安装：$name（自动识别类型：$type）——卡类包已刷新主页人设数据；bot 运行中需重启 bot 生效"
    } catch { return "安装失败：$($_.Exception.Message)" }
}
function Install-PackFolderUI {
    try {
        Add-Type -AssemblyName System.Windows.Forms | Out-Null
        $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
        $dlg.Description = "选择含 pack.json 的插件目录"
        if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
            $msg = Install-Pack $dlg.SelectedPath
            $m = $window.FindName("txtPackMsg"); if ($m) { $m.Text = $msg }
        }
    } catch { try { $m = $window.FindName("txtPackMsg"); if ($m) { $m.Text = "安装失败：" + $_.Exception.Message } } catch {} }
}
function Install-PackZipUI {
    try {
        Add-Type -AssemblyName System.Windows.Forms | Out-Null
        $dlg = New-Object System.Windows.Forms.OpenFileDialog
        $dlg.Filter = "插件包 zip|*.zip"
        if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
            $msg = Install-Pack $dlg.FileName
            $m = $window.FindName("txtPackMsg"); if ($m) { $m.Text = $msg }
        }
    } catch { try { $m = $window.FindName("txtPackMsg"); if ($m) { $m.Text = "安装失败：" + $_.Exception.Message } } catch {} }
}
function Refresh-State {
    try {
        $s = Get-LifeState
        $w = $window
        ($w.FindName("txtScene")).Text = $s.scene
        ($w.FindName("txtDoing")).Text = "正在：$($s.doing)"
        ($w.FindName("txtMoodPlan")).Text = "心情：$($s.mood)    今天打算：$($s.plan)"
        ($w.FindName("txtWake")).Text = $s.wake
        ($w.FindName("txtTimeline")).Text = (Get-LifeLog 100) -join "`n"
    } catch {}
}
function Refresh-Log($silent = $false) {
    try {
        # 视图由页内开关决定：不勾=仅报错（默认，日常）；勾=全文尾部（排查时要看错误之前的上下文）
        $full = $false
        $ck = $window.FindName("ChkLogFull")
        if ($ck) { $full = [bool]$ck.IsChecked }
        $out = if ($full) { Get-LogTail 200 } else { Get-Errors }
        $t = $window.FindName("txtLog")
        if ($t) {
            $t.Text = if ($out.Count) { $out -join "`n" }
                      else { $(if ($full) { "（日志为空或读不到——bot 跑过之后这里会有内容）" } else { "（暂无报错——一切正常）" }) }
        }
        if (-not $silent) {
            $i2 = $window.FindName("txtLogInfo")
            if ($i2) { $i2.Text = "$(if ($full) { '全文尾部' } else { '仅报错' }) · 显示 $($out.Count) 行 · 5 秒自动刷新" }
        }
        $f3 = $window.FindName("txtLogFiles")
        if ($f3) {
            $parts = @()
            foreach ($lf in (Get-LogFiles)) { $parts += ([string]$lf.name + " " + (Format-Size $lf.size)) }
            $f3.Text = if ($parts.Count) {
                "日志文件：" + ($parts -join " · ") + "　（bot.log 超 10 MB 自动轮转、保留 3 份）"
            } else { "未找到日志文件（bot 还没跑过？）" }
        }
        $scroll = $window.FindName("logScroll")
        if ($scroll) { $scroll.ScrollToEnd() }
    } catch {}
}

# ---------- 事件 ----------
# ---------- WebView2 主页 ----------
$script:wv = $null
function BindClick($name, $scriptBlock) {
    $b = $window.FindName($name)
    if ($b) { $b.Add_Click($scriptBlock) }
}
function BindSelect($name, $scriptBlock) {
    # 下拉/列表类控件的绑定口（2026-09-12）：`Add_Click` 只存在于 ButtonBase 派生类上——
    # 给 ComboBox 用 BindClick 会**静默无效**（无 Click 事件 → 抛异常被吞 → 选中什么都不发生），
    # 正是本项目最恨的那类故障。ComboBox/ListBox 要的是 SelectionChanged。
    $b = $window.FindName($name)
    if ($b) { $b.Add_SelectionChanged($scriptBlock) }
}
function WEBLOG($msg) {
    # 2026-09-12 修：原先纯追加、**无任何上限**——实测已涨到 9.3 MB 且每次操作都在长。
    # 这是个会一直长的隐藏故障（磁盘耗尽），而它不在任何测试的视野里。
    # 处理：超 2 MB 时只保留尾部 2000 行再追加。**不做 .1 滚动文件**——这份日志只用于
    # "刚才那几步发生了什么"，历史价值低，滚动文件只会让目录更乱。
    try {
        $f = "$script:logDir\webstep.log"
        try {
            $fi = Get-Item -LiteralPath $f -ErrorAction SilentlyContinue
            if ($fi -and $fi.Length -gt 2MB) {
                $tail = @(Get-Content -LiteralPath $f -Tail 2000 -ErrorAction SilentlyContinue)
                [System.IO.File]::WriteAllLines($f, $tail)
            }
        } catch {}
        [System.IO.File]::AppendAllText($f, (Get-Date).ToString("HH:mm:ss.fff") + "  " + $msg + [Environment]::NewLine)
    } catch {}
}
function Push-WebPage($pg) {
    # 仅记录目标页并即时通知（不再写 state.json——由 Push-Web 统一单写）
    $script:pendingPage = $pg
    try {
        if ($script:wv -and $script:wv.CoreWebView2) {
            $script:wv.CoreWebView2.PostWebMessageAsString((ConvertTo-Json @{ __push = $true; page = $pg } -Compress -Depth 2)) | Out-Null
        }
    } catch {}
}
function Push-Web {
    try {
        WEBLOG ("PW wv=" + ($null -ne $script:wv) + " core=" + ($null -ne $script:wv.CoreWebView2))
        if (-not $script:wv -or -not $script:wv.CoreWebView2) { return }
        $ls = try { Get-LifeState } catch { @{ scene = "-"; doing = "等待指令…" } }
        if (-not $ls) { $ls = @{ scene = "-"; doing = "等待指令…" } }
        $op = try { Get-OwnerPersona } catch { "" }
        $c = try { (Get-Cards) | Where-Object { $_.key -eq $op } | Select-Object -First 1 } catch { $null }
        $pu = ""
        if ($c) { $pf = try { Get-CardImageHome $c.key } catch { "" }; if ($pf) { $pu = "https://cards.local/" + ([System.IO.Path]::GetFileName($pf)) } }
        # 主人昵称（皮肤规范 §3 契约：`name` = **主人**昵称，"运行时取 bot 配置"，示例值 `@owner`）。
        # 2026-09-12 修：原先硬编码 "@晓咕咕Max"——那是**框架作者**的昵称，两处错：
        #   ① 语义错：皮肤拿它当"用户自己的显示名"，等于把作者署名当用户身份（同 9/12 brain 那处
        #      "拿主人昵称称呼第三方"的错，是同一类）；
        #   ② 外泄：每个用户的 state.json 里都带着作者的昵称（审计 O 项只列不判的那类）。
        # .env 的 OWNER_NICKNAME 是既有开关（.env.example 第 15 行有占位）；缺省回落中性值"主人"。
        $ownerName = "$(Get-EnvValue 'OWNER_NICKNAME')"
        if (-not $ownerName) { $ownerName = "主人" }
        $d = @{
            time    = (Get-Date).ToString("yyyy/MM/dd HH:mm")
            dialog  = $(try { ($ls.doing -replace "【.*$", "").Trim() } catch { "等待指令…" })
            scene   = $(try { $ls.scene } catch { "-" })
            name    = $ownerName
            star    = $(if ($c) { try { New-StarText $c.star } catch { "6★" } } else { "6★" })
            idtext  = $(if ($c) { "$($c.name) · $($c.uni)" } else { "博士 · 本地实例" })
            portrait = $pu
        }
        # 2026-09-10 Phase2 增量字段（只增不改，ark 皮肤忽略）：生活状态全量 + 四服务灯（皮肤主页状态区用）
        $d.doing = $(try { $ls.doing } catch { "-" })
        $d.mood  = $(try { $ls.mood } catch { "-" })
        $d.plan  = $(try { $ls.plan } catch { "-" })
        $d.wake  = $(try { $ls.wake } catch { "-" })
        $d.svc   = @{ engine = [bool](Test-Engine); embed = [bool](Test-Embedding); napcat = [bool](Test-Napcat); bot = [bool](Test-Bot) }
        $script:stamp = [int]$script:stamp + 1
        $d.__push = $true
        $d.stamp = $script:stamp
        $d.pid = $PID
        # 界面语言（2026-09-12）：皮肤页据此换文案；GAL 页由皮肤页在 URL 上带 &lang=。
        # 用内存值（$script:uiLang）而不是每 3 秒读一次配置文件。
        $d.lang = $(if ($script:uiLang) { $script:uiLang } else { "zh" })
        if ($script:pendingPage) { $d.page = $script:pendingPage }
        $d.cards = try { Get-CardsPayload } catch { @() }
        $d.op = @{ active = [bool]$script:opText; text = "$script:opText" }
        $json = ConvertTo-Json $d -Compress
        try {
            # 2026-09-10 Phase2：写入活动皮肤目录（generic=web 根；arknights=skins\arknights——皮肤页按相对路径轮询同目录 state.json）
            [System.IO.File]::WriteAllText((Get-SkinDir) + "\state.json", $json, [System.Text.UTF8Encoding]::new($false))
        } catch { [System.IO.File]::AppendAllText("$script:logDir\boot_err.log", ("STATEW " + $_.Exception.Message + [Environment]::NewLine), [System.Text.UTF8Encoding]::new($true)) | Out-Null }
        $script:wv.CoreWebView2.PostWebMessageAsString($json) | Out-Null
    } catch {}
}
function Handle-WebCmd($cmd, $data) {

    try {
        if ($cmd -eq "start")   {
            if (Start-StepMachine (New-StartSteps) "⚠（正在启动全部…）") {
                Show-Msg "正在启动全部…（引擎就绪约 30-60 秒）"
            }
        }        elseif ($cmd -eq "stop")    {
            if (Start-StepMachine (New-StopSteps) "⚠（正在停止全部…）") {
                Show-Msg "正在停止全部（含 LLM）…"
            }
        }        elseif ($cmd -eq "stopbot") { Show-Msg (Invoke-StopBotOnly) }
        elseif ($cmd -eq "exit")    {
            # 2026-09-06 修复：先停止后关窗（原实现先 Close 再对已关窗推送——次序颠倒）
            $script:exitAfterStop = $true
            Start-StepMachine (New-StopSteps) "⚠（正在停止全部…）" | Out-Null
        }
        elseif ($cmd -eq "opera")   { Show-Page "opera" }
        # `operators`（2026-09-12 新增，同日订正语义）：皮肤请求打开「干员」。
        # **不是**"一律开框架原生页"，而是走 Show-OperaPage 的唯一路由：
        # 皮肤自带干员页（manifest `operaPage: true`）→ 打开**皮肤自己的**视图；否则框架原生页兜底。
        # 补这个命令的原因（用户实测 A10）：顶栏能点的二级页里原先只有 cfg/plugins/log/state 有 cmd，
        # 皮肤发 operators/deps/about 三个名字是**静默无效**——想做点什么都只能自己再画一套。
        # `deps`/`about` 一并开，理由同上。
        elseif ($cmd -eq "operators") { Show-OperaPage }
        elseif ($cmd -eq "deps")    { Show-Page "deps" }
        elseif ($cmd -eq "about")   { Show-Page "about" }
        elseif ($cmd -eq "cfg")     { Show-Page "cfg" }
        elseif ($cmd -eq "plugins") { Show-Page "plugins" }
        elseif ($cmd -eq "log")     { Show-Page "log" }
        elseif ($cmd -eq "state")   { $script:probeCache = @{}; Show-Page "state" }
        elseif ($cmd -eq "home")    { Show-Page "home" }
        elseif ($cmd -eq "setpersona") {
            if ($data) {
                try {
                    $body = @{ name = $data; uid = (Get-OwnerId) } | ConvertTo-Json -Compress
                    $rp = Invoke-RestMethod -Uri "http://127.0.0.1:8080/launcher/persona" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 8
                    if ($rp.ok) { Set-OwnerPersona $data; Update-Home; Show-Msg "已切换人设：$data（bot 在线切换，无需重启）" }
                    else { Show-Msg "切换失败：$($rp.err)（bot 无此卡或未注册）" }
                } catch {
                    Set-OwnerPersona $data  # 通道不通时兜底写选择（下次 bot 重启生效）
                    Show-Msg "切换失败（通道）：$($_.Exception.Message) —— 已记录，bot 重启后生效"
                }
            }
        }
        try { [System.IO.File]::AppendAllText("$script:logDir\web_cmd.log", (Get-Date).ToString("HH:mm:ss") + " " + $cmd + " " + $data + [Environment]::NewLine) } catch {}
        try { Push-Web } catch {}
    } catch {}
}
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32Title {
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder lpString, int nMaxCount);
}
"@
function Init-WebView {
    try {
        WEBLOG "S1 start"
        $wh = $window.FindName("WebHost")
        if (-not $wh) { WEBLOG "S2 WebHost NULL"; return }
        WEBLOG "S2 found WebHost"
        [System.Reflection.Assembly]::LoadFrom("$script:logDir\Microsoft.Web.WebView2.Core.dll") | Out-Null
        [System.Reflection.Assembly]::LoadFrom("$script:logDir\Microsoft.Web.WebView2.Wpf.dll") | Out-Null
        New-Item -ItemType Directory -Force -Path "$script:logDir\webdata3" | Out-Null
        $script:wvCtl = $wh
        try { $script:wvCtl.DefaultBackgroundColor = [System.Windows.Media.Color]::FromRgb(0x0D, 0x0F, 0x12) } catch {}   # 2026-09-09 修复：先赋值再设色（原两行顺序先用后赋，NullReferenceException 被 catch 吞，背景色永不生效）
        WEBLOG "S3 webview created+hosted"
        WEBLOG "S4 creating env"
        $wvOpts = [Microsoft.Web.WebView2.Core.CoreWebView2EnvironmentOptions]::new()
        try { $wvOpts.AdditionalBrowserArguments = "--enable-gpu-rasterization --ignore-gpu-blocklist --disk-cache-size=0 --media-cache-size=0" } catch {}
        $script:tEnv = [Microsoft.Web.WebView2.Core.CoreWebView2Environment]::CreateAsync($null, "$script:logDir\webdata3", $wvOpts)
        $script:envTimer = New-Object System.Windows.Threading.DispatcherTimer
        $script:envTimer.Interval = [TimeSpan]::FromMilliseconds(250)
        $script:envTimer.Add_Tick({
            $script:envTimer.Stop()
            WEBLOG "S6 timer tick, env completed=$($script:tEnv.IsCompleted)"
            try {
                $env0 = $script:tEnv.GetAwaiter().GetResult()
                WEBLOG "S6b env ok, ensuring core"
                $null = $script:wvCtl.EnsureCoreWebView2Async($env0)
                WEBLOG "S6c ensure called"
            } catch { WEBLOG "S6 ERR: $($_.Exception.Message)"; try { [System.IO.File]::WriteAllText("$script:logDir\webview2_error.log", $_.Exception.ToString()) } catch {} }
        })
        $script:envTimer.Start()
        WEBLOG "S5 env timer started"
        $script:wvCtl.add_CoreWebView2InitializationCompleted({ param($s, $e)
            WEBLOG "S7 init completed success=$($e.IsSuccess)"
            if ($e.IsSuccess) {
                try {
                    WEBLOG "S8 configuring"
                    $core = $s.CoreWebView2
                    $core.SetVirtualHostNameToFolderMapping("app.local", "$script:logDir\web", [Microsoft.Web.WebView2.Core.CoreWebView2HostResourceAccessKind]::Allow)
                    $core.SetVirtualHostNameToFolderMapping("cards.local", "$script:logDir\cards", [Microsoft.Web.WebView2.Core.CoreWebView2HostResourceAccessKind]::Allow)
                    # 2026-09-10 Phase2：launcher\ui 已整体迁入 skins\arknights\ui（皮肤包分离）——ui.local 重指新址，
                    # ark 皮肤页内既有 ui.local/ark/sel_top.png、det_stars.png 引用字节不变继续可用
                    $core.SetVirtualHostNameToFolderMapping("ui.local", "$script:logDir\web\skins\arknights\ui", [Microsoft.Web.WebView2.Core.CoreWebView2HostResourceAccessKind]::Allow)
                    $core.Settings.IsStatusBarEnabled = $false
                    $core.Settings.AreDevToolsEnabled = $false
                    WEBLOG "S8b attaching msg listener"
                    $core.add_WebMessageReceived({ param($ss, $ee)
                        try {
                            $mj = $ee.WebMessageAsJson
                            $m = $mj | ConvertFrom-Json
                            $cm = [string]$m.cmd
                            if ($cm -eq "diag") { try { [System.IO.File]::WriteAllText("$script:logDir\web_diag.log", $mj) } catch {} }
                            elseif ($cm -eq "hb") { }
                            else { Handle-WebCmd $cm ([string]$m.data) }
                        } catch {}
                    })
                    $core.add_NavigationStarting({ param($s5, $e5)
                        try {
                            $uri = [string]$e5.Uri
                            $script:lastNavUri = $uri
                            # bot 未启动时导航到它提供的页面（GAL 页）是**必然失败**的一次跳转：
                            # 拦下来 + 给可执行提示，用户就停在自己的主页上，不用去点那个回不来的错误页。
                            # 这里用 Test-TcpFast 直连（不读 120s 的探测缓存）——判据必须反映"此刻"，
                            # 否则刚点完「▶ 启动」的用户会被缓存里的旧结论拦住（那是新的坑）。
                            if ($uri -match "^(?i:https?)://(?:127\.0\.0\.1|localhost):8080/" -and -not (Test-TcpFast "127.0.0.1" 8080)) {
                                $e5.Cancel = $true
                                $script:navCanceled = $true
                                WEBLOG ("GAL 导航已拦下（bot 未启动）：" + $uri)
                                Show-Msg "机器人未启动：GAL 页要先让 bot 跑起来（顶栏「▶ 启动」，约 30-60 秒）"
                                return
                            }
                            if ($uri -match "[?&]cmd=([a-z]+)") {
                                $cm = $Matches[1]
                                $dv = ""
                                if ($uri -match "[?&]d=([^&]+)") { $dv = [System.Uri]::UnescapeDataString($Matches[1]) }
                                Handle-WebCmd $cm $dv
                            }
                        } catch {}
                    })
                    try {
                    } catch {}                    try { $core.Navigate((Get-SkinEntryUrl)); WEBLOG ("S9 nav called skin=" + (Split-Path (Get-SkinDir) -Leaf)) } catch { WEBLOG "S9 NAV ERR: $($_.Exception.Message)" }
                    $core.add_NavigationCompleted({ param($s2, $e2)
                        WEBLOG ("S10 nav completed ok=" + $e2.IsSuccess)
                        try {
                            # 主动拦下的一次导航（bot 未启动）不算失败：`OperationCanceled` 也会
                            # 以 IsSuccess=false 回来，不区分就会把用户刚停在的主页又刷一遍。
                            if ($script:navCanceled) { $script:navCanceled = $false; return }
                            if ($e2.IsSuccess) { $script:navFailCount = 0; return }
                            # 加载失败 → 回到皮肤入口。失败页是**死角**：它不走 state.json、也不认主页按钮，
                            # 用户会卡在那儿（"主页键被盖住"就是这么来的）。
                            # 回退目标就是皮肤入口，所以对入口自身的失败**不再重试**（否则 Navigate→失败→
                            # Navigate…… 无限循环）；连续失败 3 次也停手，改为把话说明白。
                            $bad = "$script:lastNavUri"
                            $entry = (Get-SkinEntryUrl)
                            if ($bad -and $bad -notlike "*$entry*" -and $script:navFailCount -lt 3) {
                                $script:navFailCount = $script:navFailCount + 1
                                Show-Msg "页面加载失败，已返回启动器主页：$bad"
                                try { $s2.Navigate($entry) } catch {}
                            } else {
                                Show-Msg "页面加载失败：$bad（启动器主页也打不开——检查 launcher\web 是否完整）"
                            }
                        } catch {}
                    })
                    $core.add_DocumentTitleChanged({ param($s4, $e4) try { [System.IO.File]::AppendAllText("$script:logDir\web_title.log", $e4.NewDocumentTitle + "`n") } catch {} })
                    $core.add_SourceChanged({ param($s3, $e3)
                        try {
                            $src = [string]$e3.Source
                            if ($src -match "\?cmd=([a-z]+)(?:&d=([^&]*))?") {
                                $cm2 = $Matches[1]
                                $dt2 = $Matches[2]
                                if ($dt2) { $dt2 = [System.Uri]::UnescapeDataString($dt2) }
                                Handle-WebCmd $cm2 $dt2
                            }
                        } catch {}
                    })
                    try { [System.IO.File]::WriteAllText("$script:logDir\web_started.log", (Get-Date).ToString("HH:mm:ss")) } catch {}
                    $script:wv = $script:wvCtl
                    $script:wvStartup = $true
                } catch { try { [System.IO.File]::WriteAllText("$script:logDir\webview2_error.log", $_.Exception.ToString()) } catch {} }
            }
        })
    } catch { try { [System.IO.File]::WriteAllText("$script:logDir\webview2_error.log", $_.Exception.ToString()) } catch {} }
}
Init-WebView

$topBar = $window.FindName("TopBarBorder")
if ($topBar) { $topBar.Add_MouseLeftButtonDown({ try { $window.DragMove() } catch {} }) }
BindClick "BtnHome" ({ Show-Page "home" })
BindClick "NavOpe"   ({ Show-OperaPage })   # 皮肤自带干员页就开皮肤的（manifest operaPage），否则框架原生页
BindClick "NavCfg"   ({ Show-Page "cfg" })
BindClick "NavPack"  ({ Show-Page "plugins" })
BindClick "NavState" ({ Show-Page "state" })
BindClick "NavLog"   ({ Show-Page "log" })
BindClick "BtnBack7" ({ Show-Page "home" })
BindClick "NavDeps"  ({ Show-Page "deps" })
BindClick "NavAbout" ({ Show-Page "about" })
BindClick "BtnDepRefresh" ({ $script:probeCache = @{}; Refresh-Deps })
BindClick "BtnDepOpenDir" ({ Open-DepTarget "dir" })
BindClick "BtnDepOpenSrc" ({ Open-DepTarget "src" })
BindClick "LstDeps" ({ $i = $window.FindName("LstDeps").SelectedIndex;
                        $fs = Get-Deps; if ($i -ge 0 -and $i -lt @($fs).Count) { Show-DepDetail $fs[$i] } })
BindClick "BtnBack1" ({ Show-Page "home" })
BindClick "BtnBack2" ({ Show-Page "home" })
BindClick "BtnBack3" ({ Show-Page "home" })
BindClick "BtnBack4" ({ Show-Page "home" })
BindClick "BtnBack6" ({ Show-Page "home" })
# 2026-09-12 审计 J 项删除：原先这里有四行 BindClick "BlockOpe/BlockCfg/BlockLog/BlockState"。
# 它们**永远不可能触发**——BindClick 走 $window.FindName（WPF 名字域），而 opera/cfg/log/state
# 那些块只存在于**皮肤网页**里，FindName 恒返回 $null（守卫 `if ($b)` 让它静默什么都不做）。
# 更糟的是它让人误以为"网页按钮是从这儿接的"。网页那侧的真实通道是 postMessage
# → 上面 `elseif ($cmd -eq "opera")` 那一串分支（换任何皮肤都走它）。
BindClick "BtnExit" ({
    # 2026-09-06 修复"关闭全部崩溃"：MessageBox 与 Close/Exit 竞态移除；步进停止，最后一步停定时器再关窗
    Show-Msg "正在退出并清空全部进程（含 LLM 引擎，显存释放）…"
    $script:exitAfterStop = $true
    Start-StepMachine (New-StopSteps) "⚠（正在停止全部…）" | Out-Null
})
# 顶栏启动/停止（2026-09-12）：**唯一**入口。
# 原先此处还并存一对 BindClick "BtnStart"/"BtnStop"——那两个名字只在皮肤网页里、WPF XAML 里
# 已不存在，所以同样是永远不触发的死绑定，还把同一段启动/停止逻辑**抄了第二份**
# （改一处忘另一处正是本项目最高频的 bug 类型）。J 项审计抓到后已删，只留这一份。
BindClick "BtnTopStart" ({
    if (Start-StepMachine (New-StartSteps) "⚠（正在启动全部…）") {
        Show-Msg "正在启动全部…（引擎就绪约 30-60 秒）"
    }
})
BindClick "BtnTopStop" ({
    if (Start-StepMachine (New-StopSteps) "⚠（正在停止全部…）") {
        Show-Msg "正在停止全部（含 LLM，显存释放）…"
    }
})
# 仅停机器人（2026-09-12 分层去重）：复用 Invoke-StopBotOnly（cmdline+端口双兜底，与 cmd=stopbot 同口径）。
# 补它的理由：这条能力原先只能由皮肤网页发出来——换个皮肤就没了，正是顶栏那对按钮当初要解决的同一问题。
BindClick "BtnStopBotOnly" ({ Show-Msg (Invoke-StopBotOnly) })
BindClick "BtnSetPersona" ({
    if ($script:selectedCard) {
        if (Set-OwnerPersona $script:selectedCard) {
            $window.FindName("personaMsg").Text = "已设为主用人设：" + $script:selectedCard + "（重启机器人后生效）"
            Update-Home
        } else { $window.FindName("personaMsg").Text = "写入失败（检查 data 目录权限）" }
    }
})
BindClick "BtnSaveCfg" ({
    $d = @{ llm = [bool]$window.FindName("chkLlm").IsChecked; voice = [bool]$window.FindName("chkVoice").IsChecked }
    # 2026-09-10 Phase2：皮肤选择写入 launcher.json 的 skin 键
    try {
        $sel = $window.FindName("CmbSkin")
        if ($sel -and $sel.SelectedItem) { $d["skin"] = [string]$sel.SelectedItem.Tag }
    } catch {}
    # 配置档必须**一起**写回：Set-LauncherCfg 是整体覆写，不带这两个键就把档抹掉了
    # （同一份文件多个写者 → 每个写者都要读-改-写，这是"改一处忘另一处"的高发面）
    try {
        $profKeep = Get-LlmProfiles
        $d["llmProfile"]  = [string]$profKeep.active
        $d["llmProfiles"] = @{ local = $profKeep.local; online = $profKeep.online }
    } catch {}
    # 界面语言同理：Set-LauncherCfg 整体覆写，不带 lang 键就把语言设置抹回默认（同一类坑）
    try { $d["lang"] = Get-UiLang } catch {}
    Set-LauncherCfg $d
    # 2026-09-11 热修六：模型与接口写入 LLM_*（core/llm.py 读取）
    # 2026-09-12 配置档修正：① 保存前把三格**回存活动档**（否则这次编辑只进 .env、切档即丢）；
    #   ② **本地档显式写空**（旧实现 `if ($u)` 只写非空 → 切回本地清不掉在线 URL，
    #      而 provider=local + 在线 URL 会让请求照样发到云端，静默且烧钱）。
    try {
        $provSel = $window.FindName("CmbProvider").SelectedItem
        $prov = if ($provSel) { [string]$provSel.Tag } else { "local" }
        $b = Get-LlmBoxes
        $u = $b.url; $m = $b.model; $k = $b.key
        if ($prov -ne "local" -and (-not $u -or -not $m)) {
            $window.FindName("txtCfgMsg").Text = "API 模式需要至少填写『接口地址 URL + 模型名』——模型段未保存"
        } else {
            Set-EnvValue "LLM_PROVIDER" $prov
            Set-EnvValue "LLM_BASE_URL" $u          # 空串＝清空（本地档正是靠这一步真正切回去）
            Set-EnvValue "LLM_MODEL"    $m
            if ($k) { Set-EnvValue "LLM_API_KEY" $k }
            $prof = Get-LlmProfiles
            $pkey = if ($prof.active -eq "online" -and $prov -ne "local") { "online" } else { "local" }
            $prof[$pkey] = @{ provider = $prov; url = $u; key = $k; model = $m }
            $prof.active = $pkey
            Set-LlmProfiles $prof
            if ($script:wv -and $script:wv.CoreWebView2) { Update-LlmProfileNote }
        }
    } catch {}
    $script:cfgNotes = @()
    # 2026-09-12 T5.1：身份区——SUPERUSERS（JSON 数组）+ OWNER_NICKNAME；换 QQ 需二次确认
    try {
        $qqBox = $window.FindName("TxtOwnerQq")
        $nickBox = $window.FindName("TxtOwnerNick")
        if ($qqBox -and $nickBox) {
            $qqNew = Normalize-Qq $qqBox.Text
            $nickNew = $nickBox.Text.Trim()
            $qqOld = Normalize-Qq (Get-OwnerQqEnv)
            if ($qqNew -and -not ($qqNew -match "^\d{5,12}$")) {
                $script:cfgNotes += "主人 QQ 只接受 5-12 位数字（SUPERUSERS 未保存，其它项已保存）"
            } else {
                # 空值一律不写：防误清空 SUPERUSERS 导致启动器解析失败回落 persona_select
                if ($qqNew -and $qqNew -ne $qqOld) {
                    $ck = $window.FindName("ChkOwnerQqConfirm")
                    if (-not ($ck -and $ck.IsChecked)) {
                        $script:cfgNotes += "换了主人 QQ：请先勾选上面的确认框（改 QQ = 换身份，必须同跑 uid 迁移工具）"
                    } else {
                        # 关键：写 JSON 数组，不写裸数字串——Get-FirstSuperuser 用 ConvertFrom-Json 解析
                        if (Set-EnvValue "SUPERUSERS" (Format-QqArray $qqNew)) {
                            $script:cfgNotes += "身份已保存：主人 QQ 已改为 $qqNew（JSON 数组格式）——务必先跑 qq-bot\tools\migrate_uid.py --from $qqOld --to $qqNew 迁移记忆库，再重启机器人"
                            if ($ck) { $ck.IsChecked = $false }
                        } else {
                            $script:cfgNotes += "主人 QQ 写入失败（检查 qq-bot\.env 权限/占用）"
                        }
                    }
                }
                if ($nickNew -and $nickNew -ne (Get-EnvValue "OWNER_NICKNAME")) {
                    if (Set-EnvValue "OWNER_NICKNAME" $nickNew) { $script:cfgNotes += "身份已保存：主人昵称 = $nickNew（重启机器人后生效）" }
                }
            }
        }
    } catch {}
    # 2026-09-12 T4.2：生成与思考——写 data\think_mode.json（自决=删键；等价 brain.set_think_mode）
    try {
        $cth2 = $window.FindName("CmbThink")
        if ($cth2 -and $cth2.SelectedItem) {
            $wantT = [string]$cth2.SelectedItem.Tag            # auto / on / off
            $uid = Normalize-Qq $window.FindName("TxtOwnerQq").Text
            if (-not $uid) { $uid = [string](Get-FirstSuperuser) }
            if (-not $uid) {
                $script:cfgNotes += "思考模式未保存：无主人 uid（SUPERUSERS 为空且 persona_select.json 无记录）"
            } else {
                $tf = "$root\data\think_mode.json"
                $td = Get-CfgJsonFile $tf
                $cur = "auto"
                if ($td.ContainsKey($uid)) {
                    $cv = [string]$td[$uid]
                    if ($cv -eq "on" -or $cv -eq "off") { $cur = $cv }
                }
                if ($cur -ne $wantT) {
                    if ($wantT -eq "auto") { $td.Remove($uid) | Out-Null } else { $td[$uid] = $wantT }
                    if (Write-CfgJsonFile $tf $td) { $script:cfgNotes += "思考模式已写入：" + $wantT + "（重启机器人后生效）" }
                    else { $script:cfgNotes += "思考模式写入失败（检查 data 目录权限）" }
                }
            }
        }
    } catch {}
    # 2026-09-09 新增：直播开关 + 聊天模式跨重启保留开关写入 E:\robot\qq-bot\.env（true/false，与 LIVE_* 同口径）
    $v1 = if ($window.FindName("chkLiveDanmaku").IsChecked) { "true" } else { "false" }
    $v2 = if ($window.FindName("chkLiveAudio").IsChecked) { "true" } else { "false" }
    $v3 = if ($window.FindName("chkChatPersist").IsChecked) { "true" } else { "false" }
    $okEnv = (Set-EnvValue "LIVE_DANMAKU_ENABLED" $v1) -and (Set-EnvValue "LIVE_AUDIO_PLAYBACK" $v2) -and (Set-EnvValue "WEBGAL_CHAT_PERSIST" $v3)
    $cfgMsg = if ($okEnv) { "已保存。开关项重启机器人后生效。" } else { "启动选项已保存；开关写入失败（检查 qq-bot\.env 权限/占用）" }
    if ($script:cfgNotes -and $script:cfgNotes.Count) { $cfgMsg = ($script:cfgNotes -join " ｜ ") }
    $window.FindName("txtCfgMsg").Text = $cfgMsg
    # 2026-09-10 Phase2：皮肤切换即时生效——重导航 web 视图（state.json 由 Push-Web 随后写入新皮肤目录）
    try {
        if ($script:wv -and $script:wv.CoreWebView2) { $script:wv.CoreWebView2.Navigate((Get-SkinEntryUrl)) }
    } catch {}
})
# 2026-09-12 T5.2：预设只填 base_url + 建议模型名（Key 留空自填），三格仍可自由编辑
BindClick "BtnApplyPreset" ({
    try {
        $pr = $window.FindName("CmbProvider").SelectedItem
        if (-not $pr) { return }
        $presets = Get-ProviderPresets
        $nm = [string]$pr.Content
        if (-not $presets.ContainsKey($nm)) { return }
        $p = $presets[$nm]
        if ($p["url"]) { $window.FindName("TxtApiUrl").Text = [string]$p["url"] }
        if ($p["model"]) { $window.FindName("TxtModel").Text = [string]$p["model"] }
        # 预设说明落到模型名下面那行（含"型号名会变、点拉取型号现取"的口径）
        $nt = $window.FindName("txtModelNote")
        if ($nt) { $nt.Text = "$([string]$p["note"])" }
        $im3 = $window.FindName("txtCfgMsg")
        $msg = "已套用预设：" + $nm
        if ($p["url"]) { $msg += "（URL 已填" + $(if ($p["model"]) { "、型号已给一个起点" } else { "、**型号留空**" }) + "）" } else { $msg += "（该项不指定地址，请自填接口地址 URL）" }
        $msg += "。型号名会过期：填好 Key 后点「拉取型号」按端点现取最稳；别忘了「保存 SAVE」写入 .env。"
        $im3.Text = $msg
    } catch {}
})
# 配置档（2026-09-12）：切换档 = 只改界面（把该档的值载进三格）；激活 = 写 .env；存入 = 三格存回档。
BindSelect "CmbLang" ({
    # 语言切换（2026-09-12 全局语言切换）：存 launcher.json → 立刻在界面树上换文 → 重刷当前页
    # （列表/提示是"生成时"的中文，必须重生成才换得了）→ Push-Web 把 lang 推给皮肤页与 GAL 页。
    try {
        if ($script:langGuard) { return }        # Refresh-Cfg 的程序性选中不当作"用户切换"
        $sel = $window.FindName("CmbLang").SelectedItem
        if (-not $sel) { return }
        $lang = Set-UiLang ([string]$sel.Tag)
        Apply-UiLang $lang
        try { Refresh-Cfg } catch {}
        try { Push-Web } catch {}
        $m = $window.FindName("txtCfgMsg")
        if ($m) {
            $m.Text = $(if ($lang -eq "en") {
                "UI language: English — saved to data\launcher.json; the skin page and the GAL page follow (they re-read state.json / the URL parameter)."
            } else {
                "界面语言：中文 —— 已写入 data\launcher.json；皮肤页与 GAL 页随后跟随（它们分别读 state.json 与 URL 参数）。"
            })
        }
    } catch {}
})
BindSelect "CmbProfile" ({
    try {
        if ($script:llmProfileGuard) { return }     # 程序性选中（Refresh-Cfg 回显）不当作"用户切换"
        $sel = $window.FindName("CmbProfile").SelectedItem
        if (-not $sel) { return }
        $key = [string]$sel.Tag
        Load-LlmProfileToBoxes $key
        Update-LlmProfileNote
        $im = $window.FindName("txtCfgMsg")
        if ($im) {
            $im.Text = "已载入【" + $(if ($key -eq "local") { "本地" } else { "在线" }) + "】档的值到三格（**尚未写入 .env**）。" +
                       "点「激活此档 ↦ 写入 .env」才生效（重启 bot 后生效）。"
        }
    } catch {}
})
BindClick "BtnSaveProfile" ({
    try {
        $sel = $window.FindName("CmbProfile").SelectedItem
        if (-not $sel) { return }
        $key = [string]$sel.Tag
        $b = Get-LlmBoxes
        $prof = Get-LlmProfiles
        $prov = if ($key -eq "local") { "local" } else { "openai_compat" }
        $prof[$key] = @{ provider = $prov; url = $b.url; key = $b.key; model = $b.model }
        Set-LlmProfiles $prof
        Update-LlmProfileNote
        $im = $window.FindName("txtCfgMsg")
        if ($im) { $im.Text = "三格已存入【" + $(if ($key -eq "local") { "本地" } else { "在线" }) + "】档（launcher.json；**未动 .env**）。" }
    } catch {}
})
BindClick "BtnActivateProfile" ({
    try {
        $sel = $window.FindName("CmbProfile").SelectedItem
        if (-not $sel) { return }
        $key = [string]$sel.Tag
        $prof = Get-LlmProfiles
        $b = Get-LlmBoxes
        # 激活前先把三格收进档里：用户很可能在界面上改过值就直接点激活——不先收，激活的就是旧值（静默失效）
        $prof[$key] = @{ provider = $(if ($key -eq "local") { "local" } else { "openai_compat" }); url = $b.url; key = $b.key; model = $b.model }
        $prof.active = $key
        Set-LlmProfileToEnv $prof[$key]
        Set-LlmProfiles $prof
        Refresh-Cfg
        $im = $window.FindName("txtCfgMsg")
        if ($im) {
            $im.Text = "已激活【" + $(if ($key -eq "local") { "本地" } else { "在线" }) + "】档：.env 的 LLM_PROVIDER / LLM_BASE_URL / LLM_MODEL / LLM_API_KEY 已按档写入" +
                       $(if ($key -eq "local") { "（URL 与模型名写空＝走本地回落链：11434 + gemma）" } else { "" }) + "；**重启 bot 生效**。"
        }
    } catch {}
})
# 「拉取型号」（2026-09-12）：按 URL+Key 向端点要模型清单，填进下拉。选中即写入模型名。
BindClick "BtnFetchModels" ({
    try {
        $r = Get-ModelIds
        $cb = $window.FindName("CmbModels")
        $im3 = $window.FindName("txtCfgMsg")
        if (-not $r.ok) {
            if ($im3) { $im3.Text = [string]$r.err }
            return
        }
        if ($cb) {
            $cb.Items.Clear()
            foreach ($id in $r.ids) { $cb.Items.Add($id) | Out-Null }
            if ($cb.Items.Count -gt 0) { $cb.SelectedIndex = 0 }
        }
        if ($im3) { $im3.Text = "已取到 $($r.ids.Count) 个型号（下拉里选一个即写入模型名）：" + (($r.ids | Select-Object -First 6) -join "、") + $(if ($r.ids.Count -gt 6) { " …" } else { "" }) }
    } catch {}
})
# 下拉选中 → 写入模型名（单一事实：下拉只是"取回来的清单"，最终保存的仍是 TxtModel 的值）
BindSelect "CmbModels" ({
    try {
        $cb = $window.FindName("CmbModels")
        if ($cb -and $cb.SelectedItem) { $window.FindName("TxtModel").Text = [string]$cb.SelectedItem }
    } catch {}
})
BindClick "BtnRefreshLog" ({ Refresh-Log })
# 日志页视图开关（2026-09-12 §7.6）：勾选状态一变就重画（Add_Click 在复选框状态翻转之后触发）
BindClick "ChkLogFull" ({ Refresh-Log })
BindClick "BtnBack5" ({ Show-Page "home" })
BindClick "BtnOpenPlugins" ({ Show-Page "plugins" })
BindClick "BtnPackRefresh" ({ Refresh-Plugins })
# 内容包页点选 → 右栏详情（行号→条目靠 Refresh-Plugins 建的 $script:packRows 映射，
# 因为列表里有分组标题，行号不等于条目号）。装完之后顺手刷一次详情，避免"装完右栏还是旧的"。
BindClick "LstPacks" ({ $i = $window.FindName("LstPacks").SelectedIndex
                       if ($i -ge 0 -and $i -lt @($script:packRows).Count) { Show-PackDetail $script:packRows[$i] } })
BindClick "BtnPackFolder" ({ Install-PackFolderUI })
BindClick "BtnPackZip" ({ Install-PackZipUI })

# 初始装配（延迟 600ms，避免窗口/WebView2 竞态）
$bootTimer = New-Object System.Windows.Threading.DispatcherTimer
$bootTimer.Interval = [TimeSpan]::FromMilliseconds(600)
$bootTimer.Add_Tick({
    $bootTimer.Stop()
    try { Update-Home } catch {}
    try { if ($Mode -like "view-*") { Show-Page ($Mode -replace "^view-", "") } else { Show-Page "home" } } catch {}
    try { Refresh-Overview } catch {}

    try { Refresh-Cfg } catch {}
    try { Refresh-Log } catch {}
})
$bootTimer.Start()

# 顶部时钟（秒级本地刷新）
$clockT = New-Object System.Windows.Threading.DispatcherTimer
$clockT.Interval = [TimeSpan]::FromSeconds(1)
$clockT.Add_Tick({ try { $cl = $window.FindName("topClock"); if ($cl) { $cl.Text = (Get-Date).ToString("HH:mm:ss") } } catch {} })
$clockT.Start()
# 轮询（5 秒；try/catch 保证任何异常不中断刷新）
$timer = New-Object System.Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromSeconds(3)
$timer.Add_Tick({
    if ($script:busy) { return }  # 2026-09-06：步进操作期间跳过轮询（防叠加卡顿）
    try { Refresh-Overview } catch {}

    $pg = $window.FindName("PageState")
    if ($pg -and $pg.Visibility.ToString() -eq "Visible") { try { Refresh-State } catch {} }
    $lg = $window.FindName("PageLog")
    if ($lg -and $lg.Visibility.ToString() -eq "Visible") { try { Refresh-Log $true } catch {} }
})
$timer.Start()

# 界面语言：启动即应用一次 + 每 2 秒补翻一次（2026-09-12 全局语言切换）
# 为什么需要"补翻"：很多文案是**运行期生成**的（Show-Msg、列表项、状态行），它们出现时已经是中文；
# 在这一层收口就不必去改 30 多处调用点——而且**漏翻会留在界面上看得见**，不会藏进代码里。
$script:uiLang = Get-UiLang
$script:uiOrig = @{}
$script:langGuard = $false
$langT = New-Object System.Windows.Threading.DispatcherTimer
$langT.Interval = [TimeSpan]::FromSeconds(2)
$langT.Add_Tick({ try { Apply-UiLang $script:uiLang } catch {} })
$langT.Start()

if ($Mode -like "view-*") { try { Show-Page ($Mode -replace "^view-", "") } catch {} }
$window.ShowDialog() | Out-Null
[System.Environment]::Exit(0)
