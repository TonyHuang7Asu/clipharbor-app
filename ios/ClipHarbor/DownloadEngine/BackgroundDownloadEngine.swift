import Foundation
import CryptoKit

/// iOS 独立下载引擎。
///
/// - 使用后台 URLSession：App 进入后台/被挂起后系统继续下载，事件到达时唤回 App。
/// - 完全不依赖 Windows 后台；WindowsCompanion 只是可选增强（Phase 4）。
/// - 速度统计来自 didWriteData 的真实字节增量（SpeedMeter）。
/// - 并发上限 2（与 Windows 端一致）：新任务自动排队，槽位空闲时按加入顺序自动补位。
/// - cobalt 任务的"原始链接"与"隧道下载链接"分开存储；隧道过期时重试会自动重新解析。
/// - 已知限制（如实标注）：
///   1. URLSession downloadTask 的续传依赖系统 resumeData；没有 resumeData 时从头下载
///      （不支持手动 Range 续传，Windows 端引擎支持 Range，两端行为差异已记录在案）。
///   2. suspend() 暂停只在本进程存活期间有效；跨进程死亡由 resumeData 路径恢复。
final class BackgroundDownloadEngine: NSObject, ObservableObject {
    static let shared = BackgroundDownloadEngine()

    /// 同时活跃的下载任务数（与 Windows 端并发一致）。
    static let maxConcurrentDownloads = 2

    /// taskId → 实时进度（主线程发布）
    @Published private(set) var liveProgress: [String: DownloadProgress] = [:]

    /// 系统在后台事件送达后交给 App 的 completionHandler（AppDelegate 承接）。
    /// 本会话空闲（urlSessionDidFinishEvents）时必须回调，否则 App 会被系统提前杀死。
    var pendingBackgroundCompletionHandler: (() -> Void)?

    private var session: URLSession!
    private let history: HistoryStore
    private let lock = NSLock()
    private let pumpLock = NSLock()
    private var meters: [String: SpeedMeter] = [:]
    private var sessionTasks: [Int: URLSessionDownloadTask] = [:]   // session id → task
    private var idMap: [Int: String] = [:]                          // session id → taskId

    private static let backgroundIdentifier = "com.clipharbor.app.download.v1"

    override private init() {
        do {
            history = try HistoryStore.default()
        } catch {
            // 数据库初始化失败属致命配置问题；Phase 0 用内存占位以免崩溃，并保留错误供 UI 显示
            let tmp = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("clipharbor-fallback.db")
            history = (try? HistoryStore(path: tmp.path))!
        }
        super.init()
        let config = URLSessionConfiguration.background(withIdentifier: Self.backgroundIdentifier)
        config.isDiscretionary = false
        config.sessionSendsLaunchEvents = true
        config.waitsForConnectivity = false
        config.timeoutIntervalForResource = 7 * 24 * 3600
        session = URLSession(configuration: config, delegate: self, delegateQueue: nil)
        reattachRunningTasks()
    }

    // MARK: - 对外操作

    /// 添加任务：真实解析 → 去重 → 空间预检 → 入库（排队态）→ 有空闲槽位即启动。
    /// sourceURL 保存原始链接；downloadURL 保存解析出的时效下载链接（直链任务两者相同）。
    @discardableResult
    func addTask(urlString: String) async throws -> DownloadTask {
        guard let url = URL(string: urlString.trimmingCharacters(in: .whitespacesAndNewlines)),
              let scheme = url.scheme, ["http", "https"].contains(scheme) else {
            throw SourceAdapterError.unsupported("请输入合法的 http/https 链接")
        }
        if let existing = try history.findActive(url: url.absoluteString) {
            return existing  // 防重复加入
        }
        let adapter = SourceAdapters.adapter(for: url)
        let info = try await adapter.resolve(url)
        guard let format = info.formats.first else {
            throw SourceAdapterError.unsupported("未能解析出可下载的格式")
        }
        let expected = format.filesizeBytes ?? 0
        guard FileStore.hasSpaceFor(expectedTotal: expected) else {
            throw SourceAdapterError.unsupported("iPhone 可用存储不足（需要约 \(formatBytes(expected))）")
        }
        let baseName: String
        if let title = info.title, !title.isEmpty {
            baseName = title
        } else if !url.lastPathComponent.isEmpty {
            baseName = url.lastPathComponent
        } else {
            baseName = "download"
        }
        var task = DownloadTask(
            id: UUID().uuidString.prefix(16).description,
            sourceURL: info.sourceUrl,
            sourceID: info.sourceId,
            title: info.title ?? "",
            filename: FileStore.sanitizeFilename(baseName),
            destDir: FileStore.mediaDirectory.path,
            bytesTotal: expected
        )
        task.formatID = format.formatId
        task.thumbnailURL = info.thumbnailUrl
        task.downloadURL = info.downloadUrl ?? info.sourceUrl
        try history.upsert(task)
        publishSnapshot(task.id)
        pumpQueue()
        return task
    }

