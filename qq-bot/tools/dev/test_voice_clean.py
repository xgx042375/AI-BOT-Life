# -*- coding: utf-8 -*-
# [DEV-ONLY] 开发/诊断工具——不随核心发行版与 SDK 分发，仅供本机排障。
"""语音清洗逻辑单测（独立复制正则，验证行为；插件本体已编译通过）。"""
import re

ACT = re.compile(r"[（(][^（()）]{1,60}[)）]|\*[^*]{1,60}\*|【[^】]{1,60}】|《[^》]{1,60}》")
Q = re.compile(r"[「」『』“”\"'‘’]")
SEP = re.compile(r"(?<=[。！？!?～~…])")


def clean(reply, max_chars=36):
    t = ACT.sub("", reply or "")
    t = Q.sub("", t)
    t = re.sub(r"\s+", "", t).strip()
    if not t:
        return ""
    sents = [s for s in SEP.split(t) if s.strip()]
    out = ""
    for s in sents:
        if out and len(out) + len(s) > max_chars:
            break
        out += s
        if len(out) >= max_chars:
            break
    return out[:max_chars]


tests = [
    ("哼，区区凡人。（轻蔑地笑）", "哼，区区凡人。"),
    ("妾身才没有等你呢！【脸红】", "妾身才没有等你呢！"),
    ("*甩了甩袖子* 就这点本事？", "就这点本事？"),
    ("（内心独白：这个不知天高地厚的凡人……哼，可不是在夸你。）就饶你一命好了。", "就饶你一命好了。"),
    ("好。那就这样吧。晚点再聊。", "好。那就这样吧。晚点再聊。"),
    ("「ふん……まったく。」", "ふん……まったく。"),
    ("（摆手）算你识相。（内心：其实挺高兴的）", "算你识相。"),
    ("哼。", "哼。"),
]
ok = True
for src, exp in tests:
    got = clean(src)
    mark = "OK " if got == exp else "FAIL"
    if got != exp:
        ok = False
    print(f"{mark} {src!r} -> {got!r} (期望 {exp!r})")
print("ALL_OK" if ok else "SOME_FAIL")
