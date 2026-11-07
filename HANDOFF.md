# ClipHarbor 交接文档（Windows → Mac 继承）

> 交接日期：2026-11-07 · 交接原因：Mac 是 iPhone IPA 签名构建的场所
> 本文档是唯一入口：读完即可在三端中的任何一端继续开发。

## 0. 项目一句话

**ClipHarbor = 三端私有视频下载管理系统。**
Windows 是功能完整的下载中心（已完成并实测）；iPhone 端走"云构建 IPA + SideStore 每周自动续签"（本 Mac 负责构建/签名）；安卓端可完全独立解析 YouTube（A1 源码就绪）。三端共用协议 v1.0，互不依赖、可协同。

## 1. 目录导览

```
ClipHarbor/
├── HANDOFF.md              ← 本文件
├── README.md               # 总览
├── docs/
│   ├── PROTOCOL.md         # 跨平台协议 v1.0（数据模型 + REST API + 同步规则）★两端共用
│   ├── schemas/            # 6 个 JSON Schema
│   ├── STATUS.md           # 三端阶段进度总表（4 轮迭代记录）
│   ├── REVIEW-MOBILE.md    # 移动端可行性全面审查（iOS 26 / Android）
│   └── ANDROID-PLAN.md     # 安卓版计划（A1–A5 阶段）
├── windows/                # ★ 已完成：Phase 1–4，90 项真实测试全部通过
│   ├── clipharbor/         #   引擎(core)/SQLite(db)/适配器/CLI/FastAPI(server)/PySide6 UI
│   ├── tests/              #   6 个测试套件（真实网络）
│   └── docs/screenshots/   #   桌面 UI 真实截图 5 张
├── ios/                    # ★ 本次 Mac 上的重点
│   ├── ClipHarbor/         #   Swift 源码（引擎/SQLite/cobalt适配器/SwiftUI）
│   ├── project.yml         #   XcodeGen 工程定义（Bundle ID: com.clipharbor.app）
│   ├── Tests/              #   XCTest（SpeedMeter/HistoryStore）
│   └── Docs/               #   INSTALL-IOS26 / SHORTCUTS-COBALT / 可行性报告×2 / 构建签名
├── android/                # ★ A1 源码完成（未编译）
│   └── app/                #   Kotlin/Compose + 内嵌 yt-dlp 0.18.1 + 自研直链引擎
└── .github/workflows/      # build-ipa.yml：免费云构建未签名 IPA
```

## 2. 三端状态总表

| 端 | 状态 | 已验证 | 待完成 |
| --- | --- | --- | --- |
| Windows | ✅ Phase 1–4 完成 | 90 项测试真实网络通过 | Phase 6 安装包（你已说不优先） |
| iOS | ✅ CI 编译通过（2026-11-07 Mac 接手，仓库 `TonyHuang7Asu/clipharbor-app`） | Swift 编译零报错 + 11 项单元测试通过 + 未签名 IPA 已产出 | ①真机验收 7 项 ②SideStore 安装与排障 ③电脑 cobalt 容器恢复在线+代理（本网络封锁 googlevideo） |
| Android | 🚧 A1 源码完成 | **未编译未验证** | Android Studio 打开 `android/` → 首次编译核对 4 处 API（android/README.md）→ 真机测试 |

## 3. 已完成工作与实测结果（4 轮迭代）

**第 1 轮（Windows Phase 1 + iOS Phase 0）**
- Windows 下载引擎：Range 断点续传、真实速度（滑动窗口/平均/峰值，暂停不计入）、
  SQLite 历史（参数绑定、只增不清）、并发 2、暂停/恢复/取消/重试、崩溃恢复、
  磁盘保护、失败分类、URL 去重 —— `test_live.py` 25/25 + 安全测试 10/10
- iOS Phase 0：后台 URLSession 引擎 + SQLite + SwiftUI 最小界面 + 备份导入导出
  （诚实标注：未编译）

**第 2 轮（Windows Phase 2/3/4）**
- Phase 2：批量+播放列表展开+ZIP（含 manifest，失败不删原文件）+ 音视频分离
  双流 + ffmpeg 无损合并 —— bilibili 端到端 71.7MB（24/24）
