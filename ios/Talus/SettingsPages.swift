import SwiftUI

/// A page under Settings that reads /api/settings, with pull to refresh.
struct SettingsPage<Content: View>: View {
    let title: String
    @ViewBuilder var content: (SettingsData, @escaping () async -> Void) -> Content
    @EnvironmentObject private var api: API

    var body: some View {
        Loader(load: { try await api.get("/api/settings", as: SettingsData.self) }) { s, reload in
            ScrollView {
                VStack(alignment: .leading, spacing: 14) { content(s, reload) }.padding()
            }
            .refreshable { await reload() }
        }
        .background(Color(uiColor: .systemGroupedBackground))
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
    }
}

// MARK: Heart rate

struct HeartRatePage: View {
    var body: some View {
        SettingsPage(title: "Heart rate") { s, reload in
            ThresholdsCard(current: s.thresholds, inferred: s.inferred, reload: reload)
            if let th = s.thresholds { ZonesCard(zones: th.zones, reload: reload) }
        }
    }
}

private struct ThresholdsCard: View {
    let current: SettingsThresholds?
    let inferred: InferredThresholds?
    let reload: () async -> Void
    @EnvironmentObject private var api: API
    @State private var resting = ""
    @State private var aet = ""
    @State private var ant = ""
    @State private var max = ""
    @State private var sex = "male"
    @State private var pace = ""
    @State private var editing: Bool
    @State private var saving = false
    @State private var message: (ok: Bool, text: String)?

    init(current: SettingsThresholds?, inferred: InferredThresholds?, reload: @escaping () async -> Void) {
        self.current = current
        self.inferred = inferred
        self.reload = reload
        _editing = State(initialValue: current == nil)
        if let th = current {
            _resting = State(initialValue: Num.text(th.resting))
            // A provisional AeT is the 88% rule, so leave it blank and let the engine keep applying it.
            _aet = State(initialValue: th.aetProvisional ? "" : Num.text(th.aet))
            _ant = State(initialValue: Num.text(th.ant))
            _max = State(initialValue: Num.text(th.max))
            _sex = State(initialValue: th.sex)
            _pace = State(initialValue: Self.paceText(th.thresholdPaceSPerMi))
        }
    }

