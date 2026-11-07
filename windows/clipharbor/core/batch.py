"""批量下载（Phase 2）：多链接探测、空间预估、去重入队。

安全与体验原则：
- 入队前先探测每个 URL 的真实大小并做磁盘空间预估；预估失败(0)按"未知"处理。
- 同一 URL 的活动任务自动去重；已结束任务允许重新添加。
- 任何单个 URL 失败不影响其余任务。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field

from .downloader import UnsupportedSourceError
from .fsutil import human_bytes
from .manager import DownloadManager
from .models import Task
from .probe import probe

_ZIP_MARGIN = 128 * 1024 * 1024  # 预留后续打包空间


@dataclass
class BatchResult:
    added: list[Task] = field(default_factory=list)
    duplicates: list[tuple[str, str]] = field(default_factory=list)   # (url, taskId)
    failed: list[tuple[str, str]] = field(default_factory=list)       # (url, error)
    estimated_bytes: int = 0
    unknown_size_count: int = 0

    def summary(self) -> str:
        lines = [
            f"新增 {len(self.added)} 个，重复跳过 {len(self.duplicates)} 个，"
            f"失败 {len(self.failed)} 个",
            f"预计总大小: {human_bytes(self.estimated_bytes)}"
            + (f"（{self.unknown_size_count} 个大小未知）" if self.unknown_size_count else ""),
        ]
        for url, err in self.failed:
            lines.append(f"  [失败] {url} — {err}")
        for url, tid in self.duplicates:
            lines.append(f"  [重复] {url} → 已存在 {tid}")
        return "\n".join(lines)


def estimate_urls(urls: list[str], *, allow_private: bool = False) -> tuple[int, dict[str, int]]:
    """探测每个 URL 的真实大小；失败计 0（未知）。"""
    sizes: dict[str, int] = {}
    for url in urls:
        try:
            sizes[url] = probe(url, allow_private=allow_private).total_bytes
        except Exception:  # noqa: BLE001 — 探测失败不阻断批量流程
            sizes[url] = 0
    return sum(sizes.values()), sizes


def check_disk_space(dest_dir: str, estimated_bytes: int, *, plan_zip: bool = False) -> None:
    """磁盘空间预检：不足时抛出 UnsupportedSourceError 之外的 ValueError（调用方展示）。"""
    margin = (_ZIP_MARGIN if plan_zip else 0) + 64 * 1024 * 1024
    free = shutil.disk_usage(dest_dir).free
    if free < estimated_bytes + margin:
        raise ValueError(
            f"磁盘空间不足: 预计需要 {human_bytes(estimated_bytes + margin)}, "
            f"剩余 {human_bytes(free)}"
        )


def add_batch(
    manager: DownloadManager,
    urls: list[str],
    *,
    dest_dir: str | None = None,
    filename_map: dict[str, str] | None = None,
    source_id_map: dict[str, str] | None = None,
    plan_zip: bool = False,
    probe_first: bool = True,
) -> BatchResult:
    """批量入队。先整体空间预估（可关闭），再逐个去重入队。"""
    result = BatchResult()
    filename_map = filename_map or {}
    source_id_map = source_id_map or {}

    if probe_first:
        try:
            result.estimated_bytes, sizes = estimate_urls(urls)
            result.unknown_size_count = sum(1 for v in sizes.values() if v == 0)
            target_dir = dest_dir or manager.default_dest
            check_disk_space(target_dir, result.estimated_bytes, plan_zip=plan_zip)
        except ValueError as exc:
            raise ValueError(f"批量任务未开始（空间预估未通过）: {exc}") from exc

    for url in urls:
        try:
            task, dup = manager.add(
                url,
                dest_dir=dest_dir,
                filename=filename_map.get(url),
                source_id=source_id_map.get(url, "direct"),
            )
            if dup:
                result.duplicates.append((url, task.id))
            else:
                result.added.append(task)
        except UnsupportedSourceError as exc:
            result.failed.append((url, f"未支持: {exc}"))
        except Exception as exc:  # noqa: BLE001
            result.failed.append((url, str(exc)))
    return result
