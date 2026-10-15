import SwiftUI

// The owner-only data sources (web/components/CorosCard.tsx, StravaCard.tsx): COROS through the
// Training Hub sign-in, and the owner's own Strava activities through the website.

extension Sources {
    /// COROS direct (Training Hub's API) is unofficial and offered to the owner account only.
    struct Coros: Codable, Hashable {
        let available: Bool
        let connected: Bool
        let updatedAt: String?
        let region: String?
        let lastSync: LastSync?
    }

    /// The owner's own Strava activities through the website, with a session copied from a browser.
    /// A personal fallback for when intervals.icu can't get them; no other account sees it.
    struct StravaWeb: Codable, Hashable {
        let available: Bool
        let connected: Bool
        let enabled: Bool
        let updatedAt: String?
        let envOverride: Bool
        let lastSync: LastSync?
    }
}

/// The look-back picker and the sync button, for a card that handles the answer itself.
private struct PullRow: View {
    let title: String
    let busyTitle: String
    let busy: Bool
    @Binding var days: Int
    let pull: () -> Void

    var body: some View {
        HStack {
            Picker("Look back", selection: $days) {
                ForEach(syncRanges, id: \.self) { Text("\($0) days").tag($0) }
            }
            .disabled(busy)
            Spacer()
            Button(action: pull) { BusyLabel(title: busy ? busyTitle : title, busy: busy) }
                .buttonStyle(.borderedProminent)
                .disabled(busy)
        }
    }
}

// MARK: COROS

struct CorosCard: View {
    let coros: Sources.Coros
    let reload: () async -> Void
    @EnvironmentObject private var api: API
    @State private var login: Bool
    @State private var email = ""
    @State private var password = ""
    @State private var region: String
    @State private var days = 30
    @State private var busy = false
    @State private var message: (ok: Bool, text: String)?

    private static let regions = [("en", "Americas, Asia-Pacific and the rest"), ("eu", "Europe"), ("cn", "China")]

    init(coros: Sources.Coros, reload: @escaping () async -> Void) {
        self.coros = coros
        self.reload = reload
        _login = State(initialValue: !coros.connected)
        _region = State(initialValue: coros.region ?? "en")
    }

    var body: some View {
        Card {
            HStack {
                SectionLabel("COROS")
                Spacer()
                Pill(text: "Unofficial", tone: .warn)
                Pill(text: coros.connected ? "Connected" : "Not connected", tone: coros.connected ? .good : .neutral)
            }
            Text("Pulls activities (FIT files), overnight HRV and resting heart rate straight from COROS. COROS opens its API only to approved partners, so this signs in the way the Training Hub website does. COROS keeps one web sign-in per account: signing in to Training Hub in a browser signs Talus out, and this card asks for your password again. Use at your own risk.")
                .font(.caption).foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 6) {
                LabeledContent("Sign-in", value: coros.connected ? coros.updatedAt.map { "Saved \(SettingsText.when($0))" } ?? "Saved" : "Not yet")
                LabeledContent("Last sync") { Text(coros.lastSync.map(SettingsText.sync) ?? "Not yet").multilineTextAlignment(.trailing) }
            }
            .font(.subheadline)
            if login {
                LabeledField(label: "COROS email", text: $email, keyboard: .emailAddress)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                LabeledField(label: "COROS password", text: $password, hint: "Used once to sign in, never stored.", secure: true)
                Picker("Account region", selection: $region) {
                    ForEach(Self.regions, id: \.0) { Text($0.1).tag($0.0) }
                }
                .font(.subheadline)
                HStack {
                    Button { Task { await connect() } } label: { BusyLabel(title: busy ? "Signing in" : "Connect COROS", busy: busy) }
                        .buttonStyle(.borderedProminent)
                        .disabled(busy || email.isEmpty || password.isEmpty)
                    if coros.connected { Button("Cancel") { login = false }.buttonStyle(.bordered) }
                }
            } else {
                PullRow(title: "Sync now", busyTitle: "Syncing", busy: busy, days: $days) { Task { await sync() } }
                HStack(spacing: 16) {
                    Button("Sign in again") { login = true }.buttonStyle(.bordered)
                    Button("Disconnect", role: .destructive) { Task { await disconnect() } }.font(.subheadline)
                }
            }
            if let message { FormMessage(ok: message.ok, text: message.text) }
            Text("Runs another source already has are kept once. Sleep time isn't in COROS's data here; it still comes from intervals.icu or Apple Health.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private func connect() async {
        struct Body: Encodable { let email: String; let password: String; let region: String }
        busy = true
        defer { busy = false }
        message = nil
        do {
            try await api.post("/api/sources/coros", Body(email: email.trimmingCharacters(in: .whitespaces), password: password, region: region))
            password = ""
            login = false
            message = (true, "Connected. Sync to pull your activities.")
            await reload()
        } catch {
            message = (false, error.localizedDescription)
        }
    }

    private func disconnect() async {
        _ = try? await api.delete("/api/sources/coros")
        login = true
        await reload()
    }

    private func sync() async {
        busy = true
        message = nil
        let result = await runSync(api, source: "coros", days: days)
        busy = false
        message = result
        if !result.ok && result.text.contains("Sign in to COROS again") { login = true }
        await reload()
    }
}

// MARK: Strava

struct StravaCard: View {
    let strava: Sources.StravaWeb
    let reload: () async -> Void
    @EnvironmentObject private var api: API
    @State private var editing: Bool
    @State private var cookie = ""
    @State private var days = 30
    @State private var busy = false
    @State private var message: (ok: Bool, text: String)?

    /// Only while a session is saved: once it's turned off, the last run's ending is history.
    private var expired: Bool { strava.connected && strava.lastSync?.errorCode == "session_expired" }

