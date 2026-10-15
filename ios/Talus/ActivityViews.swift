import MapKit
import Charts
import SwiftUI

struct ActivitiesView: View {
    @EnvironmentObject private var api: API
    @State private var mode = Mode.list

    /// The web's List and Heatmap tabs, with the heatmap's Routes and Calendar side by side.
    enum Mode: String, CaseIterable {
        case list = "List", routes = "Routes", calendar = "Calendar"
    }

    var body: some View {
        NavigationStack {
            Group {
                if mode == .list {
                    list
                } else {
                    HeatmapScreen(calendar: mode == .calendar)
                }
            }
            .safeAreaInset(edge: .top, spacing: 0) {
                Picker("Activities view", selection: $mode) {
                    ForEach(Mode.allCases, id: \.self) { Text($0.rawValue) }
                }
                .pickerStyle(.segmented)
                .padding(.horizontal).padding(.bottom, 8)
                .background(.bar)
            }
            .navigationDestination(for: Int.self) { ActivityDetailView(id: $0) }
            .navigationTitle("Activity")
        }
    }

    private var list: some View {
        Loader(load: { try await api.get("/api/activities?limit=200", as: [ActivitySummary].self) }) { acts, reload in
            List {
                if acts.isEmpty {
                    Text("No activities yet. Sync from intervals.icu or Apple Health in Settings.").foregroundStyle(.secondary)
                }
                ForEach(ActivityWeek.group(acts)) { w in
                    Section {
                        ForEach(w.acts) { a in
                            NavigationLink(value: a.id) { ActivityListRow(a: a) }
                        }
                    } header: {
                        WeekHeader(w: w)
                    }
                }
            }
            .refreshable { await reload() }
        }
    }
}

/// A Monday-to-Sunday week of activities with its distance and vert, as the web's Activities page groups them.
struct ActivityWeek: Identifiable {
    let start: String
    var acts: [ActivitySummary] = []
    var miles = 0.0, gain = 0.0, loss = 0.0
    var id: String { start }

    /// Monday of the activity's week, as an ISO date.
    static func weekOf(_ day: String) -> String {
        guard let d = Fmt.date(day) else { return day }
        let cal = Calendar(identifier: .gregorian)
        let back = (cal.component(.weekday, from: d) + 5) % 7
        let monday = cal.date(byAdding: .day, value: -back, to: d) ?? d
        let c = cal.dateComponents([.year, .month, .day], from: monday)
        return String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
    }

    /// Newest week first, newest activity first within a week.
    static func group(_ acts: [ActivitySummary]) -> [ActivityWeek] {
        var weeks: [ActivityWeek] = []
        for a in acts.sorted(by: { $0.startTime > $1.startTime }) {
            let start = weekOf(a.day)
            if weeks.last?.start != start { weeks.append(ActivityWeek(start: start)) }
            weeks[weeks.count - 1].acts.append(a)
            weeks[weeks.count - 1].miles += a.distanceMi
            weeks[weeks.count - 1].gain += a.gainFt
            weeks[weeks.count - 1].loss += a.lossFt
        }
        return weeks
    }
}

private struct WeekHeader: View {
    let w: ActivityWeek

    var body: some View {
        let title = "Week of " + Fmt.day(w.start, .dateTime.month(.abbreviated).day())
        let totals = Fmt.distVert(mi: w.miles, gain: w.gain, loss: w.loss)
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
            Text(totals).monospacedDigit().foregroundStyle(.primary)
        }
        .font(.footnote.weight(.semibold))
        .textCase(nil)
        .accessibilityElement(children: .combine)
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
    @State private var renaming = false

    var body: some View {
        Loader(load: { try await api.get("/api/activities/\(id)", as: ActivityDetail.self) }) { a, reload in
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Header(a: a)
                    if let lat = a.streams.lat, let lon = a.streams.lon, lat.count > 1 { RouteCard(lat: lat, lon: lon) }
                    ProfileCard(a: a)
                    ActivityCoachCard(id: a.id)
                    LoadsCard(a: a)
                    if !a.segments.isEmpty { SegmentsCard(segments: a.segments) }
                    if let t = a.terrain, !t.bands.isEmpty {
                        GradeCard(t: t, footer: longest(a))
                    } else if let gain = a.gainByBandFt, let loss = a.lossByBandFt {
                        VertByGradeCard(gain: gain, loss: loss, footer: longest(a))
                    }
                    if let d = a.terrain?.durability { DurabilityCard(d: d) }
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
            .toolbar {
                ToolbarItem(placement: .primaryAction) { Button("Rename") { renaming = true } }
            }
            .sheet(isPresented: $renaming) {
                RenameActivitySheet(id: a.id, name: a.name, by: a.nameBy ?? "source", saved: reload)
            }
        }
    }

    private func longest(_ a: ActivityDetail) -> String {
        let high = (a.minutesAbove8000Ft ?? 0) >= 1 ? " \(Fmt.n0(a.minutesAbove8000Ft)) min above 8,000 ft." : ""
        return "Longest climb \(Fmt.n0(a.longestClimbFt)) ft. Longest descent \(Fmt.n0(a.longestDescentFt)) ft.\(high)"
    }
}

private struct Header: View {
    let a: ActivityDetail

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(Fmt.day(a.day, .dateTime.weekday(.wide).month(.abbreviated).day().year())
                 + (a.deviceName.map { " · \($0)" } ?? ""))
                .font(.subheadline).foregroundStyle(.secondary)
            if a.nameBy == "ai" {
                Text("Named by the coach" + (a.sourceName.map { ". Device name: \($0)" } ?? ""))
                    .font(.caption).foregroundStyle(.secondary)
            }
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
                }
                .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
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
                        Text(detail(s)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Text(Fmt.duration(s.timeS)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
            }
            Text("ft/h is vertical feet gained or lost per moving hour.").font(.caption).foregroundStyle(.secondary)
        }
    }

    private func detail(_ s: Segment) -> String {
        var parts = ["\(Fmt.capitalized(s.kind)), \(Int(abs(s.avgGrade).rounded()))% average grade"]
        if let r = s.vertRateFtH, r > 0 { parts.append("\(Fmt.n0(r)) ft/h") }
        if let h = s.hikeShare { parts.append(h > 0.8 ? "Hike" : h > 0.2 ? "Run and hike" : "Run") }
        return parts.joined(separator: " · ")
    }
}

/// The route on a map, from the GPS points the engine kept.
private struct RouteCard: View {
    let coords: [CLLocationCoordinate2D]

    init(lat: [Double?], lon: [Double?]) {
        coords = zip(lat, lon).compactMap { a, b in
            guard let a, let b, a.isFinite, b.isFinite, a != 0 || b != 0 else { return nil }
            return CLLocationCoordinate2D(latitude: a, longitude: b)
        }
    }

    var body: some View {
        if coords.count > 1 {
            Map(initialPosition: .automatic, interactionModes: [.pan, .zoom]) {
                MapPolyline(coordinates: coords).stroke(Ledger.color("climb"), lineWidth: 3)
                if let start = coords.first { Annotation("Start", coordinate: start) { Circle().fill(Tone.good.ink).frame(width: 10, height: 10) } }
                if let end = coords.last { Annotation("Finish", coordinate: end) { Circle().fill(Color.primary).frame(width: 10, height: 10) } }
            }
            .mapStyle(.standard(elevation: .realistic))
            .frame(height: 220)
            .clipShape(RoundedRectangle(cornerRadius: 14))
            .accessibilityLabel("Route map")
        }
    }
}
