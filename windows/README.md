# ClipHarbor Windows

高性能视频下载器与私人下载后台（Python 3.11+）。Phase 1–4 核心功能已完成并实测。

## 当前进度

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| Phase 1 | 单文件下载、真实速度统计、SQLite 历史、并发队列、断点续传、**分段并发（默认 4 段）** | ✅ 25/25 + 9/9 真实网络测试 |
| Phase 2 | 批量下载、播放列表展开、ZIP 打包、任务清单导出、音视频分离+ffmpeg 合并 | ✅ 24/24（含 bilibili 端到端） |
| Phase 3 | PySide6 桌面 UI（下载中心/历史/设置/配对）、速度曲线、yt-dlp 一键更新 | ✅ 4/4 + 截图（docs/screenshots/） |
| Phase 4 | FastAPI 后台、配对码 + token 认证、Range 文件传输 | ✅ 18/18 |
| 稳定性 | yt-dlp SABR/PO Token 客户端回退链 + `update-ytdlp` 自更新 | ✅ 逻辑实现（YouTube 本网络不可达，未真机验证） |
| Phase 5/6 | iOS 协议联调 / Windows 安装包 | ⬜ 未开始 |

## 快速开始

```bash
pip install -r requirements.txt        # yt-dlp / PySide6 / fastapi / uvicorn
python -m clipharbor.ui                # 桌面界面（推荐）

# CLI（引擎测试面）
python -m clipharbor.cli add https://example.com/video.mp4 --watch
python -m clipharbor.cli add "https://www.bilibili.com/video/BVxxxx" --watch   # 自动解析+双流合并
python -m clipharbor.cli batch <url...> --file urls.txt --zip
python -m clipharbor.cli zip <taskId...>
python -m clipharbor.cli history --query 关键词
python -m clipharbor.cli serve --remote   # 私人后台（供 iPhone 配对；默认仅本机）
python -m clipharbor.cli update-ytdlp     # 升级 yt-dlp（YouTube 兼容性关键）
python -m clipharbor.cli --segments 8 add <url> --watch   # 8 段并发（直链类提速）
```

默认并发 2（设置页可调 1–4），保存目录 `~/Downloads/ClipHarbor`，
数据库 `%LOCALAPPDATA%\ClipHarbor\history.db`（只增不清，升级仅增列）。

## 测试（全部真实，无模拟）

```bash
python tests/test_live.py        # Phase 1：下载/速度/续传/取消/并发/空间保护
python tests/test_phase2.py      # Phase 2：批量/ZIP/排序/bilibili 端到端
python tests/test_server.py      # Phase 4：配对/认证/SSRF/Range 传输
python tests/test_ui_offscreen.py# Phase 3：离屏 UI + 截图
python -m pytest tests/test_url_guard.py -q
```

## 安全设计

- URL 仅 http/https；**服务端路径一律拒绝私网/环回/CGNAT/保留地址**（每跳重定向复检）
- 配对码 + Bearer token：token 只发一次，库中仅存 SHA-256，日志不记录
- 文件接口按 taskId 注册表定位（无路径穿越面），支持 Range 断点
- SQLite 全参数绑定；下载路径强制目录包含校验
- 绝不覆盖/删除用户文件；失败只清理自己的 `.part` 分片；ZIP 失败不动原文件

## 已知限制（如实说明）

- **YouTube：本开发网络无法直连（连接超时），解析代码已实现但未能本机验证**；
  bilibili 已完整真实验证。其他 yt-dlp 站点走同一代码路径。
- DASH 高画质需要系统 ffmpeg（无损合并，-c copy）；未安装时会明确报错而非伪装。
- CLI 进程退出会中断下载（任务保持可续传）；常驻请用 `serve` 或桌面 UI。
- 桌面 UI 修改并发数需重启生效。
