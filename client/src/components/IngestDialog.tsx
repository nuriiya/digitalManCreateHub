import { useEffect, useRef, useState } from 'react'
import { getSettings, triggerIngest, uploadFiles, type UploadResult, type UploadConflict } from '../api'
import { useToast } from '../Toast'

interface Props {
  onClose: () => void
  /** Notify parent when upload completes so it can refresh job/stats. */
  onDone?: (res?: UploadResult) => void
  /** Disable the dialog (a mutex-jammed task is running). */
  disabled?: boolean
}

interface LogRow {
  kind: 'added' | 'skipped' | 'conflict' | 'overwritten' | 'error'
  name: string
  detail?: string
}

/**
 * "Add materials" dialog. Replaces the old one-click "scan & ingest" button.
 *
 * Two equally valid entry points feed the same backend:
 *   A) webkitdirectory  - user picks a folder, browser hands us the file list
 *   B) drag-and-drop    - user drags files onto the drop zone
 *
 * Backend dedupe rule is NAME-first then content_hash:
 *   - new name          -> added
 *   - same name + same hash + doc_summary present -> skipped
 *   - same name + different hash -> conflict (await user decision)
 *
 * The dialog is two-step:
 *   - 1st POST with empty overwriteNames -> we get `added` / `skipped` /
 *     `conflicts` and render the log. Conflicts are NOT ingested yet.
 *   - User picks the names they want to overwrite in the conflict panel
 *   - 2nd POST with the chosen overwriteNames -> those rows go through
 *     in-place (id stable).
 */
