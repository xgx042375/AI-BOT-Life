"""记忆服务：短时（近 N 轮对话 + 滚动摘要）+ 长时（事实库 + 关系状态）。

- log_message: 每次对话落库（也是 P3 训练数据源）
- build_context: 组装注入 LLM 的记忆文本
- extract_and_store: 后台 LLM 提取事实/情绪/亲密度/摘要（异步任务）
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nonebot import get_driver
from openai import AsyncOpenAI

from .store import db

from core import atomics
from core import llm as core_llm  # 2026-09-10 Phase1：LLM Provider 适配层
from core.paths import data_path  # 2026-09-12 I 项：状态一律进唯一数据根（内容根只放随仓内容）

logger = logging.getLogger("memory")


def log_message(user_id: str, group_id: str | None, role: str, content: str, sender_name: str | None = None):
    db.add_message(user_id, group_id, role, content, sender_name=sender_name)


SENSITIVE_KEY = "敏感触发词"  # 长时记忆 key：'肉棒' 敏感词触发记录

# 语义检索阈值：facts 超过该数量后从全量注入切换为向量 Top-K 召回
FACT_RECALL_THRESHOLD = 20
FACT_RECALL_TOP_K = 8
FACT_DEDUPE_THRESHOLD = 0.9  # 提取时语义查重相似度阈值（≥ 视为同一事实，转 update）


def _fact_is_sensitive(f: dict) -> bool:
    """事实是否含色情/亲密敏感内容（群聊收敛时过滤）。
    2026-09-08：去掉单字「性」（"性格：内向""女性"等正常事实曾被子串误杀），
    改用复合词；「敏感」保留——含"敏感"的事实（如"对某话题敏感"）同样不宜进群聊上下文
    （盲审指出曾静默删除该词，属过滤面悄悄变宽，现恢复并交代）。"""
    SENSITIVE_HINTS = ("肉棒", "性爱", "性癖", "性欲", "做爱", "爱爱", "骚", "情欲", "高潮", "敏感", "身体", "色色")
    text = f"{f.get('key', '')}：{f.get('value', '')}"
    return any(h in text for h in SENSITIVE_HINTS)


# 2026-09-08 P3：客户端单例化——曾每次调用新建 AsyncOpenAI（每 2 轮 extract + 每日/周复盘），
# 旧实例靠 GC 回收、连接池反复重建
_CLIENT: AsyncOpenAI | None = None


def _client() -> AsyncOpenAI:
    global _CLIENT
    if _CLIENT is None:
        # 2026-09-10 Phase1：构造收拢 core.llm（默认本地配置下行为零变化；属性名 _client 保留，smoke 换桩依赖）
        _CLIENT = core_llm.get_client("memory")
    return _CLIENT


# 2026-09-08 P-2 召回衰减：真人记忆会淡忘——久远的淡出排序、重大的（高情绪）保留。
# 只影响召回排序与注入取舍，库中原数据一律不删（用户问起"还记得吗"仍可答）；
# 既有召回底线（相似度阈值，如 0.58 语义召回门槛）原样保留，衰减不得突破/修改任何阈值。
RECALL_HALF_LIFE_DAYS = 14.0  # 时间衰减半衰期（天）：14 天前的记忆权重减半
EMOTION_BOOST_MAX = 0.3       # 情绪强度加成上限：intensity=1.0 时该条权重最高 ×1.3


def _recall_decay(ts: str, now: datetime | None = None) -> float:
    """P-2 时间衰减因子：decay = 0.5 ** (age_days / 14)（按 ts 指数衰减，半衰期 14 天）。
    ts 为空/解析失败 → 1.0（不加罚）。只乘在召回排序分上，不删数据。"""
    try:
        t = datetime.fromisoformat(str(ts)).astimezone()
        n = (now or datetime.now(timezone.utc)).astimezone()
        age_days = max(0.0, (n - t).total_seconds() / 86400.0)
        return 0.5 ** (age_days / RECALL_HALF_LIFE_DAYS)
    except (ValueError, TypeError, OverflowError, OSError):
        return 1.0


def _recall_boost(user_id: str, ts: str) -> float:
    """P-2 情绪强度加成：boost = 1 + 0.3 × intensity（该事实时间 ±1 天内的最强情绪）。
    取不到情绪记录 → 1.0（不加成）。B5：多事实批量路径用 _recall_boost_map（单点查询保留）。"""
    try:
        inten = db.get_emotion_intensity_near(user_id, ts)
        if inten is None:
            return 1.0
        return 1.0 + EMOTION_BOOST_MAX * max(0.0, min(1.0, float(inten)))
    except Exception:  # noqa: BLE001
        return 1.0


def _recall_boost_map(user_id: str, ts_list) -> dict[str, float]:
    """B5（2026-09-09 审计）：_recall_boost 的批量化——召回重排曾对每个事实各发一次
    「±1 天最强情绪」SQL（O(n) 次 DB 往返/次召回）。改为一次
    `SELECT ts,intensity FROM emotions WHERE user_id=? AND ts>=?`（下界=各事实窗口最早值），
    Python 侧按各事实自己的窗口取 MAX——打分语义与逐事实查询一致。失败按 1.0 兜底。"""
    tss = sorted({str(t or "") for t in (ts_list or []) if t})
    if not tss:
        return {}
    try:
        lo = None
        for t in tss:
            try:
                _lo = (datetime.fromisoformat(t).astimezone(timezone.utc) - timedelta(days=1.0)).isoformat(timespec="seconds")
                lo = _lo if lo is None else min(lo, _lo)
            except (ValueError, TypeError, OverflowError, OSError):
                continue
        if lo is None:
            return {}
        _parsed = []
        for r in db.get_emotions_since(user_id, lo):
            try:
                _parsed.append((datetime.fromisoformat(str(r["ts"])).astimezone(timezone.utc), float(r["intensity"])))
            except (ValueError, TypeError, OverflowError, OSError):
                continue
        out: dict[str, float] = {}
        for t in tss:
            try:
                t0 = datetime.fromisoformat(t).astimezone(timezone.utc)
            except (ValueError, TypeError, OverflowError, OSError):
                continue  # 解析失败不进 map → 调用方按 1.0（同 get_emotion_intensity_near 返回 None 口径）
            _wlo, _whi = t0 - timedelta(days=1.0), t0 + timedelta(days=1.0)
            _inten = max((i for (_ets, i) in _parsed if _wlo <= _ets <= _whi), default=None)
            out[t] = 1.0 if _inten is None else 1.0 + EMOTION_BOOST_MAX * max(0.0, min(1.0, _inten))
        return out
    except Exception:  # noqa: BLE001
        return {}


def _recall_score(user_id: str, sim: float, ts: str, now: datetime | None = None,
                  boost: float | None = None) -> float:
    """P-2 召回打分：final = sim × 0.5**(age_days/14) × (1 + 0.3×intensity_near)。
    衰减与加成只影响召回排序/注入取舍（打分序），不删数据、不改任何相似度阈值。
    B5：boost 显式传入时不再逐条查库（批量路径 _recall_boost_map 预取）。"""
    b = _recall_boost(user_id, ts) if boost is None else boost
    return float(sim) * _recall_decay(ts, now=now) * b


async def _recall_facts(user_id: str, facts: list[dict]) -> list[dict] | None:
    """按最近一条用户消息的语义向量召回 Top-K 事实。embedding 不可用时返回 None（调用方全量注入）。
    2026-09-07 P1：改用异步 embed——同步 httpx.Client 曾在事件循环内阻塞全 bot（≤10s/次）。"""
    try:
        from .embeddings import embed_text_async, top_k

        last_user = db.get_last_user_message(user_id)
        if not last_user:
            return None
        qemb = await embed_text_async(last_user)
        if not qemb:
            return None
        items = db.get_fact_embeddings(user_id)
        if not items:
            return None
        picked = top_k(qemb, items, k=FACT_RECALL_TOP_K)
        # 2026-09-08 P-2：召回重排——final = sim × 时间衰减(半衰期14d) × 情绪强度加成(±1天，取不到不加成)。
        # 只影响召回内的排序（注入取舍：淡出的旧事实沉到注入尾部，经 build_context 既有
        # "遗忘曲线"渲染自然模糊化）；相似度底线与库数据不动。
        # B5：情绪加成批量化——一次范围查询预取全部候选行，排序键不再逐事实查库。
        _fact_ts = {f["key"]: str(f.get("last_seen_ts") or "") for f in facts}
        _boosts = _recall_boost_map(user_id, {_fact_ts.get(k, "") for k, _ in picked})
        picked = sorted(
            picked,
            key=lambda kv: -_recall_score(
                user_id, kv[1], _fact_ts.get(kv[0], ""),
                boost=_boosts.get(_fact_ts.get(kv[0], ""), 1.0),
            ),
        )
        by_key = {f["key"]: f for f in facts}
        return [by_key[k] for k, _ in picked if k in by_key] or None
    except Exception:  # noqa: BLE001
        return None


EVENT_RECALL_TOP_K = 3  # 事件召回数量


async def _recall_events(user_id: str, since_ts: str | None = None) -> list[str] | None:
    """事件记忆召回：向量 Top-K；embedding 不可用或事件为空 → None（不注入）。
    since_ts：人设切换时间——旧人设时代的事件不召回。
    2026-09-07 P1：改用异步 embed（同上）。"""
    try:
        from .embeddings import embed_text_async, top_k

        items = db.get_event_embeddings(user_id)
        if not items:
            return None
        if since_ts:
            items = [(eid, ts, content, emb) for eid, ts, content, emb in items if (ts or "") >= since_ts]
            if not items:
                return None
        last_user = db.get_last_user_message(user_id)
        qemb = (await embed_text_async(last_user)) if last_user else None
        if not qemb:
            return None
        picked = top_k(qemb, [(content, emb) for _, _, content, emb in items], k=EVENT_RECALL_TOP_K)
        # 相似度过低（<0.5）视为不相关，不注入
        relevant = [content for content, score in picked if score >= 0.5]
        # 2026-09-06 重要性记忆（agent 层【重要】标注）：里程碑优先（置顶），且低相似（>=0.3）也保住
        imp_extra = [content for content, score in picked if score >= 0.3 and content.startswith("【重要】")]
        relevant = sorted(set(imp_extra) | set(relevant), key=lambda c: (not c.startswith("【重要】"), c))
        return relevant or None
    except Exception:  # noqa: BLE001
        return None


DAILY_REVIEW_PROMPT = (
    "你是记忆复盘员。以下是某用户今日与 AI 的对话记录。请复盘并提取**最有效的信息**：\n"
    "1. facts：对话中明确出现的新事实/偏好/关系变化（key:value，与已有记忆冲突的用 update 语义）；\n"
    "2. review：一段 ≤80 字的当日复盘（今日聊了什么、关系/互动有什么变化、哪些互动模式最有效——"
    "用户对什么回应最好、什么话题让 AI 发挥最好）。\n"
    "3. pending：今日**未完成话题**（≤30 字）：聊到一半被打断、对方说\"下次告诉你/回头讲给我\"、"
    "明显悬而未决、今天该接没接的事；没有就空字符串。\n"
    "4. 【状态期演出排除】对话中出现的催眠射线/催眠/洗脑/道具（口球/淫纹/震动棒）/情欲等"
    "临时状态情节，其中用户的言行属于**临时状态下的演出，不是稳定偏好**——"
    "不得提炼为 facts；如需记录，仅放 events 并注明（状态期演出）。\n"
    "输出严格 JSON：{\"facts\":[{\"key\":\"\",\"value\":\"\",\"confidence\":0.0}],"
    "\"review\":\"当日复盘文本\",\"pending\":\"未完成话题或空\"}"
)
WEEKLY_REVIEW_PROMPT = (
    "你是记忆复盘员。以下是某用户本周与 AI 的对话复盘 + 你本周的生活日志。请提炼**一周层面**的信息：\n"
    "1. weekly：一段 ≤120 字的周总结（本周关系/偏好/话题的趋势变化、长期有效互动模式、需要记住的关键点）；\n"
    "2. facts：跨日重复出现或本周显著的新事实（key:value）。\n"
    "3. life：**生活线摘要**——本周你的生活轨迹（在做什么、去过哪、日常的起伏；≤60 字）——"
    "它是你这周的日子，不是对话；没有明显可写就空字符串。\n"
    "4. wishes：**她的长期愿望/想做的事**（想学什么、想去哪、想要什么、想成为什么——从本周对话与生活线里提炼；每项 ≤20 字，最多 2 项；没有则空数组）。\n"
    "5. 【状态期演出排除】每日复盘中提到的催眠/洗脑/道具/情欲等临时状态情节属于演出，"
    "不得据此提炼为用户的稳定偏好 facts。\n"
    "输出严格 JSON：{\"facts\":[{\"key\":\"\",\"value\":\"\",\"confidence\":0.0}],"
    "\"weekly\":\"周总结文本\",\"life\":\"周生活线摘要或空\",\"wishes\":[\"愿望1\",\"愿望2\"]}"
)


async def run_daily_review(user_id: str, date: str) -> bool:
    """每日复盘：取当日对话 → LLM 提取 facts+review → 写入。失败不阻塞。"""
    try:
        conn_text = db.get_day_messages_text(user_id, date)
        if not conn_text:
            return False
        resp = await _client().chat.completions.create(
            model="daily-review",
            messages=[
                {"role": "system", "content": DAILY_REVIEW_PROMPT},
                {"role": "user", "content": f"今日对话（{date}）：\n{conn_text[:6000]}"},
            ],
            temperature=0.2,
            max_tokens=600,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        raw = (resp.choices[0].message.content or "").strip().strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
        data = json.loads(raw)
        from datetime import datetime as _dt, timezone as _tz

        ts = _dt.now(_tz.utc).isoformat(timespec="seconds")
        from .embeddings import embed_text_async

        for f in data.get("facts", []):
            key = str(f.get("key", "")).strip()
            value = str(f.get("value", "")).strip()
            conf = float(f.get("confidence", 0.7))
            if key and value and conf >= 0.6:
                db.upsert_fact(user_id, key, value, conf, ts)
                # 2026-09-08 P1：补写向量——此前只有 extract 路径落 embedding，facts 超过
                # FACT_RECALL_THRESHOLD 走语义 Top-K 召回后，复盘新增的事实永远进不了候选，
                # 被静默过滤出上下文（服务越正常丢得越干净）。
                _emb = await embed_text_async(f"{key}：{value}")
                if _emb:
                    db.save_fact_embedding(user_id, key, _emb)
        review = str(data.get("review", "")).strip()
        pending = str(data.get("pending", "")).strip()[:30]
        if review:
            db.upsert_daily_review(user_id, date, review, pending)
        logger.info(
            "daily review done: user=%s date=%s facts=%d review=%d pending=%d",
            user_id, date, len(data.get("facts", [])), len(review), len(pending),
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("daily review failed: %s", e)
        return False


async def run_weekly_review(user_id: str, week_start: str, since_date: str) -> bool:
    """每周复盘：取本周每日复盘 → LLM 提取 weekly+facts → 写入。
    C8（2026-09-09 审计）：不再写 summaries——周复盘已有专属表（weekly_reviews）且
    build_context 经 get_latest_weekly_review 注入，summaries 副本纯冗余（还占 20 条容量）。"""
    try:
        daily = db.get_daily_reviews(user_id, since_date=since_date, limit=10)
        if not daily:
            return False
        daily_text = "\n".join(f"[{d['date']}] {d['content']}" for d in daily)
        # 2026-09-08 批次C：周复盘带本周生活日志（心跳轨迹）→ 输出周生活线（life 字段）
        _life_text = ""
        try:
            from core.paths import data_path as _dp
            import json as _json, time as _t

            _lp = _dp("life_log.jsonl")
            if _lp.exists():
                _week_rows = []
                for _ln in _lp.read_text(encoding="utf-8-sig").strip().splitlines():
                    try:
                        _e = _json.loads(_ln)
                        if not isinstance(_e, dict) or not _e.get("ts"):
                            continue  # 盲审修正：非 dict 合法 JSON/缺 ts 行跳过（曾整周 life 被吞）
                        if _t.strftime("%Y-%m-%d", _t.localtime(float(_e.get("ts", 0)))) >= since_date:
                            _d = str(_e.get("doing") or "").strip()[:50]
                            if _d:
                                _week_rows.append(_d)
                    except (ValueError, json.JSONDecodeError, TypeError, KeyError):
                        continue
                if _week_rows:
                    _life_text = "；".join(_week_rows[-15:])
        except Exception:  # noqa: BLE001
            _life_text = ""
        resp = await _client().chat.completions.create(
            model="weekly-review",
            messages=[
                {"role": "system", "content": WEEKLY_REVIEW_PROMPT},
                {"role": "user", "content": (
                    f"本周每日复盘：\n{daily_text[:6000]}"
                    + (f"\n\n本周你的生活日志：\n{_life_text}" if _life_text else "")
                )},
            ],
            temperature=0.2,
            max_tokens=700,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        raw = (resp.choices[0].message.content or "").strip().strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
        data = json.loads(raw)
        from datetime import datetime as _dt, timezone as _tz

        ts = _dt.now(_tz.utc).isoformat(timespec="seconds")
        from .embeddings import embed_text_async

        for f in data.get("facts", []):
            key = str(f.get("key", "")).strip()
            value = str(f.get("value", "")).strip()
            conf = float(f.get("confidence", 0.7))
            if key and value and conf >= 0.6:
                db.upsert_fact(user_id, key, value, conf, ts)
                # 2026-09-08 P1：同日复盘——周复盘 facts 同样补向量（否则同批事实召回不可见）
                _emb = await embed_text_async(f"{key}：{value}")
                if _emb:
                    db.save_fact_embedding(user_id, key, _emb)
        weekly = str(data.get("weekly", "")).strip()
        life_line = str(data.get("life", "")).strip()[:60]  # 盲审：截断与 prompt 限宽对齐（曾 80>60）
        if weekly:
            db.upsert_weekly_review(user_id, week_start, weekly, life_line)
            # C8：周复盘不再写 summaries 副本（weekly_reviews 专属表 + build_context 注入已覆盖）
        # 2026-09-08 D2：周复盘提炼她的长期愿望 → wish 域（按卡隔离——身份痕迹绑身份；卡名读 persona_select）
        _wishes = [str(x).strip()[:40] for x in data.get("wishes", []) if str(x).strip()] if isinstance(data.get("wishes", []), list) else []
        if _wishes:
            try:
                from agent import lifesim as _ls_w

                # 2026-09-08 盲审修正①：卡名读取与 brain._persona_name 回退对齐（曾直读 sel.get(uid)——
                # 未选卡的 owner 会落入 default:{uid} 与脑侧 _aoding_:{uid} 错位、愿望"写进去读不到"；
                # 回退链=用户选择→主人选择→.env persona（与 brain 同源同序）
                _card = ""
                try:
                    import json as _json_w
                    from core.paths import data_path as _dp_w

                    _sel = _json_w.loads(_dp_w("persona_select.json").read_text(encoding="utf-8-sig"))
                    _card = str(_sel.get(str(user_id), "") or "")
                    if not _card:
                        from nonebot import get_driver as _gd_w

                        _own = ""
                        try:
                            _su_w = set(str(x) for x in (getattr(_gd_w().config, "superusers", set()) or set()))
                            _own = next(iter(_su_w)) if _su_w else ""
                        except Exception:  # noqa: BLE001
                            _own = ""
                        _card = str(_sel.get(_own, "") or "")
                    if not _card:
                        _card = str(getattr(_gd_w().config, "persona", "") or "")
                except Exception:  # noqa: BLE001
                    _card = ""
                for _w in _wishes[:2]:
                    if not _ls_w.remember_wish(_card, user_id, _w):
                        # C9：失败不再静默计数为零——fail-closed 拒写时主人侧可从日志看到愿望没存上
                        logger.warning("weekly wish save failed (fail-closed): user=%s card=%s text=%s", user_id, _card or "-", _w[:20])
                logger.info("weekly wishes saved: user=%s card=%s n=%d", user_id, _card or "-", len(_wishes))
            except Exception as _we:  # noqa: BLE001
                logger.debug("wish save failed: %s", _we)
        logger.info(
            "weekly review done: user=%s week=%s daily=%d review=%d life=%d",
            user_id, week_start, len(daily), len(weekly), len(life_line),
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("weekly review failed: %s", e)
        return False


async def build_context(user_id: str, functional: bool = False, group_safe: bool = False, persona_ts: str | None = None) -> str:
    """2026-09-07 P1：转 async（内部语义召回用异步 embed，曾同步阻塞事件循环）。
    调用方：brain.handle（已 await）；tools/test_scenarios*.py 为离线脚本需自行 asyncio.run。"""
    parts = []
    # 2026-09-09 深夜：并行化（结果互不依赖）——事件召回与事实召回是两条互不依赖的 embed HTTP 往返：
    # 各自独立读库（get_last_user_message/get_*_embeddings）+ 向量打分，无共享可变状态，仅返回值被各自消费。
    # 事件召回先并发启动（此处守卫与原调用点完全一致），其 HTTP 往返与下方 facts 读库/事实召回重叠；
    # 事实召回需要 facts 入参（依赖上方读库结果），保持原位原样 await。异常语义不变：两者内部本就
    # 全量 try/except（失败=None），await 并发任务与原单发 await 的失败路径完全一致。
    _evt_task = (
        asyncio.ensure_future(_recall_events(user_id, since_ts=persona_ts))
        if (not functional and not group_safe) else None
    )
    # 人设切换时间过滤（2026-09-04）：记忆库中的 RP 演出记录归属旧人设（如奥汀的办公室/傲娇/魔眼），
    # 切新人设后不再注入（user_ 前缀=用户画像，跨人设有效，保留）
    if not functional:
        facts = db.get_facts(user_id)
        if persona_ts:
            facts = [
                f for f in facts
                if str(f.get("key", "")).startswith("user_")
                or not f.get("last_seen_ts")
                or str(f["last_seen_ts"]) >= persona_ts
            ]
        if facts:
            # 群聊收敛（group_safe）：剔除敏感/色情相关事实（防群聊回复被带偏）
            if group_safe:
                from . import embeddings as _emb  # noqa: F401  （占位，避免循环 import 检查）

                facts = [f for f in facts if f["key"] != SENSITIVE_KEY and not _fact_is_sensitive(f)]
            # 语义检索：facts 超过阈值后按最近对话主题向量 Top-K 召回（embedding 服务不可用自动降级全量）
            if len(facts) > FACT_RECALL_THRESHOLD:
                recalled = await _recall_facts(user_id, facts)
                if recalled is not None:
                    facts = recalled
            if facts:
                # 遗忘曲线（2026-08-30）：>14 天未再提及的事实 = 模糊记忆（更像真人）
                try:
                    from datetime import datetime as _dtf, timezone as _tzf

                    _nowf = _dtf.now(_tzf).astimezone()
                    _aged = []
                    for f in facts:
                        _t = _dtf.fromisoformat(str(f.get("last_seen_ts", ""))).astimezone() if f.get("last_seen_ts") else None
                        if _t and (_nowf - _t).total_seconds() > 86400 * 14:
                            _aged.append(f"{f['key']}: {f['value']}（记得不太清楚了，细节有些模糊）")
                        else:
                            _aged.append(f"{f['key']}: {f['value']}")
                    parts.append("关于对方的事实：" + "；".join(_aged))
                except Exception:  # noqa: BLE001
                    parts.append("关于对方的事实：" + "；".join(f"{f['key']}: {f['value']}" for f in facts))
    rel = db.get_relation(user_id)
    mood = (rel["mood"] if rel else "") or ""
    # 2026-09-07 改接唯一状态源：特殊状态属 core/special（曾读 legacy hypno_state.json——
    # 特殊层级重写后该文件永不被写，hypno 感知/【身体遵从】切片长期失效）
    try:
        from core import special as _sp7

        _act7 = _sp7.active()
    except Exception:  # noqa: BLE001
        _act7 = {}
    _hypno_entry = _act7.get("hypno") or {}
    hypno = bool(_hypno_entry)
    # 2026-09-07 用户裁决：催眠演出（心理暗示/特性强制）仅对管理员（主人）对话注入——
    # 催眠是主人设定的状态，非管理员对话不应看到催眠演出（也不引导【身体遵从】切片）
    try:
        from nonebot import get_driver as _gd7

        _su7 = set(str(x) for x in (getattr(_gd7().config, "superusers", set()) or set()))
        _is_admin = str(user_id) in _su7
    except Exception:  # noqa: BLE001
        _is_admin = False
    if hypno and not group_safe and _is_admin:
        # 2026-09-09 agent 自决化重写：删人设假设与三段式处方（铁律#2 + 无范例化裁决）——
        # 只述状态事实与切片机制；怎么演、切片写什么，全部由 agent 按人设与现场自决
        note = str(_hypno_entry.get("note") or "").strip()
        if "心理暗示" in note:
            _raw = note.replace("心理暗示", "").replace("（表面照常，心底被写入）：", "").replace("：", "").strip() or "无条件服从主人"
            parts.append(
                f"【心理暗示·心底】你的内心深处被写入了一条暗示：「{_raw}」。"
                "它**不改变你的表面人格**——你依然是原来的自己；"
                "暗示与表面人格的拉扯会在你的言行中自然流露。"
            )
        else:
            parts.append(
                f"【催眠·特性强制】你的身体被强制遵从「{note or '主人的命令'}」，但你的思想没有转变。"
                "身体不受控的部分如需单独呈现，用【身体遵从】标记切成第二条消息，"
                "标记会被机器剥除、不会出现在消息里；切片写什么、怎么写，由你按人设与现场自决。"
            )
        logger.info("build_context: hypno applied (note=%r)", note[:40])
    elif rel:
        _inv = float(rel.get("intimacy") or 0.0)
        if _inv >= 100:
            parts.append("当前关系：你对他（对方）的好感已恒定满值（100/100，不随对话增减），你此刻的心情：" + (mood or "—"))
        elif _inv > 0:
            parts.append(
                f"当前关系：你对他（对方）的好感正在积累（{round(_inv)}/100）："
                "经常交流、互相在意会让它慢慢升温；你此刻的心情：" + (mood or "—")
            )
        # _inv == 0：无关系注记（普通用户/轨道起步）
        # 2026-09-06 情绪连续体：最近轨迹（弧线感知——逐轮开心/低落的变化；不解释、顺着感觉来）
        try:
            _emn = db.get_recent_emotions(user_id, limit=6)
            _arc = "→".join(e["emotion"] for e in _emn if e.get("emotion") and e["emotion"] != "平静")
            if _arc:
                parts.append(f"（最近的情绪轨迹：{_arc}——它只属于你，不用解释。）")
        except Exception:  # noqa: BLE001
            pass
    if not functional and not group_safe:  # 2026-09-05：摘要/复盘源自私聊提取——群聊收敛不注入（防私聊内容串入群聊）
        summary = db.get_latest_summary(user_id)
        if summary and (not persona_ts or not summary.get("ts") or str(summary["ts"]) >= persona_ts):
            parts.append(f"过往对话摘要：{summary['content']}")        # 复盘注入：最近日复盘 + 最近周复盘（自动提取的有效信息，增强记忆）
        # 2026-09-08：persona_ts（UTC ISO）转本地日期再比较——曾取 [:10]（UTC 日期）与
        # daily.date/week_start（本地日期）比，UTC+8 晚间切人设存在一天误差
        _p_local = ""
        if persona_ts:
            try:
                from datetime import datetime as _pd

                _p_local = _pd.fromisoformat(str(persona_ts)).astimezone().strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                _p_local = str(persona_ts)[:10]
        daily = db.get_latest_daily_review(user_id)
        if daily and (not _p_local or str(daily.get("date", "")) >= _p_local):
            parts.append(f"昨日复盘：{daily['content']}")
            # 2026-09-08 批次C：未完成话题悬挂——昨天聊到一半的事（感知事实，接不接由 agent 自决）
            _pending = str(daily.get("pending") or "").strip()
            if _pending:
                parts.append(f"（你们聊到一半的：{_pending}——接不接你定。）")
        weekly = db.get_latest_weekly_review(user_id)
        if weekly and (not _p_local or str(weekly.get("week_start", "")) >= _p_local):
            parts.append(f"本周复盘：{weekly['content']}")
            # 2026-09-08 批次C：周度生活线——你这周的日子（不是对话；她是连续的）
            _life_w = str(weekly.get("life") or "").strip()
            if _life_w:
                parts.append(f"（你这一周的生活：{_life_w}）")
        # 事件记忆召回：按最近消息语义向量 Top-K（C15 注释勘误：embedding 不可用=**不注入事件**、
        # 无回退——_recall_events 返回 None，本函数不追加任何事件块；并非"取最近 N 条"）
        # 2026-09-09 深夜：并行化（结果互不依赖）——此处仅取并发任务结果（任务已在函数顶部启动，见彼处注释）
        recalled_events = await _evt_task if _evt_task is not None else None
        if recalled_events:
            parts.append("过去相关事件：" + "；".join(recalled_events))
    # 矫正规则（L4 动态约束：主人对话中教过的说话方式，持久化生效）
    try:
        from .. import correction

        ctext = correction.get_text(user_id)
        if ctext:
            parts.append(ctext)
    except Exception:  # noqa: BLE001
        pass
    if group_safe:
        # 2026-09-08 活人感#3：群聊距离事实——收敛不再只是"删除"，给 agent 一条表达层的分寸依据
        # （2026-09-08 另：group_safe 已接到所有群聊回复，此前仅私语模式生效、普通群聊漏防）
        parts.append(
            "【群聊场合】此刻是群聊：你掌握的关于TA的信息只有大家公开互动能看到的部分"
            "（私聊里的事，你在群里不会提）；亲疏与分寸，按你们公开的关系自己把握。"
        )
    return "\n".join(parts)


# 2026-09-07 P3：legacy 状态期回顾层整段删除（_mark_state_review/_pop_state_review/
# _state_review_text + state_review.json）——唯一写方随旧催眠指数机删除，文件永不被写，
# 该链为每轮 build_context 的虚空调用。同批删除 legacy 四状态文件读侧
# （hypno/brainwash/item/lust_state.json 的 _load_* 与 _active_state_note 四态探测——
# 写侧已死，恒空跑）；状态期演出排除改由 EXTRACT_PROMPT 提示层 + core/special 承担。


# 2026-09-12 I 项：改 data_path——本文件是**状态**（群动作开关，atomics 落盘），
# 原先写在 `qq-bot/data/`（那是随仓**内容**根：memes/scenes/风格样本在那边）。
# 状态进内容根的后果：内容根不被发行覆盖 → 换机后开关静默重置；且同仓两个 data 根看着像 bug。
GROUP_ACTION_FILE = data_path("group_action_state.json")


def _load_group_action() -> dict:
    try:
        return json.loads(GROUP_ACTION_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_group_action(state: dict):
    GROUP_ACTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomics.write_json_atomic(GROUP_ACTION_FILE, state)


def set_group_action_off(group_id: str, off: bool) -> bool:
    """关闭/开启群聊动作回复。返回新状态（True=动作关闭）。"""
    state = _load_group_action()
    state[group_id] = off
    _save_group_action(state)
    return off


def group_action_disabled(group_id: str) -> bool:
    """该群是否关闭了 *动作* 式回复。"""
    return bool(_load_group_action().get(group_id, False))


def history_messages(user_id: str, max_turns: int = 12, group_id: str | None = None, since_ts: str | None = None):
    """最近对话（含当前消息），用于拼接 LLM 上下文。
    group_id 非空时只取该群消息（群聊上下文隔离）。
    since_ts：人设切换时间（UTC ISO）——切换前的对话不再注入（防旧人设 RP 内容污染新人设）。"""
    rows = db.recent_messages(user_id, limit=max_turns * 2, group_id=group_id)
    if since_ts:
        rows = [m for m in rows if (m.get("ts") or "") >= since_ts]
    return rows


EXTRACT_PROMPT = """你是记忆管理模块。根据下面的对话和【已有记忆】，决定如何更新关于**用户（user 角色）**的长期记忆。
已有记忆（key: value）：
{existing}

