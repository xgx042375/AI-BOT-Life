"""QQ 头像管理（NapCat 扩展 API set_qq_avatar）。

命令（仅管理员，priority=1 拦截，先于 brain）：
- "换头像" + 图片/链接/路径：临时换头像
- "设置善良头像" + 图片：保存为当前人格默认头像（persona_<名>_0），并立即应用
- "设置恶堕头像" + 图片：保存为当前人格恶堕头像（persona_<名>_1），并立即应用

人格头像自动联动：brain 在模式切换时按角色卡 avatar 字段自动应用。
图片经 Pillow 预处理：居中裁方 + 640x640 + 透明合白底；经 base64:// 传给 NapCat
（规避 Windows 路径分隔符解析问题）。
"""
import asyncio
import base64
import json
import re
import time
from pathlib import Path
from core.paths import DATA_ROOT

import httpx
from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.exception import FinishedException
from nonebot.log import logger as logger
from nonebot.rule import Rule

AVATAR_DIR = DATA_ROOT / "avatars"  # E:\robot\data\avatars（与 brain 一致）
AVATAR_DIR.mkdir(parents=True, exist_ok=True)
APPLIED_FILE = AVATAR_DIR / ".applied.json"
MODE_FILE = DATA_ROOT / "persona_mode.json"

CMD_SET_AVATAR = "换头像"
# 通用别名（素世系：日常/破防，黑化）
_PERSONA_ALIAS_MODE = {
    "设置日常头像": 0, "设置善良头像": 0, "设置普通头像": 0,
    "设置破防头像": 1, "设置黑化头像": 1, "设置恶堕头像": 1,
}
_CMD_PREFIXES = tuple(_PERSONA_ALIAS_MODE.keys()) + (CMD_SET_AVATAR,)


def _avatar_rule(event: MessageEvent) -> bool:
    """只匹配头像命令消息；其余消息不激活本 matcher，正常流向 brain。
    2026-09-03：兼容 "/设置…" 前缀（debug 不再拦截未知 / 命令）+ 纯图片消息（pending 组合）。"""
    text = event.get_plaintext().strip().lstrip("/")
    if text.startswith(_CMD_PREFIXES):
        return True
    # 图文分段第二段：待接头命令 + 本条带图
    pend = _pending.get(str(event.get_user_id()))
    if pend and time.time() - pend[1] < PENDING_WINDOW and _extract_image_url(event):
        return True
    return False


avatar_cmd = on_message(rule=Rule(_avatar_rule), priority=1, block=True)

_URL_RE = re.compile(r"https?://[^\s]+?(?:\.(?:png|jpg|jpeg|webp|gif)(?:\?[^\s]*)?|$)", re.IGNORECASE)
AVATAR_SIZE = 640  # QQ 头像标准：1:1 正方形，640x640
# 手机 QQ 图文分段修复：命令先到（无图）→ 记住 60s，图片随后到达即组合处理
_pending: dict[str, tuple[str, float]] = {}
PENDING_WINDOW = 60.0


def _is_superuser(event: MessageEvent) -> bool:
    su = getattr(get_driver().config, "superusers", set())
    return event.get_user_id() in su


def _persona_name(user_id: str | None = None) -> str:
    """当前人设卡名：优先用户选择（/人设），回退全局配置。"""
    try:
        from plugins.brain import _persona_name as _pn

        return _pn(user_id)
    except Exception:  # noqa: BLE001
        return getattr(get_driver().config, "persona", "default")


def _extract_image_url(event: MessageEvent) -> str | None:
    """从消息里提取图片 URL。"""
    for seg in event.message:
        if seg.type == "image":
            url = seg.data.get("url") or ""
            if url:
                return url
    return None


