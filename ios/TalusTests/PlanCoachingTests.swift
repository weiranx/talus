import Foundation
import Testing
@testable import Talus

struct PlanCoachingTests {
    private final class Token {}
    private func decode<T: Decodable>(_ name: String, as: T.Type) throws -> T {
        let url = try #require(Bundle(for: Token.self).url(forResource: name, withExtension: "json"))
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(T.self, from: Data(contentsOf: url))
    }

    @Test func coachingDecodesDatedSessionsAndReportedIntent() throws {
        let p = try decode("plan_coaching", as: CoachingPlan.self)
        #expect(!p.sessions.isEmpty)
        #expect(p.reasons.map(\.key).contains("sore"))
        #expect(p.evidence.lastActivity != nil && !p.evidence.assumptions.isEmpty)
        let outcome = try #require(p.outcomes.first)
        #expect(outcome.intent == "partial" && outcome.effort == 8)
        #expect(outcome.planned.source == "planned_reference")
        #expect(outcome.actual.miles > 0)
    }

    @Test func revisionDecodesBeforeAfterAndExactAvailability() throws {
        let p = try decode("plan_revision", as: RevisionPreview.self)
        #expect(!p.changes.isEmpty && !p.weeks.isEmpty)
        #expect(p.availability.values.contains { $0.minutes == 30 && $0.terrain == "flat" && !$0.locked })
        #expect(p.weeks.allSatisfy { $0.after.miles <= $0.before.miles + 0.01 })
        #expect(p.changes.allSatisfy { !$0.why.isEmpty && $0.before.day == $0.after.day })
    }
}
