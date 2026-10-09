import CoreLocation
import Foundation
import HealthKit

/// Reads workouts (with route and heart rate), HRV, resting heart rate and sleep from Apple Health
/// and uploads them to the engine's Apple Health import, `POST /api/import/healthkit`. Health holds
/// Apple Watch data and anything Garmin Connect or other apps write to it, so this is the free
/// route to those devices. The engine scores each workout from its TCX like any imported file.
@MainActor
final class HealthSync: ObservableObject {
    static let path = "/api/import/healthkit"
    static let firstSyncDays = 90
    static let batchSize = 8

    @Published var enabled: Bool { didSet { UserDefaults.standard.set(enabled, forKey: "health.enabled") } }
    @Published private(set) var lastSync: Date? { didSet { UserDefaults.standard.set(lastSync, forKey: "health.lastSync") } }
    @Published private(set) var running = false
    @Published private(set) var status: String?

    private let store = HKHealthStore()

    init() {
        enabled = UserDefaults.standard.bool(forKey: "health.enabled")
        lastSync = UserDefaults.standard.object(forKey: "health.lastSync") as? Date
    }

    var available: Bool { HKHealthStore.isHealthDataAvailable() }

    private var readTypes: Set<HKObjectType> {
        [HKObjectType.workoutType(), HKSeriesType.workoutRoute(),
         HKQuantityType(.heartRate), HKQuantityType(.distanceWalkingRunning), HKQuantityType(.distanceCycling),
         HKQuantityType(.heartRateVariabilitySDNN), HKQuantityType(.restingHeartRate),
         HKCategoryType(.sleepAnalysis)]
    }

    func turnOn(_ api: API) async {
        guard available else {
            status = "Apple Health isn't available on this device."
            return
        }
        do {
            try await store.requestAuthorization(toShare: [], read: readTypes)
            enabled = true
            await sync(api)
        } catch {
            status = "Health access failed: \(error.localizedDescription)"
        }
    }

    func syncIfDue(_ api: API) async {
        guard enabled, !running else { return }
        if let lastSync, Date.now.timeIntervalSince(lastSync) < 3600 { return }
        await sync(api)
    }

    func sync(_ api: API) async {
        guard enabled, !running else { return }
        running = true
        defer { running = false }
        let now = Date.now
        // Re-read the last two days each time: a late watch sync can add a workout or finish a night.
        let since = lastSync.map { $0.addingTimeInterval(-2 * 86400) }
            ?? Calendar.current.date(byAdding: .day, value: -Self.firstSyncDays, to: now)!
        do {
            status = "Reading Apple Health…"
            let wellness = try await wellnessDays(since: since, until: now)
            let workouts = try await self.workouts(since: since, until: now)
            var added = 0, skipped = 0
            var batches = stride(from: 0, to: workouts.count, by: Self.batchSize).map {
                Array(workouts[$0..<min($0 + Self.batchSize, workouts.count)])
            }
            if batches.isEmpty { batches = [[]] }
            for (i, batch) in batches.enumerated() {
                status = "Uploading \(i + 1) of \(batches.count)…"
                let body = try await uploadBody(batch, wellness: i == 0 ? wellness : [])
                let data = try await api.postRaw(Self.path, json: body)
                let result = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
                added += result?["workouts_added"] as? Int ?? 0
                skipped += result?["workouts_skipped"] as? Int ?? 0
            }
            lastSync = now
            status = "Synced \(workouts.count) workouts (\(added) new) and \(wellness.count) days of HRV, resting HR and sleep."
        } catch {
            status = error.localizedDescription
        }
    }

    // MARK: Reading

    private func workouts(since: Date, until: Date) async throws -> [HKWorkout] {
        let predicate = HKQuery.predicateForSamples(withStart: since, end: until)
        let descriptor = HKSampleQueryDescriptor(predicates: [.workout(predicate)],
                                                 sortDescriptors: [SortDescriptor(\.startDate)])
        return try await descriptor.result(for: store)
            .filter { HealthPayload.sport($0.workoutActivityType) != nil }
    }

    private func uploadBody(_ workouts: [HKWorkout], wellness: [HealthPayload.WellnessDay]) async throws -> Data {
        var items: [HealthPayload.Workout] = []
        for w in workouts {
            let locations = try await route(for: w)
            let hr = try await samples(.heartRate, for: w, unit: HKUnit.count().unitDivided(by: .minute()))
            let distType: HKQuantityTypeIdentifier = w.workoutActivityType == .cycling ? .distanceCycling : .distanceWalkingRunning
            let dist = try await samples(distType, for: w, unit: .meter())
            let points = HealthPayload.trackpoints(locations: locations, heartRate: hr, distance: dist,
                                                   start: w.startDate, end: w.endDate)
            items.append(HealthPayload.Workout(
                uuid: w.uuid.uuidString, sport: HealthPayload.sport(w.workoutActivityType) ?? "other",
                name: nil, start: w.startDate, end: w.endDate,
                deviceName: w.device?.name ?? w.sourceRevision.source.name,
                tcx: HealthPayload.tcx(points: points, sport: w.workoutActivityType, start: w.startDate,
                                       duration: w.duration)))
        }
        return try HealthPayload.encode(workouts: items, wellness: wellness)
    }

