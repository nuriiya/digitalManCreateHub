import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ReactFlow, ReactFlowProvider, Background, BackgroundVariant, Controls, MiniMap,
  applyNodeChanges, applyEdgeChanges, BaseEdge, EdgeLabelRenderer, Handle, Position,
  getSmoothStepPath, useReactFlow,
  type Edge, type EdgeProps, type Node, type NodeChange, type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import dagre from '@dagrejs/dagre'
import {
  getGraph, getCandidate, setStatus, mergeCandidate, deleteCandidates, getChunk, runExam,
  runOrchestrate, getOrchestration, confirmOrchestration, discardOrchestration,
  dismissOrchItem, createIdentity, type EventItem, type OrchSummary, type OrchItem,
} from '../api'
import IdentityPanel from './IdentityPanel'
import { useToast } from '../Toast'

interface Props {
  refreshKey: number
  events: EventItem[]
  focusChunkId?: number | null
  chunks?: number
}

interface Cand {
  id: number; kind: string; name: string; definition: string
  status: string; merged_into: number | null; mentions: number
  exam?: { pass: number; fail: number; missing: number } | null
}
interface Rel {
  id: number; source: number; target: number | null
  target_name: string; relation_type: string; dangling: boolean
}

// ---------------- dagre auto-layout ----------------

function dagreLayout(nodes: Node[], edges: Edge[]): Node[] {
  if (nodes.length === 0) return nodes
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: 'LR', nodesep: 50, ranksep: 90, marginx: 40, marginy: 40 })
  nodes.forEach((n) => g.setNode(n.id, { width: n.width ?? 170, height: n.height ?? 44 }))
  edges.forEach((e) => g.setEdge(e.source, e.target))
  dagre.layout(g)
  return nodes.map((n) => {
    const pos = g.node(n.id)
    return { ...n, position: { x: pos.x - (n.width ?? 170) / 2, y: pos.y - (n.height ?? 44) / 2 } }
  })
}

// ---------------- custom node / edge ----------------

type CandNodeData = { cand: Cand; selected: boolean; searchDim?: boolean; seed?: boolean } & Record<string, unknown>
type GhostNodeData = { label: string } & Record<string, unknown>

const KIND_COLOR: Record<string, string> = {
  entity: 'var(--accent)',
  Object: 'var(--accent)',
  Action: 'var(--purple)',
  Function: 'var(--green)',
}

function CandidateNodeInner({ data }: NodeProps<Node<CandNodeData>>) {
  const { cand, selected, searchDim, seed } = data
  const st = cand.status
  // visual language from the old SVG: approved = solid blue,
  // pending = dashed orange
  const border = st === 'approved' ? 'var(--accent)' : 'var(--orange)'
  return (
    <div
      className={`og-cnode st-${st}`}
      style={{
        border: `1.4px ${st === 'pending' ? 'dashed' : 'solid'} ${border}`,
        background: st === 'approved' ? '#1f3a5f' : '#3a2c17',
        boxShadow: seed ? '0 0 0 2.5px var(--green)' : selected ? `0 0 0 2px ${border}` : 'none',
        opacity: searchDim ? 0.12 : 1,
        transition: 'opacity .2s ease',
      }}
    >
      <span className="kind" style={{ background: KIND_COLOR[cand.kind] ?? 'var(--muted)' }}>
        {cand.kind}
      </span>
      <span className="name">{cand.name}</span>
      {cand.mentions > 0 && <span className="badge">{cand.mentions}</span>}
      {seed && <span className="seed-badge">种子</span>}
      <Handle type="target" position={Position.Left} className="og-handle" />
      <Handle type="source" position={Position.Right} className="og-handle" />
    </div>
  )
}
const CandidateNode = memo(CandidateNodeInner)

function GhostNodeInner({ data }: NodeProps<Node<GhostNodeData>>) {
  return (
    <div className="og-ghost">
      <span>{data.label}</span>
      <Handle type="target" position={Position.Left} className="og-handle" />
    </div>
  )
}
const GhostNode = memo(GhostNodeInner)

