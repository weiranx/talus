import SwiftUI

// Is Talus working (GET /api/status, web/app/status/page.tsx): the engine and this app, each data
// source's last sync, the activity read-ahead and the coach, for the signed-in account. The admin
// also sees the server. Each check is ok, warn (working, worth a look), bad or idle.

struct StatusOverview: Codable, Hashable {
    struct Engine: Codable, Hashable {
        let ok: Bool
        let revision: String?
        let modelVersion: String
        let startedAt: String
        let uptimeS: Double
    }
    struct Source: Codable, Hashable {
        let key: String
        let label: String
        let state: String
        let connected: Bool?
        let at: String?
        let error: String?
        let saved: Int?
        let how: String?
    }
    struct DataInfo: Codable, Hashable { let activities: Int; let newestActivity: String?; let newestNight: String? }
    struct ReadAhead: Codable, Hashable { let state: String; let ready: Int; let total: Int; let running: Bool; let waitingFor: String? }
    struct Importing: Codable, Hashable { let state: String; let done: Int; let total: Int? }
    struct Coach: Codable, Hashable {
        struct Call: Codable, Hashable { let at: String; let mode: String; let model: String?; let ok: Bool; let fallbackReason: String? }
        let state: String
        let why: String?
        let enabled: Bool
        let keySet: Bool
        let limit: Double?
        let ceiling: Double?
        let spent: Double
        let month: String
        let lastCall: Call?
        let errors24h: Int
        let lastErrorAt: String?

        // convertFromSnakeCase reads errors_24h as errors24H.
        enum CodingKeys: String, CodingKey {
            case state, why, enabled, keySet, limit, ceiling, spent, month, lastCall, lastErrorAt
            case errors24h = "errors24H"
        }
    }
    struct Server: Codable, Hashable {
        struct Failing: Codable, Hashable { let email: String; let source: String; let at: String }
        struct SignIn: Codable, Hashable { let google: Bool }
        struct OAuth: Codable, Hashable { let enabled: Bool; let connected: Int }
        let revision: String?
        let modelVersion: String
        let coach: AdminOverview.System.Coach
        let serverCeilingUsd: Double
        let dataBytes: Int
        let disk: AdminOverview.System.Disk
        let diskState: String
        let accounts: Int
        let active7d: Int
        let failingSyncs: [Failing]
        let coachSpent: Double
        let month: String
        let signupOpen: Bool
        let signIn: SignIn
        let intervalsOauth: OAuth

        enum CodingKeys: String, CodingKey {
            case revision, modelVersion, coach, serverCeilingUsd, dataBytes, disk, diskState, accounts, failingSyncs,
                 coachSpent, month, signupOpen, signIn, intervalsOauth
            case active7d = "active7D"
        }
    }
    let engine: Engine
    let sources: [Source]
    let data: DataInfo
    let readAhead: ReadAhead
    let importing: Importing?
    let coach: Coach
    let server: Server?

    /// Every check's state, for the headline: the sources, the coach, and for the admin the disk and failed syncs.
    var states: [String] {
        sources.map(\.state) + [coach.state] + (server.map { [$0.diskState] } ?? []) + ((server?.failingSyncs.isEmpty ?? true) ? [] : ["warn"])
    }

    /// Something is moving (a history import, the activity read-ahead), so the page reads again on its own.
    var live: Bool { importing?.state == "running" || readAhead.running }
}

enum StatusText {
    static func tone(_ state: String) -> Tone {
        switch state {
        case "ok": .good
        case "warn": .warn
        case "bad": .bad
        default: .neutral
        }
    }

    static func word(_ state: String) -> String {
        switch state {
        case "ok": "Working"
        case "warn": "Check"
        case "bad": "Not working"
        default: "Not set up"
        }
    }

    static func summary(_ states: [String]) -> (state: String, text: String) {
        let bad = states.filter { $0 == "bad" }.count
        let warn = states.filter { $0 == "warn" }.count
        if bad > 0 { return ("bad", "\(bad) not working") }
        if warn > 0 { return ("warn", "\(warn) to check") }
        return ("ok", "All working")
    }

    static func date(_ iso: String) -> Date? {
        let f = ISO8601DateFormatter()
        if let d = f.date(from: iso) { return d }
        let local = DateFormatter()
        local.locale = Locale(identifier: "en_US_POSIX")
        local.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return local.date(from: String(iso.prefix(19)))
    }

    static func ago(_ iso: String?, now: Date = Date()) -> String {
        guard let iso, let d = date(iso) else { return "Never" }
        let mins = Int((now.timeIntervalSince(d) / 60).rounded())
        if mins < 2 { return "Just now" }
        if mins < 60 { return "\(mins) min ago" }
        if mins < 48 * 60 { return "\(Int((Double(mins) / 60).rounded())) h ago" }
        return "\(Int((Double(mins) / 1440).rounded())) days ago"
    }

