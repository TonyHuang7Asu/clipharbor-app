"""ClipHarbor Windows Phase 1 命令行入口。

用法示例：
  python -m clipharbor.cli add https://example.com/file.mp4
  python -m clipharbor.cli list
  python -m clipharbor.cli watch            # 实时观察全部活动任务
  python -m clipharbor.cli history --query 关键词
  python -m clipharbor.cli pause <id> | resume <id> | cancel <id> | retry <id>
  python -m clipharbor.cli export-history > history.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import PROTOCOL_VERSION, __version__
from .core.fsutil import human_bytes
from .core.manager import DEFAULT_DEST, DownloadManager
from .core.models import TaskStatus
from .db.history import HistoryDatabase


def default_db_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ClipHarbor", "history.db")


def build_manager(args) -> tuple[DownloadManager, HistoryDatabase]:
    db = HistoryDatabase(args.db)
    manager = DownloadManager(
        db,
        max_concurrent=args.concurrency,
        allow_private_hosts=args.allow_lan,
        segments=getattr(args, "segments", 4),
    )
    return manager, db


def _fmt_task(task) -> str:
    size = (
        f"{human_bytes(task.bytes_downloaded)}/{human_bytes(task.bytes_total)}"
        if task.bytes_total
        else human_bytes(task.bytes_downloaded)
    )
    line = f"[{task.status.value:>10}] {task.id[:8]}  {size:>18}  {task.filename or task.url}"
    if task.error:
        line += f"  ({task.error})"
    return line


def cmd_add(args, manager, db) -> int:
    from .adapters import enqueue_source

    task, duplicate = enqueue_source(
        manager, args.url, dest_dir=args.dir,
        max_height=args.max_height, filename=args.name,
    )
    if duplicate:
        print(f"重复任务，已存在活动任务: {task.id}")
        return 0
    print(f"已加入队列: {task.id}  ->  {task.dest_dir}"
          + (f"（{len(task.streams)} 路流，待合并）" if task.streams else ""))
    if args.watch:
        return _watch(manager, db, task.id)
    print("提示：CLI 进程退出后下载线程会停止（任务保持排队/中断状态，下次运行可恢复）。"
          "请使用 --watch 观察直到完成，或使用 serve 常驻后台服务。")
    return 0


def cmd_batch(args, manager, db) -> int:
    from .adapters import expand_playlist
    from .core.batch import add_batch

    urls: list[str] = list(args.urls)
    if args.file:
        with open(args.file, "r", encoding="utf-8") as fh:
            urls += [line.strip() for line in fh if line.strip() and not line.startswith("#")]
    if not urls:
        print("未提供任何链接")
        return 1
    # 播放列表自动展开
    expanded: list[str] = []
    titles: dict[str, str] = {}
    for url in urls:
        try:
            src = expand_playlist(url, limit=args.limit)
            for e in src.get("entries", []):
                if e.get("url"):
                    expanded.append(e["url"])
                    titles[e["url"]] = e.get("title") or ""
        except Exception as exc:  # noqa: BLE001
            print(f"[展开失败] {url} — {exc}")
    if not expanded:
        print("没有可下载的链接")
        return 1
    print(f"共 {len(expanded)} 个目标（含播放列表展开）")
    result = add_batch(manager, expanded, dest_dir=args.dir, plan_zip=args.zip)
    print(result.summary())
    if args.zip and result.added:
        from .core.zipper import ZipError, create_zip

        print("等待全部完成后打包…（CLI 退出即中断，见提示）")
        ids = [t.id for t in result.added]
        try:
            while True:
                states = [db.get_task(i) for i in ids]
                if all(t and t.status in TaskStatus.terminal_states() for t in states):
                    break
                time.sleep(1.0)
            done_ids = [t.id for t in states if t.status == TaskStatus.COMPLETED]
            if done_ids:
                job = create_zip(db, done_ids, args.dir or manager.default_dest)
                print(f"打包完成: {job.path}（{job.files} 个文件）")
        except ZipError as exc:
            print(f"打包失败（原始文件不受影响）: {exc}")
    return 0


def cmd_zip(args, manager, db) -> int:
    from .core.zipper import ZipError, create_zip

    try:
        job = create_zip(db, args.ids, args.dir or manager.default_dest,
                         name=args.name, compress=args.compress)
    except ZipError as exc:
        print(f"打包失败（原始文件不受影响）: {exc}")
        return 1
    print(f"打包完成: {job.path}（{job.files} 个文件, {human_bytes(job.bytes_total)}）")
    return 0


def cmd_move(args, manager, db) -> int:
    delta = -1 if args.direction == "up" else 1
    ok = manager.move(args.id, delta)
    print("已调整顺序" if ok else "该任务不在等待队列中")
    print("当前队列:", [i[:8] for i in manager.queue_snapshot()])
    return 0 if ok else 1


def cmd_export_tasks(args, manager, db) -> int:
    sys.stdout.write(db.export_all_tasks_json())
    return 0


def _watch(manager, db, only_id: str | None = None) -> int:
    try:
        while True:
            tasks = db.tasks_by_status(TaskStatus.active_states())
            lines = []
            for t in tasks:
                if only_id and t.id != only_id:
                    continue
                snap = manager.snapshot(t.id)
                speed = snap["speedBps"] if snap else 0.0
                eta = snap.get("etaSeconds") if snap else None
                pct = (
                    f"{t.bytes_downloaded / t.bytes_total * 100:5.1f}%"
                    if t.bytes_total
                    else "  --  "
                )
                lines.append(
                    f"{t.id[:8]} {pct} {human_bytes(t.bytes_downloaded)}"
                    f"/{human_bytes(t.bytes_total) if t.bytes_total else '?'}"
                    f"  {human_bytes(speed)}/s  eta={eta if eta is not None else '--'}s"
                    f"  [{t.status.value}]"
                )
            print("\n".join(lines) + " " * 20, end="\r", flush=True)
            if only_id:
                task = db.get_task(only_id)
                if task and task.status in TaskStatus.terminal_states():
                    print()
                    print(_fmt_task(task))
                    return 0 if task.status == TaskStatus.COMPLETED else 1
            time.sleep(0.4)
    except KeyboardInterrupt:
        print()
        print("已退出观察；下载线程随进程结束，中断的任务下次运行时可 resume 续传。")
        return 0


def cmd_list(args, manager, db) -> int:
    tasks = db.all_tasks()
    if not tasks:
        print("(无任务)")
    for t in tasks:
        print(_fmt_task(t))
    return 0


def cmd_history(args, manager, db) -> int:
    rows = db.history(args.query or "", limit=args.limit)
    if not rows:
        print("(无历史记录)")
    for t in rows:
        exists = "存在" if (t.file_path and os.path.exists(t.file_path)) else "缺失"
        print(
            f"{t.id[:8]}  {human_bytes(t.bytes_total)}  avg={human_bytes(t.avg_speed_bps)}/s "
            f"peak={human_bytes(t.peak_speed_bps)}/s  [{exists}]  {t.filename}  {t.url}"
        )
    return 0


def cmd_export(args, manager, db) -> int:
    sys.stdout.write(db.export_json())
    return 0


def cmd_import(args, manager, db) -> int:
    with open(args.file, "r", encoding="utf-8") as fh:
        text = fh.read()
    imported, skipped = db.import_json(text)
    print(f"导入 {imported} 条，跳过 {skipped} 条（重复或非历史记录）")
    return 0


def cmd_stats(args, manager, db) -> int:
    print(json.dumps({"counts": db.counts(), "version": __version__, "protocol": PROTOCOL_VERSION}, ensure_ascii=False, indent=2))
    return 0


def _simple_action(action: str):
    def run(args, manager, db) -> int:
        if action == "retry":
            ok = manager.retry(args.id)
            print("已重新入队" if ok else "该任务不在可重试状态")
            return 0 if ok else 1
        getattr(manager, action)(args.id)
        print(f"已执行 {action}: {args.id}")
        return 0

    return run


def cmd_serve(args, manager, db) -> int:
    """常驻私人后台服务（Phase 4）。默认仅本机可访问。"""
    import secrets

    import uvicorn

    from .server import create_app

    pairing_code = args.pairing_code or f"{secrets.randbelow(1000000):06d}"
    host = "0.0.0.0" if args.remote else "127.0.0.1"
    app = create_app(manager, db, pairing_code=pairing_code, device_name=args.device_name)
    print("=" * 56)
    print("ClipHarbor 私人后台服务已启动")
    print(f"  地址: http://{host}:{args.port}")
    print(f"  配对码: {pairing_code}（仅显示一次，勿泄露； iPhone 配对时输入）")
    print(f"  远程访问: {'已开启（0.0.0.0）' if args.remote else '关闭（仅本机 127.0.0.1）'}")
    print("  token 不会写入任何日志；Ctrl+C 停止")
    print("=" * 56)
    try:
        uvicorn.run(app, host=host, port=args.port, log_level="warning", access_log=False)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_update_ytdlp(args, manager, db) -> int:
    from .core.selfupdate import update_ytdlp, ytdlp_version

    print(f"当前 yt-dlp: {ytdlp_version() or '未安装'}")
    changed, message = update_ytdlp()
    print(message)
    return 0 if not changed or message.startswith("已更新") else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="clipharbor", description="ClipHarbor Windows 下载引擎 CLI")
    parser.add_argument("--db", default=default_db_path(), help="SQLite 数据库路径")
    parser.add_argument("--concurrency", type=int, default=2, help="同时下载任务数（默认 2）")
    parser.add_argument("--allow-lan", action="store_true",
                        help="允许局域网/私有地址来源（默认关闭，仅本机用户显式开启）")
    parser.add_argument("--segments", type=int, default=4,
                        help="分段并发数（1-8，默认 4；1=关闭分段；仅对支持 Range 的服务器生效）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="添加下载任务（直链或视频站点，站点自动解析）")
    p.add_argument("url")
    p.add_argument("--dir", default=None, help="保存目录（默认 ~/Downloads/ClipHarbor）")
    p.add_argument("--name", default=None, help="文件名")
    p.add_argument("--max-height", type=int, default=None, help="最高分辨率（如 720）")
    p.add_argument("--watch", action="store_true", help="添加后实时观察直到结束")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("batch", help="批量下载（多个链接/播放列表，自动展开与去重）")
    p.add_argument("urls", nargs="*")
    p.add_argument("--file", default=None, help="从文本文件读取链接（每行一个）")
    p.add_argument("--dir", default=None)
    p.add_argument("--limit", type=int, default=None, help="播放列表最多展开条数")
    p.add_argument("--zip", action="store_true", help="全部完成后自动 ZIP 打包")
    p.set_defaults(fn=cmd_batch)

    p = sub.add_parser("zip", help="把已完成的任务打包为 ZIP")
    p.add_argument("ids", nargs="+", help="任务 ID（可多个）")
    p.add_argument("--dir", default=None)
    p.add_argument("--name", default=None)
    p.add_argument("--compress", action="store_true", help="DEFLATE 压缩（默认仅存储，视频已压缩）")
    p.set_defaults(fn=cmd_zip)

    p = sub.add_parser("move", help="调整等待队列顺序")
    p.add_argument("id")
    p.add_argument("direction", choices=["up", "down"])
    p.set_defaults(fn=cmd_move)

    p = sub.add_parser("export-tasks", help="导出全部任务清单 JSON 到 stdout")
    p.set_defaults(fn=cmd_export_tasks)

    p = sub.add_parser("serve", help="启动私人后台服务（供 iPhone 等设备配对使用）")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--remote", action="store_true",
                   help="显式开启远程访问（监听 0.0.0.0；默认仅本机）")
    p.add_argument("--pairing-code", default=None, help="指定 6 位配对码（默认随机生成）")
    p.add_argument("--device-name", default="Windows")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("update-ytdlp", help="升级 yt-dlp 到最新版（YouTube 兼容性关键）")
    p.set_defaults(fn=cmd_update_ytdlp)

    p = sub.add_parser("list", help="列出全部任务")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("watch", help="实时观察活动任务")
    p.add_argument("id", nargs="?", default=None)
    p.set_defaults(fn=lambda a, m, d: _watch(m, d, a.id))

    for action in ("pause", "resume", "cancel", "retry"):
        p = sub.add_parser(action, help=f"{action} 指定任务")
        p.add_argument("id")
        p.set_defaults(fn=_simple_action(action))

    p = sub.add_parser("history", help="查询历史记录")
    p.add_argument("--query", default="")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(fn=cmd_history)

    p = sub.add_parser("export-history", help="导出历史 JSON 到 stdout")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("import-history", help="从 JSON 文件导入历史")
    p.add_argument("file")
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("stats", help="数据库统计")
    p.set_defaults(fn=cmd_stats)

    args = parser.parse_args(argv)
    manager, db = build_manager(args)
    try:
        manager.recover()
        return args.fn(args, manager, db)
    finally:
        manager.shutdown()
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
