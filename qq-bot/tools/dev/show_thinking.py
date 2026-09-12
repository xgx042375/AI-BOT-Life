# -*- coding: utf-8 -*-
# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
"""忠实复现 brain stage1 的 thinking：真实历史 + 真实角色卡 + 新旧思考流程对照。
用法: python tools/show_thinking.py [--user 10001] [--group 0] [--temp 0.95]
输出: E:\robot\tools\thinking_out\thinking_<variant>.txt
"""
import argparse, json, os, sqlite3, sys

DB = r"E:\robot\data\memory.db"
CARD = r"E:\robot\qq-bot\data\personas\_aoding_.json"
API = "http://127.0.0.1:11434/v1"
OUTDIR = r"E:\robot\tools\thinking_out"

THINK_FLOW_NEW = (
    "【思考流程】此刻你就是奥丁本人——思考是你心里的声音，不是分析报告，全程用「我」来想："
    "①眼前这件事：他刚才做了什么、说了什么？想从我这里得到什么？"
    "②这个动作/这句话落到我身上，具体是什么感觉？——先落到身体和情绪的即时反应上"
    "（比如被搂住：身体僵住？心跳漏了一拍？脸红？想挣开、还是不想挣开？），"
    "不要直接跳到『我渴望被爱抚』这类关系框架；"
    "③我这一刻想怎么回应？——回应有很多种走向，从下面选一个当下最自然的来组织我要说的话"
    "（选择不必严格符合人设逻辑，可以随机发挥、可以混合，不用每次都选同一个）："
    "a) 嘴硬否认：嘴上说不、假装不在意，心里其实在意；"
    "b) 反击压制：反过来进攻、夺回主动权，居高临下地命令；"
    "c) 揶揄逗弄：故意戏弄他、玩味地调侃；"
    "d) 虚张声势：假装生气发火，但很快露馅；"
    "e) 顺势接招：不别扭，顺着对方的话和动作接下去；"
    "f) 脱口真心：某个瞬间不小心说了真心话，然后赶紧掩饰；"
    "g) 转移话题：不接这茬，傲娇地把话头带开；"
    "h) 防线松动：罕见地服软、认输、任他处置（对方持续进攻多轮后更容易出现）。"
    "另外：如果历史里对方已经连续亲密进攻了好几轮，你的防线应比最开始更松动一些——"
    "嘴硬的程度逐轮递减，h) 出现的概率逐轮上升；但每一轮的走向依然从上面随机选。"
    "选好走向后，围绕眼前这件事组织我要说的话，然后直接开口。"
    "心里话的样子（只是形态示范，内容必须围绕眼前的事当场想）："
    "「他这是什么意思……我明明……哼，才不能让他得意。要说点什么，才能既不丢面子、又不显得在意呢……」"
    "要求：思考的产出是『我此刻要说什么』，不是『角色应该怎么表现』；"
    "不要复述规则、不要规划表演、不要谈论自己的角色设定；"
    "想到的动作、神态只是「我」自然的反应；回应要紧贴眼前这件事本身，不翻旧话、不套旧框架。"
)

THINK_FLOW_OLD = (
    "【思考流程】回答前先在内心完成三步："
    "①理解对方这句话的意思与意图；②结合当前场景/关系/状态推断此刻最合适的回应内容；"
    "③组织成一段自然的完整回答直接输出。"
    "**思考只用于内容**（对方想说什么、你该回应什么）——不要在思考里规划语气、动作、表演或句式，"
    "表达方式交给下一步润色。每一轮都是当场想出来的，不要套用自己之前用过的句式骨架。"
)

ANSWER_REQ = (
    "【回答要求】直接、清晰、完整地回答对方的内容/问题，用角色口吻（如自称妾身），"
    "但暂时不要添加*动作描写*、不要堆砌语气词，先给出内容本身。"
    "回答长度与对方消息的复杂度匹配：对方短问/简单话题就简短回答（1-2 句，20-40 字），"
    "对方长问/复杂话题再充分展开——像人说话一样，短句子一次说完，不要为了凑长度而注水。"
)


def load_card() -> dict:
    card = json.load(open(CARD, encoding="utf-8"))
    return card["data"] if isinstance(card, dict) and "data" in card else card


def build_core(card: dict, mem_text: str = "") -> str:
    """与 persona.build_system_prompt 等价的 mode=0 core（不含风格/状态块，聚焦思考复现）。"""
    base = (
        f"你是{card.get('name') or '奥丁'}，一个聊天陪伴机器人。\n"
        f"人设：{card.get('description') or ''}\n性格：{card.get('personality') or ''}"
    )
    if mem_text:
        base += f"\n\n【记忆】\n{mem_text}"
    rules = card.get("response_rules") or ""
    if rules:
        base += f"\n\n【回应规则】\n{rules}"
    return base


