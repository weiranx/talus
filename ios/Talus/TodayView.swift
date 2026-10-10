import Charts
import SwiftUI

struct TodayView: View {
    @EnvironmentObject private var api: API

    var body: some View {
        NavigationStack {
            Loader(load: { try await api.get("/api/today", as: Today.self) }) { t, reload in
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        Text(Fmt.day(t.date, .dateTime.weekday(.wide).month(.abbreviated).day()))
                            .font(.subheadline).foregroundStyle(.secondary)
                        CoachCallCard(rules: t.call)
                        CheckInCard(initial: t.checkin, onSaved: reload)
                        ReadinessCard(readiness: t.readiness, score: t.score)
                        if let w = t.wellness { OvernightCard(w: w) }
                        ForEach(LedgerName.sorted(t.ledgers.keys), id: \.self) { name in
                            if let d = t.ledgers[name] {
                                TodayLedgerCard(name: name, d: d,
                                                limiter: t.readiness.legs.limiter == name && t.readiness.legs.state != "fresh")
                            }
                        }
                        WeekCard(week: t.week)
                        IntensityCard(share: t.zones28D?.share ?? t.intensity28D.share, model: t.zoneModel,
                                      belowAet: t.intensity28D.share["z1"] ?? 0)
                    }
                    .padding()
                }
                .refreshable { await reload() }
                .navigationDestination(for: Int.self) { ActivityDetailView(id: $0) }
                .navigationTitle("Today")
            }
            .background(Color(uiColor: .systemGroupedBackground))
        }
    }
}

/// Shows the rule-based call at once, then the AI coach's brief when it arrives.
struct CoachCallCard: View {
    let rules: Call
    @EnvironmentObject private var api: API
    @State private var coach: CoachReply?
    @State private var waiting = true

    var body: some View {
        let call = coach?.reply ?? rules
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("TODAY'S CALL").font(.caption.weight(.semibold)).tracking(0.5).foregroundStyle(.white.opacity(0.7))
                Spacer()
                Pill(text: Fmt.capitalized(call.call), tone: Tone.call(call.call))
            }
            Text(call.session).font(.title.bold()).foregroundStyle(.white)
            Text(call.detail).foregroundStyle(.white.opacity(0.92))
            Text("Why: \(call.why)").font(.subheadline).foregroundStyle(.white.opacity(0.75))
            HStack(spacing: 8) {
                if waiting { ProgressView().controlSize(.small).tint(.white.opacity(0.7)) }
                Text(note).font(.caption).foregroundStyle(.white.opacity(0.6))
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(hex: 0x15211D, dark: 0x22302B), in: RoundedRectangle(cornerRadius: 16))
        .task {
            coach = try? await api.get("/api/coach/brief", as: CoachReply.self)
            waiting = false
        }
    }

    /// Same labels as web/lib/coach.ts.
    private var note: String {
        guard let coach else {
            return waiting ? "Rule-based call. The AI coach is writing today's brief." : "Rule-based call. The AI coach could not answer this time."
        }
        if coach.source == "coach" { return "Written by the AI coach from your engine numbers. Every number was checked against the data." }
        switch coach.fallbackReason {
        case "no_key": return "Rule-based call. Add an Anthropic API key to the engine's .env to turn on the AI coach."
        case "off": return "Rule-based call. The AI coach is turned off in Settings."
        case "cap": return "Rule-based call. This month's AI coach budget is used up."
        case "rejected": return "Rule-based call. The coach quoted numbers that aren't in your data, so its reply was dropped."
        default: return "Rule-based call. The AI coach could not answer this time."
        }
    }
}

struct CheckInCard: View {
    let initial: CheckIn?
    let onSaved: () async -> Void
    @EnvironmentObject private var api: API
    @State private var values: [String: Int] = [:]
    @State private var editing = false
    @State private var saving = false
    @State private var error: String?

    private static let questions = [("soreness", "Leg soreness", "none", "very sore"),
                                    ("energy", "Energy", "flat", "great"),
                                    ("pain", "Any pain", "none", "sharp")]