    private func route(for workout: HKWorkout) async throws -> [CLLocation] {
        let routes = try await HKSampleQueryDescriptor(
            predicates: [.workoutRoute(HKQuery.predicateForObjects(from: workout))], sortDescriptors: []).result(for: store)
        var out: [CLLocation] = []
        for route in routes {
            let query = HKWorkoutRouteQueryDescriptor(route)
            for try await loc in query.results(for: store) { out.append(loc) }
        }
        return out.sorted { $0.timestamp < $1.timestamp }
    }

    private func samples(_ id: HKQuantityTypeIdentifier, for workout: HKWorkout, unit: HKUnit) async throws -> [(Date, Double)] {
        let predicate = HKQuery.predicateForSamples(withStart: workout.startDate, end: workout.endDate, options: [])
        let descriptor = HKSampleQueryDescriptor(predicates: [.quantitySample(type: HKQuantityType(id), predicate: predicate)],
                                                 sortDescriptors: [SortDescriptor(\.startDate)])
        return try await descriptor.result(for: store).map { ($0.endDate, $0.quantity.doubleValue(for: unit)) }
    }

    private func wellnessDays(since: Date, until: Date) async throws -> [HealthPayload.WellnessDay] {
        let cal = Calendar.current
        let start = cal.startOfDay(for: since)
        var days: [String: HealthPayload.WellnessDay] = [:]
        func day(_ d: Date) -> String { HealthPayload.dayString(d) }

        for (id, unit, keyPath) in [(HKQuantityTypeIdentifier.heartRateVariabilitySDNN, HKUnit.secondUnit(with: .milli), \HealthPayload.WellnessDay.hrvSdnnMs),
                                    (.restingHeartRate, HKUnit.count().unitDivided(by: .minute()), \HealthPayload.WellnessDay.restingHr)] {
            let query = HKStatisticsCollectionQueryDescriptor(
                predicate: .quantitySample(type: HKQuantityType(id), predicate: HKQuery.predicateForSamples(withStart: start, end: until)),
                options: .discreteAverage, anchorDate: start, intervalComponents: DateComponents(day: 1))
            let collection = try await query.result(for: store)
            collection.enumerateStatistics(from: start, to: until) { stats, _ in
                guard let v = stats.averageQuantity()?.doubleValue(for: unit) else { return }
                let key = day(stats.startDate)
                days[key, default: .init(day: key)][keyPath: keyPath] = (v * 10).rounded() / 10
            }
        }

        // Sleep counts toward the day you wake up. Only asleep stages, not time in bed.
        let asleep: Set<Int> = [HKCategoryValueSleepAnalysis.asleepUnspecified, .asleepCore, .asleepDeep, .asleepREM].map(\.rawValue).reduce(into: []) { $0.insert($1) }
        let sleepQuery = HKSampleQueryDescriptor(
            predicates: [.categorySample(type: HKCategoryType(.sleepAnalysis),
                                         predicate: HKQuery.predicateForSamples(withStart: start.addingTimeInterval(-86400), end: until))],
            sortDescriptors: [SortDescriptor(\.startDate)])
        var sleep: [String: Double] = [:]
        for s in try await sleepQuery.result(for: store) where asleep.contains(s.value) {
            sleep[day(HealthPayload.sleepDay(end: s.endDate)), default: 0] += s.endDate.timeIntervalSince(s.startDate)
        }
        for (key, seconds) in sleep where key >= day(start) {
            days[key, default: .init(day: key)].sleepS = seconds.rounded()
        }
        return days.values.sorted { $0.day < $1.day }
    }
}

/// The upload format, kept free of HealthKit queries so it can be unit tested.
enum HealthPayload {
    struct Workout: Encodable {
        let uuid: String
        let sport: String
        let name: String?
        let start: Date
        let end: Date
        let deviceName: String?
        let tcx: String
    }

    struct WellnessDay: Encodable, Equatable {
        let day: String
        var hrvSdnnMs: Double?
        var restingHr: Double?
        var sleepS: Double?

        init(day: String) { self.day = day }
    }

    struct Point: Equatable {
        let time: Date
        var lat: Double?
        var lon: Double?
        var altitudeM: Double?
        var distanceM: Double?
        var heartRate: Double?
    }

