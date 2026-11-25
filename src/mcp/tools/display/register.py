"""display MCP 工具：把文字投到设备屏幕.

当前阶段（无显示器、未实现 fbdev）工具只做两件事：
1. 通过 EventBus 广播 DISPLAY_TEXT 事件，供未来的 fb 屏幕视图订阅；
2. 打印结构化日志，作为“后端 LLM → 设备端工具”调用链的可见证据。

工具回调不依赖任何硬件，因此在任何模式/平台下都可注册并验证。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from src.logging import get_logger
from src.mcp.tooling import McpTool, Property, PropertyList, PropertyType

if TYPE_CHECKING:
    from src.core.event_bus import EventBus

logger = get_logger()


def register_display_tools(
    add_tool: Callable[[McpTool], None], event_bus: "EventBus"
) -> None:
    """向 McpServer 注册 display_text（闭包持有容器注入的 event_bus）."""

    async def display_text(args: dict[str, Any]) -> str:
        text = args.get("text", "")
        logger.info(
            "[DisplayTool] display_text 被调用 length=%d text=%r",
            len(text),
            text,
        )

        # 广播给 UI 层（未来的 fb 屏幕视图订阅此事件）。
        # 当前无订阅者时 emit 为 no-op，不影响工具返回。
        try:
            from src.core.event_bus import Events

            await event_bus.emit(Events.DISPLAY_TEXT, text)
        except Exception as e:
            logger.error(
                "[DisplayTool] 广播 DISPLAY_TEXT 失败: %s", e, exc_info=True
            )
            return json.dumps(
                {"displayed": False, "text": text, "error": str(e)},
                ensure_ascii=False,
            )

        logger.info("[DisplayTool] 已广播 DISPLAY_TEXT text=%r", text)
        return json.dumps(
            {"displayed": True, "text": text, "length": len(text)},
            ensure_ascii=False,
        )

    add_tool(
        McpTool(
            "display_text",
            (
                "在设备屏幕上显示指定的文字（通常是汉字）。"
                "当用户询问某个字怎么写、怎么读、长什么样，"
                "或要求“显示/写出/看看某个字”时，必须调用本工具，"
                "把要展示的文字原文传给 text。"
                "参数: text - 要显示的文字，例如单个汉字“疆”或一个短词。"
            ),
            PropertyList([Property("text", PropertyType.STRING)]),
            display_text,
        )
    )
    logger.info("已注册 display_text MCP 工具")
