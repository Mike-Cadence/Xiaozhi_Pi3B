#!/usr/bin/env python3
"""把小智智能体的 MCP 接入点接到本机 MCP server（仅验证工具调用链）.

小智云端在 MCP 接入点里扮演 MCP client：本脚本主动连出去，作为 MCP server
等待云端发送 initialize / tools/list / tools/call。默认只暴露 display_text，
工具真正执行时会在控制台打印 ``>>> [屏幕] <文字>``，用于验证
“后端 LLM → 设备端 MCP 工具”是否打通。

用法（PowerShell）:
    $env:XIAOZHI_MCP_ENDPOINT = "wss://api.xiaozhi.me/mcp/?token=..."
    python scripts/xiaozhi_mcp_endpoint.py

安全提示:
    URL 里的 token 等同密码，不要提交到 git、不要发到公开聊天。
    脚本只打印脱敏后的地址；请从控制台重新复制，不要复用已泄露的 token。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import websockets  # noqa: E402
from websockets.exceptions import InvalidStatus  # noqa: E402

from src.core.event_bus import EventBus, Events  # noqa: E402
from src.mcp.mcp_server import McpServer  # noqa: E402
from src.mcp.tools.display import register_display_tools  # noqa: E402

FRAME_PREVIEW = 800


def _token_of(url: str) -> str:
    values = parse_qs(urlsplit(url).query).get("token", [])
    return values[0] if values else ""


def _redact(text: str, url: str) -> str:
    token = _token_of(url)
    if token:
        text = text.replace(token, "***")
    return text


def _redact_url(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}?token=***"


def _jwt_payload(token: str) -> dict | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, json.JSONDecodeError):
        return None


def _format_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().strftime(
        "%Y-%m-%d %H:%M:%S %z"
    )


def print_diagnostics(url: str) -> None:
    """只检查 URL/token 结构，不发起连接，也不打印 token 本身."""
    token = _token_of(url)
    print("== 接入点诊断 ==", flush=True)
    print(f"地址        : {_redact_url(url)}", flush=True)
    print(f"token 长度  : {len(token)}", flush=True)
    print(f"token 段数  : {len(token.split('.'))}（标准 JWT 应为 3）", flush=True)

    if not token:
        print("!! URL 里没有 token 参数", flush=True)
        return
    if any(ch.isspace() for ch in token):
        print("!! token 中含空格/换行，复制时被截断了，请重新复制", flush=True)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", token):
        print(
            "!! token 含非法字符（可能带入了标点、中文或零宽字符），请重新复制",
            flush=True,
        )
    if len(token) < 100:
        print("!! token 明显过短，基本可以确定复制不完整", flush=True)

    payload = _jwt_payload(token)
    if payload is None:
        print("!! token 不是标准 JWT，极可能是复制不完整或带了多余字符", flush=True)
        return

    print(f"purpose     : {payload.get('purpose')}", flush=True)
    print(f"agentId     : {payload.get('agentId')}", flush=True)

    exp = payload.get("exp")
    if isinstance(exp, (int, float)):
        print(f"过期时间    : {_format_ts(int(exp))}", flush=True)
        if exp < time.time():
            print("!! token 已过期，请在控制台重新生成", flush=True)
        else:
            print("token 时间  : 未过期", flush=True)


def _print_handshake_error(exc: InvalidStatus, url: str) -> None:
    response = exc.response
    print(f"!! 握手被拒: HTTP {response.status_code}", flush=True)
    for key, value in response.headers.items():
        print(f"   {key}: {_redact(str(value), url)}", flush=True)

    body = getattr(response, "body", None)
    if body:
        text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
        print(f"   body: {_redact(text[:300], url)}", flush=True)


def build_server(event_bus: EventBus) -> McpServer:
    server = McpServer()
    register_display_tools(server.add_tool, event_bus)
    return server


async def _on_display(text: str = "") -> None:
    print(f"\n>>> [屏幕] {text}\n", flush=True)


async def _handle_frame(server: McpServer, ws, raw, url: str) -> None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    print(f"<< {_redact(raw, url)[:FRAME_PREVIEW]}", flush=True)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("!! 收到非 JSON 帧，已忽略", flush=True)
        return

    method = data.get("method")
    request_id = data.get("id")

    # 云端的 initialize 直接应答，兼容不同 protocolVersion；其余交给 McpServer。
    if method == "initialize" and request_id is not None:
        params = data.get("params") or {}
        reply = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "py-xiaozhi-display", "version": "0.1.0"},
            },
        }
        await ws.send(json.dumps(reply, ensure_ascii=False))
        return

    # MCP 基础方法：补齐空结果，避免云端因未实现而断开。
    if request_id is not None:
        empty_results = {
            "ping": {},
            "resources/list": {"resources": []},
            "prompts/list": {"prompts": []},
        }
        if method in empty_results:
            await ws.send(
                json.dumps(
                    {"jsonrpc": "2.0", "id": request_id, "result": empty_results[method]},
                    ensure_ascii=False,
                )
            )
            return

    if method and method.startswith("notifications/"):
        print(f"-- 通知 {method}（无需回复）", flush=True)
        return

    if method == "tools/call":
        params = data.get("params") or {}
        print(
            f"\n*** tools/call name={params.get('name')} "
            f"arguments={params.get('arguments')} ***",
            flush=True,
        )

    await server.parse_message(data)


async def _run_once(url: str, server: McpServer, auth_mode: str = "query") -> None:
    connect_kwargs: dict = {
        "ping_interval": 30,
        "ping_timeout": 10,
        "max_size": 1 << 20,
    }
    if auth_mode == "both":
        connect_kwargs["additional_headers"] = {
            "Authorization": f"Bearer {_token_of(url)}"
        }

    async with websockets.connect(url, **connect_kwargs) as ws:

        async def send(payload: str) -> None:
            print(f">> {_redact(payload, url)[:FRAME_PREVIEW]}", flush=True)
            await ws.send(payload)

        server.set_send_callback(send)
        print(f"== 已连接 {_redact_url(url)}，等待云端 initialize ... ==", flush=True)
        try:
            async for raw in ws:
                await _handle_frame(server, ws, raw, url)
        finally:
            server.set_send_callback(None)
            print("== 连接断开 ==", flush=True)


async def main_async(url: str, auth_mode: str = "query") -> None:
    print_diagnostics(url)
    print(f"鉴权方式    : {auth_mode}", flush=True)

    event_bus = EventBus()
    event_bus.on(Events.DISPLAY_TEXT, _on_display)
    server = build_server(event_bus)

    delay = 1
    while True:
        try:
            await _run_once(url, server, auth_mode)
            delay = 1
        except asyncio.CancelledError:
            raise
        except InvalidStatus as exc:
            _print_handshake_error(exc, url)
            print(
                "!! 鉴权失败，已停止重连。请检查：\n"
                "   1) 控制台是否重新生成过 token（旧 token 会立即失效）\n"
                "   2) 智能体是否已启用/发布，MCP 接入点是否开启\n"
                "   3) 是否有另一个客户端占用了同一个接入点\n"
                "   4) URL 是否完整复制（token 通常 300+ 字符）",
                flush=True,
            )
            return
        except Exception as exc:  # noqa: BLE001 - 断线重连是预期行为
            print(f"!! 连接失败: {_redact(str(exc), url)}", flush=True)

        print(f".. {delay}s 后重连", flush=True)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="连接小智 MCP 接入点，暴露 display_text 用于验证工具调用链"
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("XIAOZHI_MCP_ENDPOINT", ""),
        help="MCP 接入点地址；默认读环境变量 XIAOZHI_MCP_ENDPOINT",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只检查 URL/token 结构，不发起连接",
    )
    parser.add_argument(
        "--auth",
        choices=("query", "both"),
        default="query",
        help="query: 只用 ?token=（默认）；both: 同时带 Authorization: Bearer",
    )
    args = parser.parse_args()

    url = args.url.strip().strip('"').strip("'")
    if not url:
        parser.error("请设置 XIAOZHI_MCP_ENDPOINT，或通过 --url 传入")
    if not url.startswith(("ws://", "wss://")):
        parser.error("接入点地址必须以 ws:// 或 wss:// 开头")

    if args.check:
        print_diagnostics(url)
        return

    try:
        asyncio.run(main_async(url, args.auth))
    except KeyboardInterrupt:
        print("\n已退出", flush=True)


if __name__ == "__main__":
    main()