    static func uptime(_ s: Double) -> String {
        if s < 3600 { return "\(max(1, Int((s / 60).rounded()))) min" }
        if s < 2 * 86400 { return "\(Int((s / 3600).rounded())) h" }
        return "\(Int((s / 86400).rounded())) days"
    }

    static func short(_ rev: String?) -> String {
        guard let rev else { return "unknown" }
        return rev.replacingOccurrences(of: #"^([0-9a-f]{7})[0-9a-f]*"#, with: "$1", options: .regularExpression)
    }

    static func day(_ iso: String?) -> String {
        iso.map { Fmt.day($0, .dateTime.month(.abbreviated).day()) } ?? "None yet"
    }

    /// The source's state word, as the web's table says it.
    static func sourceWord(_ r: StatusOverview.Source) -> String {
        if r.error != nil { return "Failed" }
        if r.connected == false { return "Not connected" }
        if r.state == "warn" { return r.at != nil ? "Behind" : "Never synced" }
        return r.at != nil ? "OK" : "Not used yet"
    }

    static func coachWord(_ c: StatusOverview.Coach) -> String {
        switch c.state {
        case "ok": "Writing"
        case "idle": "Off"
        default: c.why == "cap" ? "At cap" : c.state == "bad" ? "Not writing" : "Check"
        }
    }

    static let coachWhy = [
        "off": "The coach is off in Settings, so pages show the rule-based read.",
        "no_key": "No Anthropic key on the server, so pages show the rule-based read.",
        "cap": "This month's spending reached the cap. The coach is back next month, or raise the cap in Settings.",
        "api_error": "The last call to the AI failed, so that page showed the rule-based read. Reloading tries again.",
        "recent_errors": "Working now, but a call failed in the last 24 hours.",
    ]

    /// This iPhone app's version, in place of the web app's commit.
    static var appVersion: String {
        let info = Bundle.main.infoDictionary
        let v = info?["CFBundleShortVersionString"] as? String ?? "unknown"
        return (info?["CFBundleVersion"] as? String).map { "\(v) (\($0))" } ?? v
    }
}

struct StatusPage: View {
    @EnvironmentObject private var api: API

    var body: some View {
        Loader(load: { try await api.get("/api/status", as: StatusOverview.self) }) { s, reload in
            ScrollView {
                VStack(alignment: .leading, spacing: 14) { StatusContent(s: s) }.padding()
            }
            .refreshable { await reload() }
            .toolbar {
                Button("Refresh") { Task { await reload() } }
            }
            .task(id: s.live) {
                // Every 15 seconds while something is moving, so its progress shows.
                while s.live && !Task.isCancelled {
                    try? await Task.sleep(for: .seconds(15))
                    if !Task.isCancelled { await reload() }
                }
            }
        }
        .background(Color(uiColor: .systemGroupedBackground))
        .navigationTitle("Status")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct StatusContent: View {
    let s: StatusOverview

    var body: some View {
        let summary = StatusText.summary(s.states)
        HStack {
            Text("Status").font(.title2.bold())
            Pill(text: summary.text, tone: StatusText.tone(summary.state))
        }
        Card {
            SectionLabel("Talus")
            VStack(alignment: .leading, spacing: 6) {
                LabeledContent("Engine") {
                    HStack(spacing: 6) {
                        Pill(text: "Up", tone: .good)
                        Text("for \(StatusText.uptime(s.engine.uptimeS)), version \(StatusText.short(s.engine.revision))")
                            .multilineTextAlignment(.trailing)
                    }
                }
                LabeledContent("iPhone app", value: StatusText.appVersion)
                LabeledContent("Scoring model", value: s.engine.modelVersion)
            }
            .font(.subheadline)
        }
        sourcesCard
        readAheadCard
        coachCard
        if let server = s.server { ServerCard(server: server) }
    }

    private var sourcesCard: some View {
        Card {
            SectionLabel("Your data sources")
            if s.sources.isEmpty {
                HStack(spacing: 4) {
                    Text("No data source yet.").foregroundStyle(.secondary)
                    NavigationLink("Connect one in Settings") { SourcesPage() }
                }
                .font(.subheadline)
            }
            ForEach(s.sources, id: \.key) { r in
                HStack(alignment: .firstTextBaseline) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(r.label).fontWeight(.semibold)
                        Text([r.how, r.at.map { _ in StatusText.ago(r.at) } ?? "Never", r.saved.map { "\($0) new" }]
                            .compactMap { $0 }.joined(separator: " · "))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Pill(text: StatusText.sourceWord(r), tone: StatusText.tone(r.state))
                }
                .font(.subheadline)
                if r.key != s.sources.last?.key { Divider() }
            }
            ForEach(s.sources.filter { $0.error != nil }, id: \.key) { r in
                FormMessage(ok: false, text: "\(r.label): \(r.error!)")
            }
            if s.sources.contains(where: { $0.state == "warn" && $0.error == nil && $0.connected != false }) {
                Text("A synced source that hasn't run for 3 days is worth a look. Sync from Settings.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if let imp = s.importing, imp.state == "running" {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Importing history: \(imp.done) of \(imp.total.map(String.init) ?? "?") activities").font(.subheadline)
                    ProgressView(value: Double(imp.done), total: Double(max(imp.total ?? 0, imp.done, 1)))
                }
            }
            VStack(alignment: .leading, spacing: 6) {
                LabeledContent("Activities", value: s.data.activities.formatted())
                LabeledContent("Newest activity", value: StatusText.day(s.data.newestActivity))
                LabeledContent("Newest overnight data", value: StatusText.day(s.data.newestNight))
            }
            .font(.subheadline)
        }
    }

