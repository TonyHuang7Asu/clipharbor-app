package com.clipharbor.android.data

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper

/**
 * 历史/任务持久化（SQLite，全部参数绑定；表结构与协议字段一致）。
 * 升级仅 ADD COLUMN，绝不删表清空历史。
 */
class HistoryDb(context: Context) :
    SQLiteOpenHelper(context, "clipharbor.db", null, DB_VERSION) {

    companion object {
        const val DB_VERSION = 1
    }

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """CREATE TABLE IF NOT EXISTS downloads (
                id TEXT PRIMARY KEY NOT NULL,
                url TEXT NOT NULL,
                source_id TEXT NOT NULL DEFAULT 'direct',
                title TEXT NOT NULL DEFAULT '',
                filename TEXT NOT NULL DEFAULT '',
                file_path TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'queued',
                bytes_downloaded INTEGER NOT NULL DEFAULT 0,
                bytes_total INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                completed_at INTEGER,
                avg_speed_bps REAL NOT NULL DEFAULT 0,
                peak_speed_bps REAL NOT NULL DEFAULT 0,
                sha256 TEXT NOT NULL DEFAULT '',
                verified INTEGER NOT NULL DEFAULT 0
            )"""
        )
        db.execSQL("CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads(status)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        // 迁移策略：只增列（固定语句），绝不清空历史
    }

    fun upsert(values: List<Pair<String, Any?>>) {
        val names = values.joinToString(",") { it.first }
        val marks = values.joinToString(",") { "?" }
        val args = values.map { bind(it.second) }.toTypedArray()
        writableDatabase.execSQL(
            "INSERT OR REPLACE INTO downloads($names) VALUES($marks)", args
        )
    }

    fun update(id: String, values: List<Pair<String, Any?>>) {
        val allowed = setOf("status", "bytes_downloaded", "bytes_total", "error",
            "file_path", "filename", "completed_at", "avg_speed_bps",
            "peak_speed_bps", "sha256", "verified")
        val pairs = values.filter { it.first in allowed }
            .joinToString(",") { "${it.first} = ?" }
        if (pairs.isEmpty()) return
        val args = values.filter { it.first in allowed }.map { bind(it.second) }
            .toTypedArray() + arrayOf(id)
        writableDatabase.execSQL("UPDATE downloads SET $pairs WHERE id = ?", args)
    }

    fun task(id: String): Map<String, Any?>? {
        readableDatabase.rawQuery("SELECT * FROM downloads WHERE id = ?", arrayOf(id)).use { c ->
            if (!c.moveToFirst()) return null
            return rowToMap(c)
        }
    }

    fun active(): List<Map<String, Any?>> {
        readableDatabase.rawQuery(
            "SELECT * FROM downloads WHERE status IN ('queued','downloading','paused','retry_wait','merging') ORDER BY created_at",
            null
        ).use { c ->
            val out = mutableListOf<Map<String, Any?>>()
            while (c.moveToNext()) out.add(rowToMap(c))
            return out
        }
    }

    fun history(query: String, limit: Int = 300): List<Map<String, Any?>> {
        val like = "%$query%"
        readableDatabase.rawQuery(
            "SELECT * FROM downloads WHERE status IN ('completed','failed','cancelled') " +
                "AND (title LIKE ? OR url LIKE ? OR filename LIKE ?) " +
                "ORDER BY completed_at DESC LIMIT ?",
            arrayOf(like, like, like, limit.toString())
        ).use { c ->
            val out = mutableListOf<Map<String, Any?>>()
            while (c.moveToNext()) out.add(rowToMap(c))
            return out
        }
    }

    fun findActiveByUrl(url: String): Map<String, Any?>? {
        readableDatabase.rawQuery(
            "SELECT * FROM downloads WHERE url = ? AND status IN " +
                "('queued','downloading','paused','retry_wait','merging') LIMIT 1",
            arrayOf(url)
        ).use { c ->
            if (!c.moveToFirst()) return null
            return rowToMap(c)
        }
    }

    /** 崩溃恢复：中断任务转为可续传暂停态。 */
    fun recoverInterrupted(): Int {
        writableDatabase.execSQL(
            "UPDATE downloads SET status = 'paused', error = '应用退出时中断，可续传' " +
                "WHERE status IN ('downloading','retry_wait')"
        )
        return 0
    }

    private fun rowToMap(c: android.database.Cursor): Map<String, Any?> {
        val map = mutableMapOf<String, Any?>()
        for (i in 0 until c.columnCount) map[c.getColumnName(i)] = c.getColumnValue(i)
        return map
    }

    private fun android.database.Cursor.getColumnValue(i: Int): Any? = when (getType(i)) {
        android.database.Cursor.FIELD_TYPE_NULL -> null
        android.database.Cursor.FIELD_TYPE_INTEGER -> getLong(i)
        android.database.Cursor.FIELD_TYPE_FLOAT -> getDouble(i)
        else -> getString(i)
    }

    private fun bind(v: Any?): Any? = when (v) {
        is Int -> v.toLong()
        is Boolean -> if (v) 1L else 0L
        else -> v  // execSQL 的绑定数组支持 null（绑定为 NULL）
    }
}
