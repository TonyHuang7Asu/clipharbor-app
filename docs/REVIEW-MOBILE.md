# 移动端可行性全面审查（iOS 26 / Android）

日期：2026-11-07 · 结论先行：**两端都可行，路线已定且消除了"无 Mac"阻塞**。
- **iOS（你的选择）：云构建 IPA + SideStore 每周自动续签** —— IPA 获取不再依赖 Mac；
  App 内 YouTube 走已部署并实测的电脑端 cobalt。
- **Android：A1 源码完成**，是唯一可完全独立解析 YouTube 的端，下一步真机构建。

## 1. iOS 端（iOS 26）

### 1.1 环境事实与已定路线
| 事项 | 结论 |
| --- | --- |
| TrollStore | ❌ iOS 17.1+ 永久无解（已确认） |
| 签名方式 | **SideStore 设备内每周自动续签**（你的选择，正确）+ 备选免费 App Store 路径 |
| **IPA 从哪来** | ✅ 新增：GitHub Actions 免费 macOS runner 云构建 **未签名 IPA**（公共仓库免费），
  你把仓库推到 GitHub → Actions 产出 `ClipHarbor-unsigned.ipa` → iPhone 用 SideStore 导入签名安装。
  **从此构建/更新不需要任何 Mac。** |
| App 内 YouTube | ✅ CobaltAdapter 已实现：粘贴链接 → 电脑 cobalt 解析（本机已实测解析成功）→
  iPhone 原生引擎真实下载。App 内诚实标注"解析在电脑端，需电脑在线"。 |
| App 内直链下载 | ✅ 已实现（后台 URLSession + 续传 + SHA-256 + 真实速度 + 备份导入导出） |
| iOS 独立解析 YouTube | ❌ 维持不做（高维护成本 + 合规边界），电脑离线时 YouTube 显示未支持 |

### 1.2 SideStore"打不开"排查（按命中率排序）
1. **开发者模式未开启**（iOS 16+ 侧载应用闪退的最常见原因）：设置 → 隐私与安全性 →
   开发者模式 → 打开 → 重启。首次安装后必须做。
2. **7 天证书过期**：过期应用直接无法启动。SideStore 内长按 App → Refresh；
   SideStore 自身也需在 7 天内续签（打开 SideStore 即自动尝试）。
3. **证书未信任**：设置 → 通用 → VPN与设备管理 → 信任你的开发者证书。
4. **Anisette 服务器失效**（报 -80045 等）：SideStore 设置内更换 Anisette 源，
   或自建 anisette-v3-server。
5. **3 App 上限 / 10 App ID 上限**：删除不再用的侧载应用，或用 LiveContainer 容器化。
6. 配对文件过期：电脑上用 [AltServer Windows 版](https://altstore.io) 或
   `jitterbugpair` 重新生成配对文件导入。

### 1.3 "每周自动签名"的真实含义（如实）
- 免费 Apple ID 证书 **7 天有效**；SideStore 在**设备上**自动重新签名（利用本地
  VPN 槽位 + Anisette），前提是：手机定期开机联网、SideStore 偶尔被打开/后台刷新、
  Anisette 源可用。长期关机或断网会导致过期，需手动 Refresh。
- SideStore 首次安装仍需一次电脑（生成配对文件）——你已完成此步，剩下是排障。

### 1.4 剩余风险（如实）
- **Swift 源码未经编译**：云构建的首轮可能报编译错误——这是预期流程，
  把 Actions 日志发我即可修复迭代。
- 隧道链接约 10 分钟有效：cobalt 任务适合"添加即下载"；暂停后若恢复失败，
  重新添加任务即可。
- 你的网络对 googlevideo 的封锁同样影响 iPhone 侧下载（与电脑同因），
  cobalt 隧道经电脑中转，**电脑网络能通则手机链路通**——这是该方案的结构性优势。

## 2. Android 端

| 事项 | 结论 |
| --- | --- |
| 可行性 | ✅ 高：APK 直装（无签名问题）、内嵌 yt-dlp+ffmpeg（youtubedl-android 0.18.1），
  **完全独立解析 YouTube**（三端唯一），后台下载无系统限制 |
| 当前状态 | A1 源码完成：Gradle 工程 + 双轨引擎（直链自研下载器 / yt-dlp 站点下载合并）+
  SQLite 历史（崩溃恢复）+ 并发 2 + Compose 界面 + 分享接收 |
| 验证状态 | ⚠️ **未编译、未真机验证**（本机无 Android SDK）；首次编译需核对 4 处 API 表面
  （见 android/README.md） |
| 下一步 | 你在 Android Studio 打开 `android/` → Sync → Run → 反馈编译/运行结果，我修复迭代 |
| 风险 | yt-dlp 版本更新（应用内一键更新已规划）、APK 体积 ~60–80MB、厂商 ROM 后台查杀（引导白名单） |

## 3. 两端对比与建议投入

| 维度 | iOS 26（SideStore 路线） | Android |
| --- | --- | --- |
| YouTube（电脑在线） | ✅ cobalt 解析 + 手机原生下载 | ✅ yt-dlp 本地解析+下载 |
| YouTube（电脑关机） | ❌（本地解析不做） | ✅ 完全独立 |
| 安装摩擦 | 每周自动续签（SideStore 稳定性依赖 Anisette 生态） | 一次直装，无续签 |
| 长期维护成本 | 中（CI 构建 + SideStore 排障） | 低（直装 APK） |
| 建议顺序 | 先用（已与电脑生态打通） | 并行验证（一次编译即可能用） |

**结论：iOS 路线按你选定的方案推进（云构建 IPA + SideStore）；
安卓在一次成功编译后将成为体验最好的独立端，建议并行验证。**

## 4. 你的行动清单（iOS 优先）

1. 把 `ClipHarbor` 仓库推到 GitHub（公共仓库，Actions 免费）：
   ```bash
   cd ClipHarbor
   git remote add origin https://github.com/<你的用户名>/clipharbor.git
   git push -u origin main      # 推送后自动触发 IPA 云构建
   ```
2. Actions 页下载 `ClipHarbor-unsigned-ipa` → 传到手机（隔空投送不行，用 iCloud 链接/微信文件均可）→ SideStore "+"导入签名安装。
3. 按上表排障 SideStore（先查**开发者模式**）。
4. App 内 设置 → YouTube 解析 确认 cobalt 地址（默认已填 `http://192.168.0.105:9000`）。
5. 首轮编译若报错：把 Actions 日志贴给我，我来修。
