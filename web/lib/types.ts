export type Ledger = "cardio" | "climb" | "descent";
export const LEDGERS: Ledger[] = ["cardio", "climb", "descent"];
export type State = "fresh" | "loaded" | "overreached";

export interface Flag { key: string; text: string }

export interface LedgerDay {
  day: string;
  load: number;
  fitness: number;
  fatigue: number;
  form: number;
  vs_normal: number | null;
  vs_normal_text: string;
  flags: Flag[];
  building: boolean;
}

export interface ZoneMinutes { z1: number; z2: number; z3: number }

export interface ActivitySummary {
  id: number;
  day: string;
  start_time: string;
  name: string | null;
  sport: string | null;
  source: string;
  device_name: string | null;
  distance_mi: number;
  gain_ft: number;
  loss_ft: number;
  effort_mi: number;
  moving_time_s: number;
  elapsed_time_s: number;
  cardio_load: number | null;
  climb_load: number;
  descent_load: number;
  cardio_estimated: boolean;
  zone_minutes: ZoneMinutes;
  hike_share: number;
  notes: string[];
}

export interface Thresholds {
  resting: number;
  aet: number;
  ant: number;
  max: number;
  source: string;
  aerobic_gap: number;
}

export interface ZoneShare { minutes: ZoneMinutes; share: ZoneMinutes }

export interface Today {
  date: string;
  checkin: { soreness: number; energy: number; pain: number } | null;
  readiness: {
    engine: { state: State; detail: string; reason: string };
    legs: { state: State; detail: string; limiter: Ledger; reason: string };
  };
  call: { call: "go" | "modify" | "rest"; session: string; detail: string; why: string; source: string };
  ledgers: Record<Ledger, LedgerDay & { spark: { day: string; fitness: number; fatigue: number }[] }>;
  week: { start: string; activities: ActivitySummary[]; distance_mi: number; gain_ft: number; loss_ft: number };
  intensity_28d: ZoneShare;
  thresholds: Thresholds;
}

export interface LoadData {
  date: string;
  ledgers: Record<Ledger, { today: LedgerDay | null; series: LedgerDay[] }>;
  intensity_by_week: (ZoneShare & { start: string })[];
  biggest_days: ActivitySummary[];
  thresholds: Thresholds | null;
  history: { since: string | null; gap: { from: string; to: string } | null };
}

export interface Segment {
  index: number;
  kind: "climb" | "descent" | "flat";
  distance_mi: number;
  gain_ft: number;
  loss_ft: number;
  avg_grade: number;
  grade_band: string;
  hike_share: number;
  time_s: number;
  start_s: number;
  end_s: number;
  zone_minutes: ZoneMinutes | null;
  climb_load: number;
  descent_load: number;
}

export interface UsualDay { load: number | null; times_usual_day: number | null; six_week_high: boolean }

export interface ActivityDetail extends Omit<ActivitySummary, "cardio_load" | "climb_load" | "descent_load" | "effort_mi"> {
  effort_mi: number;
  gain_by_band_ft: Record<string, number>;
  loss_by_band_ft: Record<string, number>;
  longest_climb_ft: number;
  longest_descent_ft: number;
  minutes_above_8000ft: number;
  loads: Record<Ledger, UsualDay>;
  segments: Segment[];
  streams: {
    time_s: number[];
    distance_mi: number[];
    altitude_ft: number[] | null;
    heart_rate: number[] | null;
    zone: number[] | null;
    lat: number[] | null;
    lon: number[] | null;
  };
}

export type Call = { call: "go" | "modify" | "rest"; session: string; detail: string; why: string };
export interface AnalysisReply { job: string; off: string; cost: string; changes: string; takeaway: string }

export interface CoachReply<T> {
  source: "coach" | "rules";
  reply: T;
  log_id: number | null;
  feedback: number | null;
  fallback_reason: string | null;
  model: string | null;
}
