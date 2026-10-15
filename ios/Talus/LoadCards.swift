import SwiftUI

// The Load page's cards above the ledgers: the coach's take, the next 7 days and weekly stats.
// Same wording as web/components/LoadRead.tsx, NextWeek.tsx and WeeklyStats.tsx.

/// The coach's take: last week, where things are heading, what to watch and what the next 7 days
/// should hold. Fetched on its own, so the charts don't wait on the coach; hidden if it fails.
struct LoadReadCard: View {
    @EnvironmentObject private var api: API
    @State private var reply: Coached<LoadReply>?
    @State private var done = false

    var body: some View {
        if let r = reply {
            Card {
                SectionLabel("Coach's take")
                CoachRead(rows: [("Last week", r.reply.lastWeek), ("The trend", r.reply.trend),
                                 ("Watch", r.reply.watch), ("Next 7 days", r.reply.next)])
                CoachFooter(source: r.source, reason: r.fallbackReason, what: "summary", logId: r.logId,
                            feedback: r.feedback, cost: r.cost)
            }
        } else if !done {
            Card {
                SectionLabel("Coach's take")
                CoachPending(rows: 4, line: "The coach is reading your weeks.")
            }
            .task {
                reply = try? await api.get("/api/coach/load", as: Coached<LoadReply>.self)
                done = true
            }
        }
    }
}

/// How much the next 7 days can hold, beside what's scheduled in them: the Load page's "what now".
struct NextWeekCard: View {
    let data: NextWeek

    /// Distance and vert lead, as everywhere else; time stands in for the cardio ledger.
    struct Col {
        let ledger: String
        let label: String
        let noun: String
        let value: (NextWeek.Scheduled) -> Double?
        let fmt: (Double) -> String
        let round: (Double) -> Double
    }

    static let cols = [
        Col(ledger: "distance", label: "Miles", noun: "mi", value: { $0.miles }, fmt: { Fmt.mi($0) }, round: { $0.rounded() }),
        Col(ledger: "climb", label: "Ft up", noun: "ft up", value: { $0.gainFt }, fmt: { Fmt.n0($0) },
            round: { ($0 / 100).rounded() * 100 }),
        Col(ledger: "descent", label: "Ft down", noun: "ft down", value: { $0.lossFt }, fmt: { Fmt.n0($0) },
            round: { ($0 / 100).rounded() * 100 }),
        Col(ledger: "cardio", label: "Time", noun: "hours", value: { $0.hours }, fmt: { Fmt.duration($0 * 3600) },
            round: { ($0 * 4).rounded() / 4 }),
    ]

    enum Verdict: String {
        case eases, holds, builds, over

        var label: String {
            switch self {
            case .eases: "Eases off"
            case .holds: "Holds your normal"
            case .builds: "Builds"
            case .over: "Too fast"
            }
        }

        var color: Color {
            switch self {
            case .builds: Tone.good.ink
            case .over: Tone.warn.ink
            default: .secondary
            }
        }

        init(_ v: Double, hold: Double, limit: Double) {
            self = v > limit ? .over : v < hold * 0.9 ? .eases : v <= hold * 1.1 ? .holds : .builds
        }
    }

    struct Row {
        let c: Col
        let r: NextWeek.Room
        let v: Double?
        let verdict: Verdict?
    }

    private static func list(_ xs: [String]) -> String {
        xs.count < 3 ? xs.joined(separator: " and ") : xs.dropLast().joined(separator: ", ") + " and " + xs.last!
    }

    private static func rounded(_ c: Col, _ v: Double) -> String { c.fmt(c.round(v)) }