    var body: some View {
        Card {
            if let initial, !editing {
                SectionLabel("Morning check-in")
                Text("Soreness \(initial.soreness), energy \(initial.energy), pain \(initial.pain) (of 5).")
                Button("Change") {
                    values = ["soreness": initial.soreness, "energy": initial.energy, "pain": initial.pain]
                    editing = true
                }
                .buttonStyle(.bordered)
            } else {
                SectionLabel("Morning check-in, about 10 seconds")
                ForEach(Self.questions, id: \.0) { key, label, low, high in
                    VStack(alignment: .leading, spacing: 6) {
                        HStack(alignment: .firstTextBaseline) {
                            Text(label).fontWeight(.semibold)
                            Text("1 \(low), 5 \(high)").font(.caption).foregroundStyle(.secondary)
                        }
                        HStack(spacing: 6) {
                            ForEach(1...5, id: \.self) { v in
                                let on = values[key] == v
                                Button { values[key] = v } label: {
                                    Text("\(v)")
                                        .foregroundStyle(on ? Color(uiColor: .systemBackground) : Color.primary)
                                        .frame(maxWidth: .infinity, minHeight: 36)
                                        .background(on ? Color.primary : Color.secondary.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
                                }
                                .buttonStyle(.plain)
                                .accessibilityLabel("\(label) \(v)")
                                .accessibilityAddTraits(on ? .isSelected : [])
                            }
                        }
                    }
                }
                HStack {
                    Button { Task { await save() } } label: { BusyLabel(title: saving ? "Saving" : "Save check-in", busy: saving) }
                        .buttonStyle(.borderedProminent)
                        .disabled(values.count < 3 || saving)
                    if let error { Text(error).font(.caption).foregroundStyle(Tone.bad.ink) }
                }
            }
        }
    }

    private func save() async {
        saving = true
        defer { saving = false }
        do {
            try await api.post("/api/checkin", CheckIn(soreness: values["soreness"]!, energy: values["energy"]!, pain: values["pain"]!))
            error = nil
            editing = false
            await onSaved()
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct ReadinessCard: View {
    let readiness: Readiness
    let score: ReadinessScore?

    var body: some View {
        Card {
            SectionLabel("Readiness")
            if let s = score {
                ScoreView(s: s)
                Divider()
            }
            ForEach([("Engine", readiness.engine), ("Legs", readiness.legs)], id: \.0) { title, part in
                if title == "Legs" { Divider() }
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(title).font(.headline)
                        Text(part.detail).font(.subheadline).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Pill(text: Fmt.state(part.state), tone: Tone.state(part.state))
                }
            }
        }
    }
}

/// The score, what cost it points, what it could not see, and the last 28 days.
struct ScoreView: View {
    let s: ReadinessScore

    private static func tone(_ call: String) -> Tone {
        switch call {
        case "go": .good
        case "modify": .warn
        default: .bad
        }
    }

    private static func lower(_ name: String) -> String {
        name == "HRV" ? name : name == "Resting HR" ? "resting HR" : name.lowercased()
    }

    var body: some View {
        let lost = s.items.filter { $0.points < 0 }.sorted { $0.points < $1.points }
        let missing = s.items.filter { !$0.used }.map { Self.lower($0.name) }
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .center, spacing: 12) {
                Text("\(s.score)").font(.system(size: 44, weight: .semibold)).monospacedDigit()
                    .accessibilityLabel("Readiness score \(s.score) of 100")
                VStack(alignment: .leading, spacing: 4) {
                    Pill(text: s.label, tone: Self.tone(s.call))
                    Text("of 100").font(.subheadline).foregroundStyle(.secondary)
                }
            }
            Text(s.basis + ".").font(.subheadline).foregroundStyle(.secondary)
            if lost.isEmpty {
                Text("Nothing reads worse than your normal.")
            }
            ForEach(lost, id: \.key) { i in
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(i.name).fontWeight(.semibold)
                        Text(i.text).font(.subheadline).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Text("\(i.points)").monospacedDigit()
                }
            }
            if let held = s.held {
                Text(held + ".").font(.subheadline).foregroundStyle(.secondary)
            }
            if !missing.isEmpty {
                Text("Not used, no data: " + ListFormatter.localizedString(byJoining: missing) + ".")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            if s.trend.count > 1 {
                Text("Last \(s.trend.count) days").font(.caption).foregroundStyle(.secondary)
                Chart {
                    ForEach(s.trend, id: \.day) { p in
                        BarMark(x: .value("Day", Fmt.date(p.day) ?? .now, unit: .day), y: .value("Score", max(p.score, 3)))
                            .foregroundStyle(Self.tone(p.call).ink)
                    }
                    RuleMark(y: .value("Go", 67)).foregroundStyle(Color.secondary.opacity(0.4))
                        .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
                }
                .chartYScale(domain: 0...100)
                .chartXAxis(.hidden)
                .chartYAxis(.hidden)
                .frame(height: 64)
                .accessibilityLabel("Readiness score over the last \(s.trend.count) days")
            }
        }
    }
}

struct OvernightCard: View {
    let w: Wellness

    var body: some View {
        Card {
            SectionLabel("Overnight")
            row("HRV", w.hrv)
            row("Resting HR", w.restingHr)
            row("Sleep", w.sleep)
        }
    }

    private func row(_ title: String, _ s: Signal) -> some View {
        let tone: Tone = switch s.status {
        case "ok": .good
        case "low", "high", "short": .warn
        default: .neutral
        }
        return HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).fontWeight(.semibold)
                Text(s.text).font(.subheadline).foregroundStyle(.secondary)
            }
            Spacer()
            Circle().fill(tone == .neutral ? Color.secondary.opacity(0.4) : tone.ink).frame(width: 8, height: 8).padding(.top, 6)
        }
    }
}

