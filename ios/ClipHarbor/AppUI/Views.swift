import SwiftUI
import UniformTypeIdentifiers

struct RootView: View {
    var body: some View {
        TabView {
            HomeView()
                .tabItem { Label("下载", systemImage: "arrow.down.circle") }
            QueueView()
                .tabItem { Label("队列", systemImage: "list.stack") }
            HistoryView()
                .tabItem { Label("历史", systemImage: "clock.arrow.circlepath") }
            SettingsView()
                .tabItem { Label("设置", systemImage: "gearshape") }
        }
    }
}

/// 下载首页：粘贴链接 → 选清晰度 → 解析 → 加入下载（自动排队，并发 2 依次下载）。
struct HomeView: View {
    @ObservedObject private var engine = BackgroundDownloadEngine.shared
    @State private var urlString = ""
    @State private var errorMessage: String?
    @State private var isWorking = false
    @AppStorage(DownloadQuality.storageKey) private var qualityRaw = DownloadQuality.best.rawValue

    private var quality: DownloadQuality { DownloadQuality(rawValue: qualityRaw) ?? .best }

    var body: some View {
        NavigationStack {
            List {
                Section("添加链接") {
                    HStack(spacing: 8) {
                        TextField("粘贴 YouTube / B站 / 视频直链", text: $urlString)
                            .keyboardType(.URL)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                        if !urlString.isEmpty {
                            Button {
                                urlString = ""
                            } label: {
                                Image(systemName: "xmark.circle.fill")
                                    .foregroundStyle(.secondary)
                            }
                            .buttonStyle(.borderless)
                        }
                        Button {
                            if let text = UIPasteboard.general.string {
                                urlString = text.trimmingCharacters(in: .whitespacesAndNewlines)
                            }
                        } label: {
                            Text("粘贴")
                        }
                        .buttonStyle(.borderless)
                    }
                    Picker("清晰度", selection: $qualityRaw) {
                        ForEach(DownloadQuality.allCases) { q in
                            Text(q.shortLabel).tag(q.rawValue)
                        }
                    }
                    .pickerStyle(.segmented)
                    Button {
                        start()
                    } label: {
                        if isWorking {
                            ProgressView()
                        } else {
                            Label("解析并加入下载（\(quality.label)）", systemImage: "arrow.down.to.line.compact")
                        }
                    }
                    .disabled(urlString.isEmpty || isWorking)
                    if let errorMessage {
                        Text(errorMessage)
                            .font(.footnote)
                            .foregroundStyle(.red)
                    }
                }
                Section("进行中") {
                    let tasks = engine.activeTasks().filter { $0.status != .queued || engine.liveProgress[$0.id] != nil }
                    if tasks.isEmpty {
                        Text("暂无活动任务")
                            .foregroundStyle(.secondary)
                    }
                    ForEach(tasks) { task in
                        TaskCardView(task: task, progress: engine.liveProgress[task.id])
                    }
                }
            }
            .navigationTitle("ClipHarbor")
        }
    }

    private func start() {
        isWorking = true
        errorMessage = nil
        let input = urlString
        Task { @MainActor in
            do {
                _ = try await BackgroundDownloadEngine.shared.addTask(urlString: input)
                urlString = ""   // 解析成功自动清空，方便连续粘贴（仿 x-video-downloader）
            } catch {
                errorMessage = error.localizedDescription
            }
            isWorking = false
        }
    }
}

/// 任务卡片：缩略图、标题、进度条、真实速度、剩余时间、状态、暂停/继续。
struct TaskCardView: View {
    let task: DownloadTask
    let progress: DownloadProgress?
    @State private var tick = 0

