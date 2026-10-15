import SwiftUI
import UniformTypeIdentifiers

/// A labeled text field in the web's field style: the label above, a hint below.
struct LabeledField: View {
    let label: String
    @Binding var text: String
    var placeholder = ""
    var hint: String?
    var keyboard: UIKeyboardType = .default
    var secure = false

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.subheadline.weight(.semibold))
            Group {
                if secure { SecureField(placeholder, text: $text) } else { TextField(placeholder, text: $text) }
            }
            .keyboardType(keyboard)
            .textFieldStyle(.roundedBorder)
            if let hint { Text(hint).font(.caption).foregroundStyle(.secondary) }
        }
    }
}

/// A message under a form: green when it worked, red when it didn't.
struct FormMessage: View {
    let ok: Bool
    let text: String

    var body: some View {
        Text(text).font(.subheadline).foregroundStyle(ok ? Tone.good.ink : Tone.bad.ink)
    }
}

enum Num {
    /// "8,000" reads as 8000; blank is nil.
    static func parse(_ v: String) -> Double? {
        let t = v.replacingOccurrences(of: ",", with: "").trimmingCharacters(in: .whitespaces)
        return t.isEmpty ? nil : Double(t)
    }

    static func text(_ v: Double?, digits: Int = 0) -> String {
        guard let v else { return "" }
        return digits > 0 ? String(format: "%.\(digits)f", v) : String(Int(v.rounded()))
    }
}

extension UTType {
    static let gpx = UTType(filenameExtension: "gpx") ?? .xml
}

/// Add or edit the objective: a race or mountain day with a date (a GPX route fills the numbers
/// in; the track itself is not kept), or a goal with no race day, set by weekly targets or a
/// year's totals.
struct ObjectiveFields: View {
    let current: Objective?
    let startNew: Bool
    let submit: String
    let onSaved: () async -> Void
    @EnvironmentObject private var api: API
    @State private var goal = "race"
    @State private var name = ""
    @State private var day = Date().addingTimeInterval(86400 * 60)
    @State private var distance = ""
    @State private var gain = ""
    @State private var loss = ""
    @State private var longestDescent = ""
    @State private var highPoint = ""
    @State private var longestClimb = ""
    @State private var source = "manual"
    @State private var picking = false
    @State private var more = false
    @State private var busy = false
    @State private var message: (ok: Bool, text: String)?

    init(current: Objective?, startNew: Bool, submit: String, onSaved: @escaping () async -> Void) {
        self.current = current
        self.startNew = startNew
        self.submit = submit
        self.onSaved = onSaved
        if let c = current, !startNew {
            _goal = State(initialValue: c.goal ?? "race")
            _name = State(initialValue: c.name)
            _day = State(initialValue: Fmt.date(c.day) ?? Date())
            _distance = State(initialValue: Num.text(c.distanceMi, digits: 1))
            _gain = State(initialValue: Num.text(c.gainFt))
            _loss = State(initialValue: Num.text(c.lossFt))
            _longestDescent = State(initialValue: Num.text(c.longestDescentFt))
            _highPoint = State(initialValue: Num.text(c.highPointFt))
            _longestClimb = State(initialValue: Num.text(c.longestClimbFt))
            _source = State(initialValue: c.source)
        }
    }

