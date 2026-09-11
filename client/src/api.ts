const BASE = ''
const TOKEN_KEY = 'rag_token'

export const getToken = () => localStorage.getItem(TOKEN_KEY) || ''
export const setToken = (t: string) => {
  if (t) localStorage.setItem(TOKEN_KEY, t)
  else localStorage.removeItem(TOKEN_KEY)
}

export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(options.headers as Record<string, string> | undefined) }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (!(options.body instanceof FormData)) headers['Content-Type'] = 'application/json'
  const res = await fetch(BASE + path, { ...options, headers })
  if (res.status === 401) {
    setToken('')
    window.dispatchEvent(new CustomEvent('auth:logout'))
  }
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try { msg = (await res.json()).detail || msg } catch { /* ignore */ }
    throw new Error(msg)
  }
  return res.json()
}

// ---------------- auth ----------------

export const login = (username: string, password: string) =>
  api<{ token: string; username: string; role: string }>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
export const changePassword = (old_password: string, new_password: string) =>
  api<{ ok: boolean }>('/api/auth/password', {
    method: 'POST',
    body: JSON.stringify({ old_password, new_password }),
  })
export const logout = () => api<{ ok: boolean }>('/api/auth/logout', { method: 'POST' })

// ---------------- mcp sandbox ----------------

export interface McpServer {
  id: number; name: string; description: string; transport: string
  image: string; command: string; port: number; status: string; created_at: number
  approval_status?: string; tools?: {
    name: string; description?: string; input_schema?: Record<string, unknown>
  }[]
  source_path?: string
  called_by?: {
    id: number; name: string; mcp_tool_name: string; status: string
    persona_id: number; persona_name: string
  }[]
}
export const getMcpServers = () => api<{ servers: McpServer[] }>('/api/mcp/servers')
export const createMcpServer = (body: object) =>
  api<{ ok: boolean; id: number }>('/api/mcp/servers', { method: 'POST', body: JSON.stringify(body) })
export const deleteMcpServer = (id: number) => api(`/api/mcp/servers/${id}`, { method: 'DELETE' })
export const startMcpServer = (id: number) => api<{ ok: boolean }>(`/api/mcp/servers/${id}/start`, { method: 'POST' })
export const stopMcpServer = (id: number) => api<{ ok: boolean }>(`/api/mcp/servers/${id}/stop`, { method: 'POST' })
export const scanMcpServers = () =>
  api<{ scanned: number; results: { file: string; ok: boolean; msg: string; server_id?: number }[] }>('/api/mcp/scan', { method: 'POST' })
export const approveMcpServer = (id: number, approve: boolean) =>
  api<{ ok: boolean; approval_status: string }>(`/api/mcp/servers/${id}/approve`, { method: 'POST', body: JSON.stringify({ approve }) })

export const getSettings = () => api('/api/settings')
export const saveSettings = (patch: object) =>
  api('/api/settings', { method: 'PUT', body: JSON.stringify(patch) })
export const testLlm = () => api('/api/settings/test-llm', { method: 'POST' })
export const testLlm2 = () => api('/api/settings/test-llm2', { method: 'POST' })
export const testEmbedding = () => api('/api/settings/test-embedding', { method: 'POST' })

// --- Local LLM (WSL2 Ollama) ---
export interface OllamaModelsResp {
  ok: boolean
  base_url: string
  models: string[]
  selected: string
  selected_installed: boolean
  error?: string
}
export interface OllamaPullStatus {
  state: 'idle' | 'running' | 'done' | 'error'
  model: string
  received: number
  total: number
  error: string | null
  started_at: number
  finished_at: number
}
export const listOllamaModels   = () => api<OllamaModelsResp>('/api/ollama/models')
export const pullOllamaModel    = (model: string) =>
  api<{ ok: boolean; model: string; state: string; error?: string }>(
    '/api/ollama/pull', { method: 'POST', body: JSON.stringify({ model }) })
