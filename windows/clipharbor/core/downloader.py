"""HTTP/HTTPS 下载器核心（仅标准库）。

- 支持断点续传（Range）；服务器忽略 Range 时自动从头下载。
- 支持分段并发下载（服务器支持 Range 且大小已知时），每段独立断点续传。
- 每个数据块之间检查暂停 / 取消控制信号。
- 下载前检查磁盘剩余空间；写入中遇到 ENOSPC 会被归类为空间不足。
- 目标路径一律通过 fsutil.open_within 打开（realpath + 目录包含校验，防穿越）。
- 完成后校验文件大小；SHA-256 由调用方对最终文件统一计算。
"""

from __future__ import annotations

import errno
import os
import re
import shutil
import socket
import threading
import time
import urllib.error
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait

from .fsutil import open_within, truncate_within
from .http_transport import open_url
from .probe import USER_AGENT
from .url_guard import validate_url

CHUNK_SIZE = 256 * 1024
_SEG_SUFFIX = ".clipharbor.seg"


class DownloadPaused(Exception):
    """用户请求暂停。"""


class DownloadCancelled(Exception):
    """用户请求取消。"""


class UnsupportedSourceError(ValueError):
    """来源类型当前不受支持（例如需要专门适配器的站点）。"""


class InsufficientSpaceError(OSError):
    """磁盘剩余空间不足。"""


class VerificationError(Exception):
    """下载完成后的校验失败。"""


class Control:
    """协作式控制面：下载器在每个数据块之间读取这些标志。"""

    __slots__ = ("paused", "cancelled")

    def __init__(self) -> None:
        self.paused = False
        self.cancelled = False


def classify_error(exc: BaseException) -> tuple[bool, str]:
    """返回 (是否可重试, 错误类别)。"""
    if isinstance(exc, InsufficientSpaceError):
        return False, "insufficient_space"
    if isinstance(exc, VerificationError):
        return False, "verification_failed"
    if isinstance(exc, UnsupportedSourceError):
        return False, "unsupported_source"
    if isinstance(exc, urllib.error.HTTPError):
        code = exc.code
        if code == 429 or 500 <= code < 600:
            return True, f"http_{code}"
        return False, f"http_{code}"
    if isinstance(exc, OSError):
        if exc.errno in (errno.ENOSPC, errno.EDQUOT):
            return False, "insufficient_space"
        if getattr(exc, "winerror", None) == 32:  # 文件被占用（共享冲突），通常可重试
            return True, "file_busy"
        if exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
            return False, "filesystem_denied"
        return True, "network"
    if isinstance(
        exc,
        (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError),
    ):
        return True, "network"
    if isinstance(exc, ValueError):
        return False, "invalid_url"
    return True, "unknown"


