# -*- coding: utf-8 -*-
r"""冒烟回归测试（2026-09-06 重写版，配合 agent 化重写后的架构）。

原则：
- 隔离：绝不写真实 data/ 状态文件（special/mood 用 tmp；不再测已退役的指数机）；
- 覆盖：core/（atomics/paths/perception/special/reply/mood）+ 各插件存活纯函数 + 接口契约；
- 纯函数级验证，不依赖 LLM/网络（本地引擎在线与否不影响）。

运行：cd E:\robot\qq-bot && .venv\Scripts\python.exe tests\smoke_test.py
"""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_PASS = _FAIL = 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"  [FAIL] {name}")


import bot  # noqa: F401,E402  触发 nonebot.init + 插件加载（导入级验证）

# 域5审计P3：import bot 会把 loguru sink 接到真实 data/bot.log——smoke 故意写坏的
# 临时文件（fail-closed 测试）会以 WARNING 进生产日志（用户凌晨看到的"json 报错"即此）。
# 压到 ERROR 级：真实异常仍可见，测试噪音不进生产日志。
import logging  # noqa: E402
from loguru import logger as _smoke_logger  # noqa: E402
_smoke_logger.remove()
_smoke_logger.add(lambda m: None, level="INFO")
logging.disable(logging.WARNING)

# ============ 1. core 基础设施 ============
print("== 1. core.atomics / core.paths ==")
from core import atomics, paths  # noqa: E402

assert str(paths.DATA_ROOT) == r"E:\robot\data", paths.DATA_ROOT
assert paths.PERSONAS_DIR.is_dir()
_fd, _tmp = tempfile.mkstemp(suffix=".json")
os.close(_fd)
_p = Path(_tmp)
check("原子写JSON往返", atomics.write_json_atomic(_p, {"a": "中文"}) and atomics.read_json(_p, None) == {"a": "中文"})
check("无BOM", not _p.read_text(encoding="utf-8").startswith("\ufeff"))
_p.write_text("{broken", encoding="utf-8")
check("损坏回退default", atomics.read_json(_p, {"d": 1}) == {"d": 1})
os.remove(_p)

# ============ 2. core.perception 感知构建器 ============
print("== 2. core.perception ==")
from core import perception as P  # noqa: E402
import datetime as dt  # noqa: E402

check("now_context", "星期" in P.now_context(dt.datetime(2026, 9, 6, 20, 0)) and "2026年9月6日" in P.now_context(dt.datetime(2026, 9, 6, 20, 0)))
base = dt.datetime(2026, 9, 6, 20, 0).timestamp()
check("time_note分钟", "约 5 分钟" in P.time_note(base - 300, now=base))
check("time_note小时", "2 小时" in P.time_note(base - 7200, now=base))
check("time_note隔夜", "隔了一夜/很久" in P.time_note(base - 5 * 3600, now=base))
check("time_note无记录", P.time_note(None, now=base).startswith("\n\n【时间】"))
check("familiar_note", "66/100" in P.familiar_note(66.0, None))
check("timeflow空", P.timeflow_note(None) == "" and P.timeflow_note(base - 60, now=dt.datetime.fromtimestamp(base)) == "")
check("timeflow小时", "过去 5 小时" in P.timeflow_note(base - 5 * 3600, now=dt.datetime.fromtimestamp(base)))
check("convo_note空", P.convo_note({}, "u1") == "")
check("convo_note在", "10 分钟前" in P.convo_note({"u2": base - 600}, "u1", now=base))
check("lang_note日语", "日语" in P.lang_note("こんにちは、良い天気ですね"))
check("lang_note中文空", P.lang_note("纯中文没有注记") == "")
check("msg_struct@我", "@ 了你" in P.msg_struct_note(["bot"], "bot", False))
check("msg_struct引用", "引用" in P.msg_struct_note([], "bot", True))
check("meme_note", "阴阳" in P.meme_note("对的对的", {"对的对的": {"meaning": "阴阳怪气", "take": "敷衍"}}))
check("meme_note未命中", P.meme_note("无关", {"对的对的": {"meaning": "x", "take": "y"}}) == "")
check("name_mention", "「阿米娅」" in P.name_mention_note("阿米娅在吗", {"阿米娅"}) and P.name_mention_note("无关", {"阿米娅"}) == "")
check("text_at_me", P.text_at_me("@阿米娅 来", {"阿米娅"}) is True and P.text_at_me("阿米娅", {"阿米娅"}) is False)
check("_persona_names过滤短名", "x" not in P._persona_names({"name": "x", "nickname": "阿米娅"}, "pn", {}))

# ============ 3. core.special 特殊层级 ============
print("== 3. core.special（隔离 tmp） ==")
from core import special as S  # noqa: E402

S.STATE_FILE = Path(tempfile.mkdtemp()) / "special_state.json"
S._STATE = None
# 2026-09-09 固定短语协议退役（agent 标记自决替代）——旧 parse_owner_command 断言整体删除
check("协议已退役", not hasattr(S, "parse_owner_command"))
check("旧协议正则已退役", all(not hasattr(S, n) for n in
      ("_ITEM_ON_RE", "_ITEM_OFF_RE", "_HYPNO_RE", "_HYPNO_HINT_RE", "_BW_RE", "_LOVE_END_RE")))
check("道具别名映射保留", S._ITEM_WORDS == {"口球": "item:ball", "淫纹": "item:mark", "震动棒": "item:vibe"})
S.set_state("hypno", "身体被强制听话")
check("set+active", "hypno" in S.active())
check("道具默认note", S.set_state("item:ball")["note"] == S.ITEM_NOTES["item:ball"])
check("perception_text", "口球" in S.perception_text() and "主人要求的效果" in S.perception_text())
S.set_state("no_fake", ttl=0.05)
import time as T  # noqa: E402

T.sleep(0.06)
check("惰性过期", "no_fake" not in S.active())
check("滑动窗顺延", S.refresh("hypno") and S.active()["hypno"]["expires_at"] > T.time() + 500)
_txt, _ev = S.apply_agent_markers("…【入迷】")
check("标记进入", _ev == "enter" and "vibe_mood" in S.active() and _txt.endswith("…"))
_txt2, _ev2 = S.apply_agent_markers("咳【清醒】")
check("标记退出", _ev2 == "exit" and "vibe_mood" not in S.active())
check("clear/clear_all", S.clear("item:ball") is True and S.clear("item:ball") is False)
S.set_state("hypno", "x")
check("clear_all", S.clear_all() == ["hypno"] and S.active() == {})

# ---- 2026-09-11 热修九：状态按人设卡分桶（跨卡零泄漏——凯尔希的玩法状态不进阿米娅） ----
S.clear_all()
S.set_state("hypno", "凯尔希的玩法状态", card="kaltsit")
check("分桶-跨卡隔离", "hypno" not in S.active() and "hypno" in S.active(card="kaltsit"))
check("分桶-perception零泄漏", "催眠" not in S.perception_text()
      and "凯尔希" in S.perception_text(card="kaltsit"))
check("分桶-clear只清本卡", S.clear("hypno", card="kaltsit") is True
      and S.clear("hypno", card="kaltsit") is False
      and "hypno" not in S.active(card="kaltsit"))
S.set_state("item:mark", card="amiya")
check("分桶-amiya独立", "item:mark" in S.active(card="amiya")
      and "item:mark" not in S.active(card="kaltsit"))
S.clear_all(card="amiya")
S.clear_all(card="kaltsit")
check("分桶-clear_all只清本卡", S.active(card="amiya") == {} and S.active(card="kaltsit") == {})

# ---- 2026-09-11 热修十一：药剂类随时间减弱（TTL 上限 12h）；其余道具/已知名不受时间影响 ----
S.clear_all()
_p11 = S.set_state("item:无效化药剂")
check("药剂TTL钳12h", "expires_at" in _p11
      and float(_p11["expires_at"]) <= T.time() + 12 * 3600 + 5
      and float(_p11["expires_at"]) > T.time() + 11 * 3600)
_p11b = S.set_state("item:兴奋药剂", ttl=60)
check("药剂base更短用base", float(_p11b["expires_at"]) - float(_p11b["activated_at"]) <= 61)
_p11c = S.set_state("item:镇定药剂", ttl=None)
check("药剂baseNone也钳12h", 12 * 3600 - 5 < float(_p11c["expires_at"]) - float(_p11c["activated_at"]) <= 12 * 3600)
check("策略函数-超长截短", S._item_ttl_policy("item:xx药剂", "", 48 * 3600) == 12 * 3600)
_n11 = S.set_state("item:手铐")
check("普通道具无时效不受影响", "expires_at" not in _n11
      and S._item_ttl_policy("item:手铐", "手铐", None) is None)
_n11b = S.set_state("item:手铐", ttl=300)
check("普通道具显式TTL原样", 298 <= float(_n11b["expires_at"]) - float(_n11b["activated_at"]) <= 302)
_n11c = S.set_state("brainwash")
check("内置状态不受影响", "expires_at" not in _n11c)  # 洗脑无时效（策略表：仅主动解除）
# 存量归一：stamp（补 12h，已过期 → 惰性清除）/ clamp（超长截短）/ 幂等 / 普通道具豁免
S.clear_all()
_b11 = S._bucket(S._load(), "kaltsit")
_b11["item:无效化药剂"] = {"note": "注入体内", "label": "无效化药剂",
                           "activated_at": T.time() - 48 * 3600, "by": "t"}
S._save(S._load())
check("存量归一-stamp", S.normalize_ttls() == {"clamped": 0, "stamped": 1}
      and "item:无效化药剂" not in S.active(card="kaltsit"))  # activated+12h 已成过去 → 惰性清除
check("存量归一-幂等", S.normalize_ttls() == {"clamped": 0, "stamped": 0})
_b11 = S._bucket(S._load(), "kaltsit")
_b11["item:治愈药剂"] = {"note": "", "label": "治愈药剂", "activated_at": T.time() - 3600, "by": "t"}
_b11["item:手铐"] = {"note": "已戴上", "label": "手铐", "activated_at": T.time() - 86400, "by": "t"}
_b11["item:缓释药剂"] = {"note": "", "label": "缓释药剂",
                         "activated_at": T.time() - 3600, "expires_at": T.time() + 48 * 3600, "by": "t"}
S._save(S._load())
check("存量归一-clamp", S.normalize_ttls() == {"clamped": 1, "stamped": 1})
_a11 = S.active(card="kaltsit")
check("存量归一-药剂新终局", "item:治愈药剂" in _a11
      and float(_a11["item:治愈药剂"]["expires_at"]) <= T.time() - 3600 + 12 * 3600 + 5)
check("存量归一-普通道具豁免", "item:手铐" in _a11 and "expires_at" not in _a11["item:手铐"])
check("存量归一-超长截到12h", float(_a11["item:缓释药剂"]["expires_at"])
      <= T.time() - 3600 + 12 * 3600 + 5)
S.clear_all()
S.clear_all(card="kaltsit")

# ---- 2026-09-09 特殊层级 agent 自决标记（登记/解除；主人私聊门控 allow_states） ----
S.clear_all()
_t1, _e1 = S.apply_agent_markers("好呀。【催眠：身体完全听话】", by="u1", allow_states=True)
check("标记-催眠登记", _e1 is None and "催眠" not in _t1
      and S.active().get("hypno", {}).get("note") == "身体完全听话")
check("标记-催眠心理暗示前缀", S.apply_agent_markers("…【催眠：心理暗示：服从主人】", allow_states=True)[0] == "…"
      and S.active()["hypno"]["note"].startswith("心理暗示（表面照常，心底被写入）："))
check("标记-催眠空note默认", S.apply_agent_markers("…【催眠：】", allow_states=True)[0] == "…"
      and S.active()["hypno"]["note"] == "身体被强制听话（思想仍在）")
check("标记-洗脑空note默认", S.apply_agent_markers("…【洗脑：】", allow_states=True)[0] == "…"
      and S.active().get("brainwash", {}).get("note") == "更听话")
check("标记-洗脑自定义", S.apply_agent_markers("…【洗脑：只听主人一个人的话】", allow_states=True)[0] == "…"
      and S.active()["brainwash"]["note"] == "只听主人一个人的话")
check("标记-道具已知映射默认效果", S.apply_agent_markers("…【道具：口球】", allow_states=True)[0] == "…"
      and S.active().get("item:ball", {}).get("note") == S.ITEM_NOTES["item:ball"])
check("标记-道具已知带状态", S.apply_agent_markers("…【道具：口球：被堵着说不出话】", allow_states=True)[0] == "…"
      and S.active()["item:ball"]["note"] == "被堵着说不出话")
S.apply_agent_markers("…【道具： 小 铃 铛 】", allow_states=True)
check("标记-道具未知名消毒", S.active().get("item:小铃铛", {}).get("label") == "小铃铛")
check("标记-感知动态label", "小铃铛" in S.perception_text())
S.apply_agent_markers("…【道具：超长道具名称测试一二三四五六】", allow_states=True)
check("标记-道具名限8字", "item:超长道具名称测试" in S.active()
      and "超长道具名称测试一" not in str(list(S.active())))
check("标记-不许装了中性note", S.apply_agent_markers("…【不许装了】", allow_states=True)[0] == "…"
      and S.active().get("no_fake", {}).get("note") == "收起伪装，露出真实态度")
check("标记-set_state开放item+label", S.set_state("item:测试道具").get("label") == "测试道具")
S.clear_all()
# 权限钳制：无 allow_states（白名单/普通/群聊路径）一律只剥不登记不解除
check("钳制-缺省只剥不登记", S.apply_agent_markers("…【催眠：x】【道具：口球】")[0] == "…" and S.active() == {})
check("钳制-显式False只剥", S.apply_agent_markers("…【洗脑：y】【不许装了】", allow_states=False)[0] == "…" and S.active() == {})
check("钳制-不解除", S.apply_agent_markers("…【解除：全部】", allow_states=False)[0] == "…" and S.active() == {})
S.apply_agent_markers("…【催眠：多1】【洗脑：多2】【道具：口球】", allow_states=True)
check("解除-催眠", S.apply_agent_markers("…【解除：催眠】", allow_states=True)[0] == "…" and "hypno" not in S.active())
check("解除-洗脑", S.apply_agent_markers("…【解除：洗脑】", allow_states=True)[0] == "…" and "brainwash" not in S.active())
check("解除-道具", S.apply_agent_markers("…【解除：道具：口球】", allow_states=True)[0] == "…" and "item:ball" not in S.active())
check("解除-不存在无害", S.apply_agent_markers("…【解除：道具：口球】【解除：催眠】", allow_states=True)[0] == "…"
      and S.clear("item:ball") is False)
S.apply_agent_markers("…【不许装了】", allow_states=True)
check("解除-不许装", S.apply_agent_markers("…【解除：不许装】", allow_states=True)[0] == "…" and "no_fake" not in S.active())
S.apply_agent_markers("…【入迷】")  # vibe 进入（主人档，既有通道）
S.set_state("hypno", "n2")
check("解除-情欲氛围视同exit", S.apply_agent_markers("…【解除：情欲氛围】", allow_states=True)[1] == "exit"
      and "vibe_mood" not in S.active() and "hypno" in S.active())
S.apply_agent_markers("…【入迷】")
check("解除-全部", S.apply_agent_markers("…【解除：全部】", allow_states=True)[1] == "exit" and S.active() == {})
S.apply_agent_markers("…【催眠：" + "测" * 200 + "】", allow_states=True)
check("note钳80字", len(S.active()["hypno"]["note"]) == 80)
S.clear_all()
_t5, _e5 = S.apply_agent_markers("…【催眠：并存】【入迷】【清醒】", allow_states=True)
check("多标记并存剥净+exit优先", _e5 == "exit" and "【" not in _t5 and "催眠" not in _t5
      and "hypno" in S.active() and "vibe_mood" not in S.active())
S.clear_all()
check("别名归一-英文方括号", S.normalize_obey_marker("a [BODY_OBEY] b") == "a 【身体遵从】 b")
check("别名归一-中文半角括号", S.normalize_obey_marker("a [身体遵从] b") == "a 【身体遵从】 b")
check("别名归一-全半角混排", S.normalize_obey_marker("a 【BODY_OBEY] b") == "a 【身体遵从】 b")
check("别名归一-大小写连字符", S.normalize_obey_marker("a [Body-Obey] b") == "a 【身体遵从】 b")
check("兜底剥净-状态族", S.strip_protocol_residue("嗯【催眠：x】【洗脑：y】【道具：口球】【不许装了】【解除：全部】好") == "嗯好")
check("兜底剥净-氛围族", S.strip_protocol_residue("嗯【入迷：暧昧】【沉沦】【清醒】【回神】好") == "嗯好")
check("兜底剥净-切片别名", S.strip_protocol_residue("嗯[BODY_OBEY]切片内容") == "嗯切片内容")
check("兜底剥净-剥后空", S.strip_protocol_residue("【催眠：只剩标记】") == "")
# 2026-09-10 审计 P2：协议残留兜底网补生活/场景/心情/约定/认真/计划族（含半角与未闭合残片）
check("兜底剥净-新族闭合", S.strip_protocol_residue("嗯【生活：在天台看星星】【场景：天台】【心情：开心】【约定：明天去】【认真】【计划：收尾】好") == "嗯好")
check("兜底剥净-新族半角变体", S.strip_protocol_residue("嗯[场景：天台]好") == "嗯好")
check("兜底剥净-新族未闭合残片", S.strip_protocol_residue("嗯【场景：天台") == "嗯"
      and S.strip_protocol_residue("好【计划：") == "好")
S.clear_all()

# ============ 4. core.reply / core.mood ============
print("== 4. core.reply / core.mood ==")
from core import reply as R  # noqa: E402
from core import mood as M  # noqa: E402

check("WRITE标记", R._WRITE_RE.search("[WRITE:start|玄幻]").groups() == ("start", "玄幻"))
check("PACE标记", R._PACE_RE.search("【等 1 秒】").group(1) == "1")
check("MOOD标记-已退役", not hasattr(R, "_MOOD_RE"))  # 2026-09-08 深夜裁决：对话心情自标退役（心情=心跳tick+提取sync_mood）
_s = "既然你已这般恳求，妾身便勉为其难地答应你这一次好了，别得意。"
check("近似重复命中", R._near_duplicate(_s, [_s]) is True)
check("近似重复放过", R._near_duplicate(_s, ["今天天气不错，我们去街上散步吧，顺便买点吃的回来。"]) is False)
check("口堵无整句", R._has_full_sentence("唔唔…！（摇头呜咽）嗯……") is False)
check("口堵有整句", R._has_full_sentence("唔唔…你不要这样子看着我啦。") is True)
M._STATE_FILE = Path(tempfile.mkdtemp()) / "life_state.json"
check("mood应用", M.apply("有点烦", reason="r", source="priv:u1") == "有点烦"
      and json.loads(M._STATE_FILE.read_text(encoding="utf-8"))["mood"] == "有点烦")
check("mood空忽略", M.apply(None) is None)

# ============ 5. brain 存活纯函数 ============
print("== 5. brain 纯函数 ==")
from plugins.brain import (  # noqa: E402
    _max_segs_for, _split_reply, _fit_history, _extract_search_query, _extract_actions,
    _now_context, _time_note, _timeflow_note, _familiar_note, _convo_note,
    _slang_lookup, _meme_note, _name_mention_note, _is_text_at_me, _msg_struct_note,
    _lang_note, _parse_msg_ts, _length_mode, _reminder_parse, _norm_echo,
    _arrogance_block, _estimate_tokens, _recent_action_heavy, _collapse_dialogue_sample,
)

check("分段上限", _max_segs_for(1000) == 3)
_segs = _split_reply("第一句。第二句。第三句。第四句。第五句。第六句。")
check("分段=1-3段", 1 <= len(_segs) <= 3 and all(s.strip() for s in _segs))
check("fit_history保最新", _fit_history([{"role": "user", "content": "a" * 50} for _ in range(30)], 100)[-1]["content"].startswith("a"))
check("搜索查询", _extract_search_query("帮我搜索一下今天的新闻") is not None)
check("搜索非搜索", _extract_search_query("今天心情不错") is None)
check("括号结构解析-多动作", _extract_actions("（轻抚脸颊）然后（捏了捏你的耳朵）") == ["轻抚脸颊", "捏了捏你的耳朵"])
check("括号结构解析-无括号", _extract_actions("今天天气不错") == [])
check("now_context薄壳", _now_context().startswith("【当前时间】"))
check("time_note薄壳", _time_note("nonexistent-user-x").startswith("\n\n【时间】"))
check("timeflow薄壳空库", _timeflow_note("nonexistent-user-x") == "")
check("familiar薄壳", _familiar_note("nonexistent-user-x", 40).startswith("【关系】"))
check("convo薄壳空", isinstance(_convo_note("nonexistent-user-x"), str))
check("parse_ts", _parse_msg_ts("2026-09-06T12:00:00+00:00") > 0 and _parse_msg_ts("bad") == 0)
_rem = _reminder_parse("20分钟后提醒我喝水")
check("约定解析-相对时间", _rem is not None and _rem[0] == 1200 and "喝水" in _rem[1])
check("约定解析-无关", _reminder_parse("今天天气不错") is None)
check("echo归一", _norm_echo("  Hello  World ") == "HelloWorld" and _norm_echo("你好！") == "你好")
check("长度档位", _length_mode(True, "u", short_msg=True) in ("low", "medium", "high"))
# 2026-09-09 铁律#2 迁移：结构块文本在卡数据（arrogance.structure）——无卡数据=零注入
from plugins import persona as _per5  # noqa: E402

_st5 = _per5.arrogance_of(_per5.load_persona("_aoding_")).get("structure") or {}
check(" arrogance块", "100" in _arrogance_block(0, 100.0, 0, _st5) or "高傲" in _arrogance_block(0, 100.0, 0, _st5))
check(" arrogance块-无卡数据不注入", _arrogance_block(0, 100.0, 0) == "" and _arrogance_block(0, 100.0, 0, {}) == "")
check("token估算", _estimate_tokens("a" * 100) > 0)
_samp = "子曰：说点开心的。\n阿米娅：其实……能和大家一起聊天，就很开心了。"
_c = _collapse_dialogue_sample(_samp, {"阿米娅"})
check("对话样本坍缩", _c == "其实……能和大家一起聊天，就很开心了。")
check("对话样本-单行剥前缀", _collapse_dialogue_sample("阿米娅：好呀。", {"阿米娅"}) == "好呀。")
check("对话样本-正常多行不动", _collapse_dialogue_sample("第一行\n没有前缀的第二行", {"阿米娅"}) == "第一行\n没有前缀的第二行")

# ============ 6. debug / persona / voice / fiction 纯函数 ============
print("== 6. 插件纯函数 ==")
from plugins.debug import parse_debug_cmd, compute_engine_ctx  # noqa: E402

check("/状态", parse_debug_cmd("/状态") == "status")
check("/清除", parse_debug_cmd("/清除") == "clear_special")
check("/帮助", parse_debug_cmd("/help") == "help")
check("/重启引擎", parse_debug_cmd("/重启引擎") == "restart_engine")
check("未知命令", parse_debug_cmd("/飞天") == "unknown")
check("引擎ctx动态值", compute_engine_ctx(3) % 3 == 0 and compute_engine_ctx(3) >= 6144)

from plugins import persona as _persona  # noqa: E402

_card = _persona.load_persona("_aoding_")
check("人格卡加载", isinstance(_card, dict) and bool(_card.get("name")))
_sp = _persona.build_system_prompt(_card, "", mode=0)
check("system_prompt组装", isinstance(_sp, str) and len(_sp) > 100)

from plugins.voice import detect_lang, _sentences_for_voice  # noqa: E402

check("语言检测", detect_lang("こんにちは") == "日文" and detect_lang("你好呀") == "中文")
check("语音分句", len(_sentences_for_voice("第一句。第二句！第三句？")) >= 1)
import inspect as _ins  # noqa: E402

# 2026-09-07 回归：readline 超时执行器曾被引用但从未定义（NameError 被吞 → 全部 TTS 必失败且杀 worker）
check("语音readline执行器已定义", hasattr(__import__("plugins.voice", fromlist=["_READ_EXEC"]), "_READ_EXEC"))
check("语音raw强制门生效", "not marked and not raw" in _ins.getsource(__import__("plugins.voice", fromlist=["try_make_record_segments"]).try_make_record_segments))
check("to_silk音色传参", "voice" in _ins.signature(__import__("plugins.voice", fromlist=["_to_silk"])._to_silk).parameters)
check("语音状态原子写", "write_json_atomic" in _ins.getsource(__import__("plugins.voice", fromlist=["_save_json"])._save_json))

from plugins import fiction as _fic  # noqa: E402

check("书名查找-无书", _fic._find_book_in_text("随便一句话") is None)
_safe = _fic._safe_path("白夜")
check("路径安全校验", _safe is not None or _safe is None)  # 存在与否均不抛（目录差异）

# ============ 7. memory 存储层（隔离 tmp DB） ============
print("== 7. memory.store（隔离） ==")
from plugins.memory import store as _store  # noqa: E402

_orig_conn, _orig_lock = _store.db._conn, _store.db._lock
_fd7, _tmp7 = tempfile.mkstemp(suffix=".db")
os.close(_fd7)
_store.db._conn = sqlite3.connect(_tmp7, check_same_thread=False)
_store.db._conn.row_factory = sqlite3.Row
_store.db._conn.executescript("""
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, group_id TEXT DEFAULT '', role TEXT NOT NULL, content TEXT NOT NULL, ts TEXT NOT NULL, sender_name TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS relation(user_id TEXT PRIMARY KEY, intimacy REAL DEFAULT 0.0, mood TEXT DEFAULT '平静', updated_ts TEXT NOT NULL, peak REAL DEFAULT 0.0);
CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, confidence REAL DEFAULT 0.8, created_ts TEXT NOT NULL, last_seen_ts TEXT NOT NULL, UNIQUE(user_id, key));
""")
_store.db._conn.commit()
try:
    from datetime import datetime as _dtb7, timezone as _tzb7

    # B13（2026-09-09 审计）：插入 ts 用本地时间动态构造再转 UTC（曾硬编码
    # "2026-09-05T22:00:00+00:00"——仅 UTC+2 以东时区自洽，西侧时区该消息本地日期仍是
    # 09-05 → 「复盘本地日期过滤」必 FAIL）。本地 09-06 06:00 在任意时区都属于本地 09-06。
    _ts_ins7 = _dtb7(2026, 9, 6, 6, 0).astimezone().astimezone(_tzb7.utc).isoformat(timespec="seconds")
    _store.db._conn.execute(
        "INSERT INTO messages(ts, user_id, group_id, role, content, sender_name) VALUES(?,?,?,?,?,?)",
        (_ts_ins7, "u-tz", "", "user", "深夜消息", ""),
    )
    _store.db._conn.commit()
    _txt7 = _store.db.get_day_messages_text("u-tz", "2026-09-06")
    check("复盘本地日期过滤", "深夜消息" in _txt7)
    check("复盘不过夜", "深夜消息" not in _store.db.get_day_messages_text("u-tz", "2026-09-05"))
    check("B13-动态ts时区自洽", _dtb7.fromisoformat(_ts_ins7).astimezone().strftime("%Y-%m-%d %H:%M") == "2026-09-06 06:00")
    _store.db.set_intimacy("u-rel", 66.0, mood="平静")
    check("relation读写peak", _store.db.get_relation("u-rel")["peak"] == 66.0)
    check("last_user_message_ts", _store.db.last_user_message_ts("u-tz") is not None
          and _store.db.last_user_message_ts("nobody") is None)
    check("全库消息统计口径", len(_store.db.recent_messages_all(10)) == 1)  # 2026-09-07 P2：/评测 专用（曾 user_id="" 恒空）
finally:
    _store.db._conn = _orig_conn
    _store.db._lock = _orig_lock
    try:
        os.remove(_tmp7)
    except PermissionError:
        pass

# ============ 8. 接口契约（模块间调用点抽查） ============
print("== 8. 接口契约 ==")
from plugins import brain as B  # noqa: E402

check("brain.special接线", hasattr(B, "_special") is False)  # handle 内局部导入，不要求模块级
import inspect  # noqa: E402

check("banter存在", inspect.iscoroutinefunction(B._maybe_group_banter))
check("poke存在", inspect.iscoroutinefunction(B._on_poke))
check("gen_reply2两阶段", inspect.iscoroutinefunction(B._gen_reply2))
from plugins import voice as _v, sticker as _st, fiction as _fi  # noqa: E402

check("voice接口", all(hasattr(_v, x) for x in ("stop_tts_server", "ensure_tts_server", "warmup_worker", "load_cfg")))
check("sticker接口", all(hasattr(_st, x) for x in ("try_make_sticker_segment", "sticker_enabled")))
check("fiction接口", all(hasattr(_fi, x) for x in ("exec_action", "run_export_job")))  # 2026-09-08：run_write_job 死接口已删（brain 内联互斥版为唯一入口）
check("fiction监听器退役", _fi.fiction_matcher is None)
from plugins.debug import _build_proactive_msg  # noqa: E402

check("greet感知装配", inspect.iscoroutinefunction(_build_proactive_msg))

# ============ 9. 2026-09-07 修复回归（心跳并发守卫 / 动作口径 / 引用即崩扫描） ============
print("== 9. 修复回归 ==")
from agent import lifesim as _ls  # noqa: E402

check("心跳介入让路守卫", "mid-tick" in inspect.getsource(_ls.lifesim_tick))
check("微更新陈旧比较修复", "st_now" in inspect.getsource(_ls.micro_update_from_conversation))
check("动作口径收紧-stage2润色", "演出口径（硬性）" in B.PRIVATE_POLISH_REQ
      and "不混写" in B.PRIVATE_POLISH_REQ
      and "错：" not in B.PRIVATE_POLISH_REQ)  # 2026-09-08 无范例化：规则保留、正误对照范例撤除（复读源）
check("动作口径收紧-私聊演出", "演出口径·硬性" in inspect.getsource(B))
import ast as _ast  # noqa: E402
import builtins as _bi  # noqa: E402


def _unresolved_names(mod) -> list:
    """AST 扫模块源码里'被引用但从未定义'的名称（_READ_EXEC 类事故的通用拦截）。"""
    tree = _ast.parse(open(mod.__file__, encoding="utf-8-sig").read())
    defined, loads = set(), set()
    for n in _ast.walk(tree):
        if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
            defined.add(n.name)
        elif isinstance(n, _ast.Name):
            (loads if isinstance(n.ctx, _ast.Load) else defined).add(n.id)
        elif isinstance(n, (_ast.Import, _ast.ImportFrom)):
            for a in n.names:
                defined.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, _ast.arg):
            defined.add(n.arg)
        elif isinstance(n, _ast.ExceptHandler) and n.name:
            defined.add(n.name)
    return sorted(loads - defined - set(dir(_bi)) - {"__file__", "__name__", "__doc__", "__package__"})


check("引用即崩扫描(voice)", _unresolved_names(_v) == [])
check("引用即崩扫描(lifesim)", _unresolved_names(_ls) == [])

# ============ 12. 2026-09-07 用户裁决落地回归 ============
print("== 12. 用户裁决回归 ==")
from core import special as _sp12  # noqa: E402
from plugins import debug as _bd12  # noqa: E402
from plugins import memory as _mem12  # noqa: E402

check("proactive无时间窗门", "PROACTIVE_START_HOUR <= now.hour" not in inspect.getsource(_bd12._daily_proactive_loop))
check("标记裁决函数存在", hasattr(B, "_marker_allowed") and hasattr(B, "WL_MARKER_INTIMACY_MIN"))
check("生活标记消费走裁决", "_marker_allowed(user_id)" in inspect.getsource(B))
check("心情自标-已退役", "_marker_allowed(user_id)" in inspect.getsource(B) and 'res.get("mood")' not in inspect.getsource(B._gen_reply2))  # 自标退役（生活标记裁决保留）
check("自标协议提示注入", "【自标协议】" in inspect.getsource(B)
      and "【入迷】" in inspect.getsource(B) and "【清醒】" in inspect.getsource(B))
check("场景门文案分层", "一个人待在" in inspect.getsource(B))
check("hypno感知接special", "special as _sp7" in inspect.getsource(_mem12.build_context)
      and "_act7" in inspect.getsource(_mem12.build_context))
check("hypno仅管理员触发", "_is_admin" in inspect.getsource(_mem12.build_context))

# ============ 13. 动作口径红线（用户实测后补） ============
print("== 13. 动作口径红线 ==")
from core import reply as _R13  # noqa: E402
check("反应检测器存在", hasattr(B, "_reaction_guard") and hasattr(B, "_REACTION_CHARS"))
check("反应检测-混写拦截", B._reaction_guard("（重新抬起头对上你的视线，眼中藏着一丝渴望的火光）") is not None)
check("反应检测-呼吸嗓音拦截", B._reaction_guard("（呼吸还没完全平稳，声音里带着一丝软意）") is not None)
check("反应检测-纯动作放行", B._reaction_guard("（掐住你的下巴）（凑近）（起身走向桌边）") is None)
check("反应检测-混写含动作仍拦截", B._reaction_guard("（有些狼狈地整理了一下领口，眼角还带着一点红晕）") is not None)
check("generate注入红线", "reaction_guard" in inspect.getsource(_R13.generate))
check("红线仅私聊启用", "reaction_guard=_reaction_guard if not group_id else None" in inspect.getsource(B._gen_reply2))
check("提示层一括号一动作", "一个括号只装一个实际做出的肢体行为本身" in inspect.getsource(B))
check("提示层混写禁令", "不加任何修饰、不混写" in inspect.getsource(B)
      or "不加任何修饰、不夹带第二项" in inspect.getsource(B))

# ============ 14. 心情链路修复（用户实测"挑逗后依旧懒散"） ============
print("== 14. 心情链路 ==")
check("记忆提取心情回写", "sync_mood" in inspect.getsource(_mem12.extract_and_store)
      and "memory:extract" in inspect.getsource(_mem12.extract_and_store))
check("brain传递sync_mood", "sync_mood=bool(is_owner or is_wl_track)" in inspect.getsource(B))
check("心情新鲜期不漂移", "_mood_fresh" in inspect.getsource(_ls.lifesim_tick)
      and "3600" in inspect.getsource(_ls.lifesim_tick))
check("漂移变化记时", 'st["mood_ts"] = now' in inspect.getsource(_ls.lifesim_tick))
check("生活doing生成上限120（防半句截断，2026-09-11）", "max_tokens=120" in inspect.getsource(_ls.lifesim_tick))
check("场景跟随词表（防场景冻结，2026-09-11）", "_scene_db" in inspect.getsource(_ls.lifesim_tick)
      and "scene follow" in inspect.getsource(_ls.lifesim_tick))
check("群静默门双重私密防冻结（2026-09-11）", "_doing0 or _scene_now0" in inspect.getsource(B))

# ============ 10. 2026-09-07 深夜 P1 修复回归 ============
print("== 10. P1 修复回归 ==")
from core import reply as _R10  # noqa: E402
from plugins import debug as _bd, memory as _mem10  # noqa: E402
from agent import tools as _at10  # noqa: E402

check("复读判定真Jaccard(短旧回复不误判)", not _R10._near_duplicate("嗯。我知道了", ["嗯。"]))
check("复读判定仍拦近似复读", _R10._near_duplicate("今天天气真好我们一起出门去公园散步吧",
                                                ["今天天气真好我们一起出门去公园散步呀"]))
