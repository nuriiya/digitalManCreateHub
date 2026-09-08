import { useEffect, useState } from 'react'
import { getMcpServers, createMcpServer, deleteMcpServer, startMcpServer, stopMcpServer, scanMcpServers, approveMcpServer, McpServer } from '../api'
import { useToast } from '../Toast'

const EMPTY = { name: '', description: '', transport: 'http', image: '', command: '', port: 0 }

const APPROVAL_LABEL: Record<string, string> = {
  approved: '已批准', pending: '待审批', rejected: '已拒绝',
}

export default function McpPage() {
  const [servers, setServers] = useState<McpServer[]>([])
  const [form, setForm] = useState({ ...EMPTY })
  const [busy, setBusy] = useState(false)
  const [scanning, setScanning] = useState(false)
  const { toast } = useToast()

  const reload = () => getMcpServers().then((r) => setServers(r.servers)).catch(() => {})

  useEffect(() => { reload() }, [])

  const doCreate = async () => {
    if (!form.name.trim() || !form.image.trim()) { toast('名称与镜像必填', 'err'); return }
    setBusy(true)
    try {
      await createMcpServer({ ...form, port: Number(form.port) || 0 })
      toast('已注册 MCP 服务', 'ok')
      setForm({ ...EMPTY })
      reload()
    } catch (e: any) { toast(e.message, 'err') }
    setBusy(false)
  }

  const doScan = async () => {
    setScanning(true)
    try {
      const r = await scanMcpServers()
      const created = r.results.filter((x) => x.ok && x.msg === 'created').length
      const updated = r.results.filter((x) => x.ok && x.msg === 'updated').length
      const failed = r.results.filter((x) => !x.ok).length
      toast(`扫描 ${r.scanned} 个 JSON：导入 ${created} 新增 / ${updated} 更新${failed ? `，${failed} 失败` : ''}`, failed ? 'err' : 'ok')
      reload()
    } catch (e: any) { toast(e.message, 'err') }
    setScanning(false)
  }

  const doApprove = async (s: McpServer, approve: boolean) => {
    try { await approveMcpServer(s.id, approve); toast(approve ? '已批准' : '已拒绝', 'ok'); reload() }
    catch (e: any) { toast(e.message, 'err') }
  }

  const doStart = async (id: number) => {
    try { await startMcpServer(id); toast('已启动', 'ok'); reload() }
    catch (e: any) { toast(e.message, 'err') }
  }
  const doStop = async (id: number) => {
    try { await stopMcpServer(id); toast('已停止', 'ok'); reload() }
    catch (e: any) { toast(e.message, 'err') }
  }
  const doDelete = async (s: McpServer) => {
    if (!confirm(`删除 MCP「${s.name}」？（容器会先停止并移除）`)) return
    try { await deleteMcpServer(s.id); toast('已删除', 'ok'); reload() }
    catch (e: any) { toast(e.message, 'err') }
  }

  return (
    <div className="page-col">
      <div className="card">
        <h3>注册 MCP 服务</h3>
        <div className="desc">每个 MCP 以 Docker 容器隔离运行（沙盒）。image 为 Docker 镜像；http 传输需填端口，stdio 传输可留空端口、用 command 指定进程。也可让数字人输出 JSON 到 <code>mcp_imports/</code> 目录自动导入。</div>
        <label className="field"><span>名称</span>
          <input value={form.name} placeholder="如 filesystem-reader" onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </label>
        <label className="field"><span>描述</span>
          <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
        </label>
        <div className="field-row">
          <label className="field"><span>传输方式</span>
            <select value={form.transport} onChange={(e) => setForm({ ...form, transport: e.target.value })}>
              <option value="http">http</option>
              <option value="stdio">stdio</option>
            </select>
          </label>
          <label className="field"><span>端口（http）</span>
            <input type="number" value={form.port || ''} placeholder="0"
              onChange={(e) => setForm({ ...form, port: Number(e.target.value) })} />
          </label>
        </div>
        <label className="field"><span>Docker 镜像</span>
          <input value={form.image} placeholder="如 alpine:3.20 / my-mcp:latest" onChange={(e) => setForm({ ...form, image: e.target.value })} />
        </label>
        <label className="field"><span>命令（可选）</span>
          <input value={form.command} placeholder="如 python -m my_mcp" onChange={(e) => setForm({ ...form, command: e.target.value })} />
        </label>
        <div className="btnrow">
          <button className="btn green" onClick={doCreate} disabled={busy}>注册</button>
        </div>
      </div>

      <div className="card">
        <div className="btnrow" style={{ justifyContent: 'space-between' }}>
          <h3>已注册 MCP（{servers.length}）</h3>
          <button className="btn small" onClick={doScan} disabled={scanning}>{scanning ? '扫描中…' : '扫描 mcp_imports 导入'}</button>
        </div>
        {servers.length === 0 && <div className="note">尚未注册任何 MCP 服务。</div>}
        {servers.map((s) => {
          const ap = s.approval_status || 'approved'
          const toolCount = (s.tools || []).length
          const calledBy = s.called_by || []
          return (
            <div key={s.id} className="mcp-row">
              <div className="mcp-info">
                <b>{s.name}</b>
                <span className={`status-pill ${s.status}`}>{s.status}</span>
                <span className={`status-pill ${ap}`}>{APPROVAL_LABEL[ap] || ap}</span>
                <span className="note">{s.transport} · {s.image}{s.port ? ` :${s.port}` : ''}{toolCount ? ` · ${toolCount} 工具` : ''}</span>
                {s.description && <div className="note">{s.description}</div>}
                {s.source_path && <div className="note">来源：{s.source_path}</div>}
                {toolCount > 0 && (
                  <div className="mcp-tools">
                    <div className="mcp-section-title">工具清单（LLM 调用输入 schema）</div>
                    {s.tools!.map((t, i) => (
                      <div key={i} className="mcp-tool">
                        <div><b>{t.name}</b>{t.description && <span className="note"> — {t.description}</span>}</div>
                        {t.input_schema && (
                          <pre className="mcp-schema">{JSON.stringify(t.input_schema, null, 2)}</pre>
                        )}
                      </div>
                    ))}
                  </div>
                )}
                {calledBy.length > 0 && (
                  <div className="mcp-tools">
                    <div className="mcp-section-title">被以下数字人调用（{calledBy.length}）</div>
                    {calledBy.map((b) => (
                      <div key={b.id} className="mcp-tool">
                        <b>{b.persona_name}</b>
                        <span className="note"> · 工具 <code>{b.mcp_tool_name}</code> · {b.status}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div className="btnrow">
                {ap === 'pending' && (
                  <>
                    <button className="btn green small" onClick={() => doApprove(s, true)}>批准</button>
                    <button className="btn ghost small" onClick={() => doApprove(s, false)}>拒绝</button>
                  </>
                )}
                {ap === 'approved' && (s.status === 'running'
                  ? <button className="btn ghost small" onClick={() => doStop(s.id)}>停止</button>
                  : <button className="btn small" onClick={() => doStart(s.id)}>启动</button>)}
                <button className="btn ghost small" onClick={() => doDelete(s)}>删除</button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
