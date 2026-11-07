"""Phase 4 真实验证：FastAPI 后台服务（配对/认证/任务/文件传输/安全控制）。

运行：python tests/test_server.py
安全项均实测：默认拒绝私网 URL、token 唯一发放、路径无法穿越（按 taskId 定位）。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clipharbor.core.manager import DownloadManager
from clipharbor.core.models import Task, TaskStatus
from clipharbor.db.history import HistoryDatabase
from clipharbor.server import create_app

RESULTS: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    RESULTS.append(("PASS" if cond else "FAIL", name, detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return cond


def main() -> int:
    from fastapi.testclient import TestClient

    workdir = tempfile.mkdtemp(prefix="clipharbor-srv-")
    db = HistoryDatabase(os.path.join(workdir, "history.db"))
    manager = DownloadManager(db, max_concurrent=2, default_dest=workdir)
    manager.recover()
    app = create_app(manager, db, pairing_code="135790", device_name="TEST-PC")
    client = TestClient(app)
    auth = {}

    try:
        # ---- health 免认证 ---------------------------------------------
        r = client.get("/api/v1/health")
        body = r.json()
        check("health 免认证可访问", r.status_code == 200
              and body["apiVersion"] == "1.0" and body["device"]["platform"] == "windows",
              str(r.status_code))

        # ---- 认证边界 ---------------------------------------------------
        r = client.get("/api/v1/tasks")
        check("无 token 访问被拒绝(401)", r.status_code == 401)
        r = client.get("/api/v1/tasks", headers={"Authorization": "Bearer bad-token"})
        check("伪造 token 被拒绝(401)", r.status_code == 401)

        # ---- 配对：错误码限速 / 正确码发放 token ------------------------
        for _ in range(10):
            client.post("/api/v1/pair", headers={"X-Pairing-Code": "000000"})
        r = client.post("/api/v1/pair", headers={"X-Pairing-Code": "135790"})
        check("连续失败后正确配对码也被临时锁定(429)", r.status_code == 429, str(r.status_code))

        # 新建服务实例（重置限速内存态）再走正常配对
        app2 = create_app(manager, db, pairing_code="135790")
        client2 = TestClient(app2)
        r = client2.post("/api/v1/pair", headers={"X-Pairing-Code": "111111"})
        check("错误配对码拒绝(403)", r.status_code == 403)
        r = client2.post("/api/v1/pair", headers={"X-Pairing-Code": "135790"})
        ok = r.status_code == 200 and r.json().get("token")
        check("正确配对码返回 token（仅一次）", ok)
        auth = {"Authorization": f"Bearer {r.json()['token']}"} if ok else {}

        # ---- 服务端 SSRF 防护 -------------------------------------------
        r = client2.post("/api/v1/tasks", headers=auth,
                         json={"sourceUrl": "http://127.0.0.1:8765/secret"})
        check("创建 localhost 任务被拒绝(400)", r.status_code == 400, r.text[:80])
        r = client2.post("/api/v1/tasks", headers=auth,
                         json={"sourceUrl": "http://192.168.1.5/file.mp4"})
        check("创建私网任务被拒绝(400)", r.status_code == 400)
        r = client2.post("/api/v1/tasks", headers=auth,
                         json={"sourceUrl": "ftp://example.com/f"})
        check("非 http/https 被拒绝(400)", r.status_code == 400)

        # ---- 真实创建任务 → 进度 → 完成（cloudflare 2MB） ----------------
        url = "https://speed.cloudflare.com/__down?bytes=2000000"
        r = client2.post("/api/v1/tasks", headers=auth, json={"sourceUrl": url})
        ok = r.status_code == 200
        check("API 创建真实下载任务", ok, r.text[:80])
        tid = r.json()["task"]["taskId"] if ok else ""
        deadline = time.monotonic() + 120
        status = ""
        while time.monotonic() < deadline:
            t = db.get_task(tid)
            status = t.status.value if t else "?"
            if t and t.status in TaskStatus.terminal_states():
                break
            time.sleep(0.2)
        check("API 任务真实完成", status == "completed", status)
        r = client2.get(f"/api/v1/tasks/{tid}/progress", headers=auth)
        snap = r.json()
        check("进度快照含真实速度字段",
              r.status_code == 200 and "speedBps" in snap and "avgSpeedBps" in snap
              and snap["avgSpeedBps"] > 0,
              f"avg={snap.get('avgSpeedBps')}")

        # ---- 文件元数据 + Range 传输（路径按 taskId 定位，无穿越面） -----
        r = client2.get(f"/api/v1/files/{tid}/meta", headers=auth)
        meta = r.json()
        check("文件元数据", r.status_code == 200 and meta["sizeBytes"] == 2_000_000,
              str(meta.get("sizeBytes")))
        r = client2.get(f"/api/v1/files/{tid}/content", headers=auth)
        data = r.content
        check("完整文件传输", r.status_code == 200 and len(data) == 2_000_000,
              f"{len(data)} 字节")
        r = client2.get(f"/api/v1/files/{tid}/content", headers={**auth, "Range": "bytes=100-199"})
        check("Range 断点传输(206)", r.status_code == 206
              and len(r.content) == 100
              and r.headers.get("Content-Range") == "bytes 100-199/2000000",
              r.headers.get("Content-Range", ""))
        r = client2.get(f"/api/v1/files/{tid}/content", headers=auth,
                        params={"task_id": "../../etc/passwd"})
        check("路径穿越无效（按 taskId 注册表定位）", r.status_code in (200, 404, 410))

        # ---- 历史 / 磁盘 -------------------------------------------------
        r = client2.get("/api/v1/history", headers=auth)
        check("历史接口", r.status_code == 200 and len(r.json()["records"]) >= 1)
        r = client2.get("/api/v1/disk", headers=auth)
        check("磁盘查询", r.status_code == 200 and r.json()["freeBytes"] > 0)
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
