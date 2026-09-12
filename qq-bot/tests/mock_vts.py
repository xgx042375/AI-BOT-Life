"""Mock VTube Studio 服务器（协议子集）：用于在无 VTS 环境下测试情绪演出链路。

运行: python tests/mock_vts.py  （监听 127.0.0.1:8001，收到的参数注入打印到控制台）
"""
import asyncio
import json

import websockets
from websockets.exceptions import ConnectionClosed

LOG = []


async def handler(ws):
    peer = ws.remote_address
    print(f"[mock-vts] 连接: {peer}", flush=True)
    try:
        async for raw in ws:
            try:
                req = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mtype = req.get("messageType", "")
            data = req.get("data", {})
            resp = {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": req.get("requestID", "?"),
                "messageType": mtype,
                "data": {},
            }

            if mtype == "AuthenticationTokenRequest":
                resp["data"] = {"authenticationToken": "mock-token-abc123"}
                print("[mock-vts] 发放 token: mock-token-abc123", flush=True)
            elif mtype == "AuthenticationRequest":
                resp["data"] = {"authenticated": True, "reason": "ok"}
                print(
                    f"[mock-vts] 认证: {data.get('pluginName')} token={data.get('authenticationToken')}",
                    flush=True,
                )
            elif mtype == "InjectParameterDataRequest":
                params = data.get("parameterValues", [])
                LOG.append(params)
                print(f"[mock-vts] 注入参数: {json.dumps(params, ensure_ascii=False)}", flush=True)
                resp["data"] = {"valuesSet": len(params)}
            elif mtype == "ExpressionStateRequest":
                resp["data"] = {"expressions": [], "currentExpressionFile": "", "currentExpressionName": ""}
                print("[mock-vts] 查询表情: 空列表", flush=True)
            elif mtype == "ExpressionActivationRequest":
                print(f"[mock-vts] 表情 {data.get('expressionFile')} active={data.get('active')}", flush=True)
            elif mtype == "CurrentModelRequest":
                resp["data"] = {"modelLoaded": True, "modelName": "MockLive2D", "modelID": "mock"}
                print("[mock-vts] 模型信息: MockLive2D", flush=True)
            else:
                print(f"[mock-vts] 未处理消息: {mtype}", flush=True)
            await ws.send(json.dumps(resp))
    except ConnectionClosed:
        print("[mock-vts] 客户端断开", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[mock-vts] handler error: {e}", flush=True)


async def main():
    print("Mock VTS 监听 ws://127.0.0.1:8001 ...", flush=True)
    async with websockets.serve(handler, "127.0.0.1", 8001):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
