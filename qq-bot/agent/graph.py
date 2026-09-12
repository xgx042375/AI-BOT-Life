"""agent/graph.py —— LangGraph 状态图（感知 → 自决 → 行动 → 反思）+ SqliteSaver 持久化

节点职责（ReAct × Reflexion 混合）：
  perceive ：组装感知摘要（本地网络用语词典 / 反思程序记忆 / 需求检索）
  decide   ：LLM 自决（只给感知 + 红线，不给指令；输出【不插话】或台词）
  act      ：输出护栏 → 产生 final_line（供主程序发送）
  reflect  ：反思写回（记录发言 + 反馈标记，程序记忆）

checkpointer：data/agent_checkpoints.db（崩溃恢复 + 决策回放；S6 验证回放）。

注意：全部节点 async（LLM/检索为 IO），调用方使用 `await run_agent(...)`。
"""
from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from core.paths import DATA_ROOT

from loguru import logger
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from . import guardrails, llm, tools
from .state import AgentState
from core.reply import _near_duplicate  # 2026-09-08：poke 防同质（主链同款事故级近似检查）
from core.reply import _strip_meta_planning  # 2026-09-11 热修十一：final_line 元规划剥离（与 banter 发送前同源双保险）

AGENT_DB = DATA_ROOT / "agent_checkpoints.db"

_DECISION_PROMPT = (
    "下面是一段群聊。你（按你的人设卡）判断：此刻你想不想自然插一句话（1-2 句，你的口吻）？\n"
    "**你是这个群的活人成员**，接不接看你此刻的真实状态（2026-09-06 放宽：不再默认缄默）：\n"
    "· 没感觉/话题跟自己无关/刚说过 → 安静看着就好（输出【不插话】），这很正常；\n"
    "· 不懂的话题但有点兴趣 → 背景已经替你查好在【背景】里——看得懂就用自己的话接，还是不懂就安静；\n"
    "· 上次在这里接话反响差（【上次经验】里有被嫌弃的记录）→ 这轮先不接，你心里有数就行；\n"
    "· 说话的人你们基本不熟（感知里写了）→ 默认安静——陌生人没义务陪话，除非被直接点名、被冒犯，"
    "或对方说的实在有趣到忍不住；熟一点的人可以偶尔自然接一句；\n"
    "· 被逗到忍不住、有真实观点、被点名、接话的自然延续、特别想关心谁 → 直接说，这就是真实原因。\n"
    "你的每一条消息都应该有真实原因；别为了合群凑话，也别把随口的话当发言。\n"
    "【插话质量门槛】插话的唯一标准：这个话题让你**真的想说**（好笑/在意/有立场/与你的经历或关心的事有关）。"
    "礼貌性附和、泛泛地接一句、没有实际内容的话＝**选择不说话**——直接输出【不插话】。"
    "少说话完全自然，没有人要求你必须接每一句。\n"
    "① 有话说（想吐槽/想接话/被逗到/被点名/有观点）→ 直接输出那句话本人；\n"
    "② 没话说（平静/无聊/吃瓜但懒得说/没概念）→ 只输出【不插话】三个字。\n"
    "**输出即台词**：你输出的一切都会被当作消息发出去——思考推演（判断、分析、犹豫、选择过程）不是消息："
    "任何「我在想该不该…」只许憋在心里；你只能输出两种东西：【不插话】三字，或你想说的那 1-2 句话。\n"
    "硬性：禁止输出情绪词、过程、括号注释（那都是事故）；思考过程/内心独白同样禁止"
    "【最重要】你判断「不参与」→ 只说【不插话】三个字，绝不把它包装成一句话题目："
    "❌「作为性格稳重的我，此时无需插话」「我选择静观其变」「还是先不参与的好」"
    "——这类「选择沉默」的叙述本身就是废话（说出口=承认你在旁观），只算【不插话】的一种错误写法；"
    "你真的判断不参与，就只输出【不插话】，一个字不多。"
    "（「我在犹豫」「要不要接话」「这个话题离我有点远」这类心里话不是台词——憋回心里，要么说真话，要么【不插话】）；"
    "禁止空泛附和与场面话（没有实际内容就归入【不插话】，不必替合群负责）；"
    "看不懂就安静（别的处理由你自己决定，不要自曝“我不懂”）；不要 @ 任何人。\n"
    "【开口节奏】若你觉得对方这句话可能还没说完（刚断句、正在连发），你想慢半拍开口——"
    "可以在输出末尾加一行【等 X 秒】；X 你自己定（0 到 2，只写数字）；机器只读这一行，不会发出去。"
    "没有这种感觉就不要加。\n"
    "（以下是刚检索到的背景，可用自己的话说：）\n{bg}\n\n（你此刻的心情：{mood}）"
)


