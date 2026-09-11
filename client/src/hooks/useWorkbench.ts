import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  getIdentities, getAssembly, getPersonaOntology, getBenchmark, getAvailableMcp,
  type Identity, type AsmSummary, type PersonaOntItem, type BenchSummary, type PersonaMcp,
} from '../api'
import { useToast } from '../Toast'

/** 数字人工作台的数据层（design §13.5 / test-metrics T-J J-4）。
 *
 *  把原先散落在组件内的 20+ 个 `useState`、派生值与加载副作用收敛到一处，
 *  组件只负责渲染。调用方**解构**后即可像本地变量一样使用 —— 因此渲染代码
 *  无需任何改造（这是刻意选的最小手术方案）。
 *
 *  加载策略：**按需**（T-J J-3）—— 只拉「当前选中」数字人的装配摘要 / 本体段 /
 *  评测摘要，不再对全部已批准数字人并发请求；评测轮询也只针对选中项。
 */
export function useWorkbench(refreshKey: number) {
  const [idents, setIdents] = useState<Identity[]>([])
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState<number | null>(null)      // 正在编辑的锚点 id
  const [draft, setDraft] = useState({ name: '', type: '概念', definition: '' })
  const [adding, setAdding] = useState<number | null>(null)        // 正在加锚点的数字人 id
  const [newAnchor, setNewAnchor] = useState({ name: '', type: '概念', definition: '' })
  const [editingId, setEditingId] = useState<number | null>(null)  // 正在编辑的数字人 id
  const [idDraft, setIdDraft] = useState({
    name: '', mission: '', prompt: '', category: 'domain_expert', reactive: false,
  })
  const [creating, setCreating] = useState(false)
  const [createDraft, setCreateDraft] = useState({
    name: '', mission: '', prompt: '', category: 'domain_expert',
  })
  // 装配（每个数字人一份）
  const [asm, setAsm] = useState<Record<number, AsmSummary>>({})
  const [personaOnt, setPersonaOnt] = useState<Record<number, PersonaOntItem[]>>({})
  const [assemblingId, setAssemblingId] = useState<number | null>(null)
  const [confirmingId, setConfirmingId] = useState<number | null>(null)
  // 折叠开关
  const [ontOpen, setOntOpen] = useState<Record<number, boolean>>({})
  const [asmOpen, setAsmOpen] = useState<Record<number, boolean>>({})
  // 评测（四组对照 + 归因提名 + 版本管理）
  const [bench, setBench] = useState<Record<number, BenchSummary>>({})
  const [bchOpen, setBchOpen] = useState<Record<number, boolean>>({})
  const [bchBusy, setBchBusy] = useState<number | null>(null)
  // MCP 绑定
  const [mcpMap, setMcpMap] = useState<Record<number, PersonaMcp[]>>({})
  const [mcpOpen, setMcpOpen] = useState<Record<number, boolean>>({})
  const [availableMcp, setAvailableMcp] = useState<
    Awaited<ReturnType<typeof getAvailableMcp>>['mcp']>([])
  const [mcpBinding, setMcpBinding] = useState<Record<string, boolean>>({})
  // 工作台外壳
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detailTab, setDetailTab] = useState('overview')
  const [filterMode, setFilterMode] = useState('all')   // all | approved | pending
  const [listQ, setListQ] = useState('')
  const [wizardStep, setWizardStep] = useState(1)

  const { toast } = useToast()

  const approved = useMemo(() => idents.filter((i) => i.status === 'approved'), [idents])
  const alternates = useMemo(() => idents.filter((i) => i.status !== 'approved'), [idents])
  // 已批准锚点总数（KPI 用）
  const approvedAnchors = useMemo(
    () => approved.reduce(
      (n, i) => n + i.anchors.filter((a) => a.status === 'approved').length, 0),
    [approved])

  const reload = useCallback(() => {
    getIdentities().then((r) => setIdents(r.identities ?? [])).catch(() => { })
  }, [])
  useEffect(() => { reload() }, [reload, refreshKey])

  // **按需加载**（design §13.5 / T-J J-3）：只拉「当前选中」数字人的本体段与装配
  // 摘要。旧实现对所有已批准数字人并发拉取（O(N) 请求），而详情区一次只看一个。
  const reloadAsm = useCallback(() => {
    if (selectedId == null) return
    const id = selectedId
    getAssembly(id).then((s) => setAsm((m) => ({ ...m, [id]: s }))).catch(() => { })
    getPersonaOntology(id).then((p) => setPersonaOnt((m) => ({ ...m, [id]: p }))).catch(() => { })
  }, [selectedId])
  useEffect(() => { reloadAsm() }, [reloadAsm, refreshKey])

  // 同上，评测摘要同样按需；且仅当**它的**评测在跑时才轮询
  const reloadBench = useCallback(() => {
    if (selectedId == null) return
    const id = selectedId
    getBenchmark(id).then((s) => setBench((m) => ({ ...m, [id]: s }))).catch(() => { })
  }, [selectedId])
  useEffect(() => { reloadBench() }, [reloadBench, refreshKey])
  const benchRunning = selectedId != null
    && bench[selectedId]?.benchmark?.status === 'running'
  useEffect(() => {
    if (!benchRunning) return
    const t = setTimeout(reloadBench, 5000)
    return () => clearTimeout(t)
  }, [benchRunning, reloadBench, bench])

  // 首次加载可用 MCP 列表（所有已审批 MCP 的工具，用于勾选面板）
  useEffect(() => {
    if (availableMcp.length > 0) return
    getAvailableMcp().then((r) => setAvailableMcp(r.mcp ?? [])).catch(() => { })
  }, [availableMcp.length])

  return {
    idents, setIdents, busy, setBusy,
    editing, setEditing, draft, setDraft, adding, setAdding, newAnchor, setNewAnchor,
    editingId, setEditingId, idDraft, setIdDraft, creating, setCreating,
    createDraft, setCreateDraft,
    asm, setAsm, personaOnt, setPersonaOnt, assemblingId, setAssemblingId,
    confirmingId, setConfirmingId,
    ontOpen, setOntOpen, asmOpen, setAsmOpen,
    bench, setBench, bchOpen, setBchOpen, bchBusy, setBchBusy,
    mcpMap, setMcpMap, mcpOpen, setMcpOpen, availableMcp, setAvailableMcp,
    mcpBinding, setMcpBinding,
    selectedId, setSelectedId, detailTab, setDetailTab,
    filterMode, setFilterMode, listQ, setListQ, wizardStep, setWizardStep,
    toast, approved, alternates, approvedAnchors,
    reload, reloadAsm, reloadBench,
  }
}
