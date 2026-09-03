import { useEffect, useState } from 'react'
import {
  getStats, getDocuments, getChunks, getChunk, search, deleteDocument,
  deleteChunk, deleteChunks, type EventItem,
} from '../api'
import { useToast } from '../Toast'

interface Props {
  refreshKey: number
  events: EventItem[]
  onOpenChunk: (id: number) => void
}

export default function RagPage({ refreshKey, events, onOpenChunk }: Props) {
  const { toast } = useToast()
  const [stats, setStats] = useState<any>({})
  const [docs, setDocs] = useState<any[]>([])
  const [page, setPage] = useState(1)
  const [chunks, setChunks] = useState<any>(null)
  const [detail, setDetail] = useState<any>(null)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<any[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [selected, setSelected] = useState<Set<number>>(new Set())

  const reload = () => {
    getStats().then(setStats)
    getDocuments().then((r) => setDocs(r.documents))
  }
  useEffect(reload, [refreshKey])
  useEffect(() => {
    getChunks(page).then((r) => {
      setChunks(r)
      setSelected(new Set())  // selection is page-scoped, reset on page change
    })
  }, [page, refreshKey])

  const reloadChunks = () => {
    setSelected(new Set())
    if (detail && !chunks?.items?.some((c: any) => c.id === detail.id)) setDetail(null)
    getChunks(page).then(setChunks)
    reload()
  }

  const doSearch = async () => {
    if (!query.trim()) return
    setSearching(true)
    try { setResults((await search(query, 5)).results) } finally { setSearching(false) }
  }

  // ---- deletion: documents ----
  const doDeleteDoc = async (d: any) => {
    if (!confirm(`删除文档「${d.name}」？其 ${d.chunk_count} 个 chunks 及关联证据将一并删除。`)) return
    try {
      await deleteDocument(d.id)
      toast(`文档已删除：${d.name}`, 'ok')
      reloadChunks()
    } catch (e: any) { toast(e.message, 'err') }
  }

  // ---- deletion: chunks (single + batch) ----
  const toggle = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }
  const allChecked = !!chunks?.items?.length &&
    chunks.items.every((c: any) => selected.has(c.id))
  const toggleAll = () => {
    setSelected(allChecked ? new Set()
      : new Set(chunks.items.map((c: any) => c.id)))
  }

  const doDeleteChunk = async (id: number) => {
    if (!confirm(`删除 chunk #${id}？其关联证据（mentions）将一并删除。`)) return
    try {
      await deleteChunk(id)
      toast(`chunk #${id} 已删除`, 'ok')
      reloadChunks()
    } catch (e: any) { toast(e.message, 'err') }
  }

  const doDeleteSelected = async () => {
    const ids = [...selected]
    if (!ids.length) return
    if (!confirm(`删除所选 ${ids.length} 个 chunks？关联证据将一并删除。`)) return
    try {
      const r = await deleteChunks(ids)
      toast(`已删除 ${r.deleted} 个 chunks`, 'ok')
      reloadChunks()
    } catch (e: any) { toast(e.message, 'err') }
  }

  return (
    <div>
      <div className="grid cols4" style={{ marginBottom: 16 }}>
        <div className="card stat"><div className="num">{stats.documents ?? 0}</div><div className="lbl">文档</div></div>
        <div className="card stat"><div className="num">{stats.chunks ?? 0}</div><div className="lbl">chunks</div></div>
        <div className="card stat"><div className="num">{stats.pending ?? 0}</div><div className="lbl">本体候选</div></div>
        <div className="card stat"><div className="num">{stats.approved ?? 0}</div><div className="lbl">已批准本体</div></div>
      </div>

      <div className="grid cols2">
        <div className="card">
          <h3>文档库</h3>
          <table className="tbl">
            <thead><tr><th>文档</th><th>chunks</th><th>整篇摘要</th><th>操作</th></tr></thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.id}>
                  <td>{d.name}</td>
                  <td>{d.chunk_count}</td>
                  <td className="note" style={{ maxWidth: 220 }}>{(d.doc_summary || '').slice(0, 80)}</td>
                  <td>
                    <button className="btn ghost small red" onClick={() => doDeleteDoc(d)}>删除</button>
                  </td>
                </tr>
              ))}
              {!docs.length && <tr><td colSpan={4} className="note">暂无数据 — 在「入库」页触发导入</td></tr>}
            </tbody>
          </table>
        </div>

        <div className="card">
          <h3>近邻检索测试</h3>
          <div className="desc">对 chunk 摘要向量做余弦检索，命中后回取原文（模拟 RAG 查询链路）。</div>
          <div className="searchbox">
            <input value={query} placeholder="输入查询，如：报销 流程"
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && doSearch()} />
            <button className="btn" onClick={doSearch} disabled={searching}>检索</button>
          </div>
          {results?.map((r) => (
            <div className="result" key={r.chunk_id}>
              <div>#{r.chunk_id} · doc {r.doc_id} · <span className="score">{r.score}</span></div>
              <div className="summary">{r.summary}</div>
              <div className="snippet">{r.text.slice(0, 120)}…</div>
            </div>
          ))}
          {results && !results.length && <div className="note">无结果</div>}
        </div>
      </div>

      <div className="card">
        <h3>Chunk 明细（原文 · 摘要 · 标签）</h3>
        <div className="btnrow" style={{ marginBottom: 8 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
            <input type="checkbox" checked={allChecked} onChange={toggleAll} /> 全选本页
          </label>
          <button className="btn ghost small red" disabled={!selected.size}
            onClick={doDeleteSelected}>全选删除（{selected.size}）</button>
        </div>
        <table className="tbl">
          <thead><tr><th></th><th>ID</th><th>doc</th><th>seq</th><th>摘要</th><th>标签</th><th>原文</th><th>操作</th></tr></thead>
          <tbody>
            {chunks?.items?.map((c: any) => (
              <tr key={c.id} className="clickable">
                <td onClick={(e) => e.stopPropagation()}>
                  <input type="checkbox" checked={selected.has(c.id)} onChange={() => toggle(c.id)} />
                </td>
                <td onClick={() => getChunk(c.id).then(setDetail)}>{c.id}</td>
                <td onClick={() => getChunk(c.id).then(setDetail)}>{c.doc_id}</td>
                <td onClick={() => getChunk(c.id).then(setDetail)}>{c.seq}</td>
                <td style={{ maxWidth: 220 }} onClick={() => getChunk(c.id).then(setDetail)}>{c.summary}</td>
                <td onClick={() => getChunk(c.id).then(setDetail)}>{(c.tags || []).join(' / ')}</td>
                <td className="note" style={{ maxWidth: 240 }} onClick={() => getChunk(c.id).then(setDetail)}>{c.text.slice(0, 60)}…</td>
                <td onClick={(e) => e.stopPropagation()}>
                  <button className="btn ghost small red" onClick={() => doDeleteChunk(c.id)}>删除</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {chunks && (
          <div className="btnrow" style={{ marginTop: 10 }}>
            <button className="btn ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button>
            <span className="note">第 {chunks.page} 页 / 共 {Math.max(1, Math.ceil(chunks.total / chunks.page_size))} 页（{chunks.total} 条）</span>
            <button className="btn ghost" disabled={page * chunks.page_size >= chunks.total} onClick={() => setPage(page + 1)}>下一页</button>
          </div>
        )}
      </div>

      {detail && (
        <div className="card">
          <h3>Chunk #{detail.id} · {detail.doc_name} · seq {detail.seq}</h3>
          <div className="note" style={{ marginBottom: 8 }}>
            摘要：{detail.summary} ｜ 标签：{(detail.tags || []).join(' / ')}
          </div>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 13, margin: 0 }}>{detail.text}</pre>
          <div className="btnrow" style={{ marginTop: 10 }}>
            <button className="btn ghost" onClick={() => setDetail(null)}>收起</button>
            <button className="btn ghost" onClick={() => onOpenChunk(detail.id)}>在本体图谱中查看</button>
            <button className="btn ghost small red" onClick={() => doDeleteChunk(detail.id)}>删除此 chunk</button>
          </div>
        </div>
      )}
    </div>
  )
}
