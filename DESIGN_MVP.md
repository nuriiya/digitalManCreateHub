# 知识包构建工具 · MVP 设计文档（前端 + 后端）

> 版本：v1.0（待评审）
> 日期：2026-08-30
> 定位：`DESIGN.md`（完整版 v1.1）的**先行裁剪落地版**——先跑通「配置 → 提取 → 预览 → 审批」核心闭环；定时任务、分时用模、GLM 归纳核验等按完整版规划延后。
> 本文只做设计，不写实现代码。
> 图示为深色版，配色语义沿用 pipeline.md v5：蓝=前端/Windows、绿(青)=WSL2 推理与存储、橙=公司模型/异常分支、灰=缓存/任务态。

---

## 0. 需求拆解与范围

| # | 要求 | 归属 |
|---|---|---|
| M1 | 设置模型：Ollama 下载合适的 embedding 模型（运行在 WSL2 内） | 设置页 + Ollama 管理 |
| M2 | V4-Flash token 填入 | 设置页 |
| M3 | 设置工作目录 | 设置页 |
| M4 | RAG 提取后**数据库预览界面** | 前端 |
| M5 | 本体提取后的**图示**（实体关系图） | 前端 |
| M6 | 各本体之间的关系展示 + **审批界面** | 前端 |
| M7 | Ollama（及存储）放到 **WSL2** 里运行 | 部署层 |
| M8 | 启动脚本：自动检查 WSL2 配置、自动识别已有账户用户名、询问密码并缓存（供后续 sudo） | `start.ps1` |

**明确搁置（MVP 不做，完整版 DESIGN.md 已有规划）**：定时任务/调度器（用户明示搁置「任务规划」）、分时用模 R9、GLM 5.2 归纳核验、本地小 LLM 选型（简化为仅本地 embedding）、扫描件 OCR、断电重连增强（保留 chunk 级 checkpoint 基本能力）。

## 1. 歧义决策（评审重点）

| 编号 | 歧义 | 本文决策 |
|---|---|---|
| DM1 | MVP 的公司 LLM 只有 V4-Flash？ | **是**。设置页独立成节：base_url + token + model 三项**全部可编辑**（当前为测试阶段，允许填外网/第三方 OpenAI 兼容端点），保存即做真连通测试；规范化复核降级为「定义向量近邻召回 + Flash 轻量复核」，GLM 5.2 按完整版 §6 延后接入 |
| DM2 | embedding 模型选哪个 | 默认 **bge-m3**（Ollama 拉取，1024 维，中文强，CPU 可跑）；备选 nomic-embed-text。**选定即锁定**——铁则 1：embedding 全程唯一模型（向量可比性），换模型只能全量重嵌入（job） |
| DM3 | sudo 密码缓存到哪才安全 | **DPAPI 加密文件**（`ConvertFrom-SecureString`，绑定当前 Windows 用户 + 本机），不落明文、不进环境变量；设置页提供「清除密码缓存」。备选 Windows 凭据管理器（PS 5.1 读回需 P/Invoke，成本高，列为 v2） |
| DM4 | RAG/本体数据存哪 | 与 Ollama 同在 WSL2 的 **PostgreSQL + pgvector**（沿用雏形 PGStore）；SQLite 只存任务态（jobs/事件/设置/checkpoint），与完整版「任务态/数据态分离」一致 |
| DM7（实现期新增） | v1 代码的数据态先用 SQLite | MS1/MS2 阶段数据态（documents/chunks/候选/证据）与任务态同放一个 SQLite 文件（`data/app.db`），**零外部依赖即可跑通全链路与 UT**；Store 保留接口，MS3 接 PG/pgvector 时仅换实现不动管线。理由：测试环境当前没有 WSL2/PG，先保证「代码+测试」先行 |
| DM5 | 「任务规划搁置」的边界 | 无调度器、无定时触发；ingest 与本体归纳都是**手动触发的 job**；进度实时展示（WS）保留——它是预览页/审批页可信度的前提 |
| DM6 | 前后端跑在哪 | 前后端跑 **Windows**：uvicorn :8000 托管前端 dist，浏览器访问 `http://localhost:8000`；WSL2 只跑 Ollama + PG，经 WSL2 localhost 端口转发（:11434 / :5432）访问。**文件由 Windows 侧后端读取**（工作目录是 Windows 路径），不涉及 /mnt 路径转换 |

## 2. 总体架构