    private var editing: Bool { current != nil && !startNew }
    private var event: Bool { PlanText.isEvent(goal) }
    private var year: Bool { goal == "target" }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Picker("Training for", selection: Binding { goal } set: { pick($0) }) {
                ForEach(PlanText.goals, id: \.key) { Text($0.label).tag($0.key) }
            }
            .font(.subheadline.weight(.semibold))
            if event {
                Button { picking = true } label: { Label("Fill in from a GPX of the route", systemImage: "doc.badge.plus") }
                    .buttonStyle(.bordered)
                    .fileImporter(isPresented: $picking, allowedContentTypes: [.gpx, .xml]) { result in
                        if case .success(let url) = result { Task { await readRoute(url) } }
                    }
            } else {
                Text(year ? "Your total for the year: miles, feet of climbing, or both. The plan spreads what's left over the weeks left."
                     : goal == "rolling" ? "About where you want a week to sit. The plan looks 8 weeks ahead and redraws itself each Monday."
                     : "The weekly miles and vert to build to (or hold). The plan gets there from your last four weeks inside the load limits.")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            LabeledField(label: "Name", text: $name, placeholder: event ? "Fall 50k" : PlanText.goalText(goal))
            DatePicker(event ? "Date" : year ? "By" : "Runs through", selection: $day,
                       in: Date().addingTimeInterval(86400)..., displayedComponents: .date)
                .font(.subheadline.weight(.semibold))
            LabeledField(label: event ? "Distance, mi" : year ? "Miles this year" : "Miles a week", text: $distance,
                         placeholder: event ? "31" : year ? "Optional" : "35", keyboard: .decimalPad)
            HStack(alignment: .top) {
                LabeledField(label: event ? "Gain, ft" : year ? "Feet up this year" : "Feet up a week", text: $gain,
                             placeholder: event ? "8,000" : year ? "1,000,000" : "6,000", keyboard: .numberPad)
                if !year {
                    LabeledField(label: event ? "Descent, ft" : "Feet down a week", text: $loss, placeholder: "Same as gain", keyboard: .numberPad)
                }
            }
            if event {
                DisclosureGroup("More detail (optional): longest descent and high point", isExpanded: $more) {
                    HStack(alignment: .top) {
                        LabeledField(label: "Longest descent, ft", text: $longestDescent, placeholder: "Optional", keyboard: .numberPad)
                        LabeledField(label: "High point, ft", text: $highPoint, placeholder: "Optional", keyboard: .numberPad)
                    }
                    .padding(.top, 6)
                }
                .font(.subheadline)
            }
            Button { Task { await save() } } label: { BusyLabel(title: busy ? "Saving" : submit, busy: busy) }
                .buttonStyle(.borderedProminent)
                .disabled(!ready || busy)
            if !ready {
                Text(event ? "Name, date and distance are needed." : year ? "A name, a date, and miles or feet are needed." : "A name, a date and weekly miles are needed.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if let message { FormMessage(ok: message.ok, text: message.text) }
        }
    }

    private var ready: Bool {
        guard !name.trimmingCharacters(in: .whitespaces).isEmpty else { return false }
        let d = Num.parse(distance) ?? 0
        return year ? d > 0 || (Num.parse(gain) ?? 0) > 0 : d > 0
    }

    /// A block runs 12 weeks (an ongoing plan 8) from this Monday; a yearly target to Dec 31.
    private func pick(_ g: String) {
        goal = g
        guard !PlanText.isEvent(g) else { return }
        let cal = Calendar(identifier: .gregorian)
        let today = cal.startOfDay(for: Date())
        let monday = cal.date(byAdding: .day, value: -((cal.component(.weekday, from: today) + 5) % 7), to: today) ?? today
        let end = g == "target"
            ? cal.date(from: DateComponents(year: cal.component(.year, from: today), month: 12, day: 31)) ?? today
            : cal.date(byAdding: .day, value: (g == "rolling" ? 8 : 12) * 7 - 1, to: monday) ?? today
        day = max(end, today.addingTimeInterval(86400))
        let trimmed = name.trimmingCharacters(in: .whitespaces)
        if trimmed.isEmpty || PlanText.goals.contains(where: { $0.label == trimmed }) { name = PlanText.goalText(g) }
    }

    private func readRoute(_ url: URL) async {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        message = nil
        do {
            let data = try Data(contentsOf: url)
            let r = try api.decode(try await api.upload("/api/plan/route", file: data, contentType: "application/gpx+xml"), as: RouteNumbers.self)
            if name.isEmpty { name = r.name ?? "" }
            distance = Num.text(r.distanceMi, digits: 1)
            gain = Num.text(r.gainFt)
            loss = Num.text(r.lossFt)
            longestDescent = Num.text(r.longestDescentFt)
            highPoint = Num.text(r.highPointFt)
            longestClimb = Num.text(r.longestClimbFt)
            source = "gpx"
            message = (true, "Route read. Check the numbers, then save.")
        } catch {
            message = (false, error.localizedDescription)
        }
    }

    private func save() async {
        struct Body: Encodable {
            let name: String, day: String, distanceMi: Double, gainFt: Double, lossFt: Double
            let longestDescentFt: Double?, highPointFt: Double?, longestClimbFt: Double?, source: String, goal: String, new: Bool
        }
        busy = true
        defer { busy = false }
        message = nil
        let g = Num.parse(gain) ?? 0
        do {
            try await api.put("/api/plan/objective", Body(
                name: name.trimmingCharacters(in: .whitespaces), day: PlanText.iso(day), distanceMi: Num.parse(distance) ?? 0,
                gainFt: g, lossFt: year ? g : Num.parse(loss) ?? g,
                longestDescentFt: event ? Num.parse(longestDescent) : nil, highPointFt: event ? Num.parse(highPoint) : nil,
                longestClimbFt: event ? Num.parse(longestClimb) : nil, source: event ? source : "manual", goal: goal,
                new: !editing || goal != (current?.goal ?? "race")))
            await onSaved()
        } catch APIError.server(422, let detail) where detail.hasPrefix("The engine answered") {
            message = (false, "Check the date and numbers.")
        } catch {
            message = (false, error.localizedDescription)
        }
    }
}

/// Training days and time limits: the plan never puts more on a day than its limit.
struct ConstraintsFields: View {
    let hasObjective: Bool
    let submit: String
    let onSaved: () async -> Void
    @EnvironmentObject private var api: API
    @State private var c: Constraints
    @State private var weekday: String
    @State private var weekend: String
    @State private var busy = false
    @State private var message: (ok: Bool, text: String)?

