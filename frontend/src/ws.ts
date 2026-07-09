import { wsUrl } from "./api";
import type { ServerEvent } from "./types";

type Handler = (ev: ServerEvent) => void;
type StatusHandler = (connected: boolean) => void;

// Thin WebSocket wrapper: JSON in/out, typed events, and automatic reconnect
// with backoff so a dropped connection recovers without a page reload.
export class ChatSocket {
  private ws: WebSocket | null = null;
  private closed = false;
  private backoff = 500;

  constructor(
    private sessionId: string,
    private onEvent: Handler,
    private onStatus: StatusHandler,
  ) {}

  connect(): void {
    this.ws = new WebSocket(wsUrl(this.sessionId));
    this.ws.onopen = () => {
      this.backoff = 500;
      this.onStatus(true);
    };
    this.ws.onmessage = (m) => {
      try {
        this.onEvent(JSON.parse(m.data) as ServerEvent);
      } catch {
        /* ignore malformed frame */
      }
    };
    this.ws.onclose = () => {
      this.onStatus(false);
      if (this.closed) return;
      setTimeout(() => this.connect(), this.backoff);
      this.backoff = Math.min(this.backoff * 2, 8000);
    };
    this.ws.onerror = () => this.ws?.close();
  }

  send(text: string): void {
    this.ws?.send(JSON.stringify({ type: "chat", text }));
  }

  close(): void {
    this.closed = true;
    this.ws?.close();
  }
}
