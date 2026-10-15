import CoreLocation
import Foundation

// The engine's activity analysis (engine/talus/terrain.py) and heatmap (GET /api/heatmap), as
// web/lib/types.ts has them. Rates are vertical feet per moving hour, pace is minutes per mile.

/// Climbing and descending by grade, what running a climb bought over hiking it, and durability.
struct Terrain: Codable, Hashable {
    let bands: [GradeBand]
    let durability: Durability?
    let hikeOrRun: [HikeOrRun]
}

struct GradeSplit: Codable, Hashable {
    let timeS: Double
    let vertRateFtH: Double
    let paceMinMi: Double?
    let avgHr: Double?
}

struct GradeBand: Codable, Hashable, Identifiable {
    /// Against the athlete's own runs on the same grade over the last 90 days.
    struct Usual: Codable, Hashable {
        let runs: Int
        let vertRateFtH: Double
        let avgHr: Double?
        let rateChangePct: Double?
        let hrChangeBpm: Double?
        let fastest: Bool
    }

    let key: String
    let label: String
    let kind: String
    let timeS: Double
    let distanceMi: Double
    let vertFt: Double
    let vertRateFtH: Double
    let paceMinMi: Double?
    let hikeShare: Double
    let avgHr: Double?
    let hike: GradeSplit?
    let run: GradeSplit?
    let usual: Usual?
    var id: String { key }
}

struct DurabilityHalf: Codable, Hashable {
    let timeS: Double
    let distanceMi: Double
    let gainFt: Double
    let flatPaceMinMi: Double?
    let climbRateFtH: Double?
    let avgHr: Double?
    let efficiency: Double?
}

/// The second half against the first on grade-adjusted pace and heart rate.
struct Durability: Codable, Hashable {
    let first: DurabilityHalf
    let second: DurabilityHalf
    let flatPaceMinMi: Double?
    let paceChangePct: Double?
    let climbRateChangePct: Double?
    let decouplingPct: Double?
    /// "held", "drifted" or "faded".
    let verdict: String?
    let usualDecouplingPct: Double?
    let usualRuns: Int
}

struct HikeOrRun: Codable, Hashable, Identifiable {
    let key: String
    let label: String
    let hikeRateFtH: Double
    let runRateFtH: Double
    let runRateGainPct: Double
    let hikeHr: Double?
    let runHr: Double?
    let runHrCostBpm: Double?
    var id: String { key }
}

/// GET /api/heatmap: every scored activity with its route as an encoded polyline (nil without
/// GPS). Lines the engine hasn't drawn yet are read in the background; `pending` counts them.
struct HeatmapData: Codable, Hashable {
    let pending: Int
    let activities: [HeatActivity]
}

struct HeatActivity: Codable, Hashable, Identifiable {
    let id: Int
    let day: String
    let startTime: String
    let name: String?
    let sport: String?
    /// "trail", "run", "hike", "ride" or "other" (engine/talus/heatmap.py).
    let kind: String
    let distanceMi: Double
    let gainFt: Double
    let lossFt: Double
    let effortMi: Double
    let movingTimeS: Double
    let line: String?
    let pending: Bool
}

/// The reply to POST /api/activities/{id}/name/suggest. `source` is "coach" when the AI wrote it.
struct NameSuggestion: Codable, Hashable {
    let name: String
    let source: String
}

/// Google's encoded polyline at 5 decimals, as the engine's heatmap.encode writes it
/// (web/lib/polyline.ts).
enum Polyline {
    static func decode(_ line: String) -> [CLLocationCoordinate2D] {
        let bytes = Array(line.utf8)
        var out: [CLLocationCoordinate2D] = []
        var i = 0, lat = 0, lon = 0
        // One signed varint: 5 bits a character, offset by 63, low bit the sign.
        func next() -> Int? {
            var shift = 0, res = 0
            while i < bytes.count {
                let b = Int(bytes[i]) - 63
                i += 1
                res |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20 { return res & 1 != 0 ? ~(res >> 1) : res >> 1 }
            }
            return nil  // cut off mid-number
        }
        while i < bytes.count {
            guard let dLat = next(), let dLon = next() else { break }
            lat += dLat
            lon += dLon
            out.append(CLLocationCoordinate2D(latitude: Double(lat) / 1e5, longitude: Double(lon) / 1e5))
        }
        return out
    }
}
