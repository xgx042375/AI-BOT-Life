import os
import signal
import sys

# Windows 控制台默认 GBK：日志含 emoji/⏎ 等字符时 loguru 控制台 sink 抛 UnicodeEncodeError 刷屏且丢日志行。
# 保留原编码（中文显示不受影响），仅把不可编码字符替换为 ?，杜绝编码异常；文件 sink（bot.log）本就 UTF-8 不受影响。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

import nonebot
from nonebot.adapters.onebot.v11 import Adapter

# 日志：控制台（stdout，启动器窗口可见）+ 文件（UTF-8，供查看与诊断）。
# 由 bot 内部写日志文件（loguru），启动器不再 Tee/重定向 bot.log——消除编码混写与句柄占用问题。
# 2026-09-12 T7.3：路径不再写死盘符——统一走 core/paths.py 的唯一数据根（data_path）。
from core.paths import data_path

BOT_LOG_FILE = str(data_path("bot.log"))
BOT_TRACE_FILE = str(data_path("bot_hook_trace.txt"))
try:
    from loguru import logger as _lg

    _lg.add(
        BOT_LOG_FILE,
        encoding="utf-8",
        rotation="10 MB",
        retention=3,
        enqueue=True,
    )
except Exception:  # noqa: BLE001
    pass

# 关闭启动器 cmd 窗口（CTRL_CLOSE / Ctrl+Break）时：整个进程树干净退出。
# Windows 上 CTRL_CLOSE 映射为 SIGBREAK；spawn 子进程共享控制台同样收到该信号。
# 连带关闭 NapCat（node napcat 进程，按命令行过滤不误杀其他 node），实现"关闭=全部退出"。
if sys.platform == "win32":
    def _shutdown_all(sig, frame):
        try:
            import subprocess

            # 先关 TTS worker（无 HTTP 常驻进程，按 Popen 停；旧 8123 服务兜底清理），再关 NapCat/QQ
            try:
                from plugins import voice as _v1
                _v1.stop_tts_server()
            except Exception:
                subprocess.run(
                    [
                        "powershell", "-NoProfile", "-Command",
                        "Get-NetTCPConnection -LocalPort 8123 -State Listen -ErrorAction SilentlyContinue | "
                        "ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }",
                    ],
                    capture_output=True, timeout=10,
                )
            subprocess.run(
                [
                    "powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | "
                    "Where-Object { $_.CommandLine -match 'napcat' } | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }",
                ],
                capture_output=True, timeout=10,
            )
            # 2026-09-12 审计 N 项：补 timeout——同块上一行的 Stop-Process 有 timeout=10 而这里没有，
            # 是疏忽不是决定：taskkill 卡住会把"关闭"流程**挂死**（紧跟其后的 os._exit(0) 就等不到）。
            subprocess.run(["taskkill", "/F", "/IM", "NapCatWinBootMain.exe"],
                           capture_output=True, timeout=10)
            # 2026-09-06 用户裁决（铁律）：绝不杀 QQ.exe——用户大号与 bot 小号同在 QQ.exe 进程，无法按进程区分。
            # 只停 NapCat 引导链；QQ 客户端保留（复用登录）。
        except Exception:  # noqa: BLE001
            pass
        os._exit(0)

    try:
        signal.signal(signal.SIGBREAK, _shutdown_all)
    except (ValueError, OSError):
        pass  # 平台差异：SIGBREAK 只有 Windows 有；注册失败即跳过（Ctrl+Break 优雅退出是加分项，非必需）


nonebot.init()
driver = nonebot.get_driver()
driver.register_adapter(Adapter)
nonebot.load_from_toml("pyproject.toml")

