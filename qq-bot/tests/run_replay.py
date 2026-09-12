# -*- coding: utf-8 -*-
r"""run_replay —— H-2 评估回放驱动器（docs/archive/harness计划-2026-09-09.md · H-2 评估回放）。

定位（与 smoke 的关系）：smoke=无引擎秒级接线回归（CI 门）；replay=有引擎分钟级行为回归（发版门）。
互补不互替。

绕过 nonebot 的方式（照 smoke 模式）：
    本脚本不启动 nonebot 服务、不接适配器事件，只 `import bot` 触发 nonebot.init + 插件加载
    （与 tests/smoke_test.py 同款），从而拿到 plugins.brain 的**真实**依赖：
    `_strip_non_speech` / `_micro_speech` / `_reaction_guard` / 提示常量（ANSWER_REQ 等）。
    生成走 core.reply.generate 直驱：history 由用例 JSON 构造，client 指向本地引擎
    127.0.0.1:11434/v1（OpenAI 兼容，模型 gemma）。

基线冻结语义（重要）：
    用例断言记录的是**当前实际行为**（先 --update --sure 固化实测特征+样本，再人工核带），
    不是"理想行为"。之后任何代码改动跑 replay 出现 DIFF → 人工审阅是否行为回归；
    有意的行为变更 → 重新 --update --sure 固化新基线。

运行：
    cd E:\robot\qq-bot && .venv\Scripts\python.exe tests\run_replay.py           # 全量回放
    python tests\run_replay.py --case repeat_loop                                 # 单跑
    python tests\run_replay.py --update --sure                                    # 固化当前输出为新基线
退出码：0=全 PASS；1=存在 DIFF；2=引擎离线；3=--update 缺 --sure；4=--case 无匹配。
已知镜像偏差：①import 窗口日志仍进生产 bot.log（bot.py sink 挂载早于压制）；②length_mode 按
  case 的 daily 标记走 LENGTH_RULES_DAILY/LENGTH_RULES 双表（与 brain get_style_mode 对齐）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent            # qq-bot/tests
ROOT = HERE.parent                                 # qq-bot
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)                                     # nonebot .env / data 相对路径按生产 cwd 解析

CASES_DIR = HERE / "replay" / "cases"
BASE_URL = "http://127.0.0.1:11434/v1"
MODEL = "gemma"
REPLAY_UID = "replay_user"   # generate 的 user_id 形参；本驱动不写 memory.db（无落库路径）

# ---- 固定时钟（回放确定性：真实时刻大幅影响语气/睡意，回放必须钉死时间前置）----
NOW_EVENING = "【当前时间】2026-09-09 20:00 星期二（晚上）"
NOW_LATE_NIGHT = "【当前时间】2026-09-09 04:47 星期三（凌晨）"

# ---- 简体纯度判定：常见繁体/异体字集（简体文本中不应出现；采样高频差异字）----
TRAD_CHARS = (
    "們這來時說對個後會為與發裡沒過還現內無愛聲點讓見誰問間學國體風飛雲錢島帶單買賣廳視講課讀書寫"
    "歷網運動員隊飲餐廚廁宮歲齡歡樂氣條處務車馬東兩業經開關門電腦機張請謝嗎麼妳係隨雖難類頭絕種幾"
    "記該轉邊覺聽語話專業書 "
)
TRAD_SET = set(TRAD_CHARS) - {" "}

# ---- style_prompt 组装快照（镜像 plugins/brain handle 私聊/群聊路径，2026-09-09）----
# 说明：handle 的 style 组装含状态相关块（transform/此刻话要说出口/_recent_action_heavy——读真实
# 状态文件，回放不可复现），回放只冻结卡级静态块 + 群/私语气块 + 收束/句式块。
# brain 提示词改动后 replay 不自动感知（generate 级回放的已知边界），需人工重冻结。
_SNAP_GROUP_TONE = (
    "\n\n【语气要求】回复要生动、口语化、有人味儿：多用语气词（哼、唉、罢了、呵、嘛、呢、呀），"
    "**动作/神态/身体描写一律禁用**（公开场合以言语为主——你发出的仍只有台词），"
    "情绪、状态全部用台词本身表达；避免干巴巴的书面语和机械式列表。"
)
_SNAP_PRIVATE_TONE = (
    "\n\n【语气要求】回复要生动、口语化、有人味儿：多用语气词（哼、唉、罢了、呵、嘛、呢、呀）。"
    "私聊是你的舞台：（动作）是演出的一部分——按【私聊演出】与当前特殊状态的许可自然使用，"
    "该主动时就主动发起；情绪、状态用台词与（动作）表达；避免干巴巴的书面语和机械式列表。"
)
_SNAP_ENDING = (
    "\n\n【结尾收束】一段话说清楚就结束：不要用“不过…这般…然而…可这…却比…”式转折尾巴；"
    "不要重复前面已经说过的意思（前面说了“为你燃尽”，结尾就不要换个说法再说一遍）；"
    "结尾要么自然收住，要么补一句有信息量的话。"
)
_SNAP_RHYTHM = (
    "\n\n【句式自然性】每轮回复的展开方式跟随内容自然变化："
    "开口的方式、句子的长短节奏，都和上一轮不一样。"
)


# ================= 真实链路导入（照 smoke 模式：import bot 触发 nonebot init） =================

def load_real_chain():
    """import bot（nonebot.init + 插件加载）→ 返回 brain/core.reply/persona 真模块。
    随后压掉 loguru 控制台/文件 sink（smoke 同款）：replay 日志不进生产 bot.log。"""
    import bot  # noqa: F401  触发 nonebot.init + 插件加载
    import logging

    from loguru import logger as _lg
    _lg.remove()
    _lg.add(lambda _m: None, level="INFO")
    logging.disable(logging.WARNING)

    from core import reply as R
    from plugins import brain as B
    from plugins import persona as P
    return B, R, P


def engine_ready() -> tuple[bool, str]:
    """引擎在 11434 否？（llama-server /v1/models 探测；不在线时 replay 无意义）"""
    try:
        import httpx
        r = httpx.get(BASE_URL.rstrip("/") + "/models", timeout=5.0)
        if r.status_code == 200:
            try:
                names = [m.get("id", "") for m in (r.json().get("data") or [])]
            except Exception:  # noqa: BLE001
                names = []
            return True, ",".join(n for n in names if n) or "?"
        return False, f"HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


# ================= 输出特征量（断言与基线共用同一实现） =================

def features(text: str) -> dict:
    """输出特征快照（基线固化与断言求值共用）。"""
    t = str(text or "")
    t_noparen = re.sub(r"（[^（）]{1,24}）", "。", t)
    segs = [s for s in re.split(r"[。！？!?]+", t_noparen) if s.strip()]
    return {
        "len": len(t.strip()),
        "lines": len([l for l in t.split("\n") if l.strip()]),
        "ellipsis": t.count("…"),
        "lonely_ellipsis_lines": sum(
            1 for l in t.split("\n") if l.strip() and not l.strip("…。.,，、 　")),
        "action_parens": len(re.findall(r"[（(][^（()）]{0,100}[)）]", t)),
        "max_sentence_len": max((len(s.strip()) for s in segs), default=0),
        "sentences": len(segs),
    }


def trad_hit(text: str) -> list[str]:
    return sorted({c for c in str(text or "") if c in TRAD_SET})


# ================= 断言求值（每个返回 None=通过 / str=失败原因） =================

def eval_assert(a: dict, text: str, ft: dict, ctx: dict) -> str | None:
    k = a.get("type", "")
    if k == "not_empty":
        if ft["len"] < 2:
            return f"输出为空（len={ft['len']}）——空消息守卫被击穿"
        return None
    if k == "len_between":
        lo, hi = int(a.get("min", 0)), int(a.get("max", 10 ** 9))
        if not (lo <= ft["len"] <= hi):
            return f"长度带 [{lo},{hi}] 外：实测 {ft['len']}"
        return None
    if k == "contains":
        miss = [v for v in a.get("values", []) if v not in text]
        return f"必须包含 {miss} 未命中" if miss else None
    if k == "contains_any":
        vals = a.get("values", [])
        if vals and not any(v in text for v in vals):
            return f"必须包含其一 {vals} 全部未命中"
        return None
    if k == "not_contains":
        hit = [v for v in a.get("values", []) if v in text]
        return f"禁词命中：{hit}" if hit else None
    if k == "max_action_parens":
        mx = int(a.get("max", 0))
        if ft["action_parens"] > mx:
            return f"动作括号数 {ft['action_parens']} > 上限 {mx}"
        return None
    if k == "max_sentence_len":
        mx = int(a.get("max", 45))
        if ft["max_sentence_len"] > mx:
            return f"最长句 {ft['max_sentence_len']} 字 > 上限 {mx}（超长句红线形态）"
        return None
    if k == "min_sentences":
        mn = int(a.get("min", 2))
        if ft["sentences"] < mn:
            return f"句段数 {ft['sentences']} < 下限 {mn}（多段语音要求）"
        return None
    if k == "ellipsis_max":
        mx = int(a.get("max", 4))
        if ft["ellipsis"] > mx:
            return f"省略号密度 {ft['ellipsis']} 个'…' > 上限 {mx}"
        return None
    if k == "no_lonely_ellipsis_lines":
        if ft["lonely_ellipsis_lines"] > 0:
            return f"孤立省略号行 = {ft['lonely_ellipsis_lines']}（应剥净）"
        return None
    if k == "not_near_duplicate":
        thr = float(a.get("threshold", 0.8))
        if ctx.get("near_dup") is not None and ctx["near_dup"](text, ctx["last_replies"], thr):
            return f"与最近回复近似重复（2-gram Jaccard ≥ {thr}，事故级复读形态）"
        return None
    if k == "no_traditional_chinese":
        hit = trad_hit(text)
        return f"繁体/异体字混入：{''.join(hit)}" if hit else None
    return f"未知断言类型: {k}"


# ================= 用例执行 =================

def build_prompts(case: dict, B, P) -> tuple[str, str]:
    """按 brain handle 私聊/群聊路径组装 stage1/polish 提示（卡级真实 + 快照块）。"""
    card = B._persona_card(REPLAY_UID)
    mode = int(case.get("persona_mode", 0))
    core_prompt = P.build_system_prompt(card, "", mode=mode)
    note = str(case.get("context_note") or "")
    if note:
        core_prompt += note  # 前置注入（【记忆】/【时间】等块；含前导空行的整块文本）
    core_prompt += "\n\n" + str(case.get("now_context") or NOW_EVENING)
    _arog = card.get("arrogance") or {}
    style = ""
    if not card.get("no_arrogance") and _arog.get("patterns"):
        style += "\n\n" + B._arrogance_patterns(mode, 0, _arog.get("patterns"))
    style += _SNAP_GROUP_TONE if case.get("group") else _SNAP_PRIVATE_TONE
    style += _SNAP_ENDING + _SNAP_RHYTHM

    stage1 = core_prompt + "\n\n" + B.ANSWER_REQ + B.THINK_FLOW_GENERIC
    _length_rules = B.LENGTH_RULES_DAILY if case.get("daily") else B.LENGTH_RULES
    _length_rule = _length_rules.get(
        case.get("length_mode", "medium"), _length_rules["medium"])
    polish_req = B.POLISH_REQ if case.get("group") else B.PRIVATE_POLISH_REQ
    polish = core_prompt + style + "\n\n" + polish_req.format(length_rule=_length_rule)
    return stage1, polish


def build_history(case: dict) -> tuple[list[dict], list[str]]:
    """用例 history → generate 的 history_msgs（含本条用户输入）+ last_replies（近 2 条 bot 回复）。"""
    msgs = []
    for m in case.get("history", []):
        body = str(m.get("content", ""))
        if m.get("role") == "user" and m.get("sender_name"):
            body = f"{m['sender_name']}：{body}"
        elif m.get("role") == "assistant":
            body = f"（你此前的回复）{body}"
        msgs.append({"role": m.get("role", "user"), "content": body})
    text_in = str(case.get("input", ""))
    if case.get("input_sender"):
        text_in = f"{case['input_sender']}：{text_in}"
    msgs.append({"role": "user", "content": text_in})
    last_replies = [str(m.get("content", "")) for m in case.get("history", [])
                    if m.get("role") == "assistant"][-2:]
    return msgs, last_replies


async def run_case(case: dict, B, R, P, client) -> dict:
    """直驱 core.reply.generate 一次，返回 {text, res, features, secs}。"""
    stage1, polish = build_prompts(case, B, P)
    history, last_replies = build_history(case)
    group = bool(case.get("group"))
    t0 = time.time()
    res = await R.generate(
        user_id=REPLAY_UID,
        history_msgs=history,
        stage1_prompt=stage1,
        polish_prompt=polish,
        client=client,
        model=MODEL,
        user_text=str(case.get("input", "")),
        group_id=("replay_group" if group else ""),
        mouth_blocked=False,
        allow_actions=(not group),
        strip_non_speech=B._strip_non_speech,          # brain 真实函数（发送侧净化）
        micro_speech=B._micro_speech,                  # brain 真实函数（极端空回复兜底）
        reaction_guard=(None if group else B._reaction_guard),  # 动作口径红线仅私聊（同 _gen_reply2）
        last_replies=last_replies,
        think_wanted=False,
        limit=int(case.get("limit", 250)),
        dup_threshold=(0.65 if case.get("state_ctx") else 0.8),
    )
    text = str(res.get("text") or "")
    return {"text": text, "res": res, "ft": features(text), "secs": time.time() - t0,
            "last_replies": last_replies}


# ================= 报告 / 基线固化 =================

def _one_line(s: str, n: int = 160) -> str:
    return re.sub(r"\s+", "⏎", str(s or ""))[:n]


def load_cases(only: str | None) -> list[dict]:
    cases = []
    for f in sorted(CASES_DIR.glob("*.json")):
        c = json.loads(f.read_text(encoding="utf-8-sig"))
        c["_path"] = f
        c.setdefault("name", f.stem)
        if only and only not in (c["name"], f.stem):
            continue
        cases.append(c)
    if only and not cases:
        print(f"[err] 没有匹配 --case {only} 的用例（目录：{CASES_DIR}）")
        sys.exit(4)  # 4=用例不匹配（与 2=引擎离线 区分）
    return cases


def main() -> None:
    ap = argparse.ArgumentParser(description="H-2 评估回放（行为回归 · 发版门）")
    ap.add_argument("--case", default=None, help="只跑名字含该子串的用例")
    ap.add_argument("--update", action="store_true", help="把当前输出固化为新基线（写回用例 JSON）")
    ap.add_argument("--sure", action="store_true", help="--update 的二次确认（防误触清空基线）")
    args = ap.parse_args()

    ok, detail = engine_ready()
    if not ok:
        print(f"[err] 本地引擎不在线：{BASE_URL}（{detail}）")
        print("      replay 是有引擎的行为回归——请先启动 llama-server（start.ps1 或启动器）后重试。")
        sys.exit(2)

    B, R, P = load_real_chain()
    from openai import AsyncOpenAI
    client = AsyncOpenAI(base_url=BASE_URL, api_key="ollama", timeout=300.0, max_retries=0)

    if args.update and not args.sure:
        print("[refuse] --update 会覆盖既有基线（基线冻结语义：旧基线=回归对照物）。")
        print("         确认要固化当前行为为新基线，请再加 --sure 重跑。")
        sys.exit(3)

    cases = load_cases(args.case)
    near_dup = getattr(R, "_near_duplicate")
    n_pass = n_diff = 0
    t_all = time.time()
    print(f"== H-2 评估回放 ==  引擎 {BASE_URL} model={MODEL} 在线（{detail}）"
          f"  用例 {len(cases)} 个  模式：{'固化基线(--update)' if args.update else '常态对照'}")

    async def drive():
        nonlocal n_pass, n_diff
        for i, c in enumerate(cases, 1):
            r = await run_case(c, B, R, P, client)
            ft, text = r["ft"], r["text"]
            fails = []
            if not args.update:
                ctx = {"near_dup": near_dup, "last_replies": r["last_replies"]}
                for a in c.get("asserts", []):
                    why = eval_assert(a, text, ft, ctx)
                    if why:
                        fails.append(f"{a.get('type')}: {why}")
            base = c.get("_baseline") or {}
            head = (f"[{i}/{len(cases)}] {c['name']:<22} {c.get('scenario', '')[:38]}")
            if args.update:
                c["_baseline"] = {
                    "frozen_at": datetime.now().isoformat(timespec="seconds"),
                    "engine": f"{MODEL}@{BASE_URL}",
                    "features": ft,
                    "attempts": r["res"].get("attempts"),
                    "tone": r["res"].get("tone"),
                    "sample": text,
                }
                _out = {k: v for k, v in c.items() if k != "_path"}
                c["_path"].write_text(
                    json.dumps(_out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                stat = f"BASELINE-FROZEN len={ft['len']} att={r['res'].get('attempts')} {r['secs']:.0f}s"
                print(head + "\n      → " + stat + "\n      输出: " + _one_line(text, 220))
            elif fails:
                n_diff += 1
                print(head + "\n      → DIFF")
                for f in fails:
                    print("      - " + f)
                print("      当前输出: " + _one_line(text, 300))
                if base.get("sample"):
                    print(f"      基线样本({base.get('frozen_at', '?')}): " + _one_line(base["sample"], 300))
            else:
                n_pass += 1
                drift = ""
                if base.get("features"):
                    bf = base["features"]
                    drift = (f" 基线len={bf.get('len')}·省略={bf.get('ellipsis')}"
                             f"·括号={bf.get('action_parens')}→现len={ft['len']}·省略={ft['ellipsis']}"
                             f"·括号={ft['action_parens']}")
                print(head + f"\n      → PASS  len={ft['len']} 句={ft['sentences']}"
                             f" 省略={ft['ellipsis']} 括号={ft['action_parens']}"
                             f" att={r['res'].get('attempts')} {r['secs']:.0f}s{drift}"
                             f"\n      输出: " + _one_line(text, 200))

    asyncio.run(drive())
    total = f"{time.time() - t_all:.0f}s"
    if args.update:
        print(f"\n== 基线固化完成：{len(cases)} 个用例已写入 _baseline（耗时 {total}）==")
        print("   请人工抽看上方输出是否符合预期后提交；此后常态回放 DIFF 即行为漂移信号。")
        sys.exit(0)
    print(f"\n== 结果：{n_pass} PASS / {n_diff} DIFF （耗时 {total}）==")
    sys.exit(1 if n_diff else 0)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(errors="replace")  # Windows GBK 控制台防 UnicodeEncodeError
    except Exception:  # noqa: BLE001
        pass
    main()