    static func sport(_ type: HKWorkoutActivityType) -> String? {
        switch type {
        case .running: "run"
        case .hiking: "hike"
        case .walking: "walk"
        case .cycling: "ride"
        case .crossCountrySkiing, .downhillSkiing, .snowSports: "other"
        case .climbing, .stairClimbing: "other"
        default: nil
        }
    }

    static func dayString(_ d: Date, calendar: Calendar = .current) -> String {
        let c = calendar.dateComponents([.year, .month, .day], from: d)
        return String(format: "%04d-%02d-%02d", c.year!, c.month!, c.day!)
    }

    /// A stretch of sleep ending before 6 pm counts toward that day; a nap or an early night that ends
    /// later counts toward the next morning.
    static func sleepDay(end: Date, calendar: Calendar = .current) -> Date {
        calendar.component(.hour, from: end) >= 18 ? calendar.date(byAdding: .day, value: 1, to: end)! : end
    }

    /// One trackpoint per GPS fix, with the latest heart rate at or before it and distance along the
    /// route. Indoor workouts have no route, so they get a point per heart-rate sample and distance
    /// from Health's distance samples.
    static func trackpoints(locations: [CLLocation], heartRate: [(Date, Double)], distance: [(Date, Double)],
                            start: Date, end: Date) -> [Point] {
        var points: [Point] = []
        if !locations.isEmpty {
            var total = 0.0
            var prev: CLLocation?
            for loc in locations {
                if let prev { total += loc.distance(from: prev) }
                prev = loc
                points.append(Point(time: loc.timestamp, lat: loc.coordinate.latitude, lon: loc.coordinate.longitude,
                                    altitudeM: loc.verticalAccuracy >= 0 ? loc.altitude : nil, distanceM: total))
            }
        } else {
            let times = (heartRate.map(\.0) + distance.map(\.0) + [start, end]).filter { $0 >= start && $0 <= end }
            var total = 0.0, di = 0
            let sortedDist = distance.sorted { $0.0 < $1.0 }
            for t in Set(times).sorted() {
                while di < sortedDist.count && sortedDist[di].0 <= t { total += sortedDist[di].1; di += 1 }
                points.append(Point(time: t, distanceM: total))
            }
        }
        let hr = heartRate.sorted { $0.0 < $1.0 }
        var hi = 0
        for i in points.indices {
            while hi + 1 < hr.count && hr[hi + 1].0 <= points[i].time { hi += 1 }
            if hi < hr.count && hr[hi].0 <= points[i].time.addingTimeInterval(5) {
                points[i].heartRate = hr[hi].1
            }
        }
        return points
    }

    private static let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    static func tcx(points: [Point], sport type: HKWorkoutActivityType, start: Date, duration: TimeInterval) -> String {
        let sport = type == .cycling ? "Biking" : type == .running ? "Running" : "Other"
        let startText = iso.string(from: start)
        var out = """
        <?xml version="1.0" encoding="UTF-8"?>
        <TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
        <Activities><Activity Sport="\(sport)"><Id>\(startText)</Id>
        <Lap StartTime="\(startText)"><TotalTimeSeconds>\(Int(duration))</TotalTimeSeconds>
        <DistanceMeters>\(String(format: "%.1f", points.last?.distanceM ?? 0))</DistanceMeters><Calories>0</Calories>
        <Intensity>Active</Intensity><TriggerMethod>Manual</TriggerMethod><Track>

        """
        for p in points {
            out += "<Trackpoint><Time>\(iso.string(from: p.time))</Time>"
            if let lat = p.lat, let lon = p.lon {
                out += "<Position><LatitudeDegrees>\(String(format: "%.7f", lat))</LatitudeDegrees><LongitudeDegrees>\(String(format: "%.7f", lon))</LongitudeDegrees></Position>"
            }
            if let a = p.altitudeM { out += "<AltitudeMeters>\(String(format: "%.1f", a))</AltitudeMeters>" }
            if let d = p.distanceM { out += "<DistanceMeters>\(String(format: "%.1f", d))</DistanceMeters>" }
            if let h = p.heartRate { out += "<HeartRateBpm><Value>\(Int(h.rounded()))</Value></HeartRateBpm>" }
            out += "</Trackpoint>\n"
        }
        out += "</Track></Lap></Activity></Activities></TrainingCenterDatabase>\n"
        return out
    }

    static func encode(workouts: [Workout], wellness: [WellnessDay]) throws -> Data {
        struct Body: Encodable { let workouts: [Workout]; let wellness: [WellnessDay] }
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        // With the local offset: the engine stores start times as wall-clock time, like a watch file.
        let local = ISO8601DateFormatter()
        local.timeZone = .current
        e.dateEncodingStrategy = .custom { date, encoder in
            var c = encoder.singleValueContainer()
            try c.encode(local.string(from: date))
        }
        e.outputFormatting = [.sortedKeys]
        return try e.encode(Body(workouts: workouts, wellness: wellness))
    }
}
