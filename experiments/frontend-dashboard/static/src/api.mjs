// 经代理访问 daemon 的 JSON 客户端（token 由代理注入，前端不接触 token）。

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function requestJSON(path, options = {}) {
  const { method = "GET", body, fetchImpl = globalThis.fetch, signal } = options;
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetchImpl(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  const raw = await response.text();
  let payload = null;
  try {
    payload = raw ? JSON.parse(raw) : null;
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const detail = payload?.error ?? payload?.detail ?? `HTTP ${response.status}`;
    throw new ApiError(response.status, String(detail));
  }
  return payload ?? {};
}

export function fetchStatus(options = {}) {
  return requestJSON("/api/status", options);
}

export function fetchTasks(options = {}) {
  return requestJSON("/api/tasks", options);
}

export function stopRole(role, options = {}) {
  return requestJSON("/api/stop", { ...options, method: "POST", body: { role } });
}