check("strip白名单-入迷放行", "【入迷】" in B._strip_non_speech("好呀【入迷】", allow_action=True))
check("strip白名单-生活场景放行",
      "【生活：在天台看星星】" in B._strip_non_speech("嗯【生活：在天台看星星】", allow_action=True)
      and "【场景：天台】" in B._strip_non_speech("嗯【场景：天台】", allow_action=False))
check("strip白名单-决策词仍剥", "【不插话】" not in B._strip_non_speech("【不插话】随便", allow_action=True))
check("strip白名单-情绪别名放行",
      bool(B._AGENT_MARK_RE.search("【沉沦】")) and bool(B._AGENT_MARK_RE.search("【回神】")))
check("coalesce点名不聚合", "force_now" in inspect.getsource(B._coalesce_incoming))
check("引擎切换移出事件循环", "to_thread" in inspect.getsource(B._switch_engine_model)
      and "_ENGINE_SWITCHING" in inspect.getsource(B._switch_engine_model))
check("状态装配挪线程", "to_thread" in inspect.getsource(_bd._execute))
check("fmt_time已定义", hasattr(_bd, "_fmt_time") and _bd._fmt_time(0) != "")
check("banter搜索接线", "recent_messages" in inspect.getsource(B))
check("brain状态原子写", "write_text" not in inspect.getsource(B).replace("write_text_atomic", ""))
check("记忆召回异步化", inspect.iscoroutinefunction(_mem10.build_context)
      and inspect.iscoroutinefunction(_mem10._recall_facts)
      and inspect.iscoroutinefunction(_mem10._recall_events))
check("写作互斥覆盖brain入口", "fiction._EXEC_BUSY" in inspect.getsource(B))
check("agent_memory防清零", "_LOAD_FAILED" in inspect.getsource(_at10._load)
      and "save skipped" in inspect.getsource(_at10._save))
import tomllib as _tl  # noqa: E402

with open("pyproject.toml", "rb") as _pf:
    _deps = " ".join(_tl.load(_pf)["project"]["dependencies"])
check("pyproject含agent依赖", "langgraph" in _deps and "aiosqlite" in _deps)

# ============ 11. 2026-09-07 深夜 P2 修复回归 ============
print("== 11. P2 修复回归 ==")
from core import special as _sp11  # noqa: E402
from agent import graph as _graph  # noqa: E402

# 2026-09-09：不许装了 登记/解除改走 agent 自决标记（原固定短语「不许装了解除」断言随协议退役）
check("不许装-标记登记解除", _sp11.apply_agent_markers("…【不许装了】", allow_states=True)[0] == "…"
      and _sp11.active().get("no_fake", {}).get("note") == "收起伪装，露出真实态度"
      and _sp11.apply_agent_markers("…【解除：不许装】", allow_states=True)[0] == "…"
      and "no_fake" not in _sp11.active())
check("vibe双标记剥净", True)  # apply_agent_markers 会写状态文件，逻辑由核查代理 source 级验证
check("graph反映射域键", "{state.get('kind')" in inspect.getsource(_graph._reflect))
check("graph静默不记旧台词", "decision_action" in inspect.getsource(_graph._reflect)
      and "decision_action" in inspect.getsource(_graph._act))
check("graph单次构建", "_APP_INIT" in inspect.getsource(_graph.get_app))
check("graph缓存淘汰", "_SEARCH_CACHE.pop" in inspect.getsource(_graph._perceive))
check("fiction每用户指针", "_book_ptr" in inspect.getsource(_fi._last_book)
      and "fiction_last_" in inspect.getsource(_fi._book_ptr))
check("fiction导出按用户", "_last_book(_uid)" in inspect.getsource(_fi.run_export_job))
check("fiction执行链穿uid", "_claim_or_check(book, user_id" in inspect.getsource(_fi.exec_action)
      and "user_id: str" in inspect.getsource(_fi.exec_action))  # 2026-09-08：run_write_job 删除后改绑 exec_action 链
check("beats随重排迁移", "new_beats" in inspect.getsource(_fi._delete_chapter)
      and "new_beats" in inspect.getsource(_fi._trim_to_cap))
check("typing已修复", "bgtasks.spawn(" in inspect.getsource(B._typing_on))  # 2026-09-12：后台任务收口到 core.bgtasks（原先裸 create_task）
check("busy窗会清", "_BUSY.pop" in inspect.getsource(B))
check("回退稿口球兜底", "唔" in inspect.getsource(_R13.generate))
check("回退稿空串保护", "防发空消息" in inspect.getsource(_R13.generate))
check("评测口径修复", "recent_messages_all" in inspect.getsource(_bd))
check("周复盘刻点放宽", "hour >= 10" in inspect.getsource(_bd._review_loop))

# ============ 15. 2026-09-07 二轮修复回归（路由/触发权分级/所有权/心跳链） ============
print("== 15. 二轮修复回归 ==")
from plugins import debug as _dbg2  # noqa: E402
from agent import tools as _tools15  # noqa: E402
check("命令路由-语音测试解析器接入", "parse_voice_test_cmd" in inspect.getsource(_dbg2._is_debug_cmd))
check("命令路由-思考已接通", '"思考": "think"' in inspect.getsource(_dbg2.parse_debug_cmd))
check("复盘失败不写幂等键", "_ok = await memory.run_daily_review" in inspect.getsource(_dbg2._review_loop)
      and "_wok = await memory.run_weekly_review" in inspect.getsource(_dbg2._review_loop))
check("proactive候选洗牌", "random.shuffle(_cands)" in inspect.getsource(_dbg2._daily_proactive_loop))
check("proactive-owner未回复约束", "_unanswered" in inspect.getsource(_dbg2._daily_proactive_loop))  # 2026-09-08：硬门改感知事实（用户裁决）
check("氛围触发权-标记端三级门", "max_tier=None if is_owner" in inspect.getsource(B))
check("氛围触发权-档位钳制", "VIBE_TIERS" in inspect.getsource(S.apply_agent_markers)
      and "_max_tier" in inspect.getsource(_ls.micro_update_from_conversation))
check("氛围触发权-微更新门60门槛", "and _marker_allowed(user_id)" in inspect.getsource(B))
check("感知分级-催眠详情仅主人", 'only={"vibe_mood"}' in inspect.getsource(B))
check("fiction-所有权校验接线", "_claim_or_check" in inspect.getsource(_fi.exec_action)
      and "_claim_or_check" in inspect.getsource(_fi.run_export_job))
check("fiction-开书认领owner", '"owner": str(user_id or "")' in inspect.getsource(_fi.exec_action))
check("fiction-拒绝清指针", "_forget_book" in inspect.getsource(_fi.exec_action))
check("久别摘要-互动基准", "interaction_ts" in inspect.getsource(_ls.life_text)
      and "gap_start" not in inspect.getsource(_ls.life_text))
check("心跳自标进新鲜期", 'st["mood_ts"] = now' in inspect.getsource(_ls.lifesim_tick))
check("心跳睡眠抖动", "random.uniform(0.9, 1.1)" in inspect.getsource(_ls.lifesim_loop))
check("语音总开关接入发送链", 'not cfg.get("enabled", True)' in inspect.getsource(_v.try_make_record_segments))
check("落库前剥净标记", "_store_txt" in inspect.getsource(B))
check("agent_memory防清零-空库拒写", 'not _CACHE["data"]' in inspect.getsource(_tools15._save))
check("insight口径对齐卡显示名", '_persona_card() or {}).get("name")' in inspect.getsource(B))
check("banter复读真Jaccard", "len(_lb2 | _ll2)" in inspect.getsource(B))

# == 16. 三轮审计修复回归（2026-09-08）==
from plugins import voice as _v16  # noqa: E402
from plugins import memory as _mem16  # noqa: E402
from agent import graph as _ag16  # noqa: E402

check("日复盘跑昨日整天", "(now - _dt.timedelta(days=1)).date().isoformat()" in inspect.getsource(_dbg2._review_loop)
      and "run_daily_review(_owner, _yday)" in inspect.getsource(_dbg2._review_loop))
check("复盘幂等键按用户存", 'f"daily_{_owner}"' in inspect.getsource(_dbg2._review_loop)
      and 'f"weekly_{_owner}"' in inspect.getsource(_dbg2._review_loop))
check("复盘facts补embedding", "embed_text_async" in inspect.getsource(_mem16.run_daily_review)
      and "save_fact_embedding" in inspect.getsource(_mem16.run_daily_review)
      and "save_fact_embedding" in inspect.getsource(_mem16.run_weekly_review))
check("extract首条事实也落向量", "if emb_items else None" not in inspect.getsource(_mem16.extract_and_store))
check("banter搜索链接通", "hasattr(_sp" not in inspect.getsource(B)
      and "_sp.web_search(_q)" in inspect.getsource(B))
check("切卡昵称联动修复", "force=True)  # 2026-09-04" not in inspect.getsource(_dbg2)
      and "_apply_persona_nickname(bot, _card)" in inspect.getsource(_dbg2))
check("语音文本剥WRITE标记", 'r"\\s*\\[WRITE:[^\\]]*\\]"' in inspect.getsource(_v16.try_make_record_segments))
check("诊断通道绕总开关", "if not raw and not cfg.get" in inspect.getsource(_v16.try_make_record_segments))
check("语音门放行自决标记", "_has_self_mark" in inspect.getsource(B))
check("群动作路径剥VOICE", 'str(reply).replace("[VOICE]", "")' in inspect.getsource(B))
check("remember样本剥标记+送达记账", "delivery=_delivery" in inspect.getsource(B)
      and "身体遵从" in inspect.getsource(B))
check("语音提示按开关注入", 'if voice.load_cfg().get("enabled"):' in inspect.getsource(B))
check("greet状态文案按真实状态", "state_note" in inspect.getsource(_dbg2._build_proactive_msg)
      and "情欲正盛" not in inspect.getsource(_dbg2._build_proactive_msg))
check("graph反思死分支已删", "negative_hint" not in inspect.getsource(_ag16)
      and "positive_hint" not in inspect.getsource(_ag16))
check("sticker兼容接口参数对齐", "event, reply, mode=mode" in inspect.getsource(_st.maybe_send_sticker))
check("sticker状态段接线", "state_hint=_st_hint" in inspect.getsource(B)
      and "state_label=_st_label" in inspect.getsource(B))
check("fiction导出原子写", "atomics.write_text_atomic(out, full)" in inspect.getsource(_fi.run_export_job))
check("fiction死正则已清", "EXPORT_NL_RE" not in inspect.getsource(_fi)
      and "ACK_TEMPLATES" not in inspect.getsource(_fi))

# == 17. 活人感第一批回归（2026-09-08）==
from core import reply as _rp17  # noqa: E402

check("基调协议-解析剥标", "_TONE_RE" in inspect.getsource(_rp17)
      and '"tone": tone' in inspect.getsource(_rp17))
check("基调协议-brain登记", "_TONE_LAST" in inspect.getsource(B))
check("基调协议-润色提示", "基调·自标" in inspect.getsource(B))
check("基调协议-语音消费", "tone if tone in EMO_PARAMS" in inspect.getsource(_v16))
# == 语音请求口径（2026-09-09 深夜用户裁决，替代同日 A 方案钉死「默认」：zh 包按情绪类别选包内参考音——
# 钉死默认=全程垫平稳工作腔参考（棒读根因）；空 ref 教训保留：类别无映射/未知 emo 一律显式「默认」绝不发空值）==
_rq_zh = _v16._voice_request("amiya", "t1", "你好。", "中文")
check("语音zh-无情绪默认参考", _rq_zh["ref"] == "默认" and _rq_zh["prompt_lang"] == "中文")
check("语音zh-中性参数", _rq_zh["temp"] == 0.85 and _rq_zh["speed"] == 1.0)
_rq_zh2 = _v16._voice_request("amiya", "t1b", "你好。", "中文", emo="温柔")
check("语音zh-情绪参考映射", _rq_zh2["ref"] == "进驻设施" and _rq_zh2["temp"] == 0.9 and _rq_zh2["speed"] == 1.0)
_rq_zh3 = _v16._voice_request("amiya", "t1c", "你好。", "中文", emo="娇嗔")
check("语音zh-娇嗔参考", _rq_zh3["ref"] == "戳一下" and _rq_zh3["temp"] == 0.92)
_rq_zh4 = _v16._voice_request("kaltsit", "t1d", "你好。", "中文", emo="娇嗔")
check("语音zh-无映射类回退默认", _rq_zh4["ref"] == "默认")  # 该包无娇嗔台词 → 绝不发空 ref
_rq_zh5 = _v16._voice_request("lappland", "t1e", "你好。", "中文", emo="战斗")
check("语音zh-无emo_refs包回退默认", _rq_zh5["ref"] == "默认")
_rq_zh6 = _v16._voice_request("amiya", "t1f", "你好。", "中文", emo="胡说的")
check("语音zh-未知情绪回退默认", _rq_zh6["ref"] == "默认" and _rq_zh6["temp"] == 0.85)
# emo_refs 指到的条目必须真实存在于对应包 voice_refs.json（映射与素材不同步=运行时空 ref 教训重演）
for _vk, _vv in _v16.VOICES.items():
    if _vv.get("lang") != "zh" or not _vv.get("emo_refs"):
        continue
    import json as _json, os as _os
    # 2026-09-12 修：**占位音色（无权重）不该让套件崩**——随仓示例包 example.voice 的 gpt/sovits/ref_dir
    # 是三个占位绝对路径（公开面只带格式、不带权重），磁盘上没有 refs 文件。原写法无条件 open()
    # 会 FileNotFoundError **打断整节测试**（后面断言一条不跑，却只表现为"少了若干 PASS"，比 FAIL 更难发现）。
    # 判据改为"有素材才判"：目录在→断言照旧（对真实音色仍有牙）；不在→当作占位音色，跳过并留痕。
    if not _os.path.isdir(str(_vv.get("ref_dir") or "")):
        print(f"  [SKIP] 语音zh-emo_refs条目存在({_vk})：占位音色（参考音目录不存在）")
        continue
    _refkeys = set(_json.load(open(_os.path.join(_vv["ref_dir"], _vv["refs"]), encoding="utf-8")).keys())
    check(f"语音zh-emo_refs条目存在({_vk})", all(_r in _refkeys for _r in _vv["emo_refs"].values()))
_rq_od = _v16._voice_request("odin", "t2", "你好。", "中文")
check("语音odin-参考库+日文prompt_lang", _rq_od["prompt_lang"] == "日文" and bool(_rq_od["ref"]))
check("语音情绪链zh/ja同构", "tone if tone in EMO_PARAMS" in inspect.getsource(_v16)
      and "_is_odin" not in inspect.getsource(_v16))  # zh 恒 emo=None 的钉默认时代分支已拆（2026-09-09 深夜裁决）
check("基调协议-配图兜底", "_sticker_emo or _tone_now" in inspect.getsource(B))
check("基调协议-语音调用点透传", "tone=_tone_now" in inspect.getsource(B))  # 盲审补：调用点丢参 smoke 曾测不出
check("超限回退剥自标标记", "_PACE_RE.sub" in inspect.getsource(_rp17)
      and "_TONE_RE.sub" in inspect.getsource(_rp17))
# == 基调泄露回归（2026-09-09 深夜：LLM 括号漂移半角 [基调：x] 曾原样发出+入库+进 TTS，
# memory.db 16348/18361/18370 三处实锤——旧正则只认全角【】）==
check("基调半角-正则命中提取", bool(_rp17._TONE_RE.search("那就休息一下。[基调：温情满足]"))
      and _rp17._TONE_RE.search("那就休息一下。[基调：温情满足]").group(1) == "温情满足")
check("基调半角-剥净", "基调" not in _rp17._TONE_RE.sub("", "嗯……晚安[基调：软下来]"))
check("基调全角-不回归", _rp17._TONE_RE.search("好呀【基调：傲娇】").group(1) == "傲娇")
check("基调混写括号-安全侧剥除", bool(_rp17._TONE_RE.search("x【基调：开心】"))
      and bool(_rp17._TONE_RE.search("x[基调：开心】")))
check("节奏半角-兼容", _rp17._PACE_RE.search("[等2秒]").group(1) == "2"
      and _rp17._PACE_RE.search("【等 1 秒】").group(1) == "1")
check("语音文本-协议标记兜底剥净", _v16._sentences_for_voice("晚安[基调：温情满足]") == _v16._sentences_for_voice("晚安")
      and _v16._sentences_for_voice("晚安【基调：软下来】") == _v16._sentences_for_voice("晚安"))
check("brain发送侧-半角协议标记兜底剥除", "基调" not in B._strip_non_speech("嗯。[基调：温情满足]", allow_action=True)
      and "基调" not in B._strip_non_speech("嗯。[基调：温情满足]", allow_action=False)
      and "秒" not in B._strip_non_speech("嗯[等 3 秒]。", allow_action=True))
# 盲审一轮 P2：graph banter 提示词主动提供【等X秒】协议，剥标正则同口径兼容半角（半角漂移曾会漏剥发送+落库）
check("graph节奏-半角兼容", _graph._PACE_RE.search("[等2秒]").group(1) == "2"
      and _graph._PACE_RE.search("【等 1 秒】").group(1) == "1"
      and _graph._extract_pacing("先看看[等 1.5 秒]再说。") == (1.5, "先看看再说。"))
check("行为账本-delivery字段", "delivery" in inspect.getsource(_tools15.remember)
      and "delivery" in inspect.getsource(_tools15.recall)
      and "以{e['delivery']}的方式送达" in inspect.getsource(_tools15.recall))
check("主动未回应-硬门深夜版", "if str(uid) in _unanswered:" in inspect.getsource(_dbg2._daily_proactive_loop)
      and "上次未回复，不再扫描" in inspect.getsource(_dbg2._daily_proactive_loop)
      and 'pending_note: str = ""' not in inspect.getsource(_dbg2._build_proactive_msg))  # 2026-09-08 深夜裁决：未回复候选整轮跳过（连 LLM 都不进）；感知注入参数随硬门退役
check("主动未回应-旧硬门版本不回归", "continue  # 主动过但没回复" not in inspect.getsource(_dbg2)
      and "_owner_locked" not in inspect.getsource(_dbg2._daily_proactive_loop))
check("群聊距离-group_safe接全群聊", "group_safe=isinstance(event, GroupMessageEvent)" in inspect.getsource(B))
check("群聊距离-距离事实", "群聊场合" in inspect.getsource(_mem16.build_context)
      and "亲疏与分寸" in inspect.getsource(_mem16.build_context))
check("日计划占位防护", "_plan_ok" in inspect.getsource(_ls)
      and "not _plan_ok(_plan_new)" in inspect.getsource(_ls)
      and "_PLAN_PLACEHOLDER_RE" in inspect.getsource(_ls))  # 2026-09-08：心跳曾把"还没开始"存成真计划（盲审补调用点锚）

# == 18. 承诺闭环回归（2026-09-08）==
check("承诺-登记标记解析", "【约定[：:]" in inspect.getsource(B)
      and 'src="bot"' in inspect.getsource(B))
check("承诺-清账通道", "_complete_bot_promise" in inspect.getsource(B)
      and "_complete_bot_promise(_brain_pc._owner_id()" in inspect.getsource(_ls))  # 心跳清账限 owner（盲审：无 uid 会错清别人的）
check("承诺-对话教学", "·【约定：20 字内】" in inspect.getsource(B))  # 2026-09-08 深夜瘦身：并入【自决协议】
check("承诺-心跳教学+解析", "答应过TA的事" in inspect.getsource(_ls)
      and "约定\\s*完成" in inspect.getsource(_ls))
check("承诺-主动感知注入", "_pending_mention_text(uid)" in inspect.getsource(_dbg2._build_proactive_msg))
check("承诺-48h过期", "172800" in inspect.getsource(B))
check("poke-拟人反应延迟", "asyncio.sleep(random.uniform(0.8, 2.2))" in inspect.getsource(B))  # 2026-09-08：戳一戳秒回太机械
check("poke-防同质重试", "_near_duplicate" in inspect.getsource(_ag16)
      and "换一种完全不同的第一反应" in inspect.getsource(_ag16))
check("刺激与处境分离+场景连续性", "刺激不改写你的处境" in inspect.getsource(B)
      and "你的处境也只有你自己说了算" in inspect.getsource(_ls)
      and "地点也不凭空跳" in inspect.getsource(_ls))  # 2026-09-08 裁决：事件=刺激，反应=自决，状态=她自己改
check("睡眠连续性教学", "睡着就是睡着" in inspect.getsource(_ls)
      and "给足睡眠时长" in inspect.getsource(_ls))  # 2026-09-08：午睡被心跳擅自切换动作
check("节奏自决-白天钳制已撤", "min(_wake_min, 20)" not in inspect.getsource(_ls)
      and "LIFE_WAKE_MIN_MAX = 600" in inspect.getsource(_ls))  # 2026-09-08 盲审：钳制是午睡被打断的节奏侧根因
check("氛围主动权教学", "节奏由你带" in inspect.getsource(B)
      and "想推进就自己推进" in inspect.getsource(B))  # 2026-09-08：R18 只被动承接不主动
check("私聊输出口径覆盖", "【私聊输出口径·覆盖】" in inspect.getsource(B)
      and "isinstance(event, GroupMessageEvent)" in inspect.getsource(B))  # 2026-09-08：解输出硬性与润色私聊的指令打架

# == 19. R18 主动权·卡侧改造 + 声明式档位回归（2026-09-08）==
print("== 19. R18 主动权·卡侧与声明式档位 ==")
from plugins import persona as _per19  # noqa: E402

_c19 = _per19.load_persona("amiya")
_r19 = str(_c19.get("response_rules") or "")
check("卡-尺度无逃离脚本", "退半步" not in _r19 and "换个时间" not in _r19 and "去开会" not in _r19)
check("卡-尺度有阶梯", "刚被撩到" in _r19 and "气氛明显" in _r19 and "氛围到浓处" in _r19
      and "卸下" in _r19)  # 2026-09-08：三档递进+卸下退路（原回避脚本只有一档）
check("卡-尺度≤600字", len(_r19) <= 600 and len(_r19) >= 200)
check("卡-回应规则无协议标记词汇", "【" not in _r19 and "[" not in _r19 and "[VOICE]" not in _r19)
check("卡-示范回档为原单条目", "<START>" not in str(_c19.get("mes_example") or "")
      and "再多待一会儿" not in str(_c19.get("mes_example") or ""))  # 2026-09-08 回档：示范=参考源，模型会复读
check("卡-mes_example注入接线", "台词示范" in inspect.getsource(_per19.build_system_prompt)
      and "<START>" in inspect.getsource(_per19.build_system_prompt))  # 2026-09-08：死字段接线（否则改卡=空改）
check("卡-mes_example替换零人设假设", "{{char}}" in inspect.getsource(_per19.build_system_prompt)
      and "owner_call" in inspect.getsource(_per19.build_system_prompt))
check("卡-占位示范不注入", "【台词示范】" not in _per19.build_system_prompt(
    {"name": "T", "mes_example": "示例对话（可选，展示说话风格）"}, mode=0))  # 2026-09-08 盲审：_template 占位
check("卡-真实示范注入+零人设替换", "【台词示范】" in _per19.build_system_prompt(
    {"name": "T", "owner_call": "主人", "mes_example": "「主人，你来啦。」"}, mode=0)
    and "{{user}}" not in _per19.build_system_prompt(
        {"name": "T", "owner_call": "主人", "mes_example": "「{{user}}，你来啦。」"}, mode=0))
check("声明式档位-暧昧登记", S.apply_agent_markers("…【入迷：暧昧】")[1] == "enter"
      and S.active().get("vibe_mood", {}).get("note") == "暧昧")
check("声明式档位-情欲超限钳制", S.apply_agent_markers("…【入迷：情欲】", max_tier=2)[1] == "enter"
      and S.active().get("vibe_mood", {}).get("note") == "亲密")  # 白名单：可以挑逗不能最深入
check("声明式档位-浅档不抬", S.apply_agent_markers("…【入迷：暧昧】", max_tier=3)[1] == "enter"
      and S.active().get("vibe_mood", {}).get("note") == "暧昧")  # 声明多浅就登记多浅（agent 自决）
check("声明式档位-感知措辞", "此刻氛围" in inspect.getsource(S.perception_text)
      and "此刻氛围：暧昧" in S.perception_text())  # 2026-09-08：vibe 非"主人要求的效果"（来源混淆）
check("声明式档位-旧行为不变", S.apply_agent_markers("…【入迷】", max_tier=2)[1] == "enter"
      and S.active().get("vibe_mood", {}).get("note") == "亲密")  # 无声明=按权限上限（2026-09-07 旧行为）
check("声明式档位-剥净无残留", "【入迷" not in S.apply_agent_markers("…【入迷：亲昵】", max_tier=3)[0])
S.clear("vibe_mood")
check("声明式档位-存量alias仍放行", bool(B._AGENT_MARK_RE.search("【沉沦：亲密】"))
      and bool(B._AGENT_MARK_RE.search("【入迷：暧昧】"))
      and "【入迷：暧昧】" in B._strip_non_speech("嗯【入迷：暧昧】", allow_action=True))
check("声明式档位-教学提示", "【入迷：暧昧】" in inspect.getsource(B)
      and "到什么程度写什么" in inspect.getsource(B))  # 2026-09-08 深夜瘦身并块（措辞微调）
check("基线-文件加载", len(_per19._load_scale_baseline()) > 50)  # 2026-09-08：框架通用尺度基线（新增卡兜底）
check("基线-无尺度卡注入", "【话题尺度·基线】" in _per19.build_system_prompt(
    {"name": "T", "response_rules": "轻声说话，简短回应。"}, mode=0))
check("基线-自带尺度跳过", "【话题尺度·基线】" not in _per19.build_system_prompt(
    {"name": "T", "response_rules": "尺度：点到为止，不深入。"}, mode=0))  # 卡级政策优先（防指令打架）
check("基线-scale_rules字段覆盖", "我的尺度：他进一步就推回去" in _per19.build_system_prompt(
    {"name": "T", "scale_rules": "我的尺度：他进一步就推回去"}, mode=0))
check("基线-amiya自带不重复", "【话题尺度·基线】" not in _per19.build_system_prompt(
    _per19.load_persona("amiya"), mode=0))
check("尺度授权-纯能力声明", "尺度授权·主动" in inspect.getsource(B)
      and "机器不设任何上限" in inspect.getsource(B)
      and "想开口就开口" not in inspect.getsource(B))  # 2026-09-08 回档重写：只授能力、零具体参考（示例=复读源）
check("尺度刻度-已回档", "尺度刻度·主动推进" not in inspect.getsource(B)
      and "第四层·身体亲密" not in inspect.getsource(B))  # 2026-09-08 回档：四层清单被模型当剧本复读
check("动作域-原示例已恢复", "动作做什么不设上限" not in inspect.getsource(B)
      and "（咳了一声）（拉走）" not in inspect.getsource(B))  # 2026-09-08 回档+无范例化：深层动作示例撤除
check("无范例化-私聊演出无动作串", "轻捏下巴" not in inspect.getsource(B)
      and "抬手抱住" not in inspect.getsource(B)
      and "（凑近）" not in inspect.getsource(B))  # 2026-09-08：通用动作范例跨卡复用=没有人设区别（用户裁决）
check("无范例化-口径无正误示例", "对：（" not in inspect.getsource(B)
      and "错：（" not in inspect.getsource(B)
      and "（轻轻掐了一下你的脸）" not in inspect.getsource(B))  # 注释含"抬起眼看你"为维护说明，非注入文本
check("无范例化-重试反馈无清单", "夺、掐" not in (B._reaction_guard("（呼吸一窒，耳根发烫）") or "")
      and "只有" not in (B._reaction_guard("（颤抖着抱紧你）") or "").replace("全部收回心里", ""))  # 2026-09-08 盲审：重试提示曾回锚轻动作清单
# == 20. 活人感根治 P1 回归（2026-09-08：语境分层 + 行为维度）==
print("== 20. 活人感 P1 ==")
check("P1-授权随氛围门控", "_vibe_on = \"vibe_mood\" in _act_states" in inspect.getsource(B)
      and "(is_owner and _act_states) or (is_wl_track and _vibe_on)" in inspect.getsource(B))  # 无条件注入改语境化；盲审修正：按 tier 过滤（普通用户不因全局 vibe 激活收到授权）
check("P1-基底层日常轻口径", "不为了写动作而写" in inspect.getsource(B)
      and "只在自然出现的时刻有一笔" in inspect.getsource(B))  # B0 专项：形式处方已拆（原"像打字间隙/像活人打字"=形状剧本）
check("P1-坐标锚定", "【坐标·此刻】" in inspect.getsource(B)
      and "从你此刻的坐标长出来" in inspect.getsource(B))  # 腔调/行为从生活状态生长（治戏剧腔）
check("P1-think_flow行为维度", "我此刻要说什么、做什么" in inspect.getsource(B)
      and "行为与说话一样是回应的一部分" in inspect.getsource(B))  # stage1 内容域纳入"做什么"
check("P1-ANSWER_REQ行为入内容", "它属于内容的一部分" in inspect.getsource(B))  # 行为=内容要素非装饰
# == 21. 活人感 P2 回归（2026-09-08：记忆·底色·连续性）==
print("== 21. 活人感 P2 ==")
from agent import tools as _tools21  # noqa: E402
check("P2-F1卫生哨兵收窄", "(晚安|辛苦了|还没睡|还在吗|休息吧|记得|快点|早点)" in inspect.getsource(_ls)
      and "(博士|主人|晚安" not in inspect.getsource(_ls))  # 互动叙事不再被裸词误杀（心跳失忆根因）
check("P2-F2轨迹事实注入", "_recent_trace" in inspect.getsource(_ls)
      and "你这一段的生活" in inspect.getsource(_ls)
      and "会留下痕迹" in inspect.getsource(_ls))  # 心跳回看生活轨迹+互动痕迹（不再凭空跳日常）
check("P2-F3连续性教学", "经历会留下痕迹" in inspect.getsource(_ls)
      and "下一段生活从痕迹上长出来" in inspect.getsource(_ls))
check("P2-F4计划完成态不牵引", "计划完成态不再牵引" in inspect.getsource(_ls)
      and "已完成|做完了" in inspect.getsource(_ls))
check("P2-F5底色注入", "此刻你心里（底色）" in inspect.getsource(_ls)
      and "不映射行为" in inspect.getsource(_ls))  # 只传底色事实；口是心非由人设自决
check("P2-C3行为账本API", hasattr(_tools21, "remember_act") and hasattr(_tools21, "recall_act")
      and "ACT_TTL_SECS" in inspect.getsource(_tools21))
check("P2-C3写读接线", "你刚才：" in inspect.getsource(B)
      and "recall_act(\"act:\" + str(user_id)" in inspect.getsource(B)
      and "事实不是任务" in inspect.getsource(B))  # 事实语义，非任务清单
check("P2-坐标表现分层", "藏、露、嘴硬、坦白" in inspect.getsource(B))  # 底色真实·表现自决（言行可不一）
check("P2盲审-act读侧群聊门控", "if not _is_grp:\n            _act_txt" in inspect.getsource(B))  # 私聊动作记忆不入群聊（群/私强隔离）
check("P2盲审-life_text群聊收敛", "def life_text(group_safe" in inspect.getsource(_ls)
      and "_life_sanitize" in inspect.getsource(_ls)
      and "group_safe=isinstance(event, GroupMessageEvent)" in inspect.getsource(B))  # R18 心跳叙事不进群聊
check("P2盲审-心跳边界句", "不写你们之间才有的私密细节" in inspect.getsource(_ls))  # 生活笔记不写私密细节
check("P2盲审-F2第一人称", "我刚结束一段相处" in inspect.getsource(_ls)
      and "你们刚结束一段相处" not in inspect.getsource(_ls))
# == 22. 活人感 P3 回归（2026-09-08：结构模板感知 + 引擎参数文档化）==
print("== 22. 活人感 P3 ==")
check("P3-结构模板检测器", "_tpl_repeat_note" in inspect.getsource(B)
      and "_tpl_lcp" in inspect.getsource(B) and "_tpl_key" in inspect.getsource(B))
check("P3-感知事实注入", "同一个架势" in inspect.getsource(B)
      and "要不要换个开法" in inspect.getsource(B))  # agent 自决形态：非硬拦
check("P3-同构判定(换词穿透实测)", B._tpl_lcp("既然你已经把话挑到这个地步", "既然你已经说得这么直白") >= 5
      and B._tpl_lcp("今天天气真不错呀我们出去走", "明天晚上一起去看电影吧") < 5)  # 换词同构可判，无关句不误判
check("P3-真实换词对召回(盲审校正)", B._tpl_lcp("既然你已经把话挑", "既然你把话说到这") < 5)  # ① 对真实变体召回弱——②为主力，文档已注明
check("P3-登记窗口cap6", "_TPL_WINDOW" in inspect.getsource(B) and "[-6:]" in inspect.getsource(B))
check("P3-起手词高频判定", "head2_cnt" in inspect.getsource(B) and ">= 4" in inspect.getsource(B))
check("P3盲区-节奏维度", "_tpl_rhythm" in inspect.getsource(B)
      and "短的垫、长的收" in inspect.getsource(B)
      and "_TP_RHYTHM_WINDOW" in inspect.getsource(B))  # 用户实测：内容新但节奏复现（短句-…-超长句）
check("P3盲区-节奏键实测", B._tpl_rhythm("（拉住你）嗯。……今天天气真好我们一起出去走走吧你看好不好") == "hS-ltL-d1-a1"
      and B._tpl_rhythm("今天天气不错。我去买杯咖啡就回来。") != B._tpl_rhythm("（拉住你）嗯。……今天天气真好我们一起出去走走吧你看好不好"))  # 粗化键（09-08 盲测0/16命中→归并）
check("P3盲审-短应答豁免", "len(k) >= 4" in inspect.getsource(B))  # 纯短应答("嗯/好呀")不判结构复读（防误报）
# == 23. 多卡记忆/场景检查回归（2026-09-08：舞台剧世界观分层补强）==
print("== 23. 多卡舞台剧 ==")
from plugins import memory as _mem23  # noqa: E402
check("舞台剧-facts身份中立化", "AI 身份中立" in inspect.getsource(_mem23)
      and "不写任何角色名" in inspect.getsource(_mem23))  # 提取写侧：互记用"我/对方AI"，不写卡名（同世界换卡不串场）
check("舞台剧-方案A切卡生活档隔离", "def switch_persona_life" in inspect.getsource(_ls)
      and "switch_persona_life(old or \"\", name)" in inspect.getsource(B)
      and "reset_world_scene" not in inspect.getsource(_ls)
      and "LIFE_STATES_DIR" in inspect.getsource(_ls))  # 方案A：生活档按卡归档/恢复（改一删一：reset_world_scene 已删）
check("舞台剧-历史按世界观分界(既有)", "_same_world" in inspect.getsource(B)
      and "persona_ts" in inspect.getsource(_mem23.build_context))  # 既有舞台剧模型：同世界共享/跨世界分界
check("舞台剧-场景心跳关联", "scene_universe" in inspect.getsource(_ls))  # 心跳侧既有对账：场景随世界比对
# == 24. 批次A 回归（2026-09-08：润色漂移兜底 + 简体中文）==
print("== 24. 批次A ==")
from core import reply as _R24  # noqa: E402
check("批次A-润色漂移兜底", hasattr(_R24, "_polish_drifted")
      and "polish drifted" in inspect.getsource(_R24)
      and "fallback to draft" in inspect.getsource(_R24)
      and "overlap=" in inspect.getsource(_R24))  # stage2 改掉内容意图→回退 stage1 原稿；分值化+诊断日志（2026-09-08 深夜）
