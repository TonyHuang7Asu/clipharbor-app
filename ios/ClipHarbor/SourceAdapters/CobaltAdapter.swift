import Foundation

/// 下载质量档位（仿 x-video-downloader 五档）。
/// 值直接映射 cobalt API 的 videoQuality / audioFormat 参数。
enum DownloadQuality: String, CaseIterable, Identifiable {
    case best          // 最佳画质 → cobalt videoQuality "max"
    case q1080 = "1080"
    case q720 = "720"
    case q480 = "480"
    case audioMP3 = "mp3"  // 仅音频 → downloadMode audio + audioFormat mp3

    var id: String { rawValue }

    var label: String {
        switch self {
        case .best: return "最佳画质"
        case .q1080: return "1080p"
        case .q720: return "720p"
        case .q480: return "480p"
        case .audioMP3: return "仅音频 MP3"
        }
    }

    /// 分段选择器的短标签（5 档并排显示）。
    var shortLabel: String {
        switch self {
        case .best: return "最佳"
        case .q1080: return "1080p"
        case .q720: return "720p"
        case .q480: return "480p"
        case .audioMP3: return "MP3"
        }
    }

    var isAudio: Bool { self == .audioMP3 }

    /// UserDefaults 存储键（HomeView 的 @AppStorage 共用）。
    static let storageKey = "downloadQuality"

    static var current: DownloadQuality {
        DownloadQuality(rawValue: UserDefaults.standard.string(forKey: storageKey) ?? "") ?? .best
    }
}

/// cobalt 适配器：通过电脑上自建的 cobalt API 解析视频站点（含 YouTube）。
///
/// 诚实标注（不伪装本地能力）：
/// - 解析发生在电脑端（cobalt 11.7.1，已实测可解析 YouTube）；iPhone 负责真实下载
///   （原生后台 URLSession、真实速度统计）。
/// - 需要电脑在线且与手机同一局域网；电脑离线时本适配器不可用，YouTube 回到"未支持"。
/// - cobalt 返回的隧道链接有时效（约 10 分钟），适合"添加即下载"的流程；
///   引擎把"原始链接"与"隧道下载链接"分开存储，重试时会自动重新解析。
struct CobaltAdapter: SourceAdapter {
    let sourceId = "cobalt"
    let baseURL: URL

    static let storageKey = "cobaltBaseURL"
    static let defaultURL = "http://192.168.0.105:9000"

    /// 从设置读取 cobalt 服务地址；未配置时返回 nil（视频站点回退到"未支持"提示）。
    static var configured: CobaltAdapter? {
        guard let raw = UserDefaults.standard.string(forKey: storageKey)?
            .trimmingCharacters(in: .whitespacesAndNewlines),
            !raw.isEmpty,
            let url = URL(string: raw) else { return nil }
        return CobaltAdapter(baseURL: url)
    }

    func canHandle(_ url: URL) -> Bool { isVideoSite(url) }

    // MARK: - YouTube 缩略图（无需任何 API：i.ytimg.com 直链）

    /// 从 YouTube 链接提取视频 ID（watch?v= / youtu.be/ / shorts/ / embed/ / live/）。
    static func youtubeVideoID(from url: URL) -> String? {
        let host = url.host?.lowercased() ?? ""
        if host == "youtu.be" || host.hasSuffix(".youtu.be") {
            let id = url.pathComponents.count > 1 ? url.pathComponents[1] : ""
            return id.isEmpty ? nil : id
        }
        guard host.contains("youtube.com") else { return nil }
        if let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems,
           let v = items.first(where: { $0.name == "v" })?.value, !v.isEmpty {
            return v
        }
        let comps = url.pathComponents
        let markers = ["shorts", "embed", "live", "v"]
        for marker in markers {
            if let idx = comps.firstIndex(of: marker), idx + 1 < comps.count {
                return comps[idx + 1]
            }
        }
        return nil
    }

    /// 视频站的公开缩略图直链（YouTube 用视频 ID 直接拼出，其余站点无此能力返回 nil）。
    static func thumbnailURL(for original: URL) -> URL? {
        guard let id = youtubeVideoID(from: original) else { return nil }
        return URL(string: "https://i.ytimg.com/vi/\(id)/mqdefault.jpg")
    }

    struct CobaltResponse: Codable {
        let status: String
        let url: String?
        let filename: String?
        struct ErrorBody: Codable { let code: String? }
        let error: ErrorBody?
    }

    /// SourceAdapter 协议默认档位（历史调用兼容）。
    func resolve(_ url: URL) async throws -> MediaInfo {
        try await resolve(url, quality: DownloadQuality.current)
    }

    /// 按档位解析：返回的 MediaInfo.sourceUrl 是**原始链接**（用于去重/历史展示），
    /// formats[0].formatId 携带隧道下载链接（引擎读取后存入 task.downloadURL）。
    func resolve(_ url: URL, quality: DownloadQuality) async throws -> MediaInfo {
        var body: [String: Any] = ["url": url.absoluteString]
        if quality.isAudio {
            body["downloadMode"] = "audio"
            body["audioFormat"] = "mp3"
        } else {
            body["videoQuality"] = quality == .best ? "max" : quality.rawValue
            body["youtubeVideoCodec"] = "h264"   // 兼容性最好；cobalt 默认即 h264，显式写明
        }
        var request = URLRequest(url: baseURL)
        request.httpMethod = "POST"
        request.timeoutInterval = 30
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw SourceAdapterError.unsupported("cobalt 返回了非 HTTP 响应")
        }
        guard http.statusCode == 200 else {
            throw SourceAdapterError.unsupported(
                "cobalt 服务返回 HTTP \(http.statusCode)：请确认电脑在线、地址正确"
            )
        }
        guard let resp = try? JSONDecoder().decode(CobaltResponse.self, from: data) else {
            throw SourceAdapterError.unsupported("无法解析 cobalt 响应")
        }
        if resp.status == "error" {
            throw SourceAdapterError.unsupported(
                "cobalt 解析失败：\(resp.error?.code ?? "未知原因")（来源是否允许下载？）"
            )
        }
        guard resp.status == "tunnel" || resp.status == "redirect",
              let tunnel = resp.url, let tunnelURL = URL(string: tunnel) else {
            throw SourceAdapterError.unsupported("cobalt 未返回可下载链接")
        }
        let note = quality.isAudio
            ? "由电脑 cobalt 解析（MP3 转码在电脑端完成）；隧道链接约 10 分钟有效"
            : "由电脑 cobalt 解析（\(quality.label)，需电脑在线）；隧道链接约 10 分钟有效，过期后重试会自动重新解析"
        return MediaInfo(
            sourceUrl: url.absoluteString,
            sourceId: sourceId,
            title: resp.filename ?? url.lastPathComponent,
            uploader: nil,                       // cobalt 隧道响应不含上传者，显示"未知"
            durationSeconds: nil,
            thumbnailUrl: Self.thumbnailURL(for: url)?.absoluteString,
            isPlaylist: false,
            entries: [],
            formats: [MediaInfo.FormatInfo(
                formatId: "cobalt-tunnel",
                container: quality.isAudio ? "mp3" : "mp4",
                resolution: quality.isAudio ? nil : quality == .best ? "最佳" : "\(quality.rawValue)p",
                height: quality.isAudio ? nil : (quality == .best ? nil : Int(quality.rawValue)),
                fps: nil,
                vcodec: quality.isAudio ? nil : "h264",
                acodec: quality.isAudio ? "mp3" : nil,
                filesizeBytes: nil,
                note: note
            )],
            downloadUrl: tunnelURL.absoluteString
        )
    }
}
