import Charts
import SwiftUI

/// Everything the Plan tab reads, fetched together.
struct PlanBundle {
    let page: PlanPage
    let schedule: Schedule
    let chat: [ChatMessage]
}

struct PlanView: View {
    @EnvironmentObject private var api: API

    var body: some View {
        NavigationStack {
            Loader(load: load) { b, reload in
                PlanScreen(b: b, reload: reload)
            }
            .background(Color(uiColor: .systemGroupedBackground))
            .navigationDestination(for: Int.self) { ActivityDetailView(id: $0) }
        }
    }

    private func load() async throws -> PlanBundle {
        // Main-actor tasks, so the three requests run at once and decode on the main actor.
        let page = Task { try await api.get("/api/plan", as: PlanPage.self) }
        let schedule = Task { try await api.get("/api/schedule", as: Schedule.self) }
        let chat = Task { try await api.get("/api/coach/chat", as: ChatLog.self) }
        return try await PlanBundle(page: page.value, schedule: schedule.value, chat: chat.value.messages)
    }
}

private struct PlanScreen: View {
    let b: PlanBundle
    let reload: () async -> Void
    @EnvironmentObject private var api: API
    @State private var editing: Edit?
    @State private var confirmDelete = false
    @State private var error: String?

    enum Edit: String, Identifiable {
        case objective, new, days
        var id: String { rawValue }
    }

    var body: some View {
        let p = b.page
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                if let obj = p.objective, let accepted = p.accepted {
                    header(obj)
                    if let error { Text(error).font(.subheadline).foregroundStyle(Tone.bad.ink) }
                    if let draft = p.draft { DraftCard(draft: draft, hasAccepted: true, reload: reload) }
                    NextRunCard(sessions: p.sessions, today: p.date)
                    if let w = p.thisWeek { ThisWeekCard(w: w) }
                    CalendarCard(initial: b.schedule, compact: true, onChange: reload)
                    RoadCard(obj: obj, plan: accepted, current: PlanScreen.weekStart(p.date), actuals: p.actuals ?? [:])
                    if let r = p.readiness { ReadinessPlanCard(r: r, projected: accepted.projected, note: p.fitnessOnly) }
                    Card {
                        SectionLabel("Coach")
                        CoachPlanSection()
                        if let review = p.review { ReviewSection(fallback: review, reload: reload) }
                    }
                    ChatCard(initial: b.chat)
                } else {
                    PlanSetup(p: p, reload: reload)
                    CalendarCard(initial: b.schedule, compact: false, onChange: reload)
                    ChatCard(initial: b.chat)
                }
            }
            .padding()
        }
        .refreshable { await reload() }
        .navigationTitle(p.objective != nil && p.accepted != nil ? p.objective!.name : "Plan")
        .toolbar {
            if let obj = p.objective, p.accepted != nil {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button("Edit objective") { editing = .objective }
                        Button("Training days") { editing = .days }
                        Button("A different objective") { editing = .new }
                        Divider()
                        Button("Delete plan", role: .destructive) { confirmDelete = true }
                    } label: {
                        Label("Change the plan", systemImage: "ellipsis.circle")
                    }
                    .confirmationDialog("Delete \(obj.name) and its plan? Your own scheduled runs stay.",
                                        isPresented: $confirmDelete, titleVisibility: .visible) {
                        Button("Delete", role: .destructive) { Task { await deletePlan() } }
                    }
                }
            }
        }
        .sheet(item: $editing) { e in
            NavigationStack {
                ScrollView {
                    Card {
                        if e != .days {
                            Text(e == .new ? "Saving a new objective retires this one and its plan." : "Saving drafts a new plan to review against this one.")
                                .font(.subheadline).foregroundStyle(.secondary)
                        }
                        sheetFields(e, p)
                    }
                    .padding()
                }
                .background(Color(uiColor: .systemGroupedBackground))
                .navigationTitle(e == .days ? "Training days" : e == .new ? "A different objective" : "Edit the objective")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { editing = nil } } }
            }
        }
    }

    @ViewBuilder
    private func sheetFields(_ e: Edit, _ p: PlanPage) -> some View {
        switch e {
        case .objective, .new:
            ObjectiveFields(current: p.objective, startNew: e == .new,
                            submit: e == .new ? "Save and build a plan" : "Save and redraft the plan") {
                editing = nil
                await reload()
            }
        case .days:
            ConstraintsFields(current: p.constraints, hasObjective: true, submit: "Save training days") {
                editing = nil
                await reload()
            }
        }
    }

    private func header(_ obj: Objective) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("\(Fmt.day(obj.day, .dateTime.weekday(.wide).month(.wide).day())) · \(obj.daysToGo) days to go")
                .font(.subheadline).foregroundStyle(.secondary)
            Text(Fmt.distVert(mi: obj.distanceMi, gain: obj.gainFt, loss: obj.lossFt)).font(.subheadline.monospacedDigit())
        }
    }

    private func deletePlan() async {
        do {
            try await api.delete("/api/plan/objective")
            error = nil
            await reload()
        } catch {
            self.error = "Could not delete: \(error.localizedDescription)"
        }
    }

    static func weekStart(_ iso: String) -> String {
        guard let d = Fmt.date(iso) else { return iso }
        let wd = (Calendar(identifier: .gregorian).component(.weekday, from: d) + 5) % 7  // Monday 0
        return PlanText.addDays(iso, -wd)
    }
}

