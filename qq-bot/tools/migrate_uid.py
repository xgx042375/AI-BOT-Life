# [DEV-ONLY] 一次性运维工具——不随核心发行版/SDK 分发。
# -*- coding: utf-8 -*-
r"""uid 迁移工具（2026-09-12 T5.3，用户决策 D3）。

换主人 QQ 号时必须同跑本工具：记忆库（memory.db）里所有 user_id 与 data\ 下的
按 uid 索引的 JSON 状态都以旧 QQ 为键，直接改 .env 会全部断链（等于换了个全新用户）。

用法（默认 dry-run，只报告不写盘）：
    cd E:\robot\qq-bot
    .venv\Scripts\python.exe tools\migrate_uid.py --from 10001 --to 123456789 --dry
    .venv\Scripts\python.exe tools\migrate_uid.py --from 10001 --to 123456789 --apply

纪律（与 spec §3 T5.3 对齐）：
  * --apply 前先把 memory.db 全量复制到 vault\backups\memory.db.bak_before_uidmigrate_<ts>；
  * 库内改动一律走单个事务（失败整体回滚）；
  * 表清单由 PRAGMA table_info 实测得出，不硬编码（新表带 user_id 自动纳入）；
  * 明确不碰 data\agent_checkpoints.db —— 那是 LangGraph 自己的库，uid 编在 thread_id 里，
    且 checkpoint/value 是 msgpack BLOB，改错一字节即线程损坏（spec §9.2 已裁决走 thread_id 删行，
    属独立工作项，本工具零接触）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # E:\robot
DATA = ROOT / "data"
DB_PATH = DATA / "memory.db"
BACKUP_DIR = ROOT / "vault" / "backups"

# data\ 下按 uid 索引的 JSON 状态（只处理实际存在的）
UID_JSON_DICTS = [                                   # 顶层 dict：uid -> 值
    "persona_select.json",
    "persona_switch_ts.json",
    "think_mode.json",
    "persona_mode.json",
]
UID_JSON_LISTS = [                                   # 顶层 list：成员就是 uid
    "intimate_whitelist.json",
]
UID_JSON_GLOBS_NESTED = [                            # 顶层 dict，uid 藏在某个子 dict 里
    ("pending_tasks*.json", ()),                     # 顶层 dict：uid -> 值
    ("proactive_state.json", ("users",)),            # 顶层 dict，users 子 dict 才是 uid->值
]


def db_uid_tables(con: sqlite3.Connection) -> list[str]:
    """实测库中所有带 user_id 列的表（按名排序；不硬编码）。"""
    names = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    out = []
    for t in names:
        cols = [r[1] for r in con.execute("PRAGMA table_info(%s)" % t)]
        if "user_id" in cols:
            out.append(t)
    return out


def count_rows(con: sqlite3.Connection, table: str, uid: str) -> int:
    try:
        return con.execute(
            "SELECT COUNT(*) FROM %s WHERE user_id = ?" % table, (uid,)).fetchone()[0]
    except sqlite3.Error as e:
        print("  !! %s 计数失败：%s" % (table, e))
        return -1


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as e:
        print("  !! %s 无法解析，跳过：%s" % (path.name, type(e).__name__))
        return None


def save_json(path: Path, obj) -> bool:
    """原子写（同项目口径：utf-8 无 BOM，indent=2）。"""
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError as e:
        print("  !! %s 写入失败：%s" % (path.name, e))
        return False


def plan_json_migrations(old: str, new: str) -> list[tuple[Path, object, str]]:
    """返回 [(路径, 迁移后的对象, 说明)]；不存在/无该键的不入列。"""
    plan: list[tuple[Path, object, str]] = []

    for name in UID_JSON_DICTS:
        p = DATA / name
        obj = load_json(p)
        if not isinstance(obj, dict) or old not in obj:
            continue
        if new in obj:
            plan.append((p, obj, "目标 uid 已存在（将合并：保留目标键原值，删除旧键）"))
            new_obj = dict(obj)
            del new_obj[old]
        else:
            new_obj = dict(obj)
            new_obj[new] = new_obj.pop(old)
        plan.append((p, new_obj, "顶层 dict 键 %s -> %s" % (old, new)))

    for name in UID_JSON_LISTS:
        p = DATA / name
        obj = load_json(p)
        if not isinstance(obj, list) or old not in obj:
            continue
        new_obj = [new if str(x) == old else x for x in obj]
        plan.append((p, new_obj, "顶层 list 成员 %s -> %s" % (old, new)))

    for pattern, nests in UID_JSON_GLOBS_NESTED:
        for p in sorted(DATA.glob(pattern)):
            obj = load_json(p)
            if not isinstance(obj, dict):
                continue
            changed = False
            new_obj = json.loads(json.dumps(obj, ensure_ascii=False))  # 深拷贝
            targets = [new_obj]
            for key in nests:
                sub = new_obj.get(key)
                if not isinstance(sub, dict):
                    targets = []
                    break
                targets = [sub]
            for tgt in targets:
                if old in tgt:
                    tgt[new] = tgt.pop(old)
                    changed = True
            if changed:
                where = ".".join(nests) if nests else "顶层 dict"
                plan.append((p, new_obj, "%s 键 %s -> %s" % (where, old, new)))
    return plan


def main() -> int:
    ap = argparse.ArgumentParser(description="记忆库与状态文件的 uid 迁移（默认 dry-run）")
    ap.add_argument("--from", dest="old", required=True, help="旧 QQ 号 / uid")
    ap.add_argument("--to", dest="new", required=True, help="新 QQ 号 / uid")
    ap.add_argument("--dry", action="store_true", help="只报告影响行数（默认行为）")
    ap.add_argument("--apply", action="store_true", help="真正落盘（先自动备份 memory.db）")
    args = ap.parse_args()

    old, new = str(args.old).strip(), str(args.new).strip()
    if not old or not new:
        print("--from / --to 不能为空")
        return 2
    if old == new:
        print("--from 与 --to 相同，无需迁移")
        return 2
    if args.apply and args.dry:
        print("--dry 与 --apply 互斥")
        return 2
    apply_mode = bool(args.apply)

    print("=" * 72)
    print("uid 迁移 %s  ->  %s     模式：%s" % (old, new, "APPLY（落盘）" if apply_mode else "DRY（只报告）"))
    print("=" * 72)

    if not DB_PATH.exists():
        print("memory.db 不存在：%s" % DB_PATH)
        return 1

    con = sqlite3.connect(str(DB_PATH))
    try:
        tables = db_uid_tables(con)
        print("\n[发现] 库中带 user_id 列的表共 %d 张（PRAGMA table_info 实测）：" % len(tables))
        for t in tables:
            print("   - %s" % t)

        print("\n[影响行数] --from %s" % old)
        counts: dict[str, int] = {}
        total = 0
        for t in tables:
            n = count_rows(con, t, old)
            counts[t] = n
            if n > 0:
                total += n
            mark = "  <-- 受影响" if n > 0 else ""
            print("   %-22s %6d%s" % (t, n, mark))
        print("   %-22s %6d" % ("合计", total))

        # 目标 uid 撞车检查（防把两个身份合并而不自知）
        collide = {t: count_rows(con, t, new) for t in tables}
        cl = {t: n for t, n in collide.items() if n > 0}
        if cl:
            print("\n[警告] --to 目标 uid 在库中已有数据，迁移会与之合并（不是覆盖）：")
            for t, n in cl.items():
                print("   %-22s %6d" % (t, n))

        print("\n[JSON 状态] data\\ 下按 uid 索引的文件：")
        json_plan = plan_json_migrations(old, new)
        for p, _obj, desc in json_plan:
            print("   - %-28s %s" % (p.name, desc))
        if not json_plan:
            print("   （无需迁移的键）")

        print("\n[明确不碰] %s" % (DATA / "agent_checkpoints.db"))
        print("   LangGraph 自有库（uid 编在 thread_id 内、checkpoint 为 msgpack BLOB）——")
        print("   spec §9.2 裁决按 thread_id 删行而非改 uid，属独立工作项，本工具零接触。")

        if not apply_mode:
            print("\n[DRY] 未写任何文件。确认上面的行数后，用 --apply 落盘。")
            return 0

        # ---------- 备份 ----------
        if total == 0 and not json_plan:
            print("\n[APPLY] 无可迁移内容（库里没有该 uid 的行，JSON 也没有该键）→ 不动库、不备份。")
            return 0
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        bak = BACKUP_DIR / ("memory.db.bak_before_uidmigrate_%s" % stamp)
        shutil.copy2(str(DB_PATH), str(bak))
        print("\n[备份] %s  (%d bytes)" % (bak, bak.stat().st_size))

        # ---------- 单事务迁移数据库 ----------
        print("\n[APPLY] 单事务迁移 %d 张表…" % len(tables))
        moved: dict[str, int] = {}
        try:
            con.execute("BEGIN")
            for t in tables:
                cur = con.execute(
                    "UPDATE %s SET user_id = ? WHERE user_id = ?" % t, (new, old))
                moved[t] = cur.rowcount
            con.execute("COMMIT")
        except sqlite3.Error as e:
            con.execute("ROLLBACK")
            print("   !! 事务失败已回滚：%s" % e)
            return 1
        for t in tables:
            if moved.get(t):
                print("   %-22s %6d 行" % (t, moved[t]))
        print("   合计 %d 行" % sum(moved.values()))

        # 迁完复查：旧 uid 应清零
        left = {t: count_rows(con, t, old) for t in tables}
        bad = {t: n for t, n in left.items() if n > 0}
        if bad:
            print("   !! 残留未迁移：%s" % bad)
        else:
            print("   核验：旧 uid 在全部表中已清零 ✓")
    finally:
        con.close()

    # ---------- JSON 迁移 ----------
    print("\n[APPLY] 迁移 JSON 状态…")
    ok = 0
    for p, obj, _desc in json_plan:
        if save_json(p, obj):
            print("   - %s ✓" % p.name)
            ok += 1
    print("   完成 %d/%d 个文件" % (ok, len(json_plan)))

    print("\n[完成] 重启机器人后生效。抽查：/状态 能看到旧关系数值即迁移成功。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
