import Foundation

/// Windows 协同（Phase 4 实现）。
///
/// Phase 0 仅冻结共享数据模型（协议 v1.0），保证 iOS 端核心**不依赖**本模块：
/// Windows 离线时以下类型不会被任何下载路径引用。
/// 协议细节见仓库根 docs/PROTOCOL.md。
enum WindowsCompanion {
    /// 协同是否已被用户配对启用（Phase 4 接入 Keychain 存储）。
    static var isPaired: Bool { false }

    static let statusNote = "Phase 4 提供：发现已配对电脑、远程提交/查看/控制任务、历史同步、文件拉取。Windows 离线时本模块自动静默，不影响 iPhone 独立下载。"
}
