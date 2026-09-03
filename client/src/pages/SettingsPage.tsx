import { useEffect, useState } from 'react'
import {
  getSettings, saveSettings, testLlm, testLlm2, testEmbedding, getStats, getCandidate,
} from '../api'
import { useToast } from '../Toast'

export default function SettingsPage({ onChanged }: { onChanged?: () => void }) {
  const [llm, setLlm] = useState({ base_url: '', api_key: '', model: '', timeout: 90, hard_timeout: 1800 })
  const [llm2, setLlm2] = useState({ base_url: 'https://open.bigmodel.cn/api/paas/v4', api_key: '', model: 'glm-5.2', timeout: 90, hard_timeout: 1800 })
  const [emb, setEmb] = useState({ provider: 'hash', base_url: 'http://localhost:11434', model: 'bge-m3' })
  const [workDir, setWorkDir] = useState('')
  const [llmTest, setLlmTest] = useState<null | { ok: boolean; reply?: string; error?: string }>(null)
  const [llm2Test, setLlm2Test] = useState<null | { ok: boolean; reply?: string; error?: string }>(null)
  const [embTest, setEmbTest] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const { toast } = useToast()

  useEffect(() => {
    getSettings().then((r) => {
      const s = r.settings
      setLlm(s.llm); setLlm2(s.llm2 || llm2); setEmb(s.embedding); setWorkDir(s.work_dir || '')
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const persist = async () => {
    setBusy(true)
    try {
      await saveSettings({ work_dir: workDir, llm, llm2, embedding: emb })
      toast('设置已保存', 'ok')
      onChanged?.()
    } catch (e: any) { toast(e.message, 'err') }
    setBusy(false)
  }

  const doTestLlm = async () => {
    await persist()
    setLlmTest(null)
    try { setLlmTest(await testLlm()) } catch (e: any) { setLlmTest({ ok: false, error: e.message }) }
  }

  const doTestLlm2 = async () => {
    await persist()
    setLlm2Test(null)
    try { setLlm2Test(await testLlm2()) } catch (e: any) { setLlm2Test({ ok: false, error: e.message }) }
  }

  const doTestEmb = async () => {
    setEmbTest(null)
    try { setEmbTest(await testEmbedding()) } catch (e: any) { setEmbTest({ ok: false, error: e.message }) }
  }

  return (
    <div className="grid cols2">
      <div className="card">
        <h3>大语言模型 · V4-Flash</h3>
        <div className="desc">OpenAI 兼容端点。测试阶段可填外网/第三方地址；模型路由 = 提名器，无终审权。</div>
        <label className="field"><span>Base URL（如 https://api.xxx.com/v1）</span>
          <input value={llm.base_url} placeholder="https://..."
            onChange={(e) => setLlm({ ...llm, base_url: e.target.value })} />
        </label>
        <label className="field"><span>API Token</span>
          <input type="password" value={llm.api_key} placeholder="sk-..."
            onChange={(e) => setLlm({ ...llm, api_key: e.target.value })} />
        </label>
        <label className="field"><span>模型名</span>
          <input value={llm.model} placeholder="deepseek-v4-flash"
            onChange={(e) => setLlm({ ...llm, model: e.target.value })} />
        </label>
        <label className="field"><span>空闲超时（秒，默认 90）</span>
          <input type="number" min={10} max={600} value={llm.timeout}
            onChange={(e) => setLlm({ ...llm, timeout: Number(e.target.value) || 90 })} />
          <div className="hint">流式调用：只要模型还在持续输出 token 就不会中断；连续这么久没有任何新输出才判定断连并自动暂停。</div>
        </label>
        <label className="field"><span>总时长上限（秒，默认 1800）</span>
          <input type="number" min={60} max={7200} value={llm.hard_timeout}
            onChange={(e) => setLlm({ ...llm, hard_timeout: Number(e.target.value) || 1800 })} />
          <div className="hint">最后防线：单次调用总时长超限（如思考死循环）强制暂停。正常情况远不会触发。</div>
        </label>
        <div className="btnrow">
          <button className="btn" onClick={persist} disabled={busy}>保存</button>
          <button className="btn ghost" onClick={doTestLlm} disabled={busy}>保存并测试连通</button>
          {llmTest && (
            <span className={llmTest.ok ? 'note' : 'warn'}>
              {llmTest.ok ? `✓ 连通（回复：${llmTest.reply}）` : `✗ ${llmTest.error}`}
            </span>
          )}
        </div>
      </div>

      <div>
        <div className="card">
          <h3>判别模型 · GLM 5.2（异源交叉核验）</h3>
          <div className="desc">考核的 LLM-2 判别器：开卷比对答案与原文。必须与生成模型（V4-Flash）<b>异源</b>，同源自判会系统性自圆其说。判别结果仍只是提名，确定性代码终审。</div>
          <label className="field"><span>Base URL</span>
            <input value={llm2.base_url} placeholder="https://open.bigmodel.cn/api/paas/v4"
              onChange={(e) => setLlm2({ ...llm2, base_url: e.target.value })} />
          </label>
          <label className="field"><span>API Token</span>
            <input type="password" value={llm2.api_key} placeholder="...id.secret"
              onChange={(e) => setLlm2({ ...llm2, api_key: e.target.value })} />
          </label>
          <label className="field"><span>模型名</span>
            <input value={llm2.model} placeholder="glm-5.2"
              onChange={(e) => setLlm2({ ...llm2, model: e.target.value })} />
          </label>
          <div className="btnrow">
            <button className="btn" onClick={persist} disabled={busy}>保存</button>
            <button className="btn ghost" onClick={doTestLlm2} disabled={busy}>保存并测试连通</button>
            {llm2Test && (
              <span className={llm2Test.ok ? 'note' : 'warn'}>
                {llm2Test.ok ? `✓ 连通（回复：${llm2Test.reply}）` : `✗ ${llm2Test.error}`}
              </span>
            )}
          </div>
        </div>

        <div className="card">
          <h3>Embedding · WSL2 Ollama</h3>
          <div className="desc">选定即锁定（向量可比性铁则）；未就绪时自动降级 hash 模式，链路不断。</div>
          <div className="grid cols2">
            <label className="field"><span>提供方</span>
              <select value={emb.provider} onChange={(e) => setEmb({ ...emb, provider: e.target.value })}>
                <option value="ollama">Ollama（WSL2，推荐）</option>
                <option value="hash">本地 hash（零依赖降级）</option>
              </select>
            </label>
            <label className="field"><span>模型</span>
              <input value={emb.model} onChange={(e) => setEmb({ ...emb, model: e.target.value })} />
            </label>
          </div>
          <label className="field"><span>Ollama 地址（WSL2 端口转发到 localhost）</span>
            <input value={emb.base_url} onChange={(e) => setEmb({ ...emb, base_url: e.target.value })} />
          </label>
          <div className="btnrow">
            <button className="btn ghost" onClick={doTestEmb}>检测 Ollama</button>
            {embTest && (
              <span className={embTest.ok ? 'note' : 'warn'}>
                {embTest.ok
                  ? embTest.provider === 'hash' ? '✓ hash 降级模式' : `✓ Ollama 可达，已装 ${embTest.models?.length ?? 0} 个模型${embTest.has_model ? '' : '（缺 ' + emb.model + '，需拉取）'}`
                  : `✗ ${embTest.error}`}
              </span>
            )}
          </div>
        </div>

        <div className="card">
          <h3>工作目录</h3>
          <div className="desc">Windows 本机路径，后端直接读取（前后端同机）。支持 .md / .txt / .pdf。</div>
          <label className="field"><span>绝对路径</span>
            <input value={workDir} placeholder="D:\docs\department"
              onChange={(e) => setWorkDir(e.target.value)} />
          </label>
          <div className="btnrow">
            <button className="btn" onClick={persist} disabled={busy}>保存</button>
          </div>
        </div>
      </div>
    </div>
  )
}