// MARK: Setup

/// Setting up a plan one step at a time: the objective, the days you can train, then the drafted
/// plan to review and accept. Shown until a plan is accepted.
private struct PlanSetup: View {
    let p: PlanPage
    let reload: () async -> Void
    @State private var step: Step

    enum Step: Int, CaseIterable {
        case objective, days, review
        var label: String { ["Objective", "Training days", "Review and accept"][rawValue] }
    }

    init(p: PlanPage, reload: @escaping () async -> Void) {
        self.p = p
        self.reload = reload
        _step = State(initialValue: p.objective == nil ? .objective : .review)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Build a plan in three steps").font(.subheadline).foregroundStyle(.secondary)
            stepper
            switch step {
            case .objective:
                Card {
                    Text("What are you training for?").font(.title3.weight(.semibold))
                    Text("A race or a mountain day with a date. Pick the route's GPX, or type its distance and vert.")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                FormCard {
                    ObjectiveFields(current: p.objective, startNew: false, submit: "Next: training days") {
                        await reload()
                        step = .days
                    }
                }
            case .days:
                Card {
                    Text("When can you train?").font(.title3.weight(.semibold))
                    Text("The plan only puts runs on these days, keeps each day inside its time limit, and puts the long day where you choose.")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                FormCard {
                    ConstraintsFields(current: p.constraints, hasObjective: p.objective != nil,
                                      submit: p.objective != nil ? "Next: draft my plan" : "Next") {
                        await reload()
                        step = p.objective != nil ? .review : .objective
                    }
                }
            case .review:
                if let obj = p.objective { SetupReview(p: p, obj: obj, reload: reload, change: { step = $0 }) }
            }
        }
    }

    private var stepper: some View {
        HStack(spacing: 6) {
            ForEach(Step.allCases, id: \.self) { s in
                let reachable = s != .review || p.objective != nil
                Button { step = s } label: {
                    HStack(spacing: 6) {
                        Text(s.rawValue < step.rawValue ? "✓" : "\(s.rawValue + 1)")
                            .font(.caption.weight(.semibold))
                            .frame(width: 22, height: 22)
                            .foregroundStyle(s.rawValue <= step.rawValue ? Color(uiColor: .systemBackground) : .secondary)
                            .background(s == step ? Color.primary : s.rawValue < step.rawValue ? Tone.good.ink : Color.secondary.opacity(0.15), in: Circle())
                        Text(s.label).font(.caption.weight(s == step ? .semibold : .regular)).lineLimit(1)
                    }
                }
                .buttonStyle(.plain)
                .disabled(!reachable || s == step)
                .accessibilityAddTraits(s == step ? .isSelected : [])
                if s != .review { Image(systemName: "chevron.right").font(.caption2).foregroundStyle(.secondary) }
            }
        }
    }
}

private struct SetupReview: View {
    let p: PlanPage
    let obj: Objective
    let reload: () async -> Void
    let change: (PlanSetup.Step) -> Void
    @EnvironmentObject private var api: API
    @State private var confirmDelete = false

