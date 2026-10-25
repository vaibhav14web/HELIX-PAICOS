const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

export class ApiError extends Error {
  public status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

export function getAuthToken(): string | null {
  if (typeof window !== 'undefined') {
    const stored = localStorage.getItem('helix_auth_token')
    if (stored) return stored
    const envToken = process.env.NEXT_PUBLIC_HELIX_AUTH_TOKEN
    if (envToken) {
      try {
        localStorage.setItem('helix_auth_token', envToken)
      } catch {}
      return envToken
    }
  }
  return process.env.NEXT_PUBLIC_HELIX_AUTH_TOKEN ?? null
}

export function generateRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `req_${crypto.randomUUID()}`
  }
  return `req_${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36)}`
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const token = getAuthToken()
  const headers: Record<string, string> = { 'Content-Type': 'application/json', ...(options?.headers as Record<string, string>) }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  if (!headers['X-Request-ID']) {
    headers['X-Request-ID'] = generateRequestId()
  }
  const url = `${API_BASE}${path}`
  const res = await fetch(url, {
    headers,
    ...options,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => 'Unknown error')
    throw new ApiError(res.status, text)
  }
  return res.json()
}

// ── Types ──

export interface HealthResponse {
  status: string
  modules: string[]
  state: string
}

export interface ConversationStoreRequest {
  session_id: string
  role: string
  content: string
  companion_id?: string
}


export interface ChatResponse {
  response: string
  session_id: string
  request_id?: string | null
  audio?: string | null
  audio_id?: string | null
}

export interface ConversationEntry {
  session_id: string
  role: string
  content: string
  timestamp: string
  companion_id: string
}

export interface ConversationHistory {
  session_id: string
  entries: ConversationEntry[]
}

export interface SessionRequest {
  session_id: string
}

export interface ProjectRequest {
  session_id: string
  project: string
}

export interface ContextResponse {
  session_id: string
  context: Record<string, unknown>
}

export interface SystemProfile {
  username: string
  hostname: string
}

export interface SystemStats {
  conversations: number
  preferences: number
  explanations: number
  permissions_historical: number
  pending_permissions: number
}

export interface SpeakResponse {
  status: string
  session_id: string
  audio: string | null
  audio_id?: string | null
}

export interface VoiceCommandResponse {
  text: string
  response: string
  session_id: string
  audio: string | null
  audio_id?: string | null
}

export interface VoiceTranscribeResponse {
  text: string
  session_id: string
}

export interface VoiceProfileItem {
  id: string
  name: string
  desc: string
  locale: string
  gender: string
  speed: number
  pitch: number
  model_file: string
  style: string
  preview_text: string
  active?: boolean
  asset_installed?: boolean
  asset_available?: boolean
  description?: string
  fallback_voice_names?: string[]
}

export interface VoiceProfilesResponse {
  status: string
  profiles: VoiceProfileItem[]
  active_profile: string
  active_profile_id?: string
  active_personality_id?: string
  tts_backend: string
}

export interface VoicePreviewResponse {
  status: string
  profile_id: string
  text: string
  preview_text?: string
  audio: string | null
  audio_id?: string | null
}

export interface PermissionRequestPayload {
  action: string
  reasoning: string
  benefits?: string[]
  risks?: string[]
  alternatives?: string[]
  source_module?: string
  resources?: string[]
  scope?: string
  level?: string
  params?: Record<string, unknown>
  target?: string
}

export interface PermissionResponse {
  id: string
  action: string
  status?: string
  reasoning: string
  benefits: string[]
  risks: string[]
  alternatives: string[]
  resources?: string[]
  source_module: string
  created_at: string
  scope?: string
  level?: string
  params?: Record<string, unknown>
  target?: string
  params_hash?: string
  risk_level?: string
  expires_at?: string | null
  decided_at?: string
}

export interface WorkContextResponse {
  session_id: string
  context: Record<string, unknown> | null
}

export interface WorkTasksResponse {
  session_id: string
  tasks: string[]
}

