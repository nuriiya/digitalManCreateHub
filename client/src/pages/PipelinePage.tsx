import { useEffect, useMemo, useRef, useState } from 'react'
import {
  getPipelines, createPipeline, getPipeline, deletePipeline,
  addPipelineNode, updateNode, removePipelineNode, addPipelineRelation, removePipelineRelation,
  validatePipeline, approvePipeline, getPipelineChanges,
  approvePipelineChange, rejectPipelineChange, chatPipeline, runPipeline,
  getIdentities,
  type Pipeline, type PipelineNode, type PipelineRelation, type PipelineChange,
  type Identity,
} from '../api'
import { useToast } from '../Toast'
import TrainerPanel from '../components/TrainerPanel'

const REL_TYPE_LABEL: Record<string, string> = {
  design: '设计', supply: '供给知识', review: '审核门', handoff: '交接', compose: '组装',
  ask: '询问',
}
const KIND_LABEL: Record<string, string> = {
  nominate: '提名节点', deterministic: '确定性节点',
}

const NODE_H = 100         // 节点矩形高（标题区 + 接口行 + 分隔线）
const IFACE_BAND = 42      // 底部接口条高度（收/出 两行 kind 标签）
const REL_KIND_NAME: Record<string, string> = {
  需求: '需求规格', 需求规格: '需求规格', 需求文档: '需求规格',
  技术方案: '技术方案', 设计: '技术方案', 设计文档: '技术方案',
  代码: '代码实现', 代码实现: '代码实现', 审查通过代码: '代码实现', 修改后代码: '代码实现',
  审查意见: '审查意见', 代码审查: '审查意见',
  失败测试报告: '失败测试报告', 测试报告: '失败测试报告', 测试输出: '失败测试报告',
}
const KIND_COLOR: Record<string, string> = {
  需求规格: '#5DCAA8', 技术方案: '#7FB4FF', 代码实现: '#FFC24D',
  审查意见: '#FF9F7B', 失败测试报告: '#FF6B8A',
}

// 归一化交接物类型（与 backend context_mgr.normalize_kind 保持一致的子集）
const normKind = (raw: string | null | undefined): string => {
  const r = (raw || '').trim()
  return REL_KIND_NAME[r] || r || '交接物'
}
// 从 relations 推导某节点的入站/出站 kind 列表
const nodeIfaceKinds = (cur: Pipeline, n: PipelineNode) => {
  const inKinds: string[] = []
  const outKinds: string[] = []
  cur.relations.forEach((r) => {
    if (r.relation_type === 'ask') return
    if (r.to_node_id === n.id) {
      const k = normKind(r.handoff_type)
      if (!inKinds.includes(k)) inKinds.push(k)
    }
    if (r.from_node_id === n.id) {
      const k = normKind(r.handoff_type)
      if (!outKinds.includes(k)) outKinds.push(k)
    }
  })
  return { inKinds, outKinds }
}

// 估算文本渲染宽度（中文全角按 1 个字号宽，英文半角按 0.6 个字号宽）
function textWidth(s: string, fontSize: number): number {
  let w = 0
  for (const ch of s) {
    w += ch.charCodeAt(0) > 0x2E80 ? fontSize : fontSize * 0.6
  }
  return w
}

// 计算节点框宽（根据 node_key / step_name / kind + 接口行文本自适应，最小 120）
function nodeBoxWidth(p: Pipeline, n: PipelineNode, personaName: (id: number | null) => string): number {
  const label = n.step_name || personaName(n.persona_id)
  const { inKinds, outKinds } = nodeIfaceKinds(p, n)
  const lines = [
    { text: n.node_key, fs: 12 },
    { text: label, fs: 11 },
    { text: KIND_LABEL[n.kind] || n.kind, fs: 10 },
    { text: `入站 ${inKinds.join('·')}`, fs: 10 },
    { text: `产出 ${outKinds.join('·')}`, fs: 10 },
  ]
  const maxText = Math.max(...lines.map((l) => textWidth(l.text, l.fs)))
  return Math.max(150, Math.min(300, maxText + 32))
}

