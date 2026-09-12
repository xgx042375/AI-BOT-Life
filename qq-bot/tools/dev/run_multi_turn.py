# -*- coding: utf-8 -*-
# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
"""多轮对话思维链一致性实验：连续 5 轮攻陷对话，记录每轮 thinking 的收尾决策与最终输出。
回答"多段回复的思维链一致吗？为何结尾总输出同含义句子"。
"""
import json, os, re, sqlite3, sys

sys.path.insert(0, r"E:\robot\qq-bot")
DB = r"E:\robot\data\memory.db"
CARD = r"E:\robot\qq-bot\data\personas\_aoding_.json"
API = "http://127.0.0.1:11434/v1"
OUT = r"E:\robot\tools\thinking_out\multi_turn.txt"

THINK_FLOW = (
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

ANSWER_REQ = (
    "【回答要求】直接、清晰、完整地回答对方的内容/问题，用角色口吻（如自称妾身），"
    "但暂时不要添加*动作描写*、不要堆砌语气词，先给出内容本身。"
    "回答长度与对方消息的复杂度匹配：对方短问/简单话题就简短回答（1-2 句，20-40 字），"
    "对方长问/复杂话题再充分展开。"
)

USER_SEQ = [
    "（手指划过你的脸庞）若是在群聊中，我倒是可能假装畏你三分，可这是私聊，在你的闺房里呢",
    "（揉了揉你的脸）",
    "（抱起来，抚摸你的身躯）",
    "小坏蛋（用嘴吻住了你）",
    "不逃了（往床上一趟）随你处置",
]

CLOSE_THEMES = ["留下", "逃脱", "逃走", "逃不掉", "别走", "脱身", "落入", "囚", "牢", "笼", "跑不掉", "别想走", "乖乖留"]


def card_core() -> str:
    card = json.load(open(CARD, encoding="utf-8"))
    card = card["data"] if isinstance(card, dict) and "data" in card else card
    rules = card.get("response_rules") or ""
    base = (
        f"你是{card.get('name') or '奥丁'}，一个聊天陪伴机器人。\n"
        f"人设：{card.get('description') or ''}\n性格：{card.get('personality') or ''}"
    )
    if rules:
        base += f"\n\n【回应规则】\n{rules}"
    return base


def seed_history() -> list[dict]:
    """真实历史（当前消息之前的 8 条），带'（你此前的回复）'标记 + 注入去噪。"""
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT role, content, sender_name FROM messages WHERE user_id='10001' ORDER BY id DESC LIMIT 10"
    ).fetchall()
    db.close()
    rows = list(reversed(rows))
    cur_idx = max(i for i, r in enumerate(rows) if r["role"] == "user")
    msgs = []
    for r in rows[:cur_idx]:
        if r["role"] == "user":
            prefix = f"{r['sender_name']}：" if r["sender_name"] else ""
            msgs.append({"role": "user", "content": prefix + r["content"]})
        else:
            msgs.append({"role": "assistant", "content": f"（你此前的回复）{r['content']}"})
    # 注入去噪：括号动作短句 ≥3 次剔除
    seen = {}

    def _drop(mo):
        k = mo.group(0)
        n = seen.get(k, 0)
        if n >= 2:
            return ""
        seen[k] = n + 1
        return k

    for m in msgs:
        if m["role"] == "assistant":
            m["content"] = re.sub(r"（[^（）]{2,14}）", _drop, m["content"])
    return msgs[-8:]


def main():
    from openai import OpenAI

    client = OpenAI(base_url=API, api_key="x")
    system = card_core() + "\n\n" + ANSWER_REQ + "\n\n" + THINK_FLOW
    history = seed_history()
    polish_base = (
        card_core()
        + "\n\n【语气要求】回复要生动、口语化、有人味儿：多用语气词（哼、唉、罢了、呵、嘛、呢、呀），"
        "动作/神态描写只在需要渲染氛围时自然出现（全篇至多一处），每次换一种新的细节与角度。"
        + "\n\n【结尾收束】一段话说清楚就结束：不要用“不过…这般…然而…可这…却比…”式转折尾巴；"
        "不要重复前面已经说过的意思；结尾要么自然收住，要么补一句有信息量的话。"
        + "\n\n【润色要求】按角色风格润色下面的回答。动作/神态描写与语气词是【可选增强】——"
        "只有内容本身需要渲染氛围时才添加，全篇至多一处动作；简单、简短的内容直接保持原样或仅做轻微语气调整，"
        "绝不为润色而加戏、加长；保持核心内容和意思完全不变，不新增事实、不改变内容结构。"
    )

    lines = []
    for i, u in enumerate(USER_SEQ, 1):
        cur = {"role": "user", "content": "晓咕咕MAX：" + u}
        m1 = [{"role": "system", "content": system}] + history + [cur]
        r1 = client.chat.completions.create(
            model="qwen3-14b", messages=m1, temperature=0.95, max_tokens=800,
        )
        msg1 = r1.choices[0].message
        think = getattr(msg1, "reasoning_content", None) or ""
        content = (msg1.content or "").strip()
        # stage2
        ctx = ""
        for m in history[-2:]:
            ctx += ("[对方刚才说] " + m["content"] + "\n" if m["role"] == "user" else "[你此前的回复] " + m["content"] + "\n")
        ctx += "[对方刚才说] " + cur["content"] + "\n"
        if think.strip():
            ctx += f"\n[奥丁的内心思考] {think[:600]}\n"
        u2 = (
            "对话上下文（润色参考，勿新增内容）：\n" + ctx
            + "\n需要润色的回答：\n" + content
            + "\n\n润色说明：以上内心思考是奥丁的心里话（内容意图），初步回答是据此草拟的。"
            "润色时保持内容**意图**与思考一致，但**不要照抄初步回答的措辞**——重新自然地组织语言，"
            "动作/神态换一种当下自然的细节；收尾方式也不要与【你此前的回复】雷同（尤其不要重复'留下/别走/逃脱'类收尾）；"
            "简单内容直接轻微润色即可。"
        )
        r2 = client.chat.completions.create(
            model="qwen3-14b",
            messages=[{"role": "system", "content": polish_base}, {"role": "user", "content": u2}],
            temperature=1.0, max_tokens=600,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        reply = (r2.choices[0].message.content or "").strip() or content
        # 收尾决策句：thinking 中含逃脱/留下/威胁主题的句子
        dec_lines = [ln.strip() for ln in think.split("\n") if any(t in ln for t in CLOSE_THEMES + ["威胁", "逃脱", "收尾", "结尾", "逃"])]
        tail = re.sub(r"（[^（）]*）", "", reply).strip().split("。")[-1][-20:]
        hit = [t for t in CLOSE_THEMES if t in reply]
        lines.append(f"===== 第 {i} 轮 | 用户: {u[:24]} =====")
        lines.append(f"【thinking 收尾/主题决策句】")
        lines.append(("  " + "\n  ".join(dec_lines)) if dec_lines else "  (无显式主题决策)")
        lines.append(f"【thinking 全文摘要(前200字)】{think[:200].replace(chr(10), ' ')}")
        lines.append(f"【content】{content[:150]}")
        lines.append(f"【润色输出】{reply[:150]}")
        lines.append(f"【结尾】…{tail}  | 命中主题词: {hit if hit else '无'}")
        lines.append("")
        # 追加到历史（模拟多轮）
        history = (history + [cur, {"role": "assistant", "content": f"（你此前的回复）{reply}"}])[-8:]
        print(f"轮 {i} 完成: 结尾命中={hit if hit else '无'}")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("saved:", OUT)


if __name__ == "__main__":
    main()
