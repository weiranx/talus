# Talus for iPhone

A native SwiftUI app (iOS 17+) over the Talus engine API: Today, Plan, Load, Activity and Settings, plus Apple Health sync. It does what the web app does, except Google sign-in and the intervals.icu OAuth button, which stay on the web. Every number comes from the engine; the app only formats it, in miles and feet, distance and vert together.

| Screen | What it shows |
|---|---|
| Today | The call for the day (the AI coach's brief when it's on, rule-based otherwise), the morning check-in, engine and legs readiness, overnight HRV, resting HR and sleep, each ledger against your normal, this week's miles and vert, and 28-day intensity |
| Plan | The objective and its plan: the next run, this week against its target, the calendar of scheduled runs (add, move, edit, delete), the road to the objective week by week, readiness for the day, the coach's read and weekly review, and chat. Until a plan is accepted, a three-step setup: objective (type it or pick a GPX), training days, review and accept |
| Load | Cardio, distance, climb and descent ledgers over 6 weeks, 6 months or a year: daily load, your normal and your last 7 days; intensity by week; biggest days |
| Activity | Every activity by week, with each week's miles, ft up and ft down; the detail has the route map, the elevation profile, the coach's read, the loads against a usual day, and climb and descent segments |
| Settings | Data sources (intervals.icu key, Garmin Connect, file upload, Apple Health), heart rate thresholds with the estimate from your runs, zones, the AI coach, sleep need, appearance, account (password, export, delete), the engine address, and Admin for the admin account |

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

Settings > Connect Apple Health asks to read workouts and their routes, heart rate, walking, running and cycling distance, HRV, resting heart rate and sleep. Health holds Apple Watch data and anything Garmin Connect or other apps write to it, so this is the free route to those watches.

The first sync reads the last 90 days. After that the app syncs when it opens (at most hourly) and on **Sync now**, re-reading the last two days each time so a late watch sync is picked up. Each run posts to `POST /api/import/healthkit`:

- Runs, hikes, walks and rides become a TCX with one trackpoint per GPS fix (position, altitude, distance along the route, and the latest heart rate). Indoor workouts get a point per heart-rate sample with distance from Health's distance samples. The engine scores them like any watch file and skips ones it already has from another source.
- Each day's average HRV (SDNN), resting heart rate, and time asleep (core, deep, REM and unspecified, not time in bed) count toward the morning you wake up. The engine only fills days no other source covers, and doesn't use SDNN in readiness yet.

## Layout

| Path | What it is |
|---|---|
| `Talus/API.swift` | Engine client; the sign-in cookie lives in the shared cookie store |
| `Talus/Models.swift` | The engine's JSON shapes |
| `Talus/HealthSync.swift` | HealthKit reads and the upload format (`HealthPayload`, unit tested) |
| `Talus/Format.swift` | Number formatting, ledger colors and badges, shared card views |
| `TalusTests/` | Decoding, badge and Health payload tests |
| `screenshots/` | The app on demo data |

The project uses folder-synced groups, so new Swift files in `Talus/` are picked up without editing the project file.
