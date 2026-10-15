import Foundation

// The Load page's weekly stats, the next 7 days and the scheduled-run projection, the coach's
// take on the page, today's scheduled run, and tap-a-number explanations (engine/talus/api.py).

/// Everything done in one Monday week, or part of one. `days` is how many of its 7 days have
/// happened; `last_week_to_date` has none.
struct WeekTotals: Codable, Hashable {
    let start: String
    let activities: Int
    let distanceMi: Double
    let gainFt: Double
    let lossFt: Double
    let effortMi: Double
    let movingTimeS: Double
    let cardioLoad: Double
    let climbLoad: Double
    let descentLoad: Double
    let days: Int?
}

/// The next 7 days: what's scheduled, and per ledger the total that holds your normal and the most
/// before ramping too fast, spread evenly, in hours, miles or feet. Ledgers still building have no room.
struct NextWeek: Codable, Hashable {
    struct Scheduled: Codable, Hashable {
        let runs: Int
        let miles: Double
        let gainFt: Double
        let lossFt: Double
        let hours: Double?
    }

    struct Room: Codable, Hashable {
        let unit: String
        let hold: Double
        let limit: Double
    }

    let start: String
    let end: String
    let scheduled: Scheduled
    let room: [String: Room]
}

/// One day of a ledger carried forward through the scheduled runs.
struct ProjectedDay: Codable, Hashable {
    let day: String
    let fitness: Double
    let fatigue: Double
}

/// A calendar week whose scheduled runs would ramp too fast or overreach, and on which ledgers.
/// (The Plan calendar's ScheduleWarning is the same warning without the ledgers.)
struct LedgerWarning: Codable, Hashable {
    let week: String
    let ledgers: [String]
    let key: String
    let text: String
}

struct Projection: Codable, Hashable {
    let series: [String: [ProjectedDay]]
    let warnings: [LedgerWarning]
    let until: String?
}

/// GET /api/coach/load: last week, the trend, what to watch and what the next 7 days should hold.
struct LoadReply: Codable, Hashable {
    let lastWeek: String
    let trend: String
    let watch: String
    let next: String
}

/// Today's scheduled run, the athlete's own (`manual`) or the plan's, or the plan's rest day.
struct PlannedToday: Codable, Hashable {
    let title: String
    let detail: String
    let source: String
    let kind: String
    let miles: Double
    let gainFt: Double
    let lossFt: Double
}

/// GET /api/explain/{path}: what went into a number, its formula and the arithmetic.
struct Explanation: Codable, Hashable {
    struct Input: Codable, Hashable {
        let label: String
        let value: String
    }

    let title: String
    let value: Double?
    let unit: String?
    let formula: String
    let inputs: [Input]
    let steps: [String]
    let note: String?
}
