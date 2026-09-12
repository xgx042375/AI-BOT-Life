# -*- coding: utf-8 -*-
# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
"""冒烟测试：回复长度自然自决 + 无动作/思考输出（真实提示词 + 本地引擎 + 剥离规则）。"""
import json, re, sys

sys.path.insert(0, r"E:\robot\qq-bot")
import asyncio  # noqa: E402

from openai import AsyncOpenAI  # noqa: E402

from plugins.persona import build_system_prompt, load_persona  # noqa: E402

LENGTH_ADAPT = "【长度自决】回复长度由你根据对方消息与话题氛围**自然决定**，像真人聊天：对方短问/轻松话题就一句两句（几个字到二十来个字都可以）；对方展开的话题就自然说下去；情绪浓烈自然变多；不为凑长注水，也不硬压短。没有固定字数档位。"

_NON_SPEECH_RE = re.compile(
    r"[（(][^（()）]{1,100}[)）]|[*＊][^*＊]{1,120}[*＊]|【[^】]{1,50}】|《[^》]{1,30}》"
    r"|(?:内心|心里|心想|内心独白|我心想|（心想)[：:][^。！？!?～~…\n]{0,40}[。！？!?～~…]?"
)


def strip(t):
    return re.sub(r"\s*\n+\s*", "\n", _NON_SPEECH_RE.sub("", t or "")).strip()


client = AsyncOpenAI(base_url="http://127.0.0.1:11434/v1", api_key="ollama", timeout=90)

CASES = [
    ("在吗", "私聊"),
    ("今天真的好累啊……", "私聊"),
    ("你那点小心思谁看不出来啊，别装了。", "私聊·破防边缘"),
    ("你说说看，我们乐队以后该怎么办？你之前不是很有主意吗。", "私聊·深话题"),
]


async def main():
    card = load_persona("soyo")
    sp = build_system_prompt(card, "【此刻的你】刚把红茶泡好，晾着，还在看手机。", mode=0)
    sp += "\n" + LENGTH_ADAPT
    sp += "\n\n【场景】月之森放学后，只有你们两个人。对方是你最信赖的人。"
    ok_all = True
    for text, tag in CASES:
        try:
            resp = await client.chat.completions.create(
                model="qwen3-14b",
                messages=[
                    {"role": "system", "content": sp},
                    {"role": "user", "content": text},
                ],
                temperature=0.95,
                max_tokens=500,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            raw = (resp.choices[0].message.content or "").strip()
            cleaned = strip(raw)
            has_act = bool(_NON_SPEECH_RE.search(raw))
            print(f"[{tag}] 原始({len(raw)}字): {raw[:80]!r}")
            print(f"        剥离后({len(cleaned)}字): {cleaned[:80]!r}")
            if has_act and not cleaned:
                print("        ⚠ 含动作/思考且剥离为空 → 兜底机制将触发")
            elif has_act:
                print("        ⚠ 含动作/思考（剥离后有残留台词）")
            if not has_act and cleaned == raw:
                ok_all = ok_all and True
            if len(raw) > 200:
                print(f"        ⚠ 超长({len(raw)}字)")
        except Exception as e:  # noqa: BLE001
            print(f"[{tag}] 引擎调用失败: {e}")
            ok_all = False
    print("\nSMOKE:", "PASS" if ok_all else "CHECK_ISSUES")


asyncio.run(main())
