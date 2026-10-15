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
                    // Once per visit, not per range: the coach reads the weeks, not the chart.
                    LoadReadCard()
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
            Text("Solid: your normal, a 6-week average. Dashed: your last 7 days. Shaded: room before ramping too fast."
                 + (data.projection?.until != nil ? " Dotted: where your scheduled runs take it." : ""))
                .font(.subheadline).foregroundStyle(.secondary)
            if let gap = data.history.gap, let since = data.history.since {
                Text("No activities from \(Fmt.day(gap.from, .dateTime.month(.abbreviated).day())) to \(Fmt.day(gap.to, .dateTime.month(.abbreviated).day())), so that stretch is treated as missing data, not rest. Charts and your normal start again on \(Fmt.day(since, .dateTime.month(.abbreviated).day())).")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            if let next = data.nextWeek { NextWeekCard(data: next) }
            if let weekly = data.weekly { WeeklyStatsCard(weekly: weekly, lastToDate: data.lastWeekToDate) }
            ForEach(LedgerName.sorted(data.ledgers.keys), id: \.self) { name in
                if let l = data.ledgers[name], let today = l.today {
                    LedgerCard(name: name, today: today, series: l.series, projected: data.projection?.series[name] ?? [],
                               warnings: (data.projection?.warnings ?? []).filter { $0.ledgers.contains(name) })
                }
            }
            WeeklyIntensityCard(weeks: data.intensityByWeek, model: data.zoneModel)
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
                            + Text(" " + (th.source.contains("provisional") ? "provisional" : th.source)).foregroundStyle(.secondary)
                        Text("AnT ") + Text(Fmt.n0(th.ant)).monospacedDigit()
                    }
                    .font(.subheadline)
                    (Text("Aerobic gap ") + Text(Fmt.pct(th.aerobicGap)).monospacedDigit()
                        + Text(" goal: under 10%").foregroundStyle(.secondary))
                        .font(.subheadline)
                }
            }
        }
    }
}

/// One ledger: your normal (solid) and last 7 days (dashed) with a band up to the ramp line, then
/// where the scheduled runs take them (dotted), as web/components/LedgerChart.tsx draws it.
private struct LedgerCard: View {
    let name: String
    let today: LedgerDay
    let series: [LedgerDay]
    let projected: [ProjectedDay]
    let warnings: [LedgerWarning]

    /// The shaded band runs from your normal up to the ramp line: a dashed line above it is ramping too fast.
    private static let rampRatio = 1.3

