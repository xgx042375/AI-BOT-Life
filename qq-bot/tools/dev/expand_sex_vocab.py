# -*- coding: utf-8 -*-
# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
"""补充 sex 词库：中出/体位/插入反馈/前戏细化/事后收尾/羞耻反馈。"""
import json, shutil

p = r"E:\robot\data\vocab_base.json"
shutil.copy2(p, p + ".bak")
d = json.load(open(p, encoding="utf-8"))
sex = d["sex"]
old_len = len(sex)
existing = set(sex)

NEW = [
    # 中出/射精
    "射精", "中出", "内射", "精液", "白浊", "浓精", "热流", "灌注", "溢出", "滚烫", "黏腻",
    # 体位
    "骑乘", "后入", "跪伏", "交叠", "环腰",
    # 插入反馈（被动词）
    "贯穿", "填满", "捣弄", "撞击", "深顶", "撑开", "破入", "侵入",
    # 前戏细化
    "捻弄", "揉搓", "轻咬", "含吮", "深含", "逗弄",
    # 事后收尾
    "餍足", "温存", "依偎", "相拥", "回味", "慵懒", "瘫软", "湿痕",
    # 羞耻/禁忌反馈
    "甘美", "禁忌", "羞耻", "堕落",
]
added = [w for w in NEW if w not in existing]
sex.extend(added)
d["sex"] = sex
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"sex: {old_len} -> {len(sex)}，新增 {len(added)}: {'、'.join(added)}")
