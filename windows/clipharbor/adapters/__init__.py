"""来源适配器注册与入队流程。

Phase 2 起：直接直链走 direct；视频站点经 yt-dlp 解析为下载计划（单流或
视频+音频双流待 ffmpeg 合并）。解析失败/未支持时给出明确原因，不假装支持。
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlparse

from ..core.downloader import UnsupportedSourceError

if TYPE_CHECKING:  # 避免循环导入：仅类型引用
    from ..core.manager import DownloadManager
    from ..core.models import Task

VIDEO_SITE_HOSTS = (
    "youtube.com",
    "youtu.be",
    "bilibili.com",
    "b23.tv",
    "vimeo.com",
    "douyin.com",
    "tiktok.com",
    "x.com",
    "twitter.com",
    "instagram.com",
)


def is_video_site(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in VIDEO_SITE_HOSTS)


def select_adapter(url: str) -> str:
    """返回应处理该 URL 的适配器 ID。视频站点必须先经解析，不允许直接入队。"""
    if is_video_site(url):
        from . import ytdlp_adapter

        if not ytdlp_adapter.is_available():
            raise UnsupportedSourceError(
                f"{urlparse(url).hostname} 需要专用解析器（未安装 yt-dlp，"
                "可 pip install yt-dlp）。"
            )
        raise UnsupportedSourceError(
            "视频站点链接需先解析：请使用 batch/playlist/add-source 流程"
            "（解析出媒体流后再入队）。"
        )
    return "direct"


def enqueue_source(
    manager: "DownloadManager",
    url: str,
    *,
    dest_dir: str | None = None,
    max_height: int | None = None,
    filename: str | None = None,
) -> tuple["Task", bool]:
    """解析任意来源并入队：直链直接下载；视频站点经 yt-dlp 解析为下载计划。"""
    if not is_video_site(url):
        return manager.add(url, dest_dir=dest_dir, filename=filename)

    from . import ytdlp_adapter

    if not ytdlp_adapter.is_available():
        raise UnsupportedSourceError("未安装 yt-dlp（pip install yt-dlp）")
    media = ytdlp_adapter.resolve_media(url, max_height=max_height)
    # 容器由 ffmpeg 无损合并决定，统一 mp4（h264/aac 兼容）
    resolved_name = filename or (media.title + ".mp4")
    return manager.add_resolved(
        url=media.webpage_url,
        title=media.title,
        filename=resolved_name,
        source_id="ytdlp",
        streams=[{"url": s.url, "headers": s.headers, "note": s.note,
                  "bytes_total": s.bytes_total, "bytes_downloaded": 0}
                 for s in media.streams],
        http_headers=media.streams[0].headers if media.streams else None,
        bytes_total=media.estimated_bytes,
        dest_dir=dest_dir,
        thumbnail_url=media.thumbnail_url or "",
    )


def expand_playlist(url: str, *, limit: int | None = None) -> dict:
    """展开播放列表（flat，真实解析）。返回 {"type","title","entries"}。"""
    from . import ytdlp_adapter

    if not is_video_site(url):
        return {"type": "single", "title": "", "entries": [
            {"url": url, "title": "", "duration": None, "uploader": None, "thumbnail": None}
        ]}
    if not ytdlp_adapter.is_available():
        raise UnsupportedSourceError("未安装 yt-dlp（pip install yt-dlp）")
    result = ytdlp_adapter.list_source(url)
    if limit and result.get("type") == "playlist":
        result["entries"] = result["entries"][:limit]
    return result
