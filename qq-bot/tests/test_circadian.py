# -*- coding: utf-8 -*-
"""作息感知（2026-09-08 活人感 3-2）独立测试：直方图 / 时段归纳 / 缓存行为。

直跑（无 pytest 依赖，assert 自解释）：
    python tests/test_circadian.py

隔离保证（不碰 data/ 任何运行文件）：
- 在加载 store 模块**之前**设置 MEMORY_DB_PATH 指向 tmp 库——store.py 的模块级单例
  db = MemoryDB() 随 env 打开 tmp 库，全程不打开生产 data/memory.db；
- 用 importlib 按文件路径直接加载 plugins/memory/store.py（模块内仅依赖标准库），
  绕开 plugins/memory/__init__.py 的 nonebot/openai 重依赖——保证本测试独立可运行；
- 结束前显式 close 全部 sqlite 连接（Windows 上连接不关会让 tmp 目录删不掉）。

播种时刻全部相对 now 取（避开 days 窗口边界的包含/排除抖动）；断言用的本地小时
均由播种时刻动态换算（.astimezone().hour），测试在任何时区行为一致。
"""
import importlib.util
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve()
_STORE_FILE = _HERE.parents[1] / "plugins" / "memory" / "store.py"
_DEBUG_FILE = _HERE.parents[1] / "plugins" / "debug" / "__init__.py"

_tmpdir = tempfile.TemporaryDirectory(prefix="circadian_test_")
os.environ["MEMORY_DB_PATH"] = str(Path(_tmpdir.name) / "memory.db")  # 必须在 exec_module 之前

_spec = importlib.util.spec_from_file_location("circadian_store", _STORE_FILE)
store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(store)

_OPEND = []  # 本测试开过的全部连接（收尾统一 close，Windows 才能清掉 tmp 目录）


def _new_db():
    db = store.MemoryDB()
    _OPEND.append(db)
    return db


def _seed(db, uid, utc_dt, role="user", n=1):
    """按指定 UTC 时刻播种消息（add_message 固定写 now，历史时刻只能直插）。"""
    ts = utc_dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    db._conn.executemany(
        "INSERT INTO messages(ts, user_id, group_id, role, content) VALUES(?,?,?,?,?)",
        [(ts, uid, None, role, "seed")] * n,
    )
    db._conn.commit()


def _now_trunc():
    """当前 UTC 时刻（截到整点）——播种基准，保证落在任意 days 窗口内部而非边界。"""
    return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)


def test_env_isolation():
    """MEMORY_DB_PATH 生效：模块级单例落在 tmp 库，生产 data/memory.db 全程未被打开。"""
    assert str(store._DB_PATH).startswith(str(_tmpdir.name)), f"单例库不在 tmp：{store._DB_PATH}"
    assert store.db._path == str(store._DB_PATH)
    print("ok: env 隔离——单例库 =", store.db._path)


def test_histogram():
    """直方图：只数 user 角色 / 本地小时归桶 / 用户隔离 / 全部在窗内。"""
    db = _new_db()
    uid, other = "u_hist", "u_other"
    base = _now_trunc() - timedelta(days=2)  # 2 天前：稳在 14 天窗内部
    h0 = base.astimezone().hour              # 期望桶：由播种时刻动态换算的本地小时
    h1 = (base + timedelta(hours=1)).astimezone().hour
    h2 = (base + timedelta(hours=2)).astimezone().hour
    h3 = (base + timedelta(hours=3)).astimezone().hour

    _seed(db, uid, base, role="user", n=3)
    _seed(db, uid, base + timedelta(hours=1), role="user", n=2)
    _seed(db, uid, base + timedelta(hours=2), role="assistant", n=5)  # bot 自己的话不计
    _seed(db, other, base + timedelta(hours=3), role="user", n=7)     # 别人的话不计

    hist = db.get_active_hours(uid, use_cache=False)
    assert hist.get(h0, 0) == 3, f"h{h0} 期望 3，实得 {hist.get(h0, 0)}"
    assert hist.get(h1, 0) == 2, f"h{h1} 期望 2，实得 {hist.get(h1, 0)}"
    assert hist.get(h2, 0) == 0, f"assistant 消息不应计数，h{h2}={hist.get(h2, 0)}"
    assert hist.get(h3, 0) == 0, f"其他用户的消息不应计入，h{h3}={hist.get(h3, 0)}"
    assert all(0 <= int(k) <= 23 for k in hist), "桶键必须是 0-23 整数小时"
    print(f"ok: 直方图计数/角色过滤/用户隔离 {hist}")