export const getOllamaPullStatus = () => api<OllamaPullStatus>('/api/ollama/pull-status')
export const setLocalLlm = (cfg: { base_url: string; model: string }) =>
  api<{ ok: boolean }>('/api/settings/local-llm',
    { method: 'PUT', body: JSON.stringify(cfg) })
export const setLlmMode = (mode: 'cloud' | 'local') =>
  api<{ ok: boolean; mode: string }>('/api/settings/llm-mode',
    { method: 'PUT', body: JSON.stringify({ mode }) })

export const triggerIngest = () => api<{ job_id: number }>('/api/rag/ingest', { method: 'POST' })
export const triggerRepair = () => api('/api/rag/repair-summaries', { method: 'POST' })
export const triggerOntology = () => api<{ job_id: number; resumed?: boolean }>(
  '/api/ontology/extract', { method: 'POST' })
export const triggerPipeline = (opts: { identity_id?: number | null } = {}) =>
  api<{ job_id: number }>('/api/ontology/pipeline', {
    method: 'POST', body: JSON.stringify(opts),
  })
export interface UploadConflict { name: string; doc_id: number; reason: string }
export interface UploadResult {
  job_id: number
  added: string[]
  skipped: string[]
  conflicts: UploadConflict[]
  errors: { name: string; error: string }[]
}
/** Send files (drop-zone + webkitdirectory both feed this).
 *  First call: pass overwriteNames=[] -> the server adds new files and
 *  returns the unresolved conflicts (the dialog then asks the user).
 *  Second call: pass overwriteNames=[...confirmed names] -> the server
 *  replaces those existing documents in place. */
export const uploadFiles = (
  files: File[],
  overwriteNames: string[] = [],
): Promise<UploadResult> => {
  const fd = new FormData()
  for (const f of files) fd.append('files', f, f.name)
  fd.append('overwrite_names', overwriteNames.join(','))
  return api<UploadResult>('/api/rag/upload-files', { method: 'POST', body: fd })
}
export const getJobs = () => api('/api/jobs')
export const pauseJob = (id: number) => api(`/api/jobs/${id}/pause`, { method: 'POST' })
export const resumeJob = (id: number) => api(`/api/jobs/${id}/resume`, { method: 'POST' })
export const deleteJob = (id: number) => api(`/api/jobs/${id}`, { method: 'DELETE' })
export const getJobEvents = (id: number) => api(`/api/events?job_id=${id}`)
export const getStats = () => api('/api/rag/stats')
export const getDocuments = () => api('/api/rag/documents')
export interface ChunkFilters {
  type?: string
  confidence?: string
  mandatory?: number
}

export const getChunks = (page: number, pageSize = 20, docId?: number,
  filters?: ChunkFilters) => {
  const qs = [`page=${page}`, `page_size=${pageSize}`]
  if (docId) qs.push(`doc_id=${docId}`)
  if (filters?.type) qs.push(`type=${encodeURIComponent(filters.type)}`)
  if (filters?.confidence) qs.push(`confidence=${encodeURIComponent(filters.confidence)}`)
  if (filters?.mandatory !== undefined && filters?.mandatory !== null)
    qs.push(`mandatory=${filters.mandatory}`)
  return api(`/api/rag/chunks?${qs.join('&')}`)
}
export const getChunk = (id: number) => api(`/api/rag/chunks/${id}`)
export const deleteDocument = (id: number) =>
  api(`/api/rag/documents/${id}`, { method: 'DELETE' })
export const deleteChunk = (id: number) =>
  api(`/api/rag/chunks/${id}`, { method: 'DELETE' })
export const deleteChunks = (ids: number[]) =>
  api('/api/rag/chunks/batch-delete', { method: 'POST', body: JSON.stringify({ ids }) })
export const search = (query: string, topK = 5,
  filters?: ChunkFilters & { tag?: string }) =>
  api('/api/rag/search', {
    method: 'POST',
    body: JSON.stringify({ query, top_k: topK, ...(filters || {}) }),
  })

// ---------------- chunk 内容类型体系（design §11） ----------------

