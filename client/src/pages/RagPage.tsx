import { useEffect, useState } from 'react'
import {
  getStats, getDocuments, getChunks, getChunk, search, deleteDocument,
  deleteChunk, deleteChunks, getChunkTypes, createChunkType, updateChunkType,
  deleteChunkType, setChunkType,
  type EventItem, type ChunkType, type ChunkFilters,
} from '../api'
import { useToast } from '../Toast'

interface Props {
  refreshKey: number
  events: EventItem[]
  onOpenChunk: (id: number) => void
}

const CONF_OPTS = ['high', 'medium', 'low']
const CONF_LABEL: Record<string, string> = { high: '高', medium: '中', low: '低' }
const MAND_OPTS = [0, 1, 2]
const MAND_LABEL: Record<number, string> = { 0: '参考', 1: '建议', 2: '强制' }

/** 徽章配色由**强制等级**决定：2 强制=红 / 1 建议=橙 / 0 参考=灰（design §11.6）。 */
function typeColor(m: number | null | undefined) {
  if (m === 2) return { bg: '#3a1d1d', bd: '#d06060', fg: '#f09595' }
  if (m === 1) return { bg: '#3a2c14', bd: '#d98324', fg: '#fac775' }
  return { bg: '#23272f', bd: '#32363e', fg: '#8b8f98' }
}

function TypeBadge({ chunk, types }: { chunk: any; types: ChunkType[] }) {
  const t = types.find((x) => x.code === chunk.type)
  const label = t?.label || chunk.type || '未分类'
  const c = typeColor(chunk.type_mandatory)
  const title = `置信度 ${CONF_LABEL[chunk.type_confidence] || '—'}`
    + ` · 强制 ${MAND_LABEL[chunk.type_mandatory] ?? '—'}`
    + ` · 来源 ${chunk.type_source || '—'}`
  return (
    <span title={title} style={{
      display: 'inline-block', padding: '1px 6px', borderRadius: 6, fontSize: 12,
      background: c.bg, border: `1px solid ${c.bd}`, color: c.fg,
    }}>{label}</span>
  )
}

function Chip({ active, onClick, children }: any) {
  return (
    <button className={`btn small ${active ? '' : 'ghost'}`}
      style={{ marginRight: 4 }} onClick={onClick}>{children}</button>
  )
}