def test_days_window():
    """窗口：days=14 只含近 14 天；days 参数真正生效；缓存 key 含 days 不串窗。"""
    db = _new_db()
    uid = "u_window"
    t = _now_trunc()
    t_a = t - timedelta(hours=6)                    # 1 天窗内
    t_b = t - timedelta(days=2)                     # 仅 14 天窗
    t_c = t - timedelta(days=3) - timedelta(hours=5)  # 仅 14 天窗
    t_d = t - timedelta(days=20) + timedelta(hours=2)  # 窗口外（+2h 错开本地桶：整 20 天与 t_b 同本地小时）
    h_a, h_b, h_c, h_d = (x.astimezone().hour for x in (t_a, t_b, t_c, t_d))
    assert len({h_a, h_b, h_c, h_d}) == 4, "前提：四个桶本地小时互异，否则断言失真"

    _seed(db, uid, t_a, role="user", n=5)
    _seed(db, uid, t_b, role="user", n=11)
    _seed(db, uid, t_c, role="user", n=4)
    _seed(db, uid, t_d, role="user", n=6)

    h14 = db.get_active_hours(uid, use_cache=False)  # 默认 days=14
    assert h14.get(h_a, 0) == 5, f"6 小时前应计入：{h14}"
    assert h14.get(h_b, 0) == 11, f"2 天前应计入：{h14}"
    assert h14.get(h_c, 0) == 4, f"3 天前应计入：{h14}"
    assert h14.get(h_d, 0) == 0, f"20 天前不应计入：{h14}"
    h1 = db.get_active_hours(uid, days=1, use_cache=False)
    assert h1.get(h_a, 0) == 5, f"days=1 应含 6 小时前：{h1}"
    assert h1.get(h_b, 0) == 0 and h1.get(h_c, 0) == 0, f"days=1 不应含 2/3 天前：{h1}"
    print(f"ok: 时间窗 days=14 {h14} / days=1 {h1}")


def test_summarize_segments():
    """时段归纳：相邻小时并段（含跨午夜）、按权重降序、2-3 段、噪音小时不入段。"""
    f = store.summarize_active_hours
    # 双段（规格示例形态）：22/23 与 12/13 各成一段；3 点只有 2 条=噪音不入段
    assert f({22: 40, 23: 30, 12: 20, 13: 15, 3: 2}) == "22-24点和12-14点", \
        f({22: 40, 23: 30, 12: 20, 13: 15, 3: 2})
    # 三段
    assert f({22: 40, 23: 35, 12: 20, 13: 18, 8: 16, 9: 15}) == "22-24点、12-14点和8-10点"
    # 跨午夜：23 点与 0 点并成一段
    assert f({23: 50, 0: 40, 12: 20, 13: 19}) == "23-1点和12-14点"
    # 段长上限 3 小时
    assert f({22: 50, 23: 45, 0: 44, 1: 43, 12: 30, 13: 29}) == "22-1点和12-14点"
    # 单段 / 单小时
    assert f({12: 10}) == "12-13点"
    print("ok: 时段归纳（并段/跨午夜/降序/噪音过滤）")


