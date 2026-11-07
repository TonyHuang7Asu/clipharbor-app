"""Phase 3 离屏 UI 验证：真实引擎 + 真实下载中的界面截图。

运行：python tests/test_ui_offscreen.py
输出：/tmp/clipharbor-ui/*.png（供人工/视觉核查；界面数据全部真实）
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

from clipharbor.core.manager import DownloadManager
from clipharbor.core.models import TaskStatus
from clipharbor.db.history import HistoryDatabase
from clipharbor.ui.main_window import MainWindow

OUT = os.path.join(tempfile.gettempdir(), "clipharbor-ui")


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="clipharbor-ui-")
    db = HistoryDatabase(os.path.join(workdir, "history.db"))
    manager = DownloadManager(db, max_concurrent=2, default_dest=workdir)
    manager.recover()

    app = QApplication.instance() or QApplication(sys.argv)
    # 离屏平台不带字体：加载系统自带中文字体，保证截图文字可读
    from PySide6.QtGui import QFontDatabase

    for font_path in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
                      "C:/Windows/Fonts/arial.ttf"):
        if os.path.exists(font_path):
            QFontDatabase.addApplicationFont(font_path)
    window = MainWindow(manager, db)
    window.resize(1080, 720)
    window.show()
    app.processEvents()

    # 注入真实任务：一条真实直链下载 + 一条失败任务（历史页可见两种状态）
    from clipharbor.adapters import enqueue_source

    task1, _ = manager.add("https://speed.cloudflare.com/__down?bytes=6000000",
                           dest_dir=workdir, filename="sample_download.bin")
    manager.add("https://test-videos.co.uk/vids/definitely-missing-404.mp4",
                dest_dir=workdir)

    # 等待第一张截图时任务处于真实下载中
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        t = db.get_task(task1.id)
        if t and t.bytes_downloaded > 500_000:
            break
        app.processEvents()
        time.sleep(0.1)
    app.processEvents()
    ok = bool(window.grab().save(os.path.join(OUT, "1-home-downloading.png")))
    print("截图 1-home-downloading:", ok)

    # 等待完成 → 队列/历史状态更新
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        t = db.get_task(task1.id)
        if t and t.status in TaskStatus.terminal_states():
            break
        app.processEvents()
        time.sleep(0.1)
    app.processEvents()
    ok &= bool(window.grab().save(os.path.join(OUT, "2-home-done.png")))
    print("截图 2-home-done:", ok)

    tabs = window.centralWidget()
    tabs.setCurrentIndex(1)
    app.processEvents()
    ok &= bool(window.grab().save(os.path.join(OUT, "3-history.png")))
    print("截图 3-history:", ok)

    tabs.setCurrentIndex(2)
    app.processEvents()
    ok &= bool(window.grab().save(os.path.join(OUT, "4-settings.png")))
    print("截图 4-settings:", ok)

    tabs.setCurrentIndex(3)
    app.processEvents()
    ok &= bool(window.grab().save(os.path.join(OUT, "5-pair.png")))
    print("截图 5-pair:", ok)

    # 程序化断言：表格确实渲染了真实数据（先手动刷新一次，规避 500ms 轮询间隔）
    window.refresh_tables()
    app.processEvents()
    t = db.get_task(task1.id)
    queue_rows = window.queue_table.rowCount()
    history_rows = window.history_table.rowCount()
    print(f"    队列行数={queue_rows}（任务全部结束后应为 0），历史行数={history_rows}")
    checks = [
        ("任务真实完成", t is not None and t.status == TaskStatus.COMPLETED),
        ("队列表格随状态清空", queue_rows == 0),
        ("历史表格渲染行数", history_rows >= 2),
        ("历史含平均速度>0", all(
            (r.avg_speed_bps > 0) for r in db.history() if r.bytes_total > 0)),
    ]
    failures = [name for name, cond in checks if not cond]
    for name, cond in checks:
        print(f"[{'PASS' if cond else 'FAIL'}] {name}")

    manager.shutdown()
    db.close()
    shutil.rmtree(workdir, ignore_errors=True)
    print("截图目录:", OUT)
    return 1 if failures or not ok else 0


if __name__ == "__main__":
    raise SystemExit(main())
