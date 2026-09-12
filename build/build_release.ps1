# ============================================================================
# QQAI 陪伴框架 构建发行脚本（Phase 4 · 2026-09-10）
#
# 裁决背景：封闭核心 + 开放 SDK。
#   - SDK 包（qqai-sdk-<yyyyMMdd>.zip）：插件接口规范 / 皮肤包格式 / 示例内容，
#     公开分发，零第三方 IP（内置自检强制）。
#   - Core 包（qqai-core-<yyyyMMdd>.zip）：bot 源码或 pyarmor 混淆产物，封闭发行。
#   - Launcher 件（-Launcher）：exe + README，可选。
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File build\build_release.ps1
#       （缺省 = Sdk + Core）
#   可选参数：-Out <目录>（默认 E:\robot\release）、-Sdk、-Core、-Launcher、-LocalContent
#
# 纪律：
#   - 幂等：staging 先清后建；Out 目录自动创建。
#   - SDK 自检违例（图片 / data 状态文件 / IP 字符串）→ 构建失败退出（exit 1）。
#   - pyarmor 缺失 → 自动尝试安装（限时 5 分钟）→ 仍无则降级为源码包 +
#     ENCRYPTED_SKIPPED.txt，不阻塞构建。
#   - -LocalContent：构建后调用 qq-bot\tools\export_content_packs.py 在
#     release\local-content\ 出**四个**私有内容分发件（方舟皮肤 / legacy 卡 / GAL 舞台包 /
#     R18 玩法配置；gitignored，不进 SDK/公开面）。存在才调，失败不阻塞主构建。
# ============================================================================

param(
    # 2026-09-12 T7.3：缺省改为"由脚本自身位置推导"（见下方 $Out 归一化）——换盘/换目录安装不再失效。
    # 显式传 -Out 仍优先；推导失败回落 E:\robot\release（历史行为）。
    [string]$Out = "",
    [switch]$Sdk,
    [switch]$Core,
    [switch]$Launcher,
    [switch]$LocalContent
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

# ---------- 路径常量 ----------
$root    = Split-Path -Parent $PSScriptRoot          # 仓库根（本脚本位于 build\）
$staging = Join-Path $PSScriptRoot "staging"         # 装配临时目录（gitignored）
$stamp   = Get-Date -Format "yyyyMMdd"
$venvDir = Join-Path $root "qq-bot\.venv"
$venvPy  = Join-Path $venvDir "Scripts\python.exe"

# ---------- -Out 归一化（2026-09-12 T7.3） ----------
# 未显式传 -Out 时按仓库根推导；推导结果不成立（父目录不含 qq-bot）则回落 E:\robot\release。
if (-not $Out) {
    $derived = Join-Path $root "release"
    if ($derived -and (Test-Path (Join-Path $root "qq-bot"))) { $Out = $derived }
    else { $Out = "E:\robot\release" }
    Write-Host ("[路径] -Out 未指定，推导为 " + $Out)
}

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$Utf8Bom   = [System.Text.Encoding]::UTF8            # 带 BOM（人读 md 用）

# ---------- 缺省开关：Sdk + Core ----------
if (-not $Sdk -and -not $Core -and -not $Launcher) {
    $Sdk = $true
    $Core = $true
}

function Step([string]$Msg) { Write-Host "`n========== $Msg ==========" -ForegroundColor Cyan }

# 必需文件拷贝：源不存在直接中止构建（宁缺勿滥，防静默缺件）
# 注意 Copy-Item 语义：目标目录已存在时，目录源会被拷成 dst\srcName（嵌套）。
# 故目录源走"目标不存在→整体复制为目标"分支，文件源走"先建目标目录再拷入"分支。
function Assert-Copy([string]$Src, [string]$DstDir) {
    if (-not (Test-Path -LiteralPath $Src)) { throw "构建中止：缺少必需文件/目录 $Src" }
    if (Test-Path -LiteralPath $Src -PathType Container) {
        if (-not (Test-Path -LiteralPath $DstDir)) {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $DstDir) | Out-Null
            Copy-Item -LiteralPath $Src -Destination $DstDir -Recurse -Force   # dst 即源的整体副本
        } else {
            Copy-Item -LiteralPath $Src -Destination $DstDir -Recurse -Force
        }
    } else {
        New-Item -ItemType Directory -Force -Path $DstDir | Out-Null
        Copy-Item -LiteralPath $Src -Destination $DstDir -Recurse -Force
    }
}

