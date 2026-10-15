import Foundation

// Shapes of the engine's JSON (engine/talus/api.py). Every number comes from the engine; the app
// only formats it. Distances are miles and vert is feet. Fields the engine may send as null are
// optional, and anything added later is ignored. Keys decode from snake_case, which capitalizes
// a letter after digits: intensity_28d is intensity28D.

struct Flag: Codable, Hashable {
    let key: String
    let text: String
}

struct LedgerDay: Codable, Hashable {
    let day: String
    let load: Double
    let fitness: Double
    let fatigue: Double
    let form: Double
    let vsNormal: Double?
    let vsNormalText: String
    let flags: [Flag]
    let building: Bool?
}

struct SparkPoint: Codable, Hashable {
    let day: String
    let fitness: Double
    let fatigue: Double
}

struct TodayLedger: Codable, Hashable {
    let day: String
    let fitness: Double
    let fatigue: Double
    let vsNormal: Double?
    let vsNormalText: String
    let flags: [Flag]
    let building: Bool?
    let spark: [SparkPoint]
}

struct ActivitySummary: Codable, Hashable, Identifiable {
    let id: Int
    let day: String
    let startTime: String
    let name: String?
    let sport: String?
    let source: String
    let deviceName: String?
    let distanceMi: Double
    let gainFt: Double
    let lossFt: Double
    let effortMi: Double
    let movingTimeS: Double
    let cardioLoad: Double?
    let climbLoad: Double?
    let descentLoad: Double?
}

struct Thresholds: Codable, Hashable {
    let resting: Double
    let aet: Double
    let ant: Double
    let max: Double
    let source: String
    let aerobicGap: Double
}

struct ZoneShare: Codable, Hashable {
    let minutes: [String: Double]
    let share: [String: Double]
}

struct ZoneInfo: Codable, Hashable {
    let zone: Int
    let name: String
    let text: String
}

struct ZoneModel: Codable, Hashable {
    let zones: Int
    let custom: Bool
    let list: [ZoneInfo]
}

struct Signal: Codable, Hashable {
    let status: String
    let text: String
}

struct Wellness: Codable, Hashable {
    let hrv: Signal
    let restingHr: Signal
    let sleep: Signal
}

struct CheckIn: Codable, Hashable {
    var soreness: Int
    var energy: Int
    var pain: Int
}

struct ReadinessPart: Codable, Hashable {
    let state: String
    let detail: String
    let reason: String
    let limiter: String?
}

struct Readiness: Codable, Hashable {
    let engine: ReadinessPart
    let legs: ReadinessPart
}

struct ScoreItem: Codable, Hashable {
    let key: String
    let name: String
    let points: Int
    let used: Bool
    let text: String
}

struct ScorePoint: Codable, Hashable {
    let day: String
    let score: Int
    let label: String
    let call: String
}

/// The engine's 0 to 100 readiness score. The day's call sets the top of its band.
struct ReadinessScore: Codable, Hashable {
    let score: Int
    let label: String
    let call: String
    let held: String?
    let items: [ScoreItem]
    let basis: String
    let trend: [ScorePoint]
}

struct Call: Codable, Hashable {
    let call: String
    let session: String
    let detail: String
    let why: String
    let planned: Bool?
}

struct Week: Codable, Hashable {
    let start: String
    let activities: [ActivitySummary]
    let distanceMi: Double
    let gainFt: Double
    let lossFt: Double
}

struct Today: Codable, Hashable {
    let date: String
    let planned: PlannedToday?
    let checkin: CheckIn?
    let wellness: Wellness?
    let readiness: Readiness
    let score: ReadinessScore?
    let call: Call
    let ledgers: [String: TodayLedger]
    let week: Week
    let intensity28D: ZoneShare
    let zones28D: ZoneShare?
    let zoneModel: ZoneModel?
}

struct CoachReply: Codable, Hashable {
    let reply: Call
    let source: String
    let fallbackReason: String?
    let logId: Int?
    let feedback: Int?
    let cost: AICost?
}

