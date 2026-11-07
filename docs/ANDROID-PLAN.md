# ClipHarbor Android — 可行性评估与开发计划

日期：2026-10-29 · 结论先行：**可行，且是三端中唯一能"完全独立解析 YouTube"的端**。
建议立项（Phase A1 起），与 iOS 并行推进。

## 1. 为什么安卓可行（iOS 做不到的）

- **无签名限制**：APK 直接安装（允许未知来源即可），无 7 天/3 App/吊销问题。
- **可嵌入完整 yt-dlp**：成熟开源库 [youtubedl-android](https://github.com/yausername/youtubedl-android)
  把 Python 运行时（arm64-v8a/armeabi-v7a）+ yt-dlp zipapp 打进 APK，运行时以
  ProcessBuilder 启动子进程执行；[Seal](https://github.com/junkfood02/Seal)
  （FOSS，Material You）已验证该路线可支撑 1000+ 站点的日常使用。
- **可捆绑 ffmpeg**：音视频分离流（YouTube 高画质）可直接在设备内无损合并。
- **后台下载自由**：前台服务（Foreground Service）+ 通知栏进度，无 iOS 的系统调度限制。
- 这些恰好绕开了 iOS 端的全部三个死结（签名、沙箱内无法跑 Python、无 ffmpeg）。

## 2. 技术方案

| 项 | 选择 | 说明 |
| --- | --- | --- |
| 语言/UI | Kotlin + Jetpack Compose | 与项目原生策略一致；Material You |
| 引擎 | 双轨：yt-dlp（来源解析+分段流）+ 原生 OkHttp 分段并发（直链/CDN） | 与 Windows 引擎同构（Range 续传、真实速度、SHA-256 校验） |
| yt-dlp 运行时 | youtubedl-android（内嵌 Python + zipapp + ffmpeg） | 关键依赖：`io.github.junkfood02.youtubedl-android` |
| 存储 | MediaStore（Download/Media 集合）+ Room(SQLite) | 历史结构与协议 Task 字段一致 |
| 同步 | 与 Windows 配对复用协议 v1.0（同一 FastAPI 后台） | 安卓既当独立下载器也可当"第二台下载机" |
| 分发 | GitHub Releases 直装 APK（可选 F-Droid） | 无商店审核，随发随更 |

## 3. 风险与对策

1. **yt-dlp 更新滞后**：YouTube SABR/PO Token 变化频繁 → 应用内"一键更新 zipapp"（从
   GitHub 拉最新 yt-dlp zipapp 覆盖 assets），并跟随 youtubedl-android 库升级。
2. **APK 体积**：内嵌 Python + ffmpeg ≈ 60–80MB → 提供"lite"构建（不含 ffmpeg，
   仅预合并格式）作为可选。
3. **厂商后台查杀**（国产 ROM）：引导用户加白名单；前台服务是标准做法，问题不大。
4. **PO Token**：与 Windows 端同一套 bgutil 思路（服务端在局域网时共享 Windows 的
   token 服务；独立运行时依赖 yt-dlp 默认客户端组合）。

## 4. 阶段划分（对应 Windows 的成功路径复制）

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| A1 | 引擎：嵌入 yt-dlp 解析 + 原生分段下载 + 断点续传 + SHA-256 | 真机下授权测试视频，速度与 Segments 生效 |
| A2 | 队列/历史（Room）/并发限制/失败重试/存储空间保护 | 重启恢复、历史持久 |
| A3 | UI（下载中心/历史/设置）+ 速度曲线 | 与 Windows 同视觉风格 |
| A4 | Windows 配对（协议 v1.0 客户端）+ cobalt 备用通道 | 手机↔电脑互操作实测 |
| A5 | 打包发布（GitHub Releases + 更新检查） | 直装可用 |

## 5. 与 iOS 的关系

安卓独立成 App（不共享代码，共享协议与视觉规范）。iOS 端维持 Phase 0 成果；
若未来你换到支持 TrollStore 的固件或购买开发者账号，iOS 路线随时可续。
