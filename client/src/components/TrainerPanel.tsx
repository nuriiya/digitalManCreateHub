import { useEffect, useState } from 'react'
import { api } from '../api'
import { useToast } from '../Toast'

interface Identity { id: number; name: string; category: string }
interface TrainSet { persona_role: string; n: number }

export default function TrainerPanel({ personas }: { personas: Identity[] }) {
  const { toast } = useToast()
  const [trainerId, setTrainerId] = useState<number | null>(null)
  const [trainSets, setTrainSets] = useState<TrainSet[]>([])
  const [targetId, setTargetId] = useState<number>(0)
  const [taskCount, setTaskCount] = useState<number>(10)
  const [baseline, setBaseline] = useState<any>(null)
  const [result, setResult] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [seed, setSeed] = useState({ kind: '规则', name: '', definition: '' })

  useEffect(() => {
    api<{ trainer_id: number; train_sets: TrainSet[] }>('/api/trainer')
      .then((r) => { setTrainerId(r.trainer_id); setTrainSets(r.train_sets || []) })
      .catch(() => { })
  }, [])

  const taskIds = () => Array.from({ length: taskCount }, (_, i) => i + 1)

  const doBaseline = async () => {
    if (!targetId) { toast('先选择要训练的数字人', 'err'); return }
    setBusy(true)
    try {
      const r = await api('/api/trainer/baseline', {
        method: 'POST',
        body: JSON.stringify({ identity_id: targetId, task_ids: taskIds() }),
      })
      setBaseline(r); setResult(null)
      toast('基线已建立', 'ok')
    } catch (e: any) { toast(`基线失败：${e.message}`, 'err') } finally { setBusy(false) }
  }

  const doIterate = async () => {
    if (!targetId) { toast('先选择要训练的数字人', 'err'); return }
    const seeds = seed.name.trim()
      ? [{ kind: seed.kind, name: seed.name.trim(), definition: seed.definition.trim() }]
      : []
    setBusy(true)
    try {
      const r = await api('/api/trainer/iterate', {
        method: 'POST',
        body: JSON.stringify({ identity_id: targetId, task_ids: taskIds(), ontology_seeds: seeds }),
      })
      setResult(r)
      toast('训练迭代完成', 'ok')
    } catch (e: any) { toast(`训练失败：${e.message}`, 'err') } finally { setBusy(false) }
  }

  const rate = (x: number | null | undefined) => (x == null ? '—' : `${(x * 100).toFixed(0)}%`)

  return (
    <div className="card">
      <h3>Pipeline 训练师</h3>
      <div className="desc">
        找训练集 → 建基线 → 迭代 pipeline → 更新数字人。训练师 #{trainerId ?? '…'} ·
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
        <label className="field"><span>训练题数</span>
          <input type="number" min={1} max={30} value={taskCount}
            onChange={(e) => setTaskCount(Number(e.target.value) || 10)} />
        </label>
      </div>

      <div className="btnrow" style={{ marginTop: 10 }}>
        <button className="btn green" onClick={doBaseline} disabled={busy}>{busy ? '跑题中…' : '① 建基线'}</button>
        {baseline && <span className="note">基线通过率：<b>{rate(baseline.pass_rate)}</b>（{baseline.pass}/{baseline.total}）</span>}
      </div>

      <div style={{ borderTop: '1px solid var(--border)', paddingTop: 10, marginTop: 10 }}>
        <b className="note">② 给数字人装配本体（训练师教学）</b>
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
        <div className="btnrow" style={{ marginTop: 8 }}>
          <button className="btn green" onClick={doIterate} disabled={busy}>{busy ? '训练中…' : '③ 训练迭代（装配 + 重测）'}</button>
          {result && (
            <span className="note">
              迭代后：<b>{rate(result.after?.pass_rate)}</b> · 提升：
              <b style={{ color: (result.improvement ?? 0) > 0 ? '#0F6E56' : '#993C1D' }}>
                {result.improvement != null ? `${result.improvement > 0 ? '+' : ''}${(result.improvement * 100).toFixed(0)}%` : '—'}
              </b> · 装配本体 {result.added_ontology} 条
            </span>
          )}
        </div>
      </div>

      {result && (
        <div style={{ marginTop: 10 }}>
          <b className="note">逐题对比</b>
          <div className="note" style={{ maxHeight: 200, overflowY: 'auto', fontFamily: 'var(--font-mono)', fontSize: 12 }}>
            {result.baseline?.results?.map((b: any, i: number) => {
              const a = result.after?.results?.[i]
              const changed = a && a.verdict !== b.verdict
              return (
                <div key={b.task_id}>
                  task {b.task_id}：{b.verdict} → {a?.verdict ?? '—'}{changed ? '（变化）' : ''}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
