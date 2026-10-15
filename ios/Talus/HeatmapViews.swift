import MapKit
import SwiftUI

// Every route on one map, brighter where you go often, and a calendar of days (web/components/
// Heatmap.tsx and HeatMapView.tsx). The range and sport filters are shared by both.

enum Heat {
    /// The web's --heat.
    static let color = Color(hex: 0xE0400F, dark: 0xFFA53D)
    /// The web's --line, the empty calendar day.
    static let line = Color(hex: 0xDCE2DF, dark: 0x2A3531)

    static let ranges: [(key: String, label: String)] = [("30", "30 days"), ("90", "90 days"), ("365", "1 year"), ("all", "All")]
    static let kinds: [(key: String, label: String)] = [
        ("trail", "Trail run"), ("run", "Run"), ("hike", "Hike"), ("ride", "Ride"), ("other", "Other"),
    ]

    static func iso(_ d: Date) -> String {
        let c = Calendar(identifier: .gregorian).dateComponents([.year, .month, .day], from: d)
        return String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
    }

    static func addDays(_ day: String, _ n: Int) -> String {
        guard let d = Fmt.date(day), let next = Calendar(identifier: .gregorian).date(byAdding: .day, value: n, to: d) else { return day }
        return iso(next)
    }
}

/// A route with its decoded line.
struct HeatTrack: Identifiable {
    let a: HeatActivity
    let coords: [CLLocationCoordinate2D]
    var id: Int { a.id }
}

struct HeatmapScreen: View {
    let calendar: Bool
    @EnvironmentObject private var api: API
    @EnvironmentObject private var session: SessionModel
    @State private var data: HeatmapData?
    @State private var error: String?
    @State private var range = "all"
    @State private var kinds: Set<String> = []
    @State private var lines: [Int: [CLLocationCoordinate2D]] = [:]
    @State private var attempt = 0

    var body: some View {
        Group {
            if let data {
                if data.activities.isEmpty {
                    ContentUnavailableView("No scored activities yet.", systemImage: "map")
                } else {
                    content(data)
                }
            } else if let error {
                ContentUnavailableView {
                    Label("Can't load this", systemImage: "exclamationmark.triangle")
                } description: {
                    Text("Could not load your routes: \(error)")
                } actions: {
                    Button("Try again") { self.error = nil; attempt += 1 }
                }
            } else {
                SkeletonScreen()
            }
        }
        .background(Color(uiColor: .systemGroupedBackground))
        .task(id: attempt) { await poll() }
    }

    /// Lines the engine hasn't drawn yet are read in the background; ask again until they are all there.
    private func poll() async {
        while !Task.isCancelled {
            do {
                let d = try await api.get("/api/heatmap", as: HeatmapData.self)
                for a in d.activities where lines[a.id] == nil {
                    if let line = a.line { lines[a.id] = Polyline.decode(line) }
                }
                data = d
                if d.pending == 0 { return }
            } catch APIError.signedOut {
                session.signedOut()
                return
            } catch {
                // Once routes are showing, a failed poll only means asking again.
                if data == nil {
                    self.error = error.localizedDescription
                    return
                }
            }
            try? await Task.sleep(for: .seconds(3))
        }
    }

