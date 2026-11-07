import Foundation

/// 链接解析结果（协议 MediaInfo）。
struct MediaInfo: Codable {
    var sourceUrl: String
    var sourceId: String
    var title: String?
    var uploader: String?        // 取不到一律 nil，界面显示“未知”，不虚构
    var durationSeconds: Double?
    var thumbnailUrl: String?
    var isPlaylist: Bool
    var entries: [String]
    var formats: [FormatInfo]
    /// 实际下载地址（cobalt 等解析器返回的时效链接）；nil 时用 sourceUrl 下载。
    /// sourceUrl 保持原始链接（去重/历史展示用），二者分离使重试可重新解析。
    var downloadUrl: String? = nil

    struct FormatInfo: Codable {
        var formatId: String
        var container: String
        var resolution: String?
        var height: Int?
        var fps: Int?
        var vcodec: String?
        var acodec: String?
        var filesizeBytes: Int64?
        var note: String
    }
}

enum SourceAdapterError: LocalizedError {
    case unsupported(String)

    var errorDescription: String? { switch self { case .unsupported(let m): return m } }
}

/// 来源适配器协议：未来 macOS / 更多来源共用。
protocol SourceAdapter {
    var sourceId: String { get }
    func canHandle(_ url: URL) -> Bool
    /// 解析媒体信息；必须基于真实 HTTP 元数据，取不到的字段为 nil。
    func resolve(_ url: URL) async throws -> MediaInfo
}

/// 直接 HTTP(S) 媒体直链适配器（iOS 独立可用，不依赖任何外部服务）。
struct DirectHTTPAdapter: SourceAdapter {
    let sourceId = "direct"

    func canHandle(_ url: URL) -> Bool {
        url.scheme == "http" || url.scheme == "https"
    }

    func resolve(_ url: URL) async throws -> MediaInfo {
        guard let scheme = url.scheme, ["http", "https"].contains(scheme) else {
            throw SourceAdapterError.unsupported("仅支持 http/https 直链")
        }
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("bytes=0-0", forHTTPHeaderField: "Range")
        request.setValue("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                         forHTTPHeaderField: "User-Agent")
        request.timeoutInterval = 20
        let (_, response) = try await URLSession.shared.data(from: request, delegate: nil)
        guard let http = response as? HTTPURLResponse else {
            throw SourceAdapterError.unsupported("非 HTTP 响应")
        }
        var total: Int64 = 0
        var acceptsRanges = false
        if http.statusCode == 206 {
            acceptsRanges = true
            if let contentRange = http.value(forHTTPHeaderField: "Content-Range"),
               let sizePart = contentRange.split(separator: "/").last,
               let parsed = Int64(sizePart) { total = parsed }
        } else if http.statusCode == 200 {
            total = (try? Int64(http.value(forHTTPHeaderField: "Content-Length") ?? "")) ?? 0
        }
        let filename = responseFilename(response) ?? url.lastPathComponent
        let type = (http.value(forHTTPHeaderField: "Content-Type") ?? "").split(separator: ";").first.map(String.init) ?? ""
        return MediaInfo(
            sourceUrl: response.url?.absoluteString ?? url.absoluteString,
            sourceId: sourceId,
            title: filename,
            uploader: nil,
            durationSeconds: nil,
            thumbnailUrl: nil,
            isPlaylist: false,
            entries: [],
            formats: [MediaInfo.FormatInfo(
                formatId: "default", container: (filename as NSString).pathExtension.isEmpty ? type : (filename as NSString).pathExtension,
                resolution: nil, height: nil, fps: nil, vcodec: nil, acodec: nil,
                filesizeBytes: total > 0 ? total : nil,
                note: acceptsRanges ? "支持断点续传" : "服务器不支持 Range，将整文件下载"
            )]
        )
    }

    private func responseFilename(_ response: URLResponse) -> String? {
        guard let http = response as? HTTPURLResponse,
              let disposition = http.value(forHTTPHeaderField: "Content-Disposition") else { return nil }
        // filename*=UTF-8''… 优先，其次 filename="…"
        if let range = disposition.range(of: "filename*=") {
            let raw = disposition[range.upperBound...]
                .split(separator: ";").first.map(String.init) ?? ""
            let decoded = raw.replacingOccurrences(of: "UTF-8''", with: "")
            return decoded.removingPercentEncoding ?? decoded
        }
        if let range = disposition.range(of: "filename=") {
            var raw = String(disposition[range.upperBound...]).split(separator: ";").first.map(String.init) ?? ""
            raw = raw.trimmingCharacters(in: CharacterSet(charactersIn: "\" "))
            return raw.isEmpty ? nil : raw
        }
        return nil
    }
}

/// 视频站点判定（YouTube/B站等需专用解析器的站点）。
func isVideoSite(_ url: URL) -> Bool {
    guard let host = url.host?.lowercased() else { return false }
    let sites = ["youtube.com", "youtu.be", "bilibili.com", "b23.tv", "vimeo.com"]
    return sites.contains { host == $0 || host.hasSuffix(".\($0)") }
}

/// YouTube 适配器 —— cobalt 未配置时的回退：明确告知"未支持"与配置方法。
/// 不得伪装支持；cobalt（电脑端）已配置时由 CobaltAdapter 接管。
struct YouTubeAdapter: SourceAdapter {
    let sourceId = "youtube"

    func canHandle(_ url: URL) -> Bool { isVideoSite(url) }

    func resolve(_ url: URL) async throws -> MediaInfo {
        throw SourceAdapterError.unsupported(
            "YouTube 等视频站点需经电脑端 cobalt 解析（iPhone 不做本地解析）。"
            + "请在 设置 → YouTube 解析 中填入 cobalt 地址（默认 http://192.168.0.105:9000）"
            + "并确保电脑在线；或使用直接媒体直链。"
        )
    }
}

/// 适配器注册表：视频站点优先走已配置的 cobalt（电脑端解析），否则回退到明确提示。
enum SourceAdapters {
    static let all: [SourceAdapter] = [DirectHTTPAdapter(), YouTubeAdapter()]

    static func adapter(for url: URL) -> SourceAdapter {
        if let cobalt = CobaltAdapter.configured, cobalt.canHandle(url) {
            return cobalt
        }
        return all.first { $0.canHandle(url) } ?? DirectHTTPAdapter()
    }
}
