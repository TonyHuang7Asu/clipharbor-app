import XCTest
@testable import ClipHarbor

/// HistoryStore 持久化行为测试（临时数据库，不依赖网络）。
final class HistoryStoreTests: XCTestCase {
    private var store: HistoryStore!
    private var dbPath: String!

    override func setUpWithError() throws {
        dbPath = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("ch-test-\(UUID().uuidString).db").path
        store = try HistoryStore(path: dbPath)
    }

    override func tearDownWithError() throws {
        store = nil
        try? FileManager.default.removeItem(atPath: dbPath)
    }

    private func makeTask(_ id: String, status: TaskStatus = .completed) -> DownloadTask {
        var t = DownloadTask(id: id, sourceURL: "https://example.com/\(id).mp4")
        t.status = status
        t.filename = "\(id).mp4"
        t.bytesTotal = 1000
        t.bytesDownloaded = status == .completed ? 1000 : 0
        t.completedAt = status == .completed ? Date() : nil
        return t
    }

    func testUpsertAndRoundTrip() throws {
        try store.upsert(makeTask("a1"))
        let loaded = try store.task(id: "a1")
        XCTAssertEqual(loaded?.id, "a1")
        XCTAssertEqual(loaded?.status, .completed)
        XCTAssertEqual(loaded?.bytesTotal, 1000)
    }

    func testHistoryExcludesActiveTasks() throws {
        try store.upsert(makeTask("done1"))
        try store.upsert(makeTask("active1", status: .downloading))
        XCTAssertEqual(try store.history().map(\.id), ["done1"])
    }

    func testSearch() throws {
        var t = makeTask("s1")
        t.title = " philosophical lecture "
        try store.upsert(t)
        XCTAssertEqual(try store.history(query: "philosophical").count, 1)
        XCTAssertEqual(try store.history(query: "不存在").count, 0)
    }

    func testURLDedupe() throws {
        try store.upsert(makeTask("d1", status: .downloading))
        XCTAssertNotNil(try store.findActive(url: "https://example.com/d1.mp4"))
        XCTAssertNil(try store.findActive(url: "https://example.com/other.mp4"))
    }

    func testCrashRecoveryMarksPaused() throws {
        try store.upsert(makeTask("r1", status: .downloading))
        let n = try store.recoverInterrupted()
        XCTAssertEqual(n, 1)
        XCTAssertEqual(try store.task(id: "r1")?.status, .paused)
    }

    func testExportImportRoundTrip() throws {
        try store.upsert(makeTask("e1"))
        let data = try store.exportJSON()
        let store2 = try HistoryStore(path: dbPath + ".2")
        let (imported, skipped) = try store2.importJSON(data)
        XCTAssertEqual(imported, 1)
        XCTAssertEqual(skipped, 0)
        // 重复导入被跳过，不覆盖
        let (again, skipAgain) = try store2.importJSON(data)
        XCTAssertEqual(again, 0)
        XCTAssertEqual(skipAgain, 1)
    }

    func testImportIgnoresActiveTasks() throws {
        let json = #"{"records":[{"taskId":"x1","sourceUrl":"https://e.com/1","status":"downloading","bytesTotal":5}]}"#
        let (imported, _) = try store.importJSON(Data(json.utf8))
        XCTAssertEqual(imported, 0)
        XCTAssertNil(try store.task(id: "x1"))
    }
}