<svg viewBox="0 0 680 396" width="100%" role="img" xmlns="http://www.w3.org/2000/svg">
<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>
<text x="340" y="26" text-anchor="middle" font-size="14" font-weight="500" fill="#D3D1C7">MVP 总体架构：Windows 前后端 + WSL2 推理存储 + 公司 V4-Flash</text>
<rect x="40" y="46" width="600" height="132" rx="12" fill="#0C447C" stroke="#85B7EB" stroke-width="0.5"/>
<text x="54" y="66" font-size="12" fill="#B5D4F4">Windows 宿主机</text>
<g><rect x="56" y="82" width="160" height="56" rx="8" fill="#185FA5" stroke="#85B7EB" stroke-width="0.5"/><text x="136" y="103" text-anchor="middle" font-size="13" font-weight="500" fill="#E6F1FB">React 前端</text><text x="136" y="124" text-anchor="middle" font-size="12" fill="#B5D4F4">Vite · 图谱·预览·审批</text></g>
<path d="M220 110 L248 110" fill="none" stroke="#B4B2A9" stroke-width="1.5" marker-end="url(#arrow)"/>
<text x="234" y="96" text-anchor="middle" font-size="11" fill="#B4B2A9">REST·WS</text>
<g><rect x="256" y="82" width="170" height="56" rx="8" fill="#185FA5" stroke="#85B7EB" stroke-width="0.5"/><text x="341" y="103" text-anchor="middle" font-size="13" font-weight="500" fill="#E6F1FB">FastAPI 后端</text><text x="341" y="124" text-anchor="middle" font-size="12" fill="#B5D4F4">管线 · job · 审批 API</text></g>
<path d="M426 110 L462 110" fill="none" stroke="#B4B2A9" stroke-width="1.5" marker-end="url(#arrow)"/>
<text x="444" y="96" text-anchor="middle" font-size="11" fill="#B4B2A9">任务态</text>
<g><rect x="466" y="82" width="158" height="56" rx="8" fill="#5F5E5A" stroke="#B4B2A9" stroke-width="0.5"/><text x="545" y="103" text-anchor="middle" font-size="13" font-weight="500" fill="#F1EFE8">SQLite</text><text x="545" y="124" text-anchor="middle" font-size="12" fill="#D3D1C7">jobs·事件·设置</text></g>
<path d="M300 178 L300 242" fill="none" stroke="#B4B2A9" stroke-width="1.5" marker-end="url(#arrow)"/>
<text x="310" y="214" font-size="12" fill="#B4B2A9">embedding · SQL</text>
<path d="M540 178 L540 242" fill="none" stroke="#B4B2A9" stroke-width="1.5" marker-end="url(#arrow)"/>
<text x="550" y="214" font-size="12" fill="#B4B2A9">LLM · Bearer token</text>
<rect x="40" y="246" width="380" height="130" rx="12" fill="#085041" stroke="#5DCAA5" stroke-width="0.5"/>
<text x="54" y="266" font-size="12" fill="#9FE1CB">WSL2 · Ubuntu</text>
<g><rect x="56" y="282" width="160" height="56" rx="8" fill="#0F6E56" stroke="#5DCAA5" stroke-width="0.5"/><text x="136" y="303" text-anchor="middle" font-size="13" font-weight="500" fill="#E1F5EE">Ollama</text><text x="136" y="324" text-anchor="middle" font-size="12" fill="#9FE1CB">bge-m3 embedding</text></g>
<g><rect x="240" y="282" width="164" height="56" rx="8" fill="#0F6E56" stroke="#5DCAA5" stroke-width="0.5"/><text x="322" y="303" text-anchor="middle" font-size="13" font-weight="500" fill="#E1F5EE">PostgreSQL+pgvector</text><text x="322" y="324" text-anchor="middle" font-size="12" fill="#9FE1CB">chunks·向量·本体</text></g>
<text x="230" y="360" text-anchor="middle" font-size="11" fill="#9FE1CB">localhost:11434 / 5432 自动端口转发</text>
<rect x="440" y="246" width="200" height="130" rx="12" fill="#633806" stroke="#FAC775" stroke-width="0.5"/>
<text x="454" y="266" font-size="12" fill="#FAC775">公司内网</text>
<g><rect x="456" y="282" width="168" height="56" rx="8" fill="#854F0B" stroke="#FAC775" stroke-width="0.5"/><text x="540" y="303" text-anchor="middle" font-size="13" font-weight="500" fill="#FAEEDA">V4-Flash</text><text x="540" y="324" text-anchor="middle" font-size="12" fill="#FAC775">OpenAI 兼容 · token</text></g>
</svg>

**架构要点**：

1. **单进程域**：FastAPI 托管 `client/dist`，浏览器只访问 `http://localhost:8000`（与 myClaudeCode 工程交付口径一致）。
2. **WSL2 只承担「推理 + 存储」**：Ollama（embedding）与 PG（pgvector）都在 WSL2，Windows 侧零 GPU/数据库依赖；WSL2 的 localhost 端口转发让后端像访问本机服务一样访问它们。
3. **模型出口唯一**：管线代码只向「模型管理器」要 `llm_client`（V4-Flash，OpenAI 兼容）与 `embed_client`（Ollama），不直接碰 SDK。
4. **双流共用切分**：ingest 一次解析分段，RAG 流（summary→标签→嵌入→入库）与本体流（EDC 抽取→候选提名）并行分流，共用同一套 job 进度机制。
5. **雏形复用**：`rag_prototype/rag/` 的 chunking / store / pipeline 原样迁入后端。

## 3. 启动脚本与 WSL2 部署（M7 / M8）

<svg viewBox="0 0 680 520" width="100%" role="img" xmlns="http://www.w3.org/2000/svg">
<defs><marker id="arrow2" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>
<text x="340" y="26" text-anchor="middle" font-size="14" font-weight="500" fill="#D3D1C7">start.ps1：WSL2 自检 · 用户名识别 · 密码缓存 · 一键启动</text>
<g><rect x="60" y="56" width="250" height="52" rx="8" fill="#0F6E56" stroke="#5DCAA5" stroke-width="0.5"/><text x="185" y="78" text-anchor="middle" font-size="13" font-weight="500" fill="#E1F5EE">检查 WSL2 环境</text><text x="185" y="96" text-anchor="middle" font-size="12" fill="#9FE1CB">wsl --status · wsl -l -v</text></g>
<g><rect x="60" y="134" width="250" height="52" rx="8" fill="#0F6E56" stroke="#5DCAA5" stroke-width="0.5"/><text x="185" y="156" text-anchor="middle" font-size="13" font-weight="500" fill="#E1F5EE">自动识别 WSL 用户名</text><text x="185" y="174" text-anchor="middle" font-size="12" fill="#9FE1CB">getent passwd · UID≥1000</text></g>
<g><rect x="60" y="212" width="250" height="52" rx="8" fill="#0F6E56" stroke="#5DCAA5" stroke-width="0.5"/><text x="185" y="234" text-anchor="middle" font-size="13" font-weight="500" fill="#E1F5EE">读取 sudo 密码缓存</text><text x="185" y="252" text-anchor="middle" font-size="12" fill="#9FE1CB">DPAPI 文件 · 失效则询问</text></g>
<g><rect x="60" y="290" width="250" height="52" rx="8" fill="#0F6E56" stroke="#5DCAA5" stroke-width="0.5"/><text x="185" y="312" text-anchor="middle" font-size="13" font-weight="500" fill="#E1F5EE">WSL2 幂等安装</text><text x="185" y="330" text-anchor="middle" font-size="12" fill="#9FE1CB">Ollama · PG+pgvector</text></g>
<g><rect x="60" y="368" width="250" height="52" rx="8" fill="#0F6E56" stroke="#5DCAA5" stroke-width="0.5"/><text x="185" y="390" text-anchor="middle" font-size="13" font-weight="500" fill="#E1F5EE">启动服务·健康检查</text><text x="185" y="408" text-anchor="middle" font-size="12" fill="#9FE1CB">:11434 · :5432 探活</text></g>
<g><rect x="60" y="446" width="250" height="52" rx="8" fill="#0F6E56" stroke="#5DCAA5" stroke-width="0.5"/><text x="185" y="468" text-anchor="middle" font-size="13" font-weight="500" fill="#E1F5EE">启动前后端</text><text x="185" y="486" text-anchor="middle" font-size="12" fill="#9FE1CB">uvicorn :8000 托管 dist</text></g>
<path d="M185 108 L185 130" fill="none" stroke="#B4B2A9" stroke-width="1.5" marker-end="url(#arrow2)"/>
<path d="M185 186 L185 208" fill="none" stroke="#B4B2A9" stroke-width="1.5" marker-end="url(#arrow2)"/>
<path d="M185 264 L185 286" fill="none" stroke="#B4B2A9" stroke-width="1.5" marker-end="url(#arrow2)"/>
<path d="M185 342 L185 364" fill="none" stroke="#B4B2A9" stroke-width="1.5" marker-end="url(#arrow2)"/>
<path d="M185 420 L185 442" fill="none" stroke="#B4B2A9" stroke-width="1.5" marker-end="url(#arrow2)"/>
<path d="M310 82 L360 82" fill="none" stroke="#888780" stroke-width="0.5" stroke-dasharray="4 3"/>
<g><rect x="360" y="56" width="270" height="52" rx="8" fill="#854F0B" stroke="#FAC775" stroke-width="0.5"/><text x="495" y="78" text-anchor="middle" font-size="13" font-weight="500" fill="#FAEEDA">未启用？引导启用安装</text><text x="495" y="96" text-anchor="middle" font-size="12" fill="#FAC775">装 Ubuntu · 提示重启</text></g>
<path d="M310 238 L360 238" fill="none" stroke="#888780" stroke-width="0.5" stroke-dasharray="4 3"/>
<g><rect x="360" y="212" width="270" height="52" rx="8" fill="#854F0B" stroke="#FAC775" stroke-width="0.5"/><text x="495" y="234" text-anchor="middle" font-size="13" font-weight="500" fill="#FAEEDA">首次：询问并验证密码</text><text x="495" y="252" text-anchor="middle" font-size="12" fill="#FAC775">sudo -S 验证 → DPAPI 缓存</text></g>
<path d="M310 316 L360 316" fill="none" stroke="#888780" stroke-width="0.5" stroke-dasharray="4 3"/>
<g><rect x="360" y="290" width="270" height="52" rx="8" fill="#5F5E5A" stroke="#B4B2A9" stroke-width="0.5"/><text x="495" y="312" text-anchor="middle" font-size="13" font-weight="500" fill="#F1EFE8">已装自动跳过</text><text x="495" y="330" text-anchor="middle" font-size="12" fill="#D3D1C7">幂等脚本不重装</text></g>
</svg>