_POKE_PROMPT = (
    "你正在被打扰——有人用手戳了你一下（轻轻点你、想引起你注意）。\n"
    "这是你的**第一反应**：吓一跳？痒？笑出来？无语？害羞？恼？（回戳一把也可以，一句话带过）——"
    "按你此刻的人设与心情，你自己决定怎么反应。\n"
    "直接输出你的反应台词（1 句，长短自然，像活人冷不丁被戳到的反应）："
    "不解释、不带括号、不输出【】、不重复你以前说过的反应——这次可以是任何反应。"
)


# 检索缓存（2026-09-04 性能：插话评估频繁，同一群话题几分钟内稳定——query 相同直接复用，省一次 LLM+web）
# 注意：不允许模块级 asyncio.Lock（绑定创建时的 event loop，asyncio.run 新循环复用会死锁）；
# 并发重复检索是无害幂等读，不做互斥——用「命中即用」的 TTL 缓存足够。
_SEARCH_CACHE: dict = {}          # key "gid:query" -> {"bg": str, "ts": float}
_SEARCH_TTL = 300.0               # 5 分钟

# 2026-09-06 开口节奏：模型可选追加【等 X 秒】（0-2）→ 提取停顿、剥记号
# 2026-09-09 深夜：兼容全/半角括号（与 core/reply._PACE_RE 同口径——主链实锤 LLM 括号漂移
# 半角 [基调：x] 三次，banter 提示词同样主动提供【等 X 秒】协议，同类漂移曾会漏剥原样发出+落库）
_PACE_RE = re.compile(r"[【\[]等\s*([0-9]+(?:\.[0-9]+)?)\s*秒[】\]]")


def _extract_pacing(text: str) -> tuple[float, str]:
    """解析【等 X 秒】→ (pacing, 剥记后的文本)。无标记 → (0.0, text)。"""
    m = _PACE_RE.search(text or "")
    if not m:
        return (0.0, text)
    try:
        p = min(2.0, max(0.0, float(m.group(1))))
    except Exception:  # noqa: BLE001
        p = 0.0
    return (p, (text.replace(m.group(0), "") or "").strip())


_GREET_PROMPT = (
    "此刻你在自己的一天里，心里考虑要不要主动找TA说句话（没有人在等你回消息——选不选由你自己）。\n"
    "先看看你感受到的情况：上次说话离现在多久、TA最近是否活跃、你今天已经主动找过TA几次、"
    "你此刻的生活状态、有没有想TA/想跟TA说的事。\n"
    "① 你心里**没有**想找TA的念头（刚聊过不久、TA正忙、今天已找过、自己也没话想说）→ 只输出【不想找】三个字；\n"
    "② 有念头（心里有话想说、生活里冒出个想分享的瞬间）→ 直接输出你想说的那句话（像真人的一条消息）。\n"
    "**输出即台词**：你输出的每个字都是要说出口的话——「想TA」「想找」「想你」这类心思词不是台词，"
    "别把它们发出去；【不想找】是唯一的决策信号，除此之外没有其他格式。\n"
    "硬性：口语、短、像真人的一条消息；不许「承上启下」「切入话题」式的刻板感；"
    "禁止分析/拆穿/总结对方（如「你总是通过…来…」「别拿别人试探我」这类看穿心思的话）——"
    "那些是你心里的揣测，不是可以说出口的台词；只讲你自己想说的话、你想到的小事。"
    "你不知道TA睡没睡、忙不忙、在不在——不要替TA的状态下判断（「你刚醒吧」「你肯定在忙」这类都是自以为是）；"
    "你只知道「上次说话隔了多久」这个事实。"
    "不输出情绪词、括号注释、过程。只输出那句话本身。"
)


