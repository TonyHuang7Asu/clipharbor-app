import Foundation

/// 任务/历史持久化：SQLite，全部参数绑定。
/// 表结构与 Windows 端同构；升级只做 ALTER 增列，绝不删除历史数据。
final class HistoryStore {
    static let schemaVersion = 1

    private let db: SQLiteDatabase
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    init(path: String) throws {
        db = try SQLiteDatabase(path: path)
        encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        try migrate()
    }

    static func `default`() throws -> HistoryStore {
        let dir = FileStore.applicationSupport.appendingPathComponent("Database", isDirectory: true)
        return try HistoryStore(path: dir.appendingPathComponent("history.db").path)
    }

    private func migrate() throws {
        try db.execute("""
        CREATE TABLE IF NOT EXISTS downloads (
            id TEXT PRIMARY KEY NOT NULL,
            url TEXT NOT NULL,
            source_id TEXT NOT NULL DEFAULT 'direct',
            title TEXT NOT NULL DEFAULT '',
            filename TEXT NOT NULL DEFAULT '',
            dest_dir TEXT NOT NULL DEFAULT '',
            file_path TEXT NOT NULL DEFAULT '',
            format_id TEXT NOT NULL DEFAULT '',
            thumbnail_url TEXT NOT NULL DEFAULT '',
            download_url TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'queued',
            phase TEXT NOT NULL DEFAULT 'idle',
            bytes_downloaded INTEGER NOT NULL DEFAULT 0,
            bytes_total INTEGER NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT '',
            retries INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL DEFAULT 0,
            completed_at REAL,
            avg_speed_bps REAL NOT NULL DEFAULT 0,
            peak_speed_bps REAL NOT NULL DEFAULT 0,
            sha256 TEXT NOT NULL DEFAULT '',
            verified INTEGER NOT NULL DEFAULT 0,
            session_task_id INTEGER NOT NULL DEFAULT -1
        );
        """)
        try db.execute("CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads(status)")
        try db.execute("CREATE INDEX IF NOT EXISTS idx_downloads_url ON downloads(url)")
        // 迁移策略：schema_version 递增时在此追加 ALTER TABLE ... ADD COLUMN，
        // 绝不 DROP/清空 downloads 表（用户历史不可丢失）。
        // 增列升级示例（download_url 列加入前的旧库）：
        try? db.execute("ALTER TABLE downloads ADD COLUMN download_url TEXT NOT NULL DEFAULT ''")
    }

    // MARK: - CRUD

