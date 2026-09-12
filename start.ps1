# QQ AI 陪伴机器人 一键启动（start.bat 调用）
# 引擎 -> NapCat -> 机器人；已在运行的组件自动跳过
# 2026-09-05 增强：
#   [0/3] 清理改为进程树强杀（taskkill /T /F），确保 shim+真身+后代无残留；
#   [3/3] 启动后轮询 8080 + bot.log 探测上线（失败时明确提示，而非笼统"机器人已停止"）；
#   收尾  打印"机器人已停止"前检测 8080/残留进程，提示可运行 stop.ps1 兜底。
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$root = $PSScriptRoot

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  QQ AI 陪伴机器人 一键启动" -ForegroundColor Cyan
Write-Host "  (推理引擎 - NapCat - 机器人)" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ---------- 防并发锁（防止重复双击） ----------
$lockFile = "$root\data\.launcher.lock"
$lockPid = if (Test-Path $lockFile) { Get-Content $lockFile -ErrorAction SilentlyContinue } else { $null }
$lockProc = if ($lockPid) { Get-Process -Id $lockPid -ErrorAction SilentlyContinue } else { $null }
# 仅当占用者是 PowerShell 启动进程时才视为并发（防 PID 被其他进程复用导致误判）
if ($lockProc -and $lockProc.ProcessName -match 'powershell|pwsh') {
    Write-Host "另一个启动器正在运行（PID $lockPid，启动于 $($lockProc.StartTime.ToString('HH:mm:ss'))）。" -ForegroundColor Yellow
    Write-Host "如需重启：先在该窗口按 Ctrl+C 退出，或运行 E:\robot\stop.ps1 后再启动。" -ForegroundColor Yellow
    Write-Host "如果那个窗口已经关闭但仍提示此信息，请删除文件后重试：" -ForegroundColor Yellow
    Write-Host "    $lockFile" -ForegroundColor Cyan
    Read-Host | Out-Null
    exit 0
}
"$PID" | Set-Content $lockFile

# ---------- 第一步：清理所有残留机器人进程（仅机器人，不动引擎/NapCat） ----------
# 放在最前面：任何环节出错时旧 bot 都不会残留占用 8080 端口与 log 文件句柄
Write-Host "[0/3] 清理残留机器人进程..." -ForegroundColor Yellow
$targets = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*bot.py*' })
foreach ($t in $targets) {
    taskkill.exe /PID $t.ProcessId /T /F 2>&1 | Out-Null
    Write-Host "      已清理进程树 PID $($t.ProcessId)（venv shim/真身/后代一并结束）" -ForegroundColor Cyan
}
Start-Sleep 3  # 等待进程句柄完全释放
# 兜底再清一次（防御：shim 已被杀后真身仍在列表快照外的情况）
$left = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*bot.py*' })
foreach ($l in $left) { taskkill.exe /PID $l.ProcessId /T /F 2>&1 | Out-Null }
Start-Sleep 1
# 端口兜底+守卫（搬自 stop.ps1 1b，2026-09-08 深夜补充）：提权运行的 bot 进程 CommandLine
# 经 WMI 查询为 NULL → 按命令行匹配找不到它；8080 的监听者就是 bot 本体。
# 仅当监听者进程名是 python 才强杀（守卫：不误杀占用 8080 的其他应用）。
$portListener = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($portListener) {
    $p = Get-Process -Id $portListener.OwningProcess -ErrorAction SilentlyContinue
    if ($p -and $p.ProcessName -match 'python') {
        Write-Host "      发现按命令行找不到的 bot 残留（提权运行，CommandLine=NULL）：PID $($p.Id)（8080 监听者）→ 强杀进程树" -ForegroundColor Yellow
        taskkill.exe /PID $p.Id /T /F 2>&1 | Out-Null
        Start-Sleep 1
    }
}

