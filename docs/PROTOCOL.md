# ClipHarbor 跨平台协议 v1.0

适用于 ClipHarbor Windows（FastAPI 后台，Phase 4）与 ClipHarbor iOS / 未来 macOS。
两端本地 SQLite 的行结构亦按本协议的 camelCase 键名序列化，保证历史同步时零转换。

## 1. 版本与标识

- 协议名：`clipharbor`
- API 版本：`1.0`（重大不兼容变更时升主版本；新增可选字段不升版）
- 所有 JSON 均为 UTF-8；时间为 ISO 8601 UTC 字符串；字节一律为整数字节（不使用位）
- 服务器在 `GET /api/v1/health` 返回 `{"protocol":"clipharbor","apiVersion":"1.0",...}`

## 2. 任务状态与阶段（两端共用枚举）

TaskStatus：`queued` / `downloading` / `paused` / `retry_wait` / `merging` / `zipping` /
`completed` / `failed` / `cancelled`

TaskPhase（细粒度，用于区分"网络下载 / 等待重试 / 合并 / 打包 / 完成"）：
`idle` / `network` / `retry_wait` / `merge` / `zip` / `done`

规则：
- `retry_wait` 状态下必须带 `error`（如 `[network] connection reset`、`[http_404] ...`）
- 速度字段单位为 B/s；`etaSeconds` 无法估算时为 `null`，不得虚构
- `bytesTotal = 0` 表示服务器未提供大小（显示"未知"）

## 3. 数据对象

### Task
```json
{
  "taskId": "hex16",
  "sourceUrl": "https://…",
  "sourceId": "direct | ytdlp | …",
  "title": "",
  "filename": "Big_Buck_Bunny_360_10s_1MB.mp4",
  "destDir": "C:\\Users\\…\\ClipHarbor",
  "filePath": "C:\\Users\\…\\Big_Buck_Bunny_360_10s_1MB.mp4",
  "formatId": "",
  "thumbnailUrl": "",
  "status": "downloading",
  "phase": "network",
  "bytesDownloaded": 1048576,
  "bytesTotal": 991017,
  "error": "",
  "retries": 0,
  "createdAt": "2026-10-27T12:00:00+00:00",
  "updatedAt": "2026-10-27T12:00:05+00:00",
  "completedAt": null,
  "avgSpeedBps": 27500.0,
  "peakSpeedBps": 1048576.0,
  "sha256": "…",
  "verified": true
}
```

### DownloadProgress（实时快照）
```json
{
  "taskId": "hex16",
  "status": "downloading",
  "phase": "network",
  "bytesDownloaded": 1048576,
  "bytesTotal": 991017,
  "speedBps": 250000.0,
  "avgSpeedBps": 220000.0,
  "peakSpeedBps": 300000.0,
  "etaSeconds": 12.5,
  "error": "",
  "updatedAt": "2026-10-27T12:00:05+00:00"
}
```

### MediaInfo（链接解析结果；取不到的字段必须为 null，不得虚构）
```json
{
  "sourceUrl": "https://…",
  "sourceId": "direct",
  "title": "Big Buck Bunny",
  "uploader": null,
  "durationSeconds": null,
  "thumbnailUrl": null,
  "isPlaylist": false,
  "entries": [],
  "formats": [
    {
      "formatId": "default",
      "container": "mp4",
      "resolution": "360p",
      "height": 360,
      "fps": null,
      "vcodec": "h264",
      "acodec": "aac",
      "filesizeBytes": 991017,
      "note": ""
    }
  ]
}
```

### DownloadHistory = Task（status ∈ completed/failed/cancelled 的子集）

### DeviceInfo
```json
{
  "deviceId": "uuid",
  "name": "DESKTOP-XXXX",
  "platform": "windows | ios | macos",
  "appVersion": "0.1.0",
  "protocolVersion": "1.0"
}
```

### PairingInfo
```json
{
  "deviceId": "uuid",
  "deviceName": "iPhone 15 Pro",
  "token": "hex48（仅配对响应中出现一次，服务端不得写入日志）",
  "protocolVersion": "1.0",
  "createdAt": "2026-10-27T12:00:00+00:00"
}
```

## 4. REST API（Phase 4 实现，路径与语义现在冻结）

认证：除 `GET /health` 与 `POST /pair` 外，一律要求 `Authorization: Bearer <token>`。
错误统一信封：`{"error": {"code": "not_found", "message": "…"}}`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/health` | 免认证；返回协议版本、DeviceInfo、磁盘剩余 |
| POST | `/api/v1/pair` | 请求头 `X-Pairing-Code`（6 位数字，服务端轮换显示）→ PairingInfo |
| GET | `/api/v1/tasks` | 任务列表（含活动与历史，`?status=` 过滤） |
| POST | `/api/v1/tasks` | 创建下载 `{sourceUrl, formatId?, filename?}` → Task |
| GET | `/api/v1/tasks/{taskId}/progress` | DownloadProgress 快照 |
| POST | `/api/v1/tasks/{taskId}/pause` / `resume` / `cancel` / `retry` | 任务控制 |
| GET | `/api/v1/history?query=&limit=&offset=` | 历史（仅 completed/failed/cancelled） |
| GET | `/api/v1/disk` | 保存目录磁盘剩余 |
| GET | `/api/v1/files/{taskId}/meta` | 已完成文件的元数据（路径白名单内，无任意读取） |
| GET | `/api/v1/files/{taskId}/content` | 文件传输（支持 Range；仅限已完成任务） |
| GET | `/openapi.json` | FastAPI 自动生成的 OpenAPI 文档 |

安全要求（服务端实现为验收条件）：
- 默认仅绑定 127.0.0.1；远程访问必须显式开启
- 任务创建等接收 URL 的服务端路径必须执行严格 URL 校验：
  仅 http/https，且解析后拒绝 localhost/环回/私有/CGNAT/链路本地/保留/组播地址
- 禁止路径穿越：文件接口只能按 taskId 经注册表定位，不接受客户端路径
- token 只在配对响应出现一次；任何日志不得记录 token 或 pairing code
- 无效 token 连续失败 10 次后锁定该来源 60 秒

## 5. 历史同步（两端对等）

- 每端本地保存完整历史；连接后按 `taskId`（全局唯一，uuid hex16）做并集合并
- 冲突时以 `updatedAt` 较新者为准（last-write-wins）；不删除对端记录
- 同步只传输 JSON 元数据；文件本体按需经 `/files/{taskId}/content` 拉取

## 6. 下载引擎行为约定（两端一致）

- 并发默认 2，用户可调（1–4）
- 速度来自真实传输统计；暂停/重试等待不计入平均速度
- 断点续传：服务器支持 Range 时续传，否则从头下载
- 完成校验：大小必须匹配；Windows 端额外计算 SHA-256
- 失败重试：指数退避（2^n 秒，上限 60s，最多 4 次）；4xx（除 429）不重试
- 磁盘空间不足：立即安全失败，保留分片与已下载文件
- 绝不覆盖同名文件（唯一名后缀 `(2)`、`(3)`…）
