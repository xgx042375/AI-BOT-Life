"""agent/state.py —— AgentState（LangGraph TypedDict 状态定义）

一个完整的决策周期：事件进来（context）→ 感知（perception）→ 自决（decision）
→ 行动（action）→ 反思（reflection 写回记忆）。
checkpointer 持久化整个状态 → 中断可恢复、决策可回放（S6 验证）。
2026-09-07：清死字段 need_search/slang_note/decision/at_target/reflection/feedback_delta
（定义后全库零读写——graph 感知/自决的实际产物走别的键）。
"""
from typing import TypedDict


class AgentState(TypedDict, total=False):
    # ---- 输入（调用方填充）----
    kind: str            # "banter" | "reply" | "greet"
    user_id: str
    group_id: str
    text: str
    is_group: bool
    mentioned: bool
    continuation: bool
    context: dict        # 调用方提供的环境快照（banned/sour/mood/历史等，guardrails 用）
    persona: str         # 当前人设卡 id（反思记忆按人设隔离——旧卡发言不注入新卡感知）

    # ---- 感知层输出 ----
    perception: str      # 感知摘要（话题/情绪/反思记忆/网络用语释义等，喂给自决）
    search_bg: str       # 检索结果背景（可选）

    # ---- 自决层输出 ----
    decision_action: str  # "speak" | "silent"
    chosen_line: str     # 净化后输出（输出护栏通过后才算数）
    pacing: float        # 2026-09-06 开口节奏：回复前停顿秒数（0~2，bot 自决【等 X 秒】）

    # ---- 行动层 ----
    final_line: str      # 待发送文本（主程序负责实际发送）

    # ---- 走查信息（checkpointer/回放用）----
    guard_notes: list    # 各护栏判定记录 [{"guard": id, "ok": bool, "reason": str}]
    meta: dict           # 备用
