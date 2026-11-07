"""真实速度统计。

- current：最近 N 秒滑动窗口内的实际字节速率。
- average：活跃传输时间（不含暂停与重试等待）内的平均速率。
- peak：窗口速率的历史峰值。

所有数字均来自真实读取的字节计数，没有任何模拟或外推。
"""

from __future__ import annotations

import time
from collections import deque


class SpeedMeter:
    # 峰值只统计窗口跨度达到该下限后的速率，避免首个数据块造成的瞬时尖刺
    MIN_PEAK_SPAN = 0.25

    def __init__(self, window_seconds: float = 5.0) -> None:
        self.window_seconds = window_seconds
        self.total_bytes = 0
        self._samples: deque[tuple[float, int]] = deque()
        self._peak = 0.0
        self._segment_start: float | None = None
        self._active_seconds = 0.0

    def on_bytes(self, count: int) -> None:
        if count <= 0:
            return
        now = time.monotonic()
        if self._segment_start is None:
            self._segment_start = now
        self.total_bytes += count
        self._samples.append((now, count))
        speed = self._window_speed(now)
        if speed > self._peak:
            self._peak = speed

    def pause_segment(self) -> None:
        """结束当前活跃传输段（暂停 / 重试等待 / 完成时调用）。"""
        if self._segment_start is not None:
            self._active_seconds += max(0.0, time.monotonic() - self._segment_start)
            self._segment_start = None

    def _window_speed(self, now: float) -> float:
        cutoff = now - self.window_seconds
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()
        if not self._samples:
            return 0.0
        # span 下限 = MIN_PEAK_SPAN：单个突发块不会产生失真的瞬时尖刺
        span = max(now - self._samples[0][0], self.MIN_PEAK_SPAN)
        return sum(n for _, n in self._samples) / span

    def current_bps(self) -> float:
        if self._segment_start is None:
            return 0.0
        return self._window_speed(time.monotonic())

    def average_bps(self) -> float:
        active = self._active_seconds
        if self._segment_start is not None:
            active += max(0.0, time.monotonic() - self._segment_start)
        if active <= 0:
            return 0.0
        return self.total_bytes / active

    def peak_bps(self) -> float:
        return self._peak
