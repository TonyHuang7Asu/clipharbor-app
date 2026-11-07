# iOS 安装路径决策矩阵（iOS 26 实况，2026-11 更新）

你的情况：iPhone 为 iOS 26，SideStore 装好后打不开 → **已定路线：云构建 IPA + SideStore 每周自动续签**（排障见下），免签名路径作为并行备选。

## 〇、IPA 云构建（新增，消除"无 Mac"依赖）★

1. 把仓库推到 GitHub（公共仓库，Actions 免费跑 macOS runner）：
   ```bash
   cd ClipHarbor
   git remote add origin https://github.com/<你的用户名>/clipharbor.git
   git push -u origin main
   ```
2. 推送后自动触发 `Build iOS IPA`（也可在 Actions 页手动触发），
   产物：`ClipHarbor-unsigned.ipa`（未签名，30 天可下载）。
3. iPhone 上用 SideStore → "＋" 导入该 IPA → 用你的 Apple ID 签名安装。
   **SideStore 只负责签名安装，构建永远不依赖 Mac。**
4. 之后每次 iOS 代码更新：`git push` → 新 IPA → SideStore 重新导入即"更新"，
   Bundle ID 不变，数据保留。

## 一、SideStore 打不开的排查（按命中率排序）

1. **开发者模式未开启**（最常见）：设置 → 隐私与安全性 → 开发者模式 → 开 → 重启。
2. **7 天证书过期**：应用过期直接打不开；SideStore 内长按 → Refresh。
3. **证书未信任**：设置 → 通用 → VPN与设备管理 → 信任开发者证书。
4. **Anisette 失效**（-80045）：更换 Anisette 源或自建。
5. **3 App / 10 App ID 上限**：清理或 LiveContainer。
6. 配对文件过期：电脑重新生成（AltServer Windows / jitterbugpair）。

## 二、免签名路径（并行备选，无需任何签名）

这些是 App Store 现成应用或系统能力，**不需要安装任何 IPA、不占用签名名额**：

| 路径 | 能力 | 局限 |
| --- | --- | --- |
| **快捷指令 + 自建 cobalt**（已部署：`http://192.168.0.105:9000`） | 系统级下载到"文件"App；解析已在你的电脑实测成功 | 只有"链接→单文件"，无队列/历史 |
| **a-Shell**（免费 App Store 应用） | 手机本地跑 yt-dlp（pip install），完全独立 | 命令行；无 ffmpeg → ≈≤720p；需手动更新 |
| **Documents by Readdle** 等带浏览器的下载器 | 简单直链/网页下载 | 对 YouTube 的解析依赖厂商服务，随时可能失效 |

快捷指令搭建步骤见 `SHORTCUTS-COBALT.md`。

## 二、修复 SideStore（备选，值得再试一次）

"打不开"最常见原因是 Anisette 服务器失效（错误 -80045）或配对文件过期：

1. 删除后重装最新版 SideStore，用 [SideStore 官方](https://sidestore.io) 的配对流程重新生成配对文件（需要一次电脑/或借用他人电脑运行 AltServer/jitterbugpair）。
2. 更换 Anisette 源（SideStore 设置内切换社区服务器，或自建 anisette-v3-server）。
3. 检查"VPN 与设备管理"里证书状态，清除过期描述文件后重试。
4. 组合 **LiveContainer**：把 ClipHarbor IPA 放进容器运行，只占 1 个应用名额（免费 Apple ID 限 3 个）。
   参考社区完整指南：[SideStore+LiveContainer 从零到完成](https://gist.github.com/sinceohsix/688637ac04695d1ff38f844acc8ba7f3)、[iOS 26 侧载总览](https://builds.io/blog/technologies/ios-technologies/sideload-ios-26-no-jailbreak)。

## 三、付费但一劳永逸

- **Apple 开发者计划（$99/年）**：TestFlight 90 天构建（最省心）、Ad Hoc 100 台设备 1 年有效。
  对"长期稳定使用自研下载器"而言这是最可靠方案，无每周续签、无吊销焦虑。
- 云 Mac / 借用 Mac 仅用于生成证书与构建（免费账号也可以，但仍是 7 天）。

## 四、明确不可用（iOS 26）

- **TrollStore**：CoreTrust 漏洞仅覆盖 iOS 14.0–16.6.1 / 16.7 RC / 17.0，iOS 17.1+（含 26）永久无解。
- 企业证书分发商店（ESign/Scarlet 类）：吊销频繁、随时失效，**不推荐**。

## 五、ClipHarbor IPA 的兼容性说明

ClipHarbor iOS 不使用推送、iCloud、扩展等付费能力，仅标准后台 URLSession ——
免费账号签名可完整工作；SideStore/LiveContainer/TrollStore（若固件允许）均可直接安装，
无需修改工程。构建产物为标准未签名 IPA + 签名说明，见 `BUILD-AND-SIGNING.md`。
