"""Phase 2 真实验证：批量下载、ZIP 打包、队列排序、yt-dlp(bilibili) 端到端。

运行：python tests/test_phase2.py
说明：YouTube 在本开发网络不可达（连接超时），适配器代码与 bilibili 完全共用，
以 bilibili 做真实端到端验证；YouTube 无法在本机验证，已在文档中如实标注。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clipharbor.adapters import expand_playlist
from clipharbor.adapters.ytdlp_adapter import is_available, resolve_media
from clipharbor.core.batch import add_batch
from clipharbor.core.fsutil import human_bytes
from clipharbor.core.manager import DownloadManager
from clipharbor.core.merge import ffmpeg_available
from clipharbor.core.models import TaskStatus
from clipharbor.core.zipper import ZipError, create_zip
from clipharbor.db.history import HistoryDatabase

CLOUDFLARE = "https://speed.cloudflare.com/__down?bytes={n}"
BILIBILI_URL = "https://www.bilibili.com/video/BV1GJ411x7h7"

RESULTS: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    RESULTS.append(("PASS" if cond else "FAIL", name, detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return cond


def wait_terminal(manager, db, tid, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = db.get_task(tid)
        if task and task.status in TaskStatus.terminal_states():
            return task
        time.sleep(0.2)
    return db.get_task(tid)


def main() -> int:
    if not check("网络连通（cloudflare）", _reachable(CLOUDFLARE.format(n=1000000))):
        print("无法访问公网测试文件，本次真实网络验证未执行")
        return 2

    workdir = tempfile.mkdtemp(prefix="clipharbor-p2-")
    db = HistoryDatabase(os.path.join(workdir, "history.db"))
    manager = DownloadManager(db, max_concurrent=2)
    manager.recover()

    try:
        # ---- 1. 批量下载（含去重与空间预估） ---------------------------
        urls = [CLOUDFLARE.format(n=6_000_000), CLOUDFLARE.format(n=7_000_000)]
        result = add_batch(manager, urls, dest_dir=workdir, plan_zip=True)
        check("批量入队 2 个", len(result.added) == 2, result.summary().splitlines()[0])
        dup_result = add_batch(manager, urls, dest_dir=workdir, probe_first=False)
        check("批量去重（同 URL 活动任务）", len(dup_result.added) == 0
              and len(dup_result.duplicates) == 2)
        for t in result.added:
            fin = wait_terminal(manager, db, t.id, timeout=180)
            check(f"批量任务 {t.id[:8]} 完成", fin.status == TaskStatus.COMPLETED,
                  f"{fin.status.value} {fin.error[:60]}")

        # ---- 2. ZIP 打包：内容校验 + 原文件保留 ------------------------
        done_ids = [t.id for t in db.history()]
        originals = {t.file_path: _sha(t.file_path) for t in db.history()}
        job = create_zip(db, done_ids, workdir, name="batch_test.zip")
        check("ZIP 生成", os.path.exists(job.path) and job.files == len(done_ids),
              f"{job.path} {job.files} 个文件")
        with zipfile.ZipFile(job.path) as zf:
            names = zf.namelist()
            check("ZIP 包含全部文件 + 清单",
                  len([n for n in names if n != "clipharbor-manifest.json"]) == len(done_ids)
                  and "clipharbor-manifest.json" in names, str(names))
            manifest = __import__("json").loads(zf.read("clipharbor-manifest.json"))
            check("清单含来源 URL 与 SHA-256",
                  all(r.get("sha256") and r.get("sourceUrl") for r in manifest["files"]))
            for record in manifest["files"]:
                data = zf.read(record["archiveName"])
                check(f"包内 {record['archiveName']} 与原文件一致",
                      hashlib.sha256(data).hexdigest() == record["sha256"])
        check("打包后原始文件全部保留",
              all(os.path.exists(p) and _sha(p) == h for p, h in originals.items()))

        # ---- 3. ZIP 失败安全：缺文件任务 → 明确失败、无半成品 ----------
        missing_task = result.added[0]
        real_path = missing_task.file_path
        db.update_fields(missing_task.id, file_path=os.path.join(workdir, "不存在.mp4"))
        try:
            create_zip(db, [missing_task.id], workdir)
            check("ZIP 失败安全", False, "未按预期抛错")
        except ZipError as exc:
            leftovers = [f for f in os.listdir(workdir) if f.endswith(".zip.part")]
            check("ZIP 失败安全（明确报错且无半成品）",
                  "文件缺失" in str(exc) and not leftovers, str(exc)[:80])
        db.update_fields(missing_task.id, file_path=real_path)

        # ---- 4. 队列排序（确定性：调度器停止时操作等待队列） ------------
        q_db = HistoryDatabase(os.path.join(workdir, "queue.db"))
        q_manager = DownloadManager(q_db, max_concurrent=2)
        q_manager.shutdown()  # 停止调度，使任务留在等待队列中以便测试排序
        q_urls = [CLOUDFLARE.format(n=n) for n in (5_000_000, 5_100_000, 5_200_000)]
        q_tasks = [q_manager.add(u, dest_dir=workdir)[0] for u in q_urls]
        queue = q_manager.queue_snapshot()
        middle = queue[1] if len(queue) >= 2 else None
        if middle and q_manager.move(middle, -1):
            after = q_manager.queue_snapshot()
            check("队列前移生效", after.index(middle) < queue.index(middle),
                  f"{[i[:6] for i in after]}")
        else:
            check("队列前移生效", False, f"队列={queue}")
        q_manager.shutdown()
        q_db.close()

        # ---- 5. 任务清单导出 -------------------------------------------
        exported = db.export_all_tasks_json()
        import json as _json

        records = _json.loads(exported)["records"]
        check("任务清单导出（含全部状态）", len(records) >= 2,
              f"{len(records)} 条（主库：批量任务）")

        # ---- 6. yt-dlp 适配器 + bilibili 端到端（解析→下载→合并） -------
        if not check("yt-dlp 可用", is_available()):
            pass
        else:
            src = expand_playlist(BILIBILI_URL)
            check("来源解析（标题/上传者/时长）",
                  src["type"] == "single" and bool(src["entries"][0]["title"])
                  and bool(src["entries"][0]["uploader"])
                  and (src["entries"][0]["duration"] or 0) > 0,
                  f"{src['entries'][0]['title'][:20]}… / {src['entries'][0]['uploader']}")
            media = resolve_media(BILIBILI_URL)
            check("解析为双流计划（DASH）", len(media.streams) == 2
                  and bool(media.streams[0].headers), media.note)
            check("格式列表可用", len(media.formats_display) > 0
                  and any(f.get("height") for f in media.formats_display),
                  f"{len(media.formats_display)} 个格式")
            check("ffmpeg 可用（合并前提）", ffmpeg_available())

            task, dup = manager.add_resolved(
                url=media.webpage_url, title=media.title, filename=media.title + ".mp4",
                source_id="ytdlp",
                streams=[{"url": s.url, "headers": s.headers, "note": s.note,
                          "bytes_total": s.bytes_total, "bytes_downloaded": 0}
                         for s in media.streams],
                http_headers=media.streams[0].headers if media.streams else None,
                bytes_total=media.estimated_bytes,
                dest_dir=workdir,
                thumbnail_url=media.thumbnail_url or "",
            )
            check("bilibili 任务入队（URL 去重）", not dup)
            fin = wait_terminal(manager, db, task.id, timeout=600)
            ok = fin.status == TaskStatus.COMPLETED
            check("bilibili 端到端完成（下载+合并）", ok,
                  f"{fin.status.value} {fin.error[:100]}")
            if ok:
                size = os.path.getsize(fin.file_path)
                check("合并输出为有效媒体文件", size > 1_000_000,
                      f"{human_bytes(size)} {fin.file_path[-40:]}")
                leftovers = [f for f in os.listdir(workdir) if ".clipharbor.part" in f]
                check("合并后流分片已清理", not leftovers, str(leftovers))
                check("平均/峰值速度真实记录",
                      fin.avg_speed_bps > 0 and fin.peak_speed_bps >= fin.avg_speed_bps,
                      f"avg={human_bytes(fin.avg_speed_bps)}/s peak={human_bytes(fin.peak_speed_bps)}/s")
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


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _reachable(url: str) -> bool:
    from clipharbor.core.http_transport import open_url

    try:
        with open_url(url, timeout=15):
            return True
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    raise SystemExit(main())