async def _fetch_image(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


def _process_image(raw: bytes, target: Path) -> Path:
    """头像预处理：任意图 -> 居中裁方 -> 640x640 -> 透明合白底 -> JPG。"""
    import io

    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    w, h = img.size
    side = min(w, h)
    img = img.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
    img = img.resize((AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)
    out = target.with_suffix(".jpg")
    img.save(out, "JPEG", quality=92)
    return out


async def _apply_avatar(bot: Bot, path: Path) -> None:
    """经 base64:// 调用 NapCat set_qq_avatar。"""
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    await bot.call_api("set_qq_avatar", file=f"base64://{b64}")


def _clear_applied():
    """头像变更后清空已应用标记，强制下次应用。"""
    try:
        if APPLIED_FILE.exists():
            APPLIED_FILE.unlink()
    except OSError:
        pass  # 标记文件删不掉不影响本轮（下次仍会尝试应用头像）


def _current_mode() -> int:
    """当前全局人格模式：取**最近一次切换**（按 ts 最大值——dict 插入序≠最近切换，2026-09-06 修复）。
    persona_mode.json 结构: {user_id: {"mode": N, "ts": float}}。"""
    try:
        modes = json.loads(MODE_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not modes:
        return 0
    entries = [v for v in modes.values() if isinstance(v, dict)]
    if not entries:
        return 0
    latest = max(entries, key=lambda e: float(e.get("ts", 0) or 0))
    return int(latest.get("mode", 0))


@avatar_cmd.handle()
async def handle_avatar(bot: Bot, event: MessageEvent):
    text = event.get_plaintext().strip().lstrip("/")  # 兼容斜杠前缀
    uid = str(event.get_user_id())
    cmd = next((c for c in _CMD_PREFIXES if text.startswith(c)), None) if text else None
    # 无命令文本：若 60 秒内有待接头的命令 + 本条带图片 → 组合执行（手机 QQ 图文分段；
    # 纯图片消息 text 为空也必须继续，不能提前 return）
    if cmd is None:
        pend = _pending.get(uid)
        if pend and time.time() - pend[1] < PENDING_WINDOW and _extract_image_url(event):
            cmd = pend[0]
            _pending.pop(uid, None)
        else:
            return  # 非头像场景，放行给 brain
    if not _is_superuser(event):
        await avatar_cmd.finish("该操作仅限主人。")  # 2026-09-10 审计铁律#2：删人设自称，功能层措辞
    url = _extract_image_url(event)
    if not url:
        m = _URL_RE.search(text)
        if m:
            url = m.group(0).rstrip(".,;，。；")
    if not url:
        if cmd is not None:
            # 命令先到、图片还没到：记下命令，等下一张图（60 秒内）
            _pending[uid] = (cmd, time.time())
            await avatar_cmd.finish("好，把图片发过来吧（分开发也可以，我会接住）。")
            return
        await avatar_cmd.finish("没看到图片——请发一张图片，或给个图片链接。")

    try:
        content = await _fetch_image(url)
        if cmd == CMD_SET_AVATAR:
            processed = await asyncio.to_thread(_process_image, content, AVATAR_DIR / "avatar")  # P2：曾同步解码大图阻塞事件循环
            await _apply_avatar(bot, processed)
            _clear_applied()
            await avatar_cmd.finish(f"头像已更换~（{processed.name}，{AVATAR_SIZE}x{AVATAR_SIZE}）")
        else:
            mode = int(_PERSONA_ALIAS_MODE[cmd])
            uid = str(event.get_user_id())
            # 2026-09-07 P2：卡文件名用原样卡名（曾 .strip("_") 把 _aoding_ 变 aoding → 卡不存在 → 头像永不持久化）
            card_name = _persona_name(uid) or "default"
            # 角色卡（按用户当前人设；卡不存在则由 brain 负责创建默认）
            from plugins.brain import _persona_card as _pcard

            card = _pcard(uid)
            # self 触发人设（素世式）默认不用 mode1 头像概念
            if mode == 1 and card.get("mode_trigger") == "self":
                await avatar_cmd.finish("该人设没有「破防头像」概念——破防是纯演出，头像保持日常不变。")
                return
            label0 = "日常" if card.get("mode_trigger") == "self" else "善良"
            label1 = "破防" if card.get("mode_trigger") == "self" else "恶堕"
            processed = await asyncio.to_thread(_process_image, content, AVATAR_DIR / f"persona_{card_name.strip('_') or 'default'}_{mode}")
            # 更新角色卡 avatar 字段
            from core.paths import PERSONAS_DIR as _pdir  # 2026-09-08：统一数据根
            card_path = next((c for c in (_pdir / f"{card_name}.json", _pdir / f"{card_name.strip('_')}.json", _pdir / "default.json") if c.exists()), None)
            if card_path is not None:
                card = json.loads(card_path.read_text(encoding="utf-8-sig"))
                # 2026-09-07 P2：曾 setdefault("data", card) 自引用 → dumps 抛 Circular reference 功能必炸；
                # v2 卡写 data 下、平铺卡写顶层
                data = card["data"] if isinstance(card.get("data"), dict) else card
                ref = processed.name
                if mode == 1:
                    alt = data.setdefault("alt_persona", {})
                    alt["avatar"] = ref
                else:
                    data["avatar"] = ref
                from core import atomics
                atomics.write_json_atomic(card_path, card)  # 曾裸写文本（写坏 = 人设卡半截 JSON → bot 全面哑火）
            _clear_applied()  # 文件已更新，无论是否立即应用都强制下次应用
            cur = _current_mode()
            if cur == mode:
                await _apply_avatar(bot, processed)
                await avatar_cmd.finish(f"{label1 if mode else label0}人格头像已记下并立即应用~")
            await avatar_cmd.finish(
                f"{label1 if mode else label0}人格头像已记下~（当前是{label1 if cur else label0}人格，切换时自动应用）"
            )
    except FinishedException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("avatar command failed")
        await avatar_cmd.finish(f"设置失败：{type(e).__name__}: {e}")
