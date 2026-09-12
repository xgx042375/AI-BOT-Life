# -*- coding: utf-8 -*-
"""webgal —— GAL 前端客户端 bot 侧（批次1'，2026-09-09）。

spec：docs/archive/计划表-GAL前端客户端-2026-09-09.md §二/§三 + 用户裁决 v2（〇之二，冲突处以 v2 为准）。

三态模式（**bot 全局开关**，持久化 data/webgal_mode.json 原子写；**开机恒复位 qq**——
gal 挂着重启后 QQ 全哑不可接受，安全侧）：
  gal  : QQ bot 停用——集中门 event_preprocessor 单点拦全部 QQ 事件（含 /命令）；
         主动面两处（debug proactive 循环、graph banter/greet/poke 决策入口）各自带门；
         心跳/生活状态是进程内循环不经事件系统 → 照常（时间仍在流动）。
  qq   : 一切照现状（开机默认）。
  chat : QQ 与页面同一口径——brain 感知注入【沟通模式】+ 发送前剥（动作）（落库=发出的
         剥离后文本，v2 #3"发出什么=发生什么"；注入/剥除实现见 plugins/brain/__init__.py）。

语音 web 路（v2 #4 实施取简定案）：**bot 不推 seg(voice)**，页面收到文字后逐行 POST /gal/tts
（voice.tts_wav，沿 _tts_lock 串行）；CaptureBot 对 record 段只捕获不推帧（见 inject.py）。

纪律：本模块顶层**不许 import plugins.brain**（会循环）——brain 引用全部函数内 lazy import。
"""
from __future__ import annotations

import asyncio
import base64
import datetime as _dt  # read_lifelog/read_gal_history ISO 时间用（R1 #11：勿在函数内重复 import）
import json
import os
import secrets
import time
import uuid

from loguru import logger
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Bot
from nonebot.exception import IgnoredException
from nonebot.message import event_preprocessor

from core import atomics
from core.paths import data_path
from core.paths import LAUNCHER_ROOT  # 2026-09-12 S1：GAL 页与差分目录的唯一来源
# fastapi 必须模块级导入：本文件顶部 `from __future__ import annotations` 使注解变字符串，
# FastAPI 用 get_type_hints 按模块全局解析端点注解——函数内局部导入的 WebSocket 不可见，
# 曾致 /gal/ws 依赖解析失败 → close-before-accept → 握手 403（2026-09-10 深夜视觉检测实锤修复）。
from fastapi import Body, WebSocket  # noqa: F401  (Body/WebSocket 供 _mount 端点注解解析)

# ---- 路径（模块常量，测试可重定向到 tmp；绝不碰真实 data/ 的测试写路径） ----
MODE_FILE = data_path("webgal_mode.json")
TOKEN_FILE = data_path("webgal_token.txt")
LIFE_LOG_FILE = data_path("life_log.jsonl")
GAL_HIST_FILE = data_path("gal_history.jsonl")  # GAL 回想轮次 JSONL（Phase 2b，2026-09-10 旁路定案）
# GAL 页单一事实源（计划表 §3.6 定案）：页面文件放 launcher/web/，bot 直接 FileResponse；
# 该文件由前端任务交付，缺失时 /gal 返回说明文字而不是 500。
GAL_PAGE_FILE = LAUNCHER_ROOT / "web" / "gal.html"
ASSET_WHITELIST = {"gal.js", "gal.css"}  # 白名单文件名（防目录穿越）
VALID_MODES = ("gal", "qq", "chat")
DEFAULT_MODE = "qq"

# 立绘 URL（计划表 §三/§〇#5：launcher WebView2 虚拟主机映射，环境级任意页面可用）
# 2026-09-12 E 项：目录段提到常量。原先 "bust4w"/"fullw" 在 URL 模板与本机素材目录各写一份——
# 改一段忘另一段，就是"前端按 URL 拉图 404、而 bot 侧校验还以为有词"的软故障。同段只写这里。
DIFF_SEG = "bust4w"   # 差分（表情）素材目录段
FULL_SEG = "fullw"    # 全身立绘目录段
BUST_URL = f"https://cards.local/{DIFF_SEG}/{{key}}.webp"
FULL_URL = f"https://cards.local/{FULL_SEG}/{{key}}.webp"
# 立绘差分素材目录（热修十三 B1，2026-09-12）：与本文件 DIFF_SEG 同源（同一常量，不再各写一份）
# （launcher.ps1 把 launcher/cards 虚拟主机映射为 https://cards.local/）——前端按 BUST_URL
# 拉 <卡键>_<词>.webp，bot 侧按本目录实扫真实差分词表（sprite_words），两处同源须同步。
# 2026-09-12 S1 审计 G3：原为硬编码 "E:\robot\..."，换盘后 sprite_words() 会**静默**返回
# 空表（fail-open，不报错）→ 差分词软校验退化成"放行任意词"。改由 core.paths 推导。
# 舞台包（type=gal）接入后此处将变为多根解析（包 → 本机），见最终方案 spec §T14。
BUST_DIR = LAUNCHER_ROOT / "cards" / DIFF_SEG
_SPRITE_WORDS_CACHE: dict = {"key": None, "words": []}


