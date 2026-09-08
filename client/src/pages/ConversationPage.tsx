import { useEffect, useMemo, useRef, useState } from 'react'
import {
  getIdentities, getChatMessages, sendChat, routeChat, getChatSessions,
  deleteChatSession, renameChatSession, clearChat, generateMcp,
  type Identity, type ChatMessage, type ChatSession, type ChatRoute,
} from '../api'
import { useToast } from '../Toast'

interface Props {
  refreshKey: number
}

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

/** 纯对话界面：只有「对话组」（会话列表）+ 对话内容。每条消息由后端自动路由
 * 到最匹配的已批准数字人回答，气泡上方标注来源数字人。 */
export default function ConversationPage({ refreshKey }: Props) {
  const [idents, setIdents] = useState<Identity[]>([])
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [loading, setLoading] = useState(false)
  const [genMcpMode, setGenMcpMode] = useState(false)
  const [renamingId, setRenamingId] = useState<number | null>(null)
  const [renameDraft, setRenameDraft] = useState('')
  const logRef = useRef<HTMLDivElement>(null)
  const { toast } = useToast()

  const approved = useMemo(() => idents.filter((i) => i.status === 'approved'), [idents])
  // 会话宿主：用第一个已批准数字人作为会话分组的宿主（消息可路由到其它数字人）
  const hostId = approved[0]?.id ?? null

  useEffect(() => {
    getIdentities().then((r) => setIdents(r.identities ?? [])).catch(() => { })
  }, [refreshKey])

  useEffect(() => {
    if (hostId == null) { setSessions([]); setSessionId(null); return }
    let cancelled = false
    getChatSessions(hostId).then((r) => {
      if (cancelled) return
      const list = r.sessions ?? []
      setSessions(list)
      setSessionId((cur) => (cur != null && !list.some((s) => s.id === cur)) ? null : cur)
    }).catch(() => { })
    return () => { cancelled = true }
  }, [hostId])

  useEffect(() => {
    if (sessionId == null) { setMessages([]); return }
    let cancelled = false
    setLoading(true)
    getChatMessages(hostId ?? 0, sessionId)
      .then((r) => { if (!cancelled) setMessages(r.messages ?? []) })
      .catch(() => { })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [sessionId, hostId])

  useEffect(() => {
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, sending])

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

  const doSend = async () => {
    const text = input.trim()
    if (!text || sending) return
    // 生成 MCP 模式：输入内容作为需求生成一个 MCP（无需路由数字人）
    if (genMcpMode) {
      setSending(true)
      setInput('')
      try {
        const r = await generateMcp(text)
        if (r.ok) {
          toast(`已生成 MCP「${r.name}」（${(r.tools || []).length} 工具），待审批`, 'ok')
          setMessages((m) => [...m, {
            id: -2, identity_id: 0, role: 'user', content: `生成 MCP：${text}`, created_at: Date.now() / 1000,
          }, {
            id: -3, identity_id: 0, role: 'assistant',
            content: `已生成 MCP「${r.name}」，工具：${(r.tools || []).join('、')}。已导入待审批，去「MCP 沙盒」页批准后即可启动。`,
            created_at: Date.now() / 1000,
          }])
        } else {
          toast(r.error || '生成失败', 'err')
          setInput(text)
        }
      } catch (e: any) {
        toast(e.message, 'err')
        setInput(text)
      } finally {
        setSending(false)
      }
      return
    }
    setSending(true)
    setInput('')
    try {
      // 1. 自动路由：判断交给哪个数字人
      const r = await routeChat(text)
      const route = r.route
      if (!route) {
        toast('无法自动确定由哪个数字人回答，请换个更明确的说法', 'err')
        setInput(text)
        return
      }
      // 2. 交给路由到的数字人回答
      const reply = await sendChat(route.identity_id, text, { session_id: sessionId })
      setMessages(reply.messages ?? [])
      if (reply.session_id != null && reply.session_id !== sessionId) {
        setSessionId(reply.session_id)
        if (hostId != null) getChatSessions(hostId).then((s) => setSessions(s.sessions ?? [])).catch(() => { })
      }
    } catch (e: any) {
      toast(e.message, 'err')
      setInput(text)
    } finally {
      setSending(false)
    }
  }

  const newSession = () => {
    setSessionId(null)
    setMessages([])
  }
  const switchSession = (id: number) => { if (id !== sessionId) setSessionId(id) }
  const startRename = (s: ChatSession) => { setRenamingId(s.id); setRenameDraft(s.title) }
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
    if (!confirm('删除该对话组及其全部消息？')) return
    try {
      await deleteChatSession(id)
      setSessions((list) => list.filter((s) => s.id !== id))
      if (sessionId === id) { setSessionId(null); setMessages([]) }
    } catch (e: any) { toast(e.message, 'err') }
  }
  const doClear = async () => {
    if (sessionId == null) return
    if (!confirm('清空当前对话组的全部消息？')) return
    try {
      await clearChat(hostId ?? 0, sessionId)
      setMessages([])
    } catch (e: any) { toast(e.message, 'err') }
  }

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      doSend()
    }
  }

  return (
    <div className="card">
      <h3>
        对话
        <span className="note" style={{ marginLeft: 8 }}>
          自动判别每个问题交给哪个数字人回答，气泡上方标注来源数字人
        </span>
      </h3>
      <div className="desc">
        无需手动选择数字人：系统根据消息内容与各数字人的本体、锚点做确定性匹配（0 次 LLM），
        自动路由到最匹配的已批准数字人回答。匹配不到的会提示你换个说法。
      </div>

      <div className="chat-wrap">
        <div className="chat-side">
          <div className="chat-sess-head">
            <span className="note">对话组</span>
            <button className="btn ghost tiny" onClick={newSession} title="新建对话">＋ 新建</button>
          </div>
          <div className="chat-sess-list">
            {sessions.length === 0 && (
              <div className="note" style={{ padding: '4px 0' }}>暂无对话，发送消息即自动创建。</div>
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
        </div>

        <div className="chat-main">
          <div className="chat-head">
            <b>对话组{sessionId != null ? ` #${sessionId}` : ''}</b>
            <span className="note" style={{ flex: 1 }}>
              {approved.length === 0 ? '尚无已批准数字人，请先在本体图谱页创建/批准。' : `已就绪 ${approved.length} 个数字人，自动路由中。`}
            </span>
            <button className="btn ghost small" onClick={doClear} disabled={sessionId == null || messages.length === 0}>
              清空对话
            </button>
          </div>

          <div className="chat-log" ref={logRef}>
            {loading && <div className="note">加载对话…</div>}
            {!loading && messages.length === 0 && (
              <div className="chat-empty">问我一个问题，系统会自动判断交给哪个数字人回答。</div>
            )}
            {grouped.map((g) => (
              <div key={g.key} className="chat-day">
                <div className="chat-day-label">{g.label}</div>
                {g.items.map((m) => (
                  <div key={m.id} className={`chat-msg ${m.role}`}>
                    {m.role === 'assistant' && m.identity_name && (
                      <span className="cv-identity">由「{m.identity_name}」回答</span>
                    )}
                    <span className="chat-msg-time">{timeLabel(m.created_at)}</span>
                    <span className="chat-msg-body">{m.content}</span>
                  </div>
                ))}
              </div>
            ))}
            {sending && <div className="chat-msg assistant chat-typing">正在判断并回答…</div>}
          </div>

          <div className={`chat-bubble ${genMcpMode ? 'mcp-on' : ''}`}>
            <textarea
              value={input}
              placeholder={genMcpMode ? '描述你要生成的 MCP（如：一个论文搜索工具，返回标题作者摘要）' : '今天帮你做些什么？（Enter 发送，Shift+Enter 换行）'}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKey}
              disabled={!genMcpMode && approved.length === 0}
            />
            <div className="chat-bubble-bar">
              <button className="bubble-op" title="上传文件（暂未开放）" disabled>＋</button>
              <button className={`bubble-op gen ${genMcpMode ? 'on' : ''}`}
                onClick={() => setGenMcpMode((v) => !v)}
                title="生成 MCP：把输入内容作为需求生成一个 MCP（LLM 设计 → 校验 → 导入待审批）">
                ⚙ 生成 MCP
              </button>
              <span className="bubble-spacer" />
              <button className="btn green" onClick={doSend}
                disabled={!input.trim() || sending || (!genMcpMode && approved.length === 0)}>
                {genMcpMode ? '生成 MCP' : '发送'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
