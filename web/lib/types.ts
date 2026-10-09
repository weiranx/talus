export type Ledger = "cardio" | "distance" | "climb" | "descent";
export const LEDGERS: Ledger[] = ["cardio", "distance", "climb", "descent"];
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
  hr_zone_minutes: HrZoneMinutes | null;
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

// Zones chosen in Settings (3 or 5). Keys run z1..zN. ZoneMinutes above is the engine's own split:
// z1 below AeT, z2 AeT to AnT, z3 above AnT, which the 80% easy target reads.
export type HrZoneMinutes = Record<string, number>;
export interface HrZoneShare { minutes: HrZoneMinutes; share: HrZoneMinutes }
export interface ZoneInfo { zone: number; name: string; low: number | null; high: number | null; text: string }
export interface ZoneModel { zones: 3 | 5; custom: boolean; list: ZoneInfo[] }

export type SignalStatus = "ok" | "low" | "high" | "short" | "no_data" | "building";

export interface Wellness {
  date: string;
  hrv: { status: SignalStatus; text: string; last_night: number | null; avg_7d: number | null; baseline: number | null };
  resting_hr: { status: SignalStatus; text: string; last_night: number | null; baseline: number | null };
  sleep: { status: SignalStatus; text: string; last_night_h: number | null; avg_7d_h: number | null; need_h: number };
}

export interface Today {
  date: string;
  checkin: { soreness: number; energy: number; pain: number } | null;
  wellness: Wellness;
  readiness: {
    engine: { state: State; detail: string; reason: string };
    legs: { state: State; detail: string; limiter: Ledger; reason: string };
  };
  call: { call: "go" | "modify" | "rest"; session: string; detail: string; why: string; source: string };
  ledgers: Record<Ledger, LedgerDay & { spark: { day: string; fitness: number; fatigue: number }[] }>;
  week: { start: string; activities: ActivitySummary[]; distance_mi: number; gain_ft: number; loss_ft: number };
  intensity_28d: ZoneShare;
  zones_28d: HrZoneShare | null;
  zone_model: ZoneModel | null;
  thresholds: Thresholds;
}

export interface LoadData {
  date: string;
  ledgers: Record<Ledger, { today: LedgerDay | null; series: LedgerDay[] }>;
  intensity_by_week: (ZoneShare & { start: string; zones: HrZoneShare | null })[];
  zone_model: ZoneModel | null;
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
  hr_zone_minutes: HrZoneMinutes | null;
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
  zone_model: ZoneModel;
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

// Settings, per account. The engine only says whether a key is set, never its value.
export interface ZoneSettings extends ZoneModel {
  floors: number[];
  derived: number[];
  options: Record<"3" | "5", { derived: number[]; custom: number[] | null; names: string[] }>;
}

export interface SettingsThresholds {
  resting: number;
  aet: number;
  ant: number;
  max: number;
  sex: "male" | "female";
  threshold_pace_s_per_mi: number | null;
  source: string;
  aet_provisional: boolean;
  zones: ZoneSettings;
  aerobic_gap: number;
}

export type Confidence = "high" | "medium" | "low" | "provisional" | "none";

export interface Estimate {
  value: number | null;
  confidence: Confidence;
  method: string;
  activities: { id: number; day: string; name: string | null }[];
}

// Thresholds the engine estimates from the last 180 days of runs (talus infer).
export interface InferredThresholds {
  days: number;
  runs_with_hr: number;
  aet: Estimate;
  ant: Estimate;
  max: Estimate;
}

export interface LastSync {
  at: string;
  source: string;
  since: string;
  listed: number;
  new: number;
  saved: number;
  duplicates?: number;
  error: string | null;
}

export interface LastImport {
  at: string;
  source: "file" | "apple_health";
  files: number;
  listed: number;
  new: number;
  duplicates: number;
  failed: string[];
  wellness_days: number;
  no_data?: number;
  error: string | null;
}

export interface CostEstimate { per_call: number | null; per_month: number | null; calls_per_month: number; from_log: boolean }

export interface Settings {
  thresholds: SettingsThresholds | null;
  inferred: InferredThresholds | null;
  source: {
    name: string;
    key_set: boolean;
    kind: "api_key" | "oauth" | null;
    athlete_id: string | null;
    updated_at: string | null;
    garmin_fallback_set: boolean;
    activities: number;
    latest_activity: string | null;
    last_sync: LastSync | null;
  };
  sources: {
    intervals: { connected: boolean; last_sync: LastSync | null };
    // Garmin direct is unofficial and offered to the owner account only.
    garmin: { available: boolean; connected: boolean; updated_at: string | null; mfa_pending: boolean; last_sync: LastSync | null };
    upload: { last_import: LastImport | null; max_mb: number };
  };
  coach: {
    enabled: boolean;
    key_set: boolean;
    brief_model: string;
    analysis_model: string;
    style: string;
    styles: { key: string; label: string; description: string }[];
    models: { id: string; label: string; input: number; output: number }[];
    estimate: { brief: Record<string, CostEstimate>; analysis: Record<string, CostEstimate> };
    // This month's spending on the server's Anthropic key. ceiling is null for the owner.
    cap: { limit: number | null; ceiling: number | null; spent: number; month: string };
  };
  readiness: { sleep_need_h: number; sleep_need_source: "saved" | "env" | "default" };
  units: { distance: "mi"; vert: "ft" };
}

export interface Account { id: number; email: string; is_owner: boolean; created_at: string }

export interface Explanation {
  title: string;
  value: number | null;
  unit: string | null;
  formula: string;
  inputs: { label: string; value: string }[];
  steps: string[];
  note: string | null;
}
