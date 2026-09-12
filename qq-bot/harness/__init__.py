r"""harness —— 项目级 agent 场景驱动（2026-09-08，agent 化工程应具备的 harness 能力）。

定位（与策略盲审边界一致）：
  - harness = **可复现回归护栏**：场景→驱动完整 handle（事件桩+DummyBot）→确定性断言
    （管线跑通/无泄漏标记/状态一致性/长度上限）——它测"环境不受破坏、链不崩"；
  - 行为质量（主动占比/模板/节奏/繁简）由 `tools/behavior_metrics.py` 行为指标 + 真人实测负责；
  - 零参考可判定化（策略盲审陷阱6②）：harness 扫描注入文本的行为域样例——判"实现是否遵守原则"。

用法：
    .\.venv\Scripts\python.exe harness\runner.py            # 跑全部用例
    .\.venv\Scripts\python.exe harness\runner.py daily     # 跑指定用例

隔离：状态文件（life_state/special_state/persona_select/intimate_whitelist 等）跑前备份→
写入夹具→跑→恢复（与 smoke 的 tmp 隔离同哲学——绝不动真实状态）。
"""
