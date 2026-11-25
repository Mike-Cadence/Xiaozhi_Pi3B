"""显示 MCP 工具（把文字投到设备屏幕）.

- register_display_tools: 向 McpServer 注册 display_text
"""

from .register import register_display_tools

__all__ = ["register_display_tools"]