    var body: some View {
        let (badge, tone) = Ledger.badge(vsNormal: today.vsNormal, flags: today.flags, building: today.building)
        let color = Ledger.color(name)
        let spike = series.suffix(7).first { $0.flags.contains { $0.key == "spike" } }
        let ahead = projected.filter { p in series.last.map { p.day > $0.day } ?? true }
        // The dotted lines start where the drawn ones end.
        let joined = (series.last.map { [ProjectedDay(day: $0.day, fitness: $0.fitness, fatigue: $0.fatigue)] } ?? []) + ahead
        Card(accent: color) {
            HStack {
                Circle().fill(color).frame(width: 10, height: 10)
                Text(Ledger.label(name)).font(.headline)
                Spacer()
                Pill(text: badge, tone: tone)
            }
            if let v = today.vsNormal {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(Fmt.signedPct(v)).font(.system(size: 30, weight: .semibold)).monospacedDigit().tracking(-0.5)
                    Text("vs your normal").font(.subheadline).foregroundStyle(.secondary)
                }
                .accessibilityElement(children: .combine)
                .accessibilityLabel(Fmt.capitalized(today.vsNormalText))
            } else {
                Text(Fmt.capitalized(today.vsNormalText)).font(.title2.weight(.semibold))
            }
            (Text("Last 7 days ") + Text(Ledger.value(name, today.fatigue)).monospacedDigit().foregroundStyle(.primary)
                + Text(" a day. Your normal ") + Text(Ledger.value(name, today.fitness)).monospacedDigit().foregroundStyle(.primary) + Text("."))
                .font(.subheadline).foregroundStyle(.secondary)
            Chart {
                ForEach(series.map { ($0.day, $0.fitness) } + ahead.map { ($0.day, $0.fitness) }, id: \.0) { day, fitness in
                    AreaMark(x: .value("Day", Fmt.date(day) ?? .now, unit: .day), yStart: .value("Normal", fitness),
                             yEnd: .value("Ramp", fitness * Self.rampRatio))
                        .foregroundStyle(color.opacity(0.1))
                }
                ForEach(series, id: \.day) { d in
                    LineMark(x: .value("Day", Fmt.date(d.day) ?? .now, unit: .day), y: .value("Last 7 days", d.fatigue),
                             series: .value("s", "7d"))
                        .foregroundStyle(color)
                        .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 3]))
                }
                ForEach(series, id: \.day) { d in
                    LineMark(x: .value("Day", Fmt.date(d.day) ?? .now, unit: .day), y: .value("Normal", d.fitness),
                             series: .value("s", "Normal"))
                        .foregroundStyle(color)
                        .lineStyle(StrokeStyle(lineWidth: 2.5))
                }
                if !ahead.isEmpty {
                    ForEach(joined, id: \.day) { d in
                        LineMark(x: .value("Day", Fmt.date(d.day) ?? .now, unit: .day), y: .value("Last 7 days, scheduled", d.fatigue),
                                 series: .value("s", "7d ahead"))
                            .foregroundStyle(color.opacity(0.6))
                            .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [1, 3]))
                    }
                    ForEach(joined, id: \.day) { d in
                        LineMark(x: .value("Day", Fmt.date(d.day) ?? .now, unit: .day), y: .value("Normal, scheduled", d.fitness),
                                 series: .value("s", "Normal ahead"))
                            .foregroundStyle(color.opacity(0.6))
                            .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round, dash: [1, 4]))
                    }
                }
            }
            // Zoomed to the lines rather than from zero, so a 20% swing reads as one.
            .chartYScale(domain: .automatic(includesZero: false))
            .frame(height: 160)
            .accessibilityLabel("\(Ledger.label(name)) load: your normal (solid) and last 7 days (dashed), with a band up to the ramp line"
                                + (ahead.isEmpty ? "" : ", then where your scheduled runs take them (dotted)"))
            ForEach(warnings, id: \.week) { w in
                Text("Week of \(Fmt.day(w.week, .dateTime.month(.abbreviated).day())): \(w.text) Change the schedule on the Plan tab.")
                    .font(.subheadline).foregroundStyle(Tone.warn.ink)
            }
            Text((spike.map { "\(Fmt.day($0.day, .dateTime.weekday(.wide))) was your biggest \(name) day in 6 weeks. " } ?? "")
                + (Ledger.unit(name) ?? ""))
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}

/// Stacked time in each zone of the chosen model. The red line is the 80% target, judged on time below AeT.
private struct WeeklyIntensityCard: View {
    let weeks: [WeekIntensity]
    let model: ZoneModel?

    var body: some View {
        let n = model?.zones ?? 3
        let colors = Zones.colors(n)
        let names = (1...n).map { "Zone \($0)" }
        Card {
            HStack {
                SectionLabel("Intensity by week")
                Spacer()
                Text("Red line: 80% below AeT target").font(.caption).foregroundStyle(.secondary)
            }
            Chart {
                ForEach(weeks, id: \.start) { w in
                    ForEach(1...n, id: \.self) { z in
                        BarMark(x: .value("Week", Fmt.day(w.start, .dateTime.month(.abbreviated).day())),
                                y: .value("Share", (w.zones?.share ?? w.share)["z\(z)"] ?? 0))
                            .foregroundStyle(by: .value("Zone", "Zone \(z)"))
                    }
                }
                RuleMark(y: .value("Target", 0.8)).foregroundStyle(Color.target).lineStyle(StrokeStyle(lineWidth: 1))
            }
            .chartForegroundStyleScale(domain: names, range: colors)
            .chartYAxis { AxisMarks(values: [0, 0.5, 1]) { v in AxisGridLine(); AxisValueLabel { Text(Fmt.pct(v.as(Double.self) ?? 0)) } } }
            .chartXAxis { AxisMarks { AxisValueLabel().font(.system(size: 9)) } }
            .frame(height: 170)
            if n == 3 {
                Text("Zone 1 is below AeT, zone 2 AeT to AnT, zone 3 above AnT.").font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}
