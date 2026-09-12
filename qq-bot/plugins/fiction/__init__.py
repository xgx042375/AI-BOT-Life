"""写文插件（fiction）：QQ 指令驱动的本地长文写作。

命令（私聊/群聊 @bot）：
  写小说 <题材/设定>     建书（meta+大纲）→ 生成第一章
  续写                   按大纲+前情摘要 生成下一章
  改第N章 <要求>         读该章 → 定向重写 → 覆盖
  大纲 / 目录            显示书籍大纲/章节列表
  删除第N章              删除一章（重新续写用）
  导出                   把全书合成 .md 发回（NapCat 文件上传）

权限隔离（安全模型）：
  - 所有文件操作限定在 data/fiction/<书名>/（白名单 base），书名经正则校验，
    任何来自模型/用户输入的路径成分一律拒绝——模型输出只作为"内容"保存，
    绝不拼进路径；章号与文件名全部由代码构造（001.md / 002.md ...）。
  - 模型只读自己书目录下的章节/大纲/元数据（无其他文件访问能力）。
"""
import json
import re
import time
from pathlib import Path
from core.paths import DATA_ROOT
from typing import Optional

from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.rule import to_me
from nonebot.log import logger as nb_logger
from core import llm as core_llm  # 2026-09-10 Phase1：LLM Provider 适配层

logger = nb_logger.opt(colors=False)

driver = get_driver()
# 2026-09-10 Phase1：构造收拢 core.llm（timeout=180 长文口径在 purpose 表中保持；
# 模块属性名 client 保留，smoke/外部换桩依赖）；默认本地配置下行为零变化
client = core_llm.get_client("fiction")

FICTION_ROOT = DATA_ROOT / "fiction"
ALLOW_BOOK_NAME = re.compile(r"^[\w\u4e00-\u9fa5\-]{1,24}$")  # 书名白名单（防路径穿越）
# 2026-09-08 P3：删除直连监听器时代的 9 个触发词正则（章节/写作/续写/改写/大纲/删除/
# 导出/上限解除/自然语言导出）——唯一入口已是 agent [WRITE:] 标记（brain:4368），
# 这批正则全仓零引用（随 _on_fiction 退役时漏清）。

# ---- 写作 Prompt（角色设定 + 结构约束，本地模型适配） ----
# 2026-09-10 审计铁律#2：代笔作者腔中性化——删角色名/性格外貌设定等人称与人设词，
# 只留写作规范（文风要求）；分段/字数/小说体等格式性指引在下方 SYSTEM_TEMPLATE 写作规则里。
WRITER_PERSONA = (
    "你是一位擅长用中文写作网文的代笔作者。文风要求：叙述克制、描写细腻、"
    "语言自然生动、张弛有度，同时不失温情。"
)

SYSTEM_TEMPLATE = (
    WRITER_PERSONA
    + "\n\n=== 写作规则 ===\n"
    "1. 输出【正文】【本章摘要】【章节细节要点】三部分：先写正文（完整小说内容），"
    "然后单独一行 `###SUMMARY###` 再接 1-2 句摘要，再单独一行 `###BEATS###` 再接 2-3 条本章细节要点"
    "（每条一行，涵盖：本节冲突、埋下的伏笔、人物变化——供后续章节记忆引用）。\n"
    "2. 【正文】只写小说内容（不要再输出任何说明、标题或多余记号）。\n"
    "3. 【本章摘要】只写 1-2 句概括（≤80 字），不要写正文内容。\n"
    "4. 正文字数目标：800~1500 字/章。\n"
    "5. 严格基于【大纲】创作；若本章要点为空，则承接前情自然推进下一幕。\n"
    "6. 内容规范：保持网文洁净——不写色情/性暗示/低俗词汇/无端暴力，不得出现现实世界的人名、"
    "平台、bot 相关词汇；不引用、复述对话中除本次创作要求外的任何内容。\n"
)


def _safe_path(book: str, *parts: str) -> Path:
    """路径域断言防呆：任何拼装结果必须严格落在 FICTION_ROOT 内，否则拒绝。

    模型/用户文本永不进入 parts（parts 只允许代码常量，如 f"{n:03d}.md"）；
    这里仍做二次校验，防未来改动引入穿越。
    """
    if not ALLOW_BOOK_NAME.match(book):
        raise ValueError("禁止的书名")
    p = FICTION_ROOT.joinpath(book, *parts).resolve()
    root = FICTION_ROOT.resolve()
    if not p.is_relative_to(root):
        raise ValueError("路径越界，已拒绝")
    return p


_CN_NUMS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _parse_chap(raw: str) -> Optional[int]:
    """章节号解析：兼容「1」「第1章」「第一章」「章节1」「1章」。"""
    s = raw.strip()
    m = re.search(r"(\d+)", s)
    if m:
        return int(m.group(1))
    m = re.search(r"第?([一二三四五六七八九十]+)章?", s)
    if m:
        num = 0
        for ch in m.group(1):
            if ch == "十":
                num = num * 10 + 10 if num else 10
            else:
                num += _CN_NUMS.get(ch, 0)
        return num or None
    return None


def _parse_edit_param(param: str) -> tuple[Optional[int], str]:
    """宽松解析「edit」参数：尽量理解模型的原意，不强制格式。

    支持：「第2章|写详细点」「2章 主角改高傲」「主角性格更傲娇」（没提章节=默认最近一章）、
    「第二章 加一段雨夜」。
    返回 (章节号 or None, 要求)。
    """
    s = (param or "").strip()
    if not s:
        return None, "整体润色，更贴合角色与剧情"
    n = None
    req = s
    # 1) 精确「第X章/章节X/X章」
    m = re.search(r"(?:第\s*)?([0-9一二三四五六七八九十]+)\s*[章回节]", s)
    if m:
        n = _parse_chap(m.group(1))
        req = (s[:m.start()] + s[m.end():]).strip()
    else:
        m2 = re.search(r"章节?\s*([0-9一二三四五六七八九十]+)", s)
        if m2:
            n = _parse_chap(m2.group(1))
            req = (s[:m2.start()] + s[m2.end():]).strip()
        else:
            # 2) 模型常用「3|要求」格式（章节号|竖线|要求）——剥掉前缀，要求清干净
            m3 = re.match(r"^\s*([0-9一二三四五六七八九十]+)\s*[|｜]\s*", s)
            if m3:
                n = _parse_chap(m3.group(1))
                req = s[m3.end():].strip()
    # 无论哪种解析，残余的「N|」前缀一律清掉（模型常拼在要求头上）
    req = re.sub(r"^\s*[0-9一二三四五六七八九十]+\s*[|｜]\s*", "", req).strip()
    req = req.strip("|,，。.；;、 \n\t")
    if not req:
        req = "整体润色，更贴合角色与剧情"
    return n, req[:200]