// 简单拓扑分层布局（DAG），节点宽度自适应、层内垂直排布
function layoutPipeline(
  p: Pipeline | null,
  personaName: (id: number | null) => string,
): Record<number, { x: number; y: number; w: number }> {
  const pos: Record<number, { x: number; y: number; w: number }> = {}
  if (!p) return pos
  const adj: Record<number, number[]> = {}
  const indeg: Record<number, number> = {}
  p.nodes.forEach((n) => { adj[n.id] = []; indeg[n.id] = 0 })
  // 拓扑分层只走「正向流转」关系（supply/handoff/review/design/compose）；
  // ask（询问）是下游问上游的反向边，不决定流程顺序，否则会形成双向环导致
  // 分层失效（图非线性：询问边只绘制、不参与拓扑）。
  p.relations.forEach((r) => {
    if (r.relation_type === 'ask') return
    if (adj[r.from_node_id] && adj[r.to_node_id] !== undefined) {
      adj[r.from_node_id].push(r.to_node_id)
      indeg[r.to_node_id]++
    }
  })
  const layers: number[][] = []
  const queue = p.nodes.filter((n) => indeg[n.id] === 0).map((n) => n.id)
  const visited = new Set<number>()
  while (queue.length) {
    const layer: number[] = []
    const next: number[] = []
    queue.forEach((id) => {
      if (visited.has(id)) return
      visited.add(id)
      layer.push(id)
      adj[id].forEach((v) => { indeg[v]--; if (indeg[v] === 0) next.push(v) })
    })
    if (layer.length) layers.push(layer)
    queue.length = 0
    queue.push(...next)
  }
  p.nodes.forEach((n) => { if (!visited.has(n.id)) layers.push([n.id]) })

  const nodeById = new Map(p.nodes.map((n) => [n.id, n]))
  const widths = layers.map((layer) =>
    Math.max(...layer.map((id) => nodeBoxWidth(p, nodeById.get(id)!, personaName))))
  const H_GAP = 52   // 节点垂直间距（含接口条）
  const V_GAP = 64   // 层水平间距
  let x = 40
  layers.forEach((layer, li) => {
    const w = widths[li]
    let y = 40
    layer.forEach((id) => {
      pos[id] = { x, y, w }
      y += NODE_H + H_GAP
    })
    x += w + V_GAP
  })
  return pos
}

