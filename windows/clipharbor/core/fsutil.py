"""文件名与路径工具。

原则：绝不覆盖用户的现有文件；只触碰 ClipHarbor 自己创建的 .part 临时文件。
所有按路径打开文件的操作必须经过 open_within / truncate_within，
它们先做 realpath + 目录包含校验，防止路径穿越。
"""

from __future__ import annotations

import os
import re
from urllib.parse import unquote, urlparse

_MIME_EXT = {
    "video/mp4": ".mp4",
    "video/x-matroska": ".mkv",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/flac": ".flac",
    "application/zip": ".zip",
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "application/octet-stream": ".bin",
}

_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    name = _UNSAFE.sub("_", name)
    name = re.sub(r"\.{2,}", ".", name).strip(" .")  # 消除 ".." 等目录穿越序列
    if len(name) > 150:
        stem, ext = os.path.splitext(name)
        name = stem[: 150 - len(ext)] + ext
    if not name:
        name = "download"
    stem = name.split(".")[0].upper()
    if stem in _WINDOWS_RESERVED:
        name = "_" + name
    return name


def default_filename_from_url(url: str, content_type: str = "") -> str:
    path = urlparse(url).path
    name = unquote(path.rsplit("/", 1)[-1]) if "/" in path else ""
    name = sanitize_filename(name) if name else ""
    if name and os.path.splitext(name)[1]:
        return name
    ext = _MIME_EXT.get((content_type or "").split(";")[0].strip().lower(), ".bin")
    stem = name or "download"
    return f"{stem}{ext}"


def ensure_within(directory: str, path: str) -> str:
    """校验 path 规范化后仍位于 directory 内（防路径穿越），返回规范化绝对路径。"""
    base = os.path.realpath(os.path.abspath(directory))
    target = os.path.realpath(os.path.abspath(path))
    if target != base and not target.startswith(base + os.sep):
        raise ValueError(f"路径越界: {path!r} 不在目录 {directory!r} 内")
    return target


def open_within(directory: str, path: str, mode: str):
    """打开目录内的文件；先做路径包含校验。"""
    return open(ensure_within(directory, path), mode)


def truncate_within(directory: str, path: str) -> None:
    """将目录内文件截断为 0 字节（用于丢弃无效分片）。"""
    with open_within(directory, path, "wb"):
        pass


def unique_path(directory: str, filename: str) -> str:
    """返回目录内不冲突的路径；绝不覆盖已有文件。"""
    candidate = os.path.join(directory, filename)
    if not os.path.exists(candidate):
        return candidate
    stem, ext = os.path.splitext(filename)
    for i in range(2, 10000):
        candidate = os.path.join(directory, f"{stem} ({i}){ext}")
        if not os.path.exists(candidate):
            return candidate
    raise RuntimeError(f"无法为 {filename!r} 生成不冲突的文件名")


def sha256_file(path: str, chunk: int = 1024 * 1024) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def human_bytes(n: float | int) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"