export default function IngestDialog({ onClose, onDone, disabled }: Props) {
  const { toast } = useToast()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [dropping, setDropping] = useState(false)
  const [busy, setBusy] = useState(false)
  const [log, setLog] = useState<LogRow[]>([])
  const [pendingConflicts, setPendingConflicts] = useState<UploadConflict[]>([])
  const [selectedOverwrites, setSelectedOverwrites] = useState<Set<string>>(new Set())
  // Current work_dir from settings, shown on the "train work_dir" action.
  const [workDir, setWorkDir] = useState('')
  // Cache the File[] from the most recent pick / drop so the user can confirm
  // overwrites without having to re-pick the folder / re-drag.
  const lastFilesRef = useRef<File[]>([])

  useEffect(() => {
    getSettings().then((r) => setWorkDir(r.settings?.work_dir || '')).catch(() => {})
  }, [])

  const submit = async (files: File[], overwriteNames: string[]) => {
    if (disabled) return
    if (!files.length) return
    lastFilesRef.current = files
    setBusy(true)
    try {
      const res = await uploadFiles(files, overwriteNames)
      const newLog: LogRow[] = []
      const ow = new Set(overwriteNames)
      for (const n of res.added) newLog.push({
        kind: ow.has(n) ? 'overwritten' : 'added',
        name: n,
        detail: ow.has(n) ? '同名覆盖 · 保留 id' : '新增',
      })
      for (const n of res.skipped) newLog.push({
        kind: 'skipped', name: n, detail: '内容一致',
      })
      for (const c of res.conflicts) newLog.push({
        kind: 'conflict', name: c.name, detail: '已提取过 · 等待确认',
      })
      for (const e of res.errors) newLog.push({
        kind: 'error', name: e.name, detail: e.error,
      })
      setLog((prev) => [...prev, ...newLog])
      if (overwriteNames.length) {
        // user just committed their overwrite decisions -> done with conflicts
        setPendingConflicts([])
        setSelectedOverwrites(new Set())
        toast(`已覆盖 ${overwriteNames.length} 个文件`, 'ok')
      } else {
        setPendingConflicts(res.conflicts)
        if (res.added.length) toast(`新增 ${res.added.length} 个文件`, 'ok')
        if (res.conflicts.length) {
          toast(`发现 ${res.conflicts.length} 个同名冲突，请确认是否覆盖`, 'err')
        }
      }
      onDone?.(res)
    } catch (e: any) {
      toast(e.message || '上传失败', 'err')
    } finally {
      setBusy(false)
    }
  }

  // ---- option C: scan & ingest the configured work_dir on the server ----
  const runWorkDirScan = async () => {
    if (busy || disabled || !workDir) return
    setBusy(true)
    try {
      const r = await triggerIngest()
      setLog((prev) => [...prev, {
        kind: 'added',
        name: `工作目录：${workDir}`,
        detail: `训练任务 #${r.job_id} 已启动 · 后台扫描入库`,
      }])
      toast(`训练任务 #${r.job_id} 已启动，正在扫描工作目录`, 'ok')
      onDone?.()   // refresh jobs/stats so the new background job shows up
    } catch (e: any) {
      toast(e.message || '启动失败', 'err')
    } finally {
      setBusy(false)
    }
  }

  // ---- option A: webkitdirectory file picker ----
  const onFolderChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    e.target.value = '' // reset so picking the same dir again still fires
    void submit(files, [])
  }

  // ---- option B: drag-and-drop ----
  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (!dropping) setDropping(true)
  }
  const onDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (e.currentTarget === e.target) setDropping(false)
  }
  const collectFiles = (e: React.DragEvent): File[] => {
    const out: File[] = []
    if (e.dataTransfer.items?.length) {
      for (const it of Array.from(e.dataTransfer.items)) {
        const f = it.getAsFile()
        if (f) out.push(f)
      }
    } else {
      out.push(...Array.from(e.dataTransfer.files))
    }
    return out
  }
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDropping(false)
    void submit(collectFiles(e), [])
  }

  const toggleConflict = (name: string) => {
    setSelectedOverwrites((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name); else next.add(name)
      return next
    })
  }
  const confirmOverwrites = () => {
    void submit(lastFilesRef.current, Array.from(selectedOverwrites))
  }
  const skipAllConflicts = () => {
    setPendingConflicts([])
    setSelectedOverwrites(new Set())
  }

  return (
    <div className="dlg-overlay" onMouseDown={(e) => {
      if (e.target === e.currentTarget && !busy) onClose()
    }}>
      <div className="dlg">
        <div className="dlg-head">
          <h3>添加资料</h3>
          <button className="btn ghost small" onClick={onClose} disabled={busy}>关闭</button>
        </div>
        <div className="dlg-tip">
          支持 md / pdf / docx / xlsx / xls / csv · 增量入库 · 按 content_hash 自动判重 · 同名文件需确认后才会覆盖
        </div>

        <div className="dlg-grid">
          <div className={`dlg-opt${dropping ? ' drop' : ''}`}
               onClick={() => fileInputRef.current?.click()}>
            <div className="dlg-opt-lbl">选项 A · 选择目录</div>
            <div className="dlg-opt-hint">点击选择文件夹（递归遍历）<br/>浏览器拿到目录里所有文件后批量入库</div>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              // non-standard webkitdirectory (Chromium / Firefox / Safari support it)
              {...({ webkitdirectory: '', directory: '' } as any)}
              style={{ display: 'none' }}
              onChange={onFolderChange}
            />
          </div>
          <div className={`dlg-opt dropzone${dropping ? ' drop' : ''}`}
               onDragOver={onDragOver}
               onDragLeave={onDragLeave}
               onDrop={onDrop}>
            <div className="dlg-opt-lbl">选项 B · 拖拽上传</div>
            <div className="dlg-opt-hint">把文件拖到这里<br/>可同时拖入多个</div>
          </div>
        </div>

        <div className={`dlg-scan${busy || disabled ? ' busy' : ''}`}
             title={!workDir ? '请先在「设置」页配置工作目录' : (busy || disabled ? '有任务运行中' : '')}>
          <div>
            <div className="dlg-opt-lbl">选项 C · 训练当前工作目录</div>
            <div className="dlg-opt-hint" style={{ wordBreak: 'break-all' }}>
              {workDir
                ? <>{workDir}<br/>扫描该目录全部文档 → 增量入库（同名文件自动更新）</>
                : '尚未配置工作目录，请先到「设置」页填写'}
            </div>
          </div>
          <button className="btn primary small"
                  onClick={runWorkDirScan}
                  disabled={busy || disabled || !workDir}>
            {busy ? '启动中…' : '开始训练'}
          </button>
        </div>

        {pendingConflicts.length > 0 && (
          <div className="dlg-conflict">
            <div className="dlg-conflict-hd">
              ⚠ 以下 {pendingConflicts.length} 个文件已提取过，是否覆盖？
            </div>
            <div className="dlg-conflict-list">
              {pendingConflicts.map((c) => (
                <label key={c.name} className="dlg-conflict-row">
                  <input type="checkbox"
                         checked={selectedOverwrites.has(c.name)}
                         onChange={() => toggleConflict(c.name)} />
                  <span>{c.name}</span>
                  <span className="note">doc_id #{c.doc_id}</span>
                </label>
              ))}
            </div>
            <div className="btnrow" style={{ marginTop: 8 }}>
              <button className="btn ghost small" onClick={skipAllConflicts}>全部跳过</button>
              <button className="btn small primary"
                      disabled={!selectedOverwrites.size || busy}
                      onClick={confirmOverwrites}>
                覆盖 {selectedOverwrites.size} 个文件
              </button>
            </div>
          </div>
        )}

        {log.length > 0 && (
          <div className="dlg-log">
            {log.map((row, i) => (
              <div key={i} className={`dlg-log-row dlg-log-${row.kind}`}>
                <span>
                  {row.kind === 'added' && '+ '}
                  {row.kind === 'skipped' && '= '}
                  {row.kind === 'conflict' && '! '}
                  {row.kind === 'overwritten' && '↻ '}
                  {row.kind === 'error' && '✗ '}
                  {row.name}
                </span>
                <span className="note">{row.detail}</span>
              </div>
            ))}
          </div>
        )}

        <div className="dlg-foot">
          <span className="note">
            {busy ? '处理中…' : (log.length
              ? `已完成 ${log.length} 个文件`
              : '等待选择目录或拖拽文件')}
          </span>
          <button className="btn ghost small" onClick={onClose} disabled={busy}>关闭</button>
        </div>
      </div>
    </div>
  )
}