struct TodayLedgerCard: View {
    let name: String
    let d: TodayLedger
    let limiter: Bool

    var body: some View {
        let (badge, tone) = Ledger.badge(vsNormal: d.vsNormal, flags: d.flags, building: d.building)
        Card {
            HStack {
                Circle().fill(Ledger.color(name)).frame(width: 10, height: 10)
                Text(Ledger.label(name)).font(.headline)
                Spacer()
                Pill(text: badge, tone: tone)
            }
            Text(Fmt.capitalized(d.vsNormalText)).font(.title2.weight(.semibold))
            (Text("Last 7 days ") + Text(Ledger.value(name, d.fatigue)).monospacedDigit().foregroundStyle(.primary)
                + Text(" a day. Your normal ") + Text(Ledger.value(name, d.fitness)).monospacedDigit().foregroundStyle(.primary) + Text("."))
                .font(.subheadline).foregroundStyle(.secondary)
            Chart(d.spark, id: \.day) { p in
                LineMark(x: .value("Day", Fmt.date(p.day) ?? .now), y: .value("Normal", p.fitness), series: .value("s", "Normal"))
                    .foregroundStyle(Ledger.color(name))
                LineMark(x: .value("Day", Fmt.date(p.day) ?? .now), y: .value("Last 7 days", p.fatigue), series: .value("s", "7d"))
                    .foregroundStyle(Ledger.color(name).opacity(0.6))
                    .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 3]))
            }
            .chartXAxis(.hidden)
            .chartYAxis(.hidden)
            .chartYScale(domain: .automatic(includesZero: false))
            .frame(height: 54)
            .accessibilityLabel("\(Ledger.label(name)) over 6 weeks: solid line your normal, dashed line your last 7 days")
        }
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(limiter ? Ledger.color(name) : .clear, lineWidth: 2))
    }
}

struct WeekCard: View {
    let week: Week

    var body: some View {
        Card {
            SectionLabel("This week, from \(Fmt.day(week.start, .dateTime.month(.abbreviated).day()))")
            Text(Fmt.distVert(mi: week.distanceMi, gain: week.gainFt, loss: week.lossFt))
                .font(.subheadline.monospacedDigit())
            if week.activities.isEmpty {
                Text("No activities yet this week.").foregroundStyle(.secondary)
            } else {
                ForEach(week.activities) { a in
                    NavigationLink(value: a.id) { ActivityRow(a: a, dayStyle: .dateTime.weekday(.abbreviated)) }
                        .buttonStyle(.plain)
                    if a.id != week.activities.last?.id { Divider() }
                }
            }
        }
    }
}

struct IntensityCard: View {
    let share: [String: Double]
    let model: ZoneModel?
    let belowAet: Double

    var body: some View {
        let n = model?.zones ?? 3
        let colors = Zones.colors(n)
        Card {
            HStack {
                SectionLabel("Intensity, 28 days")
                Spacer()
                Text("Target 80% below AeT").font(.caption).foregroundStyle(.secondary)
            }
            GeometryReader { geo in
                HStack(spacing: 0) {
                    ForEach(0..<n, id: \.self) { i in
                        colors[i].frame(width: geo.size.width * (share["z\(i + 1)"] ?? 0))
                    }
                }
                .clipShape(RoundedRectangle(cornerRadius: 4))
                .overlay(alignment: .leading) {
                    Rectangle().fill(Color.target).frame(width: 2).offset(x: geo.size.width * 0.8)
                }
            }
            .frame(height: 16)
            HStack(spacing: 12) {
                ForEach(0..<n, id: \.self) { i in
                    HStack(spacing: 4) {
                        RoundedRectangle(cornerRadius: 2).fill(colors[i]).frame(width: 10, height: 10)
                        Text("Z\(i + 1) \(Fmt.pct(share["z\(i + 1)"] ?? 0))").font(.caption.monospacedDigit())
                    }
                }
            }
            Text("\(Fmt.pct(belowAet)) below AeT.").font(.subheadline).foregroundStyle(belowAet >= 0.8 ? Tone.good.ink : Tone.warn.ink)
        }
    }
}

struct ActivityRow: View {
    let a: ActivitySummary
    var dayStyle: Date.FormatStyle = .dateTime.month(.abbreviated).day()

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                Text(a.name ?? "Activity").fontWeight(.semibold).lineLimit(1)
                Text(Fmt.distVert(mi: a.distanceMi, gain: a.gainFt, loss: a.lossFt))
                    .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            }
            Spacer()
            Text(Fmt.day(a.day, dayStyle)).font(.caption).foregroundStyle(.secondary)
        }
        .contentShape(Rectangle())
    }
}
