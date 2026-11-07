# ClipHarbor iOS

iPhone 原生下载器（SwiftUI + 后台 URLSession + SQLite）。
**Windows 完全关机时全部功能可用**；Windows 协同为可选增强（Phase 4）。

## 状态（诚实声明）

- ✅ Phase 0 源码与文档完成：独立下载引擎、SQLite 历史、最小 SwiftUI 界面、
  备份导入导出、YouTube 未支持声明、构建/签名文档。
- ✅ **2026-11-07（Mac 接手）：CI 云构建编译通过**（GitHub Actions，
  Xcode 16.4 / iOS 18.5 SDK），**11 项单元测试全部通过**
  （HistoryStore 7 + SpeedMeter 4），产出未签名 IPA（`ios/build/ClipHarbor-unsigned.ipa`
  与 Actions 产物 `ClipHarbor-unsigned-ipa`）。仓库：`TonyHuang7Asu/clipharbor-app`。
- ✅ 本轮新增（仿 x-video-downloader 体验）：清晰度五档选择（最佳/1080/720/480/MP3）、
  YouTube 缩略图（i.ytimg.com 直拼）、并发 2 自动排队、cobalt 隧道过期自动重解析重试、
  历史/队列分享与复制链接、cobalt 连接测试、ATS 局域网放行（NSAllowsLocalNetworking）。
- ⚠️ **未真机验证**：iPhone 侧下载/后台/持久化等 7 项验收待做（见 `Docs/PHASE0-FEASIBILITY.md` 第 5 节）。
- ⚠️ 真机 YouTube 下载依赖：电脑 cobalt 容器在线 + 其网络可达 googlevideo（需代理，本网络实测封锁）。

## 文档

| 文档 | 内容 |
| --- | --- |
| `Docs/PHASE0-FEASIBILITY.md` | 独立下载 8 项能力的方案与验证状态、系统限制 |
| `Docs/YOUTUBE-FEASIBILITY.md` | YouTube 独立解析评估：当前不支持的原因与替代路径 |
| `Docs/BUILD-AND-SIGNING.md` | XcodeGen 构建、免费/付费签名、数据保留与备份 |

## 目录

```
ClipHarbor/          # App 源码（SwiftUI App + 10 个模块）
Tests/               # XCTest：SpeedMeter / HistoryStore
project.yml          # XcodeGen 工程定义（Bundle ID 固定 com.clipharbor.app）
```

## 已知限制（Phase 0）

1. URLSession downloadTask 续传依赖系统 resumeData；无 resumeData 时从头下载
   （Windows 端支持手动 Range 续传，两端差异已在协议文档记录）。
2. 后台下载执行时机由 iOS 系统调度（省电策略可能推迟，蜂窝下尤其明显）。
3. 明文 http 直链默认被 ATS 拦截，Phase 2 提供用户显式开关。
4. ZIP 打包（批量完成压缩）在 Phase 5 提供，已评估方案（见 MediaProcessing 注释）。
