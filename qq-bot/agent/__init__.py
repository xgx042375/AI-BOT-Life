"""agent 包（2026-09-04，LangGraph 独立层）

架构：
    NoneBot2 主程序（brain 等）──薄适配──▶ agent（LangGraph StateGraph + SqliteSaver）
    事件 → agent.invoke()（超时兜底，不阻塞主流程）
    agent 输出 → 主程序发送

反向依赖：agent 不 import brain（数据由调用方传入 Context），保证层间解耦。
agent 再出 bug 也只影响自己的行为域，拖不垮主程序。
"""
from .graph import get_app, run_agent, AGENT_DB
from .guardrails import input_guard, output_guard

__all__ = ["get_app", "run_agent", "input_guard", "output_guard", "AGENT_DB"]
