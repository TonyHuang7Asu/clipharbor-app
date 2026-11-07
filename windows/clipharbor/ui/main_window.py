"""ClipHarbor Windows 桌面界面（PySide6，Phase 3）。

所有数据来自真实引擎（DownloadManager / SQLite / yt-dlp），
速度曲线为每 0.5s 采样的真实传输速率聚合，无模拟数据。
入口：python -m clipharbor.ui
"""

from __future__ import annotations

import os
import threading
import time as _time
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from ..adapters import expand_playlist, is_video_site
from ..adapters.ytdlp_adapter import resolve_media
from ..core.batch import add_batch
from ..core.fsutil import human_bytes
from ..core.manager import DownloadManager
from ..core.merge import ffmpeg_version
from ..core.models import TaskStatus
from ..core.zipper import ZipError, create_zip
from ..db.history import HistoryDatabase

try:
    from .. import __version__
except Exception:  # noqa: BLE001
    __version__ = "dev"


class SpeedChart(QWidget):
    """真实速度曲线：保存最近 N 个采样点（每点 = 0.5s 的聚合速率）。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._points: list[float] = []
        self._cap = 240
        self._max_seen = 1.0
        self.setMinimumHeight(120)

    def push(self, bps: float) -> None:
        self._points.append(float(bps))
        if len(self._points) > self._cap:
            self._points.pop(0)
        if bps > self._max_seen:
            self._max_seen = bps
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(30, 30, 34))
        w, h = self.width(), self.height()
        points = self._points
        if len(points) >= 2:
            peak = max(max(points), self._max_seen * 0.05, 1.0)
            pen = QPen(QColor(80, 200, 120), 1.6)
            painter.setPen(pen)
            step = w / (self._cap - 1)
            start = self._cap - len(points)
            prev = None
            for i, v in enumerate(points):
                x = int((start + i) * step)
                y = int(h - 6 - (h - 20) * min(v / peak, 1.0))
                if prev:
                    painter.drawLine(prev[0], prev[1], x, y)
                prev = (x, y)
        painter.setPen(QColor(160, 160, 160))
        painter.drawText(8, 16, f"当前 {human_bytes(points[-1] if points else 0)}/s"
                               f"    峰值 {human_bytes(self._max_seen)}/s")
        painter.end()


class PreviewPanel(QWidget):
    """链接解析预览：真实元数据（取不到显示 未知）。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        self.thumb = QLabel("无封面")
        self.thumb.setFixedSize(160, 90)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet("background:#222; color:#888; border-radius:4px;")
        layout.addWidget(self.thumb)
        info = QFormLayout()
        self.title = QLabel("—")
        self.uploader = QLabel("未知")
        self.duration = QLabel("未知")
        self.note = QLabel("—")
        info.addRow("标题:", self.title)
        info.addRow("上传者:", self.uploader)
        info.addRow("时长:", self.duration)
        info.addRow("来源:", self.note)
        wrap = QWidget()
        wrap.setLayout(info)
        layout.addWidget(wrap, 1)

    def show_media(self, media) -> None:
        self.title.setText(media.title or "未知")
        self.uploader.setText(media.uploader or "未知")
        d = media.duration_seconds
        self.duration.setText(f"{int(d // 60)}分{int(d % 60)}秒" if d else "未知")
        self.note.setText(media.note or "—")
        if media.thumbnail_url:
            threading.Thread(target=self._load_thumb, args=(media.thumbnail_url,),
                             daemon=True).start()

    def _load_thumb(self, url: str) -> None:
        try:
            from ..core.http_transport import open_url

            with open_url(url, timeout=10) as resp:
                data = resp.read()
            img = QImage.fromData(data)
            if not img.isNull():
                pixmap = QPixmap.fromImage(img).scaled(
                    160, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                QTimer.singleShot(0, lambda: self.thumb.setPixmap(pixmap))
        except Exception:  # noqa: BLE001 — 缩略图失败显示占位
            pass


class MainWindow(QMainWindow):
    parse_done = Signal(object)   # ResolvedMedia 或 str(error)
    batch_done = Signal(str)

    def __init__(self, manager: DownloadManager, db: HistoryDatabase) -> None:
        super().__init__()
        self.manager = manager
        self.db = db
        self.setWindowTitle(f"ClipHarbor Windows v{__version__}")
        self.resize(1080, 720)
        self._build_ui()
        self.parse_done.connect(self._on_parse_done)
        self.batch_done.connect(lambda s: QMessageBox.information(self, "批量结果", s))

        self._poll = QTimer(self)
        self._poll.timeout.connect(self.refresh_tables)
        self._poll.start(500)
        self.refresh_tables()

    # ---- UI 构建 ---------------------------------------------------------

    def _build_ui(self) -> None:
        tabs = QTabWidget()
        tabs.addTab(self._build_home_tab(), "下载中心")
        tabs.addTab(self._build_history_tab(), "历史")
        tabs.addTab(self._build_settings_tab(), "设置")
        tabs.addTab(self._build_pair_tab(), "设备配对")
        self.setCentralWidget(tabs)
        self.statusBar().showMessage(
            f"ffmpeg: {ffmpeg_version()}　|　数据库: {self.db.path}")

    def _build_home_tab(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)

        single = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("粘贴视频/直链地址（支持直链与 yt-dlp 支持的站点）")
        preview_btn = QPushButton("解析预览")
        preview_btn.clicked.connect(self.on_preview)
        add_btn = QPushButton("添加下载")
        add_btn.clicked.connect(self.on_add)
        single.addWidget(self.url_input, 1)
        single.addWidget(preview_btn)
        single.addWidget(add_btn)
        root.addLayout(single)

        self.preview = PreviewPanel()
        root.addWidget(self.preview)

        batch_box = QGroupBox("批量下载（每行一个链接，播放列表自动展开）")
        batch_layout = QVBoxLayout(batch_box)
        self.batch_input = QPlainTextEdit()
        self.batch_input.setPlaceholderText("https://…\nhttps://…\n# 井号开头为注释")
        self.batch_input.setFixedHeight(64)
        batch_row = QHBoxLayout()
        batch_btn = QPushButton("批量添加")
        batch_btn.clicked.connect(self.on_batch_add)
        zip_btn = QPushButton("打包已选中任务为 ZIP")
        zip_btn.clicked.connect(self.on_zip_selected)
        batch_row.addWidget(batch_btn)
        batch_row.addWidget(zip_btn)
        batch_row.addStretch(1)
        batch_layout.addWidget(self.batch_input)
        batch_layout.addLayout(batch_row)
        root.addWidget(batch_box)

        self.chart = SpeedChart()
        root.addWidget(self.chart)

        splitter = QSplitter(Qt.Vertical)
        self.queue_table = QTableWidget(0, 7)
        self.queue_table.setHorizontalHeaderLabels(
            ["ID", "标题/文件", "进度", "已下载/总量", "速度", "剩余", "状态"])
        self.queue_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.queue_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.queue_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.queue_table.setContextMenuPolicy(Qt.ActionsContextMenu)
        for label, fn in (("暂停", lambda: self._selected_action("pause")),
                          ("继续", lambda: self._selected_action("resume")),
                          ("取消", lambda: self._selected_action("cancel")),
                          ("重试", lambda: self._selected_action("retry")),
                          ("前移", lambda: self._selected_move(-1)),
                          ("打开所在文件夹", self._open_folder)):
            act = QAction(label, self)
            act.triggered.connect(fn)
            self.queue_table.addAction(act)
        splitter.addWidget(self.queue_table)
        root.addWidget(splitter, 1)
        return page

    def _build_history_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("搜索标题 / URL / 文件名")
        self.history_search.textChanged.connect(self.refresh_tables)
        export_btn = QPushButton("导出历史 JSON")
        export_btn.clicked.connect(self.on_export_history)
        import_btn = QPushButton("导入历史 JSON")
        import_btn.clicked.connect(self.on_import_history)
        row.addWidget(self.history_search, 1)
        row.addWidget(export_btn)
        row.addWidget(import_btn)
        layout.addLayout(row)
        self.history_table = QTableWidget(0, 6)
        self.history_table.setHorizontalHeaderLabels(
            ["时间", "标题/文件", "大小", "平均速度", "峰值速度", "文件状态"])
        self.history_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.history_table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.history_table)
        return page

    def _build_settings_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.concurrent_spin = QSpinBox()
        self.concurrent_spin.setRange(1, 4)
        self.concurrent_spin.setValue(self.manager.max_concurrent)
        form.addRow("同时下载任务数（重启生效）:", self.concurrent_spin)
        self.segments_spin = QSpinBox()
        self.segments_spin.setRange(1, 8)
        self.segments_spin.setValue(self.manager.segments)
        self.segments_spin.valueChanged.connect(
            lambda v: setattr(self.manager, "segments", v))
        form.addRow("分段并发数（1=关闭；仅对支持 Range 的服务器生效，立即生效）:",
                    self.segments_spin)
        dest_row = QHBoxLayout()
        self.dest_label = QLabel(self.manager.default_dest)
        pick = QPushButton("选择…")
        pick.clicked.connect(self.on_pick_dest)
        dest_row.addWidget(self.dest_label, 1)
        dest_row.addWidget(pick)
        form.addRow("保存目录:", dest_row)
        open_btn = QPushButton("打开保存目录")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(self.manager.default_dest)))
        form.addRow("", open_btn)
        from ..core.selfupdate import ytdlp_version

        ytdlp_row = QHBoxLayout()
        self.ytdlp_label = QLabel(f"当前版本: {ytdlp_version() or '未安装'}")
        update_btn = QPushButton("更新 yt-dlp（YouTube 兼容性关键，保持最新）")
        update_btn.clicked.connect(self.on_update_ytdlp)
        ytdlp_row.addWidget(self.ytdlp_label, 1)
        ytdlp_row.addWidget(update_btn)
        form.addRow("yt-dlp:", ytdlp_row)
        return page

    def on_update_ytdlp(self) -> None:
        from ..core.selfupdate import update_ytdlp, ytdlp_version

        def work():
            changed, message = update_ytdlp()
            QTimer.singleShot(0, lambda: (
                self.ytdlp_label.setText(f"当前版本: {ytdlp_version() or '未安装'}"),
                QMessageBox.information(self, "yt-dlp 更新", message)))

        threading.Thread(target=work, daemon=True).start()

    def _build_pair_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        box = QGroupBox("私人后台服务（供 ClipHarbor iOS 配对，协议 v1.0）")
        form = QFormLayout(box)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(8765)
        self.remote_check = QCheckBox("允许局域网设备连接（默认关闭，仅本机）")
        self.code_label = QLabel("—")
        self.code_label.setStyleSheet("font-size:20px; font-weight:bold;")
        form.addRow("端口:", self.port_spin)
        form.addRow("远程访问:", self.remote_check)
        form.addRow("配对码:", self.code_label)
        btn_row = QHBoxLayout()
        self.serve_btn = QPushButton("启动服务")
        self.serve_btn.clicked.connect(self.on_toggle_serve)
        btn_row.addWidget(self.serve_btn)
        btn_row.addStretch(1)
        form.addRow("", btn_row)
        layout.addWidget(box)
        note = QLabel("安全说明：token 只在配对成功时返回一次；服务端不记录 token 与配对码；"
                      "接收的任务 URL 一律拒绝私网/环回地址；文件接口按任务注册表定位，无路径穿越。")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        self._server = None
        return page

    # ---- 动作 ------------------------------------------------------------

    def on_preview(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            return

        def work():
            try:
                if is_video_site(url):
                    media = resolve_media(url)
                else:
                    from ..adapters.ytdlp_adapter import ResolvedMedia, ResolvedStream
                    from ..core.probe import probe

                    info = probe(url)
                    media = ResolvedMedia(
                        webpage_url=info.final_url, title=info.filename or "未知",
                        uploader=None, duration_seconds=None, thumbnail_url=None,
                        streams=[ResolvedStream(
                            url=info.final_url,
                            note="直链" + ("，支持断点续传" if info.accepts_ranges else ""))],
                        formats_display=[],
                        estimated_bytes=info.total_bytes,
                        note=f"直链 {human_bytes(info.total_bytes)}"
                             f"{'（支持断点续传）' if info.accepts_ranges else '（不支持 Range）'}")
                self.parse_done.emit(media)
            except Exception as exc:  # noqa: BLE001
                self.parse_done.emit(str(exc))

        threading.Thread(target=work, daemon=True).start()

    def _on_parse_done(self, media) -> None:
        if isinstance(media, str):
            QMessageBox.warning(self, "解析失败", media)
            return
        self.preview.show_media(media)

    def on_add(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            return
        from ..adapters import enqueue_source

        try:
            task, dup = enqueue_source(self.manager, url)
            if dup:
                QMessageBox.information(self, "重复任务", f"该地址已在队列中：{task.id}")
            self.url_input.clear()
            self.refresh_tables()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "添加失败", str(exc))

    def on_batch_add(self) -> None:
        lines = [l.strip() for l in self.batch_input.toPlainText().splitlines()
                 if l.strip() and not l.strip().startswith("#")]
        if not lines:
            return

        def work():
            expanded: list[str] = []
            for url in lines:
                try:
                    src = expand_playlist(url, limit=50)
                    expanded += [e["url"] for e in src.get("entries", []) if e.get("url")]
                except Exception:  # noqa: BLE001
                    expanded.append(url)
            result = add_batch(self.manager, expanded)
            self.batch_done.emit(result.summary())

        threading.Thread(target=work, daemon=True).start()

    def on_zip_selected(self) -> None:
        rows = {i.row() for i in self.queue_table.selectionModel().selectedRows()}
        ids = []
        for r in rows:
            item = self.queue_table.item(r, 0)
            if item:
                ids.append(item.text())
        ids = [i for i in ids
               if self.db.get_task(i) and self.db.get_task(i).status == TaskStatus.COMPLETED]
        if not ids:
            QMessageBox.information(self, "打包", "请先选择已完成的任务行")
            return
        try:
            job = create_zip(self.db, ids, self.manager.default_dest)
            QMessageBox.information(self, "打包完成", f"{job.files} 个文件\n{job.path}")
        except ZipError as exc:
            QMessageBox.warning(self, "打包失败（原始文件不受影响）", str(exc))

    def _selected_action(self, action: str) -> None:
        rows = {i.row() for i in self.queue_table.selectionModel().selectedRows()}
        for r in rows:
            item = self.queue_table.item(r, 0)
            if item:
                getattr(self.manager, action)(item.text())
        self.refresh_tables()

    def _selected_move(self, delta: int) -> None:
        rows = sorted({i.row() for i in self.queue_table.selectionModel().selectedRows()})
        for r in rows:
            item = self.queue_table.item(r, 0)
            if item:
                self.manager.move(item.text(), delta)
        self.refresh_tables()

    def _open_folder(self) -> None:
        rows = {i.row() for i in self.queue_table.selectionModel().selectedRows()}
        for r in rows:
            item = self.queue_table.item(r, 0)
            if item:
                task = self.db.get_task(item.text())
                if task and task.dest_dir:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(task.dest_dir))

    def on_export_history(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出历史", "clipharbor-history.json",
                                              "JSON (*.json)")
        if not path:
            return
        target = Path(os.path.realpath(path))
        if target.suffix.lower() != ".json":
            target = target.with_suffix(".json")
        if not target.parent.is_dir():
            QMessageBox.warning(self, "导出失败", "目标目录不存在")
            return
        target.write_text(self.db.export_json(), encoding="utf-8")
        QMessageBox.information(self, "导出完成", str(target))

    def on_import_history(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入历史", "", "JSON (*.json)")
        if not path:
            return
        source = Path(os.path.realpath(path))
        if not source.is_file():
            QMessageBox.warning(self, "导入失败", "文件不存在")
            return
        imported, skipped = self.db.import_json(source.read_text(encoding="utf-8"))
        QMessageBox.information(self, "导入完成", f"导入 {imported} 条，跳过 {skipped} 条")
        self.refresh_tables()

    def on_pick_dest(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择保存目录")
        if path:
            self.manager.default_dest = path
            self.dest_label.setText(path)

    def on_toggle_serve(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
            self._server = None
            self.serve_btn.setText("启动服务")
            self.code_label.setText("—")
            return
        import secrets

        import uvicorn

        from ..server import create_app

        code = f"{secrets.randbelow(1000000):06d}"
        host = "0.0.0.0" if self.remote_check.isChecked() else "127.0.0.1"
        app = create_app(self.manager, self.db, pairing_code=code, device_name="Windows")
        server = uvicorn.Server(uvicorn.Config(app, host=host, port=self.port_spin.value(),
                                               log_level="warning", access_log=False))
        self._server = server
        threading.Thread(target=server.run, daemon=True).start()
        self.code_label.setText(code)
        self.serve_btn.setText("停止服务")
        QMessageBox.information(
            self, "服务已启动",
            f"地址 http://{host}:{self.port_spin.value()}\n配对码 {code}（勿泄露，"
            "仅 iPhone 配对时输入）\ntoken 只在配对成功时返回一次。")

    # ---- 刷新 ------------------------------------------------------------

    def refresh_tables(self) -> None:
        total_speed = 0.0
        tasks = self.db.tasks_by_status(TaskStatus.active_states())
        self.queue_table.setRowCount(len(tasks))
        for i, task in enumerate(tasks):
            snap = self.manager.snapshot(task.id) or {}
            total_speed += snap.get("speedBps", 0.0)
            done, total = task.bytes_downloaded, task.bytes_total
            pct = f"{done / total * 100:.1f}%" if total else "—"
            values = [
                task.id, task.title or task.filename or task.url, pct,
                f"{human_bytes(done)}/{human_bytes(total) if total else '?'}",
                f"{human_bytes(snap.get('speedBps', 0))}/s",
                format_eta(snap.get("etaSeconds")),
                _status_text(task, snap),
            ]
            for col, text in enumerate(values):
                self.queue_table.setItem(i, col, QTableWidgetItem(text))
        self.chart.push(total_speed)

        query = self.history_search.text() if hasattr(self, "history_search") else ""
        records = self.db.history(query)
        self.history_table.setRowCount(len(records))
        for i, t in enumerate(records):
            exists = t.file_path and os.path.exists(t.file_path)
            values = [
                _time.strftime("%Y-%m-%d %H:%M", _time.localtime(t.completed_at))
                if t.completed_at else "—",
                t.title or t.filename, human_bytes(t.bytes_total),
                f"{human_bytes(t.avg_speed_bps)}/s",
                f"{human_bytes(t.peak_speed_bps)}/s",
                "存在" if exists else "缺失",
            ]
            for col, text in enumerate(values):
                self.history_table.setItem(i, col, QTableWidgetItem(text))


def _status_text(task, snap) -> str:
    if task.status == TaskStatus.DOWNLOADING and snap.get("speedBps", 0) > 0:
        return "下载中"
    return {
        TaskStatus.QUEUED: "排队中", TaskStatus.DOWNLOADING: "连接中",
        TaskStatus.PAUSED: "已暂停", TaskStatus.RETRY_WAIT: "等待重试",
        TaskStatus.MERGING: "合并中", TaskStatus.ZIPPING: "打包中",
        TaskStatus.COMPLETED: "已完成", TaskStatus.FAILED: "失败",
        TaskStatus.CANCELLED: "已取消",
    }.get(task.status, task.status.value)


def format_eta(seconds) -> str:
    if not seconds or seconds <= 0:
        return "—"
    m, s = divmod(int(seconds), 60)
    return f"{m}分{s:02d}秒" if m else f"{s}秒"


def _default_db_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ClipHarbor", "history.db")


def run_ui() -> int:
    import sys

    app = QApplication.instance() or QApplication(sys.argv)
    db = HistoryDatabase(_default_db_path())
    manager = DownloadManager(db, max_concurrent=2)
    manager.recover()
    window = MainWindow(manager, db)
    window.show()
    return app.exec()


def main() -> int:
    return run_ui()


if __name__ == "__main__":
    raise SystemExit(main())
