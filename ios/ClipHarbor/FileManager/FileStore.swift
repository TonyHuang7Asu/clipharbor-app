import Foundation

/// 文件与目录管理：应用内持久目录、磁盘空间、唯一文件名。
enum FileStore {
    /// 完成媒体保存目录（Documents/Media，开启 iTunes/文件 App 可见）
    static var mediaDirectory: URL {
        let url = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Media", isDirectory: true)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    static var applicationSupport: URL {
        let url = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("ClipHarbor", isDirectory: true)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    /// 分片目录（Application Support/Partials，用户不可见）
    static var partialDirectory: URL {
        let url = applicationSupport.appendingPathComponent("Partials", isDirectory: true)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    static func freeDiskBytes() -> Int64? {
        let url = URL(fileURLWithPath: NSHomeDirectory())
        let values = try? url.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
        return values?.volumeAvailableCapacityForImportantUsage
    }

    /// 磁盘空间预检：需要 expectedTotal - resumeFrom + 64MB 余量。
    static func hasSpaceFor(expectedTotal: Int64, resumeFrom: Int64 = 0) -> Bool {
        guard let free = freeDiskBytes() else { return true } // 取不到时由写入错误兜底
        let margin: Int64 = 64 * 1024 * 1024
        return free >= max(expectedTotal - resumeFrom, 0) + margin
    }

    /// 唯一文件名：绝不覆盖已有文件。
    static func uniqueURL(forDirectory dir: URL, filename: String) -> URL {
        var candidate = dir.appendingPathComponent(filename)
        if !FileManager.default.fileExists(atPath: candidate.path) { return candidate }
        let stem = (filename as NSString).deletingPathExtension
        let ext = (filename as NSString).pathExtension
        var i = 2
        while FileManager.default.fileExists(atPath: candidate.path) {
            candidate = dir.appendingPathComponent(ext.isEmpty ? "\(stem) (\(i))" : "\(stem) (\(i)).\(ext)")
            i += 1
        }
        return candidate
    }

    /// 文件名清洗：去除路径分隔符与 Windows 保留字符。
    static func sanitizeFilename(_ name: String) -> String {
        let invalid = CharacterSet(charactersIn: "/\\:*?\"<>|\n\r\t")
        var cleaned = name.components(separatedBy: invalid).joined(separator: "_")
        while cleaned.contains("..") { cleaned = cleaned.replacingOccurrences(of: "..", with: ".") }
        cleaned = cleaned.trimmingCharacters(in: CharacterSet(charactersIn: " ."))
        return cleaned.isEmpty ? "download" : String(cleaned.prefix(150))
    }
}
