import Charts
import SwiftUI

struct HealthNight: Codable, Hashable {
    let day: String
    let source: String?
    let hrv: Double?
    let hrvAvg7D: Double?
    let hrvBaseline: Double?
    let hrvStatus: String
    let restingHr: Double?
    let restingHrBaseline: Double?
    let restingHrStatus: String
    let sleepH: Double?
    let sleepStatus: String
}

struct HealthHistory: Codable, Hashable {
    struct Average: Codable, Hashable {
        let hrv: Double?
        let restingHr: Double?
        let sleepH: Double?
    }
    let days: Int
    let needH: Double
    let nights: [HealthNight]
    let average: Average
}

/// Overnight HRV, resting HR and sleep over a range, the way the web's Health page shows them:
/// a chart for each, then every night with the values that morning's read flagged.
struct HealthHistoryView: View {
    @EnvironmentObject private var api: API
    @State private var range = 90

    private static let ranges = [(30, "30 d"), (90, "90 d"), (180, "6 mo"), (365, "1 yr")]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Picker("Range", selection: $range) {
                    ForEach(Self.ranges, id: \.0) { Text($0.1).tag($0.0) }
                }
                .pickerStyle(.segmented)
                Loader(load: { try await api.get("/api/wellness/history?days=\(range)", as: HealthHistory.self) }) { h, _ in
                    HealthHistoryContent(h: h, rangeLabel: Self.ranges.first { $0.0 == range }?.1 ?? "")
                }
                .id(range)
            }
            .padding()
        }
        .background(Color(uiColor: .systemGroupedBackground))
        .navigationTitle("Health")
    }
}

private struct HealthHistoryContent: View {
    let h: HealthHistory
    let rangeLabel: String

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Each night is filed under the morning you woke up. Flagged values are the ones Today read as low, high or short that morning.")
                .font(.subheadline).foregroundStyle(.secondary)
            if h.nights.isEmpty {
                Card {
                    Text("No overnight data in the last \(rangeLabel)").fontWeight(.semibold)
                    Text("Turn on the watch or Apple Health under Settings > Data sources and sync.")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
            } else {
                HealthChartCard(title: "HRV", average: h.average.hrv.map(HealthFmt.ms), nights: h.nights, metric: .hrv, needH: h.needH,
                                note: "Dots: each night. Solid: 7-day average. Dashed: 60-day baseline.")
                HealthChartCard(title: "Resting HR", average: h.average.restingHr.map(HealthFmt.bpm), nights: h.nights, metric: .restingHr,
                                needH: h.needH, note: "Solid: each night. Dashed: 60-day baseline.")
                HealthChartCard(title: "Sleep", average: h.average.sleepH.map(HealthFmt.hours), nights: h.nights, metric: .sleep,
                                needH: h.needH, note: "Time asleep, not time in bed. Dashed: your \(HealthFmt.hours(h.needH)) need.")
                Card {
                    HStack {
                        SectionLabel("Each night")
                        Spacer()
                        Pill(text: "\(h.nights.count) \(h.nights.count == 1 ? "night" : "nights")", tone: .neutral)
                    }
                    ForEach(h.nights, id: \.day) { NightRow(n: $0) }
                }
            }
        }
    }
}

enum HealthFmt {
    static func ms(_ v: Double) -> String { "\(Int(v.rounded())) ms" }
    static func bpm(_ v: Double) -> String { "\(Int(v.rounded())) bpm" }
    static func hours(_ h: Double) -> String {
        let m = Int((h * 60).rounded())
        return String(format: "%d h %02d", m / 60, m % 60)
    }
    static func source(_ s: String?) -> String {
        switch s {
        case "watch": "Watch"
        case "apple_health", "healthkit": "Apple Health"
        case "intervals": "intervals.icu"
        case "garmin": "Garmin Connect"
        case "coros": "COROS"
        case let s?: s
        case nil: ""
        }
    }
    static func flagged(_ status: String) -> Bool { ["low", "high", "short"].contains(status) }
}

