import Foundation
import Testing
@testable import Talus

/// Real engine answers for the Load and Today screens, saved from a demo engine (`talus demo` and
/// three scheduled runs: an easy run today, another in two days and a long day in four).
struct LoadDecodingTests {
    private final class Token {}

    private func fixture<T: Decodable>(_ name: String, as: T.Type) throws -> T {
        let url = try #require(Bundle(for: Token.self).url(forResource: name, withExtension: "json"))
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return try d.decode(T.self, from: Data(contentsOf: url))
    }

    @Test func loadPageDecodes() throws {
        let l = try fixture("load", as: LoadData.self)
        let weekly = try #require(l.weekly)
        #expect(weekly.count == 8)
        #expect(weekly.last?.days == 3)
        #expect(l.lastWeekToDate?.days == nil)
        #expect(l.zoneModel?.zones == 3)
        #expect(l.intensityByWeek.last?.zones != nil)
        let p = try #require(l.projection)
        #expect(p.until == "2026-10-18")
        #expect(p.series["distance"]?.count == 5)
        #expect(p.warnings.first?.ledgers == ["climb", "descent"])
        let next = try #require(l.nextWeek)
        #expect(next.scheduled.runs == 3)
        #expect(next.room["distance"]?.unit == "miles")
        #expect(next.room.count == 4)
    }

    @Test func coachLoadAndExplainDecode() throws {
        let r = try fixture("coach_load", as: Coached<LoadReply>.self)
        #expect(r.source == "rules")
        #expect(r.fallbackReason == "no_key")
        #expect(!r.reply.lastWeek.isEmpty && !r.reply.next.isEmpty)
        let e = try fixture("explain_ledger", as: Explanation.self)
        #expect(e.inputs.count == 3)
        #expect(e.steps.count == 1)
        #expect(e.note == nil)
    }

    @Test func todayDecodesScheduledRun() throws {
        let t = try fixture("today", as: Today.self)
        let p = try #require(t.planned)
        #expect(p.source == "manual")
        #expect(p.kind == "easy")
        #expect(p.miles == 6)
    }

    @Test func weekChange() {
        #expect(WeeklyStatsCard.change(12, 10) == "↑ 20%")
        #expect(WeeklyStatsCard.change(8, 10) == "↓ 20%")
        #expect(WeeklyStatsCard.change(10, 10) == "same")
        #expect(WeeklyStatsCard.change(5, 0) == "new")
        #expect(WeeklyStatsCard.change(0, 0) == nil)
    }

    @Test func nextWeekVerdicts() {
        typealias V = NextWeekCard.Verdict
        #expect(V(60, hold: 40, limit: 55) == .over)
        #expect(V(30, hold: 40, limit: 55) == .eases)
        #expect(V(42, hold: 40, limit: 55) == .holds)
        #expect(V(50, hold: 40, limit: 55) == .builds)
    }
}
