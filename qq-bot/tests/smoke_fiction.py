# -*- coding: utf-8 -*-
"""写作模块冒烟测试：gemma 引擎（11434）生成 3 题材各 1 本（大纲+第一章）：
① 用户&bot 主题（符合人设：傲娇女神与主人的故事）
② 网文-玄幻
③ 网文-真实都市
输出回执 + 正文摘录，供审查。"""
import sys, os, time, asyncio

sys.path.insert(0, r"E:\robot\qq-bot")
os.chdir(r"E:\robot\qq-bot")
import nonebot
nonebot.init()

from plugins import fiction

# 题材矩阵（param ≤16字 且仅中文/字母/数字/短横线——fiction 白名单）
STOPIC = [
    ("傲娇神女与主人", "傲娇神女奥丁与主人的日常温馨甜文女主视角第一人称"),
    ("玄天剑帝", "玄幻废柴少年得剑胎逆天崛起热血升级爽文"),
    ("都市夜行者", "都市北漂程序员与旧书店女孩互相救赎现实温暖"),
]

# 进度回调（打印阶段消息）
async def _prog(msg: str):
    print(f"  [prog] {msg}")


async def main():
    for title, setting in STOPIC:
        print(f"\n{'='*60}\n📖 题材: {title}\n设定: {setting[:60]}\n{'='*60}")
        # 删除旧书（防重名冲突）——2026-09-08：原引用已删除的 fiction._delete_book_dirs
        # （hasattr 守卫恒 False → 分支从未生效）；现无按书删除 API，移除死调用（行为不变）
        t0 = time.time()
        try:
            receipt = await fiction.exec_action("start", setting, progress=_prog)
            print(f"\n[回执] {receipt}\n[耗时] {time.time()-t0:.1f}s")
            # 读取第一章正文（摘录审查）：真实书名 = 设定 param[:16]（exec_action 的 title=param[:16] 规则）
            try:
                from pathlib import Path as _P
                book_real = setting[:16]
                meta = fiction._load_meta(book_real)
                ch1 = _P(fiction.FICTION_ROOT) / book_real / "001.md"
                if ch1.exists():
                    body = ch1.read_text(encoding="utf-8")
                    print(f"\n[第一章摘录·前400字]\n{body[:400]}")
                else:
                    print(f"  [章节文件不存在] {ch1}（章节列表: {fiction._chapter_list(meta) if meta else '无meta'}）")
            except Exception as e:  # noqa: BLE001
                print(f"  [读章失败] {e}")
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"[失败] {title}: {e}")


asyncio.run(main())
