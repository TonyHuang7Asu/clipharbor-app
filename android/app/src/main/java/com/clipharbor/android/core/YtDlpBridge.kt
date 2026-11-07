package com.clipharbor.android.core

import android.content.Context
import com.yausername.youtubedl_android.YoutubeDL
import com.yausername.youtubedl_android.YoutubeDLRequest
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

/**
 * yt-dlp 桥接（youtubedl-android 0.18.1，内嵌 Python 运行时 + ffmpeg）。
 *
 * 职责边界与 Windows 端一致：视频站点交给 yt-dlp（自行完成 DASH 下载与合并），
 * 直接直链走 HttpDownloader。
 * 速度统计：500ms 轮询任务专属工作目录的落盘大小增量（真实数据，非估算）。
 */
object YtDlpBridge {

    @Volatile private var initialized = false

    /** 首次使用前初始化（解压内嵌 Python + yt-dlp）；幂等。 */
    fun ensureInit(context: Context) {
        if (initialized) return
        synchronized(this) {
            if (initialized) return
            YoutubeDL.getInstance().init(context)
            com.yausername.youtubedl_android.FfmpegLib.getInstance().init(context)
            initialized = true
        }
    }

    /** 后台更新内嵌 yt-dlp（YouTube 兼容性关键，设置页手动触发）。 */
    suspend fun updateEngine(context: Context): String = withContext(Dispatchers.IO) {
        ensureInit(context)
        when (YoutubeDL.getInstance().updateYoutubeDL(context, YoutubeDL.UpdateChannel.STABLE)) {
            YoutubeDL.UpdateStatus.DONE -> "已更新到最新 yt-dlp"
            YoutubeDL.UpdateStatus.ALREADY_UP_TO_DATE -> "已是最新"
            else -> "更新失败"
        }
    }

    /** 元数据（标题/上传者/时长/缩略图）。 */
    suspend fun info(url: String): VideoInfoLite? = withContext(Dispatchers.IO) {
        runCatching { YoutubeDL.getInstance().getInfo(url) }.getOrNull()?.let { v ->
            VideoInfoLite(
                title = v.title,
                uploader = v.uploader,
                durationSeconds = v.duration?.toDouble(),
                thumbnailUrl = v.thumbnailUrl,
            )
        }
    }

    data class VideoInfoLite(
        val title: String?, val uploader: String?,
        val durationSeconds: Double?, val thumbnailUrl: String?,
    )

    /**
     * 用 yt-dlp 真实下载（含音视频分离合并）。工作目录为 taskId 专属子目录，
     * 速度来自该目录落盘大小的真实增量。
     */
    suspend fun download(
        context: Context,
        taskId: String,
        url: String,
        workDir: File,
        filename: String?,
        meter: SpeedMeter,
        control: Control,
    ): File = withContext(Dispatchers.IO) {
        ensureInit(context)
        workDir.mkdirs()
        val request = YoutubeDLRequest(url).apply {
            addOption("-f", "bestvideo*+bestaudio/best")
            addOption("--merge-output-format", "mp4")
            addOption("--no-mtime")
            addOption("--restrict-filenames")
            addOption("-o", File(workDir, filename ?: "%(title).150s.%(ext)s").absolutePath)
        }
        coroutineScope {
            val poller = launch {
                while (isActive) {
                    delay(500)
                    if (control.cancelled) {
                        runCatching { YoutubeDL.getInstance().destroyProcessById(taskId) }
                        cancel()
                        break
                    }
                    val now = workDir.listFiles()?.sumOf { it.length() } ?: 0L
                    meter.onBytesDelta(now)   // SpeedMeter 内部换算增量
                }
            }
            try {
                val response = YoutubeDL.getInstance().execute(request, taskId) { _, _, _ ->
                    if (control.cancelled) throw RuntimeException("cancelled")
                }
                response.savedFile ?: newestFileIn(workDir)
            } catch (e: Exception) {
                if (control.cancelled) throw DownloadCancelled()
                throw e
            } finally {
                poller.cancelAndJoin()
            }
        }
    }

    private fun newestFileIn(dir: File): File =
        dir.listFiles()?.maxByOrNull { it.lastModified() } ?: dir
}