    var body: some View {
        if let draft = p.draft {
            Card {
                HStack(alignment: .top) {
                    Text("Your plan for \(obj.name)").font(.title3.weight(.semibold))
                    Spacer()
                    Pill(text: draft.reachable ? "Reachable" : "Out of reach", tone: draft.reachable ? .good : .bad)
                }
                Text("\(Fmt.day(obj.day, .dateTime.weekday(.wide).month(.wide).day())) · \(obj.daysToGo) days to go · \(Fmt.mi(obj.distanceMi)) mi, \(Fmt.n0(obj.gainFt)) ft up, \(Fmt.n0(obj.lossFt)) ft down")
                    .font(.subheadline).foregroundStyle(.secondary)
                Text(Self.summary(draft, obj.name))
                if !draft.reachable { OutOfReach(draft: draft) }
                PlanActions(draftId: draft.id, acceptOnly: true, reload: reload)
                HStack(spacing: 16) {
                    Button("Change the objective") { change(.objective) }
                    Button("Change training days") { change(.days) }
                }
                .font(.subheadline)
            }
            Card {
                SectionLabel("The first weeks")
                WeeksList(weeks: Array(draft.weeks.prefix(6)), current: draft.weeks.first?.start ?? "", actuals: [:])
            }
            Card {
                SectionLabel("Coach's read")
                CoachPlanSection()
            }
            if let r = p.readiness { ReadinessPlanCard(r: r, projected: draft.projected, note: p.fitnessOnly) }
            Text("Nothing is on your calendar until you accept. You can change or delete the plan any time after.")
                .font(.subheadline).foregroundStyle(.secondary)
            deleteButton
        } else {
            Card {
                Text("No plan drafted yet").font(.title3.weight(.semibold))
                Text("Talus builds the plan from your last four weeks of training. Sync your recent activities, then draft it.")
                    .font(.subheadline).foregroundStyle(.secondary)
                PlanActions(draftId: nil, replan: true, reload: reload)
            }
            deleteButton
        }
    }

    private var deleteButton: some View {
        Button("Delete plan", role: .destructive) { confirmDelete = true }
            .confirmationDialog("Delete \(obj.name) and its plan? Your own scheduled runs stay.",
                                isPresented: $confirmDelete, titleVisibility: .visible) {
                Button("Delete", role: .destructive) {
                    Task {
                        try? await api.delete("/api/plan/objective")
                        await reload()
                    }
                }
            }
    }

    /// The plan in one paragraph, every number the engine's. Mirrors planSummary on the web.
    static func summary(_ plan: Plan, _ name: String) -> String {
        let taper = plan.weeks.first { $0.focus == "taper" || $0.focus == "race" }
        let big = plan.weeks.compactMap(\.long).max { $0.effortMi < $1.effortMi }
        var parts = ["\(plan.weeks.count) weeks to \(name).",
                     "You start from your last four weeks, about \(Fmt.mi(plan.baseline.miles)) mi and \(Fmt.n0(plan.baseline.gainFt)) ft up a week,",
                     "and build to a peak of \(Fmt.mi(plan.peak.miles)) mi with \(Fmt.n0(plan.peak.gainFt)) ft up and \(Fmt.n0(plan.peak.lossFt)) ft down,",
                     "with a recovery week after every three builds\(taper.map { ", then taper from \(Fmt.day($0.start, .dateTime.month(.abbreviated).day()))" } ?? "")."]
        if let big { parts.append("Your biggest long day is \(Fmt.mi(big.miles)) mi with \(Fmt.n0(big.gainFt)) ft up.") }
        return parts.joined(separator: " ")
    }
}

private struct OutOfReach: View {
    let draft: Plan

    var body: some View {
        var text = "The engine can't get you there inside the load limits by this date, and it won't loosen them."
        if let later = draft.options.later { text += " It can by \(Fmt.day(later, .dateTime.month(.wide).day().year()))." }
        if let s = draft.options.smaller {
            text += " On this date, a version of about \(Fmt.mi(s.distanceMi)) mi with \(Fmt.n0((s.gainFt / 100).rounded() * 100)) ft up fits."
        }
        return Text(text).font(.subheadline)
    }
}

/// A grouped-form look for fields shown in the scrolling Plan page.
private struct FormCard<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        Card { content }
    }
}