# ---------------- 第三方插件根：data/plugins/（2026-09-12 S3） ----------------
# 三层目录分离的第三层：
#   qq-bot/plugins/  框架自带 —— 更新发行版时**会被覆盖**，不要往里放自己的东西
#   data/plugins/    第三方代码插件 —— **永不被覆盖**，本处自动发现
#   data/packs/      内容包（零代码）—— core.packs 负责
# 三条纪律：
#   ① 目录不存在 = 常态（普通用户从不建它）→ 静默跳过，零日志噪音；
#   ② **坏插件不得带崩 bot**：逐个装、逐个隔离失败，装不上的记一行 warning 就继续；
#   ③ 路径**追加**而非插到最前（sys.path.append）：插到首位会让一个叫 json.py 的插件
#      遮蔽标准库——那是最难查的一类故障。追加的代价只是"同名时插件不生效"，可接受。
try:
    from core.paths import DATA_ROOT as _pl_root

    _pl_dir = _pl_root / "plugins"
    if _pl_dir.is_dir():
        if str(_pl_dir) not in sys.path:
            sys.path.append(str(_pl_dir))
        _pl_ok: list[str] = []
        _pl_bad: list[str] = []
        for _pl in sorted(_pl_dir.iterdir()):
            if _pl.name.startswith((".", "_")) or _pl.name == "__pycache__":
                continue
            if _pl.is_dir():
                if not (_pl / "__init__.py").is_file():
                    continue          # 无 __init__.py 的目录不是包，跳过（不作警告，可能是资源目录）
                _pl_name = _pl.name
            elif _pl.suffix == ".py":
                _pl_name = _pl.stem
            else:
                continue
            try:
                # ★ 2026-09-12 实测教训：`nonebot.load_plugin()` **不抛异常**——它在内部
                # try/except 里吞掉导入错误，只打一行 `[ERROR] nonebot | Failed to import "x"`，
                # 然后照常返回。所以"没抛错"不等于"装上了"：第一版据此把坏插件报成 loaded=[],
                # 日志在说谎。改以**装载器登记表的前后差集**为准（before/after 差 = 真装上）。
                _before = {p.name for p in nonebot.get_loaded_plugins()}
                nonebot.load_plugin(_pl_name)
                if _pl_name in {p.name for p in nonebot.get_loaded_plugins()} - _before:
                    _pl_ok.append(_pl_name)
                else:
                    _pl_bad.append(f"{_pl_name}(导入期失败或未注册——见上方 nonebot 的 ERROR 行)")
            except Exception as _pl_e:  # noqa: BLE001
                _pl_bad.append(f"{_pl_name}({type(_pl_e).__name__}: {_pl_e})")
        if _pl_ok or _pl_bad:
            _lg.info("data/plugins: loaded={} failed={}", _pl_ok, _pl_bad)
        for _b in _pl_bad:
            _lg.warning("data/plugins 插件装载失败（已隔离，不影响其余）: {}", _b)
except Exception as _pl_e:  # noqa: BLE001
    _lg.warning("data/plugins 发现流程跳过: {} [{}]", _pl_e, type(_pl_e).__name__)

# 2026-09-06 启动器本地通道：设置人设（等价 /人设 命令处理，bot 同进程切换、无需重启）
try:
    from nonebot import get_app as _la_get_app
    from fastapi import Body as _la_body
    from plugins import brain as _la_brain, debug as _la_dbg

    _la_app = _la_get_app()

    @_la_app.get("/launcher/diag")
    async def _launcher_diag():
        try:
            _pn = _la_brain._persona_name(None)
            _pc = _la_brain._persona_card(None)
            from nonebot import get_driver as _gd
            _cfg = _gd().config
            _su = list(getattr(_cfg, "superusers", set()) or set())
            _sel2 = {}
            try:
                import json as _j2
                _sel2 = _j2.loads(_la_brain.PERSONA_SELECT_FILE.read_text(encoding="utf-8-sig"))
            except Exception as _e2:
                _sel2 = {"err": str(_e2)}
            try:
                _sf = str(_la_brain.PERSONA_SELECT_FILE)
            except Exception as _e3:
                _sf = "?" + str(_e3)
            return {"pn": _pn, "card": str(_pc.get("name") or ""), "owner_mode": bool(_pc.get("owner_mode")), "select": _sel2, "sel_file": _sf, "superusers": _su, "pers": str(getattr(_cfg, "persona", ""))}
        except Exception as _e:
            return {"err": str(_e)}
    @_la_app.post("/launcher/persona")
    async def _launcher_persona(data: dict = _la_body(default={})):
        try:
            _name = str(data.get("name", "") or "").strip()
            _uid = str(data.get("uid", "") or "") or next(iter(_la_dbg.SUPERUSERS), None)
            # 2026-09-07 P2：曾硬编码兜底 QQ 号——superusers 为空且未传 uid 时会把人设切到无关账号
            if not _uid:
                return {"ok": False, "err": "未指定 uid 且未配置 superusers"}
            _ok = _la_brain.set_persona(_uid, _name)
            if not _ok:
                return {"ok": False, "err": f"没有该人设卡: {_name}", "name": _name}
            # 2026-09-06 头像联动：在线切换后强制应用 QQ 头像（force 绕过已应用标记）
            _avatar_warn = ""
            try:
                from nonebot import get_bot as _la_get_bot
                _card = _la_brain._persona_card(_uid)
                _modes = _la_brain._load_modes()
                _mode = (_modes.get(str(_uid)) or {}).get("mode", 0)
                await _la_brain._apply_persona_avatar(_la_get_bot(), _card, _mode, force=True)
                try:
                    await _la_brain._apply_persona_nickname(_la_get_bot(), _card)
                except Exception as _e3:  # noqa: BLE001
                    _avatar_warn = (_avatar_warn + " | nick: " + str(_e3)[:60])[:150]
            except Exception as _e2:  # noqa: BLE001
                _avatar_warn = str(_e2)[:100]
            return {"ok": True, "name": _name, "uid": _uid, "avatar_warn": _avatar_warn}
        except Exception as _e:  # noqa: BLE001
            return {"ok": False, "err": str(_e)}
