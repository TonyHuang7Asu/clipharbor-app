"""SQLite 历史与任务持久化。

安全约束：所有含外部输入的查询一律使用参数绑定（? 占位符）；
可更新列名来自内置白名单，绝不拼接用户输入的列名。
数据库长期保存在用户数据目录；升级只做 ALTER 增列，绝不清空历史。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

from ..core.models import Task, TaskPhase, TaskStatus, iso, now_ts

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS downloads (
    id               TEXT PRIMARY KEY NOT NULL,
    url              TEXT NOT NULL,
    source_id        TEXT NOT NULL DEFAULT 'direct',
    title            TEXT NOT NULL DEFAULT '',
    filename         TEXT NOT NULL DEFAULT '',
    dest_dir         TEXT NOT NULL DEFAULT '',
    file_path        TEXT NOT NULL DEFAULT '',
    format_id        TEXT NOT NULL DEFAULT '',
    thumbnail_url    TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'queued',
    phase            TEXT NOT NULL DEFAULT 'idle',
    bytes_downloaded INTEGER NOT NULL DEFAULT 0,
    bytes_total      INTEGER NOT NULL DEFAULT 0,
    error            TEXT NOT NULL DEFAULT '',
    retries          INTEGER NOT NULL DEFAULT 0,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL DEFAULT 0,
    completed_at     REAL,
    avg_speed_bps    REAL NOT NULL DEFAULT 0,
    peak_speed_bps   REAL NOT NULL DEFAULT 0,
    sha256           TEXT NOT NULL DEFAULT '',
    verified         INTEGER NOT NULL DEFAULT 0,
    streams_json     TEXT NOT NULL DEFAULT '',
    http_headers     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads(status);
CREATE INDEX IF NOT EXISTS idx_downloads_url ON downloads(url);
CREATE INDEX IF NOT EXISTS idx_downloads_completed ON downloads(completed_at);
CREATE TABLE IF NOT EXISTS zip_jobs (
    id          TEXT PRIMARY KEY,
    task_ids    TEXT NOT NULL,
    path        TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'zipping',
    error       TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    completed_at REAL
);
CREATE TABLE IF NOT EXISTS app_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# 允许动态 UPDATE 的列白名单（列名永不来自外部输入）
_UPDATABLE = frozenset(
    {
        "url", "source_id", "title", "filename", "dest_dir", "file_path",
        "format_id", "thumbnail_url", "status", "phase", "bytes_downloaded",
        "bytes_total", "error", "retries", "updated_at", "completed_at",
        "avg_speed_bps", "peak_speed_bps", "sha256", "verified",
        "streams_json", "http_headers",
    }
)

_COLUMNS = (
    "id, url, source_id, title, filename, dest_dir, file_path, format_id, "
    "thumbnail_url, status, phase, bytes_downloaded, bytes_total, error, "
    "retries, created_at, updated_at, completed_at, avg_speed_bps, "
    "peak_speed_bps, sha256, verified, streams_json, http_headers"
)


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        url=row["url"],
        source_id=row["source_id"],
        title=row["title"],
        filename=row["filename"],
        dest_dir=row["dest_dir"],
        file_path=row["file_path"] if "file_path" in row.keys() else "",
        format_id=row["format_id"],
        thumbnail_url=row["thumbnail_url"],
        status=TaskStatus(row["status"]),
        phase=TaskPhase(row["phase"]),
        bytes_downloaded=row["bytes_downloaded"],
        bytes_total=row["bytes_total"],
        error=row["error"],
        retries=row["retries"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
        avg_speed_bps=row["avg_speed_bps"],
        peak_speed_bps=row["peak_speed_bps"],
        sha256=row["sha256"],
        verified=bool(row["verified"]),
        streams_json=row["streams_json"] if "streams_json" in row.keys() else "",
        http_headers=row["http_headers"] if "http_headers" in row.keys() else "",
    )


class HistoryDatabase:
    def __init__(self, path: str) -> None:
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA)
            self._migrate_columns()
            self._conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES(?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES(?, ?)",
                ("protocol_version", "1.0"),
            )
            self._conn.commit()

    def _migrate_columns(self) -> None:
        """升级只做 ADD COLUMN 增列（固定语句，无拼接），绝不删表/清空历史。"""
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(downloads)").fetchall()}
        if "streams_json" not in cols:
            self._conn.execute(
                "ALTER TABLE downloads ADD COLUMN streams_json TEXT NOT NULL DEFAULT ''"
            )
        if "http_headers" not in cols:
            self._conn.execute(
                "ALTER TABLE downloads ADD COLUMN http_headers TEXT NOT NULL DEFAULT ''"
            )
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._conn.commit()
            self._conn.close()

    # ---- 基础 CRUD -------------------------------------------------------

    def upsert_task(self, task: Task) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO downloads(
                    id, url, source_id, title, filename, dest_dir, file_path,
                    format_id, thumbnail_url, status, phase, bytes_downloaded,
                    bytes_total, error, retries, created_at, updated_at,
                    completed_at, avg_speed_bps, peak_speed_bps, sha256, verified,
                    streams_json, http_headers
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    task.id, task.url, task.source_id, task.title, task.filename,
                    task.dest_dir, task.file_path, task.format_id, task.thumbnail_url,
                    task.status.value, task.phase.value, task.bytes_downloaded,
                    task.bytes_total, task.error, task.retries, task.created_at,
                    task.updated_at or now_ts(), task.completed_at,
                    task.avg_speed_bps, task.peak_speed_bps, task.sha256,
                    int(task.verified), task.streams_json, task.http_headers,
                ),
            )
            self._conn.commit()

    def update_fields(self, task_id: str, **fields) -> None:
        unknown = set(fields) - _UPDATABLE
        if unknown:
            raise ValueError(f"不允许更新的列: {sorted(unknown)}")
        if not fields:
            return
        fields.setdefault("updated_at", now_ts())
        assignments = ", ".join(f"{col} = ?" for col in fields)
        values = list(fields.values()) + [task_id]
        with self._lock:
            self._conn.execute(
                f"UPDATE downloads SET {assignments} WHERE id = ?", values
            )
            self._conn.commit()

    def set_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        phase: TaskPhase | None = None,
        error: str | None = None,
    ) -> None:
        fields: dict = {"status": status.value}
        if phase is not None:
            fields["phase"] = phase.value
        if error is not None:
            fields["error"] = error
        self.update_fields(task_id, **fields)

    def get_task(self, task_id: str) -> Task | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_COLUMNS} FROM downloads WHERE id = ?", (task_id,)
            ).fetchone()
        return _row_to_task(row) if row else None

    def find_active_by_url(self, url: str) -> Task | None:
        placeholders = ", ".join("?" for _ in TaskStatus.active_states())
        states = [s.value for s in TaskStatus.active_states()]
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_COLUMNS} FROM downloads "
                f"WHERE url = ? AND status IN ({placeholders}) LIMIT 1",
                [url, *states],
            ).fetchone()
        return _row_to_task(row) if row else None

    def tasks_by_status(self, statuses) -> list[Task]:
        states = [s.value for s in statuses]
        placeholders = ", ".join("?" for _ in states)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_COLUMNS} FROM downloads "
                f"WHERE status IN ({placeholders}) ORDER BY created_at",
                states,
            ).fetchall()
        return [_row_to_task(r) for r in rows]

    def all_tasks(self) -> list[Task]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_COLUMNS} FROM downloads ORDER BY created_at DESC"
            ).fetchall()
        return [_row_to_task(r) for r in rows]

    # ---- 历史查询 / 导入导出 ---------------------------------------------

    def history(
        self, query: str = "", *, limit: int = 200, offset: int = 0
    ) -> list[Task]:
        """历史记录：含已完成、失败、取消（协议 DownloadHistory 范围）。"""
        like = f"%{query}%"
        states = [TaskStatus.COMPLETED.value, TaskStatus.FAILED.value,
                  TaskStatus.CANCELLED.value]
        placeholders = ", ".join("?" for _ in states)
        with self._lock:
            if query:
                rows = self._conn.execute(
                    f"SELECT {_COLUMNS} FROM downloads WHERE status IN ({placeholders}) "
                    "AND (title LIKE ? OR url LIKE ? OR filename LIKE ?) "
                    "ORDER BY completed_at DESC LIMIT ? OFFSET ?",
                    [*states, like, like, like, limit, offset],
                ).fetchall()
            else:
                rows = self._conn.execute(
                    f"SELECT {_COLUMNS} FROM downloads WHERE status IN ({placeholders}) "
                    "ORDER BY completed_at DESC LIMIT ? OFFSET ?",
                    [*states, limit, offset],
                ).fetchall()
        return [_row_to_task(r) for r in rows]

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM downloads GROUP BY status"
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    # ---- ZIP 任务与 KV ----------------------------------------------------

    def insert_zip_job(self, job_id: str, task_ids: list[str], path: str = "",
                       status: str = "zipping", error: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO zip_jobs(id, task_ids, path, status, error, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (job_id, json.dumps(task_ids), path, status, error, time.time()),
            )
            self._conn.commit()

    def finish_zip_job(self, job_id: str, *, path: str, status: str, error: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE zip_jobs SET path = ?, status = ?, error = ?, completed_at = ? "
                "WHERE id = ?",
                (path, status, error, time.time(), job_id),
            )
            self._conn.commit()

    def kv_get(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM app_meta WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def kv_set(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO app_meta(key, value) VALUES(?, ?)", (key, value)
            )
            self._conn.commit()

    def kv_delete(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM app_meta WHERE key = ?", (key,))
            self._conn.commit()

    def export_json(self) -> str:
        payload = {
            "protocol": "clipharbor",
            "apiVersion": "1.0",
            "exportedAt": iso(now_ts()),
            "records": [t.to_dict() for t in self.history(limit=1_000_000)],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def export_all_tasks_json(self) -> str:
        """任务清单导出（含全部状态的任务，Phase 2）。"""
        payload = {
            "protocol": "clipharbor",
            "apiVersion": "1.0",
            "exportedAt": iso(now_ts()),
            "records": [t.to_dict() for t in self.all_tasks()],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def import_json(self, text: str) -> tuple[int, int]:
        """导入历史 JSON。返回 (导入条数, 跳过条数)。

        只导入已完成/失败的历史记录；活动任务不导入（避免状态错乱）。
        已存在的 id 直接跳过，绝不覆盖本地数据。
        """
        data = json.loads(text)
        records = data.get("records", data if isinstance(data, list) else [])
        imported = skipped = 0
        with self._lock:
            for rec in records:
                tid = str(rec.get("taskId", "")).strip()
                status = str(rec.get("status", ""))
                if not tid or status not in (
                    TaskStatus.COMPLETED.value,
                    TaskStatus.FAILED.value,
                    TaskStatus.CANCELLED.value,
                ):
                    skipped += 1
                    continue
                exists = self._conn.execute(
                    "SELECT 1 FROM downloads WHERE id = ?", (tid,)
                ).fetchone()
                if exists:
                    skipped += 1
                    continue
                self._conn.execute(
                    """
                    INSERT INTO downloads(
                        id, url, source_id, title, filename, dest_dir, file_path,
                        format_id, thumbnail_url, status, phase, bytes_downloaded,
                        bytes_total, error, retries, created_at, updated_at,
                        completed_at, avg_speed_bps, peak_speed_bps, sha256, verified
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        tid,
                        str(rec.get("sourceUrl", "")),
                        str(rec.get("sourceId", "direct")),
                        str(rec.get("title", "")),
                        str(rec.get("filename", "")),
                        str(rec.get("destDir", "")),
                        str(rec.get("filePath", "")),
                        str(rec.get("formatId", "")),
                        str(rec.get("thumbnailUrl", "")),
                        status,
                        TaskPhase.DONE.value if status == TaskStatus.COMPLETED.value
                        else TaskPhase.IDLE.value,
                        int(rec.get("bytesDownloaded", 0) or 0),
                        int(rec.get("bytesTotal", 0) or 0),
                        str(rec.get("error", "")),
                        int(rec.get("retries", 0) or 0),
                        _parse_iso(rec.get("createdAt")) or now_ts(),
                        _parse_iso(rec.get("updatedAt")) or 0.0,
                        _parse_iso(rec.get("completedAt")),
                        float(rec.get("avgSpeedBps", 0) or 0),
                        float(rec.get("peakSpeedBps", 0) or 0),
                        str(rec.get("sha256", "")),
                        1 if rec.get("verified") else 0,
                    ),
                )
                imported += 1
            self._conn.commit()
        return imported, skipped


def _parse_iso(value) -> float | None:
    if not value:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None