    init(strava: Sources.StravaWeb, reload: @escaping () async -> Void) {
        self.strava = strava
        self.reload = reload
        _editing = State(initialValue: !strava.connected || (strava.connected && strava.lastSync?.errorCode == "session_expired"))
    }

    var body: some View {
        Card {
            HStack {
                SectionLabel("Strava")
                Spacer()
                Pill(text: "Unofficial", tone: .warn)
                if expired {
                    Pill(text: "Session ended", tone: .bad)
                } else {
                    Pill(text: strava.connected && strava.enabled ? "On" : "Off", tone: strava.connected && strava.enabled ? .good : .neutral)
                }
            }
            Text("A personal fallback for when intervals.icu can't get your runs: after each intervals.icu sync, Talus reads your own activities from Strava's website with a session copied from your browser, and downloads each new one's original file. Strava's terms don't allow reading the site this way, so it's only for the owner account, at your own risk. Talus never asks for your Strava password.")
                .font(.caption).foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 6) {
                LabeledContent("Session", value: strava.connected ? strava.updatedAt.map { "Saved \(SettingsText.when($0))" } ?? "Saved" : "Not yet")
                LabeledContent("Last run") { Text(strava.lastSync.map(Self.runLine) ?? "Not yet").multilineTextAlignment(.trailing) }
            }
            .font(.subheadline)
            if strava.envOverride {
                Text("STRAVA_SESSION_COOKIE in the engine's .env is set, and it overrides the session saved here.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if editing {
                if expired { FormMessage(ok: false, text: "Strava ended the session Talus held. Copy a fresh one.") }
                VStack(alignment: .leading, spacing: 4) {
                    Text("1. On a computer, in Chrome signed in to Strava, open [strava.com/dashboard](https://www.strava.com/dashboard).")
                    Text("2. Open DevTools (Cmd+Option+I on a Mac, F12 elsewhere), go to **Network** and reload the page.")
                    Text("3. Click the first request, **dashboard**, then **Headers** > **Request Headers**.")
                    Text("4. Right-click the **Cookie** line, choose **Copy value**, and paste it below.")
                }
                .font(.caption).foregroundStyle(.secondary)
                LabeledField(label: "Strava Cookie header", text: $cookie, placeholder: "_strava4_session=…",
                             hint: "Checked with Strava before it's saved, then kept encrypted and never shown again. It works until Strava ends the session; this card says when.",
                             secure: true)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                HStack {
                    Button { Task { await save() } } label: { BusyLabel(title: busy ? "Checking with Strava" : "Check and save", busy: busy) }
                        .buttonStyle(.borderedProminent)
                        .disabled(busy || cookie.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    if strava.connected { Button("Cancel") { editing = false }.buttonStyle(.bordered) }
                }
            } else {
                PullRow(title: "Pull now", busyTitle: "Pulling, a request a second", busy: busy, days: $days) { Task { await pull() } }
                HStack(spacing: 16) {
                    Button("Replace session") { editing = true }.buttonStyle(.bordered).disabled(busy)
                    Button("Turn off", role: .destructive) { Task { await turnOff() } }.font(.subheadline).disabled(busy)
                }
            }
            if let message { FormMessage(ok: message.ok, text: message.text) }
            Text("Runs another source already has are kept once. Turning it off forgets the session; activities already pulled stay.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    static func runLine(_ s: LastSync) -> String {
        if s.errorCode == "session_expired" { return "Stopped \(SettingsText.when(s.at)): Strava ended the session." }
        if s.errorCode == "captcha" {
            return "Stopped \(SettingsText.when(s.at)): Strava asked for a captcha. Open strava.com in your browser, then copy a fresh session."
        }
        // The engine's message, without the exception's class name or the command-line hint.
        if let e = s.error {
            let clean = e.replacingOccurrences(of: #"^\w+: "#, with: "", options: .regularExpression)
                .replacingOccurrences(of: #" ?Run `talus strava-web setup`[^.]*\."#, with: "", options: .regularExpression)
            return "Stopped \(SettingsText.when(s.at)): \(clean)"
        }
        let here = (s.aliased ?? 0) > 0 ? ", \(s.aliased!) already here" : ""
        return "\(SettingsText.when(s.at)): \(s.listed) on Strava since \(Fmt.day(s.since, .dateTime.month(.abbreviated).day())), \(s.saved) new\(here)."
    }

    private struct Found: Decodable {
        struct Latest: Decodable { let name: String?; let startTime: String }
        let total: Int?
        let latest: Latest?
    }

    private func save() async {
        struct Body: Encodable { let cookie: String }
        busy = true
        defer { busy = false }
        message = nil
        do {
            let data = try await api.post("/api/sources/strava-web", Body(cookie: cookie))
            let found = try? api.decode(data, as: Found.self)
            cookie = ""
            editing = false
            let latest = found?.latest.map { ", the latest \"\($0.name ?? "activity")\" on \(Fmt.day($0.startTime, .dateTime.month(.abbreviated).day()))" } ?? ""
            message = (true, "Signed in: \(found?.total ?? 0) activities on Strava\(latest). Pull now, or let the next intervals.icu sync do it.")
            await reload()
        } catch {
            message = (false, error.localizedDescription)
        }
    }

    private func turnOff() async {
        busy = true
        _ = try? await api.delete("/api/sources/strava-web")
        busy = false
        message = nil
        editing = true
        await reload()
    }

    private func pull() async {
        busy = true
        message = nil
        let result = await runSync(api, source: "strava_web", days: days)
        busy = false
        if !result.ok && result.text.range(of: "session", options: .caseInsensitive) != nil {
            message = (false, "Strava ended the session. Copy a fresh one below.")
            editing = true
        } else {
            message = result
        }
        await reload()
    }
}