    private var readAheadCard: some View {
        Card {
            SectionLabel("Activity pages")
            let r = s.readAhead
            if r.waitingFor == "thresholds" {
                HStack(spacing: 4) {
                    Text("Waiting for heart rate thresholds.").foregroundStyle(.secondary)
                    NavigationLink("Set them in Settings") { HeartRatePage() }
                }
                .font(.subheadline)
            } else if r.total == 0 {
                Text("No activities yet.").font(.subheadline).foregroundStyle(.secondary)
            } else {
                Text("\(r.ready.formatted()) of \(r.total.formatted()) ready to open instantly\(r.running ? ", reading the rest in the background" : "")")
                    .font(.subheadline)
                ProgressView(value: Double(r.ready), total: Double(r.total))
                if r.ready < r.total && !r.running {
                    Text("The rest open a little slower the first time. Reading carries on as you use Talus.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }

    private var coachCard: some View {
        Card {
            HStack {
                SectionLabel("Coach")
                Spacer()
                Pill(text: StatusText.coachWord(s.coach), tone: StatusText.tone(s.coach.state))
            }
            if let why = s.coach.why, let text = StatusText.coachWhy[why] {
                Text(text).font(.caption).foregroundStyle(.secondary)
            }
            VStack(alignment: .leading, spacing: 6) {
                LabeledContent("Last AI call") {
                    if let call = s.coach.lastCall {
                        HStack(spacing: 4) {
                            Text(StatusText.ago(call.at))
                            if !call.ok { Text("(failed)").foregroundStyle(Tone.bad.ink) }
                        }
                    } else {
                        Text("None yet")
                    }
                }
                LabeledContent("Failed, last 24 h", value: "\(s.coach.errors24h)")
                LabeledContent("Spent this month", value: "\(SettingsText.usd(s.coach.spent)) of \(s.coach.limit.map(SettingsText.usd) ?? "no cap")")
            }
            .font(.subheadline)
            if let limit = s.coach.limit, limit > 0 {
                ProgressView(value: min(s.coach.spent, limit), total: limit)
            }
        }
    }
}

private struct ServerCard: View {
    let server: StatusOverview.Server

    var body: some View {
        Card {
            HStack {
                SectionLabel("Server")
                Spacer()
                NavigationLink("Admin") { AdminPage() }.font(.subheadline)
            }
            VStack(alignment: .leading, spacing: 6) {
                LabeledContent("Accounts", value: "\(server.accounts), \(server.active7d) active this week")
                LabeledContent("Failed syncs") {
                    if server.failingSyncs.isEmpty {
                        Text("None")
                    } else {
                        VStack(alignment: .trailing, spacing: 4) {
                            ForEach(server.failingSyncs, id: \.email) { f in
                                HStack(spacing: 6) {
                                    Pill(text: f.source, tone: .warn)
                                    Text("\(f.email), \(StatusText.ago(f.at))").multilineTextAlignment(.trailing)
                                }
                            }
                        }
                    }
                }
                LabeledContent("Coach, all accounts") {
                    HStack(spacing: 4) {
                        Text("\(SettingsText.usd(server.coachSpent)) this month")
                        if !server.coach.keySet { Text("(no Anthropic key)").foregroundStyle(Tone.bad.ink) }
                    }
                }
                LabeledContent("Coach models") {
                    Text("\(server.coach.briefModel), \(server.coach.analysisModel)").multilineTextAlignment(.trailing)
                }
                LabeledContent("Disk") {
                    VStack(alignment: .trailing, spacing: 2) {
                        Pill(text: "\(SettingsText.bytes(server.disk.free)) free", tone: StatusText.tone(server.diskState))
                        Text("of \(SettingsText.bytes(server.disk.total)), Talus data \(SettingsText.bytes(server.dataBytes))")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                LabeledContent("Sign-ups", value: server.signupOpen ? "Open" : "Closed")
                LabeledContent("Google sign-in", value: server.signIn.google ? "On" : "Off")
                LabeledContent("intervals.icu app") {
                    Text(server.intervalsOauth.enabled ? "On, \(server.intervalsOauth.connected) connected"
                         : "Off until intervals.icu approves the app (accounts use API keys meanwhile)")
                        .multilineTextAlignment(.trailing)
                }
            }
            .font(.subheadline)
        }
    }
}
