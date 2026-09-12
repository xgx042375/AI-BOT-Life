# -*- coding: utf-8 -*-
# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
"""项目思维导图/架构图渲染（PIL + 微软雅黑）。
输出：docs/思维导图-活人感视角.png、docs/agent结构图.png
用法：.venv/Scripts/python.exe tools/render_mindmap.py
"""
from PIL import Image, ImageDraw, ImageFont

FONT = "C:/Windows/Fonts/msyh.ttc"
FONT_BD = "C:/Windows/Fonts/msyhbd.ttc"
OUT = r"E:\robot\docs"


def F(size, bold=False):
    try:
        return ImageFont.truetype(FONT_BD if bold else FONT, size)
    except OSError:
        return ImageFont.truetype(FONT, size)


def rbox(d, xy, r, fill, outline, width=3):
    d.rounded_rectangle(xy, radius=r, fill=fill, outline=outline, width=width)


def elbow(d, p1, p2, color, width=3):
    """肘形连接线：先横后竖再横。"""
    x1, y1 = p1
    x2, y2 = p2
    mx = (x1 + x2) // 2
    d.line([(x1, y1), (mx, y1), (mx, y2), (x2, y2)], fill=color, width=width, joint="curve")


def arrow_down(d, x, y1, y2, color="#5F6368", width=4, head=14):
    d.line([(x, y1), (x, y2 - head)], fill=color, width=width)
    d.polygon([(x - head * 0.7, y2 - head), (x + head * 0.7, y2 - head), (x, y2)], fill=color)


def text_lines(d, xy, lines, font, color, lh, title=None, title_font=None, title_color=None):
    """多行文本块，返回 (width, height)。"""
    x, y = xy
    h = 0
    if title:
        d.text((x, y), title, font=title_font, fill=title_color)
        h += lh + 6
    for ln in lines:
        d.text((x, y + h), ln, font=font, fill=color)
        h += lh
    w = max([d.textlength(t, font=font) for t in lines] + [1])
    if title:
        w = max(w, d.textlength(title, font=title_font))
    return int(w) + 10, int(h)


