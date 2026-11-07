"""来源探针：用 GET + Range: bytes=0-0 获取文件大小、断点支持与文件名。

相比 HEAD 请求，GET Range 在更多服务器上可用（部分服务器对 HEAD 返回 405）。
所有请求经 http_transport 统一安全入口（协议与地址校验 + 重定向复检）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

from .http_transport import USER_AGENT, open_url
from .url_guard import validate_url

__all__ = ["ProbeResult", "USER_AGENT", "filename_from_headers", "probe"]


@dataclass
class ProbeResult:
    url: str
    final_url: str
    total_bytes: int  # 0 = 未知
    accepts_ranges: bool
    filename: str
    content_type: str


_DISPOSITION = re.compile(
    r"filename\*=(?:UTF-8|utf-8)''([^;]+)|filename\s*=\s*\"?([^\";\r\n]+)\"?"
)


def filename_from_headers(headers) -> str:
    cd = headers.get("Content-Disposition", "") or ""
    if cd:
        m = _DISPOSITION.search(cd)
        if m:
            raw = (m.group(1) or m.group(2) or "").strip()
            raw = unquote(raw)
            if raw:
                return raw
    return ""


def probe(url: str, *, timeout: float = 20.0, allow_private: bool = False,
          extra_headers: dict | None = None) -> ProbeResult:
    validate_url(url, allow_private=allow_private)
    req_headers = {"User-Agent": USER_AGENT, "Accept": "*/*", "Range": "bytes=0-0"}
    if extra_headers:
        req_headers.update(extra_headers)
    with open_url(url, headers=req_headers, timeout=timeout, allow_private=allow_private) as resp:
        status = getattr(resp, "status", None) or resp.getcode()
        headers = resp.headers
        final_url = resp.geturl()
        content_type = headers.get("Content-Type", "") or ""
        total = 0
        accepts = False
        if status == 206:
            accepts = True
            m = re.search(r"/(\d+)\s*$", headers.get("Content-Range", "") or "")
            if m:
                total = int(m.group(1))
        elif status == 200:
            cl = headers.get("Content-Length", "") or ""
            if cl.isdigit():
                total = int(cl)
        try:
            resp.read(1)
        except Exception:  # 读取 1 字节失败不影响探测结果
            pass

    name = filename_from_headers(headers)
    if not name:
        path = urlparse(final_url).path
        name = unquote(path.rsplit("/", 1)[-1]) if "/" in path else ""
    return ProbeResult(
        url=url,
        final_url=final_url,
        total_bytes=total,
        accepts_ranges=accepts,
        filename=name,
        content_type=content_type,
    )