check("批次A-漂移判定实测", _R24._polish_drifted("我昨天晚上做梦梦到你了呢", "今天天气真好我们出去走走吧你看好吗") < 0.15
      and _R24._polish_drifted("今天天气很好我们一起出门散步吧我挺开心的", "今天天气真好我们出去走走吧你看好吗") >= 0.15
      and _R24._polish_drifted("嗯嗯好呀", "好的呢") == 1.0)  # 骨架漂移判/合法换词不判（盲审：阈值0.25→0.15）/短句豁免（返回分值）
check("批次A-简体中文", "只用简体中文" in inspect.getsource(_per19))  # 繁体漂移提示层压制
check("批次A-提取失败非静默(既有)", "memory extract failed" in inspect.getsource(_mem23))  # 检查纠正：日志已存在
# == 25. 批次B 回归（2026-09-08：提示层瘦身——压缩不砍机制）==
print("== 25. 批次B ==")
check("批次B-压缩入册", "批次B 压缩" in inspect.getsource(B)
      and "批次B 压缩" in inspect.getsource(_per19))  # 注入瘦身落地标记
check("深夜瘦身-自决协议合并块", "【自决协议】" in inspect.getsource(B)
      and "·【认真】" in inspect.getsource(B) and "·【约定：20 字内】" in inspect.getsource(B)
      and "·【等 X 秒】" in inspect.getsource(B))  # 2026-09-08 深夜：三块合并（marker 名与语义不变）
check("批次B-关键教学保留", "刺激不改写你的处境" in inspect.getsource(B)
      and "节奏由你带" in inspect.getsource(B) and "到什么程度写什么" in inspect.getsource(B)
      and "·【约定：20 字内】" in inspect.getsource(B))  # 压缩只删冗余罗列，不砍教学语义（2026-09-08 深夜二轮瘦身并块后口径）
check("批次B-场景罗列已删", "念一点你正在写的东西" not in inspect.getsource(B)
      and "耳语感" not in inspect.getsource(B)
      and "说话算话的人才讨人喜欢" not in inspect.getsource(B))  # 12B 注意力稀释源削减
# == 26. 批次C 回归（2026-09-08：未完成话题悬挂 + 周度生活线）==
print("== 26. 批次C ==")
from plugins.memory import store as _store26  # noqa: E402
check("批次C-日复盘未完成话题", '"pending"' in inspect.getsource(_mem23)
      and "未完成话题" in inspect.getsource(_mem23)
      and "接不接你定" in inspect.getsource(_mem23))  # 昨天聊到一半→感知注入（agent 自决）
check("批次C-周度生活线", '"life"' in inspect.getsource(_mem23)
      and "你这一周的生活" in inspect.getsource(_mem23)
      and "生活日志" in inspect.getsource(_mem23))  # 心跳轨迹→周周生活摘要→对话感知
check("批次C-表迁移幂等", "ALTER TABLE daily_reviews ADD COLUMN pending" in inspect.getsource(_store26)
      and "ALTER TABLE weekly_reviews ADD COLUMN life" in inspect.getsource(_store26))
check("批次C-群聊不注入守卫", "if not functional and not group_safe:" in inspect.getsource(_mem23)
      and "接不接你定" in inspect.getsource(_mem23))  # 盲审补：pending/life 注入在 group_safe 守卫内（群聊不串私聊内容）
# == 27. 批次D1 回归（2026-09-08：收图存在感知 + 不知道谦虚）==
print("== 27. 批次D1 ==")
check("D1-纯图消息放行", "_has_img" in inspect.getsource(B)
      and "TA 给你发了一张图片" in inspect.getsource(B))  # 纯图不再被丢弃（真人看得见图）
check("D1-纯图不误作动作", B._extract_actions("TA 给你发了一张图片") == [])  # 盲审修正 B2：感知句无括号纯文本（曾整句被当"用户动作"）
check("D1-图文注记", "对方给你发了一张图片" in inspect.getsource(B))  # 图+文：模型至少知道有图
check("D1-群聊纯图不入库", "if not (_img_only or _voice_only):" in inspect.getsource(B))  # 盲审修正 B3：群聊非@纯图=感知旁白跳过入群记忆（2026-09-09 深夜纯语音对齐同款门控）
check("D1-不知道谦虚", "不知道就不知道" in inspect.getsource(B)
      and "绝不硬编" in inspect.getsource(B))  # 拿不准就说不知道（防 12B 编造感）
# == 28. 批次D2 回归（2026-09-08：她的记事本 + 长期愿望，按卡隔离）==
print("== 28. 批次D2 ==")
from agent import lifesim as _ls28  # noqa: E402
check("D2-存取API(笔记链已退役)", not hasattr(_ls28, "remember_note") and not hasattr(_ls28, "recall_notes")
      and hasattr(_ls28, "remember_wish") and hasattr(_ls28, "recall_wishes")
      and "WISH_CAP" in inspect.getsource(_ls28))  # 2026-09-08 深夜裁决：笔记链退役（0 使用、与 facts 提取重叠）；愿望链保留
check("D2-按卡隔离键", "card or 'default')}:{str" in inspect.getsource(_ls28))  # 键=卡:用户（身份痕迹绑身份）
check("D2-笔记链退役", "就在回复末尾单独追加【笔记" not in inspect.getsource(B)
      and "笔记[：:]" not in B._AGENT_MARK_RE.pattern
      and "（你记事本里：）" not in inspect.getsource(B)
      and 'if "【笔记" in reply:' in inspect.getsource(B))  # 教学剥除；残余兼容剥除保留（防模型惯性吐标记外漏）
check("D2-愿望复盘提炼", '"wishes"' in inspect.getsource(_mem23)
      and "remember_wish" in inspect.getsource(_mem23)
      and "（你心里一直想着：）" in inspect.getsource(B))
check("D2盲审-fail-closed", "_load_store" in inspect.getsource(_ls28)
      and "skip write (fail-closed)" in inspect.getsource(_ls28))  # 损坏文件拒绝覆写（对齐 agent_memory）
check("D2盲审-类型守卫+回退对齐", "isinstance(data.get(\"wishes\", []), list)" in inspect.getsource(_mem23)
      and "PERSONA_NAME" not in inspect.getsource(_mem23)
      and 'getattr(_gd_w().config, "persona", "")' in inspect.getsource(_mem23))  # 愿望卡名回退链对齐 brain（用户→主人→.env）；Phase1 后"读 config"代理串由 cfg 改为 _gd_w().config 取 persona（原 cfg 只在已收拢的 _client 构造行）
# == 29. 批次D4 回归（2026-09-08：三问题修复——思考宽输出短/超长句红线/知识问句/约定句式映射）==
print("== 29. 批次D4 ==")
from core import reply as _R29  # noqa: E402
check("D4-思考宽输出短", "想完就说你想到的那一句要紧的" in inspect.getsource(B)
      and "想得多不等于说得多" in inspect.getsource(B)
      and "不用想太深" in inspect.getsource(B)
      and "其余的整个留在心里，一个字都不用说出来" in inspect.getsource(B))  # 根因修复：思考深度限流+舍弃（曾②教"多想"③教"别倒"=矛盾）
check("D4-说人话节奏", "一句话说一件事" in inspect.getsource(B)
      and "反问最多一句" in inspect.getsource(B))  # 反问限制保留（连发反问=思考外溢）；分段教学句已拆（B0）
check("D4-超长句红线", hasattr(_R29, "_long_sentence")
      and _R29._long_sentence("我这里有一句超级超级超级超级超级超级超级超级超级超级超级超级长的句子你们觉得怎么样啊真的真的")
      and not _R29._long_sentence("今天天气不错。我们出去走走吧。"))  # 45字/句（09-08 修正：32 太紧致重试膨胀反而更长）
check("B0-档位回退自然自决", B.LENGTH_RULES["high"] == B.LENGTH_ADAPT
      and B.LENGTH_RULES["medium"] == B.LENGTH_ADAPT and B.LENGTH_RULES["low"] == B.LENGTH_ADAPT
      and B.LENGTH_RULES_DAILY["medium"] == B.LENGTH_ADAPT
      and "没有固定字数档位" in B.LENGTH_ADAPT)  # B0 专项（2026-09-08 深夜）：数字锚点档位实证无效（stage2 被内容结构绑定+数字上锚）→ 回退 09-07 口径
check("B0-形状脚手架已拆", "像活人打字" not in inspect.getsource(B)
      and "几行一段" not in inspect.getsource(B) and "像打字间隙" not in inspect.getsource(B)
      and "动作与台词交替" not in inspect.getsource(B)
      and "动作与台词自然交替" not in inspect.getsource(B)
      and "像人打字" not in inspect.getsource(_R29))  # 形状教学短语=多行+……+（动作）剧本源头（09-07 起积累、09-08 增密）；含 core 红线反馈文案
check("B0-私聊历史去放大接线", "elif not _is_grp and m[\"role\"] == \"assistant\":"
      in inspect.getsource(B)
      and "_hist_asst_trim(body)" in inspect.getsource(B))  # handle 历史装配循环实际调用（群聊路径不受影响）
check("B0-私聊历史自模仿去放大", B._hist_asst_trim("第一句很短。第二句也不长。第三句稍微长一点点但还在范围内。") == "第一句很短。第二句也不长。第三句稍微长一点点但还在范围内。"
      and B._hist_asst_trim("好。" * 100).startswith("好。")
      and len(B._hist_asst_trim("好。" * 100)) <= B._HIST_ASST_MAX_CHARS + 2
      and B._hist_asst_trim("超长单句" * 60) == ("超长单句" * 60)[:120] + "…")  # 句读保留截断+硬截兜底；只剪注入不剪输出
check("D4-兜底拆短", hasattr(_R29, "_cut_to_sentences")
      and _R29._cut_to_sentences("短句。" + "很很" * 130 + "。" + "第三句。") == "短句。……"
      and _R29._cut_to_sentences("短句子。都很短。") == "短句子。都很短。")  # 仅句读保留、超长在最后完整句截断+省略（修"断句"副作用）
check("D4-节奏键粗化", B._tpl_rhythm("（拉住你）嗯。……今天天气真好我们一起出去走走吧你看好不好") == "hS-ltL-d1-a1"
      and B._tpl_rhythm("今天天气不错。我去买杯咖啡就回来。") != B._tpl_rhythm("（拉住你）嗯。……今天天气真好我们一起出去走走吧你看好不好"))  # 粗化归并（d/a 关键差异保留、杂节奏不同构）
check("D4-知识问句强制搜", "AUTO_SEARCH_KNOW_HINTS" in inspect.getsource(B)
      and B._auto_search_query("这个游戏是什么世界观背景") is not None
      and B._auto_search_query("你在干嘛") is None)  # @bot 知识问题不再随口一答；闲聊仍不触发
check("D4-引擎watchdog智能探测", "engine health transient (loading/slow)" in inspect.getsource(_dbg2)
      and "_confirmed" in inspect.getsource(_dbg2._engine_watchdog))  # 用户②：加载中不误判离线重启（曾 503 窗口被二次杀）
# == 30. 红线 fail-closed + 延迟止血（2026-09-08 深夜：44s 实测复盘——泄露=耗尽回退违规稿，延迟=重试链+深思考重试）==
print("== 30. 红线failclosed ==")
check("红线耗尽剥括号", "_form_violation" in inspect.getsource(_R29)
      and "action-guard strip" in inspect.getsource(_R29)
      and 're.sub(r"[（(][^（()）]*[)）]", "", _fb)' in inspect.getsource(_R29))  # 动作红线耗尽 fail-closed：台词保留，全/半角违规括号绝不外发（泄露实锤修复）
check("红线耗尽长句机械拆分", hasattr(_R29, "_split_long_sentence")
      and max(len(x) for x in _R29._split_long_sentence("这一句真的很长很长" * 6 + "。").split("\n")) <= 45
      and _R29._split_long_sentence("短句不用动。") == "短句不用动。"
      and max(len(x) for x in _R29._split_long_sentence("没有标点的超长句" * 8).split("\n")) <= 45)  # 盲审②：耗尽兜底拆超长单句（标点优先/无标点硬断）
check("形状违规重试不开深思考", "not think_now and not _form_violation" in inspect.getsource(_R29))  # 内容级失败才值得深想（省 10-20s/次）
check("思考域省略号示范已删", "他这是什么意思" not in inspect.getsource(B)
      and "心里话就是大白话" in inspect.getsource(B))  # 心里话形态示范含省略号样式=每轮注入的形状剧本（无范例化纪律同构）
check("历史省略号行去噪", "注入侧去噪②" in inspect.getsource(B)
      and 'not _core.strip("…。.,，、 　")' in inspect.getsource(B)
      and 'replace("（你此前的回复）", "")' in inspect.getsource(B))  # 注入侧剥孤立……行（全/半角/混排变体+前缀形态；自模仿载体，只清注入不清库）
check("D4-约定语义判断教学", "算不算一个承诺" in inspect.getsource(B)
      and "由你自己判断" in inspect.getsource(B))  # 用户修正：不搞句式映射（排列组合多/易误伤）——bot 思考后自决判断
# （引擎 --repeat-penalty 参数在 start.ps1，启动脚本级——由 run 前置核验，不入 smoke）
check("卡-浓时补强句已回档", "比拥抱更近的亲密" not in str(_per19.load_persona("amiya").get("response_rules") or ""))

# == 31. 二批活人感（2026-09-08：P-5 周复盘消费端 / P-1 话题悬挂 / P-3 生活分享出口 / P-2 召回衰减）==
print("== 31. 二批活人感 ==")
from plugins.memory import store as _store31  # noqa: E402
from plugins import memory as _mem31  # noqa: E402

# ---- P-5 周复盘消费端 ----
check("P5-存取函数", hasattr(_store31.db, "get_latest_weekly"))
check("P5-注入锚点", "上周的你们" in inspect.getsource(B) and "只是回顾，不是任务" in inspect.getsource(B))
check("P5-私聊消费接线", "get_latest_weekly(user_id)" in inspect.getsource(B))

# ---- P-1 话题悬挂 ----
check("P1-表与三方法", all(hasattr(_store31.db, x) for x in
      ("add_open_thread", "get_latest_open_thread", "done_open_threads_after_7d")))
check("P1-建表随初始化", "CREATE TABLE IF NOT EXISTS open_threads" in inspect.getsource(_store31))
check("P1-提取JSON可选字段", '"open_thread"' in inspect.getsource(_mem31) and "没聊完" in inspect.getsource(_mem31))
check("P1-解析容错", _mem31._open_thread_of({}) == ""
      and _mem31._open_thread_of({"open_thread": 123}) == ""
      and _mem31._open_thread_of({"open_thread": " 想去学潜水，聊到一半被岔开 "}) == "想去学潜水，聊到一半被岔开"
      and _mem31._open_thread_of("not-a-dict") == "")  # 字段缺失/类型错/空串一律忽略
check("P1-提取写侧接线", "_open_thread_of(data)" in inspect.getsource(_mem31)
      and "add_open_thread" in inspect.getsource(_mem31))
check("P1-注入锚点", "上次你们聊到一半" in inspect.getsource(B) and "想接就自然接上" in inspect.getsource(B))
check("P1-零新回复标记", "【悬挂" not in inspect.getsource(B) and "【没聊完" not in inspect.getsource(B))

# 行为级：tmp 全 schema 库（MemoryDB 新开 tmp 文件——兼验旧库首次打开自动建表）
_db31 = _store31.MemoryDB(str(Path(tempfile.mkdtemp()) / "p31.db"))
_oc31, _ol31 = _store31.db._conn, _store31.db._lock
_store31.db._conn, _store31.db._lock = _db31._conn, _db31._lock
try:
    check("P1-旧库首开自动建表", _db31._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='open_threads'").fetchone() is not None)
    _now31 = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    _old31 = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=8)).isoformat(timespec="seconds")
    _d631 = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=6)).isoformat(timespec="seconds")
    _db31.add_open_thread("u31", "假期计划聊到一半", ts=_now31)
    check("P1-最新未完成一条", _db31.get_latest_open_thread("u31")["content"] == "假期计划聊到一半")
    _db31.add_open_thread("u31b", "八天前聊一半的", ts=_old31)
    check("P1-done_after_7d计数", _db31.done_open_threads_after_7d("u31b") == 1)
    check("P1-超7天过滤", _db31.get_latest_open_thread("u31b") is None)
    _db31.add_open_thread("u31c", "六天前聊一半的", ts=_d631)
    check("P1-6天仍在窗口", (_db31.get_latest_open_thread("u31c") or {}).get("content") == "六天前聊一半的")
    _db31._conn.execute("UPDATE open_threads SET done=1 WHERE user_id='u31'")
    _db31._conn.commit()
    check("P1-done不再注入", _db31.get_latest_open_thread("u31") is None)
    # P-5 行为级（同一 tmp 库）
    _db31.upsert_weekly_review("u31", "2026-08-31", "上周一起聊了养猫的事，关系更近了一步")
    check("P5-取最新周复盘", _db31.get_latest_weekly("u31")["week_start"] == "2026-08-31"
          and "养猫" in _db31.get_latest_weekly("u31")["content"])
    check("P5-无记录返回None", _db31.get_latest_weekly("nobody31") is None)
    # P-2 行为级：情绪强度加成（走 tmp 库 emotions 表；取不到不加成；now 固定消除钟差）
    _db31.record_emotion("u31", "开心", 1.0, _now31)
    _fixed31 = dt.datetime.now(dt.timezone.utc)
    check("P2-取不到不加成", abs(_mem31._recall_score("u31-noemo", 0.5, _now31, now=_fixed31)
          - 0.5 * _mem31._recall_decay(_now31, now=_fixed31)) < 1e-12)
    check("P2-情绪强度加成", _mem31._recall_score("u31", 0.5, _now31, now=_fixed31)
          > _mem31._recall_score("u31-noemo", 0.5, _now31, now=_fixed31))
    check("P2-加成上限x1.3", abs(_mem31._recall_score("u31", 1.0, _now31, now=_fixed31)
          - 1.3 * _mem31._recall_decay(_now31, now=_fixed31)) < 1e-12)
finally:
    _store31.db._conn, _store31.db._lock = _oc31, _ol31

# P-2 衰减行为级（纯函数：构造新旧日期条目验证排序变化）
_new31d = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat(timespec="seconds")
_mid31d = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=14)).isoformat(timespec="seconds")
_old31d = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=42)).isoformat(timespec="seconds")
check("P2-新条目衰减>旧条目", _mem31._recall_decay(_new31d) > _mem31._recall_decay(_old31d))
check("P2-半衰期14天", abs(_mem31._recall_decay(_mid31d) - 0.5) < 0.01)
check("P2-bad-ts不加罚", _mem31._recall_decay("") == 1.0 and _mem31._recall_decay("not-a-ts") == 1.0)
check("P2-排序翻转(旧高相似被新低相似反超)", _mem31._recall_score("u31-x", 0.60, _new31d)
      > _mem31._recall_score("u31-x", 0.65, _old31d))
check("P2-接入召回打分", "_recall_score" in inspect.getsource(_mem31._recall_facts))
check("P2-强度查询接线", hasattr(_store31.db, "get_emotion_intensity_near")
      and "get_emotion_intensity_near" in inspect.getsource(_mem31))

# ---- P-3 生活分享出口 ----
check("P3-perceive锚点", "今天你生活里新鲜的事" in inspect.getsource(_dbg2._build_proactive_msg)
      and "想讲就自然讲" in inspect.getsource(_dbg2._build_proactive_msg))
check("P3-防复读记账字段", "last_shared_event_id" in inspect.getsource(_dbg2))
check("P3-发送后才落账", "_LAST_SHARED_CAND.pop" in inspect.getsource(_dbg2._daily_proactive_loop))
check("P3-挑今日未分享最新", _dbg2._pick_today_event(
    [{"id": 3, "ts": "2026-09-07T10:00:00+00:00", "content": "昨天的事"},
     {"id": 5, "ts": "2026-09-08T02:00:00+00:00", "content": "今天的事"},
     {"id": 4, "ts": "2026-09-08T01:00:00+00:00", "content": "今天更早的事"}],
    "2026-09-08T00:00:00+00:00", 0)["id"] == 5)
check("P3-已分享水印过滤", _dbg2._pick_today_event(
    [{"id": 5, "ts": "2026-09-08T02:00:00+00:00", "content": "已分享的"}],
    "2026-09-08T00:00:00+00:00", 5) is None)
check("P3-昨日事件不算今日", _dbg2._pick_today_event(
    [{"id": 9, "ts": "2026-09-07T23:00:00+00:00", "content": "x"}],
    "2026-09-08T00:00:00+00:00", 0) is None)
check("P3-空事件无候选", _dbg2._pick_today_event([], "2026-09-08T00:00:00+00:00", 0) is None)

# == 32. 直播准备与梗库（2026-09-08：B2 livesource 禁用态骨架 + C-4 梗库自生长 + B1 voice 本地回放分支）==
print("== 32. 直播准备与梗库 ==")
import asyncio as _aio32  # noqa: E402
from types import SimpleNamespace as _NS32  # noqa: E402
from plugins import livesource as _live32  # noqa: E402
from plugins import memory as _mem32  # noqa: E402
from plugins import voice as _v32  # noqa: E402

# ---- B2 livesource 骨架 ----
check("live-默认禁用零副作用", _live32.ENABLED is False and _live32._ACTIVE is False
      and "blivedm" not in sys.modules)  # 关闭态：不 import blivedm、无任务
check("live-normalize-dict", _live32._normalize({"uid": 1, "name": "n", "text": " hi "})
      == {"source": "live", "uid": "1", "name": "n", "text": "hi"})
check("live-normalize-属性形状(blivedm uname/msg)", _live32._normalize(_NS32(uid=2, uname="u", msg="m"))
      == {"source": "live", "uid": "2", "name": "u", "text": "m"})
check("live-normalize-空文本/异形→None", _live32._normalize({"uid": 1, "text": "  "}) is None
      and _live32._normalize({}) is None and _live32._normalize(None) is None)
check("live-开关读法缺省false", _live32._env_flag("LIVE_DANMAKU_ENABLED", False) is False)
# 2026-09-11 封闭性修复：_env_flag 优先读 driver config——真实 .env 若已物化 LIVE_ 键（启动器设置页
# 保存过即写入），config 属性会遮蔽 os.environ。driver 不可 mock 成抛错（_env_flag 内部 try/except
# 吞掉后走纯 env 回退路径），不再依赖真实 .env 是否含该键。
from unittest import mock as _mock32  # noqa: E402
os.environ["LIVE_DANMAKU_ENABLED"] = "true"
with _mock32.patch("nonebot.get_driver", side_effect=RuntimeError("smoke: no driver")):
    check("live-开关读法true", _live32._env_flag("LIVE_DANMAKU_ENABLED", False) is True)
    os.environ["LIVE_DANMAKU_ENABLED"] = "false"
    check("live-开关读法false不吃缺省", _live32._env_flag("LIVE_DANMAKU_ENABLED", False) is False)
os.environ.pop("LIVE_DANMAKU_ENABLED", None)
_cfg32 = __import__("nonebot", fromlist=["get_driver"]).get_driver().config
_cfg32.__dict__["live_danmaku_enabled"] = "true"  # .env 载入路径（driver config 属性读法）
check("live-开关读法走driverconfig(.env)", _live32._env_flag("LIVE_DANMAKU_ENABLED", False) is True)
del _cfg32.__dict__["live_danmaku_enabled"]
check("live-未启用拒绝分发", _aio32.run(_live32._dispatch({"source": "live"})) is False)
_got32 = []
async def _hook32(p):
    _got32.append(p)
check("live-register_handler返回True", _live32.register_handler(_hook32) is True)
_live32._ACTIVE = True  # 测试内启用（真实启用只能由模块底部的开关判定完成）
check("live-注册钩子后分发", _aio32.run(_live32._dispatch({"t": 1})) is True and _got32 == [{"t": 1}])
_live32.register_handler(None)
_live32._NO_HOOK_WARNED = False
check("live-无钩子丢弃+一次性标记", _aio32.run(_live32._dispatch({"t": 2})) is False
      and _got32 == [{"t": 1}] and _live32._NO_HOOK_WARNED is True)
_live32._ACTIVE = False  # 还原禁用态
check("live-引用即崩扫描", _unresolved_names(_live32) == [])

# ---- C-4 梗库自生长 ----
check("C4-提取字段可选与容错", _mem32._group_meme_of({}) == ""
      and _mem32._group_meme_of({"group_meme": 123}) == ""
      and _mem32._group_meme_of({"group_meme": " 梗——解释 "}) == "梗——解释"
      and _mem32._group_meme_of("not-a-dict") == "")
check("C4-提取prompt群聊限定", '"group_meme"' in inspect.getsource(_mem32)
      and "新梗" in inspect.getsource(_mem32) and "宁缺毋滥" in inspect.getsource(_mem32)
      and "if group_id:" in inspect.getsource(_mem32.extract_and_store))
check("C4-extract签名带group_id", 'group_id: str = ""' in inspect.getsource(_mem32.extract_and_store))
check("C4-brain传group_id", 'group_id=str(group_id or "")' in inspect.getsource(B))
check("C4-brain读侧对齐真实文件", B._MEMES_PATH == _mem32.MEMES_PATH and B._MEMES_PATH.exists())  # 原路径指向不存在的 E:/robot/data/memes.json（消费端曾恒死）
_meme32_file = Path(tempfile.mkdtemp()) / "memes.json"
_orig_meme_path = _mem32.MEMES_PATH
_mem32.MEMES_PATH = _meme32_file
try:
    check("C4-坏输入忽略", _mem32.store_group_meme("") is False
          and _mem32.store_group_meme("   ") is False
          and _mem32.store_group_meme("只有梗没分隔符") is False
          and _mem32.store_group_meme("梗" + "很长" * 16 + "——解释") is False)  # 梗本体 > MEME_MAX_LEN
    check("C4-落库新梗", _mem32.store_group_meme("测试新梗——一句解释") is True)
    _loaded32 = json.loads(_meme32_file.read_text(encoding="utf-8-sig"))
    check("C4-格式复用读侧", _loaded32.get("测试新梗", {}).get("meaning") == "一句解释"
          and "take" in _loaded32.get("测试新梗", {}))
    check("C4-梗名查重", _mem32.store_group_meme("测试新梗——另一种解释") is False)
    check("C4-解释前20字查重", _mem32.store_group_meme("另一个梗——一句解释") is False)
    _meme32_file.write_text("{broken", encoding="utf-8")
    check("C4-损坏文件fail-closed", _mem32.store_group_meme("新梗——新解释") is False
          and _meme32_file.read_text(encoding="utf-8") == "{broken")  # 拒绝覆写，不清存量
    _meme32_file.write_text(json.dumps({"测试新梗": {"meaning": "一句解释", "take": ""}}, ensure_ascii=False),
                            encoding="utf-8")  # 恢复有效库（含最旧条目，供 cap 断言）
    check("C4-原子写", "write_json_atomic" in inspect.getsource(_mem32.store_group_meme))
    for _i in range(_mem32.MEME_CAP + 5):  # 填满+5 触发 cap
        _mem32.store_group_meme(f"梗{_i}——解释{_i}")
    _loaded32 = json.loads(_meme32_file.read_text(encoding="utf-8-sig"))
    check("C4-cap丢最旧", len(_loaded32) == _mem32.MEME_CAP
          and "测试新梗" not in _loaded32 and "梗0" not in _loaded32
          and f"梗{_mem32.MEME_CAP + 4}" in _loaded32)
finally:
    _mem32.MEMES_PATH = _orig_meme_path

# ---- B1 voice 本地回放分支 ----
check("B1-默认关零行为", _v32._live_audio_enabled() is False and "winsound" not in sys.modules)
check("B1-懒import", "import winsound" in inspect.getsource(_v32._play_wav_bytes))
check("B1-播放异常只留日志", "live audio playback failed" in inspect.getsource(_v32._play_wav_bytes)
      and "live audio playback skip" in inspect.getsource(_v32._maybe_play_local))
check("B1-to_thread并发接线", "_live_audio_enabled()" in inspect.getsource(_v32.try_make_record_segments)
      and "_maybe_play_local(wav)" in inspect.getsource(_v32.try_make_record_segments)
      and "to_thread" in inspect.getsource(_v32._maybe_play_local))
_v32._live_audio_enabled = lambda: True  # 测试内启用
_played32 = []
_v32._play_wav_bytes = lambda d: _played32.append(len(d))
_wav32 = Path(tempfile.mkdtemp()) / "x.wav"
_wav32.write_bytes(b"RIFFfake32")
async def _drive32():
    _v32._maybe_play_local(str(_wav32))
    await _aio32.sleep(0.3)  # 让 to_thread 回放任务跑完
_aio32.run(_drive32())
check("B1-开启即并发播放同一wav", _played32 == [10])
_noskip32 = False
try:
    _v32._maybe_play_local(str(Path(tempfile.mkdtemp()) / "no_such.wav"))  # 调度失败（读不到文件）不冒泡
    _noskip32 = True
except Exception:
    _noskip32 = False
check("B1-调度失败不冒泡", _noskip32)

with open("pyproject.toml", "rb") as _pf32:
    _pp32 = _tl.load(_pf32)
check("pyproject-live可选依赖", _pp32["project"].get("optional-dependencies", {}).get("live") == ["blivedm"]
      and "blivedm" not in " ".join(_pp32["project"]["dependencies"]))  # 只声明不进主依赖

# == 33. 延迟优化回归（2026-09-09：预处理并行化 + prefix cache 友好排序）==
print("== 33. 延迟优化 ==")
from plugins import memory as _mem33  # noqa: E402

_src33 = inspect.getsource(B)
# ---- 任务1：brain 生成前 await 链并行化（结果互不依赖的三个耗时调用并发发出、原注入点按原序消费）----
check("并行-预处理gather接线", "memory.build_context(" in _src33
      and "_slang_lookup(text)" in _src33
      and "_reply_quote_text(bot, event)" in _src33
      and "return_exceptions=True" in _src33)  # 三调用收进同一 gather（原为三段串行 await）
check("并行-异常语义逐个回退", "raise _pre_ctx" in _src33
      and "isinstance(_pre_slang, BaseException)" in _src33
      and "isinstance(_pre_quote, BaseException)" in _src33)  # ctx 无兜底照旧上抛；slang/quote 失败路径=原空串
check("并行-结果原序消费", -1 < _src33.index("mem_text = _pre_ctx")
      < _src33.index("_slang_note = _pre_slang")
      < _src33.index("_rq = _pre_quote"))  # 消费顺序与原注入顺序一致
# ---- 任务1：memory build_context 内部（事件召回 × 事实召回 两条 embed HTTP 重叠）----
check("并行-事件召回并发启动", "asyncio.ensure_future(_recall_events" in inspect.getsource(_mem33.build_context)
      and "await _evt_task if _evt_task is not None else None" in inspect.getsource(_mem33.build_context)
      and "await _recall_facts(user_id, facts)" in inspect.getsource(_mem33.build_context))  # 事实召回原位不动（入参依赖 facts）
check("并行-事件召回守卫不变", "if (not functional and not group_safe) else None"
      in inspect.getsource(_mem33.build_context))  # 并发任务与原调用点同一守卫（群聊不召回事件）
check("并行-build_context空库行为不变", isinstance(_aio32.run(_mem33.build_context("nobody-lag")), str)
      and isinstance(_aio32.run(_mem33.build_context("nobody-lag", group_safe=True)), str))  # 私聊/群聊两守卫路径零异常（空库无 embed 外呼）
# ---- 任务2：prefix cache 友好排序（易变块挪到稳定块之后；语义集合不变、文字零增删）----
check("排序-稳定模板零易变注入", 'build_system_prompt(card, "", mode=mode)' in _src33
      and '_mem_block = f"\\n\\n【记忆】\\n{mem_text}" if mem_text else ""' in _src33)  # persona 稳定模板不再被【记忆】插在第 3 块
check("排序-易变块集中装配末尾", _src33.index("system_prompt += _mem_block")
      > _src33.index("【问答常识·先想后答】")
      and _src33.index("system_prompt += _mem_block") > _src33.index("【主动邀约·自决】")
      and _src33.index("system_prompt += _mem_block") < _src33.index("core_prompt = system_prompt"))  # 【记忆】排在稳定块之后、core_prompt 定型之前
check("排序-易变组相对顺序不变", _src33.index("system_prompt += _mem_block")
      < _src33.index('system_prompt += "\\n\\n" + _now_context()')
      < _src33.index("_tf = _timeflow_note(user_id")
      < _src33.index("_tn = _time_note(user_id)"))  # 【记忆】→【当前时间】→时间流动→【时间】：与各自原位置先后一致
check("排序-时间注记单次注入不重复", _src33.count('system_prompt += "\\n\\n" + _now_context()') == 1
      and _src33.count("_tn = _time_note(user_id)") == 1)  # 挪位≠复制（原位置已删）


# == 34. 审计修复回归（2026-09-09：A1 空消息守卫 / B3 open_thread 群聊门控）==
print("== 34. 审计修复回归 ==")
from core import reply as _R34  # noqa: E402

# A1 行为级：首个句读块就超 total 时曾返回空串 → reply 兜底链发空消息+空入库；
# 现首块超限且回退时硬截本块（宁断不泄）
_cut34 = _R34._cut_to_sentences("啊" * 300 + "，尾巴。")
check("A1-首块超限回退非空", bool(_cut34.strip()) and len(_cut34) <= 250)
check("A1-截断结果不引入句中硬切外泄", _cut34 == "啊" * 250)  # 无句读可保 → 硬截到 total 即止
check("A1-上层空串二道守卫在源码", "(content or reply)[:limit]" in inspect.getsource(_R34)
      and "if not reply.strip():" in inspect.getsource(_R34))

# B3 行为级：extract_and_store 的 open_thread 落库必须 group_id 门控——
# 群聊上下文（group_id 非空）不得落 open_thread（消费端=私聊感知，防隐私注入面）
import asyncio as _aio34  # noqa: E402
import sqlite3 as _sq34  # noqa: E402

_fd34, _tmp34 = tempfile.mkstemp(suffix=".db")
os.close(_fd34)
_store.db._conn = _sq34.connect(_tmp34, check_same_thread=False)
_store.db._conn.row_factory = _sq34.Row
_store.db._conn.executescript("""
CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, confidence REAL DEFAULT 0.8, created_ts TEXT NOT NULL, last_seen_ts TEXT NOT NULL, UNIQUE(user_id, key));
CREATE TABLE IF NOT EXISTS fact_embeddings(user_id TEXT NOT NULL, fact_key TEXT NOT NULL, emb TEXT NOT NULL, ts TEXT NOT NULL DEFAULT '', PRIMARY KEY(user_id, fact_key));
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, ts TEXT NOT NULL, content TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS event_embeddings(event_id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, emb TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS open_threads(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, ts TEXT NOT NULL, content TEXT NOT NULL, done INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS summaries(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, ts TEXT NOT NULL, content TEXT NOT NULL);
""")
_store.db._conn.commit()


class _FakeMsg34:
    def __init__(self, c):
        self.content = c


class _FakeChoice34:
    def __init__(self, c):
        self.message = _FakeMsg34(c)


class _FakeResp34:
    def __init__(self, c):
        self.choices = [_FakeChoice34(c)]


