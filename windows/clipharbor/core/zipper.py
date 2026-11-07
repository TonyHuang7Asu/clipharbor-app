"""ZIP 打包（Phase 2）：把已完成任务的文件打包为 zip。

安全原则：
- 只读取已完成任务的注册文件，绝不扫描/移动/删除用户其他文件。
- 打包失败只清理自己的 .part 临时包，已下载的原始文件永远不动。
- 目标 zip 已存在时使用唯一名，绝不覆盖。
- 压缩包内写入 clipharbor-manifest.json（任务元数据，可追溯到来源 URL）。
"""

from __future__ import annotations

import json
import os
import time
import uuid
import zipfile
from dataclasses import dataclass

from ..db.history import HistoryDatabase
from .fsutil import unique_path
from .models import TaskStatus, iso, now_ts


class ZipError(Exception):
    """打包失败（原始文件不受影响）。"""


@dataclass
class ZipJobResult:
    id: str
    path: str
    files: int
    bytes_total: int


def create_zip(
    db: HistoryDatabase,
    task_ids: list[str],
    dest_dir: str,
    *,
    name: str | None = None,
    compress: bool = False,
    on_progress=None,
) -> ZipJobResult:
    """把指定已完成任务打包。压缩默认关闭（视频本身已压缩，DEFLATED 耗时无收益）。"""
    if not task_ids:
        raise ZipError("未选择任何任务")

    files: list[tuple[str, str]] = []  # (本地路径, 包内文件名)
    total = 0
    for tid in task_ids:
        task = db.get_task(tid)
        if task is None or task.status != TaskStatus.COMPLETED:
            raise ZipError(f"任务 {tid} 不存在或未完成")
        if not task.file_path or not os.path.exists(task.file_path):
            raise ZipError(f"文件缺失: {task.filename}（任务 {tid}）")
        files.append((task.file_path, task.filename))
        total += task.bytes_downloaded or os.path.getsize(task.file_path)

    os.makedirs(dest_dir, exist_ok=True)
    zip_name = name or f"ClipHarbor_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    target = unique_path(dest_dir, zip_name)
    part_path = target + ".clipharbor.zip.part"

    import shutil

    free = shutil.disk_usage(dest_dir).free
    if free < total + 64 * 1024 * 1024:
        raise ZipError(
            f"磁盘空间不足: 需要 >= {total + 64 * 1024 * 1024} 字节（含余量），剩余 {free}"
        )

    job_id = uuid.uuid4().hex[:16]
    db.insert_zip_job(job_id, task_ids)
    method = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    try:
        with zipfile.ZipFile(part_path, "w", method, allowZip64=True) as zf:
            used: set[str] = set()
            for i, (src, arc_base) in enumerate(files):
                arcname = arc_base
                n = 2
                while arcname in used:  # 包内重名自动处理，不覆盖
                    stem, ext = os.path.splitext(arc_base)
                    arcname = f"{stem} ({n}){ext}"
                    n += 1
                used.add(arcname)
                zf.write(src, arcname)
                if on_progress:
                    on_progress(i + 1, len(files))
            manifest = {
                "protocol": "clipharbor",
                "apiVersion": "1.0",
                "createdAt": iso(now_ts()),
                "files": [
                    {
                        "taskId": tid,
                        "archiveName": arc,
                        "sourceUrl": (db.get_task(tid).to_dict()["sourceUrl"]),
                        "title": db.get_task(tid).title,
                        "sha256": db.get_task(tid).sha256,
                    }
                    for tid, (_, arc) in zip(task_ids, files)
                ],
            }
            zf.writestr("clipharbor-manifest.json",
                        json.dumps(manifest, ensure_ascii=False, indent=2))
        os.replace(part_path, target)
    except Exception:
        # 半成品清理；原始文件绝不触碰
        try:
            if os.path.exists(part_path):
                os.remove(part_path)
        except OSError:
            pass
        db.finish_zip_job(job_id, path="", status="failed", error="打包失败")
        raise

    db.finish_zip_job(job_id, path=target, status="completed")
    return ZipJobResult(id=job_id, path=target, files=len(files), bytes_total=total)
