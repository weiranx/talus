import Foundation
import Testing
@testable import Talus

/// Plan answers from a demo engine (engine/tests helpers): the plan chat at its first question
/// and at the draft, and an accepted plan with fartlek speed work and back-to-back long days.
struct PlanParityTests {
    private final class Token {}

    private func data(_ name: String) throws -> Data {
        let url = try #require(Bundle(for: Token.self).url(forResource: name, withExtension: "json"))
        return try Data(contentsOf: url)
    }

    private func decode<T: Decodable>(_ data: Data, as: T.Type) throws -> T {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return try d.decode(T.self, from: data)
    }

    @Test func talkDecodes() throws {
        let t = try decode(data("plan_talk_setup"), as: Talk.self)
        #expect(t.step == "objective" && t.mode == "setup" && !t.readsWithAi && t.building == false)
        let g = try #require(t.lastCoach?.groups?.first)
        #expect(g.key == "goal_kind" && g.label == "No race day" && g.value == nil)
        #expect(t.startPicks.isEmpty)
    }

    @Test func talkDraftHasActionsAndPicks() throws {
        let t = try decode(data("plan_talk_draft"), as: Talk.self)
        #expect(t.step == "draft")
        #expect(t.lastCoach?.actions?.map(\.id) == ["accept"])
        let groups = t.messages.flatMap { m in m.groups ?? [] }
        let days = try #require(groups.first { $0.key == "days" })
        #expect(days.multi == true)
        #expect(days.value == .many(["Tue", "Wed", "Thu", "Sat", "Sun"]))
        #expect(groups.contains { $0.value == .one("1 h 30") })
    }

    @Test func talkValueRoundTrips() throws {
        let picks: [String: TalkValue] = ["days": .many(["Mon", "Sat"]), "long_day": .one("Sat")]
        let back = try JSONDecoder().decode([String: TalkValue].self, from: JSONEncoder().encode(picks))
        #expect(back == picks)
        #expect(picks["days"]?.has("Sat") == true && picks["long_day"]?.has("Mon") == false)
    }

    @Test func planWithWorkoutsDecodes() throws {
        let p = try decode(data("plan_workouts"), as: PlanPage.self)
        #expect(p.constraints.workout == "fartlek" && p.constraints.backToBack == true)
        let plan = try #require(p.accepted)
        #expect(plan.raceType == "mountain" && plan.builder == "engine" && plan.building == false)
        #expect(plan.weeks.contains { $0.workout == "fartlek" })
        let hill = try #require(plan.weeks.first { $0.hillSession != nil })
        #expect(PlanText.weekFocus(hill).contains((PlanText.hillText[hill.hillSession!] ?? "").lowercased()))
        let steps = try #require(p.sessions.first { ($0.steps?.count ?? 0) > 1 && $0.kind != "rest" })
        #expect(steps.type != nil)
    }

    @Test func coachBuiltPlanDecodes() throws {
        var json = try #require(JSONSerialization.jsonObject(with: data("plan_workouts")) as? [String: Any])
        var accepted = try #require(json["accepted"] as? [String: Any])
        accepted["builder"] = "coach"
        accepted["building"] = true
        accepted["approach"] = "Two long days on weekends."
        accepted["held_weeks"] = 2
        accepted["back_to_back"] = true
        accepted["race_type"] = "hundred"
        json["accepted"] = accepted
        let plan = try #require(try decode(JSONSerialization.data(withJSONObject: json), as: PlanPage.self).accepted)
        #expect(plan.heldWeeks == 2 && plan.approach == "Two long days on weekends." && plan.building == true)
        let road = PlanText.roadText(plan, event: true)
        #expect(road.contains("back-to-back long days") && road.contains("A 100-mile plan") && road.contains("The AI coach built"))
    }

    @Test func constraintsSaveKeepsTheNewFields() throws {
        let c = Constraints(days: [1, 3, 5, 6], weekdayMin: 60, weekendMin: 240, longDay: 5, notes: "", backToBack: false, workout: "tempo")
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        let out = try #require(JSONSerialization.jsonObject(with: e.encode(c)) as? [String: Any])
        #expect(out["back_to_back"] as? Bool == false && out["workout"] as? String == "tempo")
        let old = Constraints(days: [5], weekdayMin: 60, weekendMin: 240, longDay: 5, notes: "", backToBack: nil, workout: nil)
        let left = try #require(JSONSerialization.jsonObject(with: e.encode(old)) as? [String: Any])
        #expect(left["back_to_back"] == nil && left["workout"] == nil)  // left out: the engine keeps its setting
    }

    @Test func scheduleShowsUnmatchedActivities() throws {
        let s = try decode(data("schedule_workouts"), as: Schedule.self)
        #expect(s.activities?.isEmpty == false)
        #expect(s.items.contains { $0.detail?.isEmpty == false })
    }

    @Test func goalTexts() {
        #expect(PlanText.goals.count == 8 && PlanText.goalText("comeback") == "Come back from a break")
        #expect(PlanText.isEvent(nil) && PlanText.isEvent("trip") && !PlanText.isEvent("rolling"))
        #expect(PlanText.workoutText("race_pace") == "Race pace")
    }
}