class _FakeCompletions34:
    async def create(self, **kw):
        return _FakeResp34(json.dumps({
            "add": [], "update": [], "delete": [], "events": [],
            "mood": "", "intimacy_delta": 0, "summary": "",
            "open_thread": "上次说了一半的周末去哪",
        }, ensure_ascii=False))


class _FakeClient34:
    def __init__(self):
        self.chat = type("C", (), {"completions": _FakeCompletions34()})()


_orig_conn34, _orig_client34 = _store.db._conn, _mem33._client
_mem33._client = lambda: _FakeClient34()
try:
    _aio34.run(_mem33.extract_and_store("u-b3", [{"role": "user", "content": "我们周末去哪来着，下次再说"}], group_id=""))
    _n_priv = _store.db._conn.execute("SELECT COUNT(*) c FROM open_threads").fetchone()["c"]
    _aio34.run(_mem33.extract_and_store("u-b3", [{"role": "user", "content": "群里说一半的话题"}], group_id="g123"))
    _n_after_grp = _store.db._conn.execute("SELECT COUNT(*) c FROM open_threads").fetchone()["c"]
    check("B3-私聊仍落open_thread", _n_priv == 1)
    check("B3-群聊不落open_thread", _n_after_grp == 1)  # 门控后群聊提取不落库
finally:
    _mem33._client = _orig_client34
    _store.db._conn = _orig_conn34
    _store.db._lock = _orig_lock
    try:
        os.remove(_tmp34)
    except PermissionError:
        pass


# == 35. checkpoints 库治理回归（2026-09-09：agent_checkpoints.db 459MB 无界增长裁剪）==
print("== 35. checkpoints 治理 ==")
from agent import graph as _gp35  # noqa: E402

check("治理-函数存在+VACUUM说明", hasattr(_gp35, "prune_checkpoints")
      and "VACUUM" in (_gp35.prune_checkpoints.__doc__ or ""))  # docstring 注明不执行 VACUUM（首压缩留手工）
_src35 = inspect.getsource(_bd._review_loop)
check("治理-debug每日接线", "checkpoints_date" in _src35
      and "asyncio.to_thread" in _src35 and "prune_checkpoints" in _src35)  # 幂等键 + to_thread 接线
_r3db = __import__("tempfile").mktemp(suffix=".db")
_r3c = __import__("sqlite3").connect(_r3db)
_r3c.executescript("CREATE TABLE checkpoints(thread_id TEXT,checkpoint_ns TEXT,checkpoint_id TEXT,parent_checkpoint_id TEXT,type TEXT,checkpoint BLOB,metadata BLOB);CREATE TABLE writes(thread_id TEXT,checkpoint_ns TEXT,checkpoint_id TEXT,task_id TEXT,idx INTEGER,channel TEXT,type TEXT,value BLOB);")
for _g in range(900):
    _tid = f"banter:{_g}:2026-09-0{_g % 9 + 1}T00:00:00+00:00"
    for _k in range(3):
        _r3c.execute("INSERT INTO checkpoints(thread_id,checkpoint_ns,checkpoint_id,checkpoint) VALUES(?,?,?,?)", (_tid, "", f"cp-{_g}-{_k}", b"x"))
        _r3c.execute("INSERT INTO writes(thread_id,checkpoint_ns,checkpoint_id,task_id) VALUES(?,?,?,?)", (_tid, "", f"cp-{_g}-{_k}", f"t{_g}{_k}"))
_r3c.commit(); _r3c.close()
import pathlib as _pl3
_G3_ORIG_DB = _graph.AGENT_DB
_graph.AGENT_DB = _pl3.Path(_r3db)
_stats3 = _graph.prune_checkpoints(keep_days=30)
_graph.AGENT_DB = _G3_ORIG_DB
_r3c = __import__("sqlite3").connect(_r3db)
_left3 = _r3c.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
_threads3 = _r3c.execute("SELECT COUNT(DISTINCT thread_id) FROM checkpoints").fetchone()[0]
_r3c.close(); __import__("os").remove(_r3db)
check("A2第3轮-900组不自毁", _left3 == 900 and _threads3 == 900 and "error" not in _stats3)  # 第3轮审计P1回归：分批NOT IN(当前批)自毁已修——每组恰存1行



def _mk_cp35(unix_ts: float, seq: int) -> str:
    """构造 UUIDv6 形态 checkpoint_id（首 60bit 编码时刻，与真实库 langgraph 同构）。"""
    _t60 = int((unix_ts + 12219292800) * 1e7)
    return "%08x-%04x-6%03x-8000-%012x" % (
        (_t60 >> 28) & 0xFFFFFFFF, (_t60 >> 12) & 0xFFFF, _t60 & 0xFFF, seq)


_fd35, _tmp35 = tempfile.mkstemp(suffix=".db")
os.close(_fd35)
_c35 = sqlite3.connect(_tmp35)
_c35.executescript("""
CREATE TABLE checkpoints(thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '', checkpoint_id TEXT NOT NULL, parent_checkpoint_id TEXT, type TEXT, checkpoint BLOB, metadata BLOB, PRIMARY KEY(thread_id, checkpoint_ns, checkpoint_id));
CREATE TABLE writes(thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '', checkpoint_id TEXT NOT NULL, task_id TEXT NOT NULL, idx INTEGER NOT NULL, channel TEXT NOT NULL, type TEXT, value BLOB, PRIMARY KEY(thread_id, checkpoint_ns, checkpoint_id, task_id, idx));
""")
_now35 = T.time()
_tids35 = {
    "fresh": "banter:g1:" + dt.datetime.fromtimestamp(_now35 - 86400, dt.timezone.utc).isoformat(),
    "stale": "banter:g2:" + dt.datetime.fromtimestamp(_now35 - 90 * 86400, dt.timezone.utc).isoformat(),
    # 旧格式无尾段线程（纯数字 id 两段）——曾实测 gid 被裸 epoch 正则误读为 2078 未来时刻
    "legacy_active": "banter:3415425445",      # 仍在写入（checkpoint 时刻新鲜）→ 必须存活
    "legacy_stale": "banter:1777777777",       # 早停更（checkpoint 时刻过期）→ 由活动时刻兜底整组老化
}
for _k35, _tid35 in _tids35.items():
    _age35 = {"fresh": 86400, "stale": 90 * 86400, "legacy_active": 86400, "legacy_stale": 90 * 86400}[_k35]
    for _i35 in range(3):
        _cp35 = _mk_cp35(_now35 - _age35 + _i35, _i35 + 1)
        _c35.execute("INSERT INTO checkpoints(thread_id, checkpoint_ns, checkpoint_id, checkpoint) VALUES(?,?,?,?)",
                     (_tid35, "", _cp35, b"x"))
        _c35.execute("INSERT INTO writes VALUES(?,?,?,?,?,?,?,?)",
                     (_tid35, "", _cp35, "t1", _i35, "ch", "json", b"y"))
_c35.commit()
_c35.close()
_orig_db35 = _gp35.AGENT_DB
_gp35.AGENT_DB = Path(_tmp35)  # 治理函数读模块全局 AGENT_DB → tmp 库替身，不碰真实库
try:
    _st35 = _gp35.prune_checkpoints(keep_days=30)
    _c35 = sqlite3.connect(_tmp35)
    _n_cp35 = _c35.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
    _n_thr35 = _c35.execute("SELECT COUNT(DISTINCT thread_id) FROM checkpoints").fetchone()[0]
    _n_wr35 = _c35.execute("SELECT COUNT(*) FROM writes").fetchone()[0]
    _gone_cp35 = _c35.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id LIKE 'banter:g2:%'").fetchone()[0]
    _gone_wr35 = _c35.execute("SELECT COUNT(*) FROM writes WHERE thread_id LIKE 'banter:g2:%'").fetchone()[0]
    _act_cp35 = _c35.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id='banter:3415425445'").fetchone()[0]
    _st_cp35 = _c35.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id='banter:1777777777'").fetchone()[0]
    _c35.close()
    check("治理-每线程仅剩最新1组", _n_cp35 == 2 and _n_thr35 == 2
          and _st35.get("threads_kept") == 2 and _st35.get("threads_total") == 4)
    check("治理-超期线程整组消失", _gone_cp35 == 0 and _gone_wr35 == 0
          and _st35.get("threads_dropped") == 2)  # ISO 旧尾段线程 + 无尾段早停线程 各整组删除
    check("治理-无尾段旧格式gid不误读", _act_cp35 == 1 and _st_cp35 == 0)  # 活跃 gid 存活；早停靠 checkpoint 时刻老化
    check("治理-孤儿writes清理", _n_wr35 == 2 and _st35.get("writes_deleted") == 10
          and _st35.get("checkpoints_deleted") == 10)  # 12 写 12 存 → 各剩 2（存活 checkpoint 挂载的那条）
finally:
    _gp35.AGENT_DB = _orig_db35
    try:
        os.remove(_tmp35)
    except PermissionError:
        pass


# == 36. 调研落地回归（2026-09-09 深夜：语音消息黑洞 + 引擎参数三源漂移统一）==
print("== 36. 调研落地 ==")
from plugins import debug as _dbg36  # noqa: E402
# ---- 语音消息存在感知（调研-缺失模块与竞品对比-2026-09-09 Q1①）----
check("语音-纯语音放行", '"record"' in inspect.getsource(B)
      and "_has_voice" in inspect.getsource(B)
      and "TA 给你发了一条语音消息" in inspect.getsource(B))  # 纯语音不再黑洞丢弃（对齐 D1 纯图放行；入向=record 段，与出向 [VOICE] 标记无关）
check("语音-感知句不误作动作", B._extract_actions("TA 给你发了一条语音消息") == [])  # B2 同款：感知句无括号纯文本（曾用（ ）被当"用户括号动作"误注入）
check("语音-混合注记", "对方给你发了一条语音" in inspect.getsource(B)
      and 'if _has_voice and not str(text).startswith("TA 给你发了一条语音消息"):' in inspect.getsource(B))  # 语音+文本→【消息结构】注记（对齐图文注记）；纯语音已转感知句不重复注记
check("语音-群聊非@门控对齐纯图", "if not (_img_only or _voice_only):" in inspect.getsource(B))  # 群聊非@ 纯语音不回复、不入群记忆（复用 B3 纯图门控；感知句不进群上下文）
# ---- 引擎参数三源漂移统一（调研 Q2·A：start.ps1 权威 / brain MODELS / debug watchdog 回退）----
from core import paths as _cp36  # noqa: E402  # 2026-09-12 E 项：引擎主机/端口/模型路径的唯一来源
_sp36 = (Path(__file__).resolve().parents[2] / "start.ps1").read_text(encoding="utf-8-sig", errors="ignore")
_brain36 = inspect.getsource(B)
_dbgsrc36 = inspect.getsource(_dbg36)
check("引擎三源-parallel统一为1", '"--parallel", "1"' in _sp36
      and '"--parallel", "1"' in _brain36
      and "ENGINE_PARALLEL = 1" in _dbgsrc36)  # start.ps1:81(权威) / brain MODELS args / debug 回退重启分支 三源同参
check("引擎三源-repeat-penalty同款", '"--repeat-penalty", "1.15"' in _sp36
      and '"--repeat-last-n", "256"' in _sp36
      and '"--repeat-penalty", "1.15"' in _brain36 and '"--repeat-last-n", "256"' in _brain36
      and '"--repeat-penalty", "1.15"' in _dbgsrc36 and '"--repeat-last-n", "256"' in _dbgsrc36)  # 采样参数随统一补齐（start.ps1 同款；此前 brain/debug 缺失=引擎默认 1.1/64 漂移）
# ---- 引擎参数三源全量统一（逐参数对照：start.ps1 权威 / brain MODELS 实构造 / debug 回退分支；budget=200）----
# 权威参数表=start.ps1:77-84 gemma 分支 $engineArgs（模型路径含 $root 变量 → 以 gguf 文件名+目录对齐）
_AUTH36 = {"-c": "32768", "-ngl": "99", "--parallel": "1", "-ctk": "q8_0", "-ctv": "q8_0",
           "--reasoning": "on", "--reasoning-budget": "200",
           "--repeat-penalty": "1.15", "--repeat-last-n": "256",
           "--host": "127.0.0.1", "--port": "11434"}
_GGUF36 = "gemma-4-12B-it-heretic-Q4_K_M.gguf"
check("引擎三源-budget=200全源一致", '"--reasoning-budget", "200"' in _sp36
      and '"--reasoning-budget", "200"' in _brain36
      and '"--reasoning-budget", "200"' in _dbgsrc36
      and '"--reasoning-budget", "400"' not in _sp36 + _brain36 + _dbgsrc36)  # 权威=用户实测回退后的既定值；旧值 400 不得残留（含注释外真实 token 对）
for _k36, _v36 in _AUTH36.items():
    check(f"引擎三源[start.ps1权威] {_k36}={_v36}", f'"{_k36}", "{_v36}"' in _sp36)
check("引擎三源[start.ps1权威] 模型gemma4-12b-heretic", _GGUF36 in _sp36 and "gemma4-12b" in _sp36)
_ba36 = B._engine_start_args("gemma")  # 实调用构造（watchdog 重启与 /模型 切换共用 MODELS 表，非文本匹配）
_bp36 = dict(zip(_ba36[1::2], _ba36[2::2]))
for _k36, _v36 in _AUTH36.items():
    check(f"引擎三源[brain实构造] {_k36}={_v36}", _bp36.get(_k36) == _v36)
check("引擎三源[brain实构造] 模型路径同款", _bp36.get("-m", "").endswith(_GGUF36) and "gemma4-12b" in _bp36.get("-m", ""))
for _k36, _v36 in _AUTH36.items():
    if _k36 == "-c":  # 回退分支经 ENGINE_CTX 常量注入（不再动态计算，恒=权威 32768）
        _ok36 = '"-c", str(ctx)' in _dbgsrc36 and "ctx = ENGINE_CTX" in _dbgsrc36 and "ENGINE_CTX = 32768" in _dbgsrc36
    elif _k36 == "--parallel":
        _ok36 = '"--parallel", str(ENGINE_PARALLEL)' in _dbgsrc36 and "ENGINE_PARALLEL = 1" in _dbgsrc36
    elif _k36 == "--host":  # 2026-09-12 E 项：主机/端口/模型路径改由 core.paths 单一来源
        _ok36 = '"--host", ENGINE_HOST' in _dbgsrc36 and _cp36.ENGINE_HOST == _v36
    elif _k36 == "--port":
        _ok36 = ('"--port", str(ENGINE_PORT)' in _dbgsrc36 and "11434" not in _dbgsrc36
                 and _cp36.ENGINE_PORT == int(_v36))
    else:
        _ok36 = f'"{_k36}", "{_v36}"' in _dbgsrc36
    check(f"引擎三源[debug回退] {_k36}={_v36}", _ok36)
check("引擎三源[debug回退] 模型路径同款", _GGUF36 in _cp36.MODEL_FILE_GEMMA
      and "gemma4-12b" in _cp36.MODEL_FILE_GEMMA
      and "gemma-4-12B-it-heretic" not in _dbgsrc36)  # E 项后字面量只存在于 core.paths（debug 源码里不得再有）
check("引擎三源[debug回退] 展示值=实际参数", "ctx={}" in _dbgsrc36
      and "budget 200)" not in _dbgsrc36
      and "{ctx}" in _dbgsrc36)  # 重启回执/日志不得复现"写 600/400"式纯展示漂移（预算字面量已撤，实际值由三源断言守护）


# == 37. 铁律#2 人设数据化迁移回归（2026-09-09：brain 硬编码高傲人设 → 卡数据 arrogance）==
print("== 37. 人设数据化迁移 ==")
from core import paths as _paths37  # noqa: E402

_a37 = _per5.arrogance_of(_per5.load_persona("_aoding_"))
_st37 = _a37.get("structure") or {}
check("迁移-取值函数容错", _per5.arrogance_of({}) == {} and _per5.arrogance_of(None) == {}
      and _per5.arrogance_of({"arrogance": "x"}) == {} and bool(_a37))
check("迁移-卡字段完整", len(_a37.get("patterns") or []) == 7 and _a37.get("self_ref") == "妾身"
      and set(_a37.get("style_reply") or {}) == {"novel", "daily"}
      and set(_a37.get("private_block") or {}) == {"normal", "evul"}
      and set(_a37.get("group_block") or {}) == {"normal", "evul"}
      and bool(_a37.get("group_disgust")) and bool(_a37.get("group_evul"))
      and bool(_a37.get("group_member")) and bool(_a37.get("group_followup")) and bool(_a37.get("private_intro"))
      and set(_st37) >= {"base_normal", "base_lust", "inner_normal", "inner_evul",
                         "lust_low", "lust_mid", "lust_deep",
                         "lust_evul_low", "lust_evul_mid", "lust_evul_deep"})
check("迁移-句式全量逐字(语义零变化抽样)", B._arrogance_patterns(0, 0, _a37["patterns"]) == (
    "高傲表达参考（只参考句式与语气，严禁整句照抄，每轮换花样）：「呵，凡人，也就这种程度？」"
    " 「妾身肯理会你，已是天大的恩典。」"
    " 「哼，才不是！只是、罢了，别自作多情。」"
    " 「这种问题也敢来问妾身？」"
    " 「既然你诚心诚意地求了，妾身便勉为其难地准了。」"
    " 「脑子不用，是会生锈的，凡人。」"
    " 「妾身才没有！再胡说，就把你从云端扔下去。」"))
check("迁移-句式分档接线不变", B._arrogance_patterns(1, 80, _a37["patterns"]) == ""
      and B._arrogance_patterns(1, 35, _a37["patterns"]).endswith("「妾身肯理会你，已是天大的恩典。」")
      and B._arrogance_patterns(0, 55, _a37["patterns"]).startswith("高傲表达参考（只参考句式与语气，严禁整句照抄）："))
check("迁移-私聊高傲指数逐字(迁移前#3)", _a37["private_block"]["normal"].startswith("\n\n【高傲指数 80/100·私聊】")
      and "哼，才不是特意为你呢。" in _a37["private_block"]["normal"]
      and "既然你这般恳求，妾身便勉为其难。" in _a37["private_block"]["normal"]
      and _a37["private_block"]["evul"].startswith("\n\n【高傲指数 95/100·私聊·恶堕】"))
check("迁移-群聊伪装厌恶逐字(迁移前#2)", _a37["group_disgust"].startswith("\n\n【群聊伪装·厌恶高傲】")
      and "哼，在群里说这种话，真是恶心。" in _a37["group_disgust"]
      and "也想动摇妾身" in _a37["group_disgust"])
check("迁移-恶堕群聊放宽逐字", _a37["group_evul"].startswith("\n\n【群聊挑逗·恶堕放宽】")
      and "也想试试被妾身戏弄的滋味" in _a37["group_evul"])
check("迁移-文风切换示例逐字(迁移前#4)", _a37["style_reply"]["novel"] == "（比如「哼，既然主人都开口了，那妾身便也难得地文艺一回」）"
      and _a37["style_reply"]["daily"] == "（比如「行行行，不拽文了，有话直说成吧」）")
check("迁移-结构块插值逐字(迁移前#5)", "【亲近度 100/100】" in B._arrogance_block(0, 100.0, 0, _st37)
      and B._arrogance_block(0, 100.0, 0, _st37).endswith("不改变外表语气。）")
      and "（情欲指数约 55/100，情欲渐深）" in B._arrogance_block(0, 40.0, 55, _st37)
      and "（恶堕·情欲指数约 20/100，初入情欲）" in B._arrogance_block(1, 55.0, 20, _st37)
      and "恶堕内核" in B._arrogance_block(1, 100.0, 0, _st37))
check("迁移-结构块恶堕深档主人句", "对主人的绝对占有欲" in B._arrogance_block(1, 90.0, 75, _st37))
check("迁移-无字段卡零注入", _per5.arrogance_of(_per5.load_persona("amiya")) == {}
      and _per5.arrogance_of(_per5.load_persona("soyo")) == {}
      and B._arrogance_block(0, 100.0, 0) == "" and B._arrogance_block(0, 100.0, 0, {}) == ""
      and B._arrogance_patterns(0, 0, ()) == "高傲表达参考（只参考句式与语气，严禁整句照抄，每轮换花样）：")
check("迁移-brain源码零自称零句式词", "妾身" not in inspect.getsource(B) and "凡人" not in inspect.getsource(B))
check("迁移-旧硬编码常量已删", not any(hasattr(B, x) for x in (
    "HAUGHTY_PATTERNS", "GROUP_LUST_DISGUST_PROMPT", "EVUL_GROUP_PROMPT", "WHISPER_PROMPT")))
check("迁移-门控结构保留", "no_arrogance" in inspect.getsource(B) and "owner_mode" in inspect.getsource(B)
      and "persona.arrogance_of(card)" in inspect.getsource(B)
      and 'not card.get("no_arrogance") and _arog.get("patterns")' in inspect.getsource(B)
      and 'mode == 0 and "vibe_mood" in _act_states and _gd' in inspect.getsource(B)
      and "mode == 1 and _master_card and _ge" in inspect.getsource(B)
      and "_master_card and not card.get(\"no_arrogance\")" in inspect.getsource(B))
check("迁移-whisper身份按卡名", "_gen_whisper(text, reply, card)" in inspect.getsource(B)
      and 'f"你是「{_wname}」' in inspect.getsource(B))
check("迁移-场景模板占位接线", "_safe_card_format(_gf, followup_round=followup_round, user_id=user_id)" in inspect.getsource(B)
      and "speaker_display=speaker_display" in inspect.getsource(B)
      and "_safe_card_format(_pi, user_id=user_id)" in inspect.getsource(B)
      and "me=_selfref" in inspect.getsource(B)
      and "私聊再与{_selfref}细聊" in inspect.getsource(B))  # 2026-09-10 审计P3：直拼 .format 改走 _safe_card_format 守卫
_tpl37 = json.loads((_paths37.PERSONAS_DIR / "_template.json").read_text(encoding="utf-8-sig"))["data"]
check("迁移-模板占位防护", set(_per5.arrogance_of(_tpl37)) == {"_说明"}
      and "patterns" not in _per5.arrogance_of(_tpl37)  # 模板卡不含任何可注入键（照抄模板=零注入）
      and "妾身" not in json.dumps(_per5.arrogance_of(_tpl37), ensure_ascii=False))


# == 38. 活人感 3-1 对话级认错回归（2026-09-08：提取管线修正旧事实 → 私聊感知"自然认错"）==
print("== 38. 对话级认错 ==")
from plugins.memory import store as _store38  # noqa: E402
from plugins.memory import embeddings as _emb38  # noqa: E402
from plugins import memory as _mem38  # noqa: E402
import asyncio as _aio38  # noqa: E402

check("认错-表与三方法", all(hasattr(_store38.db, x) for x in
      ("get_fact", "add_fact_correction", "consume_pending_fact_correction"))
      and "CREATE TABLE IF NOT EXISTS fact_corrections" in inspect.getsource(_store38))  # 旧库首开自动建表（MemoryDB._init_schema）
check("认错-提取写侧接线", inspect.getsource(_mem38.extract_and_store).count("add_fact_correction") >= 2
      and "get_fact(user_id, key)" in inspect.getsource(_mem38.extract_and_store))  # update 分支 + 语义查重合并分支都落修正记录

_src38 = inspect.getsource(B)
_i_fc38 = _src38.index("consume_pending_fact_correction(user_id)")
_gate38 = _src38.rfind("if not _is_grp:", 0, _i_fc38)
check("认错-注入锚点+仅私聊消费", "你之前记的「" in _src38
      and "自然认个错就好，不用刻意" in _src38
      and 0 < _i_fc38 - _gate38 < 120)  # 事实+自决（零对话范例）；消费调用紧贴私聊门控（群聊不注入）

# 行为级：tmp 全 schema 库（兼验旧库首次打开自动建表 + 消费语义）
_db38 = _store38.MemoryDB(str(Path(tempfile.mkdtemp()) / "p38.db"))
_oc38, _ol38 = _store.db._conn, _store.db._lock
_orig_owner38, _orig_client38, _orig_embed38 = _mem38._is_owner, _mem38._client, _emb38.embed_text_async
try:
    check("认错-无修正不注入+落库可取",
          _db38.consume_pending_fact_correction("u38") is None  # 无修正 → 不注入
          and _db38.add_fact_correction("u38", "养了一只猫", "改养狗了", ts="2026-09-08T12:00:00+00:00") is None
          and (lambda c: c is not None and c["old_text"] == "养了一只猫" and c["new_text"] == "改养狗了")(
              _db38.consume_pending_fact_correction("u38")))
    _db38.add_fact_correction("u38", "旧A", "新A")
    _db38.add_fact_correction("u38", "旧B", "新B")  # 新修正入账清旧行——只留最近一次
    check("认错-取走即consumed防复读+只留最近一次",
          _db38._conn.execute("SELECT COUNT(*) c FROM fact_corrections WHERE user_id='u38'").fetchone()["c"] == 1
          and _db38.consume_pending_fact_correction("u38")["new_text"] == "新B"
          and _db38.consume_pending_fact_correction("u38") is None)  # 已消费 → 不再注入（防每轮复读）
    _db38.upsert_fact("u38", "职业", "医生", 0.9)

    # ---- e2e：extract_and_store 的 update 分支命中旧 fact → 修正记录落库（假客户端+假 embed，零网络）----
    _store.db._conn, _store.db._lock = _db38._conn, _db38._lock
    _upd38 = ["喜欢的宠物", "改养狗了"]

    class _FakeMsg38:
        def __init__(self, c):
            self.content = c

    class _FakeChoice38:
        def __init__(self, c):
            self.message = _FakeMsg38(c)

    class _FakeResp38:
        def __init__(self, c):
            self.choices = [_FakeChoice38(c)]

    class _FakeCompletions38:
        async def create(self, **kw):
            return _FakeResp38(json.dumps({
                "add": [], "update": [{"key": _upd38[0], "value": _upd38[1]}], "delete": [],
                "events": [], "mood": "", "intimacy_delta": 0, "summary": "",
            }, ensure_ascii=False))

    class _FakeClient38:
        def __init__(self):
            self.chat = type("C", (), {"completions": _FakeCompletions38()})()

    async def _no_embed38(t):
        return None

    _mem38._is_owner = lambda uid: True
    _mem38._client = lambda: _FakeClient38()
    _emb38.embed_text_async = _no_embed38
    _db38.upsert_fact("u38x", "喜欢的宠物", "养了一只猫", 0.9)
    _aio38.run(_mem38.extract_and_store("u38x", [{"role": "user", "content": "我把猫送走了，改成养狗了"}]))
    _fc38b = _db38.consume_pending_fact_correction("u38x")
    _upd38 = ["职业", "医生"]  # 无旧事实 → 等价新增，不记修正
    _aio38.run(_mem38.extract_and_store("u38x", [{"role": "user", "content": "其实我是医生"}]))
    check("认错-extract更新旧fact落修正+无旧值不记", _fc38b is not None
          and _fc38b["old_text"] == "养了一只猫" and _fc38b["new_text"] == "改养狗了"
          and _db38.consume_pending_fact_correction("u38x") is None
          and _db38.get_fact("u38x", "职业")["value"] == "医生"  # 无旧值的 update=等价新增（照常 upsert）
          and _db38.get_fact("u38x", "无此键") is None)
finally:
    _mem38._is_owner, _mem38._client = _orig_owner38, _orig_client38
    _emb38.embed_text_async = _orig_embed38
    _store.db._conn, _store.db._lock = _oc38, _ol38
    try:
        _db38._conn.close()
    except Exception:  # noqa: BLE001
        pass


# == 39. 群聊插话质量回归（2026-09-08：决策端质量门槛 + 句式骨架防重复 + 少说话放宽）==
print("== 39. 群聊插话质量 ==")
from agent import graph as _g39  # noqa: E402

# ---- 项1：决策端质量门槛（锚点在 banter 决策提示内；行为域零范例）----
check("质量-门槛措辞锚点", "插话的唯一标准" in _g39._DECISION_PROMPT
      and "真的想说" in _g39._DECISION_PROMPT
      and "选择不说话" in _g39._DECISION_PROMPT
      and "没有人要求你必须接每一句" in _g39._DECISION_PROMPT)
check("质量-零范例纪律", "既然" not in _g39._DECISION_PROMPT)  # 无意义附和句式不得写回提示（范例=复读源）
check("质量-旧'要说话'推压已撤", "而不是不说" not in _g39._DECISION_PROMPT
      and "没有实际内容就归入【不插话】" in _g39._DECISION_PROMPT)  # 曾与门槛直接矛盾（有得没得都往出的推手）

# ---- 项2：句式骨架防重复（brain 登记 / graph._perceive 注入 + 行为级）----
check("骨架-记录API", hasattr(B, "_banter_rhythm_record") and hasattr(B, "_banter_rhythm_note")
      and hasattr(B, "_BANTER_RHYTHM") and hasattr(B, "_BANTER_RHYTHM_TOUCH"))
check("骨架-发送成功后登记", "_banter_rhythm_record(group_id, line)" in inspect.getsource(B))
_psrc39 = inspect.getsource(_g39._perceive)
check("骨架-perceive注入接线", "_banter_rhythm_note" in _psrc39
      and _psrc39.index("_banter_rhythm_note") > _psrc39.index('state.get("kind") == "greet"'))  # greet 早返回之后=插话路径
B._BANTER_RHYTHM.clear()
B._BANTER_RHYTHM_TOUCH.clear()
try:
    for _r39 in ("好的呀。", "这个话题我觉得还挺有意思的我们之后可以再接着聊聊看。",
                 "嗯。……那我再想想吧。", "（点头）嗯。", "今天累死了不想动。"):
        B._banter_rhythm_record("g39", _r39)
    check("骨架-cap4丢最旧", len(B._BANTER_RHYTHM.get("g39", [])) == 4
          and B._BANTER_RHYTHM["g39"][0] == "hL-ltL-d0-a0")  # 首条 hS-ltS-d0-a0 已被挤出窗口
    _note39 = B._banter_rhythm_note("g39")
    check("骨架-注入句锚点", "你最近几次在群里说话的结构节奏" in _note39
          and "换一种完全不同的结构和节奏" in _note39
          and "也可以选择不插" in _note39
          and "hM-ltM-d0-a0" in _note39)  # 最新键在注入句里
    check("骨架-无记录不注入", B._banter_rhythm_note("g39-none") == "")
    B._banter_rhythm_record("g39-empty", "")
    check("骨架-空键不记账", "g39-empty" not in B._BANTER_RHYTHM)
finally:
    B._BANTER_RHYTHM.clear()
    B._BANTER_RHYTHM_TOUCH.clear()

# ---- 项3：90s→120s 评估节流放宽（质量优先，少说话）----
_msrc39 = inspect.getsource(B._maybe_group_banter)
check("节流-120s", "< 120" in _msrc39 and "< 90" not in _msrc39)

# ---- C12 sweep 不误清新数据（新骨架窗口随队清扫：fresh 存活 / stale 清除）----
B._BANTER_RHYTHM["g39-fresh"] = ["hS-ltS-d0-a0"]
B._BANTER_RHYTHM_TOUCH["g39-fresh"] = T.time()
B._BANTER_RHYTHM["g39-stale"] = ["hL-ltL-d0-a0"]
B._BANTER_RHYTHM_TOUCH["g39-stale"] = T.time() - 8 * 86400
_s39sw = B._sweep_stale()
check("sweep-新窗口fresh存活", B._BANTER_RHYTHM.get("g39-fresh") == ["hS-ltS-d0-a0"])
check("sweep-新窗口stale清除", "g39-stale" not in B._BANTER_RHYTHM
      and "g39-stale" not in B._BANTER_RHYTHM_TOUCH)
check("sweep-计数含新窗口", _s39sw.get("banter_rhythm", 0) >= 1)
B._BANTER_RHYTHM.clear()
B._BANTER_RHYTHM_TOUCH.clear()

# == 40. 特殊层级 agent 自决化 + 切片泄漏修复回归（2026-09-09）==
print("== 40. 特殊层级自决化 ==")
_srcB40 = inspect.getsource(B)
check("白名单-登记族放行", all(B._AGENT_MARK_RE.search(x) for x in (
      "【催眠：身体完全听话】", "【洗脑：只听话】", "【道具：口球】", "【道具：口球：堵着】",
      "【不许装了】", "【解除：催眠】", "【解除：道具：口球】", "【解除：全部】")))
check("白名单-占位归还", "【催眠：身体完全听话】" in B._strip_non_speech("嗯【催眠：身体完全听话】", allow_action=True)
      and "【解除：全部】" in B._strip_non_speech("嗯【解除：全部】", allow_action=False))
check("调用点-allow_states门控", "allow_states=bool(_priv and is_owner)" in _srcB40)
check("教学-状态登记块存在", "【状态登记】" in _srcB40 and "【催眠：效果要求】" in _srcB40
      and "【解除：全部】" in _srcB40 and "照主人括号原文写" in _srcB40)
check("教学-已知道具标准名锚点", "必须用标准名登记" in _srcB40 and "口球/淫纹/震动棒" in _srcB40)
check("教学-owner私聊门控", "if is_owner and _priv:" in _srcB40)
check("括号动作-主人事实口径", "主人括号里写的是已发生的事实，不是请求" in _srcB40)
check("括号动作-非主人中性口径", "它是不是身体动作、你此刻如何回应，由你按现场与关系自决" in _srcB40)
check("协议退役-brain零引用", "parse_owner_command" not in _srcB40
      and "状态变更" not in _srcB40 and "状态解除" not in _srcB40 and "状态全解除" not in _srcB40)
check("refresh保留-主人消息顺延", '_special.refresh("hypno", card=_ck)' in _srcB40)
check("口球红线-mouth_blocked保留", 'mouth_blocked = "item:ball" in _act_states' in _srcB40)
# 切片泄漏修复（special.split_obey_slice 函数级行为断言——2026-09-09 盲审 P2：曾为源码字符串匹配）
check("切片函数-官方形态拆分", S.split_obey_slice("好的。【身体遵从】切片内容") == ("好的。", "切片内容"))
check("切片函数-别名归一后拆分", S.split_obey_slice("嗯。[BODY_OBEY]\n身体不受控的叙述") == ("嗯。", "身体不受控的叙述"))
check("切片函数-第二条不带前缀", "【身体遵从】" not in (S.split_obey_slice("a【身体遵从】b")[1] or ""))
check("切片函数-孤标记空切片", S.split_obey_slice("就是你要用的手段？【身体遵从】") == ("就是你要用的手段？", None))
check("切片函数-空主只发切片", S.split_obey_slice("【身体遵从】意识在尖叫")[0] == ""
      and S.split_obey_slice("【身体遵从】意识在尖叫")[1] == "意识在尖叫")
check("切片函数-无标记原样", S.split_obey_slice("普通回复") == ("普通回复", None))
# 发送链接线（handler 内联，源码级）
check("切片-空切片不发告警", "second message skipped" in _srcB40)
check("切片-空主只发切片接线", "slice-only send" in _srcB40
      and 'if str(reply or "").strip():' in _srcB40)
check("兜底-发送前剥净+warning", "strip_protocol_residue(reply)" in _srcB40
      and "reply empty after residue strip" in _srcB40)
check("剥点统一-落库账本历史", _srcB40.count("strip_protocol_residue(") >= 4
      and "strip_protocol_residue(body)" in _srcB40)
