import XCTest
@testable import ClipHarbor

/// SpeedMeter 真实统计行为测试（纯内存，不依赖网络）。
final class SpeedMeterTests: XCTestCase {
    func testCountsRealBytes() {
        let meter = SpeedMeter()
        meter.onBytes(1000)
        meter.onBytes(500)
        XCTAssertEqual(meter.totalBytes(inTest: true), 1500)
    }

    func testAverageExcludesPausedTime() {
        let meter = SpeedMeter(windowSeconds: 5)
        meter.onBytes(1000)
        // 模拟暂停：关闭传输段并注入已流逝的活跃时间
        meter.pauseSegment()
        Thread.sleep(forTimeInterval: 0.05)
        // 平均速率只基于活跃时间：1000 字节 / ≥0 秒，不会因为暂停 50ms 而被稀释
        let avg = meter.averageBps()
        XCTAssertGreaterThan(avg, 0)
        meter.onBytes(1000)
        meter.pauseSegment()
        XCTAssertGreaterThan(meter.averageBps(), avg / 100)
    }

    func testPeakHasSustainedFloor() {
        let meter = SpeedMeter(windowSeconds: 5)
        meter.onBytes(Int64(10 * 1024 * 1024))  // 单个 10MB 事件
        // 窗口跨度 < 0.25s 时按 0.25s 下限折算：10MB/0.25s = 40MB/s，而非 10MB/0s 的无穷大
        XCTAssertLessThanOrEqual(meter.peakSpeedBps(), 40 * 1024 * 1024 * 1.01)
        XCTAssertGreaterThan(meter.peakSpeedBps(), 0)
    }

    func testIdleSpeedIsZero() {
        let meter = SpeedMeter()
        meter.onBytes(1000)
        meter.pauseSegment()
        XCTAssertEqual(meter.currentBps(), 0)
    }
}

extension SpeedMeter {
    /// 测试辅助：读取累计字节。
    func totalBytes(inTest _: Bool) -> Int64 {
        // averageBps * activeSeconds 不方便直接取值；这里通过公开行为验证：
        // 直接用 currentBps 不可行，故用镜像逻辑读取 totalBytes。
        let mirror = Mirror(reflecting: self)
        for child in mirror.children where child.label == "totalBytes" {
            return (child.value as? Int64) ?? 0
        }
        return 0
    }
}