    var body: some View {
        Card {
            HStack {
                SectionLabel("Heart rate thresholds")
                Spacer()
                if let th = current {
                    if th.aetProvisional { Pill(text: "AeT provisional", tone: .warn) }
                    else { Pill(text: th.source == "manual" ? "Set by you" : th.source.hasPrefix("inferred") ? "From your runs" : th.source, tone: .neutral) }
                } else {
                    Pill(text: "Not set", tone: .bad)
                }
            }
            if let th = current {
                HStack {
                    stat("Resting", th.resting)
                    stat("AeT", th.aet)
                    stat("AnT", th.ant)
                    stat("Max", th.max)
                }
                Text("Aerobic gap \(Fmt.pct(th.aerobicGap)), goal under 10%.\(th.aetProvisional ? " AeT is 88% of AnT until you set it or Talus estimates it from your runs." : "")")
                    .font(.subheadline).foregroundStyle(.secondary)
            } else {
                Text("Talus needs these to score heart rate, split time into zones and work out cardio load.")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            if let inf = inferred { InferredView(inf: inf, current: current, use: useInferred) }
            if editing {
                form
            } else {
                Button("Change") { editing = true; message = nil }.buttonStyle(.bordered)
            }
            if let message { FormMessage(ok: message.ok, text: message.text) }
        }
        .task(id: inferred?.status) {
            // While the engine reads runs in the background, check back until the estimate is ready.
            guard inferred?.status == "computing" else { return }
            try? await Task.sleep(for: .seconds(5))
            await reload()
        }
    }

    private func stat(_ label: String, _ v: Double) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            Text(Fmt.n0(v)).font(.title3.weight(.semibold).monospacedDigit())
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var form: some View {
        let antV = Double(ant) ?? 0
        let preview = aet.isEmpty && antV > 0 ? "\(Int((antV * 0.88).rounded())) (88% of AnT)" : ""
        return VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                LabeledField(label: "Resting, bpm", text: $resting, hint: "first thing in the morning", keyboard: .numberPad)
                LabeledField(label: "Max, bpm", text: $max, hint: "highest you have seen", keyboard: .numberPad)
            }
            LabeledField(label: "Aerobic threshold (AeT), bpm", text: $aet, placeholder: preview, hint: "blank uses 88% of AnT, marked provisional", keyboard: .numberPad)
            LabeledField(label: "Anaerobic threshold (AnT), bpm", text: $ant, hint: "your hardest steady 30 to 60 min", keyboard: .numberPad)
            LabeledField(label: "Threshold pace, per mile", text: $pace, placeholder: "8:30", hint: "optional, scores runs that have no heart rate", keyboard: .numbersAndPunctuation)
            Picker("Load curve", selection: $sex) {
                Text("Male").tag("male")
                Text("Female").tag("female")
            }
            .font(.subheadline.weight(.semibold))
            Text("Sets how fast cardio load rises with heart rate.").font(.caption).foregroundStyle(.secondary)
            HStack {
                Button { Task { await save() } } label: { BusyLabel(title: saving ? "Saving and rescoring" : "Save and rescore", busy: saving) }
                    .buttonStyle(.borderedProminent)
                    .disabled(resting.isEmpty || ant.isEmpty || max.isEmpty || saving)
                if current != nil { Button("Cancel") { editing = false; message = nil }.buttonStyle(.bordered) }
            }
            Text("Saving rescores every activity, so loads and zone minutes use the new numbers.").font(.caption).foregroundStyle(.secondary)
        }
    }

    static func paceText(_ s: Double?) -> String {
        guard let s else { return "" }
        let r = Int(s.rounded())
        return String(format: "%d:%02d", r / 60, r % 60)
    }

    /// nil for blank, .some(nil) when it doesn't read as m:ss.
    static func paceSeconds(_ text: String) -> Double?? {
        let t = text.trimmingCharacters(in: .whitespaces)
        if t.isEmpty { return .some(nil) }
        let parts = t.split(separator: ":")
        guard parts.count == 2, let m = Int(parts[0]), let s = Int(parts[1]), parts[1].count == 2, s < 60, m < 100 else { return nil }
        return .some(Double(m * 60 + s))
    }

    private func useInferred() {
        guard let inf = inferred, let a = inf.ant, let e = inf.aet, let m = inf.max else { return }
        if let v = a.value { ant = Num.text(v) }
        // A provisional AeT is just the 88% rule: leave the box blank so the engine applies it and labels it.
        if let v = e.value { aet = e.confidence == "provisional" ? "" : Num.text(v) }
        // Recorded max is a floor: a true max may be higher, so never lower the one already set.
        if let v = m.value { max = Num.text(Swift.max(v, Double(max) ?? 0)) }
        editing = true
        message = (true, "Filled in from your runs. Check them, then save.")
    }

    private func save() async {
        struct Body: Encodable {
            let resting: Double, aet: Double?, ant: Double, max: Double, sex: String, thresholdPaceSPerMi: Double?
        }
        guard let p = Self.paceSeconds(pace) else {
            message = (false, "Write threshold pace as minutes:seconds per mile, like 8:30.")
            return
        }
        saving = true
        defer { saving = false }
        message = nil
        struct Result: Decodable { let rescored: Int }
        do {
            let data = try await api.put("/api/settings/thresholds", Body(resting: Double(resting) ?? 0, aet: Double(aet), ant: Double(ant) ?? 0,
                                                                         max: Double(max) ?? 0, sex: sex, thresholdPaceSPerMi: p))
            let r = try? api.decode(data, as: Result.self)
            message = (true, "Saved. Rescored \(r?.rescored ?? 0) activities with the new zones.")
            editing = false
            await reload()
        } catch APIError.server(422, let detail) where detail.hasPrefix("The engine answered") {
            message = (false, "Check each number is a heart rate in bpm.")
        } catch {
            message = (false, error.localizedDescription)
        }
    }
}

/// Thresholds the engine estimates from the last 180 days of runs.
private struct InferredView: View {
    let inf: InferredThresholds
    let current: SettingsThresholds?
    let use: () -> Void

    private static func tone(_ c: String) -> Tone {
        switch c {
        case "high": .good
        case "low", "provisional": .warn
        case "none": .bad
        default: .neutral
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let ant = inf.ant, let aet = inf.aet, let max = inf.max {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Estimated from your runs").fontWeight(.semibold)
                        Text("\(inf.runsWithHr) runs with heart rate in the last \(inf.days) days\(inf.status == "computing" ? ", still reading \(inf.runsTotal - inf.runsRead) more" : "")")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Use these", action: use).buttonStyle(.bordered).disabled(ant.value == nil)
                }
                ForEach([("AeT", aet, current?.aet), ("AnT", ant, current?.ant), ("Max", max, current?.max)], id: \.0) { label, e, yours in
                    HStack {
                        Text(label).frame(width: 40, alignment: .leading)
                        Text("Yours \(Fmt.n0(yours))").foregroundStyle(.secondary)
                        Spacer()
                        Text(Fmt.n0(e.value)).monospacedDigit()
                        Pill(text: e.confidence == "none" ? "no data" : e.confidence, tone: Self.tone(e.confidence))
                    }
                    .font(.subheadline)
                }
                ForEach([("AnT", ant), ("AeT", aet), ("Max", max)], id: \.0) { label, e in
                    (Text("\(label): ").bold() + Text(e.method + (e.activities.isEmpty ? "" : ". From " + e.activities.map { "\($0.name ?? "Activity") \(Fmt.day($0.day, .dateTime.month(.abbreviated).day()))" }.joined(separator: ", ")) + "."))
                        .font(.caption).foregroundStyle(.secondary)
                }
                Text("A lab or field test beats these. Whatever you save here is what Talus uses.").font(.caption).foregroundStyle(.secondary)
            } else {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("Estimating from your runs").fontWeight(.semibold)
                }
                ProgressView(value: Double(inf.runsRead), total: Double(Swift.max(inf.runsTotal, 1)))
                Text("Reading \(inf.runsTotal) runs from the last \(inf.days) days (\(inf.runsRead) done). This runs in the background, once; later visits are instant.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(12)
        .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
    }
}

private struct ZonesCard: View {
    let zones: ZoneSettings
    let reload: () async -> Void
    @EnvironmentObject private var api: API
    @State private var n: Int
    @State private var draft: [String]
    @State private var saving: String?
    @State private var message: (ok: Bool, text: String)?

