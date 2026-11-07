"""ClipHarbor 私人后台服务（FastAPI，协议 v1.0）—— Phase 4。

安全要求（与 docs/PROTOCOL.md 一致）：
- 默认仅绑定 127.0.0.1；远程监听必须由用户显式 --remote 开启。
- 除 health/pair 外全部需要 Bearer token；token 只在配对响应出现一次，
  数据库只存其 SHA-256，任何日志不记录 token / 配对码。
- 服务端接收的 URL 一律严格校验（仅 http/https，拒绝私网/环回/保留地址）。
- 文件接口只按任务注册表定位（taskId → 数据库 file_path），不接受客户端路径，
  无路径穿越面。
- 配对失败次数限制：同一来源 10 次失败锁定 60 秒。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import shutil
import time

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import PROTOCOL_VERSION, __version__
from .core.manager import DownloadManager
from .core.models import TaskStatus, iso, now_ts
from .core.url_guard import validate_url
from .db.history import HistoryDatabase

_MAX_PAIR_FAILURES = 10
_PAIR_LOCK_SECONDS = 60.0


class CreateTaskBody(BaseModel):
    sourceUrl: str
    filename: str | None = None


class _PairGuard:
    """配对失败限速（内存态，重启即清零）。"""

    def __init__(self) -> None:
        self._failures: dict[str, list[float]] = {}

    def check(self, client_key: str) -> None:
        now = time.monotonic()
        recent = [t for t in self._failures.get(client_key, []) if now - t < _PAIR_LOCK_SECONDS]
        self._failures[client_key] = recent
        if len(recent) >= _MAX_PAIR_FAILURES:
            raise HTTPException(status_code=429, detail="配对失败次数过多，请 60 秒后再试")

    def record_failure(self, client_key: str) -> None:
        self._failures.setdefault(client_key, []).append(time.monotonic())

    def clear(self, client_key: str) -> None:
        self._failures.pop(client_key, None)


def create_app(
    manager: DownloadManager,
    db: HistoryDatabase,
    *,
    pairing_code: str,
    device_name: str = "Windows",
) -> FastAPI:
    app = FastAPI(title="ClipHarbor", version=__version__, docs_url="/docs")
    guard = _PairGuard()
    app.state.manager = manager
    app.state.db = db

    def require_token(authorization: str = Header(default="")) -> str:
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="缺少 Bearer token")
        token = authorization[len("Bearer "):].strip()
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        if not db.kv_get(f"token:{digest}"):
            raise HTTPException(status_code=401, detail="token 无效")
        return token

    # ---- 免认证 -----------------------------------------------------------

    @app.get("/api/v1/health")
    def health():
        dest = manager.default_dest
        try:
            free = shutil.disk_usage(dest).free
        except OSError:
            free = None
        return {
            "status": "ok",
            "protocol": "clipharbor",
            "apiVersion": PROTOCOL_VERSION,
            "device": {"platform": "windows", "name": device_name,
                       "appVersion": __version__, "protocolVersion": PROTOCOL_VERSION},
            "diskFreeBytes": free,
            "now": iso(now_ts()),
        }

    @app.post("/api/v1/pair")
    def pair(request: Request, x_pairing_code: str = Header(default="")):
        client_key = request.client.host if request.client else "unknown"
        guard.check(client_key)
        if not x_pairing_code or not hmac.compare_digest(
            x_pairing_code.strip(), pairing_code
        ):
            guard.record_failure(client_key)
            raise HTTPException(status_code=403, detail="配对码错误")
        guard.clear(client_key)
        token = secrets.token_hex(24)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        db.kv_set(f"token:{digest}", "paired-device")
        return {
            "deviceId": f"win-{hashlib.sha256(device_name.encode()).hexdigest()[:8]}",
            "deviceName": device_name,
            "token": token,  # 仅此一次返回；服务端只保留哈希
            "protocolVersion": PROTOCOL_VERSION,
            "createdAt": iso(now_ts()),
        }

    # ---- 需认证 -----------------------------------------------------------

    @app.get("/api/v1/tasks", dependencies=[Depends(require_token)])
    def tasks(status: str | None = None):
        if status:
            try:
                rows = db.tasks_by_status([TaskStatus(status)])
            except ValueError:
                raise HTTPException(status_code=400, detail="未知状态") from None
        else:
            rows = db.all_tasks()
        return {"records": [t.to_dict() for t in rows]}

    @app.post("/api/v1/tasks", dependencies=[Depends(require_token)])
    def create_task(body: CreateTaskBody):
        url = body.sourceUrl.strip()
        try:
            validate_url(url, allow_private=False)  # 服务端一律严格模式
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        from .adapters import enqueue_source

        try:
            task, duplicate = enqueue_source(manager, url, filename=body.filename)
        except Exception as exc:  # noqa: BLE001 — 解析失败明确返回
            raise HTTPException(status_code=422, detail=f"解析失败: {exc}") from None
        return {"task": task.to_dict(), "duplicate": duplicate}

    @app.get("/api/v1/tasks/{task_id}/progress", dependencies=[Depends(require_token)])
    def progress(task_id: str):
        snap = manager.snapshot(task_id)
        if snap is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return snap

    def _control(task_id: str, action: str):
        task = db.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        getattr(manager, action)(task_id)
        if action == "retry" and not manager.retry(task_id):
            raise HTTPException(status_code=409, detail="任务不在可重试状态")
        refreshed = db.get_task(task_id)
        return {"task": refreshed.to_dict() if refreshed else None}

    @app.post("/api/v1/tasks/{task_id}/pause", dependencies=[Depends(require_token)])
    def pause(task_id: str):
        return _control(task_id, "pause")

    @app.post("/api/v1/tasks/{task_id}/resume", dependencies=[Depends(require_token)])
    def resume(task_id: str):
        return _control(task_id, "resume")

    @app.post("/api/v1/tasks/{task_id}/cancel", dependencies=[Depends(require_token)])
    def cancel(task_id: str):
        return _control(task_id, "cancel")

    @app.post("/api/v1/tasks/{task_id}/retry", dependencies=[Depends(require_token)])
    def retry(task_id: str):
        return _control(task_id, "retry")

    @app.get("/api/v1/history", dependencies=[Depends(require_token)])
    def history(query: str = "", limit: int = 100, offset: int = 0):
        rows = db.history(query, limit=min(limit, 1000), offset=max(offset, 0))
        return {"records": [t.to_dict() for t in rows]}

    @app.get("/api/v1/disk", dependencies=[Depends(require_token)])
    def disk():
        dest = manager.default_dest
        usage = shutil.disk_usage(dest)
        return {"path": dest, "freeBytes": usage.free, "totalBytes": usage.total}

    @app.get("/api/v1/files/{task_id}/meta", dependencies=[Depends(require_token)])
    def file_meta(task_id: str):
        task = db.get_task(task_id)
        if task is None or task.status != TaskStatus.COMPLETED:
            raise HTTPException(status_code=404, detail="任务不存在或未完成")
        import os

        if not task.file_path or not os.path.isfile(task.file_path):
            raise HTTPException(status_code=410, detail="文件已不存在")
        stat = os.stat(task.file_path)
        return {
            "taskId": task.id,
            "filename": task.filename,
            "sizeBytes": stat.st_size,
            "sha256": task.sha256,
            "sourceUrl": task.url,
            "title": task.title,
        }

    @app.get("/api/v1/files/{task_id}/content", dependencies=[Depends(require_token)])
    def file_content(task_id: str, request: Request):
        task = db.get_task(task_id)
        if task is None or task.status != TaskStatus.COMPLETED:
            raise HTTPException(status_code=404, detail="任务不存在或未完成")
        import os

        path = task.file_path
        if not path or not os.path.isfile(path):
            raise HTTPException(status_code=410, detail="文件已不存在")
        size = os.path.getsize(path)
        range_value = request.headers.get("range")
        start, end = 0, size - 1
        status_code = 200
        if range_value and range_value.startswith("bytes="):
            try:
                first, last = range_value[6:].split("-", 1)
                start = int(first) if first else 0
                end = int(last) if last else size - 1
            except ValueError:
                raise HTTPException(status_code=416, detail="Range 无效") from None
            if start >= size or start > end:
                raise HTTPException(status_code=416, detail="Range 超出范围")
            end = min(end, size - 1)
            status_code = 206

        def chunks():
            with open(path, "rb") as fh:
                fh.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    block = fh.read(min(256 * 1024, remaining))
                    if not block:
                        break
                    remaining -= len(block)
                    yield block

        from fastapi.responses import StreamingResponse

        headers = {"Accept-Ranges": "bytes",
                   "Content-Disposition": f'attachment; filename="{task.filename}"'}
        if status_code == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return StreamingResponse(chunks(), status_code=status_code, headers=headers)

    return app
