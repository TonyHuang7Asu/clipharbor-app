"""离线单元测试：URL 安全校验与文件名清洗（无网络）。

运行：python -m pytest tests/test_url_guard.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from clipharbor.core.fsutil import ensure_within, sanitize_filename
from clipharbor.core.url_guard import DisallowedUrlError, validate_url


class TestUrlGuard:
    def test_https_public_allowed(self):
        validate_url("https://proof.ovh.net/files/10Mb.dat")

    def test_http_allowed(self):
        validate_url("http://proof.ovh.net/files/10Mb.dat")

    def test_non_http_schemes_rejected(self):
        for url in ("ftp://example.com/f", "file:///C:/Windows", "javascript:alert(1)"):
            with pytest.raises(DisallowedUrlError):
                validate_url(url)

    def test_localhost_rejected_in_strict_mode(self):
        for url in ("http://localhost:8080/f", "http://127.0.0.1/f", "http://[::1]/f"):
            with pytest.raises(DisallowedUrlError):
                validate_url(url, allow_private=False)

    def test_private_ranges_rejected_in_strict_mode(self):
        for url in (
            "http://192.168.1.10/f",
            "http://10.0.0.5/f",
            "http://172.16.0.1/f",
            "http://169.254.169.254/latest/meta-data/",
            "http://100.64.0.1/f",
        ):
            with pytest.raises(DisallowedUrlError):
                validate_url(url, allow_private=False)

    def test_lan_opt_in_allowed(self):
        # 桌面端用户显式开启"允许局域网来源"后放行
        validate_url("http://192.168.1.10/f", allow_private=True)

    def test_empty_rejected(self):
        with pytest.raises(DisallowedUrlError):
            validate_url("   ")


class TestFilenames:
    def test_strips_path_separators(self):
        assert "/" not in sanitize_filename("..\\..\\evil.mp4")
        assert ".." not in sanitize_filename("..\\..\\evil.mp4")

    def test_windows_reserved(self):
        assert sanitize_filename("CON.mp4").startswith("_")

    def test_ensure_within_blocks_escape(self):
        import tempfile

        base = tempfile.mkdtemp()
        inside = ensure_within(base, os.path.join(base, "a.mp4"))
        assert os.path.abspath(inside).startswith(os.path.abspath(base))
        with pytest.raises(ValueError):
            ensure_within(base, os.path.join(base, "..", "escape.mp4"))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
