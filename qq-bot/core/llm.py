"""llm —— LLM Provider 适配层（2026-09-10 Phase1 通用化改造）。

用途：收拢全部 LLM 客户端构造（此前散在 brain/memory/emotion/correction/sticker/
fiction/agent 运行时共 7 处构造点，另有 tests/run_replay.py 回放工具 1 处，
合计 8 处 AsyncOpenAI(...)），统一解析端点/模型/密钥——
env 配一处即可切到任意外部 OpenAI 兼容 API（vLLM / DeepSeek / OpenAI 等），
代码零改动。

铁律：**默认本地配置下行为零变化**——不配置任何 llm_* 键时，base_url / api_key /
模型名 / 超时 / extra_body（enable_thinking 注入）/ 上下文预算，与迁移前逐字节等价
（本地 llama-server http://127.0.0.1:11434/v1，model=gemma，api_key=ollama）。

键与回落链（nonebot driver config 优先，其次 os.environ，最后内置默认；
空串视为未设置）：
    llm_provider        默认 "local"（本地 llama-server）；外部端点设任意非 local 值
                        （如 openai_compat）——thinking 注入仅 local 生效
    llm_base_url        → 回落 ollama_base_url → http://127.0.0.1:11434/v1
    llm_api_key         默认 "ollama"
    llm_model           → 回落 ollama_model → "gemma"
    llm_model_{purpose} purpose 专用模型（优先于 llm_model 链）；
                        purpose ∈ main/small/agent/emotion/correction/sticker/fiction
    llm_small_base_url  small 任务独立端点（未设回落主链）
    llm_small_api_key   small 任务独立密钥（未设回落主链 llm_api_key，默认 "ollama"）
    llm_ctx_budget      上下文预算基数（默认 32768，= 引擎总 ctx 口径）
    llm_thinking_param  auto|on|off（默认 auto：仅 provider=local 时注入 enable_thinking）
    embed_base_url      默认 http://127.0.0.1:11435/v1/embeddings
    embed_enabled       默认 true

改配置后调 reload() 清缓存生效（含客户端单例）。
"""
from __future__ import annotations

import os

from openai import AsyncOpenAI

from core.paths import EMBED_BASE_URL, ENGINE_BASE_URL

# 默认本地端点（取值自 core.paths 唯一来源；2026-09-12 E 项：此处曾与 plugins.brain /
# plugins.debug 各写一份字面量 = "改了 A 忘了 B"的高发面）
_LOCAL_BASE_URL = ENGINE_BASE_URL
_LOCAL_MODEL = "gemma"
_LOCAL_API_KEY = "ollama"
_EMBED_URL_DEFAULT = EMBED_BASE_URL
_CTX_BUDGET_DEFAULT = 32768

# purpose -> 超时（秒），与迁移前各构造点逐一对应，默认零变化：
#   brain main/small 60 / agent 60 / memory 60 / emotion 60 / correction 30 /
#   sticker 不传（openai SDK 默认）/ fiction 180（长文生成）
_PURPOSE_TIMEOUTS: dict[str, float | None] = {
    "main": 60.0,
    "small": 60.0,
    "agent": 60.0,
    "memory": 60.0,
    "emotion": 60.0,
    "correction": 30.0,
    "sticker": None,
    "fiction": 180.0,
}
_PURPOSES = tuple(_PURPOSE_TIMEOUTS)

_UNSET = object()
_cfg_obj: object = _UNSET  # nonebot driver config 对象（懒加载并缓存快照引用）
_clients: dict[str, AsyncOpenAI] = {}  # purpose -> 客户端单例


def _driver_cfg():
    """nonebot driver config 懒加载（core 层不硬依赖 nonebot：独立脚本/测试无 driver 时返回 None）。"""
    global _cfg_obj
    if _cfg_obj is _UNSET:
        try:
            from nonebot import get_driver

            _cfg_obj = get_driver().config
        except Exception:  # noqa: BLE001
            _cfg_obj = None
    return _cfg_obj