# ---------- [1/3] 推理引擎 llama-server ----------
$engineUp = $false
try { Invoke-RestMethod "http://127.0.0.1:11434/health" -TimeoutSec 2 | Out-Null; $engineUp = $true } catch { }
if ($engineUp) {
    Write-Host "[1/3] 推理引擎已在运行，跳过。" -ForegroundColor Green
} else {
    Write-Host "[1/3] 启动推理引擎（加载模型约需 15 秒）..." -ForegroundColor Yellow
    # 2026-08-26 模型感知：读 model_mode.json（切换gemma 后不拉错模型）
    $modelMode = "gemma"   # 2026-09-09 兜底对齐 Launcher.ps1 Start-Engine（qwen 分支已死：Qwen3-14B 已删；model_mode.json 缺失/损坏时不再 exit 1 卡死全新机器）
    try {
        $mm = Get-Content "$root\data\model_mode.json" -Raw -Encoding UTF8 | ConvertFrom-Json
        $vals = @($mm.PSObject.Properties | ForEach-Object { $_.Value })
        if ($vals.Count -gt 0) { $modelMode = [string]$vals[-1] }
    } catch { }
    if ($modelMode -eq "gemma") {
        Write-Host "      使用模型: Gemma4-12B（模型模式=gemma）" -ForegroundColor Cyan
        $engineArgs = @(
            "-m", "$root\data\models\gemma4-12b\gemma-4-12B-it-heretic-Q4_K_M.gguf",
            "--host", "127.0.0.1", "--port", "11434",
            "-c", "32768", "-ngl", "99", "--parallel", "1",   # 2026-09-08 调研落地A：2→1 去槽竞争（decode 16.7→32-45 tok/s）；后台任务排队无感；单槽 32k
            "-ctk", "q8_0", "-ctv", "q8_0",
            "--reasoning", "on", "--reasoning-budget", "200",   # 2026-09-08 回退（120 实证帮倒忙：思考=筛选器，砍掉=不过滤直接倒→更夸张）；200 为 09-05 后的既定值
            "--repeat-penalty", "1.15", "--repeat-last-n", "256",
            "--spec-type", "ngram-simple"  # 2026-09-08 P3 + A2(09-09)：ngram 推测解码（无损,零显存,从 prompt 复用 n-gram 起草）
        )
    } else {
        # 2026-09-08：Qwen3-14B 已删除（用户实测远不如 gemma——本分支为死分支保留，防误切）
        Write-Host "      错误：model_mode 非 gemma（Qwen3-14B 已删除，engine-ctx 动态分支不可用）——请将 model_mode 设为 gemma 后重试。" -ForegroundColor Red
        Read-Host | Out-Null
        exit 1
    }
    Start-Process -FilePath "$root\tools\llama.cpp\llama-server.exe" -ArgumentList $engineArgs `
        -WindowStyle Hidden `
        -RedirectStandardOutput "$root\data\llama-server.log" `
        -RedirectStandardError "$root\data\llama-server.err.log"
    Start-Sleep 15
    Write-Host "      引擎已启动（日志: data\llama-server.log）。" -ForegroundColor Green
}

# ---------- [1.5/3] embedding 服务（记忆语义检索，CPU 11435） ----------
& "$root\tools\start-embedding.ps1"

# ---------- [2/3] NapCat ----------
$napcat = Get-Process -Name "NapCatWinBootMain" -ErrorAction SilentlyContinue
if ($napcat) {
    Write-Host "[2/3] NapCat 已在运行，跳过。" -ForegroundColor Green
} else {
    Write-Host "[2/3] 启动 NapCat（新窗口，若需登录请扫码）..." -ForegroundColor Yellow
    Start-Process "$root\tools\napcat\napcat\launcher-user.bat" -WorkingDirectory "$root\tools\napcat\napcat"
    Start-Sleep 8
    Write-Host "      NapCat 已启动。" -ForegroundColor Green
}

# ---------- [3/3] QQ 机器人 ----------
# 残留进程已在脚本第一步清理；此处再兜底清一次（防御：引擎/NapCat 阶段耗时期间若有新残留）
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*bot.py*' } |
    ForEach-Object { taskkill.exe /PID $_.ProcessId /T /F 2>&1 | Out-Null }
Start-Sleep 2
# 等待 8080 端口完全释放（最多 15 秒 + 重试强杀一次），防止新 bot 绑定失败（Errno 10048）
$portReleased = $false
for ($i = 0; $i -lt 15; $i++) {
    if (-not (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue)) { $portReleased = $true; break }
    if ($i -eq 6) {
        # 8 秒仍未释放：端口可能被非 bot 进程占用（或僵尸句柄）→ 只杀 python 监听者
        # B12：进程名守卫（曾无守卫直杀 PID——8080 被其他应用占用时会误杀）
        $l = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($l) {
            $procName = (Get-Process -Id $l.OwningProcess -ErrorAction SilentlyContinue).ProcessName
            if ($procName -match 'python') { taskkill.exe /PID $l.OwningProcess /T /F 2>&1 | Out-Null }
        }
    }
    Start-Sleep 1
}
if (-not $portReleased) {
    Write-Host "[3/3] 警告：8080 端口 15 秒内未释放，仍尝试启动（若失败请检查占用进程）..." -ForegroundColor Yellow
}

Write-Host "[3/3] 启动机器人（日志显示在本窗口，同时写入 data\bot.log；Ctrl+C 或关闭本窗口 = 完全退出）..." -ForegroundColor Yellow
Write-Host ""
Push-Location "$root\qq-bot"
# bot 内部已配置 loguru 文件日志（UTF-8），此处直接前台运行，不再 Tee 文件（消除编码/句柄占用问题）
& ".\.venv\Scripts\python.exe" -u bot.py 2>&1
Pop-Location

# ---------- 收尾：机器人已退出 ----------
# 2026-09-05：探测启动是否成功、收尾是否有残留——给用户明确信息而非笼统"已停止"
$up = $false
try { Invoke-RestMethod "http://127.0.0.1:8080/" -TimeoutSec 2 | Out-Null; $up = $true } catch { }
$leftBots = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*bot.py*' })
$c = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
Write-Host ""
# 异常退出检测（启动失败/崩溃：10048、Traceback、startup failed 等强特征）
$tailErr = $false
try {
    $tail = Get-Content "$root\data\bot.log" -Tail 40 -Encoding UTF8 -ErrorAction SilentlyContinue
    $tailErr = [bool]($tail | Select-String -Pattern "Errno 10048|Traceback|startup failed" -Quiet -ErrorAction SilentlyContinue)
} catch { }
if ($tailErr) {
    Write-Host "提示：bot.log 尾部存在异常特征（Errno 10048/Traceback/startup failed），上次启动可能失败——见 data\bot.log。" -ForegroundColor Red
}
if ($leftBots.Count -gt 0 -or $c) {
    Write-Host "检测到残留：仍有 $($leftBots.Count) 个 bot 进程 / 8080 被 PID $($c.OwningProcess) 监听。" -ForegroundColor Red
    Write-Host "可运行 E:\robot\stop.ps1 彻底清理。" -ForegroundColor Yellow
} elseif ($up) {
    Write-Host "机器人已停止。按回车退出..." -ForegroundColor Cyan
} else {
    Write-Host "机器人已停止。按回车退出..." -ForegroundColor Cyan
}
Remove-Item $lockFile -ErrorAction SilentlyContinue
Read-Host | Out-Null
