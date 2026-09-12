"""paths —— 唯一数据根（2026-09-06）。

历史问题：同一项目两套数据根并存（E:/robot/data 与 qq-bot/data），
备份/迁移易漏（审计报告 P3）。新代码一律从这里取路径；
存量文件的迁移随各模块重写逐步收敛（读旧写新期间兼容）。
"""
from __future__ import annotations

from pathlib import Path

# E:\robot（core/ 位于 qq-bot/core）
ROBOT_ROOT = Path(__file__).resolve().parents[2]
# E:\robot\qq-bot
QQBOT_ROOT = ROBOT_ROOT / "qq-bot"
# 唯一数据根：记忆库、状态、日志都在这里
DATA_ROOT = ROBOT_ROOT / "data"
# 人格卡目录（用户自维护；历史位置在 qq-bot/data/personas，保持不迁移）
PERSONAS_DIR = QQBOT_ROOT / "data" / "personas"

# 第三方运行时与本机资源根（2026-09-12 S1 审计：这两个根此前被 5 个模块各自硬编码
# "E:\robot\..."，换盘/迁移即失效。此处是唯一来源；tools/ 整体 gitignored——
# 发行版不带这些文件，但**路径推导必须正确**，否则用户把安装根放到 D:\ 就全线断链）。
TOOLS_ROOT = ROBOT_ROOT / "tools"
LAUNCHER_ROOT = ROBOT_ROOT / "launcher"

# ── 本机引擎（llama.cpp / llama-server）与模型：Python 侧唯一来源 ──────────────
# 为什么集中（2026-09-12 审计 E 项）：同一个端口、同一个模型文件此前在 core.llm、
# plugins.brain、plugins.debug 各写一份字面量。这类"改了 A 忘了 B"是本项目最高频的
# 真 bug 类型（曾出现"同一台机器两条启动路径行为不同"）。
# ⚠ PS 侧（start.ps1=权威 / Launcher.ps1 / build_release.ps1）无法 import，只能**对齐值**：
#   改这里必须同步改那三处 —— smoke §36 逐参数守护（含 --host / --port / 模型文件名）。
ENGINE_HOST = "127.0.0.1"
ENGINE_PORT = 11434
ENGINE_URL = f"http://{ENGINE_HOST}:{ENGINE_PORT}"      # 健康检查/props 用（无 /v1）
ENGINE_BASE_URL = f"{ENGINE_URL}/v1"                    # OpenAI 兼容端点（客户端 base_url）
ENGINE_EXE = str(TOOLS_ROOT / "llama.cpp" / "llama-server.exe")
ENGINE_IMAGE = Path(ENGINE_EXE).name                    # taskkill /IM 用：由上面派生，不再各写一份 exe 名
EMBED_PORT = 11435
EMBED_URL = f"http://{ENGINE_HOST}:{EMBED_PORT}"
EMBED_BASE_URL = f"{EMBED_URL}/v1/embeddings"
MODELS_DIR = DATA_ROOT / "models"
MODEL_FILE_GEMMA = str(MODELS_DIR / "gemma4-12b" / "gemma-4-12B-it-heretic-Q4_K_M.gguf")
EMBED_MODEL_FILE = str(MODELS_DIR / "embedding" / "Qwen3-Embedding-0.6B-Q8_0.gguf")


def data_path(name: str) -> Path:
    """数据根下的文件路径。"""
    return DATA_ROOT / name
