import { useState } from 'react'
import type { JobEvent } from '../api'

/**
 * 对话明细（实验分支 E1）：一次普通对话里，模型每一步「收到什么 / 回了什么 /
 * 调了什么工具 / 结果如何」按发生顺序列出。默认收起，点一下展开。
 */
export default function ChatTraceCard({ events }: { events: JobEvent[] }) {
  const [open, setOpen] = useState(false)
  if (!events.length) return null
  const nCall = events.filter((e) => e.type === 'llm.call').length
  const nTool = events.filter((e) => e.type === 'tool.exec').length

  return (
    <div className="ppc" style={{ marginTop: 6 }}>
      <button className="ppc-stage-row" onClick={() => setOpen(!open)}>
        <span className="ppc-ico">💬</span>
        <span className="ppc-label">
          对话明细 —— {nCall} 次模型调用{nTool ? ` · ${nTool} 次工具调用` : ''}
        </span>
        <span className="ppc-chev">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="ppc-detail" style={{ marginLeft: 6 }}>
          {events.map((e) => (
            <div key={e.seq}
              className={`ppc-ev ${e.type === 'llm.call' ? 'call'
                : e.type === 'tool.exec' ? 'tool' : 'reply'}`}>
              <div className="ppc-ev-head">
                {e.type === 'llm.call' ? '📤 发给'
                  : e.type === 'tool.exec'
                    ? (e.payload?.ok ? '🔧 工具（成功）' : '🔧 工具（失败）')
                    : '📥 来自'}
                <b> {e.type === 'tool.exec'
                  ? (e.payload?.name || '动作')
                  : (e.payload?.model || 'LLM')}</b>
                <span className="ppc-ev-len">
                  {e.type === 'llm.call'
                    ? `（${e.payload?.prompt_len ?? '?'} 字提示）`
                    : e.type === 'tool.exec'
                      ? `（${e.payload?.ok ? '已执行' : (e.payload?.reason || '被拒绝')}）`
                      : `（${String(e.payload?.reply_preview || '').length} 字预览）`}
                </span>
              </div>
              <pre className="ppc-ev-body">
                {String(e.payload?.prompt_preview || e.payload?.reply_preview
                  || e.payload?.result_preview || '（无内容）')}
              </pre>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
