import Charts
import SwiftUI

struct LoadView: View {
    @EnvironmentObject private var api: API
    @State private var range = 182

    private static let ranges = [(42, "6 wk"), (182, "6 mo"), (365, "1 yr")]

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Picker("Range", selection: $range) {
                        ForEach(Self.ranges, id: \.0) { Text($0.1).tag($0.0) }
                    }
                    .pickerStyle(.segmented)
                    Text("Solid line: your normal, a 6-week average. Dashed line: your last 7 days.")
                        .font(.subheadline).foregroundStyle(.secondary)
                    Loader(load: { try await api.get("/api/load?days=\(range)", as: LoadData.self) }) { data, _ in
                        LoadContent(data: data)
                    }
                    .id(range)
                }
                .padding()
            }
            .background(Color(uiColor: .systemGroupedBackground))
            .navigationDestination(for: Int.self) { ActivityDetailView(id: $0) }
            .navigationTitle("Load")
        }
    }
}

private struct LoadContent: View {
    let data: LoadData

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let gap = data.history.gap, let since = data.history.since {
                Text("No activities from \(Fmt.day(gap.from, .dateTime.month(.abbreviated).day())) to \(Fmt.day(gap.to, .dateTime.month(.abbreviated).day())), so that stretch is treated as missing data, not rest. Charts and your normal start again on \(Fmt.day(since, .dateTime.month(.abbreviated).day())).")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            ForEach(LedgerName.sorted(data.ledgers.keys), id: \.self) { name in
                if let l = data.ledgers[name], let today = l.today {
                    LedgerCard(name: name, today: today, series: l.series)
                }
            }
            WeeklyIntensityCard(weeks: data.intensityByWeek)
            Card {
                SectionLabel("Biggest days, last 8 weeks")
                if data.biggestDays.isEmpty {
                    Text("No activities in the last 8 weeks.").foregroundStyle(.secondary)
                }
                ForEach(data.biggestDays) { a in
                    NavigationLink(value: a.id) {
                        HStack(alignment: .firstTextBaseline) {
                            ActivityRow(a: a)
                            Text("\(Fmt.mi(a.effortMi)) effort mi").font(.caption.monospacedDigit())
                        }
                    }
                    .buttonStyle(.plain)
                }
                if let th = data.thresholds {
                    Divider()
                    HStack(spacing: 18) {
                        Text("AeT ") + Text(Fmt.n0(th.aet)).monospacedDigit()
                            + Text(th.source.contains("provisional") ? " provisional" : "").foregroundStyle(.secondary)
                        Text("AnT ") + Text(Fmt.n0(th.ant)).monospacedDigit()
                        Text("Gap ") + Text(Fmt.pct(th.aerobicGap)).monospacedDigit()
                    }
                    .font(.subheadline)
                    Text("Aerobic gap goal: under 10%.").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }
}

private struct LedgerCard: View {
    let name: String
    let today: LedgerDay
    let series: [LedgerDay]

    var body: some View {
        let (badge, tone) = Ledger.badge(vsNormal: today.vsNormal, flags: today.flags, building: today.building)
        let color = Ledger.color(name)
        let spike = series.suffix(7).first { $0.flags.contains { $0.key == "spike" } }
        Card {
            HStack {
                Circle().fill(color).frame(width: 10, height: 10)
                Text(Ledger.label(name)).font(.headline)
                Pill(text: badge, tone: tone)
                Spacer()
            }
            Text(Fmt.capitalized(today.vsNormalText)).font(.title2.weight(.semibold))
            (Text("Last 7 days ") + Text(Ledger.value(name, today.fatigue)).monospacedDigit().foregroundStyle(.primary)
                + Text(" a day. Your normal ") + Text(Ledger.value(name, today.fitness)).monospacedDigit().foregroundStyle(.primary) + Text("."))
                .font(.subheadline).foregroundStyle(.secondary)
            Chart {
                ForEach(series, id: \.day) { d in
                    BarMark(x: .value("Day", Fmt.date(d.day) ?? .now, unit: .day), y: .value("Load", min(d.load, yMax)))
                        .foregroundStyle(color.opacity(0.22))
                }
                ForEach(series, id: \.day) { d in
                    LineMark(x: .value("Day", Fmt.date(d.day) ?? .now, unit: .day), y: .value("Normal", d.fitness),
                             series: .value("s", "Normal"))
                        .foregroundStyle(color)
                }
                ForEach(series, id: \.day) { d in
                    LineMark(x: .value("Day", Fmt.date(d.day) ?? .now, unit: .day), y: .value("Last 7 days", d.fatigue),
                             series: .value("s", "7d"))
                        .foregroundStyle(color.opacity(0.7))
                        .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 3]))
                }
            }
            .chartYScale(domain: 0...yMax)
            .frame(height: 160)
            Text((spike.map { "\(Fmt.day($0.day, .dateTime.weekday(.wide))) was your biggest \(name) day in 6 weeks. " } ?? "")
                + (Ledger.unit(name) ?? ""))
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    /// Daily bars can tower over the lines; cap them so the normal and the last 7 days stay readable.
    private var yMax: Double {
        let lines = series.map { max($0.fitness, $0.fatigue) }.max() ?? 1
        return max(lines * 1.6, 1)
    }
}

private struct WeeklyIntensityCard: View {
    let weeks: [WeekIntensity]

    var body: some View {
        let colors = Zones.colors(3)
        Card {
            HStack {
                SectionLabel("Intensity by week")
                Spacer()
                Text("Red line: 80% below AeT").font(.caption).foregroundStyle(.secondary)
            }
            Chart {
                ForEach(weeks, id: \.start) { w in
                    ForEach(1...3, id: \.self) { z in
                        BarMark(x: .value("Week", Fmt.day(w.start, .dateTime.month(.abbreviated).day())),
                                y: .value("Share", w.share["z\(z)"] ?? 0))
                            .foregroundStyle(by: .value("Zone", "Zone \(z)"))
                    }
                }
                RuleMark(y: .value("Target", 0.8)).foregroundStyle(Color(hex: 0xA3231B)).lineStyle(StrokeStyle(lineWidth: 1))
            }
            .chartForegroundStyleScale(domain: ["Zone 1", "Zone 2", "Zone 3"], range: colors)
            .chartYAxis { AxisMarks(values: [0, 0.5, 1]) { v in AxisGridLine(); AxisValueLabel { Text(Fmt.pct(v.as(Double.self) ?? 0)) } } }
            .chartXAxis { AxisMarks { AxisValueLabel().font(.system(size: 9)) } }
            .frame(height: 170)
            Text("Zone 1 is below AeT, zone 2 AeT to AnT, zone 3 above AnT.").font(.caption).foregroundStyle(.secondary)
        }
    }
}
