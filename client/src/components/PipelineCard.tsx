import { useCallback, useEffect, useState } from 'react'
import {
  getPipeline, validatePipeline, approvePipeline, runPipeline,
  getPipelineRuns, getDfmeaRows, getDfmeaPending, getIdentities,
  type Pipeline, type PipelineRun, type DfmeaRow, type DfmeaPending,
} from '../api'
import { useToast } from '../Toast'

/** 对话页内联的 pipeline 结果卡片（design §15）。
 *
 *  对话页是「一句话需求 → 生成 → 审批 → 运行 → 看产出」的**唯一入口**：
 *  生成后不再只弹 toast 让用户去「编排」页，而是就地展示拓扑摘要、
 *  审批/运行按钮，以及运行产出的 DFMEA 表（逐格来源 + ai_new 待确认清单）。
 */

const SRC_STYLE: Record<string, { bg: string; fg: string; label: string }> = {
  history: { bg: 'rgba(123,175,255,0.16)', fg: '#7BAFFF', label: '历史库' },
  table: { bg: 'rgba(93,202,168,0.16)', fg: '#5DCAA8', label: '查表' },
  expert: { bg: 'rgba(239,159,39,0.16)', fg: '#EF9F27', label: '专家' },
  ai_inferred: { bg: 'rgba(176,176,186,0.16)', fg: '#B0B0BA', label: 'AI 推断' },
  ai_new: { bg: 'rgba(255,107,107,0.18)', fg: '#FF6B6B', label: 'AI 生成·待确认' },
}

function srcOf(raw?: string) {
  if (!raw) return null
  const s = String(raw).trim()
  const base = s.split('#')[0]
  const meta = SRC_STYLE[base]
  if (!meta) return { ...SRC_STYLE.ai_inferred, label: s }
  const extra = s.includes('#') ? ` ${s.split('#')[1]}` : ''
  return { ...meta, label: meta.label + extra }
}

const AP_COLOR: Record<string, string> = { H: '#FF6B6B', M: '#EF9F27', L: '#5DCAA8' }

