import CoreLocation
import HealthKit
import Testing
@testable import Talus

struct DecodingTests {
    private func decode<T: Decodable>(_ json: String, as: T.Type) throws -> T {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return try d.decode(T.self, from: Data(json.utf8))
    }

    @Test func todayDecodesWithNullsAndExtraLedgers() throws {
        let json = """
        {"date":"2026-10-09","checkin":null,"wellness":{"date":"2026-10-09",
          "hrv":{"status":"ok","text":"7-day average 2% below","last_night":57.0},
          "resting_hr":{"status":"no_data","text":"No data"},"sleep":{"status":"short","text":"6 h","need_h":8.0}},
         "readiness":{"engine":{"state":"loaded","detail":"d","reason":"r"},
                      "legs":{"state":"fresh","detail":"d","limiter":"descent","reason":"r"}},
         "call":{"call":"modify","session":"Short and easy","detail":"30 min","why":"Sleep.","source":"rules"},
         "ledgers":{"cardio":{"day":"2026-10-09","load":0,"fitness":105.6,"fatigue":105.1,"form":0.5,"vs_normal":-0.005,
             "vs_normal_text":"1% below your normal","flags":[],"building":false,"spark":[{"day":"2026-10-08","fitness":1,"fatigue":2}]},
           "distance":{"day":"2026-10-09","load":3.1,"fitness":5,"fatigue":4,"form":1,"vs_normal":null,
             "vs_normal_text":"no history","flags":[{"key":"ramp","text":"Ramping fast"}],"spark":[]}},
         "week":{"start":"2026-10-05","activities":[],"distance_mi":12.3,"gain_ft":3400,"loss_ft":3350},
         "intensity_28d":{"minutes":{"z1":10,"z2":2,"z3":1},"share":{"z1":0.77,"z2":0.15,"z3":0.08}},
         "zones_28d":null,"zone_model":null,"thresholds":null}
        """
        let t = try decode(json, as: Today.self)
        #expect(t.checkin == nil)
        #expect(t.intensity28D.share["z1"] == 0.77)
        #expect(LedgerName.sorted(t.ledgers.keys) == ["cardio", "distance"])
        #expect(t.ledgers["distance"]?.building == nil)
        #expect(t.readiness.legs.limiter == "descent")
        #expect(t.score == nil)  // an engine from before the score
    }

    @Test func readinessScoreDecodes() throws {
        let json = """
        {"score":53,"label":"Ease off","call":"modify","raw":53,"held":null,
         "items":[{"key":"call","name":"Today's call: ease off","points":-34,"used":true,"text":"Descent load is 21% above"},
                  {"key":"legs","name":"Leg load","points":-10,"used":true,"text":"t","limiter":"descent"},
                  {"key":"checkin","name":"Check-in","points":0,"used":false,"text":"No check-in today"}],
         "used":["Leg load"],"missing":["Check-in"],"basis":"Based on leg load",
         "trend":[{"day":"2026-10-09","score":34,"label":"Ease off","call":"modify"}]}
        """
        let s = try decode(json, as: ReadinessScore.self)
        #expect(s.score == 53 && s.items.count == 3 && s.trend.first?.score == 34)
    }

    @Test func activityDetailDecodes() throws {
        let json = """
        {"id":7,"name":"Mt Baldy","day":"2026-10-04","start_time":"2026-10-04T07:00:00","source":"intervals",
         "device_name":"Forerunner 965","sport":"Run","distance_mi":11.2,"gain_ft":5800,"loss_ft":5790,"effort_mi":22.8,
         "moving_time_s":16000,"elapsed_time_s":17000,"hike_share":0.4,"longest_climb_ft":3900,"longest_descent_ft":3800,
         "minutes_above_8000ft":95,"notes":[],
         "loads":{"cardio":{"load":310,"times_usual_day":3.1,"six_week_high":true},
                  "climb":{"load":7000,"times_usual_day":null,"six_week_high":false}},
         "segments":[{"index":0,"kind":"climb","distance_mi":4.1,"gain_ft":3900,"loss_ft":40,"avg_grade":18.0,"time_s":6000}],
         "streams":{"time_s":[0,10],"distance_mi":[0,0.01],"altitude_ft":[6000,6010],"heart_rate":null,"zone":null,"lat":null,"lon":null}}
        """
        let a = try decode(json, as: ActivityDetail.self)
        #expect(a.minutesAbove8000Ft == 95)
        #expect(a.loads["cardio"]?.sixWeekHigh == true)
        #expect(a.streams.heartRate == nil)
    }

