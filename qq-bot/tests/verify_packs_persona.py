# -*- coding: utf-8 -*-
"""verify_packs_persona —— dev_verify_example_packs.py 用的人设插件桩。

只借 plugins.persona 的策略函数（world 注入），不 import 真插件（那会拉起 nonebot/brain 依赖）。
"""
from __future__ import annotations

from core import packs as _packs


def build_system_prompt(persona: dict) -> str:
    """复刻 plugins/persona.build_system_prompt 的 world 段（只注入 always:true）：
    与 core/packs.world_index() 同一口径——桩只省掉脑依赖，不省掉策略。"""
    base = str(persona.get("description") or "")
    uni = str(persona.get("universe") or "").strip()
    if uni:
        lines = ["- " + str(e.get("text") or "").strip()
                 for e in (_packs.world_index().get(uni) or []) if e.get("always")]
        text = "\n".join(l for l in lines if l.strip("- ").strip())
        if text:
            if len(text) > 800:
                text = text[:800] + "\n（世界书条目超长，已截断）"
            base += f"\n\n【世界观设定】\n{text}"
    return base
