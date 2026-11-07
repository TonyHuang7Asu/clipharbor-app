"""任务模型与状态枚举。

JSON 序列化键名与 ClipHarbor 跨平台协议 v1.0 保持一致（camelCase），
供未来 FastAPI 后台与 iOS 端直接复用。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class TaskStatus(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    RETRY_WAIT = "retry_wait"
    MERGING = "merging"
    ZIPPING = "zipping"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @classmethod
    def active_states(cls) -> tuple["TaskStatus", ...]:
        return (
            cls.QUEUED,
            cls.DOWNLOADING,
            cls.PAUSED,
            cls.RETRY_WAIT,
            cls.MERGING,
            cls.ZIPPING,
        )

    @classmethod
    def terminal_states(cls) -> tuple["TaskStatus", ...]:
        return (cls.COMPLETED, cls.FAILED, cls.CANCELLED)


class TaskPhase(str, Enum):
    IDLE = "idle"
    NETWORK = "network"
    RETRY_WAIT = "retry_wait"
    MERGE = "merge"
    ZIP = "zip"
    DONE = "done"


def now_ts() -> float:
    return time.time()


def iso(ts: float | None) -> str | None:
    if ts is None or ts <= 0:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


@dataclass
class Task:
    id: str
    url: str
    dest_dir: str = ""
    filename: str = ""
    file_path: str = ""  # 权威落盘路径（完成时写入；数据库列为准）
    source_id: str = "direct"
    title: str = ""
    format_id: str = ""
    thumbnail_url: str = ""
    status: TaskStatus = TaskStatus.QUEUED
    phase: TaskPhase = TaskPhase.IDLE
    bytes_downloaded: int = 0
    bytes_total: int = 0  # 0 表示未知
    error: str = ""
    retries: int = 0
    created_at: float = field(default_factory=now_ts)
    updated_at: float = 0.0
    completed_at: float | None = None
    avg_speed_bps: float = 0.0
    peak_speed_bps: float = 0.0
    sha256: str = ""
    verified: bool = False
    # 多流任务（音视频分离，Phase 2）：JSON 列表 [{url, note, bytes_downloaded}]
    # 空字符串表示单流任务（直接用 url）。http_headers 为整任务附加请求头 JSON（如 Referer）。
    streams_json: str = ""
    http_headers: str = ""

    @property
    def streams(self) -> list[dict]:
        if not self.streams_json:
            return []
        try:
            import json

            data = json.loads(self.streams_json)
            return data if isinstance(data, list) else []
        except ValueError:
            return []

    @property
    def headers_dict(self) -> dict[str, str]:
        if not self.http_headers:
            return {}
        try:
            import json

            data = json.loads(self.http_headers)
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    def to_dict(self) -> dict:
        """协议 Task / DownloadHistory 共用表示（camelCase）。"""
        return {
            "taskId": self.id,
            "sourceUrl": self.url,
            "sourceId": self.source_id,
            "title": self.title,
            "filename": self.filename,
            "destDir": self.dest_dir,
            "filePath": self.file_path,
            "formatId": self.format_id,
            "thumbnailUrl": self.thumbnail_url,
            "status": self.status.value,
            "phase": self.phase.value,
            "bytesDownloaded": self.bytes_downloaded,
            "bytesTotal": self.bytes_total,
            "error": self.error,
            "retries": self.retries,
            "createdAt": iso(self.created_at),
            "updatedAt": iso(self.updated_at) if self.updated_at else iso(self.created_at),
            "completedAt": iso(self.completed_at),
            "avgSpeedBps": self.avg_speed_bps,
            "peakSpeedBps": self.peak_speed_bps,
            "sha256": self.sha256,
            "verified": self.verified,
        }