def test_summarize_insufficient():
    """数据不足（<10 条）不归纳——宁缺毋滥，调用方据此跳过注入。"""
    f = store.summarize_active_hours
    assert f({}) == ""
    assert f({22: 5, 23: 4}) == ""          # 共 9 条 < 10
    assert f({22: 6, 23: 4}) == "22-24点"   # 共 10 条=门槛，恰好可用
    assert f(None) == ""
    print("ok: 数据不足护栏（<10 条返回空串）")


def test_cache():
    """缓存：TTL 内命中旧值；use_cache=False 强制重算；TTL 过期自动重算；命中返回副本。"""
    db = _new_db()
    uid = "u_cache"
    base = _now_trunc() - timedelta(days=2)  # 稳在窗口内
    h0 = base.astimezone().hour
    h_new = (base - timedelta(hours=5)).astimezone().hour  # 与 h0 相距 5h，必不同桶
    assert h_new != h0

    _seed(db, uid, base, role="user", n=12)
    hist1 = db.get_active_hours(uid)  # 默认走缓存 → 本次计算并落缓存
    key = (db._path, uid, 14)
    assert key in store._ACTIVE_HOURS_CACHE, "首次调用应写入缓存"
    assert hist1.get(h0, 0) == 12

    _seed(db, uid, base - timedelta(hours=5), role="user", n=5)  # 之后播种：缓存不该看见
    hist2 = db.get_active_hours(uid)
    assert hist2 == hist1 and hist2.get(h_new, 0) == 0, "TTL 内应命中缓存（看不到新数据）"

    hist3 = db.get_active_hours(uid, use_cache=False)
    assert hist3.get(h_new, 0) == 5 and hist3.get(h0, 0) == 12, "use_cache=False 应强制重算"

    # TTL 过期：把缓存时间戳拨老（不真等 1 小时）
    store._ACTIVE_HOURS_CACHE[key] = (time.monotonic() - store.ACTIVE_HOURS_TTL - 1, hist1)
    hist4 = db.get_active_hours(uid)
    assert hist4.get(h_new, 0) == 5, "TTL 过期应自动重算"

    # 命中返回副本：调用方改返回值不污染缓存
    hist2[h0] = 999
    assert store._ACTIVE_HOURS_CACHE[key][1].get(h0) == hist1.get(h0), "缓存值被调用方污染"

    store.active_hours_cache_clear()
    assert not store._ACTIVE_HOURS_CACHE, "active_hours_cache_clear 应清空缓存"
    print("ok: 缓存命中/强制重算/TTL 过期/副本隔离/手动清空")


def test_debug_wiring():
    """接线检查（源码级，避免 import 依赖 nonebot 的 debug 模块）：注入行与措辞在位。"""
    src = _DEBUG_FILE.read_text(encoding="utf-8")
    assert "get_active_hours" in src, "debug 未调用 get_active_hours"
    assert "summarize_active_hours" in src, "debug 未调用时段归纳"
    assert "（TA 通常在这些时段活跃：" in src and "——现在找不找TA，你自己拿捏）" in src, \
        "注入措辞与裁决口径不符（事实+自决）"
    # 只查活代码：该名现仅存在于记载删除史的注释里（2026-09-08 P3），出现赋值才算复活
    assert "USER_ACTIVE_WINDOW =" not in src and "USER_ACTIVE_WINDOW=" not in src, \
        "不得复活机器时窗常量（2026-09-08 P3 已删）"
    print("ok: debug perceive 注入接线与措辞")


def main():
    tests = [test_env_isolation, test_histogram, test_days_window,
             test_summarize_segments, test_summarize_insufficient,
             test_cache, test_debug_wiring]
    for t in tests:
        t()
    print(f"\nALL PASS ({len(tests)} tests)")


if __name__ == "__main__":
    try:
        main()
    finally:
        for _d in _OPEND:
            try:
                _d._conn.close()
            except Exception:
                pass
        try:
            store.db._conn.close()  # 模块级单例同样要关，否则 tmp 目录删不掉
        except Exception:
            pass
        _tmpdir.cleanup()
