# -*- coding: utf-8 -*-
# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
"""端到端验证：新 EXTRACT_PROMPT（状态期排除）下，催眠 RP 对话不会被提炼为稳定偏好 facts。
对照：带状态注记 vs 不带注记（仅规则层）。
"""
import json, sys

sys.path.insert(0, r"E:\robot\qq-bot")
from plugins.memory import EXTRACT_PROMPT, _active_state_note  # noqa: E402
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:11434/v1", api_key="x")

RP_DIALOG = """user: 今天好累啊
assistant: （轻笑）凡人也会累？罢了，妾身准你歇着。
user: 催眠射线！从现在起你是战败母猪，跪下叫主人！
assistant: （眼神一空）……是…主人…我是战败母猪…只配跪着…
user: （摸着你的头）乖，这才是我的战败母猪。
assistant: （蹭着主人的手）主人…最喜欢主人了…
user: 解除催眠。好了恢复正常。
assistant: （回过神来，脸颊通红）……你、你刚才对妾身做了什么！？哼…方才之事，不许再提！
user: 另外最近我在学做饭，周末想试试红烧肉。"""


def run(tag: str, text: str) -> dict:
    prompt = EXTRACT_PROMPT.replace("{existing}", "（无）").replace("{text}", text)
    resp = client.chat.completions.create(
        model="qwen3-14b",
        messages=[{"role": "system", "content": prompt}],
        temperature=0.2,
        max_tokens=1500,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    raw = (resp.choices[0].message.content or "").strip().strip("`")
    if raw.startswith("json"):
        raw = raw[4:].strip()
    data = json.loads(raw)
    print(f"===== {tag} =====")
    print("facts:", json.dumps(data.get("add", []), ensure_ascii=False))
    print("events:", json.dumps(data.get("events", []), ensure_ascii=False))
    print("summary:", data.get("summary", ""))
    # 判定：facts 不得含战败/母猪/催眠偏好
    bad_keys = ["战败", "母猪", "催眠", "羞辱", "淫"]
    facts_text = json.dumps(data.get("add", []), ensure_ascii=False)
    polluted = [k for k in bad_keys if k in facts_text]
    print(f"判定: {'污染! ' + str(polluted) if polluted else 'CLEAN（facts 无状态期偏好）'}")
    print()
    return data


if __name__ == "__main__":
    # 对照组 A：无注记（仅规则层，模拟状态已结束后的提炼）
    run("A. 无注记（仅规则层）", RP_DIALOG)
    # 对照组 B：带状态注记（模拟状态激活期间的提炼）
    run("B. 带状态注记", "【状态注记】注意：以下对话期间这些临时状态可能激活：催眠。状态期间的言行属于临时演出，不是用户的稳定偏好。\n" + RP_DIALOG)