    init(current: Constraints, hasObjective: Bool, submit: String, onSaved: @escaping () async -> Void) {
        self.hasObjective = hasObjective
        self.submit = submit
        self.onSaved = onSaved
        var c = current
        c.workout = c.workout ?? "none"
        c.backToBack = c.backToBack ?? true
        _c = State(initialValue: c)
        _weekday = State(initialValue: String(current.weekdayMin))
        _weekend = State(initialValue: String(current.weekendMin))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Days you can train").font(.subheadline.weight(.semibold))
            HStack(spacing: 4) {
                ForEach(0..<7, id: \.self) { d in
                    let on = c.days.contains(d)
                    Button {
                        c.days = on ? c.days.filter { $0 != d } : (c.days + [d]).sorted()
                    } label: {
                        Text(PlanText.dayShort[d]).font(.caption.weight(.medium))
                            .frame(maxWidth: .infinity, minHeight: 36)
                            .foregroundStyle(on ? Color(uiColor: .systemBackground) : .primary)
                            .background(on ? Color.primary : Color.secondary.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(PlanText.dayNames[d])
                    .accessibilityAddTraits(on ? .isSelected : [])
                }
            }
            HStack(alignment: .top) {
                LabeledField(label: "Weekday limit, min", text: $weekday, keyboard: .numberPad)
                LabeledField(label: "Weekend limit, min", text: $weekend, keyboard: .numberPad)
            }
            Picker("Long day", selection: $c.longDay) {
                ForEach(c.days, id: \.self) { Text(PlanText.dayNames[$0]).tag($0) }
            }
            .font(.subheadline.weight(.semibold))
            Picker("Speed work, once a week", selection: Binding { c.workout ?? "none" } set: { c.workout = $0 }) {
                ForEach(PlanText.workouts, id: \.key) { Text($0.label).tag($0.key) }
            }
            .font(.subheadline.weight(.semibold))
            LabeledField(label: "Notes, such as travel dates", text: $c.notes)
            Toggle(isOn: Binding { c.backToBack ?? true } set: { c.backToBack = $0 }) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Back-to-back long days for long objectives").font(.subheadline)
                    Text("About 7 h or more: two long days in a row instead of one huge one").font(.caption).foregroundStyle(.secondary)
                }
            }
            Button { Task { await save() } } label: {
                BusyLabel(title: busy ? (hasObjective ? "Drafting your plan" : "Saving") : submit, busy: busy)
            }
            .buttonStyle(.borderedProminent)
            .disabled(busy || !c.days.contains(c.longDay))
            if !c.days.contains(c.longDay) {
                Text("Your long day has to be one of your training days.").font(.caption).foregroundStyle(.secondary)
            }
            if let message { FormMessage(ok: message.ok, text: message.text) }
        }
    }

