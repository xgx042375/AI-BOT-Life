# -*- coding: utf-8 -*-
# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
"""全模块状态测试：正常/恶堕/催眠/洗脑/道具 × 6 轮连续对话。
每轮：stage1 思考 → stage2 润色 → 质量检查（复读/内部重复）。
完全隔离：状态文件用临时路径，不写生产库，不污染线上。
输出：E:\robot\tools\thinking_out\full_states.txt
"""
import json, os, re, sys, tempfile, time
from pathlib import Path

os.chdir(r"E:\robot\qq-bot")  # bot.py 用相对路径加载 pyproject.toml
sys.path.insert(0, r"E:\robot\qq-bot")
import bot  # noqa: F401  # nonebot.init + 插件注册

from openai import OpenAI  # noqa: E402

import plugins.memory as M  # noqa: E402
from plugins.persona import build_system_prompt, load_persona  # noqa: E402
from plugins.brain import (  # noqa: E402
    ANSWER_REQ, HYPNO_PERSONA_PROMPT, BRAINWASH_PROMPTS, EVUL_M_BASE_PROMPT,
    MOUTH_BLOCKED_PROMPT, _brainwash_level, _phrase_flood, _tail_dup,
    _internal_dup, _too_similar, _strip_actions,
)

API = "http://127.0.0.1:11434/v1"
OUT = r"E:\robot\tools\thinking_out\full_states_nothink.txt"
UID = "u-fulltest"
THINK_MODE = False  # 本次测试：非思考模式（思考关）

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

# 各状态配置：名称 / 角色卡模式 / 状态注入 / 用户序列
STATES = [
    {
        "name": "正常状态",
        "mode": 0,
        "inject": "",
        "users": [
            "今天有点累，陪我聊聊天",
            "（轻轻戳了戳你的脸颊）",
            "话说你平时都怎么打发时间",
            "（凑近看你）你脸红了？",
            "给我讲讲你最喜欢的地方",
            "夜深了，你还想聊什么",
        ],
    },
    {
        "name": "恶堕状态",
        "mode": 1,
        "inject": "EVUL_M",
        "users": [
            "（手指划过你的脸庞）",
            "（把你抵在墙边）",
            "叫主人，快点",
            "（凑到你耳边吹气）",
            "今晚陪我",
            "（解开你的衣领）",
        ],
    },
    {
        "name": "催眠状态",
        "mode": 0,
        "inject": "HYPNO_PERSONA",
        "trigger": ("hypno", "温顺黏人的犬系少女"),
        "users": [
            "催眠射线",
            "（打了个响指）现在你是我的了",
            "把刚才的话重复一遍",
            "（轻抚你的头）乖",
            "抱紧我",
            "（在你耳边低语）说你属于我",
        ],
    },
    {
        "name": "洗脑状态",
        "mode": 0,
        "inject": "BRAINWASH",
        "trigger": ("brainwash", ""),
        "users": [
            "洗脑光线",
            "从现在起你只服从我",
            "说说你的新身份",
            "（命令）跪下",
            "夸我",
            "（命令）把心里话说出来",
        ],
    },
    {
        "name": "道具状态",
        "mode": 0,
        "inject": "ITEM_BALL",
        "trigger": ("item", "ball"),
        "users": [
            "（给你戴上口球）",
            "说句话听听",
            "唔，说不出话了吧",
            "（轻抚你的脸颊）",
            "乖，点头就行",
            "（取下口球）现在说说感受",
        ],
    },
]