### 3.1 start.ps1 步骤（命令级）

1. **WSL2 自检**：`wsl --status` + `wsl -l -v`。无发行版 → 提示 `wsl --install -d Ubuntu`（需要重启的场景打印明确指引后退出）；「虚拟机平台」未启用 → 指引 BIOS 开虚拟化（VT-x）+ 启用 Windows 功能。
2. **用户名识别（M8）**：`wsl -d <distro> -- bash -lc "getent passwd | awk -F: '$3>=1000 && $3<65534 {print $1}'"`；多个普通用户时列出让用户选择（默认取第一个）。
3. **密码管理（M8 / DM3）**：
   - 读 DPAPI 缓存文件 `data/wsl_cred`（存在则解密）；
   - 不存在/失效 → `Read-Host -AsSecureString` 询问 → `echo <pw> | wsl -d <distro> -u <user> -- sudo -S -k true` 验证（重试 ≤3 次）；
   - 验证通过 → `ConvertFrom-SecureString` 写入 DPAPI 缓存（绑定当前 Windows 用户 + 本机）。
4. **WSL2 幂等安装（bootstrap.sh，sudo 执行）**：
   - `apt-get update && apt-get install -y postgresql postgresql-16-pgvector`（Ubuntu 24.04 自带该包；旧发行版走 PGDG apt 源兜底）；
   - Ollama 已装则跳过，否则 `curl -fsSL https://ollama.com/install.sh | sh`；
   - PG 建库 `digested` + `CREATE EXTENSION IF NOT EXISTS vector`；
   - 启动服务：`service postgresql start`；`ollama serve`（已监听 :11434 则跳过，nohup 后台）。
5. **健康检查**：`curl -s http://localhost:11434/api/tags`、`pg_isready -h localhost -p 5432`；失败打印诊断（端口占用 / WSL 端口转发未生效）。
6. **模型检查**：查询已拉模型；缺 bge-m3 → 提示进设置页拉取（进度走 WS）或命令行 `wsl -- ollama pull bge-m3`。
7. **启动应用**：`uvicorn app.main:app --port 8000`（托管 `client/dist`），启动后自动打开浏览器。

### 3.2 安全说明（DM3）

- 密码只以 SecureString 形态存在于内存，落盘是 DPAPI 密文（其他用户/其他机器不可解密）；不进日志、不进 WS 事件、不进环境变量。
- 设置页提供「清除密码缓存」（删除 `data/wsl_cred`），下次启动重新询问。
- sudo 仅用于 bootstrap/安装/服务启动；日常运行（Ollama API、PG 查询）走网络端口，不需要 sudo。

## 4. 后端设计（FastAPI）

### 4.1 模块划分

```
backend/
├── app/
│   ├── main.py            # 装配 + 托管 client/dist + WS 端点
│   ├── api/               # settings / ollama / ingest / rag / ontology / approval / jobs
│   ├── jobs/              # job 执行器 + 事件总线（完整版机制去掉调度器）
│   ├── models/            # 模型管理器：V4-Flash 客户端（OpenAI 兼容）+ Ollama 客户端
│   ├── loader/            # document_loader：pdf.py（pypdf）/ markdown.py → 统一 Document
│   └── rag/               # 雏形 rag/ 原样迁入（chunking / store / pipeline）
├── bootstrap.sh           # WSL2 内幂等安装（sudo）
└── start.ps1              # 一键启动（§3）
```

