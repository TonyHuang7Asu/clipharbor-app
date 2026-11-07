package com.clipharbor.android.core

import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/** 协作式控制面（每个数据块之间检查）。 */
class Control {
    @Volatile var paused: Boolean = false
    @Volatile var cancelled: Boolean = false
}

class DownloadPaused : Exception()
class DownloadCancelled : Exception()

/** 直链 HTTP 下载器：Range 断点续传 + 真实速度统计 + SHA-256。 */
class HttpDownloader(private val control: Control, private val readTimeoutMs: Int = 30_000) {

    data class Stats(val bytes: Long, val sha256: String)

    fun download(url: String, destFile: File, resumeFrom: Long,
                 expectedTotal: Long, onProgress: (Long) -> Unit): Stats {
        val conn = (URL(url).openConnection() as HttpURLConnection).apply {
            connectTimeout = 15_000
            readTimeout = readTimeoutMs
            setRequestProperty("User-Agent", USER_AGENT)
            setRequestProperty("Accept", "*/*")
            if (resumeFrom > 0) setRequestProperty("Range", "bytes=$resumeFrom-")
            instanceFollowRedirects = true
        }
        val code = conn.responseCode
        if (code !in listOf(HttpURLConnection.HTTP_OK, 206)) {
            conn.disconnect()
            throw IOException("HTTP $code")
        }
        var base = resumeFrom
        if (resumeFrom > 0 && code == HttpURLConnection.HTTP_OK) {
            // 服务器忽略 Range：丢弃本地分片，从头接收
            base = 0
            destFile.writeBytes(ByteArray(0))
        }
        destFile.parentFile?.mkdirs()
        conn.inputStream.use { input ->
            java.io.FileOutputStream(destFile, base > 0).use { out ->
                var written = 0L
                val buf = ByteArray(256 * 1024)
                while (true) {
                    if (control.cancelled) throw DownloadCancelled()
                    if (control.paused) throw DownloadPaused()
                    val n = input.read(buf)
                    if (n < 0) break
                    out.write(buf, 0, n)
                    written += n
                    onProgress(base + written)
                }
                out.flush()
                out.fd.sync()
            }
        }
        conn.disconnect()
        val total = base + (destFile.length() - if (base > 0) 0 else 0)
        val finalSize = destFile.length()
        if (expectedTotal > 0 && finalSize != expectedTotal) {
            throw IOException("大小校验失败: $finalSize != $expectedTotal")
        }
        return Stats(finalSize, sha256(destFile))
    }

    companion object {
        const val USER_AGENT =
            "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36"

        fun sha256(file: File): String {
            val md = MessageDigest.getInstance("SHA-256")
            file.inputStream().use { input ->
                val buf = ByteArray(1 shl 20)
                while (true) {
                    val n = input.read(buf)
                    if (n < 0) break
                    md.update(buf, 0, n)
                }
            }
            return md.digest().joinToString("") { "%02x".format(it) }
        }

        /** 探测：大小 + Range 支持（GET bytes=0-0）。 */
        fun probe(url: String): Triple<Long, Boolean, String> {
            val conn = (URL(url).openConnection() as HttpURLConnection).apply {
                connectTimeout = 15_000; readTimeout = 20_000
                setRequestProperty("User-Agent", USER_AGENT)
                setRequestProperty("Range", "bytes=0-0")
            }
            try {
                val code = conn.responseCode
                var total = 0L; var ranges = false
                if (code == 206) {
                    ranges = true
                    conn.getHeaderField("Content-Range")?.let { cr ->
                        val idx = cr.lastIndexOf('/')
                        if (idx >= 0) total = cr.substring(idx + 1).toLongOrNull() ?: 0L
                    }
                } else if (code == HttpURLConnection.HTTP_OK) {
                    total = conn.getHeaderField("Content-Length")?.toLongOrNull() ?: 0L
                }
                val name = conn.getHeaderField("Content-Disposition")
                    ?.substringAfter("filename=", "")?.trim('"', ' ', ';')
                    ?: URL(url).file.substringAfterLast('/')
                return Triple(total, ranges, name)
            } finally {
                conn.disconnect()
            }
        }
    }
}
