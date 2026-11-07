import SwiftUI
import UIKit

/// 后台 URLSession 事件承接：系统在 App 被唤醒处理下载事件时调用本方法，
/// completionHandler 必须在会话事件送达后回调（引擎在 urlSessionDidFinishEvents 中回调），
/// 否则 App 会被系统提前终止、后台下载状态无法上报。
final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication,
                     handleEventsForBackgroundURLSession identifier: String,
                     completionHandler: @escaping () -> Void) {
        BackgroundDownloadEngine.shared.pendingBackgroundCompletionHandler = completionHandler
        _ = BackgroundDownloadEngine.shared   // 确保会话创建并重新关联任务
    }
}

/// ClipHarbor iOS — 原生独立下载器。
/// Bundle ID 保持稳定：com.clipharbor.app（更新/重签名时数据保留的关键，见 Docs/BUILD-AND-SIGNING.md）。
@main
struct ClipHarborApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        WindowGroup {
            RootView()
        }
    }
}
