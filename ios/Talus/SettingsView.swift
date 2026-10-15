import SwiftUI
import UniformTypeIdentifiers

struct SettingsView: View {
    @EnvironmentObject private var api: API
    @EnvironmentObject private var session: SessionModel
    @AppStorage("appearance") private var appearance = "system"
    @State private var account: Account?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    NavigationLink { SourcesPage() } label: { Label("Data sources", systemImage: "arrow.triangle.2.circlepath") }
                    NavigationLink { HeartRatePage() } label: { Label("Heart rate thresholds and zones", systemImage: "heart") }
                    NavigationLink { CoachPage() } label: { Label("AI coach", systemImage: "text.bubble") }
                    NavigationLink { ReadinessPage() } label: { Label("Readiness", systemImage: "bed.double") }
                    NavigationLink { AccountPage() } label: { Label("Account", systemImage: "person.crop.circle") }
                    if account?.isAdmin == true {
                        NavigationLink { AdminPage() } label: { Label("Admin", systemImage: "lock.shield") }
                    }
                    NavigationLink { StatusPage() } label: { Label("Status", systemImage: "waveform.path.ecg") }
                }

                Section {
                    Picker("Appearance", selection: $appearance) {
                        Text("Light").tag("light")
                        Text("Dark").tag("dark")
                        Text("System").tag("system")
                    }
                    LabeledContent("Distance", value: "Miles")
                    LabeledContent("Vert", value: "Feet, gain and loss")
                } header: {
                    Text("Display")
                } footer: {
                    Text("System follows your phone's light or dark setting. Kilometers and meters are not available yet.")
                }

                Section("Engine") {
                    TextField("Server", text: $api.server)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                        .onSubmit { Task { await session.check(api) } }
                    if case .signedIn(let email?) = session.state {
                        LabeledContent("Signed in as", value: email)
                        Button("Sign out", role: .destructive) {
                            Task {
                                await api.signOut()
                                await session.check(api)
                            }
                        }
                    }
                }
            }
            .navigationTitle("Settings")
            .task { account = try? await api.get("/api/account", as: Account.self) }
        }
    }
}

// MARK: Data sources

struct SourcesPage: View {
    var body: some View {
        SettingsPage(title: "Data sources") { s, reload in
            SyncCard(settings: s, reload: reload)
            AppleHealthCard()
            IntervalsCard(source: s.source, reload: reload)
            if s.sources.garmin.available { GarminCard(garmin: s.sources.garmin, reload: reload) }
            if let coros = s.sources.coros, coros.available { CorosCard(coros: coros, reload: reload) }
            if let strava = s.sources.stravaWeb, strava.available { StravaCard(strava: strava, reload: reload) }
            if s.sources.watch != nil { WatchCard(on: s.watchOn, lastSync: s.sources.watch?.lastSync, reload: reload) }
            UploadCard(upload: s.sources.upload, reload: reload)
        }
    }
}

/// Health setup lives beside the other sources; all manual syncs start from the card above.
private struct AppleHealthCard: View {
    @EnvironmentObject private var api: API
    @EnvironmentObject private var health: HealthSync