/// Accept or decline a drafted plan, or ask the engine for a fresh one from today.
struct PlanActions: View {
    let draftId: Int?
    var acceptOnly = false
    var replan = false
    var reason = "request"
    let reload: () async -> Void
    @EnvironmentObject private var api: API
    @State private var busy: String?
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                if let id = draftId {
                    Button { Task { await go("/api/plan/\(id)/accept") } } label: {
                        BusyLabel(title: busy == "accept" ? "Accepting" : "Accept plan", busy: busy == "accept")
                    }
                    .buttonStyle(.borderedProminent)
                    if !acceptOnly {
                        Button { Task { await go("/api/plan/\(id)/decline") } } label: {
                            BusyLabel(title: "Keep my current plan", busy: busy == "decline")
                        }
                        .buttonStyle(.bordered)
                    }
                }
                if replan {
                    Button { Task { await go("/api/plan/draft") } } label: {
                        BusyLabel(title: busy == "draft" ? "Building" : "Build a new plan from today", busy: busy == "draft")
                    }
                    .buttonStyle(.borderedProminent)
                }
            }
            .disabled(busy != nil)
            if let error { Text(error).font(.caption).foregroundStyle(Tone.bad.ink) }
        }
    }

    private func go(_ path: String) async {
        struct Draft: Encodable { let reason: String }
        busy = path.hasSuffix("accept") ? "accept" : path.hasSuffix("decline") ? "decline" : "draft"
        defer { busy = nil }
        do {
            try await api.post(path, Draft(reason: reason))
            error = nil
            await reload()
        } catch {
            self.error = error.localizedDescription
        }
    }
}

/// A drafted plan waiting for a decision: what changes against the accepted plan, or its weeks.
private struct DraftCard: View {
    let draft: Plan
    let hasAccepted: Bool
    let reload: () async -> Void

    var body: some View {
        Card {
            HStack(alignment: .top) {
                SectionLabel("Drafted plan: \(draft.reasonText.lowercased())")
                Spacer()
                Pill(text: draft.reachable ? "Reachable" : "Out of reach", tone: draft.reachable ? .good : .bad)
            }
            if !draft.reachable { OutOfReach(draft: draft) }
            if hasAccepted {
                if draft.changes.isEmpty {
                    Text("No week moves by more than 5% against your accepted plan.").foregroundStyle(.secondary)
                } else {
                    ForEach(draft.changes, id: \.start) { c in
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Week of \(Fmt.day(c.start, .dateTime.month(.abbreviated).day()))").fontWeight(.semibold)
                            Text("Was \(c.before.map(Self.brief) ?? "–")").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                            Text("Becomes \(Self.brief(c.after))").font(.caption.monospacedDigit())
                        }
                    }
                }
            } else {
                WeeksList(weeks: Array(draft.weeks.prefix(8)), current: draft.weeks.first?.start ?? "", actuals: [:])
            }
            Text("Nothing changes until you accept. The coach's read of this draft is below.")
                .font(.subheadline).foregroundStyle(.secondary)
            PlanActions(draftId: draft.id, reload: reload)
        }
        .overlay(RoundedRectangle(cornerRadius: Card<EmptyView>.radius).stroke(Color.primary, lineWidth: 2))
    }

    static func brief(_ w: WeekBrief) -> String {
        "\(PlanText.focus(w.focus)), \(Fmt.mi(w.miles)) mi, \(Fmt.n0(w.gainFt)) ft up"
    }
}

// MARK: Cards

/// The next run on the plan, big: distance and vert together, how long and how to run it. Then
/// the next few, one line each.
private struct NextRunCard: View {
    let sessions: [PlanSession]
    let today: String

    var body: some View {
        let runs = sessions.filter { $0.day >= today && $0.kind != "rest" && $0.kind != "done" }
        VStack(alignment: .leading, spacing: 10) {
            if let next = runs.first {
                let restToday = next.day != today ? sessions.first { $0.day == today }?.kind : nil
                HStack {
                    Text("NEXT RUN · \(when(next.day).uppercased())").font(.caption.weight(.semibold)).tracking(0.5)
                        .foregroundStyle(.white.opacity(0.7))
                    Spacer()
                    if let restToday { Text("Today: \(restToday == "done" ? "done" : "rest")").font(.caption).foregroundStyle(.white.opacity(0.7)) }
                }
                (Text(next.title) + Text(next.own == true ? " · your run" : "").font(.subheadline).foregroundStyle(.white.opacity(0.7)))
                    .font(.title2.bold()).foregroundStyle(.white)
                Text(Fmt.distVert(mi: next.miles, gain: next.gainFt, loss: next.lossFt))
                    .font(.headline.monospacedDigit()).foregroundStyle(.white)
                Text("\(PlanText.minutes(next.minutes)). \(next.how)").foregroundStyle(.white.opacity(0.85))
                if runs.count > 1 {
                    Divider().overlay(Color.white.opacity(0.2))
                    ForEach(runs.dropFirst().prefix(3), id: \.day) { s in
                        HStack(alignment: .firstTextBaseline) {
                            Text(Fmt.day(s.day)).foregroundStyle(.white.opacity(0.7))
                            Text(s.title).foregroundStyle(.white).lineLimit(1)
                            Spacer()
                            Text("\(Fmt.mi(s.miles)) mi · \(Fmt.n0(s.gainFt)) ft").font(.caption.monospacedDigit()).foregroundStyle(.white)
                        }
                        .font(.subheadline)
                    }
                }
            } else {
                Text("NEXT RUN").font(.caption.weight(.semibold)).tracking(0.5).foregroundStyle(.white.opacity(0.7))
                Text("Nothing in the next 7 days").font(.title3.weight(.semibold)).foregroundStyle(.white)
                Text("Rest, or add a run on the calendar.").foregroundStyle(.white.opacity(0.85))
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(hex: 0x15211D, dark: 0x22302B), in: RoundedRectangle(cornerRadius: 16))
    }

    private func when(_ day: String) -> String {
        if day == today { return "Today" }
        if day == PlanText.addDays(today, 1) { return "Tomorrow, \(Fmt.day(day, .dateTime.weekday(.wide)))" }
        return Fmt.day(day, .dateTime.weekday(.wide).month(.abbreviated).day())
    }
}

/// This week against its target: done (solid) and still scheduled (light), for miles and both
/// directions of vert, plus the share of time below AeT.
private struct ThisWeekCard: View {
    let w: WeekProgress