export interface ScratchpadResponse {
  session_id: string
  content: string
}

export interface PreferenceEntry {
  key: string
  value: unknown
  category: string
  source: string
}

export interface PreferencesResponse {
  category: string | null
  entries: PreferenceEntry[]
}

export interface ExplainLogRequest {
  action: string
  reasoning: string
  benefits?: string[]
  risks?: string[]
  alternatives?: string[]
  rationale?: string
  source_module?: string
  outcome?: string
}

export interface StateResponse {
  state: string
}

export interface ProductivityPattern {
  action: string
  category: string
  description: string
  frequency: number
  first_observed: string
  last_observed: string
  suggested: boolean
  automated: boolean
}

export interface ProductivitySuggestion {
  suggestion_id: string
  action: string
  title: string
  description: string
  workflow: Record<string, unknown>
  status: string
  created_at: string
}

export interface LogRecord {
  timestamp: string | null
  level: string
  module: string
  message: string
}

export interface WarmMemoryEntry {
  id: string
  text: string
  category: string
  timestamp: string
  metadata: Record<string, unknown>
  score?: number | null
  project?: string | null
  component?: string | null
  is_stale?: boolean
}

export interface WarmMemoryEntriesResponse {
  entries: WarmMemoryEntry[]
  total_count: number
  next_cursor: string | null
}

export interface WarmMemoryStats {
  tier: string
  vector_count: number
  status: 'ready' | 'stopped'
}

// ── API functions ──

