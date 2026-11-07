package com.clipharbor.android

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.clipharbor.android.core.*
import kotlinx.coroutines.launch

/**
 * ClipHarbor Android（Phase A1 最小可用界面）：
 * 粘贴/分享链接 → 真实解析与下载 → 进度/速度/暂停/取消 → 历史与备份随 A2 扩展。
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val engine = DownloadEngine.get(applicationContext)
        // 处理系统分享（浏览器/YouTube → 分享到 ClipHarbor）
        val shared = intent?.getStringExtra(android.content.Intent.EXTRA_TEXT)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                HomeScreen(engine, prefill = shared ?: "")
            }
        }
    }
}

@Composable
fun HomeScreen(engine: DownloadEngine, prefill: String) {
    val scope = rememberCoroutineScope()
    var url by remember { mutableStateOf(prefill) }
    var message by remember { mutableStateOf<String?>(null) }
    val live by engine.live.collectAsState()
    val tasks = remember { mutableStateListOf<Map<String, Any?>>() }

    suspend fun refresh() {
        tasks.clear()
        tasks.addAll(engine.db.active())
    }

    LaunchedEffect(Unit) { refresh() }

    Column(Modifier.padding(16.dp)) {
        Text("ClipHarbor", style = MaterialTheme.typography.headlineSmall)
        Spacer(Modifier.height(12.dp))
        OutlinedTextField(
            value = url, onValueChange = { url = it },
            modifier = Modifier.fillMaxWidth(),
            placeholder = { Text("粘贴链接（直链或 YouTube/B站）") },
            singleLine = true,
        )
        Spacer(Modifier.height(8.dp))
        Row {
            Button(
                onClick = {
                    scope.launch {
                        runCatching { engine.add(url) }
                            .onSuccess { url = ""; refresh() }
                            .onFailure { message = it.message }
                    }
                },
                enabled = url.isNotBlank(),
            ) { Text("添加下载") }
            Spacer(Modifier.width(8.dp))
            OutlinedButton(onClick = { scope.launch { refresh() } }) { Text("刷新") }
        }
        message?.let {
            Spacer(Modifier.height(8.dp))
            Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
        }
        Spacer(Modifier.height(16.dp))
        Text("进行中", style = MaterialTheme.typography.titleMedium)
        LazyColumn {
            items(tasks, key = { it["id"] as? String ?: "" }) { row ->
                val id = row["id"] as? String ?: return@items
                val progress = live[id]
                val done = (row["bytes_downloaded"] as? Long) ?: 0L
                val total = (row["bytes_total"] as? Long) ?: 0L
                val status = row["status"] as? String ?: ""
                Column(Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
                    Text(
                        (row["title"] as? String)?.ifEmpty { row["url"] as? String ?: "" } ?: "",
                        style = MaterialTheme.typography.bodyMedium,
                        maxLines = 1,
                    )
                    if (total > 0) {
                        LinearProgressIndicator(
                            progress = { (done.toDouble() / total).toFloat().coerceIn(0f, 1f) },
                            modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                        )
                    }
                    Text(
                        "${formatBytes(done)}/${if (total > 0) formatBytes(total) else "未知"}  " +
                            "速度 ${formatSpeed(progress?.speedBps ?: 0.0)}  " +
                            "剩余 ${formatEta(progress?.etaSeconds)}  [$status]",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        if (status == "downloading" || status == "queued") {
                            TextButton(onClick = { engine.pause(id) }) { Text("暂停") }
                        } else if (status == "paused" || status == "failed") {
                            TextButton(onClick = { engine.resume(id) }) { Text("继续") }
                        }
                        if (!statusIsTerminal(status)) {
                            TextButton(onClick = { engine.cancel(id); scope.launch { refresh() } }) {
                                Text("取消")
                            }
                        }
                    }
                }
            }
        }
    }
}

private fun statusIsTerminal(status: String): Boolean =
    status == "completed" || status == "failed" || status == "cancelled"
