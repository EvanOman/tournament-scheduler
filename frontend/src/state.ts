import type { RulesState, SchedulePayload, TranscriptEntry } from "./types";

// Okabe-Ito: an eight-colour qualitative palette engineered to stay
// distinguishable under the common forms of colour-vision deficiency. Divisions
// are coloured by their server-assigned index into this list.
export const DIVISION_COLORS = [
  "#E69F00",
  "#56B4E9",
  "#009E73",
  "#F0E442",
  "#0072B2",
  "#D55E00",
  "#CC79A7",
  "#999999",
];

export function divisionColor(index: number): string {
  return DIVISION_COLORS[index % DIVISION_COLORS.length];
}

export type SolvePhase = "idle" | "solving";

export interface ChatMessage {
  role: "director" | "agent";
  text: string;
  echoes?: string[];
  streaming?: boolean;
}

export interface AppState {
  sessionId: string | null;
  connected: boolean;
  chat: ChatMessage[];
  rules: RulesState | null;
  schedule: SchedulePayload | null;
  solvePhase: SolvePhase;
}

export function emptyRules(): RulesState {
  return {
    tournament: { name: "", description: "", source_quotes: [] },
    divisions: [],
    teams: [],
    fields: [],
    coaching_conflicts: [],
    team_avoidances: [],
    time_preferences: [],
    field_preferences: [],
    intake_complete: false,
  };
}

export function transcriptToChat(entries: TranscriptEntry[]): ChatMessage[] {
  return entries.map((e) => ({ role: e.role, text: e.text, echoes: e.echoes }));
}

export function initialState(): AppState {
  return { sessionId: null, connected: false, chat: [], rules: null, schedule: null, solvePhase: "idle" };
}