    func upsert(_ task: DownloadTask) throws {
        try db.run(
            "INSERT OR REPLACE INTO downloads VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [.text(task.id), .text(task.sourceURL), .text(task.sourceID), .text(task.title),
             .text(task.filename), .text(task.destDir), .text(task.filePath),
             .text(task.formatID), .text(task.thumbnailURL ?? ""), .text(task.downloadURL),
             .text(task.status.rawValue), .text(task.phase.rawValue),
             .int(task.bytesDownloaded), .int(task.bytesTotal), .text(task.error),
             .int(Int64(task.retries)), .real(task.createdAt.timeIntervalSince1970),
             .real(task.updatedAt?.timeIntervalSince1970 ?? 0),
             task.completedAt.map { .real($0.timeIntervalSince1970) } ?? .null,
             .real(task.avgSpeedBps), .real(task.peakSpeedBps),
             .text(task.sha256), .int(task.verified ? 1 : 0), .int(Int64(task.sessionTaskID))]
        )
    }

    func updateFields(_ id: String, _ fields: [String: SQLValue]) throws {
        // 列名来自调用方代码中的白名单常量（引擎内部固定集合），值全部参数绑定
        let allowed = ["status", "phase", "bytes_downloaded", "bytes_total", "error",
                       "file_path", "filename", "updated_at", "completed_at",
                       "avg_speed_bps", "peak_speed_bps", "sha256", "verified",
                       "session_task_id", "retries", "download_url"]
        let names = Array(fields.keys)
        guard names.allSatisfy({ allowed.contains($0) }), !names.isEmpty else {
            throw NSError(domain: "ClipHarbor.History", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "非法更新列"])
        }
        let assignments = names.map { "\($0) = ?" }.joined(separator: ", ")
        var params: [SQLValue] = names.map { fields[$0]! }
        params.append(.text(id))
        try db.run("UPDATE downloads SET \(assignments) WHERE id = ?", params)
    }

    func task(id: String) throws -> DownloadTask? {
        try rows("SELECT * FROM downloads WHERE id = ?", [.text(id)]).first
    }

    func activeTasks() throws -> [DownloadTask] {
        try rows("""
        SELECT * FROM downloads WHERE status IN
        ('queued','downloading','paused','retry_wait','merging','zipping')
        ORDER BY created_at
        """)
    }

    /// 历史记录（仅已结束任务），支持标题/URL/文件名搜索。
    func history(query: String = "", limit: Int = 500) throws -> [DownloadTask] {
        if query.isEmpty {
            return try rows("""
            SELECT * FROM downloads WHERE status IN ('completed','failed','cancelled')
            ORDER BY completed_at DESC LIMIT ?
            """, [.int(Int64(limit))])
        }
        let like = "%\(query)%"
        return try rows("""
        SELECT * FROM downloads WHERE status IN ('completed','failed','cancelled')
        AND (title LIKE ? OR url LIKE ? OR filename LIKE ?)
        ORDER BY completed_at DESC LIMIT ?
        """, [.text(like), .text(like), .text(like), .int(Int64(limit))])
    }

    /// URL 去重：同一 URL 已有活动任务时返回它。
    func findActive(url: String) throws -> DownloadTask? {
        try rows("""
        SELECT * FROM downloads WHERE url = ? AND status IN
        ('queued','downloading','paused','retry_wait','merging','zipping') LIMIT 1
        """, [.text(url)]).first
    }

    /// 崩溃/退出恢复：中断任务转为可续传的暂停态。
    /// exceptSessionIds：本次启动已成功重新关联的后台会话任务仍在运行，不误标。
    func recoverInterrupted(exceptSessionIds: Set<Int64> = []) throws -> Int {
        if exceptSessionIds.isEmpty {
            return try db.run("""
            UPDATE downloads SET status = 'paused', phase = 'idle',
            error = '应用退出时中断，可续传'
            WHERE status IN ('downloading','retry_wait')
            """)
        }
        let placeholders = Array(repeating: "?", count: exceptSessionIds.count).joined(separator: ",")
        var params: [SQLValue] = Array(exceptSessionIds).map { .int($0) }
        params.append(.text("应用退出时中断，可续传"))
        return try db.run("""
        UPDATE downloads SET status = 'paused', phase = 'idle', error = ?
        WHERE status IN ('downloading','retry_wait')
        AND session_task_id NOT IN (\(placeholders))
        """)
    }

    // MARK: - 导入导出（BackupManager 使用）

    func exportJSON() throws -> Data {
        let records = try rows("""
        SELECT * FROM downloads WHERE status IN ('completed','failed','cancelled')
        ORDER BY completed_at DESC
        """)
        let wrapper: [String: Any] = [
            "protocol": "clipharbor",
            "apiVersion": "1.0",
            "exportedAt": ISO8601DateFormatter().string(from: Date()),
            "records": records.map { taskToDict($0) },
        ]
        return try JSONSerialization.data(withJSONObject: wrapper, options: [.prettyPrinted, .sortedKeys])
    }

    /// 只导入已结束的历史记录；已存在 id 跳过，绝不覆盖本地数据。
    func importJSON(_ data: Data) throws -> (imported: Int, skipped: Int) {
        guard let wrapper = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let records = (wrapper["records"] as? [[String: Any]]) ?? (wrapper as? [[String: Any]]) else {
            throw NSError(domain: "ClipHarbor.History", code: 2,
                          userInfo: [NSLocalizedDescriptionKey: "无法识别的历史 JSON"])
        }
        var imported = 0, skipped = 0
        for record in records {
            guard let id = record["taskId"] as? String,
                  let status = record["status"] as? String,
                  ["completed", "failed", "cancelled"].contains(status) else {
                skipped += 1; continue
            }
            if try task(id: id) != nil { skipped += 1; continue }
            let dict = taskFromDict(record, status: status)
            try upsert(dict)
            imported += 1
        }
        return (imported, skipped)
    }

    // MARK: - 行映射

    private func rows(_ sql: String, _ params: [SQLValue] = []) throws -> [DownloadTask] {
        try db.query(sql, params).map { row in
            func text(_ k: String) -> String {
                if case .text(let v)? = row[k] { return v }; return ""
            }
            func int(_ k: String) -> Int64 {
                if case .int(let v)? = row[k] { return v }; return 0
            }
            func real(_ k: String) -> Double {
                if case .real(let v)? = row[k] { return v }; return 0
            }
            func date(_ k: String) -> Date? {
                let t = real(k); return t > 0 ? Date(timeIntervalSince1970: t) : nil
            }
            return DownloadTask(
                id: text("id"),
                sourceURL: text("url"),
                sourceID: text("source_id"),
                title: text("title"),
                filename: text("filename"),
                destDir: text("dest_dir"),
                filePath: text("file_path"),
                formatID: text("format_id"),
                thumbnailURL: row["thumbnail_url"] == nil || text("thumbnail_url").isEmpty ? nil : text("thumbnail_url"),
                downloadURL: text("download_url"),
                status: TaskStatus(rawValue: text("status")) ?? .failed,
                phase: TaskPhase(rawValue: text("phase")) ?? .idle,
                bytesDownloaded: int("bytes_downloaded"),
                bytesTotal: int("bytes_total"),
                error: text("error"),
                retries: Int(int("retries")),
                createdAt: date("created_at") ?? Date(),
                updatedAt: date("updated_at"),
                completedAt: date("completed_at"),
                avgSpeedBps: real("avg_speed_bps"),
                peakSpeedBps: real("peak_speed_bps"),
                sha256: text("sha256"),
                verified: int("verified") == 1,
                sessionTaskID: Int(int("session_task_id"))
            )
        }
    }

    private func taskToDict(_ task: DownloadTask) -> [String: Any] {
        let iso = ISO8601DateFormatter()
        var dict: [String: Any] = [
            "taskId": task.id, "sourceUrl": task.sourceURL, "sourceId": task.sourceID,
            "title": task.title, "filename": task.filename, "destDir": task.destDir,
            "filePath": task.filePath, "formatId": task.formatID,
            "status": task.status.rawValue, "phase": task.phase.rawValue,
            "bytesDownloaded": task.bytesDownloaded, "bytesTotal": task.bytesTotal,
            "error": task.error, "retries": task.retries,
            "createdAt": iso.string(from: task.createdAt),
            "avgSpeedBps": task.avgSpeedBps, "peakSpeedBps": task.peakSpeedBps,
            "sha256": task.sha256, "verified": task.verified,
        ]
        if let u = task.updatedAt { dict["updatedAt"] = iso.string(from: u) }
        if let c = task.completedAt { dict["completedAt"] = iso.string(from: c) }
        if let t = task.thumbnailURL { dict["thumbnailUrl"] = t }
        if !task.downloadURL.isEmpty { dict["downloadUrl"] = task.downloadURL }
        return dict
    }

    private func taskFromDict(_ record: [String: Any], status: String) -> DownloadTask {
        let iso = ISO8601DateFormatter()
        func str(_ k: String) -> String { record[k] as? String ?? "" }
        func i64(_ k: String) -> Int64 { (record[k] as? NSNumber)?.int64Value ?? 0 }
        func dbl(_ k: String) -> Double { (record[k] as? NSNumber)?.doubleValue ?? 0 }
        return DownloadTask(
            id: str("taskId"),
            sourceURL: str("sourceUrl"),
            sourceID: str("sourceId").isEmpty ? "direct" : str("sourceId"),
            title: str("title"),
            filename: str("filename"),
            destDir: str("destDir"),
            filePath: str("filePath"),
            formatID: str("formatId"),
            thumbnailURL: record["thumbnailUrl"] as? String,
            downloadURL: str("downloadUrl"),
            status: TaskStatus(rawValue: status) ?? .failed,
            phase: status == "completed" ? .done : .idle,
            bytesDownloaded: i64("bytesDownloaded"),
            bytesTotal: i64("bytesTotal"),
            error: str("error"),
            retries: Int(i64("retries")),
            createdAt: (record["createdAt"] as? String).flatMap { iso.date(from: $0) } ?? Date(),
            updatedAt: (record["updatedAt"] as? String).flatMap { iso.date(from: $0) },
            completedAt: (record["completedAt"] as? String).flatMap { iso.date(from: $0) },
            avgSpeedBps: dbl("avgSpeedBps"),
            peakSpeedBps: dbl("peakSpeedBps"),
            sha256: str("sha256"),
            verified: (record["verified"] as? NSNumber)?.boolValue ?? false
        )
    }
}
