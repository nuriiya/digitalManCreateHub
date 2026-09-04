import { useEffect, useMemo, useRef, useState } from 'react'
import {
  triggerOntology, triggerRepair, getJobs, getStats,
  pauseJob, resumeJob, deleteJob, getJobEvents,
  getToken, setToken,
} from './api'
import { useEvents } from './useEvents'
import { ToastProvider, useToast } from './Toast'
import SettingsPage from './pages/SettingsPage'
import RagPage from './pages/RagPage'
import OntologyPage from './pages/OntologyPage'
import ChatPage from './pages/ChatPage'
import LoginPage from './pages/LoginPage'
import McpPage from './pages/McpPage'
import IngestDialog from './components/IngestDialog'
import type { EventItem } from './api'

type Tab = 'ingest' | 'rag' | 'ontology' | 'chat' | 'settings' | 'mcp'

const REFRESH_ON: string[] = [
  'job.finished', 'job.failed', 'job.paused', 'job.resumed', 'job.deleted',
  'job.cancelled', 'job.started', 'job.autopaused', 'rag.document_deleted',
  'rag.chunk_deleted', 'orchestration.rule_cleaned', 'orchestration.confirmed',
  'orchestration.discarded', 'assembly.confirmed', 'assembly.discarded',
  'assembly.restored', 'benchmark.merged', 'benchmark.rolled_back',
]

/** Render one event row: LLM calls get a readable prompt/reply preview. */
function EventRow({ e }: { e: EventItem }) {
  const [open, setOpen] = useState(false)
  const p = e.payload || {}
  let body: React.ReactNode = null
  if (e.type === 'llm.call') {
    body = (
      <div className="llmbox">
        <div className="llmmeta">模型 {p.model ?? '?'} · 发送 {p.prompt_len ?? '?'} 字</div>
        <pre>{p.prompt_preview ?? ''}</pre>
      </div>
    )
  } else if (e.type === 'llm.reply') {
    body = (
      <div className="llmbox">
        <div className="llmmeta">模型回复 · {p.model ?? ''}</div>
        <pre>{p.reply_preview ?? ''}</pre>
      </div>
    )
  } else {
    body = <span className="evjson">{JSON.stringify(p)}</span>
  }
  return (
    <div className={`ev ${e.type.startsWith('llm.') ? 'evllm' : ''}`}>
      <b className="evtype" onClick={() => setOpen((v) => !v)} title="点击展开/收起">
        [{e.seq}] {e.type}
      </b>
      {(open || e.type.startsWith('llm.')) && body}
    </div>
  )
}

// ---------- event stream grouped by run: one "整理" = one block ----------

const RUN_START = new Set(['job.started', 'job.resumed'])
const RUN_END: Record<string, string> = {
  'job.finished': 'done',
  'job.failed': 'failed',
  'job.paused': 'paused',
  'job.autopaused': 'autopaused',
  'job.cancelled': 'cancelled',
}
const KIND_LABEL: Record<string, string> = {
  ingest: '入库', repair: '摘要修复', ontology: '本体提取',
  identity: '身份提名', exam: '审批前考核', orchestrate: '二次编排',
  assemble: '本体装配',
}
const STATUS_LABEL: Record<string, string> = {
  running: '进行中', done: '完成', failed: '失败', paused: '已暂停',
  autopaused: '自动暂停', cancelled: '已取消', interrupted: '被新运行中断',
}

interface RunBlock {
  jobId: number
  kind: string
  runNo: number
  startSeq: number
  endSeq: number | null
  startTs: number
  endTs: number | null
  status: string
  events: EventItem[]
}

function fmtClock(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
}

function fmtDur(a: number, b: number) {
  const s = Math.max(0, Math.round(b - a))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m${String(s % 60).padStart(2, '0')}s`
  return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, '0')}m`
}

/** Group ASC events into per-run blocks: job.started / job.resumed open a
 *  block, job.finished / failed / paused / autopaused / cancelled close it.
 *  Events without a job (system.*, rag.*, ontology.candidate_*) go to a
 *  separate system bucket. */
