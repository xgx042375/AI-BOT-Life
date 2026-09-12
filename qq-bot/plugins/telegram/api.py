# -*- coding: utf-8 -*-
"""telegram.api —— Bot API 传输 + 「OneBot 消息段 → Telegram 调用」纯映射层（2026-09-12）。

为什么拆两层（可机器判的边界）：
  · 本模块**不 import nonebot、不建连接**：plan_sends / split_text / redact / u16len 全是纯函数，
    冒烟可以直接断言（不需要真 token、不需要网络、不需要模型）。
  · 网络与重试全收在 TelegramClient：一个 AsyncClient 常驻（长轮询复用连接），
    429 按 retry_after 重试一次；其余错误上抛，由 plugins/telegram/__init__ 的循环决定退避。

三条纪律：
  ① **token 绝不入日志**：httpx 异常消息里带完整 URL（含 /bot<token>/），
     所以凡是可能进日志的字符串一律先过 redact()。
  ② 不发 parse_mode：人设输出常含 * _ [ ` 等 Markdown 元字符，带 parse_mode 会 400；
     纯文本最稳（URL 仍会被 Telegram 自动识别成链接）。
  ③ 分片按 **UTF-16 码元** 数（TG 的 4096 是码元不是码点）：emoji 占 2 码元，
    按 Python 字符数切会超限被拒——这类差异只在真发时暴露，故放在本层机器判。
"""
from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Iterable
from typing import Any

import httpx
from loguru import logger

TG_TEXT_LIMIT = 4096  # Telegram sendMessage 上限（UTF-16 码元）
DEFAULT_API_BASE = "https://api.telegram.org"

# 图片形态 → (方法, 字段名, 文件名后缀)：TG 各方法的字段名不同，且 GIF/WEBP 用 sendPhoto 会被拒。
# 判定用魔数（内容为准，不看扩展名——OneBot 的 base64 段不带类型信息）。
_IMAGE_HEAD = (
    (b"\x89PNG", "sendPhoto", "photo", "photo.png"),
    (b"\xff\xd8\xff", "sendPhoto", "photo", "photo.jpg"),
    (b"GIF8", "sendAnimation", "animation", "photo.gif"),
    (b"RIFF", "sendSticker", "sticker", "photo.webp"),
)
_URL_EXT_IMAGE = (
    (".gif", "sendAnimation", "animation"),
    (".webp", "sendSticker", "sticker"),
    (".mp4", "sendAnimation", "animation"),
)


class TelegramError(RuntimeError):
    """Bot API 返回 ok=false / 传输失败（description 已脱敏）。"""


def redact(text: Any, token: str) -> str:
    """把字符串里的 bot token 换成 ***（httpx 异常与 URL 回显里会带 token）。"""
    s = str(text)
    if token:
        s = s.replace(token, "***")
    return s


def u16len(s: str) -> int:
    """UTF-16 码元数（Telegram 的计数口径）。"""
    return len(str(s).encode("utf-16-le")) // 2


def split_text(text: str, limit: int = TG_TEXT_LIMIT) -> list[str]:
    """按 UTF-16 码元上限切片（不切坏代理对；超长输出不整条丢）。"""
    out: list[str] = []
    cur: list[str] = []
    used = 0
    for ch in str(text or ""):
        n = u16len(ch)
        if used + n > limit and cur:
            out.append("".join(cur))
            cur, used = [], 0
        cur.append(ch)
        used += n
    if cur:
        out.append("".join(cur))
    return out


def _seg_type_data(seg: Any) -> tuple[str, dict]:
    """消息段取 (type, data)：兼容真 MessageSegment 与裸 dict（测试与合成用）。"""
    if isinstance(seg, dict):
        d = seg.get("data")
        return str(seg.get("type") or ""), (d if isinstance(d, dict) else {})
    t = str(getattr(seg, "type", "") or "")
    d = getattr(seg, "data", None)
    return t, (d if isinstance(d, dict) else {})


