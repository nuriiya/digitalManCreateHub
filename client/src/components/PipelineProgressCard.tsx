import { useMemo, useState } from 'react'
import { getJobEvents, type JobEvent } from '../api'

/**
 * pipeline 进度手风琴（design §23.7）：
 * 消息 content 的 @@PIPELINE_PROGRESS@@{json} 标记渲染成阶段列表 ——
 * 每阶段可点击展开，懒加载该阶段的 LLM 具体交互（llm.call / llm.reply，
 * 按 pipeline.node 事件的 seq 区间归属到节点），与「任务与事件」页同源。
 */
export interface ProgressStage {
  key: string; label: string; state: 'done' | 'running' | 'pending' | 'failed'
  seqStart: number; seqEnd: number   // llm 事件归属区间 [start, end)
}
export interface PipelineProgressData {
  v: number; pipelineId: number; name: string
  runId: number; jobId: number; runStatus: string
  stages: ProgressStage[]
}

const ST_ICON: Record<string, string> = {
  done: '✅', running: '⏳', pending: '⏸', failed: '❌',
}
const ST_TEXT: Record<string, string> = {
  done: '完成', running: '运行中', pending: '待执行', failed: '失败',
}

export default function PipelineProgressCard({ raw }: { raw: string }) {
  const data = useMemo<PipelineProgressData | null>(() => {
    try {
      return JSON.parse(raw.slice('@@PIPELINE_PROGRESS@@'.length)) as PipelineProgressData
    } catch { return null }
  }, [raw])
  const [open, setOpen] = useState<number | null>(null)   // 展开的 stage index
  const [cache, setCache] = useState<Record<number, JobEvent[]>>({})

  if (!data) return <div className="note">进度数据解析失败</div>
  const runState = data.runStatus === 'done' ? '✅ 完成'
    : data.runStatus === 'running' ? '⏳ 运行中'
      : data.runStatus === 'blocked' ? '❌ 被复核门拦下' : `❌ ${data.runStatus}`

  const toggle = async (i: number) => {
    if (open === i) { setOpen(null); return }
    setOpen(i)
    if (cache[i]) return
    try {
      const r = await getJobEvents(data.jobId)
      const evs = (r.events ?? []).filter((e: JobEvent) =>
        (e.type === 'llm.call' || e.type === 'llm.reply') &&
        e.seq >= (data.stages[i]?.seqStart ?? 0) &&
        e.seq < (data.stages[i]?.seqEnd ?? Number.MAX_SAFE_INTEGER))
      setCache((c) => ({ ...c, [i]: evs.sort((a: JobEvent, b: JobEvent) => a.seq - b.seq) }))
    } catch {
      setCache((c) => ({ ...c, [i]: [] }))
    }
  }

  return (
    <div className="ppc">
      <div className="ppc-head">
        🔗 <b>{data.name}</b>
        <span className="ppc-run">（run #{data.runId} · {runState}）</span>
      </div>
      <div className="ppc-stages">
        {data.stages.map((st, i) => (
          <div key={st.key + i} className={`ppc-stage ${st.state} ${open === i ? 'open' : ''}`}>
            <button className="ppc-stage-row" onClick={() => toggle(i)}>
              <span className="ppc-idx">{st.state === 'running' ? `${i + 1}.⏳` : `${i + 1}.`}</span>
              <span className="ppc-ico">{ST_ICON[st.state] ?? '⏸'}</span>
              <span className="ppc-label">{st.label}</span>
              <span className="ppc-chev">{open === i ? '▾' : '▸'}</span>
            </button>
            {open === i && (
              <div className="ppc-detail">
                <div className="ppc-detail-title">该阶段的大模型交互（{ST_TEXT[st.state]}）</div>
                {!cache[i] && <div className="ppc-loading">加载对话内容…</div>}
                {cache[i] && cache[i].length === 0 && (
                  <div className="ppc-empty">该阶段暂无 LLM 交互记录</div>
                )}
                {cache[i]?.map((e) => (
                  <div key={e.seq} className={`ppc-ev ${e.type === 'llm.call' ? 'call' : 'reply'}`}>
                    <div className="ppc-ev-head">
                      {e.type === 'llm.call' ? '📤 发给' : '📥 来自'}
                      <b> {e.payload?.model || 'LLM'}</b>
                      <span className="ppc-ev-len">
                        {e.type === 'llm.call'
                          ? `（${e.payload?.prompt_len ?? '?'} 字提示）`
                          : `（${String(e.payload?.reply_preview || '').length} 字预览）`}
                      </span>
                    </div>
                    <pre className="ppc-ev-body">
                      {String(e.payload?.prompt_preview || e.payload?.reply_preview || '（无内容）')}
                    </pre>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
      {data.runStatus === 'done' && (
        <div className="ppc-done">✅ 全部阶段完成 —— DFMEA 表已写入，可下载 Excel 报告。</div>
      )}
    </div>
  )
}