function groupRuns(events: EventItem[]): { runs: RunBlock[]; system: EventItem[] } {
  const open = new Map<number, RunBlock>()
  const done: RunBlock[] = []
  const system: EventItem[] = []
  const runCount = new Map<number, number>()
  const jobKind = new Map<number, string>()
  for (const e of events) {
    if (e.job_id === null) { system.push(e); continue }
    if (RUN_START.has(e.type)) {
      // a new run started for a job that was still open -> close the old one
      const prev = open.get(e.job_id)
      if (prev) {
        prev.endSeq = e.seq - 1
        prev.endTs = e.ts
        if (prev.status === 'running') prev.status = 'interrupted'
        open.delete(e.job_id)
        done.push(prev)
      }
      if (e.type === 'job.started') jobKind.set(e.job_id, e.payload?.kind ?? '')
      const n = (runCount.get(e.job_id) ?? 0) + 1
      runCount.set(e.job_id, n)
      open.set(e.job_id, {
        jobId: e.job_id,
        kind: jobKind.get(e.job_id) ?? e.payload?.kind ?? '',
        runNo: n,
        startSeq: e.seq,
        endSeq: null,
        startTs: e.ts,
        endTs: null,
        status: 'running',
        events: [e],
      })
      continue
    }
    const blk = open.get(e.job_id)
    if (blk) {
      blk.events.push(e)
      const st = RUN_END[e.type]
      if (st) {
        blk.status = st
        blk.endSeq = e.seq
        blk.endTs = e.ts
        open.delete(e.job_id)
        done.push(blk)
      }
    } else {
      system.push(e) // orphan event (job deleted etc.)
    }
  }
  return { runs: [...done, ...open.values()], system }
}

function RunBlockView({ block, showLlm }: { block: RunBlock; showLlm: boolean }) {
  const evs = block.events.filter((e) => showLlm || !e.type.startsWith('llm.'))
  const last = block.events[block.events.length - 1]
  const endTs = block.endTs ?? (last ? last.ts : block.startTs)
  const stillOpen = block.status === 'running' || block.status === 'interrupted'
  return (
    <details className="evrun" open>
      <summary className="evrun-head">
        <span className="evrun-title">#{block.jobId} · {KIND_LABEL[block.kind] ?? block.kind} · 第 {block.runNo} 次运行</span>
        <span className={`status-pill ${block.status}`}>{STATUS_LABEL[block.status] ?? block.status}</span>
        <span className="evrun-time">
          {fmtClock(block.startTs)} → {stillOpen
            ? `进行中 · 已 ${fmtDur(block.startTs, endTs)}`
            : `${fmtClock(endTs)} · 耗时 ${fmtDur(block.startTs, endTs)}`}
        </span>
        <span className="evrun-count">{evs.length} 条</span>
      </summary>
      <div className="log">
        {[...evs].reverse().map((e) => <EventRow key={e.seq} e={e} />)}
      </div>
    </details>
  )
}

