import Foundation

/// 真实速度统计：current = 滑动窗口速率；average = 活跃传输时间平均；peak = 可持续峰值。
/// 全部数字来自 URLSession didWriteData 的真实字节增量，无任何模拟。
/// 线程安全：委托回调可能来自任意队列。
final class SpeedMeter {
    private let lock = NSLock()
    private var samples: [(at: Double, bytes: Int64)] = []
    private var totalBytes: Int64 = 0
    private var peakBps: Double = 0
    private var segmentStart: Double?
    private var activeSeconds: Double = 0

    private let windowSeconds: Double
    /// 峰值窗口跨度下限，避免单个写入事件造成瞬时尖刺
    private let minPeakSpan: Double = 0.25

    init(windowSeconds: Double = 5.0) {
        self.windowSeconds = windowSeconds
    }

    func onBytes(_ count: Int64) {
        guard count > 0 else { return }
        lock.lock(); defer { lock.unlock() }
        let now = MonotonicTime.now()
        if segmentStart == nil { segmentStart = now }
        totalBytes += count
        samples.append((now, count))
        let speed = windowSpeed(now)
        if speed > peakBps { peakBps = speed }
    }

    /// 结束当前活跃传输段（暂停 / 等待重试 / 完成时调用）。
    func pauseSegment() {
        lock.lock(); defer { lock.unlock() }
        if let start = segmentStart {
            activeSeconds += max(0, MonotonicTime.now() - start)
            segmentStart = nil
        }
    }

    func currentBps() -> Double {
        lock.lock(); defer { lock.unlock() }
        guard segmentStart != nil else { return 0 }
        return windowSpeed(MonotonicTime.now())
    }

    func averageBps() -> Double {
        lock.lock(); defer { lock.unlock() }
        var active = activeSeconds
        if let start = segmentStart { active += max(0, MonotonicTime.now() - start) }
        guard active > 0 else { return 0 }
        return Double(totalBytes) / active
    }

    /// 峰值速率（方法名带后缀，避免与同名存储属性冲突）。
    func peakSpeedBps() -> Double {
        lock.lock(); defer { lock.unlock() }
        return peakBps
    }

    /// 累计真实字节数（引擎快照与测试共用）。
    func totalBytesCount() -> Int64 {
        lock.lock(); defer { lock.unlock() }
        return totalBytes
    }

    private func windowSpeed(_ now: Double) -> Double {
        let cutoff = now - windowSeconds
        while let first = samples.first, first.at < cutoff { samples.removeFirst() }
        guard !samples.isEmpty else { return 0 }
        let span = max(now - samples[0].at, minPeakSpan)
        let bytes = samples.reduce(Int64(0)) { $0 + $1.bytes }
        return Double(bytes) / span
    }
}

/// monotonic 时间（系统运行时长），避免系统时钟调整影响速率计算。
private enum MonotonicTime {
    static func now() -> Double { ProcessInfo.processInfo.systemUptime }
}