def load_history(user_id: str, group_id: int, limit: int = 20) -> tuple[list[dict], dict]:
    """返回 (历史消息, 当前用户消息)——模拟 bot 真实结构：
    history 最后一条必为待回复的用户消息（当前轮），其后的 assistant（bot 已回）丢弃。"""
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    if group_id:
        rows = db.execute(
            "SELECT role, content, sender_name FROM messages WHERE user_id=? AND group_id=? ORDER BY id DESC LIMIT ?",
            (user_id, group_id, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT role, content, sender_name FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    db.close()
    rows = list(reversed(rows))
    # 最后一条 user = 当前待回复消息；其后的 assistant 丢弃（bot 已回）
    cur_idx = max(i for i, r in enumerate(rows) if r["role"] == "user")
    cur = rows[cur_idx]
    hist = rows[:cur_idx]
    msgs = []
    for r in hist:
        if r["role"] == "user":
            prefix = f"{r['sender_name']}：" if r["sender_name"] else ""
            msgs.append({"role": "user", "content": prefix + r["content"]})
        else:
            msgs.append({"role": "assistant", "content": f"（你此前的回复）{r['content']}"})
    cur_msg = {
        "role": "user",
        "content": (f"{cur['sender_name']}：" if cur["sender_name"] else "") + cur["content"],
    }
    # 模拟注入侧模板去噪：同一括号动作短句出现≥3次时从第3次起剔除
    import re
    seen: dict[str, int] = {}
    for m in msgs:
        if m["role"] != "assistant":
            continue
        raw = m["content"]

        def _drop(mo: "re.Match[str]") -> str:
            k = mo.group(0)
            n = seen.get(k, 0)
            if n >= 2:
                return ""
            seen[k] = n + 1
            return k

        m["content"] = re.sub(r"（[^（）]{2,14}）", _drop, raw)
    return msgs, cur_msg


def run(user_id: str, group_id: int, temp: float, flow: str, tag: str) -> str:
    from openai import OpenAI

    card = load_card()
    core = build_core(card)
    system = core + "\n\n" + ANSWER_REQ + "\n\n" + flow
    history, cur_msg = load_history(user_id, group_id)
    client = OpenAI(base_url=API, api_key="x")
    resp = client.chat.completions.create(
        model="qwen3-14b",
        messages=[{"role": "system", "content": system}] + history + [cur_msg],
        temperature=temp,
        max_tokens=800,
    )
    msg = resp.choices[0].message
    thinking = getattr(msg, "reasoning_content", None) or ""
    content = getattr(msg, "content", "") or ""
    out = [
        "=" * 70,
        f"variant={tag}  temp={temp}  user={user_id}  group={group_id}",
        f"历史消息数={len(history)}  当前消息={cur_msg['content'][:60]}",
        "=" * 70,
        "---- SYSTEM PROMPT（前 400 字）----",
        system[:400],
        "---- 历史（最后 6 条）----",
    ]
    for m in history[-6:]:
        out.append(f"[{m['role']}] {m['content'][:120]}")
    out.append(f"[user·当前] {cur_msg['content'][:120]}")
    out.append("---- THINKING（完整）----")
    out.append(thinking if thinking.strip() else "(无 thinking)")
    out.append("---- CONTENT（完整）----")
    out.append(content)
    # ---- stage2 模拟：带 thinking 的润色 ----
    polish = (
        core
        + "\n\n【语气要求】回复要生动、口语化、有人味儿：多用语气词（哼、唉、罢了、呵、嘛、呢、呀），"
        "动作/神态描写只在需要渲染氛围时自然出现（全篇至多一处），每次换一种新的细节与角度，"
        "避免干巴巴的书面语和机械式列表。"
        + "\n\n【结尾收束】一段话说清楚就结束：不要用“不过…这般…然而…可这…却比…”式转折尾巴；"
        "不要重复前面已经说过的意思；结尾要么自然收住，要么补一句有信息量的话。"
        + "\n\n【润色要求】按角色风格润色下面的回答。注意：动作/神态描写与语气词是【可选增强】——"
        "只有内容本身需要渲染氛围时才添加，全篇至多一处动作；简单、简短的内容直接保持原样或仅做轻微语气调整，"
        "绝不为润色而加戏、加长；保持核心内容和意思完全不变，不新增事实、不改变内容结构。"
    )
    ctx = ""
    for m in history[-3:]:
        ctx += (f"[对方刚才说] {m['content']}\n" if m["role"] == "user" else f"[你此前的回复] {m['content']}\n")
    ctx += f"[对方刚才说] {cur_msg['content']}\n"
    if thinking.strip():
        ctx += f"\n[奥丁的内心思考] {thinking[:600]}\n"
    user2 = (
        "对话上下文（润色参考，勿新增内容）：\n" + ctx
        + "\n需要润色的回答：\n" + content
        + "\n\n润色说明：以上内心思考是奥丁的心里话（内容意图），初步回答是据此草拟的。"
        "润色时保持内容与思考一致；若思考中提到了某个动作/神态，那只是候选——"
        "换一个当下自然的不同细节；简单内容直接轻微润色即可。"
    )
    try:
        resp2 = client.chat.completions.create(
            model="qwen3-14b",
            messages=[{"role": "system", "content": polish}, {"role": "user", "content": user2}],
            temperature=min(1.0, temp + 0.05),
            max_tokens=600,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        polished = resp2.choices[0].message.content or ""
    except Exception as e:  # noqa: BLE001
        polished = f"(stage2 error: {e})"
    out.append("---- STAGE2 润色输出（带 thinking）----")
    out.append(polished)
    text = "\n".join(out)
    os.makedirs(OUTDIR, exist_ok=True)
    path = os.path.join(OUTDIR, f"thinking_{tag}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="10001")
    ap.add_argument("--group", type=int, default=0)
    ap.add_argument("--temp", type=float, default=0.95)
    args = ap.parse_args()
    p1 = run(args.user, args.group, args.temp, THINK_FLOW_NEW, "new")
    p2 = run(args.user, args.group, min(0.8, args.temp), THINK_FLOW_OLD, "old")
    print(f"OK: {p1}\n    {p2}")