def _get(key: str, default):
    """配置三级回落：nonebot driver config → os.environ（大小写各试一次）→ default。"""
    cfg = _driver_cfg()
    v = getattr(cfg, key, None) if cfg is not None else None
    if v is None or (isinstance(v, str) and not v.strip()):
        v = os.getenv(key) or os.getenv(key.upper())
    if v is None or (isinstance(v, str) and not v.strip()):
        return default
    return v


def base_url(purpose: str = "main") -> str:
    """端点回落链：small 优先 llm_small_base_url；主链 llm_base_url → ollama_base_url → 本地默认。"""
    if purpose == "small":
        v = _get("llm_small_base_url", None)
        if v:
            return str(v).strip()
    v = _get("llm_base_url", None) or _get("ollama_base_url", None) or _LOCAL_BASE_URL
    return str(v).strip()


def get_client(purpose: str = "main") -> AsyncOpenAI:
    """按 purpose 取客户端单例（同 purpose 复用同一实例）。"""
    c = _clients.get(purpose)
    if c is None:
        api_key = str(_get("llm_api_key", _LOCAL_API_KEY))
        if purpose == "small":  # small 任务独立密钥（未设回落主链，默认零变化）
            v = _get("llm_small_api_key", None)
            if v:
                api_key = str(v).strip()
        kw: dict = {"base_url": base_url(purpose), "api_key": api_key}
        timeout = _PURPOSE_TIMEOUTS.get(purpose, 60.0)
        if timeout is not None:  # None = 不传，保持 SDK 默认（对齐迁移前 sticker 构造）
            kw["timeout"] = timeout
        c = AsyncOpenAI(**kw)
        _clients[purpose] = c
    return c


def resolve_model(purpose: str = "main") -> str:
    """模型名回落链：llm_model_{purpose} → llm_model → ollama_model → "gemma"。"""
    if purpose:
        v = _get(f"llm_model_{purpose}", None)
        if v:
            return str(v).strip()
    v = _get("llm_model", None) or _get("ollama_model", None) or _LOCAL_MODEL
    return str(v).strip()


def thinking_extra(think: bool) -> dict:
    """chat.completions 的 extra_body（llama-server 思考开关）。

    provider=local 且 llm_thinking_param 非 off 时注入 {"enable_thinking": think}
    （param=on 恒注入 True）；非 local 端点或 off 时返回 {}（chat_template_kwargs
    是 llama-server 专属参数，外部服务不认识）。
    """
    if str(_get("llm_provider", "local")).strip().lower() != "local":
        return {}
    mode = str(_get("llm_thinking_param", "auto")).strip().lower()
    if mode == "off":
        return {}
    if mode == "on":
        think = True
    return {"chat_template_kwargs": {"enable_thinking": bool(think)}}


def ctx_budget() -> int:
    """上下文预算基数（llm_ctx_budget，默认 32768=引擎总 ctx 口径；坏值回落默认）。"""
    try:
        return max(1, int(_get("llm_ctx_budget", _CTX_BUDGET_DEFAULT)))
    except Exception:  # noqa: BLE001
        return _CTX_BUDGET_DEFAULT


def embed_config() -> tuple[str, bool]:
    """语义检索 embed 端点与开关（embed_base_url / embed_enabled，默认本地 11435 + 开）。"""
    url = str(_get("embed_base_url", _EMBED_URL_DEFAULT)).strip()
    raw = _get("embed_enabled", True)
    if isinstance(raw, bool):
        enabled = raw
    else:
        enabled = str(raw).strip().lower() in ("1", "true", "yes", "on")
    return url, enabled


def reload() -> None:
    """清配置快照与客户端单例缓存（改配置/测试后调用）。"""
    global _cfg_obj
    _cfg_obj = _UNSET
    _clients.clear()