    @Test func badgeMatchesWeb() {
        #expect(Ledger.badge(vsNormal: 0.2, flags: [], building: false).0 == "Loaded")
        #expect(Ledger.badge(vsNormal: 0.0, flags: [], building: true).0 == "Building your normal")
        #expect(Ledger.badge(vsNormal: 0.0, flags: [Flag(key: "overreach", text: "Overreaching")], building: false).0 == "Overreaching")
        #expect(Ledger.badge(vsNormal: -0.2, flags: [], building: nil).0 == "Recovering")
        #expect(Ledger.badge(vsNormal: nil, flags: [], building: nil).0 == "No history")
    }
}

struct HealthPayloadTests {
    let t0 = Date(timeIntervalSince1970: 1_790_000_000)

    @Test func routePointsCarryDistanceAndHeartRate() {
        let locs = (0..<3).map { i in
            CLLocation(coordinate: CLLocationCoordinate2D(latitude: 34.0 + Double(i) * 0.001, longitude: -117.0),
                       altitude: 1000 + Double(i) * 10, horizontalAccuracy: 5, verticalAccuracy: 5,
                       timestamp: t0.addingTimeInterval(Double(i) * 10))
        }
        let hr = [(t0, 120.0), (t0.addingTimeInterval(15), 130.0)]
        let pts = HealthPayload.trackpoints(locations: locs, heartRate: hr, distance: [], start: t0, end: t0.addingTimeInterval(20))
        #expect(pts.count == 3)
        #expect(pts[0].distanceM == 0)
        #expect(abs((pts[2].distanceM ?? 0) - 222) < 2)
        #expect(pts.map(\.heartRate) == [120, 120, 130])
        #expect(pts[1].altitudeM == 1010)
    }

    @Test func indoorWorkoutUsesDistanceSamples() {
        let hr = [(t0.addingTimeInterval(10), 110.0), (t0.addingTimeInterval(20), 115.0)]
        let dist = [(t0.addingTimeInterval(10), 30.0), (t0.addingTimeInterval(20), 35.0)]
        let pts = HealthPayload.trackpoints(locations: [], heartRate: hr, distance: dist, start: t0, end: t0.addingTimeInterval(30))
        #expect(pts.map(\.distanceM) == [0, 30, 65, 65])
        #expect(pts.first?.lat == nil)
    }

    @Test func tcxHasTrackpointsTheEngineReads() {
        let pts = [HealthPayload.Point(time: t0, lat: 34.1, lon: -117.6, altitudeM: 2000, distanceM: 0, heartRate: 140)]
        let tcx = HealthPayload.tcx(points: pts, sport: .running, start: t0, duration: 3600)
        #expect(tcx.contains("<Activity Sport=\"Running\">"))
        #expect(tcx.contains("<LatitudeDegrees>34.1000000</LatitudeDegrees>"))
        #expect(tcx.contains("<AltitudeMeters>2000.0</AltitudeMeters>"))
        #expect(tcx.contains("<HeartRateBpm><Value>140</Value></HeartRateBpm>"))
        #expect(xmlParses(tcx))
    }

    @Test func payloadUsesSnakeCaseKeys() throws {
        var day = HealthPayload.WellnessDay(day: "2026-10-09")
        day.hrvSdnnMs = 48.2
        day.sleepS = 26400
        let w = HealthPayload.Workout(uuid: "U", sport: "run", name: nil, start: t0, end: t0, deviceName: "Apple Watch", tcx: "<x/>")
        let json = String(decoding: try HealthPayload.encode(workouts: [w], wellness: [day]), as: UTF8.self)
        #expect(json.contains("\"hrv_sdnn_ms\":48.2"))
        #expect(json.contains("\"sleep_s\":26400"))
        #expect(json.contains("\"device_name\":\"Apple Watch\""))
        #expect(!json.contains("resting_hr"))
    }

    @Test func eveningSleepCountsTowardNextDay() {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "America/Los_Angeles")!
        let morning = cal.date(from: DateComponents(year: 2026, month: 10, day: 9, hour: 6))!
        let evening = cal.date(from: DateComponents(year: 2026, month: 10, day: 8, hour: 23))!
        #expect(HealthPayload.dayString(HealthPayload.sleepDay(end: morning, calendar: cal), calendar: cal) == "2026-10-09")
        #expect(HealthPayload.dayString(HealthPayload.sleepDay(end: evening, calendar: cal), calendar: cal) == "2026-10-09")
    }

    @Test func sportsMap() {
        #expect(HealthPayload.sport(.hiking) == "hike")
        #expect(HealthPayload.sport(.running) == "run")
        #expect(HealthPayload.sport(.yoga) == nil)
    }
}

/// Well-formed XML check without Foundation's macOS-only XMLDocument.
private func xmlParses(_ s: String) -> Bool {
    final class Delegate: NSObject, XMLParserDelegate {}
    let parser = XMLParser(data: Data(s.utf8))
    let d = Delegate()
    parser.delegate = d
    return parser.parse()
}
