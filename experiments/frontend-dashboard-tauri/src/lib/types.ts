export interface Agent {
  role?: string;
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
  alive?: boolean;
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
