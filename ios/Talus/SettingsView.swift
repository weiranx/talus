import SwiftUI
import UniformTypeIdentifiers

struct SettingsView: View {
    @EnvironmentObject private var api: API
    @EnvironmentObject private var session: SessionModel
    @EnvironmentObject private var health: HealthSync
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
                }

                Section {
                    if health.enabled {
                        Button { Task { await health.sync(api) } } label: {
                            BusyLabel(title: health.running ? "Syncing…" : "Sync now", busy: health.running)
                        }
                            .disabled(health.running)
                        Toggle("Sync when Talus opens", isOn: $health.enabled)
                    } else {
                        Button("Connect Apple Health") { Task { await health.turnOn(api) } }
                    }
                    if let status = health.status {
                        Text(status).font(.footnote).foregroundStyle(.secondary)
                    }
                    if let last = health.lastSync {
                        LabeledContent("Last sync", value: last.formatted(.relative(presentation: .named)))
                    }
                } header: {
                    Text("Apple Health")
                } footer: {
                    Text("Sends workouts with route and heart rate, plus HRV, resting heart rate and sleep, to your Talus engine. Apple Watch data is in Health, and so is Garmin data once Garmin Connect is set to write to Health. The first sync reads the last \(HealthSync.firstSyncDays) days.")
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
            IntervalsCard(source: s.source, lastSync: s.sources.intervals.lastSync, reload: reload)
            if s.sources.garmin.available { GarminCard(garmin: s.sources.garmin, reload: reload) }
            UploadCard(upload: s.sources.upload, reload: reload)
        }
    }
}

private let syncRanges = [7, 30, 90, 180]

/// Run a sync for one source and turn the answer into a status line.
@MainActor
private func runSync(_ api: API, source: String, days: Int) async -> (ok: Bool, text: String) {
    struct Body: Encodable { let days: Int; let source: String }
    struct Result: Decodable { let saved: Int?; let duplicates: Int?; let error: String? }
    do {
        let r = try api.decode(try await api.post("/api/sync", Body(days: days, source: source)), as: Result.self)
        if let e = r.error { return (false, "Sync failed: \(e)") }
        let saved = r.saved ?? 0
        let dups = (r.duplicates ?? 0) > 0 ? " \(r.duplicates!) already here from another source." : ""
        return (true, (saved > 0 ? "Pulled \(saved) new \(saved == 1 ? "activity" : "activities")." : "Up to date, nothing new.") + dups)
    } catch {
        return (false, error.localizedDescription)
    }
}

private struct SyncControls: View {
    let source: String
    let enabled: Bool
    let reload: () async -> Void
    @EnvironmentObject private var api: API
    @State private var days = 30
    @State private var syncing = false
    @State private var message: (ok: Bool, text: String)?

    var body: some View {
        HStack {
            Picker("Look back", selection: $days) {
                ForEach(syncRanges, id: \.self) { Text("\($0) days").tag($0) }
            }
            .disabled(syncing)
            Spacer()
            Button { Task { await sync() } } label: { BusyLabel(title: syncing ? "Syncing" : "Sync now", busy: syncing) }
                .buttonStyle(.borderedProminent)
                .disabled(syncing || !enabled)
        }
        if let message { FormMessage(ok: message.ok, text: message.text) }
    }

    private func sync() async {
        syncing = true
        message = nil
        message = await runSync(api, source: source, days: days)
        syncing = false
        await reload()
    }
}

/// intervals.icu: the API key form (OAuth stays web-only until intervals.icu approves the app),
/// what is in Talus, and a sync.
private struct IntervalsCard: View {
    let source: SourceInfo
    let lastSync: LastSync?
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
                LabeledContent("Last sync") { Text(lastSync.map(SettingsText.sync) ?? "Not from here yet").multilineTextAlignment(.trailing) }
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
            Divider()
            SyncControls(source: "intervals", enabled: source.keySet, reload: reload)
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
            message = (true, "Saved. Sync to pull your activities.")
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

/// Garmin Connect, unofficial and for the owner account only: sign in once (maybe with a code),
/// then sync.
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
                LabeledContent("Last sync") { Text(garmin.lastSync.map(SettingsText.sync) ?? "Not yet").multilineTextAlignment(.trailing) }
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
                SyncControls(source: "garmin", enabled: true, reload: reload)
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
            message = (true, "Connected. Sync to pull your activities.")
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
            Text("Activities you already have from another source are kept once. Not connected: Strava, whose API terms bar apps with AI coaching, and COROS, which opens its API only to approved partners. Export files from either and upload them here.")
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
