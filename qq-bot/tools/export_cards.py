# -*- coding: utf-8 -*-
r"""export_cards —— 内容包卡 → data/cards.json 导出（robot-pack-v1 Phase 3）。

链路：packs（type=card 包）→ data/cards.json → launcher PS Get-Cards（web 人设区 + 原生干员页同源）。
对现有 cards.json **只追加不覆盖**：键（card.key）已存在的条目原样保留（launcher 现卡零影响，
本地手改优先）；包卡缺省字段给中性默认。跑一遍即生效（launcher 重启后可见新卡，免重编译 exe）。

用法：cd E:\robot\qq-bot && .venv\Scripts\python.exe tools\export_cards.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import atomics  # noqa: E402
from core import packs  # noqa: E402
from core.paths import data_path  # noqa: E402

CARDS_FILE = data_path("cards.json")
# 包卡 launcher 字段缺省（中性、零作品指向；包内 card 段声明同名字段则覆盖）
_DEFAULT_ENTRY = {"cls": "MOD", "star": 4, "uni": "PACK", "desc": ""}


def entry_from_pack(pk: packs.Pack) -> dict | None:
    """卡包 → cards.json 条目 {key,name,cls,star,uni,desc}；缺 name/card.key 返回 None。"""
    card_sec = pk.manifest.get("card") if isinstance(pk.manifest.get("card"), dict) else {}
    key = pk.card_key
    name = str(card_sec.get("name") or "").strip()
    if not name:
        try:
            card = pk.read_json("card.json")
            data = card.get("data") if isinstance(card, dict) else {}
            name = str((data or {}).get("name") or "").strip()
        except Exception:  # noqa: BLE001
            name = ""
    if not key or not name:
        print(f"[skip] {pk.name}: manifest.card.key / 卡 name 缺失")
        return None
    entry = {"key": key, "name": name}
    for f, default in _DEFAULT_ENTRY.items():
        v = card_sec.get(f)
        entry[f] = v if v not in (None, "") else default
    try:
        entry["star"] = int(entry["star"])
    except (TypeError, ValueError):
        entry["star"] = _DEFAULT_ENTRY["star"]
    entry["cls"] = str(entry["cls"])[:8]
    entry["uni"] = str(entry["uni"])[:24]
    entry["desc"] = str(entry["desc"])[:40]
    return entry


def main() -> int:
    packs.reload()
    cards = atomics.read_json(CARDS_FILE, None)
    if not isinstance(cards, list):
        print(f"[warn] {CARDS_FILE} 不存在或不是数组：以空表起步（launcher 内置回退表仍在）")
        cards = []
    existing = {str(c.get("key") or "") for c in cards if isinstance(c, dict)}
    added: list[dict] = []
    for pk in packs.by_type("card"):
        entry = entry_from_pack(pk)
        if entry is None:
            continue
        if entry["key"] in existing:
            print(f"[keep] {entry['key']}: 已在 cards.json（现有卡优先，不覆盖）")
            continue
        cards.append(entry)
        existing.add(entry["key"])
        added.append(entry)
        print(f"[add]  {entry['key']}: {entry['name']}（{entry['uni']} / {entry['cls']} / {entry['star']}星）")
    if added:
        if not atomics.write_json_atomic(CARDS_FILE, cards):
            print("[error] 写出失败（cards.json 未改）")
            return 1
        print(f"[done] 追加 {len(added)} 张包卡 → {CARDS_FILE}（no-BOM 原子写；重启 launcher 生效）")
    else:
        print("[done] 无新包卡需要追加（cards.json 未改）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