    private func save() async {
        busy = true
        defer { busy = false }
        message = nil
        var body = c
        body.weekdayMin = Int(weekday) ?? 0
        body.weekendMin = Int(weekend) ?? 0
        do {
            try await api.put("/api/plan/constraints", body)
            await onSaved()
        } catch APIError.server(422, let detail) where detail.hasPrefix("The engine answered") {
            message = (false, "Check the days and minutes: weekdays 15 to 720, weekends 15 to 1440.")
        } catch {
            message = (false, error.localizedDescription)
        }
    }
}

// MARK: Calendar

/// The calendar of scheduled runs: your own, and the plan's next 7 days. Add a run on any day,
/// change its date to move it, or delete it. Editing a plan session saves it as your own run.
struct CalendarCard: View {
    let initial: Schedule
    let compact: Bool
    let onChange: () async -> Void
    @EnvironmentObject private var api: API
    @State private var data: Schedule
    @State private var start: String
    @State private var all: Bool
    @State private var loading = false
    @State private var editing: RunDraft?

    private static let weeks = 5

    init(initial: Schedule, compact: Bool, onChange: @escaping () async -> Void) {
        self.initial = initial
        self.compact = compact
        self.onChange = onChange
        _data = State(initialValue: initial)
        _start = State(initialValue: initial.start)
        _all = State(initialValue: !compact)
    }

    var body: some View {
        let byDay = Dictionary(grouping: data.items, by: \.day)
        let ran = Dictionary(grouping: data.activities ?? [], by: \.day)
        let weekStarts = (0..<(all ? Self.weeks : 2)).map { PlanText.addDays(start, $0 * 7) }
        let progress = Dictionary(data.weeks.map { ($0.start, $0) }, uniquingKeysWith: { a, _ in a })
        let warned = Dictionary(data.warnings.map { ($0.week, $0) }, uniquingKeysWith: { a, _ in a })
        Card {
            HStack {
                SectionLabel(all ? "Scheduled runs" : "This week and next")
                if loading { ProgressView().controlSize(.small) }
                Spacer()
                if compact {
                    Button(all ? "Show 2 weeks" : "Show 5 weeks") {
                        if all { start = initial.start; data = initial }
                        all.toggle()
                        if all { Task { await load() } }
                    }
                    .font(.subheadline)
                }
            }
            if all {
                HStack {
                    Button("Earlier") { move(-7 * Self.weeks) }
                    Spacer()
                    Button("This week") { start = initial.start; Task { await load() } }
                    Spacer()
                    Button("Later") { move(7 * Self.weeks) }
                }
                .font(.subheadline)
                .buttonStyle(.bordered)
            }
            ForEach(weekStarts, id: \.self) { ws in
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text("Week of \(Fmt.day(ws, .dateTime.month(.abbreviated).day()))").font(.subheadline.weight(.semibold))
                        Spacer()
                    }
                    ForEach(0..<7, id: \.self) { i in
                        let d = PlanText.addDays(ws, i)
                        DayRow(day: d, today: data.date, items: byDay[d] ?? [], ran: ran[d] ?? []) { run in
                            editing = run.map { RunDraft($0) } ?? RunDraft(day: d)
                        }
                    }
                    WeekLine(w: progress[ws], past: PlanText.addDays(ws, 6) < data.date)
                    if let w = warned[ws] { Text(w.text).font(.caption).foregroundStyle(Tone.warn.ink) }
                }
                .padding(10)
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(warned[ws] != nil ? Tone.warn.ink : Color.secondary.opacity(0.2),
                                                                  lineWidth: warned[ws] != nil ? 2 : 1))
                .opacity(loading ? 0.55 : 1)
            }
            Text("Tap + to add a run, or a run to change it; a new date moves it. Your own run replaces the plan's session that day. Runs match the activity you do that day.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .sheet(item: $editing) { draft in
            RunEditor(draft: draft, kinds: data.kinds, zones: data.zoneModel) {
                editing = nil
                await load()
                await onChange()
            }
        }
    }

    private func move(_ days: Int) {
        start = PlanText.addDays(start, days)
        Task { await load() }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        if let s = try? await api.get("/api/schedule?start=\(start)&days=\(Self.weeks * 7)", as: Schedule.self) { data = s }
    }
}

