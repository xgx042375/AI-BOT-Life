"""reply —— 两阶段回复生成（2026-09-06，Phase D）。

流程：stage1 内容思考（可开思维链）→ stage2 风格润色（对草稿重组织语言）。

D2 裁决（用户 2026-09-06）：防复读检测机（相似度/骨架/短语泛滥/收尾复读/套路短语/
破碎腔/拟声词结构检查/重试累积提示）整体退役——防复读主力是 agent 自决
（感知「你上次说过」+ 风格层「句式自然性/结尾收束」）。
仅保留**事故级硬边界**（fail-closed + 带反馈重试一次，认可例外）：
    ① 与最近回复近似重复（2-gram Jaccard ≥ 0.8，事故级复读）
    ② 口球状态下说完整句子（物理事实）
长度：max_tokens 兜底；超限回退 stage1 草稿截断（不再重试）。

标记协议（机器只剥除/登记，不判内容）：
    [WRITE:...]  写作意图 → 透传调用方
    【等 X 秒】   开口节奏 → 透传调用方
    【心情：…】   （已退役 2026-09-08 深夜：心情=心跳tick+提取sync_mood 两渠道）
    【基调：…】   本条回复的情绪基调（2026-09-08 活人感#1：语音语气/配图情绪与此同源，
                 消费方 voice/sticker；agent 自标，不标则消费方自行兜底）→ 透传调用方
    【立绘：…】  本条回复的立绘差分词（2026-09-12 热修十三 B2：与基调解耦，立绘=真实差分
                 文件词，消费方 webgal sprite 帧；brain 侧按扫描词表软校验）→ 透传调用方
"""
from __future__ import annotations

import asyncio
import re

from loguru import logger

from core import llm as core_llm  # 2026-09-10 Phase1：LLM Provider 适配层（thinking 注入策略收拢）

# 2026-09-09 深夜实测泄露修复：LLM 偶发括号漂移（全角【】→半角[]），旧正则只认全角 →
# [基调：温情满足]/[基调：软下来]/[基调：娇羞] 三次原样发出+入库+进语音（memory.db 16348/18361/18370 实锤）。
# 括号类协议标记统一兼容全/半角及混写（[\[】 等任意组合——剥多不剥少，安全侧）。
_PACE_RE = re.compile(r"[【\[]等\s*([0-9]+(?:\.[0-9]+)?)\s*秒[】\]]")
_TONE_RE = re.compile(r"[【\[]基调[：:]\s*([^】\]]{1,6})\s*[】\]]")
# 热修十三 B2（2026-09-12）：立绘差分词自标（【立绘：词】）——形制完全镜像 _TONE_RE（全/半角
# 方括号兼容，剥多不剥少安全侧）；与【基调】解耦：基调=语音 9 类，立绘=真实差分文件词
# （词表=webgal.sprite_words 实扫），登记/软校验在 brain 侧，本层只提取+剥除+透传。
_SPRITE_MARK_RE = re.compile(r"[【\[]立绘[：:]\s*([^】\]]{1,12})\s*[】\]]")
_WRITE_RE = re.compile(r"\[WRITE:([a-z]+)(?:\|([^\]]*))?\]")
_ACTION_SEG_RE = re.compile(r"\*[^*\n]{1,60}\*|（[^（）\n]{1,20}）")


def _grams2(s: str) -> set[str]:
    s = re.sub(r"\s", "", s or "")
    return {s[i:i + 2] for i in range(max(0, len(s) - 1))}


# ---- 2026-09-11 热修十一：思考/元规划泄漏防护（通用防御函数；调用方按需过一道） ----
# 实测样本（群聊插话链）：回复=【开场礼貌句】+【整段回应规划】+ "---" +【第二轮规划】+【最终回应】
# ——模型把决策规划语言整段写进了输出、解析未剥。这里给两层剥离：
#   ① _strip_inline_think：内联 <think>…</think> 块（含未闭合残段）与「嗯，用户…」式思考开头；
#   ② _strip_meta_planning：多段"规划+回应"取最后一段 + 规划动词开头行剥除（温和启发式）。

_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?(?:</think>|$)", re.S | re.I)
# 「嗯，用户」式思考开头：开头语气词 + 紧跟「用户」称谓（对幕后用户的称呼=思考残留的强信号；
# 对话对象在本项目里是主人/昵称，正常台词不会以「嗯，用户…」起头）。剥到首句读为止。
_META_OPEN_RE = re.compile(
    r"^\s*(?:嗯+|哦+|好的?|那么|收到|明白了?|了解|哈喽)?[，,、：:\s]*用户[^。！？!?\n]{0,60}[。！？!?\n，,：:]\s*"
)


