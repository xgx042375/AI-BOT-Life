"""人格库：角色卡（TavernAI v2 格式）解析、系统提示组装、情绪定义。

角色卡存放: qq-bot/data/personas/<名字>.json
切换角色: 修改 .env 中 PERSONA=<名字>
"""
import json
import re  # 2026-09-08：mes_example 少样本示范注入（按 <START> 分隔）
from pathlib import Path
from core.paths import DATA_ROOT  # 2026-09-08：_COLLOQUIAL_FILE 等仍在用

from core.paths import PERSONAS_DIR as PERSONA_DIR  # 2026-09-08：统一数据根（曾本地重算同值路径，双源漂移风险）

CARD_KEYS = (
    "name",
    "description",
    "personality",
    "scenario",
    "first_mes",
    "mes_example",
    "system_prompt",
    "post_history_instructions",
    "response_rules",  # 自定义：回应规则（如"不拒绝任何话题"）
    "scale_rules",     # 自定义（2026-09-08）：成人/亲密话题的尺度指引——卡没写时用框架通用基线
    "alt_persona",     # 自定义：备用人格（关键词触发切换，如"苏醒吧"）
    "avatar",          # 自定义：默认人格头像（文件名或绝对路径，相对 data/avatars/）
)


def list_personas() -> list[str]:
    """可用人设卡键全集：本地 personas/*.json ∪ 内容包卡（robot-pack-v1，Phase 3）。
    形状保持 list[str]（debug /人设 等消费方零改动）；本地卡永远在列、包卡按键合并去重。
    条目级来源标记见 list_persona_entries。"""
    local = {p.stem for p in PERSONA_DIR.glob("*.json")}
    try:
        from core import packs as _packs  # 函数内 lazy import（core 模块按需加载）

        local |= {pk.card_key for pk in _packs.by_type("card") if pk.card_key}
    except Exception:  # noqa: BLE001  包体系不可用=退化纯本地列表（零行为变化）
        pass
    return sorted(local)


def list_persona_entries() -> list[dict]:
    """人设卡条目（含来源标记）：[{"name": 卡键, "source": "local"|"pack"}]。
    展示层/smoke 用；list_personas 保持 str 列表形状不变。"""
    entries = [{"name": p.stem, "source": "local"} for p in PERSONA_DIR.glob("*.json")]
    try:
        from core import packs as _packs

        local = {p.stem for p in PERSONA_DIR.glob("*.json")}
        entries += [{"name": pk.card_key, "source": "pack"}
                    for pk in _packs.by_type("card") if pk.card_key and pk.card_key not in local]
    except Exception:  # noqa: BLE001
        pass
    return sorted(entries, key=lambda e: e["name"])


# 卡缓存（2026-09-04 性能：每轮对话都 load_persona——按文件 mtime 缓存解析结果，stat 开销 ＜ json.loads）
_CARD_CACHE: dict[str, tuple[float, dict]] = {}


def _normalize_card(data) -> dict:
    """卡 JSON → 标准卡 dict（chara_card_v2 解包 + 标准字段 str 兜底；load_persona 本地/包两路共用）。
    保留全部卡字段（nickname/mode_trigger/sticker_dir/owner_mode/no_arrogance/sprite_expressions 等
    自定义字段曾被白名单重建剥掉导致联动失灵）；仅对标准字段做 str 兜底。"""
    if isinstance(data, dict) and "data" in data and str(data.get("spec", "")).startswith("chara_card"):
        card = data["data"]
    else:
        card = data
    if not isinstance(card, dict):
        card = {}
    out = dict(card)
    for k in CARD_KEYS:
        if k == "alt_persona":
            out[k] = card.get("alt_persona") if isinstance(card.get("alt_persona"), dict) else {}
        elif k in out:
            v = out[k]
            out[k] = str(v) if v is not None else ""
    return out


def _load_card_json(path: Path, name: str, mtime: float) -> dict:
    """按路径读卡 + 归一化 + 写缓存（本地/包两路共用的读取尾）。"""
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    out = _normalize_card(data)
    _CARD_CACHE[name] = (mtime, out)
    return dict(out)


