import Foundation
import Photos

/// 把已完成的视频写入系统相册（仿 videoloader 的"存到相册"）。
/// 权限为 add-only（NSPhotoLibraryAddUsageDescription），App 只能新增、不能读取/删除相册内容。
enum PhotoSaver {
    private static let videoExtensions = ["mp4", "mov", "m4v"]

    /// 是否为可存入相册的视频文件（历史页按钮显隐用）。
    static func isVideo(path: String) -> Bool {
        videoExtensions.contains((path as NSString).pathExtension.lowercased())
    }

    /// 保存视频到相册；返回结果描述（相册只接受视频/图片，音频请走分享存文件）。
    static func saveVideo(at path: String) async throws -> String {
        guard FileManager.default.fileExists(atPath: path) else {
            throw SourceAdapterError.unsupported("文件不存在（可能已被移动或删除）")
        }
        let url = URL(fileURLWithPath: path)
        guard videoExtensions.contains(url.pathExtension.lowercased()) else {
            throw SourceAdapterError.unsupported("音频文件无法存入相册，请长按用「分享」存到文件 App / 其他应用")
        }
        try await PHPhotoLibrary.shared().performChanges {
            PHAssetChangeRequest.creationRequestForAssetFromVideo(atFileURL: url)
        }
        return "已存入相册：\(url.lastPathComponent)"
    }
}