except Exception as _e:  # noqa: BLE001
    with open(BOT_TRACE_FILE, "a", encoding="utf-8") as _f:
        _f.write(f"launcher persona route error: {_e}\n")

# 2026-08-26 后台循环兜底启动：debug 插件自身的 on_startup/on_bot_connect 在加载时序下未触发，
# 在 bot.py 注册（run 前）确保 watchdog/proactive/item/review 循环一定启动（幂等：debug._LOOPS_STARTED）


@driver.on_startup
async def _start_background_loops():
    try:
        from plugins import debug as _dbg
        _s = _dbg._LOOPS_STARTED
        with open(BOT_TRACE_FILE, "w", encoding="utf-8") as _f:
            _f.write(f"bot.py on_startup triggered, LOOP_STARTED={_s}\n")
        await _dbg.start_background_loops_async()  # 2026-09-06 主 loop 启动（线程 loop 会导致 LangGraph 锁跨 loop 崩溃）
    except Exception as _e:  # noqa: BLE001
        with open(BOT_TRACE_FILE, "a", encoding="utf-8") as _f:
            _f.write(f"hook error: {_e}\n")
    # 内容包预置目录接线（robot-pack-v1 Phase 3）：包登记道具 → special 预置目录
    # （幂等：同名覆盖；失败隔离：包体系任何故障不阻断启动——只留 trace 痕）
    try:
        from core import packs as _packs_boot
        from core import special as _special_boot

        _reg = _special_boot.add_item_catalog(_packs_boot.items_index())
        with open(BOT_TRACE_FILE, "a", encoding="utf-8") as _f:
            _f.write(f"packs item catalog registered: {_reg}\n")
    except Exception as _pe:  # noqa: BLE001
        with open(BOT_TRACE_FILE, "a", encoding="utf-8") as _f:
            _f.write(f"packs item catalog error: {_pe}\n")
    # 语音长驻服务随启动器一并拉起——**仅当语音功能开启时**（默认关闭不占显存；/语音 开 时由插件拉起）
    try:
        from plugins import voice as _voice
        if _voice.load_cfg().get("enabled"):
            _ok = _voice.ensure_tts_server()
            with open(BOT_TRACE_FILE, "a", encoding="utf-8") as _f:
                _f.write(f"tts server ensured: {_ok}\n")
            # 2026-09-06 启动器优化：拉起后后台预热（模型加载好，首次语音不用等 6.5s）
            if _ok:
                try:
                    from core import bgtasks as _bgt
                    # 2026-09-12 审计 N 项：裸 create_task 的返回值没人拿 → 弱引用 → 预热任务可能
                    # 被 GC 吞掉（表现是"首次语音仍然等 6.5 秒"，看不出错）。改走统一持引用。
                    _bgt.spawn(_voice.warmup_worker())
                except Exception as _we:  # noqa: BLE001
                    with open(BOT_TRACE_FILE, "a", encoding="utf-8") as _f:
                        _f.write(f"tts warmup task error: {_we}\n")
        else:
            _voice.stop_tts_server()  # 语音关：顺带停残留服务（释放显存）
            with open(BOT_TRACE_FILE, "a", encoding="utf-8") as _f:
                _f.write("tts server ensured: skipped (voice off)\n")
    except Exception as _e:  # noqa: BLE001
        with open(BOT_TRACE_FILE, "a", encoding="utf-8") as _f:
            _f.write(f"tts server hook error: {_e}\n")


if __name__ == "__main__":
    try:
        nonebot.run()
    finally:
        # 2026-09-05：Ctrl+C 时 uvicorn 回放信号后部分任务悬挂 → 进程僵死（启动器卡住/锁残留）。
        # 无论正常/异常退出，强制收尾（信号已由 uvicorn 处理过优雅关闭；此处保证进程一定退出）。
        import os as _os

        _os._exit(0)
