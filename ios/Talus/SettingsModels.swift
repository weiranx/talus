import Foundation

// Settings, account and admin shapes (engine/talus/api.py, web/lib/types.ts). The engine only
// says whether a key is set, never its value.

struct ZoneOption: Codable, Hashable {
    let derived: [Int]
    let custom: [Int]?
    let names: [String]
}

struct ZoneSettings: Codable, Hashable {
    let zones: Int
    let custom: Bool
    let floors: [Int]
    let derived: [Int]
    let options: [String: ZoneOption]
}

struct SettingsThresholds: Codable, Hashable {
    let resting: Double
    let aet: Double
    let ant: Double
    let max: Double
    let sex: String
    let thresholdPaceSPerMi: Double?
    let source: String
    let aetProvisional: Bool
    let zones: ZoneSettings
    let aerobicGap: Double
}

struct Estimate: Codable, Hashable {
    struct Run: Codable, Hashable { let id: Int; let day: String; let name: String? }
    let value: Double?
    let confidence: String
    let method: String
    let activities: [Run]
}

struct InferredThresholds: Codable, Hashable {
    let status: String
    let runsRead: Int
    let runsTotal: Int
    let days: Int
    let runsWithHr: Int
    let aet: Estimate?
    let ant: Estimate?
    let max: Estimate?
}

struct LastSync: Codable, Hashable {
    let at: String
    let source: String
    let since: String
    let listed: Int
    let new: Int
    let saved: Int
    let duplicates: Int?
    let error: String?
}

struct LastImport: Codable, Hashable {
    let at: String
    let source: String
    let files: Int
    let listed: Int
    let new: Int
    let duplicates: Int
    let failed: [String]
    let wellnessDays: Int
    let noData: Int?
    let error: String?
}

struct SourceInfo: Codable, Hashable {
    let name: String
    let keySet: Bool
    let kind: String?
    let athleteId: String?
    let athleteName: String?
    let oauthAvailable: Bool?
    let updatedAt: String?
    let activities: Int
    let latestActivity: String?
}

struct Sources: Codable, Hashable {
    struct Intervals: Codable, Hashable { let connected: Bool; let lastSync: LastSync? }
    struct Garmin: Codable, Hashable {
        let available: Bool
        let connected: Bool
        let updatedAt: String?
        let mfaPending: Bool
        let lastSync: LastSync?
    }
    struct Upload: Codable, Hashable { let lastImport: LastImport?; let maxMb: Int }
    let intervals: Intervals
    let garmin: Garmin
    let upload: Upload
}

struct CostEstimate: Codable, Hashable {
    let perCall: Double?
    let perMonth: Double?
    let callsPerMonth: Int
    let fromLog: Bool
}

struct CoachSettings: Codable, Hashable {
    struct Style: Codable, Hashable { let key: String; let label: String; let description: String }
    struct Model: Codable, Hashable { let id: String; let label: String }
    struct Cap: Codable, Hashable { let limit: Double?; let ceiling: Double?; let spent: Double; let month: String }
    struct Estimates: Codable, Hashable { let brief: [String: CostEstimate]; let analysis: [String: CostEstimate] }
    let enabled: Bool
    let keySet: Bool
    let briefModel: String
    let analysisModel: String
    let style: String
    let styles: [Style]
    let models: [Model]
    let estimate: Estimates
    let cap: Cap
}

struct ReadinessSettings: Codable, Hashable {
    let sleepNeedH: Double
    let sleepNeedSource: String
}

struct SettingsData: Codable, Hashable {
    let thresholds: SettingsThresholds?
    let inferred: InferredThresholds?
    let source: SourceInfo
    let sources: Sources
    let coach: CoachSettings
    let readiness: ReadinessSettings
}

struct Account: Codable, Hashable {
    struct Google: Codable, Hashable { let email: String? }
    let id: Int
    let email: String
    let isOwner: Bool
    let isAdmin: Bool
    let createdAt: String
    let hasPassword: Bool
    let google: Google?
    let googleAvailable: Bool
}

