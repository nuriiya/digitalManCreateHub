import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  getIdentities, nominateIdentities, setIdentityStatus, deleteIdentity,
  updateIdentity, setAnchorStatus, updateAnchor, addAnchor, createIdentity,
  runAssemble, getAssembly, confirmAssembly, discardAssembly, restoreAssembly,
  dismissAsmItem, getPersonaOntology, getIdentityMcp, syncIdentityMcp,
  runBenchmark, getBenchmark, rejectBenchChange, mergeBenchmark, rollbackBenchmark,
  type Identity, type AsmSummary, type PersonaOntItem, type BenchSummary, type PersonaMcp,
} from '../api'
import { useToast } from '../Toast'

interface Props {
  refreshKey: number
  chunks: number
}

// 数字人分类（axis = 本体从哪来，见 doc/顶层架构.md §0）
const CATEGORY_LABEL: Record<string, string> = {
  general: '通用数字人',
  domain_expert: '执行领域专家',
}
const CATEGORY_ORDER = ['general', 'domain_expert']

/** 数字人管理中心：三块（已有 / 备选 / 创造·删除·修改）。已有数字人卡片内嵌
 * 「本体段」与「本体装配」子模块。多数字人并存，各自独立装配与迭代。 */
export default function IdentityPanel({ refreshKey, chunks }: Props) {
  const [idents, setIdents] = useState<Identity[]>([])
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState<number | null>(null) // anchor id being edited
  const [draft, setDraft] = useState({ name: '', type: '概念', definition: '' })
  const [adding, setAdding] = useState<number | null>(null) // identity id being extended
  const [newAnchor, setNewAnchor] = useState({ name: '', type: '概念', definition: '' })
  const [editingId, setEditingId] = useState<number | null>(null) // identity being edited
  const [idDraft, setIdDraft] = useState({ name: '', mission: '', prompt: '', category: 'domain_expert', reactive: false })
  const [creating, setCreating] = useState(false)
  const [createDraft, setCreateDraft] = useState({ name: '', mission: '', prompt: '', category: 'domain_expert' })
  // assembly per persona
  const [asm, setAsm] = useState<Record<number, AsmSummary>>({})
  const [personaOnt, setPersonaOnt] = useState<Record<number, PersonaOntItem[]>>({})
  const [assemblingId, setAssemblingId] = useState<number | null>(null)
  const [confirmingId, setConfirmingId] = useState<number | null>(null)
  // collapsible ontology library + assembly per persona
  const [ontOpen, setOntOpen] = useState<Record<number, boolean>>({})
  const [asmOpen, setAsmOpen] = useState<Record<number, boolean>>({})
  // benchmark (四组对照测试 + 归因提名 + 版本管理) per persona
  const [bench, setBench] = useState<Record<number, BenchSummary>>({})
  const [bchOpen, setBchOpen] = useState<Record<number, boolean>>({})
  const [bchBusy, setBchBusy] = useState<number | null>(null)
  // MCP 绑定（可调用 MCP 工具清单 + 同步到本体）per persona
  const [mcpMap, setMcpMap] = useState<Record<number, PersonaMcp[]>>({})
  const [mcpOpen, setMcpOpen] = useState<Record<number, boolean>>({})
  const { toast } = useToast()

  const approved = useMemo(() => idents.filter((i) => i.status === 'approved'), [idents])
  const alternates = useMemo(() => idents.filter((i) => i.status !== 'approved'), [idents])
  // 已有数字人按分类分组：通用数字人（general）在前，执行领域专家（domain_expert）在后
  const general = useMemo(() => approved.filter((i) => i.category === 'general'), [approved])
  const experts = useMemo(() => approved.filter((i) => i.category !== 'general'), [approved])

  const reload = useCallback(() => {
    getIdentities().then((r) => setIdents(r.identities ?? [])).catch(() => { })
  }, [])
  useEffect(() => { reload() }, [reload, refreshKey])

  // load each approved persona's own assembly + ontology 段
  const reloadAsm = useCallback(() => {
    approved.forEach((it) => {
      getAssembly(it.id).then((s) => setAsm((m) => ({ ...m, [it.id]: s }))).catch(() => { })
      getPersonaOntology(it.id).then((p) => setPersonaOnt((m) => ({ ...m, [it.id]: p }))).catch(() => { })
    })
  }, [approved])
  useEffect(() => { reloadAsm() }, [reloadAsm, refreshKey])

  // load each approved persona's benchmark summary; poll while a run is active
  const reloadBench = useCallback(() => {
    approved.forEach((it) => {
      getBenchmark(it.id).then((s) => setBench((m) => ({ ...m, [it.id]: s }))).catch(() => { })
    })
  }, [approved])
  useEffect(() => { reloadBench() }, [reloadBench, refreshKey])
  const benchRunning = approved.some((it) => bench[it.id]?.benchmark?.status === 'running')
  useEffect(() => {
    if (!benchRunning) return
    const t = setTimeout(reloadBench, 5000)
    return () => clearTimeout(t)
  }, [benchRunning, reloadBench, bench])

  const nominate = async () => {
    setBusy(true)
    try {
      const r = await nominateIdentities()
      toast(`身份提名任务 #${r.job_id} 已启动（高频词统计 → LLM 提名）`, 'ok')
    } catch (e: any) { toast(e.message, 'err') }
    finally { setBusy(false) }
  }

  const identityDelete = async (id: number) => {
    if (!confirm('确认删除此数字人？（其本体段一并删除，不可恢复）')) return
    try {
      await deleteIdentity(id)
      toast('数字人已删除', 'ok')
      reload()
    } catch (e: any) { toast(e.message, 'err') }
  }

  const approve = async (id: number) => {
    try {
      await setIdentityStatus(id, 'approved')
      toast('已批准，加入「已有数字人」', 'ok')
      reload()
    } catch (e: any) { toast(e.message, 'err') }
  }

  const startEditId = (it: Identity) => {
    setEditingId(it.id)
    setIdDraft({ name: it.name, mission: it.mission || '', prompt: it.prompt || '', category: it.category || 'domain_expert', reactive: !!it.reactive })
  }
  const saveEditId = async () => {
    if (!editingId) return
    try {
      await updateIdentity(editingId, {
        name: idDraft.name, mission: idDraft.mission, prompt: idDraft.prompt,
        category: idDraft.category, reactive: idDraft.reactive,
      })
      toast('数字人已更新', 'ok')
      setEditingId(null)
      reload()
    } catch (e: any) { toast(e.message, 'err') }
  }

  const doCreate = async () => {
    const name = createDraft.name.trim()
    const defn = createDraft.mission.trim()
    if (!name && !defn) {
      // 两栏都留空 → 自主创建：LLM 身份提名 job，完成后在「备选数字人」审批
      try {
        const r = await nominateIdentities()
        toast(`已启动自主创建：身份提名任务 #${r.job_id}（高频词统计 → LLM 提名）。完成后在「备选数字人」中审批。`, 'ok')
      } catch (e: any) { toast(e.message, 'err') }
      setCreating(false)
      setCreateDraft({ name: '', mission: '', prompt: '', category: 'domain_expert' })
      return
    }
    if (!name) { toast('请填写数字人名称（或两栏都留空走「自主创建」）', 'err'); return }
    try {
      const r = await createIdentity(name, defn, [], '', createDraft.prompt, createDraft.category)
      toast(`数字人「${name}」已创建`, 'ok')
      setCreateDraft({ name: '', mission: '', prompt: '', category: 'domain_expert' })
      setCreating(false)
      reload()
    } catch (e: any) { toast(e.message, 'err') }
  }

  const anchorAct = async (id: number, status: string) => {
    try { await setAnchorStatus(id, status); reload() } catch (e: any) { toast(e.message, 'err') }
  }
  const startEdit = (a: { id: number; name: string; type: string | null; definition: string | null }) => {
    setEditing(a.id)
    setDraft({ name: a.name, type: a.type || '概念', definition: a.definition || '' })
  }
  const saveEdit = async (id: number) => {
    try { await updateAnchor(id, draft); toast('锚点已更新', 'ok'); setEditing(null); reload() } catch (e: any) { toast(e.message, 'err') }
  }
  const doAdd = async (identityId: number) => {
    if (!newAnchor.name.trim()) return
    try { await addAnchor(identityId, newAnchor); toast('锚点已添加（待批准）', 'ok'); setNewAnchor({ name: '', type: '概念', definition: '' }); reload() } catch (e: any) { toast(e.message, 'err') }
  }

  // ---- assembly sub-module ----
  const doAssemble = async (id: number) => {
    setAssemblingId(id)
    try {
      const r = await runAssemble(id)
      toast(`本体装配任务 #${r.job_id} 已启动`, 'ok')
    } catch (e: any) { toast(e.message, 'err') }
    finally { setAssemblingId(null) }
  }
  const doConfirmAsm = async (id: number) => {
    const s = asm[id]
    if (!s || !s.total) return
    if (!window.confirm(`确认装配 ${s.total} 条待确认候选？adopt 复制进本体段，exclude 忽略。`)) return
    setConfirmingId(id)
    try {
      const r = await confirmAssembly(id)
      toast(`已装配：采纳 ${r.adopted}${r.skipped ? ` · 已存在 ${r.skipped}` : ''} · 排除 ${r.excluded}`, 'ok')
      reloadAsm()
    } catch (e: any) { toast(e.message, 'err') }
    finally { setConfirmingId(null) }
  }
  const doDiscardAsm = async (id: number) => {
    const s = asm[id]
    if (!s || !s.total) return
    if (!window.confirm(`丢弃全部 ${s.total} 条待确认候选？`)) return
    try { await discardAssembly(id); toast('已丢弃装配清单', 'ok'); reloadAsm() } catch (e: any) { toast(e.message, 'err') }
  }
  const doRestoreAsm = async (id: number) => {
    try {
      const r = await restoreAssembly(id)
      toast(`已恢复上次装配：${r.items} 条候选回到待确认`, 'ok')
      setAsmOpen((m) => ({ ...m, [id]: true }))
      reloadAsm()
    } catch (e: any) { toast(e.message, 'err') }
  }
  const doDismissAsm = async (id: number) => {
    try { await dismissAsmItem(id); reloadAsm() } catch (e: any) { toast(e.message, 'err') }
  }

  const toggleOnt = (id: number) => setOntOpen((m) => ({ ...m, [id]: !m[id] }))
  const toggleAsm = (id: number) => setAsmOpen((m) => ({ ...m, [id]: !m[id] }))
  const toggleBch = (id: number) => setBchOpen((m) => ({ ...m, [id]: !m[id] }))
  const toggleMcp = async (id: number) => {
    if (mcpOpen[id]) { setMcpOpen((m) => ({ ...m, [id]: false })); return }
    setMcpOpen((m) => ({ ...m, [id]: true }))
    try {
      const r = await getIdentityMcp(id)
      setMcpMap((m) => ({ ...m, [id]: r.mcp ?? [] }))
    } catch { /* ignore */ }
  }
  const doSyncMcp = async (id: number) => {
    try {
      const r = await syncIdentityMcp(id)
      toast(`已同步 ${r.synced} 个 MCP 工具到本体规则`, 'ok')
    } catch (e: any) { toast(e.message, 'err') }
  }

  // ---- benchmark sub-module (四组对照测试 + 归因提名 + 版本管理) ----
  const doRunBench = async (id: number) => {
    setBchBusy(id)
    try {
      const r = await runBenchmark(id)
      toast(`测试任务 #${r.job_id} 已启动（四组对照 · 本地模型答题 · GLM 判卷）`, 'ok')
      setBchOpen((m) => ({ ...m, [id]: true }))
      setTimeout(reloadBench, 1500)
    } catch (e: any) { toast(e.message, 'err') }
    finally { setBchBusy(null) }
  }
  const doRejectBch = async (id: number, changeId: number) => {
    try { await rejectBenchChange(changeId); reloadBench() } catch (e: any) { toast(e.message, 'err') }
  }
  const doMergeBch = async (id: number) => {
    const s = bench[id]
    const n = s?.changes.length ?? 0
    if (!n) return
    if (!window.confirm(`确认合并 ${n} 条提名进该数字人本体段？合并前会自动快照（版本可回滚）。`)) return
    setBchBusy(id)
    try {
      const r = await mergeBenchmark(id)
      toast(`已合并为版本 v${r.version}：标注 ${r.applied.annotate ?? 0} · 修改 ${r.applied.update ?? 0} · 删除 ${r.applied.delete ?? 0} · 新增 ${r.applied.add ?? 0}`, 'ok')
      reloadBench()
      reloadAsm()
    } catch (e: any) { toast(e.message, 'err') }
    finally { setBchBusy(null) }
  }
  const doRollbackBch = async (id: number, versionId: number, version: number) => {
    if (!window.confirm(`确认回滚到版本 v${version}？（当前本体段会先自动快照，回滚本身可再回滚）`)) return
    setBchBusy(id)
    try {
      await rollbackBenchmark(id, versionId)
      toast(`已回滚到 v${version}`, 'ok')
      reloadBench()
      reloadAsm()
    } catch (e: any) { toast(e.message, 'err') }
    finally { setBchBusy(null) }
  }

  const anchorBlock = (it: Identity) => (
    <div className="id-anchors">
      {it.anchors.map((a) => (
        <div key={a.id} className={`id-anchor st-${a.status}`}>
          {editing === a.id ? (
            <div className="id-anchor-edit">
              <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
              <input value={draft.type} onChange={(e) => setDraft({ ...draft, type: e.target.value })} />
              <input value={draft.definition} placeholder="定义" onChange={(e) => setDraft({ ...draft, definition: e.target.value })} />
              <button className="btn green small" onClick={() => saveEdit(a.id)}>保存</button>
              <button className="btn ghost small" onClick={() => setEditing(null)}>取消</button>
            </div>
          ) : (
            <>
              <span className={`status-pill small ${a.status}`}>{a.status === 'approved' ? '✓' : a.status === 'rejected' ? '✗' : '…'}</span>
              <span className="nm" title={a.definition || ''}>
                {a.name}<span className="tp">（{a.type || '其他'}）</span>
              </span>
              <span className="ops">
                {a.status !== 'approved' && <button className="btn green small" onClick={() => anchorAct(a.id, 'approved')}>批准</button>}
                {a.status !== 'rejected' && <button className="btn ghost small" onClick={() => anchorAct(a.id, 'rejected')}>拒</button>}
                <button className="btn ghost small" onClick={() => startEdit(a)}>编辑</button>
              </span>
            </>
          )}
        </div>
      ))}
      {adding === it.id ? (
        <div className="id-anchor-edit">
          <input placeholder="锚点名" value={newAnchor.name} onChange={(e) => setNewAnchor({ ...newAnchor, name: e.target.value })} />
          <input placeholder="类型" value={newAnchor.type} onChange={(e) => setNewAnchor({ ...newAnchor, type: e.target.value })} />
          <input placeholder="一句话定义" value={newAnchor.definition} onChange={(e) => setNewAnchor({ ...newAnchor, definition: e.target.value })} />
          <button className="btn green small" onClick={() => doAdd(it.id)}>添加</button>
          <button className="btn ghost small" onClick={() => setAdding(null)}>取消</button>
        </div>
      ) : (
        <button className="btn ghost small" onClick={() => setAdding(it.id)}>+ 添加锚点</button>
      )}
    </div>
  )

  // ---- collapsible ontology library + assembly per persona ----
  const ontologyBlock = (it: Identity) => {
    const po = personaOnt[it.id] ?? []
    const open = ontOpen[it.id]
    return (
      <div className="ont-sub">
        <button className={`id-toggle ${open ? 'on' : ''}`} onClick={() => toggleOnt(it.id)}>
          <span className="arrow">{open ? '▼' : '▶'}</span>
          <span>本体库</span>
          <span className="note">{po.length} 个本体</span>
        </button>
        {open && (
          <div className="ont-body">
            {po.length === 0 && <div className="note">暂无已装配本体</div>}
            {po.length > 0 && (
              <div className="ont-list">
                {po.map((p) => (
                  <div key={p.id} className="ont-row">
                    <span className="ont-kind">体</span>
                    <span className="ont-name" title={p.definition ?? undefined}>{p.name}</span>
                    <span className="ont-def">{p.definition}</span>
                    {p.source_candidate_id != null && <span className="ont-src">源 #{p.source_candidate_id}</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    )
  }

  const assemblyBlock = (it: Identity) => {
    const s = asm[it.id]
    const open = asmOpen[it.id]
    const total = s?.total ?? 0
    return (
      <div className="asm-sub">
        <button className={`id-toggle ${open ? 'on' : ''}`} onClick={() => toggleAsm(it.id)}>
          <span className="arrow">{open ? '▼' : '▶'}</span>
          <span>装配本体</span>
          {total > 0 && <span className="note">待确认 {total}</span>}
        </button>
        {open && (
          <div className="asm-body">
            <div className="asm-sub-head">
              <button className="btn green small" onClick={() => doAssemble(it.id)} disabled={assemblingId === it.id}
                title="按此数字人的使命 + 锚点，从候选本体筛选可用本体（锚点召回 + 规则排除 + GLM 分档），你一键终审装配到本体段">
                {assemblingId === it.id ? '装配中…' : '装配更多本体'}
              </button>
              {(s?.restorable ?? 0) > 0 && (
                <button className="btn ghost small" onClick={() => doRestoreAsm(it.id)}
                  title="找回最近一次被丢弃/重跑归档的装配清单，回到待确认状态">
                  恢复上次装配
                </button>
              )}
            </div>
            {s && s.total > 0 && (
              <div className="asm-pending">
                <div className="asm-pending-head">
                  <span className="note">
                    待确认：采纳 {s.by_action.adopt ?? 0} · 排除 {s.by_action.exclude ?? 0}
                    {s.by_category.core != null && <> · 核心 {s.by_category.core}</>}
                  </span>
                  <button className="btn green small" onClick={() => doConfirmAsm(it.id)} disabled={confirmingId === it.id}>
                    {confirmingId === it.id ? '装配中…' : '一键终审装配'}
                  </button>
                  <button className="btn ghost small" onClick={() => doDiscardAsm(it.id)}>丢弃清单</button>
                </div>
                <div className="orch-list">
                  {s.items.map((x) => (
                    <div key={x.id} className="orch-item">
                      <span className={`orch-act ${x.action}`}>{x.action === 'adopt' ? '采' : '除'}</span>
                      {x.category && <span className={`orch-cat ${x.category}`}>{x.category === 'core' ? '核心' : x.category === 'marginal' ? '边缘' : '无关'}</span>}
                      <span className="orch-name" title={x.reason ?? undefined}>
                        {x.cand_name ?? `#${x.candidate_id}`}
                        {x.mentions != null && x.mentions > 0 ? ` ×${x.mentions}` : ''}
                      </span>
                      <span className="orch-reason">{x.reason}</span>
                      <button className="btn ghost small" onClick={() => doDismissAsm(x.id)}>忽略</button>
                    </div>
                  ))}
                </div>
                {s.truncated && <div className="note">仅显示前 {s.items.length} 条，共 {s.total} 条</div>}
              </div>
            )}
          </div>
        )}
      </div>
    )
  }

  const BENCH_ARM_LABELS: Record<string, string> = {
    none: 'G0 裸模型', ontology: 'G1 仅本体', rag: 'G2 仅RAG', rag_ontology: 'G3 RAG+本体',
  }
  const BENCH_ACT_LABELS: Record<string, string> = { annotate: '标', update: '改', delete: '删', add: '增' }

  const benchmarkBlock = (it: Identity) => {
    const s = bench[it.id]
    const open = bchOpen[it.id]
    const b = s?.benchmark
    const changes = s?.changes ?? []
    const versions = s?.versions ?? []
    return (
      <div className="bch-sub">
        <button className={`id-toggle ${open ? 'on' : ''}`} onClick={() => toggleBch(it.id)}>
          <span className="arrow">{open ? '▼' : '▶'}</span>
          <span>测试 · 版本管理</span>
          {b?.status === 'running' && <span className="note">测试运行中…</span>}
          {b?.status === 'done' && b.conclusion && <span className="note bch-note">{b.conclusion}</span>}
          {b?.status !== 'running' && b?.status !== 'done' && b?.status != null && <span className="note">上次测试失败</span>}
          {changes.length > 0 && <span className="note">待审提名 {changes.length}</span>}
          {b?.status !== 'running' && changes.length === 0 && b?.status !== 'done' && (
            <span className="note">未测试</span>
          )}
        </button>
        {open && (
          <div className="bch-body">
            <div className="bch-head">
              <button className="btn small" onClick={() => doRunBench(it.id)} disabled={bchBusy === it.id || b?.status === 'running'}
                title="从题库选与本体段相关的题（默认 15 题），四组对照：无RAG无本体 / 仅本体 / 仅RAG / RAG+本体。本地模型低温答题，GLM 判卷提名 + 确定性代码终审，错误归因提名问题本体">
                {b?.status === 'running' ? '测试运行中…' : bchBusy === it.id ? '启动中…' : '运行测试'}
              </button>
              <span className="note">四组对照 · 结论由代码模板生成 · 提名需你终审</span>
            </div>

            {b?.status === 'running' && <div className="note bch-run">正在答题与判卷（本地模型 + GLM），完成后自动刷新…</div>}
            {b?.status === 'failed' && <div className="note err">{b.error || '测试失败'}</div>}

            {b?.status === 'done' && b.stats && (
              <div className="bch-concl">
                <div className="bch-arms">
                  {Object.keys(BENCH_ARM_LABELS).map((arm) => {
                    const a = b.stats!.arms[arm]
                    if (!a) return null
                    return (
                      <div key={arm} className={`bch-arm ${arm === 'rag_ontology' ? 'best' : ''}`}>
                        <b>{BENCH_ARM_LABELS[arm]}</b>
                        <span className="acc">{a.accuracy}%</span>
                        <span className="sub">幻觉 {a.hallucination}% · 拒答 {a.refusal}%</span>
                      </div>
                    )
                  })}
                </div>
                <div className="bch-concl-text">{b.conclusion}</div>
                <div className="note">
                  本体边际 = G3−G2 · RAG边际 = G3−G1 · 总增益 = G3−G0 · partial {b.stats.arms.rag_ontology?.partial ?? 0} 题（部分正确未计入准确率）
                </div>
              </div>
            )}

            {changes.length > 0 && (
              <div className="bch-pending">
                <div className="bch-pending-head">
                  <span className="note">问题本体提名（GLM 只提名 · 已过闭集校验 · merge 由你终审）</span>
                  <button className="btn green small" onClick={() => doMergeBch(it.id)} disabled={bchBusy === it.id}>
                    {bchBusy === it.id ? '合并中…' : `合并 ${changes.length} 条进本体段`}
                  </button>
                </div>
                <div className="bch-list">
                  {changes.map((c) => (
                    <div key={c.id} className="bch-item">
                      <span className={`bch-act ${c.action}`}>{BENCH_ACT_LABELS[c.action]}</span>
                      <span className="bch-name" title={c.action === 'update' || c.action === 'add' ? (c.suggested_definition ?? '') : (c.note ?? '')}>{c.name}</span>
                      <span className="bch-reason">{c.reason}</span>
                      {(c.action === 'update' || c.action === 'add') && c.suggested_definition && (
                        <span className="bch-fix" title={c.suggested_definition}>{c.action === 'add' ? '新增定义：' : '→ '}{c.suggested_definition.length > 60 ? c.suggested_definition.slice(0, 60) + '…' : c.suggested_definition}</span>
                      )}
                      {c.evidence.length > 0 && (
                        <span className="bch-ev" title={c.evidence.map((e) => e.question).join('\n')}>
                          证据 {c.evidence.length} 题
                        </span>
                      )}
                      <button className="btn ghost small" onClick={() => doRejectBch(it.id, c.id)}>忽略</button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {versions.length > 0 && (
              <div className="bch-vers">
                <div className="note">版本历史（merge / 回滚均先快照，可追溯）</div>
                {versions.map((v) => (
                  <div key={v.id} className="bch-ver">
                    <b>v{v.version}</b>
                    <span className="bch-ver-log">
                      {v.changelog.length === 0 && '（快照基线）'}
                      {v.changelog.map((c, i) => (
                        <span key={i}>
                          {c.action === 'rollback' ? `回滚→v${c.to}` : `${BENCH_ACT_LABELS[c.action ?? 'annotate'] ?? '·'} ${c.name}`}
                          {i < v.changelog.length - 1 ? '，' : ''}
                        </span>
                      ))}
                    </span>
                    <button className="btn ghost small" onClick={() => doRollbackBch(it.id, v.id, v.version)} disabled={bchBusy === it.id}>
                      回滚到此
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    )
  }

  const approvedAnchors = approved.reduce((n, i) => n + i.anchors.filter((a) => a.status === 'approved').length, 0)

  // ---- MCP 绑定子模块（可调用 MCP 工具清单 + 同步到本体规则）----
  const mcpBlock = (it: Identity) => {
    const list = mcpMap[it.id] ?? []
    const open = mcpOpen[it.id]
    return (
      <div className="ont-sub">
        <button className={`id-toggle ${open ? 'on' : ''}`} onClick={() => toggleMcp(it.id)}>
          <span className="arrow">{open ? '▼' : '▶'}</span>
          <span>可调用 MCP</span>
          <span className="note">{open ? `${list.length} 个工具` : ''}</span>
        </button>
        {open && (
          <div className="ont-body">
            {list.length === 0 && <div className="note">暂无绑定的 MCP 工具</div>}
            {list.map((m) => (
              <div key={m.id} className="ont-row">
                <span className="ont-kind" title="MCP 工具">M</span>
                <span className="ont-name" title={m.description}>{m.mcp_tool_name}</span>
                <span className="note">{m.server_name || '?'} · {m.status}</span>
              </div>
            ))}
            <button className="btn ghost small" onClick={() => doSyncMcp(it.id)}>
              同步到本体规则
            </button>
          </div>
        )}
      </div>
    )
  }

  // 已有数字人卡片渲染（通用数字人 / 执行领域专家两组共用）
  const renderApprovedCard = (it: Identity) => (
    <div key={it.id} className={`id-card st-${it.status}`}>
      <div className="id-head">
        <b>{it.name}</b>
        <span className={`status-pill ${it.status}`} style={{ marginLeft: 0 }}>{it.status}</span>
        <span className="status-pill cat" style={{ marginLeft: 0 }}>{CATEGORY_LABEL[it.category] || '执行领域专家'}</span>
        {it.reactive && <span className="status-pill running" style={{ marginLeft: 0 }}>反应式</span>}
        <span className="ops" style={{ marginLeft: 'auto' }}>
          <button className="btn ghost small" onClick={() => startEditId(it)}>修改</button>
          <button className="btn red small" onClick={() => identityDelete(it.id)}>删除</button>
        </span>
      </div>
      {editingId === it.id ? (
        <div className="id-anchor-edit" style={{ margin: '8px 0', flexDirection: 'column' }}>
          <label className="dlg-field">
            <span>名称</span>
            <input value={idDraft.name} placeholder="名称" onChange={(e) => setIdDraft({ ...idDraft, name: e.target.value })} />
          </label>
          <label className="dlg-field">
            <span>使命</span>
            <textarea value={idDraft.mission} placeholder="一句话定义这个数字人负责什么" rows={2}
              onChange={(e) => setIdDraft({ ...idDraft, mission: e.target.value })} />
          </label>
          <label className="dlg-field">
            <span>分类</span>
            <select value={idDraft.category} onChange={(e) => setIdDraft({ ...idDraft, category: e.target.value })}>
              {CATEGORY_ORDER.map((c) => <option key={c} value={c}>{CATEGORY_LABEL[c]}</option>)}
            </select>
          </label>
          <label className="dlg-field" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ margin: 0 }}>反应式循环（写→测→改，能力型数字人打开）</span>
            <input type="checkbox" checked={idDraft.reactive}
              onChange={(e) => setIdDraft({ ...idDraft, reactive: e.target.checked })} />
          </label>
          <label className="dlg-field">
            <span>附加指令（prompt · 对话/装配时注入 · 铁律由系统硬保证不可被覆盖）</span>
            <textarea value={idDraft.prompt} placeholder="如：回答前先给出结论再展开；一律引用本体术语…" rows={4}
              maxLength={4000}
              onChange={(e) => setIdDraft({ ...idDraft, prompt: e.target.value })} />
          </label>
          <div className="dlg-actions">
            <button className="btn ghost small" onClick={() => setEditingId(null)}>取消</button>
            <button className="btn green small" onClick={saveEditId}>保存</button>
          </div>
        </div>
      ) : (
        <>
          {it.mission && <div className="id-mission">{it.mission}</div>}
          {it.prompt ? (
            <div className="id-prompt">
              <span className="id-prompt-lbl" title="可复制；对话与装配时作为附加指令注入，铁律不可被覆盖">附加指令</span>
              <span className="id-prompt-txt">{it.prompt}</span>
            </div>
          ) : null}
        </>
      )}
      {it.keywords.length > 0 && (
        <div className="id-kws">{it.keywords.map((k) => <span key={k} className="kw-chip">{k}</span>)}</div>
      )}
      {anchorBlock(it)}
      {ontologyBlock(it)}
      {assemblyBlock(it)}
      {benchmarkBlock(it)}
      {mcpBlock(it)}
    </div>
  )

  return (
    <div className="card">
      <h3>
        数字人管理中心
        <span className="btnrow" style={{ marginLeft: 'auto', display: 'inline-flex' }}>
          <span className="note">已批准锚点 {approvedAnchors} 个（下次提取时注入 prompt）</span>
          <button className="btn small" onClick={nominate} disabled={busy || !chunks}
            title={chunks ? '高频词汇总（0 LLM）→ LLM 提名 3~5 个身份与锚点 → 逐个审批' : '先入库文档'}>
            提名备选数字人
          </button>
        </span>
      </h3>
      <div className="desc">
        三块管理数字人：<b>已有</b>（已批准，各自独立本体段与装配）· <b>备选</b>（LLM 提名待批）· <b>创造·删除·修改</b>。
        从本体图谱点选节点自定义数字人，请用下方「本体图谱」工具栏的「从图谱创建数字人」。
      </div>

      {/* 块1：已有数字人（按分类分组：通用数字人 / 执行领域专家） */}
      <div className="id-block">
        <div className="id-block-title">已有数字人（已批准 · 正在使用）</div>
        {approved.length === 0 && <div className="note" style={{ padding: '8px 0' }}>尚无已批准的数字人。批准一个备选，或从图谱/手动创建。</div>}
        {general.length > 0 && (
          <>
            <div className="id-cat-title">通用数字人 · {general.length}</div>
            {general.map(renderApprovedCard)}
          </>
        )}
        {experts.length > 0 && (
          <>
            <div className="id-cat-title">执行领域专家 · {experts.length}</div>
            {experts.map(renderApprovedCard)}
          </>
        )}
      </div>

      {/* 块2：备选数字人 */}
      <div className="id-block">
        <div className="id-block-title">备选数字人（提名待批 / 已拒绝）</div>
        {alternates.length === 0 && <div className="note" style={{ padding: '8px 0' }}>暂无备选。点「提名备选数字人」或从图谱自定义。</div>}
        <div className="id-grid">
          {alternates.map((it) => (
            <div key={it.id} className={`id-card st-${it.status}`}>
              <div className="id-head">
                <b>{it.name}</b>
                <span className={`status-pill ${it.status}`}>{it.status}</span>
              </div>
              {it.mission && <div className="id-mission">{it.mission}</div>}
              {it.prompt ? (
                <div className="id-prompt">
                  <span className="id-prompt-lbl" title="可复制；对话与装配时作为附加指令注入，铁律不可被覆盖">附加指令</span>
                  <span className="id-prompt-txt">{it.prompt}</span>
                </div>
              ) : null}
              {it.keywords.length > 0 && (
                <div className="id-kws">{it.keywords.map((k) => <span key={k} className="kw-chip">{k}</span>)}</div>
              )}
              {anchorBlock(it)}
              <div className="btnrow">
                {it.status === 'pending' && (
                  <button className="btn green" onClick={() => approve(it.id)} title="批准并加入已有数字人">批准</button>
                )}
                <button className="btn red" onClick={() => identityDelete(it.id)}>
                  {it.status === 'rejected' ? '删除已拒' : '拒绝'}
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* 块3：创造·删除·修改 */}
      <div className="id-block">
        <div className="id-block-title">创造 · 删除 · 修改数字人</div>
        <div className="btnrow">
          <button className="btn green small" onClick={() => { setCreateDraft({ name: '', mission: '', prompt: '', category: 'domain_expert' }); setCreating(true) }}>
            创建数字人（可预输入 · 留空=自主创建）
          </button>
          <span className="note">从本体图谱点选种子创建 → 用下方图谱工具栏「从图谱创建数字人」</span>
        </div>

        {creating && (
          <div className="dlg-overlay" onClick={() => setCreating(false)}>
            <div className="dlg" onClick={(e) => e.stopPropagation()}>
              <div className="dlg-head">创建数字人</div>
              <div className="dlg-tip">
                可预输入「人名」与「初始定义」（初始定义将成为该数字人的使命）；
                两栏都留空 → 自主创建（高频词统计 → LLM 提名身份与锚点，完成后在
                「备选数字人」中审批）。「附加指令」可自定义数字人言行风格，仅作引导——
                系统铁律（绝对准确 / 不知即说不知 / 你终审）由代码硬保证，不会被覆盖。
              </div>
              <label className="dlg-field">
                <span>人名</span>
                <input autoFocus placeholder="如：财务制度顾问" value={createDraft.name}
                  onChange={(e) => setCreateDraft({ ...createDraft, name: e.target.value })} />
              </label>
              <label className="dlg-field">
                <span>初始定义（使命）</span>
                <textarea rows={2} placeholder="一句话定义这个数字人负责什么（可留空）" value={createDraft.mission}
                  onChange={(e) => setCreateDraft({ ...createDraft, mission: e.target.value })} />
              </label>
              <label className="dlg-field">
                <span>分类</span>
                <select value={createDraft.category} onChange={(e) => setCreateDraft({ ...createDraft, category: e.target.value })}>
                  {CATEGORY_ORDER.map((c) => <option key={c} value={c}>{CATEGORY_LABEL[c]}</option>)}
                </select>
                <div className="hint">通用数字人 = 平台通用本体（跨部门复用）；执行领域专家 = 部门文档 RAG 归纳（当前默认）。</div>
              </label>
              <label className="dlg-field">
                <span>附加指令（prompt · 可留空 · 最长 4000 字）</span>
                <textarea rows={4} maxLength={4000} placeholder="如：只引用本体术语回答；拿不准先说明不确定性…"
                  value={createDraft.prompt}
                  onChange={(e) => setCreateDraft({ ...createDraft, prompt: e.target.value })} />
              </label>
              <div className="dlg-actions">
                <button className="btn ghost small" onClick={() => setCreating(false)}>取消</button>
                <button className="btn green small" onClick={doCreate}>
                  {!createDraft.name.trim() && !createDraft.mission.trim()
                    ? '自主创建（LLM 提名）' : '创建数字人'}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
