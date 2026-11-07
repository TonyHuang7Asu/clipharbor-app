import Foundation

/// cobalt 适配器：通过电脑上自建的 cobalt API 解析视频站点（含 YouTube）。
///
/// 诚实标注（不伪装本地能力）：
/// - 解析发生在电脑端（cobalt 11.7.1，已实测可解析 YouTube）；iPhone 负责真实下载
///   （原生后台 URLSession、真实速度统计）。
/// - 需要电脑在线且与手机同一局域网；电脑离线时本适配器不可用，YouTube 回到"未支持"。
/// - cobalt 返回的隧道链接有时效（约 10 分钟），适合"添加即下载"的流程。
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

    struct CobaltResponse: Codable {
        let status: String
        let url: String?
        let filename: String?
        struct ErrorBody: Codable { let code: String? }
        let error: ErrorBody?
    }

    func resolve(_ url: URL) async throws -> MediaInfo {
        var request = URLRequest(url: baseURL)
        request.httpMethod = "POST"
        request.timeoutInterval = 30
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try JSONSerialization.data(
            withJSONObject: ["url": url.absoluteString, "videoQuality": "1080"]
        )
        let (data, response) = try await URLSession.shared.data(from: request)
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
        return MediaInfo(
            sourceUrl: tunnelURL.absoluteString,
            sourceId: sourceId,
            title: resp.filename ?? url.lastPathComponent,
            uploader: nil,                       // cobalt 隧道响应不含上传者，显示"未知"
            durationSeconds: nil,
            thumbnailUrl: nil,
            isPlaylist: false,
            entries: [],
            formats: [MediaInfo.FormatInfo(
                formatId: "cobalt-1080", container: "mp4",
                resolution: "1080p", height: 1080, fps: nil,
                vcodec: nil, acodec: nil, filesizeBytes: nil,
                note: "由电脑 cobalt 解析（需电脑在线）；隧道链接约 10 分钟有效，请尽快下载"
            )]
        )
    }
}
