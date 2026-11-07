"""下载队列管理器：并发限制、暂停/恢复/取消/重试、崩溃恢复、实时进度、多流合并。

设计：
- 每个任务一个工作线程；用有界信号量限制同时下载的数量（默认 2）。
- 进度实时写入 SQLite（节流 ~0.5s），进程意外退出后可从分片恢复。
- 速度统计来自真实传输（SpeedMeter），暂停/重试等待不计入平均速度。
- 多流任务（音视频分离）：顺序下载各流 → ffmpeg 无损合并 → 落盘。
- 绝不覆盖已有文件；失败/取消只清理 ClipHarbor 自己的 .part 分片。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import deque
from typing import TYPE_CHECKING

from . import fsutil, merge
from .downloader import (
    Control,
    Downloader,
    DownloadCancelled,
    DownloadPaused,
    classify_error,
)
from .models import Task, TaskPhase, TaskStatus, iso, now_ts
from .probe import probe
from .speedmeter import SpeedMeter

if TYPE_CHECKING:  # 避免与 db.history 的循环导入
    from ..db.history import HistoryDatabase

DEFAULT_DEST = os.path.join(os.path.expanduser("~"), "Downloads", "ClipHarbor")
_PART_SUFFIX = ".clipharbor.part"
_DB_WRITE_INTERVAL = 0.5


def _part_file_for(task: Task, index: int = 0) -> str:
    """任务专属分片路径：含任务 ID 与流序号，并发任务/多流之间绝不冲突。"""
    stream_tag = f".s{index}" if task.streams else ""
    return os.path.join(
        task.dest_dir, f"{task.filename or 'download'}.{task.id}{stream_tag}{_PART_SUFFIX}"
    )


class _Runtime:
    __slots__ = ("control", "wake", "meter", "last_cum")

    def __init__(self) -> None:
        self.control = Control()
        self.wake = threading.Event()
        self.meter = SpeedMeter()
        self.last_cum = 0  # 已计入速度统计的累计字节（续传字节不计入）


class DownloadManager:
    def __init__(
        self,
        db: "HistoryDatabase",
        *,
        max_concurrent: int = 2,
        max_retries: int = 4,
        retry_backoff: float = 2.0,
        default_dest: str | None = None,
        allow_private_hosts: bool = False,
        segments: int = 4,
        segment_min_size: int = 8 * 1024 * 1024,
    ) -> None:
        self.db = db
        self.max_concurrent = max(1, int(max_concurrent))
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.default_dest = default_dest or DEFAULT_DEST
        self.allow_private_hosts = allow_private_hosts
        # 分段并发：服务器支持 Range 且文件 >= segment_min_size 时启用；
        # 保守默认 4 段，用户可调（1 = 关闭分段）。
        self.segments = max(1, int(segments))
        self.segment_min_size = max(0, int(segment_min_size))
        self._lock = threading.RLock()
        self._pending: deque[str] = deque()
        self._runtime: dict[str, _Runtime] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._slots = threading.BoundedSemaphore(self.max_concurrent)
        self._stop_flag = False
        self._wake = threading.Event()
        self._dispatcher = threading.Thread(
            target=self._dispatch_loop, name="ClipHarborDispatcher", daemon=True
        )
        self._dispatcher.start()

    # ---- 对外操作 ---------------------------------------------------------

    def add(
        self,
        url: str,
        *,
        dest_dir: str | None = None,
        filename: str | None = None,
        title: str = "",
        source_id: str = "direct",
    ) -> tuple[Task, bool]:
        """加入队列。同一 URL 已有活动任务时返回 (已有任务, True) 防止重复。"""
        from ..adapters import select_adapter

        resolved_source = select_adapter(url) if source_id == "direct" else source_id
        existing = self.db.find_active_by_url(url)
        if existing is not None:
            return existing, True
        task = Task(
            id=uuid.uuid4().hex[:16],
            url=url,
            dest_dir=os.path.abspath(dest_dir or self.default_dest),
            filename=fsutil.sanitize_filename(filename or ""),
            title=title,
            source_id=resolved_source,
            status=TaskStatus.QUEUED,
            phase=TaskPhase.IDLE,
            created_at=now_ts(),
            updated_at=now_ts(),
        )
        self.db.upsert_task(task)
        self._enqueue(task.id)
        return task, False

    def add_resolved(
        self,
        *,
        url: str,
        title: str,
        filename: str,
        source_id: str,
        streams: list[dict] | None = None,
        http_headers: dict[str, str] | None = None,
        bytes_total: int = 0,
        dest_dir: str | None = None,
        format_id: str = "",
        thumbnail_url: str = "",
    ) -> tuple[Task, bool]:
        """加入已解析的任务（yt-dlp 等适配器使用；url 为来源页，streams 为传输流）。"""
        existing = self.db.find_active_by_url(url)
        if existing is not None:
            return existing, True
        task = Task(
            id=uuid.uuid4().hex[:16],
            url=url,
            dest_dir=os.path.abspath(dest_dir or self.default_dest),
            filename=fsutil.sanitize_filename(filename),
            title=title,
            source_id=source_id,
            format_id=format_id,
            thumbnail_url=thumbnail_url,
            bytes_total=bytes_total,
            streams_json=json.dumps(streams, ensure_ascii=False) if streams else "",
            http_headers=json.dumps(http_headers, ensure_ascii=False) if http_headers else "",
            status=TaskStatus.QUEUED,
            phase=TaskPhase.IDLE,
            created_at=now_ts(),
            updated_at=now_ts(),
        )
        self.db.upsert_task(task)
        self._enqueue(task.id)
        return task, False

    def pause(self, task_id: str) -> None:
        with self._lock:
            rt = self._runtime.get(task_id)
        if rt is not None:
            rt.control.paused = True
            return
        task = self.db.get_task(task_id)
        if task and task.status == TaskStatus.QUEUED:
            self.db.set_status(task_id, TaskStatus.PAUSED, phase=TaskPhase.IDLE)

    def resume(self, task_id: str) -> None:
        with self._lock:
            rt = self._runtime.get(task_id)
        if rt is not None:
            rt.control.paused = False
            rt.wake.set()
            return
        task = self.db.get_task(task_id)
        if task and task.status == TaskStatus.PAUSED:
            self.db.set_status(task_id, TaskStatus.QUEUED, error="")
            self._enqueue(task_id)

    def cancel(self, task_id: str) -> None:
        with self._lock:
            rt = self._runtime.get(task_id)
        if rt is not None:
            rt.control.cancelled = True
            rt.wake.set()
            return
        task = self.db.get_task(task_id)
        if task and task.status in TaskStatus.active_states():
            self.db.set_status(
                task_id, TaskStatus.CANCELLED, phase=TaskPhase.IDLE, error="已取消"
            )

    def retry(self, task_id: str) -> bool:
        task = self.db.get_task(task_id)
        if not task or task.status not in TaskStatus.terminal_states():
            return False
        self.db.update_fields(
            task_id,
            status=TaskStatus.QUEUED.value,
            phase=TaskPhase.IDLE.value,
            error="",
            completed_at=None,
        )
        self._enqueue(task_id)
        return True

    def move(self, task_id: str, delta: int) -> bool:
        """在等待队列中前移/后移排队任务（delta<0 靠前，>0 靠后）。"""
        if delta == 0:
            return False
        with self._lock:
            ids = list(self._pending)
            if task_id not in ids:
                return False
            i = ids.index(task_id)
            j = max(0, min(len(ids) - 1, i + (1 if delta > 0 else -1)))
            if i == j:
                return False
            ids[i], ids[j] = ids[j], ids[i]
            self._pending.clear()
            self._pending.extend(ids)
        return True

    def queue_snapshot(self) -> list[str]:
        """当前等待队列顺序（仅排队中，不含已交给 worker 的）。"""
        with self._lock:
            return list(self._pending)

    def snapshot(self, task_id: str) -> dict | None:
        """协议 DownloadProgress：实时速度来自 SpeedMeter，静止时回落数据库值。"""
        task = self.db.get_task(task_id)
        if task is None:
            return None
        with self._lock:
            rt = self._runtime.get(task_id)
        speed = avg = peak = 0.0
        if rt is not None:
            speed = rt.meter.current_bps()
            avg = rt.meter.average_bps()
            peak = rt.meter.peak_bps()
        else:
            avg = task.avg_speed_bps
            peak = task.peak_speed_bps
        eta = None
        if speed > 0 and task.bytes_total > task.bytes_downloaded:
            eta = round((task.bytes_total - task.bytes_downloaded) / speed, 1)
        return {
            "taskId": task.id,
            "status": task.status.value,
            "phase": task.phase.value,
            "bytesDownloaded": task.bytes_downloaded,
            "bytesTotal": task.bytes_total,
            "speedBps": round(speed, 1),
            "avgSpeedBps": round(avg, 1),
            "peakSpeedBps": round(peak, 1),
            "etaSeconds": eta,
            "error": task.error,
            "updatedAt": iso(now_ts()),
        }

    def recover(self) -> dict:
        """启动恢复：中断的任务转为暂停（可续传），排队任务重新入队。"""
        recovered = {"resumed_queued": 0, "paused_interrupted": 0}
        for task in self.db.tasks_by_status([TaskStatus.DOWNLOADING, TaskStatus.RETRY_WAIT]):
            self.db.set_status(
                task.id, TaskStatus.PAUSED, phase=TaskPhase.IDLE,
                error="应用退出时中断，可续传",
            )
            recovered["paused_interrupted"] += 1
        for task in self.db.tasks_by_status([TaskStatus.QUEUED]):
            self._enqueue(task.id)
            recovered["resumed_queued"] += 1
        return recovered

    def shutdown(self, wait: bool = False) -> None:
        self._stop_flag = True
        self._wake.set()
        with self._lock:
            for rt in self._runtime.values():
                rt.wake.set()
        if wait:
            self._dispatcher.join(timeout=5)
            with self._lock:
                threads = list(self._threads.values())
            for t in threads:
                t.join(timeout=10)

    # ---- 内部实现 ---------------------------------------------------------

    def _enqueue(self, task_id: str) -> None:
        with self._lock:
            if task_id not in self._pending and task_id not in self._threads:
                self._pending.append(task_id)
        self._wake.set()

    def _dispatch_loop(self) -> None:
        while not self._stop_flag:
            tid = self._next_dispatchable()
            if tid is None:
                self._wake.wait(0.3)
                self._wake.clear()
                continue
            if not self._slots.acquire(timeout=0.25):
                if self._stop_flag:
                    return
                with self._lock:
                    self._pending.appendleft(tid)
                continue
            if self._stop_flag:
                self._slots.release()
                return
            thread = threading.Thread(
                target=self._run_task, args=(tid,),
                name=f"ClipHarborWorker-{tid[:8]}", daemon=True,
            )
            with self._lock:
                self._threads[tid] = thread
            thread.start()

    def _next_dispatchable(self) -> str | None:
        with self._lock:
            while self._pending:
                tid = self._pending.popleft()
                task = self.db.get_task(tid)
                if task is not None and task.status == TaskStatus.QUEUED:
                    return tid
        return None

    def _run_task(self, task_id: str) -> None:
        rt = _Runtime()
        with self._lock:
            self._runtime[task_id] = rt
        try:
            self._execute(task_id, rt)
        finally:
            with self._lock:
                self._runtime.pop(task_id, None)
                self._threads.pop(task_id, None)
            self._slots.release()

    def _execute(self, task_id: str, rt: _Runtime) -> None:
        attempts = 0
        while True:
            if rt.control.cancelled:
                self._finish(task_id, TaskStatus.CANCELLED, rt, error="已取消")
                return
            if rt.control.paused:
                rt.meter.pause_segment()
                self.db.set_status(task_id, TaskStatus.PAUSED, phase=TaskPhase.IDLE)
                while rt.control.paused and not rt.control.cancelled and not self._stop_flag:
                    rt.wake.wait(0.5)
                rt.wake.clear()
                continue
            try:
                self._download_attempt(task_id, rt)
                self._finish(task_id, TaskStatus.COMPLETED, rt)
                return
            except DownloadPaused:
                continue
            except DownloadCancelled:
                self._finish(task_id, TaskStatus.CANCELLED, rt, error="已取消")
                return
            except Exception as exc:  # noqa: BLE001 — 统一分类处理
                rt.meter.pause_segment()
                retryable, kind = classify_error(exc)
                attempts += 1
                message = f"[{kind}] {exc}"
                if not retryable or attempts > self.max_retries:
                    self._finish(task_id, TaskStatus.FAILED, rt, error=message)
                    return
                self.db.set_status(
                    task_id, TaskStatus.RETRY_WAIT,
                    phase=TaskPhase.RETRY_WAIT, error=message,
                )
                delay = min(60.0, self.retry_backoff * (2 ** (attempts - 1)))
                deadline = time.monotonic() + delay
                while (
                    time.monotonic() < deadline
                    and not rt.control.cancelled
                    and not rt.control.paused
                    and not self._stop_flag
                ):
                    rt.wake.wait(0.25)
                rt.wake.clear()

    # ---- 单次下载尝试 -----------------------------------------------------

    def _download_attempt(self, task_id: str, rt: _Runtime) -> None:
        task = self.db.get_task(task_id)
        if task is None:
            return
        dest_dir = task.dest_dir or self.default_dest
        os.makedirs(dest_dir, exist_ok=True)
        if task.streams:
            self._download_streams(task, rt, dest_dir)
        else:
            self._download_single(task, rt, dest_dir)

    def _download_single(self, task: Task, rt: _Runtime, dest_dir: str) -> None:
        info = probe(task.url, allow_private=self.allow_private_hosts,
                     extra_headers=task.headers_dict)
        total = info.total_bytes or task.bytes_total
        part_path = fsutil.ensure_within(dest_dir, _part_file_for(task))
        downloader = Downloader(control=rt.control, allow_private=self.allow_private_hosts)

        # 模式选择：已有段文件 → 继续分段；已有单流分片 → 继续单流；否则按配置
        seg_files = downloader.segment_files(dest_dir, os.path.basename(part_path),
                                             max(self.segments, 1))
        use_segments = (
            self.segments > 1
            and info.accepts_ranges
            and total >= self.segment_min_size
            and (bool(seg_files) or not os.path.exists(part_path))
        )

        if not task.filename:
            task.filename = fsutil.sanitize_filename(
                info.filename
                or fsutil.default_filename_from_url(info.final_url or task.url, info.content_type)
            )
            part_path = fsutil.ensure_within(dest_dir, _part_file_for(task))

        self.db.update_fields(
            task.id,
            filename=task.filename,
            dest_dir=dest_dir,
            bytes_total=total,
            status=TaskStatus.DOWNLOADING.value,
            phase=TaskPhase.NETWORK.value,
            error="",
        )

        def on_progress(downloaded: int) -> None:
            self._progress(task.id, rt, downloaded)

        try:
            if use_segments:
                pre_existing = sum(os.path.getsize(p) for p in seg_files)
                rt.last_cum = pre_existing  # 续传的既有字节不计入速度统计
                self.db.update_fields(task.id, bytes_downloaded=pre_existing)
                downloader.download_segmented(
                    task.url, dest_dir, os.path.basename(part_path),
                    segments=self.segments, expected_total=total,
                    on_progress=on_progress, extra_headers=task.headers_dict,
                )
            else:
                resume_from = self._reconcile_part(task, part_path, total,
                                                   task.bytes_downloaded,
                                                   bool(info.accepts_ranges))
                self.db.update_fields(task.id, bytes_downloaded=resume_from)
                rt.last_cum = resume_from  # 续传的既有字节不计入速度统计
                if not (total and resume_from >= total):
                    downloader.download(
                        task.url, part_path,
                        resume_from=resume_from, expected_total=total,
                        on_progress=on_progress,
                        extra_headers=task.headers_dict,
                    )
        finally:
            rt.meter.pause_segment()
        self._finalize_single(task, part_path, total)

    def _finalize_single(self, task: Task, part_path: str, total: int) -> None:
        final_size = os.path.getsize(part_path)
        digest = fsutil.sha256_file(part_path)
        verified = bool(total == 0 or final_size == total)
        final_path = fsutil.ensure_within(
            task.dest_dir, fsutil.unique_path(task.dest_dir, task.filename)
        )
        os.replace(part_path, final_path)
        self.db.update_fields(
            task.id,
            filename=os.path.basename(final_path),
            file_path=final_path,
            bytes_downloaded=final_size,
            bytes_total=final_size if not total else total,
            status=TaskStatus.COMPLETED.value,
            phase=TaskPhase.DONE.value,
            completed_at=now_ts(),
            sha256=digest,
            verified=int(verified),
            error="",
        )

    def _download_streams(self, task: Task, rt: _Runtime, dest_dir: str) -> None:
        """多流任务：顺序下载各流 → ffmpeg 合并 → 落盘。"""
        self.db.update_fields(
            task.id,
            dest_dir=dest_dir,
            status=TaskStatus.DOWNLOADING.value,
            phase=TaskPhase.NETWORK.value,
            error="",
        )
        streams = [dict(s) for s in task.streams]
        headers = task.headers_dict
        downloader = Downloader(control=rt.control, allow_private=self.allow_private_hosts)

        try:
            for index, stream in enumerate(streams):
                if rt.control.cancelled:
                    raise DownloadCancelled()
                if rt.control.paused:
                    raise DownloadPaused()
                stream_url = stream.get("url", "")
                s_headers = {**headers, **(stream.get("headers") or {})}
                info = probe(stream_url, allow_private=self.allow_private_hosts,
                             extra_headers=s_headers)
                s_total = info.total_bytes or int(stream.get("bytes_total") or 0)
                part_path = fsutil.ensure_within(dest_dir, _part_file_for(task, index))
                resume_from = self._reconcile_part(
                    task, part_path, s_total,
                    int(stream.get("bytes_downloaded") or 0),
                    bool(info.accepts_ranges),
                )
                stream["bytes_total"] = s_total
                stream["bytes_downloaded"] = resume_from
                self._persist_streams(task, streams)
                base_downloaded = sum(
                    int(s.get("bytes_downloaded") or 0) for j, s in enumerate(streams) if j < index
                )

                def on_progress(downloaded: int, idx=index, base=base_downloaded,
                                st=stream, s_path=part_path) -> None:
                    st["bytes_downloaded"] = downloaded
                    self._progress(task.id, rt, base + downloaded)
                    # 每流进度持久化（低频），供断点续传
                    now = time.monotonic()
                    if now - self._last_stream_save.get(task.id, 0) > 2.0:
                        self._last_stream_save[task.id] = now
                        self._persist_streams(task, streams)

                if not (s_total and resume_from >= s_total):
                    rt.last_cum = base_downloaded + resume_from  # 既有字节不计速
                    downloader.download(
                        stream_url, part_path,
                        resume_from=resume_from, expected_total=s_total,
                        on_progress=on_progress, extra_headers=s_headers,
                    )
                stream["bytes_downloaded"] = (
                    os.path.getsize(part_path) if os.path.exists(part_path) else resume_from
                )
                self._persist_streams(task, streams)

            # 全部流完成 → 合并
            total_bytes = sum(int(s.get("bytes_downloaded") or 0) for s in streams)
            self.db.update_fields(
                task.id,
                bytes_downloaded=total_bytes,
                bytes_total=total_bytes or task.bytes_total,
                status=TaskStatus.MERGING.value,
                phase=TaskPhase.MERGE.value,
                error="",
            )
            self._merge_and_finalize(task, rt, streams)
        finally:
            rt.meter.pause_segment()

    def _merge_and_finalize(self, task: Task, rt: _Runtime, streams: list[dict]) -> None:
        if len(streams) == 1:
            part_path = fsutil.ensure_within(task.dest_dir, _part_file_for(task, 0))
            final_name = fsutil.unique_path(task.dest_dir, task.filename)
            final_path = fsutil.ensure_within(task.dest_dir, final_name)
            os.replace(part_path, final_path)
            size = os.path.getsize(final_path)
            self._complete_multi(task, final_path, size)
            return
        if len(streams) != 2:
            raise merge.MergeError(f"不支持 {len(streams)} 路流合并")
        video_part = fsutil.ensure_within(task.dest_dir, _part_file_for(task, 0))
        audio_part = fsutil.ensure_within(task.dest_dir, _part_file_for(task, 1))
        final_name = fsutil.unique_path(task.dest_dir, task.filename)
        final_path = fsutil.ensure_within(task.dest_dir, final_name)
        merge.mux_streams(video_part, audio_part, final_path)
        # 合并成功后清理流分片（属于 ClipHarbor 自己的临时文件）
        for p in (video_part, audio_part):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
        size = os.path.getsize(final_path)
        self._complete_multi(task, final_path, size)

    def _complete_multi(self, task: Task, final_path: str, size: int) -> None:
        self.db.update_fields(
            task.id,
            filename=os.path.basename(final_path),
            file_path=final_path,
            bytes_downloaded=size,
            bytes_total=size,
            status=TaskStatus.COMPLETED.value,
            phase=TaskPhase.DONE.value,
            completed_at=now_ts(),
            sha256=fsutil.sha256_file(final_path),
            verified=1,
            error="",
        )

    def _reconcile_part(self, task: Task, part_path: str, total: int,
                        recorded_bytes: int, accepts_ranges: bool) -> int:
        """分片协调：仅在支持 Range 且分片自洽时续传，否则重建。"""
        if recorded_bytes <= 0 or not accepts_ranges or not os.path.exists(part_path):
            return 0
        part_size = os.path.getsize(part_path)
        if total and part_size == total:
            return part_size
        if part_size <= recorded_bytes and not (total and part_size > total):
            return part_size
        try:
            os.remove(part_path)
        except OSError:
            pass
        return 0

    def _persist_streams(self, task: Task, streams: list[dict]) -> None:
        self.db.update_fields(
            task.id, streams_json=json.dumps(streams, ensure_ascii=False)
        )

    def _progress(self, task_id: str, rt: _Runtime, downloaded: int) -> None:
        delta = downloaded - rt.last_cum
        if delta > 0:  # 真实新增传输字节进入速度统计
            rt.last_cum = downloaded
            rt.meter.on_bytes(delta)
        now = time.monotonic()
        if now - self._last_db_write.get(task_id, 0.0) >= _DB_WRITE_INTERVAL:
            self._last_db_write[task_id] = now
            self.db.update_fields(
                task_id,
                bytes_downloaded=downloaded,
                avg_speed_bps=round(rt.meter.average_bps(), 1),
                peak_speed_bps=round(rt.meter.peak_bps(), 1),
            )

    _last_db_write: dict[str, float] = {}
    _last_stream_save: dict[str, float] = {}

    def _finish(
        self, task_id: str, status: TaskStatus, rt: _Runtime, *, error: str = ""
    ) -> None:
        rt.meter.pause_segment()
        fields: dict = {
            "status": status.value,
            "phase": TaskPhase.IDLE.value,
            "avg_speed_bps": round(rt.meter.average_bps(), 1),
            "peak_speed_bps": round(rt.meter.peak_bps(), 1),
        }
        if error:
            fields["error"] = error
        if status == TaskStatus.COMPLETED:
            fields["completed_at"] = now_ts()
        if status == TaskStatus.CANCELLED:
            # 只清理 ClipHarbor 自己的分片/段文件/合并临时文件，不碰任何其他文件
            task = self.db.get_task(task_id)
            if task and task.dest_dir:
                leftovers: list[str] = []
                count = max(len(task.streams), 1)
                for index in range(count):
                    leftovers.append(_part_file_for(task, index))
                for path in leftovers:
                    try:
                        if os.path.exists(fsutil.ensure_within(task.dest_dir, path)):
                            os.remove(path)
                    except (OSError, ValueError):
                        pass
                # 分段下载的段文件与合并临时文件（均为 ClipHarbor 专属命名）
                try:
                    import glob as _glob

                    for path in _glob.glob(os.path.join(task.dest_dir, "*.clipharbor.seg")) \
                            + _glob.glob(os.path.join(task.dest_dir, "*.clipharbor.merge.tmp")):
                        try:
                            if fsutil.ensure_within(task.dest_dir, path) and os.path.isfile(path):
                                os.remove(path)
                        except (OSError, ValueError):
                            pass
                except OSError:
                    pass
            fields["bytes_downloaded"] = 0
        self.db.update_fields(task_id, **fields)
