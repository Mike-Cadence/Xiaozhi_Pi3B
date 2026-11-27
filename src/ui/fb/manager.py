"""fbdev 界面 ViewPort — 实现 ui.shared.viewport.ViewPort Protocol."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Optional

from src.constants.constants import DeviceState
from src.core.event_bus import EventBus, Events
from src.logging import get_logger
from src.ui.shared.event_emitter import safe_emit

from .device import FbDevice
from .renderer import Renderer, UiState

if TYPE_CHECKING:
    from src.core.task_manager import TaskManager

logger = get_logger()


class FbViewManager:
    """基于 fbdev + Pillow 的无 GUI 界面.

    启动后会在后台跑一个 render loop（每 ~33 ms 一帧），
    订阅 UIPlugin 的 Presenter 方法（set_status / set_emotion /
    set_chat_text / set_music_line / set_button_text / set_auto_mode）
    以及 EventBus 的状态事件（DEVICE_STATE_CHANGED 等）。
    """

    def __init__(
        self,
        event_bus: EventBus,
        task_manager: Optional["TaskManager"] = None,
    ) -> None:
        self._event_bus = event_bus
        self._task_manager = task_manager
        self._fb: Optional[FbDevice] = None
        self._renderer: Optional[Renderer] = None
        self._state = UiState()
        self._running = False
        self._frame_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._subscriptions: list[tuple[str, object]] = []
        # display_text 推送文字的到期时间戳
        self._display_until = 0.0

    # -------------------------
    # ViewPort Protocol
    # -------------------------

    async def start(self, mode: str = "fb") -> None:
        logger.info("FbViewManager: 启动 fbdev 界面...")
        self._running = True
        self._loop = asyncio.get_running_loop()

        # 打开 fbdev
        try:
            self._fb = FbDevice()
        except FileNotFoundError:
            logger.error("/dev/fb0 不存在，fbdev 界面无法启动")
            self._running = False
            return
        except OSError as e:
            logger.error("打开 /dev/fb0 失败: %s", e)
            self._running = False
            return

        self._renderer = Renderer()
        self._fb.clear()

        logger.info(
            "FbViewManager: %dx%d %dbpp 已就绪",
            self._fb.width, self._fb.height, self._fb.info.bpp,
        )

        # 订阅 EventBus（UIPlugin 会通过 Presenter 调用方法，
        # 同时我们也直接接 DEVICE_STATE_CHANGED 以实时更新）
        self._subscribe_events()

        # 启动渲染循环，并在此阻塞直到界面关闭
        # （UIPlugin 约定：非 gui 模式下 start() 返回即代表生命周期结束）
        self._frame_task = self._loop.create_task(self._render_loop())
        try:
            await self._frame_task
        except asyncio.CancelledError:
            if self._running:
                raise

    async def close(self) -> None:
        logger.info("FbViewManager: 关闭...")
        self._running = False

        # 停止渲染循环
        task = self._frame_task
        self._frame_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # 退订
        for event, handler in self._subscriptions:
            try:
                self._event_bus.off(event, handler)
            except Exception:
                pass
        self._subscriptions.clear()

        # 清屏 + 关闭 fb
        if self._fb is not None:
            try:
                self._fb.clear()
            except Exception:
                pass
            self._fb.close()
            self._fb = None

        self._renderer = None
        logger.info("FbViewManager: 已关闭")

    # -------------------------
    # Presenter 回调（ViewPort Protocol）
    # -------------------------

    def set_status(self, status: str, connected: bool = True) -> None:
        self._state.status_text = status or ""
        self._state.connected = connected
        self._request_render()

    def set_chat_text(self, text: str) -> None:
        self._state.chat_text = text or ""
        self._request_render()

    def set_music_line(self, text: str) -> None:
        # fbdev 界面不单独分音乐区，直接写到 chat 区
        self._state.chat_text = text or ""
        self._request_render()

    def set_emotion(self, emotion: str) -> None:
        self._state.emotion = emotion or "neutral"
        self._request_render()

    def set_button_text(self, text: str) -> None:
        # fbdev 界面无按钮显示，忽略
        pass

    def set_auto_mode(self, auto_mode: bool) -> None:
        self._state.auto_mode = bool(auto_mode)
        self._request_render()

    # -------------------------
    # EventBus 订阅
    # -------------------------

    def _subscribe_events(self) -> None:
        bus = self._event_bus
        bus.on("device_state_changed", self._on_state_changed)
        self._subscriptions.append(("device_state_changed", self._on_state_changed))
        bus.on(Events.DISPLAY_TEXT, self._on_display_text)
        self._subscriptions.append((Events.DISPLAY_TEXT, self._on_display_text))

    async def _on_display_text(self, payload) -> None:
        """display_text MCP 工具推送的文字：屏上显示 10 秒."""
        text = payload.get("text", "") if isinstance(payload, dict) else payload
        text = str(text or "").strip()
        if not text:
            return
        self._state.display_text = text
        self._display_until = time.monotonic() + 10.0
        logger.info("fbdev 显示文字: %r", text)

    async def _on_state_changed(self, payload) -> None:
        new = payload.get("new_state") if isinstance(payload, dict) else payload
        if new is not None:
            try:
                # 统一存字符串，渲染层按 str 查表
                self._state.device_state = DeviceState(new).value
            except ValueError:
                logger.warning("未知设备状态: %r", new)
                return
        self._request_render()

    # -------------------------
    # 渲染循环
    # -------------------------

    def _request_render(self) -> None:
        """请求下一帧重绘（渲染循环会处理）."""
        # 当前实现：渲染循环以固定节拍跑，自动用到最新的 state
        pass

    def _render_frame(self, snapshot: UiState) -> None:
        """在工作线程中渲染一帧并写入 /dev/fb0（不占用事件循环）."""
        if self._renderer is None or self._fb is None:
            return
        arr = self._renderer.render(snapshot)
        self._fb.flush(arr)

    async def _render_loop(self, target_fps: float = 15.0) -> None:
        """固定节拍渲染循环.

        Pillow 全屏重绘在 Pi 3B 上约 30 ms，若放在事件循环里会挤占
        音频采集/唤醒词处理的时间片，因此渲染与写显存交给工作线程。
        """
        period = 1.0 / target_fps
        next_t = time.monotonic()
        frame_count = 0
        last_fps_t = next_t

        try:
            while self._running:
                # 推送文字到期后自动恢复圆环
                if self._state.display_text and time.monotonic() > self._display_until:
                    self._state.display_text = ""

                # 快照 + 线程渲染（避免边画边改，也不阻塞事件循环）
                snapshot = replace(self._state)
                await asyncio.to_thread(self._render_frame, snapshot)

                # 3. 节拍控制
                next_t += period
                sleep_for = next_t - time.monotonic()
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
                else:
                    # 渲染耗时超过 period，跳到下一个边界
                    next_t = time.monotonic() + period

                frame_count += 1
                now = time.monotonic()
                if now - last_fps_t >= 5.0:  # 每 5 秒打一次 fps
                    fps = frame_count / (now - last_fps_t)
                    logger.debug("fbdev 渲染 fps=%.1f", fps)
                    frame_count = 0
                    last_fps_t = now
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("fbdev 渲染循环出错")