    @ViewBuilder
    private func content(_ data: HeatmapData) -> some View {
        let today = Heat.iso(.now)
        let since = range == "all" ? nil : Heat.addDays(today, -(Int(range) ?? 0) + 1)
        let shown = data.activities.filter { a in since.map { a.day >= $0 } ?? true && (kinds.isEmpty || kinds.contains(a.kind)) }
        let tracks = shown.compactMap { a in lines[a.id].flatMap { $0.count > 1 ? HeatTrack(a: a, coords: $0) : nil } }
        let noGps = shown.count - tracks.count - shown.filter(\.pending).count
        let header = VStack(alignment: .leading, spacing: 10) {
            Text("Every route you have recorded, brighter where you go often").font(.subheadline).foregroundStyle(.secondary)
            filters(data)
            HeatTotals(acts: shown)
            status(pending: data.pending, noGps: calendar ? 0 : noGps)
        }
        if calendar {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    header
                    Card { HeatCalendar(acts: shown, from: since ?? shown.last?.day ?? today, today: today) }
                }
                .padding()
            }
        } else {
            VStack(alignment: .leading, spacing: 10) {
                header.padding(.horizontal)
                if tracks.isEmpty {
                    ContentUnavailableView("No routes in this range.", systemImage: "map")
                } else {
                    HeatMapView(tracks: tracks).padding(.horizontal).padding(.bottom, 8)
                }
            }
            .padding(.top, 8)
        }
    }

    @ViewBuilder
    private func filters(_ data: HeatmapData) -> some View {
        Picker("Time range", selection: $range) {
            ForEach(Heat.ranges, id: \.key) { Text($0.label).tag($0.key) }
        }
        .pickerStyle(.segmented)
        let present = Heat.kinds.filter { k in data.activities.contains { $0.kind == k.key } }
        if present.count > 1 {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    chip("All", on: kinds.isEmpty) { kinds = [] }
                    ForEach(present, id: \.key) { k in
                        chip(k.label, on: kinds.contains(k.key)) {
                            if kinds.contains(k.key) { kinds.remove(k.key) } else { kinds.insert(k.key) }
                        }
                    }
                }
            }
            .accessibilityLabel("Sport")
        }
    }

    private func chip(_ title: String, on: Bool, action: @escaping () -> Void) -> some View {
        Button(title, action: action)
            .font(.subheadline.weight(.medium))
            .padding(.horizontal, 12).frame(minHeight: 32)
            .foregroundStyle(on ? Color(uiColor: .systemBackground) : .primary)
            .background(on ? Color.primary : Color(uiColor: .secondarySystemGroupedBackground), in: Capsule())
            .overlay(Capsule().stroke(Color.secondary.opacity(on ? 0 : 0.35)))
            .buttonStyle(.plain)
            .accessibilityAddTraits(on ? .isSelected : [])
    }

    @ViewBuilder
    private func status(pending: Int, noGps: Int) -> some View {
        if pending > 0 || noGps > 0 {
            HStack(spacing: 6) {
                if pending > 0 {
                    ProgressView().controlSize(.small)
                    Text("Drawing \(Fmt.n0(Double(pending))) more route\(pending == 1 ? "" : "s")…")
                }
                if noGps > 0 { Text("\(Fmt.n0(Double(noGps))) without GPS not on the map.") }
            }
            .font(.caption).foregroundStyle(.secondary)
        }
    }
}

/// Activities, distance, climb, descent and moving time over what the filters show.
private struct HeatTotals: View {
    let acts: [HeatActivity]

    var body: some View {
        let mi = acts.reduce(0) { $0 + $1.distanceMi }, up = acts.reduce(0) { $0 + $1.gainFt }
        let down = acts.reduce(0) { $0 + $1.lossFt }, s = acts.reduce(0) { $0 + $1.movingTimeS }
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), alignment: .leading), count: 3), alignment: .leading, spacing: 8) {
            stat("Activities", Fmt.n0(Double(acts.count)), nil)
            stat("Distance", Fmt.n0(mi), "mi")
            stat("Moving time", Fmt.n0(s / 3600), "h")
            stat("Climb", Fmt.n0(up), "ft up")
            stat("Descent", Fmt.n0(down), "ft down")
        }
    }

    private func stat(_ label: String, _ v: String, _ unit: String?) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            (Text(v).font(.headline.monospacedDigit()) + Text(unit.map { " \($0)" } ?? "").font(.caption).foregroundStyle(.secondary))
                .lineLimit(1).minimumScaleFactor(0.8)
        }
        .accessibilityElement(children: .combine)
    }
}

// MARK: Map

struct HeatMapView: View {
    let tracks: [HeatTrack]
    @State private var position: MapCameraPosition
    @State private var picked: [Int] = []

    init(tracks: [HeatTrack]) {
        self.tracks = tracks
        _position = State(initialValue: Self.region(Self.homeOf(tracks)).map { .rect($0) } ?? .automatic)
    }

