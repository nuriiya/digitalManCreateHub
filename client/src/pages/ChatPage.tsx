import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  getIdentities, getChatModels, getChatMessages, sendChat, clearChat, compareChat,
  getChatSessions, deleteChatSession, renameChatSession,
  type Identity, type ChatMessage, type ChatContext, type ChatModels,
  type CompareSide, type ChatSession,
} from '../api'
import { useToast } from '../Toast'

interface Props {
  refreshKey: number
}

type Provider = 'llm2' | 'llm' | 'ollama'

function dateLabel(d: Date): string {
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yest = new Date(today); yest.setDate(today.getDate() - 1)
  const day = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  if (day.getTime() === today.getTime()) return '今天'
  if (day.getTime() === yest.getTime()) return '昨天'
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
function timeLabel(ts: number): string {
  const d = new Date(ts * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** 数字人对话页：选择一个已批准数字人，与其对话。回答可由 GLM 5.2 / DeepSeek
 * / 本地 Ollama 7B 生成，并可按开关决定是否用该数字人的本体约束（身份 + 锚点 +
 * 本体段 + 关系）硬性圈定。另提供「对比模式」：同一消息分屏跑两遍——左=用本体
 * （并展示发送的全部内容），右=不用本体，用于「有本体约束 vs 无约束」幻觉 A/B。 */
export default function ChatPage({ refreshKey }: Props) {
  const [idents, setIdents] = useState<Identity[]>([])
  const [chatModels, setChatModels] = useState<ChatModels | null>(null)
  const [selId, setSelId] = useState<number | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [renamingId, setRenamingId] = useState<number | null>(null)
  const [renameDraft, setRenameDraft] = useState('')
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [loading, setLoading] = useState(false)
  const [ctx, setCtx] = useState<ChatContext | null>(null)
  const [ctxOpen, setCtxOpen] = useState(false)
  const [provider, setProvider] = useState<Provider>('llm2')
  const [ollamaModel, setOllamaModel] = useState<string | null>(null)
  const [useOntology, setUseOntology] = useState(true)
  const [useRag, setUseRag] = useState(false)
  const [compareMode, setCompareMode] = useState(false)
  // 对比模式左右臂各自独立的开关（本体约束 / RAG 资料）
  const [armLeft, setArmLeft] = useState({ use_ontology: true, use_rag: false })
  const [armRight, setArmRight] = useState({ use_ontology: false, use_rag: false })
  const [cmpLeft, setCmpLeft] = useState<CompareSide | null>(null)
  const [cmpRight, setCmpRight] = useState<CompareSide | null>(null)
  const logRef = useRef<HTMLDivElement>(null)
  const { toast } = useToast()

  const approved = useMemo(() => idents.filter((i) => i.status === 'approved'), [idents])
  const sel = useMemo(() => approved.find((i) => i.id === selId) ?? null, [approved, selId])

  // 消息按日期分组（今天 / 昨天 / 具体日期）
  const grouped = useMemo(() => {
    const groups: { key: string; label: string; items: ChatMessage[] }[] = []
    for (const m of messages) {
      const d = new Date(m.created_at * 1000)
      const key = d.toDateString()
      if (!groups.length || groups[groups.length - 1].key !== key) {
        groups.push({ key, label: dateLabel(d), items: [] })
      }
      groups[groups.length - 1].items.push(m)
    }
    return groups
  }, [messages])

  // prefer the 32k variant (num_ctx pinned to the model max) when present
  const preferredOllama = useCallback((models?: string[]) => {
    if (!models || models.length === 0) return null
    return models.find((x) => x.includes('32k')) ?? models[0]
  }, [])

  const reload = useCallback(() => {
    getIdentities().then((r) => setIdents(r.identities ?? [])).catch(() => { })
    getChatModels().then((m) => {
      setChatModels(m)
      setOllamaModel((cur) => cur ?? preferredOllama(m.ollama?.models))
    }).catch(() => { })
  }, [preferredOllama])
  useEffect(() => { reload() }, [reload, refreshKey])

  // auto-select the first approved persona (and re-select if current vanishes)
  useEffect(() => {
    if (selId === null && approved.length > 0) setSelId(approved[0].id)
    else if (selId !== null && !approved.some((i) => i.id === selId))
      setSelId(approved.length > 0 ? approved[0].id : null)
  }, [approved, selId])

  // auto-select the first Ollama model when switching to ollama (prefer 32k)
  useEffect(() => {
    if (provider === 'ollama' && !ollamaModel && chatModels?.ollama?.models?.length)
      setOllamaModel(preferredOllama(chatModels.ollama.models))
  }, [provider, ollamaModel, chatModels, preferredOllama])

  // load history when the selected persona / session changes (cancelled flag
  // fixes the stale-response race when switching fast)
  useEffect(() => {
    if (selId === null) {
      setMessages([]); setCtx(null); setCmpLeft(null); setCmpRight(null)
      return
    }
    let cancelled = false
    setLoading(true)
    getChatMessages(selId, sessionId)
      .then((r) => { if (!cancelled) setMessages(r.messages ?? []) })
      .catch(() => { })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [selId, sessionId])

  // load sessions for the selected persona; reset sessionId if it vanished
  useEffect(() => {
    if (selId === null) {
      setSessions([]); setSessionId(null)
      return
    }
    let cancelled = false
    getChatSessions(selId).then((r) => {
      if (cancelled) return
      const list = r.sessions ?? []
      setSessions(list)
      setSessionId((cur) => {
        if (cur != null && !list.some((s) => s.id === cur)) return null
        return cur
      })
    }).catch(() => { })
    return () => { cancelled = true }
  }, [selId])

  // auto-scroll to the newest message
  useEffect(() => {
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, sending])

  const select = (id: number) => {
    if (id === selId) return
    setSelId(id)
    setSessionId(null)
    setSessions([])
    setMessages([])
    setCtx(null)
    setCtxOpen(false)
    setCmpLeft(null)
    setCmpRight(null)
    setInput('')
  }

  const providerOptions: { value: Provider; label: string; disabled: boolean }[] = [
    { value: 'llm2', label: `GLM 5.2${chatModels?.llm2?.model ? ` (${chatModels.llm2.model})` : ''}`, disabled: !chatModels?.llm2?.configured },
    { value: 'llm', label: `DeepSeek${chatModels?.llm?.model ? ` (${chatModels.llm.model})` : ''}`, disabled: !chatModels?.llm?.configured },
    { value: 'ollama', label: '本地 Ollama', disabled: !(chatModels?.ollama?.models?.length) },
  ]

  const doSend = async () => {
    const text = input.trim()
    if (!selId || !text || sending) return
    setSending(true)
    setInput('')
    setCtxOpen(false)

    if (compareMode) {
      try {
        const r = await compareChat(selId, text, {
          provider,
          ollama_model: provider === 'ollama' ? ollamaModel : null,
          left: armLeft,
          right: armRight,
        })
        setCmpLeft(r.left)
        setCmpRight(r.right)
      } catch (e: any) {
        toast(e.message, 'err')
        setInput(text)
      } finally {
        setSending(false)
      }
      return
    }

    const optimistic: ChatMessage = {
      id: -1, identity_id: selId, role: 'user', content: text, created_at: Date.now() / 1000,
    }
    setMessages((m) => [...m, optimistic])
    try {
      const r = await sendChat(selId, text, {
        use_ontology: useOntology,
        use_rag: useRag,
        provider,
        ollama_model: provider === 'ollama' ? ollamaModel : null,
        session_id: sessionId,
      })
      setMessages(r.messages ?? [])
      setCtx(r.context ?? null)
      setCtxOpen(true)
      if (r.session_id != null && r.session_id !== sessionId) {
        // 自动创建了新会话：更新当前会话 + 刷新会话列表
        setSessionId(r.session_id)
        getChatSessions(selId).then((s) => setSessions(s.sessions ?? [])).catch(() => { })
      }
    } catch (e: any) {
      toast(e.message, 'err')
      setMessages((m) => m.filter((x) => x.id !== -1))
      setInput(text)
    } finally {
      setSending(false)
    }
  }

  const doClear = async () => {
    if (!selId) return
    if (!confirm(sessionId ? '清空当前会话的全部消息？' : '清空与当前数字人的全部对话记录？')) return
    try {
      await clearChat(selId, sessionId)
      setMessages([])
      setCtx(null)
      setCtxOpen(false)
      setCmpLeft(null)
      setCmpRight(null)
      toast('对话已清空', 'ok')
    } catch (e: any) { toast(e.message, 'err') }
  }

  // ---- 会话操作：新建 / 切换 / 重命名 / 删除 ----
  const newSession = () => {
    setSessionId(null)   // 发下一条消息时自动创建新会话
    setMessages([])
    setCtx(null)
    setCtxOpen(false)
    setCmpLeft(null)
    setCmpRight(null)
  }
  const switchSession = (id: number) => {
    if (id === sessionId) return
    setSessionId(id)
    setCtx(null); setCtxOpen(false); setCmpLeft(null); setCmpRight(null)
  }
  const startRename = (s: ChatSession) => {
    setRenamingId(s.id)
    setRenameDraft(s.title)
  }
  const commitRename = async () => {
    const id = renamingId
    if (id == null) return
    const title = renameDraft.trim()
    setRenamingId(null)
    if (!title) return
    try {
      await renameChatSession(id, title)
      setSessions((list) => list.map((s) => (s.id === id ? { ...s, title } : s)))
    } catch (e: any) { toast(e.message, 'err') }
  }
  const removeSession = async (id: number) => {
    if (!confirm('删除该会话及其全部消息？')) return
    try {
      await deleteChatSession(id)
      setSessions((list) => list.filter((s) => s.id !== id))
      if (sessionId === id) {
        setSessionId(null)
        setMessages([]); setCtx(null); setCtxOpen(false)
      }
    } catch (e: any) { toast(e.message, 'err') }
  }

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      doSend()
    }
  }

  const providerWarn = (() => {
    if (provider === 'llm2' && chatModels && !chatModels.llm2.configured)
      return '⚠ GLM 5.2 未配置——请到「设置」页填写 GLM token。'
    if (provider === 'llm' && chatModels && !chatModels.llm.configured)
      return '⚠ DeepSeek 未配置——请到「设置」页填写 DeepSeek token。'
    if (provider === 'ollama' && chatModels && !(chatModels.ollama.models?.length))
      return '⚠ 本地 Ollama 暂无模型——请先执行 ollama pull 拉取一个模型。'
    return null
  })()

  return (
    <div className="card">
      <h3>
        数字人测试（调试）
        <span className="note" style={{ marginLeft: 8 }}>
          可选 GLM 5.2 / DeepSeek / 本地 Ollama 7B · 本体约束 / RAG 资料各自可开关（幻觉 A/B 对比）
        </span>
      </h3>
      <div className="desc">
        对话时模型以所选数字人的身份、使命、锚点与已装配本体段为知识边界回答；「本体约束」关掉后不再被本体圈定。「RAG 资料」开时会在回答中加入检索到的原文片段（可与本体叠加）。「对比模式」分屏跑两遍，左右两臂的「本体约束 / RAG 资料」各自独立开关。
      </div>

      {providerWarn && (
        <div className="warn" style={{ marginBottom: 12 }}>{providerWarn}</div>
      )}

      <div className="chat-wrap">
        <div className="chat-side">
          <div className="note" style={{ marginBottom: 2 }}>选择数字人（已批准）</div>
          {approved.length === 0 && (
            <div className="note">尚无已批准的数字人。请先在本体图谱页创建/批准一个数字人。</div>
          )}
          {approved.map((it) => {
            const anchorCount = it.anchors.filter((a) => a.status === 'approved').length
            return (
              <div key={it.id} className={`chat-persona ${it.id === selId ? 'on' : ''}`}
                onClick={() => select(it.id)}>
                <span className="nm">{it.name}</span>
                {it.mission && <span className="mission">{it.mission}</span>}
                <span className="meta">锚点 {anchorCount}</span>
              </div>
            )
          })}

          {selId !== null && (
            <>
              <div className="chat-sess-head">
                <span className="note">会话</span>
                <button className="btn ghost tiny" onClick={newSession} title="新建会话">＋ 新建</button>
              </div>
              <div className="chat-sess-list">
                {sessions.length === 0 && (
                  <div className="note" style={{ padding: '4px 0' }}>暂无历史会话，发送消息即自动创建。</div>
                )}
                {sessions.map((s) => (
                  <div key={s.id}
                    className={`chat-sess ${s.id === sessionId ? 'on' : ''}`}
                    onClick={() => switchSession(s.id)}>
                    {renamingId === s.id ? (
                      <input
                        className="chat-sess-input"
                        value={renameDraft}
                        autoFocus
                        onClick={(e) => e.stopPropagation()}
                        onChange={(e) => setRenameDraft(e.target.value)}
                        onBlur={commitRename}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') commitRename()
                          if (e.key === 'Escape') setRenamingId(null)
                        }}
                      />
                    ) : (
                      <span className="chat-sess-title" title={s.title}>{s.title}</span>
                    )}
                    <span className="chat-sess-count">{s.message_count}</span>
                    {renamingId !== s.id && (
                      <span className="chat-sess-ops" onClick={(e) => e.stopPropagation()}>
                        <button className="op" title="重命名" onClick={() => startRename(s)}>✎</button>
                        <button className="op del" title="删除" onClick={() => removeSession(s.id)}>×</button>
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}
        </div>

        <div className="chat-main">
          <div className="chat-head">
            <b>{sel ? `与「${sel.name}」对话` : '未选择数字人'}</b>
            <span className="note" style={{ flex: 1 }}>
              {compareMode
                ? '对比模式：左右臂的「本体约束 / RAG 资料」可分别开关'
                : ctx
                  ? `本次注入：本体 ${ctx.injected_ontology} · 关系 ${ctx.injected_relations}（共 ${ctx.counts.ontology} 本体 / ${ctx.counts.relations} 关系）${ctx.rag?.hits ? ` · 参考资料 ${ctx.rag.hits} 段` : ''}${ctx.rag?.used && ctx.rag.error ? ' · ⚠ RAG 资料不可用' : ''}`
                  : ''}
            </span>
            <button className="btn ghost small" onClick={doClear} disabled={!selId || messages.length === 0}>
              清空对话
            </button>
          </div>

          <div className="chat-controls">
            <label className="chat-ctl">
              <span className="chat-ctl-label">模型</span>
              <select className="chat-select" value={provider}
                onChange={(e) => setProvider(e.target.value as Provider)}>
                {providerOptions.map((o) => (
                  <option key={o.value} value={o.value} disabled={o.disabled}>{o.label}</option>
                ))}
              </select>
            </label>
            {provider === 'ollama' && (
              <label className="chat-ctl">
                <span className="chat-ctl-label">Ollama 模型</span>
                <select className="chat-select" value={ollamaModel ?? ''}
                  onChange={(e) => setOllamaModel(e.target.value || null)}>
                  {(chatModels?.ollama?.models ?? []).map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              </label>
            )}
            <label className="chat-ctl chat-toggle">
              <input type="checkbox" checked={compareMode}
                onChange={(e) => setCompareMode(e.target.checked)} />
              <span>对比模式（分屏）</span>
            </label>
            {!compareMode && (
              <label className="chat-ctl chat-toggle">
                <input type="checkbox" checked={useOntology}
                  onChange={(e) => setUseOntology(e.target.checked)} />
                <span>本体约束</span>
              </label>
            )}
            {!compareMode && (
              <label className="chat-ctl chat-toggle chat-rag">
                <input type="checkbox" checked={useRag}
                  onChange={(e) => setUseRag(e.target.checked)} />
                <span>RAG 资料</span>
              </label>
            )}
            {!compareMode && ctx && (
              <button className="btn ghost small" onClick={() => setCtxOpen((v) => !v)}>
                {ctxOpen ? '收起本体' : `查看本体 (${ctx.counts.ontology})`}
              </button>
            )}
          </div>

          {!compareMode && ctx && ctxOpen && (
            <div className="ctx-panel">
              <div className="ctx-head">
                <span className="note">
                  本次回答 · {ctx.model} · 本体约束 {ctx.use_ontology ? '开' : '关'}
                  · RAG 资料 {ctx.use_rag ? (ctx.rag?.hits ? `开（命中 ${ctx.rag.hits} 段）` : '开（未命中）') : '关'}
                </span>
              </div>
              {ctx.rag?.used && (
                <div className="ctx-retrieval">
                  {ctx.rag.error
                    ? <span className="ctx-tag warn">RAG 资料不可用：{ctx.rag.error}</span>
                    : <span className="ctx-tag">RAG 资料命中 {ctx.rag.hits} 段</span>}
                </div>
              )}
              {ctx.use_ontology && ctx.retrieval && (
                <div className="ctx-retrieval">
                  {ctx.retrieval.fallback
                    ? <span className="ctx-tag warn">空命中兜底</span>
                    : <span className="ctx-tag">命中 {ctx.retrieval.query_hits}</span>}
                  {ctx.retrieval.llm_fallback?.used && (
                    <span className={`ctx-tag${ctx.retrieval.llm_fallback.hits > 0 ? '' : ' warn'}`}>
                      概念兜底 {ctx.retrieval.llm_fallback.hits > 0
                        ? `命中 ${ctx.retrieval.llm_fallback.hits}（${ctx.retrieval.llm_fallback.concepts.slice(0, 3).join('·')}）`
                        : '未命中'}
                    </span>
                  )}
                  <span className="ctx-tag">扩展 {ctx.retrieval.expanded}</span>
                  {(ctx.retrieval.depth_reached ?? 0) > 0 && (
                    <span className="ctx-tag">深度 {ctx.retrieval.depth_reached} 跳</span>
                  )}
                  <span className="ctx-tag">注入本体 {ctx.injected_ontology}/{ctx.retrieval.total_ontology}</span>
                  <span className="ctx-tag">注入关系 {ctx.injected_relations}/{ctx.retrieval.total_relations}</span>
                  <span className="ctx-tag">
                    预算 {ctx.retrieval.budget_used.toLocaleString()}/{ctx.retrieval.budget_total.toLocaleString()}
                  </span>
                  {ctx.retrieval.truncated && <span className="ctx-tag warn">已截断</span>}
                </div>
              )}
              {ctx.usage && (
                <div className="ctx-usage">
                  <div className="ctx-usage-bar">
                    <div
                      className={`ctx-usage-fill ${ctx.usage.percent >= 100 ? 'over' : ''}`}
                      style={{ width: `${Math.min(ctx.usage.percent, 100)}%` }}
                    />
                  </div>
                  <div className="ctx-usage-text">
                    上下文占用：{ctx.usage.estimate ? '约 ' : ''}
                    {ctx.usage.prompt_tokens.toLocaleString()} / {ctx.usage.context_window.toLocaleString()} tokens
                    （{ctx.usage.percent.toFixed(2)}%）
                    {ctx.usage.completion_tokens != null && ` · 本次生成 ${ctx.usage.completion_tokens.toLocaleString()} tokens`}
                    {ctx.usage.percent >= 100 &&
                      ` · ⚠ 已超窗口：发送 ${ctx.usage.sent_chars.toLocaleString()} 字符，仅前 ${ctx.usage.context_window.toLocaleString()} tokens 被送入，其余被截断`}
                  </div>
                  {ctx.usage.model_context_length != null &&
                    ctx.usage.model_context_length > ctx.usage.context_window && (
                      <div className="ctx-usage-hint">
                        提示：该模型原生窗口 {ctx.usage.model_context_length.toLocaleString()} tokens，
                        但 Ollama 运行时 num_ctx 默认 {ctx.usage.context_window.toLocaleString()}，超出部分会被静默截断。
                        建议调大 num_ctx（如 32768）后再做无截断对比。
                      </div>
                    )}
                </div>
              )}
              {ctx.sent && ctx.sent.length > 0 && (
                <div className="ctx-group">
                  <div className="ctx-group-title">发送给模型的内容（{ctx.sent.length} 条）</div>
                  <div className="ctx-sent-list">
                    {ctx.sent.map((m, i) => (
                      <div key={i} className={`ctx-sent-item ${m.role}`}>
                        <span className="ctx-sent-role">{m.role}</span>
                        <pre className="ctx-sent-content">{m.content}</pre>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {ctx.anchors.length > 0 && (
                <div className="ctx-group">
                  <div className="ctx-group-title">锚点本体（{ctx.anchors.length}）</div>
                  <div className="ctx-list">
                    {ctx.anchors.map((a) => (
                      <div key={a.name} className="ctx-row" title={a.definition || undefined}>
                        <span className="ctx-name">{a.name}</span>
                        {a.definition && <span className="ctx-def">{a.definition}</span>}
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {ctx.ontology.length > 0 && (
                <div className="ctx-group">
                  <div className="ctx-group-title">
                    相关本体（注入 {ctx.ontology.length}{ctx.retrieval ? `/${ctx.retrieval.total_ontology}` : ''}）
                  </div>
                  <div className="ctx-list">
                    {ctx.ontology.map((o) => (
                      <div key={o.name} className="ctx-row" title={o.definition || undefined}>
                        <span className="ctx-name">{o.name}</span>
                        {o.definition && <span className="ctx-def">{o.definition}</span>}
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {ctx.relations.length > 0 && (
                <div className="ctx-group">
                  <div className="ctx-group-title">
                    关系约束（注入 {ctx.relations.length}{ctx.retrieval ? `/${ctx.retrieval.total_relations}` : ''}）
                  </div>
                  <div className="ctx-list">
                    {ctx.relations.map((r, i) => (
                      <div key={i} className="ctx-row">
                        <span className="ctx-name">{r.source} --{r.type}--&gt; {r.target}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {compareMode ? (
            <div className="cmp-grid">
              <div className="cmp-pane">
                <div className="cmp-pane-head">
                  <b>左 · {armLeft.use_ontology ? '使用本体' : '无本体'}{armLeft.use_rag ? ' + RAG 资料' : ''}</b>
                  {cmpLeft && (
                    <span className="note">
                      {cmpLeft.context.usage ? `${cmpLeft.context.usage.percent.toFixed(1)}% 窗口` : ''}
                      {cmpLeft.context.truncated ? ' · 本体已截断' : ''}
                      {cmpLeft.context.rag?.used && !cmpLeft.context.rag.error
                        ? ` · 资料 ${cmpLeft.context.rag.hits} 段` : ''}
                    </span>
                  )}
                </div>
                <div className="cmp-arms">
                  <label className={`cmp-arm-toggle ${armLeft.use_ontology ? 'on' : ''}`}>
                    <input type="checkbox" checked={armLeft.use_ontology}
                      onChange={(e) => { setArmLeft({ ...armLeft, use_ontology: e.target.checked }); setCmpLeft(null) }} />
                    本体约束
                  </label>
                  <label className={`cmp-arm-toggle ${armLeft.use_rag ? 'on' : ''}`}>
                    <input type="checkbox" checked={armLeft.use_rag}
                      onChange={(e) => { setArmLeft({ ...armLeft, use_rag: e.target.checked }); setCmpLeft(null) }} />
                    RAG 资料
                  </label>
                </div>
                {cmpLeft && <div className="cmp-reply">{cmpLeft.reply}</div>}
                {cmpLeft && (
                  <div className="cmp-sent">
                    <div className="cmp-sent-title">
                      发送给模型的全部内容（{cmpLeft.context.sent.length} 条）
                      {cmpLeft.context.usage ? ` · ${cmpLeft.context.usage.sent_chars.toLocaleString()} 字符` : ''}
                    </div>
                    <div className="ctx-sent-list">
                      {cmpLeft.context.sent.map((m, i) => (
                        <div key={i} className={`ctx-sent-item ${m.role}`}>
                          <span className="ctx-sent-role">{m.role}</span>
                          <pre className="ctx-sent-content">{m.content}</pre>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {!cmpLeft && !sending && (
                  <div className="chat-empty">发送后显示左臂回答（按上方开关组合）+ 完整发送内容</div>
                )}
                {sending && <div className="chat-msg assistant chat-typing">正在思考…</div>}
              </div>

              <div className="cmp-pane">
                <div className="cmp-pane-head">
                  <b>右 · {armRight.use_ontology ? '使用本体' : '无本体'}{armRight.use_rag ? ' + RAG 资料' : ''}</b>
                  {cmpRight && (
                    <span className="note">
                      {cmpRight.context.usage ? `${cmpRight.context.usage.percent.toFixed(1)}% 窗口` : ''}
                      {cmpRight.context.rag?.used && !cmpRight.context.rag.error
                        ? ` · 资料 ${cmpRight.context.rag.hits} 段` : ''}
                    </span>
                  )}
                </div>
                <div className="cmp-arms">
                  <label className={`cmp-arm-toggle ${armRight.use_ontology ? 'on' : ''}`}>
                    <input type="checkbox" checked={armRight.use_ontology}
                      onChange={(e) => { setArmRight({ ...armRight, use_ontology: e.target.checked }); setCmpRight(null) }} />
                    本体约束
                  </label>
                  <label className={`cmp-arm-toggle ${armRight.use_rag ? 'on' : ''}`}>
                    <input type="checkbox" checked={armRight.use_rag}
                      onChange={(e) => { setArmRight({ ...armRight, use_rag: e.target.checked }); setCmpRight(null) }} />
                    RAG 资料
                  </label>
                </div>
                {cmpRight && <div className="cmp-reply">{cmpRight.reply}</div>}
                {!cmpRight && !sending && (
                  <div className="chat-empty">发送后显示右臂回答（按上方开关组合）</div>
                )}
                {sending && <div className="chat-msg assistant chat-typing">正在思考…</div>}
              </div>
            </div>
          ) : (
            <div className="chat-log" ref={logRef}>
              {loading && <div className="note">加载历史对话…</div>}
              {!loading && messages.length === 0 && (
                <div className="chat-empty">
                  向「{sel?.name ?? '数字人'}」提问吧。它会只依据自己的本体知识回答。
                </div>
              )}
              {grouped.map((g) => (
                <div key={g.key} className="chat-day">
                  <div className="chat-day-label">{g.label}</div>
                  {g.items.map((m) => (
                    <div key={m.id} className={`chat-msg ${m.role}`}>
                      <span className="chat-msg-time">{timeLabel(m.created_at)}</span>
                      <span className="chat-msg-body">{m.content}</span>
                    </div>
                  ))}
                </div>
              ))}
              {sending && <div className="chat-msg assistant chat-typing">正在思考…</div>}
            </div>
          )}

          <div className="chat-input-row">
            <textarea
              value={input}
              placeholder={sel ? `问「${sel.name}」一个问题（Enter 发送，Shift+Enter 换行）` : '先选择一个数字人'}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKey}
              disabled={!selId}
            />
            <button className="btn green" onClick={doSend} disabled={!selId || !input.trim() || sending}>
              发送
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
