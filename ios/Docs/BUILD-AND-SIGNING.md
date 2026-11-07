# ClipHarbor iOS — 构建、签名、安装与数据保留

> **iOS 26 用户先看**：`INSTALL-IOS26.md`（免签名路径 / SideStore 修复 / LiveContainer /
> 付费方案决策矩阵）。本文件关注工程构建与签名机制本身。

环境要求：macOS 12+ 与 Xcode 15+（或 Xcode 16），Apple ID。
当前项目在 Windows 上开发，本文件是在 macOS 侧完成构建的完整说明。

## 1. 生成工程并运行

```bash
# 方式 A（推荐）：XcodeGen 生成 .xcodeproj
brew install xcodegen
cd ios && xcodegen generate
open ClipHarbor.xcodeproj

# 方式 B：不装 XcodeGen —— Xcode → File → New → Project → iOS App
#   Product Name: ClipHarbor · Bundle ID: com.clipharbor.app · Interface: SwiftUI
#   然后 "Add Files…" 把 ios/ClipHarbor/ 下各模块目录拖入（Create groups），
#   Tests/ 加入 Test Target。
```

运行测试：`Cmd+U`（ClipHarborTests：SpeedMeter / HistoryStore 单元测试）。
真机运行：连接 iPhone → 选择设备 → `Cmd+R`。

## 2. 免费个人开发者签名（Apple ID，0 美元）

| 事项 | 说明 |
| --- | --- |
| 签名有效期 | **7 天**，到期 App 无法启动，需重新 Build & Run 刷新 |
| 设备数 | 最多 3 台 iPhone |
| 能力限制 | 无推送、无 iCloud 容器等高级能力；后台 URLSession 下载**不需要**特殊能力，免费账户可用 |
| 数据保留 | **保持 Bundle ID `com.clipharbor.app` 不变**、同一 Apple ID 重新签名覆盖安装时，iOS 保留应用沙盒数据（数据库与已下载媒体都在沙盒内，不会丢） |
| 数据丢失风险 | ⚠️ 若更换签名团队（不同 Apple ID）或先删除 App 再装，沙盒数据清空。**删除前先导出历史 JSON + 复制 Documents/Media** |

## 3. 付费开发者账户（99 美元/年）分发方式

| 方式 | 限制 | 适用 |
| --- | --- | --- |
| TestFlight | 构建有效期 **90 天**；内部测试 ≤100 人、外部 ≤10000 人（外部需 Beta 审核） | 日常自用/朋友试用首选，比免费签名省心 |
| Ad Hoc | 每年注册 ≤100 台设备，需收集 UDID | 无越狱设备直装 |
| App Store | 公开发布，需通过审核 | 大范围分发 |

## 4. 更新 / 重签名时的数据保留清单

1. Bundle ID 永远保持 `com.clipharbor.app`（项目设置已固定）。
2. 同证书同 Bundle ID 覆盖安装：沙盒数据保留 ✓。
3. 免费签名 7 天到期：直接在 Xcode 重新 Build & Run 覆盖安装，数据保留 ✓。
4. 换电脑/换证书重签：若描述文件团队变更，iOS 可能要求**先删除旧 App**——
   此时沙盒清空。**安装前务必**：
   - App 内：设置 → 导出历史 JSON（到文件 App / iCloud Drive）；
   - 文件 App：复制 `ClipHarbor/Media/` 下媒体；
   - 重装后：设置 → 导入历史 JSON；媒体放回后历史中的 file_path 需重指
     （Phase 2 提供媒体库重扫描）。
5. 数据库升级只增列不清空；App 更新不会主动删除历史。

## 5. 安全红线

- 本项目**从不**要求、上传或存储 Apple 账号凭据与签名私钥；
  所有签名操作在用户自己的 macOS + Xcode 中完成。
- 不要把 `~/Library/Developer/Xcode` 下的证书/描述文件交给任何人或 CI。

## 6. 真机验收（Phase 0 → Phase 5 通过标准）

按 `ios/Docs/PHASE0-FEASIBILITY.md` 第 5 节清单逐项执行；
Windows 主机关机状态下完成独立下载测试后，才算 Phase 0 验收通过。