/// What the AI call behind a coach reply cost. Only sent when the admin turns on "Show AI cost".
struct AICost: Codable, Hashable {
    let usd: Double
    let model: String?
    let inputTokens: Int
    let outputTokens: Int
    let cached: Bool

    /// Same wording as web/components/AiCost.tsx.
    var text: String {
        let dollars = String(format: usd < 0.01 ? "$%.4f" : "$%.3f", usd)
        let tokens = "\(inputTokens.formatted()) in, \(outputTokens.formatted()) out"
        return cached ? "AI cost $0 (reused; first call \(dollars), \(model ?? "unknown model"), \(tokens))"
            : "AI cost \(dollars) · \(model ?? "unknown model") · \(tokens) tokens"
    }
}

struct LoadLedger: Codable, Hashable {
    let today: LedgerDay?
    let series: [LedgerDay]
}

struct Gap: Codable, Hashable {
    let from: String
    let to: String
}

struct History: Codable, Hashable {
    let since: String?
    let gap: Gap?
}

struct WeekIntensity: Codable, Hashable {
    let start: String
    let share: [String: Double]
    let zones: ZoneShare?
}

struct LoadData: Codable, Hashable {
    let date: String
    let ledgers: [String: LoadLedger]
    let history: History
    let intensityByWeek: [WeekIntensity]
    let biggestDays: [ActivitySummary]
    let thresholds: Thresholds?
    // Optional so an older engine's answer still decodes (LoadModels.swift).
    let weekly: [WeekTotals]?
    let lastWeekToDate: WeekTotals?
    let zoneModel: ZoneModel?
    let projection: Projection?
    let nextWeek: NextWeek?
}

struct LoadUsual: Codable, Hashable {
    let load: Double?
    let timesUsualDay: Double?
    let sixWeekHigh: Bool
}

struct Segment: Codable, Hashable, Identifiable {
    let index: Int
    let kind: String
    let distanceMi: Double
    let gainFt: Double
    let lossFt: Double
    let avgGrade: Double
    let timeS: Double
    var id: Int { index }
}

struct Streams: Codable, Hashable {
    let timeS: [Double]
    let distanceMi: [Double]?
    let altitudeFt: [Double]?
    let heartRate: [Double]?
    let lat: [Double?]?
    let lon: [Double?]?
}

struct ActivityDetail: Codable, Hashable {
    let id: Int
    let name: String?
    let day: String
    let startTime: String
    let source: String
    let deviceName: String?
    let sport: String?
    let distanceMi: Double
    let gainFt: Double
    let lossFt: Double
    let effortMi: Double
    let movingTimeS: Double
    let elapsedTimeS: Double
    let hikeShare: Double?
    let longestClimbFt: Double?
    let longestDescentFt: Double?
    let minutesAbove8000Ft: Double?
    let notes: [String]
    let loads: [String: LoadUsual]
    let segments: [Segment]
    let streams: Streams
}

struct AuthStatus: Codable {
    struct Account: Codable { let email: String }
    let account: Account?
    let hasAccounts: Bool
    let signupOpen: Bool
}

/// Ledgers in display order, as on the web. Names the engine adds later follow, alphabetically.
enum LedgerName {
    static let order = ["cardio", "distance", "climb", "descent"]

    static func sorted(_ names: some Collection<String>) -> [String] {
        names.sorted { a, b in
            let ia = order.firstIndex(of: a) ?? order.count, ib = order.firstIndex(of: b) ?? order.count
            return ia == ib ? a < b : ia < ib
        }
    }
}

/// GET /api/onboarding: which first-run steps are done. The phone only shows what Today still needs.
struct Onboarding: Codable {
    struct Step: Codable, Hashable {
        let key: String
        let done: Bool
        let skipped: Bool
        let required: Bool
    }
    let needed: Bool
    let steps: [Step]
    let activities: Int
}
