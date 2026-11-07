"""yt-dlp 来源适配器（Phase 2/3）。

职责边界：yt-dlp 只做"解析"（元数据、格式、直链、播放列表展开），
真实传输由 ClipHarbor 引擎完成（Range 断点 + 真实速度统计）。

- 渐进式流（音视频合一）：单流直接下载。
- DASH 分离流：下载视频流 + 音频流 → ffmpeg 无损合并（需要系统安装 ffmpeg）。
- 本适配器不绕过任何 DRM / 访问控制；无法访问的内容会明确报告原因。
- 已知环境事实：本开发网络无法直连 youtube.com / archive.org（连接超时），
  YouTube 路径的代码与 bilibili 完全共用，但在本机只能以 bilibili 做真实验证。
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field

ADAPTER_ID = "ytdlp"


def is_available() -> bool:
    return importlib.util.find_spec("yt_dlp") is not None


@dataclass
class ResolvedStream:
    url: str
    headers: dict = field(default_factory=dict)
    note: str = ""
    bytes_total: int = 0


@dataclass
class ResolvedMedia:
    webpage_url: str
    title: str
    uploader: str | None
    duration_seconds: float | None
    thumbnail_url: str | None
    streams: list[ResolvedStream]
    formats_display: list[dict]
    estimated_bytes: int
    note: str = ""


def _ydl_options(**overrides) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 2,
    }
    opts.update(overrides)
    return opts


# YouTube SABR / PO Token 应对：解析失败且与客户端相关时，按顺序切换 player_client 重试。
# 维持 yt-dlp 最新版（cli: update-ytdlp）是第一优先级；此回退链是第二道保险。
_CLIENT_FALLBACKS: list[list[str] | None] = [
    None,                              # yt-dlp 默认（跟随上游最新决策）
    ["tv", "web_safari"],              # SABR 影响较小的客户端组合
    ["android", "ios"],                # 移动客户端
    ["mweb", "web_embedded"],          # 备用网页客户端
]
_RETRY_MARKERS = (
    "sabr", "po token", "403", "requested format is not available",
    "only images", "nsig", "signature",
)


def _extract_info(yt_dlp, url: str):
    last_exc: Exception | None = None
    for clients in _CLIENT_FALLBACKS:
        opts = _ydl_options()
        if clients:
            opts["extractor_args"] = {"youtube": {"player_client": clients}}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception as exc:  # noqa: BLE001
            message = str(exc).lower()
            last_exc = exc
            if not any(marker in message for marker in _RETRY_MARKERS):
                raise  # 非客户端相关错误（地理限制/登录要求等）直接抛出
    raise last_exc  # type: ignore[misc]


def list_source(url: str) -> dict:
    """解析来源：单视频或播放列表（flat，快）。返回 {"type": ..., "entries": [...]}。"""
    import yt_dlp

    if not is_available():
        raise RuntimeError("yt-dlp 未安装")
    opts = _ydl_options(extract_flat="in_playlist")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info is None:
        raise ValueError("解析结果为空")
    if info.get("_type") == "playlist":
        entries = []
        for e in info.get("entries") or []:
            if not e:
                continue
            entries.append({
                "url": e.get("url") or e.get("webpage_url") or "",
                "title": e.get("title") or "",
                "duration": e.get("duration"),
                "uploader": e.get("uploader") or e.get("channel"),
                "thumbnail": e.get("thumbnails")[0].get("url") if e.get("thumbnails") else None,
            })
        return {"type": "playlist", "title": info.get("title") or "", "entries": entries}
    return {
        "type": "single",
        "title": info.get("title") or "",
        "entries": [{"url": info.get("webpage_url") or url, "title": info.get("title") or "",
                     "duration": info.get("duration"), "uploader": info.get("uploader"),
                     "thumbnail": (info.get("thumbnails") or [{}])[0].get("url")
                     if info.get("thumbnails") else None}],
    }


def resolve_media(url: str, *, max_height: int | None = None) -> ResolvedMedia:
    """解析单个视频为可下载计划（优先渐进式；否则视频+音频双流待合并）。"""
    import yt_dlp

    if not is_available():
        raise RuntimeError("yt-dlp 未安装")
    info = _extract_info(__import__("yt_dlp"), url)
    if info is None:
        raise ValueError("解析结果为空")
    if info.get("_type") == "playlist":
        raise ValueError("该地址是播放列表，请先展开选择具体视频")

    formats = [f for f in (info.get("formats") or []) if f.get("url")]
    http_fmts = [f for f in formats if f.get("protocol") in ("https", "http")]

    def height_of(f: dict) -> int:
        return int(f.get("height") or 0)

    progressive = [
        f for f in http_fmts
        if f.get("vcodec") not in (None, "none") and f.get("acodec") not in (None, "none")
    ]
    videos = [f for f in http_fmts if f.get("vcodec") not in (None, "none")
              and f.get("acodec") in (None, "none")]
    audios = [f for f in http_fmts if f.get("vcodec") in (None, "none")
              and f.get("acodec") not in (None, "none")]

    from ..core import merge as merge_mod

    streams: list[ResolvedStream] = []
    formats_display: list[dict] = []
    note = ""

    def headers_of(f: dict) -> dict:
        h = dict(f.get("http_headers") or {})
        return {k: v for k, v in h.items() if isinstance(v, str)}

    def size_of(f: dict) -> int:
        return int(f.get("filesize") or f.get("filesize_approx") or 0)

    if progressive:
        candidates = [f for f in progressive if not max_height or height_of(f) <= max_height]
        best = max(candidates or progressive, key=lambda f: (height_of(f), size_of(f)))
        streams.append(ResolvedStream(
            url=best["url"], headers=headers_of(best),
            note=f"渐进式 {best.get('format_id')} {height_of(best) or '?'}p",
            bytes_total=size_of(best),
        ))
        note = f"音视频合一（{best.get('ext') or 'mp4'}）"
    elif videos and audios and merge_mod.ffmpeg_available():
        vcandidates = [f for f in videos if not max_height or height_of(f) <= max_height]
        best_v = max(vcandidates or videos, key=lambda f: (height_of(f), size_of(f)))
        best_a = max(audios, key=lambda f: (int(f.get("abr") or 0), size_of(f)))
        streams.append(ResolvedStream(
            url=best_v["url"], headers=headers_of(best_v),
            note=f"视频流 {best_v.get('format_id')} {height_of(best_v) or '?'}p",
            bytes_total=size_of(best_v),
        ))
        streams.append(ResolvedStream(
            url=best_a["url"], headers=headers_of(best_a),
            note=f"音频流 {best_a.get('format_id')} {int(best_a.get('abr') or 0)}kbps",
            bytes_total=size_of(best_a),
        ))
        note = f"音视频分离（{height_of(best_v) or '?'}p + 音轨），下载后用 ffmpeg 无损合并"
    elif videos and audios:
        raise RuntimeError("该视频仅有音视频分离流，且系统未安装 ffmpeg，无法合并")
    else:
        raise RuntimeError("没有可用于直接下载的 http(s) 媒体流（可能需要专门处理或已受保护）")

    for f in sorted(http_fmts, key=lambda f: (-height_of(f), -size_of(f)))[:12]:
        formats_display.append({
            "formatId": str(f.get("format_id") or ""),
            "container": f.get("ext") or "",
            "resolution": f.get("resolution") or (f"{height_of(f)}p" if height_of(f) else "audio"),
            "height": height_of(f) or None,
            "fps": f.get("fps"),
            "vcodec": f.get("vcodec"),
            "acodec": f.get("acodec"),
            "filesizeBytes": size_of(f) or None,
            "note": "音视频合一" if f in progressive else ("视频流" if f in videos else "音频流"),
        })

    estimated = sum(s.bytes_total for s in streams)
    return ResolvedMedia(
        webpage_url=info.get("webpage_url") or url,
        title=info.get("title") or "video",
        uploader=info.get("uploader") or info.get("channel"),
        duration_seconds=info.get("duration"),
        thumbnail_url=(info.get("thumbnails") or [{}])[0].get("url") if info.get("thumbnails") else None,
        streams=streams,
        formats_display=formats_display,
        estimated_bytes=estimated,
        note=note,
    )
