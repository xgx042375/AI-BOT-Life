# -*- coding: utf-8 -*-
"""telegram —— 国际 IM 通道：Telegram Bot API 长轮询（2026-09-12）。

用户裁决（2026-09-12，遗留任务 A14 选 ②）：接 Telegram。海外没有 QQ，但 Telegram 的官方
Bot API 最宽松（BotFather 拿 token 即用），这条通道让"本地伴侣"在海外有一个真正能用的对话入口。

接入范式**照抄 GAL 页那条已跑通的路**（plugins/webgal/inject.py + __init__._do_round）：
    入站 update → 合成 OneBot v11 事件 → nonebot.message.handle_event(bot, ev)
    → 整条管线（记忆/好感/情绪/道具/命令/人设）原样复用
    出站 call_api → 翻译成 Bot API 调用（text / photo）
于是 **brain 5847 行零改动**，且 Telegram 与 QQ、GAL 页共用同一份主人身份与记忆（webgal v2 同身份裁决）。

与 QQ 通道的关系（两条都必须，不是可选项）：
  · **gal 停用门**：判据直接用 webgal.qq_suspended(bot)——gal 演出中 QQ 停，Telegram 同样停。
    提前判、直接跳过注入：省一次 LLM 调用，且日志说明原因，避免"发了没人回"的困惑。
  · **合成轮标记 webgal.set_synthetic_round(True)**：管线内下游（brain._typing_on 等）据此**抑制
    真实 QQ 动作**。不置位的话，一条 Telegram 消息可能去改 QQ 小号的资料/输入状态——合成通道
    不得触发任何 QQ 账号动作（webgal 计划表 §五#4 同款红线）。

v1 边界（都是明确不做，不是漏；逐条记在 docs/遗留任务.md）：
  · 仅私聊 + 仅主人（TELEGRAM_OWNER_ID）；群聊与陌生人一律忽略（不做白名单＝防"猜中 bot
    用户名就能当成你本人说话"，那会把记忆、人设、API 额度一起交出去）。日志可见，不回复。
  · 语音只入不出（A15）；启动器设置项与状态行未做（A16）；群聊/多用户未做（A17）。
  · 启动时**排空**离线期间积压的 update（只记条数、不补答）——避免开机被几十条旧消息刷屏。
    bot 停机期间的消息视为已读，这条诚实写进部署指南（不假装"她只是睡着了"）。
  · 入站媒体只做**存在感知**：注入 `tg://photo` / `tg://voice` 占位段，由 brain 自己的
    "TA 给你发了一张图片/语音"分支处理（已核实全仓无人拉取入站媒体字节；唯一的 get_msg 只服务
    引用回复段，而 v1 不注入 reply 段）。占位串不含 file_id/图片内容——历史里不留可复用的引用。

配置（qq-bot/.env；全部缺省 = 关闭 = 加载零副作用）：
    TELEGRAM_ENABLED=true                      # 总开关
    TELEGRAM_BOT_TOKEN=123456:ABC-DEF          # BotFather 给的 token
    TELEGRAM_OWNER_ID=123456789                # 你的 Telegram 数字 id（找 @userinfobot 查）
    TELEGRAM_PROXY=http://127.0.0.1:10808      # 可选：中国大陆直连不了 api.telegram.org
    TELEGRAM_API_BASE=https://api.telegram.org # 可选：自建反代（⚠️ 第三方反代会看到 token）
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from loguru import logger
from nonebot import get_driver
from nonebot.adapters.onebot.v11 import Bot

from .api import DEFAULT_API_BASE, TelegramClient, TelegramError, plan_sends, redact

_TRUTHY = ("1", "true", "yes", "on")

# 没有 QQ（SUPERUSERS 为空）时，主人身份用 TG 用户 id 加偏移自成一路：
# 10**15 + tg_id（TG id < 2^52 ≈ 4.5e15，故映射不重叠）；QQ 号 ≤ 4.3e9，两个空间不相交。
# 这样"只装 Telegram、完全没装 QQ"的国际用户也能跑，且不会与任何真实 QQ 号撞身份。
TG_UID_BASE = 10**15

# 冒烟端口（合成段的占位 file）——不含 file_id/内容
PLACEHOLDER_PHOTO = "tg://photo"
PLACEHOLDER_VOICE = "tg://voice"

_TASK: asyncio.Task | None = None  # 长轮询任务（另存一份：bgtasks 持引用防 GC，这里为可控停止）
_CLIENT: TelegramClient | None = None
_STOP = False
_SKIP_LOGGED: set[str] = set()  # 忽略类日志去重（同一个人只啰嗦一次）
_PIPELINE_RUNNER = None  # 测试注入替身；None = 主路（照抄 webgal._PIPELINE_RUNNER 的可测形态）
_STATUS: dict[str, Any] = {"running": False, "skipped_offline": 0, "rounds": 0, "last_error": ""}


# ==================== 配置（.env；nonebot pydantic 配置，键名小写） ====================
def _cfg(key: str, default: str = "") -> str:
    """读配置：**先真实环境变量、再 nonebot config（.env）**——两个来源都得看。

    2026-09-12 实测教训（tools/dev/tg_loop_check.py 抓到）：nonebot 的 pydantic 配置只把
    **写在 .env 里**的键收进 `__pydantic_extra__`；一个只在真实环境变量里给的键
    （联调用的 TELEGRAM_API_BASE）在 config 上根本不存在 → getattr 落到默认值 →
    联调打到了真 Telegram（401）。故此处两处都读；空串也算"显式给了"（与 pydantic 优先级一致）。
    """
    try:
        v = os.environ.get(key.upper())
    except Exception:  # noqa: BLE001
        v = None
    if v is None:
        try:
            v = getattr(get_driver().config, key, None)
        except Exception:  # noqa: BLE001
            v = None
    return str(v if v is not None else default).strip()


def enabled() -> bool:
    return _cfg("telegram_enabled").lower() in _TRUTHY


def token() -> str:
    return _cfg("telegram_bot_token")


def owner_id() -> int:
    try:
        return int(_cfg("telegram_owner_id") or 0)
    except ValueError:
        return 0


def api_base() -> str:
    return _cfg("telegram_api_base") or DEFAULT_API_BASE


def proxy() -> str:
    return _cfg("telegram_proxy")


def identity_uid() -> str:
    """主人身份 uid：优先 SUPERUSERS（有 QQ 就与 QQ/GAL 共用同一份记忆），
    否则用 TG id + TG_UID_BASE 自成一路（没装 QQ 也能跑）。两者都取不到 → 空串（通道不启动）。"""
    try:
        from plugins.webgal.inject import owner_uid

        uid = owner_uid()
        if uid:
            return uid
    except Exception as e:  # noqa: BLE001
        logger.debug("telegram identity: owner_uid 不可用（{}）", type(e).__name__)
    oid = owner_id()
    return str(TG_UID_BASE + oid) if oid else ""


# ==================== 入站门（纯函数：冒烟直接断言） ====================
def gate(msg: dict, owner: int) -> str:
    """入站门："" = 放行；否则返回忽略原因码（group / not_owner / empty）。

    顺序固定：先看是不是私聊，再看是不是主人，最后看有没有可处理的内容。
    纯函数（不读配置不建连接），§43 断言"陌生人/群聊/空消息一律不放行"。
    """
    chat = msg.get("chat") or {}
    if str(chat.get("type") or "private") != "private":
        return "group"
    try:
        uid = int((msg.get("from") or {}).get("id") or 0)
    except (TypeError, ValueError):
        uid = 0
    if uid == 0 or uid != int(owner or 0):
        return "not_owner"
    if not any(msg.get(k) for k in ("text", "caption", "photo", "voice", "audio")):
        return "empty"
    return ""


def extract(msg: dict) -> tuple[str, str, list[dict]]:
    """Telegram message → (文本, 昵称, 附加段描述)。

    段用 dict 描述（{"type","data"}）而不是直接建 OneBot 段：本函数保持纯函数可测，
    真正转 OneBot 段在 _to_segments()。图片只判**有没有**（TG 给多档尺寸，v1 不取字节）。
    纯语音/纯图的 text 为空——由 brain 的存在感知分支处理，这里不代它编话。
    """
    text = str(msg.get("text") or msg.get("caption") or "")
    frm = msg.get("from") or {}
    nick = " ".join(x for x in (str(frm.get("first_name") or ""),
                                str(frm.get("last_name") or "")) if x).strip()
    nick = nick or str(frm.get("username") or "") or str(frm.get("id") or "")
    descs: list[dict] = []
    if msg.get("photo"):
        descs.append({"type": "image", "data": {"file": PLACEHOLDER_PHOTO}})
    if msg.get("voice") or msg.get("audio"):
        descs.append({"type": "record", "data": {"file": PLACEHOLDER_VOICE}})
    return text, nick, descs


def _to_segments(descs: list[dict]) -> list[Any]:
    """段描述 → OneBot v11 段（import 收在这里，保持上层纯函数可测）。"""
    from nonebot.adapters.onebot.v11 import MessageSegment

    out: list[Any] = []
    for d in descs or []:
        t = str(d.get("type") or "")
        f = str((d.get("data") or {}).get("file") or "")
        if t == "image":
            out.append(MessageSegment.image(f))
        elif t == "record":
            out.append(MessageSegment.record(f))
    return out


# ==================== 出站 Bot（真发版 CaptureBot） ====================
class TgBot(Bot):
    """把管线的 OneBot 发送翻译成 Bot API 调用（真发，不捕获）。

    与 webgal.CaptureBot 的差别只有一处：CaptureBot 收进队列给页面推帧，这里真发到 Telegram。
    **路由不信 event/payload**：私聊 chat_id 恒等于 TELEGRAM_OWNER_ID（v1 仅主人私聊），
    这样即使上游把 uid 弄错，也不可能把消息发到陌生人那里。
    非发送类 API（set_qq_profile / set_group_card / set_input_status / get_msg / poke …）
    一律空操作：合成通道不得触发任何 QQ 账号动作。
    """

    def __init__(self, adapter=None, self_id: str = "9") -> None:
        if adapter is None:
            try:
                from plugins.webgal.inject import new_capture_adapter

                # 复用单例：Adapter.__init__ 每次都往 FastAPI 追加注册 6 条路由（盲审 P2-1）
                adapter = new_capture_adapter()
            except Exception as e:  # noqa: BLE001
                logger.warning("telegram: capture adapter 不可用（{}）——出站仅记日志", type(e).__name__)
                adapter = None
        super().__init__(adapter, self_id)
        self.chat_id = owner_id()

    async def call_api(self, api: str, **data: Any) -> Any:  # noqa: ANN401
        """覆写：发送类真发 Telegram，其余空操作。**永不抛**（通道故障不该带崩一轮对话）。"""
        try:
            if api in ("send_private_msg", "send_msg"):
                if str(data.get("message_type") or "private") == "group":
                    logger.info("telegram skip: 群消息不发送（v1 仅主人私聊，A17）")
                    return None
                await self._deliver(data.get("message"))
            elif api == "send_group_msg":
                logger.info("telegram skip: 群消息不发送（v1 仅主人私聊，A17）")
            else:
                logger.debug("telegram no-op api: {}", api)
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning("telegram call_api 失败: api={} [{}: {}]",
                           api, type(e).__name__, redact(e, token()))
            return None

    async def _deliver(self, message: Any) -> None:
        """Message（或纯 str）→ 计划 → 真发。客户端未就绪只记日志（通道未启动时不该静默）。"""
        if isinstance(message, str):
            segs: list[Any] = [{"type": "text", "data": {"text": message}}]
        else:
            segs = list(message or [])
        plan = plan_sends(segs, self.chat_id)
        if not plan:
            return
        if _CLIENT is None:
            logger.warning("telegram: 客户端未就绪，本轮 {} 条计划未发送", len(plan))
            return
        await _CLIENT.send_plan(plan)
        _STATUS["last_send_plan"] = len(plan)


# ==================== 一轮：合成事件 → 管线 ====================
async def _default_pipeline_runner(bot: Bot, ev: Any) -> None:  # noqa: ANN401
    """主路：nonebot 2.5.0 的 handle_event 建立 current_bot/current_event 上下文，
    matcher.send / bot.send 全部落到传入的 TgBot（webgal 同款已验证接口）。"""
    from nonebot.message import handle_event

    await handle_event(bot, ev)


async def run_round(text: str, nickname: str = "", descs: list[dict] | None = None) -> bool:
    """跑一轮：Telegram 消息 → 合成事件 → 整条管线。返回是否真的注入了（False = 被门挡下）。"""
    from plugins import webgal as _wg

    bot = TgBot()
    if _wg.qq_suspended(bot):
        # gal 演出中：QQ 与 Telegram 同规则停（判据单一来源 = webgal.qq_suspended）
        logger.info("telegram round skipped: gal 模式（与 QQ 通道同规则，消息不回复不落库）")
        return False
    uid = identity_uid()
    if not uid:
        logger.warning("telegram round skipped: 主人身份不可得（SUPERUSERS 与 TELEGRAM_OWNER_ID 皆空）")
        return False
    from plugins.webgal.inject import build_event

    ev = build_event(uid, text, nickname=nickname or None, extra_segments=_to_segments(descs or []))
    runner = _PIPELINE_RUNNER or _default_pipeline_runner
    _wg.set_synthetic_round(True)  # 合成轮置位：抑制管线内下游的真实 QQ 动作
    try:
        await runner(bot, ev)
    finally:
        _wg.set_synthetic_round(False)
    _STATUS["rounds"] = int(_STATUS.get("rounds") or 0) + 1
    return True


# ==================== 长轮询循环 ====================
def _log_skip_once(key: str, msg: str, *args: Any) -> None:  # noqa: ANN401
    if key in _SKIP_LOGGED:
        return
    _SKIP_LOGGED.add(key)
    logger.info(msg, *args)


async def _handle_update(upd: dict) -> None:
    """单条 update：门口判 → 注入一轮。任何异常只记日志（一条坏消息不该终止通道）。"""
    msg = upd.get("message")
    if not isinstance(msg, dict):
        return
    oid = owner_id()
    reason = gate(msg, oid)
    if reason == "group":
        chat_id = str((msg.get("chat") or {}).get("id") or "?")
        _log_skip_once("g:" + chat_id, "telegram 群聊消息已忽略（v1 仅主人私聊，A17）: chat={}", chat_id)
        return
    if reason == "not_owner":
        who = str((msg.get("from") or {}).get("id") or "?")
        _log_skip_once("u:" + who, "telegram 陌生人消息已忽略（不回复不解释）: user={}", who)
        return
    if reason == "empty":
        _log_skip_once("e:" + str((msg.get("from") or {}).get("id") or "?"),
                       "telegram 无可处理内容的消息已忽略（贴纸/文件/服务消息等）")
        return
    text, nick, descs = extract(msg)
    try:
        await run_round(text, nick, descs)
    except Exception as e:  # noqa: BLE001
        logger.warning("telegram round 异常（已吞，通道继续）: {} [{}]", redact(e, token()), type(e).__name__)


async def _drain(client: TelegramClient, max_rounds: int = 20) -> tuple[int, int | None]:
    """排空离线积压：反复 getUpdates(timeout=0) 直到空（上限 max_rounds 防无限循环）。

    返回 (跳过条数, 最后一条 update_id)。补答是刻意不做的：开机被几十条旧消息刷屏、且每条都烧
    LLM 额度，比"少回几条旧消息"代价大得多（诚实写进部署指南）。
    """
    skipped = 0
    last: int | None = None
    for _ in range(max(1, max_rounds)):
        ups = await client.get_updates(offset=(last + 1) if last is not None else None, timeout=0)
        if not ups:
            break
        skipped += len(ups)
        last = int(max(int(u.get("update_id") or 0) for u in ups))
    return skipped, last


async def _loop() -> None:
    """长轮询主循环：失败指数退避（3s→60s 封顶），成功即复位。"""
    global _CLIENT  # 本函数内赋值（_CLIENT = client）；_STOP 只读，不需声明

    tk, oid = token(), owner_id()
    client = TelegramClient(tk, api_base(), proxy())
    _CLIENT = client
    _STATUS.update({"running": True, "last_error": ""})
    try:
        try:
            skipped, last = await _drain(client)
            _STATUS["skipped_offline"] = skipped
            if skipped:
                logger.warning("telegram: 启动排空，跳过离线期间积压的 {} 条消息（不补答）", skipped)
            offset = (last + 1) if last is not None else None
        except TelegramError as e:
            logger.warning("telegram 启动排空失败（照常进入轮询）: {}", e)
            offset = None
        logger.info("telegram 通道已启动：owner={} api_base={} proxy={}",
                    oid, client.api_base, "有" if client.proxy else "无")
        fails = 0
        while not _STOP:
            try:
                ups = await client.get_updates(offset)
                fails = 0
            except TelegramError as e:
                fails += 1
                wait = min(60, 3 * (2 ** min(fails - 1, 4)))
                _STATUS["last_error"] = str(e)
                logger.warning("telegram getUpdates 失败（第 {} 次，{}s 后重试）: {}", fails, wait, e)
                await asyncio.sleep(wait)
                continue
            for u in ups:
                offset = int(u.get("update_id") or 0) + 1
                await _handle_update(u)
    except asyncio.CancelledError:
        logger.info("telegram 通道已停止")
        raise
    except Exception as e:  # noqa: BLE001
        _STATUS["last_error"] = str(e)
        logger.warning("telegram 通道异常退出: {}", redact(e, tk))
    finally:
        _STATUS["running"] = False
        await client.close()
        if _CLIENT is client:
            _CLIENT = None


def start_if_configured() -> bool:
    """按配置启动（幂等）。返回是否启动。缺配置时**明确说缺哪一个**，不静默死。"""
    global _TASK, _STOP

    if not enabled():
        return False
    tk, oid = token(), owner_id()
    missing = []
    if not tk or ":" not in tk:
        missing.append("TELEGRAM_BOT_TOKEN（BotFather 给的 <数字>:<串>）")
    if not oid:
        missing.append("TELEGRAM_OWNER_ID（你的 Telegram 数字 id，找 @userinfobot 查）")
    if missing:
        logger.warning("telegram 已启用但配置不全，通道未启动 —— 缺：{}", "；".join(missing))
        return False
    if _TASK is not None and not _TASK.done():
        return True  # 已在跑（幂等）
    _STOP = False
    try:
        from core import bgtasks

        _TASK = bgtasks.spawn(_loop())  # 统一持引用（裸 create_task 会被 GC 吞：bot.py 审计 N 项同款教训）
    except Exception as e:  # noqa: BLE001
        logger.warning("telegram 启动失败: {} [{}]", e, type(e).__name__)
        return False
    return True


async def stop() -> None:
    """停通道（收尾用；等待任务真正结束再退出，避免半死连接）。"""
    global _STOP
    _STOP = True
    if _TASK is not None:
        _TASK.cancel()
        try:
            await _TASK
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


def status() -> dict:
    """通道状态快照（启动器/诊断用；A16 会把它的可见面接上）。"""
    return {
        "enabled": enabled(),
        "running": bool(_TASK is not None and not _TASK.done()),
        "owner": owner_id(),
        "api_base": api_base(),
        "proxy": bool(proxy()),
        **{k: v for k, v in _STATUS.items() if k != "running"},
    }


@get_driver().on_startup
async def _tg_startup() -> None:
    try:
        start_if_configured()
    except Exception as e:  # noqa: BLE001
        logger.warning("telegram startup hook: {} [{}]", e, type(e).__name__)


@get_driver().on_shutdown
async def _tg_shutdown() -> None:
    try:
        await stop()
    except Exception as e:  # noqa: BLE001
        logger.debug("telegram shutdown hook: {} [{}]", e, type(e).__name__)