    /// 暂停：本进程存活期间挂起传输（配合 UI 立即生效），并让排队任务补位。
    func pause(_ taskId: String) {
        lock.lock()
        let task = sessionTasks.first { idMap[$0.key] == taskId }?.value
        lock.unlock()
        task?.suspend()
        try? history.updateFields(taskId, [
            "status": .text(TaskStatus.paused.rawValue),
            "phase": .text(TaskPhase.idle.rawValue),
        ])
        meters[taskId]?.pauseSegment()
        publishSnapshot(taskId)
        pumpQueue()
    }

    /// 继续：恢复挂起的后台任务（不检查并发槽位：用户显式操作优先）。
    func resume(_ taskId: String) {
        lock.lock()
        let task = sessionTasks.first { idMap[$0.key] == taskId }?.value
        lock.unlock()
        try? history.updateFields(taskId, [
            "status": .text(TaskStatus.downloading.rawValue),
            "phase": .text(TaskPhase.network.rawValue),
            "error": .text(""),
        ])
        if let task {
            task.resume()
        } else if var stored = try? history.task(id: taskId), stored.status == .paused {
            // 进程重启后没有可挂起的句柄：用存储的下载链接重建后台任务
            startSessionTask(for: &stored)
        }
        publishSnapshot(taskId)
    }

    /// 取消：仅清理本任务自己的临时分片；不触碰媒体库中任何文件。
    func cancel(_ taskId: String) {
        lock.lock()
        let task = sessionTasks.first { idMap[$0.key] == taskId }?.value
        lock.unlock()
        task?.cancel()
        try? history.updateFields(taskId, [
            "status": .text(TaskStatus.cancelled.rawValue),
            "phase": .text(TaskPhase.idle.rawValue),
            "error": .text("已取消"),
            "bytes_downloaded": .int(0),
        ])
        removeMeter(taskId)
        publishSnapshot(taskId)
        pumpQueue()
    }

    /// 重试：仅对已结束任务生效。
    /// cobalt 任务会先用原始链接**重新解析**（隧道链接有时效，旧链接可能已过期），
    /// 直链任务直接重建下载。重试后进入排队态，由并发槽位调度启动。
    @discardableResult
    func retry(_ taskId: String) -> Bool {
        guard var task = try? history.task(id: taskId), task.status.isTerminal else { return false }
        task.status = .queued
        task.phase = .idle
        task.error = ""
        task.bytesDownloaded = 0
        task.completedAt = nil
        task.retries += 1
        try? history.upsert(task)
        publishSnapshot(taskId)
        if task.sourceID == "cobalt", let url = URL(string: task.sourceURL) {
            Task { @MainActor in
                await self.reResolveTunnel(taskId: taskId, originalURL: url)
                self.pumpQueue()
            }
        } else {
            pumpQueue()
        }
        return true
    }

