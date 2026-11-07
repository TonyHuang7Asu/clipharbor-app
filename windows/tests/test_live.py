"""Phase 1 真实网络验证：对公网测试文件执行真实下载并核对全部断言。

测试文件（合法可下载）：
  - https://test-videos.co.uk/ Big Buck Bunny 片段（CC 授权测试媒体）
  - https://proof.ovh.net/files/10Mb.dat（OVH 公网测速文件）
  - https://speed.cloudflare.com/__down?bytes=N（Cloudflare 测速端点）

运行：python tests/test_live.py
所有速度/大小数字均来自真实传输，不做任何模拟。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clipharbor.core.fsutil import human_bytes
from clipharbor.core.http_transport import open_url
from clipharbor.core.manager import DownloadManager
from clipharbor.core.models import TaskStatus
from clipharbor.core.probe import probe
from clipharbor.db.history import HistoryDatabase

# 候选测试源：运行时按可达性自动择优（CC 授权媒体 + 公开测速文件兜底）
MEDIA_CANDIDATES = [
    "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/360/Big_Buck_Bunny_360_10s_1MB.mp4",
    "https://cdn.jsdelivr.net/gh/mediaelement/mediaelement-files@master/big_buck_bunny.mp4",
    "https://fastly.jsdelivr.net/gh/mediaelement/mediaelement-files@master/big_buck_bunny.mp4",
    "https://proof.ovh.net/files/10Mb.dat",
]
OVH_10MB = "https://proof.ovh.net/files/10Mb.dat"
OVH_1MB = "https://proof.ovh.net/files/1Mb.dat"
CLOUDFLARE = "https://speed.cloudflare.com/__down?bytes={n}"

RESULTS: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    RESULTS.append(("PASS" if cond else "FAIL", name, detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return cond


def wait_terminal(manager, db, tid, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = db.get_task(tid)
        if task and task.status in TaskStatus.terminal_states():
            return task
        time.sleep(0.15)
    return db.get_task(tid)


def wait_until(db, tid, predicate, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = db.get_task(tid)
        if task and predicate(task):
            return task
        time.sleep(0.1)
    return db.get_task(tid)


def reference_sha256(url: str, retries: int = 3) -> tuple[str, int]:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with open_url(url, timeout=90) as resp:
                data = resp.read()
            return hashlib.sha256(data).hexdigest(), len(data)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2 * (attempt + 1))
    raise last  # type: ignore[misc]


def url_reachable(url: str) -> bool:
    try:
        with open_url(url, timeout=10):
            return True
    except Exception:  # noqa: BLE001
        return False


class _FakeUsage:
    def __init__(self, free: int) -> None:
        self.free = free
        self.total = free
        self.used = 0


def pick_media() -> tuple[str | None, str | None, int | None]:
    """逐候选尝试完整下载，以"能拿到完整参考数据"为准选择测试源。"""
    for url in MEDIA_CANDIDATES:
        try:
            sha, size = reference_sha256(url)
            return url, sha, size
        except Exception:  # noqa: BLE001 — 尝试下一个候选
            continue
    return None, None, None


def main() -> int:
    test_media, ref_sha, ref_size = pick_media()
    if not test_media:
        print("所有候选测试源均不可达（网络波动），本次真实网络验证未执行")
        return 2
    print(f"选用测试源: {test_media}")
    print(f"参考文件: {human_bytes(ref_size)}, sha256={ref_sha[:16]}…")

    workdir = tempfile.mkdtemp(prefix="clipharbor-test-")
    db_path = os.path.join(workdir, "history.db")
    db = HistoryDatabase(db_path)
    manager = DownloadManager(db, max_concurrent=2)
    manager.recover()

    try:
        # ---- 1. 探针：真实读取文件信息 --------------------------------
        info = probe(test_media)
        check("探针读取文件大小", info.total_bytes > 0, f"{human_bytes(info.total_bytes)}")
        check("探针识别断点续传支持", info.accepts_ranges, f"accept-ranges={info.accepts_ranges}")
        check("探针解析文件名", bool(info.filename), info.filename)

        # ---- 2. 完整下载 + 真实速度 + 校验 + 历史 ----------------------
        task, dup = manager.add(test_media, dest_dir=workdir)
        check("URL 去重（首次加入）", not dup)
        done = wait_terminal(manager, db, task.id, timeout=180)
        ok = done.status == TaskStatus.COMPLETED
        check("完整下载完成", ok, f"status={done.status.value} error={done.error}")
        if ok:
            real_size = os.path.getsize(done.file_path)
            check("文件大小与参考一致", real_size == ref_size, f"{human_bytes(real_size)}")
            with open(done.file_path, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
            check("SHA-256 与参考一致", digest == ref_sha)
            check("数据库校验标记", done.verified and done.sha256 == digest)
            check(
                "平均/峰值速度为真实正值",
                done.avg_speed_bps > 0 and done.peak_speed_bps >= done.avg_speed_bps,
                f"avg={human_bytes(done.avg_speed_bps)}/s peak={human_bytes(done.peak_speed_bps)}/s",
            )

        # ---- 3. 暂停 / 恢复（断点续传） --------------------------------
        ovh_info = probe(OVH_10MB)
        task2, _ = manager.add(OVH_10MB, dest_dir=workdir)
        t = wait_until(db, task2.id, lambda t: t.bytes_downloaded > 500_000, timeout=90)
        check("任务开始真实传输", t is not None and t.bytes_downloaded > 500_000,
              f"{human_bytes(t.bytes_downloaded) if t else 'timeout'}")
        manager.pause(task2.id)
        t = wait_until(db, task2.id, lambda t: t.status == TaskStatus.PAUSED, timeout=10)
        check("暂停生效", t is not None and t.status == TaskStatus.PAUSED,
              f"status={t.status.value if t else 'timeout'}")
        paused_bytes = t.bytes_downloaded if t else 0
        time.sleep(0.8)
        still = db.get_task(task2.id)
        check("暂停后字节数不再增长", still.bytes_downloaded == paused_bytes,
              f"停于 {human_bytes(paused_bytes)}")
        manager.resume(task2.id)
        done2 = wait_terminal(manager, db, task2.id, timeout=180)
        ok2 = done2.status == TaskStatus.COMPLETED
        check("恢复后完成（断点续传）", ok2, f"status={done2.status.value} error={done2.error}")
        if ok2:
            check("续传后大小与探测一致",
                  os.path.getsize(done2.file_path) == ovh_info.total_bytes,
                  f"{human_bytes(os.path.getsize(done2.file_path))} / {human_bytes(ovh_info.total_bytes)}")

        # ---- 4. 取消：分片清理，不破坏已有文件 -------------------------
        task3, _ = manager.add(CLOUDFLARE.format(n=8_000_000), dest_dir=workdir)
        wait_until(db, task3.id, lambda t: t.bytes_downloaded > 500_000, timeout=60)
        manager.cancel(task3.id)
        done3 = wait_terminal(manager, db, task3.id, timeout=30)
        check("取消生效", done3.status == TaskStatus.CANCELLED,
              f"status={done3.status.value}")
        leftovers = [f for f in os.listdir(workdir) if f.endswith(".clipharbor.part")]
        check("取消后无残留分片", not leftovers, f"残留: {leftovers}")

        # ---- 5. 并发调度（真实同时 ≤2 路，观察到 2 路并发） -------------
        urls5 = [OVH_1MB, CLOUDFLARE.format(n=6_000_000), CLOUDFLARE.format(n=7_000_000)]
        urls5 = [u for u in urls5 if url_reachable(u)]
        ids5 = [manager.add(u, dest_dir=workdir)[0] for u in urls5]
        max_seen = 0
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            statuses = [db.get_task(x.id).status for x in ids5]
            max_seen = max(max_seen, statuses.count(TaskStatus.DOWNLOADING))
            if all(s in TaskStatus.terminal_states() for s in statuses):
                break
            time.sleep(0.05)
        check("并发不超过上限(2)", max_seen <= 2, f"观察到同时下载峰值={max_seen}")
        check("观察到 ≥2 路并发下载", max_seen >= 2, f"峰值={max_seen}")
        for x in ids5:
            fin = wait_terminal(manager, db, x.id, timeout=120)
            check(f"并发任务 {x.id[:8]} 完成", fin.status == TaskStatus.COMPLETED,
                  f"{fin.status.value} error={fin.error[:80]}")

        # ---- 6. 磁盘空间保护（注入极小剩余空间） ------------------------
        real_disk_usage = shutil.disk_usage
        try:
            shutil.disk_usage = lambda _p: _FakeUsage(1_000)
            task4, _ = manager.add(CLOUDFLARE.format(n=3_000_000), dest_dir=workdir)
            done4 = wait_terminal(manager, db, task4.id, timeout=60)
        finally:
            shutil.disk_usage = real_disk_usage
        check("空间不足时安全失败",
              done4.status == TaskStatus.FAILED and "insufficient_space" in done4.error,
              f"status={done4.status.value} error={done4.error[:80]}")

        # ---- 7. 持久化：重启数据库后历史仍在 ---------------------------
        manager.shutdown()
        db.close()
        db2 = HistoryDatabase(db_path)
        manager2 = DownloadManager(db2, max_concurrent=2)
        manager2.recover()
        hist = db2.history()
        counts = db2.counts()
        check("重启后历史记录保留", len(hist) >= 3, f"完成记录={len(hist)} counts={counts}")

        # ---- 8. 失败分类与原文件保护 ------------------------------------
        bad_task, _ = manager2.add(
            "https://test-videos.co.uk/vids/definitely-missing-404.mp4", dest_dir=workdir
        )
        fin = wait_terminal(manager2, db2, bad_task.id, timeout=60)
        check("404 归类为不可重试失败",
              fin.status == TaskStatus.FAILED and "http_404" in fin.error,
              fin.error[:80])
        completed_files = [t.file_path for t in db2.history()
                           if t.status == TaskStatus.COMPLETED and t.file_path]
        check("失败不影响已完成的文件", all(os.path.exists(p) for p in completed_files),
              f"校验 {len(completed_files)} 个已完成文件")
        manager2.shutdown()
        db2.close()
    finally:
        manager.shutdown()
        db.close()
        shutil.rmtree(workdir, ignore_errors=True)

    print("\n===== 汇总 =====")
    passed = sum(1 for s, *_ in RESULTS if s == "PASS")
    failed = [r for r in RESULTS if r[0] == "FAIL"]
    print(f"通过 {passed}/{len(RESULTS)}")
    for status, name, detail in failed:
        print(f"  [FAIL] {name} — {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
