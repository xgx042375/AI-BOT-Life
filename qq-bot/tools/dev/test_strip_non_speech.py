# -*- coding: utf-8 -*-
# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
"""非台词语段剥离单测（与 brain._strip_non_speech 逻辑一致的副本）。"""
import re

_NON_SPEECH_RE = re.compile(
    r"[（(][^（()）]{1,100}[)）]|[*＊][^*＊]{1,120}[*＊]|【[^】]{1,50}】|《[^》]{1,30}》"
    r"|(?:内心|心里|心想|内心独白|我心想|（心想)[：:][^。！？!?～~…\n]{0,40}"
)


def strip(t):
    out = _NON_SPEECH_RE.sub("", t or "")
    return re.sub(r"\s*\n+\s*", "\n", out).strip()


tests = [
    ("（心跳漏了一拍，呼吸有些急促）", ""),
    ("哼，区区凡人。（轻蔑地笑）", "哼，区区凡人。"),
    ("*甩了甩袖子* 就这点本事？", "就这点本事？"),
    ("内心：其实我有点高兴。哼。", "哼。"),
    ("「啊啦，又在发呆呢。」", "「啊啦，又在发呆呢。」"),
    ("嗯。（歪了下头）……好吧。", "嗯。……好吧。"),
    ("（身子猛地一颤）", ""),
    ("她才不会承认。（内心独白：但他捏头的感觉不坏）才怪。", "她才不会承认。才怪。"),
]
ok = True
for src, exp in tests:
    got = strip(src)
    mark = "OK " if got == exp else "FAIL"
    if got != exp:
        ok = False
    print(f"{mark} {src!r} -> {got!r} (期望 {exp!r})")
print("ALL_OK" if ok else "SOME_FAIL")
