"""SQLite 存储层：对话日志 / 事实库 / 关系状态 / 滚动摘要。

数据文件: E:\\robot\\data\\memory.db（gitignored，长期积累）。
"""
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from core.paths import DATA_ROOT

# 2026-09-08 活人感 3-2：库路径可用环境变量 MEMORY_DB_PATH 覆盖（test_circadian.py 注入 tmp 库用
# ——测试 import 本模块时模块级单例不再打开生产 data/memory.db）；未设置时与原硬编码路径逐字节一致。
_DB_PATH = Path(
    os.environ.get("MEMORY_DB_PATH")
    or (DATA_ROOT / "memory.db")
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- 作息感知（2026-09-08 活人感 3-2）：TA 的活跃时段 = 感知事实，非机器时窗 ----------
# 口径（用户裁决）：只把「TA 通常什么时候活跃」归纳成事实注入 perceive，现在合不合适找TA
# 由 agent 自决——本模块禁止输出任何"该/不该现在找"的判定。
ACTIVE_HOURS_TTL = 3600.0  # 直方图缓存 TTL（秒，1 小时）：每次 perceive 都重算直方图纯属浪费
_ACTIVE_HOURS_CACHE: dict = {}  # (db_path, user_id, days) -> (time.monotonic(), hist)；key 带库路径防多实例（测试 tmp 库）串数据


def active_hours_cache_clear():
    """清空作息直方图缓存（测试用/手动强制重算）。"""
    _ACTIVE_HOURS_CACHE.clear()


def summarize_active_hours(hist: dict, max_segments: int = 3, min_total: int = 10) -> str:
    """小时直方图 → 人读时段事实串（纯函数，如「22-24点和12-14点」）。

    算法：≥峰值 1/3 的小时算"活跃小时"；按消息量降序取种子、向后（含跨午夜 23→0）吸收相邻
    活跃小时成段（段最长 3 小时）；最多 max_segments 段，按权重降序输出。
    样本总数 < min_total 返回 ""（数据不足不归纳，宁缺毋滥——调用方据此跳过注入）。
    只做统计归纳，不做任何"现在适不适合找TA"的机器判定。"""
    counts: dict = {}
    for h, c in (hist or {}).items():
        try:
            hi, ci = int(h), int(c)
        except (TypeError, ValueError):
            continue
        if 0 <= hi <= 23 and ci > 0:
            counts[hi] = counts.get(hi, 0) + ci
    total = sum(counts.values())
    if total < min_total or not counts:
        return ""
    peak = max(counts.values())
    strong = {h for h, c in counts.items() if c * 3 >= peak}  # 噪音过滤：不足峰值 1/3 的小时不入段
    used: set = set()
    segs = []  # (start_hour, span, weight)
    for h, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        if h not in strong or h in used or len(segs) >= max_segments:
            continue
        used.add(h)
        span = 1
        nxt = (h + 1) % 24
        while nxt in strong and nxt not in used:
            used.add(nxt)  # 相邻活跃小时一律并入本段标记（展示另截）——否则段长上限会把
            span += 1      # 连续活跃带切碎成「22-1点、1-2点」这类怪串
            nxt = (nxt + 1) % 24
        span = min(span, 3)  # 展示上限：段最长 3 小时（极端"全天活跃"也不输出长串）
        segs.append((h, span, c))
    if not segs:
        return ""
    def _fmt(h: int, span: int) -> str:
        end = h + span
        return f"{h}-{end}点" if end <= 24 else f"{h}-{end % 24}点"
    parts = [_fmt(h, span) for h, span, _ in segs]  # segs 已按种子权重降序
    if len(parts) == 1:
        return parts[0]
    return "、".join(parts[:-1]) + "和" + parts[-1]


class MemoryDB:
    def __init__(self, path=None):
        self._path = str(path or _DB_PATH)
        self._path and Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    group_id TEXT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS facts(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    confidence REAL DEFAULT 0.8,
                    created_ts TEXT NOT NULL,
                    last_seen_ts TEXT NOT NULL,
                    UNIQUE(user_id, key)
                );
                CREATE TABLE IF NOT EXISTS relation(
                    user_id TEXT PRIMARY KEY,
                    intimacy REAL DEFAULT 0.0,
                    mood TEXT DEFAULT '平静',
                    updated_ts TEXT NOT NULL,
                    peak REAL DEFAULT 0.0
                );
                CREATE TABLE IF NOT EXISTS summaries(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    content TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS emotions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    emotion TEXT NOT NULL,
                    intensity REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fact_embeddings(
                    user_id TEXT NOT NULL,
                    fact_key TEXT NOT NULL,
                    emb TEXT NOT NULL,
                    ts TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(user_id, fact_key)
                );
                CREATE TABLE IF NOT EXISTS events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    content TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS event_embeddings(
                    event_id INTEGER PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    emb TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS daily_reviews(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    content TEXT NOT NULL,
                    pending TEXT DEFAULT '',
                    UNIQUE(user_id, date)
                );
                CREATE TABLE IF NOT EXISTS weekly_reviews(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    week_start TEXT NOT NULL,
                    content TEXT NOT NULL,
                    life TEXT DEFAULT '',
                    UNIQUE(user_id, week_start)
                );
                CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, id);
                CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id, id);
                CREATE INDEX IF NOT EXISTS idx_emotions_user_ts ON emotions(user_id, ts);
                """
            )
            # 兼容旧库：补 sender_name 列（说话人昵称，用于区分群聊身份）
            cols = [r[1] for r in self._conn.execute("PRAGMA table_info(messages)")]
            if "sender_name" not in cols:
                self._conn.execute("ALTER TABLE messages ADD COLUMN sender_name TEXT")
            # 2026-09-08 批次C：复盘表补列（旧库迁移，幂等）——日复盘 pending（未完成话题）/ 周复盘 life（周生活线）
            # 盲审修正：ALTER 包 try（库只读/锁时降级跳过，不阻断 import——列缺失时功能自动回退）
            try:
                _dc = [r[1] for r in self._conn.execute("PRAGMA table_info(daily_reviews)")]
                if "pending" not in _dc:
                    self._conn.execute("ALTER TABLE daily_reviews ADD COLUMN pending TEXT DEFAULT ''")
                _wc = [r[1] for r in self._conn.execute("PRAGMA table_info(weekly_reviews)")]
                if "life" not in _wc:
                    self._conn.execute("ALTER TABLE weekly_reviews ADD COLUMN life TEXT DEFAULT ''")
            except sqlite3.Error:
                pass  # 承上（176 行）：库只读/被锁 → 降级跳过，不阻断 import；列缺失时该功能自动回退
            # 2026-09-05 滞回峰值列（旧库迁移；幂等）：记录亲密度历史峰值（开启亲密待遇后不因轻度波动立即收回）
            _rc = [r[1] for r in self._conn.execute("PRAGMA table_info(relation)")]
            if "peak" not in _rc:
                self._conn.execute("ALTER TABLE relation ADD COLUMN peak REAL DEFAULT 0.0")
            # 2026-09-04 性能：群消息按 (group_id, id) 查询经常发生（每群最近 N 条）——建立覆盖索引
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_group ON messages(group_id, id DESC)"
            )
            # 2026-09-05 群友轻量互动画像（纯规则：频次/点名/被回应/话题词袋，不进 facts 防污染）
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS group_user_meta(
                     group_id TEXT, user_id TEXT,
                     msg_count INTEGER DEFAULT 0,
                     mention_count INTEGER DEFAULT 0,
                     replied_count INTEGER DEFAULT 0,
                     topics TEXT DEFAULT '{}',
                     last_ts TEXT, first_ts TEXT,
                     PRIMARY KEY (group_id, user_id)
                   )"""
            )
            # 2026-09-05 群话题知识（自决搜索→提炼→入库；同话题命中免搜）
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS topic_knowledge(
                     topic TEXT PRIMARY KEY,
                     group_id TEXT DEFAULT '',
                     summary TEXT NOT NULL,
                     bg TEXT DEFAULT '',
                     ts TEXT NOT NULL
                   )"""
            )
            # 2026-09-07 群画风画像（LLM 定性，感知注入用）
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS group_style(
                     group_id TEXT PRIMARY KEY,
                     style TEXT NOT NULL,
                     updated_ts TEXT NOT NULL
                   )"""
            )
            # 2026-09-08 P-1 话题悬挂：没聊完/被岔开的话题（extract 可选字段 open_thread 落库；
            # 消费=私聊感知"上次你们聊到一半"；7 天未完成自然淡出——置 done=1 过滤，不删数据。
            # CREATE TABLE IF NOT EXISTS：旧库首次打开自动建表，不迁移不动既有数据）
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS open_threads(
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     user_id TEXT NOT NULL,
                     ts TEXT NOT NULL,
                     content TEXT NOT NULL,
                     done INTEGER DEFAULT 0
                   )"""
            )
            # 2026-09-08 活人感 3-1 对话级认错：「最近一次事实修正」——提取管线修正了某条旧事实时
            # 落一条记录（供给侧=extract_and_store 的更新旧 fact 分支；消费端=brain 私聊感知，
            # 取走即置 consumed：只注一次，防每轮复读）。每用户只留最近一条（新入账清旧行）。
            # CREATE TABLE IF NOT EXISTS：旧库首次打开自动建表，不迁移不动既有数据。
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS fact_corrections(
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     user_id TEXT NOT NULL,
                     old_text TEXT NOT NULL,
                     new_text TEXT NOT NULL,
                     ts TEXT NOT NULL,
                     consumed INTEGER DEFAULT 0
                   )"""
            )
            self._conn.commit()

    # ---------- messages ----------
    def add_message(self, user_id: str, group_id: str | None, role: str, content: str, sender_name: str | None = None):
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages(ts, user_id, group_id, role, content, sender_name) VALUES(?,?,?,?,?,?)",
                (_now(), user_id, group_id, role, content, sender_name),
            )
            self._conn.commit()

    def recent_messages(self, user_id: str, limit: int = 24, group_id: str | None = None):
        """最近消息；group_id 三态：None=全部（含群聊，旧行为）；""="仅私聊"（群聊记忆不混入）；
        字符串=仅该群全员（2026-08-28：群聊可接上前面的讨论）。"""
        with self._lock:
            if group_id == "":
                rows = self._conn.execute(
                    "SELECT role, content, sender_name, ts FROM messages WHERE user_id=? AND (group_id IS NULL OR group_id='') "
                    "ORDER BY id DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
            elif group_id:
                rows = self._conn.execute(
                    "SELECT role, content, sender_name, ts FROM messages WHERE group_id=? ORDER BY id DESC LIMIT ?",
                    (group_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT role, content, sender_name, ts FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
        return [
            {"role": r["role"], "content": r["content"], "sender_name": r["sender_name"], "ts": r["ts"]}
            for r in reversed(rows)
        ]

    def messages_between(self, user_id: str, since_ts: str, until_ts: str, limit: int = 30):
        """ts 区间内（含边界）该用户私聊 user+assistant 消息，按 ts 升序最多 limit 条。
        2026-09-09 跨卡场景交接用：切换边界→本次换卡之间的对话样本（旧卡本次会话内容）。
        群聊行不混入（group_id 空过滤，同 recent_messages 的 "" 模式）；库存 UTC ISO，
        同格式字典序=时间序，区间比较直接走 SQL 字符串序（与 get_day_messages_text 同口径）。
        2026-09-09 盲审 P2：LIMIT 必须配 DESC（取**最近** limit 条）再反序还原升序——
        曾 ASC+LIMIT 取到窗口内最早段，会话超限时摘要锚定到过期对话。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content, sender_name, ts FROM messages "
                "WHERE user_id=? AND (group_id IS NULL OR group_id='') AND ts>=? AND ts<=? "
                "ORDER BY ts DESC, id DESC LIMIT ?",
                (str(user_id), str(since_ts), str(until_ts), int(limit)),
            ).fetchall()
        rows = list(reversed(rows))
        return [
            {"role": r["role"], "content": r["content"], "sender_name": r["sender_name"], "ts": r["ts"]}
            for r in rows
        ]

    def recent_messages_all(self, limit: int = 24):
        """全库最近消息（跨用户，含群聊）。2026-09-07 P2 新增：/评测 等统计需要跨用户样本，
        曾误用 recent_messages("")（user_id="" 匹配不到任何行 → 恒空，功能整体死亡）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content, sender_name, ts FROM messages ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"role": r["role"], "content": r["content"], "sender_name": r["sender_name"], "ts": r["ts"]}
            for r in reversed(rows)
        ]

    def recent_group_digest(self, user_id: str, limit: int = 4, max_age_s: int = 900):
        """私聊用：最近活跃群（15 分钟内）的讨论速览，最多 limit 条；无近期群聊返回 []。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT group_id, ts FROM messages WHERE user_id=? AND group_id IS NOT NULL AND group_id<>'' "
                "ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            if not row:
                return []
            try:
                from datetime import datetime as _dt, timezone as _tz

                age = (_dt.now(_tz).astimezone() - _dt.fromisoformat(row["ts"]).astimezone()).total_seconds()
                if age > max_age_s:
                    return []
            except Exception:  # noqa: BLE001
                pass
            gid = row["group_id"]
            rows = self._conn.execute(
                "SELECT role, content, sender_name FROM messages WHERE group_id=? ORDER BY id DESC LIMIT ?",
                (gid, limit),
            ).fetchall()
        out = []
        for r in reversed(rows):
            who = r["sender_name"] or ("我" if r["role"] == "user" else "她")
            out.append(f"{who}：{str(r['content'])[:40]}")
        return out

    def count_user_messages(self, user_id: str, role: str | None = None) -> int:
        """用户消息计数（用于触发周期任务，重启安全）。"""
        with self._lock:
            if role:
                return self._conn.execute(
                    "SELECT COUNT(*) c FROM messages WHERE user_id=? AND role=?", (user_id, role)
                ).fetchone()[0]
            return self._conn.execute(
                "SELECT COUNT(*) c FROM messages WHERE user_id=?", (user_id,)
            ).fetchone()[0]

    def get_active_hours(self, user_id: str, days: int = 14, use_cache: bool = True) -> dict:
        """近 days 天该用户 user 角色消息的本地小时直方图 {小时: 次数}（作息感知用，仅非零小时）。

        SQL 只取 ts（user_id 前缀走 idx_messages_user(user_id, id)），窗口用 ts 字符串序比较
        （库存 UTC ISO，同格式字典序=时间序）；本地时区换算在 Python 侧（SQL 方言做时区转换
        不可移植），无时区的旧 ts 按库存约定视作 UTC。解析失败的 ts 跳过不计数。
        带 TTL 缓存（_ACTIVE_HOURS_CACHE，key 含库路径/user_id/days）；命中返回副本，调用方
        改动不会污染缓存。use_cache=False 强制重算（测试/调试用）。"""
        key = (self._path, str(user_id), int(days))
        now_mono = time.monotonic()
        if use_cache:
            hit = _ACTIVE_HOURS_CACHE.get(key)
            if hit is not None and now_mono - hit[0] < ACTIVE_HOURS_TTL:
                return dict(hit[1])
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat(timespec="seconds")
        hist = {h: 0 for h in range(24)}
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts FROM messages WHERE user_id=? AND role='user' AND ts>=?",
                (str(user_id), cutoff),
            ).fetchall()
        for r in rows:
            try:
                t = datetime.fromisoformat(str(r["ts"]))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                hist[t.astimezone().hour] += 1
            except (ValueError, TypeError, OverflowError, OSError):
                continue
        hist = {h: c for h, c in hist.items() if c}
        if use_cache:
            _ACTIVE_HOURS_CACHE[key] = (now_mono, hist)
        return dict(hist)

    # ---------- facts ----------
    def get_last_user_message(self, user_id: str) -> str | None:
        """最近一条用户消息（语义召回查询向量用）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT content FROM messages WHERE user_id=? AND role='user' ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        return row["content"] if row else None

    def get_day_messages_text(self, user_id: str, date: str) -> str:
        """某日全部对话文本（复盘用）。date 为本地日期 YYYY-MM-DD；消息 ts 以 UTC ISO 存储，
        故先换算本地当日 [00:00, 24:00) 对应的 UTC 区间再过滤。
        （原实现按 `{date}%` 前缀 LIKE，UTC+8 下会把本地 00:00-08:00 的消息错分到前一天。）"""
        try:
            d0 = datetime.fromisoformat(date).astimezone()  # naive → 本地时区当日零点
            start = d0.astimezone(timezone.utc).isoformat()
            end = (d0.astimezone(timezone.utc) + timedelta(days=1)).isoformat()
        except ValueError:
            start, end = f"{date}T00:00:00+00:00", f"{date}T23:59:59+00:00"
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content FROM messages WHERE user_id=? AND ts>=? AND ts<? ORDER BY id",
                (user_id, start, end),
            ).fetchall()
        return "\n".join(f"{r['role']}: {r['content'][:300]}" for r in rows[:200])

    def last_user_message_ts(self, user_id: str, group_id: str = "") -> str | None:
        """该用户（可选限本群）最近一条 user 消息的 ts 字符串；无记录返回 None。
        （2026-09-06：替代外部绕 _conn 直查 SQL 的旧路径，统一走锁。）"""
        q = (
            "SELECT ts FROM messages WHERE user_id=? AND role='user'"
            + (" AND group_id=?" if group_id else "")
            + " ORDER BY id DESC LIMIT 1"
        )
        args = (user_id, group_id) if group_id else (user_id,)
        with self._lock:
            row = self._conn.execute(q, args).fetchone()
        return row["ts"] if row else None

    def upsert_fact(self, user_id: str, key: str, value: str, confidence: float, ts: str | None = None):
        ts = ts or _now()
        with self._lock:
            self._conn.execute(
                """INSERT INTO facts(user_id, key, value, confidence, created_ts, last_seen_ts)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(user_id, key) DO UPDATE SET
                     value=excluded.value,
                     confidence=excluded.confidence,
                     last_seen_ts=excluded.last_seen_ts""",
                (user_id, key, value, confidence, ts, ts),
            )
            self._conn.commit()

    def get_facts(self, user_id: str, limit: int = 50):
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value, last_seen_ts FROM facts WHERE user_id=? ORDER BY confidence DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [{"key": r["key"], "value": r["value"], "last_seen_ts": r["last_seen_ts"]} for r in rows]


    # ---------- 群友轻量互动画像（2026-09-05：纯规则，零 LLM；不进 facts 防污染） ----------

    def bump_group_user_meta(self, group_id: str, user_id: str, mention: bool = False, content: str = "", replied: bool = False, count_msg: bool = True):
        """群消息统计：发言+1（count_msg=False 时仅记 replied）、点名/被回应计数、话题词袋（2-4 字词频，cap 30）。"""
        import datetime as _dt  # 2026-09-05 修复：原 `import datetime as _dt, timezone as _tz` 会 ModuleNotFoundError（timezone 不是顶层模块）→ @ 路径必炸
        import re as _re
        import json as _json

        if not group_id or not user_id:
            return
        now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            row = self._conn.execute(
                "SELECT msg_count, mention_count, replied_count, topics, first_ts FROM group_user_meta WHERE group_id=? AND user_id=?",
                (group_id, user_id),
            ).fetchone()
            if row:
                raw = _json.loads(row["topics"] or "null")
                # 2026-09-07：topics 列改存「最近真实发言片段」列表（旧格式为 n-gram 词频 dict，读到的旧数据自然过渡丢弃）
                recent = raw if isinstance(raw, list) else []
                first_ts = row["first_ts"] or now
            else:
                recent, first_ts = [], now
            if content:
                _c = _re.sub(r"\s+", "", content)[:14]
                if len(_c) >= 4 and _c not in recent:
                    recent = ([_c] + recent)[:3]  # 最近 3 条原话片段（真实可读，非词频碎片）
            self._conn.execute(
                """INSERT INTO group_user_meta(group_id,user_id,msg_count,mention_count,replied_count,topics,last_ts,first_ts)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(group_id,user_id) DO UPDATE SET
                     msg_count=excluded.msg_count, mention_count=excluded.mention_count,
                     replied_count=excluded.replied_count, topics=excluded.topics,
                     last_ts=excluded.last_ts""",
                (group_id, user_id,
                 (row["msg_count"] if row else 0) + (1 if count_msg else 0),
                 (row["mention_count"] if row else 0) + (1 if mention else 0),
                 (row["replied_count"] if row else 0) + (1 if replied else 0),
                 _json.dumps(recent, ensure_ascii=False), now, first_ts),
            )
            self._conn.commit()

    def group_user_meta_text(self, group_id: str, limit: int = 4, nicks: dict | None = None) -> str:
        """画像摘要（插话感知注入）：互动最多的 N 人——昵称/发言量/点名/最近活跃；无数据返回空串。
        2026-09-07：称呼从"QQ 尾号"改为昵称（nicks 由调用方传 group_members）；移除 n-gram 碎片话题噪音。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT user_id, msg_count, mention_count, replied_count, topics, last_ts FROM group_user_meta "
                "WHERE group_id=? ORDER BY msg_count DESC LIMIT ?",
                (group_id, limit),
            ).fetchall()
        if not rows:
            return ""
        import datetime as _dt
        import json as _json

        out = []
        for r in rows:
            uid = r["user_id"]
            info = (nicks or {}).get(uid) or {}
            nick = ""
            if isinstance(info, dict):
                nick = (info.get("nick") or info.get("card") or "").strip()
            # 2026-09-07：昵称可改，QQ 尾号锚定唯一身份——双标识展示
            who = f"{nick}({uid[-4:]})" if nick else f"…{uid[-4:]}"
            seg = f"「{who}」发言{r['msg_count']}次"
            if r["mention_count"]:
                seg += f"/点名{r['mention_count']}次"
            try:
                raw = _json.loads(r["topics"] or "null")
                if isinstance(raw, list) and raw:
                    seg += "/最近说过：“" + "”“".join(raw[:2]) + "”"
            except Exception:  # noqa: BLE001
                pass
            try:
                _ago_h = int((_dt.datetime.now(_dt.timezone.utc) - _dt.datetime.fromisoformat(str(r["last_ts"]))).total_seconds() // 3600)
                seg += f"/最后活跃{'刚刚' if _ago_h < 1 else f'{_ago_h}小时前'}"
            except Exception:  # noqa: BLE001
                pass
            out.append(seg)
        return "【群成员印象】" + "；".join(out)

    # ---------- 群画风画像（2026-09-07：LLM 定性，感知注入用） ----------
    def get_group_style(self, group_id: str) -> str:
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT style FROM group_style WHERE group_id=?", (group_id,)
                ).fetchone()
                return str(row["style"] or "") if row else ""
            except sqlite3.OperationalError:
                return ""

    def set_group_style(self, group_id: str, style: str):
        with self._lock:
            self._conn.execute(
                """INSERT INTO group_style(group_id, style, updated_ts) VALUES(?,?,?)
                   ON CONFLICT(group_id) DO UPDATE SET style=excluded.style, updated_ts=excluded.updated_ts""",
                (group_id, style, _now()),
            )
            self._conn.commit()

    def group_style_updated_ts(self, group_id: str) -> str:
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT updated_ts FROM group_style WHERE group_id=?", (group_id,)
                ).fetchone()
                return str(row["updated_ts"] or "") if row else ""
            except sqlite3.OperationalError:
                return ""

    def distinct_group_ids(self) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT group_id FROM messages WHERE group_id IS NOT NULL AND group_id!=''"
            ).fetchall()
            return [str(r["group_id"]) for r in rows]

    # ---------- 群话题知识（2026-09-05：自决搜索→提炼→入库；同话题命中免搜） ----------
    def set_topic_knowledge(self, topic: str, summary: str, bg: str = "", group_id: str = ""):
        with self._lock:
            self._conn.execute(
                """INSERT INTO topic_knowledge(topic, group_id, summary, bg, ts) VALUES(?,?,?,?,?)
                   ON CONFLICT(topic) DO UPDATE SET
                     group_id=excluded.group_id, summary=excluded.summary, bg=excluded.bg, ts=excluded.ts""",
                (topic, group_id, summary, bg, _now()),
            )
            self._conn.commit()

    def get_topic_knowledge(self, topic: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT summary, bg, ts FROM topic_knowledge WHERE topic=?", (topic,)
            ).fetchone()
        if not row:
            return None
        return {"summary": row["summary"], "bg": row["bg"], "ts": row["ts"]}

    def delete_fact(self, user_id: str, key: str):
        """删除指定事实（Mem0 风格 delete 操作）。"""
        with self._lock:
            self._conn.execute("DELETE FROM facts WHERE user_id=? AND key=?", (user_id, key))
            self._conn.execute("DELETE FROM fact_embeddings WHERE user_id=? AND fact_key=?", (user_id, key))
            self._conn.commit()

    # ---------- fact embeddings（本地语义检索） ----------
    def save_fact_embedding(self, user_id: str, key: str, emb: list[float]):
        import json as _json

        with self._lock:
            self._conn.execute(
                """INSERT INTO fact_embeddings(user_id, fact_key, emb) VALUES(?,?,?)
                   ON CONFLICT(user_id, fact_key) DO UPDATE SET emb=excluded.emb""",
                (user_id, key, _json.dumps(emb)),
            )
            self._conn.commit()

    def get_fact_embeddings(self, user_id: str) -> list[tuple[str, list[float]]]:
        import json as _json

        with self._lock:
            rows = self._conn.execute(
                "SELECT fact_key, emb FROM fact_embeddings WHERE user_id=?", (user_id,)
            ).fetchall()
        out = []
        for r in rows:
            try:
                out.append((r["fact_key"], _json.loads(r["emb"])))
            except (TypeError, ValueError):
                continue
        return out

    # ---------- events（一次性事件记忆，Mem0 episodic 层） ----------
    EVENT_CAP = 100  # 每用户事件容量（超出删最旧）

    def add_event(self, user_id: str, content: str, ts: str | None = None) -> int:
        import time as _time

        ts = ts or _time.strftime("%Y-%m-%dT%H:%M:%S+00:00", _time.gmtime())
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events(user_id, ts, content) VALUES(?,?,?)", (user_id, ts, content)
            )
            event_id = cur.lastrowid
            # 容量控制：超出 EVENT_CAP 删最旧（连带其 embedding）
            rows = self._conn.execute(
                "SELECT id FROM events WHERE user_id=? ORDER BY id", (user_id,)
            ).fetchall()
            if len(rows) > self.EVENT_CAP:
                for r in rows[: len(rows) - self.EVENT_CAP]:
                    self._conn.execute("DELETE FROM event_embeddings WHERE event_id=?", (r["id"],))
                    self._conn.execute("DELETE FROM events WHERE id=?", (r["id"],))
            self._conn.commit()
            return event_id

    def get_events(self, user_id: str, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, content FROM events WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [{"id": r["id"], "ts": r["ts"], "content": r["content"]} for r in rows]

    def get_event_embeddings(self, user_id: str) -> list[tuple[int, str, list[float]]]:
        """[(event_id, content, emb)]，供语义召回。"""
        import json as _json

        with self._lock:
            rows = self._conn.execute(
                """SELECT e.id, e.ts, e.content, ee.emb FROM events e
                   JOIN event_embeddings ee ON ee.event_id = e.id
                   WHERE e.user_id=? ORDER BY e.id DESC""",
                (user_id,),
            ).fetchall()
        out = []
        for r in rows:
            try:
                out.append((r["id"], r["ts"], r["content"], _json.loads(r["emb"])))
            except (TypeError, ValueError):
                continue
        return out

    def save_event_embedding(self, event_id: int, user_id: str, emb: list[float]):
        import json as _json

        with self._lock:
            self._conn.execute(
                """INSERT INTO event_embeddings(event_id, user_id, emb) VALUES(?,?,?)
                   ON CONFLICT(event_id) DO UPDATE SET emb=excluded.emb""",
                (event_id, user_id, _json.dumps(emb)),
            )
            self._conn.commit()

    def clear_events(self, user_id: str):
        with self._lock:
            self._conn.execute(
                "DELETE FROM event_embeddings WHERE event_id IN (SELECT id FROM events WHERE user_id=?)",
                (user_id,),
            )
            self._conn.execute("DELETE FROM events WHERE user_id=?", (user_id,))
            self._conn.commit()

    # ---------- relation ----------
    # 好感度语义（2026-09-05 重建）：管理员（主人）恒定满值 100；白名单成员 = 积累轨道
    # （互动记账 0-100 真实值、小步升温）；普通用户不写表（public 无关系记录，防泄漏）。
    # 表只记录轨道成员进度 + 所有人的 mood。数值不指挥表演（agent 自决），只做档位与护栏。
    INTIMACY_MAX = 100.0

    @staticmethod
    def _clamp_intimacy(v: float) -> float:
        return max(0.0, min(100.0, float(v)))

    def get_relation(self, user_id: str):
        """返回表存真实值（无行 None）。调用层负责语义：admin=100 恒定，public 不读。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT intimacy, mood, peak FROM relation WHERE user_id=?", (user_id,)
            ).fetchone()
        if not row:
            return None
        return {"intimacy": float(row["intimacy"] or 0.0), "mood": row["mood"], "peak": float(row["peak"] or 0.0)}

    def update_relation(self, user_id: str, delta: float, mood: str | None, ts: str | None = None, cap: float = 100.0):
        """关系记账：小步累计（clamp 0-cap）；同步维护历史峰值（滞回用）；无行时从 delta 起。
        2026-09-05：cap=白名单上限（80）——开放好感但不会越到"满"（100+爱意是主人的位置）。"""
        ts = ts or _now()
        with self._lock:
            row = self._conn.execute(
                "SELECT intimacy, mood, peak FROM relation WHERE user_id=?", (user_id,)
            ).fetchone()
            old = float(row["intimacy"] or 0.0) if row else 0.0
            new = self._clamp_intimacy(min(old + delta, float(cap)))
            peak = max(float(row["peak"] or 0.0), new) if row else new
            new_mood = mood if mood else (row["mood"] if row else "平静")
            self._conn.execute(
                """INSERT INTO relation(user_id, intimacy, mood, updated_ts, peak) VALUES(?,?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     intimacy=excluded.intimacy, mood=excluded.mood, updated_ts=excluded.updated_ts, peak=excluded.peak""",
                (user_id, new, new_mood, ts, peak),
            )
            self._conn.commit()

    def set_intimacy(self, user_id: str, value: float, mood: str | None = None, ts: str | None = None):
        """直接设置亲密度（clamp 0-100；mood 一并记录；峰值同步维护）。"""
        ts = ts or _now()
        with self._lock:
            row = self._conn.execute(
                "SELECT mood, peak FROM relation WHERE user_id=?", (user_id,)
            ).fetchone()
            new_mood = mood if mood else (row["mood"] if row else "平静")
            new = self._clamp_intimacy(value)
            peak = max(float(row["peak"] or 0.0), new) if row else new
            self._conn.execute(
                """INSERT INTO relation(user_id, intimacy, mood, updated_ts, peak) VALUES(?,?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     intimacy=excluded.intimacy, mood=excluded.mood, updated_ts=excluded.updated_ts, peak=excluded.peak""",
                (user_id, new, new_mood, ts, peak),
            )
            self._conn.commit()

    # ---------- summaries ----------
    SUMMARY_CAP = 20  # 每用户最多保留的摘要条数（防表无限增长）

    def add_summary(self, user_id: str, ts: str, content: str):
        with self._lock:
            self._conn.execute(
                "INSERT INTO summaries(user_id, ts, content) VALUES(?,?,?)", (user_id, ts, content)
            )
            # 容量控制：超出上限删除最旧的
            self._conn.execute(
                "DELETE FROM summaries WHERE user_id=? AND id NOT IN "
                "(SELECT id FROM summaries WHERE user_id=? ORDER BY id DESC LIMIT ?)",
                (user_id, user_id, self.SUMMARY_CAP),
            )
            self._conn.commit()

    def get_latest_summary(self, user_id: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT content, ts FROM summaries WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)
            ).fetchone()
        return {"content": row["content"], "ts": row["ts"]} if row else None

    # ---------- 复盘（每日/每周自动提取，增强记忆库） ----------
    def upsert_daily_review(self, user_id: str, date: str, content: str, pending: str = ""):
        with self._lock:
            self._conn.execute(
                """INSERT INTO daily_reviews(user_id, date, content, pending) VALUES(?,?,?,?)
                   ON CONFLICT(user_id, date) DO UPDATE SET content=excluded.content, pending=excluded.pending""",
                (user_id, date, content, pending),
            )
            self._conn.commit()

    def upsert_weekly_review(self, user_id: str, week_start: str, content: str, life: str = ""):
        with self._lock:
            self._conn.execute(
                """INSERT INTO weekly_reviews(user_id, week_start, content, life) VALUES(?,?,?,?)
                   ON CONFLICT(user_id, week_start) DO UPDATE SET content=excluded.content, life=excluded.life""",
                (user_id, week_start, content, life),
            )
            self._conn.commit()

    def get_latest_daily_review(self, user_id: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT date, content, COALESCE(pending,'') AS pending FROM daily_reviews WHERE user_id=? ORDER BY date DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        return {"date": row["date"], "content": row["content"], "pending": row["pending"]} if row else None

    def get_latest_weekly_review(self, user_id: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT week_start, content, COALESCE(life,'') AS life FROM weekly_reviews WHERE user_id=? ORDER BY week_start DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        return {"week_start": row["week_start"], "content": row["content"], "life": row["life"]} if row else None

    def get_latest_weekly(self, user_id: str):
        """最新一条周复盘（仅 week_start+content）。
        2026-09-08 P-5 周复盘消费端：brain 私聊感知注入"上周的你们"——复盘产出不再是只存不读的存档。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT week_start, content FROM weekly_reviews WHERE user_id=? ORDER BY week_start DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        return {"week_start": row["week_start"], "content": row["content"]} if row else None

    def get_daily_reviews(self, user_id: str, since_date: str, limit: int = 10):
        with self._lock:
            rows = self._conn.execute(
                "SELECT date, content FROM daily_reviews WHERE user_id=? AND date>=? ORDER BY date DESC LIMIT ?",
                (user_id, since_date, limit),
            ).fetchall()
        return [{"date": r["date"], "content": r["content"]} for r in rows]

    # ---------- emotions ----------
    def record_emotion(self, user_id: str, emotion: str, intensity: float, ts: str):
        with self._lock:
            self._conn.execute(
                "INSERT INTO emotions(user_id, ts, emotion, intensity) VALUES(?,?,?,?)",
                (user_id, ts, emotion, intensity),
            )
            self._conn.commit()

    def get_latest_emotion(self, user_id: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT emotion, intensity FROM emotions WHERE user_id=? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        return {"emotion": row["emotion"], "intensity": row["intensity"]} if row else None

    def get_recent_emotions(self, user_id: str, limit: int = 6) -> list[dict]:
        """最近 N 条情绪（时间正序：旧→新）——情绪连续体/弧线注入。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT emotion, intensity FROM emotions WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [{"emotion": r["emotion"], "intensity": r["intensity"]} for r in reversed(rows or [])]

    def get_emotion_stats(self, hours: float = 24.0) -> dict:
        """最近 N 小时情绪分布（监测/评测用）。"""
        try:
            from datetime import datetime, timedelta, timezone as _tz

            cutoff = (datetime.now(_tz.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
            with self._lock:
                rows = self._conn.execute(
                    "SELECT emotion, COUNT(*) AS c FROM emotions WHERE ts >= ? GROUP BY emotion ORDER BY c DESC",
                    (cutoff,),
                ).fetchall()
            return {r["emotion"]: r["c"] for r in rows or []}
        except Exception:  # noqa: BLE001
            return {}

    def get_emotion_intensity_near(self, user_id: str, ts: str, window_days: float = 1.0):
        """ts（UTC ISO）±window_days 内的最强 emotions.intensity。
        2026-09-08 P-2 召回情绪加成用：该事实时间附近的情绪越强、事实权重越高；
        无记录/时间解析失败返回 None（调用方不加成）。
        B5（2026-09-09 审计）：多事实召回路径请改用 get_emotions_since 批量取（本方法保留
        给单点查询调用方）——每事实一次本查询曾是召回排序 O(n) 次 DB 往返。"""
        try:
            t0 = datetime.fromisoformat(str(ts)).astimezone(timezone.utc)
        except (ValueError, TypeError):
            return None
        lo = (t0 - timedelta(days=window_days)).isoformat(timespec="seconds")
        hi = (t0 + timedelta(days=window_days)).isoformat(timespec="seconds")
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(intensity) AS m FROM emotions WHERE user_id=? AND ts>=? AND ts<=?",
                (user_id, lo, hi),
            ).fetchone()
        m = row["m"] if row else None
        try:
            return None if m is None else float(m)
        except (TypeError, ValueError):
            return None

    def get_emotions_since(self, user_id: str, ts_lo: str) -> list[dict]:
        """B5 批量版：该用户 ts>=ts_lo 的全部情绪行 [(ts, intensity)]，供召回加成
        一次查询 + Python 侧按各事实自己的 ±1 天窗口取 MAX（替代每事实一次 SQL）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, intensity FROM emotions WHERE user_id=? AND ts>=?",
                (user_id, ts_lo),
            ).fetchall()
        return [{"ts": r["ts"], "intensity": r["intensity"]} for r in rows]

    # ---------- open_threads（2026-09-08 P-1 话题悬挂：没聊完/被岔开的话题，下次自然接上） ----------
    OPEN_THREAD_TTL_DAYS = 7.0  # 未完成话题的注入窗口（天）；超窗视为自然淡出
    OPEN_THREAD_SWEEP_SECS = 3600.0  # C7：超龄清理（done 置位）节流——每小时至多一次

    def add_open_thread(self, user_id: str, content: str, ts: str | None = None):
        """登记一条悬挂话题（done=0；ts 缺省=现在 UTC ISO）。"""
        ts = ts or _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO open_threads(user_id, ts, content, done) VALUES(?,?,?,0)",
                (user_id, ts, content),
            )
            self._conn.commit()

    def done_open_threads_after_7d(self, user_id: str, ttl_days: float | None = None) -> int:
        """超过 TTL（默认 7 天）仍未完成的悬挂话题置 done=1（done_after_7d 过滤：自然淡出，不删数据）。
        返回本次标记条数。"""
        ttl = float(ttl_days if ttl_days is not None else self.OPEN_THREAD_TTL_DAYS)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=ttl)).isoformat(timespec="seconds")
        with self._lock:
            cur = self._conn.execute(
                "UPDATE open_threads SET done=1 WHERE user_id=? AND done=0 AND ts<?",
                (user_id, cutoff),
            )
            self._conn.commit()
            return cur.rowcount

    def get_latest_open_thread(self, user_id: str, within_days: float | None = None):
        """within_days（默认 7）天内未完成的最新一条悬挂话题；无则 None。
        消费端（P-1）：只有窗口内未完成的才注入感知。
        C7（2026-09-09 审计）：本查询在每次私聊感知注入都会走到，顺带的一次超龄清理改为
        节流执行（记 last-sweep 时间戳，每小时至多一次）——清理本身只是例行置 done，不必逐次跑。"""
        win = float(within_days if within_days is not None else self.OPEN_THREAD_TTL_DAYS)
        now_mono = time.monotonic()
        if now_mono - getattr(self, "_ot_sweep_mono", 0.0) >= self.OPEN_THREAD_SWEEP_SECS:
            self._ot_sweep_mono = now_mono
            try:
                self.done_open_threads_after_7d(user_id, ttl_days=win)
            except sqlite3.Error:
                pass  # 维护性清扫（best-effort）：到点会再试；失败不影响本次查询返回什么
        cutoff = (datetime.now(timezone.utc) - timedelta(days=win)).isoformat(timespec="seconds")
        with self._lock:
            row = self._conn.execute(
                "SELECT id, ts, content FROM open_threads WHERE user_id=? AND done=0 AND ts>=? ORDER BY id DESC LIMIT 1",
                (user_id, cutoff),
            ).fetchone()
        return {"id": row["id"], "ts": row["ts"], "content": row["content"]} if row else None

    # ---------- fact_corrections（2026-09-08 活人感 3-1：对话级认错——「我记错了」的对话行为供给侧） ----------
    def get_fact(self, user_id: str, key: str):
        """单条事实（按 key）；无则 None。修正记录需要旧值对照（新值≠旧值才算"记错了"）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT key, value FROM facts WHERE user_id=? AND key=?", (user_id, key)
            ).fetchone()
        return {"key": row["key"], "value": row["value"]} if row else None

    def add_fact_correction(self, user_id: str, old_text: str, new_text: str, ts: str | None = None):
        """登记一条事实修正（consumed=0 待消费）。「最近一次」语义：新修正入账时清掉该用户旧行
        （含已消费）——表保持每用户 ≤1 行；消费端取走即置 consumed，只注一次。"""
        ts = ts or _now()
        with self._lock:
            self._conn.execute("DELETE FROM fact_corrections WHERE user_id=?", (user_id,))
            self._conn.execute(
                "INSERT INTO fact_corrections(user_id, old_text, new_text, ts, consumed) VALUES(?,?,?,?,0)",
                (user_id, old_text, new_text, ts),
            )
            self._conn.commit()

    def consume_pending_fact_correction(self, user_id: str):
        """取走该用户最近一条未消费修正并置 consumed（注入一次后不再复读）；无未消费返回 None。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, old_text, new_text, ts FROM fact_corrections WHERE user_id=? AND consumed=0 "
                "ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            if not row:
                return None
            self._conn.execute("UPDATE fact_corrections SET consumed=1 WHERE id=?", (row["id"],))
            self._conn.commit()
        return {"old_text": row["old_text"], "new_text": row["new_text"], "ts": row["ts"]}


db = MemoryDB()
