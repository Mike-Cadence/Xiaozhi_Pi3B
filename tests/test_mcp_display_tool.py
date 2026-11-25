"""验证 display_text MCP 工具的注册、调用链与事件广播（无硬件依赖）."""

import json

import pytest

from src.core.event_bus import EventBus, Events
from src.mcp.mcp_server import McpServer
from src.mcp.tools.display import register_display_tools


def _server_with_display() -> tuple[McpServer, EventBus]:
    bus = EventBus()
    server = McpServer()
    register_display_tools(server.add_tool, bus)
    return server, bus


async def _call_tool(server: McpServer, name: str, arguments: dict | None) -> dict:
    messages: list[str] = []

    async def send(payload: str) -> None:
        messages.append(payload)

    server.set_send_callback(send)
    await server._handle_tool_call(request_id=42, params={"name": name, "arguments": arguments})
    assert messages, "工具调用未产生任何响应"
    return json.loads(messages[0])


async def _list_tools(server: McpServer) -> list[dict]:
    messages: list[str] = []

    async def send(payload: str) -> None:
        messages.append(payload)

    server.set_send_callback(send)
    await server._handle_tools_list(request_id=1, params={"cursor": ""})
    reply = json.loads(messages[0])
    return reply["result"]["tools"]


@pytest.mark.asyncio
async def test_display_text_is_listed_with_required_string_schema():
    server, _ = _server_with_display()
    tools = await _list_tools(server)
    names = [t["name"] for t in tools]
    assert "display_text" in names

    entry = next(t for t in tools if t["name"] == "display_text")
    schema = entry["inputSchema"]
    assert schema["type"] == "object"
    assert schema["required"] == ["text"]
    assert schema["properties"]["text"]["type"] == "string"


@pytest.mark.asyncio
async def test_display_text_call_returns_success_and_echoes_text():
    server, _ = _server_with_display()
    reply = await _call_tool(server, "display_text", {"text": "疆"})

    assert "error" not in reply
    result = reply["result"]
    assert result["isError"] is False
    content = result["content"]
    assert content[0]["type"] == "text"

    # content[0].text 是回调返回的 JSON 字符串，服务端 LLM 会据此判断是否成功
    payload = json.loads(content[0]["text"])
    assert payload["displayed"] is True
    assert payload["text"] == "疆"
    assert payload["length"] == 1


@pytest.mark.asyncio
async def test_display_text_broadcasts_event_to_subscribers():
    server, bus = _server_with_display()
    received: list[str] = []

    async def recorder(text: str = "") -> None:
        received.append(text)

    bus.on(Events.DISPLAY_TEXT, recorder)
    await _call_tool(server, "display_text", {"text": "你好小智"})

    assert received == ["你好小智"]


@pytest.mark.asyncio
async def test_display_text_missing_argument_returns_error():
    server, _ = _server_with_display()
    reply = await _call_tool(server, "display_text", {})

    result = reply["result"]
    assert result["isError"] is True
    # 错误信息不应暴露为 success，便于服务端定位参数问题
    assert "Missing required argument" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_display_text_rejects_non_string_argument():
    server, _ = _server_with_display()
    reply = await _call_tool(server, "display_text", {"text": 123})

    result = reply["result"]
    assert result["isError"] is True