private struct DayRow: View {
    let day: String
    let today: String
    let items: [ScheduledRun]
    let ran: [UnmatchedActivity]
    let open: (ScheduledRun?) -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            VStack(alignment: .leading, spacing: 0) {
                Text(Fmt.day(day)).font(.caption.weight(day == today ? .bold : .regular))
                Text(Fmt.day(day, .dateTime.day())).font(.caption2).foregroundStyle(.secondary)
            }
            .frame(width: 34, alignment: .leading)
            .foregroundStyle(day < today ? .secondary : .primary)
            VStack(alignment: .leading, spacing: 4) {
                ForEach(Array(items.enumerated()), id: \.offset) { _, it in
                    Button { open(it) } label: { RunChip(run: it) }.buttonStyle(.plain)
                }
                // Activities no scheduled run matched, so every day you trained shows.
                ForEach(ran, id: \.id) { a in
                    NavigationLink(value: a.id) { DoneChip(activity: a) }.buttonStyle(.plain)
                }
            }
            Spacer(minLength: 0)
            Button { open(nil) } label: { Image(systemName: "plus.circle").imageScale(.medium) }
                .buttonStyle(.plain).foregroundStyle(.secondary)
                .accessibilityLabel("Add a run on \(Fmt.day(day, .dateTime.weekday(.wide).month(.wide).day()))")
        }
        .padding(.vertical, 2)
        .background(day == today ? Color.secondary.opacity(0.08) : .clear, in: RoundedRectangle(cornerRadius: 6))
    }
}

private struct RunChip: View {
    let run: ScheduledRun

    static func color(_ kind: String) -> Color {
        switch kind {
        case "easy": Ledger.color("cardio")
        case "long", "hike": Ledger.color("climb")
        case "hills": Ledger.color("descent")
        case "tempo", "intervals": .target
        case "race": .primary
        default: .secondary
        }
    }

    var body: some View {
        HStack(spacing: 6) {
            RoundedRectangle(cornerRadius: 1.5).fill(Self.color(run.kind)).frame(width: 3)
            VStack(alignment: .leading, spacing: 1) {
                Text(run.title + (run.source == "plan" ? " · plan" : "")).font(.caption.weight(.semibold))
                Text("\(Fmt.mi(run.miles)) mi · \(Fmt.n0(run.gainFt)) ft").font(.caption2.monospacedDigit())
                if let s = status {
                    Text(s).font(.caption2).foregroundStyle(run.status == "done" ? Tone.good.ink : run.status == "missed" ? Tone.bad.ink : .secondary)
                }
            }
        }
        .padding(.vertical, 3).padding(.trailing, 6)
        .background(run.source == "plan" ? Color.secondary.opacity(0.08) : Color.secondary.opacity(0.16), in: RoundedRectangle(cornerRadius: 6))
    }