# ---- 修改要求·要素提取与缺失检测（2026-08-26 v2：关键词窗口法） ----
_EDIT_GENERIC = re.compile(r"(?:整体润色|润色|更贴合|贴合|风格|流畅|详细|丰富|生动|优美|好一点|优化|打磨|精彩|深刻|细腻)$")
_EDIT_TAIL_WORDS = (
    "冲突", "收尾", "结尾", "结局", "动机", "戏份", "桥段", "意象", "画面", "场景",
    "节点", "转折", "氛围", "效果", "走向", "情绪", "心理", "感",
)
# 桥接/虚词字符：2-gram 窗口含这些字基本是虚词或指令词（保留定/场/约等实义字）
_EDIT_BRIDGE_CHARS = set(
    "的了呢吧啊么哟呀嘛与和及或是把将在对为且并但却之而这那她他它你我来去上中下多很都再又其"
    "入反应复重写变让使给从被于向往着每各某此所些个种点直过会出现已还也但才因当并"
)
# 强关注词：用户点名要写的情节要素（哪怕只有 1 个缺失也触发补写）
_EDIT_STRONG_WORDS = set(
    """黄毛 绿豆 豆汤 雨夜 开房 次日 约定 职场 社会 上班 前任 分手 决裂 崩溃 偏执 出轨 背叛 怀孕
    死亡 结婚 离婚 接吻 拥抱 上床 哭泣 打斗 耳光 决裂 崩溃 失控 疯狂 嫉妒 误会 吻 拥抱""".split()
)
_EDIT_STOP_WORDS = set(
    """加入 增加 强化 明确 体现 写出 描写 深入 叙述 要求 内容 本章 章节 故事 小说 情节 剧情 发展 走向
    详细 丰富 生动 优美 流畅 优化 打磨 细腻 深刻 精彩 风格 效果 情绪 心理 氛围 场景 画面 意象 桥段
    冲突 对比 讽刺 收尾 结尾 结局 开头 部分 细节 信息 设定 人物 背景 备注 注意 记得 记住 参考 添加
    以及 并且 还有 另外 同时 此外 应该 需要 想要 希望 可以 能够 开始 已经 只是 还是 就是 不是 没有
    这样 那样 什么 时候 地方 两人 彼此 之间 之后 之前 现在 整个 全都""".split()
)


def _req_elements(req: str) -> list[str]:
    """从修改要求提取短语要素（≤6 个）。拆句→剥指令词→去尾巴词。"""
    parts = re.split(r"[，。；、,;！？\n]|与|和|及|加上|并且|以及|还有", req or "")
    out = []
    for p in parts:
        p = re.sub(r"[（(][^（()）]*[)）]", "", p)
        p = p.strip("“”\"'「」《》（）() ：: \t \n\r")
        p = re.sub(r"^(?:把|将|用|以|按|在|让|使|加入|融入|体现|强化|突出|强调|呈现|描绘|写出|设定|改为|改成)(?:的)?", "", p)
        p = re.sub(r"(?:的?(?:%s))$" % "|".join(_EDIT_TAIL_WORDS), "", p)
        p = p.strip("的了呢吧啊么")
        if len(p) >= 2 and not _EDIT_GENERIC.search(p) and p not in out:
            out.append(p)
    return out[:6]


def _req_keywords(req: str) -> list[str]:
    """关键词窗口：短语内全部 2 字窗口，滤桥接词/停用词，得实义查重词（≤12 个）。"""
    kws = []
    for p in _req_elements(req):
        p = re.sub(r"^\d+\s*[|｜]", "", p)
        p = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", p)
        for i in range(len(p) - 1):
            w = p[i:i + 2]
            if w in kws or w in _EDIT_STOP_WORDS:
                continue
            if any(c in _EDIT_BRIDGE_CHARS for c in w):
                continue
            kws.append(w)
    return kws[:12]


def _should_retry(miss: list[str]) -> bool:
    """触发补写的阈值（正则兜底词表含跨词噪声，需 ≥2 缺失或强关注词才补）。"""
    if not miss:
        return False
    if len(miss) >= 2:
        return True
    return any(k in _EDIT_STRONG_WORDS for k in miss)