def _strip_inline_think(text: str) -> str:
    """剥离内联思考残留（宽容设计——不把「剥到只剩思考」的输出再原样放行）：
    ① <think>…</think> 块（未闭合的 <think> 无收口同样吃到底；输出整段都是思考 → 剥成空稿，
       由上游 micro_speech 兜底接管，绝不把思考原文外发）；
    ② 「嗯，用户…」式思考开头（剥到首句读；「嗯，好的。」这类正常台词无「用户」称谓不命中，
       剥后为空则保留剥完 ① 的版本——对误吃保持宽容，对思考块 fail-closed）。"""
    orig = str(text or "").strip()
    t = _THINK_BLOCK_RE.sub("", orig).strip()
    t2 = _META_OPEN_RE.sub("", t, count=1).strip()
    return t2 if t2 else t


# 元规划段判据（双条件，宁温和勿误伤正常台词）：行首规划性开头（"我会给出…/我的反应应该是…/
# 考虑到目前的规则…"）+ 行长 ≥ 10 + 行内带元词（回应/规则/人设/性格…——谈「回复本身」才是规划，
# "考虑到你明天要早起"这类正常关切不含元词不命中）。全部行被剥光 → 原样返回（不劫持）。
_META_PLAN_OPENER_RE = re.compile(
    r"^\s*(?:好的?[，,、：:]?|嗯+[，,、：:]?|那么[，,、：:]?)?\s*"
    r"(?:面对[「『\"'][^」』\"']{1,30}[」』\"'][，,、：:]?\s*)?"
    r"(?:我的(?:反应|回应|回复|第一反应|处理方式|思路|判断|选择)"
    r"|我会?(?:给出|回应|输出|写(?:出|一个)?|以|用|保持|选择|表现)"
    r"|我将?(?:给出|回应|保持|表现)|我需要(?:给出|回应|保持)|我应该(?:给出|回应|保持|表现|以)"
    r"|考虑到|按照?(?:目前|当下|这些)?的?(?:规则|要求|设定|人设|性格|身份)"
    r"|根据(?:目前|当下)?的?(?:规则|要求|设定|人设|性格|身份)"
    r"|基于.{0,6}(?:规则|要求|设定)|为了(?:符合|保持|遵守|满足)|如果是为了)"
)
_META_PLAN_HINT_RE = re.compile(
    r"(?:回应|回复|答复|反应|规则|人设|性格|设定|角色|台词|克制|得体|干练|分寸)"
)
# 多段"规划+回应"分隔线（--- / ——— / === 等；整行只有分隔符才算）
_META_PLAN_SEP_RE = re.compile(r"^[ \t]*(?:-{3,}|—{3,}|={3,}|─{2,}|＿{2,})[ \t]*$", re.M)


def _strip_meta_planning(text: str) -> str:
    """元规划/决策语言剥离（2026-09-11 热修十一；graph._act 与 brain 群插话发送前双保险共用）：
    ① 文本含分隔线（---）的多段"规划+回应"结构 → 取最后一个分隔线之后的最终回应段；
    ② 规划动词开头行（双条件判据见上）剥除；
    ③ 剥后不足 2 字 → 原样返回（宁温和勿误伤：输入本身像台词时绝不吞内容）。幂等可重复过。"""
    t = str(text or "").strip()
    if not t:
        return t
    _parts = _META_PLAN_SEP_RE.split(t)
    if len(_parts) > 1:  # 多段结构：前面全是规划/草稿轮次，只要最后一段
        t = _parts[-1].strip()
        if not t:
            return str(text or "").strip()
    _lines = [ln for ln in t.split("\n")
              if not (_META_PLAN_OPENER_RE.match(ln.strip())
                      and len(ln.strip()) >= 10
                      and _META_PLAN_HINT_RE.search(ln))]
    _out = "\n".join(_lines).strip()
    return _out if len(_out) >= 2 else t