    private var bytesDownloaded: Int64 {
        max(task.bytesDownloaded, Int64(progress?.bytesDownloaded ?? 0))
    }
    private var fraction: Double {
        guard task.bytesTotal > 0 else { return 0 }
        return min(1, Double(bytesDownloaded) / Double(task.bytesTotal))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                ThumbnailView(url: task.thumbnailURL)
                VStack(alignment: .leading, spacing: 2) {
                    Text(task.title.isEmpty ? task.filename : task.title)
                        .font(.subheadline.weight(.medium))
                        .lineLimit(2)
                    Text(statusText)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                controlButton
            }
            ProgressView(value: fraction)
            HStack {
                Text("\(formatBytes(bytesDownloaded)) / \(task.bytesTotal > 0 ? formatBytes(task.bytesTotal) : "未知")")
                Spacer()
                Text("速度 \(formatSpeed(progress?.speedBps ?? 0))")
                Spacer()
                Text("剩余 \(formatETA(progress?.etaSeconds))")
            }
            .font(.caption2)
            .foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
        .onReceive(Timer.publish(every: 0.5, on: .main, in: .common).autoconnect()) { _ in
            tick += 1  // 驱动快照刷新
        }
        .id(tick % 2 == 0 ? "a" : "b") // 轻量刷新
    }

    private var statusText: String {
        if let p = progress, task.status == .downloading, p.speedBps > 0 {
            return "下载中 · 峰值 \(formatSpeed(p.peakSpeedBps))"
        }
        switch task.status {
        case .queued: return "排队中"
        case .downloading: return "下载中"
        case .paused: return task.error.isEmpty ? "已暂停" : "已暂停（\(task.error)）"
        case .retryWait: return "等待重试（\(task.error)）"
        case .completed: return "已完成 ✓"
        case .failed: return "失败（\(task.error)）"
        case .cancelled: return "已取消"
        case .merging: return "合并中"
        case .zipping: return "打包中"
        }
    }

    @ViewBuilder private var controlButton: some View {
        switch task.status {
        case .downloading, .queued:
            Button { BackgroundDownloadEngine.shared.pause(task.id) } label: { Image(systemName: "pause.circle.fill").font(.title2) }
                .buttonStyle(.borderless)
        case .paused, .failed:
            Button { BackgroundDownloadEngine.shared.resume(task.id) } label: { Image(systemName: "play.circle.fill").font(.title2) }
                .buttonStyle(.borderless)
        default:
            EmptyView()
        }
    }
}

/// 缩略图（YouTube 由视频 ID 直拼 i.ytimg.com；加载失败显示占位图标）。
struct ThumbnailView: View {
    let url: String?

    var body: some View {
        Group {
            if let url, let remote = URL(string: url) {
                AsyncImage(url: remote) { phase in
                    switch phase {
                    case .success(let image):
                        image.resizable().scaledToFill()
                    default:
                        placeholder
                    }
                }
            } else {
                placeholder
            }
        }
        .frame(width: 64, height: 40)
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 6))
    }

    private var placeholder: some View {
        Image(systemName: "play.square.stack")
            .font(.title3)
            .frame(width: 64, height: 40)
            .foregroundStyle(.secondary)
    }
}

/// 下载队列页：滑动可取消/重试。
struct QueueView: View {
    @ObservedObject private var engine = BackgroundDownloadEngine.shared
    @State private var tick = 0

    var body: some View {
        NavigationStack {
            List {
                if engine.activeTasks().isEmpty {
                    Text("队列为空")
                        .foregroundStyle(.secondary)
                }
                ForEach(engine.activeTasks()) { task in
                    TaskCardView(task: task, progress: engine.liveProgress[task.id])
                        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                            Button(role: .destructive) {
                                BackgroundDownloadEngine.shared.cancel(task.id)
                            } label: { Label("取消", systemImage: "xmark") }
                            if task.status.isTerminal || task.status == .failed {
                                Button {
                                    BackgroundDownloadEngine.shared.retry(task.id)
                                } label: { Label("重试", systemImage: "arrow.clockwise") }
                                .tint(.blue)
                            }
                        }
                }
            }
            .navigationTitle("队列")
            .onReceive(Timer.publish(every: 1, on: .main, in: .common).autoconnect()) { _ in tick += 1 }
        }
    }
}

