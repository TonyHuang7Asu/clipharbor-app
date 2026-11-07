# ClipHarbor 三端进度总表（诚实状态，随每阶段更新）

更新时间：2026-10-29（第四轮：cobalt 实际部署 + 安卓 A1 立项源码）

## 第四轮新增（本轮）

- **cobalt 已实际部署**：Docker 容器 `clipharbor-cobalt`（11.7.1，开机自启，
  `http://192.168.0.105:9000/`）。**真实 YouTube 解析已验证**（返回 1080p 隧道链接
  + 文件名）；隧道出流在本开发网络为 0 字节（googlevideo 被网络封锁，与 yt-dlp 同因），
  用户网络的可用性以文档内自检命令为准（含代理接入说明）。
- **iOS 快捷指令方案落地**（`ios/Docs/SHORTCUTS-COBALT.md`）：3 分钟搭建步骤、
  精确 API 调用、保存到"文件"App、自检与排障。零签名、iOS 26 直接可用。
- **安卓 A1 立项并交付源码**（`android/`）：Gradle 工程（AGP 8.5.2/Kotlin 2.0.20）、
  双轨引擎（直链自研下载器 + yt-dlp 内嵌 0.18.1 下载合并）、SQLite 历史（参数绑定、
  崩溃恢复）、并发 2 + 重试、MediaStore 落盘、Compose 界面、系统分享接收。
  **未编译、未真机验证**（本机无 Android SDK），README 已列首次编译核对点。


## 第三轮新增（本轮）

- **引擎分段并发下载**（默认 4 段、≥8MB 且服务器支持 Range 时启用，可调 1–8；
  段文件独立断点续传，拼接后清理）。正确性 9/9：SHA-256 与参考一致。
  真实测速：OVH 对单 IP 限速 → 4 段无收益（2.7 vs 3.6 MB/s，如实记录）；
  对"限单连接"的 CDN（如 YouTube googlevideo，本机无法验证）预期有收益。
- **yt-dlp 稳定性**：SABR/PO Token 客户端回退链（默认 → tv/web_safari → android/ios →
  mweb/web_embedded，仅对客户端相关错误重试）；`update-ytdlp` 命令与设置页一键更新。
- **iOS 26 安装矩阵**（`ios/Docs/INSTALL-IOS26.md`）：TrollStore 确认不可用（iOS 17.1+ 无解）；
  SideStore 修复路径（Anisette/配对文件/LiveContainer）；免签名路径（快捷指令+自建 cobalt、
  a-Shell 跑 yt-dlp、Documents）；付费 TestFlight 路径。
- **安卓版计划**（`docs/ANDROID-PLAN.md`）：可行——youtubedl-android 嵌入 Python+yt-dlp+ffmpeg，
  安卓可完全独立解析 YouTube（三端中唯一）；阶段 A1–A5 已规划，待用户立项确认。


## 项目 A：ClipHarbor Windows（Python 3.12 实测环境）

| 阶段 | 内容 | 状态 | 验证方式与结果 |
| --- | --- | --- | --- |
| Phase 1 | 单文件真实下载、实时/平均/峰值速度、SQLite 历史、并发队列(2)、断点续传、暂停/恢复/取消/重试、崩溃恢复、磁盘空间保护、失败分类、URL 去重 | ✅ 完成 | `test_live.py` 真实网络 **25/25** |
| Phase 2 | 批量下载（多链接+去重+空间预估）、播放列表展开、队列排序、ZIP 打包（含清单，失败不删原文件）、任务清单导出、**音视频分离双流 + ffmpeg 无损合并** | ✅ 完成 | `test_phase2.py` 真实网络 **24/24**（含 bilibili 端到端：解析→双流→合并→71.7MB 有效 mp4） |
| Phase 3 | PySide6 桌面 UI：下载中心（链接/解析预览/批量/速度曲线/队列右键操作）、历史（搜索/导入导出/文件状态）、设置、设备配对（进程内启停服务） | ✅ 完成 | `test_ui_offscreen.py` **5/5** + 5 张真实截图（`docs/screenshots/`，数据全为真实引擎输出） |
| Phase 4 | FastAPI 后台（协议 v1.0 全部端点）、6 位配对码 + Bearer token（仅存哈希）、配对限速、SSRF 严格校验、按 taskId 的 Range 文件传输 | ✅ 完成 | `test_server.py` **18/18**（含真实下载、Range 206、路径穿越无效） |
| Phase 5 | 与 iOS 端协议对齐联调 | ⬜ 待 iOS 真机 | 协议文档与 JSON Schema 已冻结 |
| Phase 6 | Windows 安装包（PyInstaller） | ⬜ 未开始 | — |

测试合计：**82 项检查全部通过**（10 离线安全 + 25 真实下载 + 24 批量/合并 + 18 服务端 + 5 UI）。

### yt-dlp 来源支持（重要环境事实）

- **bilibili：已真实验证**（元数据/上传者/时长/12 个格式/双流计划/Referer 头/端到端下载合并）。
- **YouTube：本开发网络无法直连（www.youtube.com 连接超时）**，解析代码与 bilibili
  完全共用但未能本机验证；已如实标注，不虚报。
- archive.org 同样不可达。其他 yt-dlp 支持的站点按同一代码路径工作。

## 项目 B：ClipHarbor iOS（Swift/SwiftUI）

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| Phase 0 | 独立下载架构可行性报告、工程结构、最小引擎原型源码（后台 URLSession + SQLite + 最小 SwiftUI 界面 + 备份导入导出） | 🚧 源码与文档完成；**未编译、未真机验证**（开发机为 Windows） |
| Phase 0 验收 | 真机独立下载授权测试文件、速度测量、重启持久化、导出到"文件"App | ⬜ 待 macOS 环境 |
| Phase 1-5 | iOS 引擎完善 / 全套 UI / 批量 / Windows 协同 / 签名验证 | ⬜ 未开始 |

## 本轮（第二轮）实际测试结果摘录（真实网络/真实引擎）

- bilibili《Never Gonna Give You Up》官方 MV：解析出 12 个格式 → 1080p 视频流 + 音轨
  双流下载（带 Referer 头）→ ffmpeg 无损合并 → 71.7 MB mp4，avg 27.2 MB/s / peak 86.0 MB/s
- 批量 2 任务 + URL 去重 + ZIP（含 clipharbor-manifest.json，包内 SHA-256 与原文件一致，
  原始文件全部保留）
- FastAPI：正确配对码 → token 一次发放；localhost/私网/ftp 创建任务均被 400 拒绝；
  真实任务 API 创建 → 完成 → 文件 2,000,000 字节完整传输 + Range 206 断点
- UI 离屏截图：真实下载中 4.4%、256 KB/5.7 MB、287.3 KB/s、剩余 19 秒、速度曲线实时上升
- UI 测试还抓出并修复了一个真实 bug：失败任务此前不进历史（历史现含 completed/failed/cancelled）