def _near_duplicate(text: str, olds: list[str], threshold: float = 0.8) -> bool:
    """事故级近似重复判定（高阈值；与旧 0.60 质量闸门不同——只拦"几乎原样复读"）。
    2026-09-07 修复：分母用并集（真 Jaccard）——曾用 min() 实为包含度，旧回复是短句
    （"嗯。"恰是 micro_speech 兜底值）时下一条几乎必误判 → 白跑双阶段重试。"""
    g = _grams2(text)
    if len(g) < 4:
        return False
    for o in olds or []:
        og = _grams2(o)
        if not og:
            continue
        _union = g | og
        if not _union:
            continue
        if len(g & og) / len(_union) >= threshold:
            return True
    return False


def _long_sentence(text: str, max_len: int = 45) -> bool:
    """超长句检测：剥（动作）后任一完整句 >45 字 → True（观感事故级形状红线）。
    2026-09-09 修正：曾 32 字太紧——正常 RP 卡风句子 30-49 字频繁触发重试→解释性重写反而更长
    （"前所未有长度"的主因之一）；45 只拦真正怪异长句。"""
    t = re.sub(r"（[^（）]{1,24}）", "。", str(text or ""))
    for s in re.split(r"[。！？!?]+", t):
        if len(s) > max_len:
            return True
    return False


def _strip_ellipsis_lines(text: str) -> str:
    """剥除孤立省略号行（2026-09-09 用户实测残留形态；与历史注入去噪同规则）。
    纯标点/空白噪声行剔除；剥后为空则原文返回（整条"……"式沉默回复不劫持）。"""
    t = str(text or "")
    lines = [l for l in t.split("\n")
             if not (l.strip() and not l.strip("…。.,，、 　"))]
    joined = "\n".join(lines).strip()
    return joined if joined else t.strip()


def _split_long_sentence(s: str, max_len: int = 45) -> str:
    """超长单句机械拆分（红线耗尽的形状兜底，2026-09-08 深夜盲审②）：优先句内标点断开，无标点定长硬断。"""
    s = str(s or "").strip()
    if len(s) <= max_len:
        return s
    parts = re.split(r"(?<=[，、；：,;:])", s)
    chunks: list[str] = []
    cur = ""
    for p in parts:
        if cur and len(cur + p) > max_len:
            chunks.append(cur)
            cur = p
        else:
            cur += p
    if cur:
        chunks.append(cur)
    out: list[str] = []
    for c in chunks:  # 无标点可断的残余：定长硬断（形状事故级，宁断不泄）
        while len(c) > max_len:
            out.append(c[:max_len])
            c = c[max_len:]
        if c:
            out.append(c)
    return "\n".join(out)


def _cut_to_sentences(text: str, total: int = 250) -> str:
    """兜底拆短（2026-09-08 D4·修正：曾按 32 字/句硬切→生硬断句，用户实测"把话截断了"）：
    **只按句读保留**（完整句子，永不在句中硬切）——宁可少留几句，不留半个句子；
    超出 total 时保留到最后完整句处（省略号收尾）。内容保真优先于文学完整。"""
    t = str(text or "")
    out = []
    used = 0
    for s in re.split(r"(?<=[。！？!?])", t):
        s = s.strip()
        if not s:
            continue
        if used + len(s) > total:
            if out:
                out[-1] = out[-1] + "……"
            else:
                # A1（2026-09-09 审计）：首个句读块即超 total 时曾返回空串 → 上层 reply=_fb[:limit]
                # 空串出门+空入库。回退硬截本块（宁断不泄——形状让步，消息不能为空）。
                out.append(s[:total])
            break
        out.append(s)
        used += len(s)
    return "".join(out)


def _polish_drifted(polished: str, draft: str) -> float:
    """润色保真度：两代 2-gram Jaccard 分值（<0.15 判漂移；2026-09-08 批次A 引入布尔，深夜改返回分值——
    实测双连漂移各废一次 stage2（~13s×2），需要分值进日志定位阈值是否对 RP 类文本过严）。
    短内容（<8 字）不判（返回 1.0）——润色对短句必然大改是正常的；
    语义级比对需 embedding 依赖，架构层另议。"""
    a = str(draft or "").strip()
    b = str(polished or "").strip()
    if len(re.sub(r"\s", "", a)) < 8 or len(re.sub(r"\s", "", b)) < 8:
        return 1.0
    ga, gb = _grams2(a), _grams2(b)
    if not ga or not gb:
        return 1.0
    return len(ga & gb) / len(ga | gb)


