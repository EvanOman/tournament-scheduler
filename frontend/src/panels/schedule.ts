import { clear, el, fmtTime } from "../dom";
import { divisionColor } from "../state";
import type { AppState } from "../state";
import type { GameView, SchedulePayload } from "../types";

type ScheduleView = "field" | "team";
let currentView: ScheduleView = "field";

const PX_PER_MIN = 1.5;

// Schedule panel: a proportional per-field timeline, a per-team itinerary
// toggle, and clear "waiting" / "conflict" states for partial or infeasible
// specs. Redraws wholesale on each solve_completed -- cheap at demo scale.
export function renderSchedule(root: HTMLElement, state: AppState, onRerender: () => void): void {
  clear(root);
  const p = state.schedule;

  const toggle = el("div", { class: "seg" }, [
    segButton("Field view", currentView === "field", () => {
      currentView = "field";
      onRerender();
    }),
    segButton("Team view", currentView === "team", () => {
      currentView = "team";
      onRerender();
    }),
  ]);

  root.append(
    el("div", { class: "panel-head" }, [
      el("span", { class: "panel-kicker", text: "Sample schedule" }),
      state.solvePhase === "solving" ? el("span", { class: "solve-tag", text: "solving…" }) : toggle,
    ]),
  );

  if (!p || p.status === "incomplete") {
    root.append(waiting(p));
    return;
  }
  if (p.status === "inconclusive") {
    root.append(
      el("div", { class: "sched-state" }, [
        el("p", { class: "sched-state-title", text: "Still crunching — this one is tight" }),
        el("p", { class: "sched-state-sub", text: p.message ?? "The quick solve pass ran out of time." }),
      ]),
    );
    return;
  }
  if (p.status === "infeasible" || p.status === "invalid") {
    // A stale conflict banner under a live "solving…" tag reads as contradictory
    // (persona P4) — while a new solve runs, show the solving state alone.
    if (state.solvePhase === "solving") {
      root.append(el("div", { class: "sched-note", text: "Re-solving with your latest changes…" }));
      return;
    }
    root.append(conflict(p));
    return;
  }
  if (!p.fields || !p.teams || !p.stats) {
    root.append(el("div", { class: "sched-note", text: p.message ?? "No schedule yet." }));
    return;
  }

  root.append(statBar(p));
  if (p.assumptions.length) root.append(assumptions(p));
  root.append(currentView === "field" ? fieldTimeline(p) : teamItineraries(p));
}

function segButton(label: string, active: boolean, onClick: () => void): HTMLElement {
  const b = el("button", { class: `seg-btn ${active ? "active" : ""}`, text: label });
  b.addEventListener("click", onClick);
  return b;
}

function statBar(p: SchedulePayload): HTMLElement {
  const s = p.stats!;
  return el("div", { class: "stat-bar" }, [
    stat("Games", String(s.num_games_scheduled)),
    stat("Teams", String(s.num_teams)),
    stat("Fields", String(s.num_fields)),
    stat("Divisions", String(s.num_divisions)),
    stat("Solved in", `${s.wall_time_seconds.toFixed(1)}s`),
  ]);
}
function stat(label: string, value: string): HTMLElement {
  return el("div", { class: "stat" }, [
    el("span", { class: "stat-value", text: value }),
    el("span", { class: "stat-label", text: label }),
  ]);
}

function assumptions(p: SchedulePayload): HTMLElement {
  return el("details", { class: "assume" }, [
    el("summary", { text: `${p.assumptions.length} assumed default${p.assumptions.length > 1 ? "s" : ""}` }),
    el(
      "ul",
      {},
      p.assumptions.map((a) => el("li", { text: a })),
    ),
  ]);
}