    var body: some View {
        let s = data.scheduled
        let planned = s.runs > 0
        let states = Self.cols.compactMap { c -> Row? in
            guard let r = data.room[c.ledger] else { return nil }
            let v = c.value(s)
            return Row(c: c, r: r, v: v, verdict: v.map { Verdict($0, hold: r.hold, limit: r.limit) })
        }
        if !states.isEmpty {
            let (head, warn) = headline(states, planned: planned)
            Card {
                SectionLabel("Next 7 days")
                Text("\(Fmt.day(data.start, .dateTime.weekday(.abbreviated).month(.abbreviated).day())) to \(Fmt.day(data.end, .dateTime.weekday(.abbreviated).month(.abbreviated).day()))"
                     + (planned ? ", \(s.runs) run\(s.runs == 1 ? "" : "s") scheduled" : ""))
                    .font(.subheadline).foregroundStyle(.secondary)
                Text(head + (planned ? " Change it on the Plan tab." : " Schedule runs on the Plan tab."))
                    .font(.subheadline.weight(warn ? .semibold : .regular))
                    .foregroundStyle(warn ? Tone.warn.ink : .primary)
                    .fixedSize(horizontal: false, vertical: true)
                LazyVGrid(columns: [GridItem(.flexible(), spacing: 20), GridItem(.flexible())], alignment: .leading, spacing: 16) {
                    ForEach(states, id: \.c.ledger) { column($0, planned: planned) }
                }
                Text("Normal: the week that keeps your 6-week average where it is. Limit: the most before your last 7 days run 30% above your normal, spread evenly. One big day lands harder than the same total spread out.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func headline(_ states: [Row], planned: Bool) -> (String, Bool) {
        let over = states.filter { $0.verdict == .over }
        let vert = states.filter { $0.c.ledger != "cardio" && $0.verdict != nil }
        func pair(_ limit: Bool) -> String {
            Self.list(states.filter { $0.c.ledger == "distance" || $0.c.ledger == "climb" }
                .map { "\(Self.rounded($0.c, limit ? $0.r.limit : $0.r.hold)) \($0.c.noun)" })
        }
        if !planned {
            return ("Nothing scheduled yet. About \(pair(false)) keeps your normal; past \(pair(true)) you'd be ramping too fast.", false)
        }
        if !over.isEmpty {
            return ("The schedule runs past the ramp limit on \(Self.list(over.map { $0.c.label.lowercased() })). Shorten or move a run.", true)
        }
        if !vert.isEmpty && vert.allSatisfy({ $0.verdict == .eases }) {
            return ("The schedule is lighter than your normal: a good recovery week, and your normal will dip a little.", false)
        }
        if vert.contains(where: { $0.verdict == .builds }) {
            return ("The schedule builds on your normal and stays under the ramp limit.", false)
        }
        return ("The schedule holds your normal, with room to spare before ramping too fast.", false)
    }

    private func column(_ x: Row, planned: Bool) -> some View {
        let color = Ledger.color(x.c.ledger)
        let top = max(max(x.r.limit, x.v ?? 0, x.r.hold) * 1.12, 0.0001)
        let shown = planned ? x.v : nil
        return VStack(alignment: .leading, spacing: 6) {
            Text(x.c.label + (planned ? ", scheduled" : "")).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(shown.map(x.c.fmt) ?? "–").font(.title2.weight(.bold)).monospacedDigit()
                if planned, let vd = x.verdict {
                    Text(vd.label).font(.caption.weight(.semibold)).foregroundStyle(vd.color)
                }
            }
            GeometryReader { geo in
                let at = { (v: Double) in geo.size.width * min(1, v / top) }
                ZStack(alignment: .leading) {
                    Color.primary.opacity(0.1)
                    Tone.warn.fill.overlay(Tone.warn.ink.opacity(0.25)).frame(width: geo.size.width - at(x.r.limit))
                        .offset(x: at(x.r.limit))
                    if let v = shown { color.frame(width: at(v)).clipShape(Capsule()) }
                    Color.primary.opacity(0.45).frame(width: 2).offset(x: at(x.r.hold) - 1)
                    Tone.warn.ink.frame(width: 2).offset(x: at(x.r.limit) - 1)
                }
            }
            .frame(height: 10)
            .clipShape(Capsule())
            .accessibilityElement()
            .accessibilityLabel("\(x.c.label): \(shown.map { x.c.fmt($0) + " scheduled, " } ?? "")\(Self.rounded(x.c, x.r.hold)) holds your normal, \(Self.rounded(x.c, x.r.limit)) is the ramp limit")
            HStack {
                Text("Normal ") + Text(Self.rounded(x.c, x.r.hold)).monospacedDigit().foregroundStyle(.primary)
                Spacer()
                Text("Limit ") + Text(Self.rounded(x.c, x.r.limit)).monospacedDigit().foregroundStyle(.primary)
            }
            .font(.caption).foregroundStyle(.secondary)
        }
    }
}

/// The last 8 Monday weeks: last week's totals against the week before and your average, then
/// every week with a bar and its change. The phone shows the web's four lead columns.
struct WeeklyStatsCard: View {
    let weekly: [WeekTotals]
    let lastToDate: WeekTotals?

    struct Col {
        let label: String
        let value: (WeekTotals) -> Double
        let fmt: (Double) -> String
        let bar: Color?
    }

    static let cols = [
        Col(label: "Miles", value: { $0.distanceMi }, fmt: { Fmt.mi($0) }, bar: Ledger.color("distance")),
        Col(label: "Ft up", value: { $0.gainFt }, fmt: { Fmt.n0($0) }, bar: Ledger.color("climb")),
        Col(label: "Ft down", value: { $0.lossFt }, fmt: { Fmt.n0($0) }, bar: Ledger.color("descent")),
        Col(label: "Time", value: { $0.movingTimeS }, fmt: { Fmt.duration($0) }, bar: nil),
    ]

    /// Change from `prev` to `cur` as a signed percent, or nil when there is nothing to compare.
    static func change(_ cur: Double, _ prev: Double) -> String? {
        if prev == 0 { return cur != 0 ? "new" : nil }
        let p = Int(((cur - prev) / prev * 100).rounded())
        return p == 0 ? "same" : "\(p > 0 ? "↑" : "↓") \(abs(p))%"
    }

    var body: some View {
        if let now = weekly.last {
            let last = weekly.count > 1 ? weekly[weekly.count - 2] : nil
            let full = weekly.dropLast().filter { $0.activities > 0 }
            let avg = Self.cols.map { c in full.isEmpty ? 0 : full.map(c.value).reduce(0, +) / Double(full.count) }
            let top = Self.cols.map { c in max(weekly.map(c.value).max() ?? 1, 1) }
            let byDay = Fmt.date(now.start).flatMap { Calendar(identifier: .gregorian).date(byAdding: .day, value: (now.days ?? 7) - 1, to: $0) }
                .map { $0.formatted(.dateTime.weekday(.abbreviated)) } ?? ""
            let rows = Array(weekly.reversed())
            Card {
                Text("Weekly stats").font(.headline)
                Text("Monday to Sunday. Each change compares with the week before.").font(.subheadline).foregroundStyle(.secondary)
                if let last {
                    LazyVGrid(columns: [GridItem(.flexible(), spacing: 16), GridItem(.flexible())], alignment: .leading, spacing: 14) {
                        ForEach(Self.cols.indices, id: \.self) { i in
                            let c = Self.cols[i]
                            VStack(alignment: .leading, spacing: 2) {
                                Text("\(c.label), last week").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                                Text(c.fmt(c.value(last))).font(.title3.weight(.semibold)).monospacedDigit()
                                Group {
                                    if weekly.count > 2 {
                                        Text("\(Self.change(c.value(last), c.value(weekly[weekly.count - 3])) ?? "–") vs prior week")
                                    }
                                    if full.count > 1 {
                                        Text("\(Self.change(c.value(last), avg[i]) ?? "–") vs your average")
                                    }
                                }
                                .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                    .padding(.bottom, 4)
                }
                Grid(alignment: .trailing, horizontalSpacing: 10, verticalSpacing: 10) {
                    GridRow {
                        Text("Week").gridColumnAlignment(.leading)
                        ForEach(Self.cols, id: \.label) { Text($0.label) }
                    }
                    .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                    Divider()
                    ForEach(rows.indices, id: \.self) { i in
                        let w = rows[i]
                        let current = i == 0
                        let prev = current ? lastToDate : (i + 1 < rows.count ? rows[i + 1] : nil)
                        GridRow(alignment: .top) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(current ? "This week" : Fmt.day(w.start, .dateTime.month(.abbreviated).day()))
                                    .font(.subheadline.weight(.semibold)).fixedSize()
                                if current, let d = w.days, d < 7 {
                                    Text("so far, \(d) of 7 days").font(.caption2).foregroundStyle(.secondary).fixedSize()
                                }
                            }
                            ForEach(Self.cols.indices, id: \.self) { k in
                                cell(w, k, top: top[k], prev: prev,
                                     title: current ? "vs last week through \(byDay)" : "vs the week before")
                            }
                        }
                    }
                    if full.count > 1 {
                        Divider()
                        GridRow(alignment: .top) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text("Average").font(.subheadline.weight(.semibold))
                                Text("\(full.count) full weeks").font(.caption2).foregroundStyle(.secondary).fixedSize()
                            }
                            ForEach(Self.cols.indices, id: \.self) { k in
                                Text(Self.cols[k].fmt(avg[k])).font(.subheadline).monospacedDigit()
                            }
                        }
                    }
                }
                Text("Counts every activity, the same as the Activities page. Bars are scaled to the biggest of these weeks.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func cell(_ w: WeekTotals, _ k: Int, top: Double, prev: WeekTotals?, title: String) -> some View {
        let c = Self.cols[k]
        let v = c.value(w)
        return VStack(alignment: .trailing, spacing: 3) {
            Text(c.fmt(v)).font(.subheadline).monospacedDigit()
            if let bar = c.bar {
                GeometryReader { geo in
                    Capsule().fill(bar.opacity(0.85)).frame(width: geo.size.width * min(1, v / top))
                        .frame(maxWidth: .infinity, alignment: .trailing)
                }
                .frame(height: 3)
            }
            if let prev, let d = Self.change(v, c.value(prev)) {
                Text(d).font(.caption2).foregroundStyle(.secondary).fixedSize()
                    .accessibilityLabel("\(d) \(title)")
            }
        }
    }
}