# ============================================================ 图一：思维导图
def mindmap():
    BR = [
        ("1 感知 · 她眼中的世界", "#E8F0FE", "#1A56B0", "—— 只注入事实，不给禁令", [
            "对话感知：消息结构(@/引用) · 语言注记 · 群成员画像 · 群画风画像",
            "自我认知：【你刚才说过】防自复读 · 【近期回复经验】+送达账本★",
            "关系状态：亲密度三轨道(主人100/白名单80/群友30) · 情绪轨迹弧线",
            "特殊状态：事实+主人要求原文，怎么演她自决 (core/special)",
            "生活状态：life_text 此刻在做什么 · 久别\"这段时间…\" (lifesim)",
            "记忆注入：facts(>20条向量Top-8，14天\"记不清\") · 摘要/昨日复盘/周复盘 · 事件【重要】",
            "群聊距离事实★：\"私聊的事，群里不提\" (group_safe 接全群聊★)",
            "主动感知：时间事实 · 上次主动未回应(事实非禁令)★ · 心跳念头",
            "群插话感知：上次经验记账 · 俚语→搜索背景→提炼入库 (graph._perceive)",
        ]),
        ("2 决策 · 她怎么想", "#E6F4EA", "#137333", "—— 逻辑全在她，机器只守事故红线", [
            "主回复两阶段：stage1 内容思考(思维链/【认真】自决) → stage2 风格润色",
            "stage2 顺带自标【心情】【基调】★ —— 三条输出链情绪同源",
            "agent 图(群插话/被戳/主动)：perceive→decide→act→reflect",
            "【不插话】【不想找】= 她的自决协议 —— 不说话也是真话",
            "硬边界(仅事故级，fail-closed)：口堵物理事实 · 0.8 Jaccard 复读 · guardrails 红线",
        ]),
        ("3 表达 · 她怎么说", "#FEF7E0", "#B06000", "—— 标记协议，机器只剥除/登记", [
            "[VOICE] → TTS 常驻 worker（基调同源选参考音/变调★；长话自动分段）",
            "[STICKER(:情绪)] → judge 三维目录{人格-状态-情绪}（基调兜底★/特殊状态段接线）",
            "[WRITE:动作|参数] → fiction 写作引擎（每用户互斥/所有权校验/完成回执）",
            "【等X秒】开口节奏 · 【身体遵从】切片第二条消息 · 群私语转私聊",
            "时间质感：按长度随机延迟 · 段间停顿 · 「正在输入」只亮打字阶段",
        ]),
        ("4 生活 · 不聊天时她在干嘛", "#F3E8FD", "#7627BB", "—— agent/lifesim 心跳", [
            "心跳 tick：推进 doing/心情/场景/今日计划（她自报【生活】【场景】【心情】）",
            "介入存续：和TA互动后 20 分钟心跳让路（不重叙独处）",
            "生活日志 life_log → 久别\"这段时间你：…\"摘要 + 每日第一人称日记",
            "对话微更新：真实处境变化即时入状态（管理员不限/白名单次深档）",
        ]),
        ("5 记忆 · 她记得什么", "#FCE8E6", "#C5221F", "—— plugins/memory", [
            "短时：最近 N 轮对话（人设切换时间过滤，防旧卡污染）",
            "facts：每 2 轮 Mem0 式提取 add/update/delete（防幻觉置信度/语义查重合并）",
            "events 一次性事件：容量 100 滚动 · 【重要】里程碑 · 向量去重",
            "复盘：昨日整天★ · 周复盘(幂等键按人★) · summaries（名实相符★）",
            "向量层：本地 Qwen3 embedding，facts/events 全量落向量（召回盲区清零★）",
            "好感记账：白名单小步升温日cap8防刷 · 群友30轨 · mood 同步",
            "提取防火墙：催眠/道具/情欲等状态期演出不得提炼为稳定偏好",
        ]),
        ("6 成长 · 她怎么变", "#E0F7FA", "#006978", "—— 反馈与矫正", [
            "程序记忆 agent_memory：每域\"上次发言+被嫌弃/被接话计数+反思\"",
            "反馈入账点：群聊被接话→positive · 被嫌吵→negative（反应点直调）",
            "correction：主人教的说话方式 → 持久约束注入",
            "情绪→好感联动：express 分类落库，白名单正情绪小步升温",
        ]),
        ("7 边界 · 优先级链", "#ECEFF1", "#37474F", "—— agent 自决 > 硬边界 > 硬编码拦截", [
            "权限三级：管理员(主人) / 白名单(亲密轨) / 公开(只回应不积累)",
            "收敛：群聊距离事实 · 催眠演出仅主人私聊 · 普通用户不可改全局生活状态",
            "已退役：概率机 / 冷却 / 词表判内容 / 机器代笔 / 指数机",
            "工程护栏：状态文件原子写 · agent_memory 防清零 · 进程句柄回收",
        ]),
    ]

    LF, LH, PAD = F(26), 40, 26          # 叶子字号/行高/块内边距
    TF, BF = F(34, True), F(30, True)    # 分支标题/根标题

    # 预排：每分支块高
    blocks = []
    for title, fill, oc, sub, lines in BR:
        bh = PAD * 2 + 56 + len(lines) * LH
        blocks.append(bh)
    GAP = 34
    H = 210 + sum(blocks) + GAP * (len(BR) - 1) + 130
    W = 4640
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    # 标题
    d.text((60, 40), "项目思维导图 · 活人感视角（2026-09-08）", font=F(52, True), fill="#111111")
    d.text((60, 120), "她 = 一个住在 QQ 里、有生活有记忆有脾气的人　（★ = 2026-09-08 新增机制）",
           font=F(30), fill="#555555")

    root_cy = 210 + sum(blocks) + GAP * 6
    root_cy = (210 + H - 130) // 2
    rb = (60, root_cy - 150, 460, root_cy + 150)
    rbox(d, rb, 26, "#F5F5F5", "#111111", 5)
    for i, t in enumerate(["她", "", "有生活 · 有记忆", "有脾气 · 会自己决定"]):
        if t:
            d.text((rb[0] + (400 - d.textlength(t, font=BF)) / 2, rb[1] + 40 + i * 60), t,
                   font=BF, fill="#111111")

    y = 210
    for (title, fill, oc, sub, lines), bh in zip(BR, blocks):
        cy = y + bh // 2
        # 根 → 分支
        elbow(d, (rb[2], root_cy), (760, cy), oc)
        # 分支框
        bx = (760, cy - 54, 1350, cy + 54)
        rbox(d, bx, 18, fill, oc, 4)
        d.text((bx[0] + (590 - d.textlength(title, font=TF)) / 2, cy - 40), title, font=TF, fill=oc)
        # 分支 → 明细
        d.line([(bx[2], cy), (1430, cy)], fill=oc, width=3)
        detail_h = len(lines) * LH + 50
        tw, th = text_lines(d, (1450, cy - detail_h // 2), lines,
                            LF, "#333333", LH, title=sub, title_font=F(27, True), title_color=oc)
        y += bh + GAP

    img.save(f"{OUT}/思维导图-活人感视角.png")
    print("saved mindmap", img.size)


# ============================================================ 图二：agent 结构图
def architecture():
    W, H = 3760, 2900
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    TF, SF, LF = F(40, True), F(30, True), F(26)
    LH = 38

    d.text((60, 36), "Agent 结构图 · 一条消息的旅程（2026-09-08）", font=F(52, True), fill="#111111")
    d.text((60, 116), "brain.handle 主链 + agent 图 + 6 个后台循环　（★ = 2026-09-08 新增/修复）",
           font=F(30), fill="#555555")

    ACC = "#1A56B0"
    cx = W // 2

    def box(cy, h, title, lines, fill="#F5F7FA", oc="#5F6368", x0=None, x1=None, tfont=None):
        x0 = 160 if x0 is None else x0
        x1 = W - 700 if x1 is None else x1
        rbox(d, (x0, cy - h // 2, x1, cy + h // 2), 18, fill, oc, 4)
        d.text((x0 + 30, cy - h // 2 + 18), title, font=tfont or SF, fill=oc)
        yy = cy - h // 2 + 18 + 46
        for ln in lines:
            d.text((x0 + 34, yy), ln, font=LF, fill="#333333")
            yy += LH
        return (x0 + x1) // 2, x1

    y = 210
    # 入口
    c, _ = box(y, 110, "QQ 消息 / 戳一戳 / 群通知", ["文本 · @ · 引用 · 图片 · 戳一戳 · 通知事件"], "#EEF3FB", ACC, tfont=TF)
    prev, py = c, y + 55
    y += 110 + 46

    # 路由
    c, _ = box(y, 200, "NoneBot2 + OneBot v11 路由", [
        "私聊 / 群@   →  brain.handle（chat matcher）",
        "群聊非@    →  brain._maybe_group_banter → agent.graph（感知→自决→【不插话】/台词）",
        "戳一戳     →  brain poke 域 → agent.graph（第一反应自决）"], "#EEF3FB", ACC)
    arrow_down(d, prev, py, y - 100)
    y += 200 + 52

    # 主链标题
    d.text((160, y), "brain.handle 主链", font=TF, fill=ACC)
    y += 66

    steps = [
        ("① 忙窗/去重 → 身份分级 → 状态分流", [
            "is_owner / 白名单 / 公开 三级 · 催眠私语(主人群聊) / 对外收敛(非主人)"]),
        ("② 感知装配 —— 只注入事实，不给禁令", [
            "build_context：facts(向量Top-8) + 昨日/周复盘 + 事件【重要】+ 群聊距离事实★",
            "life_text 生活状态 · core/special 状态事实 · correction 矫正规则 · 群成员画像"]),
        ("③ 搜索分流（不懂就查，超时熔断）", [
            "舰船档案 / 图片 / 网页搜索 → 提炼背景注入 → 话题知识库入库（命中免搜）"]),
        ("④ 两阶段生成 core/reply.generate", [
            "stage1 内容思考（可思维链 / 【认真】自决）→ stage2 风格润色",
            "stage2 顺带自标【心情】【基调】★ —— 语音/配图/文字情绪同源"]),
        ("⑤ 标记剥除登记（机器只登记不判内容）", [
            "[STICKER(:情绪)] 【认真】【生活：…】【场景：…】 【等X秒】【心情】【基调】★"]),
        ("⑥ 发送段并行生成", [
            "语音段：[VOICE]? → TTS worker（基调同源选参考音★ → 48k wav → EQ → 24k silk）",
            "表情段：[STICKER]? → judge → 三维目录{人格-状态-情绪}（基调兜底★/状态段接线）",
            "文本：[WRITE:]→fiction 执行+回执 · 【身体遵从】拆分 · 群动作@替换 · 剥[VOICE]"]),
        ("⑦ 发送 _send_sliced", [
            "切片+按长度延迟+段间停顿+打字灯只亮打字期 · 语音优先(纯语音分段)/表情伴随/回执另发"]),
        ("⑧ 事后管线", [
            "落库(剥净标记) → 好感记账(白名单/群友轨) → 群画像 replied++",
            "remember(送达账本★) → 每2轮 extract_and_store(facts/events/关系/摘要+向量)",
            "emotion.express(主人) → lifesim 微更新 → 漏标nudge → 群私语 → 复盘挂账"]),
    ]
    PAD = 26
    heights = []
    for t, ls in steps:
        heights.append(46 + 40 + len(ls) * LH + PAD)
    for (t, ls), h in zip(steps, heights):
        c, x1 = box(y, h, t, ls, "#F8F9FA", "#3C4043", tfont=SF)
        if prev is not None:
            arrow_down(d, prev, py, y - h // 2)
        prev, py = c, y + h // 2
        y += h + 40

    # 侧栏：后台循环
    side_x0, side_x1 = W - 660, W - 60
    d.text((side_x0, 210 + 160), "后台循环（启动拉起×6）", font=TF, fill="#B06000")
    loops = [
        ("proactive 每5min", "候选(主人+白名单) → 感知(时间事实+未回应事实★) → greet 自决 发/不发"),
        ("review 每小时", "≥10点跑昨日复盘(facts+review+向量★) → 周日周复盘 → 生活日记 → 群画风"),
        ("lifesim 心跳", "生活状态推进 · 日志/日记素材 · 久别基准 interaction_ts"),
        ("engine watchdog", "11434 主模型 + 11435 embedding 守护拉起"),
        ("voice push", "语音毒丸任务清理"),
        ("metrics", "指标观测"),
    ]
    sy = 210 + 236
    for t, ls in loops:
        h = 46 + 40 + (LH * (1 + (len(ls) * 26) // 560)) + PAD
        rbox(d, (side_x0, sy, side_x1, sy + h), 14, "#FEF7E0", "#B06000", 3)
        d.text((side_x0 + 20, sy + 14), t, font=SF, fill="#B06000")
        # 手动按 24 字/行折行
        import textwrap
        yy = sy + 60
        for ln in textwrap.wrap(ls, 22):
            d.text((side_x0 + 22, yy), ln, font=F(24), fill="#333333")
            yy += 34
        sy += h + 26

    # 数据落点
    y += 10
    c, _ = box(y, 230, "数据落点（全部原子写 tmp+replace）", [
        r"E:\robot\data\  memory.db（消息/facts/events/summaries/日周复盘/relation/emotions+向量）",
        "special_state.json · life_state/log/diary · agent_memory.json(防清零守卫)",
        "proactive_state.json · review_state.json · voice_mode/state.json · sticker_state.json · fiction/"],
        "#E6F4EA", "#137333", x0=160, x1=W - 60, tfont=SF)
    arrow_down(d, prev, py, y - 115)
    arrow_down(d, (side_x0 + side_x1) // 2, sy - 20, y - 115)

    img.save(f"{OUT}/agent结构图.png")
    print("saved architecture", img.size)


if __name__ == "__main__":
    mindmap()
    architecture()
