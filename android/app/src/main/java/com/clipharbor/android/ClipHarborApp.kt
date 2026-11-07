package com.clipharbor.android

import android.app.Application
import com.clipharbor.android.core.YtDlpBridge

class ClipHarborApp : Application() {
    override fun onCreate() {
        super.onCreate()
        // yt-dlp 内嵌运行时初始化（首次解压，后台线程完成；未完成时引擎调用会等待）
        Thread {
            runCatching { YtDlpBridge.ensureInit(this) }
        }.start()
    }
}
