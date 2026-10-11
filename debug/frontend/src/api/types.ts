export type JsonPrimitive = null | boolean | number | string
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue }

export type Category =
  | 'mongo'
  | 'redis'
  | 'telegram'
  | 'ai_cache'
  | 'middleware'
  | 'resource'
  | 'log'
  | 'process'
export type Level = 'debug' | 'info' | 'warning' | 'error'
export type Origin = 'startup' | 'update' | 'background' | 'debugger'
export type WorkerState = 'starting' | 'ready' | 'reloading' | 'failed' | 'stopped'

export interface EventSummary {
  schema_version: 1
  seq: number
  session_id: string
  run_id: string
  pid: number
  timestamp: string
  category: Category
  name: string
  phase: 'start' | 'finish' | 'instant'
  level: Level
  trace_id: string | null
  span_id: string | null
  parent_span_id: string | null
  task_id: string | null
  update_id: number | null
  chat_tid: number | null
  duration_ms: number | null
  error_type: string | null
  origin: Origin
  outcome: 'ok' | 'error' | 'cancelled' | null
  summary: string
  truncated: boolean
  redacted: boolean
}

export interface StoredEvent extends EventSummary {
  monotonic_ns: string
  payload: JsonValue
}

export interface EventPage {
  events: EventSummary[]
  next_after: number
  oldest_seq: number | null
  latest_seq: number | null
  gap: boolean
  dropped_total: number
}

export interface Session {
  session_id: string
  run_id: string | null
  state: WorkerState
  restart_required: boolean
  worker_pid: number | null
  sanitized_targets: Record<string, string | number>
  capabilities: Record<string, boolean | string | number>
  dropped_total: number
  recorder_errors: number
  oldest_seq: number | null
  latest_seq: number | null
  event_count: number
  event_bytes: number
  limits: Record<string, number>
  csrf_token: string
  sensitive: true
  detail: string | null
}

export interface ResourceResponse {
  run_id: string | null
  worker: ResourceSample[]
  collector: ResourceSample[]
  vite: ResourceSample[]
}

export interface ResourceSample {
  timestamp?: string
  monotonic_ns?: string
  cpu_percent?: number | null
  rss?: number | null
  rss_bytes?: number | null
  threads?: number
  open_fds?: number | null
  event_loop_lag_ms?: number | null
  active_tasks?: number
  [key: string]: JsonValue | undefined
}

export interface ApiErrorBody {
  error: { code: string; message: string; request_id: string; status_url?: string | null }
}

export interface PreparedAction {
  action_id: string
  run_id: string
  target: JsonValue
  operation: string
  preview: JsonValue
  warnings: string[]
  expires_at: string
  confirmation_text: string
}

export type ActionState = 'prepared' | 'running' | 'succeeded' | 'failed' | 'unknown' | 'expired'
export interface ActionStatus {
  action_id: string
  run_id: string
  state: ActionState
  operation: string
  target: JsonValue
  expires_at: string
  result: JsonValue | null
  error: JsonValue | null
  status_url?: string
}

export interface MongoQueryResponse {
  items: JsonValue[]
  has_more: boolean
  duration_ms: number
  truncated: boolean
  run_id: string
}

export interface RedisQueryResponse {
  type: string
  ttl: number | null
  data: JsonValue
  next_cursor: number
  has_more: boolean
  truncated: boolean
  duration_ms: number
  run_id: string
  length?: number | null
}

export interface AiCacheResponse {
  kind: 'messages' | 'tools' | 'pricing'
  key: string
  ttl: number
  configured_ttl_seconds?: number
  count?: number
  entries?: JsonValue[]
  invalid_entries?: JsonValue[]
  truncated?: boolean
  run_id: string
  offset?: number | null
  next_cursor?: number | null
  has_more?: boolean
  replay_status?: 'not_evaluated' | null
  provider_prompt_cache?: 'not_reported'
  separate_response_cache?: false
  media_transcription_cache?: false
  value?: JsonValue
  missing?: boolean
  invalid_entry?: JsonValue
}

export interface SearchFilters {
  run?: string
  trace?: string
  chat?: number
  level?: Level
  from?: string
  to?: string
  event?: number
  q?: string
  errors?: boolean
  polling?: boolean
}
