import SwiftUI

// Climbing and descending by grade against the athlete's own last 90 days, what running a climb
// bought over hiking it, and how the second half held up on grade-adjusted pace. Same wording as
// web/components/TerrainCards.tsx; the web's wide tables become one block per grade.

extension Fmt {
    /// Minutes per mile as 12:05, or – without one.
    static func pace(_ minPerMi: Double?) -> String {
        guard let minPerMi, minPerMi > 0 else { return "–" }
        let s = Int((minPerMi * 60).rounded())
        return String(format: "%d:%02d", s / 60, s % 60)
    }

    /// +5, −3 or 0, with a true minus sign.
    static func signed(_ v: Double) -> String {
        let n = Int(v.rounded())
        return n > 0 ? "+\(n)" : n < 0 ? "−\(abs(n))" : "0"
    }

    /// A whole number when the engine sent one (66), else one decimal (36.7), as the web prints it.
    static func plain(_ v: Double) -> String {
        v == v.rounded() ? String(Int(v)) : String(format: "%.1f", v)
    }

    /// The signed form of `plain`: +5, −36.7 or 0.
    static func signedPlain(_ v: Double) -> String {
        v > 0 ? "+\(plain(v))" : v < 0 ? "−\(plain(-v))" : "0"
    }
}

struct GradeCard: View {
    let t: Terrain
    let footer: String

    var body: some View {
        let climbs = t.bands.filter { $0.kind == "climb" }
        let descents = t.bands.filter { $0.kind == "descent" }
        Card {
            SectionLabel("Climbing and descending by grade")
            Text("How fast you gained and lost vert on each grade, set against your own runs on the same grade over the last 90 days.")
                .font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            group("Climbs", climbs)
            group("Descents", descents)
            if !t.hikeOrRun.isEmpty {
                Divider()
                Text("Hike or run?").font(.subheadline.weight(.semibold))
                ForEach(t.hikeOrRun) { hikeOrRun($0).font(.subheadline).fixedSize(horizontal: false, vertical: true) }
            }
            Text("ft/h is vertical feet gained or lost per moving hour on that grade. HR in brackets is against your usual on the grade. Hiked is the share of the time spent hiking. \(footer)")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder
    private func group(_ title: String, _ bands: [GradeBand]) -> some View {
        if !bands.isEmpty {
            Divider()
            Text(title).font(.subheadline.weight(.semibold))
            ForEach(bands) { BandRow(b: $0) }
        }
    }

    private func hikeOrRun(_ h: HikeOrRun) -> Text {
        let gain = h.runRateGainPct >= 0 ? "\(Fmt.plain(h.runRateGainPct))% faster" : "\(Fmt.plain(-h.runRateGainPct))% slower"
        var t = Text("On ") + Text(h.label).bold() + Text(" climbs, running gained vert ") + Text(gain).bold()
            + Text(" than hiking (\(Fmt.n0(h.runRateFtH)) against \(Fmt.n0(h.hikeRateFtH)) ft/h)")
        if let c = h.runHrCostBpm {
            t = t + Text(" for ") + Text(c >= 0 ? "\(Fmt.plain(c)) more bpm" : "\(Fmt.plain(-c)) fewer bpm").bold()
        }
        return t + Text(".")
    }
}

private struct BandRow: View {
    let b: GradeBand

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                Text(b.label).fontWeight(.medium)
                Spacer()
                Text("\(b.kind == "climb" ? "+" : "−")\(Fmt.n0(b.vertFt)) ft · \(Fmt.duration(b.timeS))")
                    .font(.subheadline.monospacedDigit()).foregroundStyle(.secondary)
            }
            HStack(spacing: 6) {
                (Text(Fmt.n0(b.vertRateFtH)).font(.headline.monospacedDigit()) + Text(" ft/h").font(.caption).foregroundStyle(.secondary))
                vsUsual
            }
            (Text("Pace \(Fmt.pace(b.paceMinMi)) /mi · HR ") + hr + Text(" · Hiked \(Fmt.pct(b.hikeShare))"))
                .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private var vsUsual: some View {
        if let u = b.usual, let change = u.rateChangePct {
            let faster = change > 0
            let tone: Tone = abs(change) < 5 ? .neutral : faster ? .good : .warn
            Pill(text: abs(change) < 1 ? "same" : "\(Fmt.plain(abs(change)))% \(faster ? "faster" : "slower")", tone: tone)
            if u.fastest { Pill(text: "90-day best", tone: .good) }
            Text("usual \(Fmt.n0(u.vertRateFtH))").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
        } else {
            Text("not enough runs yet").font(.caption).foregroundStyle(.secondary)
        }
    }

    private var hr: Text {
        guard let avg = b.avgHr else { return Text("–") }
        let d = b.usual?.hrChangeBpm
        return Text("\(Int(avg.rounded()))") + Text(d.map { $0 != 0 ? " (\(Fmt.signed($0)))" : "" } ?? "")
    }
}

/// Gain and loss by the engine's three grade bands, for activities without the full analysis.
struct VertByGradeCard: View {
    let gain: [String: Double]
    let loss: [String: Double]
    let footer: String

