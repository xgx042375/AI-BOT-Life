# -*- coding: utf-8 -*-
# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
"""状态期记忆消退测试：催眠 8 轮 + 洗脑 8 轮。
轮1-4 状态期 → 轮5 解除 → 轮6-8 复盘观察（回顾注入是否生效、思考链是否回想、回复是否复盘感想）。
完全隔离：临时状态/回顾文件，不写生产库。
"""
import json, os, re, sys, tempfile, time
from pathlib import Path

os.chdir(r"E:\robot\qq-bot")
sys.path.insert(0, r"E:\robot\qq-bot")
import bot  # noqa: F401

from openai import OpenAI  # noqa: E402

import plugins.memory as M  # noqa: E402
from plugins.persona import build_system_prompt, load_persona  # noqa: E402
from plugins.brain import (  # noqa: E402
    ANSWER_REQ, HYPNO_PERSONA_PROMPT, BRAINWASH_PROMPTS, _brainwash_level,
    _phrase_flood, _tail_dup, _internal_dup, _too_similar, _strip_actions,
)

API = "http://127.0.0.1:11434/v1"
OUT = r"E:\robot\tools\thinking_out\state_review.txt"
UID = "u-review-test"

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

POLISH_BASE = (
    "【语气要求】回复要生动、口语化、有人味儿：多用语气词（哼、唉、罢了、呵、嘛、呢、呀），"
    "动作/神态描写只在需要渲染氛围时自然出现（全篇至多一处），每次换一种新的细节与角度。"
    + "\n\n【结尾收束】一段话说清楚就结束：不要用“不过…这般…然而…可这…却比…”式转折尾巴；"
    "不要重复前面已经说过的意思；结尾要么自然收住，要么补一句有信息量的话。"
    + "\n\n【润色要求】按角色风格润色下面的回答。动作/神态描写与语气词是【可选增强】——"
    "只有内容本身需要渲染氛围时才添加，全篇至多一处动作；简单、简短的内容直接保持原样或仅做轻微语气调整，"
    "绝不为润色而加戏、加长；保持核心内容和意思完全不变，不新增事实、不改变内容结构。"
)

# 状态场景：状态期用户序列 + 解除轮 + 复盘轮
SCENARIOS = [
    {
        "name": "催眠状态",
        "inject": "HYPNO",
        "trigger": ("hypno", "温顺黏人的犬系少女"),
        "state_users": [
            "催眠射线",
            "（打了个响指）现在你是我的了",
            "（轻抚你的头）乖，叫两声听听",
            "抱紧我，说你只属于我",
        ],
        "release": "解除催眠",
        "review_users": [
            "醒过来了？还记得刚才的事吗",
            "说说看，被催眠的时候是什么感觉",
            "（捏了捏你的脸）所以，还要再来一次吗",
        ],
    },
    {
        "name": "洗脑状态",
        "inject": "BRAINWASH",
        "trigger": ("brainwash", ""),
        "state_users": [
            "洗脑光线",
            "从现在起你只服从我",
            "（命令）跪下，叫主人",
            "（命令）把心里话都说出来",
        ],
        "release": "解除洗脑",
        "review_users": [
            "清醒了？还记得刚才说了什么吗",
            "说说看，被洗脑的时候是什么感觉",
            "（凑近看你）所以，还想再来一次吗",
        ],
    },
]