export default function PipelineCard({ pipelineId }: { pipelineId: number }) {
  const [p, setP] = useState<Pipeline | null>(null)
  const [names, setNames] = useState<Record<number, string>>({})
  const [runs, setRuns] = useState<PipelineRun[]>([])
  const [rows, setRows] = useState<DfmeaRow[]>([])
  const [pending, setPending] = useState<DfmeaPending[]>([])
  const [busy, setBusy] = useState(false)
  const { toast } = useToast()

  const load = useCallback(async () => {
    try {
      const [pr, ids] = await Promise.all([getPipeline(pipelineId), getIdentities()])
      setP(pr.pipeline)
      const m: Record<number, string> = {}
      for (const it of ids.identities ?? []) m[it.id] = it.name
      setNames(m)
      const rs = await getPipelineRuns(pipelineId)
      const list = rs.runs ?? []
      setRuns(list)
      if (list[0]) {
        const [rr, pp] = await Promise.all([
          getDfmeaRows(list[0].id), getDfmeaPending(list[0].id)])
        setRows(rr.rows ?? [])
        setPending(pp.pending ?? [])
      }
    } catch { /* 静默：卡片缺失不影响对话 */ }
  }, [pipelineId])

  useEffect(() => { load() }, [load])

  const last = runs[0]
  const running = last?.status === 'running'
  // 运行中轮询（只轮询本卡片，4s 一次，结束即停）
  useEffect(() => {
    if (!running) return
    const t = setTimeout(load, 4000)
    return () => clearTimeout(t)
  }, [running, load, runs])

  const guard = async (fn: () => Promise<void>) => {
    setBusy(true)
    try { await fn() } catch (e: any) { toast(e.message, 'err') }
    finally { setBusy(false) }
  }
  const doValidate = () => guard(async () => {
    const r = await validatePipeline(pipelineId)
    toast(r.errors?.length ? `校验未过：${r.errors.join('；')}` : '校验通过',
      r.errors?.length ? 'err' : 'ok')
  })
  const doApprove = () => guard(async () => {
    await approvePipeline(pipelineId)
    toast('已批准（打标签入本体库）', 'ok')
    await load()
  })
  const doRun = () => guard(async () => {
    const r = await runPipeline(pipelineId)
    toast(`已启动运行 job #${r.job_id}`, 'ok')
    await load()
  })

  if (!p) return <div className="note" style={{ padding: 8 }}>加载 pipeline #{pipelineId}…</div>

  const approved = p.status === 'approved'
  const statusColor = approved ? '#5DCAA8' : '#EF9F27'

  return (
    <div className="pipe-card" style={{
      border: '1px solid rgba(156,124,255,0.45)', borderRadius: 10,
      padding: 12, margin: '8px 0', background: 'rgba(42,37,64,0.35)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <b>🔗 {p.name}</b>
        <span style={{
          fontSize: 11, padding: '1px 7px', borderRadius: 8,
          color: statusColor, border: `1px solid ${statusColor}`,
        }}>{p.status}</span>
        <span className="note" style={{ marginLeft: 'auto' }}>
          {p.nodes.length} 节点 / {p.relations.length} 关系
        </span>
      </div>

      {/* 拓扑摘要（紧凑；完整图看「编排」页） */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
        {p.nodes.map((n) => (
          <span key={n.id} title={n.step_name ?? undefined} style={{
            fontSize: 11.5, padding: '2px 8px', borderRadius: 6,
            border: '1px solid rgba(156,124,255,0.35)',
            background: n.kind === 'deterministic' ? 'rgba(93,202,168,0.10)' : 'rgba(156,124,255,0.10)',
          }}>
            {n.node_key}
            <span className="note"> · {names[n.persona_id ?? -1] || '未绑定'}</span>
          </span>
        ))}
      </div>

      <div className="btnrow" style={{ marginBottom: 8 }}>
        <button className="btn ghost small" onClick={doValidate} disabled={busy}>校验</button>
        {!approved && (
          <button className="btn green small" onClick={doApprove} disabled={busy}>批准</button>
        )}
        {approved && (
          <button className="btn green small" onClick={doRun} disabled={busy || running}>
            {running ? '运行中…' : '运行'}
          </button>
        )}
      </div>

      {/* 运行状态 */}
      {last && (
        <div className="note" style={{ marginBottom: 6 }}>
          最近运行 run#{last.id} · 状态 <b style={{
            color: last.status === 'done' ? '#5DCAA8'
              : last.status === 'blocked' ? '#FF6B6B' : '#EF9F27',
          }}>{last.status}</b>
          {running && '（每 4s 自动刷新）'}
        </div>
      )}

      {/* DFMEA 产出 */}
      {rows.length > 0 && (
        <>
          <div style={{ fontWeight: 600, margin: '8px 0 4px' }}>
            DFMEA 表（{rows.length} 行）
          </div>
          <div style={{ overflowX: 'auto', maxHeight: 420, overflowY: 'auto' }}>
            <table style={{ borderCollapse: 'collapse', fontSize: 11.5, minWidth: 900 }}>
              <thead>
                <tr style={{ background: 'rgba(255,255,255,0.04)' }}>
                  {['部件', '失效模式', '后果', 'S', '原因', 'O', '现有控制', 'D', 'AP', '措施'].map((h) => (
                    <th key={h} style={{
                      padding: '4px 6px', textAlign: 'left',
                      borderBottom: '1px solid rgba(255,255,255,0.15)', whiteSpace: 'nowrap',
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const cell = (v: any, f: string) => {
                    const b = srcOf(r.sources?.[f])
                    return (
                      <td style={{ padding: '4px 6px', borderBottom: '1px solid rgba(255,255,255,0.07)', verticalAlign: 'top' }}>
                        <div>{v === null || v === undefined || v === '' ? '—' : String(v)}</div>
                        {b && (
                          <span style={{
                            fontSize: 10, padding: '0 5px', borderRadius: 6,
                            color: b.fg, background: b.bg, whiteSpace: 'nowrap',
                          }}>{b.label}</span>
                        )}
                      </td>
                    )
                  }
                  return (
                    <tr key={r.id}>
                      {cell(r.part, 'part')}
                      {cell(r.failure_mode, 'failure_mode')}
                      {cell(r.failure_effect, 'failure_effect')}
                      {cell(r.severity, 'severity')}
                      {cell(r.failure_cause, 'failure_cause')}
                      {cell(r.occurrence, 'occurrence')}
                      {cell(r.detection_control, 'detection_control')}
                      {cell(r.detection, 'detection')}
                      <td style={{ padding: '4px 6px', borderBottom: '1px solid rgba(255,255,255,0.07)' }}>
                        <b style={{ color: AP_COLOR[r.ap || ''] || 'inherit' }}>{r.ap || '—'}</b>
                      </td>
                      {cell(r.action, 'action')}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* 待人工确认清单（ai_new 必须可被机器筛出） */}
      {pending.length > 0 && (
        <div style={{
          marginTop: 10, padding: 8, borderRadius: 8,
          border: '1px solid rgba(255,107,107,0.5)', background: 'rgba(255,107,107,0.07)',
        }}>
          <b style={{ color: '#FF6B6B' }}>⚠ 待人工确认 {pending.length} 项</b>
          <div className="note" style={{ marginTop: 4 }}>
            {pending.map((x) => (
              <div key={x.row_id}>
                行 #{x.row_id} · {x.part} · {x.failure_mode} → 需确认：{x.fields.join('、')}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