    private static let dims: [(String, String, KeyPath<WeekTarget, Double>, KeyPath<Vol, Double>)] = [
        ("miles", "Miles", \.miles, \.miles), ("gain_ft", "Gain", \.gainFt, \.gainFt), ("loss_ft", "Descent", \.lossFt, \.lossFt),
    ]

    var body: some View {
        if let t = w.target, let c = w.cover {
            Card {
                HStack {
                    SectionLabel("This week, \(PlanText.focus(t.focus).lowercased())")
                    Spacer()
                    Pill(text: Self.stateText(c.state), tone: Self.stateTone(c.state))
                }
                ForEach(Self.dims, id: \.0) { key, label, tk, vk in
                    bar(key: key, label: label, target: t[keyPath: tk], done: done(key), ahead: w.ahead[keyPath: vk])
                }
                Group {
                    if let easy = w.done?.belowAetPct {
                        Text("\(Fmt.pct(easy)) of this week's time below AeT, against the 80% goal.")
                    } else {
                        Text("No heart-rate data this week yet.")
                    }
                    if c.state == .short { Text("Add a run on the calendar, or let the week come in under target.") }
                    if c.state == .over { Text("Your runs this week go well past the target. Load shows whether that ramps too fast.") }
                }
                .font(.subheadline).foregroundStyle(.secondary)
            }
        }
    }

    private func done(_ key: String) -> Double {
        guard let d = w.done else { return 0 }
        return key == "miles" ? d.miles : key == "gain_ft" ? d.gainFt : d.lossFt
    }

    private func fmt(_ key: String, _ v: Double) -> String { key == "miles" ? "\(Fmt.mi(v)) mi" : "\(Fmt.n0(v)) ft" }

    private func bar(key: String, label: String, target: Double, done: Double, ahead: Double) -> some View {
        let scale = max(target, done + ahead, 1)
        return VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                Text(label).font(.subheadline)
                Spacer()
                Text("\(fmt(key, done)) done\(ahead > 0 ? " + \(fmt(key, ahead)) scheduled" : "") of \(fmt(key, target))")
                    .font(.caption.monospacedDigit())
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Color.secondary.opacity(0.12)
                    Color.primary.frame(width: geo.size.width * done / scale)
                    Color.primary.opacity(0.3).frame(width: geo.size.width * ahead / scale).offset(x: geo.size.width * done / scale)
                    Tone.warn.ink.frame(width: 2).offset(x: geo.size.width * target / scale - 1)
                }
            }
            .frame(height: 10)
            .clipShape(RoundedRectangle(cornerRadius: 5))
            .accessibilityElement()
            .accessibilityLabel("\(label): \(fmt(key, done)) done and \(fmt(key, ahead)) scheduled of \(fmt(key, target))")
        }
    }

    static func stateText(_ s: WeekProgress.Cover) -> String {
        switch s {
        case .covered: "On track"
        case .close: "A little short"
        case .short: "Short"
        case .over: "Over target"
        }
    }

    static func stateTone(_ s: WeekProgress.Cover) -> Tone {
        switch s {
        case .covered: .good
        case .close, .over: .warn
        case .short: .bad
        }
    }
}

