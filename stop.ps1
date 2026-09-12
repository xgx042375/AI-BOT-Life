# QQ AI 陪伴机器人 停止脚本（stop.bat 调用）
# 用途：独立停止 bot——覆盖"启动器窗口已关闭但 bot 仍在"的孤儿/残留场景。
# 语义：强杀全部 bot.py 进程树（venv shim + 真解释器 + 后代）→ 清启动器锁 → 验证 8080 释放。
# 不动推理引擎 / NapCat / embedding（它们由 start.ps1 独立管理，可共用）。
# 正常停止 bot 的首选仍是：在启动器窗口按 Ctrl+C（优雅）或关闭启动器窗口（同控制台全退）。
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$root = $PSScriptRoot

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  QQ AI 陪伴机器人 停止（仅 bot，引擎/NapCat 不动）" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# 1) 收集所有 bot 相关进程（venv shim + 真解释器，命令行均含 bot.py）
$targets = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*bot.py*' })

# 1b) 2026-09-08 深夜补充：提权运行的 bot 进程 CommandLine 查询为 NULL → 上面按命令行找不到它。
# 补一条按端口兜底：8080（bot nonebot 服务端口）的监听者就是 bot 本体。
$portListener = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($portListener) {
    $p = Get-Process -Id $portListener.OwningProcess -ErrorAction SilentlyContinue
    if ($p -and $p.ProcessName -match 'python' -and -not ($targets | Where-Object { $_.ProcessId -eq $p.Id })) {
        Write-Host "发现按命令行找不到的 bot 进程（提权运行，CommandLine=NULL）：PID $($p.Id)（端口 8080 监听者）" -ForegroundColor Yellow
        $targets += @(Get-CimInstance Win32_Process -Filter "ProcessId=$($p.Id)")
    }
}

if ($targets.Count -eq 0) {
    Write-Host "没有找到运行中的机器人进程。" -ForegroundColor Green
} else {
    Write-Host "发现 $($targets.Count) 个机器人进程，强杀进程树..." -ForegroundColor Yellow
    foreach ($t in $targets) {
        $killOut = taskkill.exe /PID $t.ProcessId /T /F 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Host "      PID $($t.ProcessId) 杀除失败（拒绝访问=权限不足，请用管理员身份运行本脚本）：" -ForegroundColor Red
            Write-Host "      $killOut" -ForegroundColor DarkGray
        } else {
            Write-Host "      已清理 PID $($t.ProcessId)" -ForegroundColor Cyan
        }
    }
    Start-Sleep 3
}

# 2) 兜底：再扫一遍（进程树杀后 shim 可能重新暴露？不会——shim 是父；此处防御后代）
$left = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*bot.py*' })
foreach ($l in $left) { taskkill.exe /PID $l.ProcessId /T /F 2>&1 | Out-Null }
Start-Sleep 2

# 3) 验证 8080 释放
$c = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($c) {
    Write-Host "警告：8080 仍被 PID $($c.OwningProcess) 监听——bot 未停止。多半是权限不足（提权进程）：请用管理员身份打开 PowerShell 再运行本脚本，或任务管理器结束该 python.exe。" -ForegroundColor Red
} else {
    Write-Host "8080 已释放，机器人已完全停止。" -ForegroundColor Green
}

# 4) 清启动器锁（若启动器窗口还开着，其收尾 Remove-Item 幂等无害）
$lockFile = "$root\data\.launcher.lock"
if (Test-Path $lockFile) {
    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
    Write-Host "启动器锁已清除。" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "完成。如需启动：E:\robot\start.ps1" -ForegroundColor Cyan
