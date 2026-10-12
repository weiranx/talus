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

export interface ScoreItem { key: string; name: string; points: number; used: boolean; text: string; limiter?: string }
export interface ScorePoint { day: string; score: number; label: string; call: "go" | "modify" | "rest" }
export interface ReadinessScore {
  score: number;
  label: "Fresh" | "Ready" | "Ease off" | "Rest";
  call: "go" | "modify" | "rest";
  raw: number;
  held: string | null;
  items: ScoreItem[];
  used: string[];
  missing: string[];
  basis: string;
  trend: ScorePoint[];
}

export interface PlannedToday { title: string; detail: string; source: "manual" | "plan"; kind: string; miles: number; gain_ft: number; loss_ft: number }

export interface Today {
  date: string;
  planned: PlannedToday | null;
  checkin: { soreness: number; energy: number; pain: number } | null;
  wellness: Wellness;
  readiness: {
    engine: { state: State; detail: string; reason: string };
    legs: { state: State; detail: string; limiter: Ledger; reason: string };
  };
  score: ReadinessScore;
  call: { call: "go" | "modify" | "rest"; session: string; detail: string; why: string; source: string; planned?: boolean };
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
  weekly: (WeekTotals & { days: number })[];
  last_week_to_date: WeekTotals;
  zone_model: ZoneModel | null;
  biggest_days: ActivitySummary[];
  thresholds: Thresholds | null;
  history: { since: string | null; gap: { from: string; to: string } | null };
  projection: Projection;
  next_week: NextWeek;
}

// The next 7 days: what's scheduled, and per ledger the total that holds your normal and the most
// before ramping too fast, spread evenly, in hours, miles or feet. Ledgers still building have no room.
export interface NextWeek {
  start: string;
  end: string;
  scheduled: { runs: number; miles: number; gain_ft: number; loss_ft: number; hours: number | null };
  room: Partial<Record<Ledger, { unit: "hours" | "miles" | "gain_ft" | "loss_ft"; hold: number; limit: number }>>;
}

/** Everything done in one Monday week, or part of one. */
export interface WeekTotals {
  start: string;
  activities: number;
  distance_mi: number;
  gain_ft: number;
  loss_ft: number;
  effort_mi: number;
  moving_time_s: number;
  cardio_load: number;
  climb_load: number;
  descent_load: number;
}

export interface ProjectedDay { day: string; fitness: number; fatigue: number; flags: string[] }
export interface ScheduleWarning { week: string; ledgers: Ledger[]; key: "ramp" | "overreach"; text: string }
export interface Projection { series: Partial<Record<Ledger, ProjectedDay[]>>; warnings: ScheduleWarning[]; until: string | null }

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
export interface LoadReply { last_week: string; trend: string; watch: string; next: string }

export interface AnalysisReply { job: string; off: string; cost: string; changes: string; takeaway: string }

export interface CoachReply<T> {
  source: "coach" | "rules";
  reply: T;
  log_id: number | null;
  feedback: number | null;
  fallback_reason: string | null;
  model: string | null;
  cost?: AiCost | null;
}

// Only present when the admin's "Show AI cost" flag is on. A reused reply is cached: it cost
// nothing this time, and carries its first call's cost.
export interface AiCost { usd: number; model: string | null; input_tokens: number; output_tokens: number; cached: boolean }

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
  /** "computing" while runs are still being read in the background; the estimates then cover
   * only the runs read so far, and are missing until the first ones are. */
  status: "ready" | "computing";
  runs_read: number;
  runs_total: number;
  days: number;
  runs_with_hr: number;
  aet?: Estimate;
  ant?: Estimate;
  max?: Estimate;
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
    athlete_name: string | null;
    // Whether the server can connect intervals.icu with OAuth (it has a client id).
    oauth_available: boolean;
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
    // Posted by the Talus iOS app to /api/import/healthkit.
    healthkit: { last_sync: { at: string; workouts_added: number; workouts_skipped: number; wellness_days: number } | null };
    // Files the web or iOS app pulled straight off a Garmin watch over Bluetooth.
    watch: { last_sync: string | null };
  };
  // How new activities arrive. File upload works with either.
  sync_method: "intervals" | "watch";
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

export interface Account {
  id: number;
  email: string;
  is_owner: boolean;
  is_admin: boolean;
  created_at: string;
  has_password: boolean;
  google: { email: string | null; linked_at: string } | null;
  google_available: boolean;
}

export interface Explanation {
  title: string;
  value: number | null;
  unit: string | null;
  formula: string;
  inputs: { label: string; value: string }[];
  steps: string[];
  note: string | null;
}