/// Weekly targets to the objective: miles and feet up per week, lighter for recovery and taper,
/// with this week marked.
private struct RoadCard: View {
    let obj: Objective
    let plan: Plan
    let current: String
    let actuals: [String: WeekActual]

    var body: some View {
        Card {
            HStack {
                SectionLabel("Road to \(obj.name)")
                Spacer()
                NavigationLink("Every week") { WeeksScreen(obj: obj, plan: plan, current: current, actuals: actuals) }
                    .font(.subheadline)
            }
            Text("Peak \(Fmt.mi(plan.peak.miles)) mi and \(Fmt.n0(plan.peak.gainFt)) ft up a week. Lighter weeks are recovery and taper; the marker is this week.")
                .font(.subheadline).foregroundStyle(.secondary)
            strip("Miles a week", color: Ledger.color("distance")) { $0.miles }
            strip("Feet up a week", color: Ledger.color("climb")) { $0.gainFt }
            HStack {
                Text(plan.weeks.first.map { Fmt.day($0.start, .dateTime.month(.abbreviated).day()) } ?? "")
                Spacer()
                Text(plan.weeks.last.map { Fmt.day($0.start, .dateTime.month(.abbreviated).day()) } ?? "")
            }
            .font(.caption2).foregroundStyle(.secondary)
        }
    }

    private func strip(_ label: String, color: Color, value: @escaping (PlanWeek) -> Double) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            let top = max(plan.weeks.map(value).max() ?? 1, 1)
            Chart(plan.weeks) { w in
                let light = w.focus == "recovery" || w.focus == "taper" || w.focus == "race"
                BarMark(x: .value("Week", w.start), y: .value(label, value(w)))
                    .foregroundStyle(color.opacity(light ? 0.45 : 1))
                    .cornerRadius(3)
                    .annotation(position: .top, spacing: 2) {
                        if w.start == current { Image(systemName: "arrowtriangle.down.fill").font(.system(size: 7)) }
                    }
            }
            .chartYScale(domain: 0...(top * 1.12))
            .chartXAxis(.hidden)
            .chartYAxis { AxisMarks(position: .leading, values: .automatic(desiredCount: 3)) }
            .frame(height: 110)
            .accessibilityLabel("\(label) for each week to the objective")
        }
    }
}

/// The whole plan to the objective date, one row per week.
private struct WeeksScreen: View {
    let obj: Objective
    let plan: Plan
    let current: String
    let actuals: [String: WeekActual]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text("Peak weeks aim at \(Fmt.mi(plan.peak.miles)) mi, \(Fmt.n0(plan.peak.gainFt)) ft up and \(Fmt.n0(plan.peak.lossFt)) ft down. Build weeks add at most 10%, a recovery week follows every three builds, and every week was checked against the load limits by projecting your ledgers forward. Starting point: your last four weeks, \(Fmt.mi(plan.baseline.miles)) mi and \(Fmt.n0(plan.baseline.gainFt)) ft up a week.")
                    .font(.subheadline).foregroundStyle(.secondary)
                Card { WeeksList(weeks: plan.weeks, current: current, actuals: actuals, full: true) }
            }
            .padding()
        }
        .background(Color(uiColor: .systemGroupedBackground))
        .navigationTitle("\(obj.name): every week")
        .navigationBarTitleDisplayMode(.inline)
    }
}