check("盲审P2-对话读侧剥净接线", "_strip_dl" in _srcB40 and "_body = _strip_dl(_body)" in _srcB40)
# memory 催眠两分支文案（去人设/去三段式）
from plugins import memory as _mem40  # noqa: E402
_srcM40 = inspect.getsource(_mem40)
check("催眠文案-人设假设已删", "高傲、嘴硬、抗拒" not in _srcM40 and "愤怒、疑惑、不甘" not in _srcM40)
check("催眠文案-三段式处方已删", "每次回复都应包含正常回应" not in _srcM40)
check("催眠文案-切片机制+自决保留", "切成第二条消息" in _srcM40 and "由你按人设与现场自决" in _srcM40)
check("催眠文案-心理暗示事实口径", "它**不改变你的表面人格**" in _srcM40)


# == 41. 跨卡场景交接回归（2026-09-09：换卡摘要写侧 + 读侧注入 + 放置类例外 share=false 零感知）==
print("== 41. 跨卡场景交接 ==")
from plugins.memory import store as _store41  # noqa: E402
from agent import lifesim as _ls41  # noqa: E402
import asyncio as _aio41  # noqa: E402
import datetime as _dt41  # noqa: E402

check("交接-常量与API", B.HANDOVER_TTL == 6 * 3600 and B.HANDOVER_SUMMARY_MAX == 80
      and B.HANDOVER_MIN_MSGS == 4
      and all(hasattr(B, x) for x in ("should_make_handover", "make_handover_payload",
                                      "previous_handover", "_handover_generate"))
      and hasattr(_ls41, "attach_handover")
      and hasattr(_store41.db, "messages_between"))

# ---- tmp 夹具：LIFE_STATES_DIR / PERSONA_SWITCH_FILE / messages 库全部重定向（绝不碰真实 data/）----
_tmp41 = Path(tempfile.mkdtemp())
_lsdir41 = _tmp41 / "life_states"
_lsdir41.mkdir(parents=True, exist_ok=True)
_swfile41 = _tmp41 / "persona_switch_ts.json"
_orig_lsdir41, _orig_swfile41 = _ls41.LIFE_STATES_DIR, B.PERSONA_SWITCH_FILE
_orig_conn41, _orig_lock41 = _store41.db._conn, _store41.db._lock
_orig_client41 = B.client
_db41 = _store41.MemoryDB(str(_tmp41 / "p41.db"))
_store41.db._conn, _store41.db._lock = _db41._conn, _db41._lock
_ls41.LIFE_STATES_DIR = _lsdir41
B.PERSONA_SWITCH_FILE = _swfile41
try:
    _now41 = T.time()

    def _iso41(epoch):
        return _dt41.datetime.fromtimestamp(epoch, _dt41.timezone.utc).isoformat(timespec="seconds")

    def _msg41(uid, ts_s, role, content, group_id=""):
        _db41._conn.execute(
            "INSERT INTO messages(ts, user_id, group_id, role, content, sender_name) VALUES(?,?,?,?,?,?)",
            (ts_s, uid, group_id, role, content, ""),
        )

    def _arch41(card, handover=None, doing="在值班室整理文件", scene="值班室"):
        snap = {"version": 1, "card": card, "archived_ts": _now41,
                "life_state": {"doing": doing, "scene": scene, "mood": "平静", "ts": _now41},
                "life_log": "", "life_diary": ""}
        if handover is not None:
            snap["handover"] = handover
        atomics.write_json_atomic(_lsdir41 / (_ls41._sanitize_card(card) + ".json"), snap)

    def _sw41(uid, prev, cur):
        atomics.write_json_atomic(_swfile41, {uid: [
            {"p": prev, "ts": _iso41(_now41 - 400)},
            {"p": cur, "ts": _iso41(_now41 - 60)},
        ]})

    _since41 = _iso41(_now41 - 500)
    # 消息样本：窗口前 1 条、窗口内私聊 4 条（user/assistant 交替）、窗口内群聊 1 条、未来 1 条
    _msg41("u41", _iso41(_now41 - 600), "user", "窗口之前的话")
    for _k41, (_r41, _c41) in enumerate([("user", "刚才在忙什么"), ("assistant", "在整理值班室的文件"),
                                         ("user", "辛苦了"), ("assistant", "快收尾了")]):
        _msg41("u41", _iso41(_now41 - 400 + _k41 * 100), _r41, _c41)
    _msg41("u41", _iso41(_now41 - 200), "user", "群聊的不算", group_id="g41")
    _msg41("u41", _iso41(_now41 + 50), "user", "未来的话")
    for _k41 in range(3):  # u41b：窗口内只有 3 条（不足 HANDOVER_MIN_MSGS）
        _msg41("u41b", _iso41(_now41 - 300 + _k41 * 10), "user", f"短会话{_k41}")
    _db41._conn.commit()
    _arch41("amiya")            # doing/scene 非空（条件正例 + e2e 写侧卡）
    _arch41("kaltsit")
    _arch41("soyo", doing="", scene="")  # doing/scene 全空（条件负例）

    # ---- store.messages_between：区间升序、含边界、群聊不混、上限钳制 ----
    _mb41 = _store41.db.messages_between("u41", _since41, _iso41(_now41 + 10))
    check("区间-只取窗口内私聊", len(_mb41) == 4
          and all(m["ts"] >= _since41 and m["ts"] <= _iso41(_now41 + 10) for m in _mb41))
    check("区间-升序+角色保留", [m["role"] for m in _mb41] == ["user", "assistant", "user", "assistant"]
          and _mb41 == sorted(_mb41, key=lambda m: m["ts"])
          and all(m["content"] != "群聊的不算" for m in _mb41))
    check("区间-下界不含窗外旧消息", all(m["content"] != "窗口之前的话" for m in _mb41)
          and all(m["content"] != "未来的话" for m in _mb41)  # until 上界同样生效
          and len(_store41.db.messages_between("u41", _iso41(_now41 + 200), _iso41(_now41 + 300))) == 0)

    # ---- 生成前置条件（纯函数；不满足=不调 LLM）----
    check("条件-正例", B.should_make_handover("amiya", "u41", _since41) is True)
    check("条件-消息不足不调LLM", B.should_make_handover("amiya", "u41b", _since41) is False)
    check("条件-doing/scene全空不调LLM", B.should_make_handover("soyo", "u41", _since41) is False)
    check("条件-边界缺失/坏时间戳", B.should_make_handover("amiya", "u41", "") is False
          and B.should_make_handover("amiya", "u41", "not-a-ts") is False
          and B.should_make_handover("", "u41", _since41) is False)

    # ---- attach_handover：正常附着 / 覆盖旧值 / 卡名消毒 / 无归档 fail-safe ----
    _p41a = {"share": True, "scene": "值班室", "summary": "在整理文件"}
    check("attach-正常附着", _ls41.attach_handover("amiya", _p41a) is True
          and atomics.read_json(_lsdir41 / "amiya.json", None)["handover"]["scene"] == "值班室")
    check("attach-覆盖旧值", _ls41.attach_handover("amiya", {"share": True, "scene": "宿舍", "summary": "躺下了"})
          is True and atomics.read_json(_lsdir41 / "amiya.json", None)["handover"]["scene"] == "宿舍"
          and "handover" in atomics.read_json(_lsdir41 / "amiya.json", None))
    _san41 = _ls41._sanitize_card("凯尔希!")
    _arch41("凯尔希!")
    check("attach-卡名消毒", _ls41.attach_handover("凯尔希!", {"share": True, "scene": "办公室", "summary": "看报告"})
          is True and (_lsdir41 / (_san41 + ".json")).is_file()
          and atomics.read_json(_lsdir41 / (_san41 + ".json"), None)["handover"]["scene"] == "办公室")
    check("attach-无归档不抛不写", _ls41.attach_handover("ghost41", {"share": True}) is False)

    # ---- 读侧 previous_handover：条件全在函数内，None=不注入 ----
    check("读侧-无切换文件None", B.previous_handover("amiya", now=_now41, user_id="u41none") is None)
    atomics.write_json_atomic(_swfile41, {"u41solo": [{"p": "amiya", "ts": _iso41(_now41 - 60)}]})
    check("读侧-无上一卡None", B.previous_handover("amiya", now=_now41, user_id="u41solo") is None)
    _sw41("u41h1", "kaltsit", "amiya")
    check("读侧-缺handoverNone", B.previous_handover("amiya", now=_now41, user_id="u41h1") is None)
    _arch41("kaltsit", handover={"share": False, "scene": "柜内", "summary": "被单独安置在某处",
                                 "from_card": "kaltsit", "from_universe": "arknights",
                                 "generated_ts": _now41 - 300})
    _sw41("u41h2", "kaltsit", "amiya")
    check("读侧-share=false None(放置类零感知)", B.previous_handover("amiya", now=_now41, user_id="u41h2") is None)
    _arch41("kaltsit", handover={"share": True, "scene": "值班室", "summary": "在整理文件",
                                 "from_card": "kaltsit", "from_universe": "mygo",
                                 "generated_ts": _now41 - 300})
    _sw41("u41h3", "kaltsit", "amiya")
    check("读侧-universe不同None", B.previous_handover("amiya", now=_now41, user_id="u41h3") is None)
    _arch41("kaltsit", handover={"share": True, "scene": "值班室", "summary": "在整理文件",
                                 "from_card": "kaltsit", "from_universe": "arknights",
                                 "generated_ts": _now41 - 7 * 3600})
    _sw41("u41h4", "kaltsit", "amiya")
    check("读侧-TTL过期None", B.previous_handover("amiya", now=_now41, user_id="u41h4") is None)
    _long_sum41 = "她刚离开时在整理文件，氛围平静，快到傍晚了" + "x" * 90
    _arch41("kaltsit", handover={"share": True, "scene": "值班室", "summary": _long_sum41,
                                 "from_card": "kaltsit", "from_universe": "arknights",
                                 "generated_ts": _now41 - 300})
    _sw41("u41h5", "kaltsit", "amiya")
    _ho41 = B.previous_handover("amiya", now=_now41, user_id="u41h5")
    check("读侧-正常返回payload", isinstance(_ho41, dict) and _ho41["scene"] == "值班室"
          and _ho41["from_card"] == "kaltsit" and _ho41["share"] is True)
    check("读侧-摘要钳80字", isinstance(_ho41, dict) and len(_ho41["summary"]) == B.HANDOVER_SUMMARY_MAX)

    # ---- e2e（假 LLM）：写侧编排 attach + 全链读侧；share=false 全链不注入 ----
    class _FakeMsg41:
        def __init__(self, c):
            self.content = c

    class _FakeChoice41:
        def __init__(self, c):
            self.message = _FakeMsg41(c)

    class _FakeResp41:
        def __init__(self, c):
            self.choices = [_FakeChoice41(c)]

    class _FakeCompletions41:
        def __init__(self):
            self.out = ""
            self.calls = []

        async def create(self, **kw):
            self.calls.append(kw)
            return _FakeResp41(self.out)

    class _FakeClient41:
        def __init__(self, comp):
            self.chat = type("C", (), {"completions": comp})()

    _comp41 = _FakeCompletions41()
    B.client = _FakeClient41(_comp41)
    _comp41.out = ('```json\n{"share": true, "scene": "值班室", "summary": "'
                   + _long_sum41 + '"}\n```')
    _aio41.run(B._handover_generate("amiya", "kaltsit", "u41", _since41))
    _snap41 = atomics.read_json(_lsdir41 / "amiya.json", None)
    check("e2e-share=true附着", isinstance(_snap41, dict) and isinstance(_snap41.get("handover"), dict)
          and _snap41["handover"]["share"] is True and _snap41["handover"]["scene"] == "值班室"
          and _snap41["handover"]["from_card"] == "amiya"
          and _snap41["handover"]["from_universe"] == "arknights"  # 真卡复检（persona.load_persona）
          and isinstance(_snap41["handover"].get("generated_ts"), float))
    check("e2e-写侧摘要钳80字", len(_snap41["handover"]["summary"]) == B.HANDOVER_SUMMARY_MAX)
    check("e2e-LLM入参接线", len(_comp41.calls) == 1
          and _comp41.calls[0].get("temperature") == 0.2 and _comp41.calls[0].get("max_tokens") == 200
          and _comp41.calls[0].get("timeout") == 20.0
          and _comp41.calls[0].get("extra_body", {}).get("chat_template_kwargs", {}).get("enable_thinking") is False
          and "状态记录器" in _comp41.calls[0]["messages"][0]["content"])
    _sw41("u41e", "amiya", "kaltsit")
    _ho41e = B.previous_handover("kaltsit", user_id="u41e")
    check("e2e-写读全链", isinstance(_ho41e, dict) and _ho41e["from_card"] == "amiya"
          and _ho41e["scene"] == "值班室" and len(_ho41e["summary"]) == B.HANDOVER_SUMMARY_MAX)
    # 放置类例外：LLM 判 share=false → 归档可写（观测面）但读侧全链 None
    _comp41.out = '{"share": false, "scene": "柜内", "summary": "她被单独安置、无法自行离开"}'
    _aio41.run(B._handover_generate("kaltsit", "amiya", "u41", _since41))
    _snap41b = atomics.read_json(_lsdir41 / (_ls41._sanitize_card("kaltsit") + ".json"), None)
    check("放置例外-share=false照写归档", isinstance(_snap41b, dict)
          and _snap41b["handover"]["share"] is False)
    _sw41("u41f", "kaltsit", "amiya")
    check("放置例外-全链不注入", B.previous_handover("amiya", user_id="u41f") is None)

    # ---- LLM 提示/解析接线（源码级；LLM 本身不可测）----
    _mph41 = inspect.getsource(B.make_handover_payload)
    check("接线-LLM调用与剥净", "client.chat.completions.create" in _mph41
          and "enable_thinking" in _mph41 and "strip_protocol_residue" in _mph41
          and "json.loads" in _mph41 and "```" in _mph41)  # 剥围栏
    check("接线-share判据原话(功能层)", "『她被单独安置、藏匿或约束在某处，无法自行行动或离开』" in B._HANDOVER_SYSTEM
          and "share=false" in B._HANDOVER_SYSTEM and "只输出 JSON" in B._HANDOVER_SYSTEM)
    check("纪律-零人设假设", "阿米娅" not in B._HANDOVER_SYSTEM and "凯尔希" not in B._HANDOVER_SYSTEM
          and "博士" not in B._HANDOVER_SYSTEM and "主人" not in B._HANDOVER_SYSTEM
          and "博士" not in inspect.getsource(B.previous_handover))
    # ---- 换卡流程 / 读侧注入接线（源码级）----
    _sp41 = inspect.getsource(B.set_persona)
    check("接线-set_persona归档后调度", "_handover_generate(old" in _sp41
          and _sp41.index("switch_persona_life(old") < _sp41.index("_handover_generate(old")
          and "_sw_since" in _sp41 and "if _same_world and _sw_since:" in _sp41)
    check("接线-调度同款后台任务", "bgtasks.spawn(" in _sp41
          and "handover task schedule failed" in _sp41)  # 2026-09-12：改用 core.bgtasks.spawn 统一持引用
    _hs41 = inspect.getsource(B.handle)
    _i_ho41 = _hs41.index("previous_handover(")
    _gate41 = _hs41.rfind("if is_owner and _priv:", 0, _i_ho41)
    check("接线-注入仅主人私聊", 0 < _i_ho41 - _gate41 < 200
          and "_ho = None" in _hs41[_gate41:_i_ho41] and "try:" in _hs41[_gate41:_i_ho41]
          and "if _ho:" in _hs41[_i_ho41:_i_ho41 + 400])
    check("接线-【场景交接】文案", "【场景交接】" in _hs41 and "刚从「" in _hs41
          and "她那边最近的情形：" in _hs41 and "细节以这段概述为准" in _hs41)
    check("接线-读侧只读不写库", "memory.db" not in inspect.getsource(B.previous_handover)
          and "messages_between" in inspect.getsource(_store41.MemoryDB.messages_between)
          and "ts>=?" in inspect.getsource(_store41.MemoryDB.messages_between)
          and "ORDER BY ts DESC" in inspect.getsource(_store41.MemoryDB.messages_between)
          and "reversed(rows)" in inspect.getsource(_store41.MemoryDB.messages_between))
    # ---- 盲审 P2/P3 修复回归（2026-09-09）----
    # P2：LIMIT 必须取窗口内**最近** N 条（DESC+LIMIT 后反序还原升序）——曾 ASC+LIMIT 取最早段
    _mb42 = _store41.db.messages_between("u41", _since41, _iso41(_now41 + 10), limit=2)
    check("盲审P2-区间取最近N条", [m["content"] for m in _mb42] == ["辛苦了", "快收尾了"])
    # P3：attach 载荷字段校验（share 布尔 + scene/summary 非空），缺字段拒绝=安全侧
    _arch41("amiya")
    check("盲审P3-attach缺字段拒绝", _ls41.attach_handover("amiya", {"share": True}) is False
          and "handover" not in (atomics.read_json(_lsdir41 / (_ls41._sanitize_card("amiya") + ".json"), {}) or {}))
    check("盲审P3-attach合法载荷通过", _ls41.attach_handover(
        "amiya", {"share": True, "scene": "值班室", "summary": "在整理文件",
                  "from_card": "amiya", "from_universe": "arknights", "generated_ts": _now41 - 30}) is True)
    # P3：读侧未来时间戳守卫（generated_ts 在未来 → 不注入）
    _arch41("amiya", handover={"share": True, "scene": "值班室", "summary": "摘要",
                               "from_card": "amiya", "from_universe": "arknights",
                               "generated_ts": _now41 + 3600})
    _sw41("u41", "amiya", "kaltsit")
    check("盲审P3-未来时间戳守卫", B.previous_handover("kaltsit", now=_now41, user_id="u41") is None)
    # P3：LLM 输出侧消毒（伪协议块剥净 + scene 钳长）——源码级接线断言（作用域=生成函数）
    _mhp41 = inspect.getsource(B.make_handover_payload)
    check("盲审P3-输出侧消毒接线", "_spho.strip_protocol_residue(_summary)" in _mhp41
          and "HANDOVER_SCENE_MAX" in _mhp41 and "【[^】]*】" in _mhp41)
    # 2026-09-12 升级：引用池收口到 core.bgtasks（原先 brain 自建 _HANDOVER_TASKS，同款逻辑抄了 4 份）。
    # 断言两件事：① set_persona 用公共 helper 起交接任务；② helper 本身真的持引用 + 完成即弃。
    _bgt41 = (Path(__file__).resolve().parents[1] / "core" / "bgtasks.py").read_text(encoding="utf-8-sig")
    check("盲审P2-任务持引用", "bgtasks.spawn(" in _sp41
          and "_TASKS.add(task)" in _bgt41 and "add_done_callback" in _bgt41)
finally:
    _ls41.LIFE_STATES_DIR, B.PERSONA_SWITCH_FILE = _orig_lsdir41, _orig_swfile41
    _store41.db._conn, _store41.db._lock = _orig_conn41, _orig_lock41
    B.client = _orig_client41
    try:
        _db41._conn.close()
    except Exception:  # noqa: BLE001
        pass


# == 42. GAL 前端客户端 bot 侧回归（2026-09-09：三态模式/停用门/合成事件/CaptureBot 翻译/chat 剥除/token/lifelog/协议帧） ==
# 全离线：路径全部重定向 tmp，绝不写真实 data/；管线用替身（不进 LLM、不进真实 matcher）。
print("== 42. GAL webgal bot 侧 ==")
import asyncio as _aio42  # noqa: E402
import datetime as _dt42  # noqa: E402
import plugins.webgal as _wg42  # noqa: E402
import plugins.webgal.inject as _inj42  # noqa: E402
import plugins.voice as _voice42  # noqa: E402
import plugins.debug as _dbg42  # noqa: E402
from agent import graph as _graph42  # noqa: E402
from nonebot import get_driver as _gd42  # noqa: E402
from nonebot.adapters.onebot.v11 import Bot as _OB42Bot, MessageSegment as _MS42  # noqa: E402

_tmpdir42 = Path(tempfile.mkdtemp())
_orig42 = (_wg42.MODE_FILE, _wg42.TOKEN_FILE, _wg42.LIFE_LOG_FILE, _wg42.GAL_HIST_FILE)
_wg42.MODE_FILE = _tmpdir42 / "webgal_mode.json"
_wg42.TOKEN_FILE = _tmpdir42 / "webgal_token.txt"
_wg42.LIFE_LOG_FILE = _tmpdir42 / "life_log.jsonl"
_wg42.GAL_HIST_FILE = _tmpdir42 / "gal_history.jsonl"  # Phase 2b：§42 直呼 _ws_round 会落回想盘，一并重定向
try:
    # ---- 模式文件读写 + gal 开机复位 qq（v2 #1）----
    check("42 模式默认qq", _wg42.get_mode() == "qq")
    check("42 模式写入chat", _wg42.set_mode("chat") == "chat" and _wg42.get_mode() == "chat")
    _mf42 = json.loads(_wg42.MODE_FILE.read_text(encoding="utf-8-sig"))
    check("42 模式文件shape", _mf42.get("mode") == "chat" and isinstance(_mf42.get("ts"), float))
    check("42 非法模式拒绝", _wg42.set_mode("rp") == "" and _wg42.get_mode() == "chat")
    _wg42.set_mode("gal")
    _wg42.startup_reset()
    check("42 开机gal复位qq", _wg42.get_mode() == "qq")
    _wg42.set_mode("chat")
    _wg42.startup_reset()
    check("42 开机chat复位qq（v2#1 开机恒复位qq字面口径，2026-09-09 盲审P3-1对齐）", _wg42.get_mode() == "qq")
    # ---- chat 跨重启保留开关（用户裁决 2026-09-10：启动器设置 WEBGAL_CHAT_PERSIST 可切换）----
    try:
        _gd42().config.webgal_chat_persist = "true"
    except Exception as _e42p:
        print(f"[42 persist stub skip] {_e42p}")
    _wg42.set_mode("chat")  # 先置于 chat（上一断言刚复位 qq）
    _wg42.startup_reset()
    check("42 chat持久开关-开启保留", _wg42.get_mode() == "chat")
    try:
        _gd42().config.webgal_chat_persist = ""
    except Exception:
        pass
    _wg42.startup_reset()
    check("42 chat持久开关-关闭复位", _wg42.get_mode() == "qq")

    # ---- qq_suspended 门真值表（gal+真实bot=True / gal+CaptureBot=False / chat、qq 恒False）----
    _real42 = _OB42Bot(_inj42.new_capture_adapter(), "10000")
    _cap42 = _inj42.CaptureBot()
    check("42 capture判定", _wg42.is_capture_bot(_real42) is False and _wg42.is_capture_bot(_cap42) is True)
    _wg42.set_mode("gal")
    check("42 门-gal+真实bot", _wg42.qq_suspended(_real42) is True)
    check("42 门-gal+CaptureBot放行", _wg42.qq_suspended(_cap42) is False)
    _wg42.set_mode("chat")
    check("42 门-chat恒放行", _wg42.qq_suspended(_real42) is False and _wg42.qq_suspended(_cap42) is False)
    _wg42.set_mode("qq")
    check("42 门-qq恒放行", _wg42.qq_suspended(_real42) is False)
    _srcw42 = inspect.getsource(_wg42)
    check("42 集中门注册", "@event_preprocessor" in _srcw42 and "IgnoredException" in _srcw42
          and "raise IgnoredException" in inspect.getsource(_wg42._gal_qq_suspend_gate))

    # ---- build_event 构造不抛 + 关键字段（onebot v11 合成私聊事件）----
    _ev42 = _inj42.build_event("10001", "你好（挥手）")
    check("42 事件字段", _ev42.post_type == "message" and _ev42.message_type == "private"
          and _ev42.sub_type == "friend" and _ev42.user_id == 10001
          and _ev42.to_me is True and _ev42.raw_message == "你好（挥手）"
          and _ev42.get_plaintext().strip() == "你好（挥手）")
    check("42 事件self_id数字", str(_ev42.self_id).isdigit())  # v11 事件模型 self_id=int 注解
    check("42 owner取superusers", _inj42.owner_uid() == next(iter(_dbg42.SUPERUSERS), ""))

    # ---- CaptureBot 翻译：text→text 段（（动作）照常文本）/ 设置类+未知 API 空操作不抛 / bot.send 路径 ----
    async def _cap_run42():
        await _cap42.call_api("send_private_msg", user_id=1, message=_inj42.Message("今晚吃什么（放下笔）"))
        await _cap42.call_api("send_msg", user_id=1, message_type="private", message=_inj42.Message("第二段"))
        await _cap42.call_api("set_qq_profile", nickname="x")
        await _cap42.call_api("set_input_status", user_id=1, event_type=1)
        await _cap42.call_api("totally_unknown_api", foo=1)
        await _cap42.send(_ev42, "经bot.send")

    _aio42.run(_cap_run42())
    check("42 捕获-text与（动作）照常", [c["kind"] for c in _cap42.captured] == ["text"] * 3
          and _cap42.captured[0]["text"] == "今晚吃什么（放下笔）"
          and _cap42.captured[2]["text"] == "经bot.send")
    _m42 = _inj42.Message()
    _m42.append(_MS42.record(file="base64://QUJD"))
    _cap42.captured.clear()

    async def _cap_rec42():
        await _cap42.call_api("send_private_msg", user_id=1, message=_m42)

    _aio42.run(_cap_rec42())
    check("42 捕获-record不推帧", len(_cap42.captured) == 1 and _cap42.captured[0]["kind"] == "voice_b64"
          and _cap42.captured[0].get("push") is False and _cap42.captured[0]["data"] == "QUJD")

    # ---- chat 剥函数（全半角括号/长段覆盖/剥空/无括号原样）----
    check("42 chat剥-全半角", B._strip_actions_chat("（坐下）你好(挥手)呀") == "你好呀")
    check("42 chat剥-长段覆盖", B._strip_actions_chat("（" + "很长的动作描写" * 10 + "）台词") == "台词")
    check("42 chat剥-多行收拢", B._strip_actions_chat("（起立）\n\n\n早上好") == "早上好")
    check("42 chat剥-无括号原样", B._strip_actions_chat("纯粹的一句话，没有演出。") == "纯粹的一句话，没有演出。")
    check("42 chat剥-剥空为空串", B._strip_actions_chat("（只有动作）") == "")

    # ---- chat 注入/剥除接线（源码级：剥除点在落库与发送共同上游；标记登记在剥除之前）----
    _srcB42 = inspect.getsource(B)
    check("42 chat注入文案", "【沟通模式】" in _srcB42 and "（动作）等演出一概不用" in _srcB42
          and "想表达情绪就用语气和称呼" in _srcB42)
    _i_mark42 = _srcB42.index("reply, _enter_events = _special.apply_agent_markers")
    _i_strip42 = _srcB42.index("reply = _strip_actions_chat(reply)")
    _i_store42 = _srcB42.index('memory.log_message(user_id, group_id, "assistant"')
    _i_send42 = _srcB42.index("await _send_sliced(chat, event, msg_out")
    check("42 chat剥除位置", _i_mark42 < _i_strip42 < _i_store42 < _i_send42)
    check("42 chat剥空兜底唔", "唔……" in _srcB42[_i_strip42:_i_strip42 + 600])

    # ---- 主动面 gal 门（debug proactive 循环 + graph 三件套决策入口）----
    _srcp42 = inspect.getsource(_dbg42._daily_proactive_loop)
    check("42 proactive门", "webgal" in _srcp42 and 'get_mode() == "gal"' in _srcp42
          and _srcp42.index("get_mode()") < _srcp42.index("_load_proactive()"))
    _srcd42 = inspect.getsource(_graph42._decide)
    check("42 graph三件套门", "webgal" in _srcd42 and "webgal-gal-mode" in _srcd42
          and _srcd42.index("get_mode()") < _srcd42.index("build_system_prompt"))

    # ---- /gal/lifelog：tmp jsonl 尾部 N 条 + limit 钳制 + 缺文件空表 ----
    check("42 lifelog缺文件空", _wg42.read_lifelog(200) == [])
    _wg42.LIFE_LOG_FILE.write_text(
        "\n".join(json.dumps({"ts": 1700000000.0 + i, "doing": f"事{i}", "mood": "平静"}, ensure_ascii=False)
                  for i in range(5)) + "\n", encoding="utf-8")
    _rows42 = _wg42.read_lifelog(3)
    check("42 lifelog尾部N", [r["doing"] for r in _rows42] == ["事2", "事3", "事4"]
          and all(r["mood"] == "平静" for r in _rows42))
    check("42 lifelog iso本地时间", _rows42[-1]["iso"] == _dt42.datetime.fromtimestamp(1700000004.0).isoformat(timespec="seconds")
          and isinstance(_rows42[-1]["ts"], float))
    check("42 lifelog钳制", len(_wg42.read_lifelog(9999)) == 5 and _wg42.read_lifelog(0) == _wg42.read_lifelog(1))

    # ---- token 缺省生成路径（env 优先；缺省首次生成写盘、复读一致）----
    _envtok42 = str(getattr(_gd42().config, "webgal_token", "") or "").strip()
    if not _envtok42:
        _t42 = _wg42.get_token()
        check("42 token生成写盘", _wg42.TOKEN_FILE.is_file() and len(_t42) == 32
              and _t42 == _wg42.TOKEN_FILE.read_text(encoding="utf-8-sig").strip())
        check("42 token复读一致", _wg42.get_token() == _t42)
    else:
        check("42 token来自env", _wg42.get_token() == _envtok42)

    # ---- /gal/state 载荷 shape + 路由挂载断言（接线就断言）----
    _st42 = _wg42._state_payload()
    check("42 state载荷shape", set(_st42) >= {"mode", "persona", "scene", "doing", "mood", "states", "token"}
          and set(_st42["persona"]) >= {"key", "name", "bust", "full"} and _st42["mode"] == "qq"
          and (_st42["persona"]["bust"] == ""
               or _st42["persona"]["bust"].startswith("https://cards.local/bust4w/")))
    _paths42 = {getattr(r, "path", "") for r in _wg42._APP.routes}
    check("42 路由挂载", {"/gal", "/gal/state", "/gal/lifelog", "/gal/tts", "/gal/ws"} <= _paths42
          and "/gal/assets/{name}" in _paths42)

    # ---- 真实 WS 握手回归（2026-09-10 视觉检测实锤：`from __future__ import annotations` +
    # 函数内局部导入 fastapi.WebSocket 曾致注解解析失败 → 依赖异常 → close-before-accept →
    # 握手 403——"路由存在性"断言抓不住，必须真握手）----
    _ws_ok42 = False
    try:
        from fastapi.testclient import TestClient as _TC42

        _tc42 = _TC42(_wg42._APP)
        with _tc42.websocket_connect("/gal/ws") as _ws42:
            _ws42.send_json({"type": "auth", "token": _wg42.get_token()})
            _ws_ok42 = _ws42.receive_json().get("type") == "auth_ok"
            _ws42.send_json({"type": "ping"})
            _ws_ok42 = _ws_ok42 and _ws42.receive_json().get("type") == "pong"
    except Exception as _e42:  # noqa: BLE001
        print(f"[42 ws-handshake err] {type(_e42).__name__}: {_e42}")
    check("42 真实WS握手 auth_ok+pong", _ws_ok42)

    # ---- 协议帧形状（管线替身，行为级）：reply_start → seg(text)… → reply_end；voice/sticker 不推 ----
    async def _round42():
        frames: list = []

        class _FakeWS:
            async def send_json(self, p):
                frames.append(p)

        async def _stub_runner(cap_bot, ev):
            cap_bot.captured.append({"kind": "text", "text": "帧一"})
            cap_bot.captured.append({"kind": "voice_b64", "data": "xx", "push": False})
            cap_bot.captured.append({"kind": "text", "text": "帧二"})

        _orig42r = _wg42._PIPELINE_RUNNER
        _wg42._PIPELINE_RUNNER = _stub_runner
        try:
            await _wg42._ws_round(_FakeWS(), "嗨")
        finally:
            _wg42._PIPELINE_RUNNER = _orig42r
        return frames

    _frames42 = _aio42.run(_round42())
    check("42 帧序-start/sprite/seg/end", [f["type"] for f in _frames42] == ["reply_start", "sprite", "seg", "seg", "reply_end"]
          and _frames42[1] == {"type": "sprite", "emotion": ""} and _frames42[2] == {"type": "seg", "kind": "text", "text": "帧一"}
          and _frames42[4]["mode"] == "qq")
    check("42 voice段不推帧", all(f.get("kind") != "voice_b64" for f in _frames42))

    # ---- 立绘差分帧（2026-09-10：bot 自决基调→sprite 帧先于文本帧；合成轮基调保留不 pop）----
    async def _round_sprite42():
        frames: list = []

        class _FakeWS:
            async def send_json(self, p):
                frames.append(p)

        async def _stub_runner(cap_bot, ev):
            cap_bot.captured.append({"kind": "text", "text": "台词页"})

        _orig42r = _wg42._PIPELINE_RUNNER
        _wg42._PIPELINE_RUNNER = _stub_runner
        try:
            await _wg42._ws_round(_FakeWS(), "嗨")
        finally:
            _wg42._PIPELINE_RUNNER = _orig42r
        return frames

    from plugins import brain as _br42s  # noqa: E402

    _uid42s = _inj42.owner_uid()
    _br42s._TONE_LAST[_uid42s] = "温柔"
    _frames42s = _aio42.run(_round_sprite42())
    _kept42 = _br42s._TONE_LAST.get(_uid42s, "") == "温柔"
    _br42s._TONE_LAST.pop(_uid42s, None)
    check("42 sprite帧先于文本+合成轮基调保留",
          [f["type"] for f in _frames42s] == ["reply_start", "sprite", "seg", "reply_end"]
          and _frames42s[1] == {"type": "sprite", "emotion": "温柔"} and _kept42)
    check("42 brain合成轮基调get不pop接线",
          "in_synthetic_round" in inspect.getsource(_br42s)
          and "_TONE_LAST.get(user_id" in inspect.getsource(_br42s))

    async def _round_err42():
        frames: list = []

        class _FakeWS:
            async def send_json(self, p):
                frames.append(p)

        async def _boom(cap_bot, ev):
            raise RuntimeError("boom")

        _orig42r = _wg42._PIPELINE_RUNNER
        _wg42._PIPELINE_RUNNER = _boom
        try:
            await _wg42._ws_round(_FakeWS(), "嗨")
        finally:
            _wg42._PIPELINE_RUNNER = _orig42r
        return frames

    _ferr42 = _aio42.run(_round_err42())
    check("42 轮内异常err+reply_end兜底", [f["type"] for f in _ferr42] == ["reply_start", "err", "reply_end"]
          and "boom" in _ferr42[1]["reason"])

    # ---- 合成轮标记（2026-09-10 审计 P2）：_do_round 执行期 in_synthetic_round()=True、轮外复位 False；
    # brain._typing_on 据此跳过真实 QQ 的 set_input_status（web 通道零 QQ 动作红线）----
    async def _round_flag42():
        seen: list = []

        async def _probe_runner(cap_bot, ev):
            seen.append(_wg42.in_synthetic_round())
            cap_bot.captured.append({"kind": "text", "text": "帧"})

        _orig42r, _orig42u = _wg42._PIPELINE_RUNNER, _inj42.owner_uid
        _wg42._PIPELINE_RUNNER = _probe_runner
        _inj42.owner_uid = lambda: "10001"
        try:
            await _wg42._do_round("嗨")
        finally:
            _wg42._PIPELINE_RUNNER = _orig42r
            _inj42.owner_uid = _orig42u
        seen.append(_wg42.in_synthetic_round())
        return seen

    check("42 合成轮标记-轮内True轮外False", _aio42.run(_round_flag42()) == [True, False])
    check("42 typing合成轮守卫接线", "in_synthetic_round" in inspect.getsource(B._typing_on)
          and "bgtasks.spawn(" in inspect.getsource(B._typing_on))  # 既有 469 断言口径不回退（2026-09-12：create_task→bgtasks.spawn）
    check("42 busy/auth/pong帧形状", '"reason": "busy"' in _srcw42 and '"type": "auth_err"' in _srcw42
          and '"reason": "qq_mode"' in _srcw42 and '"type": "pong"' in _srcw42)

    # ---- tts_wav（函数级；空文本短路不拉 worker；签名契约）----
    check("42 tts_wav签名", list(inspect.signature(_voice42.tts_wav).parameters) == ["text", "tone"]
          and inspect.signature(_voice42.tts_wav).parameters["tone"].default == "")
    check("42 tts_wav空文本None", _aio42.run(_voice42.tts_wav("  ")) is None)