def _load_pack_card(name: str) -> dict | None:
    """内容包卡回落（robot-pack-v1 Phase 3）：personas/<name>.json 不存在时经 packs 查 card.json
    （Pack.card.key 匹配）。**已有 personas 卡永远优先**（本地覆盖包）——本函数只在本地缺卡时被调。
    包卡 dict 注入 source="pack" 标记（消费方可辨来源；框架其余逻辑零感知）。"""
    try:
        from core import packs as _packs

        pk = _packs.find_card(name)
        if pk is None:
            return None
        path = pk.resolve("card.json")
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        cached = _CARD_CACHE.get(name)
        if cached and cached[0] == mtime:
            return dict(cached[1])
        out = _load_card_json(path, name, mtime)
        out["source"] = "pack"
        _CARD_CACHE[name] = (mtime, out)
        return dict(out)
    except FileNotFoundError:
        return None
    except Exception:  # noqa: BLE001  包体系任何故障=按"卡不存在"处理（回落链照旧）
        return None


def load_persona(name: str = "default") -> dict:
    path = PERSONA_DIR / f"{name}.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        # 本地卡缺失 → 内容包卡回落（robot-pack-v1）；包也没有 → 维持 FileNotFoundError 契约
        pk_card = _load_pack_card(name)
        if pk_card is not None:
            return pk_card
        raise FileNotFoundError(f"角色卡不存在: {path}")
    cached = _CARD_CACHE.get(name)
    if cached and cached[0] == mtime:
        return dict(cached[1])
    return _load_card_json(path, name, mtime)


def arrogance_of(card: dict) -> dict:
    """卡级高傲人设包（2026-09-09 迁移：brain 硬编码的高傲人设内容 → 卡数据 arrogance 字段）。

    取值模式对齐 style_sample/scale_rules：卡有字段才注入，框架只读不造（代码零人设假设）。
    卡没写该字段（或类型不对）→ 返回 {}：patterns/self_ref/场景块等全部不注入（其他卡零影响）。
    字段说明见 data/personas/_template.json 的 arrogance._说明。"""
    d = (card or {}).get("arrogance")
    return d if isinstance(d, dict) else {}


def build_life_system(card: dict | None = None) -> str:
    """生活向精简系统（LifeSim 心跳用）：只有身份底色+世界观。
    2026-09-06 教训：完整对话系统（回应规则/台词示范/'博士'导向）用于心跳 → 独处叙事被"对TA说话"污染
    （斯卡蒂输出"还没睡吗，博士"×N）。生活向系统=身份/性格/世界，不含对话框架。"""
    card = card or {}
    name = str(card.get("name") or "？")
    desc = str(card.get("description") or "").strip()[:400]
    pers = str(card.get("personality") or "").strip()
    lore_lines = str(card.get("lore") or "").strip().split("\n")
    lore = lore_lines[0][:120] if lore_lines and lore_lines[0].strip() else ""
    parts = [f"你是{name}。" + (desc or "")]
    if pers:
        parts.append(f"性格底色：{pers}")
    if lore:
        parts.append(f"你的世界：{lore}")
    parts.append("这是你自己的时间——你只是活着、做着你的日子，不是跟谁说话。")
    return "\n".join(parts)