- Phase 3：PySide6 桌面 UI（真实速度曲线）—— 离屏截图 5 张
- Phase 4：FastAPI 后台 + 6 位配对码 + token（只存哈希）+ Range 文件传输（18/18）
- 本轮修复的真实 bug：并发分片名冲突(WinError 32)、CGNAT 网段 SSRF 漏洞、
  失败任务不进历史、Task.file_path property 覆盖权威路径等 9 项

**第 3 轮（提速 + 稳定性 + 移动端调研）**
- 分段并发下载（默认 4 段，段级断点续传，9/9；实测：OVH 限单 IP 无收益，
  对"限单连接"CDN 如 googlevideo 预期有收益）
- yt-dlp SABR/PO Token 客户端回退链 + `update-ytdlp` 一键更新
- iOS 26 安装矩阵 / 安卓可行性调研

**第 4 轮（cobalt 落地 + 安卓立项 + IPA 云构建）**
- **cobalt 已部署**：Windows 电脑 Docker 容器 `clipharbor-cobalt`（11.7.1，
  开机自启，`http://192.168.0.105:9000/`），**真实 YouTube 解析已验证**
  （返回 1080p 隧道链接+文件名）。⚠️ 隧道出流在该网络为 0 字节（googlevideo
  被封锁）；自检命令见 `ios/Docs/SHORTCUTS-COBALT.md` 第 2 节；有代理则在
  Docker Desktop → Settings → Proxies 配置后重启容器。
- iOS App 新增 **CobaltAdapter**：粘贴 YouTube 链接 → 电脑解析 → 手机原生下载
  （诚实标注"解析在电脑端"）；设置页可改地址。
- GitHub Actions **IPA 云构建**（build-ipa.yml）+ git 仓库初始化（2+ 提交）。
- 安卓 A1 源码交付（10 个 Kotlin 文件 + Gradle 工程）。

## 4. 【Mac 上你要做的】iOS 构建与签名完整流程

### 方式 A：云构建（最简单，本 Mac 都可以不参与构建）
1. `cd ClipHarbor && git remote add origin https://github.com/<你>/clipharbor.git && git push -u origin main`
   （公共仓库 Actions 免费；推送自动触发）
2. Actions 页下载 `ClipHarbor-unsigned-ipa`
3. iPhone 上 SideStore → "＋" 导入该 IPA → Apple ID 签名安装

### 方式 B：在本 Mac 本地构建
```bash
brew install xcodegen
cd ios && xcodegen generate
open ClipHarbor.xcodeproj      # 选免费个人团队，Cmd+R 真机运行
# 或出未签名 IPA：
xcodebuild archive -project ClipHarbor.xcodeproj -scheme ClipHarbor \
  -configuration Release -destination 'generic/platform=iOS' \
  -archivePath build/ClipHarbor.xcarchive CODE_SIGNING_ALLOWED=NO CODE_SIGN_IDENTITY=""
mkdir -p build/Payload && cp -R build/ClipHarbor.xcarchive/Products/Applications/ClipHarbor.app build/Payload/
cd build && zip -qry ClipHarbor-unsigned.ipa Payload
```
首次编译**预期会有报错**（源码在 Windows 上写就、未经编译）——把报错日志交给
ZCode 修复迭代即可，属计划内流程。单元测试：`Cmd+U`（SpeedMeter/HistoryStore）。

### SideStore 每周自动续签（iOS 26）
- 排障顺序（"打不开"按命中率）：**①开发者模式**（设置→隐私与安全性→开发者模式→开→重启）
  ②7 天证书过期 ③证书信任 ④Anisette 源（-80045）⑤3 App 上限（LiveContainer）
  ⑥配对文件过期（电脑 AltServer/jitterbugpair 重生成）
- "自动续签"的前提：手机定期联网、SideStore 偶尔打开。详见 `ios/Docs/INSTALL-IOS26.md`。

### App 内配置
设置 → YouTube 解析（cobalt·电脑端）：确认 `http://192.168.0.105:9000`
（即 Windows 电脑的地址，手机需与其同一局域网）。

