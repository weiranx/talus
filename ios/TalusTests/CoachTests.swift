import Foundation
import Testing
@testable import Talus

/// The coach chat's action cards and the coach text renderer, against the web's behavior.
struct CoachTests {
    @Test func chatMessagesCarryActionCards() throws {
        let json = """
        {"messages":[{"id":3,"created_at":"2026-10-12T23:47:44","role":"coach","text":"Here's a hill day.",
        "source":"coach","log_id":9,"cost":null,"actions":[{"id":4,"kind":"schedule_run","summary":"Add hills on Thu, Oct 15: 6 mi, 1,200 ft up",
        "status":"proposed","result":null}]},{"id":4,"created_at":"","role":"athlete","text":"Thanks","source":null,"log_id":null}]}
        """
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        let log = try d.decode(ChatLog.self, from: Data(json.utf8))
        #expect(log.messages[0].actions?.first?.kind == "schedule_run")
        #expect(log.messages[0].actions?.first?.status == "proposed")
        #expect(log.messages[1].actions == nil)
    }

    @Test func actionLabelsMatchTheWeb() {
        #expect(ActionCard.label("done") == "Done")
        #expect(ActionCard.label("dismissed") == "Skipped")
        #expect(ActionCard.label("failed") == "Didn't work")
    }

    @Test func numberedListsCountOnAndIndentedLinesContinue() {
        let b = CoachText.blocks("1. Easy\n1. Long\n  with hikes\n\n4. Rest")
        #expect(b == [.numbered("1.", "Easy"), .numbered("2.", "Long with hikes"), .numbered("4.", "Rest")])
        #expect(CoachText.blocks("- a\n- b\nAfter") == [.bullet("a"), .bullet("b"), .paragraph("After")])
    }

    @Test func onlyBoldIsMarkdown() {
        let s = CoachText.inline("Keep **6 mi** easy, *not* a_tempo")
        #expect(String(s.characters) == "Keep 6 mi easy, *not* a_tempo")
        let bold = s.runs.filter { $0.inlinePresentationIntent == .stronglyEmphasized }.map { String(s[$0.range].characters) }
        #expect(bold == ["6 mi"])
    }
}
