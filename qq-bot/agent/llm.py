"""agent/llm.py —— 推理层封装（OpenAI 兼容端点）

agent 不与任何框架耦合：换模型/端点只改 core/llm.py 的配置（env 可切外部端点）。
"""
from openai import AsyncOpenAI

from core import llm as core_llm  # 2026-09-10 Phase1：LLM Provider 适配层

# 只读别名（赋值自 core.llm 配置；grep 全仓无外部引用面，保守保留防未知引用断裂）
BASE_URL = core_llm.base_url("agent")
MODEL = core_llm.resolve_model("agent")  # llama-server 单模型加载，model 名仅作标识

_client: AsyncOpenAI | None = None


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = core_llm.get_client("agent")  # timeout=60 口径在 purpose 表保持
    return _client


async def complete(system: str, user: str, *, temperature: float = 0.9,
                   max_tokens: int = 300, no_think: bool = True) -> str:
    """单轮补全，返回 content（含 think 关闭的兼容处理）。"""
    extra = core_llm.thinking_extra(not no_think)
    resp = await client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        extra_body=extra,
    )
    return (resp.choices[0].message.content or "").strip()
