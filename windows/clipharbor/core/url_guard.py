"""URL 安全校验。

规则：
- 仅允许 http / https 协议。
- 默认严格模式（allow_private=False）：解析主机名并拒绝 localhost、环回、
  私有、链路本地、保留与组播地址，防止 SSRF / 云元数据访问。
- 仅在用户于桌面端显式开启"允许局域网来源"时（allow_private=True），
  才放行私网地址（例如用户自己的 NAS）；服务端/API 路径一律使用默认严格模式。
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class DisallowedUrlError(ValueError):
    """URL 未通过安全校验。"""


def _host_is_public(host: str) -> bool:
    lowered = host.lower().strip(".")
    if lowered in ("localhost",) or lowered.endswith(".localhost") or lowered.endswith(".local"):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (
            not ip.is_global  # 私网 / CGNAT(100.64/10) / 文档段 / 环回等所有非公网地址
            or ip.is_reserved
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


def validate_url(url: str, *, allow_private: bool = False) -> None:
    """校验下载地址。不通过时抛出 DisallowedUrlError（ValueError 子类）。"""
    if not isinstance(url, str) or not url.strip():
        raise DisallowedUrlError("URL 为空")
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise DisallowedUrlError(f"不支持的协议: {parsed.scheme!r}（仅允许 http/https）")
    host = parsed.hostname
    if not host:
        raise DisallowedUrlError("URL 缺少主机名")
    if not allow_private and not _host_is_public(host):
        raise DisallowedUrlError(f"已拒绝非公网主机地址: {host}")
