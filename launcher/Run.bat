@echo off
rem QQ AI companion launcher - (c) xiaococo
rem
rem 2026-09-12：QQAI-Launcher.exe 是**构建产物、不随仓**（克隆安装拿不到它，见 README 的两种安装路径），
rem 而快速开始正是让人双击本文件——原先那句 `start "" QQAI-Launcher.exe`
rem 在克隆环境里等于"双击了什么都不发生"（静默失败，最坏的那种）。
rem 现在：有 exe 用 exe（发行物 / 懒人包路径），没有就回落到直接跑 Launcher.ps1（源码路径）。
rem 注：Launcher.ps1 用 PowerShell 5.1 直跑即可，不需要管理员权限（WebView2 用系统 Evergreen 运行时）。
cd /d "%~dp0"
if exist "%~dp0QQAI-Launcher.exe" (
  start "" "%~dp0QQAI-Launcher.exe"
) else (
  echo [Run.bat] QQAI-Launcher.exe 不存在 —— 克隆安装的常态，改用 Launcher.ps1 启动。
  echo [Run.bat] QQAI-Launcher.exe not found - normal for a git clone; falling back to Launcher.ps1 ...
  echo.
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Launcher.ps1"
  echo.
  echo [Run.bat] Launcher.ps1 已退出 / exited.
  pause
)
