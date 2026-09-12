"""调试命令插件：以"/"开头的命令（仅主人可用），远程查看状态 / 重置状态机。

设计目的：bot 行为异常（状态机卡住、模式错乱、配额误判等）时，
无需重启/改文件即可自查自愈，避免"宕机无法解决"。

命令（私聊直接发；群聊需 @机器人）：
    /状态 或 /status        查看当前全部运行时状态（人格/亲密度/情欲/催眠/表情包配额）
    /重启引擎 /restart-engine  立即重启推理引擎（参数三源统一，对齐 start.ps1 权威值）
    /重置情欲 /reset-lust    清除情欲状态（含情欲指数）
    /重置催眠 /reset-hypno   解除催眠/不许装了（恢复原亲密度）
    /重置模式 /reset-mode    恢复善良人格（并换回善良头像）
    /重置全部 /reset-all     以上全部
    /帮助 /help              命令列表

引擎守护：bot 启动后后台 watchdog 每 60 秒探测引擎健康，
离线自动拉起引擎（三源统一参数，5 分钟防抖），并记录到日志。
权限：仅 SUPERUSERS（.env superusers）可用；命令不记录进对话记忆。
"""
import asyncio
import base64
import json
import os
import random
import re
import subprocess
import time

from core import atomics
from core import bgtasks  # 2026-09-12：后台任务统一持引用（裸 create_task 会被 GC 静默吞掉）
from core.paths import DATA_ROOT, TOOLS_ROOT  # 2026-09-12 S1：路径唯一来源
from core.paths import (  # 2026-09-12 E 项：引擎端口/模型/可执行名唯一来源（原与 core.llm、brain 各写一份）
    EMBED_MODEL_FILE, EMBED_PORT, EMBED_URL,
    ENGINE_EXE, ENGINE_HOST, ENGINE_IMAGE, ENGINE_PORT, ENGINE_URL,
)
from core.paths import MODEL_FILE_GEMMA as ENGINE_MODEL  # 本模块历史叫法，值同 core.paths.MODEL_FILE_GEMMA

import httpx
from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.rule import Rule
from nonebot.log import logger

from .. import brain, memory, persona, sticker
from agent import lifesim  # 2026-09-05：后台 gather 注册 lifesim_loop（agent 为顶层包，绝对导入）

driver = get_driver()
cfg = driver.config
SUPERUSERS = set(getattr(cfg, "superusers", set()) or set())

# ---------------- 引擎守护（健康检查 + 自动重启 + 三源统一参数） ----------------
# 2026-09-12 E 项：端口 / 模型文件路径 / 可执行名改为 core.paths 单一来源——原先本模块与
# brain.py、core/llm.py 各写一份字面量（模型路径与 exe 路径逐字重复）。对外名称保持不变
# （ENGINE_URL / ENGINE_PORT / ENGINE_MODEL / ENGINE_EXE），值逐字节相同。
# ⚠ PS 侧（start.ps1=权威 / Launcher.ps1）无法 import，只能对齐值 → smoke §36 逐参数守护。
ENGINE_PARALLEL = 1   # 2026-09-09 深夜：三源漂移统一（对齐 start.ps1 权威值）——曾 3；本值用于 compute_engine_ctx 与 watchdog 回退分支重启参数，非纯展示
ENGINE_CTX = 32768  # 2026-09-09：三源统一（对齐 start.ps1 权威值）——回退分支不再动态算 ctx（compute_engine_ctx ≤8192 与权威 32768 相差 4 倍=两条启动路径行为不同的虚空 bug）；该函数保留为诊断/回归工具
# 2026-09-10 Phase1：对话侧上下文预算基数已移 core/llm.py（llm_ctx_budget，与本值同口径 32768）；
# 本处 watchdog 重启参数不动（仅本地引擎有意义，外部端点无重启语义）
WATCHDOG_INTERVAL = 60        # 健康检查间隔（秒）
WATCHDOG_MIN_RESTART_GAP = 300  # 两次自动重启最小间隔（5 分钟防抖）
_LAST_RESTART = 0.0