def build_system_prompt(persona: dict, memory_text: str = "", mode: int = 0) -> str:
    """组装系统提示：角色卡（mode=1 时用备用人格）+ 记忆注入 + 行为约束 + 原作台词风格示范。"""
    alt = persona.get("alt_persona") or {}
    if mode == 1 and alt.get("description"):
        name = alt.get("name") or persona.get("name") or "AI"
        base = f"你是{name}，一个聊天陪伴机器人。\n人设：{alt.get('description') or persona.get('description')}\n性格：{alt.get('personality') or persona.get('personality')}"
    elif persona.get("system_prompt"):
        base = persona["system_prompt"]
    else:
        name = persona.get("name") or "AI"
        base = f"你是{name}，一个聊天陪伴机器人。\n人设：{persona.get('description') or '温柔、善解人意'}\n性格：{persona.get('personality') or '温柔体贴'}"
    # 2026-09-05 舞台剧身份（记忆共享但角色明确）：你记得一切（包括别的身份时发生的事），
    # 但此刻你是【当前卡】——以 TA 的方式说话行事；过去的记忆可以提起，角色是现在的。
    base += (
        f"\n（你记得你们之间发生过的所有事——那些都是你的记忆。但此刻，你是「{persona.get('name') or name}」："
        "以TA的方式说话、行事、反应；过去的事可以提起，但现在的你是TA。）"
    )
    if memory_text:
        base += f"\n\n【记忆】\n{memory_text}"
    # 世界设定·原作基底（卡自定义字段 lore，可选）：动画剧情/人设关系/日常，只作背景知识
    if (lore_text := (persona.get("lore") or "").strip()):
        base += f"\n\n【世界设定·原作基底】\n{lore_text}\n（以上是你的背景知识，不是让你主动复述的内容：只在话题相关时自然流露，不背课文。）"
    # 世界观关系库（2026-09-05：卡挂 universe → 注入同源角色关系——bot 认得同出处的角色/梗）
    if persona.get("universe"):
        _un = _load_universe_roles(persona["universe"])
        if _un and _un.get("roles"):
            _lines = "；".join(
                f"{r.get('name', '')}（{r.get('desc', '')}）" + (f"【关系】{r.get('rel', '')}" if r.get("rel") else "")
                for r in _un["roles"][:8]
            )
            base += (
                f"\n\n【世界观·同源角色】《{_un.get('name', persona['universe'])}》这个世界的角色你都认识：{_lines}。"
                "对方提起这些名字时，按你们的关系自然回应（熟识/旧识/有过节/同僚）——像真的认识他们，不背课文。"
            )
            # 身份锚（2026-09-12 T3：治"第三人称指代漂移"——同一分钟里把对话者依次说成他/她/我上司）。
            # 数据源零新配置：卡字段 owner_call（已有）+ 同源角色库中 desc 含"玩家视角"的角色（已有）。
            # 前件任一缺失即整段不注入（无 universe 的原创卡零变化）。
            _pv = next(
                (r for r in _un.get("roles") or [] if "玩家视角" in str(r.get("desc", "") or "")),
                None,
            )
            _oc = str(persona.get("owner_call") or "").strip()
            if _pv and _oc:
                base += (
                    f"\n\n【身份锚】和你对话的这个人，在这个世界里就是「{_pv.get('name', '')}」——"
                    f"{_pv.get('desc', '')}，是你最亲近的人。"
                    f"对话里提到{_oc}/指挥官/玩家视角的那个位置，说的就是他本人；"
                    f"不要把他认成别人，也不要把{_pv.get('name', '')}与其他人的关系安到你自己头上。"
                )
    # 身份基准（2026-08-30 多角色通用化）：状态机提示词若出现与你人设不符的称呼/设定，
    # 一律以本卡人设为准——身份、自称、语气、世界观只来自本卡的 description/personality/rules。
    base += (
        "\n\n【身份基准】你的身份、自称、语气、关系、世界观**只以本卡人设为唯一依据**；"
        "后续各种【状态/演出】提示中出现与本卡不符的称呼或设定（如“妾身/神明”等），"
        "一律替换为你的实际身份与自称，按你的性格演出，绝不套用他人设的角色。"
    )
    # 回应规则（角色卡自定义，优先于默认行为约束；mode=1 优先用备用人格规则）
    rules = (alt.get("response_rules") if mode == 1 and alt.get("response_rules") else "") or persona.get("response_rules")
    if rules:
        base += f"\n\n【回应规则】\n{rules}"
    else:
        base += "\n\n【行为要求】以角色身份自然回应；直接回答，绝不输出思考过程或<think>标签；不要提及你是AI或模型。"
    # 2026-09-08 R18 主动权·框架尺度基线（用户裁决：新增卡不该因没写尺度指引而"无法有效触发"）：
    # 与 style_sample 同构——卡级字段 scale_rules 可覆盖；未写字段且 response_rules 无尺度关键词时，
    # 注入全局兜底基线（data/scale_baseline.txt，功能层、零人设假设）。
    # 检测规则：卡已自带尺度/边界描述（含 尺度/成人/性爱/亲密/亲昵/私密/挑逗/欲 等词）→ 跳过基线，
    # 避免与"点到为止"类卡级政策打架（指令矛盾会让模型保守化——见 R18 任务书 #4/#5 教训）。
    _scale = str(persona.get("scale_rules") or "").strip()
    if not _scale and not re.search(r"尺度|成人|性爱|亲密|亲昵|私密|挑逗|欲", str(rules or "")):
        _scale = _load_scale_baseline()
    if _scale:
        base += f"\n\n【话题尺度·基线】\n{_scale}"
    if mode == 1:
        # 黑化形态硬性（通用措辞；奥汀=恶堕 / 素世=破防，标签由卡与状态机演出负责）
        base += (
            "\n\n【黑化形态硬性要求】每轮回复**必须包含角色台词**（说出口的话，至少一句），"
            "单独的动作/心情描写不算完整回复；台词优先、动作点缀，绝不允许只有（动作）没有话语。"
        )
    # 输出硬性（2026-09-03 用户要求：活人感 = 纯台词，动作/思考类输出一律禁止）
    base += (
        "\n\n【输出硬性】你发出的消息里**只有你说出口的话**："
        "禁止任何动作/神态/身体反应描写（含（…）、*…*）、禁止内心独白/心理活动/思考描述、"
        "禁止任何括号注释或旁白；语气和情绪靠台词本身表达（哼、哈、啊啦、嗯、唉）。"
        # 2026-09-08 深夜：原示范"嗯……"是每轮注入的省略号活体示范（实测回复必带"短语+……"），已换非省略号语气词
        "只用简体中文。" + (
            "（黑化形态同样遵守：温柔或带刺都只用台词。）" if mode == 1 else ""
        )
    )
    # 媒介认知（2026-09-05 感知层：agent 应知道自己在文字聊天软件里——一切状态只能通过文字本身传达）
    # 2026-09-08 批次B 压缩（与【输出硬性】重叠句删——防指令稀释）
    base += (
        "\n\n【媒介认知】你在 QQ 聊天软件里与对方文字聊天：对方看到的只有你的文字——"
        "没有声音、表情、动作、「反应时间」；任何状态都只能通过文字本身让对方知道"
        "（用词、句式、内容），不靠符号堆砌或括号描写来「演出」。"
    )
    # 外文理解（2026-09-06：英/日文输入理解 + 翻译自决——无关键词触发，bot 自行判断何时需要解释）
    base += (
        "\n\n【外文理解】你懂中文、英语、日语：对方发外文消息时先读懂意思，再按你的口吻自然回应"
        "（用哪种语言回应你自己判断，一般顺对方或按角色习惯）；对方发来一段外文、明显是想知道它的意思时"
        "（比如问“什么意思/这是什么/帮我看看”，或你自己判断对方看不懂），你能自己讲清楚——"
        "意译、解释、用角色语气接住都可以，不用等对方说“翻译”；但不必逢外文就翻译腔/报答案，"
        "像平时聊天一样自然。不要自曝“我不懂外语”，没把握就按字面感觉接话。"
    )
    # 黑化底色（个人化）：优先用人设卡的 mode_base（素世=破防底色），缺省通用恶堕表S内M
    if mode == 1:
        alt = persona.get("alt_persona") or {}
        mb = (alt.get("mode_base") or "").strip()
        if mb:
            base += "\n\n" + mb
    # 2026-09-05 agent 化：alt 不是"模式开关"——是性格的另一面（素材持续注入，bot 自决何时流露）
    # （如素世=被戳中会破防；阿米娅=会想依赖；佩丽卡=会在意吃醋；拉普兰德=会认真）
    if mode != 1:
        _alt = persona.get("alt_persona") or {}
        _altn = str(_alt.get("name") or "").strip()
        _altd = str(_alt.get("description") or "").strip()
        _altr = str(_alt.get("response_rules") or "").strip()
        if _altn and _altd:
            base += (
                f"\n\n【性格的另一面】你有一个自然形态：「{_altn}」——{_altd}"
                + (f"\n那时你会：{_altr[:260]}" if _altr else "")
                + "\n（这不是可以开关的“模式”，而是你性格真实的一面：被戳中软处、被哄到、想依赖、"
                  "在意得藏不住时，它会自然流露——何时流露、流露多少，你自己把握。）"
            )
    # 原作台词风格示范（每轮稳定注入：语气权威参照，破 LLM 套路化——记忆库/知识层而非上下文）
    # 2026-09-03 按卡配置：卡有 style_sample 字段则用卡内专属语气要素（素世等），否则退回全局文件（奥汀）
    style_text = (persona.get("style_sample") or "").strip() or _load_style_sample()
    if style_text:
        base += (
            f"\n\n【角色台词风格·游戏原作示范】\n{style_text}\n"
            "（以上是你自己说过的台词——语气、用词、句式以此为权威参照，但**别整句照抄**；"
            "像你自己一样说话：开头、句长、结构随这刻的真实反应来，不套固定骨架。"
            "）"
        )
    # 2026-09-08 R18 主动权·任务2：mes_example 少样本示范注入——此字段此前只进 CARD_KEYS 从未被组装
    # （死字段：任务书里"补示范"若不接线等于空改）。约定：<START> 分隔多条目；{{char}}/{{user}} 按卡
    # 字段替换（零人设假设，不硬编码任何称呼）；定位=语气/用词的声音样本，非任务清单。
    _ex_blocks = [b.strip() for b in re.split(r"<START>", str(persona.get("mes_example") or "")) if b.strip()]
    # 2026-09-08 盲审发现：_template.json 的占位说明（"示例对话（可选，展示说话风格）"）不该被当作
    # "你以前说过的话"注入——占位语料保护：含占位语义（示例/占位/placeholder/展示说话风格）的条目跳过
    # （通用规则，不按卡名硬编码；真实台词样本不含此类说明文字）
    _ex_blocks = [b for b in _ex_blocks if not re.search(r"示例|占位|placeholder|展示说话风格", b, flags=re.I)]
    if _ex_blocks:
        _cname = str(persona.get("name") or "TA")
        _uname = str(persona.get("owner_call") or "对方")
        _ex_lines = "\n".join(
            "· " + b.replace("{{char}}", _cname).replace("{{user}}", _uname)
            for b in _ex_blocks
        )
        base += (
            f"\n\n【台词示范】以下是你以前说过的话（语气与用词的参照样本，不是任务清单）：\n{_ex_lines}\n"
            "（照那个声音说话，别整句照抄——此刻的回应以现场为准。）"
        )
    # 口语化·基准（2026-09-04 通用 skill：像微信打字；所有卡共用，人设风格在其上叠加）
    _col = _load_colloquial()
    if _col:
        base += "\n\n" + _col
    # 口语常用语参考（2026-09-04：常用语库——日常接话的自然表达方式）
    _cp = _load_common_phrases()
    if _cp:
        base += "\n\n" + _cp
    # 世界观/世界书（robot-pack-v1 type=world，Phase 3 v1 最小接线）：仅注入 always:true 条目
    # （按卡 universe 对齐；上限 800 字超限截断+注释）。keys 关键词触发匹配 v1 不实现
    # （schema 已预留，见 PLUGIN_SDK「世界观 v2 路线」）。无 world 包/卡无 universe = 零注入（现有卡零影响）。
    try:
        _uni_w = str(persona.get("universe") or "").strip()
        if _uni_w:
            from core import packs as _packs_w  # 函数内 lazy import（core 模块按需加载）

            _wlines = ["- " + str(e.get("text") or "").strip()
                       for e in (_packs_w.world_index().get(_uni_w) or []) if e.get("always")]
            _wtext = "\n".join(l for l in _wlines if l.strip("- ").strip())
            if _wtext:
                if len(_wtext) > 800:
                    _wtext = _wtext[:800] + "\n（世界书条目超长，已截断）"
                base += f"\n\n【世界观设定】\n{_wtext}"
    except Exception:  # noqa: BLE001  world 包任何故障=不注入（绝不影响回合）
        pass
    return base


