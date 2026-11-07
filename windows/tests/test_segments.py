"""分段并发下载真实验证：1 段 vs 4 段真实测速对比 + 正确性校验。

运行：python tests/test_segments.py
测试文件：OVH 公网测速文件（支持 Range）。所有速度为真实测量值。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clipharbor.core.downloader import Control, Downloader
from clipharbor.core.fsutil import human_bytes
from clipharbor.core.http_transport import open_url
from clipharbor.core.manager import DownloadManager
from clipharbor.core.models import TaskStatus
from clipharbor.core.probe import probe
from clipharbor.db.history import HistoryDatabase

URL = "https://proof.ovh.net/files/10Mb.dat"
RESULTS: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    RESULTS.append(("PASS" if cond else "FAIL", name, detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return cond


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    info = probe(URL)
    if not check("测试源支持 Range", info.accepts_ranges and info.total_bytes > 0,
                 f"{human_bytes(info.total_bytes)}"):
        print("测试源不可用，未执行")
        return 2

    workdir = tempfile.mkdtemp(prefix="clipharbor-seg-")
    try:
        # ---- 单段 vs 4 段真实测速（同 URL、各自全新目录） ----------------
        timings = {}
        for label, segs in (("单段", 1), ("4段", 4)):
            d = os.path.join(workdir, f"out{segs}")
            os.makedirs(d, exist_ok=True)
            dl = Downloader(control=Control())
            part = os.path.join(d, "file.part")
            t0 = time.monotonic()
            if segs == 1:
                dl.download(URL, part, expected_total=info.total_bytes)
            else:
                dl.download_segmented(URL, d, "file.part", segments=segs,
                                      expected_total=info.total_bytes)
            elapsed = time.monotonic() - t0
            size = os.path.getsize(part)
            timings[label] = (elapsed, size)
            speed = size / elapsed if elapsed > 0 else 0
            print(f"    {label}: {human_bytes(size)} 用时 {elapsed:.2f}s → "
                  f"真实 {human_bytes(speed)}/s")
        e1, s1 = timings["单段"]
        e4, s4 = timings["4段"]
        check("单段下载完整", s1 == info.total_bytes)
        check("分段下载完整", s4 == info.total_bytes)

        # ---- 内容正确性：分段结果与单段逐字节一致 -----------------------
        check("分段内容与单段一致（SHA-256）", _sha(os.path.join(workdir, "out1", "file.part"))
              == _sha(os.path.join(workdir, "out4", "file.part")))
        with open_url(URL, timeout=60) as resp:
            ref = hashlib.sha256(resp.read()).hexdigest()
        check("分段内容与参考一致", _sha(os.path.join(workdir, "out4", "file.part")) == ref)

        # ---- 段文件清理 ---------------------------------------------------
        leftovers = [f for f in os.listdir(os.path.join(workdir, "out4"))
                     if ".clipharbor.seg" in f]
        check("拼接后段文件已清理", not leftovers, str(leftovers))

        # ---- 引擎集成：真实任务使用分段并完成 ------------------------------
        db = HistoryDatabase(os.path.join(workdir, "h.db"))
        manager = DownloadManager(db, max_concurrent=2, segments=4,
                                  default_dest=os.path.join(workdir, "dl"),
                                  segment_min_size=1)  # 10MB 文件也启用分段
        manager.recover()
        task, _ = manager.add(URL, dest_dir=os.path.join(workdir, "dl"))
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            t = db.get_task(task.id)
            if t and t.status in TaskStatus.terminal_states():
                break
            time.sleep(0.2)
        t = db.get_task(task.id)
        check("引擎分段任务完成", t.status == TaskStatus.COMPLETED,
              f"{t.status.value} {t.error[:60]}")
        if t.status == TaskStatus.COMPLETED:
            check("引擎分段文件校验通过", t.verified and t.sha256 == ref)
            check("平均速度真实记录", t.avg_speed_bps > 0,
                  f"avg={human_bytes(t.avg_speed_bps)}/s")
        manager.shutdown()
        db.close()
    finally:
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