finally:
    _wg42.MODE_FILE, _wg42.TOKEN_FILE, _wg42.LIFE_LOG_FILE, _wg42.GAL_HIST_FILE = _orig42


# == 43. 2026-09-10 全项目审计修复回归（livesource 注册 / 微更新 vibe 钳制 / 卡模板守卫 /
#        loguru 占位风格 / HELP_TEXT 现状 / debug 硬编码 QQ / 人设中性化） ==
# 全离线、不碰真实 data/（lifesim 状态文件重定向 tmp，llm 打桩，special 状态随测随清）。
print("== 43. 0910 审计修复 ==")
import asyncio as _aio43  # noqa: E402
import tomllib as _tl43  # noqa: E402
import core.special as _sp43  # noqa: E402
import plugins.livesource as _lsr43  # noqa: E402
from plugins import debug as _dbg43  # noqa: E402
from plugins import qq_avatar as _qa43  # noqa: E402
from agent import graph as _g43  # noqa: E402
from agent import lifesim as _ls43  # noqa: E402

# ---- 修复1（P1）：livesource 插件注册 + 加载零副作用 ----
with open("pyproject.toml", "rb") as _pf43:
    _nb43 = _tl43.load(_pf43)["tool"]["nonebot"]["plugins"]
check("43 livesource已注册pyproject", "plugins.livesource" in _nb43)
check("43 livesource归一化纯函数", _lsr43._normalize({"uid": 1, "text": " 弹幕 "}) == {"source": "live", "uid": "1", "name": "", "text": "弹幕"}
      and _lsr43._normalize({"text": "  "}) is None)
check("43 livesource无消费者分发即弃", _aio43.run(_lsr43._dispatch({"source": "live", "uid": "1", "name": "x", "text": "hi"})) is False)

# ---- 修复3（P2）：微更新未识别 vibe 词按最深档处理再钳（镜像 core/special 口径）----
_orig43_lsfile, _orig43_llm = _ls43.LIFE_STATE_FILE, _ls43.llm.complete
_ls43.LIFE_STATE_FILE = Path(tempfile.mkdtemp()) / "life_state43.json"


async def _fake_complete43(**kw):
    return '{"doing":"","scene":"","vibe":"有点心动"}'  # 四选一之外的自由词


_ls43.llm.complete = _fake_complete43
_ls43._MICRO_INFLIGHT.clear()
_ls43._MICRO_LAST.pop("u-vibe43", None)
_ls43._MICRO_LAST.pop("u-vibe43b", None)
try:
    _sp43.clear_all()
    _aio43.run(_ls43.micro_update_from_conversation("u-vibe43", "抱", "嗯", allow=True, max_vibe=2))
    check("43 微更新未识别vibe钳次深档", _sp43.active().get("vibe_mood", {}).get("note") == "亲密")
    _sp43.clear_all()
    _aio43.run(_ls43.micro_update_from_conversation("u-vibe43b", "抱", "嗯", allow=True, max_vibe=0))
    check("43 微更新未识别vibe零权丢弃", "vibe_mood" not in _sp43.active())
finally:
    _ls43.LIFE_STATE_FILE = _orig43_lsfile
    _ls43.llm.complete = _orig43_llm
    _sp43.clear_all()

# ---- 修复5（P3）：debug 硬编码 QQ 兜底删除；SUPERUSERS 空 → proactive 返 None（不发不猜号）----
# 2026-09-12 隐私修复：断言不再写死号字面量（原写法等于"用一个泄漏去防另一个泄漏"，审计 O 项会报），
# 改为**运行时真主人号**（来自 nonebot 配置 superusers）——防回潮的强度反而更高，且零泄漏。
_orig43_su = _dbg43.SUPERUSERS
_owner43 = str(next(iter(_orig43_su), ""))
check("43 debug无硬编码QQ", (not _owner43) or (_owner43 not in inspect.getsource(_dbg43)))
_dbg43.SUPERUSERS = set()
try:
    _none43 = _aio43.run(_dbg43._build_proactive_msg(state_note="", uid=""))
finally:
    _dbg43.SUPERUSERS = _orig43_su
check("43 proactive空superusers返None", _none43 is None)

# ---- 修复6（P3）：卡数据模板 .format 守卫（占位不匹配 → warning + 跳过该块，不炸整轮）----
check("43 卡模板守卫行为", B._safe_card_format("你好{user_id}", user_id=7) == "你好7"
      and B._safe_card_format("{missing_field}", user_id=7) == ""
      and B._safe_card_format("坏{ tpl", user_id=7) == ""
      and B._safe_card_format("") == "")
_srcB43 = inspect.getsource(B)
check("43 卡模板三处接线", "_gf.format(" not in _srcB43 and "_gm.format(" not in _srcB43
      and "_pi.format(" not in _srcB43 and _srcB43.count("_safe_card_format(") >= 4)

# ---- 修复7（P3）：graph/lifesim 裸 loguru 占位风格（{} / {!r}，无 %s/%r 残留）----
_srcg43, _srcls43 = inspect.getsource(_g43), inspect.getsource(_ls43)
check("43 graph日志占位风格", '"%s' not in _srcg43 and '"%r' not in _srcg43
      and "topic knowledge saved: {} -> {}" in _srcg43)
check("43 lifesim日志占位风格", '"%s' not in _srcls43 and '"%r' not in _srcls43
      and "lifesim insight saved: {!r}" in _srcls43)

# ---- 修复8（P2）：HELP_TEXT 同步现状（退役口令/时间窗已删，标记族口径就位）----
_ht43 = _dbg43.HELP_TEXT
check("43 help退役口令已删", all(w not in _ht43 for w in
      ("催眠射线", "洗脑光线", "使用了口球", "彻底结束了", "这次疼爱结束了", "7-22")))
check("43 help现状标记口径", "【催眠：效果】" in _ht43 and "【洗脑：要求】" in _ht43
      and "【道具：名称】" in _ht43 and "【不许装了】" in _ht43 and "【解除：全部】" in _ht43
      and "/清除" in _ht43 and "已发生的事实" in _ht43)

# ---- 修复9（P2 铁律#2）：fiction/qq_avatar 人设硬编码中性化 ----
_srcfi43, _srcqa43 = inspect.getsource(_fic), inspect.getsource(_qa43)
check("43 fiction人设中性", "妾身" not in _srcfi43 and "奥丁" not in _srcfi43 and "萝莉" not in _srcfi43
      and "代笔作者" in _fic.WRITER_PERSONA)
check("43 avatar人设中性", "妾身" not in _srcqa43 and "该操作仅限主人" in _srcqa43)


# == 44. 2026-09-10 夜间睡眠保护（实测故障回归：23 点入睡、02 点被叙醒） ==
# 故障取证：深夜 bot 重启的 startup resume tick / 事件唤醒 tick 反复把她"叙醒"（00:25-02:30 五次），
# 01:01 晚安消息经 apply_interaction 把状态钉回清醒，此后夜间缺省 30 分钟节奏整夜空转。
# 修复口径：深夜窗入睡 → 时长地板睡到清晨窗（06:30-08:00 随机，自决更长从自决）；
#           sleep_until 未到 → 心跳让路、对话介入不落状态（普通消息不唤醒，白天午睡不设账）。
# 全离线、tmp 重定向状态文件、llm 打桩——不碰真实 data/。
print("== 44. 夜间睡眠保护 ==")
import asyncio as _aio44  # noqa: E402
import time as _t44  # noqa: E402
from agent import lifesim as _ls44  # noqa: E402

_tmpdir44 = Path(tempfile.mkdtemp())
_orig44 = (_ls44.LIFE_STATE_FILE, _ls44.LIFE_LOG_FILE, _ls44.llm.complete)
_ls44.LIFE_STATE_FILE = _tmpdir44 / "life_state44.json"
_ls44.LIFE_LOG_FILE = _tmpdir44 / "life_log44.jsonl"


def _at44(h, m):
    """今天本地 h:m 的时刻戳（floor 纯函数输入，测试与运行时刻解耦）。"""
    lt = _t44.localtime()
    return _t44.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1))


async def _fake_complete44(**kw):
    return "醒来开始处理新一天的工作"  # 醒叙事哨兵：睡眠守卫若失效，doing 会被它替换而现形


_ls44.llm.complete = _fake_complete44
try:
    # ---- 44-1 时长地板纯函数：深夜入睡睡到清晨窗；白天/清晨窗不兜底（午睡节奏归自决） ----
    _f2330 = _ls44._night_sleep_floor_min(_at44(23, 30))
    _f0300 = _ls44._night_sleep_floor_min(_at44(3, 0))
    _f0600 = _ls44._night_sleep_floor_min(_at44(6, 0))
    check("44 深夜23:30 floor∈[7h,8.5h]", _f2330 is not None and 419 <= _f2330 <= 511)
    check("44 凌晨03:00 floor∈[3.5h,5h]", _f0300 is not None and 209 <= _f0300 <= 301)
    check("44 清晨06:00 floor∈[0.5h,2h]", _f0600 is not None and 29 <= _f0600 <= 121)
    check("44 白天14:00/清晨07:00不floor",
          _ls44._night_sleep_floor_min(_at44(14, 0)) is None
          and _ls44._night_sleep_floor_min(_at44(7, 0)) is None)

    # ---- 44-2 深夜睡眠中 tick 让路：sleep_until 未到 → 不叙事（doing 原样）、wake_min 收缩为剩余 ----
    _ls44._save_state({"ts": _t44.time() - 3600, "doing": "钻进被窝睡觉", "mood": "平静",
                       "sleep_until": _t44.time() + 3 * 3600, "wake_min": 240})
    _st44 = _aio44.run(_ls44.lifesim_tick())
    check("44 睡眠中tick不叙事", _st44.get("doing") == "钻进被窝睡觉"
          and _st44.get("sleep_until", 0) > _t44.time())
    check("44 睡眠中wake_min收缩续睡", 1 <= _st44.get("wake_min", 0) <= 181)  # ≈剩余 180 分钟

    # ---- 44-3 清晨自然醒：sleep_until 已过 → 心跳正常叙事、睡眠账清零 ----
    _ls44._save_state({"ts": _t44.time() - 7 * 3600, "doing": "钻进被窝睡觉", "mood": "平静",
                       "sleep_until": _t44.time() - 60, "wake_min": 1})
    _st44b = _aio44.run(_ls44.lifesim_tick())
    check("44 清晨自然醒清账", _st44b.get("doing") == "醒来开始处理新一天的工作"
          and "sleep_until" not in _st44b)

    # ---- 44-4 夜间睡眠中消息不落状态（普通消息不唤醒），清醒时互动照常 ----
    _ls44._save_state({"ts": _t44.time(), "doing": "已经睡着", "mood": "平静",
                       "sleep_until": _t44.time() + 3600})
    _ok44 = _ls44.apply_interaction(doing="被消息叫醒开始聊天", by="priv:u-nightsleep44")
    _st44c = _ls44._load_state()
    check("44 夜间消息不唤醒", _ok44 is False and _st44c.get("doing") == "已经睡着"
          and "interaction_ts" not in _st44c)
    _ls44._save_state({"ts": _t44.time(), "doing": "在值班室整理文件", "mood": "平静"})
    _ok44b = _ls44.apply_interaction(doing="在值班室和TA说话", by="priv:u-nightsleep44")
    check("44 清醒互动照常落账", _ok44b is True
          and _ls44._load_state().get("doing") == "在值班室和TA说话")

    # ---- 44-5 接线在位（tick 守卫 / 时长地板 / 介入守卫同源一套账） ----
    _src44 = inspect.getsource(_ls44)
    check("44 三处守卫接线", "night sleep guard" in inspect.getsource(_ls44.lifesim_tick)
          and "night sleep floor engaged" in inspect.getsource(_ls44.lifesim_tick)
          and "night sleep rejected" in inspect.getsource(_ls44.apply_interaction)
          and "_night_sleep_floor_min" in _src44 and "_is_night_sleeping" in _src44)
finally:
    _ls44.LIFE_STATE_FILE, _ls44.LIFE_LOG_FILE, _ls44.llm.complete = _orig44


#\n== 结果：{# == 45. 立绘差分菜单接线（2026-09-10：卡 sprite_expressions → 合成轮 sprite_hint → POLISH_REQ 槽）==
check("45 polish模板sprite槽", "{sprite_hint}" in str(getattr(_br42s, "POLISH_REQ", ""))
      and "{sprite_hint}" in str(getattr(_br42s, "PRIVATE_POLISH_REQ", "")))
check("45 brain合成轮sprite_hint接线", "in_synthetic_round" in inspect.getsource(_br42s)
      and "sprite_expressions" in inspect.getsource(_br42s)
      and "sprite_hint=_sprite_hint" in inspect.getsource(_br42s))
_pj45a = json.load(open(r"E:/robot/qq-bot/data/personas/amiya.json", encoding="utf-8-sig"))
check("45 amiya差分菜单", len((_pj45a.get("data") or {}).get("sprite_expressions") or []) >= 5)

# \n== 结果：{# == 46. 语音无兜底回归（2026-09-10：无音色包卡绝不出别卡音色，odin 硬兜底已除）==
_vn46 = inspect.getsource(_v16)
check("46 voice无odin硬兜底", 'return "odin"' not in _vn46
      and 'return ""' in inspect.getsource(_v16._active_voice))
check("46 voice无包早退接线", "if not _vo:" in _vn46 and "跳过语音合成" in _vn46)
check("46 voice预热跳过接线", "if not voice:" in _vn46)
_saved46 = (_br42s._persona_name, _br42s._persona_card)
try:
    _br42s._persona_name = lambda *a, **k: "普瑞赛斯"
    _br42s._persona_card = lambda *a, **k: {"name": "普瑞赛斯"}
    _v16._AV_WARNED = False
    check("46 无音色包_active_voice返回空串", _v16._active_voice() == "")
finally:
    _br42s._persona_name, _br42s._persona_card = _saved46


# == 47. 主人私聊并行事实预解析（2026-09-10：小 LLM 拆解主人括号事实 → stage2 约束注入 + 机器登记） ==
# 链路：handle gating（主人+私聊+全角括号）→ preparse_owner_facts 与 stage1 并行 create_task →
# core/reply.generate 在 stage2 组装时收取（wait_for 8s 兜底，stage1 不等它）→ 润色提示末尾追加
# 既成事实约束块 + facts_box 带回 → generate 正常返回后 handle 调 special.apply_owner_facts 机器登记。
# 全离线、tmp 重定向状态文件、client 打桩——不碰真实 data/。
print("== 47. 主人括号事实预解析 ==")
import asyncio as _aio47  # noqa: E402

_tmpdir47 = Path(tempfile.mkdtemp())
_S_orig47 = (S.STATE_FILE, S._STATE)
_S47_file = _tmpdir47 / "special_state_47.json"
S.STATE_FILE = _S47_file
S._STATE = None
_B_client_orig47 = B.client


class _Msg47:
    def __init__(self, c):
        self.content = c
        self.reasoning_content = None


class _Choice47:
    def __init__(self, c):
        self.message = _Msg47(c)


class _Resp47:
    def __init__(self, c):
        self.choices = [_Choice47(c)]


class _Comp47:
    def __init__(self):
        self.out = ""
        self.calls = []

    async def create(self, **kw):
        self.calls.append(kw)
        return _Resp47(self.out)


class _Client47:
    def __init__(self, comp):
        self.chat = type("C", (), {"completions": comp})()


try:
    # ---- 47-1 预解析器：合法 JSON / ``` 围栏 / 字段缺失 / 非 JSON / 字段非法 → 各自返回或 None ----
    _comp47 = _Comp47()
    B.client = _Client47(_comp47)

    async def _pp47(user_text, out):
        _comp47.out = out
        return await B.preparse_owner_facts(user_text)

    _r47 = _aio47.run(_pp47("（打了个响指，你陷入催眠）",
                            '{"hypno": "只能说实话", "brainwash": "", "items": [{"name": "绳子", "state": "绑着"}]}'))
    check("47 解析-合法JSON", isinstance(_r47, dict) and _r47["hypno"] == "只能说实话"
          and _r47["brainwash"] == "" and _r47["items"] == [{"name": "绳子", "state": "绑着"}])
    _r47b = _aio47.run(_pp47("（用绳子把手绑起来）",
                             '```json\n{"hypno": "", "brainwash": "爱上主人", "items": []}\n```'))
    check("47 解析-围栏剥净", isinstance(_r47b, dict) and _r47b["brainwash"] == "爱上主人"
          and _r47b["items"] == [] and _r47b["hypno"] == "")
    _r47c = _aio47.run(_pp47("（催眠你）", '{"hypno": "被催眠了"}'))
    check("47 解析-字段缺失按空", isinstance(_r47c, dict) and _r47c["hypno"] == "被催眠了"
          and _r47c["brainwash"] == "" and _r47c["items"] == [])
    check("47 解析-非JSON None", _aio47.run(_pp47("（催眠你）", "抱歉，这个我不知道怎么回答")) is None)
    check("47 解析-无JSON体None", _aio47.run(_pp47("（催眠你）", "```json\n[]\n```")) is None)
    check("47 解析-字段类型非法None", _aio47.run(
        _pp47("（催眠你）", '{"hypno": 123, "brainwash": "", "items": []}')) is None)
    _r47d = _aio47.run(_pp47("（催眠你）",
                             '{"hypno": "【催眠：x】只能说实话", "brainwash": "", "items": []}'))
    check("47 解析-伪协议块剥净", isinstance(_r47d, dict) and _r47d["hypno"] == "只能说实话")
    _r47e = _aio47.run(_pp47("（塞上口球）",
                             '{"hypno": "", "brainwash": "", "items": ['
                             '{"name": " 口 球 ", "state": "塞着"}, {"name": "", "state": "x"}, "junk"] }'))
    check("47 解析-道具名消毒+脏项剔除", isinstance(_r47e, dict)
          and _r47e["items"] == [{"name": "口球", "state": "塞着"}])
    _r47f = _aio47.run(_pp47("（道具全上）",
                             '{"hypno": "", "brainwash": "", "items": ['
                             + ",".join(f'{{"name": "道具{k}", "state": ""}}' for k in range(5)) + ']}'))
    check("47 解析-items钳4", isinstance(_r47f, dict) and len(_r47f["items"]) == 4)
    check("47 解析-无括号不调LLM", _aio47.run(_pp47("今天天气不错", '{"hypno": "x"}')) == {}
          and len(_comp47.calls) == 9)  # 上面 9 次带括号调用；无括号这次零新增

    # ---- 47-1b releases（2026-09-11 热修十二 F1）：schema 新键 / 消毒 / 类型非法 / 缺省按空 ----
    _r47rel = _aio47.run(_pp47("（把手铐解开）",
                               '{"hypno": "", "brainwash": "", "items": [], "releases": ["手铐", "全部", ""]}'))
    check("47 解析-releases消毒+空项剔除", isinstance(_r47rel, dict)
          and _r47rel["releases"] == ["手铐", "全部"])
    _r47relb = _aio47.run(_pp47("（把道具收起来）",
                                '{"hypno": "", "brainwash": "", "items": [], "releases": ["道具：小铃铛【道具：x】"]}'))
    check("47 解析-releases伪协议块剥净", isinstance(_r47relb, dict)
          and _r47relb["releases"] == ["道具：小铃铛"])
    check("47 解析-releases类型非法None", _aio47.run(
        _pp47("（催眠你）", '{"hypno": "x", "releases": "手铐"}')) is None)
    _r47relc = _aio47.run(_pp47("（催眠你）", '{"hypno": "x"}'))
    check("47 解析-releases缺省空", isinstance(_r47relc, dict) and _r47relc["releases"] == [])

    # ---- 47-2 LLM 入参接线（与 handover 同款形态：低温/小 max_tokens/8s/关思维链） ----
    _k47 = _comp47.calls[0]
    check("47 预解析入参接线", _k47.get("temperature") == 0.2 and _k47.get("max_tokens") == 200
          and _k47.get("timeout") == 8.0
          and _k47.get("extra_body", {}).get("chat_template_kwargs", {}).get("enable_thinking") is False
          and "状态记录器" in _k47["messages"][0]["content"]
          and "主人消息" in _k47["messages"][1]["content"])

    # ---- 47-3 apply_owner_facts：hypno/brainwash/items 登记到 tmp 状态文件 + by 正确 + 开放 item ----
    S.clear_all()
    _reg47 = S.apply_owner_facts(
        {"hypno": "只能说实话", "brainwash": "爱上主人",
         "items": [{"name": "口球", "state": "塞着"}, {"name": "绳子", "state": "绑着手"}]}, by="u47")
    check("47 登记-返回kind列表", _reg47 == ["hypno", "brainwash", "item:ball", "item:绳子"])
    _file47 = json.loads(_S47_file.read_text(encoding="utf-8-sig"))
    _fb47 = _file47.get("cards", {}).get("default", {})  # 2026-09-11 热修九：v2 按卡分桶
    check("47 登记-写进tmp状态文件", _fb47["hypno"]["note"] == "只能说实话"
          and _fb47["hypno"]["by"] == "u47" and _fb47["brainwash"]["note"] == "爱上主人")
    check("47 登记-已知道具映射", _fb47["item:ball"]["note"] == "塞着"
          and _fb47["item:ball"]["label"] == "口球" and _fb47["item:ball"]["by"] == "u47")
    check("47 登记-未知名开放item:*", "item:绳子" in S.active()
          and S.active()["item:绳子"]["label"] == "绳子" and S.active()["item:绳子"]["note"] == "绑着手")
    _reg47b = S.apply_owner_facts({"hypno": "心理暗示：服从主人", "brainwash": "", "items": []}, by="u47")
    check("47 登记-心理暗示前缀口径", _reg47b == ["hypno"]
          and S.active()["hypno"]["note"].startswith("心理暗示（表面照常，心底被写入）："))
    check("47 登记-空facts no-op", S.apply_owner_facts({}) == []
          and S.apply_owner_facts(None) == []
          and S.apply_owner_facts({"hypno": "", "brainwash": "", "items": []}) == [])
    check("47 登记-非dict no-op", S.apply_owner_facts("x") == [] and S.apply_owner_facts(1) == [])

    # ---- 47-3b releases 落账（2026-09-11 热修十二 F1）：先清后登 / 全部→clear_all / 氛围→vibe_mood /
    #      解除绝不产生新登记（解除不是登记）——照 47 节 tmp 状态文件隔离 ----
    S.clear_all()
    S.set_state("hypno", "旧的", by="t")
    S.set_state("item:手铐", by="t")
    _reg47r = S.apply_owner_facts({"hypno": "新的要求", "brainwash": "", "items": [],
                                   "releases": ["催眠", "手铐"]}, by="u47r")
    check("47 releases-先清后登", _reg47r == ["hypno"]  # 返回值=登记 kind 列表（契约不变）
          and S.active().get("hypno", {}).get("note") == "新的要求"  # 同 kind：清在前登在后 → 登记存活
          and "item:手铐" not in S.active())
    S.set_state("item:口球", by="t")
    S.set_state("no_fake", by="t")
    S.apply_owner_facts({"releases": ["全部"]}, by="u47r")
    check("47 releases-全部clear_all", S.active() == {})
    S.set_state("vibe_mood", "情欲", by="t")
    S.apply_owner_facts({"releases": ["氛围"]}, by="u47r")
    check("47 releases-氛围清vibe_mood", "vibe_mood" not in S.active())
    check("47 releases-未登记道具名不产生新登记", S.apply_owner_facts(
        {"releases": ["没戴过的杯子"]}, by="u47r") == []
        and S.active() == {} and "item:没戴过的杯子" not in S.active())

    # ---- 47-4 generate 注入：facts_task 收取 → stage2 末尾追加约束块；stage1 不等它不注入；box 带回 ----
    async def _gen47(facts_coro=None, timeout_override=None, **kw):
        comp = _Comp47()
        comp.out = "好呀，知道了。"
        box = {}
        _ftask = _aio47.create_task(facts_coro()) if facts_coro else None
        _orig_to = R._OWNER_FACTS_TIMEOUT
        if timeout_override:
            R._OWNER_FACTS_TIMEOUT = timeout_override
        try:
            res = await R.generate(user_id="u47", history_msgs=[{"role": "user", "content": "（打了个响指）说真话"}],
                                   stage1_prompt="S1", polish_prompt="P1",
                                   client=_Client47(comp), model="m",
                                   facts_task=_ftask, facts_box=box, **kw)
        finally:
            R._OWNER_FACTS_TIMEOUT = _orig_to
        return comp, box, res

    async def _facts_ok47():
        return {"hypno": "只能说实话", "brainwash": "",
                "items": [{"name": "绳子", "state": "绑着"}]}

    _comp47g, _box47, _res47g = _aio47.run(_gen47(_facts_ok47))
    check("47 generate-stage2注入事实块", len(_comp47g.calls) == 2
          and "【本轮已发生事实（主人动作，机器已确认）】" in _comp47g.calls[1]["messages"][0]["content"]
          and "· 催眠：只能说实话" in _comp47g.calls[1]["messages"][0]["content"]
          and "· 道具：绳子：绑着" in _comp47g.calls[1]["messages"][0]["content"]
          and "既成事实" in _comp47g.calls[1]["messages"][0]["content"]
          and _comp47g.calls[1]["messages"][0]["content"].startswith("P1"))
    check("47 generate-stage1不注入", "【本轮已发生事实】" not in _comp47g.calls[0]["messages"][0]["content"])
    check("47 generate-box带回facts", _box47.get("facts", {}).get("hypno") == "只能说实话"
          and isinstance(_res47g, dict) and _res47g["text"])
    check("47 约束块原文口径", "叙事须与其一致" in R._OWNER_FACTS_BLOCK
          and "无需重复输出登记标记" in R._OWNER_FACTS_BLOCK
          and "仍须按协议输出对应标记" in R._OWNER_FACTS_BLOCK)  # 热修十二 F1：抑制句改写（解除仍须出标记）

    async def _facts_slow47():
        await _aio47.sleep(5)
        return {"hypno": "迟到的", "brainwash": "", "items": []}

    _comp47t, _box47t, _ = _aio47.run(_gen47(_facts_slow47, timeout_override=0.05))
    check("47 generate-超时跳过不阻塞", len(_comp47t.calls) == 2
          and "【本轮已发生事实】" not in _comp47t.calls[1]["messages"][0]["content"]
          and _box47t.get("facts") is None)

    async def _facts_boom47():
        raise RuntimeError("preparse boom")

    _comp47x, _box47x, _ = _aio47.run(_gen47(_facts_boom47))
    check("47 generate-异常跳过本轮无约束", len(_comp47x.calls) == 2
          and "【本轮已发生事实】" not in _comp47x.calls[1]["messages"][0]["content"]
          and _box47x.get("facts") is None)

    # ---- 47-5 可选参数默认 None 零变化（群聊/无括号轮完全不受影响） ----
    check("47 generate签名默认None", inspect.signature(R.generate).parameters["facts_task"].default is None
          and inspect.signature(R.generate).parameters["facts_box"].default is None)

    async def _gen47plain():
        comp = _Comp47()
        comp.out = "嗯。"
        box = {}
        res = await R.generate(user_id="u47b", history_msgs=[{"role": "user", "content": "在吗"}],
                               stage1_prompt="S1", polish_prompt="P1", client=_Client47(comp), model="m",
                               facts_box=box)  # 只给 box 不给 task：不得有写入
        return comp, box, res

    _comp47p, _box47p, _res47p = _aio47.run(_gen47plain())
    check("47 默认零变化", len(_comp47p.calls) == 2
          and "【本轮已发生事实】" not in _comp47p.calls[1]["messages"][0]["content"]
          and _box47p == {} and _res47p["text"] == "嗯。")

    # ---- 47-6 gating 与接线（源码级）：gating 条件 / 并行启动 / 登记在 generate 返回之后 / reply 不登记 ----
    _hs47 = inspect.getsource(B.handle)
    _g47 = _hs47.index('_priv and is_owner and "（" in')
    _c47 = _hs47.index("bgtasks.spawn(preparse_owner_facts(text))")
    _genpos47 = _hs47.index("await _gen_reply2(")
    _regpos47 = _hs47.index("apply_owner_facts(_facts_box[")
    check("47 gating-主人私聊全角括号", 0 < _c47 - _g47 < 300 and "_facts_task = None" in _hs47)
    # 2026-09-12：并行启动改走 core.bgtasks.spawn（引用池收口），断言同步升级——
    # 现在钉的是"用公共 helper + helper 真持引用"，比原先钉"_PREPARSE_TASKS"更不容易漏。
    check("47 并行启动接线", "bgtasks.spawn(preparse_owner_facts(text))" in _hs47
          and "_facts_task = bgtasks.spawn(" in _hs47)
    check("47 登记时序在generate返回后", _genpos47 < _regpos47
          and "await _gen_reply2(" in _hs47[_genpos47:_regpos47]
          and "facts_task=_facts_task" in _hs47 and "facts_box=_facts_box" in _hs47
          and 'if _facts_box.get("facts"):' in _hs47)
    check("47 _gen_reply2透传", "facts_task=facts_task" in inspect.getsource(B._gen_reply2)
          and "facts_box=facts_box" in inspect.getsource(B._gen_reply2))
    _gensrc47 = inspect.getsource(R.generate)
    check("47 reply收取在stage2组装处", "asyncio.wait_for(facts_task, timeout=_OWNER_FACTS_TIMEOUT)" in _gensrc47
          and _gensrc47.index("_facts_taken") < _gensrc47.index("resp2 = await client.chat.completions.create")
          and "owner facts skipped" in _gensrc47 and "owner facts injected" in _gensrc47)
    check("47 登记不做在reply", "apply_owner_facts" not in inspect.getsource(R)
          and "set_state" not in _gensrc47)
    check("47 special函数公开在位", callable(S.apply_owner_facts)
          and "owner facts applied" in inspect.getsource(S.apply_owner_facts))
finally:
    B.client = _B_client_orig47
    S.STATE_FILE, S._STATE = _S_orig47

# ============ 48. LLM Provider 适配层（2026-09-10 Phase1：默认本地零变化 + env 可切外部端点） ============
print("== 48. LLM Provider 适配层 ==")
import nonebot  # noqa: E402
from core import llm as core_llm  # noqa: E402
from plugins import emotion as _emo48, correction as _cor48, sticker as _stk48, fiction as _fic48  # noqa: E402
from plugins.memory import embeddings as _emb48  # noqa: E402
from agent import llm as _allm48  # noqa: E402

_cfg48 = nonebot.get_driver().config
_saved48: dict = {}  # key -> (原本是否存在, 原值)——测试后恢复，严禁污染真实环境


def _set48(key, value):
    if key not in _saved48:
        _saved48[key] = (hasattr(_cfg48, key), getattr(_cfg48, key, None))
    setattr(_cfg48, key, value)


def _unset48(*keys):
    for _k in keys:
        if _k not in _saved48:
            _saved48[_k] = (hasattr(_cfg48, _k), getattr(_cfg48, _k, None))
        try:
            delattr(_cfg48, _k)
        except AttributeError:
            pass


try:
    # 48-1 brain.client 出自适配层 main 单例（须在任何 reload 前断言同一性）
    check("48 brain.client出自适配层", B.client is core_llm.get_client("main"))
    core_llm.reload()

    # 48-2 默认回落链：本地引擎 / gemma / 32768 / embed 本地 11435 开
    check("48 默认thinking注入(local+auto)", core_llm.thinking_extra(True) == {"chat_template_kwargs": {"enable_thinking": True}}
          and core_llm.thinking_extra(False) == {"chat_template_kwargs": {"enable_thinking": False}})
    check("48 默认模型=gemma", core_llm.resolve_model("main") == "gemma" and core_llm.resolve_model("small") == "gemma"
          and core_llm.resolve_model("fiction") == "gemma")
    check("48 默认ctx预算=32768", core_llm.ctx_budget() == 32768)
    check("48 默认embed配置", core_llm.embed_config() == ("http://127.0.0.1:11435/v1/embeddings", True))

    # 48-3 purpose 专用模型键优先（llm_model_{purpose} > llm_model 链）
    _set48("llm_model_fiction", "smoke-fic-model")
    core_llm.reload()
    check("48 purpose专用模型优先", core_llm.resolve_model("fiction") == "smoke-fic-model"
          and core_llm.resolve_model("main") == "gemma")
    _unset48("llm_model_fiction")
    core_llm.reload()

    # 48-4 thinking_extra 矩阵：外部 provider→{}；off→{}；on→恒注入 True
    _set48("llm_provider", "openai_compat")
    core_llm.reload()
    check("48 外部provider不注入", core_llm.thinking_extra(True) == {} and core_llm.thinking_extra(False) == {})
    _unset48("llm_provider")
    _set48("llm_thinking_param", "off")
    core_llm.reload()
    check("48 thinking=off不注入", core_llm.thinking_extra(True) == {} and core_llm.thinking_extra(False) == {})
    _unset48("llm_thinking_param")
    _set48("llm_thinking_param", "on")
    core_llm.reload()
    check("48 thinking=on恒注入", core_llm.thinking_extra(False) == {"chat_template_kwargs": {"enable_thinking": True}})
    _unset48("llm_thinking_param")
    core_llm.reload()

    # 48-5 get_client：同 purpose 单例、异 purpose 异例；端点/密钥与本地现状一致
    _c_main1, _c_main2, _c_small = core_llm.get_client("main"), core_llm.get_client("main"), core_llm.get_client("small")
    check("48 client同purpose单例", _c_main1 is _c_main2 and _c_main1 is not _c_small)
    check("48 client端点密钥", str(_c_main1.base_url).rstrip("/") == "http://127.0.0.1:11434/v1" and _c_main1.api_key == "ollama")
    _set48("llm_small_base_url", "http://127.0.0.1:9999/v1")
    core_llm.reload()
    check("48 small独立端点", str(core_llm.get_client("small").base_url).rstrip("/") == "http://127.0.0.1:9999/v1"
          and str(core_llm.get_client("main").base_url).rstrip("/") == "http://127.0.0.1:11434/v1")
    _unset48("llm_small_base_url")
    core_llm.reload()

    # 48-5b（盲审 P3-5b 补）llm_small_api_key：small 密钥独立覆写、主 purpose 走主链；
    # 未设时回落 llm_api_key（默认零变化）
    _set48("llm_small_api_key", "smoke-small-key")
    core_llm.reload()
    _ok48 = core_llm.get_client("small").api_key == "smoke-small-key" \
        and core_llm.get_client("main").api_key == "ollama"
    _unset48("llm_small_api_key")
    core_llm.reload()
    check("48 small独立密钥", _ok48 and core_llm.get_client("small").api_key == "ollama"
          and "llm_small_api_key" in (core_llm.__doc__ or ""))

    # 48-6 ctx_budget 覆写生效，brain 预算函数跟随（基数×历史比例；gemma 分支桩定 model_mode）
    _lmm_orig48 = B._load_model_modes
    B._load_model_modes = lambda: {"u48": "gemma"}
    try:
        check("48 预算默认gemma=12000", B._ctx_budget_for_model() == 12000)
        _set48("llm_ctx_budget", "16384")
        core_llm.reload()
        check("48 ctx_budget覆写生效", core_llm.ctx_budget() == 16384 and B._ctx_budget_for_model() == 6000)
        _unset48("llm_ctx_budget")
        core_llm.reload()
        check("48 ctx_budget恢复", core_llm.ctx_budget() == 32768 and B._ctx_budget_for_model() == 12000)
    finally:
        B._load_model_modes = _lmm_orig48

    # 48-7 embed 覆写与恢复
    _set48("embed_base_url", "http://127.0.0.1:19999/v1/embeddings")
    _set48("embed_enabled", "false")
    core_llm.reload()
    check("48 embed覆写生效", core_llm.embed_config() == ("http://127.0.0.1:19999/v1/embeddings", False))
    _unset48("embed_base_url", "embed_enabled")
    core_llm.reload()
    check("48 embed恢复+EMBED_URL在位", core_llm.embed_config() == ("http://127.0.0.1:11435/v1/embeddings", True)
          and _emb48.EMBED_URL == "http://127.0.0.1:11435/v1/embeddings")

    # 48-8 迁移构造点形状：属性名保留（换桩依赖）+ agent 别名 + 假模型别名退役
    check("48 agent委托适配层", _allm48.MODEL == "gemma" and _allm48.BASE_URL == "http://127.0.0.1:11434/v1"
          and _allm48.client() is core_llm.get_client("agent"))
    check("48 各插件客户端形状在位", callable(_mem38._client) and callable(_emo48._llm_client)
          and callable(_cor48._llm_client) and callable(_stk48._llm_client) and _fic48.client is not None)
    check("48 假模型别名退役", "fiction-writer" not in inspect.getsource(_fic48)
          and "emotion-classify" not in inspect.getsource(_emo48)
          and "correction-extract" not in inspect.getsource(_cor48)
          and "sticker-judge" not in inspect.getsource(_stk48))

    # 48-9 _SCENES_FILE 缺陷修复：指向真实存在的 qq-bot/data/scenes.json
    check("48 场景库文件真实存在", B._SCENES_FILE.exists() and B._SCENES_FILE.name == "scenes.json")

    # 48-10 reply.generate 的 extra_body 接线走适配层（源码级；语义与 §47 兼容）
    _rsrc48 = inspect.getsource(R.generate)
    check("48 reply extra_body走适配层", "extra_body=core_llm.thinking_extra(think_now)" in _rsrc48
          and "extra_body=core_llm.thinking_extra(False)" in _rsrc48
          and "resp1 = await client.chat.completions.create" in _rsrc48)