function RelEdgeInner({
  id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data,
}: EdgeProps) {
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, borderRadius: 10,
  })
  const dangling = !!(data as { dangling?: boolean } | undefined)?.dangling
  const rel = ((data as { rel?: string } | undefined)?.rel) ?? ''
  return (
    <>
      <BaseEdge
        id={id} path={path}
        style={{
          stroke: dangling ? 'var(--muted)' : 'var(--accent)',
          strokeWidth: 1.4,
          strokeDasharray: dangling ? '4 4' : undefined,
          opacity: dangling ? 0.6 : 0.85,
        }}
      />
      <EdgeLabelRenderer>
        <div
          className="og-edge-label nodrag nopan"
          style={{
            position: 'absolute',
            transform: `translate(-50%,-50%) translate(${labelX}px,${labelY}px)`,
            background: 'var(--panel2)',
            border: `1px solid ${dangling ? 'var(--border)' : 'var(--accent)'}`,
            color: dangling ? 'var(--muted)' : 'var(--text)',
          }}
        >
          {rel}
        </div>
      </EdgeLabelRenderer>
    </>
  )
}
const RelEdge = memo(RelEdgeInner)

const nodeTypes = { candidate: CandidateNode, ghost: GhostNode }
const edgeTypes = { rel: RelEdge }

// ---------------- graph building ----------------

function toFlowData(nodes: Cand[], edgesRaw: Rel[]): { nodes: Node[]; edges: Edge[] } {
  const fnodes: Node[] = nodes.map((c) => ({
    id: String(c.id), type: 'candidate', position: { x: 0, y: 0 },
    data: { cand: c, selected: false, seed: false } satisfies CandNodeData,
  }))
  // dangling targets become ghost nodes so the relation stays visible
  const ghostIds = new Set<string>()
  edgesRaw.filter((e) => e.dangling).forEach((e) => {
    const gid = `ghost-${e.id}`
    if (!ghostIds.has(gid)) {
      ghostIds.add(gid)
      fnodes.push({
        id: gid, type: 'ghost', position: { x: 0, y: 0 },
        data: { label: e.target_name }, deletable: false, draggable: true,
      })
    }
  })
  const fedges: Edge[] = edgesRaw.map((e) => ({
    id: `e-${e.id}`, type: 'rel', source: String(e.source),
    target: e.dangling ? `ghost-${e.id}` : String(e.target ?? e.source),
    data: { rel: e.relation_type, dangling: e.dangling },
  }))
  return { nodes: fnodes, edges: fedges }
}

// ---------------- page ----------------