# ------------------------------------------------------------------ 幂等准备
Step "[0/6] 幂等准备：清 staging / 建 Out"
if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
New-Item -ItemType Directory -Force -Path $staging | Out-Null
if (-not (Test-Path -LiteralPath $Out)) { New-Item -ItemType Directory -Force -Path $Out | Out-Null }

# ------------------------------------------------------------------ 版本提取
$packsPy = Join-Path $root "qq-bot\core\packs.py"
if (-not (Test-Path -LiteralPath $packsPy)) { throw "构建中止：缺少 $packsPy" }
$verMatch = Select-String -LiteralPath $packsPy -Pattern 'CORE_API_VERSION\s*=\s*"([^"]+)"'
if (-not $verMatch) { throw "构建中止：无法从 core\packs.py 提取 CORE_API_VERSION" }
$coreApi = $verMatch.Matches[0].Groups[1].Value
Write-Host "CORE_API_VERSION = $coreApi"

# 加密方式记录（写 manifest 用）：pyarmor / source-fallback
$encMode = "unknown"

# ============================================================================
# SDK 包
# ============================================================================
$sdkZipPath = $null
if ($Sdk) {
    Step "[1/6] 装配 SDK 包 → build\staging\sdk\"
    $sdkDir = Join-Path $staging "sdk"

    # --- 公开物 1：接口契约 + MOD 开发指南 + 皮肤包规范 + 第三方清单 ---
    # 2026-09-12 文档精简：契约地位由 PLUGIN_SDK.md 移交 docs\接口文档.md，
    # 上手教程由 GETTING_STARTED.md 移交 docs\MOD开发指南.md（旧两份仍留在仓内作历史，但不进 SDK）。
    Assert-Copy (Join-Path $root "docs\接口文档.md")               (Join-Path $sdkDir "docs")
    Assert-Copy (Join-Path $root "docs\MOD开发指南.md")            (Join-Path $sdkDir "docs")
    Assert-Copy (Join-Path $root "docs\皮肤包接口规范-v1.md")     (Join-Path $sdkDir "docs")
    Assert-Copy (Join-Path $root "docs\THIRD_PARTY.md")           (Join-Path $sdkDir "docs")

    # 装配时脱敏：docs\皮肤包接口规范-v1.md 的示例段含 IP 名（Phase3 源文件遗留，
    # 本脚本无权改源）。拷入 SDK 前做字符串替换；源文件修正后此表应清空。
    # 2026-09-10 盲审修复：needle 表扩词后，脱敏表须与 needle 表同步覆盖
    # （源文件 manifest 示例的 title 字段含 "明日方舟"，补条目防自检违例）。
    $specDst = Join-Path $sdkDir "docs\皮肤包接口规范-v1.md"
    $specTxt = [System.IO.File]::ReadAllText($specDst, $Utf8Bom)
    $sanitizeMap = [ordered]@{
        "阿米娅"   = "示例角色"
        "普瑞赛斯" = "示例角色乙"
        "罗德岛"   = "示例组织"
        "明日方舟" = "示例皮肤"
        "ARKNIGHTS" = "EXAMPLE-UNI"
    }
    $sanitized = $false
    foreach ($k in $sanitizeMap.Keys) {
        if ($specTxt.Contains($k)) { $specTxt = $specTxt.Replace($k, $sanitizeMap[$k]); $sanitized = $true }
    }
    if ($sanitized) {
        [System.IO.File]::WriteAllText($specDst, $specTxt, $Utf8Bom)
        Write-Host "  [脱敏] 皮肤包接口规范-v1.md 示例段 IP 名已替换（源文件遗留项，见决策日志 Phase 4）" -ForegroundColor Yellow
    }

    # --- 公开物 2：启动器 Web 面（generic 皮肤 + 主页 + GAL 客户端） ---
    $web = Join-Path $sdkDir "web"
    foreach ($f in @("index.html", "home.css", "app.js", "gal.html", "gal.js", "gal.css")) {
        Assert-Copy (Join-Path $root "launcher\web\$f") $web
    }
    Assert-Copy (Join-Path $root "launcher\web\skins\generic") (Join-Path $web "skins\generic")

    # --- 公开物 3：示例内容包（robot-pack-v1 活示例，零 IP） ---
    # ★ 2026-09-12 记：为什么这里只拷 example.sakura、不拷 example.stage（舞台包示例）——
    #   本 SDK 的发行纪律是**零图片**（下面 [2/6] 的自检会把任何 .png/.webp 判成违例，
    #   原因见那里的注释：generic 皮肤本来就无图）。而舞台包天然由**图片**构成（bg/ 背景 +
    #   sprites/diff/ 差分），所以它进不了 SDK 的发行面。
    #   舞台包活示例随 **Core 发行**走：`qq-bot/packs/` 是整目录拷贝（见本文件 Core 件那段），
    #   Core 里能直接看到 example.stage 的清单形状与三张占位背景。
    #   要改这条决定，先改自检策略再改这里——别只删自检规则。
    Assert-Copy (Join-Path $root "qq-bot\packs\example.sakura") (Join-Path $sdkDir "packs\example.sakura")

    # --- 卡格式参考（data\ 为运行时目录，无论 git 状态直接从工作区拷贝） ---
    Assert-Copy (Join-Path $root "qq-bot\data\personas\_template.json") (Join-Path $sdkDir "personas")
    Assert-Copy (Join-Path $root "qq-bot\data\personas\README.md")      (Join-Path $sdkDir "personas")

    # --- 环境样例 ---
    Assert-Copy (Join-Path $root "qq-bot\.env.example") $sdkDir

    # --- 社区加卡工具（robot-pack-v1：包卡 → data/cards.json 导出；运行需完整安装根，用法见 GETTING_STARTED） ---
    Assert-Copy (Join-Path $root "qq-bot\tools\export_cards.py") (Join-Path $sdkDir "tools")

    # --- 发行条款（根 LICENSE，T2 产物） ---
    $licenseSrc = Join-Path $root "LICENSE"
    if (Test-Path -LiteralPath $licenseSrc) { Assert-Copy $licenseSrc $sdkDir }

    # --- RELEASE-NOTES.md 生成 ---
    $notes = @"
# QQAI 陪伴框架 发行说明（$stamp）

- 核心接口版本：CORE_API_VERSION = $coreApi
- 构建日期：$stamp（$(Get-Date -Format "yyyy-MM-dd"))
- 发行模式：封闭核心 + 开放 SDK（见 LICENSE 发行条款）

