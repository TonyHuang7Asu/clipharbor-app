# iOS 快捷指令 + 自建 cobalt 部署与使用（已在本机部署并验证）

状态（2026-10-29）：**cobalt 11.7.1 已在你的电脑上运行**（Docker 容器
`clipharbor-cobalt`，开机自启），并实测完成真实 YouTube 解析——返回了
"Big Buck Bunny 60fps 4K" 的 1080p 隧道链接与文件名。
本机网络封锁 googlevideo 媒体流（隧道 0 字节），你实际使用时若网络/代理可访问
YouTube 即为完整可用；自检方法见下。

## 1. 部署事实

- 容器：`clipharbor-cobalt`，镜像 `ghcr.io/imputnet/cobalt:11`，策略 `unless-stopped`
- 地址：`http://192.168.0.105:9000/`（局域网内 iPhone 可直接访问）
- 常用命令：
  ```bash
  docker logs --tail 20 clipharbor-cobalt     # 看日志
  docker restart clipharbor-cobalt            # 重启
  docker rm -f clipharbor-cobalt              # 卸载
  ```

## 2. 一分钟自检（判断你当前网络是否可用）

电脑上：
```bash
curl -s -X POST http://127.0.0.1:9000/ -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=aqz-KE-bpKQ","videoQuality":"1080"}'
```
- 返回 `"status":"tunnel"` + filename → 解析成功（本机已验证 ✅）
- 再用浏览器或 curl 访问返回的 tunnel 链接：能出视频数据 → **链路全通**；
  若 0 字节 → 你的网络封锁了 googlevideo 媒体流（与本开发机相同），
  解决：在 Docker Desktop → Settings → Resources → Proxies 里填入你
  可用的代理地址后重启容器（代理也必须能访问 googlevideo）。

## 3. iPhone 快捷指令搭建（约 3 分钟，无需签名任何东西）

打开"快捷指令"App → 新建快捷指令，命名"ClipHarbor 下载"，依次添加动作：

1. **接收**：快捷指令设置 → 输入 → 打开"共享表单中接收"→ 选 URL（ Safari 分享即用）
2. **获取 URL 内容**（网络请求）：
   - URL：`http://192.168.0.105:9000/`
   - 方法：POST
   - 头部：`Accept: application/json`、`Content-Type: application/json`
   - 请求体（JSON）：`{"url": "输入的 URL", "videoQuality": "1080"}`
     （"输入的 URL" 用变量插入；要纯音频改为 `"downloadMode":"audio"`）
3. **获取词典值**：取 `url`（即 cobalt 返回的隧道链接）
4. **获取 URL 内容**（下载）：上一步的词典值 → 留空方法（GET），
   打开"显示下载进度"
5. **存储到"文件"**：目标 `我的 iPhone/ClipHarbor`（会自动建目录）；
   文件名用词典值 `filename`
6. （可选）**显示通知**：内容 = "已保存 filename"

首次运行 iOS 会询问"允许快捷指令访问 192.168.0.105"→ 允许。
手机与电脑须在同一 Wi-Fi；电脑关机时该快捷指令不可用（iPhone 独立引擎兜底直链）。

## 4. 已知限制（如实）

- 仅限你有权获取的内容；不绕过任何 DRM/访问控制。
- cobalt 与 yt-dlp 同样受 YouTube 变更影响：更新镜像 `docker pull ghcr.io/imputnet/cobalt:11`
  && 重启容器即可跟进官方修复。
- 本网络实测：提取 ✅ / 隧道出流 ❌（googlevideo 被封）。你的网络可能不同，先跑第 2 节自检。
