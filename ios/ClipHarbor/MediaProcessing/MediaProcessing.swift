import Foundation
import AVFoundation

/// 媒体校验：确认下载完成的视频真实可播放（验收标准 3）。
enum MediaVerifier {
    /// 用 AVAsset 检查轨道与时长；仅本地只读检查，不修改文件。
    static func isPlayable(at path: String) async -> (playable: Bool, detail: String) {
        let asset = AVURLAsset(url: URL(fileURLWithPath: path))
        do {
            let tracks = try await asset.loadTracks(withMediaType: .any)
            let duration = try await asset.load(.duration)
            if tracks.isEmpty {
                return (false, "未发现任何音视频轨道")
            }
            return (true, "轨道 \(tracks.count) 条, 时长 \(duration.seconds)s")
        } catch {
            return (false, "无法读取: \(error.localizedDescription)")
        }
    }
}

/// ZIP 打包（批量下载完成后创建压缩包）—— Phase 5 实现。
/// 说明：iOS 16 无系统级 ZIP 创建 API；将评估 AppleArchive（.aar 非通用 zip）
/// 或引入第三方 MIT 许可实现（如 ZipFoundation）。在此明确标注，不假装支持。
enum ZipMaker {
    static let statusNote = "Phase 5 提供：先评估 ZipFoundation(MIT) 打包 .zip；打包失败绝不删除已下载的原始文件（打包到独立临时目录，完成后才呈现给用户）。"
}