    /// 用原始链接重新解析 cobalt 隧道（重试路径）。
    private func reResolveTunnel(taskId: String, originalURL: URL) async {
        let adapter = SourceAdapters.adapter(for: originalURL)
        do {
            let info: MediaInfo
            if let cobalt = adapter as? CobaltAdapter {
                info = try await cobalt.resolve(originalURL, quality: DownloadQuality.current)
            } else {
                info = try await adapter.resolve(originalURL)
            }
            guard var updated = try? history.task(id: taskId) else { return }
            updated.downloadURL = info.downloadUrl ?? info.sourceUrl
            if let title = info.title, !title.isEmpty { updated.title = title }
            updated.error = ""
            try? history.upsert(updated)
        } catch {
            try? history.updateFields(taskId, [
                "status": .text(TaskStatus.failed.rawValue),
                "phase": .text(TaskPhase.idle.rawValue),
                "error": .text("重新解析失败：\(error.localizedDescription)"),
            ])
            publishSnapshot(taskId)
        }
    }

    func task(id: String) -> DownloadTask? { try? history.task(id: id) }
    func activeTasks() -> [DownloadTask] { (try? history.activeTasks()) ?? [] }
    func historyRecords(query: String = "") -> [DownloadTask] { (try? history.history(query: query)) ?? [] }
    func searchHistory(_ query: String) -> [DownloadTask] { historyRecords(query: query) }

    /// 历史备份（BackupManager 亦使用这两个入口）。
    func exportHistoryJSON() throws -> Data { try history.exportJSON() }
    @discardableResult
    func importHistoryJSON(_ data: Data) throws -> (imported: Int, skipped: Int) {
        try history.importJSON(data)
    }

    /// 文件是否仍存在于磁盘（历史列表展示用）。
    func fileExists(_ task: DownloadTask) -> Bool {
        !task.filePath.isEmpty && FileManager.default.fileExists(atPath: task.filePath)
    }

    // MARK: - 内部

    /// 并发调度：把排队任务按加入顺序填入空闲槽位。
    private func pumpQueue() {
        pumpLock.lock(); defer { pumpLock.unlock() }
        guard let all = try? history.activeTasks() else { return }
        var running = all.filter { $0.status == .downloading }
        guard running.count < Self.maxConcurrentDownloads else { return }
        let queued = all.filter { $0.status == .queued }
        for var task in queued {
            guard running.count < Self.maxConcurrentDownloads else { break }
            startSessionTask(for: &task)
            running.append(task)
        }
    }

    private func startSessionTask(for task: inout DownloadTask) throws {
        let link = task.downloadURL.isEmpty ? task.sourceURL : task.downloadURL
        guard let url = URL(string: link) else { return }
        let downloadTask = session.downloadTask(with: url)
        downloadTask.countOfBytesClientExpectsToReceive = max(task.bytesTotal, 0)
        lock.lock()
        sessionTasks[downloadTask.taskIdentifier] = downloadTask
        idMap[downloadTask.taskIdentifier] = task.id
        lock.unlock()
        task.sessionTaskID = downloadTask.taskIdentifier
        try history.updateFields(task.id, [
            "session_task_id": .int(Int64(downloadTask.taskIdentifier)),
            "status": .text(TaskStatus.downloading.rawValue),
            "phase": .text(TaskPhase.network.rawValue),
        ])
        downloadTask.resume()
        publishSnapshot(task.id)
    }

    /// App 被系统唤回时重新关联仍在进行的后台任务，然后恢复中断记录并调度队列。
    private func reattachRunningTasks() {
        session.getAllTasks { [weak self] tasks in
            guard let self else { return }
            var reattached = Set<Int64>()
            self.lock.lock()
            for case let t as URLSessionDownloadTask in tasks {
                self.sessionTasks[t.taskIdentifier] = t
                reattached.insert(Int64(t.taskIdentifier))
            }
            self.lock.unlock()
            // 与数据库中记录的 session_task_id 关联
            if let all = try? self.history.activeTasks() {
                self.lock.lock()
                for task in all where task.sessionTaskID >= 0 {
                    self.idMap[task.sessionTaskID] = task.id
                }
                self.lock.unlock()
            }
            // 崩溃/退出恢复：不在本轮 reattach 名单里的 downloading 记录转为暂停态
            _ = try? self.history.recoverInterrupted(exceptSessionIds: reattached)
            self.pumpQueue()
        }
    }