/// 历史记录页：缩略图 + 搜索 + 文件存在性 + 存相册/复制链接/分享文件。
struct HistoryView: View {
    @ObservedObject private var engine = BackgroundDownloadEngine.shared
    @State private var query = ""
    @State private var photoMessage: String?
    @State private var showPhotoAlert = false

    var body: some View {
        NavigationStack {
            List {
                ForEach(engine.searchHistory(query)) { task in
                    HistoryRowView(task: task, fileExists: engine.fileExists(task))
                        .contextMenu {
                            if task.status == .completed, engine.fileExists(task),
                               PhotoSaver.isVideo(path: task.filePath) {
                                Button {
                                    saveToPhotos(task)
                                } label: {
                                    Label("存到相册", systemImage: "photo.badge.arrow.down")
                                }
                            }
                            Button {
                                UIPasteboard.general.string = task.sourceURL
                            } label: {
                                Label("复制源链接", systemImage: "doc.on.doc")
                            }
                            if task.status == .completed, engine.fileExists(task) {
                                ShareLink(item: URL(fileURLWithPath: task.filePath)) {
                                    Label("分享 / 存到文件 App", systemImage: "square.and.arrow.up")
                                }
                            }
                            if task.status.isTerminal {
                                Button {
                                    BackgroundDownloadEngine.shared.retry(task.id)
                                } label: {
                                    Label("重新下载（重试）", systemImage: "arrow.clockwise")
                                }
                            }
                        }
                }
            }
            .searchable(text: $query, prompt: "搜索标题 / URL / 文件名")
            .navigationTitle("历史")
            .refreshable { _ = engine.historyRecords(query: query) }
            .alert("存到相册", isPresented: $showPhotoAlert) {
                Button("好", role: .cancel) {}
            } message: {
                Text(photoMessage ?? "")
            }
        }
    }

    private func saveToPhotos(_ task: DownloadTask) {
        Task { @MainActor in
            do {
                photoMessage = try await PhotoSaver.saveVideo(at: task.filePath)
            } catch {
                photoMessage = "失败：\(error.localizedDescription)"
            }
            showPhotoAlert = true
        }
    }
}

/// 历史行：缩略图、标题、大小、时间、平均速度、文件状态。
struct HistoryRowView: View {
    let task: DownloadTask
    let fileExists: Bool

