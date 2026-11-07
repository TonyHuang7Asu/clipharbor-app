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

/// 下载首页：粘贴链接 → 解析 → 开始独立下载。
struct HomeView: View {
    @ObservedObject private var engine = BackgroundDownloadEngine.shared
    @State private var urlString = ""
    @State private var errorMessage: String?
    @State private var isWorking = false

    var body: some View {
        NavigationStack {
            List {
                Section("添加链接") {
                    TextField("https://example.com/video.mp4", text: $urlString)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Button {
                        start()
                    } label: {
                        if isWorking {
                            ProgressView()
                        } else {
                            Label("解析并下载", systemImage: "arrow.down.to.line.compact")
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
                urlString = ""
            } catch {
                errorMessage = error.localizedDescription
            }
            isWorking = false
        }
    }
}

/// 任务卡片：封面位、标题、进度条、真实速度、剩余时间、状态、暂停/继续。
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
                Image(systemName: "play.square.stack")
                    .font(.title3)
                    .frame(width: 44, height: 44)
                    .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
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

/// 下载队列页。
struct QueueView: View {
    @ObservedObject private var engine = BackgroundDownloadEngine.shared
    @State private var tick = 0

    var body: some View {
        NavigationStack {
            List {
                ForEach(engine.activeTasks()) { task in
                    TaskCardView(task: task, progress: engine.liveProgress[task.id])
                        .swipeActions {
                            Button(role: .destructive) {
                                BackgroundDownloadEngine.shared.cancel(task.id)
                            } label: { Label("取消", systemImage: "xmark") }
                        }
                }
            }
            .navigationTitle("队列")
            .onReceive(Timer.publish(every: 1, on: .main, in: .common).autoconnect()) { _ in tick += 1 }
        }
    }
}

/// 历史记录页：搜索 + 文件存在性 + 详情。
struct HistoryView: View {
    @ObservedObject private var engine = BackgroundDownloadEngine.shared
    @State private var query = ""

    var body: some View {
        NavigationStack {
            List {
                ForEach(engine.searchHistory(query)) { task in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(task.title.isEmpty ? task.filename : task.title)
                            .font(.subheadline)
                        HStack {
                            Text(task.status == .completed ? "✓ \(formatBytes(task.bytesTotal))" : task.status.rawValue)
                            Text("·")
                            Text("平均 \(formatSpeed(task.avgSpeedBps))")
                            Text("·")
                            Image(systemName: BackgroundDownloadEngine.shared.fileExists(task) ? "checkmark.seal" : "questionmark.folder")
                            Text(BackgroundDownloadEngine.shared.fileExists(task) ? "文件存在" : "文件缺失")
                        }
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    }
                }
            }
            .searchable(text: $query, prompt: "搜索标题 / URL / 文件名")
            .navigationTitle("历史")
            .refreshable { _ = engine.historyRecords(query: query) }
        }
    }
}

/// 设置页：cobalt 地址（YouTube 解析）、备份导出/导入，其余功能随阶段开放。
struct SettingsView: View {
    @AppStorage(CobaltAdapter.storageKey) private var cobaltURL: String = CobaltAdapter.defaultURL
    @State private var exportDoc = ClipHarborJSONDocument(data: Data())
    @State private var showExporter = false
    @State private var showImporter = false
    @State private var message: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("http://192.168.0.105:9000", text: $cobaltURL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
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