/// Week targets: miles and feet side by side, the long day, what you actually ran, and why a
/// week was held back.
struct WeeksList: View {
    let weeks: [PlanWeek]
    let current: String
    let actuals: [String: WeekActual]
    var full = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(weeks) { w in
                VStack(alignment: .leading, spacing: 3) {
                    HStack(alignment: .firstTextBaseline) {
                        Text("Week of \(Fmt.day(w.start, .dateTime.month(.abbreviated).day()))").fontWeight(.semibold)
                        Text(PlanText.focus(w.focus) + (w.limitedBy.isEmpty ? "" : " *")).foregroundStyle(.secondary)
                        Spacer()
                        Text(PlanText.hours(w.hours)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    }
                    Text(Fmt.distVert(mi: w.miles, gain: w.gainFt, loss: w.lossFt)).font(.caption.monospacedDigit())
                    if let l = w.long {
                        Text("Long day \(Fmt.mi(l.miles)) mi, \(Fmt.n0(l.gainFt)) ft").font(.caption).foregroundStyle(.secondary)
                    }
                    if let a = actuals[w.start] {
                        Text("Done \(Fmt.mi(a.miles)) mi, \(Fmt.n0(a.gainFt)) ft\(w.start == current ? " so far" : "")")
                            .font(.caption.weight(.medium)).foregroundStyle(doneTone(w, a))
                    }
                    if full && !w.notes.isEmpty {
                        Text(w.notes.joined(separator: ". ")).font(.caption).foregroundStyle(.secondary)
                    }
                }
                .padding(.vertical, 8).padding(.horizontal, 8)
                .background(w.start == current ? Color.secondary.opacity(0.12) : .clear, in: RoundedRectangle(cornerRadius: 8))
                if w.id != weeks.last?.id { Divider() }
            }
            if weeks.contains(where: { !$0.limitedBy.isEmpty }) {
                Text("* Held back so a load doesn't ramp too fast.").font(.caption).foregroundStyle(.secondary).padding(.top, 8)
            }
        }
    }

    /// Under 70% on two of miles, gain and descent is a missed week, as the weekly review reads it.
    private func doneTone(_ w: PlanWeek, _ a: WeekActual) -> Color {
        let shares = [(a.miles, w.miles), (a.gainFt, w.gainFt), (a.lossFt, w.lossFt)].map { $1 > 0 ? $0 / $1 : 1 }
        if shares.filter({ $0 >= 0.9 }).count >= 2 { return Tone.good.ink }
        if w.start != current && shares.filter({ $0 < 0.7 }).count >= 2 { return Tone.bad.ink }
        return .primary
    }
}

/// Readiness in one glance: the verdict, the limiter in a sentence, and one line per dimension
/// with where you are now and where the plan takes you.
private struct ReadinessPlanCard: View {
    let r: PlanReadiness
    let projected: ReadinessEval?
    let note: String

    var body: some View {
        let proj = Dictionary(uniqueKeysWithValues: (projected?.dims ?? []).map { ($0.key, $0) })
        let verdict = projected?.verdict ?? r.verdict
        Card {
            HStack {
                SectionLabel("Ready on the day?")
                Spacer()
                Pill(text: verdict == "ready" ? "On track" : verdict == "close" ? "Close" : "Gaps left", tone: PlanText.status(verdict))
            }
            if let lim = r.dims.first(where: { $0.key == r.limiter }) {
                (Text("Furthest behind now: \(lim.name.lowercased()). ").bold()
                    + Text("\(lim.capacityLabel) is \(Int(((lim.ratio ?? 0) * 100).rounded()))% of what the route asks; ready is \(Int(((lim.rule ?? 0) * 100).rounded()))%."))
                    .font(.subheadline)
            }
            HStack {
                Spacer()
                Text("Now").frame(width: 76, alignment: .leading)
                Text("On the plan").frame(width: 76, alignment: .leading)
            }
            .font(.caption2).foregroundStyle(.secondary)
            ForEach(r.dims, id: \.key) { d in
                Divider()
                HStack(alignment: .center) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(d.name).font(.subheadline)
                        Text(d.demand == nil ? "\(PlanText.dimValue(d, d.capacity)) \(PlanText.dimUnit[d.key] ?? "")"
                             : "\(PlanText.dimValue(d, d.capacity)) of \(PlanText.dimValue(d, d.demand)) \(PlanText.dimUnit[d.key] ?? "")")
                            .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Pill(text: d.statusText, tone: PlanText.status(d.status)).frame(width: 76, alignment: .leading)
                    Group {
                        if let p = proj[d.key] { Pill(text: p.statusText, tone: PlanText.status(p.status)) } else { Text("") }
                    }
                    .frame(width: 76, alignment: .leading)
                }
            }
            Text("Estimated time \(PlanText.hours(r.demand.time.lowH)) to \(PlanText.hours(r.demand.time.highH)). \(note)")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}

/// The coach's read of the plan: verdict, limiter and the plan's shape. It explains, never edits.
private struct CoachPlanSection: View {
    @EnvironmentObject private var api: API
    @State private var reply: Coached<PlanReply>?
    @State private var done = false

    var body: some View {
        Group {
            if let r = reply {
                CoachRead(rows: [("Verdict", r.reply.verdict), ("Limiter", r.reply.limiter), ("The plan", r.reply.plan)])
                CoachFooter(source: r.source, reason: r.fallbackReason, what: "summary", logId: r.logId, feedback: r.feedback, cost: r.cost)
            } else if !done {
                CoachPending(rows: 3, line: "The coach is reading the plan.")
            }
        }
        .task {
            reply = try? await api.get("/api/coach/plan", as: Coached<PlanReply>.self)
            done = true
        }
    }
}

/// Last week against its targets, with the coach's review and any suggested replan.
private struct ReviewSection: View {
    let fallback: WeekReview
    let reload: () async -> Void
    @EnvironmentObject private var api: API
    @State private var r: ReviewResponse?

