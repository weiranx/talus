import Foundation

// The objective, plan, calendar and chat shapes (engine/talus/api.py, web/lib/types.ts). Every
// number is the engine's.

struct Vol: Codable, Hashable {
    let miles: Double
    let gainFt: Double
    let lossFt: Double
}

struct WeekTarget: Codable, Hashable {
    let miles: Double
    let gainFt: Double
    let lossFt: Double
    let focus: String
}

struct WeekActual: Codable, Hashable {
    let miles: Double
    let gainFt: Double
    let lossFt: Double
    let belowAetPct: Double?
}

/// A week's target against done plus still scheduled, as the calendar counts them.
struct WeekProgress: Codable, Hashable {
    let start: String
    let dated: Bool
    let target: WeekTarget?
    let done: WeekActual?
    let ahead: Vol

    enum Cover: String { case covered, close, short, over }

    /// Mirrors cover in web/lib/plan.ts: over when any dimension passes 115%, covered when every
    /// one reaches 90%, short when any falls under 70%.
    var cover: (share: [String: Double], state: Cover)? {
        guard let t = target else { return nil }
        func share(_ have: Double, _ want: Double) -> Double { want > 0 ? have / want : 1 }
        let s = ["miles": share((done?.miles ?? 0) + ahead.miles, t.miles),
                 "gain_ft": share((done?.gainFt ?? 0) + ahead.gainFt, t.gainFt),
                 "loss_ft": share((done?.lossFt ?? 0) + ahead.lossFt, t.lossFt)]
        let v = Array(s.values)
        let state: Cover = v.contains { $0 > 1.15 } ? .over : v.allSatisfy { $0 >= 0.9 } ? .covered
            : v.contains { $0 < 0.7 } ? .short : .close
        return (s, state)
    }
}

struct Dim: Codable, Hashable {
    let key: String
    let name: String
    let capacityLabel: String
    let demand: Double?
    let capacity: Double
    let unit: String?
    let rule: Double?
    let ratio: Double?
    let status: String
    let statusText: String
}

struct ReadinessEval: Codable, Hashable {
    let dims: [Dim]
    let limiter: String?
    let verdict: String
}

struct TimeRange: Codable, Hashable {
    let lowH: Double
    let highH: Double
}

struct PlanReadiness: Codable, Hashable {
    struct Demand: Codable, Hashable { let time: TimeRange }
    let dims: [Dim]
    let limiter: String?
    let verdict: String
    let demand: Demand
}

struct Objective: Codable, Hashable {
    let id: Int
    let name: String
    let day: String
    let distanceMi: Double
    let gainFt: Double
    let lossFt: Double
    let longestClimbFt: Double?
    let longestDescentFt: Double?
    let highPointFt: Double?
    let source: String
    let daysToGo: Int
}

struct Constraints: Codable, Hashable {
    var days: [Int]
    var weekdayMin: Int
    var weekendMin: Int
    var longDay: Int
    var notes: String
}

struct LongDay: Codable, Hashable {
    let miles: Double
    let gainFt: Double
    let lossFt: Double
    let effortMi: Double
}

struct PlanWeek: Codable, Hashable, Identifiable {
    let index: Int
    let start: String
    let focus: String
    let miles: Double
    let gainFt: Double
    let lossFt: Double
    let hours: Double
    let long: LongDay?
    let limitedBy: [String]
    let notes: [String]
    var id: String { start }
}

struct WeekBrief: Codable, Hashable {
    let focus: String
    let miles: Double
    let gainFt: Double
}

struct PlanChange: Codable, Hashable {
    let start: String
    let before: WeekBrief?
    let after: WeekBrief
}

struct PlanOptions: Codable, Hashable {
    struct Smaller: Codable, Hashable { let distanceMi: Double; let gainFt: Double }
    let smaller: Smaller?
    let later: String?
}

struct Plan: Codable, Hashable {
    let id: Int
    let status: String
    let reasonText: String
    let builtOn: String
    let baseline: Vol
    let peak: Vol
    let weeks: [PlanWeek]
    let projected: ReadinessEval
    let reachable: Bool
    let options: PlanOptions
    let changes: [PlanChange]
}

struct PlanSession: Codable, Hashable {
    let day: String
    let kind: String
    let miles: Double
    let gainFt: Double
    let lossFt: Double
    let minutes: Double
    let title: String
    let how: String
    let own: Bool?
}

struct ReviewFlag: Codable, Hashable {
    let ledger: String
    let key: String
    let text: String
}

struct WeekReview: Codable, Hashable {
    struct Actual: Codable, Hashable { let belowAetPct: Double? }
    let start: String
    let end: String
    let actual: Actual
    let flags: [ReviewFlag]
    let replan: [String]
    let replanText: [String]
}

struct PlanPage: Codable, Hashable {
    let date: String
    let constraints: Constraints
    let objective: Objective?
    let readiness: PlanReadiness?
    let accepted: Plan?
    let draft: Plan?
    let sessions: [PlanSession]
    let review: WeekReview?
    let fitnessOnly: String
    let thisWeek: WeekProgress?
    let actuals: [String: WeekActual]?
}

