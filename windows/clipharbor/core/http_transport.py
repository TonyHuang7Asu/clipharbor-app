"""HTTP 传输层：统一的请求入口。

安全措施：
- 仅允许 http/https（由 url_guard.validate_url 保证）。
- 每一次请求和每一次重定向跳转都重新执行 URL 校验；严格模式下拒绝
  解析到私网 / 环回 / 链路本地 / 保留地址的主机。
- 已知限制：严格模式下 DNS 解析校验与实际 TCP 连接之间存在理论上的
  TOCTOU 窗口（DNS rebinding）；当前通过每次跳转复检缓解，
  后续阶段可改为自管解析 + 按 IP 直连彻底消除。
"""

from __future__ import annotations

import urllib.request

from .url_guard import validate_url

# 部分站点（Cloudflare 防护）会拦截无 UA 或非浏览器 UA 的程序化请求；
# 与 yt-dlp 的做法一致，默认使用标准浏览器 UA，调用方可覆盖。
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_private: bool) -> None:
        super().__init__()
        self._allow_private = allow_private

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        validate_url(newurl, allow_private=self._allow_private)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(
    url: str,
    *,
    headers: dict | None = None,
    timeout: float = 30.0,
    allow_private: bool = False,
):
    merged = dict(headers or {})
    merged.setdefault("User-Agent", USER_AGENT)
    validate_url(url, allow_private=allow_private)
    req = urllib.request.Request(url, headers=merged)
    opener = urllib.request.build_opener(SafeRedirectHandler(allow_private))
    return opener.open(req, timeout=timeout)
