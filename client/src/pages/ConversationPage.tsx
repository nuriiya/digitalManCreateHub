import { useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import {
  getIdentities, getChatMessages, sendChat, routeChat, streamChat, getChatSessions,
  deleteChatSession, renameChatSession, clearChat, deleteChatMessages, generateMcp, generatePipeline,
  routePipeline, runPipeline, getPipeline, getPipelineRuns, getToken, createChatSession,
  startPipelineSession, updatePipelineProgress, getJobEvents, getSessionEvents,
  type Identity, type ChatMessage, type ChatSession, type ChatRoute, type JobEvent,
  type PipelineRoute,
} from '../api'
import { useToast } from '../Toast'
import PipelineCard from '../components/PipelineCard'
import PipelineProgressCard from '../components/PipelineProgressCard'
import ChatTraceCard from '../components/ChatTraceCard'

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
  const [genPipelineMode, setGenPipelineMode] = useState(false)
  // 已生成的 pipeline（对话页内联展示：摘要 + 审批/运行 + DFMEA 产出）
  const [pipelineIds, setPipelineIds] = useState<number[]>([])
  const [renamingId, setRenamingId] = useState<number | null>(null)
  const [renameDraft, setRenameDraft] = useState('')
  // 多选删除（design §13 配套）：selectMode 开关 + 选中 id 集合
  const [selectMode, setSelectMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  // 左侧对话组（topic）多选删除
  const [sessSelectMode, setSessSelectMode] = useState(false)
  const [sessSelectedIds, setSessSelectedIds] = useState<Set<number>>(new Set())
  // 数字人询问用户（design §22 / R-24）：等待点选的选项气泡
  const [pendingAsk, setPendingAsk] = useState<null | {
    question: string; options: string[]; note?: string;
    identityId: number; sessionId: number | null;
  }>(null)
  // pipeline 触发后的轻量阶段进度（design §23.7 手风琴）：轮询 run+事件流 →
  // 结构化标记写入 assistant 消息（持久化），渲染为可展开的 PipelineProgressCard
  const [pipelineProgress, setPipelineProgress] = useState<null | {
    pipelineId: number; name: string; assistantId: number; sessionId: number;
    jobId?: number;
  }>(null)
  const [progressDone, setProgressDone] = useState<null | { runId: number }>(null)
  // 普通对话的明细（E1）：模型每步收到什么/回了什么/调了什么工具
  const [chatTrace, setChatTrace] = useState<null | {
    sessionId: number; events: JobEvent[]
  }>(null)
  // 「创建 pipeline」意图 + 命中已有 pipeline 时的选择气泡
  const [pendingChoice, setPendingChoice] = useState<null | {
    assistantId: number; candidates: PipelineRoute[]; message: string
  }>(null)
  const logRef = useRef<HTMLDivElement>(null)
  const { toast } = useToast()

  const approved = useMemo(() => idents.filter((i) => i.status === 'approved'), [idents])

  const refreshSessions = async (): Promise<ChatSession[]> => {
    try {
      const r = await getChatSessions()
      const list = r.sessions ?? []
      setSessions(list)
      return list
    } catch {
      return []
    }
  }

  useEffect(() => {
    getIdentities().then((r) => setIdents(r.identities ?? [])).catch(() => { })
  }, [refreshKey])

  useEffect(() => {
    if (idents.length === 0) return
    let cancelled = false
    refreshSessions().then((list: ChatSession[]) => {
      if (cancelled) return
      setSessionId((cur) => (cur != null && !list.some((s: ChatSession) => s.id === cur)) ? null : cur)
    })
    return () => { cancelled = true }
  }, [idents.length])

  useEffect(() => {
    setSelectedIds(new Set())
    if (sessionId == null) { setMessages([]); return }
    const sess = sessions.find((s) => s.id === sessionId)
    const sessIdentityId = sess?.identity_id ?? approved[0]?.id ?? 0
    if (sessIdentityId === 0) { setMessages([]); return }
    let cancelled = false
    setLoading(true)
    // 发送期间 / pipeline 进度气泡进行中：**禁止**用后端加载结果覆盖当前
    // 气泡（design §23.4 竞态修复）——setSessionId 会在 runPipeline 返回前
    // 触发本 effect，此时后端可能还是空消息，直接加载会把刚显示的对话
    // 气泡清成空白（user 截图复现：组 #378 空荡）。onDone 的全量替换不走
    // 本 effect，不受影响。
    if (sending || pipelineProgress) {
      setLoading(false)
      return () => { }
    }
    getChatMessages(sessIdentityId, sessionId)
      .then((r) => { if (!cancelled) setMessages(r.messages ?? []) })
      .catch(() => { })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [sessionId, sessions, approved])

  useEffect(() => {
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, sending])

  // pipeline 阶段进度轮询（design §23.7）：4s 拉 run 状态 + 事件流 →
  // 以 @@PIPELINE_PROGRESS@@{json} 结构化标记写入 assistant 消息（持久化），
  // 渲染层识别后交给 PipelineProgressCard 手风琴（可展开看各阶段 LLM 内容）
  useEffect(() => {
    if (!pipelineProgress) return
    const { pipelineId, name, assistantId, sessionId: psSid, jobId } = pipelineProgress
    let cancelled = false
    const tick = async () => {
      try {
        const [pRes, runsRes, evRes] = await Promise.all([
          getPipeline(pipelineId), getPipelineRuns(pipelineId),
          jobId ? getJobEvents(jobId) : Promise.resolve({ events: [] }),
        ])
        if (cancelled) return
        const nodes = pRes.pipeline?.nodes ?? []
        const last = (runsRes.runs ?? [])[0]
        if (!last) return
        const evs = (evRes.events ?? []) as any[]
        // pipeline.node(running) 事件的 seq = 各阶段起点 → llm 事件归属区间
        const nodeStarts = evs
          .filter((e) => e.type === 'pipeline.node' && e.payload?.status === 'running')
          .sort((a, b) => a.seq - b.seq)
        const curIdx = nodes.findIndex((n: any) => n.id === last.current_node_id)
        const stages = nodes.map((n: any, i: number) => {
          const state = last.status === 'done' ? 'done' as const
            : curIdx < 0 ? 'pending' as const
              : i < curIdx ? 'done' as const
                : i === curIdx ? 'running' as const : 'pending' as const
          const seqStart = nodeStarts[i]?.seq ?? Number.MAX_SAFE_INTEGER
          const seqEnd = nodeStarts[i + 1]?.seq ?? Number.MAX_SAFE_INTEGER
          return { key: n.node_key, label: n.step_name || n.node_key,
                   state, seqStart, seqEnd }
        })
        const content = '@@PIPELINE_PROGRESS@@' + JSON.stringify({
          v: 1, pipelineId, name,
          runId: last.id, jobId: last.job_id ?? jobId,
          runStatus: last.status, stages,
        })
        setMessages((m) => m.map((x) => (x.id === assistantId ? { ...x, content } : x)))
        updatePipelineProgress(psSid, assistantId, content).catch(() => { })
        if (last.status !== 'running') {
          setProgressDone({ runId: last.id })
          setPipelineProgress(null)   // 停止轮询
        }
      } catch { /* 网络抖动下一轮再试 */ }
    }
    tick()
    const t = setInterval(tick, 4000)
    return () => { cancelled = true; clearInterval(t) }
  }, [pipelineProgress])

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

  // 下载命中触发的 run 的 Excel 报告（fetch→blob，design §21/§23）
  const downloadProgressExcel = async () => {
    if (!progressDone) return
    try {
      const resp = await fetch(`/api/fmea/export?run_id=${progressDone.runId}`, {
        headers: { Authorization: `Bearer ${getToken()}` },
      })
      if (!resp.ok) {
        toast(resp.status === 404 ? '该 run 没有 DFMEA 行可导出' : `导出失败（${resp.status}）`, 'err')
        return
      }
      const blob = await resp.blob()
      const cd = resp.headers.get('Content-Disposition') || ''
      const m = /filename\*=UTF-8''([^;]+)/.exec(cd)
      const fname = m ? decodeURIComponent(m[1]) : `fmea-run${progressDone.runId}.xlsx`
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = fname; a.click()
      URL.revokeObjectURL(url)
      toast('Excel 报告已下载', 'ok')
    } catch (e: any) { toast(e?.message || String(e), 'err') }
  }

  // 选择气泡的应答：'create' = 创建新 pipeline；'run' = 运行候选 pipeline
  const answerChoice = async (kind: 'create' | 'run', pid?: number, pname?: string) => {
    const pc = pendingChoice
    if (!pc) return
    setPendingChoice(null)
    setMessages((m) => m.filter((x) => x.id !== pc.assistantId))
    if (kind === 'create') {
      setSending(true)
      try {
        const r = await generatePipeline(pc.message)
        if (r.ok) {
          toast(`已生成 pipeline「${r.name}」（${r.nodes} 节点 / ${r.relations} 关系），待审批`, 'ok')
          if (r.pipeline_id) setPipelineIds((v) => [...v, r.pipeline_id as number])
          const tid = -Date.now()
          setMessages((m) => [...m, {
            id: tid, identity_id: 0, role: 'user', content: `创建 pipeline：${pc.message}`, created_at: Date.now() / 1000,
          }, {
            id: tid - 1, identity_id: 0, role: 'assistant',
            content: `已生成 pipeline「${r.name}」（${r.nodes} 节点 / ${r.relations} 关系），状态 draft —— 见下方卡片，可就地**校验 / 批准 / 运行**。`,
            created_at: Date.now() / 1000,
          }])
        } else {
          toast(r.error || '生成失败', 'err')
        }
      } catch (e: any) {
        toast(e.message, 'err')
      } finally {
        setSending(false)
      }
      return
    }
    if (pid == null) return
    try {
      const ps = await startPipelineSession({
        message: pc.message, pipeline_id: pid, pipeline_name: pname || '',
        session_id: sessionId,
      })
      setSessionId(ps.session_id)
      setMessages((m) => [...m, {
        id: -Date.now(), identity_id: 0, role: 'user', content: pc.message, created_at: Date.now() / 1000,
      }, {
        id: -Date.now() - 1, identity_id: 0, role: 'assistant',
        identity_name: `pipeline「${pname}」`,
        content: `🔗 **${pname}** 已触发，加载阶段进度…`,
        created_at: Date.now() / 1000,
      }])
      const run = await runPipeline(pid)
      setPipelineProgress({ pipelineId: pid, name: pname || '', assistantId: ps.assistant_msg_id, sessionId: ps.session_id, jobId: run.job_id })
      toast(`已触发 pipeline「${pname}」运行`, 'ok')
      refreshSessions()
    } catch (e: any) {
      toast(e?.message || String(e), 'err')
    }
  }

  const doSend = async (overrideText?: string) => {
    const text = (overrideText ?? input).trim()
    if (!text || sending) return
    setChatTrace(null)      // 新一轮开始：清掉上一轮的对话明细
    // 创建 pipeline 模式（点「🔗 创建 pipeline」按钮进入）：输入作为需求直接生成。
    // 注：对话里直接说「创建 pipeline」现在走**训练师路由**（训练师本体已赋予
    // 创建权限 + 绑定「生成 pipeline」动作，2026-09-15 治本改造），此处只保留
    // 手动按钮入口。
    if (genPipelineMode) {
      setSending(true)
      setInput('')
      try {
        const r = await generatePipeline(text)
        if (r.ok) {
          toast(`已生成 pipeline「${r.name}」（${r.nodes} 节点 / ${r.relations} 关系），待审批`, 'ok')
          if (r.pipeline_id) setPipelineIds((v) => [...v, r.pipeline_id as number])
          const tid = -Date.now()
          setMessages((m) => [...m, {
            id: tid, identity_id: 0, role: 'user', content: `创建 pipeline：${text}`, created_at: Date.now() / 1000,
          }, {
            id: tid - 1, identity_id: 0, role: 'assistant',
            content: `已生成 pipeline「${r.name}」（${r.nodes} 节点 / ${r.relations} 关系），状态 draft —— 见下方卡片，可就地**校验 / 批准 / 运行**并查看 DFMEA 产出。`,
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
    // 普通对话：**先做 pipeline 匹配**（design §23 / R-25）——
    // 命中就触发运行 + 展示 PipelineCard，不路由数字人手撸；未命中走数字人路由。
    const tempBase = -Date.now()
    const userMsg: ChatMessage = {
      id: tempBase,
      identity_id: 0,
      role: 'user',
      content: text,
      created_at: Math.floor(Date.now() / 1000),
    }
    const assistantId = tempBase - 1
    const assistantMsg: ChatMessage = {
      id: assistantId,
      identity_id: 0,
      role: 'assistant',
      content: '判断路由中…',
      identity_name: '系统',
      created_at: Math.floor(Date.now() / 1000),
    }
    setMessages((m) => [...m, userMsg, assistantMsg])
    setInput('')
    setSending(true)
    const updateAssistant = (patch: Partial<ChatMessage>) =>
      setMessages((m) => m.map((x) => (x.id === assistantId ? { ...x, ...patch } : x)))
    let streamStarted = false
    let streamFinished = false
    // 工具执行进度（拼在 assistant 气泡文本后）
    const toolLines: string[] = []

    // —— pipeline 命中分支：直接触发运行，气泡内展示「任务阶段 1.2.3」进度，
    //    不渲染大卡片（user 2026-09-14：保持界面干净）——
    try {
      const pr = await routePipeline(text)
      // 「创建 pipeline」意图 + 同时命中已有 pipeline → 弹选择气泡让用户定
      // （2026-09-15：此前靠规则强行拦截，用户不认可；改为把选择权交回用户）
      if (pr.create_intent && pr.candidates.length > 0) {
        updateAssistant({ identity_name: '选择', content: '' })
        setPendingChoice({ assistantId, candidates: pr.candidates, message: text })
        setSending(false)
        return
      }
      const matchedPipeline = pr.pipeline
      if (matchedPipeline) {
        // WorkBuddy 式上下文保存（design §23.4）：后端立刻建组 + 持久化
        // user 消息和 assistant 进度消息（真实 id），刷新/切换不丢
        try {
          const ps = await startPipelineSession({
            message: text,
            pipeline_id: matchedPipeline.pipeline_id,
            pipeline_name: matchedPipeline.name,
            primary_persona_id: matchedPipeline.primary_persona_id,
            session_id: sessionId,
          })
          setSessionId(ps.session_id)
          // 临时负 id 气泡替换为持久化消息（真实 id）
          setMessages([
            { ...userMsg, id: ps.user_msg_id },
            { ...assistantMsg, id: ps.assistant_msg_id,
              identity_name: `pipeline「${matchedPipeline.name}」`,
              content: `🔗 **${matchedPipeline.name}** 已触发，加载阶段进度…` },
          ])
          refreshSessions()
          try {
            const run = await runPipeline(matchedPipeline.pipeline_id)
            setMessages((m) => m.map((x) => (x.id === ps.assistant_msg_id
              ? { ...x, content: `🔗 **${matchedPipeline.name}** 已触发（job #${run.job_id}），加载阶段进度…` }
              : x)))
            setPipelineProgress({
              pipelineId: matchedPipeline.pipeline_id,
              name: matchedPipeline.name,
              assistantId: ps.assistant_msg_id,
              sessionId: ps.session_id,
              jobId: run.job_id,
            })
            toast(`已触发 pipeline「${matchedPipeline.name}」运行`, 'ok')
            refreshSessions()
          } catch (e: any) {
            updateAssistant({
              identity_name: `pipeline「${matchedPipeline.name}」`,
              content: `pipeline 匹配到了但运行失败：${e?.message || e}`,
            })
            toast(e?.message || String(e), 'err')
          }
        } catch (e: any) {
          updateAssistant({
            identity_name: '错误',
            content: `创建对话组失败：${e?.message || e}`,
          })
          toast(e?.message || String(e), 'err')
        }
        return
      }
    } catch { /* 匹配失败就 fallback 数字人路由 */ }

    try {
      // 阶段 A：路由（确定性 0 LLM）。同一 session 的后续消息（如「开始」）
      // 沿用 session 绑定的数字人，不重新匹配（design §22.3）。
      const r = await routeChat(text, sessionId)
      const route: ChatRoute | null = r.route
      if (!route) {
        updateAssistant({
          content: '无法自动确定由哪个数字人回答。请换个更明确的说法（包含具体领域关键词）。',
          identity_name: '提示',
        })
        toast('路由失败', 'err')
        return
      }
      updateAssistant({
        identity_id: route.identity_id,
        identity_name: `${route.identity_name} 正在组织语言…`,
        content: '',
      })

      // 阶段 B：流式生成（边收 token 边追加到气泡）
      let accumulated = ''
      await streamChat(
        route.identity_id,
        text,
        { session_id: sessionId },
        {
          onSession: (sid) => {
            if (sid !== sessionId) {
              setSessionId(sid)
              // design §23.4：新对话组**立刻**出现在左侧列表，
              // 不等流式回复完成（此前要等 onDone 的 refreshSessions）
              refreshSessions()
            }
          },
          onToken: (tok) => {
            if (!streamStarted) streamStarted = true
            accumulated += tok
            // 流式 token 只含正文（不含 tool_call 草稿）；工具进度
            // 拼在正文之后供用户看到「执行了哪些动作」。
            updateAssistant({
              identity_name: route.identity_name,
              content: accumulated + (toolLines.length
                ? '\n\n---\n' + toolLines.join('\n')
                : ''),
            })
          },
          onTool: (ev) => {
            // 实时显示「执行动作」进度；最终回灌结果在 reply 之后再补充。
            if (ev.ok) {
              toolLines.push(`⏳ 已执行 \`${ev.name}\``)
            } else {
              toolLines.push(`✗ 动作 \`${ev.name}\` 被拒：${ev.reason || '未知原因'}`)
            }
            updateAssistant({
              identity_name: route.identity_name,
              content: accumulated + (toolLines.length
                ? '\n\n---\n' + toolLines.join('\n')
                : ''),
            })
          },
          onDone: (data) => {
            streamFinished = true
            // 用后端持久化的全量消息替换掉临时 user + assistant 气泡
            // （保留下方的 created_at / identity_name 不变）
            const finalMsgs = data.messages ?? []
            if (finalMsgs.length) {
              setMessages(finalMsgs)
            } else {
              // 兜底：保留临时气泡 + 显示真实回复
              updateAssistant({
                identity_name: route.identity_name,
                content: data.reply || accumulated,
              })
            }
            // E1：拉取本轮对话明细（模型每步收到什么/回了什么/调了什么工具）
            const sid = data.session_id ?? sessionId
            if (sid) {
              getSessionEvents(sid)
                .then((r) => setChatTrace({ sessionId: sid,
                                            events: r.events ?? [] }))
                .catch(() => { })
            }
            refreshSessions()
          },
          onAsk: (ev) => {
            // 数字人询问用户：把问题展示成可点选选项气泡（design §22 / R-24）
            streamFinished = true
            updateAssistant({
              identity_name: route.identity_name,
              content: ev.question + (ev.note ? `\n\n*${ev.note}*` : ''),
            })
            setPendingAsk({
              question: ev.question, options: ev.options,
              note: ev.note, identityId: route.identity_id,
              sessionId,
            })
            refreshSessions()
          },
          onError: (err) => {
            updateAssistant({
              identity_name: '错误',
              content: `生成失败：${err}${accumulated ? '\n\n（已接收的片段）\n' + accumulated : ''}`,
            })
            toast(`生成失败：${err}`, 'err')
          },
        },
      )
    } catch (e: any) {
      updateAssistant({
        identity_name: '错误',
        content: `请求失败：${e?.message || e}`,
      })
      toast(e?.message || String(e), 'err')
    } finally {
      setSending(false)
    }
    // 静态分析提示：streamStarted 留作后续扩展（如显示打字指示器），当前未使用
    void streamFinished
  }

  const newSession = () => {
    setSessionId(null)
    setMessages([])
  }
  // 用户点选数字人给的选项 → 作为下一条消息发送（design §22 / R-24）
  const answerAsk = (choice: string) => {
    const ask = pendingAsk
    setPendingAsk(null)
    if (!ask) return
    // 把选项作为用户回答发送（带上下文标记，让数字人知道这是对它的回答）
    doSend(`（回答你的问题）${choice}`)
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

  // ---- 左侧对话组（topic）多选删除 ----
  const toggleSessSelect = (id: number) => {
    setSessSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }
  const toggleSessSelectAll = () => {
    if (sessions.length > 0 && sessions.every((s) => sessSelectedIds.has(s.id))) {
      setSessSelectedIds(new Set())
    } else {
      setSessSelectedIds(new Set(sessions.map((s) => s.id)))
    }
  }
  const doDeleteSelectedSessions = async () => {
    if (sessSelectedIds.size === 0) return
    const n = sessSelectedIds.size
    if (!confirm(`删除选中的 ${n} 个对话组及其全部消息？`)) return
    let ok = 0, fail = 0
    for (const id of sessSelectedIds) {
      try { await deleteChatSession(id); ok++ } catch { fail++ }
    }
    setSessions((list) => list.filter((s) => !sessSelectedIds.has(s.id)))
    if (sessionId != null && sessSelectedIds.has(sessionId)) {
      setSessionId(null); setMessages([])
    }
    setSessSelectedIds(new Set())
    toast(`已删除 ${ok} 个对话组${fail ? `（失败 ${fail}）` : ''}`, fail ? 'err' : 'ok')
    refreshSessions()
  }
  const doClear = async () => {
    if (sessionId == null) return
    const sess = sessions.find((s) => s.id === sessionId)
    const sessIdentityId = sess?.identity_id ?? approved[0]?.id ?? 0
    if (sessIdentityId === 0) return
    if (!confirm('清空当前对话组的全部消息？')) return
    try {
      await clearChat(sessIdentityId, sessionId)
      setMessages([])
      setSelectedIds(new Set())
    } catch (e: any) { toast(e.message, 'err') }
  }

  // ---- 多选删除 ----
  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }
  const toggleSelectAll = () => {
    // 只选真实消息（id>0）；临时占位气泡（负数 id）不可选
    const realIds = messages.filter((m) => m.id > 0).map((m) => m.id)
    if (selectedIds.size >= realIds.length && realIds.every((id) => selectedIds.has(id))) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(realIds))
    }
  }
  const doDeleteSelected = async () => {
    if (selectedIds.size === 0) return
    if (!confirm(`删除选中的 ${selectedIds.size} 条消息？`)) return
    try {
      const r = await deleteChatMessages([...selectedIds])
      setMessages((ms) => ms.filter((m) => !selectedIds.has(m.id)))
      setSelectedIds(new Set())
      toast(`已删除 ${r.deleted} 条消息`, 'ok')
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
            <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
              <button className={`btn ghost tiny ${sessSelectMode ? 'on' : ''}`}
                onClick={() => { setSessSelectMode((v) => !v); setSessSelectedIds(new Set()) }}
                disabled={sessions.length === 0}
                title="多选对话组后批量删除">
                {sessSelectMode ? '✓ 多选中' : '多选'}
              </button>
              <button className="btn ghost tiny" onClick={newSession} title="新建对话">＋ 新建</button>
            </span>
          </div>
          {sessSelectMode && (
            <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
              <button className="btn ghost tiny" onClick={toggleSessSelectAll}
                disabled={sessions.length === 0}>
                {sessions.length > 0 && sessions.every((s) => sessSelectedIds.has(s.id)) ? '取消全选' : '全选'}
              </button>
              <button className="btn red tiny" onClick={doDeleteSelectedSessions}
                disabled={sessSelectedIds.size === 0}>
                删除所选 ({sessSelectedIds.size})
              </button>
            </div>
          )}
          <div className="chat-sess-list">
            {sessions.length === 0 && (
              <div className="note" style={{ padding: '4px 0' }}>暂无对话，发送消息即自动创建。</div>
            )}
            {sessions.map((s) => (
              <div key={s.id}
                className={`chat-sess ${s.id === sessionId ? 'on' : ''}`}
                style={sessSelectMode ? {
                  cursor: 'pointer',
                  background: sessSelectedIds.has(s.id) ? 'rgba(93,202,165,0.14)' : undefined,
                  borderColor: sessSelectedIds.has(s.id) ? 'rgba(93,202,165,0.5)' : undefined,
                } : undefined}
                onClick={sessSelectMode ? () => toggleSessSelect(s.id) : () => switchSession(s.id)}>
                {sessSelectMode && (
                  <input type="checkbox"
                    checked={sessSelectedIds.has(s.id)}
                    onClick={(e) => e.stopPropagation()}
                    onChange={() => toggleSessSelect(s.id)}
                    style={{ flex: '0 0 auto', width: 'auto', accentColor: 'var(--accent)' }} />
                )}
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
                {/* sending 状态下当前会话行显示加载动画；2026-09-14 加：用户
                    期望左侧列表能感知"对话正在进行中"，与上方 spinning 状态同步 */}
                {!sessSelectMode && sending && s.id === sessionId && (
                  <span className="chat-sess-loading" title="生成中…" aria-label="loading">
                    <span className="dot">·</span>
                    <span className="dot">·</span>
                    <span className="dot">·</span>
                  </span>
                )}
                {!sessSelectMode && renamingId !== s.id && (
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
            <button className={`btn ghost small ${selectMode ? 'on' : ''}`}
              onClick={() => { setSelectMode((v) => !v); setSelectedIds(new Set()) }}
              disabled={sessionId == null || messages.length === 0}
              title="多选消息后批量删除">
              {selectMode ? '✓ 多选中' : '多选'}
            </button>
            {selectMode && (
              <>
                <button className="btn ghost small" onClick={toggleSelectAll}
                  disabled={messages.filter((m) => m.id > 0).length === 0}>
                  {(() => {
                    const real = messages.filter((m) => m.id > 0).map((m) => m.id)
                    return real.length > 0 && real.every((id) => selectedIds.has(id)) ? '取消全选' : '全选'
                  })()}
                </button>
                <button className="btn red small" onClick={doDeleteSelected} disabled={selectedIds.size === 0}>
                  删除所选 ({selectedIds.size})
                </button>
              </>
            )}
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
                  <div key={m.id} className={`chat-msg ${m.role}`}
                    style={selectMode && m.id > 0 ? { cursor: 'pointer', outline: selectedIds.has(m.id) ? '1.5px solid var(--accent)' : '1px dashed transparent', outlineOffset: '2px', borderRadius: 6 } : undefined}
                    onClick={selectMode && m.id > 0 ? () => toggleSelect(m.id) : undefined}>
                    {selectMode && m.id > 0 && (
                      <input type="checkbox"
                        checked={selectedIds.has(m.id)}
                        onClick={(e) => e.stopPropagation()}
                        onChange={() => toggleSelect(m.id)}
                        style={{ flex: '0 0 auto', width: 'auto', marginRight: 6, accentColor: 'var(--accent)' }} />
                    )}
                    {m.role === 'assistant' && m.identity_name && (
                      <span className="cv-identity">由「{m.identity_name}」回答</span>
                    )}
                    <span className="chat-msg-time">{timeLabel(m.created_at)}</span>
                    {m.role === 'assistant' && m.content.startsWith('@@PIPELINE_PROGRESS@@') ? (
                      <span className="chat-msg-body">
                        <PipelineProgressCard raw={m.content} />
                      </span>
                    ) : (
                      <span className="chat-msg-body md-body"><ReactMarkdown>{m.content}</ReactMarkdown></span>
                    )}
                  </div>
                ))}
              </div>
            ))}
            {/* 占位 assistant 气泡已在 doSend 发起时立即 push 到 messages
                （initial identity_name='系统' + content='判断路由中…'，
                流式 token 累加时实时更新），不再渲染静态 typing 气泡
                —— 否则会和占位气泡重复，造成视觉混乱。 */}
            {/* 数字人询问用户：可点选选项气泡（design §22 / R-24） */}
            {pendingAsk && (
              <div className="chat-ask-bubble">
                <div className="chat-ask-title">💬 数字人想确认一下</div>
                {pendingAsk.note && (
                  <div className="chat-ask-note">{pendingAsk.note}</div>
                )}
                <div className="chat-ask-options">
                  {pendingAsk.options.map((opt) => (
                    <button key={opt} className="chat-ask-option"
                      onClick={() => answerAsk(opt)}>
                      {opt}
                    </button>
                  ))}
                </div>
                <button className="chat-ask-skip"
                  onClick={() => { setPendingAsk(null); toast('已跳过，数字人将自行假设继续', 'ok') }}>
                  跳过，让数字人自行决定
                </button>
              </div>
            )}
            {/* 「创建 pipeline」意图 + 命中已有 pipeline：让用户选（治本：选择权交回用户） */}
            {pendingChoice && (
              <div className="chat-ask-bubble">
                <div className="chat-ask-title">🔀 你提到「创建 pipeline」，同时匹配到已有的 pipeline</div>
                <div className="chat-ask-options">
                  <button className="chat-ask-option"
                    onClick={() => answerChoice('create')}>
                    🆕 创建新 pipeline
                  </button>
                  {pendingChoice.candidates.map((c) => (
                    <button key={c.pipeline_id} className="chat-ask-option"
                      onClick={() => answerChoice('run', c.pipeline_id, c.name)}>
                      ▶ 运行「{c.name}」
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* 生成的 pipeline 草稿（「创建 pipeline」模式）：保留就地审批/运行卡片 */}
          {pipelineIds.map((pid) => (
            <PipelineCard key={pid} pipelineId={pid} />
          ))}

          {/* 命中触发的运行：完成后给一行轻量下载按钮（不渲染大卡片
              —— user 2026-09-14：保持对话界面干净，进度已在气泡里） */}
          {progressDone && (
            <div style={{ margin: '4px 0 8px' }}>
              <button className="btn green small" onClick={downloadProgressExcel}>
                ⭳ 下载 FMEA Excel 报告
              </button>
            </div>
          )}

          {/* 普通对话的明细（E1）：点开看模型每步的收到/回复/工具调用 */}
          {chatTrace && chatTrace.events.length > 0 && (
            <ChatTraceCard events={chatTrace.events} />
          )}

          <div className={`chat-bubble ${genMcpMode ? 'mcp-on' : ''} ${genPipelineMode ? 'pipe-on' : ''}`}>
            <textarea
              value={input}
              placeholder={genMcpMode ? '描述你要生成的 MCP（如：一个论文搜索工具，返回标题作者摘要）'
                : genPipelineMode ? '描述你要创建的 pipeline（如：先需求分析再技术设计再代码实现再审查）'
                : '今天帮你做些什么？（Enter 发送，Shift+Enter 换行）'}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKey}
              disabled={!genMcpMode && !genPipelineMode && approved.length === 0}
            />
            <div className="chat-bubble-bar">
              <button className="bubble-op" title="上传文件（暂未开放）" disabled>＋</button>
              <button className={`bubble-op gen ${genMcpMode ? 'on' : ''}`}
                onClick={() => { setGenMcpMode((v) => !v); setGenPipelineMode(false) }}
                title="生成 MCP：把输入内容作为需求生成一个 MCP（LLM 设计 → 校验 → 导入待审批）">
                ⚙ 生成 MCP
              </button>
              <button className={`bubble-op gen ${genPipelineMode ? 'on' : ''}`}
                onClick={() => { setGenPipelineMode((v) => !v); setGenMcpMode(false) }}
                title="创建 pipeline：把输入作为需求，让 LLM 设计节点+关系并落库 draft（待审批）">
                🔗 创建 pipeline
              </button>
              <span className="bubble-spacer" />
              <button className="btn green" onClick={() => doSend()}
                disabled={!input.trim() || sending || (!genMcpMode && !genPipelineMode && approved.length === 0)}>
                {genMcpMode ? '生成 MCP' : genPipelineMode ? '创建 pipeline' : '发送'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