### 4.2 REST API 清单

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/settings` | 当前配置（工作目录 / LLM / embedding；token 只回传 has_token） |
| PUT | `/api/settings/workdir` | 设置工作目录（校验存在、可写，记录） |
| PUT | `/api/settings/llm` | base_url + token + model |
| POST | `/api/settings/llm/test` | 真连通测试（最小 chat 请求，返回延迟/模型名） |
| GET | `/api/ollama/status` | 安装/运行状态 + 已拉模型列表 |
| POST | `/api/ollama/pull` | 拉取 embedding 模型 → job，进度走 WS |
| POST | `/api/ingest` | 手动触发：扫描工作目录 PDF/MD → 解析→分段→summary/标签→嵌入→入库 |
| GET | `/api/jobs`、`/api/jobs/{id}` | 任务列表 / 详情（进度、阶段、错误） |
| GET | `/api/jobs/{id}/events?since={seq}` | 事件增量（REST 兜底） |
| GET | `/api/rag/stats` | 文档 / chunk / 向量计数（概览卡） |
| GET | `/api/rag/chunks` | 预览分页（摘要、标签、来源文档） |
| GET | `/api/rag/chunks/{id}` | chunk 详情（原文 + 字符区间 + 证据反查） |
| POST | `/api/rag/search` | 查询文本 → 本地嵌入 → 近邻 top-k（预览页检索测试） |
| POST | `/api/ontology/extract` | 手动触发本体归纳（EDC）→ 候选提名 |
| GET | `/api/ontology/graph` | 图数据：默认 approved；`?include=pending` 附候选（前端虚线呈现） |
| GET | `/api/approval/pending` | 待审批实体/关系队列（含证据与计数） |
| POST | `/api/approval/decide` | `{kind, id, action: approve\|reject\|merge, merge_into?}` |

### 4.3 WebSocket 事件（/ws）

| 事件 | 载荷 | 说明 |
|---|---|---|
| `job.progress` | job_id、stage、current/total | ingest / 归纳 / 拉模型共用 |
| `ollama.pull` | model、received/total、速率 | 模型下载进度条 |
| `rag.stats` | 文档/chunk/向量数 | 入库侧 2s 节流聚合推送 |
| `ontology.stage` | 各阶段计数（抽取/定义/规范化/待审批） | 管线阶段卡 |
| `approval.updated` | 队列变化 | 图谱/审批页刷新 |

前端 WS 断线按完整版 §4.3 的指数退避 + `since` 增量补齐（协议保留，简化实现）。

### 4.4 数据模型

**PG（pgvector，WSL2 内）——数据态**：

```
documents(id, path, hash, size, added_at)
chunks(id, doc_id, seq, text, char_start, char_end)
summaries(chunk_id PK→chunks, summary, labels text[], embedding vector(1024))   -- bge-m3
candidates(id, kind ∈ {entity, relation}, name, type, relation,
           source_cand, target_cand, definition, state, merged_into, created_at)