def _has_full_sentence(text: str) -> bool:
    """剥掉动作段后是否还有 ≥8 字的完整句子（口堵物理约束检查用）。"""
    body = _ACTION_SEG_RE.sub("", text or "")
    for seg in re.split(r"[。！？!?…~；;\n，,]", body):
        if len(seg.strip()) >= 8:
            return True
    return False


# ---- 主人括号事实·stage2 约束注入（2026-09-10 预解析管线） ----
# brain 在主人私聊轮（含全角括号）把「小 LLM 拆解括号事实」任务与 stage1 同时 create_task；
# 本层只在 stage2 组装时收取（stage1 不等它——stage1 本身 7-15s，小模型预解析大概率已完成；
# wait_for 8s 兜底：超时/失败=本轮无约束；stage1 快于预解析时最多补等至 8s（有界））。收取结果经 facts_box 带回 brain，
# 登记由 brain 在 generate 正常返回后落账（core/special 侧；本层只注入提示与带回，不做状态写账）。
_OWNER_FACTS_TIMEOUT = 8.0     # 收取兜底超时（秒）——与 brain 侧预解析 LLM 调用 timeout 同口径
_OWNER_FACTS_NOTE_CAP = 80     # 单条事实行钳长（与 special._NOTE_CAP 同口径，防感知/提示污染面）
_OWNER_FACTS_STATE_CAP = 40    # 道具状态短述钳长
_OWNER_FACTS_ITEMS_MAX = 4     # 注入道具事实条数上限
_OWNER_FACTS_BLOCK = (
    "\n\n【本轮已发生事实（主人动作，机器已确认）】\n"
    "· {facts}\n"
    "（以上为既成事实：叙事须与其一致；登记类状态已由机器落账，登记类无需重复输出登记标记；"
    "但事实是解除/收起/结束时仍须按协议输出对应标记（【解除：…】/【清醒】），"
    "你自行新增了主人括号之外的状态变化时也按协议输出标记）"
)


def _owner_fact_lines(facts) -> list[str]:
    """预解析 facts dict → 事实行列表（一条一行，不含 · 前缀——由块模板统一加）。
    None/非 dict/字段全空 → []（本轮无约束）。"""
    if not isinstance(facts, dict):
        return []
    lines: list[str] = []
    _h = str(facts.get("hypno") or "").strip()
    if _h:
        lines.append("催眠：" + _h[:_OWNER_FACTS_NOTE_CAP])
    _b = str(facts.get("brainwash") or "").strip()
    if _b:
        lines.append("洗脑：" + _b[:_OWNER_FACTS_NOTE_CAP])
    _items = facts.get("items")
    if isinstance(_items, list):
        for it in _items[:_OWNER_FACTS_ITEMS_MAX]:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name") or "").strip()
            if not name:
                continue
            _st = str(it.get("state") or "").strip()
            lines.append("道具：" + name[:16] + (("：" + _st[:_OWNER_FACTS_STATE_CAP]) if _st else ""))
    # 热修十二 F1：解除也是本轮事实——渲染成事实行让 stage2 感知「本轮发生了解除」
    #（此前只注入登记语义，模型无从得知该按协议出解除标记）
    _rels = facts.get("releases")
    if isinstance(_rels, list):
        for r in _rels[:_OWNER_FACTS_ITEMS_MAX]:
            t = str(r or "").strip()
            if t:
                lines.append("解除：" + t[:16])
    return lines