// Objective, plan, weekly review and chat. Every number is the engine's.
export type DimStatus = "ready" | "close" | "gap" | "unknown" | "context";

export interface Dim {
  key: string;
  name: string;
  demand_label: string;
  capacity_label: string;
  demand: number | null;
  capacity: number;
  unit: "effort mi" | "h" | "ft" | null;
  rule: number | null;
  ratio: number | null;
  status: DimStatus;
  status_text: string;
}

export interface ReadinessEval { dims: Dim[]; limiter: string | null; verdict: "ready" | "close" | "gap" }

export interface Objective {
  id: number;
  name: string;
  day: string;
  distance_mi: number;
  gain_ft: number;
  loss_ft: number;
  longest_climb_ft: number | null;
  longest_descent_ft: number | null;
  high_point_ft: number | null;
  source: "manual" | "gpx";
  days_to_go: number;
}

export type Workout = "none" | "track" | "tempo" | "hill_repeats";

export interface Constraints {
  days: number[]; weekday_min: number; weekend_min: number; long_day: number; notes: string;
  back_to_back?: boolean; workout?: Workout;
}

export interface LongDay {
  day: string;
  miles: number;
  gain_ft: number;
  loss_ft: number;
  minutes: number;
  effort_mi: number;
  longest_descent_ft: number | null;
  // With back-to-back long days, the pair's other day.
  second?: { day: string; miles: number; gain_ft: number; loss_ft: number; minutes: number; effort_mi: number } | null;
}

export type Focus = "build" | "peak" | "recovery" | "taper" | "race";

export interface PlanWeek {
  index: number;
  start: string;
  focus: Focus;
  miles: number;
  gain_ft: number;
  loss_ft: number;
  hours: number;
  below_aet_hours: number;
  long: LongDay | null;
  limited_by: string[];
  notes: string[];
  workout?: Exclude<Workout, "none"> | null;
}

export interface WeekBrief { focus: Focus; miles: number; gain_ft: number; loss_ft: number; hours: number }

export interface Plan {
  id: number;
  status: "draft" | "accepted";
  reason: string;
  reason_text: string;
  created_at: string;
  decided_at: string | null;
  built_on: string;
  constraints: Constraints;
  baseline: { miles: number; gain_ft: number; loss_ft: number; hours: number };
  pace: { min_per_effort_mi: number; learned: boolean; days: number };
  demand: { effort_mi: number; time: { low_h: number; mid_h: number; high_h: number; pace_learned: boolean } };
  peak: { miles: number; gain_ft: number; loss_ft: number };
  weeks: PlanWeek[];
  projected: ReadinessEval & { capacity: Record<string, number> };
  reachable: boolean;
  back_to_back?: boolean;
  options: { smaller: { factor: number; distance_mi: number; gain_ft: number; loss_ft: number } | null; later: string | null };
  now: ReadinessEval;
  changes: { start: string; before: WeekBrief | null; after: WeekBrief }[];
}

export type SessionKind = "long" | "long2" | "hills" | "easy" | "track" | "tempo" | "hill_repeats" | "rest" | "objective" | "done";

export interface PlanSession {
  day: string;
  kind: SessionKind;
  miles: number;
  gain_ft: number;
  loss_ft: number;
  minutes: number;
  focus: Focus;
  title: string;
  how: string;
  own?: boolean;
  detail: string;
  longest_descent_ft?: number | null;
}

export interface WeekReview {
  start: string;
  end: string;
  actual: { activities: number; miles: number; gain_ft: number; loss_ft: number; hours: number; below_aet_pct: number | null; longest_day_effort_mi: number };
  target: (WeekBrief & { focus: Focus }) | null;
  share: { miles: number | null; gain_ft: number | null; loss_ft: number | null } | null;
  flags: { ledger: Ledger; key: string; text: string }[];
  max_pain: number | null;
  replan: string[];
  replan_text: string[];
}

export interface PlanPage {
  date: string;
  constraints: Constraints;
  objective: Objective | null;
  readiness: (ReadinessEval & { demand: Plan["demand"] & { distance_mi: number; gain_ft: number; loss_ft: number }; pace: Plan["pace"] }) | null;
  accepted: Plan | null;
  draft: Plan | null;
  sessions: PlanSession[];
  review: WeekReview | null;
  fitness_only: string;
  thresholds: Thresholds | null;
  this_week?: WeekProgress;
  actuals?: Record<string, WeekActual>;
}