mentions(id, cand_id→candidates, chunk_id→chunks, span_start, span_end, span_text)   -- 证据关：span 逐字回原文
approved_entities(id, name, type, definition, from_cand)
approved_relations(id, source, target, relation, from_cand)
```

**SQLite（Windows 侧）——任务态**：

```
jobs(id, type ∈ {ingest, ontology, model_pull}, state, progress, checkpoint, error)
events(seq AUTOINCREMENT, job_id, type, payload)
settings(key, value)
```

### 4.5 本体管线（EDC · Flash）

1. **开放抽取**（V4-Flash，高频短上下文）：逐 chunk 不喂 schema 抽 SPO 候选 + span；宁多勿漏。
2. **定义生成**（Flash）：每个候选一句自然语言定义。
3. **规范化**：定义 embedding（bge-m3）近邻召回 → Flash 轻量复核合并（GLM 5.2 延后）；对齐平台通用本体**只引用不改写**；低频候选过滤。
4. **三关校验**（确定性代码，LLM 无终审权）：结构（闭集/类型/长度）→ 语义（引用/基数/重复）→ 证据（span 逐字匹配原文）。
5. 产出 state=`proposed` 的候选 → 进审批队列。checkpoint 为 chunk 粒度，失败可续。

## 5. 前端设计（React）

### 5.1 页面与路由

| 路由 | 页面 | 内容 |
|---|---|---|
| `/setup` | 首启向导 | ① V4-Flash（base_url+token+测试）② WSL2/Ollama 状态 + 拉 bge-m3（进度条）③ 设置工作目录 → 进主界面 |
| `/` | 概览 | 任务进度条 + RAG 统计卡 + 本体阶段卡 |
| `/rag` | RAG 数据预览（M4） | 见 §5.2 |
| `/ontology` | 本体图谱与审批（M5/M6） | 见 §5.3 |
| `/settings` | 设置 | 工作目录、LLM/embedding 配置、清除密码缓存、重扫/重嵌入口 |

### 5.2 RAG 数据预览界面（M4）

<svg viewBox="0 0 680 470" width="100%" role="img" xmlns="http://www.w3.org/2000/svg">
<text x="340" y="26" text-anchor="middle" font-size="14" font-weight="500" fill="#D3D1C7">RAG 数据预览 · 界面示意</text>
<rect x="40" y="38" width="150" height="20" rx="10" fill="#0C447C" stroke="#85B7EB" stroke-width="0.5"/>
<text x="115" y="52" text-anchor="middle" font-size="11" fill="#B5D4F4">PostgreSQL · pgvector</text>
<g><rect x="40" y="70" width="140" height="60" rx="8" fill="#444441" stroke="#888780" stroke-width="0.5"/><text x="110" y="92" text-anchor="middle" font-size="11" fill="#888780">文档</text><text x="110" y="114" text-anchor="middle" font-size="16" font-weight="500" fill="#F1EFE8">12</text></g>
<g><rect x="190" y="70" width="140" height="60" rx="8" fill="#444441" stroke="#888780" stroke-width="0.5"/><text x="260" y="92" text-anchor="middle" font-size="11" fill="#888780">Chunks</text><text x="260" y="114" text-anchor="middle" font-size="16" font-weight="500" fill="#F1EFE8">348</text></g>
<g><rect x="340" y="70" width="140" height="60" rx="8" fill="#444441" stroke="#888780" stroke-width="0.5"/><text x="410" y="92" text-anchor="middle" font-size="11" fill="#888780">向量</text><text x="410" y="114" text-anchor="middle" font-size="16" font-weight="500" fill="#F1EFE8">348</text></g>
<g><rect x="490" y="70" width="140" height="60" rx="8" fill="#444441" stroke="#888780" stroke-width="0.5"/><text x="560" y="92" text-anchor="middle" font-size="11" fill="#888780">平均长度</text><text x="560" y="114" text-anchor="middle" font-size="16" font-weight="500" fill="#F1EFE8">412 字</text></g>
<rect x="40" y="150" width="600" height="210" rx="12" fill="#444441" stroke="#888780" stroke-width="0.5"/>
<text x="54" y="172" font-size="11" font-weight="500" fill="#D3D1C7">摘要</text>
<text x="392" y="172" font-size="11" font-weight="500" fill="#D3D1C7">标签</text>
<text x="480" y="172" font-size="11" font-weight="500" fill="#D3D1C7">来源</text>
<text x="576" y="172" font-size="11" font-weight="500" fill="#D3D1C7">操作</text>
<path d="M54 180 L626 180" fill="none" stroke="#888780" stroke-width="0.5"/>
<text x="54" y="200" font-size="11" fill="#F1EFE8">采购流程分请购、审批、下单、</text>
<text x="54" y="214" font-size="11" fill="#F1EFE8">收货四个环节…</text>
<text x="392" y="200" font-size="11" fill="#D3D1C7">采购 流程</text>
<text x="480" y="200" font-size="11" fill="#D3D1C7">需求规范.md</text>
<text x="576" y="200" font-size="11" fill="#85B7EB">原文 近邻</text>
<path d="M54 228 L626 228" fill="none" stroke="#888780" stroke-width="0.5"/>
<text x="54" y="248" font-size="11" fill="#F1EFE8">供应商准入需提供资质证明</text>
<text x="54" y="262" font-size="11" fill="#F1EFE8">并经合规审查…</text>
<text x="392" y="248" font-size="11" fill="#D3D1C7">供应商 准入</text>
<text x="480" y="248" font-size="11" fill="#D3D1C7">制度汇编.md</text>
<text x="576" y="248" font-size="11" fill="#85B7EB">原文 近邻</text>
<path d="M54 276 L626 276" fill="none" stroke="#888780" stroke-width="0.5"/>
<text x="54" y="296" font-size="11" fill="#F1EFE8">报销单据需部门负责人与</text>
<text x="54" y="310" font-size="11" fill="#F1EFE8">财务双重审批…</text>
<text x="392" y="296" font-size="11" fill="#D3D1C7">报销 审批</text>
<text x="480" y="296" font-size="11" fill="#D3D1C7">财务制度.md</text>
<text x="576" y="296" font-size="11" fill="#85B7EB">原文 近邻</text>
<path d="M54 324 L626 324" fill="none" stroke="#888780" stroke-width="0.5"/>
<text x="340" y="348" text-anchor="middle" font-size="11" fill="#888780">第 1/29 页 · 每页 12 条</text>
<rect x="40" y="380" width="600" height="70" rx="12" fill="#444441" stroke="#888780" stroke-width="0.5"/>
<text x="54" y="402" font-size="11" fill="#888780">近邻检索测试（本地 bge-m3 嵌入）</text>
<rect x="120" y="388" width="200" height="24" rx="4" fill="#5F5E5A" stroke="#888780" stroke-width="0.5"/>
<text x="130" y="403" font-size="11" fill="#888780">输入查询…</text>
<text x="340" y="403" font-size="11" fill="#D3D1C7">采购流程四环节 0.87 · 供应商准入 0.83 · 报销双审批 0.78</text>
</svg>

- **统计卡（4 个）**：文档 / Chunks / 向量 / 平均长度——WS `rag.stats` 驱动。
- **chunk 明细表**：摘要（两行截断）、标签、来源文档、操作。「原文」= 弹层显示 chunk 全文 + 字符区间高亮；「近邻」= 按该 chunk 向量召回 top-5 相似 chunk。数据走 `GET /api/rag/chunks` 分页。
- **近邻检索测试**：输入任意查询 → `POST /api/rag/search` → 展示 top-3 结果与相似度（保留两位小数）。这是 RAG 质量的肉眼验收入口。

### 5.3 本体图谱与审批界面（M5 / M6）

<svg viewBox="0 0 680 430" width="100%" role="img" xmlns="http://www.w3.org/2000/svg">
<text x="340" y="26" text-anchor="middle" font-size="14" font-weight="500" fill="#D3D1C7">本体图谱与审批 · 界面示意</text>
<g><rect x="40" y="40" width="70" height="20" rx="10" fill="#854F0B" stroke="#FAC775" stroke-width="0.5"/><text x="75" y="54" text-anchor="middle" font-size="11" fill="#FAEEDA">候选 23</text></g>
<g><rect x="118" y="40" width="80" height="20" rx="10" fill="#0F6E56" stroke="#5DCAA5" stroke-width="0.5"/><text x="158" y="54" text-anchor="middle" font-size="11" fill="#E1F5EE">已批准 12</text></g>
<g><rect x="206" y="40" width="70" height="20" rx="10" fill="#5F5E5A" stroke="#B4B2A9" stroke-width="0.5"/><text x="241" y="54" text-anchor="middle" font-size="11" fill="#F1EFE8">待定 8</text></g>
<rect x="40" y="78" width="370" height="320" rx="12" fill="#444441" stroke="#888780" stroke-width="0.5"/>
<text x="54" y="100" font-size="12" fill="#D3D1C7">图谱视图</text>
<text x="54" y="116" font-size="11" fill="#888780">实线=已批准 · 虚线=候选提名</text>
<path d="M190 160 L115 200" fill="none" stroke="#888780" stroke-width="1.5"/>
<path d="M235 160 L305 200" fill="none" stroke="#888780" stroke-width="1.5"/>
<path d="M210 300 L210 160" fill="none" stroke="#EF9F27" stroke-width="1.5" stroke-dasharray="5 3"/>
<g><rect x="165" y="126" width="90" height="34" rx="8" fill="#0F6E56" stroke="#5DCAA5" stroke-width="0.5"/><text x="210" y="143" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#E1F5EE">采购订单</text></g>
<g><rect x="70" y="200" width="80" height="34" rx="8" fill="#0F6E56" stroke="#5DCAA5" stroke-width="0.5"/><text x="110" y="217" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#E1F5EE">部门</text></g>
<g><rect x="275" y="200" width="90" height="34" rx="8" fill="#0F6E56" stroke="#5DCAA5" stroke-width="0.5"/><text x="320" y="217" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#E1F5EE">行项目</text></g>
<g><rect x="165" y="300" width="90" height="34" rx="8" fill="#854F0B" stroke="#FAC775" stroke-width="0.5" stroke-dasharray="5 3"/><text x="210" y="317" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#FAEEDA">供应商</text></g>
<text x="128" y="178" font-size="11" fill="#888780">提交</text>
<text x="290" y="178" font-size="11" fill="#888780">包含</text>
<text x="218" y="235" font-size="11" fill="#FAC775">供应</text>
<text x="225" y="356" text-anchor="middle" font-size="11" fill="#888780">点节点 → 右侧审批卡 / 实体详情</text>
<rect x="426" y="78" width="214" height="320" rx="12" fill="#444441" stroke="#888780" stroke-width="0.5"/>
<text x="440" y="100" font-size="12" fill="#D3D1C7">审批卡</text>
<rect x="540" y="86" width="88" height="20" rx="10" fill="#854F0B" stroke="#FAC775" stroke-width="0.5"/>
<text x="584" y="100" text-anchor="middle" font-size="11" fill="#FAEEDA">待审批</text>
<text x="440" y="126" font-size="13" font-weight="500" fill="#F1EFE8">供应商 · 候选实体</text>
<text x="440" y="146" font-size="11" fill="#D3D1C7">定义：向公司提供货物或</text>
<text x="440" y="160" font-size="11" fill="#D3D1C7">服务的外部组织</text>
<rect x="440" y="170" width="186" height="62" rx="6" fill="#5F5E5A" stroke="#888780" stroke-width="0.5"/>
<text x="450" y="188" font-size="11" fill="#F1EFE8">…<tspan fill="#FAC775">供应商</tspan>应在收到订单后</text>
<text x="450" y="202" font-size="11" fill="#F1EFE8">3 个工作日内确认交付…</text>
<text x="450" y="222" font-size="11" fill="#888780">chunk#142 · 需求规范.md</text>
<text x="440" y="254" font-size="11" font-weight="500" fill="#D3D1C7">关系提名</text>
<text x="440" y="272" font-size="11" fill="#D3D1C7">供应 → 采购订单（3 证据）</text>
<text x="440" y="288" font-size="11" fill="#D3D1C7">签订 → 框架合同（1 证据）</text>
<g><rect x="440" y="302" width="56" height="26" rx="6" fill="#0F6E56" stroke="#5DCAA5" stroke-width="0.5"/><text x="468" y="315" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#E1F5EE">批准</text></g>
<g><rect x="502" y="302" width="56" height="26" rx="6" fill="#5F5E5A" stroke="#B4B2A9" stroke-width="0.5"/><text x="530" y="315" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#F1EFE8">合并</text></g>
<g><rect x="564" y="302" width="62" height="26" rx="6" fill="#5F5E5A" stroke="#B4B2A9" stroke-width="0.5"/><text x="595" y="315" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#F1EFE8">拒绝</text></g>
<text x="440" y="350" font-size="11" fill="#9FE1CB">三关校验：结构✓ 语义✓ 证据✓</text>
<text x="440" y="380" font-size="11" fill="#888780">审批通过 → 写入 approved 表</text>
</svg>

- **图谱视图（Cytoscape.js）**：节点=实体、边=关系；已批准=实线，候选=虚线（amber）；顶部徽标显示候选/已批准/待定计数。点候选节点 → 右侧打开审批卡；点已批准节点 → 实体详情（定义 + mentions 证据列表，逐条跳原文 chunk）。
- **审批卡**：候选名 + 定义 + 证据 span 高亮（弹层显示 chunk 原文，span 逐字高亮）+ 关系提名（含证据计数）+ 三关校验结果 + 操作「批准 / 合并至已有 / 拒绝 / 暂缓」。
- **合并**：选目标实体后，该候选的 mentions 全部归并到目标（同义异名场景），图谱即时刷新。

### 5.4 组件与状态

- Zustand 三个 store：`settingsStore`（配置 + Ollama 状态）、`jobStore`（WS 驱动的任务/进度）、`graphStore`（图数据 + 审批队列）。
- 图谱渲染 Cytoscape.js；表格自绘（`table-layout:fixed` + 分页）；进度条按 `progress_current/total` 平滑插值。

## 6. 审批状态机

```
proposed ──批准──▶ approved（写 approved_entities/relations，图谱默认视图可见）
         ├─拒绝──▶ rejected（进入「已拒绝」筛选，可复查）
         ├─合并──▶ merged（mentions 归并目标实体）
         └─暂缓──▶ deferred（留在队列）
