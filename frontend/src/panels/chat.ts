import { clear, el } from "../dom";
import type { AppState } from "../state";

// Chat panel: streaming transcript + composer. The agent's tool echoes render
// as small provenance chips beneath its reply so the director can see, inline,
// exactly what got written into the spec.
export function renderChat(root: HTMLElement, state: AppState, onSend: (text: string) => void): void {
  clear(root);

  const header = el("div", { class: "panel-head" }, [
    el("span", { class: "panel-kicker", text: "Conversation" }),
    el("span", {
      class: `conn-dot ${state.connected ? "on" : "off"}`,
      title: state.connected ? "Connected" : "Reconnecting…",
    }),
  ]);

  const log = el("div", { class: "chat-log" });
  if (state.chat.length === 0) {
    log.append(
      el("div", { class: "chat-empty" }, [
        el("p", { class: "chat-empty-title", text: "Describe your tournament" }),
        el("p", {
          class: "chat-empty-sub",
          text: "Teams, fields, dates, any quirks. The rules and a sample schedule fill in as you talk.",
        }),
      ]),
    );
  }
  for (const m of state.chat) {
    const bubble = el("div", { class: `msg msg-${m.role}` });
    bubble.append(el("div", { class: "msg-role", text: m.role === "director" ? "You" : "TourneyDesk" }));
    const body = el("div", { class: "msg-body" });
    body.textContent = m.text || (m.streaming ? "…" : "");
    if (m.streaming) body.append(el("span", { class: "caret" }));
    bubble.append(body);
    if (m.echoes && m.echoes.length) {
      const chips = el("div", { class: "echo-chips" });
      for (const e of m.echoes) chips.append(el("span", { class: "echo-chip", title: e, text: e }));
      bubble.append(chips);
    }
    log.append(bubble);
  }

  const input = el("textarea", {
    class: "composer-input",
    rows: 1,
    placeholder: "Message the scheduler…",
  }) as HTMLTextAreaElement;
  const button = el("button", { class: "composer-send", text: "Send" }) as HTMLButtonElement;

  const submit = () => {
    const v = input.value.trim();
    if (!v) return;
    onSend(v);
    input.value = "";
    input.style.height = "auto";
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  });
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
  });
  button.addEventListener("click", submit);

  const composer = el("div", { class: "composer" }, [input, button]);

  root.append(header, log, composer);
  // Keep the latest message in view.
  log.scrollTop = log.scrollHeight;
}