    init(zones: ZoneSettings, reload: @escaping () async -> Void) {
        self.zones = zones
        self.reload = reload
        _n = State(initialValue: zones.zones)
        _draft = State(initialValue: Self.start(zones, zones.zones).map(String.init))
    }

    static func start(_ z: ZoneSettings, _ n: Int) -> [Int] {
        let o = z.options[String(n)]
        return o?.custom ?? o?.derived ?? []
    }

    var body: some View {
        let floors = draft.map { Int($0) ?? 0 }
        let derived = zones.options[String(n)]?.derived ?? []
        let names = zones.options[String(n)]?.names ?? []
        let ok = floors.enumerated().allSatisfy { i, f in f >= 40 && f <= 240 && (i == 0 || f > floors[i - 1]) }
        let saved = n == zones.zones && floors == zones.floors
        let colors = Zones.colors(n)
        Card {
            HStack {
                SectionLabel("Heart rate zones")
                Spacer()
                Pill(text: zones.custom ? "\(zones.zones) zones, edited" : "\(zones.zones) zones, from thresholds", tone: .neutral)
            }
            Picker("Number of zones", selection: $n) {
                Text("3 zones").tag(3)
                Text("5 zones").tag(5)
            }
            .pickerStyle(.segmented)
            .onChange(of: n) { _, next in
                draft = Self.start(zones, next).map(String.init)
                message = nil
            }
            ForEach(Array(names.enumerated()), id: \.offset) { k, name in
                HStack {
                    RoundedRectangle(cornerRadius: 3).fill(colors[min(k, colors.count - 1)]).frame(width: 14, height: 14)
                    Text("Zone \(k + 1), \(name.lowercased())").font(.subheadline)
                    Spacer()
                    if k > 0, k - 1 < draft.count {
                        TextField("bpm", text: $draft[k - 1])
                            .keyboardType(.numberPad).multilineTextAlignment(.trailing)
                            .textFieldStyle(.roundedBorder).frame(width: 64)
                            .accessibilityLabel("Zone \(k + 1) starts at, bpm")
                    }
                    Text(ok ? range(floors, k) : "").font(.caption.monospacedDigit()).foregroundStyle(.secondary).frame(width: 82, alignment: .trailing)
                }
            }
            if !ok { Text("Each zone has to start higher than the one before.").font(.caption).foregroundStyle(Tone.bad.ink) }
            Text((n == 5 ? "From your thresholds: zone 2 starts at 90% of AeT, zone 3 at AeT, zone 4 halfway to AnT, zone 5 just above AnT."
                  : "From your thresholds: zone 2 starts at AeT and zone 3 just above AnT.")
                 + " Zones set time in zone on Today, Load and Activity, and what the coach reads. Loads and the 80% target, which counts time below AeT, stay the same.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Button { Task { await save(floors: floors) } } label: { BusyLabel(title: saving == "save" ? "Saving" : "Save zones", busy: saving == "save") }
                    .buttonStyle(.borderedProminent)
                    .disabled(!ok || saving != nil || saved)
                if floors != derived {
                    Button { Task { await save(floors: nil) } } label: { BusyLabel(title: "Reset to thresholds", busy: saving == "reset") }
                        .buttonStyle(.bordered).disabled(saving != nil)
                }
            }
            if let message { FormMessage(ok: message.ok, text: message.text) }
        }
    }

    private func range(_ floors: [Int], _ k: Int) -> String {
        if k == 0 { return "under \(floors.first ?? 0) bpm" }
        if k == n - 1 { return "and up" }
        return "to \(floors[k] - 1) bpm"
    }