    var body: some View {
        HStack(spacing: 10) {
            ThumbnailView(url: task.thumbnailURL)
            VStack(alignment: .leading, spacing: 3) {
                Text(task.title.isEmpty ? task.filename : task.title)
                    .font(.subheadline)
                    .lineLimit(2)
                HStack(spacing: 4) {
                    Text(task.status == .completed ? "✓ \(formatBytes(task.bytesTotal))" : task.status.rawValue)
                    if let done = task.completedAt {
                        Text("·")
                        Text(done.formatted(date: .abbreviated, time: .shortened))
                    }
                    if task.avgSpeedBps > 0 {
                        Text("·")
                        Text("平均 \(formatSpeed(task.avgSpeedBps))")
                    }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
                HStack(spacing: 4) {
                    Image(systemName: fileExists ? "checkmark.seal" : "questionmark.folder")
                    Text(fileExists ? "文件存在" : "文件缺失")
                }
                .font(.caption2)
                .foregroundStyle(fileExists ? Color.secondary : Color.orange)
            }
            Spacer()
        }
        .padding(.vertical, 2)
    }
}

/// 设置页：cobalt 地址（YouTube 解析）、连接测试、历史备份，其余功能随阶段开放。
struct SettingsView: View {
    @AppStorage(CobaltAdapter.storageKey) private var cobaltURL: String = CobaltAdapter.defaultURL
    @State private var exportDoc = ClipHarborJSONDocument(data: Data())
    @State private var showExporter = false
    @State private var showImporter = false
    @State private var message: String?
    @State private var isTesting = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("http://192.168.0.105:9000", text: $cobaltURL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Button {
                        testConnection()
                    } label: {
                        if isTesting {
                            ProgressView()
                        } else {
                            Label("测试连接", systemImage: "dot.radiowaves.left.and.right")
                        }
                    }
                    .disabled(isTesting || cobaltURL.isEmpty)
                    Text("填入电脑上 cobalt 服务的地址后，App 内粘贴 YouTube/B站 链接即可下载"
                         + "（解析在电脑端完成，手机负责真实下载；电脑需在线且同一局域网）。"
                         + "留空 = YouTube 视为未支持。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                } header: {
                    Text("YouTube 解析（cobalt · 电脑端）")
                }
                Section("历史备份（JSON）") {
                    Button("导出历史到文件 App / iCloud Drive") {
                        exportDoc = ClipHarborJSONDocument(data: (try? BackgroundDownloadEngine.shared.exportHistoryJSON()) ?? Data())
                        showExporter = true
                    }
                    Button("从 JSON 文件导入历史") { showImporter = true }
                    if let message { Text(message).font(.footnote).foregroundStyle(.secondary) }
                }
                Section("关于") {
                    LabeledContent("协议版本", value: "clipharbor 1.0")
                    LabeledContent("独立模式", value: "直链本地引擎已启用")
                    LabeledContent("YouTube 解析", value: cobaltURL.isEmpty ? "未配置（未支持）" : "经电脑 cobalt")
                    LabeledContent("并发下载", value: "\(BackgroundDownloadEngine.maxConcurrentDownloads) 个任务")
                }
            }
            .navigationTitle("设置")
            .fileExporter(isPresented: $showExporter, document: exportDoc,
                          contentType: .json, defaultFilename: "clipharbor-history.json") { result in
                message = (try? result.get()) != nil ? "已导出" : "导出已取消或失败"
            }
            .fileImporter(isPresented: $showImporter, allowedContentTypes: [.json]) { result in
                switch result {
                case .success(let url):
                    guard url.startAccessingSecurityScopedResource() else { return }
                    defer { url.stopAccessingSecurityScopedResource() }
                    if let data = try? Data(contentsOf: url),
                       let (imported, skipped) = try? BackgroundDownloadEngine.shared.importHistoryJSON(data) {
                        message = "导入 \(imported) 条，跳过 \(skipped) 条"
                    } else {
                        message = "导入失败：无法识别的文件"
                    }
                case .failure: message = "导入已取消"
                }
            }
        }
    }

    /// 探测 cobalt 服务可达性（GET 根路径，短超时）。
    private func testConnection() {
        isTesting = true
        message = nil
        let raw = cobaltURL.trimmingCharacters(in: .whitespacesAndNewlines)
        Task { @MainActor in
            defer { isTesting = false }
            guard let url = URL(string: raw) else {
                message = "地址格式不正确"
                return
            }
            var request = URLRequest(url: url)
            request.timeoutInterval = 8
            do {
                let (_, response) = try await URLSession.shared.data(for: request)
                if let http = response as? HTTPURLResponse {
                    message = "连接成功：HTTP \(http.statusCode)（服务在线）"
                } else {
                    message = "连接成功（非 HTTP 响应）"
                }
            } catch {
                message = "连接失败：\(error.localizedDescription)。请确认电脑在线、地址正确、手机与其同一局域网。"
            }
        }
    }
}

/// 导出文档包装。
struct ClipHarborJSONDocument: FileDocument {
    static let readableContentTypes: [UTType] = [.json]
    var data: Data

    init(data: Data) { self.data = data }
    init(configuration: ReadConfiguration) throws {
        data = configuration.file.regularFileContents ?? Data()
    }
    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper {
        FileWrapper(regularFileWithContents: data)
    }
}