对话：
{text}

输出严格 JSON（无其他文字或代码块）：
{"add":[{"key":"简短字段名","value":"值","confidence":0.0到1.0}],
 "update":[{"key":"已有记忆中的key","value":"修正后的新值"}],
 "delete":["要删除的已有key"],
 "events":[{"content":"具体发生过的事件(≤40字)","confidence":0.0到1.0}],
 "mood":"机器人此刻应呈现的情绪(中文2-4字)",
 "intimacy_delta":-1.0到1.0,
 "summary":"重要进展摘要(≤50字)，无则空字符串",
 "open_thread":"对话里没聊完/被岔开的话题的一句原话摘要(≤40字)，无则空字符串"}

规则：
- facts 记录关于用户（对方）的客观信息与**稳定互动偏好**：姓名、职业、喜好、宠物、经历、生活习惯，以及**角色扮演中反复或显著出现的偏好元素**（如"喜欢粗暴撕衣开场""偏好雷电主题""爱用（括号）动作调戏你""喜欢羞辱向互动"）——角色扮演是对方意志的投射，稳定的偏好元素可以提取
- events 记录**具体发生过的一次性事件**（有明确时间/场景/情节的"事"，如"上周一起聊了养猫的事""昨天用户去了上海出差"）；泛化的常态信息（"用户喜欢猫"）归 facts，不要放 events；
- open_thread：仅当对话里确实出现了没聊完/被岔开的话题（说到一半被打断、对方明确说以后再讲、明显悬而未决）时，写一句**原话摘要**；没有或拿不准就写空字符串；已经聊完的事和例行寒暄不算
- **里程碑事件**（第一次共同经历/郑重承诺/重要情感节点/影响关系的大事）content 前加【重要】——这类数日后值得再被想起
- 互动偏好"显著出现一次"或"重复出现两次"即可记录，confidence 给 0.6-0.8；客观信息（职业/地名等）出现一次即可记
- 【防幻觉】只写入对话中明确出现的信息：用户没说过/对话未提及的一律不写；不确定或只是推测的信息不要 add
- 与已有记忆冲突时用 update/delete 修正（例如用户说"不养猫了"→ update 或 delete 宠物类记忆）
- 删除旧事实时，如果对话中出现了替代的新信息，必须同时 add 新事实（例：送走猫改养狗 → delete 旧宠物，同时 add 新宠物的名称/种类）
- 已有记忆中已存在的相同事实不要重复 add
- 不要编造；facts 里不要放情绪、客套话、单次即兴的 RP 情节
- **【AI 身份中立】**：描述"用户与 AI 的互动"时，AI 一律用「我/对方 AI」表达，**不写任何角色名/人设名**（如"用户与阿米娅高度亲密"→"用户与对方 AI 存在高度亲密关系"；"和阿米娅聊了养猫"→"和我聊了养猫"）——同世界观换人设后记忆共享，具体角色名会让新角色串场（2026-09-08）
- 【状态期演出排除】若对话中出现"催眠射线/催眠/洗脑/使用了道具（口球/淫纹/震动棒）/情欲状态"等临时状态情节，这些情节中的言行属于**临时状态下的演出，不是用户的稳定偏好**——禁止提炼为 facts（无论出现多少次）；如需记录，仅放 events 并注明"（状态期演出）"，confidence 不超过 0.6"""


def _existing_facts_text(user_id: str, limit: int = 30) -> str:
    facts = db.get_facts(user_id, limit=limit)
    if not facts:
        return "（无）"
    return "；".join(f"{f['key']}: {f['value']}" for f in facts)


def _is_owner(user_id: str) -> bool:
    """是否主人（SUPERUSERS）：非主人不建立长时记忆（双保险，防误调用）。"""
    from nonebot import get_driver

    su = set(getattr(get_driver().config, "superusers", set()) or set())
    return user_id in su


def _open_thread_of(data) -> str:
    """P-1 话题悬挂：从提取 JSON 容错取可选字段 open_thread。
    字段缺失/类型错/非 dict/空串一律返回 ""（调用方忽略），不阻塞主提取流程。"""
    if not isinstance(data, dict):
        return ""
    v = data.get("open_thread")
    return str(v).strip() if isinstance(v, str) else ""


# ---- 2026-09-08 C-4 梗库自生长（供给侧）：群聊提取的可选字段 group_meme → 落 memes.json ----
# 读侧消费链：brain._meme_note → perception.meme_note（群聊玩梗感知）。
# 文件对齐真实存量 memes.json（qq-bot/data/，79 条；brain._MEMES_PATH 同批对齐此处），
# 格式复用现有 {梗: {"meaning": 解释, "take": 接法}}；本项目纪律：机器只记账不判内容。
MEMES_PATH = Path(__file__).resolve().parents[2] / "data" / "memes.json"
MEME_CAP = 200      # 容量上限：超出丢最旧（dict 插入序=最旧在前，直接弹首条）
MEME_MAX_LEN = 30   # 梗本体长度上限（读侧按子串命中，过长必是坏提取）

# 提取 prompt 群聊附加段（仅群聊上下文拼接——私聊提取不问梗：省 token，也防私聊私货进梗库）
GROUP_MEME_PROMPT = (
    "\n\n【群聊梗库】本次对话来自群聊。若群里出现了值得记的**新梗/新句式**（往届梗库里没有的），"
    "在 JSON 顶层追加可选字段 \"group_meme\"：写「梗——一句解释」（梗=原句或短句式，"
    "解释=它在群里的意思/怎么接，≤40 字）；没有或拿不准就写空字符串。宁缺毋滥。"
)


def _group_meme_of(data) -> str:
    """C-4：从提取 JSON 容错取可选字段 group_meme（口径同 _open_thread_of：
    非 dict/字段缺失/类型错/空串一律 ""，调用方忽略，不阻塞主提取流程）。"""
    if not isinstance(data, dict):
        return ""
    v = data.get("group_meme")
    return str(v).strip() if isinstance(v, str) else ""


def store_group_meme(raw: str) -> bool:
    """C-4 供给侧落库：「梗——一句解释」→ memes.json（复用现有格式）。
    查重（梗名已存在 / 解释前 20 字与已有条目相同 → 跳过）；超 MEME_CAP 丢最旧；
    原子写（atomics）；损坏文件拒绝覆写（fail-closed，防清掉存量梗）；任何失败返回 False 不阻塞。"""
    try:
        raw = str(raw or "").strip()
        if not raw:
            return False
        meme, _, expl = raw.partition("——")
        meme, expl = meme.strip(), expl.strip()
        if not meme or not expl or len(meme) > MEME_MAX_LEN:
            return False
        try:
            data = json.loads(MEMES_PATH.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            data = {}
        except (OSError, json.JSONDecodeError):
            logger.warning("memes.json unreadable, group meme store skipped: %s", MEMES_PATH)
            return False
        if not isinstance(data, dict):
            return False
        if meme in data:
            return False
        head = expl[:20]
        if any(str((v or {}).get("meaning", "")).strip()[:20] == head for v in data.values()):
            return False
        data[meme] = {"meaning": expl, "take": ""}
        while len(data) > MEME_CAP:
            data.pop(next(iter(data)))
        return atomics.write_json_atomic(MEMES_PATH, data)
    except Exception as e:  # noqa: BLE001
        logger.debug("group meme store skip: %s", e)
        return False


async def extract_and_store(user_id: str, recent: list[dict], sync_mood: bool = False, group_id: str = ""):
    """sync_mood=True（brain 传 owner/白名单判定）：把记忆提取判定的对话心情同步回生活心情
    （life_state）——2026-09-07 修复：挑逗等互动心情曾被判定进 relation.mood 却从不回写
    life_state，life_text 展示的仍是旧「懒散」僵值。
    group_id 非空（群聊上下文，2026-09-08 C-4）：提取 prompt 追加梗库指令，提取到的
    group_meme 落 memes.json（私聊不写梗库）。"""
    """后台任务：Mem0 风格记忆更新（add/update/delete + 情绪/关系/摘要）。失败不影响主流程。

    仅主人（SUPERUSERS）进入长时记忆；非用户直接忽略。
    """
    owner = _is_owner(user_id)
    try:
        text = "\n".join(f"{m['role']}: {m['content']}" for m in recent)
        # 状态感知注记（2026-09-07 改接 core/special 唯一状态源——旧四文件读侧已死删）：
        # bot 全局状态激活时防止状态期演出被提炼为长期偏好
        try:
            from core import special as _spn

            if _spn.active():
                text = ("【状态注记】注意：以下对话期间临时状态可能激活。"
                        "状态期间的言行属于临时演出，不是用户的稳定偏好。\n" + text)
        except Exception:  # noqa: BLE001
            pass
        prompt = EXTRACT_PROMPT.replace("{existing}", _existing_facts_text(user_id)).replace("{text}", text)
        if group_id:  # C-4：仅群聊上下文追加梗库提取指令（私聊不问梗）
            prompt += GROUP_MEME_PROMPT
        resp = await _client().chat.completions.create(
            model="memory-extract",
            messages=[
                {"role": "system", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=1500,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},  # 提取无需思考
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
        data = json.loads(raw)

        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        n_add = n_update = n_delete = n_events = 0
        from .embeddings import dedupe_similar, embed_text_async  # 2026-09-07 P1：异步 embed（同步版曾阻塞事件循环）

        # ---- 多用户分级：仅主人做深度提取（facts/关系/摘要）；非主人只写事件（轻量） ----
        if owner:
            emb_items = db.get_fact_embeddings(user_id)
            for f in data.get("add", []):
                key = str(f.get("key", "")).strip()
                value = str(f.get("value", "")).strip()
                conf = float(f.get("confidence", 0.8))
                # 防幻觉：低置信度事实不写入（互动偏好类放宽到 0.5，客观信息仍 0.6+）
                if not (key and value and conf >= 0.5):
                    continue
                # 2026-09-08 P1：无条件尝试落向量——曾按"向量表是否已有内容"条件跳过，
                # 最早的一批事实永不入向量库，日后语义召回同样选不中它们
                new_emb = await embed_text_async(f"{key}：{value}")
                if new_emb:
                    # 语义查重：相似 ≥ 阈值 → 合并到已有事实（更新而非新增）
                    dup_key = dedupe_similar(new_emb, emb_items, threshold=FACT_DEDUPE_THRESHOLD)
                    if dup_key and dup_key != key:
                        # 2026-09-08 活人感 3-1 对话级认错：语义查重命中旧 fact（更新而非新增）——
                        # 旧值存在且与新值不同 = 提取管线修正了一条旧事实，落一条修正记录
                        # （消费端=brain 私聊感知"自然认个错"）；失败不阻塞主提取
                        _old_dup = db.get_fact(user_id, dup_key)
                        if _old_dup and str(_old_dup.get("value") or "").strip() and str(_old_dup["value"]).strip() != value:
                            try:
                                db.add_fact_correction(user_id, str(_old_dup["value"]).strip()[:40], value[:40], ts)
                            except Exception as _fce:  # noqa: BLE001
                                logger.debug("fact correction save skip: %s", _fce)
                        db.upsert_fact(user_id, dup_key, value, max(conf, 0.9), ts)
                        n_update += 1
                        continue
                # 收口审计 P2：同键 add 且值不同 = 覆写旧事实——同样落修正（曾漏此路径，
                # "职业：老师→医生"类同键改写永远不认错）；无旧值/值相同则静默正常落
                _old_same = db.get_fact(user_id, key)
                if _old_same and str(_old_same.get("value") or "").strip() and str(_old_same["value"]).strip() != value:
                    try:
                        db.add_fact_correction(user_id, str(_old_same["value"]).strip()[:40], value[:40], ts)
                    except Exception as _fce:  # noqa: BLE001
                        logger.debug("fact correction save skip: %s", _fce)
                db.upsert_fact(user_id, key, value, max(conf, 0.9), ts)
                if new_emb:
                    db.save_fact_embedding(user_id, key, new_emb)
                    emb_items.append((key, new_emb))
                n_add += 1
            for f in data.get("update", []):
                key = str(f.get("key", "")).strip()
                value = str(f.get("value", "")).strip()
                if key and value:
                    # 2026-09-08 活人感 3-1 对话级认错：EXTRACT_PROMPT 的 update 规则命中旧 fact——
                    # 旧值存在且与新值不同 = 她之前记错了，落一条修正记录（无旧值=等价新增，不算认错）
                    _old_fc = db.get_fact(user_id, key)
                    if _old_fc and str(_old_fc.get("value") or "").strip() and str(_old_fc["value"]).strip() != value:
                        try:
                            db.add_fact_correction(user_id, str(_old_fc["value"]).strip()[:40], value[:40], ts)
                        except Exception as _fce:  # noqa: BLE001
                            logger.debug("fact correction save skip: %s", _fce)
                    db.upsert_fact(user_id, key, value, 0.9, ts)
                    # 刷新 embedding（值变了向量要跟着变）
                    upd_emb = await embed_text_async(f"{key}：{value}")
                    if upd_emb:
                        db.save_fact_embedding(user_id, key, upd_emb)
                    n_update += 1
            for key in data.get("delete", []):
                key = str(key).strip()
                if key:
                    db.delete_fact(user_id, key)
                    n_delete += 1
            mood = str(data.get("mood", "")).strip()
            delta = float(data.get("intimacy_delta", 0.0))
            summary = str(data.get("summary", "")).strip()
            if mood or delta:
                db.update_relation(user_id, delta=delta, mood=mood or None, ts=ts)
            # 2026-09-07：对话后心情回写生活状态（仅 owner/白名单；agent 自标【心情】仍优先——apply 以最新为准）
            if mood and sync_mood:
                try:
                    from core import mood as _mood

                    _applied = _mood.apply(mood, reason="对话记忆提取", source="memory:extract")
                    if _applied:
                        logger.info("life mood synced from extract: user={} mood={}", user_id, _applied)
                except Exception as _me:  # noqa: BLE001
                    logger.debug("life mood sync skip: {}", _me)
            if summary:
                db.add_summary(user_id, ts, summary)
        elif sync_mood:
            # 2026-09-07 P2⑨：白名单的心情回写曾因整块包在 if owner: 内而永不可达（sync_mood 死参数）——
            # 白名单对话的提取心情同样回写生活状态（brain 门控 sync_mood=owner/白名单 60 门槛）
            _m_wl = str(data.get("mood", "") or "").strip()
            if _m_wl:
                try:
                    from core import mood as _mood

                    _applied = _mood.apply(_m_wl, reason="对话记忆提取", source="memory:extract")
                    if _applied:
                        logger.info("life mood synced from extract (wl): user={} mood={}", user_id, _applied)
                except Exception as _me:  # noqa: BLE001
                    logger.debug("life mood sync skip: {}", _me)

        # ---- 事件记忆：所有用户都写（一次性事件，confidence ≥ 0.7），语义去重 ----
        ev_items = db.get_event_embeddings(user_id)
        for e in data.get("events", []):
            content = str(e.get("content", "")).strip()
            conf = float(e.get("confidence", 0.6))
            if not (content and conf >= 0.7):
                continue
            # 2026-09-09 死锁修复（调研发现 event_embeddings=0 实锤）：嵌入曾只在"已有嵌入"时
            # 才计算——表空则 ev_items 恒空 → 永不计算 → 永远空（鸡生蛋）。现恒计算，
            # 去重仅在已有嵌入可比对时进行（首条事件无条件入库+嵌入）。
            new_emb = await embed_text_async(content)
            dup = (
                dedupe_similar(new_emb, [(c, emb) for _, _, c, emb in ev_items], threshold=FACT_DEDUPE_THRESHOLD)
                if (new_emb and ev_items)
                else None
            )
            if dup:
                continue  # 相似事件已存在，跳过
            event_id = db.add_event(user_id, content, ts)
            if new_emb:
                db.save_event_embedding(event_id, user_id, new_emb)
                ev_items.append((event_id, ts, content, new_emb))
            n_events += 1
        # ---- 2026-09-08 P-1 话题悬挂：可选字段 open_thread（没聊完/被岔开的话题一句原话摘要）----
        # B3（2026-09-09 审计）：仅私聊上下文落库（group_id 门控，与 group_meme 对偶）——
        # 消费端是私聊感知"上次你们聊到一半"；群聊提取放开后，群聊摘要原样落库=私聊注入面泄漏私聊外内容。
        # 解析容错见 _open_thread_of（字段缺失/类型错/空串一律忽略）；截 60 字防感知注入超长；失败不阻塞主提取
        try:
            _ot = _open_thread_of(data) if not group_id else ""
            if _ot:
                db.add_open_thread(user_id, _ot[:60], ts)
        except Exception as _ote:  # noqa: BLE001
            logger.debug("open_thread save skip: %s", _ote)
        # ---- 2026-09-08 C-4 梗库自生长：仅群聊上下文落库（私聊不写梗库，group_id 门控）----
        # 现状注记：brain 现行"仅私聊提取"（群聊不走 extract_and_store，2026-08-30 裁决），本分支
        # 当前不会触发；按裁决先行落好供给侧，群聊提取开启即自动生效。失败不阻塞主提取。
        if group_id:
            _gm = _group_meme_of(data)
            if _gm:
                store_group_meme(_gm)
        logger.info(
            "memory update ok: owner=%s add=%d update=%d delete=%d events=%d mood=%s delta=%s summary=%s",
            owner, n_add, n_update, n_delete, n_events, data.get("mood", ""), data.get("intimacy_delta", 0.0),
            bool(data.get("summary", "")),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("memory extract failed: %s", e)