struct AdminAccount: Codable, Hashable, Identifiable {
    struct Sync: Codable, Hashable { let at: String; let source: String; let ok: Bool }
    struct Coach: Codable, Hashable {
        let spent: Double
        let ceiling: Double?
        let customCeiling: Bool
        let ownCap: Double?
        let limit: Double?
    }
    let id: Int
    let email: String
    let isOwner: Bool
    let isAdmin: Bool
    let createdAt: String
    let lastSeen: String?
    let disabledAt: String?
    let signIn: [String]
    let sessions: Int
    let activities: Int
    let connected: [String]
    let sources: [String]
    let lastSync: Sync?
    let coach: Coach
    let diskBytes: Int
}

struct AdminOverview: Codable, Hashable {
    struct Signup: Codable, Hashable { let open: Bool; let chosen: Bool? }
    struct Features: Codable, Hashable { let showAiCost: Bool }
    struct System: Codable, Hashable {
        struct Coach: Codable, Hashable { let promptVersion: String; let briefModel: String; let analysisModel: String; let keySet: Bool }
        struct Disk: Codable, Hashable { let total: Int; let free: Int }
        let revision: String?
        let modelVersion: String
        let coach: Coach
        let serverCeilingUsd: Double
        let dataBytes: Int
        let disk: Disk
    }
    struct LogEntry: Codable, Hashable {
        struct Detail: Codable, Hashable { let feature: String?; let to: Double? }
        let id: Int
        let at: String
        let action: String
        let admin: String?
        let target: String?
        let detail: Detail?
    }
    let accounts: [AdminAccount]
    let month: String
    let signup: Signup
    let features: Features
    let system: System
    let log: [LogEntry]
}

enum SettingsText {
    static func when(_ iso: String) -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let plain = ISO8601DateFormatter()
        let local = DateFormatter()
        local.locale = Locale(identifier: "en_US_POSIX")
        local.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        guard let d = f.date(from: iso) ?? plain.date(from: iso) ?? local.date(from: String(iso.prefix(19))) else { return iso }
        return d.formatted(.dateTime.month(.abbreviated).day().hour().minute())
    }

    static func sync(_ s: LastSync) -> String {
        if let e = s.error { return "Failed \(when(s.at)): \(e)" }
        let dups = (s.duplicates ?? 0) > 0 ? ", \(s.duplicates!) already here from another source" : ""
        return "\(when(s.at)): \(s.listed) activities since \(Fmt.day(s.since, .dateTime.month(.abbreviated).day())), \(s.new) new\(dups)."
    }

    static func importLine(_ r: LastImport) -> String {
        if let e = r.error { return "Failed \(when(r.at)): \(e)" }
        var parts = ["\(r.new) new \(r.new == 1 ? "activity" : "activities")"]
        if r.duplicates > 0 { parts.append("\(r.duplicates) already here from another source") }
        if r.listed - r.new - r.duplicates > 0 { parts.append("\(r.listed - r.new - r.duplicates) already imported") }
        if r.wellnessDays > 0 { parts.append("\(r.wellnessDays) days of resting HR and sleep") }
        if let n = r.noData, n > 0 { parts.append("\(n) workouts with no route or heart rate skipped") }
        if !r.failed.isEmpty { parts.append("\(r.failed.count) files unreadable") }
        return "\(r.source == "apple_health" ? "Apple Health export" : "Files"), \(when(r.at)): \(parts.joined(separator: ", "))."
    }

    static func money(_ v: Double?) -> String {
        guard let v else { return "–" }
        if v < 0.01 { return v == 0 ? "$0" : "under 1¢" }
        return v < 1 ? "\(Int((v * 100).rounded()))¢" : String(format: "$%.2f", v)
    }

    static func usd(_ v: Double) -> String { String(format: "$%.2f", v) }

    static func bytes(_ n: Int) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(n), countStyle: .binary)
    }
}
