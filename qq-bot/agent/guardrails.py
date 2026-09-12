"""agent/guardrails.py —— 护栏（红线，不可协商）

两组（对应调研的 input/output guardrail 模式）：
  input_guard(ctx)  ：消息能否进入决策——2026-09-06 只守配置级静默（群主关闭的群）
  output_guard(text)：产出能否发送（提示词残句/情绪标签泄漏 / markdown /
                              串台核实；后处理链据此收敛）

均为纯函数：输入 dict → (ok, cleaned, reason)。失败默认拦截（fail-closed）。
"""
from __future__ import annotations

import re

# ---- 身份层（权限边界：管理员 > 亲密白名单（积累轨道） > 普通用户）----
TIER_ADMIN = 3     # 管理员（superusers）：全权（调试/状态机/亲密）
TIER_INTIMATE = 2  # 亲密白名单：关系积累轨道（亲密度随互动真实增长）；无调试、无状态机
TIER_PUBLIC = 1    # 普通用户：公共待遇

INTIMATE_TRIGGER = 80  # 开启亲密待遇（私密尺度/亲近口吻/不 SNUB）的阈值（必须硬——权限边界）
INTIMATE_HOLD = 70     # 滞回保持阈值：曾达到 TRIGGER 后，降到 HOLD 以下才收回（防 79↔80 抖动与轻度扣分挫败感）


def tier_of(user_id: str, superusers: set, intimate_wl: list) -> int:
    """统一身份层。"""
    if user_id in superusers:
        return TIER_ADMIN
    if user_id in intimate_wl:
        return TIER_INTIMATE
    return TIER_PUBLIC


def can_intimate(t: int, intimacy: float = 0.0, peak: float = 0.0) -> bool:
    """聊天亲密待遇（私密尺度/不 SNUB/亲近口吻）：管理员恒定满值；
    白名单成员累计到 INTIMATE_TRIGGER 开启，开启后带滞回（跌破 INTIMATE_HOLD 才收回）。"""
    if t == TIER_ADMIN:
        return True
    if t != TIER_INTIMATE:
        return False
    if intimacy >= INTIMATE_TRIGGER:
        return True
    return peak >= INTIMATE_TRIGGER and intimacy >= INTIMATE_HOLD


# ---- 情绪标签词（模型把它当台词输出 = 内部判断泄漏，事故）----
EMO_TAGS = (
    "平静", "无聊", "吃瓜", "感兴趣", "无语", "想吐槽",
    "被吵到", "想参与", "懒得理", "有点无语", "想说话", "不想说话",
)


def input_guard(ctx: dict) -> tuple[bool, str]:
    """输入护栏（2026-09-06：只守配置级静默——群主关闭的群不决策）。
    ctx 字段：banned。冷却/频控/免打扰类机器门已移除：消息要不要接，由 agent 自决。"""
    if ctx.get("banned"):
        return False, "group-banned-silent"
    return True, ""


def sanitize(text: str) -> str:
    """通用净化：markdown 加粗/单星剥离 + 折行整理。
    2026-09-05：单星动作段放宽到 200 字符（原来 60——长动作描写剥不干净 → 动作泄漏）。
    2026-09-06：括号内独白/内心戏清理（思考过程泄漏事故——"（这个话题离我有点远…）"整条清空→empty 拦截；
    与 voice _ACT_RE 同款模式；对话文本里的括号本来就在红线内）。"""
    t = re.sub(r"\*\*", "", text or "")
    t = re.sub(r"\*([^*\n]{1,200})\*", r"\1", t)
    t = re.sub(r"[（(][^（()）]{1,80}[)）]", "", t)
    return re.sub(r"\s*\n+\s*", "\n", t).strip()


def output_guard(text: str) -> tuple[bool, str, str]:
    """输出护栏（红线级=机器判定，仅形态事故；判定即 retry 信号，不吞消息）。返回 (ok, cleaned, reason)。
    2026-09-05：**topic-mismatch 已从护栏移除**——串台的正解是感知注入隔离（群/私聊分取、自我标注、
    引用/结构剥离），agent 只见正确的上下文；2-gram 机械判定曾把短句插话全杀（误伤），
    现在只作为**反思信号**（graph 侧：衔接弱 → 记 negative，agent 下轮自省），不再拦截。
    2026-09-06 决策契约：插话/问候/poke 域只允许两种输出形态（【不插话】/【不想找】或 1-2 句台词）。
    **长度不设限**（长短由 agent 自决）；只认形态事故——序号列表式思考推演（①…②…）、
    情绪标签泄漏、prompt 残句。"""
    line = sanitize(text)
    if not line:
        return False, "", "empty"
    if re.search(r"(^|\n)\s*[①②③④⑤⑥][\s、.，]|【[^】]{1,14}(思考|内心|分析|心声|判断)", line):
        return False, "", "leak-process-list"
    if "插话吗" in line or re.search(r"[（(]\s*你\s*要", line):
        return False, "", "leak-prompt-residual"
    if any(t in line for t in EMO_TAGS) and (len(line) <= 14 or line.startswith(tuple(EMO_TAGS))):
        return False, "", "leak-emotion-tag"
    return True, line, ""