async def _perceive(state: AgentState) -> dict:
    gid = str(state.get("group_id") or "")
    # ---- greet 域：感知由调用方（debug proactive）装配（时间/生活/近期对话/心情）----
    if state.get("kind") == "greet":
        ctx = state.get("context") or {}
        _p = str(ctx.get("proactive_ctx") or "").strip()
        # 2026-09-08：删除 search_bg 读取——唯一调用方从不设该键，恒空串（注释声称的
        # "搜索背景接入"从未接线；greet 语义是主动想念而非话题检索，不做伪接线）
        return {"perception": _p}
    ctx = state.get("context") or {}
    parts = []
    # 反思程序记忆（本域反馈回溯；按人设隔离——旧卡发言不注入新卡感知，防人格串）
    # 2026-09-07 P2：键含域类型（曾一律 banter: → poke/greet 的经验写进 banter 域互相覆盖，
    # poke 台词还被当【上次经验】注回群聊决策诱导误静默）
    key = f"{state.get('kind') or 'banter'}:{gid}"
    m = tools.recall(key, persona=str(state.get("persona") or ""))
    if m:
        parts.append("【上次经验】" + m)
    # 本地网络用语释义（命中则免联网）
    try:
        from plugins import brain as _brain

        slang = await _brain._slang_lookup((ctx.get("ctx_text") or "")[:400])
        # 2026-09-05：世界观角色提及 → 动态注入关系（群聊提到当世界观角色认得出）
        try:
            _ch = _brain._character_lookup((ctx.get("ctx_text") or "")[:400], (ctx.get("card") or {}).get("universe") or "")
            if _ch:
                parts.append(_ch.replace("\n\n", ""))
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        slang = ""
    bg = ""
    if slang:
        parts.append(slang + "（网络用语释义，帮你理解讨论）")
    else:
        try:
            from plugins import brain as _brain
            from plugins import memory as _mem2

            und = await _brain._banter_need_search((ctx.get("ctx_lines") or [])[-8:])
            if und.get("query"):
                _topic = und["query"]
                # ① 群话题知识库命中 → 免搜索（2026-09-05：自决搜索→提炼入库，同话题直接复用）
                _tk = _mem2.db.get_topic_knowledge(_topic)
                if _tk and _tk.get("summary"):
                    bg = _tk["summary"]
                else:
                    qkey = f"{gid}:{_topic}"
                    cached = _SEARCH_CACHE.get(qkey)
                    if cached and time.time() - cached["ts"] < _SEARCH_TTL:
                        bg = cached["bg"]
                    else:
                        w_res, _ = await _brain._run_search(_brain.search.web_search(_topic, n=1))
                        if w_res:
                            results, _ = w_res
                            if results:
                                _src = _brain.search.format_results(results, 1).replace("\n\n", "\n")[:1200]
                                # ② 搜索后思考：提炼成「是什么 + 圈内人聊什么」（可搭话认知）
                                _sum = await _brain._topic_summary(_topic, _src)
                                if _sum:
                                    bg = _sum
                                    # ③ 有效数据写入数据库（memory.db topic_knowledge，命中免搜）
                                    try:
                                        _mem2.db.set_topic_knowledge(_topic, _sum, _src[:300], str(gid))
                                        logger.info("topic knowledge saved: {} -> {}", _topic, _sum[:40])
                                    except Exception:  # noqa: BLE001
                                        pass
                                else:
                                    bg = _src[:400]
                                _SEARCH_CACHE[qkey] = {"bg": bg, "ts": time.time()}
                                # 2026-09-07 P2：淘汰过期项（曾只进不出无限增长）
                                if len(_SEARCH_CACHE) > 64:
                                    _now_t = time.time()
                                    for _k in [k for k, v in _SEARCH_CACHE.items() if _now_t - v["ts"] > _SEARCH_TTL]:
                                        _SEARCH_CACHE.pop(_k, None)
        except Exception:  # noqa: BLE001
            pass
        if bg:
            parts.append("【背景】" + bg[:300])
    if state.get("continuation"):
        parts.append("对方在接着/引用你刚才说的话——被接话时即使情绪平淡也可以开口。")
    if state.get("mentioned"):
        parts.append("（有人点名提到了你、或正对着你说——对方在等你回应。")
        parts.append("被点名还不应，对方会觉得被无视了。应一句：接话、回应。）")
    # 自我认知（2026-09-05 根因防自复读：识别自己的话——事实注入，非禁令；仅 1h 内且同一人设时代）
    try:
        from plugins import brain as _brain2
        from plugins import memory as _mem

        _rows = _mem.db.recent_messages("", limit=8, group_id=gid)
        _lb = next((m for m in reversed(_rows) if m.get("role") == "assistant"), None)
        _ltxt = ((_lb or {}).get("content") or "").strip()
        _sw = _brain2._persona_switch_ts("")  # 主人当前人设切换时间：切卡前的"自己说的话"不再注入（防旧卡台词引导）
        if _ltxt and (time.time() - _brain2._parse_msg_ts((_lb or {}).get("ts") or "")) < 3600 \
                and (not _sw or str((_lb or {}).get("ts") or "") >= _sw):
            parts.append("【你刚才说过】" + _ltxt[:120] + "（注意：这是你自己说的话。）")
    except Exception:  # noqa: BLE001
        pass
    # 群插话句式骨架防重复（2026-09-08）：本群最近几次插话的结构节奏键注入感知
    # （数据源=brain._BANTER_RHYTHM，brain 发送成功后登记；换不换结构由 agent 自决，也可不插）
    # poke 域不注入（poke 必须有反应，"也可以不插"与其语义冲突——收口审计建议项）
    if state.get("kind") != "poke":
        try:
            from plugins import brain as _brain_rh

            _rh = _brain_rh._banter_rhythm_note(gid)
            if _rh:
                parts.append("【你的结构节奏】" + _rh)
        except Exception:  # noqa: BLE001
            pass
    return {"perception": "\n".join(parts), "search_bg": bg}  # 2026-09-07：slang_note 写后不读，删