struct ScheduledRun: Codable, Hashable {
    struct Matched: Codable, Hashable {
        let id: Int
        let name: String?
        let distanceMi: Double
        let gainFt: Double
    }
    let id: Int?
    let day: String
    let kind: String
    let miles: Double
    let gainFt: Double
    let lossFt: Double
    let minutes: Double
    let estimatedMinutes: Bool
    let zone: Int?
    let notes: String
    let source: String
    let title: String
    let how: String?
    let status: String
    let activity: Matched?
}

struct ScheduleWarning: Codable, Hashable {
    let week: String
    let text: String
}

struct RunKind: Codable, Hashable {
    let key: String
    let label: String
}

struct Schedule: Codable, Hashable {
    let date: String
    let start: String
    let items: [ScheduledRun]
    let weeks: [WeekProgress]
    let warnings: [ScheduleWarning]
    let kinds: [RunKind]
    let zoneModel: ZoneModel?
}

/// A coach reply of any shape: the brief, the plan read, the weekly review or a workout read.
struct Coached<T: Codable & Hashable>: Codable, Hashable {
    let reply: T
    let source: String
    let fallbackReason: String?
    let logId: Int?
    let feedback: Int?
    let cost: AICost?
}

struct PlanReply: Codable, Hashable {
    let verdict: String
    let limiter: String
    let plan: String
}

struct ReviewReply: Codable, Hashable {
    let week: String
    let next: String
}

struct ReviewResponse: Codable, Hashable {
    let reply: ReviewReply
    let source: String
    let fallbackReason: String?
    let logId: Int?
    let feedback: Int?
    let cost: AICost?
    let review: WeekReview
}

struct AnalysisReply: Codable, Hashable {
    let job: String
    let off: String
    let cost: String
    let changes: String
    let takeaway: String
}

struct ChatMessage: Codable, Hashable {
    let id: Int
    let createdAt: String
    let role: String
    let text: String
    let source: String?
    let logId: Int?
    let cost: AICost?
}

struct ChatLog: Codable, Hashable {
    let messages: [ChatMessage]
}

/// A GPX route's numbers, to fill in the objective form.
struct RouteNumbers: Codable, Hashable {
    let name: String?
    let distanceMi: Double
    let gainFt: Double
    let lossFt: Double
    let longestDescentFt: Double?
    let longestClimbFt: Double?
    let highPointFt: Double?
}

enum PlanText {
    static let dayNames = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    static let dayShort = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    static func focus(_ f: String) -> String {
        switch f {
        case "build": "Build"
        case "peak": "Peak"
        case "recovery": "Recovery"
        case "taper": "Taper"
        case "race": "Objective week"
        default: Fmt.capitalized(f)
        }
    }

    static func status(_ s: String) -> Tone {
        switch s {
        case "ready": .good
        case "close": .warn
        case "gap": .bad
        default: .neutral
        }
    }

    /// h:mm, as on the web.
    static func hours(_ h: Double) -> String {
        let m = Int((h * 60).rounded())
        return String(format: "%d:%02d", m / 60, m % 60)
    }

    static func minutes(_ m: Double) -> String {
        let r = Int((m / 5).rounded()) * 5
        guard r >= 60 else { return "\(r) min" }
        return r % 60 == 0 ? "\(r / 60) h" : "\(r / 60) h \(r % 60) min"
    }

    /// A dimension's value: hours and effort miles to a tenth, the rest whole.
    static func dimValue(_ d: Dim, _ v: Double?) -> String {
        guard let v else { return "–" }
        if d.key == "altitude" { return d.demand == v ? Fmt.n0(v) : String(format: "%.1f", v) }
        if d.unit == "h" || d.unit == "effort mi" { return String(format: "%.1f", v) }
        return Fmt.n0(v)
    }

    static let dimUnit = ["big_day": "effort mi", "time_on_feet": "h", "climbing_volume": "ft a week",
                          "descent_durability": "ft in one descent", "descent_volume": "ft a week", "altitude": "h up high"]

    /// Same labels as web/lib/coach.ts.
    static func coachNote(_ source: String, _ reason: String?, _ what: String) -> String {
        if source == "coach" { return "Written by the AI coach from your engine numbers. Every number was checked against the data." }
        switch reason {
        case "no_key": return "Rule-based \(what). Add an Anthropic API key to the engine's .env to turn on the AI coach."
        case "off": return "Rule-based \(what). The AI coach is turned off in Settings."
        case "cap": return "Rule-based \(what). This month's AI coach budget is used up."
        case "rejected": return "Rule-based \(what). The coach quoted numbers that aren't in your data, so its reply was dropped."
        default: return "Rule-based \(what). The AI coach could not answer this time."
        }
    }

    static func iso(_ d: Date) -> String {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: d)
    }

    static func addDays(_ iso: String, _ n: Int) -> String {
        guard let d = Fmt.date(iso), let t = Calendar(identifier: .gregorian).date(byAdding: .day, value: n, to: d) else { return iso }
        return Self.iso(t)
    }
}
