# Strava website: what the owner's signed-in session uses

Captured 2026-10-14 from the owner's signed-in Chrome (Claude in Chrome, network requests and
same-origin fetches). No HAR is kept: a raw HAR carries the session cookies. Values below are the
response's shape; the test fixture (`engine/tests/fixtures/strava_web/training_activities.json`)
has made-up values in that shape.

## Sign-in: changed, so Talus borrows a session instead

`GET /login` is now a Next.js page (`/assets/landing-pages/_next/...`) that asks for the email
first. It has no `authenticity_token` input, no `csrf-token` meta and no form posting to
`/session`, so the old GET /login → POST /session flow no longer works. Mapping the new flow
means recording a sign-in, which puts the password in the capture, and a server signing in is
what gets accounts flagged. So Talus doesn't sign in: `talus strava-web setup` takes the Cookie
header from a signed-in browser. A request without a live session is redirected to /login.
Signed-in pages still carry `<meta name="csrf-token">`.

## Activity list

    GET /athlete/training_activities?keywords=&sport_type=&tags=&commute=&private_activities=
        &trainer=&gear=&new_activity_only=false&page=N&per_page=20
    X-Requested-With: XMLHttpRequest
    Accept: text/javascript, application/javascript

- The `My Activities` page (`/athlete/training`) renders page 1 from data inlined in the HTML,
  then fetches later pages with this request. The page adds `search_session_id`; it is not
  required (a request without it returns the same data).
- `per_page` is capped at 20: asking for 100 still returns 20 (`perPage: 20`).
- Newest first. Page until a start is older than the sync's `since`.
- Response: `{"models": [...], "page": 1, "perPage": 20, "total": 1358}`.
- Without a live session it answers 500, not a redirect to /login (seen 2026-10-14 with a
  made-up cookie). `/dashboard` does redirect, so setup checks that first.
- Per model, the fields a sync needs:
  - `id` (int) and `id_str`
  - `start_time`: UTC, `"2026-10-14T00:05:16+0000"`
  - `start_date_local_raw`: the local wall-clock start as epoch seconds read as UTC
    (1791911116 → 2026-10-13 17:05:16, Pacific time for the run above). This is the local-naive
    start the store matches on, like intervals.icu's `start_date_local`.
  - `distance_raw` (m), `elevation_gain_raw` (m), `moving_time_raw` and `elapsed_time_raw` (s)
  - `name`, `sport_type` (`"TrailRun"`, `"Run"`, ...), `display_type`
  - `trainer`, `commute`, `private`, `has_latlng`
  - `suffer_score`: Relative Effort (see below)
- The display strings (`distance`, `elevation_gain`, `start_date`, ...) are in the athlete's units.
  Use the `_raw` fields.

## Original file

    GET /activities/{id}/export_original

200 with the uploaded file in the body and no redirect. For a Garmin upload that is the raw FIT
(`.FIT` at bytes 8 to 12, not gzipped, 312 KB for a 7 mi run).

## Relative Effort and Fitness & Freshness: subscriber only

- On this account (no subscription), `suffer_score` is null for every activity in the list.
  The activity page shows a "Subscribe to Strava" prompt where Relative Effort would be, and
  its HTML carries no suffer or relative-effort data.
- `/athlete/fitness` shows only "Subscribe Now". The page makes no data requests.
- So neither number can be read today. If the account subscribes, `suffer_score` in the list is
  the place to read Relative Effort from. The Fitness & Freshness data request would need a new
  capture.
