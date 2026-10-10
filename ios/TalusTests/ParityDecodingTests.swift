import Foundation
import Testing
@testable import Talus

/// Real engine answers for the Plan, Settings, Account and Admin screens, saved from a demo engine
/// (`talus demo`, an objective, an accepted plan and one scheduled run).
struct ParityDecodingTests {
    private final class Token {}

    private func fixture<T: Decodable>(_ name: String, as: T.Type) throws -> T {
        let url = try #require(Bundle(for: Token.self).url(forResource: name, withExtension: "json"))
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return try d.decode(T.self, from: Data(contentsOf: url))
    }

    @Test func planPageDecodes() throws {
        let p = try fixture("plan", as: PlanPage.self)
        let plan = try #require(p.accepted)
        #expect(p.objective?.name == "Fall 50k")
        #expect(p.draft == nil)
        #expect(!plan.weeks.isEmpty)
        #expect(p.sessions.count == 7)
        #expect(p.thisWeek?.cover != nil)
        #expect(p.readiness?.dims.isEmpty == false)
    }

    @Test func draftPlanDecodes() throws {
        let p = try fixture("plan_draft", as: PlanPage.self)
        #expect(p.accepted == nil)
        #expect(p.draft?.status == "draft")
    }

    @Test func scheduleDecodes() throws {
        let s = try fixture("schedule", as: Schedule.self)
        let own = try #require(s.items.first { $0.source == "manual" })
        #expect(own.id == 1)
        #expect(own.estimatedMinutes)
        #expect(!s.kinds.isEmpty)
    }

    @Test func coachRepliesDecode() throws {
        #expect(try fixture("coach_plan", as: Coached<PlanReply>.self).source == "rules")
        #expect(try fixture("coach_review", as: ReviewResponse.self).review.start.isEmpty == false)
        #expect(try fixture("coach_activity", as: Coached<AnalysisReply>.self).reply.job.isEmpty == false)
        #expect(try fixture("chat", as: ChatLog.self).messages.map(\.role) == ["athlete", "coach"])
    }

    @Test func settingsDecode() throws {
        let s = try fixture("settings", as: SettingsData.self)
        let th = try #require(s.thresholds)
        #expect(th.zones.options["5"]?.names.count == 5)
        #expect(!s.coach.models.isEmpty)
        #expect(s.coach.estimate.brief[s.coach.briefModel] != nil)
        #expect(s.sources.garmin.available)
    }

    @Test func accountAndAdminDecode() throws {
        #expect(try fixture("account", as: Account.self).hasPassword)
        let a = try fixture("admin", as: AdminOverview.self)
        #expect(a.accounts.count == 2)
        #expect(a.features.showAiCost)
        #expect(a.log.first?.detail?.feature == "show_ai_cost")
    }

    @Test func weekCoverStates() {
        func week(done: Double, ahead: Double) -> WeekProgress {
            WeekProgress(start: "2026-10-05", dated: true, target: WeekTarget(miles: 40, gainFt: 8000, lossFt: 8000, focus: "build"),
                         done: WeekActual(miles: done, gainFt: done * 200, lossFt: done * 200, belowAetPct: nil),
                         ahead: Vol(miles: ahead, gainFt: ahead * 200, lossFt: ahead * 200))
        }
        #expect(week(done: 20, ahead: 18).cover?.state == .covered)
        #expect(week(done: 20, ahead: 10).cover?.state == .close)
        #expect(week(done: 10, ahead: 10).cover?.state == .short)
        #expect(week(done: 40, ahead: 10).cover?.state == .over)
    }
}
