import "./styles.css";
import { createSession, listSessions } from "./api";
import { el } from "./dom";
import { renderChat } from "./panels/chat";
import { renderRules } from "./panels/rules";
import { renderSchedule } from "./panels/schedule";
import { initialState, transcriptToChat } from "./state";
import type { AppState } from "./state";
import { ChatSocket } from "./ws";
import type { ServerEvent } from "./types";

type Tab = "chat" | "rules" | "schedule";

const state: AppState = initialState();
let socket: ChatSocket | null = null;
let activeTab: Tab = "chat";

const app = document.getElementById("app")!;
const { chatEl, rulesEl, scheduleEl, panelsEl } = buildShell(app);

function buildShell(root: HTMLElement) {
  const brand = el("div", { class: "brand" }, [
    el("span", { class: "brand-mark", text: "▚" }),
    el("span", { class: "brand-name", text: "TourneyDesk" }),
    el("span", { class: "brand-sub", text: "Match Control" }),
  ]);
  const topbar = el("header", { class: "topbar" }, [brand]);

  const tabs: Tab[] = ["chat", "rules", "schedule"];
  const tabButtons = new Map<Tab, HTMLElement>();
  const tabbar = el("nav", { class: "tabbar" });
  for (const t of tabs) {
    const b = el("button", { class: "tab-btn", "data-tab": t, text: label(t) });
    b.addEventListener("click", () => setTab(t));
    tabButtons.set(t, b);
    tabbar.append(b);
  }

  const chatPanel = el("section", { class: "panel panel-chat", "data-tab": "chat" });
  const rulesPanel = el("section", { class: "panel panel-rules", "data-tab": "rules" });
  const schedulePanel = el("section", { class: "panel panel-schedule", "data-tab": "schedule" });
  const panels = el("main", { class: "panels" }, [chatPanel, rulesPanel, schedulePanel]);

  function setTab(t: Tab) {
    activeTab = t;
    panels.setAttribute("data-active", t);
    for (const [k, b] of tabButtons) b.classList.toggle("active", k === t);
  }
  setTab("chat");

  root.append(topbar, tabbar, panels);
  return {
    chatEl: chatPanel,
    rulesEl: rulesPanel,
    scheduleEl: schedulePanel,
    panelsEl: panels,
  };
}

function label(t: Tab): string {
  return { chat: "Chat", rules: "Rules", schedule: "Schedule" }[t];
}

function renderChatPanel() {
  renderChat(chatEl, state, sendMessage);
}
function renderRulesPanel() {
  renderRules(rulesEl, state);
}
function renderSchedulePanel() {
  renderSchedule(scheduleEl, state, renderSchedulePanel);
}
function renderAll() {
  renderChatPanel();
  renderRulesPanel();
  renderSchedulePanel();
}

function sendMessage(text: string) {
  if (!socket) return;
  state.chat.push({ role: "director", text });
  socket.send(text);
  renderChatPanel();
}

function handleEvent(ev: ServerEvent) {
  switch (ev.type) {
    case "session_state":
      state.rules = ev.rules;
      if (ev.transcript?.length) state.chat = transcriptToChat(ev.transcript);
      renderAll();
      break;
    case "user_message":
      // The director's own send already appended it optimistically; skip dupes.
      if (state.chat.at(-1)?.text !== ev.text || state.chat.at(-1)?.role !== "director") {
        state.chat.push({ role: "director", text: ev.text });
        renderChatPanel();
      }
      break;
    case "assistant_delta": {
      const last = state.chat.at(-1);
      if (last && last.role === "agent" && last.streaming) {
        last.text += ev.text;
      } else {
        state.chat.push({ role: "agent", text: ev.text, streaming: true });
      }
      renderChatPanel();
      break;
    }
    case "assistant_message": {
      const last = state.chat.at(-1);
      if (last && last.role === "agent" && last.streaming) {
        last.text = ev.text;
        last.echoes = ev.echoes;
        last.streaming = false;
      } else {
        state.chat.push({ role: "agent", text: ev.text, echoes: ev.echoes });
      }
      renderChatPanel();
      break;
    }
    case "spec_updated":
      state.rules = ev.rules;
      renderRulesPanel();
      break;
    case "solve_started":
      state.solvePhase = "solving";
      renderSchedulePanel();
      break;
    case "solve_completed":
      state.solvePhase = "idle";
      state.schedule = ev.schedule;
      renderSchedulePanel();
      break;
    case "conflict_detected":
      state.schedule = ev.detail;
      renderSchedulePanel();
      break;
    case "error":
      console.error("server error:", ev.message);
      break;
  }
}

async function boot() {
  renderAll();
  // Every visitor gets their OWN conversation: joining an existing session is
  // opt-in via the URL hash (#s=<id>), which we set after creating one so a
  // reload rejoins the same session. Joining sessions[0] unconditionally let
  // concurrent visitors land in each other's conversations.
  const fromHash = new URLSearchParams(location.hash.slice(1)).get("s");
  let session = null;
  if (fromHash) {
    const sessions = await listSessions();
    session = sessions.find((s) => s.id === fromHash) ?? null;
  }
  if (!session) {
    session = await createSession("My tournament");
    history.replaceState(null, "", `#s=${session.id}`);
  }
  state.sessionId = session.id;

  socket = new ChatSocket(
    session.id,
    handleEvent,
    (connected) => {
      state.connected = connected;
      renderChatPanel();
    },
  );
  socket.connect();
}

// Keep the tabbar in sync if the viewport crosses the mobile breakpoint.
window.addEventListener("resize", () => panelsEl.setAttribute("data-active", activeTab));

boot().catch((e) => {
  console.error(e);
  chatEl.append(el("div", { class: "fatal", text: `Could not start: ${e}` }));
});