async def generate(*, user_id: str, history_msgs: list[dict],
                   stage1_prompt: str, polish_prompt: str,
                   client, model: str,
                   user_text: str = "", group_id: str = "",
                   mouth_blocked: bool = False, allow_actions: bool = True,
                   strip_non_speech=None, micro_speech=None,
                   reaction_guard=None,
                   last_replies: list[str] | None = None,
                   think_wanted: bool = False, limit: int = 350,
                   facts_task=None, facts_box=None,
                   dup_threshold: float = 0.8) -> dict:
    """生成一条回复。返回 {text, pacing, write_intent, tone, sprite, attempts}。

    sprite（2026-09-12 热修十三 B2）：stage2 自标【立绘：词】的差分词（未标=None），
    透传调用方（brain 登记进 _SPRITE_LAST，webgal sprite 帧消费）；本层只提取+剥除。

    strip_non_speech(text, allow_action)->str 与 micro_speech(uid, text, history)->str
    由调用方注入（发送侧净化与微生成兜底仍是 brain/发送层职责）。
    facts_task（2026-09-10）：brain 侧并行预解析任务的 awaitable（默认 None=零行为变化）；
    stage2 组装时收取，非空则在润色提示末尾追加既成事实约束块；facts_box 为可选 dict，
    收取结果原样写入 box["facts"] 供 brain 在 generate 正常返回后登记（本层不登记）。
    """
    stage1 = stage1_prompt
    think_now = bool(think_wanted)
    last_replies = [x for x in (last_replies or []) if x]
    facts = None
    _facts_taken = False
    content = ""
    reply = ""
    reason = ""
    _form_violation = False  # 形状红线违规（动作括号/超长句）——内容合格只需换形式；B2：粘性（循环内只置 True 不重置）
    attempts = 0
    for attempt in (0, 1):
        attempts = attempt + 1
        if attempt == 1:
            stage1 = (
                stage1_prompt
                + "\n\n【上一条不合格：" + (reason or "格式错") + "。"
                  "换一种完全不同的说法和结构重新回答，内容相同但措辞全新。】"
            )
            # 深思考重试只给内容级失败（近似复读）；形状违规开深思考=多花 10-20s 还可能把对的内容想偏
            # （2026-09-08 实测：动作红线重试链 53.6s——思考是筛选器不是形式修正器）
            if not think_now and not _form_violation:
                think_now = True
                stage1 += "\n【这次先认真想清楚再开口。】"
        m1 = [{"role": "system", "content": stage1}] + [dict(m) for m in history_msgs]
        resp1 = await client.chat.completions.create(
            model=model, messages=m1,
            temperature=0.95 if attempt == 0 else 1.1,
            max_tokens=1400,
            extra_body=core_llm.thinking_extra(think_now),  # 2026-09-10 Phase1：注入策略收拢（默认 local+auto 输出逐字节不变）
        )
        content = (resp1.choices[0].message.content or "").strip()  # C4：空稿不再用"(无回复)"占位——留给 micro_speech 兜底接管
        # 2026-09-11 热修十一：内联思考剥离（stage1 草稿偶发夹带 <think> 块/「嗯，用户」式开头——
        # 不剥会进 stage2 润色并整段外发；通用防护，与群插话元规划泄漏同源）
        content = _strip_meta_planning(_strip_inline_think(content))
        thinking1 = (getattr(resp1.choices[0].message, "reasoning_content", None) or "").strip()
        logger.info("[思维链] user={} 思考={} attempts={}", user_id, "开" if think_now else "关", attempts)
        if thinking1:
            logger.info("[思考] {}", thinking1[:1200])
        logger.info("[草稿] {}", content[:400])

        # 写作意图标记（协议剥除，透传调用方执行）
        write_intent = None
        w = _WRITE_RE.search(content)
        if w:
            write_intent = (w.group(1), (w.group(2) or ""))
            content = _WRITE_RE.sub("", content).strip()  # C4：剥标后为空同样不占位（micro_speech 兜底接管）

        # 主人括号事实预解析收取（2026-09-10）：任务与 stage1 并行，这里才是等待点（stage1 不等它）；
        # 只收取一次（超时/失败=本轮无约束；重试复用结果，不再等第二次）
        if facts_task is not None and not _facts_taken:
            _facts_taken = True
            try:
                facts = await asyncio.wait_for(facts_task, timeout=_OWNER_FACTS_TIMEOUT)
                logger.info("owner facts parsed: {}",
                            [k for k, v in sorted((facts or {}).items()) if v]
                            if isinstance(facts, dict) else "empty")
            except Exception as e:  # noqa: BLE001
                logger.info("owner facts skipped (timeout/error): {} [{}]", e, type(e).__name__)
                facts = None
            if facts_box is not None:
                facts_box["facts"] = facts
        _fact_lines = _owner_fact_lines(facts)

        # stage2 润色：对草稿重组织语言（不照抄措辞），上下文=最近 3 条 + 内心思考
        ctx_parts = []
        for cm in history_msgs[-3:]:
            tag = "[对方刚才说]" if cm["role"] == "user" else "[你此前的回复]"
            ctx_parts.append(f"{tag} {cm['content']}")
        stage2_ctx = "\n".join(ctx_parts)
        if thinking1:
            stage2_ctx += "\n\n[你的内心思考] " + thinking1[:600]
        _polish_sys = polish_prompt
        if _fact_lines:  # 既成事实约束块（机器已登记对应状态——只需叙事一致，不重复出标记）
            _polish_sys = polish_prompt + _OWNER_FACTS_BLOCK.format(facts="\n· ".join(_fact_lines))
            logger.info("owner facts injected into stage2: n={}", len(_fact_lines))
        m2 = [
            {"role": "system", "content": _polish_sys},
            {"role": "user", "content": (
                "对话上下文（润色参考，勿新增内容）：\n" + stage2_ctx
                + "\n\n需要润色的回答：\n" + content
                + "\n\n润色说明：以上内心思考是你的心里话（内容意图），初步回答是据此草拟的。"
                  "以草稿为底自然润色：语气与用词可以调，但保留内容、意思与句子结构，"
                  "不做整段重写（机器会抽查润色是否偏离原稿，偏离即弃用）；"
                  "简单内容直接轻微润色即可。"
            )},
        ]
        resp2 = await client.chat.completions.create(
            model=model, messages=m2,
            temperature=1.0, max_tokens=600,
            extra_body=core_llm.thinking_extra(False),
        )
        reply = (resp2.choices[0].message.content or "").strip() or content
        reply = _strip_meta_planning(_strip_inline_think(reply))  # 2026-09-11 热修十一+盲审 P2：stage2 输出剥内联思考与元规划残留
        # 2026-09-08 批次A：润色漂移兜底——stage2 应"润色"而非"重写"；若把内容意图改掉（过于不像），
        # 回退 stage1 原稿（机器只保"核心内容不变"的约定，不判内容对错——与近复读红线同构的质检兜底）
        _ov = _polish_drifted(reply, content) if (content and reply) else 1.0
        if _ov < 0.15:
            logger.warning("polish drifted, fallback to draft: user={} overlap={:.3f} draft_len={} polish_len={}",
                           user_id, _ov, len(content), len(reply))
            reply = content

        # 标记剥除（节奏/心情）
        pacing = 0.0
        wm = _PACE_RE.search(reply)
        if wm:
            try:
                pacing = min(2.0, max(0.0, float(wm.group(1))))
            except Exception:  # noqa: BLE001
                pacing = 0.0
            reply = (reply.replace(wm.group(0), "") or "").strip()
        # 【心情】对话自标已退役（2026-09-08 深夜用户裁决：0 使用；心情=心跳 tick + 提取 sync_mood 两渠道）
        # 2026-09-08 活人感#1：情绪基调自标（三链同源：语音语气/配图情绪消费同一口径）
        tone = None
        tm = _TONE_RE.search(reply)
        if tm:
            tone = tm.group(1).strip()
            reply = _TONE_RE.sub("", reply).strip()
        # 热修十三 B2：立绘差分词自标（提取+剥除镜像 tone——全/半角兼容、多标记并存剥净；
        # 软校验（扫描词表比对）与登记在 brain 侧，本层零语义透传）
        sprite = None
        sm = _SPRITE_MARK_RE.search(reply)
        if sm:
            sprite = sm.group(1).strip()
            reply = _SPRITE_MARK_RE.sub("", reply).strip()

        # 发送侧净化（调用方注入）+ 极端空回复微生成兜底
        if strip_non_speech:
            reply = strip_non_speech(reply, allow_action=allow_actions)
        if len(reply) < 2 and micro_speech:
            fb = await micro_speech(user_id, user_text, history_msgs)
            reply = fb if len(fb) >= 2 else "嗯。"

        # 最小硬边界（事故级 only；至多带反馈重试一次）
        # B2：_form_violation 粘性——每轮不再重置（只置 True）：attempt0 形状违规+attempt1 复读违规时，
        # 回退段此前会因本轮重置误判"无形状违规"而跳过括号剥净/超长句拆分的形状兜底
        reason = ""
        if mouth_blocked and _has_full_sentence(reply):
            reason = "你的嘴正被堵着：只能发出唔唔/嗯嗯类闷哼和（动作），说不了完整句子"
        elif last_replies and _near_duplicate(reply, last_replies, dup_threshold):
            reason = "这条和你说过的上一条几乎一样——彻底换一种说法和结构"
        elif _long_sentence(reply):
            # 2026-09-08 D4：超长句红线（用户实测"长句超级长" 35-48 字/句）——
            # 观感事故级（形状红线，非内容判定——与近复读红线同构，重试一次）
            # reason 里的数字是修复指令（事故级红线的反馈重试），非生成脚本——B0 口径清理已删打字感措辞
            reason = "你这段话里有一句太长了（一口气说不完）——把最长的那句拆成两句，每句别超过 25 字。"
            _form_violation = True
        elif reaction_guard:  # C2：elif 链内 reason 必为空，`not reason` 是死条件（删除）
            reason = reaction_guard(reply) or ""
            _form_violation = bool(reason)
        if not reason:
            break

    # 耗尽：回退 stage1 草稿（截断到上限；不再机器代写兜底台词）
    if reason:
        logger.warning("reply hard-boundary retry exhausted: user={} reason={}", user_id, reason)
        # 2026-09-07 P2：回退稿过一遍协议标记剥离（【心情：x】/【等X秒】等曾原样漏进消息）；
        # 口球物理约束保持 fail-closed——回退稿仍含完整句时以闷哼收尾（事故级红线兜底，非代写台词）
        _fb = strip_non_speech((content or reply), allow_action=allow_actions) if strip_non_speech else (content or reply)
        if not str(_fb or "").strip():
            _fb = reply  # 剥后为空（纯动作/纯标记草稿）：用循环内最后一次已剥标的干净文本，防发空消息
        if mouth_blocked and _has_full_sentence(_fb):
            logger.warning("reply fallback mouth_blocked leak blocked: user={}", user_id)
            _fb = "唔……"
        # B2：括号剥净 + 超长句拆分改为无条件执行（两者幂等——重复执行结果不变）。
        # 曾包在 if _form_violation: 内，而 _form_violation 每轮被重置（现改粘性）——
        # attempt0 形状违规+attempt1 复读违规的组合曾整段跳过形状兜底，违规形状原样出门。
        # 动作红线耗尽：fail-closed 剥净全部括号（含半角变体）——台词保留，绝不把反应/神态/旁白括号发出去。
        # 2026-09-08 用户实测泄露实锤：耗尽回退曾原样发出违规草稿（"（被突如其来的触碰吓到…呜咽声）"）；
        # 与口球 fail-closed 同构（事故级红线形态），剥后为空时以闷哼收尾
        _stripped = re.sub(r"[（(][^（()）]*[)）]", "", _fb).strip()
        _fb = _stripped if len(_stripped) >= 2 else "唔……"
        # 超长句红线耗尽的形状兜底（盲审②：_cut_to_sentences 不拆 >45 字单句——白付重试延迟后违规形状仍出门）
        _fb = "\n".join(
            _split_long_sentence(s)
            for s in re.split(r"(?<=[。！？!?])", _fb) if s.strip()
        )
        logger.warning("reply fallback action-guard strip (unconditional): user={} left={}chars", user_id, len(_fb))
        # 2026-09-08 D4 修正：fallback 之前是 stage1 原稿（一样长）——红线重试 12B 改不动=白拦；
        # 机械保句读拆短（形状兜底：内容保真优先——句读处保留 + 超长单句截断）
        # C1：total 传 limit（曾恒 250——limit<250 时回退稿仍超限出门）
        _fb = _cut_to_sentences(_fb, total=limit)
        reply = _fb[:limit]
        if not reply.strip():  # A1 第二道守卫：任何路径下不允许空串出门/入库
            reply = (content or reply)[:limit]
    elif len(reply) > int(limit * 1.2):
        # 2026-09-08 盲审建议：超限回退草稿同样剥自标标记（【基调】/【等X秒】此前可原样漏进发送文本；
        # 热修十三 B2：【立绘：词】同为自标标记，一并剥除）
        _over = _PACE_RE.sub("", _SPRITE_MARK_RE.sub("", _TONE_RE.sub("", (content or reply)))).strip()
        # B1：超限回退稿同样过发送侧净化（strip_non_speech 可能为 None——保持原判空逻辑）
        if strip_non_speech:
            _over = (strip_non_speech(_over, allow_action=allow_actions) or "").strip()
        reply = _over[:limit]
        if not reply.strip():  # 与 A1 二道守卫对称（第2轮审计：防御性补齐）
            reply = (content or reply)[:limit]

    reply = _strip_ellipsis_lines(reply)  # 孤立省略号行清整（2026-09-09 实测残留形态）
    logger.info("[回复{}] {}", ("群:" + group_id) if group_id else "(私)", reply[:500])
    return {"text": reply, "pacing": pacing, "write_intent": write_intent,
            "tone": tone, "sprite": sprite, "attempts": attempts}