export interface ChunkType {
  id: number
  code: string
  label: string
  description: string | null
  default_confidence: string
  default_mandatory: number
  priority: number
  builtin: boolean
  status: string
  mandatory_label?: string
  confidence_label?: string
}

export const getChunkTypes = (activeOnly = false) =>
  api(`/api/rag/types${activeOnly ? '?active_only=true' : ''}`)
export const createChunkType = (
  body: Partial<ChunkType> & { code: string; label: string }) =>
  api('/api/rag/types', { method: 'POST', body: JSON.stringify(body) })
export const updateChunkType = (id: number, body: Partial<ChunkType>) =>
  api(`/api/rag/types/${id}`, { method: 'PUT', body: JSON.stringify(body) })
export const deleteChunkType = (id: number) =>
  api(`/api/rag/types/${id}`, { method: 'DELETE' })
export const setChunkType = (chunkId: number, type: string) =>
  api(`/api/rag/chunks/${chunkId}/type`,
    { method: 'POST', body: JSON.stringify({ type }) })

export const getGraph = () => api('/api/ontology/graph')
export const getCandidate = (id: number) => api(`/api/ontology/candidates/${id}`)
export const setStatus = (id: number, status: string) =>
  api(`/api/ontology/candidates/${id}/status`, { method: 'POST', body: JSON.stringify({ status }) })
export const mergeCandidate = (id: number, into: number) =>
  api(`/api/ontology/candidates/${id}/merge`, { method: 'POST', body: JSON.stringify({ into }) })
export const deleteCandidates = (ids: number[]) =>
  api('/api/ontology/candidates/batch-delete', { method: 'POST', body: JSON.stringify({ ids }) })

// ---------------- identity pre-screening (anchors) ----------------

export interface IdentityAnchor {
  id: number; identity_id: number; name: string
  type: string | null; definition: string | null; status: string
}
export interface Identity {
  id: number; name: string; mission: string | null
  description: string | null; keywords: string[]; status: string
  prompt: string
  category: string    // 'general' 通用数字人 | 'domain_expert' 执行领域专家
  reactive: boolean   // 反应式循环开关（能力型数字人打开）
  anchors: IdentityAnchor[]
}
export const getIdentities = () => api<{ identities: Identity[] }>('/api/ontology/identities')
export const nominateIdentities = () =>
  api<{ job_id: number }>('/api/ontology/identities/nominate', { method: 'POST' })
export const setIdentityStatus = (id: number, status: string) =>
  api(`/api/ontology/identities/${id}/status`, { method: 'POST', body: JSON.stringify({ status }) })
export const chooseIdentity = (id: number) =>
  api<{ approved_id: number; deleted: number }>(`/api/ontology/identities/${id}/choose`, { method: 'POST' })
export const deleteIdentity = (id: number) =>
  api<{ ok: boolean; id: number }>(`/api/ontology/identities/${id}`, { method: 'DELETE' })
export const setAnchorStatus = (id: number, status: string) =>
  api(`/api/ontology/anchors/${id}/status`, { method: 'POST', body: JSON.stringify({ status }) })
export const updateAnchor = (id: number, patch: object) =>
  api(`/api/ontology/anchors/${id}`, { method: 'PUT', body: JSON.stringify(patch) })
export const addAnchor = (identityId: number, patch: object) =>
  api<{ id: number }>('/api/ontology/anchors', { method: 'POST', body: JSON.stringify({ identity_id: identityId, ...patch }) })
export const createIdentity = (
  name: string, mission: string, seedCandidateIds: number[],
  description = '', prompt = '', category = 'domain_expert',
) =>
  api<{ ok: boolean; id: number }>('/api/ontology/identities', {
    method: 'POST',
    body: JSON.stringify({ name, mission, description, seed_candidate_ids: seedCandidateIds, prompt, category }),
  })
export const updateIdentity = (id: number, patch: object) =>
  api<{ ok: boolean }>(`/api/ontology/identities/${id}`, { method: 'PUT', body: JSON.stringify(patch) })
