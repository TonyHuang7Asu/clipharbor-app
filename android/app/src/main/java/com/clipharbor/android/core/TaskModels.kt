package com.clipharbor.android.core

import kotlinx.serialization.Serializable

/** 与 ClipHarbor 协议 v1.0 一致的任务状态。 */
enum class TaskStatus(val wire: String) {
    QUEUED("queued"), DOWNLOADING("downloading"), PAUSED("paused"),
    RETRY_WAIT("retry_wait"), MERGING("merging"), COMPLETED("completed"),
    FAILED("failed"), CANCELLED("cancelled");

    val isTerminal: Boolean get() = this == COMPLETED || this == FAILED || this == CANCELLED

    companion object {
        fun from(wire: String) = entries.firstOrNull { it.wire == wire } ?: FAILED
    }
}

/** 下载任务（协议 Task 的本地表示；键名对齐协议 camelCase）。 */
@Serializable
data class DownloadTask(
    val taskId: String,
    val sourceUrl: String,
    val sourceId: String = "direct",       // direct | ytdlp
    val title: String = "",
    val filename: String = "",
    val filePath: String = "",
    val status: TaskStatus = TaskStatus.QUEUED,
    val bytesDownloaded: Long = 0,
    val bytesTotal: Long = 0,              // 0 = 未知
    val error: String = "",
    val createdAt: Long = System.currentTimeMillis(),
    val completedAt: Long? = null,
    val avgSpeedBps: Double = 0.0,
    val peakSpeedBps: Double = 0.0,
    val sha256: String = "",
    val verified: Boolean = false,
)

/** 实时进度快照（协议 DownloadProgress）。 */
data class DownloadProgress(
    val taskId: String,
    val status: TaskStatus,
    val bytesDownloaded: Long,
    val bytesTotal: Long,
    val speedBps: Double,
    val avgSpeedBps: Double,
    val peakSpeedBps: Double,
    val etaSeconds: Double?,
)

fun formatBytes(bytes: Long): String {
    if (bytes < 1024) return "$bytes B"
    val units = arrayOf("KB", "MB", "GB", "TB")
    var v = bytes.toDouble()
    var u = 0
    while (v >= 1024 && u < units.size - 1) { v /= 1024; u++ }
    return "%.1f %s".format(v, units[u])
}

fun formatSpeed(bps: Double): String =
    if (bps <= 0) "--" else formatBytes(bps.toLong()) + "/s"

fun formatEta(seconds: Double?): String {
    if (seconds == null || seconds <= 0 || !seconds.isFinite()) return "--"
    val m = (seconds / 60).toInt()
    val s = (seconds % 60).toInt()
    return if (m > 0) "${m}m${s}s" else "${s}s"
}
