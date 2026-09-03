# 数字人创建工具 · 产品化设计文档

> 版本：v1.1（v1 基础上新增 R9 分时用模：白天本地 / 夜间公司大模型）
> 日期：2026-08-29
> 范围：把 `rag_prototype`（RAG 提取管线雏形）产品化为一个可安装、可长期运行的桌面级工具。
> 关联：`pipeline.md` 第 9 章（RAG 定位 + 本体分解器方案 9.2，待确认）。
> 本文档只做设计，不写实现代码。

---

## 0. 文档目的

用户对工具的产品化要求（原始输入拆解）：

| # | 要求 | 归属 |
|---|---|---|
| R1 | 前端 React：用户可指定**工作目录** | 前端 + 后端设置 |
| R2 | 前端：可指定**使用的模型** | 模型层 + 安装向导 |
| R3 | 前端：可设定**定时任务** | 后端调度器 |
| R4 | 前端：显示**当前工作进度**及**实时 RAG / 本体构建状况** | 前后端实时通道 |
| R5 | 后端 Python：能读 **PDF** 和 **Markdown** | 文档加载器 |
| R6 | 模型 = **公司内部模型** 或 **本地部署模型**，安装时可选 | 安装向导 + 模型层 |
| R7 | 本地部署时按本地环境（CPU/GPU）**自动选模型**，部署逻辑**自动化、开箱即用** | 模型管理器 |
| R8 | 支持**断电重连**（服务重启续跑 + 前端重连补齐） | 恢复器 + 通信协议 |
| R9 | **分时用模**：公司大模型白天繁忙不可用、夜间空闲——白天用本地模型，夜间用公司大模型 | 模型管理器分时路由（§6.5） |

## 1. 需求歧义点与本文决策（评审重点）

动手前先暴露歧义，本文采用如下决策，**评审时可推翻**：

| 编号 | 歧义 | 本文决策 |
|---|---|---|
| D1 | "本地部署一个模型"的范围——公司模型（DeepSeek V4 Flash 284B / GLM 5.2 744B）本地跑不动，本地部署什么？ | **分层选择**：embedding 模型小、CPU 可跑，本地模式必装；LLM 按硬件条件决定是否给本地小模型（显存不足时引导公司模式），并明确标注本地小 LLM 的**提名质量风险**（见 6.2） |
| D2 | "定时任务"定什么时？ | 定义为**通用调度器**：任意管线（扫描目录增量 ingest / 本体归纳 / 自检）都可绑定 cron 或 interval，不只扫目录 |
| D3 | "断电重连"的语义 | 拆两层：**后端** = 进程重启后任务从 checkpoint 续跑（断电、崩溃通用）；**前端** = WS 断线重连后按事件序号增量补齐进度。不含网络远程访问（本工具定位本机 localhost 运行） |
| D4 | 模型模式是否只有"公司/本地"二选一？ | 允许**混合模式**：embedding 本地（快、免费）+ LLM 走公司（质量高）。这是低配机器的推荐组合 |
| D5 | 前端如何"指定工作目录"（浏览器拿不到本地路径） | Chromium 用 File System Access API 选目录；**兜底始终提供手动输入绝对路径**（前后端同机运行，路径直接可用） |
| D6 | "白天本地 / 夜间公司"的切换粒度——按请求切？按任务切？ | **时间窗 + 任务领取时生效**：任务启动时按当前时间窗领取对应客户端，运行中不中断（跑完再切）；LLM 密集任务建议由定时任务排入夜间窗口。长任务跨窗在 chunk 边界换模型列为后续增强（见 §11.7，技术上可行） |

---

## 2. 总体架构