export const runExam = () =>
  api<{ job_id: number }>('/api/ontology/exam/run', { method: 'POST' })

// ---------------- orchestration (二次编排) ----------------

export interface OrchItem {
  id: number; batch_id: number; candidate_id: number
  action: 'delete' | 'merge' | 'keep'
  category: 'core' | 'marginal' | 'irrelevant' | null
  reason: string | null; merge_into: number | null
  source: 'rule' | 'glm'
  cand_name: string | null; cand_status: string | null
}
export interface OrchSummary {
  batches: { id: number; source: string; status: string; created_at: number }[]
  items: OrchItem[]
  total: number; truncated: boolean
  by_source: Record<string, number>
  by_action: Record<string, number>
  by_category: Record<string, number>
}
export const runOrchestrate = () =>
  api<{ job_id: number }>('/api/ontology/orchestrate', { method: 'POST' })
export const getOrchestration = () => api<OrchSummary>('/api/ontology/orchestration')
export const confirmOrchestration = () =>
  api<{ ok: boolean; deleted: number; merged: number; kept: number; failed: number }>(
    '/api/ontology/orchestration/confirm', { method: 'POST' })
export const discardOrchestration = () =>
  api<{ ok: boolean; batches: number; items: number }>(
    '/api/ontology/orchestration/discard', { method: 'POST' })
export const dismissOrchItem = (id: number) =>
  api(`/api/ontology/orchestration/items/${id}/dismiss`, { method: 'POST' })

// ---------------- assembly (数字人本体装配) ----------------

export interface AsmItem {
  id: number; batch_id: number; candidate_id: number
  action: 'adopt' | 'exclude'
  category: 'core' | 'marginal' | 'irrelevant' | null
  reason: string | null
  cand_name: string | null; cand_definition: string | null
  cand_status: string | null; mentions: number | null
}
export interface AsmSummary {
  batches: {
    id: number; identity_id: number; status: string
    created_at: number; identity_name: string | null
  }[]
  items: AsmItem[]
  total: number; truncated: boolean
  by_action: Record<string, number>
  by_category: Record<string, number>
  restorable: number
}
export interface PersonaOntItem {
  id: number; identity_id: number; kind: string; name: string
  definition: string | null; source_candidate_id: number | null
  status: string; created_at: number; identity_name: string | null
}
export const runAssemble = (identityId: number) =>
  api<{ job_id: number }>('/api/ontology/assemble', { method: 'POST', body: JSON.stringify({ identity_id: identityId }) })
export const getAssembly = (identityId?: number) =>
  api<AsmSummary>(`/api/ontology/assembly${identityId != null ? `?identity_id=${identityId}` : ''}`)
export const confirmAssembly = (identityId?: number) =>
  api<{ ok: boolean; adopted: number; excluded: number; skipped: number; failed: number }>(
    '/api/ontology/assembly/confirm', { method: 'POST', body: JSON.stringify({ identity_id: identityId ?? null }) })
export const discardAssembly = (identityId?: number) =>
  api<{ ok: boolean; batches: number; items: number }>(
    '/api/ontology/assembly/discard', { method: 'POST', body: JSON.stringify({ identity_id: identityId ?? null }) })
export const restoreAssembly = (identityId?: number) =>
  api<{ ok: boolean; batch_id: number; items: number; identity_id: number }>(
    '/api/ontology/assembly/restore', { method: 'POST', body: JSON.stringify({ identity_id: identityId ?? null }) })
export const dismissAsmItem = (id: number) =>
  api(`/api/ontology/assembly/items/${id}/dismiss`, { method: 'POST' })
export const getPersonaOntology = (identityId?: number) =>
  api<PersonaOntItem[]>(
    `/api/ontology/persona-ontology${identityId != null ? `?identity_id=${identityId}` : ''}`)

