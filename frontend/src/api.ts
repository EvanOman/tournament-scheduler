import type { SessionSummary } from "./types";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

export async function listSessions(): Promise<SessionSummary[]> {
  return json(await fetch("/api/sessions"));
}

export async function createSession(title?: string): Promise<SessionSummary> {
  return json(
    await fetch("/api/sessions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ title: title ?? null }),
    }),
  );
}

export function wsUrl(sessionId: string): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws/${sessionId}`;
}
