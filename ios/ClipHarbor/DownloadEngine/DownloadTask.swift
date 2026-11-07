import Foundation

/// 任务状态 — 与 ClipHarbor 跨平台协议 v1.0 枚举一一对应。
enum TaskStatus: String, Codable, CaseIterable {
    case queued
    case downloading
    case paused
    case retryWait = "retry_wait"
    case merging
    case zipping
    case completed
    case failed
    case cancelled

    var isTerminal: Bool {
        switch self { case .completed, .failed, .cancelled: return true; default: return false }
    }
}

/// 细粒度阶段：网络下载 / 等待重试 / 合并 / 打包 / 完成。
enum TaskPhase: String, Codable {
    case idle, network
    case retryWait = "retry_wait"
    case merge, zip, done
}

/// 与协议 Task 对象一致（CodingKeys 显式映射到 camelCase 协议键名）。
struct DownloadTask: Identifiable, Codable, Equatable {
    var id: String
    var sourceURL: String
    var sourceID: String = "direct"
    var title: String = ""
    var filename: String = ""
    var destDir: String = ""
    var filePath: String = ""
    var formatID: String = ""
    var thumbnailURL: String?
    var status: TaskStatus = .queued
    var phase: TaskPhase = .idle
    var bytesDownloaded: Int64 = 0
    var bytesTotal: Int64 = 0      // 0 = 服务器未提供大小（界面显示“未知”）
    var error: String = ""
    var retries: Int = 0
    var createdAt: Date = Date()
    var updatedAt: Date?
    var completedAt: Date?
    var avgSpeedBps: Double = 0
    var peakSpeedBps: Double = 0
    var sha256: String = ""
    var verified: Bool = false
    /// URLSession 内部 taskIdentifier，用于 App 重启后重新关联后台任务
    var sessionTaskID: Int = -1

    enum CodingKeys: String, CodingKey {
        case id = "taskId"
        case sourceURL = "sourceUrl"
        case sourceID = "sourceId"
        case title, filename, destDir, filePath
        case formatID = "formatId"
        case thumbnailURL = "thumbnailUrl"
        case status, phase
        case bytesDownloaded, bytesTotal, error, retries
        case createdAt, updatedAt, completedAt
        case avgSpeedBps, peakSpeedBps, sha256, verified
        case sessionTaskID = "sessionTaskId"
    }
}

/// 实时进度快照（协议 DownloadProgress）。
struct DownloadProgress: Codable, Equatable {
    var taskId: String
    var status: TaskStatus
    var phase: TaskPhase
    var bytesDownloaded: Int64
    var bytesTotal: Int64
    var speedBps: Double
    var avgSpeedBps: Double
    var peakSpeedBps: Double
    var etaSeconds: Double?
    var error: String
    var updatedAt: Date
}

func formatBytes(_ bytes: Int64) -> String {
    let value = Double(bytes)
    let units = ["B", "KB", "MB", "GB", "TB"]
    var n = value
    var unit = 0
    while n >= 1024 && unit < units.count - 1 { n /= 1024; unit += 1 }
    return unit == 0 ? "\(Int(n)) B" : String(format: "%.1f %@", n, units[unit])
}

func formatSpeed(_ bps: Double) -> String {
    bps <= 0 ? "--" : formatBytes(Int64(bps)) + "/s"
}

func formatETA(_ seconds: Double?) -> String {
    guard let s = seconds, s.isFinite, s > 0 else { return "--" }
    if s < 60 { return String(format: "%.0fs", s) }
    if s < 3600 { return String(format: "%.0fm%.0fs", (s / 60).rounded(.down), s.truncatingRemainder(dividingBy: 60)) }
    return String(format: "%.1fh", s / 3600)
}
