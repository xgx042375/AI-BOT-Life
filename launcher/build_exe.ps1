# 重编译启动器 exe（ps2exe）+ 双处同步 + 自校验。
#
# 为什么要脚本而不是照 README 抄命令：**版本号原先要在两处手写**（Launcher.ps1 的
# $script:LAUNCHER_VERSION 与 ps2exe 的 -version），抄漏一次就出现"exe 说 3.9.75、
# 脚本说 3.9.76"的假版本（2026-09-12 实际发生）。这里从脚本现读版本，只有一个来源。
#
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File launcher\build_exe.ps1
# 依赖：ps2exe 模块（PowerShell Gallery），Windows PowerShell 5.1。
# 纪律：本文件必须带 UTF-8 BOM（PS5.1 否则按 GBK 读，中文注释会炸）。
$ErrorActionPreference = "Stop"
$launcherDir = $PSScriptRoot
$root = Split-Path -Parent $launcherDir
$src = Join-Path $launcherDir "Launcher.ps1"
$out = Join-Path $launcherDir "QQAI-Launcher.exe"
$rootCopy = Join-Path $root "QQAI-Launcher.exe"

# 1) 版本唯一来源：从脚本常量现读
$raw = [System.IO.File]::ReadAllText($src, [System.Text.UTF8Encoding]::new($true))
$m = [regex]::Match($raw, '\$script:LAUNCHER_VERSION\s*=\s*"([^"]+)"')
if (-not $m.Success) { Write-Host "× 读不到 `$script:LAUNCHER_VERSION（Launcher.ps1 改过结构？）"; exit 1 }
$ver = $m.Groups[1].Value

# 2) 编译前先验 BOM 与语法——BOM 一丢，编出来的 exe 跑起来就是一堆假语法错
$bytes = [System.IO.File]::ReadAllBytes($src)
if (-not ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)) {
    Write-Host "× Launcher.ps1 缺 UTF-8 BOM（PS5.1 会按 GBK 读 → exe 必挂）。先补 BOM 再编译。"; exit 1
}
$errs = $null
[System.Management.Automation.Language.Parser]::ParseFile($src, [ref]$null, [ref]$errs) | Out-Null
if (@($errs).Count) {
    Write-Host ("× 解析错误 {0} 处，先修再编译：" -f @($errs).Count)
    @($errs) | Select-Object -First 5 | ForEach-Object { Write-Host ("    {0}: {1}" -f $_.Extent.StartLineNumber, $_.Message) }
    exit 1
}
Write-Host ("√ 版本 {0} ｜ BOM 在位 ｜ 解析 0 错误" -f $ver)

# 3) 编译（-requireAdmin 勿删：提权才能终止提权启动的 bot 进程树，见 README §8）
foreach ($mod in @("$env:USERPROFILE\Documents\PowerShell\Modules\ps2exe",
                   "$env:USERPROFILE\Documents\WindowsPowerShell\Modules\ps2exe")) {
    if (Test-Path $mod) { Import-Module $mod -Force; break }
}
if (-not (Get-Command Invoke-ps2exe -ErrorAction SilentlyContinue)) { Import-Module ps2exe -Force }
Invoke-ps2exe -InputFile $src -OutputFile $out `
    -title "QQ AI Launcher" -description "QQ AI companion robot launcher" `
    -product "QQ AI Launcher" -copyright "(c) 2026" -version $ver `
    -company "" -noConsole -noOutput -noError -STA -x64 -requireAdmin | Out-Null

# 4) 双处同步 + 自校验（内容不一致 = 用户双击哪个 exe 行为不同，最阴的一类问题）
Copy-Item $out $rootCopy -Force
$h1 = (Get-FileHash $out -Algorithm SHA256).Hash
$h2 = (Get-FileHash $rootCopy -Algorithm SHA256).Hash
$vi = (Get-Item $out).VersionInfo
Write-Host ("√ 已生成 {0}（{1:N0} B）" -f (Split-Path -Leaf $out), (Get-Item $out).Length)
Write-Host ("√ 双处 SHA256 一致：{0}" -f ($h1 -eq $h2))
Write-Host ("√ 元数据：FileVersion={0} Product={1} Company=[{2}] Copyright={3}" -f `
    $vi.FileVersion, $vi.ProductName, $vi.CompanyName, $vi.LegalCopyright)
if (-not $h1.Equals($h2)) { Write-Host "× 双处不一致，别就这样发行"; exit 1 }
if ($vi.FileVersion -ne $ver) { Write-Host ("× 元数据版本 {0} ≠ 脚本版本 {1}" -f $vi.FileVersion, $ver); exit 1 }
Write-Host "√ 全部通过"
