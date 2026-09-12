# -*- coding: utf-8 -*-
"""跨卡生活状态隔离（2026-09-09 方案A，用户裁决）独立测试。

直跑（无 pytest 依赖，assert/check 自解释）：
    cd E:\\robot\\qq-bot && .venv\\Scripts\\python.exe tests\\test_card_life.py

覆盖：
- lifesim.switch_persona_life：归档 / 恢复档案 / 全新默认 / 同名 no-op / 空旧卡 /
  卡名消毒 / 缺文件容错 / 档案损坏容错 / 治理失败绝不抛出；
- brain.set_persona 接线：换卡触发生活档归档恢复 + pending 约定按卡归档恢复 +
  PERSONA_SWITCH_FILE **所有换卡都记录**（含同世界观——方案A"记忆也要变"）；
- reset_world_scene 改一删一确认。

隔离保证（不碰 data/ 真实运行文件）：
- lifesim / brain 的全部状态文件常量（LIFE_*、PENDING_FILE、PERSONA_*、MODE_FILE）
  monkeypatch 到临时目录；
- persona.load_persona 打桩为三张假卡（amiya/kaltsit 同 universe=arknights，koishi=touhou）；
- 与 smoke_test 同法 `import bot` 初始化 nonebot（导入级验证，不启动事件循环），
  导入后立即静默 loguru（测试噪音不进生产 bot.log）。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot  # noqa: E402,F401  nonebot.init + 插件加载（不 run）

# 域5审计P3 同法（smoke_test）：import bot 会把 loguru sink 接到真实 data/bot.log——
# 立即静默，fail-closed 类测试的 WARNING 不进生产日志。
import logging  # noqa: E402
from loguru import logger as _t_logger  # noqa: E402

_t_logger.remove()
_t_logger.add(lambda m: None, level="INFO")
logging.disable(logging.WARNING)

from agent import lifesim as ls  # noqa: E402
from plugins import brain as B  # noqa: E402
from plugins import persona  # noqa: E402

_PASS = _FAIL = 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"ok: {name}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {name}")


# ============ monkeypatch：全部文件常量 → 临时目录 ============
_TMP = Path(tempfile.mkdtemp(prefix="card_life_test_"))
_LS_BACKUP = {k: getattr(ls, k) for k in ("LIFE_STATE_FILE", "LIFE_LOG_FILE", "DIARY_FILE", "LIFE_STATES_DIR")}
_B_BACKUP = {k: getattr(B, k) for k in ("PERSONA_SELECT_FILE", "MODE_FILE", "PERSONA_SWITCH_FILE", "PENDING_FILE")}
_PERSONA_LOAD_BACKUP = persona.load_persona

ls.LIFE_STATE_FILE = _TMP / "life_state.json"
ls.LIFE_LOG_FILE = _TMP / "life_log.jsonl"
ls.DIARY_FILE = _TMP / "life_diary.jsonl"
ls.LIFE_STATES_DIR = _TMP / "life_states"
B.PERSONA_SELECT_FILE = _TMP / "persona_select.json"
B.MODE_FILE = _TMP / "persona_mode.json"
B.PERSONA_SWITCH_FILE = _TMP / "persona_switch_ts.json"
B.PENDING_FILE = _TMP / "pending_tasks.json"

_CARDS = {"amiya": "arknights", "kaltsit": "arknights", "koishi": "touhou"}


def _fake_load_persona(name: str) -> dict:
    if name in _CARDS:
        return {"name": name, "universe": _CARDS[name]}
    raise FileNotFoundError(f"persona not found: {name}")


persona.load_persona = _fake_load_persona


def _restore():
    for k, v in _LS_BACKUP.items():
        setattr(ls, k, v)
    for k, v in _B_BACKUP.items():
        setattr(B, k, v)
    persona.load_persona = _PERSONA_LOAD_BACKUP


def _read_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _write_state(doing: str, **extra):
    st = {"ts": 1.0, "doing": doing, "mood": "平静", "scene": ""}
    st.update(extra)
    ls.LIFE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    ls.LIFE_STATE_FILE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


# ============ 1. switch_persona_life 本体 ============

def test_archive_then_fresh():
    """amiya→kaltsit（kaltsit 无档）：amiya 生活整组归档，活动位全新默认态。"""
    _write_state("在办公室准备今晚的蛋糕", mood="期待", scene="办公室", plan="今晚做蛋糕", plan_day="2026-09-09")
    ls.LIFE_LOG_FILE.write_text(
        '{"ts": 90.0, "doing": "列了采购单", "mood": "平静"}\n'
        '{"ts": 95.0, "doing": "在办公室准备今晚的蛋糕", "mood": "期待"}\n', encoding="utf-8")
    ls.DIARY_FILE.write_text('{"date": "2026-09-08", "text": "昨天陪博士处理了任务"}\n', encoding="utf-8")
    ret = ls.switch_persona_life("amiya", "kaltsit")
    check("切卡返回统计结构", ret == {"old": "amiya", "new": "kaltsit", "archived": True, "restored": "fresh"})
    arc = _read_json(ls.LIFE_STATES_DIR / "amiya.json")
    check("归档含 life_state 全量", arc["life_state"].get("doing") == "在办公室准备今晚的蛋糕"
          and arc["life_state"].get("plan") == "今晚做蛋糕" and arc["life_state"].get("scene") == "办公室")
    check("归档含 life_log 副本", len([l for l in arc["life_log"].strip().splitlines() if l.strip()]) == 2
          and "列了采购单" in arc["life_log"])
    check("归档含日记副本", "昨天陪博士" in arc["life_diary"])
    fresh = _read_json(ls.LIFE_STATE_FILE)
    check("活动位全新默认态", fresh.get("doing") == "" and fresh.get("ts") == 0.0
          and fresh.get("mood") == "平静" and "plan" not in fresh and fresh.get("scene", "") == "")
    check("活动位 life_log 清空", ls.LIFE_LOG_FILE.read_text(encoding="utf-8") == "")
    check("活动位日记清空", ls.DIARY_FILE.read_text(encoding="utf-8") == "")


def test_restore_profile():
    """kaltsit 过了自己的生活 → 切回 amiya：恢复 amiya 自己的档，kaltsit 生活归档自洽。"""
    _write_state("在医务室值班", mood="严肃", scene="医务室")
    ls.LIFE_LOG_FILE.write_text('{"ts": 150.0, "doing": "在医务室值班", "mood": "严肃"}\n', encoding="utf-8")
    ret = ls.switch_persona_life("kaltsit", "amiya")
    check("恢复档案返回 archived", ret.get("restored") == "archived" and ret.get("old") == "kaltsit")
    st = _read_json(ls.LIFE_STATE_FILE)
    check("恢复 amiya 自己的生活档", st.get("doing") == "在办公室准备今晚的蛋糕"
          and st.get("scene") == "办公室" and st.get("plan") == "今晚做蛋糕")
    check("恢复 amiya 轨迹", "列了采购单" in ls.LIFE_LOG_FILE.read_text(encoding="utf-8"))
    check("恢复 amiya 日记", "昨天陪博士" in ls.DIARY_FILE.read_text(encoding="utf-8"))
    kal = _read_json(ls.LIFE_STATES_DIR / "kaltsit.json")
    check("kaltsit 生活已归档自洽", kal["life_state"].get("doing") == "在医务室值班")


def test_noop_and_empty_old():
    """同名 no-op（活动位不动）；新卡名为空 no-op；空旧卡（首次设卡）不归档但照常清空。"""
    before_state = ls.LIFE_STATE_FILE.read_text(encoding="utf-8")
    before_log = ls.LIFE_LOG_FILE.read_text(encoding="utf-8")
    ret = ls.switch_persona_life("amiya", "amiya")
    check("同名 no-op 返回 skipped", ret == {"skipped": True})
    check("no-op 不动活动位", ls.LIFE_STATE_FILE.read_text(encoding="utf-8") == before_state
          and ls.LIFE_LOG_FILE.read_text(encoding="utf-8") == before_log)
    check("新卡名为空 no-op", ls.switch_persona_life("amiya", "") == {"skipped": True})
    _write_state("谁的生活")
    ret2 = ls.switch_persona_life("", "koishi")
    check("空旧卡：不归档但清空活动位", ret2.get("archived") is False and ret2.get("restored") == "fresh"
          and _read_json(ls.LIFE_STATE_FILE).get("doing") == "")


def test_sanitize_and_missing_diary():
    """卡名消毒：空格/中文/符号 → _；日记文件缺失时归档容错（life_diary 记空串）。"""
    ls.DIARY_FILE.unlink(missing_ok=True)
    _write_state("敏感卡名的生活")
    ret = ls.switch_persona_life("Amiya 博士!", "K-2S/T")
    check("消毒文件名归档", (ls.LIFE_STATES_DIR / "Amiya____.json").exists()
          and ret.get("old") == "Amiya 博士!" and ret.get("archived") is True)
    arc = _read_json(ls.LIFE_STATES_DIR / "Amiya____.json")
    check("缺日记文件归档容错", arc.get("life_diary") == "" and arc["life_state"].get("doing") == "敏感卡名的生活")
    check("新卡消毒名无档→fresh（不建档）", not (ls.LIFE_STATES_DIR / "K-2S_T.json").exists()
          and ret.get("restored") == "fresh")
    check("纯符号/中文卡名全替换；空卡名退 default", ls._sanitize_card("///") == "___"
          and ls._sanitize_card("凯尔希") == "___" and ls._sanitize_card("") == "default"
          and ls._sanitize_card("  ") == "default")


def test_corrupt_archive_fallback():
    """新卡档案损坏 → 视为无档走全新默认（不 crash、不串档）。"""
    (ls.LIFE_STATES_DIR / "koishi.json").write_text("{broken", encoding="utf-8")
    _write_state("古明地恋的旧生活")
    ret = ls.switch_persona_life("kaltsit", "koishi")
    check("损坏档案按无档处理", ret.get("restored") == "fresh"
          and _read_json(ls.LIFE_STATE_FILE).get("doing") == "")


def test_never_raises():
    """治理失败绝不抛出：归档目录被文件占位（mkdir 必败）→ 返回 {"error": ...}。"""
    blocker = _TMP / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    ls.LIFE_STATES_DIR = blocker
    try:
        ret = ls.switch_persona_life("amiya", "kaltsit")
        check("治理失败返回 error 不抛出", isinstance(ret, dict) and "error" in ret)
    finally:
        ls.LIFE_STATES_DIR = _TMP / "life_states"


def test_reset_world_scene_removed():
    """改一删一：reset_world_scene 已从 lifesim 删除（brain 接线替换为 switch_persona_life）。"""
    check("reset_world_scene 已删除", not hasattr(ls, "reset_world_scene"))


# ============ 2. brain.set_persona 接线 ============

def test_brain_set_persona_wiring():
    """set_persona 全链：首次设卡 → 同世界观切卡（生活/约定隔离 + 切换分界全记录）→ 切回恢复。"""
    # 清掉本体测试留下的档案/活动残留，让接线测试从干净世界开始
    import shutil
    shutil.rmtree(ls.LIFE_STATES_DIR, ignore_errors=True)
    ls.LIFE_LOG_FILE.unlink(missing_ok=True)
    ls.DIARY_FILE.unlink(missing_ok=True)
    assert B.set_persona("1001", "amiya") is True
    check("首次设卡落 persona_select", _read_json(B.PERSONA_SELECT_FILE).get("1001") == "amiya")
    hist0 = _read_json(B.PERSONA_SWITCH_FILE).get("1001")
    check("首次设卡也记录切换", isinstance(hist0, list) and len(hist0) == 1 and hist0[0]["p"] == "amiya")

    # amiya 过生活 + 一条约定（今晚蛋糕——用户实测 bug 的同款场景）
    _write_state("在办公室准备今晚的蛋糕", mood="期待", scene="办公室", plan="今晚做蛋糕", plan_day="2026-09-09")
    B._save_pending([{"id": 1, "uid": "1001", "gid": "", "content": "今晚一起吃蛋糕",
                      "note": "", "ts": 1.0, "mention_ts": 0.0, "src": ""}])

    # 同世界观切卡（amiya→kaltsit，universe 同为 arknights）——方案A 主战场
    assert B.set_persona("1001", "kaltsit") is True
    hist = _read_json(B.PERSONA_SWITCH_FILE).get("1001")
    check("同世界观换卡也记录切换分界", len(hist) == 2 and hist[-1]["p"] == "kaltsit")
    st = _read_json(ls.LIFE_STATE_FILE)
    check("kaltsit 全新生活档（不继承蛋糕计划/办公室场景）", st.get("doing") == "" and "plan" not in st
          and st.get("scene", "") == "")
    arc = _read_json(ls.LIFE_STATES_DIR / "amiya.json")
    check("amiya 生活已归档", arc["life_state"].get("doing") == "在办公室准备今晚的蛋糕")
    check("amiya 约定已归档", [r["content"] for r in B._load_pending(B._pending_profile_file("amiya"))]
          == ["今晚一起吃蛋糕"])
    check("kaltsit 活动约定为空", B._load_pending() == [])

    # 切回 amiya：生活与约定都回来（kaltsit 的空生活归到 kaltsit 名下）
    assert B.set_persona("1001", "amiya") is True
    st2 = _read_json(ls.LIFE_STATE_FILE)
    check("切回 amiya 生活档恢复", st2.get("doing") == "在办公室准备今晚的蛋糕" and st2.get("scene") == "办公室")
    check("切回 amiya 约定恢复", [r["content"] for r in B._load_pending()] == ["今晚一起吃蛋糕"])
    kal = _read_json(ls.LIFE_STATES_DIR / "kaltsit.json")
    check("kaltsit 生活归档自洽", kal["life_state"].get("doing") == "")
    hist2 = _read_json(B.PERSONA_SWITCH_FILE).get("1001")
    check("多次换卡历史完整", len(hist2) == 3 and [h["p"] for h in hist2] == ["amiya", "kaltsit", "amiya"])


def test_brain_cross_world_and_invalid():
    """跨世界观切卡照常工作；不存在的卡 set_persona 返回 False（validation 先于治理）。"""
    _write_state("amiya 又在做蛋糕", scene="办公室")
    assert B.set_persona("1002", "kaltsit") is True  # 同世界观
    assert B.set_persona("1002", "koishi") is True   # 跨世界观
    hist = _read_json(B.PERSONA_SWITCH_FILE).get("1002")
    check("跨世界观切换分界照常", [h["p"] for h in hist] == ["kaltsit", "koishi"])
    check("跨世界观切卡生活档照常隔离", _read_json(ls.LIFE_STATE_FILE).get("doing") == "")
    check("amiya 档案留存可回", (ls.LIFE_STATES_DIR / "amiya.json").exists())
    check("不存在的卡拒绝", B.set_persona("1001", "不存在的卡") is False)


def main():
    try:
        tests = [test_archive_then_fresh, test_restore_profile, test_noop_and_empty_old,
                 test_sanitize_and_missing_diary, test_corrupt_archive_fallback,
                 test_never_raises, test_reset_world_scene_removed,
                 test_brain_set_persona_wiring, test_brain_cross_world_and_invalid]
        for t in tests:
            print(f"-- {t.__doc__.strip().splitlines()[0] if t.__doc__ else t.__name__}")
            t()
    finally:
        _restore()
    print(f"\nALL PASS ({_PASS} checks)" if _FAIL == 0 else f"\n{_PASS} PASS / {_FAIL} FAIL")
    sys.exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    main()
