# Talus

Mountain training load with an AI coach. Distance and vert always together: miles, feet of gain and feet of loss, scored as three separate loads (cardio, climb, descent).

Spec: [Talus product design spec](https://claude.ai/artifact/VVYacLk1UGmNoBB4ds3qZj)

## Status

Single-user prototype. Milestones 1 and 2 are here: sync from intervals.icu, file import, and per-activity scoring. Ledgers, web screens and the coach come next.

## Layout

| Path | What it is |
|---|---|
| `engine/` | Python load engine and `talus` CLI |
| `engine/talus/sources/` | Data sources behind one adapter: intervals.icu (primary), Garmin unofficial library (fallback) |
| `engine/talus/parse.py` | FIT, GPX, TCX and intervals.icu streams to per-second streams |
| `engine/talus/elevation.py` | Spike filter, hysteresis gain and loss, smoothed grade |
| `engine/talus/loads.py` | Cardio (Banister TRIMP, 100 = 1 h at AnT), climb and descent loads by grade band |
| `engine/talus/score.py` | Scores one activity: vert, segments, zones, loads |
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
uv run talus score 12         # full breakdown for one activity
uv run pytest
```

Find your intervals.icu API key and athlete id under Settings > Developer Settings. Skip `--aet` and Talus uses 88% of AnT, marked provisional.

Other commands: `talus import <files>` for FIT/GPX/TCX, `talus rescore` after changing thresholds or the model, `talus sync --source garmin` for the unofficial Garmin library (`uv sync --extra garmin`).

## Assumptions to check against real runs

- Hysteresis of 3 m (~10 ft) for barometric altitude and 6 m for GPS-only. Compare `talus list` gain and loss with your watch's totals on a few runs.
- Hiking is cadence under 135 steps/min, or speed under 1.5 m/s when there is no cadence.
- intervals.icu streams cadence under 110 is treated as per foot and doubled.
- Grade-band factors are the spec's starting values: climb 0.75 / 1.0 / 1.25, descent 0.5 / 1.0 / 1.5, times 0.7 when hiking down.

## Data source notes

intervals.icu is used with a personal API key, which covers one athlete. Strava-sourced activities are skipped. intervals.icu said on 2026-10-09 that Garmin data via its API will likely be blocked soon; if that happens, switch to `--source garmin` or file import.