    var body: some View {
        let byId = Dictionary(tracks.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        let sel = picked.compactMap { byId[$0] }
        VStack(alignment: .leading, spacing: 8) {
            MapReader { proxy in
                Map(position: $position, interactionModes: [.pan, .zoom]) {
                    // Each route is faint; where many overlap, they add up and the trail glows.
                    ForEach(tracks) { t in
                        MapPolyline(coordinates: t.coords)
                            .stroke(Heat.color.opacity(0.45), style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                    }
                    ForEach(sel) { t in
                        MapPolyline(coordinates: t.coords).stroke(Color(hex: 0x15211D, dark: 0xFFFFFF), lineWidth: 3)
                    }
                }
                .mapStyle(.standard(emphasis: .muted, pointsOfInterest: .excludingAll))
                .onTapGesture { point in picked = pick(at: point, proxy) }
            }
            .overlay(alignment: .bottomLeading) {
                if !sel.isEmpty { PickedList(tracks: sel).padding(10) }
            }
            .clipShape(RoundedRectangle(cornerRadius: 14))
            .accessibilityLabel("Every route in the selected range, brighter where you go often")
            HStack(spacing: 8) {
                Text("Zoom to").foregroundStyle(.secondary)
                Button("Home area") { zoom(Self.homeOf(tracks)) }
                Button("Everything") { zoom(tracks) }
                Spacer()
            }
            .font(.subheadline)
            .buttonStyle(.bordered).controlSize(.small)
            Text("Tap a line to see which runs went there.").font(.caption).foregroundStyle(.secondary)
        }
        .onChange(of: tracks.map(\.id)) { _, ids in
            // New lines drawn while you look keep your pick; a filter that hides it clears it.
            let have = Set(ids)
            if !picked.allSatisfy(have.contains) { picked = [] }
        }
    }

    private func zoom(_ ts: [HeatTrack]) {
        guard let r = Self.region(ts) else { return }
        withAnimation { position = .rect(r) }
    }

    /// Routes passing within about 10 points of the tap, newest first.
    private func pick(at point: CGPoint, _ proxy: MapProxy) -> [Int] {
        guard let c = proxy.convert(point, from: .local),
              let edge = proxy.convert(CGPoint(x: point.x + 10, y: point.y), from: .local) else { return [] }
        // A flat projection around the tap is plenty at this scale.
        let k = cos(c.latitude * .pi / 180)
        func xy(_ p: CLLocationCoordinate2D) -> (Double, Double) { ((p.longitude - c.longitude) * k, p.latitude - c.latitude) }
        let tol = abs(xy(edge).0)
        func near(_ a: (Double, Double), _ b: (Double, Double)) -> Bool {
            let dx = b.0 - a.0, dy = b.1 - a.1, len = dx * dx + dy * dy
            let t = len > 0 ? max(0, min(1, -(a.0 * dx + a.1 * dy) / len)) : 0
            let x = a.0 + t * dx, y = a.1 + t * dy
            return x * x + y * y <= tol * tol
        }
        return tracks.filter { t in
            var prev = xy(t.coords[0])
            for p in t.coords.dropFirst() {
                let cur = xy(p)
                if near(prev, cur) { return true }
                prev = cur
            }
            return false
        }
        .sorted { $0.a.day > $1.a.day }
        .map(\.id)
    }

    /// Where most routes start: the busiest half-degree cell and its neighbours, so one trip abroad
    /// doesn't zoom the map out to a whole continent.
    static func homeOf(_ tracks: [HeatTrack]) -> [HeatTrack] {
        func cell(_ t: HeatTrack) -> (Int, Int) {
            (Int((t.coords[0].longitude * 2).rounded(.down)), Int((t.coords[0].latitude * 2).rounded(.down)))
        }
        var counts: [String: (cell: (Int, Int), n: Int)] = [:]
        for t in tracks {
            let c = cell(t)
            counts["\(c.0),\(c.1)", default: (c, 0)].n += 1
        }
        guard let top = counts.values.max(by: { $0.n < $1.n })?.cell else { return tracks }
        return tracks.filter { let c = cell($0); return abs(c.0 - top.0) <= 1 && abs(c.1 - top.1) <= 1 }
    }

    /// The tracks' bounds with a margin, never closer in than about a mile across.
    static func region(_ tracks: [HeatTrack]) -> MKMapRect? {
        let points = tracks.flatMap(\.coords).map(MKMapPoint.init)
        guard let first = points.first else { return nil }
        var minX = first.x, maxX = first.x, minY = first.y, maxY = first.y
        for p in points {
            minX = min(minX, p.x); maxX = max(maxX, p.x); minY = min(minY, p.y); maxY = max(maxY, p.y)
        }
        let floor = 1600 * MKMapPointsPerMeterAtLatitude(first.coordinate.latitude)
        let w = max(maxX - minX, floor), h = max(maxY - minY, floor)
        let rect = MKMapRect(x: (minX + maxX - w) / 2, y: (minY + maxY - h) / 2, width: w, height: h)
        return rect.insetBy(dx: -w * 0.08, dy: -h * 0.08)
    }
}

/// The activities under a tapped spot on the map.
private struct PickedList: View {
    let tracks: [HeatTrack]

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            SectionLabel(tracks.count == 1 ? "1 activity here" : "\(Fmt.n0(Double(tracks.count))) activities here")
            ForEach(tracks.prefix(6)) { t in
                NavigationLink(value: t.id) {
                    VStack(alignment: .leading, spacing: 1) {
                        Text(t.a.name ?? "Activity").font(.subheadline.weight(.semibold)).foregroundStyle(.primary).lineLimit(1)
                        Text("\(Fmt.day(t.a.day, .dateTime.month(.abbreviated).day().year())) · \(Fmt.mi(t.a.distanceMi)) mi · \(Fmt.n0(t.a.gainFt)) ft up")
                            .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    }
                }
            }
            if tracks.count > 6 {
                Text("and \(Fmt.n0(Double(tracks.count - 6))) more").font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(12)
        .frame(maxWidth: 340, alignment: .leading)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
    }
}

// MARK: Calendar

struct HeatCalendar: View {
    let acts: [HeatActivity]
    let from: String
    let today: String
    @State private var measure = "effort"
    @State private var picked: String?

    private static let measures = [("effort", "Effort mi"), ("miles", "Miles"), ("gain", "Feet up")]
    private static let size: CGFloat = 18, gap: CGFloat = 3

    var body: some View {
        let byDay = Dictionary(grouping: acts, by: \.day)
        let cuts = Self.cuts(byDay.values.map(value))
        let weeks = weekStarts()
        VStack(alignment: .leading, spacing: 12) {
            Picker("Shade days by", selection: $measure) {
                ForEach(Self.measures, id: \.0) { Text($0.1).tag($0.0) }
            }
            .pickerStyle(.segmented)
            legend
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(alignment: .top, spacing: Self.gap) {
                    ForEach(weeks, id: \.self) { w in
                        column(w, first: w == weeks.first, byDay: byDay, cuts: cuts)
                    }
                }
                .padding(.trailing, 2)
            }
            // Newest weeks in view when the calendar opens or its range changes.
            .defaultScrollAnchor(.trailing)
            .id(from)
            if let picked, let list = byDay[picked], !list.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(list.sorted { $0.startTime > $1.startTime }) { a in
                        NavigationLink(value: a.id) { DayRow(a: a) }.buttonStyle(.plain)
                    }
                }
            } else {
                Text("Effort mi adds a mile for every 528 ft of climbing, so distance and vert count together. Tap a day to see its activities.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private var legend: some View {
        HStack(spacing: 4) {
            Text("Less")
            ForEach(0..<5, id: \.self) { cell(level: $0).frame(width: 12, height: 12) }
            Text("More")
        }
        .font(.caption).foregroundStyle(.secondary)
        .accessibilityHidden(true)
    }

    private func column(_ w: String, first: Bool, byDay: [String: [HeatActivity]], cuts: [Double]) -> some View {
        let days = (0..<7).map { Heat.addDays(w, $0) }
        let label = days.first { $0.hasSuffix("-01") } ?? (first ? w : nil)
        return VStack(alignment: .leading, spacing: Self.gap) {
            Text(label.map { Fmt.day($0, .dateTime.month(.abbreviated)) } ?? "")
                .font(.caption2).foregroundStyle(.secondary)
                .fixedSize()
                .frame(width: Self.size, height: 14, alignment: .leading)
            ForEach(days, id: \.self) { d in
                if d < from || d > today {
                    Color.clear.frame(width: Self.size, height: Self.size)
                } else {
                    let list = byDay[d]
                    cell(level: Self.level(value(list ?? []), cuts))
                        .frame(width: Self.size, height: Self.size)
                        .overlay {
                            if picked == d { RoundedRectangle(cornerRadius: 4).stroke(Color.primary, lineWidth: 2) }
                        }
                        .contentShape(Rectangle())
                        .onTapGesture { picked = list == nil || picked == d ? nil : d }
                        .accessibilityElement()
                        .accessibilityLabel(tip(d, list))
                        .accessibilityAddTraits(list == nil ? [] : .isButton)
                }
            }
        }
    }

    /// Level 0 is the empty day; 1 to 3 mix the heat color into it; 4 is the heat color itself.
    private func cell(level: Int) -> some View {
        RoundedRectangle(cornerRadius: 4)
            .fill(Heat.line)
            .overlay(RoundedRectangle(cornerRadius: 4).fill(Heat.color.opacity([0, 0.28, 0.5, 0.75, 1][level])))
    }

    private func tip(_ d: String, _ list: [HeatActivity]?) -> String {
        let day = Fmt.day(d, .dateTime.weekday(.abbreviated).month(.abbreviated).day().year())
        guard let list else { return "\(day): rest" }
        let mi = list.reduce(0) { $0 + $1.distanceMi }, up = list.reduce(0) { $0 + $1.gainFt }, down = list.reduce(0) { $0 + $1.lossFt }
        return "\(day): \(Fmt.mi(mi)) mi, \(Fmt.n0(up)) ft up, \(Fmt.n0(down)) ft down"
    }

    private func value(_ list: [HeatActivity]) -> Double {
        list.reduce(0) { $0 + (measure == "miles" ? $1.distanceMi : measure == "gain" ? $1.gainFt : $1.effortMi) }
    }

    private func weekStarts() -> [String] {
        var weeks: [String] = []
        var w = ActivityWeek.weekOf(from)
        while w <= today && weeks.count < 1000 {
            weeks.append(w)
            w = Heat.addDays(w, 7)
        }
        return weeks
    }

    /// Four shades, split at the quartiles of the days with something on them, so one huge day
    /// doesn't wash out the rest.
    static func cuts(_ values: [Double]) -> [Double] {
        let v = values.filter { $0 > 0 }.sorted()
        return [0.25, 0.5, 0.75].map { q in v.isEmpty ? 0 : v[Int((q * Double(v.count - 1)).rounded(.down))] }
    }

    static func level(_ v: Double, _ cuts: [Double]) -> Int {
        v <= 0 ? 0 : v <= cuts[0] ? 1 : v <= cuts[1] ? 2 : v <= cuts[2] ? 3 : 4
    }
}

private struct DayRow: View {
    let a: HeatActivity

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(a.name ?? "Activity").font(.subheadline.weight(.semibold)).lineLimit(1)
                Text("\(Fmt.day(a.day, .dateTime.weekday(.abbreviated).month(.abbreviated).day())) · \(Fmt.duration(a.movingTimeS))")
                    .font(.caption).foregroundStyle(.secondary)
                Text(Fmt.distVert(mi: a.distanceMi, gain: a.gainFt, loss: a.lossFt)).font(.caption.monospacedDigit())
            }
            Spacer()
            Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
        }
        .contentShape(Rectangle())
    }
}