    var body: some View {
        let review = r?.review ?? fallback
        VStack(alignment: .leading, spacing: 10) {
            Divider()
            HStack(alignment: .firstTextBaseline) {
                SectionLabel("Last week, \(Fmt.day(review.start, .dateTime.month(.abbreviated).day())) to \(Fmt.day(review.end, .dateTime.month(.abbreviated).day()))")
                Spacer()
                if let p = review.actual.belowAetPct { Text("\(Fmt.pct(p)) below AeT").font(.caption).foregroundStyle(.secondary) }
            }
            if !review.flags.isEmpty {
                Text("Flags: \(review.flags.map { "\($0.ledger) \($0.text.lowercased())" }.joined(separator: "; ")).")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            if let r {
                Text(r.reply.week)
                Text(r.reply.next)
                CoachFooter(source: r.source, reason: r.fallbackReason, what: "review", logId: r.logId, feedback: r.feedback, cost: r.cost)
            }
            if let reason = review.replan.first {
                Divider()
                (Text("A new plan is suggested. ").bold() + Text(review.replanText.joined(separator: ". ") + "."))
                PlanActions(draftId: nil, replan: true, reason: reason, reload: reload)
            }
        }
        .task { r = try? await api.get("/api/coach/review", as: ReviewResponse.self) }
    }
}

// MARK: Chat

/// Ask the coach. Every number in an answer is checked against your data before it shows.
private struct ChatCard: View {
    @EnvironmentObject private var api: API
    @State private var messages: [ChatMessage]
    @State private var question = ""
    @State private var busy = false
    @State private var error: String?

    init(initial: [ChatMessage]) {
        _messages = State(initialValue: initial)
    }

    var body: some View {
        Card {
            HStack {
                SectionLabel("Ask the coach")
                Spacer()
                if !messages.isEmpty { Button("Clear") { Task { await clear() } }.font(.subheadline) }
            }
            if messages.isEmpty {
                Text("Ask about your plan, your loads or your objective. For example: can I swap Saturday's long day to Sunday?")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            ForEach(Array(messages.enumerated()), id: \.offset) { _, m in
                bubble(m)
            }
            if busy {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("The coach is reading your numbers.").font(.caption).foregroundStyle(.secondary)
                }
            }
            HStack {
                TextField("Ask a question", text: $question, axis: .vertical)
                    .lineLimit(1...4)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { Task { await ask() } }
                Button("Ask") { Task { await ask() } }
                    .buttonStyle(.borderedProminent)
                    .disabled(busy || question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            if let error { Text(error).font(.caption).foregroundStyle(Tone.bad.ink) }
        }
    }

    private func bubble(_ m: ChatMessage) -> some View {
        let mine = m.role == "athlete"
        return HStack {
            if mine { Spacer(minLength: 40) }
            VStack(alignment: .leading, spacing: 6) {
                Text(m.text)
                if !mine, m.source == "coach", let id = m.logId {
                    Text("AI coach, numbers checked").font(.caption2).foregroundStyle(.secondary)
                    if let cost = m.cost { Text(cost.text).font(.caption2.monospacedDigit()).foregroundStyle(.secondary) }
                    CoachFeedback(logId: id, initial: nil)
                }
            }
            .font(.subheadline)
            .padding(.horizontal, 12).padding(.vertical, 8)
            .foregroundStyle(mine ? Color(uiColor: .systemBackground) : .primary)
            .background(mine ? Color.primary : Color.secondary.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
            if !mine { Spacer(minLength: 40) }
        }
    }

    private func ask() async {
        struct Question: Encodable { let question: String }
        let q = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty, !busy else { return }
        let before = messages
        busy = true
        error = nil
        messages.append(ChatMessage(id: -1, createdAt: "", role: "athlete", text: q, source: nil, logId: nil, cost: nil))
        question = ""
        defer { busy = false }
        do {
            let data = try await api.post("/api/coach/chat", Question(question: q))
            messages = try api.decode(data, as: ChatLog.self).messages
        } catch {
            messages = before
            question = q
            self.error = "Could not reach the coach. \(error.localizedDescription)"
        }
    }

    private func clear() async {
        if (try? await api.delete("/api/coach/chat")) != nil { messages = [] }
    }
}
