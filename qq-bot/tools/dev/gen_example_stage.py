# [DEV-ONLY] 开发工具——不随核心发行版与 SDK 分发，仅供本机生成随仓示例素材。
# -*- coding: utf-8 -*-
"""gen_example_stage —— 生成随仓示例舞台包（`qq-bot/packs/example.stage/bg/*.png`）的占位背景。

为什么要"生成"而不是拷素材（2026-09-12）：
  示例包要**随发行版走**，所以一条铁律——**不能带任何私人立绘或第三方 IP**。
  于是这里只画纯几何渐变 + 一行 ASCII 水印（`EXAMPLE STAGE — PLACEHOLDER`），
  任何人打开 GAL 页一眼就知道"这是占位图，该换成我自己的"，
  而不是把作者的本机素材误当成发行内容。

为什么不生成 `sprites/diff/`：舞台包的差分图会**覆盖用户自己的立绘差分**
（`sprite_dirs()` 包在前、本机在后，同词优先取包里的）。示例包带占位差分 =
把用户辛苦做的差分全遮住。所以示例只给背景，差分由用户自己放（见 pack.json 的 `_说明`）。

幂等：重复执行直接覆盖同名文件，不产生额外产物。
用法：cd E:\\robot\\qq-bot && .venv\\Scripts\\python.exe tools\\dev\\gen_example_stage.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent          # qq-bot/tools/dev
QQBOT_DIR = TOOLS_DIR.parents[1]                      # qq-bot
PACK_DIR = QQBOT_DIR / "packs" / "example.stage"
BG_DIR = PACK_DIR / "bg"

W, H = 1280, 720
# (bg id, 顶部色, 底部色, 地平线色) —— 三张各自冷暖不同，便于肉眼分辨"切场景生效了"
SCENES = (
    ("room", (26, 30, 42), (58, 64, 86), (86, 96, 126)),
    ("office", (20, 36, 50), (44, 84, 100), (74, 128, 142)),
    ("teahouse", (54, 36, 28), (116, 80, 52), (168, 124, 78)),
)
CAPTION_1 = "EXAMPLE STAGE - PLACEHOLDER"
CAPTION_2 = "replace with your own background art"


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)   # Pillow ≥10.1：可缩放默认字体
    except TypeError:
        return ImageFont.load_default()            # 旧版回退（字号小，但仍是 ASCII，够用）


def make_bg(bg_id: str, top, bottom, horizon) -> Path:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (W, H), top)
    d = ImageDraw.Draw(img, "RGBA")
    for y in range(H):
        d.line([(0, y), (W, y)], fill=_lerp(top, bottom, y / H))
    hy = int(H * 0.66)
    d.rectangle([0, hy, W, hy + 6], fill=horizon)
    d.rectangle([0, hy + 6, W, H], fill=_lerp(bottom, horizon, 0.25))
    # 一个"窗"框：提示这是室内场景模板（纯几何，不含任何角色/作品元素）
    d.rectangle([W - 470, 90, W - 110, 400], outline=(255, 255, 255, 60), width=3)
    d.line([(W - 290, 90), (W - 290, 400)], fill=(255, 255, 255, 36), width=2)
    d.line([(W - 470, 245), (W - 110, 245)], fill=(255, 255, 255, 36), width=2)
    # 水印：占位必须一眼可辨（不写中文：默认位图字体只有 ASCII，写了会变方块）
    f1, f2 = _font(34), _font(20)
    tx, ty = 48, H - 132
    d.rectangle([tx - 16, ty - 14, tx + 700, ty + 78], fill=(0, 0, 0, 110))
    d.text((tx, ty), CAPTION_1, font=f1, fill=(255, 255, 255, 235))
    d.text((tx, ty + 44), f"{CAPTION_2}  [{bg_id}]", font=f2, fill=(255, 214, 140, 235))
    out = BG_DIR / f"{bg_id}.png"
    img.save(out, "PNG", optimize=True)
    return out


def main() -> int:
    if not PACK_DIR.is_dir():
        print(f"× 示例包目录不存在：{PACK_DIR}")
        return 1
    BG_DIR.mkdir(parents=True, exist_ok=True)
    for bg_id, top, bottom, horizon in SCENES:
        p = make_bg(bg_id, top, bottom, horizon)
        print(f"√ {p.relative_to(QQBOT_DIR)}  ({p.stat().st_size:,} B)")
    pack = json.loads((PACK_DIR / "pack.json").read_text(encoding="utf-8-sig"))
    ids = [s[0] for s in SCENES]
    st = pack.get("stage") or {}
    refs = [st.get("default_bg"), st.get("bg_alt"), *list((st.get("bg_by_scene") or {}).values())]
    missing = sorted({str(r) for r in refs if r and str(r) not in ids})
    if missing:
        print(f"× pack.json 引用了不存在的背景 id：{missing}（bg 目录里只有 {ids}）")
        return 1
    print(f"√ pack.json 的 {len([r for r in refs if r])} 个背景引用全部有文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