    private func meter(for taskId: String) -> SpeedMeter {
        lock.lock(); defer { lock.unlock() }
        if let m = meters[taskId] { return m }
        let m = SpeedMeter()
        meters[taskId] = m
        return m
    }

    private func removeMeter(_ taskId: String) {
        lock.lock(); defer { lock.unlock() }
        meters[taskId]?.pauseSegment()
        meters.removeValue(forKey: taskId)
    }

    private var lastDBWrite: [String: Date] = [:]
    private func throttledPersist(_ taskId: String, bytes: Int64) {
        let now = Date()
        lock.lock()
        let last = lastDBWrite[taskId] ?? .distantPast
        lock.unlock()
        guard now.timeIntervalSince(last) > 0.5 else { return }
        lock.lock(); lastDBWrite[taskId] = now; lock.unlock()
        try? history.updateFields(taskId, [
            "bytes_downloaded": .int(bytes),
            "avg_speed_bps": .real(meter(for: taskId).averageBps()),
            "peak_speed_bps": .real(meter(for: taskId).peakBps()),
        ])
    }

    private func publishSnapshot(_ taskId: String) {
        guard var task = try? history.task(id: taskId) else { return }
        let m = meter(for: taskId)
        let speed = m.currentBps()
        task.bytesDownloaded = max(task.bytesDownloaded, m.totalBytesCount())
        if speed > 0, task.bytesTotal > task.bytesDownloaded {
            let eta = Double(task.bytesTotal - task.bytesDownloaded) / speed
            let snapshot = DownloadProgress(
                taskId: taskId, status: task.status, phase: task.phase,
                bytesDownloaded: task.bytesDownloaded, bytesTotal: task.bytesTotal,
                speedBps: speed, avgSpeedBps: m.averageBps(), peakSpeedBps: m.peakBps(),
                etaSeconds: eta, error: task.error, updatedAt: Date()
            )
            DispatchQueue.main.async { self.liveProgress[taskId] = snapshot }
        } else {
            let snapshot = DownloadProgress(
                taskId: taskId, status: task.status, phase: task.phase,
                bytesDownloaded: task.bytesDownloaded, bytesTotal: task.bytesTotal,
                speedBps: speed, avgSpeedBps: task.avgSpeedBps, peakSpeedBps: task.peakSpeedBps,
                etaSeconds: nil, error: task.error, updatedAt: Date()
            )
            DispatchQueue.main.async { self.liveProgress[taskId] = snapshot }
        }
    }

    private func sha256(of path: String) -> String {
        guard let handle = FileHandle(forReadingAtPath: path) else { return "" }
        defer { try? handle.close() }
        var hasher = SHA256()
        while true {
            let chunk = handle.readData(ofLength: 1024 * 1024)
            if chunk.isEmpty { break }
            hasher.update(data: chunk)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }
}

// MARK: - URLSessionDownloadDelegate

extension BackgroundDownloadEngine: URLSessionDownloadDelegate {
    func urlSession(_ session: URLSession,
                    downloadTask: URLSessionDownloadTask,
                    didWriteData bytesWritten: Int64,
                    totalBytesWritten: Int64,
                    totalBytesExpectedToWrite: Int64) {
        guard let taskId = idMap[downloadTask.taskIdentifier] else { return }
        meter(for: taskId).onBytes(bytesWritten)   // 真实字节增量
        try? history.updateFields(taskId, ["bytes_downloaded": .int(totalBytesWritten),
                                           "bytes_total": .int(max(totalBytesExpectedToWrite, 0))])
        throttledPersist(taskId, bytes: totalBytesWritten)
        publishSnapshot(taskId)
    }

