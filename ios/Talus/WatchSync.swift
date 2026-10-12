import Foundation

/// Pulls new files straight off a Garmin watch over Bluetooth and uploads each one to the engine's
/// `POST /api/import`, tagged with its key so neither this app nor the web app pulls it twice.
@MainActor
final class WatchSync: ObservableObject {
    /// FIT file types Talus reads: activities, then monitoring, sleep, HRV status and metrics.
    static let activityKind = "FIT_TYPE_4"
    static let healthKinds: Set<String> = ["FIT_TYPE_32", "FIT_TYPE_49", "FIT_TYPE_68", "FIT_TYPE_44"]
    static let firstSyncDays = 14

    struct Progress: Equatable {
        var index: Int
        var count: Int
        var kind: String
        var received: Int
        var size: Int
    }

    @Published private(set) var running = false
    @Published private(set) var phase: String?
    @Published private(set) var progress: Progress?
    @Published private(set) var result: (ok: Bool, text: String)?
    @Published private(set) var log: [String] = []

    private var link: WatchLink?

    struct Imported: Decodable { let files: [String]; let lastSync: String? }

    func sync(_ api: API) async {
        guard !running else { return }
        running = true
        result = nil
        progress = nil
        log = []
        defer { running = false; phase = nil; progress = nil }

        let link = WatchLink()
        self.link = link
        link.onEvent = { [weak self] in self?.note($0) }
        defer { link.disconnect(); self.link = nil }

        var added = 0, failed: [String] = []
        do {
            phase = "Checking what Talus already has"
            let imported = try await api.get("/api/import/watch", as: Imported.self)

            phase = "Connecting to the watch"
            let info = try await link.connect()
            note("Connected to \(info.name) \(info.model) (software \(info.softwareText))\(link.sharedLink ? ", sharing Garmin Connect's link" : ", found by scanning")")

            phase = "Listing files on the watch"
            let files = try await link.listFiles()
            let todo = Self.select(files, imported: Set(imported.files), lastSync: imported.lastSync.flatMap(Self.date))
            note("Watch has \(files.count) files; \(todo.count) new for Talus")
            phase = todo.isEmpty ? nil : "Downloading"

            for (i, f) in todo.enumerated() {
                progress = Progress(index: i + 1, count: todo.count, kind: Self.label(f.kind), received: 0, size: f.size)
                do {
                    let z = try await link.download(f) { [weak self] n in self?.progress?.received = n }
                    let fit = try GarminWire.inflate(z, expectedSize: f.size)
                    guard GarminWire.isFit(fit) else { throw GarminWire.InflateError.corrupt }
                    phase = "Importing"
                    let r = try await upload(api, f, fit)
                    phase = "Downloading"
                    added += r.new
                    note("\(f.fileName): \(SettingsText.importLine(r))")
                    if let e = r.error { failed.append("\(f.fileName): \(e)") }
                } catch let e as WatchLink.LinkError {
                    if case .refused = e { failed.append("\(f.fileName): \(e.localizedDescription)"); continue }
                    throw e
                } catch {
                    failed.append("\(f.fileName): \(error.localizedDescription)")
                    note("\(f.fileName) failed: \(error.localizedDescription)")
                }
            }
            let done = todo.count - failed.count
            var text = todo.isEmpty ? "Up to date, nothing new on the watch." :
                "Pulled \(done) \(done == 1 ? "file" : "files") off the watch, \(added) new \(added == 1 ? "activity" : "activities")."
            if !failed.isEmpty { text += " \(failed.count) failed: \(failed.joined(separator: "; "))" }
            result = (failed.isEmpty, text)
        } catch {
            result = (false, error.localizedDescription + (added > 0 ? " \(added) new activities were imported before that." : ""))
            note("Stopped: \(error.localizedDescription)")
        }
    }

    private func upload(_ api: API, _ f: WatchFile, _ fit: Data) async throws -> LastImport {
        var q = URLComponents()
        q.queryItems = [URLQueryItem(name: "name", value: f.fileName), URLQueryItem(name: "watch_file", value: f.key)]
        let data = try await api.upload("/api/import?\(q.percentEncodedQuery ?? "")", file: fit, contentType: "application/octet-stream")
        return try api.decode(data, as: LastImport.self)
    }

    private func note(_ s: String) {
        log.append(s)
        #if DEBUG
        print("[watch] \(s)")
        #endif
    }

    #if DEBUG
    /// Connects, lists and downloads up to `limit` of the files a first sync would pick, checking each
    /// inflates to a FIT file. Uploads nothing and needs no engine.
    func dryRun(limit: Int) async {
        let link = WatchLink()
        link.onEvent = { [weak self] in self?.note($0) }
        defer { link.disconnect() }
        let start = Date.now
        do {
            let info = try await link.connect()
            note("Connected to \(info.name) \(info.model) (software \(info.softwareText), product \(info.product))\(link.sharedLink ? ", sharing the phone's existing link" : ", found by scanning") in \(Int(Date.now.timeIntervalSince(start)))s")
            let files = try await link.listFiles()
            let kinds = Dictionary(grouping: files, by: \.kind).map { "\($0.key)×\($0.value.count)" }.sorted()
            note("Listed \(files.count) files: \(kinds.joined(separator: ", "))")
            let todo = Array(Self.select(files, imported: [], lastSync: nil).prefix(limit))
            note("First sync would pick \(Self.select(files, imported: [], lastSync: nil).count); trying \(todo.count)")
            for f in todo {
                let t = Date.now
                let z = try await link.download(f) { _ in }
                let fit = try GarminWire.inflate(z, expectedSize: f.size)
                let secs = max(Date.now.timeIntervalSince(t), 0.001)
                note("\(f.fileName): \(z.count) B compressed -> \(fit.count) B (listed \(f.size)), FIT=\(GarminWire.isFit(fit)), \(Int(secs))s, \(Int(Double(z.count) / secs / 1024)) KB/s")
            }
            note("Dry run done")
        } catch {
            note("Dry run stopped: \(error.localizedDescription)")
        }
    }
    #endif

    /// Same rule as the web app: health files whenever not yet imported; activities not yet
    /// imported and newer than two days before the last sync (the last two weeks on the first).
    /// Activities first, newest first.
    static func select(_ files: [WatchFile], imported: Set<String>, lastSync: Date?, now: Date = .now) -> [WatchFile] {
        let fresh = files.filter { $0.size > 0 && !imported.contains($0.key) }
        let cutoff = lastSync.map { $0.addingTimeInterval(-2 * 86400) } ?? now.addingTimeInterval(-Double(firstSyncDays) * 86400)
        let activities = fresh.filter { $0.kind == activityKind && ($0.date ?? .distantPast) > cutoff }
        let health = fresh.filter { healthKinds.contains($0.kind) }
        return activities.sorted { $0.timestamp > $1.timestamp } + health.sorted { $0.timestamp > $1.timestamp }
    }

    static func label(_ kind: String) -> String {
        switch kind {
        case "FIT_TYPE_4": "activity"
        case "FIT_TYPE_32": "monitoring"
        case "FIT_TYPE_49": "sleep"
        case "FIT_TYPE_68": "HRV status"
        case "FIT_TYPE_44": "metrics"
        default: kind
        }
    }

    static func date(_ iso: String) -> Date? {
        let f = ISO8601DateFormatter()
        if let d = f.date(from: iso) { return d }
        let local = DateFormatter()
        local.locale = Locale(identifier: "en_US_POSIX")
        local.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return local.date(from: String(iso.prefix(19)))
    }
}
