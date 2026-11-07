"""yt-dlp 自更新（长期稳定性关键）。

YouTube 频繁变更（SABR/PO Token），保持 yt-dlp 最新是第一优先级。
通过 `python -m pip install --upgrade yt-dlp` 以参数列表 + shell=False 执行，
命令参数为固定常量，无注入面。
"""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys


def ytdlp_version() -> str | None:
    try:
        return importlib.metadata.version("yt-dlp")
    except importlib.metadata.PackageNotFoundError:
        return None


def update_ytdlp(*, timeout: float = 600.0) -> tuple[bool, str]:
    """升级 yt-dlp。返回 (是否有新版本被安装, 说明文字)。"""
    old = ytdlp_version()
    if old is None:
        return False, "yt-dlp 未安装（pip install yt-dlp）"
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
            capture_output=True, text=True, timeout=timeout, shell=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"升级超时（>{timeout:.0f}s），请手动执行 pip install -U yt-dlp"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        return False, "升级失败: " + (" | ".join(detail) or f"exit={proc.returncode}")
    new = ytdlp_version() or old
    if new != old:
        return True, f"已更新 {old} → {new}（重启应用后生效）"
    return False, f"已是最新版本 {new}"
