"""VTube Studio WebSocket API 客户端（协议胶水，参照 astrbot_plugin_live_stream_companion 的
vts_client.py 移植，去掉 AstrBot 框架依赖，保留完整协议）。

协议要点：
- 端口 8001，插件需先申请 token（VTS 弹窗显示），再认证
- InjectParameterDataRequest 注入 Live2D 参数（值域 -1..1）
- ExpressionActivationRequest 切换模型表情（.exp3 文件）
"""
import asyncio
import json
import logging
import uuid
from typing import Any

try:
    import websockets
except ImportError:  # pragma: no cover
    websockets = None

logger = logging.getLogger("emotion.vts")


class VTSClientError(Exception):
    pass


class VTSConnectionError(VTSClientError):
    pass


class VTSResponseError(VTSClientError):
    pass


class VTSClient:
    """VTube Studio WebSocket API 客户端（请求-响应式，适合低频情绪注入）。"""

    API_NAME = "VTubeStudioPublicAPI"
    API_VERSION = "1.0"
    DEFAULT_TIMEOUT = 10.0
    CONNECT_TIMEOUT = 5.0

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8001,
        plugin_name: str = "QQAICompanion",
        plugin_developer: str = "local",
    ):
        self.url = f"ws://{host}:{port}"
        self.plugin_name = plugin_name
        self.plugin_developer = plugin_developer
        self.auth_token: str | None = None
        self._ws = None
        self._lock = asyncio.Lock()
        self._is_connected = False
        self._authenticated = False

    # ---------------- 底层通信 ----------------
    def _build_request(self, message_type: str, data: dict | None = None, rid: str = "") -> str:
        return json.dumps(
            {
                "apiName": self.API_NAME,
                "apiVersion": self.API_VERSION,
                "requestID": rid or str(uuid.uuid4())[:8],
                "messageType": message_type,
                "data": data or {},
            }
        )

    async def _send_request(self, message_type: str, data: dict | None = None) -> dict:
        if websockets is None:
            raise VTSClientError("缺少 websockets 库")
        async with self._lock:
            if self._ws is None or not self.is_connected:
                await self._connect()
                if message_type not in {"AuthenticationTokenRequest", "AuthenticationRequest"} and self.auth_token:
                    await self._authenticate_current_connection()
            # 2026-09-07 P2：requestID 曾生成后从不比对——VTS 的主动推送（ModelLoaded 等）或迟到响应
            # 会被当作本次请求的应答（认证错位/表情列表为空等静默错乱）。现在只认匹配的 requestID，
            # 不匹配的推送跳过继续读；超时后连接已失同步，强制断开下次重连。
            rid = str(uuid.uuid4())[:8]
            await self._ws.send(self._build_request(message_type, data, rid=rid))
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.DEFAULT_TIMEOUT
            raw = ""
            resp = None
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    await self._force_disconnect()
                    raise VTSClientError("VTS 响应超时（已断开重连）")
                try:
                    raw = await asyncio.wait_for(self._ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    await self._force_disconnect()
                    raise VTSClientError("VTS 响应超时（已断开重连）")
                try:
                    resp = json.loads(raw)
                except json.JSONDecodeError as e:
                    await self._force_disconnect()
                    raise VTSResponseError(f"VTS 无效响应: {e}") from e
                if str(resp.get("requestID") or "") == rid or resp.get("messageType") == "APIError":
                    break
        if resp.get("messageType") == "APIError":
            err = resp.get("data") or {}
            if err.get("errorID") == 8:
                self._authenticated = False
            raise VTSResponseError(f"VTS APIError {err.get('errorID')}: {err.get('message')}")
        return resp

    async def _connect(self):
        if websockets is None:
            raise VTSClientError("缺少 websockets 库")
        try:
            self._ws = await asyncio.wait_for(websockets.connect(self.url), timeout=self.CONNECT_TIMEOUT)
            self._is_connected = True
            self._authenticated = False
            logger.info("VTS 已连接 %s", self.url)
        except asyncio.TimeoutError as e:
            raise VTSConnectionError(f"连接 VTS 超时: {self.url}") from e
        except ConnectionRefusedError as e:
            raise VTSConnectionError(f"连接被拒绝，请确认 VTube Studio 已启动并开启 API（{self.url}）") from e
        except Exception as e:
            raise VTSConnectionError(f"连接 VTS 失败: {e}") from e

    async def _force_disconnect(self):
        self._is_connected = False
        self._authenticated = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None

    @property
    def is_connected(self) -> bool:
        if self._ws is None or not self._is_connected:
            return False
        try:
            if hasattr(self._ws, "closed"):
                return not self._ws.closed
            if hasattr(self._ws, "state"):
                from websockets import State

                return self._ws.state == State.OPEN
            return True
        except Exception:
            return False

    @property
    def is_authenticated(self) -> bool:
        return self.is_connected and self._authenticated

    async def _authenticate_current_connection(self):
        if not self._ws or not self.auth_token:
            raise VTSConnectionError("缺少 VTS 认证 token")
        await self._ws.send(
            self._build_request(
                "AuthenticationRequest",
                {
                    "pluginName": self.plugin_name,
                    "pluginDeveloper": self.plugin_developer,
                    "authenticationToken": self.auth_token,
                },
            )
        )
        raw = await asyncio.wait_for(self._ws.recv(), timeout=self.DEFAULT_TIMEOUT)
        resp = json.loads(raw)
        if resp.get("messageType") == "APIError":
            await self._force_disconnect()
            raise VTSResponseError(f"VTS 认证失败: {resp.get('data')}")
        self._authenticated = bool(resp.get("data", {}).get("authenticated", False))
        if not self._authenticated:
            await self._force_disconnect()
            raise VTSConnectionError("VTS 认证未通过（token 无效？）")

    # ---------------- 认证 ----------------
    async def request_auth_token(self) -> str:
        resp = await self._send_request(
            "AuthenticationTokenRequest",
            {"pluginName": self.plugin_name, "pluginDeveloper": self.plugin_developer},
        )
        token = resp.get("data", {}).get("authenticationToken")
        if token:
            self.auth_token = token
            logger.info("已向 VTS 申请认证 token（请在 VTS 弹窗中允许）")
            return token
        raise VTSClientError(f"申请 token 失败: {resp}")

    async def authenticate(self, token: str) -> bool:
        self.auth_token = token
        resp = await self._send_request(
            "AuthenticationRequest",
            {
                "pluginName": self.plugin_name,
                "pluginDeveloper": self.plugin_developer,
                "authenticationToken": token,
            },
        )
        self._authenticated = bool(resp.get("data", {}).get("authenticated", False))
        return self._authenticated

    # ---------------- 查询 ----------------
    async def get_expressions(self) -> list:
        resp = await self._send_request("ExpressionStateRequest", {"details": True})
        return resp.get("data", {}).get("expressions", [])

    # ---------------- 控制 ----------------
    async def inject_parameters(self, parameters: list[dict], mode: str = "set") -> dict:
        resp = await self._send_request(
            "InjectParameterDataRequest",
            {"faceFound": True, "mode": mode, "parameterValues": parameters},
        )
        return resp.get("data", {})

    async def set_expression(self, expression_file: str, active: bool = True, fade_time: float = 0.3) -> dict:
        resp = await self._send_request(
            "ExpressionActivationRequest",
            {"expressionFile": expression_file, "active": active, "fadeTime": fade_time},
        )
        return resp.get("data", {})