def _image_from_b64(b64: str) -> tuple[str, str, str]:
    """base64 图片 → (方法, 字段, 文件名)。解码失败/魔数不认识 → sendDocument 兜底（永不静默丢图）。"""
    raw = b64_decode(b64)
    for head, method, field, name in _IMAGE_HEAD:
        if raw.startswith(head):
            return method, field, name
    return "sendDocument", "document", "photo.bin"


def _image_from_url(url: str) -> tuple[str, str]:
    """URL 图片 → (方法, 字段)：按扩展名选（TG 直接取 URL，不下载）。"""
    low = url.split("?")[0].lower()
    for ext, method, field in _URL_EXT_IMAGE:
        if low.endswith(ext):
            return method, field
    return "sendPhoto", "photo"


def b64_decode(s: str) -> bytes:
    """OneBot 的 base64:// 段常缺 padding（'=' 被 URL 化或直接省略）——补足再解。"""
    t = str(s or "")
    t = t.replace("-", "+").replace("_", "/")
    t = t + "=" * (-len(t) % 4)
    try:
        return base64.b64decode(t)
    except Exception:  # noqa: BLE001
        return b""


def plan_sends(segs: Iterable[Any], chat_id: int, limit: int = TG_TEXT_LIMIT) -> list[dict]:
    """OneBot 消息段序列 → Telegram 调用计划（纯函数；skip 项保留，供断言与日志）。

      text   → sendMessage（连续 text 段合并；超限按 4096 码元切片）
      image  → sendPhoto / sendAnimation / sendSticker / sendDocument（url 直传 URL；base64 走上传）
      record → skip（v1 语音只入不出：brain 发的是 silk base64，TG 要 ogg/opus，
               需在 voice 侧留存 wav 再转码——见 docs/遗留任务.md A15）
      at/face/reply → 静默丢弃（私聊里没有可 @ 的对象；TG 侧引用关系 v1 不接）
      其余未知段 → skip 项（可见，不静默吞）
    """
    plan: list[dict] = []
    buf: list[str] = []

    def _flush() -> None:
        txt = "".join(buf).strip("\n")
        buf.clear()
        if txt.strip():
            for chunk in split_text(txt, limit):
                plan.append({"method": "sendMessage", "chat_id": chat_id, "text": chunk})

    for seg in segs or []:
        t, d = _seg_type_data(seg)
        if t == "text":
            buf.append(str(d.get("text") or ""))
        elif t == "image":
            _flush()
            f = str(d.get("file") or "")
            url = str(d.get("url") or "")
            if f.startswith("base64://"):
                method, field, name = _image_from_b64(f[len("base64://") :])
                plan.append({"method": method, "chat_id": chat_id, "field": field,
                             "filename": name, "b64": f[len("base64://") :]})
            elif f.startswith(("http://", "https://")):
                method, field = _image_from_url(f)
                plan.append({"method": method, "chat_id": chat_id, "field": field, "url": f})
            elif url.startswith(("http://", "https://")):
                method, field = _image_from_url(url)
                plan.append({"method": method, "chat_id": chat_id, "field": field, "url": url})
            else:
                plan.append({"method": "skip", "seg": "image",
                             "reason": "非 url/base64 形态（file=%s…）" % f[:32]})
        elif t == "record":
            _flush()
            plan.append({"method": "skip", "seg": "record", "reason": "语音 v1 只入不出（A15）"})
        elif t in ("at", "atall", "face", "reply"):
            continue
        else:
            _flush()
            plan.append({"method": "skip", "seg": t, "reason": "未支持的段类型"})
    _flush()
    return plan