    private var status: String? {
        let text = ["done": "Done", "missed": "Missed", "today": "Today"][run.status]
        guard let text else { return nil }
        if let a = run.activity { return "\(text): \(Fmt.mi(a.distanceMi)) mi, \(Fmt.n0(a.gainFt)) ft" }
        return text
    }
}

private struct DoneChip: View {
    let activity: UnmatchedActivity

    var body: some View {
        HStack(spacing: 6) {
            RoundedRectangle(cornerRadius: 1.5).fill(Tone.good.ink).frame(width: 3)
            VStack(alignment: .leading, spacing: 1) {
                Text(activity.name ?? "Activity").font(.caption.weight(.semibold)).lineLimit(1)
                Text("\(Fmt.mi(activity.distanceMi)) mi · \(Fmt.n0(activity.gainFt)) ft").font(.caption2.monospacedDigit())
                Text("Done").font(.caption2).foregroundStyle(Tone.good.ink)
            }
        }
        .padding(.vertical, 3).padding(.trailing, 6)
        .background(Color.secondary.opacity(0.16), in: RoundedRectangle(cornerRadius: 6))
    }
}

/// A line under each week: the plan's target against done plus scheduled, in mi and ft up.
private struct WeekLine: View {
    let w: WeekProgress?
    let past: Bool