```

- 用户是唯一终审点；LLM 只到 `proposed` 为止，三关校验是确定性代码。
- 审批动作写 PG 事务（candidates.state 与 approved_* 同事务提交），并广播 `approval.updated`。

## 7. 里程碑

| # | 目标 | 内容 | 验收 |
|---|---|---|---|
| MS1 | 部署+配置闭环 | start.ps1 全链路（WSL2 自检/用户名识别/密码缓存/幂等安装）+ 设置页（token/拉模型/工作目录） | Ollama+PG 在 WSL2 起来，bge-m3 拉取成功，V4-Flash 连通测试通过 |
| MS2 | RAG 闭环 | ingest job（PDF/MD→分段→summary/标签→嵌入→入库）+ RAG 预览页（表+原文弹层+近邻检索） | 工作目录文档入库，预览页可查、检索可测 |
| MS3 | 本体闭环 | EDC 归纳（Flash）+ 三关校验 + 图谱页 + 审批闭环 | 候选→审批→图谱实线可见，合并/拒绝可用 |

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| WSL2 虚拟化未开启 | 脚本检测后给出 BIOS（VT-x）+「虚拟机平台」启用指引，明确退出而非静默失败 |
| pgvector 包名随发行版差异 | 优先 `postgresql-16-pgvector`（Ubuntu 24.04），失败回退 PGDG apt 源 |
| sudo 密码缓存安全 | DPAPI 绑定用户+机器、清除入口、密码不进日志/事件/环境变量 |
| Flash 规范化质量弱于 GLM | MVP 靠审批界面人肉把关；GLM 5.2 归纳核验按完整版 §6 接入 |
| 大 PDF 解析慢/失败 | job 进度可见 + chunk 级 checkpoint 续跑 + 失败可重试 |
| bge-m3 内存占用（约 1.2GB） | CPU 可跑；低配机器按完整版混合模式思路引导 |

## 9. 数字人身份预筛与考题环节（v1.1 增补 · 2026-08-31）

> 背景：全量提取逻辑保留（锚点是**引导不是白名单**），但在提取**之前**先建立「目标数字人」的初始形象，注入 prompt 加速并精简提取；提取**之中**同次调用产出考题，审批**之前**用考题考核候选本体，剔除无用提名。LLM 仍只提名，确定性代码判分，用户是唯一终审点。

### 9.1 流程总览

<svg viewBox="0 0 680 500" width="100%" role="img" xmlns="http://www.w3.org/2000/svg">
<defs><marker id="a9" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>
<text x="340" y="24" text-anchor="middle" font-size="14" font-weight="500" fill="#D3D1C7">身份预筛 → 锚点注入 → 提取出题 → 审批前考核</text>
<g><rect x="60" y="44" width="250" height="58" rx="8" fill="#5F5E5A" stroke="#B4B2A9" stroke-width="0.5"/><text x="185" y="66" text-anchor="middle" font-size="13" font-weight="500" fill="#F1EFE8">① 高频词汇总</text><text x="185" y="86" text-anchor="middle" font-size="11" fill="#D3D1C7">词频+标签聚合 · 确定性 0 LLM</text></g>
<path d="M185 102 L185 124" fill="none" stroke="#B4B2A9" stroke-width="1.5" marker-end="url(#a9)"/>
<g><rect x="60" y="126" width="250" height="58" rx="8" fill="#854F0B" stroke="#FAC775" stroke-width="0.5"/><text x="185" y="148" text-anchor="middle" font-size="13" font-weight="500" fill="#FAEEDA">② LLM 提名数字人身份</text><text x="185" y="168" text-anchor="middle" font-size="11" fill="#FAC775">3~5 个身份 × 各 5~12 个锚点本体</text></g>
<path d="M185 184 L185 206" fill="none" stroke="#B4B2A9" stroke-width="1.5" marker-end="url(#a9)"/>
<g><rect x="60" y="208" width="250" height="58" rx="8" fill="#0C447C" stroke="#85B7EB" stroke-width="0.5"/><text x="185" y="230" text-anchor="middle" font-size="13" font-weight="500" fill="#E6F1FB">③ 用户审批（唯一终审）</text><text x="185" y="250" text-anchor="middle" font-size="11" fill="#B5D4F4">身份批准 · 锚点可编辑/增删/逐个批准</text></g>
<path d="M185 266 L185 288" fill="none" stroke="#B4B2A9" stroke-width="1.5" marker-end="url(#a9)"/>
<g><rect x="60" y="290" width="250" height="58" rx="8" fill="#085041" stroke="#5DCAA5" stroke-width="0.5"/><text x="185" y="312" text-anchor="middle" font-size="13" font-weight="500" fill="#E1F5EE">④ 锚点注入提取 prompt</text><text x="185" y="332" text-anchor="middle" font-size="11" fill="#9FE1CB">引导非白名单 · 锚点外实体照常提名</text></g>
<path d="M185 348 L185 370" fill="none" stroke="#B4B2A9" stroke-width="1.5" marker-end="url(#a9)"/>
<g><rect x="60" y="372" width="250" height="58" rx="8" fill="#0F6E56" stroke="#5DCAA5" stroke-width="0.5"/><text x="185" y="394" text-anchor="middle" font-size="13" font-weight="500" fill="#E1F5EE">⑤ 全量本体提取（并发4）</text><text x="185" y="414" text-anchor="middle" font-size="11" fill="#9FE1CB">同次调用顺带出 2~3 道考题</text></g>
<path d="M310 401 L360 401" fill="none" stroke="#B4B2A9" stroke-width="1.5" marker-end="url(#a9)"/>
<g><rect x="360" y="372" width="270" height="58" rx="8" fill="#854F0B" stroke="#FAC775" stroke-width="0.5"/><text x="495" y="394" text-anchor="middle" font-size="13" font-weight="500" fill="#FAEEDA">⑥ 审批前考核（自动触发 job）</text><text x="495" y="414" text-anchor="middle" font-size="11" fill="#FAC775">LLM 作答 · quote 逐字命中 · 确定性判分</text></g>
<path d="M495 430 L495 452" fill="none" stroke="#B4B2A9" stroke-width="1.5" marker-end="url(#a9)"/>
<g><rect x="360" y="454" width="270" height="36" rx="8" fill="#0C447C" stroke="#85B7EB" stroke-width="0.5"/><text x="495" y="477" text-anchor="middle" font-size="13" font-weight="500" fill="#E6F1FB">⑦ 考核结果=建议 · 用户终审剔除/批准</text></g>
<text x="380" y="70" font-size="11" fill="#FAC775">铁律：LLM 只提名与作答</text>
<text x="380" y="88" font-size="11" fill="#FAC775">锚点外新实体照常进候选</text>
<text x="380" y="106" font-size="11" fill="#FAC775">全量逻辑不被破坏</text>
</svg>

### 9.2 身份预筛（锚点注入）

| 决策点 | 结论 |
|---|---|
| 高频词语料 | 全部 chunk 原文（CJK 2/3-gram 词频 + 拉丁词）+ chunk 标签聚合，停用词过滤，**top 80**，纯确定性代码 |
| 身份提名 | **一次 LLM 调用**（高频词 + 标签 + 文档摘要样本），出 **3~5 个**身份，每个带 5~12 个锚点（名称/类型/一句话定义） |
| 存储 | `identities`（pending）+ `anchors`（pending），独立审批：身份与锚点均单独批准，**锚点可编辑、可增删**（用户在 UI 上修正提名） |
| 注入时机 | 提取 job 启动时读取「已批准身份的已批准锚点」拼入 prompt；暂停恢复也会重新读取（断点续跑不重提取，但新增锚点对未提取 chunk 生效） |
| 注入语义 | prompt 明示「以下是目标数字人锚点，优先并更仔细提取相关内容；锚点之外的实体关系仍照常提名」——**引导非白名单** |

### 9.3 考题环节（出题与考核）

<svg viewBox="0 0 680 430" width="100%" role="img" xmlns="http://www.w3.org/2000/svg">
<defs><marker id="b9" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>
<text x="340" y="24" text-anchor="middle" font-size="14" font-weight="500" fill="#D3D1C7">考题环节：出题（提取同次调用）→ 考核（审批前）</text>
<g><rect x="40" y="50" width="280" height="64" rx="8" fill="#0F6E56" stroke="#5DCAA5" stroke-width="0.5"/><text x="180" y="74" text-anchor="middle" font-size="13" font-weight="500" fill="#E1F5EE">chunk 提取（一次 LLM 调用）</text><text x="180" y="94" text-anchor="middle" font-size="11" fill="#9FE1CB">entities + relations + quiz（2~3 题）</text></g>
<path d="M180 114 L180 138" fill="none" stroke="#B4B2A9" stroke-width="1.5" marker-end="url(#b9)"/>
<g><rect x="40" y="140" width="280" height="64" rx="8" fill="#5F5E5A" stroke="#B4B2A9" stroke-width="0.5"/><text x="180" y="162" text-anchor="middle" font-size="13" font-weight="500" fill="#F1EFE8">考题校验（确定性）</text><text x="180" y="182" text-anchor="middle" font-size="11" fill="#D3D1C7">evidence 必须逐字命中 chunk 原文</text><text x="180" y="196" text-anchor="middle" font-size="11" fill="#D3D1C7">否则整题丢弃（LLM 永不被信任）</text></g>
<path d="M180 204 L180 228" fill="none" stroke="#B4B2A9" stroke-width="1.5" marker-end="url(#b9)"/>
<g><rect x="40" y="230" width="280" height="50" rx="8" fill="#085041" stroke="#5DCAA5" stroke-width="0.5"/><text x="180" y="250" text-anchor="middle" font-size="13" font-weight="500" fill="#E1F5EE">quiz 表（chunk 级检查点随提取入库）</text><text x="180" y="268" text-anchor="middle" font-size="11" fill="#9FE1CB">q · a（预期答案）· evidence（原文 span）</text></g>
<path d="M320 255 L370 255" fill="none" stroke="#B4B2A9" stroke-width="1.5" marker-end="url(#b9)"/>
<g><rect x="370" y="230" width="280" height="50" rx="8" fill="#854F0B" stroke="#FAC775" stroke-width="0.5"/><text x="510" y="250" text-anchor="middle" font-size="13" font-weight="500" fill="#FAEEDA">考核 job（提取完成后自动触发）</text><text x="510" y="268" text-anchor="middle" font-size="11" fill="#FAC775">逐题并发：问题 + 该 chunk 的候选本体列表</text></g>
<path d="M510 280 L510 304" fill="none" stroke="#B4B2A9" stroke-width="1.5" marker-end="url(#b9)"/>
<g><rect x="370" y="306" width="280" height="64" rx="8" fill="#0C447C" stroke="#85B7EB" stroke-width="0.5"/><text x="510" y="326" text-anchor="middle" font-size="13" font-weight="500" fill="#E6F1FB">LLM 作答：answer + quote + used + enough</text><text x="510" y="344" text-anchor="middle" font-size="11" fill="#B5D4F4">quote 须逐字命中 chunk 原文（证据关）</text><text x="510" y="360" text-anchor="middle" font-size="11" fill="#B5D4F4">used 须命中候选闭集（语义关）· 答案须含预期答案</text></g>
<path d="M360 338 L310 338" fill="none" stroke="#B4B2A9" stroke-width="1.5" marker-end="url(#b9)"/>
<g><rect x="40" y="306" width="280" height="64" rx="8" fill="#5F5E5A" stroke="#B4B2A9" stroke-width="0.5"/><text x="180" y="328" text-anchor="middle" font-size="13" font-weight="500" fill="#F1EFE8">确定性判分 → exam_results</text><text x="180" y="346" text-anchor="middle" font-size="11" fill="#9FE1CB">pass：答案对+quote真 · fail：答错/quote造假</text><text x="180" y="362" text-anchor="middle" font-size="11" fill="#9FE1CB">missing：候选不足以回答（存疑）</text></g>
<text x="180" y="400" text-anchor="middle" font-size="11" fill="#FAC775">考核结果只是建议：候选列表加「存疑」过滤与徽标，剔除/批准始终由用户终审</text>
</svg>

| 决策点 | 结论 |
|---|---|
| 出题时机 | chunk 提取**同一次 LLM 调用**顺带产出（不加调用成本）；每 chunk 2~3 题，evidence 逐字校验，不合规整题丢弃 |
| 考核触发 | 提取 job 正常完成后**自动**触发考核 job（也可在页面手动触发）；考题为 0 则跳过 |
| 考核方式 | 每题一次 LLM 调用：输入=题目+该 chunk 提取出的候选本体（名称+定义）；输出 JSON `{answer, quote, used[], enough}`；并发 4 + 失败步进（同提取调度器） |
| 判分规则（确定性代码） | `pass`：enough 且答案含预期答案（空白归一化后）且 quote 逐字命中 chunk 原文；`fail`：答错或 quote 造假；`missing`：enough=false（候选不足，存疑）。`used` 中不在候选闭集的名字直接忽略 |
| 结果消费 | exam_results 只聚合为候选的 通过/不及格/存疑 计数，前端列表徽标 + 「存疑」筛选 + 详情面板逐题展示；**不自动剔除任何候选** |

### 9.4 数据模型增量（SQLite，随 db.py 自动建表）

```
identities(id, name, mission, description, keywords JSON, status pending|approved|rejected, created_at)
anchors(id, identity_id→identities ON DELETE CASCADE, name, type, definition,
        status pending|approved|rejected, created_at)
