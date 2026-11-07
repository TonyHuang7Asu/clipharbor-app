import Foundation

/// 备份管理（Phase 0 提供历史 JSON 导入导出；完整媒体备份随 Phase 5 实现）。
///
/// 设计原则：
/// - 备份位置由用户主动选择（文件 App / iCloud Drive），绝不静默上传任何第三方。
/// - 数据库升级只增列、绝不清空；重新安装前应先导出备份（见 Docs/BUILD-AND-SIGNING.md）。
enum BackupManager {
    /// 当前历史导出为协议 JSON。
    static func exportHistoryJSON() throws -> Data {
        try BackgroundDownloadEngine.shared.exportHistoryJSON()
    }

    /// 从用户选择的 JSON 恢复历史（只导入已结束记录，不覆盖现有数据）。
    static func importHistoryJSON(_ data: Data) throws -> (imported: Int, skipped: Int) {
        try BackgroundDownloadEngine.shared.importHistoryJSON(data)
    }
}
