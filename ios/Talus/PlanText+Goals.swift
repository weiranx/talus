import SwiftUI

// Goals, race types, speed work and hill sessions: the same words as web/lib/plan.ts.

extension PlanText {
    /// What you can train for, in the web's order. A race or trip has a day to be ready for; the
    /// rest are blocks of weekly targets.
    static let goals: [(key: String, label: String)] = [
        ("race", "A race"), ("trip", "A mountain objective (peak, FKT, trip)"), ("base", "Build base"),
        ("maintain", "Maintain fitness"), ("comeback", "Come back from a break"), ("faster", "Get faster"),
        ("target", "Yearly target"), ("rolling", "Ongoing plan"),
    ]

    static func goalText(_ g: String) -> String { goals.first { $0.key == g }?.label ?? Fmt.capitalized(g) }

    static func isEvent(_ goal: String?) -> Bool { goal == nil || goal == "race" || goal == "trip" }

    /// The weekly speed session, in the web's order.
    static let workouts: [(key: String, label: String)] = [
        ("none", "Default"), ("track", "Track intervals"), ("tempo", "Tempo"), ("hill_repeats", "Hill repeats"),
        ("race_pace", "Race pace"), ("threshold", "Threshold intervals"), ("fartlek", "Fartlek"), ("progression", "Progression run"),
    ]

    static func workoutText(_ w: String) -> String { workouts.first { $0.key == w }?.label ?? Fmt.capitalized(w) }

    static let hillText = ["vert_climb": "Long climb", "power_hike": "Power-hike intervals", "downhill": "Downhill durability",
                           "muscular_endurance": "Muscular endurance", "hill_sprints": "Hill sprints"]

    /// What each race type's plan does differently, for the plan page. Each starts with a space.
    static let raceTypeText = [
        "road": " A road plan: a long run sized to the race, less vert, a weekly quality session and a taper.",
        "hundred": " A 100-mile plan: long days well short of the race that practice power-hiking, race food and, every third build week, the dark; then a two-week taper.",
        "multiday": " A multi-day plan: long days well short of the race that practice power-hiking, race food and, every third build week, the dark with a short sleep stop; then a two-week taper.",
    ]

    /// The week's focus, with its speed work and hill session: "Build · fartlek · long climb".
    static func weekFocus(_ w: PlanWeek) -> String {
        var parts = [focus(w.focus) + (w.limitedBy.isEmpty ? "" : " *")]
        if let k = w.workout, k != "none" { parts.append(workoutText(k).lowercased()) }
        if let h = w.hillSession { parts.append((hillText[h] ?? h).lowercased()) }
        return parts.joined(separator: " · ")
    }

    /// The draft's badge: reachable inside the limits, or on pace for a yearly target.
    static func reachText(_ plan: Plan, goal: String?) -> String {
        if goal == "target" { return plan.reachable ? "On pace for the year" : "Short of the year's target" }
        return plan.reachable ? "Reachable inside the limits" : "Out of reach by this date"
    }

    /// The road card's line: peak, lighter weeks, back-to-back long days, race type and who built it.
    static func roadText(_ plan: Plan, event: Bool) -> String {
        var t = "\(event ? "Peak" : "Target") \(Fmt.mi(plan.peak.miles)) mi and \(Fmt.n0(plan.peak.gainFt)) ft up a week. "
            + (event ? "Lighter weeks are recovery and taper" : "Lighter weeks are recovery weeks") + "; the marker is this week."
        if plan.backToBack == true { t += " Long weekends are back-to-back long days, so no single day has to carry the whole distance." }
        t += raceTypeText[plan.raceType ?? ""] ?? ""
        if plan.builder == "coach" { t += " The AI coach built these weeks inside the engine's limits." }
        return t
    }
}

/// Who built the weeks: the AI coach, with the engine holding each week to its limits, or the
/// engine alone. While the coach is still building, the engine's weeks show and the page checks
/// back every 5 seconds.
struct BuiltBy: View {
    let plan: Plan
    let reload: () async -> Void

    var body: some View {
        if plan.building == true {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text("The AI coach is building its version of these weeks. Until it's ready, these are the engine's.")
            }
            .font(.subheadline).foregroundStyle(.secondary)
            .task(id: plan.id) {
                while !Task.isCancelled {
                    try? await Task.sleep(for: .seconds(5))
                    if Task.isCancelled { break }
                    await reload()
                }
            }
        } else if plan.builder == "coach" {
            let held = plan.heldWeeks ?? 0
            (Text("Built by the AI coach. ").fontWeight(.semibold)
                + Text(plan.approach.map { "\($0) " } ?? "")
                + Text("The engine checked every week against your load limits"
                       + (held > 0 ? " and held \(held) week\(held == 1 ? "" : "s") the coach asked for to the 10% build limit." : "."))
                .foregroundStyle(.secondary))
                .font(.subheadline)
        }
    }
}

/// A session's steps, warm-up to cool-down, numbered.
struct SessionSteps: View {
    let steps: [String]
    var color: Color = .secondary

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(Array(steps.enumerated()), id: \.offset) { i, t in
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text("\(i + 1).").monospacedDigit()
                    Text(t).fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .foregroundStyle(color)
    }
}