private struct HealthChartCard: View {
    enum Metric { case hrv, restingHr, sleep }

    let title: String
    let average: String?
    let nights: [HealthNight]
    let metric: Metric
    let needH: Double
    let note: String

    var body: some View {
        let color = metric == .hrv ? Ledger.color("cardio") : metric == .restingHr ? Ledger.color("descent") : Ledger.color("climb")
        Card {
            HStack(alignment: .firstTextBaseline) {
                Text(title).font(.headline)
                Spacer()
                Text(average ?? "–").fontWeight(.semibold).monospacedDigit()
                Text("average").font(.subheadline).foregroundStyle(.secondary)
            }
            Chart {
                ForEach(nights, id: \.day) { n in
                    if let d = Fmt.date(n.day) {
                        switch metric {
                        case .hrv:
                            if let v = n.hrv {
                                PointMark(x: .value("Day", d), y: .value("HRV", v))
                                    .foregroundStyle(color.opacity(0.45)).symbolSize(14)
                            }
                            if let v = n.hrvAvg7D {
                                LineMark(x: .value("Day", d), y: .value("HRV", v), series: .value("Line", "avg"))
                                    .foregroundStyle(color).lineStyle(StrokeStyle(lineWidth: 2.5))
                            }
                            if let v = n.hrvBaseline {
                                LineMark(x: .value("Day", d), y: .value("HRV", v), series: .value("Line", "base"))
                                    .foregroundStyle(Color.secondary).lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 4]))
                            }
                        case .restingHr:
                            if let v = n.restingHr {
                                LineMark(x: .value("Day", d), y: .value("bpm", v), series: .value("Line", "night"))
                                    .foregroundStyle(color).lineStyle(StrokeStyle(lineWidth: 2))
                            }
                            if let v = n.restingHrBaseline {
                                LineMark(x: .value("Day", d), y: .value("bpm", v), series: .value("Line", "base"))
                                    .foregroundStyle(Color.secondary).lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 4]))
                            }
                        case .sleep:
                            if let v = n.sleepH {
                                BarMark(x: .value("Day", d, unit: .day), y: .value("Hours", v))
                                    .foregroundStyle(color.opacity(0.75))
                            }
                        }
                    }
                }
                if metric == .sleep {
                    RuleMark(y: .value("Need", needH))
                        .foregroundStyle(Color.secondary).lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 4]))
                }
            }
            .chartYScale(domain: .automatic(includesZero: metric == .sleep))
            .chartXAxis { AxisMarks(values: .automatic(desiredCount: 4)) { AxisValueLabel(format: .dateTime.month(.abbreviated).day()) } }
            .frame(height: 170)
            .accessibilityLabel("\(title) each night over the range")
            Text(note).font(.footnote).foregroundStyle(.secondary)
        }
    }
}

private struct NightRow: View {
    let n: HealthNight

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(Fmt.day(n.day, .dateTime.weekday(.abbreviated).month(.abbreviated).day())).fontWeight(.semibold)
                Spacer()
                Text(HealthFmt.source(n.source)).font(.caption).foregroundStyle(.secondary)
            }
            HStack(spacing: 14) {
                value("HRV", n.hrv.map(HealthFmt.ms), n.hrvStatus)
                value("RHR", n.restingHr.map(HealthFmt.bpm), n.restingHrStatus)
                value("Sleep", n.sleepH.map(HealthFmt.hours), n.sleepStatus)
            }
            .font(.subheadline)
        }
        .padding(.vertical, 2)
    }

    private func value(_ label: String, _ v: String?, _ status: String) -> some View {
        HStack(spacing: 4) {
            Text(label).foregroundStyle(.secondary)
            Text(v ?? "–").monospacedDigit()
                .fontWeight(v != nil && HealthFmt.flagged(status) ? .semibold : .regular)
                .foregroundStyle(v == nil ? Color.secondary : HealthFmt.flagged(status) ? Tone.warn.ink : Color.primary)
        }
    }
}