class Downloader:
    def __init__(
        self,
        *,
        control: Control | None = None,
        read_timeout: float = 30.0,
        chunk_size: int = CHUNK_SIZE,
        user_agent: str = USER_AGENT,
        allow_private: bool = False,
    ) -> None:
        self.control = control or Control()
        self.read_timeout = read_timeout
        self.chunk_size = chunk_size
        self.user_agent = user_agent
        self.allow_private = allow_private

    def _guard_disk(self, dest_dir: str, expected_total: int, resume_from: int,
                    *, factor: int = 1) -> None:
        margin = 8 * 1024 * 1024
        needed = (max(expected_total - resume_from, 0) if expected_total else 0) * factor + margin
        free = shutil.disk_usage(dest_dir).free
        if free < needed:
            raise InsufficientSpaceError(
                errno.ENOSPC,
                f"磁盘空间不足: 需要 >= {needed} 字节, 剩余 {free} 字节",
            )

    def download(
        self,
        url: str,
        dest_path: str,
        *,
        resume_from: int = 0,
        expected_total: int = 0,
        on_progress=None,
        extra_headers: dict | None = None,
    ) -> dict:
        """下载到 dest_path（.part 分片文件由调用方管理并保证位于 dest_dir 内）。

        on_progress(downloaded_including_resume) 在每个数据块后调用。
        extra_headers 为来源适配器要求的附加请求头（如 Referer）。
        返回 {"bytes": 最终字节数, "elapsed": 耗时秒}。
        """
        validate_url(url, allow_private=self.allow_private)
        dest_dir = os.path.dirname(os.path.abspath(dest_path)) or "."
        part_file = dest_path
        os.makedirs(dest_dir, exist_ok=True)
        started = time.monotonic()
        base = resume_from
        restarts = 0

        while True:
            self._guard_disk(dest_dir, expected_total, base)
            headers = {"User-Agent": self.user_agent, "Accept": "*/*"}
            if extra_headers:
                headers.update(extra_headers)
            if base > 0:
                headers["Range"] = f"bytes={base}-"
            result: dict = {}
            with open_url(url, headers=headers, timeout=self.read_timeout,
                          allow_private=self.allow_private) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                if status not in (200, 206):
                    raise urllib.error.URLError(f"服务器返回异常状态码 {status}")
                if base > 0:
                    if status == 206:
                        cr = resp.headers.get("Content-Range", "") or ""
                        m = re.match(r"bytes\s+(\d+)-", cr)
                        start = int(m.group(1)) if m else None
                        if start != base:
                            if restarts >= 1:
                                raise VerificationError("续传起点与服务器不一致")
                            restarts += 1
                            base = 0
                            continue
                    else:
                        # 服务器忽略 Range：丢弃本地分片，从头接收本次响应
                        base = 0
                        truncate_within(dest_dir, part_file)
                cl = resp.headers.get("Content-Length", "") or ""
                remaining = int(cl) if cl.isdigit() else 0
                mode = "ab" if base > 0 else "wb"
                written = 0
                with open_within(dest_dir, part_file, mode) as fh:
                    while True:
                        if self.control.cancelled:
                            raise DownloadCancelled()
                        if self.control.paused:
                            raise DownloadPaused()
                        try:
                            chunk = resp.read(self.chunk_size)
                        except socket.timeout:
                            raise TimeoutError("读取数据超时") from None
                        if not chunk:
                            break
                        fh.write(chunk)
                        written += len(chunk)
                        if on_progress:
                            on_progress(base + written)
                    fh.flush()
                    os.fsync(fh.fileno())
                total_now = base + written
                if remaining and remaining != written:
                    raise VerificationError(
                        f"响应提前结束: 收到 {written} 字节, Content-Length 为 {remaining}"
                    )
                if expected_total and total_now != expected_total:
                    raise VerificationError(
                        f"文件大小校验失败: 得到 {total_now}, 期望 {expected_total}"
                    )
                result = {"bytes": total_now, "elapsed": time.monotonic() - started}
            return result

    # ---- 分段并发下载 ------------------------------------------------------

    def _seg_path(self, part_dir: str, part_name: str, index: int) -> str:
        return os.path.join(part_dir, f"{part_name}.seg{index}{_SEG_SUFFIX}")

    def segment_files(self, part_dir: str, part_name: str, max_segments: int) -> list[str]:
        """列出该分片已存在的段文件（供断点判断与清理）。"""
        return [p for p in (self._seg_path(part_dir, part_name, i)
                            for i in range(max_segments))
                if os.path.exists(p)]

    def download_segmented(
        self,
        url: str,
        part_dir: str,
        part_name: str,
        *,
        segments: int,
        expected_total: int,
        on_progress=None,
        extra_headers: dict | None = None,
    ) -> dict:
        """分段并发下载（要求 expected_total 已知且服务器支持 Range）。

        每段写入独立段文件，可独立断点续传；全部完成后顺序拼接为 part_name，
        然后删除段文件。拼接期间同目录会短暂出现约 2 倍文件体积占用，
        因此磁盘预留按 2 倍计算。
        on_progress(累计已下载字节, 含既有分段) 在每个数据块后调用。
        """
        validate_url(url, allow_private=self.allow_private)
        part_dir_abs = os.path.abspath(part_dir) or "."
        part_name = os.path.basename(part_name)
        started = time.monotonic()
        seg_len = max(1, -(-expected_total // max(segments, 1)))  # ceil
        plan: list[tuple[int, int, int]] = []
        for i in range(max(segments, 1)):
            start = i * seg_len
            end = min((i + 1) * seg_len, expected_total) - 1
            if start > end:
                break
            plan.append((i, start, end))

        seg_paths = {i: self._seg_path(part_dir_abs, part_name, i) for i, _, _ in plan}

        def seg_size_on_disk(index: int, start: int, end: int) -> int:
            path = seg_paths[index]
            expected_len = end - start + 1
            if not os.path.exists(path):
                return 0
            size = os.path.getsize(path)
            if size > expected_len:  # 段损坏：重建
                truncate_within(part_dir_abs, path)
                return 0
            return size

        pre_existing = sum(seg_size_on_disk(i, s, e) for i, s, e in plan)
        self._guard_disk(part_dir_abs, expected_total, pre_existing, factor=2)

        lock = threading.Lock()
        done_bytes = [pre_existing]

        def worker(index: int, start: int, end: int) -> None:
            if self.control.cancelled:
                raise DownloadCancelled()
            if self.control.paused:
                raise DownloadPaused()
            seg_path = seg_paths[index]
            expected_len = end - start + 1
            cur = seg_size_on_disk(index, start, end)
            if cur == expected_len:
                return
            headers = {"User-Agent": self.user_agent, "Accept": "*/*"}
            if extra_headers:
                headers.update(extra_headers)
            headers["Range"] = f"bytes={start + cur}-{end}"
            with open_url(url, headers=headers, timeout=self.read_timeout,
                          allow_private=self.allow_private) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                if status != 206:
                    raise VerificationError(
                        f"分段下载需要 Range 支持，服务器返回 {status}"
                    )
                cr = resp.headers.get("Content-Range", "") or ""
                m = re.match(r"bytes\s+(\d+)-", cr)
                if not m or int(m.group(1)) != start + cur:
                    raise VerificationError("分段起点与服务器不一致")
                remaining = expected_len - cur
                with open_within(part_dir_abs, seg_path, "ab") as fh:
                    while remaining > 0:
                        if self.control.cancelled:
                            raise DownloadCancelled()
                        if self.control.paused:
                            raise DownloadPaused()
                        try:
                            chunk = resp.read(min(self.chunk_size, remaining))
                        except socket.timeout:
                            raise TimeoutError("读取数据超时") from None
                        if not chunk:
                            raise VerificationError("分段响应提前结束")
                        fh.write(chunk)
                        remaining -= len(chunk)
                        with lock:
                            done_bytes[0] += len(chunk)
                            if on_progress:
                                on_progress(done_bytes[0])
                    fh.flush()
                    os.fsync(fh.fileno())

        with ThreadPoolExecutor(max_workers=len(plan),
                                thread_name_prefix="ClipHarborSeg") as pool:
            futures = [pool.submit(worker, i, s, e) for i, s, e in plan]
            done, pending = wait(futures, return_when=FIRST_EXCEPTION)
            first_exc: BaseException | None = None
            for f in done:
                exc = f.exception()
                if exc is not None and first_exc is None:
                    first_exc = exc
            if first_exc is not None:
                for f in pending:
                    f.cancel()
            wait(pending)  # 等运行中的段收尾（写入各自段文件，进度保留）
            if first_exc is not None:
                raise first_exc

        # 全部段完成：校验每段长度后拼接
        total_check = 0
        for i, s, e in plan:
            size = os.path.getsize(seg_paths[i])
            if size != e - s + 1:
                raise VerificationError(
                    f"分段 {i} 大小不符: {size} != {e - s + 1}"
                )
            total_check += size
        if total_check != expected_total:
            raise VerificationError(
                f"分段总大小校验失败: {total_check} != {expected_total}"
            )
        target = open_within(part_dir_abs, os.path.join(part_dir_abs, part_name), "wb")
        try:
            for i, _, _ in plan:
                with open_within(part_dir_abs, seg_paths[i], "rb") as src:
                    while True:
                        block = src.read(1024 * 1024)
                        if not block:
                            break
                        target.write(block)
            target.flush()
            os.fsync(target.fileno())
        finally:
            target.close()
        for path in seg_paths.values():
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
        return {"bytes": expected_total, "elapsed": time.monotonic() - started}
