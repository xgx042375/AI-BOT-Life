# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
# -*- coding: utf-8 -*-
"""sync_deps_doc —— 把 launcher/deps.json 生成进 docs/部署指南.md 的标记块。

为什么要工具而不是手抄：这个项目历史上的真 bug 大半是"同一事实两处写、改一处忘另一处"
（情绪词表两套 / 差分目录两处同源 / 引擎参数三源 / `.env.example` 与代码漂移）。
依赖矩阵尤其危险——它同时被**文档**（给人看）和**启动器**（S5 做能力检测）消费，
手抄两份必然漂移。故：**JSON 是唯一事实源，文档表格是生成物**。

用法：
    cd qq-bot
    .venv\\Scripts\\python.exe tools\\dev\\sync_deps_doc.py --check    # 校验一致（不一致 exit 1）
    .venv\\Scripts\\python.exe tools\\dev\\sync_deps_doc.py --write    # 重新生成标记块
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

QQBOT = Path(__file__).resolve().parents[2]
ROBOT = QQBOT.parent
DEPS = ROBOT / "launcher" / "deps.json"
DOC = ROBOT / "docs" / "部署指南.md"

BEGIN = "<!-- BEGIN GENERATED: deps-matrix"
END = "<!-- END GENERATED: deps-matrix -->"


def esc(s) -> str:
    """表格单元格转义：竖线会破坏 Markdown 表结构。"""
    return str(s).replace("|", "\\|").replace("\n", " ").strip()


def render(deps: dict) -> str:
    feats = deps.get("features") or []
    lines: list[str] = []

    # 表一：功能总览
    lines.append(f"<!-- 自动生成，勿手改。源: launcher/deps.json（updated={deps.get('updated','—')}）"
                 f" · 重新生成: qq-bot/tools/dev/sync_deps_doc.py --write -->")
    lines.append("")
    lines.append("| 功能 | 必需 | 检测方式 | 缺失后果 |")
    lines.append("|---|---|---|---|")
    for f in feats:
        req = "**必须**" if f.get("required") else "可选"
        det = esc((f.get("detect") or {}).get("label", "—"))
        lines.append(f"| {esc(f.get('name'))} | {req} | `{det}` | {esc(f.get('degrade'))} |")
    lines.append("")
    lines.append("**读法**：只有第一行是「必须」——**没有任何外部组件是必需的**。"
                 "其余每一行拿掉之后，都有一条明确写着它怎么降级的路径。")
    lines.append("")

    # 表二：组件明细
    lines.append("| 功能 | 组件 | 本机路径 | 本机体积 | 上游来源 | 获取方式 |")
    lines.append("|---|---|---|---|---|---|")
    total = 0
    for f in feats:
        for c in (f.get("components") or []):
            mb = float(c.get("size_mb") or 0)
            total += mb if c.get("kind") != "external" else 0
            size = "（外部程序）" if c.get("kind") == "external" else (f"{mb:,.0f} MB" if mb >= 1 else "<1 MB")
            src = c.get("source") or "—"
            kind = c.get("source_kind") or ""
            if str(src).startswith("http"):
                src = f"[{src}]({src})"
            # source_kind 的唯一作用是把"还没核实"这件事显式标出来。
            # 不做 `来源（来源类型）` 这种复读——第一版生成出来是「PyPI（PyPI）」「Steam 商店（Steam）」。
            if kind == "待核":
                src = f"{src} ⚠️**待核**"
            lines.append(f"| {esc(f.get('name'))} | {esc(c.get('name'))} | `{esc(c.get('path'))}` | {size} | {src} | {esc(c.get('install'))} |")
    lines.append("")
    lines.append(f"> 本机实测总体积合计约 **{total/1024:,.1f} GB**（不含外部程序）。"
                 "体积大的是模型与语音运行环境，都是**可选的**。")
    return "\n".join(lines)


def extract_block(text: str) -> str | None:
    i = text.find(BEGIN)
    if i < 0:
        return None
    j = text.find(END, i)
    if j < 0:
        return None
    # 取标记行结束到 END 之间的正文
    body_start = text.find("\n", i) + 1
    return text[body_start:j].strip("\n")


def main() -> int:
    write = "--write" in sys.argv
    check = "--check" in sys.argv
    if not (write or check):
        print(__doc__)
        return 2
    if not DEPS.is_file():
        print(f"[X] 缺少事实源 {DEPS}")
        return 2
    if not DOC.is_file():
        print(f"[X] 缺少文档 {DOC}（先建骨架，含 {BEGIN} … {END} 标记）")
        return 2

    deps = json.loads(DEPS.read_text(encoding="utf-8-sig"))
    new_body = render(deps)
    doc = DOC.read_text(encoding="utf-8")
    cur = extract_block(doc)

    if write:
        if cur is None:
            print(f"[X] 文档里找不到标记块 {BEGIN} … {END}")
            return 2
        i = doc.find(BEGIN)
        body_start = doc.find("\n", i) + 1
        j = doc.find(END)
        out = doc[:body_start] + "\n" + new_body + "\n" + doc[j:]
        DOC.write_text(out, encoding="utf-8")
        print(f"[OK] 已重新生成 deps-matrix 标记块（{len(new_body.splitlines())} 行）")
        return 0

    # check 模式
    if cur is None:
        print("[X] 文档里找不到标记块")
        return 1
    if cur.strip() == new_body.strip():
        feats = len(deps.get("features") or [])
        print(f"[OK] deps 矩阵一致（{feats} 个功能）")
        return 0
    print("[X] deps 矩阵与 launcher/deps.json 不一致——文档过期了。")
    print("    修复：qq-bot\\tools\\dev\\sync_deps_doc.py --write")
    cur_l, new_l = cur.strip().splitlines(), new_body.strip().splitlines()
    for i in range(max(len(cur_l), len(new_l))):
        a = cur_l[i] if i < len(cur_l) else "<缺行>"
        b = new_l[i] if i < len(new_l) else "<多余行>"
        if a != b:
            print(f"    第{i+1}行 文档: {a[:90]}")
            print(f"            应为: {b[:90]}")
            break
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