def sprite_dirs() -> list:
    """差分素材根（**有序**，先到先得）：舞台包 `sprites/diff/` → 本机 `launcher/cards/bust4w`。

    2026-09-12 T14：原先只有一个硬目录，舞台包（type=gal）里的差分**扫不到**——
    放进包里等于看不见。现在包在前、本机在后：同一个词优先用包里的那张图。
    """
    dirs = []
    try:
        from core import packs as _packs  # 函数内 lazy import（与 content.json 同纪律）

        for pk in _packs.by_type("gal"):
            d = pk.dir / "sprites" / "diff"
            if d.is_dir():
                dirs.append(d)
    except Exception as e:  # noqa: BLE001
        logger.debug("sprite dirs: gal packs unavailable [{}]", type(e).__name__)
    dirs.append(BUST_DIR)
    return dirs


def sprite_words(card_key: str) -> list[str]:
    """扫描差分素材根下 <card_key>_*.webp 的真实差分词表（热修十三 B1，2026-09-12）。

    词=去 `<card_key>_` 前缀与扩展名的文件名主干（amiya_Smile.webp → "Smile"）；
    大小写不归一、"Default"（默认半身本体）不跳过——扫描层零语义，"默认词不选"由提示词教导；
    缓存键=(各根目录路径+卡键+mtime)，任一目录增删文件即失效；
    目录不存在/任何异常 → 空表（绝不抛，fail-open，前端 404 探测回退链兜底）。
    """
    key = str(card_key or "").strip()
    if not key:
        return []
    roots = sprite_dirs()
    try:
        ckey = (tuple(str(d) for d in roots), key,
                tuple(round(d.stat().st_mtime, 3) for d in roots if d.is_dir()))
    except OSError:
        return []
    if _SPRITE_WORDS_CACHE["key"] == ckey:
        return list(_SPRITE_WORDS_CACHE["words"])
    prefix = key + "_"
    words: list[str] = []
    seen: set[str] = set()
    for d in roots:
        try:
            it = list(d.iterdir())
        except OSError:
            continue          # 单根不可读不影响其它根（包目录被删/无权限 → 继续扫本机）
        for p in it:
            name = p.name
            if not name.lower().endswith(".webp"):
                continue
            try:
                if not p.is_file():
                    continue
            except OSError:
                continue
            stem = name[:-5]
            if not stem.startswith(prefix):
                continue
            w = stem[len(prefix):].strip()
            if w and w not in seen:
                seen.add(w)
                words.append(w)
    words.sort()
    _SPRITE_WORDS_CACHE.update(key=ckey, words=list(words))
    return list(words)


# ============ 模式状态（v2 #1） ============
def _load_mode_file() -> dict:
    data = atomics.read_json(MODE_FILE, {})
    return data if isinstance(data, dict) else {}


_MODE_CACHE: dict = {"key": None, "mode": DEFAULT_MODE}


def get_mode() -> str:
    """当前全局模式（gal|qq|chat）；文件缺失/非法值 → qq（安全侧默认=现状行为）。
    带 (路径, mtime) 键缓存——本判据在每个 QQ 事件集中门/chat 双挂点/graph/debug 门都调，
    免每事件盘读+JSON 解析（R1 #1）。测试重定向 MODE_FILE 属性=路径变→键变→自动重读。"""
    try:
        key = (str(MODE_FILE), MODE_FILE.stat().st_mtime)
    except OSError:
        key = (str(MODE_FILE), None)
    if _MODE_CACHE["key"] != key:
        data = _load_mode_file()
        m = str(data.get("mode") or DEFAULT_MODE).strip().lower()
        if m not in VALID_MODES:
            m = DEFAULT_MODE
            if data:  # 文件存在但值非法才告警；缺失=未初始化常态。按键变化节流，不刷屏
                logger.warning("webgal mode file invalid value {!r} -> fallback {} [{}]", data.get("mode"), m, key[0])
        _MODE_CACHE["key"] = key
        _MODE_CACHE["mode"] = m
    return _MODE_CACHE["mode"]


