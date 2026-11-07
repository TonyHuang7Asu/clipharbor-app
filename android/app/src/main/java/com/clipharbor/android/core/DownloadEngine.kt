package com.clipharbor.android.core

import android.content.ContentValues
import android.content.Context
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import com.clipharbor.android.data.HistoryDb
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Semaphore
import java.io.File
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/**
 * 下载引擎（Phase A1）：并发 2、真实速度、断点续传（直链）、失败重试、
 * 崩溃恢复、yt-dlp 站点下载（内嵌 ffmpeg 合并）、MediaStore 落盘。
 * 与 Windows/iOS 引擎同构；进度通过 [live] StateFlow 提供给 Compose。
 */
class DownloadEngine private constructor(context: Context) {

    private val appContext = context.applicationContext
    val db = HistoryDb(appContext)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val semaphore = Semaphore(2)
    private val runtimes = ConcurrentHashMap<String, Runtime>()
    private val maxRetries = 4

    private class Runtime(val control: Control, val meter: SpeedMeter) {
        var lastCum: Long = 0
    }

    private val _live = MutableStateFlow<Map<String, DownloadProgress>>(emptyMap())
    val live: StateFlow<Map<String, DownloadProgress>> = _live

    init {
        db.recoverInterrupted()
        scope.launch {
            while (isActive) {          // 进度发布循环（500ms）
                delay(500)
                publishAll()
            }
        }
    }

    companion object {
        @Volatile private var instance: DownloadEngine? = null
        fun get(context: Context): DownloadEngine = instance ?: synchronized(this) {
            instance ?: DownloadEngine(context).also { instance = it }
        }
    }

    // ---- 对外操作 ---------------------------------------------------------

    suspend fun add(url: String): DownloadTask {
        db.findActiveByUrl(url)?.let { return rowToTask(it) }
        val direct = !isVideoSite(url)
        val task = DownloadTask(
            taskId = UUID.randomUUID().toString().take(16),
            sourceUrl = url.trim(),
            sourceId = if (direct) "direct" else "ytdlp",
        )
        db.upsert(listOf(
            "id" to task.taskId, "url" to task.sourceUrl, "source_id" to task.sourceId,
            "status" to TaskStatus.QUEUED.wire, "created_at" to task.createdAt,
        ))
        scope.launch { runWithRetry(task) }
        return task
    }

    fun pause(id: String) {
        val rt = runtimes[id]
        if (rt != null) {
            rt.control.paused = true
            return
        }
        db.update(id, listOf("status" to TaskStatus.PAUSED.wire))
    }

    fun resume(id: String) {
        val rt = runtimes[id]
        if (rt != null) {
            rt.control.paused = false
            return
        }
        db.task(id)?.let { row ->
            if (TaskStatus.from(row["status"] as? String ?: "") == TaskStatus.PAUSED) {
                db.update(id, listOf("status" to TaskStatus.QUEUED.wire, "error" to ""))
                scope.launch { runWithRetry(rowToTask(row)) }
            }
        }
    }

    fun cancel(id: String) {
        runtimes[id]?.control?.cancelled = true
        db.update(id, listOf("status" to TaskStatus.CANCELLED.wire, "error" to "已取消"))
    }

    fun isVideoSite(url: String): Boolean {
        val host = Regex("""https?://([^/]+)""").find(url)?.groupValues?.get(1)?.lowercase() ?: ""
        return listOf("youtube.com", "youtu.be", "bilibili.com", "b23.tv", "vimeo.com")
            .any { host == it || host.endsWith(".$it") }
    }

    // ---- 执行 -------------------------------------------------------------

    private fun runWithRetry(task: DownloadTask) = scope.launch {
        semaphore.acquire()
        try {
            var attempts = 0
            val rt = Runtime(Control(), SpeedMeter())
            runtimes[task.taskId] = rt
            try {
                while (true) {
                    if (rt.control.cancelled) { finishCancelled(task); return@launch }
                    if (rt.control.paused) {
                        db.update(task.taskId, listOf("status" to TaskStatus.PAUSED.wire))
                        while (rt.control.paused && !rt.control.cancelled) delay(400)
                        continue
                    }
                    try {
                        execute(task, rt)
                        finishCompleted(task)
                        return@launch
                    } catch (e: DownloadPaused) {
                        continue
                    } catch (e: DownloadCancelled) {
                        finishCancelled(task)
                        return@launch
                    } catch (e: Exception) {
                        attempts += 1
                        val message = "[network] ${e.message ?: e.javaClass.simpleName}"
                        if (attempts > maxRetries) {
                            finishFailed(task, message)
                            return@launch
                        }
                        db.update(task.taskId, listOf(
                            "status" to TaskStatus.RETRY_WAIT.wire, "error" to message))
                        delay(minOf(60_000.0, 2000.0 * (1 shl (attempts - 1))).toLong())
                    }
                }
            } finally {
                runtimes.remove(task.taskId)
            }
        } finally {
            semaphore.release()
        }
    }