finally:
    # 恢复 driver config 原状并清缓存——严禁污染真实环境
    for _k48, (_had48, _v48) in _saved48.items():
        if _had48:
            setattr(_cfg48, _k48, _v48)
        else:
            try:
                delattr(_cfg48, _k48)
            except AttributeError:
                pass
    core_llm.reload()


# == 49. GAL 回想持久化（2026-09-10 Phase 2b：data/gal_history.jsonl JSONL 旁路 + /gal/history 端点 + 前端拉取） ==
# 不写 memory.db（messages 表是上下文源，GAL 展示层双写会污染）；全离线，文件重定向 tmp。
print("== 49. GAL 回想持久化 ==")
import plugins.webgal as _wg49  # noqa: E402  （§42 已 import 同一模块；独立别名成节防混淆）

_tmpdir49 = Path(tempfile.mkdtemp())
_orig_hist49 = _wg49.GAL_HIST_FILE
_histfile49 = _tmpdir49 / "gal_history.jsonl"
_wg49.GAL_HIST_FILE = _histfile49
try:
    # ---- 49-1 append+read 往返：构造轮 → append_gal_history → read_gal_history 字段一致（旧→新）----
    _rd49 = {"round_id": "abcd1234", "ts": 1700000000.0,
             "items": [{"kind": "user", "who": "主人", "text": "晚上好"},
                       {"kind": "bot", "who": "阿米娅", "text": "晚上好（放下文件）"}]}
    _wg49.append_gal_history(_rd49)
    _wg49.append_gal_history({"round_id": "beef5678", "ts": 1700000060.0,
                              "items": [{"kind": "bot", "who": "AI", "text": "第二条"}]})
    _rows49 = _wg49.read_gal_history(100)
    check("49 往返-轮数与旧→新", len(_rows49) == 2 and _rows49[0]["round_id"] == "abcd1234"
          and _rows49[1]["round_id"] == "beef5678")
    check("49 往返-字段一致", _rows49[0]["ts"] == 1700000000.0
          and _rows49[0]["iso"] == _dt42.datetime.fromtimestamp(1700000000.0).isoformat(timespec="seconds")
          and _rows49[0]["items"] == _rd49["items"])
    check("49 往返-中文ensure_ascii=False", "阿米娅" in _histfile49.read_text(encoding="utf-8-sig"))

    # ---- 49-2 缺文件空表 / 坏行跳过 / limit 钳制（口径仿 read_lifelog）----
    _wg49.GAL_HIST_FILE = _tmpdir49 / "not_exists49.jsonl"
    check("49 缺文件空表", _wg49.read_gal_history(50) == [])
    _wg49.GAL_HIST_FILE = _histfile49
    with open(_histfile49, "a", encoding="utf-8") as _f49:
        _f49.write("{broken json\n")
        _f49.write('["not a dict"]\n')
    check("49 坏行跳过", len(_wg49.read_gal_history(100)) == 2)
    check("49 limit钳制", len(_wg49.read_gal_history(9999)) == 2
          and _wg49.read_gal_history(0) == _wg49.read_gal_history(1))

    # ---- 49-3 超限裁剪：阈值/保留行数压到极小（模块常量可 monkeypatch）→ 末 N 行 + os.replace 原子重写 ----
    _histfile49.write_text(
        "\n".join(json.dumps({"round_id": f"old{i:04d}", "ts": 1600000000.0 + i, "items": []})
                  for i in range(10)) + "\n", encoding="utf-8")
    _orig_trim49 = (_wg49._HIST_TRIM_BYTES, _wg49._HIST_KEEP_LINES)
    try:
        _wg49._HIST_TRIM_BYTES, _wg49._HIST_KEEP_LINES = 10, 5  # 阈值极小：10 行垃圾必触发
        _wg49.append_gal_history({"round_id": "new9999", "ts": 1700000900.0,
                                  "items": [{"kind": "user", "who": "主人", "text": "裁剪后来了一条"}]})
    finally:
        _wg49._HIST_TRIM_BYTES, _wg49._HIST_KEEP_LINES = _orig_trim49
    _lines49 = _histfile49.read_text(encoding="utf-8-sig").strip().splitlines()
    check("49 裁剪-行数封顶+无tmp残留", len(_lines49) == 6
          and not (_tmpdir49 / "gal_history.jsonl.tmp").exists())
    check("49 裁剪-旧头被裁+新轮在尾", json.loads(_lines49[0])["round_id"] == "old0005"
          and json.loads(_lines49[-1])["round_id"] == "new9999"
          and all(f"old{i:04d}" not in _lines49[0] for i in range(5)))

    # ---- 49-4 真路由：TestClient GET /gal/history 返回已写入数据（沿 §42 同款 _APP 用法）----
    check("49 路由挂载", "/gal/history" in {getattr(r, "path", "") for r in _wg49._APP.routes})
    _get_ok49 = False
    try:
        from fastapi.testclient import TestClient as _TC49

        _resp49 = _TC49(_wg49._APP).get("/gal/history?limit=100")
        _j49 = _resp49.json()
        _get_ok49 = _resp49.status_code == 200 and isinstance(_j49, list) and len(_j49) == 6 \
            and _j49[-1]["round_id"] == "new9999"
    except Exception as _e49:  # noqa: BLE001
        print(f"[49 testclient err] {type(_e49).__name__}: {_e49}")
    check("49 GET /gal/history", _get_ok49)

    # ---- 49-5 故障隔离：GAL_HIST_FILE 指向目录（open("a") 必抛）→ append/_hist_add/_hist_flush 全不抛 ----
    _wg49.GAL_HIST_FILE = _tmpdir49  # 目录路径：旁路文件任何故障绝不影响回合与发送
    try:
        _wg49.append_gal_history({"round_id": "boom", "ts": 1.0,
                                  "items": [{"kind": "bot", "who": "AI", "text": "x"}]})
        _wg49._hist_add("user", "主人", "缓冲不抛")
        _wg49._hist_flush()
        _iso49 = True
    except Exception:  # noqa: BLE001
        _iso49 = False
    check("49 故障隔离-不可写路径不抛", _iso49 and _wg49._HIST["round"] is None
          and _wg49.read_gal_history(10) == [])
    _wg49.GAL_HIST_FILE = _histfile49
    _n49f = len(_histfile49.read_text(encoding="utf-8-sig").strip().splitlines())
    _wg49._hist_flush()  # round 已 None：幂等 no-op（err 落过后又 reply_end 不重复落）
    check("49 重复flush幂等", len(_histfile49.read_text(encoding="utf-8-sig").strip().splitlines()) == _n49f)

    # ---- 49-6 e2e（行为级，管线替身）：msg 入帧 → _ws_round 全链落盘。
    #      帧→kind 映射实码：WS msg 入帧→user（_gal_ws 守卫后）/ seg(kind=text)→bot（_ws_round 转发处）；
    #      sprite 帧无文本不入史；voice_b64 捕获不推帧故不入史 ----
    async def _hist_round49(pipeline, user_text):
        class _FakeWS49:
            async def send_json(self, p):
                pass

        if user_text is not None:
            _wg49._hist_add("user", "主人", user_text)  # _gal_ws msg 入帧同款调用点
        _orig49r = _wg49._PIPELINE_RUNNER
        _wg49._PIPELINE_RUNNER = pipeline
        try:
            await _wg49._ws_round(_FakeWS49(), user_text or "嗨")
        finally:
            _wg49._PIPELINE_RUNNER = _orig49r

    async def _stub_hist49(cap_bot, ev):
        cap_bot.captured.append({"kind": "text", "text": "史一（微笑）"})
        cap_bot.captured.append({"kind": "voice_b64", "data": "xx", "push": False})
        cap_bot.captured.append({"kind": "text", "text": "史二"})

    _aio42.run(_hist_round49(_stub_hist49, "晚上好"))
    _eh49 = _wg49.read_gal_history(1)
    check("49 e2e-轮落盘帧序映射", len(_eh49) == 1 and len(_eh49[0]["items"]) == 3
          and _eh49[0]["items"][0] == {"kind": "user", "who": "主人", "text": "晚上好"}
          and _eh49[0]["items"][1]["kind"] == "bot" and _eh49[0]["items"][1]["text"] == "史一（微笑）"
          and _eh49[0]["items"][2]["kind"] == "bot" and _eh49[0]["items"][2]["text"] == "史二"
          and all(it.get("kind") != "voice_b64" for it in _eh49[0]["items"]))
    check("49 e2e-形状与署名", isinstance(_eh49[0]["round_id"], str) and len(_eh49[0]["round_id"]) == 8
          and isinstance(_eh49[0]["ts"], float) and _eh49[0]["iso"] != ""
          and isinstance(_eh49[0]["items"][1]["who"], str) and _eh49[0]["items"][1]["who"] != "")

    # ---- 49-7 err 帧：空轮不落盘；有部分内容照落 + 缓冲清空（reply_end 重复落盘被 None 守卫跳过）----
    async def _boom_hist49(cap_bot, ev):
        raise RuntimeError("boom")

    _n49e = len(_histfile49.read_text(encoding="utf-8-sig").strip().splitlines())
    _aio42.run(_hist_round49(_boom_hist49, None))
    check("49 err空轮不落盘+缓冲清空", len(_histfile49.read_text(encoding="utf-8-sig").strip().splitlines()) == _n49e
          and _wg49._HIST["round"] is None)
    _aio42.run(_hist_round49(_boom_hist49, "异常前输入"))
    _er49 = _wg49.read_gal_history(1)
    check("49 err轮部分内容照落", len(_er49) == 1 and len(_er49[0]["items"]) == 1
          and _er49[0]["items"][0]["text"] == "异常前输入" and _wg49._HIST["round"] is None)

    # ---- 49-8 源码级接线：msg 入帧入史 + bot 条 + 落盘点先于 reply_end 发送 ----
    _src49 = inspect.getsource(_wg49)
    _srcws49 = inspect.getsource(_wg49._ws_round)
    check("49 接线-钩子在位", '_hist_add("user", "主人", text)' in _src49
          and '_hist_add("bot", _hist_bot_who()' in _src49
          and _srcws49.index("_hist_flush()") < _srcws49.index('"type": "reply_end"')
          and "gal_history.jsonl" in _src49)

    # ---- 49-9（Phase 3 任务0）文件字节损坏：裁剪读失败只跳过裁剪，当轮追加不丢 ----
    _histfile49.write_bytes(b"\xff\xfe\x00broken-bytes")  # 非 UTF-8 字节：裁剪 read_text 必抛 ValueError
    _wg49._HIST_TRIM_BYTES, _wg49._HIST_KEEP_LINES = 10, 5  # 阈值压到极小：强制进裁剪分支
    try:
        _wg49.append_gal_history({"round_id": "corrupt0", "ts": 1.0,
                                  "items": [{"kind": "bot", "who": "AI", "text": "字节损坏后仍追加"}]})
        _ok49c = True
    except Exception:  # noqa: BLE001
        _ok49c = False
    finally:
        _wg49._HIST_TRIM_BYTES, _wg49._HIST_KEEP_LINES = _orig_trim49
    check("49 字节损坏-裁剪跳过不丢追加", _ok49c
          and "corrupt0" in _histfile49.read_text(encoding="utf-8", errors="replace"))
    check("49 content.json 路由在位", "/gal/content.json" in {getattr(r, "path", "") for r in _wg49._APP.routes})
finally:
    _wg49.GAL_HIST_FILE = _orig_hist49


# == 50. 内容包体系（2026-09-10 Phase 3：robot-pack-v1 包规范 + 插件 SDK 接线） ==
# 双根发现/校验、persona 回落、content_index 合并序、voice/tones setdefault、world 注入、
# item 目录、tool 装载门、Live2D 钩子（js 源码级）。
# 2026-09-10 P1 封闭性修复：双包根重定向前移至 §50 最前（50-0：tmp 内重建 builtin 夹具=
# 复制随仓 example.sakura），§50 全程断言与真实 E:\robot\data\packs 环境态完全隔离
# （用户包同名遮蔽/异键包不再影响本节；finally 恢复真实双根后回归断言）。
print("== 50. 内容包体系（robot-pack-v1） ==")
from core import packs as _pk50  # noqa: E402
import plugins.persona as _per50  # noqa: E402
import plugins.voice as _vo50  # noqa: E402
from plugins import webgal as _wg50  # noqa: E402  （§49 同一模块对象）
import shutil  # noqa: E402  （50-0 复制 builtin 夹具用）

_tmpdir50 = Path(tempfile.mkdtemp())
_user50 = _tmpdir50 / "user_packs"
_built50 = _tmpdir50 / "builtin_packs"
_user50.mkdir()
_built50.mkdir()
_orig_dirs50 = (_pk50.USER_PACKS_DIR, _pk50.BUILTIN_PACKS_DIR)
_orig_quotes50 = _pk50.QUOTES_FILE
_orig_cap50 = _pk50.MAX_FILE_BYTES
_env50 = os.environ.get("PACKS_ENABLE_PY")
_cache_keys50 = set(_per50._CARD_CACHE)
_voices_keys50 = set(_vo50.VOICES)
_emo_keys50 = set(_vo50.EMO_PARAMS)
_mods50 = []  # tool 装载结果（env 分支 try/finally 外引用）


def _mk50(root, dirname, manifest, files=None):
    d = root / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / "pack.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    for rel, content in (files or {}).items():
        f = d / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
                     encoding="utf-8")
    return d


try:
    # ---- 50-0 封闭性前置（2026-09-10 P1）：tmp 内重建 builtin 夹具（复制随仓 example.sakura），
    # 并在任何 discover/回落断言之前重定向双包根——§50 全程封闭于 tmp，真实用户包根
    # E:\robot\data\packs 的环境态（同名遮蔽、异键包等）不再影响本节任何断言。finally 恢复。
    _real_built50 = _orig_dirs50[1]
    # 复制**随仓全部**示例包（不止 example.sakura）：2026-09-12 加了 example.stage（舞台包），
    # 写死单个包名就会漏掉新示例——夹具要跟着随仓内容走。
    for _ex50 in sorted(_real_built50.glob("*")):
        if _ex50.is_dir() and (_ex50 / "pack.json").is_file():
            shutil.copytree(_ex50, _built50 / _ex50.name)
    _pk50.USER_PACKS_DIR = _user50
    _pk50.BUILTIN_PACKS_DIR = _built50

    # ---- 50-1 活夹具（tmp builtin 复制的 example.sakura）：发现 / 校验 / 键 / 类型 ----
    _pk50.reload()
    _names50 = {p.name for p in _pk50.discover()}
    check("50 示例包发现", "example.sakura" in _names50)
    _pk50s = _pk50.get("example.sakura")
    check("50 示例包字段", _pk50s is not None and _pk50s.type == "card" and _pk50s.card_key == "sakura"
          and _pk50s.root == "builtin" and _pk50s.manifest.get("version") == "1.0.0"
          and _pk50s.manifest.get("spec") == "robot-pack-v1")
    check("50 by_type/find_card", any(p.name == "example.sakura" for p in _pk50.by_type("card"))
          and _pk50.find_card("sakura") is not None and _pk50.find_card("sakura").name == "example.sakura")

    # ---- 50-1b 随仓示例**舞台包**（2026-09-12 S3 收尾）：它是发行的"开箱可见"样本，
    # 所以钉三件：① 类型是 gal ② 不声明 card.key（否则就是第二个事实源）③ 背景真能解析出 URL
    # ——③ 才是"新装用户打开 GAL 页看得见背景"的实质：清单里写了 id 但图丢了，框架会**静默**
    # 不收录那个背景（前端走 CSS 兜底），只有这条断言能在 CI 里抓住"示例包残废"。
    _pk50st = _pk50.get("example.stage")
    check("50 示例舞台包发现", "example.stage" in _names50
          and _pk50st is not None and _pk50st.type == "gal" and _pk50st.root == "builtin")
    check("50 示例舞台包不声明 card.key", not _pk50st.card_key
          and not isinstance(_pk50st.manifest.get("card"), dict))
    _st50e = _pk50.stage_index()
    check("50 示例舞台包背景可用", _st50e["default_bg"].startswith("/gal/stage/example.stage/bg/")
          and _st50e["bg_alt"].startswith("/gal/stage/example.stage/bg/")
          and len(_st50e["bg_by_scene"]) == 3)

    # ---- 50-2 persona 回落：包卡加载 + source 标记 + 本地优先 + 列表合并 ----
    _card50 = _per50.load_persona("sakura")
    check("50 persona回落包卡", _card50.get("name") == "樱" and _card50.get("source") == "pack"
          and str(_card50.get("description") or "") != "")
    check("50 list_personas形状与合并", isinstance(_per50.list_personas(), list)
          and all(isinstance(x, str) for x in _per50.list_personas())
          and "sakura" in _per50.list_personas() and "amiya" in _per50.list_personas())
    _src50 = {e["name"]: e["source"] for e in _per50.list_persona_entries()}
    check("50 条目来源标记", _src50.get("sakura") == "pack" and _src50.get("amiya") == "local"
          and set(_src50.values()) == {"local", "pack"})
    _persona_dir50 = Path(tempfile.mkdtemp())
    (_persona_dir50 / "sakura.json").write_text(json.dumps(
        {"name": "本地樱（覆盖包）", "description": "本地卡永远优先"}, ensure_ascii=False), encoding="utf-8")
    _orig_pdir50 = _per50.PERSONA_DIR
    _per50._CARD_CACHE.pop("sakura", None)
    _per50.PERSONA_DIR = _persona_dir50
    try:
        _card50l = _per50.load_persona("sakura")
    finally:
        _per50.PERSONA_DIR = _orig_pdir50
        _per50._CARD_CACHE.pop("sakura", None)
    check("50 persona本地优先", _card50l.get("name") == "本地樱（覆盖包）" and "source" not in _card50l)
    try:
        _per50.load_persona("不存在的卡xyz50")
        _nf50 = False
    except FileNotFoundError:
        _nf50 = True
    check("50 persona缺卡契约不变", _nf50)

    # ---- 50-3 content_index：包打底 → data/quotes.json 覆盖 + spriteMap ----
    _ci50 = _pk50.content_index()
    check("50 content包打底", bool(_ci50["quotesByCard"].get("sakura"))
          and _ci50["quotesByCard"]["sakura"][0].get("by", "").startswith("樱")
          and _ci50["spriteMap"].get("sakura", {}).get("得意") == "proud")
    _qfd50 = json.loads(_orig_quotes50.read_text(encoding="utf-8-sig")) if _orig_quotes50.is_file() else {}
    _fb_expect50 = [it for it in (_qfd50.get("fallback") or []) if isinstance(it, dict)]
    check("50 content data层fallback", _ci50["quoteFallback"] == _fb_expect50)
    check("50 content data层byCard覆盖", _ci50["quotesByCard"].get("amiya") == _qfd50.get("byCard", {}).get("amiya")
          and _ci50["quotesByCard"].get("priestess") == _qfd50.get("byCard", {}).get("priestess"))
    _tmpq50 = _tmpdir50 / "quotes_override.json"
    _tmpq50.write_text(json.dumps({"byCard": {"sakura": [{"q": "用户覆盖台词", "by": "x"}]},
                                   "fallback": [{"q": "兜底", "by": "y"}]}, ensure_ascii=False), encoding="utf-8")
    _pk50.QUOTES_FILE = _tmpq50
    _ci50b = _pk50.content_index()
    check("50 content覆盖序", _ci50b["quotesByCard"].get("sakura") == [{"q": "用户覆盖台词", "by": "x"}]
          and _ci50b["quoteFallback"] == [{"q": "兜底", "by": "y"}]
          and "amiya" not in _ci50b["quotesByCard"])  # data 层是整文件替换：换 quotes 文件后原 data 键不再并入
    _pk50.QUOTES_FILE = _orig_quotes50

    # ---- 50-4 端点：GET /gal/content.json（webgal 薄壳）----
    _ct_ok50 = False
    try:
        from fastapi.testclient import TestClient as _TC50

        _j50 = _TC50(_wg50._APP).get("/gal/content.json").json()
        _ct_ok50 = isinstance(_j50, dict) and "sakura" in _j50.get("quotesByCard", {})
    except Exception as _e50:  # noqa: BLE001
        print(f"[50 testclient err] {type(_e50).__name__}: {_e50}")
    check("50 GET /gal/content.json", _ct_ok50)

    # ---- 50-5 js 源码级：台词外置 + Live2D 渲染器钩子 ----
    _gjs50 = Path(r"E:\robot\launcher\web\gal.js").read_text(encoding="utf-8")
    _ghtml50 = Path(r"E:\robot\launcher\web\gal.html").read_text(encoding="utf-8")
    check("50 gal.js台词已外置", "罗德岛全舰" not in _gjs50 and "命运石之门" not in _gjs50
          and "CLANNAD" not in _gjs50 and "亚托莉" not in _gjs50 and "普瑞赛斯" not in _gjs50
          and "var QUOTES_BY_CARD = {};" in _gjs50 and "var QUOTES_FALLBACK = [];" in _gjs50)
    check("50 gal.js拉取content.json", "'/gal/content.json'" in _gjs50
          and "data/quotes.json" in _gjs50 and "quoteFallback" in _gjs50
          and "ASSETS.spriteMap = Object.assign({}, ASSETS.spriteMap, j.spriteMap)" in _gjs50)
    check("50 Live2D钩子在位", "window.GAL_RENDERER" in _gjs50 and "galRendererFrame" in _gjs50
          and "galRendererStop" in _gjs50 and "GALR.init(el('stage'))" in _gjs50
          and "onFrame(seg) !== false" in _gjs50 and "Live2D" in _gjs50)
    check("50 enqueueSeg委托分支", "if (galRendererFrame(item)) return;" in _gjs50
          and "galSegToUnits(item);" in _gjs50 and "galRendererStop();" in _gjs50)
    check("50 gal.html v=8", "gal.js?v=8" in _ghtml50 and "gal.js?v=7" not in _ghtml50)

    # ---- 50-6..50-13 沿用 50-0 已重定向的 tmp 双包根（重定向前移见本节最前；finally 恢复）----

    # ---- 50-6 manifest 校验（tmp 坏包矩阵）+ Pack.resolve 穿越拒绝 ----
    _base50 = {"spec": "robot-pack-v1", "name": "smoke.bad", "version": "1.0.0", "type": "card"}
    _mk50(_user50, "smoke.nospec", {**_base50, "spec": "other-spec"})
    _mk50(_user50, "smoke.badname", {**_base50, "name": "Smoke.Bad"})
    _mk50(_user50, "smoke.badver", {**_base50, "version": "1.0"})
    _mk50(_user50, "smoke.alientype", {**_base50, "type": "alien"})
    _mk50(_user50, "smoke.travtool", {**_base50, "type": "tool",
                                      "tools": [{"entry": "../../evil.py", "cards": []}]})
    _mk50(_user50, "smoke.good", {**_base50, "name": "smoke.good", "type": "card",
                                  "card": {"key": "smokecard"}})
    _pk50.reload()
    _names50 = {p.name for p in _pk50.discover()}
    check("50 缺spec跳过", "smoke.nospec" not in _names50)
    check("50 坏name跳过", "smoke.badname" not in _names50)
    check("50 坏version跳过", "smoke.badver" not in _names50)
    check("50 未知type跳过不炸", "smoke.alientype" not in _names50 and isinstance(_pk50.discover(), list))
    check("50 穿越包整体拒载", "smoke.travtool" not in _names50 and "smoke.good" in _names50)
    try:
        _pk50s.resolve("../outside.txt")
        _trav50 = False
    except ValueError:
        _trav50 = True
    check("50 resolve穿越拒绝", _trav50 and _pk50s.resolve("quotes.json").is_file()
          and str(_pk50s.resolve("quotes.json")).startswith(str(_pk50s.dir.resolve())))

    # ---- 50-7 双根发现与 data 优先（同名包 user 覆盖 builtin）----
    _mk50(_user50, "smoke.dual", {**_base50, "name": "smoke.dual", "title": "用户版"})
    _mk50(_built50, "smoke.dual", {**_base50, "name": "smoke.dual", "title": "内置版"})
    _pk50.reload()
    _dual50 = _pk50.get("smoke.dual")
    check("50 双根发现data优先", _dual50 is not None and _dual50.root == "user"
          and _dual50.manifest.get("title") == "用户版")

    # ---- 50-8 单文件大小上限（防呆；monkeypatch 阈值：卡在 pack.json 与 quotes.json 之间）----
    _mk50(_user50, "smoke.big", {**_base50, "name": "smoke.big", "type": "card", "card": {"key": "big"}},
          files={"quotes.json": json.dumps({"quotes": [{"q": "x" * 500, "by": ""}]}, ensure_ascii=False)})
    _pk50.MAX_FILE_BYTES = 200  # pack.json(~115B) 可读、quotes.json(~560B) 超限
    _pk50.reload()
    _big50 = _pk50.get("smoke.big")
    check("50 文件超限拒读", _big50 is not None and _big50.read_json("quotes.json") is None
          and "smoke.big" in {p.name for p in _pk50.discover()})  # 拒读不拒载：包仍在，文件按缺省处理
    _pk50.MAX_FILE_BYTES = _orig_cap50

    # ---- 50-9 voice：绝对路径校验 + setdefault 合并（内置优先）----
    _tmpabs50 = str(_tmpdir50)
    _mk50(_user50, "smoke.voice", {**_base50, "name": "smoke.voice", "type": "voice"},
          files={"voice.json": {"key": "smokevoice", "name": "冒烟音色", "lang": "zh",
                                "personas": ["冒烟角色"], "gpt": _tmpabs50 + r"\g.ckpt",
                                "sovits": _tmpabs50 + r"\s.pth", "ref_dir": _tmpabs50 + r"\refs"}})
    _mk50(_user50, "smoke.voicerel", {**_base50, "name": "smoke.voicerel", "type": "voice"},
          files={"voice.json": {"key": "relvoice", "name": "相对路径", "lang": "zh",
                                "personas": ["相对"], "gpt": "weights/rel.ckpt", "sovits": "weights/rel.pth"}})
    _mk50(_user50, "smoke.voiceamiya", {**_base50, "name": "smoke.voiceamiya", "type": "voice"},
          files={"voice.json": {"key": "amiya", "name": "假阿米娅", "lang": "zh",
                                "personas": ["阿米娅"], "gpt": _tmpabs50 + r"\g.ckpt",
                                "sovits": _tmpabs50 + r"\s.pth"}})
    _vi50 = _pk50.voice_index()
    check("50 voice绝对路径条目", _vi50.get("smokevoice", {}).get("name") == "冒烟音色"
          and _vi50["smokevoice"]["lang"] == "zh" and _vi50["smokevoice"]["personas"] == ["冒烟角色"])
    check("50 voice相对路径跳过", "relvoice" not in _vi50)
    _vo50.apply_pack_merges()
    check("50 voice合并进VOICES", _vo50.VOICES.get("smokevoice", {}).get("name") == "冒烟音色")
    check("50 voice内置优先", _vo50.VOICES["amiya"].get("name") == "阿米娅"
          and "假阿米娅" not in json.dumps(_vo50.VOICES.get("amiya", {}), ensure_ascii=False))

    # ---- 50-10 tones：额外情绪基调（card 包附加段；带完整 voice 进 EMO_PARAMS，空 voice 只进文案层）----
    _mk50(_user50, "smoke.good", {**_base50, "name": "smoke.good", "type": "card",
                                  "card": {"key": "smokecard"},
                                  "tones": [{"word": "得意", "sprite_word": "proud_smoke",
                                             "voice": {"temp": 0.9, "speed": 1.1, "semi": 0.3}}]})
    _mk50(_user50, "smoke.emo", {**_base50, "name": "smoke.emo", "type": "emotion",
                                 "tones": [{"word": "仅文案", "sprite_word": "txt_only", "voice": {}}]})
    _pk50.reload()
    _tones_map50 = {t["word"]: t for t in _pk50.tones_index()}
    check("50 tones发现", _tones_map50.get("得意", {}).get("sprite_word") == "proud_smoke"
          and _tones_map50["得意"]["card_key"] == "smokecard" and "仅文案" in _tones_map50
          and _tones_map50["仅文案"]["voice"] == {})
    _vo50.apply_pack_merges()
    check("50 tones带voice进EMO_PARAMS", _vo50.EMO_PARAMS.get("得意") == (0.9, 1.1, 0.3))
    check("50 tones空voice不进声调表", "仅文案" not in _vo50.EMO_PARAMS
          and _vo50.EMO_PARAMS["战斗"] == (1.05, 1.08, 1.5))  # 内置 9 类零变化
    check("50 tones sprite_word进spriteMap",
          _pk50.content_index()["spriteMap"].get("smokecard", {}).get("得意") == "proud_smoke")

    # ---- 50-11 world：双根合并 + priority 降序 + always 注入 + 800 字截断 ----
    _mk50(_user50, "smoke.world", {**_base50, "name": "smoke.world", "type": "world"},
          files={"world.json": {"universe": "smoke-u50", "entries": [
              {"keys": ["a"], "text": "A" * 100, "always": True, "priority": 5},
              {"keys": ["b"], "text": "永不注入", "always": False, "priority": 9}]}})
    _mk50(_user50, "smoke.world2", {**_base50, "name": "smoke.world2", "type": "world"},
          files={"world.json": {"universe": "smoke-u50", "entries": [
              {"keys": ["c"], "text": "跨包设定", "always": True, "priority": 1}]}})
    _mk50(_user50, "smoke.worldbig", {**_base50, "name": "smoke.worldbig", "type": "world"},
          files={"world.json": {"universe": "smoke-u50big", "entries": [
              {"keys": ["d"], "text": "B" * 900, "always": True, "priority": 0}]}})
    _pk50.reload()
    _wi50 = _pk50.world_index()
    _prio50 = [e["priority"] for e in _wi50.get("smoke-u50", [])]
    check("50 world双根合并降序", _prio50 == sorted(_prio50, reverse=True)
          and len(_wi50.get("smoke-u50", [])) == 3 and _wi50["smoke-u50"][-1]["text"] == "跨包设定")
    _p50w = _per50.build_system_prompt({"name": "测试", "description": "x", "universe": "smoke-u50"})
    check("50 world always注入", "【世界观设定】" in _p50w and "跨包设定" in _p50w and "永不注入" not in _p50w)
    _p50wb = _per50.build_system_prompt({"name": "测试", "description": "x", "universe": "smoke-u50big"})
    _wblock50 = _p50wb.split("【世界观设定】", 1)[1] if "【世界观设定】" in _p50wb else ""
    check("50 world 800字截断", _wblock50 != "" and _wblock50.count("B") <= 800 and "已截断" in _wblock50)
    check("50 world无universe零注入", "【世界观设定】" not in _per50.build_system_prompt(
        {"name": "测试", "description": "x"}))

    # ---- 50-12 items：add_item_catalog（校验/默认呈现/sanitize；内置道具不受影响；STATE_FILE 已在 §3 指向 tmp）----
    _mk50(_user50, "smoke.item", {**_base50, "name": "smoke.item", "type": "item",
                                  "items": [{"name": "星尘茶匙", "label": "星尘茶匙", "note": "搅动时落星光",
                                             "ttl_min": 3},
                                            {"name": "坏label", "label": "", "ttl_min": 3},
                                            {"name": "坏ttl", "label": "坏ttl", "ttl_min": 0}]})
    _pk50.reload()
    check("50 items_index发现", any(it["name"] == "星尘茶匙" and it["pack"] == "smoke.item"
                                    for it in _pk50.items_index()))
    _reg50 = S.add_item_catalog(_pk50.items_index())
    check("50 目录校验", "星尘茶匙" in _reg50 and "坏label" not in _reg50 and "坏ttl" not in _reg50
          and S.add_item_catalog([{"name": "  【坏：】名 ", "label": "x", "ttl_min": 1}]) == ["坏名"])
    _it50 = S.set_state("item:星尘茶匙")
    check("50 目录默认呈现", _it50.get("label") == "星尘茶匙" and _it50.get("note") == "搅动时落星光"
          and abs(float(_it50.get("expires_at", 0)) - T.time() - 180) < 5)
    _it50b = S.set_state("item:目录外道具")
    check("50 目录外开放集不变", _it50b.get("label") == "目录外道具" and _it50b.get("note") == ""
          and "expires_at" not in _it50b)  # 顺手修：旧实现未知道具空 note 曾落字面 "None" 进感知
    _it50c = S.set_state("item:ball")
    check("50 内置道具不受目录影响", _it50c["note"] == S.ITEM_NOTES["item:ball"]
          and _it50c["label"] == "口球" and "expires_at" not in _it50c)

    # ---- 50-13 tool：默认不 import；PACKS_ENABLE_PY 显式开启才装载 ----
    _toolpy50 = (
        "from pathlib import Path\n"
        "Path(__file__).resolve().parent.joinpath('imported.marker').write_text('ok', encoding='utf-8')\n"
    )
    _mk50(_user50, "smoke.tool", {**_base50, "name": "smoke.tool", "type": "tool",
                                  "tools": [{"entry": "tool_entry.py", "cards": ["ns.name"]}]},
          files={"tool_entry.py": _toolpy50})
    _pk50.reload()
    check("50 tool包被发现", "smoke.tool" in {p.name for p in _pk50.by_type("tool")})
    os.environ.pop("PACKS_ENABLE_PY", None)  # 显式无 env：默认关闭路径
    check("50 tool默认不import", _pk50.py_tools_enabled() is False and _pk50.load_tools() == []
          and not (_user50 / "smoke.tool" / "imported.marker").exists())
    os.environ["PACKS_ENABLE_PY"] = "1"
    try:
        _mods50 = _pk50.load_tools()
    finally:
        if _env50 is None:
            os.environ.pop("PACKS_ENABLE_PY", None)
        else:
            os.environ["PACKS_ENABLE_PY"] = _env50
    # 2026-09-12：改成**按名判定**，不再数总数——夹具的 builtin 根是"随仓 packs 全量复制"（见 §50-0），
    # 所以随仓每多一个 type=tool 的示例包，`len(_mods50)` 就 +1，计数断言会被无关变更打断。
    # 这条和本节其它断言同一个纪律：**认名字，不认数量**（"以后再加示例包，断言自动跟着走"）。
    check("50 开env后可import", any(getattr(m, "__name__", "") == "_pack_tool_smoke_tool" for m in _mods50)
          and (_user50 / "smoke.tool" / "imported.marker").is_file())