function Console({ onLogout }: { onLogout: () => void }) {
  const [tab, setTab] = useState<Tab>('ingest')
  const [refreshKey, setRefreshKey] = useState(0)
  const [jobs, setJobs] = useState<any[]>([])
  const [stats, setStats] = useState<any>({})
  const [focusChunk, setFocusChunk] = useState<number | null>(null)
  const [expandedJob, setExpandedJob] = useState<number | null>(null)
  const [jobEvents, setJobEvents] = useState<EventItem[]>([])
  const [showLlm, setShowLlm] = useState(false)
  const [showIngestDialog, setShowIngestDialog] = useState(false)
  const { toast } = useToast()

  const bump = () => setRefreshKey((k) => k + 1)

  const onEvent = (e: EventItem) => {
    if (REFRESH_ON.includes(e.type)) {
      getJobs().then((r) => setJobs(r.jobs))
      getStats().then(setStats)
      if (e.type === 'job.finished') toast(`任务完成：#${e.job_id}`, 'ok')
      if (e.type === 'job.failed') toast(`任务失败：${e.payload?.error}`, 'err')
      if (e.type === 'job.autopaused') toast(`任务 #${e.job_id} 已自动暂停：${e.payload?.reason}`, 'err')
      if (e.type === 'job.deleted' && expandedJob === e.payload?.id) setExpandedJob(null)
      bump()
    }
    if (expandedJob !== null && e.job_id === expandedJob) {
      // live append into the open detail panel (newest first, dedup by seq)
      setJobEvents((prev) =>
        prev.some((x) => x.seq === e.seq) ? prev : [e, ...prev])
    }
  }
  const { events, connected } = useEvents(onEvent, getToken())
  const grouped = useMemo(() => groupRuns(events), [events])

  const refreshJobs = () => {
    getJobs().then((r) => setJobs(r.jobs))
    getStats().then(setStats)
  }
  useEffect(() => { refreshJobs() }, [])

  // fetch history for the expanded job detail panel
  const expandedRef = useRef<number | null>(null)
  expandedRef.current = expandedJob
  useEffect(() => {
    if (expandedJob === null) { setJobEvents([]); return }
    getJobEvents(expandedJob).then((r) => {
      // refetch only if the user hasn't switched to another job meanwhile
      if (expandedRef.current === expandedJob) setJobEvents(r.events ?? [])
    }).catch(() => {})
  }, [expandedJob])

const doRepair = async () => {
    try {
      const r = await triggerRepair()
      toast(`修复任务 #${r.job_id} 已启动`, 'ok')
      refreshJobs()
    } catch (e: any) { toast(e.message, 'err') }
  }
  const doOntology = async () => {
    try {
      const r = await triggerOntology()
      toast(`本体提取任务 #${r.job_id} 已启动`, 'ok')
      refreshJobs()
    } catch (e: any) { toast(e.message, 'err') }
  }
  const doPause = async (id: number) => {
    try { await pauseJob(id); toast(`任务 #${id} 将在当前 chunk 完成后暂停`, 'ok') }
    catch (e: any) { toast(e.message, 'err') }
  }
  const doResume = async (id: number) => {
    try { await resumeJob(id); toast(`任务 #${id} 已继续`, 'ok'); refreshJobs() }
    catch (e: any) { toast(e.message, 'err') }
  }
  const doDelete = async (id: number) => {
    if (!confirm(`删除任务 #${id}？运行中的任务会先停止再删除（入库的数据保留）。`)) return
    try {
      const r = await deleteJob(id)
      toast(r.action === 'deleting' ? `任务 #${id} 停止中，稍后自动删除` : `任务 #${id} 已删除`, 'ok')
      if (expandedJob === id) setExpandedJob(null)
      refreshJobs()
    } catch (e: any) { toast(e.message, 'err') }
  }

  const activeJob = (kind: string) =>
    jobs.find((j) => j.kind === kind && (j.status === 'running' || j.status === 'paused'))
  const ingestBusy = !!activeJob('ingest') || !!activeJob('repair')
  const ontologyBusy = !!activeJob('ontology')

  const tabs: { id: Tab; label: string }[] = [
    { id: 'ingest', label: '入库' },
    { id: 'rag', label: 'RAG 预览' },
    { id: 'ontology', label: '本体图谱' },
    { id: 'chat', label: '对话' },
    { id: 'mcp', label: 'MCP 沙盒' },
    { id: 'settings', label: '设置' },
  ]

  return (
    <div className="app">
      <div className="topbar">
        <h1>rag-mvp</h1>
        <span className={`dot ${connected ? 'on' : 'off'}`} title={connected ? '事件流已连接' : '事件流断开（自动重连中）'} />
        <span className="note">{stats.documents ?? 0} 文档 · {stats.chunks ?? 0} chunks · {stats.pending ?? 0} 候选 · {stats.approved ?? 0} 已批准</span>
        <nav>
          {tabs.map((t) => (
            <button key={t.id} className={tab === t.id ? 'active' : ''} onClick={() => setTab(t.id)}>{t.label}</button>
          ))}
        </nav>
        <button className="btn ghost small logout" onClick={onLogout} title="退出登录">退出</button>
      </div>

      <div className="content">
        {showIngestDialog && (
          <IngestDialog
            onClose={() => setShowIngestDialog(false)}
            disabled={ingestBusy}
            onDone={() => refreshJobs()}
          />
        )}
        {tab === 'ingest' && (
          <>
            <div className="card">
              <h3>管线触发</h3>
              <div className="desc">工作目录与模型在「设置」页配置。入库：加载→分段→摘要/标签（Flash）→嵌入→入库；本体提取：EDC 式提名→三关校验→待审批。同类型任务运行中不可重复触发（防并发写库冲突）。</div>
              <div className="btnrow">
                <button className="btn" onClick={() => setShowIngestDialog(true)}
                        disabled={ingestBusy}
                        title={ingestBusy ? '入库/修复任务进行中' : ''}>① 添加资料</button>
                <button className="btn ghost" onClick={doRepair} disabled={ingestBusy} title="重跑之前因网络波动退化为规则兜底的摘要/标签（LLM 未配置时无需修复）">③ 修复兜底摘要</button>
                <button className="btn ghost" onClick={doOntology} disabled={!stats.chunks || ontologyBusy} title={ontologyBusy ? '本体提取任务进行中' : ''}>② 本体提取（提名+校验）</button>
                <button className="btn ghost" onClick={refreshJobs}>刷新</button>
              </div>
            </div>
            <div className="card">
              <h3>任务进度（点击行展开详情，含发给大模型的内容）</h3>
              {jobs.map((j) => (
                <div key={j.id}>
                  <div className="jobline">
                    <span className="kind clickable" onClick={() => setExpandedJob(expandedJob === j.id ? null : j.id)}>
                      {expandedJob === j.id ? '▾' : '▸'} {j.kind} #{j.id}
                    </span>
                    <span className={`status-pill ${j.status}`}>{j.status}</span>
                    <div className="bar">
                      <div className="progressbar">
                        <div style={{ width: `${j.progress_total ? Math.round(100 * j.progress_current / j.progress_total) : 0}%` }} />
                      </div>
                    </div>
                    <span className="num">{j.progress_current}/{j.progress_total}</span>
                    {j.error && <span className="warn" title={j.error}>错误</span>}
                    <span className="jobactions">
                      {j.status === 'running' && (
                        <button className="btn ghost small" onClick={() => doPause(j.id)}>暂停</button>
                      )}
                      {(j.status === 'paused' || j.status === 'failed' || j.status === 'cancelled') && (
                        <button className="btn ghost small green" onClick={() => doResume(j.id)}>继续</button>
                      )}
                      <button className="btn ghost small red" onClick={() => doDelete(j.id)}>删除</button>
                    </span>
                  </div>
                  {j.error && (
                    <div className="joberr" title={j.error}>
                      {j.status === 'paused' ? '⚠ 已自动暂停 · ' : '⚠ '}{j.error}
                      {j.status === 'paused' && '（排查后点「继续」重跑）'}
                    </div>
                  )}
                  {expandedJob === j.id && (
                    <div className="jobdetail">
                      <div className="note">
                        {j.detail}
                        {j.error ? ` · ${j.error}` : ''}
                      </div>
                      <div className="log">
                        {jobEvents.length === 0 && <div className="ev">（无事件）</div>}
                        {jobEvents.map((e) => <EventRow key={e.seq} e={e} />)}
                      </div>
                    </div>
                  )}
                </div>
              ))}
              {!jobs.length && <div className="note">暂无任务</div>}
            </div>
            <div className="card">
              <h3>
                事件流（断线按 seq 增量补齐 · 一次运行一个块）
                <label className="llmtoggle">
                  <input type="checkbox" checked={showLlm} onChange={(e) => setShowLlm(e.target.checked)} />
                  显示 LLM 调用（任务详情里始终可见）
                </label>
              </h3>
              <div className="evstream">
                {grouped.runs
                  .sort((a, b) => b.startSeq - a.startSeq)
                  .map((b) => (
                    <RunBlockView key={`${b.jobId}-${b.startSeq}`} block={b} showLlm={showLlm} />
                  ))}
                {grouped.system.length > 0 && (
                  <details className="evrun" open>
                    <summary className="evrun-head">
                      <span className="evrun-title">系统事件</span>
                      <span className="evrun-time">与任务无关</span>
                      <span className="evrun-count">{grouped.system.length} 条</span>
                    </summary>
                    <div className="log">
                      {[...grouped.system].reverse().map((e) => <EventRow key={e.seq} e={e} />)}
                    </div>
                  </details>
                )}
                {!events.length && <div className="ev">（等待事件…）</div>}
              </div>
            </div>
          </>
        )}
        {tab === 'rag' && <RagPage refreshKey={refreshKey} events={events} onOpenChunk={(id) => { setFocusChunk(id); setTab('ontology') }} />}
        {tab === 'ontology' && <OntologyPage refreshKey={refreshKey} events={events} focusChunkId={focusChunk} chunks={stats.chunks ?? 0} />}
        {tab === 'chat' && <ChatPage refreshKey={refreshKey} />}
        {tab === 'mcp' && <McpPage />}
        {tab === 'settings' && <SettingsPage onChanged={bump} />}
      </div>
    </div>
  )
}

export default function App() {
  const [authed, setAuthed] = useState(!!getToken())

  useEffect(() => {
    const onLogout = () => setAuthed(false)
    window.addEventListener('auth:logout', onLogout)
    return () => window.removeEventListener('auth:logout', onLogout)
  }, [])

  const doLogout = () => { setToken(''); setAuthed(false) }

  return (
    <ToastProvider>
      {authed
        ? <Console onLogout={doLogout} />
        : <LoginPage onLogin={() => setAuthed(true)} />}
    </ToastProvider>
  )
}
