import Charts
import SwiftUI

struct ActivitiesView: View {
    @EnvironmentObject private var api: API

    var body: some View {
        NavigationStack {
            Loader(load: { try await api.get("/api/activities?limit=200", as: [ActivitySummary].self) }) { acts, reload in
                List {
                    if acts.isEmpty {
                        Text("No activities yet. Sync from intervals.icu or Apple Health in Settings.").foregroundStyle(.secondary)
                    }
                    ForEach(months(acts), id: \.0) { month, items in
                        Section(month) {
                            ForEach(items) { a in
                                NavigationLink(value: a.id) { ActivityListRow(a: a) }
                            }
                        }
                    }
                }
                .refreshable { await reload() }
            }
            .navigationDestination(for: Int.self) { ActivityDetailView(id: $0) }
            .navigationTitle("Activity")
        }
    }

    private func months(_ acts: [ActivitySummary]) -> [(String, [ActivitySummary])] {
        var out: [(String, [ActivitySummary])] = []
        for a in acts.sorted(by: { $0.startTime > $1.startTime }) {
            let m = Fmt.day(a.day, .dateTime.month(.wide).year())
            if out.last?.0 == m { out[out.count - 1].1.append(a) } else { out.append((m, [a])) }
        }
        return out
    }
}

private struct ActivityListRow: View {
    let a: ActivitySummary

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                Text(a.name ?? "Activity").font(.headline).lineLimit(1)
                Spacer()
                Text(Fmt.day(a.day, .dateTime.weekday(.abbreviated).month(.abbreviated).day()))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Text(Fmt.distVert(mi: a.distanceMi, gain: a.gainFt, loss: a.lossFt))
                .font(.subheadline.monospacedDigit())
            HStack(spacing: 12) {
                Text(Fmt.duration(a.movingTimeS))
                Text("\(Fmt.mi(a.effortMi)) effort mi")
                loadDot("cardio", a.cardioLoad)
                loadDot("climb", a.climbLoad)
                loadDot("descent", a.descentLoad)
            }
            .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
        }
        .padding(.vertical, 2)
    }

    private func loadDot(_ name: String, _ v: Double?) -> some View {
        HStack(spacing: 3) {
            Circle().fill(Ledger.color(name)).frame(width: 6, height: 6)
            Text(Fmt.n0(v))
        }
        .accessibilityLabel("\(Ledger.label(name)) load \(Fmt.n0(v))")
    }
}

struct ActivityDetailView: View {
    let id: Int
    @EnvironmentObject private var api: API

    var body: some View {
        Loader(load: { try await api.get("/api/activities/\(id)", as: ActivityDetail.self) }) { a, _ in
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Header(a: a)
                    ProfileCard(a: a)
                    LoadsCard(a: a)
                    if !a.segments.isEmpty { SegmentsCard(segments: a.segments) }
                    if !a.notes.isEmpty {
                        Card {
                            SectionLabel("Notes")
                            ForEach(a.notes, id: \.self) { Text($0).font(.subheadline) }
                        }
                    }
                }
                .padding()
            }
            .background(Color(uiColor: .systemGroupedBackground))
            .navigationTitle(a.name ?? "Activity")
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}

private struct Header: View {
    let a: ActivityDetail

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(Fmt.day(a.day, .dateTime.weekday(.wide).month(.abbreviated).day().year())
                 + (a.deviceName.map { " · \($0)" } ?? ""))
                .font(.subheadline).foregroundStyle(.secondary)
            HStack(alignment: .firstTextBaseline, spacing: 14) {
                stat(Fmt.mi(a.distanceMi), "mi")
                stat(Fmt.n0(a.gainFt), "ft up")
                stat(Fmt.n0(a.lossFt), "ft down")
            }
            HStack(spacing: 18) {
                small(Fmt.duration(a.movingTimeS), "moving")
                small(Fmt.mi(a.effortMi), "effort mi")
                if let h = a.hikeShare, h > 0 { small(Fmt.pct(h), "hiked") }
            }
        }
    }

    private func stat(_ v: String, _ unit: String) -> some View {
        (Text(v).font(.title2.weight(.bold).monospacedDigit()) + Text(" \(unit)").font(.subheadline).foregroundStyle(.secondary))
    }

    private func small(_ v: String, _ unit: String) -> some View {
        (Text(v).monospacedDigit() + Text(" \(unit)").foregroundStyle(.secondary)).font(.subheadline)
    }
}

