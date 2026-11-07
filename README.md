# ClipHarbor — Windows + iOS 跨平台下载管理系统

Windows 是功能完整的桌面下载中心；iPhone 是能够独立运行的原生下载器。
两者可以相互连接，但**互不依赖**（Windows 关机时 iOS 全功能可用）。

```
ClipHarbor/
├── docs/
│   ├── PROTOCOL.md        # 跨平台协议 v1.0（数据模型 + REST API + 同步规则）
│   ├── schemas/           # 6 个 JSON Schema（Task/DownloadProgress/MediaInfo/…）
│   ├── ANDROID-PLAN.md    # 安卓版计划（已立项，A1 源码完成）
│   └── STATUS.md          # 三端阶段进度总表（诚实状态）
├── windows/               # Python 3.11+ · Phase 1-4 完成（82+ 项真实测试）
│   └── clipharbor/        #   引擎/UI/FastAPI 后台；cobalt 已部署（iOS 快捷指令通道）
├── ios/                   # Swift/SwiftUI 原生 App（Phase 0 源码完成，未编译）
│   └── Docs/              #   安装矩阵(INSTALL-IOS26)、快捷指令(SHORTCUTS-COBALT)、
│                          #   可行性报告、构建与签名
└── android/               # Kotlin/Compose · Phase A1 源码完成（未编译）
    └── app/               #   内嵌 yt-dlp：三端中唯一可独立解析 YouTube 的客户端
```

## 模块划分（两端同构）

| 模块 | Windows | iOS |
| --- | --- | --- |
| DownloadEngine | `clipharbor/core/`（Downloader/Manager/SpeedMeter/多流合并） | `DownloadEngine/`（BackgroundDownloadEngine/SpeedMeter） |
| DownloadQueue | `core/manager.py`（信号量并发 + SQLite 持久队列 + 排序） | `AppUI/QueueView` + 引擎队列 |
| HistoryDatabase | `db/history.py`（SQLite，参数绑定，含 failed/cancelled 历史） | `HistoryDatabase/`（SQLite3 C API，参数绑定） |
| SourceAdapters | `adapters/`（direct + yt-dlp：bilibili 已实测，YouTube 因网络受限未验证） | `SourceAdapters/`（direct 已实现；YouTube 标记未支持） |
| AppUI | PySide6 桌面应用（`clipharbor/ui/`，含真实速度曲线） | SwiftUI（Phase 0 已含最小界面） |
| MediaProcessing | ffmpeg 无损合并 + ZIP（含清单） | 校验/播放（ZIP Phase 5） |
| FileManager | `core/fsutil.py` | `FileManager/` |
| BackupManager | 历史 JSON 导入导出（CLI + UI） | `BackupManager/` |
| WindowsCompanion | FastAPI 后台 `server.py`（配对+token+Range 传输） | `WindowsCompanion/`（Phase 4） |
| Settings | 设置页（并发/保存目录） | `AppUI/SettingsView` |

## 铁律（两端共同遵守）

1. iOS 核心下载能力绝不依赖 Windows；Windows 只是增强。
2. 速度全部来自真实传输统计，无模拟数据；不受来源/网络限制的速度不承诺。
3. 仅用于用户自有、获授权或来源允许下载的内容；不绕过 DRM 与访问控制。
4. 绝不删除/移动/覆盖用户文件；不覆盖同名文件。
5. 支持不了的来源明确标注"未支持"，不伪装。
6. 数据库只增不清；升级用 ALTER 增列；Bundle ID 稳定。

## 快速开始

- Windows（本机即可）：`windows/README.md` — Phase 1–4 已完成，**82 项真实测试全部通过**
- iOS（需要 macOS + Xcode）：`ios/Docs/BUILD-AND-SIGNING.md` — Phase 0 源码就绪，未编译
- 协议与接口：`docs/PROTOCOL.md`（v1.0 已冻结）+ `docs/schemas/`
- 进度总表：`docs/STATUS.md`