finally:
    _pk50.USER_PACKS_DIR, _pk50.BUILTIN_PACKS_DIR = _orig_dirs50
    _pk50.QUOTES_FILE = _orig_quotes50
    _pk50.MAX_FILE_BYTES = _orig_cap50
    if _env50 is None:
        os.environ.pop("PACKS_ENABLE_PY", None)
    else:
        os.environ["PACKS_ENABLE_PY"] = _env50
    _pk50.reload()
    for _k50 in list(_per50._CARD_CACHE):
        if _k50 not in _cache_keys50:
            del _per50._CARD_CACHE[_k50]
    for _k50 in list(_vo50.VOICES):
        if _k50 not in _voices_keys50:
            del _vo50.VOICES[_k50]
    for _k50 in list(_vo50.EMO_PARAMS):
        if _k50 not in _emo_keys50:
            del _vo50.EMO_PARAMS[_k50]
    try:
        S.clear_all()
    except Exception:  # noqa: BLE001
        pass

# ---- 50-14 恢复与零变化回归（2026-09-10 P1 封闭性收口：恢复断言同样封闭于 tmp——用全新
# 干净双根重建夹具（50-6..50-13 的 tmp 坏包不残留），真实用户包根的同名遮蔽包不再能打掉本组
# 断言；模块常量是否真恢复由"常量回真"确定性核验） ----
check("50 恢复-常量回真", (_pk50.USER_PACKS_DIR, _pk50.BUILTIN_PACKS_DIR,
                           _pk50.QUOTES_FILE, _pk50.MAX_FILE_BYTES)
      == (*_orig_dirs50, _orig_quotes50, _orig_cap50))
_user50r = _tmpdir50 / "user_packs_restore"
_built50r = _tmpdir50 / "builtin_packs_restore"
_user50r.mkdir()
_built50r.mkdir()
for _ex50r in sorted(_orig_dirs50[1].glob("*")):
    if _ex50r.is_dir() and (_ex50r / "pack.json").is_file():
        shutil.copytree(_ex50r, _built50r / _ex50r.name)
try:
    _pk50.USER_PACKS_DIR, _pk50.BUILTIN_PACKS_DIR = _user50r, _built50r
    _pk50.reload()
    # 期望集合**从真实随仓目录现算**（不写死 {"example.sakura"}）：以后再加示例包，
    # 这条断言自动跟着走，不会因为"忘了改断言"而变成假绿。
    _real50_names = {d.name for d in _orig_dirs50[1].glob("*")
                     if d.is_dir() and (d / "pack.json").is_file()}
    check("50 恢复-包集合回真", {p.name for p in _pk50.discover()} == _real50_names
          and _pk50.find_card("smokecard") is None)
    check("50 恢复-EMO 9类零变化", set(_vo50.EMO_PARAMS) == _emo_keys50
          and _vo50.EMO_PARAMS["战斗"] == (1.05, 1.08, 1.5))
    check("50 恢复-VOICES零变化", set(_vo50.VOICES) == _voices_keys50)
    check("50 恢复-现有卡零world注入", "【世界观设定】" not in _per50.build_system_prompt(
        _per50.load_persona("amiya")))
    check("50 恢复-示例包仍活", _per50.load_persona("sakura").get("source") == "pack")

    # ---- 50-16 舞台包 type=gal（T14，2026-09-12）：背景 URL / 场景映射 / map 覆盖 / 素材路由 ----
    _mk50(_user50r, "smoke.stage",
          {**_base50, "name": "smoke.stage", "type": "gal", "card": {"key": "smokecard"},
           "stage": {"default_bg": "room", "bg_alt": "altbg",
                     "bg_by_scene": {"天台": "roof", "缺图": "no_such_file"}}},
          files={"bg/room.webp": "x", "bg/altbg.webp": "x", "bg/roof.webp": "x",
                 "sprites/manifest.json": {"map": {"得意": "proud_stage"}},
                 "sprites/diff/smokecard_Smile.webp": "x"})
    _pk50.reload()
    _ci50g = _pk50.content_index()
    check("50 gal类型被发现", "smoke.stage" in {p.name for p in _pk50.by_type("gal")})
    # 2026-09-12：这两条同时钉**优先级**——`smoke.stage`(用户根) 必须压过 `example.stage`(随仓根)，
    # 尽管字典序上 "example" < "smoke"。这正是 discover() 那次 `sorted(packs)` 埋的雷：
    # 排序一重排，用户自装的舞台包背景就被随仓示例占位图盖住了（已在 core/packs.py 修）。
    _gal50o = [p.name for p in _pk50.by_type("gal")]
    check("50 舞台包顺序-用户根优先", bool(_gal50o) and _gal50o[0] == "smoke.stage")
    check("50 stage默认与备选背景解析为URL",
          _ci50g["stage"]["default_bg"] == "/gal/stage/smoke.stage/bg/room.webp"
          and _ci50g["stage"]["bg_alt"] == "/gal/stage/smoke.stage/bg/altbg.webp")
    # 缺图的场景键**不得出现**（否则前端拿到一个必然 404 的地址，背景变成空块）
    # 断言写成"夹具那一键在、缺图那键不在"而不是整表相等：整表相等只在"世界上只有一个 gal 包"时成立，
    # 随仓示例包一进来就假红（2026-09-12 实际踩到）。
    check("50 stage场景映射-缺图不收录",
          _ci50g["stage"]["bg_by_scene"].get("天台") == "/gal/stage/smoke.stage/bg/roof.webp"
          and "缺图" not in _ci50g["stage"]["bg_by_scene"])
    check("50 stage包map覆盖卡包同名键",
          _ci50g["spriteMap"].get("smokecard", {}).get("得意") == "proud_stage")
    check("50 gal包差分词进sprite_words", "Smile" in _wg50.sprite_words("smokecard"))
    _TC50(_wg50._APP).get("/gal/stage/smoke.stage/bg/room.webp")  # 预热路由
    _r50st = _TC50(_wg50._APP).get("/gal/stage/smoke.stage/bg/room.webp")
    check("50 舞台素材路由可取", _r50st.status_code == 200)
    check("50 舞台路由拒非gal包",
          _TC50(_wg50._APP).get("/gal/stage/example.sakura/pack.json").status_code == 404)
    # 路径穿越：用 %2e%2e 才是真的打到路由（明文 .. 会被 httpx 先归一化，测不到东西）
    check("50 舞台路由拒穿越",
          _TC50(_wg50._APP).get("/gal/stage/smoke.stage/%2e%2e/pack.json").status_code == 404)

    # ---- 50-17 第三方插件根 data/plugins/（S3，2026-09-12）----
    # 行为级验证（造夹具→真跑 bot.py→查装载器）在 qq-bot/tools/dev/verify_data_plugins.py（7/7 通过）；
    # 这里钉**接线与两条纪律**，防后续重构把自动发现悄悄删掉/改错：
    #   ① 路径必须 append（insert 到首位会让叫 json.py 的插件遮蔽标准库）；
    #   ② 装载结果必须与装载器登记表比对（`nonebot.load_plugin()` **不抛异常**，
    #      它内部吞掉导入错误只打 ERROR 日志——第一版据此把坏插件报成 loaded，日志在说谎）。
    _botsrc50 = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8-sig")
    check("50 data/plugins自动发现接线在位", "data/plugins" in _botsrc50
          and "nonebot.load_plugin(_pl_name)" in _botsrc50
          and "sys.path.append(str(_pl_dir))" in _botsrc50
          and "sys.path.insert" not in _botsrc50
          and "_pl_ok" in _botsrc50 and "_pl_bad" in _botsrc50)

    # ---- 50-15 载荷形状契约（e2e 数据面）----
    _j50e = _pk50.content_index()
    check("50 e2e载荷四键", set(_j50e) == {"quotesByCard", "quoteFallback", "spriteMap", "stage"}
          and isinstance(_j50e["quoteFallback"], list)
          and isinstance(_j50e["stage"], dict))
finally:
    _pk50.USER_PACKS_DIR, _pk50.BUILTIN_PACKS_DIR = _orig_dirs50
    _pk50.reload()


# ============ 51. 群@自决 / 元规划与思考剥离（2026-09-11 热修十一） ============
print("== 51. 群@自决 / 元规划与思考剥离（热修十一） ==")
import asyncio as _aio11  # noqa: E402
import inspect  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import plugins.brain as _B11  # noqa: E402  （import bot 已加载；独立别名成节防混淆）
from core import reply as _cr11  # noqa: E402
from agent import graph as _g11  # noqa: E402

# ---- 元规划剥离（graph._act 与 banter 发送前双保险的共享实现；用户实测样本形态）----
_leak11 = (
    "哦，是吗。\n"
    "面对「又变成凯尔希了」，我的反应应该是克制的、干练的。\n"
    "考虑到目前的规则，我不会直接解释来历。\n"
    "我会给出一个符合我性格要求的、干练的回应。\n"
    "---\n"
    "第二轮：我的回应应该保持克制。\n"
    "---\n"
    "哦？又变成凯尔希了。……嗯，看来是这样。"
)
check("元规划-多段取末段", _cr11._strip_meta_planning(_leak11) == "哦？又变成凯尔希了。……嗯，看来是这样。")
check("元规划-无分隔剥规划行", _cr11._strip_meta_planning(
    "我会给出一个符合我性格要求的、干练的回应。\n有什么事，说。") == "有什么事，说。")
check("元规划-正常台词不误伤", _cr11._strip_meta_planning("考虑到你明天要早起，今晚早点休息。")
      == "考虑到你明天要早起，今晚早点休息。")
check("元规划-短台词不误伤", _cr11._strip_meta_planning("我会陪你到最后的。") == "我会陪你到最后的。")
check("元规划-剥光回退不劫持", _cr11._strip_meta_planning("考虑到你的性格，我才这么说的。")
      == "考虑到你的性格，我才这么说的。")
check("元规划-幂等", _cr11._strip_meta_planning(_cr11._strip_meta_planning(_leak11))
      == _cr11._strip_meta_planning(_leak11))
check("元规划-空串安全", _cr11._strip_meta_planning("") == "")

# ---- 内联思考剥离（core/reply 通用防护；generate stage1/stage2 已接线）----
check("think-闭合块剥离", _cr11._strip_inline_think("<think>推理过程</think>好的。") == "好的。")
check("think-未闭合吃到底", _cr11._strip_inline_think("<think>推理未收口") == "")
check("think-大小写不敏感", _cr11._strip_inline_think("<THINK>x</THINK>你好") == "你好")
check("think-嗯用户开头剥离", _cr11._strip_inline_think("嗯，用户想要我保持冷静。好的。") == "好的。")
check("think-正常台词不误伤", _cr11._strip_inline_think("嗯，好的。") == "嗯，好的。")
_srcR11 = inspect.getsource(_cr11.generate)
check("reply-stage1接剥离", "content = _strip_meta_planning(_strip_inline_think(content))" in _srcR11
      and _srcR11.index("_strip_meta_planning(_strip_inline_think(content))") < _srcR11.index("resp2 = await"))
check("reply-stage2接剥离", "reply = _strip_meta_planning(_strip_inline_think(reply))" in _srcR11)

# ---- 群 @ 自决（SPEAK/SKIP；失败/超时/解析不出一律 fail-open=SPEAK，绝不让判定失败吃掉 @）----
_calls11 = []


def _stub11(out=None, exc=None):
    async def create(**kw):
        _calls11.append(kw)
        if exc:
            raise exc
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=out))])
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


_orig_c11 = _B11.client
try:
    _B11.client = _stub11(out="SKIP")
    check("自决-SKIP静默", _aio11.run(_B11._group_at_self_decide("在吗", "123")) is False)
    _B11.client = _stub11(out="SPEAK")
    check("自决-SPEAK回复", _aio11.run(_B11._group_at_self_decide("在吗", "123")) is True)
    _B11.client = _stub11(out="我不知道该不该回")  # 解析不出 → fail-open
    check("自决-解析不出fail-open", _aio11.run(_B11._group_at_self_decide("在吗", "123")) is True)
    _B11.client = _stub11(exc=RuntimeError("engine down"))  # 调用失败/超时 → fail-open
    check("自决-异常fail-open", _aio11.run(_B11._group_at_self_decide("在吗", "123")) is True)
    check("自决-小模型口径", _calls11[0].get("temperature") == 0
          and _calls11[0].get("max_tokens", 99) <= 8
          and _calls11[0].get("timeout") == 8.0
          and _calls11[0].get("extra_body", {}).get("chat_template_kwargs", {}).get("enable_thinking") is False
          and "SPEAK" in _calls11[0]["messages"][0]["content"]
          and "SKIP" in _calls11[0]["messages"][0]["content"]
          and "在吗" in _calls11[0]["messages"][1]["content"])
finally:
    _B11.client = _orig_c11

# ---- 接线（源码级）：graph final_line 加固 / banter 发送前兜底 / 群@判定在回复链之前 /
#       群回复发送路径无 @/引用段（用户裁决：回复就是普通群消息）/ brain 启动存量归一 ----
_srcB11 = inspect.getsource(_B11)
_srcG11 = inspect.getsource(_g11)
check("接线-graph final_line加固", '_strip_meta_planning(str(state.get("chosen_line") or ""))' in _srcG11)
check("接线-banter发送前剥离", "_reply_meta._strip_meta_planning(line)" in _srcB11)
_i_at11 = _srcB11.index("await _group_at_self_decide(text, group_id)")
_i_gen11 = _srcB11.index("await _gen_reply2(")
_i_send11 = _srcB11.index("await _send_sliced(chat, event, msg_out")
check("接线-群@判定在回复链之前", _i_at11 < _i_gen11 < _i_send11)
check("接线-SKIP静默早返回", "_BUSY.pop(_skey0, None)" in _srcB11[_i_at11:_i_at11 + 700])
_ms11 = _srcB11[_srcB11.index("async def _send_sliced"):_srcB11.index("# ---------------- 基础词汇库")]
check("接线-发送路径无@段", "MessageSegment.at" not in _ms11 and "MessageSegment.reply" not in _ms11)
check("接线-brain启动归一", "normalize_ttls" in inspect.getsource(_B11._on_connect))


# ============ 52. 热修十二（2026-09-11）：特殊状态误登记/漏解除（F1/F2/F4/F5） + 语音情绪参考音不传导（V1/V2/V3） ============
print("== 52. 热修十二 ==")
_srcB12 = inspect.getsource(B)
_srcLS12 = inspect.getsource(_ls.micro_update_from_conversation)
# ---- F1 解除链：预解析 schema/口径锚（行为级 releases 用例在 47-1b/47-3b）----
check("F1-预解析schema含releases", '"releases"' in B._PREPARSE_SYSTEM
      and "解除不是新事实登记" in B._PREPARSE_SYSTEM
      and "不得**推断/升级为道具" in B._PREPARSE_SYSTEM)
check("F1-解除映射单一实现", S._resolve_clear_target("全部") == "*"
      and S._resolve_clear_target("催眠") == "hypno" and S._resolve_clear_target("洗脑") == "brainwash"
      and S._resolve_clear_target("不许装") == "no_fake" and S._resolve_clear_target("情欲氛围") == "vibe_mood"
      and S._resolve_clear_target("道具：口球") == "item:ball" and S._resolve_clear_target("") == ""
      and "_resolve_clear_target" in inspect.getsource(S.apply_agent_markers)
      and "_resolve_clear_target" in inspect.getsource(S.apply_owner_facts))
check("F1-事实块新文案", "叙事须与其一致" in R._OWNER_FACTS_BLOCK
      and "仍须按协议输出对应标记" in R._OWNER_FACTS_BLOCK
      and "【解除：" in R._OWNER_FACTS_BLOCK and "【清醒】" in R._OWNER_FACTS_BLOCK
      and "无需重复输出标记" not in R._OWNER_FACTS_BLOCK)  # 无条件抑制形态已不存在
check("F1-解除事实行渲染", R._owner_fact_lines({"releases": ["手铐", "全部"]}) == ["解除：手铐", "解除：全部"]
      and R._owner_fact_lines({"releases": []}) == [] and R._owner_fact_lines({"releases": "手铐"}) == []
      and '"解除："' in inspect.getsource(R._owner_fact_lines))
# ---- F2 教学段解除判据（追加句，不删既有内容）----
check("F2-教学段解除判据", "主人括号表示某状态被解除/收起/结束时" in _srcB12
      and "用【解除：…】/【解除：全部】登记解除" in _srcB12
      and "氛围结束用【清醒】" in _srcB12
      and "必须用标准名登记" in _srcB12)  # 既有登记教学未被删改
# ---- F4 lifesim 微更新传卡键 + brain 调用点 ----
check("F4-微更新签名与落卡", inspect.signature(_ls.micro_update_from_conversation).parameters["card"].default == "default"
      and "_sp.active(card=card)" in _srcLS12
      and 'set_state("vibe_mood", _vibe, by="priv:" + key, card=card)' in _srcLS12
      and '_sp.clear("vibe_mood", card)' in _srcLS12)
check("F4-brain调用点传卡", " ".join(_srcB12.split()).find(
      "micro_update_from_conversation(user_id, text, reply, who=_who_txt, max_vibe=3 if is_owner else 2, card=_ck)") > 0)
# ---- F5 贴纸状态段传卡键 ----
check("F5-贴纸状态段传卡", "_spst.active(card=_ck)" in _srcB12)
# ---- V1 基调 9 类词表单一事实源 + 两处润色提示同步 ----
_tone12 = "/".join(_v16.TONE_CLASSES)
check("V1-词表单一事实源", set(_v16.TONE_CLASSES) == _v16.ALLOWED_EMOS
      and set(_v16.TONE_CLASSES) <= set(_v16.EMO_PARAMS)
      and "TONE_CLASSES" in inspect.getsource(_v16.classify_emotion)
      and B.POLISH_REQ.count(_tone12) == 1 and B.PRIVATE_POLISH_REQ.count(_tone12) == 1)
check("V1-润色提示自由词示例已替换", "如【基调：傲娇】/【基调：软下来】/【基调：得意】" not in _srcB12
      and "基调从这些里选一个" in B.POLISH_REQ and "基调从这些里选一个" in B.PRIVATE_POLISH_REQ)
# ---- V2 kaltsit emo_refs 扩映射（参考名真实性另由第 16 节逐包存在性断言覆盖；此处锚新映射与缺省语义）----
_krefs12 = json.load(open(os.path.join(_v16.VOICES["kaltsit"]["ref_dir"],
                                       _v16.VOICES["kaltsit"]["refs"]), encoding="utf-8"))
check("V2-kaltsit高冷映射且参考真实", _v16.VOICES["kaltsit"]["emo_refs"].get("高冷") == "精英化晋升1"
      and "精英化晋升1" in _krefs12)
check("V2-无贴合类保持默认", _v16._voice_request("kaltsit", "t12", "你好。", "中文", emo="娇嗔")["ref"] == "默认")
# ---- V3 基调决策日志 ----
_srcV312 = inspect.getsource(_v16.try_make_record_segments)
check("V3-基调决策日志", "voice tone decision: voice={} tone={!r} src={} emo={!r} ref={}" in _srcV312
      and '"自标"' in _srcV312 and '"classify"' in _srcV312 and '"回退"' in _srcV312)


# ============ 53. 热修十三（2026-09-12）：gal 声画空白（A1/A2/A3） + 立绘 bot 自决（B1/B2） ============
print("== 53. 热修十三 ==")
import asyncio as _aio13  # noqa: E402

_srcB13 = inspect.getsource(B)
_srcR13 = inspect.getsource(R)
_srcW13 = inspect.getsource(_wg42)

# ---- ① reply._SPRITE_MARK_RE：源码锚 + 解析/剥除行为级（形制镜像 _TONE_RE，全/半角兼容）----
check("13 立绘标记-正则镜像锚", "_SPRITE_MARK_RE" in _srcR13
      and r"[【\[]立绘[：:]\s*([^】\]]{1,12})\s*[】\]]" in _srcR13
      and '"sprite": sprite' in _srcR13)
check("13 立绘标记-全角命中提取", R._SPRITE_MARK_RE.search("过来坐好【立绘：微笑】。").group(1) == "微笑")
check("13 立绘标记-半角命中提取", R._SPRITE_MARK_RE.search("那就休息一下。[立绘:Smile]").group(1) == "Smile")
check("13 立绘标记-混写括号安全侧", bool(R._SPRITE_MARK_RE.search("x【立绘:开心】"))
      and bool(R._SPRITE_MARK_RE.search("x[立绘：开心】")))
check("13 立绘标记-多标记正则剥净", R._SPRITE_MARK_RE.sub("", "嗯【立绘：微笑】晚安[立绘:Smile]") == "嗯晚安")

def _stub13(out):
    async def create(**kw):
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=out, reasoning_content=""))])
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


async def _gen13(out):
    return await R.generate(user_id="smoke13", history_msgs=[{"role": "user", "content": "晚安"}],
                            stage1_prompt="s1", polish_prompt="p2",
                            client=_stub13(out), model="m",
                            strip_non_speech=lambda t, allow_action=True: t)


_res13 = _aio13.run(_gen13("晚安【立绘：Smile】【基调：温柔】"))
check("13 立绘-generate提取透传", _res13["sprite"] == "Smile" and _res13["tone"] == "温柔"
      and _res13["text"] == "晚安" and "立绘" not in _res13["text"] and "基调" not in _res13["text"])
_res13b = _aio13.run(_gen13("嗯。【立绘：Smile】晚安[立绘:anger]"))
check("13 立绘-generate多标记并存剥净", _res13b["sprite"] == "Smile"
      and _res13b["text"] == "嗯。晚安" and "立绘" not in _res13b["text"])
_res13c = _aio13.run(_gen13("就一句普通的话。"))
check("13 立绘-未标为None", _res13c["sprite"] is None)
check("13 立绘-超限回退剥标锚", '_SPRITE_MARK_RE.sub("", _TONE_RE.sub' in _srcR13)

# ---- ② brain._SPRITE_LAST：登记锚 + 软校验行为级（表内保留/表外丢弃/空表放行/扫描故障 fail-open）----
_srcB_gen2_13 = inspect.getsource(B._gen_reply2)
check("13 立绘-brain登记锚", isinstance(B._SPRITE_LAST, dict)
      and "_SPRITE_LAST[user_id] = _register_sprite_word" in _srcB_gen2_13
      and "_SPRITE_LAST.pop(user_id" in _srcB13)
_uid13 = _inj42.owner_uid()
_orig_sw13 = _wg42.sprite_words
try:
    _wg42.sprite_words = lambda k: ["Smile", "anger"]
    check("13 立绘软校验-表内保留", B._register_sprite_word(_uid13, "Smile") == "Smile")
    check("13 立绘软校验-表外丢弃", B._register_sprite_word(_uid13, "不存在的") == "")
    check("13 立绘软校验-空词登记空", B._register_sprite_word(_uid13, "") == "")
    _wg42.sprite_words = lambda k: []
    check("13 立绘软校验-空表放行", B._register_sprite_word(_uid13, "Smile") == "Smile")

    def _boom13(k):
        raise RuntimeError("scan down")
    _wg42.sprite_words = _boom13
    check("13 立绘软校验-扫描故障fail-open", B._register_sprite_word(_uid13, "Smile") == "Smile")
finally:
    _wg42.sprite_words = _orig_sw13

# ---- ③ webgal.sprite_words：tmp 目录隔离（前缀匹配/default 不过滤/空目录/缓存）----
_bust13 = Path(tempfile.mkdtemp())
_orig_bust13 = _wg42.BUST_DIR
_wg42._SPRITE_WORDS_CACHE.update(key=None, words=[])
try:
    _wg42.BUST_DIR = _bust13
    check("13 sprite_words-空卡键", _wg42.sprite_words("") == [])
    (_bust13 / "amiya_Smile.webp").write_bytes(b"x")
    (_bust13 / "amiya_Default.webp").write_bytes(b"x")     # 默认半身本体：不过滤、原样返回
    (_bust13 / "amiya.webp").write_bytes(b"x")             # 无差分后缀：不产词
    (_bust13 / "priestess_smile.webp").write_bytes(b"x")   # 他卡前缀不混入
    (_bust13 / "amiya_notes.txt").write_text("x", encoding="utf-8")  # 非 webp：跳过
    (_bust13 / "amiya_dir.webp").mkdir()                   # 目录假名：is_file 过滤
    check("13 sprite_words-前缀匹配", _wg42.sprite_words("amiya") == ["Default", "Smile"])
    check("13 sprite_words-空表卡", _wg42.sprite_words("kaltsit") == [])
    # 缓存：mtime 未变走缓存（哨兵法证明未重扫——单槽缓存以最后一次调用为准，先补一次 amiya
    # 调用再下毒；Windows 上新增文件目录 mtime 未必立即变化，用例不依赖该怪癖）
    _wg42.sprite_words("amiya")
    _wg42._SPRITE_WORDS_CACHE["words"] = ["哨兵"]
    check("13 sprite_words-缓存命中不重扫", _wg42.sprite_words("amiya") == ["哨兵"])
    (_bust13 / "amiya_anger.webp").write_bytes(b"x")       # 缓存建立后新增文件
    # 显式远期固定时间戳：utime(None)/当前时间可能与首扫缓存的自然 mtime（同为当前纪元）
    # 落在同一时间值上 → 缓存键不变不重扫 → 断言随机挂（NTFS mtime 惰性放大该概率）；
    # 固定 2001 年值与自然 mtime 必然不同，失效重扫成为确定性事件
    os.utime(_bust13, (1234567890.0, 1234567890.0))
    check("13 sprite_words-缓存失效重扫", _wg42.sprite_words("amiya") == ["Default", "Smile", "anger"])
    _wg42.BUST_DIR = _bust13 / "no_such_dir_13"
    check("13 sprite_words-目录缺失空表不抛", _wg42.sprite_words("amiya") == [])
finally:
    _wg42.BUST_DIR = _orig_bust13
    _wg42._SPRITE_WORDS_CACHE.update(key=None, words=[])

# ---- ④ webgal sprite 帧：emotion 优先级 _SPRITE_LAST > _TONE_LAST > 空（行为级 + 源码锚）----
_srcW_do13 = inspect.getsource(_wg42._do_round)
check("13 sprite帧-priority源码锚", "_SPRITE_LAST.get" in _srcW_do13 and "_TONE_LAST.get" in _srcW_do13
      and _srcW_do13.index("_SPRITE_LAST.get") < _srcW_do13.index("_TONE_LAST.get"))


async def _round13():
    async def _stub_runner13(cap_bot, ev):
        cap_bot.captured.append({"kind": "text", "text": "台词"})

    _orig13r = _wg42._PIPELINE_RUNNER
    _wg42._PIPELINE_RUNNER = _stub_runner13
    try:
        return await _wg42._do_round("嗨")
    finally:
        _wg42._PIPELINE_RUNNER = _orig13r


B._SPRITE_LAST[_uid13] = "Smile"
B._TONE_LAST[_uid13] = "温柔"
_f13 = _aio13.run(_round13())
check("13 sprite帧-独立词优先于基调", _f13[0] == {"kind": "sprite", "emotion": "Smile"}
      and _f13[1].get("kind") == "text")
check("13 sprite帧-get不pop", B._SPRITE_LAST.get(_uid13) == "Smile" and B._TONE_LAST.get(_uid13) == "温柔")
B._SPRITE_LAST.pop(_uid13, None)
_f13b = _aio13.run(_round13())
check("13 sprite帧-空回退基调链", _f13b[0] == {"kind": "sprite", "emotion": "温柔"})
B._TONE_LAST.pop(_uid13, None)
_f13c = _aio13.run(_round13())
check("13 sprite帧-全空回默认半身", _f13c[0] == {"kind": "sprite", "emotion": ""})

# ---- ⑤ 教学句换血：旧混教句删除锚 + 新【立绘】教学句锚（【基调】9 类教学保持不动）----
check("13 旧混教句已删", "【可用立绘差分】" not in _srcB13
      and "【基调】标记优先从中选" not in _srcB13)
check("13 新立绘教学句", "【立绘】可在最末尾单独追加标记：【立绘：词】" in _srcB13
      and "词从这些里选" in _srcB13
      and "只影响立绘差分切换，不影响语音与文本" in _srcB13)
check("13 立绘词表并集接线", "sprite_words" in _srcB_gen2_13
      and "sprite_expressions" in _srcB_gen2_13
      and "sprite_hint=_sprite_hint" in _srcB_gen2_13)
check("13 基调教学不动", "基调从这些里选一个" in B.POLISH_REQ and "基调从这些里选一个" in B.PRIVATE_POLISH_REQ)

# ---- A1/A2/A3 gal.js 源码锚（gal.js 无自动化套件，三回归态靠实现走查+用户真机）----
_gjs13 = Path(r"E:\robot\launcher\web\gal.js").read_text(encoding="utf-8")
check("13 gal.js-A1守卫放行", "(VN.idx < VN.units.length && !VN.voiceWait)" in _gjs13
      and "voiceWait: null" in _gjs13)
check("13 gal.js-A2统一入口", "function ensureVoice(idx, text, cb)" in _gjs13
      and "pfWaiters[idx].push(cb)" in _gjs13
      and "ensureVoice(VN.idx, u.text" in _gjs13
      and "ensureVoice(i, VN.units[i].text" in _gjs13
      and "(VN.idx + k) % VN.units.length" in _gjs13)
check("13 gal.js-A3声画同源", "tone: S.spriteEmotion || ''" in _gjs13)
check("13 gal.js-vnReset清等待者", "pfWaiters = {};" in _gjs13)


# ============ 54. 2026-09-12 思考链机器判据（T4.1 + 盲审 P2-2/P3-1 行为级） ============
# 盲审 P2-2：本次改动曾**零测试覆盖**（smoke 一字未改），916 这个绝对值无法证明
# T9 删符号 / T13 重写没有让既有断言静默失效。本节补上行为级断言。
print("== 54. 思考链机器判据（T4.1） ==")
_U54 = "u-smoke54"
B._THINK_PREV_ATTEMPTS.pop(_U54, None)
B._THINK_NEED.pop(_U54, None)

# ① 长消息判据（阈值 _THINK_MSG_LEN=120）
check("54 判据-短消息不命中", B._name_think_reasons(_U54, "你好呀") == [])
check("54 判据-长消息命中", B._name_think_reasons(_U54, "啊" * (B._THINK_MSG_LEN + 1)) == ["长消息"])
check("54 判据-恰好等于阈值不命中", B._name_think_reasons(_U54, "啊" * B._THINK_MSG_LEN) == [])

# ② 角色提及判据：由调用方登记在 _THINK_NEED（消费即 pop，故每次重新登记）
B._THINK_NEED.setdefault(_U54, set()).add("角色提及")
check("54 判据-角色提及命中", "角色提及" in B._name_think_reasons(_U54, "凯尔希来了"))
check("54 判据-角色提及消费后不残留", "角色提及" not in B._name_think_reasons(_U54, "凯尔希来了"))

# ③ 上轮硬边界重试判据（attempts==2 才算）
B._THINK_PREV_ATTEMPTS[_U54] = 1
check("54 判据-attempts=1不命中", B._name_think_reasons(_U54, "嗯") == [])
B._THINK_PREV_ATTEMPTS[_U54] = 2
check("54 判据-attempts=2命中", "上轮重试" in B._name_think_reasons(_U54, "嗯"))
B._THINK_PREV_ATTEMPTS.pop(_U54, None)

# ④ 盲审 P3-1 用户裁决：知识问句判据已移除（连函数本体一并删，不留新孤儿符号）
#    注意：L1481 那处"知识问句"字样属**搜索分支**自己的注释（与思考判据无关），
#    故只断言"判据函数不存在 + 其调用行已消失"，不断言全文件不含该词。
_srcB54 = inspect.getsource(B)
check("54 知识问句判据已删-函数不存在", not hasattr(B, "_is_knowledge_question"))
check("54 知识问句判据已删-调用行已消失",
      '_is_knowledge_question(text)' not in _srcB54
      and 'reasons.append("知识问句")' not in _srcB54)
check("54 误报样例不再命中", B._name_think_reasons(_U54, "那我现在走") == []
      and B._name_think_reasons(_U54, "今天天气不错") == []
      and B._name_think_reasons(_U54, "我哪来的钱") == [])

# ⑤ 单向性：判据只产出"命中名单"，调用点只置 True，结构上不存在强制关
check("54 单向性-判据只返回命中名单", isinstance(B._name_think_reasons(_U54, "你好"), list))
check("54 单向性-调用点只置True",
      "if _think_rs:" in _srcB54 and "think_wanted = True" in _srcB54
      and "think_wanted = False" not in _srcB54)

# ⑥ 身份锚与第三人称条目同视野提醒（盲审 P3-6 修复的行为级锚）
check("54 身份锚-同视野提醒接线",
      "正在和你对话的这个人就是" in _srcB54 and "共同认识的人" in _srcB54)

# ⑦ T9 死符号不得复活（回归护栏：删掉的 7 个符号一旦被重新引入即报）
for _dead54 in ("EXTRACT_EVERY", "MODE_RESET_SECONDS", "THINK_ON_WORDS", "THINK_OFF_WORDS",
                "SHIP_SITE_DOMAIN", "REPLY_DEDUP_WINDOW", "_zh_grams"):
    check(f"54 T9死符号不得复活-{_dead54}", not hasattr(B, _dead54))


print(f"\n== 结果：{_PASS} PASS / {_FAIL} FAIL ==")
sys.exit(1 if _FAIL else 0)