## 本包组件清单（qqai-sdk-$stamp.zip）

| 路径 | 内容 |
|---|---|
| docs/接口文档.md | 接口契约：内容包 / 皮肤包 / GAL 协议 / 代码扩展 / 沙箱权限 |
| docs/MOD开发指南.md | MOD 开发指南：五种 mod（角色/音色/舞台/皮肤/工具包）从零到能用 |
| docs/皮肤包接口规范-v1.md | 启动器皮肤包格式 v1（深层细节） |
| docs/THIRD_PARTY.md | 第三方组件及许可清单 |
| web/ | 启动器 Web 面：generic 默认皮肤 + 主页 + GAL 客户端（零图片素材） |
| packs/example.sakura/ | robot-pack-v1 活示例内容包（原创角色，CC0） |
| （**没有** packs/example.stage/） | 舞台包（type=gal）天然由图片构成，而本包发行面**零图片**（见自检声明）。舞台包活示例随 **Core 发行**：解包后看 qq-bot/packs/example.stage/（清单形状 + 三张占位背景） |
| personas/ | 卡格式参考：_template.json + README |
| tools/export_cards.py | 内容包卡 → data/cards.json 导出工具（社区加卡） |
| .env.example | bot 环境配置样例 |
| LICENSE | QQAI 陪伴框架 发行条款 |

## 自检声明

