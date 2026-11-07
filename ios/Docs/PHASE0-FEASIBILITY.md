# ClipHarbor iOS — Phase 0 技术可行性评估

日期：2026-10-28 · 评估环境：Windows 11（无 macOS，源码未编译——见"验证状态"）

## 0. 最高优先级结论

**iPhone 独立下载完全可行，不依赖 Windows。**
核心技术栈（Swift + SwiftUI + 后台 URLSession + SQLite + 原生文件 API）全部
由 iOS 系统提供，任何环节都不需要外部服务器。WindowsCompanion 是纯增强模块
（Phase 4），不在任何下载路径上。

## 1. 逐项验证方案与状态

| # | 验证项 | 技术方案 | 状态 |
| --- | --- | --- | --- |
| 1 | 读取文件信息 | GET + `Range: bytes=0-0` 探针：Content-Range 得总大小、Content-Disposition 得文件名、206/200 判定断点支持 | 源码实现（`SourceAdapter.swift/DirectHTTPAdapter`）；未真机验证 |
| 2 | 直接下载到 iPhone | 后台 `URLSession.downloadTask`；系统服务在 App 挂起后继续传输 | 源码实现（`BackgroundDownloadEngine.swift`）；未真机验证 |
| 3 | 实时测速 | `didWriteData` 回调的真实字节增量 → `SpeedMeter` 滑动窗口（当前/平均/峰值；暂停与等待不计入平均） | 源码实现 + 单元测试；未真机验证 |
| 4 | 后台下载 | 后台 URLSession + `sessionSendsLaunchEvents`：系统在事件到达时唤回 App。无需特殊 background mode | 方案确认；**行为细节需真机验证**（iOS 可能延迟到 Wi-Fi/充电时执行，见限制） |
| 5 | 暂停/恢复 | 进程内 `suspend()/resume()`；进程死亡后走 resumeData 路径（保存于 Application Support/Partials） | 源码实现；**已知限制：无 resumeData 时从头下载**（downloadTask 不支持手动 Range，与 Windows 引擎差异已记录） |
| 6 | 持久化保存 | 媒体 → `Documents/Media`（`UIFileSharingEnabled` 开启后"文件"App 可见）；数据库 → `Application Support/ClipHarbor/history.db` | 源码实现 |
| 7 | 导出到"文件"App | 保存目录即文件 App 可见；另提供 `fileExporter`/`fileImporter`（历史 JSON 与文件导出） | 源码实现 |
| 8 | Windows 完全离线 | 全链路（探针→下载→速度→历史→备份）均为 iOS 本地实现，代码中无任何对 Windows 的依赖调用 | 架构保证；真机验证待做 |

## 2. 关键系统约束（如实说明）

1. **后台下载时机由系统调度**：`isDiscretionary=false` 时立即执行，但系统仍可能
   因省电策略推迟（尤其蜂窝网络）。可给用户"仅 Wi-Fi 下载"选项，Phase 2 实现。
2. **App 被杀不等于下载中断**：后台 URLSession 的传输由系统守护进程执行；
   但**挂起(suspend)的任务不会**跨进程存活——这正是我们用 resumeData 补救的原因。
3. **内存限制**：扩展内存上限约 1GB（新机型更多）；流式写盘 + 流式 SHA-256
   （CryptoKit 分块）确保不整载入内存。
4. **存储预检**：`volumeAvailableCapacityForImportantUsage` 预留 64MB 余量；
   空间不足直接拒绝并提示，绝不写满磁盘。
5. **明文 http**：ATS 默认阻止 http 明文；https 全放行，http 直链在 Phase 2 提供
   用户显式开关（`NSAllowsArbitraryLoads` 保持 false，用例外域方式）。

## 3. 模块结构（与 Windows 端同构）

```
ios/ClipHarbor/
├── App/                      # 入口 + 根 TabView
├── AppUI/                    # 首页 / 队列 / 历史 / 设置（Phase 0 最小可用版）
├── DownloadEngine/           # 后台下载引擎 + SpeedMeter + 任务模型（协议 v1.0）
├── HistoryDatabase/          # SQLite（SQLite3 C API，全部参数绑定）
├── SourceAdapters/           # direct（已实现）/ youtube（明确未支持）
├── FileManager/              # 目录、磁盘空间、唯一文件名
├── MediaProcessing/          # AVFoundation 播放校验；ZIP 标注 Phase 5
├── BackupManager/            # 历史 JSON 导入导出（用户选择位置，支持 iCloud Drive）
└── WindowsCompanion/         # Phase 4；Phase 0 仅冻结协议类型
```

## 4. 验证状态（诚实声明）

- 本轮在 **Windows 环境**完成：全部 Swift 源码、XcodeGen 工程定义、单元测试、文档。
- **未做**：Xcode 编译、模拟器运行、真机下载验证。任何"已在 iPhone 上验证"的说法
  都是虚假的，本报告不使用。
- 到达 macOS 后的最短验证路径：`xcodegen generate` → 打开工程 → Cmd+R（模拟器）
  → 真机（免费签名）→ 执行 Phase 0 验收清单（见 STATUS.md）。

## 5. Phase 0 验收清单（待真机执行）

1. 断网/关机的 Windows 旁，iPhone 用直链下载 CC 授权测试视频 ✓
2. 速度显示与系统实际流量一致（对照设置→蜂窝/WLAN 用量）
3. 下载完成视频可在 App 内 / 文件 App 播放（AVAsset 校验轨道）
4. 杀掉 App 重开：任务状态、进度、历史仍在
5. 后台下载：锁屏后传输继续（系统调度范围内），完成后打开 App 记录完整
6. 存储余量 < 文件大小时：拒绝并提示，无残留
7. 导出历史 JSON → 删除 App → 重装 → 导入恢复