export interface PersonaMcp {
  id: number; name: string; description: string; mcp_tool_name: string
  mcp_server_id?: number
  status: string; server_name: string | null; server_approval: string | null
}
export const getIdentityMcp = (identityId: number) =>
  api<{ mcp: PersonaMcp[] }>(`/api/ontology/identities/${identityId}/mcp`)
export const syncIdentityMcp = (identityId: number) =>
  api<{ synced: number; tools: string[] }>(`/api/ontology/identities/${identityId}/sync-mcp`, { method: 'POST' })
export const bindIdentityMcp = (identityId: number, body: {
  mcp_server_id: number; mcp_tool_name: string; description?: string
}) =>
  api<{ ok: boolean; action_id?: number; existed?: boolean; name?: string; error?: string }>(
    `/api/ontology/identities/${identityId}/bind-mcp`,
    { method: 'POST', body: JSON.stringify(body) })
export const unbindIdentityMcp = (identityId: number, action_id: number) =>
  api<{ ok: boolean }>(`/api/ontology/identities/${identityId}/unbind-mcp`,
    { method: 'POST', body: JSON.stringify({ action_id }) })

export const getAvailableMcp = () =>
  api<{ mcp: {
    id: number; name: string; description: string; transport: string
    image: string; tools: { name: string; description?: string; input_schema?: Record<string, unknown> }[]
  }[] }>('/api/mcp/available')

// ---------------- persona chat (数字人对话: 多模型 + 本体约束开关) ----------------

export interface ChatMessage {
  id: number; identity_id: number; role: 'user' | 'assistant'
  content: string; created_at: number; session_id?: number | null
  identity_name?: string | null
}
export interface ChatRoute {
  identity_id: number; identity_name: string; score: number; matched: string[]
}
export interface ChatSession {
  id: number; identity_id: number; identity_name?: string | null
  title: string; created_at: number; message_count: number
}
export interface ChatContext {
  provider: string
  model: string
  use_ontology: boolean
  use_rag: boolean
  rag: { used: boolean; hits: number; error: string | null }
  anchors: { name: string; definition: string }[]
  ontology: { name: string; definition: string }[]
  relations: { source: string; type: string; target: string }[]
  counts: { anchors: number; ontology: number; relations: number }
  injected_ontology: number
  injected_relations: number
  retrieval: {
    total_ontology: number
    total_relations: number
    query_hits: number
    fallback: boolean
    expanded: number
    depth_reached: number
    llm_fallback: { used: boolean; concepts: string[]; hits: number }
    budget_total: number
    budget_used: number
    truncated: boolean
  }
  truncated: boolean
  sent: SentMessage[]
  usage: ChatUsage
}
export interface SentMessage {
  role: string
  content: string
}
export interface ChatUsage {
  prompt_tokens: number
  completion_tokens: number | null
  total_tokens: number | null
  estimate: boolean
  sent_chars: number
  context_window: number
  model_context_length: number | null
  percent: number
}
export interface ChatReply {
  ok: boolean; reply: string; messages: ChatMessage[]
  context: ChatContext; session_id?: number
}
export interface ChatModels {
  llm: { configured: boolean; model: string }
  llm2: { configured: boolean; model: string }
  ollama: { configured: boolean; models: string[]; base_url: string; error?: string }
}
export const getChatModels = () => api<ChatModels>('/api/chat/models')
export const routeChat = (message: string) =>
  api<{ route: ChatRoute | null }>('/api/chat/route', {
    method: 'POST', body: JSON.stringify({ message }),
  })
export const sendChat = (
  identityId: number, message: string,
  opts: { use_ontology?: boolean; use_rag?: boolean; provider?: string; ollama_model?: string | null; session_id?: number | null } = {},
) =>
  api<ChatReply>('/api/chat', {
    method: 'POST',
    body: JSON.stringify({ identity_id: identityId, message, ...opts }),
  })