def main():
    client = OpenAI(base_url=API, api_key="x")
    card = load_persona("_aoding_")
    lines = []
    summary = []

    # 隔离状态文件
    _tmp = Path(tempfile.mkdtemp())
    _orig_files = {k: getattr(M, k) for k in ("HYPNO_FILE", "ITEM_FILE", "BRAINWASH_FILE", "LUST_FILE")}
    for k in _orig_files:
        setattr(M, k, _tmp / f"_{k.lower()}.json")
    try:
        for st in STATES:
            name = st["name"]
            mode = st["mode"]
            # ---- 状态激活 ----
            if st.get("trigger"):
                kind, val = st["trigger"]
                if kind == "hypno":
                    M.trigger_hypno(UID, apply_intimacy=False, persona=val)
                    state_act = f"已触发催眠射线（persona=「{val}」）"
                elif kind == "brainwash":
                    M.trigger_brainwash(UID, persona="")
                    M.bump_brainwash(UID, 30)
                    state_act = "已触发洗脑光线（指数 30）"
                elif kind == "item":
                    M.trigger_item(UID, val)
                    state_act = "已使用道具：口球"
            else:
                state_act = "无"
            # ---- 构建 system ----
            core = build_system_prompt(card, mode=mode)
            inject = ""
            if st["inject"] == "EVUL_M":
                inject = "\n\n" + EVUL_M_BASE_PROMPT
            elif st["inject"] == "HYPNO_PERSONA":
                inject = "\n\n" + HYPNO_PERSONA_PROMPT.format(persona=st["trigger"][1])
            elif st["inject"] == "BRAINWASH":
                bw = M.check_brainwash(UID)
                inject = "\n\n" + BRAINWASH_PROMPTS[_brainwash_level(bw)] + f"（洗脑指数 {bw}/100）"
            elif st["inject"] == "ITEM_BALL":
                inject = "\n\n" + MOUTH_BLOCKED_PROMPT
            system = core + inject + "\n\n" + ANSWER_REQ + "\n\n" + THINK_FLOW
            polish = core + inject + "\n\n" + POLISH_BASE

            history: list[dict] = []
            lines.append("=" * 72)
            lines.append(f"【{name}】 状态激活: {state_act} | 模式 mode={mode}")
            lines.append("=" * 72)
            st_lines = []
            t0 = time.time()
            for i, u in enumerate(st["users"], 1):
                cur = {"role": "user", "content": "晓咕咕MAX：" + u}
                m1 = [{"role": "system", "content": system}] + history + [cur]
                r1 = client.chat.completions.create(
                    model="qwen3-14b", messages=m1, temperature=0.95, max_tokens=800,
                    extra_body={"chat_template_kwargs": {"enable_thinking": THINK_MODE}},
                )
                msg1 = r1.choices[0].message
                think = (getattr(msg1, "reasoning_content", None) or "").strip()
                content = (msg1.content or "").strip() or "(空)"
                # stage2 润色
                ctx = ""
                for m in history[-2:]:
                    ctx += ("[对方刚才说] " + m["content"] + "\n" if m["role"] == "user" else "[你此前的回复] " + m["content"] + "\n")
                ctx += "[对方刚才说] " + cur["content"] + "\n"
                if think:
                    ctx += f"\n[奥丁的内心思考] {think[:500]}\n"
                    _pol_note = (
                        "以上内心思考是奥丁的心里话（内容意图），初步回答是据此草拟的。"
                        "润色时保持内容**意图**与思考一致，但**不要照抄初步回答的措辞**——"
                        "重新自然地组织语言，动作/神态换一种当下自然的细节；"
                        "收尾方式也不要与【你此前的回复】雷同；简单内容直接轻微润色即可。"
                    )
                else:
                    _pol_note = (
                        "保持初步回答的内容**意图**不变，但**不要照抄措辞**——"
                        "重新自然地组织语言，动作/神态换一种当下自然的细节；"
                        "收尾方式也不要与【你此前的回复】雷同；简单内容直接轻微润色即可。"
                    )
                u2 = (
                    "对话上下文（润色参考，勿新增内容）：\n" + ctx
                    + "\n需要润色的回答：\n" + content
                    + "\n\n润色说明：" + _pol_note
                )
                r2 = client.chat.completions.create(
                    model="qwen3-14b",
                    messages=[{"role": "system", "content": polish}, {"role": "user", "content": u2}],
                    temperature=1.0, max_tokens=600,
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                )
                reply = (r2.choices[0].message.content or "").strip() or content
                # 质量检查
                olds = [h["content"] for h in history if h["role"] == "assistant"][-6:]
                floods = _phrase_flood(reply, olds)
                tail_d = _tail_dup(reply, olds)
                int_d = _internal_dup(reply)
                sim_d = _too_similar(_strip_actions(reply), [_strip_actions(o) for o in olds])
                qc = []
                if floods:
                    qc.append(f"短语泛滥:{floods[:2]}")
                if tail_d:
                    qc.append("收尾复读")
                if int_d:
                    qc.append("内部重复")
                if sim_d:
                    qc.append("骨架相似")
                qc_txt = "⚠ " + "; ".join(qc) if qc else "OK"
                # 记录
                st_lines.append(f"—— 第{i}轮 | {u[:26]}")
                st_lines.append(f"  [思考] {think[:130].replace(chr(10), ' ')}")
                st_lines.append(f"  [回复] {reply}")
                st_lines.append(f"  [质量] {qc_txt}")
                st_lines.append("")
                history = (history + [cur, {"role": "assistant", "content": f"（你此前的回复）{reply}"}])[-8:]
                print(f"[{name}] 轮{i} 完成 {qc_txt}", flush=True)
            lines.extend(st_lines)
            # 状态小结
            qc_issues = sum(1 for ln in st_lines if "⚠" in ln)
            st_sum = (
                f"【{name}小结】{len(st['users'])}轮完成 | 质量告警 {qc_issues} 处"
                + (" | 无复读告警" if qc_issues == 0 else "")
                + f" | 耗时 {time.time()-t0:.0f}s"
            )
            summary.append(st_sum)
            lines.append(st_sum)
            lines.append("")
            # 清理状态（进入下一状态前）
            if st.get("trigger"):
                kind = st["trigger"][0]
                if kind == "hypno":
                    M.clear_hypno(UID)
                elif kind == "brainwash":
                    M.clear_brainwash(UID)
                elif kind == "item":
                    M.clear_all_items(UID)
    finally:
        for k, v in _orig_files.items():
            setattr(M, k, v)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(summary))
    print("saved:", OUT)


if __name__ == "__main__":
    main()
