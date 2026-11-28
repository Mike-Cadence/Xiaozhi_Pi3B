"""Framebuffer 设备封装.

职责：
- 打开 /dev/fb0，mmap 映射
- 读取分辨率 / 色深 / stride
- numpy 向量化 RGB→RGB565 / BGRA→XRGB8888 转换
- 全屏 flush 或局部 row-by-row flush（stride 对齐）
"""

from __future__ import annotations

import mmap
import os
import subprocess
from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass(frozen=True)
class FbInfo:
    """Framebuffer 元信息."""

    width: int
    height: int
    bpp: int
    stride: int
    total_bytes: int


class FbDevice:
    """对 /dev/fb0 的薄封装.

    用法::

        fb = FbDevice()
        # 任何 numpy HxWx3 uint8 RGB 数组
        fb.flush(arr)
        # 或 Pillow 图像
        fb.flush_pillow(img)
        fb.close()
    """

    SYSFS = "/sys/class/graphics/fb0"
    # 面板倒装时置 True：转换后按像素整体倒序（≈一次 memcpy 的开销）
    ROTATE_180 = True

    def __init__(self, path: str = "/dev/fb0") -> None:
        self._path = path
        self._file = open(path, "r+b", buffering=0)
        self._info = self._read_info()
        self._mmap = mmap.mmap(
            self._file.fileno(),
            self._info.total_bytes,
            mmap.MAP_SHARED,
            mmap.PROT_READ | mmap.PROT_WRITE,
        )
        # fbcon 光标：开机自动干掉（systemd service 兜底），
        # 但进程启动时再试一次以防万一
        self._unbind_vtcon1()

    # -------------------------
    # 初始化
    # -------------------------

    def _read_info(self) -> FbInfo:
        def sysfs(name: str) -> str:
            with open(f"{self.SYSFS}/{name}") as f:
                return f.read().strip()

        w, h = map(int, sysfs("virtual_size").split(","))
        bpp = int(sysfs("bits_per_pixel"))
        stride = int(sysfs("stride"))
        return FbInfo(
            width=w,
            height=h,
            bpp=bpp,
            stride=stride,
            total_bytes=stride * h,
        )

    @staticmethod
    def _unbind_vtcon1() -> None:
        """尝试解除 fbcon 对 vtcon1 的绑定（避免光标闪烁）.

        静默失败——systemd service 开机已经做过，这里只是兜底。
        """
        try:
            with open("/sys/class/vtconsole/vtcon1/bind", "w") as f:
                f.write("0")
        except (OSError, FileNotFoundError):
            pass

    # -------------------------
    # 公开属性
    # -------------------------

    @property
    def info(self) -> FbInfo:
        return self._info

    @property
    def width(self) -> int:
        return self._info.width

    @property
    def height(self) -> int:
        return self._info.height

    def __enter__(self) -> "FbDevice":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._mmap.close()
        except Exception:
            pass
        try:
            self._file.close()
        except Exception:
            pass

    # -------------------------
    # 颜色空间转换
    # -------------------------

    def _rgb_to_framebuffer(self, rgb: np.ndarray) -> bytes:
        """RGB uint8 (H, W, 3) → framebuffer 原始字节."""
        arr = rgb.astype(np.uint16, copy=False)
        if self._info.bpp == 16:
            r = (arr[..., 0] >> 3) << 11
            g = (arr[..., 1] >> 2) << 5
            b = arr[..., 2] >> 3
            rgb565 = r | g | b
            return rgb565.tobytes("C")
        if self._info.bpp == 32:
            bgra = np.concatenate(
                [
                    rgb[:, :, ::-1].astype(np.uint8),
                    np.full((*rgb.shape[:2], 1), 255, dtype=np.uint8),
                ],
                axis=2,
            )
            return bgra.tobytes("C")
        raise ValueError(f"unsupported bpp={self._info.bpp}")

    # -------------------------
    # 写入
    # -------------------------

    def flush(self, rgb: np.ndarray) -> None:
        """全屏 flush：numpy RGB (H, W, 3) uint8 → fb0.

        图像尺寸必须与 framebuffer 匹配。
        """
        h, w, _ = rgb.shape
        if w != self._info.width or h != self._info.height:
            raise ValueError(
                f"shape {w}x{h} != fb {self._info.width}x{self._info.height}"
            )
        row_bytes = w * self._info.bpp // 8
        if self._info.bpp == 16 and self._info.stride == row_bytes:
            # 直接写 mmap 的 uint16 视图，省掉两次中间 bytes 拷贝
            dst = np.frombuffer(self._mmap, dtype="<u2", count=w * h)
            arr = rgb.astype(np.uint16)
            packed = (
                ((arr[..., 0] >> 3) << 11)
                | ((arr[..., 1] >> 2) << 5)
                | (arr[..., 2] >> 3)
            )
            if self.ROTATE_180:
                dst[:] = packed[::-1, ::-1].ravel()
            else:
                dst[:] = packed.ravel()
            return

        raw = self._rgb_to_framebuffer(rgb)
        if self.ROTATE_180:
            # 180° 等价于像素整体倒序：按 16/32 位元素反转，代价接近一次 memcpy
            dtype = "<u2" if self._info.bpp == 16 else "<u4"
            raw = np.frombuffer(raw, dtype=dtype)[::-1].tobytes()

        if self._info.stride == row_bytes:
            self._mmap[:] = raw
        else:
            for y in range(h):
                off = y * self._info.stride
                self._mmap[off : off + row_bytes] = raw[
                    y * row_bytes : (y + 1) * row_bytes
                ]

    def flush_rect(self, rgb: np.ndarray, x: int, y: int) -> None:
        """局部 flush：左上角 (x, y)，尺寸由 rgb 数组决定.

        ROTATE_180 时坐标与像素都会做 180° 映射。
        """
        h, w, _ = rgb.shape
        if self.ROTATE_180:
            rgb = rgb[::-1, ::-1]
            x = self._info.width - x - w
            y = self._info.height - y - h
        raw = self._rgb_to_framebuffer(rgb)
        row_bytes = w * self._info.bpp // 8
        bytes_pp = self._info.bpp // 8
        for yy in range(h):
            off = (y + yy) * self._info.stride + x * bytes_pp
            self._mmap[off : off + row_bytes] = raw[
                yy * row_bytes : (yy + 1) * row_bytes
            ]

    def clear(self, color: tuple[int, int, int] = (0, 0, 0)) -> None:
        """清屏：填充纯色."""
        w, h = self._info.width, self._info.height
        arr = np.zeros((h, w, 3), dtype=np.uint8)
        arr[..., 0] = color[0]
        arr[..., 1] = color[1]
        arr[..., 2] = color[2]
        self.flush(arr)
