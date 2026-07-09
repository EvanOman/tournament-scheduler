// Shapes mirrored from the FastAPI WebSocket protocol (tourneydesk/web/app.py)
// and the schedule payload (tourneydesk/web/schedule_view.py). Kept in one
// place so the panels stay in lockstep with the server contract.

export interface SessionSummary {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  num_divisions: number;
  num_teams: number;
  intake_complete: boolean;
}

export interface TimeWindow {
  start: string;
  end: string;
}

export interface RulesState {
  tournament: { name: string; description: string; source_quotes: string[] };
  divisions: DivisionRule[];
  teams: TeamRule[];
  fields: FieldRule[];
  coaching_conflicts: CoachingRule[];
  team_avoidances: AvoidanceRule[];
  time_preferences: TimePrefRule[];
  field_preferences: FieldPrefRule[];
  intake_complete: boolean;
}

export interface DivisionRule {
  id: string;
  name: string;
  field_size: string;
  game_format: string | null;
  game_duration_minutes: number;
  halftime_minutes: number | null;
  min_rest_minutes: number | null;
  games_per_team: number | null;
  pool_size: number | null;
  source_quotes: string[];
}
export interface TeamRule {
  id: string;
  name: string;
  division_id: string;
  club: string | null;
  source_quotes: string[];
}
export interface FieldRule {
  id: string;
  name: string;
  size: string;
  availability: TimeWindow[];
  source_quotes: string[];
}
export interface CoachingRule {
  coach_name: string;
  team_ids: string[];
  source_quotes: string[];
}
export interface AvoidanceRule {
  team_ids: string[];
  reason: string;
  source_quotes: string[];
}
export interface TimePrefRule {
  target: string;
  target_type: string;
  preferred_windows: TimeWindow[];
  priority: string;
  source_quotes: string[];
}
export interface FieldPrefRule {
  target: string;
  target_type: string;
  preferred_field_ids: string[];
  priority: string;
  source_quotes: string[];
}

export interface GameView {
  game_id: string;
  division_id: string;
  division_name: string;
  color_index: number;
  pool_id: string;
  home_team_id: string;
  away_team_id: string;
  home: string;
  away: string;
  field_id: string;
  field_name: string;
  start: string;
  end: string;
  day: string;
  start_offset_min: number;
  duration_min: number;
}

export interface FieldView {
  id: string;
  name: string;
  size: string;
  games: GameView[];
}
export interface TeamView {
  id: string;
  name: string;
  division_id: string;
  division_name: string;
  color_index: number;
  games: GameView[];
}

export type SolveStatus = "incomplete" | "infeasible" | "invalid" | "solved" | "inconclusive";

export interface SchedulePayload {
  status: SolveStatus;
  missing: string[];
  assumptions: string[];
  message?: string;
  tournament_name?: string;
  stats?: {
    status: string;
    wall_time_seconds: number;
    num_games_scheduled: number;
    num_teams: number;
    num_fields: number;
    num_divisions: number;
  };
  day_start?: string;
  day_end?: string;
  total_min?: number;
  divisions?: { id: string; name: string; color_index: number }[];
  fields?: FieldView[];
  teams?: TeamView[];
  validation?: { valid: boolean; errors: string[]; warnings: string[] };
}

export type ServerEvent =
  | { type: "session_state"; rules: RulesState; transcript: TranscriptEntry[] }
  | { type: "user_message"; text: string }
  | { type: "assistant_delta"; text: string }
  | { type: "assistant_message"; text: string; echoes: string[]; complete: boolean }
  | { type: "spec_updated"; rules: RulesState }
  | { type: "solve_started" }
  | { type: "solve_completed"; schedule: SchedulePayload }
  | { type: "conflict_detected"; detail: SchedulePayload }
  | { type: "error"; message: string };

export interface TranscriptEntry {
  role: "director" | "agent";
  text: string;
  echoes?: string[];
}
