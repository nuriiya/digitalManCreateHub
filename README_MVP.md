# rag-mvp: RAG + 本体构建控制台（MVP v1）

对应设计文档 `DESIGN_MVP.md`（v1.0 + DM1/DM7）。**代码已进入 git 管理，每次改动先测试后提交。**

## 一键启动

```powershell
powershell -ExecutionPolicy Bypass -File start.ps1   # 启动（http://localhost:8000）
powershell -ExecutionPolicy Bypass -File stop.ps1    # 停止
```

start.ps1 自动完成：
1. Python venv + 依赖安装（backend/.venv）
2. 前端按源码新旧自动构建（client/dist）
3. WSL2 自检 → 识别用户（UID≥1000）→ sudo 密码询问一次并以 **DPAPI 加密缓存**到 `data/wsl_cred` → 幂等安装 Ollama → 拉取 bge-m3（首次较慢）
4. 启动 uvicorn :8000（托管前端）

**优雅降级**：WSL2/Ollama 不可用不阻塞启动，embedding 自动降级 hash 模式（链路不断，可在设置页随时切回）。密码缓存删除 `data/wsl_cred` 即重置。

## 首次使用

1. **设置页**：填 V4-Flash 的 base_url / token / 模型名（测试阶段可填外网 OpenAI 兼容端点）→「保存并测试连通」；选工作目录（Windows 本机路径）。
2. **入库页**：①扫描并入库（md/txt/pdf → 分段 → Flash 摘要+标签 → 嵌入 → SQLite）②本体提取（EDC 式提名 → 三关校验 → 待审批）。
3. **RAG 预览页**：统计卡 / chunk 明细（原文·摘要·标签）/ 近邻检索测试。
4. **本体图谱页**：候选（虚线橙）与已批准（实线蓝）一屏可见；点节点右侧弹审批卡——**证据 span 逐字高亮原文**，批准/拒绝/暂缓/合并。

## 开发与测试

```powershell
# 后端 UT（32 个用例，零网络零外部依赖）
cd backend
python -m pytest tests -q

# 前端
cd client
npm install
npm run build     # 产物 client/dist 由后端托管
npm run dev       # 开发模式 :5173（代理 /api 与 /ws 到 :8000）
```

## 代码结构

```
backend/app/
  main.py          # FastAPI: REST + WS 事件流 + 静态托管
  settings_store.py# 设置持久化（token 落 data/settings.json，API 出口脱敏）
  db.py            # SQLite（任务态+数据态，DM7；Store 接口留 PG 换装）
  jobs.py          # job 运行器 + 事件(seq) + 进度原子提交 + 断电恢复
  loaders.py       # md/txt/pdf 加载（扫描件标记跳过）
  chunking.py      # 句子边界分段 + 无标点硬切 + 位置回映射
  llm.py           # V4-Flash（OpenAI 兼容）+ 规则降级；UT 可注入 fake
  embedding.py     # Ollama bge-m3 + hash 降级；模型锁定铁则
  ingest.py        # RAG 入库管线（内容 hash 去重、文件级容错）
  ontology.py      # EDC-lite 提名 + 结构/证据/去重三关 + 审批 + 图谱
client/src/
  App.tsx useEvents.ts  # WS 断线指数退避重连 + 按 seq 增量补齐
  pages/SettingsPage.tsx  RagPage.tsx  OntologyPage.tsx（自绘 SVG 图谱）
```

## 三条铁则（代码级实现）

1. **embedding 全程唯一**：首次入库锁定 (provider, model)，切换拒绝（`embedding.assert_model_lock`）。
2. **LLM 只提名**：本体候选必须过结构关（闭集枚举/长度）、证据关（mention 必须逐字命中 chunk 原文，span 落库）、去重关；LLM 幻觉证据当场丢弃。
3. **用户终审**：候选一律 `pending` 入库，图谱页审批后才 `approved`。

## 已知边界（MVP 范围外）

- GLM 5.2 归纳核验、分时路由、定时任务、PG/pgvector 正式存储（见 DESIGN.md 完整版）。
- PDF 仅文本层（扫描件 OCR 跳过并提示）。
