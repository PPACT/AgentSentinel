// 纯格式化函数：无 DOM 依赖，便于 Node 内置测试。

function text(value) {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

export function formatAgent(agent) {
  return {
    role: text(agent?.role),
    pid: text(agent?.pid),
    pgid: text(agent?.pgid),
    status: text(agent?.status),
    alive: agent?.alive ? "在线" : "已停止",
    lastHeartbeat: text(agent?.last_heartbeat),
    currentTask: text(agent?.current_task_id),
  };
}

export function formatTask(task) {
  return {
    taskId: text(task?.task_id),
    fromRole: text(task?.from_role),
    toRole: text(task?.to_role),
    intent: text(task?.intent),
    status: text(task?.status),
    updatedAt: text(task?.updated_at),
  };
}

export function daemonSummary(daemon) {
  if (!daemon) return "中控未启动";
  const host = text(daemon.host);
  const port = text(daemon.port);
  return `pid ${text(daemon.pid)} · ${host}:${port} · 启动于 ${text(daemon.started_at)}`;
}

const STATE_LABELS = {
  connecting: "连接中…",
  open: "已连接（SSE）",
  closed: "连接已关闭，正在重连…",
  error: "连接中断，正在重连…",
};

export function connectionLabel(state) {
  return STATE_LABELS[state] ?? "未知状态";
}

export function errorMessage(error) {
  if (error?.status === 401) {
    return "未授权（401）：token 缺失或不匹配，请检查 .claude/orchestrator/token";
  }
  if (error?.status === 503) {
    return "中控未启动：请运行 scripts\\start_orchestrator.cmd 或 -Action start";
  }
  return `请求失败：${error?.message ?? "未知错误"}`;
}

export function isDaemonDown(error) {
  return error?.status === 503;
}