## 5. 待完成清单（按优先级）

1. **[Mac] iOS 首轮编译**：云构建或本地构建 → 修复报错 → 出可用 IPA（P0）
2. **[Mac/iPhone] 真机验收 7 项**（`ios/Docs/PHASE0-FEASIBILITY.md` 第 5 节：
   Windows 关机独立下载直链、速度对照系统流量、视频可播、重启持久化、后台下载、
   空间保护、备份恢复）（P0）
3. **[iPhone] SideStore 排障**（开发者模式等 6 步）（P0）
4. **[Android] 首次编译核对 4 处 API**（android/README.md）→ 真机验证（P1）
5. **[Windows/两端] 协议 Phase 5 联调**：iPhone/Android 客户端 ↔ FastAPI 后台
   （端点已在 PROTOCOL.md 冻结；iOS 侧 CobaltAdapter 已实现"电脑解析"子集）（P2）
6. **[iOS] 批量/队列完整 UI、ZIP（Phase 5）**（P2）
7. **[Windows] PyInstaller 安装包 Phase 6**（P3，你已说不优先）
8. cobalt 隧道在你实际网络的全通验证（若 0 字节 → 配代理）（P1）

## 6. 环境事实与已验证结论（避免重复踩坑）

- **开发网络封锁 YouTube**：www.youtube.com 超时；cobalt 容器内提取 API 可达
  （已验证解析），googlevideo 媒体流封锁（隧道 0 字节）。你的网络可能不同，先自检。
- test-videos.co.uk 间歇超时 → 所有测试源已做**运行时自动择优**（jsdelivr/OVH 兜底）。
- Cloudflare 拦截程序化 UA → 全端统一使用标准浏览器 UA（与 yt-dlp 同策略）。
- YouTube 2026 现状：web 客户端强制 SABR + PO Token → 必须保持 yt-dlp 最新
  （`update-ytdlp`）+ 可选 bgutil-ytdlp-pot-provider；适配器内置客户端回退链。
- Windows 端 ffmpeg 9.0.2 可用（合并用）；未装 ffmpeg 的机器会明确报错而非假装支持。
- sqlite 全参数绑定、下载路径目录包含校验、SSRF 默认拒绝私网 —— 安全基线勿回退。

## 7. 文档索引

| 文档 | 内容 |
| --- | --- |
| `docs/PROTOCOL.md` + `docs/schemas/` | 协议 v1.0（Task/Progress/MediaInfo/…） |
| `docs/REVIEW-MOBILE.md` | 移动端可行性全面审查（本轮） |
| `docs/STATUS.md` | 三端进度总表 |
| `docs/ANDROID-PLAN.md` | 安卓 A1–A5 计划 |
| `ios/Docs/INSTALL-IOS26.md` | IPA 云构建 + SideStore 排障 + 免签名备选 |
| `ios/Docs/SHORTCUTS-COBALT.md` | 快捷指令方案（已部署 cobalt 的使用） |
| `ios/Docs/PHASE0-FEASIBILITY.md` / `YOUTUBE-FEASIBILITY.md` | iOS 可行性 / YouTube 评估 |
| `ios/Docs/BUILD-AND-SIGNING.md` | 构建签名与数据保留 |
| `windows/README.md` | Windows 使用与测试 |
| `android/README.md` | 安卓构建与核对点 |

## 8. 铁律（任何一端继续开发都必须遵守）

1. 仅用于用户自有、获授权或来源允许下载的内容；不绕过 DRM/访问控制。
2. 速度必须来自真实传输统计；不承诺突破来源/网络限制的速度。
3. 绝不删除/移动/覆盖用户文件；不覆盖同名文件；失败只清理自己的临时分片。
4. 支持不了的来源明确标注"未支持"，不伪装（YouTube 本地解析 = 不做）。
5. 数据库只增不清、升级仅增列；Bundle ID 保持稳定（iOS: com.clipharbor.app）。
6. 不把未验证的功能说成已验证（各 README 的"诚实声明"保持更新）。