export interface Vol { miles: number; gain_ft: number; loss_ft: number }
export interface WeekActual extends Vol { below_aet_pct: number | null }
export interface WeekProgress { start: string; dated: boolean; target: (Vol & { focus: Focus }) | null; done: WeekActual | null; ahead: Vol }

export interface PlanReply { verdict: string; limiter: string; plan: string }
export interface ReviewReply { week: string; next: string }

export interface CoachAction { id: number; kind: string; summary: string; status: "proposed" | "running" | "done" | "dismissed" | "failed"; result: string | null }
export interface ChatMessage { id: number; created_at: string; role: "athlete" | "coach"; text: string; source: string | null; log_id: number | null; cost?: AiCost | null; actions?: CoachAction[] }

export type RunKind = "easy" | "long" | "hills" | "tempo" | "intervals" | "hike" | "race" | "other";

export interface ScheduledRun {
  id: number | null;
  day: string;
  kind: RunKind;
  miles: number;
  gain_ft: number;
  loss_ft: number;
  minutes: number;
  estimated_minutes: boolean;
  zone: number | null;
  notes: string;
  source: "manual" | "plan";
  title: string;
  how: string | null;
  detail: string;
  status: "done" | "missed" | "today" | "planned";
  activity: { id: number; name: string | null; sport: string | null; distance_mi: number; gain_ft: number; loss_ft: number; moving_time_s: number } | null;
}

export interface Schedule {
  date: string;
  start: string;
  days: number;
  items: ScheduledRun[];
  // Activities on foot that no scheduled run matched, so the calendar shows every day you trained.
  activities?: { id: number; day: string; name: string; sport: string | null; distance_mi: number; gain_ft: number;
    loss_ft: number; moving_time_s: number }[];
  weeks: WeekProgress[];
  warnings: ScheduleWarning[];
  kinds: { key: RunKind; label: string }[];
  zone_model: ZoneModel | null;
}

export interface AdminAccount {
  id: number;
  email: string;
  is_owner: boolean;
  is_admin: boolean;
  created_at: string;
  last_seen: string | null;
  disabled_at: string | null;
  sign_in: string[];
  sessions: number;
  activities: number;
  connected: string[];
  sources: string[];
  last_sync: { at: string; source: string; ok: boolean } | null;
  coach: { spent: number; ceiling: number | null; custom_ceiling: boolean; own_cap: number | null; limit: number | null };
  disk_bytes: number;
}

export interface AdminOverview {
  accounts: AdminAccount[];
  month: string;
  signup: { open: boolean; chosen: boolean | null; env: boolean };
  features: { show_ai_cost: boolean };
  system: {
    revision: string | null;
    model_version: string;
    coach: { prompt_version: string; brief_model: string; analysis_model: string; key_set: boolean };
    server_ceiling_usd: number;
    data_bytes: number;
    disk: { total: number; free: number };
  };
  log: { id: number; at: string; action: string; admin: string | null; target: string | null; detail: Record<string, unknown> | null }[];
}

// Planning with the coach: the conversation that fills the plan's inputs.
export interface TalkGroup { key: string; options: string[]; multi?: boolean; label?: string; value?: string | string[] | null }
export interface TalkMessage {
  role: "athlete" | "coach"; text: string; step?: string | null;
  groups?: TalkGroup[]; actions?: { id: "accept" | "keep" | "retry"; label: string }[]; cost?: AiCost | null;
}
export interface Talk { messages: TalkMessage[]; step: string; mode: "setup" | "change"; reads_with_ai: boolean }

export type OnboardingStep = "connect" | "import" | "thresholds" | "sleep" | "coach" | "tour";
export type ImportRange = "6w" | "6m" | "all";

export interface ImportStatus {
  state: "running" | "done";
  source: "intervals" | "garmin";
  range: ImportRange;
  days: number;
  started: string;
  finished?: string;
  done: number;
  total: number | null;
  saved: number;
  duplicates?: number;
  wellness_days?: number;
  error: string | null;
}

export interface Onboarding {
  needed: boolean;
  started: string | null;
  finished: string | null;
  established: boolean;
  steps: { key: OnboardingStep; done: boolean; skipped: boolean; required: boolean }[];
  next: OnboardingStep | null;
  import: ImportStatus | null;
  activities: number;
  sources: { intervals: boolean; garmin: boolean; garmin_available: boolean };
  resting_hint: number | null;
  coach: { enabled: boolean; style: string; key_set: boolean; styles: { key: string; label: string; description: string }[] };
  import_choices: Record<ImportRange, number>;
}
