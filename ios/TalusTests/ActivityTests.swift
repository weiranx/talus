import CoreLocation
import Foundation
import Testing
@testable import Talus

/// The heatmap's polyline decoder, and real engine answers for the heatmap and an activity with
/// the full terrain analysis, saved from a demo engine (`talus demo`, streams cut to six points).
struct ActivityTests {
    private final class Token {}

    private func fixture<T: Decodable>(_ name: String, as: T.Type) throws -> T {
        let url = try #require(Bundle(for: Token.self).url(forResource: name, withExtension: "json"))
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return try d.decode(T.self, from: Data(contentsOf: url))
    }

    private func close(_ c: CLLocationCoordinate2D, _ lat: Double, _ lon: Double) -> Bool {
        abs(c.latitude - lat) < 1e-9 && abs(c.longitude - lon) < 1e-9
    }

    /// Google's own example in the polyline format documentation.
    @Test func decodesGoogleExample() {
        let c = Polyline.decode("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
        #expect(c.count == 3)
        #expect(close(c[0], 38.5, -120.2))
        #expect(close(c[1], 40.7, -120.95))
        #expect(close(c[2], 43.252, -126.453))
    }

    @Test func decodesEmptyAndCutOffLines() {
        #expect(Polyline.decode("").isEmpty)
        // A pair cut off mid-number is dropped; the whole pair before it stays.
        let c = Polyline.decode("_p~iF~ps|U_ulL")
        #expect(c.count == 1)
        #expect(close(c[0], 38.5, -120.2))
    }

    @Test func decodesSouthAndEastAndZero() {
        // (0, 0), then (-33.86882, 151.20929): Sydney, both signs.
        let c = Polyline.decode("??" + encode(-3386882) + encode(15120929))
        #expect(c.count == 2)
        #expect(close(c[0], 0, 0))
        #expect(close(c[1], -33.86882, 151.20929))
    }

    @Test func decodesEngineLine() throws {
        let h = try fixture("heatmap", as: HeatmapData.self)
        let line = try #require(h.activities.first?.line)
        let c = Polyline.decode(line)
        #expect(c.count > 1)
        // The demo routes are in the San Gabriels.
        #expect(c.allSatisfy { (33.5...35).contains($0.latitude) && (-119...(-117)).contains($0.longitude) })
    }

    @Test func heatmapDecodes() throws {
        let h = try fixture("heatmap", as: HeatmapData.self)
        #expect(h.pending == 1)
        #expect(h.activities.count == 4)
        #expect(h.activities.last?.line == nil)
        #expect(h.activities.last?.pending == true)
        #expect(Set(h.activities.map(\.kind)).isSubset(of: Set(Heat.kinds.map(\.key))))
    }

    @Test func terrainDecodes() throws {
        let a = try fixture("activity_detail", as: ActivityDetail.self)
        #expect(a.nameBy == "source")
        #expect(a.sourceName == a.name)
        #expect(a.gainByBandFt?["8-20"] != nil)
        let t = try #require(a.terrain)
        #expect(t.bands.contains { $0.kind == "climb" } && t.bands.contains { $0.kind == "descent" })
        #expect(t.bands.first?.usual?.runs ?? 0 > 0)
        let d = try #require(t.durability)
        #expect(d.verdict == "held")
        #expect(d.first.climbRateFtH != nil)
        #expect(t.hikeOrRun.first?.runRateGainPct == 50)
        // Flat segments have no vert rate; climbs and descents do.
        #expect(a.segments.first?.vertRateFtH == nil)
        let s = try #require(a.segments.first { $0.kind == "climb" })
        #expect(s.vertRateFtH == 1349)
        #expect(s.hikeShare != nil)
    }

    @Test func calendarShadesByQuartile() {
        let cuts = HeatCalendar.cuts([0, 1, 2, 3, 4, 5, 6, 7, 8])
        #expect(cuts == [2, 4, 6])
        #expect(HeatCalendar.level(0, cuts) == 0)
        #expect(HeatCalendar.level(2, cuts) == 1)
        #expect(HeatCalendar.level(5, cuts) == 3)
        #expect(HeatCalendar.level(9, cuts) == 4)
        #expect(HeatCalendar.cuts([]) == [0, 0, 0])
    }

    @Test func terrainWordingMatchesWeb() {
        #expect(Fmt.pace(12.0833) == "12:05")
        #expect(Fmt.pace(nil) == "–")
        #expect(Fmt.signed(-3) == "−3")
        #expect(Fmt.signedPlain(-36.7) == "−36.7")
        #expect(Fmt.signedPlain(5) == "+5")
    }

    /// One value in the polyline format, for the test above.
    private func encode(_ v: Int) -> String {
        var n = v < 0 ? ~(v << 1) : v << 1
        var out = ""
        while n >= 0x20 {
            out.append(Character(UnicodeScalar(UInt8((0x20 | (n & 0x1F)) + 63))))
            n >>= 5
        }
        out.append(Character(UnicodeScalar(UInt8(n + 63))))
        return out
    }
}
