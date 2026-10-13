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
    /// The activities the last sync added, newest first.
    @Published private(set) var added: [AddedActivity] = []
    /// When the last sync ended, so screens can refresh and show what it brought in.
    @Published private(set) var finishedAt: Date?
    /// The last sync started on its own when the app opened, rather than from the button.
    @Published private(set) var automatic = false

    private var link: WatchLink?
    private static let lastAttemptKey = "watch.lastAttempt"
    /// Opening the app syncs when the last clean sync is older than this...
    static let autoSyncAfter: TimeInterval = 3600
    /// ...and no sync was tried in the last few minutes (a failing watch isn't retried on every open).
    static let autoRetryAfter: TimeInterval = 15 * 60

    struct Imported: Decodable {
        let files: [String]
        let lastSync: String?
        /// [start, end] in UTC of each recent activity Talus has from any source.
        let taken: [[String]]?

        var spans: [ClosedRange<Date>] {
            let f = ISO8601DateFormatter()
            return (taken ?? []).compactMap { pair in
                guard pair.count == 2, let a = f.date(from: pair[0]), let b = f.date(from: pair[1]), a <= b else { return nil }
                return a...b
            }
        }
    }

    /// Syncs when the app opens, if this account syncs from the watch and the last sync is old.
    func syncIfDue(_ api: API) async {
        guard !running else { return }
        if let last = UserDefaults.standard.object(forKey: Self.lastAttemptKey) as? Date,
           Date.now.timeIntervalSince(last) < Self.autoRetryAfter { return }
        guard let s = try? await api.get("/api/settings", as: SettingsData.self), s.syncMethod == "watch" else { return }
        if let at = s.sources.watch?.lastSync.flatMap(Self.date), Date.now.timeIntervalSince(at) < Self.autoSyncAfter { return }
        await sync(api, automatic: true)
    }

    func sync(_ api: API, automatic: Bool = false) async {
        guard !running else { return }
        running = true
        self.automatic = automatic
        result = nil
        progress = nil
        self.added = []
        log = []
        UserDefaults.standard.set(Date.now, forKey: Self.lastAttemptKey)
        defer { running = false; phase = nil; progress = nil; finishedAt = .now }

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
            let todo = Self.select(files, imported: Set(imported.files), lastSync: imported.lastSync.flatMap(Self.date),
                                   taken: imported.spans)
            let already = Self.select(files, imported: Set(imported.files), lastSync: imported.lastSync.flatMap(Self.date)).count - todo.count
            note("Watch has \(files.count) files; \(todo.count) new for Talus" + (already > 0 ? ", \(already) already in Talus from another source" : ""))
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
                    self.added = ((r.added ?? []) + self.added).sorted { ($0.startTime ?? "") > ($1.startTime ?? "") }
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
            await finished(api, new: added, error: failed.isEmpty ? nil : "\(failed.count) of \(todo.count) files failed.")
        } catch {
            result = (false, error.localizedDescription + (added > 0 ? " \(added) new activities were imported before that." : ""))
            note("Stopped: \(error.localizedDescription)")
            await finished(api, new: added, error: error.localizedDescription)
        }
    }

    /// Tells the engine how the sync went: "last sync" and the status page count a clean sync with
    /// nothing new, and show the error of a failed one.
    private func finished(_ api: API, new: Int, error: String?) async {
        struct Done: Encodable { let new: Int; let error: String? }
        _ = try? await api.post("/api/import/watch/done", Done(new: new, error: error.map { String($0.prefix(500)) }))
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

    /// Pulls the newest file of every kind except activities, saves it under Documents/watch-probe
    /// and prints which FIT messages it holds, to find where a watch keeps sleep and HRV.
    func probe(skipping skip: Set<String> = ["FIT_TYPE_4"]) async {
        let link = WatchLink()
        link.onEvent = { [weak self] in self?.note($0) }
        defer { link.disconnect() }
        let dir = URL.documentsDirectory.appending(path: "watch-probe")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        do {
            _ = try await link.connect()
            let files = try await link.listFiles()
            let newest = Dictionary(grouping: files.filter { $0.size > 0 && !skip.contains($0.kind) }, by: \.kind)
                .compactMap { $0.value.max { $0.timestamp < $1.timestamp } }
                .sorted { $0.size < $1.size }
            note("Probing \(newest.count) kinds")
            for f in newest {
                do {
                    let z = try await link.download(f) { _ in }
                    let data = (try? GarminWire.inflate(z, expectedSize: f.size)) ?? z
                    try? data.write(to: dir.appending(path: f.fileName))
                    let msgs = GarminWire.isFit(data) ? Self.fitMessages(data).map { "\($0.key):\($0.value)" }.joined(separator: " ") : "not FIT"
                    note("PROBE \(f.kind) \(f.date.map { $0.formatted(.iso8601) } ?? "-") \(data.count)B msgs[\(msgs)]")
                } catch let e as WatchLink.LinkError {
                    switch e {
                    case .refused, .timeout: note("PROBE \(f.kind) \(f.size)B failed: \(e.localizedDescription)"); continue
                    default: throw e
                    }
                }
            }
            note("Probe done")
        } catch {
            note("Probe stopped: \(error.localizedDescription)")
        }
    }

    /// Global FIT message numbers in a file and how many data records each has.
    static func fitMessages(_ d: Data) -> [Int: Int] {
        let b = [UInt8](d)
        guard b.count > 14 else { return [:] }
        let headerSize = Int(b[0])
        let end = min(b.count, headerSize + (Int(b[4]) | Int(b[5]) << 8 | Int(b[6]) << 16 | Int(b[7]) << 24))
        var p = headerSize
        var defs: [Int: (global: Int, size: Int)] = [:]
        var counts: [Int: Int] = [:]
        while p < end {
            let h = Int(b[p]); p += 1
            if h & 0x80 != 0 { // compressed timestamp data message
                guard let def = defs[(h >> 5) & 0x03] else { break }
                counts[def.global, default: 0] += 1; p += def.size
            } else if h & 0x40 != 0 { // definition
                guard p + 5 <= end else { break }
                let big = b[p + 1] == 1
                let global = big ? Int(b[p + 2]) << 8 | Int(b[p + 3]) : Int(b[p + 2]) | Int(b[p + 3]) << 8
                let n = Int(b[p + 4]); p += 5
                guard p + n * 3 <= end else { break }
                var size = 0
                for i in 0..<n { size += Int(b[p + i * 3 + 1]) }
                p += n * 3
                if h & 0x20 != 0, p < end {
                    let nd = Int(b[p]); p += 1
                    guard p + nd * 3 <= end else { break }
                    for i in 0..<nd { size += Int(b[p + i * 3 + 1]) }
                    p += nd * 3
                }
                defs[h & 0x0F] = (global, size)
            } else {
                guard let def = defs[h & 0x0F] else { break }
                counts[def.global, default: 0] += 1; p += def.size
            }
        }
        return counts
    }
    #endif

    /// Same rule as the web app: health files whenever not yet imported; activities not yet
    /// imported from the last two weeks, or from two days before the last sync if that is earlier,
    /// so a sync cut short still picks up the rest. Activities first, newest first.
    /// The new activity files worth downloading. One that falls inside an activity Talus already
    /// has from another source (intervals.icu, Apple Health) is skipped: the watch file's time is
    /// near the run's start or end, so the window allows a few minutes either side.
    static func select(_ files: [WatchFile], imported: Set<String>, lastSync: Date?, taken: [ClosedRange<Date>] = [],
                       now: Date = .now) -> [WatchFile] {
        let fresh = files.filter { $0.size > 0 && !imported.contains($0.key) }
        let twoWeeks = now.addingTimeInterval(-Double(firstSyncDays) * 86400)
        let cutoff = min(lastSync.map { $0.addingTimeInterval(-2 * 86400) } ?? twoWeeks, twoWeeks)
        let windows = taken.map { $0.lowerBound.addingTimeInterval(-5 * 60)...$0.upperBound.addingTimeInterval(15 * 60) }
        let activities = fresh.filter { f in
            guard f.kind == activityKind, let d = f.date, d > cutoff else { return false }
            return !windows.contains { $0.contains(d) }
        }
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