class TelegramClient:
    """Bot API 客户端（长轮询 + 发送，一个 AsyncClient 常驻）。

    - poll_timeout：getUpdates 的服务端等待秒数；read 超时须大于它，否则每 30s 假失败一次。
    - proxy：显式代理（中国大陆直连 api.telegram.org 不通）；不填时 httpx 仍会用
      HTTP(S)_PROXY 环境变量（trust_env 默认 True）。
    """

    def __init__(self, token: str, api_base: str = DEFAULT_API_BASE,
                 proxy: str | None = None, poll_timeout: int = 30) -> None:
        self.token = str(token or "")
        self.api_base = str(api_base or DEFAULT_API_BASE).rstrip("/")
        self.proxy = (proxy or "").strip() or None
        self.poll_timeout = max(1, int(poll_timeout))
        self._client = httpx.AsyncClient(
            proxy=self.proxy,
            timeout=httpx.Timeout(connect=10.0, read=self.poll_timeout + 20.0, write=60.0, pool=10.0),
            follow_redirects=True,
        )

    def _url(self, method: str) -> str:
        return f"{self.api_base}/bot{self.token}/{method}"

    async def call(self, method: str, data: dict | None = None,
                   files: dict | None = None) -> dict:
        """POST 一个 Bot API 方法。429（限流）按 retry_after 重试一次；失败抛 TelegramError。"""
        for attempt in (1, 2):
            try:
                r = await self._client.post(self._url(method), data=data or {}, files=files)
            except Exception as e:  # noqa: BLE001  网络层任何异常都归一到 TelegramError
                raise TelegramError(redact(f"网络错误 {type(e).__name__}: {e}", self.token)) from None
            try:
                js = r.json()
            except Exception:  # noqa: BLE001  反代返回 HTML 错误页是常见坑
                raise TelegramError(redact(
                    f"HTTP {r.status_code} 非 JSON：{r.text[:200]}", self.token)) from None
            if js.get("ok"):
                return js
            desc = str(js.get("description") or "")
            retry_after = int(((js.get("parameters") or {}).get("retry_after") or 0))
            if r.status_code == 429 and retry_after and attempt == 1:
                logger.warning("telegram 限流：{}s 后重试 {}（retry_after）", retry_after, method)
                await asyncio.sleep(min(60, retry_after))
                continue
            raise TelegramError(redact(f"ok=false HTTP {r.status_code} {desc}", self.token))
        raise TelegramError("unreachable")  # pragma: no cover

    async def get_updates(self, offset: int | None = None, timeout: int | None = None) -> list[dict]:
        """长轮询取更新。allowed_updates 只订阅 message（编辑/回调查询等 v1 不接，省流量）。"""
        data: dict[str, Any] = {
            "timeout": int(timeout if timeout is not None else self.poll_timeout),
            "allowed_updates": json.dumps(["message"]),
        }
        if offset is not None:
            data["offset"] = int(offset)
        js = await self.call("getUpdates", data=data)
        res = js.get("result")
        return res if isinstance(res, list) else []

    async def send_plan(self, plan: Iterable[dict]) -> list[dict]:
        """按计划逐条发送（顺序发送＝不撞 TG 限流；单条失败不中断后续）。"""
        out: list[dict] = []
        for item in plan or []:
            method = str(item.get("method") or "")
            if method == "skip":
                logger.info("telegram skip: seg={} reason={}", item.get("seg"), item.get("reason"))
                continue
            data: dict[str, Any] = {"chat_id": item.get("chat_id")}
            files = None
            if method == "sendMessage":
                data["text"] = str(item.get("text") or "")
            elif item.get("url"):
                data[str(item.get("field") or "photo")] = str(item["url"])
            elif item.get("b64"):
                raw = b64_decode(str(item["b64"]))
                if not raw:
                    logger.warning("telegram skip: base64 解码为空（段丢弃）")
                    continue
                files = {str(item.get("field") or "photo"): (str(item.get("filename") or "photo.png"), raw)}
            else:
                logger.warning("telegram skip: 计划项既无 text/url/b64（{}）", method)
                continue
            try:
                out.append(await self.call(method, data=data, files=files))
            except TelegramError as e:
                logger.warning("telegram send 失败（后续继续）: {} {}", method, e)
        return out

    async def close(self) -> None:
        try:
            await self._client.aclose()
        except Exception as e:  # noqa: BLE001
            logger.debug("telegram client close: {} [{}]", e, type(e).__name__)