/** 流式对话回调：每个阶段都会按到达顺序触发。详见 chat.stream_answer 文档。
 * - onSession：后端若自动创建 session（session_id 未传）会触发一次
 * - onToken：LLM 每生成一个 content delta 触发一次，UI 应实时追加到气泡
 * - onDone：流正常结束，data 含 reply/messages/context/session_id，可选地
 *   用 messages 替换前端的临时气泡
 * - onError：LLM 报错、网络断、HTTP 4xx/5xx 都会触发，UI 应把错误写到气泡里
 */
export interface StreamChatCallbacks {
  onSession?: (session_id: number) => void
  onToken?: (text: string) => void
  onDone?: (data: { reply: string; messages: ChatMessage[]; context?: any; session_id: number }) => void
  onError?: (error: string) => void
}

export async function streamChat(
  identityId: number,
  message: string,
  opts: { use_ontology?: boolean; use_rag?: boolean; provider?: string; ollama_model?: string | null; session_id?: number | null } = {},
  cb: StreamChatCallbacks,
): Promise<void> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  let res: Response
  try {
    res = await fetch('/api/chat/stream', {
      method: 'POST', headers,
      body: JSON.stringify({ identity_id: identityId, message, ...opts }),
    })
  } catch (e: any) {
    cb.onError?.(`请求失败：${e?.message || e}`)
    return
  }
  if (res.status === 401) {
    setToken('')
    window.dispatchEvent(new CustomEvent('auth:logout'))
  }
  if (!res.ok || !res.body) {
    let msg = `HTTP ${res.status}`
    try {
      const data = await res.json()
      msg = data.detail || data.error || msg
    } catch { /* ignore */ }
    cb.onError?.(msg)
    return
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buf = ''
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      // SSE 事件按 `\n\n` 分隔；同一事件可能跨多个 chunk 到达
      let idx: number
      while ((idx = buf.indexOf('\n\n')) !== -1) {
        const raw = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        // 多行 `data:` 聚合（标准 SSE 允许多行）
        const dataLines = raw.split('\n')
          .filter((l) => l.startsWith('data:'))
          .map((l) => l.slice(5).trim())
        if (!dataLines.length) continue
        const data = dataLines.join('\n')
        let evt: any
        try { evt = JSON.parse(data) } catch { continue }
        if (evt.event === 'session' && typeof evt.session_id === 'number') {
          cb.onSession?.(evt.session_id)
        } else if (evt.event === 'token' && typeof evt.text === 'string') {
          cb.onToken?.(evt.text)
        } else if (evt.event === 'done') {
          cb.onDone?.(evt)
        } else if (evt.event === 'error') {
          cb.onError?.(evt.error || '未知错误')
        }
      }
    }
  } catch (e: any) {
    cb.onError?.(`流中断：${e?.message || e}`)
  }
}

export const generateMcp = (request: string) =>
  api<{ ok: boolean; name?: string; server_id?: number; tools?: string[]; approval_status?: string; error?: string }>('/api/mcp/generate', {
    method: 'POST', body: JSON.stringify({ request }),
  })

export const generatePipeline = (request: string) =>
  api<{ ok: boolean; pipeline_id?: number; name?: string; status?: string; nodes?: number; relations?: number; error?: string }>('/api/pipeline/generate', {
    method: 'POST', body: JSON.stringify({ request }),
  })

export interface CompareSide {
  reply: string
  context: ChatContext
}
export interface CompareResult {
  ok: boolean
  left: CompareSide
  right: CompareSide
}
export interface CompareArmOpts {
  use_ontology: boolean
  use_rag: boolean
}
/** 幻觉 A/B 对比：同一消息跑两遍，左右两臂的「本体约束 / RAG 资料」各自可独立开关。不写入历史。 */
export const compareChat = (
  identityId: number, message: string,
  opts: { provider?: string; ollama_model?: string | null; left?: CompareArmOpts; right?: CompareArmOpts } = {},
) =>
  api<CompareResult>('/api/chat/compare', {
    method: 'POST',
    body: JSON.stringify({ identity_id: identityId, message, ...opts }),
  })