    private suspend fun execute(task: DownloadTask, rt: Runtime) {
        db.update(task.taskId, listOf("status" to TaskStatus.DOWNLOADING.wire, "error" to ""))
        if (task.sourceId == "ytdlp") {
            val workDir = File(File(appContext.filesDir, "ytdlp"), task.taskId)
            val out = YtDlpBridge.download(
                appContext, task.taskId, task.sourceUrl, workDir, null, rt.meter, rt.control
            )
            val saved = moveToLibrary(out, (task.filename.ifEmpty { out.name }))
            rt.lastCum = saved.length()
            db.update(task.taskId, listOf(
                "file_path" to saved, "filename" to out.name,
                "bytes_downloaded" to saved.length(), "bytes_total" to saved.length(),
                "sha256" to HttpDownloader.sha256(saved), "verified" to true,
            ))
        } else {
            val (total, ranges, name) = HttpDownloader.probe(task.sourceUrl)
            val part = File(File(appContext.filesDir, "parts"), "${task.taskId}.part")
            val resume = if (ranges) part.length().coerceAtMost(if (total > 0) total else part.length()) else 0L
            if (resume == 0L) part.parentFile?.mkdirs()
            rt.lastCum = resume  // 续传字节不计入速度
            db.update(task.taskId, listOf(
                "bytes_total" to total, "bytes_downloaded" to resume,
                "filename" to sanitizeFilename(name.ifEmpty { "download" }),
            ))
            val stats = HttpDownloader(rt.control).download(
                task.sourceUrl, part, resume, total
            ) { downloaded ->
                rt.meter.onBytesDelta(downloaded)
            }
            val saved = moveToLibrary(part, sanitizeFilename(name.ifEmpty { "download" }))
            db.update(task.taskId, listOf(
                "file_path" to saved, "bytes_downloaded" to stats.bytes,
                "sha256" to stats.sha256, "verified" to (total <= 0L || stats.bytes == total),
            ))
            part.delete()
        }
    }

    // ---- 落盘 / 收尾 -------------------------------------------------------

    private fun libraryDir(): File =
        appContext.getExternalFilesDir(Environment.DIRECTORY_MOVIES)
            ?: appContext.filesDir

    private fun moveToLibrary(src: File, filename: String): String {
        if (Build.VERSION.SDK_INT >= 29) {
            val values = ContentValues().apply {
                put(MediaStore.MediaColumns.DISPLAY_NAME, filename)
                put(MediaStore.MediaColumns.MIME_TYPE, "video/mp4")
                put(MediaStore.MediaColumns.RELATIVE_PATH, "Download/ClipHarbor")
            }
            val uri = appContext.contentResolver
                .insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values) ?: return src.absolutePath
            appContext.contentResolver.openOutputStream(uri)?.use { out ->
                src.inputStream().use { it.copyTo(out) }
            }
            return uri.toString()
        }
        val dest = File(libraryDir(), uniqueName(filename))
        src.copyTo(dest, overwrite = false)
        return dest.absolutePath
    }

    private fun uniqueName(filename: String): String {
        var candidate = File(libraryDir(), filename)
        var i = 2
        while (candidate.exists()) {
            candidate = File(libraryDir(), "${filename.substringBeforeLast('.')} ($i).${filename.substringAfterLast('.', "mp4")}")
            i += 1
        }
        return candidate.name
    }

    private fun sanitizeFilename(name: String): String =
        name.replace(Regex("[\\\\/:*?\"<>|\\x00-\\x1f]"), "_")
            .replace("..", ".").trim(' ', '.').take(150).ifEmpty { "download" }

    private fun finishCompleted(task: DownloadTask) {
        val rt = runtimes[task.taskId]
        db.update(task.taskId, listOf(
            "status" to TaskStatus.COMPLETED.wire,
            "completed_at" to System.currentTimeMillis(),
            "avg_speed_bps" to (rt?.meter?.averageBps() ?: 0.0),
            "peak_speed_bps" to (rt?.meter?.peakBps() ?: 0.0),
            "error" to "",
        ))
    }

    private fun finishCancelled(task: DownloadTask) {
        db.update(task.taskId, listOf(
            "status" to TaskStatus.CANCELLED.wire, "error" to "已取消",
            "bytes_downloaded" to 0L,
        ))
    }

    private fun finishFailed(task: DownloadTask, error: String) {
        db.update(task.taskId, listOf("status" to TaskStatus.FAILED.wire, "error" to error))
    }

    private fun publishAll() {
        val map = mutableMapOf<String, DownloadProgress>()
        for (row in db.active()) {
            val id = row["id"] as? String ?: continue
            val rt = runtimes[id]
            val speed = rt?.meter?.currentBps() ?: 0.0
            val done = (row["bytes_downloaded"] as? Long) ?: 0L
            val total = (row["bytes_total"] as? Long) ?: 0L
            val eta = if (speed > 0 && total > done) (total - done) / speed else null
            map[id] = DownloadProgress(
                taskId = id,
                status = TaskStatus.from(row["status"] as? String ?: ""),
                bytesDownloaded = done, bytesTotal = total,
                speedBps = speed, avgSpeedBps = rt?.meter?.averageBps() ?: 0.0,
                peakSpeedBps = rt?.meter?.peakBps() ?: 0.0, etaSeconds = eta,
            )
        }
        _live.value = map
    }

    private fun rowToTask(row: Map<String, Any?>): DownloadTask = DownloadTask(
        taskId = row["id"] as? String ?: "",
        sourceUrl = row["url"] as? String ?: "",
        sourceId = row["source_id"] as? String ?: "direct",
        title = row["title"] as? String ?: "",
        filename = row["filename"] as? String ?: "",
        filePath = row["file_path"] as? String ?: "",
        status = TaskStatus.from(row["status"] as? String ?: ""),
        bytesDownloaded = (row["bytes_downloaded"] as? Long) ?: 0L,
        bytesTotal = (row["bytes_total"] as? Long) ?: 0L,
        error = row["error"] as? String ?: "",
        createdAt = (row["created_at"] as? Long) ?: 0L,
        completedAt = row["completed_at"] as? Long,
        avgSpeedBps = (row["avg_speed_bps"] as? Double) ?: 0.0,
        peakSpeedBps = (row["peak_speed_bps"] as? Double) ?: 0.0,
        sha256 = row["sha256"] as? String ?: "",
        verified = ((row["verified"] as? Long) ?: 0L) == 1L,
    )
}