def set_mode(mode: str) -> str:
    """写全局模式（原子写）；非法值返回空串不落盘。返回实际生效的模式名。"""
    m = str(mode or "").strip().lower()
    if m not in VALID_MODES:
        logger.warning("webgal set_mode rejected: {!r} (valid: {})", mode, VALID_MODES)
        return ""
    if not atomics.write_json_atomic(MODE_FILE, {"mode": m, "ts": time.time()}):
        # gal=QQ 停用态：写失败若假报成功，下次读回旧值会静默回弹（不安全侧）——显式失败让 WS 回 bad_mode
        logger.warning("webgal set_mode write failed: {} (not persisted)", m)
        _MODE_CACHE["key"] = None
        return ""
    _MODE_CACHE["key"] = None  # 立即失效，不等下次 stat（R1 #1）
    logger.info("webgal mode set: {}", m)
    return m


def startup_reset() -> None:
    """开机复位（v2 #1）：gal 恒复位 qq=安全侧硬裁决；chat 默认一并复位，但用户裁决（2026-09-10）
    可在启动器设置 WEBGAL_CHAT_PERSIST=true 让 chat 跨重启保留（全局设置做成可切换版）。"""
    m = get_mode()
    if m == DEFAULT_MODE:
        return
    if m == "chat":
        try:
            from nonebot import get_driver

            flag = str(getattr(get_driver().config, "webgal_chat_persist", "") or "").strip().lower()
            if flag in ("true", "1", "yes", "on"):
                logger.info("webgal startup: chat kept (WEBGAL_CHAT_PERSIST={})", flag)
                return
        except Exception:  # noqa: BLE001
            pass
    set_mode(DEFAULT_MODE)
    logger.warning("webgal startup reset: {} -> {}（开机复位，v2 #1）", m, DEFAULT_MODE)


# ============ gal 停用门（v2 #2 集中门 + 判据纯函数） ============
# 判据单一事实源在 inject.py（R1 #9）；此处转出供集中门/外部调用方同名使用。
from .inject import is_capture_bot  # noqa: E402,F401


def qq_suspended(bot) -> bool:
    """gal 门真值表：mode==gal 且 bot 非合成 CaptureBot → True（QQ 通道停用）。"""
    return get_mode() == "gal" and not is_capture_bot(bot)


@event_preprocessor
async def _gal_qq_suspend_gate(bot: Bot, event: Event) -> None:
    """集中门（v2 #2 最小接触面）：gal 模式 + 真实 QQ bot → IgnoredException 拦掉全部 QQ 事件
    （含 /命令——命令也是事件，先于一切 matcher）。合成事件 bot=CaptureBot → 放行。
    心跳/生活状态/proactive 是进程内循环不经事件系统：proactive 另有门（debug/graph 两处主动面）。"""
    if qq_suspended(bot):
        raise IgnoredException("webgal: gal mode, QQ channel suspended")


