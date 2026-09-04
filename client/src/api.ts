const BASE = ''

export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try { msg = (await res.json()).error || msg } catch { /* ignore */ }
    throw new Error(msg)
  }
  return res.json()
}

export const getSettings = () => api('/api/settings')
export const saveSettings = (patch: object) =>
  api('/api/settings', { method: 'PUT', body: JSON.stringify(patch) })
export const testLlm = () => api('/api/settings/test-llm', { method: 'POST' })
export const testLlm2 = () => api('/api/settings/test-llm2', { method: 'POST' })
export const testEmbedding = () => api('/api/settings/test-embedding', { method: 'POST' })

export const triggerIngest = () => api('/api/rag/ingest', { method: 'POST' })
export const triggerRepair = () => api('/api/rag/repair-summaries', { method: 'POST' })
export const triggerOntology = () => api<{ job_id: number; resumed?: boolean }>(
  '/api/ontology/extract', { method: 'POST' })
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
export const getChunks = (page: number, pageSize = 20, docId?: number) =>
  api(`/api/rag/chunks?page=${page}&page_size=${pageSize}${docId ? `&doc_id=${docId}` : ''}`)
export const getChunk = (id: number) => api(`/api/rag/chunks/${id}`)
export const deleteDocument = (id: number) =>
  api(`/api/rag/documents/${id}`, { method: 'DELETE' })
export const deleteChunk = (id: number) =>
  api(`/api/rag/chunks/${id}`, { method: 'DELETE' })
export const deleteChunks = (ids: number[]) =>
  api('/api/rag/chunks/batch-delete', { method: 'POST', body: JSON.stringify({ ids }) })
export const search = (query: string, topK = 5) =>
  api('/api/rag/search', { method: 'POST', body: JSON.stringify({ query, top_k: topK }) })

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
export const createIdentity = (name: string, mission: string, seedCandidateIds: number[], description = '') =>
  api<{ ok: boolean; id: number }>('/api/ontology/identities', {
    method: 'POST',
    body: JSON.stringify({ name, mission, description, seed_candidate_ids: seedCandidateIds }),
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

// ---------------- persona chat (数字人对话: 多模型 + 本体约束开关) ----------------

export interface ChatMessage {
  id: number; identity_id: number; role: 'user' | 'assistant'
  content: string; created_at: number
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
  context: ChatContext
}
export interface ChatModels {
  llm: { configured: boolean; model: string }
  llm2: { configured: boolean; model: string }
  ollama: { configured: boolean; models: string[]; base_url: string; error?: string }
}
export const getChatModels = () => api<ChatModels>('/api/chat/models')
export const sendChat = (
  identityId: number, message: string,
  opts: { use_ontology?: boolean; use_rag?: boolean; provider?: string; ollama_model?: string | null } = {},
) =>
  api<ChatReply>('/api/chat', {
    method: 'POST',
    body: JSON.stringify({ identity_id: identityId, message, ...opts }),
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
export const getChatMessages = (identityId: number) =>
  api<{ messages: ChatMessage[] }>(`/api/chat/messages?identity_id=${identityId}`)
export const clearChat = (identityId: number) =>
  api<{ ok: boolean; deleted: number }>(`/api/chat/messages?identity_id=${identityId}`, { method: 'DELETE' })

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