export const getChatMessages = (identityId: number, sessionId?: number | null) =>
  api<{ messages: ChatMessage[] }>(
    `/api/chat/messages?identity_id=${identityId}${sessionId != null ? `&session_id=${sessionId}` : ''}`)
export const clearChat = (identityId: number, sessionId?: number | null) =>
  api<{ ok: boolean; deleted: number }>(
    `/api/chat/messages?identity_id=${identityId}${sessionId != null ? `&session_id=${sessionId}` : ''}`,
    { method: 'DELETE' })

// ---------------- chat sessions (多会话历史) ----------------
export const getChatSessions = (identityId?: number | null) =>
  api<{ sessions: ChatSession[] }>(
    `/api/chat/sessions${identityId != null ? `?identity_id=${identityId}` : ''}`)
export const createChatSession = (identityId: number, title = '') =>
  api<{ ok: boolean; session: ChatSession }>('/api/chat/sessions', {
    method: 'POST', body: JSON.stringify({ identity_id: identityId, title }),
  })
export const renameChatSession = (sessionId: number, title: string) =>
  api<{ ok: boolean }>(`/api/chat/sessions/${sessionId}`, {
    method: 'PATCH', body: JSON.stringify({ title }),
  })
export const deleteChatSession = (sessionId: number) =>
  api<{ ok: boolean; deleted: number }>(`/api/chat/sessions/${sessionId}`, { method: 'DELETE' })

// ---------------- persona benchmark (四组对照测试 + 归因提名 + 版本管理) ----------------

export interface BenchArmStats {
  correct: number; partial: number; wrong: number; refused: number
  accuracy: number; hallucination: number; refusal: number
}
export interface BenchStats {
  arms: Record<string, BenchArmStats>
  margins: { ontology_pp: number; rag_pp: number; total_pp: number }
  questions: number
}
export interface BenchRow {
  id: number; identity_id: number; job_id: number | null
  model: string; judge: string; total: number
  stats: BenchStats | null; conclusion: string | null
  status: 'running' | 'done' | 'failed'; error: string | null
  created_at: number
}
export interface BenchEvidence { quiz_id: number; question: string; reply: string }
export interface BenchChange {
  id: number; identity_id: number; benchmark_id: number
  ontology_id: number | null; name: string
  kind: string | null
  action: 'annotate' | 'update' | 'delete' | 'add'
  suggested_definition: string | null; note: string | null
  reason: string | null; evidence: BenchEvidence[]
  status: 'pending' | 'merged' | 'rejected'
  version_id: number | null; created_at: number
}
export interface BenchVersion {
  id: number; identity_id: number; version: number
  benchmark_id: number | null
  changelog: { name?: string; action?: string; to?: number }[]
  created_at: number
}
export interface BenchSummary {
  benchmark: BenchRow | null
  changes: BenchChange[]
  versions: BenchVersion[]
}
export const runBenchmark = (identityId: number, limit = 15, ollamaModel?: string | null) =>
  api<{ job_id: number }>('/api/benchmark/run', {
    method: 'POST',
    body: JSON.stringify({ identity_id: identityId, limit, ollama_model: ollamaModel ?? null }),
  })
export const getBenchmark = (identityId: number) =>
  api<BenchSummary>(`/api/benchmark?identity_id=${identityId}`)
export const rejectBenchChange = (changeId: number) =>
  api<{ ok: boolean }>(`/api/benchmark/changes/${changeId}/reject`, { method: 'POST' })
export const mergeBenchmark = (identityId: number) =>
  api<{ ok: boolean; version: number; applied: Record<string, number>; snapshot_rows: number }>(
    '/api/benchmark/merge', { method: 'POST', body: JSON.stringify({ identity_id: identityId }) })
export const rollbackBenchmark = (identityId: number, versionId: number) =>
  api<{ ok: boolean; version: number; restored: number; rows: number }>(
    '/api/benchmark/rollback', {
      method: 'POST',
      body: JSON.stringify({ identity_id: identityId, version_id: versionId }),
    })

export interface EventItem {
  seq: number
  job_id: number | null
  type: string
  payload: Record<string, any>
  ts: number
}

