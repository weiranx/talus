# Talus for iPhone

A native SwiftUI app (iOS 17+) over the Talus engine API: Today, Plan, Coach, Load and Activity tabs, Settings behind the gear on Today, plus Apple Health sync. It does what the web app does, except Google sign-in and the intervals.icu OAuth button, which stay on the web. Every number comes from the engine; the app only formats it, in miles and feet, distance and vert together.

| Screen | What it shows |
|---|---|
| Today | The call for the day (the AI coach's brief when it's on, rule-based otherwise), the morning check-in, engine and legs readiness, overnight HRV, resting HR and sleep, each ledger against your normal (tap a number to see how it's worked out), this week's miles and vert, and 28-day intensity |
| Plan | The objective and its plan: the next run, this week against its target, the calendar of scheduled runs (add, move, edit, delete), the road to the objective week by week, readiness for the day, the coach's read and weekly review, and chat; change it with the coach in conversation. Until a plan is accepted, planning with the coach (tap answers, type the objective or upload its GPX, then accept the draft), or the three-step form: objective or a goal with no race day, training days with speed work and back-to-back long days, review and accept |
| Coach | Your training goals (typed, or drafted by the AI coach from three questions and your recent weeks) and the chat, shared with Plan, with starter questions. The coach can offer to add, move or delete a run, update your goals, sync or apply thresholds; each shows as a card and nothing runs until Confirm |
| Load | The coach's take on last week, the trend, what to watch and the next 7 days; how much the next 7 days can hold beside what's scheduled; weekly stats for the last 8 weeks; cardio, distance, climb and descent ledgers over 6 weeks, 6 months or a year: your normal, your last 7 days, the room before ramping too fast and where scheduled runs take them; intensity by week; biggest days |
| Activity | Every activity by week, with each week's miles, ft up and ft down; Routes, every route on one map, and Calendar, a heatmap of days, filtered by range and sport; the detail has the route map, the elevation profile, the coach's read, the loads against a usual day, climb and descent segments, climbing and descending by grade against your usual, hike or run, durability, and Rename with an AI suggestion |
| Settings | Data sources (intervals.icu key, Garmin Connect, COROS and the Strava website fallback for the owner, file upload, Apple Health), heart rate thresholds with the estimate from your runs, zones, the AI coach (style, cap, AI-built plans, activity names), sleep need, appearance, account (password, export, delete), the engine address, Status (engine, each source's last sync, read-ahead, coach, and the server for the admin), and Admin for the admin account (including the coach models for every account) |

## Run it in the Simulator

Needs Xcode 16 or later. Start the engine on your Mac, then open the project and run the `Talus` scheme:

```bash
cd engine && uv run talus serve
open ios/Talus.xcodeproj
```

The Simulator reaches the engine at `http://localhost:8000`, the default. Sign in with your Talus email and password (Google sign-in is web-only for now).

From the command line:

```bash
cd ios
xcodebuild -scheme Talus -destination 'platform=iOS Simulator,name=iPhone 17 Pro' test
```

## On an iPhone

Running on a real phone needs your Apple Developer team set under Signing & Capabilities (HealthKit is already in `Support/Talus.entitlements`). The phone must reach the engine: start it with `uv run talus serve --host 0.0.0.0` and set the server in the app to your Mac's address on the same Wi-Fi, such as `http://192.168.1.20:8000`.

## Apple Health

Settings > Data sources > Connect Apple Health asks to read workouts and their routes, heart rate, walking, running and cycling distance, HRV, resting heart rate and sleep. Health holds Apple Watch data and anything Garmin Connect or other apps write to it, so this is the free route to those watches.

For a manual sync, open **Settings > Data sources** and tap **Sync now**. Talus checks online sources, then the Garmin watch and Apple Health when enabled. The card shows the current step, the result and any new activities; **Sync details** holds the full results. Keep Talus open and your watch nearby until it finishes. A source that fails doesn't stop the others; **Try sync again** safely skips activities already imported. **Import older activities** changes the online history range only.

The first sync reads the last 90 days. After that the app syncs when it opens (at most hourly) and on **Sync now** under Settings > Data sources, which also syncs the engine's sources and the Garmin watch when it's switched on, re-reading the last two days each time so a late watch sync is picked up. Each run posts to `POST /api/import/healthkit`:

- Runs, hikes, walks and rides become a TCX with one trackpoint per GPS fix (position, altitude, distance along the route, and the latest heart rate). Indoor workouts get a point per heart-rate sample with distance from Health's distance samples. The engine scores them like any watch file and skips ones it already has from another source.
- Each day's average HRV (SDNN), resting heart rate, and time asleep (core, deep, REM and unspecified, not time in bed) count toward the morning you wake up. The engine only fills days no other source covers, and doesn't use SDNN in readiness yet.

## Layout

| Path | What it is |
|---|---|
| `Talus/API.swift` | Engine client; the sign-in cookie lives in the shared cookie store |
| `Talus/Models.swift` | The engine's JSON shapes |
| `Talus/HealthSync.swift` | HealthKit reads and the upload format (`HealthPayload`, unit tested) |
| `Talus/Format.swift` | Number formatting, ledger colors and badges, shared card views |
| `TalusTests/` | Decoding, badge, Health payload and polyline tests |
| `screenshots/` | The app on demo data |

The project uses folder-synced groups, so new Swift files in `Talus/` are picked up without editing the project file.