export default function PipelinePage({ refreshKey }: { refreshKey: number }) {
  const [pipelines, setPipelines] = useState<Pipeline[]>([])
  const [sel, setSel] = useState<number | null>(null)
  const [cur, setCur] = useState<Pipeline | null>(null)
  const [idents, setIdents] = useState<Identity[]>([])
  const [view, setView] = useState<'graph' | 'edit' | 'chat' | 'train'>('graph')
  const [changes, setChanges] = useState<PipelineChange[]>([])
  const [valErrors, setValErrors] = useState<string[]>([])
  const [creating, setCreating] = useState(false)
  const [createName, setCreateName] = useState('')
  const [createTags, setCreateTags] = useState('')
  const [createDesc, setCreateDesc] = useState('')
  const [createBusy, setCreateBusy] = useState(false)
  const [showArchived, setShowArchived] = useState(false)
  const [nodeDraft, setNodeDraft] = useState({ node_key: '', persona_id: '', kind: 'nominate', step_name: '' })
  const [relDraft, setRelDraft] = useState({ from_node_id: '', to_node_id: '', relation_type: 'handoff', handoff_type: '', handoff_schema: '' })
  const [chatMsg, setChatMsg] = useState('')
  const [chatBusy, setChatBusy] = useState(false)
  const { toast } = useToast()

  const personaName = (id: number | null) => {
    if (!id) return '（未绑定数字人）'
    return idents.find((i) => i.id === id)?.name || `#${id}`
  }

  const reload = () => { getPipelines().then((r) => setPipelines(r.pipelines ?? [])).catch(() => { }) }
  const reloadOne = (id: number) => {
    getPipeline(id).then((r) => { setCur(r.pipeline); reload() }).catch(() => { })
    getPipelineChanges(id).then((r) => setChanges(r.changes ?? [])).catch(() => { })
    validatePipeline(id).then((r) => setValErrors(r.errors ?? [])).catch(() => { })
  }
  useEffect(() => { reload(); getIdentities().then((r) => setIdents(r.identities ?? [])).catch(() => { }) }, [refreshKey])
  useEffect(() => { if (sel) reloadOne(sel) }, [sel])

  const doCreate = async () => {
    const name = createName.trim()
    if (!name) { toast('请填写名称', 'err'); return }
    setCreateBusy(true)
    try {
      const desc = createDesc.trim()
      const r = await createPipeline(name, desc,
        createTags.split(/[,，\s]+/).filter(Boolean))
      if (r.note) { toast(r.note, 'err'); setCreateBusy(false); return }
      toast(desc
        ? `pipeline「${name}」已创建，Pipeline 创建工程师已生成节点/关系`
        : `pipeline「${name}」已创建`, 'ok')
      setCreateName(''); setCreateTags(''); setCreateDesc('')
      setCreating(false)
      reload(); setSel(r.id)
    } catch (e: any) { toast(e.message, 'err') }
    setCreateBusy(false)
  }

  const doDelete = async (id: number) => {
    if (!confirm('确认删除此 pipeline？其节点与关系一并删除。')) return
    try { await deletePipeline(id); toast('已删除', 'ok'); setSel(null); reload() } catch (e: any) { toast(e.message, 'err') }
  }

  const doAddNode = async () => {
    if (!sel || !nodeDraft.node_key.trim()) { toast('请填写节点 key（如 n1）', 'err'); return }
    try {
      await addPipelineNode(sel, {
        node_key: nodeDraft.node_key.trim(),
        persona_id: nodeDraft.persona_id ? Number(nodeDraft.persona_id) : null,
        kind: nodeDraft.kind, step_name: nodeDraft.step_name,
      })
      setNodeDraft({ node_key: '', persona_id: '', kind: 'nominate', step_name: '' })
      reloadOne(sel)
    } catch (e: any) { toast(e.message, 'err') }
  }

  const doAddRel = async () => {
    if (!sel || !relDraft.from_node_id || !relDraft.to_node_id) { toast('请选择上下游节点', 'err'); return }
    try {
      await addPipelineRelation(sel, {
        from_node_id: Number(relDraft.from_node_id), to_node_id: Number(relDraft.to_node_id),
        relation_type: relDraft.relation_type, handoff_type: relDraft.handoff_type, handoff_schema: relDraft.handoff_schema,
      })
      setRelDraft({ from_node_id: '', to_node_id: '', relation_type: 'handoff', handoff_type: '', handoff_schema: '' })
      reloadOne(sel)
    } catch (e: any) { toast(e.message, 'err') }
  }

  const doApprove = async () => {
    if (!sel) return
    try { await approvePipeline(sel); toast('已批准，标签已入本体库', 'ok'); reloadOne(sel) } catch (e: any) { toast(e.message, 'err') }
  }

  const doValidate = async () => { if (sel) { const r = await validatePipeline(sel); setValErrors(r.errors ?? []); toast(r.ok ? '校验通过' : `校验失败：${r.errors.length} 处`, r.ok ? 'ok' : 'err') } }

  const doRun = async () => {
    if (!sel) return
    try {
      const r = await runPipeline(sel)
      toast(`pipeline 运行任务 #${r.job_id} 已启动`, 'ok')
    } catch (e: any) { toast(e.message, 'err') }
  }

  const doChat = async () => {
    if (!sel || !chatMsg.trim()) return
    setChatBusy(true)
    try {
      const r = await chatPipeline(sel, chatMsg.trim())
      setChatMsg('')
      if (r.changes && r.changes.length) { toast(`设计师提名 ${r.changes.length} 项修改，待审批`, 'ok'); reloadOne(sel) }
      else { toast(r.note || '未能解析出修改提名', 'err') }
    } catch (e: any) { toast(e.message, 'err') }
    setChatBusy(false)
  }

  const doChange = async (changeId: number, approve: boolean) => {
    if (!sel) return
    try {
      if (approve) await approvePipelineChange(sel, changeId); else await rejectPipelineChange(sel, changeId)
      reloadOne(sel)
    } catch (e: any) { toast(e.message, 'err') }
  }

  // —— 拖拽搭建（目标 B）：从组件气泡拖数字人到画布 → 创建节点 ——
  const autoNodeKey = () => {
    let k = 1
    while ((cur?.nodes || []).some((n) => n.node_key === `n${k}`)) k++
    return `n${k}`
  }
  const handleDropPersona = async (e: React.DragEvent) => {
    e.preventDefault()
    if (!sel) return
    const pid = e.dataTransfer.getData('application/persona')
    if (!pid) return
    const p = idents.find((i) => i.id === Number(pid))
    try {
      await addPipelineNode(sel, {
        node_key: autoNodeKey(),
        persona_id: Number(pid),
        kind: 'nominate',
        step_name: p?.name || '',
      })
      toast(`已添加节点「${p?.name || pid}」`, 'ok')
      reloadOne(sel)
    } catch (e: any) { toast(e.message, 'err') }
  }

  const layout = useMemo(() => layoutPipeline(cur, personaName), [cur, idents])

  return (
    <div className="grid cols2" style={{ gridTemplateColumns: '280px 1fr' }}>
      {/* 左：pipeline 列表 */}
      <div className="card">
        <h3>pipeline 编排</h3>
        <div className="desc">数字人编排 = 一等公民实体，有本体库（数字人节点 + 数字人关系 + 标签）。</div>
        <div className="btnrow">
          <button className="btn green small" onClick={() => setCreating(true)}>+ 创建 pipeline</button>
          <button className="btn ghost small" onClick={reload}>刷新</button>
          <label style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 4 }}>
            <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
            显示归档版本（family 历史）
          </label>
        </div>
        {creating && (
          <div className="dlg-overlay" onClick={() => setCreating(false)}>
            <div className="dlg" onClick={(e) => e.stopPropagation()}>
              <div className="dlg-head">创建 pipeline</div>
              <label className="dlg-field"><span>名称</span>
                <input autoFocus placeholder="如：调研X领域并写demo" value={createName} onChange={(e) => setCreateName(e.target.value)} /></label>
              <label className="dlg-field"><span>标签（逗号分隔，批准后入本体库）</span>
                <input placeholder="调研, demo" value={createTags} onChange={(e) => setCreateTags(e.target.value)} /></label>
              <label className="dlg-field"><span>
                需求描述（选填 · 填写后由 <b>Pipeline 创建工程师</b> 生成节点与关系）
              </span>
                <textarea rows={4} placeholder="如：先调研 FMEA 方法论文献 → 建一个 FMEA 工程师 → 复核生成的报告"
                  value={createDesc} onChange={(e) => setCreateDesc(e.target.value)} /></label>
              <div className="dlg-actions">
                <button className="btn ghost small" onClick={() => setCreating(false)}>取消</button>
                <button className="btn green small" disabled={createBusy} onClick={doCreate}>
                  {createBusy ? '生成中…' : '创建'}
                </button>
              </div>
            </div>
          </div>
        )}
        {pipelines.filter((p) => showArchived ? true : !p.is_archived).length === 0 && <div className="note" style={{ padding: '8px 0' }}>{showArchived ? '暂无 pipeline' : '暂无未归档的 pipeline（勾选右上"显示归档版本"可看历史）'}</div>}
        {pipelines.filter((p) => showArchived ? true : !p.is_archived).map((p) => (
          <div key={p.id} className={`id-card st-${p.status}`} style={{ cursor: 'pointer', border: sel === p.id ? '1px solid var(--accent)' : undefined, opacity: p.is_archived ? 0.62 : 1 }} onClick={() => setSel(p.id)}>
            <div className="id-head">
              <b>{p.name}</b>
              <span className={`status-pill ${p.status}`}>{p.status}</span>
              {p.is_archived && <span className="kw-chip" style={{ fontSize: 10 }}>归档 v{p.family_id === p.id ? '独立' : `#${p.family_id}`}</span>}
              <span className="ops" style={{ marginLeft: 'auto' }}>
                <button className="btn red small" onClick={(e) => { e.stopPropagation(); doDelete(p.id) }}>删</button>
              </span>
            </div>
            {p.tags.length > 0 && <div className="id-kws">{p.tags.map((t) => <span key={t} className="kw-chip">{t}</span>)}</div>}
            <div className="note">{p.nodes.length} 节点 · {p.relations.length} 关系{p.is_archived ? ` · family=#${p.family_id ?? '-'}` : ''}</div>
          </div>
        ))}
      </div>

      {/* 右：详情 */}
      <div>
        {!cur && <div className="card"><div className="note">选择左侧 pipeline 查看流程图 / 编辑 / 对话。</div></div>}
        {cur && (
          <>
            <div className="card">
              <div className="btnrow">
                <button className={`btn ${view === 'graph' ? '' : 'ghost'} small`} onClick={() => setView('graph')}>流程图</button>
                <button className={`btn ${view === 'edit' ? '' : 'ghost'} small`} onClick={() => setView('edit')}>编辑</button>
                <button className={`btn ${view === 'chat' ? '' : 'ghost'} small`} onClick={() => setView('chat')}>对话</button>
                <button className={`btn ${view === 'train' ? '' : 'ghost'} small`} onClick={() => setView('train')}>训练</button>
                <span style={{ marginLeft: 'auto' }} className="note">
                  v{cur.version} · {cur.status} · {cur.tags.join(' / ')}
                </span>
              </div>
              {valErrors.length > 0 && <div className="warn" style={{ marginTop: 6 }}>校验问题：{valErrors.join('；')}</div>}
            </div>

            {view === 'graph' && (
              <div className="card">
                <h3>图片化结构流程图</h3>
                <div style={{ display: 'flex', gap: 12, alignItems: 'stretch' }}>
                  {/* 组件气泡面板：拖到画布即可添加节点（目标 B） */}
                  <div className="palette" style={{ width: 176, flexShrink: 0, borderRight: '1px solid var(--border)', paddingRight: 10 }}>
                    <b className="note">组件气泡</b>
                    <div className="desc" style={{ fontSize: 11, marginBottom: 8 }}>拖到右侧画布，松手即添加节点</div>
                    {idents.filter((i) => i.status === 'approved').map((i) => (
                      <div key={i.id} draggable
                        onDragStart={(e) => { e.dataTransfer.setData('application/persona', String(i.id)); e.dataTransfer.effectAllowed = 'copy' }}
                        className="palette-item" title={`拖到画布添加「${i.name}」节点`}>
                        <span className="palette-ico">🧩</span>{i.name}
                      </div>
                    ))}
                  </div>
                  {/* 画布：接收拖入 */}
                  <div style={{ flex: 1, minWidth: 0 }}
                    onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy' }}
                    onDrop={handleDropPersona}>
                    {cur.nodes.length === 0
                      ? <div className="note" style={{ padding: 48, textAlign: 'center', border: '1.5px dashed var(--border)', borderRadius: 10 }}>
                        暂无节点 —— 把左侧「组件气泡」拖进来，或去「编辑」页添加。
                      </div>
                      : <PipelineGraph cur={cur} baseLayout={layout} personaName={personaName} />}
                  </div>
                </div>
                <div className="btnrow" style={{ marginTop: 8 }}>
                  <button className="btn ghost small" onClick={doValidate}>校验</button>
                  {cur.status !== 'approved' && <button className="btn green small" onClick={doApprove}>批准（打标签入本体库）</button>}
                  {cur.status === 'approved' && <button className="btn green small" onClick={doRun}>运行 pipeline</button>}
                </div>
              </div>
            )}

            {view === 'edit' && (
              <div className="card">
                <h3>编辑 · 增删节点 / 关系（变更即时落库，校验器把关）</h3>
                <div className="desc">关系类型闭集：{Object.entries(REL_TYPE_LABEL).map(([k, v]) => `${v}(${k})`).join(' · ')}</div>
                <div style={{ borderTop: '1px solid var(--border)', paddingTop: 8, marginTop: 8 }}>
                  <b className="note">添加数字人节点</b>
                  <div className="grid cols2">
                    <label className="field"><span>节点 key</span><input placeholder="n3" value={nodeDraft.node_key} onChange={(e) => setNodeDraft({ ...nodeDraft, node_key: e.target.value })} /></label>
                    <label className="field"><span>绑定数字人</span>
                      <select value={nodeDraft.persona_id} onChange={(e) => setNodeDraft({ ...nodeDraft, persona_id: e.target.value })}>
                        <option value="">（Function 节点，不绑定）</option>
                        {idents.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
                      </select></label>
                  </div>
                  <div className="grid cols2">
                    <label className="field"><span>kind</span>
                      <select value={nodeDraft.kind} onChange={(e) => setNodeDraft({ ...nodeDraft, kind: e.target.value })}>
                        {Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                      </select></label>
                    <label className="field"><span>步骤名</span><input placeholder="代码复核" value={nodeDraft.step_name} onChange={(e) => setNodeDraft({ ...nodeDraft, step_name: e.target.value })} /></label>
                  </div>
                  <button className="btn green small" onClick={doAddNode}>添加节点</button>
                </div>
                <div style={{ borderTop: '1px solid var(--border)', paddingTop: 8, marginTop: 8 }}>
                  <b className="note">添加数字人关系</b>
                  <div className="grid cols2">
                    <label className="field"><span>上游节点</span>
                      <select value={relDraft.from_node_id} onChange={(e) => setRelDraft({ ...relDraft, from_node_id: e.target.value })}>
                        <option value="">选择…</option>
                        {cur.nodes.map((n) => <option key={n.id} value={n.id}>{n.node_key} {n.step_name}</option>)}
                      </select></label>
                    <label className="field"><span>下游节点</span>
                      <select value={relDraft.to_node_id} onChange={(e) => setRelDraft({ ...relDraft, to_node_id: e.target.value })}>
                        <option value="">选择…</option>
                        {cur.nodes.map((n) => <option key={n.id} value={n.id}>{n.node_key} {n.step_name}</option>)}
                      </select></label>
                  </div>
                  <div className="grid cols2">
                    <label className="field"><span>关系类型</span>
                      <select value={relDraft.relation_type} onChange={(e) => setRelDraft({ ...relDraft, relation_type: e.target.value })}>
                        {Object.entries(REL_TYPE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}({k})</option>)}
                      </select></label>
                    <label className="field"><span>交接物类型</span><input placeholder="领域文献" value={relDraft.handoff_type} onChange={(e) => setRelDraft({ ...relDraft, handoff_type: e.target.value })} /></label>
                  </div>
                  <label className="field"><span>交接物 schema 标记</span><input placeholder="doc_refs[]" value={relDraft.handoff_schema} onChange={(e) => setRelDraft({ ...relDraft, handoff_schema: e.target.value })} /></label>
                  <button className="btn green small" onClick={doAddRel}>添加关系</button>
                </div>
                <div style={{ borderTop: '1px solid var(--border)', paddingTop: 8, marginTop: 8 }}>
                  <b className="note">现有节点 / 关系（点删移除）</b>
                  {cur.nodes.map((n) => (
                    <div key={n.id} className="note" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      [{n.node_key}] {n.step_name} · {KIND_LABEL[n.kind]} · {personaName(n.persona_id)}
                      <button className="btn red small" style={{ marginLeft: 'auto' }} onClick={() => removePipelineNode(cur.id, n.id).then(() => reloadOne(cur.id))}>删</button>
                    </div>
                  ))}
                  {cur.relations.map((r) => (
                    <div key={r.id} className="note" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      关系：{cur.nodes.find((n) => n.id === r.from_node_id)?.node_key} --{REL_TYPE_LABEL[r.relation_type] || r.relation_type}--&gt; {cur.nodes.find((n) => n.id === r.to_node_id)?.node_key}
                      {r.handoff_type && `（交接：${r.handoff_type}）`}
                      <button className="btn red small" style={{ marginLeft: 'auto' }} onClick={() => removePipelineRelation(cur.id, r.id).then(() => reloadOne(cur.id))}>删</button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {view === 'chat' && (
              <div className="card">
                <h3>对话 · 和流程设计师说，设计师改 pipeline</h3>
                <div className="desc">设计师只提名修改，不裁决。提名进入下方「待审批」，你批准后才应用到 pipeline。</div>
                <div className="btnrow">
                  <input style={{ flex: 1 }} placeholder="如：加一个代码复核节点；或把 n2 改成并发" value={chatMsg} onChange={(e) => setChatMsg(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') doChat() }} />
                  <button className="btn green" onClick={doChat} disabled={chatBusy}>{chatBusy ? '设计中…' : '发送'}</button>
                </div>
                <div style={{ marginTop: 10 }}>
                  <b className="note">修改提名（{changes.filter((c) => c.status === 'pending').length} 待审批）</b>
                  {changes.length === 0 && <div className="note">暂无修改提名。</div>}
                  {changes.map((c) => (
                    <div key={c.id} className="note" style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0' }}>
                      <span className={`status-pill ${c.status}`}>{c.status}</span>
                      <span>[{c.action}]</span>
                      <span style={{ flex: 1 }}>{JSON.stringify(c.payload)}</span>
                      {c.status === 'pending' && (
                        <>
                          <button className="btn green small" onClick={() => doChange(c.id, true)}>批准</button>
                          <button className="btn ghost small" onClick={() => doChange(c.id, false)}>拒绝</button>
                        </>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {view === 'train' && <TrainerPanel personas={idents} />}
          </>
        )}
      </div>
    </div>
  )
}

// 自绘 SVG 流程图（DAG）— 可拖动 / 缩放 / 撤销重做 / 位置持久化
function PipelineGraph({ cur, baseLayout, personaName }: {
  cur: Pipeline
  baseLayout: Record<number, { x: number; y: number; w: number }>
  personaName: (id: number | null) => string
}) {
  // 位置覆盖：用户拖动后的位置。key=node_id, value={x,y}
  const [positions, setPositions] = useState<Record<number, { x: number; y: number }>>(() => {
    const init: Record<number, { x: number; y: number }> = {}
    cur.nodes.forEach((n) => {
      // 优先用 DB 持久化的位置，否则用 baseLayout 的初始布局
      if (n.position_x != null && n.position_y != null) {
        init[n.id] = { x: n.position_x, y: n.position_y }
      } else {
        const b = baseLayout[n.id]
        if (b) init[n.id] = { x: b.x, y: b.y }
      }
    })
    return init
  })
  // 撤销/重做栈：每次拖动 commit 前压栈
  const [undoStack, setUndoStack] = useState<Record<number, { x: number; y: number }>[]>([])
  const [redoStack, setRedoStack] = useState<Record<number, { x: number; y: number }>[]>([])
  // 画布视口：平移 + 缩放
  const [view, setView] = useState({ x: 0, y: 0, w: 1, zoom: 1 })
  const dragRef = useRef<{ kind: 'node' | 'pan'; id?: number; startX: number; startY: number; orig?: { x: number; y: number }; origView?: { x: number; y: number } } | null>(null)
  const svgRef = useRef<SVGSVGElement | null>(null)

  // 计算绝对布局：baseLayout 覆盖默认宽高，positions 覆盖 x/y
  const layout: Record<number, { x: number; y: number; w: number }> = {}
  cur.nodes.forEach((n) => {
    const b = baseLayout[n.id]
    if (!b) return
    const p = positions[n.id] || { x: b.x, y: b.y }
    layout[n.id] = { x: p.x, y: p.y, w: b.w }
  })
  const W = Math.max(700, ...cur.nodes.map((n) => (layout[n.id]?.x ?? 0) + (layout[n.id]?.w ?? 100)).concat([0])) + 60
  const H = Math.max(320, ...cur.nodes.map((n) => (layout[n.id]?.y ?? 0) + NODE_H + 90).concat([0])) + 40
  // viewBox 是从原点 (0,0) 起的窗口；用户拖动平移通过 viewBox 的 x/y 调整；缩放通过 w/h 调整。
  const vbX = -view.x
  const vbY = -view.y
  const vbW = W / view.zoom
  const vbH = H / view.zoom

  // 文字超宽时截断（追加 …）
  const clip = (s: string, fs: number, maxW: number) => {
    if (textWidth(s, fs) <= maxW) return s
    let out = ''
    for (const ch of s) {
      if (textWidth(out + ch + '…', fs) > maxW) break
      out += ch
    }
    return out + '…'
  }

  const undo = () => {
    if (!undoStack.length) return
    const last = undoStack[undoStack.length - 1]
    setUndoStack(undoStack.slice(0, -1))
    setRedoStack([...redoStack, positions])
    setPositions(last)
  }
  const redo = () => {
    if (!redoStack.length) return
    const next = redoStack[redoStack.length - 1]
    setRedoStack(redoStack.slice(0, -1))
    setUndoStack([...undoStack, positions])
    setPositions(next)
  }

  const onMouseDownNode = (e: React.MouseEvent, nid: number) => {
    e.stopPropagation()
    e.preventDefault()
    dragRef.current = {
      kind: 'node', id: nid,
      startX: e.clientX, startY: e.clientY,
      orig: { x: positions[nid]?.x ?? baseLayout[nid]?.x ?? 0,
             y: positions[nid]?.y ?? baseLayout[nid]?.y ?? 0 },
    }
  }
  const onMouseDownBg = (e: React.MouseEvent) => {
    e.preventDefault()
    dragRef.current = { kind: 'pan', startX: e.clientX, startY: e.clientY, origView: { x: view.x, y: view.y } }
  }
  const onMouseMove = (e: React.MouseEvent) => {
    const d = dragRef.current
    if (!d) return
    const dxScreen = e.clientX - d.startX
    const dyScreen = e.clientY - d.startY
    if (d.kind === 'node' && d.id != null && d.orig) {
      // 把屏幕位移换算成画布坐标
      const k = 1 / view.zoom
      const nx = Math.max(0, d.orig.x + dxScreen * k)
      const ny = Math.max(0, d.orig.y + dyScreen * k)
      setPositions({ ...positions, [d.id]: { x: nx, y: ny } })
    } else if (d.kind === 'pan' && d.origView) {
      const k = 1 / view.zoom
      setView({ ...view, x: d.origView.x - dxScreen * k, y: d.origView.y - dyScreen * k })
    }
  }
  const onMouseUp = () => {
    const d = dragRef.current
    if (d?.kind === 'node' && d.id != null) {
      // commit：压入 undoStack、清 redoStack、持久化到后端
      setUndoStack([...undoStack, positions])
      setRedoStack([])
      const n = cur.nodes.find((nn) => nn.id === d.id)
      if (n) {
        const p = positions[d.id]
        if (p) updateNode(n.pipeline_id, d.id, { position: { x: Math.round(p.x), y: Math.round(p.y) } }).catch(() => { })
      }
    }
    dragRef.current = null
  }
  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault()
    const z = Math.max(0.4, Math.min(2.5, view.zoom * (e.deltaY < 0 ? 1.1 : 1 / 1.1)))
    setView({ ...view, zoom: z })
  }

  // 键盘快捷键：Ctrl+Z / Ctrl+Shift+Z（在 SVG 内捕获；外层应不抢焦点）
  const onKeyDown = (e: React.KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === 'z') { e.preventDefault(); undo() }
    else if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'z') { e.preventDefault(); redo() }
  }

  return (
    <div tabIndex={0} onKeyDown={onKeyDown} style={{ outline: 'none' }}>
      {/* 工具条：撤销 / 重做 / 重置 / 缩放 */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8, fontSize: 12 }}>
        <button className="btn ghost small" disabled={undoStack.length === 0} onClick={undo}>↶ 撤销 ({undoStack.length})</button>
        <button className="btn ghost small" disabled={redoStack.length === 0} onClick={redo}>↷ 重做 ({redoStack.length})</button>
        <button className="btn ghost small" onClick={() => setView({ x: 0, y: 0, w: 1, zoom: 1 })}>居中</button>
        <span style={{ color: 'var(--muted)' }}>滚轮缩放 · 拖空白处平移 · 拖节点移动 · Ctrl+Z 撤销</span>
        <span style={{ marginLeft: 'auto' }}>缩放 {(view.zoom * 100).toFixed(0)}%</span>
      </div>
      <div style={{ overflow: 'hidden', border: '0.5px solid var(--border)', borderRadius: 'var(--radius-md)', background: 'var(--bg-card)' }}>
        <svg ref={svgRef} viewBox={`${vbX} ${vbY} ${vbW} ${vbH}`} width="100%" style={{ cursor: dragRef.current?.kind === 'pan' ? 'grabbing' : 'grab' }}
             role="img" onMouseMove={onMouseMove} onMouseUp={onMouseUp} onMouseLeave={onMouseUp} onWheel={onWheel} onMouseDown={onMouseDownBg}>
          <defs>
            <marker id="parrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </marker>
          </defs>
          {/* 背景网格：帮助看出缩放与对齐 */}
          <defs>
            <pattern id="pgrid" width="40" height="40" patternUnits="userSpaceOnUse">
              <path d="M 40 0 L 0 0 0 40" fill="none" stroke="rgba(255,255,255,0.04)" strokeWidth="1" />
            </pattern>
          </defs>
          <rect x={vbX} y={vbY} width={vbW} height={vbH} fill="url(#pgrid)" pointerEvents="none" />
          {cur.relations.map((r) => {
            const a = layout[r.from_node_id]; const b = layout[r.to_node_id]
            if (!a || !b) return null
            const ax = a.x + a.w; const bx = b.x
            const mx = (ax + bx) / 2
            const isGate = r.relation_type === 'review'
            const isAsk = r.relation_type === 'ask'
            const stroke = isGate ? '#FF6B6B' : isAsk ? '#7BAFFF' : '#B0B0BA'
            const sw = isGate ? 2.6 : 1.8
            const dash = isAsk ? '5,5' : undefined
            const askOpacity = isAsk ? 0.78 : 1
            const sameLine = Math.abs(a.y - b.y) < 4
            const yA = isAsk ? a.y + NODE_H - 8 : a.y + NODE_H / 2 - 4
            const yB = isAsk ? b.y + NODE_H - 8 : b.y + NODE_H / 2 - 4
            const viaY = isAsk ? Math.max(a.y, b.y) + NODE_H + 14 : (a.y + b.y) / 2
            const dPath = isAsk
              ? `M ${ax} ${yA} C ${mx} ${viaY}, ${mx} ${viaY}, ${bx} ${yB}`
              : (sameLine
                ? `M ${ax} ${yA} L ${bx - 6} ${yB}`
                : `M ${ax} ${yA} C ${mx} ${yA}, ${mx} ${yB}, ${bx} ${yB}`)
            const label = r.handoff_type ? normKind(r.handoff_type) : (REL_TYPE_LABEL[r.relation_type] || r.relation_type)
            const labelY = isGate ? viaY - 36 : (isAsk ? viaY - 8 : viaY - 12)
            const lw = textWidth(label, 11.5)
            return (
              <g key={r.id} opacity={askOpacity}>
                <path d={dPath} fill="none" stroke={stroke} strokeWidth={sw} strokeDasharray={dash} markerEnd="url(#parrow)" />
                {isGate && (
                  <rect x={mx - 9} y={viaY - 9} width="18" height="18" transform={`rotate(45 ${mx} ${viaY})`} fill="#FF6B6B" stroke="#FFF" strokeWidth="0.6" opacity="0.98" />
                )}
                <rect x={mx - lw / 2 - 7} y={labelY - 11} width={lw + 14} height="22" rx="4" fill="rgba(8,10,16,0.96)" stroke={stroke} strokeWidth="0.8" />
                <text x={mx} y={labelY + 4} textAnchor="middle" fontSize="11.5" fontWeight="500" fill={stroke}>{label}</text>
              </g>
            )
          })}
          {cur.nodes.map((n) => {
            const p = layout[n.id]; if (!p) return null
            const fill = n.kind === 'deterministic' ? '#1e3a4a' : '#2a2540'
            const stroke = n.kind === 'deterministic' ? '#5DCAA8' : '#9C7CFF'
            const title = '#F0F0F5'
            const label = n.step_name || personaName(n.persona_id)
            const cx = p.x + p.w / 2
            const { inKinds, outKinds } = nodeIfaceKinds(cur, n)
            const iface = (s: string, maxW: number) => clip(s, 10.5, maxW)
            return (
              <g key={n.id} style={{ cursor: 'move' }} onMouseDown={(e) => onMouseDownNode(e, n.id)}>
                <rect x={p.x} y={p.y} width={p.w} height={NODE_H} rx="12" fill={fill} stroke={stroke} strokeWidth="1.6" />
                <text x={cx} y={p.y + 24} textAnchor="middle" fontSize="13" fontWeight="700" fill={title}>{clip(n.node_key, 12, p.w - 18)}</text>
                <text x={cx} y={p.y + 42} textAnchor="middle" fontSize="11.5" fill="#D8D8E4">{clip(label, 11, p.w - 18)}</text>
                <text x={cx} y={p.y + 56} textAnchor="middle" fontSize="10.5" fontWeight="500" fill={stroke}>{KIND_LABEL[n.kind]}</text>
                <line x1={p.x + 8} y1={p.y + 64} x2={p.x + p.w - 8} y2={p.y + 64} stroke="rgba(255,255,255,0.22)" strokeWidth="1" />
                <text x={p.x + 10} y={p.y + 79} fontSize="10.5" fontWeight="700" fill="#8FB3D6">收</text>
                <text x={p.x + 24} y={p.y + 79} fontSize="11" fill="#E5ECF2">{iface(inKinds.length ? inKinds.join(' / ') : '（无）', p.w - 36)}</text>
                <text x={p.x + 10} y={p.y + 95} fontSize="10.5" fontWeight="700" fill="#D6B988">出</text>
                <text x={p.x + 24} y={p.y + 95} fontSize="11" fill="#E5ECF2">{iface(outKinds.length ? outKinds.join(' / ') : '（无）', p.w - 36)}</text>
              </g>
            )
          })}
        </svg>
      </div>
    </div>
  )
}