// ---------------- pipeline 编排 ----------------

export interface PipelineNode {
  id: number; pipeline_id: number; node_key: string
  persona_id: number | null; kind: string; step_name: string | null
  position_x: number | null; position_y: number | null
}
export interface PipelineRelation {
  id: number; pipeline_id: number; from_node_id: number; to_node_id: number
  relation_type: string; handoff_type: string | null; handoff_schema: string | null
}
export interface Pipeline {
  id: number; name: string; description: string | null
  version: number; status: string; tags: string[]
  entry_node_id: number | null; exit_node_id: number | null
  nodes: PipelineNode[]; relations: PipelineRelation[]
}
export interface PipelineChange {
  id: number; pipeline_id: number; action: string
  payload: Record<string, any>; status: string; reason: string | null
}

export const getPipelines = () => api<{ pipelines: Pipeline[] }>('/api/pipelines')
export const createPipeline = (name: string, description = '', tags: string[] = []) =>
  api<{ ok: boolean; id: number; note?: string }>('/api/pipelines',
    { method: 'POST', body: JSON.stringify({ name, description, tags }) })
export const getPipeline = (id: number) => api<{ pipeline: Pipeline }>(`/api/pipelines/${id}`)
export const updatePipeline = (id: number, patch: object) =>
  api<{ ok: boolean }>(`/api/pipelines/${id}`, { method: 'PUT', body: JSON.stringify(patch) })
export const deletePipeline = (id: number) =>
  api<{ ok: boolean }>(`/api/pipelines/${id}`, { method: 'DELETE' })

export const addPipelineNode = (pipelineId: number, node: object) =>
  api<{ ok: boolean; id: number }>(`/api/pipelines/${pipelineId}/nodes`, { method: 'POST', body: JSON.stringify(node) })
export const updatePipelineNode = (pipelineId: number, nodeId: number, patch: object) =>
  api<{ ok: boolean }>(`/api/pipelines/${pipelineId}/nodes/${nodeId}`, { method: 'PUT', body: JSON.stringify(patch) })
export const removePipelineNode = (pipelineId: number, nodeId: number) =>
  api<{ ok: boolean }>(`/api/pipelines/${pipelineId}/nodes/${nodeId}`, { method: 'DELETE' })
export const addPipelineRelation = (pipelineId: number, rel: object) =>
  api<{ ok: boolean; id: number }>(`/api/pipelines/${pipelineId}/relations`, { method: 'POST', body: JSON.stringify(rel) })
export const removePipelineRelation = (pipelineId: number, relationId: number) =>
  api<{ ok: boolean }>(`/api/pipelines/${pipelineId}/relations/${relationId}`, { method: 'DELETE' })

export const validatePipeline = (id: number) =>
  api<{ ok: boolean; errors: string[] }>(`/api/pipelines/${id}/validate`, { method: 'POST' })
export const approvePipeline = (id: number) =>
  api<{ ok: boolean }>(`/api/pipelines/${id}/approve`, { method: 'POST' })
export const runPipeline = (id: number) =>
  api<{ job_id: number }>(`/api/pipelines/${id}/run`, { method: 'POST' })

export const getPipelineChanges = (id: number) =>
  api<{ changes: PipelineChange[] }>(`/api/pipelines/${id}/changes`)
export const approvePipelineChange = (pipelineId: number, changeId: number) =>
  api<{ ok: boolean }>(`/api/pipelines/${pipelineId}/changes/${changeId}/approve`, { method: 'POST' })
export const rejectPipelineChange = (pipelineId: number, changeId: number) =>
  api<{ ok: boolean }>(`/api/pipelines/${pipelineId}/changes/${changeId}/reject`, { method: 'POST' })
export const chatPipeline = (id: number, message: string) =>
  api<{ ok: boolean; change_id?: number; changes: any[]; note?: string }>(`/api/pipelines/${id}/chat`, { method: 'POST', body: JSON.stringify({ message }) })