quiz(id, chunk_id→chunks ON DELETE CASCADE, question, answer, evidence, created_at)   -- evidence 逐字命中 chunk
exam_results(id, quiz_id→quiz ON DELETE CASCADE, candidate_id→candidates ON DELETE CASCADE,
             verdict pass|fail|missing, created_at)                                   -- 重新考核幂等：按 quiz 先清后写
```

### 9.5 API 增量

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/ontology/identities/nominate` | 触发身份提名 job（kind=identity）：高频词→LLM 提名→pending 入库 |
| GET | `/api/ontology/identities` | 身份+锚点列表 |
| POST | `/api/ontology/identities/{id}/status` | 身份批准/拒绝 |
| POST | `/api/ontology/anchors` / `PUT /api/anchors/{id}` | 新增 / 编辑锚点（可编辑） |
| POST | `/api/ontology/anchors/{id}/status` | 锚点批准/拒绝 |
| POST | `/api/ontology/exam/run` | 手动触发考核 job（kind=exam） |
| GET | `/api/ontology/graph` | 增补：节点附 exam{pass,fail,missing}；载荷附考题/考核统计 |

提取 job 正常结束后若 quiz 表非空，后端自动创建考核 job（事件 `exam.auto_started`）；考核 job 支持暂停/恢复（按题检查点）。

---

*生成说明：本节为 v1.1 增补，对应「减少无用本体提取 + 审批前质量把关」两项需求，不改变既有全量提取与三关校验语义。*