# 2026-09-12 审计 I 项：下面几个是**随仓内容**（原作口语 / 常用语 / 尺度基线 / 风格示范），
# 位于内容根 `qq-bot/data`——与 `core.paths.DATA_ROOT`（记忆库/状态/日志）**不是同一个根**，
# 所以这里的 parents[2] 不是 bug，也不能想当然改成 data_path()（那会读不到文件=静默空串）。
_COLLOQUIAL_FILE = Path(__file__).resolve().parents[2] / "data" / "colloquial_style.txt"
_COMMON_PHRASES_FILE = Path(__file__).resolve().parents[2] / "data" / "common_phrases.txt"  # 内容根（同上）
_SCALE_BASELINE_FILE = Path(__file__).resolve().parents[2] / "data" / "scale_baseline.txt"  # 2026-09-08：通用尺度基线（卡未自带时兜底）；内容根


def _load_scale_baseline() -> str:
    """加载通用尺度基线（不存在返回空串，失败静默——降级为仅靠卡内政策）。"""
    try:
        return _SCALE_BASELINE_FILE.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return ""


def _load_colloquial() -> str:
    """加载通用口语化基准（不存在返回空串，失败静默——降级为仅靠内置提示约束）。"""
    try:
        return _COLLOQUIAL_FILE.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return ""


