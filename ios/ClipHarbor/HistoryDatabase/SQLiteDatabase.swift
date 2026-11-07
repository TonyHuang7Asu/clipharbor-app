import Foundation
import SQLite3

/// SQLite3 C API 薄封装。安全约束：所有含外部输入的语句必须使用参数绑定，
/// 本封装只暴露绑定式接口，不支持字符串拼接 SQL。
enum SQLValue {
    case text(String)
    case int(Int64)
    case real(Double)
    case null
}

final class SQLiteDatabase {
    private var handle: OpaquePointer?
    private let queue = DispatchQueue(label: "com.clipharbor.sqlite")
    private let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

    init(path: String) throws {
        let dir = (path as NSString).deletingLastPathComponent
        try FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        guard sqlite3_open_v2(path, &handle, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nil) == SQLITE_OK else {
            throw NSError(domain: "ClipHarbor.SQLite", code: Int(sqlite3_errcode(handle)),
                          userInfo: [NSLocalizedDescriptionKey: String(cString: sqlite3_errmsg(handle))])
        }
        try execute("PRAGMA journal_mode=WAL")
        try execute("PRAGMA synchronous=NORMAL")
    }

    deinit { sqlite3_close_v2(handle) }

    func execute(_ sql: String) throws {
        try queue.sync {
            var err: UnsafeMutablePointer<CChar>?
            guard sqlite3_exec(handle, sql, nil, nil, &err) == SQLITE_OK else {
                let message = err.map { String(cString: $0) } ?? "unknown error"
                sqlite3_free(err)
                throw NSError(domain: "ClipHarbor.SQLite", code: -1,
                              userInfo: [NSLocalizedDescriptionKey: message])
            }
        }
    }

    /// 执行带参数绑定的语句，返回受影响行数。
    @discardableResult
    func run(_ sql: String, _ params: [SQLValue] = []) throws -> Int {
        try queue.sync {
            let stmt = try prepare(sql, params)
            defer { sqlite3_finalize(stmt) }
            guard sqlite3_step(stmt) == SQLITE_DONE else {
                throw sqliteError()
            }
            return Int(sqlite3_changes(handle))
        }
    }

    /// 查询带参数绑定的语句，返回每行的列字典（列名 → 值）。
    func query(_ sql: String, _ params: [SQLValue] = []) throws -> [[String: SQLValue]] {
        try queue.sync {
            let stmt = try prepare(sql, params)
            defer { sqlite3_finalize(stmt) }
            var rows: [[String: SQLValue]] = []
            let count = sqlite3_column_count(stmt)
            while sqlite3_step(stmt) == SQLITE_ROW {
                var row: [String: SQLValue] = [:]
                for i in 0..<count {
                    let name = String(cString: sqlite3_column_name(stmt, i))
                    switch sqlite3_column_type(stmt, i) {
                    case SQLITE_TEXT: row[name] = .text(String(cString: sqlite3_column_text(stmt, i)))
                    case SQLITE_INTEGER: row[name] = .int(sqlite3_column_int64(stmt, i))
                    case SQLITE_FLOAT: row[name] = .real(sqlite3_column_double(stmt, i))
                    default: row[name] = .null
                    }
                }
                rows.append(row)
            }
            return rows
        }
    }

    private func prepare(_ sql: String, _ params: [SQLValue]) throws -> OpaquePointer {
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(handle, sql, -1, &stmt, nil) == SQLITE_OK, let stmt else {
            throw sqliteError()
        }
        for (index, param) in params.enumerated() {
            let i = Int32(index + 1)
            switch param {
            case .text(let s): sqlite3_bind_text(stmt, i, s, -1, transient)
            case .int(let n): sqlite3_bind_int64(stmt, i, n)
            case .real(let d): sqlite3_bind_double(stmt, i, d)
            case .null: sqlite3_bind_null(stmt, i)
            }
        }
        return stmt
    }

    private func sqliteError() -> NSError {
        NSError(domain: "ClipHarbor.SQLite", code: Int(sqlite3_errcode(handle)),
                userInfo: [NSLocalizedDescriptionKey: String(cString: sqlite3_errmsg(handle))])
    }
}