    var body: some View {
        Card {
            SectionLabel("Apple Health")
            Text("Workouts, sleep, resting heart rate and HRV from this iPhone.")
                .font(.caption).foregroundStyle(.secondary)
            if health.enabled {
                Toggle("Include Apple Health in sync", isOn: $health.enabled)
                    .disabled(health.running)
                Text("Also checks when Talus opens, at most hourly.")
                    .font(.caption).foregroundStyle(.secondary)
            } else {
                Button { Task { await health.turnOn(api) } } label: {
                    BusyLabel(title: "Connect Apple Health", busy: health.running)
                }
                .buttonStyle(.bordered).disabled(health.running || !health.available)
            }
            if !health.available {
                Text("Apple Health isn't available on this device.").font(.caption).foregroundStyle(.secondary)
            }
            if let status = health.status, !health.running {
                Text(status).font(.caption).foregroundStyle(health.failed ? Tone.bad.ink : .secondary)
            }
            DisclosureGroup("What syncs from Health?") {
                Text("Talus reads workouts with route and heart rate, plus HRV, resting heart rate and sleep. The first sync checks the last \(HealthSync.firstSyncDays) days; later syncs recheck recent data. Apple Watch data is here, along with data other apps write to Health.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            .font(.caption)
        }
    }
}

/// One sync for everything: the sources the engine pulls (intervals.icu, Garmin Connect, COROS,
/// Strava), then the watch and Apple Health, which only this phone can reach.
private struct SyncCard: View {
    let settings: SettingsData
    let reload: () async -> Void
    @EnvironmentObject private var api: API
    @EnvironmentObject private var watch: WatchSync
    @EnvironmentObject private var health: HealthSync
    @State private var days = 30
    @State private var syncing = false
    @State private var message: (ok: Bool, text: String)?
    @State private var added: [AddedActivity] = []
    @State private var step: String?
    @State private var syncDetails: [String] = []

    private struct Row: Identifiable { let id: String; let label: String; let line: String; let bad: Bool }

    /// The sources the engine pulls itself, with their last run.
    private var pulled: [Row] {
        let src = settings.sources
        var out: [Row] = []
        func add(_ id: String, _ label: String, _ run: LastSync?) {
            out.append(Row(id: id, label: label, line: run.map(SettingsText.sync) ?? "Ready for first sync", bad: run?.error != nil))
        }
        if src.intervals.connected { add("intervals", "intervals.icu", src.intervals.lastSync) }
        if src.garmin.available && src.garmin.connected { add("garmin", "Garmin Connect", src.garmin.lastSync) }
        if let c = src.coros, c.available && c.connected { add("coros", "COROS", c.lastSync) }
        if let st = src.stravaWeb, st.available && st.enabled {
            out.append(Row(id: "strava", label: "Strava", line: st.lastSync.map(StravaCard.runLine) ?? "Not yet", bad: st.lastSync?.error != nil))
        }
        return out
    }

    private var rows: [Row] {
        var out = pulled
        if settings.watchOn {
            let source = settings.sources.watch
            let attempt = source?.lastAttempt
            let savedError = (attempt?.at ?? "") >= (source?.lastSync ?? "") ? attempt?.error : nil
            let error = watch.result.map { $0.ok ? nil : $0.text } ?? savedError
            out.append(Row(id: "watch", label: "Garmin watch", line: error ?? (source?.lastSync.map(SettingsText.when) ?? "Ready for first sync"), bad: error != nil))
        }
        if health.enabled {
            out.append(Row(id: "health", label: "Apple Health", line: health.failed ? (health.status ?? "Try syncing again") : (health.lastSync?.formatted(.relative(presentation: .named)) ?? "Ready for first sync"), bad: health.failed))
        }
        return out
    }

    var body: some View {
        Card {
            SectionLabel("Sync activities")
            if rows.isEmpty {
                Text("Connect a source below to get started, or upload activity files.")
                    .font(.subheadline).foregroundStyle(.secondary)
            } else {
                Text("Bring your latest activities into Talus from your connected sources.")
                    .font(.subheadline).foregroundStyle(.secondary)
                Button { Task { await sync() } } label: {
                    BusyLabel(title: syncing ? "Syncing…" : message?.ok == false ? "Try sync again" : "Sync now", busy: syncing)
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .disabled(syncing || watch.running || health.running)

                if syncing || watch.running || health.running {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(step ?? (watch.running ? "Syncing Garmin watch" : "Syncing Apple Health"))
                            .font(.subheadline.bold())
                        if watch.running {
                            Text(watch.phase ?? "Connecting to your watch…")
                            if let p = watch.progress {
                                Text("File \(p.index) of \(p.count)")
                                ProgressView(value: Double(p.index - 1), total: Double(p.count))
                            }
                        } else if health.running {
                            Text(health.status ?? "Reading Apple Health…")
                        }
                        Text(settings.watchOn ? "Keep your watch nearby and Talus open until sync finishes." : "Keep Talus open until sync finishes.")
                            .foregroundStyle(.secondary)
                    }
                    .font(.caption)
                    .accessibilityElement(children: .combine)
                }
                if !pulled.isEmpty {
                    DisclosureGroup("Import older activities") {
                        Picker("Check the last", selection: $days) {
                            ForEach(syncRanges, id: \.self) { Text("\($0) days").tag($0) }
                        }
                        .disabled(syncing)
                        Text("This range applies to online sources. The watch checks the last \(WatchSync.firstSyncDays) days on its first sync; Health checks \(HealthSync.firstSyncDays) days.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    .font(.subheadline)
                }
            }
            if let message { FormMessage(ok: message.ok, text: message.text) }
            if !syncDetails.isEmpty {
                DisclosureGroup("Sync details") {
                    ForEach(Array(syncDetails.enumerated()), id: \.offset) { _, line in
                        Text(line).font(.caption).foregroundStyle(.secondary)
                    }
                }.font(.caption)
            }
            if !added.isEmpty {
                VStack(alignment: .leading, spacing: 4) { ForEach(added) { a in Text(a.line) } }.font(.caption)
            }
            ForEach(rows) { r in
                VStack(alignment: .leading, spacing: 3) {
                    Text(r.label).font(.subheadline.bold())
                    Text(r.line).font(.caption).foregroundStyle(r.bad ? Tone.bad.ink : .secondary)
                }
            }
            if !rows.isEmpty {
                Text("Duplicates are skipped. Syncing again is safe.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func sync() async {
        syncing = true
        message = nil
        added = []
        syncDetails = []
        defer { syncing = false; step = nil }
        var lines: [String] = [], ok = true
        var current = 0
        let total = (pulled.isEmpty ? 0 : 1) + (settings.watchOn ? 1 : 0) + (health.enabled ? 1 : 0)
        func advance(_ title: String) { current += 1; step = "Step \(current) of \(total) · \(title)" }
        if !pulled.isEmpty {
            advance("Checking online sources")
            let r = await syncAll(api, days: days)
            lines.append(r.text)
            ok = r.ok
            added = r.added
        }
        if settings.watchOn {
            advance("Syncing Garmin watch")
            await watch.sync(api)
            if let r = watch.result { lines.append("Watch: \(r.text)"); ok = ok && r.ok }
            added = (watch.added + added).sorted { ($0.startTime ?? "") > ($1.startTime ?? "") }
        }
        if health.enabled {
            advance("Syncing Apple Health")
            await health.sync(api)
            if let status = health.status { lines.append("Apple Health: \(status)") }
            ok = ok && !health.failed
        }
        syncDetails = lines
        message = (ok, ok ? "Sync complete. All connected sources checked." : "Sync incomplete. Check the source below and try again.")
        await reload()
    }
}

/// Syncing straight from a Garmin watch, switched on or off. The Sync card runs it, and so does
/// opening the app; this card shows how the last run went.
private struct WatchCard: View {
    let lastSync: String?
    let reload: () async -> Void
    @EnvironmentObject private var api: API
    @EnvironmentObject private var watch: WatchSync
    @State private var on: Bool
    @State private var message: String?
    @State private var showLog = false

    init(on: Bool, lastSync: String?, reload: @escaping () async -> Void) {
        self.lastSync = lastSync
        self.reload = reload
        _on = State(initialValue: on)
    }

    var body: some View {
        Card {
            HStack {
                SectionLabel("Garmin watch")
                Spacer()
                Pill(text: "Experimental", tone: .warn)
            }
            Text("Import activities directly over Bluetooth. Keep your watch nearby and Talus open, then tap Sync now above.")
                .font(.caption).foregroundStyle(.secondary)
            // A binding rather than onChange, so putting it back after a failed save doesn't save again.
            Toggle("Include Garmin watch in sync", isOn: Binding(get: { on }, set: { new in
                let before = on
                on = new
                Task { await save(new, before: before) }
            }))
                .font(.subheadline)
                .disabled(watch.running)
            if let message { FormMessage(ok: false, text: message) }
            if on {
                LabeledContent("Last sync", value: lastSync.map(SettingsText.when) ?? "Not yet")
                    .font(.subheadline)
            }
            DisclosureGroup("How watch sync works") {
                Text("Talus also checks when you open the app, at most hourly. The first sync checks the last \(WatchSync.firstSyncDays) days. Available sleep, HRV and resting heart rate files are also imported. Intervals.icu or Apple Health can supply data the watch no longer has. Nothing on the watch is changed or deleted.")
                    .font(.caption).foregroundStyle(.secondary)
            }.font(.caption)
            if let r = watch.result { FormMessage(ok: r.ok, text: r.text) }
            if !watch.added.isEmpty && !watch.running {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(watch.added) { a in Text(a.line) }
                }
                .font(.caption)
            }
            if !watch.log.isEmpty && !watch.running {
                DisclosureGroup("Details", isExpanded: $showLog) {
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach(Array(watch.log.enumerated()), id: \.offset) { _, l in Text(l) }
                    }
                    .font(.caption2.monospaced()).foregroundStyle(.secondary)
                    .textSelection(.enabled)
                }
                .font(.caption)
            }
        }
        .onChange(of: watch.running) { _, running in if !running { Task { await reload() } } }
    }

    private func save(_ new: Bool, before: Bool) async {
        struct Body: Encodable { let enabled: Bool }
        message = nil
        do {
            try await api.put("/api/sources/watch", Body(enabled: new))
            await reload()
        } catch {
            on = before
            message = error.localizedDescription
        }
    }
}

let syncRanges = [7, 30, 90, 180]

/// Sync every source the engine pulls, and turn the answer into a status line.
@MainActor
private func syncAll(_ api: API, days: Int) async -> (ok: Bool, text: String, added: [AddedActivity]) {
    struct Body: Encodable { let days: Int }
    struct Run: Decodable { let label: String?; let error: String? }
    struct Result: Decodable { let saved: Int; let duplicates: Int?; let sources: [Run]?; let added: [AddedActivity]?; let error: String? }
    do {
        let r = try api.decode(try await api.post("/api/sync", Body(days: days)), as: Result.self)
        let failed = (r.sources ?? []).filter { $0.error != nil }.compactMap(\.label)
        let dups = (r.duplicates ?? 0) > 0 ? " \(r.duplicates!) already here from another source." : ""
        let stopped = failed.isEmpty ? "" : " \(failed.joined(separator: " and ")) couldn't sync."
        let got = r.saved > 0 ? "Pulled \(r.saved) new \(r.saved == 1 ? "activity" : "activities")." : (failed.isEmpty ? "No new activities." : "")
        return (failed.isEmpty && r.error == nil, got + dups + stopped + (failed.isEmpty ? (r.error ?? "") : ""), r.added ?? [])
    } catch {
        return (false, error.localizedDescription, [])
    }
}

/// intervals.icu: the API key form (OAuth stays web-only until intervals.icu approves the app),
/// and what is in Talus. The Sync card syncs it.
private struct IntervalsCard: View {
    let source: SourceInfo
    let reload: () async -> Void
    @EnvironmentObject private var api: API
    @State private var editing = false
    @State private var key = ""
    @State private var athlete = ""
    @State private var saving = false
    @State private var message: (ok: Bool, text: String)?

    private var oauth: Bool { source.kind == "oauth" }
    private var athleteId: String? { source.athleteId.flatMap { $0 == "0" ? nil : $0 } }

    var body: some View {
        Card {
            HStack {
                SectionLabel("intervals.icu")
                Spacer()
                Pill(text: source.keySet ? "Connected" : "No API key", tone: source.keySet ? .good : .bad)
            }
            VStack(alignment: .leading, spacing: 6) {
                if oauth {
                    LabeledContent("Connection", value: source.updatedAt.map { "Approved \(SettingsText.when($0))" } ?? "Approved")
                    LabeledContent("Athlete", value: [source.athleteName, athleteId].compactMap { $0 }.joined(separator: " "))
                } else {
                    LabeledContent("API key", value: source.keySet ? (source.updatedAt.map { "Saved \(SettingsText.when($0))" } ?? "From the engine's .env") : "Not added yet")
                    if source.keySet { LabeledContent("Athlete", value: athleteId ?? "Whoever owns the key") }
                }
                LabeledContent("In Talus", value: "\(source.activities.formatted())\(source.latestActivity.map { ", latest \(Fmt.day($0, .dateTime.month(.abbreviated).day().year()))" } ?? "")")
            }
            .font(.subheadline)
            if editing || (!source.keySet && !oauth) {
                LabeledField(label: "intervals.icu API key", text: $key, hint: "intervals.icu, Settings, Developer Settings.", secure: true)
                LabeledField(label: "Athlete id", text: $athlete, placeholder: "i123456", hint: "Next to the key. Blank means whoever owns the key.")
                HStack {
                    Button { Task { await saveKey() } } label: { BusyLabel(title: saving ? "Saving" : "Save key", busy: saving) }
                        .buttonStyle(.borderedProminent)
                        .disabled(saving || key.trimmingCharacters(in: .whitespaces).count < 8)
                    if source.keySet { Button("Cancel") { editing = false }.buttonStyle(.bordered) }
                }
            } else if oauth {
                Button("Disconnect", role: .destructive) { Task { await remove() } }.font(.subheadline)
            } else {
                HStack(spacing: 16) {
                    Button("Replace key") { athlete = athleteId ?? ""; editing = true }.buttonStyle(.bordered)
                    if source.updatedAt != nil { Button("Remove key", role: .destructive) { Task { await remove() } }.font(.subheadline) }
                }
            }
            if let message { FormMessage(ok: message.ok, text: message.text) }
            Text("\(oauth ? "The connection is" : "Your key is") stored encrypted on the Talus server and never sent back to the app. Only new activities are downloaded.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private func saveKey() async {
        struct Body: Encodable { let apiKey: String; let athleteId: String }
        saving = true
        defer { saving = false }
        message = nil
        let a = athlete.trimmingCharacters(in: .whitespaces)
        do {
            try await api.put("/api/settings/source", Body(apiKey: key.trimmingCharacters(in: .whitespaces), athleteId: a.isEmpty ? "0" : a))
            key = ""
            editing = false
            message = (true, "Saved. Sync now pulls your activities.")
            await reload()
        } catch APIError.server(422, _) {
            message = (false, "Check the key, and that the athlete id looks like i123456.")
        } catch {
            message = (false, error.localizedDescription)
        }
    }

    private func remove() async {
        do {
            try await api.delete("/api/settings/source")
            message = nil
            await reload()
        } catch {
            message = (false, error.localizedDescription)
        }
    }
}

/// Garmin Connect, unofficial and for the owner account only: sign in once (maybe with a code).
/// The Sync card syncs it.
private struct GarminCard: View {
    let garmin: Sources.Garmin
    let reload: () async -> Void
    @EnvironmentObject private var api: API
    @State private var step: String
    @State private var email = ""
    @State private var password = ""
    @State private var code = ""
    @State private var busy = false
    @State private var message: (ok: Bool, text: String)?

    init(garmin: Sources.Garmin, reload: @escaping () async -> Void) {
        self.garmin = garmin
        self.reload = reload
        _step = State(initialValue: garmin.mfaPending ? "mfa" : garmin.connected ? "idle" : "login")
    }

    var body: some View {
        Card {
            HStack {
                SectionLabel("Garmin Connect")
                Spacer()
                Pill(text: "Unofficial", tone: .warn)
                Pill(text: garmin.connected ? "Connected" : "Not connected", tone: garmin.connected ? .good : .neutral)
            }
            Text("Pulls activities, HRV, resting heart rate and sleep straight from Garmin, without intervals.icu. Garmin has no public API for new apps, so this signs in the way the Garmin Connect website does. Garmin can break it at any time or limit an account that syncs often. Use at your own risk.")
                .font(.caption).foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 6) {
                LabeledContent("Sign-in", value: garmin.connected ? (garmin.updatedAt.map { "Saved \(SettingsText.when($0)), good for about 6 months" } ?? "Saved") : "Not yet")
            }
            .font(.subheadline)
            switch step {
            case "login":
                LabeledField(label: "Garmin email", text: $email, keyboard: .emailAddress)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                LabeledField(label: "Garmin password", text: $password, hint: "Used once to sign in, never stored.", secure: true)
                HStack {
                    Button { Task { await signIn() } } label: { BusyLabel(title: busy ? "Signing in" : "Connect Garmin", busy: busy) }
                        .buttonStyle(.borderedProminent)
                        .disabled(busy || email.isEmpty || password.isEmpty)
                    if garmin.connected { Button("Cancel") { step = "idle" }.buttonStyle(.bordered) }
                }
            case "mfa":
                LabeledField(label: "Garmin security code", text: $code, keyboard: .numberPad)
                    .textContentType(.oneTimeCode)
                HStack {
                    Button { Task { await finish() } } label: { BusyLabel(title: busy ? "Checking" : "Finish", busy: busy) }
                        .buttonStyle(.borderedProminent)
                        .disabled(busy || code.trimmingCharacters(in: .whitespaces).count < 4)
                    Button("Start over") { step = "login" }.font(.subheadline)
                }
            default:
                HStack(spacing: 16) {
                    Button("Sign in again") { step = "login" }.buttonStyle(.bordered)
                    Button("Disconnect", role: .destructive) {
                        Task {
                            try? await api.delete("/api/sources/garmin")
                            step = "login"
                            await reload()
                        }
                    }
                    .font(.subheadline)
                }
            }
            if let message { FormMessage(ok: message.ok, text: message.text) }
            Text("Runs that intervals.icu also has are recognized and kept once.").font(.caption).foregroundStyle(.secondary)
        }
    }

    private struct Status: Decodable { let status: String }

    private func signIn() async {
        struct Body: Encodable { let email: String; let password: String }
        await post("/api/sources/garmin", Body(email: email.trimmingCharacters(in: .whitespaces), password: password))
    }

    private func finish() async {
        struct Body: Encodable { let code: String }
        await post("/api/sources/garmin/mfa", Body(code: code.trimmingCharacters(in: .whitespaces)))
    }

    private func post(_ path: String, _ body: some Encodable) async {
        busy = true
        defer { busy = false }
        message = nil
        do {
            let s = try api.decode(try await api.post(path, body), as: Status.self)
            password = ""
            code = ""
            if s.status == "needs_mfa" {
                step = "mfa"
                message = (true, "Garmin sent a code to your email or phone. Enter it here.")
                return
            }
            step = "idle"
            message = (true, "Connected. Sync now pulls your activities.")
            await reload()
        } catch {
            message = (false, error.localizedDescription)
        }
    }
}

/// FIT, GPX, TCX or zip files from any watch or app, or the Apple Health export.
private struct UploadCard: View {
    let upload: Sources.Upload
    let reload: () async -> Void
    @EnvironmentObject private var api: API
    @State private var picking = false
    @State private var busy: String?
    @State private var lines: [(ok: Bool, text: String)] = []

    private static let types: [UTType] = ["fit", "gpx", "tcx", "zip", "gz"].compactMap { UTType(filenameExtension: $0) }

    var body: some View {
        Card {
            SectionLabel("Upload files")
            Text("FIT, GPX or TCX files from any watch or app, one at a time or zipped together. Apple Health syncs on its own from this app, so you don't need its export here.")
                .font(.caption).foregroundStyle(.secondary)
            Button { picking = true } label: { BusyLabel(title: busy != nil ? "Uploading" : "Choose files", busy: busy != nil) }
                .buttonStyle(.borderedProminent)
                .disabled(busy != nil)
                .fileImporter(isPresented: $picking, allowedContentTypes: Self.types, allowsMultipleSelection: true) { result in
                    if case .success(let urls) = result { Task { await send(urls) } }
                }
            if let busy { Text(busy).font(.caption).foregroundStyle(.secondary) }
            ForEach(Array(lines.enumerated()), id: \.offset) { _, l in FormMessage(ok: l.ok, text: l.text) }
            if lines.isEmpty, let last = upload.lastImport {
                Text("Last: \(SettingsText.importLine(last))").font(.caption).foregroundStyle(.secondary)
            }
            Text("Activities you already have from another source are kept once. Strava isn't connected because its API terms bar apps with AI coaching: export your files and upload them here. On COROS, connect COROS in intervals.icu and Talus picks your runs up through intervals.icu.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private func send(_ urls: [URL]) async {
        lines = []
        for url in urls {
            let name = url.lastPathComponent
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            guard let data = try? Data(contentsOf: url) else {
                lines.append((false, "\(name): could not read the file."))
                continue
            }
            if data.count > upload.maxMb * 1024 * 1024 {
                lines.append((false, "\(name): over \(upload.maxMb) MB."))
                continue
            }
            busy = "Reading \(name)"
            let q = name.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? name
            do {
                let r = try api.decode(try await api.upload("/api/import?name=\(q)", file: data, contentType: "application/octet-stream"), as: LastImport.self)
                lines.append((r.error == nil, "\(name): \(SettingsText.importLine(r))"))
            } catch {
                lines.append((false, "\(name): \(error.localizedDescription)"))
            }
        }
        busy = nil
        await reload()
    }
}

// MARK: Readiness

struct ReadinessPage: View {
    var body: some View {
        SettingsPage(title: "Readiness") { s, reload in
            SleepNeedCard(readiness: s.readiness, reload: reload)
        }
    }
}

private struct SleepNeedCard: View {
    let readiness: ReadinessSettings
    let reload: () async -> Void
    @EnvironmentObject private var api: API
    @State private var hours: Double
    @State private var saving = false
    @State private var message: (ok: Bool, text: String)?

    init(readiness: ReadinessSettings, reload: @escaping () async -> Void) {
        self.readiness = readiness
        self.reload = reload
        _hours = State(initialValue: readiness.sleepNeedH)
    }

    var body: some View {
        Card {
            SectionLabel("Sleep you need")
            Stepper(value: $hours, in: 4...12, step: 0.25) {
                Text("\(String(format: "%g", hours)) hours a night").font(.headline.monospacedDigit())
            }
            Text("\(source) Sleep reads short when last night is 1.5 h under this, or the 7-day average is 1 h under.")
                .font(.caption).foregroundStyle(.secondary)
            Button { Task { await save() } } label: { BusyLabel(title: saving ? "Saving" : "Save sleep need", busy: saving) }
                .buttonStyle(.borderedProminent)
                .disabled(saving || hours == readiness.sleepNeedH)
            if let message { FormMessage(ok: message.ok, text: message.text) }
        }
    }

    private var source: String {
        switch readiness.sleepNeedSource {
        case "saved": "Saved here."
        case "env": "From TALUS_SLEEP_NEED_H in the engine's .env until you save one here."
        default: "The 8 h default until you save your own."
        }
    }

    private func save() async {
        struct Body: Encodable { let sleepNeedH: Double }
        saving = true
        defer { saving = false }
        do {
            try await api.put("/api/settings/readiness", Body(sleepNeedH: hours))
            message = (true, "Saved. Today reads your sleep against this from now on.")
            await reload()
        } catch {
            message = (false, error.localizedDescription)
        }
    }
}