本包经构建自检：不含 data/quotes.json、data/cards.json 等运行时状态文件；
不含任何 .png/.jpg/.webp 图片；全文扫描（6 个第三方 IP 词，大小写不敏感，
含无扩展名文件）无命中；skins/arknights 等目录名技术性引用不属 IP 扫描对象。
"@
    [System.IO.File]::WriteAllText((Join-Path $sdkDir "RELEASE-NOTES.md"), $notes, $Utf8Bom)
    Write-Host "  RELEASE-NOTES.md 已生成（core_api=$coreApi）"

    # ------------------------------------------------------------------
    # SDK 自检（违例即失败退出）：发行面零 IP 铁律
    # ------------------------------------------------------------------
    Step "[2/6] SDK 自检（图片 / data 状态文件 / IP 字符串）"
    $violation = @()

    # (1) 任何图片禁入（generic 皮肤本来就无图）
    $imgExt = @(".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".ico")
    foreach ($f in (Get-ChildItem -LiteralPath $sdkDir -Recurse -File)) {
        if ($imgExt -contains $f.Extension.ToLower()) {
            $violation += ("图片违例: " + $f.FullName.Substring($sdkDir.Length + 1))
        }
    }

    # (2) 运行时状态文件禁入（data\quotes.json / data\cards.json）
    #     注：packs\*\quotes.json 是 robot-pack-v1 规范允许的包内台词文件，不受限
    foreach ($f in (Get-ChildItem -LiteralPath $sdkDir -Recurse -File)) {
        if ($f.FullName -match "[\\/]data[\\/](quotes|cards)\.json$") {
            $violation += ("data 状态文件违例: " + $f.FullName.Substring($sdkDir.Length + 1))
        }
    }

    # (3) 文本文件 IP 字符串扫描（2026-09-10 盲审修复强化）：
    #     · needle 表=6 个第三方 IP 中文名（阿米娅/普瑞赛斯/罗德岛/停云/明日方舟/凯尔希——
    #       2026-09-10 三轮盲审 Phase5 补词：kaltsit 卡中文名，cards.json 运行态已有、
    #       发行面同步纳入自检）。
    #     · 取舍说明：刻意不含拉丁词 "arknights"——gal.js 等公开面存在
    #       skins/arknights 虚拟主机路径引用，属皮肤目录名的技术性引用（合法），
    #       非 IP 文本；IP 语义由中文名 "明日方舟" 承载，原大写 "ARKNIGHTS"
    #       needle 随之退役（装配脱敏表中该词仍替换，双保险）。
    #     · 比对大小写不敏感（OrdinalIgnoreCase）——对现有中文 needle 无差，
    #       但防未来拉丁词 needle 因大小写变体漏检。
    #     · 扫描对象纳入无扩展名文件（如随包 LICENSE）；packs\*\quotes.json
    #       等规范允许的包内文本照扫不豁免。
    $needles = @("阿米娅", "普瑞赛斯", "罗德岛", "停云", "明日方舟", "凯尔希")
    $textExt = @(".md", ".js", ".css", ".html", ".json", ".txt", ".ps1", ".example")
    foreach ($f in (Get-ChildItem -LiteralPath $sdkDir -Recurse -File)) {
        $ext = $f.Extension.ToLower()
        if (($ext -ne "") -and ($textExt -notcontains $ext)) { continue }   # 无扩展名文件（LICENSE 等）纳入扫描
        $content = [System.IO.File]::ReadAllText($f.FullName, $Utf8Bom)
        foreach ($n in $needles) {
            if ($content.IndexOf($n, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
                $violation += ("IP 字符串 `"$n`" 违例: " + $f.FullName.Substring($sdkDir.Length + 1))
            }
        }
    }

    # (4) 控制字符 / TAB 扫描（2026-09-12 新增，被一次真实事故逼出来）：
    #     RELEASE-NOTES 里写了 markdown 反引号包 type=gal——而这是**双引号 here-string**，
    #     反引号是 PowerShell 的转义符：`` `t `` 被吃成一个 TAB，生成物里变成"（<TAB>ype=gal）"。
    #     语法合法 → 构建成功 → 前三项自检全过，只有人眼看输出才发现。
    #     本项目历史上还发生过 0x0B（竖制表符）冒充路径里的字母 v 的事故（当时 grep 都搜不到）。
    #     规则：.md/.json/.ps1/.py/.txt/.example 禁 TAB 与其它 C0 控制字符（缩进一律空格；
    #     要写字面 TAB 请用转义序列，别贴真字符）。.js/.css/.html 只禁非 TAB 的控制字符
    #     （网页前端缩进可能本就用 TAB，那是它的风格，不算缺陷）。
    $tabForbidden = @(".md", ".json", ".ps1", ".py", ".txt", ".example")
    $ctlBad = @()
    foreach ($f in (Get-ChildItem -LiteralPath $sdkDir -Recurse -File)) {
        $ext = $f.Extension.ToLower()
        if (($ext -ne "") -and ($textExt -notcontains $ext)) { continue }
        $txt = [System.IO.File]::ReadAllText($f.FullName, $Utf8Bom)
        for ($i = 0; $i -lt $txt.Length; $i++) {
            $ch = [int]$txt[$i]
            $isTab = ($ch -eq 9)
            $isOtherCtl = ($ch -lt 32 -and $ch -ne 10 -and $ch -ne 13 -and $ch -ne 9)
            if ($isOtherCtl -or ($isTab -and ($tabForbidden -contains $ext))) {
                $ctlBad += ("控制字符违例(0x{0:X2}): {1}" -f $ch, $f.FullName.Substring($sdkDir.Length + 1))
                break
            }
        }
    }
    $violation += $ctlBad

    if ($violation.Count -gt 0) {
        Write-Host "SDK 自检失败（$($violation.Count) 处违例）：" -ForegroundColor Red
        $violation | ForEach-Object { Write-Host "  [X] $_" -ForegroundColor Red }
        throw "SDK 自检失败：发行面零 IP 铁律被违反，拒绝出包"
    }
    Write-Host "  [OK] 自检通过：0 图片 / 0 data 状态文件 / 0 IP 字符串 / 0 控制字符（6 词×大小写不敏感×含无扩展名文件，$((Get-ChildItem -LiteralPath $sdkDir -Recurse -File).Count) 个文件；skins/arknights 等目录名技术性引用不属 IP 扫描对象）" -ForegroundColor Green

    # --- 压缩 ---
    $sdkZipPath = Join-Path $Out "qqai-sdk-$stamp.zip"
    if (Test-Path -LiteralPath $sdkZipPath) { Remove-Item -LiteralPath $sdkZipPath -Force }
    Compress-Archive -Path (Join-Path $sdkDir "*") -DestinationPath $sdkZipPath -CompressionLevel Optimal
    Write-Host "  SDK 包已生成：$sdkZipPath"
}

# ============================================================================
# Core 包
# ============================================================================
$coreZipPath = $null
$encSkippedReason = $null
if ($Core) {
    Step "[3/6] Core 包：检测加密工具（pyarmor）"
    $coreDir = Join-Path $staging "core"
    $coreBot = Join-Path $coreDir "qq-bot"      # zip 根下的 bot 目录（对齐仓库布局）
    $pyarmor = $null

    $cmd = Get-Command pyarmor -ErrorAction SilentlyContinue
    if ($cmd) { $pyarmor = $cmd.Source }
    elseif (Test-Path -LiteralPath (Join-Path $venvDir "Scripts\pyarmor.exe")) {
        $pyarmor = Join-Path $venvDir "Scripts\pyarmor.exe"
    }

    # 未装 → 尝试 pip 安装到 venv（限时 5 分钟，超时放弃）
    if (-not $pyarmor) {
        Write-Host "  未检测到 pyarmor，尝试 pip 安装到 venv（限时 5 分钟）..."
        if (Test-Path -LiteralPath $venvPy) {
            $pipOut = Join-Path $staging "pip_pyarmor.log"
            $pipErr = Join-Path $staging "pip_pyarmor.err.log"
            $proc = Start-Process -FilePath $venvPy `
                -ArgumentList @("-m", "pip", "install", "--disable-pip-version-check", "pyarmor") `
                -NoNewWindow -PassThru -RedirectStandardOutput $pipOut -RedirectStandardError $pipErr
            if ($proc.WaitForExit(300000)) {
                if (Test-Path -LiteralPath (Join-Path $venvDir "Scripts\pyarmor.exe")) {
                    $pyarmor = Join-Path $venvDir "Scripts\pyarmor.exe"
                    Write-Host "  [OK] pyarmor 已装入 venv" -ForegroundColor Green
                } else {
                    $encSkippedReason = "pip install pyarmor 失败（退出码 $($proc.ExitCode)，详见构建日志）"
                }
            } else {
                try { $proc.Kill() } catch { }
                $encSkippedReason = "pip install pyarmor 超时（>5 分钟），已中止安装进程"
            }
        } else {
            $encSkippedReason = "未找到 venv（qq-bot\.venv），无法尝试安装 pyarmor"
        }
    }

    $encryptedOk = $false
    if ($pyarmor) {
        Write-Host "  pyarmor: $pyarmor"
        Write-Host "  对 bot.py + core/ agent/ plugins/ harness/ 执行混淆（排除 tests/）..."
        # 注意：pyarmor 向 stderr 打 INFO 日志；PS5.1 下 `2>&1` + EAP=Stop 会把
        # stderr 行升级为终止错误——故经 cmd /c 合并重定向到日志，$LASTEXITCODE 取真实退出码。
        $obfLog = Join-Path $staging "pyarmor_gen.log"
        $obfCmd = "`"$pyarmor`" gen --recursive --output `"$coreBot`" bot.py core agent plugins harness > `"$obfLog`" 2>&1"
        Push-Location (Join-Path $root "qq-bot")     # 相对入口（bot.py/core/...）以 qq-bot 为根
        try {
            cmd /c $obfCmd
            $obfExit = $LASTEXITCODE
        } finally { Pop-Location }
        Get-Content -LiteralPath $obfLog -ErrorAction SilentlyContinue |
            Select-Object -Last 4 | ForEach-Object { Write-Host "    $_" }

        if ($obfExit -ne 0) {
            # 真实失败原因取日志尾部（如 trial 版 "out of license"）
            $errTail = (Get-Content -LiteralPath $obfLog -ErrorAction SilentlyContinue |
                Select-Object -Last 3) -join " / "
            Write-Host "  [X] pyarmor gen 失败（退出码 $obfExit）：$errTail" -ForegroundColor Yellow
            $encSkippedReason = "pyarmor gen 退出码 $obfExit：$errTail"
        } else {
            # sanity：compileall 纯语法层验证（真实启动验证留用户晨检：依赖完整 venv+引擎）
            Write-Host "  混淆 sanity：python -m compileall（纯语法层）..."
            $ccLog = Join-Path $staging "compileall.log"
            $ccCmd = "`"$venvPy`" -m compileall -q `"$coreBot`" > `"$ccLog`" 2>&1"
            cmd /c $ccCmd
            $ccExit = $LASTEXITCODE
            Get-Content -LiteralPath $ccLog -ErrorAction SilentlyContinue |
                Select-Object -Last 4 | ForEach-Object { Write-Host "    $_" }
            if ($ccExit -eq 0) {
                $encryptedOk = $true
                $encMode = "pyarmor"
                Write-Host "  [OK] 混淆包语法层验证通过（运行时启动验证留用户晨检）" -ForegroundColor Green
            } else {
                Write-Host "  [X] 混淆包 compileall 未通过（退出码 $ccExit），转降级路径" -ForegroundColor Yellow
                $encSkippedReason = "pyarmor 混淆产物 compileall 语法验证未通过"
            }
        }
    }

    if (-not $encryptedOk) {
        # ---------------------------------------------------------------
        # 降级路径：拷贝源码 + ENCRYPTED_SKIPPED.txt，不阻塞构建
        # ---------------------------------------------------------------
        if (-not $encSkippedReason) { $encSkippedReason = "未检测到 pyarmor 且自动安装未成功" }
        Write-Host "  [降级] 加密不可用（$encSkippedReason）→ 改出源码包" -ForegroundColor Yellow
        $encMode = "source-fallback"
        # 清理失败混淆可能留下的半成品（pyarmor_runtime 等），防混入源码包
        if (Test-Path -LiteralPath $coreBot) { Remove-Item -LiteralPath $coreBot -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $coreBot | Out-Null

        Assert-Copy (Join-Path $root "qq-bot\bot.py") $coreBot
        foreach ($d in @("core", "agent", "plugins", "harness")) {
            Assert-Copy (Join-Path $root "qq-bot\$d") (Join-Path $coreBot $d)
        }
        # 清理拷贝带入的运行时产物
        Get-ChildItem -LiteralPath $coreBot -Recurse -Directory -Filter "__pycache__" |
            ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }

        $skipTxt = @"
ENCRYPTED_SKIPPED

本核心包为【源码形态】，未经 pyarmor 混淆。

原因：$encSkippedReason

建议：pyarmor 免费 trial 有规模限额（本项目超限报 out of license）。
配置注册版许可后，在仓库根执行
    powershell -NoProfile -ExecutionPolicy Bypass -File build\build_release.ps1 -Core
重新出加密包。加密构建的具体路径与结论见 docs\决策日志-通用化改造-2026-09-10.md 的 Phase 4 小节。
"@
        [System.IO.File]::WriteAllText((Join-Path $coreDir "ENCRYPTED_SKIPPED.txt"), $skipTxt, $Utf8Bom)
    }

    # --- 随包文件（明文）：工程清单 / 环境样例 / 数据包 / 启停脚本 ---
    Assert-Copy (Join-Path $root "qq-bot\pyproject.toml")  $coreBot
    Assert-Copy (Join-Path $root "qq-bot\.env.example")    $coreBot
    Assert-Copy (Join-Path $root "qq-bot\packs")           (Join-Path $coreBot "packs")
    # tests/ 不入包（上文只拷 bot.py+四目录，tests 天然不在内）
    Assert-Copy (Join-Path $root "start.ps1")  $coreDir
    Assert-Copy (Join-Path $root "stop.ps1")   $coreDir
    Assert-Copy (Join-Path $root "start.bat")  $coreDir
    # 2026-09-12 文档精简：维护手册（运维权威）+ 部署指南（装/升级/迁移/卸载 + 依赖矩阵）随 Core 分发。
    Assert-Copy (Join-Path $root "docs\维护手册.md") $coreDir
    Assert-Copy (Join-Path $root "docs\部署指南.md") $coreDir
    $licenseSrc = Join-Path $root "LICENSE"
    if (Test-Path -LiteralPath $licenseSrc) { Assert-Copy $licenseSrc $coreDir }

    # compileall 产生的 __pycache__ 不入包
    Get-ChildItem -LiteralPath $coreDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }

    # --- 压缩 ---
    $coreZipPath = Join-Path $Out "qqai-core-$stamp.zip"
    if (Test-Path -LiteralPath $coreZipPath) { Remove-Item -LiteralPath $coreZipPath -Force }
    Compress-Archive -Path (Join-Path $coreDir "*") -DestinationPath $coreZipPath -CompressionLevel Optimal
    Write-Host "  Core 包已生成：$coreZipPath（加密方式：$encMode）"
}

# ============================================================================
# Launcher 件（可选）
# ============================================================================
$launcherZipPath = $null
if ($Launcher) {
    Step "[4/6] Launcher 件"
    $launcherDir = Join-Path $staging "launcher"
    New-Item -ItemType Directory -Force -Path $launcherDir | Out-Null

    $exe = Join-Path $root "launcher\QQAI-Launcher.exe"
    if (Test-Path -LiteralPath $exe) {
        Copy-Item -LiteralPath $exe -Destination $launcherDir -Force
        Write-Host "  已拷贝 QQAI-Launcher.exe"
    } else {
        Write-Host "  [跳过] 未找到 launcher\QQAI-Launcher.exe（存在才拷）" -ForegroundColor Yellow
    }
    Assert-Copy (Join-Path $root "launcher\README.md") $launcherDir

    $launcherZipPath = Join-Path $Out "qqai-launcher-$stamp.zip"
    if (Test-Path -LiteralPath $launcherZipPath) { Remove-Item -LiteralPath $launcherZipPath -Force }
    Compress-Archive -Path (Join-Path $launcherDir "*") -DestinationPath $launcherZipPath -CompressionLevel Optimal
    Write-Host "  Launcher 包已生成：$launcherZipPath"
}

# ============================================================================
# manifest.json（文件数 / 大小 / SHA256）
# ============================================================================
Step "[5/6] 生成 manifest.json"
function New-ArtifactEntry([string]$ZipPath, [string]$SrcDir, [string]$Note) {
    if (-not $ZipPath -or -not (Test-Path -LiteralPath $ZipPath)) { return $null }
    $count = (Get-ChildItem -LiteralPath $SrcDir -Recurse -File).Count
    return [ordered]@{
        file       = (Split-Path -Leaf $ZipPath)
        size_bytes = (Get-Item -LiteralPath $ZipPath).Length
        sha256     = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash
        files      = $count
        note       = $Note
    }
}

$artifacts = @()
if ($sdkZipPath) {
    $e = New-ArtifactEntry $sdkZipPath (Join-Path $staging "sdk") "开放 SDK：规范/皮肤格式/示例包（自检通过）"
    if ($e) { $artifacts += $e }
}
if ($coreZipPath) {
    $e = New-ArtifactEntry $coreZipPath (Join-Path $staging "core") "封闭核心（加密方式：$encMode）"
    if ($e) { $artifacts += $e }
}
if ($launcherZipPath) {
    # 发行台账里"发的是哪个启动器版本"应当机器可查：从**已打包的 exe 元数据**里读——
    # 那是用户实际拿到的东西，比脚本常量更可信（两者不一致时以 exe 为准，且 build_exe.ps1 会拦住不一致）。
    $exeVer = ""
    $exeSrc = Join-Path $root "launcher\QQAI-Launcher.exe"
    if (Test-Path -LiteralPath $exeSrc) { $exeVer = [string](Get-Item -LiteralPath $exeSrc).VersionInfo.FileVersion }
    $e = New-ArtifactEntry $launcherZipPath (Join-Path $staging "launcher") ("启动器 exe" + $(if ($exeVer) { " v$exeVer" } else { "（版本读取失败）" }) + " + README")
    if ($e) { $artifacts += $e }
}

$manifest = [ordered]@{
    generated     = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
    core_api      = $coreApi
    encryption    = $encMode
    sdk_selfcheck = $(if ($Sdk) { "PASS" } else { "skipped" })
    artifacts     = $artifacts
}
$json = $manifest | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText((Join-Path $staging "manifest.json"), $json, $Utf8NoBom)
Write-Host "  build\staging\manifest.json 已写入（机器写 JSON no-BOM）"
$artifacts | ForEach-Object {
    Write-Host ("  {0}  {1:N0} bytes  files={2}  sha256={3}" -f $_.file, $_.size_bytes, $_.files, $_.sha256.Substring(0, 16) + "...")
}

# ============================================================================
# 本地内容分发件（可选 -LocalContent；私有内容插件包，gitignored，不进 SDK/公开面）
# ============================================================================
if ($LocalContent) {
    Step "[+] 本地内容分发件（release\local-content，-LocalContent；4 个）"
    $exportPy = Join-Path $root "qq-bot\tools\export_content_packs.py"
    if (Test-Path -LiteralPath $exportPy) {
        if (Test-Path -LiteralPath $venvPy) {
            $env:PYTHONIOENCODING = "utf-8"
            & $venvPy $exportPy --out (Join-Path $Out "local-content")
            if ($LASTEXITCODE -ne 0) {
                Write-Host "  [warn] 内容包导出失败（退出码 $LASTEXITCODE）——不阻塞主构建" -ForegroundColor Yellow
            }
            Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue
        } else {
            Write-Host "  [warn] 未找到 venv python（$venvPy），跳过内容包导出（不阻塞主构建）" -ForegroundColor Yellow
        }
    } else {
        Write-Host "  [warn] 未找到 $exportPy（存在才调），跳过内容包导出" -ForegroundColor Yellow
    }
}

Step "[6/6] 构建完成"
Write-Host "产物目录：$Out"
Write-Host "加密路径结论：$encMode"
if ($encMode -eq "source-fallback") {
    Write-Host "提示：核心包为源码降级形态（ENCRYPTED_SKIPPED.txt 已随包说明）。" -ForegroundColor Yellow
}
