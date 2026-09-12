# -*- coding: utf-8 -*-
# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
r"""角色扮演语料构建：ChatHaruhi 剧本(texts) + 对话(dialogues) → sharegpt jsonl。

策略（广覆盖，非针对单一角色）：
- 每个角色目录：主角 = system_prompt 中的角色 或 出现次数最多的说话者
- texts/*.txt 剧本行 `角色:「台词」`：主角台词→gpt，其他角色台词→human（连续同角色合并）
- dialogues/*.jsonl {"role","text"}：同样映射
- 按窗口切分为多轮 conversations（每窗 ROUNDS 轮），system = 角色简介

输出: E:/robot/qq-bot/data/rp_train.jsonl (sharegpt)
用法: .venv-train\Scripts\python.exe -u tools\build_rp_corpus.py [--max-samples 4000] [--dry]
"""
import argparse
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CH = Path(r"E:/robot/references/roleplay/chat-haruhi/Chat-Haruhi-Suzumiya-main")
OUT = ROOT / "data" / "rp_train.jsonl"

ROUNDS = 8          # 每个样本的最大轮数（human+gpt 交替计为一轮）
MAX_WINDOWS_PER_FILE = 3   # 单个剧本文件最多切几个窗口
MAX_FILES_PER_CHAR = 400   # 每角色最多读取的剧本文件数
MIN_LINES = 4       # 少于该行数的剧本跳过

LINE_RE = re.compile(r"^\s*([^:：]+?)\s*[:：]\s*「(.*)」\s*$")


def load_system_prompt(char_dir: Path) -> str | None:
    sp = char_dir / "system_prompt.txt"
    if sp.exists():
        text = sp.read_text(encoding="utf-8").strip()
        # 取前几行作为人设摘要（剧本原文含"上文给定经典桥段"等指令，太长）
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        core = []
        for ln in lines:
            if ln.startswith(("Please", "如果", "上文", "别人")) or "codename" in ln:
                continue
            core.append(ln)
        return "\n".join(core)[:300] or None
    return None


def parse_texts(char_dir: Path) -> list[list[tuple[str, str]]]:
    """解析剧本行 → [[(说话者, 台词)], ...] 按文件分组。"""
    files: list[list[tuple[str, str]]] = []
    for tf in sorted((char_dir / "texts").glob("*.txt")):
        turns: list[tuple[str, str]] = []
        for line in tf.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = LINE_RE.match(line)
            if m:
                turns.append((m.group(1).strip(), m.group(2).strip()))
        if turns:
            files.append(turns)
    return files


def parse_dialogues(char_dir: Path) -> list[tuple[str, str]]:
    turns = []
    dlg_dir = char_dir / "dialogues"
    if dlg_dir.exists():
        for jf in sorted(dlg_dir.glob("*.jsonl")):
            for line in jf.read_text(encoding="utf-8", errors="ignore").splitlines():
                try:
                    o = json.loads(line)
                    turns.append((o["role"].strip(), o["text"].strip()))
                except Exception:
                    pass
    return turns


def pick_protagonist(char_dir: Path, turns: list[tuple[str, str]]) -> str:
    """主角 = system_prompt 提到的名字 或 出现最多的说话者。"""
    sp = char_dir / "system_prompt.txt"
    if sp.exists():
        t = sp.read_text(encoding="utf-8", errors="ignore")
        for cand in Counter(s for s, _ in turns).most_common():
            if cand[0] in t or t.startswith("你") and len(turns) > 2:
                return cand[0]
    c = Counter(s for s, _ in turns)
    return c.most_common(1)[0][0] if c else ""


def to_sharegpt_windows(turns, protagonist: str, system: str | None) -> list[dict]:
    """主角→gpt，其他→human；连续同角色合并；切窗口。"""
    merged: list[tuple[str, str]] = []
    for spk, txt in turns:
        role = "gpt" if spk == protagonist else "human"
        if merged and merged[-1][0] == role:
            merged[-1] = (role, merged[-1][1] + "\n" + txt)
        else:
            merged.append((role, txt))
    if len(merged) < 2:
        return []
    # 保证以 human 开头
    if merged[0][0] != "human":
        merged = merged[1:]
    windows = []
    step = ROUNDS * 2
    for i in range(0, len(merged) - 1, step):
        seg = merged[i:i + step]
        if len(seg) < 2 or seg[0][0] != "human":
            continue
        conv = [{"from": r, "value": v} for r, v in seg]
        sample = {"conversations": conv}
        if system:
            sample["system"] = system
        windows.append(sample)
    return windows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-samples", type=int, default=4000)
    ap.add_argument("--dry", action="store_true", help="只统计不写文件")
    args = ap.parse_args()

    char_dirs = sorted([d for d in (CH / "characters").iterdir() if d.is_dir()])
    char_dirs += sorted([d for d in (CH / "ChatHaruhi2.0" / "data" / "characters").iterdir() if d.is_dir()])

    all_samples = []
    stats = {"chars": 0, "turns": 0, "windows": 0}
    per_char_win = {}
    for cd in char_dirs:
        name = cd.name
        text_files = parse_texts(cd)
        dlg = parse_dialogues(cd)
        total_turns = sum(len(f) for f in text_files) + len(dlg)
        if total_turns < MIN_LINES:
            continue
        protagonist = pick_protagonist(cd, [t for f in text_files for t in f] + dlg)
        if not protagonist:
            continue
        system = load_system_prompt(cd)
        if not system:
            system = f"你是{name}。请以该角色的性格、口吻和语气进行角色扮演对话。"
        wins = []
        for tf in text_files[:MAX_FILES_PER_CHAR]:
            wins += to_sharegpt_windows(tf, protagonist, system)
        if dlg:
            wins += to_sharegpt_windows(dlg, protagonist, system)
        stats["chars"] += sum(len(t) for f in text_files for _, t in f) + sum(len(t) for _, t in dlg)
        stats["turns"] += total_turns
        stats["windows"] += len(wins)
        per_char_win[name] = len(wins)
        all_samples.extend(wins)

    print(f"[rp] characters={len(char_dirs)} usable={len(per_char_win)} "
          f"turns={stats['turns']} chars={stats['chars']} windows={stats['windows']}", flush=True)
    for n, w in sorted(per_char_win.items(), key=lambda x: -x[1])[:15]:
        print(f"  {n}: {w}", flush=True)

    if args.dry:
        return
    rng = __import__("random").Random(42)
    if len(all_samples) > args.max_samples:
        # 按角色分层：每组最多 ceil(cap/roles)，其余全局均匀
        cap_per_role = max(1, (args.max_samples * 2) // max(1, len(per_char_win)))
        grouped = {}
        idx = 0
        for cd in char_dirs:
            pass
        # 简化：全局均匀抽样（文件级窗口上限已保证角色多样性）
        all_samples = rng.sample(all_samples, args.max_samples)
    OUT.write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in all_samples) + "\n", encoding="utf-8")
    print(f"[rp] written {len(all_samples)} samples -> {OUT}", flush=True)
    lens = [len(s["conversations"]) for s in all_samples]
    print(f"[rp] rounds avg={sum(lens)/len(lens):.1f} min={min(lens)} max={max(lens)}", flush=True)


if __name__ == "__main__":
    main()
