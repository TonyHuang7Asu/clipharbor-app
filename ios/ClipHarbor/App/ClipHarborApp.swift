import SwiftUI

/// ClipHarbor iOS — 原生独立下载器。
/// Bundle ID 保持稳定：com.clipharbor.app（更新/重签名时数据保留的关键，见 Docs/BUILD-AND-SIGNING.md）。
@main
struct ClipHarborApp: App {
    var body: some Scene {
        WindowGroup {
            RootView()
        }
    }
}