<svg viewBox="0 0 680 614" width="100%" role="img" xmlns="http://www.w3.org/2000/svg">
<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>
<text x="340" y="30" text-anchor="middle" font-size="14" font-weight="500" fill="#2C2C2A">总体架构：React 前端 + FastAPI 后端 + 双模式模型层</text>
<rect x="40" y="56" width="600" height="104" rx="12" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
<text x="54" y="74" font-size="12" fill="#0C447C">前端 React · Vite + TypeScript + Zustand</text>
<g><rect x="56" y="88" width="136" height="62" rx="8" fill="#FFFFFF" stroke="#85B7EB" stroke-width="0.5"/><text x="124" y="110" text-anchor="middle" font-size="13" font-weight="500" fill="#0C447C">安装向导</text><text x="124" y="131" text-anchor="middle" font-size="12" fill="#185FA5">模式选择·硬件探测</text></g>
<g><rect x="200" y="88" width="136" height="62" rx="8" fill="#FFFFFF" stroke="#85B7EB" stroke-width="0.5"/><text x="268" y="110" text-anchor="middle" font-size="13" font-weight="500" fill="#0C447C">主控台</text><text x="268" y="131" text-anchor="middle" font-size="12" fill="#185FA5">进度·RAG·本体看板</text></g>
<g><rect x="344" y="88" width="136" height="62" rx="8" fill="#FFFFFF" stroke="#85B7EB" stroke-width="0.5"/><text x="412" y="110" text-anchor="middle" font-size="13" font-weight="500" fill="#0C447C">任务管理</text><text x="412" y="131" text-anchor="middle" font-size="12" fill="#185FA5">定时·手动·暂停恢复</text></g>
<g><rect x="488" y="88" width="136" height="62" rx="8" fill="#FFFFFF" stroke="#85B7EB" stroke-width="0.5"/><text x="556" y="110" text-anchor="middle" font-size="13" font-weight="500" fill="#0C447C">设置</text><text x="556" y="131" text-anchor="middle" font-size="12" fill="#185FA5">工作目录·模型切换</text></g>
<path d="M340 160 L340 178" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#arrow)"/>
<rect x="40" y="180" width="600" height="44" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
<text x="340" y="202" text-anchor="middle" font-size="13" fill="#085041">REST /api/* · WebSocket /ws（事件流，seq 增量同步）</text>
<path d="M340 224 L340 242" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#arrow)"/>
<rect x="40" y="244" width="600" height="158" rx="12" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
<text x="54" y="262" font-size="12" fill="#3C3489">后端 Python · FastAPI（异步）</text>
<g><rect x="56" y="272" width="136" height="52" rx="8" fill="#FFFFFF" stroke="#AFA9EC" stroke-width="0.5"/><text x="124" y="290" text-anchor="middle" font-size="13" font-weight="500" fill="#3C3489">API 层</text><text x="124" y="310" text-anchor="middle" font-size="12" fill="#534AB7">REST + WS 广播</text></g>
<g><rect x="200" y="272" width="136" height="52" rx="8" fill="#FFFFFF" stroke="#AFA9EC" stroke-width="0.5"/><text x="268" y="290" text-anchor="middle" font-size="13" font-weight="500" fill="#3C3489">调度器</text><text x="268" y="310" text-anchor="middle" font-size="12" fill="#534AB7">APScheduler·SQLite</text></g>
<g><rect x="344" y="272" width="136" height="52" rx="8" fill="#FFFFFF" stroke="#AFA9EC" stroke-width="0.5"/><text x="412" y="290" text-anchor="middle" font-size="13" font-weight="500" fill="#3C3489">文档加载器</text><text x="412" y="310" text-anchor="middle" font-size="12" fill="#534AB7">PDF · Markdown</text></g>
<g><rect x="488" y="272" width="136" height="52" rx="8" fill="#FFFFFF" stroke="#AFA9EC" stroke-width="0.5"/><text x="556" y="290" text-anchor="middle" font-size="13" font-weight="500" fill="#3C3489">恢复器</text><text x="556" y="310" text-anchor="middle" font-size="12" fill="#534AB7">启动扫描·断点续跑</text></g>
<g><rect x="56" y="334" width="184" height="52" rx="8" fill="#FFFFFF" stroke="#AFA9EC" stroke-width="0.5"/><text x="148" y="352" text-anchor="middle" font-size="13" font-weight="500" fill="#3C3489">RAG 管线</text><text x="148" y="372" text-anchor="middle" font-size="12" fill="#534AB7">分段→summary→入库</text></g>
<g><rect x="250" y="334" width="184" height="52" rx="8" fill="#FFFFFF" stroke="#AFA9EC" stroke-width="0.5"/><text x="342" y="352" text-anchor="middle" font-size="13" font-weight="500" fill="#3C3489">本体归纳</text><text x="342" y="372" text-anchor="middle" font-size="12" fill="#534AB7">EDC 抽取→候选提名</text></g>
<g><rect x="444" y="334" width="184" height="52" rx="8" fill="#FFFFFF" stroke="#AFA9EC" stroke-width="0.5"/><text x="536" y="352" text-anchor="middle" font-size="13" font-weight="500" fill="#3C3489">模型管理器</text><text x="536" y="372" text-anchor="middle" font-size="12" fill="#534AB7">探测·下载·路由·自检</text></g>
<path d="M340 402 L340 420" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#arrow)"/>
<rect x="40" y="422" width="292" height="88" rx="12" fill="#FAEEDA" stroke="#854F0B" stroke-width="0.5"/>
<text x="186" y="442" text-anchor="middle" font-size="13" font-weight="500" fill="#633806">公司模式（推荐）</text>
<text x="186" y="464" text-anchor="middle" font-size="12" fill="#854F0B">DeepSeek V4 Flash 高频抽取</text>
<text x="186" y="484" text-anchor="middle" font-size="12" fill="#854F0B">GLM 5.2 归纳核验 · OpenAI 兼容</text>
<rect x="348" y="422" width="292" height="88" rx="12" fill="#FAEEDA" stroke="#854F0B" stroke-width="0.5"/>
<text x="494" y="442" text-anchor="middle" font-size="13" font-weight="500" fill="#633806">本地模式（自动部署）</text>
<text x="494" y="464" text-anchor="middle" font-size="12" fill="#854F0B">Ollama · embedding + LLM</text>
<text x="494" y="484" text-anchor="middle" font-size="12" fill="#854F0B">按显存/内存自动选型</text>
<path d="M340 510 L340 528" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#arrow)"/>
<g><rect x="40" y="530" width="192" height="64" rx="8" fill="#F1EFE8" stroke="#5F5E5A" stroke-width="0.5"/><text x="136" y="554" text-anchor="middle" font-size="13" font-weight="500" fill="#444441">SQLite</text><text x="136" y="575" text-anchor="middle" font-size="12" fill="#5F5E5A">任务·事件·调度·checkpoint</text></g>
<g><rect x="244" y="530" width="192" height="64" rx="8" fill="#F1EFE8" stroke="#5F5E5A" stroke-width="0.5"/><text x="340" y="554" text-anchor="middle" font-size="13" font-weight="500" fill="#444441">PostgreSQL+pgvector</text><text x="340" y="575" text-anchor="middle" font-size="12" fill="#5F5E5A">正式模式：向量与本体</text></g>
<g><rect x="448" y="530" width="192" height="64" rx="8" fill="#F1EFE8" stroke="#5F5E5A" stroke-width="0.5"/><text x="544" y="554" text-anchor="middle" font-size="13" font-weight="500" fill="#444441">内存 MemoryStore</text><text x="544" y="575" text-anchor="middle" font-size="12" fill="#5F5E5A">演示模式：零依赖</text></g>
</svg>

分层：前端 React（展示与配置）→ 通信层（REST + WebSocket 事件流）→ 后端 FastAPI（调度、加载、管线、恢复）→ 模型层（公司/本地双模式，可混合）→ 存储层（SQLite 任务态 + PG/内存 数据态）。

**架构要点**：

1. **任务态与数据态分离**：jobs / 事件 / 调度 / checkpoint / 设置放 **SQLite**（嵌入式、断电安全、随装随用）；RAG 数据（chunk、向量、summary）沿用雏形已有的 **MemoryStore / PGStore 双实现**——演示模式零依赖，正式模式 pgvector。切模式不迁移任务态。
2. **一切长操作皆任务**：导入文档、本体归纳、模型下载都统一为 job，走同一套进度上报、暂停/恢复、断点续跑机制（R4/R7/R8 共用一套机制）。
3. **模型管理器是唯一出口**：管线代码不直接调 OpenAI SDK，只向模型管理器要 `llm_client / embed_client`，由它负责模式路由、健康检查、降级（换模型不影响管线代码）。
4. **复用现有 rag/ 模块**：chunking / llm / embedding / store / pipeline 原样复用，新增的只有外面包的「服务化外壳」。

## 3. 技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| 前端 | React 18 + Vite + TypeScript + Zustand + native fetch/WebSocket | 轻量、无重型 UI 框架依赖；进度看板自绘即可 |
| 后端 | Python 3.12+ + FastAPI + uvicorn | 异步原生适合 WS 推送与长任务；雏形即 Python |
| 调度 | APScheduler（SQLiteJobStore） | cron/interval 持久化，重启不丢定时任务 |
| PDF 解析 | pypdf（文本型）；扫描件 OCR 列为非目标（见 §11） | 纯 Python、无系统依赖 |
| MD 解析 | markdown-it-py 或直接按标题/空行分段，交由现有 chunking | 与雏形一致 |
| 任务/事件存储 | SQLite（WAL 模式） | 断电安全，事务原子提交 checkpoint |
| 本地推理 | **Ollama**（首选） | 自动检测 GPU、自带断点续传下载、OpenAI 兼容 API、跨平台静默安装；备选 vLLM（仅 Linux 服务器场景） |
| 数据存储 | 沿用雏形：MemoryStore（演示）/ PostgreSQL 16 + pgvector（正式，docker compose） | 不重复造轮子 |

## 4. 前端设计（React）

### 4.1 页面与路由

| 路由 | 页面 | 内容 |
|---|---|---|
| `/setup` | 安装向导（首启强制跳转，见 §9） | 模式选择、硬件探测展示、模型下载、工作目录 |
| `/` | 主控台 Dashboard | 三卡片：①当前任务进度（阶段 + 进度条 + 日志尾部）②RAG 状况（文档数 / chunk 数 / 向量数 / 最近一次 ingest 时间）③本体构建状况（阶段管线：抽取→定义→规范化→校验→待审批，各阶段计数） |
| `/jobs` | 任务列表 + 详情 | 全部任务（含历史），详情页含日志流与事件回放 |
| `/schedules` | 定时任务管理 | 新建（选管线 + cron/interval）、启停、下次执行时间 |
| `/settings` | 设置 | 工作目录、模型模式切换（含分时时间窗配置，可后期切换，已下载模型与已入库数据保留）、LLM/embedding 分开配置（支持 D4 混合）、当前生效模式徽标（分时模式下显示此刻用的是本地还是公司） |

### 4.2 实时进度（R4）

- **主通道 WebSocket `/ws`**：服务端推送结构化事件（见 5.3），前端按 `job_id` 分发到对应组件；进度条用 `progress_current/total` 平滑插值。
- **兜底 REST**：`GET /api/jobs/{id}/events?since={seq}` 拉取增量事件（前端重连后、或 WS 不可用时）。
- **RAG / 本体状况卡**：不逐事件刷新组件树，服务端按 2s 节流聚合推送 `rag.stats` / `ontology.stage` 快照事件。

### 4.3 断线重连（前端侧，D3）

1. WS `onclose` → 指数退避（1s/2s/4s…上限 30s）自动重连；
2. 重连成功后携带本地已收到的 `last_event_seq`；
3. 服务端从该 seq 之后回放增量（事件表自增序号）；
4. 进度条无跳变、无重复（前端按 seq 去重幂等渲染）。

### 4.4 状态管理

Zustand 三个 store：`settingsStore`（配置，启动时 REST 拉取）、`jobStore`（任务字典 + 有序事件流，WS 驱动）、`statsStore`（RAG/本体聚合快照）。

## 5. 后端设计（FastAPI）

### 5.1 模块划分

```
backend/
├── app/
│   ├── main.py            # FastAPI 装配 + 启动恢复钩子
│   ├── api/               # REST 路由 + WS 端点
│   ├── scheduler/         # APScheduler 封装（SQLite JobStore）
│   ├── loader/            # document_loader：pdf.py / markdown.py → 统一 Document
│   ├── jobs/              # 任务模型、执行器、事件总线、恢复器
│   ├── models/            # 模型管理器（探测/安装/路由/健康检查）
│   └── rag/               # ← 雏形 rag/ 原样迁入（chunking/llm/embedding/store/pipeline）
```

### 5.2 REST API 清单

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查，含模型可用性、恢复器结果 |
| GET/PUT | `/api/settings` | 工作目录、模型模式、LLM/embedding 配置 |
| POST | `/api/models/probe` | 触发硬件探测，返回自动选型推荐方案（表见 6.2） |
| POST | `/api/models/install` | 安装本地模型（内部转 job，进度走 WS） |
| GET | `/api/models/status` | 各模型健康状态（最近一次成功调用） |
| POST | `/api/jobs` | 创建任务：`ingest`（扫描+导入）/ `ontology`（本体归纳）/ `probe`（自检） |
| GET | `/api/jobs`、`/api/jobs/{id}` | 任务列表 / 详情（含进度与 checkpoint） |
| POST | `/api/jobs/{id}/pause · resume · cancel` | 任务控制 |
| GET | `/api/jobs/{id}/events?since={seq}` | 事件增量（重连兜底） |
| CRUD | `/api/schedules` | 定时任务（管线类型 + cron/interval + 参数） |
| GET | `/api/rag/stats` | 文档数/chunk 数/向量数/最近 ingest |
| GET | `/api/ontology/candidates` | 本体候选提名 + 各阶段计数 + 审批状态 |
| WS | `/ws` | 事件流（下表） |

### 5.3 WebSocket 事件协议

| 事件类型 | 载荷要点 | 触发 |
|---|---|---|
| `job.created` | job_id, type | 任务入队 |
| `job.stage` | stage（chunking/summary/embedding/抽取/规范化…） | 阶段切换 |
| `job.progress` | current, total, 单位（chunk/文档/候选） | 每 chunk/每文档原子提交后 |
| `job.completed` / `job.failed` | 摘要 / 错误与重试建议 | 终态 |
| `model.download` | 已下载/总字节、速率 | Ollama pull 进度透传 |
| `model.switched` | 生效模式（local/company）、原因（时间窗边界 / 手动切换） | 分时模式时间窗切换或用户手动切换（6.5） |
| `rag.stats` | 节流快照 | 每 2s 或入库后 |
| `ontology.stage` | 阶段 + 计数快照 | 本体归纳各阶段 |
| `sys.recovered` | 恢复的 job 列表 | 服务启动且执行了断点续跑（D3 后端侧） |

所有事件带全局自增 `seq`（SQLite 事件表主键），供前端重连增量补齐。

### 5.4 文档加载（R5）

- `Document = {doc_id, source_path, mime, title, raw_text, sections[]}` 统一结构，之后交雏形 `rag/chunking.py`。
- **Markdown**：按标题层级 + 空行切 section，保留标题路径（如 `# 财务 > ## 报销`）作为 section 元数据，利于标签与本体抽取。
- **PDF**：pypdf 逐页抽文本；按页生成 section（带页码，证据关可回引页位）。**扫描件 PDF（无文本层）**：检测到空文本层即标记 `needs_ocr` 并跳过 + 上报事件，OCR 列为非目标（§11）。
- 编码兜底：MD 按 BOM/UTF-8/GBK 依次尝试。

### 5.5 定时任务（R3 / D2）

- APScheduler + SQLiteJobStore：定时定义持久化，重启自动恢复注册。
- 任务类型 = 5.2 中 `POST /api/jobs` 的同一套管线，调度只是"定时创建 job"。
- 典型用法：`每天 02:00 扫描工作目录 → 增量 ingest`（文件 mtime + 内容 hash 双判重）；`每周日 03:00 本体归纳`（增量归纳，不全量重建）。
- 调度触发与手动触发产生的 job 在任务列表中统一展示，来源字段区分。

## 6. 模型层设计（R2 / R6 / R7）

### 6.1 三种模式

| 模式 | LLM | Embedding | 适用 |
|---|---|---|---|
| 公司模式 | 公司私有部署（DeepSeek V4 Flash 高频抽取 + GLM 5.2 归纳核验，双模型按 pipeline.md 既有分工） | 公司 embedding 接口（OpenAI 兼容） | 办公网可达公司服务 |
| 本地模式 | Ollama 本地小模型（按 6.2 自动选型） | Ollama 本地 embedding | 离线/内网隔离 |
| 混合模式（D4） | 公司 LLM | 本地 embedding | 低配机器推荐：质量与速度兼顾 |
| **分时模式（R9 新增，推荐）** | 白天（默认 08:00–20:00，可配）本地小模型；夜间公司大模型 | 不分时（保持所配模式，建议本地） | 公司模型白天繁忙、夜间空闲的真实负载曲线；白天本地扛量不排队，夜间蹭公司空闲算力跑高质量归纳 |

模式可在设置页后期切换；配置存 SQLite settings，模型管理器热重载客户端（不打断运行中任务，任务用旧客户端跑完）。

### 6.2 本地环境探测与自动选型（D1）

探测项：GPU 型号与显存（`nvidia-smi`，失败再试 `torch.cuda`）、内存（psutil）、CPU 核数、磁盘剩余空间。探测结果与推荐方案一起在安装向导展示（用户可改选，自动只是默认值）。

| 硬件环境 | Embedding（必装） | LLM（本地） | 建议 |
|---|---|---|---|
| GPU 显存 ≥ 16G | bge-m3（GPU） | qwen3:8b Q4 | 本地全链路可行 |
| GPU 显存 6~16G | bge-m3 | qwen3:4b Q4 | 本地全链路可行；LLM 提名质量一般 |
| 无 GPU，内存 ≥ 16G | bge-m3（CPU，可用 bge-small-zh 降档） | qwen3:4b Q4 CPU（慢） | **推荐混合模式**：本地 embedding + 公司 LLM |
| 内存 < 16G | bge-small-zh-v1.5（CPU） | 不装 | 强烈建议公司模式；或演示模式先体验 |

> ⚠️ 风险声明（对用户可见）：本地 4B/8B 小 LLM 的本体抽取**提名质量显著弱于公司大模型**，三关校验拒绝率会偏高。工具会在本地模式下提示"本体归纳建议使用公司模型"。

### 6.3 自动部署流程（R7 开箱即用）

1. **装 Ollama 本体**：Windows 用官方 `OllamaSetup.exe /VERYSILENT` 静默安装（安装器内嵌于发行包，不依赖外网下载安装器）；Linux/macOS 用官方脚本。已装则跳过（探测 `ollama --version`）。
2. **拉模型**：`ollama pull bge-m3`、`ollama pull qwen3:4b` 等（Ollama 自身**支持断点续传**，进度透传为 `model.download` 事件）。
3. **离线兜底**：内网无外网时，发行包内置 GGUF 离线模型或提供"从本地文件导入模型"入口（`ollama create` + Modelfile）。
4. **自检**：拉完后真实调用一次 embedding + 一次 LLM 补全，全部通过才标记模式可用；失败给出可读错误（如端口占用、显存不足）。
5. 全程包装为 job，进度/失败均走统一 WS 通道——**模型安装与文档导入在用户眼里是同一种"任务"体验**。

### 6.4 路由与降级

- 管线代码只从模型管理器取 `get_llm(kind)`（kind = 高频抽取 / 归纳核验）与 `get_embedder()`。
- 公司模式：kind→模型的映射可配置（默认 Flash=高频、GLM=归纳）。
- 分时模式：`get_llm` 增加时间窗维度（见 6.5），其余模式时间窗不生效。
- 健康检查：连续 N 次调用失败 → 事件上报 + 前端标红 + 任务转 `failed`（附重试按钮），**不自动静默切换模型**（换模型属于用户决策，避免质量悄悄劣化——夜间公司模型偶发不可用时也不会悄悄掉回本地小模型）。

### 6.5 分时路由（R9 / D6）

公司大模型负载曲线：白天繁忙（排队、超时），夜间空闲。分时模式让两边各取所长——白天本地模型**立即可用不排队**，夜间公司模型**空闲且质量高**。

<svg viewBox="0 0 680 200" width="100%" role="img" xmlns="http://www.w3.org/2000/svg">
<text x="340" y="28" text-anchor="middle" font-size="14" font-weight="500" fill="#2C2C2A">分时路由：白天本地模型 · 夜间公司大模型</text>
<rect x="40" y="80" width="200" height="56" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
<text x="140" y="102" text-anchor="middle" font-size="12" font-weight="500" fill="#0C447C">夜间 · 公司大模型</text>
<text x="140" y="122" text-anchor="middle" font-size="12" fill="#185FA5">00:00 – 08:00</text>
<rect x="240" y="80" width="300" height="56" rx="8" fill="#FAEEDA" stroke="#854F0B" stroke-width="0.5"/>
<text x="390" y="102" text-anchor="middle" font-size="12" font-weight="500" fill="#633806">白天 · 本地模型（默认 08:00–20:00，可配）</text>
<text x="390" y="122" text-anchor="middle" font-size="12" fill="#854F0B">不排队、立即可用</text>
<rect x="540" y="80" width="100" height="56" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
<text x="590" y="102" text-anchor="middle" font-size="12" font-weight="500" fill="#0C447C">公司</text>
<text x="590" y="122" text-anchor="middle" font-size="12" fill="#185FA5">20:00 – 24:00</text>
<g stroke="#5F5E5A" stroke-width="0.5"><line x1="40" y1="136" x2="40" y2="144"/><line x1="240" y1="136" x2="240" y2="144"/><line x1="340" y1="136" x2="340" y2="144"/><line x1="440" y1="136" x2="440" y2="144"/><line x1="540" y1="136" x2="540" y2="144"/><line x1="640" y1="136" x2="640" y2="144"/></g>
<g font-size="12" fill="#5F5E5A" text-anchor="middle"><text x="40" y="160">00:00</text><text x="240" y="160">08:00</text><text x="340" y="160">12:00</text><text x="440" y="160">16:00</text><text x="540" y="160">20:00</text><text x="640" y="160">24:00</text></g>
<text x="340" y="186" text-anchor="middle" font-size="12" fill="#5F5E5A">任务启动时按时间窗领取模型，运行中不中断；切换发 model.switched 事件，前端显示当前生效模式</text>
</svg>

路由规则：

1. **生效点**：任务启动时向模型管理器领取 LLM 客户端，按当时所处时间窗返回本地或公司客户端；任务运行期间不切换（D6）。
2. **时间窗配置**：白天窗起止时间可改（默认 08:00–20:00），存 settings；周末/节假日可整日走公司（后续增强）。
3. **调度联动（关键用法）**：LLM 密集、质量敏感的定时任务（如**全量本体归纳、批量 ingest 的 summary/标签**）建议 `preferred_window = night`，由调度器排在夜间窗口触发（如 22:00）——**默认就用上公司大模型**，质量与空闲双赢。白天定时任务（扫描判重、轻量自检）走本地。
4. **低配机兜底**：本地无 LLM（6.2 判定不装）时，LLM 密集任务可选「延至夜间窗口」排队执行，白天只做 embedding 与规则 summary。
5. **切换可见**：时间窗边界切换或手动切换 → 广播 `model.switched` 事件（载荷：生效模式 + 原因），前端顶部状态徽标常显当前生效模式，避免用户不知道此刻用的哪个模型。
6. **不做的事**：夜间公司模型调用失败**不自动回落本地**（同 6.4，避免质量悄悄劣化），转 failed + 重试按钮，由用户决策。

## 7. 断电重连设计（R8 / D3）

<svg viewBox="0 0 680 406" width="100%" role="img" xmlns="http://www.w3.org/2000/svg">
<defs><marker id="arrow2" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>
<text x="340" y="28" text-anchor="middle" font-size="14" font-weight="500" fill="#2C2C2A">断电重连：后端断点续跑 + 前端增量补齐</text>
<rect x="40" y="52" width="300" height="330" rx="12" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
<text x="54" y="72" font-size="12" fill="#3C3489">后端 · 服务重启续跑（断电/崩溃通用）</text>
<g><rect x="54" y="88" width="272" height="44" rx="8" fill="#FFFFFF" stroke="#AFA9EC" stroke-width="0.5"/><text x="190" y="110" text-anchor="middle" font-size="12" fill="#3C3489">① 启动扫描 jobs（running / paused）</text></g>
<path d="M190 132 L190 146" fill="none" stroke="#AFA9EC" stroke-width="1.5" marker-end="url(#arrow2)"/>
<g><rect x="54" y="146" width="272" height="44" rx="8" fill="#FFFFFF" stroke="#AFA9EC" stroke-width="0.5"/><text x="190" y="168" text-anchor="middle" font-size="12" fill="#3C3489">② 读 checkpoint（doc / chunk seq / 阶段）</text></g>
<path d="M190 190 L190 204" fill="none" stroke="#AFA9EC" stroke-width="1.5" marker-end="url(#arrow2)"/>
<g><rect x="54" y="204" width="272" height="44" rx="8" fill="#FFFFFF" stroke="#AFA9EC" stroke-width="0.5"/><text x="190" y="226" text-anchor="middle" font-size="12" fill="#3C3489">③ 幂等校验：内容 hash 判重</text></g>
<path d="M190 248 L190 262" fill="none" stroke="#AFA9EC" stroke-width="1.5" marker-end="url(#arrow2)"/>
<g><rect x="54" y="262" width="272" height="44" rx="8" fill="#FFFFFF" stroke="#AFA9EC" stroke-width="0.5"/><text x="190" y="284" text-anchor="middle" font-size="12" fill="#3C3489">④ 从断点重入队续跑</text></g>
<path d="M190 306 L190 320" fill="none" stroke="#AFA9EC" stroke-width="1.5" marker-end="url(#arrow2)"/>
<g><rect x="54" y="320" width="272" height="44" rx="8" fill="#FFFFFF" stroke="#AFA9EC" stroke-width="0.5"/><text x="190" y="342" text-anchor="middle" font-size="12" fill="#3C3489">⑤ 广播 sys.recovered + 当前进度</text></g>
<rect x="340" y="52" width="300" height="330" rx="12" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
<text x="354" y="72" font-size="12" fill="#0C447C">前端 · 断线重连补齐</text>
<g><rect x="354" y="88" width="272" height="44" rx="8" fill="#FFFFFF" stroke="#85B7EB" stroke-width="0.5"/><text x="490" y="110" text-anchor="middle" font-size="12" fill="#0C447C">① WS onclose → 指数退避重连</text></g>
<path d="M490 132 L490 146" fill="none" stroke="#85B7EB" stroke-width="1.5" marker-end="url(#arrow2)"/>
<g><rect x="354" y="146" width="272" height="44" rx="8" fill="#FFFFFF" stroke="#85B7EB" stroke-width="0.5"/><text x="490" y="168" text-anchor="middle" font-size="12" fill="#0C447C">② 重连后上报 last_event_seq</text></g>
<path d="M490 190 L490 204" fill="none" stroke="#85B7EB" stroke-width="1.5" marker-end="url(#arrow2)"/>
<g><rect x="354" y="204" width="272" height="44" rx="8" fill="#FFFFFF" stroke="#85B7EB" stroke-width="0.5"/><text x="490" y="226" text-anchor="middle" font-size="12" fill="#0C447C">③ 服务端从 seq 增量回放事件</text></g>
<path d="M490 248 L490 262" fill="none" stroke="#85B7EB" stroke-width="1.5" marker-end="url(#arrow2)"/>
<g><rect x="354" y="262" width="272" height="44" rx="8" fill="#FFFFFF" stroke="#85B7EB" stroke-width="0.5"/><text x="490" y="284" text-anchor="middle" font-size="12" fill="#0C447C">④ 按 seq 去重，进度无跳变</text></g>
<path d="M490 306 L490 320" fill="none" stroke="#85B7EB" stroke-width="1.5" marker-end="url(#arrow2)"/>
<g><rect x="354" y="320" width="272" height="44" rx="8" fill="#FFFFFF" stroke="#85B7EB" stroke-width="0.5"/><text x="490" y="342" text-anchor="middle" font-size="12" fill="#0C447C">⑤ REST /events?since= 兜底</text></g>
</svg>

### 7.1 核心机制：checkpoint 与原子提交

- 每个 job 带 `checkpoint` 字段（JSON：`last_doc_id / last_chunk_seq / last_stage`）。
- **每处理完一个 chunk / 一篇文档，在同一个 SQLite 事务里**：写入数据 + 更新 job 进度与 checkpoint → 任意时刻断电，最多重做**一个 chunk**。
- 幂等保障重跑安全：文档按**内容 hash 唯一索引**判重（重复导入直接跳过）；chunk 按 `(doc_id, seq)` upsert；本体候选按提名指纹去重。
- 模型下载断点续传由 Ollama 自带；下载 job 的 checkpoint = 已完成字节数。
- 定时任务定义存 SQLiteJobStore，重启自动重注册。

### 7.2 恢复流程

服务启动（或崩溃重启）后由恢复器执行：

1. 扫描 jobs 表 `status ∈ {running, paused}`；
2. 读取 checkpoint，做幂等校验；
3. 从断点重入队续跑（不从头开始）；
4. WS 广播 `sys.recovered` + 各任务当前进度，前端立即对齐。

前端断线重连：指数退避重连 → 上报 `last_event_seq` → 服务端按 seq 增量回放 → 按 seq 去重渲染，进度条无跳变。

## 8. 数据模型（SQLite，正式 RAG 数据仍在 PG/内存）

| 表 | 关键字段 | 用途 |
|---|---|---|
| `settings` | key, value(json) | 工作目录、模型模式与配置、阈值 |
| `jobs` | id, type, payload(json), status, progress_current/total, checkpoint(json), error, created_at, updated_at, source(manual/schedule) | 任务态机 |
| `job_events` | seq(自增主键), job_id, type, payload(json), ts | 事件流 + 重连回放 |
| `schedules` | id, pipeline, trigger(cron/interval), params(json), enabled, next_run | 定时任务（APScheduler JobStore 兼容） |

任务状态机：`pending → running → completed / failed / cancelled`；`running ↔ paused`。`running` 在进程死亡后由恢复器在**下次启动时**回收（不做心跳看门狗——单机工具，启动恢复已足够）。

## 9. 安装向导（R6 首启流程）

<svg viewBox="0 0 680 524" width="100%" role="img" xmlns="http://www.w3.org/2000/svg">
<defs><marker id="arrow3" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>
<text x="340" y="28" text-anchor="middle" font-size="14" font-weight="500" fill="#2C2C2A">首次启动安装向导（开箱即用）</text>
<g><rect x="280" y="48" width="120" height="40" rx="8" fill="#FFFFFF" stroke="#5F5E5A" stroke-width="0.5"/><text x="340" y="68" text-anchor="middle" font-size="13" fill="#2C2C2A">欢迎页</text></g>
<path d="M340 88 L340 108" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#arrow3)"/>
<g><rect x="200" y="108" width="280" height="52" rx="8" fill="#FFFFFF" stroke="#185FA5" stroke-width="0.5"/><text x="340" y="128" text-anchor="middle" font-size="13" font-weight="500" fill="#0C447C">选择模型模式</text><text x="340" y="148" text-anchor="middle" font-size="12" fill="#185FA5">公司 · 本地 · 分时 · 稍后（演示）</text></g>
<path d="M260 160 L132 190" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#arrow3)"/>
<path d="M340 160 L340 190" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#arrow3)"/>
<path d="M420 160 L548 190" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#arrow3)"/>
<g><rect x="40" y="190" width="185" height="48" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/><text x="132" y="214" text-anchor="middle" font-size="12" fill="#0C447C">填写 base_url + api_key</text></g>
<path d="M132 238 L132 252" fill="none" stroke="#185FA5" stroke-width="1.5" marker-end="url(#arrow3)"/>
<g><rect x="40" y="252" width="185" height="48" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/><text x="132" y="278" text-anchor="middle" font-size="12" fill="#0C447C">连通测试（真实调用一次）</text></g>
<g><rect x="248" y="190" width="185" height="48" rx="8" fill="#FAEEDA" stroke="#854F0B" stroke-width="0.5"/><text x="340" y="214" text-anchor="middle" font-size="12" fill="#633806">硬件探测（GPU·内存·CPU）</text></g>
<path d="M340 238 L340 252" fill="none" stroke="#854F0B" stroke-width="1.5" marker-end="url(#arrow3)"/>
<g><rect x="248" y="252" width="185" height="48" rx="8" fill="#FAEEDA" stroke="#854F0B" stroke-width="0.5"/><text x="340" y="278" text-anchor="middle" font-size="12" fill="#633806">自动选型，展示推荐表</text></g>
<path d="M340 300 L340 314" fill="none" stroke="#854F0B" stroke-width="1.5" marker-end="url(#arrow3)"/>
<g><rect x="248" y="314" width="185" height="48" rx="8" fill="#FAEEDA" stroke="#854F0B" stroke-width="0.5"/><text x="340" y="340" text-anchor="middle" font-size="12" fill="#633806">下载模型 + 自检（进度上报）</text></g>
<g><rect x="456" y="190" width="184" height="48" rx="8" fill="#F1EFE8" stroke="#5F5E5A" stroke-width="0.5"/><text x="548" y="214" text-anchor="middle" font-size="12" fill="#444441">跳过 → 演示模式</text></g>
<path d="M548 238 L548 252" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#arrow3)"/>
<g><rect x="456" y="252" width="184" height="48" rx="8" fill="#F1EFE8" stroke="#5F5E5A" stroke-width="0.5"/><text x="548" y="278" text-anchor="middle" font-size="12" fill="#444441">内存存储 + hash embedding</text></g>
<path d="M132 300 L250 392" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#arrow3)"/>
<path d="M340 362 L340 392" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#arrow3)"/>
<path d="M548 300 L430 392" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#arrow3)"/>
<g><rect x="190" y="392" width="300" height="52" rx="8" fill="#FFFFFF" stroke="#534AB7" stroke-width="0.5"/><text x="340" y="412" text-anchor="middle" font-size="13" font-weight="500" fill="#3C3489">设置工作目录</text><text x="340" y="432" text-anchor="middle" font-size="12" fill="#534AB7">首次扫描入库（mtime + hash 判重）</text></g>
<path d="M340 444 L340 464" fill="none" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#arrow3)"/>
<g><rect x="250" y="464" width="180" height="40" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/><text x="340" y="484" text-anchor="middle" font-size="13" font-weight="500" fill="#085041">完成 → 进入主控台</text></g>
</svg>

首启强制进入 `/setup`：欢迎页 → 选模式（公司 / 本地 / **分时** / 稍后）→ 公司模式填 base_url + key 并做真实连通测试；本地/分时模式自动硬件探测 → 展示 6.2 推荐表（可改）→ 静默装 Ollama + 拉模型（进度条）→ 自检通过（分时模式额外做"公司连通测试 + 时间窗预览"）→ 设置工作目录并首次扫描入库 → 进主控台。"稍后" = 演示模式（内存存储 + hash embedding + 规则 summary，零依赖可完整体验界面）。

## 10. 里程碑

| 阶段 | 内容 | 依赖 |
|---|---|---|
| M1 | 服务化外壳：FastAPI + REST/WS + jobs/事件/checkpoint + 恢复器 + PDF/MD 加载 + 公司模式（复用雏形 rag/） | 无 |
| M2 | 安装向导 + 硬件探测/自动选型 + Ollama 自动部署（含离线导入） + 定时任务 + **分时路由（R9：时间窗配置、model.switched、夜间窗口调度）** | M1 |
| M3 | 前端主控台完整看板（RAG/本体实时状况）+ 本体归纳管线接入 | M1 + pipeline.md §9.2 方案定稿 |

## 11. 风险与开放问题

1. **本地小 LLM 提名质量**（已声明，见 6.2）：本地模式默认把本体归纳引导到公司模型，或明示风险让用户自担。
2. **扫描件 PDF 无文本层**：本期不做 OCR，检测后标记跳过并上报——需确认部门文档中扫描件占比。
3. **公司 embedding 接口是否存在**：若公司只部署了 LLM 无 embedding 服务，混合模式（本地 bge + 公司 LLM）为默认推荐。
4. **内网下载 Ollama 模型**：需确认目标机器外网可达性；不可达则发行包必须内置离线模型。
5. **文件系统监控**（开放）：本期用定时扫描 + mtime/hash 判重；watchdog 实时监听列后续增强。
6. **本体归纳（M3）依赖 pipeline.md 9.2 待确认**：EDC + GraphRAG 混合式方案评审通过后才能细化本体阶段事件协议。
7. **跨时间窗的长任务**（分时模式）：v1 任务运行中不换模型（D6）。增强方向——因 checkpoint 是 chunk 粒度，长任务可在 chunk 提交边界重新领取客户端，实现"白天本地起步、入夜自动换公司模型续跑"，与 R9 目标完全吻合；需任务执行器支持按阶段重领取，列为 v2 增强。
8. **分时窗口两端都不可用**（白天本地故障 + 夜间公司故障）：按 6.4 各自转 failed 并上报，不级联自动切换；用户手动兜底。