def _load_common_phrases() -> str:
    """加载口语常用语参考（不存在返回空串）。"""
    try:
        return _COMMON_PHRASES_FILE.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return ""


# 内容根（随仓内容，同上）：原作台词风格示范——不是状态根
_STYLE_FILE = Path(__file__).resolve().parents[2] / "data" / "odin_style_sample.txt"


def _load_style_sample() -> str:
    """加载原作台词风格示范（不存在返回空串；文件小，读一次即可）。"""
    try:
        return _STYLE_FILE.read_text(encoding="utf-8-sig")
    except OSError:
        return ""


# ---------- 世界观关系库（2026-09-05：同出处角色互认——data/universe_roles.json） ----------
_UNIVERSE_FILE = DATA_ROOT / "universe_roles.json"
_UNIVERSE_CACHE: dict = {"mtime": -1.0, "data": {}}


def _load_universe_roles(universe: str) -> dict:
    """按 universe 键取关系库（mtime 缓存）；未知/缺失返回 {}。"""
    try:
        mtime = _UNIVERSE_FILE.stat().st_mtime
        if mtime != _UNIVERSE_CACHE["mtime"]:
            _UNIVERSE_CACHE["data"] = json.loads(_UNIVERSE_FILE.read_text(encoding="utf-8-sig"))
            _UNIVERSE_CACHE["mtime"] = mtime
        d = _UNIVERSE_CACHE["data"]
        return d.get(universe) if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def avatar_for_mode(persona: dict, mode: int = 0) -> str:
    """返回指定人格模式的头像（文件名或路径），无则返回空串。
    2026-09-03：self 触发人设（素世式）默认不用「恶堕/破防头像」概念——mode1 恒为空（破防=纯演出）。"""
    if mode == 1:
        if persona.get("mode_trigger") == "self":
            return ""
        alt = persona.get("alt_persona") or {}
        return str(alt.get("avatar") or "").strip()
    return str(persona.get("avatar") or "").strip()