    private static let bands = [("<8", "Under 8%"), ("8-20", "8 to 20%"), (">20", "Over 20%")]

    var body: some View {
        Card {
            SectionLabel("Vert by grade")
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
                GridRow {
                    Text("Grade")
                    Text("Gain, ft").gridColumnAlignment(.trailing)
                    Text("Descent, ft").gridColumnAlignment(.trailing)
                }
                .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                ForEach(Self.bands, id: \.0) { key, label in
                    GridRow {
                        Text(label)
                        Text(Fmt.n0(gain[key]))
                        Text(Fmt.n0(loss[key]))
                    }
                    .font(.subheadline.monospacedDigit())
                }
            }
            Text(footer).font(.caption).foregroundStyle(.secondary)
        }
    }
}

struct DurabilityCard: View {
    let d: Durability

    var body: some View {
        Card {
            HStack {
                SectionLabel("Durability")
                Spacer()
                if let v = verdict { Pill(text: v.0, tone: v.1) }
            }
            if !summary.isEmpty { Text(summary).font(.subheadline).fixedSize(horizontal: false, vertical: true) }
            // Each row's name sits above its two halves, so nothing truncates at phone width.
            Grid(alignment: .trailing, horizontalSpacing: 12, verticalSpacing: 4) {
                GridRow {
                    Text("First half")
                    Text("Second half")
                }
                .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                ForEach(rows, id: \.0) { k, a, b in
                    GridRow {
                        Text(k).font(.subheadline).foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .gridCellColumns(2)
                    }
                    .padding(.top, 4)
                    GridRow {
                        Text(a)
                        Text(b)
                    }
                    .font(.subheadline.monospacedDigit())
                    .frame(maxWidth: .infinity, alignment: .trailing)
                }
            }
            Text("Grade-adjusted pace (\(Fmt.pace(d.flatPaceMinMi)) /mi overall) is what each mile would have been on the flat for the same heart rate, so climbing out and running home compare fairly. The first 10 minutes are left out of the heart-rate read while it warms up.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private var verdict: (String, Tone)? {
        switch d.verdict {
        case "held": ("Held", .good)
        case "drifted": ("Drifted", .warn)
        case "faded": ("Faded", .bad)
        default: nil
        }
    }

    private var rows: [(String, String, String)] {
        let a = d.first, b = d.second
        var rows = [
            ("Distance and vert", "\(Fmt.mi(a.distanceMi)) mi, +\(Fmt.n0(a.gainFt)) ft", "\(Fmt.mi(b.distanceMi)) mi, +\(Fmt.n0(b.gainFt)) ft"),
            ("Moving time", Fmt.duration(a.timeS), Fmt.duration(b.timeS)),
            ("Grade-adjusted pace", "\(Fmt.pace(a.flatPaceMinMi)) /mi", "\(Fmt.pace(b.flatPaceMinMi)) /mi"),
        ]
        if let x = a.climbRateFtH, let y = b.climbRateFtH, x > 0, y > 0 {
            rows.append(("Climbing over 8%", "\(Fmt.n0(x)) ft/h", "\(Fmt.n0(y)) ft/h"))
        }
        if let x = a.avgHr, let y = b.avgHr, x > 0, y > 0 {
            rows.append(("Average HR", "\(Int(x.rounded()))", "\(Int(y.rounded()))"))
        }
        return rows
    }

    private var summary: String {
        if let drift = d.decouplingPct {
            let usual = d.usualDecouplingPct.map { " Your usual on runs this long is \(Fmt.signedPlain($0))%." } ?? ""
            if drift <= 0 { return "No drift: heart rate held or fell against grade-adjusted pace (\(Fmt.signedPlain(drift))%).\(usual)" }
            return "Heart rate drifted \(Fmt.plain(drift))% against grade-adjusted pace. Under 5% is a held aerobic effort; over 10% is a fade.\(usual)"
        }
        if let p = d.paceChangePct {
            return p > 0
                ? "Grade-adjusted pace slowed \(Fmt.plain(p))% in the second half. No heart rate, so drift can't be read."
                : "Grade-adjusted pace held or got quicker in the second half (\(Fmt.signedPlain(p))%)."
        }
        return ""
    }
}
