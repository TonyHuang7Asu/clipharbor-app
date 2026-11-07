# ClipHarbor Android

iPhone/iOS 之外的第二移动端：**唯一能完全独立解析 YouTube 的 ClipHarbor 客户端**
（内嵌 yt-dlp + Python 运行时 + ffmpeg，来自 [youtubedl-android](https://github.com/yausername/youtubedl-android) 0.18.1）。
APK 直装，无签名限制。计划书见 `docs/ANDROID-PLAN.md`。

## 状态（诚实声明）

- ✅ Phase A1 源码完成：双轨引擎（直链 → 自研 HttpDownloader 分段续传；
  视频站点 → yt-dlp 内嵌下载合并）、SQLite 历史（参数绑定、崩溃恢复）、
  并发 2 + 重试 + 暂停/恢复/取消、MediaStore 落盘、Compose 最小界面、分享接收。
- ⚠️ **未编译、未真机验证**（本开发机为 Windows，无 Android SDK/模拟器）。
  API 表面（youtubedl-android 0.18.1 的 `getInfo/execute/updateYoutubeDL/destroyProcessById`）
  以官方 README 为准，首次编译如遇签名差异按编译器提示微调即可。

## 构建

1. 安装 Android Studio（Koala+）→ Open 本 `android/` 目录
2. 等待 Gradle Sync（依赖：AGP 8.5.2 / Kotlin 2.0.20 / Compose BOM 2024.09）
3. `Run` 到真机/模拟器（minSdk 24）
4. 或命令行：`./gradlew assembleDebug` → `app/build/outputs/apk/debug/app-debug.apk`

## 已知风险（首次编译时核对）

- youtubedl-android 版本 0.18.1（2025-11 发布）——如坐标变更，查
  [Maven Central](https://central.sonatype.com/artifact/io.github.junkfood02.youtubedl-android/library)
- yt-dlp 进度回调为百分比，速度统计因此采用**轮询任务专属目录的真实落盘增量**（已实现，非估算）
- 首次启动会解压内嵌 Python（约 60–80MB 磁盘），后续启动直接使用
- 厂商 ROM 后台查杀：建议在系统设置中给 ClipHarbor 加电池白名单（A2 加引导页）

## 路线

A1 引擎（当前）→ A2 队列/历史 UI/备份 → A3 视觉对齐 Windows → A4 与 Windows 配对
（协议 v1.0）→ A5 GitHub Releases 发布。
