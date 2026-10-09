# Talus

Mountain training load with an AI coach. Distance and vert always together: miles, feet of gain and feet of loss, scored as three separate loads (cardio, climb, descent).

Spec: [Talus product design spec](https://claude.ai/artifact/VVYacLk1UGmNoBB4ds3qZj)

## Status

Single-user prototype. Milestones 1 to 4 are here: sync from intervals.icu, file import, per-activity scoring, the three load ledgers, and the web app's Today, Load and Activity screens. The AI coach comes next; until then Today shows a rule-based call.

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
| `engine/talus/demo.py` | Ten weeks of sample training for trying the app |
| `web/` | Next.js web app: Today, Load, Activities |
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

Other commands: `talus import <files>` for FIT/GPX/TCX, `talus rescore` after changing thresholds or the model, `talus sync --source garmin` for the unofficial Garmin library (`uv sync --extra garmin`).

## Assumptions to check against real runs

- Hysteresis of 3 m (~10 ft) for barometric altitude and 6 m for GPS-only. Compare `talus list` gain and loss with your watch's totals on a few runs.
- Hiking is cadence under 135 steps/min, or speed under 1.5 m/s when there is no cadence.
- intervals.icu streams cadence under 110 is treated as per foot and doubled.
- Ledger flags use the spec's starting rules: ramp when the last 7 days run over 1.3 times your normal, overreach over 1.5, spike for a day 30% above any in the last 6 weeks, detraining for fitness down 10% in 2 weeks. Flags start after 28 days of history, and both averages are seeded from the first 6 weeks so a mid-season start does not read as a ramp.
- Readiness: a ledger reads loaded when the last 7 days run over 15% above normal or it is ramping, overreached on the overreach flag. Legs take the worse of climb and descent. The check-in only makes things worse: soreness 4 is loaded, 5 or pain over 3 is overreached, energy 2 or lower loads the engine.
- Grade-band factors are the spec's starting values: climb 0.75 / 1.0 / 1.25, descent 0.5 / 1.0 / 1.5, times 0.7 when hiking down.

## Data source notes

intervals.icu is used with a personal API key, which covers one athlete. Strava-sourced activities are skipped. intervals.icu said on 2026-10-09 that Garmin data via its API will likely be blocked soon; if that happens, switch to `--source garmin` or file import.
