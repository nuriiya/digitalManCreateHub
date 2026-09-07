import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useToast } from '../Toast'

interface Identity { id: number; name: string; category: string }
interface TrainSet { persona_role: string; n: number }
interface Task { id: number; task_key: string; entry_point: string }
interface JobProgress {
  job_id: string; status: string; stage: string; done: number; total: number
  msg: string; result?: any; error?: string
}

export default function TrainerPanel({ personas }: { personas: Identity[] }) {
  const { toast } = useToast()
  const [trainerId, setTrainerId] = useState<number | null>(null)
  const [trainSets, setTrainSets] = useState<TrainSet[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [targetId, setTargetId] = useState<number>(0)
  const [taskCount, setTaskCount] = useState<number>(12)
  const [provider, setProvider] = useState<'llm' | 'llm2'>('llm')
  const [busy, setBusy] = useState(false)
  const [job, setJob] = useState<JobProgress | null>(null)
  const [result, setResult] = useState<any>(null)
  const [seed, setSeed] = useState({ kind: '规则', name: '', definition: '' })
  const pollRef = useRef<number | null>(null)

  useEffect(() => {
    api<{ trainer_id: number; train_sets: TrainSet[] }>('/api/trainer')
      .then((r) => { setTrainerId(r.trainer_id); setTrainSets(r.train_sets || []) })
      .catch(() => { })
    api<{ tasks: Task[] }>('/api/trainer/tasks?persona_role=code_engineer')
      .then((r) => setTasks(r.tasks || []))
      .catch(() => { })
    return () => { if (pollRef.current) window.clearInterval(pollRef.current) }
  }, [])

  const taskIds = () => tasks.slice(0, taskCount).map((t) => t.id)

  const stopPolling = () => {
    if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null }
  }

  const startPolling = (jobId: string) => {
    stopPolling()
    pollRef.current = window.setInterval(async () => {
      try {
        const p = await api<JobProgress>(`/api/trainer/job/${jobId}`)
        setJob(p)
        if (p.status === 'done') { setResult(p.result); setBusy(false); stopPolling(); toast('训练完成', 'ok') }
        else if (p.status === 'error') { setBusy(false); stopPolling(); toast(`训练失败：${p.error}`, 'err') }
      } catch { /* ignore */ }
    }, 1500)
  }

  const doAuto = async () => {
    if (!targetId) { toast('先选择要训练的数字人', 'err'); return }
    setBusy(true); setResult(null); setJob(null)
    try {
      const r = await api<{ job_id: string }>('/api/trainer/auto', {
        method: 'POST',
        body: JSON.stringify({ identity_id: targetId, task_ids: taskIds(), provider }),
      })
      toast('自动迭代已启动', 'ok')
      startPolling(r.job_id)
    } catch (e: any) { toast(`启动失败：${e.message}`, 'err'); setBusy(false) }
  }

  const doBaseline = async () => {
    if (!targetId) { toast('先选择要训练的数字人', 'err'); return }
    setBusy(true); setResult(null); setJob(null)
    try {
      const r = await api<{ job_id: string }>('/api/trainer/run', {
        method: 'POST',
        body: JSON.stringify({ identity_id: targetId, task_ids: taskIds(), provider, ontology_seeds: [] }),
      })
      toast('基线测算已启动', 'ok')
      startPolling(r.job_id)
    } catch (e: any) { toast(`启动失败：${e.message}`, 'err'); setBusy(false) }
  }

  const doTeach = async () => {
    if (!targetId) { toast('先选择要训练的数字人', 'err'); return }
    const seeds = seed.name.trim()
      ? [{ kind: seed.kind, name: seed.name.trim(), definition: seed.definition.trim() }]
      : []
    if (!seeds.length) { toast('请填写本体名和定义', 'err'); return }
    setBusy(true); setResult(null); setJob(null)
    try {
      const r = await api<{ job_id: string }>('/api/trainer/run', {
        method: 'POST',
        body: JSON.stringify({ identity_id: targetId, task_ids: taskIds(), provider, ontology_seeds: seeds }),
      })
      toast('训练迭代已启动', 'ok')
      startPolling(r.job_id)
    } catch (e: any) { toast(`启动失败：${e.message}`, 'err'); setBusy(false) }
  }

  const rate = (x: number | null | undefined) => (x == null ? '—' : `${(x * 100).toFixed(0)}%`)
  const pct = job && job.total > 0 ? Math.round((job.done / job.total) * 100) : 0

  return (
    <div className="card">
      <h3>Pipeline 训练师</h3>
      <div className="desc">
        找训练集 → 建基线 → 自动归因失败 → 装配本体 → 迭代。训练师 #{trainerId ?? '…'} ·
        训练集：{trainSets.map((s) => `${s.persona_role}(${s.n})`).join('、') || '暂无'}
      </div>

      <div className="grid cols2" style={{ marginTop: 10 }}>
        <label className="field"><span>训练目标数字人</span>
          <select value={targetId} onChange={(e) => setTargetId(Number(e.target.value))}>
            <option value={0}>（选择数字人）</option>
            {personas.map((p) => (
              <option key={p.id} value={p.id}>{p.name}（{p.category === 'general' ? '通用' : '专业'}）</option>
            ))}
          </select>
        </label>
        <label className="field"><span>训练题数（项目题 {tasks.length} 道）</span>
          <input type="number" min={1} max={tasks.length} value={taskCount}
            onChange={(e) => setTaskCount(Number(e.target.value) || 12)} />
        </label>
        <label className="field"><span>解题模型通道</span>
          <select value={provider} onChange={(e) => setProvider(e.target.value as 'llm' | 'llm2')}>
            <option value="llm">DeepSeek V4 Flash</option>
            <option value="llm2">GLM 5.2</option>
          </select>
        </label>
      </div>

      <div className="btnrow" style={{ marginTop: 10 }}>
        <button className="btn green" onClick={doAuto} disabled={busy}>① 自动迭代训练（推荐）</button>
        <button className="btn ghost" onClick={doBaseline} disabled={busy}>② 只测基线</button>
        <button className="btn orange" onClick={doTeach} disabled={busy}>③ 手工装配本体</button>
      </div>

      {/* 进度条 */}
      {job && (
        <div style={{ marginTop: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
            <span className="status-pill running">{job.status}</span>
            <span className="note" style={{ flex: 1, marginLeft: 8 }}>{job.msg || '进行中…'}</span>
            <span className="note">{job.done}/{job.total}</span>
          </div>
          <div className="progressbar"><div style={{ width: `${pct}%` }} /></div>
        </div>
      )}

      {/* 训练结果 */}
      {result && (
        <div style={{ marginTop: 14 }}>
          <div className="note" style={{ marginBottom: 8 }}>
            <b>基线 {rate(result.baseline_rate)}</b>
            {' → '}<b style={{ color: (result.total_improvement ?? 0) > 0 ? '#0F6E56' : '#993C1D' }}>最终 {rate(result.final_rate)}</b>
            {' · 总提升 '}
            <b style={{ color: (result.total_improvement ?? 0) > 0 ? '#0F6E56' : '#993C1D' }}>
              {result.total_improvement != null ? `${result.total_improvement > 0 ? '+' : ''}${(result.total_improvement * 100).toFixed(0)}%` : '—'}
            </b>
            {' · 本体 '}{result.ontology?.length ?? 0} 条
          </div>

          {result.rounds?.map((r: any) => (
            <div key={r.round} className="note" style={{ borderLeft: '2px solid var(--border)', paddingLeft: 8, marginBottom: 6 }}>
              <b>第 {r.round} 轮</b>：{rate(r.before)} → {rate(r.after)}
              <span style={{ color: r.improvement > 0 ? '#0F6E56' : '#993C1D' }}>
                {' '}({r.improvement > 0 ? '+' : ''}{(r.improvement * 100).toFixed(0)}%)
              </span>
              {' · 归因 '}{r.fails?.length ?? 0} 道失败 · 装配 {r.added} 条本体
              <div style={{ marginTop: 2, fontSize: 11 }}>
                {r.seeds?.map((s: any) => <span key={s.name} style={{ marginRight: 8 }}>〔{s.kind}〕{s.name}</span>)}
              </div>
            </div>
          ))}

          {result.rounds && result.rounds.length === 0 && (
            <div className="note">基线全对，无需迭代（题目对该模型太简单，建议换更难训练集）。</div>
          )}

          {result.ontology?.length > 0 && (
            <div style={{ marginTop: 8, maxHeight: 160, overflowY: 'auto' }}>
              <b className="note">当前本体</b>
              {result.ontology.map((o: any) => (
                <div key={o.id} className="note" style={{ fontSize: 11 }}>〔{o.kind}〕{o.name}：{o.definition}</div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* 手工装配本体 */}
      <div style={{ borderTop: '1px solid var(--border)', paddingTop: 10, marginTop: 12 }}>
        <b className="note">手工装配本体（也可用「① 自动迭代」让训练师自己归因）</b>
        <div className="grid cols3">
          <label className="field"><span>类型</span>
            <select value={seed.kind} onChange={(e) => setSeed({ ...seed, kind: e.target.value })}>
              {['规则', '概念', '流程', '对象', '角色', '系统', '其他'].map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
          </label>
          <label className="field"><span>本体名</span>
            <input placeholder="如：边界条件处理" value={seed.name} onChange={(e) => setSeed({ ...seed, name: e.target.value })} />
          </label>
          <label className="field"><span>定义</span>
            <input placeholder="如：空输入/单元素/负数需显式处理" value={seed.definition} onChange={(e) => setSeed({ ...seed, definition: e.target.value })} />
          </label>
        </div>
      </div>
    </div>
  )
}