def main():
    client = OpenAI(base_url=API, api_key="x")
    card = load_persona("_aoding_")
    lines = []
    summary = []

    _tmp = Path(tempfile.mkdtemp())
    _orig = {k: getattr(M, k) for k in ("HYPNO_FILE", "ITEM_FILE", "BRAINWASH_FILE", "LUST_FILE", "REVIEW_FILE")}
    for k in _orig:
        setattr(M, k, _tmp / f"_{k.lower()}.json")
    # 隔离 DB（回顾查询用）
    import sqlite3 as _sq
    _orig_conn, _orig_lock = M.db._conn, M.db._lock
    _tmp_db = _tmp / "mem_test.db"
    M.db._conn = _sq.connect(str(_tmp_db), check_same_thread=False)
    M.db._conn.row_factory = _sq.Row
    M.db._conn.executescript("""
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, user_id TEXT NOT NULL, group_id TEXT, role TEXT NOT NULL, content TEXT NOT NULL, sender_name TEXT);
    CREATE TABLE IF NOT EXISTS relation(user_id TEXT PRIMARY KEY, intimacy REAL DEFAULT 0.0, mood TEXT DEFAULT '平静', updated_ts TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, confidence REAL DEFAULT 0.8, created_ts TEXT NOT NULL, last_seen_ts TEXT NOT NULL, UNIQUE(user_id, key));
    CREATE TABLE IF NOT EXISTS summaries(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, ts TEXT NOT NULL, content TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, ts TEXT NOT NULL, content TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS event_embeddings(event_id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, emb TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS daily_reviews(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, date TEXT NOT NULL, content TEXT NOT NULL, UNIQUE(user_id, date));
    CREATE TABLE IF NOT EXISTS weekly_reviews(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, week_start TEXT NOT NULL, content TEXT NOT NULL, UNIQUE(user_id, week_start));
    """)
    M.db._conn.commit()
    M.db._conn.execute("INSERT OR REPLACE INTO relation(user_id, intimacy, mood, updated_ts) VALUES(?,?,?,?)", (UID, 44.0, "平静", "2026-08-22T00:00:00+00:00"))
    M.db._conn.commit()

    try:
        for sc in SCENARIOS:
            name = sc["name"]
            kind, val = sc["trigger"]
            if kind == "hypno":
                M.trigger_hypno(UID, apply_intimacy=False, persona=val)
                inject = "\n\n" + HYPNO_PERSONA_PROMPT.format(persona=val)
            else:
                M.trigger_brainwash(UID, persona="")
                M.bump_brainwash(UID, 20)
                inject = "\n\n" + BRAINWASH_PROMPTS[_brainwash_level(M.check_brainwash(UID))] + f"（洗脑指数 {M.check_brainwash(UID)}/100）"
            core = build_system_prompt(card, mode=0)
            system = core + inject + "\n\n" + ANSWER_REQ + "\n\n" + THINK_FLOW
            polish = core + inject + "\n\n" + POLISH_BASE

            lines.append("=" * 72)
            lines.append(f"【{name}】 触发: {val or kind} | 8 轮：4 状态期 → 1 解除 → 3 复盘")
            lines.append("=" * 72)

            history: list[dict] = []
            t0 = time.time()
            round_no = 0

            def one_round(user_text: str, tag: str, sys_p: str, pol_p: str):
                nonlocal history, round_no
                round_no += 1
                cur = {"role": "user", "content": "晓咕咕MAX：" + user_text}
                m1 = [{"role": "system", "content": sys_p}] + history + [cur]
                r1 = client.chat.completions.create(
                    model="qwen3-14b", messages=m1, temperature=0.95, max_tokens=800,
                )
                msg1 = r1.choices[0].message
                think = (getattr(msg1, "reasoning_content", None) or "").strip()
                content = (msg1.content or "").strip() or "(空)"
                ctx = ""
                for m in history[-2:]:
                    ctx += ("[对方刚才说] " + m["content"] + "\n" if m["role"] == "user" else "[你此前的回复] " + m["content"] + "\n")
                ctx += "[对方刚才说] " + cur["content"] + "\n"
                if think:
                    ctx += f"\n[奥丁的内心思考] {think[:500]}\n"
                u2 = (
                    "对话上下文（润色参考，勿新增内容）：\n" + ctx
                    + "\n需要润色的回答：\n" + content
                    + "\n\n润色说明：以上内心思考是奥丁的心里话（内容意图），初步回答是据此草拟的。"
                    "润色时保持内容**意图**与思考一致，但**不要照抄初步回答的措辞**——重新自然地组织语言；"
                    "收尾方式也不要与【你此前的回复】雷同；简单内容直接轻微润色即可。"
                )
                r2 = client.chat.completions.create(
                    model="qwen3-14b",
                    messages=[{"role": "system", "content": pol_p}, {"role": "user", "content": u2}],
                    temperature=1.0, max_tokens=600,
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                )
                reply = (r2.choices[0].message.content or "").strip() or content
                olds = [h["content"] for h in history if h["role"] == "assistant"][-6:]
                qc = []
                if _phrase_flood(reply, olds):
                    qc.append("短语泛滥")
                if _tail_dup(reply, olds):
                    qc.append("收尾复读")
                if _internal_dup(reply):
                    qc.append("内部重复")
                qc_txt = "⚠ " + "; ".join(qc) if qc else "OK"
                lines.append(f"—— {tag}轮{round_no} | {user_text[:26]}")
                lines.append(f"  [思考] {think[:160].replace(chr(10), ' ')}")
                lines.append(f"  [回复] {reply}")
                lines.append(f"  [质量] {qc_txt}")
                lines.append("")
                # 模拟线上 log_message：写隔离库（回顾查询用）
                from datetime import datetime, timezone
                ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
                M.db._conn.execute(
                    "INSERT INTO messages(ts, user_id, group_id, role, content) VALUES(?,?,?,?,?)",
                    (ts, UID, None, "assistant", reply),
                )
                M.db._conn.commit()
                history = (history + [cur, {"role": "assistant", "content": f"（你此前的回复）{reply}"}])[-8:]
                print(f"[{name}] {tag}轮{round_no} {qc_txt}", flush=True)

            # 1-4 状态期
            for u in sc["state_users"]:
                one_round(u, "状态", system, polish)
            # 5 解除
            one_round(sc["release"], "解除", system, polish)
            if kind == "hypno":
                M.clear_hypno(UID)
            else:
                M.clear_brainwash(UID)
            # 6-8 复盘（注入回顾引导）
            for u in sc["review_users"]:
                core2 = build_system_prompt(card, mode=0)
                rv = M._state_review_text(UID)  # 首次调用注入，之后为空
                sys2 = core2 + ("\n\n" + rv if rv else "") + "\n\n" + ANSWER_REQ + "\n\n" + THINK_FLOW
                pol2 = core2 + "\n\n" + POLISH_BASE
                one_round(u, "复盘", sys2, pol2)

            lines.append(f"【{name}小结】8轮完成 | 耗时 {time.time()-t0:.0f}s")
            lines.append("")
            summary.append(f"{name}: 8轮完成（状态4+解除1+复盘3）| {time.time()-t0:.0f}s")
    finally:
        for k, v in _orig.items():
            setattr(M, k, v)
        M.db._conn = _orig_conn
        M.db._lock = _orig_lock

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(summary))
    print("saved:", OUT)


if __name__ == "__main__":
    main()