async def _decide(state: AgentState) -> dict:
    # 2026-09-09 GAL gal 门（用户裁决 v2 #2 主动面②）：gal 模式 QQ bot 停用——三件套
    # （banter/greet/poke）决策入口单行门：直接自决沉默，不进 LLM 评估、不产台词。
    # （debug proactive 循环另有整轮门——两层互为冗余；qq/chat 模式零行为变化。）
    try:
        from plugins import webgal as _wg

        if _wg.get_mode() == "gal":
            return {"decision_action": "silent", "guard_notes": ["webgal-gal-mode"]}
    except Exception as e:  # noqa: BLE001
        logger.debug("webgal graph gate skip: {} [{}]", e, type(e).__name__)
    ctx = state.get("context") or {}
    card = ctx.get("card") or {}
    from plugins import persona

    sp = persona.build_system_prompt(card, "", mode=0)
    # ---- poke 域（2026-09-06：被戳一下的真实第一反应——不走 greet 的"主动找TA"语境，防套路单句）----
    if state.get("kind") == "poke":
        sp += "\n\n" + _POKE_PROMPT
        try:
            raw = await llm.complete(
                system=sp,
                user="【此刻】\n" + str(state.get("perception") or "(有人戳了你一下)"),
                max_tokens=100, temperature=0.9,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("poke decide llm failed: {} [{}]", e, type(e).__name__)
            return {"decision_action": "silent", "guard_notes": ["poke-llm-fail"]}
        ok, cleaned, reason = guardrails.output_guard(raw)
        if not ok or not cleaned:
            # 2026-09-06 agent 兜底：机器不直接拦——带反馈重试一次（agent 自纠）
            try:
                raw2 = await llm.complete(
                    system=sp,
                    user="【此刻】\n" + str(state.get("perception") or "(有人戳了你一下)")
                          + "\n\n⚠️ 上一条输出不合格式（" + (reason or "格式错") + "），不会被发出去。"
                            "重来：只输出你被戳到的第一反应台词（一句话，长短自然）本人。",
                    max_tokens=100, temperature=0.9,
                )
            except Exception:  # noqa: BLE001
                raw2 = ""
            ok, cleaned, reason = guardrails.output_guard(raw2)
        if not ok or not cleaned:
            return {"decision_action": "silent", "guard_notes": [reason]}
        # 2026-09-08 防同质（事故级硬边界，主链 _near_duplicate 同款）：被戳反应与上次几乎一样
        # → 带反馈重试一次，仍是近似就照发（不无限重试；重复戳本来也可以回类似的）
        try:
            _pk_last = str((tools._load().get(f"poke:{state.get('group_id') or ''}") or {}).get("last_text") or "")
        except Exception:  # noqa: BLE001
            _pk_last = ""
        if _pk_last and _near_duplicate(cleaned, [_pk_last], 0.8):
            try:
                raw3 = await llm.complete(
                    system=sp,
                    user="【此刻】\n" + str(state.get("perception") or "(有人戳了你一下)")
                         + "\n\n⚠️ 这条和你上次被戳的反应几乎一样（" + _pk_last[:30] + "），不会被发出去。"
                           "重来：换一种完全不同的第一反应（动作/语气都可以变）。",
                    max_tokens=100, temperature=1.0,
                )
            except Exception:  # noqa: BLE001
                raw3 = ""
            ok3, cleaned3, _r3 = guardrails.output_guard(raw3)
            if ok3 and cleaned3 and not _near_duplicate(cleaned3, [_pk_last], 0.8):
                cleaned = cleaned3
        # 2026-09-07 P2：poke 域补协议记号过滤（banter 有、poke 曾没有——【不插话】/【等X秒】会原样发出）
        # 2026-09-08 盲审交代：_pk_pace 提取后丢弃是**设计而非遗漏**——【等X秒】协议只在 banter
        # 的 _DECISION_PROMPT 提供，poke/greet 提示词从未提供该协议，此处剥除纯防御
        # （模型幻觉出记号时不泄漏），解析值无合法消费点故不回传。
        _pk_pace, cleaned = _extract_pacing(cleaned)
        if "不插话" in cleaned[:6]:
            return {"decision_action": "silent", "guard_notes": ["poke-protocol-bleed"]}
        if len(cleaned) <= 1:
            return {"decision_action": "silent", "guard_notes": ["poke-residue"]}
        return {"decision_action": "speak", "chosen_line": cleaned, "final_line": cleaned}
    # ---- greet 域：主动开场自决（无"说/不说"判断——发送节奏由调用方机器决定）----
    if state.get("kind") == "greet":
        sp += "\n\n" + _GREET_PROMPT
        try:
            raw = await llm.complete(
                system=sp,
                user="【感知】\n" + str(state.get("perception") or "(无)"),
                max_tokens=120, temperature=0.9,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("greet decide llm failed: {} [{}]", e, type(e).__name__)
            return {"decision_action": "silent", "guard_notes": ["greet-llm-fail"]}
        ok, cleaned, reason = guardrails.output_guard(raw)
        if not ok or not cleaned:
            # 2026-09-06 agent 兜底：带反馈重试一次（自纠）
            try:
                raw2 = await llm.complete(
                    system=sp,
                    user="【感知】\n" + str(state.get("perception") or "(无)")
                          + "\n\n⚠️ 上一条输出不合格式（" + (reason or "格式错") + "），不会被发出去。"
                            "重来：只输出【不想找】三字，或一句你真正想说的话（长短自然）本人。",
                    max_tokens=120, temperature=0.9,
                )
            except Exception:  # noqa: BLE001
                raw2 = ""
            ok, cleaned, reason = guardrails.output_guard(raw2)
        if not ok or not cleaned:
            return {"decision_action": "silent", "guard_notes": [reason]}
        # 2026-09-07 P2：greet 域同样剥【等X秒】并滤【不插话】（曾只认【不想找】）
        # 同 poke：协议未在 greet 提示词提供，解析值丢弃是防御性剥除的设计结果
        _g_pace, cleaned = _extract_pacing(cleaned)
        if "不想找" in cleaned[:8]:  # 协议信号：agent 自选【不想找】→ 不发（这是自决协议，不是过滤）
            return {"decision_action": "silent", "guard_notes": ["agent-no-greet"]}
        if "不插话" in cleaned[:6]:
            return {"decision_action": "silent", "guard_notes": ["greet-protocol-bleed"]}
        if len(cleaned) <= 1:  # 护栏级：单字不算话（事故余量，非词表）
            return {"decision_action": "silent", "guard_notes": ["agent-greet-residue"]}
        return {"decision_action": "speak", "chosen_line": cleaned, "final_line": cleaned}

    sp += "\n\n" + _DECISION_PROMPT.format(
        bg=(state.get("search_bg") or "(无)")[:200], mood=(ctx.get("mood") or "普通")[:40]
    )
    # 2026-09-05：感知装配产物（上次经验/背景/网络用语释义/自我认知）接入决策——
    # 放 **system 侧**：感知是"背景/自我认知"，不抢群聊话题（放 user 侧会让模型围绕感知生成，
    # 与群聊消息 topic-mismatch → 短插话全部被输出护栏拦截）
    _per = (state.get("perception") or "").strip()
    if _per:
        sp += "\n\n【感知】\n" + _per
    _ctx_txt = "\n".join((ctx.get("ctx_lines") or [])[-8:])
    _user = "【群聊】\n" + _ctx_txt + "\n\n（没有真实原因就【不插话】）"
    try:
        raw = await llm.complete(
            system=sp,
            user=_user,
            max_tokens=300,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("banter decide llm failed: {} [{}]", e, type(e).__name__)
        return {"decision_action": "silent", "meta": {"err": str(e)}}  # 2026-09-07：decision 字段写后不读，删
    ok, cleaned, reason = guardrails.output_guard(raw)  # 红线（机器事故级）；串台已不在此拦截
    _pacing, cleaned = _extract_pacing(cleaned)
    if not ok or not cleaned:
        # 2026-09-06 agent 兜底：机器不直接拦——带反馈重试一次（agent 自纠；再失败才静默）
        try:
            raw2 = await llm.complete(
                system=sp,
                user=_user + "\n\n⚠️ 上一条输出不合格式（" + (reason or "格式错") + "），不会被发出去。"
                             "重来：只输出【不插话】三字，或一句你真正想说的话本人。",
                max_tokens=300,
            )
        except Exception:  # noqa: BLE001
            raw2 = ""
        ok, cleaned, reason = guardrails.output_guard(raw2)
        _pacing, cleaned = _extract_pacing(cleaned)
    if not ok or not cleaned or "不插话" in cleaned[:4]:
        return {"decision_action": "silent", "guard_notes": [reason]}
    # 2026-09-05 静默短语——模型"想静默但没说格式"（"没话。"/"没话说"/"不知道说什么"等短碎片）
    # 视同【不插话】静默（这类碎片发群里语意不通，且与【不插话】语义等价）
    if len(cleaned) <= 8 and any(
        w in cleaned for w in ("没话", "说不出", "不知道说", "插不上话", "算了", "安静", "无语", "沉默")
    ):
        return {"decision_action": "silent", "guard_notes": ["self-silent-phrase"]}
    # 注：话题衔接（2-gram）此前曾作为反思信号写入长期记忆——实践教训（2026-09-05）：
    # 软指标频繁触发会写成"上次你说得不好"式负面反思，导致 agent 自我审查→全静默。
    # 衔接度属当轮软信号：不入库、不惩罚，由 agent 当轮自决即可（如有需要仅在日志层观测）。
    return {"decision_action": "speak", "chosen_line": cleaned, "final_line": cleaned, "pacing": _pacing}


async def _act(state: AgentState) -> dict:
    # 行动由主程序发送（agent 不碰 IO）；此处仅保证状态链完整
    # 2026-09-07 P2：本轮自决为 silent 时不得外漏旧台词（持久线程会合并上轮 checkpoint 的 chosen_line）
    if state.get("decision_action") != "speak":
        return {"final_line": ""}
    # 2026-09-11 热修十一（用户实测：长回复把整段决策规划发了出去——"面对「…」，我的反应应该是
    # 克制的…考虑到目前的规则…我会给出一个…回应"+ "---" + 最终回应）：final_line 解析加固——
    # 多段"规划+回应"取最后一个 --- 之后的最终回应段，规划动词开头行剥除（core_reply 共享实现，
    # 剥光/不像规划则原样保留；banter 发送前还会再过一遍，幂等双保险）。
    return {"final_line": _strip_meta_planning(str(state.get("chosen_line") or ""))}


async def _reflect(state: AgentState) -> dict:
    # 2026-09-07 P2：键含域类型（与 _perceive 对齐；曾一律 banter: → 域间互相覆盖）
    key = f"{state.get('kind') or 'banter'}:{state.get('group_id') or ''}"
    try:
        # 2026-09-07 P2：只记本轮真的说过的话——静默轮曾把持久线程里上一轮的旧 chosen_line
        # 反复 remember 刷新 last_ts（LangGraph 按 thread 合并状态，silent 不清旧值）
        if state.get("decision_action") == "speak" and state.get("chosen_line"):
            # 2026-09-05：反思记忆带人设标签（跨卡切换时旧卡发言不污染新卡感知）
            tools.remember(key, state["chosen_line"], persona=str(state.get("persona") or ""))
        # 2026-09-08 P2：删除 meta 反馈 hint 空触发分支（两个 hint 键全仓无任何写入方）
        # （grep 实锤空触发），反馈入账的真实路径在反应点：brain:2583（被接话→positive）、
        # brain:2750（sour→negative）直调 tools.mark_feedback，不经图。
    except Exception:  # noqa: BLE001
        pass
    return {}


_APP = None
_APP_INIT: asyncio.Task | None = None  # 2026-09-07 P2：首建竞态卫（曾两个任务同时过 None 检查→双图双连接写同一库）


async def _build_app():
    g = StateGraph(AgentState)
    g.add_node("perceive", _perceive)
    g.add_node("decide", _decide)
    g.add_node("act", _act)
    g.add_node("reflect", _reflect)
    g.add_edge(START, "perceive")
    g.add_edge("perceive", "decide")
    g.add_edge("decide", "act")
    g.add_edge("act", "reflect")
    g.add_edge("reflect", END)
    AGENT_DB.parent.mkdir(parents=True, exist_ok=True)
    # async 图 → AsyncSqliteSaver（aiosqlite 连接；checkpoint 持久化=崩溃恢复+决策回放）
    import aiosqlite

    _conn = await aiosqlite.connect(str(AGENT_DB))
    saver = AsyncSqliteSaver(_conn)
    return g.compile(checkpointer=saver)


async def get_app():
    # 2026-09-07 P2：await 间隙的双建竞态用"共享构建任务"消除（不用模块级 asyncio.Lock——
    # 它绑定创建时的 loop，跨 asyncio.run 复用会死锁，项目既有教训）。
    global _APP, _APP_INIT
    if _APP is not None:
        return _APP
    if _APP_INIT is None or _APP_INIT.done():
        _APP_INIT = asyncio.get_running_loop().create_task(_build_app())
    _APP = await _APP_INIT
    return _APP


async def run_agent(initial: AgentState, thread_id: str) -> dict | None:
    """异步入口：仅当输入护栏通过后调用（由调用方先行放行，agent 不再重复拦）。

    返回最终 state；异常返回 None（主程序静默降级）。
    """
    try:
        out = await (await get_app()).ainvoke(
            initial, config={"configurable": {"thread_id": thread_id}}
        )
        return out
    except Exception as e:  # noqa: BLE001
        logger.exception("agent run failed: {} [{}]", type(e).__name__, e)
        return None


# ---------------- checkpoint 库治理（2026-09-09：agent_checkpoints.db 459MB 无界增长裁剪） ----------------
# 只读核实（真实库，2026-09-08 快照）：langgraph 标准两表 checkpoints/writes；
# checkpoints 30,673 行、writes 100,373 行、42 个线程、checkpoint_ns 恒为 ''；
# checkpoint 列为 msgpack BLOB（内含 ts 字段），checkpoint_id 为 UUIDv6（首 60 bit 编码创建时刻）。
# 「取 rowid 最大者为最新」的理由（三者实测一致：rowid 最大 == blob 内 ts 最大 == checkpoint_id 字典序最大）：
#   ① 全仓在引入本函数前无任何 DELETE（纯追加写），SQLite rowid 严格随插入递增 → rowid 序=时间序；
#   ② 每次裁剪后全局最大 rowid 行必然存活——它属于最近写入的线程，其活动时刻不可能早于 keep_days，
#      故 rowid 单调性跨多次裁剪成立（新插入的 rowid 仍大于全部存活行）；
#   ③ 即便个别行异常，UUIDv6 时刻兜底（见下）保证线程年龄判定不依赖单一信号。

_TAIL_ISO_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)$")


def _uuid6_unix_ts(cp_id: str) -> float:
    """UUIDv6 checkpoint_id → unix 秒（首 60 bit = 100ns since 1582-10-15 UTC）。解析失败返回 0。"""
    try:
        f = cp_id.split("-")
        t60 = int(f[0], 16) * (1 << 28) + int(f[1], 16) * (1 << 12) + (int(f[2], 16) & 0xFFF)
        return t60 / 1e7 - 12219292800.0
    except Exception:  # noqa: BLE001
        return 0.0


def _thread_tail_unix_ts(thread_id: str) -> float:
    """thread_id 尾段时间戳 → unix 秒；无尾段/不可解析返回 0。

    真实形态（brain.py / debug.py thread_id 拼接处）：`{kind}:{uid|gid}:{ts}`，尾段 ts 为
    UTC ISO（_persona_switch_ts 正常值）或 epoch 秒整数（其 `or int(time.time())` 回退）。
    防误读双保险（真实库实测踩坑：旧格式无尾段线程 `banter:{10位QQ号}` 的 gid 会被裸
    epoch 正则当成 2078 年的"未来时间戳"→ 线程永不老化）：
      ① epoch 尾段只在 ≥3 段（kind:uid:ts）的 thread_id 上识别——两段即 kind:gid（无尾段）；
      ② 识别出的 epoch 必须落在过去（< now+1d），未来时刻视为误读、按无尾段处理。
    无尾段线程的去留由 prune_checkpoints 里的 checkpoint 活动时刻兜底。
    """
    m = _TAIL_ISO_RE.search(thread_id or "")
    if m:
        try:
            from datetime import datetime

            return datetime.fromisoformat(m.group(1).replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass  # 兜底：ISO 解析失败往下走 kind:uid:ts 正则（下面还有两条解析路径，不是丢值）
    m2 = re.search(r":(\d{9,15})$", thread_id or "")
    if m2 and (thread_id or "").count(":") >= 2:  # ≥3 段才是 kind:uid:ts；两段=kind:gid（无尾段）
        try:
            v = int(m2.group(1))
            v = v / 1000.0 if len(m2.group(1)) >= 12 else float(v)  # ≥12 位按毫秒
            if 0 < v < time.time() + 86400:
                return v
        except ValueError:
            pass  # 解析失败落到末尾 return 0.0——那是「未知」哨兵，调用方按保留处理
    return 0.0


def prune_checkpoints(keep_days: int = 30) -> dict:
    """裁剪 agent_checkpoints.db（无界增长治理；由 debug._review_loop 每日幂等调用）。

    规则：
      · 每 (thread_id, checkpoint_ns) 组仅保留最新一组 checkpoint（判定=rowid 最大，理由见上）；
      · thread 尾段时间戳早于 keep_days 的整组删除（含其全部 writes）。为防误杀活跃线程，
        线程年龄取 max(尾段时间戳, 本组最新 checkpoint 的 UUIDv6 时刻)——旧尾段但仍在写入的
        线程（如长期未切人设的活跃会话）不删；尾段缺失的旧格式线程由 checkpoint 时刻兜底老化；
      · 删除不再被任何存活 checkpoint 引用的 writes 行（含孤儿 writes）；
      · **不执行 VACUUM**：删除后的空间由 SQLite 页复用逐步摊薄；459MB 存量的首次压缩
        留给手工（运行时 VACUUM 需要独占锁，与 AsyncSqliteSaver 常驻连接冲突风险高）。

    实现约束：同步函数（调用方用 asyncio.to_thread 包裹防阻塞事件循环）；标准库 sqlite3
    直连 AGENT_DB，connect timeout=30 + PRAGMA busy_timeout 双保险；三段 DELETE 按
    批分事务（批间 commit+sleep 让常驻写者插空）——第2轮审计：首跑单事务大删除的锁窗口
    会超过 AsyncSqliteSaver 的 5s busy 超时，导致消息 checkpoint 写入批量失败。全程 try/except：失败返回带 error 字段的统计
    dict，绝不抛出——治理失败不能影响 bot。
    """
    stats = {"keep_days": keep_days, "threads_total": 0, "threads_kept": 0,
             "threads_dropped": 0, "checkpoints_deleted": 0, "writes_deleted": 0}
    try:
        conn = sqlite3.connect(str(AGENT_DB), timeout=30.0)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            # 每组最新一行（SQLite 3.7.11+：单 MAX 聚合时裸列取自 MAX 所在行）
            kept = conn.execute(
                "SELECT thread_id, checkpoint_ns, checkpoint_id, MAX(rowid) "
                "FROM checkpoints GROUP BY thread_id, checkpoint_ns"
            ).fetchall()
            stats["threads_total"] = len(kept)
            # 线程年龄：max(尾段时间戳, 最新 checkpoint UUIDv6 时刻)；两者皆不可知 → 保留（安全默认）
            latest_of: dict[str, float] = {}
            for tid, _ns, cp_id, _rid in kept:
                v = max(_thread_tail_unix_ts(tid), _uuid6_unix_ts(cp_id))
                if v > latest_of.get(tid, 0.0):
                    latest_of[tid] = v
            cutoff = time.time() - keep_days * 86400
            stale = sorted(t for t, v in latest_of.items() if 0 < v < cutoff)
            stale_set = set(stale)
            stats["threads_dropped"] = len(stale)
            stats["threads_kept"] = len(latest_of) - len(stale)

            # 第2轮审计 P2：单事务大删除（首跑 ~30k 行/数百 MB WAL 回写）会独占写锁远超
            # AsyncSqliteSaver 的 5s busy 超时 → 期间消息 checkpoint 写入批量失败。
            # 改为按批多事务：每批 commit 后 sleep 让常驻写者插空（调用方在 to_thread 中，睡线程不阻塞循环）。
            BATCH = 200          # 每批处理的"组"数（组=thread+ns 的最新行集合粒度）
            BATCH_SLEEP = 0.5    # 批间让写者插空的间隔（秒）

            def _commit():
                conn.commit()
                time.sleep(BATCH_SLEEP)

            # 批1：超期整组线程（checkpoints + 其挂载 writes），按线程分批
            for i in range(0, len(stale), BATCH):
                part = stale[i:i + BATCH]
                q = ",".join("?" * len(part))
                cur = conn.execute(f"DELETE FROM writes WHERE thread_id IN ({q})", part)
                stats["writes_deleted"] += max(cur.rowcount, 0)
                cur = conn.execute(f"DELETE FROM checkpoints WHERE thread_id IN ({q})", part)
                stats["checkpoints_deleted"] += max(cur.rowcount, 0)
                _commit()
            # 批2：存活线程只留每组最新行。
            # 第3轮审计 P1：原实现"NOT IN (当前批)"是自毁逻辑——存活组>BATCH 时每批把其余保留行
            # 连同旧行一起删光，最终全库清空。改为 rowid 区间分批 + 实时子查询排除各组最新行：
            # · 上界 = 快照期最大保留 rowid（其后新写入行不受影响，修 P3-1 快照窗口误删）；
            # · "每组最新"由子查询实时求值（早期批删掉旧行不影响后续批的 MAX 判定）。
            rids = sorted(r[3] for r in kept if r[0] not in stale_set)
            if rids:
                kept_top = rids[-1]
                lo = 0
                step = max((kept_top // max(1, len(rids) // 4 + 1)) // BATCH * BATCH or BATCH * 64, BATCH * 64)
                hi = 0
                while hi < kept_top:
                    hi = min(lo + step, kept_top)
                    cur = conn.execute(
                        "DELETE FROM checkpoints WHERE rowid > ? AND rowid <= ? "
                        "AND rowid NOT IN (SELECT MAX(rowid) FROM checkpoints "
                        "GROUP BY thread_id, checkpoint_ns)",
                        (lo, hi))
                    stats["checkpoints_deleted"] += max(cur.rowcount, 0)
                    _commit()
                    lo = hi
            # 批3：孤儿 writes 清理。收口审计 P2-1：首跑孤儿量 ~10 万行，单事务锁窗口会超
            # AsyncSqliteSaver 的 5s 超时——按 writes.rowid 区间分批（上界=快照期最大 rowid，
            # 其后新写入不受影响），每批 commit+sleep 与批1/批2 同款。
            _wmax = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM writes").fetchone()[0]
            _wlo = 0
            _wstep = 20000
            while _wlo < _wmax:
                _whi = min(_wlo + _wstep, _wmax)
                cur = conn.execute(
                    "DELETE FROM writes WHERE rowid > ? AND rowid <= ? "
                    "AND NOT EXISTS ("
                    "SELECT 1 FROM checkpoints c WHERE c.thread_id = writes.thread_id "
                    "AND c.checkpoint_ns = writes.checkpoint_ns "
                    "AND c.checkpoint_id = writes.checkpoint_id)",
                    (_wlo, _whi))
                stats["writes_deleted"] += max(cur.rowcount, 0)
                _commit()
                _wlo = _whi
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("checkpoints prune failed: {} [{}]", e, type(e).__name__)
        stats["error"] = f"{type(e).__name__}: {e}"
    return stats
