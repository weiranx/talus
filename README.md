# Talus

Mountain training load with an AI coach. Distance and vert always together: miles, feet of gain and feet of loss, scored as three separate loads (cardio, climb, descent).

Spec: [Talus product design spec](https://claude.ai/artifact/VVYacLk1UGmNoBB4ds3qZj)

## Status

Single-user prototype. Milestones 1 to 5 are here: sync from intervals.icu, file import, per-activity scoring, the three load ledgers, the web app's Today, Load and Activity screens, and the AI coach (daily brief and workout analysis).

## Layout

| Path | What it is |
|---|---|
| `engine/` | Python load engine and `talus` CLI |
| `engine/talus/sources/` | Data sources behind one adapter: intervals.icu (primary), Garmin unofficial library (fallback) |
| `engine/talus/parse.py` | FIT, GPX, TCX and intervals.icu streams to per-second streams |
| `engine/talus/elevation.py` | Spike filter, hysteresis gain and loss, smoothed grade |
| `engine/talus/loads.py` | Cardio (Banister TRIMP, 100 = 1 h at AnT), climb and descent loads by grade band |
| `engine/talus/score.py` | Scores one activity: vert, segments, zones, loads |
| `engine/talus/ledger.py` | Cardio, climb and descent ledgers: daily load, fitness (42-day), fatigue (7-day), form and flags |
| `engine/talus/readiness.py` | Engine and legs readiness, and the rule-based call for the day |
| `engine/talus/api.py` | HTTP API the web app reads (`talus serve`) |
| `engine/talus/settings.py` | Coach settings and model prices for the Settings screen |
| `engine/talus/explain.py` | Tap-a-number explanations: the inputs, formula and arithmetic behind each number |
| `engine/talus/zones.py` | 3- and 5-zone heart rate models for display |
| `engine/talus/demo.py` | Ten weeks of sample training for trying the app |
| `web/` | Next.js web app: Today, Load, Activities, Settings |
| `engine/talus/store.py` | SQLite plus raw files kept on disk for rescoring |

## Getting started

Needs Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env          # add INTERVALS_API_KEY and INTERVALS_ATHLETE_ID
cd engine
uv sync
uv run talus thresholds --resting 45 --aet 150 --ant 172 --max 190
uv run talus sync --days 180  # pulls original FIT files from intervals.icu
uv run talus list             # mi, ft up, ft down, effort mi and the three loads
uv run talus score 12         # full breakdown for one activity, with "times a usual day"
uv run talus load             # each ledger against your normal, flags, last 14 days
uv run pytest
```

Find your intervals.icu API key and athlete id under Settings > Developer Settings. Skip `--aet` and Talus uses 88% of AnT, marked provisional.

### Web app

Needs Node 22. In two terminals:

```bash
cd engine && uv run talus serve        # engine API on :8000
cd web && npm install && npm run dev   # web app on :3000
```

To look around before your own data is in, fill a separate folder with sample training:

```bash
cd engine
TALUS_DATA_DIR=./demo-data uv run talus demo
TALUS_DATA_DIR=./demo-data uv run talus serve
```

The map uses free OpenFreeMap tiles; set `NEXT_PUBLIC_MAP_STYLE` to switch providers. If tiles cannot load, the track still draws on a blank background.

### Accounts

Talus has sign-in. Each account has its own folder, `data/users/<id>/`, with its own `talus.db` and raw files, so accounts never share activities, thresholds, settings or coach history. `data/accounts.db` holds emails, scrypt password hashes, sessions (30 days, only a hash of each token is stored) and each account's intervals.icu key, encrypted with `TALUS_SECRET_KEY` (or `data/secret.key`, made on first use).

- **First launch after upgrading:** the engine backs up `data/talus.db` to `data/backups/talus-before-accounts-<time>.db`, then moves `talus.db` and `raw/` into `data/users/1/`. The web app opens on **Set up Talus**; the account you create there is the owner and gets that data, plus the `.env` intervals.icu key.
- **Owner:** the first account. It has no coach spending ceiling, and `talus` commands work on its data unless `--user EMAIL` names another account.
- **Other accounts:** sign up at `/login`. Each adds its own intervals.icu key under Settings, Data source. The coach runs on the server's Anthropic key, capped at `TALUS_COACH_CAP_USD` a month per account (default $5); each account can set a lower cap. Set `TALUS_SIGNUP=closed` to stop new sign-ups.
- **Export and delete:** Settings, Account. Export is a zip of the account's database and raw files, without keys. Delete needs the password and removes the folder.
- **CLI:** `talus account list`, `talus account create EMAIL`, `talus account password EMAIL` (resets a forgotten password and signs out its sessions).
- **Serving beyond localhost:** put it behind HTTPS and set `TALUS_SECURE_COOKIE=1`.

SQLite per account is fine for one machine and a handful of people. Postgres is worth it once the engine runs as more than one process or host, or accounts need querying together.

### Settings

The Settings screen (`/settings`) edits what the engine stores in SQLite:

- **Heart rate and zones:** resting, AeT, AnT and max, with the zones they make. Leave AeT blank for 88% of AnT, marked provisional. Saving rescores every activity. Estimates from `talus infer` show beside your numbers with their confidence and source runs; **Use these** fills the form for you to check and save.
- **Heart rate zones:** 3 zones (below AeT, AeT to AnT, above AnT) or 5 (recovery under 90% of AeT, aerobic base up to AeT, tempo to halfway to AnT, threshold to AnT, above AnT), with each zone's starting bpm editable and a reset. Zones set time in zone on Today, Load and Activity and what the coach reads. Cardio load, readiness and the 80% target always use AeT and AnT, so switching zones never changes a load. Activities keep a 1-bpm heart-rate histogram (`hr_hist`), so a new zone model needs no rescore; activities scored before it are rescored once on the first zone save.
- **Data source:** add or replace this account's intervals.icu key and athlete id, activity count, the last sync (from here or the CLI) and **Sync now**.
- **AI coach:** on or off, the coaching style, and the model for the brief and for workout analysis, with an estimated cost per call and per month (from your own token usage once the coach has run).
- **Units:** miles and feet.

- **Account:** change password, export, delete.

The Anthropic key stays in `.env`; intervals.icu keys are per account and encrypted. The engine reports only whether a key is set and never sends one to the browser.

### AI coach

Put an Anthropic API key in `.env` as `ANTHROPIC_API_KEY` to turn it on. Without one, Today and Activity show rule-based text, labeled as such.

- **When it calls:** right after each sync or import (`talus sync`, `talus import` or **Sync now**), it writes today's brief and a read of each new activity from the last 14 days, so the pages open with them ready. It also runs when you open a page whose input changed since (your check-in, a new day, an older activity). Replies are saved and reused until the input changes, so reloads are free. `--no-coach` skips the run after a sync or import.
- **What it sees:** a compact packet of engine numbers (`talus/coach.py`): loads, readiness, check-in, the rule-based call, the last 14 days. No GPS, no names.
- **Number check:** every number in a reply must appear in the packet, rounded no finer than it appears there (within 5% when rounded to tens or more). One retry, then the rule-based text.
- **Who decides what:** in the brief the coach picks the call and explains it, but the session is always the engine's. The call may be more cautious than the rule-based one, never less, and a more cautious call gets the engine's easier session.
- **Model:** `claude-haiku-5-5` for the brief (low effort) and `claude-opus-5-5` for workout analysis (medium effort) by default; change either in Settings (`TALUS_COACH_MODEL` sets both defaults). Opus and Sonnet requests opt in to server-side fallback, so a declined request is re-run on Anthropic's recommended substitute; Haiku 5.5 doesn't support it and runs without.
- **Style:** plain, blunt, encouraging or teaching, set in Settings. Style changes the voice only: the same rules, number check and engine-owned session apply to every style.
- **Log:** every call is stored whole in the `coach_log` table: packet, prompt version, model, reply, token usage, retries and your thumbs up or down.

### Where a number comes from

Numbers with a dotted underline open their working when tapped: on Today, each ledger's "vs your normal", last 7 days and normal; on an activity, effort miles, each load and each "x a usual day". The panel shows the inputs, the formula and the arithmetic, all from the engine (`/api/explain/...`, `talus/explain.py`), in miles and feet.

### Thresholds from your own runs

`talus infer` estimates AnT, AeT and max HR from the last 180 days of runs, says how far to trust each one and which runs it came from. `--apply` saves AeT and AnT (resting and a higher max are kept); `talus thresholds` always overrides.

- **AnT:** the best hour of heart rate, or the best 30 minutes less 3%. Without a hard sustained effort in the history it reads low, and says so.
- **AeT:** the drift test read from ordinary runs. A steady, flat hour that drifts less than 5% in pace-to-heart-rate sets a floor; one that drifts more sets a ceiling. With no such run, 88% of AnT, marked provisional.
- **Max:** the highest heart rate held for 5 seconds. A true max may be higher.

Other commands: `talus import <files>` for FIT/GPX/TCX, `talus rescore` after changing thresholds or the model, `talus sync --source garmin` for the unofficial Garmin library (`uv sync --extra garmin`).

## Assumptions to check against real runs

- Hysteresis of 3 m (~10 ft) for barometric altitude and 6 m for GPS-only. Compare `talus list` gain and loss with your watch's totals on a few runs.
- Hiking is cadence under 135 steps/min, or speed under 1.5 m/s when there is no cadence.
- intervals.icu streams cadence under 110 is treated as per foot and doubled.
- Ledger flags use the spec's starting rules: ramp when the last 7 days run over 1.3 times your normal, overreach over 1.5, spike for a day 30% above any in the last 6 weeks, detraining for fitness down 10% in 2 weeks. Flags start after 28 days of history, and both averages are seeded from the first 6 weeks so a mid-season start does not read as a ramp.
- Readiness: a ledger reads loaded when the last 7 days run over 15% above normal or it is ramping, overreached on the overreach flag. Legs take the worse of climb and descent. The check-in only makes things worse: soreness 4 is loaded, 5 or pain over 3 is overreached, energy 2 or lower loads the engine.
- Grade-band factors are the spec's starting values: climb 0.75 / 1.0 / 1.25, descent 0.5 / 1.0 / 1.5, times 0.7 when hiking down.

## Data source notes

intervals.icu is used with a personal API key per account. intervals.icu asks apps used by more than one person to use OAuth (register at intervals.icu/settings/apps; 100 requests per user per day, 500 users by default). `IntervalsSource.from_credential` already accepts an OAuth access token, so adding it needs the app registration plus authorize and token routes. Strava-sourced activities are skipped. intervals.icu said on 2026-10-09 that Garmin data via its API will likely be blocked soon; if that happens, switch to `--source garmin` or file import.
