export interface EngineCapabilities {
  chat: boolean;
  usage: boolean;
  clear: boolean;
}

export interface PreflightResult {
  ok: boolean;
  reason?: string;
  error?: string;
  command_path?: string;
  executable?: string;
  project_path?: string;
}

export interface Workspace {
  id: number;
  name: string;
  project_path: string;
  command: string;
  engine: string;
  engine_label?: string;
  wake_prompt: string;
  wake_prompt_file: string;
  wake_prompt_source?: "text" | "file";
  budget_tokens: number;
  active: number;
  capabilities?: EngineCapabilities;
  preflight?: PreflightResult;
  created_at?: string;
  updated_at?: string;
}

export interface EngineOption {
  id: string;
  label: string;
  default_command: string;
  chat: boolean;
  usage: boolean;
  clear: boolean;
}

export interface WorkspacesResponse {
  workspaces: Workspace[];
  active_id: number | null;
  active_preflight?: PreflightResult;
  engines: EngineOption[];
  workspace?: Workspace;
}

export interface PtyNotice {
  seq: number;
  kind: string;
  message: string;
  session_id?: string;
}

export interface TokenUsage {
  input_tokens: number;
  output_tokens: number;
  cache_read_input_tokens: number;
  cache_creation_input_tokens: number;
}

export interface Agent {
  role?: string;
  agent_id?: string;
  pid?: number;
  pgid?: number;
  job_id?: string;
  status?: string;
  started_at?: string;
  last_heartbeat?: string;
  command?: string;
  updated_at?: string;
  current_task_id?: string;
  cost_tokens?: number;
  cost_budget?: number | null;
  project?: string;
  alive?: boolean;
  external_session_id?: string | null;
}

/** `/discover` 返回的未注册野生进程（只读展示）。 */
export interface WildProcess {
  pid?: number;
  ppid?: number;
  cwd?: string;
  cmdline?: string;
  create_time?: number;
  source?: string;
  root?: boolean;
  child_pids?: number[];
  bound_role?: string | null;
}

export interface Task {
  task_id?: string;
  from_role?: string;
  to_role?: string;
  intent?: string;
  pointer?: string;
  acceptance?: string[];
  status?: string;
  idempotency_key?: string;
  max_tokens?: number | null;
  max_minutes?: number | null;
  attempts?: number;
  created_at?: string;
  updated_at?: string;
  ack_at?: string;
  completed_at?: string;
  artifacts?: string[];
  cost_tokens?: number;
}

export interface DaemonInfo {
  pid?: number;
  host?: string;
  port?: number;
  started_at?: string;
  project?: string;
}

export interface StatusSnapshot {
  daemon?: DaemonInfo;
  agents?: Agent[];
}

export interface TaskList {
  tasks?: Task[];
}

export interface SSEFrame {
  id: number | null;
  event: string;
  data: string;
}

export type ConnectionState = "connecting" | "open" | "closed" | "error" | "stopped";