async def _llm_extract_keywords(req: str) -> list[str]:
    """让模型把修改要求拆成 2-5 个具体名物要素（雨夜开房/绿豆汤/黄毛前任…），零噪声。
    失败返回 []（调用方回退正则词表）。"""
    try:
        resp = await client.chat.completions.create(
            model=core_llm.resolve_model("fiction"),
            messages=[
                {"role": "system", "content": (
                    "你是需求拆解器。从用户的写作修改要求中提取 2-5 个**具体名物要素**"
                    "（实体/场景/物件/事件名，如：雨夜开房、绿豆汤、黄毛前任、次日约定、职场背景），"
                    "这些要素必须出现在重写后的正文里。只输出要素，逗号分隔，不要解释、不要序号。" )},
                {"role": "user", "content": f"修改要求：{req[:300]}"},
            ],
            temperature=0.2,
            max_tokens=64,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        txt = (resp.choices[0].message.content or "").strip()
        parts = [p.strip(" ，,、；;。.：:\"“”'") for p in re.split(r"[，,、；;\n]", txt)]
        parts = [p for p in parts if 2 <= len(p) <= 12]
        return parts[:6]
    except Exception:  # noqa: BLE001
        return []


def _sanitize_content(text: str, limit: int = 6000) -> str:
    """模型输出清洗：控制长度、去除危险控制字符（防止脏内容/超长注入）。"""
    if not text:
        return ""
    text = text.replace("\x00", "").replace("\r", "")
    return text[:limit]


def _clean_blank_lines(text: str) -> str:
    """空行合并（2026-08-26：模型输出常带大面积空行）：段落间最多保留一个空行，
    行尾空白与首尾空白清掉。写盘/导出（全书合并）前统一调用。"""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    blank = 0
    for ln in text.split("\n"):
        if ln.strip() == "":
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        out.append(ln.rstrip())
    return "\n".join(out).strip()


def _load_meta(book: str) -> Optional[dict]:
    fp = _safe_path(book, "meta.json")
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_meta(book: str, meta: dict):
    p = _safe_path(book)
    p.mkdir(parents=True, exist_ok=True)
    from core import atomics

    atomics.write_text_atomic(
        _safe_path(book, "meta.json"),
        json.dumps(meta, ensure_ascii=False, indent=2),
    )  # 2026-09-07 P3：原子写（曾裸写——写坏=整本书 meta 丢）


def _read_chapter(book: str, n: int) -> Optional[str]:
    fp = _safe_path(book, f"{n:03d}.md")
    if not fp.exists():
        return None
    try:
        return fp.read_text(encoding="utf-8-sig")[:20000]
    except OSError:
        return None


def _write_chapter(book: str, n: int, content: str):
    p = _safe_path(book)
    p.mkdir(parents=True, exist_ok=True)
    from core import atomics

    atomics.write_text_atomic(
        _safe_path(book, f"{n:03d}.md"),
        _clean_blank_lines(_sanitize_content(content, limit=20000)),
    )  # 2026-09-07 P3：原子写（同 _save_meta）


def _chapter_list(meta: dict) -> list[int]:
    """meta['chapters']= [{'n':1,'summary':...}, ...] → 已完成章号列表。"""
    return [c["n"] for c in meta.get("chapters", [])]


def _recent_summaries(meta: dict, k: int = 3) -> str:
    """最近 k 章摘要（滚动记忆）。"""
    chs = meta.get("chapters", [])[-k:]
    if not chs:
        return "（尚无前情）"
    return "\n".join(f"第{c['n']}章：{c.get('summary','')}" for c in chs)


def _last_act(meta: dict) -> str:
    """"本章推进依据"：大纲=故事梗概（无章节行）→ 返回梗概全文，模型基于整体走向自行推进。"""
    out = meta.get("outline", "")
    return out if out.strip() else ""


async def _llm_think(prompt: str) -> str:
    """思考模式 LLM 调用（修改/大纲类任务）。限制输出长度与推理预算，控制耗时。"""
    resp = await client.chat.completions.create(
        model=core_llm.resolve_model("fiction"),
        messages=[
            {"role": "system", "content": WRITER_PERSONA + "\n你是故事梗概设计师：先理解任务与现状，再输出修改后的梗概正文。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.8,
        max_tokens=2048,  # 慢：4096→2048（梗概输出本身 ≤800 字，留足思考空间即可）
        extra_body={"chat_template_kwargs": {"enable_thinking": True}},
    )
    return resp.choices[0].message.content or ""


async def _llm(messages) -> str:
    resp = await client.chat.completions.create(
        model=core_llm.resolve_model("fiction"),
        messages=messages,
        temperature=0.9,
        max_tokens=4096,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    return resp.choices[0].message.content or ""


async def _llm_edit(messages) -> str:
    """编辑类任务的 LLM 调用：开启思考（先想清要求再动笔）+ 低温，提升改稿保真度。"""
    resp = await client.chat.completions.create(
        model=core_llm.resolve_model("fiction"),
        messages=messages,
        temperature=0.7,
        max_tokens=4096,
        extra_body={"chat_template_kwargs": {"enable_thinking": True}},
    )
    return resp.choices[0].message.content or ""


def _recent_refs(meta: dict, k: int = 3, limit: int = 800) -> str:
    """历史参考要点（压缩后长期记忆）：最近 k 条合并，总长 ≤ limit。"""
    refs = meta.get("refs", []) or []
    if not refs:
        return ""
    txt = "\n".join(f"- {r}" for r in refs[-k:])
    return txt[:limit]


def _recent_beats(meta: dict, k: int = 3) -> str:
    """最近 k 章的细节要点（BEATS），供续写注入。"""
    beats_map = meta.get("beats", {}) or {}
    out = []
    for c in meta.get("chapters", [])[-k:]:
        b = beats_map.get(str(c["n"])) or beats_map.get(c["n"])
        if b:
            out.append(f"第{c['n']}章细节：{b}")
    return "\n".join(out)


def _delete_chapter(book: str, meta: dict, n: int):
    """删除第 n 章：删文件 + 元数据移除 + 后续章节重排（文件与编号连续）。"""
    fp = _safe_path(book, f"{n:03d}.md")
    if fp.exists():
        fp.unlink()
    keep = sorted([c for c in meta["chapters"] if c["n"] != n], key=lambda c: c["n"])
    # 2026-09-07 P2：beats 随重排同步迁移（曾只按新编号清理——删章后保留的是被删章的要点、
    # 现存章的要点错位/悬空，续写提示词注入错误前情）
    old_beats = dict(meta.get("beats", {}) or {})
    new_beats = {}
    for new_n, c in enumerate(keep, start=1):
        b = old_beats.get(str(c["n"]))
        if b:
            new_beats[str(new_n)] = b
        if new_n != c["n"]:
            src = _safe_path(book, f"{c['n']:03d}.md")
            dst = _safe_path(book, f"{new_n:03d}.md")
            if src.exists():
                src.replace(dst)
        c["n"] = new_n
    meta["chapters"] = keep
    meta["beats"] = new_beats


# ---------------- 章节上限（2026-08-27：固定N章/删掉多余 的结构性约束，工具层强制执行） ----------------
_CAP_RE = re.compile(
    r"(?:只|就|最多|限定|保持|控制|保留|压缩为|精简到|改成|定为|写满|达到|一共|总共|共|严格控制在|仅保留|只要)"
    r"[^，。！？；、\s]{0,2}?([0-9一二三四五六七八九十]{1,3})\s*章"
    r"|([0-9一二三四五六七八九十]{1,3})\s*章\s*(?:为限|以内|封顶)"
)
_TRIM_RE = re.compile(r"删?掉?多余|多余的.*(?:删|去|删掉|去掉)|只保留|仅保留|其他的?删|超出的.*删")


def _parse_cap(req: str) -> int | None:
    """从要求中提取「固定为 N 章」的上限（三章/3章为限/只保留五章…）。"""
    if not req:
        return None
    m = _CAP_RE.search(req)
    if m:
        n = _parse_chap(m.group(1) or m.group(2))
        if n and 1 <= n <= 999:
            return n
    return None


def _trim_to_cap(book: str, meta: dict, cap: int) -> int:
    """截断到 cap 章：删除超限文件、重排编号、清理 beats。返回删除的章数。"""
    keep = [c for c in sorted(meta["chapters"], key=lambda x: x["n"]) if c["n"] <= cap]
    removed = len(meta["chapters"]) - len(keep)
    for c in meta["chapters"]:
        if c["n"] > cap:
            fp = _safe_path(book, f"{c['n']:03d}.md")
            if fp.exists():
                fp.unlink()
    old_beats = dict(meta.get("beats", {}) or {})
    new_beats = {}
    for new_n, c in enumerate(keep, start=1):
        # 2026-09-07 P2：beats 随重排同步迁移（同 _delete_chapter——曾只按编号过滤导致错位）
        b = old_beats.get(str(c["n"]))
        if b:
            new_beats[str(new_n)] = b
        if c["n"] != new_n:
            src = _safe_path(book, f"{c['n']:03d}.md")
            dst = _safe_path(book, f"{new_n:03d}.md")
            if src.exists():
                if dst.exists():
                    dst.unlink()
                src.replace(dst)
        c["n"] = new_n
    meta["chapters"] = keep
    meta["beats"] = new_beats
    return removed


def _apply_cap(book: str, meta: dict, cap: int) -> tuple[int, str]:
    """设置上限并截断；返回 (删除数, 回执附加说明)。（2026-09-07：清死参数 _say）"""
    old_cap = meta.get("chapter_cap")
    meta["chapter_cap"] = cap
    removed = _trim_to_cap(book, meta, cap)
    note = ""
    if removed:
        note = f"（已删除 {removed} 章多余内容并重排编号）"
    elif old_cap and old_cap != cap:
        note = f"（上限已更新为 {cap} 章）"
    return removed, note


# ---- 写作执行互斥 ----
# 2026-09-08 P3：删除 run_write_job 与应承话术池——生产唯一入口是 brain:4390 的
# [WRITE:] 分支（自带 _EXEC_BUSY 互斥 + 回执走 brain 发送流，需要拿回执值而非函数内直发），
# 本函数与应承话术池零生产引用（仅 smoke 以 hasattr/inspect 引用，断言已同步改绑 exec_action）。
_EXEC_BUSY: set[str] = set()  # 每用户写任务互斥（防止叠加排队）


async def _do_outline(meta: dict, book: str, req: str, ref: str, _say) -> str:
    """大纲修改的统一执行（outline 动作与 edit 兜底共用）。"""
    await _say(f"（正在重写《{book}》大纲：{(req or '按用户要求调整')[:40]}…）")
    new_outline = await _revise_outline(meta, req, ref)
    meta["outline"] = new_outline
    if ref:
        meta.setdefault("refs", []).append(await asyncio_compress_ref(ref))
    # 2026-08-27：大纲要求带「固定N章」→ 写入章节上限并按上限截断多余章节
    cap = _parse_cap(req)
    cap_note = ""
    if cap:
        removed, cap_note = _apply_cap(book, meta, cap)
        logger.info("struct cap(outline): book=%s cap=%d removed=%d", book, cap, removed)
    _save_meta(book, meta)
    return f"《{book}》梗概已修改 ✅\n===== 新故事梗概 =====\n{new_outline}\n（已完成章节进度不变，后续走向按新梗概走）{cap_note}"


async def _compress_ref(ref: str) -> str:
    """参考材料压缩成 ≤200 字要点（长期记忆）；失败则原文截断兜底。"""
    if not ref:
        return ""
    try:
        raw = await _llm(
            [
                {"role": "system", "content": "你是剧情设定压缩器。把参考材料压缩成 ≤200 字的风格/设定要点，"
                 "保留可用于小说写作的元素（氛围、设定、意象、叙事风格），丢掉不确定的细节。只输出要点。"},
                {"role": "user", "content": ref[:1500]},
            ]
        )
        txt = (raw or "").strip()[:200]
        return txt or ref[:200]
    except Exception:  # noqa: BLE001
        return ref[:200]


def _split_output(raw: str) -> tuple[str, str, str]:
    """模型输出解析（健壮版）：支持 [正文]\n###SUMMARY###\n[摘要]\n###BEATS###\n[细节要点]

    - 无分隔符 → 整篇当正文；
    - 摘要区异常（超长/为空）→ 正文首句降级；
    - beats（本章剧情细节要点 2-3 条）缺失/超长 → 空串（不阻塞）。
    """
    body, summary, beats = "", "", ""
    if "###SUMMARY###" in raw:
        body, _, rest = raw.partition("###SUMMARY###")
    else:
        body, rest = raw, ""
    if "###BEATS###" in rest:
        summary, _, beats = rest.partition("###BEATS###")
    else:
        summary = rest
    body = (body or "").strip()
    summary = (summary or "").strip()
    beats = (beats or "").strip()
    if len(summary) > 200:
        summary = ""
    if not summary:
        m = re.search(r"^[^。！？!?\n]{4,80}[。！？!?]", body)
        summary = (m.group(0)[:80] if m else "（本章未生成摘要）")
    beats = beats[:300] if len(beats) > 300 else beats
    return body[:20000], summary[:200], beats


async def _gen_chapter(meta: dict, ref: str = "") -> tuple[str, str, str]:
    """生成下一章：正文 + 摘要 + BEATS(细节要点)。ref=参考材料（可选）。"""
    prompt = (
        f"=== 书名 ===\n《{meta['title']}》\n"
        f"=== 题材设定 ===\n{meta.get('setting','')}\n"
        f"=== 人物设定 ===\n{meta.get('characters','')}\n"
        f"=== 大纲（逐行=逐章要点，已完成的上方）===\n{meta.get('outline','')}\n"
        f"=== 前情摘要（最近章节）===\n{_recent_summaries(meta)}\n"
        + (f"=== 本作参考记忆（历史参考要点，长期有效）===\n{_recent_refs(meta)}\n" if _recent_refs(meta) else "")
        + f"=== 故事梗概（整体走向，基于此推进本章）===\n{_last_act(meta) or '（无梗概，承接前情自然推进）'}\n"
        + (f"\n=== 参考材料（用户要求参考的内容，融入本章风格/设定）===\n{ref}\n" if ref else "")
        + f"\n现在是第 {len(_chapter_list(meta)) + 1} 章，请开始写作。"
    )
    raw = await _llm(
        [
            {"role": "system", "content": SYSTEM_TEMPLATE},
            {"role": "user", "content": prompt},
        ]
    )
    return _split_output(raw)


async def _gen_outline(setting: str) -> str:
    """建书时生成**故事梗概**（整体剧情走向；无章节编号，段落式）。"""
    prompt = (
        f"题材/设定：{setting}\n"
        "请写出这份小说的完整故事梗概（3-5 段）：包含 主线走向、阶段起伏（起承转合）、"
        "主角弧光、关键转折与结局方向；按叙事顺序行文，"
        "**不要分章节、不要编号、不要分点列举**；只输出梗概正文。"
    )
    raw = await _llm(
        [
            {"role": "system", "content": WRITER_PERSONA + "\n你是故事梗概设计师，只输出梗概正文。"},
            {"role": "user", "content": prompt},
        ]
    )
    return raw.strip()


def _norm_del_word(w: str) -> str:
    """净化删除词：剥掉修饰后缀，剔除含动词/噪音的整串。"""
    w = re.sub(r"(?:相关的|有关的|相关|有关|的|字样|字眼|名字|名称|文字|称号|词)+$", "", w.strip())
    # 含动作/噪音字（删/掉/去/改/换/都/也/清/把/给/了）且不是纯专名 → 丢弃（留给 LLM 提取）
    if re.search(r"[删掉去改换都也清把给了]", w):
        return ""
    return w.strip()


async def _extract_del_words_llm(req: str) -> list[str]:
    """LLM 提取删除词（正则兜底失败时用；提取任务小模型可靠）。"""
    try:
        raw = await _llm(
            [
                {"role": "system", "content": "你是指令解析器。从用户的修改要求中，提取用户要求删除/移除的词、名称或名词短语"
                 "（如人名、称谓、设定词）。输出 JSON 数组，如 [\"主角名\"]；没有要删除的词则输出 []。只输出数组。"},
                {"role": "user", "content": req[:400]},
            ]
        )
        import json as _json
        m = re.search(r"\[.*?\]", raw, re.S)
        if not m:
            return []
        arr = _json.loads(m.group(0))
        out = []
        for w in arr:
            w = _norm_del_word(str(w))
            if w and len(w) >= 2 and len(w) <= 8 and w not in out:
                out.append(w)
        return out
    except Exception:  # noqa: BLE001
        return []


def _extract_del_words(req: str) -> list[str]:
    """从修改要求中提取"要删除的明确词"（专名级，如 主角名）。
    支持：「删掉神相关的和主角名字样」「主角名字样去掉」「主角名相关的词删掉」。
    """
    words: list[str] = []
    for m in re.finditer(r"(?:删掉|去掉|删除|不要|清除|换掉)(?:的|了|下)?([^，。；;、和与及]+?)(?:字样|字眼|名字|名称|文字|称号|的词|词)?", req):
        w = _norm_del_word(m.group(1))
        if w and len(w) >= 2 and w not in words:
            words.append(w)
    # 反向句式：X字样也去掉 / X名字去掉
    for m in re.finditer(r"([^，。；;、\s]{2,10})(?:字样|字眼|名字|名称|文字|称号)(?:也|都)?(?:删掉|去掉|删除|不要|清掉|换掉)", req):
        w = _norm_del_word(m.group(1))
        if w and w not in words:
            words.append(w)
    # 兜底：句式里"和X字样"后半段
    for m in re.finditer(r"[和与及、]([^，。；;、和与及]{2,8})(?:字样|字眼|名字|名称|的词|相关)?", req):
        w = _norm_del_word(m.group(1))
        if w and len(w) >= 2 and w not in words:
            words.append(w)
    return words


async def _revise_outline(meta: dict, req: str, ref: str = "") -> str:
    """按用户要求修改**故事梗概**（整体走向；保留已完成剧情进展，重写未完成走向）。
    近章 BEATS + 历史参考注入，保证连续性；正文段落式，无章节编号。
    """
    done = len(_chapter_list(meta))
    del_words = _extract_del_words(req or "")
    if not del_words:
        for w in await _extract_del_words_llm(req or ""):
            if w not in del_words:
                del_words.append(w)
    if del_words:
        logger.info("outline del words: %r", del_words)
    # ---- 任务单式修改请求（模型为主：先读大纲、理解要求，再输出修改后的完整梗概；开放思考）----
    prompt = (
        f"书名《{meta['title']}》题材：{meta.get('setting','')}\n"
        f"=== 你要完成的任务（最高优先级）===\n{req or '调整后续剧情走向'}\n"
        + (f"【任务中的删除要求】以下词必须从新梗概中彻底消失：{'、'.join(del_words)}；"
           f"涉及这些角色/事物时用「主角」或泛指代替。\n" if del_words else "")
        + f"=== 现状资料 ===\n"
        f"当前梗概：\n{meta.get('outline','')}\n"
        f"已完成剧情（前 {done} 章）：\n{_recent_summaries(meta)}\n"
        + (f"近章细节：\n{_recent_beats(meta)}\n" if _recent_beats(meta) else "")
        + (f"参考材料（可融合的风格/设定）：\n{ref}\n" if ref else "")
        + "(参考的风格锚点：若任务要求'调低科技/去超自然'，新梗概应为**日常现代都市**："
           "无超能力、无黑科技装备/芯片/液态金属异能；冲突来自组织、人心与日常；"
           "若任务要求删除'神'相关，则设定中不得出现神祇/神力/神域/神战等字眼。)\n"
        + "=== 工作流程 ===\n"
        "1. 先通读【现状资料】与【任务】，理解任务每一条要求；\n"
        "2. 逐段对照任务，确定哪些内容要删、要改、要新增（删除要求一个都不能漏）；\n"
        "3. 输出修改后的完整故事梗概（3-5 段，段落式，与已完成剧情衔接合理，无章节编号、无分点）；\n"
        "4. 输出前自查一遍：任务里的每一条都落实了吗？有遗漏就修正后再输出。\n"
        "=== 硬性要求 ===\n"
        "**不得沿用旧梗概的句子结构与具体表述**（完全重新组织语言）；\n"
        "任务要求删除/调低的内容（超自然、神祇、黑科技、特定字样等）**绝不允许出现在新梗概任何段落**，"
        "每出现一处都算失败——输出里也不得出现『核查：已删除』之类说明文字。"
    )
    raw = await _llm_think(prompt)
    outline = raw.strip()
    # 剥离 preamble：开头 1-2 行纯说明行（"（修改后故事梗概）""根据要求…"等）
    def _strip_preamble(text: str) -> str:
        lines = [l for l in text.splitlines()]
        while lines:
            first = lines[0].strip()
            if not first:
                lines.pop(0)
                continue
            if re.match(r"^[（(【\[].{0,40}[）)】\]】]。?$", first) \
                    or re.match(r"^(?:根据|按照|按|遵循|修改后|新梗概|以下|故事梗概|正文|梗概如下)。{0,50}$", first) \
                    or (len(first) <= 20 and any(w in first for w in ("梗概", "要求", "修改", "根据"))):
                lines.pop(0)
            else:
                break
        return "\n".join(lines).strip()

    outline = _strip_preamble(outline)
    # 剥离 postamble：模型输出末尾的"（核查：…）"类自检备注
    def _strip_postamble(text: str) -> str:
        t = text
        m = re.search(r"\n\s*[（(【\[【]?\s*(?:核查|检查|自检|备注|说明)[：:][^\n]{0,120}[）)】\]】]?\s*$", t)
        if m:
            t = t[:m.start()].strip()
        return t

    outline = _strip_postamble(outline)
    # 删除类要求：≥2 字词代码级无条件替换（模型为主，此处保证 100% 落地）
    for w in del_words:
        if len(w) >= 2 and w in outline:
            outline = outline.replace(w, "主角")
    # 最后防线复核
    if del_words and any(len(w) >= 2 and w in outline for w in del_words):
        outline = _strip_preamble((await _llm_think(prompt + "\n（上次输出未完全落实删除要求——含被删词，必须彻底移除，违规处用「主角」替代）")).strip())
        for w in del_words:
            if len(w) >= 2:
                outline = outline.replace(w, "主角")
    return outline


async def _rewrite_chapter(meta: dict, book: str, n: int, req: str, ref: str = "") -> Optional[tuple[str, str, str]]:
    """按用户要求重写某章（返回 body, summary, beats）。ref=参考材料（可选）。
    2026-08-26 保真修复：要求前置+强制化；原文压缩并降级为背景参考；缺要素自动补写一次。"""
    old = _read_chapter(book, n)
    if old is None:
        return None
    _llm_els = await _llm_extract_keywords(req)
    els_from_llm = bool(_llm_els)
    els = _llm_els or _req_keywords(req)
    elem_hint = "、".join(els) if els else ""
    base = (
        "== 你的任务 ==\n"
        f"重写《{meta['title']}》第 {n} 章。以下修改要求**逐条落实、缺一不可**，"
        "不要只改几个字——为满足要求，宁可重写本章绝大多数内容：\n"
        f"{req}\n"
    )
    if elem_hint:
        base += f"\n== 关键要素（构思时确保这些内容在正文中真实出现）==\n{elem_hint}\n"
    base += (
        f"\n== 背景参考（人物/题材/前情，只用于保持衔接；**不得照抄原文句子与段落**）==\n"
        f"=== 题材 ===\n{meta.get('setting', '')}\n=== 人物 ===\n{meta.get('characters', '')}\n"
        f"=== 前情摘要 ===\n{_recent_summaries(meta)}\n"
        + (f"=== 近章细节 ===\n{_recent_beats(meta)}\n" if _recent_beats(meta) else "")
        + (f"=== 本作历史要求（此前多次补充的设定，必须持续遵守，不得违背）===\n{_recent_refs(meta)}\n"
           if _recent_refs(meta) else "")
        + f"=== 第 {n} 章原文（压缩引用，仅参考事件走向）===\n{old[:2500]}\n"
        + (f"=== 参考材料（用户要求参考的内容，融入本章风格/设定）===\n{ref}\n" if ref else "")
        + "\n输出格式同前：正文后 ###SUMMARY### 摘要、###BEATS### 细节要点。"
    )
    miss_join = ""
    for attempt in (1, 2):
        prompt = base + (f"\n\n⚠️ 上一稿没落实要求，遗漏了：{miss_join}。本次重写**必须**完整写出这些内容，不得省略。"
                         if attempt == 2 else "")
        raw = await _llm_edit(
            [
                {"role": "system", "content": SYSTEM_TEMPLATE},
                {"role": "user", "content": prompt},
            ]
        )
        body, summary, beats = _split_output(raw)
        miss = [k for k in els if k not in body]
        # LLM 拆解的要素精确，任一缺失即补写；正则兜底词表含跨词噪声，按阈值防误重试
        if not miss or attempt == 2 or (not els_from_llm and not _should_retry(miss)):
            break
        miss_join = "、".join(miss)
        logger.info("rewrite missing keywords, retry: {!r} <- req={!r}", miss_join, req[:60])
    return body, summary, beats


# ---- NoneBot 入口 ----
# 2026-09-06 Phase E 拆解：插件直连监听器已退役（与 brain 正则快速通道三线打架、同消息双跑）——
# 写作唯一入口 = agent 回复中的 [WRITE:...] 标记 → brain → exec_action（用户级互斥）。
# 下方 _on_fiction 保留为死代码（E4 孤儿清理时删除）。
fiction_matcher = None


async def _send_file(bot: Bot, event: MessageEvent, path: Path, name: str):
    """NapCat 文件发送（私聊/群聊分别走对应 API）。"""
    p = str(path.resolve())
    if isinstance(event, GroupMessageEvent):
        await bot.call_api(
            "upload_group_file", group_id=event.group_id, file=p, name=name
        )
    else:
        await bot.call_api(
            "upload_private_file", user_id=event.get_user_id(), file=p, name=name
        )


def _find_book_in_text(text: str) -> str | None:
    """按书名点名查找：<《X》书名号> 或 已有书目出现在句中（如"白夜殇那本"）。"""
    m = re.search(r"[《「]([^》」]{1,24})[》」]", text)
    if m:
        cand = m.group(1).strip()
        if ALLOW_BOOK_NAME.match(cand) and (FICTION_ROOT / cand / "meta.json").exists():
            return cand
    if FICTION_ROOT.is_dir():
        for d in FICTION_ROOT.iterdir():
            if d.is_dir() and (d / "meta.json").exists() and d.name in text:
                return d.name
    return None


# 导出去重（2026-08-28：brain 快速通道与 fiction 命令层都会触发同一条导出消息 → 同一 message_id 只执行一次）
_EXPORT_CLAIMED: set[str] = set()
_EXPORT_CLAIM_MAX = 100


async def run_export_job(bot, event, text: str, is_admin: bool = False):
    """导出执行（全书或单章）——brain 快速通道与 fiction 命令层共用入口（消息级去重）。
    2026-09-07 P1②：所有权校验（曾按书名点名即可取任意人的书——只修指针路径没修指名路径）。"""
    mid = str(getattr(event, "message_id", "") or "")
    if mid:
        if mid in _EXPORT_CLAIMED:
            return  # 已由另一入口处理，跳过（防重复发文件）
        _EXPORT_CLAIMED.add(mid)
        if len(_EXPORT_CLAIMED) > _EXPORT_CLAIM_MAX:
            _EXPORT_CLAIMED.clear()
    from core import atomics  # 2026-09-08：导出文件原子写用

    try:
        _uid = str(getattr(event, "user_id", "") or event.get_user_id())
        book = _find_book_in_text(text) or _last_book(_uid)  # 2026-09-07 P2：按用户指针（曾全局单指针可取他人书）
        meta = _load_meta(book) if book else None
        if not meta:
            await bot.send(
                event,
                "还没有在写的书——先「写小说 <题材>」开一本，或带上书名（如：《白夜殇》完整版发给我）。",
            )
            return
        _deny = _claim_or_check(book, _uid, is_admin)
        if _deny:
            _forget_book(_uid)
            await bot.send(event, _deny)
            return
        m_n = re.search(r"第\s*([0-9一二三四五六七八九十]{1,3})\s*章", text)
        if m_n:
            n = _parse_chap(m_n.group(1))
            if n not in _chapter_list(meta):
                await bot.send(event, f"第 {n} 章还不存在（目前已写到 {len(_chapter_list(meta))} 章）。")
                return
            body = _read_chapter(book, n) or ""
            full = f"# 《{meta['title']}》\n\n## 第 {n} 章\n\n{_clean_blank_lines(body)}\n"
            out = _safe_path(book, f"{meta['title']}_第{n}章.md")
            atomics.write_text_atomic(out, full)  # 2026-09-08：原子写（导出侧曾漏网裸 write_text）
            await bot.send(event, f"（正在发第 {n} 章文件…）")
            await _send_file(bot, event, out, out.name)
            await bot.send(event, f"《{meta['title']}》第 {n} 章文件已发你 ✅")
            return
        full = _compile_book(book, meta)
        out = _safe_path(book, f"{meta['title']}_全集.md")
        atomics.write_text_atomic(out, full)  # 2026-09-08：原子写（同上）
        await bot.send(event, f"（正在发送《{meta['title']}》全书…）")
        await _send_file(bot, event, out, out.name)
        await bot.send(event, f"《{meta['title']}》全书共 {len(_chapter_list(meta))} 章，文件已发你 ✅")
    except Exception as e:  # noqa: BLE001
        logger.exception("export failed: %s", e)
        await bot.send(event, "（文件发送出了点岔子，稍等重试？）")


_STATE = {"last_book": None}
LAST_BOOK_FILE = FICTION_ROOT.parent / "fiction_last.txt"


def _book_ptr(user_id: str = "") -> tuple[str, "Path"]:
    """2026-09-07 P2 用户隔离：每用户独立"当前书"指针（曾全局单指针——任何用户可对别人的
    当前书续写/删除/导出，他人书籍文件可被任意用户获取）。空 user_id（旧调用方）走全局指针。"""
    uid = str(user_id or "").strip()
    if uid:
        return f"last_book:{uid}", FICTION_ROOT.parent / f"fiction_last_{uid}.txt"
    return "last_book", LAST_BOOK_FILE


def _last_book(user_id: str = "") -> Optional[str]:
    """当前书：内存 → 持久化文件（按用户隔离）→ 扫描最近更新的书（重启不丢、多书可回溯）。"""
    key, fp = _book_ptr(user_id)
    if _STATE.get(key):
        return _STATE[key]
    try:
        if fp.exists():
            name = fp.read_text(encoding="utf-8-sig").strip()
            if name and ALLOW_BOOK_NAME.match(name) and (FICTION_ROOT / name / "meta.json").exists():
                _STATE[key] = name
                return name
    except OSError:
        pass  # 指针读不出就落到下方的全量扫描（显式兜底，非静默失败）
    # 回退：全量扫描 meta.json，取最后修改的书
    best, best_ts = None, 0.0
    if FICTION_ROOT.is_dir():
        for meta_fp in FICTION_ROOT.glob("*/meta.json"):
            try:
                ts = meta_fp.stat().st_mtime
                if ts > best_ts:
                    best_ts, best = ts, meta_fp.parent.name
            except OSError:
                continue
    if best:
        _STATE[key] = best
    return best


def _remember_book(book: str, user_id: str = ""):
    key, fp = _book_ptr(user_id)
    _STATE[key] = book
    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        from core import atomics
        atomics.write_text_atomic(fp, book)  # 2026-09-07 P2：原子写 + 按用户指针
    except OSError:
        pass  # 建目录失败；写盘失败的日志在 atomics 内部（返回 False，不抛）


def _forget_book(user_id: str = ""):
    """清本用户"当前书"指针（2026-09-07 P1②：所有权校验拒绝时调用——
    _last_book 的回退扫描会把别人的书绑进无指针用户的 _STATE，必须一并清掉）。"""
    key, fp = _book_ptr(user_id)
    _STATE.pop(key, None)
    try:
        fp.unlink()
    except OSError:
        pass  # 文件本就不存在是常态（删除是幂等意图）


def _claim_or_check(book: str, user_id: str, is_admin: bool = False) -> str | None:
    """书籍所有权校验 + 首次接触认领（2026-09-07 P1②）。

    背景：书是全局命名空间，uid 指针隔离只护住"未指名"路径——回退扫描劫持/书名点名导出/
    标题碰撞三路都可跨用户续写、删章、篡改、导出他人书籍（meta 原无 owner 字段）。
    规则：meta 无 owner → 当前用户认领；owner 匹配或管理员 → 放行；否则拒绝。
    返回 None=放行；返回提示文本=拒绝（调用方原样回给用户）。"""
    meta = _load_meta(book)
    if not meta:
        return None  # 无 meta 的异常路径由调用方自己的"数据丢了"分支处理
    owner = str(meta.get("owner", "") or "")
    if not owner:
        meta["owner"] = str(user_id or "")
        _save_meta(book, meta)
        return None
    if is_admin or (user_id and owner == str(user_id)):
        return None
    return f"《{book}》是别人开的书。想看的话自己去开一本——说个题材就行，或者报你自己写的书名。"


def _compile_book(book: str, meta: dict) -> str:
    """全书合并（完整版）：每章空行合并 + 章节标题结构统一。"""
    parts = [f"# 《{meta['title']}》", "", meta.get("setting", ""), "", "---", ""]
    for c in sorted(meta["chapters"], key=lambda x: x["n"]):
        body = _clean_blank_lines(_read_chapter(book, c["n"]) or "")
        parts.append(f"## 第 {c['n']} 章\n\n{body}\n")
    return "\n".join(parts)


# ---- 异步薄封装（直接转发；保留测试入口） ----
async def asyncio_gen_outline(setting: str) -> str:
    return await _gen_outline(setting)


async def asyncio_gen_chapter(meta: dict, ref: str = ""):
    return await _gen_chapter(meta, ref)


async def asyncio_rewrite(meta: dict, book: str, n: int, req: str, ref: str = ""):
    return await _rewrite_chapter(meta, book, n, req, ref)


async def asyncio_compress_ref(ref: str) -> str:
    return await _compress_ref(ref)


# ---- 非指令式入口（brain 调用：模型自主判断意图后执行） ----
# 动作协议: [WRITE:start|设定] / [WRITE:continue] / [WRITE:edit|N|要求] / [WRITE:show] / [WRITE:outline|要求]
# ---- 书名提取（2026-08-26 修复：快速通道把整句当前16字当书名，含标点被白名单拒） ----
_TITLE_HINT_RE = re.compile(
    r"(?:题目|书名|标题|名字|名)\s*[：:是为，,]?\s*[《“”\"'‘’]?\s*([\u4e00-\u9fa5A-Za-z0-9\-]{1,24})"
)
_TITLE_PREFIX_RE = re.compile(
    r"^(?:请|麻烦|帮我|给我|求你|替我)?(?:写一点|写一篇|写一本|写一部|写一个|写个|写文|开写|写|创作)?"
    r"(?:小说|短篇|故事|同人|文)?"
)
_TITLE_CUT_RE = re.compile(r"中间|但是|后来|最后|直到|然而|不过|可是|最终|却|然后")


def _derive_book_title(setting: str) -> str | None:
    """从用户设定文本提取合法书名（中文/字母/数字/短横线，≤24）。
    优先级：①题目/书名《X》显式名 → ②剥离指令词/括号/标点的核心名 → ③前几字兜底。失败返回 None。"""
    s = (setting or "").strip()
    if not s:
        return None
    # ① 显式书名：「题目：白夜殇」「书名《白夜殇》」「名字是"白夜殇"」
    m = _TITLE_HINT_RE.search(s)
    if m:
        cand = m.group(1).strip()
        if ALLOW_BOOK_NAME.match(cand) and len(cand) >= 2:
            return cand
    # ①b 《书名》显式书名（无"题目"字样也认）
    m2 = re.search(r"《([\u4e00-\u9fa5A-Za-z0-9\-]{1,24})》", s)
    if m2 and ALLOW_BOOK_NAME.match(m2.group(1)) and len(m2.group(1)) >= 2:
        return m2.group(1)
    # ② 剥离：括号注记（男）（女）→ 指令词 → 剧情转折词截断 → 标点清理
    t = re.sub(r"[（(][^（()）]{0,6}[)）]", "", s)
    for _ in range(3):  # 前缀指令词可能是"写文，写一篇"复合结构，循环剥离
        t = re.sub(r"^[，,、\s：:]+", "", t)
        t2 = _TITLE_PREFIX_RE.sub("", t)
        if t2 == t:
            break
        t = t2
    t = re.sub(r"^(?:题目|书名|标题|内容是|内容为|设定|题材|剧情|大概|就是|关于)", "", t)
    t = _TITLE_CUT_RE.split(t)[0]
    t = re.split(r"[。！？；，、\n]", t)[0]
    t = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9\-]", "", t)[:16]
    if len(t) >= 2 and ALLOW_BOOK_NAME.match(t):
        return t
    # ③ 兜底：任意连续的 4-8 个中文字符（再次清洗保证合法）
    m2 = re.search(r"[\u4e00-\u9fa5A-Za-z0-9\-]{4,12}", t or s)
    if m2:
        cand2 = m2.group(0)[:16]
        if ALLOW_BOOK_NAME.match(cand2):
            return cand2
    return None


async def exec_action(action: str, param: str = "", current_book: str = "",
                      progress=None, ref: str = "", user_id: str = "",
                      is_admin: bool = False) -> str:
    """执行写作动作并返回回执文本。

    progress: 可选异步回调 progress(msg) —— 逐阶段向用户反馈（"正在写第N章…"等）。
    ref: 可选参考材料（用户要求"参考 X"时的搜索结果/资料，注入生成提示词）。
    is_admin: 调用方身份（管理员可操作任何书；2026-09-07 P1② 所有权校验）。
    """
    import asyncio as _a

    async def _say(msg: str):
        if progress is not None:
            try:
                await progress(msg)
            except Exception:  # noqa: BLE001
                pass

    def _guard(book: str) -> str:
        """所有权校验；拒绝时清掉回退扫描误绑的指针。返回拒绝提示或空串。"""
        deny = _claim_or_check(book, user_id, is_admin)
        if deny:
            _forget_book(user_id)
        return deny or ""

    if action == "start":
        setting = param.strip()
        if not setting:
            return "写作指令缺少设定内容，请补充题材。"
        title = _derive_book_title(setting)
        if not title:
            return "书名只能包含中文/字母/数字/短横线（≤24 字），换个短一点的名字吧。"
        logger.info("fiction book title derived: {!r} <- {!r}", title, setting[:60])
        book = title
        if _load_meta(book):
            # 2026-09-07 P1②：标题碰撞曾是劫持路径之一（他人书被绑进攻击者指针）
            _deny = _guard(book)
            if _deny:
                return _deny
            _remember_book(book, user_id)
            return f"《{book}》已存在，接着写——说「续写」即可，或直接说想改哪章。"
        await _a.sleep(0)
        await _say("（大纲起草中…）")
        outline = await asyncio_gen_outline(setting)
        meta = {
            "title": book, "setting": setting, "characters": "",
            "outline": outline, "created": time.strftime("%Y-%m-%d %H:%M"), "chapters": [],
            "owner": str(user_id or ""),  # 2026-09-07 P1②：开书即认领
        }
        # 2026-08-27：开书设定带「三章/N章」→ 记录章节上限（续写自动守住）
        _cap0 = _parse_cap(setting)
        if _cap0:
            meta["chapter_cap"] = _cap0
        _save_meta(book, meta)
        _remember_book(book, user_id)
        await _say(f"（大纲已拟好（{len([l for l in outline.splitlines() if l.strip()])} 章要点），正在写第一章…）")
        body, summary, beats = await asyncio_gen_chapter(meta, ref)
        n = len(_chapter_list(meta)) + 1
        _write_chapter(book, n, body)
        meta["chapters"].append({"n": n, "summary": summary})
        meta.setdefault("beats", {})[str(n)] = beats
        if ref:
            meta.setdefault("refs", []).append(await asyncio_compress_ref(ref))
        _save_meta(book, meta)
        _cap_note = f"（已按你的要求固定为 {int(meta['chapter_cap'])} 章，继续写会拒绝超额）" if meta.get("chapter_cap") else ""
        return (
            f"《{book}》第 1 章完成 ✅\n【摘要】{summary}\n（大纲 {len([l for l in outline.splitlines() if l.strip()])} 章要点，说「继续」接着写）{_cap_note}"
        )
    if action == "delete":
        n = _parse_chap(param) if param else None
        book = current_book or _last_book(user_id)
        if not book:
            return "还没有在写的书。"
        meta = _load_meta(book)
        if not meta:
            return "《%s》的数据丢了。" % book
        _deny = _guard(book)
        if _deny:
            return _deny
        if n is None:
            return f"要删哪一章？现在共 {len(_chapter_list(meta))} 章。"
        if n not in _chapter_list(meta):
            return f"第 {n} 章不存在（目前已写到 {len(_chapter_list(meta))} 章）。"
        await _say(f"（删除第 {n} 章并重排后续章节…）")
        _delete_chapter(book, meta, n)
        _save_meta(book, meta)
        return f"第 {n} 章已删除 ✅（剩余 {len(_chapter_list(meta))} 章已重新编号）"
    if action == "outline":
        book = current_book or _last_book(user_id)
        if not book:
            return "还没有在写的书——说个题材即可开笔。"
        meta = _load_meta(book)
        if not meta:
            return "《%s》的数据丢了，重新说个题材再开一本吧。" % book
        _deny = _guard(book)
        if _deny:
            return _deny
        return await _do_outline(meta, book, param, ref, _say)

    if action == "show":
        book = current_book or _last_book(user_id)
        if not book:
            return "还没有在写的书——说个题材即可开笔。"
        meta = _load_meta(book)
        if not meta:
            return "《%s》的数据丢了，重新说个题材再开一本吧。" % book
        _deny = _guard(book)
        if _deny:
            return _deny
        done = _chapter_list(meta)
        sum_lines = "\n".join(f"· 第{c['n']}章：{c.get('summary','')}" for c in meta["chapters"][-5:]) or "（还没有章节）"
        return (
            f"《{book}》进度：已写 {len(done)} 章\n"
            f"=== 大纲 ===\n{meta.get('outline','')}\n"
            f"=== 最近章节摘要 ===\n{sum_lines}\n"
            f"说「继续」接着写，或「第 N 章改成…」"
        )
    if action == "continue":
        book = current_book or _last_book(user_id)
        if not book:
            return "还没有在写的书——先说个题材即可开笔。"
        meta = _load_meta(book)
        if not meta:
            return "《%s》的数据丢了，重新说个题材再开一本吧。" % book
        _deny = _guard(book)  # 2026-09-07 P1②：续写是回退扫描劫持的主路径，校验在绑指针之前
        if _deny:
            return _deny
        _remember_book(book, user_id)
        # 2026-08-27：continue 支持目标章节（「继续完成第二章」→ 完成/重写第2章，而非追加新章）
        m_c = re.search(r"第\s*([0-9一二三四五六七八九十]{1,3})\s*章", param or "")
        if m_c:
            want = _parse_chap(m_c.group(1))
            if not want:
                return "章节号没看明白，说「继续完成第N章」试试。"
            if want in _chapter_list(meta):
                req2 = re.sub(r"^\s*第?\s*[0-9一二三四五六七八九十]{1,3}\s*章", "", (param or "")).strip()
                if not req2:
                    req2 = "整体润色，更贴合角色与剧情"
                if cap := meta.get("chapter_cap"):
                    if want > int(cap):
                        return f"《{meta['title']}》已固定在 {int(cap)} 章，第 {want} 章不存在。要加章说「取消章节上限」。"
                await _say(f"（正在完善第 {want} 章：{req2[:40]}…）")
                out = await asyncio_rewrite(meta, book, want, req2, ref)
                if out is None:
                    return "第 %d 章读取失败。" % want
                body, summary, beats = out
                _write_chapter(book, want, body)
                for c in meta["chapters"]:
                    if c["n"] == want:
                        c["summary"] = summary
                meta.setdefault("beats", {})[str(want)] = beats
                meta.setdefault("refs", []).append((req2 or "").strip()[:200])
                if ref:
                    meta.setdefault("refs", []).append(await asyncio_compress_ref(ref))
                _save_meta(book, meta)
                return f"第 {want} 章已按要求完成 ✅\n【新摘要】{summary}"
            if want > len(_chapter_list(meta)) + 1:
                return f"第 {want} 章还没写到（目前已写到 {len(_chapter_list(meta))} 章）——按顺序继续即可。"
            # want == len+1：按目标章续写（走下方追加逻辑，nxt 一致）
        # 追加新章（含 cap 约束）
        cap = meta.get("chapter_cap")
        nxt = len(_chapter_list(meta)) + 1
        if cap and nxt > int(cap):
            return (f"《{meta['title']}》已按要求固定在 {int(cap)} 章 ✅\n"
                    "要继续写说「取消章节上限」；改内容说「改第N章…」；导出说「完整版发给我」。")
        await _say(f"（正在写第 {nxt} 章…）")
        body, summary, beats = await asyncio_gen_chapter(meta, ref)
        n = len(_chapter_list(meta)) + 1
        _write_chapter(book, n, body)
        meta["chapters"].append({"n": n, "summary": summary})
        meta.setdefault("beats", {})[str(n)] = beats
        if ref:
            meta.setdefault("refs", []).append(await asyncio_compress_ref(ref))
        _save_meta(book, meta)
        cap_note = f"（目标 {int(cap)} 章）" if cap else ""
        return f"《{book}》第 {n} 章完成 ✅\n【摘要】{summary}\n（已写 {n} 章{cap_note}，说「继续」接着写）"
    if action == "edit":
        n, req = _parse_edit_param(param)
        # 工具兜底：要求里含"大纲"（模型误判为 edit）→ 转执行大纲修改
        if "大纲" in (req or ""):
            book2 = current_book or _last_book(user_id)
            if book2:
                meta2 = _load_meta(book2)
                if meta2:
                    _deny2 = _guard(book2)
                    if _deny2:
                        return _deny2
                    return await _do_outline(meta2, book2, req, ref, _say)
        book = current_book or _last_book(user_id)
        if not book:
            return "还没有在写的书。"
        meta = _load_meta(book)
        if not meta:
            return "《%s》的数据丢了。" % book
        _deny = _guard(book)
        if _deny:
            return _deny
        # 2026-08-27 结构性要求（固定N章/删掉多余）：工具层直接截断执行
        _cap = _parse_cap(req)
        if _cap:
            removed, note = _apply_cap(book, meta, _cap)
            _save_meta(book, meta)
            logger.info("struct cap: book=%s cap=%d removed=%d", book, _cap, removed)
            if n is None or n not in _chapter_list(meta) or n > _cap:
                return f"已按你的要求固定为 {_cap} 章 ✅{note}（说「导出」可发文件）"
            # 指定了 n 且 n <= cap：截断后继续重写该章（下方流程）
            meta = _load_meta(book)
        elif _TRIM_RE.search(req or "") and meta.get("chapter_cap"):
            # "删掉多余的/去掉多余"：按当前上限截断
            _cap2 = int(meta.get("chapter_cap"))
            removed2, note2 = _apply_cap(book, meta, _cap2)
            _save_meta(book, meta)
            logger.info("struct trim: book=%s cap=%d removed=%d", book, _cap2, removed2)
            return f"已删除多余章节，保留 {_cap2} 章 ✅{note2}"
        elif _TRIM_RE.search(req or "") and not meta.get("chapter_cap"):
            return "要删减章节的话，说「固定为 N 章」即可（如：固定为三章）。"
        if n is None:
            # 模型没提章节 → 按理解默认改最近一章（后续用户可继续指明修正）
            n = len(_chapter_list(meta)) or 1
        if n not in _chapter_list(meta):
            return f"第 {n} 章还不存在（目前已写到 {len(_chapter_list(meta))} 章）。"
        await _say(f"（正在重写第 {n} 章：{req[:40]}…）")
        out = await asyncio_rewrite(meta, book, n, req, ref)
        if out is None:
            return "第 %d 章读取失败。" % n
        body, summary, beats = out
        _write_chapter(book, n, body)
        for c in meta["chapters"]:
            if c["n"] == n:
                c["summary"] = summary
        meta.setdefault("beats", {})[str(n)] = beats
        # 2026-08-26：用户补充的剧情设定持久化（跨次编辑不丢，续写/后续修改持续遵守）
        meta.setdefault("refs", []).append((req or "").strip()[:200])
        if ref:
            meta.setdefault("refs", []).append(await asyncio_compress_ref(ref))
        # 同步大纲：对应行更新为新剧情摘要（防止后续续写参照旧大纲漂移）
        lines = [ln for ln in meta.get("outline", "").splitlines() if ln.strip()]
        if n <= len(lines):
            old_line = lines[n - 1]
            lines[n - 1] = f"{n:02d}：{summary}"
            meta["outline"] = "\n".join(lines)
            logger.info("outline synced: ch{} {!r} -> {!r}", n, old_line[:24], lines[n - 1][:24])
        _save_meta(book, meta)
        return f"第 {n} 章已改写 ✅\n【新摘要】{summary}\n（大纲已同步）"
    return "未知写作动作。"
