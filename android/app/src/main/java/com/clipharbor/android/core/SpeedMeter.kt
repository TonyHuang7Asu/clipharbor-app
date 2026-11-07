package com.clipharbor.android.core

import java.util.concurrent.ConcurrentLinkedDeque

/** 真实速度统计（与 Windows/iOS 端同构）：滑动窗口当前速度 + 活跃时间平均 + 可持续峰值。 */
class SpeedMeter(private val windowSeconds: Double = 5.0) {
    private val samples = ConcurrentLinkedDeque<Pair<Double, Long>>()
    private val lock = Any()
    private var totalBytes = 0L
    private var peak = 0.0
    private var segmentStart: Double? = null
    private var activeSeconds = 0.0
    private var lastCumulative = 0L

    private fun now(): Double = System.nanoTime() / 1_000_000_000.0

    /** 以绝对累计字节喂入（轮询落盘大小场景），内部换算增量。 */
    fun onBytesDelta(cumulative: Long) {
        val delta = synchronized(lock) { cumulative - lastCumulative }
        if (delta > 0) {
            synchronized(lock) { lastCumulative = cumulative }
            onBytes(delta)
        }
    }

    fun onBytes(count: Long) {
        if (count <= 0) return
        val t = now()
        synchronized(lock) {
            if (segmentStart == null) segmentStart = t
            totalBytes += count
            samples.addLast(t to count)
            val speed = windowSpeedLocked(t)
            if (speed > peak) peak = speed
        }
    }

    /** 结束当前活跃传输段（暂停/完成时调用）。 */
    fun pauseSegment() {
        synchronized(lock) {
            segmentStart?.let { activeSeconds += maxOf(0.0, now() - it) }
            segmentStart = null
        }
    }

    fun currentBps(): Double = synchronized(lock) {
        if (segmentStart == null) 0.0 else windowSpeedLocked(now())
    }

    fun averageBps(): Double = synchronized(lock) {
        var active = activeSeconds
        segmentStart?.let { active += maxOf(0.0, now() - it) }
        if (active <= 0) 0.0 else totalBytes / active
    }

    fun peakBps(): Double = synchronized(lock) { peak }

    private fun windowSpeedLocked(now: Double): Double {
        val cutoff = now - windowSeconds
        while (samples.peekFirst()?.first?.let { it < cutoff } == true) samples.pollFirst()
        val list = samples.toList()
        if (list.isEmpty()) return 0.0
        val span = maxOf(now - list.first().first, 0.25) // 峰值防瞬时尖刺
        return list.sumOf { it.second } / span
    }
}
