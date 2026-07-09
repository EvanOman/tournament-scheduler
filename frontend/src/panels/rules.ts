import { clear, el, fmtTime } from "../dom";
import { divisionColor } from "../state";
import type { AppState } from "../state";
import type { RulesState } from "../types";

// Rules panel: every understood constraint as a card, grouped by category, each
// card carrying the director's originating quote (the anti-hallucination
// contract made visible).
export function renderRules(root: HTMLElement, state: AppState): void {
  clear(root);
  const rules = state.rules;
  root.append(
    el("div", { class: "panel-head" }, [
      el("span", { class: "panel-kicker", text: "Rules understood" }),
      el("span", { class: "rule-count", text: rules ? String(countRules(rules)) : "0" }),
    ]),
  );

  const body = el("div", { class: "rules-body" });
  if (!rules || countRules(rules) === 0) {
    body.append(
      el("div", { class: "rules-empty", text: "Nothing captured yet. Constraints appear here as you describe them." }),
    );
    root.append(body);
    return;
  }

  if (rules.tournament.name) {
    body.append(
      group("Tournament", [
        card(rules.tournament.name, rules.tournament.description || "—", rules.tournament.source_quotes),
      ]),
    );
  }

  if (rules.divisions.length) {
    body.append(
      group(
        "Divisions",
        rules.divisions.map((d, i) => {
          const bits = [
            // Show a format only if the director actually stated one — glossing
            // field size as a format fabricated "4v4/3v3" claims (persona P1/P2).
            d.game_format,
            `${d.field_size} fields`,
            `${d.game_duration_minutes}′ games`,
            d.games_per_team ? `${d.games_per_team} games/team` : null,
            d.min_rest_minutes ? `${d.min_rest_minutes}′ rest` : null,
          ].filter(Boolean) as string[];
          return card(d.name, bits.join(" · "), d.source_quotes, divisionColor(i));
        }),
      ),
    );
  }

  if (rules.teams.length) {
    const byDiv = new Map<string, typeof rules.teams>();
    for (const t of rules.teams) {
      const arr = byDiv.get(t.division_id) ?? [];
      arr.push(t);
      byDiv.set(t.division_id, arr);
    }
    const cards = [...byDiv.entries()].map(([divId, teams]) => {
      const divName = rules.divisions.find((d) => d.id === divId)?.name ?? divId;
      const names = teams.map((t) => t.name).join(", ");
      return card(`${divName} · ${teams.length} teams`, names, teams[0]?.source_quotes ?? []);
    });
    body.append(group("Teams", cards));
  }

  if (rules.fields.length) {
    body.append(
      group(
        "Fields",
        rules.fields.map((f) => {
          const windows = f.availability.map((w) => `${fmtTime(w.start)}–${fmtTime(w.end)}`).join(", ");
          return card(f.name, `${f.size}-size · ${windows || "no window yet"}`, f.source_quotes);
        }),
      ),
    );
  }

  if (rules.coaching_conflicts.length) {
    body.append(
      group(
        "Coaching conflicts",
        rules.coaching_conflicts.map((c) =>
          card(c.coach_name, `Can't overlap: ${c.team_ids.join(", ")}`, c.source_quotes),
        ),
      ),
    );
  }

  if (rules.team_avoidances.length) {
    body.append(
      group(
        "Team avoidances",
        rules.team_avoidances.map((a) =>
          card(a.team_ids.join(" ⨯ "), a.reason || "Not scheduled at the same time", a.source_quotes),
        ),
      ),
    );
  }

  if (rules.time_preferences.length) {
    body.append(
      group(
        "Time preferences",
        rules.time_preferences.map((p) => {
          const w = p.preferred_windows.map((x) => `${fmtTime(x.start)}–${fmtTime(x.end)}`).join(", ");
          return card(`${p.target} (${p.priority})`, w, p.source_quotes);
        }),
      ),
    );
  }

  if (rules.field_preferences.length) {
    body.append(
      group(
        "Field preferences",
        rules.field_preferences.map((p) =>
          card(`${p.target} (${p.priority})`, p.preferred_field_ids.join(", "), p.source_quotes),
        ),
      ),
    );
  }

  root.append(body);
}

function countRules(r: RulesState): number {
  return (
    (r.tournament.name ? 1 : 0) +
    r.divisions.length +
    (r.teams.length ? 1 : 0) +
    r.fields.length +
    r.coaching_conflicts.length +
    r.team_avoidances.length +
    r.time_preferences.length +
    r.field_preferences.length
  );
}

function group(title: string, cards: HTMLElement[]): HTMLElement {
  return el("section", { class: "rule-group" }, [
    el("h3", { class: "rule-group-title", text: title }),
    el("div", { class: "rule-cards" }, cards),
  ]);
}

function card(title: string, detail: string, quotes: string[], accent?: string): HTMLElement {
  const node = el("div", { class: "rule-card" });
  if (accent) node.style.setProperty("--accent", accent);
  node.append(el("div", { class: "rule-card-title", text: title }));
  node.append(el("div", { class: "rule-card-detail", text: detail }));
  const q = quotes.find((s) => s && s.trim());
  if (q) node.append(el("div", { class: "rule-quote", text: `“${q}”` }));
  return node;
}

