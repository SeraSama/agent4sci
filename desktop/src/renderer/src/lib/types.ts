// Shapes of the backend API (see src/agent4sci/server).

export type MemoryMode = 'global' | 'project' | 'conversation' | 'none'

export interface Project {
  id: string
  name: string
  path: string
  description: string
  domains: string[]
  created: number
  settings: {
    trusted: boolean
    env_passthrough: string[]
    auto_commit: boolean
    default_memory_mode: MemoryMode
    mcp_servers: string[] | null
  }
  environment?: Record<string, any>
  venv?: boolean
  git?: boolean
}

export interface Capabilities {
  tools: boolean
  parallel_tools: boolean
  vision: boolean
  thinking: boolean
  tool_result_images: boolean
  native_web_search: boolean
  native_memory_tool: boolean
  stream_usage: boolean
  echo_reasoning_content: boolean
}

export interface ModelProfile {
  id: string
  name: string
  provider: 'anthropic' | 'openai' | 'openai-compatible' | 'ollama' | 'fake'
  model: string
  base_url: string | null
  api_key_env: string | null
  effort: string | null
  reasoning_effort: string | null
  max_tokens: number | null
  max_tokens_param: 'max_tokens' | 'max_completion_tokens'
  temperature: number | null
  context_window: number
  pricing: { input: number; output: number; cache_read?: number | null; cache_write?: number | null } | null
  capabilities: Capabilities
  extra_headers: Record<string, string>
  extra_body: Record<string, any>
  timeout_s: number
  enabled: boolean
  preset: string | null
  fallbacks: boolean
  key: { stored: boolean; masked: string; env: string | null; resolved: boolean }
  /** false when neither the profile nor the built-in table has prices: costs and budgets can't be tracked */
  pricing_known?: boolean
}

export interface ModelsResponse {
  models: ModelProfile[]
  default: string | null
  roles: Record<string, string>
}

export interface Preset {
  id: string
  label: string
  provider: string
  base_url: string | null
  model: string
  model_hint: string
  api_key_env: string | null
  needs_key: boolean
  context_window: number
  capabilities: Capabilities
  effort: string | null
  docs: string
  pricing_url?: string
}

export type Block =
  | { type: 'text'; text: string }
  | { type: 'image'; media_type: string; data: string; path?: string | null }
  | { type: 'tool_call'; id: string; name: string; input: Record<string, any>; input_error?: string | null }
  | { type: 'tool_result'; call_id: string; name: string; content: Block[]; is_error: boolean }
  | { type: 'reasoning'; text: string }

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: Block[]
  meta: Record<string, any>
  seq?: number
  created?: number
}

export interface Conversation {
  id: string
  title: string
  project_id: string | null
  memory_mode: MemoryMode
  harness_id: string
  permission_mode?: string | null
  model_id: string | null
  temporary: boolean
  archived: boolean
  created: number
  updated: number
  cost_usd: number
  cost_unknown?: boolean
  tokens: Record<string, number>
  status: 'idle' | 'running'
}

export interface Interaction {
  id: string
  kind: 'approval' | 'question' | 'checkpoint'
  title: string
  payload: Record<string, any>
  topics: string[]
  created: number
  status: string
}

export interface HarnessProfile {
  id: string
  name: string
  description: string
  role: string | null
  role_prompt: string
  include_rules: boolean
  model_id: string | null
  effort: string | null
  tool_groups: string[]
  tool_exclude: string[]
  skills_mode: 'all' | 'tags' | 'list' | 'none'
  skill_tags: string[]
  skill_names: string[]
  mcp_servers: string[] | null
  memory_mode: MemoryMode
  approvals: Record<string, 'auto' | 'ask' | 'deny'>
  permission_mode: string
  max_turns: number
  max_tokens: number | null
  max_cost_usd: number | null
  tool_output_limit: number
  compact_ratio: number
  tool_timeout_s: number
  stream_idle_timeout_s: number
  allow_subagents: boolean
  subagent_max_turns: number
  builtin: boolean
}

export interface HarnessPreview {
  model: { id: string; name: string; model: string; provider: string; context_window: number }
  sections: { id: string; label: string; tokens: number; text: string }[]
  total_tokens: number
  tools: { name: string; group: string; description: string; native: boolean }[]
  skills: { name: string; description: string; trust: string; root: string }[]
  memory_scopes: string[]
  mcp: { name: string; ok: boolean; error: string | null; tools: string[] }[]
}

export interface ToolGroup {
  id: string
  label: string
  tools: { name: string; description: string; category: string }[]
}

export interface LoopNode {
  id: string
  type: string
  label: string
  position: { x: number; y: number }
  parent: string | null
  config: Record<string, any>
  width?: number | null
  height?: number | null
}

export interface LoopEdge {
  id: string
  source: string
  target: string
  condition: string | null
  label: string
}

export interface LoopGraph {
  id: string
  name: string
  description: string
  category: string
  version: number
  inputs: Record<string, { label: string; type: string; required: boolean; default: any }>
  nodes: LoopNode[]
  edges: LoopEdge[]
  settings: { max_cost_usd: number | null; memory_mode: MemoryMode | null; model_id: string | null; auto_commit: boolean }
  builtin: boolean
  issues?: Issue[]
}

export interface Issue {
  level: 'error' | 'warning'
  message: string
  node: string | null
  edge: string | null
}

export interface RunSummary {
  id: string
  loop_id: string
  loop_name: string
  project_id: string
  status: 'pending' | 'running' | 'paused' | 'waiting' | 'done' | 'failed' | 'cancelled'
  cost_usd: number
  cost_unknown?: boolean
  tokens?: Record<string, number>
  /** caveats a "done" status would hide (loop condition not met, early wrap-up, unknown prices) */
  warnings?: string[]
  created: number
  updated: number
  error: string | null
  inputs: Record<string, any>
  auto_mode: boolean
}

export interface RunDetail extends RunSummary {
  graph: LoopGraph
  nodes: Record<string, { status: string; key: string; error: string | null; cost: number }>
  records: Record<string, { key: string; node: string; status: string; output: any; error: string | null; started: number; finished: number | null; cost: number; tokens?: Record<string, number> }>
  metrics: Record<string, { iteration: number; value: number; improved: boolean; kept: boolean | null; loop: string }[]>
  waiting: Record<string, any> | null
  pending: Interaction[]
}

export interface SkillItem {
  name: string
  description: string
  root: string
  trust: string
  status: 'active' | 'pending' | 'rejected'
  license: string | null
  tags: string[]
  warnings: string[]
  path: string
  shadowed: string[]
  lock: Record<string, any> | null
}

export interface CatalogEntry {
  source: string
  name: string
  description: string
  path: string
  license: string | null
  license_status: string
  domains: string[]
  trust: string
  spec: string
  installed: boolean
}

export interface MemoryItemInfo {
  file: string
  scope: string
  name: string
  description: string
  type: string
  created: string
  updated: string
  source: string
  flagged: string | null
  body?: string
}

export interface BackendEventData {
  span?: string
  parent_span?: string | null
  [key: string]: any
}