    private func save(floors: [Int]?) async {
        struct Body: Encodable { let zones: Int; let floors: [Int]?; let reset: Bool }
        struct Result: Decodable { let rescored: Int? }
        saving = floors == nil ? "reset" : "save"
        defer { saving = nil }
        do {
            let data = try await api.put("/api/settings/zones", Body(zones: n, floors: floors, reset: floors == nil))
            let r = try? api.decode(data, as: Result.self)
            if floors == nil { draft = (zones.options[String(n)]?.derived ?? []).map(String.init) }
            message = (true, "Saved \(n) zones.\((r?.rescored ?? 0) > 0 ? " Rescored \(r!.rescored!) activities to fill in time in zone." : "")")
            await reload()
        } catch {
            message = (false, error.localizedDescription)
        }
    }
}

// MARK: AI coach

struct CoachPage: View {
    var body: some View {
        SettingsPage(title: "AI coach") { s, reload in
            CoachSettingsCard(coach: s.coach, reload: reload)
        }
    }
}

private struct CoachSettingsCard: View {
    let coach: CoachSettings
    let reload: () async -> Void
    @EnvironmentObject private var api: API
    @State private var enabled: Bool
    @State private var brief: String
    @State private var analysis: String
    @State private var style: String
    @State private var cap: String
    @State private var saving = false
    @State private var message: (ok: Bool, text: String)?

    init(coach: CoachSettings, reload: @escaping () async -> Void) {
        self.coach = coach
        self.reload = reload
        _enabled = State(initialValue: coach.enabled)
        _brief = State(initialValue: coach.briefModel)
        _analysis = State(initialValue: coach.analysisModel)
        _style = State(initialValue: coach.style)
        _cap = State(initialValue: coach.cap.limit.map { String(format: "%g", $0) } ?? "")
    }

    private var capValue: Double? { Double(cap.trimmingCharacters(in: .whitespaces)) }
    private var capDirty: Bool { capValue != coach.cap.limit }