def compute_engine_ctx(parallel: int = ENGINE_PARALLEL) -> int:
    """按当前显存空闲动态计算总 ctx（保证是 parallel 整数倍，防引擎自行取整降级）。

    探测失败回退保守值 8192（1x8192；2026-09-09 深夜随 parallel 统一 1 相应收窄）。
    2026-09-09：三源统一（对齐 start.ps1 权威值）——restart_engine 不再采用动态值（-c 恒 32768），
    本函数保留为诊断/回归工具（smoke 引用），不再驱动启动参数。
    """
    weight_gb = 9.4
    reserve_gb = 2.0
    kv_mb_per_token = 0.087
    per_slot_cap = 8192
    total_mb, used_mb = 16384.0, 0.0
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        parts = out.strip().splitlines()[0].split(",")
        total_mb = float(parts[0].strip())
        used_mb = float(parts[1].strip())
    except Exception:  # noqa: BLE001
        pass
    avail_mb = total_mb - used_mb - reserve_gb * 1024
    total_ctx = int(max(6144, (avail_mb - weight_gb * 1024) / kv_mb_per_token))
    total_ctx = min(total_ctx, per_slot_cap * parallel)
    per_slot = max(2048, (total_ctx // parallel // 512) * 512)
    return per_slot * parallel


def restart_engine() -> tuple[bool, str]:
    """重启推理引擎（先杀残留，再按三源统一参数拉起——对齐 start.ps1 权威值）。返回 (是否成功, 说明)。"""
    global _LAST_RESTART
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", ENGINE_IMAGE],
            capture_output=True, timeout=10,
        )
        time.sleep(2)
    except Exception:  # noqa: BLE001
        pass
    ctx = ENGINE_CTX  # 2026-09-09：三源统一（对齐 start.ps1 权威值）——曾 compute_engine_ctx() 动态值（≤8192），与权威 -c 32768 是"同一台机器两条启动路径行为不同"的虚空 bug
    # 与主启动参数保持一致：--reasoning on + budget（watchdog 重启不得改变思考模式）
    # 模型感知：按当前用户的 model_mode 拉起对应模型（指令式切换后 watchdog 不拉错模型）
    # 2026-09-04：qwen 已移除，默认 gemma
    model_key = "gemma"
    try:
        from plugins.brain import _load_model_modes, MODELS  # noqa: F401

        _modes = _load_model_modes()
        if _modes:
            # 多用户取多数/最近；简单处理：任一用户选了 gemma 且其为最近写入 → 用 gemma
            model_key = list(_modes.values())[-1] if _modes else "gemma"
            if model_key not in MODELS:
                model_key = "gemma"
        m = MODELS[model_key]
        engine_model = m["file"]
    except Exception:  # noqa: BLE001
        engine_model = ENGINE_MODEL
    # 2026-08-26 模型适配：gemma 用模型自带 args；2026-09-09：三源统一（对齐 start.ps1 权威值）——
    # budget 200 / -c 32768 / parallel 1 / repeat-penalty 1.15 / last-n 256（与 MODELS 表同款）
    try:
        m_args = MODELS[model_key]["args"]() if model_key in MODELS else None
    except Exception:  # noqa: BLE001
        m_args = None
    if m_args:
        args = [ENGINE_EXE, "-m", engine_model, "--host", ENGINE_HOST, "--port", str(ENGINE_PORT)] + m_args
    else:
        args = [
            ENGINE_EXE, "-m", engine_model, "--host", ENGINE_HOST, "--port", str(ENGINE_PORT),
            "-c", str(ctx), "-ngl", "99", "--parallel", str(ENGINE_PARALLEL),
            "-ctk", "q8_0", "-ctv", "q8_0", "--reasoning", "on",
            # 2026-09-09：三源统一（对齐 start.ps1 权威值）——budget 400→200（权威=用户实测回退后的既定值）
            "--reasoning-budget", "200",
            # 2026-09-09 深夜：三源漂移统一（对齐 start.ps1 权威值）——repeat-penalty/last-n 同款补齐
            # （本分支=MODELS args 不可用时的真实重启参数，非展示串）
            "--repeat-penalty", "1.15", "--repeat-last-n", "256", "--spec-type", "ngram-simple",
        ]
    try:
        # 2026-09-12 审计 N 项：句柄 with 关闭——原先直接 open 交给 Popen，**父进程侧永不关闭**，
        # 每次引擎重启泄漏 2 个句柄（brain 的等价代码早已这么修，这里漏了）。
        # 子进程持有自己继承的那份句柄，不受这里 with 退出影响（与 brain 同款做法）。
        with open(DATA_ROOT / "llama-server.log", "ab") as _lf, \
                open(DATA_ROOT / "llama-server.err.log", "ab") as _lef:
            subprocess.Popen(
                args,
                stdout=_lf,
                stderr=_lef,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        # taskkill 会连带杀掉 embedding 实例（同一 exe）——探测 EMBED_PORT，死了就一并拉起
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"try {{ Invoke-RestMethod '{EMBED_URL}/health' -TimeoutSec 2 | Out-Null; exit 0 }} catch {{ exit 1 }}"],
                capture_output=True, timeout=10,
            )
            embed_alive = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"if (Test-NetConnection -ComputerName {ENGINE_HOST} -Port {EMBED_PORT} -InformationLevel Quiet) {{ exit 0 }} else {{ exit 1 }}"],
                capture_output=True, timeout=10,
            ).returncode == 0
        except Exception:  # noqa: BLE001
            embed_alive = False
        if not embed_alive:
            from pathlib import Path
            embed_exe = ENGINE_EXE
            embed_model = Path(EMBED_MODEL_FILE)
            if embed_model.is_file():
                # 同上一处：句柄 with 关闭（父进程侧泄漏 2 个句柄/次）
                with open(DATA_ROOT / "embedding.log", "ab") as _lf2, \
                        open(DATA_ROOT / "embedding.err.log", "ab") as _lef2:
                    subprocess.Popen(
                        [embed_exe, "-m", str(embed_model), "--host", ENGINE_HOST, "--port", str(EMBED_PORT),
                         "--embedding", "-c", "8192", "-ngl", "0", "--parallel", "1"],
                        stdout=_lf2,
                        stderr=_lef2,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                logger.warning("embedding server restarted alongside engine")
        _LAST_RESTART = time.time()
        logger.warning("engine restarted: ctx={} (reasoning on)", ctx)  # 预算值不再入日志字面量（防展示漂移复活——实际值由三源一致性断言守护）
        return True, f"引擎已重启（总 ctx={ctx}，加载约 15 秒）。"
    except Exception as e:  # noqa: BLE001
        logger.exception("engine restart failed")
        return False, f"引擎重启失败: {type(e).__name__}: {e}"


async def _engine_watchdog():
    """后台守护：每 60 秒探测引擎健康，离线自动拉起（5 分钟防抖）。顺带守护 embedding 服务。"""
    while True:
        await asyncio.sleep(WATCHDOG_INTERVAL)
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(f"{ENGINE_URL}/health")
                ok = r.status_code == 200
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            # 主引擎正常时顺带检查 embedding 服务（记忆语义检索，掉线自动拉起）
            try:
                async with httpx.AsyncClient(timeout=3) as c:
                    r2 = await c.get(f"{EMBED_URL}/health")
                    if r2.status_code != 200:
                        raise RuntimeError("embedding offline")
            except Exception:  # noqa: BLE001
                logger.warning("embedding service offline, auto starting...")
                try:
                    subprocess.Popen(
                        ["powershell", "-NoProfile", "-File", str(TOOLS_ROOT / "start-embedding.ps1")],
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("embedding auto-start failed: %s", e)
            continue
        # 2026-09-08 D5-2 智能探测：/health 失败可能是"加载中/慢"而非离线——曾因此误判重启
        # （引擎 503 Loading model 窗口 2-3 分钟，watchdog 首轮失败即重启→延长窗口+二次杀）；
        # 二次确认（20s×2）通过则继续循环；真离线才进重启冷却
        _confirmed = False
        for _ in range(2):
            await asyncio.sleep(20)
            try:
                async with httpx.AsyncClient(timeout=5) as c:
                    if (await c.get(f"{ENGINE_URL}/health")).status_code == 200:
                        _confirmed = True
                        break
            except Exception:  # noqa: BLE001
                continue
        if _confirmed:
            logger.warning("engine health transient (loading/slow), confirmed alive — skip restart")
            continue
        if time.time() - _LAST_RESTART < WATCHDOG_MIN_RESTART_GAP:
            logger.warning(
                "engine offline, within restart cooldown (%.0fs left)",
                WATCHDOG_MIN_RESTART_GAP - (time.time() - _LAST_RESTART),
            )
            continue
        logger.warning("engine health check FAILED, auto restarting...")
        await asyncio.to_thread(restart_engine)


# ---------------- 语音推送队列（后台循环投递：调试用语音直推管理员 QQ） ----------------
VOICE_PUSH_FILE = DATA_ROOT / "voice_push_queue.json"


def _load_push_queue() -> list:
    try:
        return json.loads(VOICE_PUSH_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []


def _save_push_queue(jobs: list):
    VOICE_PUSH_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomics.write_json_atomic(VOICE_PUSH_FILE, jobs)


async def _voice_push_loop():
    """每 5 秒检查推送队列：把 amr/wav 语音文件作为 record 私聊发给指定 QQ（投递失败保留重试）。"""
    while True:
        await asyncio.sleep(5)
        try:
            # 2026-09-09 深夜盲审 P1-1：gal 停用门必须覆盖后台语音推送面（proactive 同款）——
            # gal 期间整轮跳过（队列不消费、不重试、不发送），gal 关闭后照常恢复。
            try:
                from plugins import webgal as _wg_push_gate

                if _wg_push_gate.get_mode() == "gal":
                    continue
            except Exception as _wg_push_e:  # noqa: BLE001
                logger.debug("webgal voice push gate skip: {} [{}]", _wg_push_e, type(_wg_push_e).__name__)
            jobs = _load_push_queue()
            if not jobs:
                continue
            request_id = jobs[0].get("qid", "")
            try:
                from nonebot import get_bot

                bot = get_bot()
            except Exception:  # noqa: BLE001
                continue  # 未连接 NapCat，下轮再试
            remain = []
            for job in jobs:
                f = job.get("file", "")
                uid = job.get("user_id")
                cap = job.get("caption", "")
                try:
                    if not f or not os.path.exists(f):
                        logger.warning("voice push missing file: %s", f)
                        continue  # 文件不存在：丢弃该任务
                    with open(f, "rb") as fh:
                        b64 = base64.b64encode(fh.read()).decode("ascii")
                    msg = Message()
                    if cap:
                        msg.append(MessageSegment.text(cap))
                    msg.append(MessageSegment.record(file=f"base64://{b64}"))
                    resp = await asyncio.wait_for(
                        bot.call_api("send_private_msg", user_id=int(uid), message=msg),
                        timeout=25,
                    )
                    logger.info("voice push sent: qid=%s file=%s -> %s resp=%s", request_id, os.path.basename(f), uid, str(resp)[:120])
                    await asyncio.sleep(1)  # 间隔，避免连发
                except Exception as e:  # noqa: BLE001
                    logger.warning("voice push failed: %s -> %s (%s: %s)", os.path.basename(f), uid, type(e).__name__, e)
                    # 2026-09-07 P2：毒丸任务曾永久重试（每 5s 整文件重发一次，永不放弃）——计次上限 5 后丢弃并升级日志
                    job["attempts"] = int(job.get("attempts", 0) or 0) + 1
                    if job["attempts"] >= 5:
                        logger.error("voice push dropped after {} attempts: qid={} -> uid={}", job["attempts"], request_id, uid)
                    else:
                        remain.append(job)
            _save_push_queue(remain)
        except Exception as e:  # noqa: BLE001
            logger.debug("voice push loop: %s", e)


# 循环启动统一由 bot.py 的 @driver.on_startup 钩子执行（2026-08-26：插件自身的
# on_startup/on_bot_connect 在加载时序下不可靠，已移除——见 bot.py _start_background_loops）
_LOOPS_STARTED = False


async def start_background_loops_async() -> None:
    """主事件循环启动全部后台任务（2026-09-06 起：原"线程内 asyncio"会导致 LangGraph/AsyncSqliteSaver
    绑定线程 loop → 主 loop 跑 run_agent 报 `Lock bound to a different event loop` → 群里 @bot 无反应。
    循环内均为 async（LLM/DB 都是 await），不会阻塞 uvicorn。bot.py @driver.on_startup 调用。"""
    global _LOOPS_STARTED
    if _LOOPS_STARTED:
        return
    _LOOPS_STARTED = True

    # 2026-09-12 审计 N 项：六个常驻守护循环原先都是**裸 create_task**——asyncio 只持弱引用，
    # 任务可能在两次 await 之间被 GC 回收，表现为"某个后台功能从此不再运行"（看门狗不再拉引擎、
    # lifesim 不再走时间、主动消息不再触发……全都无声）。改走 core.bgtasks.spawn 持续持引用。
    bgtasks.spawn(_daily_proactive_loop())
    bgtasks.spawn(_review_loop())
    bgtasks.spawn(_engine_watchdog())
    bgtasks.spawn(_voice_push_loop())
    bgtasks.spawn(_metrics_loop())
    bgtasks.spawn(lifesim.lifesim_loop())
    logger.info("background loops started (main loop)")


# ---- 主动对话调度（2026-09-06 起无机器闸门：找不找/频率全由 agent 自决）----
# 2026-09-08 P3：删除 6 个已废止零引用常量（PROACTIVE_START/END_HOUR、PROACTIVE_DAILY_MAX、
# PROACTIVE_PROB、PROACTIVE_COOLDOWN、USER_ACTIVE_WINDOW、PROACTIVE_SKIP_FORCE——
# 注释称"仅供感知统计"但感知/统计也从不读取）
PROACTIVE_FILE = DATA_ROOT / "proactive_state.json"
PROACTIVE_INTERVAL = 300     # 5 分钟检查一次（自决轮询节奏；电脑非全天开机：短间隔覆盖核心工作时间）
PROACTIVE_FIRST_DELAY = 5  # 启动后 5 秒即做首次检查（尽快覆盖）


def _load_proactive() -> dict:
    try:
        return json.loads(PROACTIVE_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_proactive(state: dict):
    PROACTIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
    # C6：写失败留 warning（原子写返回 bool）——曾静默 False，计数/水位丢一拍无从查起
    if not atomics.write_json_atomic(PROACTIVE_FILE, state):
        logger.warning("proactive state save failed: {}", PROACTIVE_FILE)


# 2026-09-08 P-3 生活分享出口：本轮 perceive 挑中的今日 event 候选（uid→event_id）。
# 只挑不记——真正发送成功后才由 _daily_proactive_loop 落账 last_shared_event_id（防复读）；
# agent 自决沉默=没有分享，同一条下轮仍可再被挑中（讲不讲由 agent 自决）。
_LAST_SHARED_CAND: dict = {}


def _pick_today_event(events: list[dict], since_utc: str, watermark: int = 0):
    """P-3：今日（ts>=since_utc，UTC ISO 字典序可比）且尚未分享过（id>watermark）的最新一条 event；
    无则返回 None。纯函数（回归用）：events 传 db.get_events 的结果（已按 id 倒序，最新在前）。"""
    for e in events or []:
        try:
            if str(e.get("ts") or "") >= since_utc and int(e.get("id", 0) or 0) > int(watermark or 0):
                return e
        except (TypeError, ValueError):
            continue
    return None


async def _build_proactive_msg(state_note: str = "", uid: str = "") -> str | None:
    """主动消息内容：**agent greet 域自决**（2026-09-06 起：找不找/说什么全由 agent 判断）。
    感知：当前时间/距上次交流时长/今日已主动次数/TA 最近是否活跃/生活状态/近期私聊/心情/心跳"想找TA"。
    state_note：当前特殊状态名列表（2026-09-08：改传状态事实文本——曾传 bool 且文案恒写
    死板的情欲措辞，口球/催眠等任意状态都会被注成情欲，判定与演出口径错位）。
    （pending_note 管道已随 2026-09-08 深夜硬门裁决退役——未回复候选在调用前整轮跳过）
    返回台词文本；agent 自决【不想找】或生成失败 → None（调用方不发送，无机器回退）。"""
    # 2026-09-10 审计 P3：删硬编码 QQ 兜底——SUPERUSERS 空=无从确定主人身份，返回 None（调用方不发送），
    # 绝不拿写死的号码去查库/发消息
    uid = uid or (str(next(iter(SUPERUSERS))) if SUPERUSERS else None)
    if not uid:
        return None
    try:
        from .. import memory as _mem, brain as _brain

        card = _brain._persona_card(uid)
        import datetime as _dtt

        _now = _dtt.datetime.now()
        rows = _mem.db.recent_messages(uid, limit=12, group_id="")
        _parts = [f"（现在是 {_now.hour} 点，周{_now.weekday() + 1}）"]
        if rows:
            _last = str(rows[-1].get("ts") or "")
            try:
                _ago_h = (_now.astimezone() - _dtt.datetime.fromisoformat(_last).astimezone()).total_seconds() / 3600
                if _ago_h > 6:
                    _parts.append(f"（距上次交流约 {_ago_h:.0f} 小时——隔了一夜/很久，已经是新的一天）")
                elif _ago_h > 0:
                    _parts.append(f"（距上次交流约 {_ago_h:.1f} 小时）")
                # 2026-09-06 感知：注入**带发言人**的最近对话记录——bot 自己看记录分辨谁说的
                # （"茶具纹路"事故教训：摘要式"你们最近聊到"不含说话人，自话被当对方输入）
                try:
                    from plugins import brain as _brain_dl

                    _dlt = _brain_dl._dialogue_lines(uid, limit=6)
                    if _dlt:
                        _parts.append("（你们最近的对话「[你（bot）]=你自己说的」：\n" + _dlt + "）")
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                pass
        # 2026-09-06 自决事实：今日已主动次数 + TA 最近是否活跃（事实注记，非闸门）
        try:
            _pst = _load_proactive()
            _ucnt = int((_pst.get("users", {}).get(str(uid), {}) or {}).get("count", 0) or 0)
            if _ucnt:
                _parts.append(f"（今天你已经主动找过TA {_ucnt} 次——这是今天的事实，不是规则；你现在想不想找，看你此刻的心情和处境）")
        except Exception:  # noqa: BLE001
            pass
        try:
            # 2026-09-06 D4/G2b：改走已取 rows（旧 _conn 直查已删）
            _latest_user = next((m for m in reversed(rows) if m.get("role") == "user"), None)
            if _latest_user and _latest_user.get("ts"):
                if time.time() - float(_dtt.datetime.fromisoformat(str(_latest_user["ts"])).timestamp()) < 1800:
                    _parts.append("（TA 最近半小时内正在跟你聊——很活跃；要不要这时候打断，你自己掂量）")
        except Exception:  # noqa: BLE001
            pass
        # 2026-09-08 活人感 3-2 作息感知：注入「TA 的活跃时段」事实（近 14 天 TA 发言的本地小时
        # 直方图归纳；store 侧模块级缓存 TTL 1h——每 5 分钟一轮 perceive 都重算直方图纯属浪费）。
        # 用户裁决口径：作息=感知事实，**不做机器时窗**——现在合不合适找TA，agent 自己拿捏；
        # 样本不足（<10 条）summarize 返回空串即不注入（宁缺毋滥）。
        try:
            from ..memory import store as _mstore

            _ah = _mstore.summarize_active_hours(_mem.db.get_active_hours(uid))
            if _ah:
                _parts.append(f"（TA 通常在这些时段活跃：{_ah}——现在找不找TA，你自己拿捏）")
        except Exception as _ahe:  # noqa: BLE001
            logger.debug("active-hours perceive skip: {}", _ahe)
        # 2026-09-05 防重：上次主动说过什么（别再说一样的内容/新闻）
        try:
            from agent import tools as _atools_g

            _gl = _atools_g.recall("greet:" + uid, persona=_brain._persona_name(uid))
            if _gl:
                _parts.append("（上次主动找TA说过：" + _gl[:90] + "——这次说点不同的，别重复。）")
        except Exception:  # noqa: BLE001
            pass
        try:
            from agent import lifesim as _ls2

            _lt = _ls2.life_text()
            if _lt:
                _parts.append(f"（你此刻的生活：{_lt}）")
        except Exception:  # noqa: BLE001
            pass
        # 2026-09-08 P-3 生活分享出口：今日 events 里挑一条尚未分享过的（想讲就讲，不想讲就不讲）。
        # 窗口=本地今日 0 点起（换算 UTC 后与事件 ts 同刻度比较）；防复读=last_shared_event_id
        # 记账（发送成功后才落，见 _daily_proactive_loop）；取不到写"无"。
        try:
            _p3_since = _now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(_dtt.timezone.utc).isoformat()
            _p3_wm = int(_load_proactive().get("last_shared_event_id", 0) or 0)
            _p3_ev = _pick_today_event(_mem.db.get_events(uid, limit=30), _p3_since, _p3_wm)
            _p3_txt = str(_p3_ev["content"]).strip()[:60] if _p3_ev else "无"
            _parts.append(f"（今天你生活里新鲜的事：{_p3_txt}——想讲就自然讲，不想讲就不讲）")
            if _p3_ev:
                _LAST_SHARED_CAND[str(uid)] = int(_p3_ev["id"])
        except Exception as _p3e:  # noqa: BLE001
            logger.debug("life share perceive skip: {}", _p3e)
        # 2026-09-05：事务融入——主动找TA时，心里记挂的约定自然成为开场素材
        try:
            _pp = _brain._pending_mention_text(uid)
            if _pp:
                _parts.append("（你心里记挂着答应过TA的事：" + _pp.replace("\n\n【你的约定】", "").strip()[:120] + "——如果想不起别的，就自然提起它）")
        except Exception:  # noqa: BLE001
            pass
        # 2026-09-06 关系修正：白名单/普通用户绝不能被称呼「主人」（主人=管理员专属）
        try:
            _su = __import__("plugins.debug", fromlist=["SUPERUSERS"]).SUPERUSERS
            # 2026-09-10 审计 P3：删硬编码 QQ 兜底——空集=无从判定主人身份，跳过该注记（不猜号）
            _owner_id = str(next(iter(_su))) if _su else None
            if _owner_id and str(uid) != _owner_id:
                _parts.append("（对方是你亲近/熟悉的人，但**不是你的主人**——绝对不要称呼对方「主人」，按你们实际关系自然称呼）")
        except Exception:  # noqa: BLE001
            pass
        # 2026-09-06 重启中断续话：TA最后一条还没接（重启打断/没顾上看）→ 自决是否接、何时接
        # （30 分钟窗口=真人节奏：过时话题自然淡出，不再挂念）
        try:
            _ru = next((m for m in reversed(rows) if m.get("role") == "user"), None)
            _la = next((m for m in reversed(rows) if m.get("role") == "assistant"), None)  # 2026-09-08：曾命名 _re 遮蔽模块级 re（脆弱写法）
            if _ru and str(_ru.get("content") or "").strip():
                _ut = float(_dtt.datetime.fromisoformat(str(_ru.get("ts"))).timestamp())
                _et = float(_dtt.datetime.fromisoformat(str(_la.get("ts"))).timestamp()) if _la and _la.get("ts") else 0.0
                if _ut > _et and (time.time() - _ut) < 1800:
                    _parts.append(
                        "（TA 最后说「" + str(_ru["content"])[:40] + "」，你还没接话——像是被打断的。"
                        "想接就自然接一句（结合你们关系与话题），待会儿再回也可以，不想接也不用勉强。）"
                    )
        except Exception:  # noqa: BLE001
            pass
        # 2026-09-05：心跳"想找TA" —— 生活里自然想找TA说话/有事想商量（自决信号）
        # 2026-09-06 措辞修正：感知必须不含「想找」字样（曾把"（你心里正想找TA…想找就开口）"当台词复述——"想找"事故）
        try:
            from agent import lifesim as _lss3

            _wl2 = _lss3._load_state().get("want_talk")
            if _wl2:
                _parts.append("（此刻你心里有个念头：有话想对TA说——这是你的心情，不是要说出口的话。）")
            else:
                _parts.append("（此刻你心里没有特别想找TA的念头——不找也是完全自然的；真想找，你要有属于自己的理由。）")
        except Exception:  # noqa: BLE001
            pass
        if state_note:
            # 2026-09-08：按真实状态名注入事实（agent 自决分寸）——不再硬编码单一情欲措辞
            _parts.append(f"（你身上此刻有特殊状态：{state_note}——这是你此刻的真实处境；"
                          "要不要这时候主动找TA、以什么分寸开口，你自己把握）")
        else:
            _parts.append("（状态：日常）")
        # pending_note 注入块已随硬门退役（未回复候选到不了本函数——2026-09-08 深夜裁决）
        # 2026-09-08 盲审修正：未兑现承诺的感知注入已由本函数前段既有块承担（_pending_mention_text），
        # 此处不再重复注入（曾同函数两次注入同一承诺 → greet 感知污染）
        # 2026-09-06 决策锚：感知开头置关键行（模型先读——长感知里 gemma 容易忽略"已找N次/TA活跃"）
        _ctx_text = "\n".join(_parts)
        try:
            _anch = [x for x in _parts if ("你此刻的生活" in x or "想找" in x or "念头" in x or "心情" in x or "正在" in x)]
            if _anch:
                _ctx_text = "（此刻：" + "；".join(a.strip("（）") for a in _anch[:2]) + "）\n" + _ctx_text
        except Exception:
            pass  # 兜底：_ctx_text 上一行已赋好值，try 里只是叠"此刻"锚行；失败就用原值（少个提示，不缺内容）
        logger.info("proactive perceive: {}", _ctx_text.replace("\n", " | ")[:700])  # 2026-09-06 感知可观测（⏎ 不为 GBK 控制台可编码，改 | ）

        from agent import run_agent

        _st = await run_agent(
            {
                "kind": "greet", "user_id": uid, "group_id": "", "text": "",
                "is_group": False, "mentioned": False, "continuation": False,
                "persona": _brain._persona_name(uid),
                "context": {"proactive_ctx": _ctx_text, "card": card, "mood": "", "ctx_lines": [], "ctx_text": ""},
            },
            thread_id=(("greet:%s") % (str(uid) + ":" + str(brain._persona_switch_ts(uid) or int(time.time())))),

        )
        if _st and _st.get("decision_action") == "speak":
            msg = str(_st.get("final_line") or "").strip()
            msg = msg.replace("**", "").strip()
            if len(msg) >= 2:
                return msg
    except Exception as _pa:  # noqa: BLE001
        # 2026-09-07 P2：曾裸吞——链路坏了（图配置/引擎 5xx）与"agent 自决不找"日志完全一样，极难定位
        logger.warning("proactive agent FAILED (非自决沉默，需关注): {} [{}]", _pa, type(_pa).__name__)
        return None
    return None  # 2026-09-06：agent 自决不找 / 生成失败 → 不发（找不找TA全由 agent 自决，无机器回退）


# ---------------- 每日评测（监测层：客观指标，机器统计；不碰表达自决） ----------------
METRICS_FILE = DATA_ROOT / "metrics_daily.json"
METRICS_WINDOW_H = 24.0


def _compute_metrics() -> dict:
    """最近 24h 客观指标：消息量/平均长/复读率/响应延迟/情绪分布。失败返回空 dict。"""
    out: dict = {}
    try:
        from datetime import datetime, timedelta, timezone as _tz

        cutoff = (datetime.now(_tz.utc) - timedelta(hours=METRICS_WINDOW_H)).isoformat(timespec="seconds")

        def _ts(s: str) -> float:
            try:
                return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
            except Exception:  # noqa: BLE001
                return 0.0

        # 2026-09-07 P2：曾 recent_messages("") ——user_id="" 匹配不到任何行，评测功能整体死亡
        rows = memory.db.recent_messages_all(limit=5000)
        msgs = [(m.get("role"), (m.get("content") or ""), m.get("ts") or "") for m in rows if (m.get("ts") or "") >= cutoff]
        asst = [(c, t) for r, c, t in msgs if r == "assistant" and c.strip()]
        if asst:
            lens = [len(c) for c, _ in asst]
            out["msgs"] = len(asst)
            out["avg_len"] = round(sum(lens) / len(lens), 1)
            # 复读率：与上一条 bot 消息 2-gram 重叠 >=0.7 视为骨架重复（≥25 字才判，防短句误伤）
            dup = 0
            judged = 0
            prev = ""
            for c, _ in asst:
                if len(c) >= 25 and prev:
                    a = {c[i:i + 2] for i in range(len(c) - 1)}
                    b = {prev[i:i + 2] for i in range(len(prev) - 1)}
                    if b:
                        judged += 1
                        if len(a & b) / len(b) >= 0.7:
                            dup += 1
                prev = c
            out["dup_rate"] = round(dup / judged, 3) if judged else 0.0
            # 响应延迟：user→下一条 assistant 的间隔（中位数）
            _d = []
            _last_user_ts = 0.0
            for r, c, t in msgs:
                _t2 = _ts(t)
                if r == "user":
                    _last_user_ts = _t2
                elif r == "assistant" and _last_user_ts and _t2 > _last_user_ts:
                    _d.append(_t2 - _last_user_ts)
                    _last_user_ts = 0.0
            if _d:
                _d.sort()
                out["median_delay_s"] = round(_d[len(_d) // 2], 1)
        try:
            out["emotions"] = memory.db.get_emotion_stats(METRICS_WINDOW_H)
        except Exception:  # noqa: BLE001
            pass
        out["window_h"] = METRICS_WINDOW_H
        out["ts"] = time.time()
    except Exception as _e:  # noqa: BLE001
        logger.debug("metrics compute failed: %s", _e)
    return out


def _save_metrics(m: dict):
    try:
        METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomics.write_json_atomic(METRICS_FILE, m)
    except Exception:  # noqa: BLE001
        pass


def _load_metrics() -> dict:
    try:
        if METRICS_FILE.exists():
            return json.loads(METRICS_FILE.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        pass
    return {}


def _metrics_text() -> str:
    m = _load_metrics()
    if not m:
        return "评测：还没有数据（每日统计在下一天凌晨生成）。"
    lines = [f"评测（最近 {m.get('window_h', 24):.0f} 小时）："]
    if m.get("msgs") is not None:
        lines.append(f"· bot 消息 {m['msgs']} 条，平均 {m['avg_len']} 字")
        lines.append(f"· 骨架复读率 {m.get('dup_rate', 0) * 100:.1f}%（≥25字消息）")
        if m.get("median_delay_s") is not None:
            lines.append(f"· 响应延迟中位 {m['median_delay_s']}s")
    emo = m.get("emotions") or {}
    if emo:
        top = "、".join(f"{k}×{v}" for k, v in list(emo.items())[:5])
        lines.append(f"· 情绪分布：{top}")
    return "\n".join(lines)


async def _metrics_loop():
    """每日评测：每 30 分钟检查一次，跨天即计算最近 24h 指标（写入 metrics_daily.json）。"""
    while True:
        try:
            await asyncio.sleep(1800)
            m = _load_metrics()
            _last_day = str(m.get("day") or "")
            _today = time.strftime("%Y-%m-%d")
            if _last_day != _today:
                _m = _compute_metrics()
                _m["day"] = _today
                _save_metrics(_m)
                logger.info("metrics daily saved: %s", {k: v for k, v in _m.items() if k != "ts"})
        except asyncio.CancelledError:
            raise
        except Exception as _e:  # noqa: BLE001
            logger.debug("metrics loop skipped: %s", _e)


async def _daily_proactive_loop():
    """主动找人说话（主人 + 亲密白名单成员；2026-09-06 全 agent 自决）。
    **无机器冷却/概率/强制闸门**：「找不找TA、什么时候找、说什么」由 greet 域 agent 看感知自决
    （距上次交流、TA 最近是否活跃、今天已主动次数、心跳"想找TA"、生活状态）。
    关系语义（2026-09-07 扩展到 owner）：候选被主动找后**没回复则不追问**（等 TA 先回复才有下次机会，
    owner 群里说话也算回复）；候选顺序每轮洗牌；每 5 分钟检查一轮（每个候选一次自决调用）；
    发送后记 count/last_sent/probed。"""
    from datetime import datetime

    owner = next(iter(SUPERUSERS), None)
    await asyncio.sleep(PROACTIVE_FIRST_DELAY)  # 开机即首检

    def _replied_after(uid: str, ts: float) -> bool:
        """该用户在 ts 之后有没有回复过（有 user 消息）——白名单「没回复不追问」判定。"""
        try:
            _ts = memory.db.last_user_message_ts(uid)  # 2026-09-06：替代绕 _conn 直查
            if not _ts:
                return False
            return float(datetime.fromisoformat(_ts).timestamp()) > ts
        except Exception:  # noqa: BLE001
            return False

    while True:
        try:
            # 2026-09-09 GAL gal 门（用户裁决 v2 #2 主动面①）：gal 模式 QQ bot 停用——主动消息循环
            # 整轮暂停（候选评估与发送全不进；心跳/生活状态照常，时间仍在流动）。
            # 本循环的 bot 来自 get_bot()（真实 bot，非 webgal CaptureBot）——直接判全局模式即可。
            try:
                from plugins import webgal as _wg_gate

                if _wg_gate.get_mode() == "gal":
                    await asyncio.sleep(PROACTIVE_INTERVAL)
                    continue
            except Exception as _wg_e:  # noqa: BLE001
                logger.debug("webgal proactive gate skip: {} [{}]", _wg_e, type(_wg_e).__name__)
            now = datetime.now()
            state = _load_proactive()
            today = now.date().isoformat()
            if state.get("date") != today:
                # 2026-09-07 P2：跨天只重置当日计数——probed（"已主动过未回复"标记）曾随 users 整体丢弃，
                # 白名单"未回复不追问"保护跨午夜失效（23:50 找过、00:05 又追同一人）
                _old_probed = {u: e.get("probed", 0.0)
                               for u, e in (state.get("users") or {}).items() if e.get("probed")}
                # C5：跨天携带 last_shared_event_id——它是"已分享到哪条"的滚动水位（防复读），
                # 不是按天量；随日期重置曾让同一条生活事件第二天再被挑中重讲一遍
                _old_shared = int(state.get("last_shared_event_id", 0) or 0)
                state = {"date": today, "count": 0,
                         "users": {u: {"probed": t} for u, t in _old_probed.items()}}
                if _old_shared:
                    state["last_shared_event_id"] = _old_shared
                # C12：跨天顺带清扫 brain 模块级缓存 dict（>7 天不活跃键）与 data/*.tmp 残壳
                try:
                    brain._sweep_stale()
                except Exception as _swe:  # noqa: BLE001
                    logger.debug("stale sweep skip: {} [{}]", _swe, type(_swe).__name__)
            users = state.setdefault("users", {})
            # ---- 候选：主人 + 白名单成员（白名单未回复不追问）----
            try:
                from .. import brain as _brainm

                _wl = [str(x) for x in _brainm._load_intimate_wl() if str(x) and str(x) != str(owner)]
            except Exception:  # noqa: BLE001
                _wl = []
            _cands = []
            # 2026-09-08 深夜用户裁决：「上次主动未回复」= 硬门（候选循环整轮跳过，连 LLM 评估都不进）。
            # （09-08 午后曾裁决改"感知事实注入"，被本裁决取代——不扫描就无需知情；TA 回复后自然恢复候选）
            _unanswered: set[str] = set()
            if owner:
                _uo = users.setdefault(str(owner), {"count": 0, "last_sent": 0.0, "probed": 0.0})
                try:
                    if bool(float(_uo.get("probed", 0) or 0)) and not _replied_after(str(owner), float(_uo["probed"])):
                        _unanswered.add(str(owner))
                except Exception:  # noqa: BLE001
                    pass
                _cands.append((str(owner), False))
            # 2026-09-06 用户裁决：候选=主人+亲密白名单（非白名单用户由 TA 先开口时 reply 域回应；
            # 旧"扫描全部私聊用户"块因引用未定义的 _mem 实为死代码，已删）
            for _w in _wl:
                _uw = users.setdefault(_w, {"count": 0, "last_sent": 0.0, "probed": 0.0})
                try:
                    if float(_uw.get("probed", 0) or 0) and not _replied_after(_w, float(_uw["probed"])):
                        _unanswered.add(_w)
                except Exception:  # noqa: BLE001
                    pass
                _cands.append((_w, True))
            # 2026-09-07：候选顺序每轮洗牌（曾固定 owner 优先 → 周期+对象双固定=机械感来源）
            random.shuffle(_cands)
            # ---- 逐个候选交给 agent 自决（每轮最多发一个）----
            for uid, is_wl in _cands:
                # 2026-09-08 深夜用户裁决：上次主动未回复 → 不再扫描评估（该候选连 LLM 自决都不进）。
                # 引擎争用实测：每 5 分钟对未回复候选照跑 perceive+judge 是纯浪费；
                # 取代 09-08 午后"硬门改感知事实"裁决——不扫描就无需知情，TA 回复后自然重新入选
                if str(uid) in _unanswered:
                    logger.info("proactive: skip (上次未回复，不再扫描) uid={}", uid)
                    continue
                u = users.setdefault(uid, {"count": 0, "last_sent": 0.0, "probed": 0.0})
                # 2026-09-08：状态按真实状态名注入（曾 bool(_sp9.active()) 一刀切 + 文案恒"情欲正盛"）
                _state_note = ""
                if not is_wl:
                    try:
                        from core import special as _sp9

                        _act9 = _sp9.active(card=brain.active_card_key())
                        # 2026-09-09 盲审 P3：开放道具 kind 的展示名在条目 label（LABELS.get 拿不到）
                        _state_note = "、".join(str(e.get("label") or _sp9.LABELS.get(k, k)) for k, e in _act9.items())
                    except Exception:  # noqa: BLE001
                        _state_note = ""
                msg = await _build_proactive_msg(state_note=_state_note, uid=uid)
                msg = str(msg or "").replace("**", "").strip()
                if len(msg) < 2:
                    logger.info("proactive: agent self-silent uid={} (自决不找)", uid)
                    continue
                # 2026-09-06 静观型回复拦截："无需插话/静观其变/作为…的我"等句意=不参与 →
                # 这种"选择性沉默"的叙述不应作为消息发出
                # 2026-09-06 撞车防护：poke/刚回复过（15 秒内）→ 这轮跳过（避免"重复回复"）
                try:
                    _pt = getattr(brain, "_POKE_TS", {}).get(str(uid), 0)
                    if _pt and (time.time() - _pt) < 15:
                        logger.info("proactive: skip (poke 刚回复 15s 内) uid={}", uid)
                        continue
                except Exception as _pe:  # noqa: BLE001
                    # 2026-09-06 修复：原 except 里误带 continue——任何异常都会静默跳过该候选
                    logger.warning("proactive poke-ts check failed: {} [{}]", _pe, type(_pe).__name__)
                from nonebot import get_bot

                bot = get_bot()
                await bot.send_private_msg(user_id=int(uid), message=msg)
                try:
                    from agent import tools as _atools

                    _atools.remember("greet:" + str(uid), str(msg)[:60], persona=brain._persona_name(uid))
                except Exception:  # noqa: BLE001
                    pass
                state["count"] = int(state.get("count", 0)) + 1
                u["count"] = int(u.get("count", 0)) + 1
                u["last_sent"] = now.timestamp()
                u["probed"] = now.timestamp()  # 2026-09-07：owner 也记「已主动」（未回复则本轮后跳过）
                # 2026-09-08 P-3：真的分享出去了才落账 last_shared_event_id（防复读）——
                # perceive 挑中但 agent 自决沉默的不算分享，不推进水位
                try:
                    _p3_id = _LAST_SHARED_CAND.pop(str(uid), None)
                    if _p3_id:
                        state["last_shared_event_id"] = int(_p3_id)
                except Exception as _p3s:  # noqa: BLE001
                    logger.debug("life share bookkeeping skip: {}", _p3s)
                # 清心跳"想找TA"标记（已用掉）
                try:
                    from agent import lifesim as _lss2

                    _s2 = _lss2._load_state()
                    if _s2.get("want_talk"):
                        _s2["want_talk"] = False
                        _lss2._save_state(_s2)
                except Exception:  # noqa: BLE001
                    pass
                _save_proactive(state)
                logger.info("proactive sent: uid={} count={} state_note={}", uid, state["count"], _state_note)
                break  # 2026-09-06：每轮只发给一个候选（顺序已洗牌）
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            import traceback as _tb

            if "no bots" in str(e).lower():
                logger.debug("proactive loop: bot 未连接（启动/断线期），跳过本轮")  # 良性
            else:
                logger.warning("proactive loop skipped: {}", repr(e)[:200])
                logger.debug("proactive loop trace:\n" + "".join(_tb.format_exc()))
        await asyncio.sleep(PROACTIVE_INTERVAL)


async def _review_loop():
    """复盘循环（每 1 小时检查）：每日 ≥10 点首个整点刻跑**昨日**日复盘（整天对话提取 facts+review）；
    每周日 ≥10 点跑周复盘（本周每日复盘 → 周总结，增强记忆库）。幂等键按超管用户分别存。"""
    REVIEW_FILE = DATA_ROOT / "review_state.json"

    def _load_r() -> dict:
        try:
            return json.loads(REVIEW_FILE.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_r(state: dict):
        REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomics.write_json_atomic(REVIEW_FILE, state)

    while True:
        await asyncio.sleep(3600)
        try:
            import datetime as _dt

            now = _dt.datetime.now()
            st = _load_r()
            if not SUPERUSERS:
                continue
            # 2026-09-08 P1：复盘对象改「昨天」——曾传 now.date()：10 点执行时当天只过了 10 小时，
            # 幂等键当天落盘后不再跑 → 每天 10:00 之后的对话永远进不了任何一天的日复盘。
            # 改跑昨日整天，与 build_context 注入标签「昨日复盘」名实对齐。
            _yday = (now - _dt.timedelta(days=1)).date().isoformat()
            # 2026-09-08：幂等键按用户存（曾全局单键 daily_date——多超管时首人复盘后其余人整天被跳过）
            for _owner in SUPERUSERS:
                _okey = f"daily_{_owner}"
                # B4（2026-09-09 审计）日复盘补扫：幂等键落后 yesterday 超过 1 天（停机跨天/连续失败）
                # → 每 tick 补跑最旧缺失的一天（一天/tick，幂等键推进防重；仿周复盘补扫结构）。
                # 常规块只认 yesterday，缺口天会永久漏掉（关键对话整天进不了任何日复盘）。
                _catch_day = ""
                _done_day = str(st.get(_okey) or "")
                try:
                    if _done_day:
                        _lag = (_dt.date.fromisoformat(_yday) - _dt.date.fromisoformat(_done_day)).days
                        if _lag > 1:
                            _catch_day = (_dt.date.fromisoformat(_done_day) + _dt.timedelta(days=1)).isoformat()
                except ValueError:
                    _catch_day = ""  # 幂等键损坏/缺失：无从定基线，只走常规块（避免全库回扫）
                if _catch_day:
                    # 空天无可复盘——直接推进水位（否则 run_daily_review 返回 False 会把补扫永久卡死）
                    try:
                        _has_txt = bool(memory.db.get_day_messages_text(_owner, _catch_day))
                    except Exception:  # noqa: BLE001
                        _has_txt = True
                    if not _has_txt:
                        st[_okey] = _catch_day
                        _save_r(st)
                        logger.info("daily review catch-up skip empty day: owner=%s date=%s", _owner, _catch_day)
                    else:
                        # 失败不写幂等键，下 tick 重试同一天
                        _cok = await memory.run_daily_review(_owner, _catch_day)
                        if _cok:
                            st[_okey] = _catch_day
                            _save_r(st)
                            logger.info("daily review catch-up done: owner=%s date=%s", _owner, _catch_day)
                        else:
                            logger.warning("daily review catch-up failed, retry next tick: owner=%s %s", _owner, _catch_day)
                    continue  # 本 tick 该用户已消费于补扫；追平后常规块自然接手 yesterday
                if now.hour >= 10 and st.get(_okey) != _yday:
                    # 失败不写幂等键（曾失败也标记 → 当天复盘静默丢失；下轮整点自动重试）
                    _ok = await memory.run_daily_review(_owner, _yday)
                    if _ok:
                        st[_okey] = _yday
                        _save_r(st)
                        logger.info("daily review scheduled done: owner=%s date=%s", _owner, _yday)
                    else:
                        logger.warning("daily review failed this hour, will retry next tick: owner=%s %s", _owner, _yday)
            # 2026-09-09 agent_checkpoints.db 无界增长治理：每日一次裁剪（幂等键 checkpoints_date，
            # 同周/日复盘模式：键不等当天才跑）。同步 sqlite 直连 → to_thread 防阻塞循环；
            # prune 自身全程吞错（失败带 error 字段统计），此处再兜一层只记日志，绝不影响复盘链。
            try:
                _cp_today = time.strftime("%Y-%m-%d")
                if st.get("checkpoints_date") != _cp_today:
                    from agent import graph as _graph_cp

                    _cp_stat = await asyncio.to_thread(_graph_cp.prune_checkpoints)
                    st["checkpoints_date"] = _cp_today
                    _save_r(st)
                    logger.info("checkpoints prune done: {}", _cp_stat)
            except Exception as _cp_err:  # noqa: BLE001
                logger.debug("checkpoints prune skip: {}", _cp_err)
            # 2026-09-07 生活日记：把昨天的生活轨迹汇成第一人称日记（梦/晨间连续性的素材源）
            try:
                from agent import lifesim as _lsd

                _yd = (now - _dt.timedelta(days=1)).date().isoformat()
                _diary = await _lsd.write_daily_diary(_yd)
                if _diary:
                    logger.info("life diary done: {}…", _diary[:40])
            except Exception as _de:  # noqa: BLE001
                logger.debug("life diary skip: {}", _de)
            # 2026-09-07 群画风定性：每群每日一次（24h 龄期；2026-09-08 深夜用户裁决，计数触发已删）
            try:
                await brain.refresh_all_group_styles()
            except Exception as _ge:  # noqa: BLE001
                logger.debug("group style pass skip: {}", _ge)
            # 每周复盘：周日 ≥10 点首刻跑当周（周一为起始）；错过（周日停机/整日失败）则之后每天补扫
            # 2026-09-08 深夜修复：曾只在周日跑——09-06 重启折腾整日未成 → 该周永久漏掉（断链实锤）
            _sun_done = False
            if now.weekday() == 6 and now.hour >= 10:
                week_start = (now - _dt.timedelta(days=6)).date().isoformat()
                for _owner in SUPERUSERS:
                    _wkey = f"weekly_{_owner}"
                    if st.get(_wkey) != week_start:
                        # 失败不写幂等键，下轮整点重试
                        _wok = await memory.run_weekly_review(_owner, week_start, since_date=week_start)
                        if _wok:
                            st[_wkey] = week_start
                            _save_r(st)
                            logger.info("weekly review scheduled done: owner=%s week=%s", _owner, week_start)
                        else:
                            logger.warning("weekly review failed this hour, will retry next tick: owner=%s", _owner)
                    else:
                        _sun_done = True
            # 补扫：最近一个已过完的周日所在周漏跑 → 任何一天补上（幂等键防重）
            if not _sun_done:
                _last_sun = (now - _dt.timedelta(days=(now.weekday() + 1) % 7)).date()
                if _last_sun < now.date():
                    _week_start = (_last_sun - _dt.timedelta(days=6)).date().isoformat()
                    for _owner in SUPERUSERS:
                        _wkey = f"weekly_{_owner}"
                        if st.get(_wkey) != _week_start:
                            _wok = await memory.run_weekly_review(_owner, _week_start, since_date=_week_start)
                            if _wok:
                                st[_wkey] = _week_start
                                _save_r(st)
                                logger.info("weekly review catch-up done: owner=%s week=%s", _owner, _week_start)
                            else:
                                logger.warning("weekly review catch-up failed, retry next tick: owner=%s", _owner)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.debug("review loop skipped: %s", e)


def _is_debug_cmd(event: MessageEvent) -> bool:
    """Rule：仅 "/" 开头（私聊任意；群聊必须 @bot）且**是已知命令**才触发本 matcher。
    2026-09-03：未知 /xxx 放行给 brain / qq_avatar（否则 /设置头像 这类会被吞）。"""
    if isinstance(event, GroupMessageEvent) and not event.is_tome():
        return False
    text = event.get_plaintext().strip()
    if not text.startswith("/"):
        return False
    # 只拦截"已知"命令：任一解析器命中即 true（含 /人设 /语音 /表情 /语音测试 /指数 /矫正等）
    # 2026-09-07：元组曾缺 parse_voice_test_cmd → /语音测试、/语音日文 到不了本 matcher（handler 白写）
    for parser in (parse_debug_cmd, parse_voice_cmd, parse_persona_cmd, parse_sticker_cmd,
                   parse_whitelist_cmd, parse_voice_test_cmd):
        try:
            r = parser(text)
        except Exception:  # noqa: BLE001
            continue
        if parser is parse_debug_cmd and r != "unknown":
            return True
        if parser is not parse_debug_cmd and r is not None:
            return True
    return False


debug = on_message(rule=Rule(_is_debug_cmd), priority=1, block=True)


def parse_debug_cmd(text: str) -> str:
    """命令解析（纯函数，便于测试）：返回动作名。"""
    parts = text.strip().lstrip("/").split()
    cmd = parts[0].lower() if parts else ""
    mapping = {
        "状态": "status", "status": "status", "状态机": "status",
        "重启引擎": "restart_engine", "restart-engine": "restart_engine",
        "清除": "clear_special", "清除状态": "clear_special", "clear": "clear_special",
        "矫正": "correction", "矫正清除": "correction_clear",
        "群聊动作": "group_action", "群聊动作关": "group_action_off", "群聊动作开": "group_action_on",
        "动作": "group_action", "动作关": "group_action_off", "动作开": "group_action_on",
        "帮助": "help", "help": "help",
        "评测": "metrics", "metrics": "metrics",
        "思考": "think",  # 2026-09-07：缺此键导致 /思考 到不了本 matcher（被 brain 当聊天回复）
    }
    return mapping.get(cmd, "unknown")


def _correction_text(user_id: str) -> str:
    """列出当前矫正规则（/矫正）。"""
    from .. import correction

    rules = correction.list_rules(user_id)
    if not rules:
        return "暂无矫正规则。对话中直接说（如：以后不要说'哼，凡人'；记住：回复简短点）即可添加，/矫正清除 全部移除。"
    lines = [f"【矫正规则】共 {len(rules)} 条（每次回复注入生效）："]
    for i, r in enumerate(rules, 1):
        lines.append(f"{i}. {r['rule']}（生效 {r.get('hits', 0)} 次，来自：{r['source'][:30]}）")
    lines.append("清除全部：/矫正清除")
    return "\n".join(lines)


def parse_voice_cmd(text: str) -> str | None:
    """解析语音开关命令：/语音 开|关|状态。返回参数；非该命令返回 None。"""
    parts = text.strip().lstrip("/").split()
    if not parts or parts[0].lower() not in ("语音", "voice"):
        return None
    return parts[1].strip().lower() if len(parts) > 1 else "状态"


def parse_persona_cmd(text: str) -> str | None:
    """解析人设选择命令：/人设 素世|奥汀|状态。返回卡名或"状态"；非该命令返回 None。"""
    parts = text.strip().lstrip("/").split()
    if not parts or parts[0].lower() not in ("人设", "persona"):
        return None
    return parts[1].strip() if len(parts) > 1 else "状态"


def parse_sticker_cmd(text: str) -> str | None:
    """解析表情包开关命令：/表情 开|关|状态。返回参数；非该命令返回 None。"""
    parts = text.strip().lstrip("/").split()
    if not parts or parts[0].lower() not in ("表情", "sticker", "表情包"):
        return None
    return parts[1].strip().lower() if len(parts) > 1 else "状态"


def parse_voice_test_cmd(text: str) -> tuple[str, str] | None:
    """解析语音调试指令：/语音测试 [文本]（中文）/ 语音日文|语音ja [文本]（日文，自动翻译）。

    返回 (模式, 文本)：("zh"/"ja", 可朗读文本)；非本指令返回 None。
    """
    parts = text.strip().lstrip("/").split(maxsplit=1)
    if not parts:
        return None
    cmd = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""
    if cmd in ("语音测试", "voice-test", "voice_test"):
        return ("zh", rest)
    if cmd in ("语音日文", "语音日语", "语音ja", "voice-ja", "语音曰文"):
        return ("ja", rest)
    return None


def _fmt_time(ts) -> str:
    """2026-09-07 修复：追问模式到期时刻格式化——此前被引用但从未定义（NameError 被
    /状态 handler 外层吞掉，追问激活时 /状态 整条无输出）。"""
    try:
        return time.strftime("%H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError, OSError):
        return "—"


def _build_status_text(user_id: str, group_id: str = "") -> str:
    """汇总当前运行时状态（纯文本，供 /状态）。2026-09-06 重构补齐：人设卡/生活心跳/场景。"""
    lines = ["【当前状态】"]
    # 人设卡 + 生活心跳（重构后新增维度）
    try:
        _pn = brain._persona_name(user_id)
        _pcb = persona.load_persona(_pn)
        lines.append(f"人设卡：{_pcb.get('name') or _pn}（{_pn}）")
    except Exception:
        try:
            lines.append(f"人设卡：{brain._persona_name(user_id)}")
        except Exception:
            pass  # 双层兜底：外层已降级为"只写人设名"，这里再失败就连这行也不写（调试展示，不影响主流程）
    try:
        from agent import lifesim as _ls0
        _ls = _ls0._load_state()
        lines.append(f"场景：{_ls.get('scene') or '—'}｜正在：{_ls.get('doing') or '—'}")
    except Exception:
        pass  # 状态文本尽力而为：少一行展示不影响判断，故不打断拼装
    # 人格模式
    modes = brain._load_modes()
    m = modes.get(user_id, {"mode": 0, "ts": 0.0})
    try:
        _cc = persona.load_persona(brain._persona_name(user_id))
        _mt = str(_cc.get("mode_trigger") or "")
    except Exception:
        _mt = ""
    if _mt == "self":
        lines.append("人格形态：自决（按人设自然流动，无模式开关）")
    else:
        lines.append(f"人格模式：{'恶堕' if m['mode'] == 1 else '善良'}（切换于 {time.strftime('%H:%M', time.localtime(m.get('ts', 0)))}）")
    # 模型 + 文风模式（2026-08-26：切换后可见）
    try:
        from plugins.brain import get_model_key, get_style_mode, MODELS
        _mk = get_model_key(user_id)
        _mk_label = MODELS.get(_mk, {}).get("label", _mk)
        _sty = get_style_mode(user_id)
        _sty_label = "日常口语" if _sty == "daily" else "小说型"
        lines.append(f"模型：{_mk_label}（说「切换gemma」切换）｜文风：{_sty_label}（说「小说模式/正常说话」切换）")
    except Exception:  # noqa: BLE001
        pass

    rel = memory.db.get_relation(user_id)
    if rel:
        _love = f"（爱意 +{brain.OWNER_LOVE_BONUS:.0f}）" if str(user_id) in SUPERUSERS else ""
        lines.append(f"亲密度：{rel['intimacy']:.1f}/100{_love}（心情：{rel['mood'] or '—'}）")
    # 特殊层级状态（2026-09-06：core/special 唯一状态源——指数机已退役）
    try:
        from core import special as _sp

        _act = _sp.active(card=brain.active_card_key())
        if _act:
            for _k, _e in _act.items():
                _lab = _sp.LABELS.get(_k, _k)
                _since = time.strftime("%H:%M", time.localtime(float(_e.get("activated_at", 0))))
                lines.append(f"特殊状态·{_lab}：生效中（{_e.get('note') or '—'}，自 {_since}）")
        else:
            lines.append("特殊状态：无")
    except Exception:  # noqa: BLE001
        lines.append("特殊状态：读取失败")
    # 全局心情（agent 自标【心情：…】+ 心跳漂移回归）
    try:
        from agent import lifesim as _ls9

        _ls9s = _ls9._load_state()
        _mr = str(_ls9s.get("mood_reason") or "")
        lines.append(f"心情：{_ls9s.get('mood') or '—'}" + (f"（{_mr}）" if _mr else ""))
    except Exception:  # noqa: BLE001
        pass
    # 表情包状态（本地模式：差分目录优先，不限次数）
    try:
        _diff_on = "开启" if (getattr(sticker, "DIFF_DIR", None) is not None) else "未配置（走本地库）"
        lines.append(f"表情包：本地模式（情绪化演出，不限次数）｜差分 {_diff_on}")
    except Exception:  # noqa: BLE001
        lines.append("表情包：本地模式（不限次数）")
    # 语音（2026-08-28：奥汀 TTS 选择性触发）
    try:
        from plugins import voice as _voice
        lines.append(_voice.status_text(user_id))
    except Exception:  # noqa: BLE001
        pass
    # 群聊追问状态
    if group_id:
        fu = brain._FOLLOWUP.get(group_id)
        if fu:
            lines.append(f"追问模式：第 {fu['count']} 轮（{_fmt_time(fu['expire'])}）")
        else:
            lines.append("追问模式：未激活")
    # 引擎与显存（同步请求：/状态 在事件循环内被调用，不能用 asyncio.run）
    try:
        with httpx.Client(timeout=3) as c:
            r = c.get(f"{ENGINE_URL}/props")
            if r.status_code == 200:
                ctx = int(r.json()["default_generation_settings"]["n_ctx"])
                lines.append(f"引擎：在线（ctx: {ctx}）")
            else:
                lines.append("引擎：离线")
    except Exception:  # noqa: BLE001
        lines.append("引擎：离线")
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        used_mb, total_mb = out.strip().splitlines()[0].split(",")
        lines.append(f"显存：{int(float(used_mb.strip())) / 1024:.1f}/{int(float(total_mb.strip())) / 1024:.1f} GB")
    except Exception:  # noqa: BLE001
        pass
    lines.append(f"（用户 {user_id}）")
    return "\n".join(lines)


HELP_TEXT = (
    "【命令】仅主人可用（群聊需 @）：\n"
    "/帮助 - 本页\n"
    "/状态 - 人格/心情/特殊状态/亲密度/引擎一览\n"
    "/人设 卡名|状态 - 切换人设 / 查看可用卡列表\n"
    "/白名单 添加|删除|列表 [QQ] - 亲密积累轨道管理（从 0 随互动升温；禁调试/状态机）\n"
    "/语音 开|关 - TTS 语音（默认关）；/表情 开|关 - 表情包\n"
    "/清除 - 解除全部特殊状态并恢复默认人格形态\n"
    "/重启引擎 - 重启推理引擎；「小说模式/正常说话」切文风\n"
    "/矫正 - 查看/清除对话矫正规则；/群聊动作 开|关 - 关闭本群动作式回复\n"
    "\n"
    "【特殊层级】（主人私聊括号里写的是已发生的事实；登记/解除由 bot 在回复里用标记自决，机器只记账不代演）\n"
    "· 登记：【催眠：效果】/【洗脑：要求】/【道具：名称】或【道具：名称：状态】/【不许装了】\n"
    "· 解除：【解除：催眠】等逐项；【解除：全部】全解（/清除 命令同样全解并恢复默认形态）\n"
    "· 时效：催眠/不许装了 10 分钟无交互自动淡出；洗脑/道具不自动消失；氛围对话中自然产生/消退\n"
    "\n"
    "【日常】\n"
    "· 主动私聊：主人+白名单（找不找/说什么 bot 自决）；被嫌吵会自我收敛\n"
    "· 生活模拟：bot 过着自己的生活（自定节奏心跳），隔久再聊自然衔接\n"
    "· 群聊：插话自决（没感觉安静、不懂可查背景、反响差不接）；@必回；引用按指向理解\n"
    "· 矫正：直接教（「以后不要说…」「回复简短点」）→ 持久生效；/矫正 查看清除\n"
    "· 记忆：按人设隔离——私聊只读私聊、群聊只读本群，不会串台\n"
)


async def _execute(action: str, user_id: str, bot: Bot, group_id: str = "") -> str:
    if action == "status":
        # 2026-09-07 P1：状态装配含同步 httpx(≤3s)+nvidia-smi(≤5s)，曾直调阻塞整个事件循环
        # （引擎离线时全 bot 冻结 8 秒级）——整函数挪线程执行。
        return await asyncio.to_thread(_build_status_text, user_id, group_id)
    if action == "help":
        return HELP_TEXT
    if action == "restart_engine":
        ok, msg = await asyncio.to_thread(restart_engine)
        return msg
    if action == "clear_special":
        # 2026-09-06 G1：/清除 替代 /重置* 族——解除全部特殊状态 + 恢复默认形态
        from core import special as _sp

        cleared = _sp.clear_all(card=brain.active_card_key())
        _su_key = str(next(iter(SUPERUSERS), user_id))
        modes = brain._load_modes()
        modes[_su_key] = {"mode": 0, "ts": time.time()}
        brain._save_modes(modes)
        try:
            card = brain._persona_card(user_id)
            bgtasks.spawn(
                brain._apply_persona_avatar(bot, card, 0, force=True)
            )
        except Exception:  # noqa: BLE001
            pass
        if cleared:
            return "已解除全部特殊状态（" + "、".join(cleared) + "），人格形态已恢复默认。"
        return "没有生效中的特殊状态（人格形态已恢复默认）。"
    if action == "correction":
        return _correction_text(user_id)
    if action == "correction_clear":
        from .. import correction

        return "矫正规则已全部清除。" if correction.clear_all(user_id) else "没有矫正规则。"
    if action == "metrics":
        return _metrics_text()
    if action == "group_action":
        cur = memory.group_action_disabled(group_id) if group_id else False
        return f"本群动作回复：{'已关闭' if cur else '开启中'}。/群聊动作 关|开 切换。"
    if action == "group_action_off":
        if not group_id:
            return "该命令仅在群聊中使用。"
        memory.set_group_action_off(group_id, True)
        return "本群 *动作* 式回复已关闭（回复不再带动作描写）。/群聊动作 开 恢复。"
    if action == "group_action_on":
        if not group_id:
            return "该命令仅在群聊中使用。"
        memory.set_group_action_off(group_id, False)
        return "本群 *动作* 式回复已恢复。"
    return (
        "未知命令。可用：/状态 /清除 /重启引擎 /人设 /矫正 /群聊动作 /帮助"
    )


# ---------------- 亲密白名单管理（2026-09-04：白名单=聊天全待遇（好感/私密尺度）；无调试、无状态机权限） ----------------
# 状态存储由 brain 维护（intimate_whitelist.json）；此处仅提供命令入口。


def _is_debug_user(uid: str) -> bool:
    """调试权限：仅超管（白名单成员无调试权限——与主人唯一区别）。"""
    return uid in SUPERUSERS


def parse_whitelist_cmd(text: str) -> tuple[str, str] | None:
    """/白名单 添加|删除|列表 [QQ] —— 亲密白名单管理（仅超管可增删）；返回 (action, qq)；非命令返回 None。
    2026-09-06：没写动作但带 QQ → 默认为**添加**（直觉用法：/白名单 QQ号 即添加）；无参数 → 列表。"""
    m = re.match(r"^/白名单\s*(添加|删除|列表|移除)?\s*(\d{5,11})?\s*$", text.strip())
    if not m:
        return None
    action = m.group(1) or ("列表" if not m.group(2) else "添加")
    action = "删除" if action == "移除" else action
    return (action, m.group(2) or "")


@debug.handle()
async def handle_debug_cmd(bot: Bot, event: MessageEvent):
    text = event.get_plaintext().strip()
    user_id = event.get_user_id()
    # 2026-09-04：调试命令仅超管（白名单成员无调试权限）
    if not _is_debug_user(user_id):
        msg = "哼，凡人，这不是你该碰的东西。"
        if isinstance(event, GroupMessageEvent):
            await debug.finish(MessageSegment.at(user_id) + " " + msg)
        await debug.finish(msg)
        return
    # ---- /思考 开|关|自动（2026-09-05：思考唯一命令入口；"自动"=bot 自决默认）----
    _tm = re.match(r"^/思考\s*(开|关|自动|on|off|auto)\s*$", text.strip(), re.I)
    if _tm:
        if user_id not in SUPERUSERS:
            await debug.finish("思考开关仅主人可用。")
            return
        _mv = _tm.group(1).lower()
        _mode = "on" if _mv in ("开", "on") else ("off" if _mv in ("关", "off") else "auto")
        brain.set_think_mode(user_id, _mode)
        if _mode == "on":
            await debug.finish("思考模式：已锁定开启（随后回复都会带思考）。")
        elif _mode == "off":
            await debug.finish("思考模式：已关闭（bot 不再自动思考，也不自决开启）。")
        else:
            await debug.finish("思考模式：已设为自动（bot 按话题需要自决是否思考）。")
        return
    # ---- 亲密白名单管理（仅超管）：聊天全待遇（好感/私密尺度），无调试/状态机权限 ----
    wl_cmd = parse_whitelist_cmd(text)
    if wl_cmd is not None:
        act, qq = wl_cmd
        if user_id not in SUPERUSERS:
            await debug.finish("白名单管理只能由主人操作。")
            return
        from .. import brain as _brain2

        wl = _brain2._load_intimate_wl()
        if act == "列表":
            cur = "、".join(wl) if wl else "（空）"
            await debug.finish(
                f"亲密白名单（{len(wl)} 人）：{cur}\n"
                "白名单 = 亲密关系积累轨道：从 0 开始真实积累，随互动慢慢升温（80 开放私密尺度/亲近口吻/不被冷淡，"
                "好感上限 80——「满」和爱意是主人专属的位置），"
                "不能用调试命令、不能触发任何状态机（情欲/催眠/洗脑/道具）。"
            )
            return
        if not re.fullmatch(r"\d{5,11}", qq):
            await debug.finish("用法：/白名单 添加|删除 [QQ]｜/白名单 列表")
            return
        if act == "添加":
            if qq in wl:
                await debug.finish(f"{qq} 已在亲密白名单中。")
                return
            wl.append(qq)
            _brain2._save_intimate_wl(wl)
            memory.db.set_intimacy(qq, _brain2.REL_START, mood="平静")  # 2026-09-07：从 0 真实积累（不设起点）
            await debug.finish(
                f"已添加 {qq}：进入亲密关系积累轨道（熟悉程度从 {_brain2.REL_START:.0f} 开始真实积累，"
                "随互动慢慢升温；积累到 80 开放私密尺度/亲近口吻）；调试与状态机依然不可用。"
            )
            return
        if act == "删除":
            if qq not in wl:
                await debug.finish(f"{qq} 不在白名单中。")
                return
            wl.remove(qq)
            _brain2._save_intimate_wl(wl)
            await debug.finish(f"已从亲密白名单移除 {qq}。")
            return
    action = parse_debug_cmd(text)
    group_id = str(getattr(event, "group_id", "") or "")
    # /人设 [卡名|状态]：切换用户的人设卡（状态机不变，只换底层人设；仅私聊）
    pc = parse_persona_cmd(text)
    if pc is not None:
        if not isinstance(event, PrivateMessageEvent):
            await debug.finish("该命令仅限私聊使用。")
            return
        if pc == "状态":
            from .. import persona as _persona

            cards = [c for c in _persona.list_personas() if not c.startswith("_") and c != "default"]
            cur = brain._persona_name(user_id)
            await debug.finish(
                f"当前人设卡：{cur}。\n可用卡（{len(cards)}）：/人设 " + "|".join(cards)
                + "\n（别名：奥汀=奥丁、夏亚=柯瓦特罗/赤色彗星、庄方宜=庄小妹、菲比=大饼脸、四季映姬=映姬、deepseek=蓝色大肥鱼；卡文件在 data/personas/）"
            )
        elif brain.set_persona(user_id, pc):
            # 头像+昵称联动：按当前模式应用新卡（多卡切换）；逐步日志化错误
            _errs = []
            try:
                _card = brain._persona_card(user_id)
                _mstate = brain._load_modes().get(user_id, {})
                _mm = int(_mstate.get("mode", 0))
            except Exception as _e:  # noqa: BLE001
                _errs.append(f"card={_e!r}")
            try:
                await brain._apply_persona_avatar(bot, _card, _mm, force=True)
            except Exception as _e:  # noqa: BLE001
                _errs.append(f"avatar={_e!r}")
            try:
                # 2026-09-08：随 brain 清死参数同步——force 形参已不存在，残留传参曾令本调用
                # 每次 TypeError 被 except 吞掉 → /人设 切卡的昵称联动整体失效
                _nick_ok = await brain._apply_persona_nickname(bot, _card)
                logger.info("persona switch nickname: {} ok={}", pc, _nick_ok)
            except Exception as _e:  # noqa: BLE001
                _errs.append(f"nick={_e!r}")
            if _errs:
                logger.warning("persona switch partial errors: {}", " | ".join(_errs))
            await debug.finish(f"已切换人设卡：{pc}（状态机不变，仅底层人设更新）。")
        else:
            await debug.finish(f"没有人设卡「{pc}」（请检查 data/personas/ 下的文件名）。")
        return
    # /表情 开|关|状态：表情包总开关（仅私聊；素材跟随人设——无素材的人设自动不触发）
    sc = parse_sticker_cmd(text)
    if sc is not None:
        if not isinstance(event, PrivateMessageEvent):
            await debug.finish("该命令仅限私聊使用。")
            return
        from .. import sticker as _stk

        if sc in ("开", "开启", "on", "1"):
            _stk.set_sticker_enabled(True)
            await debug.finish("表情包已开启（跟随人设素材；当前人设无素材则不触发）。")
        elif sc in ("关", "关闭", "off", "0"):
            _stk.set_sticker_enabled(False)
            await debug.finish("表情包已关闭。")
        else:
            cur = _stk.sticker_enabled()
            await debug.finish(f"表情包：{'开启' if cur else '关闭'}。用法：/表情 开|关")
        return
    # /语音 开|关|状态：语音开关（仅私聊；对话中不再触发）
    vc = parse_voice_cmd(text)
    if vc is not None:
        from .. import voice as _voice

        if not isinstance(event, PrivateMessageEvent):
            await debug.finish("该命令仅限私聊使用（私聊她本人可发 /语音 开|关）。")
            return
        _vcfg = _voice.load_cfg()
        if vc in ("开", "开启", "on", "1"):
            _vcfg["enabled"] = True
            _voice.save_cfg(_vcfg)
            _voice.ensure_tts_server()  # 开启即拉起语音模型（后台预热）
            # 2026-09-08：回执去腐化——曾写"私聊80%/群聊65%"，概率机 2026-09-06 已删（发不发由 agent 自决）
            await debug.finish("语音已开启（发不发由她自己定；链路诊断用 /语音测试）。")
        elif vc in ("关", "关闭", "off", "0"):
            _vcfg["enabled"] = False
            _voice.save_cfg(_vcfg)
            _voice.stop_tts_server()  # 关语音即停长驻模型（释放显存）
            await debug.finish("语音已关闭（常驻模型已停止，显存已释放）。")
        else:
            await debug.finish(f"语音状态：{'开启' if _vcfg.get('enabled') else '关闭'}。用法：/语音 开|关")
        return
    # /指数 命令族已退役（特殊层级全 agent 化，2026-09-06）——/清除 替代
    # /语音测试 [文本]：强制生成并发送一条语音（raw 直通 [VOICE] 自决门），验证 TTS 链路
    # 2026-09-07 日文封存（奥汀直出中文）→ /语音测试 原文直出不再自动翻译；
    # /语音日文 [文本] 保留为封存日文链路的专用诊断（仍走翻译+日文合成）
    vt = parse_voice_test_cmd(text)
    if vt is not None:
        from .. import voice as _voice

        mode, vtext = vt
        # 2026-09-08 盲审交代：raw 直通绕过总开关（诊断本意），但语音关闭时本诊断会真正跑
        # TTS 链并把刚释放的常驻模型拉回显存——明确提示，避免"关了语音怎么又占显存"的困惑
        if not _voice.load_cfg().get("enabled", True):
            await bot.send(event, "（提示：语音总开关当前为关闭——本诊断将临时拉起 TTS 模型，测完如需释放显存请 /语音 关）")
        speech = vtext or ("ふん、くだらん。妾は見渡す限りの軍神ぞ。" if mode == "ja" else "哼，区区凡人，也敢来打扰妾身？")
        if mode == "ja" and _voice.detect_lang(speech) == "中文":
            await bot.send(event, "（正在翻译为日文…）")
            speech = await _voice.translate_ja(speech)
        await bot.send(event, "（语音测试中…请稍候）")
        ok_sent = await _voice.maybe_send_voice(bot, event, reply=speech, user_id=user_id, raw=True)
        logger.info("debug voice test: user=%s mode=%s ok=%s text=%r", user_id, mode, ok_sent, speech)
        await debug.finish("语音已发出，请查收。" if ok_sent else "语音生成失败（详见日志）。")
        return
    reply = await _execute(action, user_id, bot, group_id)
    if isinstance(event, GroupMessageEvent):
        reply = f"{MessageSegment.at(user_id)} {reply}"
    logger.info("debug cmd: user=%s action=%s", user_id, action)
    await debug.finish(Message(reply))