# ============ 快照（/gal/state 与 auth_ok/state 帧共用载荷） ============
def _persona_snapshot() -> dict:
    """persona key=人设卡文件键（persona_select 值；bot.py:89-97 同款入口），取不到置空串并日志。"""
    key = name = ""
    try:
        from plugins import brain as _brain  # 函数内 lazy import（避免循环，见模块头纪律）

        key = str(_brain._persona_name(None) or "").strip()
        name = str((_brain._persona_card(None) or {}).get("name") or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.warning("webgal persona snapshot failed: {} [{}]", e, type(e).__name__)
    if not key:
        logger.warning("webgal persona key empty（当前人设卡键不可得，立绘 URL 置空）")
    return {
        "key": key,
        "name": name or key,
        "bust": BUST_URL.format(key=key) if key else "",
        "full": FULL_URL.format(key=key) if key else "",
    }


def _life_snapshot() -> dict:
    """scene/doing/mood 来自 life_state（agent.lifesim 现有读取函数）。"""
    st: dict = {}
    try:
        from agent import lifesim as _ls

        st = _ls._load_state() or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("webgal life snapshot failed: {} [{}]", e, type(e).__name__)
    return {
        "scene": str(st.get("scene") or ""),
        "doing": str(st.get("doing") or ""),
        "mood": str(st.get("mood") or ""),
    }


def _active_state_labels() -> list[str]:
    """特殊状态标签列表（core/special 现有 active()）。"""
    try:
        from core import special as _sp
        from plugins.brain import active_card_key as _ack

        return [str(_sp.LABELS.get(k, k)) for k in _sp.active(card=_ack())]
    except Exception as e:  # noqa: BLE001
        logger.debug("webgal states snapshot failed: {}", e)
        return []


def _state_payload() -> dict:
    persona = _persona_snapshot()
    life = _life_snapshot()
    return {
        "mode": get_mode(),
        "persona": persona,
        "scene": life["scene"],
        "doing": life["doing"],
        "mood": life["mood"],
        "states": _active_state_labels(),
        "token": get_token(),
        "mount_err": _MOUNT_ERR,  # 挂载失败诊断口（正常为空串；R2 #4——曾只写不读）
        "bot_avatar": _bot_avatar(),  # 聊天软件模式页头头像（用户裁决 2026-09-10 #5）
    }


# ============ token（纵深防御；binding 现状仅本机 127.0.0.1 可达） ============
_TOKEN_CACHE: dict = {"key": None, "value": ""}


def get_token() -> str:
    """WS 鉴权 token：.env WEBGAL_TOKEN（nonebot driver config 读）优先；
    缺省首次自动 secrets.token_hex(16) 写 TOKEN_FILE（原子写），之后复读。
    文件读取带 (路径, mtime) 键进程内缓存（R1 #10：auth/state 每帧都调）。"""
    try:
        from nonebot import get_driver

        v = str(getattr(get_driver().config, "webgal_token", "") or "").strip()
        if v:
            return v
    except Exception:  # noqa: BLE001
        pass
    try:
        key = (str(TOKEN_FILE), TOKEN_FILE.stat().st_mtime)
    except OSError:
        key = (str(TOKEN_FILE), None)
    if _TOKEN_CACHE["value"] and _TOKEN_CACHE["key"] == key:
        return _TOKEN_CACHE["value"]
    try:
        t = TOKEN_FILE.read_text(encoding="utf-8-sig").strip()
    except OSError:
        t = ""
    if not t:
        t = secrets.token_hex(16)
        if atomics.write_text_atomic(TOKEN_FILE, t + "\n"):
            logger.info("webgal token generated: {}", TOKEN_FILE)
        else:
            logger.warning("webgal token file write failed（本次生成 token 仅进程内有效）")
            return t  # 写失败不缓存（盘上内容不可信），每次重生成=旧行为
    try:
        _TOKEN_CACHE.update(key=(str(TOKEN_FILE), TOKEN_FILE.stat().st_mtime), value=t)
    except OSError:
        pass  # 缓存写失败无所谓：mtime 一变自然重算（cache miss 就是它的兜底路径）
    return t


def _bot_avatar() -> str:
    """bot 本人 QQ 头像 URL（聊天软件模式页头圆形头像用）；取运行中真实 bot 的 self_id。
    取不到（无连接/smoke 环境）返回空串，前端降级为首字占位。"""
    try:
        from nonebot import get_bot

        nk = str(getattr(get_bot(), "self_id", "") or "").strip()
        if nk.isdigit():
            return "https://q1.qlogo.cn/g?b=qq&nk=" + nk + "&s=640"
    except Exception:  # noqa: BLE001
        pass
    return ""


# ============ 生活轨迹（v2 #5②） ============
def read_lifelog(limit: int = 200) -> list[dict]:
    """读 data/life_log.jsonl 尾部 N 条（limit 钳 1..1000）。
    返回 [{ts, iso, doing, mood}]——ts 原始秒值 + iso 本地时间字符串一并给（v2 #5②）。"""
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 200
    n = max(1, min(1000, n))
    try:
        lines = LIFE_LOG_FILE.read_text(encoding="utf-8-sig").strip().splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in lines[-n:]:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        try:
            ts = float(e.get("ts") or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        out.append({
            "ts": ts,
            "iso": _dt.datetime.fromtimestamp(ts).isoformat(timespec="seconds") if ts > 0 else "",
            "doing": str(e.get("doing") or ""),
            "mood": str(e.get("mood") or ""),
        })
    return out


# ============ GAL 回想持久化（Phase 2b，2026-09-10） ============
# 旁路定案：不写 memory.db——messages 表是上下文源，GAL 展示层持久化进去会双写污染；
# 沿 life_log.jsonl 先例走 data/gal_history.jsonl 单行 JSONL 追加。
# 轮缓冲 = 单 WS 槽（webgal 单连接设计）无需锁；落盘/缓冲全程 try/except 全包（debug 留痕）——
# 历史记录故障绝不影响回合与发送。
_HIST_TRIM_BYTES = 2_000_000  # 文件超此字节数触发裁剪（提为常量：测试 monkeypatch 与日后调整）
_HIST_KEEP_LINES = 1000       # 裁剪保留末 N 行（近似行数：切点未必落在轮边界）
_HIST: dict = {"round": None}  # 当前轮缓冲：msg 入帧/reply_start 开轮 → reply_end/err 落盘清空


def _hist_round_open() -> dict:
    """取当前轮缓冲；无开轮则开新轮（幂等：msg 入帧先开、reply_start 沿用同一轮）。"""
    r = _HIST["round"]
    if r is None:
        r = {"round_id": uuid.uuid4().hex[:8], "ts": time.time(), "items": []}
        _HIST["round"] = r
    return r


def _hist_bot_who() -> str:
    """bot 侧发言署名=当前人设卡名；取不到（smoke 环境/异常）固定 'AI'。"""
    try:
        from plugins import brain as _brain  # 函数内 lazy import（模块头纪律：避免循环）

        return str((_brain._persona_card(None) or {}).get("name") or "").strip() or "AI"
    except Exception:  # noqa: BLE001
        return "AI"


def _hist_add(kind: str, who: str, text: str) -> None:
    """向当前轮追加一条（kind：user|bot；action|slice 为 seg 帧未来类型预留），无轮自动开轮。"""
    try:
        _hist_round_open()["items"].append({"kind": str(kind), "who": str(who), "text": str(text or "")})
    except Exception as e:  # noqa: BLE001
        logger.debug("webgal hist add failed: {} [{}]", e, type(e).__name__)


def _hist_flush() -> None:
    """轮结束落盘并清缓冲；幂等防御：round 已 None（err 已落过/重复 reply_end）直接跳过。"""
    r = _HIST["round"]
    if r is None:
        return
    _HIST["round"] = None
    append_gal_history(r)


def append_gal_history(round: dict) -> None:
    """单轮落盘：JSONL 单行追加（ensure_ascii=False，utf-8 文本 "a" 模式）。
    追加前文件 > _HIST_TRIM_BYTES → 读全量、保留末 _HIST_KEEP_LINES 行、临时文件 +
    os.replace 同卷原子重写（旁路展示数据，沿 core/atomics 的同卷替换思路，不必走全套路）。
    全程 try/except 全包 + debug 留痕：任何故障只丢历史，绝不影响回合与发送。"""
    try:
        items = round.get("items") if isinstance(round, dict) else None
        if not items:
            return
        try:
            if GAL_HIST_FILE.stat().st_size > _HIST_TRIM_BYTES:
                lines = GAL_HIST_FILE.read_text(encoding="utf-8-sig").splitlines()
                lines = lines[-_HIST_KEEP_LINES:]
                tmp = GAL_HIST_FILE.with_name(GAL_HIST_FILE.name + ".tmp")
                tmp.write_text("".join(l + "\n" for l in lines), encoding="utf-8")
                os.replace(str(tmp), str(GAL_HIST_FILE))
        except (OSError, ValueError):
            pass  # 首写文件尚不存在 / stat 不可得 / 文件字节损坏解码失败（ValueError）：跳过裁剪直接追加——只丢裁剪不丢当轮
        with open(GAL_HIST_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(round, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        logger.debug("webgal hist append failed: {} [{}]", e, type(e).__name__)


def read_gal_history(limit: int = 100) -> list[dict]:
    """读 data/gal_history.jsonl 尾部 N 轮（limit 钳 1..500），旧→新排序。
    口径仿 read_lifelog：坏行跳过、ts+iso 本地时间一并给；返回 [{round_id, ts, iso, items}]。"""
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 100
    n = max(1, min(500, n))
    try:
        lines = GAL_HIST_FILE.read_text(encoding="utf-8-sig").strip().splitlines()
    except (OSError, ValueError):  # ValueError=坏字节解码（文件级；行级坏 JSON 逐行跳）
        return []
    out: list[dict] = []
    for line in lines[-n:]:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        try:
            ts = float(e.get("ts") or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        items = e.get("items")
        out.append({
            "round_id": str(e.get("round_id") or ""),
            "ts": ts,
            "iso": _dt.datetime.fromtimestamp(ts).isoformat(timespec="seconds") if ts > 0 else "",
            "items": [it for it in items if isinstance(it, dict)] if isinstance(items, list) else [],
        })
    return out


# ============ 注入轮（计划表 §3.3；选型与并发纪律见模块头与函数注释） ============
# 主路 = nonebot.message.handle_event(capture_bot, ev)（nonebot 2.5.0 实测存在，内部建立
# current_bot/current_event matcher 上下文，chat.send/matcher.send/bot.send 全落到 CaptureBot）。
# 备选 = 手工 set current_bot/current_event contextvar 后 await brain.handle(capture_bot, ev)——
# 仅当主路在本机实测不可用时启用：改 _PIPELINE_RUNNER 指向备选实现即可，无需动其余代码。
_PIPELINE_RUNNER = None  # 测试可注入替身；None = 主路

# 合成轮标记（2026-09-10 全项目审计 P2）：_do_round 执行期为 True——web 页面轮是 CaptureBot
# 合成事件（从不注册进 driver），整链"零 QQ 动作"红线由下游据本标记自检（brain._typing_on
# 据此跳过真实 bot 的 set_input_status，不再在主人真实 QQ 上亮「正在输入」）。
_SYNTHETIC = {"on": False}


def in_synthetic_round() -> bool:
    """当前是否处于 web 页面合成轮（_do_round 执行期）。合成轮内禁止一切真实 QQ 动作。"""
    return _SYNTHETIC["on"]


def set_synthetic_round(on: bool) -> None:
    """合成轮标记开关（2026-09-12：**合成通道共用**——GAL 页与 Telegram 都经此置位）。

    标记语义是"当前不在真实 QQ 事件里"，故 telegram 插件也用它：不置位的话，一条 Telegram
    消息触发的管线轮可能去动 QQ 小号（brain._typing_on / 头像 / 输入状态），
    即"合成通道触发了真实 QQ 动作"——webgal 计划表 §五#4 的红线。
    """
    _SYNTHETIC["on"] = bool(on)


async def _default_pipeline_runner(cap_bot, ev) -> None:
    from nonebot.message import handle_event

    await handle_event(cap_bot, ev)


async def _do_round(text: str) -> list[dict]:
    """跑一轮注入：合成事件 → CaptureBot → 管线。返回应推帧列表（text 段；voice/sticker 本批不推）。"""
    from .inject import CaptureBot, build_event, owner_uid

    uid = owner_uid()
    if not uid:
        raise RuntimeError("superusers 为空——页面通道无主人身份可用（v2 同身份裁决）")
    ev = build_event(uid, text)
    cap = CaptureBot()
    runner = _PIPELINE_RUNNER or _default_pipeline_runner
    _SYNTHETIC["on"] = True  # 合成轮标记置位：管线内下游（brain._typing_on 等）据此抑制真实 QQ 动作
    try:
        await runner(cap, ev)
    finally:
        _SYNTHETIC["on"] = False  # try/finally 保证：轮异常也复位（标记只描述"_do_round 执行期"）
    out: list[dict] = []
    # 立绘差分帧（用户裁决 2026-09-10：情绪由 bot 自决，输出开始时切换，整轮不再切）：
    # 基调在合成轮由 brain 保留（get 不 pop），此处先于文本帧推送 sprite 帧。
    # 热修十三 B2（2026-09-12）：emotion 优先级 = _SPRITE_LAST（【立绘：词】独立自决位，非空优先）
    # → _TONE_LAST（9 类基调回退链）→ 空（客户端回默认半身）；同款 get 不 pop（轮内串行，下轮覆盖）。
    _emo = ""
    try:
        from plugins import brain as _brain_tone

        _emo = str(_brain_tone._SPRITE_LAST.get(uid, "") or "").strip()  # 独立立绘词优先：_SPRITE_LAST > _TONE_LAST
        if not _emo:
            _emo = str(_brain_tone._TONE_LAST.get(uid, "") or "").strip()
    except Exception:  # noqa: BLE001
        _emo = ""
    # 每轮必发 sprite 帧：有词=切该差分；空=客户端回默认半身（防止上轮情绪差分残留）
    out.append({"kind": "sprite", "emotion": _emo[:12]})
    out.extend(f for f in cap.captured if f.get("kind") == "text")
    return out


async def _ws_round(ws, text: str) -> None:
    """一轮：reply_start →（逐帧推捕获段）→ reply_end（try/finally 保证；异常回 err 帧）。
    同一 WS 同时只允许一轮——调用方以 round_task.done() 判 busy（reply_end 是本协程最后一帧，
    未发即任务未完成 → busy 判定与协议时序天然一致）。
    Phase 2b 回想：reply_start 开轮（msg 入帧已开则沿用）→ seg(text) 记 bot 条 →
    reply_end/err 落盘清缓冲（sprite 帧无文本内容不入史；落盘异常全吞不影响发送）。"""
    try:
        try:
            _hist_round_open()  # msg 入帧已开轮则沿用；无 msg 直达的轮（如测试直呼）在此开
            await ws.send_json({"type": "reply_start"})
            frames = await _do_round(text)
            for f in frames:
                if f.get("kind") == "sprite":
                    await ws.send_json({"type": "sprite", "emotion": str(f.get("emotion") or "")})
                elif f.get("kind") == "text":
                    _hist_add("bot", _hist_bot_who(), str(f.get("text") or ""))
                    await ws.send_json({"type": "seg", "kind": "text", "text": str(f.get("text") or "")})
        except Exception as e:  # noqa: BLE001
            logger.warning("webgal round failed: {} [{}]", e, type(e).__name__)
            _hist_flush()  # err 帧前落盘（异常前已入史的部分内容照保）；finally 里重复调用被 None 守卫跳过
            try:
                await ws.send_json({"type": "err", "reason": (str(e) or "round_error")[:120]})
            except Exception:  # noqa: BLE001
                pass
    finally:
        _hist_flush()  # 轮统一落盘点（正常/异常都到）；幂等：round 已 None 则 no-op
        try:
            await ws.send_json({"type": "reply_end", "mode": get_mode()})
        except Exception:  # noqa: BLE001
            pass


_INFLIGHT: set = set()  # 在飞轮任务集：busy 的跨连接判据——断线重连窗口防双轮 brain 并发（R1 #4）


def _round_done_cb(t: asyncio.Task) -> None:
    """轮任务收尾：异常兜底记录 + 在飞轮集合清理（create_task 火后不管，异常不能无人取）。"""
    _INFLIGHT.discard(t)
    if t.cancelled():
        return
    exc = t.exception()
    if exc:
        logger.warning("webgal round task error: {} [{}]", exc, type(exc).__name__)


# ============ HTTP/WS 挂载（插件加载期 get_app，bot.py:79-137 同款；事实 #2 预期零 bot.py 改动） ============
_APP = None
_MOUNT_ERR = ""
# WS 单连接状态（模块级 holder；asyncio.Lock 仅护住换位窗口）
_WS_LOCK = asyncio.Lock()
_WS_CURRENT: dict = {"ws": None}
_AUTHED_OK: set = set()  # 已鉴权连接的 id(ws)（连接对象生命周期与该集合同步增删）


def _mount() -> None:
    global _APP
    from fastapi.responses import FileResponse, PlainTextResponse

    from nonebot import get_app

    app = get_app()
    _APP = app

    @app.get("/gal")
    async def _gal_page():
        if GAL_PAGE_FILE.is_file():
            return FileResponse(str(GAL_PAGE_FILE), media_type="text/html")
        return PlainTextResponse(
            "GAL 页面文件缺失：launcher/web/gal.html 尚未就位（前端任务交付后此页自动可用）。"
        )

    @app.get("/gal/assets/{name}")
    async def _gal_asset(name: str):
        if name not in ASSET_WHITELIST:
            return PlainTextResponse("not found", status_code=404)
        p = GAL_PAGE_FILE.parent / name
        if p.is_file():
            return FileResponse(str(p))
        return PlainTextResponse(f"asset missing: {name}", status_code=404)

    @app.get("/gal/state")
    async def _gal_state():
        return _state_payload()

    @app.get("/gal/lifelog")
    async def _gal_lifelog(limit: int = 200):
        return read_lifelog(limit)

    @app.get("/gal/history")
    async def _gal_history(limit: int = 100):
        return read_gal_history(limit)

    @app.get("/gal/stage/{name:path}")
    async def _gal_stage(name: str):
        """舞台素材（type=gal 包内的 bg/ 与 sprites/diff/）。

        为什么需要这个路由（2026-09-12 T14）：舞台包的背景/差分像素原先**根本没有服务出口**
        （/gal/assets/ 只放行 gal.js/gal.css 两个文件名），等于把 bg 文件放进包也显示不出来。
        校验全部在 core.packs.stage_file 里（包必须 type=gal + resolve 拦路径穿越 + 必须存在文件），
        本处只做 MIME 与 404。

        `{name:path}` 形如 `<包名>/bg/room.png`——包名是点分小写（author.name），含点不影响匹配。"""
        try:
            from core import packs as _packs  # 函数内 lazy import（与 /gal/content.json 同纪律）

            p, _pk = _packs.stage_file(name.split("/", 1)[0], name.split("/", 1)[1] if "/" in name else "")
        except Exception as e:  # noqa: BLE001
            logger.warning("webgal stage resolve failed: {} [{}]", e, type(e).__name__)
            return PlainTextResponse("stage unavailable", status_code=404)
        if p is None:
            return PlainTextResponse("not found", status_code=404)
        return FileResponse(str(p))

    @app.get("/gal/content.json")
    async def _gal_content():
        """内容包索引（robot-pack-v1 Phase 3）：台词/差分映射的外置数据源（gal.js 启动时拉取一次）。
        载荷与合并序见 core.packs.content_index；失败回空载荷=前端台词行隐藏、差分映射走内置默认。
        2026-09-12 T14：载荷增 stage 键（舞台背景/场景映射），**失败回退里也要带**——
        前端只认"有没有这个键"，缺键与空值在它眼里是两回事。"""
        try:
            from core import packs as _packs  # 函数内 lazy import（core 模块按需加载）

            return _packs.content_index()
        except Exception as e:  # noqa: BLE001
            logger.warning("webgal content index failed: {} [{}]", e, type(e).__name__)
            return {"quotesByCard": {}, "quoteFallback": [], "spriteMap": {},
                    "stage": {"default_bg": "", "bg_alt": "", "bg_by_scene": {}, "roots": []}}

    @app.post("/gal/tts")
    async def _gal_tts(data: dict = Body(default={})):
        text = str((data or {}).get("text") or "").strip()
        tone = str((data or {}).get("tone") or "")
        if not text:
            return {"err": "empty text"}
        text = text[:300]  # 钳制：超长文本会长时间持 _tts_lock 饿死 QQ 语音链（R1 #6）
        try:
            from plugins import voice as _voice  # 函数内 lazy import（voice 顶层也不依赖本插件）

            wav = await _voice.tts_wav(text, tone)
        except Exception as e:  # noqa: BLE001
            logger.warning("webgal tts failed: {} [{}]", e, type(e).__name__)
            return {"err": (str(e) or type(e).__name__)[:120]}
        if not wav:
            return {"err": "tts unavailable"}
        return {"wav_b64": base64.b64encode(wav).decode("ascii")}

    @app.websocket("/gal/ws")
    async def _gal_ws(ws: WebSocket):
        """单连接 WS：首帧必须 auth（token）；此后 msg/mode/ping。协议细节见模块头与计划表 §3.2。"""
        await ws.accept()
        async with _WS_LOCK:
            if _WS_CURRENT.get("ws") is not None:
                try:
                    await ws.send_json({"type": "err", "reason": "busy"})
                    await ws.close()
                except Exception:  # noqa: BLE001
                    pass
                return
            _WS_CURRENT["ws"] = ws
        round_task: asyncio.Task | None = None
        try:
            while True:
                # 未鉴权 10s 内必须 auth；已鉴权 90s 空闲上限（客户端 30s ping 保活）——
                # 僵尸/半开连接自愈释放唯一 slot，无需重启（盲审 P3-3 + P7 场景冒烟 + R1 #5）
                _authed = id(ws) in _AUTHED_OK
                try:
                    raw = await asyncio.wait_for(ws.receive_text(), timeout=(90 if _authed else 10))
                except asyncio.TimeoutError:
                    try:
                        await ws.send_json({"type": "err", "reason": ("idle timeout" if _authed else "auth timeout")})
                        await ws.close()
                    except Exception:  # noqa: BLE001
                        pass
                    return
                try:
                    frame = json.loads(raw)
                except ValueError:
                    await ws.send_json({"type": "err", "reason": "bad_json"})
                    continue
                if not isinstance(frame, dict):
                    await ws.send_json({"type": "err", "reason": "bad_frame"})
                    continue
                ftype = str(frame.get("type") or "")
                if ftype == "auth":
                    if str(frame.get("token") or "") == get_token():
                        _AUTHED_OK.add(id(ws))
                        await ws.send_json({"type": "auth_ok", **_state_payload()})
                    else:
                        await ws.send_json({"type": "auth_err", "reason": "token mismatch"})
                        await ws.close()
                        return
                    continue
                if id(ws) not in _AUTHED_OK:
                    await ws.send_json({"type": "auth_err", "reason": "auth first"})
                    await ws.close()
                    return
                if ftype == "msg":
                    text = str(frame.get("text") or "").strip()
                    if not text:
                        continue  # 忽略空消息（未知字段一并忽略）
                    if len(text) > 4000:
                        await ws.send_json({"type": "err", "reason": "text too long"})
                        continue  # 服务端长度钳制：maxlength 可绕（R1 #6）
                    if (round_task is not None and not round_task.done()) or any(not t.done() for t in _INFLIGHT):
                        # 跨连接 busy：轮中途断线重连的新连接也要等旧轮收尾（R1 #4）
                        await ws.send_json({"type": "err", "reason": "busy"})
                        continue
                    if get_mode() == "qq":
                        # qq 模式页面不出输入框（v2 #1）；服务端再拦一道防双通道身份分裂
                        await ws.send_json({"type": "err", "reason": "qq_mode"})
                        continue
                    # Phase 2b 回想：用户页面输入入史（页面发送者显示名服务端不可得，固定"主人"）；
                    # 无开轮则在此开轮——用户条目先于本轮 bot 条目，时序与页面一致
                    _hist_add("user", "主人", text)
                    round_task = asyncio.get_running_loop().create_task(_ws_round(ws, text))
                    round_task.add_done_callback(_round_done_cb)
                    _INFLIGHT.add(round_task)
                elif ftype == "mode":
                    m = set_mode(str(frame.get("mode") or ""))
                    if m:
                        await ws.send_json({"type": "state", **_state_payload()})
                    else:
                        await ws.send_json({"type": "err", "reason": "bad_mode"})
                elif ftype == "ping":
                    await ws.send_json({"type": "pong"})
                # 未知帧：忽略
        except Exception as e:  # noqa: BLE001
            # WebSocketDisconnect（客户端断开）与其他异常统一收口：清连接位，轮任务由 done_callback 记录
            logger.debug("webgal ws closed: {} [{}]", e, type(e).__name__)
        finally:
            async with _WS_LOCK:
                if _WS_CURRENT.get("ws") is ws:
                    _WS_CURRENT["ws"] = None
            _AUTHED_OK.discard(id(ws))


try:
    startup_reset()
except Exception as _sr_e:  # noqa: BLE001
    logger.warning("webgal startup reset failed: {}", _sr_e)

try:
    _mount()
except Exception as _m_e:  # noqa: BLE001
    _MOUNT_ERR = f"{_m_e} [{type(_m_e).__name__}]"
    logger.warning("webgal mount failed（兜底口：bot.py 可调 plugins.webgal._mount() 重挂）: {}", _MOUNT_ERR)
