import SwiftUI

// Estimated vertical speed and observed gait comparisons against recent activity history.
// Also shows how the second half held up on grade-adjusted pace. Same wording as
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
            Text("Estimated vertical speed by grade band, compared with your median from similar activity types in the preceding 90 days. Comparisons require at least 3 prior activities in the band and do not account for effort or trail conditions.")
                .font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            group("Climbs", climbs)
            group("Descents", descents)
            if !t.hikeOrRun.isEmpty {
                Divider()
                Text("Observed running vs. hiking").font(.subheadline.weight(.semibold))
                Text("Observed within this activity on different segments. This does not predict the benefit or HR cost of switching gait on the same terrain.").font(.caption).foregroundStyle(.secondary)
                ForEach(t.hikeOrRun) { hikeOrRun($0).font(.subheadline).fixedSize(horizontal: false, vertical: true) }
            }
            Text("ft/h is estimated vertical feet gained or lost per moving hour in that grade band. HR in parentheses is the difference from your recent median in the band. Hiking share is estimated from cadence, or speed when cadence is unavailable; activities recorded as hikes or walks are treated as hiking. Lower speed at lower HR does not by itself indicate worse performance. \(footer)")
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
        let gain = h.runRateGainPct >= 0 ? "\(Fmt.plain(h.runRateGainPct))% higher vertical speed" : "\(Fmt.plain(-h.runRateGainPct))% lower vertical speed"
        var t = Text("On ") + Text(h.label).bold() + Text(" climbs, segments classified as running had ") + Text(gain).bold()
            + Text(" than those classified as hiking (\(Fmt.n0(h.runRateFtH)) against \(Fmt.n0(h.hikeRateFtH)) ft/h)")
        if let c = h.runHrCostBpm {
            t = t + Text(" with average HR ") + Text(c >= 0 ? "\(Fmt.plain(c)) bpm higher" : "\(Fmt.plain(-c)) bpm lower").bold()
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
            (Text("Pace \(Fmt.pace(b.paceMinMi)) /mi · HR ") + hr + Text(" · Est. hiking share \(Fmt.pct(b.hikeShare))"))
                .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private var vsUsual: some View {
        if let u = b.usual, let change = u.rateChangePct {
            let faster = change > 0
            Pill(text: abs(change) < 1 ? "At median" : "\(Fmt.plain(abs(change)))% \(faster ? "above" : "below") median", tone: .neutral)
            if u.fastest { Pill(text: "90-day highest rate", tone: .neutral) }
            Text("median \(Fmt.n0(u.vertRateFtH)) ft/h").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
        } else {
            Text("Comparison unavailable").font(.caption).foregroundStyle(.secondary)
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
            SectionLabel("Durability")
            Text(status).font(.headline)
            Text(d.decouplingPct.map { $0 == 0 ? "Aerobic efficiency unchanged" : efficiencyLabel($0) } ?? "Not enough heart-rate evidence")
                .font(.title2.weight(.semibold)).monospacedDigit()
            Text(history).font(.subheadline).foregroundStyle(.secondary)
            if let interpretation { Text(interpretation).font(.subheadline) }
            Text("Comparison by moving-time halves").font(.caption).foregroundStyle(.secondary)
            comparison(rows)
            DisclosureGroup("Compare terrain and duration") {
                comparison(contextRows)
            }.font(.subheadline)
            DisclosureGroup("How this is calculated") {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Aerobic efficiency compares grade-adjusted speed per heartbeat between moving-time halves. A positive drop means lower efficiency in the second half; a gain means higher efficiency. Talus labels a drop below 5% as steady, 5% to below 10% as declined, and 10% or more as substantially declined. This assesses aerobic efficiency, not overall durability.")
                    Text("Grade-adjusted pace estimates equivalent flat-ground pace based on slope. It does not fully account for terrain or conditions. The first 10 moving minutes are excluded from first-half heart rate and efficiency, but included in the displayed pace and duration, so the table alone will not reproduce the efficiency result.")
                    Text("The recent median uses activities of the same type in the prior 90 days with an efficiency measurement; these are not matched by duration.")
                }.font(.caption).foregroundStyle(.secondary)
            }.font(.subheadline)
        }
    }

    private func comparison(_ values: [(String, String, String, String)]) -> some View {
        Grid(alignment: .trailing, horizontalSpacing: 12, verticalSpacing: 6) {
            GridRow { Text("First half"); Text("Second half") }
                .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            ForEach(values, id: \.0) { label, first, second, change in
                GridRow {
                    Text(label).font(.subheadline).foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading).gridCellColumns(2)
                }.padding(.top, 6)
                GridRow { Text(first); Text(second) }
                    .font(.subheadline.monospacedDigit())
                    .frame(maxWidth: .infinity, alignment: .trailing)
                if !change.isEmpty {
                    GridRow {
                        Text("Change: \(change)").font(.caption).foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading).gridCellColumns(2)
                    }
                }
            }
        }
    }

    private func efficiencyLabel(_ value: Double) -> String {
        "\(Fmt.plain(abs(value)))% efficiency \(value < 0 ? "gain" : "drop")"
    }
    private func movingTime(_ seconds: Double) -> String {
        let minutes = Int((seconds / 60).rounded())
        return "\(minutes / 60)h \(minutes % 60)m"
    }
    private var status: String {
        guard let v = d.decouplingPct else { return "Aerobic efficiency unavailable" }
        return v < 5 ? "Aerobic effort held steady" : v < 10 ? "Aerobic efficiency declined" : "Aerobic efficiency declined substantially"
    }
    private var history: String {
        guard let v = d.usualDecouplingPct else { return "Recent median unavailable: not enough comparable activity history." }
        return "Recent median: \(efficiencyLabel(v)) across \(d.usualRuns) activities of the same type in the prior 90 days."
    }
    private var interpretation: String? {
        guard let x = d.first.flatPaceMinMi, let y = d.second.flatPaceMinMi,
              let h = d.first.avgHr, let j = d.second.avgHr else { return nil }
        let pace = y > x ? "slowed" : y < x ? "quickened" : "held steady"
        let hr = j < h ? "fell" : j > h ? "rose" : "held steady"
        let steady = d.decouplingPct.map { abs($0) < 5 } ?? false
        return "Grade-adjusted pace \(pace) while average heart rate \(hr)." + (steady ? " Aerobic efficiency was nearly unchanged." : "")
    }
    private var rows: [(String, String, String, String)] {
        let a = d.first, b = d.second
        var paceChange = "Unavailable"
        if let x = a.flatPaceMinMi, let y = b.flatPaceMinMi {
            let delta = Int((y * 60).rounded()) - Int((x * 60).rounded())
            paceChange = delta == 0 ? "Unchanged" : "\(abs(delta)) sec/mi \(delta > 0 ? "slower" : "faster")"
        }
        let pace: (Double?) -> String = { $0.map { "\(Fmt.pace($0))/mi" } ?? "Unavailable" }
        let hr: (Double?) -> String = { $0.map { "\(Int($0.rounded())) bpm" } ?? "Unavailable" }
        let climb: (Double?) -> String = { $0.map { "\(Fmt.n0($0)) ft/h" } ?? "Unavailable" }
        var hrChange = "Unavailable", climbChange = "Unavailable"
        if let x = a.avgHr, let y = b.avgHr { hrChange = "\(Fmt.signedPlain(y.rounded() - x.rounded())) bpm" }
        if let x = a.climbRateFtH, let y = b.climbRateFtH, x > 0 {
            climbChange = "\(Fmt.signedPlain(((y / x - 1) * 100).rounded()))%"
        }
        return [
            ("Grade-adjusted pace", pace(a.flatPaceMinMi), pace(b.flatPaceMinMi), paceChange),
            ("Average heart rate", hr(a.avgHr), hr(b.avgHr), hrChange),
            ("Uphill speed · grades ≥8%", climb(a.climbRateFtH), climb(b.climbRateFtH), climbChange),
        ]
    }
    private var contextRows: [(String, String, String, String)] {
        let a = d.first, b = d.second
        return [
            ("Distance", "\(Fmt.mi(a.distanceMi)) mi", "\(Fmt.mi(b.distanceMi)) mi", ""),
            ("Elevation gain", "\(Fmt.n0(a.gainFt)) ft", "\(Fmt.n0(b.gainFt)) ft", ""),
            ("Moving time", movingTime(a.timeS), movingTime(b.timeS), ""),
        ]
    }
}