private struct ProfileCard: View {
    let a: ActivityDetail

    private struct Point: Identifiable {
        let id: Int
        let x: Double
        let alt: Double
        let hr: Double?
    }

    var body: some View {
        let points = makePoints()
        Card {
            SectionLabel("Elevation")
            if points.isEmpty {
                Text("No elevation in this file.").foregroundStyle(.secondary)
            } else {
                let lo = points.map(\.alt).min()!, hi = points.map(\.alt).max()!
                let pad = max((hi - lo) * 0.1, 20)
                Chart(points) { p in
                    AreaMark(x: .value("Miles", p.x), yStart: .value("Floor", lo - pad), yEnd: .value("Feet", p.alt))
                        .foregroundStyle(LinearGradient(colors: [Ledger.color("climb").opacity(0.35), Ledger.color("climb").opacity(0.05)],
                                                        startPoint: .top, endPoint: .bottom))
                    LineMark(x: .value("Miles", p.x), y: .value("Feet", p.alt))
                        .foregroundStyle(Ledger.color("climb"))
                }
                .chartYScale(domain: (lo - pad)...(hi + pad))
                .chartXScale(domain: 0...(points.last?.x ?? 1))
                .chartXAxisLabel(hasDistance ? "mi" : "min")
                .chartYAxisLabel("ft")
                .frame(height: 180)
                .accessibilityLabel("Elevation profile from \(Fmt.n0(lo)) to \(Fmt.n0(hi)) feet")
                HStack(spacing: 16) {
                    Text("Low \(Fmt.n0(lo)) ft")
                    Text("High \(Fmt.n0(hi)) ft")
                    if let c = a.longestClimbFt, c > 0 { Text("Longest climb \(Fmt.n0(c)) ft") }
                }
                .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                if let m = a.minutesAbove8000Ft, m > 0 {
                    Text("\(Fmt.n0(m)) min above 8,000 ft.").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }

    private var hasDistance: Bool { a.streams.distanceMi?.contains { $0 > 0 } == true }

    private func makePoints() -> [Point] {
        guard let alt = a.streams.altitudeFt else { return [] }
        let xs = hasDistance ? a.streams.distanceMi! : a.streams.timeS.map { $0 / 60 }
        return alt.indices.compactMap { i in
            i < xs.count ? Point(id: i, x: xs[i], alt: alt[i], hr: a.streams.heartRate.flatMap { i < $0.count ? $0[i] : nil }) : nil
        }
    }
}

private struct LoadsCard: View {
    let a: ActivityDetail

    var body: some View {
        Card {
            SectionLabel("Loads")
            ForEach(LedgerName.sorted(a.loads.keys), id: \.self) { name in
                if let l = a.loads[name] {
                    HStack(alignment: .firstTextBaseline) {
                        Circle().fill(Ledger.color(name)).frame(width: 8, height: 8)
                        Text(Ledger.label(name)).fontWeight(.semibold)
                        Spacer()
                        VStack(alignment: .trailing, spacing: 2) {
                            Text(Ledger.value(name, l.load)).monospacedDigit()
                            if let t = l.timesUsualDay {
                                Text("\(String(format: "%.1f", t))× a usual day\(l.sixWeekHigh ? ", 6-week high" : "")")
                                    .font(.caption).foregroundStyle(l.sixWeekHigh ? Tone.warn.ink : .secondary)
                            }
                        }
                    }
                }
            }
        }
    }
}

private struct SegmentsCard: View {
    let segments: [Segment]

    var body: some View {
        Card {
            SectionLabel("Segments")
            ForEach(segments) { s in
                HStack(alignment: .firstTextBaseline) {
                    Image(systemName: s.kind == "climb" ? "arrow.up.right" : s.kind == "descent" ? "arrow.down.right" : "arrow.right")
                        .foregroundStyle(s.kind == "climb" ? Ledger.color("climb") : s.kind == "descent" ? Ledger.color("descent") : .secondary)
                        .frame(width: 18)
                    VStack(alignment: .leading, spacing: 1) {
                        Text("\(Fmt.mi(s.distanceMi)) mi · \(Fmt.n0(s.gainFt)) ft up · \(Fmt.n0(s.lossFt)) ft down")
                            .font(.subheadline.monospacedDigit())
                        Text("\(Fmt.capitalized(s.kind)), \(Int(s.avgGrade.rounded()))% average grade")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Text(Fmt.duration(s.timeS)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
            }
        }
    }
}