function fieldTimeline(p: SchedulePayload): HTMLElement {
  const total = p.total_min ?? 0;
  const height = Math.max(total * PX_PER_MIN, 200);
  const dayStart = p.day_start ? new Date(p.day_start) : new Date();

  const axis = el("div", { class: "tl-axis", style: `height:${height}px` });
  const startMin = dayStart.getMinutes();
  const firstTick = startMin === 0 ? 0 : 60 - startMin;
  for (let m = firstTick; m <= total; m += 60) {
    const t = new Date(dayStart.getTime() + m * 60000);
    axis.append(
      el("div", { class: "tl-tick", style: `top:${m * PX_PER_MIN}px` }, [
        el("span", { class: "tl-tick-label", text: fmtTime(t.toISOString()) }),
      ]),
    );
  }

  const lanes = el("div", { class: "tl-lanes" });
  for (const f of p.fields!) {
    const lane = el("div", { class: "tl-lane", style: `height:${height}px` });
    lane.append(el("div", { class: "tl-lane-head", text: `${f.name} · ${f.size}` }));
    for (const g of f.games) lane.append(gameBlock(g));
    lanes.append(el("div", { class: "tl-lane-wrap" }, [lane]));
  }

  return el("div", { class: "timeline" }, [axis, lanes]);
}

function gameBlock(g: GameView): HTMLElement {
  const top = g.start_offset_min * PX_PER_MIN;
  const h = Math.max(g.duration_min * PX_PER_MIN, 24);
  const block = el("div", {
    class: "game",
    style: `top:${top + 26}px;height:${h}px;--accent:${divisionColor(g.color_index)}`,
    title: `${g.home} vs ${g.away} — ${fmtTime(g.start)}`,
  });
  block.append(el("div", { class: "game-time", text: fmtTime(g.start) }));
  block.append(el("div", { class: "game-teams", text: `${g.home} v ${g.away}` }));
  block.append(el("div", { class: "game-div", text: g.division_name }));
  return block;
}

function teamItineraries(p: SchedulePayload): HTMLElement {
  const wrap = el("div", { class: "team-list" });
  for (const t of p.teams!) {
    const row = el("div", { class: "team-row" });
    row.append(
      el("div", { class: "team-name" }, [
        el("span", { class: "team-dot", style: `background:${divisionColor(t.color_index)}` }),
        el("span", { text: t.name }),
      ]),
    );
    const games = el("div", { class: "team-games" });
    for (const g of t.games) {
      const opp = g.home_team_id === t.id ? g.away : g.home;
      games.append(
        el("div", { class: "team-game", style: `--accent:${divisionColor(t.color_index)}` }, [
          el("span", { class: "tg-time", text: `${g.day} ${fmtTime(g.start)}` }),
          el("span", { class: "tg-opp", text: `v ${opp}` }),
          el("span", { class: "tg-field", text: g.field_name }),
        ]),
      );
    }
    if (t.games.length === 0) games.append(el("span", { class: "tg-none", text: "no games" }));
    row.append(games);
    wrap.append(row);
  }
  return wrap;
}

function waiting(p: SchedulePayload | null): HTMLElement {
  const box = el("div", { class: "sched-state waiting" });
  box.append(el("div", { class: "sched-state-icon", text: "◷" }));
  box.append(el("div", { class: "sched-state-title", text: "Waiting for a few details" }));
  const missing = p?.missing ?? [];
  if (missing.length) {
    box.append(
      el(
        "ul",
        { class: "sched-missing" },
        missing.map((m) => el("li", { text: m })),
      ),
    );
  } else {
    box.append(el("p", { class: "sched-state-sub", text: "Start describing your tournament in the chat." }));
  }
  return box;
}

function conflict(p: SchedulePayload): HTMLElement {
  const box = el("div", { class: "sched-state conflict" });
  box.append(el("div", { class: "sched-state-icon", text: "⚠" }));
  box.append(el("div", { class: "sched-state-title", text: "These constraints can't all be met" }));
  const issues = p.validation?.errors ?? [];
  if (issues.length) {
    box.append(
      el(
        "ul",
        { class: "sched-missing" },
        issues.slice(0, 6).map((m) => el("li", { text: m })),
      ),
    );
  } else {
    box.append(
      el("p", {
        class: "sched-state-sub",
        text: "The solver couldn't fit every game into the available fields and time. Try adding a field, extending hours, or relaxing rest time.",
      }),
    );
  }
  return box;
}