/** 类型词表管理（design §11.2：用户可自定义；builtin/unknown 不可删）。 */
function TypeManager({ types, toast, reload }: any) {
  const blank = {
    code: '', label: '', description: '',
    default_confidence: 'medium', default_mandatory: 0, priority: 10,
  }
  const [draft, setDraft] = useState<any>(blank)
  const [editId, setEditId] = useState<number | null>(null)
  const [editDraft, setEditDraft] = useState<any>({})

  const add = async () => {
    if (!draft.code.trim() || !draft.label.trim()) {
      toast('code 与 label 必填', 'err'); return
    }
    try {
      await createChunkType(draft)
      toast('类型已创建', 'ok')
      setDraft(blank)
      reload()
    } catch (e: any) { toast(e.message, 'err') }
  }
  const save = async (id: number) => {
    try { await updateChunkType(id, editDraft); toast('已保存', 'ok'); setEditId(null); reload() }
    catch (e: any) { toast(e.message, 'err') }
  }
  const del = async (t: ChunkType) => {
    if (!confirm(`删除类型「${t.label}」？已被 chunk 引用的类型无法删除。`)) return
    try { await deleteChunkType(t.id); toast('已删除', 'ok'); reload() }
    catch (e: any) { toast(e.message, 'err') }
  }
  const toggle = async (t: ChunkType) => {
    try {
      await updateChunkType(t.id, { status: t.status === 'active' ? 'disabled' : 'active' })
      toast(t.status === 'active' ? '已停用' : '已启用', 'ok'); reload()
    } catch (e: any) { toast(e.message, 'err') }
  }

  return (
    <div className="card">
      <h3>类型词表（用户可自定义）</h3>
      <div className="desc">
        type 是 chunk 的「性质」；它的两个维度 —— 置信度（有多准）与强制等级（能不能违反）
        —— 由词表默认值确定性裁决。预置类型可改说明/维度/停用，但不可删除。
      </div>
      <table className="tbl">
        <thead><tr>
          <th>类型</th><th>code</th><th>默认置信度</th><th>默认强制</th>
          <th>权重</th><th>状态</th><th>说明</th><th>操作</th>
        </tr></thead>
        <tbody>
          {types.map((t: ChunkType) => editId === t.id ? (
            <tr key={t.id}>
              <td><input value={editDraft.label ?? ''} style={{ width: 90 }}
                onChange={(e) => setEditDraft({ ...editDraft, label: e.target.value })} /></td>
              <td className="note">{t.code}</td>
              <td>
                <select value={editDraft.default_confidence ?? t.default_confidence}
                  onChange={(e) => setEditDraft({ ...editDraft, default_confidence: e.target.value })}>
                  {CONF_OPTS.map((c) => <option key={c} value={c}>{CONF_LABEL[c]}</option>)}
                </select>
              </td>
              <td>
                <select value={editDraft.default_mandatory ?? t.default_mandatory}
                  onChange={(e) => setEditDraft({ ...editDraft, default_mandatory: Number(e.target.value) })}>
                  {MAND_OPTS.map((m) => <option key={m} value={m}>{MAND_LABEL[m]}</option>)}
                </select>
              </td>
              <td><input type="number" value={editDraft.priority ?? t.priority} style={{ width: 62 }}
                onChange={(e) => setEditDraft({ ...editDraft, priority: Number(e.target.value) })} /></td>
              <td className="note">{t.status}</td>
              <td><input value={editDraft.description ?? (t.description || '')} style={{ width: 200 }}
                onChange={(e) => setEditDraft({ ...editDraft, description: e.target.value })} /></td>
              <td>
                <button className="btn small" onClick={() => save(t.id)}>保存</button>
                <button className="btn ghost small" onClick={() => { setEditId(null); setEditDraft({}) }}>取消</button>
              </td>
            </tr>
          ) : (
            <tr key={t.id}>
              <td><TypeBadge types={types} chunk={{ type: t.code, type_mandatory: t.default_mandatory }} /></td>
              <td className="note">{t.code}</td>
              <td>{CONF_LABEL[t.default_confidence] || t.default_confidence}</td>
              <td>{MAND_LABEL[t.default_mandatory] ?? t.default_mandatory}</td>
              <td>{t.priority}</td>
              <td className="note">{t.builtin ? '预置' : '自定义'} · {t.status === 'active' ? '启用' : '停用'}</td>
              <td className="note" style={{ maxWidth: 240 }}>{t.description}</td>
              <td>
                <button className="btn ghost small"
                  onClick={() => { setEditId(t.id); setEditDraft({}) }}>编辑</button>
                <button className="btn ghost small" onClick={() => toggle(t)}>
                  {t.status === 'active' ? '停用' : '启用'}
                </button>
                <button className="btn ghost small red" disabled={t.builtin}
                  title={t.builtin ? '预置类型不可删除' : ''}
                  onClick={() => del(t)}>删除</button>
              </td>
            </tr>
          ))}
          <tr>
            <td><input placeholder="显示名" value={draft.label} style={{ width: 90 }}
              onChange={(e) => setDraft({ ...draft, label: e.target.value })} /></td>
            <td><input placeholder="code" value={draft.code} style={{ width: 90 }}
              onChange={(e) => setDraft({ ...draft, code: e.target.value })} /></td>
            <td>
              <select value={draft.default_confidence}
                onChange={(e) => setDraft({ ...draft, default_confidence: e.target.value })}>
                {CONF_OPTS.map((c) => <option key={c} value={c}>{CONF_LABEL[c]}</option>)}
              </select>
            </td>
            <td>
              <select value={draft.default_mandatory}
                onChange={(e) => setDraft({ ...draft, default_mandatory: Number(e.target.value) })}>
                {MAND_OPTS.map((m) => <option key={m} value={m}>{MAND_LABEL[m]}</option>)}
              </select>
            </td>
            <td><input type="number" value={draft.priority} style={{ width: 62 }}
              onChange={(e) => setDraft({ ...draft, priority: Number(e.target.value) })} /></td>
            <td className="note">新增</td>
            <td><input placeholder="判定说明（会进 LLM prompt）" value={draft.description}
              style={{ width: 200 }}
              onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></td>
            <td><button className="btn small green" onClick={add}>添加类型</button></td>
          </tr>
        </tbody>
      </table>
    </div>
  )
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
  // design §11.6：三维筛选 + 词表
  const [filters, setFilters] = useState<ChunkFilters>({})
  const [types, setTypes] = useState<ChunkType[]>([])
  const [showTypes, setShowTypes] = useState(false)

  const reload = () => {
    getStats().then(setStats)
    getDocuments().then((r) => setDocs(r.documents))
    getChunkTypes().then((r: any) => setTypes(r.types || []))
  }
  useEffect(reload, [refreshKey])
  useEffect(() => {
    getChunks(page, 20, undefined, filters).then((r) => {
      setChunks(r)
      setSelected(new Set())  // selection is page-scoped, reset on page change
    })
  }, [page, refreshKey, filters])

  const reloadChunks = () => {
    setSelected(new Set())
    if (detail && !chunks?.items?.some((c: any) => c.id === detail.id)) setDetail(null)
    getChunks(page, 20, undefined, filters).then(setChunks)
    reload()
  }

  const setFilter = (patch: ChunkFilters) => {
    setPage(1)  // 改筛选回到第一页，避免页码越界
    setFilters((prev) => ({ ...prev, ...patch }))
  }
  const clearFilters = () => { setPage(1); setFilters({}) }

  const doSearch = async () => {
    if (!query.trim()) return
    setSearching(true)
    try { setResults((await search(query, 5, filters)).results) } finally { setSearching(false) }
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

  // ---- 用户终审：改单个 chunk 的 type（两维度按词表默认值重取）----
  const doSetChunkType = async (id: number, code: string) => {
    try {
      await setChunkType(id, code)
      toast('类型已更新（两维度按词表默认值重取）', 'ok')
      getChunk(id).then(setDetail)
      reloadChunks()
    } catch (e: any) { toast(e.message, 'err') }
  }

  const activeTypes = types.filter((t) => t.status === 'active')
  const filtered = !!(filters.type || filters.confidence || filters.mandatory !== undefined)

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
          <div className="desc">对 chunk 摘要向量做余弦检索，命中后回取原文（模拟 RAG 查询链路）。筛选条件同样作用于检索。</div>
          <div className="searchbox">
            <input value={query} placeholder="输入查询，如：报销 流程"
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && doSearch()} />
            <button className="btn" onClick={doSearch} disabled={searching}>检索</button>
          </div>
          {results?.map((r) => (
            <div className="result" key={r.chunk_id}>
              <div>#{r.chunk_id} · doc {r.doc_id} · <span className="score">{r.score}</span>
                {' '}<TypeBadge chunk={r} types={types} />
              </div>
              <div className="summary">{r.summary}</div>
              <div className="snippet">{r.text.slice(0, 120)}…</div>
            </div>
          ))}
          {results && !results.length && <div className="note">无结果</div>}
        </div>
      </div>

      <div className="card">
        <h3>Chunk 明细（原文 · 摘要 · 标签 · 类型）</h3>
        <div style={{ marginBottom: 6 }}>
          <span className="note" style={{ marginRight: 6 }}>类型</span>
          <Chip active={!filters.type} onClick={() => setFilter({ type: undefined })}>全部</Chip>
          {activeTypes.map((t) => (
            <Chip key={t.code} active={filters.type === t.code}
              onClick={() => setFilter({ type: t.code })}>{t.label}</Chip>
          ))}
        </div>
        <div style={{ marginBottom: 6 }}>
          <span className="note" style={{ marginRight: 6 }}>置信度</span>
          <Chip active={!filters.confidence} onClick={() => setFilter({ confidence: undefined })}>不限</Chip>
          {CONF_OPTS.map((c) => (
            <Chip key={c} active={filters.confidence === c}
              onClick={() => setFilter({ confidence: c })}>≥{CONF_LABEL[c]}</Chip>
          ))}
        </div>
        <div style={{ marginBottom: 8 }}>
          <span className="note" style={{ marginRight: 6 }}>强制等级</span>
          <Chip active={filters.mandatory === undefined}
            onClick={() => setFilter({ mandatory: undefined })}>不限</Chip>
          {MAND_OPTS.map((m) => (
            <Chip key={m} active={filters.mandatory === m}
              onClick={() => setFilter({ mandatory: m })}>{MAND_LABEL[m]}</Chip>
          ))}
          {filtered && <button className="btn ghost small" onClick={clearFilters}>清除筛选</button>}
          <button className="btn ghost small" style={{ marginLeft: 8 }}
            onClick={() => setShowTypes((v) => !v)}>
            {showTypes ? '收起类型词表' : '管理类型词表'}
          </button>
        </div>
        <div className="btnrow" style={{ marginBottom: 8 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
            <input type="checkbox" checked={allChecked} onChange={toggleAll} /> 全选本页
          </label>
          <button className="btn ghost small red" disabled={!selected.size}
            onClick={doDeleteSelected}>全选删除（{selected.size}）</button>
        </div>
        <table className="tbl">
          <thead><tr><th></th><th>ID</th><th>doc</th><th>seq</th><th>类型</th><th>摘要</th><th>标签</th><th>原文</th><th>操作</th></tr></thead>
          <tbody>
            {chunks?.items?.map((c: any) => (
              <tr key={c.id} className="clickable">
                <td onClick={(e) => e.stopPropagation()}>
                  <input type="checkbox" checked={selected.has(c.id)} onChange={() => toggle(c.id)} />
                </td>
                <td onClick={() => getChunk(c.id).then(setDetail)}>{c.id}</td>
                <td onClick={() => getChunk(c.id).then(setDetail)}>{c.doc_id}</td>
                <td onClick={() => getChunk(c.id).then(setDetail)}>{c.seq}</td>
                <td onClick={() => getChunk(c.id).then(setDetail)}><TypeBadge chunk={c} types={types} /></td>
                <td style={{ maxWidth: 200 }} onClick={() => getChunk(c.id).then(setDetail)}>{c.summary}</td>
                <td onClick={() => getChunk(c.id).then(setDetail)}>{(c.tags || []).join(' / ')}</td>
                <td className="note" style={{ maxWidth: 200 }} onClick={() => getChunk(c.id).then(setDetail)}>{c.text.slice(0, 60)}…</td>
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

      {showTypes && <TypeManager types={types} toast={toast} reload={reload} />}

      {detail && (
        <div className="card">
          <h3>Chunk #{detail.id} · {detail.doc_name} · seq {detail.seq}</h3>
          <div className="note" style={{ marginBottom: 8 }}>
            摘要：{detail.summary} ｜ 标签：{(detail.tags || []).join(' / ')}
          </div>
          <div className="btnrow" style={{ marginBottom: 8, alignItems: 'center' }}>
            <span className="note">内容类型</span>
            <TypeBadge chunk={detail} types={types} />
            <span className="note">
              置信度 {CONF_LABEL[detail.type_confidence] || '—'} ·
              强制 {MAND_LABEL[detail.type_mandatory] ?? '—'} ·
              来源 {detail.type_source || '—'}
            </span>
            <span className="note">改为</span>
            <select value={detail.type || ''}
              onChange={(e) => doSetChunkType(detail.id, e.target.value)}>
              <option value="" disabled>选择类型…</option>
              {activeTypes.map((t) => (
                <option key={t.code} value={t.code}>
                  {t.label}（{CONF_LABEL[t.default_confidence]} / {MAND_LABEL[t.default_mandatory]}）
                </option>
              ))}
            </select>
            <span className="note">（改类型会按词表默认值重取两个维度）</span>
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
