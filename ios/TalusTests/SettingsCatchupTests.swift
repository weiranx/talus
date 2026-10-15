import Foundation
import Testing
@testable import Talus

/// Engine answers for the owner's Settings (COROS, Strava, AI-built plans), the admin's coach
/// models, AI names and the Status page, saved from the engine's test client.
struct SettingsCatchupTests {
    private final class Token {}

    private func fixture<T: Decodable>(_ name: String, as: T.Type) throws -> T {
        let url = try #require(Bundle(for: Token.self).url(forResource: name, withExtension: "json"))
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return try d.decode(T.self, from: Data(contentsOf: url))
    }

    @Test func ownerSettingsHaveCorosAndStrava() throws {
        let s = try fixture("settings_owner", as: SettingsData.self)
        let coros = try #require(s.sources.coros)
        #expect(coros.available && !coros.connected)
        #expect(coros.lastSync?.listed == 2)
        let strava = try #require(s.sources.stravaWeb)
        #expect(strava.available && !strava.envOverride)
        #expect(strava.lastSync?.aliased == 3)
        #expect(strava.lastSync.map(StravaCard.runLine)?.hasSuffix("4 on Strava since Sep 14, 1 new, 3 already here.") == true)
        #expect(s.coach.aiPlans == true)
        #expect(s.coach.estimate.build?[s.coach.analysisModel] != nil)
    }

    @Test func olderSettingsStillDecode() throws {
        let s = try fixture("settings", as: SettingsData.self)
        #expect(s.sources.coros == nil && s.sources.stravaWeb == nil)
        #expect(s.coach.aiPlans == nil && s.coach.estimate.build == nil)
        #expect(try fixture("admin", as: AdminOverview.self).coachModels == nil)
    }

    @Test func stravaRunLineReadsStops() {
        let base = LastSync(at: "2026-10-14T08:00:00", source: "strava_web", since: "2026-09-14", listed: 0, new: 0, saved: 0,
                            duplicates: nil, error: "StravaWebError: Strava wants a fresh session. Run `talus strava-web setup` again.")
        #expect(StravaCard.runLine(base).hasSuffix(": Strava wants a fresh session."))
        var expired = base
        expired.errorCode = "session_expired"
        #expect(StravaCard.runLine(expired).hasSuffix("Strava ended the session."))
    }

    @Test func adminCoachModels() throws {
        let a = try fixture("admin_models", as: AdminOverview.self)
        let m = try #require(a.coachModels)
        #expect(!m.chosen && m.env == nil)
        #expect(m.models.map(\.id).contains(m.briefModel))
        #expect(CoachModelsCard.costLine(m.estimate.brief[m.briefModel]) == "Under 1¢ a call, 3¢ a month on your account")
    }

    @Test func namesBackfill() throws {
        let n = try fixture("names_backfill", as: NamesStatus.self)
        #expect(n.enabled && !n.running && n.pending == 0)
        #expect(n.cost == "under a cent")
    }

    @Test func statusDecodes() throws {
        let s = try fixture("status", as: StatusOverview.self)
        #expect(s.engine.ok)
        #expect(s.sources.map(\.key) == ["intervals", "coros", "upload"])
        #expect(s.sources.first?.how == nil)
        #expect(s.readAhead.waitingFor == "thresholds")
        #expect(s.coach.errors24h == 1 && s.coach.lastCall?.ok == false)
        let server = try #require(s.server)
        #expect(server.active7d == 1 && server.failingSyncs.count == 1)
        // no_key is bad, the never-synced COROS row and the failing sync are each a warn.
        #expect(StatusText.summary(s.states).text == "1 not working")
        #expect(!s.live)
    }

    @Test func statusWords() {
        let now = StatusText.date("2026-10-14T12:00:00")!
        #expect(StatusText.ago("2026-10-14T11:59:30", now: now) == "Just now")
        #expect(StatusText.ago("2026-10-14T11:15:00", now: now) == "45 min ago")
        #expect(StatusText.ago("2026-10-11T12:00:00", now: now) == "3 days ago")
        #expect(StatusText.ago(nil) == "Never")
        #expect(StatusText.uptime(7200) == "2 h")
        #expect(StatusText.short("27e2bec5fe750373") == "27e2bec")
        #expect(StatusText.summary(["ok", "warn", "idle"]).text == "1 to check")
        let r = StatusOverview.Source(key: "intervals", label: "intervals.icu", state: "warn", connected: true, at: nil, error: nil, saved: nil, how: "API key")
        #expect(StatusText.sourceWord(r) == "Never synced")
    }
}