export const api = {
  health: () => request<HealthResponse>('/health'),

  session: {
    start: (session_id: string) => request<{ session_id: string }>('/session/start', {
      method: 'POST',
      body: JSON.stringify({ session_id }),
    }),
    end: (session_id: string) => request<{ session_id: string }>('/session/end', {
      method: 'POST',
      body: JSON.stringify({ session_id }),
    }),
  },

  conversation: {
    store: (payload: ConversationStoreRequest) =>
      request<{ session_id: string; role: string }>('/conversation/store', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
  },

  chat: {
    send: (message: string, session_id = 'default', request_id?: string) => request<ChatResponse>('/chat', {
      method: 'POST',
      body: JSON.stringify({ message, session_id, ...(request_id ? { request_id } : {}) }),
    }),
    stop: (session_id = 'default', request_id?: string) => request<{ status: string; session_id: string; request_id?: string }>('/chat/stop', {
      method: 'POST',
      body: JSON.stringify({ session_id, ...(request_id ? { request_id } : {}) }),
    }),
    history: (session_id: string, limit = 50) =>
      request<ConversationHistory>(`/conversation/${session_id}?limit=${limit}`),
    search: (query: string, limit = 10) =>
      request<{ query: string; results: ConversationEntry[] }>(`/conversation/search?query=${encodeURIComponent(query)}&limit=${limit}`),
    sessions: () => request<{ sessions: string[] }>('/conversation/sessions'),
  },

  files: {
    list: () => request<{ files: Array<{ name: string; filename: string; path: string; size_bytes: number; size: string; modified: string; extension: string }> }>('/files'),
    download: async (filename: string): Promise<Blob> => {
      const token = getAuthToken()
      const headers: Record<string, string> = {}
      if (token) headers['Authorization'] = `Bearer ${token}`
      const res = await fetch(`${API_BASE}/files/${encodeURIComponent(filename)}/download`, { headers })
      if (!res.ok) throw new ApiError(res.status, await res.text())
      return res.blob()
    },
    delete: (filename: string) =>
      request<{ status: string; name: string }>(`/files/${encodeURIComponent(filename)}`, { method: 'DELETE' }),
    upload: async (form: FormData): Promise<{ status: string; name: string; path: string; size: string; size_bytes: number }> => {
      const token = getAuthToken()
      const headers: Record<string, string> = {}
      if (token) headers['Authorization'] = `Bearer ${token}`
      const res = await fetch(`${API_BASE}/files/upload`, { method: 'POST', headers, body: form })
      if (!res.ok) throw new ApiError(res.status, await res.text())
      return res.json()
    },
  },
  notes: {
    list: () => request<{ notes: NoteItem[] }>('/notes'),
    create: (title: string, content: string) =>
      request<{ status: string; title: string; filename: string; size_bytes: number; path: string }>('/notes/create', {
        method: 'POST',
        body: JSON.stringify({ title, content }),
      }),
    delete: (filename: string) =>
      request<{ status: string; title: string; filename: string }>(`/notes/${encodeURIComponent(filename)}`, { method: 'DELETE' }),
  },
  voice: {
    speak: (text: string, session_id = 'default') => request<SpeakResponse>('/speak', {
      method: 'POST',
      body: JSON.stringify({ text, session_id }),
    }),
    command: async (file: Blob, session_id = 'default'): Promise<VoiceCommandResponse> => {
      const form = new FormData()
      form.append('file', file, 'audio.webm')
      form.append('session_id', session_id)
      const token = getAuthToken()
      const headers: Record<string, string> = {}
      if (token) headers['Authorization'] = `Bearer ${token}`
      const res = await fetch(`${API_BASE}/voice/command`, { method: 'POST', headers, body: form })
      if (!res.ok) throw new ApiError(res.status, await res.text())
      return res.json()
    },
    transcribe: async (file: Blob, session_id = 'default'): Promise<VoiceTranscribeResponse> => {
      const form = new FormData()
      form.append('file', file, 'audio.webm')
      form.append('session_id', session_id)
      const token = getAuthToken()
      const headers: Record<string, string> = {}
      if (token) headers['Authorization'] = `Bearer ${token}`
      const res = await fetch(`${API_BASE}/voice/transcribe`, { method: 'POST', headers, body: form })
      if (!res.ok) throw new ApiError(res.status, await res.text())
      return res.json()
    },
    interrupt: () => request<{ status: string }>('/voice/interrupt', {
      method: 'POST',
    }),
    getProfiles: (session_id = 'default') =>
      request<VoiceProfilesResponse>(`/voice/profiles?session_id=${encodeURIComponent(session_id)}`),
    selectProfile: (profile_id: string, session_id = 'default') =>
      request<{ status: string; profile: VoiceProfileItem; session_id: string }>('/voice/profile/select', {
        method: 'POST',
        body: JSON.stringify({ profile_id, session_id }),
      }),
    previewProfile: (profile_id: string) =>
      request<VoicePreviewResponse>('/voice/preview', {
        method: 'POST',
        body: JSON.stringify({ profile_id }),
      }),
  },

  project: {
    set: (session_id: string, project: string) => request<{ session_id: string; project: string }>('/project/set', {
      method: 'POST',
      body: JSON.stringify({ session_id, project }),
    }),
  },

  context: {
    aggregated: (session_id: string, force = false) =>
      request<ContextResponse>(`/context/aggregated/${session_id}?force=${force}`),
  },

  work: {
    addTask: (session_id: string, task: string) => request<{ session_id: string; task: string }>('/work/task/add', {
      method: 'POST',
      body: JSON.stringify({ session_id, task }),
    }),
    context: (session_id: string) => request<WorkContextResponse>(`/work/context/${session_id}`),
    updateContext: (session_id: string, updates: Record<string, unknown>) =>
      request<{ session_id: string }>('/work/context/update', {
        method: 'POST',
        body: JSON.stringify({ session_id, updates }),
      }),
    scratchpad: {
      save: (session_id: string, content: string) =>
        request<{ session_id: string }>('/work/scratchpad/save', {
          method: 'POST',
          body: JSON.stringify({ session_id, content }),
        }),
      get: (session_id: string) => request<ScratchpadResponse>(`/work/scratchpad/${session_id}`),
    },
    tasks: (session_id: string) => request<WorkTasksResponse>(`/work/tasks/${session_id}`),
    sessions: () => request<{ sessions: any[] }>('/work/sessions'),
  },

  preferences: {
    store: (key: string, value: unknown, category?: string, source?: string) =>
      request<{ key: string }>('/preference/store', {
        method: 'POST',
        body: JSON.stringify({ key, value, category, source }),
      }),
    get: (key: string) => request<{ key: string; entry: PreferenceEntry | null }>(`/preference/${key}`),
    getAll: (category?: string) =>
      request<PreferencesResponse>(`/preferences${category ? `?category=${encodeURIComponent(category)}` : ''}`),
  },

  permissions: {
    request: (payload: PermissionRequestPayload) =>
      request<PermissionResponse>('/permission/request', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    grant: (id: string) => request<{ id: string; status: string }>('/permission/grant', {
      method: 'POST',
      body: JSON.stringify({ id }),
    }),
    deny: (id: string) => request<{ id: string; status: string }>('/permission/deny', {
      method: 'POST',
      body: JSON.stringify({ id }),
    }),
    history: () => request<{ history: PermissionResponse[] }>('/permission/history'),
    patterns: () => request<{ patterns: string[] }>('/permission/patterns'),
    pending: () => request<{ pending: PermissionResponse[] }>('/permission/pending'),
    addAutoApprove: (pattern: string) => request<{ pattern: string; status: string }>('/permission/auto-approve', {
      method: 'POST',
      body: JSON.stringify({ pattern }),
    }),
    removeAutoApprove: (pattern: string) => request<{ pattern: string; status: string }>(`/permission/auto-approve?pattern=${encodeURIComponent(pattern)}`, {
      method: 'DELETE',
    }),
  },

  explain: {
    log: (payload: ExplainLogRequest) =>
      request<{ action: string }>('/memory/explain/log', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    query: (payload: { decision_id?: string; action?: string; limit?: number }) =>
      request<{ records: unknown[] }>('/memory/explain/query', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
  },

  warmMemory: {
    stats: () => request<WarmMemoryStats>('/memory/warm/stats'),
    index: (payload: { text: string; category: string; metadata?: Record<string, unknown> }) =>
      request<{ status: string; id: string; category: string }>('/memory/warm/index', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    entries: (params: { limit?: number; cursor?: string; category?: string } = {}) => {
      const query = new URLSearchParams()
      if (params.limit) query.set('limit', String(params.limit))
      if (params.cursor) query.set('cursor', params.cursor)
      if (params.category) query.set('category', params.category)
      return request<WarmMemoryEntriesResponse>(`/memory/warm/entries?${query.toString()}`)
    },
    search: (params: { query: string; category?: string; limit?: number; min_score?: number }) =>
      request<{ query: string; results: WarmMemoryEntry[]; count: number; duration_ms: number }>(
        '/memory/warm/search',
        { method: 'POST', body: JSON.stringify(params) },
      ),
  },

  productivity: {
    patterns: () => request<{ patterns: ProductivityPattern[] }>('/productivity/patterns'),
    suggestions: (status?: string) =>
      request<{ suggestions: ProductivitySuggestion[] }>(
        `/productivity/suggestions${status ? `?status=${encodeURIComponent(status)}` : ''}`
      ),
    accept: (suggestion_id: string) => request<{ status: string }>('/productivity/suggestion/accept', {
      method: 'POST',
      body: JSON.stringify({ suggestion_id }),
    }),
    dismiss: (suggestion_id: string) => request<{ status: string }>('/productivity/suggestion/dismiss', {
      method: 'POST',
      body: JSON.stringify({ suggestion_id }),
    }),
    insights: () =>
      request<{
        focus_score: number
        total_focus_hours: number
        context_switches: number
        categories: Array<{ name: string; percentage: number; hours: number; color: string }>
        timeline: Array<{ time: string; score: number }>
      }>('/productivity/insights'),
  },

  system: {
    profile: () => request<SystemProfile>('/system/profile'),
    stats: () => request<SystemStats>('/stats/system'),
    logs: (level?: string, limit = 100) =>
      request<{ logs: LogRecord[] }>(
        `/logs?limit=${limit}${level ? `&level=${encodeURIComponent(level)}` : ''}`
      ),
    selfState: () => request<Record<string, unknown>>('/system/self_state'),
    worldState: () => request<Record<string, unknown>>('/system/world_state'),
    capabilities: () => request<{ capabilities: Array<{ id: string; name: string; description: string; supported_actions: string[]; risk_level: string; health: string }> }>('/system/capabilities'),
    apps: () => request<{ apps: Array<{ name: string; executable_name: string; executable_path: string; source: string; aliases: string[] }> }>('/system/discovered_apps'),
    projects: () => request<{ projects: Array<{ project_id: string; name: string; root_path: string; is_git_repo: boolean; languages: string[]; frameworks: string[] }> }>('/system/projects'),
    traces: () => request<{ traces: Array<{ trace_id: string; component: string; action: string; duration_ms: number; status: string; timestamp: string }> }>('/system/traces'),
  },

  goals: {
    get: (params?: { includeArchived?: boolean; archivedOnly?: boolean }) => {
      const q = new URLSearchParams()
      if (params?.includeArchived) q.set('include_archived', 'true')
      if (params?.archivedOnly) q.set('archived_only', 'true')
      const qs = q.toString()
      return request<{ goals: Array<{ goal_id: string; title: string; description: string; priority: string; progress_percent: number; status: string; tasks: Array<{ task_id: string; title: string; status: string }> }> }>(`/goals${qs ? `?${qs}` : ''}`)
    },
    create: (data: { title: string; description: string; priority?: string }) =>
      request<unknown>('/goals', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    archive: (goalId: string) =>
      request<{ status: string; goal: any }>(`/goals/${encodeURIComponent(goalId)}/archive`, {
        method: 'POST',
      }),
    restore: (goalId: string) =>
      request<{ status: string; goal: any }>(`/goals/${encodeURIComponent(goalId)}/restore`, {
        method: 'POST',
      }),
    delete: (goalId: string) =>
      request<{ status: string; goal_id: string }>(`/goals/${encodeURIComponent(goalId)}`, {
        method: 'DELETE',
      }),
  },

  reminders: {
    list: () => request<{ reminders: ReminderEntry[] }>('/reminders'),
    add: (data: { text: string; delay_seconds?: number; time?: string; trigger_at?: string; recurring?: boolean }) =>
      request<{ status: string; reminder: ReminderEntry }>('/reminders/add', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    delete: (id: string) =>
      request<{ status: string; reminder_id: string }>(`/reminders/${encodeURIComponent(id)}`, {
        method: 'DELETE',
      }),
  },

  browser: {
    open: (url: string) =>
      request<{ status: string; url: string }>('/browser/open', {
        method: 'POST',
        body: JSON.stringify({ url }),
      }),
    extract: (url: string) =>
      request<{
        status: string
        url: string
        title: string
        description?: string
        text: string
        char_count: number
        word_count: number
      }>('/browser/extract', {
        method: 'POST',
        body: JSON.stringify({ url }),
      }),
    summarize: (url: string, question?: string, saveToMemory?: boolean) =>
      request<{
        status: string
        url: string
        title: string
        summary: string
        question?: string
        saved_to_memory?: boolean
        untrusted_content_notice?: string
      }>('/browser/summarize', {
        method: 'POST',
        body: JSON.stringify({ url, question, save_to_memory: saveToMemory }),
      }),
    getBookmarks: () =>
      request<{ bookmarks: Array<{ id: string; label: string; url: string; icon: string }> }>('/browser/bookmarks'),
    addBookmark: (bookmark: { id?: string; label: string; url: string; icon?: string }) =>
      request<{ status: string; bookmark: { id: string; label: string; url: string; icon: string } }>('/browser/bookmarks', {
        method: 'POST',
        body: JSON.stringify(bookmark),
      }),
    deleteBookmark: (id: string) =>
      request<{ status: string; id: string }>(`/browser/bookmarks/${encodeURIComponent(id)}`, {
        method: 'DELETE',
      }),
    getQuickLinks: () =>
      request<{
        quick_links: Array<{ id: string; label: string; url: string; icon: string; desc?: string; color?: string }>
      }>('/browser/quick-links'),
  },

  automation: {
    getPipelines: () => request<{ pipelines: PipelineItem[] }>('/automation/pipelines'),
    savePipeline: (data: PipelineSaveRequest) =>
      request<{ status: string; pipeline_id: string }>('/automation/pipelines', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    runPipeline: (pipelineId: string) =>
      request<PipelineRunResponse>(`/automation/pipelines/${encodeURIComponent(pipelineId)}/run`, {
        method: 'POST',
      }),
  },

  agents: {
    list: () => request<{ agents: AgentItem[] }>('/agents'),
    deploy: (data: AgentDeployRequest) =>
      request<AgentDeployResponse>('/agents/deploy', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
  },

  aliases: {
    list: () => request<{ aliases: Record<string, string> }>('/aliases'),
    register: (alias: string, executable: string) =>
      request<AliasRegisterResponse>('/aliases/register', {
        method: 'POST',
        body: JSON.stringify({ alias, executable }),
      }),
    delete: (alias: string) =>
      request<{ status: string; alias: string }>(`/aliases/${encodeURIComponent(alias)}`, {
        method: 'DELETE',
      }),
  },

  settings: {
    get: () => request<{ status: string; settings: SettingsStatePayload }>('/settings'),
    update: (data: SettingsStatePayload) =>
      request<{ status: string; settings: SettingsStatePayload }>('/settings', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
  },

  state: () => request<StateResponse>('/state'),
}

export interface PipelineNode {
  id: string
  type?: string
  data?: {
    label?: string
    [key: string]: unknown
  }
  position?: { x: number; y: number }
  [key: string]: unknown
}

export interface PipelineEdge {
  id: string
  source: string
  target: string
  animated?: boolean
  [key: string]: unknown
}

export interface PipelineItem {
  id: string
  name: string
  description?: string | null
  status?: string
  lastRun?: string
  runs?: number
  nodes: PipelineNode[]
  edges: PipelineEdge[]
}

export interface PipelineSaveRequest {
  id: string
  name: string
  description?: string | null
  nodes?: PipelineNode[]
  edges?: PipelineEdge[]
}

export interface PipelineStepResult {
  node_id?: string
  label?: string
  status: string
  timestamp: number
}

export interface PipelineRunResponse {
  status: string
  pipeline_id: string
  runs: number
  executed_steps: number
  step_results: PipelineStepResult[]
}

export interface AgentItem {
  id: string
  name: string
  role: string
  status: string
  capabilities: string[]
  max_tokens?: number
  tasks_completed?: number
  created_at?: number
}

export interface AgentDeployRequest {
  name: string
  role: string
  capabilities?: string[]
  max_tokens?: number
}

export interface AgentDeployResponse {
  status: string
  agent: AgentItem
}

export interface AliasRegisterRequest {
  alias: string
  executable: string
}

export interface AliasRegisterResponse {
  status: string
  alias: string
  executable: string
}

export interface SettingsStatePayload {
  autoStart?: boolean
  rememberHistory?: boolean
  animations?: boolean
  compactMode?: boolean
  soundAlerts?: boolean
  privateMode?: boolean
  shortcutHints?: boolean
  apiKeySaved?: boolean
  browser_history_enabled?: boolean
  browserHistoryEnabled?: boolean
}

export interface ReminderEntry {
  id: string
  text: string
  timestamp?: number
  time?: string
  recurring?: boolean
  triggered?: boolean
  created_at?: number
}

export interface NoteItem {
  title: string
  filename: string
  content: string
  created_at: number
  updated_at: number
  size_bytes: number
  path?: string
}

export interface BrowserBookmarkItem {
  id: string
  label: string
  url: string
  icon: string
}

export interface BrowserQuickLinkItem {
  id: string
  label: string
  url: string
  icon: string
  desc?: string
  color?: string
}

export interface BrowserPageSummary {
  status: string
  url: string
  title: string
  summary: string
  question?: string
  saved_to_memory?: boolean
  untrusted_content_notice?: string
}