function OntologyGraphInner({ refreshKey, focusChunkId, chunks = 0 }: Props) {
  const [cands, setCands] = useState<Cand[]>([])
  const [edgesRaw, setEdgesRaw] = useState<Rel[]>([])
  const [examStat, setExamStat] = useState<{
    quiz_total: number; pass: number; fail: number; missing: number;
    ablation?: {
      with_ontology_pass: number; with_ontology_fail: number;
      ablated_pass: number; ablated_fail: number; gain: number;
      issues: Record<string, number>;
    }
  } | null>(null)
  const [hidden, setHidden] = useState<Set<number>>(new Set()) // removed from canvas
  const [nodes, setNodes] = useState<Node[]>([])
  const [edges, setEdges] = useState<Edge[]>([])
  const [sel, setSel] = useState<number | null>(null)
  const [detail, setDetail] = useState<any>(null)
  const [texts, setTexts] = useState<Record<number, string>>({})
  const [q, setQ] = useState('')
  const [statusFilter, setStatusFilter] = useState<'all' | 'pending' | 'approved' | 'doubt'>('all')
  const [mergeInto, setMergeInto] = useState('')
  const [checked, setChecked] = useState<Set<number>>(new Set())
  const [deleting, setDeleting] = useState(false)
  const [orch, setOrch] = useState<OrchSummary | null>(null)
  const [orchFilter, setOrchFilter] = useState<'all' | 'delete' | 'merge' | 'keep'>('all')
  const [orching, setOrching] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [seedMode, setSeedMode] = useState(false)
  const [seedSel, setSeedSel] = useState<Set<number>>(new Set())
  const [seedName, setSeedName] = useState('')
  const [seedMission, setSeedMission] = useState('')
  const [seedCreating, setSeedCreating] = useState(false)
  const [idVer, setIdVer] = useState(0)
  const relaid = useRef(false)
  const canvasRef = useRef<HTMLDivElement>(null)
  const { fitView, getNodes } = useReactFlow()
  const { toast } = useToast()

  const visible = useMemo(() => cands.filter((c) => !hidden.has(c.id)), [cands, hidden])

  const reload = useCallback(() => {
    getGraph().then((g) => {
      setCands(g.nodes)
      setEdgesRaw(g.edges)
      setExamStat(g.exam ?? null)
    }).catch(() => { })
  }, [])
  const reloadOrch = useCallback(() => {
    getOrchestration().then(setOrch).catch(() => { })
  }, [])
  useEffect(() => { reload() }, [reload, refreshKey])
  useEffect(() => { if (focusChunkId) reload() }, [focusChunkId, reload])
  useEffect(() => { reloadOrch() }, [reloadOrch, refreshKey])

  // build graph once data changes, but keep user-dragged positions afterwards
  useEffect(() => {
    const srcEdges = edgesRaw.filter((e) => !hidden.has(e.source))
    const built = toFlowData(visible, srcEdges)
    let laid = dagreLayout(built.nodes, built.edges)
    if (relaid.current) {
      // preserve dragged positions: only add/remove, don't reset layout
      laid = built.nodes.map((n) => {
        const old = nodes.find((o) => o.id === n.id)
        return old ? { ...n, position: old.position } : n
      })
    }
    setNodes(laid)
    setEdges(built.edges)
    if (!relaid.current && built.nodes.length) {
      relaid.current = true
      requestAnimationFrame(() => fitView({ padding: 0.15, duration: 400 }))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, edgesRaw])

  // one-click auto-arrange: dagre re-layout of ALL nodes (clears drag chaos
  // / overlaps), then fit view
  const autoLayout = useCallback(() => {
    setNodes((ns) => dagreLayout(ns, edges))
    requestAnimationFrame(() => fitView({ padding: 0.15, duration: 400 }))
  }, [edges, fitView])

  // search: dim non-matches on the canvas + bring matches into view, so a
  // searched candidate is actually VISIBLE among hundreds of nodes
  useEffect(() => {
    const term = q.trim().toLowerCase()
    const cur = getNodes()
    if (!term) {
      const hadDim = cur.some((n) => n.data?.searchDim)
      if (hadDim) {
        setNodes((ns) => ns.map((n) => (n.data?.searchDim
          ? { ...n, data: { ...n.data, searchDim: false } } : n)))
      }
      return
    }
    const matchIds = new Set(
      cands.filter((c) => c.name.toLowerCase().includes(term)).map((c) => String(c.id)))
    setNodes((ns) => ns.map((n) => {
      if (n.type !== 'candidate') return n
      const dim = !matchIds.has(n.id)
      return n.data?.searchDim === dim ? n : { ...n, data: { ...n.data, searchDim: dim } }
    }))
    const matched = cur.filter((n) => matchIds.has(n.id))
    if (matched.length) {
      requestAnimationFrame(() => fitView({ nodes: matched, padding: 0.3, duration: 300 }))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, cands, visible])

  // container resize (preview panel show/hide, window resize, side changes):
  // re-fit so the whole graph stays reachable instead of breaking the zoom
  useEffect(() => {
    const el = canvasRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    let raf = 0
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(() => fitView({ padding: 0.15, duration: 250 }))
    })
    ro.observe(el)
    return () => { cancelAnimationFrame(raf); ro.disconnect() }
  }, [fitView])

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((ns) => applyNodeChanges(changes, ns))
    const removed = changes.filter((c) => c.type === 'remove')
    if (removed.length) {
      const ids = removed.map((r) => Number(r.id))
      setHidden((h) => new Set([...h, ...ids.filter((i) => !Number.isNaN(i))]))
      setSel((s) => (s !== null && ids.includes(s) ? null : s))
    }
  }, [])
  const onEdgesChange = useCallback(
    (changes: any) => setEdges((es) => applyEdgeChanges(changes, es)), [],
  )

  const open = async (id: number) => {
    setSel(id)
    setDetail(null)
    try {
      const d = await getCandidate(id)
      setDetail(d)
      const need = [...new Set(d.mentions.map((m: any) => m.chunk_id))] as number[]
      const entries = await Promise.all(need.map(async (cid) => {
        try { const c = await getChunk(cid); return [cid, c.text] as const }
        catch { return [cid, ''] as const }
      }))
      setTexts((prev) => ({ ...prev, ...Object.fromEntries(entries) }))
    } catch (e: any) { toast(e.message, 'err') }
  }

  const act = async (status: string) => {
    if (!sel) return
    try {
      await setStatus(sel, status)
      toast(status === 'approved' ? '已批准入本体库' : status === 'rejected' ? '已拒绝' : '已暂缓', 'ok')
      setCands((cs) => cs.map((c) => (c.id === sel ? { ...c, status } : c)))
      setDetail((d: any) => (d ? { ...d, candidate: { ...d.candidate, status } } : d))
      if (status === 'rejected') { setSel(null); setDetail(null); setHidden((h) => new Set([...h, sel])) }
    } catch (e: any) { toast(e.message, 'err') }
  }

  const doMerge = async (into: number) => {
    if (!sel || !into) return
    try {
      await mergeCandidate(sel, into)
      toast(`已合并到 #${into}`, 'ok')
      setHidden((h) => new Set([...h, sel]))
      setSel(null); setDetail(null)
      reload()
    } catch (e: any) { toast(e.message, 'err') }
  }

  const removeNode = (id: number) => {
    setHidden((h) => new Set([...h, id]))
    setSel((s) => (s === id ? null : s))
    setDetail(null)
  }
  const restoreAll = () => { setHidden(new Set()); relaid.current = false }

  const isDoubtful = (c: Cand) =>
    !!c.exam && (c.exam.fail > 0 || c.exam.missing > 0)

  const filtered = visible.filter((c) =>
    (statusFilter === 'all' ||
      (statusFilter === 'doubt' ? isDoubtful(c) : c.status === statusFilter)) &&
    (!q || c.name.toLowerCase().includes(q.toLowerCase())),
  )

  // ---------------- multi-select + batch delete ----------------
  const filteredIds = useMemo(() => filtered.map((c) => c.id), [filtered])
  const allChecked = filteredIds.length > 0 && filteredIds.every((id) => checked.has(id))
  const someChecked = filteredIds.some((id) => checked.has(id))

  const toggleCheck = (id: number) => {
    setChecked((s) => {
      const next = new Set(s)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }
  const toggleAll = () => {
    setChecked((s) => {
      const next = new Set(s)
      if (filteredIds.every((id) => next.has(id))) filteredIds.forEach((id) => next.delete(id))
      else filteredIds.forEach((id) => next.add(id))
      return next
    })
  }
  const doBatchDelete = async () => {
    const ids = [...checked]
    if (!ids.length || deleting) return
    if (!window.confirm(
      `确定永久删除选中的 ${ids.length} 个候选本体？\n（连同其证据与出边关系一并删除，不可恢复）`))
      return
    setDeleting(true)
    try {
      const r = await deleteCandidates(ids)
      toast(`已删除 ${r.deleted} 个候选`, 'ok')
      setChecked(new Set())
      setHidden((h) => new Set([...h, ...ids]))
      setSel((s) => (s !== null && ids.includes(s) ? null : s))
      setDetail(null)
      reload()
    } catch (e: any) { toast(e.message, 'err') }
    finally { setDeleting(false) }
  }
  // sync selection + seed highlight
  useEffect(() => {
    setNodes((ns) => ns.map((x) => ({
      ...x,
      data: { ...x.data, selected: x.id === String(sel), seed: seedMode && seedSel.has(Number(x.id)) },
    })))
  }, [sel, nodes.length, seedMode, seedSel])

  // ---------------- exam (quiz grading before approval) ----------------
  const doRunExam = async () => {
    try {
      const r = await runExam()
      toast(`考核任务 #${r.job_id} 已启动`, 'ok')
    } catch (e: any) { toast(e.message, 'err') }
  }

  // ---------------- orchestration (二次编排: rule clean + GLM triage) ----------------
  const doOrchestrate = async () => {
    if (orching) return
    setOrching(true)
    try {
      const r = await runOrchestrate()
      toast(`二次编排任务 #${r.job_id} 已启动（GLM 相关度分档 + 删除/合并提名）`, 'ok')
    } catch (e: any) { toast(e.message, 'err') }
    finally { setOrching(false) }
  }
  const doConfirmOrch = async () => {
    const total = orch?.total ?? 0
    if (!total || confirming) return
    if (!window.confirm(
      `确认落地待确认清单（${total} 条建议）？\n删除/合并将立即执行，keep 无操作。`))
      return
    setConfirming(true)
    try {
      const r = await confirmOrchestration()
      toast(`已落地：删除 ${r.deleted} · 合并 ${r.merged} · 保留 ${r.kept}${r.failed ? ` · 失败 ${r.failed}` : ''}`, 'ok')
      reloadOrch(); reload()
    } catch (e: any) { toast(e.message, 'err') }
    finally { setConfirming(false) }
  }
  const doDiscardOrch = async () => {
    const total = orch?.total ?? 0
    if (!total) return
    if (!window.confirm(`丢弃全部 ${total} 条待确认建议？（不执行任何删除/合并）`)) return
    try {
      await discardOrchestration()
      toast('已丢弃待确认清单', 'ok')
      reloadOrch()
    } catch (e: any) { toast(e.message, 'err') }
  }
  const doDismissOrch = async (id: number) => {
    try {
      await dismissOrchItem(id)
      reloadOrch()
    } catch (e: any) { toast(e.message, 'err') }
  }

  const orchItems = useMemo<OrchItem[]>(() => {
    const items = orch?.items ?? []
    return orchFilter === 'all' ? items : items.filter((i) => i.action === orchFilter)
  }, [orch, orchFilter])

  // ---------------- 从图谱创建数字人（点选种子 → AI 从本体库提取适配本体） ----------------
  const toggleSeed = (id: number) => {
    setSeedSel((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })
  }
  const enterSeedMode = () => { setSeedMode(true); setSeedSel(new Set()); setSeedName(''); setSeedMission('') }
  const cancelSeedMode = () => { setSeedMode(false); setSeedSel(new Set()); setSeedName(''); setSeedMission('') }
  const doCreateFromSeeds = async () => {
    if (!seedName.trim()) { toast('请填写数字人名称', 'err'); return }
    if (!seedSel.size) { toast('请先在图谱中点选至少一个种子节点', 'err'); return }
    setSeedCreating(true)
    try {
      await createIdentity(seedName.trim(), seedMission.trim(), [...seedSel])
      toast(`数字人「${seedName.trim()}」已创建（${seedSel.size} 个种子）`, 'ok')
      setIdVer((v) => v + 1)
      cancelSeedMode()
    } catch (e: any) { toast(e.message, 'err') }
    finally { setSeedCreating(false) }
  }

  // highlight the matched substring in a candidate name
  const hl = (name: string) => {
    const term = q.trim()
    if (!term) return name
    const i = name.toLowerCase().indexOf(term.toLowerCase())
    if (i < 0) return name
    return (
      <>{name.slice(0, i)}<mark>{name.slice(i, i + term.length)}</mark>{name.slice(i + term.length)}</>
    )
  }

  const examBadge = (c: Cand) => {
    if (!c.exam) return null
    const { pass, fail, missing } = c.exam
    if (!pass && !fail && !missing) return null
    const cls = fail > 0 ? 'fail' : missing > 0 ? 'miss' : 'pass'
    const txt = `✓${pass}` + (fail ? ` ✗${fail}` : '') + (missing ? ` ?${missing}` : '')
    return <span className={`exam-badge ${cls}`} title="考核计数：通过 / 不及格 / 存疑">{txt}</span>
  }

  const stat = {
    pending: cands.filter((c) => c.status === 'pending' && !hidden.has(c.id)).length,
    approved: cands.filter((c) => c.status === 'approved' && !hidden.has(c.id)).length,
    doubt: cands.filter((c) => !hidden.has(c.id) && isDoubtful(c)).length,
    hidden: hidden.size,
  }

  return (
    <>
      <IdentityPanel refreshKey={refreshKey + idVer} chunks={chunks} />

      {orch && orch.total > 0 && (
        <div className="card">
          <h3>
            二次编排 · 待确认清单
            <span className="btnrow" style={{ marginLeft: 'auto', display: 'inline-flex' }}>
              <span className="note">
                规则 {orch.by_source.rule ?? 0} · GLM {orch.by_source.glm ?? 0}
                {' '}· 删除 {orch.by_action.delete ?? 0} · 合并 {orch.by_action.merge ?? 0}
                {' '}· 保留 {orch.by_action.keep ?? 0}
              </span>
              <button className="btn green small" onClick={doConfirmOrch} disabled={confirming}>
                {confirming ? '落地中…' : '一键终审落地'}
              </button>
              <button className="btn ghost small" onClick={doDiscardOrch}>丢弃清单</button>
            </span>
          </h3>
          <div className="desc">
            提取完成自动跑确定性清洗（纯数字/单字符/引用/大小写去重）；点「保存并二次编排」由 GLM 做相关度分档。所有建议只是提名——点「一键终审落地」才真正删除/合并。
          </div>
          <div className="og-seg orch-seg">
            {(['all', 'delete', 'merge', 'keep'] as const).map((s) => (
              <button key={s} className={orchFilter === s ? 'on' : ''}
                onClick={() => setOrchFilter(s)}>
                {s === 'all' ? `全部 ${orch.total}` : s === 'delete' ? `删除 ${orch.by_action.delete ?? 0}`
                  : s === 'merge' ? `合并 ${orch.by_action.merge ?? 0}` : `保留 ${orch.by_action.keep ?? 0}`}
              </button>
            ))}
          </div>
          <div className="orch-list">
            {orchItems.map((it) => (
              <div key={it.id} className="orch-item">
                <span className={`orch-act ${it.action}`}>
                  {it.action === 'delete' ? '删' : it.action === 'merge' ? '并' : '留'}
                </span>
                {it.category && <span className={`orch-cat ${it.category}`}>
                  {it.category === 'core' ? '核心' : it.category === 'marginal' ? '边缘' : '无关'}
                </span>}
                <span className="orch-name" title={it.reason ?? undefined}>
                  {it.cand_name ?? `#${it.candidate_id}`}
                </span>
                {it.action === 'merge' && it.merge_into != null && (
                  <span className="orch-into">→ #{it.merge_into}</span>
                )}
                <span className="orch-src">{it.source === 'rule' ? '规则' : 'GLM'}</span>
                <span className="orch-reason">{it.reason}</span>
                <button className="btn ghost small" onClick={() => doDismissOrch(it.id)}>忽略</button>
              </div>
            ))}
          </div>
          {orch.truncated && (
            <div className="note">仅显示前 {orch.items.length} 条，共 {orch.total} 条待确认</div>
          )}
        </div>
      )}

      <div className="card">
      <h3>
        本体图谱
        <span className="btnrow" style={{ marginLeft: 'auto', display: 'inline-flex' }}>
          {examStat && examStat.quiz_total > 0 && (
            <span className="note">
              考题 {examStat.quiz_total} · 通过 {examStat.pass} · 不及格 {examStat.fail} · 存疑 {examStat.missing}
              {examStat.ablation && (
                <span title={`A/B 消融：同题双跑「问题+本体子图」vs「只给问题」，由 GLM 开卷判别。增益 = 有本体通过数 - 无本体通过数`}>
                  &nbsp;| 本体增益 <b className={examStat.ablation.gain > 0 ? 'ok' : 'warn'}>{examStat.ablation.gain > 0 ? '+' : ''}{examStat.ablation.gain}</b>
                  &nbsp;(有本体 {examStat.ablation.with_ontology_pass}/{examStat.ablation.with_ontology_pass + examStat.ablation.with_ontology_fail} · 无本体 {examStat.ablation.ablated_pass}/{examStat.ablation.ablated_pass + examStat.ablation.ablated_fail})
                  {examStat.ablation.issues && (() => {
                    const it = examStat.ablation.issues
                    const parts: string[] = []
                    if (it.retrieval) parts.push(`检索缺 ${it.retrieval}`)
                    if (it.ontology) parts.push(`本体错 ${it.ontology}`)
                    if (it.context) parts.push(`原文不足 ${it.context}`)
                    if (it.unused) parts.push(`未用本体 ${it.unused}`)
                    return parts.length ? <> · {parts.join(' / ')}</> : null
                  })()}
                </span>
              )}
            </span>
          )}
          <button className="btn ghost small" onClick={doRunExam}
            title="用提取时生成的考题考核候选本体：不及格且从未通过者自动删除，存疑保留待你终审">运行考核</button>
          <button className="btn ghost small" onClick={doOrchestrate} disabled={orching}
            title="调用 GLM 5.2 做垂直领域相关度分档 + 删除/合并提名，产出待确认清单，你一键终审才落地">
            {orching ? '编排中…' : '保存并二次编排'}</button>
          <button className={`btn ${seedMode ? 'orange' : 'green'} small`}
            onClick={seedMode ? cancelSeedMode : enterSeedMode}
            title="从本体图谱点选节点作为种子 → 创建数字人 → AI 从本体库提取适配本体到本体段">
            {seedMode ? '取消选择' : '从图谱创建数字人'}</button>
          <button className="btn ghost small" onClick={autoLayout}
            title="节点叠起来/拖乱了，一键 dagre 重排并自适应视野">自动整理</button>
          {stat.hidden > 0 && (
            <button className="btn ghost small" onClick={restoreAll}>
              撤销 {stat.hidden} 处删除
            </button>
          )}
          <span className="note">待审 {stat.pending} · 已批准 {stat.approved} · 存疑 {stat.doubt}</span>
        </span>
      </h3>
      <div className="desc">LLM 只提名：候选须过三关校验才来到这里，你批准后才进本体库。左列表点选 / 画布点节点查看证据与审批。考核后「不及格且从未通过」的待审候选被直接删除；「存疑」（候选不足/矛盾）保留由你终审。</div>
      {seedMode && (
        <div className="seed-bar">
          <span className="note">点选画布节点或左列表勾选作为种子（已选 <b>{seedSel.size}</b> 个）→ 创建后 AI 从本体库提取适配本体</span>
          <input placeholder="数字人名称（必填）" value={seedName}
            onChange={(e) => setSeedName(e.target.value)} />
          <input placeholder="使命（一句话，可选）" value={seedMission}
            onChange={(e) => setSeedMission(e.target.value)} />
          <button className="btn green small" onClick={doCreateFromSeeds}
            disabled={seedCreating || !seedSel.size || !seedName.trim()}>
            {seedCreating ? '创建中…' : `创建数字人${seedSel.size ? `（${seedSel.size} 种子）` : ''}`}
          </button>
          <button className="btn ghost small" onClick={cancelSeedMode}>取消</button>
        </div>
      )}
      <div className="og-wrap">
        <aside className="og-side">
          <input placeholder="搜索候选…" value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="og-seg">
            {(['all', 'pending', 'approved', 'doubt'] as const).map((s) => (
              <button key={s} className={statusFilter === s ? 'on' : ''}
                onClick={() => setStatusFilter(s)}>
                {s === 'all' ? '全部' : s === 'pending' ? '待审' : s === 'approved' ? '已批准' : '存疑'}
              </button>
            ))}
          </div>
          {seedMode ? (
            <div className="og-batch">
              <span className="note">种子模式：已选 {seedSel.size} 个</span>
              <button className="btn ghost small" onClick={() => setSeedSel(new Set())}>清空</button>
            </div>
          ) : (
            <div className="og-batch">
              <label className="og-selall">
                <input type="checkbox" checked={allChecked}
                  ref={(el) => { if (el) el.indeterminate = someChecked && !allChecked }}
                  onChange={toggleAll} />
                全选{filteredIds.length ? ` (${filteredIds.length})` : ''}
              </label>
              <button className="btn red small" disabled={!checked.size || deleting}
                onClick={doBatchDelete}>
                {deleting ? '删除中…' : `删除所选${checked.size ? ` (${checked.size})` : ''}`}
              </button>
            </div>
          )}
          <div className="og-list">
            {filtered.map((c) => (
              <div key={c.id}
                className={`og-item ${c.id === sel ? 'on' : ''} ${seedMode && seedSel.has(c.id) ? 'seed' : ''}`}
                onClick={() => { if (seedMode) toggleSeed(c.id); else open(c.id) }}>
                <input type="checkbox" className="og-check"
                  checked={seedMode ? seedSel.has(c.id) : checked.has(c.id)}
                  onClick={(e) => e.stopPropagation()}
                  onChange={() => { if (seedMode) toggleSeed(c.id); else toggleCheck(c.id) }} />
                <span className="k">{c.kind}</span>
                <span className="nm">{hl(c.name)}</span>
                {examBadge(c)}
                <span className="mn">×{c.mentions}</span>
              </div>
            ))}
            {filtered.length === 0 && <div className="note">无匹配候选</div>}
          </div>
        </aside>

        <div className="og-canvas" ref={canvasRef}>
          <ReactFlow
            nodes={nodes} edges={edges}
            nodeTypes={nodeTypes} edgeTypes={edgeTypes}
            onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
            onNodeClick={(_, n) => {
              if (n.type !== 'candidate') return
              const id = Number(n.id)
              if (seedMode) toggleSeed(id)
              else open(id)
            }}
            onNodeDoubleClick={(_, n) => { if (n.type === 'candidate') removeNode(Number(n.id)) }}
            fitView fitViewOptions={{ padding: 0.15 }} minZoom={0.02} maxZoom={2.5}
            deleteKeyCode={['Delete', 'Backspace']}
            proOptions={{ hideAttribution: true }}
          >
            <Background variant={BackgroundVariant.Dots} gap={22} size={1.4} color="#2a2e36" />
            <Controls showInteractive={false} />
            <MiniMap
              pannable zoomable
              nodeColor={(n) => (n.type === 'ghost' ? '#3a3d44'
                : (n.data as any).cand?.status === 'approved' ? '#4f83cc' : '#d98324')}
              maskColor="rgba(20,22,26,0.75)"
              style={{ background: '#1d2026', border: '1px solid #32363e' }}
            />
          </ReactFlow>
          <div className="og-legend">
            <span><i className="d approved" />已批准（实线蓝）</span>
            <span><i className="d pending" />待审（虚线橙）</span>
            <span><i className="d ghost" />悬空目标（虚灰）</span>
            <span className="tip">拖拽 · 滚轮缩放 · 双击/Delete 移出画布 · 叠起来点「自动整理」</span>
          </div>
        </div>

        {sel !== null && detail && (
          <aside className="og-detail">
            <h4>{detail.candidate.name}
              <span className={`status-pill ${detail.candidate.status}`}>{detail.candidate.status}</span>
            </h4>
            <div className="def">{detail.candidate.definition || '（无定义）'}</div>
            <div className="note">类型 {detail.candidate.kind} · 证据 {detail.mentions.length} 处</div>
            {detail.mentions.map((m: any) => {
              const text = texts[m.chunk_id] || ''
              const before = text.slice(Math.max(0, m.span_start - 30), m.span_start)
              const hit = text.slice(m.span_start, m.span_end)
              const after = text.slice(m.span_end, m.span_end + 30)
              return (
                <div className="evidence" key={m.id}>
                  <div>…{before}<span className="mark">{hit}</span>{after}…</div>
                  <div className="src">chunk #{m.chunk_id} · span [{m.span_start}, {m.span_end}]</div>
                </div>
              )
            })}
            {detail.relations.map((r: any) => (
              <div className="evidence" key={r.id}>关系 → {r.target_name}（{r.relation_type}）</div>
            ))}
            {detail.exam && detail.exam.length > 0 && (
              <div style={{ marginTop: 10 }}>
                <div className="note">考题考核（建议，非终审）：</div>
                {detail.exam.map((x: any, i: number) => (
                  <div className={`evidence exam-${x.verdict}`} key={i}>
                    <div>
                      <span className={`status-pill small ${x.verdict === 'pass' ? 'approved' : x.verdict === 'fail' ? 'rejected' : 'autopaused'}`}>
                        {x.verdict === 'pass' ? '通过' : x.verdict === 'fail' ? '不及格' : '存疑'}
                      </span>{' '}
                      {x.question}
                    </div>
                    <div className="src">预期答案：{x.answer}</div>
                  </div>
                ))}
              </div>
            )}
            <div className="btnrow" style={{ marginTop: 10 }}>
              <button className="btn green" onClick={() => act('approved')}>批准</button>
              <button className="btn red" onClick={() => act('rejected')}>拒绝</button>
              <button className="btn ghost" onClick={() => act('pending')}>暂缓</button>
            </div>
            {detail.similar.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <div className="note">疑似同义候选（可合并）：</div>
                <div className="btnrow" style={{ marginTop: 6 }}>
                  <input value={mergeInto} placeholder="合并到候选 ID"
                    onChange={(e) => setMergeInto(e.target.value)} style={{ width: 130 }} />
                  <button className="btn orange" onClick={() => doMerge(Number(mergeInto))}
                    disabled={!mergeInto || Number.isNaN(Number(mergeInto))}>合并</button>
                </div>
                <div className="btnrow">
                  {detail.similar.map((s: any) => (
                    <button key={s.id} className="btn ghost small"
                      onClick={() => doMerge(s.id)}>合并到 #{s.id} {s.name}</button>
                  ))}
                </div>
              </div>
            )}
            <div className="btnrow" style={{ marginTop: 10 }}>
              <button className="btn ghost small" onClick={() => removeNode(sel)}>从画布移出</button>
              <button className="btn ghost small" onClick={() => { setSel(null); setDetail(null) }}>关闭</button>
            </div>
          </aside>
        )}
      </div>
    </div>
    </>
  )
}

export default function OntologyPage(props: Props) {
  return (
    <ReactFlowProvider>
      <OntologyGraphInner {...props} />
    </ReactFlowProvider>
  )
}
