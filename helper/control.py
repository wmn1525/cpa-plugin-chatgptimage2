"""请求级截止时间与取消控制。"""

from __future__ import annotations

import threading
import time
from typing import Any

from helper.errors import HelperError


class RequestControl:
    """让排队和全部上游阶段共享同一个截止时间。"""

    def __init__(self, timeout_seconds: float) -> None:
        """按配置的总超时创建控制器。"""
        self.deadline = time.monotonic() + max(float(timeout_seconds), 0.01)
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._resources: set[Any] = set()

    def timeout(self, maximum: float | None = None) -> float:
        """返回不超过剩余总时长的单次网络超时。"""
        remaining = self.deadline - time.monotonic()
        if self._cancelled.is_set() or remaining <= 0:
            raise HelperError("ChatGPT 网页生图请求总超时", 504, "image_timeout")
        return max(0.001, min(remaining, maximum)) if maximum is not None else max(0.001, remaining)

    def sleep(self, seconds: float) -> None:
        """执行可被取消事件提前唤醒的等待。"""
        wait_seconds = min(max(seconds, 0.0), self.timeout())
        self._cancelled.wait(wait_seconds)
        self.timeout()

    def expired(self) -> bool:
        """检查请求是否已经取消或超过截止时间。"""
        return self._cancelled.is_set() or time.monotonic() >= self.deadline

    def register(self, resource: Any) -> None:
        """登记取消时需要关闭的网络资源。"""
        with self._lock:
            if self._cancelled.is_set():
                resource.close()
                raise HelperError("ChatGPT 网页生图请求已取消", 504, "image_timeout")
            self._resources.add(resource)

    def unregister(self, resource: Any) -> None:
        """移除已经主动释放的网络资源。"""
        with self._lock:
            self._resources.discard(resource)

    def cancel(self) -> None:
        """取消请求并关闭所有正在阻塞的网络会话。"""
        self._cancelled.set()
        with self._lock:
            resources = list(self._resources)
        for resource in resources:
            try:
                resource.close()
            except Exception:
                pass
