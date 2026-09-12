# -*- coding: utf-8 -*-
"""tg_loop_check —— Telegram 通道传输层联调（2026-09-12）。

为什么需要它：冒烟 §55 判的是**纯映射与门**，判不到"长轮询真跑起来、真把回复发出去"。
而真 token 要人去 BotFather 建 bot（我做不了），所以这里用一个**本地 mock Bot API 服务器**
把整链走通：getUpdates 长轮询 → 入站门 → 合成 OneBot 事件 → 管线（替身，不调 LLM）→
call_api 出站翻译 → 真发 HTTP 回到 mock。唯一没被覆盖的是 Telegram 服务器自己。

覆盖的判据（每条都会打一行 OK/FAIL）：
  1. 启动排空：离线积压 2 条只记条数、**不补答**（无任何出站）
  2. 排空后 offset = 最后一条 update_id + 1（不重放旧消息——重放会重复回话、重复烧额度）
  3. 主人私聊：uid = identity_uid()（与 QQ/GAL 同记忆）、文本原样、合成轮标记已置位
  4. 出站真的发到 Bot API：chat_id 锁主人、内容与替身产出一致
  5. 陌生人消息：不回复（且只啰嗦一次）
  6. 群聊消息：不回复（v1 边界）
  7. stop() 干净停止，状态可诊断

用法（qq-bot 目录下）：.venv\\Scripts\\python.exe tools\\dev\\tg_loop_check.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PORT = 18999
TOKEN = "0:mocktoken"
OWNER = 424242
STRANGER = 999999

# 离线积压（drain 阶段应被跳过）+ 在线两条（主人一条、陌生人一条）
STALE = [{"update_id": 1, "message": {"chat": {"id": OWNER, "type": "private"},
                                      "from": {"id": OWNER, "first_name": "我"},
                                      "message_id": 1, "text": "离线时说的第一句"}},
         {"update_id": 2, "message": {"chat": {"id": OWNER, "type": "private"},
                                      "from": {"id": OWNER, "first_name": "我"},
                                      "message_id": 2, "text": "离线时说的第二句"}}]
LIVE = deque([
    {"update_id": 3, "message": {"chat": {"id": OWNER, "type": "private"},
                                 "from": {"id": OWNER, "first_name": "晓咕咕", "id_last": None},
                                 "message_id": 3, "text": "在吗"}},
    {"update_id": 4, "message": {"chat": {"id": STRANGER, "type": "private"},
                                 "from": {"id": STRANGER, "first_name": "路人"},
                                 "message_id": 4, "text": "你能帮我写作业吗"}},
    {"update_id": 5, "message": {"chat": {"id": -100123, "type": "group", "title": "群"},
                                 "from": {"id": OWNER, "first_name": "我"},
                                 "message_id": 5, "text": "群里@一下"}},
])

REC: dict[str, list] = {"getupdates": [], "sends": [], "other": []}
DRAIN = deque(STALE)
ok = 0
bad = 0


def check(name: str, cond: bool) -> None:
    global ok, bad
    if cond:
        ok += 1
        print(f"  [OK] {name}")
    else:
        bad += 1
        print(f"  [FAIL] {name}")


class MockTG(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # 静音（否则每条请求刷一行）
        pass

    def _json(self, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        ctype = str(self.headers.get("Content-Type") or "")
        method = self.path.rsplit("/", 1)[-1]
        if "application/x-www-form-urlencoded" in ctype:
            form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
        else:  # multipart（图片上传）：只记大小，不解包
            form = {"__multipart_bytes": str(len(raw))}
        if method == "getUpdates":
            REC["getupdates"].append(form)
            if int(form.get("timeout") or 30) == 0:  # 排空调用（服务端不等待）
                items = list(DRAIN)
                DRAIN.clear()
                return self._json({"ok": True, "result": items})
            if LIVE:
                return self._json({"ok": True, "result": [LIVE.popleft()]})
            time.sleep(0.3)  # 长轮询模拟：没消息就挂着
            return self._json({"ok": True, "result": []})
        if method in ("sendMessage", "sendPhoto", "sendAnimation", "sendSticker", "sendDocument"):
            REC["sends"].append({"method": method, **form})
            return self._json({"ok": True, "result": {"message_id": len(REC["sends"])}})
        REC["other"].append({"method": method, **form})
        return self._json({"ok": True, "result": True})


def main() -> int:
    os.environ["TELEGRAM_ENABLED"] = "true"
    os.environ["TELEGRAM_BOT_TOKEN"] = TOKEN
    os.environ["TELEGRAM_OWNER_ID"] = str(OWNER)
    os.environ["TELEGRAM_API_BASE"] = f"http://127.0.0.1:{PORT}"
    os.environ["TELEGRAM_PROXY"] = ""

    from loguru import logger

    logger.remove()
    logger.add(sys.stdout, level="INFO", format="    | {message}")

    import bot  # noqa: F401  触发 nonebot.init + 插件加载（.env 里的 TELEGRAM_* 被上面 env 覆盖）
    import plugins.telegram as tg
    from nonebot.adapters.onebot.v11 import Message
    from plugins.webgal import in_synthetic_round

    srv = ThreadingHTTPServer(("127.0.0.1", PORT), MockTG)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    seen: dict[str, object] = {}

    async def stub_runner(bot_, ev):  # 替身管线：不调 LLM，只验合成事件 + 走真出站
        seen["uid"] = str(ev.user_id)
        seen["text"] = ev.get_plaintext().strip()
        seen["types"] = [str(s.type) for s in ev.message]
        seen["synthetic"] = in_synthetic_round()
        await bot_.call_api("send_private_msg", user_id=ev.user_id,
                            message=Message("（联调用替身回复）"))

    tg._PIPELINE_RUNNER = stub_runner

    async def run() -> None:
        check("启动：start_if_configured 返回 True", tg.start_if_configured() is True)
        deadline = time.time() + 20
        while time.time() < deadline and len(REC["sends"]) < 1:
            await asyncio.sleep(0.2)
        # 给陌生人与群聊两条 update 留出被消费的时间（固定等待，不空转到超时）
        await asyncio.sleep(2.0)
        await tg.stop()

    asyncio.run(run())

    drain_calls = [g for g in REC["getupdates"] if int(g.get("timeout") or 30) == 0]
    polls = [g for g in REC["getupdates"] if int(g.get("timeout") or 30) != 0]
    sends = REC["sends"]

    print("\n== 判据 ==")
    check("① 排空：跳过的离线积压条数 = 2", tg.status().get("skipped_offline") == 2)
    check("① 排空：离线消息一条都没被回复", len(sends) == 1)
    check("② 排空后 offset = 过期末条+1（不重放）", bool(polls) and polls[0].get("offset") == "3")
    check("② 排空请求确实用 timeout=0", len(drain_calls) >= 1)
    check("③ 合成事件：uid 与 QQ/GAL 同源", seen.get("uid") == tg.identity_uid())
    check("③ 合成事件：文本原样送进管线", seen.get("text") == "在吗")
    check("③ 合成事件：合成轮标记已置位（不触发真实 QQ 动作）", seen.get("synthetic") is True)
    check("④ 出站：真发到 Bot API 一条 sendMessage", len(sends) == 1
          and sends[0]["method"] == "sendMessage")
    check("④ 出站：chat_id 锁主人（不信 payload）", sends and sends[0].get("chat_id") == str(OWNER))
    check("④ 出站：内容与管线产出一致", sends and sends[0].get("text") == "（联调用替身回复）")
    check("⑤ 陌生人消息被拦（无第二条出站）", len(sends) == 1)
    check("⑤ 陌生人只啰嗦一次", any(k.startswith("u:") for k in tg._SKIP_LOGGED))
    check("⑥ 群聊消息被拦（无第三条出站）", len(sends) == 1
          and any(k.startswith("g:") for k in tg._SKIP_LOGGED))
    check("⑦ 停止后 running=False", tg.status().get("running") is False)
    check("⑦ 一轮计数：rounds=1（陌生人与群聊未进管线）", tg.status().get("rounds") == 1)
    check("⑦ 非发送类 API 未触达（无 getMe/setX 出站）", REC["other"] == [])

    srv.shutdown()
    print(f"\n== 结果：{ok} OK / {bad} FAIL ==")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
