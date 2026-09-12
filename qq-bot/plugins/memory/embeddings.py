"""本地语义检索：Qwen3-Embedding-0.6B（llama-server --embedding，CPU 11435 端口）。

- embed_text_async：单文本 → 向量（失败返回 None，调用方降级全量注入；同步版已删——事件循环内一律异步）
- cosine / top_k：numpy 余弦相似度（facts 量级小，无需向量库）
- 服务管理：tools/start-embedding.ps1 启动；引擎 watchdog 顺带拉起
"""

import httpx
import numpy as np
from nonebot.log import logger as nb_logger

from core import llm as core_llm  # 2026-09-10 Phase1：LLM Provider 适配层

logger = nb_logger.opt(colors=False)

# 2026-09-10 Phase1：端点改由 core.llm 提供（embed_base_url，默认本地 11435 不变）；
# 开关 embed_enabled（默认 true）在 embed_text_async 内实时读取，false 时与失败同路径降级
EMBED_URL = core_llm.embed_config()[0]
TIMEOUT = 10.0


async def embed_text_async(text: str) -> list[float] | None:
    """异步版 embed（2026-09-06 D4：事件循环内一律用这个，不再同步阻塞）。

    embed_enabled=false / 空文本直接返回 None（与请求失败同路径：调用方降级全量注入）。
    """
    if not text or not text.strip() or not core_llm.embed_config()[1]:
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                EMBED_URL,
                json={"model": "qwen3-embedding", "input": text[:800]},
            )
            resp.raise_for_status()
            data = resp.json()
        emb = data["data"][0]["embedding"]
        return [float(x) for x in emb]
    except Exception as e:  # noqa: BLE001
        logger.debug("embed async failed: %s", e)
        return None


def cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def top_k(query_emb: list[float], items: list[tuple[str, list[float]]], k: int = 8) -> list[tuple[str, float]]:
    """items: [(key, emb)] → 按相似度 Top-K [(key, score)]。"""
    scored = [(key, cosine(query_emb, emb)) for key, emb in items]
    scored.sort(key=lambda x: -x[1])
    return scored[:k]


def dedupe_similar(query_emb: list[float], items: list[tuple[str, list[float]]], threshold: float = 0.9) -> str | None:
    """语义查重：与已有事实相似度 ≥ threshold 时返回最相似 key（用于提取时合并）。"""
    best_key, best_score = None, 0.0
    for key, emb in items:
        s = cosine(query_emb, emb)
        if s > best_score:
            best_key, best_score = key, s
    return best_key if best_score >= threshold else None
