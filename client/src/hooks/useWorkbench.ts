import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  getIdentities, getAssembly, getPersonaOntology, getBenchmark, getAvailableMcp,
  getPersonaTemplates, ingestIntoPersona, getJobs, getJobEvents,
  type Identity, type AsmSummary, type PersonaOntItem, type BenchSummary, type PersonaMcp,
  type PersonaTemplate, type TemplateRender, type PersonaUploadResult, type EventItem,
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
  // 从模板创建（design §16）：模板列表 + 当前选择 + 槽位值 + 实时预览
  const [templates, setTemplates] = useState<PersonaTemplate[]>([])
  const [tplMode, setTplMode] = useState(false)
  const [tplId, setTplId] = useState<number | null>(null)
  const [tplValues, setTplValues] = useState<Record<string, string>>({})
  const [tplPreview, setTplPreview] = useState<TemplateRender | null>(null)
  // 反向沉淀：把现有数字人存为模板（design §16.5）
  const [saveTplFor, setSaveTplFor] = useState<Identity | null>(null)
  const [saveTplDraft, setSaveTplDraft] = useState({
    code: '', label: '', description: '', findWord: '', slotKey: 'domain',
    slotLabel: '应用领域',
  })
  // 模板管理面板（编辑 / 停用 / 删除 / 新建）
  const [tplAdminMode, setTplAdminMode] = useState(false)
  const [tplEditId, setTplEditId] = useState<number | null>(null)
  const [tplEditDraft, setTplEditDraft] = useState({
    label: '', description: '', category: 'domain_expert', status: 'active',
    blueprintText: '',
  })
  const [tplNewOpen, setTplNewOpen] = useState(false)
  const [tplNewDraft, setTplNewDraft] = useState({
    code: '', label: '', description: '', category: 'domain_expert',
  })
  // 数字人卡片上传（design §13.7）：上传 → 录入 RAG → 范围化提取 → 装配（待确认）
  const [ingOpen, setIngOpen] = useState<Record<number, boolean>>({})
  const [ingBusy, setIngBusy] = useState<number | null>(null)
  const [ingState, setIngState] = useState<Record<number, PersonaUploadResult>>({})
  const [ingStep, setIngStep] = useState<Record<number, {
    label: string; status: string
  }>>({})
  const pollRef = useRef<number | null>(null)

  const { toast } = useToast()

  const reloadTemplates = useCallback(() => {
    getPersonaTemplates(false).then((r) => setTemplates(r.templates ?? []))
      .catch(() => { })
  }, [])

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

  // 数字人模板（design §16）：进「新建数字人」前预取一次
  useEffect(() => {
    getPersonaTemplates(true).then((r) => setTemplates(r.templates ?? [])).catch(() => { })
  }, [refreshKey])

  // ---- 数字人卡片上传（design §13.7）----
  //
  // 一条链：上传 → 录入 RAG → 范围化本体提取 → 装配（待确认）。
  // 后端 `ingest-and-assemble` 把后两步串成**父 job**跑在后台，接口立刻返回
  // 父 job_id；这里轮询该 job，并把它的子 job 进度翻成「第 n 步」文案。
  //
  // 刻意**不**自动采纳：链跑完只产出「待确认」清单，装配仍要用户点
  // 「一键终审装配」（用户是唯一终审点）。所以这里轮询完只做两件事：
  // ① 刷新装配摘要（让待确认数字出现在卡片上）② 刷新本体段。
  const stopPoll = useCallback(() => {
    if (pollRef.current != null) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])
  useEffect(() => stopPoll, [stopPoll])   // 卸载时清掉定时器

  const pollIngest = useCallback((identityId: number, jobId: number) => {
    stopPoll()
    let ticks = 0
    pollRef.current = window.setInterval(async () => {
      ticks += 1
      if (ticks > 600) { stopPoll(); return }        // 兜底：20 分钟
      try {
        const [jr, ev] = await Promise.all([
          getJobs(), getJobEvents(jobId),
        ]) as [any, { events?: EventItem[] }]
        const job = (jr.jobs ?? []).find((j: any) => j.id === jobId)
        // 事件里的 pipeline.step 是**最后一条为准**（step1 running → done → step2 …）
        const steps = (ev.events ?? []).filter((e) => e.type === 'pipeline.step')
        const last = steps[steps.length - 1]
        if (last) {
          const p = last.payload ?? {}
          setIngStep((m) => ({
            ...m,
            [identityId]: {
              label: `第 ${p.step ?? '?'} 步 · ${p.name ?? ''}`,
              status: p.status ?? '',
            },
          }))
        }
        if (!job) return
        if (job.status === 'done') {
          stopPoll()
          setIngStep((m) => ({ ...m, [identityId]: { label: '已完成', status: 'done' } }))
          setIngBusy(null)
          reloadAsm()
          toast('已录入并提取本体，「待确认」清单请到「装配本体」终审', 'ok')
        } else if (job.status === 'failed' || job.status === 'paused') {
          stopPoll()
          setIngStep((m) => ({
            ...m,
            [identityId]: {
              label: job.status === 'failed' ? `失败：${job.error ?? '未知'}` : '已暂停',
              status: job.status,
            },
          }))
          setIngBusy(null)
          toast(job.status === 'failed' ? `融入失败：${job.error ?? ''}` : '任务已暂停', 'err')
        }
      } catch { /* 下一轮再试 */ }
    }, 2000)
  }, [reloadAsm, stopPoll, toast])

  const doIngestForPersona = useCallback(async (identityId: number, files: File[]) => {
    if (!files.length) return
    setIngBusy(identityId)
    setIngStep((m) => ({ ...m, [identityId]: { label: '上传中…', status: 'running' } }))
    try {
      const r = await ingestIntoPersona(identityId, files)
      setIngState((m) => ({ ...m, [identityId]: r }))
      if (r.conflicts?.length) {
        // 有名称冲突：文件已落盘但没进库，等用户决定是否覆盖。
        toast(`有 ${r.conflicts.length} 个同名文件内容不同，请先处理冲突`, 'err')
        setIngStep((m) => ({
          ...m, [identityId]: { label: '待处理同名冲突', status: 'conflict' },
        }))
        setIngBusy(null)
        return
      }
      if (!r.job_id) {
        setIngStep((m) => ({
          ...m, [identityId]: { label: r.note ?? '没有新增文档', status: 'done' },
        }))
        setIngBusy(null)
        return
      }
      setIngStep((m) => ({
        ...m, [identityId]: { label: '已录入，正在提取本体…', status: 'running' },
      }))
      pollIngest(identityId, r.job_id)
    } catch (e: any) {
      toast(e.message ?? '上传失败', 'err')
      setIngStep((m) => ({ ...m, [identityId]: { label: e.message ?? '失败', status: 'failed' } }))
      setIngBusy(null)
    }
  }, [pollIngest, toast])

  const toggleIng = (id: number) => setIngOpen((m) => ({ ...m, [id]: !m[id] }))

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
    templates, tplMode, setTplMode, tplId, setTplId, tplValues, setTplValues,
    tplPreview, setTplPreview,
    saveTplFor, setSaveTplFor, saveTplDraft, setSaveTplDraft,
    tplAdminMode, setTplAdminMode, tplEditId, setTplEditId,
    tplEditDraft, setTplEditDraft, tplNewOpen, setTplNewOpen,
    tplNewDraft, setTplNewDraft, reloadTemplates,
    ingOpen, ingBusy, ingState, ingStep, doIngestForPersona, toggleIng,
    toast, approved, alternates, approvedAnchors,
    reload, reloadAsm, reloadBench,
  }
}