    var body: some View {
        let capValid = cap.trimmingCharacters(in: .whitespaces).isEmpty || (capValue.map { $0 >= 0 && $0 <= 1000 } ?? false)
        let dirty = capDirty || enabled != coach.enabled || style != coach.style || brief != coach.briefModel || analysis != coach.analysisModel
        let capped = coach.cap.limit.map { coach.cap.spent >= $0 } ?? false
        Card {
            HStack {
                SectionLabel("AI coach")
                Spacer()
                Pill(text: !coach.keySet ? "No API key" : coach.enabled ? "On" : "Off", tone: !coach.keySet ? .warn : coach.enabled ? .good : .neutral)
            }
            Toggle(isOn: $enabled) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Use the AI coach").fontWeight(.semibold)
                    Text("Off shows the rule-based call and summary instead, with no API calls. Sessions always come from the engine either way.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            if !coach.keySet {
                Text((coach.cap.ceiling == nil ? "Add ANTHROPIC_API_KEY to the engine's .env and restart it." : "The owner of this Talus hasn't added an Anthropic key yet.")
                     + " Until then Talus shows rule-based text.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            choices(capped: capped)
            .disabled(!enabled)
            .font(.subheadline)
            Button { Task { await save(capValid: capValid) } } label: { BusyLabel(title: saving ? "Saving" : "Save coach settings", busy: saving) }
                .buttonStyle(.borderedProminent)
                .disabled(!dirty || saving)
            if let message { FormMessage(ok: message.ok, text: message.text) }
        }
    }

    @ViewBuilder
    private func choices(capped: Bool) -> some View {
        VStack(alignment: .leading, spacing: 10) {
                Picker("Coaching style", selection: $style) {
                    ForEach(coach.styles, id: \.key) { Text($0.label).tag($0.key) }
                }
                Text("\(coach.styles.first { $0.key == style }?.description ?? "") Style changes the voice only: the call, the session and the number check stay the same.")
                    .font(.caption).foregroundStyle(.secondary)
                Picker("Daily brief model", selection: $brief) {
                    ForEach(coach.models, id: \.id) { Text($0.label).tag($0.id) }
                }
                Text("Runs after each sync and after your check-in. \(costLine(coach.estimate.brief[brief])).").font(.caption).foregroundStyle(.secondary)
                Picker("Workout analysis model", selection: $analysis) {
                    ForEach(coach.models, id: \.id) { Text($0.label).tag($0.id) }
                }
                Text("Runs once per activity, right after it syncs. \(costLine(coach.estimate.analysis[analysis])).").font(.caption).foregroundStyle(.secondary)
                if let haiku = coach.models.first(where: { $0.id.hasPrefix("claude-haiku") }), brief != haiku.id {
                    Text("The brief is a short reply, so it could run on \(haiku.label) for about \(SettingsText.money(coach.estimate.brief[haiku.id]?.perMonth)) a month. Every reply still goes through the same number check.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                LabeledField(label: "Monthly spending cap, $", text: $cap, placeholder: coach.cap.ceiling == nil ? "No cap" : "", keyboard: .decimalPad)
                Text("\(SettingsText.money(coach.cap.spent)) spent this month\(coach.cap.limit.map { " of \(SettingsText.usd($0))" } ?? "").\(capped ? " Cap reached: rule-based text until next month." : "")\(coach.cap.ceiling.map { " This Talus allows up to \(SettingsText.usd($0)) a month per account." } ?? " Blank means no cap.")")
                    .font(.caption).foregroundStyle(capped ? Tone.bad.ink : .secondary)
        }
    }

    private func costLine(_ e: CostEstimate?) -> String {
        guard let e else { return "" }
        let call = SettingsText.money(e.perCall)
        return "\(call.hasPrefix("under") ? "Under 1¢" : "About \(call)") a call, \(SettingsText.money(e.perMonth)) a month at \(e.callsPerMonth) calls\(e.fromLog ? "" : ", from a typical reply size until the coach has run")"
    }

    /// The cap goes along only when it changed, and as an explicit null when cleared.
    private struct CoachIn: Encodable {
        let enabled: Bool, briefModel: String, analysisModel: String, style: String
        let cap: Double??

        enum CodingKeys: String, CodingKey { case enabled, briefModel, analysisModel, style, monthlyCapUsd }

        func encode(to encoder: Encoder) throws {
            var c = encoder.container(keyedBy: CodingKeys.self)
            try c.encode(enabled, forKey: .enabled)
            try c.encode(briefModel, forKey: .briefModel)
            try c.encode(analysisModel, forKey: .analysisModel)
            try c.encode(style, forKey: .style)
            if let cap { try c.encode(cap, forKey: .monthlyCapUsd) }
        }
    }

    private func save(capValid: Bool) async {
        guard capValid else {
            message = (false, "The cap is a dollar amount from 0 to 1000, or blank.")
            return
        }
        saving = true
        defer { saving = false }
        do {
            try await api.put("/api/settings/coach", CoachIn(enabled: enabled, briefModel: brief, analysisModel: analysis, style: style,
                                                          cap: capDirty ? .some(capValue) : .none))
            message = (true, "Saved. The next brief and analysis use these settings.")
            await reload()
        } catch {
            message = (false, error.localizedDescription)
        }
    }
}

// MARK: Account

struct AccountPage: View {
    @EnvironmentObject private var api: API

    var body: some View {
        Loader(load: { try await api.get("/api/account", as: Account.self) }) { a, reload in
            ScrollView {
                VStack(alignment: .leading, spacing: 14) { AccountCard(account: a, reload: reload) }.padding()
            }
        }
        .background(Color(uiColor: .systemGroupedBackground))
        .navigationTitle("Account")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct AccountCard: View {
    let account: Account
    let reload: () async -> Void
    @EnvironmentObject private var api: API
    @EnvironmentObject private var session: SessionModel
    @State private var current = ""
    @State private var new = ""
    @State private var pwMessage: (ok: Bool, text: String)?
    @State private var busy: String?
    @State private var googleMessage: (ok: Bool, text: String)?
    @State private var export: URL?
    @State private var exportError: String?
    @State private var confirming = false
    @State private var delConfirm = ""
    @State private var delError: String?

    var body: some View {
        Card {
            SectionLabel("Account")
            LabeledContent("Email", value: account.email + (account.isOwner ? " (owner)" : "")).font(.subheadline)
            LabeledContent("Since", value: Fmt.day(account.createdAt, .dateTime.month(.abbreviated).day().year())).font(.subheadline)
            if let g = account.google {
                HStack {
                    Text("Google: \(g.email ?? "linked")").font(.subheadline)
                    Spacer()
                    Button { Task { await unlinkGoogle() } } label: { BusyLabel(title: "Unlink Google", busy: busy == "google") }
                        .buttonStyle(.bordered)
                        .disabled(busy != nil || !account.hasPassword)
                }
                if !account.hasPassword { Text("Set a password first.").font(.caption).foregroundStyle(.secondary) }
            } else if account.googleAvailable {
                Text("Google not linked. Link it from the Talus web app.").font(.caption).foregroundStyle(.secondary)
            }
            if let googleMessage { FormMessage(ok: googleMessage.ok, text: googleMessage.text) }
        }
        Card {
            SectionLabel(account.hasPassword ? "Change password" : "Set a password")
            if !account.hasPassword {
                Text("You sign in with Google. Set a password to also sign in with email.").font(.caption).foregroundStyle(.secondary)
            } else {
                SecureField("Current password", text: $current).textContentType(.password).textFieldStyle(.roundedBorder)
            }
            SecureField("New password, at least 10 characters", text: $new).textContentType(.newPassword).textFieldStyle(.roundedBorder)
            Button { Task { await changePassword() } } label: {
                BusyLabel(title: account.hasPassword ? "Change password" : "Set password", busy: busy == "password")
            }
            .buttonStyle(.bordered)
            .disabled(busy != nil || (account.hasPassword && current.isEmpty) || new.count < 10)
            if let pwMessage { FormMessage(ok: pwMessage.ok, text: pwMessage.text) }
        }
        Card {
            SectionLabel("Your data")
            Text("A zip of your activities, raw files, settings and coach history. Keys are left out.").font(.caption).foregroundStyle(.secondary)
            if let export {
                ShareLink(item: export) { Label("Save or share the export", systemImage: "square.and.arrow.up") }
                    .buttonStyle(.borderedProminent)
            } else {
                Button { Task { await makeExport() } } label: { BusyLabel(title: busy == "export" ? "Preparing" : "Export my data", busy: busy == "export") }
                    .buttonStyle(.bordered)
                    .disabled(busy != nil)
            }
            if let exportError { FormMessage(ok: false, text: exportError) }
        }
        Card {
            if confirming {
                Text("This deletes your account, every activity and raw file, your settings and coach history. It can't be undone.\(account.isOwner ? " You own this Talus; other accounts keep working." : "")")
                    .font(.subheadline).foregroundStyle(Tone.bad.ink)
                Group {
                    if account.hasPassword {
                        SecureField("Password to confirm", text: $delConfirm).textContentType(.password)
                    } else {
                        TextField("Type your email to confirm", text: $delConfirm)
                            .keyboardType(.emailAddress).textInputAutocapitalization(.never).autocorrectionDisabled()
                    }
                }
                .textFieldStyle(.roundedBorder)
                HStack {
                    Button(role: .destructive) { Task { await remove() } } label: { BusyLabel(title: "Delete my account", busy: busy == "delete") }
                        .buttonStyle(.borderedProminent).tint(Tone.bad.ink)
                        .disabled(busy != nil || delConfirm.isEmpty)
                    Button("Keep it") { confirming = false; delConfirm = "" }.buttonStyle(.bordered)
                }
                if let delError { FormMessage(ok: false, text: delError) }
            } else {
                Button("Delete account…", role: .destructive) { confirming = true }.font(.subheadline)
            }
        }
    }

    private func unlinkGoogle() async {
        busy = "google"
        defer { busy = nil }
        do {
            try await api.post("/api/account/google/unlink", [String: String]())
            googleMessage = (true, "Google unlinked. Sign in with your email and password.")
            await reload()
        } catch {
            googleMessage = (false, error.localizedDescription)
        }
    }

    private func changePassword() async {
        struct Body: Encodable { let current: String; let new: String }
        busy = "password"
        defer { busy = nil }
        do {
            try await api.put("/api/account/password", Body(current: current, new: new))
            current = ""
            new = ""
            pwMessage = (true, "Password changed. Other devices are signed out.")
        } catch {
            pwMessage = (false, error.localizedDescription)
        }
    }

    private func makeExport() async {
        busy = "export"
        defer { busy = nil }
        exportError = nil
        do {
            let data = try await api.download("/api/account/export")
            let url = FileManager.default.temporaryDirectory.appendingPathComponent("talus-export-\(PlanText.iso(Date())).zip")
            try data.write(to: url)
            export = url
        } catch {
            exportError = error.localizedDescription
        }
    }

    private func remove() async {
        struct Body: Encodable { let password: String }
        busy = "delete"
        defer { busy = nil }
        do {
            try await api.post("/api/account/delete", Body(password: delConfirm))
            await api.signOut()
            await session.check(api)
        } catch {
            delError = error.localizedDescription
        }
    }
}

// MARK: Admin

/// The admin page: only ADMIN_EMAIL's account sees it. Counts only; nobody's activities,
/// check-ins or keys are shown.
struct AdminPage: View {
    @EnvironmentObject private var api: API

    var body: some View {
        Loader(load: { try await api.get("/api/admin", as: AdminOverview.self) }) { d, reload in
            ScrollView {
                VStack(alignment: .leading, spacing: 14) { AdminContent(d: d, reload: reload) }.padding()
            }
            .refreshable { await reload() }
        }
        .background(Color(uiColor: .systemGroupedBackground))
        .navigationTitle("Admin")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct AdminContent: View {
    let d: AdminOverview
    let reload: () async -> Void
    @EnvironmentObject private var api: API
    @State private var busy = false
    @State private var message: (ok: Bool, text: String)?

    private static let actions = ["disable": "Turned off sign-in", "enable": "Turned on sign-in", "sign-out": "Signed out everywhere",
                                  "coach_ceiling": "Changed coach ceiling", "signup_open": "Opened sign-ups",
                                  "signup_closed": "Closed sign-ups", "feature_on": "Turned on", "feature_off": "Turned off"]

    var body: some View {
        let month = Fmt.date(d.month).map { $0.formatted(.dateTime.month(.wide)) } ?? d.month
        Card {
            SectionLabel("Accounts (\(d.accounts.count))")
            ForEach(d.accounts) { a in
                NavigationLink { AdminAccountPage(a: a, month: month, reload: reload) } label: { AdminRow(a: a, month: month) }
                    .buttonStyle(.plain)
                if a.id != d.accounts.last?.id { Divider() }
            }
            Text("Counts only. Nobody's activities, check-ins or keys are shown here.").font(.caption).foregroundStyle(.secondary)
        }
        Card {
            HStack {
                SectionLabel("Sign-ups")
                Spacer()
                Pill(text: d.signup.open ? "Open" : "Closed", tone: d.signup.open ? .warn : .neutral)
            }
            Text((d.signup.open ? "Anyone who can reach this Talus can make an account, with email or Google." : "Nobody new can make an account. Existing accounts sign in as usual.")
                 + (d.signup.chosen == nil ? " Set by TALUS_SIGNUP until you change it here." : ""))
                .font(.caption).foregroundStyle(.secondary)
            Button(d.signup.open ? "Close sign-ups" : "Open sign-ups") { Task { await toggleSignup() } }
                .buttonStyle(.bordered).disabled(busy)
        }
        Card {
            SectionLabel("Feature flags")
            Toggle(isOn: Binding(get: { d.features.showAiCost }, set: { on in Task { await feature("show_ai_cost", on) } })) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Show AI cost").fontWeight(.semibold)
                    Text("Every coach reply (today's call, activity reads, plan, weekly review, chat) shows what its AI call cost, the model and its tokens. Everyone sees the cost of their own calls.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .disabled(busy)
        }
        if let message { FormMessage(ok: message.ok, text: message.text) }
        Card {
            SectionLabel("Recent admin actions")
            if d.log.isEmpty { Text("Nothing yet.").foregroundStyle(.secondary) }
            ForEach(d.log, id: \.id) { e in
                Text(logLine(e)).font(.caption)
            }
        }
        Card {
            SectionLabel("System")
            VStack(alignment: .leading, spacing: 6) {
                LabeledContent("Version", value: d.system.revision.map { String($0.prefix(7)) } ?? "unknown")
                LabeledContent("Scoring model", value: d.system.modelVersion)
                LabeledContent("Coach") {
                    Text("Brief \(d.system.coach.briefModel), analysis \(d.system.coach.analysisModel), prompt \(d.system.coach.promptVersion)\(d.system.coach.keySet ? "" : " (no Anthropic key)")")
                        .multilineTextAlignment(.trailing)
                }
                LabeledContent("Coach ceiling", value: "\(SettingsText.usd(d.system.serverCeilingUsd)) a month")
                LabeledContent("Data folder", value: SettingsText.bytes(d.system.dataBytes))
                LabeledContent("Disk", value: "\(SettingsText.bytes(d.system.disk.free)) free of \(SettingsText.bytes(d.system.disk.total))")
            }
            .font(.subheadline)
        }
    }

    private func logLine(_ e: AdminOverview.LogEntry) -> String {
        var s = Self.actions[e.action] ?? e.action
        if let t = e.target { s += " for \(t)" }
        if e.action.hasPrefix("feature_"), let f = e.detail?.feature { s += " " + (f == "show_ai_cost" ? "Show AI cost" : f) }
        if e.action == "coach_ceiling" { s += " to \(e.detail?.to.map(SettingsText.usd) ?? "the default")" }
        return "\(s) · \(SettingsText.when(e.at))"
    }

    private func toggleSignup() async {
        struct Body: Encodable { let open: Bool }
        busy = true
        defer { busy = false }
        do {
            try await api.put("/api/admin/signup", Body(open: !d.signup.open))
            message = (true, d.signup.open ? "Sign-ups closed." : "Sign-ups open.")
            await reload()
        } catch {
            message = (false, error.localizedDescription)
        }
    }

    private func feature(_ key: String, _ on: Bool) async {
        struct Body: Encodable { let on: Bool }
        busy = true
        defer { busy = false }
        do {
            try await api.put("/api/admin/features/\(key)", Body(on: on))
            message = (true, "Show AI cost is \(on ? "on" : "off").")
            await reload()
        } catch {
            message = (false, error.localizedDescription)
        }
    }
}

private func ago(_ iso: String?) -> String {
    guard let iso, let d = ISO8601DateFormatter().date(from: iso) ?? {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return f.date(from: String(iso.prefix(19)))
    }() else { return "Never" }
    let mins = Int(Date().timeIntervalSince(d) / 60)
    if mins < 10 { return "Just now" }
    if mins < 60 { return "\(mins) min ago" }
    if mins < 48 * 60 { return "\(Int((Double(mins) / 60).rounded())) h ago" }
    return d.formatted(.dateTime.month(.abbreviated).day().year())
}

private struct AdminRow: View {
    let a: AdminAccount
    let month: String

    var body: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(a.email).fontWeight(.semibold).lineLimit(1)
                    if a.isOwner { Pill(text: "Owner", tone: .neutral) }
                    if a.disabledAt != nil { Pill(text: "Off", tone: .bad) }
                }
                Text("\(a.activities.formatted()) activities · \(a.connected.isEmpty ? (a.sources.isEmpty ? "no source" : a.sources.joined(separator: ", ")) : a.connected.joined(separator: ", "))")
                    .font(.caption).foregroundStyle(.secondary)
                Text("Active \(ago(a.lastSeen).lowercased()) · coach \(SettingsText.usd(a.coach.spent)) / \(a.coach.limit.map(SettingsText.usd) ?? "no cap") in \(month)")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Image(systemName: "chevron.right").font(.caption).foregroundStyle(.tertiary)
        }
        .contentShape(Rectangle())
    }
}

private struct AdminAccountPage: View {
    let a: AdminAccount
    let month: String
    let reload: () async -> Void
    @EnvironmentObject private var api: API
    @State private var busy: String?
    @State private var ceiling: String
    @State private var message: (ok: Bool, text: String)?

    init(a: AdminAccount, month: String, reload: @escaping () async -> Void) {
        self.a = a
        self.month = month
        self.reload = reload
        _ceiling = State(initialValue: a.coach.customCeiling ? a.coach.ceiling.map { String(format: "%g", $0) } ?? "" : "")
    }

    var body: some View {
        Form {
            Section {
                LabeledContent("Sign-in", value: a.signIn.isEmpty ? "None" : a.signIn.map { $0 == "google" ? "Google" : "Password" }.joined(separator: ", "))
                LabeledContent("Created", value: Fmt.day(a.createdAt, .dateTime.month(.abbreviated).day().year()))
                LabeledContent("Last active", value: ago(a.lastSeen))
                LabeledContent("Activities", value: a.activities.formatted())
                LabeledContent("Data source", value: a.connected.isEmpty ? (a.sources.isEmpty ? "None" : a.sources.joined(separator: ", ")) : a.connected.joined(separator: ", "))
                LabeledContent("Last sync", value: a.lastSync.map { "\(ago($0.at))\($0.ok ? "" : " (failed)")" } ?? "Never")
                LabeledContent("Coach, \(month)", value: "\(SettingsText.usd(a.coach.spent)) / \(a.coach.limit.map(SettingsText.usd) ?? "no cap")")
                LabeledContent("Sessions", value: "\(a.sessions) active")
                LabeledContent("On disk", value: SettingsText.bytes(a.diskBytes))
                if let own = a.coach.ownCap { LabeledContent("Their own cap", value: SettingsText.usd(own)) }
            }
            if a.isAdmin {
                Section { Text("Your account. Change it from Settings.").foregroundStyle(.secondary) }
            } else {
                Section {
                    if a.disabledAt != nil {
                        Button { Task { await run("enable", "POST", "/api/admin/accounts/\(a.id)/enable", "\(a.email) can sign in again.") } } label: {
                            BusyLabel(title: "Turn on sign-in", busy: busy == "enable")
                        }
                    } else {
                        Button(role: .destructive) { Task { await run("disable", "POST", "/api/admin/accounts/\(a.id)/disable", "\(a.email) is signed out and can't sign in.") } } label: {
                            BusyLabel(title: "Turn off sign-in", busy: busy == "disable")
                        }
                    }
                    Button { Task { await run("sign-out", "POST", "/api/admin/accounts/\(a.id)/sign-out", "\(a.email) is signed out everywhere.") } } label: {
                        BusyLabel(title: "Sign out everywhere", busy: busy == "sign-out")
                    }
                    .disabled(a.sessions == 0)
                }
                .disabled(busy != nil)
                if !a.isOwner {
                    Section {
                        TextField("Default", text: $ceiling).keyboardType(.decimalPad)
                        Button { Task { await saveCeiling() } } label: { BusyLabel(title: "Save coach ceiling", busy: busy == "ceiling") }
                            .disabled(busy != nil || !(ceiling.isEmpty || (Double(ceiling).map { $0 >= 0 && $0 <= 1000 } ?? false)))
                    } header: {
                        Text("Coach ceiling, $ a month")
                    } footer: {
                        Text(a.coach.ceiling.map { "Now \(SettingsText.usd($0))\(a.coach.customCeiling ? "" : " (server default")\(a.coach.customCeiling ? "" : ")"). Blank goes back to the server default." } ?? "Blank goes back to the server default.")
                    }
                }
            }
            if let message { Section { FormMessage(ok: message.ok, text: message.text) } }
        }
        .navigationTitle(a.email)
        .navigationBarTitleDisplayMode(.inline)
    }

    private func run(_ key: String, _ method: String, _ path: String, _ ok: String) async {
        busy = key
        defer { busy = nil }
        do {
            try await api.post(path, [String: String]())
            message = (true, ok)
            await reload()
        } catch {
            message = (false, error.localizedDescription)
        }
    }

    /// ceiling_usd is required, so a blank goes as an explicit null.
    private struct Ceiling: Encodable {
        let value: Double?
        enum CodingKeys: String, CodingKey { case ceilingUsd }
        func encode(to encoder: Encoder) throws {
            var c = encoder.container(keyedBy: CodingKeys.self)
            try c.encode(value, forKey: .ceilingUsd)
        }
    }

    private func saveCeiling() async {
        busy = "ceiling"
        defer { busy = nil }
        let v = Double(ceiling)
        do {
            try await api.put("/api/admin/accounts/\(a.id)/coach-ceiling", Ceiling(value: v))
            message = (true, v.map { "Coach ceiling set to \(SettingsText.usd($0)) a month." } ?? "Coach ceiling back to the server default.")
            await reload()
        } catch {
            message = (false, error.localizedDescription)
        }
    }
}
