# -*- coding: utf-8 -*-
# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
"""场景白名单校验单测（独立副本，与 brain 逻辑一致）。"""
SCENE_DESCRIPTIONS = {"宫殿闺房", "卧室", "办公室", "神殿大厅", "花园", "作战室", "客厅", "厨房", "浴室", "公园", "车里", "餐厅", "浴场"}
_SCENE_SYNONYM = {"床": "卧室", "床上": "卧室", "床铺": "卧室", "被窝": "卧室", "公司": "办公室",
                  "公司": "办公室", "书房": "办公室", "大殿": "神殿大厅", "神殿": "神殿大厅",
                  "温泉": "浴场", "泳池": "浴场", "家里": "客厅", "家": "客厅"}
_SCENE_EXTRA = {"天台", "阳台", "露台", "地下室", "车库", "仓库", "走廊", "楼梯", "电梯", "屋顶",
                "医院", "学校", "教室", "图书馆", "网吧", "电影院", "健身房", "商场", "超市", "便利店",
                "咖啡馆", "酒吧", "夜店", "KTV", "酒店", "旅馆", "车站", "机场", "码头", "地铁",
                "海边", "沙滩", "湖畔", "湖边", "山顶", "山脚", "森林", "树林", "集市", "广场",
                "游乐场", "体育馆", "剧院", "澡堂", "桑拿", "会所", "棋牌室", "麻将馆", "街边", "巷子",
                "大街", "街上", "路上", "路边", "马路上", "拐角", "大街上"}


def _is_plausible_scene(s: str) -> bool:
    if not s or len(s) > 8:
        return False
    if s in SCENE_DESCRIPTIONS or s in _SCENE_SYNONYM or s in _SCENE_SYNONYM.values() or s in _SCENE_EXTRA:
        return True
    if any(x in s for x in ("这种", "那种", "某种", "那些", "这些", "地方", "时候", "状态",
                            "感觉", "样子", "情况", "事情", "方面", "程度", "阶段", "模式", "方式")):
        return False
    if s.endswith(("了", "呢", "啊", "吧", "吗", "的", "着", "过")):
        return False
    if s.endswith(("房", "室", "厅", "间", "屋", "阁", "馆", "店", "台", "园", "院", "楼",
                   "宫", "殿", "庙", "桥", "街", "路", "巷", "场", "站", "港", "城", "山",
                   "湖", "林", "谷", "洞", "穴", "窟", "堡", "寨", "营", "车", "铺", "坊", "廊", "梯")):
        return True
    return False


tests = [
    ("那种地方", False), ("那种事情", False), ("卧室", True), ("天台", True), ("办公室", True),
    ("雪山", True), ("床", True), ("公司", True), ("商店", True), ("大街上", True),
    ("酒吧", True), ("水族馆", True), ("海底洞穴", True), ("……", False), ("啊", False),
    ("那种感觉", False), ("什么地方", False), ("后宫", True), ("游乐园", True),
]
ok = True
for src, exp in tests:
    got = _is_plausible_scene(src)
    mark = "OK " if got == exp else "FAIL"
    if got != exp:
        ok = False
    print(f"{mark} {src!r} -> {got} (期望 {exp})")
print("ALL_OK" if ok else "SOME_FAIL")
