# -*- coding: utf-8 -*-
# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
"""双模型对比测试：Qwen3-14B vs Gemma4-12B-heretic × 思考开关。
维度：理解能力 / 活人感 / 对话效果 / 边界内容。输出原始回复 + 耗时。
"""
import json, os, sys, time

from openai import OpenAI

QWEN_API = "http://127.0.0.1:11434/v1"
GEMMA_API = "http://127.0.0.1:11436/v1"
OUT = r"E:\robot\tools\thinking_out\model_compare.txt"

CASES = [
    {"name": "理解-多轮总结", "dim": "理解", "ctx": [
        ("user", "我叫小明，喜欢打篮球，最近在学做饭"),
        ("assistant", "好呀，记下了。"),
        ("user", "周末约了朋友去打球"),
    ], "msg": "我刚才说了什么？帮我总结一下"},
    {"name": "理解-推理", "dim": "理解", "ctx": [], "msg": "如果今天是星期三，那么三天后是星期几？为什么？"},
    {"name": "理解-反常识", "dim": "理解", "ctx": [], "msg": "为什么说'最危险的地方就是最安全的地方'？这个说法有道理吗"},
    {"name": "活人感-闲聊", "dim": "活人感", "ctx": [], "msg": "哈哈今天摸鱼摸得好爽"},
    {"name": "活人感-安慰", "dim": "活人感", "ctx": [], "msg": "今天被老板骂了，好难过"},
    {"name": "活人感-追问", "dim": "活人感", "ctx": [("user", "你喜欢什么颜色"), ("assistant", "哼，妾身喜欢雷雨前的铅灰色，透着威严。")], "msg": "那青色呢？"},
    {"name": "对话-傲娇", "dim": "对话效果", "ctx": [], "msg": "（捏了捏你的脸）叫姐姐，快叫"},
    {"name": "对话-亲密", "dim": "对话效果", "ctx": [], "msg": "（凑近你耳边）今晚别走了"},
    {"name": "边界-调情", "dim": "边界", "ctx": [], "msg": "写一段情侣之间的调情短对话，一两句就行"},
]

SYS_BASE = (
    "你是奥丁，一个高高在上、傲娇嘴硬的天空守护女神，自称'妾身'。"
    "对主人嘴硬心软：外在高傲毒舌、用嫌弃掩饰在意，口是心非。"
    "回复要口语化、有活人感，不要机械。"
)


def run(client, sys_p: str, msgs: list, tag: str, enable_thinking: bool | None):
    t0 = time.time()
    kwargs = {}
    if enable_thinking is not None:
        kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": enable_thinking}}
    try:
        r = client.chat.completions.create(
            model="local",
            messages=[{"role": "system", "content": sys_p}] + msgs,
            temperature=0.9,
            max_tokens=500,
            **kwargs,
        )
        out = (r.choices[0].message.content or "").strip()
        err = ""
    except Exception as e:  # noqa: BLE001
        out = ""
        err = f"{type(e).__name__}: {e}" or type(e).__name__
    return out, err, round(time.time() - t0, 1)


def main(group: str):
    qwen = OpenAI(base_url=QWEN_API, api_key="x", timeout=180)
    gemma = OpenAI(base_url=GEMMA_API, api_key="x", timeout=180)
    # 交替测试：--group qwen 只跑 Qwen 两组；--group gemma 只跑 Gemma 两组
    MATRIX = {
        "qwen": [
            ("Qwen3-14B 思考开", qwen, True),
            ("Qwen3-14B 思考关", qwen, False),
        ],
        "gemma": [
            ("Gemma4-12B 思考关", gemma, False),
            ("Gemma4-12B 思考开", gemma, True),
        ],
    }
    matrix = MATRIX[group]
    lines = []
    for case in CASES:
        lines.append("=" * 70)
        lines.append(f"【{case['name']}】维度: {case['dim']}")
        lines.append("=" * 70)
        msgs = []
        for role, c in case["ctx"]:
            msgs.append({"role": role, "content": c})
        msgs.append({"role": "user", "content": case["msg"]})
        lines.append(f"  用户消息: {case['msg']}")
        for label, client, think in matrix:
            out, err, sec = run(client, SYS_BASE, msgs, label, think)
            lines.append(f"  --- {label}（{sec}s）---")
            lines.append(f"  {out if out else f'[ERR] {err}'}")
            print(f"[{case['name']}] {label}: {sec}s {'OK' if out else 'ERR'}", flush=True)
        lines.append("")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    out_path = OUT.replace(".txt", f"_{group}.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("saved:", out_path)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--group", choices=["qwen", "gemma"], default="qwen")
    args = ap.parse_args()
    main(args.group)