    func urlSession(_ session: URLSession,
                    downloadTask: URLSessionDownloadTask,
                    didFinishDownloadingTo location: URL) {
        guard let taskId = idMap[downloadTask.taskIdentifier],
              let task = try? history.task(id: taskId) else { return }
        // cobalt 隧道过期防护：隧道失效时服务器可能回 HTML/短文件而非媒体流
        let mime = (downloadTask.response?.mimeType ?? "").lowercased()
        if task.sourceID == "cobalt", mime.contains("text/html") {
            try? history.updateFields(taskId, [
                "status": .text(TaskStatus.failed.rawValue),
                "phase": .text(TaskPhase.idle.rawValue),
                "error": .text("下载链接已过期（隧道约 10 分钟有效），请长按重试，会自动重新解析"),
            ])
            removeMeter(taskId)
            publishSnapshot(taskId)
            pumpQueue()
            return
        }
        let filename = FileStore.sanitizeFilename(
            task.filename.isEmpty ? downloadTask.response?.suggestedFilename ?? "download" : task.filename
        )
        let destination = FileStore.uniqueURL(forDirectory: FileStore.mediaDirectory, filename: filename)
        do {
            // location 是临时文件，委托方法返回后即被清除，必须同步搬移
            try FileManager.default.moveItem(at: location, to: destination)
        } catch {
            try? history.updateFields(taskId, [
                "status": .text(TaskStatus.failed.rawValue),
                "phase": .text(TaskPhase.idle.rawValue),
                "error": .text("落盘失败: \(error.localizedDescription)"),
            ])
            removeMeter(taskId)
            publishSnapshot(taskId)
            pumpQueue()
            return
        }
        let attrs = try? FileManager.default.attributesOfItem(atPath: destination.path)
        let size = (attrs?[.size] as? NSNumber)?.int64Value ?? 0
        let verified = task.bytesTotal == 0 || size == task.bytesTotal
        try? history.updateFields(taskId, [
            "status": .text(verified ? TaskStatus.completed.rawValue : TaskStatus.failed.rawValue),
            "phase": .text(TaskPhase.done.rawValue),
            "file_path": .text(destination.path),
            "filename": .text(destination.lastPathComponent),
            "bytes_downloaded": .int(size),
            "bytes_total": .int(max(task.bytesTotal, size)),
            "completed_at": .real(Date().timeIntervalSince1970),
            "sha256": .text(sha256(of: destination.path)),
            "verified": .int(verified ? 1 : 0),
            "error": .text(verified ? "" : "文件大小校验失败"),
        ])
        removeMeter(taskId)
        publishSnapshot(taskId)
        pumpQueue()
    }

    func urlSession(_ session: URLSession,
                    task: URLSessionTask,
                    didCompleteWithError error: Error?) {
        guard let downloadTask = task as? URLSessionDownloadTask,
              let taskId = idMap[downloadTask.taskIdentifier] else { return }
        guard let error else { return }  // 无 error 的完成走 didFinishDownloadingTo
        if let resumeData = (error as NSError).userInfo[NSURLSessionDownloadTaskResumeData] as? Data {
            // 可续传失败：保存 resumeData，转为暂停态
            let url = FileStore.partialDirectory.appendingPathComponent("\(taskId).resumeData")
            try? resumeData.write(to: url)
            try? history.updateFields(taskId, [
                "status": .text(TaskStatus.paused.rawValue),
                "phase": .text(TaskPhase.idle.rawValue),
                "error": .text("网络中断，已保存续传数据"),
            ])
        } else {
            var message = error.localizedDescription
            if let t = try? history.task(id: taskId), t.sourceID == "cobalt" {
                message += "（cobalt 隧道链接有时效，长按重试可自动重新解析）"
            }
            try? history.updateFields(taskId, [
                "status": .text(TaskStatus.failed.rawValue),
                "phase": .text(TaskPhase.idle.rawValue),
                "error": .text(message),
            ])
        }
        meters[taskId]?.pauseSegment()
        publishSnapshot(taskId)
        pumpQueue()
    }

    /// 后台事件全部送达后通知系统（否则 App 可能被提前终止）。
    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        DispatchQueue.main.async { [weak self] in
            self?.pendingBackgroundCompletionHandler?()
            self?.pendingBackgroundCompletionHandler = nil
        }
    }
}
