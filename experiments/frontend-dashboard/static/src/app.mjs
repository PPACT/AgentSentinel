import { fetchStatus, fetchTasks, stopRole } from "./api.mjs";
import { connectionLabel, daemonSummary, errorMessage, formatAgent, formatTask } from "./format.mjs";
import { consumeEventStream, parseEventData } from "./sse.mjs";

const state = { daemon: null, agents: [], tasks: [], connection: "connecting" };

const els = {
  connection: document.getElementById("connection"),
  daemon: document.getElementById("daemon-summary"),
  error: document.getElementById("error-banner"),
  agentsBody: document.getElementById("agents-body"),
  tasksBody: document.getElementById("tasks-body"),
};

function cell(value, className = "") {
  const td = document.createElement("td");
  td.textContent = value;
  if (className) td.className = className;
  return td;
}

function emptyRow(colspan, message) {
  const tr = document.createElement("tr");
  const td = document.createElement("td");
  td.colSpan = colspan;
  td.className = "empty";
  td.textContent = message;
  tr.append(td);
  return tr;
}

function renderConnection() {
  els.connection.textContent = connectionLabel(state.connection);
  els.connection.className = `badge badge-${state.connection}`;
}

function showError(error) {
  els.error.textContent = errorMessage(error);
  els.error.hidden = false;
}

function clearError() {
  els.error.hidden = true;
}

function applySnapshot(snapshot) {
  state.daemon = snapshot?.daemon ?? null;
  state.agents = Array.isArray(snapshot?.agents) ? snapshot.agents : [];
  els.daemon.textContent = daemonSummary(state.daemon);
  renderAgents();
}

function renderAgents() {
  const rows = state.agents.map((agent) => {
    const cells = formatAgent(agent);
    const tr = document.createElement("tr");
    tr.append(
      cell(cells.role),
      cell(cells.pid),
      cell(cells.pgid),
      cell(cells.status),
      cell(cells.alive, agent?.alive ? "status-online" : "status-offline"),
      cell(cells.lastHeartbeat),
      cell(cells.currentTask),
    );
    const action = document.createElement("td");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "停止";
    button.disabled = !agent?.role;
    button.addEventListener("click", () => {
      void stopAgent(agent?.role, button);
    });
    action.append(button);
    tr.append(action);
    return tr;
  });
  els.agentsBody.replaceChildren(...(rows.length ? rows : [emptyRow(8, "暂无 agent")]));
}

function renderTasks() {
  const rows = state.tasks.map((task) => {
    const cells = formatTask(task);
    const tr = document.createElement("tr");
    tr.append(
      cell(cells.taskId),
      cell(cells.fromRole),
      cell(cells.toRole),
      cell(cells.intent),
      cell(cells.status),
      cell(cells.updatedAt),
    );
    return tr;
  });
  els.tasksBody.replaceChildren(...(rows.length ? rows : [emptyRow(6, "暂无任务")]));
}

async function refreshStatus() {
  try {
    applySnapshot(await fetchStatus());
    clearError();
  } catch (error) {
    showError(error);
  }
}

async function refreshTasks() {
  try {
    const data = await fetchTasks();
    state.tasks = Array.isArray(data?.tasks) ? data.tasks : [];
    renderTasks();
  } catch (error) {
    showError(error);
  }
}

async function stopAgent(role, button) {
  if (!role) return;
  button.disabled = true;
  try {
    await stopRole(String(role));
    clearError();
    await refreshTasks();
  } catch (error) {
    showError(error);
  } finally {
    button.disabled = false;
  }
}

let tasksTimer = null;

/** 收到任意 SSE 事件后去抖 250ms 重取 /tasks；不是轮询。 */
function scheduleTasksRefresh() {
  if (tasksTimer !== null) clearTimeout(tasksTimer);
  tasksTimer = setTimeout(() => {
    tasksTimer = null;
    void refreshTasks();
  }, 250);
}

function startStream() {
  const controller = new AbortController();
  window.addEventListener("beforeunload", () => controller.abort());
  void consumeEventStream({
    url: "/api/events",
    signal: controller.signal,
    onFrame(frame) {
      const payload = parseEventData(frame);
      if (payload && (frame.event === "snapshot" || frame.event === "heartbeat")) {
        applySnapshot(payload);
        clearError();
      }
      if (frame.event === "shutdown") {
        state.connection = "closed";
        renderConnection();
      }
      scheduleTasksRefresh();
    },
    onState(next) {
      state.connection = next;
      renderConnection();
    },
    onError(error) {
      showError(error);
    },
  });
}

renderConnection();
renderTasks();
void refreshStatus();
void refreshTasks();
startStream();
