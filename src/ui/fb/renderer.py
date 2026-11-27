"""Pillow 渲染层 — 画状态界面.

状态标识统一用字符串 "idle" / "listening" / "speaking" / "notifying".
既兼容 DeviceState 枚举（.value 刚好也是这些字符串），
也兼容独立测试场景（不依赖 src.constants）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# 竖屏 480×800 布局
W, H = 480, 800

# 颜色（RGB）
BG = (15, 17, 22)           # 深空黑
TEXT = (235, 240, 250)      # 近白
TEXT_DIM = (130, 140, 160)  # 灰蓝
ACCENT = (70, 180, 255)     # 青蓝
RED = (240, 80, 90)
GREEN = (80, 220, 140)
YELLOW = (240, 210, 80)
PURPLE = (170, 120, 255)

# 全部用字符串 key —— 解耦 DeviceState 枚举
STATE_COLORS: dict[str, tuple[int, int, int]] = {
    "idle": TEXT_DIM,
    "listening": ACCENT,
    "speaking": PURPLE,
    "notifying": YELLOW,
}

STATE_LABELS: dict[str, str] = {
    "idle": "待机",
    "listening": "聆听中",
    "speaking": "说话中",
    "notifying": "通知中",
}

STATE_ICONS: dict[str, str] = {
    "idle": "○",
    "listening": "◎",
    "speaking": "▶",
    "notifying": "!" ,
}

# 默认状态
DEFAULT_STATE = "idle"


@dataclass
class UiState:
    """渲染器的可变状态.

    多个字段会被异步更新（设备状态、文字、波形），
    渲染帧开始时一次性读快照，避免边画边改。
    """

    device_state: str = DEFAULT_STATE
    status_text: str = "就绪"
    chat_text: str = ""       # STT 转写 / TTS 字幕
    emotion: str = "neutral"
    auto_mode: bool = False
    connected: bool = True
    # display_text MCP 工具推送的文字（空表示不显示）
    display_text: str = ""


class Renderer:
    """单帧生成器：每帧拿到一个 UiState 快照，返回 HxWx3 numpy RGB."""

    def __init__(self) -> None:
        self._font_small = self._load_font(18)
        self._font_cjk = self._load_cjk_font(22)
        self._font_cjk_large = self._load_cjk_font(30)
        # display_text 用的大字号按需加载并缓存
        self._font_cache: dict[int, ImageFont.ImageFont] = {}
        # 楷体字号缓存（display_text 专用）
        self._kai_cache: dict[int, ImageFont.ImageFont] = {}
        # 复用画布
        self._img = Image.new("RGB", (W, H), BG)
        self._draw = ImageDraw.Draw(self._img)

    # -------------------------
    # 字体
    # -------------------------

    @staticmethod
    def _load_font(size: int) -> ImageFont.ImageFont:
        for path in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        return ImageFont.load_default()

    @staticmethod
    def _load_cjk_font(size: int) -> ImageFont.ImageFont:
        for path in (
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        ):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        return ImageFont.load_default()

    # -------------------------
    # 公共 API
    # -------------------------

    def render(self, s: UiState) -> np.ndarray:
        """根据状态快照生成完整帧，返回 numpy RGB (H, W, 3)."""
        d = self._draw
        d.rectangle([0, 0, W, H], fill=BG)
        self._draw_header(d, s)
        self._draw_emotion_area(d, s)
        self._draw_text_area(d, s)
        return np.array(self._img)

    # -------------------------
    # 分区绘制
    # -------------------------

    # -------------------------
    # 状态图标：全部用 Pillow 画，不用 Unicode 字符
    # -------------------------

    @staticmethod
    def _draw_shape(d: ImageDraw.ImageDraw, state: str, cx: int, cy: int,
                    size: int, color: tuple[int, int, int]) -> None:
        """在 (cx, cy) 画一个 2size×2size 的纯形状."""
        s = size
        if state == "idle":
            # 空心圆 + 中心点
            d.ellipse([cx - s, cy - s, cx + s, cy + s], outline=color, width=3)
            d.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=color)
        elif state == "listening":
            # 实心圆 + 外环（表示"有东西进入"）
            d.ellipse([cx - s - 4, cy - s - 4, cx + s + 4, cy + s + 4],
                      outline=color, width=2)
            d.ellipse([cx - s, cy - s, cx + s, cy + s], fill=color)
        elif state == "speaking":
            # 向右三角（播放/发声）
            points = [
                (cx - s, cy - s),
                (cx - s, cy + s),
                (cx + s, cy),
            ]
            d.polygon(points, fill=color)
        elif state == "notifying":
            # 方尖碑 + 小点（感叹号）
            d.rectangle([cx - s // 2, cy - s, cx + s // 2, cy - 2], fill=color)
            d.ellipse([cx - 4, cy + 2, cx + 4, cy + 10], fill=color)
        else:
            d.ellipse([cx - s, cy - s, cx + s, cy + s], fill=color)

    @staticmethod
    def _draw_wifi_icon(d: ImageDraw.ImageDraw, cx: int, cy: int,
                        color: tuple[int, int, int]) -> None:
        """手绘 WiFi 图标：三层弧 + 底部小点."""
        for i, r in enumerate((18, 12, 6)):
            d.arc([cx - r, cy - r, cx + r, cy + r], start=210, end=330,
                  fill=color, width=2)
        d.ellipse([cx - 3, cy - 1, cx + 3, cy + 5], fill=color)

    def _draw_header(self, d: ImageDraw.ImageDraw, s: UiState) -> None:
        """重绘版 header：高 80px，所有元素用纯色形状，清晰可见."""
        color = STATE_COLORS.get(s.device_state, TEXT_DIM)
        label = STATE_LABELS.get(s.device_state, s.device_state)

        # 背景条
        d.rectangle([0, 0, W - 1, 80], fill=(25, 30, 40))
        # 底边线
        d.line([0, 80, W - 1, 80], fill=(60, 70, 90), width=2)

        # 1. 左侧：状态圆点（大）+ 形状
        dot_cx, dot_cy = 25, 40
        d.ellipse([dot_cx - 15, dot_cy - 15, dot_cx + 15, dot_cy + 15],
                  fill=color)
        shape_cx, shape_cy = 70, 40
        self._draw_shape(d, s.device_state, shape_cx, shape_cy, 15, color)

        # 2. 中间：中文标签（居中，32px CJK）
        try:
            tw = d.textlength(label, font=self._font_cjk_large)
        except AttributeError:
            tw = len(label) * 20
        d.text(((W - tw) // 2, 24), label, font=self._font_cjk_large, fill=TEXT)

        # 3. 右侧：WiFi 图标
        wifi_color = GREEN if s.connected else RED
        self._draw_wifi_icon(d, W - 45, 40, wifi_color)

    @staticmethod
    def _load_kai_font(size: int) -> ImageFont.ImageFont:
        """楷体：优先 AR PL UKai，找不到退回 CJK 黑体."""
        for path in (
            "/usr/share/fonts/truetype/arphic/ukai.ttc",
            "/usr/share/fonts/truetype/arphic-gkai00mp/gkai00mp.ttf",
        ):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        return Renderer._load_cjk_font(size)

    def _kai_font(self, size: int) -> ImageFont.ImageFont:
        font = self._kai_cache.get(size)
        if font is None:
            font = self._load_kai_font(size)
            self._kai_cache[size] = font
        return font

    def _cjk_font(self, size: int) -> ImageFont.ImageFont:
        """按字号取 CJK 字体（缓存，避免反复加载）."""
        font = self._font_cache.get(size)
        if font is None:
            font = self._load_cjk_font(size)
            self._font_cache[size] = font
        return font

    def _draw_display_text(self, d: ImageDraw.ImageDraw, text: str) -> None:
        """在屏幕中部用最大可用字号显示推送的文字."""
        max_w, max_h = W - 40, 500
        font, size, lines = self._kai_font(30), 30, [text]
        for candidate in (200, 180, 160, 140, 120, 100, 84, 72, 60, 48, 36):
            f = self._kai_font(candidate)
            wrapped = self._wrap_text(d, text, max_w, f)
            if len(wrapped) * int(candidate * 1.25) <= max_h:
                font, size, lines = f, candidate, wrapped
                break

        line_h = int(size * 1.25)
        cy = 360  # 上方状态栏 80、下方文字区 630 之间的视觉中心
        y = cy - len(lines) * line_h // 2
        for line in lines:
            d.text((W // 2, y + line_h // 2), line,
                   font=font, fill=ACCENT, anchor="mm")
            y += line_h

    def _draw_emotion_area(self, d: ImageDraw.ImageDraw, s: UiState) -> None:
        # display_text 优先：有推送文字时替换圆环
        if s.display_text:
            self._draw_display_text(d, s.display_text)
            return

        cx, cy = W // 2, 220
        r = 115
        pulse = 0.9 + 0.1 * np.sin(time.monotonic() * 3)
        ring_r = int(r * pulse)
        color = STATE_COLORS.get(s.device_state, TEXT_DIM)

        for i, a in enumerate(range(6, 0, -2)):
            d.ellipse(
                [cx - ring_r - a, cy - ring_r - a,
                 cx + ring_r + a, cy + ring_r + a],
                outline=color, width=1,
            )
        d.ellipse(
            [cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r],
            fill=(30, 35, 45),
            outline=color,
            width=3,
        )

    def _draw_text_area(self, d: ImageDraw.ImageDraw, s: UiState) -> None:
        top = 630
        d.line([10, top, W - 10, top], fill=(40, 48, 60), width=1)

        d.text(
            (20, top + 10), s.status_text or "",
            font=self._font_cjk, fill=TEXT_DIM,
        )
        chat = (s.chat_text or "")[:38]
        lines = self._wrap_text(d, chat, W - 40, self._font_cjk)
        y = top + 42
        for line in lines[:2]:
            d.text((20, y), line, font=self._font_cjk, fill=TEXT)
            y += 32

        mode_text = "自动" if s.auto_mode else "手动"
        d.text((20, H - 28), mode_text, font=self._font_small,
               fill=ACCENT if s.auto_mode else TEXT_DIM)
        t = time.strftime("%H:%M")
        d.text((W - 20, H - 28), t, font=self._font_small,
               fill=TEXT_DIM, anchor="ra")

    @staticmethod
    def _wrap_text(
        d: ImageDraw.ImageDraw, text: str, max_w: int, font: ImageFont.ImageFont
    ) -> list[str]:
        if not text:
            return []
        result: list[str] = []
        line = ""
        for ch in text:
            test = line + ch
            try:
                bbox = d.textbbox((0, 0), test, font=font)
                w = bbox[2] - bbox[0]
            except AttributeError:
                w = d.textlength(test, font=font)
            if w <= max_w or not line:
                line = test
            else:
                result.append(line)
                line = ch
        if line:
            result.append(line)
        return result