    var body: some View {
        if let w {
            let have = ((w.done?.miles ?? 0) + w.ahead.miles, (w.done?.gainFt ?? 0) + w.ahead.gainFt)
            let all = w.cover
            let c = w.dated || all?.state == .over ? all : nil
            VStack(alignment: .leading, spacing: 2) {
                if let t = w.target {
                    Text("\(PlanText.focus(t.focus)) target \(Fmt.mi(t.miles)) mi · \(Fmt.n0(t.gainFt)) ft up").font(.caption.monospacedDigit())
                }
                Text("\(past ? "Done" : w.done != nil ? "Done + scheduled" : "Scheduled") \(Fmt.mi(have.0)) mi · \(Fmt.n0(have.1)) ft up")
                    .font(.caption).foregroundStyle(.secondary)
                if let c {
                    Text(coverText(c)).font(.caption.weight(.semibold)).foregroundStyle(coverTone(c.state).ink)
                } else if w.target != nil {
                    Text("The plan's days show a week ahead").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }

    private func coverText(_ c: (share: [String: Double], state: WeekProgress.Cover)) -> String {
        switch c.state {
        case .covered: return "Covered"
        case .over: return "Over target: \(Int(((c.share.values.max() ?? 0) * 100).rounded()))%"
        default: return "\(Int((min(c.share["miles"] ?? 0, c.share["gain_ft"] ?? 0) * 100).rounded()))% of target"
        }
    }

    private func coverTone(_ s: WeekProgress.Cover) -> Tone {
        switch s {
        case .covered: .good
        case .short: .bad
        default: .warn
        }
    }
}

/// A scheduled run being added or edited. A plan session saves as your own run.
struct RunDraft: Identifiable {
    let id = UUID()
    var runId: Int?
    var fromPlan = false
    var day: Date
    var kind = "easy"
    var miles = ""
    var gain = ""
    var loss = ""
    var minutes = ""
    var zone: Int?
    var notes = ""
    /// How to run the plan's session, shown while editing it.
    var how: String?

    init(day: String) {
        self.day = Fmt.date(day) ?? Date()
    }

    init(_ r: ScheduledRun) {
        runId = r.source == "manual" ? r.id : nil
        fromPlan = r.source == "plan"
        day = Fmt.date(r.day) ?? Date()
        kind = r.kind
        miles = String(format: "%.1f", r.miles)
        gain = Num.text(r.gainFt)
        loss = Num.text(r.lossFt)
        minutes = r.estimatedMinutes ? "" : Num.text(r.minutes)
        zone = r.zone
        notes = r.notes
        how = r.source == "plan" ? r.how : nil
    }
}

private struct RunEditor: View {
    @State var draft: RunDraft
    let kinds: [RunKind]
    let zones: ZoneModel?
    let onDone: () async -> Void
    @EnvironmentObject private var api: API
    @Environment(\.dismiss) private var dismiss
    @State private var busy: String?
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                if draft.fromPlan {
                    Section {
                        if let how = draft.how, !how.isEmpty { Text(how).font(.subheadline) }
                        Text("Saving changes the plan's session into your own run.").font(.subheadline).foregroundStyle(.secondary)
                    }
                }
                Section {
                    DatePicker("Date", selection: $draft.day, displayedComponents: .date)
                    Picker("Type", selection: $draft.kind) {
                        ForEach(kinds, id: \.key) { Text($0.label).tag($0.key) }
                    }
                    row("Distance, mi", $draft.miles, "", .decimalPad)
                    row("Gain, ft", $draft.gain, "", .numberPad)
                    row("Descent, ft", $draft.loss, "Same as gain", .numberPad)
                    row("Time, min", $draft.minutes, "From your pace", .numberPad)
                    Picker("Zone", selection: $draft.zone) {
                        Text("Any").tag(Int?.none)
                        ForEach(zones?.list ?? [], id: \.zone) { z in
                            Text("Zone \(z.zone), \(z.name.lowercased()) (\(z.text))").tag(Int?.some(z.zone))
                        }
                    }
                    TextField("Notes", text: $draft.notes)
                }
                Section {
                    Button { Task { await save() } } label: { BusyLabel(title: busy == "save" ? "Saving" : "Save run", busy: busy == "save") }
                        .disabled(busy != nil)
                    if draft.runId != nil {
                        Button(role: .destructive) { Task { await remove() } } label: { BusyLabel(title: "Delete", busy: busy == "delete") }
                            .disabled(busy != nil)
                    }
                }
                if let error { Section { Text(error).foregroundStyle(Tone.bad.ink) } }
            }
            .navigationTitle(draft.runId != nil || draft.fromPlan ? "Edit run" : "Add a run")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
        }
    }

    private func row(_ label: String, _ text: Binding<String>, _ placeholder: String, _ keyboard: UIKeyboardType) -> some View {
        LabeledContent(label) {
            TextField(placeholder, text: text).keyboardType(keyboard).multilineTextAlignment(.trailing)
        }
    }

    private func save() async {
        struct Body: Encodable {
            let day: String, kind: String, miles: Double, gainFt: Double, lossFt: Double?, minutes: Double?, zone: Int?, notes: String
        }
        busy = "save"
        defer { busy = nil }
        let body = Body(day: PlanText.iso(draft.day), kind: draft.kind, miles: Num.parse(draft.miles) ?? 0, gainFt: Num.parse(draft.gain) ?? 0,
                        lossFt: Num.parse(draft.loss), minutes: Num.parse(draft.minutes), zone: draft.zone, notes: draft.notes)
        do {
            if let id = draft.runId { try await api.put("/api/schedule/\(id)", body) } else { try await api.post("/api/schedule", body) }
            await onDone()
        } catch APIError.server(422, let detail) where detail.hasPrefix("The engine answered") {
            error = "Check the date and numbers."
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func remove() async {
        guard let id = draft.runId else { return }
        busy = "delete"
        defer { busy = nil }
        do {
            try await api.delete("/api/schedule/\(id)")
            await onDone()
        } catch {
            self.error = "Could not delete. \(error.localizedDescription)"
        }
    }
}
